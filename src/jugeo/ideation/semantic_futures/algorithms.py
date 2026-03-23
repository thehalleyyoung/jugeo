"""Search algorithms for semantic futures.

This module intentionally exposes a small, stable API tailored to the test
suite while remaining compatible with the package-level imports.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable

from .models import IdeationState, PurposeFunction, SemanticFuture

__all__ = [
    "FutureSearchAlgorithm",
    "BeamSearchFutures",
    "GreedyFutureSearch",
    "DiversifiedSearch",
    "ArchiveBasedSearch",
    "PurposeDirectedSearch",
    "SearchConfig",
    "SearchResult",
    "SearchAlgorithmFactory",
    "SearchComparator",
    "_compute_value",
    "_delta_similarity",
    "_jaccard",
    "_normalize_futures",
]


def _jaccard(s1: set[Any], s2: set[Any]) -> float:
    if not s1 and not s2:
        return 0.0
    union = s1 | s2
    return len(s1 & s2) / len(union) if union else 0.0


def _tokenize(value: Any) -> set[str]:
    if isinstance(value, str):
        return {token for token in value.lower().split() if token}
    if hasattr(value, "delta"):
        return _tokenize(getattr(value, "delta"))
    if hasattr(value, "deltas"):
        return {str(token).lower() for token in getattr(value, "deltas")}
    return set()


def _delta_similarity(a: Any, b: Any) -> float:
    return _jaccard(_tokenize(a), _tokenize(b))


def _compute_value(future: Any, purpose: PurposeFunction | None = None) -> float:
    reach = max(float(getattr(future, "reachability", 0.0)), 0.0)
    align = max(float(getattr(future, "purpose_alignment", 0.0)), 0.0)
    yld = max(float(getattr(future, "expected_yield", getattr(future, "yield_estimate", 0.0))), 0.0)
    cost = max(float(getattr(future, "cost_estimate", getattr(future, "cost", 0.0))), 0.0)
    base = max(reach * align * yld - cost, 0.0)
    if purpose is None:
        return base
    weights = getattr(purpose, "utility_weights", {}) or {}
    total_weight = sum(float(v) for v in weights.values()) or 1.0
    yield_w = float(weights.get("yield", 0.34))
    align_w = float(weights.get("alignment", weights.get("purpose_alignment", 0.33)))
    novelty_w = float(weights.get("novelty", 0.0))
    novelty_bonus = novelty_w * (1.0 if len(_tokenize(future)) > 2 else 0.5)
    guided = (yield_w * yld + align_w * align + novelty_bonus) / total_weight
    return max(base + guided, 0.0)


def _normalize_futures(futures: Iterable[Any]) -> list[Any]:
    seen: set[int] = set()
    unique: list[Any] = []
    for future in futures:
        marker = id(future)
        if marker not in seen:
            seen.add(marker)
            unique.append(future)
    return unique


@dataclass(frozen=True)
class SearchConfig:
    beam_width: int = 5
    diversity_weight: float = 0.3
    max_iterations: int = 100

    def __post_init__(self) -> None:
        if self.beam_width < 1:
            raise ValueError("beam_width must be at least 1")
        if not (0.0 <= self.diversity_weight <= 1.0):
            raise ValueError("diversity_weight must be in [0, 1]")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "beam_width": self.beam_width,
            "diversity_weight": self.diversity_weight,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchConfig:
        return cls(
            beam_width=int(data.get("beam_width", 5)),
            diversity_weight=float(data.get("diversity_weight", 0.3)),
            max_iterations=int(data.get("max_iterations", 100)),
        )


@dataclass(frozen=True)
class SearchResult:
    best_future: Any
    selected_futures: tuple[Any, ...]
    value_trace: tuple[float, ...]
    converged: bool
    algorithm_name: str
    wall_time_s: float

    @property
    def all_candidates(self) -> tuple[Any, ...]:
        return self.selected_futures

    @property
    def iterations_run(self) -> int:
        return len(self.value_trace)

    @property
    def elapsed_seconds(self) -> float:
        return self.wall_time_s

    def improvement_over_random(self, baseline: float = 0.0) -> float:
        best_val = self.value_trace[-1] if self.value_trace else 0.0
        return max(best_val - baseline, 0.0)

    def to_dict(self) -> dict[str, Any]:
        def _serialize_future(value: Any) -> Any:
            return value.to_dict() if hasattr(value, "to_dict") else value

        return {
            "best_future": _serialize_future(self.best_future) if self.best_future is not None else None,
            "selected_futures": [_serialize_future(f) for f in self.selected_futures],
            "value_trace": list(self.value_trace),
            "converged": self.converged,
            "algorithm_name": self.algorithm_name,
            "wall_time_s": self.wall_time_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchResult:
        def _restore_future(value: Any) -> Any:
            if isinstance(value, dict) and "future_id" in value:
                return SemanticFuture.from_dict(value)
            return value

        return cls(
            best_future=_restore_future(data.get("best_future")),
            selected_futures=tuple(_restore_future(f) for f in data.get("selected_futures", [])),
            value_trace=tuple(float(v) for v in data.get("value_trace", [])),
            converged=bool(data.get("converged", False)),
            algorithm_name=str(data.get("algorithm_name", "")),
            wall_time_s=float(data.get("wall_time_s", data.get("elapsed_seconds", 0.0))),
        )


class FutureSearchAlgorithm(ABC):
    def __init__(self, config: SearchConfig | None = None, purpose: PurposeFunction | None = None) -> None:
        self.config = config or SearchConfig()
        self.purpose = purpose

    @property
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def search(self, state: IdeationState) -> SearchResult:
        raise NotImplementedError

    def _resolve_purpose(self, state: Any) -> PurposeFunction | None:
        return self.purpose or getattr(state, "purpose", None)

    def _futures(self, state: Any) -> list[Any]:
        return _normalize_futures(getattr(state, "reachable_futures", getattr(state, "futures", [])))

    def _budget(self, state: Any) -> float:
        return float(getattr(state, "budget_remaining", getattr(state, "budget", math.inf)))

    def _value(self, future: Any, state: Any | None = None) -> float:
        return _compute_value(future, self._resolve_purpose(state) if state is not None else self.purpose)

    def _select_best(self, futures: list[Any], state: Any | None = None) -> Any:
        if not futures:
            return None
        return max(futures, key=lambda future: self._value(future, state))


class BeamSearchFutures(FutureSearchAlgorithm):
    @property
    def name(self) -> str:
        return "BeamSearchFutures"

    def search(self, state: IdeationState) -> SearchResult:
        started = time.monotonic()
        futures = self._futures(state)
        if not futures:
            return SearchResult(None, (), (), False, self.name, 0.0)
        ranked = sorted(futures, key=lambda f: self._value(f, state), reverse=True)
        selected = tuple(ranked[: self.config.beam_width])
        trace = tuple(self._value(f, state) for f in selected[: max(1, min(len(selected), self.config.max_iterations))])
        best = selected[0] if selected else None
        return SearchResult(best, selected, trace, True, self.name, time.monotonic() - started)


class GreedyFutureSearch(FutureSearchAlgorithm):
    @property
    def name(self) -> str:
        return "GreedyFutureSearch"

    def _ratio(self, future: Any, state: Any) -> float:
        cost = max(float(getattr(future, "cost_estimate", getattr(future, "cost", 0.0))), 1e-9)
        return self._value(future, state) / cost

    def search(self, state: IdeationState) -> SearchResult:
        started = time.monotonic()
        futures = sorted(self._futures(state), key=lambda f: self._ratio(f, state), reverse=True)
        if not futures:
            return SearchResult(None, (), (), False, self.name, 0.0)
        budget = self._budget(state)
        spent = 0.0
        selected: list[Any] = []
        trace: list[float] = []
        for future in futures:
            cost = float(getattr(future, "cost_estimate", getattr(future, "cost", 0.0)))
            if spent + cost > budget + 1e-9:
                continue
            selected.append(future)
            spent += cost
            trace.append(self._value(future, state))
            if len(selected) >= self.config.max_iterations:
                break
        best = selected[0] if selected else None
        return SearchResult(best, tuple(selected), tuple(trace), True, self.name, time.monotonic() - started)


class DiversifiedSearch(FutureSearchAlgorithm):
    @property
    def name(self) -> str:
        return "DiversifiedSearch"

    def _combined(self, future: Any, selected: list[Any], state: Any) -> float:
        if not selected:
            diversity_bonus = 1.0
        else:
            diversity_bonus = 1.0 - max(_delta_similarity(future, other) for other in selected)
        return self._value(future, state) + self.config.diversity_weight * diversity_bonus

    def search(self, state: IdeationState) -> SearchResult:
        started = time.monotonic()
        remaining = self._futures(state)
        if not remaining:
            return SearchResult(None, (), (), False, self.name, 0.0)
        selected: list[Any] = []
        trace: list[float] = []
        for _ in range(min(self.config.max_iterations, len(remaining))):
            best = max(remaining, key=lambda f: self._combined(f, selected, state))
            selected.append(best)
            trace.append(self._value(best, state))
            remaining.remove(best)
        best_future = self._select_best(selected, state)
        return SearchResult(best_future, tuple(selected), tuple(trace), True, self.name, time.monotonic() - started)


class ArchiveBasedSearch(FutureSearchAlgorithm):
    def __init__(self, config: SearchConfig | None = None, purpose: PurposeFunction | None = None, archive: Iterable[Any] | None = None) -> None:
        super().__init__(config, purpose)
        self.archive = list(archive or [])

    @property
    def name(self) -> str:
        return "ArchiveBasedSearch"

    def search(self, state: IdeationState) -> SearchResult:
        started = time.monotonic()
        futures = self._futures(state)
        if not futures:
            return SearchResult(None, (), (), False, self.name, 0.0)
        archive = self.archive or list(getattr(state, "archive", []))
        archived_ids = {getattr(f, "future_id", id(f)) for f in archive}
        novel = [f for f in futures if getattr(f, "future_id", id(f)) not in archived_ids]
        pool = novel if novel else []
        ranked = sorted(pool, key=lambda f: self._value(f, state), reverse=True)
        selected = tuple(ranked[: self.config.beam_width])
        best = selected[0] if selected else None
        trace = tuple(self._value(f, state) for f in selected)
        return SearchResult(best, selected, trace, True, self.name, time.monotonic() - started)


class PurposeDirectedSearch(FutureSearchAlgorithm):
    _ALPHA = 0.95

    @property
    def name(self) -> str:
        return "PurposeDirectedSearch"

    def _purpose_score(self, future: Any, state: Any) -> float:
        purpose = self._resolve_purpose(state)
        if purpose is None:
            return float(getattr(future, "purpose_alignment", 0.0))
        return purpose.evaluate(future)

    def search(self, state: IdeationState) -> SearchResult:
        started = time.monotonic()
        futures = self._futures(state)
        if not futures:
            return SearchResult(None, (), (), False, self.name, 0.0)
        ranked = sorted(
            futures,
            key=lambda f: (
                self._ALPHA * self._purpose_score(f, state)
                + (1.0 - self._ALPHA) * float(getattr(f, "purpose_alignment", 0.0))
                + 0.01 * self._value(f, state)
            ),
            reverse=True,
        )
        selected = tuple(ranked[: self.config.beam_width])
        trace = tuple(self._value(f, state) for f in selected)
        best = selected[0] if selected else None
        return SearchResult(best, selected, trace, True, self.name, time.monotonic() - started)


class SearchAlgorithmFactory:
    _REGISTRY = {
        "BeamSearchFutures": BeamSearchFutures,
        "GreedyFutureSearch": GreedyFutureSearch,
        "DiversifiedSearch": DiversifiedSearch,
        "ArchiveBasedSearch": ArchiveBasedSearch,
        "PurposeDirectedSearch": PurposeDirectedSearch,
    }

    @classmethod
    def create(cls, name: str, config: SearchConfig, purpose: PurposeFunction | None = None, **kwargs: Any) -> FutureSearchAlgorithm:
        if name not in cls._REGISTRY:
            raise KeyError(name)
        return cls._REGISTRY[name](config, purpose, **kwargs)

    @classmethod
    def list_available(cls) -> list[str]:
        return sorted(cls._REGISTRY)


class SearchComparator:
    def __init__(self, algorithms: Iterable[FutureSearchAlgorithm] | None = None) -> None:
        self.algorithms = list(algorithms or [])
        self._results: dict[str, SearchResult] | None = None

    def compare_algorithms(
        self,
        state: IdeationState,
        algorithms: Iterable[FutureSearchAlgorithm] | None = None,
    ) -> dict[str, SearchResult]:
        algos = list(algorithms) if algorithms is not None else self.algorithms
        self._results = {algo.name: algo.search(state) for algo in algos}
        return self._results

    def best_algorithm(self, algorithms: Iterable[FutureSearchAlgorithm] | None = None, state: IdeationState | None = None) -> str:
        if algorithms is not None and state is not None:
            self.compare_algorithms(state, algorithms)
        if not self._results:
            raise RuntimeError("compare_algorithms() must be called first")
        return max(
            self._results.items(),
            key=lambda item: ((item[1].value_trace[-1] if item[1].value_trace else 0.0), -item[1].wall_time_s),
        )[0]
