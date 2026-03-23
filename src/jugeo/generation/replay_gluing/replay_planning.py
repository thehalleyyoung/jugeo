r"""Replay planning for the replay_gluing sub-package (Stage 1).

Theory (theory2.tex §41.2 — Replay Planning):
    Stage 1 of the replay gluing pipeline analyses the incoming change set
    and constructs an admissible :class:`ReplayGluingPlan`.  It answers three
    questions:

    1. *What changed?* — Partition the patch set into changed, unchanged,
       and removed subsets.
    2. *What depends on what?* — Build or update the dependency DAG so that
       downstream stages know which patches must be replayed before others.
    3. *Which strategy fits best?* — Choose a :class:`ReplayStrategy` based
       on the size of the blast radius relative to the total patch count.

    copilot: s01-marker

Usage::

    from jugeo.generation.replay_gluing.replay_planning import (
        ChangeSet, ReplayPlanner, DependencyAnalyzer
    )
    cs = ChangeSet(changed_patches=frozenset(["p1"]),
                   unchanged_patches=frozenset(["p2", "p3"]))
    planner = ReplayPlanner()
    plan = planner.plan(cs, prior_state={})
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.replay_gluing.models import (
    ReplayGluingPlan,
    ReplayStrategy,
)

__all__ = [
    "ChangeSet",
    "ReplayPlanner",
    "DependencyAnalyzer",
    "ReplayCostEstimator",
]


# ---------------------------------------------------------------------------
# ChangeSet
# ---------------------------------------------------------------------------


@dataclass
class ChangeSet:
    """Describes what changed between two successive goal snapshots.

    Attributes
    ----------
    changed_patches:
        Patches that were modified (content changed) or newly added.
    unchanged_patches:
        Patches whose content is identical to the prior state.
    removed_patches:
        Patches that no longer appear in the new goal.
    change_metadata:
        Optional per-patch extra data (e.g. change type strings).
    created_at:
        Unix timestamp.
    """

    changed_patches: frozenset[str] = field(default_factory=frozenset)
    unchanged_patches: frozenset[str] = field(default_factory=frozenset)
    removed_patches: frozenset[str] = field(default_factory=frozenset)
    change_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def all_patches(self) -> frozenset[str]:
        return self.changed_patches | self.unchanged_patches

    @property
    def change_ratio(self) -> float:
        total = len(self.all_patches)
        if total == 0:
            return 0.0
        raw_ratio = len(self.changed_patches) / total
        # The planner uses coarse ratio buckets rather than exact fractions.
        return min(1.0, round(raw_ratio * 4.0) / 4.0)

    def is_empty(self) -> bool:
        return not (self.changed_patches or self.removed_patches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_patches": sorted(self.changed_patches),
            "unchanged_patches": sorted(self.unchanged_patches),
            "removed_patches": sorted(self.removed_patches),
            "change_metadata": self.change_metadata,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# DependencyAnalyzer
# ---------------------------------------------------------------------------


class DependencyAnalyzer:
    """Analyses and maintains the patch dependency DAG.

    The DAG is represented as a dict mapping each patch name to the
    frozenset of patches it *directly* depends on.  A patch X depends on Y
    iff X's gluing condition references Y's section data.

    Parameters
    ----------
    prior_dependencies:
        An existing dependency dict to seed the analyzer with.  If *None*,
        starts from scratch.
    """

    def __init__(self, prior_dependencies: dict[str, frozenset[str]] | None = None) -> None:
        self._deps: dict[str, set[str]] = {}
        if prior_dependencies:
            for patch, deps in prior_dependencies.items():
                self._deps[patch] = set(deps)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, change_set: ChangeSet, prior_state: dict[str, Any]) -> dict[str, frozenset[str]]:
        """Build a full dependency dict for all patches in *change_set*.

        For each patch that appears in the prior state, any explicitly stored
        dependency information is preserved.  For new patches (not in prior
        state), an empty dependency set is assigned.

        Returns
        -------
        dict[str, frozenset[str]]
            Mapping patch → frozenset of patches it depends on.
        """
        result: dict[str, frozenset[str]] = {}
        all_patches = change_set.all_patches

        for patch in all_patches:
            if patch in self._deps:
                result[patch] = frozenset(self._deps[patch])
            elif patch in prior_state:
                stored = prior_state[patch].get("dependencies", []) if isinstance(prior_state[patch], dict) else []
                result[patch] = frozenset(stored)
                self._deps[patch] = set(stored)
            else:
                result[patch] = frozenset()
                self._deps[patch] = set()

        return result

    def add_dependency(self, patch: str, depends_on: str) -> None:
        """Record that *patch* directly depends on *depends_on*."""
        self._deps.setdefault(patch, set()).add(depends_on)

    def remove_dependency(self, patch: str, depends_on: str) -> None:
        if patch in self._deps:
            self._deps[patch].discard(depends_on)

    def get_dependents(self, patch: str) -> frozenset[str]:
        """Return all patches that directly depend on *patch*."""
        return frozenset(p for p, deps in self._deps.items() if patch in deps)

    def get_transitive_dependents(self, patch: str) -> frozenset[str]:
        """BFS to collect all transitive dependents of *patch*."""
        visited: set[str] = set()
        queue = [patch]
        while queue:
            current = queue.pop()
            for p, deps in self._deps.items():
                if current in deps and p not in visited:
                    visited.add(p)
                    queue.append(p)
        visited.discard(patch)
        return frozenset(visited)

    def topological_order(self, patches: frozenset[str]) -> list[str]:
        """Return a valid topological ordering of *patches*.

        Patches with no dependencies come first.  Ties are broken
        alphabetically for determinism.
        """
        in_degree: dict[str, int] = {p: 0 for p in patches}
        for p in patches:
            for dep in self._deps.get(p, set()):
                if dep in patches:
                    in_degree[p] += 1

        ready = sorted(p for p, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while ready:
            node = ready.pop(0)
            order.append(node)
            for p in sorted(patches):
                if node in self._deps.get(p, set()):
                    in_degree[p] -= 1
                    if in_degree[p] == 0:
                        ready.append(p)
                        ready.sort()

        # Append any remaining patches that might have been missed
        for p in sorted(patches):
            if p not in order:
                order.append(p)

        return order

    def snapshot(self) -> dict[str, frozenset[str]]:
        return {p: frozenset(deps) for p, deps in self._deps.items()}


# ---------------------------------------------------------------------------
# ReplayPlanner
# ---------------------------------------------------------------------------


class ReplayPlanner:
    """Constructs a :class:`ReplayGluingPlan` from a :class:`ChangeSet`.

    The planner performs three steps:

    1. Validate the change set for internal consistency.
    2. Invoke a :class:`DependencyAnalyzer` to build the dependency DAG.
    3. Choose a :class:`ReplayStrategy` based on the change ratio.

    Parameters
    ----------
    dependency_analyzer:
        Optional pre-seeded analyzer.  A fresh one is created if *None*.
    strategy_override:
        If provided, forces this strategy regardless of heuristics.
    """

    # Thresholds for automatic strategy selection
    _INCREMENTAL_THRESHOLD = 0.5   # < 50% changed → incremental
    _LAZY_THRESHOLD = 0.15         # < 15% changed → lazy

    def __init__(
        self,
        dependency_analyzer: DependencyAnalyzer | None = None,
        strategy_override: ReplayStrategy | None = None,
    ) -> None:
        self._analyzer = dependency_analyzer or DependencyAnalyzer()
        self._strategy_override = strategy_override

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, change_set: ChangeSet, prior_state: dict[str, Any]) -> ReplayGluingPlan:
        """Build and return a :class:`ReplayGluingPlan`.

        Parameters
        ----------
        change_set:
            Describes what changed since the last gluing.
        prior_state:
            The prior gluing dict; used to seed dependency information.

        Returns
        -------
        ReplayGluingPlan
            A validated, ready-to-execute plan.
        """
        strategy = self._choose_strategy(change_set)
        dependencies = self._analyzer.analyze(change_set, prior_state)

        return ReplayGluingPlan(
            plan_id=str(uuid.uuid4()),
            strategy=strategy,
            changed_patches=change_set.changed_patches,
            unchanged_patches=change_set.unchanged_patches,
            removed_patches=change_set.removed_patches,
            dependencies=dependencies,
            metadata={
                "change_ratio": change_set.change_ratio,
                "planned_at": time.time(),
            },
        )

    def validate(self, change_set: ChangeSet) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: list[str] = []
        overlap = change_set.changed_patches & change_set.unchanged_patches
        if overlap:
            errors.append(f"Patches appear in both changed and unchanged: {sorted(overlap)}")
        removed_and_present = change_set.removed_patches & change_set.all_patches
        if removed_and_present:
            errors.append(f"Removed patches still in changed/unchanged: {sorted(removed_and_present)}")
        return errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _choose_strategy(self, change_set: ChangeSet) -> ReplayStrategy:
        if self._strategy_override is not None:
            return self._strategy_override
        ratio = change_set.change_ratio
        if ratio >= 1.0:
            return ReplayStrategy.FULL
        if ratio < self._LAZY_THRESHOLD:
            return ReplayStrategy.LAZY
        if ratio < self._INCREMENTAL_THRESHOLD:
            return ReplayStrategy.INCREMENTAL
        return ReplayStrategy.ADAPTIVE


# ---------------------------------------------------------------------------
# ReplayCostEstimator
# ---------------------------------------------------------------------------


class ReplayCostEstimator:
    """Estimates the cost of a replay operation.

    Parameters
    ----------
    base_cost_per_patch:
        Cost unit charged per patch that must be replayed.
    overhead:
        Fixed overhead charged regardless of patch count.
    """

    def __init__(
        self,
        base_cost_per_patch: float = 10.0,
        overhead: float = 5.0,
    ) -> None:
        self.base_cost_per_patch = base_cost_per_patch
        self.overhead = overhead

    def estimate(self, change_set: ChangeSet, dependency_graph: dict | None = None) -> float:
        """Return a cost estimate for replaying *change_set*.

        If *dependency_graph* is supplied the transitive blast radius is
        used instead of just the directly-changed count.
        """
        if dependency_graph is None:
            affected_count = len(change_set.changed_patches)
        else:
            affected: set[str] = set(change_set.changed_patches)
            # Propagate through the dependency graph
            changed = True
            while changed:
                changed = False
                for patch, deps in dependency_graph.items():
                    if patch not in affected and (set(deps) & affected):
                        affected.add(patch)
                        changed = True
            affected_count = len(affected)

        return self.overhead + affected_count * self.base_cost_per_patch

    def estimate_savings(self, change_set: ChangeSet) -> float:
        """Return the cost saved by using INCREMENTAL instead of FULL replay."""
        full_cost = self.estimate(change_set)
        incremental_cost = self.estimate(
            ChangeSet(
                changed_patches=change_set.changed_patches,
                unchanged_patches=frozenset(),
                removed_patches=change_set.removed_patches,
            )
        )
        return max(0.0, full_cost - incremental_cost)


@dataclass(frozen=True, slots=True)
class ReplayPlanWitness:
    """Immutable record capturing the outcome of a single replay planning run.

    A witness ties together the plan identity, the strategy chosen, patch
    counts, estimated cost, and an ordered provenance chain so that the full
    decision trail can be reconstructed later.
    """

    witness_id: str
    plan_id: str
    strategy: str
    changed_count: int
    unchanged_count: int
    estimated_cost: float
    provenance: tuple[str, ...]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize the witness to a plain dictionary suitable for JSON encoding."""
        return {
            "witness_id": self.witness_id,
            "plan_id": self.plan_id,
            "strategy": self.strategy,
            "changed_count": self.changed_count,
            "unchanged_count": self.unchanged_count,
            "estimated_cost": self.estimated_cost,
            "provenance": list(self.provenance),
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a concise human-readable one-liner describing this witness."""
        total = self.changed_count + self.unchanged_count
        return (
            f"[{self.witness_id[:8]}] plan={self.plan_id[:8]} "
            f"strategy={self.strategy} patches={total} "
            f"(changed={self.changed_count}, unchanged={self.unchanged_count}) "
            f"cost={self.estimated_cost:.3f}"
        )


class ReplayPlanAnalyzer:
    """Analyzes replay plans and change sets to surface actionable metrics.

    This class is intentionally stateless: every method derives its result
    purely from the arguments passed to it so that instances can be reused
    freely across different change sets.
    """

    def analyze(self, change_set: ChangeSet, plan: object) -> dict[str, Any]:
        """Return a structured analysis of *change_set* in the context of *plan*.

        Keys in the returned dict:
        - ``patch_counts``         – mapping of ``changed`` / ``unchanged`` / ``total``
        - ``change_ratio``         – fraction of patches that changed (0.0–1.0)
        - ``blast_radius_estimate``– conservative upper bound on transitively
                                     affected patches (uses empty dep graph)
        - ``recommended_strategy`` – ``"INCREMENTAL"`` or ``"FULL"``
        - ``efficiency_score``     – 0.0–1.0 (higher is more incremental)
        """
        changed = len(change_set.changed_patches)
        unchanged = len(change_set.unchanged_patches)
        total = changed + unchanged
        change_ratio = changed / total if total > 0 else 0.0
        blast_radius = self.estimate_blast_radius(change_set, {})
        efficiency = self.compute_efficiency_score(change_set)
        recommended = "INCREMENTAL" if efficiency >= 0.5 else "FULL"
        return {
            "patch_counts": {"changed": changed, "unchanged": unchanged, "total": total},
            "change_ratio": change_ratio,
            "blast_radius_estimate": blast_radius,
            "recommended_strategy": recommended,
            "efficiency_score": efficiency,
        }

    def score(self, change_set: ChangeSet) -> float:
        """Return an efficiency score between 0.0 and 1.0.

        1.0 means the replay is fully incremental (no unchanged patches need
        re-execution).  0.0 means every patch must be replayed in full.
        """
        return self.compute_efficiency_score(change_set)

    def report(self, change_set: ChangeSet, plan: object) -> str:
        """Build and return a human-readable multi-line analysis report."""
        data = self.analyze(change_set, plan)
        counts = data["patch_counts"]
        lines = [
            "=== ReplayPlanAnalyzer Report ===",
            f"  Total patches      : {counts['total']}",
            f"  Changed patches    : {counts['changed']}",
            f"  Unchanged patches  : {counts['unchanged']}",
            f"  Change ratio       : {data['change_ratio']:.2%}",
            f"  Blast radius (est) : {data['blast_radius_estimate']}",
            f"  Efficiency score   : {data['efficiency_score']:.4f}",
            f"  Recommended strat  : {data['recommended_strategy']}",
            "=================================",
        ]
        return "\n".join(lines)

    def estimate_blast_radius(self, change_set: ChangeSet, deps: dict) -> int:
        """BFS over *deps* to count transitively affected patches.

        Args:
            change_set: The set of changed / unchanged patches.
            deps: Adjacency dict mapping patch name → iterable of dependents.
                  An empty dict means no cross-patch dependencies are known.

        Returns:
            Total number of patches reachable from the initial changed set.
        """
        frontier: set[str] = set(change_set.changed_patches)
        visited: set[str] = set(frontier)
        queue = list(frontier)
        while queue:
            current = queue.pop()
            for dependent in deps.get(current, []):
                if dependent not in visited:
                    visited.add(dependent)
                    queue.append(dependent)
        return len(visited)

    def compute_efficiency_score(self, change_set: ChangeSet) -> float:
        """Derive an efficiency score from the ratio of unchanged to total patches.

        The score is defined as ``unchanged / total`` when *total* > 0, so a
        change set where nothing changed scores 1.0 (fully reusable) and one
        where everything changed scores 0.0 (full replay required).
        """
        changed = len(change_set.changed_patches)
        unchanged = len(change_set.unchanged_patches)
        total = changed + unchanged
        if total == 0:
            return 1.0
        return unchanged / total


class ReplayPlanCoordinator:
    """Orchestrates the full replay-planning pipeline end-to-end.

    The coordinator owns a :class:`ReplayPlanner` instance, delegates planning
    to it, runs the :class:`ReplayPlanAnalyzer` for post-plan analysis, and
    records each run as a :class:`ReplayPlanWitness` in an internal history
    list so that callers can audit past decisions.
    """

    def __init__(self) -> None:
        self._planner = ReplayPlanner()
        self._analyzer = ReplayPlanAnalyzer()
        self._history: list[ReplayPlanWitness] = []

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def run(self, change_set: ChangeSet, prior_state: dict) -> ReplayPlanWitness:
        """Execute validate → plan → analyze → witness and return the witness.

        Args:
            change_set:  The set of patches that changed / stayed the same.
            prior_state: Arbitrary dict of prior coordinator or planner state
                         (e.g. previous cost estimates, dependency snapshots).

        Returns:
            A frozen :class:`ReplayPlanWitness` describing the outcome.

        Raises:
            ValueError: If :meth:`validate` returns any errors.
        """
        errors = self.validate(change_set)
        if errors:
            raise ValueError(f"Invalid change set: {'; '.join(errors)}")

        plan = self.plan(change_set, prior_state)
        analysis = self._analyzer.analyze(change_set, plan)

        estimator = ReplayCostEstimator()
        cost = estimator.estimate(change_set)

        witness = ReplayPlanWitness(
            witness_id=str(uuid.uuid4()),
            plan_id=str(uuid.uuid4()),
            strategy=analysis["recommended_strategy"],
            changed_count=len(change_set.changed_patches),
            unchanged_count=len(change_set.unchanged_patches),
            estimated_cost=cost,
            provenance=("coordinator.run", analysis["recommended_strategy"]),
            timestamp=time.time(),
        )
        self.record_witness(witness)
        return witness

    # ------------------------------------------------------------------
    # Supporting methods
    # ------------------------------------------------------------------

    def validate(self, change_set: ChangeSet) -> list[str]:
        """Return a list of validation error strings for *change_set*.

        An empty list means the change set is valid.  Checks performed:
        - Changed and unchanged sets must not overlap.
        - Patch identifiers must be non-empty strings.
        """
        errors: list[str] = []
        overlap = set(change_set.changed_patches) & set(change_set.unchanged_patches)
        if overlap:
            errors.append(f"Patches appear in both changed and unchanged: {sorted(overlap)}")
        for patch in change_set.changed_patches | change_set.unchanged_patches:
            if not isinstance(patch, str) or not patch.strip():
                errors.append(f"Invalid patch identifier: {patch!r}")
        return errors

    def plan(self, change_set: ChangeSet, prior_state: dict) -> object:
        """Delegate to the internal :class:`ReplayPlanner` and return the plan."""
        return self._planner.plan(change_set, prior_state)

    def record_witness(self, witness: ReplayPlanWitness) -> None:
        """Append *witness* to the internal history list."""
        self._history.append(witness)

    def get_history(self) -> list[ReplayPlanWitness]:
        """Return a shallow copy of the witness history list."""
        return list(self._history)

    def reset(self) -> None:
        """Clear all recorded witnesses from the history."""
        self._history.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize coordinator state (history) to a plain dictionary."""
        return {
            "history": [w.to_dict() for w in self._history],
            "history_length": len(self._history),
        }

    def summarize(self) -> str:
        """Return a human-readable summary of coordinator state."""
        n = len(self._history)
        if n == 0:
            return "ReplayPlanCoordinator: no runs recorded."
        last = self._history[-1]
        return (
            f"ReplayPlanCoordinator: {n} run(s) recorded. "
            f"Last run → {last.summary()}"
        )


if __name__ == "__main__":
    # Smoke test: exercise the coordinator and analyzer end-to-end.
    _cs = ChangeSet(
        changed_patches=frozenset({"patch_alpha", "patch_beta", "patch_gamma"}),
        unchanged_patches=frozenset({"patch_delta", "patch_epsilon"}),
        removed_patches=frozenset(),
    )

    _coordinator = ReplayPlanCoordinator()
    _witness = _coordinator.run(_cs, prior_state={})
    print("Witness dict:")
    import pprint
    pprint.pprint(_witness.to_dict())

    _analyzer = ReplayPlanAnalyzer()
    print()
    print(_analyzer.report(_cs, plan=None))

    print()
    print("smoke test passed")
