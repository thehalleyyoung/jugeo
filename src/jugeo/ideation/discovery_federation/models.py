"""
Models for the discovery_federation package.

This module defines the core data models for the JuGeo Discovery Federation
subsystem, implementing theory2.tex Chapter 61 (Ch61) — Federated Discovery
Authority. The federation protocol enables distributed JuGeo nodes to share,
validate, and reach consensus on discovered geometric and ideational results,
establishing shared authority over novel discoveries through trust-weighted
consensus mechanisms.

The models here encode:
  - FederatedDiscovery: a discovery result shared across nodes
  - FederationConsensus: the consensus record for a shared discovery
  - DiscoveryAuthority: authority granted over a discovery
  - KnowledgePropagation: a propagation event across the federation graph
  - AuthorityGrant: an individual grant record
  - FederationVote: a single cast vote in consensus
  - FederationNode: a participating node in the federation
  - ConflictRecord: a logged conflict between competing discoveries

The federation pipeline proceeds as:
  1. A node makes a discovery (FederatedDiscovery)
  2. The discovery is broadcast to peer nodes (KnowledgePropagation)
  3. Peer nodes cast votes (FederationVote) forming a consensus
  4. If consensus is achieved (FederationConsensus), authority is granted
  5. Authority grants (DiscoveryAuthority, AuthorityGrant) are distributed
  6. Conflicts are logged and resolved via ConflictRecord

Design notes
------------
All frozen dataclasses use ``__slots__`` for compact memory layout and
immutability enforcement.  Mutable models (FederationConsensus,
DiscoveryAuthority, FederationNode) also use ``__slots__`` but omit
``frozen=True`` so that in-place mutation methods (``add_vote``,
``add_discovery``, ``register_discovery``) remain available without having
to rebuild the entire object on every state change.

Timestamps
~~~~~~~~~~
All timestamps are UTC Unix epoch floats produced by :func:`_utcnow`.  No
timezone objects are stored so that records can round-trip through JSON
without any special codec.

Trust integration
~~~~~~~~~~~~~~~~~
Trust tier strings are intentionally kept as plain ``str`` rather than
importing ``TrustTier`` from ``jugeo.evidence.trust``.  This keeps the
models layer importable even when the optional evidence package is absent,
while still carrying enough information for downstream consumers that do
have the package installed.

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""
from __future__ import annotations

import calendar
import datetime
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict, Tuple, Sequence

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

__all__ = [
    "FederationStatus",
    "ConsensusOutcome",
    "AuthorityLevel",
    "FederatedDiscovery",
    "FederationConsensus",
    "DiscoveryAuthority",
    "KnowledgePropagation",
    "AuthorityGrant",
    "FederationVote",
    "FederationNode",
    "ConflictRecord",
]

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (float).

    This helper is used throughout the discovery_federation package to
    obtain consistent, timezone-naive timestamps for all records and
    events. Using a single helper ensures that timestamp semantics
    can be changed in one place (e.g., to use a mock clock in tests).

    Returns:
        float: The current UTC time in seconds since the Unix epoch.

    Example:
        >>> ts = _utcnow()
        >>> assert isinstance(ts, float)
        >>> assert ts > 0
    """
    return time.time()


def _uid() -> str:
    """Generate a new universally unique identifier string.

    Returns a UUID4 string suitable for use as an opaque, collision-resistant
    identifier for federation records, grants, votes, and nodes.  The UUID4
    format provides 122 bits of randomness, making accidental collision
    effectively impossible in normal operation.

    Returns:
        str: A lowercase hyphenated UUID4 string, e.g.
             '3d6f2a1c-8e47-4b3d-9c2a-1f0e5b7d4a6c'.

    Example:
        >>> uid = _uid()
        >>> assert len(uid) == 36
        >>> assert uid.count('-') == 4
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a float value to the inclusive range [lo, hi].

    Used throughout the federation package to keep weights, confidences,
    trust scores, and vote margins within well-defined numeric bounds.
    Preventing out-of-range values avoids unexpected behaviour in
    downstream arithmetic (e.g., negative trust weights or quorum
    fractions greater than 1.0).

    Args:
        value: The value to be clamped.
        lo:    The lower bound (inclusive).
        hi:    The upper bound (inclusive).

    Returns:
        float: ``value`` if ``lo <= value <= hi``, else ``lo`` or ``hi``.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
        >>> _clamp(-0.1, 0.0, 1.0)
        0.0
        >>> _clamp(0.5, 0.0, 1.0)
        0.5
    """
    return max(lo, min(hi, value))


