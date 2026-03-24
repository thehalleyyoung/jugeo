"""Shared types, enumerations, dataclasses, and protocols for jugeo-agents.

Every concept from the JuGeo multi-agent verification theory has a
concrete Python representation here.  Modules throughout the package
import from this central registry so that there is a single source of
truth for names, orderings, and structural invariants.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any, Callable, Protocol, Sequence, runtime_checkable


# ---------------------------------------------------------------------------
# 1. Trust levels — ordered algebra
# ---------------------------------------------------------------------------

class TrustLevel(IntEnum):
    """Trust level for an agent claim.

    Ordered from lowest to highest.  The ordering encodes the JuGeo axiom
    that trust strengthens *only* through named evidence channels — never
    silently.

    The integer values enable ``min()`` / ``max()`` comparisons directly.
    """

    SELF_CONTRADICTED = 0
    UNGROUNDED_CLAIM = 10
    WEAK_MODEL_GENERATED = 20
    STRONG_MODEL_GENERATED = 30
    CROSS_AGENT_CONFIRMED = 40
    CITATION_BACKED = 50
    RAG_GROUNDED = 60
    TOOL_EXECUTED = 70
    TOOL_VERIFIED = 80
    HUMAN_VERIFIED = 90
    FORMALLY_PROVEN = 100

    # Convenience predicates -------------------------------------------------

    @property
    def is_grounded(self) -> bool:
        """True when supported by evidence beyond pure LLM generation."""
        return self >= TrustLevel.CROSS_AGENT_CONFIRMED

    @property
    def is_verified(self) -> bool:
        """True when independently verified (tool, human, or proof)."""
        return self >= TrustLevel.TOOL_VERIFIED


# ---------------------------------------------------------------------------
# 2. Cohomology / obstruction classification
# ---------------------------------------------------------------------------

class CohomologyClass(Enum):
    """Čech cohomology class of an obstruction.

    H0 = section incompleteness (agent didn't produce output)
    H1 = pairwise contradiction (two agents disagree on a shared fact)
    H2 = cascading hallucination (pairwise consistent but globally fabricated)
    PHANTOM = consistent everywhere but entirely ungrounded
    """

    H0 = "H0"
    H1 = "H1"
    H2 = "H2"
    PHANTOM = "phantom"


class ObstructionKind(Enum):
    """Fine-grained obstruction taxonomy."""

    SECTION_INCOMPLETE = auto()       # H0: agent didn't produce output
    COVER_GAP = auto()                # H0: subtask decomposition is incomplete
    TEMPORAL_CONTRADICTION = auto()   # H1: dates/times disagree
    QUANTITATIVE_CONTRADICTION = auto()  # H1: numbers disagree
    DIRECTIONAL_CONTRADICTION = auto()   # H1: trend/direction disagrees
    ENTITY_CONTRADICTION = auto()     # H1: names/identities disagree
    LOGICAL_CONTRADICTION = auto()    # H1: reasoning contradicts
    DEPENDENCY_CONTRADICTION = auto()   # H1: ordering/dependency disagrees
    TYPE_MISMATCH = auto()            # H1: expected vs. actual type
    TRUST_BOUNDARY_VIOLATION = auto()   # Silent promotion detected
    CASCADING_HALLUCINATION = auto()  # H2: multi-hop fabrication
    PHANTOM_GLOBAL_SECTION = auto()   # Phantom: consistent but ungrounded
    CONTEXT_OVERFLOW = auto()         # Section truncation
    INFINITE_LOOP = auto()            # Convergence failure
    TOOL_HALLUCINATION = auto()       # Provenance forgery


# ---------------------------------------------------------------------------
# 3. Convergence phases and status
# ---------------------------------------------------------------------------

class ConvergencePhase(Enum):
    """Phase of the multi-agent pipeline."""

    EXPLORATION = "exploration"       # Agents generating initial outputs
    CONSOLIDATION = "consolidation"   # Cross-checking begins
    RESOLUTION = "resolution"         # Contradictions being resolved
    VERIFICATION = "verification"     # Claims being grounded/verified
    COMPLETE = "complete"             # Converged


class ConvergenceStatus(Enum):
    """Instantaneous convergence status."""

    UNKNOWN = "unknown"
    CONVERGING = "converging"
    STUCK = "stuck"
    DIVERGING = "diverging"
    CONVERGED = "converged"


# ---------------------------------------------------------------------------
# 4. Challenge types and outcomes
# ---------------------------------------------------------------------------

class ChallengeType(Enum):
    """Type of challenge one agent raises against another."""

    FACTUAL = "factual"           # "Your claim is wrong"
    LOGICAL = "logical"           # "Your reasoning doesn't follow"
    COMPLETENESS = "completeness"  # "You missed something"
    TRUST = "trust"               # "Your evidence doesn't support your claim level"


class ChallengeOutcome(Enum):
    """Outcome of a challenge adjudication."""

    UPHELD = "upheld"         # Challenge succeeds; original claim demoted
    OVERTURNED = "overturned"  # Challenge fails; original claim stands
    SPLIT = "split"           # Partial merit; both claims revised
    WITHDRAWN = "withdrawn"   # Challenger withdrew


# ---------------------------------------------------------------------------
# 5. Evidence channels
# ---------------------------------------------------------------------------

class EvidenceChannel(Enum):
    """Named evidence channel with declared trust ceiling."""

    CODE_EXECUTION = "code_execution"
    SQL_QUERY = "sql_query"
    API_CALL = "api_call"
    WEB_SEARCH = "web_search"
    RAG_RETRIEVAL = "rag_retrieval"
    LLM_VERIFICATION = "llm_verification"
    LLM_GENERATION = "llm_generation"
    HUMAN_REVIEW = "human_review"
    FORMAL_PROOF = "formal_proof"


# ---------------------------------------------------------------------------
# 6. Core data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FactualClaim:
    """A single factual claim extracted from agent output."""

    text: str
    subject: str = ""
    predicate: str = ""
    value: str = ""
    source_agent: str = ""
    source_text_span: tuple[int, int] = (0, 0)
    trust: TrustLevel = TrustLevel.UNGROUNDED_CLAIM
    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_trust(self, level: TrustLevel) -> FactualClaim:
        """Return a copy with a different trust level."""
        return FactualClaim(
            text=self.text,
            subject=self.subject,
            predicate=self.predicate,
            value=self.value,
            source_agent=self.source_agent,
            source_text_span=self.source_text_span,
            trust=level,
            claim_id=self.claim_id,
            timestamp=self.timestamp,
            metadata=self.metadata,
        )


@dataclass(slots=True)
class AgentOutput:
    """Complete output record for a single agent invocation."""

    agent_id: str
    output_text: str
    model: str = ""
    role: str = ""
    subtask: str = ""
    tools_used: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    rag_sources: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    claims: list[FactualClaim] = field(default_factory=list)
    trust: TrustLevel = TrustLevel.UNGROUNDED_CLAIM
    round_number: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    output_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass(slots=True)
class Contradiction:
    """A detected contradiction between two agent claims."""

    claim_a: FactualClaim
    claim_b: FactualClaim
    agent_a: str
    agent_b: str
    kind: ObstructionKind
    confidence: float = 1.0
    explanation: str = ""
    repair_hint: str = ""
    contradiction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass(slots=True)
class Obstruction:
    """A verified obstruction in the agent pipeline.

    Obstructions are the fundamental output of JuGeo verification: they
    describe *what went wrong* and *how bad it is* (cohomology class).
    """

    kind: ObstructionKind
    cohomology: CohomologyClass
    agents_involved: list[str]
    contradictions: list[Contradiction] = field(default_factory=list)
    description: str = ""
    repair_frontier: list[str] = field(default_factory=list)
    obstruction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class DescentResult:
    """Result of a descent check across agent outputs."""

    is_consistent: bool
    obstructions: list[Obstruction] = field(default_factory=list)
    checked_pairs: int = 0
    total_claims_checked: int = 0
    consistency_score: float = 1.0


@dataclass(slots=True)
class CoverageReport:
    """Report on task decomposition coverage."""

    is_complete: bool
    coverage_score: float
    covered_dimensions: set[str] = field(default_factory=set)
    gaps: set[str] = field(default_factory=set)
    redundancies: dict[str, int] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    dimension_assignments: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ProvenanceLink:
    """Single link in a provenance chain."""

    agent_id: str
    action: str
    source: str = ""
    trust: TrustLevel = TrustLevel.UNGROUNDED_CLAIM
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProvenanceChain:
    """Full provenance chain for a single claim."""

    claim: FactualClaim
    links: list[ProvenanceLink] = field(default_factory=list)

    @property
    def root_trust(self) -> TrustLevel:
        """Trust level at the origin of the chain."""
        if self.links:
            return self.links[-1].trust
        return self.claim.trust

    @property
    def weakest_link(self) -> ProvenanceLink | None:
        """The link with the lowest trust level."""
        if not self.links:
            return None
        return min(self.links, key=lambda lk: lk.trust)

    @property
    def overall_trust(self) -> TrustLevel:
        """Conservative join: overall trust is the minimum in the chain."""
        if not self.links:
            return self.claim.trust
        return TrustLevel(min(lk.trust for lk in self.links))


@dataclass(slots=True)
class ConvergenceSnapshot:
    """Single time-step snapshot of convergence metrics."""

    round_number: int
    coverage: float           # fraction of task covered [0,1]
    consistency: float        # 1 - obstruction density [0,1]
    trust_level: float        # average trust level (normalized to [0,1])
    obligation_pressure: float = 0.0  # fraction of open obligations
    lyapunov_v: float = 0.0  # weighted combination
    phase: ConvergencePhase = ConvergencePhase.EXPLORATION
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class TreatyResolution:
    """Result of a treaty negotiation between conflicting agents."""

    success: bool
    winning_agent: str = ""
    strategy_used: str = ""
    evidence: str = ""
    merged_text: str = ""
    audit_trail: list[str] = field(default_factory=list)
    resolution_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class Challenge:
    """A formal challenge from one agent against another's claim."""

    challenger: str
    challenged: str
    claim: FactualClaim
    challenge_type: ChallengeType
    evidence: str = ""
    proposed_alternative: str = ""
    outcome: ChallengeOutcome | None = None
    adjudication_evidence: str = ""
    challenge_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class RoutingDecision:
    """A decision to route a verification request to a specific channel."""

    claim: FactualClaim
    channel: EvidenceChannel
    required_trust: TrustLevel
    channel_ceiling: TrustLevel
    estimated_cost: float = 0.0
    rationale: str = ""
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass(slots=True)
class CalibrationRecord:
    """Single calibration observation for a (model, task_type) pair."""

    model: str
    task_type: str
    claim: FactualClaim
    was_accurate: bool
    was_hallucination: bool = False
    was_tool_verified: bool = False
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class PipelineReport:
    """Full verification report for a completed multi-agent pipeline."""

    descent_result: DescentResult
    coverage: CoverageReport
    trust_summary: dict[str, Any] = field(default_factory=dict)
    convergence_history: list[ConvergenceSnapshot] = field(default_factory=list)
    provenance_chains: list[ProvenanceChain] = field(default_factory=list)
    treaties: list[TreatyResolution] = field(default_factory=list)
    challenges: list[Challenge] = field(default_factory=list)
    total_agents: int = 0
    total_claims: int = 0
    total_rounds: int = 0
    final_phase: ConvergencePhase = ConvergencePhase.EXPLORATION
    final_lyapunov: float = 1.0
    bundle_diagnostics: dict[str, Any] = field(default_factory=dict)

    def summary_text(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Pipeline Report ({self.total_agents} agents, {self.total_rounds} rounds)",
            f"  Coverage: {self.coverage.coverage_score:.0%}"
            + (" ✅" if self.coverage.is_complete else f" — gaps: {self.coverage.gaps}"),
            f"  Consistency: {self.descent_result.consistency_score:.0%}"
            + (" ✅" if self.descent_result.is_consistent else ""),
            f"  Obstructions: {len(self.descent_result.obstructions)}",
            f"  Treaties resolved: {sum(1 for t in self.treaties if t.success)}"
            f"/{len(self.treaties)}",
            f"  Challenges: {len(self.challenges)}",
            f"  Claims: {self.total_claims}",
        ]
        if self.trust_summary:
            lines.append("  Trust distribution:")
            for level, count in sorted(self.trust_summary.items()):
                lines.append(f"    {level}: {count}")
        lines.append(f"  Phase: {self.final_phase.value}")
        lines.append(f"  Lyapunov V: {self.final_lyapunov:.4f}")
        if self.bundle_diagnostics:
            bd = self.bundle_diagnostics
            lines.append("  Bundle Geometry:")
            c1 = bd.get("first_chern_class", {})
            if c1:
                lines.append(f"    c₁ = {c1.get('value', 0):+.3f}")
            lines.append(
                f"    Flat: {'Yes' if bd.get('bundle_is_flat', True) else 'No'}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Protocols (structural typing)
# ---------------------------------------------------------------------------

@runtime_checkable
class ClaimExtractor(Protocol):
    """Protocol for extracting factual claims from text."""

    def extract(self, text: str, agent_id: str = "") -> list[FactualClaim]: ...


@runtime_checkable
class ContradictionDetector(Protocol):
    """Protocol for detecting contradictions between claims."""

    def detect(
        self, claims_a: list[FactualClaim], claims_b: list[FactualClaim]
    ) -> list[Contradiction]: ...


@runtime_checkable
class EvidenceProvider(Protocol):
    """Protocol for an evidence channel that can verify a claim."""

    def verify(self, claim: FactualClaim) -> TrustLevel: ...

    @property
    def trust_ceiling(self) -> TrustLevel: ...

    @property
    def channel(self) -> EvidenceChannel: ...


@runtime_checkable
class AgentFrameworkAdapter(Protocol):
    """Protocol for adapting a specific agent framework."""

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any]
    ) -> AgentOutput: ...

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]: ...


