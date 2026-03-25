"""JavaScript lexical scopes modelled as a Grothendieck site.

In sheaf-theoretic terms, JavaScript's lexical scope system forms a
Grothendieck site where:

* **Objects** — scopes (global, module, function, block, catch, with, eval …)
* **Morphisms** — scope containment (inner scope → outer scope, i.e. inclusion)
* **Covering families** — the direct child scopes of a function scope jointly
  cover that scope, mirroring the variable-resolution rule: any identifier
  accessible in a scope is accessible from at least one of its inner scopes.

This correspondence lets us apply sheaf-descent to study closures, hoisting,
and the temporal dead zone.  A captured variable becomes a section of the
structure sheaf on the site; descent ensures it can be reconstructed from the
data visible on the inner-scope cover even after the outer frame is gone.
"""

from __future__ import annotations

__all__ = [
    "ScopeKind",
    "VariableKind",
    "JSScopeCoordinate",
    "JSScopeChain",
    "ClosureSection",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
    GrothendieckTopology,
)
from jugeo.geometry.descent import LocalSection, DescentResult  # noqa: F401


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ScopeKind(str, Enum):
    """The kinds of lexical scope that JavaScript can create.

    Each member names the JS construct responsible for introducing the scope:

    * ``GLOBAL``     — The outermost script scope; ``var`` declarations land
                       here when no enclosing function exists.
    * ``MODULE``     — An ES-module file scope; implicitly strict, top-level
                       ``const``/``let``/``class`` declarations live here.
    * ``FUNCTION``   — A ``function`` declaration, expression, or method body;
                       also covers ``async function`` and ``function*``.
    * ``BLOCK``      — Any ``{}`` block not belonging to a function or class
                       (bare blocks, ``if``/``else`` bodies, ``try`` bodies).
    * ``CATCH``      — The ``catch (e) { … }`` clause; introduces the binding
                       ``e`` with ``CATCH_BINDING`` kind.
    * ``FOR_OF``     — A ``for … of`` loop; the iteration variable is
                       block-scoped per iteration.
    * ``FOR_IN``     — A ``for … in`` loop; iteration variable is block-scoped.
    * ``SWITCH``     — A ``switch`` statement body (single block scope shared
                       by all ``case`` clauses).
    * ``WITH``       — A ``with (obj) { … }`` block; forbidden in strict mode.
    * ``EVAL``       — An ``eval("…")`` call; scope behaviour depends on
                       whether the enclosing context is in strict mode.
    * ``CLASS_BODY`` — The body of a ``class`` declaration or expression; the
                       class name binding and static initialisation blocks live
                       here, and the class body is implicitly strict.
    """

    GLOBAL = "global"
    MODULE = "module"
    FUNCTION = "function"
    BLOCK = "block"
    CATCH = "catch"
    FOR_OF = "for_of"
    FOR_IN = "for_in"
    SWITCH = "switch"
    WITH = "with"
    EVAL = "eval"
    CLASS_BODY = "class_body"


class VariableKind(str, Enum):
    """The binding kind of a declared identifier in a JavaScript scope.

    Scoping and hoisting rules differ per kind:

    * ``VAR``           — Function-scoped (or global-scoped); hoisted to the
                          nearest enclosing function/global boundary and
                          initialised to ``undefined`` before the body runs.
    * ``LET``           — Block-scoped; enters the *temporal dead zone* (TDZ)
                          from scope entry until the declaration statement is
                          reached at runtime.
    * ``CONST``         — Block-scoped; same TDZ rules as ``LET``; cannot be
                          re-assigned after initialisation.
    * ``FUNCTION_DECL`` — Hoisted with its complete function-object value to the
                          top of the enclosing function/global scope (before any
                          statement executes).
    * ``CLASS_DECL``    — Block-scoped; subject to the TDZ exactly like ``LET``.
    * ``IMPORT``        — Module-scope; a live binding to the exported value from
                          another module; read-only from the importing side.
    * ``PARAMETER``     — Function-scope; initialised with the caller-supplied
                          argument when the function is invoked.
    * ``CATCH_BINDING`` — Catch-clause scope; initialised with the thrown value
                          when the ``catch`` block is entered.
    """

    VAR = "var"
    LET = "let"
    CONST = "const"
    FUNCTION_DECL = "function_decl"
    CLASS_DECL = "class_decl"
    IMPORT = "import"
    PARAMETER = "parameter"
    CATCH_BINDING = "catch_binding"


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

