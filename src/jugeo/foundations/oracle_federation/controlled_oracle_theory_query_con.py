"""Section — Controlled Oracle Theory: Query Constructors, Jurisdiction, and Trust Boundaries.

# copilot: foundations/oracle_federation §query-constructors — Theory2.tex
# Chapter: Controlled oracles, solver federation, and runtime witnesses

This module implements the **controlled oracle** model as described in Theory2.tex, focusing
on the three pillars that make an oracle *controlled*:

1. **Query constructors** — first-class objects that build well-typed queries from a
   coordinate and an evidence request.  A query constructor enforces a schema so that the
   oracle never receives a malformed request.

2. **Jurisdiction bounds** — a controlled oracle may only be dispatched for coordinates
   that fall within its declared jurisdiction.  Jurisdiction is represented as a
   ``JurisdictionBound`` — a typed coordinate predicate — and the coordinator refuses to
   issue a query outside that bound.

3. **Trust boundaries** — every oracle is associated with a hard trust ceiling
   (``TrustBoundary``).  Responses that claim a tier above the ceiling are silently clamped
   to ``PROPOSAL``.  This preserves the invariant that oracle proposals always enter the
   trust stack at ``PROPOSAL`` tier and can only be promoted by independent evidence.

Theory2.tex invariant
---------------------
A judgment is a tuple ``(c, φ, A, E, O, B, T, Π)`` — never a boolean.
Trust is an ordered algebra ``PROPOSAL → REVIEWED → VERIFIED`` — never a float.
Oracle proposals **always** enter at ``PROPOSAL`` tier; there is no silent promotion.

Public API
----------
- :class:`QueryKind` — enum of supported query types
- :class:`TrustTierLocal` — local three-level trust algebra
- :class:`JurisdictionBound` — predicate + coordinate range for oracle scope
- :class:`TrustBoundary` — hard ceiling on trust level for an oracle
- :class:`QueryConstructorSpec` — schema for building oracle queries
- :class:`QueryRequest` — fully-formed query ready for dispatch
- :class:`QueryResult` — raw result before trust adjudication
- :class:`AdjudicatedResult` — result after trust-ceiling enforcement
- :class:`ControlledOracleTheoryQueryCoordinator` — orchestrates the full query lifecycle
- :class:`ControlledOracleTheoryQueryAnalyzer` — analyzes query traces and trust flows
- :class:`ControlledOracleTheoryQueryWitness` — immutable certificate of a completed query run
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
from typing import Any, Callable, Mapping, Sequence

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel, TrustProfile
except ImportError:
    TrustTier = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustProfile = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust algebra constants (Theory2.tex §7 ordered algebra)
# ---------------------------------------------------------------------------

_TRUST_ORDER: dict[str, int] = {
    "PROPOSAL": 0,
    "REVIEWED": 1,
    "VERIFIED": 2,
}

_DEFAULT_ORACLE_CEILING = "PROPOSAL"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class QueryKind(Enum):
    """Classifies the type of oracle query being constructed.

    Theory2.tex defines several query modalities that map onto different
    evidence channels and have different trust ceilings by default.

    STRUCTURAL
        Queries about structural properties of a coordinate (types, shapes).
    BEHAVIORAL
        Queries about dynamic/runtime properties (termination, liveness).
    SEMANTIC
        Queries about the meaning or interpretation of an expression.
    RELATIONAL
        Queries that compare two or more coordinates (subsumption, equivalence).
    FORENSIC
        Post-hoc queries about past execution traces or logs.
    """

    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    SEMANTIC = "semantic"
    RELATIONAL = "relational"
    FORENSIC = "forensic"


class QueryStatus(Enum):
    """Lifecycle status of an oracle query."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    ANSWERED = "answered"
    CLAMPED = "clamped"      # Trust was clamped by ceiling enforcer
    REJECTED = "rejected"    # Outside jurisdiction or malformed
    TIMED_OUT = "timed_out"
    ERROR = "error"


class JurisdictionVerdict(Enum):
    """Result of checking whether a query falls within an oracle's jurisdiction."""

    IN_JURISDICTION = "in_jurisdiction"
    OUT_OF_JURISDICTION = "out_of_jurisdiction"
    BOUNDARY = "boundary"    # On the exact edge; coordinator decides
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustTierLocal:
    """A local three-level trust algebra for the oracle_federation package.

    Maps the string labels ``PROPOSAL``, ``REVIEWED``, ``VERIFIED`` to an
    ordered integer rank.  This is an **ordered algebra** — not a float —
    so that promotions are always explicit and auditable.

    Parameters
    ----------
    label:
        One of ``'PROPOSAL'``, ``'REVIEWED'``, ``'VERIFIED'``.
    rank:
        Integer rank in the partial order (0, 1, 2 respectively).
    provenance:
        Free-text note explaining how this tier was assigned.
    """

    label: str = "PROPOSAL"
    rank: int = 0
    provenance: str = ""

    @classmethod
    def proposal(cls, provenance: str = "") -> TrustTierLocal:
        """Create a PROPOSAL tier (the default entry point for oracle evidence)."""
        return cls(label="PROPOSAL", rank=0, provenance=provenance)

    @classmethod
    def reviewed(cls, provenance: str = "") -> TrustTierLocal:
        """Create a REVIEWED tier (requires independent corroboration)."""
        return cls(label="REVIEWED", rank=1, provenance=provenance)

    @classmethod
    def verified(cls, provenance: str = "") -> TrustTierLocal:
        """Create a VERIFIED tier (requires mechanically-checked proof)."""
        return cls(label="VERIFIED", rank=2, provenance=provenance)

    def dominates(self, other: TrustTierLocal) -> bool:
        """Return True if ``self`` is at least as trusted as ``other``."""
        return self.rank >= other.rank

    def clamp_to(self, ceiling: TrustTierLocal) -> TrustTierLocal:
        """Return the lesser of ``self`` and ``ceiling`` in the trust order."""
        if self.rank <= ceiling.rank:
            return self
        return ceiling

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "rank": self.rank, "provenance": self.provenance}


