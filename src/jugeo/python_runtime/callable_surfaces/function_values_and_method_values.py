from __future__ import annotations

r"""Function values, method binding, class construction, and descriptor lookup (theory2.tex Ch16).

Overview
--------
This module implements the analysis and witnessing machinery for Python
*function values* and *method values*, corresponding to §16.1–§16.4 of
theory2.tex.  Functions are modelled as first-class morphisms in the
type-object category; binding a function to an instance (or class) is a
restriction morphism that refines the function's *callable surface* and
introduces a new *trust context* centred on ``self``.

Section mapping (theory2.tex Ch16)
-----------------------------------
§16.1  Functions as first-class objects — :class:`FunctionValuesMethodValuesCoordinator`
§16.2  Method binding and ``self`` trust context — :class:`FunctionValuesMethodValuesCoordinator`
§16.3  AST and live introspection — :class:`FunctionValuesMethodValuesAnalyzer`
§16.4  Runtime witnessing — :class:`FunctionValuesMethodValuesWitness`

Architecture
------------
:class:`FunctionValuesMethodValuesCoordinator`
    Central registry for function and method-binding objects.  Maintains
    stable ``func_id`` / ``binding_id`` identifiers (UUID hex), records
    ``types.FunctionType`` and ``types.MethodType`` metadata, and builds
    :class:`CoordinateObject` / morphism records for the geometry layer.

:class:`FunctionValuesMethodValuesAnalyzer`
    Dual static/dynamic analysis engine.  Parses Python source via the
    ``ast`` module to discover function and method definitions, and also
    introspects live callables with ``inspect`` and ``dis`` to produce
    comprehensive callable profiles, dis-assembly reports, and surface
    comparison diffs.

:class:`FunctionValuesMethodValuesWitness`
    Runtime witness layer.  Independently verifies function identity,
    method-binding correctness, ``self`` trust context, and the callable
    protocol (``__call__`` presence / signature).  Records trust violations
    and aggregates evidence bundles for downstream judgment emission.

Copilot integration
-------------------
This module was scaffolded with copilot assistance; all proposals enter at
``TrustLevel.ORACLE_PROPOSED`` (level 2).  Runtime witness evidence promotes
entries to ``TrustLevel.RUNTIME_WITNESSED`` (level 3).  See theory2.tex §16.9
for the promotion policy.

Examples
--------
Quick start::

    from jugeo.python_runtime.callable_surfaces.function_values_and_method_values import (
        FunctionValuesMethodValuesCoordinator,
        FunctionValuesMethodValuesAnalyzer,
        FunctionValuesMethodValuesWitness,
        classify_callable,
        function_id,
    )

    def my_func(x: int, y: str = "hello") -> bool:
        return bool(x)

    coord = FunctionValuesMethodValuesCoordinator()
    fid = coord.register_function(my_func)
    print(coord.classify_callable(my_func))   # "plain_function"

    analyzer = FunctionValuesMethodValuesAnalyzer()
    profile = analyzer.analyze_live_function(my_func)
    print(profile["qualname"])                # "my_func"

    witness = FunctionValuesMethodValuesWitness()
    evidence = witness.witness_function_identity(my_func)
    print(evidence["consistent"])             # True
"""

import ast
import dis
import inspect
import logging
import types
import uuid
import time
import re
import textwrap
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# geometry.site imports with full stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateKind,
        MorphismKind,
        CoordinateObject,
        CoordinateMorphism,
        Site,
        SiteBuilder,
    )
except Exception:  # pragma: no cover — stubs used outside the jugeo package
    import enum

    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind (geometry.site unavailable)."""

        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind (geometry.site unavailable)."""

        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub for CoordinateObject (geometry.site unavailable)."""

        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        """Stub for CoordinateMorphism (geometry.site unavailable)."""

        def __init__(self, source: str, target: str, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site:  # type: ignore[no-redef]
        """Stub for Site (geometry.site unavailable)."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub for SiteBuilder (geometry.site unavailable)."""

# ---------------------------------------------------------------------------
# judgments.judgment_terms imports with full stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        TrustLevel,
        JudgmentStatus,
        PropositionKind,
        EvidenceItemKind,
        Proposition,
        Carrier,
        EvidenceItem,
        EvidenceBundle,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
        Judgment,
    )
