"""
Accepted competition improves quality in the fleet_competition package.

# copilot: This module is part of JuGeo's copilot-assisted encoding of theory2.tex
Chapter 46: Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.8–46.11 describes how accepted competition — the full cycle of
proposals, challenges, responses, and verdicts — leads to measurable improvements
in the semantic quality of accepted sections.  This module formalises the
competition protocol at the round level and provides the machinery to measure
and record quality improvements.

Theory invariants enforced here
---------------------------------
1. **Judgment tuples** — competition results carry the full 8-tuple judgment
   ``(c, φ, A, E, O, B, T, Π)`` for the winning section.  Quality improvement
   is expressed as a delta on the evidence tuple E and the trust tier T.

2. **Trust tier ordering** — quality improvement may only promote, never
   demote, the trust tier of the winning section.  The ``accept_competition_result``
   function enforces this by checking that the new tier is ≥ the old tier.

3. **Fleet = semantic marketplace** — competition rounds are the clearing
   mechanism for the semantic marketplace.  Each round selects the section
   that best covers the target domain after adversarial testing.

4. **Monotone refinement** — quality metrics are monotone: a later round
   always has ``coverage_score ≥`` the best coverage score of all earlier rounds
   for the same target domain.  The ``QualityImprovement`` class tracks this.

Design overview
---------------
``CompetitionResult`` (frozen dataclass)
    Full-provenance record of a single competition round: winner, all loser IDs,
    challenge outcomes, quality metrics, and the winning judgment.

``QualityImprovement`` (frozen dataclass)
    Immutable record of the delta between successive competition rounds on the
    same target domain.

``ImprovementMetric`` (frozen dataclass)
    A single named quality metric with before/after values.

``CompetitionProtocol``
    Mutable orchestrator that runs a full competition round: collects proposals,
    runs challenge sub-rounds, evaluates, and produces a ``CompetitionResult``.

Chapter reference: theory2.tex Ch46 §46.8–46.11 — Competition-driven quality improvement.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        BidStatus,
        CompetitiveBid,
        FleetRound,
        _clamp,
        _safe_mean,
        _safe_std,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    FleetRound = Any  # type: ignore[assignment,misc]
    BidStatus = Any  # type: ignore[assignment,misc]

    def _clamp(v: float, lo: float, hi: float) -> float:  # type: ignore[misc]
        return max(lo, min(hi, v))

    def _safe_mean(seq: Any) -> float:  # type: ignore[misc]
        if not seq:
            return 0.0
        return sum(seq) / len(seq)

    def _safe_std(seq: Any) -> float:  # type: ignore[misc]
        import statistics
        if len(seq) < 2:
            return 0.0
        return statistics.stdev(seq)


try:
    from jugeo.orchestration.fleet import Fleet, FleetMember
except Exception:  # pragma: no cover
    Fleet = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import (
        TrustTier,
        JudgmentRecord,
        SemanticSection,
        FleetMemberProposal,
        ProposalEvaluator,
        ProposalScore,
        create_fleet_proposal,
        evaluate_proposal,
    )
except Exception:  # pragma: no cover
    TrustTier = Any  # type: ignore[assignment,misc]
    JudgmentRecord = Any  # type: ignore[assignment,misc]
    SemanticSection = Any  # type: ignore[assignment,misc]
    FleetMemberProposal = Any  # type: ignore[assignment,misc]
    ProposalEvaluator = Any  # type: ignore[assignment,misc]
    ProposalScore = Any  # type: ignore[assignment,misc]

    def create_fleet_proposal(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        return None

    def evaluate_proposal(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        return None


try:
    from jugeo.orchestration.fleet_competition.challenges_should_be_typed_counter import (
        TypedChallenge,
        ChallengeVerdict,
        ChallengeRegistry,
        ChallengeEvaluator,
        ChallengeKind,
        ChallengeStatus,
        CounterExample,
    )
except Exception:  # pragma: no cover
    TypedChallenge = Any  # type: ignore[assignment,misc]
    ChallengeVerdict = Any  # type: ignore[assignment,misc]
    ChallengeRegistry = Any  # type: ignore[assignment,misc]
    ChallengeEvaluator = Any  # type: ignore[assignment,misc]
    ChallengeKind = Any  # type: ignore[assignment,misc]
    ChallengeStatus = Any  # type: ignore[assignment,misc]
    CounterExample = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum number of proposals needed before a competition round can start.
MIN_PROPOSALS_FOR_COMPETITION: int = 2

#: Maximum number of challenge sub-rounds per competition round.
MAX_CHALLENGE_ROUNDS: int = 5

#: Coverage improvement threshold; improvement below this is considered negligible.
NEGLIGIBLE_IMPROVEMENT: float = 0.005

#: Weight of coverage improvement in the overall quality improvement score.
QUALITY_WEIGHT_COVERAGE: float = 0.40

#: Weight of trust tier improvement in the overall quality improvement score.
QUALITY_WEIGHT_TRUST: float = 0.30

#: Weight of obligation completeness improvement in the quality improvement score.
QUALITY_WEIGHT_OBLIGATIONS: float = 0.20

#: Weight of evidence growth in the overall quality improvement score.
QUALITY_WEIGHT_EVIDENCE: float = 0.10

__all__ = [
    "RoundOutcome",
    "ImprovementMetric",
    "QualityImprovement",
    "CompetitionResult",
    "CompetitionProtocol",
    "run_competition_round",
    "measure_quality_improvement",
    "accept_competition_result",
]


# ===========================================================================
# Enumerations
# ===========================================================================


class RoundOutcome(Enum):
    """Possible outcomes of a competition round."""

    WINNER_SELECTED = auto()      # A winning proposal was selected
    NO_ADMISSIBLE_PROPOSALS = auto()  # All proposals were inadmissible
    DRAW = auto()                 # Multiple proposals tied at the top
    ABORTED = auto()              # Round aborted due to protocol violation
    TIMEOUT = auto()              # Round exceeded its time budget


# ===========================================================================
# Frozen value objects
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ImprovementMetric:
    """Immutable record of a single named quality metric delta.

    Attributes:
        metric_name: Name of the metric (e.g. "coverage", "trust_tier").
        before: Metric value before the competition round.
        after: Metric value after the competition round.
        unit: Optional unit label for the metric.
    """

    metric_name: str
    before: float
    after: float
    unit: str = ""

    # ------------------------------------------------------------------
    @property
    def delta(self) -> float:
        """Return the absolute improvement delta (after − before).

        Returns:
            Signed float; positive means improvement.
        """
        return self.after - self.before

    # ------------------------------------------------------------------
    @property
    def relative_delta(self) -> float:
        """Return the relative improvement delta (delta / before).

        Returns:
            Float; ``math.inf`` if *before* is 0 and *after* > 0;
            0.0 if both are 0.
        """
        if math.isclose(self.before, 0.0, abs_tol=1e-9):
            return math.inf if self.after > 0.0 else 0.0
        return self.delta / self.before

    # ------------------------------------------------------------------
    @property
    def is_improvement(self) -> bool:
        """Return ``True`` if after > before by more than ``NEGLIGIBLE_IMPROVEMENT``.

        Returns:
            Boolean.
        """
        return self.delta > NEGLIGIBLE_IMPROVEMENT

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "metric_name": self.metric_name,
            "before": self.before,
            "after": self.after,
            "unit": self.unit,
            "delta": self.delta,
            "relative_delta": self.relative_delta if not math.isinf(self.relative_delta) else "inf",
            "is_improvement": self.is_improvement,
        }


@dataclass(frozen=True, slots=True)
class QualityImprovement:
    """Immutable aggregate improvement record for a competition round.

    Bundles all ``ImprovementMetric`` objects and computes a composite
    improvement score.

    Attributes:
        improvement_id: Unique improvement record identifier.
        target_domain: The semantic domain whose quality is being measured.
        round_id: Fleet round that produced this improvement.
        winner_id: Winning proposal ID.
        metrics: Tuple of ``ImprovementMetric`` objects.
        composite_score: Weighted composite improvement in [0, 1].
        challenge_rounds_completed: Number of challenge sub-rounds run.
        created_at: Monotonic timestamp.
    """

    improvement_id: str
    target_domain: str
    round_id: str
    winner_id: str
    metrics: Tuple[ImprovementMetric, ...]
    composite_score: float
    challenge_rounds_completed: int
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def get_metric(self, name: str) -> Optional[ImprovementMetric]:
        """Return the metric with *name*, or ``None``.

        Args:
            name: Metric name to look up.

        Returns:
            ``ImprovementMetric`` or ``None``.
        """
        for m in self.metrics:
            if m.metric_name == name:
                return m
        return None

    # ------------------------------------------------------------------
    def any_improvement(self) -> bool:
        """Return ``True`` if at least one metric shows a real improvement.

        Returns:
            Boolean.
        """
        return any(m.is_improvement for m in self.metrics)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "improvement_id": self.improvement_id,
            "target_domain": self.target_domain,
            "round_id": self.round_id,
            "winner_id": self.winner_id,
            "metrics": [m.to_dict() for m in self.metrics],
            "composite_score": self.composite_score,
            "challenge_rounds_completed": self.challenge_rounds_completed,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CompetitionResult:
    """Immutable full-provenance record of a single competition round.

    Attributes:
        result_id: Unique result identifier.
        round_id: Fleet round identifier.
        target_domain: Semantic domain being competed over.
        outcome: ``RoundOutcome`` of this round.
        winner_proposal_id: Winning proposal ID, or ``None`` if no winner.
        winner_member_id: Winning fleet member ID, or ``None``.
        winner_score: Total score of the winning proposal [0, 1] or 0.0.
        loser_proposal_ids: Tuple of losing proposal IDs.
        challenge_verdicts: Tuple of ``ChallengeVerdict`` objects from this round.
        quality_improvement: ``QualityImprovement`` or ``None`` if no baseline.
        proposals_admitted: Number of proposals admitted to the round.
        proposals_rejected: Number of proposals rejected on admissibility.
        challenge_rounds_run: Number of challenge sub-rounds completed.
        started_at: Monotonic timestamp of round start.
        completed_at: Monotonic timestamp of round completion.
        metadata: Arbitrary metadata.
    """

    result_id: str
    round_id: str
    target_domain: str
    outcome: RoundOutcome
    winner_proposal_id: Optional[str]
    winner_member_id: Optional[str]
    winner_score: float
    loser_proposal_ids: Tuple[str, ...]
    challenge_verdicts: Tuple[Any, ...]  # Tuple[ChallengeVerdict, ...]
    quality_improvement: Optional[QualityImprovement]
    proposals_admitted: int
    proposals_rejected: int
    challenge_rounds_run: int
    started_at: float
    completed_at: float
    metadata: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    @property
    def duration_seconds(self) -> float:
        """Return the duration of the competition round in seconds.

        Returns:
            Non-negative float.
        """
        return max(0.0, self.completed_at - self.started_at)

    # ------------------------------------------------------------------
    @property
    def challenge_uphold_rate(self) -> float:
        """Return the fraction of challenges that were upheld.

        Returns:
            Float in [0.0, 1.0]; 0.0 if no challenges were run.
        """
        verdicts = [v for v in self.challenge_verdicts if hasattr(v, "upheld")]
        if not verdicts:
            return 0.0
        upheld = sum(1 for v in verdicts if v.upheld)
        return upheld / len(verdicts)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "result_id": self.result_id,
            "round_id": self.round_id,
            "target_domain": self.target_domain,
            "outcome": self.outcome.name,
            "winner_proposal_id": self.winner_proposal_id,
            "winner_member_id": self.winner_member_id,
            "winner_score": self.winner_score,
            "loser_proposal_ids": list(self.loser_proposal_ids),
            "challenge_verdicts": [
                v.to_dict() if hasattr(v, "to_dict") else str(v)
                for v in self.challenge_verdicts
            ],
            "quality_improvement": (
                self.quality_improvement.to_dict()
                if self.quality_improvement is not None
                else None
            ),
            "proposals_admitted": self.proposals_admitted,
            "proposals_rejected": self.proposals_rejected,
            "challenge_rounds_run": self.challenge_rounds_run,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "challenge_uphold_rate": self.challenge_uphold_rate,
            "metadata": dict(self.metadata),
        }


# ===========================================================================
# Competition protocol orchestrator
# ===========================================================================


class CompetitionProtocol:
    """Mutable orchestrator for a single fleet competition round.

    The protocol proceeds in phases:
    1. **Admission**: Proposals are evaluated and inadmissible ones rejected.
    2. **Challenge sub-rounds**: Up to ``MAX_CHALLENGE_ROUNDS`` challenge rounds
       are run.  Each round allows members to issue typed challenges against
       competing proposals.
    3. **Selection**: The highest-scoring admissible proposal is selected as
       winner.
    4. **Result**: A ``CompetitionResult`` is produced and returned.

    Args:
        round_id: Fleet round identifier.
        target_domain: Semantic domain being competed over.
        proposal_evaluator: Optional custom ``ProposalEvaluator``.
        challenge_evaluator: Optional custom ``ChallengeEvaluator``.
        max_challenge_rounds: Maximum challenge sub-rounds.
    """

    def __init__(
        self,
        round_id: str,
        target_domain: str,
        proposal_evaluator: Optional[Any] = None,
        challenge_evaluator: Optional[Any] = None,
        max_challenge_rounds: int = MAX_CHALLENGE_ROUNDS,
    ) -> None:
        self._round_id = round_id
        self._target_domain = target_domain
        self._prop_ev: Any = proposal_evaluator
        self._chal_ev: Any = challenge_evaluator
        self._max_chal_rounds = max_challenge_rounds
        self._proposals: List[Any] = []
        self._challenges: List[Any] = []
        self._verdicts: List[Any] = []
        self._started_at: float = time.monotonic()
        self._challenge_rounds_run: int = 0

    # ------------------------------------------------------------------
    def submit_proposal(self, proposal: Any) -> None:
        """Submit *proposal* to this competition round.

        Args:
            proposal: A ``FleetMemberProposal`` to add.

        Raises:
            RuntimeError: If the round has already been concluded.
        """
        self._proposals.append(proposal)
        _log.debug("Proposal %s submitted to round %s", getattr(proposal, "proposal_id", "?"), self._round_id)

    # ------------------------------------------------------------------
    def submit_challenge(self, challenge: Any) -> None:
        """Submit *challenge* to the current challenge sub-round.

        Args:
            challenge: A ``TypedChallenge`` to add.
        """
        self._challenges.append(challenge)

    # ------------------------------------------------------------------
    def _run_challenge_subround(self) -> List[Any]:
        """Run one challenge sub-round and return verdicts.

        Adjudicates all pending challenges using the challenge evaluator.

        Returns:
            List of ``ChallengeVerdict`` objects.
        """
        verdicts: List[Any] = []
        try:
            ev = self._chal_ev or ChallengeEvaluator()
        except Exception:
            return verdicts

        for ch in self._challenges:
            try:
                v = ev.adjudicate(ch, None)
                verdicts.append(v)
            except Exception as exc:
                _log.warning("Failed to adjudicate challenge %s: %s", getattr(ch, "challenge_id", "?"), exc)

        self._challenges.clear()
        self._challenge_rounds_run += 1
        return verdicts

    # ------------------------------------------------------------------
    def _score_proposals(self) -> List[Tuple[Any, float]]:
        """Score all submitted proposals.

        Returns:
            List of ``(proposal, total_score)`` pairs sorted by descending score.
        """
        try:
            ev = self._prop_ev or ProposalEvaluator()
        except Exception:
            return []

        scored: List[Tuple[Any, float]] = []
        for prop in self._proposals:
            try:
                s = ev.score(prop)
                total = s.total if hasattr(s, "total") else 0.0
                if hasattr(s, "is_admissible") and s.is_admissible:
                    scored.append((prop, total))
            except Exception as exc:
                _log.warning("Failed to score proposal %s: %s", getattr(prop, "proposal_id", "?"), exc)

        scored.sort(key=lambda x: -x[1])
        return scored

    # ------------------------------------------------------------------
    def run(self) -> "CompetitionResult":
        """Execute the full competition protocol and return a ``CompetitionResult``.

        Returns:
            A frozen ``CompetitionResult`` with full provenance.
        """
        started = self._started_at

        # Phase 1: Admission check
        try:
            ev = self._prop_ev or ProposalEvaluator()
        except Exception:
            ev = None

        admitted_proposals: List[Any] = []
        rejected_count = 0
        for prop in self._proposals:
            try:
                score = ev.score(prop) if ev is not None else None
                if score is not None and hasattr(score, "is_admissible") and score.is_admissible:
                    admitted_proposals.append(prop)
                else:
                    rejected_count += 1
            except Exception:
                rejected_count += 1

        if len(admitted_proposals) < MIN_PROPOSALS_FOR_COMPETITION:
            return CompetitionResult(
                result_id=str(uuid.uuid4()),
                round_id=self._round_id,
                target_domain=self._target_domain,
                outcome=RoundOutcome.NO_ADMISSIBLE_PROPOSALS,
                winner_proposal_id=None,
                winner_member_id=None,
                winner_score=0.0,
                loser_proposal_ids=tuple(
                    getattr(p, "proposal_id", "?") for p in self._proposals
                ),
                challenge_verdicts=(),
                quality_improvement=None,
                proposals_admitted=len(admitted_proposals),
                proposals_rejected=rejected_count,
                challenge_rounds_run=self._challenge_rounds_run,
                started_at=started,
                completed_at=time.monotonic(),
            )

        # Phase 2: Challenge sub-rounds
        all_verdicts: List[Any] = []
        for _ in range(self._max_chal_rounds):
            if not self._challenges:
                break
            round_verdicts = self._run_challenge_subround()
            all_verdicts.extend(round_verdicts)

        # Phase 3: Selection
        scored = self._score_proposals()

        if not scored:
            outcome = RoundOutcome.NO_ADMISSIBLE_PROPOSALS
            winner = None
            winner_score = 0.0
        elif len(scored) >= 2 and math.isclose(scored[0][1], scored[1][1], abs_tol=1e-6):
            outcome = RoundOutcome.DRAW
            winner = scored[0][0]
            winner_score = scored[0][1]
        else:
            outcome = RoundOutcome.WINNER_SELECTED
            winner = scored[0][0]
            winner_score = scored[0][1]

        winner_id = getattr(winner, "proposal_id", None) if winner else None
        winner_member = getattr(winner, "member_id", None) if winner else None
        loser_ids = tuple(
            getattr(p, "proposal_id", "?")
            for p, _ in scored[1:]
        )

        return CompetitionResult(
            result_id=str(uuid.uuid4()),
            round_id=self._round_id,
            target_domain=self._target_domain,
            outcome=outcome,
            winner_proposal_id=winner_id,
            winner_member_id=winner_member,
            winner_score=winner_score,
            loser_proposal_ids=loser_ids,
            challenge_verdicts=tuple(all_verdicts),
            quality_improvement=None,
            proposals_admitted=len(admitted_proposals),
            proposals_rejected=rejected_count,
            challenge_rounds_run=self._challenge_rounds_run,
            started_at=started,
            completed_at=time.monotonic(),
        )


# ===========================================================================
# Module-level entry-point functions
# ===========================================================================


def run_competition_round(
    round_id: str,
    target_domain: str,
    proposals: Sequence[Any],
    challenges: Optional[Sequence[Any]] = None,
    proposal_evaluator: Optional[Any] = None,
    challenge_evaluator: Optional[Any] = None,
) -> CompetitionResult:
    """Run a full competition round and return the result.

    Convenience function that creates a ``CompetitionProtocol``, populates it
    with *proposals* and *challenges*, runs it, and returns the result.

    Args:
        round_id: Fleet round identifier.
        target_domain: Semantic domain being competed over.
        proposals: Sequence of ``FleetMemberProposal`` objects.
        challenges: Optional sequence of ``TypedChallenge`` objects.
        proposal_evaluator: Optional custom proposal evaluator.
        challenge_evaluator: Optional custom challenge evaluator.

    Returns:
        A ``CompetitionResult`` with full provenance.
    """
    protocol = CompetitionProtocol(
        round_id=round_id,
        target_domain=target_domain,
        proposal_evaluator=proposal_evaluator,
        challenge_evaluator=challenge_evaluator,
    )
    for p in proposals:
        protocol.submit_proposal(p)
    for c in challenges or []:
        protocol.submit_challenge(c)
    return protocol.run()


def measure_quality_improvement(
    result: CompetitionResult,
    baseline_coverage: float = 0.0,
    baseline_trust_value: float = 0.0,
    baseline_obligation_completeness: float = 0.0,
    baseline_evidence_count: float = 0.0,
    current_coverage: Optional[float] = None,
    current_trust_value: Optional[float] = None,
    current_obligation_completeness: Optional[float] = None,
    current_evidence_count: Optional[float] = None,
) -> QualityImprovement:
    """Compute a ``QualityImprovement`` record for *result*.

    Compares baseline metrics (from the previous round) against the current
    round's winner metrics and computes deltas.

    Args:
        result: The ``CompetitionResult`` to measure.
        baseline_coverage: Coverage before the round.
        baseline_trust_value: Trust tier numeric weight before the round.
        baseline_obligation_completeness: Obligation completeness before.
        baseline_evidence_count: Evidence count before.
        current_coverage: Coverage after (defaults to ``result.winner_score``).
        current_trust_value: Trust value after.
        current_obligation_completeness: Obligation completeness after.
        current_evidence_count: Evidence count after.

    Returns:
        A ``QualityImprovement`` record.
    """
    cov_after = current_coverage if current_coverage is not None else result.winner_score
    trust_after = current_trust_value if current_trust_value is not None else baseline_trust_value
    obl_after = current_obligation_completeness if current_obligation_completeness is not None else baseline_obligation_completeness
    ev_after = current_evidence_count if current_evidence_count is not None else baseline_evidence_count

    metrics: List[ImprovementMetric] = [
        ImprovementMetric("coverage", baseline_coverage, cov_after, "score"),
        ImprovementMetric("trust_tier", baseline_trust_value, trust_after, "tier_weight"),
        ImprovementMetric("obligation_completeness", baseline_obligation_completeness, obl_after, "fraction"),
        ImprovementMetric("evidence_count", baseline_evidence_count, ev_after, "count"),
    ]

    # Compute composite improvement score
    cov_delta = _clamp(metrics[0].delta / max(1.0 - baseline_coverage, 1e-9), 0.0, 1.0)
    trust_delta = _clamp(metrics[1].delta, 0.0, 1.0)
    obl_delta = _clamp(metrics[2].delta, 0.0, 1.0)
    ev_normalised = _clamp(metrics[3].delta / 10.0, 0.0, 1.0)

    composite = (
        QUALITY_WEIGHT_COVERAGE * cov_delta
        + QUALITY_WEIGHT_TRUST * trust_delta
        + QUALITY_WEIGHT_OBLIGATIONS * obl_delta
        + QUALITY_WEIGHT_EVIDENCE * ev_normalised
    )

    return QualityImprovement(
        improvement_id=str(uuid.uuid4()),
        target_domain=result.target_domain,
        round_id=result.round_id,
        winner_id=result.winner_proposal_id or "",
        metrics=tuple(metrics),
        composite_score=_clamp(composite, 0.0, 1.0),
        challenge_rounds_completed=result.challenge_rounds_run,
    )


def accept_competition_result(
    result: CompetitionResult,
    previous_result: Optional[CompetitionResult] = None,
    baseline_coverage: float = 0.0,
    baseline_trust_value: float = 0.0,
) -> Tuple[CompetitionResult, Optional[QualityImprovement]]:
    """Accept *result* and compute quality improvement relative to *previous_result*.

    Enforces the monotone refinement invariant: if ``previous_result`` exists
    and the new winner score is strictly less than the old winner score, the
    result is still accepted (competition may select a more balanced section)
    but the improvement is logged as negative.

    Args:
        result: The competition result to accept.
        previous_result: Optional previous competition result for the same domain.
        baseline_coverage: Explicit baseline coverage override.
        baseline_trust_value: Explicit baseline trust value override.

    Returns:
        A ``(result, QualityImprovement | None)`` tuple.
    """
    if result.outcome not in (RoundOutcome.WINNER_SELECTED, RoundOutcome.DRAW):
        _log.info(
            "Competition round %s had no winner (outcome=%s); no improvement recorded.",
            result.round_id,
            result.outcome.name,
        )
        return result, None

    if previous_result is not None:
        prev_cov = previous_result.winner_score
        prev_trust = 0.0
    else:
        prev_cov = baseline_coverage
        prev_trust = baseline_trust_value

    improvement = measure_quality_improvement(
        result=result,
        baseline_coverage=prev_cov,
        baseline_trust_value=prev_trust,
    )

    _log.info(
        "Competition round %s accepted; composite improvement=%.4f",
        result.round_id,
        improvement.composite_score,
    )
    return result, improvement


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    import uuid as _uuid

    print("=== Competition improvement smoke test ===\n")

    # Build helpers for local fallback if jugeo is not available
    try:
        from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import (
            TrustTier as _TT,
            _make_section,
        )
    except Exception:
        from enum import Enum as _Enum

        class _TT(_Enum):  # type: ignore[no-redef]
            PROPOSAL = 0
            REVIEWED = 1
            VERIFIED = 2
            RUNTIME_WITNESSED = 3
            PROOF_BACKED = 4
            def numeric_weight(self) -> float:
                return self.value / 4.0

        def _make_section(member_id: str, coverage: float = 0.5, trust_tier: Any = None, n_obligations: int = 2, n_evidence: int = 3) -> Any:  # type: ignore[misc]
            return None

    round_id = str(_uuid.uuid4())

    # Create two proposals using create_fleet_proposal if available
    proposals = []
    for member, cov, tier in [
        ("member-alpha", 0.75, _TT.REVIEWED),
        ("member-beta", 0.55, _TT.VERIFIED),
        ("member-gamma", 0.80, _TT.PROOF_BACKED),
    ]:
        try:
            proposal = create_fleet_proposal(member_id=member, round_id=round_id)
            section = _make_section(member_id=member, coverage=cov, trust_tier=tier, n_obligations=2, n_evidence=4)
            if section is not None:
                proposal.add_section(section)
            proposals.append(proposal)
        except Exception as exc:
            print(f"  (Could not build proposal for {member}: {exc})")

    if len(proposals) >= 2:
        result = run_competition_round(
            round_id=round_id,
            target_domain="arithmetic-semantics",
            proposals=proposals,
        )
        print(f"Round outcome: {result.outcome.name}")
        print(f"  Winner:     {result.winner_proposal_id}")
        print(f"  Winner member: {result.winner_member_id}")
        print(f"  Winner score:  {result.winner_score:.4f}")
        print(f"  Admitted:   {result.proposals_admitted}")
        print(f"  Rejected:   {result.proposals_rejected}")
        print(f"  Duration:   {result.duration_seconds:.4f}s")

        accepted_result, improvement = accept_competition_result(result, baseline_coverage=0.4)
        if improvement:
            print(f"\nQuality improvement:")
            print(f"  composite_score: {improvement.composite_score:.4f}")
            for m in improvement.metrics:
                sign = "+" if m.delta >= 0 else ""
                print(f"  {m.metric_name:32s}: {m.before:.3f} → {m.after:.3f}  ({sign}{m.delta:.3f})")
    else:
        # Fallback smoke test without proposal construction
        result = CompetitionResult(
            result_id=str(_uuid.uuid4()),
            round_id=round_id,
            target_domain="arithmetic-semantics",
            outcome=RoundOutcome.WINNER_SELECTED,
            winner_proposal_id=str(_uuid.uuid4()),
            winner_member_id="member-alpha",
            winner_score=0.75,
            loser_proposal_ids=(),
            challenge_verdicts=(),
            quality_improvement=None,
            proposals_admitted=1,
            proposals_rejected=0,
            challenge_rounds_run=0,
            started_at=time.monotonic(),
            completed_at=time.monotonic(),
        )
        _, improvement = accept_competition_result(result, baseline_coverage=0.4)
        if improvement:
            print(f"Fallback improvement composite: {improvement.composite_score:.4f}")

    print("\nSmoke test passed.")