#: Scope kinds that act as a hoisting boundary for ``var`` declarations.
_FUNCTION_BOUNDARY_KINDS: frozenset[ScopeKind] = frozenset({
    ScopeKind.FUNCTION,
    ScopeKind.GLOBAL,
    ScopeKind.MODULE,
    ScopeKind.EVAL,
})

#: Variable kinds subject to the temporal dead zone before initialisation.
_TDZ_KINDS: frozenset[VariableKind] = frozenset({
    VariableKind.LET,
    VariableKind.CONST,
    VariableKind.CLASS_DECL,
})

#: Variable kinds hoisted with a concrete value (the function object).
_HOISTED_WITH_VALUE: frozenset[VariableKind] = frozenset({
    VariableKind.FUNCTION_DECL,
})

#: Variable kinds hoisted as ``undefined`` before the scope body runs.
_HOISTED_AS_UNDEFINED: frozenset[VariableKind] = frozenset({
    VariableKind.VAR,
})

#: Variable kinds that are fully initialised at scope entry (no hoisting needed).
_INITIALIZED_AT_ENTRY: frozenset[VariableKind] = frozenset({
    VariableKind.PARAMETER,
    VariableKind.CATCH_BINDING,
    VariableKind.IMPORT,
})


def _scope_kind_to_coordinate_kind(kind: ScopeKind) -> CoordinateKind:
    """Map a :class:`ScopeKind` to the closest :class:`CoordinateKind`."""
    if kind == ScopeKind.MODULE:
        return CoordinateKind.MODULE
    if kind == ScopeKind.FUNCTION:
        return CoordinateKind.FUNCTION
    if kind == ScopeKind.CLASS_BODY:
        return CoordinateKind.INTERFACE
    return CoordinateKind.REGION


# ---------------------------------------------------------------------------
# JSScopeCoordinate
# ---------------------------------------------------------------------------


@dataclass
class JSScopeCoordinate:
    """A single JavaScript lexical scope, presented as a site coordinate.

    Parameters
    ----------
    coord_name:
        Unique identifier for this scope (e.g. ``"global"``,
        ``"module:app.js"``, ``"fn:handleClick#3"``).
    scope_kind:
        Which JS construct introduced this scope.
    parent_name:
        The ``coord_name`` of the immediately enclosing scope, or ``None``
        for the global/module root.
    variables:
        Mapping from identifier name to its binding kind as declared in
        *this* scope (not hoisted into it from a child).
    is_strict_mode:
        ``True`` when the scope executes in ECMAScript strict mode.
    is_async:
        ``True`` for ``async function`` bodies.
    is_generator:
        ``True`` for ``function*`` generator bodies.
    """

    coord_name: str
    scope_kind: ScopeKind
    parent_name: str | None = None
    variables: dict[str, VariableKind] = field(default_factory=dict)
    is_strict_mode: bool = False
    is_async: bool = False
    is_generator: bool = False

    # -- site integration ----------------------------------------------------

    def to_coordinate(self) -> Coordinate:
        """Convert this scope to a :class:`Coordinate` for use in a :class:`Site`.

        The coordinate's ``components`` are derived from ``coord_name`` and the
        ``kind`` reflects the closest analogue in :class:`CoordinateKind`.
        """
        return Coordinate(
            components=(self.coord_name,),
            kind=_scope_kind_to_coordinate_kind(self.scope_kind),
            metadata={
                "scope_kind": self.scope_kind.value,
                "parent_name": self.parent_name,
                "is_strict_mode": self.is_strict_mode,
                "is_async": self.is_async,
                "is_generator": self.is_generator,
            },
        )

    # -- variable queries ----------------------------------------------------

    def has_binding(self, name: str) -> bool:
        """Return ``True`` if ``name`` is directly bound in this scope."""
        return name in self.variables

    def binding_kind(self, name: str) -> VariableKind | None:
        """Return the :class:`VariableKind` for ``name``, or ``None`` if absent."""
        return self.variables.get(name)

    def is_temporal_dead_zone(self, name: str, access_before_init: bool) -> bool:
        """Return ``True`` when accessing ``name`` would trigger a TDZ error.

        A ``LET``, ``CONST``, or ``CLASS_DECL`` binding is in the temporal dead
        zone from the very first instruction of its enclosing block until the
        point at which its declaration is evaluated.  Any read or write of the
        identifier before that point raises a ``ReferenceError`` at runtime.

        Parameters
        ----------
        name:
            The identifier being accessed.
        access_before_init:
            ``True`` when the access site textually precedes the declaration
            (modelling a pre-initialisation access inside the same block).
        """
        kind = self.variables.get(name)
        if kind is None:
            return False
        return kind in _TDZ_KINDS and access_before_init


