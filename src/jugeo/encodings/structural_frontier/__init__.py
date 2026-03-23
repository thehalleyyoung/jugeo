"""Structural Frontier package for JuGeo (Chapter 25 — Z3 and the Structural Frontier).

This package defines what Z3 can and cannot decide, how types lift Z3 invariants,
and how countermodels become repair instructions.  It is organised into eight
modules that together implement the full structural-frontier subsystem:

* ``manifest``   — Chapter 25 coverage manifest, symbol groups, and theory claims.
* ``models``     — Core data models: DecidabilityClass, FrontierSide, RepairAction,
                   StructuralFrontier, SolverLiftedType, FrontierBoundary,
                   DecidabilityMap, CountermodelObstruction.
* ``structural_frontier_definer`` — StructuralFrontierDefiner: classify formulas
                   against the frontier, enumerate decidable/undecidable regions,
                   produce undecidability witnesses and frontier reports.
* ``solver_lifted_type_system``   — SolverLiftedTypeSystem: lift base types to
                   Z3-invariant-carrying solver types, check subtyping and inhabitation,
                   emit SMT-LIB2 declarations.
* ``countermodel_to_repair``      — CountermodelToRepair: classify countermodel
                   obstructions, generate repair candidates, navigate the repair
                   frontier to a decidable encoding.
* ``algorithms`` — Standalone algorithms: fragment classification, bisection,
                   countermodel aggregation, repair priority scheduling.
* ``integration``— Integration layer: pipeline, Z3 bridge, support linker,
                   type system integrator, repair dispatcher.
* ``theorems``   — Theorem records and registry for all Ch25 theorems.

Copilot integration points are present throughout; see individual module docstrings
for details on copilot-assisted hints and narratives.
"""

from __future__ import annotations

# --- models ---
from jugeo.encodings.structural_frontier.models import (
    CountermodelObstruction,
    DecidabilityClass,
    DecidabilityMap,
    FrontierBoundary,
    FrontierSide,
    RepairAction,
    SolverLiftedType,
    StructuralFrontier,
)

# --- manifest ---
from jugeo.encodings.structural_frontier.manifest import (
    ClaimSummary,
    CoverageStatus,
    ManifestRecord,
    PackageManifest,
    SymbolGroup,
)

# --- s01: frontier definer ---
from jugeo.encodings.structural_frontier.structural_frontier_definer import (
    DecidabilityOracle,
    FrontierBoundaryLocator,
    StructuralFrontierDefiner,
    UndecidabilityWitness,
)

# --- s02: solver-lifted type system ---
from jugeo.encodings.structural_frontier.solver_lifted_type_system import (
    InvariantChecker,
    SolverLiftedTypeSystem,
    TypeLiftingStrategy,
    TypeLiftingTranslator,
)

# --- s03: countermodel to repair ---
from jugeo.encodings.structural_frontier.countermodel_to_repair import (
    CountermodelToRepair,
    ObstructionClassifier,
    RepairCandidateGenerator,
    RepairFrontierNavigator,
)

# --- algorithms ---
from jugeo.encodings.structural_frontier.algorithms import (
    CountermodelAggregator,
    DecidabilityBisector,
    FrontierExplorer,
    RepairPriorityScheduler,
    batch_classify,
    classify_formula_fragment,
    compute_frontier_boundary,
    find_cheapest_encoding,
)

# --- integration ---
from jugeo.encodings.structural_frontier.integration import (
    CountermodelRepairDispatcher,
    FrontierSupportLinker,
    StructuralFrontierPipeline,
    TypeSystemIntegrator,
    Z3FrontierBridge,
)

# --- theorems ---
from jugeo.encodings.structural_frontier.theorems import (
    DEFAULT_REGISTRY,
    TheoremRecord,
    TheoremRegistry,
    TheoremStatus,
    check_dependencies_met,
    export_theorem_list,
    verify_theorem_sketch,
)

