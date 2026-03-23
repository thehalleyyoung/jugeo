from __future__ import annotations

"""Theory2.tex Ch8 §"Fleet semantics and economic choice under obligations" —
Projects, modules, hypercovers, and fleets.

Fleet semantics describes the situation where multiple LLM/solver agents
(fleet members) collaborate or compete to cover the judgment site.  Each
agent proposes a local section at PROPOSAL tier; their contributions are
assembled into a ProjectHypercover by the coordinator, which then applies
economic choice principles to allocate a finite compute budget across the
outstanding obligations.

Mathematical setting
--------------------
A *fleet* F = {A₁, …, Aₙ} is a finite collection of agents.  Each agent Aᵢ
has a *capability profile* κ(Aᵢ) ⊆ Domains, a *cost function* c(Aᵢ, φ) → ℝ₊,
and a *proposal function* π(Aᵢ, U) → LocalSection(U) that takes a site
coordinate and returns a local section at PROPOSAL tier.

Economic choice
---------------
Given a set of outstanding *obligations* O = {o₁, …, oₘ} and a total compute
budget B, the coordinator must allocate sub-budgets Bᵢ such that:

    ∑ᵢ Bᵢ ≤ B    (budget constraint)
    ∀ oⱼ ∈ O: ∃ Aᵢ ∈ F with oⱼ ∈ covered(Aᵢ, Bᵢ)   (coverage constraint)

This is a set-cover/knapsack hybrid.  Theory2.tex §8 proves that optimal
allocation is NP-hard in general but admits a (1 − 1/e)-approximation via
greedy submodular maximization.

Judgment tuples are (c, φ, A, E, O, B, T, Π) — trust T is a tier string,
never a float.  Fleet members always propose at PROPOSAL tier; silent
promotion is forbidden.

# copilot: foundations/project_hypercovers §s02 — fleet semantics and economic choice
"""

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore

try:
    from jugeo.foundations.project_hypercovers.models import TrustTier as _MTT
    _TRUST_BASE = _MTT
except ImportError:
    _TRUST_BASE = None  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TrustTier(str, Enum):
    """Categorical trust tier for fleet-level reasoning.

    Fleet members always propose at PROPOSAL tier.  Promotion requires
    explicit verification and is recorded in the provenance chain.
    Trust is categorical — never a float.
    """

    PROPOSAL     = "PROPOSAL"
    PROVISIONAL  = "PROVISIONAL"
    CORROBORATED = "CORROBORATED"
    CERTIFIED    = "CERTIFIED"
    CANONICAL    = "CANONICAL"

    def dominates(self, other: TrustTier) -> bool:
        """Return True if this tier is strictly higher than *other*."""
        order = list(TrustTier)
        return order.index(self) > order.index(other)

    def meets(self, other: TrustTier) -> TrustTier:
        """Return the lower of two tiers (lattice meet)."""
        order = list(TrustTier)
        return self if order.index(self) <= order.index(other) else other


class FleetRole(str, Enum):
    """Role of a FleetMember within the fleet.

    Theory2.tex §8 distinguishes three roles:

    -   PROPOSER: generates local sections (LLM, code generator).
    -   VERIFIER: checks proposals made by proposers (formal verifier, test runner).
    -   ARBITRATOR: resolves conflicts between proposers (senior model, oracle).
    """

    PROPOSER   = "PROPOSER"
    VERIFIER   = "VERIFIER"
    ARBITRATOR = "ARBITRATOR"


class AllocationStrategy(str, Enum):
    """Strategy for distributing the compute budget across fleet members.

    Theory2.tex §8 provides approximation guarantees for each strategy.
    """

    GREEDY       = "GREEDY"        # Greedy submodular (1 − 1/e) guarantee
    PROPORTIONAL = "PROPORTIONAL"  # Budget proportional to capability score
    UNIFORM      = "UNIFORM"       # Equal budget to all active members
    PRIORITY     = "PRIORITY"      # Full budget to highest-priority member first
    AUCTION      = "AUCTION"       # Members bid; highest bid wins budget share


class ObligationStatus(str, Enum):
    """Lifecycle status of a single obligation."""

    OPEN       = "OPEN"        # Not yet assigned to any agent
    ASSIGNED   = "ASSIGNED"    # Assigned; agent working on it
    FULFILLED  = "FULFILLED"   # Agent discharged the obligation
    DEFERRED   = "DEFERRED"    # Moved to next budget cycle
    ABANDONED  = "ABANDONED"   # No agent can cover it; blocked


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FleetMember:
    """A single agent (LLM or solver) participating in the fleet.

    Each fleet member has a capability profile describing which domains and
    obligation types it can address, a cost model, and a trust tier at which
    it is allowed to propose sections.

    Parameters
    ----------
    member_id : str
        Unique 12-hex identifier.
    name : str
        Human-readable name (e.g. ``"gpt-4o"``, ``"lean4-verifier"``).
    role : FleetRole
        The agent's role within the fleet.
    capability_domains : Sequence[str]
        Domains this agent can reason about (e.g. ``["security", "correctness"]``).
    cost_per_obligation : float
        Estimated compute cost (in abstract budget units) per obligation addressed.
    proposal_tier : TrustTier
        The trust tier at which this agent's proposals are accepted.
        Per Theory2.tex §8: fleet members propose at PROPOSAL tier.  Any other
        tier must be explicitly granted by the fleet coordinator.
    meta : Mapping[str, Any]
        Arbitrary metadata (model version, endpoint, temperature, etc.).
    created_at : str
        ISO-8601 creation timestamp.

    Notes
    -----
    ``proposal_tier`` must be PROPOSAL for untrusted agents.  Verifiers and
    arbitrators may be granted higher tiers by the coordinator with explicit
    justification.

    Examples
    --------
    >>> m = FleetMember.make("gpt-4o", FleetRole.PROPOSER, ["security", "correctness"])
    >>> m.proposal_tier
    <TrustTier.PROPOSAL: 'PROPOSAL'>
    """

    member_id            : str
    name                 : str
    role                 : FleetRole
    capability_domains   : Sequence[str]
    cost_per_obligation  : float
    proposal_tier        : TrustTier
    meta                 : Mapping[str, Any]
    created_at           : str

    def can_cover_domain(self, domain: str) -> bool:
        """Return True if this member's capabilities include *domain*."""
        return domain in self.capability_domains

    def estimated_cost(self, n_obligations: int) -> float:
        """Return estimated compute cost for *n_obligations* obligations."""
        return self.cost_per_obligation * max(n_obligations, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "member_id":           self.member_id,
            "name":                self.name,
            "role":                self.role.value,
            "capability_domains":  list(self.capability_domains),
            "cost_per_obligation": self.cost_per_obligation,
            "proposal_tier":       self.proposal_tier.value,
            "meta":                dict(self.meta),
            "created_at":          self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FleetMember:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            member_id            = d["member_id"],
            name                 = d["name"],
            role                 = FleetRole(d["role"]),
            capability_domains   = tuple(d.get("capability_domains", [])),
            cost_per_obligation  = float(d.get("cost_per_obligation", 1.0)),
            proposal_tier        = TrustTier(d.get("proposal_tier", "PROPOSAL")),
            meta                 = d.get("meta", {}),
            created_at           = d["created_at"],
        )

    @classmethod
    def make(
        cls,
        name: str,
        role: FleetRole,
        capability_domains: Sequence[str],
        cost_per_obligation: float = 1.0,
        proposal_tier: TrustTier = TrustTier.PROPOSAL,
        meta: Mapping[str, Any] | None = None,
    ) -> FleetMember:
        """Factory with auto-assigned member_id and created_at."""
        return cls(
            member_id            = uuid.uuid4().hex[:12],
            name                 = name,
            role                 = role,
            capability_domains   = tuple(capability_domains),
            cost_per_obligation  = cost_per_obligation,
            proposal_tier        = proposal_tier,
            meta                 = meta or {},
            created_at           = datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True, slots=True)
