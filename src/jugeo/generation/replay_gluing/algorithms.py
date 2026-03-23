"""Replay algorithm implementations (theory2.tex §41.6).

Provides concrete algorithm classes for full, incremental, and lazy replay
of gluing plans, plus scheduling and registry infrastructure.
"""
from __future__ import annotations

import abc
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.replay_gluing.models import (
    GluingUnderReplay,
    ReplayGluingPlan,
    ReplayPhase,
    ReplayStrategy,
)
from jugeo.generation.replay_gluing.replay_planning import ChangeSet

__all__ = [
    "ReplayAlgorithm",
    "FullReplayAlgorithm",
    "IncrementalReplayAlgorithm",
    "LazyReplayAlgorithm",
    "ChangeImpactAnalyzer",
    "GluingMerger",
    "ReplayTask",
    "ReplayScheduler",
    "AlgorithmRegistry",
    "select_algorithm",
    "run_algorithm",
    "DEFAULT_ALGORITHM",
]

DEFAULT_ALGORITHM: str = "incremental"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ReplayAlgorithm(abc.ABC):
    """Abstract base class for replay algorithms."""

    @abc.abstractmethod
    def get_name(self) -> str:
        ...

    @abc.abstractmethod
    def supports_strategy(self, strategy: ReplayStrategy) -> bool:
        ...

    @abc.abstractmethod
    def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
        ...

    def pre_check(self, plan: ReplayGluingPlan) -> list[str]:
        errors: list[str] = []
        if not plan.is_valid():
            errors.append("Plan is not valid: changed and unchanged sets overlap")
        return errors


# ---------------------------------------------------------------------------
# Concrete algorithms
# ---------------------------------------------------------------------------


class FullReplayAlgorithm(ReplayAlgorithm):
    """Replays every patch unconditionally."""

    def get_name(self) -> str:
        return "full"

    def supports_strategy(self, strategy: ReplayStrategy) -> bool:
        return strategy == ReplayStrategy.FULL

    def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
        g = GluingUnderReplay(plan=plan)
        g.phase = ReplayPhase.REPLAYING
        for patch in sorted(plan.all_patches):
            g.mark_replayed(patch, {"section": f"full_section_{patch}", "version": 1})
        g.transition(ReplayPhase.COMPLETED)
        return g


class IncrementalReplayAlgorithm(ReplayAlgorithm):
    """Replays only changed patches; reuses cached results for unchanged ones."""

    def get_name(self) -> str:
        return "incremental"

    def supports_strategy(self, strategy: ReplayStrategy) -> bool:
        return strategy in (ReplayStrategy.INCREMENTAL, ReplayStrategy.ADAPTIVE)

    def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
        g = GluingUnderReplay(plan=plan)
        g.phase = ReplayPhase.REPLAYING
        for patch in sorted(plan.changed_patches):
            g.mark_replayed(patch, {"section": f"new_section_{patch}", "changed": True})
        for patch in sorted(plan.unchanged_patches):
            g.mark_replayed(patch, {"section": f"cached_section_{patch}", "changed": False})
        g.transition(ReplayPhase.COMPLETED)
        return g


class LazyReplayAlgorithm(ReplayAlgorithm):
    """Defers unchanged patches; only replays strictly needed ones."""

    def get_name(self) -> str:
        return "lazy"

    def supports_strategy(self, strategy: ReplayStrategy) -> bool:
        return strategy == ReplayStrategy.LAZY

    def execute(self, plan: ReplayGluingPlan) -> GluingUnderReplay:
        g = GluingUnderReplay(plan=plan)
        g.phase = ReplayPhase.REPLAYING
        for patch in sorted(plan.changed_patches):
            g.mark_replayed(patch, {"section": f"lazy_section_{patch}"})
        g.deferred_patches = sorted(plan.unchanged_patches)
        g.transition(ReplayPhase.COMPLETED)
        return g


# ---------------------------------------------------------------------------
# ChangeImpactAnalyzer
# ---------------------------------------------------------------------------


class ChangeImpactAnalyzer:
    """Determines the blast radius of a change set."""

    _SEVERITY = {"removed": 3, "added": 2, "modified": 1}

    def analyze(self, change_set: ChangeSet) -> dict[str, Any]:
        impacts: dict[str, Any] = {}
        for patch in change_set.changed_patches:
            meta = change_set.change_metadata.get(patch, {})
            change_type = meta.get("type", "modified") if isinstance(meta, dict) else "modified"
            impacts[patch] = {
                "severity": self._SEVERITY.get(change_type, 1),
                "change_type": change_type,
                "transitive_deps": [],
            }
        for patch in change_set.removed_patches:
            impacts[patch] = {"severity": self._SEVERITY["removed"], "change_type": "removed", "transitive_deps": []}
        return impacts

    def compute_blast_radius(self, patch: str, dependencies: dict[str, frozenset[str]]) -> int:
        visited: set[str] = set()
        queue = [patch]
        while queue:
            current = queue.pop()
            for p, deps in dependencies.items():
                if current in deps and p not in visited:
                    visited.add(p)
                    queue.append(p)
        return len(visited)

    def rank_by_impact(self, impacts: dict[str, Any]) -> list[str]:
        return sorted(impacts, key=lambda p: impacts[p].get("severity", 0), reverse=True)

    def severity(self, change_type: str) -> int:
        return self._SEVERITY.get(change_type, 1)


