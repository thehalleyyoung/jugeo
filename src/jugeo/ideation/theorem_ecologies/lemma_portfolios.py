"""Lemma portfolio management (theory2.tex Ch61 §2).

Module layout::

    PortfolioConfig          – configuration for portfolio management
    LemmaUtilityEstimator    – estimates lemma utility in context
    ReuseTracker             – tracks lemma reuse across theorems
    CoverageCalculator       – calculates coverage of lemma portfolios
    PortfolioRebalancer      – rebalances portfolios for better coverage
    LemmaPortfolioManager    – orchestrates full portfolio management

Theory Background
=================

A *lemma portfolio* is a curated set of reusable lemmas that support a
collection of theorems.  The central economic metaphor is that lemmas have
*utility* — the degree to which they contribute to proving theorems — and
*coverage* — the fraction of target theorems that can make use of them.

Utility estimation is context-sensitive: the same lemma may be highly useful
for one set of theorems and irrelevant for another.  The
``LemmaUtilityEstimator`` computes a context-adjusted utility by combining
an intrinsic utility score (stored in the portfolio) with a context-match
bonus derived from tokenised string similarity, and a reuse bonus that
rewards frequently-used lemmas.

Reuse tracking via ``ReuseTracker`` records every (lemma, theorem) usage
event with a Unix timestamp.  The ``reuse_frequency`` method computes a
time-windowed event rate, which can be used to identify lemmas that were
heavily used in the recent past but have since fallen out of favour.

Coverage is computed by ``CoverageCalculator``, which estimates for each
target theorem the fraction of its token vocabulary that is covered by the
union of lemma token vocabularies in the portfolio.  A theorem is considered
*covered* when this fraction exceeds a configurable threshold.

Portfolio rebalancing in ``PortfolioRebalancer`` uses a greedy set-cover
heuristic: at each step it selects the candidate lemma that maximises the
marginal coverage gain, subject to a budget constraint on portfolio size.
Pruning removes lemmas whose utility falls below ``min_utility_threshold``.

The ``LemmaPortfolioManager`` ties all components together and maintains a
registry of named portfolios.  Its ``optimize`` method calls the rebalancer
and records the result as a ``PortfolioOptimization`` value object.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.ideation.theorem_ecologies.models import (
    LemmaPortfolio,
    TheoremEcology,
    PortfolioOptimization,
    EcologyHealth,
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenise *text* into lowercase alphabetic words of length >= 2."""
    return frozenset(w for w in re.split(r"[^a-z]+", text.lower()) if len(w) >= 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _uid() -> str:
    return str(uuid.uuid4())


def _ema(current: float, new_value: float, alpha: float) -> float:
    """Exponential moving average update."""
    return alpha * current + (1.0 - alpha) * new_value


# ---------------------------------------------------------------------------
# PortfolioConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioConfig:
    """Configuration for lemma portfolio management algorithms.

    Attributes
    ----------
    min_utility_threshold:
        Lemmas below this utility score are candidates for pruning.
    max_portfolio_size:
        Hard upper bound on the number of lemmas in any portfolio.
    target_coverage:
        Desired fraction of target theorems to be covered.
    reuse_bonus:
        Additional utility credited per unit of normalised reuse count.
    pruning_interval:
        Number of ``optimize`` calls between automatic pruning passes.
    coverage_decay:
        Per-cycle decay factor applied to coverage when lemmas are not reused.
    utility_smoothing:
        EMA weight for utility score updates (higher = more inertia).
    """

    min_utility_threshold: float = 0.1
    max_portfolio_size: int = 100
    target_coverage: float = 0.8
    reuse_bonus: float = 0.1
    pruning_interval: int = 10
    coverage_decay: float = 0.01
    utility_smoothing: float = 0.9

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_utility_threshold <= 1.0:
            raise ValueError("min_utility_threshold must be in [0, 1]")
        if self.max_portfolio_size < 1:
            raise ValueError("max_portfolio_size must be >= 1")
        if not 0.0 <= self.target_coverage <= 1.0:
            raise ValueError("target_coverage must be in [0, 1]")
        if not 0.0 <= self.utility_smoothing <= 1.0:
            raise ValueError("utility_smoothing must be in [0, 1]")

    def effective_utility(self, base: float, reuse_count: int) -> float:
        """Compute effective utility combining base score and reuse bonus.

        The reuse bonus is capped so that very frequent reuse cannot push the
        utility above 1.0.
        """
        bonus = self.reuse_bonus * math.log1p(reuse_count)
        return _clamp(base + bonus)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_utility_threshold": self.min_utility_threshold,
            "max_portfolio_size": self.max_portfolio_size,
            "target_coverage": self.target_coverage,
            "reuse_bonus": self.reuse_bonus,
            "pruning_interval": self.pruning_interval,
            "coverage_decay": self.coverage_decay,
            "utility_smoothing": self.utility_smoothing,
        }


