"""Inhabitant Fleets – Ch42 Semantic-Fleet Generation Package.

Overview
--------
This package implements the *Ch42 Inhabitant Fleet* model for the JuGeo
semantic-construction pipeline.  The central idea is that a *construction
space* S is partitioned into overlapping *semantic patches* {Pᵢ}, and
multiple AI agents (the "fleet") compete to *inhabit* each patch with a
typed semantic term t such that the inhabitation judgement Γ ⊢ t : Pᵢ holds
under evidence context Γ.

Core Concepts
-------------
**Semantic patches** P ⊆ S
    A patch is a convex region of semantic space identified by a ``patch_id``.
    Multiple patches can overlap; overlapping patches create *instability*
    that the backpressure mechanism must resolve.

**Inhabitant fleets**
    A fleet F = {a₁, a₂, …, aₙ} is a set of AI agents each capable of
    proposing inhabitants for patches.  Fleet members coordinate via the
    *auction mechanism* described below.  Formally:

        ∀ aᵢ ∈ F, ∃ a function  propose: PatchID → InhabitantProposal
        and  bid: PatchID → FleetBid

**Semantic moves**
    A semantic move M : S → S is one of five types:

        PROPOSE    p →  t        "introduce a new candidate inhabitant"
        RETRACT    t →  ∅        "withdraw an existing candidate"
        REFINE     t →  t'       "narrow: t' ⊆ t  (irreversible)"
        GENERALIZE t →  t̂        "broaden: t̂ ⊇ t  (reversible)"
        SPECIALIZE t →  t₁|t₂   "case-split: t₁ ∪ t₂ = t  (irreversible)"

    Moves are *semantic*, not syntactic: they operate on the *meaning* of
    patches, not on surface-level character sequences.  The semantic distance
    δ(M) ≥ 0 of a move satisfies the triangle inequality, making (S, δ) a
    metric space (Ch42 Theorem 2.1).

**Backpressure from overlap instability**
    When two or more inhabitants tᵢ, tⱼ compete for the same patch P, an
    instability score σ(P) is computed:

        σ(P) = Σᵢ<ⱼ max(0, 1 − compat(tᵢ, tⱼ))

    where compat(·,·) ∈ [0,1] measures how well two inhabitants co-exist.
    When σ(P) > θ (the backpressure threshold), a :class:`BackpressureSignal`
    is emitted.  Fleet members receiving the signal must:

        1. Throttle their proposal rate (exponential back-off)
        2. RETRACT low-confidence proposals (score < threshold)
        3. Attempt to GENERALIZE overlapping proposals into a single one

    Backpressure can *cascade*: resolving overlap in P may expose instability
    in neighbouring patches P', P'', …  The :class:`CascadeDetector` in
    ``semantic_backpressure`` handles cascade propagation.

**AI fleet coordination**
    The fleet coordinator runs a *Vickrey-style sealed-bid auction*:

        ∀ patches P:
            winner(P) = argmax_{b ∈ Bids(P)} total_score(b)
            where total_score(b) = bid_score(b) × compat(b) × bp_tol(b)

    subject to cross-bid compatibility constraints.  The coordinator is
    implemented in ``ai_fleets``.

**Convergence theorems**
    The fleet is said to *converge* on patch P when:

        ∃ t* ∈ Inhabitants(P):
            score(t*) ≥ score(t)  ∀ t ∈ Competitors(P)
        AND σ(P) ≤ θ

    Four key theorems (proved in Ch42 §6) govern fleet behaviour:

    Theorem 1 – Existence
        ∀ non-empty patches P, ∃ at least one valid inhabitant t.
        (Proved by construction: PROPOSE always produces a candidate.)

    Theorem 2 – Completeness
        The five move types {PROPOSE, RETRACT, REFINE, GENERALIZE, SPECIALIZE}
        are sufficient to reach any target inhabitant from any source state.
        (Proved by showing the move graph is strongly connected for any P.)

    Theorem 3 – Boundedness
        Backpressure signals are bounded in cascade depth:
            depth(cascade) ≤ log₂(|patches|)
        (Proved by the diminishing-σ property of the RETRACT move.)

    Theorem 4 – Convergence
        Under the fleet auction mechanism, the fleet converges in finite time
        provided:
            (a) All fleet members use the same θ threshold
            (b) The auction is run with a fixed tie-breaking rule
            (c) No new patches are introduced during convergence
        (Proved by monotone score improvement + finite-patch finiteness.)

    Corollary 4.1 – Unique Convergence
        When proposal scoring is strict (no ties), the fleet converges to a
        *unique* inhabitant assignment for each patch.

Submodule Structure
-------------------
::

    inhabitant_fleets/
    ├── __init__.py            ← this file
    ├── models.py              ← core data structures
    ├── local_inhabitant_synthesis.py
    ├── ai_fleets.py
    ├── semantic_backpressure.py
    ├── algorithms.py
    ├── integration.py
    ├── theorems.py
    └── manifest.py

Usage Example
-------------
>>> from jugeo.generation.inhabitant_fleets import make_proposal, make_bid
>>> p = make_proposal("patch-1", "intro", "The system is well-typed.")
>>> p.status.value
'pending'
>>> p.score()
0.5
>>> b = make_bid("agent-alpha", "goal:soundness", "∀x.typed(x)")
>>> b.compute_total_score()  # 0.5 × 0.8 × 0.9
0.36...

>>> from jugeo.generation.inhabitant_fleets import make_signal, MoveType, make_move
>>> sig = make_signal("patch-1", ["patch-2", "patch-3"], 0.85)
>>> sig.severity.value
'high'
>>> sig2 = sig.escalate()
>>> sig2.severity.value
'critical'
>>> m = make_move(MoveType.REFINE, "∀x.P(x)", "∀x∈Fin.P(x)")
>>> m.is_reversible()
False

Mathematical Notation Reference
---------------------------------
Symbol  Meaning
------  -------
∀       for all (universal quantifier)
∃       there exists (existential quantifier)
∈       element of
∉       not element of
⊆       subset of or equal to
⊇       superset of or equal to
∩       intersection
∪       union
→       maps to / implies
↔       if and only if
∘       function composition
∅       empty set
σ       instability score
θ       backpressure threshold
δ       semantic distance
Γ       evidence context
⊢       inhabitation turnstile (Γ ⊢ t : P)
"""
from __future__ import annotations

