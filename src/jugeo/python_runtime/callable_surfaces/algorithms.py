"""Algorithms for callable surface analysis, method resolution, and class hierarchy
traversal.  References theory2.tex Ch16.  Provides copilot integration points for
automated analysis.

This module implements the core algorithmic layer for introspecting Python callables,
resolving method bindings, checking call compatibility, and traversing class
hierarchies.  All algorithms operate on :class:`CallableSurface` objects and produce
:class:`~jugeo.judgments.judgment_terms.Judgment` records that can be fed into the
JuGeo judgment system for verification and trust tracking.

Sheaf-theoretic grounding
--------------------------
A *CallableSurface* is a section of the callable presheaf ``F`` over a semantic site
whose objects are qualified Python names.  Compatibility checks correspond to
restriction maps; method resolution corresponds to the glueing axiom (taking the
unique section that restricts to each base consistently along the MRO cover).

Module-level utilities
-----------------------
* :class:`CallableSurfaceAnalyzer` — produce and cache surfaces from live callables.
* :class:`MethodResolutionAlgorithm` — simulate Python's full descriptor protocol.
* :class:`CallCompatibilityChecker` — verify that a call site is compatible with a surface.
* :class:`InheritanceGraphAlgorithm` — build and query class inheritance graphs.
* :class:`DecoratorAnalyzer` — introspect and reason about the decorator stack.
"""

from __future__ import annotations

import dis
import inspect
import logging
import sys
import time
import types
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports – callable_surfaces.models
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.callable_surfaces.models import (  # type: ignore[import]
        BoundMethod,
        CallableSurface,
        ClassConstruction,
        DescriptorKind,
        DescriptorRecord,
        MethodBinding,
        ParameterKind,
        ParameterSpec,
        SignatureRecord,
    )