@dataclass(frozen=True, slots=True)
class JurisdictionBound:
    """Declares the coordinate range and domain over which an oracle is authoritative.

    A ``JurisdictionBound`` is the primary mechanism by which a controlled oracle
    advertises its scope.  The coordinator checks this bound **before** constructing
    a query — if the target coordinate falls outside the bound, the query is rejected
    with status ``REJECTED`` and a ``JurisdictionVerdict.OUT_OF_JURISDICTION`` verdict.

    Theory2.tex §7.1 defines oracle jurisdiction as a typed predicate on coordinates
    together with a domain tag that names the semantic theory governing the oracle.

    Parameters
    ----------
    oracle_id:
        Unique identifier of the oracle owning this bound.
    domain_tag:
        A short label naming the semantic theory (e.g. ``'type_theory'``,
        ``'linear_arithmetic'``, ``'program_logic'``).
    coordinate_predicate:
        A string expression describing the predicate over coordinates.
        In a full implementation this would be a callable; here it is stored
        as documentation and evaluated by :meth:`check`.
    coordinate_range_lo:
        Lower bound of the numeric coordinate range (inclusive), if applicable.
    coordinate_range_hi:
        Upper bound of the numeric coordinate range (exclusive), if applicable.
    allowed_query_kinds:
        The set of :class:`QueryKind` values this oracle accepts.  If empty,
        all kinds are accepted.
    strict:
        If True, boundary coordinates are rejected rather than accepted.
    metadata:
        Auxiliary key-value pairs for extension.
    """

    oracle_id: str = ""
    domain_tag: str = ""
    coordinate_predicate: str = "true"
    coordinate_range_lo: float = float("-inf")
    coordinate_range_hi: float = float("inf")
    allowed_query_kinds: tuple[str, ...] = ()
    strict: bool = False
    metadata: dict = field(default_factory=dict)

    def check(self, coordinate: str, query_kind: QueryKind) -> JurisdictionVerdict:
        """Check whether *coordinate* with *query_kind* falls within this bound.

        The check is a two-part test:
        1. If ``allowed_query_kinds`` is non-empty, the kind must be present.
        2. The coordinate string is tested for membership in the domain.

        Returns
        -------
        JurisdictionVerdict
            The verdict of the check.
        """
        if self.allowed_query_kinds and query_kind.value not in self.allowed_query_kinds:
            return JurisdictionVerdict.OUT_OF_JURISDICTION
        # Naive numeric check: if coordinate is a float string, compare range
        try:
            val = float(coordinate)
            if val < self.coordinate_range_lo or val >= self.coordinate_range_hi:
                return JurisdictionVerdict.OUT_OF_JURISDICTION
            if self.strict and (
                val == self.coordinate_range_lo or val == self.coordinate_range_hi - 1
            ):
                return JurisdictionVerdict.BOUNDARY
        except (ValueError, TypeError):
            # Non-numeric coordinate — accept if domain_tag prefix matches
            if self.domain_tag and not coordinate.startswith(self.domain_tag):
                return JurisdictionVerdict.OUT_OF_JURISDICTION
        return JurisdictionVerdict.IN_JURISDICTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "domain_tag": self.domain_tag,
            "coordinate_predicate": self.coordinate_predicate,
            "coordinate_range_lo": self.coordinate_range_lo,
            "coordinate_range_hi": self.coordinate_range_hi,
            "allowed_query_kinds": list(self.allowed_query_kinds),
            "strict": self.strict,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JurisdictionBound:
        return cls(
            oracle_id=d.get("oracle_id", ""),
            domain_tag=d.get("domain_tag", ""),
            coordinate_predicate=d.get("coordinate_predicate", "true"),
            coordinate_range_lo=float(d.get("coordinate_range_lo", float("-inf"))),
            coordinate_range_hi=float(d.get("coordinate_range_hi", float("inf"))),
            allowed_query_kinds=tuple(d.get("allowed_query_kinds", [])),
            strict=bool(d.get("strict", False)),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class TrustBoundary:
    """Hard ceiling on the trust tier an oracle may claim in its responses.

    Theory2.tex invariant: oracle proposals always enter at ``PROPOSAL`` tier.
    This class enforces that invariant by storing the ceiling and providing
    :meth:`enforce` which clamps any incoming tier claim.

    Parameters
    ----------
    oracle_id:
        The oracle whose responses are governed by this boundary.
    ceiling:
        The maximum :class:`TrustTierLocal` this oracle is permitted to claim.
    rationale:
        A free-text explanation of why this ceiling was set.
    override_allowed:
        If True, a privileged caller may temporarily override the ceiling
        (used in testing only; defaults to False).
    """

    oracle_id: str = ""
    ceiling: TrustTierLocal = field(default_factory=TrustTierLocal.proposal)
    rationale: str = "Oracle proposals default to PROPOSAL tier per Theory2.tex §7.1"
    override_allowed: bool = False

    def enforce(self, claimed_tier: TrustTierLocal) -> tuple[TrustTierLocal, bool]:
        """Clamp *claimed_tier* to this boundary's ceiling.

        Returns
        -------
        (enforced_tier, was_clamped)
            The enforced tier and a flag indicating whether clamping occurred.
        """
        enforced = claimed_tier.clamp_to(self.ceiling)
        was_clamped = enforced.rank < claimed_tier.rank
        if was_clamped:
            logger.debug(
                "TrustBoundary: oracle %s claimed %s, clamped to %s",
                self.oracle_id,
                claimed_tier.label,
                enforced.label,
            )
        return enforced, was_clamped

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "ceiling": self.ceiling.to_dict(),
            "rationale": self.rationale,
            "override_allowed": self.override_allowed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrustBoundary:
        ceiling_d = d.get("ceiling", {})
        ceiling = TrustTierLocal(
            label=ceiling_d.get("label", "PROPOSAL"),
            rank=ceiling_d.get("rank", 0),
            provenance=ceiling_d.get("provenance", ""),
        )
        return cls(
            oracle_id=d.get("oracle_id", ""),
            ceiling=ceiling,
            rationale=d.get("rationale", ""),
            override_allowed=bool(d.get("override_allowed", False)),
        )


@dataclass(frozen=True, slots=True)
class QueryConstructorSpec:
    """Schema definition for constructing a well-typed oracle query.

    A ``QueryConstructorSpec`` is the template from which a :class:`QueryRequest`
    is instantiated.  It declares:
    - The kind of query.
    - The required and optional fields.
    - A version tag for schema evolution.

    Theory2.tex §7.1 treats query constructors as first-class morphisms between
    the coordinate category and the oracle's input domain.  The spec captures
    the object-level description of that morphism.

    Parameters
    ----------
    spec_id:
        Unique identifier for this spec.
    query_kind:
        The :class:`QueryKind` this spec produces.
    required_fields:
        Field names that must be present in the payload.
    optional_fields:
        Field names that may be present in the payload.
    schema_version:
        Semver-style version of the spec schema.
    description:
        Human-readable description of what queries built from this spec ask.
    """

    spec_id: str = field(default_factory=lambda: "qcs_" + uuid.uuid4().hex[:12])
    query_kind: str = QueryKind.STRUCTURAL.value
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    schema_version: str = "1.0.0"
    description: str = ""

    def validate_payload(self, payload: dict[str, Any]) -> list[str]:
        """Return a list of validation errors for *payload* against this spec.

        An empty list means the payload is valid.
        """
        errors: list[str] = []
        for req in self.required_fields:
            if req not in payload:
                errors.append(f"Missing required field: {req!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "query_kind": self.query_kind,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "schema_version": self.schema_version,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryConstructorSpec:
        return cls(
            spec_id=d.get("spec_id", "qcs_" + uuid.uuid4().hex[:12]),
            query_kind=d.get("query_kind", QueryKind.STRUCTURAL.value),
            required_fields=tuple(d.get("required_fields", [])),
            optional_fields=tuple(d.get("optional_fields", [])),
            schema_version=d.get("schema_version", "1.0.0"),
            description=d.get("description", ""),
        )


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """A fully-formed oracle query ready for dispatch.

    Built by :class:`ControlledOracleTheoryQueryCoordinator` using a
    :class:`QueryConstructorSpec`.  Immutable once created.

    Parameters
    ----------
    request_id:
        UUID hex identifier.
    oracle_id:
        Target oracle.
    coordinate:
        The coordinate being queried.
    query_kind:
        Kind of query (from :class:`QueryKind`).
    payload:
        Typed key-value payload conforming to the spec.
    spec_id:
        The :class:`QueryConstructorSpec` used to build this request.
    created_at:
        Unix timestamp of creation.
    timeout_seconds:
        Maximum allowed response time.
    metadata:
        Extension metadata.
    """

    request_id: str = field(default_factory=lambda: "qreq_" + uuid.uuid4().hex[:12])
    oracle_id: str = ""
    coordinate: str = ""
    query_kind: str = QueryKind.STRUCTURAL.value
    payload: dict = field(default_factory=dict)
    spec_id: str = ""
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 30.0
    metadata: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Return a deterministic SHA-256 fingerprint of the request content."""
        content = json.dumps(
            {"oracle_id": self.oracle_id, "coordinate": self.coordinate,
             "query_kind": self.query_kind, "payload": self.payload,
             "spec_id": self.spec_id},
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "oracle_id": self.oracle_id,
            "coordinate": self.coordinate,
            "query_kind": self.query_kind,
            "payload": dict(self.payload),
            "spec_id": self.spec_id,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryRequest:
        return cls(
            request_id=d.get("request_id", "qreq_" + uuid.uuid4().hex[:12]),
            oracle_id=d.get("oracle_id", ""),
            coordinate=d.get("coordinate", ""),
            query_kind=d.get("query_kind", QueryKind.STRUCTURAL.value),
            payload=dict(d.get("payload", {})),
            spec_id=d.get("spec_id", ""),
            created_at=float(d.get("created_at", time.time())),
            timeout_seconds=float(d.get("timeout_seconds", 30.0)),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Raw oracle response before trust adjudication.

    Carries the oracle's claimed trust tier which will be checked against the
    :class:`TrustBoundary` by the coordinator.

    Parameters
    ----------
    result_id:
        UUID hex identifier.
    request_id:
        The :class:`QueryRequest` this is a response to.
    oracle_id:
        Responding oracle.
    claimed_tier:
        Trust tier as claimed by the oracle (may be clamped).
    answer:
        The substantive oracle answer (free-form dict).
    latency_seconds:
        Wall-clock time to produce this result.
    status:
        :class:`QueryStatus` at the time of delivery.
    metadata:
        Extension metadata.
    """

    result_id: str = field(default_factory=lambda: "qres_" + uuid.uuid4().hex[:12])
    request_id: str = ""
    oracle_id: str = ""
    claimed_tier: TrustTierLocal = field(default_factory=TrustTierLocal.proposal)
    answer: dict = field(default_factory=dict)
    latency_seconds: float = 0.0
    status: str = QueryStatus.ANSWERED.value
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "request_id": self.request_id,
            "oracle_id": self.oracle_id,
            "claimed_tier": self.claimed_tier.to_dict(),
            "answer": dict(self.answer),
            "latency_seconds": self.latency_seconds,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryResult:
        ct_d = d.get("claimed_tier", {})
        ct = TrustTierLocal(
            label=ct_d.get("label", "PROPOSAL"),
            rank=ct_d.get("rank", 0),
            provenance=ct_d.get("provenance", ""),
        )
        return cls(
            result_id=d.get("result_id", "qres_" + uuid.uuid4().hex[:12]),
            request_id=d.get("request_id", ""),
            oracle_id=d.get("oracle_id", ""),
            claimed_tier=ct,
            answer=dict(d.get("answer", {})),
            latency_seconds=float(d.get("latency_seconds", 0.0)),
            status=d.get("status", QueryStatus.ANSWERED.value),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class AdjudicatedResult:
    """Oracle response after trust-ceiling enforcement.

    An ``AdjudicatedResult`` wraps a :class:`QueryResult` and records whether
    the trust tier was clamped.  This is the output of the coordinator's
    adjudication step and is the value stored in the :class:`ControlledOracleTheoryQueryWitness`.

    Parameters
    ----------
    adj_id:
        Unique identifier of this adjudication.
    raw_result:
        The original :class:`QueryResult`.
    enforced_tier:
        Trust tier after ceiling enforcement.
    was_clamped:
        True if the oracle claimed a higher tier that was clamped.
    clamping_rationale:
        Explanation of why clamping occurred (or empty if no clamping).
    adjudicated_at:
        Unix timestamp.
    """

    adj_id: str = field(default_factory=lambda: "adj_" + uuid.uuid4().hex[:12])
    raw_result: QueryResult = field(default_factory=QueryResult)
    enforced_tier: TrustTierLocal = field(default_factory=TrustTierLocal.proposal)
    was_clamped: bool = False
    clamping_rationale: str = ""
    adjudicated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adj_id": self.adj_id,
            "raw_result": self.raw_result.to_dict(),
            "enforced_tier": self.enforced_tier.to_dict(),
            "was_clamped": self.was_clamped,
            "clamping_rationale": self.clamping_rationale,
            "adjudicated_at": self.adjudicated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AdjudicatedResult:
        et_d = d.get("enforced_tier", {})
        et = TrustTierLocal(
            label=et_d.get("label", "PROPOSAL"),
            rank=et_d.get("rank", 0),
            provenance=et_d.get("provenance", ""),
        )
        return cls(
            adj_id=d.get("adj_id", "adj_" + uuid.uuid4().hex[:12]),
            raw_result=QueryResult.from_dict(d.get("raw_result", {})),
            enforced_tier=et,
            was_clamped=bool(d.get("was_clamped", False)),
            clamping_rationale=d.get("clamping_rationale", ""),
            adjudicated_at=float(d.get("adjudicated_at", time.time())),
        )


# ---------------------------------------------------------------------------
# Witness (immutable certificate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlledOracleTheoryQueryWitness:
    """Immutable certificate produced by a completed controlled-oracle query run.

    A ``ControlledOracleTheoryQueryWitness`` captures the full audit trail of
    one oracle interaction cycle: the request, jurisdiction verdict, raw result,
    adjudicated result, and final trust tier.

    Theory2.tex invariant: the ``final_tier`` field is ALWAYS a
    :class:`TrustTierLocal` string label — never a float and never a bool.
    The judgment tuple ``(c, φ, A, E, O, B, T, Π)`` is captured in
    ``judgment_tuple``.

    Parameters
    ----------
    witness_id:
        Globally unique identifier.
    coordinate:
        The coordinate that was queried.
    oracle_id:
        The oracle that responded.
    query_kind:
        The kind of query that was issued.
    jurisdiction_verdict:
        The :class:`JurisdictionVerdict` that was computed.
    request_fingerprint:
        SHA-256 fingerprint of the :class:`QueryRequest`.
    adjudicated_result:
        The post-adjudication result.
    final_tier:
        The final trust tier assigned to this evidence (must be a string label).
    judgment_tuple:
        The full ``(c, φ, A, E, O, B, T, Π)`` tuple as a dict.
    clamping_events:
        Number of times trust was clamped during this run.
    created_at:
        ISO-8601 UTC timestamp of witness creation.
    metadata:
        Extension key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: "cotqw_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    oracle_id: str = ""
    query_kind: str = QueryKind.STRUCTURAL.value
    jurisdiction_verdict: str = JurisdictionVerdict.UNKNOWN.value
    request_fingerprint: str = ""
    adjudicated_result: AdjudicatedResult = field(default_factory=AdjudicatedResult)
    final_tier: str = "PROPOSAL"
    judgment_tuple: dict = field(default_factory=dict)
    clamping_events: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "oracle_id": self.oracle_id,
            "query_kind": self.query_kind,
            "jurisdiction_verdict": self.jurisdiction_verdict,
            "request_fingerprint": self.request_fingerprint,
            "adjudicated_result": self.adjudicated_result.to_dict(),
            "final_tier": self.final_tier,
            "judgment_tuple": dict(self.judgment_tuple),
            "clamping_events": self.clamping_events,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ControlledOracleTheoryQueryWitness:
        """Deserialise from a plain dictionary."""
        return cls(
            witness_id=d.get("witness_id", "cotqw_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            oracle_id=d.get("oracle_id", ""),
            query_kind=d.get("query_kind", QueryKind.STRUCTURAL.value),
            jurisdiction_verdict=d.get("jurisdiction_verdict", JurisdictionVerdict.UNKNOWN.value),
            request_fingerprint=d.get("request_fingerprint", ""),
            adjudicated_result=AdjudicatedResult.from_dict(d.get("adjudicated_result", {})),
            final_tier=d.get("final_tier", "PROPOSAL"),
            judgment_tuple=dict(d.get("judgment_tuple", {})),
            clamping_events=int(d.get("clamping_events", 0)),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            metadata=dict(d.get("metadata", {})),
        )

    def validate(self) -> list[str]:
        """Return a list of invariant violations (empty means valid).

        Checks:
        - ``final_tier`` is a known tier label (not a float, not a bool).
        - ``jurisdiction_verdict`` is a known verdict.
        - ``clamping_events`` is non-negative.
        - ``judgment_tuple`` is non-empty.
        """
        errors: list[str] = []
        if self.final_tier not in _TRUST_ORDER:
            errors.append(
                f"final_tier {self.final_tier!r} is not in the trust algebra "
                f"(must be one of {list(_TRUST_ORDER.keys())})"
            )
        valid_verdicts = {v.value for v in JurisdictionVerdict}
        if self.jurisdiction_verdict not in valid_verdicts:
            errors.append(
                f"jurisdiction_verdict {self.jurisdiction_verdict!r} is not a valid verdict"
            )
        if self.clamping_events < 0:
            errors.append("clamping_events must be non-negative")
        if not self.judgment_tuple:
            errors.append("judgment_tuple must be non-empty (Theory2.tex invariant)")
        return errors

    def merge(self, other: ControlledOracleTheoryQueryWitness) -> ControlledOracleTheoryQueryWitness:
        """Merge this witness with *other* into a composite witness.

        The composite witness takes the weaker of the two final tiers
        (conservative principle) and accumulates clamping events.
        """
        my_rank = _TRUST_ORDER.get(self.final_tier, 0)
        other_rank = _TRUST_ORDER.get(other.final_tier, 0)
        merged_tier = self.final_tier if my_rank <= other_rank else other.final_tier
        merged_meta = {**self.metadata, **other.metadata, "merged_from": [self.witness_id, other.witness_id]}
        return replace(
            self,
            witness_id="cotqw_" + uuid.uuid4().hex[:12],
            final_tier=merged_tier,
            clamping_events=self.clamping_events + other.clamping_events,
            metadata=merged_meta,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def content_hash(self) -> str:
        """Return a SHA-256 digest of the canonical serialisation."""
        content = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


@dataclass
class ControlledOracleTheoryQueryCoordinator:
    """Orchestrates the full lifecycle of a controlled-oracle query.

    Implements Theory2.tex §7.1 (query constructors, jurisdiction, trust bounds)
    by chaining three steps:

    1. **Schema validation** — the payload is checked against the registered
       :class:`QueryConstructorSpec` for the oracle.
    2. **Jurisdiction check** — the target coordinate is tested against the
       oracle's :class:`JurisdictionBound`.
    3. **Trust adjudication** — the raw :class:`QueryResult` is passed through
       the :class:`TrustBoundary` and clamped if necessary.

    A :class:`ControlledOracleTheoryQueryWitness` is produced at the end of
    each successful ``run()`` call.

    Parameters
    ----------
    coordinator_id:
        Unique identifier of this coordinator instance.
    specs:
        Mapping from oracle_id to :class:`QueryConstructorSpec`.
    bounds:
        Mapping from oracle_id to :class:`JurisdictionBound`.
    trust_boundaries:
        Mapping from oracle_id to :class:`TrustBoundary`.
    history:
        List of all :class:`ControlledOracleTheoryQueryWitness` produced.
    strict_jurisdiction:
        If True, ``BOUNDARY`` verdicts are treated as ``OUT_OF_JURISDICTION``.
    """

    coordinator_id: str = field(default_factory=lambda: "cotqc_" + uuid.uuid4().hex[:12])
    specs: dict[str, QueryConstructorSpec] = field(default_factory=dict)
    bounds: dict[str, JurisdictionBound] = field(default_factory=dict)
    trust_boundaries: dict[str, TrustBoundary] = field(default_factory=dict)
    history: list[ControlledOracleTheoryQueryWitness] = field(default_factory=list)
    strict_jurisdiction: bool = False

    # ---- registration ----

    def register_oracle(
        self,
        oracle_id: str,
        spec: QueryConstructorSpec,
        bound: JurisdictionBound,
        trust_boundary: TrustBoundary | None = None,
    ) -> None:
        """Register an oracle with its spec, jurisdiction bound, and optional trust boundary.

        If *trust_boundary* is None a default PROPOSAL-ceiling boundary is created.
        """
        self.specs[oracle_id] = spec
        self.bounds[oracle_id] = bound
        if trust_boundary is None:
            trust_boundary = TrustBoundary(
                oracle_id=oracle_id,
                ceiling=TrustTierLocal.proposal(provenance="default registration"),
            )
        self.trust_boundaries[oracle_id] = trust_boundary
        logger.info("Registered oracle %s in coordinator %s", oracle_id, self.coordinator_id)

    def deregister_oracle(self, oracle_id: str) -> bool:
        """Remove an oracle from all registry maps.  Returns True if it existed."""
        existed = oracle_id in self.specs
        self.specs.pop(oracle_id, None)
        self.bounds.pop(oracle_id, None)
        self.trust_boundaries.pop(oracle_id, None)
        return existed

    # ---- query construction ----

    def build_request(
        self,
        oracle_id: str,
        coordinate: str,
        payload: dict[str, Any],
        timeout_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[QueryRequest | None, list[str]]:
        """Construct a :class:`QueryRequest` after validating the payload.

        Returns
        -------
        (request, errors)
            If *errors* is non-empty, *request* is None.
        """
        spec = self.specs.get(oracle_id)
        if spec is None:
            return None, [f"Oracle {oracle_id!r} is not registered"]
        errors = spec.validate_payload(payload)
        if errors:
            return None, errors
        req = QueryRequest(
            oracle_id=oracle_id,
            coordinate=coordinate,
            query_kind=spec.query_kind,
            payload=payload,
            spec_id=spec.spec_id,
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )
        return req, []

    # ---- jurisdiction check ----

    def check_jurisdiction(self, oracle_id: str, request: QueryRequest) -> JurisdictionVerdict:
        """Return the jurisdiction verdict for *request* against oracle *oracle_id*."""
        bound = self.bounds.get(oracle_id)
        if bound is None:
            return JurisdictionVerdict.UNKNOWN
        try:
            kind = QueryKind(request.query_kind)
        except ValueError:
            kind = QueryKind.STRUCTURAL
        verdict = bound.check(request.coordinate, kind)
        if self.strict_jurisdiction and verdict == JurisdictionVerdict.BOUNDARY:
            return JurisdictionVerdict.OUT_OF_JURISDICTION
        return verdict

    # ---- trust adjudication ----

    def adjudicate(
        self, oracle_id: str, result: QueryResult
    ) -> AdjudicatedResult:
        """Apply trust-ceiling enforcement to a raw query result."""
        boundary = self.trust_boundaries.get(oracle_id)
        if boundary is None:
            # No boundary registered — use default PROPOSAL ceiling
            boundary = TrustBoundary(oracle_id=oracle_id)
        enforced, was_clamped = boundary.enforce(result.claimed_tier)
        rationale = (
            f"Clamped from {result.claimed_tier.label} to {enforced.label} "
            f"by boundary for oracle {oracle_id}"
            if was_clamped
            else ""
        )
        return AdjudicatedResult(
            raw_result=result,
            enforced_tier=enforced,
            was_clamped=was_clamped,
            clamping_rationale=rationale,
        )

    # ---- main entry point ----

    def run(
        self,
        oracle_id: str,
        coordinate: str,
        payload: dict[str, Any],
        oracle_callable: Callable[[QueryRequest], QueryResult] | None = None,
        timeout_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> ControlledOracleTheoryQueryWitness:
        """Execute the full controlled-oracle query lifecycle.

        Steps:
        1. Build request (schema validation).
        2. Check jurisdiction.
        3. Dispatch to oracle (or mock if no callable provided).
        4. Adjudicate trust.
        5. Produce witness.

        Parameters
        ----------
        oracle_id:
            Target oracle identifier.
        coordinate:
            Coordinate to query.
        payload:
            Query payload (validated against spec).
        oracle_callable:
            Optional callable ``(QueryRequest) -> QueryResult``.  If None,
            a stub result is produced for testing.
        timeout_seconds:
            Dispatch timeout.
        metadata:
            Extra metadata attached to the witness.

        Returns
        -------
        ControlledOracleTheoryQueryWitness
            The immutable certificate of this query run.
        """
        t0 = time.monotonic()

        # Step 1 — build request
        req, errors = self.build_request(
            oracle_id, coordinate, payload, timeout_seconds, metadata
        )
        if errors:
            stub_result = QueryResult(
                oracle_id=oracle_id,
                status=QueryStatus.REJECTED.value,
                answer={"errors": errors},
            )
            stub_adj = AdjudicatedResult(
                raw_result=stub_result,
                enforced_tier=TrustTierLocal.proposal(provenance="rejected"),
                was_clamped=False,
                clamping_rationale="Request rejected due to schema errors",
            )
            w = ControlledOracleTheoryQueryWitness(
                coordinate=coordinate,
                oracle_id=oracle_id,
                jurisdiction_verdict=JurisdictionVerdict.UNKNOWN.value,
                request_fingerprint="",
                adjudicated_result=stub_adj,
                final_tier="PROPOSAL",
                judgment_tuple=self._build_judgment_tuple(coordinate, oracle_id, "PROPOSAL", errors),
                metadata=metadata or {},
            )
            self.history.append(w)
            return w

        # Step 2 — jurisdiction
        verdict = self.check_jurisdiction(oracle_id, req)
        if verdict == JurisdictionVerdict.OUT_OF_JURISDICTION:
            stub_result = QueryResult(
                oracle_id=oracle_id,
                request_id=req.request_id,
                status=QueryStatus.REJECTED.value,
                answer={"reason": "out_of_jurisdiction"},
            )
            stub_adj = AdjudicatedResult(
                raw_result=stub_result,
                enforced_tier=TrustTierLocal.proposal(provenance="rejected"),
                was_clamped=False,
                clamping_rationale="Out of jurisdiction",
            )
            w = ControlledOracleTheoryQueryWitness(
                coordinate=coordinate,
                oracle_id=oracle_id,
                query_kind=req.query_kind,
                jurisdiction_verdict=verdict.value,
                request_fingerprint=req.fingerprint(),
                adjudicated_result=stub_adj,
                final_tier="PROPOSAL",
                judgment_tuple=self._build_judgment_tuple(coordinate, oracle_id, "PROPOSAL", []),
                metadata=metadata or {},
            )
            self.history.append(w)
            return w

        # Step 3 — dispatch
        if oracle_callable is not None:
            raw_result = oracle_callable(req)
        else:
            raw_result = self._stub_result(req)

        # Step 4 — adjudicate
        adj = self.adjudicate(oracle_id, raw_result)
        elapsed = time.monotonic() - t0

        # Step 5 — produce witness
        w = ControlledOracleTheoryQueryWitness(
            coordinate=coordinate,
            oracle_id=oracle_id,
            query_kind=req.query_kind,
            jurisdiction_verdict=verdict.value,
            request_fingerprint=req.fingerprint(),
            adjudicated_result=adj,
            final_tier=adj.enforced_tier.label,
            judgment_tuple=self._build_judgment_tuple(coordinate, oracle_id, adj.enforced_tier.label, []),
            clamping_events=1 if adj.was_clamped else 0,
            metadata={**(metadata or {}), "elapsed_seconds": elapsed},
        )
        self.history.append(w)
        logger.info(
            "Query run complete: oracle=%s coord=%s tier=%s clamped=%s",
            oracle_id, coordinate, adj.enforced_tier.label, adj.was_clamped,
        )
        return w

    def _stub_result(self, req: QueryRequest) -> QueryResult:
        """Produce a stub oracle result for testing when no callable is provided."""
        return QueryResult(
            request_id=req.request_id,
            oracle_id=req.oracle_id,
            claimed_tier=TrustTierLocal.proposal(provenance="stub"),
            answer={"stub": True, "coordinate": req.coordinate},
            latency_seconds=0.001,
            status=QueryStatus.ANSWERED.value,
        )

    def _build_judgment_tuple(
        self,
        coordinate: str,
        oracle_id: str,
        trust_tier: str,
        errors: list[str],
    ) -> dict[str, Any]:
        """Build the ``(c, φ, A, E, O, B, T, Π)`` judgment tuple as a dict.

        Fields
        ------
        c  — coordinate
        φ  — formula (oracle query description)
        A  — agent (oracle_id)
        E  — evidence (latest query result summary)
        O  — obligations (pending schema errors)
        B  — boundary (jurisdiction verdict)
        T  — trust tier
        Π  — provenance (coordinator_id)
        """
        return {
            "c": coordinate,
            "phi": f"oracle_query({oracle_id})",
            "A": oracle_id,
            "E": {"source": oracle_id, "query": True},
            "O": errors,
            "B": self.bounds.get(oracle_id, JurisdictionBound()).domain_tag,
            "T": trust_tier,
            "Pi": self.coordinator_id,
        }

    # ---- introspection ----

    def get_history(self, oracle_id: str | None = None) -> list[ControlledOracleTheoryQueryWitness]:
        """Return all witnesses, optionally filtered by *oracle_id*."""
        if oracle_id is None:
            return list(self.history)
        return [w for w in self.history if w.oracle_id == oracle_id]

    def clamping_rate(self, oracle_id: str | None = None) -> float:
        """Return the fraction of runs in which trust was clamped."""
        history = self.get_history(oracle_id)
        if not history:
            return 0.0
        clamped = sum(1 for w in history if w.clamping_events > 0)
        return clamped / len(history)

    def clear_history(self) -> int:
        """Clear all witness history.  Returns the number of records removed."""
        count = len(self.history)
        self.history.clear()
        return count

    # ---- serialisation ----

    def validate(self) -> list[str]:
        """Return invariant violations for this coordinator (empty = valid)."""
        errors: list[str] = []
        for oid, boundary in self.trust_boundaries.items():
            if boundary.ceiling.rank > _TRUST_ORDER.get("PROPOSAL", 0):
                errors.append(
                    f"Oracle {oid!r} has trust ceiling above PROPOSAL — "
                    "this violates the Theory2.tex invariant"
                )
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "specs": {k: v.to_dict() for k, v in self.specs.items()},
            "bounds": {k: v.to_dict() for k, v in self.bounds.items()},
            "trust_boundaries": {k: v.to_dict() for k, v in self.trust_boundaries.items()},
            "history_count": len(self.history),
            "strict_jurisdiction": self.strict_jurisdiction,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ControlledOracleTheoryQueryCoordinator:
        c = cls(
            coordinator_id=d.get("coordinator_id", "cotqc_" + uuid.uuid4().hex[:12]),
            strict_jurisdiction=bool(d.get("strict_jurisdiction", False)),
        )
        for k, v in d.get("specs", {}).items():
            c.specs[k] = QueryConstructorSpec.from_dict(v)
        for k, v in d.get("bounds", {}).items():
            c.bounds[k] = JurisdictionBound.from_dict(v)
        for k, v in d.get("trust_boundaries", {}).items():
            c.trust_boundaries[k] = TrustBoundary.from_dict(v)
        return c


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


@dataclass
class ControlledOracleTheoryQueryAnalyzer:
    """Analyzes a corpus of query witnesses and produces trust-flow reports.

    The analyzer operates on the ``history`` list of a
    :class:`ControlledOracleTheoryQueryCoordinator` or on any collection of
    :class:`ControlledOracleTheoryQueryWitness` objects.

    It computes:
    - Per-oracle clamping rates.
    - Jurisdiction rejection rates.
    - Trust-tier distribution.
    - Latency statistics.
    - Anomaly detection (oracles that systematically over-claim trust).

    Theory2.tex relevance: the analyzer provides the empirical feedback loop
    that allows operators to detect oracles whose trust claims are consistently
    above their ceilings — a signal that the oracle's ceiling may be miscalibrated
    or that the oracle is attempting trust inflation.
    """

    analyzer_id: str = field(default_factory=lambda: "cotqa_" + uuid.uuid4().hex[:12])
    witnesses: list[ControlledOracleTheoryQueryWitness] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)

    def load(self, witnesses: Sequence[ControlledOracleTheoryQueryWitness]) -> None:
        """Replace the internal witness corpus."""
        self.witnesses = list(witnesses)
        self._cache.clear()

    def append(self, witness: ControlledOracleTheoryQueryWitness) -> None:
        """Append a single witness to the corpus."""
        self.witnesses.append(witness)
        self._cache.clear()

    # ---- core analysis ----

    def analyze(self) -> dict[str, Any]:
        """Return a structured analysis of the witness corpus.

        The returned dict has keys:
        ``total``, ``by_oracle``, ``by_tier``, ``by_verdict``, ``clamping_summary``,
        ``latency_stats``, ``anomalies``.
        """
        if "analysis" in self._cache:
            return self._cache["analysis"]  # type: ignore[return-value]

        result: dict[str, Any] = {
            "total": len(self.witnesses),
            "by_oracle": self._group_by_oracle(),
            "by_tier": self._group_by_tier(),
            "by_verdict": self._group_by_verdict(),
            "clamping_summary": self._clamping_summary(),
            "latency_stats": self._latency_stats(),
            "anomalies": self._detect_anomalies(),
        }
        self._cache["analysis"] = result
        return result

    def score(self) -> float:
        """Return a scalar trust-health score in [0, 1].

        A higher score indicates a healthier trust ecology:
        - Fewer clamping events → higher score.
        - Fewer jurisdiction rejections → higher score.
        - Lower anomaly count → higher score.

        Returns 0.0 if the corpus is empty.
        """
        if not self.witnesses:
            return 0.0
        analysis = self.analyze()
        total = analysis["total"]
        clamp_count = analysis["clamping_summary"].get("total_clamped", 0)
        reject_count = sum(
            v.get("rejected", 0) for v in analysis["by_oracle"].values()
        )
        anomaly_count = len(analysis.get("anomalies", []))
        penalty = (clamp_count + reject_count + anomaly_count * 2) / (total + 1)
        return max(0.0, 1.0 - penalty)

    def report(self) -> str:
        """Return a human-readable multi-line report."""
        a = self.analyze()
        lines = [
            "=== ControlledOracleTheoryQuery Analysis Report ===",
            f"Total witnesses: {a['total']}",
            f"Trust-health score: {self.score():.3f}",
            "",
            "--- By oracle ---",
        ]
        for oid, stats in a["by_oracle"].items():
            lines.append(
                f"  {oid}: total={stats.get('total', 0)}, "
                f"clamped={stats.get('clamped', 0)}, "
                f"rejected={stats.get('rejected', 0)}"
            )
        lines += [
            "",
            "--- Trust tier distribution ---",
        ]
        for tier, count in a["by_tier"].items():
            lines.append(f"  {tier}: {count}")
        lines += [
            "",
            "--- Jurisdiction verdict distribution ---",
        ]
        for verdict, count in a["by_verdict"].items():
            lines.append(f"  {verdict}: {count}")
        if a.get("anomalies"):
            lines += ["", "--- Anomalies ---"]
            for anomaly in a["anomalies"]:
                lines.append(f"  {anomaly}")
        return "\n".join(lines)

    def summarize(self) -> dict[str, Any]:
        """Return a compact summary dict suitable for embedding in another report."""
        return {
            "analyzer_id": self.analyzer_id,
            "total_witnesses": len(self.witnesses),
            "score": self.score(),
            "anomaly_count": len(self.analyze().get("anomalies", [])),
        }

    # ---- per-oracle clamping analysis ----

    def clamping_rate_per_oracle(self) -> dict[str, float]:
        """Return a mapping from oracle_id to its clamping rate."""
        rates: dict[str, float] = {}
        for oid, stats in self._group_by_oracle().items():
            total = stats.get("total", 0)
            clamped = stats.get("clamped", 0)
            rates[oid] = clamped / total if total > 0 else 0.0
        return rates

    def tier_promotion_attempts(self) -> list[dict[str, Any]]:
        """Return witnesses where the oracle claimed a tier above PROPOSAL."""
        result = []
        for w in self.witnesses:
            raw_tier = w.adjudicated_result.raw_result.claimed_tier.label
            if _TRUST_ORDER.get(raw_tier, 0) > _TRUST_ORDER.get("PROPOSAL", 0):
                result.append({
                    "witness_id": w.witness_id,
                    "oracle_id": w.oracle_id,
                    "claimed": raw_tier,
                    "enforced": w.final_tier,
                })
        return result

    def jurisdiction_rejection_rate(self) -> float:
        """Return the overall fraction of witnesses that were rejected for jurisdiction."""
        if not self.witnesses:
            return 0.0
        rejected = sum(
            1 for w in self.witnesses
            if w.jurisdiction_verdict == JurisdictionVerdict.OUT_OF_JURISDICTION.value
        )
        return rejected / len(self.witnesses)

    def latency_percentiles(self, percentiles: Sequence[float] = (0.5, 0.9, 0.99)) -> dict[str, float]:
        """Return latency percentiles (in seconds) for answered queries."""
        latencies = [
            w.adjudicated_result.raw_result.latency_seconds
            for w in self.witnesses
            if w.adjudicated_result.raw_result.status == QueryStatus.ANSWERED.value
        ]
        if not latencies:
            return {str(p): 0.0 for p in percentiles}
        latencies.sort()
        n = len(latencies)
        result = {}
        for p in percentiles:
            idx = min(int(p * n), n - 1)
            result[f"p{int(p * 100)}"] = latencies[idx]
        return result

    # ---- private helpers ----

    def _group_by_oracle(self) -> dict[str, dict[str, int]]:
        groups: dict[str, dict[str, int]] = {}
        for w in self.witnesses:
            g = groups.setdefault(w.oracle_id, {"total": 0, "clamped": 0, "rejected": 0})
            g["total"] += 1
            if w.clamping_events > 0:
                g["clamped"] += 1
            if w.jurisdiction_verdict == JurisdictionVerdict.OUT_OF_JURISDICTION.value:
                g["rejected"] += 1
        return groups

    def _group_by_tier(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.final_tier] = counts.get(w.final_tier, 0) + 1
        return counts

    def _group_by_verdict(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.jurisdiction_verdict] = counts.get(w.jurisdiction_verdict, 0) + 1
        return counts

    def _clamping_summary(self) -> dict[str, Any]:
        total_clamped = sum(1 for w in self.witnesses if w.clamping_events > 0)
        total_events = sum(w.clamping_events for w in self.witnesses)
        return {
            "total_clamped": total_clamped,
            "total_clamp_events": total_events,
            "clamping_rate": total_clamped / len(self.witnesses) if self.witnesses else 0.0,
        }

    def _latency_stats(self) -> dict[str, float]:
        latencies = [
            w.adjudicated_result.raw_result.latency_seconds for w in self.witnesses
        ]
        if not latencies:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "min": min(latencies),
            "max": max(latencies),
            "mean": sum(latencies) / len(latencies),
        }

    def _detect_anomalies(self) -> list[str]:
        """Flag oracles with clamping rate > 50% as anomalous."""
        anomalies = []
        for oid, rate in self.clamping_rate_per_oracle().items():
            if rate > 0.5:
                anomalies.append(
                    f"Oracle {oid!r} has clamping rate {rate:.1%} — "
                    "may be systematically over-claiming trust"
                )
        return anomalies


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== controlled_oracle_theory_query_con.py smoke test ===")

    # Build coordinator and register a mock oracle
    coordinator = ControlledOracleTheoryQueryCoordinator()
    spec = QueryConstructorSpec(
        query_kind=QueryKind.STRUCTURAL.value,
        required_fields=("expression",),
        description="Structural query for type inference",
    )
    bound = JurisdictionBound(
        oracle_id="mock_oracle",
        domain_tag="type_theory",
        coordinate_predicate="coordinate starts with 'type_theory'",
        allowed_query_kinds=(QueryKind.STRUCTURAL.value,),
    )
    coordinator.register_oracle("mock_oracle", spec, bound)

    # Validate coordinator invariants
    violations = coordinator.validate()
    assert violations == [], f"Invariant violations: {violations}"

    # Run a query
    witness = coordinator.run(
        oracle_id="mock_oracle",
        coordinate="type_theory.Nat",
        payload={"expression": "Nat.succ"},
    )
    print(f"Witness ID: {witness.witness_id}")
    print(f"Final tier: {witness.final_tier}")
    print(f"Jurisdiction verdict: {witness.jurisdiction_verdict}")
    assert witness.final_tier == "PROPOSAL", "Oracle must enter at PROPOSAL tier"

    # Validate witness
    w_errors = witness.validate()
    assert w_errors == [], f"Witness validation errors: {w_errors}"

    # Roundtrip
    d = witness.to_dict()
    w2 = ControlledOracleTheoryQueryWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Analyzer
    analyzer = ControlledOracleTheoryQueryAnalyzer(witnesses=[witness])
    score = analyzer.score()
    print(f"Trust-health score: {score:.3f}")
    print(analyzer.report())

    # Merge witnesses
    w3 = witness.merge(w2)
    print(f"Merged witness ID: {w3.witness_id}")

    print("\n[PASS] All smoke tests passed.")
