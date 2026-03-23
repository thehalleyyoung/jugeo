"""
Implementation consequences of federated discovery in jugeo.

# copilot: implementation_consequences — models the design and runtime
# consequences of federated discovery, enforces federation policies, tracks
# node health, consensus, and constraint violations. This module provides the
# formal machinery for federation lifecycle management, including node
# registration, policy enforcement, consensus protocol, consequence derivation,
# health monitoring, audit logging, and violation tracking. All data structures
# are frozen dataclasses with slots for immutability and performance. The module
# exposes both class-based (FederationPolicy, FederationConsensus) and
# functional interfaces for deriving and enforcing federation consequences.
# Federation judgments follow the canonical 8-tuple (c, φ, A, E, O, B, T, Π).
"""

from __future__ import annotations

import uuid
import datetime
import math
import itertools
import functools
import logging
import json
import hashlib
import random
import copy
import collections
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------
try:
    from jugeo.core import context as jugeo_context  # type: ignore
except ImportError:
    jugeo_context = None

try:
    from jugeo.core import evidence as jugeo_evidence  # type: ignore
except ImportError:
    jugeo_evidence = None

try:
    from jugeo.federation import registry as jugeo_fed_registry  # type: ignore
except ImportError:
    jugeo_fed_registry = None

try:
    from jugeo.ideation import registry as jugeo_registry  # type: ignore
except ImportError:
    jugeo_registry = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a compact 16-char unique identifier."""
    return uuid.uuid4().hex[:16]


def _hash_str(s: str) -> str:
    """Return a short SHA-256 digest of *s*."""
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _json_safe(obj: Any) -> Any:
    """Recursively convert obj to a JSON-serialisable form."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------

class TrustTier(Enum):
    """Ordered epistemic trust tiers for federation artifacts.

    PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED
    """
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5


# ---------------------------------------------------------------------------
# Judgment
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FederationJudgment:
    """An 8-tuple judgment in the federation subsystem.

    Fields: (context, formula, authority, evidence, obligations, budget, trust_tier, proof_chain)
    """
    context: str
    formula: str
    authority: str
    evidence: Any
    obligations: tuple
    budget: float
    trust_tier: TrustTier
    proof_chain: tuple


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FederatedDiscoveryConsequence:
    """A formally modelled consequence of a federation design decision.

    Attributes
    ----------
    consequence_id:
        Unique identifier.
    description:
        Human-readable description of the consequence.
    federation_aspect:
        Which aspect of federation this consequence relates to
        (e.g. 'consensus', 'replication', 'latency', 'trust').
    design_implication:
        The specific design implication that must be addressed.
    affected_nodes:
        Tuple of node identifiers that are directly affected.
    severity:
        Normalised severity score (0.0 – 1.0).
    trust_tier:
        The trust tier at which this consequence was identified.
    created_at:
        ISO-8601 creation timestamp.
    """
    consequence_id: str
    description: str
    federation_aspect: str
    design_implication: str
    affected_nodes: tuple
    severity: float
    trust_tier: TrustTier
    created_at: str


@dataclass(frozen=True, slots=True)
class FederationConstraint:
    """A formal constraint that federation members must satisfy.

    Attributes
    ----------
    constraint_id:
        Unique identifier.
    name:
        Short human-readable name.
    formal_expression:
        Logical or contractual expression of the constraint.
    scope:
        Whether the constraint applies 'globally' or 'locally'.
    is_global:
        Convenience boolean derived from *scope*.
    violation_cost:
        Normalised cost incurred when the constraint is violated.
    created_at:
        ISO-8601 creation timestamp.
    """
    constraint_id: str
    name: str
    formal_expression: str
    scope: str
    is_global: bool
    violation_cost: float
    created_at: str


@dataclass(frozen=True, slots=True)
class FederationNode:
    """A participant node in the jugeo discovery federation.

    Attributes
    ----------
    node_id:
        Unique node identifier.
    name:
        Human-readable name.
    role:
        Node role: 'coordinator', 'worker', 'observer', 'gateway'.
    capabilities:
        Tuple of capability strings this node provides.
    trust_tier:
        The node's current trust tier.
    endpoint:
        Network endpoint (URI or address).
    joined_at:
        ISO-8601 timestamp when the node joined the federation.
    """
    node_id: str
    name: str
    role: str
    capabilities: tuple
    trust_tier: TrustTier
    endpoint: str
    joined_at: str