except ImportError:
    _log.debug("callable_surfaces.models unavailable – activating algorithm stubs")

    class ParameterKind:  # type: ignore[no-redef]
        POSITIONAL_ONLY = "POSITIONAL_ONLY"
        POSITIONAL_OR_KEYWORD = "POSITIONAL_OR_KEYWORD"
        VAR_POSITIONAL = "VAR_POSITIONAL"
        KEYWORD_ONLY = "KEYWORD_ONLY"
        VAR_KEYWORD = "VAR_KEYWORD"

    _EMPTY_SENTINEL = inspect.Parameter.empty

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub: single callable parameter descriptor."""

        name: str
        kind: str
        annotation: Any = inspect.Parameter.empty
        has_default: bool = False
        default_value: Any = inspect.Parameter.empty

        def serialize(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "kind": self.kind,
                "annotation": repr(self.annotation),
                "has_default": self.has_default,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> ParameterSpec:
            return cls(
                name=data["name"],
                kind=data.get("kind", "POSITIONAL_OR_KEYWORD"),
                has_default=data.get("has_default", False),
            )

    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub: immutable descriptor of a Python callable's public surface."""

        name: str
        qualname: str
        parameters: tuple[ParameterSpec, ...]
        return_annotation: Any
        is_async: bool = False
        is_generator: bool = False
        is_coroutine: bool = False
        module: str = ""
        docstring: str | None = None
        source_file: str | None = None
        lineno: int | None = None
        closure_vars: tuple[str, ...] = ()
        decorators: tuple[str, ...] = ()

        def serialize(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "qualname": self.qualname,
                "parameters": [p.serialize() for p in self.parameters],
                "return_annotation": repr(self.return_annotation),
                "is_async": self.is_async,
                "is_generator": self.is_generator,
                "module": self.module,
                "docstring": self.docstring,
                "source_file": self.source_file,
                "lineno": self.lineno,
                "closure_vars": list(self.closure_vars),
                "decorators": list(self.decorators),
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> CallableSurface:
            params = tuple(ParameterSpec.parse(p) for p in data.get("parameters", []))
            return cls(
                name=data["name"],
                qualname=data.get("qualname", data["name"]),
                parameters=params,
                return_annotation=inspect.Parameter.empty,
                is_async=data.get("is_async", False),
                is_generator=data.get("is_generator", False),
                module=data.get("module", ""),
                docstring=data.get("docstring"),
                source_file=data.get("source_file"),
                lineno=data.get("lineno"),
                closure_vars=tuple(data.get("closure_vars", [])),
                decorators=tuple(data.get("decorators", [])),
            )

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub: binding record for a method on a class."""

        method_name: str
        owner_class: str
        defined_in: str
        is_classmethod: bool = False
        is_staticmethod: bool = False
        is_abstractmethod: bool = False
        surface: Any = None

        def serialize(self) -> dict[str, Any]:
            return {
                "method_name": self.method_name,
                "owner_class": self.owner_class,
                "defined_in": self.defined_in,
                "is_classmethod": self.is_classmethod,
                "is_staticmethod": self.is_staticmethod,
                "is_abstractmethod": self.is_abstractmethod,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> MethodBinding:
            return cls(
                method_name=data["method_name"],
                owner_class=data.get("owner_class", ""),
                defined_in=data.get("defined_in", ""),
                is_classmethod=data.get("is_classmethod", False),
                is_staticmethod=data.get("is_staticmethod", False),
            )

    class DescriptorKind:  # type: ignore[no-redef]
        DATA = "DATA"
        NON_DATA = "NON_DATA"
        OVERRIDING = "OVERRIDING"
        VIRTUAL = "VIRTUAL"

    @dataclass(frozen=True, slots=True)
    class DescriptorRecord:  # type: ignore[no-redef]
        """Stub: describes a descriptor found in a class hierarchy."""

        name: str
        kind: str
        owner: str
        has_get: bool = False
        has_set: bool = False
        has_delete: bool = False
        priority: int = 0

        def serialize(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "kind": self.kind,
                "owner": self.owner,
                "has_get": self.has_get,
                "has_set": self.has_set,
                "has_delete": self.has_delete,
                "priority": self.priority,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> DescriptorRecord:
            return cls(
                name=data["name"],
                kind=data.get("kind", DescriptorKind.NON_DATA),
                owner=data.get("owner", ""),
                has_get=data.get("has_get", False),
                has_set=data.get("has_set", False),
                has_delete=data.get("has_delete", False),
                priority=data.get("priority", 0),
            )

    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub: a callable surface bound to a concrete instance type."""

        surface: Any
        instance_type: str
        binding_id: str = ""

        def __post_init__(self) -> None:
            if not self.binding_id:
                object.__setattr__(self, "binding_id", uuid.uuid4().hex[:12])

        def serialize(self) -> dict[str, Any]:
            return {
                "surface": self.surface.serialize() if self.surface else {},
                "instance_type": self.instance_type,
                "binding_id": self.binding_id,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> BoundMethod:
            return cls(
                surface=CallableSurface.parse(data["surface"]) if "surface" in data else None,
                instance_type=data.get("instance_type", ""),
                binding_id=data.get("binding_id", uuid.uuid4().hex[:12]),
            )

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub: captures how a class is constructed (bases, methods, metaclass)."""

        class_name: str
        qualname: str
        bases: tuple[str, ...]
        methods: tuple[str, ...]
        metaclass: str = "type"
        is_abstract: bool = False
        is_dataclass: bool = False
        is_protocol: bool = False

        def serialize(self) -> dict[str, Any]:
            return {
                "class_name": self.class_name,
                "qualname": self.qualname,
                "bases": list(self.bases),
                "methods": list(self.methods),
                "metaclass": self.metaclass,
                "is_abstract": self.is_abstract,
                "is_dataclass": self.is_dataclass,
                "is_protocol": self.is_protocol,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> ClassConstruction:
            return cls(
                class_name=data["class_name"],
                qualname=data.get("qualname", data["class_name"]),
                bases=tuple(data.get("bases", [])),
                methods=tuple(data.get("methods", [])),
                metaclass=data.get("metaclass", "type"),
                is_abstract=data.get("is_abstract", False),
                is_dataclass=data.get("is_dataclass", False),
                is_protocol=data.get("is_protocol", False),
            )

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub: a named, possibly overloaded signature."""

        qualname: str
        parameters: tuple[ParameterSpec, ...]
        return_annotation: Any
        is_overloaded: bool = False
        overload_index: int = 0

        def serialize(self) -> dict[str, Any]:
            return {
                "qualname": self.qualname,
                "parameters": [p.serialize() for p in self.parameters],
                "return_annotation": repr(self.return_annotation),
                "is_overloaded": self.is_overloaded,
                "overload_index": self.overload_index,
            }

        @classmethod
        def parse(cls, data: dict[str, Any]) -> SignatureRecord:
            return cls(
                qualname=data["qualname"],
                parameters=tuple(
                    ParameterSpec.parse(p) for p in data.get("parameters", [])
                ),
                return_annotation=inspect.Parameter.empty,
                is_overloaded=data.get("is_overloaded", False),
                overload_index=data.get("overload_index", 0),
            )


# ---------------------------------------------------------------------------
# Guarded imports – JuGeo judgment terms
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (  # type: ignore[import]
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Judgment,
        JudgmentStatus,
        Obstruction,
        Proposition,
        PropositionKind,
        Provenance,
        ProvenanceSource,
        ResidualObligation,
        TrustAnnotation,
        TrustLevel,
    )
except ImportError:
    _log.debug("judgment_terms unavailable – using minimal stubs")

    class JudgmentStatus:  # type: ignore[no-redef]
        PROPOSED = "PROPOSED"
        CHALLENGED = "CHALLENGED"
        SETTLED = "SETTLED"
        OBSTRUCTED = "OBSTRUCTED"

    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind:  # type: ignore[no-redef]
        STRUCTURAL = "STRUCTURAL"
        BEHAVIORAL = "BEHAVIORAL"
        RELATIONAL = "RELATIONAL"
        RESOURCE = "RESOURCE"
        SEMANTIC = "SEMANTIC"

    class EvidenceItemKind:  # type: ignore[no-redef]
        SOLVER_PROOF = "SOLVER_PROOF"
        RUNTIME_WITNESS = "RUNTIME_WITNESS"
        ORACLE_PROPOSAL = "ORACLE_PROPOSAL"
        FORMAL_PROOF = "FORMAL_PROOF"

    class ProvenanceSource:  # type: ignore[no-redef]
        SOLVER = "SOLVER"
        RUNTIME = "RUNTIME"
        ORACLE = "ORACLE"
        HUMAN = "HUMAN"
        COMPOSED = "COMPOSED"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: Any
        formula: str
        free_variables: tuple[str, ...]
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str
        parameters: tuple[str, ...]
        is_dependent: bool
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any
        payload: dict[str, Any]
        trust_level: Any
        channel: str
        timestamp: str
        expiry: str
        provenance: tuple[str, ...]

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[EvidenceItem, ...]

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        obligation_id: str
        description: str
        required_evidence_kind: Any
        deadline: str
        priority: int
        depends_on: tuple[str, ...]
        is_discharged: bool
        discharge_evidence: str

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        obstruction_id: str
        violated_condition: str
        coordinate: Any
        cohomology_class: str
        is_resolved: bool
        resolution_evidence: str

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: Any
        evidence_basis: tuple[str, ...]
        ceiling: Any
        floor: Any
        reasons: tuple[str, ...]

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        source: Any
        parent_judgments: tuple[str, ...]
        creation_timestamp: str
        transformation_history: tuple[str, ...]
        metadata: dict[str, Any]

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any
        proposition: Any
        carrier: Any
        evidence: Any
        obligations: tuple[Any, ...]
        obstructions: tuple[Any, ...]
        trust: Any
        provenance: Any
        clauses: tuple[Any, ...]
        status: Any


# ---------------------------------------------------------------------------
# Guarded imports – geometry (CoordinateObject / CoordinateKind)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateKind, CoordinateObject  # type: ignore[import]
except ImportError:
    _log.debug("geometry.site unavailable – using coordinate stubs")

    class CoordinateKind:  # type: ignore[no-redef]
        MODULE = "MODULE"
        FUNCTION = "FUNCTION"
        INTERFACE = "INTERFACE"
        TEST = "TEST"
        THEOREM = "THEOREM"
        REGION = "REGION"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        components: tuple[str, ...]
        kind: Any
        support_labels: frozenset[str]
        metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_EMPTY = inspect.Parameter.empty

_PARAM_KIND_MAP: dict[Any, str] = {
    inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
    inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
    inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD,
}

# Known conflicting decorator combinations: (a, b) means a + b is problematic.
_DECORATOR_CONFLICTS: list[tuple[str, str, str]] = [
    ("staticmethod", "classmethod", "staticmethod and classmethod cannot coexist"),
    (
        "property",
        "functools.lru_cache",
        "property + lru_cache: use functools.cached_property instead",
    ),
    (
        "classmethod",
        "functools.lru_cache",
        "classmethod + lru_cache: cache is not per-class in Python <3.9",
    ),
    (
        "abstractmethod",
        "staticmethod",
        "abstractmethod + staticmethod: @abstractmethod must be the innermost decorator",
    ),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _inspect_param(p: inspect.Parameter) -> ParameterSpec:
    """Convert an :class:`inspect.Parameter` to a :class:`ParameterSpec`.

    Parameters:
        p: The inspect.Parameter to convert.

    Returns:
        A ParameterSpec with all fields populated from *p*.
    """
    kind_str = _PARAM_KIND_MAP.get(p.kind, str(p.kind))
    return ParameterSpec(
        name=p.name,
        kind=kind_str,
        annotation=p.annotation,
        has_default=(p.default is not _EMPTY),
        default_value=p.default,
    )


def _make_coordinate(surface: CallableSurface) -> CoordinateObject:
    """Build a :class:`CoordinateObject` from a :class:`CallableSurface`.

    Parameters:
        surface: Surface to locate in the JuGeo coordinate space.

    Returns:
        A CoordinateObject rooted at the module and traversing the qualname
        component hierarchy.
    """
    parts = tuple(
        c
        for c in (surface.module,) + tuple(surface.qualname.split("."))
        if c
    )
    return CoordinateObject(
        components=parts,
        kind=CoordinateKind.FUNCTION,
        support_labels=frozenset({"callable", "surface", "analysis"}),
        metadata={
            "qualname": surface.qualname,
            "module": surface.module,
            "lineno": surface.lineno,
        },
    )


# ---------------------------------------------------------------------------
# CallableSurfaceAnalyzer – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallableSurfaceAnalyzer:
    """Analyzes Python callables and produces :class:`CallableSurface` descriptors.

    This is the primary entry point for callable surface analysis.  It supports
    single-callable analysis, batch analysis of many callables, and call graph
    construction via bytecode inspection.  Results are cached internally and can
    be used to produce :class:`~jugeo.judgments.judgment_terms.Judgment` records
    for the JuGeo trust system.

    Provides copilot integration points: the surface index and judgment builder
    are consumed by downstream copilot-assisted verification workflows.

    Attributes:
        _surfaces: Cache mapping qualname to analyzed :class:`CallableSurface`.
        _call_graph: Adjacency list mapping caller qualname → set of callee names.
        _errors: Accumulated non-fatal error messages from analysis runs.
    """

    _surfaces: dict[str, CallableSurface] = field(default_factory=dict)
    _call_graph: dict[str, set[str]] = field(default_factory=dict)
    _errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Single-callable analysis
    # ------------------------------------------------------------------

    def analyze(self, func: Any) -> CallableSurface:
        """Analyze a single callable and return its :class:`CallableSurface`.

        Introspects *func* using :mod:`inspect`, extracting its parameter list,
        return annotation, async/generator flags, source location, and closure
        variables.  The result is cached by qualname.

        Parameters:
            func: Any Python callable (function, lambda, class, bound method,
                callable instance, or builtin).

        Returns:
            A frozen :class:`CallableSurface` describing the callable's public
            surface.

        Raises:
            ValueError: If *func* is not callable.
        """
        if not callable(func):
            raise ValueError(f"Expected a callable, got {type(func)!r}")

        name: str = getattr(func, "__name__", repr(func))
        qualname: str = getattr(func, "__qualname__", name)

        if qualname in self._surfaces:
            _log.debug("analyze: cache hit for %r", qualname)
            return self._surfaces[qualname]

        is_async = inspect.iscoroutinefunction(func)
        is_gen = inspect.isgeneratorfunction(func)
        is_coro = inspect.iscoroutine(func)
        module: str = getattr(func, "__module__", "") or ""
        docstring: str | None = inspect.getdoc(func)

        try:
            src_file: str | None = inspect.getfile(func)
        except (TypeError, OSError):
            src_file = None

        lineno: int | None = None
        try:
            _, start = inspect.getsourcelines(func)
            lineno = start
        except (TypeError, OSError):
            pass

        params: list[ParameterSpec] = []
        return_annotation: Any = _EMPTY
        try:
            sig = inspect.signature(func, follow_wrapped=False)
            for p in sig.parameters.values():
                params.append(_inspect_param(p))
            return_annotation = sig.return_annotation
        except (ValueError, TypeError) as exc:
            msg = f"analyze: failed to get signature for {qualname!r}: {exc}"
            _log.warning(msg)
            self._errors.append(msg)

        # Collect closure variable names from __code__.co_freevars
        closure_vars: tuple[str, ...] = ()
        raw = func
        while hasattr(raw, "__wrapped__"):
            raw = raw.__wrapped__
        code = getattr(raw, "__code__", None)
        if code is not None and code.co_freevars:
            closure_vars = tuple(code.co_freevars)

        # Infer decorator wrapper names by walking the __wrapped__ chain
        decorators: list[str] = []
        wrapper = func
        while hasattr(wrapper, "__wrapped__"):
            outer_name = getattr(wrapper, "__name__", "")
            wrapper = wrapper.__wrapped__
            inner_name = getattr(wrapper, "__name__", "")
            if outer_name and outer_name != inner_name and outer_name not in decorators:
                decorators.append(outer_name)
        if hasattr(func, "cache_info") and callable(getattr(func, "cache_info", None)):
            if "functools.lru_cache" not in decorators:
                decorators.insert(0, "functools.lru_cache")

        surface = CallableSurface(
            name=name,
            qualname=qualname,
            parameters=tuple(params),
            return_annotation=return_annotation,
            is_async=is_async,
            is_generator=is_gen,
            is_coroutine=is_coro,
            module=module,
            docstring=docstring,
            source_file=src_file,
            lineno=lineno,
            closure_vars=closure_vars,
            decorators=tuple(decorators),
        )
        self._surfaces[qualname] = surface
        _log.debug("analyze: stored surface for %r (%d param(s))", qualname, len(params))
        return surface

    # ------------------------------------------------------------------
    # Batch analysis
    # ------------------------------------------------------------------

    def batch_analyze(self, funcs: list[Any]) -> dict[str, CallableSurface]:
        """Analyze many callables and return a dict keyed by qualname.

        Each callable is passed through :meth:`analyze`.  Failures are
        recorded in ``_errors`` and silently excluded from the result so
        that one bad callable does not abort the whole batch.

        Parameters:
            funcs: List of callables to analyze.

        Returns:
            Mapping from qualname to :class:`CallableSurface` for each
            successfully analyzed callable.
        """
        result: dict[str, CallableSurface] = {}
        for func in funcs:
            try:
                surface = self.analyze(func)
                result[surface.qualname] = surface
            except Exception as exc:
                msg = f"batch_analyze: skipping {func!r}: {exc}"
                _log.warning(msg)
                self._errors.append(msg)
        _log.info(
            "batch_analyze: %d/%d callables succeeded", len(result), len(funcs)
        )
        return result

    # ------------------------------------------------------------------
    # Compatibility filtering
    # ------------------------------------------------------------------

    def find_compatible_callables(
        self,
        target: CallableSurface,
        candidates: list[Any],
    ) -> list[CallableSurface]:
        """Filter *candidates* by arity and return-type compatibility with *target*.

        A candidate is compatible when:

        * Its required positional parameter count does not exceed that of *target*.
        * It does not accept more positional parameters than *target* (unless
          *target* accepts ``*args``).
        * Where both surfaces have explicit return annotations they agree.

        Parameters:
            target: The :class:`CallableSurface` to match against.
            candidates: Callables to evaluate.

        Returns:
            Sorted list (by qualname) of compatible :class:`CallableSurface` objects.
        """
        target_required = sum(
            1
            for p in target.parameters
            if not p.has_default
            and p.kind
            not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
        )
        target_pos = sum(
            1
            for p in target.parameters
            if p.kind
            in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
        )
        target_has_var_pos = any(
            p.kind == ParameterKind.VAR_POSITIONAL for p in target.parameters
        )

        compatible: list[CallableSurface] = []
        analyzed = self.batch_analyze(candidates)

        for surface in analyzed.values():
            cand_required = sum(
                1
                for p in surface.parameters
                if not p.has_default
                and p.kind
                not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
            )
            cand_pos = sum(
                1
                for p in surface.parameters
                if p.kind
                in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
            )
            cand_has_var_pos = any(
                p.kind == ParameterKind.VAR_POSITIONAL for p in surface.parameters
            )

            # Required parameter count must not exceed target's
            if cand_required > target_required:
                continue
            # Positional capacity: candidate must not consume more positional
            # slots than target unless someone accepts *args
            if (
                not target_has_var_pos
                and not cand_has_var_pos
                and cand_pos > target_pos
            ):
                continue
            # Return annotation must agree when both are explicit
            if (
                target.return_annotation is not _EMPTY
                and surface.return_annotation is not _EMPTY
                and target.return_annotation != surface.return_annotation
            ):
                continue

            compatible.append(surface)

        compatible.sort(key=lambda s: s.qualname)
        return compatible

    # ------------------------------------------------------------------
    # Call graph construction
    # ------------------------------------------------------------------

    def compute_call_graph(self, module: Any) -> dict[str, set[str]]:
        """Build a call graph for all callables in *module* via bytecode analysis.

        Disassembles each function's bytecode with :func:`dis.get_instructions`
        and collects ``LOAD_GLOBAL``, ``LOAD_NAME``, and attribute-load
        instructions that appear just before ``CALL``-family opcodes.

        Parameters:
            module: A Python module object whose callable members will be
                analyzed.

        Returns:
            Adjacency dict mapping caller qualname → set of callee name strings.
        """
        graph: dict[str, set[str]] = {}
        members = inspect.getmembers(module, predicate=callable)
        for attr_name, obj in members:
            if inspect.isbuiltin(obj):
                continue
            qualname = getattr(obj, "__qualname__", attr_name)
            callees: set[str] = set()
            code = getattr(obj, "__code__", None)
            if code is None:
                continue
            try:
                instructions = list(dis.get_instructions(code))
            except Exception as exc:
                _log.debug("compute_call_graph: cannot disassemble %r: %s", qualname, exc)
                continue

            for idx, instr in enumerate(instructions):
                if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF"):
                    callee_name: str = str(instr.argval)
                    lookahead = instructions[idx + 1 : idx + 6]
                    if any(i.opname.startswith("CALL") for i in lookahead):
                        callees.add(callee_name)
                elif instr.opname == "LOAD_ATTR":
                    attr = str(instr.argval)
                    lookahead = instructions[idx + 1 : idx + 5]
                    if any(i.opname.startswith("CALL") for i in lookahead):
                        callees.add(attr)
            graph[qualname] = callees

        self._call_graph.update(graph)
        _log.debug("compute_call_graph: built graph with %d nodes", len(graph))
        return graph

    # ------------------------------------------------------------------
    # Overload detection
    # ------------------------------------------------------------------

    def detect_overloads(
        self, cls: type, method_name: str
    ) -> list[CallableSurface]:
        """Find all distinct overload implementations of *method_name* in *cls* MRO.

        Walks the MRO and deduplicates by function identity so that re-exported
        aliases are counted only once.

        Parameters:
            cls: The class whose MRO to traverse.
            method_name: Name of the method to search for.

        Returns:
            List of :class:`CallableSurface` objects, one per distinct
            implementation found across the MRO.

        Raises:
            TypeError: If *cls* is not a class.
        """
        if not isinstance(cls, type):
            raise TypeError(f"detect_overloads: expected a class, got {type(cls)!r}")

        seen_ids: set[int] = set()
        overloads: list[CallableSurface] = []

        for base in cls.__mro__:
            raw = base.__dict__.get(method_name)
            if raw is None:
                continue
            # Unwrap staticmethod / classmethod descriptor wrappers
            if isinstance(raw, staticmethod):
                raw = raw.__func__
            elif isinstance(raw, classmethod):
                raw = raw.__func__

            fn_id = id(raw)
            if fn_id in seen_ids:
                continue
            seen_ids.add(fn_id)

            if callable(raw):
                try:
                    overloads.append(self.analyze(raw))
                except Exception as exc:
                    _log.warning(
                        "detect_overloads: skipping %r.%s in %s – %s",
                        base.__name__,
                        method_name,
                        base.__qualname__,
                        exc,
                    )
        return overloads

    # ------------------------------------------------------------------
    # Dynamic callable discovery
    # ------------------------------------------------------------------

    def find_dynamic_callables(self, obj: Any) -> list[str]:
        """Find ``__call__`` and other dynamic-dispatch entry points on *obj*.

        Checks the object and its type's MRO for ``__call__``, ``__getattr__``,
        ``__missing__``, and similar hooks that make an object callable in
        non-obvious ways.  Metaclass ``__call__`` overrides are also reported.

        Parameters:
            obj: Any Python object to inspect.

        Returns:
            Ordered, deduplicated list of attribute names (e.g. ``'__call__'``,
            ``'__getattr__'``) that indicate dynamic dispatch capabilities.
        """
        dynamic_markers = [
            "__call__",
            "__getattr__",
            "__getattribute__",
            "__missing__",
            "__class_getitem__",
        ]
        found: list[str] = []
        obj_type = type(obj)

        for marker in dynamic_markers:
            # Check instance __dict__ first (only for __call__ and similar)
            inst_dict = getattr(obj, "__dict__", {}) or {}
            if marker in inst_dict:
                found.append(marker)
                continue
            # Walk MRO for class-level definition, excluding trivial object slot
            for base in obj_type.__mro__:
                if marker in base.__dict__:
                    if base is not object or marker == "__call__":
                        found.append(marker)
                    break

        # Report metaclass __call__ override (class factories, etc.)
        meta = type(obj_type)
        if meta is not type and "__call__" in meta.__dict__:
            found.append(f"metaclass::{meta.__name__}.__call__")

        # Deduplicate while preserving insertion order
        return list(dict.fromkeys(found))

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def build_surface_index(
        self, surfaces: list[CallableSurface]
    ) -> dict[str, CallableSurface]:
        """Index *surfaces* by qualname for O(1) lookup.

        In the event of duplicate qualnames the last occurrence wins and a
        warning is emitted.

        Parameters:
            surfaces: List of :class:`CallableSurface` objects to index.

        Returns:
            Dict mapping qualname → :class:`CallableSurface`.
        """
        index: dict[str, CallableSurface] = {}
        for surface in surfaces:
            if surface.qualname in index:
                _log.warning(
                    "build_surface_index: duplicate qualname %r – replacing",
                    surface.qualname,
                )
            index[surface.qualname] = surface
        self._surfaces.update(index)
        _log.debug("build_surface_index: indexed %d surface(s)", len(index))
        return index

    # ------------------------------------------------------------------
    # Judgment emission
    # ------------------------------------------------------------------

    def build_surface_judgment(self, surface: CallableSurface) -> Judgment:
        """Build a JuGeo :class:`Judgment` asserting the structural properties of *surface*.

        The resulting Judgment captures:

        * That the callable exists and has been introspected (RUNTIME_WITNESSED).
        * Its arity and parameter kinds as a structural proposition.
        * A PROPOSED status since no solver discharge has occurred yet.

        Parameters:
            surface: The :class:`CallableSurface` to produce a Judgment for.

        Returns:
            A :class:`Judgment` with ``PROPOSED`` status and
            ``RUNTIME_WITNESSED`` trust level.
        """
        coord = _make_coordinate(surface)
        param_names = tuple(p.name for p in surface.parameters)

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"callable_surface({surface.qualname!r}, "
                f"arity={len(surface.parameters)}, "
                f"async={surface.is_async}, generator={surface.is_generator})"
            ),
            free_variables=param_names,
            metadata={
                "source_file": surface.source_file,
                "module": surface.module,
                "lineno": surface.lineno,
                "decorators": list(surface.decorators),
            },
        )
        carrier = Carrier(
            name="CallableSurface",
            parameters=param_names,
            is_dependent=len(param_names) > 0,
            metadata={"qualname": surface.qualname, "module": surface.module},
        )
        ev_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={
                "qualname": surface.qualname,
                "param_count": len(surface.parameters),
                "is_async": surface.is_async,
                "module": surface.module,
            },
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel="callable_surface_analyzer",
            timestamp=str(time.time()),
            expiry="",
            provenance=(),
        )
        evidence = EvidenceBundle(items=(ev_item,))
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            evidence_basis=("runtime_inspect",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"callable surface analyzed via inspect for {surface.qualname!r}",),
        )
        provenance = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=str(time.time()),
            transformation_history=(),
            metadata={"analyzer": "CallableSurfaceAnalyzer"},
        )
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=JudgmentStatus.PROPOSED,
        )