# ---------------------------------------------------------------------------
# JSScopeChain
# ---------------------------------------------------------------------------


class JSScopeChain:
    """A Grothendieck site whose objects are JavaScript lexical scopes.

    The site encodes:

    * **Objects** — every registered :class:`JSScopeCoordinate`.
    * **Morphisms** — inclusion arrows ``inner → outer`` for each parent-child
      relationship in the scope hierarchy.
    * **Covering families** — the collection of all direct child scopes of a
      given scope forms a covering family (any variable reachable in the parent
      can be reached from at least one child).

    The class provides high-level helpers for variable resolution, hoisting
    analysis, and closure-capture computation built on top of the underlying
    :class:`Site`.
    """

    def __init__(self) -> None:
        self._scopes: dict[str, JSScopeCoordinate] = {}
        self._site: Site = Site(
            topology=GrothendieckTopology(name="js_scope_chain"),
            label="JavaScript Scope Chain",
        )

    # -- registration --------------------------------------------------------

    def add_scope(self, scope: JSScopeCoordinate) -> None:
        """Register a scope and update the site with its coordinate and morphisms.

        If the scope's ``parent_name`` is already registered an inclusion
        morphism ``inner → parent`` is added immediately.  Conversely, any
        previously registered child that named this scope as its parent will
        have its morphism added retroactively, so registration order does not
        matter.

        Parameters
        ----------
        scope:
            The scope to register.

        Raises
        ------
        ValueError
            If a scope with the same ``coord_name`` is already registered.
        """
        if scope.coord_name in self._scopes:
            raise ValueError(
                f"Scope {scope.coord_name!r} is already registered."
            )
        self._scopes[scope.coord_name] = scope
        coord = scope.to_coordinate()
        self._site.add_coordinate(coord)

        # Link to parent if it is already known.
        if scope.parent_name is not None and scope.parent_name in self._scopes:
            parent_coord = self._scopes[scope.parent_name].to_coordinate()
            self._site.add_morphism(Morphism(
                source=coord,
                target=parent_coord,
                kind=MorphismKind.INCLUSION,
                label=f"{scope.coord_name}→{scope.parent_name}",
            ))

        # Retrospectively link children registered before their parent.
        for child in self._scopes.values():
            if child.parent_name != scope.coord_name:
                continue
            if child.coord_name == scope.coord_name:
                continue
            child_coord = child.to_coordinate()
            already_linked = any(
                m.source == child_coord and m.target == coord
                for m in self._site.morphisms_from(child_coord)
            )
            if not already_linked:
                self._site.add_morphism(Morphism(
                    source=child_coord,
                    target=coord,
                    kind=MorphismKind.INCLUSION,
                    label=f"{child.coord_name}→{scope.coord_name}",
                ))

    # -- traversal -----------------------------------------------------------

    def scope_chain_for(self, scope_name: str) -> list[str]:
        """Return the ancestor chain from ``scope_name`` up to the root.

        The first element is ``scope_name`` itself; the last is the outermost
        scope with no parent (typically the global or module scope).

        Parameters
        ----------
        scope_name:
            Name of the scope to start from.

        Raises
        ------
        KeyError
            If ``scope_name`` is not registered.
        """
        if scope_name not in self._scopes:
            raise KeyError(f"Unknown scope: {scope_name!r}")
        chain: list[str] = []
        current: str | None = scope_name
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                break  # Guard against malformed cycles.
            seen.add(current)
            chain.append(current)
            scope = self._scopes.get(current)
            if scope is None:
                break
            current = scope.parent_name
        return chain

    def _direct_children(self, scope_name: str) -> list[str]:
        """Return names of all scopes whose ``parent_name`` is ``scope_name``."""
        return [
            s.coord_name
            for s in self._scopes.values()
            if s.parent_name == scope_name
        ]

    # -- variable resolution -------------------------------------------------

    def resolve_variable(
        self, name: str, from_scope: str
    ) -> tuple[str, VariableKind] | None:
        """Walk the scope chain to find where ``name`` is bound.

        Resolution follows the ECMAScript specification: the lookup starts at
        ``from_scope`` and ascends through ancestors until a binding is found.
        ``LET``, ``CONST``, ``CLASS_DECL``, ``IMPORT``, ``PARAMETER``, and
        ``CATCH_BINDING`` are resolved at the scope where they are declared.
        ``VAR`` and ``FUNCTION_DECL`` are likewise stored at their declaration
        scope (after hoisting); use :meth:`hoisting_analysis` to determine
        effective availability before the scope body runs.

        Parameters
        ----------
        name:
            The identifier to resolve.
        from_scope:
            The scope in which the lookup originates.

        Returns
        -------
        A ``(scope_name, kind)`` pair identifying the binding, or ``None`` if
        the identifier is not found anywhere in the chain (``ReferenceError``).
        """
        try:
            chain = self.scope_chain_for(from_scope)
        except KeyError:
            return None
        for scope_name in chain:
            scope = self._scopes[scope_name]
            kind = scope.binding_kind(name)
            if kind is not None:
                return (scope_name, kind)
        return None

    # -- closure analysis ----------------------------------------------------

    def closure_captures(
        self, inner_scope: str, outer_scope: str
    ) -> list[str]:
        """Return variables from ``outer_scope`` that ``inner_scope`` can capture.

        A variable declared in ``outer_scope`` is *capturable* when no
        intermediate scope between ``inner_scope`` (inclusive) and
        ``outer_scope`` (exclusive) declares a shadowing binding for the same
        name.  These are exactly the variables that a closure created in
        ``inner_scope`` would hold a reference to from ``outer_scope``'s frame.

        In sheaf-theoretic terms, these are the sections of the variable-binding
        sheaf on ``outer_scope`` whose restriction to ``inner_scope`` is
        non-trivial — i.e., they survive along the morphism chain.

        Parameters
        ----------
        inner_scope:
            Name of the enclosed (inner) function scope.
        outer_scope:
            Name of the enclosing scope whose variables may be captured.

        Returns
        -------
        Sorted list of variable names from ``outer_scope`` not shadowed by any
        intermediate scope.
        """
        inner_chain = self.scope_chain_for(inner_scope)
        if outer_scope not in inner_chain:
            return []
        outer_obj = self._scopes.get(outer_scope)
        if outer_obj is None:
            return []

        # Scopes strictly between inner (inclusive) and outer (exclusive).
        idx = inner_chain.index(outer_scope)
        intermediate = inner_chain[:idx]

        shadowed: set[str] = set()
        for scope_name in intermediate:
            shadowed.update(self._scopes[scope_name].variables.keys())

        return sorted(
            var for var in outer_obj.variables if var not in shadowed
        )

    # -- hoisting analysis ---------------------------------------------------

    def hoisting_analysis(self, scope_name: str) -> dict[str, str]:
        """Classify each binding in ``scope_name`` by its state before body runs.

        Returns a ``{identifier: status}`` mapping where ``status`` is one of:

        ``"hoisted"``
            Available before the first statement — either as ``undefined``
            (``var``) or with its full value (``function_decl``).
        ``"tdz"``
            In the temporal dead zone; accessing the name raises
            ``ReferenceError`` until the declaration statement is reached.
        ``"initialized"``
            Immediately available with a concrete value (function parameters,
            catch bindings, ``import`` live-bindings).

        Parameters
        ----------
        scope_name:
            The scope to analyse.

        Raises
        ------
        KeyError
            If ``scope_name`` is not registered.
        """
        if scope_name not in self._scopes:
            raise KeyError(f"Unknown scope: {scope_name!r}")
        scope = self._scopes[scope_name]
        result: dict[str, str] = {}
        for var_name, kind in scope.variables.items():
            if kind in _HOISTED_AS_UNDEFINED or kind in _HOISTED_WITH_VALUE:
                result[var_name] = "hoisted"
            elif kind in _TDZ_KINDS:
                result[var_name] = "tdz"
            else:
                result[var_name] = "initialized"
        return result

    # -- Grothendieck topology -----------------------------------------------

    def scope_covering(self, parent: str) -> CoveringFamily:
        """Build a covering family for ``parent`` from its direct child scopes.

        In the Grothendieck topology on the scope site, the collection of all
        direct child scopes of a function scope forms a *covering family*: any
        variable reachable in the parent is also reachable from at least one
        child, which mirrors the sheaf gluing condition — global data can be
        reconstructed from local sections on a cover.

        The family is registered in the underlying :class:`Site`'s topology so
        that descent computations can use it.

        Parameters
        ----------
        parent:
            The scope name whose children form the cover.

        Returns
        -------
        A :class:`CoveringFamily` whose ``base`` is the parent coordinate and
        whose ``members`` are inclusion morphisms from each child scope.

        Raises
        ------
        KeyError
            If ``parent`` is not registered.
        """
        if parent not in self._scopes:
            raise KeyError(f"Unknown scope: {parent!r}")
        parent_coord = self._scopes[parent].to_coordinate()
        members: list[Morphism] = [
            Morphism(
                source=self._scopes[child_name].to_coordinate(),
                target=parent_coord,
                kind=MorphismKind.INCLUSION,
                label=f"{child_name}→{parent}",
            )
            for child_name in self._direct_children(parent)
        ]
        family = CoveringFamily(
            base=parent_coord,
            members=members,
            label=f"cover({parent})",
        )
        self._site.add_covering_family(family)
        return family