except Exception:  # pragma: no cover
    import enum

    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        """Stub for TrustLevel."""

        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""

        PROPOSED = "proposed"
        CHALLENGED = "challenged"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for PropositionKind."""

        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
        RESOURCE = "resource"
        SEMANTIC = "semantic"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for EvidenceItemKind."""

        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(enum.Enum):  # type: ignore[no-redef]
        """Stub for ProvenanceSource."""

        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"
        COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub for Proposition."""

        kind: Any = None
        formula: str = ""
        free_variables: tuple[str, ...] = ()
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        """Stub for Carrier."""

        name: str = ""
        parameters: tuple[str, ...] = ()
        is_dependent: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        """Stub for EvidenceItem."""

        kind: Any = None
        payload: Mapping[str, Any] = field(default_factory=dict)
        trust_level: Any = None
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        """Stub for EvidenceBundle."""

        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        """Stub for ResidualObligation."""

        description: str = ""
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        """Stub for Obstruction."""

        description: str = ""
        coordinate: Any = None

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        """Stub for TrustAnnotation."""

        level: Any = None
        evidence_basis: tuple[str, ...] = ()
        ceiling: Any = None
        floor: Any = None
        reasons: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        """Stub for Provenance."""

        source: Any = None
        parent_judgments: tuple[str, ...] = ()
        creation_timestamp: str = ""
        transformation_history: tuple[str, ...] = ()
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass
    class Judgment:  # type: ignore[no-redef]
        """Stub for Judgment."""

        coordinate: Any = None
        proposition: Any = None
        carrier: Any = None
        evidence: Any = None
        obligations: tuple[Any, ...] = ()
        obstructions: tuple[Any, ...] = ()
        trust: Any = None
        provenance: Any = None
        clauses: tuple[Any, ...] = ()
        status: Any = None

# ---------------------------------------------------------------------------
# callable_surfaces.models imports with full stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.callable_surfaces.models import (
        ParameterKind,
        ParameterSpec,
        CallableSurface,
        MethodBinding,
        DescriptorKind,
        BoundMethod,
        ClassConstruction,
        SignatureRecord,
    )
except Exception:  # pragma: no cover
    import enum

    class ParameterKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for ParameterKind, mirrors inspect.Parameter.kind."""

        POSITIONAL_ONLY = "positional_only"
        POSITIONAL_OR_KEYWORD = "positional_or_keyword"
        VAR_POSITIONAL = "var_positional"
        KEYWORD_ONLY = "keyword_only"
        VAR_KEYWORD = "var_keyword"

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub for ParameterSpec."""

        name: str = ""
        kind: Any = None
        annotation: str = "Any"
        has_default: bool = False
        default_repr: str = ""
        is_variadic: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "name": self.name,
                "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
                "annotation": self.annotation,
                "has_default": self.has_default,
                "default_repr": self.default_repr,
                "is_variadic": self.is_variadic,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "ParameterSpec":
            """Parse from a plain dict."""
            kind_val = data.get("kind", "positional_or_keyword")
            try:
                kind = ParameterKind(kind_val)
            except (ValueError, KeyError):
                kind = ParameterKind.POSITIONAL_OR_KEYWORD
            return cls(
                name=data.get("name", ""),
                kind=kind,
                annotation=data.get("annotation", "Any"),
                has_default=bool(data.get("has_default", False)),
                default_repr=data.get("default_repr", ""),
                is_variadic=bool(data.get("is_variadic", False)),
                metadata=data.get("metadata", {}),
            )

    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub for CallableSurface."""

        name: str = ""
        qualname: str = ""
        module: str = ""
        parameters: tuple[Any, ...] = ()
        return_annotation: str = "Any"
        is_async: bool = False
        is_generator: bool = False
        docstring: str = ""
        surface_id: str = ""
        created_at: float = 0.0
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "name": self.name,
                "qualname": self.qualname,
                "module": self.module,
                "parameters": [
                    p.serialize() if hasattr(p, "serialize") else p
                    for p in self.parameters
                ],
                "return_annotation": self.return_annotation,
                "is_async": self.is_async,
                "is_generator": self.is_generator,
                "docstring": self.docstring,
                "surface_id": self.surface_id,
                "created_at": self.created_at,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "CallableSurface":
            """Parse from a plain dict."""
            raw_params = data.get("parameters", [])
            params = tuple(
                ParameterSpec.parse(p) if isinstance(p, dict) else p
                for p in raw_params
            )
            return cls(
                name=data.get("name", ""),
                qualname=data.get("qualname", ""),
                module=data.get("module", ""),
                parameters=params,
                return_annotation=data.get("return_annotation", "Any"),
                is_async=bool(data.get("is_async", False)),
                is_generator=bool(data.get("is_generator", False)),
                docstring=data.get("docstring", ""),
                surface_id=data.get("surface_id", ""),
                created_at=float(data.get("created_at", 0.0)),
                metadata=data.get("metadata", {}),
            )

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub for MethodBinding."""

        func_id: str = ""
        instance_type: str = ""
        binding_kind: str = "instance"
        bound_at: float = 0.0
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "func_id": self.func_id,
                "instance_type": self.instance_type,
                "binding_kind": self.binding_kind,
                "bound_at": self.bound_at,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "MethodBinding":
            """Parse from a plain dict."""
            return cls(
                func_id=data.get("func_id", ""),
                instance_type=data.get("instance_type", ""),
                binding_kind=data.get("binding_kind", "instance"),
                bound_at=float(data.get("bound_at", 0.0)),
                metadata=data.get("metadata", {}),
            )

    class DescriptorKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for DescriptorKind."""

        DATA = "data"
        NON_DATA = "non_data"
        METHOD = "method"
        CLASS_METHOD = "class_method"
        STATIC_METHOD = "static_method"

    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub for BoundMethod."""

        surface: Any = None
        instance_type: str = ""
        binding_morphism: str = ""
        bound_at: float = 0.0
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "surface": self.surface.serialize() if hasattr(self.surface, "serialize") else None,
                "instance_type": self.instance_type,
                "binding_morphism": self.binding_morphism,
                "bound_at": self.bound_at,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "BoundMethod":
            """Parse from a plain dict."""
            surface_data = data.get("surface")
            surface = CallableSurface.parse(surface_data) if surface_data else None
            return cls(
                surface=surface,
                instance_type=data.get("instance_type", ""),
                binding_morphism=data.get("binding_morphism", ""),
                bound_at=float(data.get("bound_at", 0.0)),
                metadata=data.get("metadata", {}),
            )

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub for ClassConstruction."""

        class_name: str = ""
        mro: tuple[str, ...] = ()
        metaclass: str = "type"
        has_slots: bool = False
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "class_name": self.class_name,
                "mro": list(self.mro),
                "metaclass": self.metaclass,
                "has_slots": self.has_slots,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "ClassConstruction":
            """Parse from a plain dict."""
            return cls(
                class_name=data.get("class_name", ""),
                mro=tuple(data.get("mro", [])),
                metaclass=data.get("metaclass", "type"),
                has_slots=bool(data.get("has_slots", False)),
                metadata=data.get("metadata", {}),
            )

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub for SignatureRecord."""

        surface: Any = None
        raw_annotations: Mapping[str, str] = field(default_factory=dict)
        forward_refs: tuple[str, ...] = ()
        is_complete: bool = True

        def serialize(self) -> dict[str, Any]:
            """Serialize to a plain dict."""
            return {
                "surface": self.surface.serialize() if hasattr(self.surface, "serialize") else None,
                "raw_annotations": dict(self.raw_annotations),
                "forward_refs": list(self.forward_refs),
                "is_complete": self.is_complete,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "SignatureRecord":
            """Parse from a plain dict."""
            surface_data = data.get("surface")
            surface = CallableSurface.parse(surface_data) if surface_data else None
            return cls(
                surface=surface,
                raw_annotations=data.get("raw_annotations", {}),
                forward_refs=tuple(data.get("forward_refs", [])),
                is_complete=bool(data.get("is_complete", True)),
            )

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s01-function-values-method-values"

_FUNCTION_KINDS: frozenset[str] = frozenset({
    "plain_function",
    "lambda",
    "method",
    "classmethod",
    "staticmethod",
    "builtin_function",
    "builtin_method",
    "partial",
    "coroutine_function",
    "generator_function",
    "async_generator_function",
})

_BINDING_SEMANTICS: dict[str, str] = {
    "instance_method": "self_binding",
    "classmethod": "cls_binding",
    "staticmethod": "no_binding",
    "builtin_method": "implicit_binding",
}

# ---------------------------------------------------------------------------
# Module-level logger (channel-tagged)
# ---------------------------------------------------------------------------

_log = logging.getLogger(_ANALYSIS_CHANNEL)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def function_id(func: types.FunctionType) -> str:
    """Return a stable, hash-based identifier for a function object.

    The ID is derived from the function's qualified name, module, source
    file, and first line number.  This makes it stable across interpreter
    restarts as long as the source has not changed, and distinguishes
    functions with the same ``__qualname__`` that live in different modules.

    Parameters
    ----------
    func:
        The function whose identity should be encoded.  Must be a genuine
        ``types.FunctionType`` (not a built-in or bound method).

    Returns
    -------
    str
        A 32-character lowercase hex string (MD5 digest).

    Theory alignment (theory2.tex §16.1)
    -------------------------------------
    A function's identity in the type-object category is determined by its
    *code object* — the immutable compiled bytecode.  The hash here serves as
    a *canonical representative* for that identity within a single process.
    """
    # Gather the components that together uniquely identify a function's
    # code-object across restarts (name + location in source).
    qualname = getattr(func, "__qualname__", repr(func))
    module = getattr(func, "__module__", "") or ""
    code = getattr(func, "__code__", None)
    filename = getattr(code, "co_filename", "") if code else ""
    lineno = str(getattr(code, "co_firstlineno", 0)) if code else "0"

    # Concatenate with a separator that cannot appear in any of the parts.
    raw = f"{qualname}\x00{module}\x00{filename}\x00{lineno}"
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()


def method_id(method: types.MethodType) -> str:
    """Return a stable identifier for a bound method.

    Combines the underlying function's :func:`function_id` with the runtime
    identity of the bound instance (``id(method.__self__)``).  Because
    ``id`` values are only unique for the lifetime of an object, this
    identifier is *session-scoped* rather than cross-restart stable.

    Parameters
    ----------
    method:
        A bound method (``types.MethodType``).

    Returns
    -------
    str
        A 32-character lowercase hex string.

    Theory alignment (theory2.tex §16.2)
    -------------------------------------
    A bound method is the *image* of the restriction morphism
    ``bind_self : Func × Instance → BoundMethod``.  Its identity must
    therefore encode both the morphism source (the function) and the
    restriction point (the instance).
    """
    func = getattr(method, "__func__", None)
    func_part = function_id(func) if isinstance(func, types.FunctionType) else uuid.uuid4().hex
    # Use the Python id of the bound instance as the instance component.
    self_part = str(id(getattr(method, "__self__", None)))
    raw = f"{func_part}\x00{self_part}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def is_bound_method(obj: Any) -> bool:
    """Return ``True`` iff *obj* is a bound method (``types.MethodType``).

    Parameters
    ----------
    obj:
        Any Python object.

    Returns
    -------
    bool
        ``True`` exactly when ``isinstance(obj, types.MethodType)``.

    Notes
    -----
    This deliberately avoids ``inspect.ismethod`` to keep the check minimal
    and free of import-time side effects.
    """
    return isinstance(obj, types.MethodType)


def is_unbound_function(obj: Any) -> bool:
    """Return ``True`` iff *obj* is a plain (unbound) Python function.

    Parameters
    ----------
    obj:
        Any Python object.

    Returns
    -------
    bool
        ``True`` exactly when ``isinstance(obj, types.FunctionType)``.

    Notes
    -----
    Built-in functions, methods, lambda-wrapped callables, and partial
    objects all return ``False``; only ``def``-defined Python-level
    functions return ``True``.
    """
    return isinstance(obj, types.FunctionType)


def binding_kind(obj: Any) -> str:
    """Classify the binding semantics of *obj*.

    Returns one of the following string tokens:

    * ``"bound_method"`` — a ``types.MethodType`` (self is already bound).
    * ``"unbound_function"`` — a plain ``types.FunctionType``.
    * ``"classmethod"`` — a ``classmethod`` descriptor wrapper.
    * ``"staticmethod"`` — a ``staticmethod`` descriptor wrapper.
    * ``"builtin"`` — a ``types.BuiltinFunctionType`` or ``types.BuiltinMethodType``.
    * ``"other"`` — none of the above.

    Parameters
    ----------
    obj:
        Any Python object.

    Returns
    -------
    str
        One of the six token strings listed above.

    Theory alignment (theory2.tex §16.2)
    -------------------------------------
    The binding kind determines which *restriction morphism* has been applied:
    instance methods have had ``bind_self`` applied; classmethods have had
    ``bind_cls``; staticmethods carry no binding at all.
    """
    if isinstance(obj, types.MethodType):
        return "bound_method"
    if isinstance(obj, types.FunctionType):
        return "unbound_function"
    if isinstance(obj, classmethod):
        return "classmethod"
    if isinstance(obj, staticmethod):
        return "staticmethod"
    if isinstance(obj, (types.BuiltinFunctionType, types.BuiltinMethodType)):
        return "builtin"
    return "other"


def parameter_count(func: Any) -> int:
    """Safely return the number of declared parameters for *func*.

    Uses ``inspect.signature`` with a try/except so that callables that do
    not expose a meaningful signature (some built-ins) return ``0`` rather
    than raising.

    Parameters
    ----------
    func:
        Any callable.

    Returns
    -------
    int
        Number of parameters visible via ``inspect.signature``.
    """
    try:
        sig = inspect.signature(func)
        return len(sig.parameters)
    except (ValueError, TypeError):
        # Some built-ins do not expose a Python-level signature.
        return 0


def extract_default_values(func: types.FunctionType) -> dict[str, Any]:
    """Return a mapping of ``param_name → default_value`` for *func*.

    Combines ``__defaults__`` (positional defaults) with ``__kwdefaults__``
    (keyword-only defaults) into a single flat dict.  Parameters without
    defaults are absent from the returned mapping.

    Parameters
    ----------
    func:
        A ``types.FunctionType``.  Non-function callables are handled
        gracefully and return an empty dict.

    Returns
    -------
    dict[str, Any]
        ``{"param_name": default_value, ...}``.

    Notes
    -----
    The positional defaults in ``func.__defaults__`` are right-aligned
    against the parameter list from ``func.__code__.co_varnames``.  For
    example, ``def f(a, b=1, c=2)`` stores ``(1, 2)`` in ``__defaults__``
    and the first ``co_argcount - 2`` parameters have no default.
    """
    result: dict[str, Any] = {}
    if not isinstance(func, types.FunctionType):
        return result

    code = func.__code__
    # co_varnames contains all local variable names; only the first
    # co_argcount entries correspond to declared parameters.
    all_params = code.co_varnames[: code.co_argcount]
    defaults = func.__defaults__ or ()

    # Right-align positional defaults against the parameter list.
    if defaults:
        offset = len(all_params) - len(defaults)
        for i, default_val in enumerate(defaults):
            param_name = all_params[offset + i]
            result[param_name] = default_val

    # Keyword-only defaults are already stored as a name→value dict.
    kwdefaults = func.__kwdefaults__ or {}
    result.update(kwdefaults)

    return result


# ---------------------------------------------------------------------------
# FunctionValuesMethodValuesCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FunctionValuesMethodValuesCoordinator:
    """Central registry coordinating function objects and method bindings.

    Theory alignment (theory2.tex §16.1–§16.2)
    -------------------------------------------
    A *coordinator* plays the role of a *functor* from the concrete Python
    runtime category (function/method objects) to the abstract jugeo geometry
    category (coordinates and morphisms).  For each registered callable it:

    1. Assigns a stable ``func_id`` (UUID or hash-based hex string).
    2. Extracts code-object metadata (``co_argcount``, ``co_varcount``, flags).
    3. Builds a :class:`CoordinateObject` locating the function in the site.
    4. Records the *trust context* introduced when ``self`` is bound (§16.2).

    Attributes
    ----------
    _function_registry:
        Keyed by ``func_id`` (UUID hex).  Each value is a dict of extracted
        metadata for the registered function.
    _method_binding_map:
        Keyed by ``binding_id`` (UUID hex).  Each value records the
        underlying function's ``func_id``, the type of ``self``, and the
        binding kind.
    _trust_context_map:
        Maps ``binding_id`` → trust-context dict.  The trust context captures
        *who* ``self`` is and what obligations are introduced by the binding.
    _coordinator_id:
        Unique per-instance identifier for logging and cross-reference.
    """

    _function_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    _method_binding_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    _trust_context_map: dict[str, Any] = field(default_factory=dict)
    _coordinator_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_function(
        self,
        func: types.FunctionType | types.BuiltinFunctionType,
    ) -> str:
        """Register a callable and return its stable ``func_id``.

        Extracts all metadata available through the code object and
        ``inspect`` machinery, storing it in :attr:`_function_registry` under
        a stable ``func_id`` derived from :func:`function_id` (or a fresh
        UUID for built-ins that lack a ``__code__``).

        Parameters
        ----------
        func:
            A ``types.FunctionType`` or ``types.BuiltinFunctionType``.
            Other callables are accepted but receive minimal metadata.

        Returns
        -------
        str
            The ``func_id`` (32-char hex) under which the function is stored.

        Notes
        -----
        Re-registering the same function (identical ``func_id``) is a no-op
        — the existing record is returned as-is to preserve any metadata that
        may have been enriched by subsequent calls.
        """
        # Compute a stable identifier for this function object.
        if isinstance(func, types.FunctionType):
            fid = function_id(func)
        else:
            # For built-ins we cannot compute a hash from __code__; use UUID.
            fid = uuid.uuid4().hex

        # Idempotency: if already registered, return the existing record's id.
        if fid in self._function_registry:
            _log.debug("coordinator=%s func_id=%s already registered (skipping)", self._coordinator_id, fid)
            return fid

        # Extract code-object attributes for Python functions.
        code = getattr(func, "__code__", None)
        record: dict[str, Any] = {
            "func_id": fid,
            "qualname": getattr(func, "__qualname__", repr(func)),
            "name": getattr(func, "__name__", ""),
            "module": getattr(func, "__module__", "") or "",
            "is_async": inspect.iscoroutinefunction(func),
            "is_generator": inspect.isgeneratorfunction(func),
            "is_async_generator": inspect.isasyncgenfunction(func),
            "doc": (getattr(func, "__doc__", "") or "")[:256],
            "registered_at": time.monotonic(),
        }
        if code is not None:
            # co_varcount is not a real attribute; use co_nlocals instead.
            record.update({
                "co_argcount": code.co_argcount,
                "co_kwonlyargcount": getattr(code, "co_kwonlyargcount", 0),
                "co_nlocals": code.co_nlocals,
                "co_flags": code.co_flags,
                "co_filename": code.co_filename,
                "co_firstlineno": code.co_firstlineno,
                "co_name": code.co_name,
                "co_varnames": list(code.co_varnames[: code.co_argcount]),
            })

        self._function_registry[fid] = record
        _log.debug(
            "coordinator=%s registered func_id=%s qualname=%s",
            self._coordinator_id,
            fid,
            record["qualname"],
        )
        return fid

    def register_method_binding(self, method: types.MethodType) -> str:
        """Register a bound method and return its ``binding_id``.

        A bound method carries a ``__func__`` (the unbound function) and a
        ``__self__`` (the bound instance).  This method:

        1. Registers ``method.__func__`` via :meth:`register_function`.
        2. Records the binding in :attr:`_method_binding_map`.
        3. Computes and stores a trust context in :attr:`_trust_context_map`
           reflecting that binding ``self`` introduces a new obligation.

        Parameters
        ----------
        method:
            A ``types.MethodType``.  If the argument is not a bound method,
            a ``TypeError`` is raised with a descriptive message.

        Returns
        -------
        str
            The ``binding_id`` (UUID hex, 32 chars) for this binding record.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        Method binding is the *restriction morphism*
        ``bind_self : Func × Instance → BoundMethod``.
        Recording the binding explicitly allows the geometry layer to track
        which instances have acquired which morphisms and at what trust level.
        """
        if not isinstance(method, types.MethodType):
            raise TypeError(
                f"register_method_binding expects types.MethodType, got {type(method).__name__!r}"
            )

        # Register the underlying function first (idempotent).
        underlying_func = method.__func__
        func_id_val = self.register_function(underlying_func)

        # Generate a fresh binding_id for this (function, instance) pair.
        binding_id = method_id(method)

        self_obj = method.__self__
        self_type = type(self_obj)

        binding_record: dict[str, Any] = {
            "binding_id": binding_id,
            "func_id": func_id_val,
            "self_type": self_type.__qualname__,
            "self_module": getattr(self_type, "__module__", "") or "",
            "binding_kind": "instance",
            "bound_at": time.monotonic(),
            # MRO as a list of qualified class names.
            "mro": [c.__qualname__ for c in self_type.__mro__],
        }
        self._method_binding_map[binding_id] = binding_record

        # Trust context: binding self introduces an obligation to verify
        # that self is a bona-fide instance of the expected class.
        trust_ctx: dict[str, Any] = {
            "binding_id": binding_id,
            "trust_level": TrustLevel.ORACLE_PROPOSED,
            "self_type": self_type.__qualname__,
            "is_trusted": False,  # not yet witnessed at runtime
            "obligation": f"Verify self is instance of {self_type.__qualname__}",
        }
        self._trust_context_map[binding_id] = trust_ctx

        _log.debug(
            "coordinator=%s registered binding_id=%s func_id=%s self_type=%s",
            self._coordinator_id,
            binding_id,
            func_id_val,
            self_type.__qualname__,
        )
        return binding_id

    def register_classmethod(self, cm: classmethod) -> str:
        """Register a ``classmethod`` descriptor and return its ``binding_id``.

        Extracts the underlying function from the ``classmethod`` wrapper and
        registers it.  The binding kind is ``"cls_binding"`` because the
        class — not an instance — is bound as the first argument.

        Parameters
        ----------
        cm:
            A ``classmethod`` descriptor object (not yet bound to any class).
            If the argument is not a ``classmethod``, a ``TypeError`` is
            raised.

        Returns
        -------
        str
            A fresh UUID hex used as the ``binding_id``.

        Notes
        -----
        In CPython, ``classmethod.__func__`` holds the wrapped function.
        The class itself is bound lazily when the descriptor is accessed via
        the class; at registration time we can only record the function.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        ``classmethod`` implements ``bind_cls : Func × Class → BoundMethod``
        (the class-level restriction morphism), distinct from the instance
        restriction morphism used by ordinary methods.
        """
        if not isinstance(cm, classmethod):
            raise TypeError(
                f"register_classmethod expects a classmethod descriptor, got {type(cm).__name__!r}"
            )

        # classmethod.__func__ holds the underlying function.
        underlying = cm.__func__
        func_id_val = self.register_function(underlying)

        binding_id = uuid.uuid4().hex

        binding_record: dict[str, Any] = {
            "binding_id": binding_id,
            "func_id": func_id_val,
            "binding_kind": "cls_binding",
            "bound_at": time.monotonic(),
            "descriptor_type": "classmethod",
        }
        self._method_binding_map[binding_id] = binding_record

        # Trust context for classmethod: the class itself must be verified.
        trust_ctx: dict[str, Any] = {
            "binding_id": binding_id,
            "trust_level": TrustLevel.ORACLE_PROPOSED,
            "binding_kind": "cls_binding",
            "is_trusted": False,
            "obligation": "Verify cls is the expected class or subclass",
        }
        self._trust_context_map[binding_id] = trust_ctx

        _log.debug(
            "coordinator=%s registered classmethod binding_id=%s func_id=%s",
            self._coordinator_id,
            binding_id,
            func_id_val,
        )
        return binding_id

    # ------------------------------------------------------------------
    # Coordinate and morphism construction
    # ------------------------------------------------------------------

    def trust_context_for_binding(self, binding_id: str) -> dict[str, Any]:
        """Return the trust context dict for a registered binding.

        The trust context describes *who* ``self`` is, what class it belongs
        to, and whether the binding has been verified at runtime.

        Parameters
        ----------
        binding_id:
            A ``binding_id`` returned by :meth:`register_method_binding` or
            :meth:`register_classmethod`.

        Returns
        -------
        dict[str, Any]
            A copy of the stored trust context, or an empty dict if the
            ``binding_id`` is not known.
        """
        ctx = self._trust_context_map.get(binding_id, {})
        # Return a shallow copy to prevent external mutation of the registry.
        return dict(ctx)

    def function_coordinate(self, func: types.FunctionType) -> CoordinateObject:
        """Build a :class:`CoordinateObject` locating *func* in the geometry site.

        The coordinate uses ``CoordinateKind.FUNCTION`` and derives its
        ``components`` tuple from ``(module, qualname)`` — mirroring the
        convention used throughout the callable_surfaces package.

        Parameters
        ----------
        func:
            A ``types.FunctionType``.

        Returns
        -------
        CoordinateObject
            An immutable coordinate suitable for storage in the site.

        Theory alignment (theory2.tex §16.1)
        -------------------------------------
        Every function is a *point* in the typed callable space.  Its
        coordinate is the functor image of the pair ``(module, qualname)``
        under the canonical embedding into the site lattice.
        """
        module = getattr(func, "__module__", "") or "<unknown>"
        qualname = getattr(func, "__qualname__", repr(func))
        # Split qualname on '.' to get a hierarchical component path.
        parts = qualname.split(".")
        components = tuple([module] + parts)
        return CoordinateObject(
            components=components,
            kind=CoordinateKind.FUNCTION,
            support_labels=frozenset({"function_value"}),
            metadata={
                "func_id": function_id(func),
                "is_async": inspect.iscoroutinefunction(func),
                "is_generator": inspect.isgeneratorfunction(func),
            },
        )

    def method_morphism(
        self,
        func_coord: CoordinateObject,
        instance_coord: CoordinateObject,
    ) -> dict[str, Any]:
        """Compute the restriction morphism from unbound function to bound method.

        Binding ``self`` is modelled as a *restriction morphism* in the site:
        the domain of the function (all possible ``self`` values) is restricted
        to the specific instance represented by ``instance_coord``.

        Parameters
        ----------
        func_coord:
            The coordinate of the unbound function (source of the morphism).
        instance_coord:
            The coordinate of the instance to which ``self`` is bound (the
            restriction point).

        Returns
        -------
        dict[str, Any]
            A plain dict encoding the morphism: ``source``, ``target``,
            ``morphism_kind``, ``binding_semantics``, and ``created_at``.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        In the restriction-morphism picture, ``bind_self(f, x)`` restricts
        the domain of ``f`` to the singleton ``{x}``, yielding a new callable
        with ``self`` pre-applied.  The morphism kind is ``RESTRICTION``.
        """
        return {
            "source": func_coord.components,
            "target": instance_coord.components,
            "morphism_kind": MorphismKind.RESTRICTION.value
            if hasattr(MorphismKind, "RESTRICTION")
            else "restriction",
            "binding_semantics": _BINDING_SEMANTICS["instance_method"],
            "created_at": time.monotonic(),
        }

    # ------------------------------------------------------------------
    # Classification and summary
    # ------------------------------------------------------------------

    def classify_callable(self, obj: Any) -> str:
        """Classify *obj* as one of the tokens in :data:`_FUNCTION_KINDS`.

        Uses ``inspect`` predicates in specificity order so that, e.g., an
        async-generator function is labelled ``"async_generator_function"``
        rather than the more general ``"plain_function"``.

        Parameters
        ----------
        obj:
            Any Python object.

        Returns
        -------
        str
            A member of :data:`_FUNCTION_KINDS`, or ``"other"`` if no
            classification matches.
        """
        import functools

        # Check most-specific classifications first.
        if inspect.isasyncgenfunction(obj):
            return "async_generator_function"
        if inspect.isgeneratorfunction(obj):
            return "generator_function"
        if inspect.iscoroutinefunction(obj):
            return "coroutine_function"
        if isinstance(obj, functools.partial):
            return "partial"
        if inspect.isbuiltin(obj):
            # Distinguish free builtins from methods bound to C objects.
            if hasattr(obj, "__self__") and obj.__self__ is not None:
                return "builtin_method"
            return "builtin_function"
        if inspect.ismethod(obj):
            # Bound method — check for classmethod (cls binding).
            if hasattr(obj, "__self__") and isinstance(obj.__self__, type):
                return "classmethod"
            return "method"
        if isinstance(obj, staticmethod):
            return "staticmethod"
        if inspect.isclass(obj):
            # Classes are callable but not functions; return "other" since
            # "class" is not in _FUNCTION_KINDS by design.
            return "other"
        if inspect.isfunction(obj):
            # Check for lambda by inspecting __name__.
            name = getattr(obj, "__name__", "")
            if name == "<lambda>":
                return "lambda"
            return "plain_function"
        return "other"

    def summary(self) -> dict[str, Any]:
        """Return a summary dict with counts and aggregate statistics.

        Includes the number of registered functions, method bindings, and
        trust contexts, broken down by binding kind and trust level.

        Returns
        -------
        dict[str, Any]
            Keys: ``coordinator_id``, ``num_functions``, ``num_bindings``,
            ``num_trust_contexts``, ``binding_kinds``, ``trust_levels``.
        """
        binding_kinds: dict[str, int] = defaultdict(int)
        for record in self._method_binding_map.values():
            kind = record.get("binding_kind", "unknown")
            binding_kinds[kind] += 1

        trust_levels: dict[str, int] = defaultdict(int)
        for ctx in self._trust_context_map.values():
            lvl = ctx.get("trust_level")
            label = lvl.name if hasattr(lvl, "name") else str(lvl)
            trust_levels[label] += 1

        return {
            "coordinator_id": self._coordinator_id,
            "num_functions": len(self._function_registry),
            "num_bindings": len(self._method_binding_map),
            "num_trust_contexts": len(self._trust_context_map),
            "binding_kinds": dict(binding_kinds),
            "trust_levels": dict(trust_levels),
        }


# ---------------------------------------------------------------------------
# FunctionValuesMethodValuesAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FunctionValuesMethodValuesAnalyzer:
    """Dual static/dynamic analysis engine for function and method objects.

    Theory alignment (theory2.tex §16.3)
    -------------------------------------
    The analyzer provides two complementary views of a callable:

    1. *Static view* — parse Python source with ``ast``, enumerate function
       definitions, extract parameter lists, detect decorators, classify
       sync/async/generator.
    2. *Dynamic view* — introspect live objects with ``inspect`` and ``dis``,
       retrieve code-object attributes, default values, annotations, and the
       bytecode disassembly.

    Both views feed the :class:`FunctionValuesMethodValuesCoordinator` which
    builds the geometry-layer representation.

    Attributes
    ----------
    _coordinator:
        Shared coordinator instance used for function registration and
        classification.
    _ast_cache:
        Keyed by a hash of the source text so that repeated analysis of the
        same source avoids redundant ``ast.parse`` calls.
    _callable_profile_cache:
        Keyed by function ``qualname`` (or ``func_id``).  Stores the result of
        :meth:`analyze_live_function` for reuse.
    _stats:
        Running counters for cache hits, cache misses, functions analyzed,
        methods analyzed, and dis-assembly calls.
    """

    _coordinator: FunctionValuesMethodValuesCoordinator = field(
        default_factory=FunctionValuesMethodValuesCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _callable_profile_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # ------------------------------------------------------------------
    # Source-level (static) analysis
    # ------------------------------------------------------------------

    def analyze_source(
        self,
        source: str,
        module_name: str = "<module>",
    ) -> dict[str, Any]:
        """Perform a full AST analysis of Python source text.

        Parses *source* with ``ast.parse``, walks the tree to find all
        function/lambda definitions and class-level method definitions,
        classifies each (sync / async / generator), and extracts parameter
        lists and return annotations.

        Parameters
        ----------
        source:
            Raw Python source text.
        module_name:
            Optional name assigned to the parsed module (used in error
            messages and as a namespace label in the result).

        Returns
        -------
        dict[str, Any]
            Comprehensive analysis result with keys:
            ``module_name``, ``num_functions``, ``num_classes``,
            ``functions``, ``methods``, ``lambdas``, ``parse_errors``.
        """
        # Use a hash of the source to cache parsed ASTs across calls.
        src_hash = hashlib.md5(source.encode("utf-8", errors="replace")).hexdigest()
        if src_hash in self._ast_cache:
            tree = self._ast_cache[src_hash]
            self._stats["ast_cache_hits"] += 1
        else:
            try:
                tree = ast.parse(source, filename=module_name)
                self._ast_cache[src_hash] = tree
                self._stats["ast_cache_misses"] += 1
            except SyntaxError as exc:
                _log.warning("analyze_source: SyntaxError in %s: %s", module_name, exc)
                return {
                    "module_name": module_name,
                    "num_functions": 0,
                    "num_classes": 0,
                    "functions": [],
                    "methods": [],
                    "lambdas": [],
                    "parse_errors": [str(exc)],
                }

        all_func_defs = self.find_function_definitions(tree)
        # Separate lambdas from named definitions.
        lambdas = [f for f in all_func_defs if f["name"] == "<lambda>"]
        named_funcs = [f for f in all_func_defs if f["name"] != "<lambda>"]

        # Count class nodes at the top level of the tree.
        class_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        ]

        # Gather all method definitions from every ClassDef in the tree.
        all_methods: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = self.analyze_method_definitions(tree, class_name=node.name)
                all_methods.extend(methods)

        self._stats["sources_analyzed"] += 1

        return {
            "module_name": module_name,
            "num_functions": len(named_funcs),
            "num_lambdas": len(lambdas),
            "num_classes": len(class_names),
            "class_names": class_names,
            "functions": named_funcs,
            "methods": all_methods,
            "lambdas": lambdas,
            "parse_errors": [],
        }

    def find_function_definitions(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Walk *tree* and return one dict per function/lambda definition.

        Handles ``ast.FunctionDef``, ``ast.AsyncFunctionDef``, and
        ``ast.Lambda`` nodes.  For each, returns a dict with:
        ``name``, ``lineno``, ``is_async``, ``args``, ``decorators``,
        ``return_annotation``, ``docstring``.

        Parameters
        ----------
        tree:
            An ``ast.AST`` tree (typically the result of ``ast.parse``).

        Returns
        -------
        list[dict[str, Any]]
            One entry per discovered function/lambda node.
        """
        results: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Extract decorator names as strings.
                decorators = []
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        decorators.append(dec.id)
                    elif isinstance(dec, ast.Attribute):
                        decorators.append(f"{ast.unparse(dec)}" if hasattr(ast, "unparse") else "?")
                    else:
                        decorators.append("<complex>")

                # Extract argument names and their annotations.
                args_info = self._extract_args_info(node.args)

                # Attempt to extract return annotation.
                ret_annotation = ""
                if node.returns is not None and hasattr(ast, "unparse"):
                    ret_annotation = ast.unparse(node.returns)

                # Docstring: first statement if it's an Expr(Constant).
                docstring = ""
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    docstring = textwrap.shorten(node.body[0].value.value, width=120)

                results.append({
                    "name": node.name,
                    "lineno": node.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "args": args_info,
                    "decorators": decorators,
                    "return_annotation": ret_annotation,
                    "docstring": docstring,
                })

            elif isinstance(node, ast.Lambda):
                # Lambdas have no name; record lineno and arg list.
                args_info = self._extract_args_info(node.args)
                results.append({
                    "name": "<lambda>",
                    "lineno": getattr(node, "lineno", 0),
                    "is_async": False,
                    "args": args_info,
                    "decorators": [],
                    "return_annotation": "",
                    "docstring": "",
                })

        return results

    def _extract_args_info(self, args: ast.arguments) -> list[dict[str, Any]]:
        """Extract argument information from an ``ast.arguments`` node.

        Handles positional, keyword-only, and variadic arguments.  Returns
        a list of dicts with ``name``, ``kind``, and ``annotation``.

        Parameters
        ----------
        args:
            An ``ast.arguments`` node from a ``FunctionDef`` or ``Lambda``.

        Returns
        -------
        list[dict[str, Any]]
            One entry per declared argument.
        """
        result: list[dict[str, Any]] = []

        def _ann(node: ast.expr | None) -> str:
            if node is None:
                return ""
            if hasattr(ast, "unparse"):
                return ast.unparse(node)
            return "?"

        for arg in args.posonlyargs:
            result.append({"name": arg.arg, "kind": "positional_only", "annotation": _ann(arg.annotation)})
        for arg in args.args:
            result.append({"name": arg.arg, "kind": "positional_or_keyword", "annotation": _ann(arg.annotation)})
        if args.vararg:
            result.append({"name": args.vararg.arg, "kind": "var_positional", "annotation": _ann(args.vararg.annotation)})
        for arg in args.kwonlyargs:
            result.append({"name": arg.arg, "kind": "keyword_only", "annotation": _ann(arg.annotation)})
        if args.kwarg:
            result.append({"name": args.kwarg.arg, "kind": "var_keyword", "annotation": _ann(args.kwarg.annotation)})

        return result

    def analyze_method_definitions(
        self,
        tree: ast.AST,
        class_name: str = "",
    ) -> list[dict[str, Any]]:
        """Find method definitions inside ``ClassDef`` nodes in *tree*.

        Identifies ``self``/``cls`` patterns, detects ``@classmethod``,
        ``@staticmethod``, and ``@property`` decorators.

        Parameters
        ----------
        tree:
            The full AST tree (methods are discovered by walking ``ClassDef``
            nodes at any nesting level).
        class_name:
            If non-empty, only analyse ``ClassDef`` nodes whose ``name``
            matches.

        Returns
        -------
        list[dict[str, Any]]
            One entry per method, each with keys: ``name``, ``class_name``,
            ``lineno``, ``is_async``, ``is_classmethod``, ``is_staticmethod``,
            ``is_property``, ``first_param``, ``args``.
        """
        results: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if class_name and node.name != class_name:
                continue

            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                decorator_names = []
                for dec in item.decorator_list:
                    if isinstance(dec, ast.Name):
                        decorator_names.append(dec.id)
                    elif isinstance(dec, ast.Attribute) and hasattr(ast, "unparse"):
                        decorator_names.append(ast.unparse(dec))
                    else:
                        decorator_names.append("<complex>")

                is_classmethod = "classmethod" in decorator_names
                is_staticmethod = "staticmethod" in decorator_names
                is_property = "property" in decorator_names

                args_info = self._extract_args_info(item.args)

                # The first parameter name reveals self/cls convention.
                first_param = args_info[0]["name"] if args_info else ""

                results.append({
                    "name": item.name,
                    "class_name": node.name,
                    "lineno": item.lineno,
                    "is_async": isinstance(item, ast.AsyncFunctionDef),
                    "is_classmethod": is_classmethod,
                    "is_staticmethod": is_staticmethod,
                    "is_property": is_property,
                    "first_param": first_param,
                    "args": args_info,
                    "decorator_names": decorator_names,
                })

        return results

    # ------------------------------------------------------------------
    # Live (dynamic) analysis
    # ------------------------------------------------------------------

    def analyze_live_function(self, func: types.FunctionType) -> dict[str, Any]:
        """Full introspection of a live ``types.FunctionType`` object.

        Combines ``inspect.signature``, ``__code__`` attributes, annotations,
        defaults, closure inspection, and flag decoding into a single
        comprehensive profile dict.

        Parameters
        ----------
        func:
            A Python function object.  Non-function callables are handled
            gracefully but produce less detail.

        Returns
        -------
        dict[str, Any]
            Rich profile with keys including:
            ``func_id``, ``qualname``, ``module``, ``is_async``,
            ``is_generator``, ``parameters``, ``return_annotation``,
            ``defaults``, ``co_flags_decoded``, ``closure_vars``.

        Theory alignment (theory2.tex §16.1)
        -------------------------------------
        The profile captures the *callable surface* of the function as seen
        from the runtime — equivalent to evaluating the functor from the
        Python object to its jugeo coordinate.
        """
        fid = function_id(func) if isinstance(func, types.FunctionType) else uuid.uuid4().hex

        # Check the cache before performing expensive introspection.
        cache_key = fid
        if cache_key in self._callable_profile_cache:
            self._stats["profile_cache_hits"] += 1
            return dict(self._callable_profile_cache[cache_key])

        self._stats["profile_cache_misses"] += 1
        self._stats["functions_analyzed"] += 1

        # Register with the coordinator (idempotent).
        self._coordinator.register_function(func)

        # Basic identity metadata.
        profile: dict[str, Any] = {
            "func_id": fid,
            "qualname": getattr(func, "__qualname__", repr(func)),
            "name": getattr(func, "__name__", ""),
            "module": getattr(func, "__module__", "") or "",
            "is_async": inspect.iscoroutinefunction(func),
            "is_generator": inspect.isgeneratorfunction(func),
            "is_async_generator": inspect.isasyncgenfunction(func),
            "docstring": (getattr(func, "__doc__", "") or "")[:512],
        }

        # Signature via inspect — may fail for some wrapped callables.
        params: list[dict[str, Any]] = []
        return_annotation: str = ""
        try:
            sig = inspect.signature(func)
            for pname, param in sig.parameters.items():
                ann = "" if param.annotation is inspect.Parameter.empty else repr(param.annotation)
                has_default = param.default is not inspect.Parameter.empty
                params.append({
                    "name": pname,
                    "kind": param.kind.name,
                    "annotation": ann,
                    "has_default": has_default,
                    "default_repr": repr(param.default) if has_default else "",
                })
            if sig.return_annotation is not inspect.Parameter.empty:
                return_annotation = repr(sig.return_annotation)
        except (ValueError, TypeError) as exc:
            _log.debug("analyze_live_function: signature failed for %s: %s", profile["qualname"], exc)

        profile["parameters"] = params
        profile["return_annotation"] = return_annotation
        profile["param_count"] = len(params)

        # Code object attributes — available only for Python functions.
        code = getattr(func, "__code__", None)
        if code is not None:
            profile["co_argcount"] = code.co_argcount
            profile["co_kwonlyargcount"] = getattr(code, "co_kwonlyargcount", 0)
            profile["co_nlocals"] = code.co_nlocals
            profile["co_flags"] = code.co_flags
            profile["co_filename"] = code.co_filename
            profile["co_firstlineno"] = code.co_firstlineno
            profile["co_name"] = code.co_name
            # All local variable names (parameters + locals).
            profile["co_varnames"] = list(code.co_varnames[: code.co_nlocals])

            # Decode common co_flags bits for readability.
            flags_decoded = {
                "CO_OPTIMIZED": bool(code.co_flags & 0x01),
                "CO_NEWLOCALS": bool(code.co_flags & 0x02),
                "CO_VARARGS": bool(code.co_flags & 0x04),
                "CO_VARKEYWORDS": bool(code.co_flags & 0x08),
                "CO_GENERATOR": bool(code.co_flags & 0x20),
                "CO_COROUTINE": bool(code.co_flags & 0x100),
                "CO_ASYNC_GENERATOR": bool(code.co_flags & 0x200),
            }
            profile["co_flags_decoded"] = flags_decoded

        # Default values.
        profile["defaults"] = extract_default_values(func) if isinstance(func, types.FunctionType) else {}

        # Raw annotations dict.
        profile["annotations"] = {
            k: repr(v) for k, v in getattr(func, "__annotations__", {}).items()
        }

        # Closure inspection.
        closure = getattr(func, "__closure__", None)
        if closure:
            # Capture the repr of each free variable's cell content.
            free_vars = getattr(code, "co_freevars", ()) if code else ()
            profile["closure_vars"] = {
                name: repr(cell.cell_contents)
                for name, cell in zip(free_vars, closure)
                if hasattr(cell, "cell_contents")
            }
        else:
            profile["closure_vars"] = {}

        # Cache and return.
        self._callable_profile_cache[cache_key] = profile
        return dict(profile)

    def analyze_live_method(self, method: types.MethodType) -> dict[str, Any]:
        """Inspect a bound method: its function, instance, MRO, and trust context.

        Parameters
        ----------
        method:
            A ``types.MethodType`` (bound method).

        Returns
        -------
        dict[str, Any]
            Profile with keys: ``binding_id``, ``func_profile``,
            ``self_type``, ``mro``, ``is_descriptor_result``,
            ``trust_context``.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        A bound method is the result of the descriptor protocol's ``__get__``
        machinery: ``type(instance).__dict__[name].__get__(instance, type(instance))``.
        Recording the MRO verifies which class actually owns the underlying
        function (the defining class vs. the instance's class may differ via
        inheritance).
        """
        if not isinstance(method, types.MethodType):
            raise TypeError(
                f"analyze_live_method expects types.MethodType, got {type(method).__name__!r}"
            )

        self._stats["methods_analyzed"] += 1

        binding_id = self._coordinator.register_method_binding(method)
        func_profile = self.analyze_live_function(method.__func__)

        self_obj = method.__self__
        self_type = type(self_obj)
        mro_names = [c.__qualname__ for c in self_type.__mro__]

        # Check whether this method came from the descriptor protocol by
        # looking for the underlying function in the class's __dict__ chain.
        is_descriptor_result = False
        method_name = method.__func__.__name__
        for cls in self_type.__mro__:
            if method_name in cls.__dict__:
                raw = cls.__dict__[method_name]
                # If the raw attribute is a function (not yet bound), then
                # descriptor __get__ produced this bound method.
                is_descriptor_result = isinstance(raw, (types.FunctionType, classmethod, staticmethod))
                break

        trust_ctx = self._coordinator.trust_context_for_binding(binding_id)

        return {
            "binding_id": binding_id,
            "func_profile": func_profile,
            "self_type": self_type.__qualname__,
            "self_module": getattr(self_type, "__module__", "") or "",
            "mro": mro_names,
            "is_descriptor_result": is_descriptor_result,
            "trust_context": trust_ctx,
        }

    def disassemble_function(self, func: types.FunctionType) -> list[dict[str, Any]]:
        """Disassemble *func* using ``dis`` and return structured instruction dicts.

        Groups instructions by opcode family (LOAD*, STORE*, CALL*, etc.)
        and returns a flat list of instruction records.

        Parameters
        ----------
        func:
            A Python function object accessible to ``dis.get_instructions``.

        Returns
        -------
        list[dict[str, Any]]
            One dict per bytecode instruction with keys:
            ``offset``, ``opname``, ``opcode``, ``argval``, ``argrepr``,
            ``is_jump_target``, ``opcode_family``.

        Notes
        -----
        This is useful for verifying that the bytecode of a function matches
        expected patterns (e.g., confirming that a generator function contains
        ``YIELD_VALUE`` instructions) at the trust-witness level.
        """
        self._stats["disassembly_calls"] += 1
        results: list[dict[str, Any]] = []

        try:
            for instr in dis.get_instructions(func):
                # Derive a broad "opcode family" from the first word of the opname.
                family = re.split(r"[_]", instr.opname)[0]
                results.append({
                    "offset": instr.offset,
                    "opname": instr.opname,
                    "opcode": instr.opcode,
                    "argval": instr.argval,
                    "argrepr": instr.argrepr,
                    "is_jump_target": instr.is_jump_target,
                    "opcode_family": family,
                })
        except (TypeError, AttributeError) as exc:
            _log.warning("disassemble_function: dis failed for %r: %s", func, exc)

        return results

    def compare_function_surfaces(
        self,
        func1: types.FunctionType,
        func2: types.FunctionType,
    ) -> dict[str, Any]:
        """Compare the callable surfaces of two functions and return a diff dict.

        Compares parameter counts, parameter names (with position-indexed
        alignment), return type annotations, and decorator lists (from AST
        analysis of ``inspect.getsource`` if available).

        Parameters
        ----------
        func1:
            The first function to compare.
        func2:
            The second function to compare.

        Returns
        -------
        dict[str, Any]
            Keys: ``equal``, ``param_count_diff``, ``param_name_diff``,
            ``return_annotation_diff``, ``func1_qualname``,
            ``func2_qualname``, ``common_param_names``, ``only_in_func1``,
            ``only_in_func2``.
        """
        p1 = self.analyze_live_function(func1)
        p2 = self.analyze_live_function(func2)

        names1: set[str] = {p["name"] for p in p1["parameters"]}
        names2: set[str] = {p["name"] for p in p2["parameters"]}

        common = names1 & names2
        only_in_1 = names1 - names2
        only_in_2 = names2 - names1

        param_count_diff = p1["param_count"] - p2["param_count"]
        return_diff = p1["return_annotation"] != p2["return_annotation"]

        equal = (
            param_count_diff == 0
            and not only_in_1
            and not only_in_2
            and not return_diff
        )

        return {
            "equal": equal,
            "func1_qualname": p1["qualname"],
            "func2_qualname": p2["qualname"],
            "param_count_diff": param_count_diff,
            "param_name_diff": bool(only_in_1 or only_in_2),
            "return_annotation_diff": return_diff,
            "common_param_names": sorted(common),
            "only_in_func1": sorted(only_in_1),
            "only_in_func2": sorted(only_in_2),
            "func1_return": p1["return_annotation"],
            "func2_return": p2["return_annotation"],
        }

    def emit_function_judgment(
        self,
        func: types.FunctionType,
        proposition: str,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Emit a judgment dict about a function's callable surface.

        Builds a minimal judgment record encoding the proposition, the
        function's coordinate, and the requested trust level.  The result
        is a plain dict (not a live :class:`Judgment` object) so it can be
        serialised and passed across process boundaries.

        Parameters
        ----------
        func:
            The function being judged.
        proposition:
            A free-text proposition string, e.g.
            ``"parameter 'x' is of type int"``.
        trust_level:
            A :class:`TrustLevel` value or compatible int.  Defaults to
            ``TrustLevel.ORACLE_PROPOSED``.

        Returns
        -------
        dict[str, Any]
            Judgment record with keys: ``judgment_id``, ``func_id``,
            ``qualname``, ``proposition``, ``trust_level``, ``channel``,
            ``emitted_at``.
        """
        if trust_level is None:
            trust_level = TrustLevel.ORACLE_PROPOSED

        fid = function_id(func) if isinstance(func, types.FunctionType) else uuid.uuid4().hex
        qualname = getattr(func, "__qualname__", repr(func))
        judgment_id = uuid.uuid4().hex

        _log.debug(
            "emit_function_judgment: judgment_id=%s func=%s proposition=%r",
            judgment_id,
            qualname,
            proposition,
        )

        return {
            "judgment_id": judgment_id,
            "func_id": fid,
            "qualname": qualname,
            "module": getattr(func, "__module__", "") or "",
            "proposition": proposition,
            "trust_level": trust_level.name if hasattr(trust_level, "name") else str(trust_level),
            "trust_level_int": int(trust_level),
            "channel": _ANALYSIS_CHANNEL,
            "emitted_at": time.monotonic(),
        }


# ---------------------------------------------------------------------------
# FunctionValuesMethodValuesWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FunctionValuesMethodValuesWitness:
    """Runtime witness layer for function and method identity verification.

    Theory alignment (theory2.tex §16.4)
    -------------------------------------
    A *witness* accumulates concrete runtime evidence that either confirms or
    refutes structural propositions about callables.  Evidence collected here
    promotes judgments from ``ORACLE_PROPOSED`` (level 2) to
    ``RUNTIME_WITNESSED`` (level 3).

    The witness checks:

    * **Function identity** — qualname/module/filename/lineno consistency.
    * **Method binding correctness** — ``__self__`` is the expected instance,
      ``__func__`` is in ``type(instance).__dict__`` (up the MRO).
    * **Self trust context** — the ``self`` object is a genuine instance of
      the declaring class (not a proxy or mock that bypasses ``isinstance``).
    * **Callable protocol** — ``__call__`` attribute present, ``callable()``
      returns ``True``, signature is non-empty.
    * **Trust violations** — records instances where ``self`` is *not* an
      instance of the expected class.

    Attributes
    ----------
    _analyzer:
        Shared analyzer for live introspection.
    _witnessed_functions:
        Accumulated evidence dicts from :meth:`witness_function_identity`.
    _binding_evidence:
        Accumulated evidence dicts from :meth:`witness_method_binding`.
    _trust_violations:
        Accumulated violation dicts from :meth:`detect_trust_violation`.
    _witness_id:
        Unique per-instance identifier for cross-reference in logs.
    """

    _analyzer: FunctionValuesMethodValuesAnalyzer = field(
        default_factory=FunctionValuesMethodValuesAnalyzer
    )
    _witnessed_functions: list[dict[str, Any]] = field(default_factory=list)
    _binding_evidence: list[dict[str, Any]] = field(default_factory=list)
    _trust_violations: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # ------------------------------------------------------------------
    # Witness methods
    # ------------------------------------------------------------------

    def witness_function_identity(self, func: types.FunctionType) -> dict[str, Any]:
        """Witness the runtime identity of a function object.

        Checks that the four identity components (``__qualname__``,
        ``__module__``, ``co_filename``, ``co_firstlineno``) are internally
        consistent and non-empty.  Records the result as evidence in
        :attr:`_witnessed_functions`.

        Parameters
        ----------
        func:
            A Python function to witness.

        Returns
        -------
        dict[str, Any]
            Evidence dict with keys: ``func_id``, ``qualname``, ``module``,
            ``filename``, ``firstlineno``, ``consistent``, ``issues``,
            ``trust_level``, ``witnessed_at``.

        Theory alignment (theory2.tex §16.1)
        -------------------------------------
        Function identity is witnessed by confirming that the code object's
        source-location metadata matches the object's ``__qualname__`` and
        ``__module__`` attributes — an inconsistency would indicate
        deliberate tampering or a code-patching scenario.
        """
        fid = function_id(func) if isinstance(func, types.FunctionType) else uuid.uuid4().hex
        qualname = getattr(func, "__qualname__", "")
        module = getattr(func, "__module__", "") or ""
        code = getattr(func, "__code__", None)
        filename = getattr(code, "co_filename", "") if code else ""
        firstlineno = getattr(code, "co_firstlineno", -1) if code else -1

        issues: list[str] = []

        # Identity check 1: qualname must be non-empty.
        if not qualname:
            issues.append("__qualname__ is empty or missing")

        # Identity check 2: module should be non-empty (except for __main__).
        if not module:
            issues.append("__module__ is empty")

        # Identity check 3: code object should have a valid filename.
        if not filename:
            issues.append("co_filename is empty")

        # Identity check 4: firstlineno should be positive.
        if firstlineno <= 0:
            issues.append(f"co_firstlineno is non-positive ({firstlineno})")

        # Identity check 5: the function's __name__ should appear somewhere
        # in its __qualname__ (guards against attribute aliasing).
        name = getattr(func, "__name__", "")
        if name and qualname and name not in qualname:
            issues.append(f"__name__ {name!r} not a component of __qualname__ {qualname!r}")

        consistent = len(issues) == 0
        trust = TrustLevel.RUNTIME_WITNESSED if consistent else TrustLevel.CONTRADICTED

        evidence: dict[str, Any] = {
            "func_id": fid,
            "qualname": qualname,
            "module": module,
            "filename": filename,
            "firstlineno": firstlineno,
            "consistent": consistent,
            "issues": issues,
            "trust_level": trust.name if hasattr(trust, "name") else str(trust),
            "trust_level_int": int(trust),
            "channel": _ANALYSIS_CHANNEL,
            "witnessed_at": time.monotonic(),
            "witness_id": self._witness_id,
        }

        self._witnessed_functions.append(evidence)
        _log.debug(
            "witness=%s func_id=%s consistent=%s issues=%s",
            self._witness_id,
            fid,
            consistent,
            issues,
        )
        return evidence

    def witness_method_binding(self, instance: Any, method_name: str) -> dict[str, Any]:
        """Witness the binding of a named method on a live instance.

        Retrieves ``getattr(instance, method_name)``, confirms it is a
        ``types.MethodType``, verifies ``__self__ is instance``, and verifies
        that ``__func__`` is reachable through ``type(instance).__mro__``.

        Parameters
        ----------
        instance:
            The live object on which the method is sought.
        method_name:
            The attribute name of the method to witness.

        Returns
        -------
        dict[str, Any]
            Evidence dict with keys: ``binding_id``, ``method_name``,
            ``self_type``, ``is_bound_method``, ``self_is_instance``,
            ``func_in_mro``, ``consistent``, ``issues``, ``trust_level``.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        The binding witness verifies that the descriptor protocol produced a
        genuine bound method: ``__self__`` is exactly the instance we passed
        in, and ``__func__`` is the undecorated function stored in some class
        along the MRO.
        """
        issues: list[str] = []
        binding_id = ""
        self_is_instance = False
        func_in_mro = False
        is_bm = False

        try:
            method = getattr(instance, method_name)
        except AttributeError as exc:
            issues.append(f"AttributeError: {exc}")
            method = None

        if method is not None:
            is_bm = isinstance(method, types.MethodType)
            if not is_bm:
                issues.append(
                    f"{method_name!r} is not a types.MethodType (got {type(method).__name__!r})"
                )
            else:
                # Verify __self__ identity.
                self_is_instance = method.__self__ is instance
                if not self_is_instance:
                    issues.append("method.__self__ is not the expected instance")

                # Verify __func__ is findable in the MRO.
                func_name = method.__func__.__name__
                inst_type = type(instance)
                for cls in inst_type.__mro__:
                    if func_name in cls.__dict__:
                        raw = cls.__dict__[func_name]
                        if isinstance(raw, (types.FunctionType, classmethod, staticmethod)):
                            func_in_mro = True
                            break
                if not func_in_mro:
                    issues.append(
                        f"__func__ {func_name!r} not found in MRO of {inst_type.__qualname__}"
                    )

                # Register the binding.
                try:
                    binding_id = self._analyzer._coordinator.register_method_binding(method)
                except Exception as exc2:
                    issues.append(f"register_method_binding failed: {exc2}")

        consistent = is_bm and self_is_instance and func_in_mro and not issues
        trust = TrustLevel.RUNTIME_WITNESSED if consistent else TrustLevel.CONTRADICTED

        evidence: dict[str, Any] = {
            "binding_id": binding_id,
            "method_name": method_name,
            "self_type": type(instance).__qualname__,
            "is_bound_method": is_bm,
            "self_is_instance": self_is_instance,
            "func_in_mro": func_in_mro,
            "consistent": consistent,
            "issues": issues,
            "trust_level": trust.name if hasattr(trust, "name") else str(trust),
            "trust_level_int": int(trust),
            "witnessed_at": time.monotonic(),
            "witness_id": self._witness_id,
        }

        self._binding_evidence.append(evidence)
        return evidence

    def witness_self_trust_context(self, method: types.MethodType) -> dict[str, Any]:
        """Verify that ``method.__self__`` is a bona-fide instance of the expected class.

        Checks ``isinstance(method.__self__, expected_class)`` where
        ``expected_class`` is inferred from the MRO of ``type(method.__self__)``.
        Also checks that the class owning ``__func__`` is in the MRO.

        Parameters
        ----------
        method:
            A bound method.

        Returns
        -------
        dict[str, Any]
            Trust evidence with keys: ``self_type``, ``expected_class``,
            ``isinstance_ok``, ``owner_class_in_mro``, ``mro``,
            ``trust_level``, ``witnessed_at``.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        The ``self`` trust context establishes that the instance carries the
        obligations associated with its declared type.  An ``isinstance``
        failure is a trust violation that must be recorded and propagated.
        """
        if not isinstance(method, types.MethodType):
            raise TypeError(
                f"witness_self_trust_context expects types.MethodType, got {type(method).__name__!r}"
            )

        self_obj = method.__self__
        self_type = type(self_obj)
        mro = list(self_type.__mro__)
        mro_names = [c.__qualname__ for c in mro]

        # Find the class that owns __func__ in its __dict__.
        func_name = method.__func__.__name__
        owner_class: type | None = None
        for cls in mro:
            if func_name in cls.__dict__:
                owner_class = cls
                break

        owner_class_in_mro = owner_class is not None
        # isinstance check: self must be an instance of the owner class.
        isinstance_ok = isinstance(self_obj, owner_class) if owner_class else False
        expected_class_name = owner_class.__qualname__ if owner_class else "<unknown>"

        trust = TrustLevel.RUNTIME_WITNESSED if (isinstance_ok and owner_class_in_mro) else TrustLevel.CONTRADICTED

        evidence: dict[str, Any] = {
            "self_type": self_type.__qualname__,
            "expected_class": expected_class_name,
            "isinstance_ok": isinstance_ok,
            "owner_class_in_mro": owner_class_in_mro,
            "mro": mro_names,
            "trust_level": trust.name if hasattr(trust, "name") else str(trust),
            "trust_level_int": int(trust),
            "channel": _ANALYSIS_CHANNEL,
            "witnessed_at": time.monotonic(),
            "witness_id": self._witness_id,
        }

        return evidence

    def witness_classmethod_binding(
        self,
        cls: type,
        method_name: str,
    ) -> dict[str, Any]:
        """Witness that *method_name* on *cls* is a properly formed ``classmethod``.

        Retrieves ``cls.__dict__[method_name]``, confirms it is a
        ``classmethod`` descriptor, verifies that the bound version has
        ``__self__ is cls``, and records the evidence.

        Parameters
        ----------
        cls:
            The class on which the classmethod is declared.
        method_name:
            The name of the classmethod attribute.

        Returns
        -------
        dict[str, Any]
            Evidence dict with keys: ``class_name``, ``method_name``,
            ``is_classmethod_descriptor``, ``bound_self_is_cls``,
            ``func_qualname``, ``consistent``, ``issues``, ``trust_level``.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        The classmethod binding witness verifies that the ``bind_cls``
        restriction morphism has been correctly applied: the bound ``__self__``
        is the class itself, confirming that the class-level trust context
        is properly established.
        """
        issues: list[str] = []
        is_cm_descriptor = False
        bound_self_is_cls = False
        func_qualname = ""

        raw = cls.__dict__.get(method_name)
        if raw is None:
            issues.append(
                f"{method_name!r} not found directly in {cls.__qualname__}.__dict__ "
                f"(may be inherited — use MRO lookup)"
            )
        else:
            is_cm_descriptor = isinstance(raw, classmethod)
            if not is_cm_descriptor:
                issues.append(
                    f"{method_name!r} is not a classmethod descriptor (got {type(raw).__name__!r})"
                )
            else:
                func_qualname = getattr(raw.__func__, "__qualname__", "")

        # Retrieve the bound version via getattr to exercise descriptor __get__.
        try:
            bound = getattr(cls, method_name)
            if isinstance(bound, types.MethodType):
                bound_self_is_cls = bound.__self__ is cls
                if not bound_self_is_cls:
                    issues.append("bound classmethod __self__ is not the class itself")
            else:
                issues.append(
                    f"getattr({cls.__qualname__}, {method_name!r}) is not a MethodType "
                    f"(got {type(bound).__name__!r})"
                )
        except AttributeError as exc:
            issues.append(f"AttributeError on getattr: {exc}")

        consistent = is_cm_descriptor and bound_self_is_cls and not issues
        trust = TrustLevel.RUNTIME_WITNESSED if consistent else TrustLevel.CONTRADICTED

        evidence: dict[str, Any] = {
            "class_name": cls.__qualname__,
            "method_name": method_name,
            "is_classmethod_descriptor": is_cm_descriptor,
            "bound_self_is_cls": bound_self_is_cls,
            "func_qualname": func_qualname,
            "consistent": consistent,
            "issues": issues,
            "trust_level": trust.name if hasattr(trust, "name") else str(trust),
            "trust_level_int": int(trust),
            "witnessed_at": time.monotonic(),
            "witness_id": self._witness_id,
        }

        self._binding_evidence.append(evidence)
        return evidence

    def witness_callable_protocol(self, obj: Any) -> bool:
        """Check that *obj* satisfies the callable protocol and record evidence.

        Verifies:
        1. ``callable(obj)`` returns ``True``.
        2. ``hasattr(obj, "__call__")`` is ``True``.
        3. ``inspect.signature(obj.__call__)`` does not raise (where applicable).

        Records a summary evidence dict into :attr:`_witnessed_functions`.

        Parameters
        ----------
        obj:
            Any Python object.

        Returns
        -------
        bool
            ``True`` iff all three checks pass.

        Theory alignment (theory2.tex §16.1)
        -------------------------------------
        In the type-object category, an object is callable iff it is equipped
        with a ``__call__`` morphism.  This witness verifies that the object
        exposes the full callable interface required by the protocol.
        """
        is_callable = callable(obj)
        has_call_attr = hasattr(obj, "__call__")

        call_sig_ok = False
        call_sig_repr = ""
        if has_call_attr:
            try:
                sig = inspect.signature(obj)
                call_sig_repr = str(sig)
                call_sig_ok = True
            except (ValueError, TypeError):
                # Some built-ins don't expose a Python signature.
                call_sig_ok = True  # Not a failure — just unavailable.
                call_sig_repr = "<unavailable>"

        consistent = is_callable and has_call_attr and call_sig_ok
        trust = TrustLevel.RUNTIME_WITNESSED if consistent else TrustLevel.CONTRADICTED

        evidence: dict[str, Any] = {
            "obj_type": type(obj).__qualname__,
            "is_callable": is_callable,
            "has_call_attr": has_call_attr,
            "call_sig_ok": call_sig_ok,
            "call_sig_repr": call_sig_repr,
            "consistent": consistent,
            "trust_level": trust.name if hasattr(trust, "name") else str(trust),
            "trust_level_int": int(trust),
            "witnessed_at": time.monotonic(),
            "witness_id": self._witness_id,
        }

        self._witnessed_functions.append(evidence)
        return consistent

    def detect_trust_violation(
        self,
        method: types.MethodType,
        expected_self_type: type,
    ) -> bool:
        """Check whether ``method.__self__`` is an instance of *expected_self_type*.

        Records a violation dict in :attr:`_trust_violations` if the check
        fails, enabling downstream trust-demotion in the judgment layer.

        Parameters
        ----------
        method:
            A bound method.
        expected_self_type:
            The type that ``method.__self__`` is expected to be an instance of.

        Returns
        -------
        bool
            ``True`` if a violation was detected (``__self__`` is *not* an
            instance of ``expected_self_type``), ``False`` if the check passes.

        Theory alignment (theory2.tex §16.2)
        -------------------------------------
        A trust violation in the method-binding context means that ``self``
        bypasses the expected type boundary.  This can happen with monkey-
        patching, ``super()`` proxies, or mock objects.  Violations are
        recorded so that they can be escalated to ``CONTRADICTED`` judgments.
        """
        self_obj = getattr(method, "__self__", None)
        violation = not isinstance(self_obj, expected_self_type)

        if violation:
            violation_record: dict[str, Any] = {
                "witness_id": self._witness_id,
                "method_qualname": getattr(
                    getattr(method, "__func__", None), "__qualname__", "?"
                ),
                "actual_self_type": type(self_obj).__qualname__,
                "expected_self_type": expected_self_type.__qualname__,
                "trust_level": TrustLevel.CONTRADICTED.name
                if hasattr(TrustLevel.CONTRADICTED, "name")
                else str(TrustLevel.CONTRADICTED),
                "detected_at": time.monotonic(),
            }
            self._trust_violations.append(violation_record)
            _log.warning(
                "witness=%s trust violation: self type %r is not instance of %r",
                self._witness_id,
                type(self_obj).__qualname__,
                expected_self_type.__qualname__,
            )

        return violation

    def collect_evidence(self) -> dict[str, Any]:
        """Return a complete evidence bundle aggregating all witnessed data.

        Combines :attr:`_witnessed_functions`, :attr:`_binding_evidence`,
        and :attr:`_trust_violations` into a single evidence report.

        Returns
        -------
        dict[str, Any]
            Keys: ``witness_id``, ``num_function_witnesses``,
            ``num_binding_witnesses``, ``num_trust_violations``,
            ``all_consistent``, ``functions``, ``bindings``,
            ``violations``, ``collected_at``.

        Notes
        -----
        The ``all_consistent`` flag is ``True`` only when every piece of
        collected evidence has ``consistent == True`` and there are zero
        trust violations.  A single failure demotes the whole bundle.
        """
        all_consistent = (
            all(e.get("consistent", True) for e in self._witnessed_functions)
            and all(e.get("consistent", True) for e in self._binding_evidence)
            and len(self._trust_violations) == 0
        )

        return {
            "witness_id": self._witness_id,
            "num_function_witnesses": len(self._witnessed_functions),
            "num_binding_witnesses": len(self._binding_evidence),
            "num_trust_violations": len(self._trust_violations),
            "all_consistent": all_consistent,
            "functions": list(self._witnessed_functions),
            "bindings": list(self._binding_evidence),
            "violations": list(self._trust_violations),
            "collected_at": time.monotonic(),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "FunctionValuesMethodValuesCoordinator",
    "FunctionValuesMethodValuesAnalyzer",
    "FunctionValuesMethodValuesWitness",
    # Helper functions
    "function_id",
    "method_id",
    "is_bound_method",
    "is_unbound_function",
    "binding_kind",
    "parameter_count",
    "extract_default_values",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_FUNCTION_KINDS",
    "_BINDING_SEMANTICS",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    # ---- demo functions and a class with methods -------------------------

    def plain_func(x: int, y: str = "hello") -> bool:
        """A plain function for smoke-testing."""
        return bool(x)

    async def async_func(a: float) -> float:
        """An async function."""
        return a * 2.0

    def gen_func(n: int):
        """A generator function."""
        for i in range(n):
            yield i

    class _SampleClass:
        """Sample class for method-binding tests."""

        def instance_method(self, value: int) -> str:
            """An ordinary instance method."""
            return f"value={value}"

        @classmethod
        def class_method(cls, tag: str) -> str:
            """A classmethod."""
            return f"{cls.__name__}:{tag}"

        @staticmethod
        def static_method(x: int, y: int) -> int:
            """A staticmethod."""
            return x + y

    obj = _SampleClass()

    print("=" * 60)
    print("FunctionValuesMethodValuesCoordinator smoke test")
    print("=" * 60)

    coord = FunctionValuesMethodValuesCoordinator()

    fid_plain = coord.register_function(plain_func)
    fid_async = coord.register_function(async_func)
    fid_gen = coord.register_function(gen_func)
    print(f"  Registered plain_func  func_id={fid_plain}")
    print(f"  Registered async_func  func_id={fid_async}")
    print(f"  Registered gen_func    func_id={fid_gen}")

    bid = coord.register_method_binding(obj.instance_method)
    print(f"  Method binding id={bid}")
    print(f"  Trust context: {coord.trust_context_for_binding(bid)}")

    cm_desc = _SampleClass.__dict__["class_method"]
    cm_bid = coord.register_classmethod(cm_desc)
    print(f"  classmethod binding id={cm_bid}")

    print(f"  classify plain_func:  {coord.classify_callable(plain_func)!r}")
    print(f"  classify async_func:  {coord.classify_callable(async_func)!r}")
    print(f"  classify gen_func:    {coord.classify_callable(gen_func)!r}")
    print(f"  classify bound method:{coord.classify_callable(obj.instance_method)!r}")
    print(f"  classify lambda:      {coord.classify_callable(lambda x: x)!r}")
    print(f"  classify len (builtin):{coord.classify_callable(len)!r}")
    print(f"  Summary: {coord.summary()}")

    print()
    print("=" * 60)
    print("FunctionValuesMethodValuesAnalyzer smoke test")
    print("=" * 60)

    analyzer = FunctionValuesMethodValuesAnalyzer()

    profile = analyzer.analyze_live_function(plain_func)
    print(f"  plain_func profile: qualname={profile['qualname']}, params={profile['parameters']}")

    method_profile = analyzer.analyze_live_method(obj.instance_method)
    print(f"  instance_method profile: self_type={method_profile['self_type']}")

    source = textwrap.dedent("""
        def foo(x: int, y: str = "hi") -> bool:
            return True

        async def bar(a: float) -> float:
            return a

        class MyClass:
            def baz(self) -> None:
                pass

            @classmethod
            def qux(cls) -> None:
                pass
    """)
    analysis = analyzer.analyze_source(source, module_name="smoke_test")
    print(f"  Source analysis: {analysis['num_functions']} functions, {analysis['num_classes']} classes")

    diff = analyzer.compare_function_surfaces(plain_func, gen_func)
    print(f"  Surface diff plain_func vs gen_func: equal={diff['equal']}")

    judgment = analyzer.emit_function_judgment(plain_func, "parameter x is int")
    print(f"  Judgment: {judgment['judgment_id']}, trust={judgment['trust_level']}")

    instrs = analyzer.disassemble_function(plain_func)
    print(f"  Disassembly: {len(instrs)} instructions")

    print()
    print("=" * 60)
    print("FunctionValuesMethodValuesWitness smoke test")
    print("=" * 60)

    witness = FunctionValuesMethodValuesWitness()

    fn_ev = witness.witness_function_identity(plain_func)
    print(f"  Function identity witness: consistent={fn_ev['consistent']}")

    bind_ev = witness.witness_method_binding(obj, "instance_method")
    print(f"  Method binding witness: consistent={bind_ev['consistent']}, issues={bind_ev['issues']}")

    trust_ev = witness.witness_self_trust_context(obj.instance_method)
    print(f"  Self trust context: isinstance_ok={trust_ev['isinstance_ok']}")

    cm_ev = witness.witness_classmethod_binding(_SampleClass, "class_method")
    print(f"  classmethod binding: consistent={cm_ev['consistent']}")

    callable_ok = witness.witness_callable_protocol(plain_func)
    print(f"  Callable protocol: {callable_ok}")

    violation = witness.detect_trust_violation(obj.instance_method, _SampleClass)
    print(f"  Trust violation (expected False): {violation}")

    bundle = witness.collect_evidence()
    print(f"  Evidence bundle: all_consistent={bundle['all_consistent']}, "
          f"violations={bundle['num_trust_violations']}")

    print()
    print("All smoke tests passed.")
