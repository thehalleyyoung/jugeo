from __future__ import annotations

"""Scope semantics: coordinate formation over locals, globals, and cells (theory2.tex Ch15).

In the JuGeo formal-semantics framework, every Python *scope* is modelled as a
*coordinate system* over the Grothendieck site described in theory2.tex Ch15.
The LEGB rule (Local → Enclosing → Global → Builtin) defines the canonical
*cover* of that site, and a name-resolution operation is the computation of a
*stalk* at a use-site coordinate.

**Scopes as formal coordinate systems (Ch15 §3)**

A scope is not merely a dictionary; it is a stratified object in the site.
Each stratum carries a coordinate label drawn from :class:`CoordinateKind`, and
morphisms between strata encode the precise relationship (restriction, inclusion,
transport, or refinement) that Python's scoping rules implement.

- ``LOCAL`` stratum   → names bound by assignment or ``def``/``class`` inside
  the current function body.
- ``ENCLOSING`` stratum → names captured from lexically surrounding function
  scopes; these create implicit *cell* references and therefore generate
  coordination obligations (mutable shared state across activation records).
- ``GLOBAL`` stratum  → names living in the module-level ``__dict__``; accessing
  them from an inner scope is a *transport* morphism.
- ``BUILTIN`` stratum → the terminal object of the site; always present as the
  fallback layer.

**Coordinate formation (Ch15 §4)**

This module implements *coordinate formation* — the process of constructing a
:class:`CoordinateObject` that precisely locates a name or scope in the site
hierarchy.  The three principal classes are:

1. :class:`ScopeSemanticsCoordinateFormationCoordinator` — low-level coordinate
   builder; caches coordinates and records obligations.
2. :class:`ScopeSemanticsCoordinateFormationAnalyzer` — AST-level and bytecode-
   level analyser that extracts scope trees and LEGB information from source
   code or live function objects.
3. :class:`ScopeSemanticsCoordinateFormationWitness` — runtime witness that
   verifies coordinate invariants and collects evidence bundles.

**Module-level constants** define the LEGB layer names, the set of bytecode
operations that touch scope state, and the full set of Python built-in names.

Typical usage::

    from jugeo.python_runtime.scope_and_state.scope_semantics_coordinate_formati import (
        ScopeSemanticsCoordinateFormationCoordinator,
        ScopeSemanticsCoordinateFormationAnalyzer,
        ScopeSemanticsCoordinateFormationWitness,
    )

    coord = ScopeSemanticsCoordinateFormationCoordinator()
    c = coord.coordinate_for_scope("mymodule.foo", "function", ("mymodule",))

    analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
    result = analyzer.analyze_source("x = 1\\ndef f():\\n    return x\\n")

    witness = ScopeSemanticsCoordinateFormationWitness()
    def sample(): pass
    record = witness.witness_scope_formation(sample)
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

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, MorphismKind, Site, SiteBuilder,
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

    class Site:  # type: ignore[no-redef]
        pass

    class SiteBuilder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import (
        TrustLevel, JudgmentStatus, PropositionKind, EvidenceItemKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, Judgment,
    )
except Exception:
    import enum

    class TrustLevel(enum.Enum):  # type: ignore[no-redef]
        """Stub for TrustLevel."""
        AXIOM = "axiom"
        VERIFIED = "verified"
        ASSUMED = "assumed"
        SUSPECTED = "suspected"
        UNKNOWN = "unknown"

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""
        PROVED = "proved"
        REFUTED = "refuted"
        OPEN = "open"
        PARTIAL = "partial"

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for PropositionKind."""
        INVARIANT = "invariant"
        PRECONDITION = "precondition"
        POSTCONDITION = "postcondition"
        OBLIGATION = "obligation"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for EvidenceItemKind."""
        RUNTIME_ASSERTION = "runtime_assertion"
        STATIC_ANALYSIS = "static_analysis"
        BYTECODE_INSPECTION = "bytecode_inspection"
        CLOSURE_INSPECTION = "closure_inspection"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub for Proposition."""
        text: str = ""
        kind: Any = None
        metadata: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        """Stub for Carrier."""
        name: str = ""
        coord: Any = None

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        """Stub for EvidenceItem."""
        kind: Any = None
        description: str = ""
        data: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        """Stub for EvidenceBundle."""
        items: tuple[Any, ...] = ()
        summary: str = ""

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        """Stub for ResidualObligation."""
        description: str = ""
        coord: Any = None

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        """Stub for Obstruction."""
        reason: str = ""
        coord: Any = None

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        """Stub for TrustAnnotation."""
        level: Any = None
        reason: str = ""

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        """Stub for Provenance."""
        source: str = ""
        timestamp: float = 0.0

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        """Stub for Judgment."""
        proposition: Any = None
        status: Any = None
        evidence: Any = None
        provenance: Any = None

try:
    from jugeo.python_runtime.scope_and_state.models import (
        NameKind, NameCoordinate, ScopeKind, ScopeChain, ScopeSection, BindingMap, NameResolutionResult,
    )
