"""Authority model for the JuGeo sheaf-theoretic type-checking system.

In JuGeo's geometric view of type-checking, *authority* answers the question:
which subsystem is **allowed** to make which kinds of judgments?  The solver
has authority over structural and arithmetic claims.  The runtime has
authority over heap, identity, and resource claims.  A copilot or other
oracle has bounded authority—its judgments require a trust ceiling and may
demand corroboration before they are accepted.  No subsystem may silently
exceed its jurisdiction.

This module encodes those rules as a first-class authority model consisting
of domains, ceilings, grants, registries, enforcers, jurisdiction maps,
delegation chains, and an append-only audit log.  The copilot token appears
naturally here because proposal agents are a first-class but bounded
authority in the shared core.

Backward-compatible re-exports
------------------------------
The legacy helpers ``AuthorityTier``, ``DelegationRule``, ``AuthorityCenter``,
``build_authority_center``, and ``validate_delegation_graph`` are preserved
so that existing call-sites (services, tests) continue to work without
modification.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 0. Legacy layer (backward compatibility)
# ---------------------------------------------------------------------------


class AuthorityTier(IntEnum):
    """Coarse trust tiers used by the legacy delegation model.

    PROPOSAL is the lowest tier—suitable for copilot-generated suggestions
    that still require review.  REVIEWED has been vetted by at least one
    corroborating subsystem.  VERIFIED is the highest tier and is reserved
    for judgments produced by the solver or runtime with full evidence.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3


@dataclass(frozen=True, slots=True)
class DelegationRule:
    """A single delegation edge in the legacy authority graph.

    Parameters
    ----------
    target:
        Name of the authority center that receives the delegation.
    capabilities:
        The set of capability tokens being delegated.
    trust_ceiling:
        Maximum trust tier the delegate may exercise.
    requires_explicit_review:
        When ``True``, the delegation is only effective after an explicit
        human or solver review step.
    """

    target: str
    capabilities: frozenset[str]
    trust_ceiling: AuthorityTier
    requires_explicit_review: bool = False


@dataclass(frozen=True, slots=True)
class AuthorityCenter:
    """Named authority center with capabilities and optional delegations.

    This is the legacy representation kept for backward compatibility with
    :mod:`jugeo.kernel.services` and existing tests.  New code should prefer
    the richer :class:`AuthorityGrant` / :class:`AuthorityRegistry` model
    defined below.
    """

    name: str
    capabilities: frozenset[str]
    trust_ceiling: AuthorityTier
    delegations: tuple[DelegationRule, ...] = field(default_factory=tuple)
    provenance: tuple[str, ...] = ()

    def can_issue(self, capability: str, tier: AuthorityTier) -> bool:
        """Return ``True`` if this center may issue *capability* at *tier*."""
        return capability in self.capabilities and tier <= self.trust_ceiling


def build_authority_center(
    name: str,
    *,
    capabilities: Iterable[str],
    trust_ceiling: AuthorityTier,
    delegations: Iterable[DelegationRule] = (),
    provenance: Iterable[str] = (),
) -> AuthorityCenter:
    """Factory for :class:`AuthorityCenter` with coercion to frozen collections."""
    return AuthorityCenter(
        name,
        frozenset(capabilities),
        trust_ceiling,
        tuple(delegations),
        tuple(provenance),
    )


def validate_delegation_graph(authorities: Iterable[AuthorityCenter]) -> None:
    """Validate that no delegation silently promotes trust.

    Raises :class:`JuGeoError` when a delegation edge references an unknown
    target, grants capabilities beyond the target's jurisdiction, or would
    silently promote trust past the source ceiling without explicit review.
    """
    lookup = {authority.name: authority for authority in authorities}
    for authority in lookup.values():
        for delegation in authority.delegations:
            if delegation.target not in lookup:
                raise JuGeoError(
                    StructuredFailure(
                        "unknown-delegation-target",
                        "Delegation references an unknown authority center.",
                        FailureScope.AUTHORITY,
                        {"source": authority.name, "target": delegation.target},
                    )
                )
            target = lookup[delegation.target]
            if not delegation.capabilities.issubset(target.capabilities):
                raise JuGeoError(
                    StructuredFailure(
                        "delegation-capability-mismatch",
                        "Delegation capability exceeds target jurisdiction.",
                        FailureScope.AUTHORITY,
                        {"source": authority.name, "target": target.name},
                    )
                )
            if (
                delegation.trust_ceiling > authority.trust_ceiling
                and not delegation.requires_explicit_review
            ):
                raise JuGeoError(
                    StructuredFailure(
                        "silent-trust-promotion",
                        "Delegation would silently promote trust beyond "
                        "the source ceiling.",
                        FailureScope.AUTHORITY,
                        {"source": authority.name, "target": target.name},
                        trust_boundary="delegation",
                    )
                )


# ---------------------------------------------------------------------------
# 1. AuthorityDomain
# ---------------------------------------------------------------------------


class AuthorityDomain(Enum):
    """Enumeration of the judgment domains recognized by JuGeo.

    Every judgment that the system can produce belongs to exactly one
    domain.  The domain determines which subsystem has *primary*
    jurisdiction and what evidence quality is expected.

    STRUCTURAL
        Claims about the shape and nesting of types—handled by the solver.
    ARITHMETIC
        Numeric bounds, overflow, and precision claims—handled by the solver.
    RELATIONAL
        Sub-typing, coercion, and variance relationships.
    HEAP
        Claims about allocation, lifetime, and aliasing—runtime territory.
    IDENTITY
        Object identity and reference-equality claims—runtime territory.
    RESOURCE
        Ownership, capability tokens, and linear-resource tracking.
    SEMANTIC
        Higher-level meaning claims—copilot-eligible with ceiling.
    BEHAVIORAL
        Runtime-behavior predictions—copilot-eligible with ceiling.
    DOCUMENTATION
        Human-readable annotations and doc-comment integrity.
    ORCHESTRATION
        Cross-subsystem coordination and scheduling claims.
    """

    STRUCTURAL = "structural"
    ARITHMETIC = "arithmetic"
    RELATIONAL = "relational"
    HEAP = "heap"
    IDENTITY = "identity"
    RESOURCE = "resource"
    SEMANTIC = "semantic"
    BEHAVIORAL = "behavioral"
    DOCUMENTATION = "documentation"
    ORCHESTRATION = "orchestration"


