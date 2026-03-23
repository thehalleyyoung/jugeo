"""Compatibility-focused data models for semantic futures.

This module supports both the older test-facing API and the newer package-facing
API by accepting multiple field aliases at construction time while exposing a
stable set of convenience methods.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, EnumMeta
from typing import Any, Iterable, Mapping


__all__ = [
    "SemanticFuture",
    "FutureState",
    "PurposeFunction",
    "FutureValuation",
    "IdeationState",
    "FutureFilter",
    "FutureRanker",
    "FutureComparator",
    "FutureTag",
    "_clamp",
    "_cosine_distance",
    "_dot",
    "_norm",
    "_weighted_sum",
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _dot(a: Iterable[float], b: Iterable[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b, strict=False))


def _norm(v: Iterable[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


def _cosine_distance(a: Iterable[float], b: Iterable[float]) -> float:
    a_vals = tuple(float(x) for x in a)
    b_vals = tuple(float(x) for x in b)
    if not a_vals and not b_vals:
        return 0.0
    denom = _norm(a_vals) * _norm(b_vals)
    if denom == 0.0:
        return 1.0
    cosine = _clamp(_dot(a_vals, b_vals) / denom, -1.0, 1.0)
    return 1.0 - cosine


def _weighted_sum(values: Iterable[float], weights: Iterable[float]) -> float:
    return sum(float(v) * float(w) for v, w in zip(values, weights, strict=False))


@dataclass(frozen=True)
class _DynamicFutureTag:
    """Fallback tag record for tests that construct ad-hoc tags."""

    name: str
    color: str = ""

    @property
    def value(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


class _FutureTagMeta(EnumMeta):
    def __call__(cls, value: Any = None, names: Any = None, *args: Any, **kwargs: Any) -> Any:
        if names is None and "name" in kwargs:
            tag_name = str(kwargs.pop("name")).strip().lower()
            color = str(kwargs.pop("color", ""))
            if kwargs:
                unexpected = ", ".join(sorted(kwargs))
                raise TypeError(f"unexpected FutureTag keyword arguments: {unexpected}")
            member = cls._value2member_map_.get(tag_name)
            return member if member is not None else _DynamicFutureTag(name=tag_name, color=color)
        return super().__call__(value, names, *args, **kwargs)


class FutureTag(str, Enum, metaclass=_FutureTagMeta):
    """Tag enum with compatibility support for older ad-hoc constructor calls."""

    exploratory = "exploratory"
    EXPLORATORY = "exploratory"
    exploitative = "exploitative"
    EXPLOITATIVE = "exploitative"
    bridge = "bridge"
    BRIDGE = "bridge"
    consolidation = "consolidation"
    CONSOLIDATION = "consolidation"
    speculative = "speculative"
    SPECULATIVE = "speculative"
    high_risk = "high_risk"
    HIGH_RISK = "high_risk"
    low_cost = "low_cost"
    LOW_COST = "low_cost"
    extension = "extension"
    EXTENSION = "extension"
    novelty = "novelty"
    NOVELTY = "novelty"

    @property
    def color(self) -> str:
        return _FUTURE_TAG_COLORS.get(self.value, "")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def coerce(cls, value: Any) -> FutureTag | _DynamicFutureTag:
        if isinstance(value, (cls, _DynamicFutureTag)):
            return value
        if hasattr(value, "value"):
            key = str(getattr(value, "value")).strip().lower()
            color = str(getattr(value, "color", ""))
        elif hasattr(value, "name"):
            key = str(getattr(value, "name")).strip().lower()
            color = str(getattr(value, "color", ""))
        else:
            key = str(value).strip().lower()
            color = ""
        return cls._value2member_map_.get(key, _DynamicFutureTag(name=key, color=color))


_FUTURE_TAG_COLORS = {
    "exploratory": "",
    "exploitative": "",
    "bridge": "",
    "consolidation": "",
    "speculative": "",
    "high_risk": "",
    "low_cost": "",
    "extension": "",
    "novelty": "",
}


@dataclass(frozen=True, init=False)
class FutureState:
    state_id: str
    coordinates: tuple[float, ...]
    label: str
    description: str
    domain: str
    embedding: tuple[float, ...]
    theorem_portfolio: tuple[str, ...]
    known_kinds: tuple[str, ...]
    semantic_embedding: tuple[float, ...]
    timestamp: datetime | float
    metadata: tuple[tuple[str, str], ...]

    def __init__(
        self,
        *,
        state_id: str,
        coordinates: Iterable[float] | None = None,
        label: str = "",
        description: str = "",
        domain: str = "",
        embedding: Iterable[float] | None = None,
        theorem_portfolio: Iterable[str] | None = None,
        known_kinds: Iterable[str] | None = None,
        semantic_embedding: Iterable[float] | None = None,
        timestamp: datetime | float | None = None,
        metadata: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    ) -> None:
        coords = tuple(float(x) for x in (coordinates or ()))
        embed = tuple(float(x) for x in (embedding or semantic_embedding or coords))
        if isinstance(metadata, Mapping):
            meta = tuple((str(k), str(v)) for k, v in metadata.items())
        else:
            meta = tuple((str(k), str(v)) for k, v in metadata)
        ts: datetime | float = timestamp if timestamp is not None else time.time()
        object.__setattr__(self, "state_id", state_id)
        object.__setattr__(self, "coordinates", coords or embed)
        object.__setattr__(self, "label", label or description or state_id)
        object.__setattr__(self, "description", description or label)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "embedding", embed)
        object.__setattr__(self, "theorem_portfolio", tuple(theorem_portfolio or ()))
        object.__setattr__(self, "known_kinds", tuple(known_kinds or ()))
        object.__setattr__(self, "semantic_embedding", tuple(float(x) for x in (semantic_embedding or embed)))
        object.__setattr__(self, "timestamp", ts)
        object.__setattr__(self, "metadata", meta)

    def get_metadata(self, key: str, default: str = "") -> str:
        return dict(self.metadata).get(key, default)

    def distance_to(self, other: FutureState) -> float:
        a = self.coordinates
        b = other.coordinates
        n = max(len(a), len(b))
        padded_a = a + (0.0,) * (n - len(a))
        padded_b = b + (0.0,) * (n - len(b))
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(padded_a, padded_b, strict=True)))

    def size(self) -> int:
        return len(self.coordinates)

    def summary(self) -> str:
        return f"FutureState({self.state_id}, dims={self.size()}, label={self.label!r})"

    def to_dict(self) -> dict[str, Any]:
        timestamp = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        return {
            "state_id": self.state_id,
            "coordinates": list(self.coordinates),
            "label": self.label,
            "description": self.description,
            "domain": self.domain,
            "embedding": list(self.embedding),
            "theorem_portfolio": list(self.theorem_portfolio),
            "known_kinds": list(self.known_kinds),
            "semantic_embedding": list(self.semantic_embedding),
            "timestamp": timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FutureState:
        raw_ts = data.get("timestamp")
        if isinstance(raw_ts, str):
            try:
                timestamp: datetime | float = datetime.fromisoformat(raw_ts)
            except ValueError:
                timestamp = raw_ts
        else:
            timestamp = raw_ts
        return cls(
            state_id=str(data["state_id"]),
            coordinates=data.get("coordinates") or (),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            domain=str(data.get("domain", "")),
            embedding=data.get("embedding") or (),
            theorem_portfolio=data.get("theorem_portfolio") or (),
            known_kinds=data.get("known_kinds") or (),
            semantic_embedding=data.get("semantic_embedding") or (),
            timestamp=timestamp,
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, init=False)
class PurposeFunction:
    purpose_id: str
    components: tuple[str, ...]
    weights: tuple[float, ...]
    domain: str
    utility_weights: dict[str, float]
    alignment_threshold: float
    description: str
    keywords: tuple[str, ...]
    weight: float

    def __init__(
        self,
        *,
        purpose_id: str = "",
        components: Iterable[str] | None = None,
        weights: Iterable[float] | None = None,
        domain: str = "",
        utility_weights: Mapping[str, float] | None = None,
        alignment_threshold: float = 0.0,
        description: str = "",
        keywords: Iterable[str] | None = None,
        weight: float = 1.0,
    ) -> None:
        comps = tuple(components or tuple((utility_weights or {}).keys()))
        if weights is None:
            if utility_weights:
                weight_values = tuple(float(v) for v in utility_weights.values())
            elif comps:
                weight_values = tuple(1.0 for _ in comps)
            else:
                weight_values = ()
        else:
            weight_values = tuple(float(v) for v in weights)
        if len(comps) != len(weight_values):
            raise ValueError("components and weights must have the same length")
        if weight < 0.0:
            raise ValueError("weight must be non-negative")
        util = dict(utility_weights or zip(comps, weight_values, strict=False))
        object.__setattr__(self, "purpose_id", purpose_id)
        object.__setattr__(self, "components", comps)
        object.__setattr__(self, "weights", weight_values)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "utility_weights", util)
        object.__setattr__(self, "alignment_threshold", float(alignment_threshold))
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "keywords", tuple(keywords or ()))
        object.__setattr__(self, "weight", float(weight))

    def normalize_weights(self) -> PurposeFunction:
        if not self.weights:
            return self
        total = sum(self.weights)
        if total == 0.0:
            normalized = tuple(1.0 / len(self.weights) for _ in self.weights)
        else:
            normalized = tuple(w / total for w in self.weights)
        return PurposeFunction(
            purpose_id=self.purpose_id,
            components=self.components,
            weights=normalized,
            domain=self.domain,
            utility_weights=dict(zip(self.components, normalized, strict=False)),
            alignment_threshold=self.alignment_threshold,
            description=self.description,
            keywords=self.keywords,
            weight=self.weight,
        )

    def evaluate(self, future_or_delta: Any) -> float:
        if isinstance(future_or_delta, SemanticFuture):
            future = future_or_delta
            score_parts = []
            if self.utility_weights:
                if "alignment" in self.utility_weights or "purpose_alignment" in self.utility_weights:
                    score_parts.append(future.purpose_alignment * self.utility_weights.get("alignment", self.utility_weights.get("purpose_alignment", 0.0)))
                if "yield" in self.utility_weights:
                    yield_component = future.expected_yield / (future.expected_yield + future.cost_estimate + 1.0)
                    score_parts.append(yield_component * self.utility_weights["yield"])
                if "novelty" in self.utility_weights:
                    novelty = 1.0 if any(tag.name == "novelty" for tag in future.tags) else 0.5
                    score_parts.append(novelty * self.utility_weights["novelty"])
            if score_parts:
                total_weight = sum(self.utility_weights.values()) or 1.0
                return _clamp(sum(score_parts) / total_weight, 0.0, 1.0)
            return _clamp(future.purpose_alignment, 0.0, 1.0)
        delta = str(future_or_delta or "").lower()
        if not delta:
            return 0.0
        domain_score = 0.5 if self.domain and self.domain.lower() in delta else 0.0
        kw_hits = sum(1 for kw in self.keywords if kw.lower() in delta)
        kw_score = (kw_hits / len(self.keywords) * 0.5) if self.keywords else 0.0
        return _clamp(domain_score + kw_score, 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose_id": self.purpose_id,
            "components": list(self.components),
            "weights": list(self.weights),
            "domain": self.domain,
            "utility_weights": dict(self.utility_weights),
            "alignment_threshold": self.alignment_threshold,
            "description": self.description,
            "keywords": list(self.keywords),
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PurposeFunction:
        return cls(
            purpose_id=str(data.get("purpose_id", "")),
            components=data.get("components") or (),
            weights=data.get("weights"),
            domain=str(data.get("domain", "")),
            utility_weights=data.get("utility_weights") or None,
            alignment_threshold=float(data.get("alignment_threshold", 0.0)),
            description=str(data.get("description", "")),
            keywords=data.get("keywords") or (),
            weight=float(data.get("weight", 1.0)),
        )


@dataclass(frozen=True, init=False)
class SemanticFuture:
    future_id: str
    title: str
    description: str
    delta: str
    source_state_id: str
    reachability: float
    purpose_alignment: float
    yield_estimate: float
    cost_estimate: float
    tags: tuple[FutureTag, ...]
    metadata: dict[str, Any]
    operator_id: str
    explanation: str
    timestamp: float

    def __init__(
        self,
        *,
        future_id: str,
        title: str = "",
        description: str = "",
        delta: str = "",
        source_state_id: str = "",
        reachability: float,
        purpose_alignment: float,
        yield_estimate: float | None = None,
        expected_yield: float | None = None,
        cost_estimate: float | None = None,
        cost: float | None = None,
        tags: Iterable[FutureTag | str] = (),
        metadata: Mapping[str, Any] | None = None,
        operator_id: str = "",
        explanation: str = "",
        timestamp: float | None = None,
        value: float | None = None,
    ) -> None:
        reach = float(reachability)
        align = float(purpose_alignment)
        if not (0.0 <= reach <= 1.0):
            raise ValueError("reachability must be in [0, 1]")
        if not (0.0 <= align <= 1.0):
            raise ValueError("purpose_alignment must be in [0, 1]")
        yld = float(expected_yield if expected_yield is not None else yield_estimate if yield_estimate is not None else value if value is not None else 1.0)
        cst = float(cost if cost is not None else cost_estimate if cost_estimate is not None else 0.0)
        if cst < 0.0:
            raise ValueError("cost_estimate must be non-negative")
        object.__setattr__(self, "future_id", future_id)
        object.__setattr__(self, "title", title or delta or future_id)
        object.__setattr__(self, "description", description or delta)
        object.__setattr__(self, "delta", delta or title or description)
        object.__setattr__(self, "source_state_id", source_state_id)
        object.__setattr__(self, "reachability", reach)
        object.__setattr__(self, "purpose_alignment", align)
        object.__setattr__(self, "yield_estimate", yld)
        object.__setattr__(self, "cost_estimate", cst)
        tag_values = tuple(FutureTag.coerce(tag) for tag in tags)
        metadata_dict = dict(metadata or {})
        if value is not None and "composite_value" not in metadata_dict:
            metadata_dict["composite_value"] = float(value)
        object.__setattr__(self, "tags", tag_values)
        object.__setattr__(self, "metadata", metadata_dict)
        object.__setattr__(self, "operator_id", operator_id)
        object.__setattr__(self, "explanation", explanation)
        object.__setattr__(self, "timestamp", float(timestamp if timestamp is not None else time.time()))

    @property
    def expected_yield(self) -> float:
        return self.yield_estimate

    @property
    def cost(self) -> float:
        return self.cost_estimate

    @property
    def deltas(self) -> tuple[str, ...]:
        return tuple(token for token in self.delta.lower().split() if token)

    def value(self) -> float:
        return self.purpose_alignment * self.reachability * self.yield_estimate - self.cost_estimate

    def composite_value(self) -> float:
        if "composite_value" in self.metadata:
            return float(self.metadata["composite_value"])
        return self.value()

    def is_viable(self, min_reachability: float = 0.0) -> bool:
        return self.reachability >= min_reachability and self.value() > 0.0

    def dominates(self, other: SemanticFuture) -> bool:
        return (
            self.reachability >= other.reachability
            and self.purpose_alignment >= other.purpose_alignment
            and self.yield_estimate >= other.yield_estimate
            and self.cost_estimate <= other.cost_estimate
            and (
                self.reachability > other.reachability
                or self.purpose_alignment > other.purpose_alignment
                or self.yield_estimate > other.yield_estimate
                or self.cost_estimate < other.cost_estimate
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "future_id": self.future_id,
            "title": self.title,
            "description": self.description,
            "delta": self.delta,
            "source_state_id": self.source_state_id,
            "reachability": self.reachability,
            "purpose_alignment": self.purpose_alignment,
            "yield_estimate": self.yield_estimate,
            "expected_yield": self.expected_yield,
            "cost_estimate": self.cost_estimate,
            "tags": [tag.name for tag in self.tags],
            "metadata": dict(self.metadata),
            "operator_id": self.operator_id,
            "explanation": self.explanation,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemanticFuture:
        return cls(
            future_id=str(data["future_id"]),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            delta=str(data.get("delta", "")),
            source_state_id=str(data.get("source_state_id", "")),
            reachability=float(data.get("reachability", 0.0)),
            purpose_alignment=float(data.get("purpose_alignment", 0.0)),
            yield_estimate=float(data.get("yield_estimate", data.get("expected_yield", 1.0))),
            cost_estimate=float(data.get("cost_estimate", data.get("cost", 0.0))),
            tags=data.get("tags", ()),
            metadata=data.get("metadata", {}),
            operator_id=str(data.get("operator_id", "")),
            explanation=str(data.get("explanation", "")),
            timestamp=float(data.get("timestamp", time.time())),
        )

    def __str__(self) -> str:
        return f"SemanticFuture({self.future_id}, value={self.value():.3f})"


@dataclass(frozen=True)
class FutureValuation:
    future_id: str
    purpose_score: float
    reachability_score: float
    yield_score: float
    cost_score: float
    composite: float

    @property
    def score(self) -> float:
        return self.composite

    def is_viable(self) -> bool:
        return self.composite > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "future_id": self.future_id,
            "purpose_score": self.purpose_score,
            "reachability_score": self.reachability_score,
            "yield_score": self.yield_score,
            "cost_score": self.cost_score,
            "composite": self.composite,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FutureValuation:
        return cls(
            future_id=str(data["future_id"]),
            purpose_score=float(data.get("purpose_score", 0.0)),
            reachability_score=float(data.get("reachability_score", 0.0)),
            yield_score=float(data.get("yield_score", 0.0)),
            cost_score=float(data.get("cost_score", 0.0)),
            composite=float(data.get("composite", 0.0)),
        )


@dataclass
class IdeationState:
    state_id: str = ""
    current_state: FutureState | None = None
    purpose: PurposeFunction | None = None
    reachable_futures: list[SemanticFuture] = field(default_factory=list)
    budget_remaining: float = 0.0
    archive: list[SemanticFuture] = field(default_factory=list)
    spent_budget: float = 0.0
    epoch: int = 0
    n_generated: int = 0
    n_accepted: int = 0
    n_rejected: int = 0
    active_regime: str | None = None
    phase: str = "exploration"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        *,
        state_id: str = "",
        current_state: FutureState | None = None,
        purpose: PurposeFunction | None = None,
        reachable_futures: Iterable[SemanticFuture] | None = None,
        budget_remaining: float | None = None,
        archive: Iterable[SemanticFuture] | None = None,
        futures: Iterable[SemanticFuture] | None = None,
        budget: float | None = None,
        spent_budget: float = 0.0,
        spent: float | None = None,
        epoch: int = 0,
        n_generated: int = 0,
        n_accepted: int = 0,
        n_rejected: int = 0,
        active_regime: str | None = None,
        phase: str = "exploration",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.state_id = state_id
        self.current_state = current_state
        self.purpose = purpose
        self.reachable_futures = list(reachable_futures if reachable_futures is not None else futures or [])
        self.budget_remaining = float(budget_remaining if budget_remaining is not None else budget if budget is not None else 0.0)
        self.archive = list(archive or [])
        self.spent_budget = float(spent if spent is not None else spent_budget)
        self.epoch = int(epoch)
        self.n_generated = int(n_generated)
        self.n_accepted = int(n_accepted)
        self.n_rejected = int(n_rejected)
        self.active_regime = active_regime
        self.phase = phase
        self.metadata = dict(metadata or {})
        if self.budget_remaining < 0.0 or self.spent_budget < 0.0:
            raise ValueError("budget values must be non-negative")

    @property
    def futures(self) -> list[SemanticFuture]:
        return self.reachable_futures

    @property
    def budget(self) -> float:
        return self.budget_remaining

    def best_future(self) -> SemanticFuture | None:
        return max(self.reachable_futures, key=lambda f: f.value(), default=None)

    def viable_futures(self) -> list[SemanticFuture]:
        return [f for f in self.reachable_futures if f.is_viable()]

    def archive_future(self, future_id: str) -> None:
        for idx, future in enumerate(self.reachable_futures):
            if future.future_id == future_id:
                self.archive.append(future)
                del self.reachable_futures[idx]
                return
        raise KeyError(future_id)

    def advance_to(self, future: SemanticFuture, cost: float) -> IdeationState:
        return IdeationState(
            state_id=self.state_id,
            current_state=self.current_state,
            purpose=self.purpose,
            reachable_futures=list(self.reachable_futures),
            budget_remaining=self.budget_remaining,
            archive=list(self.archive),
            spent_budget=self.spent_budget + float(cost),
            epoch=self.epoch + 1,
            n_generated=self.n_generated,
            n_accepted=self.n_accepted,
            n_rejected=self.n_rejected,
            active_regime=self.active_regime,
            phase=self.phase,
            metadata={**self.metadata, "advanced_to": future.future_id},
        )

    def remaining_budget_fraction(self) -> float:
        if self.budget_remaining <= 0.0:
            return 0.0
        remaining = max(self.budget_remaining - self.spent_budget, 0.0)
        return remaining / self.budget_remaining

    def acceptance_rate(self) -> float:
        return self.n_accepted / self.n_generated if self.n_generated > 0 else 0.0

    def is_budget_exhausted(self, threshold: float = 0.0) -> bool:
        return (self.budget_remaining - self.spent_budget) <= threshold

    def advance_epoch(self) -> None:
        self.epoch += 1

    def spend(self, cost: float) -> None:
        if cost < 0.0:
            raise ValueError("cost must be non-negative")
        self.spent_budget += cost
        self.budget_remaining = max(self.budget_remaining - cost, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "current_state": self.current_state.to_dict() if self.current_state else None,
            "purpose": self.purpose.to_dict() if self.purpose else None,
            "reachable_futures": [f.to_dict() for f in self.reachable_futures],
            "budget_remaining": self.budget_remaining,
            "archive": [f.to_dict() for f in self.archive],
            "spent_budget": self.spent_budget,
            "epoch": self.epoch,
            "n_generated": self.n_generated,
            "n_accepted": self.n_accepted,
            "n_rejected": self.n_rejected,
            "active_regime": self.active_regime,
            "phase": self.phase,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IdeationState:
        current_state = data.get("current_state")
        purpose = data.get("purpose")
        return cls(
            state_id=str(data.get("state_id", "")),
            current_state=FutureState.from_dict(current_state) if isinstance(current_state, Mapping) else None,
            purpose=PurposeFunction.from_dict(purpose) if isinstance(purpose, Mapping) else None,
            reachable_futures=[SemanticFuture.from_dict(f) for f in data.get("reachable_futures", [])],
            budget_remaining=float(data.get("budget_remaining", data.get("budget", 0.0))),
            archive=[SemanticFuture.from_dict(f) for f in data.get("archive", [])],
            spent_budget=float(data.get("spent_budget", 0.0)),
            epoch=int(data.get("epoch", 0)),
            n_generated=int(data.get("n_generated", 0)),
            n_accepted=int(data.get("n_accepted", 0)),
            n_rejected=int(data.get("n_rejected", 0)),
            active_regime=data.get("active_regime"),
            phase=str(data.get("phase", "exploration")),
            metadata=data.get("metadata", {}),
        )


class FutureFilter:
    @staticmethod
    def filter_by_budget(futures: Iterable[SemanticFuture], max_cost: float) -> list[SemanticFuture]:
        return [f for f in futures if f.cost_estimate <= max_cost]

    @staticmethod
    def filter_by_tags(futures: Iterable[SemanticFuture], tag_names: set[str]) -> list[SemanticFuture]:
        return [f for f in futures if any(tag.name in tag_names for tag in f.tags)]

    @staticmethod
    def filter_by_min_reachability(futures: Iterable[SemanticFuture], min_r: float) -> list[SemanticFuture]:
        return [f for f in futures if f.reachability >= min_r]

    @staticmethod
    def filter_by_min_alignment(futures: Iterable[SemanticFuture], min_a: float) -> list[SemanticFuture]:
        return [f for f in futures if f.purpose_alignment >= min_a]

    @staticmethod
    def filter_dominated(futures: Iterable[SemanticFuture]) -> list[SemanticFuture]:
        futures_list = list(futures)
        return [f for f in futures_list if not any(other is not f and other.dominates(f) for other in futures_list)]


class FutureRanker:
    @staticmethod
    def rank_by_value(futures: Iterable[SemanticFuture]) -> list[SemanticFuture]:
        return sorted(futures, key=lambda f: f.value(), reverse=True)

    @staticmethod
    def rank_by_reachability(futures: Iterable[SemanticFuture]) -> list[SemanticFuture]:
        return sorted(futures, key=lambda f: f.reachability, reverse=True)

    @staticmethod
    def rank_composite(futures: Iterable[SemanticFuture]) -> list[SemanticFuture]:
        return sorted(
            futures,
            key=lambda f: (f.purpose_alignment + f.reachability + f.expected_yield) - f.cost_estimate,
            reverse=True,
        )


class FutureComparator:
    @staticmethod
    def compare(a: SemanticFuture, b: SemanticFuture) -> int:
        a_val = a.value()
        b_val = b.value()
        if a_val > b_val:
            return 1
        if a_val < b_val:
            return -1
        return 0

    @staticmethod
    def is_pareto_optimal(future: SemanticFuture, others: Iterable[SemanticFuture]) -> bool:
        return not any(other.dominates(future) for other in others)

    @staticmethod
    def pareto_front(futures: Iterable[SemanticFuture]) -> list[SemanticFuture]:
        futures_list = list(futures)
        return [f for f in futures_list if FutureComparator.is_pareto_optimal(f, [o for o in futures_list if o is not f])]