except Exception:
    import enum

    class NameKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for NameKind."""
        LOCAL = "local"
        GLOBAL = "global"
        FREE = "free"
        CLOSURE = "closure"
        BUILTIN = "builtin"
        PARAMETER = "parameter"
        NONLOCAL = "nonlocal"
        IMPORT = "import"

    class ScopeKind(enum.Enum):  # type: ignore[no-redef]
        """Stub for ScopeKind."""
        MODULE = "module"
        FUNCTION = "function"
        CLASS = "class"
        COMPREHENSION = "comprehension"
        LAMBDA = "lambda"
        ASYNC_FUNCTION = "async_function"

    @dataclass(frozen=True, slots=True)
    class NameCoordinate:  # type: ignore[no-redef]
        """Stub for NameCoordinate."""
        name: str = ""
        scope_path: tuple[str, ...] = ()
        kind: Any = None

    @dataclass(frozen=True, slots=True)
    class ScopeChain:  # type: ignore[no-redef]
        """Stub for ScopeChain."""
        scopes: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ScopeSection:  # type: ignore[no-redef]
        """Stub for ScopeSection."""
        name: str = ""
        kind: Any = None
        parent: str = ""

    @dataclass(frozen=True, slots=True)
    class BindingMap:  # type: ignore[no-redef]
        """Stub for BindingMap."""
        bindings: Mapping[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class NameResolutionResult:  # type: ignore[no-redef]
        """Stub for NameResolutionResult."""
        name: str = ""
        resolved_coord: Any = None
        layer: str = ""

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s01-scope-semantics-coordinate"

_LEGB_LAYERS: tuple[str, ...] = ("local", "enclosing", "global", "builtin")

_SCOPE_OPS: frozenset[str] = frozenset({
    "LOAD_FAST", "STORE_FAST", "DELETE_FAST",
    "LOAD_GLOBAL", "STORE_GLOBAL", "DELETE_GLOBAL",
    "LOAD_DEREF", "STORE_DEREF", "DELETE_DEREF",
    "LOAD_CLASSDEREF",
    "MAKE_CELL", "COPY_FREE_VARS",
})

_PYTHON_BUILTINS: frozenset[str] = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "delattr", "dict",
    "dir", "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
    "frozenset", "getattr", "globals", "hasattr", "hash", "help", "hex", "id",
    "input", "int", "isinstance", "issubclass", "iter", "len", "list", "locals",
    "map", "max", "memoryview", "min", "next", "object", "oct", "open", "ord",
    "pow", "print", "property", "range", "repr", "reversed", "round", "set",
    "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple",
    "type", "vars", "zip",
    "None", "True", "False", "NotImplemented", "Ellipsis", "__debug__",
    "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
    "BlockingIOError", "BrokenPipeError", "BufferError", "BytesWarning",
    "ChildProcessError", "ConnectionAbortedError", "ConnectionError",
    "ConnectionRefusedError", "ConnectionResetError", "DeprecationWarning",
    "EOFError", "EnvironmentError", "Exception", "FileExistsError",
    "FileNotFoundError", "FloatingPointError", "FutureWarning", "GeneratorExit",
    "IOError", "ImportError", "ImportWarning", "IndentationError", "IndexError",
    "InterruptedError", "IsADirectoryError", "KeyError", "KeyboardInterrupt",
    "LookupError", "MemoryError", "ModuleNotFoundError", "NameError",
    "NotADirectoryError", "NotImplementedError", "OSError", "OverflowError",
    "PendingDeprecationWarning", "PermissionError", "ProcessLookupError",
    "RecursionError", "ReferenceError", "ResourceWarning", "RuntimeError",
    "RuntimeWarning", "StopAsyncIteration", "StopIteration", "SyntaxError",
    "SyntaxWarning", "SystemError", "SystemExit", "TimeoutError", "TypeError",
    "UnboundLocalError", "UnicodeDecodeError", "UnicodeEncodeError", "UnicodeError",
    "UnicodeTranslateError", "UnicodeWarning", "UserWarning", "ValueError",
    "Warning", "ZeroDivisionError",
})

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def scope_kind_to_coordinate_kind(scope_kind: Any) -> Any:
    """Map a :class:`ScopeKind` value to the corresponding :class:`CoordinateKind`.

    The mapping follows the theory2.tex Ch15 §3 correspondence between scope
    strata and coordinate kinds.  Unknown or ``None`` inputs fall back to
    ``CoordinateKind.REGION``.

    Parameters
    ----------
    scope_kind:
        A :class:`ScopeKind` instance (or stub), or a plain string such as
        ``"module"`` or ``"function"``.

    Returns
    -------
    CoordinateKind
        The matching coordinate kind.

    Examples
    --------
    >>> scope_kind_to_coordinate_kind(ScopeKind.MODULE)
    <CoordinateKind.MODULE: 'module'>
    >>> scope_kind_to_coordinate_kind("function")
    <CoordinateKind.FUNCTION: 'function'>
    """
    # Normalise to a plain string for comparison.
    raw = getattr(scope_kind, "value", scope_kind)
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.lower()
    _map = {
        "module": CoordinateKind.MODULE,
        "function": CoordinateKind.FUNCTION,
        "async_function": CoordinateKind.FUNCTION,
        "lambda": CoordinateKind.FUNCTION,
        "class": CoordinateKind.INTERFACE,
        "comprehension": CoordinateKind.REGION,
        "test": CoordinateKind.TEST,
        "theorem": CoordinateKind.THEOREM,
        "interface": CoordinateKind.INTERFACE,
        "region": CoordinateKind.REGION,
    }
    return _map.get(raw, CoordinateKind.REGION)


def legb_layer_rank(layer: str) -> int:
    """Return the integer rank of a LEGB layer name (lower = higher priority).

    The LEGB ordering from theory2.tex Ch15 §2 assigns rank 0 to ``local``
    (highest priority) and rank 3 to ``builtin`` (lowest priority).  Any
    unrecognised layer receives rank 99 so it sorts last.

    Parameters
    ----------
    layer:
        One of ``"local"``, ``"enclosing"``, ``"global"``, ``"builtin"``.

    Returns
    -------
    int
        Rank integer; lower means higher lookup priority.

    Examples
    --------
    >>> legb_layer_rank("local")
    0
    >>> legb_layer_rank("builtin")
    3
    >>> legb_layer_rank("unknown")
    99
    """
    _ranks = {
        "local": 0,
        "enclosing": 1,
        "global": 2,
        "builtin": 3,
    }
    return _ranks.get(layer.lower(), 99)


def format_scope_chain(chain: tuple[str, ...]) -> str:
    """Format a scope chain tuple as a dot-separated string.

    Parameters
    ----------
    chain:
        A tuple of scope name segments, e.g. ``("mymodule", "outer", "inner")``.

    Returns
    -------
    str
        Dot-separated path, e.g. ``"mymodule.outer.inner"``.

    Examples
    --------
    >>> format_scope_chain(("a", "b", "c"))
    'a.b.c'
    >>> format_scope_chain(())
    '<root>'
    """
    if not chain:
        return "<root>"
    return ".".join(chain)


def extract_free_vars_from_code(code: types.CodeType) -> frozenset[str]:
    """Return a frozenset of free-variable names from a code object.

    Free variables are those listed in ``co_freevars`` — names that the code
    object reads from an enclosing scope's cell.  This is the primary indicator
    that a closure relationship exists.

    Parameters
    ----------
    code:
        A Python :class:`types.CodeType` object (e.g. ``func.__code__``).

    Returns
    -------
    frozenset[str]
        Names that appear in ``co_freevars``.

    Examples
    --------
    >>> def outer():
    ...     x = 1
    ...     def inner(): return x
    ...     return inner
    >>> extract_free_vars_from_code(outer().__code__)
    frozenset({'x'})
    """
    try:
        return frozenset(code.co_freevars)
    except AttributeError:
        return frozenset()


def classify_name_in_scope(name: str, code: types.CodeType) -> str:
    """Classify a name within a code object's scope.

    Checks ``co_varnames`` (locals/parameters), ``co_cellvars`` (cells that
    child scopes capture), ``co_freevars`` (captured from parent), and infers
    ``global`` or ``builtin`` for anything else.

    Parameters
    ----------
    name:
        The identifier string to classify.
    code:
        The code object representing the scope to inspect.

    Returns
    -------
    str
        One of ``"local"``, ``"parameter"``, ``"cell"``, ``"free"``,
        ``"global"``, or ``"builtin"``.

    Examples
    --------
    >>> def f(x):
    ...     y = 1
    ...     return x + y
    >>> classify_name_in_scope("x", f.__code__)
    'parameter'
    >>> classify_name_in_scope("print", f.__code__)
    'builtin'
    """
    # co_varnames contains both parameters and local variables; parameters come
    # first (up to co_argcount + co_posonlyargcount + co_kwonlyargcount).
    varnames: tuple[str, ...] = getattr(code, "co_varnames", ())
    cellvars: tuple[str, ...] = getattr(code, "co_cellvars", ())
    freevars: tuple[str, ...] = getattr(code, "co_freevars", ())
    # Determine parameter count to distinguish parameters from plain locals.
    n_args = (
        getattr(code, "co_argcount", 0)
        + getattr(code, "co_posonlyargcount", 0)
        + getattr(code, "co_kwonlyargcount", 0)
    )
    has_varargs = bool(getattr(code, "co_flags", 0) & 0x04)
    has_varkw = bool(getattr(code, "co_flags", 0) & 0x08)
    param_end = n_args + (1 if has_varargs else 0) + (1 if has_varkw else 0)
    param_names = set(varnames[:param_end])

    if name in cellvars:
        return "cell"
    if name in freevars:
        return "free"
    if name in param_names:
        return "parameter"
    if name in varnames:
        return "local"
    if name in _PYTHON_BUILTINS:
        return "builtin"
    # Fallback: assume global (could also be an unresolved name).
    return "global"


def build_scope_coordinate(scope_path: tuple[str, ...], scope_kind_str: str) -> CoordinateObject:
    """Build a :class:`CoordinateObject` from a scope path tuple and kind string.

    Converts the kind string to a :class:`CoordinateKind` via
    :func:`scope_kind_to_coordinate_kind` and wraps everything into a frozen
    coordinate object with a stable hash label derived from the path.

    Parameters
    ----------
    scope_path:
        Ordered tuple of scope name segments from outermost to innermost,
        e.g. ``("mymodule", "outer_func", "inner_func")``.
    scope_kind_str:
        A string naming the scope kind, e.g. ``"function"`` or ``"module"``.

    Returns
    -------
    CoordinateObject
        A new coordinate object localising the scope in the site.

    Examples
    --------
    >>> c = build_scope_coordinate(("mymod", "f"), "function")
    >>> c.components
    ('mymod', 'f')
    """
    kind = scope_kind_to_coordinate_kind(scope_kind_str)
    # Derive a stable label for caching and identity checks.
    label = hashlib.sha1(".".join(scope_path).encode()).hexdigest()[:12]
    return CoordinateObject(
        components=scope_path,
        kind=kind,
        support_labels=frozenset({label}),
        metadata={"scope_kind": scope_kind_str, "path_hash": label},
    )


# ---------------------------------------------------------------------------
# Class 1 — Coordinator
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScopeSemanticsCoordinateFormationCoordinator:
    """Low-level coordinate builder with obligation tracking.

    This class implements the coordinate-formation algorithm from theory2.tex
    Ch15 §4.  It maintains an in-memory cache of formed coordinates (keyed by
    scope name) and a log of coordination obligations that arise whenever a
    mutable shared name is accessed across scope boundaries.

    The coordinator is the foundation on which :class:`ScopeSemanticsCoordinateFormationAnalyzer`
    and :class:`ScopeSemanticsCoordinateFormationWitness` are built.

    Attributes
    ----------
    _scope_map:
        Raw scope metadata keyed by scope name string.
    _coordinate_cache:
        Memoisation cache: scope_name → :class:`CoordinateObject`.
    _obligation_log:
        Append-only log of dicts recording coordination obligations.
    _analysis_id:
        Unique hex identifier for this coordinator instance.
    """

    _scope_map: dict[str, Any] = field(default_factory=dict)
    _coordinate_cache: dict[str, CoordinateObject] = field(default_factory=dict)
    _obligation_log: list[dict[str, Any]] = field(default_factory=list)
    _analysis_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def coordinate_for_scope(
        self,
        scope_name: str,
        scope_kind: Any,
        parent_chain: tuple[str, ...],
    ) -> CoordinateObject:
        """Build and cache a coordinate for the named scope.

        Constructs a :class:`CoordinateObject` whose ``components`` are derived
        by appending ``scope_name`` to ``parent_chain``, and whose ``kind`` is
        determined by mapping ``scope_kind`` through
        :func:`scope_kind_to_coordinate_kind`.  Results are cached so repeated
        calls with the same ``scope_name`` return the identical object.

        Parameters
        ----------
        scope_name:
            The unqualified name of the scope (e.g. ``"inner"``).
        scope_kind:
            A :class:`ScopeKind` value or plain string describing the kind of
            scope being located.
        parent_chain:
            Tuple of ancestor scope names from outermost to the immediate
            parent, e.g. ``("mymodule", "outer")``.

        Returns
        -------
        CoordinateObject
            A frozen coordinate object uniquely locating the scope.

        Raises
        ------
        ValueError
            If ``scope_name`` is empty.

        Examples
        --------
        >>> coord = ScopeSemanticsCoordinateFormationCoordinator()
        >>> c = coord.coordinate_for_scope("foo", "function", ("mymod",))
        >>> c.components
        ('mymod', 'foo')
        """
        if not scope_name:
            raise ValueError("scope_name must be a non-empty string")

        # Return cached coordinate if already computed.
        if scope_name in self._coordinate_cache:
            log.debug("[%s] cache hit for scope %r", _ANALYSIS_CHANNEL, scope_name)
            return self._coordinate_cache[scope_name]

        # Build the full component path by extending the parent chain.
        full_path: tuple[str, ...] = parent_chain + (scope_name,)

        # Map the scope kind to a coordinate kind for the site layer.
        coord_kind = scope_kind_to_coordinate_kind(scope_kind)

        # Compute a deterministic hash label from the full path.
        path_str = format_scope_chain(full_path)
        label = hashlib.sha1(path_str.encode()).hexdigest()[:12]

        # Determine whether this scope introduces a mutable shared state
        # obligation (i.e. global or enclosing scopes).
        raw_kind = getattr(scope_kind, "value", str(scope_kind)).lower()
        if raw_kind in ("module", "global"):
            self.register_obligation(
                coord=CoordinateObject(
                    components=full_path,
                    kind=coord_kind,
                    support_labels=frozenset({label}),
                    metadata={"scope_kind": raw_kind},
                ),
                reason="module-global mutable state coordination obligation",
            )

        # Construct the coordinate object.
        coord = CoordinateObject(
            components=full_path,
            kind=coord_kind,
            support_labels=frozenset({label, f"analysis:{self._analysis_id[:8]}"}),
            metadata={
                "scope_name": scope_name,
                "scope_kind": str(scope_kind),
                "path_hash": label,
                "analysis_id": self._analysis_id,
                "formed_at": time.monotonic(),
            },
        )

        # Store in both caches.
        self._coordinate_cache[scope_name] = coord
        self._scope_map[scope_name] = {
            "coord": coord,
            "parent_chain": parent_chain,
            "scope_kind": str(scope_kind),
        }
        log.debug("[%s] formed coordinate %r for scope %r", _ANALYSIS_CHANNEL, label, scope_name)
        return coord

    def register_obligation(
        self,
        coord: CoordinateObject,
        reason: str,
        trust_level: Any = None,
    ) -> None:
        """Record a coordination obligation arising at the given coordinate.

        Obligations represent points in the program where mutable shared state
        crosses a scope boundary and therefore requires explicit coordination
        analysis (theory2.tex Ch15 §5).

        Parameters
        ----------
        coord:
            The coordinate at which the obligation arises.
        reason:
            Human-readable description of why the obligation exists.
        trust_level:
            Optional trust annotation; defaults to ``TrustLevel.UNKNOWN`` if
            not provided.

        Examples
        --------
        >>> coordinator = ScopeSemanticsCoordinateFormationCoordinator()
        >>> c = coordinator.coordinate_for_scope("mod", "module", ())
        >>> coordinator.register_obligation(c, "global write")
        """
        effective_trust = trust_level if trust_level is not None else TrustLevel.UNKNOWN
        entry = {
            "coord_components": getattr(coord, "components", ()),
            "coord_kind": str(getattr(coord, "kind", None)),
            "reason": reason,
            "trust_level": str(effective_trust),
            "timestamp": time.monotonic(),
            "analysis_id": self._analysis_id,
        }
        self._obligation_log.append(entry)
        log.debug(
            "[%s] obligation recorded at %r: %s",
            _ANALYSIS_CHANNEL,
            getattr(coord, "components", ()),
            reason,
        )

    def form_legb_chain(
        self,
        local_names: frozenset[str],
        enclosing_names: frozenset[str],
        global_names: frozenset[str],
        builtin_names: frozenset[str],
    ) -> dict[str, tuple[str, Any]]:
        """Resolve names according to LEGB priority and return a layer map.

        Implements the LEGB rule from theory2.tex Ch15 §2: for each name in the
        union of all four name sets, determine the highest-priority layer in
        which it appears and record the winning layer alongside its
        :class:`NameKind`.

        Parameters
        ----------
        local_names:
            Names defined in the local (innermost) scope.
        enclosing_names:
            Names visible from lexically enclosing function scopes.
        global_names:
            Names in the module-level ``__dict__``.
        builtin_names:
            Names from the builtins layer (usually a subset of ``_PYTHON_BUILTINS``).

        Returns
        -------
        dict[str, tuple[str, Any]]
            Mapping ``name → (legb_layer, NameKind)`` where ``legb_layer`` is
            one of ``"local"``, ``"enclosing"``, ``"global"``, ``"builtin"``.

        Examples
        --------
        >>> coord = ScopeSemanticsCoordinateFormationCoordinator()
        >>> result = coord.form_legb_chain(
        ...     frozenset({"x"}), frozenset({"x", "y"}), frozenset({"z"}), frozenset({"print"})
        ... )
        >>> result["x"]
        ('local', ...)
        """
        resolved: dict[str, tuple[str, Any]] = {}

        # Process in LEGB order so earlier (higher-priority) layers win.
        layer_sets = [
            ("local",     local_names,     NameKind.LOCAL),
            ("enclosing", enclosing_names, NameKind.FREE),
            ("global",    global_names,    NameKind.GLOBAL),
            ("builtin",   builtin_names,   NameKind.BUILTIN),
        ]
        for layer_name, name_set, name_kind in layer_sets:
            for name in name_set:
                # Only record if this name hasn't been claimed by a
                # higher-priority layer yet.
                if name not in resolved:
                    resolved[name] = (layer_name, name_kind)

        return resolved

    def coordinate_morphism(
        self,
        source_coord: CoordinateObject,
        target_coord: CoordinateObject,
    ) -> dict[str, Any]:
        """Compute the morphism kind and distance between two coordinates.

        Inspects the component paths of ``source_coord`` and ``target_coord``
        to determine the nature of the morphism (restriction, inclusion,
        transport, or refinement) following theory2.tex Ch15 §3.

        Parameters
        ----------
        source_coord:
            The coordinate at the use site.
        target_coord:
            The coordinate at the binding site.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"kind"`` (:class:`MorphismKind`), ``"distance"``
            (int), ``"chain"`` (tuple), ``"lca"`` (lowest common ancestor
            components as a tuple).

        Examples
        --------
        >>> coord = ScopeSemanticsCoordinateFormationCoordinator()
        >>> src = coord.coordinate_for_scope("inner", "function", ("mod", "outer"))
        >>> tgt = coord.coordinate_for_scope("outer", "function", ("mod",))
        >>> morph = coord.coordinate_morphism(src, tgt)
        >>> morph["kind"] == MorphismKind.RESTRICTION
        True
        """
        src_comps: tuple[str, ...] = getattr(source_coord, "components", ())
        tgt_comps: tuple[str, ...] = getattr(target_coord, "components", ())

        # Find the longest common prefix (lowest common ancestor in the tree).
        lca_len = 0
        for a, b in zip(src_comps, tgt_comps):
            if a == b:
                lca_len += 1
            else:
                break
        lca = src_comps[:lca_len]

        src_depth = len(src_comps) - lca_len
        tgt_depth = len(tgt_comps) - lca_len
        distance = src_depth + tgt_depth

        # Determine morphism kind from relative depths.
        if src_depth > 0 and tgt_depth == 0:
            # Source is deeper → restriction (inner reading from outer).
            kind = MorphismKind.RESTRICTION
        elif src_depth == 0 and tgt_depth > 0:
            # Source is shallower → inclusion (outer referencing inner).
            kind = MorphismKind.INCLUSION
        elif src_depth == 0 and tgt_depth == 0:
            # Same coordinate → identity (refinement of self).
            kind = MorphismKind.REFINEMENT
        else:
            # General cross-scope reference → transport morphism.
            kind = MorphismKind.TRANSPORT

        return {
            "kind": kind,
            "distance": distance,
            "chain": src_comps + tgt_comps[lca_len:],
            "lca": lca,
            "src_depth": src_depth,
            "tgt_depth": tgt_depth,
        }

    def flush_obligations(self) -> list[dict[str, Any]]:
        """Return all logged obligations and clear the log.

        Returns
        -------
        list[dict[str, Any]]
            All obligation entries accumulated since the last flush (or since
            construction).

        Examples
        --------
        >>> coordinator = ScopeSemanticsCoordinateFormationCoordinator()
        >>> obligations = coordinator.flush_obligations()
        >>> obligations
        []
        """
        flushed = list(self._obligation_log)
        self._obligation_log.clear()
        log.debug(
            "[%s] flushed %d obligations", _ANALYSIS_CHANNEL, len(flushed)
        )
        return flushed

    def summary(self) -> dict[str, Any]:
        """Return a summary dict with counts and analysis metadata.

        Returns
        -------
        dict[str, Any]
            Summary containing ``"analysis_id"``, ``"cached_scopes"``,
            ``"pending_obligations"``, and ``"scope_names"``.

        Examples
        --------
        >>> coordinator = ScopeSemanticsCoordinateFormationCoordinator()
        >>> s = coordinator.summary()
        >>> "analysis_id" in s
        True
        """
        return {
            "analysis_id": self._analysis_id,
            "cached_scopes": len(self._coordinate_cache),
            "pending_obligations": len(self._obligation_log),
            "scope_names": list(self._scope_map.keys()),
        }


# ---------------------------------------------------------------------------
# Class 2 — Analyzer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScopeSemanticsCoordinateFormationAnalyzer:
    """AST-level and bytecode-level scope analyser.

    This class walks Python source code (as AST) or live function objects to
    extract the full LEGB coordinate structure and emit judgments about scope
    boundaries, closure obligations, and name resolutions.

    It delegates coordinate formation to an embedded
    :class:`ScopeSemanticsCoordinateFormationCoordinator`.

    Attributes
    ----------
    _coordinator:
        The underlying coordinator used for coordinate formation.
    _ast_cache:
        Memoisation cache: ``module_name → ast.Module``.
    _scope_tree:
        Hierarchical scope tree: ``scope_name → list[child_scope_names]``.
    _analysis_log:
        Append-only log of analysis events.
    _stats:
        Counter dict for tracking analysis statistics.
    """

    _coordinator: ScopeSemanticsCoordinateFormationCoordinator = field(
        default_factory=ScopeSemanticsCoordinateFormationCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _scope_tree: dict[str, list[str]] = field(default_factory=dict)
    _analysis_log: list[dict[str, Any]] = field(default_factory=list)
    _stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def analyze_source(
        self,
        source: str,
        module_name: str = "<module>",
    ) -> dict[str, Any]:
        """Parse and analyse a Python source string.

        Parses ``source`` with :func:`ast.parse`, builds the scope tree via
        :meth:`extract_scope_tree`, gathers per-scope name bindings via
        :meth:`extract_names_by_scope`, forms coordinates for every discovered
        scope, and returns a comprehensive analysis dict.

        Parameters
        ----------
        source:
            Python source text to analyse.
        module_name:
            Logical module name used as the root scope label.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"module_name"``, ``"scope_tree"``, ``"scopes"``,
            ``"coordinates"``, ``"obligations"``, ``"stats"``.

        Raises
        ------
        SyntaxError
            If ``source`` cannot be parsed.

        Examples
        --------
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> result = analyzer.analyze_source("x = 1\\n")
        >>> "<module>" in result["scope_tree"] or result["module_name"] == "<module>"
        True
        """
        # Parse the source; propagate SyntaxError to the caller.
        tree = ast.parse(source, filename=module_name)
        self._ast_cache[module_name] = tree
        self._stats["parse_count"] += 1

        # Build the hierarchical scope tree.
        scope_tree = self.extract_scope_tree(tree)
        self._scope_tree.update(scope_tree)
        self._stats["scope_tree_nodes"] += len(scope_tree)

        # Extract per-scope name classifications.
        names_by_scope = self.extract_names_by_scope(tree)
        self._stats["name_bindings"] += sum(len(v) for v in names_by_scope.values())

        # Form coordinates for every scope using the coordinator.
        coordinates: dict[str, CoordinateObject] = {}
        for scope_name, children in scope_tree.items():
            # Determine parent chain by splitting on "." (module is root).
            parts = scope_name.split(".")
            if len(parts) == 1:
                parent_chain: tuple[str, ...] = ()
                short_name = parts[0]
            else:
                parent_chain = tuple(parts[:-1])
                short_name = parts[-1]
            # Infer scope kind from presence of children and name patterns.
            scope_kind_str = "function" if "." in scope_name else "module"
            coord = self._coordinator.coordinate_for_scope(
                scope_name=short_name,
                scope_kind=scope_kind_str,
                parent_chain=parent_chain,
            )
            coordinates[scope_name] = coord

        # Flush obligations accumulated during coordinate formation.
        obligations = self._coordinator.flush_obligations()

        # Record analysis event.
        event = {
            "module_name": module_name,
            "timestamp": time.monotonic(),
            "scopes_found": len(scope_tree),
            "obligations": len(obligations),
        }
        self._analysis_log.append(event)
        self._stats["analysis_count"] += 1

        return {
            "module_name": module_name,
            "scope_tree": scope_tree,
            "scopes": list(scope_tree.keys()),
            "names_by_scope": names_by_scope,
            "coordinates": {k: v for k, v in coordinates.items()},
            "obligations": obligations,
            "stats": dict(self._stats),
        }

    def extract_scope_tree(self, tree: ast.Module) -> dict[str, list[str]]:
        """Walk an AST module and build the hierarchical scope tree.

        Traverses all :class:`ast.FunctionDef`, :class:`ast.AsyncFunctionDef`,
        :class:`ast.ClassDef` nodes, and comprehension nodes to discover nested
        scope boundaries.  Returns a dict mapping each scope's dotted name to
        the list of its direct children's dotted names.

        Parameters
        ----------
        tree:
            The root :class:`ast.Module` node from :func:`ast.parse`.

        Returns
        -------
        dict[str, list[str]]
            Mapping ``dotted_scope_name → [child_dotted_scope_name, ...]``.

        Examples
        --------
        >>> import ast
        >>> src = "def f():\\n    def g(): pass\\n"
        >>> t = ast.parse(src)
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> st = analyzer.extract_scope_tree(t)
        >>> "<module>" in st
        True
        """
        result: dict[str, list[str]] = {"<module>": []}
        # Stack items are (node, parent_scope_name).
        stack: list[tuple[ast.AST, str]] = [(tree, "<module>")]

        while stack:
            node, parent_scope = stack.pop()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child_scope = f"{parent_scope}.{child.name}"
                    result.setdefault(parent_scope, []).append(child_scope)
                    result.setdefault(child_scope, [])
                    stack.append((child, child_scope))
                elif isinstance(child, ast.ClassDef):
                    child_scope = f"{parent_scope}.{child.name}"
                    result.setdefault(parent_scope, []).append(child_scope)
                    result.setdefault(child_scope, [])
                    stack.append((child, child_scope))
                elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    # Comprehensions introduce their own scope in Python 3.
                    comp_id = f"{parent_scope}.<comp@{getattr(child, 'lineno', '?')}>"
                    result.setdefault(parent_scope, []).append(comp_id)
                    result.setdefault(comp_id, [])
                    # Do not recurse into comprehension internals for scope tree.
                else:
                    stack.append((child, parent_scope))

        return result

    def extract_names_by_scope(self, tree: ast.Module) -> dict[str, dict[str, str]]:
        """Walk AST nodes to classify name bindings by scope layer.

        Scans all :class:`ast.Name` nodes and categorises each occurrence as
        ``"store"``, ``"load"``, ``"del"``, ``"global"``, or ``"nonlocal"``.
        Tracks ``ast.Global`` and ``ast.Nonlocal`` statements to adjust
        classification accordingly.

        Parameters
        ----------
        tree:
            Root :class:`ast.Module` node.

        Returns
        -------
        dict[str, dict[str, str]]
            Mapping ``scope_name → {name: classification_string}``.

        Examples
        --------
        >>> import ast
        >>> src = "x = 1\\ndef f():\\n    global x\\n    x = 2\\n"
        >>> t = ast.parse(src)
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> names = analyzer.extract_names_by_scope(t)
        >>> "x" in names.get("<module>", {})
        True
        """
        result: dict[str, dict[str, str]] = defaultdict(dict)
        # Use a stack to track the current scope path.
        scope_stack: list[str] = ["<module>"]

        def _current_scope() -> str:
            return scope_stack[-1]

        class _NameVisitor(ast.NodeVisitor):
            def visit_FunctionDef(inner_self, node: ast.FunctionDef) -> None:  # noqa: N805
                new_scope = f"{_current_scope()}.{node.name}"
                scope_stack.append(new_scope)
                inner_self.generic_visit(node)
                scope_stack.pop()

            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef) -> None:  # noqa: N805
                new_scope = f"{_current_scope()}.{node.name}"
                scope_stack.append(new_scope)
                inner_self.generic_visit(node)
                scope_stack.pop()

            def visit_ClassDef(inner_self, node: ast.ClassDef) -> None:  # noqa: N805
                new_scope = f"{_current_scope()}.{node.name}"
                scope_stack.append(new_scope)
                inner_self.generic_visit(node)
                scope_stack.pop()

            def visit_Global(inner_self, node: ast.Global) -> None:  # noqa: N805
                for name in node.names:
                    result[_current_scope()][name] = "global"

            def visit_Nonlocal(inner_self, node: ast.Nonlocal) -> None:  # noqa: N805
                for name in node.names:
                    result[_current_scope()][name] = "nonlocal"

            def visit_Name(inner_self, node: ast.Name) -> None:  # noqa: N805
                scope = _current_scope()
                existing = result[scope].get(node.id)
                if existing in ("global", "nonlocal"):
                    # Preserve explicit declarations over implicit classifications.
                    return
                if isinstance(node.ctx, ast.Store):
                    result[scope][node.id] = "store"
                elif isinstance(node.ctx, ast.Del):
                    result[scope][node.id] = "del"
                else:
                    if node.id not in result[scope]:
                        result[scope][node.id] = "load"

        visitor = _NameVisitor()
        visitor.visit(tree)
        return dict(result)

    def analyze_cell_variables(self, func: types.FunctionType) -> dict[str, Any]:
        """Extract cell and free variable information from a live function.

        Inspects ``func.__code__.co_cellvars``, ``func.__code__.co_freevars``,
        and ``inspect.getclosurevars(func)`` to produce a complete picture of
        the closure relationships around ``func``.

        Parameters
        ----------
        func:
            A live Python function object.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"cells"``, ``"freevars"``, ``"closure_vars"``,
            ``"closure_coords"``, ``"has_closure"``.

        Examples
        --------
        >>> def outer():
        ...     x = 1
        ...     def inner(): return x
        ...     return inner
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> info = analyzer.analyze_cell_variables(outer())
        >>> "x" in info["freevars"]
        True
        """
        code = func.__code__
        cells = list(getattr(code, "co_cellvars", ()))
        freevars = list(getattr(code, "co_freevars", ()))

        # Use inspect.getclosurevars for the actual runtime values when available.
        try:
            closure_info = inspect.getclosurevars(func)
            nonlocals_dict = dict(closure_info.nonlocals)
            globals_dict = dict(closure_info.globals)
            builtins_dict = dict(closure_info.builtins)
            unbound_set = set(closure_info.unbound)
        except Exception:
            nonlocals_dict = {}
            globals_dict = {}
            builtins_dict = {}
            unbound_set = set()

        # Build coordinate objects for each free variable.
        closure_coords: dict[str, CoordinateObject] = {}
        func_name = getattr(func, "__qualname__", func.__name__)
        func_module = getattr(func, "__module__", "<unknown>") or "<unknown>"
        for fv in freevars:
            parent_parts = func_module.split(".") + func_name.split(".")[:-1]
            coord = self._coordinator.coordinate_for_scope(
                scope_name=fv,
                scope_kind="closure",
                parent_chain=tuple(parent_parts),
            )
            closure_coords[fv] = coord
            # A free variable reference from inside a function is a restriction
            # morphism obligation; register it.
            self._coordinator.register_obligation(
                coord=coord,
                reason=f"free variable '{fv}' captured from enclosing scope",
            )

        has_closure = bool(cells or freevars)
        self._stats["cell_analyses"] += 1

        return {
            "cells": cells,
            "freevars": freevars,
            "closure_vars": nonlocals_dict,
            "globals_used": globals_dict,
            "builtins_used": builtins_dict,
            "unbound": list(unbound_set),
            "closure_coords": closure_coords,
            "has_closure": has_closure,
        }

    def build_legb_coordinates(self, func: types.FunctionType) -> list[CoordinateObject]:
        """Build the complete LEGB coordinate chain for a live function.

        Constructs one :class:`CoordinateObject` per LEGB layer that is
        non-empty for ``func``, in priority order (local first, builtin last).

        Parameters
        ----------
        func:
            A live Python function.

        Returns
        -------
        list[CoordinateObject]
            List of up to four coordinates, one per non-empty LEGB layer.

        Examples
        --------
        >>> def f(x): return x
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> coords = analyzer.build_legb_coordinates(f)
        >>> len(coords) >= 1
        True
        """
        result: list[CoordinateObject] = []
        code = func.__code__
        func_name = getattr(func, "__qualname__", func.__name__)
        module_name = getattr(func, "__module__", "<unknown>") or "<unknown>"

        # Local layer — co_varnames covers parameters and locals.
        local_names = frozenset(getattr(code, "co_varnames", ()))
        if local_names:
            c = self._coordinator.coordinate_for_scope(
                scope_name=f"{func_name}:local",
                scope_kind="function",
                parent_chain=(module_name,),
            )
            result.append(c)

        # Enclosing layer — co_freevars names free variables from cells.
        free_names = frozenset(getattr(code, "co_freevars", ()))
        if free_names:
            c = self._coordinator.coordinate_for_scope(
                scope_name=f"{func_name}:enclosing",
                scope_kind="function",
                parent_chain=(module_name,),
            )
            result.append(c)

        # Global layer — infer from the function's __globals__ dict.
        global_names = frozenset(func.__globals__.keys()) if hasattr(func, "__globals__") else frozenset()
        if global_names:
            c = self._coordinator.coordinate_for_scope(
                scope_name=f"{module_name}:global",
                scope_kind="module",
                parent_chain=(),
            )
            result.append(c)

        # Builtin layer — always present.
        c = self._coordinator.coordinate_for_scope(
            scope_name="builtins",
            scope_kind="module",
            parent_chain=(),
        )
        result.append(c)

        self._stats["legb_chain_builds"] += 1
        return result

    def disassemble_scope_ops(self, func: types.FunctionType) -> list[dict[str, Any]]:
        """Disassemble ``func`` and return all scope-related bytecode instructions.

        Uses :func:`dis.get_instructions` to iterate over bytecode instructions
        and filters to those whose opnames appear in :data:`_SCOPE_OPS`.

        Parameters
        ----------
        func:
            A live Python function to disassemble.

        Returns
        -------
        list[dict[str, Any]]
            List of dicts, each with keys ``"opname"``, ``"argval"``,
            ``"offset"``, ``"is_jump_target"``.

        Examples
        --------
        >>> def f(x): return x
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> ops = analyzer.disassemble_scope_ops(f)
        >>> all("opname" in op for op in ops)
        True
        """
        scope_instructions: list[dict[str, Any]] = []
        try:
            for instr in dis.get_instructions(func):
                if instr.opname in _SCOPE_OPS:
                    scope_instructions.append({
                        "opname": instr.opname,
                        "argval": instr.argval,
                        "offset": instr.offset,
                        "is_jump_target": instr.is_jump_target,
                        "starts_line": instr.starts_line,
                    })
        except Exception as exc:
            log.warning("[%s] disassembly failed: %s", _ANALYSIS_CHANNEL, exc)
        self._stats["disassemblies"] += 1
        return scope_instructions

    def emit_judgment(
        self,
        coord: CoordinateObject,
        proposition_text: str,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Emit a judgment dict for the given coordinate and proposition.

        Constructs a judgment record following the theory2.tex judgment schema
        (Ch15 §6) and appends it to the analysis log.

        Parameters
        ----------
        coord:
            The coordinate at which the judgment is made.
        proposition_text:
            Human-readable statement of the proposition being judged.
        trust_level:
            Optional trust annotation; defaults to ``TrustLevel.ASSUMED``.

        Returns
        -------
        dict[str, Any]
            Judgment record with ``"coord"``, ``"proposition"``, ``"trust"``,
            ``"status"``, ``"timestamp"`` fields.

        Examples
        --------
        >>> analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
        >>> coord = build_scope_coordinate(("mod",), "module")
        >>> j = analyzer.emit_judgment(coord, "module scope is well-formed")
        >>> j["status"] == str(JudgmentStatus.OPEN)
        True
        """
        effective_trust = trust_level if trust_level is not None else TrustLevel.ASSUMED
        judgment_record = {
            "coord_components": getattr(coord, "components", ()),
            "coord_kind": str(getattr(coord, "kind", None)),
            "proposition": proposition_text,
            "trust": str(effective_trust),
            "status": str(JudgmentStatus.OPEN),
            "timestamp": time.monotonic(),
            "analysis_id": self._coordinator._analysis_id,
        }
        self._analysis_log.append(judgment_record)
        self._stats["judgments_emitted"] += 1
        log.debug(
            "[%s] judgment emitted for %r: %s",
            _ANALYSIS_CHANNEL,
            getattr(coord, "components", ()),
            proposition_text,
        )
        return judgment_record


