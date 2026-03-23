"""Package scaffold for JuGeo generated modules."""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def import_site(graph: object) -> object:
    """Build a semantic site from an import graph.

    Uses :mod:`jugeo.geometry.site` to construct a Grothendieck site whose
    coordinates correspond to imported modules and whose morphisms correspond
    to import edges.

    Parameters
    ----------
    graph : object
        An import-graph object (adjacency mapping or compatible structure).

    Returns
    -------
    object
        A site object, or a fallback dict.
    """
    try:
        from jugeo.geometry.site import build_site
    except ImportError:
        return {
            "type": "import_site",
            "nodes": list(graph) if hasattr(graph, "__iter__") else [],
        }

    nodes = list(graph) if hasattr(graph, "__iter__") else []
    edges = getattr(graph, "edges", [])
    return build_site(
        coordinates=nodes,
        morphisms=edges,
        origin="import_graph",
    )


def import_federation(graph: object) -> dict:
    """Federate verification across import boundaries.

    Uses :mod:`jugeo.packs.federation` to split the import graph into
    federatable units that can be verified independently.

    Parameters
    ----------
    graph : object
        An import-graph object.

    Returns
    -------
    dict
        A federation plan dict with ``"units"`` and ``"boundary_crossings"``.
    """
    try:
        from jugeo.packs.federation import federate
    except ImportError:
        return {
            "units": [],
            "boundary_crossings": [],
        }

    nodes = list(graph) if hasattr(graph, "__iter__") else []
    edges = getattr(graph, "edges", [])
    return federate(
        nodes=nodes,
        edges=edges,
        domain="import_graph",
    )


def import_evidence(graph: object) -> dict:
    """Collect evidence from import-graph analysis.

    Uses :mod:`jugeo.evidence` to record import-graph topology and
    dependency metrics as evidence for trust computation.

    Parameters
    ----------
    graph : object
        An import-graph object.

    Returns
    -------
    dict
        An evidence record dict.
    """
    try:
        from jugeo.evidence import record_evidence
    except ImportError:
        return {
            "channel": "import_graph",
            "source": "python_runtime",
            "payload": {"graph": str(graph)},
        }

    nodes = list(graph) if hasattr(graph, "__iter__") else []
    return record_evidence(
        channel="import_graph",
        source="python_runtime.import_graph",
        payload={
            "module_count": len(nodes),
            "modules": [str(n) for n in nodes],
        },
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import dynamic_import_and_reflection
except Exception:
    pass
try:
    from . import import_cycles_and_package_fixed_po
except Exception:
    pass
try:
    from . import import_graph
except Exception:
    pass
try:
    from . import import_is_execution_plus_namespace
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
    from . import package_fixpoints
except Exception:
    pass
try:
    from . import proof_targets_for_import_semantics
except Exception:
    pass
try:
    from . import re_exports_star_imports_and_packag
except Exception:
    pass
try:
    from . import reexports
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
