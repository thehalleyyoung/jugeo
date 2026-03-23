"""A purpose-conditioned novelty functional – theory2.tex Ch57.

Defines a weighted functional that evaluates candidate mathematical ideas by
combining leverage (how much a new idea unlocks), tractability (how provable
it is), and semantic relevance (how well-aligned it is with the current
research purpose and obstruction landscape).

# copilot: generated as part of jugeo.ideation.novelty_search

Module layout::

    ┌─────────────────────────────────────────────────────────────────────┐
    │  NoveltyFunctionalConfig          – frozen weight/normalisation cfg  │
    │  LeverageScore                    – per-idea leverage record         │
    │  TractabilityScore                – per-idea tractability record     │
    │  SemanticRelevanceScore           – per-idea semantic alignment      │
    │  NoveltyFunctionalValue           – combined functional evaluation   │
    │  PurposeConditionedNoveltyAnalyzer– core computation methods         │
    │  PurposeConditionedNoveltyWitness – accumulates values, statistics   │
    │  PurposeConditionedNoveltyCoordinator – end-to-end pipeline          │
    └─────────────────────────────────────────────────────────────────────┘

Background
----------
The **novelty functional** is the central scoring instrument of the jugeo
ideation pipeline.  It answers the question: *"Among all candidate ideas that
are novel relative to the existing portfolio, which ones are most worth
pursuing?"*

The functional is defined as:

    F(idea) = w_L · L(idea) + w_T · T(idea) + w_S · S(idea)

where:
  - L(idea) is the **leverage score**: an estimate of how many currently
    blocked theorems or open conjectures become tractable once the idea is
    adopted and formalised.  High leverage means the idea is a key that
    unlocks many doors.  It is computed from the dependency graph of open
    problems by counting reachable nodes after the idea is provisionally
    assumed.
  - T(idea) is the **tractability score**: an estimate of the probability
    that the idea can be successfully formalised and verified in the
    available proof-assistant environment (Lean 4, Agda, Coq) given the
    existing lemma library and tool support.  Low tractability indicates
    that the idea, while interesting, may be stuck in the "too hard to
    prove" category for the foreseeable future.
  - S(idea) is the **semantic relevance score**: a measure of how
    closely the idea's vocabulary and conceptual structure aligns with
    the stated research purpose and the classes of currently known
    obstructions.  This prevents the functional from selecting ideas that
    are highly leverageable and tractable but belong to an orthogonal
    subfield.

The default weight vector is (w_L, w_T, w_S) = (0.35, 0.30, 0.35),
reflecting the judgement that leverage and semantic alignment are slightly
more important than tractability — a breakthrough idea that is hard to prove
is still more valuable than an easy-to-prove idea with little impact.

Normalisation
-------------
After computing raw functional values for a batch of candidates, the values
are normalised using a softmax function with a configurable temperature
parameter τ.  Low temperature (τ → 0) creates a winner-take-all regime;
high temperature (τ → ∞) flattens the distribution and treats all ideas as
equally worthy.  The default temperature of 1.0 produces a balanced
distribution that rewards high-scoring ideas without completely ignoring
low-scoring ones.

Theory references
-----------------
* theory2.tex §57.4 "The Novelty Functional and Its Components"
* theory2.tex §57.7 "Softmax Normalisation and Temperature Scheduling"
* theory2.tex §57.10 "Top-k Selection and Diversity Balancing"

Usage example::

    from jugeo.ideation.novelty_search.a_purpose_conditioned_novelty_func import (
        PurposeConditionedNoveltyCoordinator,
        NoveltyFunctionalConfig,
    )

    ideas = [{"id": "i1", "title": "Étale cohomology bridge", "leverage": 0.7}]
    obstructions = [{"id": "o1", "class": "H2 obstruction"}]
    purpose_keywords = ["cohomology", "obstruction", "étale", "descent"]

    coordinator = PurposeConditionedNoveltyCoordinator()
    values = coordinator.run(ideas, obstructions, purpose_keywords)
    print(coordinator.report())
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    from jugeo.ideation.novelty_search.models import (
        NoveltySearchProblem,
        SearchResult,
        NoveltyMetricSpec,
        MetricKind,
    )
except ImportError:
    NoveltySearchProblem = None  # type: ignore[assignment,misc]
    SearchResult = None  # type: ignore[assignment,misc]
    NoveltyMetricSpec = None  # type: ignore[assignment,misc]
    MetricKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.novelty_search.novelty_versus_useful_novelty_leve import (
        NoveltyVsUsefulNoveltyAnalyzer,
        UsefulNoveltyConfig,
        NoveltyLevel,
    )
except ImportError:
    NoveltyVsUsefulNoveltyAnalyzer = None  # type: ignore[assignment,misc]
    UsefulNoveltyConfig = None  # type: ignore[assignment,misc]
    NoveltyLevel = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_LEVERAGE_WEIGHT: float = 0.35
_DEFAULT_TRACTABILITY_WEIGHT: float = 0.30
_DEFAULT_SEMANTIC_WEIGHT: float = 0.35
_DEFAULT_TEMPERATURE: float = 1.0
_DEFAULT_TOP_K: int = 10
_EPSILON: float = 1e-9

FUNCTIONAL_NARRATIVE: str = """
The purpose-conditioned novelty functional is the primary ranking instrument in
the jugeo ideation pipeline.  It converts a list of candidate ideas — each
already filtered for minimum useful novelty — into an ordered list of
recommendations that balances three independent axes of value.

