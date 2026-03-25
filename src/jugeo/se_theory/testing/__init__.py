r"""``jugeo.se_theory.testing`` — Testing as Witness Construction (B3).

Public re-exports of the key classes and functions from this subpackage.

Theory summary (JuGeo B3):
    * A *test* is a local evidence section at a coordinate in the site.
    * *Test adequacy* is a descent question: the local witnesses must glue.
    * *Regression testing* is re-descent at affected overlaps after a change.
    * *Coverage* is the fraction of the site that has been witnessed.
    * *Priority* is determined by coupling weight, trust deficit, and blast
      radius of each open obligation.

    copilot: se-theory-testing-init
"""
from __future__ import annotations

__all__ = [
    # Models
    "TestLevel",
    "ObligationStatus",
    "TestObligation",
    "TestResult",
    "WitnessSection",
    "CoverageReport",
    "RegressionScope",
    "TestPrioritization",
    "TestSuiteReport",
    "make_obligation",
    "make_result",
    # Algorithms
    "TestObligationGenerator",
    "WitnessConstructor",
    "CoverageAnalyzer",
    "TestPrioritizer",
    "RegressionAnalyzer",
    "TRUST_ORDER",
    "trust_rank",
    "higher_trust",
    # Integration
    "SiteTestAnalyzer",
    "EvidenceIntegrator",
    # Theorems
    "theorem_test_adequacy_is_descent",
    "theorem_regression_scope_is_minimal",
    "theorem_geometric_coverage_implies_logical_coverage",
    "theorem_trust_floor_monotone_under_testing",
    "theorem_hierarchical_testing_composes",
    "ALL_THEOREMS",
    "Theorem",
    "TheoremViolation",
]

try:
    from jugeo.se_theory.testing.models import (
        TestLevel,
        ObligationStatus,
        TestObligation,
        TestResult,
        WitnessSection,
        CoverageReport,
        RegressionScope,
        TestPrioritization,
        TestSuiteReport,
        make_obligation,
        make_result,
    )
except Exception:  # pragma: no cover
    pass

try:
    from jugeo.se_theory.testing.algorithms import (
        TestObligationGenerator,
        WitnessConstructor,
        CoverageAnalyzer,
        TestPrioritizer,
        RegressionAnalyzer,
        TRUST_ORDER,
        trust_rank,
        higher_trust,
    )
except Exception:  # pragma: no cover
    pass

try:
    from jugeo.se_theory.testing.integration import (
        SiteTestAnalyzer,
        EvidenceIntegrator,
    )
except Exception:  # pragma: no cover
    pass

try:
    from jugeo.se_theory.testing.theorems import (
        theorem_test_adequacy_is_descent,
        theorem_regression_scope_is_minimal,
        theorem_geometric_coverage_implies_logical_coverage,
        theorem_trust_floor_monotone_under_testing,
        theorem_hierarchical_testing_composes,
        ALL_THEOREMS,
        Theorem,
        TheoremViolation,
    )
except Exception:  # pragma: no cover
    pass
