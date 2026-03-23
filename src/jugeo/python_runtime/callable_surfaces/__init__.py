"""JuGeo callable_surfaces package — theory2.tex Ch16.

Overview
--------
The ``callable_surfaces`` package implements the five primary callable-surface
constructs introduced in Chapter 16 of ``preliminaries/theory2.tex``:

1. **Callable-surface analysis** (§16.2) — extract and verify the typed
   interface of any Python callable as a :class:`~models.CallableSurface`
   object.  The surface records parameter kinds, annotations, async/generator
   status, and decorator stack as an immutable, hash-stable value.

2. **Method binding** (§16.3) — model the transformation from an unbound
   function to a bound method as a coordinate morphism in the semantic site.
   Binding is tracked as a :class:`~models.MethodBinding` and carries
   provenance back to the originating class coordinate.

3. **Descriptor lookup** (§16.4) — encode the data/non-data descriptor
   precedence ordering (Python data model §3.3.2) as a sheaf restriction map.
   A :class:`~models.DescriptorRecord` carries the ``__get__``/``__set__``/
   ``__delete__`` flags and the resulting lookup priority.

4. **Class construction** (§16.5) — represent the MRO, metaclass, slot
   configuration, and ``__new__``/``__init__`` presence as a
   :class:`~models.ClassConstruction` value.

5. **Signature inspection** (§16.6) — perform full type-annotation resolution
   (following ``from __future__ import annotations`` semantics) and build
   :class:`~models.SignatureRecord` objects for downstream theorem-schema
   generation.

Package structure
-----------------
* ``manifest``    — symbol registry, validation, version tracking, and Ch16
  theory alignment index.
* ``models``      — core frozen and mutable dataclasses for the five callable
  surface constructs plus :class:`~models.SignatureRecord`.
* ``functions``        — §16.2 callable-surface analysis.
* ``method_binding``   — §16.3 method binding.
* ``descriptors``      — §16.4 descriptor lookup.
* ``class_construction``— §16.5 class construction.
* ``algorithms``  — cross-cutting algorithms operating on surfaces and bindings.
* ``integration`` — integration with the JuGeo judgment and solver layers.
* ``theorems``    — formal theorem objects T1–T5 (theory2.tex §16.7).

Copilot integration
--------------------
All copilot-assisted code generation within this sub-package is governed by
the trust algebra defined in theory2.tex Ch.2.  Generated stubs enter at
``TrustLevel.ORACLE_PROPOSED`` (level 2) and must be promoted explicitly
through CI verification before they carry ``SOLVER_DISCHARGED`` (level 4) or
higher trust.  The :attr:`ComponentRegistration.metadata` field carries a
``"copilot_assisted"`` boolean key whenever a component was initially
scaffolded with copilot assistance; this preserves the audit trail required
by §16.9.

Theory alignment
-----------------
Section §16.1 of theory2.tex ("Ch16 Package Overview") is the primary
reference.  Sections §16.2–§16.6 enumerate the five typed callable
constructions; §16.7–§16.10 cover algorithms, integration, theorems, and
the package API surface.

Usage example
-------------
::

    from jugeo.python_runtime.callable_surfaces import (
        build_default_registry,
        TheoremRegistry,
        PACKAGE_VERSION,
    )

    registry = build_default_registry()
    print(registry.report())

Version history
---------------
* ``0.1.0`` — initial scaffold covering §16.1–§16.7.
"""

from __future__ import annotations

import time
import warnings

# ══════════════════════════════════════════════════════════════════════════════
# Package-level constants
# ══════════════════════════════════════════════════════════════════════════════

__version__: str = "0.1.0"
__theory_chapter__: str = "Ch16"
__author__: str = "copilot"

_PACKAGE_NAME: str = "jugeo.python_runtime.callable_surfaces"

# ══════════════════════════════════════════════════════════════════════════════
# Imports from manifest
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.manifest import (
        PACKAGE_VERSION,
        PACKAGE_NAME,
        Capability,
        ComponentRegistration,
        PackageManifest,
        MANIFEST,
        build_manifest,
    )