import importlib
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Core models (always available)
# ---------------------------------------------------------------------------
from jugeo.generation.inhabitant_fleets.models import (
    ProposalStatus,
    SeverityLevel,
    MoveType,
    InhabitantProposal,
    FleetBid,
    BackpressureSignal,
    SemanticMove,
    NormalizedProposal,
    make_proposal,
    make_bid,
    make_signal,
    make_move,
)

# ---------------------------------------------------------------------------
# Sub-module imports (with graceful degradation)
# Each sub-module may not exist yet during incremental development; we
# attempt each import and fall back to a descriptive ImportError message
# rather than crashing the entire package.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (  # type: ignore[import]
        InhabitantSpace,
        SynthesisContext,
        InhabitantValidator,
        LocalInhabitantSynthesizer,
        synthesize_inhabitants,
        normalize_proposal,
    )
    _HAS_S01 = True
except ImportError:
    _HAS_S01 = False

try:
    from jugeo.generation.inhabitant_fleets.ai_fleets import (  # type: ignore[import]
        FleetMember,
        FleetCoordinator,
        InhabitantFleet,
        FleetRegistry,
        BidAggregator,
        create_default_fleet,
        create_fleet_member,
    )
    _HAS_S02 = True
except ImportError:
    _HAS_S02 = False

try:
    from jugeo.generation.inhabitant_fleets.semantic_backpressure import (  # type: ignore[import]
        InstabilityMetric,
        BackpressureMonitor,
        BackpressureController,
        BackpressureResolver,
        CascadeDetector,
    )
    _HAS_S03 = True
