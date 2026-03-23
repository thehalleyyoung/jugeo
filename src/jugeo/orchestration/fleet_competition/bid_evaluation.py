"""
Bid evaluation engine for the fleet_competition orchestration package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 46:
Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.3 describes the *evaluation sub-system* of the fleet competition
protocol.  Once bids are collected in a :class:`~models.FleetRound`, this module
provides the machinery to score, rank, filter, and select them.  The design
separates four orthogonal concerns:

1. **Criterion-based scoring** (:class:`BidEvaluationCriterion`,
   :class:`MultiCriterionEvaluator`) — each bid is scored independently on
   multiple weighted criteria and the scores are combined into a total.

2. **Pareto filtering** (:class:`ParetoFilter`) — before auction-style
   elimination, Pareto-dominated bids are pruned to keep only the frontier,
   consistent with the theory's notion of *admissible futures*.

3. **Ranked selection** (:class:`BidRanker`) — the filtered set is ranked by a
   configurable scoring function; ties are broken lexicographically on bid_id.

4. **Auction dynamics** (:class:`BidAuction`) — a multi-round elimination
   auction repeatedly scores and eliminates the bottom fraction until a winner
   emerges or a single bid remains.

An :class:`EvaluationHistory` accumulates evaluation results per fleet member
over time, providing per-member statistics (average score, win rate, recent
scores) that feed back into the calibration layer.

Chapter reference: theory2.tex Ch46 — Fleet semantics.

copilot
"""
from __future__ import annotations

import math
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        BidDelta,
        BidStatus,
        CalibrationStatus,
        CalibrationTrace,
        ChallengeRecord,
        CompetitiveBid,
        FleetRound,
        RoundPhase,
        _clamp,
        _safe_mean,
        _safe_std,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    BidDelta = Any  # type: ignore[assignment,misc]
    FleetRound = Any  # type: ignore[assignment,misc]
    BidStatus = Any  # type: ignore[assignment,misc]
    RoundPhase = Any  # type: ignore[assignment,misc]
    CalibrationStatus = Any  # type: ignore[assignment,misc]
    CalibrationTrace = Any  # type: ignore[assignment,misc]
    ChallengeRecord = Any  # type: ignore[assignment,misc]

    def _clamp(v: float, lo: float, hi: float) -> float:  # type: ignore[misc]
        return max(lo, min(hi, v))

    def _safe_mean(seq: Any) -> float:  # type: ignore[misc]
        if not seq:
            return 0.0
        return sum(seq) / len(seq)

    def _safe_std(seq: Any) -> float:  # type: ignore[misc]
        if len(seq) < 2:
            return 0.0
        return statistics.stdev(seq)


try:
    from jugeo.orchestration.fleet import (
        BidEvaluator,
        BidOutcome,
        Fleet,
        FleetBid,
        FleetMember,
    )
except Exception:  # pragma: no cover
    BidEvaluator = Any  # type: ignore[assignment,misc]
    BidOutcome = Any  # type: ignore[assignment,misc]
    Fleet = Any  # type: ignore[assignment,misc]
    FleetBid = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]


try:
    from jugeo.evidence.trust import TrustAlgebra, TrustCeiling, TrustLevel
except Exception:  # pragma: no cover
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustAlgebra = Any  # type: ignore[assignment,misc]
    TrustCeiling = Any  # type: ignore[assignment,misc]


