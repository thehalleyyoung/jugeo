r"""Core dataclass models for the replay_gluing sub-package.

Theory (theory2.tex §41 — Replay Gluing):
    Chapter 41 develops the theory of *replay gluing* — the mechanism by which
    a JuGeo generation run can be efficiently restarted after a partial change
    to the input goal set.  Rather than re-running the entire descent from
    scratch, replay gluing identifies the *minimal affected frontier* and
    replays only those patches whose gluing conditions have been invalidated.

    §41.2 defines a *gluing plan* as a tuple

        P = (C, Δ, π, κ)

    where C is the cover, Δ is the change set, π is the replay strategy, and
    κ is the convergence criterion.  A plan is *admissible* iff every patch
    in C appears exactly once in either the changed or unchanged partition of
    Δ.

    §41.3 introduces *gluing under replay*, the stateful object that tracks
    which patches have been replayed, which are pending, and what the current
    overlap conditions look like.

    §41.4 defines the *incremental gluing* record that captures the diff
    between two consecutive gluing states.

    §41.5 specifies *convergence records* and their relation to the broader
    ConvergenceCertificate machinery of §38.

    copilot: models-marker

Usage::

    from jugeo.generation.replay_gluing.models import (
        ReplayGluingPlan,
        GluingUnderReplay,
        IncrementalGluing,
        ConvergenceRecord,
        ReplayStrategy,
        ReplayPhase,
    )
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enumerations
    "ReplayStrategy",
    "ReplayPhase",
    "PatchStatus",
    # Dataclasses
    "ReplayGluingPlan",
    "GluingUnderReplay",
    "IncrementalGluing",
    "ConvergenceRecord",
    "ReplayMetrics",
    "GluingDiff",
    # Constants
    "REPLAY_STRATEGY_COSTS",
    "DEFAULT_CONVERGENCE_THRESHOLD",
    "MAX_REPLAY_ROUNDS",
    # Helper functions
    "validate_plan_id",
    "compute_replay_cost",
    "merge_dependency_structures",
    "patch_set_difference",
    "format_convergence_history",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONVERGENCE_THRESHOLD: float = 1e-6
MAX_REPLAY_ROUNDS: int = 100
REPLAY_STRATEGY_COSTS: dict[str, float] = {
    "full": 1.0,
    "incremental": 0.5,
    "lazy": 0.2,
    "adaptive": 0.7,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def validate_plan_id(plan_id: str) -> bool:
    """Return True iff *plan_id* is a non-empty string, else raise ValueError."""
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError(f"Invalid plan_id: {plan_id!r}")
    return True


def compute_replay_cost(patches: frozenset[str], strategy: "ReplayStrategy") -> float:
    """Return a naive cost estimate for replaying *patches* under *strategy*."""
    multiplier = REPLAY_STRATEGY_COSTS.get(strategy.value, 1.0)
    return len(patches) * multiplier * 10.0


def merge_dependency_structures(
    d1: dict[str, frozenset[str]], d2: dict[str, frozenset[str]]
) -> dict[str, frozenset[str]]:
    """Merge two dependency dicts; per-key values (frozensets) are unioned."""
    result: dict[str, frozenset[str]] = dict(d1)
    for key, deps in d2.items():
        result[key] = result[key] | deps if key in result else deps
    return result


def patch_set_difference(a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
    """Return elements in *a* that are not in *b*."""
    return a - b


def format_convergence_history(history: list[tuple[int, float]]) -> str:
    """Return a multi-line human-readable rendering of convergence rounds."""
    return "\n".join(f"Round {r}: {e:.6f}" for r, e in history)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PatchStatus(str, Enum):
    """Per-patch processing status during a replay run."""

    PENDING = "pending"
    REPLAYED = "replayed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ReplayStrategy(str, Enum):
    """Strategy controlling how replay gluing processes patches.

    FULL
        All patches are re-replayed unconditionally.
    INCREMENTAL
        Only changed patches and their transitive dependents are replayed;
        unchanged patches reuse a cache.
    LAZY
        Only patches strictly required for the immediate change are replayed;
        others are deferred until explicitly requested.
    ADAPTIVE
        The algorithm dynamically switches between INCREMENTAL and LAZY
        based on the measured blast radius and available budget.
    """

    FULL = "full"
    INCREMENTAL = "incremental"
    LAZY = "lazy"
    ADAPTIVE = "adaptive"


class ReplayPhase(str, Enum):
    """Phase of the replay gluing state machine.

    Transitions::

        PENDING ──start──► PLANNING ──ready──► REPLAYING
        REPLAYING ──done──► VERIFYING ──pass──► COMPLETED
        REPLAYING ──fail──► FAILED
        VERIFYING ──fail──► FAILED
    """

    PENDING = "pending"
    PLANNING = "planning"
    REPLAYING = "replaying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ReplayGluingPlan:
    """A validated plan describing how to replay a gluing after a change set.

    Attributes
    ----------
    plan_id:
        Unique identifier assigned at creation time.
    strategy:
        Which :class:`ReplayStrategy` to use when executing the plan.
    changed_patches:
        Frozenset of patch names that have been modified or added.
    unchanged_patches:
        Frozenset of patch names that are structurally unchanged.
    removed_patches:
        Frozenset of patch names that no longer appear in the new goal.
    dependencies:
        Dict mapping each patch name to the frozenset of patches it depends
        on (i.e. patches that must be replayed before it).
    metadata:
        Arbitrary extra data stored by the planner for downstream use.
    created_at:
        Unix timestamp of plan creation.
    """

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy: ReplayStrategy = ReplayStrategy.INCREMENTAL
    changed_patches: frozenset[str] = field(default_factory=frozenset)
    unchanged_patches: frozenset[str] = field(default_factory=frozenset)
    removed_patches: frozenset[str] = field(default_factory=frozenset)
    dependencies: dict[str, frozenset[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def all_patches(self) -> frozenset[str]:
        """All patches that will be processed (changed ∪ unchanged)."""
        return self.changed_patches | self.unchanged_patches

    @property
    def total_patch_count(self) -> int:
        """Total number of patches in the plan."""
        return len(self.all_patches)

    def is_valid(self) -> bool:
        """Return True iff changed and unchanged sets are disjoint."""
        return len(self.changed_patches & self.unchanged_patches) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "strategy": self.strategy.value,
            "changed_patches": sorted(self.changed_patches),
            "unchanged_patches": sorted(self.unchanged_patches),
            "removed_patches": sorted(self.removed_patches),
            "dependencies": {k: sorted(v) for k, v in self.dependencies.items()},
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplayGluingPlan":
        """Reconstruct a plan from a dict produced by :meth:`to_dict`."""
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())),
            strategy=ReplayStrategy(data.get("strategy", ReplayStrategy.INCREMENTAL.value)),
            changed_patches=frozenset(data.get("changed_patches", [])),
            unchanged_patches=frozenset(data.get("unchanged_patches", [])),
            removed_patches=frozenset(data.get("removed_patches", [])),
            dependencies={
                k: frozenset(v) for k, v in data.get("dependencies", {}).items()
            },
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
        )

    def validate(self) -> None:
        """Raise ValueError for any invalid state."""
        overlap = self.changed_patches & self.unchanged_patches
        if overlap:
            raise ValueError(
                f"changed_patches and unchanged_patches overlap: {sorted(overlap)}"
            )

    def identify_affected_regions(self) -> frozenset[str]:
        """Return all patches transitively reachable from *changed_patches*
        via the dependency graph (i.e., everything that must be replayed)."""
        affected: set[str] = set(self.changed_patches)
        changed = True
        while changed:
            changed = False
            for patch, deps in self.dependencies.items():
                if patch not in affected and (set(deps) & affected):
                    affected.add(patch)
                    changed = True
        return frozenset(affected)

    def compute_replay_scope(self) -> dict[str, frozenset[str]]:
        """Return a dict classifying every patch as must-replay or can-skip."""
        affected = self.identify_affected_regions()
        return {
            "must_replay": affected,
            "can_skip": self.unchanged_patches - affected,
            "total": affected | self.unchanged_patches,
        }

    def optimize_for_cost(self) -> "ReplayStrategy":
        """Suggest the most cost-effective strategy given metadata cost hint."""
        cost_hint = float(self.metadata.get("estimated_cost", len(self.changed_patches) * 10))
        if cost_hint > 100.0:
            return ReplayStrategy.FULL
        if cost_hint >= 10.0:
            return ReplayStrategy.INCREMENTAL
        return ReplayStrategy.FULL  # trivially cheap; just replay everything

    def summary_string(self) -> str:
        return (
            f"ReplayGluingPlan({self.plan_id[:8]}…, "
            f"changed={len(self.changed_patches)}, "
            f"strategy={self.strategy.value})"
        )

    def __repr__(self) -> str:  # pragma: no cover
        return self.summary_string()


@dataclass
class GluingUnderReplay:
    """Mutable state of an in-progress replay gluing run.

    Attributes
    ----------
    gluing_id:
        Unique run identifier.
    plan:
        The :class:`ReplayGluingPlan` driving this run.
    phase:
        Current phase in the replay state machine.
    replayed_patches:
        Patches that have already been replayed in this run.
    pending_patches:
        Patches that still need to be replayed.
    deferred_patches:
        Patches whose replay has been intentionally deferred (LAZY strategy).
    patch_sections:
        Mapping from patch name to its current section data (arbitrary dict).
    overlaps:
        Mapping from ``"patch_a:patch_b"`` overlap key to overlap data dict.
    error_log:
        List of error/warning strings accumulated during the run.
    started_at:
        Unix timestamp when the run was started.
    completed_at:
        Unix timestamp when the run finished (0.0 if still running).
    """

    gluing_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan: ReplayGluingPlan = field(default_factory=ReplayGluingPlan)
    phase: ReplayPhase = ReplayPhase.PENDING
    replayed_patches: list[str] = field(default_factory=list)
    pending_patches: list[str] = field(default_factory=list)
    deferred_patches: list[str] = field(default_factory=list)
    patch_sections: dict[str, Any] = field(default_factory=dict)
    overlaps: dict[str, Any] = field(default_factory=dict)
    error_log: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def transition(self, phase: ReplayPhase) -> None:
        """Advance to *phase*; records timestamp when reaching COMPLETED."""
        self.phase = phase
        if phase in (ReplayPhase.COMPLETED, ReplayPhase.FAILED):
            self.completed_at = time.time()

    def mark_replayed(self, patch: str, section_data: dict[str, Any]) -> None:
        """Record that *patch* has been successfully replayed."""
        if patch in self.pending_patches:
            self.pending_patches.remove(patch)
        if patch not in self.replayed_patches:
            self.replayed_patches.append(patch)
        self.patch_sections[patch] = section_data

    def add_overlap(self, key: str, overlap_data: dict[str, Any]) -> None:
        """Store or update an overlap condition."""
        self.overlaps[key] = overlap_data

    def log_error(self, msg: str) -> None:
        self.error_log.append(msg)

    # ------------------------------------------------------------------
    # Convenience aliases used by tests / higher-level callers
    # ------------------------------------------------------------------

    def start_phase(self, new_phase: "ReplayPhase") -> None:
        """Alias for :meth:`transition`."""
        self.transition(new_phase)

    def record_replay(self, patch: str, result: Any) -> None:
        """Record *patch* as replayed; wraps :meth:`mark_replayed`."""
        self.mark_replayed(patch, result if isinstance(result, dict) else {"result": result})

    def record_skip(self, patch: str) -> None:
        """Mark *patch* as deferred / skipped."""
        if patch in self.pending_patches:
            self.pending_patches.remove(patch)
        if patch not in self.deferred_patches:
            self.deferred_patches.append(patch)

    def finalize(self) -> None:
        """Transition to COMPLETED or FAILED based on *error_log*."""
        self.transition(ReplayPhase.FAILED if self.error_log else ReplayPhase.COMPLETED)

    def get_progress(self) -> dict[str, Any]:
        """Return a progress summary dict."""
        total = len(self.plan.all_patches)
        done = len(self.replayed_patches) + len(self.deferred_patches)
        return {
            "total": total,
            "done": done,
            "replayed": len(self.replayed_patches),
            "skipped": len(self.deferred_patches),
            "fraction": done / total if total > 0 else 0.0,
        }

    @property
    def is_complete(self) -> bool:
        return self.phase == ReplayPhase.COMPLETED

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at if self.completed_at else time.time()
        return end - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "gluing_id": self.gluing_id,
            "plan_id": self.plan.plan_id,
            "phase": self.phase.value,
            "replayed_patches": self.replayed_patches,
            "pending_patches": self.pending_patches,
            "deferred_patches": self.deferred_patches,
            "patch_sections": self.patch_sections,
            "overlaps": self.overlaps,
            "error_log": self.error_log,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class IncrementalGluing:
    """Diff record between two consecutive gluing states.

    Captures which patches changed, what their old and new section data
    looked like, and which overlap conditions were affected.

    Attributes
    ----------
    diff_id:
        Unique identifier for this diff.
    base_gluing_id:
        The :attr:`GluingUnderReplay.gluing_id` of the *before* state.
    target_gluing_id:
        The gluing ID of the *after* state.
    added_patches:
        Patches that appeared in the target but not the base.
    removed_patches:
        Patches that appeared in the base but not the target.
    modified_patches:
        Patches present in both with differing section data.
    overlap_diffs:
        Mapping from overlap key to ``{"before": ..., "after": ...}``.
    created_at:
        Unix timestamp.
    """

    diff_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    base_gluing_id: str = ""
    target_gluing_id: str = ""
    added_patches: list[str] = field(default_factory=list)
    removed_patches: list[str] = field(default_factory=list)
    modified_patches: list[str] = field(default_factory=list)
    overlap_diffs: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def total_changes(self) -> int:
        return len(self.added_patches) + len(self.removed_patches) + len(self.modified_patches)

    def is_empty(self) -> bool:
        return self.total_changes == 0 and not self.overlap_diffs

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff_id": self.diff_id,
            "base_gluing_id": self.base_gluing_id,
            "target_gluing_id": self.target_gluing_id,
            "added_patches": self.added_patches,
            "removed_patches": self.removed_patches,
            "modified_patches": self.modified_patches,
            "overlap_diffs": self.overlap_diffs,
            "created_at": self.created_at,
        }


@dataclass
class ConvergenceRecord:
    """Record of whether a replay gluing run achieved convergence.

    Attributes
    ----------
    record_id:
        Unique identifier.
    gluing_id:
        The :attr:`GluingUnderReplay.gluing_id` this record pertains to.
    converged:
        True iff all required patches were replayed and all overlap
        conditions were satisfied.
    rounds:
        Number of replay rounds that were performed.
    unresolved_patches:
        Patches that could not be successfully replayed.
    violation_messages:
        Human-readable descriptions of any convergence violations.
    score:
        A numeric convergence score in [0, 1]; 1.0 = fully converged.
    created_at:
        Unix timestamp.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    gluing_id: str = ""
    converged: bool = False
    rounds: int = 0
    unresolved_patches: list[str] = field(default_factory=list)
    violation_messages: list[str] = field(default_factory=list)
    score: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "gluing_id": self.gluing_id,
            "converged": self.converged,
            "rounds": self.rounds,
            "unresolved_patches": self.unresolved_patches,
            "violation_messages": self.violation_messages,
            "score": self.score,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Additional value objects
# ---------------------------------------------------------------------------


@dataclass
class ReplayMetrics:
    """Accumulates statistics during a replay run."""

    total_patches: int = 0
    replayed_patches: int = 0
    skipped_patches: int = 0
    total_cost: float = 0.0
    elapsed_seconds: float = 0.0

    @property
    def efficiency(self) -> float:
        if self.total_patches == 0:
            return 0.0
        return self.skipped_patches / self.total_patches

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_patches": self.total_patches,
            "replayed_patches": self.replayed_patches,
            "skipped_patches": self.skipped_patches,
            "total_cost": self.total_cost,
            "elapsed_seconds": self.elapsed_seconds,
            "efficiency": self.efficiency,
        }


@dataclass
class GluingDiff:
    """Compact summary of the structural difference between two gluing states."""

    base_id: str = ""
    target_id: str = ""
    added: frozenset[str] = field(default_factory=frozenset)
    removed: frozenset[str] = field(default_factory=frozenset)
    modified: frozenset[str] = field(default_factory=frozenset)
    created_at: float = field(default_factory=time.time)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "target_id": self.target_id,
            "added": sorted(self.added),
            "removed": sorted(self.removed),
            "modified": sorted(self.modified),
            "created_at": self.created_at,
        }
