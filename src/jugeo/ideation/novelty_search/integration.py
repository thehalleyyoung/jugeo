"""Integration layer for novelty_search with other jugeo packages – theory2.tex Ch57.

Bridges the novelty_search subsystem with:
  - jugeo.ideation.novelty (TheoremPortfolio, NoveltySearcher, etc.)
  - jugeo.ideation.ideas (Idea, IdeaPortfolio, IdeaEvaluator)
  - jugeo.evidence.trust (TrustLevel, TrustAlgebra)

Module layout::

    PortfolioNoveltyIntegrator  – integrates with TheoremPortfolio
    IdeaNoveltyScorer           – scores ideas using novelty subsystem
    TrustFilteredSearch         – filters search results by trust level
    FederationNoveltyBridge     – bridges with federation (stub-free)
    IntegratedNoveltyPipeline   – end-to-end pipeline

Design notes
------------
All classes are designed to be stateless with respect to the underlying idea
data; they hold configuration and caches only.  Side-effects on ``Idea``
objects are expressed by returning new instances (functional update).

Threading: none of these classes are thread-safe.  If you need concurrent
access, guard with an external lock.

Budget semantics: ``budget`` is treated as a maximum total ``cost`` sum over
the selected ideas (``GainProfile.cost``).  An idea with ``cost=0`` is always
within budget.  When budget is exhausted the pipeline halts selection early.

Trust-level ordering (weakest → strongest)::

    SPECULATIVE < PROVISIONAL < GROUNDED < VALIDATED < RETIRED

``RETIRED`` is intentionally at the end because a retired idea may still have
high historical trust evidence; callers that want to exclude retired ideas
should use ``max_trust=TrustStatus.VALIDATED``.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustPolicy, TrustAuditEntry, TrustAuditLog
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
    LifecycleStatus,
    EvaluationResult,
    IdeaGenerator,
    IdeaEvaluator,
    IdeaDependencyGraph,
    IdeaHistory,
    IdeaSerializer,
    IdeaDiagnostics,
    IdeaRefiner,
    IdeaLifecycle,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltyMetric as _NoveltyMetricBase,
    NoveltySearcher as _NoveltySearcherBase,
    TheoremPortfolio,
    PurposeAlignmentChecker,
    NoveltyFilter,
    SemanticDistanceModel,
    NoveltyHistory,
    NoveltyOptimizer,
    NoveltyDiagnostics,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        The clamped value.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.1)
    0.0
    >>> _clamp(0.5, 0.2, 0.8)
    0.5
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    The returned string uses the format ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.

    Returns
    -------
    str
        ISO-8601 UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenise *text* into a frozenset of lowercase word tokens.

    Punctuation is stripped; runs of non-alphanumeric characters are treated
    as delimiters.  Stop-words are *not* removed so that domain-specific
    short words (e.g. "not") are preserved.

    Parameters
    ----------
    text:
        Arbitrary natural-language string.

    Returns
    -------
    frozenset[str]
        Unique lowercase tokens.

    Examples
    --------
    >>> _tokenize("Hello, World!")
    frozenset({'hello', 'world'})
    """
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Jaccard(A, B) = |A ∩ B| / |A ∪ B|.  Returns 0.0 when both sets are
    empty (by convention).

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Jaccard similarity in [0, 1].
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _idea_tokens(idea: Idea) -> frozenset[str]:
    """Extract a combined token set from an idea's key text fields.

    Combines tokens from ``title``, ``purpose``, ``target_area``, and
    ``hypothesis`` to form a representative fingerprint.

    Parameters
    ----------
    idea:
        The idea whose text fields will be tokenised.

    Returns
    -------
    frozenset[str]
        Union of tokens from all relevant text fields.
    """
    parts = [
        idea.title or "",
        idea.purpose or "",
        idea.target_area or "",
        idea.hypothesis or "",
    ]
    combined = " ".join(parts)
    return _tokenize(combined)


def _trust_level_from_status(status: TrustStatus) -> TrustLevel:
    """Map a ``TrustStatus`` enum value to the corresponding ``TrustLevel``.

    The mapping is designed to align the idea-lifecycle notion of trust with
    the evidence-layer notion:

    +----------------+--------------------+
    | TrustStatus    | TrustLevel         |
    +================+====================+
    | SPECULATIVE    | COPILOT_SUGGESTED  |
    | PROVISIONAL    | ORACLE_PROPOSED    |
    | GROUNDED       | HUMAN_ATTESTED     |
    | VALIDATED      | RUNTIME_WITNESSED  |
    | RETIRED        | UNVERIFIED         |
    +----------------+--------------------+

    Parameters
    ----------
    status:
        The idea lifecycle trust status.

    Returns
    -------
    TrustLevel
        The corresponding evidence-layer trust level.

    Raises
    ------
    ValueError
        If *status* is not a recognised ``TrustStatus`` member.
    """
    mapping: dict[TrustStatus, TrustLevel] = {
        TrustStatus.SPECULATIVE: TrustLevel.COPILOT_SUGGESTED,
        TrustStatus.PROVISIONAL: TrustLevel.ORACLE_PROPOSED,
        TrustStatus.GROUNDED: TrustLevel.HUMAN_ATTESTED,
        TrustStatus.VALIDATED: TrustLevel.RUNTIME_WITNESSED,
        TrustStatus.RETIRED: TrustLevel.UNVERIFIED,
    }
    if status not in mapping:
        raise ValueError(f"Unknown TrustStatus: {status!r}")
    return mapping[status]


# ---------------------------------------------------------------------------
# PortfolioNoveltyIntegrator
# ---------------------------------------------------------------------------


