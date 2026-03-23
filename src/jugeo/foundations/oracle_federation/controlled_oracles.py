from __future__ import annotations
"""Section 7.1 — Controlled Oracle Model (Theory2.tex Ch7).

An oracle in the JuGeo framework is a trust-bounded channel that may *propose*
evidence but cannot self-promote its trust level.  The central invariant is the
**trust ceiling**: every oracle is registered with a hard ceiling (default
``TrustLevel.ORACLE_PROPOSED``) and the ``TrustCeilingEnforcer`` ensures no
response from that oracle can exceed the ceiling regardless of what the oracle
claims.

§7.1 of Theory2.tex defines:

* **Controlled oracle** — an evidence source constrained to a declared
  jurisdiction and trust ceiling.  Proposals are first-class records: they
  carry their ceiling, their proposal-id, the originating channel, and a
  timestamp.  Revocation marks a proposal as invalidated without deleting it.

* **Trust ceiling enforcement** — the ``TrustCeilingEnforcer`` acts as a
  policy guard at the boundary between oracle output and the evidence pool.
  Any response whose claimed trust_level exceeds the registered ceiling is
  silently clamped and the violation is logged.

* **Corroboration** — a proposal may gain strength only through *external*
  corroboration: a higher-tier channel confirms the proposal, and the record
  is updated.  The oracle itself cannot supply the corroborating evidence.

* **Jurisdiction** — an ``OracleJurisdiction`` declares the domains the
  oracle is authorised to address.  Requests outside the jurisdiction are
  rejected before they reach the oracle.

* **Copilot channel** — ``CopilotOracleChannel`` is a specialized oracle
  whose trust ceiling is ``TrustLevel.COPILOT_SUGGESTED`` by default,
  slightly below ``ORACLE_PROPOSED``.  Copilot proposals enter the evidence
  pool at this tier and require explicit external corroboration before they
  can be promoted.  The channel exposes ``validate_no_self_promotion()`` to
  assert this invariant programmatically.

Theory alignment
----------------
- Theory2.tex §7.1.1 defines the oracle model and trust ceiling.
- Theory2.tex §7.1.2 defines jurisdiction and operation allowlists.
- Theory2.tex §7.1.3 defines the corroboration protocol.
- Theory2.tex §7.1.4 defines the copilot channel specifically.
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    from jugeo.evidence.channels import (
        EvidenceChannel,
        ChannelJurisdiction,
        EvidenceRequest,
        EvidenceResponse,
    )
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    ChannelJurisdiction = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust rank table (Theory2.tex §7.1.1)
# Maps trust level names (lowercase) to ordinal ranks used for ceiling
# comparisons.  Legacy TrustTier names are also included for compatibility.
# ---------------------------------------------------------------------------
_TRUST_RANK: dict[str, int] = {
    "contradicted": 0,
    "unverified": 1,
    "copilot_suggested": 2,
    "oracle_proposed": 3,
    "human_attested": 4,
    "runtime_witnessed": 5,
    "solver_discharged": 6,
    "mechanically_verified": 7,
    # Legacy TrustTier names
    "proposal": 1,
    "reviewed": 3,
    "verified": 5,
}


def _rank(level: str) -> int:
    """Return the ordinal rank for *level*, defaulting to 0 for unknown names."""
    return _TRUST_RANK.get(level.lower(), 0)


# ---------------------------------------------------------------------------
# OracleProposalRecord
# ---------------------------------------------------------------------------


@dataclass
class OracleProposalRecord:
    """A first-class record of a single oracle proposal (Theory2.tex §7.1.1).

    Each proposal is immutably identified by ``proposal_id`` and carries the
    trust level at which it was created together with the ceiling that was
    applied at creation time.  Revocation does not delete the record; it sets
    ``revoked=True`` and records a human-readable ``revocation_reason`` so that
    the audit trail remains intact.

    Corroboration is additive: every external source that independently
    confirms the proposal appends its identifier to ``corroborated_by``.  The
    oracle itself must never appear in this list (enforced by convention and
    asserted in ``CopilotOracleChannel.validate_no_self_promotion``).
    """

    proposal_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    oracle_id: str = ""
    request_summary: str = ""
    response_summary: str = ""
    trust_at_creation: str = "oracle_proposed"
    ceiling_applied: str = "oracle_proposed"
    corroborated_by: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    revoked: bool = False
    revocation_reason: str = ""
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def corroborate(self, source_id: str, trust_level: str) -> None:
        """Record external corroboration from *source_id* at *trust_level*.

        Implements the corroboration step in Theory2.tex §7.1.3.  The
        ``source_id`` is appended to ``corroborated_by`` and the event is
        logged at INFO level so that audit trails can be reconstructed from
        structured logs alone.

        Args:
            source_id: Identifier of the external source providing
                corroboration.  Must not equal ``self.oracle_id`` (the oracle
                cannot corroborate its own proposal).
            trust_level: The trust level claimed by the corroborating source.
                Stored in metadata but not automatically applied — promotion
                requires a separate policy decision.
        """
        if source_id == self.oracle_id:
            logger.warning(
                "Self-corroboration attempted for proposal %s by oracle %s — ignored",
                self.proposal_id,
                self.oracle_id,
            )
            return
        if source_id not in self.corroborated_by:
            self.corroborated_by.append(source_id)
            self.metadata.setdefault("corroboration_levels", {})[source_id] = trust_level
            logger.info(
                "Proposal %s corroborated by %s at level %s (total corroborations: %d)",
                self.proposal_id,
                source_id,
                trust_level,
                len(self.corroborated_by),
            )
        else:
            logger.debug(
                "Duplicate corroboration from %s for proposal %s — skipped",
                source_id,
                self.proposal_id,
            )

    def revoke(self, reason: str) -> None:
        """Revoke this proposal with a human-readable *reason*.

        Revocation is final: once ``revoked=True`` the proposal cannot be
        re-activated.  The record is preserved so that downstream consumers
        can detect revocation.

        Args:
            reason: Free-text explanation of why the proposal was revoked.
        """
        if self.revoked:
            logger.debug("Proposal %s already revoked; ignoring duplicate revoke call", self.proposal_id)
            return
        self.revoked = True
        self.revocation_reason = reason
        self.metadata["revoked_at"] = time.time()
        logger.info("Proposal %s revoked: %s", self.proposal_id, reason)

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return ``True`` iff the proposal has not been revoked."""
        return not self.revoked

    def corroboration_count(self) -> int:
        """Return the number of distinct external corroborating sources."""
        return len(self.corroborated_by)

    def age_seconds(self) -> float:
        """Return the age of this proposal in seconds since creation."""
        return time.time() - self.timestamp

    def summary(self) -> str:
        """Return a human-readable one-line summary suitable for log output."""
        status = "REVOKED" if self.revoked else "active"
        return (
            f"Proposal[{self.proposal_id}] oracle={self.oracle_id} "
            f"ceiling={self.ceiling_applied} corroborations={self.corroboration_count()} "
            f"status={status} age={self.age_seconds():.1f}s"
        )

    def to_dict(self) -> dict:
        """Serialize the proposal record to a plain dictionary.

        The dictionary is suitable for JSON serialisation and for storage in
        the evidence pool's audit log.
        """
        return {
            "proposal_id": self.proposal_id,
            "oracle_id": self.oracle_id,
            "request_summary": self.request_summary,
            "response_summary": self.response_summary,
            "trust_at_creation": self.trust_at_creation,
            "ceiling_applied": self.ceiling_applied,
            "corroborated_by": list(self.corroborated_by),
            "timestamp": self.timestamp,
            "revoked": self.revoked,
            "revocation_reason": self.revocation_reason,
            "metadata": dict(self.metadata),
            "age_seconds": self.age_seconds(),
        }


