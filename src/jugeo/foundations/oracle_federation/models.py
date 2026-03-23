from __future__ import annotations
"""Core data models for oracle_federation — Theory2.tex Chapter 7.

Provides the primary model classes used across all sub-sections of Ch7:
OracleModel, SolverFederationModel, RuntimeWitnessModel, JurisdictionModel,
and associated configuration dataclasses.

These models are the runtime representations of the formal objects defined in
Theory2.tex Chapter 7:

  §7.1  OracleModel — runtime representation of a Controlled Oracle (Def 7.1).
        Enforces the TrustCeiling property (Thm 7.2) by clamping any proposed
        evidence item to at most ORACLE_PROPOSED trust.

  §7.2  SolverFederationModel — runtime registry of solver backends and the
        routing table that maps LogicalFragment kinds to specific backends.
        Implements the MergeConsistency lemma (Lem 7.6) via configurable
        MergeStrategy policies.

  §7.3  RuntimeWitnessModel — container for heap/identity/stack witnesses
        collected during program execution. Exposes serialisation helpers that
        integrate with the EvidenceChannel pipeline.

All models support serialisation to plain dicts for persistence and
inter-process communication.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile  # noqa: F401
    from jugeo.evidence.channels import (  # noqa: F401
        EvidenceChannel,
        EvidenceRequest,
        EvidenceResponse,
    )
    from jugeo.solver.router import RoutingDecision, BackendKind  # noqa: F401
    from jugeo.solver.fragments import LogicalFragment  # noqa: F401
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    LogicalFragment = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class WitnessKind(str, Enum):
    """Enumeration of runtime witness categories (Theory2.tex §7.3).

    Each variant corresponds to a distinct collection mechanism:

    HEAP
        A snapshot of the JVM/Python heap at a program point,
        used to verify memory-safety predicates.
    IDENTITY
        A cryptographic or structural identity proof (e.g., object
        hash + class hierarchy path).
    STACK
        A call-stack trace captured at a specific execution point.
    BEHAVIORAL
        An aggregate behavioural log (sequence of method calls,
        IO events, etc.) that witnesses a higher-level property.
    COMPOSITE
        A composite witness built from multiple sub-witnesses of
        different kinds.
    """

    HEAP = "heap"
    IDENTITY = "identity"
    STACK = "stack"
    BEHAVIORAL = "behavioral"
    COMPOSITE = "composite"


class MergeStrategy(str, Enum):
    """Strategy for merging multiple solver results in a federation (§7.2).

    CONSERVATIVE
        Accept a claim only when *all* solvers agree.  Produces the
        most restrictive trust level.
    OPTIMISTIC
        Accept a claim when *any* solver agrees.  Produces the least
        restrictive trust level — use only in low-stakes contexts.
    INTERSECTION
        Return the intersection of all result sets (claims present in
        every result).
    UNION
        Return the union of all result sets (claims present in at
        least one result).
    WEIGHTED
        Apply per-solver weights when aggregating results; requires
        the ``stats`` field to contain ``solver_weights`` sub-dict.
    """

    CONSERVATIVE = "conservative"
    OPTIMISTIC = "optimistic"
    INTERSECTION = "intersection"
    UNION = "union"
    WEIGHTED = "weighted"


# ---------------------------------------------------------------------------
# Fragment classification helpers
# ---------------------------------------------------------------------------

_ARITHMETIC_KEYWORDS = frozenset({"arith", "arithmetic", "integer", "real", "linear", "nonlinear"})
_STRUCTURAL_KEYWORDS = frozenset({"struct", "structural", "array", "record", "pointer", "heap"})
_BEHAVIORAL_KEYWORDS = frozenset({"behavior", "behavioral", "temporal", "liveness", "safety", "trace"})


def _classify_fragment(fragment_kind: str) -> str:
    """Classify a fragment kind string into a solver category.

    Parameters
    ----------
    fragment_kind:
        Lower-case string description of the logical fragment.

    Returns
    -------
    str
        One of ``"arithmetic"``, ``"structural"``, ``"behavioral"``, or
        ``"hybrid"``.
    """
    lower = fragment_kind.lower()
    tokens = set(lower.replace("-", "_").split("_"))
    is_arith = bool(tokens & _ARITHMETIC_KEYWORDS)
    is_struct = bool(tokens & _STRUCTURAL_KEYWORDS)
    is_behav = bool(tokens & _BEHAVIORAL_KEYWORDS)
    matched = sum([is_arith, is_struct, is_behav])
    if matched > 1:
        return "hybrid"
    if is_arith:
        return "arithmetic"
    if is_struct:
        return "structural"
    if is_behav:
        return "behavioral"
    return "hybrid"


# ---------------------------------------------------------------------------
# OracleModel
# ---------------------------------------------------------------------------


@dataclass
class OracleModel:
    """Runtime representation of a Controlled Oracle (Theory2.tex Def 7.1).

    A Controlled Oracle is any external knowledge source that is permitted to
    contribute evidence to the JuGeo verification pipeline, subject to the
    constraint that its trust assertions never exceed ORACLE_PROPOSED level
    (the TrustCeiling property, Thm 7.2).

    Attributes
    ----------
    oracle_id:
        UUID string uniquely identifying this oracle instance.
    channel_kind:
        Logical channel kind label (default ``"oracle"``).
    trust_ceiling:
        The maximum TrustLevel this oracle may assert.  When the
        jugeo.evidence.trust module is available, this is set to
        ``TrustLevel.ORACLE_PROPOSED``; otherwise stored as the string
        ``"ORACLE_PROPOSED"``.
    jurisdiction_scope:
        List of domain strings (e.g. ``["arithmetic", "set_theory"]``)
        for which this oracle is authorised to make proposals.
    proposal_history:
        Ordered list of proposal record dicts, newest last.
    audit_log:
        Ordered list of audit record dicts, newest last.
    created_at:
        Unix timestamp of model creation.
    is_active:
        Whether this oracle is currently accepting requests.
    """

    oracle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    channel_kind: str = "oracle"
    trust_ceiling: Any = None
    jurisdiction_scope: list[str] = field(default_factory=list)
    proposal_history: list[dict] = field(default_factory=list)
    audit_log: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    is_active: bool = True

    def __post_init__(self) -> None:
        if self.trust_ceiling is None:
            if TrustLevel is not None:
                self.trust_ceiling = TrustLevel.ORACLE_PROPOSED
            else:
                self.trust_ceiling = "ORACLE_PROPOSED"

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def propose(self, request_summary: str, response_summary: str) -> dict:
        """Record a new proposal and return the proposal record.

        Creates a proposal record dict, appends it to
        :attr:`proposal_history`, and records a corresponding audit entry.

        Parameters
        ----------
        request_summary:
            Short textual summary of the incoming evidence request.
        response_summary:
            Short textual summary of the proposed response.

        Returns
        -------
        dict
            The newly created proposal record with keys:
            ``proposal_id``, ``oracle_id``, ``request_summary``,
            ``response_summary``, ``trust_ceiling``, ``timestamp``.
        """
        if not self.is_active:
            raise RuntimeError(f"OracleModel {self.oracle_id!r} is not active; cannot propose")

        ceiling_str = (
            self.trust_ceiling.value
            if hasattr(self.trust_ceiling, "value")
            else str(self.trust_ceiling)
        )
        record: dict[str, Any] = {
            "proposal_id": str(uuid.uuid4()),
            "oracle_id": self.oracle_id,
            "request_summary": request_summary,
            "response_summary": response_summary,
            "trust_ceiling": ceiling_str,
            "timestamp": time.time(),
        }
        self.proposal_history.append(record)
        self.record_audit(
            "propose",
            {
                "proposal_id": record["proposal_id"],
                "request_summary": request_summary,
            },
        )
        logger.debug(
            "OracleModel.propose: oracle=%s proposal=%s",
            self.oracle_id,
            record["proposal_id"],
        )
        return record

    def enforce_ceiling(self, response_dict: dict) -> dict:
        """Clamp the trust_level in *response_dict* to the oracle ceiling.

        Implements the TrustCeiling property (Theory2.tex Thm 7.2):
        any response passing through a Controlled Oracle may not carry a
        trust level higher than ORACLE_PROPOSED.

        The method mutates a *copy* of *response_dict* rather than the
        original.  It sets ``trust_level`` to the ceiling string and adds
        a ``ceiling_enforced`` flag set to ``True``.

        Parameters
        ----------
        response_dict:
            A dict representing an evidence response.  May contain a
            ``trust_level`` key with any string value.

        Returns
        -------
        dict
            Modified copy with ``trust_level`` clamped and
            ``ceiling_enforced=True``.
        """
        result = dict(response_dict)
        ceiling_str = (
            self.trust_ceiling.value
            if hasattr(self.trust_ceiling, "value")
            else str(self.trust_ceiling)
        )
        result["trust_level"] = ceiling_str
        result["ceiling_enforced"] = True
        result["enforcing_oracle_id"] = self.oracle_id
        self.record_audit(
            "enforce_ceiling",
            {"original_trust": response_dict.get("trust_level"), "clamped_to": ceiling_str},
        )
        logger.debug(
            "OracleModel.enforce_ceiling: clamped trust to %s for oracle=%s",
            ceiling_str,
            self.oracle_id,
        )
        return result

    def record_audit(self, action: str, details: dict) -> None:
        """Append a structured audit record to :attr:`audit_log`.

        Parameters
        ----------
        action:
            Short action label such as ``"propose"`` or ``"enforce_ceiling"``.
        details:
            Arbitrary key-value pairs describing the action.
        """
        entry: dict[str, Any] = {
            "audit_id": str(uuid.uuid4()),
            "oracle_id": self.oracle_id,
            "action": action,
            "timestamp": time.time(),
            "details": dict(details),
        }
        self.audit_log.append(entry)

    def get_history(self) -> list[dict]:
        """Return a shallow copy of the proposal history.

        Returns
        -------
        list[dict]
            Copy of :attr:`proposal_history`.
        """
        return list(self.proposal_history)

    def validate_jurisdiction(self, domain: str) -> bool:
        """Check whether *domain* falls within this oracle's jurisdiction.

        Parameters
        ----------
        domain:
            Domain name string to test (e.g. ``"arithmetic"``).

        Returns
        -------
        bool
            ``True`` iff *domain* is in :attr:`jurisdiction_scope` or the
            scope list is empty (meaning unrestricted).
        """
        if not self.jurisdiction_scope:
            return True
        result = domain.strip().lower() in [s.lower() for s in self.jurisdiction_scope]
        logger.debug(
            "OracleModel.validate_jurisdiction: domain=%r -> %s (oracle=%s)",
            domain,
            result,
            self.oracle_id,
        )
        return result

    def summary(self) -> dict:
        """Return a concise summary of this oracle's state.

        Returns
        -------
        dict
            Keys: ``oracle_id``, ``channel_kind``, ``is_active``,
            ``proposal_count``, ``audit_entry_count``,
            ``jurisdiction_scope``, ``last_activity``.
        """
        last_activity: float | None = None
        if self.audit_log:
            last_activity = self.audit_log[-1].get("timestamp")
        elif self.proposal_history:
            last_activity = self.proposal_history[-1].get("timestamp")

        return {
            "oracle_id": self.oracle_id,
            "channel_kind": self.channel_kind,
            "is_active": self.is_active,
            "proposal_count": len(self.proposal_history),
            "audit_entry_count": len(self.audit_log),
            "jurisdiction_scope": list(self.jurisdiction_scope),
            "last_activity": last_activity,
        }


# ---------------------------------------------------------------------------
# SolverFederationModel
# ---------------------------------------------------------------------------


@dataclass
class SolverFederationModel:
    """Registry of federated solver backends and their routing configuration.

    Formalises the SolverFederation concept from Theory2.tex Def 7.4.  The
    routing table maps LogicalFragment kind strings to backend solver IDs,
    and the merge_strategy controls how conflicting solver outputs are
    reconciled (Lem 7.6).

    Attributes
    ----------
    federation_id:
        UUID string identifying this federation instance.
    name:
        Human-readable name.
    member_solvers:
        Ordered list of registered solver IDs.
    routing_table:
        Mapping of fragment-kind strings to solver IDs.
    fragment_classifiers:
        List of classifier labels active in this federation.
    merge_strategy:
        :class:`MergeStrategy` enum value controlling result aggregation.
    created_at:
        Unix creation timestamp.
    stats:
        Mutable dict for tracking routing counts and performance metrics.
    """

    federation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "default_federation"
    member_solvers: list[str] = field(default_factory=list)
    routing_table: dict[str, str] = field(default_factory=dict)
    fragment_classifiers: list[str] = field(default_factory=list)
    merge_strategy: MergeStrategy = MergeStrategy.CONSERVATIVE
    created_at: float = field(default_factory=time.time)
    stats: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def add_solver(self, solver_id: str, jurisdiction: list[str]) -> None:
        """Register a solver and update the routing table.

        Each string in *jurisdiction* is treated as a fragment-kind or
        category label.  If the routing table has no entry for that label
        yet, the solver is registered as the primary handler.

        Parameters
        ----------
        solver_id:
            Unique identifier for the solver backend (e.g. ``"z3_v4_13"``).
        jurisdiction:
            List of fragment-kind strings this solver can handle.
        """
        solver_id = solver_id.strip()
        if not solver_id:
            raise ValueError("add_solver: solver_id must be non-empty")
        if solver_id not in self.member_solvers:
            self.member_solvers.append(solver_id)
            logger.debug(
                "SolverFederationModel.add_solver: registered %r (total members: %d)",
                solver_id,
                len(self.member_solvers),
            )
        for kind in jurisdiction:
            kind_key = kind.strip().lower()
            if kind_key and kind_key not in self.routing_table:
                self.routing_table[kind_key] = solver_id
                logger.debug(
                    "SolverFederationModel.add_solver: routing %r -> %r",
                    kind_key,
                    solver_id,
                )
        self.stats.setdefault("add_solver_calls", 0)
        self.stats["add_solver_calls"] += 1

    def route_fragment(self, fragment_kind: str) -> str:
        """Look up the routing table for *fragment_kind*.

        Falls back to the first registered member solver if no explicit
        routing entry exists.  Returns an empty string if no members are
        registered.

        Parameters
        ----------
        fragment_kind:
            Fragment kind string (e.g. ``"arithmetic_linear"``).

        Returns
        -------
        str
            Solver ID of the selected backend.
        """
        key = fragment_kind.strip().lower()
        if key in self.routing_table:
            selected = self.routing_table[key]
        elif self.member_solvers:
            selected = self.member_solvers[0]
            logger.debug(
                "route_fragment: no explicit route for %r, falling back to %r",
                fragment_kind,
                selected,
            )
        else:
            logger.warning(
                "route_fragment: no members in federation %r, cannot route %r",
                self.name,
                fragment_kind,
            )
            return ""

        self.stats.setdefault("route_calls", 0)
        self.stats["route_calls"] += 1
        return selected

    def classify(self, fragment_kind: str) -> str:
        """Classify a fragment kind string into a solver category.

        Uses the module-level :func:`_classify_fragment` helper to map
        *fragment_kind* to one of ``"arithmetic"``, ``"structural"``,
        ``"behavioral"``, or ``"hybrid"``.

        Parameters
        ----------
        fragment_kind:
            Raw fragment kind descriptor.

        Returns
        -------
        str
            Category label.
        """
        category = _classify_fragment(fragment_kind)
        if category not in self.fragment_classifiers:
            self.fragment_classifiers.append(category)
        return category

    def merge_results(self, results: list[dict]) -> dict:
        """Merge a list of solver result dicts using :attr:`merge_strategy`.

        Parameters
        ----------
        results:
            List of result dicts, each expected to contain at least a
            ``"claims"`` key (list of str) and a ``"trust_level"`` key.

        Returns
        -------
        dict
            Merged result dict with keys ``"claims"``, ``"trust_level"``,
            ``"strategy_used"``, ``"source_count"``.
        """
        if not results:
            return {"claims": [], "trust_level": "UNVERIFIED", "strategy_used": self.merge_strategy.value, "source_count": 0}

        strategy = self.merge_strategy
        all_claims: list[list[str]] = [r.get("claims", []) for r in results]
        trust_levels: list[str] = [r.get("trust_level", "UNVERIFIED") for r in results]

        if strategy == MergeStrategy.CONSERVATIVE:
            # Intersection of claims; lowest trust level
            if all_claims:
                merged_claims = list(set(all_claims[0]).intersection(*[set(c) for c in all_claims[1:]]))
            else:
                merged_claims = []
            merged_trust = min(trust_levels, key=lambda t: t) if trust_levels else "UNVERIFIED"

        elif strategy == MergeStrategy.OPTIMISTIC:
            # Union of claims; highest trust level
            merged_claims_set: set[str] = set()
            for c in all_claims:
                merged_claims_set.update(c)
            merged_claims = sorted(merged_claims_set)
            merged_trust = max(trust_levels, key=lambda t: t) if trust_levels else "UNVERIFIED"

        elif strategy == MergeStrategy.INTERSECTION:
            if all_claims:
                merged_claims = list(set(all_claims[0]).intersection(*[set(c) for c in all_claims[1:]]))
            else:
                merged_claims = []
            merged_trust = trust_levels[0] if trust_levels else "UNVERIFIED"

        elif strategy == MergeStrategy.UNION:
            merged_claims_set_u: set[str] = set()
            for c in all_claims:
                merged_claims_set_u.update(c)
            merged_claims = sorted(merged_claims_set_u)
            merged_trust = trust_levels[0] if trust_levels else "UNVERIFIED"

        elif strategy == MergeStrategy.WEIGHTED:
            weights = self.stats.get("solver_weights", {})
            weighted: dict[str, float] = {}
            for idx, (claims, trust) in enumerate(zip(all_claims, trust_levels)):
                solver_id = self.member_solvers[idx] if idx < len(self.member_solvers) else f"solver_{idx}"
                weight = weights.get(solver_id, 1.0)
                for claim in claims:
                    weighted[claim] = weighted.get(claim, 0.0) + weight
            threshold = sum(weights.values()) / 2.0 if weights else len(results) / 2.0
            merged_claims = sorted(k for k, v in weighted.items() if v >= threshold)
            merged_trust = trust_levels[0] if trust_levels else "UNVERIFIED"

        else:
            merged_claims = []
            merged_trust = "UNVERIFIED"

        logger.debug(
            "merge_results: strategy=%s sources=%d merged_claims=%d",
            strategy.value,
            len(results),
            len(merged_claims),
        )
        return {
            "claims": merged_claims,
            "trust_level": merged_trust,
            "strategy_used": strategy.value,
            "source_count": len(results),
        }

    def get_routing_table(self) -> dict:
        """Return a copy of the routing table.

        Returns
        -------
        dict[str, str]
            Copy of :attr:`routing_table`.
        """
        return dict(self.routing_table)

    def federation_status(self) -> dict:
        """Return a status summary of the federation.

        Returns
        -------
        dict
            Keys: ``federation_id``, ``name``, ``member_count``,
            ``routing_entries``, ``merge_strategy``, ``stats``.
        """
        return {
            "federation_id": self.federation_id,
            "name": self.name,
            "member_count": len(self.member_solvers),
            "member_solvers": list(self.member_solvers),
            "routing_entries": len(self.routing_table),
            "merge_strategy": self.merge_strategy.value,
            "fragment_classifiers": list(self.fragment_classifiers),
            "stats": dict(self.stats),
        }


# ---------------------------------------------------------------------------
# RuntimeWitnessModel
# ---------------------------------------------------------------------------


@dataclass
class RuntimeWitnessModel:
    """Container for a single runtime witness (Theory2.tex Def 7.8).

    A runtime witness is concrete evidence collected from program execution
    that certifies a predicate holds at a given program point.  The
    WitnessAdequacy theorem (Thm 7.9) requires that the witness contains
    enough information to reconstruct the relevant program state.

    Attributes
    ----------
    witness_id:
        UUID string identifying this witness.
    witness_kind:
        :class:`WitnessKind` indicating the collection mechanism.
    payload:
        Arbitrary key-value payload holding collected data.
    trust_tier:
        String label for the trust tier (``"runtime_witnessed"`` by default).
    collection_time:
        Unix timestamp of witness collection.
    heap_snapshot:
        For HEAP witnesses: a dict representing the captured heap state.
    invariant_violations:
        List of invariant violation strings detected during validation.
    is_validated:
        Whether :meth:`validate` has been successfully called.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    witness_kind: WitnessKind = WitnessKind.HEAP
    payload: dict = field(default_factory=dict)
    trust_tier: str = "runtime_witnessed"
    collection_time: float = field(default_factory=time.time)
    heap_snapshot: dict = field(default_factory=dict)
    invariant_violations: list[str] = field(default_factory=list)
    is_validated: bool = False

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def collect(self, data: dict) -> None:
        """Populate :attr:`payload` and optionally :attr:`heap_snapshot`.

        If *data* contains a ``"heap"`` key, its value is also stored in
        :attr:`heap_snapshot`.  Sets ``collection_time`` to the current
        time and resets :attr:`is_validated` to ``False`` since the
        witness content has changed.

        Parameters
        ----------
        data:
            Arbitrary data dict collected from the runtime.
        """
        self.payload = dict(data)
        if "heap" in data and isinstance(data["heap"], dict):
            self.heap_snapshot = dict(data["heap"])
        self.collection_time = time.time()
        self.is_validated = False
        logger.debug(
            "RuntimeWitnessModel.collect: witness=%s kind=%s payload_keys=%s",
            self.witness_id,
            self.witness_kind.value,
            list(data.keys()),
        )

    def validate(self) -> bool:
        """Validate the witness for adequacy (Thm 7.9 pre-conditions).

        Checks:
        - :attr:`payload` is non-empty.
        - :attr:`witness_kind` is a valid :class:`WitnessKind`.
        - For HEAP witnesses, :attr:`heap_snapshot` must be non-empty.
        - Detects obvious invariant violations (logs but does not raise).

        Sets :attr:`is_validated` to ``True`` on success.

        Returns
        -------
        bool
            ``True`` iff the witness passes all adequacy checks.
        """
        self.invariant_violations = []
        if not self.payload:
            self.invariant_violations.append("payload is empty")
        if not isinstance(self.witness_kind, WitnessKind):
            self.invariant_violations.append(f"invalid witness_kind: {self.witness_kind!r}")
        if self.witness_kind == WitnessKind.HEAP and not self.heap_snapshot:
            self.invariant_violations.append("HEAP witness requires non-empty heap_snapshot")
        if self.invariant_violations:
            logger.warning(
                "RuntimeWitnessModel.validate: witness=%s violations=%s",
                self.witness_id,
                self.invariant_violations,
            )
            self.is_validated = False
            return False
        self.is_validated = True
        logger.debug("RuntimeWitnessModel.validate: witness=%s valid", self.witness_id)
        return True

    def serialize(self) -> dict:
        """Fully serialise this witness to a plain dict.

        Returns
        -------
        dict
            All fields as JSON-compatible types.
        """
        return {
            "witness_id": self.witness_id,
            "witness_kind": self.witness_kind.value,
            "payload": dict(self.payload),
            "trust_tier": self.trust_tier,
            "collection_time": self.collection_time,
            "heap_snapshot": dict(self.heap_snapshot),
            "invariant_violations": list(self.invariant_violations),
            "is_validated": self.is_validated,
        }

    def get_trust_assertion(self) -> dict:
        """Return a trust assertion dict for use in evidence pipelines.

        Returns
        -------
        dict
            Keys: ``witness_id``, ``trust_tier``, ``witness_kind``,
            ``is_validated``, ``assertion_time``.
        """
        return {
            "witness_id": self.witness_id,
            "trust_tier": self.trust_tier,
            "witness_kind": self.witness_kind.value,
            "is_validated": self.is_validated,
            "assertion_time": time.time(),
        }

    def compare_witnesses(self, other: RuntimeWitnessModel) -> dict:
        """Compute the structural diff between this and *other*'s payloads.

        Returns a dict indicating which keys are shared, added in *other*,
        removed in *other*, and which values differ.

        Parameters
        ----------
        other:
            Another :class:`RuntimeWitnessModel` to compare against.

        Returns
        -------
        dict
            Keys: ``keys_added``, ``keys_removed``, ``keys_shared``,
            ``value_diffs``, ``are_equal``.
        """
        self_keys = set(self.payload.keys())
        other_keys = set(other.payload.keys())

        keys_added = sorted(other_keys - self_keys)
        keys_removed = sorted(self_keys - other_keys)
        keys_shared = sorted(self_keys & other_keys)
        value_diffs: dict[str, dict] = {}
        for k in keys_shared:
            if self.payload[k] != other.payload[k]:
                value_diffs[k] = {
                    "self": self.payload[k],
                    "other": other.payload[k],
                }

        are_equal = (
            not keys_added
            and not keys_removed
            and not value_diffs
            and self.witness_kind == other.witness_kind
        )
        return {
            "keys_added": keys_added,
            "keys_removed": keys_removed,
            "keys_shared": keys_shared,
            "value_diffs": value_diffs,
            "are_equal": are_equal,
        }

    def to_evidence_response_dict(self) -> dict:
        """Return a dict suitable for constructing an EvidenceResponse.

        The returned dict uses the field names expected by EvidenceResponse
        (as documented in jugeo.evidence.channels).

        Returns
        -------
        dict
            Keys: ``request_id``, ``channel``, ``evidence_item``,
            ``trust_level``, ``latency_ms``, ``is_partial``,
            ``residuals``, ``provenance``.
        """
        return {
            "request_id": self.witness_id,
            "channel": f"runtime_witness_{self.witness_kind.value}",
            "evidence_item": dict(self.payload),
            "trust_level": self.trust_tier,
            "latency_ms": 0.0,
            "is_partial": not self.is_validated,
            "residuals": list(self.invariant_violations),
            "provenance": {
                "witness_id": self.witness_id,
                "witness_kind": self.witness_kind.value,
                "collection_time": self.collection_time,
                "heap_snapshot_size": len(self.heap_snapshot),
            },
        }