# ---------------------------------------------------------------------------
# ClosureSection
# ---------------------------------------------------------------------------


@dataclass
class ClosureSection:
    """A closure modelled as a local section that survives outer scope destruction.

    In sheaf-theoretic terms, a closure is a section of the variable-binding
    sheaf restricted to the inner function's coordinate.  When the outer scope's
    call frame is popped (the enclosing function returns), the section "descends"
    to the inner scope and persists there — exactly the descent/gluing property:
    local data on a cover glues to a global section that survives beyond the
    cover's own lifetime.

    Parameters
    ----------
    closure_scope:
        The ``coord_name`` of the inner function scope that holds the closure.
    captured_vars:
        Mapping from variable name to its captured value at closure-creation
        time.
    outer_scope_destroyed:
        ``True`` when the outer scope's execution context no longer exists;
        the closure is now the sole reference to the captured bindings.
    """

    closure_scope: str
    captured_vars: dict[str, Any] = field(default_factory=dict)
    outer_scope_destroyed: bool = False

    def to_local_section(self) -> LocalSection:
        """Convert this closure to a :class:`LocalSection` on the scope site.

        The section's ``coordinate`` is the closure's scope name.  Each
        captured variable becomes an entry in ``judgment_data``.  The trust
        level is ``1.0`` while the outer scope is still alive (fully evidenced)
        and drops to ``0.8`` once it has been destroyed (reduced verifiability
        once the original frame is gone; open obligations remain for each
        captured binding).

        Returns
        -------
        A :class:`LocalSection` representing the closure's captured state.
        """
        trust = 0.8 if self.outer_scope_destroyed else 1.0
        provenance: tuple[str, ...] = (
            ("closure:scope_destroyed",)
            if self.outer_scope_destroyed
            else ("closure:scope_alive",)
        )
        obligations: list[str] = (
            [f"verify_capture:{name}" for name in self.captured_vars]
            if self.outer_scope_destroyed
            else []
        )
        return LocalSection(
            coordinate=self.closure_scope,
            judgment_data=dict(self.captured_vars),
            evidence_bundle=tuple(
                f"capture:{name}" for name in self.captured_vars
            ),
            trust_level=trust,
            provenance=provenance,
            is_partial=self.outer_scope_destroyed,
            residual_obligations=obligations,
        )