# ---------------------------------------------------------------------------
# OracleJurisdiction
# ---------------------------------------------------------------------------


class OracleJurisdiction:
    """Declares the domains an oracle is authorised to address (Theory2.tex §7.1.2).

    A jurisdiction is a named *scope* paired with an allowlist of domain
    strings.  When a request arrives, ``check_request`` tests whether the
    request kind falls within the allowlist using exact match, prefix match,
    or substring match (in that order of precedence).

    The jurisdiction also carries a *trust ceiling*: any response from a
    channel operating under this jurisdiction is clamped to that ceiling via
    ``enforce``.  When two jurisdictions are composed (intersected), the
    resulting jurisdiction adopts the more restrictive of the two ceilings.

    Args:
        scope: A human-readable identifier for the jurisdiction, e.g.
            ``"arithmetic_facts"`` or ``"type_inference"``.
        allowed_domains: Initial list of domain strings in the allowlist.
        trust_ceiling: The maximum trust level permitted for responses under
            this jurisdiction.  Defaults to ``"oracle_proposed"``.
        parent: Optional identifier of a parent jurisdiction from which this
            one was derived.  Informational only.
        constraints: Optional dict of additional policy constraints, e.g.
            ``{"max_response_size_bytes": 4096}``.
    """

    def __init__(
        self,
        scope: str,
        allowed_domains: list[str],
        trust_ceiling: str = "oracle_proposed",
        parent: str | None = None,
        constraints: dict | None = None,
    ) -> None:
        self.scope: str = scope
        self.allowed_domains: set[str] = set(allowed_domains)
        self.trust_ceiling: str = trust_ceiling.lower()
        self.parent: str | None = parent
        self.constraints: dict = constraints or {}
        self._predicate_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def check_request(self, request_kind: str) -> bool:
        """Return ``True`` if *request_kind* falls within this jurisdiction.

        Matching is attempted in three stages (Theory2.tex §7.1.2):
        1. Exact match against the allowlist.
        2. Prefix match: the request kind starts with an allowlisted domain.
        3. Substring match: the request kind contains an allowlisted domain.

        Results are cached to avoid repeated linear scans for hot paths.

        Args:
            request_kind: A string identifying the type of request, e.g.
                ``"arithmetic.addition"`` or ``"type_check"``.
        """
        if request_kind in self._predicate_cache:
            return self._predicate_cache[request_kind]

        rk = request_kind.lower()

        # Stage 1: exact
        if rk in {d.lower() for d in self.allowed_domains}:
            self._predicate_cache[request_kind] = True
            return True

        # Stage 2: prefix
        for domain in self.allowed_domains:
            dl = domain.lower()
            if rk.startswith(dl) or dl.startswith(rk):
                self._predicate_cache[request_kind] = True
                return True

        # Stage 3: substring
        for domain in self.allowed_domains:
            if domain.lower() in rk or rk in domain.lower():
                self._predicate_cache[request_kind] = True
                return True

        self._predicate_cache[request_kind] = False
        return False

    def enforce(self, response_dict: dict) -> dict:
        """Clamp the trust level in *response_dict* to ``self.trust_ceiling``.

        If the response's ``trust_level`` exceeds the jurisdiction's ceiling,
        it is replaced with the ceiling value and ``jurisdiction_clamped`` is
        set to ``True`` in the returned dict.  A deep copy is returned so that
        the original dict is not mutated.

        Args:
            response_dict: A response dictionary as returned by an oracle
                backend.  Expected to have a ``"trust_level"`` key.
        """
        result = dict(response_dict)
        claimed = result.get("trust_level", "unverified")
        if _rank(str(claimed)) > _rank(self.trust_ceiling):
            logger.debug(
                "Jurisdiction '%s' clamping trust %s -> %s",
                self.scope,
                claimed,
                self.trust_ceiling,
            )
            result["trust_level"] = self.trust_ceiling
            result["jurisdiction_clamped"] = True
        return result

    def compose(self, other: OracleJurisdiction) -> OracleJurisdiction:
        """Return a new jurisdiction that is the intersection of *self* and *other*.

        The composed jurisdiction's:
        - ``allowed_domains`` is the intersection of both domain sets.
        - ``trust_ceiling`` is the minimum (more restrictive) of the two.
        - ``scope`` is a compound name reflecting both scopes.
        - ``parent`` is set to ``self.scope``.

        Args:
            other: The other jurisdiction to compose with.
        """
        intersected_domains = list(self.allowed_domains & other.allowed_domains)
        composed_ceiling = (
            self.trust_ceiling
            if _rank(self.trust_ceiling) <= _rank(other.trust_ceiling)
            else other.trust_ceiling
        )
        composed_constraints = {**self.constraints, **other.constraints}
        return OracleJurisdiction(
            scope=f"{self.scope}∩{other.scope}",
            allowed_domains=intersected_domains,
            trust_ceiling=composed_ceiling,
            parent=self.scope,
            constraints=composed_constraints,
        )

    def is_valid(self) -> bool:
        """Return ``True`` iff the jurisdiction is structurally valid.

        Validity requires:
        - ``scope`` is a non-empty string.
        - ``allowed_domains`` is non-empty.
        - ``trust_ceiling`` names a recognised trust level.
        """
        if not self.scope or not self.scope.strip():
            return False
        if not self.allowed_domains:
            return False
        if self.trust_ceiling not in _TRUST_RANK:
            return False
        return True

    def to_predicate(self) -> Callable[[str], bool]:
        """Return a closure that tests whether a domain string is in this jurisdiction.

        The returned callable is stateless with respect to the jurisdiction
        object and can be passed to higher-order functions without retaining a
        reference to the jurisdiction.
        """
        frozen_domains = frozenset(d.lower() for d in self.allowed_domains)

        def predicate(domain: str) -> bool:
            dl = domain.lower()
            if dl in frozen_domains:
                return True
            for fd in frozen_domains:
                if dl.startswith(fd) or fd.startswith(dl):
                    return True
            return False

        return predicate

    def add_domain(self, domain: str) -> None:
        """Add *domain* to the allowlist, invalidating the predicate cache."""
        self.allowed_domains.add(domain)
        self._predicate_cache.clear()
        logger.debug("Jurisdiction '%s': added domain '%s'", self.scope, domain)

    def remove_domain(self, domain: str) -> None:
        """Remove *domain* from the allowlist, invalidating the predicate cache."""
        self.allowed_domains.discard(domain)
        self._predicate_cache.clear()
        logger.debug("Jurisdiction '%s': removed domain '%s'", self.scope, domain)

    def describe(self) -> str:
        """Return a multi-line human-readable description of this jurisdiction."""
        lines = [
            f"OracleJurisdiction: {self.scope}",
            f"  trust_ceiling : {self.trust_ceiling}",
            f"  parent        : {self.parent or '(none)'}",
            f"  domains ({len(self.allowed_domains)}): " + ", ".join(sorted(self.allowed_domains)),
        ]
        if self.constraints:
            lines.append(f"  constraints   : {self.constraints}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize the jurisdiction to a plain dictionary."""
        return {
            "scope": self.scope,
            "allowed_domains": sorted(self.allowed_domains),
            "trust_ceiling": self.trust_ceiling,
            "parent": self.parent,
            "constraints": dict(self.constraints),
        }


# ---------------------------------------------------------------------------
# TrustCeilingEnforcer
# ---------------------------------------------------------------------------


class TrustCeilingEnforcer:
    """Policy guard that clamps oracle responses to registered ceilings (Theory2.tex §7.1.1).

    The enforcer maintains a ``ceiling_map`` keyed by ``channel_id`` and
    records every instance where a response's claimed trust level exceeded the
    registered ceiling in ``violation_log``.  The enforcer is deliberately
    stateless with respect to the evidence pool — it only operates on response
    dictionaries.

    Typical usage::

        enforcer = TrustCeilingEnforcer()
        enforcer.register_ceiling("my_oracle", "oracle_proposed")
        safe_response = enforcer.enforce("my_oracle", raw_response)
    """

    def __init__(self) -> None:
        self.ceiling_map: dict[str, str] = {}
        self.violation_log: list[dict] = []
        self._enforcement_count: int = 0

    def register_ceiling(self, channel_id: str, ceiling_level: str) -> None:
        """Register a trust ceiling for *channel_id*.

        If *channel_id* was already registered, the ceiling is updated and the
        change is logged at WARNING level (unexpected reconfiguration).

        Args:
            channel_id: Unique identifier for the oracle channel.
            ceiling_level: The maximum trust level this channel's responses
                may claim.  Must be a key in ``_TRUST_RANK``.
        """
        normalised = ceiling_level.lower()
        if channel_id in self.ceiling_map:
            logger.warning(
                "TrustCeilingEnforcer: re-registering ceiling for '%s': %s -> %s",
                channel_id,
                self.ceiling_map[channel_id],
                normalised,
            )
        else:
            logger.info(
                "TrustCeilingEnforcer: registered ceiling '%s' for channel '%s'",
                normalised,
                channel_id,
            )
        self.ceiling_map[channel_id] = normalised

    def enforce(self, channel_id: str, response_dict: dict) -> dict:
        """Clamp *response_dict*'s trust level to the ceiling registered for *channel_id*.

        If no ceiling is registered for *channel_id*, a default ceiling of
        ``"oracle_proposed"`` is applied and a warning is emitted.

        Args:
            channel_id: The channel whose ceiling should be applied.
            response_dict: Mutable-style dict returned by the oracle backend.

        Returns:
            A new dict (the original is not mutated) with the trust level
            clamped if necessary.
        """
        self._enforcement_count += 1
        ceiling = self.ceiling_map.get(channel_id)
        if ceiling is None:
            logger.warning(
                "TrustCeilingEnforcer: no ceiling registered for '%s'; defaulting to oracle_proposed",
                channel_id,
            )
            ceiling = "oracle_proposed"

        result = dict(response_dict)
        claimed = str(result.get("trust_level", "unverified")).lower()
        if _rank(claimed) > _rank(ceiling):
            request_id = str(result.get("request_id", "unknown"))
            self.log_violation(channel_id, claimed, ceiling, request_id)
            result["trust_level"] = ceiling
            result.setdefault("metadata", {})["ceiling_clamped"] = True
            result["metadata"]["original_claimed_level"] = claimed
        return result

    def log_violation(
        self,
        channel_id: str,
        claimed_level: str,
        enforced_level: str,
        request_id: str,
    ) -> None:
        """Append a violation record to ``violation_log``.

        Args:
            channel_id: The offending channel.
            claimed_level: The trust level claimed by the oracle response.
            enforced_level: The level that was actually applied after clamping.
            request_id: The request that triggered the violation.
        """
        record = {
            "channel_id": channel_id,
            "claimed_level": claimed_level,
            "enforced_level": enforced_level,
            "request_id": request_id,
            "timestamp": time.time(),
        }
        self.violation_log.append(record)
        logger.warning(
            "TrustCeilingEnforcer: VIOLATION channel='%s' claimed='%s' enforced='%s' request='%s'",
            channel_id,
            claimed_level,
            enforced_level,
            request_id,
        )

    def get_violation_summary(self) -> dict:
        """Return a summary of violations grouped by channel_id.

        The summary maps each channel_id to a dict containing:
        - ``count``: total violation count for that channel.
        - ``recent``: the most recent violation record for that channel.
        """
        summary: dict[str, dict] = {}
        for v in self.violation_log:
            cid = v["channel_id"]
            if cid not in summary:
                summary[cid] = {"count": 0, "recent": None}
            summary[cid]["count"] += 1
            summary[cid]["recent"] = v
        return summary

    def reset_violations(self) -> None:
        """Clear the violation log (e.g., after a compliance audit cycle)."""
        n = len(self.violation_log)
        self.violation_log.clear()
        logger.info("TrustCeilingEnforcer: cleared %d violation records", n)

    def audit_all_channels(self) -> list[dict]:
        """Return a list of dicts describing each registered channel and its ceiling.

        Each entry contains ``channel_id``, ``ceiling``, ``ceiling_rank``, and
        ``violation_count`` derived from the current violation log.
        """
        violation_counts: dict[str, int] = {}
        for v in self.violation_log:
            violation_counts[v["channel_id"]] = violation_counts.get(v["channel_id"], 0) + 1

        return [
            {
                "channel_id": cid,
                "ceiling": ceiling,
                "ceiling_rank": _rank(ceiling),
                "violation_count": violation_counts.get(cid, 0),
            }
            for cid, ceiling in sorted(self.ceiling_map.items())
        ]

    def is_compliant(self, channel_id: str) -> bool:
        """Return ``True`` if *channel_id* has no violations in the last 100 log entries.

        A channel with no registered ceiling is considered non-compliant
        because it has never been properly configured.
        """
        if channel_id not in self.ceiling_map:
            return False
        recent_100 = self.violation_log[-100:]
        return not any(v["channel_id"] == channel_id for v in recent_100)

    def get_ceiling(self, channel_id: str) -> str | None:
        """Return the registered ceiling for *channel_id*, or ``None`` if unknown."""
        return self.ceiling_map.get(channel_id)


# ---------------------------------------------------------------------------
# OracleChannel
# ---------------------------------------------------------------------------


class OracleChannel:
    """A trust-bounded oracle channel (Theory2.tex §7.1.1 and §7.1.2).

    ``OracleChannel`` is the primary abstraction for evidence-producing backends
    in the JuGeo federation.  Each channel:

    - Has a unique ``oracle_id`` (auto-generated if not provided).
    - Operates within an optional ``OracleJurisdiction`` that constrains the
      domain of requests it may handle.
    - Has a hard ``trust_ceiling`` enforced by an internal
      ``TrustCeilingEnforcer``.
    - Accumulates ``OracleProposalRecord`` objects for every proposal it makes,
      supporting revocation and corroboration.
    - Maintains an append-only ``audit_log`` for compliance and debugging.

    Args:
        oracle_id: Unique identifier for this channel.  A random hex string is
            generated if ``None`` is provided.
        name: Human-readable name, e.g. ``"z3_solver"`` or ``"copilot"``.
        channel_kind: Categorical tag, e.g. ``"solver"``, ``"llm"``, ``"db"``.
        jurisdiction: Optional ``OracleJurisdiction``.  If provided, requests
            outside the jurisdiction are rejected in ``propose``.
        trust_ceiling: The maximum trust level any response from this channel
            may carry.  Defaults to ``"oracle_proposed"``.
        config: Arbitrary channel-specific configuration dictionary.
    """

    def __init__(
        self,
        oracle_id: str | None = None,
        name: str = "oracle",
        channel_kind: str = "oracle",
        jurisdiction: OracleJurisdiction | None = None,
        trust_ceiling: str = "oracle_proposed",
        config: dict | None = None,
    ) -> None:
        self.oracle_id: str = oracle_id or uuid.uuid4().hex[:16]
        self.name: str = name
        self.channel_kind: str = channel_kind
        self.jurisdiction: OracleJurisdiction | None = jurisdiction
        self.trust_ceiling: str = trust_ceiling.lower()
        self.active_proposals: dict[str, OracleProposalRecord] = {}
        self.audit_log: list[dict] = []
        self.config: dict = config or {}
        self._enforcer: TrustCeilingEnforcer = TrustCeilingEnforcer()
        self._proposal_counter: int = 0
        self._revoked_proposals: dict[str, OracleProposalRecord] = {}

        # Pre-register ceiling with internal enforcer
        self._enforcer.register_ceiling(self.oracle_id, self.trust_ceiling)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def propose(self, request: Any) -> dict:
        """Produce a proposal in response to *request* and return a response dict.

        If a live ``EvidenceRequest`` object is available (jugeo imports
        succeeded), the proposition and coordinate are extracted from it.
        Otherwise the request is coerced to a string summary.

        The trust ceiling is applied via the internal enforcer before the
        proposal record is stored, ensuring the stored record already reflects
        the clamped level.

        Args:
            request: An ``EvidenceRequest`` or any object whose string
                representation can serve as a request summary.

        Returns:
            A response dict with keys ``request_id``, ``evidence_item``,
            ``trust_level``, ``provenance``, ``is_partial``, and ``metadata``.
        """
        # Extract request fields if possible
        if EvidenceRequest is not None and isinstance(request, EvidenceRequest):
            request_id = request.request_id
            proposition = request.proposition
            req_kind = getattr(request, "required_kind", "")
            request_summary = f"{req_kind}:{proposition}"
        elif isinstance(request, dict):
            request_id = request.get("request_id", uuid.uuid4().hex[:16])
            proposition = request.get("proposition", str(request))
            req_kind = request.get("required_kind", "")
            request_summary = f"{req_kind}:{proposition}"
        else:
            request_id = uuid.uuid4().hex[:16]
            proposition = str(request)
            req_kind = ""
            request_summary = proposition[:120]

        # Jurisdiction check
        if self.jurisdiction is not None and req_kind:
            if not self.jurisdiction.check_request(req_kind):
                logger.warning(
                    "Oracle '%s': request kind '%s' outside jurisdiction '%s'",
                    self.oracle_id,
                    req_kind,
                    self.jurisdiction.scope,
                )
                return {
                    "request_id": request_id,
                    "trust_level": "unverified",
                    "evidence_item": {},
                    "provenance": (self.oracle_id,),
                    "is_partial": True,
                    "metadata": {"rejected_by": "jurisdiction"},
                }

        # Build the initial response dict
        self._proposal_counter += 1
        response_summary = f"oracle_response_{self._proposal_counter}"
        raw_response: dict = {
            "request_id": request_id,
            "evidence_item": {
                "proposition": proposition,
                "oracle_id": self.oracle_id,
                "channel_kind": self.channel_kind,
                "proposal_index": self._proposal_counter,
                "fingerprint": hashlib.sha256(
                    f"{self.oracle_id}:{proposition}:{self._proposal_counter}".encode()
                ).hexdigest()[:16],
            },
            "trust_level": self.trust_ceiling,
            "provenance": (self.oracle_id, self.name),
            "is_partial": False,
            "metadata": {
                "channel": self.name,
                "oracle_id": self.oracle_id,
            },
        }

        # Apply trust ceiling enforcement
        safe_response = self._enforcer.enforce(self.oracle_id, raw_response)
        safe_response = self.enforce_ceiling(safe_response)

        # Create and store proposal record
        record = OracleProposalRecord(
            oracle_id=self.oracle_id,
            request_summary=request_summary,
            response_summary=response_summary,
            trust_at_creation=safe_response["trust_level"],
            ceiling_applied=self.trust_ceiling,
        )
        self.active_proposals[record.proposal_id] = record
        safe_response["metadata"]["proposal_id"] = record.proposal_id

        self._record_audit("propose", {
            "request_id": request_id,
            "proposal_id": record.proposal_id,
            "trust_level": safe_response["trust_level"],
        })
        logger.info(
            "Oracle '%s' produced proposal %s at trust '%s'",
            self.oracle_id,
            record.proposal_id,
            safe_response["trust_level"],
        )
        return safe_response

    def enforce_ceiling(self, response_dict: dict) -> dict:
        """Apply the channel's trust ceiling to *response_dict*.

        Delegates to the internal ``TrustCeilingEnforcer`` and additionally
        marks ``metadata.ceiling_enforced = True`` in the returned dict.

        Args:
            response_dict: A response dictionary whose ``trust_level`` may need
                clamping.
        """
        result = self._enforcer.enforce(self.oracle_id, response_dict)
        result.setdefault("metadata", {})["ceiling_enforced"] = True
        return result

    def register_jurisdiction(self, jurisdiction: OracleJurisdiction) -> None:
        """Attach *jurisdiction* to this channel, replacing any prior jurisdiction.

        Args:
            jurisdiction: The new jurisdiction to apply.
        """
        old_scope = self.jurisdiction.scope if self.jurisdiction else "(none)"
        self.jurisdiction = jurisdiction
        self._record_audit("register_jurisdiction", {
            "old_scope": old_scope,
            "new_scope": jurisdiction.scope,
            "ceiling": jurisdiction.trust_ceiling,
        })
        logger.info(
            "Oracle '%s': jurisdiction updated %s -> %s",
            self.oracle_id,
            old_scope,
            jurisdiction.scope,
        )

    def revoke_proposal(self, proposal_id: str, reason: str = "unspecified") -> bool:
        """Revoke the proposal identified by *proposal_id*.

        The proposal record is moved from ``active_proposals`` to the internal
        ``_revoked_proposals`` dict so that it is no longer returned by
        ``get_proposal`` but remains accessible for auditing.

        Args:
            proposal_id: The hex identifier of the proposal to revoke.
            reason: Human-readable reason for revocation.

        Returns:
            ``True`` if the proposal was found and revoked, ``False`` otherwise.
        """
        record = self.active_proposals.pop(proposal_id, None)
        if record is None:
            logger.warning("Oracle '%s': proposal '%s' not found for revocation", self.oracle_id, proposal_id)
            return False
        record.revoke(reason)
        self._revoked_proposals[proposal_id] = record
        self._record_audit("revoke_proposal", {"proposal_id": proposal_id, "reason": reason})
        return True

    def corroborate(self, proposal_id: str, corroborating_evidence: dict) -> bool:
        """Record external corroboration for *proposal_id*.

        Args:
            proposal_id: The proposal to corroborate.
            corroborating_evidence: A dict containing at least ``"source_id"``
                and optionally ``"trust_level"``.

        Returns:
            ``True`` if the proposal was found and updated; ``False`` otherwise.
        """
        record = self.active_proposals.get(proposal_id)
        if record is None:
            logger.warning(
                "Oracle '%s': corroboration for unknown proposal '%s'",
                self.oracle_id,
                proposal_id,
            )
            return False
        source_id = corroborating_evidence.get("source_id", "unknown_source")
        trust_level = corroborating_evidence.get("trust_level", "human_attested")
        record.corroborate(source_id, trust_level)
        self._record_audit("corroborate", {
            "proposal_id": proposal_id,
            "source_id": source_id,
            "trust_level": trust_level,
        })
        return True

    # ------------------------------------------------------------------
    # Queries and serialisation
    # ------------------------------------------------------------------

    def get_audit_trail(self) -> list[dict]:
        """Return a copy of the audit log for external inspection."""
        return list(self.audit_log)

    def serialize(self) -> dict:
        """Serialize the full channel state to a plain dictionary."""
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "channel_kind": self.channel_kind,
            "trust_ceiling": self.trust_ceiling,
            "active_proposal_count": len(self.active_proposals),
            "revoked_proposal_count": len(self._revoked_proposals),
            "audit_log_count": len(self.audit_log),
            "jurisdiction": self.jurisdiction.to_dict() if self.jurisdiction else None,
            "config": dict(self.config),
            "violation_summary": self._enforcer.get_violation_summary(),
        }

    def describe(self) -> str:
        """Return a multi-line human-readable description of this channel."""
        lines = [
            f"OracleChannel: {self.name} (id={self.oracle_id})",
            f"  kind          : {self.channel_kind}",
            f"  trust_ceiling : {self.trust_ceiling}",
            f"  active proposals : {len(self.active_proposals)}",
            f"  revoked proposals: {len(self._revoked_proposals)}",
            f"  audit entries : {len(self.audit_log)}",
        ]
        if self.jurisdiction:
            lines.append(f"  jurisdiction  : {self.jurisdiction.scope}")
        return "\n".join(lines)

    def active_proposal_count(self) -> int:
        """Return the number of currently active (non-revoked) proposals."""
        return len(self.active_proposals)

    def get_proposal(self, proposal_id: str) -> OracleProposalRecord | None:
        """Return the active proposal with *proposal_id*, or ``None`` if not found."""
        return self.active_proposals.get(proposal_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_audit(self, action: str, details: dict) -> None:
        """Append an audit entry to the channel's append-only audit log.

        Args:
            action: Short verb describing the action, e.g. ``"propose"``.
            details: Arbitrary dict of action-specific metadata.
        """
        entry = {
            "timestamp": time.time(),
            "oracle_id": self.oracle_id,
            "action": action,
            **details,
        }
        self.audit_log.append(entry)


# ---------------------------------------------------------------------------
# CopilotOracleChannel
# ---------------------------------------------------------------------------


class CopilotOracleChannel(OracleChannel):
    """Specialised oracle channel for Copilot-generated suggestions (Theory2.tex §7.1.4).

    The Copilot channel is constrained to ``TrustLevel.COPILOT_SUGGESTED``
    (rank 2), one tier below ``ORACLE_PROPOSED`` (rank 3).  This reflects
    the design principle that LLM-generated suggestions are inherently
    unverified and must be externally corroborated before they can influence
    high-trust conclusions.

    The channel's trust ceiling is enforced at every call boundary by the
    inherited ``TrustCeilingEnforcer``.  The ``validate_no_self_promotion``
    method provides a programmatic assertion that can be called in test suites
    or audit pipelines to confirm the invariant holds over all recorded
    proposals.

    Args:
        oracle_id: Optional channel identifier; generated if ``None``.
        name: Human-readable label for this copilot instance.
        config: Optional channel-specific configuration.
    """

    def __init__(
        self,
        oracle_id: str | None = None,
        name: str = "copilot_oracle",
        config: dict | None = None,
    ) -> None:
        # Copilot enters at COPILOT_SUGGESTED by design (§7.1.4)
        # This is one tier below ORACLE_PROPOSED
        super().__init__(
            oracle_id=oracle_id,
            name=name,
            channel_kind="copilot",
            trust_ceiling="copilot_suggested",
            config=config,
        )
        self.suggestion_history: list[dict] = []
        self.refusal_reasons: list[dict] = []

    # ------------------------------------------------------------------
    # Copilot-specific operations
    # ------------------------------------------------------------------

    def suggest(self, context: dict) -> dict:
        """Generate a suggestion from *context* and store it in suggestion history.

        Builds a proposal-style response with ``trust_level`` permanently fixed
        at ``copilot_suggested``.  The response is stored in both
        ``active_proposals`` (via the parent ``propose`` logic) and in the
        per-channel ``suggestion_history`` list for quick retrieval.

        # Copilot proposals are permanently at COPILOT_SUGGESTED unless externally corroborated (Theory2.tex §7.1.4)

        Args:
            context: A dict describing the suggestion context.  Expected keys
                include ``"proposition"`` (str), ``"coordinate"`` (str), and
                optionally ``"required_kind"`` (str).

        Returns:
            A response dict with ``trust_level == "copilot_suggested"``.
        """
        # Build a minimal request dict from context
        request: dict = {
            "request_id": context.get("request_id", uuid.uuid4().hex[:16]),
            "proposition": context.get("proposition", str(context)),
            "required_kind": context.get("required_kind", "copilot_suggestion"),
        }
        response = self.propose(request)

        # Ensure the trust level is exactly copilot_suggested (belt-and-suspenders)
        response["trust_level"] = "copilot_suggested"
        if "metadata" in response:
            response["metadata"]["copilot_ceiling"] = "copilot_suggested"
            response["metadata"]["theory_ref"] = "Theory2.tex §7.1.4"

        # Record in suggestion history
        history_entry = {
            "request_id": request["request_id"],
            "proposition": request["proposition"],
            "trust_level": "copilot_suggested",
            "timestamp": time.time(),
            "proposal_id": response.get("metadata", {}).get("proposal_id"),
            "context_keys": list(context.keys()),
        }
        self.suggestion_history.append(history_entry)

        logger.info(
            "CopilotOracleChannel '%s': suggestion recorded (proposal=%s trust=copilot_suggested)",
            self.oracle_id,
            history_entry.get("proposal_id"),
        )
        return response

    def refuse(self, reason: str, context: dict | None = None) -> None:
        """Record a refusal — a case where the copilot declined to suggest.

        Refusals are first-class records in the channel (Theory2.tex §7.1.4
        notes that refusals are evidence of scope limits) and are stored in
        ``refusal_reasons`` with a timestamp.

        Args:
            reason: Human-readable explanation for the refusal.
            context: Optional context dict describing the refused request.
        """
        record = {
            "reason": reason,
            "timestamp": time.time(),
            "context": context or {},
        }
        self.refusal_reasons.append(record)
        self._record_audit("refuse", {"reason": reason})
        logger.info("CopilotOracleChannel '%s': refused — %s", self.oracle_id, reason)

    def get_suggestion_history(self) -> list[dict]:
        """Return a copy of the suggestion history list."""
        return list(self.suggestion_history)

    def validate_no_self_promotion(self) -> bool:
        """Assert that no proposal or suggestion exceeds ``copilot_suggested``.

        Iterates over all entries in ``active_proposals`` and
        ``suggestion_history`` and raises ``AssertionError`` if any recorded
        trust level exceeds the ``copilot_suggested`` ceiling (rank 2).

        Returns:
            ``True`` if the invariant holds for all recorded proposals and
            suggestions.

        Raises:
            AssertionError: If any proposal or suggestion carries a trust level
                above ``copilot_suggested``.
        """
        max_allowed_rank = _rank("copilot_suggested")

        for pid, record in self.active_proposals.items():
            actual_rank = _rank(record.trust_at_creation)
            assert actual_rank <= max_allowed_rank, (
                f"Self-promotion invariant violated: proposal {pid} "
                f"has trust_at_creation='{record.trust_at_creation}' "
                f"(rank {actual_rank}) > copilot_suggested (rank {max_allowed_rank}). "
                f"Theory2.tex §7.1.4 prohibits self-promotion."
            )

        for entry in self.suggestion_history:
            tl = entry.get("trust_level", "copilot_suggested")
            entry_rank = _rank(str(tl))
            assert entry_rank <= max_allowed_rank, (
                f"Self-promotion invariant violated: suggestion history entry "
                f"request_id={entry.get('request_id')} has trust_level='{tl}' "
                f"(rank {entry_rank}) > copilot_suggested (rank {max_allowed_rank}). "
                f"Theory2.tex §7.1.4 prohibits self-promotion."
            )

        logger.info(
            "CopilotOracleChannel '%s': no_self_promotion invariant validated OK "
            "(%d proposals, %d suggestions checked)",
            self.oracle_id,
            len(self.active_proposals),
            len(self.suggestion_history),
        )
        return True

    def get_refusal_count(self) -> int:
        """Return the total number of refusals recorded by this channel."""
        return len(self.refusal_reasons)

    def describe(self) -> str:
        """Return a multi-line description emphasising the copilot trust ceiling invariant."""
        lines = [
            f"CopilotOracleChannel: {self.name} (id={self.oracle_id})",
            "  trust_ceiling : copilot_suggested  [INVARIANT: cannot self-promote]",
            f"  active proposals : {len(self.active_proposals)}",
            f"  suggestions recorded: {len(self.suggestion_history)}",
            f"  refusals recorded   : {len(self.refusal_reasons)}",
            f"  audit entries       : {len(self.audit_log)}",
            "  theory ref    : Theory2.tex §7.1.4",
        ]
        if self.jurisdiction:
            lines.append(f"  jurisdiction  : {self.jurisdiction.scope}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level defaults and factory functions
# ---------------------------------------------------------------------------

_DEFAULT_ENFORCER: TrustCeilingEnforcer = TrustCeilingEnforcer()
"""Module-level default enforcer.  Channels may delegate to this instance
instead of maintaining their own, but the per-channel enforcer in
``OracleChannel.__init__`` is preferred for isolation."""


def create_oracle_channel(
    name: str,
    domains: list[str],
    ceiling: str = "oracle_proposed",
) -> OracleChannel:
    """Convenience factory for creating a configured ``OracleChannel``.

    Creates a jurisdiction from *domains* with the given *ceiling*, then
    constructs an ``OracleChannel`` with that jurisdiction pre-attached.

    Args:
        name: Human-readable name for the channel.
        domains: List of domain strings for the jurisdiction allowlist.
        ceiling: Trust ceiling for both the channel and its jurisdiction.

    Returns:
        A fully configured, ready-to-use ``OracleChannel`` instance.
    """
    scope = f"{name}_jurisdiction"
    jurisdiction = OracleJurisdiction(
        scope=scope,
        allowed_domains=domains,
        trust_ceiling=ceiling,
    )
    channel = OracleChannel(
        name=name,
        channel_kind="oracle",
        jurisdiction=jurisdiction,
        trust_ceiling=ceiling,
    )
    logger.info(
        "create_oracle_channel: created '%s' with ceiling='%s' domains=%s",
        name,
        ceiling,
        domains,
    )
    return channel


def create_copilot_channel(name: str = "copilot") -> CopilotOracleChannel:
    """Convenience factory for creating a ``CopilotOracleChannel``.

    The returned channel is pre-configured with a ``copilot_suggested``
    ceiling (Theory2.tex §7.1.4) and a general jurisdiction accepting any
    domain.

    Args:
        name: Human-readable name for this copilot channel instance.

    Returns:
        A ready-to-use ``CopilotOracleChannel`` with default configuration.
    """
    channel = CopilotOracleChannel(name=name)
    # Attach a permissive jurisdiction so that the channel can handle any
    # domain, but the trust ceiling is still enforced at the channel level.
    jurisdiction = OracleJurisdiction(
        scope=f"{name}_default_jurisdiction",
        allowed_domains=["*", "general", "copilot"],
        trust_ceiling="copilot_suggested",
    )
    channel.register_jurisdiction(jurisdiction)
    logger.info("create_copilot_channel: created '%s' (ceiling=copilot_suggested)", name)
    return channel