# ---------------------------------------------------------------------------
# MethodResolutionAlgorithm – frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MethodResolutionAlgorithm:
    """Full Python attribute lookup algorithm with descriptor protocol support.

    Implements the Python data model attribute resolution rules including
    data-descriptor priority, instance ``__dict__`` lookup, and non-data
    descriptor fallback (PEP 3135 / reference implementation).

    Optionally records a human-readable trace of every lookup step for
    debugging and copilot-assisted explanation.

    Attributes:
        trace_enabled: When ``True`` each resolution step is logged at DEBUG
            level and returned by :meth:`trace_resolution`.
    """

    trace_enabled: bool = False

    def resolve(self, obj: Any, name: str) -> Any:
        """Full attribute resolution following the Python descriptor protocol.

        Resolution order:

        1. Data descriptors (``__get__`` + ``__set__``) from ``type(obj).__mro__``.
        2. Instance ``__dict__``.
        3. Non-data descriptors and plain class variables from the MRO.

        Parameters:
            obj: The object on which to resolve the attribute.
            name: Attribute name to look up.

        Returns:
            The resolved attribute value.

        Raises:
            AttributeError: If *name* is not found anywhere in the lookup chain.
        """
        obj_type = type(obj)
        # Phase 1: data descriptor in MRO?
        for base in obj_type.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None and hasattr(raw, "__get__") and hasattr(raw, "__set__"):
                if self.trace_enabled:
                    _log.debug("resolve: data descriptor %r in %r", name, base.__name__)
                return raw.__get__(obj, obj_type)

        # Phase 2: instance __dict__
        inst_dict: dict[str, Any] = {}
        try:
            inst_dict = object.__getattribute__(obj, "__dict__")
        except AttributeError:
            pass
        if name in inst_dict:
            if self.trace_enabled:
                _log.debug("resolve: found %r in instance __dict__", name)
            return inst_dict[name]

        # Phase 3: non-data descriptor or plain class variable
        for base in obj_type.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None:
                if hasattr(raw, "__get__"):
                    if self.trace_enabled:
                        _log.debug(
                            "resolve: non-data descriptor %r in %r", name, base.__name__
                        )
                    return raw.__get__(obj, obj_type)
                return raw

        raise AttributeError(
            f"{type(obj).__name__!r} object has no attribute {name!r}"
        )

    def resolve_special(self, cls: type, name: str) -> Any | None:
        """Resolve a special (dunder) method through the *type* of *cls*.

        Special methods are always looked up on the type (i.e. the metaclass),
        never the class instance, per the Python data model.

        Parameters:
            cls: The class whose special method to resolve.
            name: Dunder name (e.g. ``'__len__'``, ``'__iter__'``).

        Returns:
            The method descriptor if found, else ``None``.
        """
        meta = type(cls)
        for base in meta.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None:
                return raw
        return None

    def resolve_dunder(self, cls: type, name: str) -> Any | None:
        """Resolve a dunder method by walking *cls*'s own MRO.

        Unlike :meth:`resolve_special` (which searches the metaclass), this
        walks the class's own ``__mro__``, consistent with how Python resolves
        most dunder methods for instances.

        Parameters:
            cls: The class to search.
            name: Dunder attribute name.

        Returns:
            The attribute from the first MRO class that defines it, or ``None``.
        """
        for base in cls.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None:
                return raw
        return None

    def simulate_getattr(self, obj: Any, name: str) -> Any:
        """Simulate ``object.__getattribute__`` semantics without side effects.

        Re-implements CPython's attribute lookup so callers can reason about
        each step without triggering ``__getattribute__`` overrides.  Falls
        back to ``__getattr__`` (if defined) only when the primary lookup
        raises ``AttributeError``.

        Parameters:
            obj: Object to look up the attribute on.
            name: Attribute name.

        Returns:
            The resolved attribute value.

        Raises:
            AttributeError: If neither the descriptor protocol nor
                ``__getattr__`` provides a binding.
        """
        try:
            return self.resolve(obj, name)
        except AttributeError:
            obj_type = type(obj)
            getattr_hook = self.resolve_dunder(obj_type, "__getattr__")
            if getattr_hook is not None:
                return getattr_hook(obj, name)
            raise

    def simulate_setattr(self, obj: Any, name: str, value: Any) -> None:
        """Simulate ``object.__setattr__`` semantics.

        Checks for a data descriptor (``__set__``) on the type's MRO first;
        otherwise writes directly to the instance ``__dict__``.

        Parameters:
            obj: Object whose attribute to set.
            name: Attribute name.
            value: Value to assign.

        Raises:
            AttributeError: If the object has no ``__dict__`` and no descriptor
                handles the assignment.
        """
        obj_type = type(obj)
        for base in obj_type.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None and hasattr(raw, "__set__"):
                raw.__set__(obj, value)
                return

        inst_dict = getattr(obj, "__dict__", None)
        if inst_dict is None:
            raise AttributeError(
                f"'{type(obj).__name__}' object has no '__dict__' "
                f"and no data descriptor for {name!r}"
            )
        inst_dict[name] = value

    def simulate_delattr(self, obj: Any, name: str) -> None:
        """Simulate ``object.__delattr__`` semantics.

        Checks for a data descriptor with ``__delete__``; otherwise removes
        the key from the instance ``__dict__``.

        Parameters:
            obj: Object whose attribute to delete.
            name: Attribute name.

        Raises:
            AttributeError: If no descriptor handles deletion and *name* is
                absent from the instance ``__dict__``.
        """
        obj_type = type(obj)
        for base in obj_type.__mro__:
            raw = base.__dict__.get(name)
            if raw is not None and hasattr(raw, "__delete__"):
                raw.__delete__(obj)
                return

        inst_dict = getattr(obj, "__dict__", {}) or {}
        if name not in inst_dict:
            raise AttributeError(
                f"'{type(obj).__name__}' object has no attribute {name!r}"
            )
        del inst_dict[name]

    def trace_resolution(self, obj: Any, name: str) -> list[str]:
        """Return an ordered trace of all lookup steps for *name* on *obj*.

        Produces a human-readable log of every MRO base checked, whether a
        data descriptor was found, whether the instance dict was consulted,
        and how the final value was obtained.

        Parameters:
            obj: Object to trace the lookup on.
            name: Attribute name.

        Returns:
            Ordered list of step description strings suitable for display or
            logging.
        """
        trace: list[str] = []
        obj_type = type(obj)
        mro_names = [b.__name__ for b in obj_type.__mro__]
        trace.append(f"begin resolve({type(obj).__name__!r}, {name!r})")
        trace.append(f"MRO: {mro_names!r}")

        found_data_desc = False
        for base in obj_type.__mro__:
            raw = base.__dict__.get(name)
            if raw is None:
                trace.append(f"  {base.__name__}.__dict__: absent")
                continue
            type_label = type(raw).__name__
            trace.append(f"  {base.__name__}.__dict__: found {type_label!r}")
            if hasattr(raw, "__get__") and hasattr(raw, "__set__"):
                trace.append(f"  → data descriptor resolved in {base.__name__!r}")
                found_data_desc = True
                break

        if not found_data_desc:
            inst_dict = getattr(obj, "__dict__", {}) or {}
            if name in inst_dict:
                trace.append("  instance.__dict__: FOUND — returned directly")
            else:
                trace.append("  instance.__dict__: absent")
                resolved_in: str | None = None
                for base in obj_type.__mro__:
                    raw = base.__dict__.get(name)
                    if raw is not None:
                        kind = "non-data descriptor" if hasattr(raw, "__get__") else "class var"
                        trace.append(f"  → {kind} resolved in {base.__name__!r}")
                        resolved_in = base.__name__
                        break
                if resolved_in is None:
                    trace.append(f"  → AttributeError: {name!r} not found anywhere in MRO")

        trace.append(f"end resolve({name!r})")
        return trace


# ---------------------------------------------------------------------------
# CallCompatibilityChecker – frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallCompatibilityChecker:
    """Checks whether a call site is compatible with a :class:`CallableSurface`.

    Implements arity checking, positional argument matching, keyword argument
    matching, and ``*args`` / ``**kwargs`` validation.  In strict mode every
    unexpected keyword argument is also reported; in lenient mode only missing
    required arguments are flagged.

    Attributes:
        strict: When ``True`` unexpected keyword arguments are reported as
            errors; when ``False`` only missing required args matter.
    """

    strict: bool = True

    def check(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> bool:
        """Return ``True`` iff *args* and *kwargs* are compatible with *surface*.

        Parameters:
            surface: The callable surface to check against.
            args: Positional arguments that would be passed at the call site.
            kwargs: Keyword arguments that would be passed at the call site.

        Returns:
            ``True`` if no compatibility errors were found.
        """
        errors: list[str] = []
        errors.extend(self.check_positional(surface, args))
        errors.extend(self.check_keyword(surface, kwargs))
        errors.extend(self.check_var_args(surface, args, kwargs))
        errors.extend(self.infer_missing_args(surface, args, kwargs))
        if errors:
            _log.debug(
                "check: %d error(s) for %r: %s", len(errors), surface.qualname, errors[0]
            )
        return len(errors) == 0

    def check_positional(
        self, surface: CallableSurface, args: tuple[Any, ...]
    ) -> list[str]:
        """Verify the positional argument count is within bounds.

        Parameters:
            surface: The callable surface.
            args: Positional arguments passed at the call site.

        Returns:
            List of error strings; empty if positional args are valid.
        """
        errors: list[str] = []
        positional_params = [
            p
            for p in surface.parameters
            if p.kind
            in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
        ]
        has_var_pos = any(
            p.kind == ParameterKind.VAR_POSITIONAL for p in surface.parameters
        )
        max_pos = len(positional_params)
        min_pos = sum(1 for p in positional_params if not p.has_default)

        if not has_var_pos and len(args) > max_pos:
            errors.append(
                f"{surface.qualname}() takes at most {max_pos} positional argument(s) "
                f"({len(args)} given)"
            )
        if len(args) < min_pos:
            errors.append(
                f"{surface.qualname}() requires at least {min_pos} positional "
                f"argument(s) ({len(args)} given)"
            )
        return errors

    def check_keyword(
        self, surface: CallableSurface, kwargs: dict[str, Any]
    ) -> list[str]:
        """Verify that keyword arguments map to known parameters.

        If the surface accepts ``**kwargs`` this check is skipped since
        arbitrary keyword arguments are valid.

        Parameters:
            surface: The callable surface.
            kwargs: Keyword arguments passed at the call site.

        Returns:
            List of error strings.
        """
        errors: list[str] = []
        has_var_kw = any(
            p.kind == ParameterKind.VAR_KEYWORD for p in surface.parameters
        )
        if has_var_kw:
            return errors

        known = {
            p.name
            for p in surface.parameters
            if p.kind
            in (ParameterKind.POSITIONAL_OR_KEYWORD, ParameterKind.KEYWORD_ONLY)
        }
        for kw in kwargs:
            if kw not in known:
                errors.append(
                    f"{surface.qualname}() got unexpected keyword argument {kw!r}"
                )
                if not self.strict:
                    break
        return errors

    def check_var_args(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> list[str]:
        """Check ``*args`` / ``**kwargs`` conventions and positional-only constraints.

        Ensures that positional-only parameters are not supplied as keywords,
        and that keyword-only parameters without defaults are eventually
        provided.

        Parameters:
            surface: The callable surface.
            args: Positional arguments.
            kwargs: Keyword arguments.

        Returns:
            List of error strings.
        """
        errors: list[str] = []
        for p in surface.parameters:
            if p.kind == ParameterKind.POSITIONAL_ONLY and p.name in kwargs:
                errors.append(
                    f"{surface.qualname}(): parameter {p.name!r} is positional-only "
                    "and cannot be passed as a keyword argument"
                )

        kw_only_required = [
            p.name
            for p in surface.parameters
            if p.kind == ParameterKind.KEYWORD_ONLY and not p.has_default
        ]
        for name in kw_only_required:
            if name not in kwargs:
                errors.append(
                    f"{surface.qualname}(): keyword-only parameter {name!r} is "
                    "required but was not provided"
                )
        return errors

    def infer_missing_args(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> list[str]:
        """Identify required parameters not satisfied by *args* or *kwargs*.

        Parameters:
            surface: The callable surface.
            args: Positional arguments.
            kwargs: Keyword arguments.

        Returns:
            List of error strings naming each missing required parameter.
        """
        errors: list[str] = []
        positional_params = [
            p
            for p in surface.parameters
            if p.kind
            in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
        ]
        satisfied_positionally: set[str] = {
            p.name for i, p in enumerate(positional_params) if i < len(args)
        }
        for param in surface.parameters:
            if param.kind in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD):
                continue
            if param.has_default:
                continue
            if param.name in satisfied_positionally:
                continue
            if param.name in kwargs:
                continue
            errors.append(
                f"{surface.qualname}(): required parameter {param.name!r} is missing"
            )
        return errors

    def build_compatibility_judgment(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Judgment:
        """Build a :class:`Judgment` reflecting call compatibility with *surface*.

        Parameters:
            surface: The target callable surface.
            args: Positional arguments.
            kwargs: Keyword arguments.

        Returns:
            A :class:`Judgment` with ``SETTLED`` status when compatible and
            ``OBSTRUCTED`` status when compatibility errors exist.
        """
        errors: list[str] = []
        errors.extend(self.check_positional(surface, args))
        errors.extend(self.check_keyword(surface, kwargs))
        errors.extend(self.check_var_args(surface, args, kwargs))
        errors.extend(self.infer_missing_args(surface, args, kwargs))

        compatible = len(errors) == 0
        trust_level = (
            TrustLevel.RUNTIME_WITNESSED if compatible else TrustLevel.CONTRADICTED
        )
        status = (
            JudgmentStatus.SETTLED if compatible else JudgmentStatus.OBSTRUCTED
        )

        coord = _make_coordinate(surface)
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=(
                f"call_compatible({surface.qualname!r}, "
                f"nargs={len(args)}, nkwargs={len(kwargs)})"
            ),
            free_variables=tuple(kwargs.keys()),
            metadata={"errors": errors, "compatible": compatible},
        )
        carrier = Carrier(
            name="CallCompatibility",
            parameters=(surface.qualname,),
            is_dependent=True,
            metadata={"errors": errors, "strict": self.strict},
        )
        ev_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"errors": errors, "nargs": len(args), "nkwargs": len(kwargs)},
            trust_level=trust_level,
            channel="call_compatibility_checker",
            timestamp=str(time.time()),
            expiry="",
            provenance=(),
        )
        evidence = EvidenceBundle(items=(ev_item,))
        trust = TrustAnnotation(
            level=trust_level,
            evidence_basis=("arity_check", "keyword_check", "var_args_check"),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.CONTRADICTED,
            reasons=tuple(errors) if errors else ("all call arguments are compatible",),
        )
        provenance = Provenance(
            source=ProvenanceSource.RUNTIME,
            parent_judgments=(),
            creation_timestamp=str(time.time()),
            transformation_history=(),
            metadata={"checker": "CallCompatibilityChecker", "strict": self.strict},
        )
        obstructions: tuple[Any, ...] = tuple(
            Obstruction(
                obstruction_id=uuid.uuid4().hex[:8],
                violated_condition=err,
                coordinate=coord,
                cohomology_class="arity_mismatch",
                is_resolved=False,
                resolution_evidence="",
            )
            for err in errors
        )
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            obligations=(),
            obstructions=obstructions,
            trust=trust,
            provenance=provenance,
            clauses=(),
            status=status,
        )


# ---------------------------------------------------------------------------
# InheritanceGraphAlgorithm – mutable dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InheritanceGraphAlgorithm:
    """Algorithms for traversing and analysing Python class inheritance graphs.

    Builds adjacency lists from class lists, performs topological sort (Kahn's
    algorithm), detects diamond inheritance, computes dominator sets, and finds
    method shadowing across an inheritance hierarchy.

    Attributes:
        _graph: Parent-qualname → list[child-qualname] adjacency list.
        _reverse: Child-qualname → list[parent-qualname] reverse adjacency list.
    """

    _graph: dict[str, list[str]] = field(default_factory=dict)
    _reverse: dict[str, list[str]] = field(default_factory=dict)

    def build_graph(self, classes: list[type]) -> dict[str, list[str]]:
        """Build a parent→children adjacency list from *classes*.

        Only classes present in *classes* are included as graph nodes; external
        bases (e.g. ``object``) are excluded.

        Parameters:
            classes: List of class objects to include in the graph.

        Returns:
            Dict mapping class qualname → list of direct child qualnames.
        """
        name_set = {cls.__qualname__ for cls in classes}
        graph: dict[str, list[str]] = {cls.__qualname__: [] for cls in classes}
        reverse: dict[str, list[str]] = {cls.__qualname__: [] for cls in classes}

        for cls in classes:
            child = cls.__qualname__
            for base in cls.__bases__:
                parent = base.__qualname__
                if parent not in name_set:
                    continue
                graph.setdefault(parent, []).append(child)
                reverse.setdefault(child, []).append(parent)

        self._graph.update(graph)
        self._reverse.update(reverse)
        _log.debug("build_graph: %d nodes, %d edges", len(graph), sum(len(v) for v in graph.values()))
        return graph

    def topological_sort(self, graph: dict[str, list[str]]) -> list[str]:
        """Return nodes in topological order using Kahn's algorithm.

        Parents always precede their children in the returned list.

        Parameters:
            graph: Parent→children adjacency list (as returned by
                :meth:`build_graph`).

        Returns:
            Nodes in topological order.

        Raises:
            ValueError: If *graph* contains a cycle.
        """
        in_degree: dict[str, int] = {node: 0 for node in graph}
        for node, children in graph.items():
            for child in children:
                in_degree.setdefault(child, 0)
                in_degree[child] += 1

        queue: deque[str] = deque(
            sorted(n for n, d in in_degree.items() if d == 0)
        )
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for child in sorted(graph.get(node, [])):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(in_degree):
            cycle_nodes = sorted(n for n, d in in_degree.items() if d > 0)
            raise ValueError(
                f"topological_sort: cycle detected involving {cycle_nodes!r}"
            )
        return result

    def find_diamonds(self, graph: dict[str, list[str]]) -> list[str]:
        """Find nodes reachable via more than one path from any root.

        A diamond occurs when a class has two or more parents that both (directly
        or indirectly) derive from a common ancestor, causing the ancestor to
        appear multiple times in the reachability BFS.

        Parameters:
            graph: Parent→children adjacency list.

        Returns:
            Sorted list of node names that participate in diamond inheritance.
        """
        # Roots = nodes with no incoming edges
        all_children: set[str] = {c for children in graph.values() for c in children}
        roots = [n for n in graph if n not in all_children]
        if not roots:
            roots = list(graph.keys())

        diamonds: list[str] = []
        for root in roots:
            visit_count: dict[str, int] = defaultdict(int)
            queue: deque[str] = deque([root])
            while queue:
                node = queue.popleft()
                for child in graph.get(node, []):
                    visit_count[child] += 1
                    queue.append(child)
            for node, count in visit_count.items():
                if count > 1 and node not in diamonds:
                    diamonds.append(node)
        return sorted(diamonds)

    def compute_dominance(
        self, root: str, graph: dict[str, list[str]]
    ) -> dict[str, set[str]]:
        """Compute the dominator set for each node reachable from *root*.

        A node *d* dominates node *n* if every path from *root* to *n* passes
        through *d*.  The dominator set of *n* is the set of all its dominators
        (including itself).

        Parameters:
            root: The entry node.
            graph: Parent→children adjacency list.

        Returns:
            Dict mapping each reachable node → its set of dominators
            (always includes the node itself).
        """
        # BFS to collect all reachable nodes
        all_nodes: set[str] = set()
        queue: deque[str] = deque([root])
        visited: set[str] = set()
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            all_nodes.add(n)
            for child in graph.get(n, []):
                queue.append(child)

        # Initialise dominator sets
        dominators: dict[str, set[str]] = {root: {root}}
        for n in all_nodes - {root}:
            dominators[n] = set(all_nodes)

        # Iterative data-flow convergence
        changed = True
        while changed:
            changed = False
            for n in all_nodes - {root}:
                predecessors = [p for p, children in graph.items() if n in children]
                if not predecessors:
                    continue
                new_dom = set(all_nodes)
                for pred in predecessors:
                    new_dom &= dominators.get(pred, set())
                new_dom.add(n)
                if new_dom != dominators[n]:
                    dominators[n] = new_dom
                    changed = True
        return dominators

    def find_method_shadows(
        self, classes: list[type], method_name: str
    ) -> list[tuple[str, str]]:
        """List (shadowing_class, shadowed_from) pairs for *method_name* overrides.

        A class *C* shadows a method from *B* if *C* defines *method_name* in
        its own ``__dict__`` and *B* is an ancestor of *C* that also defines it.

        Parameters:
            classes: Class objects to inspect.
            method_name: Method name to trace across the hierarchy.

        Returns:
            List of (shadowing_class_qualname, shadowed_from_qualname) tuples.
        """
        shadows: list[tuple[str, str]] = []
        for cls in classes:
            if method_name not in cls.__dict__:
                continue
            for base in cls.__mro__[1:]:
                if method_name in base.__dict__ and base is not object:
                    shadows.append((cls.__qualname__, base.__qualname__))
                    break
        return shadows


