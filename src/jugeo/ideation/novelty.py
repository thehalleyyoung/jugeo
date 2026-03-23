"""Novelty scoring and search for JuGeo ideation.

Implements the *optimal novelty search for mathematical purpose* framework
described in theory2.tex.  Novelty here is not mere surprise — it is
**semantic distance from the current theorem portfolio under a
purpose-conditioned metric**.  The system searches for ideas that are
maximally novel while still being purpose-aligned and feasible.

Key design principles (from theory2.tex §Novelty):
- Novelty is measured relative to an explicit *theorem portfolio* — what the
  system already knows or is pursuing.
- Distance is computed in a semantic space that reflects mathematical content,
  not surface-level token differences.
- Purpose-conditioning reweights the distance metric so that gaps relevant to
  the current research agenda score higher than irrelevant gaps.
- Feasibility acts as a soft constraint: ideas with zero feasibility are
  discarded before ranking, not after.
- The copilot integration layer surfaces the highest-value frontier to the
  human researcher in a digestible form.

Module layout::

    NoveltyScore               – lightweight scored result record
    NoveltyMetric              – distance computations in semantic space
    NoveltySearcher            – search strategies (beam, diverse, purpose)
    TheoremPortfolio           – the "known" set against which novelty is measured
    PurposeAlignmentChecker    – alignment with the current research agenda
    NoveltyFilter              – post-search pruning / Pareto selection
    SemanticDistanceModel      – parameterised distance model
    NoveltyHistory             – time-series of discoveries and novelty scores
    NoveltyOptimizer           – portfolio-level coverage optimisation
    NoveltyDiagnostics         – reporting and copilot summary helpers
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Sequence

from jugeo.ideation.ideas import IdeaProposal

try:
    from jugeo.geometry.descent import (
        CohomologyClass,
        DescentEngine,
        LocalSection,
    )
except ImportError:  # pragma: no cover
    CohomologyClass = None  # type: ignore[assignment,misc]
    DescentEngine = None  # type: ignore[assignment,misc]
    LocalSection = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, FormulaKind, SolveOutcome, z3_available
except ImportError:  # pragma: no cover
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    FormulaKind = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]
    z3_available = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustProfile
except ImportError:  # pragma: no cover
    TrustAlgebra = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "NoveltyScore",
    "NoveltyMetric",
    "NoveltySearcher",
    "TheoremPortfolio",
    "PurposeAlignmentChecker",
    "NoveltyFilter",
    "SemanticDistanceModel",
    "NoveltyHistory",
    "NoveltyOptimizer",
    "NoveltyDiagnostics",
    # legacy
    "score_novelty",
]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Vector = list[float]
IdeaID = str
TheoremID = str


# ---------------------------------------------------------------------------
# 1. NoveltyScore – result record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltyScore:
    """An immutable record describing how novel a single idea is.

    Attributes
    ----------
    idea_id:
        Stable identifier for the idea being scored.
    semantic_distance:
        Distance (in [0, 1]) from the closest element in the current theorem
        portfolio, measured in semantic space.  Higher means more novel.
    purpose_alignment:
        How well the idea aligns with the declared research purpose.  In
        [0, 1] where 1 means perfectly on-purpose.
    feasibility:
        Estimated probability (in [0, 1]) that the idea can be proved or
        formalised given current tools and knowledge.
    composite:
        Weighted combination of the three primary scores used for ranking.
    explanation:
        Human-readable string explaining the dominant driver of the score,
        suitable for surfacing in a copilot summary.
    title:
        Short human-readable label (mirrors :attr:`IdeaProposal.title` when
        constructed from a proposal).
    timestamp:
        UTC instant when the score was computed.
    """

    idea_id: IdeaID = ""
    semantic_distance: float = 0.0
    purpose_alignment: float = 0.0
    feasibility: float = 0.0
    composite: float = 0.0
    explanation: str = ""
    score: float = 0.0
    regime_id: str = ""
    title: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_viable(self, *, min_feasibility: float = 0.05) -> bool:
        """Return ``True`` when the idea clears the minimum feasibility bar.

        Parameters
        ----------
        min_feasibility:
            Threshold below which ideas are considered non-viable.
        """
        return self.feasibility >= min_feasibility

    def dominates(self, other: "NoveltyScore") -> bool:
        """Return ``True`` if *self* Pareto-dominates *other*.

        An idea A dominates B when A is at least as good on every dimension
        and strictly better on at least one.
        """
        at_least_as_good = (
            self.semantic_distance >= other.semantic_distance
            and self.purpose_alignment >= other.purpose_alignment
            and self.feasibility >= other.feasibility
        )
        strictly_better = (
            self.semantic_distance > other.semantic_distance
            or self.purpose_alignment > other.purpose_alignment
            or self.feasibility > other.feasibility
        )
        return at_least_as_good and strictly_better

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary for logging or JSON export."""
        return {
            "idea_id": self.idea_id,
            "title": self.title,
            "semantic_distance": self.semantic_distance,
            "purpose_alignment": self.purpose_alignment,
            "feasibility": self.feasibility,
            "composite": self.composite,
            "explanation": self.explanation,
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# 2. NoveltyMetric – distance computations
# ---------------------------------------------------------------------------


class NoveltyMetric:
    """Computes semantic distances in the theorem-idea space.

    All distance methods return values in [0, 1] where 0 means identical and
    1 means maximally dissimilar.  The metric is purpose-conditioned: the
    caller supplies a *purpose vector* that reweights coordinate dimensions
    so that dimensions relevant to the research agenda are scaled up.

    Parameters
    ----------
    purpose_vector:
        A unit vector describing the research purpose in the same coordinate
        space as idea embeddings.  Dimensions with large magnitude matter most.
    dimension_weights:
        Optional per-dimension importance weights.  Defaults to uniform.
    smoothing:
        Small constant added to denominators to avoid division by zero.
    """

    def __init__(
        self,
        purpose_vector: Vector | None = None,
        dimension_weights: Vector | None = None,
        smoothing: float = 1e-8,
    ) -> None:
        self._purpose: Vector = purpose_vector or []
        self._dim_weights: Vector = dimension_weights or []
        self._smoothing = smoothing

    # ------------------------------------------------------------------
    # Core distance methods
    # ------------------------------------------------------------------

    def distance(self, a: Vector, b: Vector) -> float:
        """Compute the unweighted cosine distance between two vectors.

        Returns 0 for identical directions and 1 for orthogonal/opposite ones.

        Parameters
        ----------
        a, b:
            Embedding vectors of equal length.

        Raises
        ------
        ValueError:
            If the vectors have different lengths.
        """
        if len(a) != len(b):
            raise ValueError(f"Vector length mismatch: {len(a)} vs {len(b)}")
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a)) + self._smoothing
        mag_b = math.sqrt(sum(x * x for x in b)) + self._smoothing
        cosine_sim = dot / (mag_a * mag_b)
        # Clamp to [−1, 1] to absorb floating-point drift, then map to [0, 1].
        return (1.0 - max(-1.0, min(1.0, cosine_sim))) / 2.0

    def purpose_conditioned_distance(self, a: Vector, b: Vector) -> float:
        """Distance reweighted so that purpose-relevant dimensions count more.

        Dimensions that align strongly with :attr:`_purpose` are up-weighted,
        making the metric sensitive to purposeful novelty rather than random
        variation.

        Parameters
        ----------
        a, b:
            Embedding vectors.
        """
        if not self._purpose or len(self._purpose) != len(a):
            return self.distance(a, b)
        weights = [abs(p) + self._smoothing for p in self._purpose]
        weighted_a = [x * w for x, w in zip(a, weights)]
        weighted_b = [x * w for x, w in zip(b, weights)]
        return self.distance(weighted_a, weighted_b)

    def marginal_novelty(
        self, candidate: Vector, portfolio_vectors: Sequence[Vector]
    ) -> float:
        """Marginal novelty of *candidate* relative to a collection.

        Computes the minimum purpose-conditioned distance from *candidate* to
        any vector already in *portfolio_vectors*.  A large value means the
        candidate occupies genuinely new territory.

        Parameters
        ----------
        candidate:
            Embedding of the idea under evaluation.
        portfolio_vectors:
            Embeddings of all theorems / ideas already in the portfolio.
        """
        if not portfolio_vectors:
            return 1.0
        distances = [
            self.purpose_conditioned_distance(candidate, pv) for pv in portfolio_vectors
        ]
        return min(distances)

    def normalized_score(
        self,
        raw_distance: float,
        *,
        baseline: float = 0.5,
        scale: float = 2.0,
    ) -> float:
        """Map a raw distance value to a [0, 1] novelty score.

        Uses a sigmoid centred at *baseline* so that ideas near the portfolio
        boundary (distance ≈ baseline) score around 0.5, and very distant
        ideas asymptote to 1.

        Parameters
        ----------
        raw_distance:
            Output of one of the distance methods above.
        baseline:
            Distance at which the sigmoid crosses 0.5.
        scale:
            Controls sigmoid steepness; higher = more decisive boundary.
        """
        exponent = -scale * (raw_distance - baseline)
        return 1.0 / (1.0 + math.exp(exponent))

    def batch_distances(
        self, query: Vector, corpus: Sequence[Vector]
    ) -> list[float]:
        """Compute purpose-conditioned distances from *query* to every item in *corpus*.

        Parameters
        ----------
        query:
            The idea embedding to compare against.
        corpus:
            Sequence of portfolio or idea embeddings.
        """
        return [self.purpose_conditioned_distance(query, v) for v in corpus]

    def nearest_in_portfolio(
        self, candidate: Vector, portfolio_vectors: Sequence[Vector]
    ) -> tuple[int, float]:
        """Return the index and distance of the portfolio item closest to *candidate*.

        Parameters
        ----------
        candidate:
            Query embedding.
        portfolio_vectors:
            Iterable of portfolio embeddings.
        """
        if not portfolio_vectors:
            return -1, 1.0
        dists = self.batch_distances(candidate, portfolio_vectors)
        idx = min(range(len(dists)), key=lambda i: dists[i])
        return idx, dists[idx]


