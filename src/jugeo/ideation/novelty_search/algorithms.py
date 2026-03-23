"""High-level novelty search algorithms for jugeo.ideation.novelty_search – theory2.tex Ch57.

Provides the top-level algorithm classes that orchestrate all subsystems:
optimal search, novelty ranking, frontier exploration, diagnostics,
history tracking, and benchmarking.

These classes build on the lower-level strategies in :mod:`search_strategies`
and the primitive types in :mod:`jugeo.ideation.novelty` and
:mod:`jugeo.ideation.ideas` to expose a cohesive, high-level API for
novelty-driven idea discovery.

Module layout::

    OptimalNoveltySearch   – implements optimal novelty search combining all strategies
    NoveltyRanker          – ranks candidates by multi-dimensional novelty
    FrontierExplorer       – explores and characterises the novelty frontier
    SearchDiagnostics      – diagnostics for search quality
    SearchHistoryEntry     – single-run record (frozen dataclass)
    SearchHistory          – time-series log of search runs
    SearchBenchmark        – benchmarks and compares search runs

Design notes
------------
* Each class is independently instantiable.  There is no global registry and
  no shared mutable state between instances.
* All public methods return plain Python types (lists, dicts, strings) to
  keep the API serialisation-friendly.
* Budget is expressed in the same units as ``GainProfile.cost`` throughout.
* The diagnostics layer is deliberately non-blocking: it reports issues as
  warning strings rather than raising exceptions, so downstream code can
  decide what to do with quality reports.

References
----------
theory2.tex Chapter 57 "Optimal Novelty Search for Mathematical Purpose".
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel, TrustAlgebra
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
    IdeaEvaluator,
    EvaluationResult,
    IdeaHistory,
    IdeaDiagnostics,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltyMetric as _NoveltyMetricBase,
    NoveltySearcher as _NoveltySearcherBase,
    TheoremPortfolio,
    PurposeAlignmentChecker,
    NoveltyFilter,
    NoveltyOptimizer,
    NoveltyHistory,
    NoveltyDiagnostics,
)

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to ``0.0``.
    hi:
        Upper bound (inclusive).  Defaults to ``1.0``.

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
    >>> _clamp(0.42)
    0.42
    """
    return max(lo, min(hi, float(value)))


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    The string always includes the ``+00:00`` timezone designator for
    unambiguous downstream parsing.

    Returns
    -------
    str
        UTC datetime, e.g. ``"2024-01-15T12:34:56.789012+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Convert *text* to a frozenset of lowercase word tokens.

    Punctuation is replaced with whitespace; resulting tokens shorter than
    two characters are discarded.

    Parameters
    ----------
    text:
        Arbitrary human-readable text.

    Returns
    -------
    frozenset[str]
        Immutable set of lowercase word tokens.
    """
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return frozenset(t for t in cleaned.split() if len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard similarity between two token sets.

    Jaccard(A, B) = |A ∩ B| / |A ∪ B|.  Returns 1.0 when both sets are
    empty (two empty documents are treated as identical).

    Parameters
    ----------
    a, b:
        Token sets to compare.

    Returns
    -------
    float
        Similarity in [0, 1].  Higher means more similar (less novel).
    """
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _idea_tokens(idea: Idea) -> frozenset[str]:
    """Extract all textual tokens from the meaningful fields of *idea*.

    Combines tokens from title, purpose, target_area, and hypothesis to
    represent the full semantic content of the idea.

    Parameters
    ----------
    idea:
        The idea from which to extract tokens.

    Returns
    -------
    frozenset[str]
        Combined token set from all text fields.
    """
    parts = [idea.title, idea.purpose, idea.target_area, idea.hypothesis]
    combined: set[str] = set()
    for part in parts:
        combined |= _tokenize(part)
    return frozenset(combined)


# ---------------------------------------------------------------------------
# 1. OptimalNoveltySearch
# ---------------------------------------------------------------------------


