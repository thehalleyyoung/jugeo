from __future__ import annotations

r"""Core data models for ``jugeo.python_runtime.callable_surfaces`` (theory2.tex Ch16).

Overview
--------
This module defines the fundamental immutable data structures used throughout
the callable_surfaces package.  Every type is a :func:`~dataclasses.dataclass`
with ``frozen=True, slots=True``, full serialisation/deserialisation support,
strict type annotations, and comprehensive docstrings.

Theory alignment (theory2.tex Ch16)
-------------------------------------
The models in this file encode the *typed callable surface* of a Python
program as a collection of structured sheaf sections over the semantic site:

* §16.2 :class:`CallableSurface`     — the primary typed interface of any callable.
* §16.3 :class:`MethodBinding`       — binding morphism from function to bound method.
* §16.3 :class:`BoundMethod`         — fully bound method with effective surface.
* §16.4 :class:`DescriptorRecord`    — descriptor-protocol flags and lookup priority.
* §16.5 :class:`ClassConstruction`   — MRO, metaclass, slot/init configuration.
* §16.6 :class:`SignatureRecord`     — full resolved signature with annotation types.
* §16.2 :class:`ParameterSpec`       — single parameter record within a surface.
* §16.2 :class:`ParameterKind`       — enum mapping inspect.Parameter.kind values.
* §16.4 :class:`DescriptorKind`      — enum classifying descriptor flavours.

Design principles
-----------------
All frozen dataclasses that need to expose "mutation" operations use
:func:`dataclasses.replace` and return new instances rather than modifying in
place, preserving the immutability guarantees required for hashing and caching.

Every model provides:

* ``serialize() -> dict[str, Any]``           — JSON-serialisable dict.
* ``classmethod parse(data: dict) -> Self``   — reconstruct from serialised form.

Helper functions at the bottom of the module complement the models with
``inspect``-based construction utilities:

* :func:`parameter_spec_from_inspect`  — build :class:`ParameterSpec` from an
  :class:`inspect.Parameter` object.
* :func:`callable_surface_from_qualname` — attempt live construction of a
  :class:`CallableSurface` by importing and inspecting a qualified name.
* :func:`merge_parameter_specs`         — merge two parameter-spec tuples,
  letting an override sequence replace matching positional entries.

Copilot integration
-------------------
This module was initially scaffolded with copilot assistance as part of the
callable_surfaces sub-package.  All generated stubs entered at
``TrustLevel.ORACLE_PROPOSED`` (level 2) and are promoted through explicit CI
review.  See theory2.tex §16.9 for the trust promotion policy.

Examples
--------
Typical construction via the helper function::

    import inspect
    from jugeo.python_runtime.callable_surfaces.models import (
        parameter_spec_from_inspect,
        CallableSurface,
        ParameterSpec,
    )
    from jugeo.geometry.site import CoordinateObject, CoordinateKind

    def my_func(x: int, y: str = "hello") -> bool: ...

    sig = inspect.signature(my_func)
    params = tuple(
        parameter_spec_from_inspect(p, i)
        for i, p in enumerate(sig.parameters.values())
    )
    coord = CoordinateObject(
        components=("my_module", "my_func"),
        kind=CoordinateKind.FUNCTION,
    )
    surface = CallableSurface(
        coordinate=coord,
        parameters=params,
        return_annotation="bool",
        is_async=False,
        is_generator=False,
        decorators=(),
        qualname="my_func",
        module="my_module",
    )
    print(surface.arity())          # 2
    print(surface.required_arity()) # 1  (only 'x' is required)
"""

import inspect
import logging
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from importlib import import_module
from typing import Any

from jugeo.geometry.site import CoordinateKind, CoordinateObject

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ParameterKind
# ---------------------------------------------------------------------------


class ParameterKind(str, Enum):
    """Enumeration of Python parameter kinds, mirroring :class:`inspect.Parameter`.

    Each variant's string value matches the ``.name`` attribute of the
    corresponding :class:`inspect.Parameter` kind constant, making round-trip
    conversion via ``inspect.Parameter.kind.name`` straightforward.

    Theory alignment
    ----------------
    §16.2.1 of theory2.tex ("Parameter classification") uses these five kinds
    to partition the parameter sequence of a callable surface into disjoint
    classes that determine positional resolution, keyword-resolution, and
    variadic-argument handling.

    Ordering
    --------
    Parameters appear in the following canonical order in a valid signature::

        (POSITIONAL_ONLY, ..., /, POSITIONAL_OR_KEYWORD, ...,
         *VAR_POSITIONAL, KEYWORD_ONLY, ..., **VAR_KEYWORD)

    The slash (``/``) and star (``*``) tokens are implicit; they are inferred
    from the transition between kinds.

    Examples
    --------
    >>> ParameterKind.POSITIONAL_ONLY.is_positional()
    True
    >>> ParameterKind.VAR_POSITIONAL.is_variadic()
    True
    >>> ParameterKind.KEYWORD_ONLY.is_keyword()
    True
    """

    POSITIONAL_ONLY = "POSITIONAL_ONLY"
    POSITIONAL_OR_KEYWORD = "POSITIONAL_OR_KEYWORD"
    VAR_POSITIONAL = "VAR_POSITIONAL"
    KEYWORD_ONLY = "KEYWORD_ONLY"
    VAR_KEYWORD = "VAR_KEYWORD"

    # ------------------------------------------------------------------
    # Helper predicates
    # ------------------------------------------------------------------

    def is_positional(self) -> bool:
        """Return True for kinds that accept positional arguments.

        Returns
        -------
        bool
            ``True`` for :attr:`POSITIONAL_ONLY`, :attr:`POSITIONAL_OR_KEYWORD`,
            and :attr:`VAR_POSITIONAL`.
        """
        return self in (
            ParameterKind.POSITIONAL_ONLY,
            ParameterKind.POSITIONAL_OR_KEYWORD,
            ParameterKind.VAR_POSITIONAL,
        )

    def is_keyword(self) -> bool:
        """Return True for kinds that accept keyword arguments.

        Returns
        -------
        bool
            ``True`` for :attr:`POSITIONAL_OR_KEYWORD`, :attr:`KEYWORD_ONLY`,
            and :attr:`VAR_KEYWORD`.
        """
        return self in (
            ParameterKind.POSITIONAL_OR_KEYWORD,
            ParameterKind.KEYWORD_ONLY,
            ParameterKind.VAR_KEYWORD,
        )

    def is_variadic(self) -> bool:
        """Return True for the two variadic kinds.

        Returns
        -------
        bool
            ``True`` for :attr:`VAR_POSITIONAL` and :attr:`VAR_KEYWORD`.
        """
        return self in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)

    def inspect_kind(self) -> int:
        """Return the corresponding :class:`inspect.Parameter` kind integer.

        Returns
        -------
        int
            One of the five ``inspect.Parameter.*`` integer constants.
        """
        _map: dict[str, int] = {
            "POSITIONAL_ONLY": inspect.Parameter.POSITIONAL_ONLY,
            "POSITIONAL_OR_KEYWORD": inspect.Parameter.POSITIONAL_OR_KEYWORD,
            "VAR_POSITIONAL": inspect.Parameter.VAR_POSITIONAL,
            "KEYWORD_ONLY": inspect.Parameter.KEYWORD_ONLY,
            "VAR_KEYWORD": inspect.Parameter.VAR_KEYWORD,
        }
        return _map[self.value]

    @classmethod
    def from_inspect(cls, kind: int) -> "ParameterKind":
        """Construct a :class:`ParameterKind` from an :class:`inspect.Parameter` kind int.

        Parameters
        ----------
        kind:
            One of ``inspect.Parameter.POSITIONAL_ONLY``,
            ``inspect.Parameter.POSITIONAL_OR_KEYWORD``, etc.

        Returns
        -------
        ParameterKind
            The matching enum variant.

        Raises
        ------
        ValueError
            If *kind* does not correspond to any known variant.
        """
        _reverse: dict[int, str] = {
            inspect.Parameter.POSITIONAL_ONLY: "POSITIONAL_ONLY",
            inspect.Parameter.POSITIONAL_OR_KEYWORD: "POSITIONAL_OR_KEYWORD",
            inspect.Parameter.VAR_POSITIONAL: "VAR_POSITIONAL",
            inspect.Parameter.KEYWORD_ONLY: "KEYWORD_ONLY",
            inspect.Parameter.VAR_KEYWORD: "VAR_KEYWORD",
        }
        name = _reverse.get(kind)
        if name is None:
            raise ValueError(f"Unknown inspect.Parameter kind: {kind!r}")
        return cls(name)