except ImportError as _manifest_err:
    warnings.warn(
        f"callable_surfaces: could not import from manifest submodule "
        f"({_manifest_err}).  Manifest symbols will be unavailable.",
        ImportWarning,
        stacklevel=2,
    )
    PACKAGE_VERSION = "0.1.0"  # type: ignore[assignment]
    PACKAGE_NAME = "callable_surfaces"  # type: ignore[assignment]
    Capability = None  # type: ignore[assignment,misc]
    ComponentRegistration = None  # type: ignore[assignment,misc]
    PackageManifest = None  # type: ignore[assignment,misc]
    MANIFEST = None  # type: ignore[assignment]
    build_manifest = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from models
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.models import (
        ParameterKind,
        ParameterSpec,
        CallableSurface,
        MethodBinding,
        DescriptorRecord,
        DescriptorKind,
        BoundMethod,
        ClassConstruction,
        SignatureRecord,
    )
except ImportError as _models_err:
    warnings.warn(
        f"callable_surfaces: could not import from models submodule "
        f"({_models_err}).  Core model symbols will be unavailable.",
        ImportWarning,
        stacklevel=2,
    )
    ParameterKind = None  # type: ignore[assignment,misc]
    ParameterSpec = None  # type: ignore[assignment,misc]
    CallableSurface = None  # type: ignore[assignment,misc]
    MethodBinding = None  # type: ignore[assignment,misc]
    DescriptorRecord = None  # type: ignore[assignment,misc]
    DescriptorKind = None  # type: ignore[assignment,misc]
    BoundMethod = None  # type: ignore[assignment,misc]
    ClassConstruction = None  # type: ignore[assignment,misc]
    SignatureRecord = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from functions
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.functions import (
        FunctionMorphismAnalyzer,
        SignatureExtractor,
        AnnotationResolver,
        CallableSurfaceCache,
        surface_from_callable,
        surfaces_are_compatible,
        merge_surfaces,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: functions submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    FunctionMorphismAnalyzer = None  # type: ignore[assignment,misc]
    SignatureExtractor = None  # type: ignore[assignment,misc]
    AnnotationResolver = None  # type: ignore[assignment,misc]
    CallableSurfaceCache = None  # type: ignore[assignment,misc]
    surface_from_callable = None  # type: ignore[assignment]
    surfaces_are_compatible = None  # type: ignore[assignment]
    merge_surfaces = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from method_binding
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.method_binding import (
        MethodBinder,
        MROComputer,
        MethodResolver,
        BindingConstraintChecker,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: method_binding submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    MethodBinder = None  # type: ignore[assignment,misc]
    MROComputer = None  # type: ignore[assignment,misc]
    MethodResolver = None  # type: ignore[assignment,misc]
    BindingConstraintChecker = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from descriptors
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.descriptors import (
        DescriptorProtocol,
        DescriptorInspector,
        PropertyAnalyzer,
        SlotDescriptorAnalyzer,
        DescriptorJudgmentBuilder,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: descriptors submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    DescriptorProtocol = None  # type: ignore[assignment,misc]
    DescriptorInspector = None  # type: ignore[assignment,misc]
    PropertyAnalyzer = None  # type: ignore[assignment,misc]
    SlotDescriptorAnalyzer = None  # type: ignore[assignment,misc]
    DescriptorJudgmentBuilder = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from class_construction
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.class_construction import (
        ClassBuilder,
        MetaclassAnalyzer,
        InitAnalyzer,
        ClassHierarchyTracker,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: class_construction submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    ClassBuilder = None  # type: ignore[assignment,misc]
    MetaclassAnalyzer = None  # type: ignore[assignment,misc]
    InitAnalyzer = None  # type: ignore[assignment,misc]
    ClassHierarchyTracker = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from algorithms
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.algorithms import (
        CallableSurfaceAnalyzer,
        MethodResolutionAlgorithm,
        CallCompatibilityChecker,
        InheritanceGraphAlgorithm,
        DecoratorAnalyzer,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: algorithms submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    CallableSurfaceAnalyzer = None  # type: ignore[assignment,misc]
    MethodResolutionAlgorithm = None  # type: ignore[assignment,misc]
    CallCompatibilityChecker = None  # type: ignore[assignment,misc]
    InheritanceGraphAlgorithm = None  # type: ignore[assignment,misc]
    DecoratorAnalyzer = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from integration
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.integration import (
        CallableJudgmentEmitter,
        Z3CallableEncoder,
        CallableCoordinateMapper,
        SupportRegionBuilder,
        CopilotCallableAdvisor,
    )
except ImportError:
    warnings.warn(
        "callable_surfaces: integration submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )
    CallableJudgmentEmitter = None  # type: ignore[assignment,misc]
    Z3CallableEncoder = None  # type: ignore[assignment,misc]
    CallableCoordinateMapper = None  # type: ignore[assignment,misc]
    SupportRegionBuilder = None  # type: ignore[assignment,misc]
    CopilotCallableAdvisor = None  # type: ignore[assignment,misc]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from theorems
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.theorems import (
        TheoremKind,
        CallableTheorem,
        ArityConsistencyTheorem,
        DescriptorPriorityTheorem,
        MROValidityTheorem,
        BindingValidityTheorem,
        SurfaceCompatibilityTheorem,
        TheoremRegistry,
        build_default_registry,
    )
except ImportError as _theorems_err:
    warnings.warn(
        f"callable_surfaces: could not import from theorems submodule "
        f"({_theorems_err}).  Theorem symbols will be unavailable.",
        ImportWarning,
        stacklevel=2,
    )
    TheoremKind = None  # type: ignore[assignment,misc]
    CallableTheorem = None  # type: ignore[assignment,misc]
    ArityConsistencyTheorem = None  # type: ignore[assignment,misc]
    DescriptorPriorityTheorem = None  # type: ignore[assignment,misc]
    MROValidityTheorem = None  # type: ignore[assignment,misc]
    BindingValidityTheorem = None  # type: ignore[assignment,misc]
    SurfaceCompatibilityTheorem = None  # type: ignore[assignment,misc]
    TheoremRegistry = None  # type: ignore[assignment,misc]
    build_default_registry = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Package metadata helpers
# ══════════════════════════════════════════════════════════════════════════════


def get_package_info() -> dict[str, object]:
    """Return a dictionary of package metadata.

    The returned dict is suitable for logging, introspection tools, and
    health-check endpoints.  It includes version, theory chapter, author,
    and a snapshot of which submodules loaded successfully.

    Returns
    -------
    dict[str, object]
        Dict with keys ``"name"``, ``"version"``, ``"theory_chapter"``,
        ``"author"``, and ``"generated_at"``.
    """
    return {
        "name": _PACKAGE_NAME,
        "version": __version__,
        "theory_chapter": __theory_chapter__,
        "author": __author__,
        "theorems_available": build_default_registry is not None,
        "models_available": CallableSurface is not None,
        "generated_at": time.time(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Version
    "__version__",
    # manifest
    "PACKAGE_VERSION",
    "PACKAGE_NAME",
    "Capability",
    "ComponentRegistration",
    "PackageManifest",
    "MANIFEST",
    "build_manifest",
    # models
    "ParameterKind",
    "ParameterSpec",
    "CallableSurface",
    "MethodBinding",
    "DescriptorRecord",
    "DescriptorKind",
    "BoundMethod",
    "ClassConstruction",
    "SignatureRecord",
    # functions
    "FunctionMorphismAnalyzer",
    "SignatureExtractor",
    "AnnotationResolver",
    "CallableSurfaceCache",
    "surface_from_callable",
    "surfaces_are_compatible",
    "merge_surfaces",
    # method_binding
    "MethodBinder",
    "MROComputer",
    "MethodResolver",
    "BindingConstraintChecker",
    # descriptors
    "DescriptorProtocol",
    "DescriptorInspector",
    "PropertyAnalyzer",
    "SlotDescriptorAnalyzer",
    "DescriptorJudgmentBuilder",
    # class_construction
    "ClassBuilder",
    "MetaclassAnalyzer",
    "InitAnalyzer",
    "ClassHierarchyTracker",
    # algorithms
    "CallableSurfaceAnalyzer",
    "MethodResolutionAlgorithm",
    "CallCompatibilityChecker",
    "InheritanceGraphAlgorithm",
    "DecoratorAnalyzer",
    # integration
    "CallableJudgmentEmitter",
    "Z3CallableEncoder",
    "CallableCoordinateMapper",
    "SupportRegionBuilder",
    "CopilotCallableAdvisor",
    # theorems
    "TheoremKind",
    "CallableTheorem",
    "ArityConsistencyTheorem",
    "DescriptorPriorityTheorem",
    "MROValidityTheorem",
    "BindingValidityTheorem",
    "SurfaceCompatibilityTheorem",
    "TheoremRegistry",
    "build_default_registry",
    # helpers
    "get_package_info",
    # cross-references
    "callable_judgment",
    "callable_encoding",
    "callable_evidence",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def callable_judgment(surface: object) -> tuple:
    """Create an 8-tuple judgment for a callable surface.

    Uses :mod:`jugeo.judgments.judgment_terms` to build a judgment term
    capturing the surface's parameter kinds, annotations, and async status.

    Parameters
    ----------
    surface : object
        A :class:`CallableSurface` or compatible mapping.

    Returns
    -------
    tuple
        An 8-tuple judgment term ``(coord, kind, params, ret, async_, gen, dec, trust)``.
    """
    try:
        from jugeo.judgments.judgment_terms import make_judgment_term
    except ImportError:
        return ("callable", "judgment", surface, None, False, False, (), 0)

    name = getattr(surface, "name", "unknown")
    params = getattr(surface, "parameters", ())
    ret = getattr(surface, "return_annotation", None)
    is_async = getattr(surface, "is_async", False)
    is_gen = getattr(surface, "is_generator", False)
    decorators = getattr(surface, "decorators", ())
    return make_judgment_term(
        coordinate=name,
        kind="callable_surface",
        parameters=params,
        return_type=ret,
        is_async=is_async,
        is_generator=is_gen,
        decorators=decorators,
        trust_level=0,
    )


def callable_encoding(surface: object) -> object:
    """Encode a callable surface for Z3 constraint solving.

    Uses :mod:`jugeo.encodings.scalar_encodings` to produce a Z3-compatible
    encoding of the surface's arity and type constraints.

    Parameters
    ----------
    surface : object
        A :class:`CallableSurface` or compatible mapping.

    Returns
    -------
    object
        A Z3 encoding object, or *None* if encoding is unavailable.
    """
    try:
        from jugeo.encodings.scalar_encodings import encode_scalar
    except ImportError:
        return None

    name = getattr(surface, "name", "callable")
    arity = len(getattr(surface, "parameters", ()))
    return encode_scalar(
        label=f"callable_surface_{name}",
        value=arity,
        domain="nat",
    )


def callable_evidence(surface: object) -> dict:
    """Collect evidence from callable-surface analysis.

    Uses :mod:`jugeo.evidence.channels` to record the analysis result as
    an evidence channel entry for downstream trust aggregation.

    Parameters
    ----------
    surface : object
        A :class:`CallableSurface` or compatible mapping.

    Returns
    -------
    dict
        An evidence record dict with keys ``"channel"``, ``"source"``, and
        ``"payload"``.
    """
    try:
        from jugeo.evidence.channels import record_evidence
    except ImportError:
        return {
            "channel": "callable_surfaces",
            "source": "python_runtime",
            "payload": {"surface": str(surface)},
        }

    return record_evidence(
        channel="callable_surfaces",
        source="python_runtime.callable_surfaces",
        payload={
            "name": getattr(surface, "name", "unknown"),
            "arity": len(getattr(surface, "parameters", ())),
            "is_async": getattr(surface, "is_async", False),
        },
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import class_construction
except Exception:
    pass
try:
    from . import class_objects_construction_pipelin
except Exception:
    pass
try:
    from . import descriptor_lookup_route_tagged_att
except Exception:
    pass
try:
    from . import descriptors
except Exception:
    pass
try:
    from . import function_values_and_method_values
except Exception:
    pass
try:
    from . import functions
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
    from . import method_binding
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