__all__ = [
    "BidEvaluation",
    "BidEvaluationCriterion",
    "MultiCriterionEvaluator",
    "ParetoFilter",
    "BidRanker",
    "BidAuction",
    "EvaluationHistory",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default weight for a criterion if not otherwise specified.
DEFAULT_CRITERION_WEIGHT: float = 1.0

#: Default number of rounds in a :class:`BidAuction`.
DEFAULT_AUCTION_ROUNDS: int = 3

#: Default fraction of bids to eliminate per auction round.
DEFAULT_ELIMINATION_FRACTION: float = 0.5

#: Maximum number of evaluation records per member in :class:`EvaluationHistory`.
DEFAULT_HISTORY_MAX_SIZE: int = 1000

#: Small epsilon used to avoid division-by-zero in normalisation.
_EPS: float = 1e-12


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_weights(weights: list[float]) -> list[float]:
    """Normalise *weights* so that they sum to 1.0.

    Parameters
    ----------
    weights:
        List of non-negative floats.

    Returns
    -------
    list[float]
        Normalised weights; if the total is zero every weight becomes
        ``1 / len(weights)``.
    """
    total = sum(weights)
    if total < _EPS:
        n = max(len(weights), 1)
        return [1.0 / n] * len(weights)
    return [w / total for w in weights]


def _rank_list(scores: list[float]) -> list[int]:
    """Convert a list of scores to 1-based ranks (highest score → rank 1).

    Ties are broken by position (earlier position wins on a tie), giving
    deterministic output without requiring bid_id secondary sort at this level.

    Parameters
    ----------
    scores:
        List of numeric scores in the same order as the bids.

    Returns
    -------
    list[int]
        1-based rank for each position in *scores*.
    """
    indexed = sorted(enumerate(scores), key=lambda x: -x[1])
    ranks = [0] * len(scores)
    for rank_zero_based, (original_idx, _) in enumerate(indexed):
        ranks[original_idx] = rank_zero_based + 1
    return ranks


# ---------------------------------------------------------------------------
# BidEvaluation — frozen evaluation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BidEvaluation:
    """Immutable result of evaluating a single :class:`CompetitiveBid`.

    Produced by :class:`MultiCriterionEvaluator` after scoring a bid on all
    registered criteria.  The ``rank`` field is assigned post-hoc by
    :meth:`MultiCriterionEvaluator.evaluate_all` once the full set of bids
    has been scored.

    Attributes
    ----------
    bid_id:
        Identifier of the bid that was evaluated.
    scores:
        Dictionary mapping criterion name → raw criterion score (before
        weighting; in [0, 1]).
    total_score:
        Weighted sum of criterion scores; in [0, 1] after normalisation.
    rank:
        1-based rank among all evaluated bids in the same round (1 = best).
        Defaults to 0 (unranked) until :meth:`~MultiCriterionEvaluator.evaluate_all`
        is called.
    rationale:
        Human-readable explanation of the evaluation, if provided.
    evaluated_at:
        Wall-clock timestamp of evaluation.
    """

    bid_id: str
    scores: dict[str, float]
    total_score: float
    rank: int = 0
    rationale: str = ""
    evaluated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def is_winner(self) -> bool:
        """Return True if this evaluation has rank 1.

        Returns
        -------
        bool
            ``True`` iff ``rank == 1``.
        """
        return self.rank == 1

    def dominant_over(self, other: "BidEvaluation") -> bool:
        """Return True if this evaluation dominates *other* on total score.

        A simple scalar dominance check: *self* dominates *other* iff
        ``self.total_score > other.total_score``.  For multi-criterion Pareto
        dominance at the bid level use :class:`ParetoFilter`.

        Parameters
        ----------
        other:
            The evaluation to compare against.

        Returns
        -------
        bool
            ``True`` iff ``self.total_score > other.total_score``.
        """
        return self.total_score > other.total_score

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this evaluation to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "bid_id": self.bid_id,
            "scores": dict(self.scores),
            "total_score": self.total_score,
            "rank": self.rank,
            "rationale": self.rationale,
            "evaluated_at": self.evaluated_at,
            "is_winner": self.is_winner(),
        }


