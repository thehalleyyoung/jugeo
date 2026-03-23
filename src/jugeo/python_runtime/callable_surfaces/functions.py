"""Section 1 of the callable_surfaces package: Functions as Morphisms in the type-object category.

References theory2.tex Ch16. Copilot integration via CopilotCallableAdvisor.

Functions are morphisms in the type-object category: f : A → B where A is the
parameter carrier and B is the return carrier. A function's "callable surface"
is the shape of this morphism — the domain (parameter types) and codomain
(return type). This module implements the machinery for extracting, representing,
caching, and validating these morphisms from live Python callables.

Architecture
------------
:class:`FunctionMorphismAnalyzer`
    Primary analysis engine. Introspects live callables, constructs their
    callable surfaces, and issues judgments about morphism validity.

:class:`SignatureExtractor`
    Dispatches signature extraction depending on whether the callable is a
    plain function, class, or built-in.

:class:`AnnotationResolver`
    Resolves raw Python annotations (including ``ForwardRef`` and generic
    aliases) into canonical string representations.

:class:`CallableSurfaceCache`
    LRU-bounded cache for :class:`~jugeo.python_runtime.callable_surfaces.models.CallableSurface`
    objects keyed by a stable hash of the callable.

Copilot note: CopilotCallableAdvisor may propose ParameterSpec annotations for
unknown callables; such proposals enter at ``TrustLevel.ORACLE_PROPOSED`` and
are held pending runtime witness evidence before being settled.
"""

from __future__ import annotations

import inspect
import logging
import time
import types
import typing
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo geometry imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject,
        CoordinateKind,
        CoordinateMorphism,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except Exception:
    import enum

    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind."""
        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind."""
        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub for CoordinateObject."""
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: Mapping[str, Any] = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        """Stub for CoordinateMorphism."""
        def __init__(self, source: str, target: str, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site:  # type: ignore[no-redef]
        """Stub for Site."""
        pass

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub for SiteBuilder."""
        pass