class PortfolioNoveltyIntegrator:
    """Integrate ``TheoremPortfolio`` with idea novelty scoring.

    This class provides the bridge between the token-based ``TheoremPortfolio``
    (which tracks which token-sets have already been "claimed") and the higher-
    level ``Idea`` representation.

    Typical usage::

        integrator = PortfolioNoveltyIntegrator(purpose="automated theorem proving")
        tp = integrator.build_theorem_portfolio(existing_ideas)
        novel_ideas = integrator.filter_novel(candidate_ideas, existing_ideas)

    Parameters
    ----------
    purpose:
        Free-text description of the research purpose.  Used to weight
        purpose-alignment scores.
    novelty_threshold:
        Minimum composite novelty score for an idea to be considered novel.
        Ideas below this threshold are filtered out by :meth:`filter_novel`.
    """

    def __init__(self, purpose: str = "", novelty_threshold: float = 0.3) -> None:
        self.purpose = purpose
        self.novelty_threshold = _clamp(novelty_threshold)
        self._alignment_checker = PurposeAlignmentChecker(purpose=purpose)
        self._distance_model = SemanticDistanceModel()
        self._history = NoveltyHistory()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_theorem_portfolio(self, ideas: Sequence[Idea]) -> TheoremPortfolio:
        """Build a ``TheoremPortfolio`` from a sequence of existing ideas.

        Each idea is added to the portfolio using its :func:`_idea_tokens`
        fingerprint.  The portfolio can then be used to assess the novelty
        of new candidates.

        Parameters
        ----------
        ideas:
            Sequence of existing ideas that form the current portfolio.

        Returns
        -------
        TheoremPortfolio
            A populated portfolio ready for novelty queries.
        """
        tp = TheoremPortfolio()
        for idea in ideas:
            tokens = _idea_tokens(idea)
            tp.add(idea.idea_id, tokens)
        return tp

    def score_against_portfolio(
        self,
        candidate: Idea,
        theorem_portfolio: TheoremPortfolio,
    ) -> NoveltyScore:
        """Compute a ``NoveltyScore`` for *candidate* against *theorem_portfolio*.

        The score is assembled from three components:

        1. **Semantic distance** – derived from
           ``TheoremPortfolio.novelty_score``, which measures how far the
           candidate's token-set is from all existing portfolio entries.
        2. **Purpose alignment** – computed by ``PurposeAlignmentChecker``
           against ``self.purpose``.
        3. **Feasibility** – estimated from the candidate's ``GainProfile``
           by normalising ``theorem_yield / (theorem_yield + uncertainty)``.

        Parameters
        ----------
        candidate:
            The idea whose novelty we want to measure.
        theorem_portfolio:
            The portfolio against which novelty is assessed.

        Returns
        -------
        NoveltyScore
            Full novelty breakdown.
        """
        tokens = _idea_tokens(candidate)
        all_existing = theorem_portfolio.all_tokens()

        # Semantic distance: use TheoremPortfolio if it has entries, else 1.0
        if all_existing:
            raw_distance = theorem_portfolio.novelty_score(tokens)
        else:
            raw_distance = 1.0
        semantic_distance = _clamp(float(raw_distance))

        # Purpose alignment via checker
        alignment_raw = self._alignment_checker.check(
            idea_text=" ".join([
                candidate.title or "",
                candidate.hypothesis or "",
                candidate.target_area or "",
            ])
        )
        purpose_alignment = _clamp(float(alignment_raw))

        # Feasibility from GainProfile
        gp = candidate.predicted_gain
        if gp is not None:
            ty = max(0.0, float(gp.theorem_yield))
            unc = max(0.0, float(gp.uncertainty))
            feasibility = _clamp(ty / (ty + unc + 1e-9))
        else:
            feasibility = 0.5

        return NoveltyScore(
            semantic_distance=semantic_distance,
            purpose_alignment=purpose_alignment,
            feasibility=feasibility,
        )

    def score_all(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[Idea, NoveltyScore]]:
        """Score every candidate against the given portfolio.

        Parameters
        ----------
        candidates:
            Ideas to score.
        portfolio:
            Existing ideas forming the baseline portfolio.

        Returns
        -------
        list[tuple[Idea, NoveltyScore]]
            Pairs of (idea, novelty_score) in the same order as *candidates*.
        """
        tp = self.build_theorem_portfolio(portfolio)
        return [(idea, self.score_against_portfolio(idea, tp)) for idea in candidates]

    def filter_novel(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Keep only candidates whose composite novelty exceeds the threshold.

        Parameters
        ----------
        candidates:
            Ideas to filter.
        portfolio:
            Existing portfolio for comparison.

        Returns
        -------
        list[Idea]
            Subset of *candidates* that are sufficiently novel.
        """
        scored = self.score_all(candidates, portfolio)
        return [idea for idea, score in scored if score.composite >= self.novelty_threshold]

    def update_portfolio_with_ideas(
        self,
        theorem_portfolio: TheoremPortfolio,
        new_ideas: Sequence[Idea],
    ) -> TheoremPortfolio:
        """Add *new_ideas* to an existing theorem portfolio.

        Returns a **new** ``TheoremPortfolio`` instance that contains all
        entries from *theorem_portfolio* plus the new ideas.

        Parameters
        ----------
        theorem_portfolio:
            The existing portfolio.
        new_ideas:
            Ideas to add.

        Returns
        -------
        TheoremPortfolio
            Updated portfolio (new instance).
        """
        new_tp = TheoremPortfolio()
        # Re-add existing entries
        for idea_id, tokens in theorem_portfolio.all_tokens():
            new_tp.add(idea_id, tokens)
        # Add new ideas
        for idea in new_ideas:
            new_tp.add(idea.idea_id, _idea_tokens(idea))
        return new_tp

    def novelty_distribution(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> dict[str, float]:
        """Compute idea_id → composite novelty score for all candidates.

        Parameters
        ----------
        candidates:
            Ideas to evaluate.
        portfolio:
            Portfolio for comparison.

        Returns
        -------
        dict[str, float]
            Mapping from idea_id to composite novelty score.
        """
        scored = self.score_all(candidates, portfolio)
        return {idea.idea_id: score.composite for idea, score in scored}

    def portfolio_coverage_ratio(
        self,
        idea_portfolio: IdeaPortfolio,
        theorem_portfolio: TheoremPortfolio,
    ) -> float:
        """Compute the fraction of *idea_portfolio* already present in *theorem_portfolio*.

        An idea is considered "covered" if its token set intersects with any
        entry in the theorem portfolio above the novelty threshold (i.e. it
        would *not* be filtered as novel).

        Parameters
        ----------
        idea_portfolio:
            The IdeaPortfolio whose coverage we measure.
        theorem_portfolio:
            The TheoremPortfolio acting as the existing knowledge base.

        Returns
        -------
        float
            Coverage ratio in [0, 1].  Returns 0.0 for an empty portfolio.
        """
        ideas = idea_portfolio.ideas
        if not ideas:
            return 0.0
        covered = 0
        for idea in ideas:
            tokens = _idea_tokens(idea)
            score = theorem_portfolio.novelty_score(tokens)
            # Low novelty_score means it IS covered (similar to existing)
            if float(score) < self.novelty_threshold:
                covered += 1
        return covered / len(ideas)

    def recommend_exploration(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        k: int = 5,
    ) -> list[Idea]:
        """Return the top-*k* most novel candidates for exploration.

        Candidates are ranked by composite novelty score (descending) and
        the top *k* are returned.

        Parameters
        ----------
        candidates:
            Pool of candidate ideas.
        portfolio:
            Existing portfolio.
        k:
            Number of ideas to recommend.

        Returns
        -------
        list[Idea]
            Up to *k* ideas with the highest novelty.
        """
        scored = self.score_all(candidates, portfolio)
        scored.sort(key=lambda t: t[1].composite, reverse=True)
        return [idea for idea, _ in scored[:k]]

    def diagnostics(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> dict[str, Any]:
        """Collect diagnostic information about the novelty distribution.

        Returns a dictionary with the following keys:

        - ``n_candidates``: number of candidate ideas evaluated.
        - ``n_portfolio``: size of the existing portfolio.
        - ``threshold``: current novelty threshold.
        - ``purpose``: the purpose string.
        - ``mean_novelty``: mean composite novelty across all candidates.
        - ``median_novelty``: median composite novelty.
        - ``novel_count``: number of candidates above the threshold.
        - ``novel_fraction``: fraction above the threshold.
        - ``min_novelty``, ``max_novelty``: range.
        - ``score_breakdown``: per-idea breakdown (id → dict).
        """
        scored = self.score_all(candidates, portfolio)
        composites = [s.composite for _, s in scored]
        novel_count = sum(1 for c in composites if c >= self.novelty_threshold)
        sorted_c = sorted(composites)
        n = len(sorted_c)
        mean_nov = sum(sorted_c) / n if n else 0.0
        median_nov: float
        if n == 0:
            median_nov = 0.0
        elif n % 2 == 1:
            median_nov = sorted_c[n // 2]
        else:
            median_nov = (sorted_c[n // 2 - 1] + sorted_c[n // 2]) / 2.0

        breakdown = {
            idea.idea_id: {
                "composite": score.composite,
                "semantic_distance": score.semantic_distance,
                "purpose_alignment": score.purpose_alignment,
                "feasibility": score.feasibility,
            }
            for idea, score in scored
        }

        return {
            "n_candidates": len(candidates),
            "n_portfolio": len(portfolio),
            "threshold": self.novelty_threshold,
            "purpose": self.purpose,
            "mean_novelty": mean_nov,
            "median_novelty": median_nov,
            "novel_count": novel_count,
            "novel_fraction": novel_count / n if n else 0.0,
            "min_novelty": min(composites) if composites else 0.0,
            "max_novelty": max(composites) if composites else 0.0,
            "score_breakdown": breakdown,
        }


# ---------------------------------------------------------------------------
# IdeaNoveltyScorer
# ---------------------------------------------------------------------------


class IdeaNoveltyScorer:
    """Score ideas using the novelty subsystem, with optional IdeaEvaluator support.

    This class provides a high-level interface for computing novelty scores
    against an existing portfolio.  When an ``IdeaEvaluator`` is provided,
    it can produce full ``EvaluationResult`` objects alongside novelty scores.

    Parameters
    ----------
    purpose:
        Research purpose string, used for alignment scoring.
    evaluator:
        Optional ``IdeaEvaluator`` for full evaluation support.
    """

    def __init__(self, purpose: str = "", evaluator: IdeaEvaluator | None = None) -> None:
        self.purpose = purpose
        self.evaluator = evaluator
        self._integrator = PortfolioNoveltyIntegrator(purpose=purpose)
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------

    def score(self, idea: Idea, portfolio: Sequence[Idea]) -> float:
        """Compute the scalar composite novelty of *idea* against *portfolio*.

        Parameters
        ----------
        idea:
            Idea to score.
        portfolio:
            Existing ideas to compare against.

        Returns
        -------
        float
            Composite novelty score in [0, 1].
        """
        return self.score_full(idea, portfolio).composite

    def score_full(self, idea: Idea, portfolio: Sequence[Idea]) -> NoveltyScore:
        """Compute the full ``NoveltyScore`` breakdown for *idea*.

        Parameters
        ----------
        idea:
            Idea to score.
        portfolio:
            Existing portfolio.

        Returns
        -------
        NoveltyScore
            Full score with semantic_distance, purpose_alignment, feasibility.
        """
        tp = self._integrator.build_theorem_portfolio(portfolio)
        return self._integrator.score_against_portfolio(idea, tp)

    def score_portfolio(self, portfolio: IdeaPortfolio) -> dict[str, float]:
        """Score every idea in *portfolio* against all others.

        Each idea is scored against the rest of the portfolio (leave-one-out).

        Parameters
        ----------
        portfolio:
            The IdeaPortfolio to score internally.

        Returns
        -------
        dict[str, float]
            Mapping from idea_id to composite novelty score.
        """
        ideas = list(portfolio.ideas)
        result: dict[str, float] = {}
        for i, idea in enumerate(ideas):
            others = [x for j, x in enumerate(ideas) if j != i]
            result[idea.idea_id] = self.score(idea, others)
        return result

    def rerank_portfolio(self, portfolio: IdeaPortfolio) -> list[Idea]:
        """Rerank the ideas in *portfolio* by internal novelty (descending).

        Parameters
        ----------
        portfolio:
            IdeaPortfolio to rerank.

        Returns
        -------
        list[Idea]
            Ideas sorted by novelty score, most novel first.
        """
        scores = self.score_portfolio(portfolio)
        ideas = list(portfolio.ideas)
        ideas.sort(key=lambda idea: scores.get(idea.idea_id, 0.0), reverse=True)
        return ideas

    def evaluate_and_score(
        self,
        idea: Idea,
        portfolio: Sequence[Idea],
    ) -> tuple[float, EvaluationResult]:
        """Compute novelty and run full evaluation via the embedded IdeaEvaluator.

        If no ``IdeaEvaluator`` was provided at construction time, this method
        raises ``RuntimeError``.

        Parameters
        ----------
        idea:
            Idea to evaluate.
        portfolio:
            Existing portfolio.

        Returns
        -------
        tuple[float, EvaluationResult]
            (novelty_score, evaluation_result) pair.

        Raises
        ------
        RuntimeError
            If ``self.evaluator`` is ``None``.
        """
        if self.evaluator is None:
            raise RuntimeError(
                "IdeaNoveltyScorer.evaluate_and_score requires an IdeaEvaluator. "
                "Pass evaluator= at construction time."
            )
        novelty = self.score(idea, portfolio)
        evaluation = self.evaluator.evaluate(idea)
        return novelty, evaluation

    def batch_score(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[Idea, float]]:
        """Score all candidates and return sorted by novelty descending.

        Parameters
        ----------
        candidates:
            Ideas to score.
        portfolio:
            Portfolio for comparison.

        Returns
        -------
        list[tuple[Idea, float]]
            (idea, novelty) pairs sorted by novelty descending.
        """
        tp = self._integrator.build_theorem_portfolio(portfolio)
        pairs = [
            (idea, self._integrator.score_against_portfolio(idea, tp).composite)
            for idea in candidates
        ]
        pairs.sort(key=lambda t: t[1], reverse=True)
        return pairs

    def update_idea_novelty(self, idea: Idea, portfolio: Sequence[Idea]) -> Idea:
        """Return a new Idea with the ``novelty_score`` field updated.

        Computes the composite novelty of *idea* against *portfolio* and
        returns a new ``Idea`` instance with ``novelty_score`` set to the
        result.

        Parameters
        ----------
        idea:
            Original idea.
        portfolio:
            Portfolio for comparison.

        Returns
        -------
        Idea
            New Idea instance with updated ``novelty_score``.
        """
        score = self.score(idea, portfolio)
        return Idea(
            idea_id=idea.idea_id,
            title=idea.title,
            purpose=idea.purpose,
            target_area=idea.target_area,
            hypothesis=idea.hypothesis,
            predicted_gain=idea.predicted_gain,
            novelty_score=score,
            validation_plan=idea.validation_plan,
            trust_status=idea.trust_status,
        )

    def novelty_trend(
        self,
        portfolio_history: Sequence[Sequence[Idea]],
    ) -> list[float]:
        """Compute the mean portfolio novelty at each historical snapshot.

        Each snapshot in *portfolio_history* is a sequence of ideas
        representing the portfolio at a given point in time.  Novelty for
        each snapshot is computed against the *previous* snapshot.

        Parameters
        ----------
        portfolio_history:
            Ordered sequence of portfolio snapshots (oldest first).

        Returns
        -------
        list[float]
            Mean novelty at each snapshot.  The first snapshot always has
            novelty 1.0 (nothing to compare against).
        """
        if not portfolio_history:
            return []
        trend: list[float] = [1.0]  # first snapshot is trivially novel
        for i in range(1, len(portfolio_history)):
            current = list(portfolio_history[i])
            previous = list(portfolio_history[i - 1])
            if not current:
                trend.append(0.0)
                continue
            tp = self._integrator.build_theorem_portfolio(previous)
            scores = [
                self._integrator.score_against_portfolio(idea, tp).composite
                for idea in current
            ]
            trend.append(sum(scores) / len(scores))
        return trend

    def explain(self, idea: Idea, portfolio: Sequence[Idea]) -> str:
        """Generate a human-readable explanation of the novelty score.

        Parameters
        ----------
        idea:
            Idea to explain.
        portfolio:
            Portfolio used for comparison.

        Returns
        -------
        str
            Multi-line explanation.
        """
        ns = self.score_full(idea, portfolio)
        lines = [
            f"Novelty explanation for idea '{idea.title}' [{idea.idea_id}]",
            "=" * 60,
            f"  Composite score   : {ns.composite:.4f}",
            f"  Semantic distance : {ns.semantic_distance:.4f}  "
            f"(how far this idea is from existing portfolio ideas)",
            f"  Purpose alignment : {ns.purpose_alignment:.4f}  "
            f"(how well this idea aligns with the stated purpose)",
            f"  Feasibility       : {ns.feasibility:.4f}  "
            f"(estimated likelihood of producing useful results)",
            "",
            f"  Portfolio size    : {len(portfolio)} ideas",
            f"  Purpose           : {self.purpose or '(none)'}",
            "",
        ]
        if ns.composite >= 0.7:
            lines.append("  Assessment: HIGHLY NOVEL – strong candidate for exploration.")
        elif ns.composite >= 0.4:
            lines.append("  Assessment: MODERATELY NOVEL – worth considering.")
        else:
            lines.append("  Assessment: LOW NOVELTY – similar ideas already in portfolio.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TrustFilteredSearch
# ---------------------------------------------------------------------------


class TrustFilteredSearch:
    """Search and filter ideas by trust level before applying novelty ranking.

    This class composes trust-level filtering with novelty-based search.
    It ensures that only ideas within the specified trust range are considered,
    and it records audit information for every filtering decision.

    Parameters
    ----------
    min_trust:
        Minimum trust status (inclusive).  Ideas with lower trust are excluded.
    max_trust:
        Maximum trust status (inclusive).  ``None`` means no upper bound.
    algebra:
        Optional ``TrustAlgebra`` for composed trust reasoning.
    """

    _ORDINALS: dict[TrustStatus, int] = {
        TrustStatus.SPECULATIVE: 0,
        TrustStatus.PROVISIONAL: 1,
        TrustStatus.GROUNDED: 2,
        TrustStatus.VALIDATED: 3,
        TrustStatus.RETIRED: 4,
    }

    def __init__(
        self,
        min_trust: TrustStatus = TrustStatus.PROVISIONAL,
        max_trust: TrustStatus | None = None,
        algebra: TrustAlgebra | None = None,
    ) -> None:
        self.min_trust = min_trust
        self.max_trust = max_trust
        self.algebra = algebra
        self._audit: list[dict[str, Any]] = []
        self._integrator = PortfolioNoveltyIntegrator()

    # ------------------------------------------------------------------

    def _trust_ordinal(self, status: TrustStatus) -> int:
        """Map *status* to an integer for ordinal comparison.

        Parameters
        ----------
        status:
            Trust status to convert.

        Returns
        -------
        int
            Ordinal value; higher means more trusted.
        """
        return self._ORDINALS.get(status, -1)

    def filter(self, candidates: Sequence[Idea]) -> list[Idea]:
        """Keep only ideas within the configured trust range.

        Parameters
        ----------
        candidates:
            Ideas to filter.

        Returns
        -------
        list[Idea]
            Filtered ideas.
        """
        result: list[Idea] = []
        min_ord = self._trust_ordinal(self.min_trust)
        max_ord = self._trust_ordinal(self.max_trust) if self.max_trust else 999

        for idea in candidates:
            ord_val = self._trust_ordinal(idea.trust_status)
            if min_ord <= ord_val <= max_ord:
                result.append(idea)
                self._audit.append({
                    "idea_id": idea.idea_id,
                    "action": "PASS",
                    "trust_status": idea.trust_status.value,
                    "ordinal": ord_val,
                    "timestamp": _now_iso(),
                })
            else:
                direction = "too low" if ord_val < min_ord else "too high"
                self._audit.append({
                    "idea_id": idea.idea_id,
                    "action": "REJECT",
                    "reason": f"trust {direction}",
                    "trust_status": idea.trust_status.value,
                    "ordinal": ord_val,
                    "timestamp": _now_iso(),
                })
        return result

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Filter by trust, then run greedy novelty search.

        Parameters
        ----------
        candidates:
            Candidate ideas.
        portfolio:
            Existing portfolio.
        purpose:
            Optional purpose for novelty alignment scoring.

        Returns
        -------
        list[Idea]
            Trust-filtered, novelty-ranked ideas.
        """
        filtered = self.filter(candidates)
        if not filtered:
            return []
        integrator = PortfolioNoveltyIntegrator(purpose=purpose)
        tp = integrator.build_theorem_portfolio(portfolio)
        scored = [(idea, integrator.score_against_portfolio(idea, tp).composite) for idea in filtered]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [idea for idea, _ in scored]

    def stratified_search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, list[Idea]]:
        """Search independently within each trust stratum.

        Parameters
        ----------
        candidates:
            All candidate ideas.
        portfolio:
            Existing portfolio.
        purpose:
            Optional purpose for alignment scoring.

        Returns
        -------
        dict[str, list[Idea]]
            Mapping from TrustStatus name to ranked ideas in that stratum.
        """
        strata: dict[TrustStatus, list[Idea]] = defaultdict(list)
        for idea in candidates:
            strata[idea.trust_status].append(idea)

        integrator = PortfolioNoveltyIntegrator(purpose=purpose)
        tp = integrator.build_theorem_portfolio(portfolio)

        result: dict[str, list[Idea]] = {}
        for status, stratum_ideas in strata.items():
            scored = [
                (idea, integrator.score_against_portfolio(idea, tp).composite)
                for idea in stratum_ideas
            ]
            scored.sort(key=lambda t: t[1], reverse=True)
            result[status.name] = [idea for idea, _ in scored]
        return result

    def trust_novelty_tradeoff(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[Idea, float, TrustStatus]]:
        """Compute (idea, novelty, trust_status) triples sorted by novelty.

        Parameters
        ----------
        candidates:
            Ideas to analyse.
        portfolio:
            Portfolio for novelty comparison.

        Returns
        -------
        list[tuple[Idea, float, TrustStatus]]
            Triples sorted by novelty score descending.
        """
        integrator = PortfolioNoveltyIntegrator()
        tp = integrator.build_theorem_portfolio(portfolio)
        triples = [
            (idea, integrator.score_against_portfolio(idea, tp).composite, idea.trust_status)
            for idea in candidates
        ]
        triples.sort(key=lambda t: t[1], reverse=True)
        return triples

    def elevate_trustworthy(
        self,
        result: list[Idea],
        threshold: float = 0.7,
    ) -> list[Idea]:
        """Prioritise high-trust ideas among those above a novelty threshold.

        Ideas above *threshold* novelty are sorted by trust (most trusted
        first), while ideas below the threshold retain their original order.

        Parameters
        ----------
        result:
            Novelty-ranked ideas (from :meth:`search`).
        threshold:
            Novelty cutoff for the trust-elevation step.

        Returns
        -------
        list[Idea]
            Reordered ideas.
        """
        integrator = PortfolioNoveltyIntegrator()
        # Use a simple heuristic: ideas in result are already sorted by novelty.
        # We just re-sort the top group by trust ordinal (desc).
        above = [idea for idea in result if self._trust_ordinal(idea.trust_status) >= self._trust_ordinal(self.min_trust)]
        below = [idea for idea in result if idea not in above]
        above.sort(key=lambda x: self._trust_ordinal(x.trust_status), reverse=True)
        return above + below

    def audit_log(self) -> list[dict[str, Any]]:
        """Return the accumulated audit log of filter decisions.

        Returns
        -------
        list[dict[str, Any]]
            List of audit records, each with keys ``idea_id``, ``action``,
            ``trust_status``, ``timestamp``, and (for rejections) ``reason``.
        """
        return list(self._audit)

    def statistics(self, candidates: Sequence[Idea]) -> dict[str, Any]:
        """Compute trust distribution and filter statistics.

        Parameters
        ----------
        candidates:
            Ideas to analyse.

        Returns
        -------
        dict[str, Any]
            Dictionary with trust distribution, filter rates, etc.
        """
        total = len(candidates)
        dist: dict[str, int] = defaultdict(int)
        for idea in candidates:
            dist[idea.trust_status.name] += 1
        filtered = self.filter(list(candidates))
        return {
            "total": total,
            "passed": len(filtered),
            "rejected": total - len(filtered),
            "filter_rate": (total - len(filtered)) / total if total else 0.0,
            "min_trust": self.min_trust.name,
            "max_trust": self.max_trust.name if self.max_trust else None,
            "trust_distribution": dict(dist),
        }


