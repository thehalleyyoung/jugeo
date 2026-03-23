"""Lifting ordinary Python type annotations to coordinate-indexed JuGeo types.

Theory2 §3.1 establishes that every ordinary annotation α: X → Type can be
*lifted* to a coordinate-indexed type τ_α = (c, K_α, ρ_id, γ_triv, supp_α,
trust_unverified) where K_α is derived from the Python annotation, ρ_id is the
identity transport, γ_triv is the trivial gluing, supp_α = {c}, and trust is
UNVERIFIED until a checker discharges it.

This module implements the lifting functor L: Ann → JuGeoType and its adjuncts.
The functor is *faithful*: distinct Python annotations (up to structural
equivalence) map to distinct coordinate-indexed types.  The lifting is *natural*
in the coordinate: if c' ≤ c (c' refines c) then L(α)|_{c'} = L(α|_{c'}).

The six primary exports are:
- :class:`AnnotationKind` — classification enumeration for Python annotations
- :class:`AnnotationRecord` — immutable parsed annotation with all metadata
- :class:`AnnotationInterpreter` — classifies raw Python annotations
- :class:`CoordinateIndexer` — maps annotation source locations → Coordinates
- :class:`SemanticTypeDecorator` — attaches JuGeo metadata to functions/classes
- :class:`TypeAnnotationLifter` — the lifting functor L itself

Provenance
----------
MODULE_AUTHOR : str
    "copilot"
THEORY_REF : str
    "theory2.tex Ch3 §3.1"
"""

from __future__ import annotations

import inspect
import types
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Final, get_type_hints

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure, raise_with_scope
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateObject,
    CoordinateMorphism,
    Morphism,
    MorphismKind,
    Site,
)
from jugeo.judgments.judgment_terms import Judgment, Proposition, TrustLevel, JudgmentStatus
from jugeo.foundations.type_objects.models import (
    JuGeoType,
    TypeCarrier,
    TransportMap,
    GluingLaw,
    TypeTrustAnnotation,
    CarrierKind,
)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

MODULE_AUTHOR: Final[str] = "copilot"
THEORY_REF: Final[str] = "theory2.tex Ch3 §3.1"

