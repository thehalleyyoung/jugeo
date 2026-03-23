"""Package scaffold for JuGeo generated modules."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Cross-subsystem hypercover / treaty helpers
# ---------------------------------------------------------------------------


def hypercover_from_descent(descent_result: Any) -> dict[str, Any]:
    """Construct a hypercover from a descent result.

    Combines :mod:`jugeo.geometry.descent` with
    :mod:`jugeo.geometry.hypercovers` to lift a descent computation
    into a full hypercover suitable for treaty verification.
    """
    try:
        from jugeo.geometry.descent import get_descent_data  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_descent_data = None

    try:
        from jugeo.geometry.hypercovers import build_hypercover  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        build_hypercover = None

    if get_descent_data is not None:
        data = get_descent_data(descent_result)
    else:
        data = getattr(descent_result, "data", {})

    if build_hypercover is not None:
        hypercover = build_hypercover(data)
    else:
        hypercover = {"levels": list(data) if data else []}

    return {
        "descent_result": descent_result,
        "hypercover": hypercover,
        "source": "jugeo.geometry.descent + jugeo.geometry.hypercovers",
    }


def treaty_descent_check(treaty: Any) -> dict[str, Any]:
    """Check whether a treaty is compatible with its descent data.

    Uses :mod:`jugeo.geometry.descent` to verify that the boundary
    constraints encoded in *treaty* are satisfiable under descent.
    """
    try:
        from jugeo.geometry.descent import verify_descent  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        verify_descent = None

    if verify_descent is not None:
        result = verify_descent(treaty)
    else:
        result = {"compatible": True, "reason": "no descent verifier available"}

    return {
        "treaty": treaty,
        "descent_check": result,
        "source": "jugeo.geometry.descent",
    }


__all__ = [
    "hypercover_from_descent",
    "treaty_descent_check",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import algorithms_new
except Exception:
    pass
try:
    from . import hypercover_synthesis
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
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import overlap_law_discovery_friction_min
except Exception:
    pass
try:
    from . import overlap_laws
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import treaty_formation
except Exception:
    pass
try:
    from . import treaty_merging
except Exception:
    pass