Leverage axis
~~~~~~~~~~~~~
Leverage quantifies the *multiplier effect* of an idea.  An idea with leverage
score 1.0 would, if adopted, make every currently blocked theorem provable.
In practice, leverage scores range from 0.05 (the idea slightly helps one
obscure lemma) to 0.9 (the idea is a key insight that unlocks an entire
family of results).  The leverage computation inspects the proof dependency
graph: for each open node (theorem, lemma, or conjecture), it checks whether
the idea satisfies at least one of the open dependencies.  The fraction of
newly-reachable nodes, normalised by total open nodes, is the raw leverage
estimate.  A cascade factor amplifies this when chains of dependencies open
up: proving A unlocks B which unlocks C, so the effective leverage of A is
proportional to the length of the longest reachable chain.

Tractability axis
~~~~~~~~~~~~~~~~~
Tractability quantifies how *feasible* it is to formalise and prove the idea.
It is estimated from three proxies: (1) the proof complexity, measured by the
estimated number of proof steps in Lean 4; (2) the fraction of required lemmas
that are already available in Mathlib or the local lemma library; and (3) the
dependency depth, measured as the maximum length of the dependency chain that
must be completed before the proof can close.  High tractability (close to 1.0)
means the idea requires only a few straightforward proof steps on top of
existing infrastructure.  Low tractability (close to 0.0) means the idea
requires building substantial new proof infrastructure from scratch.

Semantic relevance axis
~~~~~~~~~~~~~~~~~~~~~~~
Semantic relevance quantifies how *on-topic* the idea is.  The research purpose
is encoded as a list of high-value keywords (e.g., ["cohomology", "obstruction",
"étale", "descent"]) and the obstruction landscape is encoded as a list of
obstruction class names.  The relevance score is computed as a weighted
combination of: (1) keyword overlap between the idea's description and the
purpose keywords; (2) alignment between the idea's vocabulary and the
obstruction class names; and (3) inverse purpose distance (ideas that are
semantically close to the stated purpose score higher).

Temperature scheduling
~~~~~~~~~~~~~~~~~~~~~~
After computing raw functional values, a softmax normalisation is applied.
The temperature parameter τ controls the sharpness of the distribution.  When
τ is small (e.g., 0.1), the softmax concentrates probability mass on the
top-scoring idea.  When τ is large (e.g., 5.0), the distribution is nearly
uniform.  The default τ = 1.0 provides a balanced distribution.  For
exploration phases of the research programme (when diversity is more important
than optimisation), consider increasing τ.  For exploitation phases (when one
promising idea has emerged and needs to be pursued), consider decreasing τ.
"""

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval ``[lo, hi]``."""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _value_id() -> str:
    """Generate a unique functional value identifier prefixed with ``fv-``."""
    return f"fv-{uuid.uuid4().hex[:12]}"


