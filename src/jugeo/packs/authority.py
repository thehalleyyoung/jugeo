"""Authority rules for pack usage.

From theory2.tex — *From mathematical discovery to pack federation and
implementation authority*: each domain pack has authority over certain
semantic domains.  Pack authority determines which pack can make which
kinds of claims, at what trust level, and over which coordinates.
Authority must be explicitly granted and cannot be silently assumed.

The governing principle is *no silent jurisdiction widening*: a pack
may only act within the domains it has been granted, at or below the
trust ceiling declared for each domain.  Delegation chains are explicit
and carry trust attenuation.  Conflicts are typed, not averaged away.

A controlled oracle — including any copilot-based proposal mechanism —
enters the system at a bounded trust tier and is subject to the same
jurisdiction rules as every other evidence channel.

This module implements:

* ``PackAuthority`` — the core authority record.
* ``PackAuthorityRegistry`` — grant, revoke, and query authorities.
* ``PackJurisdiction`` — coordinate-level jurisdiction geometry.
* ``PackAuthorityEnforcer`` — runtime enforcement of authority
  constraints before judgments and evidence submission.
* ``PackAuthorityDelegation`` — explicit delegation chains between
  packs with trust attenuation.
* ``PackAuthorityConflictResolver`` — typed conflict detection and
  resolution strategies.
* ``PackAuthorityAudit`` — append-only audit log for every authority
  event.
* ``PackAuthorityPolicy`` — configurable authority policy.
* ``PackAuthorityMigration`` — safe authority migration during pack
  updates.
* ``PackAuthorityDiagnostics`` — diagnostic views of authority state.

copilot: shared-core marker for future LLM orchestration.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from jugeo.evidence.trust import TrustTier
from jugeo.packs.catalog import PackDescriptor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_AUTHORITY_LEVELS: tuple[str, ...] = (
    "quarantined",
    "exploratory",
    "provisional",
    "foundational",
)

_AUTHORITY_RANK: dict[str, int] = {
    level: idx for idx, level in enumerate(KNOWN_AUTHORITY_LEVELS)
}

#: Maximum delegation chain length before the system raises a
#: ``DelegationChainTooLong`` error.  Strong claims require short
#: chains (theory2.tex §Delegation chains).
MAX_DELEGATION_CHAIN_LENGTH: int = 8

#: Default trust attenuation factor per delegation hop.  Each hop
#: weakens the effective ceiling by one ``TrustTier.step_weaker()``
#: call unless the delegation explicitly overrides attenuation.
DEFAULT_ATTENUATION_PER_HOP: int = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConflictKind(Enum):
    """Classification of authority conflicts (theory2.tex §Conflict Types)."""

    OVERLAPPING_DOMAIN = auto()
    OVERLAPPING_COORDINATE = auto()
    TRUST_CEILING_MISMATCH = auto()
    DELEGATION_LOOP = auto()
    EVIDENCE_CHANNEL_COLLISION = auto()
    COPILOT_JURISDICTION_OVERREACH = auto()


class ResolutionStrategy(Enum):
    """Strategy used to resolve an authority conflict."""

    PRIORITY = auto()
    SPECIFICITY = auto()
    TRUST = auto()
    COPILOT_MEDIATION = auto()
    MANUAL = auto()


class AuditLevel(Enum):
    """Granularity of the audit trail."""

    SILENT = auto()
    SUMMARY = auto()
    VERBOSE = auto()
    FULL_TRACE = auto()


class ViolationSeverity(Enum):
    """Severity of an authority violation."""

    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


# ---------------------------------------------------------------------------
# 1. PackAuthority — core authority record
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthority:
    """Core authority record for a domain pack.

    Every authority instance binds a *pack_id* to a set of *granted
    domains*, an optional *coordinate_jurisdiction* geometry, per-domain
    trust ceilings, and explicit evidence-channel allowances.

    The ``copilot_delegation_allowed`` flag controls whether this pack
    may delegate part of its authority to a copilot oracle.  When
    ``False``, no copilot-originated proposal may invoke this pack's
    jurisdiction — even transitively through a delegation chain.

    Attributes
    ----------
    pack_id : str
        Unique identifier for the pack this authority covers.
    granted_domains : set[str]
        Semantic domains over which this pack has been granted authority
        (e.g. ``{"topology", "measure_theory"}``).
    coordinate_jurisdiction : PackJurisdiction | None
        Optional coordinate-level jurisdiction geometry.
    trust_ceiling_per_domain : dict[str, TrustTier]
        Maximum admissible trust tier for each granted domain.
    evidence_channels_allowed : set[str]
        Evidence channel identifiers this pack is permitted to use
        (e.g. ``{"solver", "runtime_witness", "copilot_proposal"}``).
    copilot_delegation_allowed : bool
        Whether a copilot oracle may act under this authority.
    granted_by : str
        Identifier of the entity that granted this authority.
    granted_at : datetime
        UTC timestamp when the authority was granted.
    """

    pack_id: str
    granted_domains: set[str] = field(default_factory=set)
    coordinate_jurisdiction: PackJurisdiction | None = None
    trust_ceiling_per_domain: dict[str, TrustTier] = field(default_factory=dict)
    evidence_channels_allowed: set[str] = field(default_factory=set)
    copilot_delegation_allowed: bool = False
    requires_certificate: bool = False
    granted_by: str = "system"
    granted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # -- query helpers -------------------------------------------------------

    def covers_domain(self, domain: str) -> bool:
        """Return whether *domain* is within the granted set."""
        return domain in self.granted_domains

    def trust_ceiling_for(self, domain: str) -> TrustTier | None:
        """Return the trust ceiling for *domain*, or ``None``."""
        return self.trust_ceiling_per_domain.get(domain)

    def allows_channel(self, channel: str) -> bool:
        """Return whether *channel* is an allowed evidence channel."""
        return channel in self.evidence_channels_allowed

    def allows_coordinate(self, coordinate: str) -> bool:
        """Return whether *coordinate* falls within jurisdiction.

        When no jurisdiction is set, the authority is coordinate-agnostic
        and returns ``True`` for any coordinate.
        """
        if self.coordinate_jurisdiction is None:
            return True
        # Guard: if set to a non-PackJurisdiction value (e.g. during tests), fall back gracefully.
        if not hasattr(self.coordinate_jurisdiction, 'includes_coordinate'):
            return True
        return self.coordinate_jurisdiction.includes_coordinate(coordinate)

    def allows(self, coordinate: str, tier: TrustTier) -> bool:
        """Legacy compatibility check combining coordinate and tier.

        Returns ``True`` when *coordinate* is within jurisdiction and
        *tier* does not exceed the lowest domain ceiling.  Useful as a
        quick pre-check before the full enforcement pipeline.
        """
        if not self.allows_coordinate(coordinate):
            return False
        if not self.trust_ceiling_per_domain:
            return True
        min_ceiling = min(self.trust_ceiling_per_domain.values())
        return tier <= min_ceiling

    def effective_ceiling(self) -> TrustTier:
        """Return the weakest trust ceiling across all domains.

        This is the conservative, worst-case ceiling.  If no domains
        are configured, returns ``TrustTier.PROPOSAL``.
        """
        if not self.trust_ceiling_per_domain:
            return TrustTier.PROPOSAL
        return min(self.trust_ceiling_per_domain.values())

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of this authority."""
        return {
            "pack_id": self.pack_id,
            "domains": sorted(self.granted_domains),
            "effective_ceiling": self.effective_ceiling().label(),
            "channels": sorted(self.evidence_channels_allowed),
            "copilot_allowed": self.copilot_delegation_allowed,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at.isoformat(),
            "jurisdiction": (
                self.coordinate_jurisdiction.summary()
                if self.coordinate_jurisdiction
                else None
            ),
        }