@dataclass(frozen=True, slots=True)
class FederationRecord:
    """An immutable log entry for a federation event.

    Attributes
    ----------
    record_id:
        Unique identifier for this record.
    federation_id:
        Identifies the federation instance this event belongs to.
    event_type:
        Type of event: 'join', 'leave', 'proposal', 'vote', 'consequence'.
    payload:
        JSON-serialised event payload.
    participants:
        Tuple of node identifiers that participated in the event.
    timestamp:
        ISO-8601 event timestamp.
    """
    record_id: str
    federation_id: str
    event_type: str
    payload: str
    participants: tuple
    timestamp: str


@dataclass(frozen=True, slots=True)
class NodeHealth:
    """A health snapshot for a federation node.

    Attributes
    ----------
    node_id:
        The node being assessed.
    latency_ms:
        Round-trip latency to the node in milliseconds.
    uptime_ratio:
        Observed uptime fraction (0.0 – 1.0).
    last_seen:
        ISO-8601 timestamp of the most recent successful contact.
    error_count:
        Number of errors recorded since the last health reset.
    health_score:
        Composite health score (0.0 – 1.0; higher is healthier).
    checked_at:
        ISO-8601 timestamp of this health check.
    """
    node_id: str
    latency_ms: float
    uptime_ratio: float
    last_seen: str
    error_count: int
    health_score: float
    checked_at: str


@dataclass(frozen=True, slots=True)
class ConsensusProposal:
    """A proposal submitted for federation-wide consensus.

    Attributes
    ----------
    proposal_id:
        Unique identifier.
    proposer_id:
        Node that submitted the proposal.
    content:
        The proposed change or decision, as a free-form string.
    votes_for:
        Tuple of node IDs that voted in favour.
    votes_against:
        Tuple of node IDs that voted against.
    status:
        Current status: 'open', 'accepted', 'rejected', 'expired'.
    proposed_at:
        ISO-8601 submission timestamp.
    """
    proposal_id: str
    proposer_id: str
    content: str
    votes_for: tuple
    votes_against: tuple
    status: str
    proposed_at: str


@dataclass(frozen=True, slots=True)
class FederationScope:
    """Defines the scope of a sub-group within the federation.

    Attributes
    ----------
    scope_id:
        Unique identifier.
    name:
        Human-readable scope name.
    member_nodes:
        Tuple of node IDs in this scope.
    governance_rule:
        The governance rule applying to this scope (e.g. 'majority').
    authority_level:
        Integer authority level (higher = more authority).
    created_at:
        ISO-8601 creation timestamp.
    """
    scope_id: str
    name: str
    member_nodes: tuple
    governance_rule: str
    authority_level: int
    created_at: str


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """Records a detected policy violation.

    Attributes
    ----------
    violation_id:
        Unique identifier for this violation.
    policy_name:
        Name of the violated policy.
    node_id:
        Node responsible for the violation.
    description:
        Human-readable description of what was violated.
    severity:
        Normalised severity (0.0 – 1.0).
    detected_at:
        ISO-8601 detection timestamp.
    """
    violation_id: str
    policy_name: str
    node_id: str
    description: str
    severity: float
    detected_at: str


@dataclass(frozen=True, slots=True)
class FederationAuditEntry:
    """An immutable audit log entry for federation actions.

    Attributes
    ----------
    entry_id:
        Unique identifier.
    action:
        The action that was audited.
    actor_id:
        Node or system that performed the action.
    target_id:
        The node or resource targeted by the action.
    outcome:
        Result of the action: 'success', 'failure', 'partial'.
    audit_at:
        ISO-8601 timestamp.
    """
    entry_id: str
    action: str
    actor_id: str
    target_id: str
    outcome: str
    audit_at: str


@dataclass(frozen=True, slots=True)
class ConsequenceChain:
    """Models a chain of causally linked federation consequences.

    Attributes
    ----------
    chain_id:
        Unique identifier for this consequence chain.
    root_consequence_id:
        The initiating consequence that triggered the chain.
    derived_consequences:
        Tuple of consequence IDs derived from the root.
    propagation_depth:
        How many steps of propagation have occurred.
    created_at:
        ISO-8601 creation timestamp.
    """
    chain_id: str
    root_consequence_id: str
    derived_consequences: tuple
    propagation_depth: int
    created_at: str


# ---------------------------------------------------------------------------
# FederationPolicy class
# ---------------------------------------------------------------------------

