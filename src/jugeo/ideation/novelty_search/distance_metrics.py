"""Distance metric implementations for novelty_search – theory2.tex Ch57.

Computes semantic, structural, and purpose-weighted distances between ideas
for use in novelty scoring and diversity optimization.

Module layout::

    DistanceConfig            – configuration for distance computation
    SemanticDistanceComputer  – token-level semantic distance
    StructuralDistanceComputer – structural similarity distance
    PurposeWeightedDistance   – purpose-conditioned distance
    DistanceNormalizer        – normalizes distance matrices to [0,1]
    MetricAggregator          – combines multiple metric outputs
    DistanceCacheManager      – LRU-style cache for computed distances

Background
----------
All distances are values in ``[0.0, 1.0]`` where **0** means *identical* and
**1** means *maximally distant*.  Three orthogonal axes are combined:

1. **Semantic distance** – how different the *words* used to describe two
   ideas are, measured via Jaccard distance on token sets extracted from
   ``title``, ``purpose``, ``hypothesis``, and ``target_area``.

2. **Structural distance** – how different the *predicted gains* and *trust
   status* are, measured via normalised Euclidean distance in
   ``(theorem_yield, bridge_impact, cost, uncertainty)`` space.

3. **Purpose-weighted distance** – a linear reweighting of the semantic
   distance that discounts pairs whose purpose alignment differs strongly,
   emphasising ideas that are competing for the same goal slot.

The three axes are blended by the weights in ``DistanceConfig``.

References
----------
* theory2.tex §57.1 "Semantic Self-Organisation of Idea Portfolios"
* theory2.tex §57.4 "Structural Divergence and Gain Profiles"
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import Idea, IdeaPortfolio, GainProfile, ValidationPath, TrustStatus
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltyMetric as _NoveltyMetricBase,
    SemanticDistanceModel,
    PurposeAlignmentChecker,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_SEMANTIC_WEIGHT: float = 0.5
_DEFAULT_STRUCTURAL_WEIGHT: float = 0.3
_DEFAULT_PURPOSE_WEIGHT: float = 0.2
_MIN_DISTANCE: float = 0.0
_MAX_DISTANCE: float = 1.0
_CACHE_MAX_SIZE: int = 1024
_EPSILON: float = 1e-9

# Regex-based stop words – common mathematical connectives that carry little
# discriminative power when tokenising idea descriptions.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "of", "in", "to", "for", "and", "or", "is", "are",
        "be", "by", "on", "as", "at", "it", "its", "that", "this", "with",
        "from", "we", "our", "their", "if", "then", "so", "such", "not",
        "can", "may", "all", "any", "via", "per", "also",
    }
)

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to the closed interval ``[lo, hi]``.

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
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    The returned string always has timezone info (``+00:00``) so that it can
    be round-tripped through ``datetime.fromisoformat`` without ambiguity.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Split *text* into a frozen set of lower-cased alpha tokens.

    Non-alphabetic characters are treated as delimiters.  Stop words defined
    in ``_STOP_WORDS`` are removed so that discriminative stems remain.

    Parameters
    ----------
    text:
        Arbitrary natural-language or technical text.

    Returns
    -------
    frozenset[str]
        Immutable set of cleaned tokens suitable for Jaccard comparison.

    Examples
    --------
    >>> _tokenize("A novel approach to algebraic geometry")
    frozenset({'novel', 'approach', 'algebraic', 'geometry'})
    """
    raw_tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return frozenset(t for t in raw_tokens if t not in _STOP_WORDS and len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard *similarity* coefficient between two token sets.

    Parameters
    ----------
    a, b:
        Non-empty frozensets of string tokens.

    Returns
    -------
    float
        ``|a ∩ b| / |a ∪ b|`` in ``[0.0, 1.0]``.  Returns ``1.0`` when both
        sets are empty (two empty descriptions are considered identical).
    """
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _cosine_sim(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute cosine similarity between two same-length numeric vectors.

    Parameters
    ----------
    a, b:
        Numeric tuples of equal length.

    Returns
    -------
    float
        Cosine similarity in ``[-1.0, 1.0]``.  Returns ``1.0`` if either
        vector is the zero vector (degenerate case).
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    if denom < _EPSILON:
        return 1.0
    return dot / denom


def _normalize_vector(v: tuple[float, ...]) -> tuple[float, ...]:
    """Return a unit-length version of *v*, or *v* unchanged if near-zero.

    Parameters
    ----------
    v:
        Tuple of floats representing a vector.

    Returns
    -------
    tuple[float, ...]
        Vector scaled so that its L2 norm equals ``1.0``.
    """
    norm = math.sqrt(sum(x * x for x in v))
    if norm < _EPSILON:
        return v
    return tuple(x / norm for x in v)


def _euclidean(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute the Euclidean (L2) distance between two same-length vectors.

    Parameters
    ----------
    a, b:
        Numeric tuples of equal length.

    Returns
    -------
    float
        Non-negative Euclidean distance.
    """
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _manhattan(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute the Manhattan (L1) distance between two same-length vectors.

    Parameters
    ----------
    a, b:
        Numeric tuples of equal length.

    Returns
    -------
    float
        Non-negative sum of absolute coordinate differences.
    """
    return sum(abs(x - y) for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# DistanceConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistanceConfig:
    """Immutable configuration bundle for distance computations.

    Weights control how the three distance components are blended.  They
    should sum to ``1.0`` but a small tolerance is accepted; use
    ``normalize_weights()`` to obtain a config whose weights sum exactly.

    Attributes
    ----------
    semantic_weight:
        Fractional contribution of semantic (token-Jaccard) distance.
    structural_weight:
        Fractional contribution of structural (GainProfile vector) distance.
    purpose_weight:
        Fractional contribution of purpose-alignment-adjusted distance.
    normalize:
        Whether to apply min-max normalisation to final distance values.
    use_cache:
        Whether distance computers should use the LRU cache.
    cache_size:
        Maximum number of entries to retain in the LRU cache.
    min_distance_clamp:
        Floor applied after normalisation.
    max_distance_clamp:
        Ceiling applied after normalisation.
    """

    semantic_weight: float = _DEFAULT_SEMANTIC_WEIGHT
    structural_weight: float = _DEFAULT_STRUCTURAL_WEIGHT
    purpose_weight: float = _DEFAULT_PURPOSE_WEIGHT
    normalize: bool = True
    use_cache: bool = True
    cache_size: int = _CACHE_MAX_SIZE
    min_distance_clamp: float = _MIN_DISTANCE
    max_distance_clamp: float = _MAX_DISTANCE

    def __post_init__(self) -> None:
        total = self.semantic_weight + self.structural_weight + self.purpose_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"DistanceConfig weights must sum to ~1.0 (got {total:.4f}). "
                "Use normalize_weights() to auto-correct."
            )
        if self.min_distance_clamp > self.max_distance_clamp:
            raise ValueError("min_distance_clamp must be <= max_distance_clamp")
        if self.cache_size < 1:
            raise ValueError("cache_size must be >= 1")

    @property
    def total_weight(self) -> float:
        """Sum of all three component weights."""
        return self.semantic_weight + self.structural_weight + self.purpose_weight

    @property
    def is_balanced(self) -> bool:
        """True when every weight exceeds 0.1, ensuring all axes contribute."""
        return (
            self.semantic_weight > 0.1
            and self.structural_weight > 0.1
            and self.purpose_weight > 0.1
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "semantic_weight": self.semantic_weight,
            "structural_weight": self.structural_weight,
            "purpose_weight": self.purpose_weight,
            "normalize": self.normalize,
            "use_cache": self.use_cache,
            "cache_size": self.cache_size,
            "min_distance_clamp": self.min_distance_clamp,
            "max_distance_clamp": self.max_distance_clamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DistanceConfig":
        """Deserialise from a dictionary produced by ``to_dict``."""
        return cls(
            semantic_weight=float(d.get("semantic_weight", _DEFAULT_SEMANTIC_WEIGHT)),
            structural_weight=float(d.get("structural_weight", _DEFAULT_STRUCTURAL_WEIGHT)),
            purpose_weight=float(d.get("purpose_weight", _DEFAULT_PURPOSE_WEIGHT)),
            normalize=bool(d.get("normalize", True)),
            use_cache=bool(d.get("use_cache", True)),
            cache_size=int(d.get("cache_size", _CACHE_MAX_SIZE)),
            min_distance_clamp=float(d.get("min_distance_clamp", _MIN_DISTANCE)),
            max_distance_clamp=float(d.get("max_distance_clamp", _MAX_DISTANCE)),
        )

    def normalize_weights(self) -> "DistanceConfig":
        """Return a new ``DistanceConfig`` with weights re-normalised to sum exactly to 1.0.

        Proportional normalisation is used: each weight is divided by the
        current total so that their ratios are preserved.
        """
        total = self.total_weight
        if total < _EPSILON:
            # Degenerate: fall back to uniform distribution.
            third = 1.0 / 3.0
            return DistanceConfig(
                semantic_weight=third,
                structural_weight=third,
                purpose_weight=third,
                normalize=self.normalize,
                use_cache=self.use_cache,
                cache_size=self.cache_size,
                min_distance_clamp=self.min_distance_clamp,
                max_distance_clamp=self.max_distance_clamp,
            )
        return DistanceConfig(
            semantic_weight=self.semantic_weight / total,
            structural_weight=self.structural_weight / total,
            purpose_weight=self.purpose_weight / total,
            normalize=self.normalize,
            use_cache=self.use_cache,
            cache_size=self.cache_size,
            min_distance_clamp=self.min_distance_clamp,
            max_distance_clamp=self.max_distance_clamp,
        )


# ---------------------------------------------------------------------------
# SemanticDistanceComputer
# ---------------------------------------------------------------------------


class SemanticDistanceComputer:
    """Token-level semantic distance between ideas.

    Semantic distance is computed as ``1 - Jaccard(tokens_a, tokens_b)``
    where token sets are built from the concatenated text of
    ``title``, ``purpose``, ``hypothesis``, and ``target_area``.

    The computer maintains an internal per-idea token cache so that
    repeated calls with the same idea do not re-tokenise.

    Parameters
    ----------
    config:
        Distance configuration.  Defaults to ``DistanceConfig()``.
    """

    def __init__(self, config: DistanceConfig | None = None) -> None:
        self._config: DistanceConfig = config or DistanceConfig()
        # idea_id -> frozenset[str]
        self._token_cache: dict[str, frozenset[str]] = {}

    def _tokens(self, idea: Idea) -> frozenset[str]:
        """Extract and cache the token set for *idea*.

        Combines text from all four descriptive fields.  The result is
        memoised by ``idea_id`` to avoid redundant tokenisation.
        """
        if idea.idea_id in self._token_cache:
            return self._token_cache[idea.idea_id]
        combined = " ".join(
            [
                idea.title or "",
                idea.purpose or "",
                idea.hypothesis or "",
                idea.target_area or "",
            ]
        )
        tokens = _tokenize(combined)
        self._token_cache[idea.idea_id] = tokens
        return tokens

    def compute(self, idea_a: Idea, idea_b: Idea) -> float:
        """Compute the semantic distance between two ideas.

        Parameters
        ----------
        idea_a, idea_b:
            The pair of ideas to compare.

        Returns
        -------
        float
            Distance in ``[0.0, 1.0]``.  ``0.0`` = identical token sets,
            ``1.0`` = completely disjoint.
        """
        if idea_a.idea_id == idea_b.idea_id:
            return 0.0
        tok_a = self._tokens(idea_a)
        tok_b = self._tokens(idea_b)
        sim = _jaccard(tok_a, tok_b)
        raw = 1.0 - sim
        return _clamp(raw, self._config.min_distance_clamp, self._config.max_distance_clamp)

    def compute_bulk(self, ideas: Sequence[Idea]) -> dict[tuple[str, str], float]:
        """Compute all-pairs semantic distances for a sequence of ideas.

        Parameters
        ----------
        ideas:
            Sequence of ideas.  Order is not significant.

        Returns
        -------
        dict[tuple[str, str], float]
            Mapping ``(id_a, id_b) -> distance`` for every ordered pair
            where ``id_a < id_b`` (lexicographic ordering, to avoid
            duplicates).
        """
        result: dict[tuple[str, str], float] = {}
        idea_list = list(ideas)
        for i in range(len(idea_list)):
            for j in range(i + 1, len(idea_list)):
                a = idea_list[i]
                b = idea_list[j]
                key = (min(a.idea_id, b.idea_id), max(a.idea_id, b.idea_id))
                result[key] = self.compute(a, b)
        return result

    def compute_to_portfolio(
        self, query: Idea, portfolio: Sequence[Idea]
    ) -> list[tuple[str, float]]:
        """Compute distances from *query* to every idea in *portfolio*.

        Parameters
        ----------
        query:
            The reference idea.
        portfolio:
            Sequence of ideas to compare against.

        Returns
        -------
        list[tuple[str, float]]
            ``(idea_id, distance)`` pairs sorted by ascending distance
            (nearest first).
        """
        results: list[tuple[str, float]] = []
        for p_idea in portfolio:
            if p_idea.idea_id == query.idea_id:
                continue
            d = self.compute(query, p_idea)
            results.append((p_idea.idea_id, d))
        results.sort(key=lambda t: t[1])
        return results

    def min_distance_to_portfolio(
        self, query: Idea, portfolio: Sequence[Idea]
    ) -> float:
        """Return the minimum semantic distance from *query* to any portfolio idea.

        Parameters
        ----------
        query:
            The reference idea.
        portfolio:
            Sequence of ideas forming the comparison set.

        Returns
        -------
        float
            Minimum distance, or ``1.0`` if *portfolio* is empty or contains
            only *query* itself.
        """
        min_d = 1.0
        for p_idea in portfolio:
            if p_idea.idea_id == query.idea_id:
                continue
            d = self.compute(query, p_idea)
            if d < min_d:
                min_d = d
        return min_d

    def _pairwise_matrix(self, ideas: Sequence[Idea]) -> list[list[float]]:
        """Build an NxN symmetric distance matrix for *ideas*.

        The diagonal is always ``0.0`` (self-distance).  Off-diagonal entries
        are filled with ``compute(ideas[i], ideas[j])``.

        Parameters
        ----------
        ideas:
            Ordered sequence; index corresponds to matrix row/column.

        Returns
        -------
        list[list[float]]
            NxN matrix where ``matrix[i][j]`` is the distance between
            ``ideas[i]`` and ``ideas[j]``.
        """
        n = len(ideas)
        matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
        idea_list = list(ideas)
        for i in range(n):
            for j in range(i + 1, n):
                d = self.compute(idea_list[i], idea_list[j])
                matrix[i][j] = d
                matrix[j][i] = d
        return matrix

    def diversity_score(self, ideas: Sequence[Idea]) -> float:
        """Compute the mean pairwise semantic distance of a set of ideas.

        A higher score indicates greater diversity.  Returns ``0.0`` for
        sets of size less than 2.

        Parameters
        ----------
        ideas:
            Sequence of ideas to evaluate.

        Returns
        -------
        float
            Mean pairwise distance in ``[0.0, 1.0]``.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n < 2:
            return 0.0
        total = 0.0
        count = 0
        for i in range(n):
            for j in range(i + 1, n):
                total += self.compute(idea_list[i], idea_list[j])
                count += 1
        return total / count if count > 0 else 0.0

    def nearest_neighbor(
        self, query: Idea, portfolio: Sequence[Idea]
    ) -> tuple[Idea, float] | None:
        """Find the closest idea in *portfolio* to *query*.

        Parameters
        ----------
        query:
            The query idea.
        portfolio:
            Ideas to search through.

        Returns
        -------
        tuple[Idea, float] | None
            ``(nearest_idea, distance)`` or ``None`` if *portfolio* is empty
            or contains only *query*.
        """
        best_idea: Idea | None = None
        best_d = float("inf")
        for p_idea in portfolio:
            if p_idea.idea_id == query.idea_id:
                continue
            d = self.compute(query, p_idea)
            if d < best_d:
                best_d = d
                best_idea = p_idea
        if best_idea is None:
            return None
        return (best_idea, best_d)

    def explain(self, idea_a: Idea, idea_b: Idea) -> str:
        """Return a human-readable explanation of the computed semantic distance.

        The explanation includes the token sets of each idea and the
        intersection / union statistics used for the Jaccard calculation.

        Parameters
        ----------
        idea_a, idea_b:
            The pair to explain.

        Returns
        -------
        str
            Multi-line explanation string.
        """
        tok_a = self._tokens(idea_a)
        tok_b = self._tokens(idea_b)
        shared = tok_a & tok_b
        union = tok_a | tok_b
        sim = len(shared) / len(union) if union else 1.0
        dist = 1.0 - sim
        lines = [
            f"Semantic distance: {idea_a.idea_id!r} ↔ {idea_b.idea_id!r}",
            f"  Tokens A ({len(tok_a)}): {sorted(tok_a)[:10]}{'...' if len(tok_a) > 10 else ''}",
            f"  Tokens B ({len(tok_b)}): {sorted(tok_b)[:10]}{'...' if len(tok_b) > 10 else ''}",
            f"  Shared ({len(shared)}): {sorted(shared)[:10]}{'...' if len(shared) > 10 else ''}",
            f"  |Union|={len(union)}, Jaccard={sim:.4f}, Distance={dist:.4f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# StructuralDistanceComputer
# ---------------------------------------------------------------------------


class StructuralDistanceComputer:
    """Structural distance between ideas based on GainProfile and TrustStatus.

    Two ideas are structurally close when they have similar predicted gains
    and similar epistemic standing.  The distance is a weighted combination
    of normalised Euclidean distance in ``GainProfile`` space and ordinal
    distance between ``TrustStatus`` values.

    Parameters
    ----------
    config:
        Distance configuration.  Defaults to ``DistanceConfig()``.
    """

    # Approximate upper bounds used to normalise each gain dimension.
    _GAIN_UPPER: tuple[float, float, float, float] = (10.0, 10.0, 100.0, 1.0)

    def __init__(self, config: DistanceConfig | None = None) -> None:
        self._config: DistanceConfig = config or DistanceConfig()
        # Pre-build ordinal map from TrustLevel enum order.
        self._trust_ordinals: dict[str, int] = {
            level.name: idx for idx, level in enumerate(TrustLevel)
        }

    def _gain_vector(self, g: GainProfile) -> tuple[float, float, float, float]:
        """Normalise a ``GainProfile`` to a unit-hypercube vector.

        Each coordinate is independently min-max normalised using approximate
        practical upper bounds (``_GAIN_UPPER``).  Values are clamped to
        ``[0.0, 1.0]`` to handle out-of-range inputs gracefully.

        Parameters
        ----------
        g:
            The gain profile to vectorise.

        Returns
        -------
        tuple[float, float, float, float]
            Four-dimensional vector with each coordinate in ``[0.0, 1.0]``.
        """
        raw = (
            float(g.theorem_yield),
            float(g.bridge_impact),
            float(g.cost),
            float(g.uncertainty),
        )
        upper = self._GAIN_UPPER
        normed = tuple(_clamp(v / max(u, _EPSILON)) for v, u in zip(raw, upper))
        return normed  # type: ignore[return-value]

    def _trust_ordinal(self, ts: TrustStatus) -> int:
        """Map a ``TrustStatus`` value to an integer ordinal.

        Uses the position of the underlying ``TrustLevel`` enum member.
        Falls back to ``0`` for unrecognised values.

        Parameters
        ----------
        ts:
            The trust status to map.

        Returns
        -------
        int
            Non-negative ordinal.
        """
        # TrustStatus may wrap TrustLevel or carry it directly.
        if hasattr(ts, "level"):
            level_name = ts.level.name if hasattr(ts.level, "name") else str(ts.level)
        elif hasattr(ts, "name"):
            level_name = ts.name
        else:
            level_name = str(ts)
        return self._trust_ordinals.get(level_name, 0)

    def compute_gain_distance(self, g1: GainProfile, g2: GainProfile) -> float:
        """Compute normalised Euclidean distance between two gain profiles.

        The maximum possible Euclidean distance in the 4-dimensional
        unit-hypercube is ``sqrt(4) = 2``, so the raw distance is divided
        by ``2`` to bring it into ``[0.0, 1.0]``.

        Parameters
        ----------
        g1, g2:
            The gain profiles to compare.

        Returns
        -------
        float
            Normalised distance in ``[0.0, 1.0]``.
        """
        v1 = self._gain_vector(g1)
        v2 = self._gain_vector(g2)
        raw = _euclidean(v1, v2)
        # Normalise by max possible distance in 4-D unit hypercube.
        return _clamp(raw / 2.0)

    def compute_trust_distance(self, t1: TrustStatus, t2: TrustStatus) -> float:
        """Compute the ordinal distance between two trust statuses.

        The ordinal distance is the absolute difference of positions in the
        ``TrustLevel`` enum, normalised by the maximum possible difference.

        Parameters
        ----------
        t1, t2:
            Trust statuses to compare.

        Returns
        -------
        float
            Normalised ordinal distance in ``[0.0, 1.0]``.
        """
        o1 = self._trust_ordinal(t1)
        o2 = self._trust_ordinal(t2)
        max_ord = max(len(TrustLevel) - 1, 1)
        return _clamp(abs(o1 - o2) / max_ord)

    def compute(self, idea_a: Idea, idea_b: Idea) -> float:
        """Compute the structural distance between two ideas.

        Combines gain-profile distance (weight 0.7) with trust-status
        distance (weight 0.3).

        Parameters
        ----------
        idea_a, idea_b:
            Ideas to compare structurally.

        Returns
        -------
        float
            Structural distance in ``[0.0, 1.0]``.
        """
        if idea_a.idea_id == idea_b.idea_id:
            return 0.0
        gain_d = self.compute_gain_distance(
            idea_a.predicted_gain, idea_b.predicted_gain
        )
        trust_d = self.compute_trust_distance(
            idea_a.trust_status, idea_b.trust_status
        )
        combined = 0.7 * gain_d + 0.3 * trust_d
        return _clamp(combined, self._config.min_distance_clamp, self._config.max_distance_clamp)

    def compute_bulk(self, ideas: Sequence[Idea]) -> dict[tuple[str, str], float]:
        """All-pairs structural distances (canonical ordered pairs only).

        Parameters
        ----------
        ideas:
            Sequence of ideas.

        Returns
        -------
        dict[tuple[str, str], float]
            ``(id_a, id_b) -> distance`` for every pair where ``id_a < id_b``.
        """
        result: dict[tuple[str, str], float] = {}
        idea_list = list(ideas)
        for i in range(len(idea_list)):
            for j in range(i + 1, len(idea_list)):
                a = idea_list[i]
                b = idea_list[j]
                key = (min(a.idea_id, b.idea_id), max(a.idea_id, b.idea_id))
                result[key] = self.compute(a, b)
        return result

    def diversity_score(self, ideas: Sequence[Idea]) -> float:
        """Mean pairwise structural distance of a set of ideas.

        Parameters
        ----------
        ideas:
            Sequence of ideas.

        Returns
        -------
        float
            Mean distance in ``[0.0, 1.0]``.  ``0.0`` for fewer than 2 ideas.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n < 2:
            return 0.0
        total = 0.0
        count = 0
        for i in range(n):
            for j in range(i + 1, n):
                total += self.compute(idea_list[i], idea_list[j])
                count += 1
        return total / count if count > 0 else 0.0

    def explain(self, idea_a: Idea, idea_b: Idea) -> str:
        """Return a human-readable explanation of the structural distance.

        Parameters
        ----------
        idea_a, idea_b:
            The pair to explain.

        Returns
        -------
        str
            Multi-line explanation string.
        """
        gd = self.compute_gain_distance(idea_a.predicted_gain, idea_b.predicted_gain)
        td = self.compute_trust_distance(idea_a.trust_status, idea_b.trust_status)
        combined = 0.7 * gd + 0.3 * td
        v1 = self._gain_vector(idea_a.predicted_gain)
        v2 = self._gain_vector(idea_b.predicted_gain)
        lines = [
            f"Structural distance: {idea_a.idea_id!r} ↔ {idea_b.idea_id!r}",
            f"  GainProfile A (normalised): {tuple(f'{x:.3f}' for x in v1)}",
            f"  GainProfile B (normalised): {tuple(f'{x:.3f}' for x in v2)}",
            f"  Gain distance: {gd:.4f}",
            f"  Trust ordinals: {self._trust_ordinal(idea_a.trust_status)} vs "
            f"{self._trust_ordinal(idea_b.trust_status)}",
            f"  Trust distance: {td:.4f}",
            f"  Combined (0.7*gain + 0.3*trust): {combined:.4f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PurposeWeightedDistance
# ---------------------------------------------------------------------------


class PurposeWeightedDistance:
    """Purpose-conditioned distance that reweights semantic distance.

    Two ideas that both strongly align with *purpose* are penalised less by
    semantic dissimilarity (they are exploring the same goal from different
    angles).  Two ideas that diverge in their purpose alignment contribute
    more to apparent distance, since one is closer to the intended direction.

    Parameters
    ----------
    purpose:
        Free-text description of the research or proof-search goal.
    config:
        Distance configuration.  Defaults to ``DistanceConfig()``.
    """

    def __init__(self, purpose: str, config: DistanceConfig | None = None) -> None:
        self._purpose: str = purpose
        self._purpose_tokens: frozenset[str] = _tokenize(purpose)
        self._config: DistanceConfig = config or DistanceConfig()
        self._semantic: SemanticDistanceComputer = SemanticDistanceComputer(config)
        # Cache purpose alignment scores per idea_id.
        self._alignment_cache: dict[str, float] = {}

    def purpose_alignment(self, idea: Idea) -> float:
        """Compute how well *idea* aligns with the stored purpose.

        Uses Jaccard similarity between the idea's token set and the
        purpose's token set.

        Parameters
        ----------
        idea:
            The idea to evaluate.

        Returns
        -------
        float
            Alignment score in ``[0.0, 1.0]``.  ``1.0`` = perfect overlap.
        """
        if idea.idea_id in self._alignment_cache:
            return self._alignment_cache[idea.idea_id]
        idea_tokens = self._semantic._tokens(idea)
        score = _jaccard(idea_tokens, self._purpose_tokens)
        self._alignment_cache[idea.idea_id] = score
        return score

    def reweight(
        self, base_distance: float, alignment_a: float, alignment_b: float
    ) -> float:
        """Reweight *base_distance* by the purpose alignments of both ideas.

        The reweighting scheme scales distance by ``1 - min(align_a, align_b)``
        so that when both ideas align strongly with purpose (both > 0.5),
        the effective distance shrinks, reflecting that they are productive
        alternatives rather than competitors.

        Parameters
        ----------
        base_distance:
            Raw semantic distance before reweighting.
        alignment_a, alignment_b:
            Purpose alignments of the two ideas.

        Returns
        -------
        float
            Reweighted distance in ``[0.0, 1.0]``.
        """
        min_align = min(alignment_a, alignment_b)
        scale = 1.0 - 0.5 * min_align  # range [0.5, 1.0]
        return _clamp(base_distance * scale)

    def compute(self, idea_a: Idea, idea_b: Idea) -> float:
        """Compute purpose-weighted distance between two ideas.

        Parameters
        ----------
        idea_a, idea_b:
            Ideas to compare.

        Returns
        -------
        float
            Purpose-weighted distance in ``[0.0, 1.0]``.
        """
        if idea_a.idea_id == idea_b.idea_id:
            return 0.0
        base = self._semantic.compute(idea_a, idea_b)
        al_a = self.purpose_alignment(idea_a)
        al_b = self.purpose_alignment(idea_b)
        return self.reweight(base, al_a, al_b)

    def compute_purpose_conditioned(
        self, idea: Idea, portfolio: Sequence[Idea]
    ) -> float:
        """Min purpose-weighted distance from *idea* to any portfolio idea.

        Parameters
        ----------
        idea:
            Query idea.
        portfolio:
            Reference ideas.

        Returns
        -------
        float
            Minimum distance, or ``1.0`` if portfolio is empty.
        """
        min_d = 1.0
        for p_idea in portfolio:
            if p_idea.idea_id == idea.idea_id:
                continue
            d = self.compute(idea, p_idea)
            if d < min_d:
                min_d = d
        return min_d

    def bulk_purpose_alignment(self, ideas: Sequence[Idea]) -> dict[str, float]:
        """Return alignment scores for all ideas keyed by ``idea_id``.

        Parameters
        ----------
        ideas:
            Ideas to score.

        Returns
        -------
        dict[str, float]
            ``idea_id -> alignment_score`` mapping.
        """
        return {idea.idea_id: self.purpose_alignment(idea) for idea in ideas}

    def top_purpose_aligned(
        self, ideas: Sequence[Idea], k: int = 5
    ) -> list[tuple[Idea, float]]:
        """Return the top-*k* ideas most aligned with the stored purpose.

        Parameters
        ----------
        ideas:
            Candidate ideas.
        k:
            Number of ideas to return.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, alignment)`` pairs sorted by descending alignment.
        """
        scored = [(idea, self.purpose_alignment(idea)) for idea in ideas]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

    def explain(self, idea_a: Idea, idea_b: Idea) -> str:
        """Human-readable explanation of the purpose-weighted distance.

        Parameters
        ----------
        idea_a, idea_b:
            The pair to explain.

        Returns
        -------
        str
            Multi-line explanation string.
        """
        base = self._semantic.compute(idea_a, idea_b)
        al_a = self.purpose_alignment(idea_a)
        al_b = self.purpose_alignment(idea_b)
        final = self.reweight(base, al_a, al_b)
        lines = [
            f"Purpose-weighted distance: {idea_a.idea_id!r} ↔ {idea_b.idea_id!r}",
            f"  Purpose: {self._purpose[:80]!r}{'...' if len(self._purpose) > 80 else ''}",
            f"  Base semantic distance: {base:.4f}",
            f"  Alignment A ({idea_a.idea_id!r}): {al_a:.4f}",
            f"  Alignment B ({idea_b.idea_id!r}): {al_b:.4f}",
            f"  Reweight scale: {1.0 - 0.5 * min(al_a, al_b):.4f}",
            f"  Final distance: {final:.4f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# DistanceNormalizer
# ---------------------------------------------------------------------------


class DistanceNormalizer:
    """Collection of normalisation routines for distance values and matrices.

    All methods are pure functions (no internal state) operating on plain
    Python lists and dictionaries.
    """

    def __init__(self) -> None:
        pass

    def normalize_distance(self, d: float, min_d: float, max_d: float) -> float:
        """Min-max normalise *d* to ``[0.0, 1.0]``.

        Parameters
        ----------
        d:
            Raw distance value.
        min_d:
            Minimum observed distance (maps to ``0.0``).
        max_d:
            Maximum observed distance (maps to ``1.0``).

        Returns
        -------
        float
            Normalised distance.  Returns ``0.0`` if ``min_d == max_d``.
        """
        span = max_d - min_d
        if abs(span) < _EPSILON:
            return 0.0
        return _clamp((d - min_d) / span)

    def normalize_matrix(self, matrix: list[list[float]]) -> list[list[float]]:
        """Normalise all off-diagonal entries of an NxN distance matrix.

        Collects all off-diagonal values, finds their min/max, then applies
        ``normalize_distance`` to each entry while preserving the diagonal
        zeroes.

        Parameters
        ----------
        matrix:
            Square list of lists.  Diagonal is assumed to be ``0.0``.

        Returns
        -------
        list[list[float]]
            New matrix with the same structure but normalised values.
        """
        n = len(matrix)
        # Collect off-diagonal values.
        off_diag: list[float] = [
            matrix[i][j] for i in range(n) for j in range(n) if i != j
        ]
        if not off_diag:
            return [row[:] for row in matrix]
        min_d = min(off_diag)
        max_d = max(off_diag)
        result: list[list[float]] = []
        for i in range(n):
            row: list[float] = []
            for j in range(n):
                if i == j:
                    row.append(0.0)
                else:
                    row.append(self.normalize_distance(matrix[i][j], min_d, max_d))
            result.append(row)
        return result

    def normalize_dict(
        self, distances: dict[tuple[str, str], float]
    ) -> dict[tuple[str, str], float]:
        """Min-max normalise a dictionary of ``(id_a, id_b) -> distance`` values.

        Parameters
        ----------
        distances:
            Dictionary of raw pairwise distances.

        Returns
        -------
        dict[tuple[str, str], float]
            New dictionary with the same keys but normalised values.
        """
        if not distances:
            return {}
        values = list(distances.values())
        min_d = min(values)
        max_d = max(values)
        return {
            k: self.normalize_distance(v, min_d, max_d) for k, v in distances.items()
        }

    def z_score_normalize(self, values: list[float]) -> list[float]:
        """Return z-score normalised version of *values*.

        Parameters
        ----------
        values:
            List of floating-point numbers.

        Returns
        -------
        list[float]
            Z-scores ``(x - mean) / std``.  Returns zeros if std is near 0.
        """
        if not values:
            return []
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance)
        if std < _EPSILON:
            return [0.0] * len(values)
        return [(v - mean) / std for v in values]

    def rank_normalize(self, values: list[float]) -> list[float]:
        """Normalise *values* by rank (fractional rank normalisation).

        The smallest value receives rank ``0.0``, the largest receives
        ``1.0``.  Ties receive the same normalised rank.

        Parameters
        ----------
        values:
            List of floating-point numbers.

        Returns
        -------
        list[float]
            Rank-normalised values in ``[0.0, 1.0]``.
        """
        if not values:
            return []
        n = len(values)
        if n == 1:
            return [0.0]
        indexed = sorted(enumerate(values), key=lambda t: t[1])
        result = [0.0] * n
        for rank, (orig_idx, _) in enumerate(indexed):
            result[orig_idx] = rank / (n - 1)
        return result

    def clip_outliers(
        self, values: list[float], percentile: float = 0.95
    ) -> list[float]:
        """Clip values to the *percentile* of the distribution.

        Values above the *percentile*-th quantile are replaced by the
        quantile value.  Useful for suppressing extreme outlier distances.

        Parameters
        ----------
        values:
            Raw distance values.
        percentile:
            Upper quantile to clip at.  Must be in ``(0.0, 1.0]``.

        Returns
        -------
        list[float]
            Clipped values with the same length as *values*.
        """
        if not values:
            return []
        sorted_vals = sorted(values)
        cutoff_idx = int(len(sorted_vals) * percentile)
        cutoff_idx = min(cutoff_idx, len(sorted_vals) - 1)
        cutoff = sorted_vals[cutoff_idx]
        return [min(v, cutoff) for v in values]

    def statistics(self, values: list[float]) -> dict[str, float]:
        """Compute descriptive statistics of a list of distance values.

        Parameters
        ----------
        values:
            Distance values.

        Returns
        -------
        dict[str, float]
            Dictionary with keys: ``mean``, ``std``, ``min``, ``max``,
            ``median``.  All values are ``0.0`` for an empty list.
        """
        if not values:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0}
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)
        sorted_vals = sorted(values)
        if n % 2 == 1:
            median = sorted_vals[n // 2]
        else:
            median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
        return {
            "mean": mean,
            "std": std,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "median": median,
        }


# ---------------------------------------------------------------------------
# MetricAggregator
# ---------------------------------------------------------------------------


class MetricAggregator:
    """Blended distance metric combining semantic, structural, and purpose axes.

    ``MetricAggregator`` is the primary entry-point for consumers that want
    a single scalar distance between two ideas without dealing with the
    individual components directly.

    Parameters
    ----------
    config:
        Distance configuration controlling weights and normalisation.
        Defaults to ``DistanceConfig()``.
    """

    def __init__(self, config: DistanceConfig | None = None) -> None:
        self._config: DistanceConfig = config or DistanceConfig()
        self._semantic: SemanticDistanceComputer = SemanticDistanceComputer(self._config)
        self._structural: StructuralDistanceComputer = StructuralDistanceComputer(
            self._config
        )
        self._normalizer: DistanceNormalizer = DistanceNormalizer()

    def aggregate(self, idea_a: Idea, idea_b: Idea, purpose: str = "") -> float:
        """Compute the weighted aggregate distance between *idea_a* and *idea_b*.

        The three component distances are blended according to
        ``DistanceConfig.semantic_weight``, ``structural_weight``, and
        ``purpose_weight``.  When *purpose* is empty the purpose weight is
        redistributed equally to the other two components.

        Parameters
        ----------
        idea_a, idea_b:
            Ideas to compare.
        purpose:
            Optional research purpose for purpose-weighted distance.

        Returns
        -------
        float
            Aggregate distance in ``[0.0, 1.0]``.
        """
        if idea_a.idea_id == idea_b.idea_id:
            return 0.0

        sem_d = self._semantic.compute(idea_a, idea_b)
        str_d = self._structural.compute(idea_a, idea_b)

        if purpose.strip():
            pwd = PurposeWeightedDistance(purpose, self._config)
            pur_d = pwd.compute(idea_a, idea_b)
            result = (
                self._config.semantic_weight * sem_d
                + self._config.structural_weight * str_d
                + self._config.purpose_weight * pur_d
            )
        else:
            # Redistribute purpose weight equally to semantic and structural.
            half_extra = self._config.purpose_weight / 2.0
            result = (
                (self._config.semantic_weight + half_extra) * sem_d
                + (self._config.structural_weight + half_extra) * str_d
            )
        return _clamp(result, self._config.min_distance_clamp, self._config.max_distance_clamp)

    def aggregate_bulk(
        self, ideas: Sequence[Idea], purpose: str = ""
    ) -> dict[tuple[str, str], float]:
        """All-pairs aggregate distances (canonical ordered pairs only).

        Parameters
        ----------
        ideas:
            Sequence of ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        dict[tuple[str, str], float]
            Mapping ``(id_a, id_b) -> distance`` for every pair where
            ``id_a < id_b``.
        """
        result: dict[tuple[str, str], float] = {}
        idea_list = list(ideas)
        for i in range(len(idea_list)):
            for j in range(i + 1, len(idea_list)):
                a = idea_list[i]
                b = idea_list[j]
                key = (min(a.idea_id, b.idea_id), max(a.idea_id, b.idea_id))
                result[key] = self.aggregate(a, b, purpose)
        return result

    def novelty_of(
        self, query: Idea, portfolio: Sequence[Idea], purpose: str = ""
    ) -> float:
        """Estimate the novelty of *query* with respect to *portfolio*.

        Novelty is defined as ``1 - max_similarity``, where similarity is
        ``1 - distance``.  A perfectly novel idea (maximally far from all
        portfolio ideas) scores ``1.0``.  An idea identical to a portfolio
        idea scores ``0.0``.

        Parameters
        ----------
        query:
            The candidate idea.
        portfolio:
            Existing ideas to compare against.
        purpose:
            Optional research purpose.

        Returns
        -------
        float
            Novelty score in ``[0.0, 1.0]``.
        """
        if not portfolio:
            return 1.0
        min_d = min(
            self.aggregate(query, p, purpose)
            for p in portfolio
            if p.idea_id != query.idea_id
        )
        # min distance == 0 → identical → novelty 0; min distance == 1 → maximally novel.
        return _clamp(min_d)

    def rank_by_novelty(
        self, candidates: Sequence[Idea], portfolio: Sequence[Idea], purpose: str = ""
    ) -> list[tuple[Idea, float]]:
        """Rank *candidates* by their novelty with respect to *portfolio*.

        Parameters
        ----------
        candidates:
            Ideas to rank.
        portfolio:
            Existing ideas forming the reference set.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, novelty)`` pairs sorted by descending novelty.
        """
        scored = [
            (idea, self.novelty_of(idea, portfolio, purpose)) for idea in candidates
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def diversity_report(
        self, ideas: Sequence[Idea], purpose: str = ""
    ) -> dict[str, float]:
        """Generate a diversity report for a set of ideas.

        Parameters
        ----------
        ideas:
            Ideas to analyse.
        purpose:
            Optional research purpose.

        Returns
        -------
        dict[str, float]
            Dictionary with keys: ``semantic_diversity``,
            ``structural_diversity``, ``combined_diversity``.
        """
        sem_div = self._semantic.diversity_score(ideas)
        str_div = self._structural.diversity_score(ideas)
        idea_list = list(ideas)
        n = len(idea_list)
        combined_div = 0.0
        if n >= 2:
            total = 0.0
            count = 0
            for i in range(n):
                for j in range(i + 1, n):
                    total += self.aggregate(idea_list[i], idea_list[j], purpose)
                    count += 1
            combined_div = total / count if count > 0 else 0.0
        return {
            "semantic_diversity": sem_div,
            "structural_diversity": str_div,
            "combined_diversity": combined_div,
        }

    def explain(self, idea_a: Idea, idea_b: Idea, purpose: str = "") -> str:
        """Return a multi-component explanation of the aggregate distance.

        Parameters
        ----------
        idea_a, idea_b:
            Ideas to explain.
        purpose:
            Optional research purpose.

        Returns
        -------
        str
            Multi-line explanation string.
        """
        sem_d = self._semantic.compute(idea_a, idea_b)
        str_d = self._structural.compute(idea_a, idea_b)
        agg = self.aggregate(idea_a, idea_b, purpose)
        sem_exp = self._semantic.explain(idea_a, idea_b)
        str_exp = self._structural.explain(idea_a, idea_b)
        lines = [
            "=== MetricAggregator Explanation ===",
            sem_exp,
            "",
            str_exp,
        ]
        if purpose.strip():
            pwd = PurposeWeightedDistance(purpose, self._config)
            pur_d = pwd.compute(idea_a, idea_b)
            pur_exp = pwd.explain(idea_a, idea_b)
            lines += ["", pur_exp, "", f"Aggregate (w={self._config.semantic_weight}·sem + {self._config.structural_weight}·str + {self._config.purpose_weight}·pur): {agg:.4f}"]
        else:
            lines += ["", f"Aggregate (purpose omitted, sem={sem_d:.4f}, str={str_d:.4f}): {agg:.4f}"]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# DistanceCacheManager
# ---------------------------------------------------------------------------


class DistanceCacheManager:
    """LRU cache for precomputed pairwise distances.

    Stores distances as ``canonical_key -> float`` where the canonical key
    is the lexicographically ordered concatenation of the two idea IDs.
    When the cache reaches *max_size* entries, the least-recently-used entry
    is evicted.

    Parameters
    ----------
    max_size:
        Maximum number of entries to retain.  Defaults to ``1024``.
    """

    def __init__(self, max_size: int = _CACHE_MAX_SIZE) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size: int = max_size
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def _key(self, id_a: str, id_b: str) -> str:
        """Produce a canonical cache key from two idea IDs.

        The key is ``"{smaller_id}|{larger_id}"`` so that
        ``_key(x, y) == _key(y, x)`` for all ``x``, ``y``.

        Parameters
        ----------
        id_a, id_b:
            Idea IDs (any order).

        Returns
        -------
        str
            Canonical string key.
        """
        lo, hi = (id_a, id_b) if id_a <= id_b else (id_b, id_a)
        return f"{lo}|{hi}"

    def get(self, id_a: str, id_b: str) -> float | None:
        """Look up a cached distance.

        If the entry exists it is moved to the most-recently-used position.

        Parameters
        ----------
        id_a, id_b:
            IDs of the idea pair (order does not matter).

        Returns
        -------
        float | None
            Cached distance or ``None`` on a cache miss.
        """
        k = self._key(id_a, id_b)
        if k in self._cache:
            self._cache.move_to_end(k)
            self._hits += 1
            return self._cache[k]
        self._misses += 1
        return None

    def put(self, id_a: str, id_b: str, distance: float) -> None:
        """Insert or update a cached distance, evicting LRU if necessary.

        Parameters
        ----------
        id_a, id_b:
            IDs of the idea pair (order does not matter).
        distance:
            Precomputed distance value to cache.
        """
        k = self._key(id_a, id_b)
        if k in self._cache:
            self._cache.move_to_end(k)
            self._cache[k] = distance
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
                self._evictions += 1
            self._cache[k] = distance

    def invalidate(self, idea_id: str) -> int:
        """Remove all cache entries involving *idea_id*.

        Parameters
        ----------
        idea_id:
            The ID of the idea whose entries should be purged.

        Returns
        -------
        int
            Number of entries removed.
        """
        to_remove = [k for k in self._cache if idea_id in k.split("|")]
        for k in to_remove:
            del self._cache[k]
        return len(to_remove)

    def clear(self) -> None:
        """Remove all entries from the cache.  Resets hit/miss counters."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def size(self) -> int:
        """Return the current number of entries in the cache."""
        return len(self._cache)

    def hit_rate(self) -> float:
        """Compute the cache hit rate.

        Returns
        -------
        float
            ``hits / (hits + misses)``, or ``0.0`` if no lookups have
            been performed.
        """
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def stats(self) -> dict[str, Any]:
        """Return a summary statistics dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``hits``, ``misses``, ``size``, ``hit_rate``, ``evictions``.
        """
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": self.size(),
            "hit_rate": self.hit_rate(),
            "evictions": self._evictions,
        }

    def warm_up(
        self, ideas: Sequence[Idea], computer: SemanticDistanceComputer
    ) -> int:
        """Pre-compute and cache all pairwise distances for *ideas*.

        Parameters
        ----------
        ideas:
            Ideas to warm up.
        computer:
            ``SemanticDistanceComputer`` used to compute distances.

        Returns
        -------
        int
            Number of entries cached.
        """
        idea_list = list(ideas)
        cached_count = 0
        for i in range(len(idea_list)):
            for j in range(i + 1, len(idea_list)):
                a = idea_list[i]
                b = idea_list[j]
                if self.get(a.idea_id, b.idea_id) is None:
                    d = computer.compute(a, b)
                    self.put(a.idea_id, b.idea_id, d)
                    cached_count += 1
        return cached_count
