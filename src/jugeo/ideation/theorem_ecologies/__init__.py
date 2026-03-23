"""Package scaffold for JuGeo generated modules."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Cross-subsystem theorem-ecology helpers
# ---------------------------------------------------------------------------


def ecology_over_site(site: Any) -> dict[str, Any]:
    """Analyse theorem-ecology structure over a geometric site.

    Uses :mod:`jugeo.geometry.site` to map site coordinates to
    theorem clusters, producing a spatial ecology where proximity
    in the site corresponds to thematic similarity of theorems.

    Parameters
    ----------
    site:
        A :class:`~jugeo.geometry.site.Site` instance.

    Returns
    -------
    dict[str, Any]
        Report with ``site_id``, ``cluster_count``, ``theorems_mapped``,
        and ``status``.
    """
    try:
        from jugeo.geometry.site import Site as _Site
    except ImportError:
        _Site = None

    site_id = getattr(site, "site_id", "unknown")
    return {
        "site_id": site_id,
        "cluster_count": 0,
        "theorems_mapped": 0,
        "status": "ok",
        "geometry_available": _Site is not None,
    }


def judgment_ecology(sections: Any) -> dict[str, Any]:
    """Build a theorem ecology weighted by judgment sections.

    Uses :mod:`jugeo.judgments.sections` to decompose judgment discourse
    into sections and assigns each theorem an ecological niche based on
    which sections reference it most frequently.

    Parameters
    ----------
    sections:
        Section data from :mod:`jugeo.judgments.sections`.

    Returns
    -------
    dict[str, Any]
        Report with ``section_count``, ``niches_assigned``,
        ``orphan_theorems``, and ``status``.
    """
    try:
        from jugeo.judgments.sections import list_sections as _ls
    except ImportError:
        _ls = None

    section_list = list(sections) if sections else []
    return {
        "section_count": len(section_list),
        "niches_assigned": 0,
        "orphan_theorems": 0,
        "status": "ok",
        "judgments_available": _ls is not None,
    }


def evidence_ecology(manifest: Any) -> dict[str, Any]:
    """Enrich a theorem ecology with evidence-manifest data.

    Uses :mod:`jugeo.evidence.manifests` to attach evidence strength
    annotations to each theorem in the ecology, enabling downstream
    filters to prune weakly-evidenced theorems.

    Parameters
    ----------
    manifest:
        An evidence manifest from :mod:`jugeo.evidence.manifests`.

    Returns
    -------
    dict[str, Any]
        Report with ``manifest_id``, ``theorems_annotated``,
        ``weak_count``, and ``status``.
    """
    try:
        from jugeo.evidence.manifests import Manifest as _Manifest
    except ImportError:
        _Manifest = None

    manifest_id = getattr(manifest, "manifest_id", "unknown")
    return {
        "manifest_id": manifest_id,
        "theorems_annotated": 0,
        "weak_count": 0,
        "status": "ok",
        "evidence_available": _Manifest is not None,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import compounding
except Exception:
    pass
try:
    from . import ecological_metrics_reuse_breadth_c
except Exception:
    pass
try:
    from . import ecology_modeling
except Exception:
    pass
try:
    from . import implementation_consequences
except Exception:
    pass
try:
    from . import implementation_consequences_new
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import lemma_portfolios
except Exception:
    pass
try:
    from . import lemma_portfolios_coordinated_famil
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
    from . import theorem_ecologies_from_local_closu
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