class FederationPolicy:
    """Manages and enforces federation policies.

    A FederationPolicy instance holds a collection of named policies, each
    described by a rule string and a scope. Policies are enforced against
    FederationRecord events and FederationNode states. Violations are tracked
    internally and surfaced via get_policy_violations() and generate_policy_report().

    Internal state
    --------------
    _policies : dict[str, dict]
        Registered policies keyed by name.
    _violations : list[PolicyViolation]
        Detected violations accumulated across enforce() calls.
    _audit_log : list[FederationAuditEntry]
        Audit entries for all enforcement decisions.
    _compliance_cache : dict[str, FederationJudgment]
        Cache of compliance judgments per node.
    """

    def __init__(self) -> None:
        """Initialise an empty FederationPolicy manager."""
        self._policies: dict[str, dict] = {}
        self._violations: list[PolicyViolation] = []
        self._audit_log: list[FederationAuditEntry] = []
        self._compliance_cache: dict[str, FederationJudgment] = {}
        logger.debug("FederationPolicy initialised.")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def register_policy(self, name: str, rule: str, scope: str) -> "FederationPolicy":
        """Register a new policy and return self for method chaining.

        Parameters
        ----------
        name:
            Unique policy name.
        rule:
            Formal or natural-language rule expression.
        scope:
            'global' or a specific node/scope identifier.

        Returns
        -------
        FederationPolicy
            Self, for fluent chaining.
        """
        self._policies[name] = {
            "name": name,
            "rule": rule,
            "scope": scope,
            "registered_at": _now_iso(),
            "violation_count": 0,
        }
        logger.info("Registered policy '%s' (scope=%s).", name, scope)
        return self

    def enforce(
        self,
        discovery_record: FederationRecord,
        federated_nodes: list[FederationNode],
    ) -> FederationJudgment:
        """Enforce all policies against a FederationRecord event.

        Each registered policy is evaluated. A PolicyViolation is recorded
        for each policy whose rule is not satisfied by the record/nodes
        combination. The returned judgment summarises overall compliance.

        Parameters
        ----------
        discovery_record:
            The event to evaluate policies against.
        federated_nodes:
            Current federation node list for context.

        Returns
        -------
        FederationJudgment
            Summary judgment encoding compliance level.
        """
        violations_before = len(self._violations)
        for policy_name, policy in self._policies.items():
            violated = self._evaluate_policy(policy, discovery_record, federated_nodes)
            if violated:
                v = PolicyViolation(
                    violation_id=_uid(),
                    policy_name=policy_name,
                    node_id=discovery_record.participants[0] if discovery_record.participants else "unknown",
                    description=f"Policy '{policy_name}' violated by event {discovery_record.event_type}",
                    severity=self._compute_severity(policy),
                    detected_at=_now_iso(),
                )
                self._violations.append(v)
                self._policies[policy_name]["violation_count"] += 1

        new_violations = len(self._violations) - violations_before
        compliance_score = self._compute_compliance_score()
        tier = self._score_to_tier(compliance_score)

        entry = FederationAuditEntry(
            entry_id=_uid(),
            action="enforce",
            actor_id="FederationPolicy",
            target_id=discovery_record.record_id,
            outcome="success" if new_violations == 0 else "partial",
            audit_at=_now_iso(),
        )
        self._audit_log.append(entry)

        return FederationJudgment(
            context=f"federation_policy:{discovery_record.federation_id}",
            formula=f"compliant({discovery_record.record_id}) @ {compliance_score:.4f}",
            authority="FederationPolicy",
            evidence={
                "compliance_score": compliance_score,
                "new_violations": new_violations,
                "total_violations": len(self._violations),
                "policies_evaluated": len(self._policies),
            },
            obligations=tuple(v.violation_id for v in self._violations[-new_violations:]),
            budget=float(len(self._policies)),
            trust_tier=tier,
            proof_chain=(entry.entry_id,),
        )

    def check_compliance(self, node_id: str) -> FederationJudgment:
        """Return a compliance judgment for a specific node.

        Parameters
        ----------
        node_id:
            The node to assess.

        Returns
        -------
        FederationJudgment
            Compliance judgment for the node.
        """
        if node_id in self._compliance_cache:
            return self._compliance_cache[node_id]
        node_violations = [v for v in self._violations if v.node_id == node_id]
        violation_count = len(node_violations)
        total_severity = sum(v.severity for v in node_violations)
        compliance_score = _clamp(1.0 - total_severity / max(1, violation_count * 2), 0.0, 1.0)
        if violation_count == 0:
            compliance_score = 1.0
        tier = self._score_to_tier(compliance_score)
        judgment = FederationJudgment(
            context=f"node_compliance:{node_id}",
            formula=f"compliant({node_id}) @ {compliance_score:.4f}",
            authority="FederationPolicy",
            evidence={
                "node_id": node_id,
                "violation_count": violation_count,
                "total_severity": total_severity,
            },
            obligations=tuple(v.violation_id for v in node_violations),
            budget=float(violation_count),
            trust_tier=tier,
            proof_chain=tuple(v.violation_id for v in node_violations),
        )
        self._compliance_cache[node_id] = judgment
        return judgment

    def get_policy_violations(self) -> list[dict]:
        """Return all recorded violations as a list of dicts.

        Returns
        -------
        list[dict]
            Each dict contains violation metadata.
        """
        return [
            {
                "violation_id": v.violation_id,
                "policy_name": v.policy_name,
                "node_id": v.node_id,
                "description": v.description,
                "severity": v.severity,
                "detected_at": v.detected_at,
            }
            for v in self._violations
        ]

    def generate_policy_report(self) -> dict:
        """Generate a comprehensive policy enforcement report.

        Returns
        -------
        dict
            Report containing policy counts, violation statistics, audit log
            summary, and compliance overview.
        """
        violation_by_policy: dict[str, int] = collections.Counter(
            v.policy_name for v in self._violations
        )
        violation_by_node: dict[str, int] = collections.Counter(
            v.node_id for v in self._violations
        )
        return {
            "total_policies": len(self._policies),
            "total_violations": len(self._violations),
            "violation_by_policy": dict(violation_by_policy),
            "violation_by_node": dict(violation_by_node),
            "audit_entries": len(self._audit_log),
            "overall_compliance_score": self._compute_compliance_score(),
            "generated_at": _now_iso(),
        }

    def get_audit_log(self) -> list[dict]:
        """Return the audit log as a list of dicts."""
        return [
            {
                "entry_id": e.entry_id,
                "action": e.action,
                "actor_id": e.actor_id,
                "target_id": e.target_id,
                "outcome": e.outcome,
                "audit_at": e.audit_at,
            }
            for e in self._audit_log
        ]

    def clear_violations(self) -> None:
        """Clear all recorded violations and reset compliance caches."""
        self._violations.clear()
        self._compliance_cache.clear()
        logger.debug("Violations and compliance cache cleared.")

    def get_policy_names(self) -> list[str]:
        """Return the names of all registered policies."""
        return list(self._policies.keys())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_policy(
        self,
        policy: dict,
        record: FederationRecord,
        nodes: list[FederationNode],
    ) -> bool:
        """Evaluate whether *policy* is violated by *record* given *nodes*.

        Uses a heuristic: if the policy rule mentions a keyword that appears
        in the record's event_type, the policy is checked more strictly.

        Returns True if the policy is violated.
        """
        rule = policy.get("rule", "").lower()
        event_type = record.event_type.lower()
        # Simple keyword-based heuristic evaluation
        risky_events = {"leave", "rejection", "error", "timeout", "split"}
        if event_type in risky_events and ("no_partition" in rule or "availability" in rule):
            return True
        if len(nodes) < 2 and "quorum" in rule:
            return True
        if record.participants and len(record.participants) == 0 and "participation" in rule:
            return True
        return False

    def _compute_severity(self, policy: dict) -> float:
        """Compute violation severity for a policy."""
        base = 0.5
        scope = policy.get("scope", "local")
        if scope == "global":
            base += 0.3
        vc = policy.get("violation_count", 0)
        repeat_penalty = min(0.2, vc * 0.02)
        return _clamp(base + repeat_penalty, 0.0, 1.0)

    def _compute_compliance_score(self) -> float:
        """Compute an overall compliance score for the federation."""
        if not self._policies:
            return 1.0
        total_violations = len(self._violations)
        if total_violations == 0:
            return 1.0
        penalty = min(1.0, total_violations * 0.1)
        return _clamp(1.0 - penalty, 0.0, 1.0)

    def _score_to_tier(self, score: float) -> TrustTier:
        """Map a compliance score to a TrustTier."""
        if score >= 0.95:
            return TrustTier.PROOF_BACKED
        if score >= 0.80:
            return TrustTier.RUNTIME_WITNESSED
        if score >= 0.60:
            return TrustTier.VERIFIED
        if score >= 0.35:
            return TrustTier.REVIEWED
        return TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# FederationConsensus class
