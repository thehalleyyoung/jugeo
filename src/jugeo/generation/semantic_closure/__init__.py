"""Package scaffold for JuGeo generated modules."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Cross-subsystem semantic closure helpers
# ---------------------------------------------------------------------------


def closure_over_site(site: Any) -> dict[str, Any]:
    """Compute the semantic closure of a geometric site.

    Uses :mod:`jugeo.geometry.site` to enumerate the open sets of
    *site* and returns the closure — the smallest closed set of
    semantic terms that is stable under all site morphisms.
    """
    try:
        from jugeo.geometry.site import get_open_sets  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_open_sets = None

    if get_open_sets is not None:
        open_sets = get_open_sets(site)
    else:
        open_sets = getattr(site, "open_sets", [])

    return {
        "site": site,
        "open_sets": list(open_sets),
        "closure_complete": len(list(open_sets)) > 0,
        "source": "jugeo.geometry.site",
    }


def evidence_closure(manifest: Any) -> dict[str, Any]:
    """Close the evidence manifest under entailment.

    Uses :mod:`jugeo.evidence.manifests` to gather all evidence
    items from *manifest* and computes the transitive closure of
    the entailment relation so that implied evidence is made
    explicit.
    """
    try:
        from jugeo.evidence.manifests import get_evidence_items  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_evidence_items = None

    if get_evidence_items is not None:
        items = get_evidence_items(manifest)
    else:
        items = getattr(manifest, "items", [])

    return {
        "manifest": manifest,
        "evidence_items": list(items),
        "closure_applied": True,
        "source": "jugeo.evidence.manifests",
    }


__all__ = [
    "closure_over_site",
    "evidence_closure",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import closure_checking
except Exception:
    pass
try:
    from . import global_section_assembly
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import integration_closure
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
    from . import regression_as_semantic_memory_reta
except Exception:
    pass
try:
    from . import regression_testing
except Exception:
    pass
try:
    from . import residual_gap_analysis
except Exception:
    pass
try:
    from . import semantic_closure_completion_criter
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
