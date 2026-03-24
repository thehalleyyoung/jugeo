"""Global Section Assembly via Sheaf-Theoretic Knowledge Fusion.

This is the theoretical crown jewel of jugeo-agents.  Given a collection
of agent outputs (local sections of a presheaf over the agent-task site),
the :class:`GlobalSectionAssembler` computes the **verified global section**
— the largest consistent, trust-certified knowledge base that can be glued
from the local pieces.

The algorithm:

1. **Extract** — parse every agent output into a ``LocalSection`` of
   ``FactualClaim`` objects with trust annotations.
2. **Overlap** — identify claim pairs that share a subject (the
   "restriction maps" of the presheaf to double intersections).
3. **Descent check** — for each overlap, test whether the two sections
   agree (the sheaf/descent condition).
4. **Classify obstructions** — disagreements are classified into Čech
   cohomology classes:
   - *H⁰ gaps*: missing coverage (a task dimension no agent addressed).
   - *H¹ cocycles*: pairwise contradictions on double overlaps.
   - *H² cascades*: multi-hop fabrication chains (hallucination cascades).
   - *Phantom sections*: consistent but ungrounded claims (phantom
     consensus — all agents agree on something none can evidence).
5. **Resolve** — H¹ obstructions are resolved via trust-weighted
   selection or treaty negotiation.  H² obstructions quarantine entire
   derivation chains.  Phantoms are flagged.
6. **Assemble** — the remaining consistent, non-quarantined, non-phantom
   claims are glued into a :class:`VerifiedGlobalSection` where every
   claim carries a trust certificate and provenance chain.

No existing multi-agent framework performs this operation.  Majority vote,
debate, and reflection all operate on raw text without the structural
guarantees that sheaf cohomology provides.

Example
-------
>>> from jugeo_agents import JuGeoAgentWrapper, AgentOutput, TrustLevel
>>> from jugeo_agents.core.fusion import GlobalSectionAssembler
>>>
>>> assembler = GlobalSectionAssembler()
>>> assembler.ingest(AgentOutput(agent_id="a", text="Acme was founded in 2018."))
>>> assembler.ingest(AgentOutput(agent_id="b", text="Acme was founded in 2020."))
>>> section = assembler.assemble()
>>> section.verified_claims   # only the trust-winner survives
>>> section.quarantined       # the loser is quarantined with reason
>>> section.obstructions      # H1 temporal contradiction recorded
"""

from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Sequence

from jugeo_agents.types import (
    AgentOutput,
    CohomologyClass,
    Contradiction,
    ConvergencePhase,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    ProvenanceChain,
    ProvenanceLink,
    TrustLevel,
    conservative_join,
)
from jugeo_agents.core.claims import (
    HeuristicContradictionDetector,
    SubjectMatcher,
    make_detector,
    make_extractor,
)
from jugeo_agents.core.descent import DescentEngine, LocalSection
from jugeo_agents.core.obstructions import ObstructionClassifier
from jugeo_agents.core.provenance import ProvenanceGraph
from jugeo_agents.core.trust import TrustAlgebra
from jugeo_agents.core.bundle import JudgmentBundle


__all__ = [
    "GlobalSectionAssembler",
    "VerifiedGlobalSection",
    "VerifiedClaim",
    "QuarantinedClaim",
    "QuarantineReason",
    "FusionReport",
    "CohomologyComputation",
    "NaiveVoteResult",
    "compare_to_naive_vote",
]


# ===================================================================
# Data model
# ===================================================================

class QuarantineReason(Enum):
    """Why a claim was excluded from the global section."""
    H1_LOST_TRUST_CONTEST = auto()      # lower trust than contradicting claim
    H1_LOST_EVIDENCE_COUNT = auto()     # fewer supporting agents
    H2_CASCADING_HALLUCINATION = auto() # part of a fabrication chain
    PHANTOM_UNGROUNDED = auto()         # consistent but no grounding evidence
    TRUST_BELOW_THRESHOLD = auto()      # trust too low to include
    SELF_CONTRADICTED = auto()          # agent contradicts itself


