"""Section — Semantic Jurisdiction.

# copilot: foundations/oracle_federation §semantic-jurisdiction — Theory2.tex
# Chapter: Controlled oracles, solver federation, and runtime witnesses

This module implements **semantic jurisdiction** as described in Theory2.tex:
the determination of which oracle or solver is *authoritative* over which
coordinate range, and how authority is delegated, contested, and resolved when
multiple agents claim jurisdiction.

Background
----------
In the JuGeo framework, a *semantic site* is partitioned into *jurisdiction
domains* — regions over which a specific oracle or solver has authoritative
knowledge.  Jurisdiction is not merely advisory; it is the mechanism by which
the framework routes queries and interprets conflicting evidence.

Theory2.tex §7.2 formalises jurisdiction as a *sheaf of authority* over the
coordinate site.  Each open set ``U`` of the site has an associated authority
set ``Auth(U)`` — the set of agents whose claims over ``U`` are authoritative.
Jurisdiction is *coherent* when the authority assignments satisfy the gluing
condition: if agent ``a`` is authoritative over ``U`` and over ``V``, then
``a`` is authoritative over ``U ∩ V``.

When two agents both claim jurisdiction over a coordinate ``c``, the
framework must resolve the conflict.  Theory2.tex provides three resolution
strategies:
- **Intersection** — both are authoritative if they agree.
- **Priority** — a predeclared priority ordering decides.
- **Escalate** — the conflict is escalated to a human arbitrator.

Theory2.tex invariants
----------------------
- Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — never booleans.
- Trust is an ordered algebra ``PROPOSAL → REVIEWED → VERIFIED`` — never a float.
- Jurisdiction claims always enter at ``PROPOSAL``; they can be promoted only
  by the resolution protocol.

Public API
----------
- :class:`AuthorityLevel` — enum of authority strengths
- :class:`JurisdictionConflictKind` — enum of conflict types
- :class:`ResolutionStrategy` — enum of resolution strategies
- :class:`CoordinateRange` — a typed range over the coordinate space
- :class:`JurisdictionClaim` — a single authority claim by one agent
- :class:`AuthorityMapping` — the aggregated authority map for a site
- :class:`JurisdictionConflict` — a detected conflict between claims
- :class:`ResolutionRecord` — the outcome of resolving a conflict
- :class:`SemanticDomain` — a named semantic domain with its jurisdiction
- :class:`SemanticJurisdictionCoordinator` — orchestrates jurisdiction management
- :class:`SemanticJurisdictionAnalyzer` — analyzes jurisdiction health
- :class:`SemanticJurisdictionWitness` — immutable certificate
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel, TrustProfile
except ImportError:
    TrustTier = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustProfile = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust algebra
# ---------------------------------------------------------------------------

_TRUST_ORDER: dict[str, int] = {
    "PROPOSAL": 0,
    "REVIEWED": 1,
    "VERIFIED": 2,
}

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AuthorityLevel(Enum):
    """Strength of an authority claim over a coordinate range.

    EXCLUSIVE
        Only this agent is authoritative; all others are forbidden.
    PRIMARY
        This agent is the primary authority; others may advise.
    SHARED
        This agent shares authority equally with others.
    ADVISORY
        This agent may advise but is not authoritative.
    NONE
        No authority claimed.
    """

    EXCLUSIVE = "exclusive"
    PRIMARY = "primary"
    SHARED = "shared"
    ADVISORY = "advisory"
    NONE = "none"


class JurisdictionConflictKind(Enum):
    """Classifier for the type of jurisdiction conflict."""

    OVERLAP = "overlap"                  # Two agents claim the same range
    EXCLUSIVITY_VIOLATION = "exclusivity_violation"  # Exclusive claim violated
    GAP = "gap"                          # No agent covers a coordinate
    INHERITANCE = "inheritance"          # Derived jurisdiction contradicts parent
    PRIORITY_TIE = "priority_tie"        # Two agents have equal priority


class ResolutionStrategy(Enum):
    """Strategy for resolving a jurisdiction conflict.

    INTERSECTION
        Both agents are authoritative only if they agree on the coordinate.
    PRIORITY
        A predeclared priority ordering determines the winner.
    ESCALATE
        The conflict is escalated to a higher-level arbitrator.
    MERGE
        The claims are merged into a shared authority entry.
    DROP_LOWER
        The lower-authority-level claim is dropped.
    """

    INTERSECTION = "intersection"
    PRIORITY = "priority"
    ESCALATE = "escalate"
    MERGE = "merge"
    DROP_LOWER = "drop_lower"


class JurisdictionStatus(Enum):
    """Lifecycle status of a jurisdiction run."""

    PENDING = "pending"
    COMPUTED = "computed"
    CONFLICTED = "conflicted"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoordinateRange:
    """A typed range over the coordinate space.

    Coordinates in JuGeo may be structured (dotted paths like
    ``type_theory.Nat.succ``) or numeric.  A ``CoordinateRange`` captures
    both cases via a prefix pattern and optional numeric bounds.

    Parameters
    ----------
    range_id:
        Unique identifier.
    prefix:
        String prefix that coordinates in this range must start with.
    lo:
        Numeric lower bound (inclusive), if applicable.
    hi:
        Numeric upper bound (exclusive), if applicable.
    include_descendants:
        If True, all coordinates whose path starts with *prefix* are included.
    domain_tag:
        The semantic domain tag for this range.
    """

    range_id: str = field(default_factory=lambda: "cr_" + uuid.uuid4().hex[:12])
    prefix: str = ""
    lo: float = float("-inf")
    hi: float = float("inf")
    include_descendants: bool = True
    domain_tag: str = ""

    def contains(self, coordinate: str) -> bool:
        """Return True if *coordinate* falls within this range."""
        if self.prefix and not coordinate.startswith(self.prefix):
            return False
        try:
            val = float(coordinate)
            if val < self.lo or val >= self.hi:
                return False
        except (ValueError, TypeError):
            pass  # Non-numeric coordinate — prefix check suffices
        return True

    def overlaps_with(self, other: CoordinateRange) -> bool:
        """Return True if this range and *other* share at least one coordinate."""
        # Prefix overlap
        if self.prefix and other.prefix:
            a, b = sorted([self.prefix, other.prefix], key=len)
            if not b.startswith(a) and not a.startswith(b):
                return False
        # Numeric overlap
        return self.lo < other.hi and other.lo < self.hi

    def to_dict(self) -> dict[str, Any]:
        return {
            "range_id": self.range_id,
            "prefix": self.prefix,
            "lo": self.lo,
            "hi": self.hi,
            "include_descendants": self.include_descendants,
            "domain_tag": self.domain_tag,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoordinateRange:
        return cls(
            range_id=d.get("range_id", "cr_" + uuid.uuid4().hex[:12]),
            prefix=d.get("prefix", ""),
            lo=float(d.get("lo", float("-inf"))),
            hi=float(d.get("hi", float("inf"))),
            include_descendants=bool(d.get("include_descendants", True)),
            domain_tag=d.get("domain_tag", ""),
        )


@dataclass(frozen=True, slots=True)
class JurisdictionClaim:
    """A single authority claim by one agent over a coordinate range.

    Theory2.tex: a jurisdiction claim is the object-level description of a
    morphism from an agent to a region of the coordinate site.

    Parameters
    ----------
    claim_id:
        Unique identifier.
    agent_id:
        The oracle, solver, or other agent making the claim.
    coordinate_range:
        The :class:`CoordinateRange` being claimed.
    authority_level:
        The strength of the claim (:class:`AuthorityLevel`).
    trust_tier:
        The trust tier at which this claim enters (always ``PROPOSAL`` initially).
    priority:
        Numeric priority for conflict resolution (higher = more priority).
    rationale:
        Free-text justification for the claim.
    created_at:
        Unix timestamp of claim creation.
    expires_at:
        Unix timestamp of claim expiry (or inf if permanent).
    metadata:
        Extension key-value pairs.
    """

    claim_id: str = field(default_factory=lambda: "jc_" + uuid.uuid4().hex[:12])
    agent_id: str = ""
    coordinate_range: CoordinateRange = field(default_factory=CoordinateRange)
    authority_level: str = AuthorityLevel.SHARED.value
    trust_tier: str = "PROPOSAL"
    priority: int = 0
    rationale: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = float("inf")
    metadata: dict = field(default_factory=dict)

    def is_active(self, at_time: float | None = None) -> bool:
        """Return True if the claim has not expired."""
        t = at_time if at_time is not None else time.time()
        return t < self.expires_at

    def covers(self, coordinate: str) -> bool:
        """Return True if this claim covers *coordinate*."""
        return self.coordinate_range.contains(coordinate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "agent_id": self.agent_id,
            "coordinate_range": self.coordinate_range.to_dict(),
            "authority_level": self.authority_level,
            "trust_tier": self.trust_tier,
            "priority": self.priority,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JurisdictionClaim:
        return cls(
            claim_id=d.get("claim_id", "jc_" + uuid.uuid4().hex[:12]),
            agent_id=d.get("agent_id", ""),
            coordinate_range=CoordinateRange.from_dict(d.get("coordinate_range", {})),
            authority_level=d.get("authority_level", AuthorityLevel.SHARED.value),
            trust_tier=d.get("trust_tier", "PROPOSAL"),
            priority=int(d.get("priority", 0)),
            rationale=d.get("rationale", ""),
            created_at=float(d.get("created_at", time.time())),
            expires_at=float(d.get("expires_at", float("inf"))),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class AuthorityMapping:
    """The aggregated authority map for a semantic site.

    An ``AuthorityMapping`` is the result of collating all registered
    :class:`JurisdictionClaim` objects into a coherent assignment from
    coordinates to authoritative agents.

    Parameters
    ----------
    mapping_id:
        Unique identifier.
    claims:
        All registered claims.
    coordinate_to_agents:
        Materialised mapping from coordinate prefix to list of agent_ids.
    is_coherent:
        True if the mapping satisfies the sheaf gluing condition.
    gaps:
        Coordinate prefixes that have no covering claim.
    conflicts:
        Detected :class:`JurisdictionConflict` objects.
    computed_at:
        Unix timestamp of last materialisation.
    """

    mapping_id: str = field(default_factory=lambda: "am_" + uuid.uuid4().hex[:12])
    claims: tuple[JurisdictionClaim, ...] = ()
    coordinate_to_agents: dict = field(default_factory=dict)
    is_coherent: bool = True
    gaps: tuple[str, ...] = ()
    conflicts: tuple = ()  # tuple[JurisdictionConflict, ...]
    computed_at: float = field(default_factory=time.time)

    def authoritative_agents(self, coordinate: str) -> list[str]:
        """Return the list of agents authoritative for *coordinate*."""
        result = []
        for claim in self.claims:
            if claim.covers(coordinate) and claim.is_active():
                result.append(claim.agent_id)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_id": self.mapping_id,
            "claims": [c.to_dict() for c in self.claims],
            "coordinate_to_agents": dict(self.coordinate_to_agents),
            "is_coherent": self.is_coherent,
            "gaps": list(self.gaps),
            "conflict_count": len(self.conflicts),
            "computed_at": self.computed_at,
        }


@dataclass(frozen=True, slots=True)
class JurisdictionConflict:
    """A detected conflict between two or more jurisdiction claims.

    Parameters
    ----------
    conflict_id:
        Unique identifier.
    claim_ids:
        The claim IDs involved.
    coordinate:
        The coordinate at which the conflict occurs.
    kind:
        :class:`JurisdictionConflictKind`.
    description:
        Human-readable description.
    is_resolved:
        True if a :class:`ResolutionRecord` has been produced.
    """

    conflict_id: str = field(default_factory=lambda: "jconf_" + uuid.uuid4().hex[:12])
    claim_ids: tuple[str, ...] = ()
    coordinate: str = ""
    kind: str = JurisdictionConflictKind.OVERLAP.value
    description: str = ""
    is_resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "claim_ids": list(self.claim_ids),
            "coordinate": self.coordinate,
            "kind": self.kind,
            "description": self.description,
            "is_resolved": self.is_resolved,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JurisdictionConflict:
        return cls(
            conflict_id=d.get("conflict_id", "jconf_" + uuid.uuid4().hex[:12]),
            claim_ids=tuple(d.get("claim_ids", [])),
            coordinate=d.get("coordinate", ""),
            kind=d.get("kind", JurisdictionConflictKind.OVERLAP.value),
            description=d.get("description", ""),
            is_resolved=bool(d.get("is_resolved", False)),
        )


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    """The outcome of resolving a jurisdiction conflict.

    Parameters
    ----------
    resolution_id:
        Unique identifier.
    conflict_id:
        The :class:`JurisdictionConflict` that was resolved.
    strategy:
        The :class:`ResolutionStrategy` that was applied.
    winning_claim_id:
        The claim that won (if strategy is PRIORITY or DROP_LOWER).
    resulting_tier:
        The trust tier of the resolution (always PROPOSAL unless independently promoted).
    rationale:
        Free-text explanation.
    resolved_at:
        Unix timestamp.
    """

    resolution_id: str = field(default_factory=lambda: "rr_" + uuid.uuid4().hex[:12])
    conflict_id: str = ""
    strategy: str = ResolutionStrategy.PRIORITY.value
    winning_claim_id: str = ""
    resulting_tier: str = "PROPOSAL"
    rationale: str = ""
    resolved_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "conflict_id": self.conflict_id,
            "strategy": self.strategy,
            "winning_claim_id": self.winning_claim_id,
            "resulting_tier": self.resulting_tier,
            "rationale": self.rationale,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResolutionRecord:
        return cls(
            resolution_id=d.get("resolution_id", "rr_" + uuid.uuid4().hex[:12]),
            conflict_id=d.get("conflict_id", ""),
            strategy=d.get("strategy", ResolutionStrategy.PRIORITY.value),
            winning_claim_id=d.get("winning_claim_id", ""),
            resulting_tier=d.get("resulting_tier", "PROPOSAL"),
            rationale=d.get("rationale", ""),
            resolved_at=float(d.get("resolved_at", time.time())),
        )


@dataclass(frozen=True, slots=True)
class SemanticDomain:
    """A named semantic domain with its associated jurisdiction claims.

    A semantic domain groups related coordinates (e.g. all coordinates in the
    ``type_theory`` namespace) and declares which agents are authoritative over
    them.

    Parameters
    ----------
    domain_id:
        Unique identifier.
    name:
        Human-readable name (e.g. ``'type_theory'``).
    root_prefix:
        Coordinate prefix that defines membership in this domain.
    primary_agents:
        Agents with PRIMARY authority over this domain.
    shared_agents:
        Agents with SHARED authority.
    advisory_agents:
        Agents with ADVISORY authority.
    trust_tier:
        The trust tier of this domain definition.
    description:
        Human-readable description.
    """

    domain_id: str = field(default_factory=lambda: "sd_" + uuid.uuid4().hex[:12])
    name: str = ""
    root_prefix: str = ""
    primary_agents: tuple[str, ...] = ()
    shared_agents: tuple[str, ...] = ()
    advisory_agents: tuple[str, ...] = ()
    trust_tier: str = "PROPOSAL"
    description: str = ""

    def all_agents(self) -> list[str]:
        return list(self.primary_agents) + list(self.shared_agents) + list(self.advisory_agents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "name": self.name,
            "root_prefix": self.root_prefix,
            "primary_agents": list(self.primary_agents),
            "shared_agents": list(self.shared_agents),
            "advisory_agents": list(self.advisory_agents),
            "trust_tier": self.trust_tier,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticDomain:
        return cls(
            domain_id=d.get("domain_id", "sd_" + uuid.uuid4().hex[:12]),
            name=d.get("name", ""),
            root_prefix=d.get("root_prefix", ""),
            primary_agents=tuple(d.get("primary_agents", [])),
            shared_agents=tuple(d.get("shared_agents", [])),
            advisory_agents=tuple(d.get("advisory_agents", [])),
            trust_tier=d.get("trust_tier", "PROPOSAL"),
            description=d.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Witness (immutable certificate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticJurisdictionWitness:
    """Immutable certificate produced by a semantic jurisdiction computation.

    Captures the authority mapping, detected conflicts, resolutions, and the
    Theory2.tex judgment tuple ``(c, φ, A, E, O, B, T, Π)``.

    Parameters
    ----------
    witness_id:
        Globally unique identifier.
    coordinate:
        The coordinate whose jurisdiction was computed.
    authoritative_agents:
        Agents found to be authoritative for *coordinate*.
    authority_mapping:
        The :class:`AuthorityMapping` produced.
    conflicts:
        Detected :class:`JurisdictionConflict` objects.
    resolutions:
        Applied :class:`ResolutionRecord` objects.
    final_tier:
        Trust tier of the jurisdiction determination.
    is_coherent:
        True if the authority mapping is coherent.
    judgment_tuple:
        ``(c, φ, A, E, O, B, T, Π)`` as a dict.
    created_at:
        ISO-8601 UTC timestamp.
    metadata:
        Extension key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: "sjw_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    authoritative_agents: tuple[str, ...] = ()
    authority_mapping: AuthorityMapping = field(default_factory=AuthorityMapping)
    conflicts: tuple[JurisdictionConflict, ...] = ()
    resolutions: tuple[ResolutionRecord, ...] = ()
    final_tier: str = "PROPOSAL"
    is_coherent: bool = True
    judgment_tuple: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "authoritative_agents": list(self.authoritative_agents),
            "authority_mapping": self.authority_mapping.to_dict(),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "resolutions": [r.to_dict() for r in self.resolutions],
            "final_tier": self.final_tier,
            "is_coherent": self.is_coherent,
            "judgment_tuple": dict(self.judgment_tuple),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticJurisdictionWitness:
        return cls(
            witness_id=d.get("witness_id", "sjw_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            authoritative_agents=tuple(d.get("authoritative_agents", [])),
            authority_mapping=AuthorityMapping(
                mapping_id=d.get("authority_mapping", {}).get("mapping_id", "am_" + uuid.uuid4().hex[:12]),
            ),
            conflicts=tuple(
                JurisdictionConflict.from_dict(c) for c in d.get("conflicts", [])
            ),
            resolutions=tuple(
                ResolutionRecord.from_dict(r) for r in d.get("resolutions", [])
            ),
            final_tier=d.get("final_tier", "PROPOSAL"),
            is_coherent=bool(d.get("is_coherent", True)),
            judgment_tuple=dict(d.get("judgment_tuple", {})),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            metadata=dict(d.get("metadata", {})),
        )

    def validate(self) -> list[str]:
        """Return invariant violations (empty = valid).

        Checks:
        - ``final_tier`` is a known tier label.
        - ``judgment_tuple`` is non-empty.
        - ``authoritative_agents`` is non-empty (at least one agent must be authoritative).
        """
        errors: list[str] = []
        if self.final_tier not in _TRUST_ORDER:
            errors.append(f"final_tier {self.final_tier!r} not in trust algebra")
        if not self.judgment_tuple:
            errors.append("judgment_tuple must be non-empty (Theory2.tex invariant)")
        if not self.authoritative_agents and not self.conflicts:
            errors.append(
                "authoritative_agents is empty and no conflicts recorded — "
                "jurisdiction is undetermined"
            )
        return errors

    def merge(self, other: SemanticJurisdictionWitness) -> SemanticJurisdictionWitness:
        """Merge two witnesses (conservative: weaker tier, union of agents and conflicts)."""
        my_rank = _TRUST_ORDER.get(self.final_tier, 0)
        other_rank = _TRUST_ORDER.get(other.final_tier, 0)
        merged_tier = self.final_tier if my_rank <= other_rank else other.final_tier
        merged_agents = tuple(set(self.authoritative_agents) | set(other.authoritative_agents))
        merged_conflicts = self.conflicts + other.conflicts
        merged_resolutions = self.resolutions + other.resolutions
        merged_meta = {
            **self.metadata, **other.metadata,
            "merged_from": [self.witness_id, other.witness_id],
        }
        return replace(
            self,
            witness_id="sjw_" + uuid.uuid4().hex[:12],
            final_tier=merged_tier,
            authoritative_agents=merged_agents,
            conflicts=merged_conflicts,
            resolutions=merged_resolutions,
            is_coherent=self.is_coherent and other.is_coherent,
            metadata=merged_meta,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def content_hash(self) -> str:
        content = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


@dataclass
class SemanticJurisdictionCoordinator:
    """Orchestrates semantic jurisdiction computation over a coordinate site.

    Implements Theory2.tex §7.2 jurisdiction sheaf:
    1. Register jurisdiction claims from agents.
    2. Compute the authority mapping for a given coordinate.
    3. Detect pairwise conflicts.
    4. Apply the configured resolution strategy.
    5. Emit a :class:`SemanticJurisdictionWitness`.

    Parameters
    ----------
    coordinator_id:
        Unique identifier.
    claims:
        All registered :class:`JurisdictionClaim` objects.
    domains:
        Registered :class:`SemanticDomain` objects.
    default_resolution_strategy:
        Fallback strategy when no domain-specific strategy is set.
    agent_priorities:
        Mapping from agent_id to numeric priority (higher = more authoritative).
    history:
        All witnesses produced.
    """

    coordinator_id: str = field(default_factory=lambda: "sjc_" + uuid.uuid4().hex[:12])
    claims: list[JurisdictionClaim] = field(default_factory=list)
    domains: list[SemanticDomain] = field(default_factory=list)
    default_resolution_strategy: str = ResolutionStrategy.PRIORITY.value
    agent_priorities: dict[str, int] = field(default_factory=dict)
    history: list[SemanticJurisdictionWitness] = field(default_factory=list)

    # ---- registration ----

    def register_claim(self, claim: JurisdictionClaim) -> None:
        """Register a jurisdiction claim."""
        self.claims.append(claim)
        logger.debug("Registered claim %s for agent %s", claim.claim_id, claim.agent_id)

    def register_domain(self, domain: SemanticDomain) -> None:
        """Register a semantic domain with its pre-declared authority."""
        self.domains.append(domain)
        # Auto-register claims for domain agents
        cr = CoordinateRange(prefix=domain.root_prefix, domain_tag=domain.name)
        for agent in domain.primary_agents:
            self.claims.append(JurisdictionClaim(
                agent_id=agent,
                coordinate_range=cr,
                authority_level=AuthorityLevel.PRIMARY.value,
                trust_tier=domain.trust_tier,
                priority=10,
                rationale=f"Primary authority from domain {domain.name!r}",
            ))
        for agent in domain.shared_agents:
            self.claims.append(JurisdictionClaim(
                agent_id=agent,
                coordinate_range=cr,
                authority_level=AuthorityLevel.SHARED.value,
                trust_tier=domain.trust_tier,
                priority=5,
                rationale=f"Shared authority from domain {domain.name!r}",
            ))

    def set_priority(self, agent_id: str, priority: int) -> None:
        """Set the numeric priority for an agent."""
        self.agent_priorities[agent_id] = priority

    def deregister_agent(self, agent_id: str) -> int:
        """Remove all claims for *agent_id*.  Returns number of claims removed."""
        before = len(self.claims)
        self.claims = [c for c in self.claims if c.agent_id != agent_id]
        return before - len(self.claims)

    # ---- jurisdiction computation ----

    def active_claims_for(self, coordinate: str) -> list[JurisdictionClaim]:
        """Return all active claims that cover *coordinate*."""
        return [c for c in self.claims if c.covers(coordinate) and c.is_active()]

    def build_authority_mapping(self, coordinate: str) -> AuthorityMapping:
        """Build the :class:`AuthorityMapping` for *coordinate*."""
        active = self.active_claims_for(coordinate)
        coord_to_agents: dict[str, list[str]] = {}
        if active:
            coord_to_agents[coordinate] = [c.agent_id for c in active]
        conflicts = list(self._detect_conflicts(active, coordinate))
        is_coherent = not any(c for c in conflicts if c.kind == JurisdictionConflictKind.EXCLUSIVITY_VIOLATION.value)
        gaps: list[str] = []
        if not active:
            gaps.append(coordinate)
        return AuthorityMapping(
            claims=tuple(active),
            coordinate_to_agents=coord_to_agents,
            is_coherent=is_coherent,
            gaps=tuple(gaps),
            conflicts=tuple(conflicts),
        )

    def _detect_conflicts(
        self, claims: list[JurisdictionClaim], coordinate: str
    ) -> list[JurisdictionConflict]:
        """Detect pairwise conflicts among *claims*."""
        conflicts: list[JurisdictionConflict] = []
        exclusive_claims = [c for c in claims if c.authority_level == AuthorityLevel.EXCLUSIVE.value]
        # Multiple exclusive claims = conflict
        if len(exclusive_claims) > 1:
            for i in range(len(exclusive_claims)):
                for j in range(i + 1, len(exclusive_claims)):
                    conflicts.append(JurisdictionConflict(
                        claim_ids=(exclusive_claims[i].claim_id, exclusive_claims[j].claim_id),
                        coordinate=coordinate,
                        kind=JurisdictionConflictKind.EXCLUSIVITY_VIOLATION.value,
                        description=(
                            f"Agents {exclusive_claims[i].agent_id!r} and "
                            f"{exclusive_claims[j].agent_id!r} both claim exclusive "
                            f"jurisdiction over {coordinate!r}"
                        ),
                    ))
        # Overlapping ranges
        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                ca, cb = claims[i], claims[j]
                if ca.coordinate_range.overlaps_with(cb.coordinate_range):
                    if ca.agent_id != cb.agent_id:
                        conflicts.append(JurisdictionConflict(
                            claim_ids=(ca.claim_id, cb.claim_id),
                            coordinate=coordinate,
                            kind=JurisdictionConflictKind.OVERLAP.value,
                            description=(
                                f"Claims {ca.claim_id!r} and {cb.claim_id!r} overlap "
                                f"at coordinate {coordinate!r}"
                            ),
                        ))
        return conflicts

    def resolve_conflicts(
        self,
        conflicts: list[JurisdictionConflict],
        claims: list[JurisdictionClaim],
    ) -> list[ResolutionRecord]:
        """Apply the resolution strategy to each conflict."""
        resolutions = []
        strategy = self.default_resolution_strategy
        for conflict in conflicts:
            involved = [c for c in claims if c.claim_id in conflict.claim_ids]
            if strategy == ResolutionStrategy.PRIORITY.value:
                if involved:
                    winner = max(
                        involved,
                        key=lambda c: self.agent_priorities.get(c.agent_id, c.priority),
                    )
                    resolutions.append(ResolutionRecord(
                        conflict_id=conflict.conflict_id,
                        strategy=strategy,
                        winning_claim_id=winner.claim_id,
                        resulting_tier="PROPOSAL",
                        rationale=f"Priority resolution: agent {winner.agent_id!r} wins",
                    ))
            elif strategy == ResolutionStrategy.DROP_LOWER.value:
                if involved:
                    winner = max(
                        involved,
                        key=lambda c: _TRUST_ORDER.get(c.trust_tier, 0),
                    )
                    resolutions.append(ResolutionRecord(
                        conflict_id=conflict.conflict_id,
                        strategy=strategy,
                        winning_claim_id=winner.claim_id,
                        resulting_tier="PROPOSAL",
                        rationale=f"Drop-lower resolution: agent {winner.agent_id!r} wins",
                    ))
            elif strategy == ResolutionStrategy.ESCALATE.value:
                resolutions.append(ResolutionRecord(
                    conflict_id=conflict.conflict_id,
                    strategy=strategy,
                    winning_claim_id="",
                    resulting_tier="PROPOSAL",
                    rationale="Conflict escalated to human arbitrator",
                ))
            else:  # MERGE or INTERSECTION
                resolutions.append(ResolutionRecord(
                    conflict_id=conflict.conflict_id,
                    strategy=strategy,
                    winning_claim_id="",
                    resulting_tier="PROPOSAL",
                    rationale=f"Strategy {strategy!r} applied — no single winner",
                ))
        return resolutions

    # ---- main entry point ----

    def run(
        self,
        coordinate: str,
        metadata: dict[str, Any] | None = None,
    ) -> SemanticJurisdictionWitness:
        """Compute jurisdiction for *coordinate* and produce a witness.

        Steps:
        1. Gather active claims.
        2. Build authority mapping (includes conflict detection).
        3. Resolve conflicts.
        4. Determine final trust tier.
        5. Produce witness.
        """
        mapping = self.build_authority_mapping(coordinate)
        conflicts = list(mapping.conflicts)
        resolutions = self.resolve_conflicts(conflicts, list(mapping.claims))

        # Authoritative agents after resolution
        resolved_winner_ids = {r.winning_claim_id for r in resolutions if r.winning_claim_id}
        if resolved_winner_ids:
            auth_agents = tuple(
                c.agent_id for c in mapping.claims
                if c.claim_id in resolved_winner_ids
            )
        else:
            auth_agents = tuple(set(c.agent_id for c in mapping.claims))

        # Trust tier: PROPOSAL (jurisdiction claims enter at PROPOSAL)
        final_tier = "PROPOSAL"

        w = SemanticJurisdictionWitness(
            coordinate=coordinate,
            authoritative_agents=auth_agents,
            authority_mapping=mapping,
            conflicts=tuple(conflicts),
            resolutions=tuple(resolutions),
            final_tier=final_tier,
            is_coherent=mapping.is_coherent,
            judgment_tuple=self._build_judgment_tuple(
                coordinate, auth_agents, final_tier, conflicts
            ),
            metadata=metadata or {},
        )
        self.history.append(w)
        logger.info(
            "Jurisdiction computed: coord=%s agents=%s coherent=%s conflicts=%d",
            coordinate, auth_agents, mapping.is_coherent, len(conflicts),
        )
        return w

    def _build_judgment_tuple(
        self,
        coordinate: str,
        agents: tuple[str, ...],
        trust_tier: str,
        conflicts: list[JurisdictionConflict],
    ) -> dict[str, Any]:
        return {
            "c": coordinate,
            "phi": f"semantic_jurisdiction({coordinate})",
            "A": list(agents),
            "E": {"jurisdiction_claim": True, "agent_count": len(agents)},
            "O": [c.description for c in conflicts],
            "B": self.default_resolution_strategy,
            "T": trust_tier,
            "Pi": self.coordinator_id,
        }

    # ---- introspection ----

    def coverage_report(self) -> dict[str, Any]:
        """Return a report of which coordinate prefixes are covered by which agents."""
        coverage: dict[str, list[str]] = {}
        for claim in self.claims:
            if claim.is_active():
                prefix = claim.coordinate_range.prefix or "(global)"
                coverage.setdefault(prefix, []).append(claim.agent_id)
        return coverage

    def gap_coordinates(self, coordinates: Sequence[str]) -> list[str]:
        """Return coordinates from *coordinates* that have no active claim."""
        return [c for c in coordinates if not self.active_claims_for(c)]

    def conflict_count(self) -> int:
        """Return total number of conflicts across all history."""
        return sum(len(w.conflicts) for w in self.history)

    def validate(self) -> list[str]:
        """Return invariant violations for this coordinator."""
        errors: list[str] = []
        for claim in self.claims:
            if claim.trust_tier not in _TRUST_ORDER:
                errors.append(f"Claim {claim.claim_id!r} has invalid trust_tier {claim.trust_tier!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "claim_count": len(self.claims),
            "domain_count": len(self.domains),
            "default_resolution_strategy": self.default_resolution_strategy,
            "agent_priorities": dict(self.agent_priorities),
            "history_count": len(self.history),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticJurisdictionCoordinator:
        return cls(
            coordinator_id=d.get("coordinator_id", "sjc_" + uuid.uuid4().hex[:12]),
            default_resolution_strategy=d.get(
                "default_resolution_strategy", ResolutionStrategy.PRIORITY.value
            ),
            agent_priorities=dict(d.get("agent_priorities", {})),
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


@dataclass
class SemanticJurisdictionAnalyzer:
    """Analyzes a corpus of jurisdiction witnesses to assess authority health.

    Provides metrics on conflict rates, coherence, gap coverage, resolution
    effectiveness, and anomaly detection.

    Theory2.tex relevance: a well-managed jurisdiction should have high
    coherence, low conflict rate, and zero gaps.  Persistent conflicts
    indicate that the authority map needs redesign.
    """

    analyzer_id: str = field(default_factory=lambda: "sja_" + uuid.uuid4().hex[:12])
    witnesses: list[SemanticJurisdictionWitness] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)

    def load(self, witnesses: Sequence[SemanticJurisdictionWitness]) -> None:
        self.witnesses = list(witnesses)
        self._cache.clear()

    def append(self, witness: SemanticJurisdictionWitness) -> None:
        self.witnesses.append(witness)
        self._cache.clear()

    # ---- core analysis ----

    def analyze(self) -> dict[str, Any]:
        """Return structured analysis of the witness corpus."""
        if "analysis" in self._cache:
            return self._cache["analysis"]  # type: ignore[return-value]
        result = {
            "total": len(self.witnesses),
            "coherence_rate": self._coherence_rate(),
            "conflict_stats": self._conflict_stats(),
            "agent_authority_distribution": self._agent_authority_distribution(),
            "resolution_stats": self._resolution_stats(),
            "tier_distribution": self._tier_distribution(),
            "anomalies": self._detect_anomalies(),
        }
        self._cache["analysis"] = result
        return result

    def score(self) -> float:
        """Return a jurisdiction-health score in [0, 1]."""
        if not self.witnesses:
            return 0.0
        a = self.analyze()
        coherence = a["coherence_rate"]
        conflict_rate = a["conflict_stats"].get("conflict_rate", 0.0)
        anomaly_count = len(a.get("anomalies", []))
        return max(0.0, coherence - conflict_rate - anomaly_count * 0.1)

    def report(self) -> str:
        """Return a human-readable multi-line report."""
        a = self.analyze()
        lines = [
            "=== SemanticJurisdiction Analysis Report ===",
            f"Total witnesses: {a['total']}",
            f"Jurisdiction-health score: {self.score():.3f}",
            f"Coherence rate: {a['coherence_rate']:.3f}",
            "",
            "--- Conflict stats ---",
        ]
        for k, v in a["conflict_stats"].items():
            lines.append(f"  {k}: {v}")
        lines += ["", "--- Agent authority distribution ---"]
        for agent, count in a["agent_authority_distribution"].items():
            lines.append(f"  {agent}: {count}")
        if a.get("anomalies"):
            lines += ["", "--- Anomalies ---"]
            for an in a["anomalies"]:
                lines.append(f"  {an}")
        return "\n".join(lines)

    def summarize(self) -> dict[str, Any]:
        return {
            "analyzer_id": self.analyzer_id,
            "total_witnesses": len(self.witnesses),
            "score": self.score(),
            "coherence_rate": self._coherence_rate(),
        }

    # ---- specialist metrics ----

    def agents_by_authority_count(self) -> list[tuple[str, int]]:
        """Return agents sorted by number of witnesses in which they are authoritative."""
        dist = self._agent_authority_distribution()
        return sorted(dist.items(), key=lambda kv: kv[1], reverse=True)

    def most_conflicted_coordinates(self, top_n: int = 5) -> list[tuple[str, int]]:
        """Return the *top_n* coordinates with the most conflicts."""
        counts: dict[str, int] = {}
        for w in self.witnesses:
            for c in w.conflicts:
                counts[c.coordinate] = counts.get(c.coordinate, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    def incoherent_witnesses(self) -> list[SemanticJurisdictionWitness]:
        """Return witnesses where the authority mapping was incoherent."""
        return [w for w in self.witnesses if not w.is_coherent]

    # ---- private helpers ----

    def _coherence_rate(self) -> float:
        if not self.witnesses:
            return 1.0
        coherent = sum(1 for w in self.witnesses if w.is_coherent)
        return coherent / len(self.witnesses)

    def _conflict_stats(self) -> dict[str, Any]:
        total = sum(len(w.conflicts) for w in self.witnesses)
        runs_with = sum(1 for w in self.witnesses if w.conflicts)
        return {
            "total_conflicts": total,
            "runs_with_conflicts": runs_with,
            "conflict_rate": runs_with / len(self.witnesses) if self.witnesses else 0.0,
        }

    def _agent_authority_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            for agent in w.authoritative_agents:
                counts[agent] = counts.get(agent, 0) + 1
        return counts

    def _resolution_stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            for r in w.resolutions:
                counts[r.strategy] = counts.get(r.strategy, 0) + 1
        return counts

    def _tier_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.final_tier] = counts.get(w.final_tier, 0) + 1
        return counts

    def _detect_anomalies(self) -> list[str]:
        anomalies = []
        conflict_rate = self._conflict_stats().get("conflict_rate", 0.0)
        if conflict_rate > 0.3:
            anomalies.append(
                f"High conflict rate {conflict_rate:.1%} — authority map may be over-specified"
            )
        coherence = self._coherence_rate()
        if coherence < 0.7:
            anomalies.append(
                f"Low coherence rate {coherence:.1%} — gluing condition frequently violated"
            )
        return anomalies


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== semantic_jurisdiction.py smoke test ===")

    coordinator = SemanticJurisdictionCoordinator()

    # Register a domain
    domain = SemanticDomain(
        name="type_theory",
        root_prefix="type_theory.",
        primary_agents=("lean_solver",),
        shared_agents=("llm_oracle",),
        trust_tier="PROPOSAL",
        description="All coordinates in the type-theory semantic domain",
    )
    coordinator.register_domain(domain)
    coordinator.set_priority("lean_solver", priority=20)
    coordinator.set_priority("llm_oracle", priority=5)

    # Validate
    violations = coordinator.validate()
    assert violations == [], f"Violations: {violations}"

    # Run jurisdiction computation
    witness = coordinator.run("type_theory.Nat.succ")
    print(f"Witness ID: {witness.witness_id}")
    print(f"Authoritative agents: {witness.authoritative_agents}")
    print(f"Final tier: {witness.final_tier}")
    assert witness.final_tier == "PROPOSAL"

    # Validate witness
    w_errors = witness.validate()
    assert w_errors == [], f"Witness errors: {w_errors}"

    # Roundtrip
    d = witness.to_dict()
    w2 = SemanticJurisdictionWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Merge
    w3 = witness.merge(w2)
    print(f"Merged witness ID: {w3.witness_id}")

    # Analyzer
    analyzer = SemanticJurisdictionAnalyzer(witnesses=[witness])
    score = analyzer.score()
    print(f"Jurisdiction-health score: {score:.3f}")
    print(analyzer.report())

    # Coverage report
    report = coordinator.coverage_report()
    print(f"Coverage report: {report}")

    print("\n[PASS] All smoke tests passed.")