# ---------------------------------------------------------------------------
# LemmaUtilityEstimator
# ---------------------------------------------------------------------------

class LemmaUtilityEstimator:
    """Estimates the utility of lemmas in different theorem-proving contexts.

    Utility is a combination of three signals:

    1. **Intrinsic utility** — the base score stored in the portfolio's
       ``utility_scores`` mapping.
    2. **Reuse bonus** — logarithmically scaled bonus for frequently-reused
       lemmas, calibrated by ``PortfolioConfig.reuse_bonus``.
    3. **Context match** — Jaccard similarity between the lemma token set and
       the union of the context theorem token sets, scaled to [0, 0.3].

    Parameters
    ----------
    config:
        Configuration controlling scoring weights.
    """

    def __init__(self, config: PortfolioConfig = PortfolioConfig()) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(
        self,
        lemma_id: str,
        context_theorems: list[str],
        portfolio: LemmaPortfolio,
    ) -> float:
        """Estimate utility of *lemma_id* in the context of *context_theorems*.

        Returns a value in [0, 1].
        """
        base = portfolio.utility_of(lemma_id)
        reuse = portfolio.reuse_count_of(lemma_id)
        bonus = self._config.reuse_bonus * math.log1p(reuse)
        ctx = self._context_match_score(lemma_id, context_theorems) * 0.3
        return _clamp(base + bonus + ctx)

    def batch_estimate(
        self,
        lemma_ids: list[str],
        context_theorems: list[str],
        portfolio: LemmaPortfolio,
    ) -> dict[str, float]:
        """Estimate utility for multiple lemmas in a single call."""
        return {lid: self.estimate(lid, context_theorems, portfolio)
                for lid in lemma_ids}

    def marginal_utility(
        self,
        lemma_id: str,
        portfolio: LemmaPortfolio,
        existing_coverage: float,
    ) -> float:
        """Estimate the marginal gain in coverage if *lemma_id* is added.

        Uses a diminishing-returns model: the marginal gain is scaled by the
        remaining coverage gap.
        """
        remaining = _clamp(1.0 - existing_coverage)
        base_utility = portfolio.utility_of(lemma_id)
        return _clamp(base_utility * remaining)

    def opportunity_cost(
        self,
        lemma_id: str,
        alternatives: list[str],
        portfolio: LemmaPortfolio,
    ) -> float:
        """Estimate the opportunity cost of including *lemma_id* vs alternatives.

        Defined as the utility of the best alternative minus the utility of
        *lemma_id*.  Negative values indicate *lemma_id* is better than all
        alternatives.
        """
        if not alternatives:
            return 0.0
        best_alt = max(portfolio.utility_of(a) for a in alternatives)
        return best_alt - portfolio.utility_of(lemma_id)

    def decay_utility(
        self,
        lemma_id: str,
        portfolio: LemmaPortfolio,
        time_since_use: float,
    ) -> float:
        """Compute decayed utility based on time elapsed since last use (seconds).

        Uses an exponential decay model with a half-life of 7 days (604800 s).
        """
        half_life = 604_800.0  # 7 days in seconds
        base = portfolio.utility_of(lemma_id)
        decay_factor = math.exp(-math.log(2.0) * time_since_use / half_life)
        return _clamp(base * decay_factor)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _context_match_score(
        self,
        lemma_id: str,
        context_theorems: list[str],
    ) -> float:
        """Jaccard similarity between lemma tokens and context theorem tokens."""
        lemma_tokens = _tokenize(lemma_id)
        if not context_theorems:
            return 0.0
        context_tokens: frozenset[str] = frozenset()
        for th in context_theorems:
            context_tokens = context_tokens | _tokenize(th)
        return _jaccard(lemma_tokens, context_tokens)