# ---------------------------------------------------------------------------
# 8. Trust algebra operations (module-level functions)
# ---------------------------------------------------------------------------

def conservative_join(*levels: TrustLevel) -> TrustLevel:
    """Conservative join: combined trust is the *weakest* input.

    This is the core JuGeo invariant — you cannot launder hallucinated
    facts through a pipeline that also contains verified facts.
    """
    if not levels:
        return TrustLevel.UNGROUNDED_CLAIM
    return TrustLevel(min(levels))


def trust_compose(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Compose two trust levels: result is the weaker of the two."""
    return conservative_join(a, b)


def can_promote(current: TrustLevel, target: TrustLevel, evidence: str) -> bool:
    """Check whether promotion from *current* to *target* is admissible.

    No-silent-promotion law: promotion requires explicit evidence string.
    """
    if not evidence:
        return False
    return target > current


# ---------------------------------------------------------------------------
# 9. Channel trust ceiling map
# ---------------------------------------------------------------------------

CHANNEL_TRUST_CEILINGS: dict[EvidenceChannel, TrustLevel] = {
    EvidenceChannel.FORMAL_PROOF: TrustLevel.FORMALLY_PROVEN,
    EvidenceChannel.HUMAN_REVIEW: TrustLevel.HUMAN_VERIFIED,
    EvidenceChannel.CODE_EXECUTION: TrustLevel.TOOL_VERIFIED,
    EvidenceChannel.SQL_QUERY: TrustLevel.TOOL_VERIFIED,
    EvidenceChannel.API_CALL: TrustLevel.TOOL_EXECUTED,
    EvidenceChannel.WEB_SEARCH: TrustLevel.RAG_GROUNDED,
    EvidenceChannel.RAG_RETRIEVAL: TrustLevel.RAG_GROUNDED,
    EvidenceChannel.LLM_VERIFICATION: TrustLevel.CROSS_AGENT_CONFIRMED,
    EvidenceChannel.LLM_GENERATION: TrustLevel.WEAK_MODEL_GENERATED,
}
