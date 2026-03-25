r"""Shared dataclass models for the ``jugeo.se_theory.teams`` package.

Theory (JuGeo — "Teams as Sheaves of Authority", B9):
    In the judgment-geometry framework, a software team is modelled as a local
    section of an *authority sheaf* over the coordinate site.  Each team owns
    a sub-cover (a set of glob patterns) and can delegate sub-authorities to
    other teams via restriction morphisms.

    Key theoretical correspondences:
    * Team                 ↔ section of authority sheaf at a sub-cover
    * Jurisdiction         ↔ restriction of authority to a coordinate pattern
    * AuthorityGrant       ↔ explicit restriction morphism between sections
    * CrossTeamTreaty      ↔ gluing datum at an overlap of two sub-covers
    * ObstructionEscalation↔ failed gluing lifted through organisational stalk
    * JurisdictionReport   ↔ global section inventory of the authority sheaf

    Trust levels follow the ordering established by the evidence sheaf:
        none < claim < conjecture < heuristic < proof < verified

    copilot: se-theory-teams-models
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "TeamRole",
    "AuthorityLevel",
    "EscalationLevel",
    # Dataclasses
    "Team",
    "Jurisdiction",
    "CodeownersEntry",
    "CodeownersMapping",
    "AuthorityGrant",
    "ObstructionEscalation",
    "CrossTeamTreaty",
    "JurisdictionReport",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TeamRole(str, Enum):
    """Role a member plays within a team.

    * ``OWNER``       — final decision-maker; can grant authority to others
    * ``MAINTAINER``  — day-to-day responsible; can approve changes
    * ``CONTRIBUTOR`` — submits changes; requires approval
    * ``REVIEWER``    — can review but cannot merge
    * ``OBSERVER``    — read-only access; no voting rights
    """

    OWNER = "owner"
    MAINTAINER = "maintainer"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    OBSERVER = "observer"


class AuthorityLevel(str, Enum):
    """Strength of authority a team holds over a coordinate.

    * ``FULL``        — can read, write, approve, and re-delegate
    * ``APPROVE``     — can approve changes; cannot re-delegate further
    * ``REVIEW_ONLY`` — can review and comment; cannot approve
    * ``READ_ONLY``   — can view; no modification or approval rights
    """

    FULL = "full"
    APPROVE = "approve"
    REVIEW_ONLY = "review_only"
    READ_ONLY = "read_only"


class EscalationLevel(str, Enum):
    """Organisational scope of an obstruction escalation.

    * ``TEAM``         — resolved within the owning team
    * ``DEPARTMENT``   — escalated to a department lead or parent team
    * ``ORGANIZATION`` — escalated to an org-wide authority
    * ``EMERGENCY``    — critical path blockage; executive involvement
    """

    TEAM = "team"
    DEPARTMENT = "department"
    ORGANIZATION = "organization"
    EMERGENCY = "emergency"


# ---------------------------------------------------------------------------
# Authority ordering helpers
# ---------------------------------------------------------------------------

_AUTHORITY_RANK: dict[AuthorityLevel, int] = {
    AuthorityLevel.READ_ONLY: 0,
    AuthorityLevel.REVIEW_ONLY: 1,
    AuthorityLevel.APPROVE: 2,
    AuthorityLevel.FULL: 3,
}


def authority_rank(level: AuthorityLevel) -> int:
    """Return a numeric rank for comparison (higher = stronger authority)."""
    return _AUTHORITY_RANK.get(level, 0)


def weaker_authority(a: AuthorityLevel, b: AuthorityLevel) -> AuthorityLevel:
    """Return the weaker (more restricted) of two authority levels."""
    return a if authority_rank(a) <= authority_rank(b) else b


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Team:
    """A software team and its authority configuration.

    Attributes
    ----------
    id:
        Unique team identifier (e.g. ``"platform-infra"``).
    name:
        Human-readable display name.
    members:
        List of member identifiers (user IDs or handles).
    authority_level:
        Default authority this team holds over its owned coordinates.
    coordinate_patterns:
        Glob patterns describing the coordinates this team owns.
    trust_ceiling:
        Maximum trust level this team is permitted to grant in evidence
        it produces (e.g. ``"proof"`` or ``"verified"``).
    parent_team_id:
        Optional ID of the parent team in the organisational hierarchy.
    metadata:
        Arbitrary extra data (e.g. Slack channel, on-call rotation link).
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    name: str = ""
    members: list[str] = field(default_factory=list)
    authority_level: AuthorityLevel = AuthorityLevel.APPROVE
    coordinate_patterns: list[str] = field(default_factory=list)
    trust_ceiling: str = "proof"
    parent_team_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "members": list(self.members),
            "authority_level": self.authority_level.value,
            "coordinate_patterns": list(self.coordinate_patterns),
            "trust_ceiling": self.trust_ceiling,
            "parent_team_id": self.parent_team_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Team:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            name=data.get("name", ""),
            members=list(data.get("members", [])),
            authority_level=AuthorityLevel(
                data.get("authority_level", "approve")
            ),
            coordinate_patterns=list(data.get("coordinate_patterns", [])),
            trust_ceiling=data.get("trust_ceiling", "proof"),
            parent_team_id=data.get("parent_team_id"),
            metadata=dict(data.get("metadata", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Team(id={self.id!r}, name={self.name!r}, "
            f"authority={self.authority_level.value})"
        )


@dataclass
class Jurisdiction:
    """A team's authority over a specific coordinate pattern.

    Attributes
    ----------
    team_id:
        The team that holds authority.
    coordinate_pattern:
        Glob (or exact) pattern identifying the covered coordinates.
    authority:
        Strength of authority granted.
    trust_ceiling:
        Maximum trust the team can certify for evidence at these coords.
    delegated_from:
        If non-None, the ID of the team that delegated this jurisdiction.
    delegation_depth:
        How many hops deep in the delegation chain (0 = primary owner).
    conditions:
        Optional constraints on the jurisdiction, e.g.
        ``{"requires_tests": True, "min_reviewers": 2}``.
    """

    team_id: str = ""
    coordinate_pattern: str = ""
    authority: AuthorityLevel = AuthorityLevel.APPROVE
    trust_ceiling: str = "proof"
    delegated_from: Optional[str] = None
    delegation_depth: int = 0
    conditions: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "coordinate_pattern": self.coordinate_pattern,
            "authority": self.authority.value,
            "trust_ceiling": self.trust_ceiling,
            "delegated_from": self.delegated_from,
            "delegation_depth": self.delegation_depth,
            "conditions": self.conditions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Jurisdiction:
        return cls(
            team_id=data.get("team_id", ""),
            coordinate_pattern=data.get("coordinate_pattern", ""),
            authority=AuthorityLevel(data.get("authority", "approve")),
            trust_ceiling=data.get("trust_ceiling", "proof"),
            delegated_from=data.get("delegated_from"),
            delegation_depth=int(data.get("delegation_depth", 0)),
            conditions=data.get("conditions"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Jurisdiction(team={self.team_id!r}, "
            f"pattern={self.coordinate_pattern!r}, "
            f"authority={self.authority.value})"
        )


@dataclass
class CodeownersEntry:
    """A single line from a CODEOWNERS file.

    Attributes
    ----------
    pattern:
        File/directory glob pattern (CODEOWNERS format).
    teams:
        List of team handles that own the pattern.
    priority:
        Specificity score — more specific patterns have higher priority.
        Computed automatically; higher wins when multiple patterns match.
    """

    pattern: str = ""
    teams: list[str] = field(default_factory=list)
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "teams": list(self.teams),
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeownersEntry:
        return cls(
            pattern=data.get("pattern", ""),
            teams=list(data.get("teams", [])),
            priority=int(data.get("priority", 0)),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CodeownersEntry(pattern={self.pattern!r}, teams={self.teams!r})"
        )


@dataclass
class CodeownersMapping:
    """Parsed representation of an entire CODEOWNERS file.

    Attributes
    ----------
    entries:
        All parsed entries, ordered from least to most specific.
    source_file:
        Path to the original CODEOWNERS file.
    """

    entries: list[CodeownersEntry] = field(default_factory=list)
    source_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeownersMapping:
        return cls(
            entries=[
                CodeownersEntry.from_dict(e)
                for e in data.get("entries", [])
            ],
            source_file=data.get("source_file", ""),
        )


@dataclass
class AuthorityGrant:
    """An explicit delegation of authority from one team to another.

    Attributes
    ----------
    id:
        Unique grant identifier.
    granting_team:
        Team that is delegating part of its authority.
    receiving_team:
        Team that receives the delegated authority.
    coordinate_patterns:
        Patterns describing the scope of the delegation.
    authority:
        Level of authority being granted.
    trust_attenuation:
        Maximum trust level the receiving team can certify after
        attenuation through this delegation chain.
    valid_from:
        ISO-8601 timestamp from which the grant is active.
    valid_until:
        Optional ISO-8601 timestamp after which the grant expires.
    conditions:
        Optional constraints, e.g. ``{"auto_merge_disabled": True}``.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    granting_team: str = ""
    receiving_team: str = ""
    coordinate_patterns: list[str] = field(default_factory=list)
    authority: AuthorityLevel = AuthorityLevel.REVIEW_ONLY
    trust_attenuation: str = "heuristic"
    valid_from: str = ""
    valid_until: Optional[str] = None
    conditions: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "granting_team": self.granting_team,
            "receiving_team": self.receiving_team,
            "coordinate_patterns": list(self.coordinate_patterns),
            "authority": self.authority.value,
            "trust_attenuation": self.trust_attenuation,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "conditions": self.conditions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthorityGrant:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            granting_team=data.get("granting_team", ""),
            receiving_team=data.get("receiving_team", ""),
            coordinate_patterns=list(data.get("coordinate_patterns", [])),
            authority=AuthorityLevel(data.get("authority", "review_only")),
            trust_attenuation=data.get("trust_attenuation", "heuristic"),
            valid_from=data.get("valid_from", ""),
            valid_until=data.get("valid_until"),
            conditions=data.get("conditions"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"AuthorityGrant(id={self.id!r}, "
            f"from={self.granting_team!r}→{self.receiving_team!r}, "
            f"authority={self.authority.value})"
        )


@dataclass
class ObstructionEscalation:
    """An unresolved gluing obstruction routed to a responsible team.

    Attributes
    ----------
    obstruction_id:
        Unique identifier of the obstruction.
    coordinate_id:
        Coordinate at which the gluing failed.
    blast_radius:
        Number of coordinates transitively affected.
    escalation_level:
        Organisational scope of the escalation.
    responsible_team:
        Team currently assigned to resolve the obstruction.
    escalation_chain:
        Ordered list of team IDs notified, from most local to most global.
    status:
        Lifecycle status: OPEN / ACKNOWLEDGED / ASSIGNED / RESOLVED.
    created_at:
        ISO-8601 creation timestamp.
    acknowledged_at:
        Optional ISO-8601 timestamp of first acknowledgement.
    resolved_at:
        Optional ISO-8601 timestamp of resolution.
    """

    obstruction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate_id: str = ""
    blast_radius: int = 0
    escalation_level: EscalationLevel = EscalationLevel.TEAM
    responsible_team: str = ""
    escalation_chain: list[str] = field(default_factory=list)
    status: str = "OPEN"
    created_at: str = field(
        default_factory=lambda: _iso_now()
    )
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate_id": self.coordinate_id,
            "blast_radius": self.blast_radius,
            "escalation_level": self.escalation_level.value,
            "responsible_team": self.responsible_team,
            "escalation_chain": list(self.escalation_chain),
            "status": self.status,
            "created_at": self.created_at,
            "acknowledged_at": self.acknowledged_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObstructionEscalation:
        return cls(
            obstruction_id=data.get(
                "obstruction_id", uuid.uuid4().hex[:16]
            ),
            coordinate_id=data.get("coordinate_id", ""),
            blast_radius=int(data.get("blast_radius", 0)),
            escalation_level=EscalationLevel(
                data.get("escalation_level", "team")
            ),
            responsible_team=data.get("responsible_team", ""),
            escalation_chain=list(data.get("escalation_chain", [])),
            status=data.get("status", "OPEN"),
            created_at=data.get("created_at", _iso_now()),
            acknowledged_at=data.get("acknowledged_at"),
            resolved_at=data.get("resolved_at"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ObstructionEscalation(id={self.obstruction_id!r}, "
            f"coord={self.coordinate_id!r}, status={self.status})"
        )


@dataclass
class CrossTeamTreaty:
    """A negotiated agreement between two teams at shared coordinates.

    Attributes
    ----------
    treaty_id:
        Unique treaty identifier.
    team_a:
        First team party to the treaty.
    team_b:
        Second team party to the treaty.
    overlap_coordinates:
        Coordinate IDs that both teams share.
    agreed_propositions:
        List of propositions (strings) both teams have committed to uphold
        at the overlap coordinates.
    trust_floor:
        Minimum acceptable trust level for evidence at the overlap.
    review_policy:
        Human-readable review policy, e.g. ``"dual approval"`` or
        ``"team_a primary, team_b shadow"``.
    last_negotiated:
        ISO-8601 timestamp of the most recent negotiation.
    status:
        ACTIVE / PENDING / DISPUTED / EXPIRED.
    """

    treaty_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    team_a: str = ""
    team_b: str = ""
    overlap_coordinates: list[str] = field(default_factory=list)
    agreed_propositions: list[str] = field(default_factory=list)
    trust_floor: str = "heuristic"
    review_policy: str = "dual approval"
    last_negotiated: str = field(default_factory=lambda: _iso_now())
    status: str = "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "treaty_id": self.treaty_id,
            "team_a": self.team_a,
            "team_b": self.team_b,
            "overlap_coordinates": list(self.overlap_coordinates),
            "agreed_propositions": list(self.agreed_propositions),
            "trust_floor": self.trust_floor,
            "review_policy": self.review_policy,
            "last_negotiated": self.last_negotiated,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrossTeamTreaty:
        return cls(
            treaty_id=data.get("treaty_id", uuid.uuid4().hex[:16]),
            team_a=data.get("team_a", ""),
            team_b=data.get("team_b", ""),
            overlap_coordinates=list(data.get("overlap_coordinates", [])),
            agreed_propositions=list(data.get("agreed_propositions", [])),
            trust_floor=data.get("trust_floor", "heuristic"),
            review_policy=data.get("review_policy", "dual approval"),
            last_negotiated=data.get("last_negotiated", _iso_now()),
            status=data.get("status", "PENDING"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CrossTeamTreaty(id={self.treaty_id!r}, "
            f"{self.team_a!r}↔{self.team_b!r}, status={self.status})"
        )


@dataclass
class JurisdictionReport:
    """Global inventory of the authority sheaf over a site.

    Attributes
    ----------
    total_coordinates:
        Total number of coordinates in the site.
    covered_by_team:
        Coordinates that are claimed by at least one team.
    uncovered:
        Coordinates with no team ownership.
    teams:
        All teams participating in the authority sheaf.
    overlap_conflicts:
        List of coordinate IDs claimed by more than one team with
        conflicting authority.
    escalation_queue:
        Open obstructions awaiting resolution.
    pending_treaties:
        Treaties in PENDING or DISPUTED status.
    """

    total_coordinates: int = 0
    covered_by_team: int = 0
    uncovered: int = 0
    teams: list[Team] = field(default_factory=list)
    overlap_conflicts: list[str] = field(default_factory=list)
    escalation_queue: list[ObstructionEscalation] = field(
        default_factory=list
    )
    pending_treaties: list[CrossTeamTreaty] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_coordinates": self.total_coordinates,
            "covered_by_team": self.covered_by_team,
            "uncovered": self.uncovered,
            "teams": [t.to_dict() for t in self.teams],
            "overlap_conflicts": list(self.overlap_conflicts),
            "escalation_queue": [e.to_dict() for e in self.escalation_queue],
            "pending_treaties": [t.to_dict() for t in self.pending_treaties],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JurisdictionReport:
        return cls(
            total_coordinates=int(data.get("total_coordinates", 0)),
            covered_by_team=int(data.get("covered_by_team", 0)),
            uncovered=int(data.get("uncovered", 0)),
            teams=[Team.from_dict(t) for t in data.get("teams", [])],
            overlap_conflicts=list(data.get("overlap_conflicts", [])),
            escalation_queue=[
                ObstructionEscalation.from_dict(e)
                for e in data.get("escalation_queue", [])
            ],
            pending_treaties=[
                CrossTeamTreaty.from_dict(t)
                for t in data.get("pending_treaties", [])
            ],
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return current UTC time as an ISO-8601 string."""
    import datetime

    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