# ---------------------------------------------------------------------------
# FederationNoveltyBridge
# ---------------------------------------------------------------------------


class FederationNoveltyBridge:
    """Bridge novelty search across a federation of idea sources.

    A *federation* is a collection of named ``IdeaPortfolio`` instances from
    different sources (e.g. different research groups, agents, or subsystems).
    This class aggregates them, enables cross-source novelty comparison, and
    supports federated search.

    Parameters
    ----------
    federation_name:
        Human-readable name for this federation.
    purpose:
        Common research purpose, used for alignment scoring.
    """

    def __init__(self, federation_name: str = "default", purpose: str = "") -> None:
        self.federation_name = federation_name
        self.purpose = purpose
        self._sources: dict[str, IdeaPortfolio] = {}
        self._integrator = PortfolioNoveltyIntegrator(purpose=purpose)

    # ------------------------------------------------------------------

    def register_source(self, source_id: str, portfolio: IdeaPortfolio) -> None:
        """Register a new idea source.

        Parameters
        ----------
        source_id:
            Unique identifier for the source.
        portfolio:
            The source's ``IdeaPortfolio``.
        """
        self._sources[source_id] = portfolio

    def unregister_source(self, source_id: str) -> bool:
        """Remove a source from the federation.

        Parameters
        ----------
        source_id:
            Source to remove.

        Returns
        -------
        bool
            ``True`` if the source was found and removed, ``False`` otherwise.
        """
        if source_id in self._sources:
            del self._sources[source_id]
            return True
        return False

    def merged_portfolio(self) -> list[Idea]:
        """Return all ideas from all sources as a flat list.

        Returns
        -------
        list[Idea]
            All federated ideas, deduplicated by ``idea_id``.
        """
        seen: set[str] = set()
        result: list[Idea] = []
        for portfolio in self._sources.values():
            for idea in portfolio.ideas:
                if idea.idea_id not in seen:
                    seen.add(idea.idea_id)
                    result.append(idea)
        return result

    def cross_source_novelty(self, candidate: Idea) -> dict[str, float]:
        """Compute novelty of *candidate* against each registered source.

        Parameters
        ----------
        candidate:
            The idea to evaluate.

        Returns
        -------
        dict[str, float]
            Mapping from source_id to composite novelty score.
        """
        result: dict[str, float] = {}
        for source_id, portfolio in self._sources.items():
            ideas = list(portfolio.ideas)
            tp = self._integrator.build_theorem_portfolio(ideas)
            score = self._integrator.score_against_portfolio(candidate, tp)
            result[source_id] = score.composite
        return result

    def federated_search(
        self,
        query_ideas: Sequence[Idea],
        k: int = 10,
        purpose: str = "",
    ) -> list[Idea]:
        """Search for the most cross-source-novel ideas from *query_ideas*.

        Each query idea is scored against the full merged portfolio.  The top
        *k* ideas by novelty are returned.

        Parameters
        ----------
        query_ideas:
            Candidate ideas from which to select.
        k:
            Number of results to return.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Top-*k* novel ideas.
        """
        integrator = PortfolioNoveltyIntegrator(purpose=purpose or self.purpose)
        merged = self.merged_portfolio()
        tp = integrator.build_theorem_portfolio(merged)
        scored = [
            (idea, integrator.score_against_portfolio(idea, tp).composite)
            for idea in query_ideas
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [idea for idea, _ in scored[:k]]

    def source_contribution(self) -> dict[str, int]:
        """Count ideas per source.

        Returns
        -------
        dict[str, int]
            Mapping from source_id to idea count.
        """
        return {sid: len(list(p.ideas)) for sid, p in self._sources.items()}

    def inter_source_diversity(self) -> float:
        """Compute mean pairwise distance between source centroids.

        Each source's "centroid" is represented by the union of its token
        sets.  Pairwise Jaccard distance between centroids is averaged.

        Returns
        -------
        float
            Mean inter-source diversity in [0, 1].  Returns 0.0 when fewer
            than two sources are registered.
        """
        source_ids = list(self._sources.keys())
        if len(source_ids) < 2:
            return 0.0
        centroids: dict[str, frozenset[str]] = {}
        for sid, portfolio in self._sources.items():
            all_tokens: set[str] = set()
            for idea in portfolio.ideas:
                all_tokens.update(_idea_tokens(idea))
            centroids[sid] = frozenset(all_tokens)

        distances: list[float] = []
        for i in range(len(source_ids)):
            for j in range(i + 1, len(source_ids)):
                a = centroids[source_ids[i]]
                b = centroids[source_ids[j]]
                sim = _jaccard(a, b)
                distances.append(1.0 - sim)  # distance = 1 - similarity

        return sum(distances) / len(distances) if distances else 0.0

    def diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information about the federation.

        Returns
        -------
        dict[str, Any]
            Dictionary with federation stats.
        """
        contribution = self.source_contribution()
        merged = self.merged_portfolio()
        return {
            "federation_name": self.federation_name,
            "purpose": self.purpose,
            "n_sources": len(self._sources),
            "source_ids": list(self._sources.keys()),
            "total_ideas": len(merged),
            "source_contribution": contribution,
            "inter_source_diversity": self.inter_source_diversity(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise federation metadata (not idea content) to a dictionary.

        Returns
        -------
        dict[str, Any]
            Serialisable representation.
        """
        return {
            "federation_name": self.federation_name,
            "purpose": self.purpose,
            "sources": {
                sid: {"idea_count": len(list(p.ideas))}
                for sid, p in self._sources.items()
            },
            "inter_source_diversity": self.inter_source_diversity(),
        }


# ---------------------------------------------------------------------------
# PipelineStage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineStage:
    """Metadata for a single stage in the integrated novelty pipeline.

    Parameters
    ----------
    stage_id:
        Unique identifier for this stage (e.g. ``"trust-filter"``).
    name:
        Human-readable name.
    enabled:
        Whether this stage is active.  Disabled stages are skipped.
    weight:
        Relative weight used to combine stage scores (not currently used
        in scoring but available for future weighting schemes).
    """

    stage_id: str
    name: str
    enabled: bool = True
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "stage_id": self.stage_id,
            "name": self.name,
            "enabled": self.enabled,
            "weight": self.weight,
        }

    def describe(self) -> str:
        """Return a one-line human-readable description.

        Returns
        -------
        str
        """
        status = "ENABLED" if self.enabled else "DISABLED"
        return f"[{self.stage_id}] {self.name} ({status}, weight={self.weight:.2f})"


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineResult:
    """Immutable record of the output from ``IntegratedNoveltyPipeline.run``.

    Parameters
    ----------
    result_id:
        Unique run identifier.
    results:
        Tuple of selected ideas.
    novelty_scores:
        Tuple of novelty scores (parallel to *results*).
    pipeline_stages:
        Tuple of stage names executed.
    purpose:
        The purpose for which the pipeline was run.
    duration_ms:
        Wall-clock time of the pipeline run in milliseconds.
    timestamp:
        ISO-8601 UTC timestamp of when the run completed.
    """

    result_id: str
    results: tuple[Idea, ...]
    novelty_scores: tuple[float, ...]
    pipeline_stages: tuple[str, ...]
    purpose: str
    duration_ms: float
    timestamp: str = field(default_factory=_now_iso)

    @property
    def count(self) -> int:
        """Number of results returned."""
        return len(self.results)

    @property
    def mean_novelty(self) -> float:
        """Mean novelty score across all results.  Returns 0.0 if empty."""
        if not self.novelty_scores:
            return 0.0
        return sum(self.novelty_scores) / len(self.novelty_scores)

    @property
    def top_result(self) -> Idea | None:
        """The highest-novelty idea, or ``None`` if no results."""
        return self.results[0] if self.results else None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the result to a dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "result_id": self.result_id,
            "results": [
                {
                    "idea_id": idea.idea_id,
                    "title": idea.title,
                    "novelty": self.novelty_scores[i] if i < len(self.novelty_scores) else None,
                }
                for i, idea in enumerate(self.results)
            ],
            "pipeline_stages": list(self.pipeline_stages),
            "purpose": self.purpose,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "count": self.count,
            "mean_novelty": self.mean_novelty,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PipelineResult":
        """Reconstruct a ``PipelineResult`` from a dictionary.

        Note: This cannot reconstruct full ``Idea`` objects; the ``results``
        field will be empty.  Use this only for metadata recovery.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PipelineResult
        """
        return cls(
            result_id=d.get("result_id", str(uuid.uuid4())),
            results=(),
            novelty_scores=tuple(float(r.get("novelty") or 0.0) for r in d.get("results", [])),
            pipeline_stages=tuple(d.get("pipeline_stages", [])),
            purpose=d.get("purpose", ""),
            duration_ms=float(d.get("duration_ms", 0.0)),
            timestamp=d.get("timestamp", _now_iso()),
        )

    def summary(self) -> str:
        """Return a concise one-line summary string.

        Returns
        -------
        str
        """
        top = self.top_result
        top_str = f"'{top.title}'" if top else "none"
        return (
            f"PipelineResult[{self.result_id[:8]}] "
            f"count={self.count} mean_novelty={self.mean_novelty:.3f} "
            f"top={top_str} duration={self.duration_ms:.1f}ms"
        )