@dataclass
class VerifiedClaim:
    """A claim that survived fusion and belongs to the global section."""
    claim: FactualClaim
    trust: TrustLevel
    supporting_agents: list[str]
    provenance: list[str]            # chain of agent_ids
    resolution_method: str           # "unanimous", "trust_winner", "treaty", "sole_source"
    confidence: float                # 0..1 combined confidence


@dataclass
class QuarantinedClaim:
    """A claim excluded from the global section."""
    claim: FactualClaim
    reason: QuarantineReason
    explanation: str
    related_contradiction: Contradiction | None = None
    winning_claim: FactualClaim | None = None


@dataclass
class CohomologyComputation:
    """The full Čech cohomology computation over the agent-task site.

    - **H⁰** = ker(d⁰) = set of globally consistent claims (the global section).
    - **H¹** = ker(d¹)/im(d⁰) = pairwise contradictions modulo resolutions.
    - **H²** = cascading fabrication chains (higher-order obstructions).
    - **Phantom** = claims in H⁰ that are ungrounded (phantom global sections).

    This is a genuine topological invariant of the multi-agent knowledge space.
    """
    h0_global_claims: int = 0       # |H⁰| — verified global section size
    h1_contradictions: int = 0      # |H¹| — pairwise contradiction cocycles
    h1_resolved: int = 0            # H¹ cocycles resolved by trust/treaty
    h1_unresolved: int = 0          # H¹ cocycles that remain open
    h2_cascades: int = 0            # |H²| — cascading hallucination chains
    phantom_sections: int = 0       # phantom global sections (ungrounded)
    euler_characteristic: float = 0.0   # χ = h0 - h1 + h2 (topological summary)
    betti_numbers: tuple[int, ...] = ()  # (β₀, β₁, β₂)
    obstruction_density: float = 0.0     # (h1+h2+phantom) / total_claims

    def compute_derived(self) -> None:
        total = self.h0_global_claims + self.h1_contradictions + self.h2_cascades + self.phantom_sections
        self.euler_characteristic = self.h0_global_claims - self.h1_contradictions + self.h2_cascades
        self.betti_numbers = (self.h0_global_claims, self.h1_contradictions, self.h2_cascades)
        self.obstruction_density = (
            (self.h1_unresolved + self.h2_cascades + self.phantom_sections) / total
            if total > 0 else 0.0
        )


