"""Descent Engine — Čech-cohomological consistency verification for multi-agent outputs.

The *descent condition* in sheaf theory asks whether local sections (one per
agent) can be glued into a coherent global section.  When two agents produce
overlapping outputs, their claims must agree on the overlap.  This module
checks that constraint for every pair of agents, classifies failures by
cohomology class, detects higher-order pathologies (cascading hallucinations,
phantom global sections), and suggests concrete repairs.

Key components
--------------
:class:`LocalSection`
    A single agent's output viewed as a local section of the agent presheaf.
:class:`OverlapRegion`
    The overlap between two agents' sections — paired claims and any
    contradictions found.
:class:`DescentEngine`
    The main stateful engine that accumulates sections, checks pairwise
    consistency, and computes the global descent result.
:class:`DescentReporter`
    Human-readable report generation from :class:`DescentResult`.
:class:`RepairSuggestion`
    A single actionable repair suggestion.
:class:`RepairFrontier`
    Suggests repairs for detected obstructions.
"""

from __future__ import annotations

import itertools
import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from jugeo_agents.types import (
    AgentOutput,
    CohomologyClass,
    Contradiction,
    DescentResult,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    TrustLevel,
)
from jugeo_agents.core.claims import (
    HeuristicContradictionDetector,
    SubjectMatcher,
    make_extractor,
)
from jugeo_agents.core.trust import TrustAlgebra


# ---------------------------------------------------------------------------
# 1. LocalSection — a single agent's output as a presheaf section
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LocalSection:
    """A single agent's output viewed as a local section of the agent presheaf.

    In the JuGeo framework every agent *i* produces a local section
    ``s_i ∈ F(U_i)`` over its assigned open set (subtask).  The descent
    engine collects these sections and checks the cocycle condition on
    every pairwise overlap ``U_i ∩ U_j``.

    Attributes
    ----------
    agent_id:
        Unique identifier of the producing agent.
    claims:
        Factual claims extracted from the agent's output.
    trust:
        Overall trust level assigned by the trust algebra.
    subtask:
        Human-readable description of the subtask this section covers.
    round_number:
        Pipeline round in which the section was produced.
    output:
        The original :class:`AgentOutput`, retained for provenance.
    """

    agent_id: str
    claims: list[FactualClaim]
    trust: TrustLevel
    subtask: str
    round_number: int
    output: AgentOutput | None = None

    # -- convenience ---------------------------------------------------------

    @property
    def claim_count(self) -> int:
        """Number of claims in this section."""
        return len(self.claims)

    @property
    def grounded_claims(self) -> list[FactualClaim]:
        """Claims whose trust is at or above CROSS_AGENT_CONFIRMED."""
        return [c for c in self.claims if c.trust.is_grounded]

    @property
    def ungrounded_claims(self) -> list[FactualClaim]:
        """Claims whose trust is below CROSS_AGENT_CONFIRMED."""
        return [c for c in self.claims if not c.trust.is_grounded]

    @property
    def grounding_ratio(self) -> float:
        """Fraction of claims that are grounded — 1.0 if no claims."""
        if not self.claims:
            return 1.0
        return len(self.grounded_claims) / len(self.claims)

    def subjects(self) -> set[str]:
        """Return the set of distinct subjects across all claims."""
        return {c.subject for c in self.claims if c.subject}


# ---------------------------------------------------------------------------
# 2. OverlapRegion — pairwise overlap between two sections
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OverlapRegion:
    """The overlap between two agents' local sections.

    On the intersection ``U_i ∩ U_j`` the sheaf axiom requires that
    ``ρ_{ij}(s_i) = ρ_{ji}(s_j)`` — i.e., the restriction of both
    sections to shared subjects must agree.

    Attributes
    ----------
    agents:
        The pair of agent identifiers whose overlap this represents.
    shared_claims:
        Pairs ``(claim_from_a, claim_from_b)`` about the same subject.
    contradictions:
        Contradictions detected between the paired claims.
    is_consistent:
        ``True`` iff no contradictions were found on this overlap.
    """

    agents: tuple[str, str]
    shared_claims: list[tuple[FactualClaim, FactualClaim]]
    contradictions: list[Contradiction]
    is_consistent: bool

    @property
    def overlap_size(self) -> int:
        """Number of shared-subject claim pairs."""
        return len(self.shared_claims)

    @property
    def contradiction_rate(self) -> float:
        """Fraction of shared claims that are contradictory."""
        if not self.shared_claims:
            return 0.0
        return len(self.contradictions) / len(self.shared_claims)


# ---------------------------------------------------------------------------
# 3. RepairSuggestion & RepairFrontier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RepairSuggestion:
    """A single actionable repair for a detected obstruction.

    Attributes
    ----------
    action:
        Short imperative description of the repair action
        (e.g., ``"re-query"``, ``"ground-with-tool"``, ``"demote-trust"``).
    target_agent:
        Agent that should perform the repair.
    description:
        Human-readable explanation of *why* this repair helps.
    estimated_effort:
        Rough effort estimate (``"trivial"``, ``"low"``, ``"medium"``,
        ``"high"``).
    """

    action: str
    target_agent: str
    description: str
    estimated_effort: str