# ---------------------------------------------------------------------------
# ReuseTracker
# ---------------------------------------------------------------------------

class ReuseTracker:
    """Tracks lemma reuse across theorem-proving events.

    Every recorded use associates a lemma with a theorem and a timestamp,
    enabling both total counts and time-windowed frequency analysis.

    Internal state:
    ~~~~~~~~~~~~~~~
    * ``_reuse_log`` — list of (lemma_id, theorem_id, timestamp) tuples.
    * ``_counts`` — dict mapping lemma_id to total reuse count.
    """

    def __init__(self) -> None:
        self._reuse_log: list[tuple[str, str, float]] = []
        self._counts: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_use(self, lemma_id: str, theorem_id: str) -> None:
        """Record a single lemma usage event."""
        self._reuse_log.append((lemma_id, theorem_id, time.time()))
        self._counts[lemma_id] += 1

    def record_batch(self, uses: list[tuple[str, str]]) -> int:
        """Record multiple usage events in one call.  Returns the count added."""
        now = time.time()
        for lemma_id, theorem_id in uses:
            self._reuse_log.append((lemma_id, theorem_id, now))
            self._counts[lemma_id] += 1
        return len(uses)

    # ------------------------------------------------------------------
    # Counts and frequencies
    # ------------------------------------------------------------------

    def reuse_count(self, lemma_id: str) -> int:
        """Return total reuse count for *lemma_id*."""
        return self._counts.get(lemma_id, 0)

    def reuse_frequency(
        self, lemma_id: str, window_seconds: float = 86400.0
    ) -> float:
        """Return uses-per-second for *lemma_id* within the given time window."""
        now = time.time()
        cutoff = now - window_seconds
        count = sum(
            1
            for lid, _, ts in self._reuse_log
            if lid == lemma_id and ts >= cutoff
        )
        return count / window_seconds if window_seconds > 0 else 0.0

    def most_reused(self, k: int = 10) -> list[tuple[str, int]]:
        """Return the *k* most-reused lemmas as (lemma_id, count) pairs."""
        sorted_items = sorted(self._counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]

    def least_reused(self, k: int = 10) -> list[tuple[str, int]]:
        """Return the *k* least-reused lemmas as (lemma_id, count) pairs."""
        sorted_items = sorted(self._counts.items(), key=lambda x: x[1])
        return sorted_items[:k]

    def unused_lemmas(self, portfolio: LemmaPortfolio) -> list[str]:
        """Return lemma IDs in the portfolio that have never been recorded."""
        return [lid for lid in portfolio.lemma_ids if self._counts.get(lid, 0) == 0]

    def used_with(self, lemma_id: str) -> dict[str, int]:
        """Return a dict of theorem_id -> count for uses of *lemma_id*."""
        result: dict[str, int] = defaultdict(int)
        for lid, tid, _ in self._reuse_log:
            if lid == lemma_id:
                result[tid] += 1
        return dict(result)

    def co_occurrence(self, lemma_a: str, lemma_b: str) -> int:
        """Count events where both *lemma_a* and *lemma_b* were used in the same theorem.

        Two lemmas co-occur when they share at least one theorem in their
        ``used_with`` dictionaries.
        """
        theorems_a = set(self.used_with(lemma_a).keys())
        theorems_b = set(self.used_with(lemma_b).keys())
        return len(theorems_a & theorems_b)

    def reuse_matrix(self, lemma_ids: list[str]) -> list[list[int]]:
        """Compute a pairwise co-occurrence matrix for *lemma_ids*.

        Returns an N×N list of lists where entry [i][j] is the number of
        theorems that used both lemma i and lemma j.
        """
        n = len(lemma_ids)
        matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i, n):
                co = self.co_occurrence(lemma_ids[i], lemma_ids[j])
                matrix[i][j] = co
                matrix[j][i] = co
        return matrix

    def summary(self) -> dict[str, Any]:
        """Return a summary dict with aggregate reuse statistics."""
        total_events = len(self._reuse_log)
        n_unique_lemmas = len(self._counts)
        n_unique_theorems = len({tid for _, tid, _ in self._reuse_log})
        avg_reuse = (sum(self._counts.values()) / n_unique_lemmas
                     if n_unique_lemmas else 0.0)
        return {
            "total_events": total_events,
            "unique_lemmas_used": n_unique_lemmas,
            "unique_theorems_seen": n_unique_theorems,
            "average_reuse_per_lemma": avg_reuse,
            "most_reused_top5": self.most_reused(5),
        }