class Fleet:
    """A collection of FleetMembers available to cover the judgment site.

    A Fleet represents the full pool of available agents.  The coordinator
    selects a subset of the fleet for each budget cycle based on capability
    overlap with outstanding obligations.

    Parameters
    ----------
    fleet_id : str
        Unique 12-hex identifier.
    name : str
        Human-readable fleet name.
    members : Sequence[FleetMember]
        All members in this fleet.
    total_budget : float
        Total compute budget available for this fleet cycle.
    created_at : str
        ISO-8601 creation timestamp.
    meta : Mapping[str, Any]
        Arbitrary metadata.

    Notes
    -----
    A fleet with zero members cannot cover any obligation; the coordinator
    will produce an empty witness with all obligations ABANDONED.
    """

    fleet_id     : str
    name         : str
    members      : Sequence[FleetMember]
    total_budget : float
    created_at   : str
    meta         : Mapping[str, Any] = field(default_factory=dict)

    def member_by_id(self, member_id: str) -> FleetMember | None:
        """Return the member with *member_id*, or None if not found."""
        for m in self.members:
            if m.member_id == member_id:
                return m
        return None

    def members_for_domain(self, domain: str) -> list[FleetMember]:
        """Return all members whose capability profile covers *domain*."""
        return [m for m in self.members if m.can_cover_domain(domain)]

    def proposers(self) -> list[FleetMember]:
        """Return all members with role PROPOSER."""
        return [m for m in self.members if m.role == FleetRole.PROPOSER]

    def verifiers(self) -> list[FleetMember]:
        """Return all members with role VERIFIER."""
        return [m for m in self.members if m.role == FleetRole.VERIFIER]

    def arbitrators(self) -> list[FleetMember]:
        """Return all members with role ARBITRATOR."""
        return [m for m in self.members if m.role == FleetRole.ARBITRATOR]

    def capability_union(self) -> frozenset[str]:
        """Return the union of all member capability domains."""
        domains: set[str] = set()
        for m in self.members:
            domains.update(m.capability_domains)
        return frozenset(domains)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "fleet_id":     self.fleet_id,
            "name":         self.name,
            "members":      [m.to_dict() for m in self.members],
            "total_budget": self.total_budget,
            "created_at":   self.created_at,
            "meta":         dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fleet:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            fleet_id     = d["fleet_id"],
            name         = d["name"],
            members      = tuple(FleetMember.from_dict(m) for m in d.get("members", [])),
            total_budget = float(d.get("total_budget", 0.0)),
            created_at   = d["created_at"],
            meta         = d.get("meta", {}),
        )

    @classmethod
    def make(
        cls,
        name: str,
        members: Sequence[FleetMember],
        total_budget: float = 100.0,
        meta: Mapping[str, Any] | None = None,
    ) -> Fleet:
        """Factory with auto-assigned fleet_id and created_at."""
        return cls(
            fleet_id     = uuid.uuid4().hex[:12],
            name         = name,
            members      = tuple(members),
            total_budget = total_budget,
            created_at   = datetime.now(timezone.utc).isoformat(),
            meta         = meta or {},
        )


@dataclass(frozen=True, slots=True)
class ObligationBudget:
    """Budget allocation for a single outstanding obligation.

    In Theory2.tex §8 each obligation oⱼ receives a sub-budget Bⱼ from the
    total fleet budget B.  The budget allocation is computed by the coordinator
    based on the chosen AllocationStrategy.

    Parameters
    ----------
    obligation_key : str
        Identifier matching an obligation key from an ArtifactPatch.
    assigned_member_id : str | None
        The FleetMember assigned to discharge this obligation, or None if
        unassigned (status OPEN or ABANDONED).
    budget_units : float
        Compute budget allocated to this obligation.
    status : ObligationStatus
        Current lifecycle status.
    domain : str
        Semantic domain the obligation belongs to.
    priority : int
        Priority score (higher = more urgent); used by PRIORITY strategy.
    created_at : str
        ISO-8601 creation timestamp.
    fulfilled_at : str | None
        ISO-8601 timestamp when the obligation was discharged, or None.

    Notes
    -----
    Once an obligation is FULFILLED its ``budget_units`` is considered spent.
    DEFERRED obligations carry their remaining budget into the next cycle.
    """

    obligation_key      : str
    assigned_member_id  : str | None
    budget_units        : float
    status              : ObligationStatus
    domain              : str
    priority            : int
    created_at          : str
    fulfilled_at        : str | None = None

    def is_active(self) -> bool:
        """Return True if the obligation is OPEN or ASSIGNED."""
        return self.status in (ObligationStatus.OPEN, ObligationStatus.ASSIGNED)

    def is_terminal(self) -> bool:
        """Return True if the obligation is in a terminal state."""
        return self.status in (
            ObligationStatus.FULFILLED,
            ObligationStatus.ABANDONED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "obligation_key":     self.obligation_key,
            "assigned_member_id": self.assigned_member_id,
            "budget_units":       self.budget_units,
            "status":             self.status.value,
            "domain":             self.domain,
            "priority":           self.priority,
            "created_at":         self.created_at,
            "fulfilled_at":       self.fulfilled_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObligationBudget:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            obligation_key      = d["obligation_key"],
            assigned_member_id  = d.get("assigned_member_id"),
            budget_units        = float(d.get("budget_units", 0.0)),
            status              = ObligationStatus(d["status"]),
            domain              = d.get("domain", "unknown"),
            priority            = int(d.get("priority", 0)),
            created_at          = d["created_at"],
            fulfilled_at        = d.get("fulfilled_at"),
        )

    @classmethod
    def make(
        cls,
        obligation_key: str,
        domain: str,
        budget_units: float = 1.0,
        priority: int = 0,
    ) -> ObligationBudget:
        """Factory with auto-assigned timestamps and OPEN status."""
        return cls(
            obligation_key     = obligation_key,
            assigned_member_id = None,
            budget_units       = budget_units,
            status             = ObligationStatus.OPEN,
            domain             = domain,
            priority           = priority,
            created_at         = datetime.now(timezone.utc).isoformat(),
            fulfilled_at       = None,
        )