except ImportError:
    _HAS_S03 = False

try:
    from jugeo.generation.inhabitant_fleets.algorithms import (  # type: ignore[import]
        FleetAllocationAlgorithm,
        GreedyFleetAllocation,
        OptimalFleetAllocation,
        HeuristicFleetAllocation,
        BackpressurePropagation,
        InhabitantRanking,
        SemanticDistanceComputer,
        FleetConvergenceChecker,
    )
    _HAS_ALGORITHMS = True
except ImportError:
    _HAS_ALGORITHMS = False

try:
    from jugeo.generation.inhabitant_fleets.integration import (  # type: ignore[import]
        DescentAdaptor,
        GoalAdaptor,
        FrontierIntegrator,
        ConstructionAdaptor,
        InhabitantFleetPipeline,
    )
    _HAS_INTEGRATION = True
except ImportError:
    _HAS_INTEGRATION = False

try:
    from jugeo.generation.inhabitant_fleets.theorems import (  # type: ignore[import]
        TheoremVerifier,
        FleetConvergenceTheorem,
        BackpressureBoundednessTheorem,
        SemanticMoveCompletenessTheorem,
        InhabitantExistenceTheorem,
    )
    _HAS_THEOREMS = True
except ImportError:
    _HAS_THEOREMS = False

try:
    from jugeo.generation.inhabitant_fleets.manifest import (  # type: ignore[import]
        ModuleDescriptor,
        ExportRegistry,
        DependencyTracker,
        InhabitantFleetsManifest,
    )
    _HAS_MANIFEST = True
except ImportError:
    _HAS_MANIFEST = False  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
"""Semantic version of this package."""

PACKAGE_NAME = "jugeo.generation.inhabitant_fleets"
"""Fully-qualified package name."""

DESCRIPTION = (
    "Ch42 Semantic-Fleet Model: inhabitant fleets, semantic moves, "
    "backpressure, and convergence theorems for the JuGeo pipeline."
)
"""One-line package description."""

AUTHOR = "JuGeo Generation Team"

# Ch42 theoretical constants used throughout the package
BACKPRESSURE_DEFAULT_THRESHOLD: float = 0.7
"""Default backpressure threshold θ used by BackpressureSignal factories."""

CONVERGENCE_AGREEMENT_THRESHOLD: float = 0.95
"""Minimum Jaccard similarity above which two proposals are considered
equivalent (Corollary 4.1 normalisation constant)."""

MAX_CASCADE_DEPTH: int = 32
"""Upper bound on cascade depth (Theorem 3 guarantees log₂(|patches|) depth;
32 covers 2^32 ≈ 4 billion patches, far beyond practical use)."""

SCORE_CLAMP_MAX: float = 3.0
"""Maximum possible proposal score (= max(TrustTier) × 1.0 × 1.0)."""

# ---------------------------------------------------------------------------
# Sub-module registry
# ---------------------------------------------------------------------------

_SUBMODULE_NAMES: list[str] = [
    "models",
    "local_inhabitant_synthesis",
    "ai_fleets",
    "semantic_backpressure",
    "algorithms",
    "integration",
    "theorems",
    "manifest",
]
"""Ordered list of sub-module names within this package."""


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------


def get_version() -> str:
    """Return the package version string.

    Returns
    -------
    str
        Semantic version string (e.g. ``"0.1.0"``).

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets import get_version
    >>> get_version()
    '0.1.0'
    """
    return VERSION


def list_submodules() -> list[str]:
    """Return the ordered list of sub-module names in this package.

    Returns
    -------
    list[str]
        Sub-module names (without the package prefix).

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets import list_submodules
    >>> "models" in list_submodules()
    True
    """
    return list(_SUBMODULE_NAMES)


