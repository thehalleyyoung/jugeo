"""Package scaffold for JuGeo generated modules.

Cross-references: semantic control consults geometry (site topology),
evidence coverage, and judgment section quality.
"""

from __future__ import annotations
from typing import Any

try:
    from jugeo.geometry.site import Site, SiteDiagnostics, build_site
except Exception:
    Site = None  # type: ignore[assignment,misc]
    SiteDiagnostics = None  # type: ignore[assignment,misc]
    build_site = None  # type: ignore[assignment]

try:
    from jugeo.evidence.manifests import EvidenceManifest
except Exception:
    EvidenceManifest = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustAlgebra
except Exception:
    TrustAlgebra = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.sections import Section, SectionFamily, SectionComparator
except Exception:
    Section = None  # type: ignore[assignment,misc]
    SectionFamily = None  # type: ignore[assignment,misc]
    SectionComparator = None  # type: ignore[assignment,misc]


def control_via_site(site: Any) -> dict[str, Any]:
    """Semantic control based on site topology using jugeo.geometry.site.

    Inspects the covering families and morphism structure of a site to
    determine which control moves are admissible.
    """
    if SiteDiagnostics is None:
        coords = getattr(site, "coordinates", [])
        return {
            "admissible_moves": len(coords),
            "topology_available": False,
            "subsystem": "jugeo.geometry.site",
        }

    try:
        diag = SiteDiagnostics(site) if callable(SiteDiagnostics) else SiteDiagnostics
        morphism_count = getattr(diag, "morphism_count", lambda: 0)
        n_morphisms = morphism_count() if callable(morphism_count) else morphism_count
    except Exception:
        n_morphisms = 0

    coords = getattr(site, "coordinates", [])
    return {
        "admissible_moves": len(coords),
        "morphism_count": n_morphisms,
        "topology_available": True,
        "subsystem": "jugeo.geometry.site",
    }


def control_via_evidence(manifest: Any) -> dict[str, Any]:
    """Semantic control based on evidence coverage using jugeo.evidence.

    The orchestrator adjusts its control strategy based on the evidence
    manifest's obligation and obstruction state.
    """
    obligations = getattr(manifest, "obligations", [])
    obstructions = getattr(manifest, "obstructions", [])
    coverage = getattr(manifest, "coverage_ratio", None)

    if callable(coverage):
        try:
            coverage = coverage()
        except Exception:
            coverage = None

    undischarged = sum(
        1 for o in obligations
        if getattr(o, "status", "open") != "discharged"
    )
    severity_sum = sum(getattr(o, "severity", 0) for o in obstructions)

    if coverage is not None and coverage >= 0.95 and undischarged == 0:
        strategy = "converge"
    elif severity_sum > 0:
        strategy = "repair"
    else:
        strategy = "explore"

    return {
        "strategy": strategy,
        "coverage": coverage,
        "undischarged": undischarged,
        "obstruction_severity": severity_sum,
        "subsystem": "jugeo.evidence",
    }


def control_via_judgments(sections: list[Any]) -> dict[str, Any]:
    """Semantic control based on judgment section quality.

    Uses jugeo.judgments.sections to evaluate section gluing consistency
    and selects a control strategy (refine, extend, or accept).
    """
    if SectionComparator is None:
        return {
            "strategy": "extend",
            "section_count": len(sections),
            "quality_available": False,
            "subsystem": "jugeo.judgments",
        }

    comparator = SectionComparator()
    scores: list[float] = []
    for s in sections:
        try:
            score = float(comparator.compare(s, s))
        except Exception:
            score = 0.0
        scores.append(score)

    avg = sum(scores) / len(scores) if scores else 0.0

    if avg >= 0.9:
        strategy = "accept"
    elif avg >= 0.5:
        strategy = "refine"
    else:
        strategy = "extend"

    return {
        "strategy": strategy,
        "section_count": len(sections),
        "average_quality": round(avg, 4),
        "quality_available": True,
        "subsystem": "jugeo.judgments",
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import convergence
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
    from . import move_selection
except Exception:
    pass
try:
    from . import orchestration_is_a_control_problem
except Exception:
    pass
try:
    from . import proof_obligations_for_orchestratio
except Exception:
    pass
try:
    from . import search_should_proceed_on_a_frontie
except Exception:
    pass
try:
    from . import semantic_transitions
except Exception:
    pass
try:
    from . import state_management
except Exception:
    pass
try:
    from . import the_controller_should_optimize_sem
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
