"""Package scaffold for JuGeo generated modules."""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def aliasing_obstruction(alias: object) -> object:
    """Convert an aliasing issue into a geometric obstruction.

    Uses :mod:`jugeo.geometry.descent` to produce a descent obstruction
    representing the alias conflict in the Čech cohomology of the heap.

    Parameters
    ----------
    alias : object
        An alias record describing conflicting heap references.

    Returns
    -------
    object
        A descent obstruction object, or a fallback dict.
    """
    try:
        from jugeo.geometry.descent import make_obstruction
    except ImportError:
        return {
            "obstruction_type": "aliasing",
            "alias": str(alias),
        }

    source = getattr(alias, "source", "unknown")
    target = getattr(alias, "target", "unknown")
    kind = getattr(alias, "kind", "shared_ref")
    return make_obstruction(
        obstruction_type="aliasing",
        source=source,
        target=target,
        kind=kind,
    )


def heap_encoding(heap: object) -> object:
    """Encode heap state for Z3 constraint solving.

    Uses :mod:`jugeo.encodings.collection_heap_encodings` to produce a
    Z3-compatible encoding of the heap's object graph.

    Parameters
    ----------
    heap : object
        A heap snapshot or mapping of addresses to objects.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings.collection_heap_encodings import encode_heap
    except ImportError:
        return None

    entries = dict(heap) if isinstance(heap, dict) else {}
    return encode_heap(
        label="heap_snapshot",
        entries=entries,
    )


def aliasing_evidence(aliases: object) -> dict:
    """Create evidence from aliasing analysis.

    Uses :mod:`jugeo.evidence` to record alias analysis results as an
    evidence entry for downstream verification.

    Parameters
    ----------
    aliases : object
        A collection of alias records or a mapping.

    Returns
    -------
    dict
        An evidence record dict.
    """
    try:
        from jugeo.evidence import record_evidence
    except ImportError:
        return {
            "channel": "heap_aliasing",
            "source": "python_runtime",
            "payload": {"aliases": str(aliases)},
        }

    alias_list = list(aliases) if hasattr(aliases, "__iter__") else [aliases]
    return record_evidence(
        channel="heap_aliasing",
        source="python_runtime.heap_aliasing",
        payload={
            "alias_count": len(alias_list),
            "aliases": [str(a) for a in alias_list],
        },
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import aliasing
except Exception:
    pass
try:
    from . import aliasing_as_shared_geometry_suppor
except Exception:
    pass
try:
    from . import descent
except Exception:
    pass
try:
    from . import heap_objects
except Exception:
    pass
try:
    from . import identity_and_equality_observationa
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
    from . import mutation
except Exception:
    pass
try:
    from . import primitive_and_heap_mediated_values
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
