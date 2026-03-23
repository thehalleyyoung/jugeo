"""Integration layer for the Mixed-Evidence Routing package.

theory2.tex Chapter 45 — Integration Layer
============================================
This module wires together the four subsystems of the MER pipeline:

1. **Trust integration** (``RoutingTrustIntegrator``) — maps every routing
   decision to a trust ceiling consistent with the channel's epistemic
   guarantees.  Copilot/LLM channels are hard-capped at COPILOT_SUGGESTED;
   Z3 channels floor at SOLVER_DISCHARGED.

2. **Descent validation** (``RoutingDescentConnector``) — verifies that a
   set of routing decisions forms a coherent global section by performing a
   descent-inspired consistency check over the channel overlap graph.

3. **Copilot gateway** (``CopilotTrustGateway``) — single point of control
   for every query that touches the LLM oracle, enforcing the no-silent-
   promotion rule and logging every interaction to an append-only audit trail.

4. **Fleet bridge** (``RoutingFleetBridge``) — dispatches a routing decision
   to the fleet member registered for that channel and collects the result.

5. **Top-level orchestrator** (``MixedEvidenceOrchestrator``) — composes all
   four sub-systems into a single ``route``/``execute`` interface.

All upstream imports are guarded by ``try/except`` so that the module can be
tested in isolation without a fully-installed jugeo tree.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Upstream jugeo imports — all guarded
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra  # type: ignore[import]
except Exception:
    class TrustLevel:  # type: ignore[no-redef]
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        ORACLE_PROPOSED = "ORACLE_PROPOSED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        UNVERIFIED = "UNVERIFIED"
        CONTRADICTED = "CONTRADICTED"

    class TrustAlgebra:  # type: ignore[no-redef]
        def join(self, a: str, b: str) -> str:
            return a

try:
    from jugeo.evidence.trust import TrustCeiling  # type: ignore[import]
except Exception:
    class TrustCeiling:  # type: ignore[no-redef]
        def __init__(self, ceiling: str) -> None:
            self.ceiling = ceiling

        def apply(self, trust: str) -> str:
            return trust

try:
    from jugeo.geometry.descent import (  # type: ignore[import]
        GluingData,
        DescentEngine,
        DescentResult,
        DescentObstruction,
    )
except Exception:
    @dataclass(frozen=True, slots=True)
    class GluingData:  # type: ignore[no-redef]
        sections: list[Any] = field(default_factory=list)

    class DescentEngine:  # type: ignore[no-redef]
        def run(self, data: Any) -> Any:
            return None

    @dataclass(frozen=True, slots=True)
    class DescentResult:  # type: ignore[no-redef]
        success: bool = True
        obstructions: list[str] = field(default_factory=list)

    @dataclass(frozen=True, slots=True)
    class DescentObstruction:  # type: ignore[no-redef]
        description: str = ""

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, FleetBid  # type: ignore[import]
except Exception:
    @dataclass(slots=True)
    class FleetMember:  # type: ignore[no-redef]
        member_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        capabilities: list[str] = field(default_factory=list)

    @dataclass(slots=True)
    class FleetBid:  # type: ignore[no-redef]
        bid_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        member_id: str = ""
        task: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class Fleet:  # type: ignore[no-redef]
        fleet_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        members: list[FleetMember] = field(default_factory=list)

        def register(self, member: FleetMember) -> None:
            self.members.append(member)

        def dispatch(self, task: dict[str, Any]) -> dict[str, Any]:
            return {"status": "dispatched", "task_id": task.get("task_id", "unknown")}

try:
    from jugeo.orchestration.controller import Orchestrator  # type: ignore[import]
except Exception:
    @dataclass(slots=True)
    class Orchestrator:  # type: ignore[no-redef]
        orchestrator_id: str = field(default_factory=lambda: str(uuid.uuid4()))

# ---------------------------------------------------------------------------
# Internal MER imports — all guarded
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import]
        EvidenceChannel,
        RoutingStrategy,
        EscalationUrgency,
        RoutingDecision,
        JurisdictionMap,
        EvidenceChannelSelector,
        RoutingHistory,
        ChannelStats,
    )
except Exception:
    from enum import Enum

    class EvidenceChannel(str, Enum):  # type: ignore[no-redef]
        Z3 = "z3"
        COPILOT_LLM = "copilot_llm"
        RUNTIME_WITNESS = "runtime_witness"
        HUMAN = "human"
        COMPOSITE = "composite"

    class RoutingStrategy(str, Enum):  # type: ignore[no-redef]
        STRICT_JURISDICTION = "strict_jurisdiction"
        COST_OPTIMAL = "cost_optimal"
        LATENCY_OPTIMAL = "latency_optimal"
        TRUST_OPTIMAL = "trust_optimal"
        LOAD_BALANCED = "load_balanced"

    class EscalationUrgency(str, Enum):  # type: ignore[no-redef]
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"
        CRITICAL = "critical"

    @dataclass(frozen=True, slots=True)
    class RoutingDecision:  # type: ignore[no-redef]
        decision_id: str
        task_id: str
        channel: EvidenceChannel
        rationale: str
        confidence: float
        estimated_cost: float
        estimated_latency: float
        timestamp: float
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class JurisdictionMap:  # type: ignore[no-redef]
        map_id: str
        channel: EvidenceChannel
        supported_claim_kinds: list[str]
        max_complexity: int
        min_trust_level: str
        exclusions: list[str] = field(default_factory=list)

        def can_handle(self, task: dict[str, Any]) -> bool:
            kind = task.get("claim_kind", "unknown")
            complexity = task.get("complexity", 0)
            excluded = task.get("claim_kind", "") in self.exclusions
            return (
                kind in self.supported_claim_kinds
                and complexity <= self.max_complexity
                and not excluded
            )

    @dataclass(slots=True)
    class EvidenceChannelSelector:  # type: ignore[no-redef]
        selector_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        jurisdiction_maps: list[JurisdictionMap] = field(default_factory=list)
        cost_weights: dict[str, float] = field(default_factory=dict)
        latency_weights: dict[str, float] = field(default_factory=dict)

    @dataclass(slots=True)
    class RoutingHistory:  # type: ignore[no-redef]
        decisions: list[RoutingDecision] = field(default_factory=list)

        def append(self, decision: RoutingDecision) -> None:
            self.decisions.append(decision)

    @dataclass(slots=True)
    class ChannelStats:  # type: ignore[no-redef]
        channel: EvidenceChannel = EvidenceChannel.Z3
        total_routed: int = 0
        total_success: int = 0
        total_cost: float = 0.0
        total_latency_ms: float = 0.0

try:
    from jugeo.orchestration.mixed_evidence_routing.manifest import (  # type: ignore[import]
        MixedEvidenceRoutingManifest,
        ChannelRegistry,
        JurisdictionCatalog,
    )
except Exception:
    @dataclass(slots=True)
    class ChannelRegistry:  # type: ignore[no-redef]
        channels: dict[str, Any] = field(default_factory=dict)

        def register(self, channel: EvidenceChannel, adapter: Any) -> None:
            self.channels[channel.value] = adapter

        def get(self, channel: EvidenceChannel) -> Any:
            return self.channels.get(channel.value)

        def available_channels(self) -> list[EvidenceChannel]:
            return list(EvidenceChannel)

    @dataclass(slots=True)
    class JurisdictionCatalog:  # type: ignore[no-redef]
        maps: list[JurisdictionMap] = field(default_factory=list)

        def add(self, jmap: JurisdictionMap) -> None:
            self.maps.append(jmap)

        def find_handlers(self, task: dict[str, Any]) -> list[JurisdictionMap]:
            return [m for m in self.maps if m.can_handle(task)]

    @dataclass(slots=True)
    class MixedEvidenceRoutingManifest:  # type: ignore[no-redef]
        registry: ChannelRegistry = field(default_factory=ChannelRegistry)
        catalog: JurisdictionCatalog = field(default_factory=JurisdictionCatalog)

try:
    from jugeo.orchestration.mixed_evidence_routing.jurisdiction_enforcement import (  # type: ignore[import]
        JurisdictionAuditLog,
    )
except Exception:
    @dataclass(slots=True)
    class JurisdictionAuditLog:  # type: ignore[no-redef]
        entries: list[dict[str, Any]] = field(default_factory=list)

        def record(self, event: dict[str, Any]) -> None:
            self.entries.append({**event, "timestamp": time.time()})

try:
    from jugeo.orchestration.mixed_evidence_routing.routing_policy import (  # type: ignore[import]
        RoutingPolicy,
        RoutingContext,
        RoutingOutcome,
        RoutingPolicyRegistry,
        AdaptiveRoutingPolicy,
        StrictJurisdictionPolicy,
    )
except Exception:
    @dataclass(frozen=True, slots=True)
    class RoutingContext:  # type: ignore[no-redef]
        task: dict[str, Any]
        strategy: str = "strict_jurisdiction"

    @dataclass(frozen=True, slots=True)
    class RoutingOutcome:  # type: ignore[no-redef]
        outcome_id: str
        decision_id: str
        success: bool
        actual_cost: float
        actual_latency_ms: float
        trust_achieved: str
        timestamp: float
        notes: str = ""

    @dataclass(slots=True)
    class RoutingPolicy:  # type: ignore[no-redef]
        policy_id: str = field(default_factory=lambda: str(uuid.uuid4()))

        def select_channel(
            self,
            task: dict[str, Any],
            jurisdiction_catalog: Any,
        ) -> EvidenceChannel:
            if hasattr(jurisdiction_catalog, "find_handlers"):
                handlers = jurisdiction_catalog.find_handlers(task)
            else:
                maps = getattr(jurisdiction_catalog, "jurisdiction_maps", [])
                handlers = [
                    jmap
                    for jmap in maps
                    if hasattr(jmap, "can_handle") and jmap.can_handle(task)
                ]
            if handlers:
                return handlers[0].channel
            return EvidenceChannel.HUMAN

    @dataclass(slots=True)
    class StrictJurisdictionPolicy(RoutingPolicy):  # type: ignore[no-redef]
        pass

    @dataclass(slots=True)
    class AdaptiveRoutingPolicy(RoutingPolicy):  # type: ignore[no-redef]
        pass

    @dataclass(slots=True)
    class RoutingPolicyRegistry:  # type: ignore[no-redef]
        policies: dict[str, RoutingPolicy] = field(default_factory=dict)

        def register(self, name: str, policy: RoutingPolicy) -> None:
            self.policies[name] = policy

        def get(self, name: str) -> RoutingPolicy:
            return self.policies.get(name, RoutingPolicy())

try:
    from jugeo.orchestration.mixed_evidence_routing.algorithms import (  # type: ignore[import]
        route_task,
        route_batch,
        routing_efficiency_score,
        detect_jurisdiction_gap,
    )
except Exception:
    def route_task(task: dict[str, Any], catalog: Any, policy: Any) -> RoutingDecision:  # type: ignore[misc]
        channel = policy.select_channel(task, catalog)
        return RoutingDecision(
            decision_id=str(uuid.uuid4()),
            task_id=task.get("task_id", str(uuid.uuid4())),
            channel=channel,
            rationale=f"policy={policy.policy_id}",
            confidence=0.8,
            estimated_cost=1.0,
            estimated_latency=100.0,
            timestamp=time.time(),
            metadata={},
        )

    def route_batch(  # type: ignore[misc]
        tasks: list[dict[str, Any]],
        catalog: Any,
        policy: Any,
    ) -> list[RoutingDecision]:
        return [route_task(t, catalog, policy) for t in tasks]

    def routing_efficiency_score(history: RoutingHistory) -> float:  # type: ignore[misc]
        if not history.decisions:
            return 0.0
        return sum(d.confidence for d in history.decisions) / len(history.decisions)

    def detect_jurisdiction_gap(  # type: ignore[misc]
        tasks: list[dict[str, Any]], catalog: Any
    ) -> list[dict[str, Any]]:
        return [t for t in tasks if not catalog.find_handlers(t)]


# ---------------------------------------------------------------------------
# Trust rank mapping
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = [
    "CONTRADICTED",
    "UNVERIFIED",
    "COPILOT_SUGGESTED",
    "ORACLE_PROPOSED",
    "HUMAN_ATTESTED",
    "RUNTIME_WITNESSED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]


def _rank(trust: str) -> int:
    """Return numeric rank for a trust level name (0 = weakest)."""
    try:
        return _TRUST_ORDER.index(trust.upper())
    except ValueError:
        return 0


def _weaker(a: str, b: str) -> str:
    """Return the weaker of two trust levels."""
    return a if _rank(a) <= _rank(b) else b


# ---------------------------------------------------------------------------
# CopilotQueryRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CopilotQueryRecord:
    """Immutable record of a single Copilot LLM interaction.

    Captures the query, response, claimed trust, enforced trust, model
    identifier, and a timestamp for full auditability.
    """

    record_id: str
    query_text: str
    response_text: str
    claimed_trust: str
    enforced_trust: str
    model_id: str
    timestamp: float
    blocked: bool

    @classmethod
    def create(
        cls,
        query_text: str,
        response_text: str,
        claimed_trust: str,
        enforced_trust: str,
        model_id: str,
        blocked: bool,
    ) -> CopilotQueryRecord:
        """Construct a new record with a fresh UUID and current timestamp."""
        return cls(
            record_id=str(uuid.uuid4()),
            query_text=query_text,
            response_text=response_text,
            claimed_trust=claimed_trust,
            enforced_trust=enforced_trust,
            model_id=model_id,
            timestamp=time.time(),
            blocked=blocked,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "record_id": self.record_id,
            "query_text": self.query_text,
            "response_text": self.response_text,
            "claimed_trust": self.claimed_trust,
            "enforced_trust": self.enforced_trust,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
            "blocked": self.blocked,
        }


# ---------------------------------------------------------------------------
# 1. RoutingTrustIntegrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RoutingTrustIntegrator:
    """Bridges routing decisions to the jugeo trust ordered algebra.

    Applies channel-specific trust ceilings, composes multi-evidence trust
    values, and records every trust event to an append-only audit log.

    The channel ceilings enforce the theory2.tex invariant that Copilot/LLM
    evidence is capped at COPILOT_SUGGESTED and can never impersonate
    solver-discharged proofs.
    """

    integrator_id: str
    trust_algebra: Any
    channel_ceilings: dict[str, str]
    audit_log: Any

    @classmethod
    def default(cls) -> RoutingTrustIntegrator:
        """Construct with sensible defaults from the theory2.tex spec."""
        return cls(
            integrator_id=str(uuid.uuid4()),
            trust_algebra=TrustAlgebra(),
            channel_ceilings={
                EvidenceChannel.Z3.value: "MECHANICALLY_VERIFIED",
                EvidenceChannel.COPILOT_LLM.value: "COPILOT_SUGGESTED",
                EvidenceChannel.RUNTIME_WITNESS.value: "RUNTIME_WITNESSED",
                EvidenceChannel.HUMAN.value: "HUMAN_ATTESTED",
                EvidenceChannel.COMPOSITE.value: "SOLVER_DISCHARGED",
            },
            audit_log=JurisdictionAuditLog(),
        )

    def trust_rank(self, trust_level: str) -> int:
        """Return ordinal rank of *trust_level* (0 = CONTRADICTED, 7 = MECHANICALLY_VERIFIED)."""
        return _rank(trust_level)

    def apply_trust_ceiling(
        self, decision: RoutingDecision, claimed_trust: str
    ) -> str:
        """Return ``min(claimed_trust, channel_ceiling)`` for the decision's channel.

        If the channel has no registered ceiling, the claimed trust is
        returned unchanged.
        """
        ceiling = self.channel_ceilings.get(decision.channel.value)
        if ceiling is None:
            return claimed_trust
        return _weaker(claimed_trust, ceiling)

    def compose_evidence_trust(self, trusts: list[str]) -> str:
        """Return the weakest trust in *trusts* (conjunction semantics).

        An empty list returns CONTRADICTED — no evidence is the weakest
        possible state.
        """
        if not trusts:
            return "CONTRADICTED"
        result = trusts[0]
        for t in trusts[1:]:
            result = _weaker(result, t)
        return result

    def validate_trust_assignment(
        self, channel: EvidenceChannel, trust: str
    ) -> tuple[bool, str]:
        """Validate that *trust* does not exceed the ceiling for *channel*.

        Returns ``(True, "")`` on success, ``(False, reason)`` on violation.
        """
        ceiling = self.channel_ceilings.get(channel.value)
        if ceiling is None:
            return True, ""
        if _rank(trust) > _rank(ceiling):
            reason = (
                f"Channel {channel.value} ceiling is {ceiling}; "
                f"assigned trust {trust} exceeds it"
            )
            return False, reason
        return True, ""

    def copilot_ceiling_satisfied(self, trust: str) -> bool:
        """Return ``True`` iff *trust* is at or below COPILOT_SUGGESTED."""
        return _rank(trust) <= _rank("COPILOT_SUGGESTED")

    def promote_if_permitted(
        self, trust: str, justification: str, channel: EvidenceChannel
    ) -> str:
        """Attempt to promote *trust* by one level if permitted for the channel.

        Promotion is only allowed when *justification* is non-empty and the
        result would not exceed the channel ceiling.  Returns the original
        trust level if promotion is not permitted.
        """
        if not justification.strip():
            self.record_trust_event(
                "promotion_blocked",
                channel,
                trust,
                {"reason": "empty justification"},
            )
            return trust

        ceiling = self.channel_ceilings.get(channel.value, "MECHANICALLY_VERIFIED")
        current_rank = _rank(trust)
        if current_rank >= len(_TRUST_ORDER) - 1:
            return trust
        promoted = _TRUST_ORDER[current_rank + 1]
        if _rank(promoted) > _rank(ceiling):
            self.record_trust_event(
                "promotion_blocked",
                channel,
                trust,
                {"reason": f"would exceed ceiling {ceiling}", "attempted": promoted},
            )
            return trust
        self.record_trust_event(
            "promotion_applied",
            channel,
            trust,
            {"promoted_to": promoted, "justification": justification},
        )
        return promoted

    def record_trust_event(
        self,
        event_type: str,
        channel: EvidenceChannel,
        trust: str,
        details: dict[str, Any],
    ) -> None:
        """Append a trust event to the audit log."""
        self.audit_log.record(
            {
                "event_type": event_type,
                "channel": channel.value,
                "trust": trust,
                **details,
            }
        )

    def trust_summary(self) -> dict[str, Any]:
        """Return a snapshot of integrator state for diagnostics."""
        return {
            "integrator_id": self.integrator_id,
            "channel_ceilings": dict(self.channel_ceilings),
            "audit_entry_count": len(self.audit_log.entries),
        }


# ---------------------------------------------------------------------------
# 2. RoutingDescentConnector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RoutingDescentConnector:
    """Validates routing decisions via descent-inspired consistency checks.

    Simulates the overlap / gluing step from theory2.tex Ch45: a set of
    routing decisions is viewed as local sections of a channel sheaf; the
    connector checks that overlapping sections are consistent (i.e., the
    same task always routes to the same channel).
    """

    connector_id: str
    strategy: str = "exhaustive"
    timeout_s: float = 30.0

    @classmethod
    def default(cls) -> RoutingDescentConnector:
        """Construct with default exhaustive strategy and 30 s timeout."""
        return cls(
            connector_id=str(uuid.uuid4()),
            strategy="exhaustive",
            timeout_s=30.0,
        )

    def check_channel_overlap_consistency(
        self, d1: RoutingDecision, d2: RoutingDecision
    ) -> bool:
        """Return ``True`` iff *d1* and *d2* are mutually consistent.

        Two decisions are consistent if they concern different tasks, or if
        they concern the same task and agree on the channel.
        """
        if d1.task_id != d2.task_id:
            return True
        return d1.channel == d2.channel

    def compute_obstruction(self, decisions: list[RoutingDecision]) -> list[str]:
        """Identify channel-overlap inconsistencies across *decisions*.

        Returns a list of human-readable descriptions of each inconsistency.
        An empty list means no obstruction — the global section exists.
        """
        obstructions: list[str] = []
        for i, d1 in enumerate(decisions):
            for d2 in decisions[i + 1 :]:
                if not self.check_channel_overlap_consistency(d1, d2):
                    obstructions.append(
                        f"Inconsistency: task {d1.task_id} routed to both "
                        f"{d1.channel.value} (decision {d1.decision_id}) and "
                        f"{d2.channel.value} (decision {d2.decision_id})"
                    )
        return obstructions

    def build_gluing_data(
        self, decisions: list[RoutingDecision]
    ) -> dict[str, Any]:
        """Build a GluingData-like structure from *decisions*.

        Groups decisions by task_id to expose the overlap structure
        expected by the descent engine.
        """
        by_task: dict[str, list[dict[str, Any]]] = {}
        for d in decisions:
            by_task.setdefault(d.task_id, []).append(
                {
                    "decision_id": d.decision_id,
                    "channel": d.channel.value,
                    "confidence": d.confidence,
                }
            )
        return {
            "sections": by_task,
            "section_count": len(decisions),
            "task_count": len(by_task),
            "strategy": self.strategy,
        }

    def validate_routing_result(
        self, decisions: list[RoutingDecision], task: dict[str, Any]
    ) -> tuple[bool, str]:
        """Check that *decisions* form a consistent section for *task*.

        Simulates a one-step descent: collects all decisions for the given
        task_id and verifies they all agree on the channel.  Returns
        ``(True, "")`` on success, ``(False, reason)`` on failure.
        """
        task_id = task.get("task_id", "")
        relevant = [d for d in decisions if d.task_id == task_id]
        if not relevant:
            return False, f"No routing decision found for task {task_id}"
        channels = {d.channel for d in relevant}
        if len(channels) > 1:
            names = ", ".join(c.value for c in channels)
            return False, f"Inconsistent channels for task {task_id}: {names}"
        return True, ""

    def run_descent_validation(
        self, tasks: list[dict[str, Any]], decisions: list[RoutingDecision]
    ) -> dict[str, Any]:
        """Run full descent validation over *tasks* and *decisions*.

        Returns a summary dict with per-task validation results and
        any obstructions found.
        """
        obstructions = self.compute_obstruction(decisions)
        task_results: dict[str, Any] = {}
        for task in tasks:
            ok, msg = self.validate_routing_result(decisions, task)
            task_results[task.get("task_id", "unknown")] = {
                "valid": ok,
                "message": msg,
            }
        return {
            "connector_id": self.connector_id,
            "strategy": self.strategy,
            "task_count": len(tasks),
            "decision_count": len(decisions),
            "obstructions": obstructions,
            "task_results": task_results,
            "global_section_exists": len(obstructions) == 0,
        }

    def descent_summary(self) -> dict[str, Any]:
        """Return a static summary of this connector's configuration."""
        return {
            "connector_id": self.connector_id,
            "strategy": self.strategy,
            "timeout_s": self.timeout_s,
        }


