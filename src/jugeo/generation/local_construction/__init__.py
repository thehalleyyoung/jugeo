r"""Local construction loops, interface discipline, and coordinated elaboration.

Theory (theory2.tex §39 — Local construction):
    This package implements the inner verification loop that produces an
    inhabitant from a goal.  The local construction loop drives the cycle::

        goal → candidates → select → verify → propagate

    Each goal is a tuple ``g_u = (u, Γ_u, Λ_u, Σ_u, Ω_u, T_∂u, μ_u)``
    where ``u`` is the coordinate, ``Γ`` is the context, ``Λ`` the laws,
    ``Σ`` the section space, ``Ω`` the obligations, ``T_∂u`` the interface
    treaty, and ``μ`` the budget.

    Interface discipline ensures that every local section exposes the
    correct boundary values agreed in the treaty ``T_∂u``.  Coordinated
    elaboration synchronises parallel loops sharing a common boundary.

    The copilot participates throughout: proposing candidates, evaluating
    feasibility, mediating interface negotiations, and adapting strategy
    based on construction feedback.

copilot: local-construction-init
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.models import (
    # Enums
    LoopStatus,
    StrictnessLevel,
    GenerationMethod,
    # Exceptions
    LocalConstructionError,
    InterfaceBreachError,
    BudgetExhaustedError,
    ConvergenceFailureError,
    # Core dataclasses
    LocalConstructionLoop,
    InterfaceDiscipline,
    CoordinatedElaboration,
    CandidateSet,
)

# ---------------------------------------------------------------------------
# Section engines
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.local_construction_loop import (
    LocalConstructionLoopEngine,
)
from jugeo.generation.local_construction.interface_discipline import (
    InterfaceDisciplineEnforcer,
    InterfaceBreach,
    NegotiationRecord,
)
from jugeo.generation.local_construction.coordinated_elaboration import (
    CoordinatedElaborationEngine,
    ElaborationSchedule,
    CoordinationConflict,
)
from jugeo.generation.local_construction.copilot_in_construction import (
    CopilotConstructionParticipant,
    CopilotProposal,
    CopilotNegotiationRecord,
    CopilotStrategyState,
    StrategyAdaptation,
)

# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.algorithms import (
    run_local_construction_loop,
    propose_candidates,
    select_best_candidate,
    verify_candidate,
    propagate_obligations,
    coordinate_interfaces,
)

# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.integration import (
    LocalConstructionIntegration,
    PipelineState,
    ConstructionRecord,
    IntegrationConfig,
)

# ---------------------------------------------------------------------------
# Theorems
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.theorems import (
    TheoremResult,
    TheoremSuite,
    run_all_theorems,
    verify_construction_loop_termination,
    verify_interface_discipline_soundness,
    verify_coordinated_elaboration_consistency,
    verify_candidate_selection_correctness,
    verify_obligation_propagation_completeness,
    verify_copilot_proposal_safety,
    verify_interface_negotiation_convergence,
    verify_semantic_compression_record_correctness,
)

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
from jugeo.generation.local_construction.manifest import (
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

# ---------------------------------------------------------------------------
# Cross-subsystem construction helpers
# ---------------------------------------------------------------------------


def local_section_construction(coordinate: object) -> dict:
    """Construct a local section for a geometric coordinate.

    Combines :mod:`jugeo.geometry.site` (to resolve the coordinate's
    site context) with :mod:`jugeo.judgments.sections` (to identify
    the obligations that the section must satisfy).
    """
    try:
        from jugeo.geometry.site import resolve_site  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        resolve_site = None

    try:
        from jugeo.judgments.sections import get_obligations  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_obligations = None

    site = resolve_site(coordinate) if resolve_site is not None else getattr(coordinate, "site", None)
    obligations = get_obligations(coordinate) if get_obligations is not None else []

    return {
        "coordinate": coordinate,
        "site": site,
        "obligations": list(obligations),
        "source": "jugeo.geometry.site + jugeo.judgments.sections",
    }


def solver_verified_construction(candidate: object) -> dict:
    """Verify a construction candidate via the Z3 solver session.

    Delegates to :mod:`jugeo.solver.z3_session` to discharge the
    proof obligations attached to *candidate* and returns a
    verification summary.
    """
    try:
        from jugeo.solver.z3_session import verify_candidate as _z3_verify  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        _z3_verify = None

    if _z3_verify is not None:
        result = _z3_verify(candidate)
    else:
        result = {"verified": None, "reason": "z3_session unavailable"}

    return {
        "candidate": candidate,
        "verification": result,
        "source": "jugeo.solver.z3_session",
    }


__all__: list[str] = [
    # ---- Models ----
    "LoopStatus",
    "StrictnessLevel",
    "GenerationMethod",
    "LocalConstructionError",
    "InterfaceBreachError",
    "BudgetExhaustedError",
    "ConvergenceFailureError",
    "LocalConstructionLoop",
    "InterfaceDiscipline",
    "CoordinatedElaboration",
    "CandidateSet",
    # ---- Engines ----
    "LocalConstructionLoopEngine",
    "InterfaceDisciplineEnforcer",
    "InterfaceBreach",
    "NegotiationRecord",
    "CoordinatedElaborationEngine",
    "ElaborationSchedule",
    "CoordinationConflict",
    "CopilotConstructionParticipant",
    "CopilotProposal",
    "CopilotNegotiationRecord",
    "CopilotStrategyState",
    "StrategyAdaptation",
    # ---- Algorithms ----
    "run_local_construction_loop",
    "propose_candidates",
    "select_best_candidate",
    "verify_candidate",
    "propagate_obligations",
    "coordinate_interfaces",
    # ---- Integration ----
    "LocalConstructionIntegration",
    "PipelineState",
    "ConstructionRecord",
    "IntegrationConfig",
    # ---- Theorems ----
    "TheoremResult",
    "TheoremSuite",
    "run_all_theorems",
    "verify_construction_loop_termination",
    "verify_interface_discipline_soundness",
    "verify_coordinated_elaboration_consistency",
    "verify_candidate_selection_correctness",
    "verify_obligation_propagation_completeness",
    "verify_copilot_proposal_safety",
    "verify_interface_negotiation_convergence",
    "verify_semantic_compression_record_correctness",
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
    # ---- Cross-subsystem helpers ----
    "local_section_construction",
    "solver_verified_construction",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import coordinated_elaboration
except Exception:
    pass
try:
    from . import coordination_with_semantic_account
except Exception:
    pass
try:
    from . import copilot_in_construction
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import interface_discipline
except Exception:
    pass
try:
    from . import interface_discipline_overlap_objec
except Exception:
    pass
try:
    from . import local_construction_loop
except Exception:
    pass
try:
    from . import local_construction_loops_proposal
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
    from . import theorems
except Exception:
    pass