# ---------------------------------------------------------------------------
# 2. AuthorityCeiling
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorityCeiling:
    """Maximum trust parameters for a single authority domain.

    A ceiling constrains how much trust the system places in judgments
    made by a particular grantee within a given domain.  Copilot-eligible
    domains always carry a ceiling that is strictly below the solver's
    ceiling for the same domain, ensuring that AI-generated evidence
    never silently supersedes formally verified evidence.

    Parameters
    ----------
    domain:
        The :class:`AuthorityDomain` this ceiling applies to.
    max_trust_level:
        Numeric trust cap (higher is more trusted; 0–100 scale).
    requires_witness:
        ``True`` when every judgment in this domain must be accompanied
        by at least one independent witness judgment.
    requires_corroboration:
        ``True`` when the judgment must be independently reproduced by a
        second subsystem before it is accepted.
    copilot_eligible:
        ``True`` when a copilot agent is permitted to produce judgments
        in this domain (subject to the ceiling).
    human_override_allowed:
        ``True`` when a human operator may override the ceiling.
    """

    domain: AuthorityDomain
    max_trust_level: int
    requires_witness: bool = False
    requires_corroboration: bool = False
    copilot_eligible: bool = False
    human_override_allowed: bool = True

    # -- helpers --

    def allows_trust(self, requested_level: int) -> bool:
        """Return ``True`` if *requested_level* is within this ceiling."""
        return 0 <= requested_level <= self.max_trust_level

    def effective_trust(self, raw_trust: int) -> int:
        """Clamp *raw_trust* to the ceiling, returning the effective value."""
        return max(0, min(raw_trust, self.max_trust_level))

    def is_stricter_than(self, other: AuthorityCeiling) -> bool:
        """Return ``True`` if *self* imposes a stricter ceiling than *other*."""
        if self.max_trust_level < other.max_trust_level:
            return True
        if self.requires_witness and not other.requires_witness:
            return True
        if self.requires_corroboration and not other.requires_corroboration:
            return True
        return False

    def merge_with(self, other: AuthorityCeiling) -> AuthorityCeiling:
        """Create a new ceiling that is the intersection (stricter) of both.

        The resulting ceiling takes the lower trust level, the union of
        witness/corroboration requirements, and the intersection of
        eligibility flags.
        """
        if self.domain != other.domain:
            raise ValueError(
                f"Cannot merge ceilings for different domains: "
                f"{self.domain!r} vs {other.domain!r}"
            )
        return AuthorityCeiling(
            domain=self.domain,
            max_trust_level=min(self.max_trust_level, other.max_trust_level),
            requires_witness=self.requires_witness or other.requires_witness,
            requires_corroboration=(
                self.requires_corroboration or other.requires_corroboration
            ),
            copilot_eligible=(
                self.copilot_eligible and other.copilot_eligible
            ),
            human_override_allowed=(
                self.human_override_allowed and other.human_override_allowed
            ),
        )

    def describe(self) -> str:
        """Return a human-readable summary of this ceiling."""
        parts = [
            f"domain={self.domain.value}",
            f"max_trust={self.max_trust_level}",
        ]
        if self.requires_witness:
            parts.append("witness_required")
        if self.requires_corroboration:
            parts.append("corroboration_required")
        if self.copilot_eligible:
            parts.append("copilot_eligible")
        if self.human_override_allowed:
            parts.append("human_override")
        return f"Ceiling({', '.join(parts)})"