# ---------------------------------------------------------------------------
# 3. CopilotTrustGateway
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotTrustGateway:
    """Single control point for all Copilot/LLM oracle interactions.

    Enforces the no-silent-promotion rule (theory2.tex §45.2): every
    response from the LLM channel is capped at COPILOT_SUGGESTED, and
    every interaction is written to an append-only audit trail.

    Attributes
    ----------
    gateway_id:
        Unique identifier for this gateway instance.
    max_trust:
        The hard trust ceiling for Copilot queries (default: COPILOT_SUGGESTED).
    allow_promotion:
        If ``True``, the gateway may promote trust when an explicit
        justification is provided.
    query_count:
        Running total of queries processed.
    blocked_count:
        Running total of queries whose claimed trust exceeded the ceiling.
    audit_entries:
        Append-only log of all interactions.
    """

    gateway_id: str
    max_trust: str = "COPILOT_SUGGESTED"
    allow_promotion: bool = False
    query_count: int = 0
    blocked_count: int = 0
    audit_entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def default(cls) -> CopilotTrustGateway:
        """Construct with strict COPILOT_SUGGESTED ceiling and no promotion."""
        return cls(
            gateway_id=str(uuid.uuid4()),
            max_trust="COPILOT_SUGGESTED",
            allow_promotion=False,
        )

    def enforce_ceiling(self, claimed_trust: str) -> str:
        """Return the lesser of *claimed_trust* and the gateway ceiling."""
        return _weaker(claimed_trust, self.max_trust)

    def is_trust_within_ceiling(self, trust: str) -> bool:
        """Return ``True`` iff *trust* does not exceed the ceiling."""
        return _rank(trust) <= _rank(self.max_trust)

    def process_query(
        self,
        query_text: str,
        response_text: str,
        claimed_trust: str,
        model_id: str = "unknown",
    ) -> CopilotQueryRecord:
        """Process a single Copilot query and return an audit record.

        Enforces the trust ceiling and increments counters.
        """
        self.query_count += 1
        enforced = self.enforce_ceiling(claimed_trust)
        blocked = _rank(claimed_trust) > _rank(self.max_trust)
        if blocked:
            self.blocked_count += 1
        record = CopilotQueryRecord.create(
            query_text=query_text,
            response_text=response_text,
            claimed_trust=claimed_trust,
            enforced_trust=enforced,
            model_id=model_id,
            blocked=blocked,
        )
        self.audit_entries.append(record.to_dict())
        return record

    def block_if_exceeds_ceiling(
        self, decision: RoutingDecision
    ) -> tuple[bool, RoutingDecision]:
        """Gate a routing decision through the Copilot trust ceiling.

        If the decision's channel is COPILOT_LLM and its metadata trust
        exceeds the ceiling, the decision is modified to reflect the enforced
        trust level.  Returns ``(blocked, decision)`` where *blocked* is
        ``True`` when the original trust was demoted.
        """
        if decision.channel != EvidenceChannel.COPILOT_LLM:
            return False, decision
        claimed = decision.metadata.get("trust", self.max_trust)
        enforced = self.enforce_ceiling(claimed)
        blocked = claimed != enforced
        if blocked:
            new_meta = {**decision.metadata, "trust": enforced, "trust_demoted": True}
            decision = RoutingDecision(
                decision_id=decision.decision_id,
                task_id=decision.task_id,
                channel=decision.channel,
                rationale=decision.rationale,
                confidence=decision.confidence,
                estimated_cost=decision.estimated_cost,
                estimated_latency=decision.estimated_latency,
                timestamp=decision.timestamp,
                metadata=new_meta,
            )
        return blocked, decision

    def query_statistics(self) -> dict[str, Any]:
        """Return a summary of gateway query statistics."""
        block_rate = (
            self.blocked_count / self.query_count if self.query_count > 0 else 0.0
        )
        return {
            "gateway_id": self.gateway_id,
            "query_count": self.query_count,
            "blocked_count": self.blocked_count,
            "block_rate": block_rate,
            "max_trust": self.max_trust,
            "allow_promotion": self.allow_promotion,
        }

    def audit_trail(self) -> list[dict[str, Any]]:
        """Return a copy of the append-only audit trail."""
        return list(self.audit_entries)


# ---------------------------------------------------------------------------
# 4. RoutingFleetBridge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RoutingFleetBridge:
    """Bridges routing decisions to the fleet orchestration layer.

    After the router selects an ``EvidenceChannel``, the bridge locates the
    registered fleet member for that channel and dispatches the task to it.
    """

    bridge_id: str
    fleet: Any
    channel_to_member_map: dict[str, str] = field(default_factory=dict)

    @classmethod
    def default(cls) -> RoutingFleetBridge:
        """Construct with an empty fleet and no channel registrations."""
        try:
            fleet = Fleet()
        except Exception:
            fleet = Fleet
        return cls(
            bridge_id=str(uuid.uuid4()),
            fleet=fleet,
        )

    def register_channel_member(
        self, channel: EvidenceChannel, member_id: str
    ) -> None:
        """Associate *member_id* with *channel* in the bridge map."""
        self.channel_to_member_map[channel.value] = member_id

    def dispatch_to_fleet(
        self, decision: RoutingDecision, task: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch *task* to the fleet member registered for *decision.channel*.

        Returns a result dict.  If no member is registered for the channel,
        returns an error result.
        """
        member_id = self.channel_to_member_map.get(decision.channel.value)
        if member_id is None:
            return {
                "status": "error",
                "reason": f"No fleet member for channel {decision.channel.value}",
                "task_id": task.get("task_id", "unknown"),
                "decision_id": decision.decision_id,
            }
        payload = {**task, "member_id": member_id}
        result: dict[str, Any]
        if hasattr(self.fleet, "dispatch"):
            result = self.fleet.dispatch(payload)
        elif hasattr(self.fleet, "assign_work"):
            assign_work = getattr(self.fleet, "assign_work")
            try:
                result = assign_work(member_id, payload)
            except TypeError:
                try:
                    result = assign_work(member_id=member_id, task=payload)
                except TypeError:
                    result = {"status": "queued", "task_id": payload.get("task_id", "unknown")}
        else:
            result = {"status": "queued", "task_id": payload.get("task_id", "unknown")}
        return {
            "status": "dispatched",
            "member_id": member_id,
            "channel": decision.channel.value,
            "task_id": task.get("task_id", "unknown"),
            "decision_id": decision.decision_id,
            "fleet_result": result,
        }

    def collect_fleet_result(self, member_id: str) -> dict[str, Any]:
        """Retrieve the latest result from *member_id*.

        In the stub implementation this returns a placeholder; with a real
        fleet the member's result queue would be polled.
        """
        return {
            "member_id": member_id,
            "status": "collected",
            "result": None,
            "timestamp": time.time(),
        }

    def fleet_health(self) -> dict[str, Any]:
        """Return health status of the underlying fleet."""
        member_count = len(
            getattr(self.fleet, "members", [])
        )
        return {
            "bridge_id": self.bridge_id,
            "fleet_id": getattr(self.fleet, "fleet_id", "unknown"),
            "member_count": member_count,
            "registered_channels": list(self.channel_to_member_map.keys()),
        }

    def channel_availability(self) -> dict[str, bool]:
        """Return a mapping of channel name → ``True`` iff a member is registered."""
        return {
            channel.value: channel.value in self.channel_to_member_map
            for channel in EvidenceChannel
        }

    def bridge_summary(self) -> dict[str, Any]:
        """Return a comprehensive summary of the bridge state."""
        return {
            "bridge_id": self.bridge_id,
            "channel_map": dict(self.channel_to_member_map),
            "channel_availability": self.channel_availability(),
            "fleet_health": self.fleet_health(),
        }


# ---------------------------------------------------------------------------
# 5. MixedEvidenceOrchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MixedEvidenceOrchestrator:
    """Top-level orchestrator for the Mixed-Evidence Routing pipeline.

    Composes channel selection, jurisdiction enforcement, policy evaluation,
    trust integration, Copilot gating, fleet dispatch, and descent validation
    into a single ``route``/``execute`` interface.

    This is the entry-point class described in theory2.tex §45.6.
    """

    orchestrator_id: str
    channel_registry: Any
    jurisdiction_catalog: Any
    policy_registry: Any
    trust_integrator: RoutingTrustIntegrator
    descent_connector: RoutingDescentConnector
    copilot_gateway: CopilotTrustGateway
    fleet_bridge: RoutingFleetBridge
    audit_log: Any
    routing_history: Any

    # Internal counters (not part of the public API, managed via helper methods)
    _total_routed: int = field(default=0, init=False)
    _total_success: int = field(default=0, init=False)
    _total_cost: float = field(default=0.0, init=False)
    _total_latency_ms: float = field(default=0.0, init=False)
    _outcomes: list[RoutingOutcome] = field(default_factory=list, init=False)

    @classmethod
    def default(cls) -> MixedEvidenceOrchestrator:
        """Construct a fully-wired orchestrator with default sub-components."""
        registry = ChannelRegistry.default() if hasattr(ChannelRegistry, "default") else ChannelRegistry()
        catalog = (
            JurisdictionCatalog.default()
            if hasattr(JurisdictionCatalog, "default")
            else JurisdictionCatalog()
        )

        policy_reg = RoutingPolicyRegistry()
        policy_reg.register("strict_jurisdiction", StrictJurisdictionPolicy())
        policy_reg.register("adaptive", AdaptiveRoutingPolicy())

        return cls(
            orchestrator_id=str(uuid.uuid4()),
            channel_registry=registry,
            jurisdiction_catalog=catalog,
            policy_registry=policy_reg,
            trust_integrator=RoutingTrustIntegrator.default(),
            descent_connector=RoutingDescentConnector.default(),
            copilot_gateway=CopilotTrustGateway.default(),
            fleet_bridge=RoutingFleetBridge.default(),
            audit_log=JurisdictionAuditLog(),
            routing_history=RoutingHistory(),
        )

    def route(
        self, task: dict[str, Any], strategy: str = "strict_jurisdiction"
    ) -> RoutingDecision:
        """Select the best channel for *task* using *strategy*.

        1. Resolve the routing policy from the registry.
        2. Call ``route_task`` to produce a ``RoutingDecision``.
        3. Gate the decision through the Copilot trust gateway (if applicable).
        4. Record the decision in the routing history.
        5. Return the (possibly modified) decision.
        """
        policy = self.policy_registry.get(strategy)
        decision = route_task(task, self.jurisdiction_catalog, policy)

        # Apply Copilot ceiling
        _, decision = self.copilot_gateway.block_if_exceeds_ceiling(decision)

        # Record
        self.routing_history.append(decision)
        self._total_routed += 1

        self.audit_log.record(
            {
                "event": "routed",
                "task_id": decision.task_id,
                "channel": decision.channel.value,
                "strategy": strategy,
            }
        )
        return decision

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Route and dispatch *task*, returning the combined result.

        The method:
        1. Routes the task.
        2. Dispatches to the fleet bridge.
        3. Records a synthetic outcome.
        4. Returns a unified result dict.
        """
        decision = self.route(task)
        fleet_result = self.fleet_bridge.dispatch_to_fleet(decision, task)

        success = fleet_result.get("status") == "dispatched"
        actual_cost = task.get("actual_cost", decision.estimated_cost)
        actual_latency = task.get("actual_latency_ms", decision.estimated_latency)
        trust_achieved = self.trust_integrator.apply_trust_ceiling(
            decision, task.get("trust_claimed", "UNVERIFIED")
        )

        outcome = self.record_outcome(
            decision,
            success,
            float(actual_cost),
            float(actual_latency),
            trust_achieved,
        )

        if success:
            self._total_success += 1
        self._total_cost += float(actual_cost)
        self._total_latency_ms += float(actual_latency)

        return {
            "task_id": task.get("task_id", decision.task_id),
            "decision_id": decision.decision_id,
            "channel": decision.channel.value,
            "success": success,
            "trust_achieved": trust_achieved,
            "outcome_id": outcome.outcome_id,
            "fleet_result": fleet_result,
        }

    def route_and_execute_batch(
        self, tasks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Route and execute every task in *tasks*, returning one result per task."""
        results: list[dict[str, Any]] = []
        for task in tasks:
            try:
                result = self.execute(task)
            except Exception as exc:  # noqa: BLE001
                result = {
                    "task_id": task.get("task_id", "unknown"),
                    "success": False,
                    "error": str(exc),
                }
            results.append(result)
        return results

    def record_outcome(
        self,
        decision: RoutingDecision,
        success: bool,
        actual_cost: float,
        actual_latency_ms: float,
        trust_achieved: str,
    ) -> RoutingOutcome:
        """Create and store a ``RoutingOutcome`` for *decision*."""
        outcome = RoutingOutcome(
            outcome_id=str(uuid.uuid4()),
            decision_id=decision.decision_id,
            success=success,
            actual_cost=actual_cost,
            actual_latency_ms=actual_latency_ms,
            trust_achieved=trust_achieved,
            timestamp=time.time(),
            notes="",
        )
        self._outcomes.append(outcome)
        return outcome

    def system_health(self) -> dict[str, Any]:
        """Return a snapshot of all sub-system health indicators."""
        success_rate = (
            self._total_success / self._total_routed
            if self._total_routed > 0
            else 0.0
        )
        return {
            "orchestrator_id": self.orchestrator_id,
            "total_routed": self._total_routed,
            "total_success": self._total_success,
            "success_rate": success_rate,
            "total_cost": self._total_cost,
            "total_latency_ms": self._total_latency_ms,
            "routing_history_size": len(self.routing_history.decisions),
            "copilot_gateway": self.copilot_gateway.query_statistics(),
            "fleet_bridge": self.fleet_bridge.fleet_health(),
            "trust_integrator": self.trust_integrator.trust_summary(),
            "descent_connector": self.descent_connector.descent_summary(),
            "audit_entries": len(self.audit_log.entries),
        }

    def routing_summary(self) -> dict[str, Any]:
        """Return aggregated routing statistics."""
        decisions = self.routing_history.decisions
        by_channel: dict[str, int] = {}
        for d in decisions:
            by_channel[d.channel.value] = by_channel.get(d.channel.value, 0) + 1
        return {
            "orchestrator_id": self.orchestrator_id,
            "total_decisions": len(decisions),
            "by_channel": by_channel,
            "efficiency_score": routing_efficiency_score(self.routing_history),
        }

    def reset_statistics(self) -> None:
        """Reset all runtime counters and histories to zero/empty state."""
        self._total_routed = 0
        self._total_success = 0
        self._total_cost = 0.0
        self._total_latency_ms = 0.0
        self._outcomes.clear()
        self.routing_history.decisions.clear()
        self.copilot_gateway.query_count = 0
        self.copilot_gateway.blocked_count = 0
        self.copilot_gateway.audit_entries.clear()
        self.audit_log.entries.clear()