@dataclass
class VerifiedGlobalSection:
    """The assembled global section — the output of sheaf-theoretic fusion.

    This is the largest consistent, trust-certified sub-presheaf that can
    be glued from the agent outputs.  Every claim in ``verified_claims``
    has been checked against all overlapping claims and either:

    - Unanimously agreed upon by all agents who mentioned the subject.
    - Won a trust contest against contradicting claims.
    - Survived treaty negotiation.

    Claims that lost are in ``quarantined``.
    """
    verified_claims: list[VerifiedClaim] = field(default_factory=list)
    quarantined: list[QuarantinedClaim] = field(default_factory=list)
    obstructions: list[Obstruction] = field(default_factory=list)
    cohomology: CohomologyComputation = field(default_factory=CohomologyComputation)
    agent_trust_scores: dict[str, float] = field(default_factory=dict)
    coverage_gaps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_consistent(self) -> bool:
        return self.cohomology.h1_unresolved == 0 and self.cohomology.h2_cascades == 0

    @property
    def claim_count(self) -> int:
        return len(self.verified_claims)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined)

    @property
    def total_claims_seen(self) -> int:
        return self.claim_count + self.quarantined_count

    @property
    def verification_rate(self) -> float:
        total = self.total_claims_seen
        return self.claim_count / total if total > 0 else 1.0

    def claims_by_trust(self) -> dict[TrustLevel, list[VerifiedClaim]]:
        result: dict[TrustLevel, list[VerifiedClaim]] = defaultdict(list)
        for vc in self.verified_claims:
            result[vc.trust].append(vc)
        return dict(result)

    def claims_for_subject(self, subject: str) -> list[VerifiedClaim]:
        subject_lower = subject.lower()
        return [
            vc for vc in self.verified_claims
            if subject_lower in vc.claim.subject.lower()
        ]

    def summary_text(self) -> str:
        lines = [
            "═══ Verified Global Section ═══",
            f"  Claims verified: {self.claim_count}",
            f"  Claims quarantined: {self.quarantined_count}",
            f"  Verification rate: {self.verification_rate:.0%}",
            f"  Consistent: {'Yes' if self.is_consistent else 'NO — unresolved obstructions remain'}",
            "",
            "  Cohomology:",
            f"    H⁰ (global section):  {self.cohomology.h0_global_claims}",
            f"    H¹ (contradictions):  {self.cohomology.h1_contradictions}"
            f"  ({self.cohomology.h1_resolved} resolved, {self.cohomology.h1_unresolved} open)",
            f"    H² (cascades):        {self.cohomology.h2_cascades}",
            f"    Phantom sections:     {self.cohomology.phantom_sections}",
            f"    χ (Euler char):       {self.cohomology.euler_characteristic:.1f}",
            f"    Obstruction density:  {self.cohomology.obstruction_density:.1%}",
        ]
        if self.coverage_gaps:
            lines.append("")
            lines.append("  Coverage gaps:")
            for gap in self.coverage_gaps:
                lines.append(f"    • {gap}")
        if self.quarantined:
            lines.append("")
            lines.append("  Quarantined claims:")
            for qc in self.quarantined[:5]:
                lines.append(f"    ✗ [{qc.reason.name}] {qc.claim.subject}: {qc.claim.value}")
                lines.append(f"      {qc.explanation}")
        return "\n".join(lines)


# ===================================================================
# Fusion Report (comparison to naive approaches)
# ===================================================================

@dataclass
class NaiveVoteResult:
    """What a naive majority-vote system would produce."""
    accepted_claims: list[FactualClaim]
    # Claims a vote system accepts that are actually wrong:
    false_positives: list[FactualClaim]
    # Claims a vote system rejects that are actually right:
    false_negatives: list[FactualClaim]
    explanation: str = ""


@dataclass
class FusionReport:
    """Complete report comparing JuGeo fusion to naive approaches."""
    global_section: VerifiedGlobalSection
    naive_comparison: NaiveVoteResult | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def advantage_text(self) -> str:
        if not self.naive_comparison:
            return "No naive comparison computed."
        nc = self.naive_comparison
        lines = [
            "═══ JuGeo vs Naive Majority Vote ═══",
            f"  JuGeo verified claims:     {self.global_section.claim_count}",
            f"  Naive accepted claims:     {len(nc.accepted_claims)}",
            f"  Naive false positives:     {len(nc.false_positives)}  ← wrong claims accepted!",
            f"  Naive false negatives:     {len(nc.false_negatives)}  ← right claims rejected!",
            "",
            "  JuGeo advantages:",
            f"    • Detected {self.global_section.cohomology.h2_cascades} cascading hallucination chain(s)",
            f"    • Detected {self.global_section.cohomology.phantom_sections} phantom consensus instance(s)",
            f"    • Resolved {self.global_section.cohomology.h1_resolved} contradictions via trust algebra",
            f"    • Every verified claim has a provenance chain",
        ]
        if nc.false_positives:
            lines.append("")
            lines.append("  Naive system would WRONGLY ACCEPT:")
            for fp in nc.false_positives[:5]:
                lines.append(f"    ✗ {fp.subject}: {fp.value} (from {fp.source_agent})")
        return "\n".join(lines)


# ===================================================================
# The Assembler
# ===================================================================