class OptimalNoveltySearch:
    """Full-pipeline optimal novelty search.

    Combines novelty filtering, multi-dimensional ranking, diversity
    selection, and budget enforcement into a single orchestrated pipeline
    that delivers the best-quality result for the given constraints.

    The pipeline executes in this order:

    1. **Filter** – remove candidates below ``novelty_threshold``.
    2. **Rank** – rank remaining candidates by a multi-dimensional novelty
       score (semantic distance, purpose alignment, feasibility).
    3. **Diversify** – apply greedy diversity selection to the top-ranked
       candidates so the result set covers a broad semantic region.
    4. **Budget** – drop ideas until the cumulative cost stays within
       ``budget``.

    Parameters
    ----------
    purpose:
        Research purpose string.  Used in purpose-alignment scoring.
    budget:
        Maximum cumulative cost of selected ideas (in ``GainProfile.cost``
        units).  Defaults to ``100.0``.
    k:
        Maximum number of ideas to return.
    novelty_threshold:
        Minimum composite novelty score for a candidate to be eligible.
    diversity_weight:
        Weight applied to pairwise diversity in the diversification step.
        Range [0, 1].  ``0`` disables diversification; ``1`` makes
        diversity the primary objective.
    """

    def __init__(
        self,
        purpose: str,
        budget: float = 100.0,
        k: int = 10,
        novelty_threshold: float = 0.3,
        diversity_weight: float = 0.5,
    ) -> None:
        self.purpose = purpose
        self.budget = budget
        self.k = max(1, k)
        self.novelty_threshold = _clamp(novelty_threshold)
        self.diversity_weight = _clamp(diversity_weight)
        self._diagnostics_log: list[str] = []

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Execute the full novelty search pipeline.

        Parameters
        ----------
        candidates:
            Ideas to search over.  May overlap with *portfolio*; such
            ideas will score poorly and not be selected.
        portfolio:
            Current portfolio ideas.  Novelty is measured relative to
            this set.

        Returns
        -------
        list[Idea]
            Up to ``k`` ideas passing all pipeline stages.
        """
        self._diagnostics_log.clear()

        if not candidates:
            self._diagnostics_log.append("Empty candidate list — returning empty result.")
            return []

        # Stage 1: filter below novelty threshold
        filtered = self._filter_threshold(candidates, portfolio)
        self._diagnostics_log.append(
            f"Threshold filter: {len(candidates)} → {len(filtered)} candidates "
            f"(threshold={self.novelty_threshold:.2f})"
        )
        if not filtered:
            self._diagnostics_log.append("All candidates below threshold.")
            return []

        # Stage 2: rank by novelty
        ranked = self._rank_by_novelty(filtered, portfolio)
        self._diagnostics_log.append(f"Ranked {len(ranked)} candidates by novelty.")

        # Stage 3: diversity selection from top-2k candidates
        pool_size = min(len(ranked), self.k * 2)
        top_pool = [idea for idea, _ in ranked[:pool_size]]
        diverse_selection = self._apply_diversity(
            [(idea, score) for idea, score in ranked[:pool_size]], self.k
        )
        self._diagnostics_log.append(
            f"Diversity selection: {len(top_pool)} → {len(diverse_selection)} ideas"
        )

        # Stage 4: apply budget constraint
        result = self._within_budget(diverse_selection, self.budget)
        self._diagnostics_log.append(
            f"Budget filter (budget={self.budget:.1f}): "
            f"{len(diverse_selection)} → {len(result)} ideas"
        )

        return result

    def _filter_threshold(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Keep only candidates with composite novelty >= threshold.

        Composite novelty is estimated using the Jaccard-based distance from
        the union of portfolio tokens.

        Parameters
        ----------
        candidates:
            Candidate ideas to filter.
        portfolio:
            Reference portfolio.

        Returns
        -------
        list[Idea]
            Candidates that clear the novelty threshold.
        """
        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()

        result: list[Idea] = []
        for candidate in candidates:
            idea_tok = _idea_tokens(candidate)
            novelty = _clamp(1.0 - _jaccard(idea_tok, portfolio_tokens))
            if novelty >= self.novelty_threshold:
                result.append(candidate)
        return result

    def _rank_by_novelty(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[Idea, float]]:
        """Rank candidates by their minimum Jaccard distance to the portfolio.

        Higher distance → higher rank (more novel).

        Parameters
        ----------
        candidates:
            Ideas to rank.
        portfolio:
            Reference portfolio.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, novelty_score)`` sorted descending by score.
        """
        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()

        scored: list[tuple[Idea, float]] = []
        for candidate in candidates:
            idea_tok = _idea_tokens(candidate)
            novelty = _clamp(1.0 - _jaccard(idea_tok, portfolio_tokens))

            # Purpose alignment modulation
            if self.purpose:
                purpose_tok = _tokenize(self.purpose)
                purpose_sim = _jaccard(_tokenize(candidate.purpose), purpose_tok)
                # Blend: 60% raw novelty + 40% purpose alignment
                novelty = _clamp(0.6 * novelty + 0.4 * purpose_sim)

            scored.append((candidate, novelty))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _apply_diversity(
        self,
        ranked: list[tuple[Idea, float]],
        k: int,
    ) -> list[Idea]:
        """Greedy diversity selection over a ranked candidate list.

        The first idea is taken from the top of the ranked list.
        Subsequent ideas are chosen to maximise a blend of rank-based
        score and minimum pairwise distance from already-selected ideas.

        Parameters
        ----------
        ranked:
            ``(idea, novelty_score)`` pairs sorted descending.
        k:
            Number of ideas to select.

        Returns
        -------
        list[Idea]
            Selected ideas.
        """
        if not ranked:
            return []
        k = min(k, len(ranked))
        if k == 1:
            return [ranked[0][0]]

        score_map: dict[str, float] = {idea.idea_id: score for idea, score in ranked}
        remaining: list[Idea] = [idea for idea, _ in ranked]

        selected: list[Idea] = [remaining.pop(0)]

        while len(selected) < k and remaining:
            best: Idea | None = None
            best_combined: float = -1.0

            for candidate in remaining:
                cand_tok = _idea_tokens(candidate)
                min_dist = min(
                    1.0 - _jaccard(cand_tok, _idea_tokens(s)) for s in selected
                )
                rank_score = score_map.get(candidate.idea_id, 0.0)
                combined = (
                    (1.0 - self.diversity_weight) * rank_score
                    + self.diversity_weight * min_dist
                )
                if combined > best_combined:
                    best_combined = combined
                    best = candidate

            if best is not None:
                selected.append(best)
                remaining.remove(best)

        return selected

    def _within_budget(
        self,
        ideas: list[Idea],
        budget: float,
    ) -> list[Idea]:
        """Return the longest prefix of *ideas* whose cumulative cost ≤ *budget*.

        Ideas are taken in the order provided (descending quality).  The
        greedy approach accepts ideas until adding the next would bust the
        budget.  Skipping is also tried: a cheaper later idea may fit even
        when an expensive earlier one would not.

        Parameters
        ----------
        ideas:
            Ideas in preferred order.
        budget:
            Maximum total cost.

        Returns
        -------
        list[Idea]
            Subset of *ideas* with total cost ≤ *budget*.
        """
        selected: list[Idea] = []
        cumulative: float = 0.0
        for idea in ideas:
            cost = idea.predicted_gain.cost
            if cumulative + cost <= budget:
                selected.append(idea)
                cumulative += cost
        return selected

    def incremental_search(
        self,
        new_candidates: Sequence[Idea],
        current_selection: list[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Incrementally update the selection given new candidates.

        Computes novelty of new candidates relative to both the existing
        portfolio and the current selection, then merges the best additions
        into the current selection, respecting ``k`` and ``budget``.

        Parameters
        ----------
        new_candidates:
            Newly available ideas not yet evaluated.
        current_selection:
            Ideas already selected.
        portfolio:
            Reference portfolio.

        Returns
        -------
        list[Idea]
            Updated selection list with best new candidates integrated.
        """
        combined_reference = list(portfolio) + current_selection

        filtered = self._filter_threshold(new_candidates, combined_reference)
        if not filtered:
            return list(current_selection)

        ranked = self._rank_by_novelty(filtered, combined_reference)
        additions = self._apply_diversity(ranked, self.k)
        merged = list(current_selection) + additions

        # Re-rank merged list and cap at k within budget
        re_ranked = self._rank_by_novelty(merged, portfolio)
        diverse_merged = self._apply_diversity(re_ranked, self.k)
        return self._within_budget(diverse_merged, self.budget)

    def explain(
        self,
        result: list[Idea],
        portfolio: Sequence[Idea],
    ) -> str:
        """Generate a detailed explanation of the search result.

        Parameters
        ----------
        result:
            The ideas returned by :meth:`search`.
        portfolio:
            Reference portfolio used in the search.

        Returns
        -------
        str
            Multi-line explanation text suitable for logging or display.
        """
        lines: list[str] = [
            "OptimalNoveltySearch Result Explanation",
            "=" * 50,
            f"Purpose:    {self.purpose!r}",
            f"Budget:     {self.budget:.2f}",
            f"k:          {self.k}",
            f"Threshold:  {self.novelty_threshold:.2f}",
            f"Diversity:  {self.diversity_weight:.2f}",
            "",
            "Pipeline log:",
        ]
        for log_line in self._diagnostics_log:
            lines.append(f"  • {log_line}")
        lines.append("")
        lines.append(f"Selected {len(result)} idea(s):")

        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()

        total_cost: float = 0.0
        for rank, idea in enumerate(result, 1):
            idea_tok = _idea_tokens(idea)
            novelty = _clamp(1.0 - _jaccard(idea_tok, portfolio_tokens))
            total_cost += idea.predicted_gain.cost
            lines.append(
                f"  {rank:2d}. [{idea.idea_id}] {idea.title!r}\n"
                f"       novelty={novelty:.3f}  trust={idea.trust_status.value}  "
                f"cost={idea.predicted_gain.cost:.2f}  "
                f"theorem_yield={idea.predicted_gain.theorem_yield:.2f}"
            )

        lines.append(f"\nTotal cost: {total_cost:.2f} / {self.budget:.2f}")
        return "\n".join(lines)

    def diagnostics(self) -> dict[str, Any]:
        """Return a diagnostics snapshot.

        Returns
        -------
        dict[str, Any]
            ``"purpose"``, ``"budget"``, ``"k"``, ``"novelty_threshold"``,
            ``"diversity_weight"``, ``"pipeline_log"``.
        """
        return {
            "purpose": self.purpose,
            "budget": self.budget,
            "k": self.k,
            "novelty_threshold": self.novelty_threshold,
            "diversity_weight": self.diversity_weight,
            "pipeline_log": list(self._diagnostics_log),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise configuration to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation of the search configuration.
        """
        return {
            "purpose": self.purpose,
            "budget": self.budget,
            "k": self.k,
            "novelty_threshold": self.novelty_threshold,
            "diversity_weight": self.diversity_weight,
        }


# ---------------------------------------------------------------------------
# 2. NoveltyRanker
# ---------------------------------------------------------------------------


class NoveltyRanker:
    """Ranks candidate ideas by a multi-dimensional novelty score.

    The composite score uses three independent components:

    - **Semantic novelty** (weight *w₀*): 1 − max Jaccard similarity to
      any portfolio idea.
    - **Purpose novelty** (weight *w₁*): how differently aligned the
      candidate's purpose is from the portfolio mean.
    - **Feasibility** (weight *w₂*): an estimate of how achievable the idea
      is given current tools.

    Default weights ``(0.4, 0.35, 0.25)`` match the ``NoveltyScore.composite``
    formula from theory2.tex.

    Parameters
    ----------
    purpose:
        Research purpose string for purpose-novelty computation.  May be empty.
    weights:
        ``(w_semantic, w_purpose, w_feasibility)`` summing to 1.0 (not
        enforced, but normalised internally to avoid surprises).
    """

    def __init__(
        self,
        purpose: str = "",
        weights: tuple[float, float, float] = (0.4, 0.35, 0.25),
    ) -> None:
        self.purpose = purpose
        total = sum(weights) or 1.0
        self.weights: tuple[float, float, float] = (
            weights[0] / total,
            weights[1] / total,
            weights[2] / total,
        )

    # ------------------------------------------------------------------
    # Core ranking
    # ------------------------------------------------------------------

    def rank(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[tuple[Idea, float]]:
        """Rank all candidates by composite novelty score (descending).

        Parameters
        ----------
        candidates:
            Ideas to rank.
        portfolio:
            Reference portfolio.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, composite_score)`` sorted descending by score.
        """
        scored: list[tuple[Idea, float]] = [
            (idea, self.novelty_score(idea, portfolio)) for idea in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def novelty_score(
        self,
        candidate: Idea,
        portfolio: Sequence[Idea],
    ) -> float:
        """Compute the composite novelty score for a single candidate.

        Parameters
        ----------
        candidate:
            The idea to evaluate.
        portfolio:
            Reference portfolio.

        Returns
        -------
        float
            Weighted composite novelty score in [0, 1].
        """
        w0, w1, w2 = self.weights
        s = _semantic_novelty(candidate, portfolio)
        p = self._purpose_novelty(candidate, portfolio)
        f = self._feasibility_score(candidate)
        return _clamp(w0 * s + w1 * p + w2 * f)

    def _semantic_novelty(
        self,
        idea: Idea,
        portfolio: Sequence[Idea],
    ) -> float:
        """Semantic novelty = 1 − max Jaccard similarity to any portfolio idea.

        Parameters
        ----------
        idea:
            Candidate idea.
        portfolio:
            Reference portfolio.

        Returns
        -------
        float
            Semantic novelty in [0, 1].
        """
        idea_tok = _idea_tokens(idea)
        max_sim: float = 0.0
        for p_idea in portfolio:
            sim = _jaccard(idea_tok, _idea_tokens(p_idea))
            if sim > max_sim:
                max_sim = sim
        return _clamp(1.0 - max_sim)

    def _purpose_novelty(
        self,
        idea: Idea,
        portfolio: Sequence[Idea],
    ) -> float:
        """Measure how differently purpose-aligned *idea* is from the portfolio mean.

        If the portfolio is empty or no purpose is set, returns 0.5 as a
        neutral baseline.

        Parameters
        ----------
        idea:
            Candidate idea.
        portfolio:
            Reference portfolio.

        Returns
        -------
        float
            Purpose novelty in [0, 1].
        """
        if not portfolio:
            return 0.5

        purpose_tok = _tokenize(self.purpose) if self.purpose else _tokenize(idea.purpose)
        if not purpose_tok:
            return 0.5

        portfolio_purpose_sims: list[float] = []
        for p_idea in portfolio:
            p_tok = _tokenize(p_idea.purpose)
            sim = _jaccard(purpose_tok, p_tok)
            portfolio_purpose_sims.append(sim)

        mean_portfolio_sim = sum(portfolio_purpose_sims) / len(portfolio_purpose_sims)
        idea_sim = _jaccard(purpose_tok, _tokenize(idea.purpose))
        delta = abs(idea_sim - mean_portfolio_sim)
        return _clamp(delta)

    def _feasibility_score(self, idea: Idea) -> float:
        """Estimate feasibility from gain profile and trust status.

        The base feasibility is ``theorem_yield / (cost + 0.1)`` clipped to
        [0, 1].  Trust status provides an additive bonus:
        - SPECULATIVE: 0.0
        - PROVISIONAL: +0.05
        - GROUNDED: +0.1
        - VALIDATED: +0.2
        - RETIRED: 0.0 (no bonus)

        Parameters
        ----------
        idea:
            Idea to evaluate.

        Returns
        -------
        float
            Feasibility estimate in [0, 1].
        """
        gain = idea.predicted_gain
        raw = gain.theorem_yield / (gain.cost + 0.1)
        trust_bonus = {
            TrustStatus.SPECULATIVE: 0.0,
            TrustStatus.PROVISIONAL: 0.05,
            TrustStatus.GROUNDED: 0.10,
            TrustStatus.VALIDATED: 0.20,
            TrustStatus.RETIRED: 0.0,
        }.get(idea.trust_status, 0.0)
        return _clamp(raw + trust_bonus)

    # ------------------------------------------------------------------
    # Convenience selectors
    # ------------------------------------------------------------------

    def top_k(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        k: int,
    ) -> list[Idea]:
        """Return the *k* most novel candidates.

        Parameters
        ----------
        candidates:
            Ideas to select from.
        portfolio:
            Reference portfolio.
        k:
            Number of ideas to return.

        Returns
        -------
        list[Idea]
            Top-*k* ideas by composite novelty score.
        """
        ranked = self.rank(candidates, portfolio)
        return [idea for idea, _ in ranked[:k]]

    def bottom_k(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        k: int,
    ) -> list[Idea]:
        """Return the *k* least novel (most redundant) candidates.

        These ideas score lowest on the composite novelty measure and are
        most similar to ideas already in the portfolio.

        Parameters
        ----------
        candidates:
            Ideas to select from.
        portfolio:
            Reference portfolio.
        k:
            Number of ideas to return.

        Returns
        -------
        list[Idea]
            Bottom-*k* ideas by composite novelty score.
        """
        ranked = self.rank(candidates, portfolio)
        return [idea for idea, _ in ranked[-k:]]

    def score_distribution(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> dict[str, float]:
        """Compute descriptive statistics of the novelty score distribution.

        Parameters
        ----------
        candidates:
            Ideas to score.
        portfolio:
            Reference portfolio.

        Returns
        -------
        dict[str, float]
            Keys: ``"mean"``, ``"std"``, ``"min"``, ``"max"``, ``"median"``.
            Returns zeros when *candidates* is empty.
        """
        if not candidates:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0}
        scores = [self.novelty_score(idea, portfolio) for idea in candidates]
        return {
            "mean": statistics.mean(scores),
            "std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "min": min(scores),
            "max": max(scores),
            "median": statistics.median(scores),
        }

    def explain(
        self,
        candidate: Idea,
        portfolio: Sequence[Idea],
    ) -> str:
        """Return a component-level breakdown of the novelty score.

        Parameters
        ----------
        candidate:
            The idea to explain.
        portfolio:
            Reference portfolio.

        Returns
        -------
        str
            Human-readable multi-line explanation.
        """
        w0, w1, w2 = self.weights
        sem = self._semantic_novelty(candidate, portfolio)
        pur = self._purpose_novelty(candidate, portfolio)
        feas = self._feasibility_score(candidate)
        composite = _clamp(w0 * sem + w1 * pur + w2 * feas)

        lines = [
            f"NoveltyRanker breakdown for [{candidate.idea_id}] {candidate.title!r}",
            f"  Semantic novelty:  {sem:.4f}  (weight={w0:.2f}  contrib={w0*sem:.4f})",
            f"  Purpose novelty:   {pur:.4f}  (weight={w1:.2f}  contrib={w1*pur:.4f})",
            f"  Feasibility:       {feas:.4f}  (weight={w2:.2f}  contrib={w2*feas:.4f})",
            f"  Composite score:   {composite:.4f}",
            f"  Trust status:      {candidate.trust_status.value}",
            f"  Theorem yield:     {candidate.predicted_gain.theorem_yield:.3f}",
            f"  Cost:              {candidate.predicted_gain.cost:.3f}",
        ]
        return "\n".join(lines)


# Module-level alias used inside NoveltyRanker to avoid name shadowing
def _semantic_novelty(idea: Idea, portfolio: Sequence[Idea]) -> float:
    """Standalone semantic novelty helper (1 − max Jaccard to portfolio)."""
    idea_tok = _idea_tokens(idea)
    max_sim: float = 0.0
    for p_idea in portfolio:
        sim = _jaccard(idea_tok, _idea_tokens(p_idea))
        if sim > max_sim:
            max_sim = sim
    return _clamp(1.0 - max_sim)


# ---------------------------------------------------------------------------
# 3. FrontierExplorer
# ---------------------------------------------------------------------------


class FrontierExplorer:
    """Explores and characterises the novelty frontier.

    The novelty frontier consists of ideas that are not dominated on both
    novelty and feasibility.  This class iteratively expands the frontier
    from high-novelty seed points and characterises the resulting set.

    Parameters
    ----------
    purpose:
        Research purpose string for purpose-gap scoring.
    """

    def __init__(self, purpose: str = "") -> None:
        self.purpose = purpose
        self._ranker = NoveltyRanker(purpose=purpose)

    # ------------------------------------------------------------------
    # Core exploration
    # ------------------------------------------------------------------

    def explore(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        steps: int = 10,
    ) -> list[Idea]:
        """Iteratively explore the novelty frontier.

        Starts from the ideas with highest novelty (seeds) and expands the
        frontier by iteratively adding the candidate that most increases the
        minimum pairwise distance from the current frontier.

        Parameters
        ----------
        candidates:
            Ideas to explore.
        portfolio:
            Reference portfolio.
        steps:
            Number of expansion steps.  Each step adds one idea to the
            frontier up to a maximum of ``min(steps, len(candidates))`` ideas.

        Returns
        -------
        list[Idea]
            The explored frontier after *steps* expansions.
        """
        if not candidates:
            return []

        steps = min(steps, len(candidates))
        ranked = self._ranker.rank(candidates, portfolio)
        if not ranked:
            return []

        frontier: list[Idea] = [ranked[0][0]]
        remaining = [idea for idea, _ in ranked[1:]]

        for _ in range(steps - 1):
            if not remaining:
                break
            best_candidate = max(
                remaining,
                key=lambda c: self._min_frontier_distance(c, frontier),
            )
            frontier.append(best_candidate)
            remaining.remove(best_candidate)

        return frontier

    def _min_frontier_distance(
        self,
        candidate: Idea,
        frontier: list[Idea],
    ) -> float:
        """Minimum Jaccard distance from *candidate* to any frontier idea.

        Parameters
        ----------
        candidate:
            Idea to measure.
        frontier:
            Current frontier.

        Returns
        -------
        float
            Minimum distance in [0, 1].  1.0 when frontier is empty.
        """
        if not frontier:
            return 1.0
        cand_tok = _idea_tokens(candidate)
        return min(1.0 - _jaccard(cand_tok, _idea_tokens(f)) for f in frontier)

    def frontier(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Return ideas on the Pareto frontier (novelty × feasibility).

        An idea is on the frontier when no other candidate strictly dominates
        it on both novelty and feasibility.

        Parameters
        ----------
        candidates:
            Ideas to compute the frontier from.
        portfolio:
            Reference portfolio.

        Returns
        -------
        list[Idea]
            Non-dominated ideas.
        """
        if not candidates:
            return []

        scores: list[tuple[Idea, float, float]] = []
        for idea in candidates:
            novelty = _semantic_novelty(idea, portfolio)
            gain = idea.predicted_gain
            feasibility = _clamp(gain.theorem_yield / (gain.cost + 0.1))
            scores.append((idea, novelty, feasibility))

        front: list[Idea] = []
        for i, (idea_a, nov_a, feas_a) in enumerate(scores):
            dominated = False
            for j, (idea_b, nov_b, feas_b) in enumerate(scores):
                if i == j:
                    continue
                if nov_b >= nov_a and feas_b >= feas_a and (nov_b > nov_a or feas_b > feas_a):
                    dominated = True
                    break
            if not dominated:
                front.append(idea_a)
        return front

    def characterize_frontier(self, frontier: list[Idea]) -> dict[str, Any]:
        """Compute descriptive statistics for the frontier.

        Returns
        -------
        dict[str, Any]
            Keys:
            - ``"size"``: number of ideas on the frontier.
            - ``"mean_novelty"``: mean ``idea.novelty_score``.
            - ``"domain_spread"``: number of distinct ``target_area`` values.
            - ``"trust_distribution"``: count per ``TrustStatus``.
            - ``"mean_cost"``: mean ``GainProfile.cost``.
            - ``"mean_yield"``: mean ``GainProfile.theorem_yield``.
        """
        if not frontier:
            return {
                "size": 0,
                "mean_novelty": 0.0,
                "domain_spread": 0,
                "trust_distribution": {},
                "mean_cost": 0.0,
                "mean_yield": 0.0,
            }

        trust_counts: dict[str, int] = defaultdict(int)
        for idea in frontier:
            trust_counts[idea.trust_status.value] += 1

        areas = {idea.target_area for idea in frontier}
        novelties = [idea.novelty_score for idea in frontier]
        costs = [idea.predicted_gain.cost for idea in frontier]
        yields = [idea.predicted_gain.theorem_yield for idea in frontier]

        return {
            "size": len(frontier),
            "mean_novelty": sum(novelties) / len(novelties),
            "domain_spread": len(areas),
            "trust_distribution": dict(trust_counts),
            "mean_cost": sum(costs) / len(costs),
            "mean_yield": sum(yields) / len(yields),
        }

    def expand_frontier(
        self,
        current_frontier: list[Idea],
        new_candidates: Sequence[Idea],
    ) -> list[Idea]:
        """Update the frontier by incorporating new candidates.

        Any new candidate that is not dominated by the existing frontier
        (on novelty × feasibility) is added.  Existing frontier members
        that become dominated by a new candidate are removed.

        Parameters
        ----------
        current_frontier:
            Existing frontier.
        new_candidates:
            Newly available ideas to consider.

        Returns
        -------
        list[Idea]
            Updated frontier.
        """
        all_ideas = list(current_frontier) + list(new_candidates)
        return self.frontier(all_ideas, [])

    def frontier_gap_score(
        self,
        frontier: list[Idea],
        purpose: str,
    ) -> float:
        """Estimate how well the frontier covers the purpose space.

        Coverage is estimated as the mean Jaccard similarity between
        the purpose token set and the purpose fields of frontier ideas.
        A score of 1.0 means perfect coverage; 0.0 means no alignment.

        Parameters
        ----------
        frontier:
            Current frontier.
        purpose:
            Research purpose string.

        Returns
        -------
        float
            Coverage score in [0, 1].
        """
        if not frontier or not purpose:
            return 0.0
        purpose_tok = _tokenize(purpose)
        if not purpose_tok:
            return 0.0
        sims = [_jaccard(purpose_tok, _tokenize(idea.purpose)) for idea in frontier]
        return _clamp(sum(sims) / len(sims))

    def explore_with_budget(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        budget: float,
    ) -> list[Idea]:
        """Explore the frontier subject to a budget constraint.

        Parameters
        ----------
        candidates:
            Ideas to explore.
        portfolio:
            Reference portfolio.
        budget:
            Maximum cumulative cost.

        Returns
        -------
        list[Idea]
            Frontier ideas within budget.
        """
        all_frontier = self.frontier(candidates, portfolio)
        selected: list[Idea] = []
        cumulative: float = 0.0
        for idea in sorted(all_frontier, key=lambda i: i.predicted_gain.cost):
            if cumulative + idea.predicted_gain.cost <= budget:
                selected.append(idea)
                cumulative += idea.predicted_gain.cost
        return selected

    def exploration_path(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        steps: int = 5,
    ) -> list[list[Idea]]:
        """Return a sequence of frontier snapshots as exploration progresses.

        Each snapshot is the frontier after one additional expansion step.
        This allows callers to observe how the frontier grows and where the
        most significant jumps occur.

        Parameters
        ----------
        candidates:
            Ideas to explore.
        portfolio:
            Reference portfolio.
        steps:
            Number of expansion steps.

        Returns
        -------
        list[list[Idea]]
            One frontier snapshot per step.
        """
        ranked = self._ranker.rank(candidates, portfolio)
        if not ranked:
            return []

        snapshots: list[list[Idea]] = []
        frontier: list[Idea] = []
        remaining = [idea for idea, _ in ranked]

        for step in range(min(steps, len(remaining))):
            if step == 0:
                seed = remaining.pop(0)
                frontier = [seed]
            else:
                if not remaining:
                    break
                best = max(
                    remaining,
                    key=lambda c: self._min_frontier_distance(c, frontier),
                )
                frontier.append(best)
                remaining.remove(best)
            snapshots.append(list(frontier))

        return snapshots


# ---------------------------------------------------------------------------
# 4. SearchDiagnostics
# ---------------------------------------------------------------------------


class SearchDiagnostics:
    """Diagnostics for search quality assessment.

    Provides comprehensive metrics for evaluating the quality of a novelty
    search result, including recall, diversity, coverage gain, and issue
    detection.  All metrics are computed on-demand from the provided result
    and reference data.

    This class is stateless — every diagnostic method is a pure function of
    its inputs.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Core diagnosis
    # ------------------------------------------------------------------

    def diagnose(
        self,
        result: list[Idea],
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, Any]:
        """Perform a comprehensive quality diagnosis of a search result.

        Parameters
        ----------
        result:
            The ideas returned by the search.
        candidates:
            The full candidate pool that was searched.
        portfolio:
            Reference portfolio.
        purpose:
            Optional research purpose string.

        Returns
        -------
        dict[str, Any]
            Keys: ``"recall"``, ``"novelty_mean"``, ``"novelty_std"``,
            ``"diversity_score"``, ``"coverage_gain"``, ``"budget_utilization"``,
            ``"trust_breakdown"``, ``"issues"``.
        """
        ranker = NoveltyRanker(purpose=purpose)
        all_novel = [
            idea for idea in candidates
            if ranker.novelty_score(idea, portfolio) >= 0.3
        ]

        return {
            "recall": self.recall(result, all_novel),
            **self.novelty_statistics(result, portfolio),
            "diversity_score": self.diversity_statistics(result).get("mean_pairwise_dist", 0.0),
            "coverage_gain": self.coverage_statistics(result, portfolio, purpose).get("gain", 0.0),
            "budget_utilization": sum(i.predicted_gain.cost for i in result),
            "trust_breakdown": {
                ts.value: sum(1 for i in result if i.trust_status == ts)
                for ts in TrustStatus
            },
            "issues": self.detect_issues(result, candidates),
        }

    def recall(
        self,
        result: list[Idea],
        all_novel: list[Idea],
    ) -> float:
        """Compute recall of the result against the full novel set.

        Recall = |result ∩ all_novel| / |all_novel|.

        Parameters
        ----------
        result:
            Ideas returned by search.
        all_novel:
            All novel ideas in the candidate pool.

        Returns
        -------
        float
            Recall in [0, 1].  1.0 when *all_novel* is empty.
        """
        if not all_novel:
            return 1.0
        result_ids = {idea.idea_id for idea in result}
        novel_ids = {idea.idea_id for idea in all_novel}
        return len(result_ids & novel_ids) / len(novel_ids)

    def novelty_statistics(
        self,
        result: list[Idea],
        portfolio: Sequence[Idea],
    ) -> dict[str, float]:
        """Compute novelty statistics for the result set.

        Parameters
        ----------
        result:
            Ideas to compute statistics over.
        portfolio:
            Reference portfolio.

        Returns
        -------
        dict[str, float]
            Keys: ``"novelty_mean"``, ``"novelty_std"``, ``"novelty_min"``, ``"novelty_max"``.
        """
        if not result:
            return {"novelty_mean": 0.0, "novelty_std": 0.0, "novelty_min": 0.0, "novelty_max": 0.0}
        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()
        scores = [
            _clamp(1.0 - _jaccard(_idea_tokens(i), portfolio_tokens)) for i in result
        ]
        return {
            "novelty_mean": statistics.mean(scores),
            "novelty_std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "novelty_min": min(scores),
            "novelty_max": max(scores),
        }

    def diversity_statistics(self, result: list[Idea]) -> dict[str, float]:
        """Compute pairwise diversity statistics for the result set.

        Parameters
        ----------
        result:
            Ideas to compute diversity over.

        Returns
        -------
        dict[str, float]
            Keys: ``"mean_pairwise_dist"``, ``"min_pairwise_dist"``,
            ``"max_pairwise_dist"``.
        """
        if len(result) < 2:
            return {"mean_pairwise_dist": 0.0, "min_pairwise_dist": 0.0, "max_pairwise_dist": 0.0}
        token_sets = [_idea_tokens(idea) for idea in result]
        dists: list[float] = []
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                dists.append(1.0 - _jaccard(token_sets[i], token_sets[j]))
        return {
            "mean_pairwise_dist": statistics.mean(dists),
            "min_pairwise_dist": min(dists),
            "max_pairwise_dist": max(dists),
        }

    def coverage_statistics(
        self,
        result: list[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, float]:
        """Compute semantic coverage gain from adding *result* to *portfolio*.

        Coverage gain is the fractional increase in the union token set size.

        Parameters
        ----------
        result:
            Ideas selected by the search.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string for purpose coverage.

        Returns
        -------
        dict[str, float]
            Keys: ``"gain"``, ``"portfolio_coverage"``, ``"purpose_coverage"``.
        """
        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()
        result_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(r) for r in result)
        ) if result else frozenset()

        old_size = len(portfolio_tokens)
        new_size = len(portfolio_tokens | result_tokens)
        gain = (new_size - old_size) / max(1, old_size)

        purpose_cov = 0.0
        if purpose and result:
            purpose_tok = _tokenize(purpose)
            purpose_cov = _clamp(_jaccard(purpose_tok, result_tokens | portfolio_tokens))

        return {
            "gain": _clamp(gain),
            "portfolio_coverage": _clamp(len(portfolio_tokens & result_tokens) / max(1, len(result_tokens))),
            "purpose_coverage": purpose_cov,
        }

    def detect_issues(
        self,
        result: list[Idea],
        candidates: Sequence[Idea],
    ) -> list[str]:
        """Detect potential quality issues in the search result.

        Checks for: empty result, low-trust ideas, high-cost ideas, near-
        duplicate ideas, and RETIRED ideas.

        Parameters
        ----------
        result:
            Search result to inspect.
        candidates:
            Full candidate pool.

        Returns
        -------
        list[str]
            List of issue description strings.  Empty list means no issues.
        """
        issues: list[str] = []

        if not result:
            issues.append("Result is empty.")
            return issues

        speculative_count = sum(1 for i in result if i.trust_status == TrustStatus.SPECULATIVE)
        if speculative_count > len(result) / 2:
            issues.append(
                f"{speculative_count}/{len(result)} ideas are SPECULATIVE — "
                "consider raising the feasibility threshold."
            )

        retired_count = sum(1 for i in result if i.trust_status == TrustStatus.RETIRED)
        if retired_count:
            issues.append(
                f"{retired_count} RETIRED idea(s) in result — these should be excluded."
            )

        token_sets = [_idea_tokens(idea) for idea in result]
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                sim = _jaccard(token_sets[i], token_sets[j])
                if sim > 0.8:
                    issues.append(
                        f"Near-duplicate pair: [{result[i].idea_id}] and [{result[j].idea_id}] "
                        f"(Jaccard={sim:.2f})."
                    )

        total_cost = sum(i.predicted_gain.cost for i in result)
        if total_cost > 1000:
            issues.append(f"Total cost {total_cost:.1f} is very high — budget enforcement may help.")

        return issues

    def compare_results(
        self,
        result_a: list[Idea],
        result_b: list[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, Any]:
        """Compare two search results on key quality metrics.

        Parameters
        ----------
        result_a, result_b:
            The two result lists to compare.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string.

        Returns
        -------
        dict[str, Any]
            Side-by-side metrics for A and B with delta values.
        """
        stats_a = self.novelty_statistics(result_a, portfolio)
        stats_b = self.novelty_statistics(result_b, portfolio)
        div_a = self.diversity_statistics(result_a)
        div_b = self.diversity_statistics(result_b)

        return {
            "a": {**stats_a, **div_a, "n_results": len(result_a)},
            "b": {**stats_b, **div_b, "n_results": len(result_b)},
            "delta_novelty_mean": stats_b["novelty_mean"] - stats_a["novelty_mean"],
            "delta_diversity": (
                div_b["mean_pairwise_dist"] - div_a["mean_pairwise_dist"]
            ),
            "delta_count": len(result_b) - len(result_a),
        }

    def generate_report(
        self,
        result: list[Idea],
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> str:
        """Generate a multi-line text quality report.

        Parameters
        ----------
        result:
            Search result to report on.
        candidates:
            Full candidate pool.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string.

        Returns
        -------
        str
            Formatted multi-line report.
        """
        diag = self.diagnose(result, candidates, portfolio, purpose)
        lines = [
            "SearchDiagnostics Report",
            "=" * 50,
            f"  Result size:       {len(result)}",
            f"  Candidate pool:    {len(candidates)}",
            f"  Portfolio size:    {len(portfolio)}",
            f"  Recall:            {diag['recall']:.3f}",
            f"  Mean novelty:      {diag['novelty_mean']:.3f}",
            f"  Novelty std:       {diag['novelty_std']:.3f}",
            f"  Diversity score:   {diag['diversity_score']:.3f}",
            f"  Coverage gain:     {diag['coverage_gain']:.3f}",
            f"  Budget used:       {diag['budget_utilization']:.2f}",
            "",
            "  Trust breakdown:",
        ]
        for ts, count in diag["trust_breakdown"].items():
            if count:
                lines.append(f"    {ts}: {count}")
        if diag["issues"]:
            lines.append("")
            lines.append("  Issues:")
            for issue in diag["issues"]:
                lines.append(f"    ⚠  {issue}")
        else:
            lines.append("")
            lines.append("  No issues detected.")
        return "\n".join(lines)

    def copilot_summary(
        self,
        result: list[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> str:
        """Return a compact one-paragraph summary suitable for copilot display.

        Parameters
        ----------
        result:
            Search result.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string.

        Returns
        -------
        str
            One-paragraph plain-text summary.
        """
        if not result:
            return "No novel ideas found in this search run."
        stats = self.novelty_statistics(result, portfolio)
        div = self.diversity_statistics(result)
        purpose_str = f" for purpose {purpose!r}" if purpose else ""
        return (
            f"Found {len(result)} novel idea(s){purpose_str}. "
            f"Mean novelty: {stats['novelty_mean']:.2f} "
            f"(±{stats['novelty_std']:.2f}). "
            f"Mean pairwise diversity: {div['mean_pairwise_dist']:.2f}. "
            f"Top idea: [{result[0].idea_id}] {result[0].title!r} "
            f"(trust={result[0].trust_status.value}, "
            f"yield={result[0].predicted_gain.theorem_yield:.2f})."
        )


# ---------------------------------------------------------------------------
# 5. SearchHistoryEntry – frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHistoryEntry:
    """An immutable record of a single search run.

    Attributes
    ----------
    entry_id:
        Unique identifier for this history entry.
    timestamp:
        ISO 8601 timestamp of the search run.
    purpose:
        Research purpose used in the search.
    n_candidates:
        Number of candidates provided.
    n_portfolio:
        Number of portfolio ideas provided.
    n_results:
        Number of ideas returned.
    mean_novelty:
        Mean composite novelty score of the result set.
    diversity_score:
        Mean pairwise Jaccard distance of the result set.
    budget_used:
        Sum of costs of selected ideas.
    strategy:
        Name of the search strategy used.
    duration_ms:
        Wall-clock duration of the search in milliseconds.
    """

    entry_id: str
    timestamp: str
    purpose: str
    n_candidates: int
    n_portfolio: int
    n_results: int
    mean_novelty: float
    diversity_score: float
    budget_used: float
    strategy: str
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as primitive Python types.
        """
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "purpose": self.purpose,
            "n_candidates": self.n_candidates,
            "n_portfolio": self.n_portfolio,
            "n_results": self.n_results,
            "mean_novelty": self.mean_novelty,
            "diversity_score": self.diversity_score,
            "budget_used": self.budget_used,
            "strategy": self.strategy,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchHistoryEntry":
        """Construct a :class:`SearchHistoryEntry` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        SearchHistoryEntry
        """
        return cls(
            entry_id=str(d["entry_id"]),
            timestamp=str(d["timestamp"]),
            purpose=str(d.get("purpose", "")),
            n_candidates=int(d.get("n_candidates", 0)),
            n_portfolio=int(d.get("n_portfolio", 0)),
            n_results=int(d.get("n_results", 0)),
            mean_novelty=float(d.get("mean_novelty", 0.0)),
            diversity_score=float(d.get("diversity_score", 0.0)),
            budget_used=float(d.get("budget_used", 0.0)),
            strategy=str(d.get("strategy", "")),
            duration_ms=float(d.get("duration_ms", 0.0)),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary of this entry.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"[{self.entry_id[:8]}] {self.timestamp[:19]}  "
            f"strategy={self.strategy!r}  results={self.n_results}  "
            f"novelty={self.mean_novelty:.3f}  diversity={self.diversity_score:.3f}  "
            f"duration={self.duration_ms:.1f}ms"
        )


# ---------------------------------------------------------------------------
# 6. SearchHistory
# ---------------------------------------------------------------------------


class SearchHistory:
    """Time-series log of search runs.

    Maintains a bounded deque of :class:`SearchHistoryEntry` instances.
    When the deque is full the oldest entry is discarded.

    Parameters
    ----------
    max_entries:
        Maximum number of entries to retain.  Older entries are evicted
        when the limit is reached.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: deque[SearchHistoryEntry] = deque(maxlen=self._max_entries)
        self._diagnostics = SearchDiagnostics()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        result: list[Idea],
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str,
        strategy: str,
        start_time: float,
        budget_used: float = 0.0,
    ) -> SearchHistoryEntry:
        """Record a completed search run and return the new entry.

        Parameters
        ----------
        result:
            The ideas returned by the search.
        candidates:
            The full candidate pool.
        portfolio:
            The reference portfolio.
        purpose:
            The research purpose string.
        strategy:
            The name of the strategy that was used.
        start_time:
            ``time.perf_counter()`` value at the start of the search.
        budget_used:
            Cumulative cost of selected ideas.

        Returns
        -------
        SearchHistoryEntry
            The newly created history entry.
        """
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        stats = self._diagnostics.novelty_statistics(result, portfolio)
        div_stats = self._diagnostics.diversity_statistics(result)

        entry = SearchHistoryEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=_now_iso(),
            purpose=purpose,
            n_candidates=len(candidates),
            n_portfolio=len(portfolio),
            n_results=len(result),
            mean_novelty=round(stats["novelty_mean"], 4),
            diversity_score=round(div_stats["mean_pairwise_dist"], 4),
            budget_used=round(budget_used or sum(i.predicted_gain.cost for i in result), 4),
            strategy=strategy,
            duration_ms=round(duration_ms, 3),
        )
        self._entries.append(entry)
        return entry

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def entries(self) -> list[SearchHistoryEntry]:
        """Return all recorded entries in chronological order.

        Returns
        -------
        list[SearchHistoryEntry]
        """
        return list(self._entries)

    def recent(self, n: int = 10) -> list[SearchHistoryEntry]:
        """Return the *n* most recent entries.

        Parameters
        ----------
        n:
            Number of entries to return.

        Returns
        -------
        list[SearchHistoryEntry]
        """
        return list(self._entries)[-n:]

    def by_purpose(self, purpose: str) -> list[SearchHistoryEntry]:
        """Return all entries whose purpose matches *purpose* (case-insensitive).

        Parameters
        ----------
        purpose:
            Purpose string to filter by.

        Returns
        -------
        list[SearchHistoryEntry]
        """
        purpose_lower = purpose.lower()
        return [e for e in self._entries if purpose_lower in e.purpose.lower()]

    def statistics(self) -> dict[str, Any]:
        """Compute summary statistics across all recorded entries.

        Returns
        -------
        dict[str, Any]
            Keys: ``"n_entries"``, ``"mean_novelty"``, ``"std_novelty"``,
            ``"mean_diversity"``, ``"mean_duration_ms"``, ``"strategy_counts"``.
        """
        if not self._entries:
            return {
                "n_entries": 0,
                "mean_novelty": 0.0,
                "std_novelty": 0.0,
                "mean_diversity": 0.0,
                "mean_duration_ms": 0.0,
                "strategy_counts": {},
            }
        novelties = [e.mean_novelty for e in self._entries]
        diversities = [e.diversity_score for e in self._entries]
        durations = [e.duration_ms for e in self._entries]
        strategy_counts: dict[str, int] = defaultdict(int)
        for e in self._entries:
            strategy_counts[e.strategy] += 1
        return {
            "n_entries": len(self._entries),
            "mean_novelty": statistics.mean(novelties),
            "std_novelty": statistics.stdev(novelties) if len(novelties) > 1 else 0.0,
            "mean_diversity": statistics.mean(diversities),
            "mean_duration_ms": statistics.mean(durations),
            "strategy_counts": dict(strategy_counts),
        }

    def trend(self, n: int = 20) -> dict[str, list[float]]:
        """Return time-series of novelty and diversity for the last *n* entries.

        Parameters
        ----------
        n:
            Number of entries to include.

        Returns
        -------
        dict[str, list[float]]
            Keys: ``"novelty"``, ``"diversity"``, ``"duration_ms"``.
        """
        recent = self.recent(n)
        return {
            "novelty": [e.mean_novelty for e in recent],
            "diversity": [e.diversity_score for e in recent],
            "duration_ms": [e.duration_ms for e in recent],
        }

    def best_run(self) -> "SearchHistoryEntry | None":
        """Return the entry with the highest mean novelty score.

        Returns
        -------
        SearchHistoryEntry | None
            The best entry, or ``None`` if the history is empty.
        """
        if not self._entries:
            return None
        return max(self._entries, key=lambda e: e.mean_novelty)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire history to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            ``"max_entries"`` and ``"entries"`` list.
        """
        return {
            "max_entries": self._max_entries,
            "entries": [e.to_dict() for e in self._entries],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchHistory":
        """Deserialise a :class:`SearchHistory` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        SearchHistory
        """
        history = cls(max_entries=int(d.get("max_entries", 500)))
        for entry_dict in d.get("entries", []):
            history._entries.append(SearchHistoryEntry.from_dict(entry_dict))
        return history


# ---------------------------------------------------------------------------
# 7. SearchBenchmark
# ---------------------------------------------------------------------------


class SearchBenchmark:
    """Benchmarks and compares multiple search strategies.

    Provides facilities to time each strategy, compute quality metrics, and
    generate human-readable comparison reports.

    This class is stateless — all state is passed through method arguments.
    """

    _ALL_STRATEGIES: tuple[str, ...] = ("greedy", "beam", "pareto", "diverse")

    def __init__(self) -> None:
        self._diagnostics = SearchDiagnostics()

    # ------------------------------------------------------------------
    # Core benchmarking
    # ------------------------------------------------------------------

    def run(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str,
        strategies: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Benchmark all (or the specified) strategies.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Research purpose string.
        strategies:
            Strategy names to benchmark.  When ``None`` all four strategies
            are benchmarked.

        Returns
        -------
        dict[str, dict[str, Any]]
            Strategy name → metrics dict.  Each metrics dict contains
            ``"duration_ms"``, ``"n_results"``, ``"novelty_mean"``,
            ``"diversity"``, ``"coverage_gain"``, ``"count"``.
        """
        from jugeo.ideation.novelty_search.search_strategies import (
            SearchConfig,
            GreedySearcher,
            BeamSearcher,
            ParetoSearcher,
            DiverseSearcher,
        )

        to_run = list(strategies) if strategies else list(self._ALL_STRATEGIES)
        config = SearchConfig(purpose=purpose)
        searchers: dict[str, Any] = {
            "greedy": GreedySearcher(config),
            "beam": BeamSearcher(config),
            "pareto": ParetoSearcher(config),
            "diverse": DiverseSearcher(config),
        }

        results: dict[str, dict[str, Any]] = {}
        for strategy_name in to_run:
            if strategy_name not in searchers:
                continue
            result, duration_ms = self._time_strategy(
                strategy_name,
                candidates,
                portfolio,
                purpose,
                searchers[strategy_name],
            )
            metrics = self._score_result(result, portfolio, purpose)
            metrics["duration_ms"] = round(duration_ms, 3)
            results[strategy_name] = metrics

        return results

    def _time_strategy(
        self,
        strategy_name: str,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str,
        searcher: Any,
    ) -> tuple[list[Idea], float]:
        """Execute a searcher and measure wall-clock time.

        Parameters
        ----------
        strategy_name:
            Name of the strategy (for logging).
        candidates:
            Candidate ideas.
        portfolio:
            Reference portfolio.
        purpose:
            Purpose string.
        searcher:
            Instantiated searcher object with a ``.search()`` method.

        Returns
        -------
        tuple[list[Idea], float]
            ``(result, duration_ms)``.
        """
        t0 = time.perf_counter()
        result = searcher.search(candidates, portfolio, purpose)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return result, duration_ms

    def _score_result(
        self,
        result: list[Idea],
        portfolio: Sequence[Idea],
        purpose: str,
    ) -> dict[str, float]:
        """Compute quality metrics for a search result.

        Parameters
        ----------
        result:
            Ideas returned by the strategy.
        portfolio:
            Reference portfolio.
        purpose:
            Research purpose string.

        Returns
        -------
        dict[str, float]
            Keys: ``"novelty_mean"``, ``"diversity"``, ``"coverage_gain"``,
            ``"count"``.
        """
        novelty_stats = self._diagnostics.novelty_statistics(result, portfolio)
        div_stats = self._diagnostics.diversity_statistics(result)
        cov_stats = self._diagnostics.coverage_statistics(result, portfolio, purpose)
        return {
            "novelty_mean": round(novelty_stats["novelty_mean"], 4),
            "diversity": round(div_stats["mean_pairwise_dist"], 4),
            "coverage_gain": round(cov_stats["gain"], 4),
            "count": float(len(result)),
        }

    def compare(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> str:
        """Generate a human-readable comparison between two benchmark results.

        Parameters
        ----------
        a, b:
            Metrics dictionaries as produced by :meth:`_score_result`.

        Returns
        -------
        str
            Multi-line comparison text.
        """
        def _delta(key: str) -> str:
            delta = b.get(key, 0.0) - a.get(key, 0.0)
            sign = "+" if delta >= 0 else ""
            return f"{sign}{delta:.4f}"

        lines = [
            "Benchmark Comparison (A vs B)",
            "-" * 40,
            f"  novelty_mean:   A={a.get('novelty_mean', 0):.4f}  B={b.get('novelty_mean', 0):.4f}  Δ={_delta('novelty_mean')}",
            f"  diversity:      A={a.get('diversity', 0):.4f}  B={b.get('diversity', 0):.4f}  Δ={_delta('diversity')}",
            f"  coverage_gain:  A={a.get('coverage_gain', 0):.4f}  B={b.get('coverage_gain', 0):.4f}  Δ={_delta('coverage_gain')}",
            f"  count:          A={int(a.get('count', 0))}  B={int(b.get('count', 0))}  Δ={int(b.get('count', 0)) - int(a.get('count', 0))}",
            f"  duration_ms:    A={a.get('duration_ms', 0):.1f}  B={b.get('duration_ms', 0):.1f}  Δ={_delta('duration_ms')}",
        ]
        return "\n".join(lines)

    def best_strategy(
        self,
        benchmark_results: dict[str, dict[str, Any]],
    ) -> str:
        """Return the name of the best-performing strategy.

        The combined score is ``novelty_mean + diversity + coverage_gain``.

        Parameters
        ----------
        benchmark_results:
            Mapping from strategy name to metrics dict.

        Returns
        -------
        str
            Name of the strategy with the highest combined score.
            Returns ``"(none)"`` when *benchmark_results* is empty.
        """
        if not benchmark_results:
            return "(none)"
        return max(
            benchmark_results,
            key=lambda s: (
                benchmark_results[s].get("novelty_mean", 0.0)
                + benchmark_results[s].get("diversity", 0.0)
                + benchmark_results[s].get("coverage_gain", 0.0)
            ),
        )

    def report(
        self,
        benchmark_results: dict[str, dict[str, Any]],
    ) -> str:
        """Generate a multi-line benchmark text report.

        Parameters
        ----------
        benchmark_results:
            Mapping from strategy name to metrics dict.

        Returns
        -------
        str
            Formatted multi-line report.
        """
        if not benchmark_results:
            return "No benchmark results to report."

        best = self.best_strategy(benchmark_results)
        lines = [
            "SearchBenchmark Report",
            "=" * 60,
            f"{'Strategy':<12} {'N':>4} {'Novelty':>9} {'Diversity':>10} "
            f"{'Coverage':>10} {'Duration':>10}",
            "-" * 60,
        ]
        for strategy, metrics in sorted(benchmark_results.items()):
            marker = " ← best" if strategy == best else ""
            lines.append(
                f"{strategy:<12} {int(metrics.get('count', 0)):>4} "
                f"{metrics.get('novelty_mean', 0):>9.4f} "
                f"{metrics.get('diversity', 0):>10.4f} "
                f"{metrics.get('coverage_gain', 0):>10.4f} "
                f"{metrics.get('duration_ms', 0):>9.1f}ms{marker}"
            )
        lines.append("=" * 60)
        lines.append(f"Best strategy: {best!r}")
        return "\n".join(lines)
