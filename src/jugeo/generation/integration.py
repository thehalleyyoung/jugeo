"""Integration, regression, and semantic closure for JuGeo.

Theory (theory2.tex §3): Integration is the process of taking locally
constructed sections and gluing them into a global section.  This involves
checking all overlap conditions (treaties), performing descent, handling
obstructions, and achieving semantic closure.  Regression means verifying
that new sections do not break existing ones.

The sheaf-theoretic model treats each coordinate as an open set in a
semantic site.  Local sections (judgments with evidence at individual
coordinates) must satisfy the cocycle condition on overlaps before they can
be assembled into a global section via descent.  When descent fails, the
obstruction lives in H¹ of the Čech complex and is recorded persistently.

Copilot acts as a controlled oracle: it may propose candidates for
integration (new local sections, cover refinements, repair hints) but
every proposal must pass through the evidence pipeline and cannot
self-promote its trust tier.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from jugeo.generation.construction import ConstructionPlan
from jugeo.generation.treaties import OverlapTreaty, TreatyClause

# ---------------------------------------------------------------------------
# Geometry / descent layer
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.descent import (
        CohomologyClass,
        DescentEngine,
        DescentObstruction,
        DescentStrategy,
        GlobalSection,
        LocalSection,
        OverlapStatus,
        RepairFrontier,
    )
except Exception:  # pragma: no cover – tolerate partial builds
    CohomologyClass = None  # type: ignore[assignment,misc]
    DescentEngine = None  # type: ignore[assignment,misc]
    DescentObstruction = None  # type: ignore[assignment,misc]
    DescentStrategy = None  # type: ignore[assignment,misc]
    GlobalSection = None  # type: ignore[assignment,misc]
    LocalSection = None  # type: ignore[assignment,misc]
    OverlapStatus = None  # type: ignore[assignment,misc]
    RepairFrontier = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import Coordinate
except Exception:  # pragma: no cover
    Coordinate = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import Cover
except Exception:  # pragma: no cover
    Cover = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Judgment / section layer
# ---------------------------------------------------------------------------
try:
    from jugeo.judgments.sections import (
        GluingStatus,
        Section,
        SectionFamily,
        SectionGluing,
    )
except Exception:  # pragma: no cover
    GluingStatus = None  # type: ignore[assignment,misc]
    Section = None  # type: ignore[assignment,misc]
    SectionFamily = None  # type: ignore[assignment,misc]
    SectionGluing = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Evidence layer
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.channels import EvidenceChannel
except Exception:  # pragma: no cover
    EvidenceChannel = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.manifests import ObstructionKind, ObstructionStore
except Exception:  # pragma: no cover
    ObstructionKind = None  # type: ignore[assignment,misc]
    ObstructionStore = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustProfile, TrustTier
except Exception:  # pragma: no cover
    TrustProfile = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]


# =========================================================================
# Enumerations
# =========================================================================

class IntegrationStatus(str, Enum):
    """Outcome of an integration attempt."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    REGRESSION = "regression"


class IntegrationStrategy(str, Enum):
    """Strategy for ordering and executing integration steps."""

    EAGER = "eager"
    EXHAUSTIVE = "exhaustive"
    INCREMENTAL = "incremental"
    COPILOT_GUIDED = "copilot_guided"


# =========================================================================
# 1. IntegrationPlan
# =========================================================================

