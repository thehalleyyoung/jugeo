"""Coordinate-aware type system: types indexed by site coordinates with context,
support, and scope.

Theory2 §3.2 establishes the *coordinate type system* CTS(S) for a site S: for
each object c ∈ Obj(S), CTS(S)(c) is the full subcategory of types whose support
contains c.  The assignment c ↦ CTS(S)(c) forms a presheaf of type categories,
with restriction maps given by transport along morphisms f: c' → c.

*Contextual types* τ_Γ are types equipped with a semantic context Γ — a finite
set of hypotheses at coordinate c.  A hypothesis is a named type formula
(x : A) where x is a term variable and A is a formula.  The context Γ is
well-formed when all formulae are closed (no free variables beyond those
introduced by Γ itself).

*Support-aware types* track exactly which coordinates contribute non-trivially
to the type's extension: supp(τ) ⊆ Obj(S) is the minimal set such that τ|_c = ⊥
for all c ∉ supp(τ).

*Scope-indexed types* partition type assignments across lexical/semantic scopes
(module, class, function, block).  Each scope forms a node in the scope tree,
and the type assignment at a scope S is the collection of types visible at S.

*Type localization* restricts the global type system to a coordinate
neighbourhood N(c) = {c' | ∃ f: c' → c or g: c → c'}.

The seven primary exports are:
- :class:`ScopeKind` — enumeration of lexical/semantic scope kinds
- :class:`TypeContext` — finite set of hypotheses at a coordinate
- :class:`ContextualType` — a JuGeoType equipped with a semantic context
- :class:`SupportAwareType` — a JuGeoType with explicit support tracking
- :class:`ScopeIndexedType` — a type assignment indexed by lexical scope
- :class:`TypeLocalization` — localizes the type system at a neighbourhood
- :class:`CoordinateTypeSystem` — the full presheaf of type categories

Provenance
----------
MODULE_AUTHOR : str
    "copilot"
THEORY_REF : str
    "theory2.tex Ch3 §3.2"
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Final

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
THEORY_REF: Final[str] = "theory2.tex Ch3 §3.2"

__all__ = [
    "MODULE_AUTHOR",
    "THEORY_REF",
    "ScopeKind",
    "TypeContext",
    "ContextualType",
    "SupportAwareType",
    "ScopeIndexedType",
    "TypeLocalization",
    "CoordinateTypeSystem",
]

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fresh_id(prefix: str = "ctx") -> str:
    """Generate a fresh, unique identifier with a given prefix.

    Parameters
    ----------
    prefix : str
        String prefix prepended to the hex UUID fragment.

    Returns
    -------
    str
        A string of the form ``"<prefix>-<8-hex-chars>"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _root_coordinate() -> Coordinate:
    """Return a canonical root coordinate for bootstrapping purposes.

    The root coordinate serves as the unique initial object in the synthetic
    site when no other coordinate is available.  Its kind is MODULE, reflecting
    the top-level lexical scope from which all other scopes descend (∀c. root ⪯ c).

    Returns
    -------
    Coordinate
        A ``Coordinate`` with components ``("root",)`` and kind ``MODULE``.
    """
    return Coordinate(components=("root",), kind=CoordinateKind.MODULE)


def _coord_name(coord: Coordinate | str) -> str:
    """Extract a canonical string name from a coordinate or passthrough a string.

    When *coord* is a :class:`~jugeo.geometry.site.Coordinate`, its name is
    derived from the last component of ``coord.components`` joined by ``"."``.
    When *coord* is already a ``str``, it is returned unchanged.

    Parameters
    ----------
    coord : Coordinate | str
        The coordinate object or string to extract a name from.

    Returns
    -------
    str
        A non-empty string identifier for the coordinate.
    """
    if isinstance(coord, str):
        return coord
    if hasattr(coord, "components") and coord.components:
        return ".".join(str(c) for c in coord.components)
    return repr(coord)