@dataclass(frozen=True, slots=True)
class EconomicChoiceRecord:
    """An immutable record of one budget-allocation decision by the coordinator.

    Theory2.tex §8 treats economic choice as a formal decision procedure:
    each choice must be recorded, justified, and auditable.

    Parameters
    ----------
    record_id : str
        Unique 12-hex identifier.
    fleet_id : str
        The fleet over which the allocation was computed.
    strategy : AllocationStrategy
        The allocation strategy used.
    obligation_budgets : Sequence[ObligationBudget]
        The per-obligation budget allocations produced by this decision.
    total_budget_used : float
        Sum of all allocated budget_units.
    budget_remaining : float
        ``fleet.total_budget − total_budget_used``.
    coverage_fraction : float
        Fraction of obligations that received a non-zero assignment.
    created_at : str
        ISO-8601 timestamp of this decision.
    rationale : str
        Human-readable explanation of the allocation choices.

    Notes
    -----
    Records are immutable by design: once a decision is recorded it cannot be
    modified retroactively.  A new ``EconomicChoiceRecord`` must be created
    for any revised allocation.
    """

    record_id           : str
    fleet_id            : str
    strategy            : AllocationStrategy
    obligation_budgets  : Sequence[ObligationBudget]
    total_budget_used   : float
    budget_remaining    : float
    coverage_fraction   : float
    created_at          : str
    rationale           : str

    def obligations_by_status(
        self, status: ObligationStatus
    ) -> list[ObligationBudget]:
        """Return obligation budgets with the given *status*."""
        return [ob for ob in self.obligation_budgets if ob.status == status]

    def fulfilled_count(self) -> int:
        """Return the number of FULFILLED obligations."""
        return sum(
            1 for ob in self.obligation_budgets
            if ob.status == ObligationStatus.FULFILLED
        )

    def efficiency_score(self) -> float:
        """Return fulfilled / total obligations as a [0, 1] efficiency metric."""
        total = len(self.obligation_budgets)
        if total == 0:
            return 1.0
        return self.fulfilled_count() / total

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "record_id":          self.record_id,
            "fleet_id":           self.fleet_id,
            "strategy":           self.strategy.value,
            "obligation_budgets": [ob.to_dict() for ob in self.obligation_budgets],
            "total_budget_used":  self.total_budget_used,
            "budget_remaining":   self.budget_remaining,
            "coverage_fraction":  self.coverage_fraction,
            "created_at":         self.created_at,
            "rationale":          self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EconomicChoiceRecord:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            record_id          = d["record_id"],
            fleet_id           = d["fleet_id"],
            strategy           = AllocationStrategy(d["strategy"]),
            obligation_budgets = tuple(
                ObligationBudget.from_dict(ob) for ob in d.get("obligation_budgets", [])
            ),
            total_budget_used  = float(d.get("total_budget_used", 0.0)),
            budget_remaining   = float(d.get("budget_remaining", 0.0)),
            coverage_fraction  = float(d.get("coverage_fraction", 0.0)),
            created_at         = d["created_at"],
            rationale          = d.get("rationale", ""),
        )


