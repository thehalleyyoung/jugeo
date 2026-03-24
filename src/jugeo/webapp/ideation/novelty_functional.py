"""Purpose-conditioned novelty scoring for the ideation pipeline.

Standalone module – no jugeo imports beyond sibling *models*, Python stdlib
only.  Implements the Stage-4 scoring framework described in §5.4:

* **PurposeConditionedNoveltyFunctional** – weighted scoring of idea
  proposals against a purpose specification and the current portfolio.
* **FeasibilityFilter** – multi-dimensional feasibility assessment
  (no-LLM, Flask compatibility, library availability, frontend complexity).
* **NoveltyMetric** – Jaccard + structural novelty relative to the
  existing application portfolio.

All public methods return floats clamped to ``[0, 1]`` unless otherwise
documented.
"""

from __future__ import annotations

import math
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    ExistingApp,
    GainProfile,
    IdeaPortfolio,
    IdeaProposal,
)

# ── Constants ────────────────────────────────────────────────────────────────

# Coordinates with broad, consumer-facing appeal (high leverage).
_HIGH_LEVERAGE_COORDS: frozenset[ApplicationCoordinate] = frozenset({
    ApplicationCoordinate.DATA_VISUALIZATION,
    ApplicationCoordinate.SCHEDULING,
    ApplicationCoordinate.MATCHING,
    ApplicationCoordinate.FORM_WORKFLOW,
    ApplicationCoordinate.NOTIFICATION,
    ApplicationCoordinate.INTERACTIVE_DASHBOARD,
})

# Specialised / back-end coordinates (lower consumer leverage).
_LOW_LEVERAGE_COORDS: frozenset[ApplicationCoordinate] = frozenset({
    ApplicationCoordinate.SIMULATION,
    ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    ApplicationCoordinate.BATCH_PROCESSING,
    ApplicationCoordinate.AUDIT_TRAIL,
    ApplicationCoordinate.API_PROVISION,
})

# Domain-to-relevant-coordinate mapping for semantic relevance.
_DOMAIN_COORD_MAP: dict[str, frozenset[ApplicationCoordinate]] = {
    "personal finance": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.STATIC_REPORT,
    }),
    "legal-tech": frozenset({
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.COMPARISON,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.STATIC_REPORT,
    }),
    "education": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.MATCHING,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.COMPARISON,
    }),
    "health": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.AUDIT_TRAIL,
    }),
    "logistics": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.INVENTORY,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.NOTIFICATION,
    }),
    "data science": frozenset({
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.AGGREGATION,
    }),
    "project management": frozenset({
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.FORM_WORKFLOW,
    }),
}

# Sensible default when a domain is unknown – all coordinates neutral.
_DEFAULT_DOMAIN_COORDS: frozenset[ApplicationCoordinate] = frozenset()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to ``[lo, hi]``."""
    return max(lo, min(hi, value))


def _safe_mean(values: list[float], default: float = 0.5) -> float:
    """Arithmetic mean with an empty-list fallback."""
    if not values:
        return default
    return sum(values) / len(values)


def _keyword_tokens(text: str) -> set[str]:
    """Lower-case whitespace-split tokens from *text*, removing punctuation."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return {tok for tok in cleaned.lower().split() if len(tok) > 1}


# ── PurposeConditionedNoveltyFunctional ──────────────────────────────────────


