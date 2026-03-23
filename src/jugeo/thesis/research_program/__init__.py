"""
jugeo.thesis.research_program
===============================

Research program status, dependency tracking, and milestone assessment for
the JuGeo framework.  Cross-references all major subsystems to report on
the overall health and completeness of the research program.

copilot: shared-core marker
Theory reference: theory2.tex Ch1–2
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "research_program_status",
    "program_dependencies",
    "program_milestones",
]


# ---------------------------------------------------------------------------
# Subsystem registry
# ---------------------------------------------------------------------------

_SUBSYSTEMS: dict[str, str] = {
    "geometry": "jugeo.geometry",
    "geometry.site": "jugeo.geometry.site",
    "geometry.descent": "jugeo.geometry.descent",
    "evidence": "jugeo.evidence",
    "evidence.manifests": "jugeo.evidence.manifests",
    "evidence.certificates": "jugeo.evidence.certificates",
    "solver": "jugeo.solver",
    "solver.z3_session": "jugeo.solver.z3_session",
    "encodings": "jugeo.encodings",
    "orchestration": "jugeo.orchestration",
    "generation": "jugeo.generation",
    "judgments": "jugeo.judgments",
    "judgments.judgment_terms": "jugeo.judgments.judgment_terms",
    "maturity": "jugeo.maturity",
    "evaluation": "jugeo.evaluation",
    "thesis": "jugeo.thesis",
}


def research_program_status() -> dict:
    """Report status of each research program component.

    Checks importability and basic completeness of every registered JuGeo
    subsystem (``jugeo.geometry``, ``jugeo.evidence``, ``jugeo.solver``,
    ``jugeo.encodings``, ``jugeo.orchestration``, ``jugeo.generation``, etc.).

    Returns:
        A dict with keys ``"components"`` (per-component status dicts),
        ``"available_count"``, ``"total_count"``, and ``"readiness_ratio"``.
    """
    components: dict[str, dict[str, Any]] = {}

    for name, import_path in _SUBSYSTEMS.items():
        try:
            mod = __import__(import_path, fromlist=["__all__"])
            exports = getattr(mod, "__all__", [])
            doc = getattr(mod, "__doc__", "") or ""
            is_stub = len(doc.strip()) < 60 and len(exports) == 0
            components[name] = {
                "importable": True,
                "is_stub": is_stub,
                "export_count": len(exports),
                "status": "stub" if is_stub else "available",
            }
        except ImportError:
            components[name] = {
                "importable": False,
                "is_stub": False,
                "export_count": 0,
                "status": "missing",
            }

    available = sum(1 for c in components.values() if c["status"] == "available")
    total = len(components)

    return {
        "components": components,
        "available_count": available,
        "total_count": total,
        "readiness_ratio": available / max(total, 1),
    }


def program_dependencies() -> dict:
    """Map dependency graph between research program components.

    Returns a dict describing which subsystems depend on which other
    subsystems.  This is a static, curated graph reflecting the architectural
    design of the JuGeo framework.

    Returns:
        A dict with keys ``"nodes"`` (list of component names),
        ``"edges"`` (list of ``(from, to)`` dependency pairs), and
        ``"adjacency"`` (dict mapping each node to its dependencies).
    """
    adjacency: dict[str, list[str]] = {
        "geometry.site": [],
        "geometry.descent": ["geometry.site"],
        "judgments": [],
        "judgments.judgment_terms": ["judgments"],
        "evidence": ["judgments"],
        "evidence.manifests": ["evidence", "judgments"],
        "evidence.certificates": ["evidence"],
        "encodings": ["judgments", "geometry"],
        "solver": [],
        "solver.z3_session": ["solver", "encodings"],
        "orchestration": ["evidence", "solver", "geometry"],
        "generation": ["judgments", "geometry", "evidence"],
        "maturity": ["evidence", "geometry"],
        "evaluation": ["judgments", "geometry", "evidence", "solver", "encodings"],
        "thesis": ["judgments", "geometry", "evidence", "solver", "encodings", "maturity"],
    }

    nodes = sorted(adjacency.keys())
    edges: list[tuple[str, str]] = []
    for node, deps in adjacency.items():
        for dep in deps:
            edges.append((node, dep))

    return {
        "nodes": nodes,
        "edges": edges,
        "adjacency": adjacency,
    }


def program_milestones() -> dict:
    """Define milestones and check completion status.

    Each milestone corresponds to a major research objective in the JuGeo
    program.  Completion is checked by probing the relevant subsystems for
    importability and non-stub status.

    Returns:
        A dict with keys ``"milestones"`` (list of milestone dicts with
        ``"name"``, ``"description"``, ``"required_subsystems"``,
        ``"complete"``, and ``"status"``), ``"completed_count"``, and
        ``"total_count"``.
    """
    milestones_spec = [
        {
            "name": "M1: Judgment Algebra",
            "description": "Judgment terms and algebra are fully implemented.",
            "required_subsystems": ["jugeo.judgments.judgment_terms"],
        },
        {
            "name": "M2: Geometric Site",
            "description": "Site, coordinates, and morphisms are implemented.",
            "required_subsystems": ["jugeo.geometry.site"],
        },
        {
            "name": "M3: Descent Gluing",
            "description": "Descent and gluing machinery is operational.",
            "required_subsystems": ["jugeo.geometry.descent"],
        },
        {
            "name": "M4: Evidence Pipeline",
            "description": "Evidence manifests, certificates, and trust algebra are in place.",
            "required_subsystems": ["jugeo.evidence.manifests", "jugeo.evidence.certificates"],
        },
        {
            "name": "M5: Solver Integration",
            "description": "Z3 session and solver adapter are operational.",
            "required_subsystems": ["jugeo.solver.z3_session"],
        },
        {
            "name": "M6: Encoding Layer",
            "description": "Encoding dispatcher and encoding families are available.",
            "required_subsystems": ["jugeo.encodings"],
        },
        {
            "name": "M7: Orchestration",
            "description": "Orchestration layer coordinates subsystems end-to-end.",
            "required_subsystems": ["jugeo.orchestration"],
        },
        {
            "name": "M8: Generation Pipeline",
            "description": "Generation pipeline produces new mathematical content.",
            "required_subsystems": ["jugeo.generation"],
        },
        {
            "name": "M9: Evaluation Framework",
            "description": "Evaluation design, methodology loops, and scaling limits are complete.",
            "required_subsystems": [
                "jugeo.evaluation.evaluation_design",
                "jugeo.evaluation.methodology_loops",
                "jugeo.evaluation.scaling_limits",
            ],
        },
        {
            "name": "M10: Maturity Model",
            "description": "Maturity cyclic picture and self-improvement are operational.",
            "required_subsystems": ["jugeo.maturity.cyclic_picture"],
        },
    ]

    milestones: list[dict] = []
    completed_count = 0

    for spec in milestones_spec:
        all_present = True
        any_stub = False
        for mod_path in spec["required_subsystems"]:
            try:
                mod = __import__(mod_path, fromlist=["__all__"])
                doc = getattr(mod, "__doc__", "") or ""
                exports = getattr(mod, "__all__", [])
                if len(doc.strip()) < 60 and len(exports) == 0:
                    any_stub = True
            except ImportError:
                all_present = False
                break

        complete = all_present and not any_stub
        if complete:
            completed_count += 1

        status = "complete" if complete else ("partial" if all_present else "incomplete")
        milestones.append({
            "name": spec["name"],
            "description": spec["description"],
            "required_subsystems": spec["required_subsystems"],
            "complete": complete,
            "status": status,
        })

    return {
        "milestones": milestones,
        "completed_count": completed_count,
        "total_count": len(milestones),
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import falsifiability
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import long_horizon_orchestration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import mathematical_ideation
except Exception:
    pass
try:
    from . import mixed_evidence
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import representation
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