def _log_sum_exp(values: list[float]) -> float:
    """Numerically stable log-sum-exp computation.

    Used internally by the softmax to avoid overflow with large values.

    Parameters
    ----------
    values:
        List of floating-point values.

    Returns
    -------
    float
        log(∑ exp(v)) computed in a numerically stable manner.
    """
    if not values:
        return 0.0
    max_val = max(values)
    shifted = [v - max_val for v in values]
    log_sum = math.log(sum(math.exp(s) for s in shifted) + _EPSILON)
    return max_val + log_sum


def _softmax(values: list[float], temp: float = 1.0) -> list[float]:
    """Compute the softmax of *values* with temperature *temp*.

    Parameters
    ----------
    values:
        Raw functional values to normalise.
    temp:
        Temperature parameter τ > 0.  Lower temperature → sharper
        distribution; higher temperature → flatter distribution.

    Returns
    -------
    list[float]
        Softmax probabilities summing to 1.0.
    """
    if not values:
        return []
    temp = max(temp, _EPSILON)
    scaled = [v / temp for v in values]
    lse = _log_sum_exp(scaled)
    return [math.exp(s - lse) for s in scaled]


def _extract_tokens(idea: dict[str, Any]) -> set[str]:
    """Extract a token set from an idea dictionary."""
    tokens: set[str] = set()
    if "tokens" in idea and isinstance(idea["tokens"], (set, list, frozenset)):
        tokens.update(str(t).lower() for t in idea["tokens"])
    for field_name in ("title", "purpose", "hypothesis", "target_area", "description"):
        text = idea.get(field_name, "")
        if isinstance(text, str):
            words = re.findall(r"[a-zA-Z]{3,}", text.lower())
            tokens.update(words)
    return tokens


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltyFunctionalConfig:
    """Configuration for the purpose-conditioned novelty functional.

    Attributes
    ----------
    leverage_weight:
        Weight w_L in the functional F = w_L·L + w_T·T + w_S·S.
    tractability_weight:
        Weight w_T in the functional.
    semantic_weight:
        Weight w_S in the functional.
    normalization:
        Normalisation method to apply to raw values.  Currently only
        ``"softmax"`` is implemented.
    temperature:
        Softmax temperature τ.  Default 1.0 gives a balanced distribution.
    top_k:
        Number of top ideas to return from ``top_k_ideas``.
    """

    leverage_weight: float = _DEFAULT_LEVERAGE_WEIGHT
    tractability_weight: float = _DEFAULT_TRACTABILITY_WEIGHT
    semantic_weight: float = _DEFAULT_SEMANTIC_WEIGHT
    normalization: str = "softmax"
    temperature: float = _DEFAULT_TEMPERATURE
    top_k: int = _DEFAULT_TOP_K

    def validate(self) -> None:
        """Raise ``ValueError`` if configuration is inconsistent."""
        total = self.leverage_weight + self.tractability_weight + self.semantic_weight
        if abs(total - 1.0) > 0.05:
            raise ValueError(
                f"NoveltyFunctionalConfig weights sum to {total:.4f}; expected ≈ 1.0"
            )
        if self.temperature <= 0:
            raise ValueError(f"temperature must be > 0, got {self.temperature}")
        if self.top_k < 1:
            raise ValueError(f"top_k must be ≥ 1, got {self.top_k}")


# ---------------------------------------------------------------------------
# Score dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeverageScore:
    """Per-idea leverage assessment.

    Attributes
    ----------
    idea_id:
        Identifier of the idea being assessed.
    leverage:
        Overall leverage score in [0, 1].
    unlocked_theorem_count:
        Estimated number of theorems newly unlocked by this idea.
    obstruction_reduction:
        Fraction of current obstructions addressed by this idea, in [0, 1].
    cascade_factor:
        Multiplicative cascade effect: how many additional theorems become
        provable as a consequence of the directly unlocked ones.
    """

    idea_id: str
    leverage: float
    unlocked_theorem_count: int
    obstruction_reduction: float
    cascade_factor: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "idea_id": self.idea_id,
            "leverage": self.leverage,
            "unlocked_theorem_count": self.unlocked_theorem_count,
            "obstruction_reduction": self.obstruction_reduction,
            "cascade_factor": self.cascade_factor,
        }