def check_imports() -> dict[str, bool]:
    """Attempt to import each sub-module and report success/failure.

    This is a diagnostic utility useful during development and CI to
    identify which sub-modules are available in the current environment.

    Returns
    -------
    dict[str, bool]
        Mapping from sub-module name to whether its import succeeded.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets import check_imports
    >>> results = check_imports()
    >>> results["models"]  # models.py always exists
    True
    """
    results: dict[str, bool] = {}
    for name in _SUBMODULE_NAMES:
        full_name = f"{PACKAGE_NAME}.{name}"
        try:
            importlib.import_module(full_name)
            results[name] = True
        except ImportError:
            results[name] = False
    return results


def fleet_status_report() -> dict[str, Any]:
    """Return a comprehensive status report for this package.

    The report includes version info, available sub-modules, and
    package-level theoretical constants.

    Returns
    -------
    dict[str, Any]
        Status dictionary suitable for logging or health-check endpoints.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets import fleet_status_report
    >>> report = fleet_status_report()
    >>> report["version"]
    '0.1.0'
    >>> "submodule_availability" in report
    True
    """
    return {
        "version": VERSION,
        "package_name": PACKAGE_NAME,
        "description": DESCRIPTION,
        "backpressure_threshold": BACKPRESSURE_DEFAULT_THRESHOLD,
        "convergence_threshold": CONVERGENCE_AGREEMENT_THRESHOLD,
        "max_cascade_depth": MAX_CASCADE_DEPTH,
        "score_clamp_max": SCORE_CLAMP_MAX,
        "submodule_availability": check_imports(),
        "timestamp": time.time(),
    }


def get_manifest() -> Any:
    """Return a fully-built manifest for this package.

    The manifest describes all exported symbols, their sub-module origins,
    and inter-module dependencies.

    Returns
    -------
    InhabitantFleetsManifest
        Fully built manifest object.

    Raises
    ------
    ImportError
        If the ``manifest`` sub-module is not available.

    Examples
    --------
    >>> # Only works when manifest submodule is installed:
    >>> # m = get_manifest(); m.version == VERSION
    """
    if not _HAS_MANIFEST:
        raise ImportError(
            "The 'manifest' sub-module is not available. "
            "Ensure jugeo.generation.inhabitant_fleets.manifest is installed."
        )
    m = InhabitantFleetsManifest()  # type: ignore[name-defined]
    m.build()
    return m


def create_quick_proposal(
    patch_id: str,
    content: str,
    *,
    section_label: str = "default",
    evidence_score: float = 0.5,
) -> InhabitantProposal:
    """Create and return a minimal InhabitantProposal with default settings.

    A convenience wrapper around :func:`make_proposal` that requires only
    the two most important fields.

    Parameters
    ----------
    patch_id : str
        Target patch identifier.
    content : str
        Semantic content to propose as the inhabitant.
    section_label : str
        Section label (default ``"default"``).
    evidence_score : float
        Evidence quality ∈ [0, 1] (default 0.5).

    Returns
    -------
    InhabitantProposal

    Examples
    --------
    >>> p = create_quick_proposal("patch-1", "∀x.P(x)")
    >>> p.patch_id
    'patch-1'
    >>> p.semantic_content
    '∀x.P(x)'
    """
    return make_proposal(
        patch_id=patch_id,
        section_label=section_label,
        content=content,
        evidence_score=evidence_score,
    )


