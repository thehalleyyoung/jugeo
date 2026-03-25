"""Continuous Maturity module for jugeo.se_theory (B10).

Theory (JuGeo — "Continuous Maturity as Sheaf Descent", B10):
    Maturity levels track the degree to which the evidence sheaf satisfies
    the descent condition across the coordinate site.  Each level corresponds
    to progressively stronger gluing data:

    * Level 0 — RAW: code exists but no evidence sections.
    * Level 1 — LOCAL_EVIDENCE: local witnesses exist at individual coordinates.
    * Level 2 — LOCAL_DESCENT: local witnesses glue within packages.
    * Level 3 — GLOBAL_DESCENT: cross-package gluing verified via morphisms.
    * Level 4 — CERTIFIED: full proof-carrying certificates across the site.

    The improvement cycle drives the site from lower to higher levels through
    ASSESS → PRIORITIZE → REPAIR → CERTIFY → COMPLETE phases.
"""
from __future__ import annotations

from jugeo.se_theory.maturity.models import (
    CyclicSchedule,
    ImprovementCycle,
    ImprovementPlan,
    MaturityAssessment,
    MaturityCriterion,
    MaturityLevel,
    MaturityReport,
    MaturityTrend,
)
from jugeo.se_theory.maturity.algorithms import (
    CycleManager,
    ImprovementPlanner,
    MaturityAssessor,
    MaturityTracker,
)
from jugeo.se_theory.maturity.integration import SiteMaturityAnalyzer

__all__ = [
    # models
    "MaturityLevel",
    "MaturityCriterion",
    "MaturityAssessment",
    "ImprovementCycle",
    "ImprovementPlan",
    "MaturityTrend",
    "CyclicSchedule",
    "MaturityReport",
    # algorithms
    "MaturityAssessor",
    "ImprovementPlanner",
    "CycleManager",
    "MaturityTracker",
    # integration
    "SiteMaturityAnalyzer",
]