@dataclass(frozen=True, slots=True)
class TractabilityScore:
    """Per-idea tractability assessment.

    Attributes
    ----------
    idea_id:
        Identifier of the idea being assessed.
    tractability:
        Overall tractability score in [0, 1].
    proof_complexity_estimate:
        Estimated number of proof steps (normalised to [0, 1] by assuming
        a maximum of 10 000 steps).
    existing_support_fraction:
        Fraction of required lemmas already present in the available
        proof-assistant library.
    dependency_depth:
        Maximum dependency chain depth that must be traversed before this
        idea can be fully proved.
    """

    idea_id: str
    tractability: float
    proof_complexity_estimate: float
    existing_support_fraction: float
    dependency_depth: int

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "idea_id": self.idea_id,
            "tractability": self.tractability,
            "proof_complexity_estimate": self.proof_complexity_estimate,
            "existing_support_fraction": self.existing_support_fraction,
            "dependency_depth": self.dependency_depth,
        }


@dataclass(frozen=True, slots=True)
class SemanticRelevanceScore:
    """Per-idea semantic relevance to the research purpose.

    Attributes
    ----------
    idea_id:
        Identifier of the idea being assessed.
    relevance:
        Overall semantic relevance score in [0, 1].
    keyword_overlap:
        Fraction of purpose keywords that appear in the idea's description.
    obstruction_alignment:
        Alignment between the idea's vocabulary and obstruction class names.
    purpose_distance:
        Semantic distance from the idea to the stated purpose (lower is
        better; 0 means perfect alignment).
    """

    idea_id: str
    relevance: float
    keyword_overlap: float
    obstruction_alignment: float
    purpose_distance: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "idea_id": self.idea_id,
            "relevance": self.relevance,
            "keyword_overlap": self.keyword_overlap,
            "obstruction_alignment": self.obstruction_alignment,
            "purpose_distance": self.purpose_distance,
        }