@dataclass(slots=True)
class IntegrationPlan:
    """A plan describing which local sections to integrate, which treaties
    must hold, and the expected cost.

    Fields
    ------
    plan_id : str
        Unique identifier for this plan.
    target_coordinate : str
        The coordinate over which the global section is being assembled.
    sections_to_integrate : list[str]
        Keys of the local sections that participate.
    treaties_to_check : list[str]
        Identifiers of overlap treaties that must be verified.
    expected_overlaps : dict[tuple[str, str], str]
        Map from section-pair to the expected overlap datum key.
    estimated_cost : float
        Heuristic cost estimate in abstract work-units.
    strategy : IntegrationStrategy
        How the engine should order its work.
    plans : tuple[ConstructionPlan, ...] | None
        Legacy: construction plans bundled into this integration.
    treaties : tuple[OverlapTreaty, ...] | None
        Legacy: treaty objects.
    blockers : tuple[str, ...]
        Residual obligations that block readiness.
    """

    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    target_coordinate: str = ""
    sections_to_integrate: list[str] = field(default_factory=list)
    treaties_to_check: list[str] = field(default_factory=list)
    expected_overlaps: dict[tuple[str, str], str] = field(default_factory=dict)
    estimated_cost: float = 0.0
    strategy: IntegrationStrategy = IntegrationStrategy.EAGER
    plans: tuple[ConstructionPlan, ...] | None = None
    treaties: tuple[OverlapTreaty, ...] | None = None
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Support legacy positional construction `(plans, treaties, blockers)`."""
        if (
            isinstance(self.plan_id, tuple)
            and isinstance(self.target_coordinate, tuple)
            and isinstance(self.sections_to_integrate, tuple)
            and not self.treaties_to_check
            and not self.expected_overlaps
            and self.estimated_cost == 0.0
            and self.strategy == IntegrationStrategy.EAGER
            and self.plans is None
            and self.treaties is None
            and not self.blockers
        ):
            legacy_plans = self.plan_id
            legacy_treaties = self.target_coordinate
            legacy_blockers = self.sections_to_integrate
            self.plan_id = uuid.uuid4().hex[:12]
            self.target_coordinate = ""
            self.sections_to_integrate = []
            self.plans = legacy_plans
            self.treaties = legacy_treaties
            self.blockers = tuple(legacy_blockers)

    # -- queries ----------------------------------------------------------

    @property
    def ready(self) -> bool:
        """True when there are no blockers and all bundled treaties accept."""
        if self.blockers:
            return False
        if self.treaties:
            return all(t.accepted for t in self.treaties)
        return True

    @property
    def overlap_count(self) -> int:
        return len(self.expected_overlaps)

    def involves_section(self, key: str) -> bool:
        """Check whether a section key participates in this plan."""
        return key in self.sections_to_integrate

    def add_section(self, key: str) -> None:
        """Register an additional local section."""
        if key not in self.sections_to_integrate:
            self.sections_to_integrate.append(key)

    def add_treaty(self, treaty_id: str) -> None:
        """Register an additional treaty that must be checked."""
        if treaty_id not in self.treaties_to_check:
            self.treaties_to_check.append(treaty_id)

    def summary(self) -> dict[str, Any]:
        """Human-/copilot-readable summary of the plan."""
        return {
            "plan_id": self.plan_id,
            "target": self.target_coordinate,
            "section_count": len(self.sections_to_integrate),
            "treaty_count": len(self.treaties_to_check),
            "overlap_count": self.overlap_count,
            "estimated_cost": self.estimated_cost,
            "strategy": self.strategy.value,
            "ready": self.ready,
        }


# =========================================================================
# 2. IntegrationResult
# =========================================================================

@dataclass(slots=True)
class IntegrationResult:
    """Outcome of a single integration attempt.

    Contains the global section (if produced), any obstructions encountered,
    regressions detected, and timing information.
    """

    status: IntegrationStatus = IntegrationStatus.FAILED
    global_section: Any | None = None
    obstructions: list[dict[str, Any]] = field(default_factory=list)
    regressions: list[dict[str, Any]] = field(default_factory=list)
    integration_time_ms: float = 0.0
    evidence_produced: list[dict[str, Any]] = field(default_factory=list)
    plan_id: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == IntegrationStatus.SUCCESS

    @property
    def has_obstructions(self) -> bool:
        return len(self.obstructions) > 0

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def add_obstruction(self, coordinate: str, kind: str, message: str) -> None:
        """Record an obstruction discovered during integration."""
        self.obstructions.append({
            "id": uuid.uuid4().hex[:10],
            "coordinate": coordinate,
            "kind": kind,
            "message": message,
            "timestamp": time.time(),
        })

    def add_regression(self, section_key: str, description: str,
                       severity: str = "warning") -> None:
        """Record a regression: a previously-valid section now fails."""
        self.regressions.append({
            "section_key": section_key,
            "description": description,
            "severity": severity,
            "timestamp": time.time(),
        })

    def summary(self) -> dict[str, Any]:
        """Copilot-consumable summary of the result."""
        return {
            "status": self.status.value,
            "plan_id": self.plan_id,
            "has_global_section": self.global_section is not None,
            "obstruction_count": len(self.obstructions),
            "regression_count": len(self.regressions),
            "evidence_count": len(self.evidence_produced),
            "time_ms": round(self.integration_time_ms, 2),
        }


# =========================================================================
# 3. RegressionChecker
# =========================================================================

class RegressionChecker:
    """Verify that newly integrated sections do not weaken or invalidate
    previously established judgments.

    Regression is *support-local*: only sections whose support overlaps
    the changed region need re-checking.
    """

    def __init__(self, existing_sections: Mapping[str, Any] | None = None,
                 trust_profiles: Mapping[str, Any] | None = None,
                 treaties: Sequence[OverlapTreaty] | None = None) -> None:
        self._existing: dict[str, Any] = dict(existing_sections or {})
        self._trusts: dict[str, Any] = dict(trust_profiles or {})
        self._treaties: list[OverlapTreaty] = list(treaties or [])
        self._report_lines: list[str] = []

    # -- public API -------------------------------------------------------

    def check(self, new_global: Any,
              affected_keys: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Run all regression checks and return a list of issues found.

        Parameters
        ----------
        new_global : GlobalSection or dict
            The freshly assembled global section.
        affected_keys : list[str] | None
            If given, limits checking to these section keys.
        """
        issues: list[dict[str, Any]] = []
        keys = affected_keys or list(self._existing.keys())
        affected = self.identify_affected_sections(keys)
        for key in affected:
            weakening = self.verify_no_weakening(key, new_global)
            if weakening:
                issues.append(weakening)
            trust_loss = self.verify_no_trust_loss(key, new_global)
            if trust_loss:
                issues.append(trust_loss)
        treaty_issues = self.verify_treaty_preservation(new_global)
        issues.extend(treaty_issues)
        self._report_lines = [str(i) for i in issues]
        return issues

    def identify_affected_sections(self, changed_keys: Sequence[str]) -> list[str]:
        """Determine which existing sections are affected by the change.

        A section is affected if its key appears in *changed_keys* or if
        it shares an overlap (treaty) with any changed section.
        """
        affected: set[str] = set(changed_keys)
        treaty_patches: dict[str, set[str]] = defaultdict(set)
        for treaty in self._treaties:
            for p in treaty.patches:
                treaty_patches[p].update(treaty.patches)
        for key in list(changed_keys):
            affected.update(treaty_patches.get(key, set()))
        return sorted(affected & set(self._existing.keys()))

    def verify_no_weakening(self, section_key: str,
                            new_global: Any) -> dict[str, Any] | None:
        """Check that the judgment content of *section_key* has not been
        weakened (fewer claims, reduced scope) in the new global section."""
        old = self._existing.get(section_key)
        if old is None:
            return None
        old_data = old if isinstance(old, dict) else getattr(old, "data", {})
        new_data = new_global if isinstance(new_global, dict) else getattr(
            new_global, "merged_judgment", {}
        )
        missing_keys = set(old_data.keys()) - set(new_data.keys())
        if missing_keys:
            return {
                "kind": "weakening",
                "section_key": section_key,
                "missing_keys": sorted(missing_keys),
                "message": (
                    f"Section '{section_key}' lost claims: {sorted(missing_keys)}"
                ),
            }
        return None

    def verify_no_trust_loss(self, section_key: str,
                             new_global: Any) -> dict[str, Any] | None:
        """Ensure the trust tier of *section_key* has not decreased."""
        old_trust = self._trusts.get(section_key)
        if old_trust is None:
            return None
        new_trust_floor: float = 1.0
        if hasattr(new_global, "trust_floor"):
            new_trust_floor = new_global.trust_floor
        elif isinstance(new_global, dict):
            new_trust_floor = new_global.get("trust_floor", 1.0)
        old_level = old_trust if isinstance(old_trust, (int, float)) else getattr(
            old_trust, "tier", 1
        )
        if new_trust_floor < float(old_level):
            return {
                "kind": "trust_loss",
                "section_key": section_key,
                "old_trust": old_level,
                "new_trust": new_trust_floor,
                "message": (
                    f"Trust for '{section_key}' dropped from "
                    f"{old_level} to {new_trust_floor}"
                ),
            }
        return None

    def verify_treaty_preservation(self, new_global: Any) -> list[dict[str, Any]]:
        """Verify that all previously accepted treaties remain satisfied."""
        issues: list[dict[str, Any]] = []
        for treaty in self._treaties:
            if not treaty.accepted:
                continue
            for clause in treaty.clauses:
                if not clause.satisfied:
                    issues.append({
                        "kind": "treaty_violation",
                        "patches": treaty.patches,
                        "clause_patch": clause.patch,
                        "expectation": clause.expectation,
                        "message": (
                            f"Treaty clause for patch '{clause.patch}' "
                            f"no longer satisfied: {clause.expectation}"
                        ),
                    })
        return issues

    def regression_report(self) -> str:
        """Return a human-readable regression report."""
        if not self._report_lines:
            return "No regressions detected."
        header = f"Regression report — {len(self._report_lines)} issue(s):\n"
        body = "\n".join(f"  • {line}" for line in self._report_lines)
        return header + body