_EFFORT_ORDER = {"trivial": 0, "low": 1, "medium": 2, "high": 3}

_KIND_TO_COHOMOLOGY: dict[ObstructionKind, CohomologyClass] = {
    ObstructionKind.SECTION_INCOMPLETE: CohomologyClass.H0,
    ObstructionKind.COVER_GAP: CohomologyClass.H0,
    ObstructionKind.TEMPORAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.QUANTITATIVE_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.DIRECTIONAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.ENTITY_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.LOGICAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.DEPENDENCY_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.TYPE_MISMATCH: CohomologyClass.H1,
    ObstructionKind.TRUST_BOUNDARY_VIOLATION: CohomologyClass.H1,
    ObstructionKind.CASCADING_HALLUCINATION: CohomologyClass.H2,
    ObstructionKind.PHANTOM_GLOBAL_SECTION: CohomologyClass.PHANTOM,
    ObstructionKind.CONTEXT_OVERFLOW: CohomologyClass.H0,
    ObstructionKind.INFINITE_LOOP: CohomologyClass.H0,
    ObstructionKind.TOOL_HALLUCINATION: CohomologyClass.H2,
}

# Severity ordering for sorting obstructions in reports.
_COHOMOLOGY_SEVERITY: dict[CohomologyClass, int] = {
    CohomologyClass.PHANTOM: 4,
    CohomologyClass.H2: 3,
    CohomologyClass.H1: 2,
    CohomologyClass.H0: 1,
}