# ---------------------------------------------------------------------------
# IntegratedNoveltyPipeline
# ---------------------------------------------------------------------------


class IntegratedNoveltyPipeline:
    """End-to-end pipeline combining trust filtering, novelty scoring, and diversity selection.

    Pipeline stages (in order):

    1. **trust-filter**   – discard ideas below ``min_trust``.
    2. **build-portfolio** – construct ``TheoremPortfolio`` from existing ideas.
    3. **novelty-score**  – score each candidate; keep those above threshold.
    4. **greedy-diversity** – iteratively select the most novel remaining idea.
    5. **budget-cap**     – halt when cumulative cost exceeds ``budget``.

    Parameters
    ----------
    purpose:
        Research purpose, passed to all sub-components.
    budget:
        Maximum cumulative idea cost (``GainProfile.cost``).
    k:
        Maximum number of ideas to select.
    min_trust:
        Minimum trust status for trust-filter stage.
    novelty_threshold:
        Minimum composite novelty for novelty-score stage.
    """

    _DEFAULT_STAGES = [
        PipelineStage("trust-filter", "Trust Filter"),
        PipelineStage("build-portfolio", "Build Theorem Portfolio"),
        PipelineStage("novelty-score", "Novelty Scoring"),
        PipelineStage("greedy-diversity", "Greedy Diversity Selection"),
        PipelineStage("budget-cap", "Budget Cap"),
    ]

    def __init__(
        self,
        purpose: str,
        budget: float = 100.0,
        k: int = 10,
        min_trust: TrustStatus = TrustStatus.PROVISIONAL,
        novelty_threshold: float = 0.3,
    ) -> None:
        self.purpose = purpose
        self.budget = budget
        self.k = k
        self.min_trust = min_trust
        self.novelty_threshold = novelty_threshold

        self._integrator = PortfolioNoveltyIntegrator(purpose=purpose, novelty_threshold=novelty_threshold)
        self._scorer = IdeaNoveltyScorer(purpose=purpose)
        self._trust_search = TrustFilteredSearch(min_trust=min_trust)
        self._federation = FederationNoveltyBridge(purpose=purpose)

        self._stages: dict[str, PipelineStage] = {
            s.stage_id: s for s in self._DEFAULT_STAGES
        }
        self._run_history: list[PipelineResult] = []

    # ------------------------------------------------------------------

    def configure_stage(self, stage_name: str, enabled: bool) -> None:
        """Enable or disable a pipeline stage by name.

        Parameters
        ----------
        stage_name:
            The ``stage_id`` of the stage to configure.
        enabled:
            Whether the stage should run.

        Raises
        ------
        KeyError
            If *stage_name* does not match any known stage.
        """
        if stage_name not in self._stages:
            raise KeyError(f"Unknown pipeline stage: {stage_name!r}")
        old = self._stages[stage_name]
        self._stages[stage_name] = PipelineStage(
            stage_id=old.stage_id,
            name=old.name,
            enabled=enabled,
            weight=old.weight,
        )

    def run(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> PipelineResult:
        """Execute the full pipeline and return a ``PipelineResult``.

        Parameters
        ----------
        candidates:
            Pool of candidate ideas to select from.
        portfolio:
            Existing ideas forming the current portfolio.

        Returns
        -------
        PipelineResult
            The result of the pipeline run.
        """
        t0 = time.monotonic()
        executed_stages: list[str] = []
        working: list[Idea] = list(candidates)

        # Stage 1: trust filter
        if self._stages["trust-filter"].enabled:
            working = self._trust_search.filter(working)
            executed_stages.append("trust-filter")

        # Stage 2: build theorem portfolio
        tp: TheoremPortfolio | None = None
        if self._stages["build-portfolio"].enabled:
            tp = self._integrator.build_theorem_portfolio(portfolio)
            executed_stages.append("build-portfolio")
        else:
            tp = TheoremPortfolio()

        # Stage 3: novelty scoring
        scored: list[tuple[Idea, float]] = []
        if self._stages["novelty-score"].enabled:
            for idea in working:
                ns = self._integrator.score_against_portfolio(idea, tp)
                if ns.composite >= self.novelty_threshold:
                    scored.append((idea, ns.composite))
            executed_stages.append("novelty-score")
        else:
            scored = [(idea, 1.0) for idea in working]

        # Stage 4: greedy diversity selection
        selected: list[tuple[Idea, float]] = []
        if self._stages["greedy-diversity"].enabled:
            selected_set: set[str] = set()
            dynamic_portfolio = list(portfolio)
            for _ in range(self.k):
                if not scored:
                    break
                # Re-score against dynamic portfolio (already-selected ideas added)
                best_idea: Idea | None = None
                best_score = -1.0
                for idea, _ in scored:
                    if idea.idea_id in selected_set:
                        continue
                    ns = self._integrator.score_against_portfolio(idea, tp)
                    # Bonus for diversity: subtract mean similarity to selected
                    diversity_bonus = 0.0
                    if selected_set:
                        sel_tokens = [_idea_tokens(s) for s, _ in selected]
                        cand_tokens = _idea_tokens(idea)
                        sims = [_jaccard(cand_tokens, st) for st in sel_tokens]
                        diversity_bonus = 1.0 - (sum(sims) / len(sims))
                    combined = 0.7 * ns.composite + 0.3 * diversity_bonus
                    if combined > best_score:
                        best_score = combined
                        best_idea = idea
                if best_idea is None:
                    break
                selected.append((best_idea, best_score))
                selected_set.add(best_idea.idea_id)
                tp = self._integrator.update_portfolio_with_ideas(tp, [best_idea])
            executed_stages.append("greedy-diversity")
        else:
            scored.sort(key=lambda t: t[1], reverse=True)
            selected = scored[: self.k]

        # Stage 5: budget cap
        if self._stages["budget-cap"].enabled:
            budget_remaining = self.budget
            final: list[tuple[Idea, float]] = []
            for idea, score in selected:
                cost = float((idea.predicted_gain.cost if idea.predicted_gain else 0.0))
                if budget_remaining - cost >= 0:
                    final.append((idea, score))
                    budget_remaining -= cost
            selected = final
            executed_stages.append("budget-cap")

        t1 = time.monotonic()
        duration_ms = (t1 - t0) * 1000.0

        result = PipelineResult(
            result_id=str(uuid.uuid4()),
            results=tuple(idea for idea, _ in selected),
            novelty_scores=tuple(score for _, score in selected),
            pipeline_stages=tuple(executed_stages),
            purpose=self.purpose,
            duration_ms=duration_ms,
        )
        self._run_history.append(result)
        return result

    def run_staged(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[str, list[Idea]]]:
        """Run the pipeline stage-by-stage, returning intermediate results.

        Parameters
        ----------
        candidates:
            Candidate ideas.
        portfolio:
            Existing portfolio.

        Returns
        -------
        list[tuple[str, list[Idea]]]
            List of (stage_name, ideas_at_that_stage) pairs.
        """
        stages: list[tuple[str, list[Idea]]] = []
        working: list[Idea] = list(candidates)
        stages.append(("input", list(working)))

        if self._stages["trust-filter"].enabled:
            working = self._trust_search.filter(working)
            stages.append(("trust-filter", list(working)))

        tp = self._integrator.build_theorem_portfolio(portfolio)
        if self._stages["build-portfolio"].enabled:
            stages.append(("build-portfolio", list(working)))  # no reduction

        if self._stages["novelty-score"].enabled:
            filtered = []
            for idea in working:
                ns = self._integrator.score_against_portfolio(idea, tp)
                if ns.composite >= self.novelty_threshold:
                    filtered.append(idea)
            working = filtered
            stages.append(("novelty-score", list(working)))

        if self._stages["greedy-diversity"].enabled:
            selected: list[Idea] = []
            selected_ids: set[str] = set()
            for _ in range(min(self.k, len(working))):
                best: Idea | None = None
                best_score = -1.0
                for idea in working:
                    if idea.idea_id in selected_ids:
                        continue
                    ns = self._integrator.score_against_portfolio(idea, tp)
                    if ns.composite > best_score:
                        best_score = ns.composite
                        best = idea
                if best is not None:
                    selected.append(best)
                    selected_ids.add(best.idea_id)
                    tp = self._integrator.update_portfolio_with_ideas(tp, [best])
            working = selected
            stages.append(("greedy-diversity", list(working)))

        if self._stages["budget-cap"].enabled:
            budget_remaining = self.budget
            final: list[Idea] = []
            for idea in working:
                cost = float((idea.predicted_gain.cost if idea.predicted_gain else 0.0))
                if budget_remaining - cost >= 0:
                    final.append(idea)
                    budget_remaining -= cost
            working = final
            stages.append(("budget-cap", list(working)))

        return stages

    def dry_run(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> dict[str, Any]:
        """Simulate the pipeline and return per-stage stats without side effects.

        Parameters
        ----------
        candidates:
            Candidate ideas.
        portfolio:
            Existing portfolio.

        Returns
        -------
        dict[str, Any]
            Per-stage statistics.
        """
        staged = self.run_staged(candidates, portfolio)
        stats: dict[str, Any] = {}
        for stage_name, ideas_at_stage in staged:
            stats[stage_name] = {
                "count": len(ideas_at_stage),
                "idea_ids": [idea.idea_id for idea in ideas_at_stage],
            }
        stats["_total_candidates"] = len(candidates)
        stats["_portfolio_size"] = len(portfolio)
        stats["_budget"] = self.budget
        stats["_k"] = self.k
        stats["_threshold"] = self.novelty_threshold
        return stats

    def explain(self, result: PipelineResult, portfolio: Sequence[Idea]) -> str:
        """Generate a detailed human-readable explanation of the pipeline result.

        Parameters
        ----------
        result:
            The result to explain.
        portfolio:
            Portfolio used during the run.

        Returns
        -------
        str
            Multi-line explanation.
        """
        lines = [
            f"Pipeline Explanation for run {result.result_id[:8]}",
            "=" * 60,
            f"  Purpose       : {self.purpose}",
            f"  Duration      : {result.duration_ms:.1f}ms",
            f"  Stages run    : {', '.join(result.pipeline_stages)}",
            f"  Portfolio size: {len(portfolio)}",
            f"  Results       : {result.count} ideas selected",
            f"  Mean novelty  : {result.mean_novelty:.4f}",
            "",
            "Selected ideas:",
        ]
        for i, (idea, score) in enumerate(zip(result.results, result.novelty_scores)):
            lines.append(
                f"  {i + 1:2d}. [{idea.trust_status.value:>12}] "
                f"{idea.title} (novelty={score:.3f})"
            )
        if not result.results:
            lines.append("  (no ideas selected)")
        lines.append("")
        lines.append("Stage configuration:")
        for stage in self._stages.values():
            lines.append(f"  {stage.describe()}")
        return "\n".join(lines)

    def diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information about the pipeline.

        Returns
        -------
        dict[str, Any]
            Dictionary with configuration and run history stats.
        """
        return {
            "purpose": self.purpose,
            "budget": self.budget,
            "k": self.k,
            "min_trust": self.min_trust.name,
            "novelty_threshold": self.novelty_threshold,
            "stages": {sid: s.to_dict() for sid, s in self._stages.items()},
            "run_count": len(self._run_history),
            "last_run": self._run_history[-1].to_dict() if self._run_history else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise pipeline configuration to a dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "purpose": self.purpose,
            "budget": self.budget,
            "k": self.k,
            "min_trust": self.min_trust.name,
            "novelty_threshold": self.novelty_threshold,
            "stages": {sid: s.to_dict() for sid, s in self._stages.items()},
        }


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "PortfolioNoveltyIntegrator",
    "IdeaNoveltyScorer",
    "TrustFilteredSearch",
    "FederationNoveltyBridge",
    "PipelineStage",
    "PipelineResult",
    "IntegratedNoveltyPipeline",
]
