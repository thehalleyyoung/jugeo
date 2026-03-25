r"""Integration helpers for the ``jugeo.se_theory.maturity`` package.

Theory (JuGeo — "Continuous Maturity as Sheaf Descent", B10):
    Integration provides a high-level facade over the maturity algorithms,
    accepting "site data" dictionaries and returning maturity artefacts.

    ``SiteMaturityAnalyzer`` is the primary entry-point for consumers that
    have a raw site-data dict (as produced by a JuGeo site scanner) and
    want maturity analysis without constructing the individual algorithm
    objects by hand.

    copilot: se-theory-maturity-integration
"""
from __future__ import annotations

from typing import Any, Optional

from jugeo.se_theory.maturity.algorithms import (
    CycleManager,
    ImprovementPlanner,
    MaturityAssessor,
    MaturityTracker,
)
from jugeo.se_theory.maturity.models import (
    CyclicSchedule,
    ImprovementCycle,
    ImprovementPlan,
    MaturityAssessment,
    MaturityLevel,
    MaturityReport,
    _iso_now,
)

__all__ = ["SiteMaturityAnalyzer"]


# ---------------------------------------------------------------------------
# SiteMaturityAnalyzer
# ---------------------------------------------------------------------------


class SiteMaturityAnalyzer:
    """High-level facade for maturity analysis of a JuGeo site.

    All methods accept a ``site_data`` dictionary with the following
    optional keys:

    * ``"coordinates"``  — ``list[str]`` of coordinate IDs
    * ``"evidence"``     — ``list[dict]`` of evidence section records
    * ``"obstructions"`` — ``list[dict]`` of open obstruction records
    * ``"covers"``       — ``list[dict]`` of cover membership records
    * ``"morphisms"``    — ``list[dict]`` of cross-package morphisms
    * ``"certificates"`` — ``list[dict]`` of certificate records
    * ``"site_id"``      — ``str`` identifier for the site
    * ``"schedule"``     — ``dict`` matching ``CyclicSchedule.to_dict()``
    * ``"target_level"`` — ``int`` desired maturity level
    """

    def __init__(self) -> None:
        self._tracker = MaturityTracker()
        self._assessor = MaturityAssessor()
        self._planner = ImprovementPlanner()
        self._cycle_mgr = CycleManager()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_maturity(self, site_data: dict[str, Any]) -> MaturityReport:
        """Perform a full maturity analysis and return a ``MaturityReport``.

        Parameters
        ----------
        site_data:
            Site data dictionary (see class docstring for keys).

        Returns
        -------
        MaturityReport
        """
        assessment = self._run_assessment(site_data)
        schedule = self._extract_schedule(site_data)
        target = self._extract_target_level(site_data, assessment)
        return self._tracker.full_report(
            assessment=assessment,
            schedule=schedule,
            target_level=target,
        )

    def suggest_improvements(
        self, site_data: dict[str, Any]
    ) -> ImprovementPlan:
        """Suggest an improvement plan for advancing the site's maturity.

        Parameters
        ----------
        site_data:
            Site data dictionary.

        Returns
        -------
        ImprovementPlan
        """
        assessment = self._run_assessment(site_data)
        target = self._extract_target_level(site_data, assessment)
        return self._planner.plan_improvement(assessment, target)

    def run_improvement_cycle(
        self, site_data: dict[str, Any]
    ) -> ImprovementCycle:
        """Run a full automated improvement cycle for the site.

        Parameters
        ----------
        site_data:
            Site data dictionary.

        Returns
        -------
        ImprovementCycle
            Completed cycle (phase ``"COMPLETE"``).
        """
        coords = site_data.get("coordinates", [])
        evidence = site_data.get("evidence", [])
        obstructions = site_data.get("obstructions", [])
        covers = site_data.get("covers", [])
        morphisms = site_data.get("morphisms", [])
        certificates = site_data.get("certificates", [])
        site_id = site_data.get("site_id", "default")
        return self._cycle_mgr.auto_cycle(
            coordinates=coords,
            evidence=evidence,
            obstructions=obstructions,
            covers=covers,
            morphisms=morphisms,
            certificates=certificates,
            site_id=site_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_assessment(
        self, site_data: dict[str, Any]
    ) -> MaturityAssessment:
        """Run a maturity assessment from raw site data."""
        return self._assessor.assess(
            coordinates=site_data.get("coordinates", []),
            evidence=site_data.get("evidence", []),
            obstructions=site_data.get("obstructions", []),
            covers=site_data.get("covers", []),
            morphisms=site_data.get("morphisms", []),
            certificates=site_data.get("certificates", []),
            site_id=site_data.get("site_id", "default"),
        )

    def _extract_schedule(
        self, site_data: dict[str, Any]
    ) -> CyclicSchedule:
        """Extract or construct a ``CyclicSchedule`` from site data."""
        raw = site_data.get("schedule")
        if raw and isinstance(raw, dict):
            return CyclicSchedule.from_dict(raw)
        return CyclicSchedule(
            frequency="WEEKLY",
            next_cycle_at=_iso_now(),
        )

    def _extract_target_level(
        self,
        site_data: dict[str, Any],
        assessment: MaturityAssessment,
    ) -> MaturityLevel:
        """Determine the target maturity level from site data or defaults."""
        raw = site_data.get("target_level")
        if raw is not None:
            try:
                return MaturityLevel(int(raw))
            except (ValueError, KeyError):
                pass
        # Default: one level above current
        next_val = min(int(assessment.overall_level) + 1, 4)
        return MaturityLevel(next_val)