__all__ = [
    # models
    "CountermodelObstruction",
    "DecidabilityClass",
    "DecidabilityMap",
    "FrontierBoundary",
    "FrontierSide",
    "RepairAction",
    "SolverLiftedType",
    "StructuralFrontier",
    # manifest
    "ClaimSummary",
    "CoverageStatus",
    "ManifestRecord",
    "PackageManifest",
    "SymbolGroup",
    # s01
    "DecidabilityOracle",
    "FrontierBoundaryLocator",
    "StructuralFrontierDefiner",
    "UndecidabilityWitness",
    # s02
    "InvariantChecker",
    "SolverLiftedTypeSystem",
    "TypeLiftingStrategy",
    "TypeLiftingTranslator",
    # s03
    "CountermodelToRepair",
    "ObstructionClassifier",
    "RepairCandidateGenerator",
    "RepairFrontierNavigator",
    # algorithms
    "CountermodelAggregator",
    "DecidabilityBisector",
    "FrontierExplorer",
    "RepairPriorityScheduler",
    "batch_classify",
    "classify_formula_fragment",
    "compute_frontier_boundary",
    "find_cheapest_encoding",
    # integration
    "CountermodelRepairDispatcher",
    "FrontierSupportLinker",
    "StructuralFrontierPipeline",
    "TypeSystemIntegrator",
    "Z3FrontierBridge",
    # theorems
    "DEFAULT_REGISTRY",
    "TheoremRecord",
    "TheoremRegistry",
    "TheoremStatus",
    "check_dependencies_met",
    "export_theorem_list",
    "verify_theorem_sketch",
    # cross-subsystem integration
    "frontier_over_site",
    "repair_encoding",
    "solver_session_encoding",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration — geometry sites, repair semantics, Z3 sessions
# ---------------------------------------------------------------------------

from typing import Any

try:
    from jugeo.geometry.site import Site, Coordinate  # type: ignore[import]
except ImportError:
    Site = None  # type: ignore[assignment]
    Coordinate = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.repair_semantics import (  # type: ignore[import]
        RepairPlan,
        RepairValidator,
    )
except ImportError:
    RepairPlan = None  # type: ignore[assignment]
    RepairValidator = None  # type: ignore[assignment]

try:
    from jugeo.solver.z3_session import Z3Session  # type: ignore[import]
except ImportError:
    Z3Session = None  # type: ignore[assignment]


def frontier_over_site(site: object) -> "DecidabilityMap":
    """Classify decidability of formulas across all coordinates of a site.

    Takes a ``jugeo.geometry.site.Site`` and iterates its coordinates,
    running the ``StructuralFrontierDefiner`` on each coordinate's
    associated formulas to build a complete ``DecidabilityMap``.

    Parameters
    ----------
    site:
        A ``jugeo.geometry.site.Site`` instance.

    Returns
    -------
    DecidabilityMap
        A mapping from coordinate keys to their ``DecidabilityClass``.
    """
    definer = StructuralFrontierDefiner()
    classifications: dict[str, Any] = {}

    coordinates = getattr(site, "coordinates", None)
    if coordinates is None:
        coordinates = getattr(site, "objects", None) or []

    for coord in coordinates:
        key = getattr(coord, "key", None) or str(coord)
        try:
            result = definer.classify(coord)
            classifications[key] = result
        except Exception:
            classifications[key] = DecidabilityClass.UNKNOWN if (
                DecidabilityClass is not None
                and hasattr(DecidabilityClass, "UNKNOWN")
            ) else "unknown"

    return DecidabilityMap(classifications=classifications)


def repair_encoding(
    frontier: "StructuralFrontier",
    undecidable_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Repair undecidable encodings using the repair-semantics subsystem.

    Consults ``jugeo.problem_modes.repair_semantics`` to generate a
    ``RepairPlan`` for each undecidable region in the frontier, then
    applies ``CountermodelToRepair`` to navigate toward a decidable
    encoding.

    Parameters
    ----------
    frontier:
        A ``StructuralFrontier`` containing decidability classifications.
    undecidable_keys:
        Optional list of coordinate keys to repair.  When ``None`` all
        undecidable regions in the frontier are considered.

    Returns
    -------
    dict[str, Any]
        Dictionary with ``repairs`` (list of per-key repair results),
        ``repair_count``, and ``all_repaired`` flag.
    """
    repairs: list[dict[str, Any]] = []

    boundary = getattr(frontier, "boundary", None)
    dec_map = getattr(frontier, "decidability_map", None) or {}

    targets = undecidable_keys
    if targets is None:
        targets = [
            k for k, v in dec_map.items()
            if getattr(v, "value", str(v)) in ("UNDECIDABLE", "UNKNOWN")
        ]

    repairer = CountermodelToRepair()
    for key in targets:
        entry: dict[str, Any] = {"key": key, "repaired": False}
        try:
            candidates = repairer.generate_candidates(key, frontier)
            entry["candidates"] = len(candidates) if candidates else 0

            # Integrate with repair_semantics when available
            if RepairPlan is not None and candidates:
                plan = RepairPlan(target=key, candidates=candidates)
                entry["repair_plan"] = str(plan)
                if RepairValidator is not None:
                    validator = RepairValidator()
                    entry["repaired"] = validator.validate(plan)
                else:
                    entry["repaired"] = True
            elif candidates:
                entry["repaired"] = True
        except Exception as exc:
            entry["error"] = str(exc)

        repairs.append(entry)

    return {
        "repairs": repairs,
        "repair_count": sum(1 for r in repairs if r.get("repaired")),
        "all_repaired": all(r.get("repaired") for r in repairs) if repairs else True,
    }


def solver_session_encoding(
    frontier: "StructuralFrontier",
    session: object | None = None,
) -> dict[str, Any]:
    """Feed a structural frontier encoding directly into a Z3 solver session.

    When a ``jugeo.solver.z3_session.Z3Session`` is provided (or one can
    be instantiated), the decidable portion of the frontier is translated
    into Z3 assertions via ``Z3FrontierBridge`` and submitted for solving.

    Parameters
    ----------
    frontier:
        A ``StructuralFrontier`` instance.
    session:
        Optional ``jugeo.solver.z3_session.Z3Session``.  When ``None`` a
        fresh session is created if the solver subsystem is available.

    Returns
    -------
    dict[str, Any]
        Result dictionary with ``session_id``, ``assertions_added``, and
        ``solve_result`` keys.
    """
    result: dict[str, Any] = {"session_id": None, "assertions_added": 0, "solve_result": None}

    if session is None and Z3Session is not None:
        try:
            session = Z3Session()
        except Exception:
            pass

    if session is None:
        result["error"] = "Z3Session unavailable"
        return result

    result["session_id"] = getattr(session, "session_id", id(session))

    try:
        bridge = Z3FrontierBridge()
        assertions = bridge.translate(frontier)
        result["assertions_added"] = len(assertions) if assertions else 0

        for assertion in (assertions or []):
            try:
                session.assert_formula(assertion)
            except Exception:
                session.add(assertion)

        solve_result = session.check()
        result["solve_result"] = getattr(solve_result, "value", str(solve_result))
    except Exception as exc:
        result["error"] = str(exc)

    return result



# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import countermodel_to_repair
except Exception:
    pass
try:
    from . import countermodels_should_become_first
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
    from . import solver_lifted_type_system
except Exception:
    pass
try:
    from . import structural_frontier_definer
except Exception:
    pass
try:
    from . import the_code_should_make_solver_lifted
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import z3_should_own_the_structural_front
except Exception:
    pass
