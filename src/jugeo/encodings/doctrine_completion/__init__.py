"""
doctrine_completion — JuGeo encoding package for the implementation-complete thesis doctrine.

This package is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides a complete encoding of the implementation-complete thesis doctrine from Ch37,
including models, manifests, evidence collection and validation utilities, completeness
analysis, core algorithms, the main doctrine checker, formal theorem statements and
proofs, and a runtime integration layer.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

Key exports:
- ``DoctrineStatement``          — a single claim in the doctrine
- ``ImplementationEvidence``     — a piece of evidence grounding a claim
- ``DoctrineChecker``            — main checker orchestrator
- ``DoctrineCompletionReport``   — aggregated completeness report
- ``DoctrineCompletionManifest`` — manifest for this encoding
- ``DoctrineTheoremRegistry``    — registry of Ch37 formal theorems
- ``DoctrineCompletionPipeline`` — full evaluation pipeline

Quick-start example::

    from jugeo.encodings.doctrine_completion import (
        DoctrineStatement, ImplementationEvidence,
        ClaimType, EvidenceKind, check_doctrine_completeness,
    )
    stmt = DoctrineStatement.create(
        claim_text="Every module has a test suite.",
        claim_type=ClaimType.STRUCTURAL,
        coordinate_key="module:test:1",
        required_evidence_kinds=[EvidenceKind.CODE, EvidenceKind.TEST],
    )
    ev = ImplementationEvidence.create(
        statement_id=stmt.statement_id,
        evidence_kind=EvidenceKind.TEST,
        artifact_ref="tests/test_module.py",
        confidence=0.9,
        grounding_depth=2,
    )
    report = check_doctrine_completeness([stmt], {stmt.statement_id: [ev]})
    print(report.summarize())

copilot
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Re-export from submodules
# ---------------------------------------------------------------------------

from .models import (
    ClaimType,
    StatementStatus,
    EvidenceKind,
    GapSeverity,
    DoctrineStatement,
    ImplementationEvidence,
    CompletenessCheck,
    DoctrineGap,
    DoctrineCompletionReport,
    ClaimGroundingMap,
    EvidenceRequirement,
)

from .manifest import (
    DoctrineCompletionManifest,
    DoctrineDescriptor,
    DoctrineRegistry,
    build_manifest,
    validate_manifest,
)

from .implementation_evidence import (
    EvidenceKind as ExtendedEvidenceKind,
    EvidenceChain,
    EvidenceCollector,
    EvidenceValidator,
    EvidenceAggregator,
    ArtifactResolver,
    ConfidenceEstimator,
    collect_evidence_for,
    validate_evidence_chain,
)

from .completeness import (
    CompletionStrategy,
    CompletenessMetrics,
    CompletenessAnalyzer,
    CriticalPathAnalyzer,
    DoctrineGraph,
    CompletionPlan,
    GapBridger,
    compute_completion_metrics,
    plan_completion,
)

from .algorithms import (
    GroundingAlgorithm,
    GapFindingAlgorithm,
    CoverageComputationAlgorithm,
    EvidenceSynthesisAlgorithm,
    ClaimPropagationAlgorithm,
    DoctrineMinimizationAlgorithm,
    IncrementalCheckAlgorithm,
    RiskAssessmentAlgorithm,
)

from .doctrine_checker import (
    DoctrineChecker,
    GroundingVerifier,
    CoverageAnalyzer,
    GapPrioritizer,
    DoctrineAuditor,
    check_doctrine_completeness,
    quick_check,
)

from .theorems import (
    DoctrineTheorem,
    TheoremStatement,
    DoctrineTheoremRegistry,
    ImplementationCompletenessProof,
    GroundingSoundnessProof,
    CoverageAdequacyProof,
    verify_doctrine_theorem,
    check_all_doctrine_theorems,
)

from .integration import (
    IntegrationHealth,
    DoctrineCompletionIntegration,
    ManifestDoctrineLinker,
    RuntimeDoctrineMonitor,
    EvidenceArchiveAdapter,
    DoctrineCompletionPipeline,
    run_integration_test,
)

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__: str = "1.0.0"
__author__: str = "JuGeo / copilot"
__chapter_ref__: str = "Ch37"
__theory_ref__: str = "theory2.tex Chapter 37 — Implementation-Complete Thesis Doctrine"

__all__: list[str] = [
    # models
    "ClaimType",
    "StatementStatus",
    "EvidenceKind",
    "GapSeverity",
    "DoctrineStatement",
    "ImplementationEvidence",
    "CompletenessCheck",
    "DoctrineGap",
    "DoctrineCompletionReport",
    "ClaimGroundingMap",
    "EvidenceRequirement",
    # manifest
    "DoctrineCompletionManifest",
    "DoctrineDescriptor",
    "DoctrineRegistry",
    "build_manifest",
    "validate_manifest",
    # implementation_evidence
    "ExtendedEvidenceKind",
    "EvidenceChain",
    "EvidenceCollector",
    "EvidenceValidator",
    "EvidenceAggregator",
    "ArtifactResolver",
    "ConfidenceEstimator",
    "collect_evidence_for",
    "validate_evidence_chain",
    # completeness
    "CompletionStrategy",
    "CompletenessMetrics",
    "CompletenessAnalyzer",
    "CriticalPathAnalyzer",
    "DoctrineGraph",
    "CompletionPlan",
    "GapBridger",
    "compute_completion_metrics",
    "plan_completion",
    # algorithms
    "GroundingAlgorithm",
    "GapFindingAlgorithm",
    "CoverageComputationAlgorithm",
    "EvidenceSynthesisAlgorithm",
    "ClaimPropagationAlgorithm",
    "DoctrineMinimizationAlgorithm",
    "IncrementalCheckAlgorithm",
    "RiskAssessmentAlgorithm",
    # doctrine_checker
    "DoctrineChecker",
    "GroundingVerifier",
    "CoverageAnalyzer",
    "GapPrioritizer",
    "DoctrineAuditor",
    "check_doctrine_completeness",
    "quick_check",
    # theorems
    "DoctrineTheorem",
    "TheoremStatement",
    "DoctrineTheoremRegistry",
    "ImplementationCompletenessProof",
    "GroundingSoundnessProof",
    "CoverageAdequacyProof",
    "verify_doctrine_theorem",
    "check_all_doctrine_theorems",
    # integration
    "IntegrationHealth",
    "DoctrineCompletionIntegration",
    "ManifestDoctrineLinker",
    "RuntimeDoctrineMonitor",
    "EvidenceArchiveAdapter",
    "DoctrineCompletionPipeline",
    "run_integration_test",
    # metadata
    "__version__",
    "__author__",
    "__chapter_ref__",
    "__theory_ref__",
    "get_package_info",
    "create_default_registry",
    "create_sample_doctrine",
    "run_doctrine_check",
    "build_evidence_map_from_archive",
    "verify_all_theorems",
    "get_coverage_summary",
    "find_all_gaps",
    "assess_risk",
    "create_completion_plan",
    # cross-subsystem integration
    "complete_from_foundations",
    # constants
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_GROUNDING_DEPTH",
    "DEFAULT_ADEQUACY_THRESHOLD",
    "ALL_THEOREMS",
    "EVIDENCE_KIND_DESCRIPTIONS",
    "CLAIM_TYPE_DESCRIPTIONS",
]


# ---------------------------------------------------------------------------
# Package-level utility functions
# ---------------------------------------------------------------------------


def get_package_info() -> dict[str, Any]:
    """Return a dictionary of package metadata.

    Provides version, author, chapter reference, and a catalogue of all
    exported symbols for introspection and documentation purposes.

    Returns:
        Dictionary with keys: version, author, chapter_ref, theory_ref,
        export_count, exports, generated_at.
    """
    return {
        "package": "jugeo.encodings.doctrine_completion",
        "version": __version__,
        "author": __author__,
        "chapter_ref": __chapter_ref__,
        "theory_ref": __theory_ref__,
        "export_count": len(__all__),
        "exports": list(__all__),
        "description": (
            "Implementation-complete thesis doctrine encoding for JuGeo. "
            "Encodes Ch37 of theory2.tex: every claim has implementation evidence. "
            "Generated with copilot assistance."
        ),
        "generated_at": time.time(),
        "package_id": str(uuid.uuid4()),
    }


def create_default_registry() -> DoctrineTheoremRegistry:
    """Create and return the default theorem registry for Ch37.

    Delegates to DoctrineTheoremRegistry.build_default_registry() which
    pre-populates all six core theorems from the implementation-complete
    thesis doctrine.

    Returns:
        A DoctrineTheoremRegistry pre-populated with all 6 Ch37 theorems.

    Example::

        registry = create_default_registry()
        ts = registry.lookup(DoctrineTheorem.IMPLEMENTATION_COMPLETENESS)
        print(ts.summarize())
    """
    return DoctrineTheoremRegistry.build_default_registry()


def create_sample_doctrine(
    n_statements: int = 5,
    include_evidence: bool = True,
) -> tuple[list[DoctrineStatement], dict[str, list[ImplementationEvidence]]]:
    """Create a sample doctrine with synthetic statements and evidence.

    Useful for testing, documentation examples, and integration smoke-tests.
    Generates n_statements DoctrineStatements of varying ClaimType, each
    with associated ImplementationEvidence items when include_evidence is True.

    Args:
        n_statements: Number of statements to generate (default 5).
        include_evidence: Whether to generate evidence for each statement.

    Returns:
        A (statements, evidence_map) tuple.

    Example::

        stmts, ev_map = create_sample_doctrine(n_statements=3)
        report = check_doctrine_completeness(stmts, ev_map)
        print(report.summarize())
    """
    import itertools

    claim_types = list(ClaimType)
    evidence_kinds = [EvidenceKind.CODE, EvidenceKind.TEST, EvidenceKind.RUNTIME,
                      EvidenceKind.PROOF, EvidenceKind.BENCHMARK]

    statements: list[DoctrineStatement] = []
    evidence_map: dict[str, list[ImplementationEvidence]] = {}

    for i in range(n_statements):
        ctype = claim_types[i % len(claim_types)]
        n_required = (i % 3) + 1  # 1, 2, or 3 required kinds
        required_kinds = evidence_kinds[:n_required]

        stmt = DoctrineStatement.create(
            claim_text=(
                f"Sample doctrine statement #{i + 1}: "
                f"claims that the {ctype.value} aspect of the system "
                f"has been implemented and is evidenced by "
                f"{[k.value for k in required_kinds]}."
            ),
            claim_type=ctype,
            coordinate_key=f"sample:{ctype.value}:{i + 1}",
            required_evidence_kinds=required_kinds,
            metadata={"sample": True, "index": i},
        )
        statements.append(stmt)

        if include_evidence:
            evs: list[ImplementationEvidence] = []
            for kind in required_kinds:
                ev = ImplementationEvidence.create(
                    statement_id=stmt.statement_id,
                    evidence_kind=kind,
                    artifact_ref=f"sample://{kind.value}/{stmt.statement_id[:8]}",
                    confidence=0.75 + (i % 3) * 0.05,
                    grounding_depth=2,
                    author="sample_generator",
                    copilot_assisted=True,
                    metadata={"sample": True},
                )
                evs.append(ev)
            evidence_map[stmt.statement_id] = evs
        else:
            evidence_map[stmt.statement_id] = []

    return statements, evidence_map


def run_doctrine_check(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
    manifest: DoctrineCompletionManifest | None = None,
    verbose: bool = False,
) -> DoctrineCompletionReport:
    """Run a full doctrine check and optionally print a verbose summary.

    Convenience wrapper that creates a DoctrineCompletionPipeline (if a
    manifest is provided) or falls back to check_doctrine_completeness()
    for a quick check.

    Args:
        statements: All doctrine statements to evaluate.
        evidence_map: Mapping from statement_id to evidence list.
        manifest: Optional manifest for pipeline-based evaluation.
        verbose: If True, prints a summary to stdout.

    Returns:
        A DoctrineCompletionReport.

    Example::

        stmts, ev_map = create_sample_doctrine()
        report = run_doctrine_check(stmts, ev_map, verbose=True)
    """
    if manifest is not None:
        pipeline = DoctrineCompletionPipeline()
        report = pipeline.run(manifest=manifest, statements=statements, evidence_map=evidence_map)
    else:
        report = check_doctrine_completeness(statements, evidence_map)

    if verbose:
        print(report.summarize())
        for check in report.checks:
            print(f"  {check.summarize()}")

    return report


def build_evidence_map_from_archive(
    archive_data: dict[str, Any],
    statements: list[DoctrineStatement],
) -> dict[str, list[ImplementationEvidence]]:
    """Build an evidence map from an external archive dictionary.

    Uses EvidenceArchiveAdapter to translate the archive format into the
    standard evidence map expected by DoctrineChecker.

    Args:
        archive_data: External archive data dictionary.
        statements: All doctrine statements (needed to fill missing entries).

    Returns:
        Evidence map: statement_id -> list[ImplementationEvidence].

    Example::

        archive = {
            stmt_id: [{"kind": "code", "artifact_ref": "src/foo.py", "confidence": 0.8}]
        }
        ev_map = build_evidence_map_from_archive(archive, statements)
    """
    adapter = EvidenceArchiveAdapter(archive_data=archive_data)
    return adapter.sync(archive_data=archive_data, statements=statements)


def verify_all_theorems(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> dict[str, tuple[bool, str]]:
    """Verify all six Ch37 doctrine theorems and return results as a plain dict.

    Builds the default theorem registry, runs all theorem verifications,
    and returns results keyed by theorem name (string) rather than enum.

    Args:
        statements: All doctrine statements.
        evidence_map: Evidence map.

    Returns:
        Dictionary mapping theorem name (str) to (holds, explanation).

    Example::

        results = verify_all_theorems(stmts, ev_map)
        for name, (holds, reason) in results.items():
            print(f"{name}: {'HOLDS' if holds else 'FAILS'} — {reason[:60]}")
    """
    registry = create_default_registry()
    raw_results = check_all_doctrine_theorems(
        registry=registry,
        statements=statements,
        evidence_map=evidence_map,
    )
    return {
        theorem.value: result
        for theorem, result in raw_results.items()
    }


def get_coverage_summary(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> dict[str, Any]:
    """Compute and return a coverage summary for the given doctrine data.

    Runs CompletenessAnalyzer with the EXHAUSTIVE strategy and packages
    the metrics into a plain dictionary.

    Args:
        statements: All doctrine statements.
        evidence_map: Evidence map.

    Returns:
        Dictionary with coverage, confidence, depth, breadth, overall_score,
        statement_count, evidence_count, and adequacy flag.
    """
    analyzer = CompletenessAnalyzer(strategy=CompletionStrategy.EXHAUSTIVE)
    metrics = analyzer.analyze(statements, evidence_map)
    return {
        "coverage": metrics.coverage,
        "confidence": metrics.confidence,
        "depth": metrics.depth,
        "breadth": metrics.breadth,
        "overall_score": metrics.overall_score(),
        "statement_count": metrics.statement_count,
        "evidence_count": metrics.evidence_count,
        "is_adequate": metrics.is_adequate(),
        "strategy": metrics.strategy_used.value,
        "computed_at": time.time(),
    }


def find_all_gaps(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
    prioritize: bool = True,
) -> list[DoctrineGap]:
    """Find all evidence gaps in the doctrine and optionally prioritize them.

    Uses GapFindingAlgorithm to locate all unsatisfied evidence requirements,
    then optionally sorts them by severity using GapPrioritizer.

    Args:
        statements: All doctrine statements.
        evidence_map: Evidence map.
        prioritize: If True, sort gaps by severity (default True).

    Returns:
        List of DoctrineGap instances.
    """
    from .algorithms import GapFindingAlgorithm
    algo = GapFindingAlgorithm()
    gaps = algo.find_all_gaps(statements, evidence_map)
    if prioritize:
        prioritizer = GapPrioritizer()
        gaps = prioritizer.prioritize(gaps)
    return gaps


def assess_risk(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> list[dict[str, Any]]:
    """Assess risk for all doctrine statements.

    Uses RiskAssessmentAlgorithm to compute risk scores and returns
    the results sorted by descending risk score.

    Args:
        statements: All doctrine statements.
        evidence_map: Evidence map.

    Returns:
        List of risk assessment dictionaries, sorted by risk_score descending.
    """
    algo = RiskAssessmentAlgorithm()
    results = algo.assess_all(statements, evidence_map)
    return sorted(results, key=lambda r: -r.get("risk_score", 0.0))


def create_completion_plan(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
    max_effort: float = 100.0,
) -> CompletionPlan:
    """Create a prioritised completion plan for closing all doctrine gaps.

    Finds all gaps, estimates bridge effort, and assembles a CompletionPlan
    constrained by the max_effort budget.

    Args:
        statements: All doctrine statements.
        evidence_map: Evidence map.
        max_effort: Maximum total effort budget for the plan (default 100.0).

    Returns:
        A CompletionPlan with prioritised steps.
    """
    gaps = find_all_gaps(statements, evidence_map, prioritize=True)
    return plan_completion(gaps, resources={"max_effort": max_effort})


# ---------------------------------------------------------------------------
# Module-level convenience constants
# ---------------------------------------------------------------------------

#: Default confidence threshold used throughout the package.
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.7

#: Default grounding depth threshold used in soundness checks.
DEFAULT_GROUNDING_DEPTH: int = 2

#: Default coverage adequacy threshold from Ch37.
DEFAULT_ADEQUACY_THRESHOLD: float = 0.85

#: Ordered list of all six DoctrineTheorem enum values (for iteration).
ALL_THEOREMS: list[DoctrineTheorem] = list(DoctrineTheorem)

#: Mapping from EvidenceKind to a human-readable description.
EVIDENCE_KIND_DESCRIPTIONS: dict[str, str] = {
    EvidenceKind.CODE.value: "Direct source code artefact (module, class, function)",
    EvidenceKind.TEST.value: "Automated test or test suite",
    EvidenceKind.RUNTIME.value: "Runtime observation, trace, or execution log",
    EvidenceKind.PROOF.value: "Formal proof or mechanised verification certificate",
    EvidenceKind.ORACLE.value: "Oracle-based or property-based test result",
    EvidenceKind.BENCHMARK.value: "Performance measurement or benchmark result",
    EvidenceKind.HUMAN_REVIEW.value: "Signed-off human review artefact",
}

#: Mapping from ClaimType to a human-readable description.
CLAIM_TYPE_DESCRIPTIONS: dict[str, str] = {
    ClaimType.STRUCTURAL.value: "Claims about structural relationships between components",
    ClaimType.BEHAVIORAL.value: "Claims about the dynamic behaviour of the system",
    ClaimType.RELATIONAL.value: "Claims about inter-entity relations in the model",
    ClaimType.RESOURCE.value: "Claims about resource usage and capacity",
    ClaimType.SEMANTIC.value: "Claims about the meaning or interpretation of constructs",
}


# ---------------------------------------------------------------------------
# Cross-subsystem integration — formal core foundations
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.formal_core import (  # type: ignore[import]
        FormalSite,
        TrustAlgebraAxioms,
        ObstructionTheory,
    )
    _formal_core_available = True
except ImportError:
    FormalSite = None  # type: ignore[assignment]
    TrustAlgebraAxioms = None  # type: ignore[assignment]
    ObstructionTheory = None  # type: ignore[assignment]
    _formal_core_available = False


def complete_from_foundations(
    statements: list[DoctrineStatement] | None = None,
    evidence_map: dict[str, list[ImplementationEvidence]] | None = None,
) -> dict[str, Any]:
    """Check doctrine completeness against the formal core foundations.

    Verifies that every axiom and structural claim from
    ``jugeo.foundations.formal_core`` (Chapter 9 — the mathematical
    interlude) has a corresponding doctrine statement backed by
    implementation evidence.  This ensures the implementation-complete
    thesis truly covers the formal foundations.

    When *statements* and *evidence_map* are ``None``, the function
    creates a sample doctrine via ``create_sample_doctrine()`` for
    demonstration purposes.

    Parameters
    ----------
    statements:
        Doctrine statements to check.  Defaults to sample statements.
    evidence_map:
        Evidence map keyed by statement ID.  Defaults to sample evidence.

    Returns
    -------
    dict[str, Any]
        Result dictionary with keys:

        ``formal_core_available`` : bool
            Whether the formal core subsystem could be imported.
        ``axiom_coverage`` : dict[str, bool]
            Per-axiom coverage status (axiom name → covered).
        ``overall_complete`` : bool
            Whether all formal-core axioms are covered.
        ``doctrine_report`` : DoctrineCompletionReport | None
            Full doctrine report when statements are provided.
        ``gaps`` : list[str]
            Names of axioms without evidence coverage.
    """
    result: dict[str, Any] = {
        "formal_core_available": _formal_core_available,
        "axiom_coverage": {},
        "overall_complete": False,
        "doctrine_report": None,
        "gaps": [],
    }

    if statements is None or evidence_map is None:
        statements, evidence_map = create_sample_doctrine()

    # Run the standard doctrine check
    try:
        report = check_doctrine_completeness(statements, evidence_map)
        result["doctrine_report"] = report
    except Exception as exc:
        result["doctrine_report_error"] = str(exc)
        report = None

    if not _formal_core_available:
        result["gaps"].append("formal_core subsystem not available")
        return result

    # Extract axioms from the formal core and check coverage
    formal_axioms: list[str] = []
    if FormalSite is not None:
        try:
            site = FormalSite()
            axioms = getattr(site, "axiom_names", None)
            if callable(axioms):
                axioms = axioms()
            if axioms:
                formal_axioms.extend(axioms)
        except Exception:
            pass

    if TrustAlgebraAxioms is not None:
        try:
            ta = TrustAlgebraAxioms()
            axioms = getattr(ta, "axiom_names", None)
            if callable(axioms):
                axioms = axioms()
            if axioms:
                formal_axioms.extend(axioms)
        except Exception:
            pass

    if ObstructionTheory is not None:
        try:
            ot = ObstructionTheory()
            axioms = getattr(ot, "axiom_names", None)
            if callable(axioms):
                axioms = axioms()
            if axioms:
                formal_axioms.extend(axioms)
        except Exception:
            pass

    # Match axioms against doctrine statements
    stmt_texts = {s.claim_text.lower() for s in statements} if statements else set()
    stmt_keys = {
        getattr(s, "coordinate_key", "").lower()
        for s in (statements or [])
    }

    for axiom in formal_axioms:
        axiom_lower = axiom.lower()
        covered = any(
            axiom_lower in txt or axiom_lower in key
            for txt in stmt_texts
            for key in stmt_keys
        )
        result["axiom_coverage"][axiom] = covered
        if not covered:
            result["gaps"].append(axiom)

    result["overall_complete"] = len(result["gaps"]) == 0 and (
        report is not None
        and getattr(report, "is_complete", lambda: False)()
        if hasattr(report, "is_complete")
        else len(result["gaps"]) == 0
    )

    return result