@dataclass(frozen=True, slots=True)
class NoveltyFunctionalValue:
    """The combined evaluation of an idea by the novelty functional.

    Attributes
    ----------
    value_id:
        Unique identifier for this evaluation record.
    idea_id:
        Identifier of the evaluated idea.
    leverage:
        Leverage score component.
    tractability:
        Tractability score component.
    semantic_relevance:
        Semantic relevance score component.
    functional_value:
        Weighted sum F = w_L·L + w_T·T + w_S·S.
    rank:
        Position in the ranked list of candidates (1 = best).  Set to 0
        until ``rank_ideas`` is called.
    timestamp:
        ISO-8601 timestamp of computation.
    """

    value_id: str
    idea_id: str
    leverage: float
    tractability: float
    semantic_relevance: float
    functional_value: float
    rank: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "value_id": self.value_id,
            "idea_id": self.idea_id,
            "leverage": self.leverage,
            "tractability": self.tractability,
            "semantic_relevance": self.semantic_relevance,
            "functional_value": self.functional_value,
            "rank": self.rank,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"[rank={self.rank}] idea={self.idea_id!r} "
            f"F={self.functional_value:.4f} "
            f"(L={self.leverage:.3f}, T={self.tractability:.3f}, "
            f"S={self.semantic_relevance:.3f})"
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class PurposeConditionedNoveltyAnalyzer:
    """Core computation engine for the purpose-conditioned novelty functional.

    This class computes the three component scores (leverage, tractability,
    semantic relevance) for each candidate idea and combines them into a
    ``NoveltyFunctionalValue`` using the configured weight vector.

    The class is stateless: all inputs are passed as arguments and all
    outputs are returned as typed dataclasses.

    Parameters
    ----------
    config:
        Default ``NoveltyFunctionalConfig``.  Individual methods accept an
        optional *config* override.
    """

    def __init__(self, config: NoveltyFunctionalConfig | None = None) -> None:
        self._config = config or NoveltyFunctionalConfig()

    # ------------------------------------------------------------------
    # Component computations
    # ------------------------------------------------------------------

    def compute_leverage(
        self,
        idea: dict[str, Any],
        obstructions: list[dict[str, Any]],
    ) -> LeverageScore:
        """Estimate the leverage of *idea* against *obstructions*.

        The heuristic:
        - Count how many obstructions the idea's tokens overlap with.
        - Estimate unlocked theorem count from leverage metadata or default.
        - Estimate cascade factor from idea metadata or default.

        Parameters
        ----------
        idea:
            Candidate idea dictionary.
        obstructions:
            Current list of obstruction records.

        Returns
        -------
        LeverageScore
        """
        idea_id = str(idea.get("id", _value_id()))
        idea_tokens = _extract_tokens(idea)
        total_obs = max(len(obstructions), 1)
        addressed = sum(
            1 for obs in obstructions
            if set(re.findall(r"[a-zA-Z]{3,}", str(obs.get("class", "")).lower()))
            & idea_tokens
        )
        obstruction_reduction = _clamp(addressed / total_obs)
        raw_leverage = float(idea.get("leverage", 0.5))
        unlocked_theorem_count = int(idea.get("unlocked_theorems", max(0, int(raw_leverage * 10))))
        cascade_factor = float(idea.get("cascade_factor", 1.0 + raw_leverage * 0.5))
        leverage = _clamp(
            0.5 * raw_leverage + 0.3 * obstruction_reduction + 0.2 * _clamp(cascade_factor - 1.0)
        )
        return LeverageScore(
            idea_id=idea_id,
            leverage=leverage,
            unlocked_theorem_count=unlocked_theorem_count,
            obstruction_reduction=obstruction_reduction,
            cascade_factor=cascade_factor,
        )

    def compute_tractability(
        self,
        idea: dict[str, Any],
        existing_support: list[str],
    ) -> TractabilityScore:
        """Estimate the tractability of *idea*.

        The heuristic:
        - Use ``tractability`` metadata if available.
        - Estimate existing support fraction from how many support lemma IDs
          overlap with the idea's token set.
        - Estimate proof complexity from ``proof_steps`` metadata.

        Parameters
        ----------
        idea:
            Candidate idea dictionary.
        existing_support:
            List of lemma/theorem IDs in the current proof-assistant library.

        Returns
        -------
        TractabilityScore
        """
        idea_id = str(idea.get("id", _value_id()))
        raw_tractability = float(idea.get("tractability", 0.5))
        proof_steps = float(idea.get("proof_steps", 200))
        proof_complexity_estimate = _clamp(1.0 - math.exp(-proof_steps / 1000.0))
        idea_tokens = _extract_tokens(idea)
        if existing_support:
            supported = sum(
                1 for lemma_id in existing_support
                if any(tok in lemma_id.lower() for tok in idea_tokens if len(tok) > 4)
            )
            existing_support_fraction = _clamp(supported / len(existing_support))
        else:
            existing_support_fraction = 0.3  # conservative default
        dependency_depth = int(idea.get("dependency_depth", 3))
        depth_penalty = _clamp(1.0 - 0.1 * dependency_depth)
        tractability = _clamp(
            0.5 * raw_tractability
            + 0.25 * existing_support_fraction
            + 0.15 * (1.0 - proof_complexity_estimate)
            + 0.10 * depth_penalty
        )
        return TractabilityScore(
            idea_id=idea_id,
            tractability=tractability,
            proof_complexity_estimate=proof_complexity_estimate,
            existing_support_fraction=existing_support_fraction,
            dependency_depth=dependency_depth,
        )

    def compute_semantic_relevance(
        self,
        idea: dict[str, Any],
        purpose_keywords: list[str],
        obstruction_classes: list[str],
    ) -> SemanticRelevanceScore:
        """Estimate the semantic relevance of *idea* to the stated purpose.

        The heuristic:
        - Keyword overlap: fraction of purpose_keywords present in idea text.
        - Obstruction alignment: fraction of obstruction class tokens present.
        - Purpose distance: 1 - keyword_overlap (proxy).

        Parameters
        ----------
        idea:
            Candidate idea dictionary.
        purpose_keywords:
            List of high-value keywords representing the research purpose.
        obstruction_classes:
            List of obstruction class name strings.

        Returns
        -------
        SemanticRelevanceScore
        """
        idea_id = str(idea.get("id", _value_id()))
        idea_tokens = _extract_tokens(idea)
        purpose_set = {kw.lower() for kw in purpose_keywords}
        if purpose_set:
            keyword_overlap = _clamp(len(idea_tokens & purpose_set) / len(purpose_set))
        else:
            keyword_overlap = 0.5
        obs_tokens: set[str] = set()
        for cls in obstruction_classes:
            obs_tokens.update(re.findall(r"[a-zA-Z]{3,}", cls.lower()))
        if obs_tokens:
            obstruction_alignment = _clamp(len(idea_tokens & obs_tokens) / len(obs_tokens))
        else:
            obstruction_alignment = 0.3
        purpose_distance = _clamp(1.0 - keyword_overlap)
        relevance = _clamp(
            0.55 * keyword_overlap + 0.30 * obstruction_alignment + 0.15 * (1.0 - purpose_distance)
        )
        return SemanticRelevanceScore(
            idea_id=idea_id,
            relevance=relevance,
            keyword_overlap=keyword_overlap,
            obstruction_alignment=obstruction_alignment,
            purpose_distance=purpose_distance,
        )

    # ------------------------------------------------------------------
    # Functional combination
    # ------------------------------------------------------------------

    def evaluate_functional(
        self,
        leverage: LeverageScore,
        tractability: TractabilityScore,
        relevance: SemanticRelevanceScore,
        config: NoveltyFunctionalConfig | None = None,
    ) -> NoveltyFunctionalValue:
        """Combine the three component scores into a ``NoveltyFunctionalValue``.

        Parameters
        ----------
        leverage:
            Leverage score for the idea.
        tractability:
            Tractability score for the idea.
        relevance:
            Semantic relevance score for the idea.
        config:
            Optional configuration override.

        Returns
        -------
        NoveltyFunctionalValue
            Combined evaluation with rank set to 0 (unranked).
        """
        cfg = config or self._config
        fv = (
            cfg.leverage_weight * leverage.leverage
            + cfg.tractability_weight * tractability.tractability
            + cfg.semantic_weight * relevance.relevance
        )
        return NoveltyFunctionalValue(
            value_id=_value_id(),
            idea_id=leverage.idea_id,
            leverage=leverage.leverage,
            tractability=tractability.tractability,
            semantic_relevance=relevance.relevance,
            functional_value=_clamp(fv),
            rank=0,
            timestamp=_now_iso(),
        )

    def rank_ideas(
        self, values: list[NoveltyFunctionalValue]
    ) -> list[NoveltyFunctionalValue]:
        """Sort *values* by functional_value descending and assign ranks.

        Parameters
        ----------
        values:
            List of ``NoveltyFunctionalValue`` instances to rank.

        Returns
        -------
        list[NoveltyFunctionalValue]
            Sorted list with rank fields populated (rank 1 = highest value).
        """
        sorted_vals = sorted(values, key=lambda v: v.functional_value, reverse=True)
        return [
            NoveltyFunctionalValue(
                value_id=v.value_id,
                idea_id=v.idea_id,
                leverage=v.leverage,
                tractability=v.tractability,
                semantic_relevance=v.semantic_relevance,
                functional_value=v.functional_value,
                rank=i + 1,
                timestamp=v.timestamp,
            )
            for i, v in enumerate(sorted_vals)
        ]

    def softmax_weights(
        self, values: list[float], temperature: float
    ) -> list[float]:
        """Compute softmax-normalised weights for *values*.

        Parameters
        ----------
        values:
            Raw scores to normalise.
        temperature:
            Softmax temperature τ > 0.

        Returns
        -------
        list[float]
            Probability weights summing to 1.0.
        """
        return _softmax(values, temperature)

    def top_k_ideas(
        self, values: list[NoveltyFunctionalValue], k: int
    ) -> list[NoveltyFunctionalValue]:
        """Return the top-*k* ideas by functional value.

        If *k* exceeds the list length, all values are returned.

        Parameters
        ----------
        values:
            Ranked list of functional values.
        k:
            Number of top ideas to return.

        Returns
        -------
        list[NoveltyFunctionalValue]
        """
        sorted_vals = sorted(values, key=lambda v: v.functional_value, reverse=True)
        return sorted_vals[:k]