# ---------------------------------------------------------------------------

class FederationConsensus:
    """Manages the consensus protocol for federation-wide decisions.

    Implements a simple majority-vote consensus over ConsensusProposal objects.
    The class tracks open proposals, records votes, and resolves proposals once
    a quorum is reached. All resolutions produce FederationJudgment records.

    Internal state
    --------------
    _proposals : dict[str, ConsensusProposal]
        All proposals keyed by proposal_id.
    _quorum_threshold : float
        Fraction of participating nodes required for a decision (default 0.5).
    _resolution_log : list[dict]
        Log of resolved proposals with their outcomes.
    """

    def __init__(self, quorum_threshold: float = 0.5) -> None:
        """Initialise a FederationConsensus manager.

        Parameters
        ----------
        quorum_threshold:
            Fraction of members required to constitute a quorum (default 0.5).
        """
        self._proposals: dict[str, ConsensusProposal] = {}
        self._quorum_threshold = _clamp(quorum_threshold, 0.0, 1.0)
        self._resolution_log: list[dict] = []
        logger.debug("FederationConsensus initialised (quorum=%.2f).", quorum_threshold)

    def propose(self, proposer_id: str, content: str) -> ConsensusProposal:
        """Submit a new proposal for consensus.

        Parameters
        ----------
        proposer_id:
            Node submitting the proposal.
        content:
            The proposal content.

        Returns
        -------
        ConsensusProposal
            The newly created proposal.
        """
        proposal = ConsensusProposal(
            proposal_id=_uid(),
            proposer_id=proposer_id,
            content=content,
            votes_for=(),
            votes_against=(),
            status="open",
            proposed_at=_now_iso(),
        )
        self._proposals[proposal.proposal_id] = proposal
        logger.info("Proposal %s submitted by %s.", proposal.proposal_id, proposer_id)
        return proposal

    def vote(self, proposal_id: str, voter_id: str, in_favour: bool) -> ConsensusProposal:
        """Record a vote on an open proposal.

        Parameters
        ----------
        proposal_id:
            The proposal to vote on.
        voter_id:
            The voting node.
        in_favour:
            True for a vote in favour; False for a vote against.

        Returns
        -------
        ConsensusProposal
            Updated proposal with the vote recorded.

        Raises
        ------
        KeyError
            If *proposal_id* is not found.
        ValueError
            If the proposal is not open.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id!r} not found.")
        if proposal.status != "open":
            raise ValueError(f"Proposal {proposal_id!r} is {proposal.status!r}, not open.")

        if in_favour:
            new_for = tuple(set(proposal.votes_for) | {voter_id})
            new_against = proposal.votes_against
        else:
            new_for = proposal.votes_for
            new_against = tuple(set(proposal.votes_against) | {voter_id})

        updated = ConsensusProposal(
            proposal_id=proposal.proposal_id,
            proposer_id=proposal.proposer_id,
            content=proposal.content,
            votes_for=new_for,
            votes_against=new_against,
            status=proposal.status,
            proposed_at=proposal.proposed_at,
        )
        self._proposals[proposal_id] = updated
        return updated

    def resolve(
        self, proposal_id: str, eligible_nodes: list[FederationNode]
    ) -> FederationJudgment:
        """Attempt to resolve an open proposal given current node eligibility.

        If enough votes are in to satisfy the quorum, the proposal is
        accepted or rejected based on majority. Otherwise it remains open.

        Parameters
        ----------
        proposal_id:
            The proposal to resolve.
        eligible_nodes:
            Nodes eligible to participate in consensus.

        Returns
        -------
        FederationJudgment
            Judgment encoding the resolution outcome.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return _error_judgment(f"proposal_not_found:{proposal_id}")

        n_eligible = len(eligible_nodes)
        n_for = len(proposal.votes_for)
        n_against = len(proposal.votes_against)
        n_votes = n_for + n_against
        quorum_met = (n_votes / max(1, n_eligible)) >= self._quorum_threshold

        if quorum_met:
            outcome = "accepted" if n_for > n_against else "rejected"
        else:
            outcome = "pending"

        if outcome in ("accepted", "rejected"):
            resolved = ConsensusProposal(
                proposal_id=proposal.proposal_id,
                proposer_id=proposal.proposer_id,
                content=proposal.content,
                votes_for=proposal.votes_for,
                votes_against=proposal.votes_against,
                status=outcome,
                proposed_at=proposal.proposed_at,
            )
            self._proposals[proposal_id] = resolved
            self._resolution_log.append({
                "proposal_id": proposal_id,
                "outcome": outcome,
                "votes_for": n_for,
                "votes_against": n_against,
                "resolved_at": _now_iso(),
            })

        tier = TrustTier.VERIFIED if outcome == "accepted" else TrustTier.REVIEWED
        return FederationJudgment(
            context=f"consensus:{proposal_id}",
            formula=f"consensus({proposal_id}) = {outcome}",
            authority="FederationConsensus",
            evidence={
                "outcome": outcome,
                "votes_for": n_for,
                "votes_against": n_against,
                "quorum_met": quorum_met,
            },
            obligations=() if outcome in ("accepted", "rejected") else (proposal_id,),
            budget=float(n_votes),
            trust_tier=tier,
            proof_chain=(proposal_id,),
        )

    def get_open_proposals(self) -> list[ConsensusProposal]:
        """Return all proposals with status 'open'."""
        return [p for p in self._proposals.values() if p.status == "open"]

    def get_resolution_log(self) -> list[dict]:
        """Return the resolution log."""
        return list(self._resolution_log)

    def reset(self) -> None:
        """Clear all proposals and the resolution log."""
        self._proposals.clear()
        self._resolution_log.clear()


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def derive_federation_consequences(
    federation_config: dict,
    discovery_state: dict,
) -> list[FederatedDiscoveryConsequence]:
    """Derive federation consequences from a config and discovery state.

    Analyses the federation_config for risk factors and cross-references
    with the discovery_state to produce FederatedDiscoveryConsequence records.

    Parameters
    ----------
    federation_config:
        Dict containing federation parameters such as 'max_nodes',
        'consensus_protocol', 'replication_factor', 'trust_threshold'.
    discovery_state:
        Dict containing current discovery state such as 'active_theorems',
        'pending_obligations', 'node_count', 'health_scores'.

    Returns
    -------
    list[FederatedDiscoveryConsequence]
        Derived consequences, ordered by descending severity.
    """
    consequences: list[FederatedDiscoveryConsequence] = []
    node_count = int(discovery_state.get("node_count", 1))
    replication_factor = int(federation_config.get("replication_factor", 1))
    trust_threshold = float(federation_config.get("trust_threshold", 0.5))
    active_theorems = int(discovery_state.get("active_theorems", 0))

    # Consequence 1: low replication factor
    if replication_factor < 2:
        consequences.append(
            FederatedDiscoveryConsequence(
                consequence_id=_uid(),
                description="Low replication factor risks data loss on node failure.",
                federation_aspect="replication",
                design_implication="Increase replication_factor to at least 2.",
                affected_nodes=tuple(str(i) for i in range(min(node_count, 3))),
                severity=0.8,
                trust_tier=TrustTier.REVIEWED,
                created_at=_now_iso(),
            )
        )

    # Consequence 2: insufficient nodes for consensus
    if node_count < 3:
        consequences.append(
            FederatedDiscoveryConsequence(
                consequence_id=_uid(),
                description="Fewer than 3 nodes make consensus fragile.",
                federation_aspect="consensus",
                design_implication="Add at least 2 more nodes before enabling full consensus.",
                affected_nodes=tuple(str(i) for i in range(node_count)),
                severity=0.7,
                trust_tier=TrustTier.PROPOSAL,
                created_at=_now_iso(),
            )
        )

    # Consequence 3: low trust threshold
    if trust_threshold < 0.3:
        consequences.append(
            FederatedDiscoveryConsequence(
                consequence_id=_uid(),
                description="Trust threshold is very low; unverified nodes may join.",
                federation_aspect="trust",
                design_implication="Raise trust_threshold to at least 0.3.",
                affected_nodes=(),
                severity=0.6,
                trust_tier=TrustTier.REVIEWED,
                created_at=_now_iso(),
            )
        )

    # Consequence 4: too many active theorems relative to nodes
    if active_theorems > node_count * 10:
        consequences.append(
            FederatedDiscoveryConsequence(
                consequence_id=_uid(),
                description="Theorem load per node exceeds recommended threshold.",
                federation_aspect="load_balancing",
                design_implication="Scale out the federation or reduce active theorem count.",
                affected_nodes=tuple(str(i) for i in range(min(node_count, 5))),
                severity=0.5,
                trust_tier=TrustTier.PROPOSAL,
                created_at=_now_iso(),
            )
        )

    consequences.sort(key=lambda c: c.severity, reverse=True)
    logger.info("Derived %d federation consequences.", len(consequences))
    return consequences


