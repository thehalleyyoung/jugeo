r"""Core algorithms for the ``jugeo.se_theory.maturity`` package.

Theory (JuGeo — "Continuous Maturity as Sheaf Descent", B10):
    All algorithms here operate on the maturity data model:

    * **MaturityAssessor**    — evaluates the site against each level's
      descent criteria and produces a ``MaturityAssessment``.
    * **ImprovementPlanner**  — builds a ``ImprovementPlan`` to advance from
      the current level to a target level.
    * **CycleManager**        — drives the ASSESS→PRIORITIZE→REPAIR→
      CERTIFY→COMPLETE improvement loop.
    * **MaturityTracker**     — accumulates assessment history and derives
      ``MaturityTrend`` statistics (improving / degrading / stagnant).

    copilot: se-theory-maturity-algorithms
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from jugeo.se_theory.maturity.models import (
    CyclicSchedule,
    ImprovementCycle,
    ImprovementPlan,
    MaturityAssessment,
    MaturityCriterion,
    MaturityLevel,
    MaturityReport,
    MaturityTrend,
    _iso_now,
)

__all__ = [
    "MaturityAssessor",
    "ImprovementPlanner",
    "CycleManager",
    "MaturityTracker",
    # criterion catalog
    "DEFAULT_CRITERIA",
]


# ---------------------------------------------------------------------------
# Default criterion catalog
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA: list[MaturityCriterion] = [
    MaturityCriterion(
        level=MaturityLevel.LEVEL_0_RAW,
        name="code_exists",
        description="At least one coordinate has source code.",
        required_metrics={"coordinate_count": 1.0},
    ),
    MaturityCriterion(
        level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
        name="local_evidence_coverage",
        description=(
            "Local evidence sections (tests/witnesses) exist for at least "
            "50 % of coordinates."
        ),
        required_metrics={"evidence_coverage": 0.5},
    ),
    MaturityCriterion(
        level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
        name="local_descent_passes",
        description=(
            "Intra-package gluing verified: solver evidence exists for all "
            "critical-path coordinates."
        ),
        required_metrics={
            "evidence_coverage": 0.8,
            "critical_path_coverage": 1.0,
        },
    ),
    MaturityCriterion(
        level=MaturityLevel.LEVEL_3_GLOBAL_DESCENT,
        name="global_descent_passes",
        description=(
            "Cross-package morphism consistency verified: descent condition "
            "holds across all package overlaps."
        ),
        required_metrics={
            "evidence_coverage": 0.95,
            "morphism_consistency": 1.0,
        },
    ),
    MaturityCriterion(
        level=MaturityLevel.LEVEL_4_CERTIFIED,
        name="full_certificates",
        description=(
            "Proof-carrying certificates cover the entire site — every "
            "coordinate has a verified certificate."
        ),
        required_metrics={"certificate_coverage": 1.0},
    ),
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PHASES: list[str] = [
    "ASSESS",
    "PRIORITIZE",
    "REPAIR",
    "CERTIFY",
    "COMPLETE",
]


# ---------------------------------------------------------------------------
# MaturityAssessor
# ---------------------------------------------------------------------------


class MaturityAssessor:
    """Evaluate a site's maturity level against the descent criterion hierarchy.

    All methods are pure: they produce new data without mutating their inputs.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
        obstructions: Optional[list[dict[str, Any]]] = None,
        covers: Optional[list[dict[str, Any]]] = None,
        morphisms: Optional[list[dict[str, Any]]] = None,
        certificates: Optional[list[dict[str, Any]]] = None,
        site_id: str = "default",
    ) -> MaturityAssessment:
        """Assess the overall maturity of a site.

        Parameters
        ----------
        coordinates:
            All coordinate IDs in the site.
        evidence:
            Evidence dicts (should have at least ``coordinate_id`` key).
        obstructions:
            Open obstruction records (may block critical-path coverage).
        covers:
            Cover membership dicts for package-level grouping.
        morphisms:
            Cross-package morphism dicts for global descent checking.
        certificates:
            Certificate dicts (should have ``coordinate_id`` key).
        site_id:
            Identifier for the site being assessed.

        Returns
        -------
        MaturityAssessment
        """
        obstructions = obstructions or []
        covers = covers or []
        morphisms = morphisms or []
        certificates = certificates or []

        level_0_ok = self._check_level_0(coordinates)
        level_1_ok = level_0_ok and self._check_level_1(coordinates, evidence)
        level_2_ok = level_1_ok and self._check_level_2(
            coordinates, evidence, covers, obstructions
        )
        level_3_ok = level_2_ok and self._check_level_3(
            coordinates, covers, morphisms, evidence
        )
        level_4_ok = level_3_ok and self._check_level_4(
            coordinates, certificates
        )

        if level_4_ok:
            overall = MaturityLevel.LEVEL_4_CERTIFIED
        elif level_3_ok:
            overall = MaturityLevel.LEVEL_3_GLOBAL_DESCENT
        elif level_2_ok:
            overall = MaturityLevel.LEVEL_2_LOCAL_DESCENT
        elif level_1_ok:
            overall = MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        elif level_0_ok:
            overall = MaturityLevel.LEVEL_0_RAW
        else:
            overall = MaturityLevel.LEVEL_0_RAW

        # Per-package levels
        by_package = self._per_package_levels(
            coordinates, evidence, covers, morphisms, certificates,
            obstructions,
        )

        # Criteria accounting
        criteria_met: list[str] = []
        criteria_unmet: list[str] = []
        level_checks = [
            (level_0_ok, "code_exists"),
            (level_1_ok, "local_evidence_coverage"),
            (level_2_ok, "local_descent_passes"),
            (level_3_ok, "global_descent_passes"),
            (level_4_ok, "full_certificates"),
        ]
        for ok, name in level_checks:
            (criteria_met if ok else criteria_unmet).append(name)

        blocking = self._collect_blockers(
            coordinates, evidence, covers, morphisms, certificates,
            obstructions, overall,
        )
        recommendations = self._generate_recommendations(overall, blocking)

        return MaturityAssessment(
            site_id=site_id,
            overall_level=overall,
            by_package=by_package,
            criteria_met=criteria_met,
            criteria_unmet=criteria_unmet,
            blocking_issues=blocking,
            recommendations=recommendations,
            computed_at=_iso_now(),
        )

    def _check_level_0(self, coordinates: list[str]) -> bool:
        """Level 0: at least one coordinate exists (code exists)."""
        return len(coordinates) > 0

    def _check_level_1(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
    ) -> bool:
        """Level 1: local evidence sections exist for ≥ 50 % of coordinates."""
        if not coordinates:
            return False
        evidenced = {e.get("coordinate_id") for e in evidence}
        covered = sum(1 for c in coordinates if c in evidenced)
        return (covered / len(coordinates)) >= 0.5

    def _check_level_2(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
        covers: list[dict[str, Any]],
        obstructions: list[dict[str, Any]],
    ) -> bool:
        """Level 2: local descent passes + no open obstructions on critical path."""
        if not coordinates:
            return False
        evidenced = {e.get("coordinate_id") for e in evidence}
        covered = sum(1 for c in coordinates if c in evidenced)
        coverage_ok = (covered / len(coordinates)) >= 0.8
        # Critical path: coords flagged as critical in covers
        critical_coords = {
            m.get("id")
            for cov in covers
            for m in cov.get("members", [])
            if m.get("critical_path", False)
        }
        open_obstructions_on_critical = {
            o.get("coordinate_id")
            for o in obstructions
            if o.get("status", "OPEN") != "RESOLVED"
            and o.get("coordinate_id") in critical_coords
        }
        return coverage_ok and len(open_obstructions_on_critical) == 0

    def _check_level_3(
        self,
        coordinates: list[str],
        covers: list[dict[str, Any]],
        morphisms: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> bool:
        """Level 3: cross-package morphism consistency verified."""
        if not coordinates:
            return False
        evidenced = {e.get("coordinate_id") for e in evidence}
        covered = sum(1 for c in coordinates if c in evidenced)
        if len(coordinates) and (covered / len(coordinates)) < 0.95:
            return False
        # All morphisms must have a "verified" status
        for morph in morphisms:
            if morph.get("status", "unverified") != "verified":
                return False
        return True

    def _check_level_4(
        self,
        coordinates: list[str],
        certificates: list[dict[str, Any]],
    ) -> bool:
        """Level 4: full proof-carrying certificates cover every coordinate."""
        if not coordinates:
            return False
        certified = {c.get("coordinate_id") for c in certificates}
        return all(coord in certified for coord in coordinates)

    def assess_package(
        self,
        package_coords: list[str],
        evidence: list[dict[str, Any]],
        obstructions: Optional[list[dict[str, Any]]] = None,
    ) -> MaturityLevel:
        """Assess the maturity level of a single package.

        Parameters
        ----------
        package_coords:
            Coordinate IDs belonging to this package.
        evidence:
            All site evidence (filtered internally).
        obstructions:
            Open obstruction records.

        Returns
        -------
        MaturityLevel
        """
        obstructions = obstructions or []
        pkg_evidence = [
            e for e in evidence if e.get("coordinate_id") in package_coords
        ]
        pkg_obs = [
            o for o in obstructions
            if o.get("coordinate_id") in package_coords
        ]
        if not self._check_level_0(package_coords):
            return MaturityLevel.LEVEL_0_RAW
        if not self._check_level_1(package_coords, pkg_evidence):
            return MaturityLevel.LEVEL_0_RAW
        # For package-level: level 2 = 80 % coverage + no open obstructions
        evidenced = {e.get("coordinate_id") for e in pkg_evidence}
        covered = sum(1 for c in package_coords if c in evidenced)
        has_good_coverage = (covered / len(package_coords)) >= 0.8
        open_obs = [
            o for o in pkg_obs if o.get("status", "OPEN") != "RESOLVED"
        ]
        if not has_good_coverage or open_obs:
            return MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        return MaturityLevel.LEVEL_2_LOCAL_DESCENT

    def criteria_for_level(
        self, level: MaturityLevel
    ) -> list[MaturityCriterion]:
        """Return all default criteria that gate ``level``.

        Parameters
        ----------
        level:
            The maturity level to query.

        Returns
        -------
        list[MaturityCriterion]
        """
        return [c for c in DEFAULT_CRITERIA if c.level == level]

    def identify_blockers(
        self, assessment: MaturityAssessment
    ) -> list[str]:
        """Return human-readable descriptions of what blocks the next level.

        Parameters
        ----------
        assessment:
            A completed maturity assessment.

        Returns
        -------
        list[str]
        """
        return list(assessment.blocking_issues)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _per_package_levels(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
        covers: list[dict[str, Any]],
        morphisms: list[dict[str, Any]],
        certificates: list[dict[str, Any]],
        obstructions: list[dict[str, Any]],
    ) -> dict[str, MaturityLevel]:
        """Compute per-package maturity levels from cover membership."""
        by_package: dict[str, list[str]] = {}
        # Build package → coords map from cover members
        for cov in covers:
            pkg_id = cov.get("id", "")
            members = [m.get("id", "") for m in cov.get("members", [])]
            if pkg_id and members:
                by_package[pkg_id] = members
        # Default: treat whole site as one package
        if not by_package and coordinates:
            by_package["default"] = list(coordinates)
        result: dict[str, MaturityLevel] = {}
        for pkg_id, coords in by_package.items():
            result[pkg_id] = self.assess_package(coords, evidence, obstructions)
        return result

    def _collect_blockers(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
        covers: list[dict[str, Any]],
        morphisms: list[dict[str, Any]],
        certificates: list[dict[str, Any]],
        obstructions: list[dict[str, Any]],
        current_level: MaturityLevel,
    ) -> list[str]:
        """Collect blocking issues preventing the next maturity level."""
        blockers: list[str] = []
        next_level_val = int(current_level) + 1
        if next_level_val > 4:
            return []

        evidenced = {e.get("coordinate_id") for e in evidence}
        cert_coords = {c.get("coordinate_id") for c in certificates}

        if next_level_val == 1:
            uncovered = [c for c in coordinates if c not in evidenced]
            if uncovered:
                blockers.append(
                    f"Missing evidence at {len(uncovered)} coordinates: "
                    + ", ".join(uncovered[:5])
                )
        elif next_level_val == 2:
            coverage = (
                len(evidenced & set(coordinates)) / len(coordinates)
                if coordinates else 0
            )
            if coverage < 0.8:
                blockers.append(
                    f"Evidence coverage {coverage:.0%} < 80 % required for "
                    "local descent."
                )
            open_obs = [
                o for o in obstructions
                if o.get("status", "OPEN") != "RESOLVED"
            ]
            if open_obs:
                ids = [o.get("coordinate_id", "?") for o in open_obs[:3]]
                blockers.append(
                    f"{len(open_obs)} open obstruction(s) blocking critical "
                    "path: " + ", ".join(ids)
                )
        elif next_level_val == 3:
            coverage = (
                len(evidenced & set(coordinates)) / len(coordinates)
                if coordinates else 0
            )
            if coverage < 0.95:
                blockers.append(
                    f"Evidence coverage {coverage:.0%} < 95 % required for "
                    "global descent."
                )
            unverified = [
                m for m in morphisms
                if m.get("status", "unverified") != "verified"
            ]
            if unverified:
                blockers.append(
                    f"{len(unverified)} unverified morphism(s) blocking "
                    "global descent."
                )
        elif next_level_val == 4:
            uncertified = [c for c in coordinates if c not in cert_coords]
            if uncertified:
                blockers.append(
                    f"{len(uncertified)} coordinate(s) missing certificates."
                )

        return blockers

    def _generate_recommendations(
        self,
        current_level: MaturityLevel,
        blockers: list[str],
    ) -> list[str]:
        """Generate actionable recommendations based on blockers."""
        recs: list[str] = []
        if not blockers:
            if int(current_level) < 4:
                recs.append(
                    f"Site meets level {current_level.name}. "
                    "Run repair cycle to advance further."
                )
            else:
                recs.append(
                    "Site is fully certified. Schedule periodic re-assessment."
                )
            return recs
        for blocker in blockers:
            if "Missing evidence" in blocker:
                recs.append(
                    "Add unit tests or witness sections for uncovered "
                    "coordinates."
                )
            elif "coverage" in blocker.lower():
                recs.append(
                    "Increase test coverage to meet the required threshold."
                )
            elif "obstruction" in blocker.lower():
                recs.append(
                    "Resolve open obstructions on critical-path coordinates "
                    "before advancing."
                )
            elif "morphism" in blocker.lower():
                recs.append(
                    "Verify cross-package morphism consistency via the descent "
                    "solver."
                )
            elif "certificate" in blocker.lower():
                recs.append(
                    "Issue proof-carrying certificates for all uncertified "
                    "coordinates."
                )
            else:
                recs.append(f"Address: {blocker}")
        return recs


# ---------------------------------------------------------------------------
# ImprovementPlanner
# ---------------------------------------------------------------------------


class ImprovementPlanner:
    """Build structured improvement plans for advancing maturity levels.

    Plans contain topologically ordered action steps with effort estimates.
    """

    # Base effort in notional story-points per action type
    _EFFORT_TABLE: dict[str, float] = {
        "add_evidence": 1.0,
        "resolve_obstruction": 2.0,
        "verify_morphism": 3.0,
        "issue_certificate": 1.5,
        "increase_coverage": 2.0,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan_improvement(
        self,
        assessment: MaturityAssessment,
        target_level: MaturityLevel,
    ) -> ImprovementPlan:
        """Create an ``ImprovementPlan`` to advance from the current level.

        Parameters
        ----------
        assessment:
            Most recent maturity assessment.
        target_level:
            Desired target maturity level.

        Returns
        -------
        ImprovementPlan
        """
        current = assessment.overall_level
        if int(target_level) <= int(current):
            return ImprovementPlan(
                current_level=current,
                target_level=target_level,
                required_actions=[],
                estimated_cycles=0,
                blocking_dependencies=[],
            )

        all_actions: list[dict[str, Any]] = []
        for lvl_val in range(int(current) + 1, int(target_level) + 1):
            lvl = MaturityLevel(lvl_val)
            actions = self._actions_for_level_transition(current, lvl)
            all_actions.extend(actions)

        ordered = self.dependency_order(all_actions)
        total_effort = self.estimate_effort(
            ImprovementPlan(
                current_level=current,
                target_level=target_level,
                required_actions=ordered,
            )
        )
        estimated_cycles = max(1, int(total_effort / 5.0 + 0.5))
        blocking = [a["action"] for a in ordered if a.get("blocking", False)]

        return ImprovementPlan(
            current_level=current,
            target_level=target_level,
            required_actions=ordered,
            estimated_cycles=estimated_cycles,
            blocking_dependencies=blocking,
        )

    def _actions_for_level_transition(
        self,
        from_level: MaturityLevel,
        to_level: MaturityLevel,
    ) -> list[dict[str, Any]]:
        """Return the set of actions required to move from ``from_level`` to ``to_level``.

        Parameters
        ----------
        from_level:
            Source maturity level.
        to_level:
            Target maturity level (must be exactly one step above).

        Returns
        -------
        list[dict]
            Action dicts with keys:
            ``action``, ``description``, ``coordinates``,
            ``estimated_effort``, ``blocking``.
        """
        actions: list[dict[str, Any]] = []
        target_val = int(to_level)

        if target_val == 1:
            actions.append({
                "action": "add_evidence",
                "description": (
                    "Add local evidence sections (unit tests / witnesses) to "
                    "reach 50 % coverage of all coordinates."
                ),
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["add_evidence"],
                "blocking": True,
            })
        elif target_val == 2:
            actions.append({
                "action": "increase_coverage",
                "description": "Increase evidence coverage to ≥ 80 %.",
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["increase_coverage"],
                "blocking": True,
            })
            actions.append({
                "action": "resolve_obstruction",
                "description": (
                    "Resolve all open obstructions on critical-path coordinates."
                ),
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["resolve_obstruction"],
                "blocking": True,
            })
        elif target_val == 3:
            actions.append({
                "action": "increase_coverage",
                "description": "Increase evidence coverage to ≥ 95 %.",
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["increase_coverage"],
                "blocking": True,
            })
            actions.append({
                "action": "verify_morphism",
                "description": (
                    "Verify all cross-package morphisms using the descent solver."
                ),
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["verify_morphism"],
                "blocking": True,
            })
        elif target_val == 4:
            actions.append({
                "action": "issue_certificate",
                "description": (
                    "Issue proof-carrying certificates for every coordinate in "
                    "the site."
                ),
                "coordinates": [],
                "estimated_effort": self._EFFORT_TABLE["issue_certificate"],
                "blocking": True,
            })

        return actions

    def estimate_effort(self, plan: ImprovementPlan) -> float:
        """Estimate total story-point effort for a plan.

        Parameters
        ----------
        plan:
            An improvement plan with ``required_actions``.

        Returns
        -------
        float
            Sum of ``estimated_effort`` values across all actions.
        """
        return sum(
            float(a.get("estimated_effort", 1.0))
            for a in plan.required_actions
        )

    def identify_quick_wins(
        self,
        assessment: MaturityAssessment,
    ) -> list[dict[str, Any]]:
        """Identify low-effort actions with a high maturity impact.

        Quick wins are actions with ``estimated_effort`` ≤ 1.5 that target
        the *next* maturity level.

        Parameters
        ----------
        assessment:
            Current maturity assessment.

        Returns
        -------
        list[dict]
            Quick-win action dicts.
        """
        next_val = min(int(assessment.overall_level) + 1, 4)
        target = MaturityLevel(next_val)
        actions = self._actions_for_level_transition(
            assessment.overall_level, target
        )
        return [
            a for a in actions if a.get("estimated_effort", 99.0) <= 1.5
        ]

    def dependency_order(
        self,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Topologically sort actions so blocking actions come first.

        Parameters
        ----------
        actions:
            Flat list of action dicts (may have a ``depends_on`` key).

        Returns
        -------
        list[dict]
            Actions ordered so that blocking / foundational actions precede
            those that depend on them.
        """
        # Simple stable sort: blocking actions first, then by estimated_effort
        return sorted(
            actions,
            key=lambda a: (
                0 if a.get("blocking", False) else 1,
                float(a.get("estimated_effort", 1.0)),
            ),
        )


# ---------------------------------------------------------------------------
# CycleManager
# ---------------------------------------------------------------------------


class CycleManager:
    """Drive ASSESS→PRIORITIZE→REPAIR→CERTIFY→COMPLETE improvement cycles.

    Each call to :meth:`advance_phase` moves the cycle one step forward.
    :meth:`auto_cycle` runs the full cycle end-to-end without manual
    intervention (useful for CI/CD pipelines).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_cycle(
        self, assessment: MaturityAssessment
    ) -> ImprovementCycle:
        """Create a new improvement cycle starting from an assessment.

        Parameters
        ----------
        assessment:
            The assessment that triggered the cycle.

        Returns
        -------
        ImprovementCycle
            New cycle in ``ASSESS`` phase.
        """
        return ImprovementCycle(
            cycle_id=uuid.uuid4().hex[:16],
            phase="ASSESS",
            started_at=_iso_now(),
            assessment_before=assessment,
            level_before=assessment.overall_level,
        )

    def advance_phase(
        self, cycle: ImprovementCycle
    ) -> ImprovementCycle:
        """Advance the cycle to the next phase.

        Phase order: ASSESS → PRIORITIZE → REPAIR → CERTIFY → COMPLETE.

        Parameters
        ----------
        cycle:
            Current cycle.

        Returns
        -------
        ImprovementCycle
            Updated cycle at the next phase.  If already COMPLETE, returns
            the cycle unchanged.
        """
        if cycle.phase not in _PHASES:
            return cycle
        idx = _PHASES.index(cycle.phase)
        if idx >= len(_PHASES) - 1:
            return cycle  # already COMPLETE
        next_phase = _PHASES[idx + 1]
        return ImprovementCycle(
            cycle_id=cycle.cycle_id,
            phase=next_phase,
            started_at=cycle.started_at,
            completed_at=(
                _iso_now() if next_phase == "COMPLETE" else cycle.completed_at
            ),
            assessment_before=cycle.assessment_before,
            assessment_after=cycle.assessment_after,
            repairs_applied=list(cycle.repairs_applied),
            certificates_issued=list(cycle.certificates_issued),
            level_before=cycle.level_before,
            level_after=cycle.level_after,
        )

    def complete_cycle(
        self,
        cycle: ImprovementCycle,
        assessment_after: MaturityAssessment,
    ) -> ImprovementCycle:
        """Close out a cycle with a final assessment.

        Parameters
        ----------
        cycle:
            Cycle to complete.
        assessment_after:
            Assessment taken after all repairs and certifications.

        Returns
        -------
        ImprovementCycle
            Completed cycle with ``phase="COMPLETE"``.
        """
        return ImprovementCycle(
            cycle_id=cycle.cycle_id,
            phase="COMPLETE",
            started_at=cycle.started_at,
            completed_at=_iso_now(),
            assessment_before=cycle.assessment_before,
            assessment_after=assessment_after,
            repairs_applied=list(cycle.repairs_applied),
            certificates_issued=list(cycle.certificates_issued),
            level_before=cycle.level_before,
            level_after=assessment_after.overall_level,
        )

    def is_cycle_overdue(self, schedule: CyclicSchedule) -> bool:
        """Return True if the next scheduled cycle is past due.

        Parameters
        ----------
        schedule:
            Cyclic schedule configuration.

        Returns
        -------
        bool
        """
        now_str = _iso_now()
        try:
            next_dt = datetime.strptime(
                schedule.next_cycle_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            now_ts = time.time()
            return now_ts > next_dt.timestamp()
        except (ValueError, AttributeError):
            return True  # unparseable = treat as overdue

    def auto_cycle(
        self,
        coordinates: list[str],
        evidence: list[dict[str, Any]],
        obstructions: Optional[list[dict[str, Any]]] = None,
        covers: Optional[list[dict[str, Any]]] = None,
        morphisms: Optional[list[dict[str, Any]]] = None,
        certificates: Optional[list[dict[str, Any]]] = None,
        site_id: str = "default",
    ) -> ImprovementCycle:
        """Run a full improvement cycle automatically (no manual phases).

        This is the entry-point for CI/CD-driven continuous maturity.
        The cycle runs through all phases and returns a COMPLETE cycle.

        Parameters
        ----------
        coordinates:
            All site coordinates.
        evidence:
            Current evidence sections.
        obstructions:
            Open obstruction records.
        covers:
            Cover membership dicts.
        morphisms:
            Cross-package morphism dicts.
        certificates:
            Certificate dicts.
        site_id:
            Site identifier.

        Returns
        -------
        ImprovementCycle
            Completed cycle with before/after assessments.
        """
        assessor = MaturityAssessor()
        before = assessor.assess(
            coordinates=coordinates,
            evidence=evidence,
            obstructions=obstructions,
            covers=covers,
            morphisms=morphisms,
            certificates=certificates,
            site_id=site_id,
        )
        cycle = self.start_cycle(before)

        # Advance through phases automatically
        for _ in _PHASES[1:]:  # skip ASSESS (already done)
            cycle = self.advance_phase(cycle)
            if cycle.phase == "COMPLETE":
                break

        # Final assessment (same data — in a real system repairs would improve
        # the evidence set; here we re-assess to demonstrate the API shape)
        after = assessor.assess(
            coordinates=coordinates,
            evidence=evidence,
            obstructions=obstructions,
            covers=covers,
            morphisms=morphisms,
            certificates=certificates,
            site_id=site_id,
        )
        return self.complete_cycle(cycle, after)


# ---------------------------------------------------------------------------
# MaturityTracker
# ---------------------------------------------------------------------------


class MaturityTracker:
    """Accumulate assessment history and compute trend statistics.

    The tracker maintains an in-memory history list.  For persistent
    storage, callers should serialise and reload the history externally.
    """

    def __init__(self) -> None:
        self._history: list[MaturityAssessment] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_assessment(self, assessment: MaturityAssessment) -> None:
        """Append an assessment to the history.

        Parameters
        ----------
        assessment:
            A completed ``MaturityAssessment``.
        """
        self._history.append(assessment)

    def compute_trend(self, window: int = 10) -> MaturityTrend:
        """Compute trend statistics over the most recent ``window`` assessments.

        Parameters
        ----------
        window:
            Number of most recent assessments to include.

        Returns
        -------
        MaturityTrend
        """
        recent = self._history[-window:]
        if not recent:
            return MaturityTrend()

        timestamps = [a.computed_at for a in recent]
        levels = [int(a.overall_level) for a in recent]

        # Per-package trends
        all_pkgs: set[str] = set()
        for a in recent:
            all_pkgs.update(a.by_package.keys())

        by_pkg: dict[str, list[int]] = {pkg: [] for pkg in sorted(all_pkgs)}
        for a in recent:
            for pkg in by_pkg:
                lvl = int(a.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
                by_pkg[pkg].append(lvl)

        improving = self.improving_packages(window)
        degrading = self.degrading_packages(window)
        stagnant = self.stagnant_packages(min_cycles=min(3, len(recent)))

        return MaturityTrend(
            timestamps=timestamps,
            levels=levels,
            by_package_trends=by_pkg,
            improving_packages=improving,
            degrading_packages=degrading,
            stagnant_packages=stagnant,
        )

    def improving_packages(self, window: int = 10) -> list[str]:
        """Return package IDs whose level increased in the window.

        Parameters
        ----------
        window:
            Number of most recent assessments.

        Returns
        -------
        list[str]
        """
        recent = self._history[-window:]
        if len(recent) < 2:
            return []
        first = recent[0]
        last = recent[-1]
        improving: list[str] = []
        all_pkgs = set(first.by_package) | set(last.by_package)
        for pkg in sorted(all_pkgs):
            before_lvl = int(first.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
            after_lvl = int(last.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
            if after_lvl > before_lvl:
                improving.append(pkg)
        return improving

    def degrading_packages(self, window: int = 10) -> list[str]:
        """Return package IDs whose level decreased in the window.

        Parameters
        ----------
        window:
            Number of most recent assessments.

        Returns
        -------
        list[str]
        """
        recent = self._history[-window:]
        if len(recent) < 2:
            return []
        first = recent[0]
        last = recent[-1]
        degrading: list[str] = []
        all_pkgs = set(first.by_package) | set(last.by_package)
        for pkg in sorted(all_pkgs):
            before_lvl = int(first.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
            after_lvl = int(last.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
            if after_lvl < before_lvl:
                degrading.append(pkg)
        return degrading

    def stagnant_packages(self, min_cycles: int = 3) -> list[str]:
        """Return package IDs with no level improvement in the last N cycles.

        Parameters
        ----------
        min_cycles:
            Minimum number of consecutive cycles without improvement.

        Returns
        -------
        list[str]
        """
        recent = self._history[-min_cycles:]
        if len(recent) < min_cycles:
            return []
        all_pkgs: set[str] = set()
        for a in recent:
            all_pkgs.update(a.by_package.keys())
        stagnant: list[str] = []
        for pkg in sorted(all_pkgs):
            levels = [
                int(a.by_package.get(pkg, MaturityLevel.LEVEL_0_RAW))
                for a in recent
            ]
            # Stagnant = no change across the window (first == last)
            if levels[0] == levels[-1]:
                stagnant.append(pkg)
        return stagnant

    def full_report(
        self,
        assessment: MaturityAssessment,
        schedule: CyclicSchedule,
        target_level: Optional[MaturityLevel] = None,
        current_cycle: Optional[ImprovementCycle] = None,
    ) -> MaturityReport:
        """Produce a ``MaturityReport`` combining all tracker data.

        Parameters
        ----------
        assessment:
            Most recent assessment.
        schedule:
            Cyclic schedule configuration.
        target_level:
            Desired target level for the improvement plan.
        current_cycle:
            Active improvement cycle, if any.

        Returns
        -------
        MaturityReport
        """
        self.record_assessment(assessment)
        trend = self.compute_trend()
        planner = ImprovementPlanner()
        if target_level is None:
            next_val = min(int(assessment.overall_level) + 1, 4)
            target_level = MaturityLevel(next_val)
        plan = planner.plan_improvement(assessment, target_level)
        return MaturityReport(
            assessment=assessment,
            trend=trend,
            current_cycle=current_cycle,
            plan=plan,
            schedule=schedule,
        )