def _merge_metadata(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a shallow merge of two metadata dictionaries.

    Keys present in ``overlay`` take precedence over keys in ``base``.
    Neither input dict is mutated; a fresh dict is always returned.
    This is used when updating FederatedDiscovery metadata without
    rebuilding the frozen object from scratch each time an ancillary
    field changes.

    Args:
        base:    The base metadata dict whose keys serve as defaults.
        overlay: The overlay dict whose keys override those in ``base``.

    Returns:
        Dict[str, Any]: A new dict containing all keys from both inputs,
        with ``overlay`` values winning on collision.

    Example:
        >>> _merge_metadata({"a": 1, "b": 2}, {"b": 99, "c": 3})
        {'a': 1, 'b': 99, 'c': 3}
    """
    result = dict(base)
    result.update(overlay)
    return result


def _weighted_fraction(tally: Dict[str, float], key: str) -> float:
    """Compute the fraction of total weight represented by a single tally key.

    Given a vote tally mapping vote-keys to accumulated weights, returns
    what fraction of the total accumulated weight is attributed to the
    given ``key``.  If the total weight is zero (no votes cast), returns
    0.0 to avoid division-by-zero.

    Args:
        tally: A mapping from vote-key strings to accumulated weight floats.
        key:   The specific vote-key whose fraction is desired.

    Returns:
        float: A value in [0.0, 1.0] representing the proportion of total
        weight held by ``key``, or 0.0 if the tally is empty.

    Example:
        >>> _weighted_fraction({"yes": 3.0, "no": 1.0}, "yes")
        0.75
    """
    total = sum(tally.values())
    if total <= 0.0:
        return 0.0
    return _clamp(tally.get(key, 0.0) / total, 0.0, 1.0)


def _format_timestamp(ts: float) -> str:
    """Format a Unix epoch float as an ISO-8601-like UTC string.

    Produces a human-readable timestamp string in the form
    ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.  Used in ``render_summary`` and
    ``summary`` methods to make log output more legible than raw floats.

    Args:
        ts: A Unix epoch timestamp as returned by :func:`_utcnow`.

    Returns:
        str: An ISO-8601-like string in UTC, e.g.
             ``'2025-07-14T12:34:56.789012Z'``.
    """
    dt = datetime.datetime.utcfromtimestamp(ts)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso_utc(s: str) -> float:
    """Parse ISO-8601 UTC string to Unix timestamp float."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
            return float(calendar.timegm(dt.timetuple())) + dt.microsecond / 1e6
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {s!r}")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FederationStatus(str, Enum):
    """Lifecycle status of a discovery federation instance.

    Tracks the state machine transitions of a federation from initial
    formation through to resolution. The valid transitions are:
        PENDING -> FORMING -> ACTIVE -> MERGED | DISSOLVED

    PENDING   — The federation has been proposed but not yet accepted by
                enough nodes to begin forming.  This is the initial state
                assigned when a discovery is first broadcast.
    FORMING   — Enough nodes have acknowledged the broadcast; the federation
                is gathering votes and establishing consensus criteria.
    ACTIVE    — Consensus has been reached; the federation is live and
                authority has been distributed to participating nodes.
    MERGED    — The federation has been merged into a larger or more
                authoritative federation, and its records are subsumed.
    DISSOLVED — The federation has been explicitly dissolved, either because
                consensus was not reached or the discovery was retracted.
    """

    PENDING    = "pending"     # Initial state; awaiting acknowledgements.
    FORMING    = "forming"     # Consensus gathering in progress.
    ACTIVE     = "active"      # Consensus reached; authority distributed.
    CONTESTED  = "contested"   # Active but under challenge; re-voting.
    MERGED     = "merged"      # Subsumed into a larger federation.
    DISSOLVED  = "dissolved"   # Federation ended without consensus.
    RESOLVED   = "resolved"    # Federation resolved.
    EXPIRED    = "expired"     # Federation timed out without resolution.


class ConsensusOutcome(str, Enum):
    """Possible outcomes of a federation consensus round.

    After votes are tallied in a VotingRound (see federation_consensus.py),
    the outcome is one of these four values.  The outcome determines whether
    authority is granted, deferred, or denied.

    ACCEPTED — A quorum of participating nodes voted in favour of the
               discovery; authority can now be granted.
    REJECTED — A quorum voted against; the discovery is not recognised as
               authoritative by the federation.
    DEFERRED — Quorum was reached but the result was inconclusive (e.g.,
               exactly at threshold); a new round should be opened.
    SPLIT    — The vote is evenly divided with no clear majority; conflict
               resolution procedures should be invoked.
    """

    PENDING   = "pending"   # Initial state; voting not yet begun.
    ACCEPTED  = "accepted"  # Quorum in favour; authority granted.
    REJECTED  = "rejected"  # Quorum against; authority denied.
    ABSTAINED = "abstained" # Quorum abstained; no decision reached.
    DEFERRED  = "deferred"  # Inconclusive; new round required.
    SPLIT     = "split"     # Even division; conflict resolution needed.


class AuthorityLevel(str, Enum):
    """Graduated authority levels for nodes participating in the federation.

    Authority progresses through these levels as trust accumulates.  Higher
    levels confer greater influence over consensus outcomes and allow nodes
    to sponsor other nodes for authority promotion.

    NONE     — The node has no recognised authority over any discovery.
    LOCAL    — Authority recognised within the local cluster only.
    REGIONAL — Authority recognised across a regional partition.
    GLOBAL   — Authority recognised federation-wide.
    SUPREME  — The highest level; may act as tie-breaker in SPLIT outcomes.

    Legacy aliases (kept for backward-compat):
    CANDIDATE   = LOCAL
    PROVISIONAL = REGIONAL
    FULL        = GLOBAL
    SOVEREIGN   = SUPREME
    """

    NONE        = "none"        # No authority recognised.
    LOCAL       = "local"       # Local cluster authority.
    REGIONAL    = "regional"    # Regional partition authority.
    GLOBAL      = "global"      # Federation-wide authority.
    SUPREME     = "supreme"     # Tie-breaking authority.
    # Legacy aliases
    CANDIDATE   = "local"       # Discovery submitted; consensus pending.
    PROVISIONAL = "regional"    # Consensus reached; in grace period.
    FULL        = "global"      # Fully recognised authority.
    SOVEREIGN   = "supreme"     # Sovereign tie-breaking authority.


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederatedDiscovery:
    """Immutable record of a discovery shared across federation nodes."""

    discovery_id: str
    source_node: str
    target_node: str
    trust_score: float
    payload: dict = field(default_factory=dict)
    status: FederationStatus = FederationStatus.PENDING
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def create(
        cls,
        discovery_id: str,
        source_node: str,
        target_node: str,
        trust_score: float,
        payload: Optional[Dict[str, Any]] = None,
        status: FederationStatus = FederationStatus.PENDING,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> "FederatedDiscovery":
        import math
        if not (0.0 <= trust_score <= 1.0) or math.isnan(trust_score):
            raise ValueError(f"trust_score must be in [0.0, 1.0], got {trust_score!r}")
        if not discovery_id:
            raise ValueError("discovery_id cannot be empty")
        now = _format_timestamp(_utcnow())
        return cls(
            discovery_id=discovery_id,
            source_node=source_node,
            target_node=target_node,
            trust_score=float(trust_score),
            payload=payload if payload is not None else {},
            status=status,
            created_at=created_at if created_at is not None else now,
            updated_at=updated_at if updated_at is not None else now,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discovery_id": self.discovery_id,
            "source_node": self.source_node,
            "target_node": self.target_node,
            "trust_score": float(self.trust_score),
            "payload": dict(self.payload),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FederatedDiscovery":
        raw_status = d["status"]
        try:
            status = FederationStatus(raw_status)
        except ValueError:
            status = FederationStatus[raw_status]
        return cls(
            discovery_id=d["discovery_id"],
            source_node=d["source_node"],
            target_node=d["target_node"],
            trust_score=float(d["trust_score"]),
            payload=dict(d.get("payload", {})),
            status=status,
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    def age_seconds(self) -> float:
        if not self.created_at:
            return 0.0
        try:
            ts = _parse_iso_utc(self.created_at)
            return max(0.0, _utcnow() - ts)
        except Exception:
            return 0.0

    def render_summary(self) -> str:
        return (
            f"FederatedDiscovery({self.discovery_id}) "
            f"source={self.source_node} "
            f"target={self.target_node} "
            f"trust={self.trust_score:.3f} "
            f"status={self.status.value}"
        )


@dataclass(frozen=True, slots=True)
class FederationVote:
    """Immutable record of a single vote cast in a consensus round."""

    vote_id: str
    voter_id: str
    position: str
    weight: float = 1.0
    rationale: str = ""
    cast_at: str = ""

    @classmethod
    def create(
        cls,
        vote_id: str,
        voter_id: str,
        position: str,
        weight: float = 1.0,
        rationale: str = "",
        cast_at: Optional[str] = None,
    ) -> "FederationVote":
        if position not in ("YES", "NO", "ABSTAIN"):
            raise ValueError(f"position must be YES, NO, or ABSTAIN; got {position!r}")
        return cls(
            vote_id=vote_id,
            voter_id=voter_id,
            position=position,
            weight=float(weight),
            rationale=rationale,
            cast_at=cast_at if cast_at is not None else _format_timestamp(_utcnow()),
        )

    def effective_weight(self) -> float:
        if self.position == "ABSTAIN":
            return 0.0
        if self.position == "YES":
            return float(self.weight)
        return -float(self.weight)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vote_id": self.vote_id,
            "voter_id": self.voter_id,
            "position": self.position,
            "weight": float(self.weight),
            "rationale": self.rationale,
            "cast_at": self.cast_at,
        }


@dataclass(slots=True)
class FederationConsensus:
    """Mutable consensus record for a shared discovery."""

    consensus_id: str = ""
    discovery_id: str = ""
    votes: List[FederationVote] = field(default_factory=list)
    quorum_threshold: float = 0.5
    outcome: ConsensusOutcome = ConsensusOutcome.PENDING
    created_at: str = ""

    def is_quorum_met(self) -> bool:
        yes_weight = sum(v.weight for v in self.votes if v.position == "YES")
        total_weight = sum(v.weight for v in self.votes)
        if total_weight <= 0.0:
            return False
        return (yes_weight / total_weight) >= self.quorum_threshold

    def winning_margin(self) -> float:
        yes_w = sum(v.weight for v in self.votes if v.position == "YES")
        no_w = sum(v.weight for v in self.votes if v.position == "NO")
        total = yes_w + no_w
        if total <= 0.0:
            return 0.0
        return _clamp(abs(yes_w / total - no_w / total), 0.0, 1.0)

    def add_vote(self, vote: FederationVote) -> None:
        self.votes.append(vote)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consensus_id": self.consensus_id,
            "discovery_id": self.discovery_id,
            "votes": [v.to_dict() for v in self.votes],
            "quorum_threshold": self.quorum_threshold,
            "outcome": self.outcome.value,
            "created_at": self.created_at,
        }

    def summary(self) -> str:
        return (
            f"FederationConsensus({self.consensus_id}) "
            f"disc={self.discovery_id} "
            f"votes={len(self.votes)} "
            f"outcome={self.outcome.value}"
        )


@dataclass(slots=True)
class DiscoveryAuthority:
    """Mutable authority record granted over a discovery."""

    authority_id: str
    node_id: str
    level: AuthorityLevel
    domain: str = ""
    discoveries: List[str] = field(default_factory=list)
    granted_at: str = ""
    expires_at: Optional[str] = None
    revoked: bool = False

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        try:
            ts = _parse_iso_utc(self.expires_at)
            return _utcnow() > ts
        except Exception:
            return False

    def is_active(self) -> bool:
        return not self.revoked and not self.is_expired()

    def add_discovery(self, discovery_id: str) -> None:
        if discovery_id not in self.discoveries:
            self.discoveries.append(discovery_id)

    def revoke(self) -> None:
        self.revoked = True

    def promote(self, new_level: AuthorityLevel) -> bool:
        _order = [
            AuthorityLevel.NONE,
            AuthorityLevel.LOCAL,
            AuthorityLevel.REGIONAL,
            AuthorityLevel.GLOBAL,
            AuthorityLevel.SUPREME,
        ]
        current_rank = _order.index(self.level)
        target_rank = _order.index(new_level)
        if target_rank > current_rank:
            self.level = new_level
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "node_id": self.node_id,
            "level": self.level.value,
            "domain": self.domain,
            "discoveries": list(self.discoveries),
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "is_active": self.is_active(),
        }


@dataclass(slots=True)
class KnowledgePropagation:
    """Mutable record of a propagation event across the federation graph."""

    propagation_id: str = ""
    source_node: str = ""
    path: List[str] = field(default_factory=list)
    knowledge_items: List[Any] = field(default_factory=list)
    created_at: str = ""

    def hop_count(self) -> int:
        return max(0, len(self.path) - 1)

    def includes_node(self, node_id: str) -> bool:
        return node_id in self.path

    def extend_path(self, new_node: str) -> None:
        self.path.append(new_node)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "propagation_id": self.propagation_id,
            "source_node": self.source_node,
            "path": list(self.path),
            "knowledge_items": list(self.knowledge_items),
            "created_at": self.created_at,
            "hop_count": self.hop_count(),
        }


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    """Immutable record of an authority grant between nodes."""

    grant_id: str
    grantor_node: str
    grantee_node: str
    level: AuthorityLevel
    domain: str = ""
    granted_at: str = ""
    expires_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        grantor_node: str,
        grantee_node: str,
        level: AuthorityLevel,
        grant_id: Optional[str] = None,
        domain: str = "",
        granted_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AuthorityGrant":
        return cls(
            grant_id=grant_id if grant_id is not None else _uid(),
            grantor_node=grantor_node,
            grantee_node=grantee_node,
            level=level,
            domain=domain,
            granted_at=granted_at if granted_at is not None else _format_timestamp(_utcnow()),
            expires_at=expires_at,
            metadata=metadata if metadata is not None else {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "grantor_node": self.grantor_node,
            "grantee_node": self.grantee_node,
            "level": self.level.value,
            "domain": self.domain,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }

    def summary(self) -> str:
        return (
            f"AuthorityGrant({self.grant_id}) "
            f"grantor={self.grantor_node} -> grantee={self.grantee_node} "
            f"level={self.level.value} "
            f"domain={self.domain}"
        )


@dataclass(slots=True)
class FederationNode:
    """Mutable record of a participating node in the federation."""

    node_id: str
    name: str = ""
    trust_score: float = 0.0
    authority_level: AuthorityLevel = AuthorityLevel.NONE
    discoveries: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    registered_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.authority_level, str):
            try:
                self.authority_level = AuthorityLevel(self.authority_level.lower())
            except ValueError:
                self.authority_level = self._level_from_trust(self.trust_score)
        elif self.authority_level == AuthorityLevel.NONE and self.trust_score > 0:
            self.authority_level = self._level_from_trust(self.trust_score)
        if not self.registered_at:
            self.registered_at = _format_timestamp(_utcnow())

    @staticmethod
    def _level_from_trust(trust_score: float) -> AuthorityLevel:
        score = float(trust_score)
        if score >= 0.8:
            return AuthorityLevel.GLOBAL
        if score >= 0.6:
            return AuthorityLevel.REGIONAL
        if score >= 0.4:
            return AuthorityLevel.LOCAL
        return AuthorityLevel.NONE

    @classmethod
    def create(
        cls,
        trust_score: float = 0.0,
        node_id: str | None = None,
        name: str = "",
        authority_level: AuthorityLevel | str | None = None,
        metadata: dict | None = None,
    ) -> "FederationNode":
        return cls(
            node_id=node_id or _uid(),
            name=name,
            trust_score=float(trust_score),
            authority_level=authority_level or cls._level_from_trust(trust_score),
            metadata=dict(metadata or {}),
            registered_at=_format_timestamp(_utcnow()),
        )

    def register_discovery(self, discovery_id: str) -> None:
        self.discoveries.append(discovery_id)

    def has_discovery(self, discovery_id: str) -> bool:
        return discovery_id in self.discoveries

    def get_authority_level(self) -> AuthorityLevel:
        return self.authority_level

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "trust_score": float(self.trust_score),
            "authority_level": self.authority_level.value,
            "discoveries": list(self.discoveries),
            "metadata": dict(self.metadata),
            "registered_at": self.registered_at,
        }

    def summary(self) -> str:
        return (
            f"FederationNode({self.node_id}) "
            f"name={self.name} "
            f"level={self.authority_level.value} "
            f"trust={self.trust_score:.3f} "
            f"discoveries={len(self.discoveries)}"
        )


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    """Immutable record of a conflict between competing discoveries."""

    conflict_id: str = ""
    parties: List[str] = field(default_factory=list)
    subject: str = ""
    description: str = ""
    created_at: str = ""
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None

    @classmethod
    def create(
        cls,
        conflict_id: Optional[str] = None,
        parties: Optional[List[str]] = None,
        subject: str = "",
        description: str = "",
        created_at: Optional[str] = None,
        resolved_at: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> "ConflictRecord":
        return cls(
            conflict_id=conflict_id if conflict_id is not None else _uid(),
            parties=list(parties) if parties else [],
            subject=subject,
            description=description,
            created_at=created_at if created_at is not None else _format_timestamp(_utcnow()),
            resolved_at=resolved_at,
            resolution=resolution,
        )

    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    def age(self) -> float:
        try:
            ts = _parse_iso_utc(self.created_at)
            return max(0.0, _utcnow() - ts)
        except Exception:
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "parties": list(self.parties),
            "subject": self.subject,
            "description": self.description,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }
