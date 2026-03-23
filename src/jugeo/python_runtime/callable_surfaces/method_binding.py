"""Section 2 of the callable_surfaces package: Method Binding as Restriction along the class→instance morphism.

References theory2.tex Ch16. Copilot integration via CopilotCallableAdvisor.

Method binding is restriction along the class→instance morphism: given class C
and instance obj, ``obj.method`` is the restriction of the unbound method to
the instance coordinate. This reduces arity by 1 (for regular methods, 'self'
is bound). The site-theoretic viewpoint treats the class C as a coordinate,
each instance as a restriction of that coordinate, and the bound method as the
restriction morphism applied to the unbound callable surface.

This module implements the binding machinery, MRO computation, attribute
resolution via the descriptor protocol, and constraint checking for method
binding correctness.

Architecture
------------
:class:`MethodBinder`
    Binds Python callables to instances or classes, issuing
    :class:`~jugeo.python_runtime.callable_surfaces.models.BoundMethod` records
    and corresponding :class:`~jugeo.judgments.judgment_terms.Judgment` objects.

:class:`MROComputer`
    Implements the C3 linearization algorithm to compute the method resolution
    order of a class, validating consistency and locating the class in the MRO
    that provides a given method.

:class:`MethodResolver`
    Resolves a method or attribute through the MRO, handling the descriptor
    protocol (``__get__``, ``__set__``, ``__delete__``) and super() chains.

:class:`BindingConstraintChecker`
    Validates binding constraints: arity compatibility, ``self``/``cls`` type
    annotations, and overall binding validity.

Copilot note: CopilotCallableAdvisor may propose ``self`` type annotations for
untyped methods; these enter at ``TrustLevel.ORACLE_PROPOSED`` and require a
runtime witness before being promoted.
"""

from __future__ import annotations

import inspect
import logging
import time
import types
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
        """Stub for ParameterKind."""
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
            """Serialize to dict."""
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
            """Parse from dict."""
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
            """Serialize to dict."""
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
            """Parse from dict."""
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
    """Return current UTC time as ISO-8601 string.

    Returns:
        ISO-8601 timestamp string.
    """
    import datetime
    return datetime.datetime.utcnow().isoformat()


def _class_qualname(cls: type) -> str:
    """Return a stable qualified name string for a class.

    Parameters:
        cls: A Python class.

    Returns:
        ``"module.QualifiedName"`` or ``"QualifiedName"`` if module is builtins.
    """
    mod = getattr(cls, "__module__", "") or ""
    qname = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", repr(cls))
    if mod and mod != "builtins":
        return f"{mod}.{qname}"
    return qname


def _extract_surface_from_callable(func: Any) -> CallableSurface:
    """Extract a minimal :class:`CallableSurface` from a callable.

    Used internally in s02 without importing s01 to avoid circular imports.

    Parameters:
        func: Any Python callable.

    Returns:
        A :class:`CallableSurface`.
    """
    name = getattr(func, "__name__", "<unknown>")
    qualname = getattr(func, "__qualname__", name)
    module = getattr(func, "__module__", "") or ""
    is_async = inspect.iscoroutinefunction(func)
    is_gen = inspect.isgeneratorfunction(func)
    doc = inspect.getdoc(func) or ""

    try:
        sig = inspect.signature(func, follow_wrapped=True)
    except (ValueError, TypeError):
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

    params: list[ParameterSpec] = []
    for pname, param in sig.parameters.items():
        anno = param.annotation
        if anno is inspect.Parameter.empty:
            anno_str = "Any"
        elif isinstance(anno, str):
            anno_str = anno
        elif isinstance(anno, type):
            anno_str = anno.__name__
        else:
            anno_str = repr(anno)

        has_default = param.default is not inspect.Parameter.empty
        default_repr = ""
        if has_default:
            try:
                default_repr = repr(param.default)
                if len(default_repr) > 80:
                    default_repr = default_repr[:77] + "..."
            except Exception:
                default_repr = "<default>"

        kind_map = {
            inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
            inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD,
        }
        pk = kind_map.get(param.kind, ParameterKind.POSITIONAL_OR_KEYWORD)
        is_variadic = param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

        params.append(ParameterSpec(
            name=pname,
            kind=pk,
            annotation=anno_str,
            has_default=has_default,
            default_repr=default_repr,
            is_variadic=is_variadic,
        ))

    ret_anno = sig.return_annotation
    if ret_anno is inspect.Signature.empty:
        ret_str = "Any"
    elif isinstance(ret_anno, type):
        ret_str = ret_anno.__name__
    elif isinstance(ret_anno, str):
        ret_str = ret_anno
    else:
        ret_str = repr(ret_anno)

    return CallableSurface(
        name=name,
        qualname=qualname,
        module=module,
        parameters=tuple(params),
        return_annotation=ret_str,
        is_async=is_async,
        is_generator=is_gen,
        docstring=doc,
        surface_id=uuid.uuid4().hex,
        created_at=time.time(),
    )


def _drop_self_parameter(surface: CallableSurface) -> CallableSurface:
    """Return a copy of ``surface`` with the first ``self`` parameter removed.

    If the first parameter is named ``self`` (or ``cls`` for class methods),
    it is stripped. Otherwise the surface is returned unchanged.

    Parameters:
        surface: The :class:`CallableSurface` to strip.

    Returns:
        A new :class:`CallableSurface` without the leading ``self``/``cls``
        parameter, or the original surface if no such parameter was found.
    """
    from dataclasses import replace as dc_replace
    params = surface.parameters
    if params and params[0].name in ("self", "cls"):
        return dc_replace(surface, parameters=params[1:])
    return surface


# ---------------------------------------------------------------------------
# MROComputer — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MROComputer:
    """Computes and validates the C3 MRO linearization of a class.

    Implements the C3 merge algorithm as specified in the Python data model
    and validates the resulting ordering for consistency.

    Parameters:
        strict: When ``True``, raise ``TypeError`` on inconsistent MROs
            instead of returning a best-effort result.
    """

    strict: bool

    def compute_mro(self, cls: type) -> tuple[str, ...]:
        """Compute the MRO for ``cls`` and return class names.

        Uses :attr:`type.__mro__` (the canonical C3 result maintained by
        Python's type machinery) to avoid reimplementing C3 from scratch for
        production use, while still validating consistency.

        Parameters:
            cls: The class whose MRO to compute.

        Returns:
            Tuple of fully-qualified class name strings in MRO order.

        Raises:
            TypeError: If ``strict=True`` and the MRO is inconsistent.
        """
        # Python's type.__mro__ already gives us the C3 ordering.
        mro_types = getattr(cls, "__mro__", (cls,))
        names = tuple(_class_qualname(c) for c in mro_types)
        errors = self.validate_mro(names)
        if errors and self.strict:
            raise TypeError(
                f"Inconsistent MRO for {_class_qualname(cls)!r}: {errors}"
            )
        return names

    def c3_merge(self, sequences: list[list[type]]) -> list[type]:
        """Perform the C3 merge step on a list of linearization sequences.

        Given the sequences L[B1], L[B2], ..., [B1, B2, ...], this method
        repeatedly selects the head of the first sequence that does not
        appear in the tail of any other sequence, removes it from all
        sequences where it appears as the head, and appends it to the output.

        Parameters:
            sequences: List of type lists as used in C3 linearization.

        Returns:
            The merged linear ordering of types.

        Raises:
            TypeError: When no valid head can be found (inconsistent
                hierarchy) and ``strict=True``.
        """
        result: list[type] = []
        seqs = [list(s) for s in sequences]  # defensive copy
        while True:
            # Remove empty sequences
            seqs = [s for s in seqs if s]
            if not seqs:
                break
            # Find a good head: not in the tail of any sequence
            head: type | None = None
            for seq in seqs:
                candidate = seq[0]
                # Check it does not appear in any tail
                in_tail = any(candidate in s[1:] for s in seqs)
                if not in_tail:
                    head = candidate
                    break
            if head is None:
                # No valid head found — inconsistent MRO
                if self.strict:
                    raise TypeError(
                        "Cannot compute consistent C3 merge; "
                        "remaining sequences: " + repr(seqs)
                    )
                # Best-effort: take the head of the first non-empty sequence
                head = seqs[0][0]
            result.append(head)
            # Remove head from the front of all sequences where it appears
            seqs = [s[1:] if s and s[0] is head else s for s in seqs]
        return result

    def c3_linearize(self, cls: type) -> list[type]:
        """Compute the full C3 linearization of ``cls``.

        Builds the linearization sequences for each base class and merges
        them with the list of direct bases, as specified in the C3 algorithm.

        Parameters:
            cls: The class to linearize.

        Returns:
            List of types in C3 MRO order (including ``cls`` and ``object``).
        """
        if cls is object:
            return [object]
        bases = list(cls.__bases__)
        if not bases:
            return [cls]
        # L[cls] = [cls] + merge(L[B1], L[B2], ..., [B1, B2, ...])
        base_linearizations = [self.c3_linearize(b) for b in bases]
        sequences = base_linearizations + [bases]
        merged = self.c3_merge(sequences)
        return [cls] + merged

    def validate_mro(self, mro: tuple[str, ...]) -> list[str]:
        """Validate the consistency of a pre-computed MRO (as class name strings).

        Checks that:
        * The MRO is non-empty.
        * No class name appears more than once.
        * ``"object"`` (or a builtins-qualified variant) appears at most once
          and, if present, is at the end.

        Parameters:
            mro: Tuple of class name strings in MRO order.

        Returns:
            List of error message strings; empty if the MRO is valid.
        """
        errors: list[str] = []
        if not mro:
            errors.append("MRO is empty")
            return errors
        seen: set[str] = set()
        for name in mro:
            if name in seen:
                errors.append(f"Duplicate class in MRO: {name!r}")
            seen.add(name)
        # Check object is at the end (if present)
        object_names = {"object", "builtins.object"}
        for i, name in enumerate(mro):
            if name in object_names and i != len(mro) - 1:
                errors.append(
                    f"'object' appears at position {i} in MRO but should be last"
                )
        return errors

    def find_method_in_mro(
        self,
        mro: tuple[str, ...],
        classes: list[type],
        method_name: str,
    ) -> type | None:
        """Find the first class in the MRO that defines ``method_name``.

        Iterates through ``classes`` in MRO order (as determined by the index
        of each class's qualified name in ``mro``), returning the first class
        whose ``__dict__`` directly contains ``method_name``.

        Parameters:
            mro: Ordered tuple of class name strings (from :meth:`compute_mro`).
            classes: The actual type objects corresponding to ``mro`` entries.
            method_name: The method name to search for.

        Returns:
            The first :class:`type` providing the method, or ``None`` if not found.
        """
        # Build a mapping from qualified name → class for fast lookup
        qname_to_cls: dict[str, type] = {}
        for cls in classes:
            qname_to_cls[_class_qualname(cls)] = cls

        for qname in mro:
            cls = qname_to_cls.get(qname)
            if cls is None:
                continue
            if method_name in cls.__dict__:
                return cls
        return None

    def all_bases(self, cls: type) -> set[type]:
        """Return the set of all base classes of ``cls`` transitively.

        Parameters:
            cls: The class to inspect.

        Returns:
            Set of all ancestor types (including ``cls`` itself and
            ``object``).
        """
        result: set[type] = set()
        queue: list[type] = [cls]
        while queue:
            current = queue.pop()
            if current in result:
                continue
            result.add(current)
            queue.extend(getattr(current, "__bases__", ()))
        return result

    def base_classes_at_depth(self, cls: type, depth: int) -> set[type]:
        """Return the set of base classes exactly ``depth`` levels above ``cls``.

        Parameters:
            cls: The starting class.
            depth: The number of inheritance steps to traverse.

        Returns:
            Set of classes exactly ``depth`` levels above ``cls`` in the
            hierarchy. ``depth=0`` returns ``{cls}``; ``depth=1`` returns
            ``set(cls.__bases__)``.
        """
        if depth < 0:
            return set()
        if depth == 0:
            return {cls}
        current_level: set[type] = {cls}
        for _ in range(depth):
            next_level: set[type] = set()
            for c in current_level:
                next_level.update(getattr(c, "__bases__", ()))
            current_level = next_level
        return current_level


# ---------------------------------------------------------------------------
# MethodResolver — mutable dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MethodResolver:
    """Resolves methods and attributes through the MRO and descriptor protocol.

    Caches resolution results to avoid repeated MRO traversals.

    Parameters:
        _cache: Dict from ``(class_qualname, attr_name)`` → resolved value or sentinel.
        _mro_computer: The :class:`MROComputer` used for MRO traversal.
    """

    _cache: dict[str, Any]
    _mro_computer: MROComputer

    def resolve_method(self, cls: type, name: str) -> Any | None:
        """Resolve a method on ``cls`` by traversing the MRO.

        Parameters:
            cls: The class to start from.
            name: The attribute/method name to resolve.

        Returns:
            The raw attribute value from the first class in the MRO that
            defines it, or ``None`` if not found anywhere.
        """
        cache_key = f"{_class_qualname(cls)}::{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        result_pair = self.find_in_mro(cls, name)
        if result_pair is None:
            self._cache[cache_key] = None
            return None
        _defining_cls, value = result_pair
        self._cache[cache_key] = value
        return value

    def resolve_attribute(self, obj: Any, name: str) -> Any | None:
        """Resolve an attribute on ``obj`` using the descriptor protocol.

        The lookup order follows the Python data model:
        1. Data descriptors from the type's MRO.
        2. Instance ``__dict__``.
        3. Non-data descriptors / class attributes from the type's MRO.

        Parameters:
            obj: Any Python object.
            name: Attribute name.

        Returns:
            The attribute value, or ``None`` if not found.
        """
        cls = type(obj)
        # Step 1: scan MRO for data descriptors (have both __get__ and __set__)
        for base in cls.__mro__:
            if name in base.__dict__:
                attr = base.__dict__[name]
                if hasattr(attr, "__get__") and hasattr(attr, "__set__"):
                    # Data descriptor — takes precedence over instance __dict__
                    try:
                        return attr.__get__(obj, cls)
                    except AttributeError:
                        return None
                break  # Found in MRO but not a data descriptor; check instance dict next

        # Step 2: instance __dict__
        instance_dict = getattr(obj, "__dict__", {}) or {}
        if name in instance_dict:
            return instance_dict[name]

        # Step 3: non-data descriptors and plain class attributes
        for base in cls.__mro__:
            if name in base.__dict__:
                attr = base.__dict__[name]
                if hasattr(attr, "__get__"):
                    try:
                        return attr.__get__(obj, cls)
                    except AttributeError:
                        return attr
                return attr

        return None

    def find_in_mro(self, cls: type, name: str) -> tuple[type, Any] | None:
        """Find the first class in the MRO that defines ``name``.

        Parameters:
            cls: The starting class.
            name: The attribute name to look up.

        Returns:
            A ``(defining_class, value)`` tuple, or ``None`` if ``name``
            is not defined anywhere in the MRO.
        """
        for base in cls.__mro__:
            if name in base.__dict__:
                return (base, base.__dict__[name])
        return None

    def handle_super(self, cls: type, instance: Any, name: str) -> Any | None:
        """Simulate a ``super()`` attribute lookup starting above ``cls`` in the MRO.

        Finds the position of ``cls`` in the MRO of ``type(instance)``, then
        searches subsequent classes for ``name``.

        Parameters:
            cls: The class to start the search above.
            instance: The instance (or class) on which to perform the lookup.
            name: Attribute name.

        Returns:
            The attribute value from the first class above ``cls`` in the
            MRO that defines it, or ``None`` if not found.
        """
        if inspect.isclass(instance):
            mro = instance.__mro__
        else:
            mro = type(instance).__mro__
        try:
            start_index = list(mro).index(cls) + 1
        except ValueError:
            # cls not in the MRO; start from the beginning
            start_index = 0
        for base in mro[start_index:]:
            if name in base.__dict__:
                attr = base.__dict__[name]
                if hasattr(attr, "__get__"):
                    try:
                        return attr.__get__(instance, type(instance))
                    except AttributeError:
                        return attr
                return attr
        return None

    def handle_overriding(self, cls: type, name: str) -> list[tuple[str, Any]]:
        """Find all classes in the MRO that provide an override of ``name``.

        Returns every class in the MRO whose ``__dict__`` contains ``name``,
        not just the first one. This allows callers to see the full chain of
        overrides from most-derived to least-derived.

        Parameters:
            cls: The class to inspect.
            name: Attribute name.

        Returns:
            List of ``(class_qualname, value)`` pairs in MRO order (most
            derived first).
        """
        overrides: list[tuple[str, Any]] = []
        for base in cls.__mro__:
            if name in base.__dict__:
                overrides.append((_class_qualname(base), base.__dict__[name]))
        return overrides

    def build_resolution_chain(self, cls: type, name: str) -> list[str]:
        """Build the full chain of class names that provide ``name`` in the MRO.

        Parameters:
            cls: The class to inspect.
            name: Attribute name.

        Returns:
            Ordered list of class qualified-name strings (from most-derived to
            least-derived) that define ``name`` in their own ``__dict__``.
            Classes that inherit but do not directly define ``name`` are excluded.
        """
        chain: list[str] = []
        for base in cls.__mro__:
            if name in base.__dict__:
                chain.append(_class_qualname(base))
        return chain


# ---------------------------------------------------------------------------
# BindingConstraintChecker — frozen dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BindingConstraintChecker:
    """Validates binding constraints between an unbound surface and a bound method.

    Checks arity compatibility (self/cls stripped), type annotation validity
    of the first parameter, and overall binding integrity.

    Parameters:
        strict_mode: When ``True``, treat annotation mismatches as errors
            rather than warnings.
    """

    strict_mode: bool

    def check_arity_compatibility(
        self, surface: CallableSurface, instance: Any
    ) -> bool:
        """Check that the unbound surface has at least one parameter for binding.

        A callable surface is arity-compatible with binding if it has at
        least one parameter (to absorb ``self`` or ``cls``).

        Parameters:
            surface: The unbound :class:`CallableSurface`.
            instance: The instance (or class) being bound.

        Returns:
            ``True`` if the surface has at least one parameter.
        """
        return len(surface.parameters) >= 1

    def check_self_type(self, method: Any, instance: Any) -> list[str]:
        """Validate that the ``self`` annotation is compatible with ``instance``.

        Attempts to retrieve the type annotation for the first parameter of
        ``method`` and checks that ``isinstance(instance, resolved_type)``
        holds (when the annotation can be resolved to a live type).

        Parameters:
            method: An unbound Python method or function.
            instance: The instance to bind to.

        Returns:
            A possibly-empty list of violation message strings.
        """
        violations: list[str] = []
        try:
            sig = inspect.signature(method, follow_wrapped=True)
        except (ValueError, TypeError) as exc:
            violations.append(f"Cannot inspect signature of {method!r}: {exc}")
            return violations

        params = list(sig.parameters.values())
        if not params:
            violations.append(
                f"Method {getattr(method, '__qualname__', repr(method))!r} "
                "has no parameters; cannot bind self"
            )
            return violations

        first_param = params[0]
        anno = first_param.annotation
        if anno is inspect.Parameter.empty:
            # No annotation — assume compatible
            return violations

        # Attempt to resolve the annotation to a type
        resolved_type: type | None = None
        if isinstance(anno, type):
            resolved_type = anno
        elif isinstance(anno, str):
            # Try to find the type in the method's globals
            func_globals = getattr(method, "__globals__", {}) or {}
            resolved_type = func_globals.get(anno)
            if resolved_type is not None and not isinstance(resolved_type, type):
                resolved_type = None

        if resolved_type is not None:
            if not isinstance(instance, resolved_type):
                msg = (
                    f"self annotation {anno!r} is not satisfied by instance of "
                    f"type {type(instance).__qualname__!r}"
                )
                if self.strict_mode:
                    violations.append(msg)
                else:
                    logger.debug("BindingConstraintChecker: %s", msg)
        return violations

    def check_cls_type(self, method: Any, cls: type) -> list[str]:
        """Validate that the ``cls`` annotation is compatible with ``cls``.

        Analogous to :meth:`check_self_type` but for classmethods where the
        first parameter is conventionally named ``cls``.

        Parameters:
            method: An unbound Python classmethod function.
            cls: The class being bound.

        Returns:
            A possibly-empty list of violation message strings.
        """
        violations: list[str] = []
        try:
            # For classmethods, the underlying function is in __func__
            underlying = getattr(method, "__func__", method)
            sig = inspect.signature(underlying, follow_wrapped=True)
        except (ValueError, TypeError) as exc:
            violations.append(f"Cannot inspect signature of classmethod {method!r}: {exc}")
            return violations

        params = list(sig.parameters.values())
        if not params:
            violations.append(
                f"Classmethod {getattr(method, '__qualname__', repr(method))!r} "
                "has no parameters; cannot bind cls"
            )
            return violations

        first_param = params[0]
        if first_param.name not in ("cls", "mcs", "mcls"):
            # Unconventional first-parameter name — warn only
            logger.debug(
                "BindingConstraintChecker: classmethod first param is %r, expected 'cls'",
                first_param.name,
            )

        anno = first_param.annotation
        if anno is inspect.Parameter.empty:
            return violations

        # The annotation of cls should be a metatype (type or subclass of type)
        if isinstance(anno, type) and not issubclass(anno, type):
            # Anno is a regular class, not a metatype; check cls is a subclass
            if not issubclass(cls, anno):
                msg = (
                    f"cls annotation {anno.__qualname__!r} is not satisfied by "
                    f"class {cls.__qualname__!r}"
                )
                if self.strict_mode:
                    violations.append(msg)
                else:
                    logger.debug("BindingConstraintChecker: %s", msg)
        return violations

    def check_binding_validity(
        self, surface: CallableSurface, bound: BoundMethod
    ) -> list[str]:
        """Validate overall binding validity between an unbound surface and a bound method.

        Checks:
        * The bound surface has exactly one fewer parameter than the unbound
          surface (self/cls has been stripped).
        * The binding morphism string is non-empty.
        * The instance_type string is non-empty.

        Parameters:
            surface: The unbound :class:`CallableSurface`.
            bound: The :class:`BoundMethod` produced by binding.

        Returns:
            A possibly-empty list of violation message strings.
        """
        violations: list[str] = []
        bound_surface = bound.surface
        if bound_surface is None:
            violations.append("BoundMethod.surface is None")
            return violations

        unbound_arity = len(surface.parameters)
        bound_arity = len(bound_surface.parameters)
        # Bound arity should be one less (self stripped) or same (static)
        if bound_arity not in (unbound_arity, unbound_arity - 1):
            violations.append(
                f"Bound surface arity {bound_arity} is incompatible with "
                f"unbound arity {unbound_arity}"
            )
        if not bound.binding_morphism:
            violations.append("BoundMethod.binding_morphism is empty")
        if not bound.instance_type:
            violations.append("BoundMethod.instance_type is empty")
        return violations

    def build_constraint_judgment(
        self, bound: BoundMethod, violations: list[str]
    ) -> Judgment:
        """Build a :class:`Judgment` recording binding constraint results.

        Issues a structural judgment at ``RUNTIME_WITNESSED`` trust when
        there are no violations, or at ``UNVERIFIED`` trust when there are.

        Parameters:
            bound: The :class:`BoundMethod` to judge.
            violations: Violation strings from constraint checking.

        Returns:
            A :class:`Judgment` with status ``SETTLED`` or ``OBSTRUCTED``.
        """
        surface = bound.surface
        surface_name = surface.name if surface is not None else "<unknown>"
        formula = (
            f"method_binding_valid("
            f"method={surface_name!r}, "
            f"instance_type={bound.instance_type!r}, "
            f"morphism={bound.binding_morphism!r})"
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
            metadata={"violations": violations},
        )
        carrier_params: tuple[str, ...] = ()
        if surface is not None:
            carrier_params = tuple(p.name for p in surface.parameters)
        carrier = Carrier(
            name=surface_name,
            parameters=carrier_params,
            is_dependent=bool(bound.instance_type),
            metadata={"instance_type": bound.instance_type},
        )
        trust_level = TrustLevel.UNVERIFIED if violations else TrustLevel.RUNTIME_WITNESSED
        now = _now_iso()
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"violations": violations, "binding_morphism": bound.binding_morphism},
            trust_level=trust_level,
            channel="binding_constraint_checker",
            timestamp=now,
            expiry="",
            provenance=(bound.binding_morphism,),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"checker": "BindingConstraintChecker"},
        )
        trust_ann = TrustAnnotation(
            level=trust_level,
            evidence_basis=(bound.binding_morphism,),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=tuple(violations) if violations else ("binding constraints satisfied",),
        )
        components = ("binding", bound.instance_type, surface_name)
        try:
            coord = CoordinateObject(
                components=components,
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({surface_name, bound.instance_type}),
                metadata={"binding_morphism": bound.binding_morphism},
            )
        except Exception:
            coord = CoordinateObject()  # type: ignore[call-arg]

        obstructions: tuple[Any, ...] = ()
        if violations:
            obstructions = tuple(
                Obstruction(description=v) for v in violations
            )
        status = JudgmentStatus.OBSTRUCTED if violations else JudgmentStatus.SETTLED
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=trust_ann,
            provenance=prov,
            clauses=(),
            status=status,
        )


# ---------------------------------------------------------------------------
# MethodBinder — mutable dataclass (KEY class)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MethodBinder:
    """Binds Python methods to instances or classes, modelling restriction morphisms.

    In the site-theoretic framework, ``obj.method`` is the restriction of the
    unbound method surface along the class → instance morphism. This class
    implements that restriction: it extracts the unbound surface, strips the
    ``self`` parameter, and records the binding as a :class:`BoundMethod`.

    Parameters:
        _bindings: Cache from ``binding_key`` → :class:`BoundMethod`.
        _site: Optional :class:`Site` for coordinate registration.
        _errors: Accumulated error strings.
    """

    _bindings: dict[str, BoundMethod]
    _site: Site | None
    _errors: list[str]

    def bind(self, method: Any, instance: Any) -> BoundMethod:
        """Bind a regular method to an instance.

        Extracts the unbound surface, drops the ``self`` parameter, and
        constructs a :class:`BoundMethod` describing the restriction morphism.

        Parameters:
            method: An unbound Python function or method.
            instance: The instance to bind to.

        Returns:
            A :class:`BoundMethod` with the ``self`` parameter stripped from
            its surface.
        """
        # Use the underlying function if already bound
        underlying = getattr(method, "__func__", method)
        unbound_surface = _extract_surface_from_callable(underlying)
        bound_surface = _drop_self_parameter(unbound_surface)
        morphism_desc = self.compute_binding_morphism(type(instance), instance)
        instance_type_name = _class_qualname(type(instance))

        bound = self.build_bound_method(method, instance, bound_surface)
        key = f"{instance_type_name}::{unbound_surface.qualname}"
        self._bindings[key] = bound
        logger.debug("MethodBinder.bind: %r → %r", unbound_surface.qualname, instance_type_name)
        return bound

    def bind_classmethod(self, method: Any, cls: type) -> BoundMethod:
        """Bind a classmethod to a class.

        For classmethods the first parameter is ``cls``; binding strips it
        and records the class as the bound context.

        Parameters:
            method: A classmethod object or its underlying function.
            cls: The class to bind to.

        Returns:
            A :class:`BoundMethod` with the ``cls`` parameter stripped.
        """
        # classmethods have a __func__ attribute pointing to the underlying function
        underlying = getattr(method, "__func__", method)
        unbound_surface = _extract_surface_from_callable(underlying)
        bound_surface = _drop_self_parameter(unbound_surface)
        morphism_desc = f"classmethod_restriction:{_class_qualname(cls)}"
        instance_type_name = _class_qualname(cls)

        from dataclasses import replace as dc_replace
        bound = BoundMethod(
            surface=bound_surface,
            instance_type=instance_type_name,
            binding_morphism=morphism_desc,
            bound_at=time.time(),
            metadata={"binding_kind": "classmethod"},
        )
        key = f"{instance_type_name}::classmethod::{unbound_surface.qualname}"
        self._bindings[key] = bound
        logger.debug("MethodBinder.bind_classmethod: %r → %r", unbound_surface.qualname, instance_type_name)
        return bound

    def bind_staticmethod(self, method: Any) -> BoundMethod:
        """Wrap a staticmethod as a :class:`BoundMethod`.

        Static methods have no ``self`` or ``cls`` parameter; binding is a
        no-op on the parameter list. The binding morphism is recorded as
        ``"static_identity"``.

        Parameters:
            method: A staticmethod object or plain function.

        Returns:
            A :class:`BoundMethod` whose surface is identical to the
            unbound surface (no parameter stripped).
        """
        underlying = getattr(method, "__func__", method)
        surface = _extract_surface_from_callable(underlying)
        bound = BoundMethod(
            surface=surface,
            instance_type="<static>",
            binding_morphism="static_identity",
            bound_at=time.time(),
            metadata={"binding_kind": "staticmethod"},
        )
        key = f"static::{surface.qualname}"
        self._bindings[key] = bound
        logger.debug("MethodBinder.bind_staticmethod: %r", surface.qualname)
        return bound

    def unbind(self, bound: BoundMethod) -> CallableSurface:
        """Retrieve the unbound callable surface from a :class:`BoundMethod`.

        For regular methods, re-inserts a synthetic ``self: Any`` parameter
        at position 0 to reconstruct the unbound surface. For static methods,
        the surface is returned unchanged.

        Parameters:
            bound: The :class:`BoundMethod` to unbind.

        Returns:
            A :class:`CallableSurface` representing the unbound method.
        """
        surface = bound.surface
        if surface is None:
            return CallableSurface(
                name="<unknown>",
                qualname="<unknown>",
                module="",
                parameters=(),
                return_annotation="Any",
                is_async=False,
                is_generator=False,
                docstring="",
                surface_id=uuid.uuid4().hex,
                created_at=time.time(),
            )
        if bound.binding_morphism == "static_identity":
            # Static methods don't have self stripped
            return surface
        # Re-insert self parameter
        from dataclasses import replace as dc_replace
        self_param = ParameterSpec(
            name="self",
            kind=ParameterKind.POSITIONAL_OR_KEYWORD,
            annotation=bound.instance_type or "Any",
            has_default=False,
            default_repr="",
            is_variadic=False,
        )
        unbound_params = (self_param,) + surface.parameters
        return dc_replace(surface, parameters=unbound_params)

    def is_bound(self, func: Any) -> bool:
        """Check whether ``func`` is already a bound method.

        A callable is considered bound if it has a ``__self__`` attribute
        that is not ``None``, which is the standard Python indicator for
        bound methods.

        Parameters:
            func: Any callable.

        Returns:
            ``True`` if ``func`` is a bound method.
        """
        self_attr = getattr(func, "__self__", None)
        return self_attr is not None

    def compute_binding_morphism(self, cls: type, instance: Any) -> str:
        """Describe the restriction morphism from the class coordinate to the instance.

        Produces a string of the form ``"restrict:{cls_qualname}→{instance_id}"``.

        Parameters:
            cls: The class coordinate (domain of the morphism).
            instance: The instance (target of the restriction).

        Returns:
            A string describing the morphism.
        """
        cls_name = _class_qualname(cls)
        instance_id = hex(id(instance))
        return f"restrict:{cls_name}→{instance_id}"

    def validate_binding(
        self, surface: CallableSurface, instance: Any
    ) -> list[str]:
        """Validate that a surface can be bound to an instance.

        Checks that the surface has at least one parameter (for ``self``),
        and that the first parameter name is a conventional self name.

        Parameters:
            surface: The unbound :class:`CallableSurface`.
            instance: The instance to bind to.

        Returns:
            List of error strings; empty if binding is valid.
        """
        errors: list[str] = []
        if not surface.parameters:
            errors.append(
                f"Cannot bind surface {surface.name!r}: has no parameters"
            )
            return errors
        first_name = surface.parameters[0].name
        if first_name not in ("self", "cls", "mcs", "mcls"):
            logger.debug(
                "validate_binding: first parameter %r is not 'self' or 'cls' on %r",
                first_name,
                surface.name,
            )
        # Check that instance is actually an instance of some type
        if not hasattr(type(instance), "__mro__"):
            errors.append(
                f"Instance of type {type(instance)!r} has no __mro__; binding may fail"
            )
        return errors

    def build_bound_method(
        self,
        method: Any,
        instance: Any,
        surface: CallableSurface,
    ) -> BoundMethod:
        """Construct a :class:`BoundMethod` record.

        Parameters:
            method: The unbound Python method or function.
            instance: The instance the method is bound to.
            surface: The already-stripped :class:`CallableSurface`.

        Returns:
            A fully populated :class:`BoundMethod`.
        """
        instance_type_name = _class_qualname(type(instance))
        morphism = self.compute_binding_morphism(type(instance), instance)
        method_qualname = getattr(method, "__qualname__", getattr(method, "__name__", repr(method)))
        return BoundMethod(
            surface=surface,
            instance_type=instance_type_name,
            binding_morphism=morphism,
            bound_at=time.time(),
            metadata={
                "method_qualname": method_qualname,
                "instance_id": hex(id(instance)),
            },
        )

    def build_judgment(self, bound: BoundMethod) -> Judgment:
        """Build a :class:`Judgment` asserting that a method binding is valid.

        Issues a behavioral judgment at ``RUNTIME_WITNESSED`` trust, recording
        evidence of the live binding.

        Parameters:
            bound: The :class:`BoundMethod` to judge.

        Returns:
            A :class:`Judgment` with status ``SETTLED``.
        """
        surface = bound.surface
        surface_name = surface.name if surface is not None else "<unknown>"
        formula = (
            f"method_binding("
            f"method={surface_name!r}, "
            f"instance_type={bound.instance_type!r})"
        )
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=formula,
            free_variables=(),
            metadata={
                "instance_type": bound.instance_type,
                "binding_morphism": bound.binding_morphism,
            },
        )
        carrier_params: tuple[str, ...] = ()
        if surface is not None:
            carrier_params = tuple(p.name for p in surface.parameters)
        carrier = Carrier(
            name=surface_name,
            parameters=carrier_params,
            is_dependent=True,
            metadata={"instance_type": bound.instance_type},
        )
        now = _now_iso()
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "surface_name": surface_name,
                "instance_type": bound.instance_type,
                "binding_morphism": bound.binding_morphism,
                "bound_at": bound.bound_at,
            },
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel="method_binder",
            timestamp=now,
            expiry="",
            provenance=(bound.binding_morphism,),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        prov = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=now,
            transformation_history=(),
            metadata={"binder": "MethodBinder"},
        )
        trust_ann = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=(bound.binding_morphism,),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=("method bound at runtime",),
        )
        components = ("binding", bound.instance_type, surface_name)
        try:
            coord = CoordinateObject(
                components=components,
                kind=CoordinateKind.FUNCTION,
                support_labels=frozenset({surface_name, bound.instance_type}),
                metadata={"binding_morphism": bound.binding_morphism},
            )
        except Exception:
            coord = CoordinateObject()  # type: ignore[call-arg]

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
            status=JudgmentStatus.SETTLED,
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MethodBinder",
    "MROComputer",
    "MethodResolver",
    "BindingConstraintChecker",
]