# ---------------------------------------------------------------------------
# DecoratorAnalyzer – frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecoratorAnalyzer:
    """Analyses the decorator stack applied to a Python callable.

    Uses ``__wrapped__`` chains and type introspection to infer which decorators
    have been applied to a function, detects conflicting combinations, and
    computes the effective :class:`CallableSurface` after decoration is unwrapped.

    Attributes:
        known_decorators: Decorator names this analyzer recognises explicitly;
            used to distinguish well-known wrappers from user-defined ones.
    """

    known_decorators: tuple[str, ...] = (
        "staticmethod",
        "classmethod",
        "property",
        "functools.wraps",
        "functools.lru_cache",
        "functools.cache",
        "functools.cached_property",
        "abstractmethod",
        "override",
        "dataclass",
    )

    def analyze_decorators(self, func: Any) -> list[str]:
        """Infer the decorator names applied to *func*.

        Walks the ``__wrapped__`` chain and compares outer/inner names to
        detect wrapper functions.  Also checks for ``cache_info`` (lru_cache)
        and type-level indicators (``cached_property``).

        Parameters:
            func: Callable to analyse.

        Returns:
            List of decorator name strings in application order (outermost first).
        """
        found: list[str] = []
        current = func

        # Built-in descriptor wrappers
        if isinstance(func, staticmethod):
            found.append("staticmethod")
            current = func.__func__
        elif isinstance(func, classmethod):
            found.append("classmethod")
            current = func.__func__
        elif isinstance(func, property):
            found.append("property")
            return found

        # Walk __wrapped__ chain for functools.wraps-style stacks
        inner = current
        while hasattr(inner, "__wrapped__"):
            outer_name = getattr(inner, "__name__", "")
            inner = inner.__wrapped__
            inner_name = getattr(inner, "__name__", "")
            if outer_name and outer_name != inner_name and outer_name not in found:
                found.append(outer_name)

        # Detect functools.lru_cache / functools.cache by cache_info attribute
        if hasattr(current, "cache_info") and callable(getattr(current, "cache_info", None)):
            if "functools.lru_cache" not in found and "functools.cache" not in found:
                found.append("functools.lru_cache")

        # Detect functools.cached_property by type name
        if type(func).__name__ == "cached_property":
            found.append("functools.cached_property")

        return found

    def order_decorators(self, decorators: list[str]) -> list[str]:
        """Return *decorators* sorted by canonical application order.

        Lower priority numbers = outermost (applied last, called first).
        Unknown decorators are appended at the end in their original order.

        Parameters:
            decorators: List of decorator name strings (as returned by
                :meth:`analyze_decorators`).

        Returns:
            Reordered list with known decorators in canonical position.
        """
        priority: dict[str, int] = {
            "staticmethod": 0,
            "classmethod": 1,
            "abstractmethod": 2,
            "property": 3,
            "override": 4,
            "dataclass": 5,
            "functools.cached_property": 6,
            "functools.lru_cache": 7,
            "functools.cache": 8,
            "functools.wraps": 9,
        }
        known = sorted(
            (d for d in decorators if d in priority), key=lambda d: priority[d]
        )
        unknown = [d for d in decorators if d not in priority]
        return known + unknown

    def detect_decorator_conflicts(self, decorators: list[str]) -> list[str]:
        """Detect known conflicting decorator combinations.

        Parameters:
            decorators: List of decorator name strings.

        Returns:
            List of human-readable conflict descriptions; empty if no conflicts
            are detected.
        """
        conflicts: list[str] = []
        dset = set(decorators)
        for a, b, message in _DECORATOR_CONFLICTS:
            if a in dset and b in dset:
                conflicts.append(message)
        return conflicts

    def compute_effective_surface(
        self, func: Any, decorators: list[str]
    ) -> CallableSurface:
        """Compute the effective :class:`CallableSurface` after decoration.

        Unwraps the ``__wrapped__`` chain to reach the innermost callable, then
        analyses that callable to produce the canonical surface, and re-annotates
        it with the supplied *decorators* tuple.

        Parameters:
            func: The decorated callable.
            decorators: List of decorator names (from :meth:`analyze_decorators`).

        Returns:
            A :class:`CallableSurface` for the innermost callable with the
            *decorators* field populated.
        """
        innermost = func
        while hasattr(innermost, "__wrapped__"):
            innermost = innermost.__wrapped__
        if isinstance(innermost, (staticmethod, classmethod)):
            innermost = innermost.__func__

        analyzer = CallableSurfaceAnalyzer()
        try:
            base_surface = analyzer.analyze(innermost)
        except Exception:
            # Fallback: analyse the outer (possibly decorated) callable
            try:
                base_surface = analyzer.analyze(func)
            except Exception as exc:
                _log.warning("compute_effective_surface: analysis failed – %s", exc)
                # Return a minimal surface stub
                name = getattr(func, "__name__", repr(func))
                base_surface = CallableSurface(
                    name=name,
                    qualname=getattr(func, "__qualname__", name),
                    parameters=(),
                    return_annotation=_EMPTY,
                    module=getattr(func, "__module__", "") or "",
                )
        return replace(base_surface, decorators=tuple(decorators))


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CallCompatibilityChecker",
    "CallableSurfaceAnalyzer",
    "DecoratorAnalyzer",
    "InheritanceGraphAlgorithm",
    "MethodResolutionAlgorithm",
]

# copilot: shared-core marker for LLM-assisted callable surface orchestration.
