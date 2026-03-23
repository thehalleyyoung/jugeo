r"""Cover design: patch selection, budget allocation, and completion tracking.

Theory (theory2.tex §42 — Cover Design):
    This package implements the cover design discipline that governs how the
    generation pipeline decides *which* patches to build, in *what order*, with
    *how much* budget, and when the overall cover is considered complete.

    A *cover design* is a tuple
    ``D = (P, B, Δ, Q, C, Π)`` where:

    * ``P``  — the ordered set of *patch descriptors*: each descriptor names a
      coordinate chart, the obligations the local section must satisfy, the
      treaty constraints on its boundary, and any dependency edges to other
      patches.
    * ``B``  — the *budget allocation* function ``B : P → ℝ₊``; it assigns an
      abstract cost envelope to every patch so that the total allocation never
      exceeds the plan budget ``μ``.
    * ``Δ``  — the *dependency graph* ``Δ ⊆ P × P``; a directed acyclic graph
      that encodes which patches must be completed before others may begin.
    * ``Q``  — the *quality metric* suite ``Q = {q_i : Evidence → [0,1]}``;
      each metric measures one dimension of section quality (e.g., coherence,
      coverage, treaty compliance).
    * ``C``  — the *completion criteria* ``C = (τ, σ)``; a quality threshold
      ``τ ∈ [0,1]`` and a strictness level ``σ ∈ {strict, lenient, advisory}``
      that together determine when a patch (and ultimately the whole cover) is
      declared done.
    * ``Π``  — the *parallelism strategy* ``Π``; determines which dependency-
      free batches of patches may be elaborated concurrently.

    The cover design loop drives the cycle::

        plan → order patches → allocate budget → run parallel batches
             → score quality → check completion → assemble design record

    Interface discipline is enforced between adjacent patches: every patch must
    export the boundary values expected by its downstream neighbours, and every
    import must match the corresponding upstream export.

    The copilot participates throughout: proposing initial patch candidates,
    estimating budget requirements, suggesting dependency hints, and explaining
    quality failures when patches fall below threshold.

    Section organisation
    --------------------
    ``cover_design_principles``
        Formalises the cover design axioms and invariants; provides the
        canonical definitions of the ``CoverDesignPlan`` type and the
        ``PatchDescriptor`` schema.
    ``patch_selection``
        Implements the patch selection engine: given a descriptor and a context,
        it iterates over candidate patches and selects the best one according to
        a composite score that weighs treaty compliance, budget consumption, and
        quality estimates.
    ``budget_allocation``
        Allocates the plan budget across patches using a strategy chosen from
        ``uniform``, ``proportional`` (weighted by obligation count), or
        ``adaptive`` (driven by copilot budget estimates).
    ``parallelism_strategy``
        Determines the parallel execution schedule.  Patches are grouped into
        dependency-free *batches*; within a batch, all patches may run
        concurrently up to the configured ``max_parallel_patches`` limit.
    ``dependency_ordering``
        Performs topological sort on the dependency graph ``Δ`` to produce an
        ordered list of batches.  Supports ``topological``,
        ``greedy_parallel``, and ``priority_weighted`` strategies.
    ``quality_metrics``
        Evaluates each quality metric in ``Q`` against the evidence produced by
        the patch selection engine and returns a composite quality score.
    ``completion_criteria``
        Applies the completion criteria ``(τ, σ)`` to the scored patch evidence
        and determines which obligations (if any) remain unresolved.
    ``integration``
        *Section-level* integration glue (distinct from ``integration.py``):
        wires sections s01–s07 together and provides the canonical
        ``run_cover_design_section`` entry point used by the integration layer.

copilot: cover-design-init
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.models import (
        # Enums
        DesignStatus,
        PatchStatus,
        AllocationStrategy,
        ParallelismMode,
        # Exceptions
        CoverDesignError,
        PatchSelectionError,
        BudgetExhaustedError,
        QualityFailureError,
        CompletionFailureError,
        # Core dataclasses
        CoverDesignPlan,
        PatchDescriptor,
        DesignBudget,
        QualityTarget,
        CompletionCriteria,
        PatchResult,
        CoverDesignResult,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.algorithms import (
        run_cover_design_plan,
        select_best_patch,
        allocate_budget,
        compute_dependency_order,
        run_parallel_batch,
        score_patch_quality,
        check_completion_criteria,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Theorems
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.theorems import (
        TheoremResult,
        TheoremSuite,
        run_all_theorems,
        verify_cover_design_plan_consistency,
        verify_patch_selection_correctness,
        verify_budget_allocation_soundness,
        verify_dependency_ordering_acyclicity,
        verify_parallelism_strategy_safety,
        verify_quality_metrics_calibration,
        verify_completion_criteria_termination,
        verify_copilot_proposal_safety,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.manifest import (
        PackageManifest,
        FileManifest,
        ManifestRegistry,
        ManifestDiagnostics,
        ManifestError,
        PACKAGE_MANIFEST,
        FILE_MANIFESTS,
        get_manifest,
        get_file_manifest,
        validate_package_structure,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.integration import (
        CoverDesignIntegration,
        CopilotCoverDesignAdapter,
        CoverDesignPipelineAdapter,
        DesignRecord,
        DesignPipelineState,
        DesignIntegrationConfig,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Section engines
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.cover_design.cover_design_principles import (
        CoverDesignPrinciplesEngine,
        CoverDesignAxiom,
        PlanValidator,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.patch_selection import (
        PatchSelectionEngine,
        PatchCandidate,
        SelectionRecord,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.budget_allocation import (
        BudgetAllocationEngine,
        BudgetEnvelope,
        AllocationRecord,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.parallelism_strategy import (
        ParallelismStrategyEngine,
        ExecutionBatch,
        ParallelismRecord,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.dependency_ordering import (
        DependencyOrderingEngine,
        DependencyGraph,
        OrderingRecord,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.quality_metrics import (
        QualityMetricsEngine,
        QualityScore,
        MetricRecord,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.completion_criteria import (
        CompletionCriteriaEngine,
        CompletionReport,
        ResidualObligation,
    )
except Exception:  # noqa: BLE001
    pass

try:
    from jugeo.generation.cover_design.integration import (
        run_cover_design_section,
        CoverDesignSectionResult,
        SectionIntegrationRecord,
    )
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------------------
# Cross-subsystem cover design helpers
# ---------------------------------------------------------------------------


def cover_from_site(site: object) -> dict:
    """Build a cover design seeded by a geometric site.

    Uses :mod:`jugeo.geometry.covers` to enumerate the canonical open
    sets of *site* and returns a preliminary cover layout that the
    design engine can refine.
    """
    try:
        from jugeo.geometry.covers import enumerate_covers  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        enumerate_covers = None

    if enumerate_covers is not None:
        raw_covers = enumerate_covers(site)
    else:
        raw_covers = getattr(site, "covers", [])

    return {
        "site": site,
        "covers": list(raw_covers),
        "source": "jugeo.geometry.covers",
    }


def judgment_guided_cover(sections: object) -> dict:
    """Produce a cover guided by judgment-section analysis.

    Consults :mod:`jugeo.judgments.sections` to determine which
    sections carry unresolved obligations, then returns a cover
    plan that prioritises those sections.
    """
    try:
        from jugeo.judgments.sections import analyze_sections  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        analyze_sections = None

    if analyze_sections is not None:
        analysis = analyze_sections(sections)
    else:
        analysis = {"unresolved": [], "resolved": []}

    return {
        "sections": sections,
        "analysis": analysis,
        "source": "jugeo.judgments.sections",
    }


__all__: list[str] = [
    # ---- Models ----
    "DesignStatus",
    "PatchStatus",
    "AllocationStrategy",
    "ParallelismMode",
    "CoverDesignError",
    "PatchSelectionError",
    "BudgetExhaustedError",
    "QualityFailureError",
    "CompletionFailureError",
    "CoverDesignPlan",
    "PatchDescriptor",
    "DesignBudget",
    "QualityTarget",
    "CompletionCriteria",
    "PatchResult",
    "CoverDesignResult",
    # ---- Algorithms ----
    "run_cover_design_plan",
    "select_best_patch",
    "allocate_budget",
    "compute_dependency_order",
    "run_parallel_batch",
    "score_patch_quality",
    "check_completion_criteria",
    # ---- Theorems ----
    "TheoremResult",
    "TheoremSuite",
    "run_all_theorems",
    "verify_cover_design_plan_consistency",
    "verify_patch_selection_correctness",
    "verify_budget_allocation_soundness",
    "verify_dependency_ordering_acyclicity",
    "verify_parallelism_strategy_safety",
    "verify_quality_metrics_calibration",
    "verify_completion_criteria_termination",
    "verify_copilot_proposal_safety",
    # ---- Manifest ----
    "PackageManifest",
    "FileManifest",
    "ManifestRegistry",
    "ManifestDiagnostics",
    "ManifestError",
    "PACKAGE_MANIFEST",
    "FILE_MANIFESTS",
    "get_manifest",
    "get_file_manifest",
    "validate_package_structure",
    # ---- Integration ----
    "CoverDesignIntegration",
    "CopilotCoverDesignAdapter",
    "CoverDesignPipelineAdapter",
    "DesignRecord",
    "DesignPipelineState",
    "DesignIntegrationConfig",
    # ---- Section engines ----
    "CoverDesignPrinciplesEngine",
    "CoverDesignAxiom",
    "PlanValidator",
    "PatchSelectionEngine",
    "PatchCandidate",
    "SelectionRecord",
    "BudgetAllocationEngine",
    "BudgetEnvelope",
    "AllocationRecord",
    "ParallelismStrategyEngine",
    "ExecutionBatch",
    "ParallelismRecord",
    "DependencyOrderingEngine",
    "DependencyGraph",
    "OrderingRecord",
    "QualityMetricsEngine",
    "QualityScore",
    "MetricRecord",
    "CompletionCriteriaEngine",
    "CompletionReport",
    "ResidualObligation",
    "run_cover_design_section",
    "CoverDesignSectionResult",
    "SectionIntegrationRecord",
    # ---- Cross-subsystem helpers ----
    "cover_from_site",
    "judgment_guided_cover",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import budget_allocation
except Exception:
    pass
try:
    from . import completion_criteria
except Exception:
    pass
try:
    from . import cover_design_principles
except Exception:
    pass
try:
    from . import dependency_ordering
except Exception:
    pass
try:
    from . import initial_cover_synthesis_obligation
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import module_boundaries_overlap_quality
except Exception:
    pass
try:
    from . import parallelism_strategy
except Exception:
    pass
try:
    from . import patch_selection
except Exception:
    pass
try:
    from . import quality_metrics
except Exception:
    pass
try:
    from . import s08_integration
except Exception:
    pass
try:
    from . import semantic_decomposition_criteria_co
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
