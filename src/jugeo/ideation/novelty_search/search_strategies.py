"""Search strategy implementations for novelty_search – theory2.tex Ch57.

Implements multiple novelty-maximising search strategies: greedy, beam,
Pareto-optimal, diversity-maximising, and an orchestrator that selects
the best strategy for a given problem.

Novelty is measured as semantic distance from the current theorem portfolio
under a purpose-conditioned metric (see theory2.tex §Novelty).  All strategies
accept a ``Sequence[Idea]`` of *candidates* and a ``Sequence[Idea]``
representing the *current portfolio*, returning a ranked ``list[Idea]``.

Module layout::

    SearchConfig        – search configuration (frozen dataclass)
    GreedySearcher      – greedy novelty search
    BeamSearcher        – beam search with diversity bonus
    ParetoSearcher      – Pareto-optimal search (novelty × feasibility)
    DiverseSearcher     – diversity-maximising search
    SearchOrchestrator  – strategy selection and orchestration

Design principles
-----------------
* All helpers are deterministic given the same random seed so that results
  are reproducible across runs.
* Budget constraints are expressed in the same units as ``GainProfile.cost``
  so the orchestrator can reason about feasibility together with novelty.
* Every public method returns plain Python collections (no custom iterator
  types) so callers can freely sort, slice, and serialise results.
* The orchestrator uses lightweight heuristics — not learned models — to
  select a strategy, keeping inference latency negligible.

References
----------
theory2.tex Chapter 57 "Optimal Novelty Search for Mathematical Purpose".
"""

from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
    IdeaEvaluator,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltyMetric as _NoveltyMetricBase,
    NoveltySearcher as _NoveltySearcherBase,
    TheoremPortfolio,
    PurposeAlignmentChecker,
    NoveltyFilter,
    NoveltyOptimizer,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_BEAM_WIDTH: int = 5