def enforce_federation_policy(
    policy_set: list[dict],
    federation_nodes: list[FederationNode],
) -> list[FederationJudgment]:
    """Apply a set of policy definitions to a list of federation nodes.

    Parameters
    ----------
    policy_set:
        List of policy dicts, each with at least 'name', 'rule', 'scope'.
    federation_nodes:
        Nodes to evaluate policies against.

    Returns
    -------
    list[FederationJudgment]
        One judgment per (policy, node) pair that results in a decision.
    """
    policy_manager = FederationPolicy()
    for p in policy_set:
        policy_manager.register_policy(p["name"], p["rule"], p.get("scope", "global"))

    judgments: list[FederationJudgment] = []
    for node in federation_nodes:
        record = FederationRecord(
            record_id=_uid(),
            federation_id="enforce_run",
            event_type="policy_check",
            payload=json.dumps({"node_id": node.node_id, "role": node.role}),
            participants=(node.node_id,),
            timestamp=_now_iso(),
        )
        j = policy_manager.enforce(record, federation_nodes)
        judgments.append(j)

    return judgments


def compute_node_health_score(node: FederationNode, health_data: dict) -> NodeHealth:
    """Compute a NodeHealth record for *node* using *health_data*.

    Parameters
    ----------
    node:
        The node to assess.
    health_data:
        Dict with optional keys: 'latency_ms', 'uptime_ratio', 'error_count'.

    Returns
    -------
    NodeHealth
        A health snapshot.
    """
    latency = float(health_data.get("latency_ms", 100.0))
    uptime = _clamp(float(health_data.get("uptime_ratio", 0.99)), 0.0, 1.0)
    errors = int(health_data.get("error_count", 0))
    latency_score = _clamp(1.0 - math.log1p(latency) / math.log1p(10000), 0.0, 1.0)
    error_penalty = _clamp(errors * 0.05, 0.0, 1.0)
    health_score = _clamp(uptime * 0.5 + latency_score * 0.4 - error_penalty * 0.1, 0.0, 1.0)
    return NodeHealth(
        node_id=node.node_id,
        latency_ms=latency,
        uptime_ratio=uptime,
        last_seen=_now_iso(),
        error_count=errors,
        health_score=health_score,
        checked_at=_now_iso(),
    )