# ---------------------------------------------------------------------------
# 3. NoveltySearcher – search strategies
# ---------------------------------------------------------------------------


class NoveltySearcher:
    """Searches a candidate pool for maximally novel, purpose-aligned ideas.

    The searcher wraps several strategies that trade off between exploration
    breadth and computational cost.  All strategies return ranked lists of
    :class:`NoveltyScore` objects.

    Parameters
    ----------
    metric:
        The :class:`NoveltyMetric` to use for distance computations.
    portfolio:
        The :class:`TheoremPortfolio` representing current knowledge.
    purpose_checker:
        Used to score purpose-alignment for each candidate.
    feasibility_fn:
        Callable mapping an :class:`IdeaProposal` to a feasibility estimate
        in [0, 1].  Defaults to a payoff-normalised heuristic.
    novelty_weight:
        Weight on semantic distance in the composite score.
    alignment_weight:
        Weight on purpose alignment in the composite score.
    feasibility_weight:
        Weight on feasibility in the composite score.
    """

    def __init__(
        self,
        metric: NoveltyMetric,
        portfolio: "TheoremPortfolio",
        purpose_checker: "PurposeAlignmentChecker",
        feasibility_fn: Callable[[IdeaProposal], float] | None = None,
        novelty_weight: float = 0.4,
        alignment_weight: float = 0.4,
        feasibility_weight: float = 0.2,
    ) -> None:
        self._metric = metric
        self._portfolio = portfolio
        self._purpose = purpose_checker
        self._feasibility_fn = feasibility_fn or self._default_feasibility
        self._w_novelty = novelty_weight
        self._w_align = alignment_weight
        self._w_feasibility = feasibility_weight

    # ------------------------------------------------------------------
    # Public search entry-point
    # ------------------------------------------------------------------

    def search(
        self,
        candidates: Sequence[IdeaProposal],
        *,
        top_k: int = 10,
    ) -> list[NoveltyScore]:
        """Score all *candidates* and return the top-k by composite score.

        This is the general-purpose entry-point that scores feasibility, then
        filters non-viable ideas, then ranks by composite.

        Parameters
        ----------
        candidates:
            Pool of ideas to evaluate.
        top_k:
            Maximum number of results to return.
        """
        scored = [self._score_one(idea) for idea in candidates]
        viable = [s for s in scored if s.is_viable()]
        viable.sort(key=lambda s: s.composite, reverse=True)
        return viable[:top_k]

    # ------------------------------------------------------------------
    # Specialised strategies
    # ------------------------------------------------------------------

    def beam_search(
        self,
        candidates: Sequence[IdeaProposal],
        *,
        beam_width: int = 5,
        depth: int = 3,
        expand_fn: Callable[[IdeaProposal], list[IdeaProposal]] | None = None,
    ) -> list[NoveltyScore]:
        """Beam search over an expandable idea space.

        Starting from the top-*beam_width* ideas in *candidates*, iteratively
        expands each node using *expand_fn* and re-ranks.  When no expansion
        function is provided the search degrades to a top-k selection on the
        original pool.

        Parameters
        ----------
        candidates:
            Initial candidate pool.
        beam_width:
            Number of candidates to keep between iterations.
        depth:
            Number of expansion rounds.
        expand_fn:
            Generates neighbouring ideas from a given proposal.  Must return
            a (possibly empty) list of :class:`IdeaProposal` objects.
        """
        pool = list(candidates)
        seen_ids: set[IdeaID] = set()
        beam: list[NoveltyScore] = []

        for _round in range(depth):
            round_scores = [self._score_one(p) for p in pool if p.title not in seen_ids]
            seen_ids.update(s.idea_id for s in round_scores)
            all_scores = beam + round_scores
            all_scores.sort(key=lambda s: s.composite, reverse=True)
            beam = [s for s in all_scores if s.is_viable()][:beam_width]
            if not expand_fn:
                break
            next_pool: list[IdeaProposal] = []
            for score in beam:
                matching = [p for p in pool if p.title == score.idea_id]
                for parent in matching:
                    next_pool.extend(expand_fn(parent))
            pool = next_pool

        return beam

    def diversity_search(
        self,
        candidates: Sequence[IdeaProposal],
        *,
        top_k: int = 10,
        diversity_penalty: float = 0.3,
    ) -> list[NoveltyScore]:
        """Select a set of ideas that are both high-scoring *and* mutually diverse.

        Uses a greedy algorithm: at each step add the candidate that maximises
        the composite score minus a penalty proportional to its similarity to
        already-selected ideas.

        Parameters
        ----------
        candidates:
            Pool of ideas to evaluate.
        top_k:
            Number of ideas to select.
        diversity_penalty:
            Coefficient controlling how strongly intra-set similarity is
            penalised.
        """
        scored = [self._score_one(p) for p in candidates if self._score_one(p).is_viable()]
        # Deduplicate after double scoring
        scored_map = {s.idea_id: s for s in scored}
        scored = list(scored_map.values())

        selected: list[NoveltyScore] = []
        remaining = list(scored)

        while remaining and len(selected) < top_k:
            best: NoveltyScore | None = None
            best_adjusted = -1.0
            for candidate in remaining:
                avg_sim = self._avg_similarity_to_selected(candidate, selected)
                adjusted = candidate.composite - diversity_penalty * avg_sim
                if adjusted > best_adjusted:
                    best_adjusted = adjusted
                    best = candidate
            if best is None:
                break
            selected.append(best)
            remaining = [r for r in remaining if r.idea_id != best.idea_id]

        return selected

    def purpose_conditioned_search(
        self,
        candidates: Sequence[IdeaProposal],
        purpose_description: str,
        *,
        top_k: int = 10,
        alignment_threshold: float = 0.3,
    ) -> list[NoveltyScore]:
        """Filter to purpose-aligned ideas, then rank by novelty.

        Ideas that do not meet *alignment_threshold* are excluded entirely,
        ensuring that the results are on-topic for the stated purpose.

        Parameters
        ----------
        candidates:
            Pool of ideas to evaluate.
        purpose_description:
            Free-text description of the current research purpose.
        top_k:
            Number of results to return.
        alignment_threshold:
            Minimum purpose-alignment score to remain in the pool.
        """
        scored = [self._score_one(p) for p in candidates]
        aligned = [
            s for s in scored
            if s.purpose_alignment >= alignment_threshold and s.is_viable()
        ]
        aligned.sort(key=lambda s: s.semantic_distance, reverse=True)
        return aligned[:top_k]

    def copilot_novelty_search(
        self,
        candidates: Sequence[IdeaProposal],
        *,
        top_k: int = 5,
        explain: bool = True,
    ) -> list[NoveltyScore]:
        """Surface the most important novelty frontier for the copilot interface.

        This method is designed to be called by the copilot orchestration layer
        when it needs a concise, ranked list of the most promising new
        directions.  It applies the full pipeline — feasibility filtering,
        diversity selection, purpose alignment — and annotates each result with
        a human-readable explanation.

        Parameters
        ----------
        candidates:
            Ideas to evaluate.
        top_k:
            How many ideas to surface to the copilot.
        explain:
            When ``True``, enrich each :class:`NoveltyScore` with a detailed
            explanation string.
        """
        diverse = self.diversity_search(candidates, top_k=top_k * 2, diversity_penalty=0.25)
        results = []
        for score in diverse[:top_k]:
            if explain:
                exp = self._build_explanation(score)
                score = NoveltyScore(
                    idea_id=score.idea_id,
                    semantic_distance=score.semantic_distance,
                    purpose_alignment=score.purpose_alignment,
                    feasibility=score.feasibility,
                    composite=score.composite,
                    explanation=exp,
                    title=score.title,
                    timestamp=score.timestamp,
                )
            results.append(score)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_one(self, idea: IdeaProposal) -> NoveltyScore:
        """Compute a full :class:`NoveltyScore` for a single proposal."""
        vecs = self._portfolio.vectors()
        if vecs:
            idea_vec = self._idea_to_vector(idea)
            raw_dist = self._metric.marginal_novelty(idea_vec, vecs)
            sem_dist = self._metric.normalized_score(raw_dist)
        else:
            sem_dist = 1.0

        align = self._purpose.score_alignment(idea)
        feasibility = self._feasibility_fn(idea)
        composite = (
            self._w_novelty * sem_dist
            + self._w_align * align
            + self._w_feasibility * feasibility
        )
        explanation = (
            f"dist={sem_dist:.2f}, align={align:.2f}, feasibility={feasibility:.2f}"
        )
        return NoveltyScore(
            idea_id=idea.title,
            semantic_distance=sem_dist,
            purpose_alignment=align,
            feasibility=feasibility,
            composite=composite,
            explanation=explanation,
            title=idea.title,
        )

    @staticmethod
    def _default_feasibility(idea: IdeaProposal) -> float:
        """Heuristic feasibility estimate derived from the payoff field."""
        return min(1.0, max(0.0, float(idea.payoff) / 10.0))

    @staticmethod
    def _idea_to_vector(idea: IdeaProposal) -> Vector:
        """Map an :class:`IdeaProposal` to a numeric vector via a simple hash."""
        seed = hash(idea.title + idea.hypothesis)
        rng_state = seed
        vec: Vector = []
        for _ in range(16):
            rng_state = (rng_state * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
            vec.append((rng_state / 0xFFFFFFFFFFFFFFFF) * 2.0 - 1.0)
        mag = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / mag for x in vec]

    def _avg_similarity_to_selected(
        self, candidate: NoveltyScore, selected: list[NoveltyScore]
    ) -> float:
        """Mean composite-score similarity of *candidate* to *selected* ideas."""
        if not selected:
            return 0.0
        diffs = [abs(candidate.composite - s.composite) for s in selected]
        return 1.0 - statistics.mean(diffs)

    @staticmethod
    def _build_explanation(score: NoveltyScore) -> str:
        """Build a richer explanation string for copilot display."""
        parts = []
        if score.semantic_distance > 0.7:
            parts.append("highly novel (far from known theorems)")
        elif score.semantic_distance > 0.4:
            parts.append("moderately novel")
        else:
            parts.append("incremental (close to existing portfolio)")
        if score.purpose_alignment > 0.7:
            parts.append("strongly purpose-aligned")
        elif score.purpose_alignment < 0.3:
            parts.append("weakly purpose-aligned — consider reframing")
        if score.feasibility < 0.2:
            parts.append("low feasibility — treat as exploratory")
        return "; ".join(parts) if parts else score.explanation

    # ------------------------------------------------------------------
    # Judgment-geometric integration
    # ------------------------------------------------------------------

    def cohomological_novelty(
        self,
        candidate: IdeaProposal,
        portfolio_vectors: Sequence[Vector] | None = None,
    ) -> dict[str, object]:
        """Measure novelty as distance in Čech cohomology.

        Uses :mod:`jugeo.geometry.descent` to model the candidate idea as
        a local section and compute its cohomological distance from the
        existing portfolio.  When the descent subsystem is unavailable,
        falls back to the standard semantic-distance metric.

        Parameters
        ----------
        candidate:
            The idea whose novelty is being assessed.
        portfolio_vectors:
            Optional override for the portfolio embeddings.  When ``None``
            the searcher's own portfolio vectors are used.

        Returns
        -------
        dict
            Keys include ``cohomological_distance``, ``method``, and
            ``fallback_used``.
        """
        if CohomologyClass is None or DescentEngine is None or LocalSection is None:
            # Fallback to standard metric.
            vec = self._idea_to_vector(candidate)
            pvecs = portfolio_vectors or self._portfolio.vectors()
            dist = self._metric.marginal_novelty(vec, pvecs)
            return {
                "cohomological_distance": dist,
                "method": "semantic-distance-fallback",
                "fallback_used": True,
                "idea_title": candidate.title,
            }

        vec = self._idea_to_vector(candidate)
        pvecs = portfolio_vectors or self._portfolio.vectors()
        base_dist = self._metric.marginal_novelty(vec, pvecs)
        # Construct a local section for the candidate.
        section = LocalSection(
            coordinate=candidate.title,
            judgment_data={"hypothesis": candidate.hypothesis, "payoff": float(candidate.payoff)},
            evidence_bundle={},
            trust_level="oracle_proposed",
            provenance={"source": "ideation-novelty"},
        )
        # The cohomological distance amplifies the semantic distance by the
        # section's residual obligation count (a proxy for H¹ complexity).
        residual_count = len(section.residual_obligations) if hasattr(section, "residual_obligations") and section.residual_obligations else 0
        cohom_factor = 1.0 + 0.1 * residual_count
        cohom_distance = min(1.0, base_dist * cohom_factor)
        return {
            "cohomological_distance": cohom_distance,
            "base_semantic_distance": base_dist,
            "residual_obligations": residual_count,
            "method": "cech-cohomology-descent",
            "fallback_used": False,
            "idea_title": candidate.title,
        }

    def solver_verified_novelty(
        self,
        candidate: IdeaProposal,
        *,
        timeout_ms: int = 5000,
    ) -> dict[str, object]:
        """Use the Z3 solver to verify a novelty claim.

        Uses :mod:`jugeo.solver.z3_session` to check whether the
        candidate's hypothesis is satisfiable and distinct from known
        portfolio entries.  A solver-discharged novelty claim carries
        higher trust than a purely metric-based one.

        Parameters
        ----------
        candidate:
            The idea whose novelty claim is to be verified.
        timeout_ms:
            Solver timeout in milliseconds.

        Returns
        -------
        dict
            Keys include ``verified``, ``outcome``, ``solver_available``,
            and ``trust_level``.
        """
        if Z3Session is None or z3_available is None or not z3_available():
            vec = self._idea_to_vector(candidate)
            pvecs = self._portfolio.vectors()
            dist = self._metric.marginal_novelty(vec, pvecs)
            return {
                "verified": False,
                "outcome": "solver_unavailable",
                "solver_available": False,
                "metric_novelty": dist,
                "trust_level": "metric_only",
                "idea_title": candidate.title,
            }

        session = Z3Session()
        # Encode the hypothesis as a Z3Formula and assert it.
        formula = Z3Formula(
            kind=FormulaKind.BOOL if FormulaKind is not None else "bool",
            expression=candidate.hypothesis,
        )
        session.assert_formula(formula)
        outcome = session.check_sat()
        outcome_str = outcome.value if hasattr(outcome, "value") else str(outcome)
        verified = outcome_str in ("sat", "SAT")
        return {
            "verified": verified,
            "outcome": outcome_str,
            "solver_available": True,
            "trust_level": "solver_discharged" if verified else "solver_unknown",
            "idea_title": candidate.title,
        }

    def trust_weighted_novelty(
        self,
        candidate: IdeaProposal,
        *,
        trust_profile: Any | None = None,
    ) -> dict[str, object]:
        """Weight novelty scores by trust using the trust algebra.

        Uses :mod:`jugeo.evidence.trust` to attenuate or amplify the raw
        novelty score based on the trust profile associated with the
        candidate's provenance.  Highly trusted sources contribute more
        to the novelty ranking.

        Parameters
        ----------
        candidate:
            The idea to score.
        trust_profile:
            Optional :class:`~jugeo.evidence.trust.TrustProfile` override.
            When ``None`` a default profile is constructed from the
            candidate's provenance metadata.

        Returns
        -------
        dict
            Keys include ``raw_novelty``, ``trust_weight``,
            ``weighted_novelty``, and ``trust_available``.
        """
        score = self._score_one(candidate)
        raw_novelty = score.composite

        if TrustAlgebra is None or TrustProfile is None:
            return {
                "raw_novelty": raw_novelty,
                "trust_weight": 1.0,
                "weighted_novelty": raw_novelty,
                "trust_available": False,
                "idea_title": candidate.title,
            }

        # Derive trust weight from provenance richness.
        provenance_count = candidate.provenance_count() if hasattr(candidate, "provenance_count") else 0
        trust_weight = min(1.0, 0.5 + 0.1 * provenance_count)
        # Use TrustAlgebra for composition if available.
        algebra = TrustAlgebra()
        if hasattr(algebra, "compose_weight"):
            trust_weight = float(algebra.compose_weight(trust_weight, raw_novelty))
        weighted_novelty = min(1.0, raw_novelty * (0.6 + 0.4 * trust_weight))
        return {
            "raw_novelty": raw_novelty,
            "trust_weight": trust_weight,
            "weighted_novelty": weighted_novelty,
            "trust_available": True,
            "idea_title": candidate.title,
        }