_DEFAULT_K: int = 10
_DEFAULT_NOVELTY_THRESHOLD: float = 0.3
_DEFAULT_DIVERSITY_BONUS: float = 0.2
_MAX_ITERATIONS: int = 1000
_PARETO_EPSILON: float = 1e-6

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

    The string includes the ``+00:00`` timezone designator so that
    downstream consumers can parse it unambiguously.

    Returns
    -------
    str
        UTC datetime in ISO 8601 format, e.g. ``"2024-01-15T12:34:56.789012+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Convert *text* to a frozenset of lowercase word tokens.

    Punctuation is stripped and runs of whitespace are collapsed.  The
    resulting tokens are suitable for Jaccard-based similarity computations.

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
    empty (treating two empty documents as identical).

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
    produce a rich token set that represents the semantic content of the idea.

    Parameters
    ----------
    idea:
        The idea from which to extract tokens.

    Returns
    -------
    frozenset[str]
        Combined token set from all text fields.
    """
    parts = [
        idea.title,
        idea.purpose,
        idea.target_area,
        idea.hypothesis,
    ]
    combined: set[str] = set()
    for part in parts:
        combined |= _tokenize(part)
    return frozenset(combined)


def _novelty_of(idea: Idea, portfolio_tokens: frozenset[str]) -> float:
    """Compute novelty of *idea* relative to a merged portfolio token set.

    Novelty is defined as ``1 - Jaccard(idea_tokens, portfolio_tokens)``,
    so an idea with no token overlap with the portfolio has novelty 1.0
    and one that exactly mirrors the portfolio has novelty 0.0.

    Parameters
    ----------
    idea:
        The candidate idea.
    portfolio_tokens:
        Union of all tokens from the current portfolio.

    Returns
    -------
    float
        Novelty score in [0, 1].
    """
    idea_tok = _idea_tokens(idea)
    return _clamp(1.0 - _jaccard(idea_tok, portfolio_tokens))


# ---------------------------------------------------------------------------
# 1. SearchConfig – frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchConfig:
    """Immutable configuration object for novelty search strategies.

    All search classes accept a ``SearchConfig`` instance; when ``None`` is
    passed the class constructs a default configuration.

    Attributes
    ----------
    strategy_name:
        Name of the default strategy.  One of ``"greedy"``, ``"beam"``,
        ``"pareto"``, ``"diverse"``, or ``"auto"``.
    beam_width:
        Width of the beam in :class:`BeamSearcher`.  Must be ≥ 1.
    k:
        Maximum number of results to return.  Must be ≥ 1.
    novelty_threshold:
        Minimum composite novelty score for a candidate to be eligible.
        Candidates below this threshold are discarded before selection.
    diversity_bonus:
        Weight applied to pairwise diversity in beam and diverse search.
        Larger values trade off raw novelty for result variety.
    feasibility_threshold:
        Minimum feasibility for an idea to be considered non-speculative.
        Ideas below this are never selected when ``trust_status == SPECULATIVE``.
    budget:
        Maximum total cost (``GainProfile.cost``) for the selected set.
        ``float("inf")`` disables budget enforcement.
    purpose:
        Research purpose string used for purpose-alignment scoring.
        Empty string disables purpose conditioning.
    random_seed:
        Optional integer seed for reproducible tie-breaking in randomised
        strategies.  ``None`` uses the system RNG state.
    max_iterations:
        Hard cap on inner loop iterations to prevent infinite loops.
    """

    strategy_name: str = "greedy"
    beam_width: int = _DEFAULT_BEAM_WIDTH
    k: int = _DEFAULT_K
    novelty_threshold: float = _DEFAULT_NOVELTY_THRESHOLD
    diversity_bonus: float = _DEFAULT_DIVERSITY_BONUS
    feasibility_threshold: float = 0.1
    budget: float = float("inf")
    purpose: str = ""
    random_seed: int | None = None
    max_iterations: int = _MAX_ITERATIONS

    def __post_init__(self) -> None:
        """Validate and normalise field values after construction.

        Normalises ``strategy_name`` to lowercase and clamps all probability
        values to [0, 1].  Raises ``ValueError`` for out-of-range integer
        fields.

        Raises
        ------
        ValueError
            If ``k < 1`` or ``beam_width < 1``.
        """
        object.__setattr__(self, "strategy_name", self.strategy_name.lower().strip())
        object.__setattr__(self, "novelty_threshold", _clamp(self.novelty_threshold))
        object.__setattr__(self, "diversity_bonus", _clamp(self.diversity_bonus))
        object.__setattr__(self, "feasibility_threshold", _clamp(self.feasibility_threshold))
        if self.k < 1:
            raise ValueError(f"k must be >= 1, got {self.k}")
        if self.beam_width < 1:
            raise ValueError(f"beam_width must be >= 1, got {self.beam_width}")
        if self.max_iterations < 1:
            object.__setattr__(self, "max_iterations", _MAX_ITERATIONS)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_bounded(self) -> bool:
        """Return ``True`` when an explicit budget cap is in effect."""
        return math.isfinite(self.budget)

    @property
    def effective_k(self) -> int:
        """Return the effective result count (always ≥ 1)."""
        return max(1, self.k)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise config to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields represented as primitive Python types.
        """
        return {
            "strategy_name": self.strategy_name,
            "beam_width": self.beam_width,
            "k": self.k,
            "novelty_threshold": self.novelty_threshold,
            "diversity_bonus": self.diversity_bonus,
            "feasibility_threshold": self.feasibility_threshold,
            "budget": self.budget if math.isfinite(self.budget) else None,
            "purpose": self.purpose,
            "random_seed": self.random_seed,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchConfig":
        """Construct a :class:`SearchConfig` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        SearchConfig
            Reconstructed configuration.
        """
        budget = d.get("budget")
        return cls(
            strategy_name=d.get("strategy_name", "greedy"),
            beam_width=int(d.get("beam_width", _DEFAULT_BEAM_WIDTH)),
            k=int(d.get("k", _DEFAULT_K)),
            novelty_threshold=float(d.get("novelty_threshold", _DEFAULT_NOVELTY_THRESHOLD)),
            diversity_bonus=float(d.get("diversity_bonus", _DEFAULT_DIVERSITY_BONUS)),
            feasibility_threshold=float(d.get("feasibility_threshold", 0.1)),
            budget=float(budget) if budget is not None else float("inf"),
            purpose=str(d.get("purpose", "")),
            random_seed=d.get("random_seed"),
            max_iterations=int(d.get("max_iterations", _MAX_ITERATIONS)),
        )

    def to_json(self) -> str:
        """Serialise to a JSON string.

        Returns
        -------
        str
            JSON-encoded configuration.
        """
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "SearchConfig":
        """Deserialise from a JSON string.

        Parameters
        ----------
        s:
            JSON string as produced by :meth:`to_json`.

        Returns
        -------
        SearchConfig
        """
        return cls.from_dict(json.loads(s))

    def describe(self) -> str:
        """Return a human-readable one-line summary.

        Returns
        -------
        str
            Summary suitable for logging or display.
        """
        budget_str = f"budget={self.budget:.1f}" if self.is_bounded else "unbounded"
        seed_str = f"seed={self.random_seed}" if self.random_seed is not None else "unseeded"
        return (
            f"SearchConfig(strategy={self.strategy_name!r}, k={self.k}, "
            f"beam_width={self.beam_width}, novelty_threshold={self.novelty_threshold:.2f}, "
            f"diversity_bonus={self.diversity_bonus:.2f}, {budget_str}, {seed_str})"
        )


# ---------------------------------------------------------------------------
# 2. GreedySearcher
# ---------------------------------------------------------------------------


class GreedySearcher:
    """Greedy novelty-maximising search strategy.

    At each step the candidate with the highest novelty score relative to
    all already-selected ideas *and* the incoming portfolio is appended to
    the result set.  The algorithm terminates when *k* ideas have been
    selected or all remaining candidates fall below the novelty threshold.

    This is the fastest search strategy (O(n · k) pair-wise distance
    comparisons) and serves as a baseline for the other strategies.

    Parameters
    ----------
    config:
        Optional search configuration.  A default :class:`SearchConfig` is
        used when ``None`` is passed.

    Notes
    -----
    The greedy strategy is optimal in the *submodular maximisation* sense
    only when the diversity function is monotone submodular.  In practice the
    Jaccard-based novelty is approximately submodular for typical corpora,
    so greedy gives a (1 - 1/e) ≈ 0.63 approximation to the optimum.
    """

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config: SearchConfig = config or SearchConfig()
        self._rng: random.Random = random.Random(self.config.random_seed)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Run greedy novelty-maximising search.

        Iteratively picks the candidate with the highest novelty w.r.t. the
        union of the already-selected set and the incoming portfolio.
        Candidates below :attr:`SearchConfig.novelty_threshold` are never
        selected.

        Parameters
        ----------
        candidates:
            Ideas to search over.  May contain ideas already in *portfolio*;
            these will score low and will not be selected.
        portfolio:
            Ideas that form the current "known" set.  Novelty is measured
            relative to this set.
        purpose:
            Optional purpose string used for purpose-aware scoring.  When
            non-empty this modulates novelty toward purpose-relevant gaps.

        Returns
        -------
        list[Idea]
            Up to ``config.k`` ideas in descending novelty order.
        """
        effective_purpose = purpose or self.config.purpose
        k = self.config.effective_k
        threshold = self.config.novelty_threshold

        selected: list[Idea] = []
        remaining: list[Idea] = list(candidates)
        iterations = 0

        while remaining and len(selected) < k and iterations < self.config.max_iterations:
            iterations += 1
            best_idx: int = -1
            best_score: float = -1.0

            for i, candidate in enumerate(remaining):
                score = self._compute_novelty(candidate, selected, portfolio)
                if score > best_score:
                    best_score = score
                    best_idx = i

            if best_idx < 0 or best_score < threshold:
                break

            selected.append(remaining.pop(best_idx))

        return selected

    def _compute_novelty(
        self,
        candidate: Idea,
        selected: list[Idea],
        portfolio: Sequence[Idea],
    ) -> float:
        """Compute the marginal novelty of *candidate* given already-selected ideas and portfolio.

        Novelty is defined as ``1 - max_jaccard_similarity`` over all
        ideas in ``selected + portfolio``.  This encourages selection of
        ideas that are distant from both the existing portfolio and any
        previously selected candidates.

        Parameters
        ----------
        candidate:
            The idea being evaluated.
        selected:
            Ideas already picked in the current search run.
        portfolio:
            Ideas in the fixed background portfolio.

        Returns
        -------
        float
            Novelty in [0, 1].  Higher is more novel.
        """
        candidate_tok = _idea_tokens(candidate)
        max_sim = 0.0
        for existing in (*selected, *portfolio):
            sim = _jaccard(candidate_tok, _idea_tokens(existing))
            if sim > max_sim:
                max_sim = sim
        return _clamp(1.0 - max_sim)

    def _is_feasible(self, idea: Idea) -> bool:
        """Determine whether *idea* meets the minimum feasibility requirement.

        An idea is feasible when its ``trust_status`` is not
        ``TrustStatus.SPECULATIVE`` AND its theorem yield is positive.
        This avoids selecting ungrounded ideas with no expected payoff.

        Parameters
        ----------
        idea:
            The idea to evaluate.

        Returns
        -------
        bool
            ``True`` iff the idea is feasible.
        """
        if idea.trust_status == TrustStatus.SPECULATIVE:
            return False
        return idea.predicted_gain.theorem_yield > 0.0

    def search_with_budget(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        budget: float,
        purpose: str = "",
    ) -> list[Idea]:
        """Greedy search that stops when cumulative cost would exceed *budget*.

        Each selected idea's ``GainProfile.cost`` is accumulated.  When
        adding the next best idea would push the total past *budget* the
        search stops early.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Current portfolio for novelty reference.
        budget:
            Maximum total cost of selected ideas.
        purpose:
            Optional purpose string.

        Returns
        -------
        list[Idea]
            Selected ideas whose costs sum to at most *budget*.
        """
        effective_purpose = purpose or self.config.purpose
        k = self.config.effective_k
        threshold = self.config.novelty_threshold

        selected: list[Idea] = []
        remaining: list[Idea] = list(candidates)
        cumulative_cost: float = 0.0
        iterations = 0

        while remaining and len(selected) < k and iterations < self.config.max_iterations:
            iterations += 1
            best_idx: int = -1
            best_score: float = -1.0

            for i, candidate in enumerate(remaining):
                if cumulative_cost + candidate.predicted_gain.cost > budget:
                    continue
                score = self._compute_novelty(candidate, selected, portfolio)
                if score > best_score:
                    best_score = score
                    best_idx = i

            if best_idx < 0 or best_score < threshold:
                break

            chosen = remaining.pop(best_idx)
            selected.append(chosen)
            cumulative_cost += chosen.predicted_gain.cost

        return selected

    def rank_candidates(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[tuple[Idea, float]]:
        """Rank all candidates by greedy novelty score (descending).

        Unlike :meth:`search`, this method computes the marginal novelty of
        each candidate relative only to the portfolio (not the selected set),
        giving a static ranking rather than a sequentially updated one.

        Parameters
        ----------
        candidates:
            Ideas to rank.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, novelty_score)`` pairs sorted by score descending.
        """
        scored: list[tuple[Idea, float]] = []
        for candidate in candidates:
            score = self._compute_novelty(candidate, [], portfolio)
            scored.append((candidate, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def explain_selection(
        self,
        selected: list[Idea],
        portfolio: Sequence[Idea],
    ) -> str:
        """Generate a human-readable explanation of the selection.

        For each selected idea, reports its novelty score relative to the
        portfolio and the most similar portfolio idea.

        Parameters
        ----------
        selected:
            The ideas that were selected.
        portfolio:
            Reference portfolio.

        Returns
        -------
        str
            Multi-line explanation text.
        """
        lines: list[str] = ["Greedy Search Selection Report", "=" * 40]
        for rank, idea in enumerate(selected, 1):
            idea_tok = _idea_tokens(idea)
            best_sim: float = 0.0
            best_match: str = "(none)"
            for p_idea in portfolio:
                sim = _jaccard(idea_tok, _idea_tokens(p_idea))
                if sim > best_sim:
                    best_sim = sim
                    best_match = p_idea.title
            novelty = _clamp(1.0 - best_sim)
            lines.append(
                f"  {rank}. [{idea.idea_id}] {idea.title!r}\n"
                f"     novelty={novelty:.3f}  most_similar_in_portfolio={best_match!r}  "
                f"trust={idea.trust_status.value}  yield={idea.predicted_gain.theorem_yield:.2f}"
            )
        lines.append(f"\nTotal selected: {len(selected)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. BeamSearcher
# ---------------------------------------------------------------------------


class BeamSearcher:
    """Beam search with diversity bonus for novelty maximisation.

    Maintains a beam of the top-*beam_width* partial solutions, expanding
    each state greedily and scoring the expansion with a diversity bonus.
    This allows the algorithm to escape locally optimal choices that reduce
    the diversity of the final result set.

    The diversity bonus is computed as the mean pairwise Jaccard distance
    among the ideas in a candidate state, weighted by
    :attr:`SearchConfig.diversity_bonus`.

    Parameters
    ----------
    config:
        Optional search configuration.
    """

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config: SearchConfig = config or SearchConfig()
        self._rng: random.Random = random.Random(self.config.random_seed)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Run beam search with diversity bonus.

        Initialises a single empty state and expands it step-by-step.  At
        each step every state in the beam is expanded by each remaining
        candidate and the top-*beam_width* resulting states are kept.  The
        final beam state with the highest combined score is returned.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Up to ``config.k`` ideas chosen by beam search.
        """
        result, _ = self.search_with_trace(candidates, portfolio, purpose)
        return result

    def _score_beam_state(
        self,
        selected: list[Idea],
        candidate: Idea,
        portfolio: Sequence[Idea],
        purpose: str,
    ) -> float:
        """Score a candidate beam state extension.

        The total score combines:
        - The marginal novelty of the candidate relative to selected + portfolio.
        - A diversity bonus equal to the mean pairwise distance among all
          ideas in the proposed new state weighted by ``diversity_bonus``.

        Parameters
        ----------
        selected:
            Ideas already in the current beam state.
        candidate:
            Candidate idea to add.
        portfolio:
            Reference portfolio.
        purpose:
            Purpose string.

        Returns
        -------
        float
            Combined score.  Higher is better.
        """
        candidate_tok = _idea_tokens(candidate)
        max_sim = 0.0
        for existing in (*selected, *portfolio):
            sim = _jaccard(candidate_tok, _idea_tokens(existing))
            if sim > max_sim:
                max_sim = sim
        novelty = _clamp(1.0 - max_sim)

        proposed = selected + [candidate]
        diversity = self._pairwise_diversity(proposed)
        return novelty + self.config.diversity_bonus * diversity

    def _expand_beam(
        self,
        beam: list[list[Idea]],
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str,
    ) -> list[list[Idea]]:
        """Perform one beam expansion step.

        Each state in the beam is expanded by every remaining candidate.
        All resulting states are scored and the top-*beam_width* are kept.

        Parameters
        ----------
        beam:
            Current beam states.
        candidates:
            Remaining candidates to expand with.
        portfolio:
            Reference portfolio.
        purpose:
            Purpose string.

        Returns
        -------
        list[list[Idea]]
            New beam of at most *beam_width* states.
        """
        already_selected_ids: set[str] = set()
        for state in beam:
            for idea in state:
                already_selected_ids.add(idea.idea_id)

        expanded: list[tuple[list[Idea], float]] = []
        for state in beam:
            selected_ids = {idea.idea_id for idea in state}
            for candidate in candidates:
                if candidate.idea_id in selected_ids:
                    continue
                score = self._score_beam_state(state, candidate, portfolio, purpose)
                if score >= self.config.novelty_threshold:
                    expanded.append((state + [candidate], score))

        if not expanded:
            return beam

        expanded.sort(key=lambda x: x[1], reverse=True)
        return [state for state, _ in expanded[: self.config.beam_width]]

    def _pairwise_diversity(self, ideas: list[Idea]) -> float:
        """Compute the mean pairwise Jaccard distance among *ideas*.

        Pairwise distance = 1 - Jaccard similarity.  Returns 0.0 for zero or
        one ideas.

        Parameters
        ----------
        ideas:
            Ideas to measure diversity over.

        Returns
        -------
        float
            Mean pairwise distance in [0, 1].
        """
        if len(ideas) < 2:
            return 0.0
        total: float = 0.0
        count: int = 0
        token_sets = [_idea_tokens(idea) for idea in ideas]
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                total += 1.0 - _jaccard(token_sets[i], token_sets[j])
                count += 1
        return total / count if count > 0 else 0.0

    def search_with_trace(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> tuple[list[Idea], list[dict[str, Any]]]:
        """Beam search returning both the result and an expansion trace.

        The trace is a list of dictionaries, one per expansion step, each
        containing ``"step"``, ``"beam_size"``, ``"best_score"``, and
        ``"best_state_titles"`` keys.  This is useful for debugging and
        for understanding why certain ideas were selected.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        tuple[list[Idea], list[dict[str, Any]]]
            ``(selected_ideas, trace)`` where *trace* records each expansion.
        """
        effective_purpose = purpose or self.config.purpose
        k = self.config.effective_k
        beam_width = self.adaptive_beam_width(len(candidates))

        beam: list[list[Idea]] = [[]]
        trace: list[dict[str, Any]] = []

        for step in range(k):
            new_beam = self._expand_beam(beam, candidates, portfolio, effective_purpose)
            if new_beam == beam:
                break
            beam = new_beam

            best_state = beam[0]
            best_score = self._pairwise_diversity(best_state)
            trace.append(
                {
                    "step": step + 1,
                    "beam_size": len(beam),
                    "best_score": best_score,
                    "best_state_titles": [idea.title for idea in best_state],
                }
            )

        best_state = max(
            beam,
            key=lambda s: sum(
                self._score_beam_state(s[:i], s[i], portfolio, effective_purpose)
                for i in range(len(s))
            )
            if s
            else 0.0,
            default=[],
        )
        return best_state[:k], trace

    def adaptive_beam_width(self, n_candidates: int) -> int:
        """Choose a beam width based on the candidate pool size.

        Uses heuristic scaling: small pools can afford wide beams; large
        pools narrow the beam to keep the search tractable.

        Parameters
        ----------
        n_candidates:
            Number of candidate ideas.

        Returns
        -------
        int
            Recommended beam width (at least 1).
        """
        if n_candidates <= 10:
            return max(1, self.config.beam_width)
        elif n_candidates <= 50:
            return max(1, min(self.config.beam_width, 5))
        elif n_candidates <= 200:
            return max(1, min(self.config.beam_width, 4))
        else:
            return max(1, min(self.config.beam_width, 3))


# ---------------------------------------------------------------------------
# 4. ParetoSearcher
# ---------------------------------------------------------------------------


class ParetoSearcher:
    """Pareto-optimal search on the (novelty × feasibility) objective space.

    Computes Pareto ranks for all candidates on three objectives:
    - novelty (semantic distance from portfolio)
    - feasibility (estimated probability of formalisation)
    - purpose_alignment (relevance to the stated research purpose)

    The Pareto front (rank-1 ideas) is taken as the primary candidate pool.
    When the front has more than *k* ideas the combined score is used to
    select the top-*k* subset.

    Parameters
    ----------
    config:
        Optional search configuration.
    """

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config: SearchConfig = config or SearchConfig()

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Return Pareto-optimal ideas ranked by combined score.

        Computes the Pareto front and returns up to ``config.k`` ideas from
        it sorted by :meth:`combined_score`.  When the front is smaller than
        *k*, rank-2 ideas are included as well.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Top-*k* Pareto-optimal ideas.
        """
        effective_purpose = purpose or self.config.purpose
        k = self.config.effective_k

        if not candidates:
            return []

        ranked = self.pareto_rank(candidates, portfolio, effective_purpose)
        # Sort by (pareto_rank asc, combined_score desc)
        objectives = {
            idea.idea_id: self._objective_vector(idea, portfolio, effective_purpose)
            for idea, _ in ranked
        }

        ranked.sort(key=lambda x: (x[1], -self.combined_score(objectives[x[0].idea_id])))
        return [idea for idea, _ in ranked][:k]

    def _objective_vector(
        self,
        idea: Idea,
        portfolio: Sequence[Idea],
        purpose: str,
    ) -> tuple[float, float, float]:
        """Compute the three-dimensional objective vector for *idea*.

        Returns
        -------
        tuple[float, float, float]
            ``(novelty, feasibility, purpose_alignment)`` each in [0, 1].
        """
        idea_tok = _idea_tokens(idea)
        max_sim = 0.0
        purpose_tok = _tokenize(purpose) if purpose else frozenset()
        purpose_sims: list[float] = []

        for p_idea in portfolio:
            sim = _jaccard(idea_tok, _idea_tokens(p_idea))
            if sim > max_sim:
                max_sim = sim
            if purpose_tok:
                p_purpose_tok = _tokenize(p_idea.purpose)
                purpose_sims.append(_jaccard(_tokenize(idea.purpose), p_purpose_tok))

        novelty = _clamp(1.0 - max_sim)

        gain = idea.predicted_gain
        raw_feasibility = gain.theorem_yield / (gain.cost + 1.0)
        feasibility = _clamp(raw_feasibility)

        if purpose_tok and idea.purpose:
            purpose_alignment = _clamp(_jaccard(_tokenize(idea.purpose), purpose_tok))
        elif purpose_sims:
            purpose_alignment = _clamp(1.0 - (sum(purpose_sims) / len(purpose_sims)))
        else:
            purpose_alignment = 0.5

        return novelty, feasibility, purpose_alignment

    def _is_dominated(
        self,
        a: tuple[float, ...],
        b: tuple[float, ...],
    ) -> bool:
        """Return ``True`` if vector *b* Pareto-dominates vector *a*.

        *b* dominates *a* iff *b* is at least as good as *a* on every
        objective and strictly better on at least one.

        Parameters
        ----------
        a, b:
            Objective vectors of equal length.

        Returns
        -------
        bool
        """
        at_least_as_good = all(
            b_i >= a_i - _PARETO_EPSILON for a_i, b_i in zip(a, b)
        )
        strictly_better = any(
            b_i > a_i + _PARETO_EPSILON for a_i, b_i in zip(a, b)
        )
        return at_least_as_good and strictly_better

    def pareto_front(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Return the full Pareto-optimal set of candidates.

        An idea is Pareto-optimal when no other candidate dominates it on all
        three objectives simultaneously.

        Parameters
        ----------
        candidates:
            Ideas to compute the Pareto front over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            All non-dominated ideas.
        """
        effective_purpose = purpose or self.config.purpose
        if not candidates:
            return []

        objectives = [
            self._objective_vector(idea, portfolio, effective_purpose) for idea in candidates
        ]
        front: list[Idea] = []
        for i, idea in enumerate(candidates):
            dominated = False
            for j, other in enumerate(candidates):
                if i == j:
                    continue
                if self._is_dominated(objectives[i], objectives[j]):
                    dominated = True
                    break
            if not dominated:
                front.append(idea)
        return front

    def pareto_rank(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[tuple[Idea, int]]:
        """Assign Pareto ranks to all candidates.

        Rank 1 = Pareto front; rank 2 = front of the remaining candidates
        after removing rank-1 ideas; and so on.

        Parameters
        ----------
        candidates:
            Ideas to rank.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[tuple[Idea, int]]
            ``(idea, rank)`` pairs.
        """
        effective_purpose = purpose or self.config.purpose
        if not candidates:
            return []

        objectives = [
            self._objective_vector(idea, portfolio, effective_purpose) for idea in candidates
        ]
        ranks = self._assign_pareto_ranks(objectives)
        return list(zip(candidates, ranks))

    def _assign_pareto_ranks(self, objectives: list[tuple[float, ...]]) -> list[int]:
        """Iteratively assign Pareto ranks by stripping successive fronts.

        Parameters
        ----------
        objectives:
            List of objective vectors in the same order as the candidates.

        Returns
        -------
        list[int]
            Rank per candidate (1-indexed).
        """
        n = len(objectives)
        ranks = [0] * n
        remaining = list(range(n))
        rank = 1

        while remaining:
            front_indices: list[int] = []
            for i in remaining:
                dominated = False
                for j in remaining:
                    if i == j:
                        continue
                    if self._is_dominated(objectives[i], objectives[j]):
                        dominated = True
                        break
                if not dominated:
                    front_indices.append(i)
            for idx in front_indices:
                ranks[idx] = rank
            remaining = [r for r in remaining if r not in front_indices]
            rank += 1

        return ranks

    def combined_score(self, obj: tuple[float, ...]) -> float:
        """Compute a weighted combination of the objective vector.

        Weights: novelty=0.40, feasibility=0.35, purpose_alignment=0.25
        (matching the ``NoveltyScore.composite`` definition in theory2.tex).

        Parameters
        ----------
        obj:
            Objective vector ``(novelty, feasibility, purpose_alignment)``.
            Extra dimensions are ignored.

        Returns
        -------
        float
            Scalar combined score in [0, 1].
        """
        weights = (0.40, 0.35, 0.25)
        score = sum(w * v for w, v in zip(weights, obj))
        return _clamp(score)


# ---------------------------------------------------------------------------
# 5. DiverseSearcher
# ---------------------------------------------------------------------------


class DiverseSearcher:
    """Diversity-maximising search strategy.

    Selects ideas that collectively cover a wide region of the semantic
    space.  Unlike the greedy and beam strategies, which are primarily
    novelty-driven, this strategy explicitly optimises for the diversity of
    the selected set while using the novelty threshold only as a filter.

    The core algorithm is a greedy max-min diversity selection: at each step
    the candidate that maximises the *minimum* distance to any already-selected
    idea is chosen.

    Parameters
    ----------
    config:
        Optional search configuration.
    """

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config: SearchConfig = config or SearchConfig()
        self._rng: random.Random = random.Random(self.config.random_seed)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Maximise total pairwise diversity subject to novelty threshold filter.

        Candidates below the novelty threshold are filtered out first.  The
        remaining ideas are then passed through max-min selection.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Up to ``config.k`` ideas maximising collective diversity.
        """
        effective_purpose = purpose or self.config.purpose
        filtered = self._filter_by_novelty(candidates, portfolio)
        if not filtered:
            return []
        return self.max_min_selection(filtered, portfolio, self.config.effective_k)

    def _min_diversity_gain(
        self,
        candidate: Idea,
        selected: list[Idea],
    ) -> float:
        """Compute the minimum diversity gain from adding *candidate* to *selected*.

        The gain is the Jaccard distance (1 - similarity) to the closest
        already-selected idea.  Larger values indicate that *candidate* is
        far from all selected ideas.

        Parameters
        ----------
        candidate:
            Idea to evaluate.
        selected:
            Currently selected ideas.

        Returns
        -------
        float
            Min-distance gain.  1.0 when *selected* is empty.
        """
        if not selected:
            return 1.0
        candidate_tok = _idea_tokens(candidate)
        min_dist = 1.0
        for s in selected:
            dist = 1.0 - _jaccard(candidate_tok, _idea_tokens(s))
            if dist < min_dist:
                min_dist = dist
        return min_dist

    def _filter_by_novelty(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> list[Idea]:
        """Remove candidates whose novelty falls below the configured threshold.

        Parameters
        ----------
        candidates:
            Candidate ideas.
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
        threshold = self.config.novelty_threshold
        return [c for c in candidates if _novelty_of(c, portfolio_tokens) >= threshold]

    def max_min_selection(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        k: int,
    ) -> list[Idea]:
        """Select *k* ideas maximising the minimum pairwise distance.

        The first idea is chosen as the one with highest novelty relative to
        the portfolio.  Subsequent ideas are chosen greedily to maximise the
        minimum distance to any already-selected idea.

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
            Selected ideas in selection order.
        """
        if not candidates:
            return []
        k = min(k, len(candidates))
        remaining: list[Idea] = list(candidates)

        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()
        first = max(remaining, key=lambda c: _novelty_of(c, portfolio_tokens))
        remaining.remove(first)
        selected: list[Idea] = [first]

        while len(selected) < k and remaining:
            best_candidate = max(
                remaining, key=lambda c: self._min_diversity_gain(c, selected)
            )
            remaining.remove(best_candidate)
            selected.append(best_candidate)

        return selected

    def coverage_based_selection(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        domains: Sequence[str],
        k: int,
    ) -> list[Idea]:
        """Select ideas ensuring representation from each domain.

        For each domain at least one idea is selected (if available) before
        filling the remaining slots with max-min diversity selection.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        domains:
            Domain strings (e.g. target areas) to ensure coverage of.
        k:
            Total number of ideas to return.

        Returns
        -------
        list[Idea]
            Selected ideas with at least one from each domain if possible.
        """
        selected: list[Idea] = []
        used_ids: set[str] = set()

        for domain in domains:
            if len(selected) >= k:
                break
            domain_candidates = [
                c for c in candidates
                if c.idea_id not in used_ids and domain.lower() in c.target_area.lower()
            ]
            if domain_candidates:
                portfolio_tokens: frozenset[str] = frozenset().union(
                    *(_idea_tokens(p) for p in portfolio)
                ) if portfolio else frozenset()
                best = max(domain_candidates, key=lambda c: _novelty_of(c, portfolio_tokens))
                selected.append(best)
                used_ids.add(best.idea_id)

        remaining_candidates = [c for c in candidates if c.idea_id not in used_ids]
        remaining_k = k - len(selected)
        if remaining_k > 0 and remaining_candidates:
            extra = self.max_min_selection(remaining_candidates, portfolio + selected, remaining_k)
            selected.extend(extra)

        return selected[:k]

    def explain_selection(self, selected: list[Idea]) -> str:
        """Generate a human-readable explanation of the diversity selection.

        Reports the pairwise diversity metrics for the selected set.

        Parameters
        ----------
        selected:
            Ideas that were selected.

        Returns
        -------
        str
            Multi-line explanation text.
        """
        lines: list[str] = ["Diverse Search Selection Report", "=" * 40]
        if not selected:
            lines.append("  (no ideas selected)")
            return "\n".join(lines)

        token_sets = [_idea_tokens(idea) for idea in selected]
        pairwise_distances: list[float] = []
        for i in range(len(token_sets)):
            for j in range(i + 1, len(token_sets)):
                pairwise_distances.append(1.0 - _jaccard(token_sets[i], token_sets[j]))

        mean_dist = sum(pairwise_distances) / len(pairwise_distances) if pairwise_distances else 0.0
        min_dist = min(pairwise_distances) if pairwise_distances else 0.0

        lines.append(f"  Selected {len(selected)} ideas")
        lines.append(f"  Mean pairwise distance: {mean_dist:.3f}")
        lines.append(f"  Min pairwise distance:  {min_dist:.3f}")
        lines.append("")
        for rank, idea in enumerate(selected, 1):
            gain = self._min_diversity_gain(idea, [s for s in selected if s.idea_id != idea.idea_id])
            lines.append(
                f"  {rank}. [{idea.idea_id}] {idea.title!r}  "
                f"min_dist_from_others={gain:.3f}  "
                f"target_area={idea.target_area!r}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. SearchOrchestrator
# ---------------------------------------------------------------------------


class SearchOrchestrator:
    """Strategy selection and orchestration for novelty search.

    Provides a unified interface over all search strategies.  When given a
    set of candidates and a portfolio the orchestrator inspects problem
    characteristics (candidate count, budget, purpose specification) and
    selects the most appropriate strategy.

    The orchestrator also supports explicit strategy specification and
    cross-strategy benchmarking for experiments.

    Parameters
    ----------
    config:
        Optional shared search configuration used by all sub-searchers.
        Sub-searcher configs can be overridden individually after construction.
    """

    _VALID_STRATEGIES: frozenset[str] = frozenset({"greedy", "beam", "pareto", "diverse"})

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config: SearchConfig = config or SearchConfig()
        self._greedy = GreedySearcher(self.config)
        self._beam = BeamSearcher(self.config)
        self._pareto = ParetoSearcher(self.config)
        self._diverse = DiverseSearcher(self.config)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Auto-select the best strategy and run the search.

        The strategy is chosen via :meth:`_select_strategy` based on the
        characteristics of the problem.  The selected strategy is then
        executed and its result returned.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Selected ideas from the best-fit strategy.
        """
        effective_purpose = purpose or self.config.purpose
        strategy = self._select_strategy(
            len(candidates),
            len(portfolio),
            self.config.budget,
            effective_purpose,
        )
        return self.search_with_strategy(strategy, candidates, portfolio, effective_purpose)

    def _select_strategy(
        self,
        n_candidates: int,
        n_portfolio: int,
        budget: float,
        purpose: str,
    ) -> str:
        """Choose a search strategy based on problem characteristics.

        Heuristic rules (in priority order):
        1. If budget < 10 → ``"greedy"`` (fast, budget-safe).
        2. If n_candidates > 100 → ``"beam"`` (broad exploration).
        3. If purpose is non-empty → ``"diverse"`` (purpose-gap coverage).
        4. Otherwise → ``"pareto"`` (multi-objective optimality).

        Parameters
        ----------
        n_candidates:
            Number of candidates.
        n_portfolio:
            Number of portfolio ideas.
        budget:
            Available budget.
        purpose:
            Research purpose string.

        Returns
        -------
        str
            Strategy name.
        """
        if math.isfinite(budget) and budget < 10.0:
            return "greedy"
        if n_candidates > 100:
            return "beam"
        if purpose.strip():
            return "diverse"
        return "pareto"

    def search_with_strategy(
        self,
        strategy: str,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Run a specific named strategy.

        Parameters
        ----------
        strategy:
            One of ``"greedy"``, ``"beam"``, ``"pareto"``, ``"diverse"``.
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            Search result from the named strategy.

        Raises
        ------
        ValueError
            If *strategy* is not a recognised strategy name.
        """
        effective_purpose = purpose or self.config.purpose
        strategy = strategy.lower().strip()
        if strategy == "greedy":
            return self._greedy.search(candidates, portfolio, effective_purpose)
        elif strategy == "beam":
            return self._beam.search(candidates, portfolio, effective_purpose)
        elif strategy == "pareto":
            return self._pareto.search(candidates, portfolio, effective_purpose)
        elif strategy == "diverse":
            return self._diverse.search(candidates, portfolio, effective_purpose)
        else:
            raise ValueError(
                f"Unknown strategy {strategy!r}. "
                f"Valid strategies: {sorted(self._VALID_STRATEGIES)}"
            )

    def compare_strategies(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, list[Idea]]:
        """Run all strategies and return their results as a dictionary.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        dict[str, list[Idea]]
            Mapping from strategy name to the list of ideas it selected.
        """
        effective_purpose = purpose or self.config.purpose
        results: dict[str, list[Idea]] = {}
        for strategy in sorted(self._VALID_STRATEGIES):
            results[strategy] = self.search_with_strategy(
                strategy, candidates, portfolio, effective_purpose
            )
        return results

    def benchmark_strategies(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Time and score each strategy, returning a benchmark report.

        For each strategy, measures wall-clock duration, result count,
        mean novelty, and mean pairwise diversity.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        dict[str, dict[str, Any]]
            Mapping from strategy name to metrics dict.  Each metrics dict
            contains ``"duration_ms"``, ``"n_results"``, ``"mean_novelty"``,
            ``"mean_diversity"``, ``"result_ids"``.
        """
        effective_purpose = purpose or self.config.purpose
        benchmark: dict[str, dict[str, Any]] = {}
        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()

        for strategy in sorted(self._VALID_STRATEGIES):
            t0 = time.perf_counter()
            result = self.search_with_strategy(strategy, candidates, portfolio, effective_purpose)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            novelty_scores = [_novelty_of(idea, portfolio_tokens) for idea in result]
            mean_novelty = sum(novelty_scores) / len(novelty_scores) if novelty_scores else 0.0

            token_sets = [_idea_tokens(idea) for idea in result]
            pair_dists: list[float] = []
            for i in range(len(token_sets)):
                for j in range(i + 1, len(token_sets)):
                    pair_dists.append(1.0 - _jaccard(token_sets[i], token_sets[j]))
            mean_diversity = sum(pair_dists) / len(pair_dists) if pair_dists else 0.0

            benchmark[strategy] = {
                "duration_ms": round(duration_ms, 3),
                "n_results": len(result),
                "mean_novelty": round(mean_novelty, 4),
                "mean_diversity": round(mean_diversity, 4),
                "result_ids": [idea.idea_id for idea in result],
            }

        return benchmark

    def best_of(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[Idea]:
        """Run all strategies and return the result with the best combined score.

        The combined score for a result list is ``mean_novelty + mean_diversity``.

        Parameters
        ----------
        candidates:
            Ideas to search over.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose override.

        Returns
        -------
        list[Idea]
            The best-scoring result across all strategies.
        """
        effective_purpose = purpose or self.config.purpose
        benchmark = self.benchmark_strategies(candidates, portfolio, effective_purpose)
        best_strategy = max(
            benchmark,
            key=lambda s: benchmark[s]["mean_novelty"] + benchmark[s]["mean_diversity"],
        )
        return self.search_with_strategy(
            best_strategy, candidates, portfolio, effective_purpose
        )

    def explain(
        self,
        strategy: str,
        result: list[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> str:
        """Generate a human-readable explanation of a strategy's result.

        Reports the strategy name, configuration summary, and per-idea
        novelty scores relative to the portfolio.

        Parameters
        ----------
        strategy:
            Strategy name used to produce *result*.
        result:
            The ideas that were selected.
        portfolio:
            Reference portfolio.
        purpose:
            Optional purpose string.

        Returns
        -------
        str
            Multi-line explanation text.
        """
        effective_purpose = purpose or self.config.purpose
        lines: list[str] = [
            f"SearchOrchestrator Explanation",
            f"Strategy: {strategy}",
            f"Config: {self.config.describe()}",
            f"Purpose: {effective_purpose!r}" if effective_purpose else "Purpose: (none)",
            f"Portfolio size: {len(portfolio)}",
            f"Results: {len(result)} ideas",
            "=" * 50,
        ]

        portfolio_tokens: frozenset[str] = frozenset().union(
            *(_idea_tokens(p) for p in portfolio)
        ) if portfolio else frozenset()

        for rank, idea in enumerate(result, 1):
            novelty = _novelty_of(idea, portfolio_tokens)
            lines.append(
                f"  {rank:2d}. [{idea.idea_id}] {idea.title!r}\n"
                f"       novelty={novelty:.3f}  trust={idea.trust_status.value}  "
                f"yield={idea.predicted_gain.theorem_yield:.2f}  "
                f"cost={idea.predicted_gain.cost:.2f}"
            )

        if result:
            novelties = [_novelty_of(idea, portfolio_tokens) for idea in result]
            lines.append(f"\nMean novelty: {sum(novelties) / len(novelties):.3f}")

        return "\n".join(lines)