class RepairFrontier:
    """Suggest concrete repairs for detected obstructions.

    The repair frontier is the *minimal set of actions* that, if carried
    out, would resolve all obstructions.  Each obstruction kind has a
    strategy table; the frontier simply applies the appropriate strategy.
    """

    # -- public API ----------------------------------------------------------

    @staticmethod
    def suggest_repairs(obstruction: Obstruction) -> list[RepairSuggestion]:
        """Return a list of repair suggestions for *obstruction*.

        Strategies are selected by :attr:`ObstructionKind` and refined by
        the agents involved and the specific contradictions.
        """
        kind = obstruction.kind
        agents = obstruction.agents_involved

        if kind == ObstructionKind.SECTION_INCOMPLETE:
            return RepairFrontier._repair_incomplete(obstruction, agents)

        if kind == ObstructionKind.COVER_GAP:
            return RepairFrontier._repair_cover_gap(obstruction, agents)

        if kind in (
            ObstructionKind.TEMPORAL_CONTRADICTION,
            ObstructionKind.QUANTITATIVE_CONTRADICTION,
        ):
            return RepairFrontier._repair_factual_mismatch(obstruction, agents)

        if kind in (
            ObstructionKind.DIRECTIONAL_CONTRADICTION,
            ObstructionKind.LOGICAL_CONTRADICTION,
            ObstructionKind.ENTITY_CONTRADICTION,
            ObstructionKind.DEPENDENCY_CONTRADICTION,
        ):
            return RepairFrontier._repair_logical_mismatch(obstruction, agents)

        if kind == ObstructionKind.TRUST_BOUNDARY_VIOLATION:
            return RepairFrontier._repair_trust_violation(obstruction, agents)

        if kind == ObstructionKind.CASCADING_HALLUCINATION:
            return RepairFrontier._repair_cascading(obstruction, agents)

        if kind == ObstructionKind.PHANTOM_GLOBAL_SECTION:
            return RepairFrontier._repair_phantom(obstruction, agents)

        if kind == ObstructionKind.TOOL_HALLUCINATION:
            return RepairFrontier._repair_tool_hallucination(obstruction, agents)

        if kind == ObstructionKind.TYPE_MISMATCH:
            return RepairFrontier._repair_type_mismatch(obstruction, agents)

        if kind == ObstructionKind.CONTEXT_OVERFLOW:
            return RepairFrontier._repair_context_overflow(obstruction, agents)

        if kind == ObstructionKind.INFINITE_LOOP:
            return RepairFrontier._repair_infinite_loop(obstruction, agents)

        # Fallback for unknown kinds
        return [
            RepairSuggestion(
                action="investigate",
                target_agent=agents[0] if agents else "unknown",
                description=f"Unknown obstruction kind {kind.name}; manual investigation required.",
                estimated_effort="high",
            )
        ]

    # -- private strategy methods --------------------------------------------

    @staticmethod
    def _repair_incomplete(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        suggestions: list[RepairSuggestion] = []
        for agent in agents:
            suggestions.append(
                RepairSuggestion(
                    action="re-invoke",
                    target_agent=agent,
                    description=(
                        f"Agent '{agent}' did not produce a complete output. "
                        f"Re-invoke with the same subtask prompt and verify "
                        f"the output is non-empty."
                    ),
                    estimated_effort="low",
                )
            )
        return suggestions

    @staticmethod
    def _repair_cover_gap(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="assign-subtask",
                target_agent=agents[0] if agents else "orchestrator",
                description=(
                    "The task decomposition has a coverage gap.  Assign the "
                    "missing dimension to an existing agent or spawn a new one."
                ),
                estimated_effort="medium",
            )
        ]

    @staticmethod
    def _repair_factual_mismatch(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        suggestions: list[RepairSuggestion] = []
        # Prefer grounding the lower-trust agent
        trust_ranked = _rank_agents_by_trust(obs.contradictions)
        for agent_id, _ in trust_ranked:
            suggestions.append(
                RepairSuggestion(
                    action="ground-with-tool",
                    target_agent=agent_id,
                    description=(
                        f"Factual mismatch detected.  Ground agent '{agent_id}' "
                        f"claims via a tool call (web search, code execution, etc.) "
                        f"to determine the correct value."
                    ),
                    estimated_effort="medium",
                )
            )
        if len(agents) >= 2 and not suggestions:
            for agent in agents[:2]:
                suggestions.append(
                    RepairSuggestion(
                        action="ground-with-tool",
                        target_agent=agent,
                        description=(
                            f"Ground agent '{agent}' claims via tool execution."
                        ),
                        estimated_effort="medium",
                    )
                )
        return suggestions

    @staticmethod
    def _repair_logical_mismatch(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        suggestions: list[RepairSuggestion] = []
        for agent in agents:
            suggestions.append(
                RepairSuggestion(
                    action="challenge",
                    target_agent=agent,
                    description=(
                        f"Issue a formal challenge to agent '{agent}' asking it "
                        f"to justify its reasoning with explicit evidence."
                    ),
                    estimated_effort="low",
                )
            )
        suggestions.append(
            RepairSuggestion(
                action="adjudicate",
                target_agent="orchestrator",
                description=(
                    "Have the orchestrator adjudicate the logical conflict "
                    "between the involved agents using a treaty negotiation."
                ),
                estimated_effort="medium",
            )
        )
        return suggestions

    @staticmethod
    def _repair_trust_violation(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="demote-trust",
                target_agent=agents[0] if agents else "unknown",
                description=(
                    "A claim exceeds its evidence channel's trust ceiling.  "
                    "Demote the claim to the ceiling or provide additional "
                    "evidence through a higher-trust channel."
                ),
                estimated_effort="trivial",
            )
        ]

    @staticmethod
    def _repair_cascading(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        suggestions: list[RepairSuggestion] = []
        for agent in agents:
            suggestions.append(
                RepairSuggestion(
                    action="break-chain",
                    target_agent=agent,
                    description=(
                        f"Agent '{agent}' is part of a cascading hallucination "
                        f"chain.  Re-invoke with independent tool-grounded evidence "
                        f"to break the ungrounded dependency."
                    ),
                    estimated_effort="high",
                )
            )
        suggestions.append(
            RepairSuggestion(
                action="independent-verification",
                target_agent="orchestrator",
                description=(
                    "Spawn an independent verification agent that does NOT "
                    "receive the outputs of the cascading chain.  Compare its "
                    "results against the chain's conclusions."
                ),
                estimated_effort="high",
            )
        )
        return suggestions

    @staticmethod
    def _repair_phantom(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        suggestions: list[RepairSuggestion] = []
        for agent in agents:
            suggestions.append(
                RepairSuggestion(
                    action="ground-with-tool",
                    target_agent=agent,
                    description=(
                        f"Agent '{agent}' participates in a phantom global section "
                        f"(consistent but entirely ungrounded).  Re-invoke with "
                        f"mandatory tool use or RAG retrieval."
                    ),
                    estimated_effort="medium",
                )
            )
        return suggestions

    @staticmethod
    def _repair_tool_hallucination(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="re-execute-tool",
                target_agent=agents[0] if agents else "unknown",
                description=(
                    "The agent fabricated a tool result.  Re-execute the tool "
                    "call independently and compare the actual output against "
                    "the agent's claimed result."
                ),
                estimated_effort="medium",
            )
        ]

    @staticmethod
    def _repair_type_mismatch(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="align-types",
                target_agent=agents[0] if agents else "unknown",
                description=(
                    "Claims have incompatible types (e.g., expecting a number "
                    "but received a string).  Clarify the expected schema in "
                    "the subtask prompt and re-invoke."
                ),
                estimated_effort="low",
            )
        ]

    @staticmethod
    def _repair_context_overflow(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="split-subtask",
                target_agent=agents[0] if agents else "orchestrator",
                description=(
                    "The agent's context window overflowed, truncating the "
                    "output.  Split the subtask into smaller chunks or use "
                    "a model with a larger context window."
                ),
                estimated_effort="medium",
            )
        ]

    @staticmethod
    def _repair_infinite_loop(
        obs: Obstruction, agents: list[str]
    ) -> list[RepairSuggestion]:
        return [
            RepairSuggestion(
                action="break-loop",
                target_agent="orchestrator",
                description=(
                    "The pipeline has entered an infinite loop (convergence "
                    "failure).  Inject a circuit-breaker: limit the maximum "
                    "number of rounds or force a final adjudication."
                ),
                estimated_effort="low",
            )
        ]


# ---------------------------------------------------------------------------
# Helper: rank agents by trust from contradictions
# ---------------------------------------------------------------------------