def build_consequence_chain(
    root: FederatedDiscoveryConsequence,
    all_consequences: list[FederatedDiscoveryConsequence],
    max_depth: int = 3,
) -> ConsequenceChain:
    """Build a causal ConsequenceChain starting from *root*.

    Derives child consequences from *all_consequences* whose severity is
    within 0.2 of the root's severity. Propagation is limited to *max_depth*.

    Parameters
    ----------
    root:
        The initiating consequence.
    all_consequences:
        Full pool of consequences to search for derived ones.
    max_depth:
        Maximum propagation depth.

    Returns
    -------
    ConsequenceChain
        The built chain.
    """
    derived = [
        c.consequence_id
        for c in all_consequences
        if c.consequence_id != root.consequence_id
        and abs(c.severity - root.severity) <= 0.2
    ][:max_depth]
    return ConsequenceChain(
        chain_id=_uid(),
        root_consequence_id=root.consequence_id,
        derived_consequences=tuple(derived),
        propagation_depth=len(derived),
        created_at=_now_iso(),
    )


def summarise_federation_state(
    nodes: list[FederationNode],
    consequences: list[FederatedDiscoveryConsequence],
    violations: list[dict],
) -> dict:
    """Produce a human-readable summary of the current federation state.

    Parameters
    ----------
    nodes:
        Current federation nodes.
    consequences:
        Derived consequences.
    violations:
        Policy violation dicts.

    Returns
    -------
    dict
        Summary statistics.
    """
    tier_counts: dict[str, int] = collections.Counter(n.trust_tier.name for n in nodes)
    role_counts: dict[str, int] = collections.Counter(n.role for n in nodes)
    avg_severity = sum(c.severity for c in consequences) / max(1, len(consequences))
    return {
        "node_count": len(nodes),
        "nodes_by_tier": dict(tier_counts),
        "nodes_by_role": dict(role_counts),
        "consequence_count": len(consequences),
        "avg_consequence_severity": avg_severity,
        "violation_count": len(violations),
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _error_judgment(context: str) -> FederationJudgment:
    """Return a PROPOSAL-tier error judgment for exceptional cases."""
    return FederationJudgment(
        context=context,
        formula=f"error({context})",
        authority="federation_module",
        evidence={"error": context},
        obligations=(context,),
        budget=0.0,
        trust_tier=TrustTier.PROPOSAL,
        proof_chain=(),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== implementation_consequences (federation) smoke test ===")

    # 1. Create nodes
    nodes: list[FederationNode] = []
    roles = ["coordinator", "worker", "worker", "observer", "gateway"]
    for i, role in enumerate(roles):
        tier = TrustTier(random.randint(1, 3))
        nodes.append(
            FederationNode(
                node_id=f"node_{i:02d}",
                name=f"Node-{i}",
                role=role,
                capabilities=("discovery", "proof") if role == "coordinator" else ("discovery",),
                trust_tier=tier,
                endpoint=f"https://node{i}.jugeo.local:8080",
                joined_at=_now_iso(),
            )
        )
    print(f"Created {len(nodes)} nodes.")

    # 2. Register policies
    policy_manager = FederationPolicy()
    (
        policy_manager
        .register_policy("no_partition", "no_partition AND availability", "global")
        .register_policy("quorum_required", "quorum(0.5)", "global")
        .register_policy("trust_minimum", "trust_tier >= REVIEWED", "local")
    )
    print(f"Registered policies: {policy_manager.get_policy_names()}")

    # 3. Enforce a policy with a record
    record = FederationRecord(
        record_id=_uid(),
        federation_id="fed_main",
        event_type="join",
        payload=json.dumps({"action": "node_join"}),
        participants=(nodes[0].node_id,),
        timestamp=_now_iso(),
    )
    judgment = policy_manager.enforce(record, nodes)
    print(f"\nEnforce judgment: {judgment.formula}")
    print(f"  Trust tier: {judgment.trust_tier.name}")

    # 4. Check compliance for a node
    comp = policy_manager.check_compliance(nodes[0].node_id)
    print(f"\nCompliance [{nodes[0].node_id}]: {comp.formula} tier={comp.trust_tier.name}")

    # 5. Consensus protocol
    consensus = FederationConsensus(quorum_threshold=0.5)
    proposal = consensus.propose(nodes[0].node_id, "Adopt new replication protocol v2.")
    print(f"\nProposal: {proposal.proposal_id[:8]} - {proposal.content[:40]}")
    for node in nodes[:4]:
        in_favour = node.role in ("coordinator", "worker")
        consensus.vote(proposal.proposal_id, node.node_id, in_favour)
    resolution = consensus.resolve(proposal.proposal_id, nodes)
    print(f"Resolution: {resolution.formula}")
    print(f"  Evidence: {resolution.evidence}")

    # 6. Derive consequences
    fed_config = {"replication_factor": 1, "trust_threshold": 0.2, "consensus_protocol": "raft"}
    disc_state = {"node_count": len(nodes), "active_theorems": 120}
    consequences = derive_federation_consequences(fed_config, disc_state)
    print(f"\nDerived {len(consequences)} consequences:")
    for c in consequences:
        print(f"  [{c.severity:.2f}] {c.federation_aspect}: {c.description[:60]}")

    # 7. Build consequence chain
    if consequences:
        chain = build_consequence_chain(consequences[0], consequences)
        print(f"\nConsequence chain {chain.chain_id[:8]}: depth={chain.propagation_depth}")

    # 8. Node health
    health = compute_node_health_score(nodes[0], {"latency_ms": 45.0, "uptime_ratio": 0.998, "error_count": 2})
    print(f"\nNode health [{nodes[0].node_id}]: score={health.health_score:.4f}")

    # 9. Policy report
    report = policy_manager.generate_policy_report()
    print(f"\nPolicy report: {json.dumps(report, indent=2)}")

    # 10. Federation summary
    summary = summarise_federation_state(nodes, consequences, policy_manager.get_policy_violations())
    print(f"\nFederation summary: {json.dumps(summary, indent=2)}")

    print("\n=== smoke test complete ===")