class PurposeConditionedNoveltyFunctional:
    """Stage 4: Scores proposals by purpose-conditioned novelty.

    Transported from theorem discovery: the novelty of a theorem is
    measured relative to the current portfolio AND weighted by purpose
    alignment.

    Formula::

        score = w_L · leverage + w_T · tractability + w_S · semantic_relevance

    where ``w_L``, ``w_T``, ``w_S`` come from
    ``purpose.leverage_weight``, ``tractability_weight``,
    ``relevance_weight``.
    """

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def score(
        self,
        idea: IdeaProposal,
        portfolio: IdeaPortfolio,
        purpose: AppIdeationPurpose,
    ) -> float:
        """Score an idea proposal.  Returns float in ``[0, 1]``.

        The score is a weighted combination of three axes: leverage,
        tractability, and semantic relevance.  Weights are normalised so
        they always sum to 1.
        """
        lev = self._leverage(idea, purpose)
        tra = self._tractability(idea, purpose)
        rel = self._semantic_relevance(idea, purpose)

        w_sum = (
            purpose.leverage_weight
            + purpose.tractability_weight
            + purpose.relevance_weight
        )
        if w_sum <= 0:
            w_sum = 1.0

        raw = (
            purpose.leverage_weight * lev
            + purpose.tractability_weight * tra
            + purpose.relevance_weight * rel
        ) / w_sum

        return _clamp(raw)

    def batch_score(
        self,
        ideas: list[IdeaProposal],
        portfolio: IdeaPortfolio,
        purpose: AppIdeationPurpose,
    ) -> list[tuple[IdeaProposal, float]]:
        """Score all *ideas* and return ``(idea, score)`` pairs sorted
        by score descending."""
        scored = [(idea, self.score(idea, portfolio, purpose)) for idea in ideas]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def filter_and_rank(
        self,
        ideas: list[IdeaProposal],
        portfolio: IdeaPortfolio,
        purpose: AppIdeationPurpose,
        min_score: float = 0.3,
        temperature: float = 1.0,
    ) -> list[IdeaProposal]:
        """Score, filter by *min_score*, and rank ideas.

        After filtering, the surviving scores are softmax-normalised
        (with *temperature*) and written back to each idea's
        ``novelty_score``.  The returned list is sorted by the
        normalised score descending.
        """
        pairs = self.batch_score(ideas, portfolio, purpose)
        filtered = [(idea, s) for idea, s in pairs if s >= min_score]
        if not filtered:
            return []

        raw_scores = [s for _, s in filtered]
        normed = self._softmax_normalize(raw_scores, temperature=temperature)

        result: list[IdeaProposal] = []
        for (idea, _raw), n_score in zip(filtered, normed):
            idea.novelty_score = n_score
            result.append(idea)

        result.sort(key=lambda i: i.novelty_score, reverse=True)
        return result

    # ------------------------------------------------------------------
    # Scoring axes (private)
    # ------------------------------------------------------------------

    def _leverage(self, idea: IdeaProposal, purpose: AppIdeationPurpose) -> float:
        """How many people benefit from this idea.

        Components
        ----------
        * breadth   – normalised count of coordinates (more = broader).
        * bridge    – ``idea.gain.bridge_impact`` scaled to [0, 1].
        * consumer  – fraction of coordinates in the high-leverage set.

        ``leverage = 0.35 * breadth + 0.35 * bridge + 0.30 * consumer``
        """
        coords = idea.coordinates or set()
        total_coords = len(ApplicationCoordinate)

        breadth = min(len(coords) / max(total_coords / 3.0, 1.0), 1.0)

        bridge = _clamp(idea.gain.bridge_impact)

        if coords:
            consumer = len(coords & _HIGH_LEVERAGE_COORDS) / len(coords)
        else:
            consumer = 0.0

        return _clamp(0.35 * breadth + 0.35 * bridge + 0.30 * consumer)

    def _tractability(self, idea: IdeaProposal, purpose: AppIdeationPurpose) -> float:
        """How buildable is this with Flask.

        Components
        ----------
        * feasibility – ``idea.feasibility_score`` (pre-computed).
        * cost_inv    – inverse cost: ``1 / (1 + cost / 100)``.
        * constraint  – penalty if ``"no-llm"`` is in constraint_tags
          and the idea depends heavily on LLM-prone coordinates.

        ``tractability = 0.40 * feasibility + 0.35 * cost_inv + 0.25 * constraint``
        """
        feasibility = _clamp(idea.feasibility_score)

        cost_inv = 1.0 / (1.0 + idea.gain.cost / 100.0)

        constraint = 1.0
        if "no-llm" in purpose.constraint_tags:
            coords = idea.coordinates or set()
            if coords:
                llm_frac = len(coords & FeasibilityFilter.LLM_PRONE_COORDS) / len(
                    coords
                )
            else:
                llm_frac = 0.0
            constraint = max(1.0 - llm_frac * 1.5, 0.0)

        return _clamp(0.40 * feasibility + 0.35 * cost_inv + 0.25 * constraint)

    def _semantic_relevance(
        self, idea: IdeaProposal, purpose: AppIdeationPurpose
    ) -> float:
        """Alignment with stated purpose.

        Two sub-scores blended equally:

        * **keyword_match** – fraction of purpose domain/population
          keywords found in the idea title + hypothesis.
        * **coord_relevance** – fraction of the idea's coordinates that
          appear in the domain's recommended coordinate set.
        """
        keyword_score = self._keyword_overlap(idea, purpose)
        coord_score = self._coord_domain_overlap(idea, purpose)
        return _clamp(0.50 * keyword_score + 0.50 * coord_score)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _keyword_overlap(
        self, idea: IdeaProposal, purpose: AppIdeationPurpose
    ) -> float:
        """Fraction of domain + population keywords present in the idea text."""
        purpose_tokens = _keyword_tokens(purpose.domain) | _keyword_tokens(
            purpose.user_population
        )
        if not purpose_tokens:
            return 0.5

        idea_tokens = _keyword_tokens(idea.title) | _keyword_tokens(idea.hypothesis)
        if not idea_tokens:
            return 0.0

        hits = purpose_tokens & idea_tokens
        return len(hits) / len(purpose_tokens)

    def _coord_domain_overlap(
        self, idea: IdeaProposal, purpose: AppIdeationPurpose
    ) -> float:
        """Overlap between the idea's coordinates and the domain's
        recommended coordinate set."""
        domain_key = purpose.domain.strip().lower()
        relevant = _DOMAIN_COORD_MAP.get(domain_key, _DEFAULT_DOMAIN_COORDS)
        if not relevant:
            return 0.5  # unknown domain → neutral score

        coords = idea.coordinates or set()
        if not coords:
            return 0.0

        overlap = len(coords & relevant)
        return overlap / len(coords)

    def _softmax_normalize(
        self, scores: list[float], temperature: float = 1.0
    ) -> list[float]:
        """Softmax normalization with *temperature*.

        Higher temperature → more uniform distribution (exploration).
        Lower temperature  → winner-takes-all (exploitation).

        Handles empty lists, all-zero inputs, and numerical overflow.
        """
        if not scores:
            return []

        temperature = max(temperature, 1e-8)

        scaled = [s / temperature for s in scores]
        max_s = max(scaled)
        exps = [math.exp(s - max_s) for s in scaled]
        total = sum(exps)

        if total <= 0:
            n = len(scores)
            return [1.0 / n] * n

        return [e / total for e in exps]


