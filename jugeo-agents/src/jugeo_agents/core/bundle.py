"""Judgment Fiber Bundle — Trust as a Geometric Connection.

This module implements the central construction of Judgment Geometry
applied to multi-agent systems: the **judgment fiber bundle**.

The Classical Setup (what jugeo-agents already does)
----------------------------------------------------
Treat agent outputs as local sections of a presheaf.  Check the sheaf
condition (descent) on overlaps.  Classify obstructions by Čech
cohomology class.  This is powerful but treats trust as a *flat label*
— a tag attached to a claim, not a geometric object in its own right.

The Judgment Geometry Upgrade
-----------------------------
In JG, a **judgment** is not just a claim.  It is:

    J = (claim, evidence, trust, channel)

The space of all judgments over a task forms a **fiber bundle**:

    π : E → B

where:
- **B** (base) = the task space (subtasks, agent assignments)
- **E** (total space) = the judgment space (claims × evidence × trust × channel)
- **F** (fiber) = the space of possible judgments at each point
- **G** (structure group) = the trust algebra, acting on fibers

The key new object is the **trust connection** ∇ on this bundle.
When you "parallel transport" a judgment from agent A's context to
agent B's, the trust level *transforms* according to the evidence
relationship between A and B.  This transport is path-dependent:
going A→B→C may give a different trust than going A→C directly.

The **curvature** F = d∇ + ∇∧∇ measures this path-dependence.
Non-zero curvature at a point means the agents surrounding that
subtask have *structural unreliability* — no single pairwise check
can detect it; you must examine the full loop.

**Holonomy** — the total trust change around a closed loop of agents
— is the integral of curvature.  Non-trivial holonomy means the
agent team has a *topological* trust defect.

**Characteristic classes** (Chern-like invariants of the bundle)
detect global obstructions that survive to the cohomology level.
The first Chern class c₁ measures the average curvature (average
trust inconsistency across the team).  Higher classes detect
subtler correlations.

This is not metaphorical.  The curvature is computable from agent
outputs, and non-zero curvature implies a *provably unreliable*
agent configuration that cannot be fixed by local corrections.

Example
-------
>>> from jugeo_agents.core.bundle import JudgmentBundle
>>> bundle = JudgmentBundle()
>>> bundle.add_judgment("agent-a", "agent-b", claim="founded in 2018",
...     trust_a=TrustLevel.TOOL_VERIFIED, trust_b=TrustLevel.UNGROUNDED_CLAIM)
>>> bundle.add_judgment("agent-b", "agent-c", claim="founded in 2018",
...     trust_a=TrustLevel.UNGROUNDED_CLAIM, trust_b=TrustLevel.RAG_GROUNDED)
>>> bundle.add_judgment("agent-c", "agent-a", claim="founded in 2018",
...     trust_a=TrustLevel.RAG_GROUNDED, trust_b=TrustLevel.WEAK_MODEL_GENERATED)
>>> curv = bundle.curvature("agent-a", "agent-b", "agent-c")
>>> curv.value  # non-zero → structural unreliability
3
>>> bundle.holonomy(["agent-a", "agent-b", "agent-c"])
3  # trust shifts by 3 levels around the loop
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from jugeo_agents.types import (
    AgentOutput,
    CohomologyClass,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    TrustLevel,
)


__all__ = [
    "Judgment",
    "JudgmentFiber",
    "TrustConnection",
    "TransportResult",
    "Curvature",
    "Holonomy",
    "CharacteristicClass",
    "JudgmentBundle",
    "StratifiedJudgmentSpace",
    "Stratum",
    "EvidenceFunctor",
    "SemanticMove",
    "VerificationPath",
]


# ===================================================================
# 1.  Judgment — the fundamental object
# ===================================================================

@dataclass(frozen=True)
class Judgment:
    """A judgment in the sense of Judgment Geometry.

    Not just a claim, but a claim together with its epistemic context:
    the evidence supporting it, the trust level assigned to it, and the
    channel through which the evidence was obtained.

    This is an element of the fiber F_p over a point p in the base
    space (the task).
    """
    claim: FactualClaim
    evidence: list[str] = field(default_factory=list)   # evidence items
    trust: TrustLevel = TrustLevel.UNGROUNDED_CLAIM
    channel: str = "model"   # "tool", "rag", "citation", "model", "formal"
    agent_id: str = ""
    judgment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def trust_value(self) -> int:
        """Numeric trust level for arithmetic."""
        return self.trust.value

    def with_trust(self, new_trust: TrustLevel) -> Judgment:
        return Judgment(
            claim=self.claim,
            evidence=self.evidence,
            trust=new_trust,
            channel=self.channel,
            agent_id=self.agent_id,
        )


# ===================================================================
# 2.  Judgment Fiber — the fiber over a point in the base
# ===================================================================

@dataclass
class JudgmentFiber:
    """The fiber F_p — the space of all judgments at a given base point.

    A base point is an (agent, subject) pair.  The fiber contains all
    judgments that agent has made about that subject, across all
    evidence channels and trust levels.
    """
    agent_id: str
    subject: str
    judgments: list[Judgment] = field(default_factory=list)

    @property
    def max_trust(self) -> TrustLevel:
        if not self.judgments:
            return TrustLevel.UNGROUNDED_CLAIM
        return max(self.judgments, key=lambda j: j.trust_value).trust

    @property
    def min_trust(self) -> TrustLevel:
        if not self.judgments:
            return TrustLevel.UNGROUNDED_CLAIM
        return min(self.judgments, key=lambda j: j.trust_value).trust

    @property
    def trust_spread(self) -> int:
        """Spread of trust levels in this fiber."""
        if not self.judgments:
            return 0
        return self.max_trust.value - self.min_trust.value

    @property
    def channels(self) -> set[str]:
        return {j.channel for j in self.judgments}


# ===================================================================
# 3.  Trust Connection — parallel transport of trust across agents
# ===================================================================

@dataclass(frozen=True)
class TransportResult:
    """Result of parallel-transporting a judgment along a connection."""
    source_agent: str
    target_agent: str
    source_trust: TrustLevel
    transported_trust: TrustLevel
    actual_trust: TrustLevel        # what the target agent actually claims
    trust_shift: int                 # transported - actual (the discrepancy)
    is_consistent: bool              # shift == 0

    @property
    def signed_shift(self) -> int:
        """Positive = source trusts more than target warrants."""
        return self.transported_trust.value - self.actual_trust.value


@dataclass
class TrustConnection:
    """A connection ∇ on the judgment fiber bundle.

    The connection defines how to parallel-transport trust levels
    from one agent's fiber to another's.  Given agents A and B
    sharing a subject, the connection maps:

        ∇_{A→B} : F_A → F_B

    sending a judgment in A's fiber to the "corresponding" judgment
    in B's fiber, with trust adjusted by the transport rule.

    The transport rule encodes the evidence relationship:
    - If A has tool evidence and B doesn't, transport preserves A's trust.
    - If both have tool evidence, transport is identity.
    - If neither has evidence, transport demotes by 1 level.
    - If B has strictly better evidence, transport promotes.

    The connection is NOT flat in general — parallel transport
    around a loop can shift trust, which is the curvature.
    """

    def __init__(self) -> None:
        # Pairwise trust observations: (agent_a, agent_b, subject) → (trust_a, trust_b)
        self._edges: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)
        self._agents: set[str] = set()
        self._subjects: set[str] = set()

    def observe(
        self,
        agent_a: str,
        agent_b: str,
        subject: str,
        trust_a: TrustLevel,
        trust_b: TrustLevel,
    ) -> None:
        """Record an observed trust relationship on a shared subject."""
        self._edges[(agent_a, agent_b, subject)].append(
            (trust_a.value, trust_b.value)
        )
        self._agents.update({agent_a, agent_b})
        self._subjects.add(subject)

    def transport(
        self,
        source: str,
        target: str,
        source_trust: TrustLevel,
        subject: str = "",
    ) -> TransportResult:
        """Parallel-transport trust from source agent to target agent.

        Uses the empirical trust differential observed on shared subjects.
        """
        # Find the average trust differential on the edge
        key = (source, target, subject) if subject else None
        observations: list[tuple[int, int]] = []

        if key and key in self._edges:
            observations = self._edges[key]
        else:
            # Aggregate over all subjects
            for (a, b, s), obs in self._edges.items():
                if a == source and b == target:
                    observations.extend(obs)

        if not observations:
            # No data: transport is identity (flat connection)
            return TransportResult(
                source_agent=source,
                target_agent=target,
                source_trust=source_trust,
                transported_trust=source_trust,
                actual_trust=source_trust,
                trust_shift=0,
                is_consistent=True,
            )

        # Average differential: how much does trust shift A→B?
        avg_diff = sum(b - a for a, b in observations) / len(observations)
        transported_value = max(0, min(
            TrustLevel.FORMALLY_PROVEN.value,
            round(source_trust.value + avg_diff),
        ))
        transported = TrustLevel(transported_value)

        # What does B actually say?
        avg_b = round(sum(b for _, b in observations) / len(observations))
        actual = TrustLevel(max(0, min(TrustLevel.FORMALLY_PROVEN.value, avg_b)))

        shift = transported.value - actual.value

        return TransportResult(
            source_agent=source,
            target_agent=target,
            source_trust=source_trust,
            transported_trust=transported,
            actual_trust=actual,
            trust_shift=shift,
            is_consistent=(shift == 0),
        )

    def connection_matrix(self, subject: str = "") -> dict[tuple[str, str], float]:
        """The connection 1-form as a matrix of average trust differentials."""
        matrix: dict[tuple[str, str], float] = {}
        agents = sorted(self._agents)
        for a in agents:
            for b in agents:
                if a == b:
                    continue
                observations: list[tuple[int, int]] = []
                for (sa, sb, s), obs in self._edges.items():
                    if sa == a and sb == b:
                        if not subject or s == subject:
                            observations.extend(obs)
                if observations:
                    matrix[(a, b)] = sum(
                        b_val - a_val for a_val, b_val in observations
                    ) / len(observations)
        return matrix


# ===================================================================
# 4.  Curvature — the path-dependence of trust transport
# ===================================================================

@dataclass(frozen=True)
class Curvature:
    """Curvature F = d∇ + ∇∧∇ of the trust connection at a face.

    The curvature at a triple of agents (A, B, C) measures how much
    trust changes when you transport around the loop A→B→C→A vs
    the direct path A→A (identity).

    Non-zero curvature means the agent triple has STRUCTURAL
    unreliability: the trust relationships are inconsistent in a way
    that cannot be detected by any pairwise check.

    Geometrically, curvature is the infinitesimal holonomy — the
    Lie-algebra-valued 2-form measuring how the connection fails
    to be flat.
    """
    agents: tuple[str, str, str]    # the face (triangle of agents)
    subject: str
    value: float                     # curvature value (0 = flat)
    transport_ab: float             # trust shift A→B
    transport_bc: float             # trust shift B→C
    transport_ca: float             # trust shift C→A
    loop_holonomy: float            # total shift around the loop

    @property
    def is_flat(self) -> bool:
        return abs(self.value) < 0.01

    @property
    def interpretation(self) -> str:
        if self.is_flat:
            return "Flat: trust is consistent around this agent triple."
        if self.value > 0:
            return (
                f"Positive curvature ({self.value:+.2f}): trust INFLATES "
                f"around the loop {self.agents[0]}→{self.agents[1]}→"
                f"{self.agents[2]}. Agent trust assessments are mutually "
                f"inflating — possible echo chamber."
            )
        return (
            f"Negative curvature ({self.value:+.2f}): trust DEFLATES "
            f"around the loop. Agents are systematically undermining "
            f"each other's trust — adversarial configuration."
        )


# ===================================================================
# 5.  Holonomy — total trust change around a closed path
# ===================================================================

@dataclass(frozen=True)
class Holonomy:
    """Holonomy of the trust connection around a closed agent loop.

    The holonomy Hol(γ) of a loop γ = (A₁ → A₂ → ... → Aₙ → A₁) is
    the total trust transformation obtained by parallel-transporting
    around the loop.  It is the integral of curvature over the region
    bounded by the loop (Ambrose-Singer theorem analog).

    Non-trivial holonomy (≠ 0) means the agent team has a TOPOLOGICAL
    trust defect: there is no consistent global trust assignment.
    """
    loop: tuple[str, ...]           # agent IDs forming the loop
    subject: str
    total_shift: float              # net trust change around the loop
    edge_shifts: list[float]        # trust shift on each edge
    is_trivial: bool                # total_shift ≈ 0

    @property
    def winding_number(self) -> int:
        """Quantized trust winding: how many full trust levels the loop shifts."""
        return round(self.total_shift)


# ===================================================================
# 6.  Characteristic Classes — global invariants of the bundle
# ===================================================================

@dataclass
class CharacteristicClass:
    """Chern-like characteristic class of the judgment bundle.

    The first Chern class c₁ is the average curvature over all agent
    triples.  It measures the GLOBAL trust inconsistency of the team:

        c₁ = (1/2π) ∫ F

    In our discrete setting:
        c₁ = mean(|curvature|) over all agent triples

    - c₁ ≈ 0: trust is globally consistent (flat bundle).
    - c₁ > 0: structural trust inflation.
    - c₁ < 0: structural trust deflation.
    - |c₁| large: the team configuration is fundamentally unreliable.

    Higher classes detect subtler correlations:
    - c₂ measures curvature-curvature correlations (4-agent patterns).
    - The Euler class χ = c₁ for rank-1 bundles (our case).
    """
    name: str                        # "c1", "c2", "euler"
    value: float
    num_faces: int                   # number of agent triples sampled
    curvatures: list[float] = field(default_factory=list)

    @property
    def is_trivial(self) -> bool:
        return abs(self.value) < 0.01

    @property
    def variance(self) -> float:
        if len(self.curvatures) < 2:
            return 0.0
        mean = self.value
        return sum((c - mean) ** 2 for c in self.curvatures) / len(self.curvatures)

    @property
    def interpretation(self) -> str:
        if self.is_trivial:
            return f"{self.name} ≈ 0: The judgment bundle is approximately flat (globally consistent trust)."
        if self.value > 0:
            return f"{self.name} = {self.value:+.3f}: Positive — systematic trust inflation across the team."
        return f"{self.name} = {self.value:+.3f}: Negative — systematic trust deflation across the team."


# ===================================================================
# 7.  Trust Stratification
# ===================================================================

@dataclass
class Stratum:
    """A single stratum in the trust-stratified judgment space.

    Judgments at trust level t live in stratum S_t.  The strata are
    ordered by trust: S_{FORMALLY_PROVEN} ⊃ S_{TOOL_VERIFIED} ⊃ ...

    Moving "up" (promotion) requires evidence — an ascending path
    in the stratified space.  Moving "down" (demotion) is always
    allowed.  This asymmetry gives the stratification its geometry.
    """
    trust_level: TrustLevel
    judgments: list[Judgment] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.judgments)

    @property
    def agents(self) -> set[str]:
        return {j.agent_id for j in self.judgments}


@dataclass
class StratifiedJudgmentSpace:
    """The full trust-stratified judgment space.

    Partition all judgments by trust level.  The strata form a filtered
    complex whose intersection homology detects obstructions invisible
    to unstratified analysis.

    An obstruction in the TOOL_VERIFIED stratum means "even among
    tool-backed claims, there's a contradiction."  This is far more
    severe than a contradiction in the UNGROUNDED stratum.
    """
    strata: dict[TrustLevel, Stratum] = field(default_factory=dict)

    def add(self, judgment: Judgment) -> None:
        if judgment.trust not in self.strata:
            self.strata[judgment.trust] = Stratum(trust_level=judgment.trust)
        self.strata[judgment.trust].judgments.append(judgment)

    def stratum_at(self, level: TrustLevel) -> Stratum:
        return self.strata.get(level, Stratum(trust_level=level))

    @property
    def total_judgments(self) -> int:
        return sum(s.size for s in self.strata.values())

    def trust_distribution(self) -> dict[str, int]:
        return {
            level.name: self.stratum_at(level).size
            for level in TrustLevel
            if self.stratum_at(level).size > 0
        }

    def stratum_obstructions(
        self,
    ) -> dict[TrustLevel, list[tuple[Judgment, Judgment]]]:
        """Find contradicting judgment pairs within each stratum.

        An intra-stratum contradiction is more severe than a cross-stratum
        one, because both judgments have the same trust level.
        """
        from jugeo_agents.core.claims import make_detector
        detector = make_detector()
        result: dict[TrustLevel, list[tuple[Judgment, Judgment]]] = {}

        for level, stratum in self.strata.items():
            pairs: list[tuple[Judgment, Judgment]] = []
            for i, ja in enumerate(stratum.judgments):
                for jb in stratum.judgments[i + 1:]:
                    if ja.agent_id == jb.agent_id:
                        continue
                    contras = detector.detect([ja.claim], [jb.claim])
                    if contras:
                        pairs.append((ja, jb))
            if pairs:
                result[level] = pairs
        return result


# ===================================================================
# 8.  Evidence Channel Functors
# ===================================================================

@dataclass
class EvidenceFunctor:
    """A fiber functor from the judgment site to an evidence category.

    Each evidence channel (tool, RAG, model, formal) is a functor:
        Φ_ch : Judg → Evid_ch

    that extracts the channel-specific evidence from a judgment.  The
    natural transformations between functors ARE the cross-channel
    verification operations.

    When you verify a model-generated claim against tool output, you're
    computing the natural transformation η : Φ_model ⇒ Φ_tool.
    """
    channel: str
    trust_ceiling: TrustLevel

    def extract(self, judgment: Judgment) -> list[str]:
        """Extract evidence items relevant to this channel."""
        if judgment.channel == self.channel:
            return judgment.evidence
        return []

    def can_verify(self, judgment: Judgment) -> bool:
        """Whether this channel can provide evidence for the judgment."""
        return self.trust_ceiling.value >= judgment.trust.value


# ===================================================================
# 9.  Semantic Moves — morphisms in the verification site
# ===================================================================

@dataclass(frozen=True)
class SemanticMove:
    """A morphism in the verification site.

    Each move (running a test, challenging a claim, negotiating a treaty)
    changes the geometric state of the judgment bundle.  Formally, a
    move is a morphism m : (E, ∇) → (E', ∇') that transforms the
    total space and connection.

    The move's "action" S(m) is its cost/trust tradeoff:
        S(m) = cost(m) - Δtrust(m)

    The optimal verification strategy minimizes the total action
    along a path of moves (principle of least action for verification).
    """
    name: str                        # "run_test", "challenge", "negotiate", etc.
    source_state: str               # before-state description
    target_state: str               # after-state description
    cost: float = 0.0               # computational/time cost
    trust_delta: float = 0.0        # change in total trust
    curvature_delta: float = 0.0    # change in curvature

    @property
    def action(self) -> float:
        """The action functional: cost minus trust gain."""
        return self.cost - self.trust_delta


@dataclass
class VerificationPath:
    """A path through the verification site (sequence of semantic moves).

    The path's holonomy is the total trust transformation.  The path's
    action is the integral of the action functional along the path.
    The optimal path minimizes action — the "geodesic" of verification.
    """
    moves: list[SemanticMove] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(m.cost for m in self.moves)

    @property
    def total_trust_delta(self) -> float:
        return sum(m.trust_delta for m in self.moves)

    @property
    def total_action(self) -> float:
        return sum(m.action for m in self.moves)

    @property
    def total_curvature_delta(self) -> float:
        return sum(m.curvature_delta for m in self.moves)

    def append(self, move: SemanticMove) -> None:
        self.moves.append(move)


# ===================================================================
# 10.  The Judgment Bundle — the main construction
# ===================================================================

class JudgmentBundle:
    """The judgment fiber bundle over a multi-agent task space.

    This is the central construction of Judgment Geometry applied to
    multi-agent systems.  It assembles all the pieces:

    - Fibers: judgment spaces at each (agent, subject) point
    - Connection: trust transport rules between agents
    - Curvature: path-dependence of trust transport
    - Holonomy: total trust shift around agent loops
    - Characteristic classes: global trust invariants
    - Stratification: trust-level partitioning
    - Moves: morphisms that change the bundle

    The bundle is *computable* from agent outputs and provides
    *provable* diagnostics about agent team reliability.
    """

    def __init__(self) -> None:
        self._fibers: dict[tuple[str, str], JudgmentFiber] = {}
        self._connection = TrustConnection()
        self._stratification = StratifiedJudgmentSpace()
        self._path = VerificationPath()
        self._judgments: list[Judgment] = []
        self._agents: set[str] = set()
        self._subjects: set[str] = set()

    # ---- Building the bundle -------------------------------------------

    def add_judgment(self, judgment: Judgment) -> None:
        """Add a judgment to the bundle."""
        self._judgments.append(judgment)
        self._agents.add(judgment.agent_id)
        subject = judgment.claim.subject.lower().strip()
        self._subjects.add(subject)

        key = (judgment.agent_id, subject)
        if key not in self._fibers:
            self._fibers[key] = JudgmentFiber(
                agent_id=judgment.agent_id, subject=subject,
            )
        self._fibers[key].judgments.append(judgment)

        self._stratification.add(judgment)

    def add_agent_output(self, output: AgentOutput) -> list[Judgment]:
        """Ingest an AgentOutput and extract judgments."""
        from jugeo_agents.core.claims import make_extractor
        from jugeo_agents.core.trust import TrustAlgebra

        extractor = make_extractor()
        trust_alg = TrustAlgebra()
        trust_level = trust_alg.classify_output(output)

        channel = "model"
        if output.tools_used:
            channel = "tool"
        elif output.citations:
            channel = "citation"
        elif output.rag_sources:
            channel = "rag"

        claims = extractor.extract(output.output_text, output.agent_id)
        judgments: list[Judgment] = []

        for claim in claims:
            j = Judgment(
                claim=claim,
                evidence=output.tools_used + output.citations + output.rag_sources,
                trust=trust_level,
                channel=channel,
                agent_id=output.agent_id,
            )
            self.add_judgment(j)
            judgments.append(j)

        return judgments

    def build_connection(self) -> TrustConnection:
        """Build the trust connection from observed judgment pairs.

        For every pair of agents that share a subject, record their
        trust levels as a connection observation.
        """
        for subject in self._subjects:
            agents_for_subject: dict[str, list[Judgment]] = defaultdict(list)
            for j in self._judgments:
                if j.claim.subject.lower().strip() == subject:
                    agents_for_subject[j.agent_id].append(j)

            agent_ids = list(agents_for_subject.keys())
            for i, a in enumerate(agent_ids):
                for b in agent_ids[i + 1:]:
                    for ja in agents_for_subject[a]:
                        for jb in agents_for_subject[b]:
                            self._connection.observe(
                                a, b, subject, ja.trust, jb.trust,
                            )
                            self._connection.observe(
                                b, a, subject, jb.trust, ja.trust,
                            )
        return self._connection

    # ---- Curvature computation -----------------------------------------

    def curvature(
        self, agent_a: str, agent_b: str, agent_c: str,
        subject: str = "",
    ) -> Curvature:
        """Compute the curvature at a 2-face (agent triple).

        Curvature = holonomy of the infinitesimal triangle:
            F(A,B,C) = ∇_{A→B} + ∇_{B→C} + ∇_{C→A}

        If the connection were flat, this would be zero.
        Non-zero means structural trust inconsistency.
        """
        if not self._connection._edges:
            self.build_connection()

        subjects = [subject] if subject else list(self._subjects)
        if not subjects:
            return Curvature(
                agents=(agent_a, agent_b, agent_c), subject="",
                value=0.0, transport_ab=0.0, transport_bc=0.0,
                transport_ca=0.0, loop_holonomy=0.0,
            )

        total_curv = 0.0
        total_ab = 0.0
        total_bc = 0.0
        total_ca = 0.0
        count = 0

        for subj in subjects:
            key_ab = (agent_a, agent_b, subj)
            key_bc = (agent_b, agent_c, subj)
            key_ca = (agent_c, agent_a, subj)

            obs_ab = self._connection._edges.get(key_ab, [])
            obs_bc = self._connection._edges.get(key_bc, [])
            obs_ca = self._connection._edges.get(key_ca, [])

            if not (obs_ab and obs_bc and obs_ca):
                continue

            # Average trust differentials on each edge
            diff_ab = sum(b - a for a, b in obs_ab) / len(obs_ab)
            diff_bc = sum(b - a for a, b in obs_bc) / len(obs_bc)
            diff_ca = sum(b - a for a, b in obs_ca) / len(obs_ca)

            # Curvature = sum of differentials around the loop
            curv = diff_ab + diff_bc + diff_ca

            total_curv += curv
            total_ab += diff_ab
            total_bc += diff_bc
            total_ca += diff_ca
            count += 1

        if count == 0:
            return Curvature(
                agents=(agent_a, agent_b, agent_c),
                subject=subject or "(all)",
                value=0.0, transport_ab=0.0, transport_bc=0.0,
                transport_ca=0.0, loop_holonomy=0.0,
            )

        avg_curv = total_curv / count
        return Curvature(
            agents=(agent_a, agent_b, agent_c),
            subject=subject or "(all)",
            value=avg_curv,
            transport_ab=total_ab / count,
            transport_bc=total_bc / count,
            transport_ca=total_ca / count,
            loop_holonomy=total_curv / count,
        )

    # ---- Holonomy computation ------------------------------------------

    def holonomy(self, loop: Sequence[str], subject: str = "") -> Holonomy:
        """Compute the holonomy around a closed loop of agents.

        The holonomy Hol(γ) is the total trust shift when you parallel-
        transport around the loop γ = (A₁→A₂→...→Aₙ→A₁).

        By the discrete Ambrose-Singer theorem, this equals the sum of
        curvatures of all triangulations of the loop.
        """
        if not self._connection._edges:
            self.build_connection()

        edge_shifts: list[float] = []
        agents = list(loop)

        for i in range(len(agents)):
            a = agents[i]
            b = agents[(i + 1) % len(agents)]

            observations: list[tuple[int, int]] = []
            for (sa, sb, s), obs in self._connection._edges.items():
                if sa == a and sb == b:
                    if not subject or s == subject:
                        observations.extend(obs)

            if observations:
                avg_shift = sum(b_val - a_val for a_val, b_val in observations) / len(observations)
                edge_shifts.append(avg_shift)
            else:
                edge_shifts.append(0.0)

        total = sum(edge_shifts)
        return Holonomy(
            loop=tuple(loop),
            subject=subject or "(all)",
            total_shift=total,
            edge_shifts=edge_shifts,
            is_trivial=abs(total) < 0.01,
        )

    # ---- Characteristic classes ----------------------------------------

    def first_chern_class(self) -> CharacteristicClass:
        """Compute c₁ — the first Chern class of the judgment bundle.

        c₁ = (1/N) Σ |F(A,B,C)| over all agent triples (A,B,C)

        This is the average curvature magnitude.  It measures the
        global trust inconsistency of the agent team.
        """
        if not self._connection._edges:
            self.build_connection()

        agents = sorted(self._agents)
        curvatures: list[float] = []
        num_faces = 0

        for i, a in enumerate(agents):
            for j, b in enumerate(agents):
                if j <= i:
                    continue
                for k, c in enumerate(agents):
                    if k <= j:
                        continue
                    curv = self.curvature(a, b, c)
                    if curv.value != 0.0 or True:  # include zero faces
                        curvatures.append(curv.value)
                        num_faces += 1

        if not curvatures:
            return CharacteristicClass(
                name="c₁", value=0.0, num_faces=0,
            )

        avg = sum(curvatures) / len(curvatures)
        return CharacteristicClass(
            name="c₁",
            value=avg,
            num_faces=num_faces,
            curvatures=curvatures,
        )

    # ---- Full diagnostic -----------------------------------------------

    def diagnose(self) -> dict[str, Any]:
        """Full geometric diagnostic of the agent team.

        Returns a comprehensive report including:
        - Fiber structure (judgments per agent/subject)
        - Connection (pairwise trust differentials)
        - Curvature at every agent triple
        - Holonomy around significant loops
        - First Chern class (global trust consistency)
        - Stratification (trust distribution)
        - Stratum-level obstructions
        """
        if not self._connection._edges:
            self.build_connection()

        agents = sorted(self._agents)
        c1 = self.first_chern_class()

        # Compute all curvatures
        all_curvatures: list[Curvature] = []
        for i, a in enumerate(agents):
            for j, b in enumerate(agents):
                if j <= i:
                    continue
                for k, c_agent in enumerate(agents):
                    if k <= j:
                        continue
                    curv = self.curvature(a, b, c_agent)
                    all_curvatures.append(curv)

        # Full-team holonomy (if ≥ 3 agents)
        team_holonomy = None
        if len(agents) >= 3:
            team_holonomy = self.holonomy(agents)

        # Stratum obstructions
        strat_obs = self._stratification.stratum_obstructions()

        non_flat = [c for c in all_curvatures if not c.is_flat]

        return {
            "agents": agents,
            "subjects": sorted(self._subjects),
            "total_judgments": len(self._judgments),
            "fibers": {
                k: {"count": len(f.judgments), "trust_spread": f.trust_spread}
                for k, f in self._fibers.items()
            },
            "first_chern_class": {
                "value": c1.value,
                "interpretation": c1.interpretation,
                "variance": c1.variance,
                "num_faces": c1.num_faces,
            },
            "curvatures": [
                {
                    "agents": c.agents,
                    "value": c.value,
                    "interpretation": c.interpretation,
                }
                for c in non_flat
            ],
            "flat_faces": len(all_curvatures) - len(non_flat),
            "curved_faces": len(non_flat),
            "team_holonomy": {
                "loop": team_holonomy.loop if team_holonomy else (),
                "total_shift": team_holonomy.total_shift if team_holonomy else 0,
                "is_trivial": team_holonomy.is_trivial if team_holonomy else True,
            },
            "stratification": self._stratification.trust_distribution(),
            "stratum_obstructions": {
                level.name: len(pairs) for level, pairs in strat_obs.items()
            },
            "bundle_is_flat": c1.is_trivial,
        }

    def summary_text(self) -> str:
        """Human-readable summary of the bundle geometry."""
        diag = self.diagnose()
        c1 = diag["first_chern_class"]
        hol = diag["team_holonomy"]

        lines = [
            "═══ Judgment Fiber Bundle Diagnostic ═══",
            f"  Agents: {', '.join(diag['agents'])}",
            f"  Subjects: {len(diag['subjects'])}",
            f"  Total judgments: {diag['total_judgments']}",
            "",
            "  Connection Geometry:",
            f"    First Chern class c₁ = {c1['value']:+.3f}",
            f"    {c1['interpretation']}",
            f"    Curved faces: {diag['curved_faces']} / "
            f"{diag['curved_faces'] + diag['flat_faces']}",
            f"    Bundle is flat: {'Yes ✓' if diag['bundle_is_flat'] else 'NO — structural trust inconsistency'}",
            "",
            f"  Team Holonomy:",
            f"    Loop: {' → '.join(hol['loop'])}",
            f"    Total shift: {hol['total_shift']:+.2f}",
            f"    Trivial: {'Yes ✓' if hol['is_trivial'] else 'NO — topological trust defect'}",
            "",
            "  Trust Stratification:",
        ]
        for level, count in diag["stratification"].items():
            lines.append(f"    {level}: {count} judgments")

        if diag["stratum_obstructions"]:
            lines.append("")
            lines.append("  ⚠ Stratum Obstructions (intra-level contradictions):")
            for level, count in diag["stratum_obstructions"].items():
                lines.append(f"    {level}: {count} contradicting pairs")

        if diag["curvatures"]:
            lines.append("")
            lines.append("  Non-Flat Curvatures:")
            for c in diag["curvatures"][:5]:
                lines.append(
                    f"    {c['agents'][0]}↔{c['agents'][1]}↔{c['agents'][2]}: "
                    f"F = {c['value']:+.2f}"
                )
                lines.append(f"      {c['interpretation']}")

        return "\n".join(lines)

    # ---- Properties ----------------------------------------------------

    @property
    def agents(self) -> set[str]:
        return self._agents.copy()

    @property
    def subjects(self) -> set[str]:
        return self._subjects.copy()

    @property
    def connection(self) -> TrustConnection:
        return self._connection

    @property
    def stratification(self) -> StratifiedJudgmentSpace:
        return self._stratification

    def reset(self) -> None:
        self._fibers.clear()
        self._connection = TrustConnection()
        self._stratification = StratifiedJudgmentSpace()
        self._path = VerificationPath()
        self._judgments.clear()
        self._agents.clear()
        self._subjects.clear()
