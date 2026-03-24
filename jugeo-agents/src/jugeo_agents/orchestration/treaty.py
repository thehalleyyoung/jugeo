"""Treaty Negotiation — evidence-based conflict resolution between LLM agents.

When multiple agents in a JuGeo pipeline produce contradictory claims, the
treaty negotiation engine resolves the conflict by applying a prioritised
cascade of resolution strategies.  Each strategy is tried in priority order
until one produces a definitive resolution.

Key components
--------------
:class:`ResolutionStrategy`
    Enum of available conflict-resolution strategies.
:class:`StrategyConfig`
    Per-strategy configuration (priority, cost, description).
:class:`TreatyNegotiator`
    Main negotiation engine — resolves contradictions via the strategy cascade.
:class:`TreatyConstraint`
    A constraint learned from past negotiations.
:class:`PreemptiveTreaty`
    Treaties applied *before* running the pipeline to prevent known conflicts.
:class:`TreatyBuilder`
    Builds preemptive treaties from historical resolution patterns.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo_agents.types import (
    CHANNEL_TRUST_CEILINGS,
    Contradiction,
    EvidenceChannel,
    FactualClaim,
    Obstruction,
    TreatyResolution,
    TrustLevel,
)


# ---------------------------------------------------------------------------
# Resolution strategies
# ---------------------------------------------------------------------------


class ResolutionStrategy(Enum):
    """Available strategies for resolving contradictions between agents."""

    TOOL_ARBITRATION = "tool_arbitration"
    EVIDENCE_WEIGHTING = "evidence_weighting"
    CROSS_REFERENCE = "cross_reference"
    TRUST_ORDERING = "trust_ordering"
    MAJORITY_VOTE = "majority_vote"
    ESCALATION = "escalation"


# ---------------------------------------------------------------------------
# Strategy configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StrategyConfig:
    """Configuration for a single resolution strategy.

    Attributes
    ----------
    strategy:
        Which resolution strategy this config governs.
    priority:
        Lower numbers are tried first.
    requires_tool:
        Whether the strategy needs an external tool / API.
    max_cost:
        Upper bound on the monetary cost of invoking this strategy.
    description:
        Human-readable explanation of the strategy.
    """

    strategy: ResolutionStrategy
    priority: int
    requires_tool: bool
    max_cost: float
    description: str


# ---------------------------------------------------------------------------
# Default strategy configurations
# ---------------------------------------------------------------------------

DEFAULT_STRATEGIES: list[StrategyConfig] = [
    StrategyConfig(
        strategy=ResolutionStrategy.TOOL_ARBITRATION,
        priority=10,
        requires_tool=True,
        max_cost=0.50,
        description=(
            "Re-execute the disputed computation with a tool and accept the "
            "tool's result as ground truth."
        ),
    ),
    StrategyConfig(
        strategy=ResolutionStrategy.EVIDENCE_WEIGHTING,
        priority=20,
        requires_tool=False,
        max_cost=0.0,
        description=(
            "Compare the evidence channels backing each claim and pick the "
            "claim with the higher trust ceiling."
        ),
    ),
    StrategyConfig(
        strategy=ResolutionStrategy.CROSS_REFERENCE,
        priority=30,
        requires_tool=True,
        max_cost=0.10,
        description=(
            "Cross-reference both claims against an independent knowledge "
            "source (RAG retrieval or web search)."
        ),
    ),
    StrategyConfig(
        strategy=ResolutionStrategy.TRUST_ORDERING,
        priority=40,
        requires_tool=False,
        max_cost=0.0,
        description=(
            "Use the calibrated TrustLevel lattice to pick the claim whose "
            "source agent has the higher baseline trust."
        ),
    ),
    StrategyConfig(
        strategy=ResolutionStrategy.MAJORITY_VOTE,
        priority=50,
        requires_tool=False,
        max_cost=0.0,
        description=(
            "Poll all agents for agreement; the majority position wins if a "
            "quorum is reached."
        ),
    ),
    StrategyConfig(
        strategy=ResolutionStrategy.ESCALATION,
        priority=100,
        requires_tool=False,
        max_cost=0.0,
        description=(
            "Flag the contradiction for human review — always succeeds as the "
            "last-resort fallback."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRUST_DIFF_DECISIVE_THRESHOLD: int = 20
"""Minimum integer gap between two TrustLevel values for evidence weighting
to consider the result decisive (avoids resolving near-ties automatically)."""

_MAJORITY_QUORUM: int = 3
"""Minimum number of supporting claims required for a majority-vote win."""


def _make_id() -> str:
    """Return a short unique id."""
    return uuid.uuid4().hex[:12]


def _ts() -> float:
    """Current POSIX timestamp."""
    return time.time()


def _trust_gap(a: TrustLevel, b: TrustLevel) -> int:
    """Signed difference ``a - b`` on the integer trust lattice."""
    return int(a) - int(b)


def _sorted_pair(a: str, b: str) -> tuple[str, str]:
    """Canonical agent-pair key (alphabetical order)."""
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# TreatyNegotiator — main negotiation engine
# ---------------------------------------------------------------------------


class TreatyNegotiator:
    """Resolve contradictions between LLM agents via a strategy cascade.

    Parameters
    ----------
    strategies:
        Strategy configurations in priority order.  Defaults to
        :data:`DEFAULT_STRATEGIES` when *None*.
    evidence_providers:
        Mapping from provider name to an opaque callable / client that can be
        used by strategies like ``TOOL_ARBITRATION`` and ``CROSS_REFERENCE``.
        The negotiator stores but does **not** import any concrete provider
        implementation so it stays dependency-free.
    """

    def __init__(
        self,
        strategies: list[StrategyConfig] | None = None,
        evidence_providers: dict[str, Any] | None = None,
    ) -> None:
        configs = strategies if strategies is not None else list(DEFAULT_STRATEGIES)
        self._strategies: list[StrategyConfig] = sorted(
            configs, key=lambda c: c.priority
        )
        self._providers: dict[str, Any] = evidence_providers or {}
        self._audit: list[str] = []
        self._resolutions: list[TreatyResolution] = []

    # -- public API ---------------------------------------------------------

    def negotiate(
        self,
        contradiction: Contradiction,
        agent_outputs: dict[str, str] | None = None,
    ) -> TreatyResolution:
        """Try strategies in priority order until one succeeds.

        Parameters
        ----------
        contradiction:
            The detected contradiction to resolve.
        agent_outputs:
            Optional mapping of ``agent_name → full output text`` used by
            strategies that need broader context.

        Returns
        -------
        TreatyResolution
            Always returns a resolution — ``ESCALATION`` is the guaranteed
            last-resort fallback.
        """
        cid = contradiction.contradiction_id
        self._log(
            f"[negotiate] Starting resolution for contradiction {cid} "
            f"between {contradiction.agent_a!r} and {contradiction.agent_b!r}"
        )

        for cfg in self._strategies:
            self._log(
                f"[negotiate] Trying strategy {cfg.strategy.value} "
                f"(priority={cfg.priority})"
            )
            result = self._dispatch(cfg.strategy, contradiction, agent_outputs)
            if result is not None:
                self._log(
                    f"[negotiate] Strategy {cfg.strategy.value} succeeded → "
                    f"winner={result.winning_agent!r}"
                )
                self._resolutions.append(result)
                return result
            self._log(
                f"[negotiate] Strategy {cfg.strategy.value} was inconclusive"
            )

        # Should never be reached because ESCALATION always succeeds, but
        # guard defensively.
        fallback = self._try_escalation(contradiction)
        self._resolutions.append(fallback)
        return fallback

    def negotiate_all(
        self, contradictions: list[Contradiction]
    ) -> list[TreatyResolution]:
        """Resolve every contradiction in *contradictions*.

        Returns resolutions in the same order as the input list.
        """
        return [self.negotiate(c) for c in contradictions]

    def audit_trail(self) -> list[str]:
        """Return the full negotiation history as a list of log lines."""
        return list(self._audit)

    @property
    def resolutions(self) -> list[TreatyResolution]:
        """All resolutions produced so far (in chronological order)."""
        return list(self._resolutions)

    # -- strategy dispatch --------------------------------------------------

    def _dispatch(
        self,
        strategy: ResolutionStrategy,
        contradiction: Contradiction,
        agent_outputs: dict[str, str] | None,
    ) -> TreatyResolution | None:
        """Route to the concrete strategy implementation."""
        if strategy is ResolutionStrategy.TOOL_ARBITRATION:
            return self._try_tool_arbitration(contradiction)
        if strategy is ResolutionStrategy.EVIDENCE_WEIGHTING:
            return self._try_evidence_weighting(contradiction)
        if strategy is ResolutionStrategy.CROSS_REFERENCE:
            return self._try_cross_reference(contradiction, agent_outputs)
        if strategy is ResolutionStrategy.TRUST_ORDERING:
            return self._try_trust_ordering(contradiction)
        if strategy is ResolutionStrategy.MAJORITY_VOTE:
            return self._try_majority_vote(contradiction)
        if strategy is ResolutionStrategy.ESCALATION:
            return self._try_escalation(contradiction)
        return None

    # -- concrete strategy implementations ----------------------------------

    def _try_tool_arbitration(
        self, contradiction: Contradiction
    ) -> TreatyResolution | None:
        """Re-execute the disputed operation via an external tool.

        Requires a registered evidence provider keyed ``"tool"`` that exposes
        a ``verify(claim_text: str) -> str | None`` interface.  Returns
        *None* when no tool provider is available.
        """
        provider = self._providers.get("tool")
        if provider is None:
            self._log("[tool_arbitration] No tool provider registered — skip")
            return None

        claim_a = contradiction.claim_a
        claim_b = contradiction.claim_b

        try:
            result_a = provider.verify(claim_a.text)  # type: ignore[union-attr]
            result_b = provider.verify(claim_b.text)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            self._log(f"[tool_arbitration] Provider error: {exc}")
            return None

        if result_a is not None and result_b is None:
            winner, evidence = contradiction.agent_a, result_a
        elif result_b is not None and result_a is None:
            winner, evidence = contradiction.agent_b, result_b
        elif result_a is not None and result_b is not None:
            # Both verified — prefer the one with higher trust.
            if claim_a.trust >= claim_b.trust:
                winner, evidence = contradiction.agent_a, result_a
            else:
                winner, evidence = contradiction.agent_b, result_b
        else:
            self._log("[tool_arbitration] Tool could not verify either claim")
            return None

        trail = self._build_trail(
            "tool_arbitration", contradiction, winner, evidence
        )
        return TreatyResolution(
            success=True,
            winning_agent=winner,
            strategy_used=ResolutionStrategy.TOOL_ARBITRATION.value,
            evidence=str(evidence),
            merged_text=self._merged_text(contradiction, winner),
            audit_trail=trail,
            resolution_id=_make_id(),
            timestamp=_ts(),
        )

    def _try_evidence_weighting(
        self, contradiction: Contradiction
    ) -> TreatyResolution | None:
        """Compare the trust levels backing each claim.

        The claim backed by a strictly higher trust level wins **only** when
        the gap exceeds :data:`_TRUST_DIFF_DECISIVE_THRESHOLD` — resolving
        near-ties automatically would be unsafe.
        """
        claim_a = contradiction.claim_a
        claim_b = contradiction.claim_b
        gap = _trust_gap(claim_a.trust, claim_b.trust)

        self._log(
            f"[evidence_weighting] trust_a={claim_a.trust.name}({int(claim_a.trust)}) "
            f"trust_b={claim_b.trust.name}({int(claim_b.trust)}) gap={gap}"
        )

        if abs(gap) < _TRUST_DIFF_DECISIVE_THRESHOLD:
            self._log(
                f"[evidence_weighting] Gap {abs(gap)} below decisive "
                f"threshold {_TRUST_DIFF_DECISIVE_THRESHOLD} — inconclusive"
            )
            return None

        if gap > 0:
            winner = contradiction.agent_a
            winning_claim = claim_a
            losing_claim = claim_b
        else:
            winner = contradiction.agent_b
            winning_claim = claim_b
            losing_claim = claim_a

        evidence = (
            f"Claim from {winner} has trust {winning_claim.trust.name} "
            f"({int(winning_claim.trust)}) vs {losing_claim.trust.name} "
            f"({int(losing_claim.trust)}); gap={abs(gap)} exceeds "
            f"threshold {_TRUST_DIFF_DECISIVE_THRESHOLD}"
        )

        trail = self._build_trail(
            "evidence_weighting", contradiction, winner, evidence
        )
        return TreatyResolution(
            success=True,
            winning_agent=winner,
            strategy_used=ResolutionStrategy.EVIDENCE_WEIGHTING.value,
            evidence=evidence,
            merged_text=self._merged_text(contradiction, winner),
            audit_trail=trail,
            resolution_id=_make_id(),
            timestamp=_ts(),
        )

    def _try_cross_reference(
        self,
        contradiction: Contradiction,
        agent_outputs: dict[str, str] | None,
    ) -> TreatyResolution | None:
        """Cross-reference claims against an independent knowledge source.

        Uses a ``"search"`` evidence provider when available.  Falls back to
        scanning *agent_outputs* for corroborating text.
        """
        search = self._providers.get("search")
        claim_a = contradiction.claim_a
        claim_b = contradiction.claim_b

        # 1) Try external search provider
        if search is not None:
            try:
                score_a: float = search.score(claim_a.text)  # type: ignore[union-attr]
                score_b: float = search.score(claim_b.text)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._log(f"[cross_reference] Search provider error: {exc}")
                score_a, score_b = 0.0, 0.0

            if score_a != score_b and max(score_a, score_b) > 0.0:
                if score_a > score_b:
                    winner = contradiction.agent_a
                else:
                    winner = contradiction.agent_b
                evidence = (
                    f"Cross-reference scores: "
                    f"{contradiction.agent_a}={score_a:.3f}, "
                    f"{contradiction.agent_b}={score_b:.3f}"
                )
                trail = self._build_trail(
                    "cross_reference", contradiction, winner, evidence
                )
                return TreatyResolution(
                    success=True,
                    winning_agent=winner,
                    strategy_used=ResolutionStrategy.CROSS_REFERENCE.value,
                    evidence=evidence,
                    merged_text=self._merged_text(contradiction, winner),
                    audit_trail=trail,
                    resolution_id=_make_id(),
                    timestamp=_ts(),
                )

        # 2) Fallback: check whether other agent outputs corroborate a claim
        if agent_outputs:
            support_a = self._count_corroboration(
                claim_a.text, contradiction.agent_a, agent_outputs
            )
            support_b = self._count_corroboration(
                claim_b.text, contradiction.agent_b, agent_outputs
            )
            if support_a != support_b:
                if support_a > support_b:
                    winner = contradiction.agent_a
                else:
                    winner = contradiction.agent_b
                evidence = (
                    f"Corroboration in agent outputs: "
                    f"{contradiction.agent_a}={support_a}, "
                    f"{contradiction.agent_b}={support_b}"
                )
                trail = self._build_trail(
                    "cross_reference", contradiction, winner, evidence
                )
                return TreatyResolution(
                    success=True,
                    winning_agent=winner,
                    strategy_used=ResolutionStrategy.CROSS_REFERENCE.value,
                    evidence=evidence,
                    merged_text=self._merged_text(contradiction, winner),
                    audit_trail=trail,
                    resolution_id=_make_id(),
                    timestamp=_ts(),
                )

        self._log("[cross_reference] No decisive cross-reference found")
        return None

    def _try_trust_ordering(
        self, contradiction: Contradiction
    ) -> TreatyResolution | None:
        """Use the calibrated TrustLevel lattice to decide.

        Unlike :meth:`_try_evidence_weighting`, this strategy uses the
        *grounded*/*verified* predicates rather than a raw numeric gap.
        A verified claim always beats a non-verified claim; a grounded
        claim always beats an ungrounded claim.
        """
        claim_a = contradiction.claim_a
        claim_b = contradiction.claim_b

        a_verified = claim_a.trust.is_verified
        b_verified = claim_b.trust.is_verified
        a_grounded = claim_a.trust.is_grounded
        b_grounded = claim_b.trust.is_grounded

        self._log(
            f"[trust_ordering] a: verified={a_verified}, grounded={a_grounded} | "
            f"b: verified={b_verified}, grounded={b_grounded}"
        )

        winner: str | None = None
        reason: str = ""

        # Verified beats non-verified
        if a_verified and not b_verified:
            winner = contradiction.agent_a
            reason = (
                f"{contradiction.agent_a}'s claim is verified "
                f"({claim_a.trust.name}); {contradiction.agent_b}'s is not "
                f"({claim_b.trust.name})"
            )
        elif b_verified and not a_verified:
            winner = contradiction.agent_b
            reason = (
                f"{contradiction.agent_b}'s claim is verified "
                f"({claim_b.trust.name}); {contradiction.agent_a}'s is not "
                f"({claim_a.trust.name})"
            )
        # Grounded beats ungrounded
        elif a_grounded and not b_grounded:
            winner = contradiction.agent_a
            reason = (
                f"{contradiction.agent_a}'s claim is grounded "
                f"({claim_a.trust.name}); {contradiction.agent_b}'s is not "
                f"({claim_b.trust.name})"
            )
        elif b_grounded and not a_grounded:
            winner = contradiction.agent_b
            reason = (
                f"{contradiction.agent_b}'s claim is grounded "
                f"({claim_b.trust.name}); {contradiction.agent_a}'s is not "
                f"({claim_a.trust.name})"
            )

        if winner is None:
            self._log(
                "[trust_ordering] Both claims occupy the same trust tier — "
                "inconclusive"
            )
            return None

        trail = self._build_trail(
            "trust_ordering", contradiction, winner, reason
        )
        return TreatyResolution(
            success=True,
            winning_agent=winner,
            strategy_used=ResolutionStrategy.TRUST_ORDERING.value,
            evidence=reason,
            merged_text=self._merged_text(contradiction, winner),
            audit_trail=trail,
            resolution_id=_make_id(),
            timestamp=_ts(),
        )

    def _try_majority_vote(
        self,
        contradiction: Contradiction,
        all_claims: list[FactualClaim] | None = None,
    ) -> TreatyResolution | None:
        """Majority-vote resolution using all available claims.

        Counts how many *other* agents' claims textually support each side of
        the contradiction.  Requires at least :data:`_MAJORITY_QUORUM`
        supporting claims to declare a winner.
        """
        if not all_claims:
            self._log("[majority_vote] No auxiliary claims provided — skip")
            return None

        claim_a = contradiction.claim_a
        claim_b = contradiction.claim_b

        votes_a = 0
        votes_b = 0
        voter_details_a: list[str] = []
        voter_details_b: list[str] = []

        for claim in all_claims:
            # Skip claims from the two disputing agents
            if claim.source_agent in (contradiction.agent_a, contradiction.agent_b):
                continue

            similarity_a = self._text_similarity(claim.text, claim_a.text)
            similarity_b = self._text_similarity(claim.text, claim_b.text)

            if similarity_a > similarity_b and similarity_a > 0.3:
                votes_a += 1
                voter_details_a.append(
                    f"{claim.source_agent}(sim={similarity_a:.2f})"
                )
            elif similarity_b > similarity_a and similarity_b > 0.3:
                votes_b += 1
                voter_details_b.append(
                    f"{claim.source_agent}(sim={similarity_b:.2f})"
                )

        self._log(
            f"[majority_vote] votes_a={votes_a} votes_b={votes_b} "
            f"quorum={_MAJORITY_QUORUM}"
        )

        if votes_a >= _MAJORITY_QUORUM and votes_a > votes_b:
            winner = contradiction.agent_a
            evidence = (
                f"Majority vote: {votes_a} agents support "
                f"{contradiction.agent_a} [{', '.join(voter_details_a)}] "
                f"vs {votes_b} for {contradiction.agent_b}"
            )
        elif votes_b >= _MAJORITY_QUORUM and votes_b > votes_a:
            winner = contradiction.agent_b
            evidence = (
                f"Majority vote: {votes_b} agents support "
                f"{contradiction.agent_b} [{', '.join(voter_details_b)}] "
                f"vs {votes_a} for {contradiction.agent_a}"
            )
        else:
            self._log("[majority_vote] No quorum reached — inconclusive")
            return None

        trail = self._build_trail(
            "majority_vote", contradiction, winner, evidence
        )
        return TreatyResolution(
            success=True,
            winning_agent=winner,
            strategy_used=ResolutionStrategy.MAJORITY_VOTE.value,
            evidence=evidence,
            merged_text=self._merged_text(contradiction, winner),
            audit_trail=trail,
            resolution_id=_make_id(),
            timestamp=_ts(),
        )

    def _try_escalation(
        self, contradiction: Contradiction
    ) -> TreatyResolution:
        """Escalate to human review — the guaranteed fallback.

        This strategy *always* succeeds.  The returned resolution has
        ``success=True`` but ``winning_agent=""`` because no automated
        decision was reached.
        """
        evidence = (
            f"Contradiction {contradiction.contradiction_id} between "
            f"{contradiction.agent_a} and {contradiction.agent_b} could not "
            f"be resolved automatically.  Escalated for human review.  "
            f"Kind: {contradiction.kind.value if hasattr(contradiction.kind, 'value') else contradiction.kind}; "
            f"confidence: {contradiction.confidence:.2f}."
        )

        trail = self._build_trail(
            "escalation", contradiction, "(human)", evidence
        )
        return TreatyResolution(
            success=True,
            winning_agent="",
            strategy_used=ResolutionStrategy.ESCALATION.value,
            evidence=evidence,
            merged_text="",
            audit_trail=trail,
            resolution_id=_make_id(),
            timestamp=_ts(),
        )

    # -- internal helpers ---------------------------------------------------

    def _log(self, message: str) -> None:
        """Append a timestamped message to the audit trail."""
        self._audit.append(f"{_ts():.3f}  {message}")

    def _build_trail(
        self,
        strategy_name: str,
        contradiction: Contradiction,
        winner: str,
        evidence: str,
    ) -> list[str]:
        """Build a per-resolution audit trail section."""
        return [
            f"strategy: {strategy_name}",
            f"contradiction_id: {contradiction.contradiction_id}",
            f"agent_a: {contradiction.agent_a}",
            f"agent_b: {contradiction.agent_b}",
            f"claim_a_trust: {contradiction.claim_a.trust.name}",
            f"claim_b_trust: {contradiction.claim_b.trust.name}",
            f"winner: {winner}",
            f"evidence: {evidence}",
            f"timestamp: {_ts():.3f}",
        ]

    @staticmethod
    def _merged_text(contradiction: Contradiction, winner: str) -> str:
        """Produce the merged output text using the winning claim."""
        if winner == contradiction.agent_a:
            return contradiction.claim_a.text
        if winner == contradiction.agent_b:
            return contradiction.claim_b.text
        return ""

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        """Cheap word-overlap Jaccard similarity.

        This is intentionally a lightweight heuristic — real deployments
        should swap in an embedding-based similarity function via the
        evidence-provider mechanism.
        """
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    @staticmethod
    def _count_corroboration(
        claim_text: str,
        claim_agent: str,
        agent_outputs: dict[str, str],
    ) -> int:
        """Count how many *other* agents' outputs contain key terms from *claim_text*."""
        key_words = {
            w.lower()
            for w in claim_text.split()
            if len(w) > 3  # skip short common words
        }
        if not key_words:
            return 0

        count = 0
        for agent, output in agent_outputs.items():
            if agent == claim_agent:
                continue
            output_lower = output.lower()
            matches = sum(1 for w in key_words if w in output_lower)
            if matches / len(key_words) > 0.5:
                count += 1
        return count


# ---------------------------------------------------------------------------
# TreatyConstraint — learned constraint from past negotiations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TreatyConstraint:
    """A constraint learned from a past treaty negotiation.

    Constraints capture repeating patterns — e.g., *"prefer tool-verified
    over LLM-generated for numerical claims between agent-X and agent-Y"* —
    so that future pipelines can avoid the same dispute.

    Attributes
    ----------
    agent_pair:
        Canonical (alphabetically sorted) pair of agent names.
    domain:
        Subject domain to which the constraint applies (e.g., ``"numerical"``,
        ``"temporal"``, ``"entity"``).
    rule:
        Human-readable description of the constraint.
    confidence:
        Float in ``[0, 1]`` reflecting how strongly the historical evidence
        supports this constraint.
    source_resolution_id:
        The :attr:`TreatyResolution.resolution_id` from which this constraint
        was extracted.
    """

    agent_pair: tuple[str, str]
    domain: str
    rule: str
    confidence: float
    source_resolution_id: str


# ---------------------------------------------------------------------------
# PreemptiveTreaty — applied before pipeline execution
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PreemptiveTreaty:
    """A treaty applied *before* running the pipeline.

    Preemptive treaties encode knowledge from past negotiations so that the
    orchestrator can configure agent priorities, prompt constraints, or tool
    selection to avoid repeating known conflicts.

    Attributes
    ----------
    agents:
        The pair of agents this treaty governs.
    constraints:
        Ordered list of constraints that should be enforced.
    rationale:
        Human-readable explanation of why this treaty exists.
    """

    agents: tuple[str, str]
    constraints: list[TreatyConstraint]
    rationale: str


# ---------------------------------------------------------------------------
# TreatyBuilder — build preemptive treaties from history
# ---------------------------------------------------------------------------


# Mapping from ObstructionKind name fragments to domain labels used in
# TreatyConstraint.  Kept intentionally coarse — callers can override.
_KIND_TO_DOMAIN: dict[str, str] = {
    "QUANTITATIVE": "numerical",
    "TEMPORAL": "temporal",
    "ENTITY": "entity",
    "DIRECTIONAL": "directional",
    "LOGICAL": "logical",
    "DEPENDENCY": "dependency",
    "TYPE": "type",
    "TRUST": "trust",
    "CASCADING": "hallucination",
    "PHANTOM": "phantom",
    "SECTION": "coverage",
    "COVER": "coverage",
    "CONTEXT": "context",
    "INFINITE": "control_flow",
    "TOOL": "tool",
}


def _infer_domain(kind_name: str) -> str:
    """Map an ObstructionKind name to a coarse domain label."""
    upper = kind_name.upper()
    for prefix, domain in _KIND_TO_DOMAIN.items():
        if prefix in upper:
            return domain
    return "general"


def _describe_strategy(strategy: str) -> str:
    """Return a human-readable constraint rule for a resolution strategy."""
    rules: dict[str, str] = {
        ResolutionStrategy.TOOL_ARBITRATION.value: (
            "prefer tool-verified results over LLM-generated claims"
        ),
        ResolutionStrategy.EVIDENCE_WEIGHTING.value: (
            "prefer the claim with the higher evidence channel ceiling"
        ),
        ResolutionStrategy.CROSS_REFERENCE.value: (
            "cross-reference against independent sources before accepting"
        ),
        ResolutionStrategy.TRUST_ORDERING.value: (
            "defer to the agent with the higher calibrated trust level"
        ),
        ResolutionStrategy.MAJORITY_VOTE.value: (
            "require majority agreement from other pipeline agents"
        ),
        ResolutionStrategy.ESCALATION.value: (
            "escalate to human review"
        ),
    }
    return rules.get(strategy, f"apply strategy {strategy}")


class TreatyBuilder:
    """Build :class:`PreemptiveTreaty` objects from historical resolutions.

    The builder analyses past :class:`TreatyResolution` records for a given
    agent pair, identifies recurring resolution patterns, and emits a
    :class:`PreemptiveTreaty` with :class:`TreatyConstraint` entries that
    the orchestrator can apply proactively.
    """

    def __init__(self, min_confidence: float = 0.5, min_occurrences: int = 2) -> None:
        self._min_confidence = min_confidence
        self._min_occurrences = min_occurrences

    def from_history(
        self,
        resolutions: list[TreatyResolution],
        agent_a: str,
        agent_b: str,
    ) -> PreemptiveTreaty | None:
        """Build a preemptive treaty from past resolutions between two agents.

        Parameters
        ----------
        resolutions:
            All historical :class:`TreatyResolution` records.
        agent_a, agent_b:
            The two agents to analyse.

        Returns
        -------
        PreemptiveTreaty | None
            A treaty if enough recurring patterns exist; *None* otherwise.
        """
        pair = _sorted_pair(agent_a, agent_b)

        # Filter to resolutions involving this pair.
        relevant = self._filter_resolutions(resolutions, pair)
        if not relevant:
            return None

        # Tally strategies used.
        strategy_counts: Counter[str] = Counter()
        winner_counts: Counter[str] = Counter()
        domain_strategy: dict[str, Counter[str]] = defaultdict(Counter)

        for res in relevant:
            strategy_counts[res.strategy_used] += 1
            if res.winning_agent:
                winner_counts[res.winning_agent] += 1
            domain = self._extract_domain(res)
            domain_strategy[domain][res.strategy_used] += 1

        total = len(relevant)
        constraints: list[TreatyConstraint] = []

        # Build constraints from domain-specific patterns
        for domain, strat_counter in domain_strategy.items():
            for strategy, count in strat_counter.most_common():
                if count < self._min_occurrences:
                    continue
                confidence = count / total
                if confidence < self._min_confidence:
                    continue

                rule = _describe_strategy(strategy)
                # Find a representative resolution_id
                source_id = self._find_source_id(relevant, strategy, domain)

                constraints.append(
                    TreatyConstraint(
                        agent_pair=pair,
                        domain=domain,
                        rule=f"{rule} for {domain} claims",
                        confidence=round(confidence, 3),
                        source_resolution_id=source_id,
                    )
                )

        # Also build a global constraint if a single strategy dominated
        for strategy, count in strategy_counts.most_common():
            if count < self._min_occurrences:
                continue
            confidence = count / total
            if confidence < self._min_confidence:
                # Check if already covered by a domain constraint
                already = any(c.domain != "general" for c in constraints)
                if already:
                    continue
                break

            rule = _describe_strategy(strategy)
            source_id = next(
                (r.resolution_id for r in relevant if r.strategy_used == strategy),
                "",
            )
            constraints.append(
                TreatyConstraint(
                    agent_pair=pair,
                    domain="general",
                    rule=rule,
                    confidence=round(confidence, 3),
                    source_resolution_id=source_id,
                )
            )
            break  # only the dominant strategy

        if not constraints:
            return None

        # Build rationale
        dominant_winner = winner_counts.most_common(1)[0][0] if winner_counts else "N/A"
        rationale = (
            f"Derived from {total} historical resolutions between "
            f"{pair[0]} and {pair[1]}.  "
            f"Dominant winner: {dominant_winner} "
            f"({winner_counts.get(dominant_winner, 0)}/{total}).  "
            f"Primary strategy: {strategy_counts.most_common(1)[0][0]} "
            f"(used {strategy_counts.most_common(1)[0][1]}× out of {total})."
        )

        return PreemptiveTreaty(
            agents=pair,
            constraints=sorted(constraints, key=lambda c: -c.confidence),
            rationale=rationale,
        )

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _filter_resolutions(
        resolutions: list[TreatyResolution],
        pair: tuple[str, str],
    ) -> list[TreatyResolution]:
        """Keep only resolutions whose audit trail mentions both agents."""
        relevant: list[TreatyResolution] = []
        for res in resolutions:
            agents_in_trail = set()
            for line in res.audit_trail:
                if line.startswith("agent_a: "):
                    agents_in_trail.add(line.removeprefix("agent_a: "))
                elif line.startswith("agent_b: "):
                    agents_in_trail.add(line.removeprefix("agent_b: "))
            trail_pair = _sorted_pair(*agents_in_trail) if len(agents_in_trail) == 2 else None
            if trail_pair == pair:
                relevant.append(res)
        return relevant

    @staticmethod
    def _extract_domain(res: TreatyResolution) -> str:
        """Infer a domain label from a resolution's audit trail."""
        for line in res.audit_trail:
            if line.startswith("evidence: "):
                evidence_text = line.removeprefix("evidence: ")
                # Heuristic: look for known domain keywords
                lower = evidence_text.lower()
                for keyword, domain in _KIND_TO_DOMAIN.items():
                    if keyword.lower() in lower:
                        return domain
        return "general"

    @staticmethod
    def _find_source_id(
        resolutions: list[TreatyResolution],
        strategy: str,
        domain: str,
    ) -> str:
        """Find a representative resolution ID for a strategy + domain."""
        for res in resolutions:
            if res.strategy_used != strategy:
                continue
            for line in res.audit_trail:
                if line.startswith("evidence: ") and domain != "general":
                    if domain.lower() in line.lower():
                        return res.resolution_id
            if domain == "general":
                return res.resolution_id
        # Fallback: first resolution with the matching strategy
        return next(
            (r.resolution_id for r in resolutions if r.strategy_used == strategy),
            "",
        )