# ---------------------------------------------------------------------------
# Jugeo judgment imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
except Exception:
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
# Callable surfaces models imports with stubs
# ---------------------------------------------------------------------------

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
except Exception:
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
            """Serialize to a plain dict.

            Returns:
                Dict with all fields serialized.
            """
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
            """Parse from a plain dict.

            Parameters:
                data: Dict as produced by :meth:`serialize`.

            Returns:
                A new ParameterSpec.
            """
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
            """Serialize to a plain dict.

            Returns:
                Dict with all fields serialized.
            """
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
            """Parse from a plain dict.

            Parameters:
                data: Dict as produced by :meth:`serialize`.

            Returns:
                A new CallableSurface.
            """
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
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub for SignatureRecord."""
        surface: Any = None
        raw_annotations: Mapping[str, str] = field(default_factory=dict)
        forward_refs: tuple[str, ...] = ()
        is_complete: bool = True

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "surface": self.surface.serialize() if hasattr(self.surface, "serialize") else None,
                "raw_annotations": dict(self.raw_annotations),
                "forward_refs": list(self.forward_refs),
                "is_complete": self.is_complete,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "SignatureRecord":
            """Parse from dict."""
            surface_data = data.get("surface")
            surface = CallableSurface.parse(surface_data) if surface_data else None
            return cls(
                surface=surface,
                raw_annotations=data.get("raw_annotations", {}),
                forward_refs=tuple(data.get("forward_refs", [])),
                is_complete=bool(data.get("is_complete", True)),
            )

    # Remaining stubs used only via sibling imports in s02
    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub for BoundMethod."""
        surface: Any = None
        instance_type: str = ""
        binding_morphism: str = ""
        bound_at: float = 0.0
        metadata: Mapping[str, Any] = field(default_factory=dict)

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "surface": self.surface.serialize() if hasattr(self.surface, "serialize") else None,
                "instance_type": self.instance_type,
                "binding_morphism": self.binding_morphism,
                "bound_at": self.bound_at,
                "metadata": dict(self.metadata),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "BoundMethod":
            """Parse from dict."""
            surface_data = data.get("surface")
            surface = CallableSurface.parse(surface_data) if surface_data else None
            return cls(
                surface=surface,
                instance_type=data.get("instance_type", ""),
                binding_morphism=data.get("binding_morphism", ""),
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
    class DescriptorRecord:  # type: ignore[no-redef]
        """Stub for DescriptorRecord."""
        name: str = ""
        kind: Any = None
        owner_class: str = ""
        has_get: bool = False
        has_set: bool = False
        has_delete: bool = False

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "name": self.name,
                "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
                "owner_class": self.owner_class,
                "has_get": self.has_get,
                "has_set": self.has_set,
                "has_delete": self.has_delete,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "DescriptorRecord":
            """Parse from dict."""
            kind_val = data.get("kind", "non_data")
            try:
                kind = DescriptorKind(kind_val)
            except (ValueError, KeyError):
                kind = DescriptorKind.NON_DATA
            return cls(
                name=data.get("name", ""),
                kind=kind,
                owner_class=data.get("owner_class", ""),
                has_get=bool(data.get("has_get", False)),
                has_set=bool(data.get("has_set", False)),
                has_delete=bool(data.get("has_delete", False)),
            )

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub for MethodBinding."""
        surface: Any = None
        descriptor: Any = None
        bound_at: float = 0.0

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "surface": self.surface.serialize() if hasattr(self.surface, "serialize") else None,
                "descriptor": self.descriptor.serialize() if hasattr(self.descriptor, "serialize") else None,
                "bound_at": self.bound_at,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "MethodBinding":
            """Parse from dict."""
            return cls(bound_at=float(data.get("bound_at", 0.0)))

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub for ClassConstruction."""
        class_name: str = ""
        bases: tuple[str, ...] = ()
        constructed_at: float = 0.0

        def serialize(self) -> dict[str, Any]:
            """Serialize to dict."""
            return {
                "class_name": self.class_name,
                "bases": list(self.bases),
                "constructed_at": self.constructed_at,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> "ClassConstruction":
            """Parse from dict."""
            return cls(
                class_name=data.get("class_name", ""),
                bases=tuple(data.get("bases", [])),
                constructed_at=float(data.get("constructed_at", 0.0)),
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        ISO-8601 timestamp string, e.g. ``"2024-01-15T12:00:00.123456"``.
    """
    import datetime
    return datetime.datetime.utcnow().isoformat()


def _callable_key(func: Any) -> str:
    """Compute a stable string key for a callable.

    Uses the qualified name, module, and id to build an identifier
    that is stable within a single process run.

    Parameters:
        func: Any callable object.

    Returns:
        A string key suitable for use as a cache key.
    """
    qualname = getattr(func, "__qualname__", None) or getattr(func, "__name__", None) or ""
    module = getattr(func, "__module__", "") or ""
    # Include id so overwritten names at the same qualname stay distinct
    return f"{module}:{qualname}:{id(func)}"


def _inspect_kind_to_parameter_kind(kind: inspect.Parameter.kind) -> ParameterKind:  # type: ignore[name-defined]
    """Map an :class:`inspect.Parameter` kind constant to :class:`ParameterKind`.

    Parameters:
        kind: One of the ``inspect.Parameter.*`` kind constants.

    Returns:
        The corresponding :class:`ParameterKind` member.
    """
    mapping = {
        inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
        inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
        inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD,
    }
    return mapping.get(kind, ParameterKind.POSITIONAL_OR_KEYWORD)


# ---------------------------------------------------------------------------
# AnnotationResolver — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AnnotationResolver:
    """Resolves raw Python annotations to canonical string representations.

    Handles ``ForwardRef``, ``typing.Union``, ``X | Y`` union syntax,
    ``Optional[X]``, ``Generic[X, Y]``, and plain type objects.

    Parameters:
        module_globals: The ``__globals__`` dict from the owning module,
            used to resolve string forward references.
        strict: When ``True``, raise ``NameError`` on unresolvable forward
            references instead of returning the raw string.
    """

    module_globals: dict[str, Any]
    strict: bool

    def resolve(self, anno: Any) -> str:
        """Resolve an annotation to a canonical string.

        Parameters:
            anno: Any Python annotation — a type, string, ForwardRef, or
                ``typing`` generic alias.

        Returns:
            A string representation of the annotation, e.g. ``"int"``,
            ``"list[str]"``, ``"str | None"``.
        """
        if anno is inspect.Parameter.empty or anno is inspect.Signature.empty:
            return "Any"
        if anno is None:
            return "None"
        if anno is type(None):
            return "None"
        # String annotations (PEP 563 or manual forward refs)
        if isinstance(anno, str):
            return self.resolve_forward_ref(anno)
        # typing.ForwardRef
        if isinstance(anno, typing.ForwardRef):
            return self.resolve_forward_ref(anno.__forward_arg__)
        # Handle typing generics
        origin = getattr(anno, "__origin__", None)
        if origin is not None:
            # Union types: Optional[X], X | Y
            if origin is typing.Union:
                return self.handle_union(anno)
            # tuple
            if origin is tuple:
                return self.handle_tuple(anno)
            # Other generics: list, dict, set, frozenset, etc.
            return self.parse_generic(anno)
        # Plain type
        if isinstance(anno, type):
            mod = getattr(anno, "__module__", "")
            name = getattr(anno, "__qualname__", anno.__name__)
            if mod in ("builtins", ""):
                return name
            return f"{mod}.{name}"
        # Fallback: repr
        return repr(anno)

    def resolve_forward_ref(self, ref: str) -> str:
        """Attempt to resolve a forward-reference string.

        Parameters:
            ref: The forward-reference string, e.g. ``"MyClass"``.

        Returns:
            The resolved string representation if the name is found in
            ``module_globals``; otherwise returns ``ref`` unchanged (unless
            ``strict=True``, in which case raises ``NameError``).

        Raises:
            NameError: When ``strict=True`` and ``ref`` is not found.
        """
        ref = ref.strip()
        if ref in self.module_globals:
            resolved = self.module_globals[ref]
            if isinstance(resolved, type):
                return resolved.__qualname__
            return ref
        if self.strict:
            raise NameError(f"Cannot resolve forward reference {ref!r}")
        # Keep the raw string — may be resolved later
        return ref

    def parse_generic(self, tp: Any) -> str:
        """Build a string for a Generic type such as ``list[int]``.

        Parameters:
            tp: A ``typing`` generic alias with ``__origin__`` and
                ``__args__`` attributes.

        Returns:
            E.g. ``"list[int]"``, ``"dict[str, Any]"``.
        """
        origin = getattr(tp, "__origin__", None)
        args = getattr(tp, "__args__", None) or ()
        # Determine the base name
        if origin is None:
            return repr(tp)
        origin_name = getattr(origin, "__name__", None) or getattr(origin, "_name", None) or repr(origin)
        if not args:
            return origin_name
        arg_strs = ", ".join(self.resolve(a) for a in args)
        return f"{origin_name}[{arg_strs}]"

    def handle_union(self, tp: Any) -> str:
        """Format a ``Union`` or ``X | Y`` annotation as ``X | Y``.

        Parameters:
            tp: A ``typing.Union`` generic alias.

        Returns:
            E.g. ``"str | int | None"``.
        """
        args = getattr(tp, "__args__", None) or ()
        parts = [self.resolve(a) for a in args]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return " | ".join(unique)

    def handle_optional(self, tp: Any) -> str:
        """Handle ``Optional[X]`` — convert to ``X | None``.

        Parameters:
            tp: A ``typing.Optional`` generic alias (i.e. ``Union[X, None]``).

        Returns:
            E.g. ``"str | None"``.
        """
        args = getattr(tp, "__args__", None) or ()
        if not args:
            return "Any | None"
        inner = self.resolve(args[0])
        return f"{inner} | None"

    def handle_tuple(self, tp: Any) -> str:
        """Format a ``Tuple`` annotation.

        Parameters:
            tp: A ``typing.Tuple`` or ``tuple[...]`` alias.

        Returns:
            E.g. ``"tuple[int, str]"``, ``"tuple[str, ...]"``.
        """
        args = getattr(tp, "__args__", None) or ()
        if not args:
            return "tuple[Any, ...]"
        # tuple[()] is the empty tuple
        if len(args) == 1 and args[0] is type(None):
            return "tuple[()]"
        arg_strs = ", ".join(self.resolve(a) for a in args)
        return f"tuple[{arg_strs}]"

    def is_resolved(self, anno: str) -> bool:
        """Check whether a string annotation appears fully resolved.

        A string is considered resolved if it contains no quotes and no
        leading/trailing whitespace, and if all identifiers it references
        exist in module_globals or are builtins.

        Parameters:
            anno: The annotation string to check.

        Returns:
            ``True`` if the annotation looks fully resolved.
        """
        anno = anno.strip()
        if not anno:
            return False
        # Contains quoted strings → still a forward ref
        if '"' in anno or "'" in anno:
            return False
        # Simple single identifier
        if anno.isidentifier():
            import builtins
            if hasattr(builtins, anno):
                return True
            return anno in self.module_globals
        # Composite (e.g. "str | None") — check all parts
        for part in anno.replace("|", " ").replace("[", " ").replace("]", " ").replace(",", " ").split():
            part = part.strip()
            if not part or part == "...":
                continue
            if part.isidentifier():
                import builtins
                if not hasattr(builtins, part) and part not in self.module_globals and part != "Any":
                    return False
        return True


# ---------------------------------------------------------------------------
# SignatureExtractor — mutable dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SignatureExtractor:
    """Dispatches signature extraction based on the kind of callable.

    Handles plain functions, classes (via ``__init__``), and built-in
    functions (graceful degradation). Normalises annotations via
    :class:`AnnotationResolver`.

    Parameters:
        _resolved: Cache of previously resolved annotation strings.
        _module_globals: Globals dict of the module under inspection.
    """

    _resolved: dict[str, Any]
    _module_globals: dict[str, Any]

    def extract(self, func: Any) -> CallableSurface:
        """Dispatch to the appropriate extraction method.

        Parameters:
            func: Any callable — function, method, class, or built-in.

        Returns:
            A :class:`CallableSurface` representing the callable's signature.
        """
        if inspect.isbuiltin(func) or isinstance(func, types.BuiltinFunctionType):
            return self.extract_from_builtin(func)
        if inspect.isclass(func):
            return self.extract_from_class(func)
        # Bound methods, plain functions, lambdas, callable objects
        underlying = func
        if inspect.ismethod(func):
            underlying = func.__func__
        return self.extract_from_function(underlying)

    def extract_from_function(self, func: Any) -> CallableSurface:
        """Extract a :class:`CallableSurface` from a plain Python function.

        Uses :func:`inspect.signature` to obtain the parameter list and
        return annotation, then normalises each annotation via
        :class:`AnnotationResolver`.

        Parameters:
            func: A Python function, coroutine function, or generator.

        Returns:
            A fully populated :class:`CallableSurface`.
        """
        name = getattr(func, "__name__", "<unknown>")
        qualname = getattr(func, "__qualname__", name)
        module = getattr(func, "__module__", "") or ""
        doc = inspect.getdoc(func) or ""
        is_async = inspect.iscoroutinefunction(func)
        is_gen = inspect.isgeneratorfunction(func)

        # Update module globals for annotation resolution
        func_globals: dict[str, Any] = getattr(func, "__globals__", {}) or {}
        self._module_globals.update(func_globals)

        resolver = AnnotationResolver(module_globals=self._module_globals, strict=False)

        try:
            sig = inspect.signature(func, follow_wrapped=True)
        except (ValueError, TypeError) as exc:
            logger.debug("signature() failed for %r: %s", func, exc)
            return CallableSurface(
                name=name,
                qualname=qualname,
                module=module,
                parameters=(),
                return_annotation="Any",
                is_async=is_async,
                is_generator=is_gen,
                docstring=doc,
                surface_id=uuid.uuid4().hex,
                created_at=time.time(),
            )

        raw_params = list(sig.parameters.values())
        param_specs = self._convert_parameters(raw_params, resolver)

        ret_anno = resolver.resolve(sig.return_annotation)

        return CallableSurface(
            name=name,
            qualname=qualname,
            module=module,
            parameters=tuple(param_specs),
            return_annotation=ret_anno,
            is_async=is_async,
            is_generator=is_gen,
            docstring=doc,
            surface_id=uuid.uuid4().hex,
            created_at=time.time(),
        )

    def extract_from_class(self, cls: type) -> CallableSurface:
        """Extract a :class:`CallableSurface` from a class constructor.

        Looks at ``cls.__init__`` and strips the leading ``self`` parameter
        so the surface represents the user-visible construction interface.

        Parameters:
            cls: A Python class.

        Returns:
            A :class:`CallableSurface` representing ``cls.__init__``
            minus the ``self`` parameter.
        """
        name = getattr(cls, "__name__", "<unknown>")
        qualname = getattr(cls, "__qualname__", name)
        module = getattr(cls, "__module__", "") or ""
        doc = inspect.getdoc(cls) or inspect.getdoc(cls.__init__) or ""

        resolver = AnnotationResolver(module_globals=self._module_globals, strict=False)

        init_fn = cls.__init__
        try:
            sig = inspect.signature(init_fn, follow_wrapped=True)
        except (ValueError, TypeError) as exc:
            logger.debug("signature() on __init__ of %r failed: %s", cls, exc)
            return CallableSurface(
                name=name,
                qualname=qualname,
                module=module,
                parameters=(),
                return_annotation=qualname,
                is_async=False,
                is_generator=False,
                docstring=doc,
                surface_id=uuid.uuid4().hex,
                created_at=time.time(),
            )

        raw_params = [
            p for pname, p in sig.parameters.items() if pname != "self"
        ]
        param_specs = self._convert_parameters(raw_params, resolver)

        return CallableSurface(
            name=name,
            qualname=qualname,
            module=module,
            parameters=tuple(param_specs),
            return_annotation=qualname,
            is_async=False,
            is_generator=False,
            docstring=doc,
            surface_id=uuid.uuid4().hex,
            created_at=time.time(),
        )

    def extract_from_builtin(self, func: Any) -> CallableSurface:
        """Handle built-in functions gracefully with limited introspection.

        Built-ins (e.g. ``len``, ``print``) often lack accessible signatures.
        This method attempts :func:`inspect.signature` and falls back to an
        empty parameter list with ``Any`` return type.

        Parameters:
            func: A built-in function or method.

        Returns:
            A :class:`CallableSurface` with as much detail as available.
        """
        name = getattr(func, "__name__", "<builtin>")
        qualname = getattr(func, "__qualname__", name)
        module = getattr(func, "__module__", "builtins") or "builtins"
        doc = inspect.getdoc(func) or ""

        try:
            sig = inspect.signature(func)
            resolver = AnnotationResolver(module_globals=self._module_globals, strict=False)
            raw_params = list(sig.parameters.values())
            param_specs = self._convert_parameters(raw_params, resolver)
            ret_anno = resolver.resolve(sig.return_annotation)
        except (ValueError, TypeError):
            param_specs = []
            ret_anno = "Any"

        return CallableSurface(
            name=name,
            qualname=qualname,
            module=module,
            parameters=tuple(param_specs),
            return_annotation=ret_anno,
            is_async=False,
            is_generator=False,
            docstring=doc,
            surface_id=uuid.uuid4().hex,
            created_at=time.time(),
        )

    def normalize_annotations(self, anno: Any) -> str:
        """Convert any annotation object to a canonical string.

        Parameters:
            anno: Any Python annotation — type, string, ForwardRef, or generic.

        Returns:
            A canonical string representation.
        """
        resolver = AnnotationResolver(module_globals=self._module_globals, strict=False)
        result = resolver.resolve(anno)
        # Memoize in the _resolved cache so callers can reuse
        key = repr(anno)
        self._resolved[key] = result
        return result

    def resolve_defaults(self, param: inspect.Parameter) -> tuple[bool, str]:
        """Check whether a parameter has a default and return its repr.

        Parameters:
            param: An :class:`inspect.Parameter` object.

        Returns:
            A ``(has_default, default_repr)`` tuple where ``has_default``
            is ``True`` iff a default value is present, and ``default_repr``
            is the string representation of that default (empty string if
            none).
        """
        if param.default is inspect.Parameter.empty:
            return False, ""
        default = param.default
        # For simple literals, use repr; for complex objects, use type name
        try:
            r = repr(default)
            # Truncate very long reprs
            if len(r) > 120:
                r = r[:117] + "..."
        except Exception:
            r = f"<{type(default).__name__}>"
        return True, r

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _convert_parameters(
        self,
        raw_params: list[inspect.Parameter],
        resolver: AnnotationResolver,
    ) -> list[ParameterSpec]:
        """Convert a list of :class:`inspect.Parameter` objects to :class:`ParameterSpec` objects.

        Parameters:
            raw_params: List of parameters from :func:`inspect.signature`.
            resolver: The annotation resolver to use.

        Returns:
            List of :class:`ParameterSpec` instances.
        """
        result: list[ParameterSpec] = []
        for param in raw_params:
            anno_str = resolver.resolve(param.annotation)
            has_default, default_repr = self.resolve_defaults(param)
            is_variadic = param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
            pk = _inspect_kind_to_parameter_kind(param.kind)
            spec = ParameterSpec(
                name=param.name,
                kind=pk,
                annotation=anno_str,
                has_default=has_default,
                default_repr=default_repr,
                is_variadic=is_variadic,
            )
            result.append(spec)
        return result


# ---------------------------------------------------------------------------
# CallableSurfaceCache — mutable dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CallableSurfaceCache:
    """LRU-bounded cache for :class:`CallableSurface` objects.

    Tracks per-key access counts to support LRU eviction when the cache
    reaches capacity. Hit/miss counters enable monitoring of cache effectiveness.

    Parameters:
        _cache: Internal dict mapping cache key → CallableSurface.
        _access_count: Number of times each key was accessed.
        _hit_count: Total number of cache hits.
        _miss_count: Total number of cache misses.
        capacity: Maximum number of entries before LRU eviction triggers.
    """

    _cache: dict[str, CallableSurface]
    _access_count: dict[str, int]
    _hit_count: int
    _miss_count: int
    capacity: int

    def get(self, key: str) -> CallableSurface | None:
        """Retrieve an entry from the cache.

        Parameters:
            key: Cache key string.

        Returns:
            The cached :class:`CallableSurface` or ``None`` on a miss.
        """
        if key in self._cache:
            self._access_count[key] = self._access_count.get(key, 0) + 1
            self._hit_count += 1
            return self._cache[key]
        self._miss_count += 1
        return None

    def put(self, key: str, surface: CallableSurface) -> None:
        """Insert or update an entry in the cache.

        If adding a new entry would exceed ``capacity``, the LRU entry is
        evicted first.

        Parameters:
            key: Cache key string.
            surface: The :class:`CallableSurface` to cache.
        """
        if key not in self._cache and len(self._cache) >= self.capacity:
            evicted = self.evict_lru()
            if evicted:
                logger.debug("CallableSurfaceCache evicted key %r (capacity=%d)", evicted, self.capacity)
        self._cache[key] = surface
        # Reset access count for new/updated entries
        self._access_count[key] = self._access_count.get(key, 0)

    def invalidate(self, key: str) -> bool:
        """Remove a single entry from the cache.

        Parameters:
            key: Cache key string.

        Returns:
            ``True`` if the key was present and removed; ``False`` otherwise.
        """
        if key in self._cache:
            del self._cache[key]
            self._access_count.pop(key, None)
            return True
        return False

    def size(self) -> int:
        """Return the number of entries currently in the cache.

        Returns:
            Number of cached surfaces.
        """
        return len(self._cache)

    def hit_rate(self) -> float:
        """Compute the cache hit rate as a fraction in [0.0, 1.0].

        Returns:
            Hit count / total lookups, or ``0.0`` if no lookups have
            occurred.
        """
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.0
        return self._hit_count / total

    def evict_lru(self) -> str | None:
        """Evict the least recently used entry from the cache.

        Selects the entry with the lowest access count, breaking ties by
        insertion order (the first encountered key wins).

        Returns:
            The evicted key, or ``None`` if the cache is empty.
        """
        if not self._cache:
            return None
        # Find the key with the lowest access count
        lru_key = min(
            self._cache.keys(),
            key=lambda k: self._access_count.get(k, 0),
        )
        del self._cache[lru_key]
        self._access_count.pop(lru_key, None)
        return lru_key


# ---------------------------------------------------------------------------
# FunctionMorphismAnalyzer — mutable dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FunctionMorphismAnalyzer:
    """Analyzes Python callables as morphisms in the type-object category.

    Each function ``f : A → B`` is treated as a morphism in the category
    whose objects are type-coordinates (carriers). This analyzer extracts the
    domain ``A`` (parameter tuple type) and codomain ``B`` (return type),
    constructs :class:`CallableSurface` records, and builds :class:`Judgment`
    objects attesting to the surface's validity.

    Parameters:
        _cache: Dict from callable key → cached :class:`CallableSurface`.
        _site: Optional :class:`Site` to attach coordinate records to.
        _errors: Accumulated error strings from previous analyses.
    """

    _cache: dict[str, CallableSurface]
    _site: Site | None
    _errors: list[str]

    def analyze(self, func: Any) -> CallableSurface:
        """Main entry point: analyze a callable and return its surface.

        Checks the internal cache first. On a miss, extracts the surface,
        validates it, and caches the result.

        Parameters:
            func: Any Python callable.

        Returns:
            The :class:`CallableSurface` for ``func``.
        """
        key = _callable_key(func)
        if key in self._cache:
            logger.debug("FunctionMorphismAnalyzer cache hit for %r", key)
            return self._cache[key]
        logger.debug("FunctionMorphismAnalyzer extracting surface for %r", func)
        surface = self.extract_surface(func)
        errors = self.validate_morphism(surface)
        if errors:
            self._errors.extend(errors)
            logger.warning("Morphism validation errors for %r: %s", key, errors)
        self._cache[key] = surface
        return surface

    def extract_surface(self, func: Any) -> CallableSurface:
        """Extract a :class:`CallableSurface` from a callable.

        Delegates to :class:`SignatureExtractor` for the actual introspection.

        Parameters:
            func: Any Python callable.

        Returns:
            A populated :class:`CallableSurface`.
        """
        module_globals: dict[str, Any] = getattr(func, "__globals__", {}) or {}
        extractor = SignatureExtractor(_resolved={}, _module_globals=dict(module_globals))
        return extractor.extract(func)

    def compute_parameter_carriers(self, params: list[inspect.Parameter]) -> list[ParameterSpec]:
        """Convert a list of :class:`inspect.Parameter` to :class:`ParameterSpec`.

        Parameters:
            params: Parameter objects from :func:`inspect.signature`.

        Returns:
            Ordered list of :class:`ParameterSpec` instances.
        """
        resolver = AnnotationResolver(module_globals={}, strict=False)
        extractor = SignatureExtractor(_resolved={}, _module_globals={})
        return extractor._convert_parameters(params, resolver)

    def compute_return_carrier(self, func: Any) -> str:
        """Get the return annotation as a canonical string.

        Parameters:
            func: A Python callable.

        Returns:
            String representation of the return annotation, e.g. ``"int"``
            or ``"str | None"``. Returns ``"Any"`` if unavailable.
        """
        try:
            sig = inspect.signature(func, follow_wrapped=True)
        except (ValueError, TypeError):
            return "Any"
        module_globals: dict[str, Any] = getattr(func, "__globals__", {}) or {}
        resolver = AnnotationResolver(module_globals=dict(module_globals), strict=False)
        return resolver.resolve(sig.return_annotation)

    def find_domain_coordinate(self, func: Any) -> CoordinateObject:
        """Build a coordinate object representing the function's domain.

        The domain coordinate captures the parameter types as components,
        making it addressable in the site topology.

        Parameters:
            func: A Python callable.

        Returns:
            A :class:`CoordinateObject` for the domain of ``func``.
        """
        surface = self.extract_surface(func)
        # Encode parameter types as coordinate components
        param_components = tuple(
            f"{p.name}:{p.annotation}" for p in surface.parameters
        )
        module_component = surface.module or "unknown"
        name_component = surface.qualname or surface.name
        components = (module_component, name_component, "domain") + param_components
        try:
            coord = CoordinateObject(
                components=components,
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({surface.name, surface.module}),
                metadata={"role": "domain", "arity": len(surface.parameters)},
            )
        except Exception:
            coord = CoordinateObject()  # type: ignore[call-arg]
        return coord

    def find_codomain_coordinate(self, func: Any) -> CoordinateObject:
        """Build a coordinate object representing the function's codomain.

        Parameters:
            func: A Python callable.

        Returns:
            A :class:`CoordinateObject` for the codomain of ``func``.
        """
        surface = self.extract_surface(func)
        module_component = surface.module or "unknown"
        name_component = surface.qualname or surface.name
        return_component = f"return:{surface.return_annotation}"
        components = (module_component, name_component, "codomain", return_component)
        try:
            coord = CoordinateObject(
                components=components,
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({surface.name, surface.return_annotation}),
                metadata={"role": "codomain", "return_type": surface.return_annotation},
            )
        except Exception:
            coord = CoordinateObject()  # type: ignore[call-arg]
        return coord

    def build_morphism_record(self, func: Any) -> dict[str, Any]:
        """Build a dict describing the function as a morphism ``f : A → B``.

        Includes the surface, domain/codomain coordinate keys, arity, and
        whether the function is a coroutine or generator.

        Parameters:
            func: A Python callable.

        Returns:
            A dict with keys ``surface``, ``domain``, ``codomain``, ``arity``,
            ``is_async``, ``is_generator``, ``qualname``, and ``module``.
        """
        surface = self.extract_surface(func)
        domain_coord = self.find_domain_coordinate(func)
        codomain_coord = self.find_codomain_coordinate(func)
        domain_key = ":".join(getattr(domain_coord, "components", ()))
        codomain_key = ":".join(getattr(codomain_coord, "components", ()))
        return {
            "surface": surface.serialize(),
            "domain": domain_key,
            "codomain": codomain_key,
            "arity": len(surface.parameters),
            "is_async": surface.is_async,
            "is_generator": surface.is_generator,
            "qualname": surface.qualname,
            "module": surface.module,
        }

    def validate_morphism(self, surface: CallableSurface) -> list[str]:
        """Validate a :class:`CallableSurface` for morphism-theoretic consistency.

        Checks that:
        * The surface name is non-empty.
        * All parameter names are valid Python identifiers.
        * No two parameters share a name.
        * The return annotation is a non-empty string.
        * VAR_POSITIONAL and VAR_KEYWORD parameters appear at most once each.

        Parameters:
            surface: The surface to validate.

        Returns:
            A possibly-empty list of error message strings.
        """
        errors: list[str] = []
        if not surface.name:
            errors.append("CallableSurface has empty name")
        ret = surface.return_annotation
        if not ret or ret.strip() == "":
            errors.append(f"Surface {surface.name!r}: empty return_annotation")

        seen_names: set[str] = set()
        var_positional_count = 0
        var_keyword_count = 0
        for spec in surface.parameters:
            if not spec.name.isidentifier():
                errors.append(
                    f"Surface {surface.name!r}: parameter name {spec.name!r} is not a valid identifier"
                )
            if spec.name in seen_names:
                errors.append(
                    f"Surface {surface.name!r}: duplicate parameter name {spec.name!r}"
                )
            seen_names.add(spec.name)
            if spec.kind == ParameterKind.VAR_POSITIONAL:
                var_positional_count += 1
            elif spec.kind == ParameterKind.VAR_KEYWORD:
                var_keyword_count += 1

        if var_positional_count > 1:
            errors.append(f"Surface {surface.name!r}: more than one *args parameter")
        if var_keyword_count > 1:
            errors.append(f"Surface {surface.name!r}: more than one **kwargs parameter")
        return errors

    def build_judgment(self, surface: CallableSurface) -> Judgment:
        """Build a :class:`Judgment` asserting that a callable surface is well-formed.

        The judgment expresses the proposition "function ``name`` is a valid
        morphism from its domain carrier to its codomain carrier" at
        ``RUNTIME_WITNESSED`` trust.

        Parameters:
            surface: The surface to issue a judgment about.

        Returns:
            A :class:`Judgment` with :attr:`JudgmentStatus.PROPOSED` status.
        """
        errors = self.validate_morphism(surface)
        formula = (
            f"callable_surface_valid({surface.name!r}, "
            f"arity={len(surface.parameters)}, "
            f"return={surface.return_annotation!r})"
        )
        prop_kind = PropositionKind.STRUCTURAL
        prop = Proposition(
            kind=prop_kind,
            formula=formula,
            free_variables=tuple(p.name for p in surface.parameters),
            metadata={"surface_id": surface.surface_id},
        )
        carrier = Carrier(
            name=surface.qualname or surface.name,
            parameters=tuple(p.name for p in surface.parameters),
            is_dependent=any(p.annotation != "Any" for p in surface.parameters),
            metadata={"module": surface.module},
        )
        trust_level = TrustLevel.UNVERIFIED if errors else TrustLevel.RUNTIME_WITNESSED
        now = _now_iso()
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "surface_id": surface.surface_id,
                "name": surface.name,
                "arity": len(surface.parameters),
            },
            trust_level=trust_level,
            channel="callable_surface_analyzer",
            timestamp=now,
            expiry="",
            provenance=(surface.surface_id,),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"analyzer": "FunctionMorphismAnalyzer"},
        )
        trust_ann = TrustAnnotation(
            level=trust_level,
            evidence_basis=(surface.surface_id,),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=tuple(errors) if errors else ("morphism extracted by runtime introspection",),
        )
        domain_coord = self.find_domain_coordinate
        # Build a minimal coordinate object for the judgment
        components = (surface.module or "unknown", surface.qualname or surface.name)
        try:
            coord = CoordinateObject(
                components=components,
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({surface.name}),
                metadata={"surface_id": surface.surface_id},
            )
        except Exception:
            coord = CoordinateObject()  # type: ignore[call-arg]

        status = JudgmentStatus.PROPOSED if errors else JudgmentStatus.SETTLED
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust_ann,
            provenance=prov,
            clauses=(),
            status=status,
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def surface_from_callable(func: Any) -> CallableSurface:
    """Convenience wrapper: extract a :class:`CallableSurface` from any callable.

    Creates a fresh :class:`FunctionMorphismAnalyzer` and returns its analysis
    of ``func``.

    Parameters:
        func: Any Python callable.

    Returns:
        A :class:`CallableSurface` describing the callable's signature.
    """
    analyzer = FunctionMorphismAnalyzer(_cache={}, _site=None, _errors=[])
    return analyzer.analyze(func)


def surfaces_are_compatible(a: CallableSurface, b: CallableSurface) -> bool:
    """Check whether two callable surfaces are compatible morphisms.

    Two surfaces are compatible if:
    * They have the same arity (number of parameters).
    * Corresponding parameters have the same ``kind``.
    * Return annotations are equal, or at least one is ``"Any"``.

    Parameters:
        a: First :class:`CallableSurface`.
        b: Second :class:`CallableSurface`.

    Returns:
        ``True`` if the surfaces are compatible.
    """
    if len(a.parameters) != len(b.parameters):
        return False
    for pa, pb in zip(a.parameters, b.parameters):
        if pa.kind != pb.kind:
            return False
    # Return annotation compatibility: "Any" is compatible with anything
    ret_a = a.return_annotation
    ret_b = b.return_annotation
    if ret_a != ret_b and ret_a != "Any" and ret_b != "Any":
        return False
    return True


def merge_surfaces(surfaces: list[CallableSurface]) -> CallableSurface | None:
    """Merge a list of callable surfaces into a common surface.

    Produces a surface where each parameter's annotation is the union of
    all per-surface annotations at that position. The merged surface name
    is the common prefix of all input names, falling back to
    ``"<merged>"``.

    Requires all surfaces to have the same arity. Returns ``None`` on
    incompatible input.

    Parameters:
        surfaces: List of :class:`CallableSurface` objects to merge.

    Returns:
        A merged :class:`CallableSurface`, or ``None`` if the surfaces are
        incompatible (differing arities or parameter kinds).
    """
    if not surfaces:
        return None
    if len(surfaces) == 1:
        return surfaces[0]

    arity = len(surfaces[0].parameters)
    for s in surfaces[1:]:
        if len(s.parameters) != arity:
            logger.warning(
                "merge_surfaces: arity mismatch (%d vs %d)", arity, len(s.parameters)
            )
            return None

    # Check kind compatibility across all surfaces
    for pos in range(arity):
        kinds = {s.parameters[pos].kind for s in surfaces}
        if len(kinds) > 1:
            logger.warning("merge_surfaces: incompatible parameter kinds at position %d", pos)
            return None

    # Build merged parameters
    merged_params: list[ParameterSpec] = []
    for pos in range(arity):
        param_at_pos = [s.parameters[pos] for s in surfaces]
        # Collect distinct annotations
        annotations = list(dict.fromkeys(p.annotation for p in param_at_pos))
        if len(annotations) == 1:
            merged_anno = annotations[0]
        else:
            # Use the first non-Any annotation, or union them all
            non_any = [a for a in annotations if a != "Any"]
            merged_anno = " | ".join(non_any) if non_any else "Any"
        # Use the name from the first surface
        base_param = param_at_pos[0]
        has_default = all(p.has_default for p in param_at_pos)
        merged_params.append(
            ParameterSpec(
                name=base_param.name,
                kind=base_param.kind,
                annotation=merged_anno,
                has_default=has_default,
                default_repr=base_param.default_repr if has_default else "",
                is_variadic=base_param.is_variadic,
            )
        )

    # Merge return annotation
    ret_annotations = list(dict.fromkeys(s.return_annotation for s in surfaces))
    non_any_ret = [r for r in ret_annotations if r != "Any"]
    merged_return = " | ".join(non_any_ret) if non_any_ret else "Any"

    # Compute a common name prefix
    names = [s.name for s in surfaces]
    common_name: str
    if all(n == names[0] for n in names):
        common_name = names[0]
    else:
        # Find longest common prefix of the name list
        prefix_chars: list[str] = []
        for chars in zip(*names):
            if len(set(chars)) == 1:
                prefix_chars.append(chars[0])
            else:
                break
        common_name = "".join(prefix_chars) or "<merged>"

    is_async = any(s.is_async for s in surfaces)
    is_gen = any(s.is_generator for s in surfaces)

    return CallableSurface(
        name=common_name,
        qualname=common_name,
        module=surfaces[0].module,
        parameters=tuple(merged_params),
        return_annotation=merged_return,
        is_async=is_async,
        is_generator=is_gen,
        docstring="",
        surface_id=uuid.uuid4().hex,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "FunctionMorphismAnalyzer",
    "SignatureExtractor",
    "AnnotationResolver",
    "CallableSurfaceCache",
    "surface_from_callable",
    "surfaces_are_compatible",
    "merge_surfaces",
]