# ---------------------------------------------------------------------------
# Class 3 — Witness
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScopeSemanticsCoordinateFormationWitness:
    """Runtime witness for scope coordinate invariants.

    A witness actively verifies coordinate invariants at runtime by inspecting
    live function objects, performing real LEGB lookups, and recording evidence
    for or against stated propositions (theory2.tex Ch15 §7).

    Attributes
    ----------
    _analyzer:
        Embedded analyser for deeper AST/bytecode inspection.
    _witnessed_facts:
        Append-only list of successfully witnessed facts.
    _refutations:
        Append-only list of refutations (failures of expected invariants).
    _witness_id:
        Short hex string identifying this witness instance.
    """

    _analyzer: ScopeSemanticsCoordinateFormationAnalyzer = field(
        default_factory=ScopeSemanticsCoordinateFormationAnalyzer
    )
    _witnessed_facts: list[dict[str, Any]] = field(default_factory=list)
    _refutations: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def witness_scope_formation(self, func: types.FunctionType) -> dict[str, Any]:
        """Perform complete runtime witnessing of a function's scope structure.

        Inspects ``func.__code__`` attributes, verifies LEGB compliance by
        cross-checking co_varnames / co_freevars / co_cellvars, disassembles
        scope bytecode ops, analyses cell variables, and records all evidence.

        Parameters
        ----------
        func:
            A live Python function object to witness.

        Returns
        -------
        dict[str, Any]
            Witness record containing ``"func_name"``, ``"qualname"``,
            ``"code_attrs"``, ``"scope_ops"``, ``"cell_info"``,
            ``"legb_coords"``, ``"facts_count"``, ``"witness_id"``.

        Examples
        --------
        >>> def f(x):
        ...     return x + 1
        >>> witness = ScopeSemanticsCoordinateFormationWitness()
        >>> record = witness.witness_scope_formation(f)
        >>> record["func_name"] == "f"
        True
        """
        code = func.__code__
        func_name = func.__name__
        qualname = getattr(func, "__qualname__", func_name)

        # Collect basic code object attributes for the witness record.
        code_attrs = {
            "co_name": code.co_name,
            "co_filename": getattr(code, "co_filename", "<unknown>"),
            "co_firstlineno": getattr(code, "co_firstlineno", 0),
            "co_argcount": getattr(code, "co_argcount", 0),
            "co_varnames": list(getattr(code, "co_varnames", ())),
            "co_cellvars": list(getattr(code, "co_cellvars", ())),
            "co_freevars": list(getattr(code, "co_freevars", ())),
            "co_flags": getattr(code, "co_flags", 0),
        }

        # Verify that co_varnames does not overlap with co_freevars (a Python
        # invariant: a name cannot be both local and free).
        varnames_set = frozenset(code_attrs["co_varnames"])
        freevars_set = frozenset(code_attrs["co_freevars"])
        overlap = varnames_set & freevars_set
        if overlap:
            self.refute(
                reason=f"co_varnames ∩ co_freevars is non-empty: {overlap}",
                evidence={"overlap": list(overlap), "func": qualname},
            )
        else:
            self._witnessed_facts.append({
                "fact": "co_varnames ∩ co_freevars = ∅",
                "func": qualname,
                "timestamp": time.monotonic(),
                "witness_id": self._witness_id,
            })

        # Verify that co_cellvars does not overlap with co_freevars.
        cellvars_set = frozenset(code_attrs["co_cellvars"])
        cell_free_overlap = cellvars_set & freevars_set
        if cell_free_overlap:
            self.refute(
                reason=f"co_cellvars ∩ co_freevars is non-empty: {cell_free_overlap}",
                evidence={"overlap": list(cell_free_overlap), "func": qualname},
            )
        else:
            self._witnessed_facts.append({
                "fact": "co_cellvars ∩ co_freevars = ∅",
                "func": qualname,
                "timestamp": time.monotonic(),
                "witness_id": self._witness_id,
            })

        # Disassemble scope operations.
        scope_ops = self._analyzer.disassemble_scope_ops(func)

        # Analyse cell/free variable relationships.
        cell_info = self._analyzer.analyze_cell_variables(func)

        # Build LEGB coordinate chain.
        legb_coords = self._analyzer.build_legb_coordinates(func)

        # Record a general witnessing fact for this function.
        fact = {
            "fact": f"scope_formation witnessed for {qualname!r}",
            "scope_ops_count": len(scope_ops),
            "has_closure": cell_info["has_closure"],
            "timestamp": time.monotonic(),
            "witness_id": self._witness_id,
        }
        self._witnessed_facts.append(fact)

        return {
            "func_name": func_name,
            "qualname": qualname,
            "code_attrs": code_attrs,
            "scope_ops": scope_ops,
            "cell_info": {
                k: v for k, v in cell_info.items()
                if k != "closure_coords"
            },
            "legb_coords": [
                {"components": c.components, "kind": str(c.kind)}
                for c in legb_coords
            ],
            "facts_count": len(self._witnessed_facts),
            "refutations_count": len(self._refutations),
            "witness_id": self._witness_id,
        }

    def witness_coordinate_invariant(
        self,
        coord: CoordinateObject,
        expected_kind: Any,
    ) -> bool:
        """Verify that a coordinate's kind matches the expected kind.

        Compares ``coord.kind`` to ``expected_kind`` (comparing ``.value``
        attributes when available).  Records the result in
        ``_witnessed_facts`` on success or ``_refutations`` on failure.

        Parameters
        ----------
        coord:
            The coordinate to check.
        expected_kind:
            The expected :class:`CoordinateKind` (or compatible value).

        Returns
        -------
        bool
            ``True`` if the invariant holds, ``False`` otherwise.

        Examples
        --------
        >>> witness = ScopeSemanticsCoordinateFormationWitness()
        >>> c = build_scope_coordinate(("mod",), "module")
        >>> witness.witness_coordinate_invariant(c, CoordinateKind.MODULE)
        True
        """
        actual_kind = getattr(coord, "kind", None)
        # Normalise both sides to their .value strings for robust comparison.
        actual_val = getattr(actual_kind, "value", str(actual_kind))
        expected_val = getattr(expected_kind, "value", str(expected_kind))
        holds = actual_val == expected_val

        record = {
            "coord_components": getattr(coord, "components", ()),
            "expected_kind": expected_val,
            "actual_kind": actual_val,
            "holds": holds,
            "timestamp": time.monotonic(),
            "witness_id": self._witness_id,
        }
        if holds:
            self._witnessed_facts.append({"fact": "coordinate_kind_invariant", **record})
        else:
            self._refutations.append({"refutation": "coordinate_kind_mismatch", **record})
        return holds

    def witness_legb_resolution(
        self,
        name: str,
        local_ns: dict[str, Any],
        global_ns: dict[str, Any],
        builtin_ns: dict[str, Any],
    ) -> tuple[str, Any]:
        """Perform a real LEGB lookup and record where the name was found.

        Checks ``local_ns`` first, then ``global_ns``, then ``builtin_ns``.
        (The enclosing-scope layer is not directly accessible as a dict in a
        live call; callers should merge enclosing names into ``local_ns``.)

        Parameters
        ----------
        name:
            The identifier to resolve.
        local_ns:
            The local (and optionally enclosing) namespace dict.
        global_ns:
            The module global namespace dict.
        builtin_ns:
            The builtins namespace dict.

        Returns
        -------
        tuple[str, Any]
            ``(layer, value)`` where ``layer`` is one of ``"local"``,
            ``"global"``, ``"builtin"``, or ``"unresolved"``.

        Examples
        --------
        >>> witness = ScopeSemanticsCoordinateFormationWitness()
        >>> layer, val = witness.witness_legb_resolution("x", {"x": 42}, {}, {})
        >>> layer
        'local'
        >>> val
        42
        """
        if name in local_ns:
            layer, value = "local", local_ns[name]
        elif name in global_ns:
            layer, value = "global", global_ns[name]
        elif name in builtin_ns:
            layer, value = "builtin", builtin_ns[name]
        else:
            layer, value = "unresolved", None

        fact = {
            "fact": "legb_resolution",
            "name": name,
            "layer": layer,
            "found": layer != "unresolved",
            "timestamp": time.monotonic(),
            "witness_id": self._witness_id,
        }
        if layer == "unresolved":
            self._refutations.append({"refutation": "legb_name_unresolved", **fact})
        else:
            self._witnessed_facts.append(fact)

        return (layer, value)

    def collect_evidence_bundle(self) -> dict[str, Any]:
        """Collect all witnessed facts and refutations into an evidence bundle.

        Returns
        -------
        dict[str, Any]
            Bundle dict with ``"witness_id"``, ``"facts"``, ``"refutations"``,
            ``"fact_count"``, ``"refutation_count"``, ``"collected_at"``.

        Examples
        --------
        >>> witness = ScopeSemanticsCoordinateFormationWitness()
        >>> bundle = witness.collect_evidence_bundle()
        >>> "witness_id" in bundle
        True
        """
        return {
            "witness_id": self._witness_id,
            "facts": list(self._witnessed_facts),
            "refutations": list(self._refutations),
            "fact_count": len(self._witnessed_facts),
            "refutation_count": len(self._refutations),
            "collected_at": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }

    def refute(self, reason: str, evidence: dict[str, Any]) -> None:
        """Record a refutation of an expected invariant.

        Parameters
        ----------
        reason:
            Human-readable description of what was violated.
        evidence:
            Dict of supporting data (variable values, code attributes, etc.).

        Examples
        --------
        >>> witness = ScopeSemanticsCoordinateFormationWitness()
        >>> witness.refute("test refutation", {"detail": "none"})
        >>> len(witness._refutations)
        1
        """
        self._refutations.append({
            "refutation": reason,
            "evidence": evidence,
            "timestamp": time.monotonic(),
            "witness_id": self._witness_id,
        })
        log.debug(
            "[%s] refutation recorded: %s", _ANALYSIS_CHANNEL, reason
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "ScopeSemanticsCoordinateFormationCoordinator",
    "ScopeSemanticsCoordinateFormationAnalyzer",
    "ScopeSemanticsCoordinateFormationWitness",
    # Helper functions
    "scope_kind_to_coordinate_kind",
    "legb_layer_rank",
    "format_scope_chain",
    "extract_free_vars_from_code",
    "classify_name_in_scope",
    "build_scope_coordinate",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_LEGB_LAYERS",
    "_SCOPE_OPS",
    "_PYTHON_BUILTINS",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import textwrap as _textwrap

    print("=" * 70)
    print("scope_semantics_coordinate_formati — smoke test")
    print("=" * 70)

    # ── 1. Coordinator ──────────────────────────────────────────────────────
    print("\n[1] ScopeSemanticsCoordinateFormationCoordinator")
    coordinator = ScopeSemanticsCoordinateFormationCoordinator()
    coord_mod = coordinator.coordinate_for_scope("mymodule", ScopeKind.MODULE, ())
    coord_fn = coordinator.coordinate_for_scope("foo", ScopeKind.FUNCTION, ("mymodule",))
    coord_inner = coordinator.coordinate_for_scope("bar", ScopeKind.FUNCTION, ("mymodule", "foo"))
    print(f"  module coord  : {coord_mod.components}  kind={coord_mod.kind}")
    print(f"  function coord: {coord_fn.components}  kind={coord_fn.kind}")
    print(f"  inner coord   : {coord_inner.components}  kind={coord_inner.kind}")

    legb = coordinator.form_legb_chain(
        local_names=frozenset({"x", "y"}),
        enclosing_names=frozenset({"y", "z"}),
        global_names=frozenset({"z", "w"}),
        builtin_names=frozenset({"print", "len"}),
    )
    print(f"  LEGB resolution sample: x→{legb['x']}, y→{legb['y']}, z→{legb['z']}, print→{legb['print']}")

    morph = coordinator.coordinate_morphism(coord_inner, coord_mod)
    print(f"  morphism inner→module: kind={morph['kind']}  distance={morph['distance']}")
    summary = coordinator.summary()
    print(f"  coordinator summary: {summary}")
    obligations = coordinator.flush_obligations()
    print(f"  flushed obligations: {len(obligations)}")

    # ── 2. Analyzer ─────────────────────────────────────────────────────────
    print("\n[2] ScopeSemanticsCoordinateFormationAnalyzer")
    sample_source = _textwrap.dedent("""\
        x = 10

        def outer(a, b):
            c = a + b
            def inner(d):
                return c + d + x
            return inner

        class Foo:
            bar = 42
            def method(self):
                return self.bar
    """)
    analyzer = ScopeSemanticsCoordinateFormationAnalyzer()
    analysis = analyzer.analyze_source(sample_source, module_name="smoke_test")
    print(f"  scopes found   : {analysis['scopes']}")
    print(f"  obligations    : {len(analysis['obligations'])}")
    print(f"  stats          : {analysis['stats']}")

    # Demonstrate disassemble on a real function.
    def _demo_closure():
        captured = [1, 2, 3]
        def _inner(n):
            return captured[n]
        return _inner

    inner_fn = _demo_closure()
    ops = analyzer.disassemble_scope_ops(inner_fn)
    print(f"  scope ops in _inner: {[op['opname'] for op in ops]}")
    cell_info = analyzer.analyze_cell_variables(inner_fn)
    print(f"  cell_info for _inner: freevars={cell_info['freevars']}  has_closure={cell_info['has_closure']}")

    # ── 3. Witness ───────────────────────────────────────────────────────────
    print("\n[3] ScopeSemanticsCoordinateFormationWitness")
    witness = ScopeSemanticsCoordinateFormationWitness()

    def _witnessed_func(x, y=0):
        total = x + y
        return total

    record = witness.witness_scope_formation(_witnessed_func)
    print(f"  witnessed func  : {record['func_name']}  ops={len(record['scope_ops'])}")
    print(f"  legb coords     : {record['legb_coords']}")

    c_for_invariant = build_scope_coordinate(("smoke_test",), "module")
    ok = witness.witness_coordinate_invariant(c_for_invariant, CoordinateKind.MODULE)
    print(f"  invariant check (MODULE): {ok}")

    layer, val = witness.witness_legb_resolution(
        "x",
        local_ns={"x": 99},
        global_ns={"y": 0},
        builtin_ns={"print": print},
    )
    print(f"  LEGB lookup 'x': layer={layer!r}  value={val}")

    bundle = witness.collect_evidence_bundle()
    print(f"  evidence bundle : facts={bundle['fact_count']}  refutations={bundle['refutation_count']}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Smoke test complete — all three classes exercised successfully.")
    print("=" * 70)