# ---------------------------------------------------------------------------
# ParameterSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Immutable record for a single parameter in a callable surface.

    A :class:`ParameterSpec` captures everything that can be statically
    determined about a single parameter: its name, kind (see
    :class:`ParameterKind`), annotation as a string, whether it has a
    default value (and the repr of that default), its zero-based position
    in the parameter list, and a derived ``is_optional`` flag.

    Theory alignment
    ----------------
    §16.2.2 of theory2.tex ("Parameter records") specifies that parameter
    information must be captured as immutable value objects to allow
    deterministic hashing and comparison across serialisation boundaries.
    The ``annotation_str`` field stores the annotation as a raw string
    (not a live type object) to survive serialisation.

    Parameters
    ----------
    name:
        The parameter name as it appears in the source, e.g. ``"self"``,
        ``"x"``, ``"*args"``.
    kind:
        The :class:`ParameterKind` classifying how this parameter receives
        its argument at call time.
    annotation_str:
        The annotation as a string.  May be ``"inspect.Parameter.empty"``
        if no annotation is present, or a repr of the annotation object.
    has_default:
        ``True`` if this parameter has a default value.
    default_repr:
        The ``repr()`` of the default value, or ``""`` if :attr:`has_default`
        is ``False``.
    position:
        Zero-based index of this parameter in the full parameter list of the
        callable surface.
    is_optional:
        ``True`` when the parameter can be omitted from a call.  This is
        ``True`` iff :attr:`has_default` is ``True`` or :attr:`kind` is
        variadic.

    Examples
    --------
    >>> spec = ParameterSpec(
    ...     name="count",
    ...     kind=ParameterKind.POSITIONAL_OR_KEYWORD,
    ...     annotation_str="int",
    ...     has_default=True,
    ...     default_repr="0",
    ...     position=1,
    ...     is_optional=True,
    ... )
    >>> spec.is_variadic()
    False
    >>> spec.serialize()["name"]
    'count'
    """

    name: str
    kind: ParameterKind
    annotation_str: str
    has_default: bool
    default_repr: str
    position: int
    is_optional: bool

    # ------------------------------------------------------------------
    # Derived predicates
    # ------------------------------------------------------------------

    def is_variadic(self) -> bool:
        """Return True when this parameter is ``*args`` or ``**kwargs``.

        Returns
        -------
        bool
            Equivalent to ``self.kind.is_variadic()``.
        """
        return self.kind.is_variadic()

    def is_required(self) -> bool:
        """Return True when this parameter must be supplied at every call site.

        A parameter is required if it is not optional (no default, not
        variadic) and not a keyword-only parameter with a default.

        Returns
        -------
        bool
            ``True`` iff :attr:`is_optional` is ``False``.
        """
        return not self.is_optional

    def has_annotation(self) -> bool:
        """Return True when an annotation is present (not inspect.Parameter.empty).

        Returns
        -------
        bool
            ``True`` when :attr:`annotation_str` is not the empty-sentinel
            string ``"inspect.Parameter.empty"``.
        """
        return self.annotation_str not in ("", "inspect.Parameter.empty")

    def display_name(self) -> str:
        """Return a display-friendly parameter name with kind sigils.

        Returns
        -------
        str
            ``*name`` for VAR_POSITIONAL, ``**name`` for VAR_KEYWORD,
            or plain *name* otherwise.
        """
        if self.kind == ParameterKind.VAR_POSITIONAL:
            return f"*{self.name}"
        if self.kind == ParameterKind.VAR_KEYWORD:
            return f"**{self.name}"
        return self.name

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this parameter spec to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``kind``, ``annotation_str``, ``has_default``,
            ``default_repr``, ``position``, ``is_optional``.

        Examples
        --------
        >>> spec.serialize()["kind"]
        'POSITIONAL_OR_KEYWORD'
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "annotation_str": self.annotation_str,
            "has_default": self.has_default,
            "default_repr": self.default_repr,
            "position": self.position,
            "is_optional": self.is_optional,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "ParameterSpec":
        """Reconstruct a :class:`ParameterSpec` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        ParameterSpec
            The reconstructed instance.

        Raises
        ------
        KeyError
            If the ``name`` or ``kind`` key is missing.
        ValueError
            If ``kind`` is not a valid :class:`ParameterKind` value.

        Examples
        --------
        >>> spec2 = ParameterSpec.parse(spec.serialize())
        >>> spec2 == spec
        True
        """
        kind = ParameterKind(data["kind"])
        has_default = bool(data.get("has_default", False))
        is_variadic = kind.is_variadic()
        is_optional = bool(data.get("is_optional", has_default or is_variadic))
        return cls(
            name=data["name"],
            kind=kind,
            annotation_str=data.get("annotation_str", "inspect.Parameter.empty"),
            has_default=has_default,
            default_repr=data.get("default_repr", ""),
            position=int(data.get("position", 0)),
            is_optional=is_optional,
        )


# ---------------------------------------------------------------------------
# DescriptorKind
# ---------------------------------------------------------------------------


class DescriptorKind(str, Enum):
    """Classification of Python descriptor flavours.

    Theory alignment
    ----------------
    §16.4.1 of theory2.tex ("Descriptor taxonomy") distinguishes six descriptor
    kinds based on which protocol methods are implemented and how the descriptor
    is stored.  The lookup-priority ordering follows CPython's slot resolution
    logic: DATA > SLOT > PROPERTY > instance ``__dict__`` > NON_DATA > CLASSVAR
    > INSTANCEVAR.

    Examples
    --------
    >>> DescriptorKind.DATA.lookup_priority()
    1
    >>> DescriptorKind.NON_DATA.lookup_priority()
    3
    """

    DATA = "data"
    """Defines both ``__get__`` and ``__set__`` (and/or ``__delete__``).
    Takes precedence over the instance ``__dict__``."""

    NON_DATA = "non_data"
    """Defines only ``__get__``.  The instance ``__dict__`` overrides it."""

    SLOT = "slot"
    """Descriptor generated by ``__slots__``.  Equivalent to a data descriptor."""

    PROPERTY = "property"
    """Built-in :class:`property` descriptor — a specialised data descriptor."""

    CLASSVAR = "classvar"
    """A class-level variable that is NOT a descriptor (no ``__get__``)."""

    INSTANCEVAR = "instancevar"
    """An instance variable stored in the instance ``__dict__``."""

    def lookup_priority(self) -> int:
        """Return the numeric lookup priority (lower = higher precedence).

        Returns
        -------
        int
            Priority in ``[1, 4]``.  Data descriptors win at 1; instance
            variables at 2; non-data descriptors at 3; plain class/instance
            vars at 4.
        """
        _priorities: dict[str, int] = {
            "data": 1,
            "slot": 1,
            "property": 1,
            "non_data": 3,
            "classvar": 4,
            "instancevar": 2,
        }
        return _priorities[self.value]

    def is_descriptor(self) -> bool:
        """Return True for kinds that implement the descriptor protocol.

        Returns
        -------
        bool
            ``True`` for DATA, NON_DATA, SLOT, and PROPERTY.
        """
        return self in (
            DescriptorKind.DATA,
            DescriptorKind.NON_DATA,
            DescriptorKind.SLOT,
            DescriptorKind.PROPERTY,
        )


# ---------------------------------------------------------------------------
# CallableSurface  (§16.2 — primary model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallableSurface:
    """The typed interface of a Python callable, as an immutable value object.

    A :class:`CallableSurface` is the primary model in the callable_surfaces
    package.  It records everything that can be statically determined about a
    callable's interface: the parameter sequence (as :class:`ParameterSpec`
    objects), the return annotation string, async/generator flags, the
    decorator stack, and the qualified name and module.

    The surface is bound to a :class:`~jugeo.geometry.site.CoordinateObject`
    in the semantic site, enabling downstream judgment algebra to operate on
    it as a typed section.

    Theory alignment
    ----------------
    §16.2 of theory2.tex ("Callable surfaces") treats a callable surface as
    a section ``σ: U → CallableSurface`` over the open set ``U`` defined by
    the coordinate.  The arity and required-arity functions compute the rank
    of this section.

    Parameters
    ----------
    coordinate:
        The :class:`~jugeo.geometry.site.CoordinateObject` locating this
        surface in the semantic site.  Should have
        ``kind == CoordinateKind.FUNCTION``.
    parameters:
        Tuple of :class:`ParameterSpec` objects in declaration order.
    return_annotation:
        The return annotation as a string.  ``"inspect.Parameter.empty"``
        indicates no annotation.
    is_async:
        ``True`` when this callable is declared with ``async def``.
    is_generator:
        ``True`` when this callable contains ``yield`` or ``yield from``.
    decorators:
        Tuple of decorator names (outermost first) applied to this callable.
    qualname:
        The ``__qualname__`` attribute of the callable.
    module:
        The ``__module__`` attribute of the callable.

    Examples
    --------
    >>> surface.arity()
    3
    >>> surface.required_arity()
    1
    >>> surface.parameter_names()
    ('self', 'x', 'y')
    """

    coordinate: CoordinateObject
    parameters: tuple[ParameterSpec, ...]
    return_annotation: str
    is_async: bool
    is_generator: bool
    decorators: tuple[str, ...]
    qualname: str
    module: str

    # ------------------------------------------------------------------
    # Arity helpers
    # ------------------------------------------------------------------

    def arity(self) -> int:
        """Return the total number of declared parameters.

        Returns
        -------
        int
            ``len(self.parameters)``.
        """
        return len(self.parameters)

    def required_arity(self) -> int:
        """Return the number of parameters that MUST be supplied at every call.

        Variadic parameters and parameters with defaults are excluded.

        Returns
        -------
        int
            Count of required (non-optional, non-variadic) parameters.
        """
        return sum(
            1
            for p in self.parameters
            if not p.is_optional and not p.is_variadic()
        )

    def has_var_args(self) -> bool:
        """Return True when the signature contains a ``*args`` parameter.

        Returns
        -------
        bool
            ``True`` iff any parameter has ``kind == ParameterKind.VAR_POSITIONAL``.
        """
        return any(p.kind == ParameterKind.VAR_POSITIONAL for p in self.parameters)

    def has_var_kwargs(self) -> bool:
        """Return True when the signature contains a ``**kwargs`` parameter.

        Returns
        -------
        bool
            ``True`` iff any parameter has ``kind == ParameterKind.VAR_KEYWORD``.
        """
        return any(p.kind == ParameterKind.VAR_KEYWORD for p in self.parameters)

    def parameter_names(self) -> tuple[str, ...]:
        """Return the parameter names in declaration order.

        Returns
        -------
        tuple[str, ...]
            Parameter names, e.g. ``('self', 'x', 'y')``.
        """
        return tuple(p.name for p in self.parameters)

    def keyword_parameters(self) -> tuple[ParameterSpec, ...]:
        """Return only the keyword-accessible parameters.

        Returns
        -------
        tuple[ParameterSpec, ...]
            Parameters where ``kind.is_keyword()`` is ``True``.
        """
        return tuple(p for p in self.parameters if p.kind.is_keyword())

    def positional_parameters(self) -> tuple[ParameterSpec, ...]:
        """Return only the positional parameters (excluding VAR_POSITIONAL).

        Returns
        -------
        tuple[ParameterSpec, ...]
            Parameters where ``kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)``.
        """
        return tuple(
            p
            for p in self.parameters
            if p.kind in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
        )

    def has_return_annotation(self) -> bool:
        """Return True when a return annotation is present.

        Returns
        -------
        bool
            ``True`` when :attr:`return_annotation` is not the empty-sentinel.
        """
        return self.return_annotation not in ("", "inspect.Parameter.empty")

    def is_compatible_with(self, other: "CallableSurface") -> bool:
        """Check whether this surface is call-compatible with *other*.

        Two surfaces are *call-compatible* when:

        1. This surface's required arity is ``<=`` the other's arity (so the
           other always provides enough arguments).
        2. All required parameter names in this surface appear in the other's
           parameter names (for keyword resolution).
        3. Neither surface has conflicting positional-only vs keyword-only
           parameter ordering.

        This is a conservative structural check; it does not resolve types.

        Parameters
        ----------
        other:
            The surface to compare against.

        Returns
        -------
        bool
            ``True`` when a call using *other*'s parameters can be forwarded
            to this surface without a ``TypeError``.
        """
        if self.required_arity() > other.arity():
            return False
        # All required parameter names must be resolvable from *other*
        other_names = set(other.parameter_names())
        for param in self.parameters:
            if param.is_required() and not param.is_variadic():
                if param.kind == ParameterKind.POSITIONAL_ONLY:
                    continue  # Positional-only resolved by position, not name
                if param.name not in other_names:
                    return False
        return True

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this surface to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Nested structure with coordinate, parameters (list of dicts),
            and all scalar fields.
        """
        return {
            "coordinate": self.coordinate.serialize(),
            "parameters": [p.serialize() for p in self.parameters],
            "return_annotation": self.return_annotation,
            "is_async": self.is_async,
            "is_generator": self.is_generator,
            "decorators": list(self.decorators),
            "qualname": self.qualname,
            "module": self.module,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "CallableSurface":
        """Reconstruct a :class:`CallableSurface` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        CallableSurface
            The reconstructed instance.

        Raises
        ------
        KeyError
            If ``coordinate`` is missing from *data*.
        """
        parameters = tuple(
            ParameterSpec.parse(p) for p in data.get("parameters", [])
        )
        return cls(
            coordinate=CoordinateObject.parse(data["coordinate"]),
            parameters=parameters,
            return_annotation=data.get("return_annotation", "inspect.Parameter.empty"),
            is_async=bool(data.get("is_async", False)),
            is_generator=bool(data.get("is_generator", False)),
            decorators=tuple(data.get("decorators", ())),
            qualname=data.get("qualname", ""),
            module=data.get("module", ""),
        )


# ---------------------------------------------------------------------------
# MethodBinding  (§16.3 — key model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MethodBinding:
    """Coordinate morphism from an unbound function to a bound method.

    A :class:`MethodBinding` represents the Python descriptor ``__get__``
    invocation that converts an unbound function (located at
    :attr:`callable_surface`.coordinate) into a bound method.  The binding
    carries the instance and class coordinates, the morphism kind identifier,
    and the names of arguments that are pre-bound (typically ``"self"`` or
    ``"cls"``).

    Theory alignment
    ----------------
    §16.3 of theory2.tex ("Method-binding morphisms") models binding as a
    coordinate morphism ``φ: func_coord → bound_coord`` where the source is
    the function coordinate and the target is a derived coordinate under the
    instance or class.

    Parameters
    ----------
    callable_surface:
        The :class:`CallableSurface` of the unbound function.
    instance_coordinate:
        The coordinate of the instance to which the method is being bound.
        For classmethods this is the class coordinate; for staticmethods
        it is a sentinel.
    class_coordinate:
        The coordinate of the class that owns the method.
    binding_morphism:
        A string identifying the morphism kind: ``"instancemethod"``,
        ``"classmethod"``, or ``"staticmethod"``.
    bound_args:
        Tuple of argument names that are pre-bound by this morphism, e.g.
        ``("self",)`` for instance methods or ``("cls",)`` for classmethods.

    Examples
    --------
    >>> binding.is_classmethod()
    False
    >>> binding.effective_arity()
    2  # total arity minus the one bound arg
    """

    callable_surface: CallableSurface
    instance_coordinate: CoordinateObject
    class_coordinate: CoordinateObject
    binding_morphism: str
    bound_args: tuple[str, ...]

    # ------------------------------------------------------------------
    # Derived predicates
    # ------------------------------------------------------------------

    def is_classmethod(self) -> bool:
        """Return True when this binding represents a classmethod invocation.

        Returns
        -------
        bool
            ``True`` iff :attr:`binding_morphism` is ``"classmethod"``.
        """
        return self.binding_morphism == "classmethod"

    def is_staticmethod(self) -> bool:
        """Return True when this binding represents a staticmethod.

        Returns
        -------
        bool
            ``True`` iff :attr:`binding_morphism` is ``"staticmethod"``.
        """
        return self.binding_morphism == "staticmethod"

    def is_instancemethod(self) -> bool:
        """Return True when this binding represents a regular instance method.

        Returns
        -------
        bool
            ``True`` iff :attr:`binding_morphism` is ``"instancemethod"``.
        """
        return self.binding_morphism == "instancemethod"

    def effective_arity(self) -> int:
        """Return the arity of the surface after subtracting bound arguments.

        Returns
        -------
        int
            ``callable_surface.arity() - len(bound_args)``.  For a staticmethod
        where no args are pre-bound this equals the full arity.
        """
        return self.callable_surface.arity() - len(self.bound_args)

    def effective_required_arity(self) -> int:
        """Return the required arity of the surface after removing bound args.

        Returns
        -------
        int
            Required arity after pre-bound arguments are removed.
        """
        bound_set = set(self.bound_args)
        return sum(
            1
            for p in self.callable_surface.parameters
            if p.is_required() and not p.is_variadic() and p.name not in bound_set
        )

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this method binding to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Nested structure with :attr:`callable_surface`, coordinates,
            and scalar binding fields.
        """
        return {
            "callable_surface": self.callable_surface.serialize(),
            "instance_coordinate": self.instance_coordinate.serialize(),
            "class_coordinate": self.class_coordinate.serialize(),
            "binding_morphism": self.binding_morphism,
            "bound_args": list(self.bound_args),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "MethodBinding":
        """Reconstruct a :class:`MethodBinding` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        MethodBinding
            The reconstructed instance.
        """
        return cls(
            callable_surface=CallableSurface.parse(data["callable_surface"]),
            instance_coordinate=CoordinateObject.parse(data["instance_coordinate"]),
            class_coordinate=CoordinateObject.parse(data["class_coordinate"]),
            binding_morphism=data.get("binding_morphism", "instancemethod"),
            bound_args=tuple(data.get("bound_args", ())),
        )


# ---------------------------------------------------------------------------
# DescriptorRecord  (§16.4 — key model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescriptorRecord:
    """Descriptor-protocol flags and lookup priority for a class attribute.

    A :class:`DescriptorRecord` captures all information needed by the
    descriptor-protocol lookup algorithm (Python data model §3.3.2): which
    protocol methods are implemented, the :class:`DescriptorKind`
    classification, and the numeric lookup priority.

    Theory alignment
    ----------------
    §16.4 of theory2.tex ("Descriptor-protocol precedence") encodes the three-tier
    ordering (data descriptors > instance ``__dict__`` > non-data descriptors) as
    an explicit sheaf restriction map.  The :meth:`lookup_priority` method returns
    the tier number.

    Parameters
    ----------
    name:
        The attribute name as it appears in the owning class body.
    owner_class:
        Qualified name of the class that owns this descriptor.
    has_get:
        ``True`` if the descriptor defines ``__get__``.
    has_set:
        ``True`` if the descriptor defines ``__set__``.
    has_delete:
        ``True`` if the descriptor defines ``__delete__``.
    kind:
        The :class:`DescriptorKind` classification.
    coordinate:
        The :class:`~jugeo.geometry.site.CoordinateObject` locating this
        descriptor in the semantic site.

    Examples
    --------
    >>> record.is_data_descriptor()
    True
    >>> record.lookup_priority()
    1
    """

    name: str
    owner_class: str
    has_get: bool
    has_set: bool
    has_delete: bool
    kind: DescriptorKind
    coordinate: CoordinateObject

    # ------------------------------------------------------------------
    # Descriptor protocol predicates
    # ------------------------------------------------------------------

    def is_data_descriptor(self) -> bool:
        """Return True when both ``__get__`` and ``__set__`` (or ``__delete__``) are present.

        Data descriptors take priority over the instance ``__dict__``; they
        can intercept assignment as well as retrieval.

        Returns
        -------
        bool
            ``True`` iff :attr:`has_get` is ``True`` and at least one of
            :attr:`has_set` or :attr:`has_delete` is ``True``.
        """
        return self.has_get and (self.has_set or self.has_delete)

    def is_non_data_descriptor(self) -> bool:
        """Return True when only ``__get__`` is present (no ``__set__`` or ``__delete__``).

        Non-data descriptors are overridden by instance ``__dict__`` entries.

        Returns
        -------
        bool
            ``True`` iff :attr:`has_get` is ``True`` and both :attr:`has_set`
            and :attr:`has_delete` are ``False``.
        """
        return self.has_get and not self.has_set and not self.has_delete

    def lookup_priority(self) -> int:
        """Return the numeric lookup priority for this descriptor.

        Follows the Python data model §3.3.2 ordering:

        1. Data descriptors (``__get__`` + ``__set__``/``__delete__``).
        2. Instance ``__dict__`` (not represented here, priority 2).
        3. Non-data descriptors (``__get__`` only).
        4. Class variables and instance variables (no descriptor protocol).

        Returns
        -------
        int
            Priority in ``[1, 4]`` where 1 is highest.
        """
        if self.is_data_descriptor():
            return 1
        if self.is_non_data_descriptor():
            return 3
        return self.kind.lookup_priority()

    def shadows_instance_dict(self) -> bool:
        """Return True when this descriptor takes precedence over instance ``__dict__``.

        Returns
        -------
        bool
            ``True`` iff :meth:`lookup_priority` returns 1 (data descriptor tier).
        """
        return self.lookup_priority() == 1

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this descriptor record to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Flat dict with all fields; ``kind`` as its string value;
            ``coordinate`` as a nested dict.
        """
        return {
            "name": self.name,
            "owner_class": self.owner_class,
            "has_get": self.has_get,
            "has_set": self.has_set,
            "has_delete": self.has_delete,
            "kind": self.kind.value,
            "coordinate": self.coordinate.serialize(),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "DescriptorRecord":
        """Reconstruct a :class:`DescriptorRecord` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        DescriptorRecord
            The reconstructed instance.
        """
        return cls(
            name=data["name"],
            owner_class=data.get("owner_class", ""),
            has_get=bool(data.get("has_get", False)),
            has_set=bool(data.get("has_set", False)),
            has_delete=bool(data.get("has_delete", False)),
            kind=DescriptorKind(data.get("kind", DescriptorKind.NON_DATA.value)),
            coordinate=CoordinateObject.parse(data["coordinate"]),
        )


# ---------------------------------------------------------------------------
# BoundMethod  (§16.3 — key model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoundMethod:
    """A fully bound Python method with its effective callable surface.

    A :class:`BoundMethod` is the result of applying a :class:`MethodBinding`
    to a specific instance or class.  Unlike :class:`MethodBinding` (which
    records the *process* of binding), :class:`BoundMethod` records the
    *result*: the method name, its instance and class coordinates, the
    underlying :class:`CallableSurface`, and the binding-kind flags.

    Theory alignment
    ----------------
    §16.3.4 of theory2.tex ("Bound method records") specifies that the bound
    method object must carry both the original surface and the effective surface
    (after subtracting the pre-bound ``self``/``cls`` argument).

    Parameters
    ----------
    method_name:
        The unqualified method name, e.g. ``"forward"``.
    instance_coordinate:
        Coordinate of the instance (or class for classmethods).
    class_coordinate:
        Coordinate of the owning class.
    surface:
        The :class:`CallableSurface` of the underlying *unbound* function.
    is_classmethod:
        ``True`` when this bound method was created via ``classmethod``.
    is_staticmethod:
        ``True`` when this bound method was created via ``staticmethod``
        (in which case no arg is pre-bound).

    Examples
    --------
    >>> bm.effective_surface().arity()
    2  # self is removed for instance methods
    """

    method_name: str
    instance_coordinate: CoordinateObject
    class_coordinate: CoordinateObject
    surface: CallableSurface
    is_classmethod: bool
    is_staticmethod: bool

    # ------------------------------------------------------------------
    # Effective surface
    # ------------------------------------------------------------------

    def effective_surface(self) -> CallableSurface:
        """Return the :class:`CallableSurface` as seen by external callers.

        For instance methods, the leading ``self`` parameter is removed.
        For classmethods, the leading ``cls`` parameter is removed.
        For staticmethods, the surface is returned unchanged.

        Returns
        -------
        CallableSurface
            A new :class:`CallableSurface` with the implicit first argument
            stripped, or the original surface for staticmethods.
        """
        if self.is_staticmethod:
            return self.surface

        params = self.surface.parameters
        if not params:
            return self.surface

        # Remove the first parameter (self or cls)
        stripped = params[1:]
        # Re-index positions
        reindexed = tuple(replace(p, position=i) for i, p in enumerate(stripped))
        return replace(self.surface, parameters=reindexed)

    def effective_arity(self) -> int:
        """Return the arity visible to external callers.

        Returns
        -------
        int
            The arity of :meth:`effective_surface`.
        """
        return self.effective_surface().arity()

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this bound method to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Nested structure with surface dict and coordinate dicts.
        """
        return {
            "method_name": self.method_name,
            "instance_coordinate": self.instance_coordinate.serialize(),
            "class_coordinate": self.class_coordinate.serialize(),
            "surface": self.surface.serialize(),
            "is_classmethod": self.is_classmethod,
            "is_staticmethod": self.is_staticmethod,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "BoundMethod":
        """Reconstruct a :class:`BoundMethod` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        BoundMethod
            The reconstructed instance.
        """
        return cls(
            method_name=data["method_name"],
            instance_coordinate=CoordinateObject.parse(data["instance_coordinate"]),
            class_coordinate=CoordinateObject.parse(data["class_coordinate"]),
            surface=CallableSurface.parse(data["surface"]),
            is_classmethod=bool(data.get("is_classmethod", False)),
            is_staticmethod=bool(data.get("is_staticmethod", False)),
        )


# ---------------------------------------------------------------------------
# ClassConstruction  (§16.5 — key model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassConstruction:
    """Immutable record of a Python class's construction state.

    A :class:`ClassConstruction` captures all static information about a
    class that is relevant to callable-surface analysis: the base class
    tuple, the C3-linearised MRO, the metaclass name, ``__slots__`` usage,
    and the presence of custom ``__new__``/``__init__``/``__init_subclass__``.

    Theory alignment
    ----------------
    §16.5 of theory2.tex ("Class construction") treats the class object as
    a structured record in the semantic site.  The MRO tuple determines which
    coordinate's callable surfaces are inherited and in what precedence order.

    Parameters
    ----------
    class_coordinate:
        The coordinate of the class itself in the semantic site.
    base_classes:
        Tuple of qualified names of the direct base classes in declaration
        order, e.g. ``("torch.nn.Module", "object")``.
    metaclass:
        Qualified name of the metaclass, typically ``"type"`` or
        ``"abc.ABCMeta"``.
    mro:
        Tuple of qualified names in C3-MRO order (class itself first,
        ``"object"`` last).
    has_slots:
        ``True`` when the class defines ``__slots__``.
    has_new:
        ``True`` when the class defines a custom ``__new__``.
    has_init:
        ``True`` when the class defines a custom ``__init__``.
    has_init_subclass:
        ``True`` when the class defines ``__init_subclass__``.

    Examples
    --------
    >>> cc.depth_in_hierarchy()
    3
    >>> cc.is_new_style()
    True
    """

    class_coordinate: CoordinateObject
    base_classes: tuple[str, ...]
    metaclass: str
    # NOTE: 'mro' must use field() to prevent Python from resolving
    # ClassConstruction.mro as type.mro (the metaclass method), which would
    # cause dataclass to treat it as a field with a default value.
    mro: tuple[str, ...] = field()
    has_slots: bool = field()
    has_new: bool
    has_init: bool
    has_init_subclass: bool

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def depth_in_hierarchy(self) -> int:
        """Return the depth of this class in the inheritance hierarchy.

        Depth is defined as ``len(mro) - 1`` (the MRO includes the class
        itself; ``"object"`` is at the bottom).

        Returns
        -------
        int
            Inheritance depth.  Returns 0 if :attr:`mro` is empty.
        """
        return max(0, len(self.mro) - 1)

    def is_new_style(self) -> bool:
        """Return True when this class is a new-style class.

        In Python 3, all classes are new-style (all inherit from ``object``).
        This method checks that ``"object"`` appears in the MRO.

        Returns
        -------
        bool
            ``True`` iff ``"object"`` is in :attr:`mro`.
        """
        return "object" in self.mro

    def uses_descriptor_protocol(self) -> bool:
        """Return True when this class likely makes use of the descriptor protocol.

        A class uses the descriptor protocol when it has ``__slots__``
        (which generates slot descriptors), a custom ``__new__`` (which may
        create descriptor-backed attributes), or a non-trivial metaclass.

        Returns
        -------
        bool
            ``True`` iff ``has_slots`` is ``True``, ``has_new`` is ``True``,
            or :attr:`metaclass` is not ``"type"``.
        """
        return self.has_slots or self.has_new or self.metaclass not in ("type", "")

    def inherits_from(self, class_qname: str) -> bool:
        """Return True when *class_qname* appears in this class's MRO.

        Parameters
        ----------
        class_qname:
            Qualified class name to search for in the MRO.

        Returns
        -------
        bool
            ``True`` iff *class_qname* is in :attr:`mro` (excluding the
            first entry, which is the class itself).
        """
        if len(self.mro) <= 1:
            return False
        return class_qname in self.mro[1:]

    def direct_base_count(self) -> int:
        """Return the number of direct base classes.

        Returns
        -------
        int
            ``len(self.base_classes)``.
        """
        return len(self.base_classes)

    def is_multiple_inheritance(self) -> bool:
        """Return True when this class directly inherits from more than one base.

        Returns
        -------
        bool
            ``True`` iff :meth:`direct_base_count` exceeds 1 and the
            only extra base is not simply ``"object"``.
        """
        non_object_bases = [b for b in self.base_classes if b != "object"]
        return len(non_object_bases) > 1

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this class construction record to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Nested dict with :attr:`class_coordinate` and all scalar/tuple fields.
        """
        return {
            "class_coordinate": self.class_coordinate.serialize(),
            "base_classes": list(self.base_classes),
            "metaclass": self.metaclass,
            "mro": list(self.mro),
            "has_slots": self.has_slots,
            "has_new": self.has_new,
            "has_init": self.has_init,
            "has_init_subclass": self.has_init_subclass,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "ClassConstruction":
        """Reconstruct a :class:`ClassConstruction` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        ClassConstruction
            The reconstructed instance.
        """
        return cls(
            class_coordinate=CoordinateObject.parse(data["class_coordinate"]),
            base_classes=tuple(data.get("base_classes", ())),
            metaclass=data.get("metaclass", "type"),
            mro=tuple(data.get("mro", ())),
            has_slots=bool(data.get("has_slots", False)),
            has_new=bool(data.get("has_new", False)),
            has_init=bool(data.get("has_init", False)),
            has_init_subclass=bool(data.get("has_init_subclass", False)),
        )


# ---------------------------------------------------------------------------
# SignatureRecord  (§16.6 — resolved signature)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignatureRecord:
    """Full resolved signature with annotation types evaluated.

    A :class:`SignatureRecord` extends a :class:`CallableSurface` with the
    result of type-annotation resolution: each parameter's annotation string
    is resolved (following PEP-563 / ``from __future__ import annotations``
    semantics) and stored in :attr:`resolved_parameters`.  Resolution errors
    are accumulated in :attr:`resolution_errors` rather than raised, allowing
    partial signatures to carry downstream evidence.

    Theory alignment
    ----------------
    §16.6 of theory2.tex ("Signature inspection and annotation resolution")
    specifies that resolved signatures feed directly into the theorem-schema
    encoder at §16.8.  The :attr:`is_complete` flag gates whether the
    resulting :class:`~jugeo.judgments.judgment_terms.Judgment` may carry
    ``SOLVER_DISCHARGED`` trust.

    Parameters
    ----------
    qualname:
        The qualified name of the callable.
    surface:
        The underlying :class:`CallableSurface` (unresolved annotations).
    resolved_parameters:
        Tuple of :class:`ParameterSpec` objects with annotation strings
        replaced by the resolved type string (or the original string if
        resolution failed for that parameter).
    resolved_return:
        The resolved return annotation string.
    is_complete:
        ``True`` iff all annotations resolved without error.
    resolution_errors:
        Tuple of error strings for annotations that failed to resolve.

    Examples
    --------
    >>> sig.is_complete
    True
    >>> sig.serialize()["qualname"]
    'MyClass.my_method'
    """

    qualname: str
    surface: CallableSurface
    resolved_parameters: tuple[ParameterSpec, ...]
    resolved_return: str
    is_complete: bool
    resolution_errors: tuple[str, ...]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def error_count(self) -> int:
        """Return the number of annotation resolution errors.

        Returns
        -------
        int
            ``len(self.resolution_errors)``.
        """
        return len(self.resolution_errors)

    def resolved_parameter_by_name(self, name: str) -> ParameterSpec | None:
        """Look up a resolved parameter by name.

        Parameters
        ----------
        name:
            The parameter name to look up.

        Returns
        -------
        ParameterSpec | None
            The matching resolved parameter, or ``None`` if not found.
        """
        for p in self.resolved_parameters:
            if p.name == name:
                return p
        return None

    def annotation_coverage(self) -> float:
        """Return the fraction of parameters that have resolved annotations.

        Returns
        -------
        float
            In ``[0.0, 1.0]``.  ``1.0`` means all parameters (and return)
            have non-empty annotations.
        """
        total = len(self.resolved_parameters) + 1  # +1 for return
        if total == 0:
            return 1.0
        annotated = sum(
            1 for p in self.resolved_parameters if p.has_annotation()
        )
        if self.resolved_return not in ("", "inspect.Parameter.empty"):
            annotated += 1
        return annotated / total

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this signature record to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Nested structure with :attr:`surface` dict and all scalar/tuple fields.
        """
        return {
            "qualname": self.qualname,
            "surface": self.surface.serialize(),
            "resolved_parameters": [p.serialize() for p in self.resolved_parameters],
            "resolved_return": self.resolved_return,
            "is_complete": self.is_complete,
            "resolution_errors": list(self.resolution_errors),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "SignatureRecord":
        """Reconstruct a :class:`SignatureRecord` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        SignatureRecord
            The reconstructed instance.
        """
        resolved_parameters = tuple(
            ParameterSpec.parse(p) for p in data.get("resolved_parameters", [])
        )
        return cls(
            qualname=data.get("qualname", ""),
            surface=CallableSurface.parse(data["surface"]),
            resolved_parameters=resolved_parameters,
            resolved_return=data.get("resolved_return", "inspect.Parameter.empty"),
            is_complete=bool(data.get("is_complete", False)),
            resolution_errors=tuple(data.get("resolution_errors", ())),
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def parameter_spec_from_inspect(
    param: inspect.Parameter, position: int
) -> ParameterSpec:
    """Build a :class:`ParameterSpec` from a live :class:`inspect.Parameter` object.

    This is the primary construction path for :class:`ParameterSpec` when
    analysing live callables.  It extracts all five fields from the
    :class:`inspect.Parameter` object and computes the derived fields
    (:attr:`~ParameterSpec.is_optional`, :attr:`~ParameterSpec.default_repr`).

    Parameters
    ----------
    param:
        The :class:`inspect.Parameter` to convert.  Obtained from
        ``inspect.signature(callable).parameters.values()``.
    position:
        Zero-based index of this parameter in the full parameter list.

    Returns
    -------
    ParameterSpec
        The constructed spec.

    Examples
    --------
    >>> import inspect
    >>> def f(x: int, y: str = "hi") -> bool: ...
    >>> sig = inspect.signature(f)
    >>> specs = [
    ...     parameter_spec_from_inspect(p, i)
    ...     for i, p in enumerate(sig.parameters.values())
    ... ]
    >>> specs[0].name
    'x'
    >>> specs[1].has_default
    True
    >>> specs[1].default_repr
    "'hi'"
    """
    kind = ParameterKind.from_inspect(param.kind)

    # Resolve annotation to a string
    if param.annotation is inspect.Parameter.empty:
        annotation_str = "inspect.Parameter.empty"
    else:
        try:
            annotation_str = (
                param.annotation
                if isinstance(param.annotation, str)
                else getattr(param.annotation, "__name__", None)
                or getattr(param.annotation, "__qualname__", None)
                or repr(param.annotation)
            )
        except Exception:  # noqa: BLE001
            annotation_str = repr(param.annotation)

    # Resolve default
    has_default = param.default is not inspect.Parameter.empty
    if has_default:
        try:
            default_repr = repr(param.default)
        except Exception:  # noqa: BLE001
            default_repr = "<unrepresentable>"
    else:
        default_repr = ""

    is_optional = has_default or kind.is_variadic()

    return ParameterSpec(
        name=param.name,
        kind=kind,
        annotation_str=annotation_str,
        has_default=has_default,
        default_repr=default_repr,
        position=position,
        is_optional=is_optional,
    )


def callable_surface_from_qualname(
    qualname: str,
    module: str,
    coordinate: CoordinateObject | None = None,
) -> CallableSurface | None:
    """Attempt to construct a :class:`CallableSurface` by importing a callable.

    This function performs a best-effort live inspection of the callable
    identified by *module* and *qualname*.  It imports the module, walks
    the attribute chain described by *qualname*, and calls
    :func:`parameter_spec_from_inspect` on each parameter.

    Parameters
    ----------
    qualname:
        The qualified name of the callable within its module, e.g.
        ``"MyClass.my_method"`` or simply ``"my_function"``.
    module:
        The dotted module path to import, e.g. ``"mypackage.submodule"``.
    coordinate:
        Optional pre-constructed coordinate.  If ``None``, a coordinate is
        derived from the module and qualname components.

    Returns
    -------
    CallableSurface | None
        The constructed surface, or ``None`` if the module cannot be imported
        or the callable cannot be found.

    Examples
    --------
    >>> surface = callable_surface_from_qualname(
    ...     qualname="signature",
    ...     module="inspect",
    ... )
    >>> surface is not None
    True
    >>> surface.qualname
    'signature'
    """
    try:
        mod = import_module(module)
    except ImportError as exc:
        logger.debug("callable_surface_from_qualname: cannot import %r: %s", module, exc)
        return None

    # Walk the attribute chain: "Outer.Inner.method" → mod.Outer.Inner.method
    obj: Any = mod
    for part in qualname.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            logger.debug(
                "callable_surface_from_qualname: %r has no attribute %r.",
                obj,
                part,
            )
            return None

    # Unwrap staticmethod/classmethod descriptors
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__

    if not callable(obj):
        logger.debug(
            "callable_surface_from_qualname: %r.%r is not callable.", module, qualname
        )
        return None

    # Inspect the signature
    try:
        sig = inspect.signature(obj)
    except (ValueError, TypeError) as exc:
        logger.debug(
            "callable_surface_from_qualname: cannot get signature of %r: %s", obj, exc
        )
        return None

    # Build parameter specs
    params = tuple(
        parameter_spec_from_inspect(p, i)
        for i, p in enumerate(sig.parameters.values())
    )

    # Resolve return annotation
    if sig.return_annotation is inspect.Parameter.empty:
        return_annotation = "inspect.Parameter.empty"
    else:
        try:
            return_annotation = (
                sig.return_annotation
                if isinstance(sig.return_annotation, str)
                else repr(sig.return_annotation)
            )
        except Exception:  # noqa: BLE001
            return_annotation = "inspect.Parameter.empty"

    # Detect async and generator
    is_async = inspect.iscoroutinefunction(obj)
    is_generator = inspect.isgeneratorfunction(obj)

    # Build coordinate if not provided
    if coordinate is None:
        components = tuple(module.split(".") + qualname.split("."))
        coordinate = CoordinateObject(
            components=components,
            kind=CoordinateKind.FUNCTION,
        )

    return CallableSurface(
        coordinate=coordinate,
        parameters=params,
        return_annotation=return_annotation,
        is_async=is_async,
        is_generator=is_generator,
        decorators=(),
        qualname=getattr(obj, "__qualname__", qualname),
        module=getattr(obj, "__module__", module) or module,
    )


def merge_parameter_specs(
    base: tuple[ParameterSpec, ...],
    override: tuple[ParameterSpec, ...],
) -> tuple[ParameterSpec, ...]:
    """Merge two parameter-spec tuples, letting *override* replace matching entries.

    This function is used when combining a base class's parameter sequence with
    an overriding subclass's parameter sequence (e.g. for inherited ``__init__``
    signatures).  The merge strategy is:

    1. Parameters in *override* replace base parameters with the same *name*.
    2. Parameters in *base* not matched by name in *override* are retained in
       their original position.
    3. Parameters present only in *override* (new names) are appended after the
       matched entries, in the order they appear in *override*.
    4. Positions are re-indexed from 0 after merging.

    Parameters
    ----------
    base:
        The base parameter sequence (e.g. from the parent class).
    override:
        The overriding parameter sequence (e.g. from the child class).

    Returns
    -------
    tuple[ParameterSpec, ...]
        The merged sequence with positions re-indexed from 0.

    Examples
    --------
    >>> base_params = (
    ...     ParameterSpec("self", ParameterKind.POSITIONAL_OR_KEYWORD,
    ...                   "inspect.Parameter.empty", False, "", 0, False),
    ...     ParameterSpec("x", ParameterKind.POSITIONAL_OR_KEYWORD,
    ...                   "int", False, "", 1, False),
    ... )
    >>> override_params = (
    ...     ParameterSpec("x", ParameterKind.POSITIONAL_OR_KEYWORD,
    ...                   "float", True, "0.0", 0, True),
    ...     ParameterSpec("z", ParameterKind.KEYWORD_ONLY,
    ...                   "str", True, "''", 1, True),
    ... )
    >>> merged = merge_parameter_specs(base_params, override_params)
    >>> [p.name for p in merged]
    ['self', 'x', 'z']
    >>> merged[1].annotation_str  # override wins
    'float'
    >>> merged[2].name
    'z'
    """
    override_by_name: dict[str, ParameterSpec] = {p.name: p for p in override}
    override_names_used: set[str] = set()

    merged: list[ParameterSpec] = []

    # First pass: iterate base, replace with override where name matches
    for base_param in base:
        if base_param.name in override_by_name:
            merged.append(override_by_name[base_param.name])
            override_names_used.add(base_param.name)
        else:
            merged.append(base_param)

    # Second pass: append override params not yet consumed (new names)
    for override_param in override:
        if override_param.name not in override_names_used:
            merged.append(override_param)

    # Re-index positions
    reindexed = tuple(replace(p, position=i) for i, p in enumerate(merged))
    return reindexed


def build_signature_record(
    surface: CallableSurface,
    global_ns: dict[str, Any] | None = None,
    local_ns: dict[str, Any] | None = None,
) -> SignatureRecord:
    """Build a :class:`SignatureRecord` by resolving annotation strings in *surface*.

    Attempts to evaluate each annotation string (from
    :attr:`CallableSurface.parameters` and :attr:`CallableSurface.return_annotation`)
    in the provided namespaces.  Resolution errors are collected rather than
    raised.

    Parameters
    ----------
    surface:
        The :class:`CallableSurface` whose annotations should be resolved.
    global_ns:
        Optional global namespace for ``eval``.  Defaults to an empty dict.
    local_ns:
        Optional local namespace for ``eval``.  Defaults to an empty dict.

    Returns
    -------
    SignatureRecord
        The constructed record.  :attr:`~SignatureRecord.is_complete` is
        ``True`` iff no resolution errors occurred.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.models import (
    ...     build_signature_record, callable_surface_from_qualname,
    ... )
    >>> surface = callable_surface_from_qualname("getattr", "builtins")
    >>> sig = build_signature_record(surface)
    >>> sig.qualname
    'getattr'
    """
    gns: dict[str, Any] = global_ns or {}
    lns: dict[str, Any] = local_ns or {}
    errors: list[str] = []
    resolved_params: list[ParameterSpec] = []

    for param in surface.parameters:
        if not param.has_annotation():
            resolved_params.append(param)
            continue
        try:
            resolved_str = str(eval(param.annotation_str, gns, lns))  # noqa: S307
            resolved_params.append(replace(param, annotation_str=resolved_str))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"Failed to resolve annotation {param.annotation_str!r} "
                f"for parameter {param.name!r}: {exc}"
            )
            resolved_params.append(param)

    resolved_return = surface.return_annotation
    if surface.has_return_annotation():
        try:
            resolved_return = str(eval(surface.return_annotation, gns, lns))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"Failed to resolve return annotation {surface.return_annotation!r}: {exc}"
            )

    return SignatureRecord(
        qualname=surface.qualname,
        surface=surface,
        resolved_parameters=tuple(resolved_params),
        resolved_return=resolved_return,
        is_complete=len(errors) == 0,
        resolution_errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ParameterKind",
    "DescriptorKind",
    # Data models
    "ParameterSpec",
    "CallableSurface",
    "MethodBinding",
    "DescriptorRecord",
    "BoundMethod",
    "ClassConstruction",
    "SignatureRecord",
    # Helper functions
    "parameter_spec_from_inspect",
    "callable_surface_from_qualname",
    "merge_parameter_specs",
    "build_signature_record",
]