def run_mini_auction(
    patch_id: str,
    proposals: list[tuple[str, str, float]],
) -> InhabitantProposal | None:
    """Run a simplified single-round fleet auction and return the winner.

    This is a self-contained mini-auction that does NOT require any of the
    optional sub-modules.  It accepts a list of (member_id, content,
    evidence_score) tuples, creates proposals, registers competition
    relationships, and returns the highest-scoring proposal (or None if
    the list is empty).

    Parameters
    ----------
    patch_id : str
        The patch to auction inhabitants for.
    proposals : list[tuple[str, str, float]]
        Each tuple is (fleet_member_id, semantic_content, evidence_score).

    Returns
    -------
    InhabitantProposal | None
        The winning proposal, or None if no proposals were provided.

    Examples
    --------
    >>> winner = run_mini_auction("patch-1", [
    ...     ("agent-A", "∀x.typed(x)", 0.9),
    ...     ("agent-B", "∃x.typed(x)", 0.6),
    ... ])
    >>> winner is not None
    True
    >>> winner.proposer_id
    'agent-A'
    """
    if not proposals:
        return None

    created: list[InhabitantProposal] = []
    for member_id, content, escore in proposals:
        p = make_proposal(patch_id, "auction", content, evidence_score=escore)
        p.proposer_id = member_id
        created.append(p)

    # Register all competition relationships
    for i, pi in enumerate(created):
        for pj in created[i + 1 :]:
            pi.compete_with(pj)

    # Select winner by score
    winner = max(created, key=lambda p: p.score())
    winner.accept()
    for p in created:
        if p is not winner:
            p.reject(reason=f"Lost auction to {winner.proposal_id[:8]}")
    return winner


def describe_move_types() -> dict[str, str]:
    """Return a human-readable description of each semantic move type.

    Returns
    -------
    dict[str, str]
        Mapping from MoveType value to description string.

    Examples
    --------
    >>> descs = describe_move_types()
    >>> "propose" in descs
    True
    """
    return {
        MoveType.PROPOSE.value: (
            "Introduce a new candidate inhabitant for a patch. "
            "Reversible: the proposal can be RETRACTed."
        ),
        MoveType.RETRACT.value: (
            "Withdraw a previously proposed inhabitant. "
            "Reversible: a new PROPOSE can reinstate the inhabitant."
        ),
        MoveType.REFINE.value: (
            "Narrow the semantic content of a proposal (t' ⊆ t). "
            "Irreversible: information is lost in narrowing."
        ),
        MoveType.GENERALIZE.value: (
            "Broaden the semantic content of a proposal (t̂ ⊇ t). "
            "Reversible: a subsequent REFINE can recover the original."
        ),
        MoveType.SPECIALIZE.value: (
            "Introduce a specialised sub-case inhabitant (case-split). "
            "Irreversible: the split cannot be trivially undone."
        ),
    }


def describe_severity_levels() -> dict[str, str]:
    """Return a human-readable description of each backpressure severity level.

    Returns
    -------
    dict[str, str]
        Mapping from SeverityLevel value to description.

    Examples
    --------
    >>> descs = describe_severity_levels()
    >>> "critical" in descs
    True
    """
    return {
        SeverityLevel.LOW.value: (
            "Informational: no immediate action required. "
            "Fleet members may continue at normal rate."
        ),
        SeverityLevel.MEDIUM.value: (
            "Advisory: consider throttling proposal rate. "
            "Low-confidence proposals should be reviewed."
        ),
        SeverityLevel.HIGH.value: (
            "Warning: suspend new proposals in affected patches "
            "until backpressure is resolved."
        ),
        SeverityLevel.CRITICAL.value: (
            "Halt: freeze all fleet activity in affected patches immediately. "
            "Cascade risk is high; the coordinator must intervene."
        ),
    }


# ---------------------------------------------------------------------------
# Cross-subsystem fleet helpers
# ---------------------------------------------------------------------------


def fleet_from_orchestration(fleet: Any) -> dict[str, Any]:
    """Enrich a fleet descriptor with orchestration metadata.

    Queries :mod:`jugeo.orchestration.fleet` to obtain scheduling
    hints, resource caps, and coordination policies for the fleet.
    """
    try:
        from jugeo.orchestration.fleet import get_fleet_config  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_fleet_config = None

    if get_fleet_config is not None:
        config = get_fleet_config(fleet)
    else:
        config = getattr(fleet, "config", {})

    return {
        "fleet": fleet,
        "orchestration_config": config,
        "source": "jugeo.orchestration.fleet",
    }