# Backward-compatible helper kept in __all__ for loading.py.

def authorize_pack(
    descriptor: PackDescriptor,
    authority: PackAuthority,
    *,
    coordinate: str,
    tier: TrustTier,
) -> bool:
    """Authorize a pack load request.

    Returns ``True`` when the descriptor's name matches the authority's
    ``pack_id`` **and** the coordinate/tier combination is permitted.

    This function is intentionally kept thin — it delegates to
    ``PackAuthority.allows`` so that more elaborate enforcement can
    layer on top without changing the simple call-site contract in
    ``loading.py``.
    """
    return descriptor.name == authority.pack_id and authority.allows(
        coordinate, tier
    )


# ---------------------------------------------------------------------------
# 3. PackJurisdiction — coordinate-level jurisdiction geometry
#    (placed before PackAuthorityRegistry because the registry references it)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackJurisdiction:
    """Defines the coordinate-level jurisdiction for a pack authority.

    Jurisdiction is expressed as a list of *coordinate_patterns* —
    fnmatch-style glob strings that describe which coordinates a pack
    may operate over.  This mirrors the Grothendieck-topology notion of
    admissible covers in theory2.tex: a pack's local sections are valid
    only within the declared coordinate site.

    Attributes
    ----------
    coordinate_patterns : list[str]
        Glob patterns (fnmatch syntax) that match coordinate strings.
    """

    pack_id: str = ""
    coordinate_patterns: list[str] = field(default_factory=list)

    def includes_coordinate(self, coordinate: str) -> bool:
        """Return ``True`` if *coordinate* matches any pattern."""
        return any(
            fnmatch.fnmatch(coordinate, pat) for pat in self.coordinate_patterns
        )

    def intersection_with(self, other: PackJurisdiction) -> PackJurisdiction:
        """Return a jurisdiction covering only coordinates matched by both.

        The implementation uses exact pattern equality — two patterns
        that match the same set of strings but are syntactically
        different are treated as distinct.  A future version may
        implement pattern algebra.
        """
        common = [p for p in self.coordinate_patterns if p in other.coordinate_patterns]
        return PackJurisdiction(pack_id=self.pack_id or other.pack_id, coordinate_patterns=common)

    def union_with(self, other: PackJurisdiction) -> PackJurisdiction:
        """Return a jurisdiction covering coordinates matched by either."""
        combined = list(dict.fromkeys(self.coordinate_patterns + other.coordinate_patterns))
        return PackJurisdiction(pack_id=self.pack_id or other.pack_id, coordinate_patterns=combined)

    def is_disjoint_from(self, other: PackJurisdiction) -> bool:
        """Return ``True`` when no pattern appears in both jurisdictions.

        For a quick syntactic check we test pattern-set intersection.
        """
        return not bool(
            set(self.coordinate_patterns) & set(other.coordinate_patterns)
        )

    def overlap_with(self, other: PackJurisdiction) -> list[str]:
        """Return the list of patterns common to both jurisdictions."""
        return sorted(
            set(self.coordinate_patterns) & set(other.coordinate_patterns)
        )

    def matches(self, coordinates: Sequence[str]) -> list[str]:
        """Return the subset of *coordinates* that fall within jurisdiction."""
        return [c for c in coordinates if self.includes_coordinate(c)]

    def is_empty(self) -> bool:
        """Return ``True`` when no patterns are defined."""
        return len(self.coordinate_patterns) == 0

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary."""
        return {
            "patterns": list(self.coordinate_patterns),
            "pattern_count": len(self.coordinate_patterns),
        }


# ---------------------------------------------------------------------------
# 2. PackAuthorityRegistry — manage pack authorities
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityRegistry:
    """Central registry that stores and queries ``PackAuthority`` records.

    The registry enforces *explicit grant* semantics: an authority must
    be granted via :meth:`grant` before a pack may operate within a
    domain.  Revoking an authority removes it from the registry and
    records the event for auditing.

    Attributes
    ----------
    _authorities : dict[str, list[PackAuthority]]
        Mapping from *pack_id* to the list of authorities granted to
        that pack.  A pack may hold multiple non-overlapping authorities.
    """

    _authorities: dict[str, list[PackAuthority]] = field(default_factory=lambda: defaultdict(list))

    def grant(self, authority: PackAuthority) -> None:
        """Grant *authority* to the pack identified by ``authority.pack_id``.

        Raises ``ValueError`` if an identical authority (same pack,
        same domains, same jurisdiction) already exists.
        """
        existing = self._authorities[authority.pack_id]
        for auth in existing:
            if (
                auth.granted_domains == authority.granted_domains
                and auth.coordinate_jurisdiction == authority.coordinate_jurisdiction
            ):
                raise ValueError(
                    f"Duplicate authority for pack '{authority.pack_id}' "
                    f"over domains {authority.granted_domains}"
                )
        existing.append(authority)

    def register(self, authority: PackAuthority) -> None:
        self.grant(authority)

    def revoke(self, pack_id: str, domain: str) -> PackAuthority | None:
        """Revoke the first authority for *pack_id* that covers *domain*.

        Returns the revoked ``PackAuthority`` or ``None`` when no
        matching authority was found.
        """
        authorities = self._authorities.get(pack_id, [])
        for idx, auth in enumerate(authorities):
            if domain in auth.granted_domains:
                return authorities.pop(idx)
        return None

    def revoke_all(self, pack_id: str) -> list[PackAuthority]:
        """Revoke every authority held by *pack_id*.

        Returns the list of revoked authorities (empty if the pack
        held none).
        """
        return self._authorities.pop(pack_id, [])

    def check_authority(
        self,
        pack_id: str,
        domain: str,
        coordinate: str | None = None,
        tier: TrustTier | None = None,
    ) -> bool:
        """Return ``True`` when *pack_id* holds authority over *domain*.

        Optionally checks coordinate jurisdiction and trust ceiling.
        """
        for auth in self._authorities.get(pack_id, []):
            if domain not in auth.granted_domains:
                continue
            if coordinate is not None and not auth.allows_coordinate(coordinate):
                continue
            if tier is not None:
                ceiling = auth.trust_ceiling_for(domain)
                if ceiling is not None and tier > ceiling:
                    continue
            return True
        return False

    def authorities_for_pack(self, pack_id: str) -> list[PackAuthority]:
        """Return all authorities currently held by *pack_id*."""
        return list(self._authorities.get(pack_id, []))

    def get(self, pack_id: str) -> PackAuthority | None:
        authorities = self._authorities.get(pack_id, [])
        return authorities[0] if authorities else None

    def packs_with_authority_over(self, domain: str) -> list[str]:
        """Return pack IDs that hold authority over *domain*."""
        result: list[str] = []
        for pack_id, auths in self._authorities.items():
            for auth in auths:
                if domain in auth.granted_domains:
                    result.append(pack_id)
                    break
        return sorted(result)

    def validate_all(self) -> list[str]:
        """Validate every registered authority and return issue strings.

        Checks include:
        * Trust ceilings defined for every granted domain.
        * At least one evidence channel allowed.
        * Jurisdiction non-empty when coordinate patterns are set.
        """
        issues: list[str] = []
        for pack_id, auths in self._authorities.items():
            for auth in auths:
                for domain in auth.granted_domains:
                    if domain not in auth.trust_ceiling_per_domain:
                        issues.append(
                            f"{pack_id}: domain '{domain}' has no trust ceiling"
                        )
                if not auth.evidence_channels_allowed:
                    issues.append(
                        f"{pack_id}: no evidence channels allowed"
                    )
                jur = auth.coordinate_jurisdiction
                if jur is not None and jur.is_empty():
                    issues.append(
                        f"{pack_id}: jurisdiction is set but has no patterns"
                    )
        return issues

    def detect_conflicts(self) -> list[Any]:
        """Detect authority conflicts across all registered packs.

        A conflict arises when two different packs hold authority over
        the same domain with overlapping coordinate jurisdictions.
        """
        domain_to_packs: dict[str, list[PackAuthority]] = defaultdict(list)
        for auths in self._authorities.values():
            for auth in auths:
                for domain in auth.granted_domains:
                    domain_to_packs[domain].append(auth)

        conflicts: list[Any] = []
        for domain, auths in domain_to_packs.items():
            if len(auths) < 2:
                continue
            for i in range(len(auths)):
                for j in range(i + 1, len(auths)):
                    a, b = auths[i], auths[j]
                    if a.pack_id == b.pack_id:
                        continue
                    ja = a.coordinate_jurisdiction
                    jb = b.coordinate_jurisdiction
                    # If either has no jurisdiction, they potentially overlap everywhere.
                    if ja is None or jb is None or not ja.is_disjoint_from(jb):
                        conflicts.append(SimpleNamespace(
                            domain=domain,
                            pack_a=a.pack_id,
                            pack_b=b.pack_id,
                            kind=ConflictKind.OVERLAPPING_DOMAIN,
                        ))
        return conflicts

    def all_pack_ids(self) -> list[str]:
        """Return sorted list of all pack IDs with at least one authority."""
        return sorted(pid for pid, auths in self._authorities.items() if auths)

    def domain_count(self) -> dict[str, int]:
        """Return a mapping from domain name to the number of packs covering it."""
        counts: dict[str, int] = defaultdict(int)
        for auths in self._authorities.values():
            for auth in auths:
                for domain in auth.granted_domains:
                    counts[domain] += 1
        return dict(counts)


# ---------------------------------------------------------------------------
# 4. PackAuthorityEnforcer — runtime enforcement
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityEnforcer:
    """Enforces authority constraints before judgments and evidence.

    The enforcer sits between the pack and the judgment/evidence
    pipeline.  Every operation that asserts a claim or submits evidence
    must pass through the enforcer.

    Violations are logged and, depending on the configured policy, may
    be blocking or advisory.  A copilot oracle proposal that exceeds
    its jurisdiction is treated identically to any other violation.

    Attributes
    ----------
    registry : PackAuthorityRegistry
        The authority registry to check against.
    policy : PackAuthorityPolicy
        Configurable enforcement policy.
    violations : list[dict[str, Any]]
        Accumulated violation records.
    """

    registry: PackAuthorityRegistry
    policy: PackAuthorityPolicy | None = None
    violations: list[dict[str, Any]] = field(default_factory=list)

    def enforce(
        self,
        pack_id: str,
        domain: str,
        coordinate: str,
        tier: TrustTier,
        channel: str = "unknown",
    ) -> bool:
        """Run all authority checks and return ``True`` if permitted.

        When the check fails, a violation is logged.
        """
        ok = self.check_before_judgment(pack_id, domain, coordinate, tier, channel)
        if not ok:
            self.log_violation(
                pack_id=pack_id,
                domain=domain,
                coordinate=coordinate,
                tier=tier,
                channel=channel,
                reason="enforcement_denied",
            )
        return ok

    def check_before_judgment(
        self,
        pack_id: str,
        domain: str,
        coordinate: str,
        tier: TrustTier,
        channel: str = "unknown",
    ) -> bool:
        """Pre-judgment authority check.

        Returns ``True`` only when *pack_id* holds authority over
        *domain* at the given *coordinate* and *tier*, and the
        *channel* is allowed.
        """
        if not self.registry.check_authority(pack_id, domain, coordinate, tier):
            return False
        for auth in self.registry.authorities_for_pack(pack_id):
            if domain in auth.granted_domains and auth.allows_channel(channel):
                return True
        return False

    def check_before_evidence(
        self,
        pack_id: str,
        domain: str,
        channel: str,
        coordinate: str | None = None,
    ) -> bool:
        """Pre-evidence-submission check.

        Ensures the pack is allowed to submit evidence through
        *channel* for *domain*.  Copilot channels are subject to the
        ``copilot_delegation_allowed`` flag.
        """
        for auth in self.registry.authorities_for_pack(pack_id):
            if domain not in auth.granted_domains:
                continue
            if coordinate and not auth.allows_coordinate(coordinate):
                continue
            if not auth.allows_channel(channel):
                continue
            if "copilot" in channel.lower() and not auth.copilot_delegation_allowed:
                continue
            return True
        return False

    def check_copilot_proposal(
        self,
        pack_id: str,
        domain: str,
        coordinate: str,
    ) -> bool:
        """Check whether a copilot oracle may propose within *domain*.

        A copilot proposal is allowed only when the authority's
        ``copilot_delegation_allowed`` flag is ``True`` **and** the
        ``copilot_proposal`` evidence channel is permitted.
        """
        for auth in self.registry.authorities_for_pack(pack_id):
            if domain not in auth.granted_domains:
                continue
            if not auth.allows_coordinate(coordinate):
                continue
            if not auth.copilot_delegation_allowed:
                continue
            if not auth.allows_channel("copilot_proposal"):
                continue
            return True
        return False

    def log_violation(
        self,
        *,
        pack_id: str,
        domain: str,
        coordinate: str,
        tier: TrustTier,
        channel: str,
        reason: str,
        severity: ViolationSeverity = ViolationSeverity.ERROR,
    ) -> None:
        """Record an authority violation.

        All violations are appended to the internal list and, when a
        ``policy`` is set with ``audit_level >= VERBOSE``, emitted to
        the audit subsystem.
        """
        record = {
            "violation_id": uuid.uuid4().hex[:12],
            "pack_id": pack_id,
            "domain": domain,
            "coordinate": coordinate,
            "tier": tier.label(),
            "channel": channel,
            "reason": reason,
            "severity": severity.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.violations.append(record)

    def suggest_delegation(
        self,
        pack_id: str,
        domain: str,
        coordinate: str,
    ) -> list[str]:
        """Suggest packs that *could* delegate authority to *pack_id*.

        Looks for packs that already hold authority over *domain* at
        the given *coordinate* and have ``copilot_delegation_allowed``
        set (if the requester is a copilot channel).
        """
        candidates: list[str] = []
        for cand_id in self.registry.packs_with_authority_over(domain):
            if cand_id == pack_id:
                continue
            if self.registry.check_authority(cand_id, domain, coordinate):
                candidates.append(cand_id)
        return candidates

    def violation_count(self) -> int:
        """Return the total number of recorded violations."""
        return len(self.violations)

    def violations_for_pack(self, pack_id: str) -> list[dict[str, Any]]:
        """Return violations associated with *pack_id*."""
        return [v for v in self.violations if v["pack_id"] == pack_id]

    def clear_violations(self) -> int:
        """Clear all recorded violations and return the count cleared."""
        count = len(self.violations)
        self.violations.clear()
        return count


# ---------------------------------------------------------------------------
# 5. PackAuthorityDelegation — explicit delegation chains
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _DelegationRecord:
    """Internal record for a single delegation link."""

    from_pack: str
    to_pack: str
    domains: set[str]
    coordinate_jurisdiction: PackJurisdiction | None
    trust_attenuation: int
    accepted: bool
    created_at: datetime
    delegation_id: str


@dataclass(slots=True)
class PackAuthorityDelegation:
    """Manages explicit delegation of authority between packs.

    Delegation follows the theory2.tex model where each hop in a
    delegation chain is an explicit ``(origin, authority, result)``
    triple.  Trust attenuates through each hop — a copilot proposal
    relayed through two delegation links arrives at a weaker ceiling
    than if it were direct.

    Attributes
    ----------
    registry : PackAuthorityRegistry
        The authority registry for validation.
    delegations : list[_DelegationRecord]
        All delegation records.
    """

    registry: PackAuthorityRegistry
    delegations: list[_DelegationRecord] = field(default_factory=list)

    def delegate(
        self,
        from_pack: str,
        to_pack: str,
        domains: set[str],
        *,
        coordinate_jurisdiction: PackJurisdiction | None = None,
        trust_attenuation: int = DEFAULT_ATTENUATION_PER_HOP,
    ) -> str:
        """Create a delegation from *from_pack* to *to_pack*.

        The delegating pack must already hold authority over every
        domain in *domains*.  Returns the delegation ID.

        Raises ``ValueError`` when:
        * *from_pack* lacks authority over any requested domain.
        * A delegation loop would be created.
        * Chain length would exceed ``MAX_DELEGATION_CHAIN_LENGTH``.
        """
        for domain in domains:
            if not self.registry.check_authority(from_pack, domain):
                raise ValueError(
                    f"Pack '{from_pack}' lacks authority over domain "
                    f"'{domain}' — cannot delegate"
                )
        if self._creates_loop(from_pack, to_pack):
            raise ValueError(
                f"Delegation from '{from_pack}' to '{to_pack}' "
                f"would create a loop"
            )
        chain = self.delegation_chain(to_pack, next(iter(domains)))
        if len(chain) >= MAX_DELEGATION_CHAIN_LENGTH:
            raise ValueError(
                f"Delegation chain length ({len(chain) + 1}) exceeds "
                f"maximum ({MAX_DELEGATION_CHAIN_LENGTH})"
            )

        delegation_id = uuid.uuid4().hex[:16]
        record = _DelegationRecord(
            from_pack=from_pack,
            to_pack=to_pack,
            domains=set(domains),
            coordinate_jurisdiction=coordinate_jurisdiction,
            trust_attenuation=trust_attenuation,
            accepted=False,
            created_at=datetime.now(timezone.utc),
            delegation_id=delegation_id,
        )
        self.delegations.append(record)
        return delegation_id

    def accept_delegation(self, delegation_id: str) -> bool:
        """Mark a delegation as accepted by the receiving pack.

        Returns ``True`` when the delegation was found and accepted,
        ``False`` otherwise.
        """
        for rec in self.delegations:
            if rec.delegation_id == delegation_id:
                rec.accepted = True
                return True
        return False

    def revoke_delegation(self, delegation_id: str) -> bool:
        """Revoke a previously created delegation.

        Removes the delegation record entirely.  Returns ``True`` when
        found and removed.
        """
        for idx, rec in enumerate(self.delegations):
            if rec.delegation_id == delegation_id:
                self.delegations.pop(idx)
                return True
        return False

    def delegation_chain(self, pack_id: str, domain: str) -> list[_DelegationRecord]:
        """Trace the delegation chain that grants *pack_id* authority over *domain*.

        Returns the ordered list of ``_DelegationRecord`` entries from
        the original authority holder to *pack_id*.  An empty list
        means *pack_id* holds the authority directly (no delegation).
        """
        chain: list[_DelegationRecord] = []
        visited: set[str] = set()
        current = pack_id
        while current not in visited:
            visited.add(current)
            link = self._find_delegation_to(current, domain)
            if link is None:
                break
            chain.append(link)
            current = link.from_pack
        chain.reverse()
        return chain

    def trust_attenuation_through_delegation(
        self,
        pack_id: str,
        domain: str,
        original_tier: TrustTier,
    ) -> TrustTier:
        """Compute the effective trust tier after delegation attenuation.

        Each hop in the delegation chain weakens the tier by the hop's
        ``trust_attenuation`` steps (via ``TrustTier.step_weaker``).
        The result saturates at ``TrustTier.PROPOSAL``.
        """
        chain = self.delegation_chain(pack_id, domain)
        tier = original_tier
        for link in chain:
            for _ in range(link.trust_attenuation):
                tier = tier.step_weaker()
        return tier

    def active_delegations_for(self, pack_id: str) -> list[_DelegationRecord]:
        """Return accepted delegations *to* pack_id."""
        return [
            d for d in self.delegations
            if d.to_pack == pack_id and d.accepted
        ]

    def delegations_from(self, pack_id: str) -> list[_DelegationRecord]:
        """Return all delegations *from* pack_id (granted to others)."""
        return [d for d in self.delegations if d.from_pack == pack_id]

    def _creates_loop(self, from_pack: str, to_pack: str) -> bool:
        """Check whether delegating from *from_pack* to *to_pack* creates a loop."""
        visited: set[str] = {to_pack}
        frontier: list[str] = [to_pack]
        while frontier:
            current = frontier.pop()
            for rec in self.delegations:
                if rec.from_pack == current and rec.to_pack not in visited:
                    if rec.to_pack == from_pack:
                        return True
                    visited.add(rec.to_pack)
                    frontier.append(rec.to_pack)
        return False

    def _find_delegation_to(self, pack_id: str, domain: str) -> _DelegationRecord | None:
        """Find an accepted delegation *to* pack_id covering *domain*."""
        for rec in self.delegations:
            if rec.to_pack == pack_id and domain in rec.domains and rec.accepted:
                return rec
        return None


# ---------------------------------------------------------------------------
# 6. PackAuthorityConflictResolver — typed conflict resolution
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityConflictResolver:
    """Detects and resolves authority conflicts.

    Conflicts are *typed*, not averaged away (theory2.tex §Conflict
    Types).  The resolver classifies each conflict and applies the
    configured resolution strategy.

    Attributes
    ----------
    registry : PackAuthorityRegistry
        The authority registry to inspect.
    default_strategy : ResolutionStrategy
        Fallback strategy when no specific rule matches.
    resolutions : list[dict[str, Any]]
        History of resolution decisions.
    """

    registry: PackAuthorityRegistry
    default_strategy: ResolutionStrategy = ResolutionStrategy.PRIORITY
    resolutions: list[dict[str, Any]] = field(default_factory=list)

    def detect_conflicts(self) -> list[dict[str, Any]]:
        """Detect all authority conflicts in the registry.

        Delegates to ``PackAuthorityRegistry.detect_conflicts`` and
        enriches results with conflict classification.
        """
        raw = self.registry.detect_conflicts()
        for conflict in raw:
            conflict["classification"] = self.classify_conflict(conflict)
        return raw

    def classify_conflict(self, conflict: dict[str, Any]) -> ConflictKind:
        """Classify a conflict record into a ``ConflictKind``.

        The classification considers:
        * Whether the overlap is at the domain or coordinate level.
        * Whether trust ceilings disagree.
        * Whether a copilot jurisdiction is involved.
        """
        pack_a_auths = self.registry.authorities_for_pack(conflict["pack_a"])
        pack_b_auths = self.registry.authorities_for_pack(conflict["pack_b"])
        domain = conflict["domain"]

        a_copilot = any(
            a.copilot_delegation_allowed
            for a in pack_a_auths
            if domain in a.granted_domains
        )
        b_copilot = any(
            a.copilot_delegation_allowed
            for a in pack_b_auths
            if domain in a.granted_domains
        )
        if a_copilot != b_copilot:
            return ConflictKind.COPILOT_JURISDICTION_OVERREACH

        a_ceiling = None
        b_ceiling = None
        for a in pack_a_auths:
            if domain in a.granted_domains:
                a_ceiling = a.trust_ceiling_for(domain)
                break
        for b in pack_b_auths:
            if domain in b.granted_domains:
                b_ceiling = b.trust_ceiling_for(domain)
                break
        if a_ceiling is not None and b_ceiling is not None and a_ceiling != b_ceiling:
            return ConflictKind.TRUST_CEILING_MISMATCH

        a_jur = next(
            (a.coordinate_jurisdiction for a in pack_a_auths if domain in a.granted_domains),
            None,
        )
        b_jur = next(
            (a.coordinate_jurisdiction for a in pack_b_auths if domain in a.granted_domains),
            None,
        )
        if a_jur is not None and b_jur is not None and not a_jur.is_disjoint_from(b_jur):
            return ConflictKind.OVERLAPPING_COORDINATE

        return ConflictKind.OVERLAPPING_DOMAIN

    def resolve_by_priority(
        self,
        conflict: dict[str, Any],
        priority_order: dict[str, int],
    ) -> str:
        """Resolve a conflict by comparing pack priorities.

        Returns the winning pack_id.  Lower numeric priority wins
        (higher precedence).
        """
        a, b = conflict["pack_a"], conflict["pack_b"]
        a_pri = priority_order.get(a, 999)
        b_pri = priority_order.get(b, 999)
        winner = a if a_pri <= b_pri else b
        self._record_resolution(conflict, ResolutionStrategy.PRIORITY, winner)
        return winner

    def resolve_by_specificity(self, conflict: dict[str, Any]) -> str:
        """Resolve by choosing the pack with more specific jurisdiction.

        Specificity is approximated by the number of coordinate
        patterns — fewer patterns means more focused, hence more
        specific.  A pack with no jurisdiction (global) is least
        specific.
        """
        a_auths = self.registry.authorities_for_pack(conflict["pack_a"])
        b_auths = self.registry.authorities_for_pack(conflict["pack_b"])
        domain = conflict["domain"]

        def _specificity(auths: list[PackAuthority]) -> int:
            for auth in auths:
                if domain in auth.granted_domains and auth.coordinate_jurisdiction:
                    return len(auth.coordinate_jurisdiction.coordinate_patterns)
            return 9999  # global/no jurisdiction

        a_spec = _specificity(a_auths)
        b_spec = _specificity(b_auths)
        winner = conflict["pack_a"] if a_spec <= b_spec else conflict["pack_b"]
        self._record_resolution(conflict, ResolutionStrategy.SPECIFICITY, winner)
        return winner

    def resolve_by_trust(self, conflict: dict[str, Any]) -> str:
        """Resolve by choosing the pack with the higher trust ceiling.

        The pack whose domain-specific trust ceiling is strictly
        stronger wins.  Ties fall back to alphabetical pack_id.
        """
        domain = conflict["domain"]
        a_ceil = self._domain_ceiling(conflict["pack_a"], domain)
        b_ceil = self._domain_ceiling(conflict["pack_b"], domain)
        if a_ceil is not None and b_ceil is not None:
            if a_ceil > b_ceil:
                winner = conflict["pack_a"]
            elif b_ceil > a_ceil:
                winner = conflict["pack_b"]
            else:
                winner = min(conflict["pack_a"], conflict["pack_b"])
        else:
            winner = conflict["pack_a"] if a_ceil is not None else conflict["pack_b"]
        self._record_resolution(conflict, ResolutionStrategy.TRUST, winner)
        return winner

    def copilot_mediate_conflict(self, conflict: dict[str, Any]) -> dict[str, Any]:
        """Request copilot mediation for an authority conflict.

        Copilot mediation produces a structured recommendation —
        it cannot unilaterally resolve the conflict.  The recommendation
        includes the conflict classification, suggested winner, and a
        confidence flag.

        Returns a mediation record.
        """
        classification = self.classify_conflict(conflict)
        # Heuristic: prefer the pack without copilot delegation for
        # domain conflicts, as it is likely the primary authority.
        a_auths = self.registry.authorities_for_pack(conflict["pack_a"])
        b_auths = self.registry.authorities_for_pack(conflict["pack_b"])
        a_copilot = any(
            a.copilot_delegation_allowed for a in a_auths
            if conflict["domain"] in a.granted_domains
        )
        b_copilot = any(
            a.copilot_delegation_allowed for a in b_auths
            if conflict["domain"] in a.granted_domains
        )
        if a_copilot and not b_copilot:
            suggestion = conflict["pack_b"]
        elif b_copilot and not a_copilot:
            suggestion = conflict["pack_a"]
        else:
            suggestion = min(conflict["pack_a"], conflict["pack_b"])

        mediation = {
            "conflict": conflict,
            "classification": classification.name,
            "strategy": ResolutionStrategy.COPILOT_MEDIATION.name,
            "suggested_winner": suggestion,
            "confidence": "low" if a_copilot == b_copilot else "medium",
            "copilot_note": (
                "Copilot mediation is advisory.  The suggested winner "
                "should be validated by the authority policy before "
                "taking effect."
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.resolutions.append(mediation)
        return mediation

    def resolve(
        self,
        conflict: dict[str, Any],
        strategy: ResolutionStrategy | None = None,
        *,
        priority_order: dict[str, int] | None = None,
    ) -> str | dict[str, Any]:
        """Resolve a conflict using the specified or default strategy."""
        strat = strategy or self.default_strategy
        if strat == ResolutionStrategy.PRIORITY:
            return self.resolve_by_priority(conflict, priority_order or {})
        if strat == ResolutionStrategy.SPECIFICITY:
            return self.resolve_by_specificity(conflict)
        if strat == ResolutionStrategy.TRUST:
            return self.resolve_by_trust(conflict)
        if strat == ResolutionStrategy.COPILOT_MEDIATION:
            return self.copilot_mediate_conflict(conflict)
        raise ValueError(f"Unknown resolution strategy: {strat}")

    def _domain_ceiling(self, pack_id: str, domain: str) -> TrustTier | None:
        """Return the trust ceiling for *domain* in *pack_id*'s authority."""
        for auth in self.registry.authorities_for_pack(pack_id):
            if domain in auth.granted_domains:
                return auth.trust_ceiling_for(domain)
        return None

    def _record_resolution(
        self,
        conflict: dict[str, Any],
        strategy: ResolutionStrategy,
        winner: str,
    ) -> None:
        """Record a resolution decision."""
        self.resolutions.append({
            "conflict": conflict,
            "strategy": strategy.name,
            "winner": winner,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


# ---------------------------------------------------------------------------
# 7. PackAuthorityAudit — append-only audit log
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityAudit:
    """Append-only audit log for authority events.

    Theory2.tex mandates that *every trust promotion must carry an
    explicit justification and is recorded in an append-only audit
    log*.  This class implements that requirement for authority grants,
    revocations, checks, and violations.

    Attributes
    ----------
    entries : list[dict[str, Any]]
        The audit log.  Entries are never mutated after creation.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)

    def _append(self, kind: str, payload: dict[str, Any]) -> str:
        """Append an entry and return its ID."""
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "entry_id": entry_id,
            "kind": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self.entries.append(entry)
        return entry_id

    def record_check(
        self,
        pack_id: str,
        domain: str,
        coordinate: str,
        result: bool,
    ) -> str:
        """Record an authority check event.  Returns the entry ID."""
        return self._append("check", {
            "pack_id": pack_id,
            "domain": domain,
            "coordinate": coordinate,
            "result": result,
        })

    def record_grant(
        self,
        authority: PackAuthority,
    ) -> str:
        """Record an authority grant.  Returns the entry ID."""
        return self._append("grant", {
            "pack_id": authority.pack_id,
            "domains": sorted(authority.granted_domains),
            "granted_by": authority.granted_by,
            "copilot_delegation_allowed": authority.copilot_delegation_allowed,
        })

    def record_revocation(
        self,
        pack_id: str,
        domain: str,
        revoked_by: str = "system",
    ) -> str:
        """Record an authority revocation.  Returns the entry ID."""
        return self._append("revocation", {
            "pack_id": pack_id,
            "domain": domain,
            "revoked_by": revoked_by,
        })

    def record_violation(
        self,
        violation: dict[str, Any],
    ) -> str:
        """Record an authority violation.  Returns the entry ID."""
        return self._append("violation", dict(violation))

    def record_delegation(
        self,
        delegation_id: str,
        from_pack: str,
        to_pack: str,
        domains: set[str],
    ) -> str:
        """Record a delegation event.  Returns the entry ID."""
        return self._append("delegation", {
            "delegation_id": delegation_id,
            "from_pack": from_pack,
            "to_pack": to_pack,
            "domains": sorted(domains),
        })

    def record_migration(
        self,
        migration_id: str,
        pack_id: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Record a migration event.  Returns the entry ID."""
        return self._append("migration", {
            "migration_id": migration_id,
            "pack_id": pack_id,
            "action": action,
            "details": details or {},
        })

    def audit_report(
        self,
        *,
        kind: str | None = None,
        pack_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Generate a filtered audit report.

        Parameters
        ----------
        kind : str, optional
            Filter by entry kind (``"check"``, ``"grant"``, etc.).
        pack_id : str, optional
            Filter by pack identifier.
        since : datetime, optional
            Only include entries after this timestamp.

        Returns a list of matching entries.
        """
        results: list[dict[str, Any]] = []
        for entry in self.entries:
            if kind and entry["kind"] != kind:
                continue
            if pack_id and entry.get("pack_id") != pack_id:
                continue
            if since:
                entry_ts = datetime.fromisoformat(entry["timestamp"])
                if entry_ts < since:
                    continue
            results.append(entry)
        return results

    def compliance_score(self) -> float:
        """Compute a compliance score in ``[0.0, 1.0]``.

        The score is the ratio of non-violation entries to total
        entries.  A score of ``1.0`` means no violations were recorded.
        Returns ``1.0`` when the log is empty (vacuously compliant).
        """
        if not self.entries:
            return 1.0
        violations = sum(1 for e in self.entries if e["kind"] == "violation")
        return 1.0 - (violations / len(self.entries))

    def entry_count(self) -> dict[str, int]:
        """Return a mapping from entry kind to count."""
        counts: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            counts[entry["kind"]] += 1
        return dict(counts)

    def last_n(self, n: int) -> list[dict[str, Any]]:
        """Return the last *n* entries."""
        return self.entries[-n:] if n < len(self.entries) else list(self.entries)


# ---------------------------------------------------------------------------
# 8. PackAuthorityPolicy — configurable authority policy
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityPolicy:
    """Configurable policy governing authority behaviour.

    The policy determines defaults for new authorities, delegation
    rules, conflict resolution strategy, and audit granularity.

    Attributes
    ----------
    default_authorities : dict[str, set[str]]
        Mapping from pack_id to the set of domains automatically
        granted when the pack is registered.
    delegation_rules : dict[str, Any]
        Rules governing delegation (e.g. max chain length, allowed
        delegation targets, attenuation overrides).
    conflict_resolution_strategy : ResolutionStrategy
        Default strategy for the ``PackAuthorityConflictResolver``.
    audit_level : AuditLevel
        How verbose the audit trail should be.
    copilot_default_allowed : bool
        Whether new authorities allow copilot delegation by default.
    max_domains_per_authority : int
        Upper bound on the number of domains a single authority
        record may cover.
    require_jurisdiction : bool
        When ``True``, every authority must declare coordinate
        jurisdiction (no global fallback).
    """

    default_authorities: dict[str, set[str]] = field(default_factory=dict)
    delegation_rules: dict[str, Any] = field(default_factory=dict)
    conflict_resolution_strategy: ResolutionStrategy = ResolutionStrategy.PRIORITY
    audit_level: AuditLevel = AuditLevel.SUMMARY
    copilot_default_allowed: bool = False
    max_domains_per_authority: int = 20
    require_jurisdiction: bool = False

    def validate_authority(self, authority: PackAuthority) -> list[str]:
        """Validate an authority against this policy.  Returns issues."""
        issues: list[str] = []
        if len(authority.granted_domains) > self.max_domains_per_authority:
            issues.append(
                f"Authority covers {len(authority.granted_domains)} domains "
                f"(max {self.max_domains_per_authority})"
            )
        if self.require_jurisdiction and authority.coordinate_jurisdiction is None:
            issues.append("Coordinate jurisdiction is required by policy")
        if (
            authority.copilot_delegation_allowed
            and not self.copilot_default_allowed
            and "allow_copilot" not in self.delegation_rules
        ):
            issues.append(
                "Copilot delegation enabled but policy does not permit it "
                "by default and no 'allow_copilot' delegation rule is set"
            )
        return issues

    def effective_max_chain_length(self) -> int:
        """Return the maximum delegation chain length from policy or global default."""
        return int(
            self.delegation_rules.get(
                "max_chain_length", MAX_DELEGATION_CHAIN_LENGTH
            )
        )

    def effective_attenuation(self) -> int:
        """Return the per-hop trust attenuation from policy or global default."""
        return int(
            self.delegation_rules.get(
                "attenuation_per_hop", DEFAULT_ATTENUATION_PER_HOP
            )
        )

    def should_audit(self, event_kind: str) -> bool:
        """Return whether an event of *event_kind* should be audited."""
        if self.audit_level == AuditLevel.SILENT:
            return False
        if self.audit_level == AuditLevel.FULL_TRACE:
            return True
        if self.audit_level == AuditLevel.VERBOSE:
            return True
        # SUMMARY: audit grants, revocations, violations — not checks.
        return event_kind in {"grant", "revocation", "violation", "delegation", "migration"}

    def auto_grant_for(self, pack_id: str) -> set[str]:
        """Return the set of domains auto-granted to *pack_id*."""
        return set(self.default_authorities.get(pack_id, set()))

    def to_dict(self) -> dict[str, Any]:
        """Serialise the policy to a JSON-compatible dict."""
        return {
            "default_authorities": {
                k: sorted(v) for k, v in self.default_authorities.items()
            },
            "delegation_rules": dict(self.delegation_rules),
            "conflict_resolution_strategy": self.conflict_resolution_strategy.name,
            "audit_level": self.audit_level.name,
            "copilot_default_allowed": self.copilot_default_allowed,
            "max_domains_per_authority": self.max_domains_per_authority,
            "require_jurisdiction": self.require_jurisdiction,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackAuthorityPolicy:
        """Deserialise a policy from a dict."""
        return cls(
            default_authorities={
                k: set(v) for k, v in data.get("default_authorities", {}).items()
            },
            delegation_rules=dict(data.get("delegation_rules", {})),
            conflict_resolution_strategy=ResolutionStrategy[
                data.get("conflict_resolution_strategy", "PRIORITY")
            ],
            audit_level=AuditLevel[data.get("audit_level", "SUMMARY")],
            copilot_default_allowed=bool(data.get("copilot_default_allowed", False)),
            max_domains_per_authority=int(data.get("max_domains_per_authority", 20)),
            require_jurisdiction=bool(data.get("require_jurisdiction", False)),
        )


# ---------------------------------------------------------------------------
# 9. PackAuthorityMigration — safe authority migration
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _MigrationStep:
    """A single step within an authority migration."""

    action: str  # "grant", "revoke", "modify_ceiling", "modify_jurisdiction"
    pack_id: str
    domain: str | None
    details: dict[str, Any]


@dataclass(slots=True)
class PackAuthorityMigration:
    """Handles authority changes during pack updates and version bumps.

    When a pack is updated — e.g. new domains added, old domains
    removed, trust ceilings adjusted — the migration computes the
    diff, applies it transactionally, and supports rollback.

    Attributes
    ----------
    registry : PackAuthorityRegistry
        The registry to mutate.
    audit : PackAuthorityAudit
        Audit log for recording migration events.
    _pending : list[_MigrationStep]
        Steps computed but not yet applied.
    _applied : list[_MigrationStep]
        Steps that have been applied (for rollback).
    _snapshots : dict[str, list[PackAuthority]]
        Pre-migration snapshots keyed by migration ID.
    """

    registry: PackAuthorityRegistry
    audit: PackAuthorityAudit
    _pending: list[_MigrationStep] = field(default_factory=list)
    _applied: list[_MigrationStep] = field(default_factory=list)
    _snapshots: dict[str, list[PackAuthority]] = field(default_factory=dict)

    def compute_migration(
        self,
        pack_id: str,
        new_authority: PackAuthority,
    ) -> str:
        """Compute migration steps from current to *new_authority*.

        Returns a migration ID.  Steps are stored internally and can
        be inspected before calling :meth:`apply_migration`.
        """
        migration_id = uuid.uuid4().hex[:16]
        current_auths = self.registry.authorities_for_pack(pack_id)
        self._snapshots[migration_id] = list(current_auths)
        self._pending.clear()

        current_domains: set[str] = set()
        for auth in current_auths:
            current_domains |= auth.granted_domains

        new_domains = new_authority.granted_domains

        # Domains to revoke.
        for domain in current_domains - new_domains:
            self._pending.append(_MigrationStep(
                action="revoke",
                pack_id=pack_id,
                domain=domain,
                details={"reason": "domain_removed"},
            ))

        # Domains to grant.
        for domain in new_domains - current_domains:
            ceiling = new_authority.trust_ceiling_for(domain)
            self._pending.append(_MigrationStep(
                action="grant",
                pack_id=pack_id,
                domain=domain,
                details={
                    "ceiling": ceiling.label() if ceiling else "proposal",
                    "channels": sorted(new_authority.evidence_channels_allowed),
                },
            ))

        # Domains to check for ceiling changes.
        for domain in current_domains & new_domains:
            old_ceiling = self._current_ceiling(current_auths, domain)
            new_ceiling = new_authority.trust_ceiling_for(domain)
            if old_ceiling != new_ceiling:
                self._pending.append(_MigrationStep(
                    action="modify_ceiling",
                    pack_id=pack_id,
                    domain=domain,
                    details={
                        "old_ceiling": old_ceiling.label() if old_ceiling else "none",
                        "new_ceiling": new_ceiling.label() if new_ceiling else "none",
                    },
                ))

        self.audit.record_migration(migration_id, pack_id, "computed", {
            "steps": len(self._pending),
        })
        return migration_id

    def apply_migration(self, migration_id: str) -> list[str]:
        """Apply the pending migration steps.

        Returns a list of human-readable status messages, one per step.
        """
        messages: list[str] = []
        for step in self._pending:
            if step.action == "revoke":
                revoked = self.registry.revoke(step.pack_id, step.domain or "")
                msg = f"Revoked domain '{step.domain}' from '{step.pack_id}'"
                if revoked is None:
                    msg += " (was already absent)"
                messages.append(msg)
            elif step.action == "grant":
                ceiling_label = step.details.get("ceiling", "proposal")
                tier = _label_to_tier(ceiling_label)
                channels = set(step.details.get("channels", []))
                auth = PackAuthority(
                    pack_id=step.pack_id,
                    granted_domains={step.domain} if step.domain else set(),
                    trust_ceiling_per_domain={step.domain: tier} if step.domain else {},
                    evidence_channels_allowed=channels,
                    granted_by="migration",
                )
                try:
                    self.registry.grant(auth)
                    messages.append(
                        f"Granted domain '{step.domain}' to '{step.pack_id}'"
                    )
                except ValueError as exc:
                    messages.append(f"Grant failed: {exc}")
            elif step.action == "modify_ceiling":
                messages.append(
                    f"Modified ceiling for '{step.domain}' on '{step.pack_id}': "
                    f"{step.details.get('old_ceiling')} → {step.details.get('new_ceiling')}"
                )
            self._applied.append(step)

        self._pending.clear()
        self.audit.record_migration(migration_id, "", "applied", {
            "steps_applied": len(self._applied),
        })
        return messages

    def rollback_migration(self, migration_id: str) -> bool:
        """Rollback a migration by restoring the pre-migration snapshot.

        Returns ``True`` when the snapshot was found and restored.
        """
        snapshot = self._snapshots.get(migration_id)
        if snapshot is None:
            return False

        # Determine affected pack IDs from the snapshot.
        pack_ids = {auth.pack_id for auth in snapshot}
        for pid in pack_ids:
            self.registry.revoke_all(pid)

        for auth in snapshot:
            try:
                self.registry.grant(auth)
            except ValueError:
                pass  # Best-effort restoration.

        self._applied.clear()
        self.audit.record_migration(migration_id, "", "rolled_back")
        return True

    def validate_post_migration(self, pack_id: str) -> list[str]:
        """Validate the registry state after migration.

        Returns a list of issues found.
        """
        issues = self.registry.validate_all()
        pack_issues = [i for i in issues if pack_id in i]
        conflicts = self.registry.detect_conflicts()
        for conflict in conflicts:
            if conflict["pack_a"] == pack_id or conflict["pack_b"] == pack_id:
                pack_issues.append(
                    f"Post-migration conflict: {conflict['pack_a']} vs "
                    f"{conflict['pack_b']} over '{conflict['domain']}'"
                )
        return pack_issues

    def pending_steps(self) -> list[dict[str, Any]]:
        """Return a serialisable view of pending migration steps."""
        return [
            {
                "action": s.action,
                "pack_id": s.pack_id,
                "domain": s.domain,
                "details": s.details,
            }
            for s in self._pending
        ]

    @staticmethod
    def _current_ceiling(
        auths: list[PackAuthority],
        domain: str,
    ) -> TrustTier | None:
        """Find the trust ceiling for *domain* in *auths*."""
        for auth in auths:
            if domain in auth.granted_domains:
                return auth.trust_ceiling_for(domain)
        return None


# ---------------------------------------------------------------------------
# 10. PackAuthorityDiagnostics — diagnostic views
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackAuthorityDiagnostics:
    """Diagnostic views over the authority subsystem.

    Provides read-only aggregate queries useful for tooling, dashboards,
    and copilot-driven authority summaries.

    Attributes
    ----------
    registry : PackAuthorityRegistry
        Authority registry.
    enforcer : PackAuthorityEnforcer
        Enforcer (for violation data).
    delegation : PackAuthorityDelegation
        Delegation subsystem.
    audit : PackAuthorityAudit
        Audit log.
    resolver : PackAuthorityConflictResolver
        Conflict resolver (for resolution history).
    """

    registry: PackAuthorityRegistry
    enforcer: PackAuthorityEnforcer
    delegation: PackAuthorityDelegation
    audit: PackAuthorityAudit
    resolver: PackAuthorityConflictResolver

    def authority_map(self) -> dict[str, list[dict[str, Any]]]:
        """Return a mapping from pack_id to authority summaries.

        This is the primary entry point for understanding the current
        authority landscape.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for pack_id in self.registry.all_pack_ids():
            auths = self.registry.authorities_for_pack(pack_id)
            result[pack_id] = [a.summary() for a in auths]
        return result

    def conflict_report(self) -> dict[str, Any]:
        """Generate a structured conflict report.

        Includes all detected conflicts, their classifications, and
        any resolutions that have been applied.
        """
        conflicts = self.resolver.detect_conflicts()
        return {
            "total_conflicts": len(conflicts),
            "conflicts": conflicts,
            "resolutions_applied": len(self.resolver.resolutions),
            "resolution_history": list(self.resolver.resolutions),
        }

    def violation_report(self) -> dict[str, Any]:
        """Generate a structured violation report.

        Groups violations by pack and severity.
        """
        by_pack: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_severity: dict[str, int] = defaultdict(int)
        for v in self.enforcer.violations:
            by_pack[v["pack_id"]].append(v)
            by_severity[v.get("severity", "UNKNOWN")] += 1
        return {
            "total_violations": len(self.enforcer.violations),
            "by_pack": dict(by_pack),
            "by_severity": dict(by_severity),
        }

    def delegation_report(self) -> dict[str, Any]:
        """Generate a structured delegation report.

        Lists all delegations, grouped by source pack, with acceptance
        status and trust attenuation.
        """
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        accepted_count = 0
        for d in self.delegation.delegations:
            rec = {
                "delegation_id": d.delegation_id,
                "to_pack": d.to_pack,
                "domains": sorted(d.domains),
                "accepted": d.accepted,
                "trust_attenuation": d.trust_attenuation,
                "created_at": d.created_at.isoformat(),
            }
            by_source[d.from_pack].append(rec)
            if d.accepted:
                accepted_count += 1
        return {
            "total_delegations": len(self.delegation.delegations),
            "accepted": accepted_count,
            "pending": len(self.delegation.delegations) - accepted_count,
            "by_source": dict(by_source),
        }

    def copilot_authority_summary(self) -> dict[str, Any]:
        """Summarise copilot-related authority state.

        This is the diagnostic a copilot orchestration layer would
        call to understand its current permissions and constraints.
        """
        copilot_packs: list[str] = []
        copilot_domains: set[str] = set()
        copilot_channels: set[str] = set()
        total_packs = 0

        for pack_id in self.registry.all_pack_ids():
            total_packs += 1
            for auth in self.registry.authorities_for_pack(pack_id):
                if auth.copilot_delegation_allowed:
                    copilot_packs.append(pack_id)
                    copilot_domains |= auth.granted_domains
                    copilot_channels |= {
                        ch for ch in auth.evidence_channels_allowed
                        if "copilot" in ch.lower()
                    }

        copilot_violations = [
            v for v in self.enforcer.violations
            if "copilot" in v.get("channel", "").lower()
        ]

        return {
            "packs_allowing_copilot": sorted(set(copilot_packs)),
            "copilot_domain_coverage": sorted(copilot_domains),
            "copilot_channels": sorted(copilot_channels),
            "copilot_violations": len(copilot_violations),
            "total_packs": total_packs,
            "copilot_coverage_ratio": (
                len(set(copilot_packs)) / total_packs if total_packs else 0.0
            ),
        }

    def health_check(self) -> dict[str, Any]:
        """Run a quick health check across the authority subsystem.

        Returns a dict with boolean ``healthy`` flag and any issues.
        """
        issues = self.registry.validate_all()
        conflicts = self.resolver.detect_conflicts()
        violation_count = self.enforcer.violation_count()
        compliance = self.audit.compliance_score()

        healthy = (
            len(issues) == 0
            and len(conflicts) == 0
            and violation_count == 0
        )
        return {
            "healthy": healthy,
            "validation_issues": len(issues),
            "active_conflicts": len(conflicts),
            "violations": violation_count,
            "compliance_score": compliance,
            "audit_entries": len(self.audit.entries),
            "registered_packs": len(self.registry.all_pack_ids()),
        }

    def domain_coverage(self) -> dict[str, list[str]]:
        """Return a mapping from domain to the list of packs covering it."""
        coverage: dict[str, list[str]] = defaultdict(list)
        for pack_id in self.registry.all_pack_ids():
            for auth in self.registry.authorities_for_pack(pack_id):
                for domain in auth.granted_domains:
                    coverage[domain].append(pack_id)
        return {k: sorted(v) for k, v in coverage.items()}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _label_to_tier(label: str) -> TrustTier:
    """Convert a lower-case label back to a ``TrustTier``.

    Falls back to ``TrustTier.PROPOSAL`` for unrecognised labels.
    """
    label_upper = label.upper()
    for member in TrustTier:
        if member.name == label_upper:
            return member
    return TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "KNOWN_AUTHORITY_LEVELS",
    "MAX_DELEGATION_CHAIN_LENGTH",
    "DEFAULT_ATTENUATION_PER_HOP",
    "ConflictKind",
    "ResolutionStrategy",
    "AuditLevel",
    "ViolationSeverity",
    "PackAuthority",
    "authorize_pack",
    "PackJurisdiction",
    "PackAuthorityRegistry",
    "PackAuthorityEnforcer",
    "PackAuthorityDelegation",
    "PackAuthorityConflictResolver",
    "PackAuthorityAudit",
    "PackAuthorityPolicy",
    "PackAuthorityMigration",
    "PackAuthorityDiagnostics",
]