# ---------------------------------------------------------------------------
# BidEvaluationCriterion — individual scoring criterion
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BidEvaluationCriterion:
    """A single weighted criterion for bid evaluation.

    Each criterion wraps a callable that maps a :class:`CompetitiveBid` to a
    raw score in [0, 1], plus a numeric weight.  The
    :class:`MultiCriterionEvaluator` collects a list of criteria, normalises
    their weights, and combines their scores into a total.

    Criterion scores are clamped to [0, 1] by :meth:`apply` before use, so
    raw evaluators need not perform clamping themselves.

    Attributes
    ----------
    name:
        Human-readable criterion name (used as key in
        :attr:`BidEvaluation.scores`).
    weight:
        Non-negative importance weight.  Zero-weight criteria are included
        for transparency but contribute nothing to the total.
    evaluator:
        Callable ``(CompetitiveBid) -> float`` that scores the bid on this
        criterion.  Should return a value in [0, 1]; values outside this
        range are clamped.
    description:
        Optional prose description of what this criterion measures.
    """

    name: str
    weight: float
    evaluator: Callable[[Any], float]
    description: str = ""

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def apply(self, bid: Any) -> float:
        """Apply this criterion's evaluator to *bid* and clamp the result.

        Parameters
        ----------
        bid:
            A :class:`~models.CompetitiveBid` (typed as ``Any`` to survive
            guarded import failures).

        Returns
        -------
        float
            Clamped score in [0, 1].
        """
        try:
            raw = float(self.evaluator(bid))
        except Exception:
            raw = 0.0
        return _clamp(raw, 0.0, 1.0)

    def weighted_score(self, bid: Any) -> float:
        """Return the criterion score multiplied by the weight.

        Parameters
        ----------
        bid:
            A :class:`~models.CompetitiveBid`.

        Returns
        -------
        float
            ``weight * apply(bid)``.
        """
        return self.weight * self.apply(bid)


# ---------------------------------------------------------------------------
# MultiCriterionEvaluator
# ---------------------------------------------------------------------------