def inhabitant_judgment(inhabitant: Any) -> dict[str, Any]:
    """Produce a judgment term for a proposed inhabitant.

    Uses :mod:`jugeo.judgments.judgment_terms` to derive the formal
    inhabitation judgement Γ ⊢ t : P for the given *inhabitant*.
    """
    try:
        from jugeo.judgments.judgment_terms import derive_judgment  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        derive_judgment = None

    if derive_judgment is not None:
        judgment = derive_judgment(inhabitant)
    else:
        judgment = {"term": str(inhabitant), "valid": None}

    return {
        "inhabitant": inhabitant,
        "judgment": judgment,
        "source": "jugeo.judgments.judgment_terms",
    }


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # ---- Package metadata ----
    "VERSION",
    "PACKAGE_NAME",
    "DESCRIPTION",
    "AUTHOR",
    "BACKPRESSURE_DEFAULT_THRESHOLD",
    "CONVERGENCE_AGREEMENT_THRESHOLD",
    "MAX_CASCADE_DEPTH",
    "SCORE_CLAMP_MAX",
    # ---- Enumerations (from models) ----
    "ProposalStatus",
    "SeverityLevel",
    "MoveType",
    # ---- Core dataclasses (from models) ----
    "InhabitantProposal",
    "FleetBid",
    "BackpressureSignal",
    "SemanticMove",
    "NormalizedProposal",
    # ---- Factory functions (from models) ----
    "make_proposal",
    "make_bid",
    "make_signal",
    "make_move",
    # ---- Package-level helpers ----
    "get_version",
    "list_submodules",
    "check_imports",
    "fleet_status_report",
    "get_manifest",
    "create_quick_proposal",
    "run_mini_auction",
    "describe_move_types",
    "describe_severity_levels",
    # ---- local_inhabitant_synthesis (optional) ----
    "InhabitantSpace",
    "SynthesisContext",
    "InhabitantValidator",
    "LocalInhabitantSynthesizer",
    "synthesize_inhabitants",
    "normalize_proposal",
    # ---- ai_fleets (optional) ----
    "FleetMember",
    "FleetCoordinator",
    "InhabitantFleet",
    "FleetRegistry",
    "BidAggregator",
    "create_default_fleet",
    "create_fleet_member",
    # ---- semantic_backpressure (optional) ----
    "InstabilityMetric",
    "BackpressureMonitor",
    "BackpressureController",
    "BackpressureResolver",
    "CascadeDetector",
    # ---- algorithms (optional) ----
    "FleetAllocationAlgorithm",
    "GreedyFleetAllocation",
    "OptimalFleetAllocation",
    "HeuristicFleetAllocation",
    "BackpressurePropagation",
    "InhabitantRanking",
    "SemanticDistanceComputer",
    "FleetConvergenceChecker",
    # ---- integration (optional) ----
    "DescentAdaptor",
    "GoalAdaptor",
    "FrontierIntegrator",
    "ConstructionAdaptor",
    "InhabitantFleetPipeline",
    # ---- theorems (optional) ----
    "TheoremVerifier",
    "FleetConvergenceTheorem",
    "BackpressureBoundednessTheorem",
    "SemanticMoveCompletenessTheorem",
    "InhabitantExistenceTheorem",
    # ---- manifest (optional) ----
    "ModuleDescriptor",
    "ExportRegistry",
    "DependencyTracker",
    "InhabitantFleetsManifest",
    "get_manifest",
    # ---- Cross-subsystem helpers ----
    "fleet_from_orchestration",
    "inhabitant_judgment",
]


# --- auto-registered submodules ---
try:
    from . import ai_fleets
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import fleet_merging
except Exception:
    pass
try:
    from . import fleet_search_over_admissible_inhab
except Exception:
    pass
try:
    from . import implementation_consequences
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import local_inhabitant_synthesis
except Exception:
    pass
try:
    from . import local_inhabitant_synthesis_goal_re
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
    from . import semantic_backpressure
except Exception:
    pass
try:
    from . import semantic_backpressure_congestion_s
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