# ── FeasibilityFilter ────────────────────────────────────────────────────────


class FeasibilityFilter:
    """Filters ideas by feasibility under various constraints."""

    # Coordinates that tend to require an LLM for good UX.
    LLM_PRONE_COORDS: frozenset[ApplicationCoordinate] = frozenset({
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.MATCHING,
        ApplicationCoordinate.SIMULATION,
    })

    # Coordinates that are inherently algorithmic (high no-LLM score).
    ALGORITHMIC_COORDS: frozenset[ApplicationCoordinate] = frozenset({
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.COMPARISON,
        ApplicationCoordinate.SCHEDULING,
    })

    # Per-coordinate Flask compatibility scores.
    _FLASK_COMPAT: dict[ApplicationCoordinate, float] = {
        ApplicationCoordinate.DATA_INGESTION: 0.85,
        ApplicationCoordinate.DATA_TRANSFORMATION: 0.80,
        ApplicationCoordinate.DATA_VISUALIZATION: 0.75,
        ApplicationCoordinate.DATA_EXPORT: 0.90,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND: 0.90,
        ApplicationCoordinate.BATCH_PROCESSING: 0.70,
        ApplicationCoordinate.COMPARISON: 0.85,
        ApplicationCoordinate.AGGREGATION: 0.85,
        ApplicationCoordinate.FORM_WORKFLOW: 1.00,
        ApplicationCoordinate.FILE_PROCESSING: 0.85,
        ApplicationCoordinate.REAL_TIME_FEEDBACK: 0.50,
        ApplicationCoordinate.COLLABORATIVE_EDITING: 0.40,
        ApplicationCoordinate.SCHEDULING: 0.80,
        ApplicationCoordinate.INVENTORY: 0.85,
        ApplicationCoordinate.MATCHING: 0.75,
        ApplicationCoordinate.SIMULATION: 0.60,
        ApplicationCoordinate.AUDIT_TRAIL: 0.85,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION: 0.75,
        ApplicationCoordinate.STATIC_REPORT: 0.90,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD: 0.70,
        ApplicationCoordinate.NOTIFICATION: 0.80,
        ApplicationCoordinate.API_PROVISION: 0.90,
    }

    # Per-coordinate library availability scores.
    _LIB_AVAIL: dict[ApplicationCoordinate, float] = {
        ApplicationCoordinate.DATA_INGESTION: 0.90,
        ApplicationCoordinate.DATA_TRANSFORMATION: 0.95,
        ApplicationCoordinate.DATA_VISUALIZATION: 0.95,
        ApplicationCoordinate.DATA_EXPORT: 0.90,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND: 0.90,
        ApplicationCoordinate.BATCH_PROCESSING: 0.85,
        ApplicationCoordinate.COMPARISON: 0.80,
        ApplicationCoordinate.AGGREGATION: 0.90,
        ApplicationCoordinate.FORM_WORKFLOW: 0.90,
        ApplicationCoordinate.FILE_PROCESSING: 0.85,
        ApplicationCoordinate.REAL_TIME_FEEDBACK: 0.70,
        ApplicationCoordinate.COLLABORATIVE_EDITING: 0.55,
        ApplicationCoordinate.SCHEDULING: 0.85,
        ApplicationCoordinate.INVENTORY: 0.75,
        ApplicationCoordinate.MATCHING: 0.70,
        ApplicationCoordinate.SIMULATION: 0.65,
        ApplicationCoordinate.AUDIT_TRAIL: 0.80,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION: 0.75,
        ApplicationCoordinate.STATIC_REPORT: 0.90,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD: 0.75,
        ApplicationCoordinate.NOTIFICATION: 0.80,
        ApplicationCoordinate.API_PROVISION: 0.90,
    }

    # Per-coordinate frontend complexity scores (higher = simpler frontend).
    _FRONTEND_SIMPLE: dict[ApplicationCoordinate, float] = {
        ApplicationCoordinate.DATA_INGESTION: 0.80,
        ApplicationCoordinate.DATA_TRANSFORMATION: 0.75,
        ApplicationCoordinate.DATA_VISUALIZATION: 0.55,
        ApplicationCoordinate.DATA_EXPORT: 0.90,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND: 0.80,
        ApplicationCoordinate.BATCH_PROCESSING: 0.85,
        ApplicationCoordinate.COMPARISON: 0.70,
        ApplicationCoordinate.AGGREGATION: 0.80,
        ApplicationCoordinate.FORM_WORKFLOW: 0.90,
        ApplicationCoordinate.FILE_PROCESSING: 0.85,
        ApplicationCoordinate.REAL_TIME_FEEDBACK: 0.40,
        ApplicationCoordinate.COLLABORATIVE_EDITING: 0.20,
        ApplicationCoordinate.SCHEDULING: 0.70,
        ApplicationCoordinate.INVENTORY: 0.75,
        ApplicationCoordinate.MATCHING: 0.65,
        ApplicationCoordinate.SIMULATION: 0.50,
        ApplicationCoordinate.AUDIT_TRAIL: 0.85,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION: 0.70,
        ApplicationCoordinate.STATIC_REPORT: 1.00,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD: 0.50,
        ApplicationCoordinate.NOTIFICATION: 0.75,
        ApplicationCoordinate.API_PROVISION: 0.95,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def no_llm_feasibility(self, idea: IdeaProposal) -> float:
        """Score feasibility under the no-LLM constraint.

        Returns
        -------
        float
            0.0 – fundamentally requires LLM;
            1.0 – purely algorithmic / structural.

        Formula (§5.4)::

            llm_dependency  = |coords ∩ LLM_PRONE| / |coords|
            structural_value = |coords ∩ ALGORITHMIC| / |coords|
            algorithmic_depth = idea.gain.theorem_yield

            if llm_dependency > 0.8: return 0.0
            return 0.4 * structural_value + 0.4 * algorithmic_depth
                   + 0.2 * (1 - llm_dependency)
        """
        coords = idea.coordinates or set()
        if not coords:
            return 0.5

        n = len(coords)
        llm_dep = len(coords & self.LLM_PRONE_COORDS) / n
        struct_val = len(coords & self.ALGORITHMIC_COORDS) / n
        algo_depth = _clamp(idea.gain.theorem_yield)

        if llm_dep > 0.8:
            return 0.0

        return _clamp(0.4 * struct_val + 0.4 * algo_depth + 0.2 * (1.0 - llm_dep))

    def flask_compatibility(self, idea: IdeaProposal) -> float:
        """Mean per-coordinate Flask compatibility for the idea.

        Flask excels at request/response, forms, REST APIs, Jinja2
        templates; struggles with real-time, complex shared state, and
        heavy background computation.
        """
        return self._mean_coord_score(idea, self._FLASK_COMPAT)

    def library_availability(self, idea: IdeaProposal) -> float:
        """Mean per-coordinate Python library availability for the idea."""
        return self._mean_coord_score(idea, self._LIB_AVAIL)

    def frontend_complexity(self, idea: IdeaProposal) -> float:
        """Mean per-coordinate frontend simplicity score.

        Higher = simpler frontend (vanilla JS is fine).
        """
        return self._mean_coord_score(idea, self._FRONTEND_SIMPLE)

    def combined_feasibility(self, idea: IdeaProposal) -> float:
        """Geometric mean of all four feasibility dimensions.

        The geometric mean penalises a single very-low dimension more
        heavily than the arithmetic mean.
        """
        dims = [
            max(self.no_llm_feasibility(idea), 1e-12),
            max(self.flask_compatibility(idea), 1e-12),
            max(self.library_availability(idea), 1e-12),
            max(self.frontend_complexity(idea), 1e-12),
        ]
        product = 1.0
        for d in dims:
            product *= d
        return _clamp(product ** (1.0 / len(dims)))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mean_coord_score(
        idea: IdeaProposal,
        lookup: dict[ApplicationCoordinate, float],
    ) -> float:
        """Return the mean of *lookup[c]* for each coordinate in *idea*.

        Falls back to 0.5 for any coordinate not in *lookup* and for
        ideas with no coordinates at all.
        """
        coords = idea.coordinates or set()
        if not coords:
            return 0.5

        scores = [lookup.get(c, 0.5) for c in coords]
        return _safe_mean(scores)


# ── NoveltyMetric ────────────────────────────────────────────────────────────


class NoveltyMetric:
    """Computes novelty of an idea relative to the existing portfolio."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def jaccard_novelty(
        self, idea_coords: set, portfolio: IdeaPortfolio
    ) -> float:
        """Jaccard distance from *idea_coords* to every app in the portfolio.

        For each app the Jaccard similarity is::

            J(A, B) = |A ∩ B| / |A ∪ B|

        and the distance is ``1 − J``.  The novelty of the idea is the
        *minimum* distance to any portfolio app – a low value means the
        idea closely duplicates something that already exists.

        Returns
        -------
        float
            1.0 – fully novel (no overlap with any portfolio app);
            0.0 – identical coordinate set already in portfolio.
        """
        if not idea_coords:
            return 0.0

        apps: list[ExistingApp] = portfolio.ideas if portfolio.ideas else []
        if not apps:
            return 1.0  # empty portfolio → everything is novel

        min_dist = 1.0
        for app in apps:
            app_coords = app.coordinates or set()
            if not app_coords:
                continue
            intersection = len(idea_coords & app_coords)
            union = len(idea_coords | app_coords)
            if union == 0:
                continue
            similarity = intersection / union
            dist = 1.0 - similarity
            if dist < min_dist:
                min_dist = dist

        return _clamp(min_dist)

    def structural_novelty(
        self, idea: IdeaProposal, portfolio: IdeaPortfolio
    ) -> float:
        """Distance in gain-profile space.

        Portfolio apps don't carry a full :class:`GainProfile`, so we
        build a proxy gain vector from the app's observable features:

        * ``proxy_yield``  – normalised coordinate count.
        * ``proxy_impact`` – quality-tier ordinal (high=1, med=0.5, low=0.2).
        * ``proxy_cost``   – log(1 + user_base_estimate) / 20  (heuristic).
        * ``proxy_uncert`` – fixed 0.5 (no data).

        We then compute the Euclidean distance between the idea's
        gain vector and each proxy, take the minimum, and normalise
        into ``[0, 1]``.
        """
        idea_vec = self._gain_to_vec(idea.gain)

        apps: list[ExistingApp] = portfolio.ideas if portfolio.ideas else []
        if not apps:
            return 1.0

        min_dist = float("inf")
        for app in apps:
            proxy = self._app_proxy_vec(app)
            dist = self._euclidean(idea_vec, proxy)
            if dist < min_dist:
                min_dist = dist

        # Normalise: max possible Euclidean distance for 4-d unit vectors
        # is sqrt(4) = 2.  We scale so that distance=2 → novelty=1.
        max_dist = 2.0
        return _clamp(min_dist / max_dist)

    def combined_novelty(
        self,
        idea: IdeaProposal,
        portfolio: IdeaPortfolio,
        weights: dict[str, float] | None = None,
    ) -> float:
        """Weighted combination of Jaccard and structural novelty.

        Parameters
        ----------
        weights : dict, optional
            Keys ``"jaccard"`` and ``"structural"`` with float values.
            Defaults to ``{"jaccard": 0.6, "structural": 0.4}``.
        """
        if weights is None:
            weights = {"jaccard": 0.6, "structural": 0.4}

        w_j = weights.get("jaccard", 0.6)
        w_s = weights.get("structural", 0.4)
        w_total = w_j + w_s
        if w_total <= 0:
            w_total = 1.0

        j_score = self.jaccard_novelty(idea.coordinates or set(), portfolio)
        s_score = self.structural_novelty(idea, portfolio)

        return _clamp((w_j * j_score + w_s * s_score) / w_total)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _gain_to_vec(gain: GainProfile) -> tuple[float, float, float, float]:
        """Convert a :class:`GainProfile` to a 4-d vector in ``[0, 1]^4``."""
        return (
            _clamp(gain.theorem_yield),
            _clamp(gain.bridge_impact),
            _clamp(gain.cost / max(gain.cost, 500.0)) if gain.cost > 0 else 0.0,
            _clamp(gain.uncertainty),
        )

    @staticmethod
    def _app_proxy_vec(app: ExistingApp) -> tuple[float, float, float, float]:
        """Build a proxy gain vector from an :class:`ExistingApp`."""
        total = len(ApplicationCoordinate)
        coords = app.coordinates or set()
        proxy_yield = len(coords) / max(total / 3.0, 1.0)
        proxy_yield = min(proxy_yield, 1.0)

        tier_map = {"high": 1.0, "medium": 0.5, "low": 0.2}
        proxy_impact = tier_map.get(app.quality_tier, 0.5)

        ub = max(app.user_base_estimate, 0)
        proxy_cost = math.log1p(ub) / 20.0
        proxy_cost = min(proxy_cost, 1.0)

        proxy_uncert = 0.5

        return (proxy_yield, proxy_impact, proxy_cost, proxy_uncert)

    @staticmethod
    def _euclidean(
        a: tuple[float, ...], b: tuple[float, ...]
    ) -> float:
        """Euclidean distance between two equal-length tuples."""
        return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))