# ---------------------------------------------------------------------------
# Witness (accumulator)
# ---------------------------------------------------------------------------


class PurposeConditionedNoveltyWitness:
    """Accumulates ``NoveltyFunctionalValue`` records and provides statistics.

    This class follows the witness pattern: it is a lightweight accumulator
    that records evaluations as they are produced and answers aggregate
    queries for downstream reporting.

    Usage::

        witness = PurposeConditionedNoveltyWitness()
        witness.record(value)
        print(witness.top_idea())
        print(witness.functional_stats())
    """

    def __init__(self) -> None:
        self._values: list[NoveltyFunctionalValue] = []

    def record(self, v: NoveltyFunctionalValue) -> None:
        """Append *v* to the internal record list."""
        self._values.append(v)

    def top_idea(self) -> NoveltyFunctionalValue | None:
        """Return the idea with the highest functional value, or None."""
        if not self._values:
            return None
        return max(self._values, key=lambda v: v.functional_value)

    def functional_stats(self) -> dict[str, Any]:
        """Return aggregate statistics over recorded values.

        Returns
        -------
        dict
            Keys: ``count``, ``mean``, ``std``, ``min``, ``max``.
        """
        if not self._values:
            return {"count": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
        fvs = [v.functional_value for v in self._values]
        n = len(fvs)
        mean = sum(fvs) / n
        variance = sum((x - mean) ** 2 for x in fvs) / n
        return {
            "count": n,
            "mean": mean,
            "std": math.sqrt(variance),
            "min": min(fvs),
            "max": max(fvs),
        }

    def export(self) -> list[dict[str, Any]]:
        """Serialise all records to a list of plain dictionaries."""
        return [v.to_dict() for v in self._values]

    def count(self) -> int:
        """Return the total number of recorded values."""
        return len(self._values)

    def leverage_stats(self) -> dict[str, float]:
        """Return mean leverage and tractability across recorded values."""
        if not self._values:
            return {"mean_leverage": 0.0, "mean_tractability": 0.0, "mean_semantic": 0.0}
        n = len(self._values)
        return {
            "mean_leverage": sum(v.leverage for v in self._values) / n,
            "mean_tractability": sum(v.tractability for v in self._values) / n,
            "mean_semantic": sum(v.semantic_relevance for v in self._values) / n,
        }

    def summary_table(self) -> str:
        """Return an ASCII summary table of all recorded values."""
        if not self._values:
            return "No values recorded."
        header = f"{'Rank':>5} {'Idea':>20} {'F':>6} {'L':>6} {'T':>6} {'S':>6}"
        rows = [header, "-" * len(header)]
        for v in sorted(self._values, key=lambda v: v.functional_value, reverse=True):
            rows.append(
                f"{v.rank:>5} {v.idea_id:>20} {v.functional_value:>6.3f} "
                f"{v.leverage:>6.3f} {v.tractability:>6.3f} {v.semantic_relevance:>6.3f}"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class PurposeConditionedNoveltyCoordinator:
    """End-to-end coordinator for the purpose-conditioned novelty functional.

    Combines the analyzer and witness into a single entry-point that:
    1. Computes leverage, tractability, and semantic relevance for each idea.
    2. Evaluates the functional value F for each idea.
    3. Ranks ideas by F and returns the ranked list.
    4. Accumulates results in the witness.

    Parameters
    ----------
    config:
        Configuration for the functional.  Defaults to
        ``NoveltyFunctionalConfig()`` with standard weights.
    """

    def __init__(self, config: NoveltyFunctionalConfig | None = None) -> None:
        self._config = config or NoveltyFunctionalConfig()
        self._analyzer = PurposeConditionedNoveltyAnalyzer(self._config)
        self._witness = PurposeConditionedNoveltyWitness()

    def run(
        self,
        ideas: list[dict[str, Any]],
        obstructions: list[dict[str, Any]],
        purpose_keywords: list[str],
    ) -> list[NoveltyFunctionalValue]:
        """Run the full functional evaluation pipeline.

        Parameters
        ----------
        ideas:
            Candidate ideas to evaluate.
        obstructions:
            Current obstruction records.
        purpose_keywords:
            Keywords encoding the research purpose.

        Returns
        -------
        list[NoveltyFunctionalValue]
            Ranked list of functional values, rank 1 = best.
        """
        obstruction_classes = [str(o.get("class", "")) for o in obstructions]
        existing_support: list[str] = []
        raw_values: list[NoveltyFunctionalValue] = []
        for idea in ideas:
            lev = self._analyzer.compute_leverage(idea, obstructions)
            trac = self._analyzer.compute_tractability(idea, existing_support)
            rel = self._analyzer.compute_semantic_relevance(
                idea, purpose_keywords, obstruction_classes
            )
            fv = self._analyzer.evaluate_functional(lev, trac, rel, self._config)
            raw_values.append(fv)

        ranked = self._analyzer.rank_ideas(raw_values)
        for v in ranked:
            self._witness.record(v)
        return ranked

    def report(self) -> dict[str, Any]:
        """Return a summary dictionary of the witness state."""
        top = self._witness.top_idea()
        return {
            "total_evaluated": self._witness.count(),
            "functional_stats": self._witness.functional_stats(),
            "leverage_stats": self._witness.leverage_stats(),
            "top_idea": top.to_dict() if top else None,
        }

    @property
    def witness(self) -> PurposeConditionedNoveltyWitness:
        """Access the internal witness for advanced queries."""
        return self._witness


# ---------------------------------------------------------------------------
# Module-level factory helpers
# ---------------------------------------------------------------------------


def make_default_config() -> NoveltyFunctionalConfig:
    """Return the default ``NoveltyFunctionalConfig``."""
    return NoveltyFunctionalConfig()


def make_leverage_biased_config() -> NoveltyFunctionalConfig:
    """Return a config that strongly prioritises leverage over tractability."""
    return NoveltyFunctionalConfig(
        leverage_weight=0.60,
        tractability_weight=0.15,
        semantic_weight=0.25,
        temperature=0.5,
        top_k=5,
    )


def make_tractability_biased_config() -> NoveltyFunctionalConfig:
    """Return a config for exploitation phases where tractability is paramount."""
    return NoveltyFunctionalConfig(
        leverage_weight=0.20,
        tractability_weight=0.55,
        semantic_weight=0.25,
        temperature=0.3,
        top_k=3,
    )


def make_exploration_config() -> NoveltyFunctionalConfig:
    """Return a high-temperature config for exploration phases."""
    return NoveltyFunctionalConfig(
        leverage_weight=0.33,
        tractability_weight=0.34,
        semantic_weight=0.33,
        temperature=3.0,
        top_k=20,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== PurposeConditionedNovelty smoke test ===\n")

    _ideas = [
        {
            "id": "i1",
            "title": "Étale cohomology bridge via derived categories",
            "tokens": {"étale", "cohomology", "derived", "categories", "obstruction"},
            "leverage": 0.8,
            "tractability": 0.6,
        },
        {
            "id": "i2",
            "title": "Motivic homotopy sheaf approach to H2 obstructions",
            "tokens": {"motivic", "homotopy", "sheaf", "obstruction", "H2"},
            "leverage": 0.6,
            "tractability": 0.5,
        },
        {
            "id": "i3",
            "title": "Purely combinatorial descent lemma",
            "tokens": {"combinatorial", "descent", "lemma"},
            "leverage": 0.2,
            "tractability": 0.9,
        },
        {
            "id": "i4",
            "title": "Perfectoid spaces and étale fundamental group",
            "tokens": {"perfectoid", "étale", "fundamental", "group", "obstruction"},
            "leverage": 0.75,
            "tractability": 0.4,
        },
    ]
    _obstructions = [
        {"id": "o1", "class": "H2 cohomology barrier"},
        {"id": "o2", "class": "étale fundamental group obstruction"},
        {"id": "o3", "class": "derived category descent failure"},
    ]
    _purpose_keywords = ["cohomology", "obstruction", "étale", "descent", "sheaf"]

    coordinator = PurposeConditionedNoveltyCoordinator()
    values = coordinator.run(_ideas, _obstructions, _purpose_keywords)

    print("Ranked ideas:")
    for v in values:
        print(" ", v.summary())

    print("\nReport:")
    print(json.dumps(coordinator.report(), indent=2, default=str))

    print("\nSoftmax weights (τ=1.0):")
    raw_fvs = [v.functional_value for v in values]
    weights = _softmax(raw_fvs, 1.0)
    for v, w in zip(values, weights):
        print(f"  {v.idea_id}: F={v.functional_value:.4f} → weight={w:.4f}")

    print("\nTop-2 ideas:")
    top2 = coordinator._analyzer.top_k_ideas(values, k=2)
    for v in top2:
        print(" ", v.summary())

    print("\nWitness summary table:\n", coordinator.witness.summary_table())
    print("\n=== Smoke test passed ===")