# ---------------------------------------------------------------------------
# JurisdictionModel
# ---------------------------------------------------------------------------


@dataclass
class JurisdictionModel:
    """Models the jurisdiction (scope of authority) of an oracle or solver.

    Corresponds to the jurisdiction formalisation in Theory2.tex §7.1 and
    §7.2.  A jurisdiction restricts which operations an oracle may address
    and caps the trust assertions it may make.

    Attributes
    ----------
    scope:
        Human-readable scope label (default ``"global"``).
    allowed_operations:
        List of operation strings permitted within this jurisdiction.
    trust_ceiling:
        String name of the maximum trust level (default
        ``"oracle_proposed"``).
    parent_jurisdiction:
        Optional parent scope for hierarchical jurisdiction modelling.
    constraints:
        Arbitrary additional constraints dict.
    is_strict:
        When ``True``, operations not in :attr:`allowed_operations` are
        rejected; when ``False``, unknown operations are allowed by default.
    """

    scope: str = "global"
    allowed_operations: list[str] = field(default_factory=list)
    trust_ceiling: str = "oracle_proposed"
    parent_jurisdiction: str | None = None
    constraints: dict = field(default_factory=dict)
    is_strict: bool = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def contains(self, operation: str) -> bool:
        """Check whether *operation* is within this jurisdiction.

        In strict mode, returns ``True`` only for explicitly listed
        operations.  In non-strict mode, returns ``True`` unless the
        operations list is non-empty *and* the operation is absent.

        Parameters
        ----------
        operation:
            Operation name string.

        Returns
        -------
        bool
        """
        op = operation.strip().lower()
        if not self.allowed_operations:
            return not self.is_strict
        in_list = op in [a.lower() for a in self.allowed_operations]
        if self.is_strict:
            return in_list
        return in_list

    def is_subset_of(self, other: JurisdictionModel) -> bool:
        """Return True iff this jurisdiction's operations are a subset of *other*'s.

        Parameters
        ----------
        other:
            Jurisdiction to compare against.

        Returns
        -------
        bool
        """
        if not self.allowed_operations:
            return True
        if not other.allowed_operations:
            return False
        self_ops = {o.lower() for o in self.allowed_operations}
        other_ops = {o.lower() for o in other.allowed_operations}
        return self_ops.issubset(other_ops)

    def intersect(self, other: JurisdictionModel) -> JurisdictionModel:
        """Return a new jurisdiction that is the intersection of *self* and *other*.

        The resulting scope is ``"<self.scope>∩<other.scope>"``, the
        allowed operations are the set intersection, and the trust ceiling
        is whichever is more restrictive (lexicographically smaller string).

        Parameters
        ----------
        other:
            Jurisdiction to intersect with.

        Returns
        -------
        JurisdictionModel
            New intersection jurisdiction.
        """
        self_ops = {o.lower() for o in self.allowed_operations}
        other_ops = {o.lower() for o in other.allowed_operations}
        if self_ops and other_ops:
            intersected = sorted(self_ops & other_ops)
        elif not self_ops:
            intersected = sorted(other_ops)
        else:
            intersected = sorted(self_ops)

        # More restrictive ceiling = lexicographically smaller (UNVERIFIED < oracle_proposed)
        ceiling = min(self.trust_ceiling, other.trust_ceiling)
        return JurisdictionModel(
            scope=f"{self.scope}∩{other.scope}",
            allowed_operations=intersected,
            trust_ceiling=ceiling,
            parent_jurisdiction=self.scope,
            constraints={**self.constraints, **other.constraints},
            is_strict=self.is_strict or other.is_strict,
        )

    def enforce(self, response_dict: dict) -> dict:
        """Apply this jurisdiction's trust_ceiling constraint to *response_dict*.

        Mutates a copy of *response_dict*, clamping ``trust_level`` to
        :attr:`trust_ceiling` and adding a ``jurisdiction_enforced`` key.

        Parameters
        ----------
        response_dict:
            Evidence response dict.

        Returns
        -------
        dict
            Copy with clamped trust_level and enforcement metadata.
        """
        result = dict(response_dict)
        result["trust_level"] = self.trust_ceiling
        result["jurisdiction_enforced"] = True
        result["jurisdiction_scope"] = self.scope
        logger.debug(
            "JurisdictionModel.enforce: scope=%r ceiling=%r",
            self.scope,
            self.trust_ceiling,
        )
        return result

    def to_constraints(self) -> dict:
        """Return a constraint representation of this jurisdiction.

        Returns
        -------
        dict
            Keys: ``scope``, ``trust_ceiling``, ``allowed_operations``,
            ``is_strict``, ``parent_jurisdiction``, ``constraints``.
        """
        return {
            "scope": self.scope,
            "trust_ceiling": self.trust_ceiling,
            "allowed_operations": list(self.allowed_operations),
            "is_strict": self.is_strict,
            "parent_jurisdiction": self.parent_jurisdiction,
            "constraints": dict(self.constraints),
        }

    def describe(self) -> str:
        """Return a human-readable one-line description of this jurisdiction.

        Returns
        -------
        str
        """
        ops = ", ".join(self.allowed_operations) or "(all)"
        strict = "strict" if self.is_strict else "lenient"
        parent = f" (sub of {self.parent_jurisdiction!r})" if self.parent_jurisdiction else ""
        return (
            f"Jurisdiction[{self.scope!r}{parent}] "
            f"ops=[{ops}] ceiling={self.trust_ceiling!r} mode={strict}"
        )


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OracleChannelConfig:
    """Configuration for an oracle channel (Theory2.tex §7.1).

    Attributes
    ----------
    max_proposals:
        Maximum number of proposals this oracle channel will accept
        before refusing further requests.
    timeout_ms:
        Timeout for each proposal in milliseconds.
    require_corroboration:
        Whether a second oracle must corroborate each proposal.
    audit_enabled:
        Whether to record audit entries for every action.
    allowed_domains:
        List of domain strings this channel is authorised for; empty
        means unrestricted.
    rejection_threshold:
        Minimum confidence score (0.0–1.0) below which proposals are
        rejected outright.
    """

    max_proposals: int = 100
    timeout_ms: float = 5000.0
    require_corroboration: bool = False
    audit_enabled: bool = True
    allowed_domains: list[str] = field(default_factory=list)
    rejection_threshold: float = 0.0