class MultiCriterionEvaluator:
    """Multi-criterion evaluator that scores bids against registered criteria.

    This is the primary evaluation engine described in theory2.tex §46.3.
    It maintains an ordered list of :class:`BidEvaluationCriterion` objects
    and computes a weighted total score for each bid.  After evaluating all
    bids in a round, it assigns 1-based ranks.

    The default criteria (created by :meth:`standard_evaluator`) score bids
    on semantic quality, uncertainty, trust, and raw bid value.

    Attributes
    ----------
    criteria:
        Ordered list of :class:`BidEvaluationCriterion` objects.
    """

    def __init__(self, criteria: Optional[list[BidEvaluationCriterion]] = None) -> None:
        """Initialise with an optional list of criteria.

        Parameters
        ----------
        criteria:
            Initial criteria.  If ``None`` or empty, the evaluator starts
            with no criteria and must have at least one added before it can
            evaluate bids.
        """
        self.criteria: list[BidEvaluationCriterion] = list(criteria or [])

    # ------------------------------------------------------------------
    # Criterion management
    # ------------------------------------------------------------------

    def add_criterion(self, criterion: BidEvaluationCriterion) -> None:
        """Append a criterion to the evaluation list.

        Parameters
        ----------
        criterion:
            The criterion to add.
        """
        self.criteria.append(criterion)

    def weight_sum(self) -> float:
        """Return the sum of all criterion weights.

        Returns
        -------
        float
        """
        return sum(c.weight for c in self.criteria)

    def normalize_weights(self) -> None:
        """Normalise criterion weights in-place so that they sum to 1.0.

        After calling this method each criterion's ``weight`` attribute is
        replaced by ``weight / total_weight``.  If the total weight is zero
        all criteria are given equal weight ``1 / len(criteria)``.
        """
        if not self.criteria:
            return
        total = self.weight_sum()
        if total < _EPS:
            equal = 1.0 / len(self.criteria)
            for c in self.criteria:
                c.weight = equal
            return
        for c in self.criteria:
            c.weight = c.weight / total

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, bid: Any) -> BidEvaluation:
        """Score a single bid against all registered criteria.

        Parameters
        ----------
        bid:
            A :class:`~models.CompetitiveBid`.

        Returns
        -------
        BidEvaluation
            Evaluation result with per-criterion scores and total score.
            The ``rank`` field is left at 0 (unranked); call
            :meth:`evaluate_all` to get ranked results.
        """
        if not self.criteria:
            return BidEvaluation(
                bid_id=getattr(bid, "bid_id", "unknown"),
                scores={},
                total_score=0.0,
                rationale="No criteria registered",
            )
        total_weight = self.weight_sum()
        if total_weight < _EPS:
            total_weight = 1.0
        per_scores: dict[str, float] = {}
        weighted_sum = 0.0
        for criterion in self.criteria:
            raw = criterion.apply(bid)
            per_scores[criterion.name] = raw
            weighted_sum += criterion.weight * raw
        total = _clamp(weighted_sum / total_weight, 0.0, 1.0)
        top_criterion = max(self.criteria, key=lambda c: c.weight * per_scores.get(c.name, 0.0))
        rationale = (
            f"Highest-contributing criterion: {top_criterion.name!r} "
            f"(weight={top_criterion.weight:.3f}, score={per_scores.get(top_criterion.name, 0.0):.3f})"
        )
        return BidEvaluation(
            bid_id=getattr(bid, "bid_id", "unknown"),
            scores=per_scores,
            total_score=total,
            rationale=rationale,
        )

    def evaluate_all(self, bids: list[Any]) -> list[BidEvaluation]:
        """Score all *bids* and assign 1-based ranks.

        Evaluations are ordered by descending total score; ties are broken
        by lexicographic order of ``bid_id`` (alphabetically earlier
        ``bid_id`` wins).

        Parameters
        ----------
        bids:
            List of :class:`~models.CompetitiveBid` objects.

        Returns
        -------
        list[BidEvaluation]
            Evaluations in the same order as *bids* (not sorted), but with
            :attr:`~BidEvaluation.rank` fields filled in.
        """
        if not bids:
            return []
        raw_evaluations = [self.evaluate(bid) for bid in bids]
        # Sort by (total_score desc, bid_id asc) to break ties
        sorted_evals = sorted(
            enumerate(raw_evaluations),
            key=lambda x: (-x[1].total_score, getattr(bids[x[0]], "bid_id", "")),
        )
        # Build index from original position to rank
        rank_map: dict[int, int] = {}
        for rank_zero, (orig_idx, _) in enumerate(sorted_evals):
            rank_map[orig_idx] = rank_zero + 1
        # Reconstruct evaluations with ranks (frozen dataclass — rebuild)
        ranked: list[BidEvaluation] = []
        for orig_idx, ev in enumerate(raw_evaluations):
            ranked.append(
                BidEvaluation(
                    bid_id=ev.bid_id,
                    scores=ev.scores,
                    total_score=ev.total_score,
                    rank=rank_map[orig_idx],
                    rationale=ev.rationale,
                    evaluated_at=ev.evaluated_at,
                )
            )
        return ranked

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def standard_evaluator(cls) -> "MultiCriterionEvaluator":
        """Create a :class:`MultiCriterionEvaluator` with four standard criteria.

        The standard criteria are:

        1. **semantic_score** (weight 0.4) — directly uses the bid's
           ``semantic_score`` field.
        2. **inv_uncertainty** (weight 0.3) — ``1 - uncertainty``; rewards low
           uncertainty.
        3. **trust_ceiling** (weight 0.2) — directly uses ``trust_ceiling``.
        4. **norm_bid_value** (weight 0.1) — bid value normalised to [0, 1]
           by dividing by 100.0 (the default budget).  Values above 100 are
           clamped to 1.

        Returns
        -------
        MultiCriterionEvaluator
            Ready-to-use evaluator with normalised weights.
        """
        criteria = [
            BidEvaluationCriterion(
                name="semantic_score",
                weight=0.4,
                evaluator=lambda b: getattr(b, "semantic_score", 0.0),
                description="Semantic quality score declared by the bidder [0, 1].",
            ),
            BidEvaluationCriterion(
                name="inv_uncertainty",
                weight=0.3,
                evaluator=lambda b: 1.0 - getattr(b, "uncertainty", 1.0),
                description=(
                    "Inverted uncertainty; 1 − uncertainty rewards low epistemic "
                    "uncertainty in the bidder's self-assessment."
                ),
            ),
            BidEvaluationCriterion(
                name="trust_ceiling",
                weight=0.2,
                evaluator=lambda b: getattr(b, "trust_ceiling", 0.0),
                description="Trust ceiling declared by the bidder; bounded in [0, 1].",
            ),
            BidEvaluationCriterion(
                name="norm_bid_value",
                weight=0.1,
                evaluator=lambda b: _clamp(getattr(b, "bid_value", 0.0) / 100.0, 0.0, 1.0),
                description=(
                    "Normalised bid value (bid_value / 100), clamped to [0, 1].  "
                    "Provides a tiebreaker between semantically equivalent bids."
                ),
            ),
        ]
        evaluator = cls(criteria=criteria)
        evaluator.normalize_weights()
        return evaluator

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        names = [c.name for c in self.criteria]
        return f"MultiCriterionEvaluator(criteria={names!r})"