def _rank_agents_by_trust(
    contradictions: list[Contradiction],
) -> list[tuple[str, TrustLevel]]:
    """Return agents from contradictions sorted by trust (lowest first).

    Lower-trust agents are listed first because they are the best repair
    targets — their claims are the least trustworthy.
    """
    agent_trusts: dict[str, TrustLevel] = {}
    for c in contradictions:
        if c.agent_a and c.agent_a not in agent_trusts:
            agent_trusts[c.agent_a] = c.claim_a.trust
        if c.agent_b and c.agent_b not in agent_trusts:
            agent_trusts[c.agent_b] = c.claim_b.trust
    return sorted(agent_trusts.items(), key=lambda t: t[1])


def _values_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Check whether two claim value strings are similar enough to indicate propagation."""
    from difflib import SequenceMatcher as _SM

    na = a.strip().lower()
    nb = b.strip().lower()
    if not na or not nb:
        return False
    if na == nb:
        return True
    return _SM(None, na, nb).ratio() >= threshold


# ---------------------------------------------------------------------------
# 4. DescentEngine — the main verification engine
# ---------------------------------------------------------------------------


class DescentEngine:
    """Čech-cohomological consistency checker for multi-agent outputs.

    The engine accumulates :class:`LocalSection` objects (one per agent
    output) and checks the descent / cocycle condition on every pairwise
    overlap.  It classifies obstructions by cohomology class and detects
    higher-order pathologies that pairwise checks alone cannot catch.

    Parameters
    ----------
    claim_extractor:
        Extracts :class:`FactualClaim` instances from agent output text.
        Defaults to :class:`~jugeo_agents.core.claims.make_extractor`.
    contradiction_detector:
        Detects contradictions between two claim lists.
        Defaults to :class:`HeuristicContradictionDetector`.
    trust_algebra:
        Classifies and audits trust levels.
        Defaults to :class:`TrustAlgebra`.

    Examples
    --------
    >>> engine = DescentEngine()
    >>> sec = engine.add_section(AgentOutput(agent_id="a1", output_text="X is 42."))
    >>> result = engine.check_all()
    >>> result.is_consistent
    True
    """

    def __init__(
        self,
        claim_extractor: Any | None = None,
        contradiction_detector: HeuristicContradictionDetector | None = None,
        trust_algebra: TrustAlgebra | None = None,
    ) -> None:
        self._extractor = claim_extractor or make_extractor()
        self._detector = contradiction_detector or HeuristicContradictionDetector()
        self._trust = trust_algebra or TrustAlgebra()
        self._subject_matcher = SubjectMatcher()
        self._sections: list[LocalSection] = []
        self._overlap_cache: dict[tuple[str, str], OverlapRegion] = {}
        self._all_obstructions: list[Obstruction] = []

    # -- section management --------------------------------------------------

    @property
    def sections(self) -> list[LocalSection]:
        """All sections currently registered with the engine."""
        return list(self._sections)

    @property
    def section_count(self) -> int:
        """Number of sections currently registered."""
        return len(self._sections)

    def add_section(self, output: AgentOutput) -> LocalSection:
        """Convert an :class:`AgentOutput` to a :class:`LocalSection` and register it.

        If the output already carries extracted claims they are reused;
        otherwise the engine's claim extractor runs on the output text.
        Trust classification is delegated to the trust algebra.

        Parameters
        ----------
        output:
            The agent output to convert and register.

        Returns
        -------
        LocalSection
            The newly created and registered section.
        """
        # Classify trust level
        trust = self._trust.classify_output(output)

        # Extract claims (use pre-extracted if available)
        if output.claims:
            claims = list(output.claims)
        else:
            claims = self._extractor.extract(output.output_text, output.agent_id)

        # Propagate agent_id and trust to claims that lack them
        for claim in claims:
            if not claim.source_agent:
                claim = claim.with_trust(claim.trust)  # noqa: PLW2901
                # Mutate in-place for source_agent (slots dataclass)
                object.__setattr__(claim, "source_agent", output.agent_id)
            # Stamp unclassified claims with the output-level trust
            if claim.trust == TrustLevel.UNGROUNDED_CLAIM and trust > TrustLevel.UNGROUNDED_CLAIM:
                object.__setattr__(claim, "trust", trust)

        section = LocalSection(
            agent_id=output.agent_id,
            claims=claims,
            trust=trust,
            subtask=output.subtask,
            round_number=output.round_number,
            output=output,
        )
        self._sections.append(section)
        # Invalidate cache entries involving this agent
        self._invalidate_cache(output.agent_id)
        return section

    def _invalidate_cache(self, agent_id: str) -> None:
        """Remove cached overlaps that involve *agent_id*."""
        stale = [
            key for key in self._overlap_cache if agent_id in key
        ]
        for key in stale:
            del self._overlap_cache[key]

    # -- pairwise checking ---------------------------------------------------

    def _pair_shared_claims(
        self, section_a: LocalSection, section_b: LocalSection
    ) -> list[tuple[FactualClaim, FactualClaim]]:
        """Identify claim pairs from *section_a* and *section_b* that share a subject.

        Delegates to :class:`SubjectMatcher` which compares full
        :class:`FactualClaim` objects and returns matched pairs.
        """
        return self._subject_matcher.match(section_a.claims, section_b.claims)

    def check_pairwise(
        self, section_a: LocalSection, section_b: LocalSection
    ) -> OverlapRegion:
        """Check the cocycle condition on the overlap of two local sections.

        Shared claims are identified by subject matching, then the
        contradiction detector runs on those pairs.

        Parameters
        ----------
        section_a:
            First local section.
        section_b:
            Second local section.

        Returns
        -------
        OverlapRegion
            Overlap descriptor including any contradictions found.
        """
        cache_key = tuple(sorted((section_a.agent_id, section_b.agent_id)))
        cache_key = (cache_key[0], cache_key[1])
        if cache_key in self._overlap_cache:
            return self._overlap_cache[cache_key]

        shared = self._pair_shared_claims(section_a, section_b)
        contradictions = self._detector.detect(section_a.claims, section_b.claims)

        overlap = OverlapRegion(
            agents=(section_a.agent_id, section_b.agent_id),
            shared_claims=shared,
            contradictions=contradictions,
            is_consistent=len(contradictions) == 0,
        )
        self._overlap_cache[cache_key] = overlap
        return overlap

    # -- incremental checking ------------------------------------------------

    def check_incremental(self, new_section: LocalSection) -> DescentResult:
        """Check *new_section* against all previously registered sections.

        This is the recommended entry point when sections arrive one at a
        time (streaming / online mode).  It avoids redundant re-checks of
        already-verified pairs.

        Parameters
        ----------
        new_section:
            The section to check (must already be in ``self._sections``).

        Returns
        -------
        DescentResult
            Result covering only the new pairwise checks.
        """
        obstructions: list[Obstruction] = []
        checked_pairs = 0
        total_claims = 0

        for existing in self._sections:
            if existing.agent_id == new_section.agent_id:
                continue
            overlap = self.check_pairwise(new_section, existing)
            checked_pairs += 1
            total_claims += overlap.overlap_size

            for contradiction in overlap.contradictions:
                cohomology = _KIND_TO_COHOMOLOGY.get(
                    contradiction.kind, CohomologyClass.H1
                )
                obs = Obstruction(
                    kind=contradiction.kind,
                    cohomology=cohomology,
                    agents_involved=[contradiction.agent_a, contradiction.agent_b],
                    contradictions=[contradiction],
                    description=contradiction.explanation,
                )
                obstructions.append(obs)

        self._all_obstructions.extend(obstructions)
        score = self._compute_consistency_score(obstructions, total_claims)

        return DescentResult(
            is_consistent=len(obstructions) == 0,
            obstructions=obstructions,
            checked_pairs=checked_pairs,
            total_claims_checked=total_claims,
            consistency_score=score,
        )

    # -- full check ----------------------------------------------------------

    def check_all(self) -> DescentResult:
        """Check all pairs of registered sections.

        This is the batch entry point — it checks every ``(i, j)`` pair
        with ``i < j``.  It also runs the higher-order detectors for
        cascading hallucinations and phantom global sections.

        Returns
        -------
        DescentResult
            Global descent result covering all pairs.
        """
        obstructions: list[Obstruction] = []
        checked_pairs = 0
        total_claims = 0

        # 1. Pairwise H1 checks
        for a, b in itertools.combinations(self._sections, 2):
            overlap = self.check_pairwise(a, b)
            checked_pairs += 1
            total_claims += overlap.overlap_size

            for contradiction in overlap.contradictions:
                cohomology = _KIND_TO_COHOMOLOGY.get(
                    contradiction.kind, CohomologyClass.H1
                )
                obs = Obstruction(
                    kind=contradiction.kind,
                    cohomology=cohomology,
                    agents_involved=[contradiction.agent_a, contradiction.agent_b],
                    contradictions=[contradiction],
                    description=contradiction.explanation,
                )
                obstructions.append(obs)

        # 2. H0 checks — incomplete sections (empty claims)
        for section in self._sections:
            if section.claim_count == 0 and section.output is not None:
                if section.output.output_text.strip():
                    # Output exists but no claims were extractable
                    continue
                obs = Obstruction(
                    kind=ObstructionKind.SECTION_INCOMPLETE,
                    cohomology=CohomologyClass.H0,
                    agents_involved=[section.agent_id],
                    description=(
                        f"Agent '{section.agent_id}' produced no output for "
                        f"subtask '{section.subtask}'."
                    ),
                )
                obstructions.append(obs)

        # 3. H2 checks — cascading hallucinations
        obstructions.extend(self.detect_cascading_hallucinations())

        # 4. Phantom checks — consistent but entirely ungrounded
        obstructions.extend(self.detect_phantom_sections())

        self._all_obstructions = obstructions
        score = self._compute_consistency_score(obstructions, max(total_claims, 1))

        return DescentResult(
            is_consistent=len(obstructions) == 0,
            obstructions=obstructions,
            checked_pairs=checked_pairs,
            total_claims_checked=total_claims,
            consistency_score=score,
        )

    # -- global status -------------------------------------------------------

    def global_status(self) -> DescentResult:
        """Return the current global descent status.

        If no checks have been performed yet, runs :meth:`check_all`.
        Otherwise returns the cached result from the most recent full check.

        Returns
        -------
        DescentResult
            The current global descent status.
        """
        if not self._all_obstructions and len(self._sections) > 1:
            return self.check_all()

        total_claims = sum(s.claim_count for s in self._sections)
        n = len(self._sections)
        checked_pairs = n * (n - 1) // 2 if n > 1 else 0
        score = self._compute_consistency_score(
            self._all_obstructions, max(total_claims, 1)
        )

        return DescentResult(
            is_consistent=len(self._all_obstructions) == 0,
            obstructions=list(self._all_obstructions),
            checked_pairs=checked_pairs,
            total_claims_checked=total_claims,
            consistency_score=score,
        )

    # -- H2: cascading hallucination detection -------------------------------

    def detect_cascading_hallucinations(self) -> list[Obstruction]:
        """Detect H2 obstructions — fabrication cascades.

        A cascading hallucination occurs when a claim propagates through
        multiple agents, each treating the previous agent's ungrounded
        output as fact.  The result is pairwise consistent (all agents
        agree) but globally fabricated (no agent's version traces back to
        a grounded source).

        Detection algorithm
        -------------------
        1. Build a *provenance graph*: for each claim, record which agent
           produced it and what trust level it carries.
        2. Group claims by subject across all sections.
        3. For each subject group, check whether *every* claim in the group
           is ungrounded (trust < CROSS_AGENT_CONFIRMED).
        4. Among those, check whether claims form a *chain*: agent B's claim
           appeared in a later round than agent A's, with matching value,
           suggesting B copied from A without independent verification.
        5. Chains of length ≥ 2 where the root is ungrounded are flagged
           as cascading hallucinations.

        Returns
        -------
        list[Obstruction]
            One obstruction per detected cascade.
        """
        if len(self._sections) < 2:
            return []

        # Group claims by normalised subject across all sections.
        subject_groups: dict[str, list[tuple[FactualClaim, LocalSection]]] = defaultdict(list)
        for section in self._sections:
            for claim in section.claims:
                if claim.subject:
                    key = claim.subject.strip().lower()
                    subject_groups[key].append((claim, section))

        obstructions: list[Obstruction] = []

        for subject_key, group in subject_groups.items():
            if len(group) < 2:
                continue

            # All claims in the group must be ungrounded
            all_ungrounded = all(
                not claim.trust.is_grounded for claim, _ in group
            )
            if not all_ungrounded:
                continue

            # Sort by round number to detect temporal propagation chains
            group_sorted = sorted(group, key=lambda t: t[1].round_number)

            # Check for value propagation: later agents echo earlier agents
            chains = self._find_propagation_chains(group_sorted)

            for chain in chains:
                if len(chain) < 2:
                    continue
                agents_in_chain = [section.agent_id for _, section in chain]
                contradictions_in_chain: list[Contradiction] = []

                # Build pseudo-contradictions to record the chain links
                for i in range(len(chain) - 1):
                    claim_src, sec_src = chain[i]
                    claim_dst, sec_dst = chain[i + 1]
                    contradictions_in_chain.append(
                        Contradiction(
                            claim_a=claim_src,
                            claim_b=claim_dst,
                            agent_a=sec_src.agent_id,
                            agent_b=sec_dst.agent_id,
                            kind=ObstructionKind.CASCADING_HALLUCINATION,
                            confidence=0.8,
                            explanation=(
                                f"Ungrounded claim '{claim_src.subject}' propagated "
                                f"from '{sec_src.agent_id}' (round {sec_src.round_number}) "
                                f"to '{sec_dst.agent_id}' (round {sec_dst.round_number}) "
                                f"without independent grounding."
                            ),
                        )
                    )

                obs = Obstruction(
                    kind=ObstructionKind.CASCADING_HALLUCINATION,
                    cohomology=CohomologyClass.H2,
                    agents_involved=agents_in_chain,
                    contradictions=contradictions_in_chain,
                    description=(
                        f"Cascading hallucination on subject '{subject_key}': "
                        f"{len(chain)}-agent chain with no grounded root.  "
                        f"Agents: {' → '.join(agents_in_chain)}."
                    ),
                )
                obstructions.append(obs)

        return obstructions

    def _find_propagation_chains(
        self,
        group: list[tuple[FactualClaim, LocalSection]],
    ) -> list[list[tuple[FactualClaim, LocalSection]]]:
        """Find propagation chains within a group of claims about the same subject.

        A chain is a sequence of claims where each subsequent claim has a
        similar value to the previous one and was produced in a later (or
        equal) round.  We use a greedy longest-chain approach.

        Returns
        -------
        list[list[tuple[FactualClaim, LocalSection]]]
            Each inner list is a chain of (claim, section) pairs ordered
            by round number.
        """
        if len(group) < 2:
            return []

        chains: list[list[tuple[FactualClaim, LocalSection]]] = []
        used: set[str] = set()

        for i, (claim_i, sec_i) in enumerate(group):
            if claim_i.claim_id in used:
                continue
            chain: list[tuple[FactualClaim, LocalSection]] = [(claim_i, sec_i)]
            used.add(claim_i.claim_id)

            for j in range(i + 1, len(group)):
                claim_j, sec_j = group[j]
                if claim_j.claim_id in used:
                    continue
                if sec_j.agent_id == sec_i.agent_id:
                    continue

                last_claim, last_sec = chain[-1]
                # Same or later round, similar value → propagation
                if sec_j.round_number >= last_sec.round_number:
                    # Check value similarity using SubjectMatcher on
                    # synthetic single-claim lists (the matcher compares
                    # the .subject field, so we use value as subject).
                    if _values_similar(last_claim.value, claim_j.value):
                        chain.append((claim_j, sec_j))
                        used.add(claim_j.claim_id)

            if len(chain) >= 2:
                chains.append(chain)

        return chains

    # -- Phantom: consistent but entirely ungrounded -------------------------

    def detect_phantom_sections(self) -> list[Obstruction]:
        """Detect phantom global sections — consistent everywhere but ungrounded.

        A phantom global section arises when *all* agents agree (no H1
        contradictions) yet *every* claim across all sections has trust
        at or below ``UNGROUNDED_CLAIM``.  The agreement is spurious:
        the agents may have converged on a shared hallucination.

        Detection algorithm
        -------------------
        1. Collect *all* claims from all sections.
        2. If every claim's trust ≤ UNGROUNDED_CLAIM, flag as phantom.
        3. Additionally check per-subject: if a subject is covered by ≥ 2
           agents and all claims about it are ungrounded, that subject is
           a phantom dimension even if other subjects are grounded.

        Returns
        -------
        list[Obstruction]
            One obstruction per phantom dimension.
        """
        if len(self._sections) < 2:
            return []

        obstructions: list[Obstruction] = []

        # Per-subject phantom detection
        subject_agents: dict[str, list[tuple[FactualClaim, str]]] = defaultdict(list)
        for section in self._sections:
            for claim in section.claims:
                if claim.subject:
                    key = claim.subject.strip().lower()
                    subject_agents[key].append((claim, section.agent_id))

        for subject_key, entries in subject_agents.items():
            # Need at least 2 agents covering this subject
            unique_agents = {agent for _, agent in entries}
            if len(unique_agents) < 2:
                continue

            # Check if ALL claims about this subject are ungrounded
            all_ungrounded = all(
                claim.trust <= TrustLevel.UNGROUNDED_CLAIM
                for claim, _ in entries
            )
            if not all_ungrounded:
                continue

            # Check that the claims are actually consistent (not already flagged as H1)
            claims_by_agent: dict[str, list[FactualClaim]] = defaultdict(list)
            for claim, agent in entries:
                claims_by_agent[agent].append(claim)

            pairwise_consistent = True
            agent_list = list(claims_by_agent.keys())
            for i, j in itertools.combinations(range(len(agent_list)), 2):
                c_i = claims_by_agent[agent_list[i]]
                c_j = claims_by_agent[agent_list[j]]
                contradictions = self._detector.detect(c_i, c_j)
                if contradictions:
                    pairwise_consistent = False
                    break

            if not pairwise_consistent:
                # Not phantom — there are contradictions so the H1 detector
                # handles this.
                continue

            obs = Obstruction(
                kind=ObstructionKind.PHANTOM_GLOBAL_SECTION,
                cohomology=CohomologyClass.PHANTOM,
                agents_involved=sorted(unique_agents),
                description=(
                    f"Phantom global section on subject '{subject_key}': "
                    f"{len(unique_agents)} agents agree but all claims are "
                    f"ungrounded (trust ≤ UNGROUNDED_CLAIM).  Likely shared "
                    f"hallucination."
                ),
            )
            obstructions.append(obs)

        # Global phantom check — all claims everywhere ungrounded
        all_claims = [c for s in self._sections for c in s.claims]
        if all_claims and all(
            c.trust <= TrustLevel.UNGROUNDED_CLAIM for c in all_claims
        ):
            all_agents = sorted({s.agent_id for s in self._sections})
            # Only add if not already covered by per-subject phantoms
            if not obstructions:
                obs = Obstruction(
                    kind=ObstructionKind.PHANTOM_GLOBAL_SECTION,
                    cohomology=CohomologyClass.PHANTOM,
                    agents_involved=all_agents,
                    description=(
                        f"Global phantom section: all {len(all_claims)} claims "
                        f"across {len(all_agents)} agents are ungrounded.  "
                        f"The entire pipeline output is unsupported."
                    ),
                )
                obstructions.append(obs)

        return obstructions

    # -- scoring -------------------------------------------------------------

    @staticmethod
    def _compute_consistency_score(
        obstructions: list[Obstruction], total_claims: int
    ) -> float:
        """Compute a [0, 1] consistency score.

        The score is ``1.0`` when there are no obstructions and decays
        toward ``0.0`` as obstructions accumulate.  Higher-cohomology
        obstructions carry heavier penalties:

        - H0: weight 0.5
        - H1: weight 1.0
        - H2: weight 2.0
        - PHANTOM: weight 3.0

        The formula is::

            score = exp(-weighted_obstruction_count / max(total_claims, 1))
        """
        if not obstructions:
            return 1.0

        weight_map = {
            CohomologyClass.H0: 0.5,
            CohomologyClass.H1: 1.0,
            CohomologyClass.H2: 2.0,
            CohomologyClass.PHANTOM: 3.0,
        }
        weighted = sum(
            weight_map.get(obs.cohomology, 1.0) for obs in obstructions
        )
        return math.exp(-weighted / max(total_claims, 1))

    # -- reset ---------------------------------------------------------------

    def reset(self) -> None:
        """Clear all sections, caches, and accumulated obstructions."""
        self._sections.clear()
        self._overlap_cache.clear()
        self._all_obstructions.clear()


# ---------------------------------------------------------------------------
# 5. DescentReporter — human-readable reports
# ---------------------------------------------------------------------------


class DescentReporter:
    """Generate human-readable reports from :class:`DescentResult`.

    All methods are static — the reporter carries no state.
    """

    @staticmethod
    def report(result: DescentResult) -> str:
        """Produce a multi-line report summarising *result*.

        Obstructions are sorted by severity (highest cohomology class
        first).  Each obstruction is formatted with its kind, cohomology
        class, involved agents, and description.

        Parameters
        ----------
        result:
            The descent result to report on.

        Returns
        -------
        str
            Multi-line human-readable report.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("  DESCENT VERIFICATION REPORT")
        lines.append("=" * 72)
        lines.append("")

        # Summary
        status = "✅ CONSISTENT" if result.is_consistent else "❌ INCONSISTENT"
        lines.append(f"  Status:             {status}")
        lines.append(f"  Consistency score:  {result.consistency_score:.2%}")
        lines.append(f"  Pairs checked:      {result.checked_pairs}")
        lines.append(f"  Claims checked:     {result.total_claims_checked}")
        lines.append(f"  Obstructions:       {len(result.obstructions)}")
        lines.append("")

        if not result.obstructions:
            lines.append("  No obstructions detected.  The descent condition holds.")
            lines.append("")
            lines.append("=" * 72)
            return "\n".join(lines)

        # Obstruction summary by cohomology class
        summary = DescentReporter.obstruction_summary(result.obstructions)
        lines.append("  Obstruction summary by cohomology class:")
        for cls_name, count in sorted(
            summary.get("by_cohomology", {}).items(),
            key=lambda kv: _COHOMOLOGY_SEVERITY.get(
                CohomologyClass(kv[0]), 0
            ),
            reverse=True,
        ):
            lines.append(f"    {cls_name}: {count}")
        lines.append("")

        lines.append("  Obstruction summary by kind:")
        for kind_name, count in sorted(
            summary.get("by_kind", {}).items()
        ):
            lines.append(f"    {kind_name}: {count}")
        lines.append("")

        # Detailed obstructions (sorted by severity)
        lines.append("-" * 72)
        lines.append("  DETAILED OBSTRUCTIONS")
        lines.append("-" * 72)
        lines.append("")

        sorted_obs = sorted(
            result.obstructions,
            key=lambda o: _COHOMOLOGY_SEVERITY.get(o.cohomology, 0),
            reverse=True,
        )

        for idx, obs in enumerate(sorted_obs, 1):
            lines.append(f"  [{idx}] {obs.cohomology.value} / {obs.kind.name}")
            lines.append(f"      Agents: {', '.join(obs.agents_involved)}")
            if obs.description:
                # Wrap long descriptions
                desc_lines = _wrap_text(obs.description, width=60)
                for i, dl in enumerate(desc_lines):
                    prefix = "      Desc:   " if i == 0 else "              "
                    lines.append(f"{prefix}{dl}")

            # Show contradictions
            if obs.contradictions:
                lines.append(f"      Contradictions ({len(obs.contradictions)}):")
                for c in obs.contradictions[:5]:  # cap at 5 for readability
                    lines.append(
                        f"        • [{c.kind.name}] {c.agent_a} vs {c.agent_b}"
                        f" (conf={c.confidence:.0%})"
                    )
                    if c.explanation:
                        lines.append(f"          {c.explanation}")
                if len(obs.contradictions) > 5:
                    lines.append(
                        f"        … and {len(obs.contradictions) - 5} more"
                    )

            # Show repair suggestions
            repairs = RepairFrontier.suggest_repairs(obs)
            if repairs:
                lines.append(f"      Suggested repairs:")
                for r in repairs:
                    lines.append(
                        f"        → [{r.estimated_effort}] {r.action} "
                        f"@ {r.target_agent}: {r.description[:80]}"
                    )

            lines.append("")

        lines.append("=" * 72)
        return "\n".join(lines)

    @staticmethod
    def obstruction_summary(
        obstructions: list[Obstruction],
    ) -> dict[str, Any]:
        """Produce counts of obstructions by kind and cohomology class.

        Parameters
        ----------
        obstructions:
            The list of obstructions to summarise.

        Returns
        -------
        dict
            ``{"by_kind": {name: count}, "by_cohomology": {value: count},
            "total": int, "worst_class": str | None}``
        """
        by_kind: Counter[str] = Counter()
        by_cohomology: Counter[str] = Counter()

        for obs in obstructions:
            by_kind[obs.kind.name] += 1
            by_cohomology[obs.cohomology.value] += 1

        worst: str | None = None
        if by_cohomology:
            worst = max(
                by_cohomology.keys(),
                key=lambda v: _COHOMOLOGY_SEVERITY.get(CohomologyClass(v), 0),
            )

        return {
            "by_kind": dict(by_kind),
            "by_cohomology": dict(by_cohomology),
            "total": len(obstructions),
            "worst_class": worst,
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _wrap_text(text: str, width: int = 60) -> list[str]:
    """Simple word-wrap without importing textwrap."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if length + len(word) + 1 > width and current:
            lines.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return lines or [""]
