"""Purpose Alignment for Semantic Futures.

Formalises the degree to which a ``SemanticFuture`` serves a
``PurposeFunction`` while keeping a small, compatibility-oriented API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
import math
import re

from jugeo.ideation.semantic_futures.models import PurposeFunction, SemanticFuture

__all__ = [
    "AlignmentCriterion",
    "AlignmentScore",
    "PurposeDecomposer",
    "UtilityAggregator",
    "PurposeAligner",
    "AlignmentCache",
    "AlignmentReport",
    "_token_overlap",
    "_keyword_match",
    "_normalize_score",
    "_purpose_keywords",
]


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _normalize_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))



def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall((text or "").lower()) if token}



def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0



def _keyword_match(text: str, keywords: Iterable[str]) -> float:
    keyword_list = [str(keyword).strip().lower() for keyword in keywords if str(keyword).strip()]
    if not keyword_list:
        return 0.0
    haystack = _tokens(text)
    hits = sum(1 for keyword in keyword_list if keyword in haystack)
    return hits / len(keyword_list)



def _purpose_keywords(purpose: PurposeFunction | str | None) -> tuple[str, ...]:
    if purpose is None:
        return ()
    if isinstance(purpose, str):
        return tuple(sorted(_tokens(purpose)))
    keywords = tuple(str(k).strip().lower() for k in getattr(purpose, "keywords", ()) if str(k).strip())
    if keywords:
        return keywords
    components = tuple(str(c).strip().lower() for c in getattr(purpose, "components", ()) if str(c).strip())
    if components:
        return components
    description = str(getattr(purpose, "description", ""))
    return tuple(sorted(_tokens(description)))


@dataclass(frozen=True)
class AlignmentCriterion:
    name: str
    weight: float = 1.0
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlignmentScore:
    future_id: str
    purpose_id: str = ""
    semantic_similarity: float = 0.0
    keyword_score: float = 0.0
    structural_score: float = 0.0
    total_score: float = 0.0
    rationale: str = ""
    criterion_scores: dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.total_score


class PurposeDecomposer:
    def decompose(self, purpose: PurposeFunction | str | None) -> tuple[AlignmentCriterion, ...]:
        keywords = _purpose_keywords(purpose)
        if not keywords:
            return (AlignmentCriterion(name="generic", weight=1.0, keywords=()),)
        return tuple(AlignmentCriterion(name=keyword, weight=1.0, keywords=(keyword,)) for keyword in keywords)


class UtilityAggregator:
    def aggregate(self, values: dict[str, float] | Iterable[float], weights: dict[str, float] | Iterable[float] | None = None) -> float:
        if isinstance(values, dict):
            value_map = {str(k): _normalize_score(v) for k, v in values.items()}
            if isinstance(weights, dict):
                weight_map = {str(k): max(0.0, float(v)) for k, v in weights.items()}
            else:
                weight_map = {key: 1.0 for key in value_map}
            total_weight = sum(weight_map.get(key, 1.0) for key in value_map)
            if total_weight <= 0.0:
                return 0.0
            return sum(value_map[key] * weight_map.get(key, 1.0) for key in value_map) / total_weight
        value_list = [_normalize_score(v) for v in values]
        if not value_list:
            return 0.0
        if weights is None:
            return sum(value_list) / len(value_list)
        weight_list = [max(0.0, float(w)) for w in weights]
        if not weight_list or math.isclose(sum(weight_list), 0.0):
            return sum(value_list) / len(value_list)
        pairs = list(zip(value_list, weight_list, strict=False))
        return sum(v * w for v, w in pairs) / sum(w for _, w in pairs)


class AlignmentCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], AlignmentScore] = {}

    def get(self, future_id: str, purpose_id: str) -> AlignmentScore | None:
        return self._cache.get((future_id, purpose_id))

    def put(self, score: AlignmentScore) -> AlignmentScore:
        self._cache[(score.future_id, score.purpose_id)] = score
        return score


@dataclass(frozen=True)
class AlignmentReport:
    score: AlignmentScore
    summary: str


class PurposeAligner:
    def __init__(self, decomposer: PurposeDecomposer | None = None, aggregator: UtilityAggregator | None = None, cache: AlignmentCache | None = None) -> None:
        self.decomposer = decomposer or PurposeDecomposer()
        self.aggregator = aggregator or UtilityAggregator()
        self.cache = cache or AlignmentCache()

    def align(self, future: SemanticFuture, purpose: PurposeFunction | str | None) -> AlignmentScore:
        future_id = str(getattr(future, "future_id", ""))
        purpose_id = str(getattr(purpose, "purpose_id", getattr(purpose, "description", purpose or "")))
        cached = self.cache.get(future_id, purpose_id)
        if cached is not None:
            return cached

        future_text = " ".join(
            part for part in (
                str(getattr(future, "title", "")),
                str(getattr(future, "delta", "")),
                str(getattr(future, "description", "")),
            )
            if part
        )
        purpose_text = " ".join(
            part for part in (
                str(getattr(purpose, "description", "")) if purpose is not None else "",
                " ".join(_purpose_keywords(purpose)),
                str(getattr(purpose, "domain", "")) if purpose is not None else "",
            )
            if part
        )
        semantic_similarity = _token_overlap(future_text, purpose_text)
        keyword_score = _keyword_match(future_text, _purpose_keywords(purpose))
        structural_score = _normalize_score(getattr(future, "purpose_alignment", 0.0))
        weights = getattr(purpose, "utility_weights", {}) if purpose is not None else {}
        total_score = self.aggregator.aggregate(
            {
                "semantic_similarity": semantic_similarity,
                "keyword_score": keyword_score,
                "structural_score": structural_score,
            },
            weights={
                "semantic_similarity": float(weights.get("semantic_similarity", 1.0)),
                "keyword_score": float(weights.get("novelty", weights.get("keyword_score", 1.0))),
                "structural_score": float(weights.get("yield", weights.get("structural_score", 1.0))),
            },
        )
        score = AlignmentScore(
            future_id=future_id,
            purpose_id=purpose_id,
            semantic_similarity=semantic_similarity,
            keyword_score=keyword_score,
            structural_score=structural_score,
            total_score=total_score,
            rationale=f"alignment={total_score:.3f} from semantic, keyword, and structural signals",
            criterion_scores={
                "semantic_similarity": semantic_similarity,
                "keyword_score": keyword_score,
                "structural_score": structural_score,
            },
        )
        return self.cache.put(score)

    def score_future(self, future: SemanticFuture, purpose: PurposeFunction | str | None) -> float:
        return self.align(future, purpose).total_score

    def report(self, future: SemanticFuture, purpose: PurposeFunction | str | None) -> AlignmentReport:
        score = self.align(future, purpose)
        return AlignmentReport(score=score, summary=score.rationale)
