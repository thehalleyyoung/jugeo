"""Integration layer for the replay_gluing pipeline (theory2.tex §41.7).

Connects the planner, replayer, and convergence verifier into a single
end-to-end pipeline, and provides adaptors for jugeo.geometry.descent
and jugeo.generation.goals.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.replay_gluing.models import (
    GluingUnderReplay,
    ReplayGluingPlan,
    ReplayPhase,
    ReplayStrategy,
    ConvergenceRecord,
)
from jugeo.generation.replay_gluing.replay_planning import ChangeSet, ReplayPlanner
from jugeo.generation.replay_gluing.incremental_replay import (
    GluingSnapshot,
    ReplayCache,
    IncrementalReplayer,
)
from jugeo.generation.replay_gluing.convergence_verification import (
    ConvergenceCertificate,
    ConvergenceVerifier,
)
from jugeo.generation.replay_gluing.algorithms import run_algorithm

__all__ = [
    "ReplayGluingPipeline",
    "DescentAdaptor",
    "GoalAdaptor",
    "FrontierIntegrator",
    "PipelineResult",
    "run_full_pipeline",
    "pipeline_from_goal_change",
]


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Result returned by a full pipeline run."""

    result_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    success: bool = False
    gluing: GluingUnderReplay | None = None
    certificate: ConvergenceCertificate | None = None
    convergence_record: ConvergenceRecord | None = None
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        cert = f" cert={self.certificate.cert_id[:8]}" if self.certificate else ""
        return f"PipelineResult[{status}{cert} elapsed={self.elapsed_seconds:.3f}s]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "success": self.success,
            "gluing_id": self.gluing.gluing_id if self.gluing else None,
            "certificate": self.certificate.to_dict() if self.certificate else None,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "elapsed_seconds": self.elapsed_seconds,
        }


# ---------------------------------------------------------------------------
# ReplayGluingPipeline
# ---------------------------------------------------------------------------


class ReplayGluingPipeline:
    """Orchestrates planning → replay → verification."""

    def __init__(
        self,
        strategy: ReplayStrategy = ReplayStrategy.INCREMENTAL,
        verify_convergence: bool = True,
        max_rounds: int = 10,
    ) -> None:
        self.strategy = strategy
        self.verify_convergence = verify_convergence
        self.max_rounds = max_rounds
        self._planner = ReplayPlanner(strategy_override=strategy)
        self._verifier = ConvergenceVerifier()

    def run(
        self,
        change_set: ChangeSet,
        prior_state: dict[str, Any] | None = None,
    ) -> PipelineResult:
        t0 = time.time()
        prior_state = prior_state or {}
        try:
            plan = self._planner.plan(change_set, prior_state)
            gluing = run_algorithm(plan)
            certificate: ConvergenceCertificate | None = None
            if self.verify_convergence:
                history = [gluing]
                certificate = self._verifier.certify_convergence(history)
            return PipelineResult(
                success=True,
                gluing=gluing,
                certificate=certificate,
                elapsed_seconds=time.time() - t0,
            )
        except Exception as exc:
            return PipelineResult(
                success=False,
                error_message=str(exc),
                elapsed_seconds=time.time() - t0,
            )


# ---------------------------------------------------------------------------
# DescentAdaptor
# ---------------------------------------------------------------------------


class DescentAdaptor:
    """Adapts the jugeo.geometry.descent interface for use with replay gluing."""

    def adapt(self, gluing: GluingUnderReplay) -> dict[str, Any]:
        return {
            "patch_sections": dict(gluing.patch_sections),
            "overlaps": dict(gluing.overlaps),
            "phase": gluing.phase.value,
        }

    def build_gluing_data(self, gluing: GluingUnderReplay) -> dict[str, Any]:
        return {"sections": gluing.patch_sections, "overlaps": gluing.overlaps}

    def run_descent(self, gluing_data: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "result": gluing_data, "converged": True}

    def extract_local_sections(self, gluing: GluingUnderReplay) -> dict[str, Any]:
        return dict(gluing.patch_sections)

    def extract_overlap_conditions(self, gluing: GluingUnderReplay) -> dict[str, Any]:
        return dict(gluing.overlaps)


# ---------------------------------------------------------------------------
# GoalAdaptor
# ---------------------------------------------------------------------------


class GoalAdaptor:
    """Converts goal objects / dicts into ChangeSet and prior gluing."""

    def goal_change_to_change_set(
        self,
        old_goal: Any,
        new_goal: Any,
    ) -> ChangeSet:
        old_patches = self._extract_patches(old_goal)
        new_patches = self._extract_patches(new_goal)
        changed = new_patches - old_patches | (new_patches & old_patches)
        unchanged: frozenset[str] = frozenset()
        removed = old_patches - new_patches
        if old_patches == new_patches:
            return ChangeSet(
                changed_patches=frozenset(),
                unchanged_patches=old_patches,
                removed_patches=frozenset(),
            )
        return ChangeSet(
            changed_patches=changed,
            unchanged_patches=frozenset(),
            removed_patches=removed,
        )

    def _extract_patches(self, goal: Any) -> frozenset[str]:
        if goal is None:
            return frozenset()
        if isinstance(goal, dict):
            raw = goal.get("patches", goal.get("patch_names", []))
            if isinstance(raw, (list, set, frozenset)):
                return frozenset(str(p) for p in raw)
            return frozenset()
        # Try .patches or .patch_names attribute
        for attr in ("patches", "patch_names"):
            val = getattr(goal, attr, None)
            if val is not None:
                if isinstance(val, (set, frozenset, list)):
                    return frozenset(str(p) for p in val)
        return frozenset()

    def goals_to_prior_gluing(self, goals: Any) -> dict[str, Any]:
        patches = self._extract_patches(goals)
        return {p: {"section": f"prior_{p}"} for p in patches}


# ---------------------------------------------------------------------------
# FrontierIntegrator
# ---------------------------------------------------------------------------


class FrontierIntegrator:
    """Integrates pipeline results back into the construction frontier."""

    def __init__(self) -> None:
        self._frontier: dict[str, Any] = {}

    def integrate(self, result: PipelineResult) -> None:
        if not result.success or result.gluing is None:
            return
        updates = self.extract_frontier_updates(result)
        self._frontier.update(updates)

    def create_frontier_item(self, patch: str, section_data: Any) -> dict[str, Any]:
        return {"patch": patch, "section": section_data, "timestamp": time.time()}

    def extract_frontier_updates(self, result: PipelineResult) -> dict[str, Any]:
        if result.gluing is None:
            return {}
        return {
            p: self.create_frontier_item(p, s)
            for p, s in result.gluing.patch_sections.items()
        }

    def mark_resolved(self, patch: str) -> None:
        self._frontier.pop(patch, None)

    @property
    def frontier(self) -> dict[str, Any]:
        return dict(self._frontier)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def run_full_pipeline(
    change_set: ChangeSet,
    prior_state: dict[str, Any] | None = None,
    strategy: ReplayStrategy = ReplayStrategy.INCREMENTAL,
) -> PipelineResult:
    pipeline = ReplayGluingPipeline(strategy=strategy)
    return pipeline.run(change_set, prior_state)


def pipeline_from_goal_change(
    old_goal: Any,
    new_goal: Any,
    strategy: ReplayStrategy = ReplayStrategy.INCREMENTAL,
) -> PipelineResult:
    adaptor = GoalAdaptor()
    change_set = adaptor.goal_change_to_change_set(old_goal, new_goal)
    prior_state = adaptor.goals_to_prior_gluing(old_goal)
    return run_full_pipeline(change_set, prior_state, strategy=strategy)