# ---------------------------------------------------------------------------
# GluingMerger
# ---------------------------------------------------------------------------


class GluingMerger:
    """Merges two GluingUnderReplay objects, with g2 taking precedence."""

    def merge(self, g1: GluingUnderReplay, g2: GluingUnderReplay) -> GluingUnderReplay:
        merged = GluingUnderReplay(plan=g2.plan)
        merged.patch_sections = {**g1.patch_sections, **g2.patch_sections}
        merged.overlaps = {**g1.overlaps, **g2.overlaps}
        merged.replayed_patches = list(
            dict.fromkeys(g1.replayed_patches + g2.replayed_patches)
        )
        return merged

    def resolve_conflict(self, d1: Any, d2: Any) -> Any:
        if isinstance(d1, dict) and isinstance(d2, dict):
            return {**d1, **d2}
        return d2

    def verify_merge_coherence(self, merged: GluingUnderReplay) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        for p in merged.replayed_patches:
            if p in seen:
                errors.append(f"Duplicate patch in replayed_patches: {p}")
            seen.add(p)
        return errors


# ---------------------------------------------------------------------------
# ReplayTask / ReplayScheduler
# ---------------------------------------------------------------------------


@dataclass
class ReplayTask:
    """A unit of work in the replay schedule."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    patch: str = ""
    is_changed: bool = True
    priority: int = 0
    dependencies: frozenset[str] = field(default_factory=frozenset)
    estimated_ms: float = 10.0
    status: str = "pending"


class ReplayScheduler:
    """Schedules replay tasks respecting dependencies and priority."""

    def schedule(self, plan: ReplayGluingPlan) -> list[ReplayTask]:
        tasks: list[ReplayTask] = []
        for patch in sorted(plan.changed_patches):
            tasks.append(ReplayTask(
                patch=patch,
                is_changed=True,
                priority=10,
                dependencies=plan.dependencies.get(patch, frozenset()),
            ))
        for patch in sorted(plan.unchanged_patches):
            tasks.append(ReplayTask(
                patch=patch,
                is_changed=False,
                priority=5,
                dependencies=plan.dependencies.get(patch, frozenset()),
            ))
        tasks.sort(key=lambda t: (-t.priority, t.patch))
        return tasks

    def get_ready_tasks(self, tasks: list[ReplayTask], completed: set[str]) -> list[ReplayTask]:
        return [t for t in tasks if t.status == "pending" and t.dependencies <= completed]

    def estimate_completion_time(self, tasks: list[ReplayTask]) -> float:
        return sum(t.estimated_ms for t in tasks)


# ---------------------------------------------------------------------------
# AlgorithmRegistry
# ---------------------------------------------------------------------------


class AlgorithmRegistry:
    """Registry of available replay algorithms."""

    def __init__(self) -> None:
        self._algorithms: dict[str, ReplayAlgorithm] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        for algo in [FullReplayAlgorithm(), IncrementalReplayAlgorithm(), LazyReplayAlgorithm()]:
            self._algorithms[algo.get_name()] = algo

    def get(self, name: str | ReplayStrategy) -> ReplayAlgorithm | None:
        if isinstance(name, ReplayStrategy):
            return self.get_for_strategy(name)
        return self._algorithms.get(name)

    def get_for_strategy(self, strategy: ReplayStrategy) -> ReplayAlgorithm | None:
        for algo in self._algorithms.values():
            if algo.supports_strategy(strategy):
                return algo
        return None

    def register(self, algo: ReplayAlgorithm) -> None:
        self._algorithms[algo.get_name()] = algo

    def list_names(self) -> list[str]:
        return list(self._algorithms.keys())


_registry = AlgorithmRegistry()


def select_algorithm(strategy: ReplayStrategy) -> ReplayAlgorithm:
    algo = _registry.get_for_strategy(strategy)
    return algo or _registry.get(DEFAULT_ALGORITHM) or IncrementalReplayAlgorithm()


def run_algorithm(plan: ReplayGluingPlan) -> GluingUnderReplay:
    algo = select_algorithm(plan.strategy)
    return algo.execute(plan)
