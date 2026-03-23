"""Mixed evidence routing models for the jugeo orchestration layer.

Defines data structures for routing decisions, jurisdiction maps, channel
selection, Copilot query records, human escalations, routing history, and
per-channel statistics.  Corresponds to theory2.tex Ch45 mixed evidence
routing.
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel
    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

    class TrustLevel(str, enum.Enum):  # type: ignore[no-redef]
        """Fallback TrustLevel used when jugeo.evidence.trust is unavailable."""

        MECHANICALLY_VERIFIED = "mechanically_verified"
        SOLVER_DISCHARGED = "solver_discharged"
        RUNTIME_WITNESSED = "runtime_witnessed"
        HUMAN_ATTESTED = "human_attested"
        ORACLE_PROPOSED = "oracle_proposed"
        COPILOT_SUGGESTED = "copilot_suggested"
        UNVERIFIED = "unverified"
        CONTRADICTED = "contradicted"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EvidenceChannel(str, Enum):
    """Identifies a source of evidence used during claim evaluation."""

    Z3 = "z3"
    COPILOT_LLM = "copilot_llm"
    RUNTIME_WITNESS = "runtime_witness"
    HUMAN = "human"
    COMPOSITE = "composite"


class RoutingStrategy(str, Enum):
    """Routing optimisation objective."""

    STRICT_JURISDICTION = "strict_jurisdiction"
    COST_OPTIMAL = "cost_optimal"
    LATENCY_OPTIMAL = "latency_optimal"
    TRUST_OPTIMAL = "trust_optimal"
    LOAD_BALANCED = "load_balanced"


class EscalationUrgency(str, Enum):
    """Priority level for a human escalation request."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# RoutingDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """An immutable record of a single routing decision for a task.

    Attributes:
        decision_id: Unique identifier for this decision.
        task_id: The task this decision was made for.
        channel: The evidence channel selected.
        rationale: Human-readable explanation for the selection.
        confidence: Confidence in the decision in [0, 1].
        estimated_cost: Estimated monetary or compute cost.
        estimated_latency: Estimated latency in seconds.
        timestamp: Unix epoch timestamp when the decision was made.
        metadata: Arbitrary key-value annotations.
    """

    decision_id: str
    task_id: str
    channel: EvidenceChannel
    rationale: str
    confidence: float = 0.8
    estimated_cost: float = 1.0
    estimated_latency: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        channel: EvidenceChannel,
        rationale: str,
        confidence: float = 0.8,
        estimated_cost: float = 1.0,
        estimated_latency: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "RoutingDecision":
        """Create a new RoutingDecision with a generated ID and timestamp."""
        return cls(
            decision_id=str(uuid.uuid4()),
            task_id=task_id,
            channel=channel,
            rationale=rationale,
            confidence=confidence,
            estimated_cost=estimated_cost,
            estimated_latency=estimated_latency,
            timestamp=time.time(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "channel": self.channel.value,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "estimated_cost": self.estimated_cost,
            "estimated_latency": self.estimated_latency,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def is_confident(self, threshold: float = 0.7) -> bool:
        """Return True when confidence meets or exceeds threshold."""
        return self.confidence >= threshold

    def cost_benefit_ratio(self) -> float:
        """Return estimated cost divided by confidence."""
        return self.estimated_cost / max(self.confidence, 0.001)

    def age_seconds(self) -> float:
        """Return the age of the decision in seconds."""
        return max(0.0, time.time() - self.timestamp)

    def with_metadata(self, key: str, value: Any) -> "RoutingDecision":
        """Return a copy of this decision with an updated metadata entry."""
        metadata = dict(self.metadata)
        metadata[key] = value
        return replace(self, metadata=metadata)

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"RoutingDecision({self.decision_id[:8]}) "
            f"task={self.task_id} channel={self.channel.value} "
            f"confidence={self.confidence:.2f}"
        )


# ---------------------------------------------------------------------------
# JurisdictionMap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JurisdictionMap:
    """Describes which claim kinds an evidence channel is authoritative for.

    Attributes:
        map_id: Unique identifier for this map.
        channel: The evidence channel this map belongs to.
        supported_claim_kinds: Tuple of claim-kind tokens the channel handles.
        max_complexity: Upper bound on task complexity the channel can handle.
        min_trust_level: Minimum TrustLevel name produced by this channel.
        exclusions: Claim kinds explicitly excluded from this channel scope.
    """

    map_id: str
    channel: EvidenceChannel
    supported_claim_kinds: tuple[str, ...]
    max_complexity: float
    min_trust_level: str
    exclusions: tuple[str, ...]

    @classmethod
    def new(
        cls,
        channel: EvidenceChannel,
        supported_claim_kinds: tuple[str, ...] | list[str],
        max_complexity: float = 10.0,
        min_trust_level: str = "UNVERIFIED",
        exclusions: tuple[str, ...] | list[str] = (),
    ) -> "JurisdictionMap":
        """Create a new JurisdictionMap with a generated ID."""
        return cls(
            map_id=str(uuid.uuid4()),
            channel=channel,
            supported_claim_kinds=tuple(supported_claim_kinds),
            max_complexity=max_complexity,
            min_trust_level=min_trust_level,
            exclusions=tuple(exclusions),
        )

    def covers(self, claim_kind: str) -> bool:
        """Return True if claim_kind is within this jurisdiction."""
        return claim_kind in self.supported_claim_kinds and claim_kind not in self.exclusions

    def can_handle(self, claim: dict[str, Any]) -> bool:
        """Return True if this map can handle the supplied claim."""
        claim_kind = str(claim.get("claim_kind", ""))
        complexity = self.complexity_score(claim)
        return self.covers(claim_kind) and complexity <= self.max_complexity

    def complexity_score(self, claim: dict[str, Any]) -> float:
        """Return the claim complexity score with a default of 1.0."""
        return float(claim.get("complexity", 1.0))

    def is_exclusive_to(self, claim_kind: str) -> bool:
        """Return True when the claim kind is explicitly excluded."""
        return claim_kind in self.exclusions

    def coverage_fraction(self, universe: list[str] | tuple[str, ...]) -> float:
        """Return the fraction of a universe of claim kinds covered by this map."""
        if not universe:
            return 0.0
        covered = sum(1 for claim_kind in universe if self.covers(claim_kind))
        return covered / len(universe)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "map_id": self.map_id,
            "channel": self.channel.value,
            "supported_claim_kinds": list(self.supported_claim_kinds),
            "max_complexity": self.max_complexity,
            "min_trust_level": self.min_trust_level,
            "exclusions": list(self.exclusions),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        kinds = ", ".join(self.supported_claim_kinds[:3])
        suffix = "..." if len(self.supported_claim_kinds) > 3 else ""
        return (
            f"JurisdictionMap({self.map_id[:8]}) "
            f"channel={self.channel.value} kinds=[{kinds}{suffix}]"
        )


# ---------------------------------------------------------------------------
# EvidenceChannelSelector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceChannelSelector:
    """Selects an appropriate evidence channel for a task.

    Attributes:
        selector_id: Unique identifier for this selector instance.
        jurisdiction_maps: Ordered list of JurisdictionMap entries.
        cost_weights: Per-channel weighting for cost scoring.
        latency_weights: Per-channel weighting for latency scoring.
    """

    selector_id: str
    jurisdiction_maps: list[JurisdictionMap]
    cost_weights: dict[str, float]
    latency_weights: dict[str, float]

    @classmethod
    def default(cls) -> "EvidenceChannelSelector":
        """Create a selector pre-loaded with sensible default jurisdiction maps."""
        z3_kinds = (
            "equality",
            "arithmetic",
            "smt",
            "bitvector",
            "logical",
            "type_constraint",
            "invariant",
        )
        copilot_kinds = (
            "semantic",
            "natural_language",
            "suggestion",
            "summary",
            "heuristic",
            "docstring",
        )
        runtime_kinds = (
            "runtime",
            "runtime_invariant",
            "trace",
            "execution_trace",
            "performance",
            "coverage",
        )
        human_kinds = (
            "review",
            "approval",
            "policy",
            "ethics",
            "edge_case",
            "ambiguous",
            "unknown_claim_kind",
        )
        maps: list[JurisdictionMap] = [
            JurisdictionMap.new(
                EvidenceChannel.Z3,
                supported_claim_kinds=z3_kinds,
                max_complexity=8.0,
                min_trust_level="SOLVER_DISCHARGED",
            ),
            JurisdictionMap.new(
                EvidenceChannel.COPILOT_LLM,
                supported_claim_kinds=copilot_kinds,
                max_complexity=10.0,
                min_trust_level="COPILOT_SUGGESTED",
            ),
            JurisdictionMap.new(
                EvidenceChannel.RUNTIME_WITNESS,
                supported_claim_kinds=runtime_kinds,
                max_complexity=6.0,
                min_trust_level="RUNTIME_WITNESSED",
            ),
            JurisdictionMap.new(
                EvidenceChannel.HUMAN,
                supported_claim_kinds=human_kinds,
                max_complexity=20.0,
                min_trust_level="HUMAN_ATTESTED",
            ),
            JurisdictionMap.new(
                EvidenceChannel.COMPOSITE,
                supported_claim_kinds=z3_kinds + copilot_kinds + runtime_kinds + human_kinds + ("composite",),
                max_complexity=20.0,
                min_trust_level="SOLVER_DISCHARGED",
            ),
        ]
        return cls(
            selector_id=str(uuid.uuid4()),
            jurisdiction_maps=maps,
            cost_weights={ch.value: 1.0 for ch in EvidenceChannel},
            latency_weights={ch.value: 1.0 for ch in EvidenceChannel},
        )

    def select(
        self,
        task: dict[str, Any],
        candidates: list[EvidenceChannel] | None = None,
    ) -> RoutingDecision:
        """Select the best channel for task and return a routing decision."""
        task_id = str(task.get("task_id") or uuid.uuid4())
        ranked = self.rank_channels(task, candidates=candidates)
        if ranked and ranked[0][1] > 0.0:
            channel, score = ranked[0]
            rationale = f"Selected {channel.value} with score {score:.3f}"
            confidence = min(1.0, max(0.5, score))
        else:
            channel = EvidenceChannel.HUMAN
            rationale = "Falling back to human review due to missing jurisdiction coverage"
            confidence = 0.8
        return RoutingDecision.new(
            task_id=task_id,
            channel=channel,
            rationale=rationale,
            confidence=confidence,
            metadata={"claim_kind": task.get("claim_kind", "")},
        )

    def add_map(self, m: JurisdictionMap) -> None:
        """Append m to the jurisdiction maps list."""
        self.jurisdiction_maps.append(m)

    def add_jurisdiction_map(self, m: JurisdictionMap) -> None:
        """Compatibility alias for adding a jurisdiction map."""
        self.add_map(m)

    def remove_map(self, map_id: str) -> bool:
        """Remove the map with the given map_id.  Returns True if found."""
        before = len(self.jurisdiction_maps)
        self.jurisdiction_maps = [jm for jm in self.jurisdiction_maps if jm.map_id != map_id]
        return len(self.jurisdiction_maps) < before

    def remove_jurisdiction_map(self, map_id: str) -> bool:
        """Compatibility alias for removing a jurisdiction map."""
        return self.remove_map(map_id)

    def channel_score(self, channel: EvidenceChannel, task: dict[str, Any]) -> float:
        """Return a normalised suitability score for a channel."""
        claim_kind = str(task.get("claim_kind", ""))
        maps = [jm for jm in self.jurisdiction_maps if jm.channel == channel]
        if not maps:
            return 0.0
        score = 0.0
        for jmap in maps:
            if not jmap.covers(claim_kind):
                continue
            complexity = jmap.complexity_score(task)
            if complexity > jmap.max_complexity:
                continue
            complexity_component = 1.0 - min(1.0, complexity / max(jmap.max_complexity, 1.0))
            score = max(score, 0.6 + 0.4 * complexity_component)
        return max(0.0, min(1.0, score))

    def rank_channels(
        self,
        task: dict[str, Any],
        candidates: list[EvidenceChannel] | None = None,
    ) -> list[tuple[EvidenceChannel, float]]:
        """Return candidate channels ranked by descending score."""
        channels = list(candidates) if candidates is not None else list(EvidenceChannel)
        ranked = [(channel, self.channel_score(channel, task)) for channel in channels]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def explain(self, decision: RoutingDecision) -> str:
        """Return a human-readable explanation of a routing decision."""
        return (
            f"Routing Decision {decision.decision_id}: "
            f"task={decision.task_id}, channel={decision.channel.value}, "
            f"confidence={decision.confidence:.2f}, rationale={decision.rationale}"
        )

    def covered_channels(self) -> list[EvidenceChannel]:
        """Return the deduplicated list of channels that have at least one map."""
        seen: set[EvidenceChannel] = set()
        result: list[EvidenceChannel] = []
        for jm in self.jurisdiction_maps:
            if jm.channel not in seen:
                seen.add(jm.channel)
                result.append(jm.channel)
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "selector_id": self.selector_id,
            "jurisdiction_maps": [jm.to_dict() for jm in self.jurisdiction_maps],
            "cost_weights": self.cost_weights,
            "latency_weights": self.latency_weights,
        }


# ---------------------------------------------------------------------------
# CopilotQueryRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CopilotQueryRecord:
    """Immutable record of a single Copilot LLM query and its response.

    Attributes:
        query_id: Unique identifier for this query record.
        query_text: The prompt sent to the model.
        response_text: The raw text returned by the model.
        trust_ceiling: Maximum TrustLevel name assignable to the response.
        latency_ms: Round-trip latency in milliseconds.
        token_count: Total tokens consumed (prompt + completion).
        timestamp: Unix epoch timestamp of the query.
        model_id: Identifier of the model used.
    """

    query_id: str
    query_text: str
    response_text: str
    trust_ceiling: str
    latency_ms: float
    token_count: int
    timestamp: float
    model_id: str

    @classmethod
    def new(
        cls,
        query_text: str,
        response_text: str,
        trust_ceiling: str = "COPILOT_SUGGESTED",
        latency_ms: float = 0.0,
        token_count: int = 0,
        model_id: str = "unknown",
    ) -> "CopilotQueryRecord":
        """Create a new CopilotQueryRecord with a generated ID and timestamp."""
        return cls(
            query_id=str(uuid.uuid4()),
            query_text=query_text,
            response_text=response_text,
            trust_ceiling=trust_ceiling,
            latency_ms=latency_ms,
            token_count=token_count,
            timestamp=time.time(),
            model_id=model_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "response_text": self.response_text,
            "trust_ceiling": self.trust_ceiling,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "timestamp": self.timestamp,
            "model_id": self.model_id,
        }

    def truncated_query(self, max_len: int = 100) -> str:
        """Return the query text truncated to max_len characters."""
        if len(self.query_text) <= max_len:
            return self.query_text
        return self.query_text[:max_len] + "..."

    def trust_adjusted_score(self) -> float:
        """Return a bounded reliability score biased by token usage."""
        return min(1.0, 0.5 + min(self.token_count / 1000.0, 0.5))

    def is_reliable(
        self,
        min_tokens: int = 10,
        max_latency_ms: float = 30_000.0,
    ) -> bool:
        """Return True when the record is sufficiently informative and timely."""
        return self.token_count >= min_tokens and self.latency_ms <= max_latency_ms

    def token_efficiency(self) -> float:
        """Return tokens per millisecond."""
        if self.latency_ms <= 0:
            return 0.0
        return self.token_count / self.latency_ms

    def summary(self, max_response_len: int = 80) -> str:
        """Return a concise human-readable summary."""
        response = self.response_text
        if len(response) > max_response_len:
            response = response[:max_response_len] + "..."
        return (
            f"CopilotQueryRecord(model={self.model_id}, "
            f"tokens={self.token_count}, latency_ms={self.latency_ms}, "
            f"response={response})"
        )


# ---------------------------------------------------------------------------
# HumanEscalation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HumanEscalation:
    """A mutable record tracking a human escalation lifecycle.

    Attributes:
        escalation_id: Unique identifier for this escalation.
        task_id: The task that triggered the escalation.
        reason: Explanation of why escalation was required.
        urgency: Priority of the escalation.
        assigned_to: Username or identifier of the assigned human reviewer.
        resolved_at: Unix epoch timestamp when the escalation was resolved.
        resolution: Free-text description of the resolution.
        created_at: Unix epoch timestamp when the escalation was created.
    """

    escalation_id: str
    task_id: str
    reason: str
    urgency: EscalationUrgency
    assigned_to: str | None
    resolved_at: float | None
    resolution: str | None
    created_at: float

    @classmethod
    def new(
        cls,
        task_id: str,
        reason: str,
        urgency: EscalationUrgency = EscalationUrgency.MEDIUM,
        assigned_to: str | None = None,
    ) -> "HumanEscalation":
        """Create a new unresolved HumanEscalation."""
        return cls(
            escalation_id=str(uuid.uuid4()),
            task_id=task_id,
            reason=reason,
            urgency=urgency,
            assigned_to=assigned_to,
            resolved_at=None,
            resolution=None,
            created_at=time.time(),
        )

    def resolve(self, resolution: str, resolver: str | None = None) -> None:
        """Mark the escalation as resolved.

        Args:
            resolution: Description of how the escalation was resolved.
            resolver: Optional identifier of the human who resolved it.
        """
        self.resolution = resolution
        self.resolved_at = time.time()
        if resolver is not None:
            self.assigned_to = resolver

    def is_resolved(self) -> bool:
        """Return True if the escalation has been resolved."""
        return self.resolved_at is not None

    def age_hours(self) -> float:
        """Return the number of hours since the escalation was created."""
        return (time.time() - self.created_at) / 3600.0

    def urgency_level(self) -> EscalationUrgency:
        """Return urgency as a validated enum member."""
        if isinstance(self.urgency, EscalationUrgency):
            return self.urgency
        try:
            return EscalationUrgency(str(self.urgency))
        except ValueError:
            return EscalationUrgency.MEDIUM

    def sla_breached(self, sla_hours: float = 24.0) -> bool:
        """Return True when the unresolved escalation exceeds the SLA."""
        return not self.is_resolved() and self.age_hours() > sla_hours

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "escalation_id": self.escalation_id,
            "task_id": self.task_id,
            "reason": self.reason,
            "urgency": self.urgency_level().value,
            "assigned_to": self.assigned_to,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "created_at": self.created_at,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        status = "resolved" if self.is_resolved() else "open"
        return (
            f"HumanEscalation({self.escalation_id[:8]}) "
            f"task={self.task_id} urgency={self.urgency_level().value} status={status}"
        )


# ---------------------------------------------------------------------------
# RoutingHistory
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RoutingHistory:
    """Append-only log of RoutingDecision objects for a session.

    Attributes:
        history_id: Unique identifier for this history instance.
        decisions: Ordered list of routing decisions, oldest first.
    """

    history_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    decisions: list[RoutingDecision] = field(default_factory=list)

    @classmethod
    def new(cls) -> "RoutingHistory":
        """Create an empty RoutingHistory."""
        return cls()

    def append(self, decision: RoutingDecision) -> None:
        """Append decision to the history."""
        self.decisions.append(decision)

    def record(self, decision: RoutingDecision) -> None:
        """Compatibility alias for append."""
        self.append(decision)

    def recent(self, n: int = 10) -> list[RoutingDecision]:
        """Return the n most recent decisions."""
        return self.decisions[-n:]

    def for_task(self, task_id: str) -> list[RoutingDecision]:
        """Return all decisions for the given task_id."""
        return [d for d in self.decisions if d.task_id == task_id]

    def by_channel(self, channel: EvidenceChannel) -> list[RoutingDecision]:
        """Return all decisions for the given channel."""
        return [d for d in self.decisions if d.channel == channel]

    def success_rate(self, channel: EvidenceChannel | None = None) -> float:
        """Return the share of confident decisions."""
        decisions = self.by_channel(channel) if channel is not None else self.decisions
        if not decisions:
            return 0.0
        return sum(1 for decision in decisions if decision.is_confident()) / len(decisions)

    def average_confidence(self) -> float:
        """Return average confidence across decisions."""
        if not self.decisions:
            return 0.0
        return sum(decision.confidence for decision in self.decisions) / len(self.decisions)

    def channel_counts(self) -> dict[str, int]:
        """Return a mapping of channel value to number of times selected."""
        counts: dict[str, int] = {}
        for d in self.decisions:
            counts[d.channel.value] = counts.get(d.channel.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "history_id": self.history_id,
            "decision_count": len(self.decisions),
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ---------------------------------------------------------------------------
# ChannelStats
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelStats:
    """Mutable runtime statistics for a single evidence channel.

    Attributes:
        channel: The evidence channel these stats belong to.
        call_count: Total number of calls recorded.
        success_count: Number of successful calls.
        total_cost: Cumulative cost across all calls.
        total_latency_ms: Cumulative latency in milliseconds.
        error_count: Number of failed calls.
        last_updated: Unix epoch timestamp of the last update.
    """

    channel: EvidenceChannel
    call_count: int = 0
    success_count: int = 0
    total_cost: float = 0.0
    total_latency: float = 0.0
    error_count: int = 0
    last_used: float | None = None

    @classmethod
    def new(cls, channel: EvidenceChannel) -> "ChannelStats":
        """Create a zero-initialised ChannelStats for channel."""
        return cls(channel=channel)

    def record_call(self, success: bool, cost: float, latency_ms: float) -> None:
        """Record the outcome of a single channel call.

        Args:
            success: Whether the call succeeded.
            cost: Monetary or compute cost of the call.
            latency_ms: Round-trip latency in milliseconds.
        """
        self.call_count += 1
        self.total_cost += cost
        self.total_latency += latency_ms
        if success:
            self.success_count += 1
        else:
            self.error_count += 1
        self.last_used = time.time()

    @property
    def total_requests(self) -> int:
        """Alias for the total number of recorded calls."""
        return self.call_count

    @property
    def successful_requests(self) -> int:
        """Alias for the total number of successful calls."""
        return self.success_count

    @property
    def total_successes(self) -> int:
        """Compatibility alias for successful request count."""
        return self.success_count

    @property
    def total_latency_ms(self) -> float:
        """Compatibility alias for total latency."""
        return self.total_latency

    @property
    def last_updated(self) -> float | None:
        """Compatibility alias for last usage time."""
        return self.last_used

    def update(self, decision: RoutingDecision, success: bool) -> None:
        """Update stats from a routing decision outcome."""
        self.record_call(
            success=success,
            cost=decision.estimated_cost,
            latency_ms=decision.estimated_latency,
        )

    def success_rate(self) -> float:
        """Return the fraction of successful calls, or 0.0 if none recorded."""
        return self.success_count / self.call_count if self.call_count else 0.0

    def avg_cost(self) -> float:
        """Return the mean cost per call, or 0.0 if none recorded."""
        return self.total_cost / self.call_count if self.call_count else 0.0

    def avg_latency_ms(self) -> float:
        """Return the mean latency in milliseconds, or 0.0 if none recorded."""
        return self.total_latency / self.call_count if self.call_count else 0.0

    def average_cost(self) -> float:
        """Compatibility alias for average cost."""
        return self.avg_cost()

    def average_latency(self) -> float:
        """Compatibility alias for average latency."""
        return self.avg_latency_ms()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "channel": self.channel.value,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "total_cost": self.total_cost,
            "total_latency": self.total_latency,
            "last_used": self.last_used,
            "success_rate": self.success_rate(),
            "average_cost": self.average_cost(),
            "average_latency": self.average_latency(),
        }

    def reset(self) -> None:
        """Reset all counters and accumulators to zero."""
        self.call_count = 0
        self.success_count = 0
        self.total_cost = 0.0
        self.total_latency = 0.0
        self.error_count = 0
        self.last_used = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TrustLevel",
    "EvidenceChannel",
    "RoutingStrategy",
    "EscalationUrgency",
    "RoutingDecision",
    "JurisdictionMap",
    "EvidenceChannelSelector",
    "CopilotQueryRecord",
    "HumanEscalation",
    "RoutingHistory",
    "ChannelStats",
]
