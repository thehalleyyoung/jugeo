"""Package scaffold for JuGeo generated modules.

Cross-references: orchestration derives frontier objectives from
geometry (descent), encodings, and evidence (manifests).
"""

from __future__ import annotations
from typing import Any

try:
    from jugeo.geometry.descent import (
        DescentPhase,
        RepairFrontier,
        DescentConfiguration,
    )
except Exception:
    DescentPhase = None  # type: ignore[assignment,misc]
    RepairFrontier = None  # type: ignore[assignment,misc]
    DescentConfiguration = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_judgment, encoding_registry
except Exception:
    encode_judgment = None  # type: ignore[assignment]
    encoding_registry = None  # type: ignore[assignment]

try:
    from jugeo.evidence.manifests import EvidenceManifest, build_evidence_manifest
except Exception:
    EvidenceManifest = None  # type: ignore[assignment,misc]
    build_evidence_manifest = None  # type: ignore[assignment]


def objective_from_descent(descent_result: Any) -> dict[str, Any]:
    """Derive frontier objectives from descent gaps.

    Inspects a descent result for unclosed repair frontiers and converts
    each gap into a frontier objective that the orchestrator can schedule.
    Uses jugeo.geometry.descent to interpret phase and frontier state.
    """
    gaps: list[dict[str, Any]] = []
    phase = getattr(descent_result, "phase", None)
    if DescentPhase is not None and phase is not None:
        phase_label = phase.value if hasattr(phase, "value") else str(phase)
    else:
        phase_label = "unknown"

    frontier = getattr(descent_result, "repair_frontier", None)
    if frontier is not None:
        open_items = getattr(frontier, "open_items", getattr(frontier, "items", []))
        for item in open_items:
            gaps.append({
                "target": getattr(item, "id", str(item)),
                "priority": getattr(item, "priority", 1.0),
            })

    return {
        "objectives": gaps,
        "descent_phase": phase_label,
        "gap_count": len(gaps),
        "subsystem": "jugeo.geometry.descent",
    }


def encoding_objective(family: Any) -> dict[str, Any]:
    """Create objectives for encoding completeness.

    Queries jugeo.encodings for the set of registered encoding families
    and identifies any that are incomplete or missing coverage for the
    given family specification.
    """
    if encoding_registry is None:
        return {
            "complete": False,
            "reason": "encoding_registry unavailable",
            "subsystem": "jugeo.encodings",
        }

    try:
        registry = encoding_registry()
    except Exception:
        registry = {}

    family_name = getattr(family, "name", str(family))
    entry = registry.get(family_name)
    if entry is None:
        return {
            "complete": False,
            "missing_family": family_name,
            "subsystem": "jugeo.encodings",
        }

    missing = [
        k for k, v in entry.items()
        if isinstance(v, dict) and not v.get("implemented", True)
    ]
    return {
        "complete": len(missing) == 0,
        "missing_encodings": missing,
        "family": family_name,
        "subsystem": "jugeo.encodings",
    }


def evidence_objective(manifest: Any) -> dict[str, Any]:
    """Create objectives for evidence coverage.

    Inspects an evidence manifest (jugeo.evidence.manifests) and emits
    objectives for any obligations that remain undischarged or any
    obstructions that block progress.
    """
    obligations = getattr(manifest, "obligations", [])
    obstructions = getattr(manifest, "obstructions", [])

    undischarged = [
        o for o in obligations
        if getattr(o, "status", "open") != "discharged"
    ]
    blocking = [
        o for o in obstructions
        if getattr(o, "severity", 0) > 0
    ]

    return {
        "coverage_complete": len(undischarged) == 0 and len(blocking) == 0,
        "undischarged_obligations": len(undischarged),
        "blocking_obstructions": len(blocking),
        "subsystem": "jugeo.evidence.manifests",
    }


# --- auto-registered submodules ---
try:
    from . import a_frontier_control_objective
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import budget_allocation
except Exception:
    pass
try:
    from . import exploitation_pressure
except Exception:
    pass
try:
    from . import exploration_pressure
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
    from . import objective_scoring
except Exception:
    pass
try:
    from . import the_frontier_as_a_controlled_searc
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
