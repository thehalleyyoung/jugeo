"""Challenge Protocol — agents challenging each other's claims with formal adjudication.

When two agents produce contradictory outputs the JuGeo framework requires an
explicit, auditable resolution pathway rather than silent majority-voting.
This module provides the machinery:

Classes
-------
:class:`ChallengeInitiator`
    Factory helpers that convert raw contradictions, completeness gaps, and
    trust violations into formal :class:`Challenge` records.
:class:`ChallengeAdjudicator`
    Score-based adjudicator that weighs evidence quality against trust
    differentials and returns an outcome.
:class:`ChallengeLedger`
    Bounded in-memory store of all challenges with query and analytics
    helpers.
:class:`ChallengeStats`
    Summary statistics over the current ledger contents.
:class:`ChallengePolicy`
    Rate-limiting and cooldown rules that prevent challenge flooding.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import ClassVar

from jugeo_agents.types import (
    Challenge,
    ChallengeOutcome,
    ChallengeType,
    Contradiction,
    FactualClaim,
    TrustLevel,
)

__all__ = [
    "ChallengeInitiator",
    "ChallengeAdjudicator",
    "ChallengeLedger",
    "ChallengeStats",
    "ChallengePolicy",
]


# ---------------------------------------------------------------------------
# 1. ChallengeInitiator — create challenges from detected problems
# ---------------------------------------------------------------------------


class ChallengeInitiator:
    """Factory that converts raw signals into formal :class:`Challenge` records.

    All methods are stateless class-level helpers; no instance is needed but
    the class groups the operations logically.
    """

    # -- Contradiction → Challenge -------------------------------------------

    @staticmethod
    def from_contradiction(
        contradiction: Contradiction,
        challenger_agent: str,
    ) -> Challenge:
        """Convert a :class:`Contradiction` into a formal :class:`Challenge`.

        The *challenger_agent* must be one of the two agents involved in the
        contradiction.  The opposing agent becomes the *challenged* party.

        Parameters
        ----------
        contradiction:
            Detected contradiction between two factual claims.
        challenger_agent:
            Agent ID of the party raising the challenge.

        Returns
        -------
        Challenge
            Formal challenge record with type ``FACTUAL``.
        """
        if challenger_agent == contradiction.agent_a:
            challenged = contradiction.agent_b
            claim = contradiction.claim_b
            alternative = contradiction.claim_a.text
        elif challenger_agent == contradiction.agent_b:
            challenged = contradiction.agent_a
            claim = contradiction.claim_a
            alternative = contradiction.claim_b.text
        else:
            # Default: challenger is external; challenge agent_a's claim.
            challenged = contradiction.agent_a
            claim = contradiction.claim_a
            alternative = contradiction.claim_b.text

        evidence_parts: list[str] = []
        if contradiction.explanation:
            evidence_parts.append(contradiction.explanation)
        if contradiction.repair_hint:
            evidence_parts.append(f"Repair hint: {contradiction.repair_hint}")
        evidence_parts.append(
            f"Confidence: {contradiction.confidence:.2f} "
            f"(kind={contradiction.kind.value})"
        )

        return Challenge(
            challenger=challenger_agent,
            challenged=challenged,
            claim=claim,
            challenge_type=ChallengeType.FACTUAL,
            evidence="; ".join(evidence_parts),
            proposed_alternative=alternative,
        )

    # -- Completeness gap → Challenge ----------------------------------------

    @staticmethod
    def from_completeness_gap(
        agent_id: str,
        missing_dimension: str,
        challenger: str,
    ) -> Challenge:
        """Create a :class:`Challenge` for missing coverage.

        Parameters
        ----------
        agent_id:
            Agent whose output is missing the dimension.
        missing_dimension:
            Human-readable name of the missing aspect (e.g. "risk analysis").
        challenger:
            Agent raising the completeness challenge.

        Returns
        -------
        Challenge
            Formal challenge record with type ``COMPLETENESS``.
        """
        placeholder_claim = FactualClaim(
            text=f"[completeness gap] Missing dimension: {missing_dimension}",
            subject=missing_dimension,
            predicate="missing_from",
            value=agent_id,
            source_agent=agent_id,
        )
        return Challenge(
            challenger=challenger,
            challenged=agent_id,
            claim=placeholder_claim,
            challenge_type=ChallengeType.COMPLETENESS,
            evidence=f"Agent '{agent_id}' output does not address '{missing_dimension}'.",
            proposed_alternative=f"Include analysis of '{missing_dimension}'.",
        )

    # -- Trust violation → Challenge -----------------------------------------

    @staticmethod
    def from_trust_violation(
        claim: FactualClaim,
        actual_channel: str,
        challenger: str,
    ) -> Challenge:
        """Challenge a claim whose trust level exceeds the channel ceiling.

        Parameters
        ----------
        claim:
            The claim with an inflated trust level.
        actual_channel:
            The evidence channel that actually produced the claim.
        challenger:
            Agent raising the trust challenge.

        Returns
        -------
        Challenge
            Formal challenge record with type ``TRUST``.
        """
        return Challenge(
            challenger=challenger,
            challenged=claim.source_agent,
            claim=claim,
            challenge_type=ChallengeType.TRUST,
            evidence=(
                f"Claim trust={claim.trust.name} but arrived via "
                f"channel='{actual_channel}' which should not grant this level."
            ),
            proposed_alternative=(
                f"Demote trust to level appropriate for channel '{actual_channel}'."
            ),
        )


# ---------------------------------------------------------------------------
# 2. ChallengeAdjudicator — score and adjudicate challenges
# ---------------------------------------------------------------------------


class ChallengeAdjudicator:
    """Score-based adjudicator for formal challenges.

    The adjudicator computes a weighted score combining *evidence quality*
    (specificity, citations, tool references) with the *trust differential*
    between the challenger and the challenged agent.  The final score maps
    onto a :class:`ChallengeOutcome`.

    Parameters
    ----------
    trust_weight:
        Weight assigned to the trust-differential component (default 0.4).
    evidence_weight:
        Weight assigned to the evidence-quality component (default 0.6).
    """

    # Patterns used by ``_score_evidence`` for specificity heuristics.
    _CITATION_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\[[\w\s]+(?:,\s*\d{4})?\]"  # e.g. [Smith, 2024] or [RFC 1234]
        r"|https?://\S+"               # URLs
        r"|doi:\S+"                     # DOI references
    )
    _TOOL_REF_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\btool[_\s]?(?:executed|verified|output|result|call)\b",
        re.IGNORECASE,
    )
    _NUMERIC_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b\d+(?:\.\d+)?%?"           # percentages or plain numbers
    )
    _SPECIFICITY_KEYWORDS: ClassVar[frozenset[str]] = frozenset({
        "because", "specifically", "according", "measured",
        "observed", "confirmed", "verified", "evidence",
        "data", "result", "metric", "statistic",
    })

    # Outcome thresholds on the final [0, 1] score.
    _UPHELD_THRESHOLD: ClassVar[float] = 0.65
    _SPLIT_LOWER: ClassVar[float] = 0.40
    _SPLIT_UPPER: ClassVar[float] = 0.65

    def __init__(
        self,
        trust_weight: float = 0.4,
        evidence_weight: float = 0.6,
    ) -> None:
        if not (0.0 <= trust_weight <= 1.0 and 0.0 <= evidence_weight <= 1.0):
            raise ValueError("Weights must be in [0, 1].")
        total = trust_weight + evidence_weight
        if total == 0.0:
            raise ValueError("At least one weight must be positive.")
        # Normalise so they sum to 1.
        self.trust_weight = trust_weight / total
        self.evidence_weight = evidence_weight / total

    # -- Public API ----------------------------------------------------------

    def adjudicate(
        self,
        challenge: Challenge,
        challenger_trust: TrustLevel,
        challenged_trust: TrustLevel,
    ) -> Challenge:
        """Return a *new* :class:`Challenge` with the outcome set.

        The original challenge is not mutated; instead a fresh instance is
        returned with ``outcome`` and ``adjudication_evidence`` filled in.

        Parameters
        ----------
        challenge:
            The pending challenge (``outcome`` should be ``None``).
        challenger_trust:
            Current trust level of the challenger agent.
        challenged_trust:
            Current trust level of the challenged agent.

        Returns
        -------
        Challenge
            Copy of *challenge* with ``outcome`` and
            ``adjudication_evidence`` populated.
        """
        evidence_score = self._score_evidence(challenge.evidence)
        trust_score = self._score_trust_differential(
            challenger_trust, challenged_trust,
        )
        combined = (
            self.evidence_weight * evidence_score
            + self.trust_weight * trust_score
        )

        if combined >= self._UPHELD_THRESHOLD:
            outcome = ChallengeOutcome.UPHELD
        elif combined >= self._SPLIT_LOWER:
            outcome = ChallengeOutcome.SPLIT
        else:
            outcome = ChallengeOutcome.OVERTURNED

        adjudication = (
            f"evidence_score={evidence_score:.3f}, "
            f"trust_score={trust_score:.3f}, "
            f"combined={combined:.3f} "
            f"(e_weight={self.evidence_weight:.2f}, "
            f"t_weight={self.trust_weight:.2f})"
        )

        return Challenge(
            challenger=challenge.challenger,
            challenged=challenge.challenged,
            claim=challenge.claim,
            challenge_type=challenge.challenge_type,
            evidence=challenge.evidence,
            proposed_alternative=challenge.proposed_alternative,
            outcome=outcome,
            adjudication_evidence=adjudication,
            challenge_id=challenge.challenge_id,
            timestamp=challenge.timestamp,
        )

    # -- Scoring helpers (internal) ------------------------------------------

    def _score_evidence(self, evidence: str) -> float:
        """Score evidence quality on a ``[0, 1]`` scale.

        The score is a weighted combination of:

        * **Citation density** — presence of references, URLs, or DOIs.
        * **Tool references** — mentions of tool executions / verifications.
        * **Numeric specificity** — concrete numbers strengthen a claim.
        * **Keyword specificity** — domain-specific reasoning language.
        * **Length bonus** — longer evidence *tends* to be more substantive
          (capped contribution).

        Returns
        -------
        float
            Evidence quality in ``[0, 1]``.
        """
        if not evidence:
            return 0.0

        components: list[float] = []

        # 1. Citation density (up to 0.30 weight).
        citation_count = len(self._CITATION_RE.findall(evidence))
        citation_score = min(citation_count / 3.0, 1.0)
        components.append(0.30 * citation_score)

        # 2. Tool references (up to 0.25 weight).
        tool_count = len(self._TOOL_REF_RE.findall(evidence))
        tool_score = min(tool_count / 2.0, 1.0)
        components.append(0.25 * tool_score)

        # 3. Numeric specificity (up to 0.20 weight).
        num_count = len(self._NUMERIC_RE.findall(evidence))
        num_score = min(num_count / 3.0, 1.0)
        components.append(0.20 * num_score)

        # 4. Keyword specificity (up to 0.15 weight).
        words = set(evidence.lower().split())
        kw_hits = len(words & self._SPECIFICITY_KEYWORDS)
        kw_score = min(kw_hits / 4.0, 1.0)
        components.append(0.15 * kw_score)

        # 5. Length bonus (up to 0.10 weight).
        length_score = min(len(evidence) / 300.0, 1.0)
        components.append(0.10 * length_score)

        return min(sum(components), 1.0)

    @staticmethod
    def _score_trust_differential(
        challenger_trust: TrustLevel,
        challenged_trust: TrustLevel,
    ) -> float:
        """Score the trust differential between challenger and challenged.

        A higher trust for the *challenger* relative to the *challenged*
        makes it more likely the challenge is upheld.

        Returns
        -------
        float
            Score in ``[0, 1]`` where 1.0 means maximum advantage for the
            challenger.
        """
        max_trust = float(TrustLevel.FORMALLY_PROVEN)
        if max_trust == 0.0:
            return 0.5
        diff = float(challenger_trust) - float(challenged_trust)
        # Map [-max_trust, +max_trust] → [0, 1].
        return (diff / (2.0 * max_trust)) + 0.5


# ---------------------------------------------------------------------------
# 3. ChallengeStats — summary dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChallengeStats:
    """Summary statistics over a :class:`ChallengeLedger`.

    Attributes
    ----------
    total:
        Total number of recorded challenges.
    upheld:
        Number of challenges with outcome ``UPHELD``.
    overturned:
        Number of challenges with outcome ``OVERTURNED``.
    split:
        Number of challenges with outcome ``SPLIT``.
    withdrawn:
        Number of challenges with outcome ``WITHDRAWN``.
    most_challenged_agent:
        Agent ID that has been *challenged* the most (empty if ledger is
        empty).
    most_challenging_agent:
        Agent ID that has *issued* the most challenges (empty if ledger is
        empty).
    most_conflicted_pair:
        ``(agent_a, agent_b)`` pair with the highest mutual challenge count,
        or ``("", "")`` if the ledger is empty.
    """

    total: int = 0
    upheld: int = 0
    overturned: int = 0
    split: int = 0
    withdrawn: int = 0
    most_challenged_agent: str = ""
    most_challenging_agent: str = ""
    most_conflicted_pair: tuple[str, str] = ("", "")


# ---------------------------------------------------------------------------
# 4. ChallengeLedger — bounded in-memory store
# ---------------------------------------------------------------------------


class ChallengeLedger:
    """Bounded in-memory store for :class:`Challenge` records.

    The ledger tracks all challenges and exposes analytical queries such as
    per-agent win-rates and conflict-pair rankings.
    """

    def __init__(self, *, max_size: int = 10_000) -> None:
        self._challenges: list[Challenge] = []
        self._max_size = max_size
        # Indices for fast lookup.
        self._by_agent: dict[str, list[int]] = defaultdict(list)
        self._by_pair: dict[tuple[str, str], list[int]] = defaultdict(list)

    # -- Mutation -------------------------------------------------------------

    def record(self, challenge: Challenge) -> None:
        """Add a challenge to the ledger.

        If the ledger has reached its maximum size the oldest entry is
        evicted before the new one is stored.
        """
        if len(self._challenges) >= self._max_size:
            self._rebuild_after_eviction()

        idx = len(self._challenges)
        self._challenges.append(challenge)
        self._by_agent[challenge.challenger].append(idx)
        self._by_agent[challenge.challenged].append(idx)
        pair = _ordered_pair(challenge.challenger, challenge.challenged)
        self._by_pair[pair].append(idx)

    def expire_old(self, max_age_seconds: float) -> None:
        """Remove challenges older than *max_age_seconds*."""
        cutoff = time.time() - max_age_seconds
        self._challenges = [
            c for c in self._challenges if c.timestamp >= cutoff
        ]
        self._rebuild_indices()

    # -- Queries --------------------------------------------------------------

    def challenges_for(self, agent_id: str) -> list[Challenge]:
        """Return all challenges involving *agent_id* (as challenger or challenged)."""
        return [
            self._challenges[i]
            for i in self._by_agent.get(agent_id, [])
            if i < len(self._challenges)
        ]

    def challenges_between(self, agent_a: str, agent_b: str) -> list[Challenge]:
        """Return all challenges between two agents (in either direction)."""
        pair = _ordered_pair(agent_a, agent_b)
        return [
            self._challenges[i]
            for i in self._by_pair.get(pair, [])
            if i < len(self._challenges)
        ]

    def win_rate(self, agent_id: str) -> float:
        """Fraction of adjudicated challenges *agent_id* won.

        An agent *wins* a challenge when:
        * they are the **challenger** and the outcome is ``UPHELD``, or
        * they are the **challenged** and the outcome is ``OVERTURNED``.

        Unadjudicated challenges (``outcome is None``) are ignored.

        Returns
        -------
        float
            Win rate in ``[0, 1]``, or ``0.0`` if no adjudicated challenges
            exist.
        """
        wins = 0
        total = 0
        for c in self.challenges_for(agent_id):
            if c.outcome is None:
                continue
            total += 1
            if c.challenger == agent_id and c.outcome is ChallengeOutcome.UPHELD:
                wins += 1
            elif c.challenged == agent_id and c.outcome is ChallengeOutcome.OVERTURNED:
                wins += 1
        return wins / total if total else 0.0

    def conflict_pairs(self) -> list[tuple[str, str, int]]:
        """Agent pairs ranked by mutual challenge count (descending).

        Returns
        -------
        list[tuple[str, str, int]]
            Each element is ``(agent_a, agent_b, count)`` sorted by *count*
            descending.
        """
        pair_counts: list[tuple[str, str, int]] = [
            (pair[0], pair[1], len(indices))
            for pair, indices in self._by_pair.items()
            if indices
        ]
        pair_counts.sort(key=lambda t: t[2], reverse=True)
        return pair_counts

    def stats(self) -> ChallengeStats:
        """Compute summary statistics over the current ledger contents."""
        outcome_counts: Counter[ChallengeOutcome | None] = Counter()
        challenged_counts: Counter[str] = Counter()
        challenger_counts: Counter[str] = Counter()

        for c in self._challenges:
            outcome_counts[c.outcome] += 1
            challenged_counts[c.challenged] += 1
            challenger_counts[c.challenger] += 1

        most_challenged = (
            challenged_counts.most_common(1)[0][0] if challenged_counts else ""
        )
        most_challenging = (
            challenger_counts.most_common(1)[0][0] if challenger_counts else ""
        )

        top_pair: tuple[str, str] = ("", "")
        conflict_list = self.conflict_pairs()
        if conflict_list:
            top_pair = (conflict_list[0][0], conflict_list[0][1])

        return ChallengeStats(
            total=len(self._challenges),
            upheld=outcome_counts.get(ChallengeOutcome.UPHELD, 0),
            overturned=outcome_counts.get(ChallengeOutcome.OVERTURNED, 0),
            split=outcome_counts.get(ChallengeOutcome.SPLIT, 0),
            withdrawn=outcome_counts.get(ChallengeOutcome.WITHDRAWN, 0),
            most_challenged_agent=most_challenged,
            most_challenging_agent=most_challenging,
            most_conflicted_pair=top_pair,
        )

    # -- Internal helpers -----------------------------------------------------

    def _rebuild_indices(self) -> None:
        """Rebuild secondary indices from the current challenge list."""
        self._by_agent = defaultdict(list)
        self._by_pair = defaultdict(list)
        for idx, c in enumerate(self._challenges):
            self._by_agent[c.challenger].append(idx)
            self._by_agent[c.challenged].append(idx)
            pair = _ordered_pair(c.challenger, c.challenged)
            self._by_pair[pair].append(idx)

    def _rebuild_after_eviction(self) -> None:
        """Evict the oldest 10 % of entries and rebuild indices."""
        evict_count = max(1, self._max_size // 10)
        self._challenges = self._challenges[evict_count:]
        self._rebuild_indices()


# ---------------------------------------------------------------------------
# 5. ChallengePolicy — rate-limiting and cooldown rules
# ---------------------------------------------------------------------------


class ChallengePolicy:
    """Rate-limiting guard for challenge issuance.

    Prevents agents from flooding the system with challenges by enforcing a
    maximum number of challenges per agent per round and a cooldown period
    after an agent has been active.

    Parameters
    ----------
    max_per_agent_per_round:
        Maximum challenges a single agent may issue in one round.
    cooldown_rounds:
        Minimum number of rounds that must elapse between an agent's last
        challenge and the next one.
    """

    def __init__(
        self,
        max_per_agent_per_round: int = 3,
        cooldown_rounds: int = 1,
    ) -> None:
        if max_per_agent_per_round < 1:
            raise ValueError("max_per_agent_per_round must be >= 1.")
        if cooldown_rounds < 0:
            raise ValueError("cooldown_rounds must be >= 0.")
        self.max_per_agent_per_round = max_per_agent_per_round
        self.cooldown_rounds = cooldown_rounds
        # round_number → agent → count
        self._round_counts: dict[int, Counter[str]] = defaultdict(Counter)
        # agent → last round in which they challenged
        self._last_round: dict[str, int] = {}

    def can_challenge(self, challenger: str, round_number: int) -> bool:
        """Return ``True`` if *challenger* is allowed to issue a challenge.

        Parameters
        ----------
        challenger:
            Agent ID of the prospective challenger.
        round_number:
            Current orchestration round.
        """
        # Cooldown check: must have waited enough rounds since last challenge.
        last = self._last_round.get(challenger)
        if last is not None and (round_number - last) <= self.cooldown_rounds:
            return False

        # Per-round cap.
        current = self._round_counts[round_number][challenger]
        return current < self.max_per_agent_per_round

    def record_challenge(self, challenger: str, round_number: int) -> None:
        """Record that *challenger* issued a challenge in *round_number*.

        Should be called **after** successfully creating the challenge.
        """
        self._round_counts[round_number][challenger] += 1
        self._last_round[challenger] = round_number


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _ordered_pair(a: str, b: str) -> tuple[str, str]:
    """Return a canonically ordered agent pair for symmetric lookups."""
    return (a, b) if a <= b else (b, a)