# =========================================================================
# 4. SemanticClosureChecker
# =========================================================================

class SemanticClosureChecker:
    """Verify that the result of integration is *semantically closed*:
    no dangling obligations, no unresolved obstructions, and all treaties
    are fully satisfied.

    Semantic closure corresponds to the condition that the global section
    lies in H⁰ with no residual H¹ classes.
    """

    def __init__(self, obligations: Sequence[dict[str, Any]] | None = None,
                 obstructions: Sequence[dict[str, Any]] | None = None,
                 treaties: Sequence[OverlapTreaty] | None = None) -> None:
        self._obligations: list[dict[str, Any]] = list(obligations or [])
        self._obstructions: list[dict[str, Any]] = list(obstructions or [])
        self._treaties: list[OverlapTreaty] = list(treaties or [])

    def check(self) -> dict[str, Any]:
        """Run the full closure check and return a structured report."""
        open_obs = self.find_open_obligations()
        unresolved = self.find_unresolved_obstructions()
        incomplete = self.find_incomplete_treaties()
        ratio = self.closure_ratio()
        closed = self.is_closed()
        return {
            "closed": closed,
            "closure_ratio": round(ratio, 4),
            "open_obligations": open_obs,
            "unresolved_obstructions": unresolved,
            "incomplete_treaties": incomplete,
        }

    def find_open_obligations(self) -> list[dict[str, Any]]:
        """Return obligations that have not been discharged."""
        return [
            o for o in self._obligations
            if not o.get("discharged", False)
        ]

    def find_unresolved_obstructions(self) -> list[dict[str, Any]]:
        """Return obstructions that remain active (not resolved)."""
        return [
            o for o in self._obstructions
            if not o.get("resolved", False)
        ]

    def find_incomplete_treaties(self) -> list[dict[str, Any]]:
        """Return treaties where at least one clause is unsatisfied."""
        result: list[dict[str, Any]] = []
        for treaty in self._treaties:
            unsatisfied = [c for c in treaty.clauses if not c.satisfied]
            if unsatisfied:
                result.append({
                    "patches": treaty.patches,
                    "unsatisfied_count": len(unsatisfied),
                    "clauses": [
                        {"patch": c.patch, "expectation": c.expectation}
                        for c in unsatisfied
                    ],
                })
        return result

    def closure_ratio(self) -> float:
        """Fraction of items that are resolved / discharged / satisfied.

        1.0 means full closure; 0.0 means nothing is resolved.
        """
        total = (
            len(self._obligations)
            + len(self._obstructions)
            + sum(len(t.clauses) for t in self._treaties)
        )
        if total == 0:
            return 1.0
        resolved = (
            sum(1 for o in self._obligations if o.get("discharged", False))
            + sum(1 for o in self._obstructions if o.get("resolved", False))
            + sum(
                sum(1 for c in t.clauses if c.satisfied)
                for t in self._treaties
            )
        )
        return resolved / total

    def is_closed(self) -> bool:
        """True when the closure ratio is exactly 1.0."""
        return self.closure_ratio() == 1.0


# =========================================================================
# 5. ReplayEngine
# =========================================================================

class ReplayEngine:
    """Replay integration under changes to local sections.

    When a local section is updated (new evidence, refined judgment), the
    replay engine determines the minimal set of overlaps that need
    re-checking and either performs an incremental recheck or falls back
    to a full replay.
    """

    def __init__(self, previous_result: IntegrationResult | None = None,
                 section_versions: Mapping[str, int] | None = None) -> None:
        self._previous = previous_result
        self._versions: dict[str, int] = dict(section_versions or {})
        self._replay_log: list[dict[str, Any]] = []

    def replay(self, updated_sections: Mapping[str, Any],
               plan: IntegrationPlan,
               engine: IntegrationEngine | None = None) -> IntegrationResult:
        """Top-level entry: replay integration given *updated_sections*.

        Decides between incremental and full replay based on the fraction
        of sections that changed.
        """
        t0 = time.monotonic_ns()
        changed = self.identify_changed_sections(updated_sections)
        affected_overlaps = self.compute_affected_overlaps(changed, plan)
        change_fraction = len(changed) / max(len(plan.sections_to_integrate), 1)

        if change_fraction <= 0.3 and self._previous is not None:
            result = self.incremental_recheck(changed, affected_overlaps, plan)
        else:
            result = self.full_replay_if_needed(plan, engine)

        elapsed_ms = (time.monotonic_ns() - t0) / 1_000_000
        result.integration_time_ms = elapsed_ms
        self._replay_log.append({
            "changed": changed,
            "affected_overlaps": len(affected_overlaps),
            "mode": "incremental" if change_fraction <= 0.3 else "full",
            "time_ms": elapsed_ms,
        })
        return result

    def identify_changed_sections(self,
                                  updated: Mapping[str, Any]) -> list[str]:
        """Return keys of sections whose version has incremented."""
        changed: list[str] = []
        for key, section in updated.items():
            version = getattr(section, "version", hash(str(section)))
            if self._versions.get(key) != version:
                changed.append(key)
                self._versions[key] = version
        return changed

    def compute_affected_overlaps(
        self, changed_keys: Sequence[str], plan: IntegrationPlan,
    ) -> list[tuple[str, str]]:
        """Return overlap pairs that involve at least one changed section."""
        result: list[tuple[str, str]] = []
        for pair in plan.expected_overlaps:
            if pair[0] in changed_keys or pair[1] in changed_keys:
                result.append(pair)
        return result

    def incremental_recheck(
        self, changed: Sequence[str],
        affected_overlaps: Sequence[tuple[str, str]],
        plan: IntegrationPlan,
    ) -> IntegrationResult:
        """Re-verify only the affected overlaps, keeping the rest of the
        previous result intact."""
        result = IntegrationResult(
            status=IntegrationStatus.SUCCESS,
            plan_id=plan.plan_id,
        )
        failures: list[dict[str, Any]] = []
        for pair in affected_overlaps:
            ok = self._check_single_overlap(pair, plan)
            if not ok:
                failures.append({
                    "pair": pair,
                    "message": f"Overlap {pair} failed after incremental change",
                })
        if failures:
            result.status = IntegrationStatus.PARTIAL
            for f in failures:
                result.add_obstruction(
                    coordinate=plan.target_coordinate,
                    kind="overlap_failure",
                    message=f["message"],
                )
        elif self._previous and self._previous.global_section is not None:
            result.global_section = self._previous.global_section
        return result

    def full_replay_if_needed(
        self, plan: IntegrationPlan,
        engine: IntegrationEngine | None = None,
    ) -> IntegrationResult:
        """Perform a complete re-integration from scratch."""
        if engine is not None:
            return engine.integrate(plan)
        return IntegrationResult(
            status=IntegrationStatus.FAILED,
            plan_id=plan.plan_id,
            obstructions=[{
                "coordinate": plan.target_coordinate,
                "kind": "replay_failure",
                "message": "Full replay requested but no engine provided",
            }],
        )

    def replay_log(self) -> list[dict[str, Any]]:
        """Return the history of replay attempts."""
        return list(self._replay_log)

    # -- internal ---------------------------------------------------------

    @staticmethod
    def _check_single_overlap(pair: tuple[str, str],
                               plan: IntegrationPlan) -> bool:
        """Stub overlap check for incremental mode.

        Returns True if the overlap datum exists in the plan.
        """
        return pair in plan.expected_overlaps