# ---------------------------------------------------------------------------
# ParetoFilter
# ---------------------------------------------------------------------------


class ParetoFilter:
    """Filter a list of bids to retain only Pareto-optimal members.

    The Pareto frontier described in theory2.tex §46.3 consists of bids for
    which no other bid simultaneously achieves a higher (or equal) semantic
    score *and* a lower (or equal) uncertainty with at least one strict
    improvement.  Bids on the frontier are the *admissible* candidates for
    the final selection.

    This filter is applied before the auction to prune clearly dominated bids
    early and reduce the evaluation load.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def filter(self, bids: list[Any]) -> list[Any]:
        """Return the Pareto-optimal subset of *bids*.

        A bid is Pareto-optimal iff no other bid in *bids* dominates it.
        Bids are evaluated on two objectives:

        * **semantic_score** — maximise.
        * **uncertainty** — minimise.

        Parameters
        ----------
        bids:
            Candidate bids to filter.

        Returns
        -------
        list
            Subset of *bids* that are not dominated by any other bid.
            Preserves the original order of non-dominated bids.
        """
        if not bids:
            return []
        pareto: list[Any] = []
        for candidate in bids:
            dominated = False
            for other in bids:
                if other is candidate:
                    continue
                if self.dominates(other, candidate):
                    dominated = True
                    break
            if not dominated:
                pareto.append(candidate)
        return pareto

    def dominates(self, a: Any, b: Any) -> bool:
        """Return True if bid *a* Pareto-dominates bid *b*.

        Bid *a* dominates *b* iff:

        * ``a.semantic_score >= b.semantic_score``
        * ``a.uncertainty <= b.uncertainty``
        * At least one of the above inequalities is strict.

        Parameters
        ----------
        a:
            The potentially dominant bid.
        b:
            The potentially dominated bid.

        Returns
        -------
        bool
            ``True`` iff *a* dominates *b*.
        """
        a_score = getattr(a, "semantic_score", 0.0)
        b_score = getattr(b, "semantic_score", 0.0)
        a_unc = getattr(a, "uncertainty", 1.0)
        b_unc = getattr(b, "uncertainty", 1.0)
        weakly_better_score = a_score >= b_score
        weakly_better_unc = a_unc <= b_unc
        strictly_better = (a_score > b_score) or (a_unc < b_unc)
        return weakly_better_score and weakly_better_unc and strictly_better


# ---------------------------------------------------------------------------
# BidRanker
# ---------------------------------------------------------------------------


class BidRanker:
    """Rank a list of bids using a configurable scoring function.

    Provides a simple, stateless ranking utility that wraps an arbitrary
    scoring callable.  If no scoring function is provided :meth:`default_score`
    is used, which combines ``semantic_score``, inverted ``uncertainty``, and
    ``trust_ceiling`` with fixed weights.

    Ties in score are broken by lexicographic order of ``bid_id``.

    Attributes
    ----------
    scoring_fn:
        Optional ``(CompetitiveBid) -> float`` scoring function.
    """

    def __init__(
        self, scoring_fn: Optional[Callable[[Any], float]] = None
    ) -> None:
        """Initialise the ranker.

        Parameters
        ----------
        scoring_fn:
            Custom scoring function; defaults to :meth:`default_score`.
        """
        self.scoring_fn: Callable[[Any], float] = scoring_fn or self.default_score

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def rank(self, bids: list[Any]) -> list[tuple[int, Any]]:
        """Rank *bids* by descending score; ties broken by bid_id.

        Parameters
        ----------
        bids:
            List of :class:`~models.CompetitiveBid` objects.

        Returns
        -------
        list[tuple[int, CompetitiveBid]]
            List of ``(rank, bid)`` pairs in rank order (rank 1 first).
        """
        if not bids:
            return []
        scored = [
            (self.scoring_fn(b), getattr(b, "bid_id", ""), b) for b in bids
        ]
        # Sort by score desc, then bid_id asc for deterministic tie-breaking
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(i + 1, item[2]) for i, item in enumerate(scored)]

    def top_k(self, bids: list[Any], k: int) -> list[Any]:
        """Return the top-*k* bids by score.

        Parameters
        ----------
        bids:
            Candidate bids.
        k:
            Number of bids to return.

        Returns
        -------
        list
            The top-*k* bids in descending score order.
        """
        k = max(0, k)
        ranked = self.rank(bids)
        return [bid for _, bid in ranked[:k]]

    @staticmethod
    def default_score(bid: Any) -> float:
        """Compute a default composite score for *bid*.

        The default score is:

        .. math::

            0.5 \\cdot \\text{semantic\\_score}
            + 0.3 \\cdot (1 - \\text{uncertainty})
            + 0.2 \\cdot \\text{trust\\_ceiling}

        All components are taken from the bid's attributes; missing attributes
        default to 0.

        Parameters
        ----------
        bid:
            A :class:`~models.CompetitiveBid`.

        Returns
        -------
        float
            Composite score in [0, 1].
        """
        ss = _clamp(getattr(bid, "semantic_score", 0.0), 0.0, 1.0)
        unc = _clamp(getattr(bid, "uncertainty", 1.0), 0.0, 1.0)
        tc = _clamp(getattr(bid, "trust_ceiling", 0.0), 0.0, 1.0)
        return 0.5 * ss + 0.3 * (1.0 - unc) + 0.2 * tc


# ---------------------------------------------------------------------------
# BidAuction
# ---------------------------------------------------------------------------


class BidAuction:
    """Multi-round elimination auction over a set of bids.

    The auction runs up to :attr:`rounds` evaluation-and-elimination cycles.
    In each cycle the :class:`MultiCriterionEvaluator` scores all remaining
    bids, and the bottom :attr:`elimination_fraction` are eliminated.  The
    cycle repeats until one bid remains or the round limit is reached.

    This implements the *competitive search* mechanism of theory2.tex §46.3
    where the fleet converges toward the best admissible future through
    iterated elimination.

    Attributes
    ----------
    rounds:
        Maximum number of elimination rounds.
    elimination_fraction:
        Fraction of remaining bids to eliminate per round (in (0, 1)).
    evaluator:
        The :class:`MultiCriterionEvaluator` used to score bids each round.
    _history:
        Internal list of per-round dictionaries capturing the state at each
        elimination step.
    """

    def __init__(
        self,
        rounds: int = DEFAULT_AUCTION_ROUNDS,
        elimination_fraction: float = DEFAULT_ELIMINATION_FRACTION,
        evaluator: Optional[MultiCriterionEvaluator] = None,
    ) -> None:
        """Initialise the auction.

        Parameters
        ----------
        rounds:
            Maximum elimination rounds; clamped to at least 1.
        elimination_fraction:
            Fraction of bids to drop per round; clamped to (0, 1).
        evaluator:
            Custom evaluator; defaults to
            :meth:`MultiCriterionEvaluator.standard_evaluator`.
        """
        self.rounds: int = max(1, rounds)
        self.elimination_fraction: float = _clamp(elimination_fraction, 0.01, 0.99)
        self.evaluator: MultiCriterionEvaluator = (
            evaluator if evaluator is not None else MultiCriterionEvaluator.standard_evaluator()
        )
        self._history: list[dict[str, Any]] = []

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return the auction round history (read-only list).

        Returns
        -------
        list[dict]
            Each element describes one elimination round: ``round_number``,
            ``bids_before``, ``bids_after``, ``eliminated_bid_ids``,
            ``top_score``.
        """
        return list(self._history)

    # ------------------------------------------------------------------
    # Core auction logic
    # ------------------------------------------------------------------

    def run(
        self, bids: list[Any]
    ) -> tuple[Optional[Any], list[Any]]:
        """Run the auction and return the winner plus runners-up.

        The auction proceeds as follows:

        1. Apply the :class:`ParetoFilter` to the full bid set.
        2. For each elimination round (up to :attr:`rounds`):

           a. Evaluate all remaining bids with :attr:`evaluator`.
           b. Eliminate the bottom :attr:`elimination_fraction`.
           c. If one bid remains, stop early.

        3. Among the surviving bids, return the top-ranked as winner.

        Parameters
        ----------
        bids:
            The full list of candidate bids.

        Returns
        -------
        tuple[CompetitiveBid | None, list[CompetitiveBid]]
            ``(winner, runners_up)`` where ``runners_up`` is the list of
            surviving non-winning bids at the end of the auction.  If *bids*
            is empty, returns ``(None, [])``.
        """
        self._history = []
        if not bids:
            return None, []
        # Step 1: Pareto pre-filter
        pareto = ParetoFilter().filter(bids)
        remaining = pareto if pareto else list(bids)
        # Step 2: Elimination rounds
        for round_num in range(1, self.rounds + 1):
            if len(remaining) <= 1:
                break
            evaluations = self.evaluator.evaluate_all(remaining)
            eliminated_bids = self._elimination_round(remaining, evaluations)
            eliminated_ids = {getattr(b, "bid_id", "") for b in eliminated_bids}
            surviving = [b for b in remaining if getattr(b, "bid_id", "") not in eliminated_ids]
            top_eval = max(evaluations, key=lambda e: e.total_score, default=None)
            self._history.append(
                {
                    "round_number": round_num,
                    "bids_before": len(remaining),
                    "bids_after": len(surviving),
                    "eliminated_bid_ids": list(eliminated_ids),
                    "top_score": top_eval.total_score if top_eval else 0.0,
                }
            )
            remaining = surviving
        # Step 3: Select winner from survivors
        if not remaining:
            return None, []
        ranker = BidRanker()
        ranked = ranker.rank(remaining)
        winner_bid = ranked[0][1]
        runners_up = [b for _, b in ranked[1:]]
        return winner_bid, runners_up

    def _elimination_round(
        self,
        bids: list[Any],
        evaluations: list[BidEvaluation],
    ) -> list[Any]:
        """Determine which bids to eliminate in one round.

        Eliminations remove the bottom :attr:`elimination_fraction` of bids
        by total score.  At least one bid is always eliminated per round
        (unless only one bid remains).

        Parameters
        ----------
        bids:
            Current surviving bids.
        evaluations:
            Corresponding evaluations (same order as *bids*).

        Returns
        -------
        list
            Bids to eliminate this round.
        """
        if len(bids) <= 1:
            return []
        n_eliminate = max(1, int(math.floor(len(bids) * self.elimination_fraction)))
        # Build (score, bid_id, bid) triples to sort
        scored = [
            (evaluations[i].total_score, getattr(bids[i], "bid_id", ""), bids[i])
            for i in range(len(bids))
        ]
        # Sort ascending by score, then descending by bid_id (worst first)
        scored.sort(key=lambda x: (x[0], -ord(x[1][0]) if x[1] else 0))
        return [item[2] for item in scored[:n_eliminate]]