@dataclass(frozen=True, slots=True)
class FleetProposal:
    """A local section proposal from a single FleetMember.

    Fleet members generate proposals at PROPOSAL tier.  The fleet coordinator
    aggregates proposals, verifies cocycle conditions between them, and—if all
    checks pass—assembles a global section from the individual proposals.

    Parameters
    ----------
    proposal_id : str
        Unique 12-hex identifier.
    member_id : str
        The fleet member who generated this proposal.
    obligation_key : str
        The obligation this proposal aims to discharge.
    section_data : Mapping[str, Any]
        The proposed local section content for the target coordinate.
    tier : TrustTier
        Always PROPOSAL for fleet-member proposals per Theory2.tex §8.
    confidence_label : str
        A qualitative label (NOT a float) expressing the member's confidence:
        one of ``"low"``, ``"medium"``, ``"high"``, ``"certain"``.
    created_at : str
        ISO-8601 creation timestamp.

    Notes
    -----
    ``confidence_label`` is intentionally qualitative.  JuGeo does not use
    probability floats for trust or confidence — only categorical labels that
    correspond to evidence tiers.
    """

    proposal_id       : str
    member_id         : str
    obligation_key    : str
    section_data      : Mapping[str, Any]
    tier              : TrustTier
    confidence_label  : str
    created_at        : str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "proposal_id":      self.proposal_id,
            "member_id":        self.member_id,
            "obligation_key":   self.obligation_key,
            "section_data":     dict(self.section_data),
            "tier":             self.tier.value,
            "confidence_label": self.confidence_label,
            "created_at":       self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FleetProposal:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            proposal_id      = d["proposal_id"],
            member_id        = d["member_id"],
            obligation_key   = d["obligation_key"],
            section_data     = d.get("section_data", {}),
            tier             = TrustTier(d.get("tier", "PROPOSAL")),
            confidence_label = d.get("confidence_label", "medium"),
            created_at       = d["created_at"],
        )

    @classmethod
    def make(
        cls,
        member_id: str,
        obligation_key: str,
        section_data: Mapping[str, Any],
        confidence_label: str = "medium",
    ) -> FleetProposal:
        """Factory: proposals are always at PROPOSAL tier."""
        return cls(
            proposal_id      = uuid.uuid4().hex[:12],
            member_id        = member_id,
            obligation_key   = obligation_key,
            section_data     = dict(section_data),
            tier             = TrustTier.PROPOSAL,
            confidence_label = confidence_label,
            created_at       = datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# FleetSemanticsEconomicChoiceCoordinator
# ---------------------------------------------------------------------------

class FleetSemanticsEconomicChoiceCoordinator:
    """Orchestrates budget allocation and obligation coverage across a fleet.

    This coordinator implements the economic choice procedure of Theory2.tex §8:

    1.  Collect outstanding obligations from the current set of ArtifactPatches.
    2.  For each obligation, determine which fleet members can address it.
    3.  Allocate the compute budget across obligations using the chosen strategy.
    4.  Record the allocation as an EconomicChoiceRecord.
    5.  Integrate proposals back into the hypercover (trust promotion deferred).

    The coordinator never silently promotes fleet proposals above PROPOSAL tier.
    Trust promotion requires explicit verification by a VERIFIER or ARBITRATOR.

    Parameters
    ----------
    strategy : AllocationStrategy
        Budget allocation strategy.  Default: GREEDY.
    max_rounds : int
        Maximum number of allocation cycles before halting with DEFERRED.
        Default: 10.
    verbose : bool
        Emit progress messages to stdout.

    Examples
    --------
    >>> fleet = Fleet.make("my_fleet", [
    ...     FleetMember.make("gpt-4o", FleetRole.PROPOSER, ["security"]),
    ...     FleetMember.make("lean4",  FleetRole.VERIFIER, ["correctness"]),
    ... ])
    >>> coord = FleetSemanticsEconomicChoiceCoordinator()
    >>> witness = coord.run(fleet, ["security/check_sql", "correctness/type_safety"])
    """

    def __init__(
        self,
        strategy: AllocationStrategy = AllocationStrategy.GREEDY,
        max_rounds: int = 10,
        verbose: bool = False,
    ) -> None:
        self.strategy   = strategy
        self.max_rounds = max_rounds
        self.verbose    = verbose
        self._log: list[str] = []

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def run(
        self,
        fleet: Fleet,
        obligation_keys: Sequence[str],
        domain_map: Mapping[str, str] | None = None,
    ) -> FleetSemanticsEconomicChoiceWitness:
        """Allocate budget and produce an EconomicChoiceRecord witness.

        Parameters
        ----------
        fleet : Fleet
            The fleet to allocate budget across.
        obligation_keys : Sequence[str]
            Obligation identifiers to be covered (e.g. from ArtifactPatch.obligations).
        domain_map : Mapping[str, str], optional
            Maps obligation_key → domain name.  Defaults to using the portion
            of the key before the first ``"/"`` as the domain.

        Returns
        -------
        FleetSemanticsEconomicChoiceWitness
            Immutable certificate of the allocation decision.
        """
        t0 = time.monotonic()
        self._log.clear()
        self._emit(
            f"run: fleet={fleet.fleet_id!r} n_obligations={len(obligation_keys)} "
            f"strategy={self.strategy.value}"
        )

        errs = self.validate(fleet, obligation_keys)
        if errs:
            raise ValueError(f"Input validation failed: {errs}")

        dmap = domain_map or {}
        budgets = self._initialise_budgets(fleet, obligation_keys, dmap)
        budgets = self._allocate(fleet, budgets)
        record  = self._build_record(fleet, budgets)

        elapsed = time.monotonic() - t0
        return FleetSemanticsEconomicChoiceWitness(
            witness_id     = uuid.uuid4().hex[:12],
            fleet          = fleet,
            choice_record  = record,
            proposals      = (),
            elapsed_s      = elapsed,
            log_lines      = tuple(self._log),
            created_at     = datetime.now(timezone.utc).isoformat(),
        )

    def validate(
        self,
        fleet: Fleet,
        obligation_keys: Sequence[str],
    ) -> list[str]:
        """Return validation error messages (empty list = valid).

        Checks:

        -   Fleet has at least one member.
        -   ``fleet.total_budget`` is positive.
        -   No duplicate obligation keys.
        -   All obligation keys are non-empty strings.

        Parameters
        ----------
        fleet : Fleet
            The fleet to validate.
        obligation_keys : Sequence[str]
            Obligation keys to validate.

        Returns
        -------
        list[str]
            Human-readable error messages; empty when inputs are valid.
        """
        errors: list[str] = []
        if not fleet.members:
            errors.append("Fleet has no members.")
        if fleet.total_budget <= 0:
            errors.append(f"Fleet total_budget must be positive; got {fleet.total_budget}.")
        seen: set[str] = set()
        for key in obligation_keys:
            if not key:
                errors.append("Empty obligation key encountered.")
            if key in seen:
                errors.append(f"Duplicate obligation key: {key!r}.")
            seen.add(key)
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise coordinator configuration."""
        return {
            "strategy":   self.strategy.value,
            "max_rounds": self.max_rounds,
            "verbose":    self.verbose,
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> FleetSemanticsEconomicChoiceCoordinator:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            strategy   = AllocationStrategy(d.get("strategy", "GREEDY")),
            max_rounds = int(d.get("max_rounds", 10)),
            verbose    = bool(d.get("verbose", False)),
        )

    # ------------------------------------------------------------------
    # Domain methods
    # ------------------------------------------------------------------

    def select_capable_members(
        self,
        fleet: Fleet,
        obligation_key: str,
        domain: str,
    ) -> list[FleetMember]:
        """Return fleet members capable of addressing *obligation_key* in *domain*.

        Capability is determined by ``FleetMember.can_cover_domain(domain)``.
        Members are returned sorted by ``cost_per_obligation`` ascending (cheapest first).

        Parameters
        ----------
        fleet : Fleet
            The fleet to search.
        obligation_key : str
            The obligation key (used for logging).
        domain : str
            The semantic domain of the obligation.

        Returns
        -------
        list[FleetMember]
            Capable members sorted cheapest-first.
        """
        capable = [m for m in fleet.members if m.can_cover_domain(domain)]
        return sorted(capable, key=lambda m: m.cost_per_obligation)

    def compute_coverage_matrix(
        self, fleet: Fleet, obligation_keys: Sequence[str],
        domain_map: Mapping[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """Compute a coverage matrix: obligation_key → [member_ids that can cover it].

        Parameters
        ----------
        fleet : Fleet
            The fleet to analyse.
        obligation_keys : Sequence[str]
            Obligation keys to compute coverage for.
        domain_map : Mapping[str, str], optional
            Maps obligation_key → domain.

        Returns
        -------
        dict[str, list[str]]
            Maps each obligation key to the list of capable member_ids.
        """
        dmap = domain_map or {}
        matrix: dict[str, list[str]] = {}
        for key in obligation_keys:
            domain  = dmap.get(key, key.split("/")[0] if "/" in key else "general")
            capable = self.select_capable_members(fleet, key, domain)
            matrix[key] = [m.member_id for m in capable]
        return matrix

    def estimate_total_cost(
        self,
        fleet: Fleet,
        obligation_keys: Sequence[str],
        domain_map: Mapping[str, str] | None = None,
    ) -> float:
        """Estimate the total compute cost of discharging all obligations.

        Uses the cheapest capable member for each obligation as the lower bound.

        Parameters
        ----------
        fleet : Fleet
            The fleet to estimate cost for.
        obligation_keys : Sequence[str]
            Obligation keys.
        domain_map : Mapping[str, str], optional
            Maps obligation_key → domain.

        Returns
        -------
        float
            Total estimated cost (sum of cheapest-member costs per obligation).
        """
        dmap  = domain_map or {}
        total = 0.0
        for key in obligation_keys:
            domain  = dmap.get(key, key.split("/")[0] if "/" in key else "general")
            capable = self.select_capable_members(fleet, key, domain)
            if capable:
                total += capable[0].cost_per_obligation
        return total

    def propose_section(
        self,
        member: FleetMember,
        obligation_key: str,
        section_data: Mapping[str, Any],
    ) -> FleetProposal:
        """Record a fleet member's proposed local section for an obligation.

        This method enforces that all fleet proposals are at PROPOSAL tier.
        Any attempt to propose at a higher tier is silently corrected and logged.

        Parameters
        ----------
        member : FleetMember
            The proposing member.
        obligation_key : str
            The obligation being addressed.
        section_data : Mapping[str, Any]
            The proposed section content.

        Returns
        -------
        FleetProposal
            An immutable proposal record at PROPOSAL tier.

        Notes
        -----
        Trust promotion from PROPOSAL to a higher tier is a separate, explicit
        step that requires a VERIFIER or ARBITRATOR.
        """
        if member.proposal_tier != TrustTier.PROPOSAL:
            self._emit(
                f"propose_section: member {member.name!r} has tier "
                f"{member.proposal_tier.value}; clamping to PROPOSAL per Theory2.tex §8"
            )
        return FleetProposal.make(
            member_id        = member.member_id,
            obligation_key   = obligation_key,
            section_data     = section_data,
            confidence_label = "medium",
        )

    def arbitrate_conflict(
        self,
        proposals: Sequence[FleetProposal],
        fleet: Fleet,
    ) -> FleetProposal | None:
        """Resolve conflicting proposals for the same obligation.

        When two PROPOSER members disagree on a section, an ARBITRATOR is
        invoked.  If no arbitrator is available in the fleet, the first
        proposal (by creation time) is returned as the tentative winner.

        Parameters
        ----------
        proposals : Sequence[FleetProposal]
            Conflicting proposals for the same ``obligation_key``.
        fleet : Fleet
            The fleet containing potential arbitrators.

        Returns
        -------
        FleetProposal | None
            The winning proposal, or None if *proposals* is empty.
        """
        if not proposals:
            return None
        if len(proposals) == 1:
            return proposals[0]

        arbitrators = fleet.arbitrators()
        if arbitrators:
            self._emit(
                f"arbitrate_conflict: {len(arbitrators)} arbitrator(s) available; "
                "using first by capability domain coverage"
            )
        # Without a live arbitrator, return the proposal from the member with the
        # highest-ranked role: VERIFIER > PROPOSER (ARBITRATOR rarely proposes)
        role_rank = {FleetRole.ARBITRATOR: 3, FleetRole.VERIFIER: 2, FleetRole.PROPOSER: 1}
        def _rank(p: FleetProposal) -> int:
            m = fleet.member_by_id(p.member_id)
            return role_rank.get(m.role, 0) if m else 0
        return max(proposals, key=_rank)

    def simulate_round(
        self,
        fleet: Fleet,
        obligation_keys: Sequence[str],
        domain_map: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Simulate one allocation round without committing to a witness.

        Returns a preview of which members would be assigned to which
        obligations and the estimated costs, without producing proposals.

        Parameters
        ----------
        fleet : Fleet
            The fleet to simulate.
        obligation_keys : Sequence[str]
            Obligations to simulate.
        domain_map : Mapping[str, str], optional
            Domain mapping.

        Returns
        -------
        dict[str, Any]
            ``assignments``, ``total_cost``, ``uncovered``.
        """
        dmap    = domain_map or {}
        matrix  = self.compute_coverage_matrix(fleet, obligation_keys, dmap)
        assignments: dict[str, str | None] = {}
        total_cost = 0.0

        for key in obligation_keys:
            capable_ids = matrix.get(key, [])
            if capable_ids:
                m = fleet.member_by_id(capable_ids[0])
                assignments[key] = capable_ids[0]
                if m:
                    total_cost += m.cost_per_obligation
            else:
                assignments[key] = None

        uncovered = [k for k, v in assignments.items() if v is None]
        return {
            "assignments": assignments,
            "total_cost":  total_cost,
            "uncovered":   uncovered,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        ts    = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        if self.verbose:
            print(entry)

    def _infer_domain(self, key: str, dmap: Mapping[str, str]) -> str:
        if key in dmap:
            return dmap[key]
        return key.split("/")[0] if "/" in key else "general"

    def _initialise_budgets(
        self,
        fleet: Fleet,
        obligation_keys: Sequence[str],
        dmap: Mapping[str, str],
    ) -> list[ObligationBudget]:
        total = fleet.total_budget
        n     = max(len(obligation_keys), 1)
        base  = total / n

        return [
            ObligationBudget.make(
                obligation_key = key,
                domain         = self._infer_domain(key, dmap),
                budget_units   = base,
                priority       = i,
            )
            for i, key in enumerate(obligation_keys)
        ]

    def _allocate(
        self,
        fleet: Fleet,
        budgets: list[ObligationBudget],
    ) -> list[ObligationBudget]:
        if self.strategy == AllocationStrategy.GREEDY:
            return self._allocate_greedy(fleet, budgets)
        elif self.strategy == AllocationStrategy.UNIFORM:
            return self._allocate_uniform(fleet, budgets)
        elif self.strategy == AllocationStrategy.PRIORITY:
            return self._allocate_priority(fleet, budgets)
        elif self.strategy == AllocationStrategy.PROPORTIONAL:
            return self._allocate_proportional(fleet, budgets)
        else:
            return self._allocate_greedy(fleet, budgets)

    def _allocate_greedy(
        self, fleet: Fleet, budgets: list[ObligationBudget]
    ) -> list[ObligationBudget]:
        """Greedy allocation: sort by priority desc, assign cheapest capable member."""
        remaining = fleet.total_budget
        result: list[ObligationBudget] = []
        for ob in sorted(budgets, key=lambda b: -b.priority):
            capable = self.select_capable_members(fleet, ob.obligation_key, ob.domain)
            if capable and remaining >= capable[0].cost_per_obligation:
                cost = capable[0].cost_per_obligation
                remaining -= cost
                result.append(replace(
                    ob,
                    assigned_member_id = capable[0].member_id,
                    budget_units       = cost,
                    status             = ObligationStatus.ASSIGNED,
                ))
            elif capable:
                result.append(replace(ob, status=ObligationStatus.DEFERRED))
            else:
                result.append(replace(ob, status=ObligationStatus.ABANDONED))
        return result

    def _allocate_uniform(
        self, fleet: Fleet, budgets: list[ObligationBudget]
    ) -> list[ObligationBudget]:
        """Uniform: equal budget to each obligation; assign first capable member."""
        result: list[ObligationBudget] = []
        for ob in budgets:
            capable = self.select_capable_members(fleet, ob.obligation_key, ob.domain)
            if capable:
                result.append(replace(
                    ob,
                    assigned_member_id = capable[0].member_id,
                    status             = ObligationStatus.ASSIGNED,
                ))
            else:
                result.append(replace(ob, status=ObligationStatus.ABANDONED))
        return result

    def _allocate_priority(
        self, fleet: Fleet, budgets: list[ObligationBudget]
    ) -> list[ObligationBudget]:
        """Priority: full budget to highest-priority obligation first."""
        remaining = fleet.total_budget
        result: list[ObligationBudget] = []
        for ob in sorted(budgets, key=lambda b: -b.priority):
            capable = self.select_capable_members(fleet, ob.obligation_key, ob.domain)
            if capable and remaining > 0:
                cost = min(capable[0].cost_per_obligation, remaining)
                remaining -= cost
                result.append(replace(
                    ob,
                    assigned_member_id = capable[0].member_id,
                    budget_units       = cost,
                    status             = ObligationStatus.ASSIGNED if cost > 0 else ObligationStatus.DEFERRED,
                ))
            else:
                result.append(replace(ob, status=ObligationStatus.ABANDONED))
        return result

    def _allocate_proportional(
        self, fleet: Fleet, budgets: list[ObligationBudget]
    ) -> list[ObligationBudget]:
        """Proportional: budget proportional to each obligation's priority weight."""
        total_priority = sum(max(ob.priority, 1) for ob in budgets) or 1
        result: list[ObligationBudget] = []
        for ob in budgets:
            fraction = max(ob.priority, 1) / total_priority
            allocated = fleet.total_budget * fraction
            capable = self.select_capable_members(fleet, ob.obligation_key, ob.domain)
            if capable:
                result.append(replace(
                    ob,
                    assigned_member_id = capable[0].member_id,
                    budget_units       = allocated,
                    status             = ObligationStatus.ASSIGNED,
                ))
            else:
                result.append(replace(ob, status=ObligationStatus.ABANDONED))
        return result

    def _build_record(
        self,
        fleet: Fleet,
        budgets: list[ObligationBudget],
    ) -> EconomicChoiceRecord:
        total_used  = sum(ob.budget_units for ob in budgets)
        remaining   = fleet.total_budget - total_used
        assigned    = sum(1 for ob in budgets if ob.status == ObligationStatus.ASSIGNED)
        n_total     = max(len(budgets), 1)
        coverage    = assigned / n_total
        return EconomicChoiceRecord(
            record_id          = uuid.uuid4().hex[:12],
            fleet_id           = fleet.fleet_id,
            strategy           = self.strategy,
            obligation_budgets = tuple(budgets),
            total_budget_used  = total_used,
            budget_remaining   = remaining,
            coverage_fraction  = coverage,
            created_at         = datetime.now(timezone.utc).isoformat(),
            rationale          = (
                f"Allocated {total_used:.2f} of {fleet.total_budget:.2f} budget units "
                f"using {self.strategy.value} strategy; "
                f"covered {assigned}/{n_total} obligations."
            ),
        )


# ---------------------------------------------------------------------------
# FleetSemanticsEconomicChoiceAnalyzer
# ---------------------------------------------------------------------------

class FleetSemanticsEconomicChoiceAnalyzer:
    """Analyses fleet allocation witnesses and produces diagnostic reports.

    Produces coverage metrics, budget efficiency scores, member utilisation
    stats, and recommendations for improving fleet composition or strategy.

    Parameters
    ----------
    coverage_threshold : float
        Minimum acceptable coverage fraction in [0, 1].  Default: 0.8.
    budget_efficiency_threshold : float
        Minimum acceptable budget efficiency in [0, 1].  Default: 0.5.
    """

    def __init__(
        self,
        coverage_threshold: float = 0.8,
        budget_efficiency_threshold: float = 0.5,
    ) -> None:
        self.coverage_threshold           = coverage_threshold
        self.budget_efficiency_threshold  = budget_efficiency_threshold

    def analyze(
        self, witness: FleetSemanticsEconomicChoiceWitness
    ) -> dict[str, Any]:
        """Produce a full structured analysis of the fleet allocation witness.

        Parameters
        ----------
        witness : FleetSemanticsEconomicChoiceWitness
            The output of a ``Coordinator.run()`` call.

        Returns
        -------
        dict[str, Any]
            Keys: ``summary``, ``coverage``, ``budget``, ``utilisation``,
            ``recommendations``.
        """
        rec = witness.choice_record
        coverage    = self._analyze_coverage(rec)
        budget      = self._analyze_budget(rec)
        utilisation = self._analyze_utilisation(rec, witness.fleet)
        return {
            "summary":         self.summarize(witness),
            "coverage":        coverage,
            "budget":          budget,
            "utilisation":     utilisation,
            "recommendations": self._build_recommendations(coverage, budget, utilisation),
        }

    def score(self, witness: FleetSemanticsEconomicChoiceWitness) -> float:
        """Return a [0, 1] quality score for the allocation.

        Score formula::

            score = coverage_fraction × efficiency_score × (1 − abandoned_penalty)
        """
        rec = witness.choice_record
        n_total   = max(len(rec.obligation_budgets), 1)
        abandoned = sum(
            1 for ob in rec.obligation_budgets
            if ob.status == ObligationStatus.ABANDONED
        )
        abandoned_penalty = abandoned / n_total
        return rec.coverage_fraction * rec.efficiency_score() * (1.0 - abandoned_penalty)

    def report(self, witness: FleetSemanticsEconomicChoiceWitness) -> str:
        """Return a human-readable text report of the fleet allocation.

        Parameters
        ----------
        witness : FleetSemanticsEconomicChoiceWitness
            The certificate to report on.

        Returns
        -------
        str
            Multi-line text suitable for printing to a terminal.
        """
        rec = witness.choice_record
        lines = [
            "=" * 72,
            "FleetSemanticsEconomicChoice — Allocation Report",
            f"  witness_id       : {witness.witness_id}",
            f"  created_at       : {witness.created_at}",
            f"  elapsed_s        : {witness.elapsed_s:.4f}",
            f"  strategy         : {rec.strategy.value}",
            f"  score            : {self.score(witness):.4f}",
            "-" * 72,
            f"  fleet            : {witness.fleet.name!r}  ({len(witness.fleet.members)} members)",
            f"  total_budget     : {witness.fleet.total_budget:.2f}",
            f"  budget_used      : {rec.total_budget_used:.2f}",
            f"  budget_remaining : {rec.budget_remaining:.2f}",
            f"  coverage         : {rec.coverage_fraction:.2%}",
            f"  n_obligations    : {len(rec.obligation_budgets)}",
            f"  fulfilled        : {rec.fulfilled_count()}",
            "-" * 72,
        ]
        for ob in rec.obligation_budgets:
            lines.append(
                f"  [{ob.status.value:10s}] {ob.obligation_key!r}  "
                f"domain={ob.domain!r}  "
                f"budget={ob.budget_units:.2f}  "
                f"member={ob.assigned_member_id or 'none'}"
            )
        lines.append("=" * 72)
        return "\n".join(lines)

    def summarize(
        self, witness: FleetSemanticsEconomicChoiceWitness
    ) -> dict[str, Any]:
        """Return a compact summary dict.

        Parameters
        ----------
        witness : FleetSemanticsEconomicChoiceWitness
            The certificate to summarise.

        Returns
        -------
        dict[str, Any]
            Keys: ``witness_id``, ``score``, ``coverage_fraction``,
            ``n_obligations``, ``n_members``, ``budget_used``, ``strategy``.
        """
        rec = witness.choice_record
        return {
            "witness_id":        witness.witness_id,
            "score":             round(self.score(witness), 6),
            "coverage_fraction": round(rec.coverage_fraction, 6),
            "n_obligations":     len(rec.obligation_budgets),
            "n_members":         len(witness.fleet.members),
            "budget_used":       round(rec.total_budget_used, 4),
            "strategy":          rec.strategy.value,
            "elapsed_s":         round(witness.elapsed_s, 6),
        }

    def is_healthy(self, witness: FleetSemanticsEconomicChoiceWitness) -> bool:
        """Return True when the allocation meets health criteria.

        Healthy allocation satisfies ALL of:

        -   Coverage fraction ≥ ``coverage_threshold``.
        -   Budget efficiency ≥ ``budget_efficiency_threshold``.
        -   No ABANDONED obligations.
        """
        rec = witness.choice_record
        no_abandoned = not any(
            ob.status == ObligationStatus.ABANDONED for ob in rec.obligation_budgets
        )
        return (
            rec.coverage_fraction >= self.coverage_threshold
            and rec.efficiency_score() >= self.budget_efficiency_threshold
            and no_abandoned
        )

    def domain_coverage(
        self, witness: FleetSemanticsEconomicChoiceWitness
    ) -> dict[str, dict[str, Any]]:
        """Break down allocation coverage by semantic domain.

        Parameters
        ----------
        witness : FleetSemanticsEconomicChoiceWitness
            The certificate to analyse.

        Returns
        -------
        dict[str, dict[str, Any]]
            Maps domain → ``{total, assigned, coverage_fraction}``.
        """
        rec = witness.choice_record
        by_domain: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "assigned": 0})
        for ob in rec.obligation_budgets:
            by_domain[ob.domain]["total"] += 1
            if ob.status == ObligationStatus.ASSIGNED:
                by_domain[ob.domain]["assigned"] += 1
        return {
            d: {
                "total":             v["total"],
                "assigned":          v["assigned"],
                "coverage_fraction": v["assigned"] / max(v["total"], 1),
            }
            for d, v in by_domain.items()
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_coverage(self, rec: EconomicChoiceRecord) -> dict[str, Any]:
        total    = len(rec.obligation_budgets)
        assigned = sum(1 for ob in rec.obligation_budgets if ob.status == ObligationStatus.ASSIGNED)
        deferred = sum(1 for ob in rec.obligation_budgets if ob.status == ObligationStatus.DEFERRED)
        abandoned = sum(1 for ob in rec.obligation_budgets if ob.status == ObligationStatus.ABANDONED)
        return {
            "total":              total,
            "assigned":           assigned,
            "deferred":           deferred,
            "abandoned":          abandoned,
            "coverage_fraction":  rec.coverage_fraction,
        }

    def _analyze_budget(self, rec: EconomicChoiceRecord) -> dict[str, Any]:
        return {
            "total_budget":    rec.total_budget_used + rec.budget_remaining,
            "used":            rec.total_budget_used,
            "remaining":       rec.budget_remaining,
            "utilisation_pct": (
                rec.total_budget_used / max(rec.total_budget_used + rec.budget_remaining, 1e-9)
            ) * 100,
        }

    def _analyze_utilisation(
        self, rec: EconomicChoiceRecord, fleet: Fleet
    ) -> dict[str, Any]:
        member_loads: dict[str, int] = defaultdict(int)
        for ob in rec.obligation_budgets:
            if ob.assigned_member_id:
                member_loads[ob.assigned_member_id] += 1
        return {
            "member_loads": dict(member_loads),
            "overloaded":   [mid for mid, cnt in member_loads.items() if cnt > 5],
            "idle_members": [
                m.member_id for m in fleet.members
                if m.member_id not in member_loads
            ],
        }

    def _build_recommendations(
        self,
        coverage: dict[str, Any],
        budget: dict[str, Any],
        utilisation: dict[str, Any],
    ) -> list[str]:
        recs: list[str] = []
        if coverage["abandoned"] > 0:
            recs.append(
                f"Add fleet members to cover {coverage['abandoned']} abandoned obligation(s)."
            )
        if coverage["deferred"] > 0:
            recs.append(
                f"Increase fleet budget to prevent {coverage['deferred']} deferred obligation(s)."
            )
        if budget["utilisation_pct"] < 50:
            recs.append("Budget utilisation below 50%; consider reducing fleet size or budget.")
        if utilisation["overloaded"]:
            recs.append(
                f"{len(utilisation['overloaded'])} member(s) overloaded; "
                "add fleet members or redistribute obligations."
            )
        if not recs:
            recs.append("Fleet allocation is healthy; no action required.")
        return recs


# ---------------------------------------------------------------------------
# FleetSemanticsEconomicChoiceWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FleetSemanticsEconomicChoiceWitness:
    """Immutable output certificate for a fleet allocation run.

    Captures the fleet, the economic choice record, any generated proposals,
    timing information, and the audit log.

    Parameters
    ----------
    witness_id : str
        Unique 12-hex identifier.
    fleet : Fleet
        The fleet over which allocation was computed.
    choice_record : EconomicChoiceRecord
        The immutable allocation decision record.
    proposals : tuple[FleetProposal, ...]
        Fleet member proposals generated during this cycle (may be empty if
        proposals were deferred to downstream pipeline stages).
    elapsed_s : float
        Wall-clock seconds for the coordinator run.
    log_lines : tuple[str, ...]
        Ordered log lines for debugging.
    created_at : str
        ISO-8601 creation timestamp.

    Examples
    --------
    >>> w = coordinator.run(fleet, ["security/sql_injection"])
    >>> assert w.choice_record.coverage_fraction > 0
    >>> serialised = json.dumps(w.to_dict())
    """

    witness_id    : str
    fleet         : Fleet
    choice_record : EconomicChoiceRecord
    proposals     : tuple[FleetProposal, ...]
    elapsed_s     : float
    log_lines     : tuple[str, ...]
    created_at    : str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "witness_id":    self.witness_id,
            "fleet":         self.fleet.to_dict(),
            "choice_record": self.choice_record.to_dict(),
            "proposals":     [p.to_dict() for p in self.proposals],
            "elapsed_s":     self.elapsed_s,
            "log_lines":     list(self.log_lines),
            "created_at":    self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FleetSemanticsEconomicChoiceWitness:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            witness_id    = d["witness_id"],
            fleet         = Fleet.from_dict(d["fleet"]),
            choice_record = EconomicChoiceRecord.from_dict(d["choice_record"]),
            proposals     = tuple(FleetProposal.from_dict(p) for p in d.get("proposals", [])),
            elapsed_s     = float(d.get("elapsed_s", 0.0)),
            log_lines     = tuple(d.get("log_lines", [])),
            created_at    = d["created_at"],
        )

    def digest(self) -> str:
        """Content-hash of this witness (SHA-256 over canonical JSON, 24 hex)."""
        raw = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def is_successful(self) -> bool:
        """True when all obligations are assigned and none abandoned."""
        rec = self.choice_record
        return all(
            ob.status != ObligationStatus.ABANDONED
            for ob in rec.obligation_budgets
        ) and len(rec.obligation_budgets) > 0


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== FleetSemanticsEconomicChoice smoke test ===")

    # Build a fleet with diverse capability profiles
    members = [
        FleetMember.make(
            "gpt-4o",
            FleetRole.PROPOSER,
            ["security", "correctness"],
            cost_per_obligation=2.0,
        ),
        FleetMember.make(
            "lean4-verifier",
            FleetRole.VERIFIER,
            ["correctness", "performance"],
            cost_per_obligation=5.0,
            proposal_tier=TrustTier.PROVISIONAL,
        ),
        FleetMember.make(
            "bandit-scanner",
            FleetRole.PROPOSER,
            ["security"],
            cost_per_obligation=1.0,
        ),
        FleetMember.make(
            "senior-model",
            FleetRole.ARBITRATOR,
            ["security", "correctness", "performance"],
            cost_per_obligation=10.0,
            proposal_tier=TrustTier.CORROBORATED,
        ),
    ]
    fleet = Fleet.make("test_fleet", members, total_budget=30.0)

    obligations = [
        "security/sql_injection",
        "security/xss",
        "correctness/type_safety",
        "performance/hot_path",
        "correctness/null_check",
    ]

    coord = FleetSemanticsEconomicChoiceCoordinator(
        strategy=AllocationStrategy.GREEDY, verbose=False
    )
    errs = coord.validate(fleet, obligations)
    assert not errs, f"Validation errors: {errs}"

    witness  = coord.run(fleet, obligations)
    analyzer = FleetSemanticsEconomicChoiceAnalyzer()

    print(analyzer.report(witness))
    summary = analyzer.summarize(witness)
    assert summary["n_obligations"] == 5
    assert summary["n_members"] == 4

    # Round-trip serialisation
    reloaded = FleetSemanticsEconomicChoiceWitness.from_dict(witness.to_dict())
    assert reloaded.witness_id == witness.witness_id
    assert reloaded.digest() == witness.digest()

    # Simulate round
    sim = coord.simulate_round(fleet, obligations)
    assert "assignments" in sim

    print("\nsmoke test PASSED")
    print(json.dumps(summary, indent=2))
    sys.exit(0)