# ---------------------------------------------------------------------------
# 3. AuthorityGrant
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AuthorityGrant:
    """A grant of authority from one subsystem to another.

    Grants are the primary mechanism by which the system records that a
    particular grantee is allowed to produce judgments in a set of
    domains, subject to ceilings.

    Parameters
    ----------
    grantee:
        The subsystem name that receives the authority.
    domains:
        Set of :class:`AuthorityDomain` values the grantee may operate in.
    ceilings:
        Mapping from each domain to its :class:`AuthorityCeiling`.
    granted_at:
        UTC timestamp when the grant was created.
    granted_by:
        Name of the subsystem or operator that issued the grant.
    expiry:
        Optional UTC timestamp after which the grant is no longer valid.
    revocable:
        ``True`` when the grant may be revoked before its expiry.
    grant_id:
        Unique identifier for this grant (auto-generated if omitted).
    metadata:
        Free-form metadata attached to the grant.
    """

    grantee: str
    domains: Set[AuthorityDomain]
    ceilings: Dict[AuthorityDomain, AuthorityCeiling]
    granted_at: datetime = field(default_factory=_utcnow)
    granted_by: str = "system"
    expiry: Optional[datetime] = None
    revocable: bool = True
    grant_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: Dict[str, Any] = field(default_factory=dict)

    # -- query helpers --

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Return ``True`` if the grant has expired."""
        if self.expiry is None:
            return False
        reference = now or _utcnow()
        return reference >= self.expiry

    def covers_domain(self, domain: AuthorityDomain) -> bool:
        """Return ``True`` if the grant covers *domain*."""
        return domain in self.domains

    def ceiling_for(self, domain: AuthorityDomain) -> Optional[AuthorityCeiling]:
        """Return the ceiling for *domain*, or ``None`` if not covered."""
        return self.ceilings.get(domain)

    def effective_trust_for(self, domain: AuthorityDomain, raw: int) -> int:
        """Return the effective trust level for *domain* given *raw* trust."""
        ceil = self.ceiling_for(domain)
        if ceil is None:
            return 0
        return ceil.effective_trust(raw)

    def remaining_lifetime(self, now: Optional[datetime] = None) -> Optional[timedelta]:
        """Return the remaining lifetime of the grant, or ``None`` if perpetual."""
        if self.expiry is None:
            return None
        reference = now or _utcnow()
        remaining = self.expiry - reference
        return remaining if remaining.total_seconds() > 0 else timedelta(0)

    def summary(self) -> str:
        """Return a compact human-readable summary."""
        domain_names = sorted(d.value for d in self.domains)
        status = "expired" if self.is_expired() else "active"
        return (
            f"Grant({self.grant_id}: {self.grantee} "
            f"[{', '.join(domain_names)}] "
            f"by={self.granted_by} {status})"
        )

    def narrow_to(self, allowed_domains: Set[AuthorityDomain]) -> AuthorityGrant:
        """Return a copy of this grant narrowed to *allowed_domains*.

        Domains not in *allowed_domains* are removed together with their
        ceilings.  This is used during delegation to restrict a grant to
        the delegator's own jurisdiction.
        """
        restricted = self.domains & allowed_domains
        restricted_ceilings = {
            d: c for d, c in self.ceilings.items() if d in restricted
        }
        return AuthorityGrant(
            grantee=self.grantee,
            domains=restricted,
            ceilings=restricted_ceilings,
            granted_at=self.granted_at,
            granted_by=self.granted_by,
            expiry=self.expiry,
            revocable=self.revocable,
            grant_id=self.grant_id + "-narrowed",
            metadata={**self.metadata, "narrowed_from": self.grant_id},
        )


# ---------------------------------------------------------------------------
# 4. AuthorityRegistry
# ---------------------------------------------------------------------------


class AuthorityRegistry:
    """Central registry of all authority grants.

    The registry is the single source of truth for "who is allowed to do
    what".  It supports granting, revoking, querying, validation, overlap
    detection, and merging of grants.

    The registry deliberately does **not** enforce grants—that is the job
    of :class:`AuthorityEnforcer`.  Separating the *registry* from the
    *enforcer* mirrors the separation of *policy* from *mechanism* in
    operating-system design.
    """

    def __init__(self) -> None:
        self._grants: Dict[str, List[AuthorityGrant]] = defaultdict(list)
        self._revoked_ids: Set[str] = set()
        self._audit: AuthorityAuditLog = AuthorityAuditLog()

    # -- mutators --

    def grant(self, grant: AuthorityGrant) -> None:
        """Register a new authority grant.

        Raises :class:`JuGeoError` if a grant with the same id already
        exists and has not been revoked.
        """
        for existing in self._grants[grant.grantee]:
            if (
                existing.grant_id == grant.grant_id
                and existing.grant_id not in self._revoked_ids
            ):
                raise JuGeoError(
                    StructuredFailure(
                        "duplicate-grant",
                        f"Grant {grant.grant_id!r} already exists for "
                        f"{grant.grantee!r}.",
                        FailureScope.AUTHORITY,
                        {"grant_id": grant.grant_id, "grantee": grant.grantee},
                    )
                )
        self._grants[grant.grantee].append(grant)
        self._audit.record_grant(grant)
        logger.info("Authority granted: %s", grant.summary())

    def revoke(self, grant_id: str) -> bool:
        """Revoke a grant by its identifier.

        Returns ``True`` if the grant was found and revoked, ``False``
        otherwise.  Non-revocable grants raise :class:`JuGeoError`.
        """
        for grantee, grants in self._grants.items():
            for g in grants:
                if g.grant_id == grant_id:
                    if not g.revocable:
                        raise JuGeoError(
                            StructuredFailure(
                                "irrevocable-grant",
                                f"Grant {grant_id!r} for {grantee!r} is "
                                f"not revocable.",
                                FailureScope.AUTHORITY,
                                {"grant_id": grant_id, "grantee": grantee},
                            )
                        )
                    self._revoked_ids.add(grant_id)
                    self._audit.record_revocation(grant_id, grantee)
                    logger.info(
                        "Authority revoked: grant=%s grantee=%s",
                        grant_id,
                        grantee,
                    )
                    return True
        return False

    # -- queries --

    def check_authority(
        self,
        grantee: str,
        domain: AuthorityDomain,
        required_trust: int = 0,
        now: Optional[datetime] = None,
    ) -> bool:
        """Return ``True`` if *grantee* has active authority in *domain*.

        A grant is considered active when it:

        1. covers the requested domain,
        2. has not been revoked,
        3. has not expired, and
        4. its ceiling allows the requested trust level.
        """
        for g in self._active_grants(grantee, now):
            if g.covers_domain(domain):
                ceiling = g.ceiling_for(domain)
                if ceiling is not None and ceiling.allows_trust(required_trust):
                    self._audit.record_check(grantee, domain, True)
                    return True
        self._audit.record_check(grantee, domain, False)
        return False

    def get_grants_for(
        self, grantee: str, now: Optional[datetime] = None
    ) -> List[AuthorityGrant]:
        """Return all active grants for *grantee*."""
        return list(self._active_grants(grantee, now))

    def get_grantees_for_domain(
        self, domain: AuthorityDomain, now: Optional[datetime] = None
    ) -> List[str]:
        """Return all grantees that currently have authority in *domain*."""
        result: List[str] = []
        for grantee, grants in self._grants.items():
            for g in grants:
                if (
                    g.grant_id not in self._revoked_ids
                    and not g.is_expired(now)
                    and g.covers_domain(domain)
                ):
                    if grantee not in result:
                        result.append(grantee)
        return result

    def validate_all_grants(self, now: Optional[datetime] = None) -> List[str]:
        """Validate every registered grant and return a list of warnings.

        Checks for expired grants, revoked grants still referenced,
        ceilings that do not match the declared domains, and grants with
        no domains.
        """
        warnings: List[str] = []
        for grantee, grants in self._grants.items():
            for g in grants:
                if g.is_expired(now):
                    warnings.append(
                        f"Grant {g.grant_id} for {grantee} has expired."
                    )
                if g.grant_id in self._revoked_ids:
                    warnings.append(
                        f"Grant {g.grant_id} for {grantee} has been revoked."
                    )
                if not g.domains:
                    warnings.append(
                        f"Grant {g.grant_id} for {grantee} has no domains."
                    )
                for d in g.domains:
                    if d not in g.ceilings:
                        warnings.append(
                            f"Grant {g.grant_id} for {grantee} covers "
                            f"{d.value} but has no ceiling for it."
                        )
                for d in g.ceilings:
                    if d not in g.domains:
                        warnings.append(
                            f"Grant {g.grant_id} for {grantee} has a "
                            f"ceiling for {d.value} which is not in its "
                            f"domain set."
                        )
        return warnings

    def detect_jurisdiction_overlaps(
        self, now: Optional[datetime] = None
    ) -> Dict[AuthorityDomain, List[str]]:
        """Detect domains where multiple grantees hold overlapping authority.

        Returns a mapping from domain to the list of overlapping grantees.
        Overlaps are not necessarily errors—they may be intentional for
        redundancy—but they should be reviewed to avoid conflicting
        judgments.
        """
        domain_holders: Dict[AuthorityDomain, List[str]] = defaultdict(list)
        for grantee in self._grants:
            for g in self._active_grants(grantee, now):
                for d in g.domains:
                    if grantee not in domain_holders[d]:
                        domain_holders[d].append(grantee)
        return {
            d: holders
            for d, holders in domain_holders.items()
            if len(holders) > 1
        }

    def merge_grants(
        self, grantee: str, now: Optional[datetime] = None
    ) -> Optional[AuthorityGrant]:
        """Merge all active grants for *grantee* into a single synthetic grant.

        The merged grant contains the union of domains and, for each
        domain, the *stricter* of any overlapping ceilings.  This is
        useful for quick "what can this grantee do?" queries.
        """
        active = list(self._active_grants(grantee, now))
        if not active:
            return None
        merged_domains: Set[AuthorityDomain] = set()
        merged_ceilings: Dict[AuthorityDomain, AuthorityCeiling] = {}
        earliest_grant = min(g.granted_at for g in active)
        latest_expiry: Optional[datetime] = None
        any_perpetual = False

        for g in active:
            merged_domains |= g.domains
            for d, c in g.ceilings.items():
                if d in merged_ceilings:
                    merged_ceilings[d] = merged_ceilings[d].merge_with(c)
                else:
                    merged_ceilings[d] = c
            if g.expiry is None:
                any_perpetual = True
            elif latest_expiry is None or g.expiry > latest_expiry:
                latest_expiry = g.expiry

        return AuthorityGrant(
            grantee=grantee,
            domains=merged_domains,
            ceilings=merged_ceilings,
            granted_at=earliest_grant,
            granted_by="merged",
            expiry=None if any_perpetual else latest_expiry,
            revocable=False,
            grant_id=f"merged-{grantee}",
            metadata={"source_grants": [g.grant_id for g in active]},
        )

    @property
    def audit_log(self) -> AuthorityAuditLog:
        """Return the audit log attached to this registry."""
        return self._audit

    # -- internals --

    def _active_grants(
        self, grantee: str, now: Optional[datetime] = None
    ) -> Iterable[AuthorityGrant]:
        """Yield grants for *grantee* that are neither revoked nor expired."""
        for g in self._grants.get(grantee, []):
            if g.grant_id not in self._revoked_ids and not g.is_expired(now):
                yield g


# ---------------------------------------------------------------------------
# 5. AuthorityViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorityViolation:
    """Record of a single authority violation.

    An authority violation is created whenever a subsystem attempts to
    produce a judgment outside its jurisdiction.  The violation record
    carries enough context for both automated remediation and human
    audit.

    Parameters
    ----------
    violator:
        Name of the subsystem that attempted the unauthorized judgment.
    attempted_domain:
        The :class:`AuthorityDomain` the violator tried to operate in.
    required_ceiling:
        The ceiling that would have been needed.
    actual_trust:
        The trust level the violator actually held.
    timestamp:
        UTC time of the violation.
    context_coordinate:
        The semantic coordinate (e.g. a sheaf section path) where the
        violation occurred.
    remediation_hints:
        Suggestions for how to resolve the violation.
    violation_id:
        Unique identifier for this violation record.
    metadata:
        Additional context (e.g. stack frame, request id).
    """

    violator: str
    attempted_domain: AuthorityDomain
    required_ceiling: int
    actual_trust: int
    timestamp: datetime = field(default_factory=_utcnow)
    context_coordinate: str = ""
    remediation_hints: Tuple[str, ...] = ()
    violation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def severity(self) -> str:
        """Classify the violation severity.

        ``critical`` — the gap between actual and required trust is large
        and the domain is safety-relevant (HEAP, IDENTITY, RESOURCE).
        ``high`` — a significant trust gap in any domain.
        ``medium`` — a small trust gap or a benign domain.
        ``low`` — informational (e.g. documentation domain).
        """
        gap = self.required_ceiling - self.actual_trust
        safety_domains = {
            AuthorityDomain.HEAP,
            AuthorityDomain.IDENTITY,
            AuthorityDomain.RESOURCE,
        }
        if gap > 50 and self.attempted_domain in safety_domains:
            return "critical"
        if gap > 30:
            return "high"
        if gap > 10:
            return "medium"
        return "low"

    def describe(self) -> str:
        """Return a human-readable description of the violation."""
        return (
            f"[{self.severity().upper()}] {self.violator} attempted "
            f"{self.attempted_domain.value} judgment "
            f"(required={self.required_ceiling}, "
            f"actual={self.actual_trust}) "
            f"at coordinate {self.context_coordinate!r}"
        )

    def as_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON encoding."""
        return {
            "violation_id": self.violation_id,
            "violator": self.violator,
            "domain": self.attempted_domain.value,
            "required_ceiling": self.required_ceiling,
            "actual_trust": self.actual_trust,
            "severity": self.severity(),
            "timestamp": self.timestamp.isoformat(),
            "coordinate": self.context_coordinate,
            "hints": list(self.remediation_hints),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# 6. AuthorityEnforcer
# ---------------------------------------------------------------------------


class AuthorityEnforcer:
    """Enforcement layer that wraps evidence-producing operations.

    The enforcer sits between a caller and the operation it wants to
    perform.  Before the operation is invoked, the enforcer checks that
    the caller has sufficient authority in the relevant domain.  If not,
    a violation is created and an exception is raised.

    This class is intentionally stateless with respect to the operations
    it wraps—it delegates all state to the :class:`AuthorityRegistry`
    and :class:`AuthorityAuditLog`.

    A copilot agent, for example, would have its evidence-producing
    calls routed through an enforcer configured with a registry that
    only grants it SEMANTIC and BEHAVIORAL authority with a trust
    ceiling—preventing it from silently issuing HEAP judgments.
    """

    def __init__(
        self,
        registry: AuthorityRegistry,
        *,
        strict: bool = True,
        violation_callback: Optional[
            Callable[[AuthorityViolation], None]
        ] = None,
    ) -> None:
        self._registry = registry
        self._strict = strict
        self._violation_callback = violation_callback
        self._violations: List[AuthorityViolation] = []

    # -- enforcement --

    def enforce(
        self,
        caller: str,
        domain: AuthorityDomain,
        required_trust: int = 0,
        *,
        context_coordinate: str = "",
    ) -> None:
        """Assert that *caller* has authority; raise on violation.

        In strict mode (the default), an :class:`JuGeoError` is raised
        when the check fails.  In permissive mode, the violation is
        recorded but execution continues.
        """
        if self._registry.check_authority(caller, domain, required_trust):
            logger.debug(
                "Authority check passed: caller=%s domain=%s trust=%d",
                caller,
                domain.value,
                required_trust,
            )
            return

        violation = self._build_violation(
            caller, domain, required_trust, context_coordinate
        )
        self._violations.append(violation)
        self._registry.audit_log.record_violation(violation)

        if self._violation_callback is not None:
            self._violation_callback(violation)

        logger.warning("Authority violation: %s", violation.describe())

        if self._strict:
            raise JuGeoError(
                StructuredFailure(
                    "authority-violation",
                    violation.describe(),
                    FailureScope.AUTHORITY,
                    violation.as_dict(),
                )
            )

    def check_and_log(
        self,
        caller: str,
        domain: AuthorityDomain,
        required_trust: int = 0,
        *,
        context_coordinate: str = "",
    ) -> bool:
        """Non-throwing variant: return ``True`` if authorized, else log.

        This is useful in advisory contexts (e.g. copilot suggestions)
        where a violation should be surfaced but should not abort the
        operation.
        """
        authorized = self._registry.check_authority(
            caller, domain, required_trust
        )
        if not authorized:
            violation = self._build_violation(
                caller, domain, required_trust, context_coordinate
            )
            self._violations.append(violation)
            self._registry.audit_log.record_violation(violation)
            logger.info("Authority check failed (advisory): %s", violation.describe())
        return authorized

    def create_violation_report(self) -> List[Dict[str, Any]]:
        """Return a serializable report of all recorded violations."""
        return [v.as_dict() for v in self._violations]

    def suggest_remediation(self, violation: AuthorityViolation) -> List[str]:
        """Suggest concrete steps to resolve a given violation.

        The suggestions depend on the domain and on the gap between the
        required and actual trust levels.
        """
        hints: List[str] = list(violation.remediation_hints)
        gap = violation.required_ceiling - violation.actual_trust

        if violation.attempted_domain in {
            AuthorityDomain.SEMANTIC,
            AuthorityDomain.BEHAVIORAL,
        }:
            hints.append(
                "Consider requesting copilot corroboration or raising "
                "the ceiling for this copilot agent."
            )

        if gap > 50:
            hints.append(
                "The trust gap is large.  The judgment should be "
                "delegated to a subsystem with verified authority."
            )
        elif gap > 20:
            hints.append(
                "Request a human review to bridge the trust gap."
            )
        else:
            hints.append(
                "A witness judgment from a second subsystem may "
                "be sufficient to meet the required ceiling."
            )

        if violation.attempted_domain in {
            AuthorityDomain.HEAP,
            AuthorityDomain.IDENTITY,
        }:
            hints.append(
                "HEAP and IDENTITY claims are runtime-only.  "
                "Redirect this judgment to the runtime subsystem."
            )

        if violation.attempted_domain in {
            AuthorityDomain.STRUCTURAL,
            AuthorityDomain.ARITHMETIC,
        }:
            hints.append(
                "STRUCTURAL and ARITHMETIC claims are solver-only.  "
                "Redirect this judgment to the solver subsystem."
            )

        return hints

    def clear_violations(self) -> int:
        """Clear recorded violations and return the count cleared."""
        count = len(self._violations)
        self._violations.clear()
        return count

    @property
    def violation_count(self) -> int:
        """Return the number of violations recorded since last clear."""
        return len(self._violations)

    @property
    def violations(self) -> Sequence[AuthorityViolation]:
        """Return an immutable view of recorded violations."""
        return tuple(self._violations)

    # -- internals --

    def _build_violation(
        self,
        caller: str,
        domain: AuthorityDomain,
        required_trust: int,
        context_coordinate: str,
    ) -> AuthorityViolation:
        """Construct a violation record with computed actual trust."""
        merged = self._registry.merge_grants(caller)
        actual = 0
        if merged is not None:
            ceiling = merged.ceiling_for(domain)
            if ceiling is not None:
                actual = ceiling.max_trust_level
        return AuthorityViolation(
            violator=caller,
            attempted_domain=domain,
            required_ceiling=required_trust,
            actual_trust=actual,
            context_coordinate=context_coordinate,
            remediation_hints=(),
        )


# ---------------------------------------------------------------------------
# 7. JurisdictionMap
# ---------------------------------------------------------------------------


class JurisdictionMap:
    """Maps coordinate prefixes to authority domains.

    JuGeo uses hierarchical semantic coordinates (e.g.
    ``pkg.module.Class.method``) to locate judgments in the sheaf.  The
    jurisdiction map records which authority domain governs each prefix.
    A coordinate inherits its parent's jurisdiction unless an explicit
    override is registered for a more specific prefix.

    Example::

        jmap = JurisdictionMap()
        jmap.assign("runtime.heap", AuthorityDomain.HEAP)
        jmap.assign("runtime.heap.gc", AuthorityDomain.RESOURCE)
        jmap.lookup("runtime.heap.gc.sweep")
        # => AuthorityDomain.RESOURCE  (most-specific match)
    """

    def __init__(self, separator: str = ".") -> None:
        self._sep = separator
        self._map: Dict[str, AuthorityDomain] = {}
        self._default: Optional[AuthorityDomain] = None

    def assign(self, prefix: str, domain: AuthorityDomain) -> None:
        """Assign *domain* as the authority for all coordinates under *prefix*."""
        self._map[prefix] = domain
        logger.debug("Jurisdiction assigned: %s -> %s", prefix, domain.value)

    def remove(self, prefix: str) -> bool:
        """Remove the jurisdiction entry for *prefix*.

        Returns ``True`` if the prefix was found and removed.
        """
        if prefix in self._map:
            del self._map[prefix]
            return True
        return False

    def set_default(self, domain: AuthorityDomain) -> None:
        """Set the fallback domain for coordinates that match no prefix."""
        self._default = domain

    def lookup(self, coordinate: str) -> Optional[AuthorityDomain]:
        """Find the most-specific jurisdiction for *coordinate*.

        Walks from the full coordinate up to its root, returning the
        first matching domain.  Falls back to the default domain if no
        prefix matches.
        """
        parts = coordinate.split(self._sep)
        for length in range(len(parts), 0, -1):
            candidate = self._sep.join(parts[:length])
            if candidate in self._map:
                return self._map[candidate]
        return self._default

    def all_prefixes(self) -> List[str]:
        """Return all registered prefixes sorted lexicographically."""
        return sorted(self._map.keys())

    def domains_for_subtree(self, root: str) -> Set[AuthorityDomain]:
        """Return all domains assigned under *root* (inclusive)."""
        result: Set[AuthorityDomain] = set()
        for prefix, domain in self._map.items():
            if prefix == root or prefix.startswith(root + self._sep):
                result.add(domain)
        return result

    def prefixes_for_domain(self, domain: AuthorityDomain) -> List[str]:
        """Return all prefixes assigned to *domain*."""
        return sorted(p for p, d in self._map.items() if d == domain)

    def depth_of(self, prefix: str) -> int:
        """Return the depth (number of segments) of *prefix*."""
        return len(prefix.split(self._sep))

    def conflicts(self) -> List[Tuple[str, str, AuthorityDomain, AuthorityDomain]]:
        """Detect pairs of prefixes where a child overrides its parent.

        This is informational—overrides are legal but worth reviewing.
        Returns tuples of (parent_prefix, child_prefix, parent_domain,
        child_domain).
        """
        result: List[Tuple[str, str, AuthorityDomain, AuthorityDomain]] = []
        prefixes = sorted(self._map.keys())
        for i, child in enumerate(prefixes):
            for parent in prefixes[:i]:
                if child.startswith(parent + self._sep):
                    if self._map[parent] != self._map[child]:
                        result.append(
                            (parent, child, self._map[parent], self._map[child])
                        )
        return result

    def describe(self) -> str:
        """Return a human-readable summary of the jurisdiction map."""
        lines = [f"JurisdictionMap (default={self._default})"]
        for prefix in sorted(self._map.keys()):
            indent = "  " * self.depth_of(prefix)
            lines.append(f"  {indent}{prefix} -> {self._map[prefix].value}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. AuthorityDelegation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuthorityDelegation:
    """Represents one subsystem delegating a subset of its authority.

    Delegation enables a chain of trust: the solver may delegate a
    narrow slice of its STRUCTURAL authority to a copilot, for example,
    so that the copilot can propose structural judgments up to a reduced
    ceiling.  Each hop in the chain attenuates trust by a configurable
    factor.

    Parameters
    ----------
    delegator:
        The subsystem that is delegating.
    delegate:
        The subsystem that receives the delegation.
    domains:
        Subset of the delegator's domains being delegated.
    attenuation_factor:
        Multiplicative factor (0.0–1.0) applied to the ceiling at each
        delegation hop.  A factor of 0.8 means the delegate's ceiling is
        80% of the delegator's ceiling.
    max_chain_depth:
        Maximum number of successive delegations before the chain is
        considered invalid.
    requires_review:
        If ``True``, the delegation is only effective after an explicit
        review step.
    created_at:
        UTC timestamp of creation.
    delegation_id:
        Unique identifier.
    """

    delegator: str
    delegate: str
    domains: Set[AuthorityDomain]
    attenuation_factor: float = 0.8
    max_chain_depth: int = 3
    requires_review: bool = False
    created_at: datetime = field(default_factory=_utcnow)
    delegation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # -- helpers --

    def attenuated_ceiling(self, original_ceiling: int, hops: int = 1) -> int:
        """Compute the effective ceiling after *hops* delegation steps.

        Each hop multiplies the ceiling by :attr:`attenuation_factor`.
        """
        effective = float(original_ceiling)
        for _ in range(hops):
            effective *= self.attenuation_factor
        return int(effective)

    def is_chain_valid(self, current_depth: int) -> bool:
        """Return ``True`` if the chain has not exceeded its maximum depth."""
        return current_depth <= self.max_chain_depth

    def derive_grant(
        self,
        source_grant: AuthorityGrant,
        hops: int = 1,
    ) -> AuthorityGrant:
        """Create a derived :class:`AuthorityGrant` for the delegate.

        The derived grant inherits only the domains that overlap between
        the delegation and the source grant, and each ceiling is
        attenuated.
        """
        overlap = self.domains & source_grant.domains
        derived_ceilings: Dict[AuthorityDomain, AuthorityCeiling] = {}
        for d in overlap:
            original = source_grant.ceiling_for(d)
            if original is not None:
                derived_ceilings[d] = AuthorityCeiling(
                    domain=d,
                    max_trust_level=self.attenuated_ceiling(
                        original.max_trust_level, hops
                    ),
                    requires_witness=original.requires_witness,
                    requires_corroboration=True,
                    copilot_eligible=original.copilot_eligible,
                    human_override_allowed=original.human_override_allowed,
                )
        return AuthorityGrant(
            grantee=self.delegate,
            domains=overlap,
            ceilings=derived_ceilings,
            granted_by=self.delegator,
            expiry=source_grant.expiry,
            revocable=True,
            metadata={
                "delegation_id": self.delegation_id,
                "hops": hops,
                "attenuation": self.attenuation_factor,
            },
        )

    def validate(self, registry: AuthorityRegistry) -> List[str]:
        """Validate this delegation against the registry.

        Returns a list of warnings/errors (empty means valid).
        """
        issues: List[str] = []
        delegator_grants = registry.get_grants_for(self.delegator)
        if not delegator_grants:
            issues.append(
                f"Delegator {self.delegator!r} has no active grants."
            )
            return issues

        delegator_domains: Set[AuthorityDomain] = set()
        for g in delegator_grants:
            delegator_domains |= g.domains

        excess = self.domains - delegator_domains
        if excess:
            issues.append(
                f"Delegation includes domains not held by the delegator: "
                f"{[d.value for d in excess]}"
            )

        if self.attenuation_factor <= 0.0 or self.attenuation_factor > 1.0:
            issues.append(
                f"Attenuation factor {self.attenuation_factor} is out of "
                f"the valid range (0.0, 1.0]."
            )

        if self.max_chain_depth < 1:
            issues.append(
                f"Max chain depth {self.max_chain_depth} must be >= 1."
            )

        return issues

    def summary(self) -> str:
        """Return a compact human-readable summary."""
        domain_names = sorted(d.value for d in self.domains)
        return (
            f"Delegation({self.delegation_id}: "
            f"{self.delegator} -> {self.delegate} "
            f"[{', '.join(domain_names)}] "
            f"attenuation={self.attenuation_factor})"
        )


class DelegationChain:
    """Tracks and validates a chain of delegations from origin to leaf.

    In JuGeo's authority model a copilot might receive authority through
    a chain: solver → orchestrator → copilot.  Each hop attenuates trust.
    This class makes the chain explicit and validates it end-to-end.
    """

    def __init__(self) -> None:
        self._links: List[AuthorityDelegation] = []

    def add_link(self, delegation: AuthorityDelegation) -> None:
        """Append a delegation link to the chain.

        Raises ``ValueError`` if the link does not connect to the
        current tail of the chain.
        """
        if self._links:
            tail = self._links[-1]
            if delegation.delegator != tail.delegate:
                raise ValueError(
                    f"Link {delegation.delegation_id} does not connect: "
                    f"expected delegator={tail.delegate!r}, "
                    f"got {delegation.delegator!r}"
                )
        self._links.append(delegation)

    @property
    def depth(self) -> int:
        """Return the number of hops in the chain."""
        return len(self._links)

    @property
    def origin(self) -> Optional[str]:
        """Return the subsystem at the root of the chain."""
        return self._links[0].delegator if self._links else None

    @property
    def leaf(self) -> Optional[str]:
        """Return the subsystem at the end of the chain."""
        return self._links[-1].delegate if self._links else None

    def is_valid(self) -> bool:
        """Return ``True`` if every link respects its max chain depth."""
        for i, link in enumerate(self._links):
            if not link.is_chain_valid(i + 1):
                return False
        return True

    def effective_ceiling(self, base_ceiling: int) -> int:
        """Compute the effective ceiling after all hops."""
        ceiling = float(base_ceiling)
        for link in self._links:
            ceiling *= link.attenuation_factor
        return int(ceiling)

    def overlapping_domains(self) -> Set[AuthorityDomain]:
        """Return domains that survive through every link of the chain."""
        if not self._links:
            return set()
        result = set(self._links[0].domains)
        for link in self._links[1:]:
            result &= link.domains
        return result

    def describe(self) -> str:
        """Return a human-readable description of the chain."""
        if not self._links:
            return "DelegationChain(empty)"
        path = " -> ".join(
            [self._links[0].delegator]
            + [link.delegate for link in self._links]
        )
        return (
            f"DelegationChain({path}, depth={self.depth}, "
            f"valid={self.is_valid()})"
        )


# ---------------------------------------------------------------------------
# 9. AuthorityAuditLog
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single entry in the authority audit log.

    Parameters
    ----------
    event_type:
        One of ``grant``, ``revocation``, ``check``, ``violation``,
        ``delegation``.
    timestamp:
        UTC time of the event.
    actor:
        The subsystem or operator that triggered the event.
    details:
        Free-form details about the event.
    entry_id:
        Unique identifier.
    """

    event_type: str
    timestamp: datetime
    actor: str
    details: Dict[str, Any]
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class AuthorityAuditLog:
    """Append-only log of all authority-related events.

    The audit log is the system's memory of every grant, revocation,
    check, and violation that has occurred.  It is designed to be
    tamper-evident: entries are never modified or deleted (within a
    single session), and each entry carries a unique id and timestamp.

    The log is used by :class:`AuthorityRegistry` and
    :class:`AuthorityEnforcer` to record events automatically.  External
    consumers (diagnostics, the copilot UI) can query it for reporting.
    """

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []

    def record_grant(self, grant: AuthorityGrant) -> AuditEntry:
        """Record that a grant was issued."""
        entry = AuditEntry(
            event_type="grant",
            timestamp=_utcnow(),
            actor=grant.granted_by,
            details={
                "grant_id": grant.grant_id,
                "grantee": grant.grantee,
                "domains": [d.value for d in grant.domains],
            },
        )
        self._entries.append(entry)
        return entry

    def record_revocation(self, grant_id: str, grantee: str) -> AuditEntry:
        """Record that a grant was revoked."""
        entry = AuditEntry(
            event_type="revocation",
            timestamp=_utcnow(),
            actor="system",
            details={"grant_id": grant_id, "grantee": grantee},
        )
        self._entries.append(entry)
        return entry

    def record_check(
        self,
        grantee: str,
        domain: AuthorityDomain,
        result: bool,
    ) -> AuditEntry:
        """Record that an authority check was performed."""
        entry = AuditEntry(
            event_type="check",
            timestamp=_utcnow(),
            actor=grantee,
            details={
                "domain": domain.value,
                "result": "allowed" if result else "denied",
            },
        )
        self._entries.append(entry)
        return entry

    def record_violation(self, violation: AuthorityViolation) -> AuditEntry:
        """Record an authority violation."""
        entry = AuditEntry(
            event_type="violation",
            timestamp=_utcnow(),
            actor=violation.violator,
            details=violation.as_dict(),
        )
        self._entries.append(entry)
        return entry

    def record_delegation(
        self, delegation: AuthorityDelegation
    ) -> AuditEntry:
        """Record that a delegation was created."""
        entry = AuditEntry(
            event_type="delegation",
            timestamp=_utcnow(),
            actor=delegation.delegator,
            details={
                "delegation_id": delegation.delegation_id,
                "delegate": delegation.delegate,
                "domains": [d.value for d in delegation.domains],
                "attenuation": delegation.attenuation_factor,
            },
        )
        self._entries.append(entry)
        return entry

    # -- queries --

    @property
    def entries(self) -> Sequence[AuditEntry]:
        """Return an immutable view of all log entries."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def filter_by_type(self, event_type: str) -> List[AuditEntry]:
        """Return entries matching *event_type*."""
        return [e for e in self._entries if e.event_type == event_type]

    def filter_by_actor(self, actor: str) -> List[AuditEntry]:
        """Return entries involving *actor*."""
        return [e for e in self._entries if e.actor == actor]

    def filter_by_time_range(
        self,
        start: datetime,
        end: Optional[datetime] = None,
    ) -> List[AuditEntry]:
        """Return entries whose timestamp falls within [start, end)."""
        effective_end = end or _utcnow()
        return [
            e
            for e in self._entries
            if start <= e.timestamp < effective_end
        ]

    def violations_only(self) -> List[AuditEntry]:
        """Convenience: return only violation entries."""
        return self.filter_by_type("violation")

    def grants_only(self) -> List[AuditEntry]:
        """Convenience: return only grant entries."""
        return self.filter_by_type("grant")

    def summary_counts(self) -> Dict[str, int]:
        """Return a count of entries by event type."""
        counts: Dict[str, int] = defaultdict(int)
        for e in self._entries:
            counts[e.event_type] += 1
        return dict(counts)

    def last_n(self, n: int) -> List[AuditEntry]:
        """Return the most recent *n* entries."""
        return list(self._entries[-n:])

    def describe(self) -> str:
        """Return a human-readable summary of the audit log."""
        counts = self.summary_counts()
        parts = [f"{k}={v}" for k, v in sorted(counts.items())]
        return f"AuditLog(total={len(self)}, {', '.join(parts)})"


# ---------------------------------------------------------------------------
# 10. DefaultAuthorityPolicy
# ---------------------------------------------------------------------------


class DefaultAuthorityPolicy:
    """Factory that creates the standard JuGeo authority layout.

    The default policy encodes the jurisdiction boundaries described in
    the theory:

    - **solver** receives STRUCTURAL + ARITHMETIC at full trust (100).
    - **runtime** receives HEAP + IDENTITY + RESOURCE at full trust.
    - **copilot** receives SEMANTIC + BEHAVIORAL with a ceiling (60) and
      corroboration required.
    - **human** receives all domains with override capability and the
      highest trust.
    - **orchestrator** receives ORCHESTRATION + DOCUMENTATION.

    Call :meth:`apply` to populate a fresh :class:`AuthorityRegistry`
    and :class:`JurisdictionMap` in one step.
    """

    # -- ceiling builders --

    @staticmethod
    def solver_ceiling(domain: AuthorityDomain) -> AuthorityCeiling:
        """Build the ceiling the solver uses for a given domain.

        The solver is fully trusted within its jurisdiction and does not
        require witnesses or corroboration.
        """
        return AuthorityCeiling(
            domain=domain,
            max_trust_level=100,
            requires_witness=False,
            requires_corroboration=False,
            copilot_eligible=False,
            human_override_allowed=True,
        )

    @staticmethod
    def runtime_ceiling(domain: AuthorityDomain) -> AuthorityCeiling:
        """Build the ceiling the runtime uses for a given domain.

        The runtime is fully trusted within its jurisdiction.  Witnesses
        are not required because the runtime produces direct observations
        rather than inferences.
        """
        return AuthorityCeiling(
            domain=domain,
            max_trust_level=100,
            requires_witness=False,
            requires_corroboration=False,
            copilot_eligible=False,
            human_override_allowed=True,
        )

    @staticmethod
    def copilot_ceiling(domain: AuthorityDomain) -> AuthorityCeiling:
        """Build the ceiling applied to copilot-generated judgments.

        Copilot authority is intentionally capped.  Corroboration is
        required so that a second subsystem (typically the solver or a
        human reviewer) must independently confirm the copilot's claim.
        """
        return AuthorityCeiling(
            domain=domain,
            max_trust_level=60,
            requires_witness=True,
            requires_corroboration=True,
            copilot_eligible=True,
            human_override_allowed=True,
        )

    @staticmethod
    def human_ceiling(domain: AuthorityDomain) -> AuthorityCeiling:
        """Build the ceiling for human-issued judgments.

        Humans have the highest trust and override capability.  This
        models the fact that a human operator can always override the
        system when necessary.
        """
        return AuthorityCeiling(
            domain=domain,
            max_trust_level=100,
            requires_witness=False,
            requires_corroboration=False,
            copilot_eligible=False,
            human_override_allowed=True,
        )

    @staticmethod
    def orchestrator_ceiling(domain: AuthorityDomain) -> AuthorityCeiling:
        """Build the ceiling for the orchestrator subsystem.

        The orchestrator has moderate trust—it coordinates but does not
        produce primary evidence.
        """
        return AuthorityCeiling(
            domain=domain,
            max_trust_level=80,
            requires_witness=False,
            requires_corroboration=False,
            copilot_eligible=False,
            human_override_allowed=True,
        )

    # -- grant builders --

    @classmethod
    def solver_grant(cls) -> AuthorityGrant:
        """Create the standard solver authority grant."""
        domains = {AuthorityDomain.STRUCTURAL, AuthorityDomain.ARITHMETIC}
        return AuthorityGrant(
            grantee="solver",
            domains=domains,
            ceilings={d: cls.solver_ceiling(d) for d in domains},
            granted_by="default-policy",
            revocable=False,
            grant_id="default-solver",
        )

    @classmethod
    def runtime_grant(cls) -> AuthorityGrant:
        """Create the standard runtime authority grant."""
        domains = {
            AuthorityDomain.HEAP,
            AuthorityDomain.IDENTITY,
            AuthorityDomain.RESOURCE,
        }
        return AuthorityGrant(
            grantee="runtime",
            domains=domains,
            ceilings={d: cls.runtime_ceiling(d) for d in domains},
            granted_by="default-policy",
            revocable=False,
            grant_id="default-runtime",
        )

    @classmethod
    def copilot_grant(cls) -> AuthorityGrant:
        """Create the standard copilot authority grant.

        The copilot is allowed to produce judgments in SEMANTIC and
        BEHAVIORAL domains, but every judgment is subject to a trust
        ceiling and requires corroboration.
        """
        domains = {AuthorityDomain.SEMANTIC, AuthorityDomain.BEHAVIORAL}
        return AuthorityGrant(
            grantee="copilot",
            domains=domains,
            ceilings={d: cls.copilot_ceiling(d) for d in domains},
            granted_by="default-policy",
            revocable=True,
            grant_id="default-copilot",
        )

    @classmethod
    def human_grant(cls) -> AuthorityGrant:
        """Create the standard human authority grant.

        Humans receive authority in every domain with override capability.
        """
        domains = set(AuthorityDomain)
        return AuthorityGrant(
            grantee="human",
            domains=domains,
            ceilings={d: cls.human_ceiling(d) for d in domains},
            granted_by="default-policy",
            revocable=False,
            grant_id="default-human",
        )

    @classmethod
    def orchestrator_grant(cls) -> AuthorityGrant:
        """Create the standard orchestrator authority grant."""
        domains = {
            AuthorityDomain.ORCHESTRATION,
            AuthorityDomain.DOCUMENTATION,
        }
        return AuthorityGrant(
            grantee="orchestrator",
            domains=domains,
            ceilings={d: cls.orchestrator_ceiling(d) for d in domains},
            granted_by="default-policy",
            revocable=True,
            grant_id="default-orchestrator",
        )

    # -- jurisdiction map --

    @classmethod
    def default_jurisdiction_map(cls) -> JurisdictionMap:
        """Build the default jurisdiction map.

        The map assigns coordinate prefixes to domains following the
        standard JuGeo project layout.
        """
        jmap = JurisdictionMap()
        jmap.set_default(AuthorityDomain.STRUCTURAL)
        jmap.assign("solver", AuthorityDomain.STRUCTURAL)
        jmap.assign("solver.arithmetic", AuthorityDomain.ARITHMETIC)
        jmap.assign("solver.relational", AuthorityDomain.RELATIONAL)
        jmap.assign("runtime", AuthorityDomain.HEAP)
        jmap.assign("runtime.heap", AuthorityDomain.HEAP)
        jmap.assign("runtime.identity", AuthorityDomain.IDENTITY)
        jmap.assign("runtime.resource", AuthorityDomain.RESOURCE)
        jmap.assign("copilot", AuthorityDomain.SEMANTIC)
        jmap.assign("copilot.semantic", AuthorityDomain.SEMANTIC)
        jmap.assign("copilot.behavioral", AuthorityDomain.BEHAVIORAL)
        jmap.assign("orchestrator", AuthorityDomain.ORCHESTRATION)
        jmap.assign("documentation", AuthorityDomain.DOCUMENTATION)
        return jmap

    # -- top-level factory --

    @classmethod
    def apply(cls) -> Tuple[AuthorityRegistry, JurisdictionMap]:
        """Create and populate a registry and jurisdiction map.

        This is the primary entry point for setting up the authority
        model at system startup.

        Returns
        -------
        registry:
            A fully populated :class:`AuthorityRegistry`.
        jurisdiction:
            A fully populated :class:`JurisdictionMap`.
        """
        registry = AuthorityRegistry()
        registry.grant(cls.solver_grant())
        registry.grant(cls.runtime_grant())
        registry.grant(cls.copilot_grant())
        registry.grant(cls.human_grant())
        registry.grant(cls.orchestrator_grant())

        jmap = cls.default_jurisdiction_map()

        logger.info(
            "DefaultAuthorityPolicy applied: %d grants, %d prefixes",
            sum(
                len(registry.get_grants_for(g))
                for g in ["solver", "runtime", "copilot", "human", "orchestrator"]
            ),
            len(jmap.all_prefixes()),
        )

        return registry, jmap

    @classmethod
    def summary(cls) -> str:
        """Return a human-readable summary of the default policy."""
        lines = [
            "DefaultAuthorityPolicy",
            "  solver:       STRUCTURAL, ARITHMETIC        (trust=100, no witness)",
            "  runtime:      HEAP, IDENTITY, RESOURCE      (trust=100, no witness)",
            "  copilot:      SEMANTIC, BEHAVIORAL          (trust=60, witness+corroboration)",
            "  human:        ALL DOMAINS                   (trust=100, override)",
            "  orchestrator: ORCHESTRATION, DOCUMENTATION  (trust=80)",
        ]
        return "\n".join(lines)

    @classmethod
    def validate_policy(cls) -> List[str]:
        """Run self-checks on the default policy.

        Returns a list of warnings (empty means valid).
        """
        registry, jmap = cls.apply()
        warnings = registry.validate_all_grants()

        # Every domain should be covered by at least one grantee.
        for domain in AuthorityDomain:
            grantees = registry.get_grantees_for_domain(domain)
            if not grantees:
                warnings.append(
                    f"Domain {domain.value} has no grantee in the "
                    f"default policy."
                )

        # Copilot should never exceed its ceiling.
        copilot_merged = registry.merge_grants("copilot")
        if copilot_merged is not None:
            for d in copilot_merged.domains:
                ceil = copilot_merged.ceiling_for(d)
                if ceil is not None and ceil.max_trust_level > 60:
                    warnings.append(
                        f"Copilot trust for {d.value} exceeds the "
                        f"policy ceiling of 60."
                    )

        return warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Legacy (backward-compatible)
    "AuthorityTier",
    "DelegationRule",
    "AuthorityCenter",
    "build_authority_center",
    "validate_delegation_graph",
    # New authority model
    "AuthorityDomain",
    "AuthorityCeiling",
    "AuthorityGrant",
    "AuthorityRegistry",
    "AuthorityViolation",
    "AuthorityEnforcer",
    "JurisdictionMap",
    "AuthorityDelegation",
    "DelegationChain",
    "AuditEntry",
    "AuthorityAuditLog",
    "DefaultAuthorityPolicy",
]