# ---------------------------------------------------------------------------
# CoverageCalculator
# ---------------------------------------------------------------------------

class CoverageCalculator:
    """Calculates how well a lemma portfolio covers a set of target theorems.

    Coverage is defined as the fraction of target theorems for which the
    portfolio provides at least *threshold* token-vocabulary overlap.  A
    lemma *covers* a theorem when their tokenised IDs share sufficient tokens.

    Parameters
    ----------
    target_theorems:
        List of theorem IDs that this calculator evaluates coverage over.
    """

    def __init__(self, target_theorems: list[str]) -> None:
        self._target_theorems = list(target_theorems)
        self._theorem_tokens: dict[str, frozenset[str]] = {
            th: _tokenize(th) for th in target_theorems
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(self, portfolio: LemmaPortfolio) -> float:
        """Return the fraction of target theorems covered (at threshold 0.5)."""
        if not self._target_theorems:
            return 0.0
        covered = sum(
            1 for th in self._target_theorems
            if self.theorem_coverage(th, portfolio) >= 0.5
        )
        return covered / len(self._target_theorems)

    def theorem_coverage(
        self, theorem_id: str, portfolio: LemmaPortfolio
    ) -> float:
        """Estimate coverage of a single theorem by the portfolio.

        Returns the maximum token-overlap (Jaccard) between the theorem's
        tokens and any lemma in the portfolio.
        """
        if not portfolio.lemma_ids:
            return 0.0
        th_tokens = self._theorem_tokens.get(theorem_id) or _tokenize(theorem_id)
        best = 0.0
        for lid in portfolio.lemma_ids:
            score = _jaccard(th_tokens, _tokenize(lid))
            if score > best:
                best = score
        return _clamp(best)

    def uncovered_theorems(
        self,
        portfolio: LemmaPortfolio,
        threshold: float = 0.5,
    ) -> list[str]:
        """Return theorems whose coverage falls below *threshold*."""
        return [
            th for th in self._target_theorems
            if self.theorem_coverage(th, portfolio) < threshold
        ]

    def coverage_by_lemma(
        self, portfolio: LemmaPortfolio
    ) -> dict[str, float]:
        """Return the fraction of target theorems each lemma covers individually."""
        result: dict[str, float] = {}
        for lid in portfolio.lemma_ids:
            lid_tokens = _tokenize(lid)
            covered = sum(
                1 for th in self._target_theorems
                if _jaccard(lid_tokens, self._theorem_tokens.get(th) or _tokenize(th)) >= 0.3
            )
            result[lid] = (covered / len(self._target_theorems)
                           if self._target_theorems else 0.0)
        return result

    def coverage_gap(self, portfolio: LemmaPortfolio) -> float:
        """Return how far the portfolio is from the target coverage (non-negative)."""
        from jugeo.ideation.theorem_ecologies.models import PortfolioOptimization as _PO  # noqa: F401
        # We need a config to get target_coverage; use a sensible default
        current = self.calculate(portfolio)
        target = 0.8  # default; callers that care can pass a PortfolioConfig
        return max(0.0, target - current)

    def marginal_coverage(
        self, new_lemma: str, portfolio: LemmaPortfolio
    ) -> float:
        """Estimate the additional coverage gained by adding *new_lemma*."""
        current = self.calculate(portfolio)
        # Simulate adding the lemma
        new_lemma_ids = tuple(list(portfolio.lemma_ids) + [new_lemma])
        temp_portfolio = replace(portfolio, lemma_ids=new_lemma_ids)
        new_coverage = self.calculate(temp_portfolio)
        return max(0.0, new_coverage - current)

    def coverage_report(self, portfolio: LemmaPortfolio) -> dict[str, Any]:
        """Return a comprehensive coverage report."""
        overall = self.calculate(portfolio)
        uncovered = self.uncovered_theorems(portfolio)
        by_lemma = self.coverage_by_lemma(portfolio)
        top_lemmas = sorted(by_lemma.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "overall_coverage": overall,
            "total_target_theorems": len(self._target_theorems),
            "uncovered_count": len(uncovered),
            "uncovered_theorems": uncovered[:20],
            "portfolio_size": portfolio.size,
            "top_contributing_lemmas": top_lemmas,
            "coverage_gap": self.coverage_gap(portfolio),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _lemma_covers_theorem(self, lemma_id: str, theorem_id: str) -> bool:
        """Return True if *lemma_id* sufficiently covers *theorem_id*."""
        lm_tokens = _tokenize(lemma_id)
        th_tokens = self._theorem_tokens.get(theorem_id) or _tokenize(theorem_id)
        return _jaccard(lm_tokens, th_tokens) >= 0.3


# ---------------------------------------------------------------------------
# PortfolioRebalancer
# ---------------------------------------------------------------------------

class PortfolioRebalancer:
    """Rebalances lemma portfolios for better coverage and efficiency.

    Rebalancing consists of two phases:

    1. **Pruning** — remove lemmas whose effective utility falls below
       ``PortfolioConfig.min_utility_threshold``.
    2. **Addition** — greedily add candidate lemmas that maximise marginal
       coverage, subject to ``max_portfolio_size``.

    Parameters
    ----------
    config:
        Configuration controlling pruning thresholds and size limits.
    coverage_calc:
        Optional pre-constructed ``CoverageCalculator``.  A default one
        operating over an empty target set is used if not provided.
    """

    def __init__(
        self,
        config: PortfolioConfig = PortfolioConfig(),
        coverage_calc: CoverageCalculator | None = None,
    ) -> None:
        self._config = config
        self._coverage_calc: CoverageCalculator = (
            coverage_calc if coverage_calc is not None else CoverageCalculator([])
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rebalance(
        self,
        portfolio: LemmaPortfolio,
        candidate_lemmas: list[str] | None = None,
    ) -> LemmaPortfolio:
        """Prune low-utility lemmas, then greedily add high-value candidates.

        Returns a new ``LemmaPortfolio`` reflecting the rebalanced state.
        """
        pruned = self.prune(portfolio)
        if candidate_lemmas:
            available = portfolio.size - pruned.size
            max_add = max(0, self._config.max_portfolio_size - pruned.size)
            pruned = self.add_candidates(pruned, candidate_lemmas, min(max_add, 10))
        return pruned

    def prune(self, portfolio: LemmaPortfolio) -> LemmaPortfolio:
        """Remove lemmas whose utility score falls below the threshold.

        Returns a new portfolio with the low-utility lemmas removed.
        The utility_scores and reuse_counts dicts are updated accordingly.
        """
        threshold = self._config.min_utility_threshold
        kept = tuple(
            lid for lid in portfolio.lemma_ids
            if portfolio.utility_of(lid) >= threshold
        )
        new_utility = {k: v for k, v in portfolio.utility_scores.items() if k in kept}
        new_reuse = {k: v for k, v in portfolio.reuse_counts.items() if k in kept}
        new_coverage = self._coverage_calc.calculate(
            replace(portfolio, lemma_ids=kept, utility_scores=new_utility)
        )
        return replace(
            portfolio,
            lemma_ids=kept,
            utility_scores=new_utility,
            reuse_counts=new_reuse,
            coverage=new_coverage,
        )

    def add_candidates(
        self,
        portfolio: LemmaPortfolio,
        candidates: list[str],
        max_add: int = 10,
    ) -> LemmaPortfolio:
        """Greedily add candidates that maximise marginal coverage.

        Candidates already in the portfolio are skipped.  At most *max_add*
        new lemmas are added.
        """
        current = portfolio
        existing = set(current.lemma_ids)
        remaining = [c for c in candidates if c not in existing]
        added = 0

        while remaining and added < max_add:
            if current.size >= self._config.max_portfolio_size:
                break
            # Score each candidate by marginal coverage
            scores = [
                (c, self._coverage_calc.marginal_coverage(c, current))
                for c in remaining
            ]
            best_candidate, best_score = max(scores, key=lambda x: x[1])
            if best_score <= 0.0 and added > 0:
                break
            # Add best candidate
            new_ids = tuple(list(current.lemma_ids) + [best_candidate])
            new_utility = dict(current.utility_scores)
            new_utility[best_candidate] = self._score_candidate(
                best_candidate, current
            )
            new_coverage = self._coverage_calc.calculate(
                replace(current, lemma_ids=new_ids)
            )
            current = replace(
                current,
                lemma_ids=new_ids,
                utility_scores=new_utility,
                coverage=new_coverage,
            )
            remaining.remove(best_candidate)
            added += 1

        return current

    def reweight(
        self,
        portfolio: LemmaPortfolio,
        reuse_tracker: ReuseTracker,
    ) -> LemmaPortfolio:
        """Update utility scores based on observed reuse data.

        Uses exponential moving average (EMA) to blend existing scores with
        reuse-adjusted scores.
        """
        alpha = self._config.utility_smoothing
        new_scores: dict[str, float] = {}
        max_reuse = max(
            (reuse_tracker.reuse_count(lid) for lid in portfolio.lemma_ids),
            default=1,
        )
        max_reuse = max(max_reuse, 1)

        for lid in portfolio.lemma_ids:
            old_score = portfolio.utility_of(lid)
            reuse_based = reuse_tracker.reuse_count(lid) / max_reuse
            new_score = _ema(old_score, reuse_based, alpha)
            new_scores[lid] = _clamp(new_score)

        return replace(portfolio, utility_scores=new_scores)

    def suggest_additions(
        self,
        portfolio: LemmaPortfolio,
        coverage_gap: float,
    ) -> list[str]:
        """Generate candidate lemma IDs to fill a coverage gap.

        Generates synthetic candidate IDs by suffixing existing lemma IDs
        with structural variants.  Real deployments would query a lemma
        database here.
        """
        suggestions: list[str] = []
        existing = set(portfolio.lemma_ids)
        for lid in list(portfolio.lemma_ids)[:5]:
            candidate = f"{lid}_extended"
            if candidate not in existing:
                suggestions.append(candidate)
        # Generate gap-scaled count of additional suggestions
        n_extra = max(1, int(coverage_gap * 10))
        for i in range(n_extra):
            suggestions.append(f"candidate_lemma_{i:03d}")
        return suggestions[:20]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_candidate(
        self, candidate: str, portfolio: LemmaPortfolio
    ) -> float:
        """Estimate utility score for a candidate not yet in the portfolio."""
        # Base score from token overlap with existing lemmas
        candidate_tokens = _tokenize(candidate)
        if not portfolio.lemma_ids:
            return 0.5
        overlaps = [
            _jaccard(candidate_tokens, _tokenize(lid))
            for lid in portfolio.lemma_ids
        ]
        mean_overlap = sum(overlaps) / len(overlaps)
        # Moderate overlap is good (complementary); high overlap is redundant
        target = 0.4
        score = 1.0 - abs(mean_overlap - target) / max(target, 1.0 - target)
        return _clamp(score)


# ---------------------------------------------------------------------------
# LemmaPortfolioManager
# ---------------------------------------------------------------------------

class LemmaPortfolioManager:
    """Full lemma portfolio management orchestrator.

    Maintains a registry of named ``LemmaPortfolio`` instances and
    coordinates all sub-components.

    Parameters
    ----------
    config:
        Configuration for all sub-components.
    target_theorems:
        Optional list of theorem IDs used by the ``CoverageCalculator``.
    """

    def __init__(
        self,
        config: PortfolioConfig = PortfolioConfig(),
        target_theorems: list[str] | None = None,
    ) -> None:
        self._config = config
        self._portfolios: dict[str, LemmaPortfolio] = {}
        self._reuse_tracker = ReuseTracker()
        self._utility_estimator = LemmaUtilityEstimator(config)
        self._coverage_calc = CoverageCalculator(target_theorems or [])
        self._rebalancer = PortfolioRebalancer(config, self._coverage_calc)
        self._optimize_call_count: int = 0

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def create_portfolio(
        self,
        name: str | None = None,
        initial_lemmas: list[str] | None = None,
    ) -> LemmaPortfolio:
        """Create, register, and return a new portfolio."""
        lemma_ids = tuple(initial_lemmas or [])
        utility_scores = {lid: 0.5 for lid in lemma_ids}
        portfolio = LemmaPortfolio(
            name=name or f"portfolio_{_uid()[:8]}",
            lemma_ids=lemma_ids,
            utility_scores=utility_scores,
        )
        self._portfolios[portfolio.portfolio_id] = portfolio
        return portfolio

    def add_portfolio(self, portfolio: LemmaPortfolio) -> None:
        """Register an externally constructed portfolio."""
        self._portfolios[portfolio.portfolio_id] = portfolio

    def get_portfolio(self, portfolio_id: str) -> LemmaPortfolio | None:
        """Return the portfolio with the given ID, or None."""
        return self._portfolios.get(portfolio_id)

    def update_portfolio(
        self, portfolio_id: str, portfolio: LemmaPortfolio
    ) -> None:
        """Replace the registered portfolio for *portfolio_id*."""
        self._portfolios[portfolio_id] = portfolio

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def record_use(
        self,
        lemma_id: str,
        theorem_id: str,
        portfolio_id: str | None = None,
    ) -> None:
        """Record a lemma usage event.

        If *portfolio_id* is provided and the portfolio exists, its
        reuse_counts are also updated.
        """
        self._reuse_tracker.record_use(lemma_id, theorem_id)
        if portfolio_id and portfolio_id in self._portfolios:
            portfolio = self._portfolios[portfolio_id]
            new_reuse = dict(portfolio.reuse_counts)
            new_reuse[lemma_id] = new_reuse.get(lemma_id, 0) + 1
            self._portfolios[portfolio_id] = replace(
                portfolio, reuse_counts=new_reuse
            )

    # ------------------------------------------------------------------
    # Optimization
    # ------------------------------------------------------------------

    def optimize(self, portfolio_id: str) -> PortfolioOptimization:
        """Rebalance a portfolio and record the optimization result.

        Runs pruning + greedy candidate addition and returns a
        ``PortfolioOptimization`` value object capturing the delta.
        """
        portfolio = self._portfolios.get(portfolio_id)
        if portfolio is None:
            raise KeyError(f"No portfolio with id '{portfolio_id}'")

        self._optimize_call_count += 1
        coverage_before = self._coverage_calc.calculate(portfolio)

        # First reweight based on observed reuse
        reweighted = self._rebalancer.reweight(portfolio, self._reuse_tracker)
        # Then rebalance
        suggestions = self._rebalancer.suggest_additions(reweighted, 0.2)
        rebalanced = self._rebalancer.rebalance(reweighted, suggestions)

        coverage_after = self._coverage_calc.calculate(rebalanced)
        utility_before = portfolio.average_utility()
        utility_after = rebalanced.average_utility()

        removed = tuple(
            lid for lid in portfolio.lemma_ids
            if lid not in rebalanced.lemma_ids
        )
        added = tuple(
            lid for lid in rebalanced.lemma_ids
            if lid not in portfolio.lemma_ids
        )

        self._portfolios[portfolio_id] = rebalanced

        return PortfolioOptimization(
            portfolio_id=portfolio_id,
            removed_lemmas=removed,
            added_lemmas=added,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            utility_improvement=utility_after - utility_before,
            notes=(
                f"Pruned {len(removed)} lemmas; added {len(added)} lemmas; "
                f"coverage {coverage_before:.3f} -> {coverage_after:.3f}"
            ),
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def assess_coverage(self, portfolio_id: str) -> dict[str, Any]:
        """Return a full coverage report for the given portfolio."""
        portfolio = self._portfolios.get(portfolio_id)
        if portfolio is None:
            raise KeyError(f"No portfolio with id '{portfolio_id}'")
        return self._coverage_calc.coverage_report(portfolio)

    def merge_portfolios(self, id_a: str, id_b: str) -> LemmaPortfolio:
        """Merge two portfolios by union of their lemma sets.

        Utility scores and reuse counts are combined by taking the max.
        """
        a = self._portfolios.get(id_a)
        b = self._portfolios.get(id_b)
        if a is None:
            raise KeyError(f"No portfolio with id '{id_a}'")
        if b is None:
            raise KeyError(f"No portfolio with id '{id_b}'")

        all_ids = list(dict.fromkeys(list(a.lemma_ids) + list(b.lemma_ids)))
        merged_utility: dict[str, float] = {}
        for lid in all_ids:
            merged_utility[lid] = max(a.utility_of(lid), b.utility_of(lid))
        merged_reuse: dict[str, int] = {}
        for lid in all_ids:
            merged_reuse[lid] = a.reuse_count_of(lid) + b.reuse_count_of(lid)

        merged_portfolio = LemmaPortfolio(
            name=f"{a.name}_x_{b.name}",
            lemma_ids=tuple(all_ids),
            utility_scores=merged_utility,
            reuse_counts=merged_reuse,
        )
        coverage = self._coverage_calc.calculate(merged_portfolio)
        merged_portfolio = replace(merged_portfolio, coverage=coverage)
        self._portfolios[merged_portfolio.portfolio_id] = merged_portfolio
        return merged_portfolio

    def all_portfolios(self) -> list[LemmaPortfolio]:
        """Return all registered portfolios."""
        return list(self._portfolios.values())

    def diagnostics(self) -> dict[str, Any]:
        """Return aggregate diagnostics across all registered portfolios."""
        portfolios = self.all_portfolios()
        if not portfolios:
            return {"portfolio_count": 0}
        avg_coverage = sum(p.coverage for p in portfolios) / len(portfolios)
        avg_size = sum(p.size for p in portfolios) / len(portfolios)
        avg_utility = sum(p.average_utility() for p in portfolios) / len(portfolios)
        return {
            "portfolio_count": len(portfolios),
            "average_coverage": avg_coverage,
            "average_size": avg_size,
            "average_utility": avg_utility,
            "reuse_summary": self._reuse_tracker.summary(),
            "optimize_calls": self._optimize_call_count,
        }

    def report(self, portfolio_id: str) -> str:
        """Generate a multi-line human-readable report for a registered portfolio."""
        portfolio = self._portfolios.get(portfolio_id)
        if portfolio is None:
            return f"[LemmaPortfolioManager] No portfolio with id '{portfolio_id}'"

        coverage_report = self._coverage_calc.coverage_report(portfolio)
        utility_estimator = self._utility_estimator
        # Estimate utilities with empty context
        utilities = utility_estimator.batch_estimate(
            list(portfolio.lemma_ids), [], portfolio
        )
        unused = self._reuse_tracker.unused_lemmas(portfolio)

        lines: list[str] = [
            "=== Lemma Portfolio Report ===",
            f"  Name:              {portfolio.name}",
            f"  ID:                {portfolio.portfolio_id}",
            f"  Lemma count:       {portfolio.size}",
            f"  Coverage:          {portfolio.coverage:.4f}",
            f"  Avg utility:       {portfolio.average_utility():.4f}",
            f"  Unused lemmas:     {len(unused)}",
            "",
            "--- Coverage Report ---",
            f"  Overall coverage:  {coverage_report.get('overall_coverage', 0):.4f}",
            f"  Uncovered:         {coverage_report.get('uncovered_count', 0)}",
            f"  Coverage gap:      {coverage_report.get('coverage_gap', 0):.4f}",
            "",
            "--- Top 5 Lemmas by Utility ---",
        ]
        top_5 = sorted(utilities.items(), key=lambda x: x[1], reverse=True)[:5]
        for lid, score in top_5:
            reuse = portfolio.reuse_count_of(lid)
            lines.append(f"  {lid:<40} util={score:.4f}  reuse={reuse}")
        lines.append("")
        lines.append(f"  Report generated at: {_now_iso()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PortfolioConfig",
    "LemmaUtilityEstimator",
    "ReuseTracker",
    "CoverageCalculator",
    "PortfolioRebalancer",
    "LemmaPortfolioManager",
]