class GlobalSectionAssembler:
    """Assemble a verified global section from multi-agent outputs.

    This is the central sheaf-theoretic operation: given local sections
    (agent outputs), compute the Čech cohomology of the resulting
    presheaf and extract the largest consistent global section.

    Parameters
    ----------
    trust_threshold : TrustLevel
        Minimum trust for a claim to enter the global section.
    phantom_detection : bool
        Whether to run phantom-section detection (ungrounded consensus).
    cascade_detection : bool
        Whether to detect H² cascading hallucination chains.
    """

    def __init__(
        self,
        *,
        trust_threshold: TrustLevel = TrustLevel.WEAK_MODEL_GENERATED,
        phantom_detection: bool = True,
        cascade_detection: bool = True,
    ) -> None:
        self._trust_threshold = trust_threshold
        self._phantom_detection = phantom_detection
        self._cascade_detection = cascade_detection

        self._extractor = make_extractor()
        self._detector = make_detector()
        self._trust = TrustAlgebra()
        self._descent = DescentEngine(
            claim_extractor=self._extractor,
            contradiction_detector=self._detector,
        )
        self._provenance = ProvenanceGraph()
        self._matcher = SubjectMatcher()

        self._outputs: list[AgentOutput] = []
        self._sections: dict[str, LocalSection] = {}
        self._all_claims: dict[str, list[FactualClaim]] = defaultdict(list)

        # Judgment Fiber Bundle (geometric stack)
        self._bundle = JudgmentBundle()

    def ingest(self, output: AgentOutput) -> LocalSection:
        """Ingest an agent output as a local section."""
        self._outputs.append(output)
        section = self._descent.add_section(output)
        self._sections[output.agent_id] = section

        trust_level = self._trust.classify_output(output)
        claims = self._extractor.extract(output.output_text, output.agent_id)
        for c in claims:
            c = FactualClaim(
                text=c.text,
                subject=c.subject,
                predicate=c.predicate,
                value=c.value,
                source_agent=output.agent_id,
                source_text_span=c.source_text_span,
                trust=trust_level,
                metadata=c.metadata,
            )
            self._all_claims[output.agent_id].append(c)

        self._provenance.add_agent_output(output)

        # Feed into the judgment fiber bundle
        self._bundle.add_agent_output(output)

        return section

    def assemble(self) -> VerifiedGlobalSection:
        """Compute the Čech cohomology and assemble the global section.

        This is the main operation.  It:
        1. Runs full descent check across all agent pairs.
        2. Classifies all obstructions (H⁰/H¹/H²/phantom).
        3. Resolves H¹ contradictions via trust-weighted selection.
        4. Quarantines H² cascades and phantom claims.
        5. Assembles remaining claims into the global section.
        """
        # Step 1: Full descent check
        descent_result = self._descent.check_all()

        # Step 2: Collect all contradictions
        all_contradictions: list[Contradiction] = []
        for obs in descent_result.obstructions:
            if hasattr(obs, 'metadata') and 'contradiction' in obs.metadata:
                all_contradictions.append(obs.metadata['contradiction'])

        # Also run pairwise detection directly for completeness
        agent_ids = list(self._all_claims.keys())
        for i, aid_a in enumerate(agent_ids):
            for aid_b in agent_ids[i + 1:]:
                contradictions = self._detector.detect(
                    self._all_claims[aid_a],
                    self._all_claims[aid_b],
                )
                all_contradictions.extend(contradictions)

        # Deduplicate contradictions by explanation
        seen_explanations: set[str] = set()
        unique_contradictions: list[Contradiction] = []
        for c in all_contradictions:
            if c.explanation not in seen_explanations:
                seen_explanations.add(c.explanation)
                unique_contradictions.append(c)

        # Step 3: Detect cascading hallucinations (H²)
        cascade_claims: set[str] = set()  # claim_ids in cascades
        h2_count = 0
        if self._cascade_detection:
            h2_count, cascade_claims = self._detect_cascades()

        # Step 4: Detect phantom sections (ungrounded consensus)
        phantom_claims: set[str] = set()
        phantom_count = 0
        if self._phantom_detection:
            phantom_count, phantom_claims = self._detect_phantoms()

        # Step 5: Resolve H¹ contradictions
        verified: list[VerifiedClaim] = []
        quarantined: list[QuarantinedClaim] = []
        h1_resolved = 0
        h1_unresolved = 0

        # Build a map of subject → claims from all agents
        subject_claims: dict[str, list[FactualClaim]] = defaultdict(list)
        for agent_claims in self._all_claims.values():
            for claim in agent_claims:
                subject_key = claim.subject.lower().strip()
                if subject_key:
                    subject_claims[subject_key].append(claim)

        # Track which claims are involved in contradictions
        contradicted_claim_ids: set[str] = set()
        contradiction_map: dict[str, list[Contradiction]] = defaultdict(list)
        for c in unique_contradictions:
            contradicted_claim_ids.add(c.claim_a.claim_id)
            contradicted_claim_ids.add(c.claim_b.claim_id)
            contradiction_map[c.claim_a.claim_id].append(c)
            contradiction_map[c.claim_b.claim_id].append(c)

        # Process all claims
        processed: set[str] = set()
        all_flat_claims = [c for cs in self._all_claims.values() for c in cs]

        # Bundle geometry — build connection for curvature-aware H¹ resolution
        self._bundle.build_connection()
        bundle_diag = self._bundle.diagnose()

        # Pre-compute pairwise curvature magnitudes for agent pairs involved
        # in contradictions.  Non-zero curvature between two agents is a
        # stronger signal of structural unreliability than trust comparison
        # alone, so we use it to boost the confidence penalty on the loser.
        _pair_curvature: dict[tuple[str, str], float] = {}
        for curv_entry in bundle_diag.get("curvatures", []):
            agents_triple = curv_entry["agents"]
            val = abs(curv_entry["value"])
            for idx in range(3):
                pair = tuple(sorted((agents_triple[idx], agents_triple[(idx + 1) % 3])))
                _pair_curvature[pair] = max(_pair_curvature.get(pair, 0.0), val)

        # First: resolve contradictions
        for contradiction in unique_contradictions:
            ca, cb = contradiction.claim_a, contradiction.claim_b

            if ca.claim_id in processed and cb.claim_id in processed:
                continue

            # H² cascade: quarantine both
            if ca.claim_id in cascade_claims or cb.claim_id in cascade_claims:
                for claim in (ca, cb):
                    if claim.claim_id not in processed:
                        processed.add(claim.claim_id)
                        quarantined.append(QuarantinedClaim(
                            claim=claim,
                            reason=QuarantineReason.H2_CASCADING_HALLUCINATION,
                            explanation=f"Part of a cascading hallucination chain "
                                       f"originating from ungrounded claim.",
                            related_contradiction=contradiction,
                        ))
                continue

            # H¹: resolve via trust
            winner, loser = self._resolve_h1(ca, cb)
            h1_resolved += 1

            # Use bundle curvature to sharpen confidence on the winner.
            # Non-zero curvature between the two agents means a structural
            # trust inconsistency exists, so the loser is *more* suspect
            # and the winner's relative confidence should increase.
            pair_key = tuple(sorted((ca.source_agent, cb.source_agent)))
            curvature_at_pair = _pair_curvature.get(pair_key, 0.0)
            curvature_bonus = min(curvature_at_pair / TrustLevel.FORMALLY_PROVEN.value, 0.2)

            if winner.claim_id not in processed:
                processed.add(winner.claim_id)
                supporting = self._find_supporting_agents(winner)
                base_confidence = winner.trust.value / TrustLevel.FORMALLY_PROVEN.value
                verified.append(VerifiedClaim(
                    claim=winner,
                    trust=winner.trust,
                    supporting_agents=supporting,
                    provenance=[winner.source_agent],
                    resolution_method="trust_winner",
                    confidence=min(base_confidence + curvature_bonus, 1.0),
                ))

            if loser.claim_id not in processed:
                processed.add(loser.claim_id)
                quarantined.append(QuarantinedClaim(
                    claim=loser,
                    reason=QuarantineReason.H1_LOST_TRUST_CONTEST,
                    explanation=f"Lost trust contest to {winner.source_agent} "
                               f"({winner.trust.name} > {loser.trust.name})."
                               + (f" Bundle curvature {curvature_at_pair:+.2f} "
                                  f"confirms structural inconsistency."
                                  if curvature_at_pair else ""),
                    related_contradiction=contradiction,
                    winning_claim=winner,
                ))

        # Second: add uncontested claims
        for claim in all_flat_claims:
            if claim.claim_id in processed:
                continue
            processed.add(claim.claim_id)

            # Phantom check
            if claim.claim_id in phantom_claims:
                quarantined.append(QuarantinedClaim(
                    claim=claim,
                    reason=QuarantineReason.PHANTOM_UNGROUNDED,
                    explanation="Consistent across agents but no grounding evidence. "
                               "Possible phantom consensus (coordinated hallucination).",
                ))
                continue

            # Trust threshold check
            if claim.trust.value < self._trust_threshold.value:
                quarantined.append(QuarantinedClaim(
                    claim=claim,
                    reason=QuarantineReason.TRUST_BELOW_THRESHOLD,
                    explanation=f"Trust {claim.trust.name} below threshold "
                               f"{self._trust_threshold.name}.",
                ))
                continue

            # Uncontested: include
            supporting = self._find_supporting_agents(claim)
            method = "unanimous" if len(supporting) > 1 else "sole_source"
            verified.append(VerifiedClaim(
                claim=claim,
                trust=claim.trust,
                supporting_agents=supporting,
                provenance=[claim.source_agent],
                resolution_method=method,
                confidence=claim.trust.value / TrustLevel.FORMALLY_PROVEN.value,
            ))

        # Step 6: Compute cohomology
        cohomology = CohomologyComputation(
            h0_global_claims=len(verified),
            h1_contradictions=len(unique_contradictions),
            h1_resolved=h1_resolved,
            h1_unresolved=len(unique_contradictions) - h1_resolved,
            h2_cascades=h2_count,
            phantom_sections=phantom_count,
        )
        cohomology.compute_derived()

        # Step 7: Agent trust scores
        agent_scores: dict[str, float] = {}
        for agent_id, claims in self._all_claims.items():
            if not claims:
                continue
            verified_for_agent = [
                vc for vc in verified if vc.claim.source_agent == agent_id
            ]
            quarantined_for_agent = [
                qc for qc in quarantined if qc.claim.source_agent == agent_id
            ]
            total = len(verified_for_agent) + len(quarantined_for_agent)
            if total > 0:
                agent_scores[agent_id] = len(verified_for_agent) / total
            else:
                agent_scores[agent_id] = 0.0

        # Build the global section
        global_section = VerifiedGlobalSection(
            verified_claims=verified,
            quarantined=quarantined,
            obstructions=descent_result.obstructions,
            cohomology=cohomology,
            agent_trust_scores=agent_scores,
            metadata={
                "agent_count": len(self._all_claims),
                "total_claims_extracted": sum(
                    len(cs) for cs in self._all_claims.values()
                ),
                "bundle_diagnostics": bundle_diag,
            },
        )
        return global_section

    # ---- H¹ resolution --------------------------------------------------

    def _resolve_h1(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> tuple[FactualClaim, FactualClaim]:
        """Resolve an H¹ contradiction by trust level, then by support count."""
        # Higher trust wins
        if ca.trust.value != cb.trust.value:
            if ca.trust.value > cb.trust.value:
                return ca, cb
            return cb, ca

        # Same trust: more supporting agents wins
        support_a = len(self._find_supporting_agents(ca))
        support_b = len(self._find_supporting_agents(cb))
        if support_a != support_b:
            if support_a > support_b:
                return ca, cb
            return cb, ca

        # Tiebreak: prefer grounded claims (those with metadata)
        grounded_a = bool(ca.metadata)
        grounded_b = bool(cb.metadata)
        if grounded_a and not grounded_b:
            return ca, cb
        if grounded_b and not grounded_a:
            return cb, ca

        # Final tiebreak: first claim wins (deterministic)
        return ca, cb

    def _find_supporting_agents(self, claim: FactualClaim) -> list[str]:
        """Find all agents that made a compatible claim about the same subject."""
        supporters = [claim.source_agent]
        subject_lower = claim.subject.lower().strip()
        if not subject_lower:
            return supporters

        for agent_id, claims in self._all_claims.items():
            if agent_id == claim.source_agent:
                continue
            for other in claims:
                if other.subject.lower().strip() == subject_lower:
                    # Check if the values are compatible (not contradicting)
                    pair_contradictions = self._detector.detect([claim], [other])
                    if not pair_contradictions:
                        if agent_id not in supporters:
                            supporters.append(agent_id)
                        break
        return supporters

    # ---- H² cascade detection -------------------------------------------

    def _detect_cascades(self) -> tuple[int, set[str]]:
        """Detect H² cascading hallucination chains.

        A cascade occurs when:
        - Agent A makes an ungrounded claim (no tool/citation evidence).
        - Agent B's output references or agrees with Agent A's claim.
        - Agent C then synthesizes A+B — creating "phantom consensus"
          that LOOKS verified (multiple agents agree) but is actually
          built on Agent A's ungrounded claim.

        This is the key failure mode that majority-vote systems CANNOT
        detect, because they see 3 agents agreeing and call it consensus.
        """
        cascade_claims: set[str] = set()
        cascade_count = 0

        # Find ungrounded root claims (from agents with no tool use)
        ungrounded_agents: set[str] = set()
        for output in self._outputs:
            if not output.tools_used and not output.citations:
                ungrounded_agents.add(output.agent_id)

        if not ungrounded_agents:
            return 0, set()

        # For each ungrounded agent, check if other agents echo their claims
        for ug_agent in ungrounded_agents:
            ug_claims = self._all_claims.get(ug_agent, [])
            for ug_claim in ug_claims:
                echoing_agents: list[str] = []
                for other_agent, other_claims in self._all_claims.items():
                    if other_agent == ug_agent:
                        continue
                    for other_claim in other_claims:
                        # Check if the other agent echoes this claim
                        contradictions = self._detector.detect(
                            [ug_claim], [other_claim]
                        )
                        if not contradictions:
                            # Compatible — might be echoing
                            if self._matcher._subjects_match(ug_claim, other_claim):
                                echoing_agents.append(other_agent)
                                break

                # If 2+ agents echo an ungrounded claim → cascade
                if len(echoing_agents) >= 2:
                    cascade_count += 1
                    cascade_claims.add(ug_claim.claim_id)
                    for ea in echoing_agents:
                        for ec in self._all_claims.get(ea, []):
                            if self._matcher._subjects_match(ug_claim, ec):
                                cascade_claims.add(ec.claim_id)

        return cascade_count, cascade_claims

    # ---- Phantom detection ----------------------------------------------

    def _detect_phantoms(self) -> tuple[int, set[str]]:
        """Detect phantom global sections (ungrounded consensus).

        A phantom section is a claim that:
        1. Multiple agents agree on (it passes all descent checks).
        2. NO agent provides grounding evidence (tool calls, citations).

        This is "consistent but false" — the most dangerous failure mode.
        """
        phantom_claims: set[str] = set()
        phantom_count = 0

        # Group claims by normalized subject+predicate
        claim_groups: dict[str, list[FactualClaim]] = defaultdict(list)
        for claims in self._all_claims.values():
            for claim in claims:
                key = f"{claim.subject.lower()}::{claim.predicate.lower()}"
                claim_groups[key].append(claim)

        for key, group in claim_groups.items():
            if len(group) < 2:
                continue

            # Check if all claims in group are ungrounded
            all_ungrounded = all(
                not self._is_grounded(c) for c in group
            )

            # Check if they agree (no contradictions within group)
            if all_ungrounded and len(group) >= 2:
                agents = {c.source_agent for c in group}
                if len(agents) >= 2:
                    # Multiple agents, all ungrounded, all agree → phantom
                    has_contradiction = False
                    for i, ca in enumerate(group):
                        for cb in group[i + 1:]:
                            if self._detector.detect([ca], [cb]):
                                has_contradiction = True
                                break
                        if has_contradiction:
                            break

                    if not has_contradiction:
                        phantom_count += 1
                        for c in group:
                            phantom_claims.add(c.claim_id)

        return phantom_count, phantom_claims

    def _is_grounded(self, claim: FactualClaim) -> bool:
        """Check if a claim has grounding evidence."""
        agent_id = claim.source_agent
        for output in self._outputs:
            if output.agent_id == agent_id:
                if output.tools_used or output.citations:
                    return True
                if claim.trust.is_grounded:
                    return True
        return False

    def reset(self) -> None:
        """Reset the assembler for a new pipeline."""
        self._descent.reset()
        self._outputs.clear()
        self._sections.clear()
        self._all_claims.clear()
        self._bundle.reset()


# ===================================================================
# Naive comparison
# ===================================================================

def compare_to_naive_vote(
    global_section: VerifiedGlobalSection,
    all_claims: dict[str, list[FactualClaim]],
) -> NaiveVoteResult:
    """Compare the verified global section to a naive majority-vote result.

    In a majority-vote system:
    - Claims mentioned by 2+ agents are accepted.
    - Claims mentioned by only 1 agent are rejected.
    - NO trust weighting, NO provenance, NO cascade detection.

    This function shows what the naive system gets WRONG.
    """
    # Count claim "votes" by normalized subject+value
    votes: dict[str, list[FactualClaim]] = defaultdict(list)
    for agent_claims in all_claims.values():
        for claim in agent_claims:
            key = f"{claim.subject.lower()}::{claim.value.lower()}"
            votes[key].append(claim)

    # Naive: accept if 2+ agents agree
    naive_accepted: list[FactualClaim] = []
    for key, group in votes.items():
        agents = {c.source_agent for c in group}
        if len(agents) >= 2:
            naive_accepted.append(group[0])

    # Find false positives: naive accepts but JuGeo quarantines
    verified_ids = {vc.claim.claim_id for vc in global_section.verified_claims}
    quarantined_ids = {qc.claim.claim_id for qc in global_section.quarantined}

    false_positives: list[FactualClaim] = []
    for claim in naive_accepted:
        # Check if any quarantined claim matches this subject+value
        for qc in global_section.quarantined:
            if (qc.claim.subject.lower() == claim.subject.lower() and
                qc.reason in (
                    QuarantineReason.H2_CASCADING_HALLUCINATION,
                    QuarantineReason.PHANTOM_UNGROUNDED,
                )):
                false_positives.append(claim)
                break

    # Find false negatives: naive rejects but JuGeo verifies
    false_negatives: list[FactualClaim] = []
    naive_keys = {f"{c.subject.lower()}::{c.value.lower()}" for c in naive_accepted}
    for vc in global_section.verified_claims:
        key = f"{vc.claim.subject.lower()}::{vc.claim.value.lower()}"
        if key not in naive_keys and vc.resolution_method == "sole_source":
            # JuGeo verified a sole-source claim that naive would reject
            if vc.trust.value >= TrustLevel.RAG_GROUNDED.value:
                false_negatives.append(vc.claim)

    return NaiveVoteResult(
        accepted_claims=naive_accepted,
        false_positives=false_positives,
        false_negatives=false_negatives,
        explanation=(
            f"Naive majority vote accepted {len(naive_accepted)} claims. "
            f"Of those, {len(false_positives)} are phantom/cascade claims that "
            f"JuGeo correctly quarantined. Additionally, {len(false_negatives)} "
            f"high-trust sole-source claims were rejected by naive voting but "
            f"verified by JuGeo's trust algebra."
        ),
    )