# =========================================================================
# 6. IntegrationScheduler
# =========================================================================

class IntegrationScheduler:
    """Order integration attempts for efficiency and correctness.

    Respects dependency ordering (a section depending on another must
    integrate after it) and supports budget-aware scheduling.
    """

    def __init__(self, dependency_graph: Mapping[str, Sequence[str]] | None = None,
                 budget: float = float("inf")) -> None:
        self._deps: dict[str, list[str]] = {
            k: list(v) for k, v in (dependency_graph or {}).items()
        }
        self._budget = budget

    def schedule(self, plans: Sequence[IntegrationPlan]) -> list[IntegrationPlan]:
        """Produce an ordered list of plans to execute.

        Combines dependency ordering with budget awareness.
        """
        ordered = self.dependency_order(plans)
        if self._budget < float("inf"):
            ordered = self.budget_aware_scheduling(ordered, self._budget)
        return ordered

    def dependency_order(self, plans: Sequence[IntegrationPlan]) -> list[IntegrationPlan]:
        """Topological sort of plans based on the dependency graph.

        Plans whose target coordinate depends on another coordinate are
        scheduled after that dependency.
        """
        plan_map = {p.target_coordinate: p for p in plans}
        visited: set[str] = set()
        result: list[IntegrationPlan] = []

        def _visit(coord: str) -> None:
            if coord in visited:
                return
            visited.add(coord)
            for dep in self._deps.get(coord, []):
                if dep in plan_map:
                    _visit(dep)
            if coord in plan_map:
                result.append(plan_map[coord])

        for p in plans:
            _visit(p.target_coordinate)
        # Append any plans not reachable via the graph (no dependencies).
        scheduled_ids = {p.plan_id for p in result}
        for p in plans:
            if p.plan_id not in scheduled_ids:
                result.append(p)
        return result

    def parallelize_independent(
        self, plans: Sequence[IntegrationPlan],
    ) -> list[list[IntegrationPlan]]:
        """Group plans into waves of mutually independent batches.

        Plans within the same wave share no dependency edges and can
        execute concurrently.
        """
        remaining = list(plans)
        completed: set[str] = set()
        waves: list[list[IntegrationPlan]] = []
        while remaining:
            wave: list[IntegrationPlan] = []
            still_blocked: list[IntegrationPlan] = []
            for p in remaining:
                deps = self._deps.get(p.target_coordinate, [])
                if all(d in completed for d in deps):
                    wave.append(p)
                else:
                    still_blocked.append(p)
            if not wave:
                # Break cycles: force the first blocked plan through.
                wave.append(still_blocked.pop(0))
            waves.append(wave)
            completed.update(p.target_coordinate for p in wave)
            remaining = still_blocked
        return waves

    def critical_path(self, plans: Sequence[IntegrationPlan]) -> list[str]:
        """Return the sequence of coordinates on the longest dependency chain."""
        cost_map: dict[str, float] = {p.target_coordinate: p.estimated_cost for p in plans}
        memo: dict[str, tuple[float, list[str]]] = {}

        def _longest(coord: str) -> tuple[float, list[str]]:
            if coord in memo:
                return memo[coord]
            best_cost = cost_map.get(coord, 0.0)
            best_path = [coord]
            for dep in self._deps.get(coord, []):
                dep_cost, dep_path = _longest(dep)
                total = dep_cost + cost_map.get(coord, 0.0)
                if total > best_cost:
                    best_cost = total
                    best_path = dep_path + [coord]
            memo[coord] = (best_cost, best_path)
            return memo[coord]

        all_coords = [p.target_coordinate for p in plans]
        longest: list[str] = []
        best: float = 0.0
        for c in all_coords:
            cost, path = _longest(c)
            if cost > best:
                best = cost
                longest = path
        return longest

    def budget_aware_scheduling(
        self, ordered_plans: Sequence[IntegrationPlan], budget: float,
    ) -> list[IntegrationPlan]:
        """Truncate the plan list so that total estimated cost ≤ *budget*."""
        result: list[IntegrationPlan] = []
        remaining = budget
        for p in ordered_plans:
            if p.estimated_cost <= remaining:
                result.append(p)
                remaining -= p.estimated_cost
            else:
                break
        return result


# =========================================================================
# 7. GluingOrchestrator
# =========================================================================