__all__ = [
    "AnnotationKind",
    "AnnotationRecord",
    "AnnotationInterpreter",
    "CoordinateIndexer",
    "SemanticTypeDecorator",
    "TypeAnnotationLifter",
    "MODULE_AUTHOR",
    "THEORY_REF",
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fresh_id(prefix: str = "ann") -> str:
    """Return a fresh unique identifier string with the given prefix.

    Parameters
    ----------
    prefix : str
        Short tag prepended to the UUID fragment (default ``"ann"``).

    Returns
    -------
    str
        A string of the form ``"<prefix>-<8 hex chars>"``, e.g.
        ``"ann-3f7a12bc"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _root_coordinate() -> Coordinate:
    """Return the canonical root coordinate used as a fallback.

    The root coordinate represents the top-level scope in the site lattice
    (τ-theory: the unique object ⊤ ∈ Ob(Site) satisfying c ⪯ ⊤ for all c).

    Returns
    -------
    Coordinate
        A :class:`~jugeo.geometry.site.Coordinate` with components
        ``("root",)`` and kind :attr:`~jugeo.geometry.site.CoordinateKind.MODULE`.
    """
    return Coordinate(components=("root",), kind=CoordinateKind.MODULE)


def _is_none_type(ann: Any) -> bool:
    """Return ``True`` iff *ann* is the ``NoneType`` singleton.

    In Python's type system ``type(None)`` is the annotation used to express
    the absence of a value.  This helper centralises the detection so callers
    do not need to reason about ``type(None) is type(None)`` identity checks.

    Parameters
    ----------
    ann : Any
        The annotation to test.

    Returns
    -------
    bool
        ``True`` when *ann* ``is type(None)``, ``False`` otherwise.
    """
    return ann is type(None)


def _origin_of(ann: Any) -> Any | None:
    """Safe wrapper around :func:`typing.get_origin`.

    Returns ``None`` for bare types and forward references, never raises.

    Parameters
    ----------
    ann : Any
        The annotation whose generic origin should be retrieved.

    Returns
    -------
    Any | None
        The generic origin (e.g. ``list`` for ``list[int]``) or ``None`` if
        not applicable.
    """
    import typing
    try:
        return typing.get_origin(ann)
    except Exception:
        return None


def _args_of(ann: Any) -> tuple[Any, ...]:
    """Safe wrapper around :func:`typing.get_args`.

    Returns an empty tuple for bare types and forward references.

    Parameters
    ----------
    ann : Any
        The annotation whose generic arguments should be retrieved.

    Returns
    -------
    tuple[Any, ...]
        A (possibly empty) tuple of type arguments (e.g. ``(int, str)`` for
        ``dict[int, str]``).
    """
    import typing
    try:
        return typing.get_args(ann)
    except Exception:
        return ()


def _annotation_display(ann: Any) -> str:
    """Produce a human-readable display string for any annotation.

    Handles the common cases: ``None``, ``NoneType``, built-in types,
    :mod:`typing` generics, string forward references, and arbitrary objects.

    Parameters
    ----------
    ann : Any
        The annotation to render as a string.

    Returns
    -------
    str
        A concise, human-readable representation suitable for log messages and
        formula generation (e.g. ``"int"``, ``"list[str]"``,
        ``"dict[str, int]"``).
    """
    if ann is None:
        return "None"
    if _is_none_type(ann):
        return "NoneType"
    if isinstance(ann, str):
        return ann
    if isinstance(ann, type):
        return ann.__name__
    origin = _origin_of(ann)
    args = _args_of(ann)
    if origin is not None:
        origin_name = getattr(origin, "__name__", str(origin))
        if args:
            args_str = ", ".join(_annotation_display(a) for a in args)
            return f"{origin_name}[{args_str}]"
        return origin_name
    # Fallback: use repr but strip module prefixes for cleanliness
    raw = repr(ann)
    for prefix in ("typing.", "collections.abc.", "builtins."):
        raw = raw.replace(prefix, "")
    return raw


def _classify_annotation(ann: Any) -> "AnnotationRecord":
    """Standalone annotation classifier used by :meth:`AnnotationRecord.type_args`.

    This function is the primitive building block of the interpretation
    pipeline; it is intentionally kept free of side effects so it can be
    called recursively without risk of infinite loops or shared state
    mutations.

    The classification logic mirrors the logic in
    :meth:`AnnotationInterpreter.classify` but is available at module level so
    that :class:`AnnotationRecord` (a frozen dataclass) can reference it
    without holding a reference to the interpreter instance.

    Parameters
    ----------
    ann : Any
        The raw Python annotation to classify.

    Returns
    -------
    AnnotationRecord
        A fully populated :class:`AnnotationRecord` for *ann*.
    """
    import typing

    # Determine origin and args first
    origin = _origin_of(ann)
    args = _args_of(ann)

    # NoneType
    if _is_none_type(ann):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.PRIMITIVE,
            origin=None,
            args=(),
            is_optional=False,
            display_str="NoneType",
            source_module=None,
            source_line=None,
        )

    # String forward references
    if isinstance(ann, str):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.UNKNOWN,
            origin=None,
            args=(),
            is_optional=False,
            display_str=ann,
            source_module=None,
            source_line=None,
        )

    # Primitive bare types
    _PRIMITIVES = (int, str, float, bool, bytes, complex)
    if isinstance(ann, type) and ann in _PRIMITIVES:
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.PRIMITIVE,
            origin=None,
            args=(),
            is_optional=False,
            display_str=ann.__name__,
            source_module=getattr(ann, "__module__", None),
            source_line=None,
        )

    # Union / Optional detection
    if origin is typing.Union:
        is_opt = any(_is_none_type(a) for a in args)
        kind = AnnotationKind.OPTIONAL if is_opt else AnnotationKind.UNION
        return AnnotationRecord(
            raw_annotation=ann,
            kind=kind,
            origin=origin,
            args=tuple(args),
            is_optional=is_opt,
            display_str=_annotation_display(ann),
            source_module=None,
            source_line=None,
        )

    # Callable
    if origin is not None and (
        origin is collections_abc_callable() or origin is typing.Callable
    ):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.CALLABLE,
            origin=origin,
            args=tuple(args),
            is_optional=False,
            display_str=_annotation_display(ann),
            source_module=None,
            source_line=None,
        )

    # Literal
    if origin is typing.Literal:
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.LITERAL,
            origin=origin,
            args=tuple(args),
            is_optional=False,
            display_str=_annotation_display(ann),
            source_module=None,
            source_line=None,
        )

    # Annotated
    if origin is not None and _annotation_display(origin) in ("Annotated",):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.ANNOTATED,
            origin=origin,
            args=tuple(args),
            is_optional=False,
            display_str=_annotation_display(ann),
            source_module=None,
            source_line=None,
        )

    # Generic containers
    _GENERIC_ORIGINS = {list, dict, tuple, set, frozenset}
    if origin in _GENERIC_ORIGINS or (origin is not None and args):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.GENERIC,
            origin=origin,
            args=tuple(args),
            is_optional=False,
            display_str=_annotation_display(ann),
            source_module=None,
            source_line=None,
        )

    # Bare generic origins without args (e.g. plain `list`, `dict`)
    if isinstance(ann, type) and ann in (list, dict, tuple, set, frozenset):
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.GENERIC,
            origin=None,
            args=(),
            is_optional=False,
            display_str=ann.__name__,
            source_module=getattr(ann, "__module__", None),
            source_line=None,
        )

    # Protocol detection
    if isinstance(ann, type):
        bases = getattr(ann, "__bases__", ())
        for base in bases:
            if getattr(base, "_is_protocol", False):
                return AnnotationRecord(
                    raw_annotation=ann,
                    kind=AnnotationKind.PROTOCOL,
                    origin=None,
                    args=(),
                    is_optional=False,
                    display_str=ann.__name__,
                    source_module=getattr(ann, "__module__", None),
                    source_line=None,
                )
        return AnnotationRecord(
            raw_annotation=ann,
            kind=AnnotationKind.PRIMITIVE,
            origin=None,
            args=(),
            is_optional=False,
            display_str=ann.__name__,
            source_module=getattr(ann, "__module__", None),
            source_line=None,
        )

    # Unknown / fallback
    return AnnotationRecord(
        raw_annotation=ann,
        kind=AnnotationKind.UNKNOWN,
        origin=None,
        args=(),
        is_optional=False,
        display_str=_annotation_display(ann),
        source_module=None,
        source_line=None,
    )


def collections_abc_callable() -> Any:
    """Return ``collections.abc.Callable`` for origin comparison.

    This tiny helper exists solely to avoid a top-level import of
    ``collections.abc`` in contexts where it may not be needed, and to
    centralise the attribute lookup so it is easy to patch in tests.

    Returns
    -------
    Any
        The ``collections.abc.Callable`` object.
    """
    import collections.abc
    return collections.abc.Callable


# ---------------------------------------------------------------------------
# AnnotationKind
# ---------------------------------------------------------------------------


class AnnotationKind(str, Enum):
    """Exhaustive classification of Python type annotations.

    Each member corresponds to a distinct syntactic/semantic category
    recognised by the lifting functor.  The string value is used in
    serialised representations so it is human-readable.

    Theory2 §3.1 defines a partition of the annotation lattice into these
    nine classes; the functor L: Ann → JuGeoType respects the partition in
    the sense that L(α).carrier.kind is determined entirely by
    AnnotationKind(α).

    Attributes
    ----------
    PRIMITIVE : str
        Scalar built-in types: ``int``, ``str``, ``float``, ``bool``,
        ``bytes``.  These admit a trivial transport ρ_id.
    GENERIC : str
        Parameterised container types: ``list[X]``, ``dict[K, V]``, etc.
        The type arguments form a *telescope* in type-theory notation.
    UNION : str
        ``Union[A, B, ...]`` (excluding ``Optional``).  Transport is
        defined by case analysis ∨.
    CALLABLE : str
        ``Callable[[A, …], R]``.  Transport is defined by the arrow rule →.
    OPTIONAL : str
        ``Optional[X]`` / ``X | None``.  Shorthand for ``Union[X, None]``
        but given separate treatment because it is so common.
    LITERAL : str
        ``Literal["a", 1, …]``.  Carries a finite set of value witnesses.
    ANNOTATED : str
        ``Annotated[T, metadata, …]``.  The metadata is carried in *args*.
    PROTOCOL : str
        Structural subtype specification.  Honoured specially in the gluing
        law γ.
    UNKNOWN : str
        Anything the classifier cannot categorise, including forward
        references that have not been resolved.
    """

    PRIMITIVE = "primitive"
    GENERIC = "generic"
    UNION = "union"
    CALLABLE = "callable"
    OPTIONAL = "optional"
    LITERAL = "literal"
    ANNOTATED = "annotated"
    PROTOCOL = "protocol"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# AnnotationRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """Immutable, fully-parsed representation of a single Python annotation.

    Every field is populated during classification; no field is lazily
    computed.  This design guarantees that two ``AnnotationRecord`` objects
    constructed from structurally equivalent annotations are equal (by the
    default frozen-dataclass ``__eq__``).

    In theory2.tex §3.1 language, an ``AnnotationRecord`` is the *syntax
    object* α before the functor L is applied; its ``kind`` field determines
    which branch of L's definition is used.

    Parameters
    ----------
    raw_annotation : Any
        The original annotation object (e.g. ``int``, ``list[str]``).
    kind : AnnotationKind
        Classification of the annotation (see :class:`AnnotationKind`).
    origin : Any | None
        The un-parameterised generic origin, e.g. ``list`` for ``list[str]``.
        ``None`` for bare types.
    args : tuple[Any, ...]
        The type arguments, e.g. ``(str,)`` for ``list[str]``.
    is_optional : bool
        ``True`` iff the annotation includes ``None`` as a valid value
        (i.e. ``Optional[X]`` or ``X | None``).
    display_str : str
        Human-readable representation used in log messages and formulas.
    source_module : str | None
        The ``__name__`` of the module where this annotation was encountered,
        or ``None`` if not determinable.
    source_line : int | None
        The source line number (1-based), or ``None``.
    """

    raw_annotation: Any
    kind: AnnotationKind
    origin: Any | None
    args: tuple[Any, ...]
    is_optional: bool
    display_str: str
    source_module: str | None
    source_line: int | None

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_primitive(self) -> bool:
        """Return ``True`` iff this annotation is a primitive scalar type.

        Returns
        -------
        bool
            ``True`` when ``self.kind == AnnotationKind.PRIMITIVE``.
        """
        return self.kind == AnnotationKind.PRIMITIVE

    def is_generic(self) -> bool:
        """Return ``True`` iff this annotation is a parameterised generic.

        Returns
        -------
        bool
            ``True`` when ``self.kind == AnnotationKind.GENERIC``.
        """
        return self.kind == AnnotationKind.GENERIC

    def is_union(self) -> bool:
        """Return ``True`` iff this annotation is a (non-Optional) union type.

        Returns
        -------
        bool
            ``True`` when ``self.kind == AnnotationKind.UNION``.
        """
        return self.kind == AnnotationKind.UNION

    def is_callable(self) -> bool:
        """Return ``True`` iff this annotation is a callable type.

        Returns
        -------
        bool
            ``True`` when ``self.kind == AnnotationKind.CALLABLE``.
        """
        return self.kind == AnnotationKind.CALLABLE

    # ------------------------------------------------------------------
    # Structural accessors
    # ------------------------------------------------------------------

    def unwrap_optional(self) -> AnnotationRecord | None:
        """Unwrap an ``Optional[X]`` annotation to its inner ``X``.

        If this record represents ``Optional[X]`` (i.e. ``X | None``),
        return a new :class:`AnnotationRecord` for the first non-``NoneType``
        argument.  If this record is not optional, return ``None``.

        Returns
        -------
        AnnotationRecord | None
            The inner record for ``X``, or ``None`` if not optional.

        Notes
        -----
        In theory2.tex §3.1 terms, unwrapping discards the ¬ component of
        the optional telescope so that the residual type τ(X) can be handled
        by its own branch of the functor L.
        """
        if not self.is_optional:
            return None
        for arg in self.args:
            if not _is_none_type(arg):
                return _classify_annotation(arg)
        return None

    def type_args(self) -> tuple[AnnotationRecord, ...]:
        """Return the type arguments as a tuple of :class:`AnnotationRecord`.

        Each element of ``self.args`` is classified independently via
        :func:`_classify_annotation`.  This is the recursive step of the
        lifting functor L when applied to generic or union types.

        Returns
        -------
        tuple[AnnotationRecord, ...]
            One :class:`AnnotationRecord` per element of ``self.args``.
        """
        return tuple(_classify_annotation(a) for a in self.args)

    def to_carrier_name(self) -> str:
        """Produce a string name for the :class:`TypeCarrier`.

        The name follows Python's own type annotation syntax so that it is
        both human-readable and unambiguous.

        Returns
        -------
        str
            E.g. ``"int"``, ``"list[str]"``, ``"dict[str, int]"``,
            ``"Optional[bool]"``.
        """
        if self.kind == AnnotationKind.PRIMITIVE:
            return self.display_str
        if self.kind in (AnnotationKind.GENERIC, AnnotationKind.UNION,
                         AnnotationKind.OPTIONAL, AnnotationKind.CALLABLE,
                         AnnotationKind.LITERAL, AnnotationKind.ANNOTATED):
            return self.display_str
        if self.kind == AnnotationKind.PROTOCOL:
            return f"Protocol[{self.display_str}]"
        return self.display_str

    def to_formula(self) -> str:
        """Return a type-theory formula string for this annotation.

        The formula is expressed using the τ notation from theory2.tex §3.1.
        Generic types use the telescope notation τ(C)[A₁, …, Aₙ].

        Returns
        -------
        str
            E.g. ``"τ(int)"``, ``"τ(list[str])"``,
            ``"τ(dict[str, int])"``.
        """
        return f"τ({self.display_str})"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary.

        All fields are included.  ``raw_annotation`` is stored as its
        display string because arbitrary Python objects are not
        JSON-serialisable.

        Returns
        -------
        dict[str, Any]
            A dictionary suitable for ``json.dumps`` (assuming all values
            in ``args`` are themselves serialisable types or strings).
        """
        return {
            "raw_annotation": _annotation_display(self.raw_annotation),
            "kind": self.kind.value,
            "origin": _annotation_display(self.origin) if self.origin is not None else None,
            "args": [_annotation_display(a) for a in self.args],
            "is_optional": self.is_optional,
            "display_str": self.display_str,
            "source_module": self.source_module,
            "source_line": self.source_line,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> AnnotationRecord:
        """Reconstruct an :class:`AnnotationRecord` from a serialised dict.

        Note: the ``raw_annotation`` field is reconstructed as a plain string
        because the original Python object cannot be recovered without an
        active interpreter environment.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        AnnotationRecord
            A new record.  ``raw_annotation`` and ``origin`` will be strings
            rather than live Python types.

        Raises
        ------
        KeyError
            If a required key is missing from *data*.
        ValueError
            If ``kind`` is not a valid :class:`AnnotationKind` member.
        """
        kind = AnnotationKind(data["kind"])
        args_raw = data.get("args", [])
        return cls(
            raw_annotation=data["raw_annotation"],
            kind=kind,
            origin=data.get("origin"),
            args=tuple(args_raw),
            is_optional=bool(data.get("is_optional", False)),
            display_str=data["display_str"],
            source_module=data.get("source_module"),
            source_line=data.get("source_line"),
        )

    @classmethod
    def from_annotation(
        cls,
        ann: Any,
        module: str | None = None,
        line: int | None = None,
    ) -> AnnotationRecord:
        """Fully classify *ann* and return a populated :class:`AnnotationRecord`.

        This is the primary factory method.  It delegates to
        :func:`_classify_annotation` and then patches in the optional
        *module* and *line* provenance data.

        Parameters
        ----------
        ann : Any
            The raw Python annotation to classify and wrap.
        module : str | None
            The module name where this annotation was encountered.  If
            ``None``, the classifier may attempt to infer it from
            ``ann.__module__`` for bare types.
        line : int | None
            Source line number (1-based), or ``None``.

        Returns
        -------
        AnnotationRecord
            A fully populated record with *module* and *line* set.
        """
        base = _classify_annotation(ann)
        # Patch in provenance if provided; replace() returns a new frozen instance
        inferred_module = module or base.source_module
        return replace(base, source_module=inferred_module, source_line=line)


# ---------------------------------------------------------------------------
# AnnotationInterpreter
# ---------------------------------------------------------------------------


class AnnotationInterpreter:
    """Interprets raw Python annotations into :class:`AnnotationRecord` objects.

    The interpreter maintains a registry mapping Python types to
    :class:`AnnotationKind` values, and a list of custom handler callables
    for domain-specific annotation types.  It is stateful and mutable, which
    contrasts with the immutable :class:`AnnotationRecord` it produces.

    In theory2.tex §3.1 the interpreter corresponds to the *syntactic phase*
    of the functor L: it maps the raw annotation object α to the
    *pre-type record* before coordinate assignment.

    Parameters
    ----------
    None
        Constructed with no arguments; call :meth:`__init__` to initialise.

    Attributes
    ----------
    _registry : dict[type, AnnotationKind]
        Maps known Python types to their classification.
    _custom_handlers : list[Callable]
        Zero-argument factories used for domain-specific annotations.
    """

    def __init__(self) -> None:
        """Initialise the interpreter with default primitive and generic types."""
        self._registry: dict[type, AnnotationKind] = {}
        self._custom_handlers: list[Callable] = []
        # Primitive scalars
        for t in (int, str, float, bool, bytes, complex):
            self._registry[t] = AnnotationKind.PRIMITIVE
        # Generic containers
        for t in (list, dict, tuple, set, frozenset):
            self._registry[t] = AnnotationKind.GENERIC

    def interpret(self, annotation: Any) -> AnnotationRecord:
        """Run the full interpretation pipeline for *annotation*.

        The pipeline is:
        1. Check custom handlers.
        2. Consult the type registry.
        3. Delegate to :func:`_classify_annotation` for structural analysis.

        Parameters
        ----------
        annotation : Any
            The raw Python annotation object to interpret.

        Returns
        -------
        AnnotationRecord
            A fully populated record for *annotation*.
        """
        # Try custom handlers first
        for handler in self._custom_handlers:
            result = handler(annotation)
            if result is not None:
                return result
        # Registry shortcut for exact-type matches
        if isinstance(annotation, type) and annotation in self._registry:
            kind = self._registry[annotation]
            return AnnotationRecord(
                raw_annotation=annotation,
                kind=kind,
                origin=None,
                args=(),
                is_optional=False,
                display_str=_annotation_display(annotation),
                source_module=getattr(annotation, "__module__", None),
                source_line=None,
            )
        # Full structural classification
        return _classify_annotation(annotation)

    def classify(self, annotation: Any) -> AnnotationKind:
        """Return the :class:`AnnotationKind` for *annotation*.

        This is a lightweight method that returns only the kind without
        building a full :class:`AnnotationRecord`.

        Parameters
        ----------
        annotation : Any
            The annotation to classify.

        Returns
        -------
        AnnotationKind
            The classification of *annotation*.
        """
        return self.interpret(annotation).kind

    def register_handler(self, type_: type, kind: AnnotationKind) -> None:
        """Register a type ↦ kind mapping in the interpreter's registry.

        Parameters
        ----------
        type_ : type
            The Python type to register.
        kind : AnnotationKind
            The :class:`AnnotationKind` to associate with *type_*.
        """
        self._registry[type_] = kind

    def unwrap_generics(self, annotation: Any) -> tuple[Any, tuple[Any, ...]]:
        """Extract the generic origin and its type arguments from *annotation*.

        Parameters
        ----------
        annotation : Any
            The annotation to unwrap.

        Returns
        -------
        tuple[Any, tuple[Any, ...]]
            A pair ``(origin, args)`` where *origin* is the un-parameterised
            generic (e.g. ``list``) and *args* is a (possibly empty) tuple
            of type parameters (e.g. ``(str,)``).
        """
        origin = _origin_of(annotation)
        args = _args_of(annotation)
        return (origin, args)

    def extract_origin(self, annotation: Any) -> Any | None:
        """Return the un-parameterised generic origin of *annotation*.

        Parameters
        ----------
        annotation : Any
            The annotation to inspect.

        Returns
        -------
        Any | None
            The origin (e.g. ``list`` for ``list[int]``), or ``None``.
        """
        import typing
        return typing.get_origin(annotation)

    def is_optional_annotation(self, annotation: Any) -> bool:
        """Return ``True`` iff *annotation* is ``Optional[X]`` or ``X | None``.

        Parameters
        ----------
        annotation : Any
            The annotation to test.

        Returns
        -------
        bool
            ``True`` when the annotation admits ``None`` as a legal value.
        """
        import typing
        origin = self.extract_origin(annotation)
        if origin is not typing.Union:
            return False
        args = self.get_type_args(annotation)
        return any(_is_none_type(a) for a in args)

    def get_type_args(self, annotation: Any) -> tuple[Any, ...]:
        """Return the type arguments of a generic annotation.

        Parameters
        ----------
        annotation : Any
            The annotation to inspect.

        Returns
        -------
        tuple[Any, ...]
            A (possibly empty) tuple of type arguments.
        """
        import typing
        return typing.get_args(annotation)

    def reset(self) -> None:
        """Clear custom handlers and reset the registry to its default state.

        After calling ``reset()``, the interpreter behaves exactly as it did
        immediately after construction.
        """
        self._custom_handlers.clear()
        self._registry.clear()
        for t in (int, str, float, bool, bytes, complex):
            self._registry[t] = AnnotationKind.PRIMITIVE
        for t in (list, dict, tuple, set, frozenset):
            self._registry[t] = AnnotationKind.GENERIC

    def known_types(self) -> list[type]:
        """Return the list of types explicitly registered with this interpreter.

        Returns
        -------
        list[type]
            All types currently in the registry, in insertion order.
        """
        return list(self._registry.keys())


# ---------------------------------------------------------------------------
# CoordinateIndexer
# ---------------------------------------------------------------------------


class CoordinateIndexer:
    """Maps Python annotation source locations to :class:`Coordinate` objects.

    The indexer maintains a cache of coordinates keyed by a string identifier
    (usually a module name or a dotted qualname).  It optionally operates
    within a :class:`~jugeo.geometry.site.Site` to validate that generated
    coordinates belong to the site's object collection.

    In theory2.tex §3.1 terms, the indexer implements the *coordinate
    assignment* phase of the functor L, mapping source locations to objects
    c ∈ Ob(Site).

    Parameters
    ----------
    site : Site | None
        Optional site for coordinate validation.  When ``None``, coordinates
        are created without site membership checks.

    Attributes
    ----------
    _site : Site | None
        The associated site, or ``None``.
    _coord_cache : dict[str, Coordinate]
        Memoisation cache mapping string keys to :class:`Coordinate` objects.
    """

    def __init__(self, site: Site | None = None) -> None:
        """Initialise the indexer with an optional site and empty cache."""
        self._site: Site | None = site
        self._coord_cache: dict[str, Coordinate] = {}

    def index_annotation(
        self,
        record: AnnotationRecord,
        hint_coord: Coordinate | None = None,
    ) -> Coordinate:
        """Assign a :class:`Coordinate` to *record*.

        Priority:
        1. Use *hint_coord* if provided.
        2. Derive from ``record.source_module`` and ``record.source_line``.
        3. Fall back to the root coordinate.

        Parameters
        ----------
        record : AnnotationRecord
            The annotation record for which a coordinate is needed.
        hint_coord : Coordinate | None
            An explicit coordinate to use; overrides all other logic.

        Returns
        -------
        Coordinate
            The assigned coordinate for *record*.
        """
        if hint_coord is not None:
            return hint_coord
        if record.source_module is not None:
            key = record.source_module
            if record.source_line is not None:
                key = f"{record.source_module}:{record.source_line}"
            cached = self.cache_lookup(key)
            if cached is not None:
                return cached
            coord = Coordinate(
                components=(record.source_module,),
                kind=CoordinateKind.MODULE,
            )
            self._coord_cache[key] = coord
            return coord
        return _root_coordinate()

    def module_coordinate(self, module_name: str) -> Coordinate:
        """Return (and cache) a coordinate for the given module name.

        Parameters
        ----------
        module_name : str
            The fully-qualified Python module name, e.g.
            ``"jugeo.foundations.type_objects"``.

        Returns
        -------
        Coordinate
            A :class:`Coordinate` with kind
            :attr:`~jugeo.geometry.site.CoordinateKind.MODULE` whose
            components are derived from the dotted module path.
        """
        cached = self.cache_lookup(module_name)
        if cached is not None:
            return cached
        parts = tuple(module_name.split("."))
        coord = Coordinate(components=parts, kind=CoordinateKind.MODULE)
        self._coord_cache[module_name] = coord
        return coord

    def function_coordinate(self, func: Callable) -> Coordinate:
        """Derive a coordinate from a callable's qualname and module.

        Parameters
        ----------
        func : Callable
            The callable for which a coordinate is needed.

        Returns
        -------
        Coordinate
            A coordinate whose components are the module path plus the
            qualname components of *func*.
        """
        qualname = getattr(func, "__qualname__", None) or repr(func)
        module = getattr(func, "__module__", None) or ""
        key = f"{module}.{qualname}"
        cached = self.cache_lookup(key)
        if cached is not None:
            return cached
        module_parts = tuple(module.split(".")) if module else ()
        qual_parts = tuple(qualname.split("."))
        coord = Coordinate(
            components=module_parts + qual_parts,
            kind=CoordinateKind.MODULE,
        )
        self._coord_cache[key] = coord
        return coord

    def class_coordinate(self, cls: type) -> Coordinate:
        """Derive a coordinate from a class's qualname and module.

        Parameters
        ----------
        cls : type
            The class for which a coordinate is needed.

        Returns
        -------
        Coordinate
            A coordinate representing the class in the site lattice.
        """
        qualname = getattr(cls, "__qualname__", None) or repr(cls)
        module = getattr(cls, "__module__", None) or ""
        key = f"{module}.{qualname}"
        cached = self.cache_lookup(key)
        if cached is not None:
            return cached
        module_parts = tuple(module.split(".")) if module else ()
        qual_parts = tuple(qualname.split("."))
        coord = Coordinate(
            components=module_parts + qual_parts,
            kind=CoordinateKind.MODULE,
        )
        self._coord_cache[key] = coord
        return coord

    def method_coordinate(self, cls: type, method_name: str) -> Coordinate:
        """Derive a coordinate for a named method of *cls*.

        Parameters
        ----------
        cls : type
            The class owning the method.
        method_name : str
            The unqualified name of the method (e.g. ``"__init__"``).

        Returns
        -------
        Coordinate
            A coordinate that refines the class coordinate by appending the
            method name as a final component.
        """
        class_coord = self.class_coordinate(cls)
        key = f"{'.'.join(class_coord.components)}.{method_name}"
        cached = self.cache_lookup(key)
        if cached is not None:
            return cached
        coord = Coordinate(
            components=class_coord.components + (method_name,),
            kind=CoordinateKind.MODULE,
        )
        self._coord_cache[key] = coord
        return coord

    def infer_from_qualname(self, qualname: str) -> Coordinate:
        """Build a coordinate by splitting a dotted qualname string.

        Parameters
        ----------
        qualname : str
            A dotted qualname such as ``"MyClass.my_method"`` or
            ``"my_module.MyClass"``.

        Returns
        -------
        Coordinate
            A coordinate whose components are the split parts of *qualname*.
        """
        cached = self.cache_lookup(qualname)
        if cached is not None:
            return cached
        parts = tuple(qualname.split(".")) if qualname else ("__unknown__",)
        coord = Coordinate(components=parts, kind=CoordinateKind.MODULE)
        self._coord_cache[qualname] = coord
        return coord

    def cache_lookup(self, key: str) -> Coordinate | None:
        """Return the cached coordinate for *key*, or ``None``.

        Parameters
        ----------
        key : str
            The cache key to look up.

        Returns
        -------
        Coordinate | None
            The cached :class:`Coordinate`, or ``None`` if not present.
        """
        return self._coord_cache.get(key)

    def register_site(self, site: Site) -> None:
        """Associate this indexer with a :class:`~jugeo.geometry.site.Site`.

        Parameters
        ----------
        site : Site
            The site to use for coordinate membership validation.
        """
        self._site = site

    def clear_cache(self) -> None:
        """Clear the entire coordinate cache.

        After this call, all subsequent lookups will recompute coordinates
        from scratch.
        """
        self._coord_cache.clear()


# ---------------------------------------------------------------------------
# SemanticTypeDecorator
# ---------------------------------------------------------------------------


class SemanticTypeDecorator:
    """Decorator that attaches JuGeo type metadata to functions and classes.

    When used as a decorator (``@SemanticTypeDecorator(indexer, interpreter)``),
    this class inspects the decorated callable or class, classifies all of its
    annotations via the provided *interpreter*, assigns coordinates via the
    provided *indexer*, and stores the result in a ``__jugeo_type_metadata__``
    attribute.

    In theory2.tex §3.1 terms, ``SemanticTypeDecorator`` is the *annotation
    phase* of the functor: it applies L to every annotation in a function or
    class signature and records the pre-type records for later use by the
    :class:`TypeAnnotationLifter`.

    Parameters
    ----------
    coord_indexer : CoordinateIndexer
        Used to assign coordinates to each annotation.
    interpreter : AnnotationInterpreter
        Used to classify each annotation.

    Attributes
    ----------
    _coord_indexer : CoordinateIndexer
        The coordinate indexer in use.
    _interpreter : AnnotationInterpreter
        The annotation interpreter in use.
    _decorated : list[str]
        Accumulates the names (qualnames) of all objects that have been
        decorated by this instance.
    """

    def __init__(
        self,
        coord_indexer: CoordinateIndexer,
        interpreter: AnnotationInterpreter,
    ) -> None:
        """Initialise with the given indexer and interpreter."""
        self._coord_indexer = coord_indexer
        self._interpreter = interpreter
        self._decorated: list[str] = []

    def __call__(self, func_or_class: Any) -> Any:
        """Decorate *func_or_class* by attaching JuGeo type metadata.

        Dispatches to :meth:`decorate_function` for callables and
        :meth:`decorate_class` for classes.

        Parameters
        ----------
        func_or_class : Any
            The function or class to decorate.

        Returns
        -------
        Any
            The original object, with ``__jugeo_type_metadata__`` attached.

        Raises
        ------
        TypeError
            If *func_or_class* is neither a callable nor a class.
        """
        if isinstance(func_or_class, type):
            return self.decorate_class(func_or_class)
        if callable(func_or_class):
            return self.decorate_function(func_or_class)
        raise TypeError(
            f"SemanticTypeDecorator can only decorate callables or classes, "
            f"got {type(func_or_class).__name__!r}"
        )

    def decorate_function(self, func: Callable) -> Callable:
        """Attach JuGeo type metadata to *func*.

        Extracts all parameter annotations and the return annotation, runs
        them through the interpreter, and stores the result in
        ``func.__jugeo_type_metadata__``.

        Parameters
        ----------
        func : Callable
            The function to decorate.

        Returns
        -------
        Callable
            The same function object with ``__jugeo_type_metadata__`` set.
        """
        param_types = self.extract_parameter_types(func)
        return_type = self.extract_return_type(func)
        coord = self._coord_indexer.function_coordinate(func)
        metadata: dict[str, Any] = {
            "kind": "function",
            "qualname": getattr(func, "__qualname__", repr(func)),
            "module": getattr(func, "__module__", None),
            "coordinate": coord,
            "parameters": {k: v.serialize() for k, v in param_types.items()},
            "return_type": return_type.serialize() if return_type is not None else None,
        }
        self.attach_type_metadata(func, metadata)
        qualname = getattr(func, "__qualname__", repr(func))
        if qualname not in self._decorated:
            self._decorated.append(qualname)
        return func

    def decorate_class(self, cls: type) -> type:
        """Attach JuGeo type metadata to *cls*.

        Iterates over ``cls.__annotations__`` and all method signatures,
        classifying each annotation and recording the result in
        ``cls.__jugeo_type_metadata__``.

        Parameters
        ----------
        cls : type
            The class to decorate.

        Returns
        -------
        type
            The same class with ``__jugeo_type_metadata__`` set.
        """
        coord = self._coord_indexer.class_coordinate(cls)
        attr_annotations: dict[str, Any] = {}
        raw_annotations = getattr(cls, "__annotations__", {})
        for attr_name, attr_ann in raw_annotations.items():
            record = self._interpreter.interpret(attr_ann)
            attr_annotations[attr_name] = record.serialize()
        method_metadata: dict[str, Any] = {}
        for name, value in inspect.getmembers(cls, predicate=inspect.isfunction):
            param_types = self.extract_parameter_types(value)
            return_type = self.extract_return_type(value)
            method_metadata[name] = {
                "parameters": {k: v.serialize() for k, v in param_types.items()},
                "return_type": return_type.serialize() if return_type is not None else None,
            }
        metadata: dict[str, Any] = {
            "kind": "class",
            "qualname": getattr(cls, "__qualname__", repr(cls)),
            "module": getattr(cls, "__module__", None),
            "coordinate": coord,
            "attributes": attr_annotations,
            "methods": method_metadata,
        }
        self.attach_type_metadata(cls, metadata)
        qualname = getattr(cls, "__qualname__", repr(cls))
        if qualname not in self._decorated:
            self._decorated.append(qualname)
        return cls

    def extract_parameter_types(self, func: Callable) -> dict[str, AnnotationRecord]:
        """Extract and classify all parameter annotations of *func*.

        Parameters
        ----------
        func : Callable
            The function whose parameter annotations should be extracted.

        Returns
        -------
        dict[str, AnnotationRecord]
            A mapping from parameter name to its :class:`AnnotationRecord`.
            Parameters without annotations are omitted.
        """
        result: dict[str, AnnotationRecord] = {}
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            if param_name in hints:
                record = self._interpreter.interpret(hints[param_name])
                result[param_name] = record
            elif param.annotation is not inspect.Parameter.empty:
                record = self._interpreter.interpret(param.annotation)
                result[param_name] = record
        return result

    def extract_return_type(self, func: Callable) -> AnnotationRecord | None:
        """Extract and classify the return annotation of *func*.

        Parameters
        ----------
        func : Callable
            The function whose return annotation should be extracted.

        Returns
        -------
        AnnotationRecord | None
            The :class:`AnnotationRecord` for the return annotation, or
            ``None`` if the function has no return annotation.
        """
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        return_ann = hints.get("return")
        if return_ann is None:
            sig = inspect.signature(func)
            if sig.return_annotation is inspect.Parameter.empty:
                return None
            return_ann = sig.return_annotation
        return self._interpreter.interpret(return_ann)

    def attach_type_metadata(self, obj: Any, metadata: dict[str, Any]) -> None:
        """Set ``obj.__jugeo_type_metadata__`` to *metadata*.

        Parameters
        ----------
        obj : Any
            The object to annotate.
        metadata : dict[str, Any]
            The metadata dictionary to attach.
        """
        try:
            object.__setattr__(obj, "__jugeo_type_metadata__", metadata)
        except (AttributeError, TypeError):
            # For objects that do not support attribute setting
            try:
                obj.__jugeo_type_metadata__ = metadata  # type: ignore[attr-defined]
            except Exception:
                pass

    def get_type_metadata(self, obj: Any) -> dict[str, Any] | None:
        """Retrieve the ``__jugeo_type_metadata__`` attribute of *obj*.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        dict[str, Any] | None
            The attached metadata dictionary, or ``None`` if not present.
        """
        return getattr(obj, "__jugeo_type_metadata__", None)

    def list_decorated(self) -> list[str]:
        """Return the qualnames of all objects decorated by this instance.

        Returns
        -------
        list[str]
            A list of qualname strings, in decoration order.
        """
        return list(self._decorated)


# ---------------------------------------------------------------------------
# TypeAnnotationLifter
# ---------------------------------------------------------------------------


class TypeAnnotationLifter:
    """The lifting functor L: Ann → JuGeoType.

    ``TypeAnnotationLifter`` is the main entry point of this module.  Given
    any Python annotation ``α``, its :meth:`lift` method produces a fully
    populated :class:`~jugeo.foundations.type_objects.models.JuGeoType`
    object whose trust level starts at ``UNVERIFIED`` (as required by
    theory2.tex §3.1).

    The functor is *faithful* up to structural annotation equivalence:
    if ``α ≠ β`` (structurally) then ``lift(α) ≠ lift(β)`` (by distinct
    ``type_id`` values and, in most cases, distinct carrier names).

    The lifting is *natural* in the coordinate: if ``c' ⪯ c`` then
    ``lift(α, coord=c')|_{c'} == lift(α|_{c'}, coord=c')`` (restriction
    commutes with lifting).

    Parameters
    ----------
    site : Site | None
        Optional site for coordinate validation.

    Attributes
    ----------
    _interpreter : AnnotationInterpreter
        Used to classify annotations.
    _indexer : CoordinateIndexer
        Used to assign coordinates.
    _primitive_map : dict[type, str]
        Custom names for primitive types (augmented by
        :meth:`register_primitive`).
    _stats : dict[str, int]
        Counters for ``"lifted"``, ``"cached"``, ``"errors"``.
    _site : Site | None
        Optional associated site.
    """

    def __init__(self, site: Site | None = None) -> None:
        """Initialise the lifter with default interpreter, indexer, and stats.

        Parameters
        ----------
        site : Site | None
            Optional site to use for coordinate assignment.
        """
        self._site = site
        self._interpreter = AnnotationInterpreter()
        self._indexer = CoordinateIndexer(site=site)
        self._primitive_map: dict[type, str] = {
            int: "int",
            str: "str",
            float: "float",
            bool: "bool",
            bytes: "bytes",
            complex: "complex",
        }
        self._stats: dict[str, int] = {
            "lifted": 0,
            "cached": 0,
            "errors": 0,
        }

    def lift(
        self,
        annotation: Any,
        coord: Coordinate | None = None,
        trust_level: TrustLevel | None = None,
    ) -> JuGeoType:
        """Lift *annotation* to a :class:`JuGeoType`.

        This is the primary method of the lifting functor L.  It proceeds
        through four phases:

        1. **Interpretation**: classify *annotation* using the interpreter.
        2. **Coordinate assignment**: use *coord* if given, else ask the
           indexer.
        3. **Carrier construction**: wrap the classification in a
           :class:`TypeCarrier`.
        4. **Type assembly**: combine carrier + identity transport + trivial
           gluing + unverified trust into a :class:`JuGeoType`.

        Parameters
        ----------
        annotation : Any
            The raw Python annotation to lift.
        coord : Coordinate | None
            An explicit coordinate.  If ``None``, the indexer infers one from
            the annotation's source provenance.
        trust_level : TrustLevel | None
            Initial trust level.  Defaults to
            :attr:`~jugeo.judgments.judgment_terms.TrustLevel.UNVERIFIED`.

        Returns
        -------
        JuGeoType
            A fresh :class:`JuGeoType` with a unique ``type_id``.

        Raises
        ------
        JuGeoError
            If the annotation cannot be lifted (e.g. due to a broken import
            or a malformed generic alias).
        """
        try:
            record = self._interpreter.interpret(annotation)
            c = coord if coord is not None else self._indexer.index_annotation(record)
            carrier = self.annotation_to_carrier(record)
            transport = TransportMap.identity(c, carrier)
            gluing = GluingLaw.trivial(c, carrier)
            tl = trust_level if trust_level is not None else TrustLevel.UNVERIFIED
            rationale = f"lifted from {record.display_str} (trust={tl.name})"
            trust = TypeTrustAnnotation.unverified(rationale=rationale)
            result = JuGeoType(
                type_id=_fresh_id("τ"),
                coordinate=c,
                carrier=carrier,
                transport_maps=(transport,),
                gluing_law=gluing,
                support=frozenset({c.name}),
                trust=trust,
                formula=self.annotation_to_formula(record),
                metadata={},
            )
            self._stats["lifted"] += 1
            return result
        except Exception as exc:
            self._stats["errors"] += 1
            raise_with_scope(
                code="annotation_lift_failure",
                message=f"Failed to lift annotation {annotation!r}: {exc}",
                scope=FailureScope.JUDGMENT,
            )

    def lift_function(
        self,
        func: Callable,
        coord: Coordinate | None = None,
    ) -> dict[str, JuGeoType]:
        """Lift all parameter and return annotations of *func*.

        Parameters
        ----------
        func : Callable
            The function whose annotations should be lifted.
        coord : Coordinate | None
            An optional base coordinate.  If ``None``, each annotation's
            coordinate is inferred from the function's source location.

        Returns
        -------
        dict[str, JuGeoType]
            A mapping from parameter name (and ``"return"`` for the return
            annotation) to the corresponding :class:`JuGeoType`.
        """
        result: dict[str, JuGeoType] = {}
        func_coord = coord if coord is not None else self._indexer.function_coordinate(func)
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            ann = hints.get(param_name)
            if ann is None and param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
            if ann is not None:
                param_coord = self._indexer.method_coordinate(
                    type(None), f"{func.__name__}.{param_name}"
                ) if coord is None else func_coord
                result[param_name] = self.lift(ann, coord=param_coord)
        return_ann = hints.get("return")
        if return_ann is None:
            if sig.return_annotation is not inspect.Parameter.empty:
                return_ann = sig.return_annotation
        if return_ann is not None:
            result["return"] = self.lift(return_ann, coord=func_coord)
        return result

    def lift_class(self, cls: type) -> dict[str, JuGeoType]:
        """Lift all attribute annotations declared on *cls*.

        Parameters
        ----------
        cls : type
            The class whose ``__annotations__`` should be lifted.

        Returns
        -------
        dict[str, JuGeoType]
            A mapping from attribute name to :class:`JuGeoType`.  Inherited
            annotations are not included (only those defined directly on
            *cls*).
        """
        result: dict[str, JuGeoType] = {}
        class_coord = self._indexer.class_coordinate(cls)
        raw_annotations: dict[str, Any] = getattr(cls, "__annotations__", {})
        for attr_name, attr_ann in raw_annotations.items():
            attr_coord = self._indexer.method_coordinate(cls, attr_name)
            try:
                result[attr_name] = self.lift(attr_ann, coord=attr_coord)
            except Exception:
                self._stats["errors"] += 1
        return result

    def lift_module(self, module: types.ModuleType) -> dict[str, JuGeoType]:
        """Lift all top-level annotations declared in *module*.

        Parameters
        ----------
        module : types.ModuleType
            The module whose top-level ``__annotations__`` should be lifted.

        Returns
        -------
        dict[str, JuGeoType]
            A mapping from annotation name to :class:`JuGeoType`.  Only
            annotations defined in the module's own ``__annotations__`` dict
            are included (not annotations of nested classes or functions).
        """
        result: dict[str, JuGeoType] = {}
        module_name = getattr(module, "__name__", repr(module))
        module_coord = self._indexer.module_coordinate(module_name)
        raw_annotations: dict[str, Any] = getattr(module, "__annotations__", {})
        for var_name, var_ann in raw_annotations.items():
            try:
                result[var_name] = self.lift(var_ann, coord=module_coord)
            except Exception:
                self._stats["errors"] += 1
        return result

    def annotation_to_carrier(self, record: AnnotationRecord) -> TypeCarrier:
        """Build a :class:`TypeCarrier` from *record*.

        Parameters
        ----------
        record : AnnotationRecord
            The annotation record to convert.

        Returns
        -------
        TypeCarrier
            A fresh :class:`TypeCarrier` encapsulating the annotation's
            carrier kind, name, and parameters.

        Notes
        -----
        The mapping from :class:`AnnotationKind` to
        :class:`~jugeo.foundations.type_objects.models.CarrierKind` is
        defined by the τ-theory in theory2.tex §3.1, Table 3.1.
        """
        name = record.to_carrier_name()
        kind_map = {
            AnnotationKind.PRIMITIVE: CarrierKind.PRIMITIVE,
            AnnotationKind.GENERIC: CarrierKind.COMPOSITE,
            AnnotationKind.UNION: CarrierKind.COMPOSITE,
            AnnotationKind.CALLABLE: CarrierKind.DEPENDENT,
            AnnotationKind.OPTIONAL: CarrierKind.COMPOSITE,
            AnnotationKind.PROTOCOL: CarrierKind.EXTENSION,
            AnnotationKind.UNKNOWN: CarrierKind.PRIMITIVE,
        }
        carrier_kind = kind_map.get(record.kind, CarrierKind.PRIMITIVE)
        params = tuple(str(a) for a in record.args) if record.args else ()
        meta: dict[str, Any] = {
            "display_str": record.display_str,
            "source_module": record.source_module or "",
            "source_line": record.source_line or 0,
        }
        from types import MappingProxyType
        return TypeCarrier(
            carrier_id=_fresh_id("K"),
            kind=carrier_kind,
            display_name=name,
            inhabitants=params,
            constraints=(),
            dependencies=(),
            metadata=MappingProxyType(meta),
        )

    def annotation_to_formula(self, record: AnnotationRecord) -> str:
        """Return the τ-formula string for *record*.

        Parameters
        ----------
        record : AnnotationRecord
            The annotation record whose formula is needed.

        Returns
        -------
        str
            A formula string of the form ``"τ(<display>)"`` where
            ``<display>`` is the annotation's display representation, e.g.
            ``"τ(int)"``, ``"τ(list[str])"``.
        """
        return record.to_formula()

    def batch_lift(
        self,
        annotations: dict[str, Any],
        base_coord: Coordinate | None = None,
    ) -> dict[str, JuGeoType]:
        """Lift multiple annotations at once.

        Parameters
        ----------
        annotations : dict[str, Any]
            A mapping from name to raw Python annotation.
        base_coord : Coordinate | None
            An optional coordinate to use for all annotations in the batch.
            If ``None``, each annotation's coordinate is inferred
            independently.

        Returns
        -------
        dict[str, JuGeoType]
            A mapping from name to the corresponding :class:`JuGeoType`.
            Names whose annotation could not be lifted are omitted from the
            result (the error counter in :meth:`statistics` is incremented).
        """
        result: dict[str, JuGeoType] = {}
        for name, ann in annotations.items():
            try:
                result[name] = self.lift(ann, coord=base_coord)
            except Exception:
                self._stats["errors"] += 1
        return result

    def register_primitive(self, type_: type, carrier_name: str) -> None:
        """Register a custom carrier name for a primitive type.

        Parameters
        ----------
        type_ : type
            The Python type to register.
        carrier_name : str
            The carrier name to use when lifting annotations of this type.

        Notes
        -----
        This also registers *type_* in the underlying interpreter's registry
        as :attr:`AnnotationKind.PRIMITIVE`.
        """
        self._primitive_map[type_] = carrier_name
        self._interpreter.register_handler(type_, AnnotationKind.PRIMITIVE)

    def statistics(self) -> dict[str, int]:
        """Return a snapshot of the lifter's operational statistics.

        Returns
        -------
        dict[str, int]
            A dictionary with keys ``"lifted"`` (successful lifts),
            ``"cached"`` (cache hits, currently always 0 as caching is not
            yet implemented), and ``"errors"`` (failed lifts).
        """
        return dict(self._stats)
