"""Shared data models for the jugeo ``scope_and_state`` package.

These models provide the vocabulary used across Sections 3 and 4 of Ch15
(theory2.tex Ch15) to describe Python scopes, name bindings, closure records,
module state manifests, and name-resolution outcomes within a sheaf-theoretic
setting.  Every model is either a frozen dataclass (immutable value object) or
a plain type alias, so they can be passed across subsystem boundaries safely.

Copilot-generated scaffolding — see theory2.tex Ch15 §§3–4 for the formal
treatment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from jugeo.geometry.site import CoordinateObject, CoordinateKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class NameKind(str, Enum):
    """Classifies the role of a name inside a Python scope.

    Attributes:
        LOCAL: Assigned within the current scope (no ``global``/``nonlocal``).
        PARAMETER: Received as a formal function argument.
        FREE: Referenced but not defined in the current scope; resolved in an
            enclosing scope via closure semantics.
        CLOSURE: A cell variable that has been captured into a closure object.
        GLOBAL: Explicitly declared ``global``; resolved at module level.
        BUILTIN: Lives in the Python builtins namespace.
        NONLOCAL: Explicitly declared ``nonlocal``; resolved in the nearest
            enclosing function scope.
        UNKNOWN: Kind has not yet been determined during analysis.
    """

    LOCAL = "local"
    PARAMETER = "parameter"
    FREE = "free"
    CLOSURE = "closure"
    GLOBAL = "global"
    BUILTIN = "builtin"
    NONLOCAL = "nonlocal"
    UNKNOWN = "unknown"


class ScopeKind(str, Enum):
    """Classifies the syntactic origin of a Python scope.

    Attributes:
        MODULE: Top-level module scope (globals).
        FUNCTION: ``def`` statement body.
        LAMBDA: ``lambda`` expression body.
        CLASS: ``class`` statement body.
        COMPREHENSION: List/set/dict comprehension or generator expression scope
            (Python 3 — each gets its own scope).
        GENERATOR: ``(x for x in ...)`` generator expression.
    """

    MODULE = "module"
    FUNCTION = "function"
    LAMBDA = "lambda"
    CLASS = "class"
    COMPREHENSION = "comprehension"
    GENERATOR = "generator"


# ---------------------------------------------------------------------------
# Frozen dataclasses — immutable value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NameCoordinate:
    """Pinpoints a single named binding within a specific scope.

    Acts as the finest-grained coordinate in the scope lattice: it identifies
    not just *which* name but *where* (``scope_key``) and *how* it was
    introduced (``kind``).

    Parameters:
        name: The bare identifier string, e.g. ``"x"``.
        kind: How the name was introduced (parameter, local, free, …).
        scope_key: Slash-separated key of the enclosing :class:`ScopeSection`.
        type_repr: Best-effort string representation of the inferred type,
            defaults to ``"unknown"``.
        metadata: Arbitrary key/value annotations (e.g. AST line number,
            assignment context flags).

    Returns:
        An immutable, hashable name binding descriptor.
    """

    name: str
    kind: NameKind
    scope_key: str
    type_repr: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert to a plain dictionary suitable for JSON serialisation.

        Returns:
            Serialised representation with string enum values.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "scope_key": self.scope_key,
            "type_repr": self.type_repr,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> NameCoordinate:
        """Reconstruct a :class:`NameCoordinate` from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`NameCoordinate`.

        Raises:
            KeyError: If ``name``, ``kind``, or ``scope_key`` are absent.
            ValueError: If ``kind`` is not a valid :class:`NameKind` member.
        """
        return cls(
            name=data["name"],
            kind=NameKind(data["kind"]),
            scope_key=data["scope_key"],
            type_repr=data.get("type_repr", "unknown"),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class ScopeSection:
    """Models a single Python scope as a *section* in the sheaf sense.

    In theory2.tex Ch15, each scope is a section that assigns values (or at
    least types) to its locally-bound names.  The ``bindings`` tuple enumerates
    every name visible *within* this scope (not necessarily every name visible
    *from* this scope — for that see :class:`ScopeChain`).

    Parameters:
        scope_key: Unique slash-separated identifier, e.g.
            ``"mymodule/outer/inner"``.
        scope_kind: Syntactic category of this scope.
        parent_key: ``scope_key`` of the immediately enclosing scope, or
            ``None`` for module-level scopes.
        bindings: Ordered tuple of all name bindings introduced in this scope.
        source_location: Optional human-readable source location string
            (``"file.py:42"``).
        metadata: Arbitrary annotations (e.g. AST node id, decorator flags).

    Returns:
        Immutable section descriptor.
    """

    scope_key: str
    scope_kind: ScopeKind
    parent_key: str | None
    bindings: tuple[NameCoordinate, ...]
    source_location: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict.
        """
        return {
            "scope_key": self.scope_key,
            "scope_kind": self.scope_kind.value,
            "parent_key": self.parent_key,
            "bindings": [b.serialize() for b in self.bindings],
            "source_location": self.source_location,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ScopeSection:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`ScopeSection`.

        Raises:
            KeyError: If required keys are absent.
            ValueError: If enum values are invalid.
        """
        return cls(
            scope_key=data["scope_key"],
            scope_kind=ScopeKind(data["scope_kind"]),
            parent_key=data.get("parent_key"),
            bindings=tuple(
                NameCoordinate.parse(b) for b in data.get("bindings", [])
            ),
            source_location=data.get("source_location", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class ClosureRecord:
    """Captures the closure relationship for a single nested function.

    A closure record is produced when a function scope references names from
    an enclosing scope.  It records the full set of free variables (as
    :class:`NameCoordinate` objects) and the chain of enclosing scope keys.

    Parameters:
        function_key: ``scope_key`` of the inner (closed-over) function.
        enclosing_keys: Ordered tuple of enclosing scope keys from innermost
            to outermost enclosing function/module.
        free_variables: Tuple of :class:`NameCoordinate` objects that are free
            in the inner function and captured from some enclosing scope.
        all_free_names: Plain string names of all free variables, for quick
            membership checks.
        depth: Nesting depth of the closure (0 = not nested, 1 = one level of
            enclosure, …).
        metadata: Arbitrary annotations.

    Returns:
        An immutable closure descriptor.
    """

    function_key: str
    enclosing_keys: tuple[str, ...]
    free_variables: tuple[NameCoordinate, ...]
    all_free_names: tuple[str, ...]
    depth: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict.
        """
        return {
            "function_key": self.function_key,
            "enclosing_keys": list(self.enclosing_keys),
            "free_variables": [fv.serialize() for fv in self.free_variables],
            "all_free_names": list(self.all_free_names),
            "depth": self.depth,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ClosureRecord:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`ClosureRecord`.

        Raises:
            KeyError: If required keys are absent.
        """
        return cls(
            function_key=data["function_key"],
            enclosing_keys=tuple(data.get("enclosing_keys", [])),
            free_variables=tuple(
                NameCoordinate.parse(fv)
                for fv in data.get("free_variables", [])
            ),
            all_free_names=tuple(data.get("all_free_names", [])),
            depth=data.get("depth", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class ModuleStateManifest:
    """Point-in-time snapshot of a module's global namespace as a section.

    In Ch15 §4, the global namespace of a module is modelled as a section over
    the module coordinate.  This manifest captures that section at a given
    ``version`` epoch.

    Parameters:
        module_name: Dotted module name, e.g. ``"mypackage.mymodule"``.
        module_coordinate: The :class:`CoordinateObject` identifying this module
            in the semantic site.
        global_names: Tuple of globally-bound names at this version.
        type_reprs: Mapping from name to type-representation string.
        version: Monotonically increasing integer epoch counter.
        metadata: Arbitrary annotations.

    Returns:
        Immutable module state descriptor.
    """

    module_name: str
    module_coordinate: CoordinateObject
    global_names: tuple[str, ...]
    type_reprs: Mapping[str, str]
    version: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict (note: ``module_coordinate`` is serialised
            via its own ``serialize()`` method).
        """
        return {
            "module_name": self.module_name,
            "module_coordinate": self.module_coordinate.serialize(),
            "global_names": list(self.global_names),
            "type_reprs": dict(self.type_reprs),
            "version": self.version,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(
        cls, data: dict[str, Any], coord: CoordinateObject
    ) -> ModuleStateManifest:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.
            coord: The already-reconstructed :class:`CoordinateObject` for the
                module (callers must resolve this separately).

        Returns:
            A freshly constructed :class:`ModuleStateManifest`.

        Raises:
            KeyError: If required keys are absent.
        """
        return cls(
            module_name=data["module_name"],
            module_coordinate=coord,
            global_names=tuple(data.get("global_names", [])),
            type_reprs=data.get("type_reprs", {}),
            version=data.get("version", 0),
            metadata=data.get("metadata", {}),
        )


# Type alias — a mutable mapping from bare name strings to coordinates.
BindingMap = dict[str, NameCoordinate]


@dataclass(frozen=True, slots=True)
class NameResolutionResult:
    """Outcome of resolving a name through a :class:`ScopeChain`.

    Parameters:
        name: The bare name that was looked up.
        resolved: ``True`` if the name was found somewhere in the chain.
        coordinate: The :class:`NameCoordinate` where it was found, or
            ``None`` if not resolved.
        scope_key: The ``scope_key`` of the scope that contained the binding,
            or ``None`` if not resolved.
        resolution_path: Ordered tuple of ``scope_key`` values that were
            visited during the search (innermost first).
        error_message: Human-readable description of why resolution failed,
            empty string on success.

    Returns:
        Immutable resolution outcome.
    """

    name: str
    resolved: bool
    coordinate: NameCoordinate | None
    scope_key: str | None
    resolution_path: tuple[str, ...]
    error_message: str = ""

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict.
        """
        return {
            "name": self.name,
            "resolved": self.resolved,
            "coordinate": (
                self.coordinate.serialize() if self.coordinate else None
            ),
            "scope_key": self.scope_key,
            "resolution_path": list(self.resolution_path),
            "error_message": self.error_message,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> NameResolutionResult:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`NameResolutionResult`.

        Raises:
            KeyError: If required keys are absent.
        """
        return cls(
            name=data["name"],
            resolved=data["resolved"],
            coordinate=(
                NameCoordinate.parse(data["coordinate"])
                if data.get("coordinate")
                else None
            ),
            scope_key=data.get("scope_key"),
            resolution_path=tuple(data.get("resolution_path", [])),
            error_message=data.get("error_message", ""),
        )

    @classmethod
    def not_found(
        cls, name: str, path: tuple[str, ...]
    ) -> NameResolutionResult:
        """Convenience constructor for a failed resolution.

        Parameters:
            name: The bare name that could not be resolved.
            path: The scope keys visited before giving up.

        Returns:
            A :class:`NameResolutionResult` with ``resolved=False``.
        """
        return cls(
            name=name,
            resolved=False,
            coordinate=None,
            scope_key=None,
            resolution_path=path,
            error_message=f"Name '{name}' not found in scope chain",
        )


@dataclass(frozen=True, slots=True)
class ScopeChain:
    """Ordered sequence of scopes from innermost to outermost.

    Implements LEGB-style name resolution by walking from the innermost scope
    outward.  In the sheaf model, this corresponds to pulling back a section
    along the inclusion morphism chain.

    Parameters:
        scopes: Tuple of :class:`ScopeSection` objects, innermost first.
        module_key: The ``scope_key`` of the enclosing module scope.

    Returns:
        Immutable scope chain descriptor.
    """

    scopes: tuple[ScopeSection, ...]
    module_key: str

    @property
    def innermost(self) -> ScopeSection | None:
        """Return the innermost scope, or ``None`` if the chain is empty."""
        return self.scopes[0] if self.scopes else None

    @property
    def outermost(self) -> ScopeSection | None:
        """Return the outermost scope, or ``None`` if the chain is empty."""
        return self.scopes[-1] if self.scopes else None

    @property
    def depth(self) -> int:
        """Number of scopes in the chain."""
        return len(self.scopes)

    def resolve(self, name: str) -> NameResolutionResult:
        """Resolve a name by walking from innermost to outermost scope.

        Parameters:
            name: The bare identifier to look up.

        Returns:
            A :class:`NameResolutionResult` describing where (if anywhere)
            the name was found.
        """
        path: list[str] = []
        for scope in self.scopes:
            path.append(scope.scope_key)
            for binding in scope.bindings:
                if binding.name == name:
                    return NameResolutionResult(
                        name=name,
                        resolved=True,
                        coordinate=binding,
                        scope_key=scope.scope_key,
                        resolution_path=tuple(path),
                    )
        return NameResolutionResult.not_found(name, tuple(path))

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict.
        """
        return {
            "scopes": [s.serialize() for s in self.scopes],
            "module_key": self.module_key,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ScopeChain:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`ScopeChain`.

        Raises:
            KeyError: If required keys are absent.
        """
        return cls(
            scopes=tuple(
                ScopeSection.parse(s) for s in data.get("scopes", [])
            ),
            module_key=data["module_key"],
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "NameKind",
    "ScopeKind",
    "NameCoordinate",
    "ScopeSection",
    "ClosureRecord",
    "ModuleStateManifest",
    "BindingMap",
    "NameResolutionResult",
    "ScopeChain",
]