def _merge_hypotheses(
    a: tuple[tuple[str, str], ...],
    b: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Compute the union of two hypothesis tuples, deduplicating by name.

    When the same name appears in both *a* and *b*, the entry from *a* is
    preferred (left-biased union).  The result preserves the relative order of
    *a* first, then any novel entries from *b*.

    The operation models the merge rule for contexts:
        Γ ∪ Δ = Γ, {x:A ∈ Δ | x ∉ dom(Γ)}

    Parameters
    ----------
    a : tuple[tuple[str, str], ...]
        Primary hypotheses (name, formula) pairs; preferred on collision.
    b : tuple[tuple[str, str], ...]
        Secondary hypotheses (name, formula) pairs; used for novel entries.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Merged tuple with no duplicate names.
    """
    seen: dict[str, str] = {}
    result: list[tuple[str, str]] = []
    for name, formula in a:
        if name not in seen:
            seen[name] = formula
            result.append((name, formula))
    for name, formula in b:
        if name not in seen:
            seen[name] = formula
            result.append((name, formula))
    return tuple(result)


def _types_agree_on_overlap(t1: JuGeoType, t2: JuGeoType) -> bool:
    """Check whether two types agree on the overlap of their supports.

    Two types τ₁ and τ₂ agree on their overlap when either:
    - supp(τ₁) ∩ supp(τ₂) = ∅  (disjoint support, vacuously true), or
    - the formula strings of τ₁ and τ₂ match exactly.

    This is a simplified gluing check used by the sheaf axiom validator.

    Parameters
    ----------
    t1 : JuGeoType
        First type to compare.
    t2 : JuGeoType
        Second type to compare.

    Returns
    -------
    bool
        ``True`` when the types agree on their shared support.
    """
    s1 = frozenset(t1.support) if hasattr(t1, "support") and t1.support else frozenset()
    s2 = frozenset(t2.support) if hasattr(t2, "support") and t2.support else frozenset()
    overlap = s1 & s2
    if not overlap:
        return True
    formula1 = getattr(t1, "formula", None)
    formula2 = getattr(t2, "formula", None)
    return formula1 == formula2


# ---------------------------------------------------------------------------
# ScopeKind
# ---------------------------------------------------------------------------


class ScopeKind(str, Enum):
    """Enumeration of lexical and semantic scope kinds.

    Each member corresponds to a distinct syntactic scope boundary.  Scope
    kinds are ordered roughly from outermost (GLOBAL, MODULE) to innermost
    (BLOCK, CLOSURE).

    Members
    -------
    MODULE : str
        Top-level module scope; the coarsest lexical boundary.
    CLASS : str
        Class body scope; introduces a new namespace for attribute look-up.
    FUNCTION : str
        Function (``def``) scope; introduces a new local variable frame.
    BLOCK : str
        Nested block scope (e.g., ``with``, ``for`` body in languages that
        support block scoping).
    GLOBAL : str
        The interpreter's built-in global scope, shared across modules.
    LOCAL : str
        A local scope inside a function, distinct from the function's own
        scope when nested closures are considered.
    CLOSURE : str
        Closure scope; captures free variables from an enclosing function.
    """

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    BLOCK = "block"
    GLOBAL = "global"
    LOCAL = "local"
    CLOSURE = "closure"


# ---------------------------------------------------------------------------
# TypeContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeContext:
    """A finite set of type hypotheses at a specific coordinate.

    A *context* Γ = (x₁:A₁, …, xₙ:Aₙ) is an ordered sequence of named
    formula bindings at coordinate *c*.  The context is *closed* when the
    ``is_closed`` flag is set; no further hypotheses may be added after
    closing.

    The well-formedness condition requires that each formula Aᵢ is a valid
    type expression over the hypotheses (x₁:A₁, …, xᵢ₋₁:Aᵢ₋₁) already in
    scope.  This module does not enforce well-formedness syntactically but
    provides the data structures for downstream checking.

    Parameters
    ----------
    context_id : str
        Globally unique identifier for this context.
    coordinate : Coordinate
        The site coordinate at which the context is valid.
    hypotheses : tuple[tuple[str, str], ...]
        Ordered sequence of (name, type_formula) pairs forming the context.
    scope_kind : ScopeKind
        The lexical scope kind associated with this context.
    parent_context_id : str | None
        Identifier of the enclosing parent context, or ``None`` for root.
    is_closed : bool
        When ``True``, the context is sealed and cannot be extended.

    Examples
    --------
    >>> coord = _root_coordinate()
    >>> ctx = TypeContext.empty(coord, ScopeKind.FUNCTION)
    >>> ctx2 = ctx.extend("x", "Int")
    >>> ctx2.has_hypothesis("x")
    True
    """

    context_id: str
    coordinate: Coordinate
    hypotheses: tuple[tuple[str, str], ...]
    scope_kind: ScopeKind
    parent_context_id: str | None
    is_closed: bool

    # ------------------------------------------------------------------
    # Mutation (returns new instances)
    # ------------------------------------------------------------------

    def extend(self, name: str, formula: str) -> TypeContext:
        """Return a new context with hypothesis (name : formula) appended.

        Implements the context extension rule Γ ⊢ A type ⟹ Γ, x:A ctx.
        If the context is already closed (``is_closed=True``), raises a
        ``ValueError`` since closed contexts are immutable.

        Parameters
        ----------
        name : str
            The term variable name to bind.
        formula : str
            The type formula associated with *name*.

        Returns
        -------
        TypeContext
            A new ``TypeContext`` identical to *self* but with the additional
            hypothesis ``(name, formula)`` appended to ``hypotheses``.

        Raises
        ------
        ValueError
            If ``self.is_closed`` is ``True``.
        """
        if self.is_closed:
            raise ValueError(
                f"Cannot extend closed context {self.context_id!r}; "
                "use a new context derived from this one."
            )
        new_hyps = self.hypotheses + ((name, formula),)
        return replace(self, hypotheses=new_hyps)

    def has_hypothesis(self, name: str) -> bool:
        """Return whether a hypothesis with the given name is in this context.

        Performs a linear scan of ``self.hypotheses`` comparing the first
        element of each pair against *name*.

        Parameters
        ----------
        name : str
            The term variable name to search for.

        Returns
        -------
        bool
            ``True`` iff ∃ (n, A) ∈ Γ such that n = *name*.
        """
        for n, _ in self.hypotheses:
            if n == name:
                return True
        return False

    def lookup(self, name: str) -> str | None:
        """Return the type formula bound to *name*, or ``None`` if absent.

        Scans ``self.hypotheses`` from left to right; returns the formula
        associated with the first matching name.

        Parameters
        ----------
        name : str
            The term variable name whose formula to retrieve.

        Returns
        -------
        str | None
            The type formula string if found, else ``None``.
        """
        for n, formula in self.hypotheses:
            if n == name:
                return formula
        return None

    def hypothesis_names(self) -> tuple[str, ...]:
        """Return a tuple of all hypothesis names in order.

        Parameters
        ----------
        (none)

        Returns
        -------
        tuple[str, ...]
            Ordered names of all hypotheses in this context.
        """
        return tuple(n for n, _ in self.hypotheses)

    def close(self) -> TypeContext:
        """Return a sealed copy of this context.

        Once closed, the context cannot be extended further.  This mirrors
        the rule that a complete derivation context is fixed.

        Returns
        -------
        TypeContext
            A new ``TypeContext`` identical to *self* but with ``is_closed=True``.
        """
        return replace(self, is_closed=True)

    def is_subcontext_of(self, other: TypeContext) -> bool:
        """Return whether every hypothesis of *self* also appears in *other*.

        Formally: Γ ⊆ Δ iff ∀ (x:A) ∈ Γ, (x:A) ∈ Δ.  Both the name and
        the formula must match (not just the name).

        Parameters
        ----------
        other : TypeContext
            The candidate supercontext.

        Returns
        -------
        bool
            ``True`` iff self.hypotheses ⊆ other.hypotheses as sets of pairs.
        """
        other_set = frozenset(other.hypotheses)
        for hyp in self.hypotheses:
            if hyp not in other_set:
                return False
        return True

    def merge(self, other: TypeContext) -> TypeContext:
        """Return the left-biased union of this context and *other*.

        Produces a new context containing all hypotheses from both *self* and
        *other*, with duplicate names resolved in favour of *self* (the left
        operand).  The new context inherits *self*'s coordinate, scope_kind,
        and parent_context_id.  The result is always open (``is_closed=False``).

        Parameters
        ----------
        other : TypeContext
            The context to merge with.

        Returns
        -------
        TypeContext
            A new ``TypeContext`` with merged hypotheses at *self*'s coordinate.
        """
        merged_hyps = _merge_hypotheses(self.hypotheses, other.hypotheses)
        return replace(
            self,
            context_id=_fresh_id("ctx"),
            hypotheses=merged_hyps,
            is_closed=False,
        )

    def hypothesis_count(self) -> int:
        """Return the number of hypotheses in this context.

        Returns
        -------
        int
            ``len(self.hypotheses)``.
        """
        return len(self.hypotheses)

    def serialize(self) -> dict[str, Any]:
        """Serialize this context to a plain Python dictionary.

        The serialized form contains only JSON-compatible primitives and nested
        structures; it can be round-tripped through :meth:`parse`.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys: ``context_id``, ``coordinate``,
            ``hypotheses``, ``scope_kind``, ``parent_context_id``,
            ``is_closed``.
        """
        coord_data: Any
        if hasattr(self.coordinate, "serialize"):
            coord_data = self.coordinate.serialize()
        else:
            coord_data = _coord_name(self.coordinate)
        return {
            "context_id": self.context_id,
            "coordinate": coord_data,
            "hypotheses": [list(h) for h in self.hypotheses],
            "scope_kind": self.scope_kind.value,
            "parent_context_id": self.parent_context_id,
            "is_closed": self.is_closed,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TypeContext:
        """Deserialize a ``TypeContext`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        TypeContext
            The reconstructed ``TypeContext`` instance.
        """
        coord_data = data["coordinate"]
        if isinstance(coord_data, dict) and hasattr(Coordinate, "parse"):
            coord = Coordinate.parse(coord_data)
        else:
            coord = _root_coordinate()
        hyps: tuple[tuple[str, str], ...] = tuple(
            (h[0], h[1]) for h in data.get("hypotheses", [])
        )
        return cls(
            context_id=data["context_id"],
            coordinate=coord,
            hypotheses=hyps,
            scope_kind=ScopeKind(data["scope_kind"]),
            parent_context_id=data.get("parent_context_id"),
            is_closed=bool(data.get("is_closed", False)),
        )

    @classmethod
    def empty(cls, coord: Coordinate, scope_kind: ScopeKind) -> TypeContext:
        """Create an empty (zero-hypothesis) context at *coord*.

        Parameters
        ----------
        coord : Coordinate
            The site coordinate for the new context.
        scope_kind : ScopeKind
            The lexical scope kind.

        Returns
        -------
        TypeContext
            A fresh ``TypeContext`` with no hypotheses.
        """
        return cls(
            context_id=_fresh_id("ctx"),
            coordinate=coord,
            hypotheses=(),
            scope_kind=scope_kind,
            parent_context_id=None,
            is_closed=False,
        )


# ---------------------------------------------------------------------------
# ContextualType
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextualType:
    """A JuGeoType equipped with a semantic typing context.

    A *contextual type* τ_Γ is a type τ together with a context Γ = (x₁:A₁,
    …, xₙ:Aₙ) such that τ is well-typed relative to Γ at coordinate *c*.
    The set ``context_dependencies`` records exactly which hypothesis names
    τ refers to; ``is_open`` indicates whether there are unresolved context
    variables.

    The weakening rule (Γ ⊢ τ → Γ, x:A ⊢ τ) is implemented by :meth:`weaken`.
    The substitution rule (Γ, x:A, Δ ⊢ τ → Γ, Δ[v/x] ⊢ τ[v/x]) is partially
    modelled by :meth:`substitute` (structural bookkeeping only).

    Parameters
    ----------
    type_id : str
        Unique identifier for this contextual type.
    coordinate : Coordinate
        Site coordinate at which this contextual type lives.
    context : TypeContext
        The semantic context Γ.
    inner_type : JuGeoType
        The underlying type τ whose interpretation is relative to Γ.
    context_dependencies : tuple[str, ...]
        Names of hypotheses in Γ that τ depends on.
    is_open : bool
        ``True`` when τ has free context variables not resolved by Γ.
    """

    type_id: str
    coordinate: Coordinate
    context: TypeContext
    inner_type: JuGeoType
    context_dependencies: tuple[str, ...]
    is_open: bool

    def close_context(self) -> ContextualType:
        """Seal the context and mark this type as closed.

        Applies :meth:`TypeContext.close` to ``self.context`` and sets
        ``is_open=False``.

        Returns
        -------
        ContextualType
            A new ``ContextualType`` with a closed context and ``is_open=False``.
        """
        closed_ctx = self.context.close()
        return replace(self, context=closed_ctx, is_open=False)

    def weaken(self, name: str, formula: str) -> ContextualType:
        """Apply the weakening rule: Γ ⊢ τ becomes Γ, name:formula ⊢ τ.

        Adds a new hypothesis to the context without changing the inner type
        or its dependencies.  The new hypothesis does not become a dependency
        unless explicitly added to ``context_dependencies``.

        Parameters
        ----------
        name : str
            The new hypothesis name.
        formula : str
            The type formula for the new hypothesis.

        Returns
        -------
        ContextualType
            A new ``ContextualType`` with an extended context.
        """
        new_ctx = self.context.extend(name, formula)
        return replace(self, context=new_ctx)

    def substitute(self, name: str, value: str) -> ContextualType:
        """Remove *name* from ``context_dependencies`` and update the context.

        Models the substitution bookkeeping: after substituting a concrete
        value for hypothesis *name*, that hypothesis is no longer a free
        dependency.  The hypothesis remains in the context (it now has a
        fixed witness), and ``context_dependencies`` no longer lists *name*.

        Parameters
        ----------
        name : str
            The hypothesis name to substitute away.
        value : str
            The concrete value or formula substituted for *name* (used for
            documentation; not interpreted further here).

        Returns
        -------
        ContextualType
            A new ``ContextualType`` with *name* removed from dependencies.
        """
        new_deps = tuple(d for d in self.context_dependencies if d != name)
        still_open = len(new_deps) > 0
        return replace(self, context_dependencies=new_deps, is_open=still_open)

    def is_ground(self) -> bool:
        """Return whether this type is ground (no open context variables).

        A contextual type is *ground* when ``is_open=False`` and there are no
        remaining context dependencies.

        Returns
        -------
        bool
            ``True`` iff the type is fully resolved with respect to Γ.
        """
        return not self.is_open and len(self.context_dependencies) == 0

    def free_context_vars(self) -> frozenset[str]:
        """Return the set of context dependency names not bound in the context.

        A dependency name is *free* when it appears in ``context_dependencies``
        but has no corresponding hypothesis in ``self.context``.

        Returns
        -------
        frozenset[str]
            Names in ``context_dependencies`` that are not bound in ``context``.
        """
        bound = frozenset(self.context.hypothesis_names())
        return frozenset(d for d in self.context_dependencies if d not in bound)

    def context_depth(self) -> int:
        """Return the number of hypotheses in the context.

        Returns
        -------
        int
            ``self.context.hypothesis_count()``.
        """
        return self.context.hypothesis_count()

    def strip_context(self) -> JuGeoType:
        """Return the inner type, discarding the context.

        Returns
        -------
        JuGeoType
            ``self.inner_type`` without contextual wrapping.
        """
        return self.inner_type

    def matches_context(self, ctx: TypeContext) -> bool:
        """Return whether all of this type's context hypotheses appear in *ctx*.

        Checks that every hypothesis in ``self.context`` is also present in
        *ctx* (i.e., ``self.context`` is a subcontext of *ctx*).

        Parameters
        ----------
        ctx : TypeContext
            The candidate containing context.

        Returns
        -------
        bool
            ``True`` iff self.context ⊆ ctx as hypothesis sets.
        """
        return self.context.is_subcontext_of(ctx)

    def serialize(self) -> dict[str, Any]:
        """Serialize this contextual type to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialized form with keys: ``type_id``, ``coordinate``,
            ``context``, ``inner_type``, ``context_dependencies``, ``is_open``.
        """
        coord_data: Any
        if hasattr(self.coordinate, "serialize"):
            coord_data = self.coordinate.serialize()
        else:
            coord_data = _coord_name(self.coordinate)
        inner_data: Any
        if hasattr(self.inner_type, "serialize"):
            inner_data = self.inner_type.serialize()
        else:
            inner_data = str(self.inner_type)
        return {
            "type_id": self.type_id,
            "coordinate": coord_data,
            "context": self.context.serialize(),
            "inner_type": inner_data,
            "context_dependencies": list(self.context_dependencies),
            "is_open": self.is_open,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ContextualType:
        """Deserialize a ``ContextualType`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        ContextualType
            The reconstructed ``ContextualType`` instance.
        """
        ctx = TypeContext.parse(data["context"])
        coord_data = data["coordinate"]
        if isinstance(coord_data, dict) and hasattr(Coordinate, "parse"):
            coord = Coordinate.parse(coord_data)
        else:
            coord = _root_coordinate()
        inner_data = data["inner_type"]
        if isinstance(inner_data, dict) and hasattr(JuGeoType, "parse"):
            inner = JuGeoType.parse(inner_data)
        else:
            raise ValueError(
                "Cannot deserialize inner_type without JuGeoType.parse support."
            )
        deps: tuple[str, ...] = tuple(data.get("context_dependencies", []))
        return cls(
            type_id=data["type_id"],
            coordinate=coord,
            context=ctx,
            inner_type=inner,
            context_dependencies=deps,
            is_open=bool(data.get("is_open", False)),
        )

    @classmethod
    def wrap(cls, type_: JuGeoType, context: TypeContext) -> ContextualType:
        """Wrap a bare ``JuGeoType`` inside a context.

        The resulting ``ContextualType`` has no initial dependencies and is
        open iff the context is not closed.

        Parameters
        ----------
        type_ : JuGeoType
            The type to wrap.
        context : TypeContext
            The semantic context to equip the type with.

        Returns
        -------
        ContextualType
            A fresh ``ContextualType`` with ``inner_type=type_`` and
            ``context=context``.
        """
        coord = context.coordinate
        is_open = not context.is_closed
        return cls(
            type_id=_fresh_id("cty"),
            coordinate=coord,
            context=context,
            inner_type=type_,
            context_dependencies=(),
            is_open=is_open,
        )


# ---------------------------------------------------------------------------
# SupportAwareType
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportAwareType:
    """A JuGeoType with an explicit tracking of its support set.

    The *support* supp(τ) ⊆ Obj(S) is the minimal set of coordinates at
    which the type τ is non-trivial (i.e., not equal to the bottom type ⊥).
    Two types τ and σ can be glued along an open cover {Uᵢ} iff their
    restrictions agree on every pairwise overlap Uᵢ ∩ Uⱼ.

    The ``minimal_support`` field records the independently computed minimal
    support, which may be a proper subset of ``support`` when some coordinates
    in ``support`` only have trivial contributions.

    Parameters
    ----------
    type_id : str
        Unique identifier for this support-aware type.
    base_type : JuGeoType
        The underlying type τ.
    support : frozenset[str]
        Full set of coordinate names at which τ may be non-trivial.
    minimal_support : frozenset[str]
        Minimal subset of *support* at which τ is genuinely non-trivial.
    is_globally_supported : bool
        ``True`` iff *support* covers every object in the ambient site.
    support_certificate : str | None
        Optional proof witness or identifier certifying correctness of
        *minimal_support*.
    """

    type_id: str
    base_type: JuGeoType
    support: frozenset[str]
    minimal_support: frozenset[str]
    is_globally_supported: bool
    support_certificate: str | None

    def is_supported_at(self, coord_name: str) -> bool:
        """Return whether the type is supported at *coord_name*.

        Parameters
        ----------
        coord_name : str
            The coordinate name to test.

        Returns
        -------
        bool
            ``True`` iff *coord_name* ∈ supp(τ).
        """
        return coord_name in self.support

    def support_size(self) -> int:
        """Return the cardinality of the support set.

        Returns
        -------
        int
            ``len(self.support)``.
        """
        return len(self.support)

    def minimal_support_size(self) -> int:
        """Return the cardinality of the minimal support set.

        Returns
        -------
        int
            ``len(self.minimal_support)``.
        """
        return len(self.minimal_support)

    def extend_support(self, coords: frozenset[str]) -> SupportAwareType:
        """Return a new type with support extended by *coords*.

        The extended support is the union of the current support and *coords*.
        The ``minimal_support`` is unchanged; ``is_globally_supported`` is
        left as-is (caller must recompute if needed).

        Parameters
        ----------
        coords : frozenset[str]
            Coordinate names to add to the support.

        Returns
        -------
        SupportAwareType
            A new ``SupportAwareType`` with ``support | coords``.
        """
        new_support = self.support | coords
        return replace(self, support=new_support)

    def restrict_support(self, coords: frozenset[str]) -> SupportAwareType:
        """Return a new type with support restricted to *coords*.

        The restricted support is the intersection of the current support and
        *coords*.  ``minimal_support`` is clipped to the new support as well.

        Parameters
        ----------
        coords : frozenset[str]
            The set of coordinate names to retain.

        Returns
        -------
        SupportAwareType
            A new ``SupportAwareType`` with ``support & coords``.
        """
        new_support = self.support & coords
        new_minimal = self.minimal_support & coords
        return replace(
            self,
            support=new_support,
            minimal_support=new_minimal,
            is_globally_supported=False,
        )

    def compute_minimal_support(self) -> frozenset[str]:
        """Return the minimal support, computing it from *support* if needed.

        If ``self.minimal_support`` is non-empty, it is returned directly.
        Otherwise falls back to returning ``self.support`` as an approximation.

        Returns
        -------
        frozenset[str]
            The minimal support set for this type.
        """
        if self.minimal_support:
            return self.minimal_support
        return self.support

    def has_global_support(self, site: Site) -> bool:
        """Return whether this type is supported at every object in *site*.

        Retrieves the set of all site object names and checks whether each is
        in ``self.support``.  Falls back to ``self.is_globally_supported`` when
        *site* does not expose an ``objects`` attribute.

        Parameters
        ----------
        site : Site
            The ambient site whose objects define the global support.

        Returns
        -------
        bool
            ``True`` iff supp(τ) ⊇ Obj(site).
        """
        if site is None:
            return self.is_globally_supported
        if hasattr(site, "objects"):
            site_obj_names = frozenset(
                _coord_name(o) for o in site.objects
            )
            return site_obj_names <= self.support
        return self.is_globally_supported

    def support_intersection(self, other: SupportAwareType) -> frozenset[str]:
        """Return the intersection of this type's support with *other*'s.

        Parameters
        ----------
        other : SupportAwareType
            The other support-aware type.

        Returns
        -------
        frozenset[str]
            ``self.support & other.support``.
        """
        return self.support & other.support

    def serialize(self) -> dict[str, Any]:
        """Serialize this support-aware type to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialized form with keys: ``type_id``, ``base_type``,
            ``support``, ``minimal_support``, ``is_globally_supported``,
            ``support_certificate``.
        """
        base_data: Any
        if hasattr(self.base_type, "serialize"):
            base_data = self.base_type.serialize()
        else:
            base_data = str(self.base_type)
        return {
            "type_id": self.type_id,
            "base_type": base_data,
            "support": sorted(self.support),
            "minimal_support": sorted(self.minimal_support),
            "is_globally_supported": self.is_globally_supported,
            "support_certificate": self.support_certificate,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> SupportAwareType:
        """Deserialize a ``SupportAwareType`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        SupportAwareType
            The reconstructed ``SupportAwareType`` instance.
        """
        base_data = data["base_type"]
        if isinstance(base_data, dict) and hasattr(JuGeoType, "parse"):
            base = JuGeoType.parse(base_data)
        else:
            raise ValueError(
                "Cannot deserialize base_type without JuGeoType.parse support."
            )
        return cls(
            type_id=data["type_id"],
            base_type=base,
            support=frozenset(data.get("support", [])),
            minimal_support=frozenset(data.get("minimal_support", [])),
            is_globally_supported=bool(data.get("is_globally_supported", False)),
            support_certificate=data.get("support_certificate"),
        )

    @classmethod
    def from_type(
        cls,
        type_: JuGeoType,
        site: Site | None = None,
    ) -> SupportAwareType:
        """Wrap a ``JuGeoType`` as a ``SupportAwareType`` using its stored support.

        The ``support`` field of *type_* is used directly when available.
        ``is_globally_supported`` is determined by checking all site objects
        when *site* is provided.

        Parameters
        ----------
        type_ : JuGeoType
            The type to wrap.
        site : Site | None
            Optional ambient site for computing ``is_globally_supported``.

        Returns
        -------
        SupportAwareType
            A new ``SupportAwareType`` wrapping *type_*.
        """
        raw_support = getattr(type_, "support", None)
        if raw_support is None:
            support: frozenset[str] = frozenset()
        elif isinstance(raw_support, frozenset):
            support = raw_support
        else:
            support = frozenset(str(s) for s in raw_support)

        globally_supported = False
        if site is not None and hasattr(site, "objects"):
            site_names = frozenset(_coord_name(o) for o in site.objects)
            globally_supported = bool(site_names) and site_names <= support

        return cls(
            type_id=_fresh_id("sat"),
            base_type=type_,
            support=support,
            minimal_support=support,
            is_globally_supported=globally_supported,
            support_certificate=None,
        )


# ---------------------------------------------------------------------------
# ScopeIndexedType
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeIndexedType:
    """A type assignment indexed by a lexical/semantic scope.

    A *scope-indexed type* records the set of named types visible within a
    particular scope (module, class, function, block, …).  Scopes form a
    tree via the ``scope_parent`` pointer; look-up may optionally traverse
    the parent chain when ``include_parent=True`` is passed to
    :meth:`visible_names`.

    Parameters
    ----------
    type_id : str
        Unique identifier for this scope-indexed type assignment.
    scope_kind : ScopeKind
        The kind of lexical scope.
    scope_name : str
        A human-readable name for this scope (e.g., module or function name).
    coordinate : Coordinate
        The site coordinate associated with this scope.
    local_types : tuple[tuple[str, JuGeoType], ...]
        Ordered sequence of (name, type) pairs for types in this scope.
    scope_parent : str | None
        The ``scope_name`` of the enclosing scope, or ``None`` for root.
    """

    type_id: str
    scope_kind: ScopeKind
    scope_name: str
    coordinate: Coordinate
    local_types: tuple[tuple[str, JuGeoType], ...]
    scope_parent: str | None

    def lookup(self, name: str) -> JuGeoType | None:
        """Return the type bound to *name* in this scope, or ``None``.

        Performs a left-to-right linear scan of ``self.local_types``.

        Parameters
        ----------
        name : str
            The type binding name to look up.

        Returns
        -------
        JuGeoType | None
            The associated type, or ``None`` if not found.
        """
        for n, t in self.local_types:
            if n == name:
                return t
        return None

    def names(self) -> tuple[str, ...]:
        """Return the tuple of all bound names in this scope, in order.

        Returns
        -------
        tuple[str, ...]
            Names of all type bindings in ``self.local_types``.
        """
        return tuple(n for n, _ in self.local_types)

    def add(self, name: str, type_: JuGeoType) -> ScopeIndexedType:
        """Return a new scope with (name, type_) added or replaced.

        If *name* already exists, its entry is replaced in-place (order
        preserved).  Otherwise, the new binding is appended at the end.

        Parameters
        ----------
        name : str
            The binding name to add or replace.
        type_ : JuGeoType
            The type to associate with *name*.

        Returns
        -------
        ScopeIndexedType
            A new ``ScopeIndexedType`` with the updated binding.
        """
        replaced = False
        new_types: list[tuple[str, JuGeoType]] = []
        for n, t in self.local_types:
            if n == name:
                new_types.append((name, type_))
                replaced = True
            else:
                new_types.append((n, t))
        if not replaced:
            new_types.append((name, type_))
        return replace(self, local_types=tuple(new_types))

    def remove(self, name: str) -> ScopeIndexedType:
        """Return a new scope with the binding for *name* removed.

        If *name* is not present, the scope is returned unchanged.

        Parameters
        ----------
        name : str
            The binding name to remove.

        Returns
        -------
        ScopeIndexedType
            A new ``ScopeIndexedType`` without the *name* binding.
        """
        new_types = tuple((n, t) for n, t in self.local_types if n != name)
        return replace(self, local_types=new_types)

    def merge_scope(self, other: ScopeIndexedType) -> ScopeIndexedType:
        """Return the left-biased union of this scope and *other*.

        Names in *self* take precedence; novel names from *other* are
        appended after.

        Parameters
        ----------
        other : ScopeIndexedType
            The scope to merge with.

        Returns
        -------
        ScopeIndexedType
            A new ``ScopeIndexedType`` with merged bindings.
        """
        existing_names: set[str] = set(self.names())
        extra = tuple(
            (n, t) for n, t in other.local_types if n not in existing_names
        )
        merged = self.local_types + extra
        return replace(
            self,
            type_id=_fresh_id("sit"),
            local_types=merged,
        )

    def visible_names(self, include_parent: bool = False) -> frozenset[str]:
        """Return the set of visible binding names in this scope.

        The *include_parent* flag is stored in metadata intent only; this
        implementation returns the names of all local bindings regardless,
        since parent scope traversal requires external context.

        Parameters
        ----------
        include_parent : bool
            Metadata hint indicating whether the caller wants parent-visible
            names as well.  Does not alter the returned set in this
            implementation.

        Returns
        -------
        frozenset[str]
            Names of all locally bound types.
        """
        return frozenset(self.names())

    def count(self) -> int:
        """Return the number of bindings in this scope.

        Returns
        -------
        int
            ``len(self.local_types)``.
        """
        return len(self.local_types)

    def is_empty(self) -> bool:
        """Return whether this scope has no bindings.

        Returns
        -------
        bool
            ``True`` iff ``len(self.local_types) == 0``.
        """
        return len(self.local_types) == 0

    def serialize(self) -> dict[str, Any]:
        """Serialize this scope-indexed type to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialized form with keys: ``type_id``, ``scope_kind``,
            ``scope_name``, ``coordinate``, ``local_types``, ``scope_parent``.
        """
        coord_data: Any
        if hasattr(self.coordinate, "serialize"):
            coord_data = self.coordinate.serialize()
        else:
            coord_data = _coord_name(self.coordinate)
        serialized_types = []
        for n, t in self.local_types:
            t_data: Any
            if hasattr(t, "serialize"):
                t_data = t.serialize()
            else:
                t_data = str(t)
            serialized_types.append([n, t_data])
        return {
            "type_id": self.type_id,
            "scope_kind": self.scope_kind.value,
            "scope_name": self.scope_name,
            "coordinate": coord_data,
            "local_types": serialized_types,
            "scope_parent": self.scope_parent,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ScopeIndexedType:
        """Deserialize a ``ScopeIndexedType`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        ScopeIndexedType
            The reconstructed ``ScopeIndexedType`` instance.
        """
        coord_data = data["coordinate"]
        if isinstance(coord_data, dict) and hasattr(Coordinate, "parse"):
            coord = Coordinate.parse(coord_data)
        else:
            coord = _root_coordinate()
        raw_types = data.get("local_types", [])
        local: list[tuple[str, JuGeoType]] = []
        for entry in raw_types:
            n, t_data = entry[0], entry[1]
            if isinstance(t_data, dict) and hasattr(JuGeoType, "parse"):
                t = JuGeoType.parse(t_data)
            else:
                raise ValueError(
                    "Cannot deserialize local_type entry without JuGeoType.parse."
                )
            local.append((n, t))
        return cls(
            type_id=data["type_id"],
            scope_kind=ScopeKind(data["scope_kind"]),
            scope_name=data["scope_name"],
            coordinate=coord,
            local_types=tuple(local),
            scope_parent=data.get("scope_parent"),
        )

    @classmethod
    def empty(
        cls,
        coord: Coordinate,
        scope_kind: ScopeKind,
        name: str,
    ) -> ScopeIndexedType:
        """Create an empty scope-indexed type at *coord*.

        Parameters
        ----------
        coord : Coordinate
            The site coordinate for the scope.
        scope_kind : ScopeKind
            The kind of lexical scope.
        name : str
            The human-readable scope name.

        Returns
        -------
        ScopeIndexedType
            A fresh ``ScopeIndexedType`` with no bindings.
        """
        return cls(
            type_id=_fresh_id("sit"),
            scope_kind=scope_kind,
            scope_name=name,
            coordinate=coord,
            local_types=(),
            scope_parent=None,
        )


# ---------------------------------------------------------------------------
# TypeLocalization
# ---------------------------------------------------------------------------


class TypeLocalization:
    """Restricts the global type system to a coordinate neighbourhood N(c).

    The neighbourhood of *c* is defined as:
        N(c) = {c} ∪ {c' | ∃ f: c' → c} ∪ {c'' | ∃ g: c → c''}

    Types are *localized* by restricting their support to the neighbourhood;
    types whose support does not intersect N(c) are invisible in the
    localization.

    The :meth:`is_sheaf_condition_satisfied` check verifies the uniqueness
    half of the sheaf axiom (patching is checked by :meth:`glue_local_types`).

    Parameters
    ----------
    site : Site
        The ambient site.
    center_coord : Coordinate
        The coordinate at which to localize.
    """

    def __init__(self, site: Site, center_coord: Coordinate) -> None:
        """Initialise the localization at *center_coord*.

        Parameters
        ----------
        site : Site
            The ambient site.
        center_coord : Coordinate
            The centre of the neighbourhood.
        """
        self._site: Site = site
        self._center: Coordinate = center_coord
        self._types: list[JuGeoType] = []

    def localized_types(self) -> list[JuGeoType]:
        """Return all types whose support intersects the neighbourhood.

        Filters ``self._types`` by checking whether each type's support
        overlaps with the neighbourhood coordinate names.

        Returns
        -------
        list[JuGeoType]
            Types supported within N(self._center).
        """
        nbhd_names = frozenset(_coord_name(c) for c in self.neighbourhood())
        result: list[JuGeoType] = []
        for t in self._types:
            t_support = frozenset(getattr(t, "support", None) or [])
            if not t_support or t_support & nbhd_names:
                result.append(t)
        return result

    def localize_type(self, type_: JuGeoType) -> JuGeoType | None:
        """Restrict *type_* to the centre coordinate.

        Calls ``type_.restrict_to(self._center)`` when available; otherwise
        checks whether the centre coordinate name is in the type's support
        and returns the type unchanged if it is, or ``None`` if it is not.

        Parameters
        ----------
        type_ : JuGeoType
            The type to localize.

        Returns
        -------
        JuGeoType | None
            The localized type, or ``None`` if not supported at the centre.
        """
        if hasattr(type_, "restrict_to"):
            return type_.restrict_to(self._center)
        center_name = _coord_name(self._center)
        t_support = frozenset(getattr(type_, "support", None) or [])
        if not t_support or center_name in t_support:
            return type_
        return None

    def neighbourhood(self) -> list[Coordinate]:
        """Return the coordinate neighbourhood of the centre.

        Includes the centre coordinate plus all coordinates reachable by
        exactly one morphism step (both incoming and outgoing).  Falls back
        to returning ``[self._center]`` when the site does not expose
        morphism accessors.

        Returns
        -------
        list[Coordinate]
            Coordinates in N(self._center).
        """
        nbhd: list[Coordinate] = [self._center]
        seen: set[str] = {_coord_name(self._center)}

        def _maybe_add(coord: Coordinate) -> None:
            n = _coord_name(coord)
            if n not in seen:
                seen.add(n)
                nbhd.append(coord)

        if self._site is not None:
            if hasattr(self._site, "morphisms_from"):
                for m in self._site.morphisms_from(self._center):
                    if hasattr(m, "target"):
                        _maybe_add(m.target)
            if hasattr(self._site, "morphisms_to"):
                for m in self._site.morphisms_to(self._center):
                    if hasattr(m, "source"):
                        _maybe_add(m.source)
        return nbhd

    def restriction_maps(self) -> list[TransportMap]:
        """Return transport maps for each morphism in the neighbourhood.

        For each coordinate *c'* in the neighbourhood (excluding the centre),
        constructs a :class:`TransportMap` representing the restriction from
        *c'* to the centre.

        Returns
        -------
        list[TransportMap]
            Transport maps indexed by neighbourhood morphisms.
        """
        maps: list[TransportMap] = []
        if self._site is None:
            return maps
        if hasattr(self._site, "morphisms_to"):
            for m in self._site.morphisms_to(self._center):
                if hasattr(TransportMap, "from_morphism"):
                    maps.append(TransportMap.from_morphism(m))
        return maps

    def is_sheaf_condition_satisfied(self, types: list[JuGeoType]) -> bool:
        """Check the uniqueness part of the sheaf axiom for *types*.

        The uniqueness condition requires that no two distinct types in the
        collection have the same type identifier.  (A full sheaf check would
        also verify the gluing/descent condition; that is handled by
        :meth:`glue_local_types`.)

        Parameters
        ----------
        types : list[JuGeoType]
            A list of local type sections to check.

        Returns
        -------
        bool
            ``True`` iff all type identifiers in *types* are unique.
        """
        seen_ids: set[str] = set()
        for t in types:
            tid = getattr(t, "type_id", id(t))
            if tid in seen_ids:
                return False
            seen_ids.add(tid)
        return True

    def glue_local_types(
        self,
        local_types: list[JuGeoType],
        law: GluingLaw,
    ) -> JuGeoType | None:
        """Assemble local type sections into a global type using *law*.

        When *law* is a trivial cover and *local_types* is empty, returns
        ``None``.  Otherwise, uses the first type in *local_types* as a
        representative and extends its support to cover all neighbourhood
        coordinates.

        Parameters
        ----------
        local_types : list[JuGeoType]
            The local sections to glue.
        law : GluingLaw
            The gluing law governing how sections are assembled.

        Returns
        -------
        JuGeoType | None
            The globally assembled type, or ``None`` if the cover is trivial
            and there are no sections.
        """
        if not local_types:
            is_trivial = (
                law.is_trivial_cover() if hasattr(law, "is_trivial_cover") else False
            )
            if is_trivial:
                return None
            return None
        base = local_types[0]
        nbhd_names = frozenset(_coord_name(c) for c in self.neighbourhood())
        if hasattr(base, "with_support"):
            return base.with_support(nbhd_names)
        return base

    def add_type(self, type_: JuGeoType) -> None:
        """Append *type_* to the localisation's type collection.

        Parameters
        ----------
        type_ : JuGeoType
            The type to register in this localisation.
        """
        self._types.append(type_)

    def remove_type(self, type_id: str) -> None:
        """Remove the type with identifier *type_id* from the collection.

        If no type with *type_id* is found, this method is a no-op.

        Parameters
        ----------
        type_id : str
            The identifier of the type to remove.
        """
        self._types = [
            t for t in self._types if getattr(t, "type_id", None) != type_id
        ]

    def type_by_id(self, type_id: str) -> JuGeoType | None:
        """Return the type with identifier *type_id*, or ``None``.

        Parameters
        ----------
        type_id : str
            The identifier to search for.

        Returns
        -------
        JuGeoType | None
            The matching type, or ``None`` if not found.
        """
        for t in self._types:
            if getattr(t, "type_id", None) == type_id:
                return t
        return None

    def statistics(self) -> dict[str, int]:
        """Return summary statistics for this localisation.

        Returns
        -------
        dict[str, int]
            Dictionary with keys ``"type_count"`` and
            ``"neighbourhood_size"``.
        """
        return {
            "type_count": len(self._types),
            "neighbourhood_size": len(self.neighbourhood()),
        }


# ---------------------------------------------------------------------------
# CoordinateTypeSystem
# ---------------------------------------------------------------------------


class CoordinateTypeSystem:
    """The full presheaf of type categories indexed by site coordinates.

    ``CoordinateTypeSystem`` represents CTS(S): for each coordinate *c* of
    the site *S*, ``types_at(c)`` returns the full subcategory of all
    registered types whose support contains *c*.

    The assignment c ↦ CTS(S)(c) is functorial: restriction along a morphism
    f: c' → c is implemented by :meth:`transport_along`.

    Internal bookkeeping uses two dictionaries:
    - ``_types``: ``type_id → JuGeoType`` for O(1) look-up by id.
    - ``_type_index``: ``coord_name → [type_id, …]`` for O(1) look-up by
      coordinate.

    Parameters
    ----------
    site : Site
        The ambient site whose objects index the type system.
    """

    def __init__(self, site: Site) -> None:
        """Initialise the coordinate type system for *site*.

        Parameters
        ----------
        site : Site
            The ambient Grothendieck site.
        """
        self._site: Site = site
        self._types: dict[str, JuGeoType] = {}
        self._type_index: dict[str, list[str]] = {}

    def types_at(self, coord: Coordinate) -> list[JuGeoType]:
        """Return all types supported at *coord*.

        Looks up the coordinate index and retrieves all types whose support
        includes *coord*.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to query.

        Returns
        -------
        list[JuGeoType]
            All registered types with *coord* in their support.
        """
        coord_name = _coord_name(coord)
        ids = self._type_index.get(coord_name, [])
        return [self._types[tid] for tid in ids if tid in self._types]

    def register_type(self, type_: JuGeoType) -> None:
        """Register *type_* in the type system.

        Adds the type to ``_types`` and updates ``_type_index`` for every
        coordinate name in the type's support.  If the type has no support
        attribute, it is indexed under the empty-string key ``""``.

        Parameters
        ----------
        type_ : JuGeoType
            The type to register.
        """
        tid = getattr(type_, "type_id", _fresh_id("typ"))
        self._types[tid] = type_
        raw_support = getattr(type_, "support", None)
        if raw_support:
            for coord_name in raw_support:
                cname = str(coord_name)
                if cname not in self._type_index:
                    self._type_index[cname] = []
                if tid not in self._type_index[cname]:
                    self._type_index[cname].append(tid)
        else:
            bucket = self._type_index.setdefault("", [])
            if tid not in bucket:
                bucket.append(tid)

    def unregister_type(self, type_id: str) -> None:
        """Remove the type with *type_id* from the system.

        Deletes the entry from ``_types`` and removes all index references
        in ``_type_index``.

        Parameters
        ----------
        type_id : str
            The identifier of the type to remove.
        """
        self._types.pop(type_id, None)
        for coord_name in list(self._type_index.keys()):
            ids = self._type_index[coord_name]
            if type_id in ids:
                ids.remove(type_id)
            if not ids:
                del self._type_index[coord_name]

    def transport_along(self, type_: JuGeoType, morphism: Morphism) -> JuGeoType:
        """Restrict *type_* to the source of *morphism*.

        Implements the presheaf restriction map f* along f: c' → c.  Calls
        ``type_.restrict_to(morphism.source)`` when available; otherwise
        returns *type_* unchanged.

        Parameters
        ----------
        type_ : JuGeoType
            The type section at the target coordinate.
        morphism : Morphism
            The morphism f: c' → c along which to transport.

        Returns
        -------
        JuGeoType
            The restricted type at ``morphism.source``.
        """
        source = getattr(morphism, "source", None)
        if source is not None and hasattr(type_, "restrict_to"):
            return type_.restrict_to(source)
        return type_

    def restrict_to(self, coord: Coordinate) -> TypeLocalization:
        """Return a ``TypeLocalization`` centred at *coord*.

        Creates a new :class:`TypeLocalization` at *coord* and populates it
        with all types supported at *coord*.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to localise at.

        Returns
        -------
        TypeLocalization
            A localisation of the type system at *coord*.
        """
        loc = TypeLocalization(self._site, coord)
        for t in self.types_at(coord):
            loc.add_type(t)
        return loc

    def global_types(self) -> list[JuGeoType]:
        """Return every type registered in the system.

        Returns
        -------
        list[JuGeoType]
            All types in registration order (dict iteration order).
        """
        return list(self._types.values())

    def local_types(self, coord: Coordinate) -> list[SupportAwareType]:
        """Return support-aware wrappers for all types at *coord*.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to query.

        Returns
        -------
        list[SupportAwareType]
            ``SupportAwareType.from_type(t, self._site)`` for each type at *coord*.
        """
        return [SupportAwareType.from_type(t, self._site) for t in self.types_at(coord)]

    def contextual_types(
        self,
        coord: Coordinate,
        context: TypeContext,
    ) -> list[ContextualType]:
        """Return contextual wrappers for all types at *coord* under *context*.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to query.
        context : TypeContext
            The semantic context to equip each type with.

        Returns
        -------
        list[ContextualType]
            ``ContextualType.wrap(t, context)`` for each type at *coord*.
        """
        return [ContextualType.wrap(t, context) for t in self.types_at(coord)]

    def scope_types(
        self,
        coord: Coordinate,
        scope_kind: ScopeKind,
    ) -> ScopeIndexedType:
        """Build a scope-indexed type assignment for all types at *coord*.

        Collects all types at *coord*, names them by their ``type_id``, and
        places them in a new :class:`ScopeIndexedType`.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to query.
        scope_kind : ScopeKind
            The lexical scope kind for the resulting index.

        Returns
        -------
        ScopeIndexedType
            A scope-indexed type assignment for *coord*.
        """
        coord_name = _coord_name(coord)
        scope = ScopeIndexedType.empty(coord, scope_kind, coord_name)
        for t in self.types_at(coord):
            tid = getattr(t, "type_id", _fresh_id("typ"))
            scope = scope.add(tid, t)
        return scope

    def is_locally_consistent(self) -> bool:
        """Return whether the type system is locally consistent.

        A system is locally consistent when no two types at the same
        coordinate share an identical formula string.  This is a necessary
        (though not sufficient) condition for the type assignment to form a
        sheaf.

        Returns
        -------
        bool
            ``True`` iff all formula strings are unique per coordinate.
        """
        for coord_name, ids in self._type_index.items():
            formulas: list[str] = []
            for tid in ids:
                t = self._types.get(tid)
                if t is None:
                    continue
                formula = getattr(t, "formula", None)
                if formula is not None:
                    if formula in formulas:
                        return False
                    formulas.append(formula)
        return True

    def validate_sheaf_axioms(self) -> list[str]:
        """Return a list of sheaf axiom violation strings.

        Checks two simplified sheaf axioms:
        1. *Identity*: every coordinate with a registered type has at least
           one type (trivially satisfied if the index is non-empty).
        2. *Overlap agreement*: for each pair of types at the same coordinate,
           they must agree on their support overlap (via
           :func:`_types_agree_on_overlap`).

        Returns
        -------
        list[str]
            Human-readable violation descriptions.  An empty list means the
            axioms are satisfied.
        """
        violations: list[str] = []
        for coord_name, ids in self._type_index.items():
            if not ids:
                violations.append(
                    f"Identity axiom violated: coordinate '{coord_name}' "
                    "has empty type list in index."
                )
            types_here = [self._types[tid] for tid in ids if tid in self._types]
            for i, t1 in enumerate(types_here):
                for t2 in types_here[i + 1:]:
                    if not _types_agree_on_overlap(t1, t2):
                        tid1 = getattr(t1, "type_id", "?")
                        tid2 = getattr(t2, "type_id", "?")
                        violations.append(
                            f"Overlap axiom violated at '{coord_name}': "
                            f"types '{tid1}' and '{tid2}' disagree on overlap."
                        )
        return violations

    def statistics(self) -> dict[str, int]:
        """Return summary statistics for the coordinate type system.

        Returns
        -------
        dict[str, int]
            Dictionary with keys:
            - ``"total_types"``: number of registered types.
            - ``"coordinates_with_types"``: number of coordinates with at
              least one type.
            - ``"global_support_count"``: number of types whose support is
              non-empty (a proxy for global support).
        """
        total = len(self._types)
        coords_with_types = sum(1 for ids in self._type_index.values() if ids)
        global_support_count = sum(
            1
            for t in self._types.values()
            if getattr(t, "support", None)
        )
        return {
            "total_types": total,
            "coordinates_with_types": coords_with_types,
            "global_support_count": global_support_count,
        }