@dataclass
class FederationConfig:
    """Configuration for a solver federation (Theory2.tex §7.2).

    Attributes
    ----------
    max_members:
        Maximum number of solver backends that can join the federation.
    rebalance_interval_s:
        How often (in seconds) to rebalance routing weights.
    merge_strategy:
        Default :class:`MergeStrategy` for result aggregation.
    routing_cache_size:
        LRU cache size for the routing table lookup.
    enable_z3_routing:
        Whether to include Z3 as a routing target for arithmetic fragments.
    fallback_to_copilot:
        Whether to fall back to Copilot-oracle suggestions when all
        solver backends time out.
    """

    max_members: int = 10
    rebalance_interval_s: float = 300.0
    merge_strategy: MergeStrategy = MergeStrategy.CONSERVATIVE
    routing_cache_size: int = 1000
    enable_z3_routing: bool = True
    fallback_to_copilot: bool = False


@dataclass
class WitnessCollectionConfig:
    """Configuration for the runtime witness collection system (§7.3).

    Attributes
    ----------
    snapshot_interval_s:
        How often (in seconds) to capture heap snapshots.
    max_witnesses:
        Maximum number of witnesses to retain in memory.
    prune_after_s:
        Witnesses older than this many seconds are pruned from memory.
    validate_on_collect:
        Whether to call :meth:`RuntimeWitnessModel.validate` immediately
        after each collection.
    trust_tier:
        Trust tier label to assign to collected witnesses.
    collection_kinds:
        List of :class:`WitnessKind` value strings to collect.
    """

    snapshot_interval_s: float = 60.0
    max_witnesses: int = 1000
    prune_after_s: float = 3600.0
    validate_on_collect: bool = True
    trust_tier: str = "runtime_witnessed"
    collection_kinds: list[str] = field(
        default_factory=lambda: ["heap", "identity", "stack"]
    )


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Central registry for oracle_federation model instances.

    Provides thread-safe (at Python GIL level) registration and lookup of
    :class:`OracleModel`, :class:`SolverFederationModel`, and
    :class:`RuntimeWitnessModel` instances.

    Usage::

        registry = ModelRegistry()
        oracle = OracleModel()
        registry.register_oracle(oracle)
        retrieved = registry.get_oracle(oracle.oracle_id)
    """

    def __init__(self) -> None:
        """Initialise empty registries for each model type."""
        self._oracles: dict[str, OracleModel] = {}
        self._federations: dict[str, SolverFederationModel] = {}
        self._witnesses: dict[str, RuntimeWitnessModel] = {}
        logger.debug("ModelRegistry: initialised empty registry")

    # ------------------------------------------------------------------
    # Oracle management
    # ------------------------------------------------------------------

    def register_oracle(self, model: OracleModel) -> None:
        """Register an :class:`OracleModel` instance.

        Parameters
        ----------
        model:
            The oracle model to register.  Overwrites any existing entry
            with the same ``oracle_id``.
        """
        if not isinstance(model, OracleModel):
            raise TypeError(f"register_oracle expects OracleModel, got {type(model).__name__}")
        self._oracles[model.oracle_id] = model
        logger.debug("ModelRegistry.register_oracle: id=%s", model.oracle_id)

    def get_oracle(self, oracle_id: str) -> OracleModel | None:
        """Look up a registered oracle by ID.

        Parameters
        ----------
        oracle_id:
            UUID string identifying the oracle.

        Returns
        -------
        OracleModel | None
            The registered model, or ``None`` if not found.
        """
        result = self._oracles.get(oracle_id)
        if result is None:
            logger.debug("ModelRegistry.get_oracle: id=%s not found", oracle_id)
        return result

    # ------------------------------------------------------------------
    # Federation management
    # ------------------------------------------------------------------

    def register_federation(self, model: SolverFederationModel) -> None:
        """Register a :class:`SolverFederationModel` instance.

        Parameters
        ----------
        model:
            The federation model to register.
        """
        if not isinstance(model, SolverFederationModel):
            raise TypeError(
                f"register_federation expects SolverFederationModel, got {type(model).__name__}"
            )
        self._federations[model.federation_id] = model
        logger.debug("ModelRegistry.register_federation: id=%s", model.federation_id)

    def get_federation(self, federation_id: str) -> SolverFederationModel | None:
        """Look up a registered federation by ID.

        Parameters
        ----------
        federation_id:
            UUID string identifying the federation.

        Returns
        -------
        SolverFederationModel | None
        """
        result = self._federations.get(federation_id)
        if result is None:
            logger.debug("ModelRegistry.get_federation: id=%s not found", federation_id)
        return result

    # ------------------------------------------------------------------
    # Witness management
    # ------------------------------------------------------------------

    def register_witness(self, model: RuntimeWitnessModel) -> None:
        """Register a :class:`RuntimeWitnessModel` instance.

        Parameters
        ----------
        model:
            The witness model to register.
        """
        if not isinstance(model, RuntimeWitnessModel):
            raise TypeError(
                f"register_witness expects RuntimeWitnessModel, got {type(model).__name__}"
            )
        self._witnesses[model.witness_id] = model
        logger.debug("ModelRegistry.register_witness: id=%s kind=%s", model.witness_id, model.witness_kind.value)

    def get_witness(self, witness_id: str) -> RuntimeWitnessModel | None:
        """Look up a registered witness by ID.

        Parameters
        ----------
        witness_id:
            UUID string identifying the witness.

        Returns
        -------
        RuntimeWitnessModel | None
        """
        result = self._witnesses.get(witness_id)
        if result is None:
            logger.debug("ModelRegistry.get_witness: id=%s not found", witness_id)
        return result

    # ------------------------------------------------------------------
    # Aggregate operations
    # ------------------------------------------------------------------

    def list_all(self) -> dict:
        """Return a summary dict with counts and IDs for all registered models.

        Returns
        -------
        dict
            Keys: ``oracles``, ``federations``, ``witnesses``, each mapping
            to ``{"count": int, "ids": list[str]}``.
        """
        return {
            "oracles": {
                "count": len(self._oracles),
                "ids": sorted(self._oracles.keys()),
            },
            "federations": {
                "count": len(self._federations),
                "ids": sorted(self._federations.keys()),
            },
            "witnesses": {
                "count": len(self._witnesses),
                "ids": sorted(self._witnesses.keys()),
            },
        }

    def clear(self) -> None:
        """Clear all registered models from all registries."""
        oracle_count = len(self._oracles)
        fed_count = len(self._federations)
        wit_count = len(self._witnesses)
        self._oracles.clear()
        self._federations.clear()
        self._witnesses.clear()
        logger.debug(
            "ModelRegistry.clear: removed %d oracles, %d federations, %d witnesses",
            oracle_count,
            fed_count,
            wit_count,
        )

    def to_dict(self) -> dict:
        """Serialise the entire registry to a plain dictionary.

        Returns
        -------
        dict
            Keys: ``oracles``, ``federations``, ``witnesses`` — each
            mapping model IDs to their :meth:`summary`/:meth:`federation_status`
            / :meth:`serialize` representations.
        """
        return {
            "oracles": {oid: m.summary() for oid, m in self._oracles.items()},
            "federations": {fid: m.federation_status() for fid, m in self._federations.items()},
            "witnesses": {wid: m.serialize() for wid, m in self._witnesses.items()},
        }


# ---------------------------------------------------------------------------
# Cross-referencing helpers — Theory2.tex §7 (Oracle Federation)
# ---------------------------------------------------------------------------


def model_descent_bridge(model):
    """Map an oracle model to a descent context for site-level reasoning.

    Constructs a :class:`~jugeo.geometry.descent.LocalSection` per oracle
    entry using the :class:`~jugeo.geometry.descent.DescentStrategy` and
    :class:`~jugeo.geometry.site.Coordinate` classes, bridging the oracle
    federation layer with the descent engine.

    See Theory2.tex §7 (Oracle Federation) for descent integration.

    Parameters
    ----------
    model : dict
        Oracle model dict; each value should contain ``"coordinate"`` and
        optionally ``"trust_level"`` and ``"strategy"`` fields.

    Returns
    -------
    dict
        Descent mapping with ``sections``, ``strategy``, and ``count`` keys.
    """
    try:
        from jugeo.geometry.descent import LocalSection, DescentStrategy
        from jugeo.geometry.site import Coordinate
    except ImportError:
        logger.warning("model_descent_bridge: geometry modules unavailable")
        return {"sections": {}, "strategy": None, "count": 0, "error": "missing_geometry"}

    strategy = DescentStrategy.ITERATIVE
    sections = {}
    for key, entry in (model or {}).items():
        coord_name = entry.get("coordinate", key)
        trust = float(entry.get("trust_level", 1.0))
        section = LocalSection(coordinate=coord_name, trust_level=trust)
        sections[key] = {
            "section": section,
            "coordinate": Coordinate(components=(coord_name,)),
            "trust_level": trust,
        }
    logger.debug("model_descent_bridge: built %d sections with strategy=%s", len(sections), strategy.value)
    return {"sections": sections, "strategy": strategy.value, "count": len(sections)}


def model_judgment_bridge(model):
    """Convert an oracle model into a judgment record with encoding.

    Wraps each oracle entry in a :class:`~jugeo.judgments.judgment_terms.Proposition`,
    assigns :class:`~jugeo.judgments.judgment_terms.JudgmentStatus`, and
    encodes the result via :func:`~jugeo.encodings.encode_judgment`.

    See Theory2.tex §7 (Oracle Federation) for judgment bridge semantics.

    Parameters
    ----------
    model : dict
        Oracle model dict; each value should contain ``"formula"`` and
        optionally ``"kind"`` fields.

    Returns
    -------
    dict
        Judgment dict with ``judgments``, ``encoded_count``, and ``errors`` keys.
    """
    try:
        from jugeo.judgments.judgment_terms import Proposition, JudgmentStatus
        from jugeo.encodings import encode_judgment
    except ImportError:
        logger.warning("model_judgment_bridge: judgment/encoding modules unavailable")
        return {"judgments": {}, "encoded_count": 0, "errors": ["missing_modules"]}

    judgments = {}
    errors = []
    for key, entry in (model or {}).items():
        formula = entry.get("formula", "")
        kind_str = entry.get("kind", "structural")
        prop = Proposition(kind=kind_str, formula=formula)
        status = JudgmentStatus.PROPOSED
        try:
            encoded = encode_judgment(entry)
        except Exception as exc:  # noqa: BLE001
            logger.error("model_judgment_bridge: encoding failed for %s: %s", key, exc)
            encoded = None
            errors.append(f"{key}: {exc}")
        judgments[key] = {"proposition": prop, "status": status.value, "encoded": encoded}
    logger.debug("model_judgment_bridge: produced %d judgments, %d errors", len(judgments), len(errors))
    return {"judgments": judgments, "encoded_count": len(judgments) - len(errors), "errors": errors}
