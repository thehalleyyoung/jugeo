"""Package scaffold for JuGeo generated modules."""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def scope_as_coordinate(scope: object) -> object:
    """Map a Python scope to a site coordinate.

    Uses :mod:`jugeo.geometry.site` to convert a scope descriptor into a
    coordinate in the semantic Grothendieck site.

    Parameters
    ----------
    scope : object
        A scope descriptor (dict, namespace, or compatible object).

    Returns
    -------
    object
        A site coordinate, or a fallback dict if the geometry layer is
        unavailable.
    """
    try:
        from jugeo.geometry.site import make_coordinate
    except ImportError:
        return {
            "type": "scope_coordinate",
            "scope": str(scope),
        }

    name = getattr(scope, "name", str(scope))
    kind = getattr(scope, "kind", "local")
    return make_coordinate(name=name, kind=kind, origin="python_scope")


def state_judgment(state: object) -> dict:
    """Create a judgment section for a runtime state snapshot.

    Uses :mod:`jugeo.judgments.sections` to build a judgment section that
    records the state's bindings and their trust levels.

    Parameters
    ----------
    state : object
        A state object or mapping of variable bindings.

    Returns
    -------
    dict
        A judgment section dict.
    """
    try:
        from jugeo.judgments.sections import make_section
    except ImportError:
        return {
            "section_type": "state_judgment",
            "bindings": dict(state) if isinstance(state, dict) else {},
        }

    bindings = dict(state) if isinstance(state, dict) else {}
    return make_section(
        section_type="state_judgment",
        coordinate=getattr(state, "coordinate", "global"),
        bindings=bindings,
        trust_level=getattr(state, "trust_level", 0),
    )


def scope_trust(scope: object) -> object:
    """Assign a trust level to a Python scope.

    Uses :mod:`jugeo.evidence.trust` to compute a trust level based on the
    scope's provenance and visibility.

    Parameters
    ----------
    scope : object
        A scope descriptor.

    Returns
    -------
    object
        A trust-level object or integer.
    """
    try:
        from jugeo.evidence.trust import assign_trust
    except ImportError:
        return 0

    kind = getattr(scope, "kind", "local")
    origin = getattr(scope, "origin", "unknown")
    return assign_trust(
        entity="scope",
        kind=kind,
        origin=origin,
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import closure_capture_cell_transport_lat
except Exception:
    pass
try:
    from . import closures
except Exception:
    pass
try:
    from . import global_and_local_bindings_obligati
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
    from . import module_state
except Exception:
    pass
try:
    from . import names
except Exception:
    pass
try:
    from . import scope_semantics_coordinate_formati
except Exception:
    pass
try:
    from . import scopes
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