# ---------------------------------------------------------------------------
# EvaluationHistory
# ---------------------------------------------------------------------------


class EvaluationHistory:
    """Per-member longitudinal store of :class:`BidEvaluation` results.

    The evaluation history accumulates results as fleet rounds complete and
    provides aggregate statistics — average score, win rate, recent score
    trend — that feed back into the :class:`~models.CalibrationTrace` and
    scheduler.

    A circular-buffer-style max_size bound prevents unbounded memory growth
    in long-running fleets: once the total record count reaches
    :attr:`max_size`, the oldest record for each member is evicted.

    Attributes
    ----------
    max_size:
        Maximum total number of evaluation records to retain across all
        members.
    _store:
        Internal mapping from ``member_id`` to list of
        :class:`BidEvaluation`.
    _total_count:
        Running total of records ever recorded (not capped).
    """

    def __init__(self, max_size: int = DEFAULT_HISTORY_MAX_SIZE) -> None:
        """Initialise an empty evaluation history.

        Parameters
        ----------
        max_size:
            Maximum total records.  When this limit is reached the oldest
            record for the member with the most records is evicted.
        """
        self.max_size: int = max(1, max_size)
        self._store: dict[str, list[BidEvaluation]] = defaultdict(list)
        self._total_count: int = 0

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def record(self, member_id: str, evaluation: BidEvaluation) -> None:
        """Record an evaluation result for a fleet member.

        If the total number of stored evaluations would exceed :attr:`max_size`,
        the oldest record for the member with the most records is evicted first.

        Parameters
        ----------
        member_id:
            Identifier of the fleet member.
        evaluation:
            The :class:`BidEvaluation` to record.
        """
        # Evict if needed
        current_total = sum(len(v) for v in self._store.values())
        if current_total >= self.max_size:
            busiest = max(self._store, key=lambda k: len(self._store[k]), default=None)
            if busiest and self._store[busiest]:
                self._store[busiest].pop(0)
        self._store[member_id].append(evaluation)
        self._total_count += 1

    # ------------------------------------------------------------------
    # Per-member retrieval
    # ------------------------------------------------------------------

    def history_for(self, member_id: str) -> list[BidEvaluation]:
        """Return all recorded evaluations for *member_id*.

        Parameters
        ----------
        member_id:
            The fleet member identifier.

        Returns
        -------
        list[BidEvaluation]
            Ordered list (oldest first); empty if no records exist.
        """
        return list(self._store.get(member_id, []))

    def average_score(self, member_id: str) -> float:
        """Return the mean total score over all recorded evaluations for a member.

        Parameters
        ----------
        member_id:
            The fleet member identifier.

        Returns
        -------
        float
            Mean total score; 0.0 if no records.
        """
        records = self._store.get(member_id, [])
        if not records:
            return 0.0
        return _safe_mean([r.total_score for r in records])

    def win_rate(self, member_id: str) -> float:
        """Return the fraction of evaluations in which this member had rank 1.

        Parameters
        ----------
        member_id:
            The fleet member identifier.

        Returns
        -------
        float
            Win rate in [0, 1]; 0.0 if no records.
        """
        records = self._store.get(member_id, [])
        if not records:
            return 0.0
        wins = sum(1 for r in records if r.is_winner())
        return wins / len(records)

    def recent_scores(self, member_id: str, n: int = 10) -> list[float]:
        """Return the *n* most recent total scores for a member.

        Parameters
        ----------
        member_id:
            The fleet member identifier.
        n:
            Number of recent scores to return.

        Returns
        -------
        list[float]
            Up to *n* most-recent total scores, latest last.
        """
        records = self._store.get(member_id, [])
        return [r.total_score for r in records[-n:]]

    # ------------------------------------------------------------------
    # Aggregate export
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        """Export the full evaluation history to a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``total_records``, ``member_count``, ``members`` (dict of
            per-member stats), ``max_size``.
        """
        members_summary: dict[str, Any] = {}
        for member_id, records in self._store.items():
            members_summary[member_id] = {
                "n_evaluations": len(records),
                "average_score": self.average_score(member_id),
                "win_rate": self.win_rate(member_id),
                "recent_scores": self.recent_scores(member_id),
                "best_score": max((r.total_score for r in records), default=0.0),
                "worst_score": min((r.total_score for r in records), default=0.0),
                "score_std": _safe_std([r.total_score for r in records]),
            }
        return {
            "max_size": self.max_size,
            "total_records_ever": self._total_count,
            "current_record_count": sum(len(v) for v in self._store.values()),
            "member_count": len(self._store),
            "members": members_summary,
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        total = sum(len(v) for v in self._store.values())
        return (
            f"EvaluationHistory("
            f"members={len(self._store)}, "
            f"records={total}, "
            f"max_size={self.max_size})"
        )