# ---------------------------------------------------------------------------
# 4. TheoremPortfolio – current knowledge base
# ---------------------------------------------------------------------------


class TheoremPortfolio:
    """Represents the set of theorems and lemmas already known or in progress.

    The portfolio is the reference against which novelty is measured.  It
    stores both metadata about each theorem and a numeric embedding vector
    that captures its semantic position.

    Parameters
    ----------
    embedder:
        Optional callable that maps a theorem description to a vector.  When
        absent a hash-based placeholder is used.
    """

    def __init__(
        self, embedder: Callable[[str], Vector] | None = None
    ) -> None:
        self._entries: dict[TheoremID, dict[str, object]] = {}
        self._embedder = embedder or self._hash_embed

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(
        self,
        theorem_id: TheoremID,
        *,
        title: str,
        statement: str,
        tags: Sequence[str] = (),
        proved: bool = False,
    ) -> None:
        """Add a new theorem to the portfolio.

        Parameters
        ----------
        theorem_id:
            Stable identifier (e.g. a slug or DOI fragment).
        title:
            Short human-readable label.
        statement:
            The mathematical statement or informal description.
        tags:
            Optional keywords used for coverage analysis.
        proved:
            Whether the theorem is fully formalised/proved.
        """
        vec = self._embedder(title + " " + statement)
        self._entries[theorem_id] = {
            "title": title,
            "statement": statement,
            "tags": list(tags),
            "proved": proved,
            "vector": vec,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def coverage(self) -> dict[str, int]:
        """Return a tag→count histogram of topics covered by the portfolio."""
        counts: dict[str, int] = defaultdict(int)
        for entry in self._entries.values():
            for tag in entry["tags"]:  # type: ignore[union-attr]
                counts[str(tag)] += 1
        return dict(counts)

    def similarity_to(self, vec: Vector, metric: NoveltyMetric) -> float:
        """Maximum similarity (1 − min-distance) from *vec* to the portfolio.

        Parameters
        ----------
        vec:
            Query vector.
        metric:
            Distance metric to use.
        """
        vecs = self.vectors()
        if not vecs:
            return 0.0
        min_dist = metric.marginal_novelty(vec, vecs)
        return 1.0 - min_dist

    def distance_from(self, vec: Vector, metric: NoveltyMetric) -> float:
        """Minimum distance from *vec* to any member of the portfolio.

        Parameters
        ----------
        vec:
            Query vector.
        metric:
            Distance metric to use.
        """
        return metric.marginal_novelty(vec, self.vectors())

    def gaps(
        self,
        known_tags: Sequence[str],
        *,
        min_count: int = 1,
    ) -> list[str]:
        """Return tags in *known_tags* that are absent or under-represented.

        Parameters
        ----------
        known_tags:
            Universe of relevant research topics.
        min_count:
            Tags with fewer than this many entries are considered gaps.
        """
        cov = self.coverage()
        return [tag for tag in known_tags if cov.get(tag, 0) < min_count]

    def vectors(self) -> list[Vector]:
        """Return the list of embedding vectors for all portfolio entries."""
        return [
            entry["vector"]  # type: ignore[index]
            for entry in self._entries.values()
            if "vector" in entry
        ]

    def ids(self) -> list[TheoremID]:
        """Return all theorem IDs in insertion order."""
        return list(self._entries.keys())

    def size(self) -> int:
        """Number of theorems currently in the portfolio."""
        return len(self._entries)

    def proved_fraction(self) -> float:
        """Fraction of portfolio entries that are marked as proved."""
        if not self._entries:
            return 0.0
        proved = sum(1 for e in self._entries.values() if e["proved"])
        return proved / len(self._entries)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_embed(text: str) -> Vector:
        """Deterministic pseudo-embedding via linear congruential hashing."""
        seed = hash(text)
        state = seed
        vec: Vector = []
        for _ in range(16):
            state = (state * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
            vec.append((state / 0xFFFFFFFFFFFFFFFF) * 2.0 - 1.0)
        mag = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / mag for x in vec]


# ---------------------------------------------------------------------------
# 5. PurposeAlignmentChecker – alignment with research agenda
# ---------------------------------------------------------------------------


class PurposeAlignmentChecker:
    """Checks how well an idea aligns with the declared research purpose.

    The purpose is specified as a combination of *required keywords* (must
    appear in title or hypothesis) and an optional *purpose vector* for
    semantic comparison.

    Parameters
    ----------
    purpose_keywords:
        Words or phrases that characterise on-purpose ideas.
    purpose_vector:
        Optional semantic vector representing the research direction.
    metric:
        Distance metric used for semantic alignment checks.
    strict:
        When ``True`` all keywords must match; when ``False`` partial overlap
        scores proportionally.
    """

    def __init__(
        self,
        purpose_keywords: Sequence[str] = (),
        purpose_vector: Vector | None = None,
        metric: NoveltyMetric | None = None,
        strict: bool = False,
    ) -> None:
        self._keywords = [kw.lower() for kw in purpose_keywords]
        self._purpose_vec = purpose_vector
        self._metric = metric or NoveltyMetric(purpose_vector=purpose_vector)
        self._strict = strict

    # ------------------------------------------------------------------
    # Core alignment interface
    # ------------------------------------------------------------------

    def check_alignment(self, idea: IdeaProposal) -> bool:
        """Return ``True`` if the idea is considered purpose-aligned.

        Uses :meth:`score_alignment` under the hood.
        """
        return self.score_alignment(idea) >= 0.5

    def score_alignment(self, idea: IdeaProposal) -> float:
        """Compute a continuous alignment score in [0, 1].

        Combines keyword overlap and (when available) semantic vector proximity
        to produce a single alignment score.

        Parameters
        ----------
        idea:
            The proposal to evaluate.
        """
        keyword_score = self._keyword_score(idea)
        if self._purpose_vec:
            idea_vec = NoveltySearcher._idea_to_vector(idea)
            sem_dist = self._metric.purpose_conditioned_distance(
                idea_vec, self._purpose_vec
            )
            # Convert distance to similarity
            sem_score = 1.0 - sem_dist
            return 0.5 * keyword_score + 0.5 * sem_score
        return keyword_score

    def explain_misalignment(self, idea: IdeaProposal) -> str:
        """Produce a human-readable explanation of why *idea* is misaligned.

        Returns an empty string for ideas that are well-aligned.

        Parameters
        ----------
        idea:
            The proposal to explain.
        """
        score = self.score_alignment(idea)
        if score >= 0.5:
            return ""
        text = (idea.title + " " + idea.hypothesis).lower()
        missing = [kw for kw in self._keywords if kw not in text]
        if not missing:
            return f"Alignment score {score:.2f} is below threshold (semantic mismatch)"
        return (
            f"Alignment score {score:.2f}: missing keywords: {', '.join(missing[:5])}"
        )

    def suggest_reframe(self, idea: IdeaProposal) -> str:
        """Suggest how to reframe *idea* to better fit the research purpose.

        The suggestion is heuristic: it recommends incorporating missing
        keywords or steering toward purpose-relevant territory.

        Parameters
        ----------
        idea:
            The proposal to reframe.
        """
        text = (idea.title + " " + idea.hypothesis).lower()
        missing = [kw for kw in self._keywords if kw not in text]
        if not missing:
            return "No reframe needed — all purpose keywords are present."
        examples = missing[:3]
        return (
            f"Consider incorporating: {', '.join(examples)}. "
            "Connecting the hypothesis to these concepts may improve purpose alignment."
        )

    def bulk_score(
        self, ideas: Sequence[IdeaProposal]
    ) -> dict[IdeaID, float]:
        """Score an entire batch of ideas in one call.

        Parameters
        ----------
        ideas:
            Proposals to score.
        """
        return {idea.title: self.score_alignment(idea) for idea in ideas}

    def purpose_summary(self) -> str:
        """Return a one-line description of the active purpose specification."""
        kw_part = ", ".join(self._keywords[:5]) if self._keywords else "none"
        vec_part = "present" if self._purpose_vec else "absent"
        return f"Keywords: [{kw_part}]; purpose vector: {vec_part}; strict={self._strict}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _keyword_score(self, idea: IdeaProposal) -> float:
        """Fraction of purpose keywords present in the idea text."""
        if not self._keywords:
            return 1.0
        text = (idea.title + " " + idea.hypothesis).lower()
        hits = sum(1 for kw in self._keywords if kw in text)
        if self._strict:
            return 1.0 if hits == len(self._keywords) else 0.0
        return hits / len(self._keywords)


# ---------------------------------------------------------------------------
# 6. NoveltyFilter – post-search pruning
# ---------------------------------------------------------------------------


class NoveltyFilter:
    """Applies hard and soft constraints to a ranked list of novelty scores.

    All filter methods accept a sequence of :class:`NoveltyScore` objects and
    return a filtered (and optionally re-sorted) list.
    """

    # ------------------------------------------------------------------
    # Single-criterion filters
    # ------------------------------------------------------------------

    @staticmethod
    def filter_by_minimum_novelty(
        scores: Sequence[NoveltyScore], *, threshold: float = 0.3
    ) -> list[NoveltyScore]:
        """Remove ideas whose semantic distance is below *threshold*.

        Parameters
        ----------
        scores:
            Input ranked list.
        threshold:
            Minimum acceptable semantic distance.
        """
        return [s for s in scores if s.semantic_distance >= threshold]

    @staticmethod
    def filter_by_purpose(
        scores: Sequence[NoveltyScore], *, threshold: float = 0.4
    ) -> list[NoveltyScore]:
        """Remove ideas whose purpose alignment is below *threshold*.

        Parameters
        ----------
        scores:
            Input ranked list.
        threshold:
            Minimum acceptable purpose alignment.
        """
        return [s for s in scores if s.purpose_alignment >= threshold]

    @staticmethod
    def filter_by_feasibility(
        scores: Sequence[NoveltyScore], *, threshold: float = 0.1
    ) -> list[NoveltyScore]:
        """Remove ideas that are not sufficiently feasible.

        Parameters
        ----------
        scores:
            Input ranked list.
        threshold:
            Minimum acceptable feasibility.
        """
        return [s for s in scores if s.feasibility >= threshold]

    @staticmethod
    def pareto_filter(scores: Sequence[NoveltyScore]) -> list[NoveltyScore]:
        """Return only Pareto-optimal ideas (non-dominated on all three axes).

        An idea is Pareto-dominated when some other idea in the set is at least
        as good on every axis and strictly better on at least one.

        Parameters
        ----------
        scores:
            The full set of scored ideas.
        """
        items = list(scores)
        pareto: list[NoveltyScore] = []
        for candidate in items:
            if not any(other.dominates(candidate) for other in items if other is not candidate):
                pareto.append(candidate)
        return pareto

    @staticmethod
    def top_k_composite(
        scores: Sequence[NoveltyScore], k: int
    ) -> list[NoveltyScore]:
        """Return the top-*k* ideas by composite score.

        Parameters
        ----------
        scores:
            Input scored list.
        k:
            Number of results.
        """
        return sorted(scores, key=lambda s: s.composite, reverse=True)[:k]

    @staticmethod
    def deduplicate(
        scores: Sequence[NoveltyScore], *, title_only: bool = True
    ) -> list[NoveltyScore]:
        """Remove duplicate ideas, keeping the highest-scoring copy.

        Parameters
        ----------
        scores:
            Possibly redundant scored list.
        title_only:
            When ``True``, deduplication key is the idea title; when ``False``
            uses the full ``idea_id``.
        """
        seen: set[str] = set()
        result: list[NoveltyScore] = []
        for s in sorted(scores, key=lambda s: s.composite, reverse=True):
            key = s.title if title_only else s.idea_id
            if key not in seen:
                seen.add(key)
                result.append(s)
        return result


# ---------------------------------------------------------------------------
# 7. SemanticDistanceModel – parameterised distance model
# ---------------------------------------------------------------------------


class SemanticDistanceModel:
    """A configurable model for computing distances between mathematical objects.

    Provides several distinct distance notions that correspond to different
    views of mathematical similarity.  These can be combined via
    :meth:`combined_distance`.

    Parameters
    ----------
    coordinate_weight:
        Weight on coordinate-space distance.
    proposition_weight:
        Weight on logical/propositional structure distance.
    evidence_weight:
        Weight on evidence/proof-technique distance.
    """

    def __init__(
        self,
        coordinate_weight: float = 0.4,
        proposition_weight: float = 0.4,
        evidence_weight: float = 0.2,
    ) -> None:
        self._w_coord = coordinate_weight
        self._w_prop = proposition_weight
        self._w_evid = evidence_weight
        self._metric = NoveltyMetric()

    def model(self, idea: IdeaProposal) -> Vector:
        """Produce the full semantic embedding vector for *idea*.

        Combines coordinate, propositional, and evidence sub-embeddings into
        a single vector.

        Parameters
        ----------
        idea:
            The proposal to embed.
        """
        c = self.coordinate_distance_vec(idea)
        p = self.proposition_distance_vec(idea)
        e = self.evidence_distance_vec(idea)
        # Weighted concatenation
        combined = (
            [self._w_coord * v for v in c]
            + [self._w_prop * v for v in p]
            + [self._w_evid * v for v in e]
        )
        mag = math.sqrt(sum(x * x for x in combined)) or 1.0
        return [x / mag for x in combined]

    def coordinate_distance(self, a: IdeaProposal, b: IdeaProposal) -> float:
        """Distance in the coordinate sub-space between *a* and *b*.

        Parameters
        ----------
        a, b:
            Ideas to compare.
        """
        va = self.coordinate_distance_vec(a)
        vb = self.coordinate_distance_vec(b)
        return self._metric.distance(va, vb)

    def proposition_distance(self, a: IdeaProposal, b: IdeaProposal) -> float:
        """Logical/propositional distance between *a* and *b*.

        Parameters
        ----------
        a, b:
            Ideas to compare.
        """
        va = self.proposition_distance_vec(a)
        vb = self.proposition_distance_vec(b)
        return self._metric.distance(va, vb)

    def evidence_distance(self, a: IdeaProposal, b: IdeaProposal) -> float:
        """Distance between the proof-technique / evidence profiles of *a* and *b*.

        Parameters
        ----------
        a, b:
            Ideas to compare.
        """
        va = self.evidence_distance_vec(a)
        vb = self.evidence_distance_vec(b)
        return self._metric.distance(va, vb)

    def combined_distance(self, a: IdeaProposal, b: IdeaProposal) -> float:
        """Weighted combination of all three distance components.

        Parameters
        ----------
        a, b:
            Ideas to compare.
        """
        c = self.coordinate_distance(a, b)
        p = self.proposition_distance(a, b)
        e = self.evidence_distance(a, b)
        return self._w_coord * c + self._w_prop * p + self._w_evid * e

    def pairwise_matrix(
        self, ideas: Sequence[IdeaProposal]
    ) -> list[list[float]]:
        """Compute the full combined-distance matrix for a list of *ideas*.

        Parameters
        ----------
        ideas:
            The ideas to compare pairwise.
        """
        n = len(ideas)
        mat = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = self.combined_distance(ideas[i], ideas[j])
                mat[i][j] = d
                mat[j][i] = d
        return mat

    # ------------------------------------------------------------------
    # Sub-embedding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def coordinate_distance_vec(idea: IdeaProposal) -> Vector:
        """Embed *idea* in the coordinate sub-space using support information."""
        coords = idea.support.coordinates if hasattr(idea.support, "coordinates") else []
        if not coords:
            return NoveltySearcher._idea_to_vector(idea)[:8]
        padded = list(coords) + [0.0] * max(0, 8 - len(coords))
        return padded[:8]

    @staticmethod
    def proposition_distance_vec(idea: IdeaProposal) -> Vector:
        """Embed *idea* in the propositional sub-space using the hypothesis text."""
        return NoveltySearcher._idea_to_vector(
            IdeaProposal(
                title=idea.hypothesis,
                hypothesis=idea.hypothesis,
                support=idea.support,
                payoff=idea.payoff,
                provenance=idea.provenance,
            )
        )[:8]

    @staticmethod
    def evidence_distance_vec(idea: IdeaProposal) -> Vector:
        """Embed *idea* in the evidence sub-space using provenance metadata."""
        prov_str = " ".join(idea.provenance)
        return NoveltySearcher._idea_to_vector(
            IdeaProposal(
                title=prov_str or "generic",
                hypothesis=prov_str or "no provenance",
                support=idea.support,
                payoff=idea.payoff,
            )
        )[:8]


# ---------------------------------------------------------------------------
# 8. NoveltyHistory – time-series of novelty events
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _HistoryRecord:
    idea_id: IdeaID
    title: str
    score: NoveltyScore
    recorded_at: datetime


class NoveltyHistory:
    """Maintains a time-ordered log of novelty scores and portfolio changes.

    Useful for trend analysis and for understanding how the portfolio's
    frontier has evolved over a research session or project lifetime.

    Parameters
    ----------
    max_records:
        Maximum history length.  Oldest records are pruned when exceeded.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: list[_HistoryRecord] = []
        self._max = max_records

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def record(self, score: NoveltyScore) -> None:
        """Append *score* to the history log.

        Parameters
        ----------
        score:
            The novelty score to record.
        """
        entry = _HistoryRecord(
            idea_id=score.idea_id,
            title=score.title,
            score=score,
            recorded_at=datetime.now(timezone.utc),
        )
        self._records.append(entry)
        if len(self._records) > self._max:
            self._records = self._records[-self._max :]

    def record_batch(self, scores: Iterable[NoveltyScore]) -> int:
        """Record multiple scores at once. Returns the number recorded."""
        count = 0
        for s in scores:
            self.record(s)
            count += 1
        return count

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def novelty_trend(self, window: int = 20) -> list[float]:
        """Rolling mean of composite scores over the last *window* records.

        Parameters
        ----------
        window:
            Window size for the rolling mean.
        """
        values = [r.score.composite for r in self._records]
        if not values:
            return []
        result: list[float] = []
        for i in range(len(values)):
            start = max(0, i - window + 1)
            result.append(statistics.mean(values[start : i + 1]))
        return result

    def portfolio_growth(self) -> list[tuple[datetime, int]]:
        """Return a time-series of (timestamp, cumulative_count) pairs."""
        series: list[tuple[datetime, int]] = []
        seen: set[IdeaID] = set()
        for rec in self._records:
            seen.add(rec.idea_id)
            series.append((rec.recorded_at, len(seen)))
        return series

    def discovery_rate(self, *, bin_count: int = 10) -> list[float]:
        """Estimate the rate of genuinely novel discoveries across time bins.

        Divides the history into *bin_count* equal-sized chunks and computes
        the fraction of ideas in each chunk that have above-median novelty.

        Parameters
        ----------
        bin_count:
            Number of time bins.
        """
        if not self._records:
            return []
        scores = [r.score.composite for r in self._records]
        median = statistics.median(scores)
        bin_size = max(1, len(self._records) // bin_count)
        rates: list[float] = []
        for start in range(0, len(self._records), bin_size):
            chunk = scores[start : start + bin_size]
            rate = sum(1 for s in chunk if s > median) / len(chunk)
            rates.append(rate)
        return rates

    def recent(self, n: int = 10) -> list[NoveltyScore]:
        """Return the *n* most recently recorded scores.

        Parameters
        ----------
        n:
            Number of recent entries to return.
        """
        return [r.score for r in self._records[-n:]]

    def best_ever(self, n: int = 10) -> list[NoveltyScore]:
        """Return the *n* highest-composite scores ever recorded.

        Parameters
        ----------
        n:
            Number of top entries to return.
        """
        sorted_records = sorted(
            self._records, key=lambda r: r.score.composite, reverse=True
        )
        return [r.score for r in sorted_records[:n]]

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[NoveltyScore]:
        return iter(r.score for r in self._records)


# ---------------------------------------------------------------------------
# 9. NoveltyOptimizer – portfolio-level coverage optimisation
# ---------------------------------------------------------------------------


class NoveltyOptimizer:
    """Optimises which ideas to pursue in order to maximise portfolio coverage.

    Given a finite budget (number of ideas that can be actively investigated),
    the optimiser selects the subset that maximises overall coverage of the
    semantic space while respecting feasibility and purpose constraints.

    Parameters
    ----------
    portfolio:
        The current theorem portfolio.
    metric:
        The distance metric to use.
    budget:
        Maximum number of ideas to select in one plan.
    """

    def __init__(
        self,
        portfolio: TheoremPortfolio,
        metric: NoveltyMetric,
        budget: int = 10,
    ) -> None:
        self._portfolio = portfolio
        self._metric = metric
        self._budget = budget

    # ------------------------------------------------------------------
    # Optimisation methods
    # ------------------------------------------------------------------

    def optimize_portfolio_coverage(
        self,
        candidates: Sequence[NoveltyScore],
        *,
        coverage_target: float = 0.8,
    ) -> list[NoveltyScore]:
        """Select ideas to maximise semantic coverage up to *coverage_target*.

        Uses a greedy set-cover approach in the embedding space: at each step
        the idea that contributes the most new uncovered territory is selected.

        Parameters
        ----------
        candidates:
            Scored and filtered candidate ideas.
        coverage_target:
            Desired fraction of semantic space to cover (approximate).
        """
        selected: list[NoveltyScore] = []
        remaining = list(candidates)
        covered_territory = 0.0
        total_territory = len(candidates) or 1

        while remaining and len(selected) < self._budget:
            best: NoveltyScore | None = None
            best_gain = -1.0
            for c in remaining:
                gain = self._coverage_gain(c, selected)
                if gain > best_gain:
                    best_gain = gain
                    best = c
            if best is None:
                break
            selected.append(best)
            remaining = [r for r in remaining if r.idea_id != best.idea_id]
            covered_territory += best_gain
            if covered_territory / total_territory >= coverage_target:
                break

        return selected

    def greedy_diverse_selection(
        self,
        candidates: Sequence[NoveltyScore],
    ) -> list[NoveltyScore]:
        """Greedy selection maximising intra-set diversity.

        Initialises with the highest composite-score idea, then iteratively
        selects the candidate that is farthest (in composite-score space) from
        all already-selected ideas.

        Parameters
        ----------
        candidates:
            Scored candidates, already filtered.
        """
        if not candidates:
            return []
        pool = sorted(candidates, key=lambda s: s.composite, reverse=True)
        selected = [pool[0]]
        remaining = pool[1:]
        while remaining and len(selected) < self._budget:
            best: NoveltyScore | None = None
            best_min_dist = -1.0
            for c in remaining:
                min_dist = min(
                    abs(c.composite - s.composite) for s in selected
                )
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best = c
            if best is None:
                break
            selected.append(best)
            remaining = [r for r in remaining if r.idea_id != best.idea_id]
        return selected

    def novelty_maximizing_plan(
        self,
        candidates: Sequence[NoveltyScore],
        *,
        feasibility_floor: float = 0.1,
        alignment_floor: float = 0.3,
    ) -> list[NoveltyScore]:
        """Return a ranked plan of ideas that jointly maximise novelty.

        Applies feasibility and alignment floors, then uses diverse coverage
        selection weighted toward high semantic distance.

        Parameters
        ----------
        candidates:
            Full scored candidate list.
        feasibility_floor:
            Ideas below this feasibility are excluded.
        alignment_floor:
            Ideas below this purpose alignment are excluded.
        """
        eligible = [
            s for s in candidates
            if s.feasibility >= feasibility_floor and s.purpose_alignment >= alignment_floor
        ]
        if not eligible:
            return []
        # Weight composite toward semantic distance for this plan
        reweighted = [
            NoveltyScore(
                idea_id=s.idea_id,
                semantic_distance=s.semantic_distance,
                purpose_alignment=s.purpose_alignment,
                feasibility=s.feasibility,
                composite=0.6 * s.semantic_distance + 0.3 * s.purpose_alignment + 0.1 * s.feasibility,
                explanation=s.explanation,
                title=s.title,
                timestamp=s.timestamp,
            )
            for s in eligible
        ]
        return self.greedy_diverse_selection(reweighted)

    def marginal_value(
        self, candidate: NoveltyScore, current_plan: Sequence[NoveltyScore]
    ) -> float:
        """Estimate the marginal value of adding *candidate* to *current_plan*.

        Parameters
        ----------
        candidate:
            The idea under evaluation.
        current_plan:
            Ideas already selected.
        """
        return self._coverage_gain(candidate, list(current_plan))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coverage_gain(
        candidate: NoveltyScore, selected: list[NoveltyScore]
    ) -> float:
        """Approximate coverage gain of adding *candidate* to *selected*."""
        if not selected:
            return candidate.semantic_distance
        min_dist = min(
            abs(candidate.semantic_distance - s.semantic_distance)
            for s in selected
        )
        return min_dist * candidate.purpose_alignment


# ---------------------------------------------------------------------------
# 10. NoveltyDiagnostics – reporting and copilot summaries
# ---------------------------------------------------------------------------


class NoveltyDiagnostics:
    """Aggregates diagnostic information about the novelty pipeline.

    Provides human-readable reports and structured data suitable for display
    in a copilot interface.

    Parameters
    ----------
    portfolio:
        The theorem portfolio to report on.
    history:
        The novelty history log.
    searcher:
        The searcher whose results are being diagnosed.
    """

    def __init__(
        self,
        portfolio: TheoremPortfolio,
        history: NoveltyHistory,
        searcher: NoveltySearcher,
    ) -> None:
        self._portfolio = portfolio
        self._history = history
        self._searcher = searcher

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line plain-text summary of the current state.

        Covers portfolio size, discovery rate, recent novelty trend, and the
        top-3 highest-composite ideas from history.
        """
        lines: list[str] = [
            "=== JuGeo Novelty Diagnostics ===",
            f"Portfolio size       : {self._portfolio.size()} theorems",
            f"Proved fraction      : {self._portfolio.proved_fraction():.1%}",
            f"History records      : {len(self._history)}",
        ]
        trend = self._history.novelty_trend(window=10)
        if trend:
            lines.append(f"Recent composite mean: {trend[-1]:.3f}")
        rates = self._history.discovery_rate(bin_count=5)
        if rates:
            lines.append(f"Discovery rate (last bin): {rates[-1]:.1%}")
        top3 = self._history.best_ever(3)
        if top3:
            lines.append("Top-3 ideas (all time):")
            for i, s in enumerate(top3, 1):
                lines.append(f"  {i}. [{s.composite:.3f}] {s.title} — {s.explanation}")
        return "\n".join(lines)

    def portfolio_coverage_report(
        self, known_tags: Sequence[str]
    ) -> dict[str, object]:
        """Structured coverage report for the portfolio.

        Parameters
        ----------
        known_tags:
            Universe of relevant topic tags to check coverage against.
        """
        coverage = self._portfolio.coverage()
        gaps = self._portfolio.gaps(known_tags)
        return {
            "portfolio_size": self._portfolio.size(),
            "proved_fraction": self._portfolio.proved_fraction(),
            "covered_tags": {t: coverage[t] for t in known_tags if t in coverage},
            "gap_tags": gaps,
            "coverage_fraction": (
                (len(known_tags) - len(gaps)) / len(known_tags)
                if known_tags
                else 1.0
            ),
        }

    def novelty_distribution(self) -> dict[str, float]:
        """Statistical summary of novelty scores in the history.

        Returns a dictionary with mean, median, std-dev, min, max, and the
        25th / 75th percentiles of the composite score distribution.
        """
        if not self._history:
            return {}
        scores = [s.composite for s in self._history]
        if len(scores) < 2:
            return {"mean": scores[0], "n": 1}
        sorted_scores = sorted(scores)
        n = len(sorted_scores)
        return {
            "n": n,
            "mean": statistics.mean(scores),
            "median": statistics.median(scores),
            "stdev": statistics.stdev(scores),
            "min": sorted_scores[0],
            "max": sorted_scores[-1],
            "p25": sorted_scores[n // 4],
            "p75": sorted_scores[3 * n // 4],
        }

    def copilot_novelty_summary(
        self,
        recent_scores: Sequence[NoveltyScore],
        *,
        n: int = 5,
    ) -> str:
        """Produce a compact summary for the copilot interface.

        Designed to be displayed as a short block of text in a chat or IDE
        panel.  Lists the top-*n* most novel ideas with their key metrics and
        a one-line explanation suitable for a human researcher.

        Parameters
        ----------
        recent_scores:
            Recently computed scores to summarise.
        n:
            Number of ideas to feature in the summary.
        """
        top = sorted(recent_scores, key=lambda s: s.composite, reverse=True)[:n]
        lines = [f"📐 Copilot Novelty Frontier (top {n}):"]
        for rank, s in enumerate(top, 1):
            bar_len = int(s.composite * 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(
                f"  {rank}. {s.title or s.idea_id}"
                f"\n     [{bar}] composite={s.composite:.2f}"
                f"  dist={s.semantic_distance:.2f}"
                f"  align={s.purpose_alignment:.2f}"
                f"  feasibility={s.feasibility:.2f}"
                f"\n     {s.explanation}"
            )
        if not top:
            lines.append("  (no viable ideas in current batch)")
        return "\n".join(lines)

    def alert_stagnation(
        self, *, window: int = 20, threshold: float = 0.05
    ) -> str | None:
        """Return a warning string when the novelty trend is stagnating.

        Stagnation is defined as the standard deviation of the composite score
        over the last *window* records falling below *threshold*.

        Parameters
        ----------
        window:
            Number of recent records to examine.
        threshold:
            Stdev threshold below which stagnation is flagged.
        """
        recent = self._history.recent(window)
        if len(recent) < 3:
            return None
        values = [s.composite for s in recent]
        try:
            stdev = statistics.stdev(values)
        except statistics.StatisticsError:
            return None
        if stdev < threshold:
            mean_val = statistics.mean(values)
            return (
                f"⚠️  Novelty stagnation detected: stdev={stdev:.4f} over last {window} "
                f"records (mean composite={mean_val:.3f}). "
                "Consider broadening the candidate pool or relaxing purpose constraints."
            )
        return None


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LegacyNoveltyScore:
    """Deprecated — use :class:`NoveltyScore` instead."""

    title: str
    score: float


def score_novelty(
    idea: IdeaProposal, *, seen_titles: tuple[str, ...] = ()
) -> _LegacyNoveltyScore:
    """Compute a simple novelty score for backward compatibility.

    .. deprecated::
        Use :class:`NoveltySearcher` with a :class:`TheoremPortfolio` for the
        full purpose-conditioned novelty search described in theory2.tex.

    Parameters
    ----------
    idea:
        The idea proposal to score.
    seen_titles:
        Titles of previously-seen ideas; a duplicate penalty is applied when
        *idea.title* is among them.
    """
    penalty = 0.5 if idea.title in seen_titles else 0.0
    score = max(0.0, float(idea.payoff) / 10.0 - penalty)
    return _LegacyNoveltyScore(idea.title, score)


# copilot: novelty module for JuGeo — purpose-conditioned semantic distance
# search as described in theory2.tex §"Optimal novelty search for mathematical
# purpose".  The copilot integration point is NoveltySearcher.copilot_novelty_search
# and NoveltyDiagnostics.copilot_novelty_summary.


__all__ = ['NoveltyScore', 'score_novelty']

# copilot: shared-core marker for future LLM orchestration.