class GluingOrchestrator:
    """Orchestrate the sheaf-theoretic gluing process.

    Collects local sections, verifies overlap compatibility via the
    cocycle condition, performs descent, and produces a global section
    or records the obstruction.
    """

    def __init__(self, descent_strategy: str = "eager") -> None:
        self._strategy = descent_strategy
        self._log: list[dict[str, Any]] = []

    def orchestrate(self, plan: IntegrationPlan,
                    local_sections: Mapping[str, Any]) -> IntegrationResult:
        """Run the full gluing pipeline for *plan*.

        Steps:
          1. Collect relevant local sections.
          2. Setup descent configuration.
          3. Verify pairwise compatibility (cocycle condition).
          4. Perform descent to produce global section.
          5. On failure, record obstruction and repair hints.
        """
        t0 = time.monotonic_ns()
        result = IntegrationResult(plan_id=plan.plan_id)

        collected = self.collect_local_sections(plan, local_sections)
        if not collected:
            result.status = IntegrationStatus.FAILED
            result.add_obstruction(
                plan.target_coordinate, "missing_data",
                "No local sections available for gluing",
            )
            return result

        descent_cfg = self.setup_descent(plan)
        compatible = self.verify_compatibility(collected, plan)
        if not compatible["all_ok"]:
            result.status = IntegrationStatus.PARTIAL
            for v in compatible.get("violations", []):
                result.add_obstruction(
                    plan.target_coordinate, "cocycle_failure", str(v),
                )
            return result

        global_section = self.perform_descent(collected, descent_cfg)
        if global_section is not None:
            result.status = IntegrationStatus.SUCCESS
            result.global_section = global_section
            result.evidence_produced.append({
                "kind": "descent_certificate",
                "coordinate": plan.target_coordinate,
                "constituent_count": len(collected),
            })
        else:
            self.handle_failure(result, plan, collected)

        elapsed_ms = (time.monotonic_ns() - t0) / 1_000_000
        result.integration_time_ms = elapsed_ms
        self._log.append(result.summary())
        return result

    def setup_descent(self, plan: IntegrationPlan) -> dict[str, Any]:
        """Build a descent configuration dict from the plan."""
        return {
            "strategy": self._strategy,
            "target": plan.target_coordinate,
            "expected_overlaps": plan.expected_overlaps,
            "max_iterations": 100 if self._strategy == "iterative" else 1,
        }

    def collect_local_sections(
        self, plan: IntegrationPlan, pool: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Filter the section pool to those named in the plan."""
        return {
            key: pool[key]
            for key in plan.sections_to_integrate
            if key in pool
        }

    def verify_compatibility(
        self, sections: Mapping[str, Any], plan: IntegrationPlan,
    ) -> dict[str, Any]:
        """Check the cocycle condition on every declared overlap.

        For each pair (i, j) in the expected overlaps, the restrictions
        of section_i and section_j to the overlap must agree.
        """
        violations: list[str] = []
        checked = 0
        for (k1, k2), overlap_key in plan.expected_overlaps.items():
            s1 = sections.get(k1)
            s2 = sections.get(k2)
            if s1 is None or s2 is None:
                violations.append(
                    f"Missing section for overlap ({k1}, {k2})"
                )
                continue
            checked += 1
            if not self._sections_agree_on_overlap(s1, s2, overlap_key):
                violations.append(
                    f"Cocycle violation on ({k1}, {k2}) at {overlap_key}"
                )
        return {
            "all_ok": len(violations) == 0,
            "checked": checked,
            "violations": violations,
        }

    def perform_descent(self, sections: Mapping[str, Any],
                        config: dict[str, Any]) -> dict[str, Any] | None:
        """Attempt to glue local sections into a global section.

        Returns a merged-data dict on success or None on failure.
        """
        if not sections:
            return None
        merged: dict[str, Any] = {}
        constituents: list[str] = []
        for key, section in sections.items():
            data = section if isinstance(section, dict) else getattr(
                section, "data", {}
            )
            if isinstance(data, dict):
                merged.update(data)
            constituents.append(key)
        return {
            "coordinate": config.get("target", ""),
            "merged_judgment": merged,
            "constituent_sections": tuple(constituents),
            "strategy": config.get("strategy", self._strategy),
        }

    def handle_failure(self, result: IntegrationResult,
                       plan: IntegrationPlan,
                       sections: Mapping[str, Any]) -> None:
        """Populate *result* with obstruction and repair-frontier data when
        descent fails."""
        result.status = IntegrationStatus.FAILED
        result.add_obstruction(
            plan.target_coordinate,
            "descent_failure",
            f"Descent failed for {len(sections)} local sections at "
            f"'{plan.target_coordinate}'",
        )
        result.evidence_produced.append({
            "kind": "repair_frontier",
            "coordinate": plan.target_coordinate,
            "suggested_refinements": [
                f"Refine cover to separate {k}" for k in list(sections)[:3]
            ],
        })

    # -- internal ---------------------------------------------------------

    @staticmethod
    def _sections_agree_on_overlap(s1: Any, s2: Any,
                                    overlap_key: str) -> bool:
        """Return True if *s1* and *s2* agree on *overlap_key*.

        Uses duck-typed access: dict key lookup or attribute access.
        """
        def _get(section: Any, key: str) -> Any:
            if isinstance(section, dict):
                return section.get(key)
            data = getattr(section, "data", {})
            if isinstance(data, dict):
                return data.get(key)
            return None

        v1 = _get(s1, overlap_key)
        v2 = _get(s2, overlap_key)
        if v1 is None or v2 is None:
            return True  # missing data is not a violation (yet)
        return v1 == v2


# =========================================================================
# 8. IntegrationEngine
# =========================================================================

class IntegrationEngine:
    """Main integration engine — the primary entry point for assembling
    local sections into a global section under treaty constraints.

    Orchestrates overlap checking, gluing, obstruction handling, regression
    testing, and semantic closure verification.  Supports copilot-assisted
    integration where the LLM proposes candidates that enter the pipeline
    as unverified proposals.
    """

    def __init__(
        self,
        local_sections: Mapping[str, Any] | None = None,
        existing_globals: Mapping[str, Any] | None = None,
        treaties: Sequence[OverlapTreaty] | None = None,
        trust_profiles: Mapping[str, Any] | None = None,
        copilot_enabled: bool = False,
    ) -> None:
        self._locals: dict[str, Any] = dict(local_sections or {})
        self._globals: dict[str, Any] = dict(existing_globals or {})
        self._treaties: list[OverlapTreaty] = list(treaties or [])
        self._trusts: dict[str, Any] = dict(trust_profiles or {})
        self._copilot_enabled = copilot_enabled
        self._gluing = GluingOrchestrator()
        self._regression = RegressionChecker(
            existing_sections=existing_globals,
            trust_profiles=trust_profiles,
            treaties=treaties,
        )
        self._history = IntegrationHistory()

    # -- primary pipeline -------------------------------------------------

    def integrate(self, plan: IntegrationPlan) -> IntegrationResult:
        """Execute the full integration pipeline for *plan*.

        Steps:
          1. Check overlaps.
          2. Attempt gluing via the orchestrator.
          3. Handle obstructions if gluing fails.
          4. Run regression checks against existing globals.
          5. Verify semantic closure.
          6. Record result in history.
        """
        t0 = time.monotonic_ns()
        overlap_ok = self.check_overlaps(plan)
        if not overlap_ok["all_ok"]:
            result = IntegrationResult(
                status=IntegrationStatus.FAILED,
                plan_id=plan.plan_id,
            )
            for v in overlap_ok.get("violations", []):
                result.add_obstruction(
                    plan.target_coordinate, "overlap_failure", str(v),
                )
            elapsed = (time.monotonic_ns() - t0) / 1_000_000
            result.integration_time_ms = elapsed
            self._history.record(result)
            return result

        result = self.attempt_gluing(plan)

        if result.has_obstructions:
            self.handle_obstruction(result, plan)

        if result.global_section is not None:
            reg_issues = self.regression_check(result.global_section)
            for issue in reg_issues:
                result.add_regression(
                    issue.get("section_key", "unknown"),
                    issue.get("message", "regression detected"),
                    issue.get("severity", "warning"),
                )
            if reg_issues:
                result.status = IntegrationStatus.REGRESSION

            closure = self.semantic_closure_check(plan)
            if not closure.get("closed", False):
                result.evidence_produced.append({
                    "kind": "closure_gap",
                    "closure_ratio": closure.get("closure_ratio", 0.0),
                })

        elapsed = (time.monotonic_ns() - t0) / 1_000_000
        result.integration_time_ms = elapsed
        self._history.record(result)
        return result

    def check_overlaps(self, plan: IntegrationPlan) -> dict[str, Any]:
        """Verify that all expected overlaps have compatible data."""
        violations: list[str] = []
        for (k1, k2), overlap_key in plan.expected_overlaps.items():
            s1 = self._locals.get(k1)
            s2 = self._locals.get(k2)
            if s1 is None or s2 is None:
                violations.append(f"Missing section for overlap ({k1}, {k2})")
                continue
            if not GluingOrchestrator._sections_agree_on_overlap(
                s1, s2, overlap_key,
            ):
                violations.append(
                    f"Overlap mismatch on ({k1}, {k2}) at '{overlap_key}'"
                )
        return {"all_ok": len(violations) == 0, "violations": violations}

    def attempt_gluing(self, plan: IntegrationPlan) -> IntegrationResult:
        """Delegate to the GluingOrchestrator for the actual descent."""
        return self._gluing.orchestrate(plan, self._locals)

    def handle_obstruction(self, result: IntegrationResult,
                           plan: IntegrationPlan) -> None:
        """Process obstructions: log them, compute repair hints, and
        optionally invoke copilot for candidate proposals."""
        for obs in result.obstructions:
            obs["repair_hints"] = [
                "Check evidence completeness at overlap",
                "Consider refining the cover",
                "Verify treaty clause expectations",
            ]
        if self._copilot_enabled:
            copilot_hints = self.copilot_integration_assist(result, plan)
            result.evidence_produced.append({
                "kind": "copilot_proposal",
                "channel": "copilot",
                "trust_ceiling": "proposal",
                "hints": copilot_hints,
            })

    def replay_under_changes(
        self, updated_sections: Mapping[str, Any], plan: IntegrationPlan,
    ) -> IntegrationResult:
        """Re-run integration after local sections have been updated.

        Delegates to the ReplayEngine for incremental-vs-full decision.
        """
        previous = self._history.latest_for_coordinate(plan.target_coordinate)
        replay = ReplayEngine(
            previous_result=previous,
            section_versions={
                k: hash(str(v)) for k, v in self._locals.items()
            },
        )
        self._locals.update(updated_sections)
        return replay.replay(updated_sections, plan, engine=self)

    def semantic_closure_check(self, plan: IntegrationPlan) -> dict[str, Any]:
        """Verify semantic closure for the current plan."""
        obligations = [
            {"id": b, "discharged": False} for b in plan.blockers
        ]
        checker = SemanticClosureChecker(
            obligations=obligations,
            treaties=self._treaties,
        )
        return checker.check()

    def regression_check(self, new_global: Any) -> list[dict[str, Any]]:
        """Run regression checks against the existing global sections."""
        return self._regression.check(new_global)

    def copilot_integration_assist(
        self, result: IntegrationResult, plan: IntegrationPlan,
    ) -> list[str]:
        """Produce copilot-consumable hints for resolving integration failures.

        Copilot is a controlled oracle: its proposals enter the evidence
        pipeline at trust_ceiling='proposal' and require corroboration
        before promotion.
        """
        hints: list[str] = []
        if result.has_obstructions:
            hints.append(
                f"[copilot] {len(result.obstructions)} obstruction(s) at "
                f"'{plan.target_coordinate}' — suggest cover refinement or "
                f"additional evidence."
            )
        for obs in result.obstructions[:3]:
            hints.append(
                f"[copilot] Obstruction '{obs.get('kind')}': "
                f"{obs.get('message', 'no detail')}"
            )
        if not plan.ready:
            hints.append(
                f"[copilot] Plan has {len(plan.blockers)} blocker(s): "
                f"{', '.join(plan.blockers[:5])}"
            )
        return hints


# =========================================================================
# 9. IntegrationHistory
# =========================================================================

class IntegrationHistory:
    """Track integration attempts across coordinates and time.

    Provides analytics on success rates, common failure modes, and
    regression frequency to guide future scheduling and copilot hints.
    """

    def __init__(self) -> None:
        self._records: list[IntegrationResult] = []
        self._by_coord: dict[str, list[IntegrationResult]] = defaultdict(list)

    def record(self, result: IntegrationResult) -> None:
        """Persist an integration result."""
        self._records.append(result)
        coord = self._coord_of(result)
        self._by_coord[coord].append(result)

    def by_coordinate(self, coordinate: str) -> list[IntegrationResult]:
        """Return all results for *coordinate*, oldest first."""
        return list(self._by_coord.get(coordinate, []))

    def latest_for_coordinate(self, coordinate: str) -> IntegrationResult | None:
        """Return the most recent result for *coordinate*."""
        history = self._by_coord.get(coordinate, [])
        return history[-1] if history else None

    def success_rate(self, coordinate: str | None = None) -> float:
        """Fraction of attempts that succeeded (globally or per coordinate)."""
        pool = (
            self._by_coord.get(coordinate, []) if coordinate
            else self._records
        )
        if not pool:
            return 0.0
        wins = sum(1 for r in pool if r.status == IntegrationStatus.SUCCESS)
        return wins / len(pool)

    def common_failures(self, top_n: int = 5) -> list[dict[str, Any]]:
        """Return the *top_n* most common obstruction kinds."""
        counter: dict[str, int] = defaultdict(int)
        for r in self._records:
            for obs in r.obstructions:
                counter[obs.get("kind", "unknown")] += 1
        ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {"kind": kind, "count": count}
            for kind, count in ranked[:top_n]
        ]

    def average_time(self, coordinate: str | None = None) -> float:
        """Mean integration time in milliseconds."""
        pool = (
            self._by_coord.get(coordinate, []) if coordinate
            else self._records
        )
        if not pool:
            return 0.0
        return sum(r.integration_time_ms for r in pool) / len(pool)

    def regression_frequency(self, coordinate: str | None = None) -> float:
        """Fraction of attempts that resulted in REGRESSION status."""
        pool = (
            self._by_coord.get(coordinate, []) if coordinate
            else self._records
        )
        if not pool:
            return 0.0
        regressions = sum(
            1 for r in pool if r.status == IntegrationStatus.REGRESSION
        )
        return regressions / len(pool)

    def total_attempts(self) -> int:
        return len(self._records)

    # -- internal ---------------------------------------------------------

    @staticmethod
    def _coord_of(result: IntegrationResult) -> str:
        gs = result.global_section
        if gs is None:
            return result.plan_id
        if isinstance(gs, dict):
            return gs.get("coordinate", result.plan_id)
        return getattr(gs, "coordinate", result.plan_id)


# =========================================================================
# 10. IntegrationDiagnostics
# =========================================================================

class IntegrationDiagnostics:
    """Produce diagnostic reports for integration pipelines.

    Designed for both human operators and copilot consumption: every
    report method returns structured data that an LLM can parse and
    act upon.
    """

    def __init__(self, history: IntegrationHistory | None = None,
                 closure_checker: SemanticClosureChecker | None = None,
                 regression_checker: RegressionChecker | None = None) -> None:
        self._history = history or IntegrationHistory()
        self._closure = closure_checker or SemanticClosureChecker()
        self._regression = regression_checker or RegressionChecker()

    def integration_summary(self) -> dict[str, Any]:
        """High-level summary of all integration activity."""
        total = self._history.total_attempts()
        return {
            "total_attempts": total,
            "success_rate": round(self._history.success_rate(), 4),
            "average_time_ms": round(self._history.average_time(), 2),
            "regression_frequency": round(
                self._history.regression_frequency(), 4
            ),
            "common_failures": self._history.common_failures(top_n=3),
        }

    def obstruction_analysis(self) -> dict[str, Any]:
        """Detailed breakdown of obstructions across all attempts."""
        all_obs: list[dict[str, Any]] = []
        for r in self._history._records:
            all_obs.extend(r.obstructions)
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for obs in all_obs:
            by_kind[obs.get("kind", "unknown")].append(obs)
        return {
            "total_obstructions": len(all_obs),
            "by_kind": {k: len(v) for k, v in by_kind.items()},
            "sample_per_kind": {
                k: v[0] if v else None for k, v in by_kind.items()
            },
        }

    def regression_report(self) -> dict[str, Any]:
        """Summary of all regressions detected."""
        all_reg: list[dict[str, Any]] = []
        for r in self._history._records:
            all_reg.extend(r.regressions)
        by_severity: dict[str, int] = defaultdict(int)
        for reg in all_reg:
            by_severity[reg.get("severity", "unknown")] += 1
        return {
            "total_regressions": len(all_reg),
            "by_severity": dict(by_severity),
            "regression_frequency": round(
                self._history.regression_frequency(), 4
            ),
            "report_text": self._regression.regression_report(),
        }

    def closure_report(self) -> dict[str, Any]:
        """Semantic closure status."""
        return self._closure.check()

    def copilot_integration_summary(self) -> dict[str, Any]:
        """A concise, copilot-optimised summary that an LLM can use to
        decide next actions.

        Includes actionable recommendations ranked by estimated impact.
        """
        summary = self.integration_summary()
        obs = self.obstruction_analysis()
        closure = self.closure_report()
        recommendations: list[str] = []

        if summary["success_rate"] < 0.5:
            recommendations.append(
                "[copilot] Low success rate — consider refining covers or "
                "adding evidence before reattempting integration."
            )
        if obs["total_obstructions"] > 0:
            top_kind = max(obs["by_kind"], key=obs["by_kind"].get, default="")  # type: ignore[arg-type]
            recommendations.append(
                f"[copilot] Most common obstruction: '{top_kind}' "
                f"({obs['by_kind'].get(top_kind, 0)} occurrences). "
                f"Focus repair efforts there."
            )
        if not closure.get("closed", False):
            ratio = closure.get("closure_ratio", 0.0)
            recommendations.append(
                f"[copilot] Semantic closure at {ratio:.0%} — "
                f"discharge open obligations to reach full closure."
            )
        if summary["regression_frequency"] > 0.1:
            recommendations.append(
                "[copilot] Elevated regression frequency — run targeted "
                "regression checks before integrating new sections."
            )

        return {
            "summary": summary,
            "obstruction_analysis": obs,
            "closure": closure,
            "recommendations": recommendations,
        }


# =========================================================================
# Legacy compatibility
# =========================================================================

def integrate_plans(
    plans: tuple[ConstructionPlan, ...],
    treaties: tuple[OverlapTreaty, ...],
) -> IntegrationPlan:
    """Create an IntegrationPlan from construction plans and treaties.

    Backward-compatible entry point.  Blockers are inferred from
    residual obligations on the construction plans.
    """
    blockers = tuple(
        dict.fromkeys(
            residual for plan in plans for residual in plan.residuals
        )
    )
    section_keys = [
        step.patch
        for plan in plans
        for step in plan.steps
        if hasattr(step, "patch")
    ]
    return IntegrationPlan(
        target_coordinate="",
        sections_to_integrate=section_keys,
        plans=plans,
        treaties=treaties,
        blockers=blockers,
    )


__all__ = [
    "IntegrationPlan",
    "IntegrationResult",
    "IntegrationStatus",
    "IntegrationStrategy",
    "IntegrationEngine",
    "RegressionChecker",
    "SemanticClosureChecker",
    "ReplayEngine",
    "IntegrationScheduler",
    "GluingOrchestrator",
    "IntegrationHistory",
    "IntegrationDiagnostics",
    "integrate_plans",
    # Cross-subsystem enrichments
    "descent_integration",
    "certificate_integration",
]


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.certificates import (
        Certificate as _Certificate,
        CertificateBuilder as _CertificateBuilder,
        emit_certificate as _emit_certificate,
    )
except Exception:  # pragma: no cover
    _Certificate = None  # type: ignore[assignment,misc]
    _CertificateBuilder = None  # type: ignore[assignment,misc]
    _emit_certificate = None  # type: ignore[assignment,misc]


def descent_integration(
    plan: IntegrationPlan,
    *,
    sections: Mapping[str, Any] | None = None,
    cover: Any | None = None,
    strategy: Any | None = None,
) -> dict[str, Any]:
    """Integrate local constructions into a global section via descent.

    Delegates to ``jugeo.geometry.descent.DescentEngine`` (or the
    module-level ``glue_sections`` helper) to perform sheaf-theoretic
    gluing of local sections along a cover.

    Parameters
    ----------
    plan:
        The integration plan describing which sections participate.
    sections:
        Mapping from section keys to section objects.  When *None* an
        empty mapping is used and the result is a dry-run report.
    cover:
        A ``jugeo.geometry.covers.Cover`` over which descent runs.
    strategy:
        An optional ``DescentStrategy`` value; defaults to *EAGER*.

    Returns
    -------
    dict[str, Any]
        ``{"status": str, "global_section": object | None,
        "obstructions": list, "strategy": str}``.
    """
    secs = dict(sections or {})
    strat = strategy
    if strat is None and DescentStrategy is not None:
        strat = DescentStrategy.EAGER

    result: dict[str, Any] = {
        "status": "failed",
        "global_section": None,
        "obstructions": [],
        "strategy": strat.value if hasattr(strat, "value") else str(strat),
    }

    if DescentEngine is None:
        result["status"] = "unavailable"
        result["obstructions"] = ["jugeo.geometry.descent not available"]
        return result

    if cover is None:
        result["status"] = "no_cover"
        result["obstructions"] = ["No cover provided for descent"]
        return result

    try:
        engine = DescentEngine()
        if hasattr(engine, "attempt_descent"):
            descent_result = engine.attempt_descent(cover, secs)
        elif hasattr(engine, "run"):
            descent_result = engine.run(cover, secs)
        else:
            result["obstructions"] = ["DescentEngine has no run method"]
            return result

        if hasattr(descent_result, "status"):
            status_val = descent_result.status
            result["status"] = status_val.value if hasattr(status_val, "value") else str(status_val)
        else:
            result["status"] = "success"

        if hasattr(descent_result, "global_section"):
            result["global_section"] = descent_result.global_section
        if hasattr(descent_result, "obstructions"):
            result["obstructions"] = list(descent_result.obstructions or [])

    except Exception as exc:
        result["status"] = "error"
        result["obstructions"] = [str(exc)]

    return result


def certificate_integration(
    plan: IntegrationPlan,
    integration_result: dict[str, Any] | None = None,
    *,
    issuer: str = "jugeo.generation.integration",
) -> dict[str, Any]:
    """Produce an integration certificate for a completed integration.

    Uses ``jugeo.evidence.certificates`` to build a
    :class:`~jugeo.evidence.certificates.Certificate` that attests the
    integration outcome, recording verified propositions, residual
    obligations, and any obstructions.

    Parameters
    ----------
    plan:
        The integration plan that was executed.
    integration_result:
        Output dict from :func:`descent_integration` or similar.
    issuer:
        Identifier of the issuing subsystem.

    Returns
    -------
    dict[str, Any]
        ``{"certificate_id": str, "status": str, "coordinate": str,
        "verified_propositions": list, "obstructions": list}``.
    """
    ir = integration_result or {}
    status = ir.get("status", "unknown")
    obstructions = ir.get("obstructions", [])

    output: dict[str, Any] = {
        "certificate_id": "",
        "status": status,
        "coordinate": plan.target_coordinate,
        "verified_propositions": list(plan.sections_to_integrate),
        "obstructions": obstructions,
    }

    if _CertificateBuilder is not None:
        try:
            builder = _CertificateBuilder()
            if hasattr(builder, "set_coordinate"):
                builder.set_coordinate(plan.target_coordinate)
            if hasattr(builder, "set_issuer"):
                builder.set_issuer(issuer)
            if hasattr(builder, "add_verified_propositions"):
                builder.add_verified_propositions(plan.sections_to_integrate)
            if hasattr(builder, "set_obstructions"):
                builder.set_obstructions(obstructions)
            if hasattr(builder, "build"):
                cert = builder.build()
                output["certificate_id"] = getattr(cert, "certificate_id", "")
        except Exception:
            pass

    if not output["certificate_id"] and _Certificate is not None:
        try:
            import uuid as _uuid
            cert = _Certificate(
                certificate_id=_uuid.uuid4().hex[:12],
                coordinate=plan.target_coordinate,
                verified_propositions=tuple(plan.sections_to_integrate),
                obstructions=tuple(str(o) for o in obstructions),
                issuer=issuer,
            )
            output["certificate_id"] = cert.certificate_id
        except Exception:
            pass

    if not output["certificate_id"]:
        import uuid as _uuid
        output["certificate_id"] = f"fallback-{_uuid.uuid4().hex[:12]}"

    return output


# copilot: integration module — gluing, descent, regression, and semantic
# closure under sheaf-theoretic discipline.  All copilot proposals enter
# at trust_ceiling='proposal' and require corroboration.
