from __future__ import annotations

r"""Package ``jugeo.python_runtime.metaobject_surfaces``.

Ch20 — Class Creation, Metaclasses, Descriptors, and Behavioral Surfaces.

theory2.tex Ch20 §20.1–§20.10.

This package models Python's metaobject protocol (MOP) as a Grothendieck site:
- Metaclasses are type constructors that produce new coordinates.
- Behavioral surfaces are judgment-indexed protocol specifications.
- Descriptor chains are MRO-ordered attribute-access morphisms.
- Class creation traces record the three-phase class construction protocol.

Exported symbols
----------------

Models
^^^^^^
:class:`MetaclassRecord`
:class:`BehavioralSurface`
:class:`DescriptorChain`
:class:`ClassCreationTrace`

Manifest
^^^^^^^^
:data:`MANIFEST`
"""

# --- core model exports ---

from jugeo.python_runtime.metaobject_surfaces.models import (
    MetaclassRecord,
    BehavioralSurface,
    DescriptorChain,
    ClassCreationTrace,
)

# --- manifest singleton export ---

from jugeo.python_runtime.metaobject_surfaces.manifest import MANIFEST

# ---

__all__ = [
    "MetaclassRecord",
    "BehavioralSurface",
    "DescriptorChain",
    "ClassCreationTrace",
    "MANIFEST",
    # cross-references
    "metaobject_judgment",
    "metaobject_encoding",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def metaobject_judgment(surface: object) -> tuple:
    """Create a judgment term for a metaobject surface.

    Uses :mod:`jugeo.judgments.judgment_terms` to build a judgment term
    encoding the metaclass, behavioral surface, or descriptor chain
    properties.

    Parameters
    ----------
    surface : object
        A metaobject surface record (e.g. :class:`MetaclassRecord`,
        :class:`BehavioralSurface`, :class:`DescriptorChain`).

    Returns
    -------
    tuple
        A judgment term tuple.
    """
    try:
        from jugeo.judgments.judgment_terms import make_judgment_term
    except ImportError:
        return ("metaobject", type(surface).__name__, str(surface), None, False, False, (), 0)

    kind = type(surface).__name__
    coordinate = getattr(surface, "name", getattr(surface, "metaclass_name", str(surface)))
    return make_judgment_term(
        coordinate=coordinate,
        kind=f"metaobject_{kind}",
        parameters=(),
        return_type=None,
        is_async=False,
        is_generator=False,
        decorators=(),
        trust_level=getattr(surface, "trust_level", 0),
    )


def metaobject_encoding(surface: object) -> object:
    """Encode a metaobject surface for Z3 constraint solving.

    Uses :mod:`jugeo.encodings` to produce a Z3-compatible encoding of
    the surface's MRO and attribute-access constraints.

    Parameters
    ----------
    surface : object
        A metaobject surface record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings import encode_value
    except ImportError:
        return None

    kind = type(surface).__name__
    name = getattr(surface, "name", getattr(surface, "metaclass_name", "metaobject"))
    return encode_value(
        label=f"metaobject_{kind}_{name}",
        value=name,
        domain="metaobject",
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import behavioral_surfaces
except Exception:
    pass
try:
    from . import class_creation
except Exception:
    pass
try:
    from . import class_creation_as_staged_semantics
except Exception:
    pass
try:
    from . import descriptor_resolution_routes
except Exception:
    pass
try:
    from . import descriptors
except Exception:
    pass
try:
    from . import generated_behavioral_surfaces
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
    from . import metaclasses
except Exception:
    pass
try:
    from . import metaclasses_as_contract_transforme
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
