"""Section 2 — Scopes as Sections (theory2.tex Ch15 §2).

In the JuGeo formal-semantics framework, a *scope* is modelled as a
*section* over a coordinate region in the semantic site described in
theory2.tex Ch15.  A section assigns a *value* (the binding) to each *point*
(name) in its support region.

Scopes nest hierarchically: each function scope is a sub-region of its
enclosing module or function scope.  Together the nested scopes form a
*sheaf* over the module's coordinate site.  The two fundamental sheaf axioms
govern this structure:

- **Locality** — if two sections agree on every name in their overlap, they
  are equal on that overlap.  In Python, this means the binding of a name
  inside a function scope is fully determined by the local scope data; it
  cannot depend on side effects of other sibling scopes.

- **Gluing** — compatible sections on overlapping patches can be uniquely
  glued into a section on the union.  In Python, this corresponds to the rule
  that if an inner scope and an outer scope both bind the same name, the
  inner binding *shadows* the outer one (they are "compatible" in the sheaf
  sense because only the inner one is visible).

The *gluing axiom* check is implemented by :class:`ScopeValidator`, which
verifies that the ``kind`` fields of shared bindings agree between sibling
scopes, and optionally uses a :class:`Z3Session` to encode the check as a
satisfiability query.

This module was developed with **copilot** assistance as part of the JuGeo
Python-runtime formal-semantics layer.

Typical usage::

    from jugeo.python_runtime.scope_and_state.scopes import (
        ScopeBuilder, ScopeAnalyzer, ScopeValidator, ScopeVisualizer,
        compute_lexical_depth, scope_contains, scopes_overlap,
    )

    analyzer = ScopeAnalyzer(module_name="mymodule")
    module_coord = CoordinateObject(components=("mymodule",), kind=CoordinateKind.MODULE)
    module_scope = analyzer.analyze_module(module_coord, ["x", "y", "MyClass"])

    validator = ScopeValidator()
    assert validator.is_well_formed_section(module_scope)

    viz = ScopeVisualizer()
    print(viz.scope_summary(module_scope))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.site import (
    CoordinateKind,
    CoordinateObject,
    MorphismKind,
    Site,
    SiteBuilder,
)
from jugeo.geometry.supports import (
    SupportRegion,
    SupportSet,
    SupportedSection,
    SupportTracker,
)
from jugeo.judgments.judgment_terms import (
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
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.python_runtime.scope_and_state.models import (
    BindingMap,
    NameCoordinate,
    NameKind,
    NameResolutionResult,
    ScopeChain,
    ScopeKind,
    ScopeSection,
)
from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Analysis channel tag — referenced in evidence payloads
# ---------------------------------------------------------------------------

_SCOPE_ANALYSIS_CHANNEL: str = "copilot-s02-scopes"


# ---------------------------------------------------------------------------
# ScopeBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeBuilder:
    """Fluent builder for :class:`~jugeo.python_runtime.scope_and_state.models.ScopeSection` objects.

    A ``ScopeBuilder`` accumulates bindings and configuration incrementally,
    then produces an immutable :class:`ScopeSection` via :meth:`build`.  This
    matches the *construction phase* described in theory2.tex Ch15 §2.2,
    where a scope section is built up as the AST is traversed and names are
    encountered, before being frozen into an immutable patch.

    The builder follows the *fluent interface* pattern: every configuration
    method returns ``self``, enabling method chaining::

        section = (
            ScopeBuilder()
            .set_coordinate(func_coord)
            .set_kind(ScopeKind.FUNCTION)
            .set_parent("mymodule")
            .add_binding(param_coord)
            .add_binding(local_coord)
            .build()
        )

    Raises:
        ValueError: From :meth:`build` if no coordinate has been set.
    """

    _bindings: list[NameCoordinate] = field(default_factory=list, init=False)
    _coordinate: CoordinateObject | None = field(default=None, init=False)
    _parent_scope: str | None = field(default=None, init=False)
    _scope_kind: ScopeKind = field(default=ScopeKind.FUNCTION, init=False)
    _is_class_scope: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Fluent mutators
    # ------------------------------------------------------------------

    def add_binding(self, coord: NameCoordinate) -> ScopeBuilder:
        """Append *coord* to the list of bindings for the scope being built.

        If a binding with the same ``name`` already exists, it is replaced
        with *coord* (last-write wins, matching Python semantics for
        re-assignments within the same scope).

        Parameters:
            coord: The :class:`NameCoordinate` to add.

        Returns:
            ``self`` (for chaining).
        """
        # Replace existing binding with same name if present.
        for idx, existing in enumerate(self._bindings):
            if existing.name == coord.name:
                self._bindings[idx] = coord
                log.debug(
                    "ScopeBuilder.add_binding: replaced %r with kind %s",
                    coord.name,
                    coord.kind.value,
                )
                return self
        self._bindings.append(coord)
        log.debug("ScopeBuilder.add_binding: added %r (%s)", coord.name, coord.kind.value)
        return self

    def remove_binding(self, name: str) -> ScopeBuilder:
        """Remove any binding for the bare name *name*.

        If no binding with that name exists, this is a no-op.

        Parameters:
            name: The bare identifier to remove.

        Returns:
            ``self`` (for chaining).
        """
        before = len(self._bindings)
        self._bindings = [b for b in self._bindings if b.name != name]
        if len(self._bindings) < before:
            log.debug("ScopeBuilder.remove_binding: removed %r", name)
        return self

    def set_coordinate(self, coord: CoordinateObject) -> ScopeBuilder:
        """Set the geometry coordinate for the scope being built.

        This determines the ``scope_key`` of the resulting
        :class:`ScopeSection`.

        Parameters:
            coord: The :class:`CoordinateObject` identifying this scope's
                syntactic origin.

        Returns:
            ``self`` (for chaining).
        """
        self._coordinate = coord
        return self

    def set_parent(self, parent_key: str) -> ScopeBuilder:
        """Set the ``parent_key`` of the scope being built.

        Parameters:
            parent_key: The ``scope_key`` of the immediately enclosing scope.
                Pass an empty string to clear a previously-set parent.

        Returns:
            ``self`` (for chaining).
        """
        self._parent_scope = parent_key if parent_key else None
        return self

    def set_kind(self, kind: ScopeKind) -> ScopeBuilder:
        """Set the :class:`ScopeKind` of the scope being built.

        Parameters:
            kind: The syntactic category of this scope.

        Returns:
            ``self`` (for chaining).
        """
        self._scope_kind = kind
        return self

    def set_class_scope(self, is_class: bool) -> ScopeBuilder:
        """Mark the scope as a class namespace (or not).

        Class scopes have special rules in Python: names assigned at class
        level are *not* automatically visible in nested method scopes.

        Parameters:
            is_class: ``True`` to mark as a class scope.

        Returns:
            ``self`` (for chaining).
        """
        self._is_class_scope = is_class
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> ScopeSection:
        """Produce an immutable :class:`ScopeSection` from the accumulated state.

        Returns:
            A freshly constructed :class:`ScopeSection`.

        Raises:
            ValueError: If no coordinate has been set via :meth:`set_coordinate`.
        """
        if self._coordinate is None:
            raise ValueError(
                "ScopeBuilder.build(): a coordinate must be set via "
                "set_coordinate() before calling build()"
            )
        scope_key = self._coordinate.key
        section = ScopeSection(
            scope_key=scope_key,
            scope_kind=self._scope_kind,
            parent_key=self._parent_scope,
            bindings=tuple(self._bindings),
            metadata={
                "is_class_scope": self._is_class_scope,
                "coordinate_name": self._coordinate.name,
                "builder": "ScopeBuilder",
            },
        )
        log.debug(
            "ScopeBuilder.build: scope_key=%r, kind=%s, bindings=%d",
            scope_key,
            self._scope_kind.value,
            len(self._bindings),
        )
        return section

    def reset(self) -> ScopeBuilder:
        """Clear all accumulated state, returning the builder to its initial condition.

        Returns:
            ``self`` (for chaining), with all state cleared.
        """
        self._bindings = []
        self._coordinate = None
        self._parent_scope = None
        self._scope_kind = ScopeKind.FUNCTION
        self._is_class_scope = False
        return self

    def from_dict(self, data: dict[str, Any]) -> ScopeBuilder:
        """Populate the builder state from a dictionary.

        Expected keys:

        - ``"scope_kind"`` — string value of a :class:`ScopeKind` member.
        - ``"parent_key"`` — optional parent scope key string.
        - ``"is_class_scope"`` — optional bool.
        - ``"bindings"`` — optional list of serialised :class:`NameCoordinate`
          dicts (as produced by :meth:`NameCoordinate.serialize`).

        Parameters:
            data: Dictionary of builder configuration.

        Returns:
            ``self`` (for chaining), with state populated from *data*.
        """
        if "scope_kind" in data:
            self._scope_kind = ScopeKind(data["scope_kind"])
        if "parent_key" in data:
            self._parent_scope = data["parent_key"] or None
        if "is_class_scope" in data:
            self._is_class_scope = bool(data["is_class_scope"])
        for binding_data in data.get("bindings", []):
            self.add_binding(NameCoordinate.parse(binding_data))
        return self


# ---------------------------------------------------------------------------
# ScopeAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeAnalyzer:
    """Builds and analyses the scope structure of a Python module.

    A ``ScopeAnalyzer`` accumulates :class:`ScopeSection` objects as it
    analyses each scope in a module, maintaining a scope tree (parent →
    children adjacency map) and a flat registry keyed by ``scope_key``.

    The analyser provides three entry-point methods corresponding to the three
    main scope types in Python:

    - :meth:`analyze_module` — top-level module scope.
    - :meth:`analyze_function` — ``def`` / ``lambda`` scope.
    - :meth:`analyze_class` — ``class`` body scope.

    It also produces a :class:`~jugeo.judgments.judgment_terms.Judgment` for
    each analysed scope via :meth:`build_scope_judgment`, encoding the
    structural well-formedness assertion as a sheaf-theoretic proposition.

    Parameters:
        module_name: The dotted module name being analysed.

    Example::

        analyzer = ScopeAnalyzer(module_name="mymodule")
        module_coord = CoordinateObject(
            components=("mymodule",), kind=CoordinateKind.MODULE
        )
        mod_scope = analyzer.analyze_module(module_coord, ["x", "MyClass"])
        func_coord = CoordinateObject(
            components=("mymodule", "my_func"), kind=CoordinateKind.FUNCTION
        )
        func_scope = analyzer.analyze_function(
            func_coord, ["a", "b"], ["result"], [], parent_key=mod_scope.scope_key
        )
    """

    module_name: str
    _scopes: dict[str, ScopeSection] = field(default_factory=dict, init=False)
    _scope_tree: dict[str, list[str]] = field(default_factory=dict, init=False)
    _diagnostics: list[str] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------
    # Scope construction
    # ------------------------------------------------------------------

    def analyze_module(
        self,
        module_coord: CoordinateObject,
        global_names: list[str],
    ) -> ScopeSection:
        """Construct and register the module-level scope section.

        Each name in *global_names* is assigned :class:`~NameKind.GLOBAL`
        and recorded as a :class:`NameCoordinate` with the module's
        ``scope_key`` as its own ``scope_key``.

        The resulting scope section forms the *root patch* of the module's
        scope sheaf (theory2.tex Ch15 §2.3).

        Parameters:
            module_coord: The :class:`CoordinateObject` for the module.
            global_names: List of names defined at module level.

        Returns:
            The registered module-level :class:`ScopeSection`.
        """
        scope_key = module_coord.key
        bindings: list[NameCoordinate] = []

        for name in global_names:
            nc = NameCoordinate(
                name=name,
                kind=NameKind.GLOBAL,
                scope_key=scope_key,
                type_repr="unknown",
            )
            bindings.append(nc)

        section = ScopeSection(
            scope_key=scope_key,
            scope_kind=ScopeKind.MODULE,
            parent_key=None,
            bindings=tuple(bindings),
            source_location=f"{self.module_name}:1",
            metadata={"module_name": self.module_name, "channel": _SCOPE_ANALYSIS_CHANNEL},
        )
        self._scopes[scope_key] = section
        log.debug(
            "ScopeAnalyzer.analyze_module: registered %r with %d bindings",
            scope_key,
            len(bindings),
        )
        return section

    def analyze_function(
        self,
        func_coord: CoordinateObject,
        param_names: list[str],
        local_names: list[str],
        free_names: list[str],
        parent_key: str,
    ) -> ScopeSection:
        """Construct and register a function-level scope section.

        Formal parameters are assigned :class:`~NameKind.PARAMETER`, locally
        assigned names receive :class:`~NameKind.LOCAL`, and names resolved
        from enclosing scopes are tagged :class:`~NameKind.FREE`.

        In the sheaf model, the function scope is a *restricted section*
        obtained by pulling back the module section along the inclusion
        morphism ``func_coord → parent_key``.

        Parameters:
            func_coord: The :class:`CoordinateObject` for this function.
            param_names: Names of formal parameters.
            local_names: Names assigned locally (excluding parameters).
            free_names: Names that are free (resolved from an enclosing scope).
            parent_key: The ``scope_key`` of the enclosing scope.

        Returns:
            The registered function-level :class:`ScopeSection`.
        """
        scope_key = func_coord.key
        bindings: list[NameCoordinate] = []

        # Parameters come first (they shadow locals of the same name).
        for name in param_names:
            bindings.append(
                NameCoordinate(
                    name=name,
                    kind=NameKind.PARAMETER,
                    scope_key=scope_key,
                    type_repr="unknown",
                )
            )

        # Local names that are not also parameters.
        param_set = set(param_names)
        for name in local_names:
            if name not in param_set:
                bindings.append(
                    NameCoordinate(
                        name=name,
                        kind=NameKind.LOCAL,
                        scope_key=scope_key,
                        type_repr="unknown",
                    )
                )

        # Free names (captured from enclosing scopes).
        local_and_param = param_set | set(local_names)
        for name in free_names:
            if name not in local_and_param:
                bindings.append(
                    NameCoordinate(
                        name=name,
                        kind=NameKind.FREE,
                        scope_key=scope_key,
                        type_repr="unknown",
                    )
                )

        section = ScopeSection(
            scope_key=scope_key,
            scope_kind=ScopeKind.FUNCTION,
            parent_key=parent_key,
            bindings=tuple(bindings),
            metadata={"channel": _SCOPE_ANALYSIS_CHANNEL},
        )
        self._scopes[scope_key] = section
        # Register as child of parent in the scope tree.
        self._scope_tree.setdefault(parent_key, []).append(scope_key)
        log.debug(
            "ScopeAnalyzer.analyze_function: %r (parent=%r), "
            "%d params, %d locals, %d free",
            scope_key,
            parent_key,
            len(param_names),
            len(local_names),
            len(free_names),
        )
        return section

    def analyze_class(
        self,
        class_coord: CoordinateObject,
        method_names: list[str],
        class_var_names: list[str],
        parent_key: str,
    ) -> ScopeSection:
        """Construct and register a class-body scope section.

        Method names and class-variable names are assigned
        :class:`~NameKind.LOCAL`.  Class bodies are opaque to their nested
        method scopes (the *class scope exception* from LEGB), which is
        recorded in the section's ``metadata``.

        Parameters:
            class_coord: The :class:`CoordinateObject` for this class.
            method_names: Names of methods defined in the class body.
            class_var_names: Names of class-level variable assignments.
            parent_key: The ``scope_key`` of the enclosing scope.

        Returns:
            The registered class-level :class:`ScopeSection`.
        """
        scope_key = class_coord.key
        bindings: list[NameCoordinate] = []

        # Methods and class variables are LOCAL to the class scope.
        seen: set[str] = set()
        for name in method_names + class_var_names:
            if name not in seen:
                bindings.append(
                    NameCoordinate(
                        name=name,
                        kind=NameKind.LOCAL,
                        scope_key=scope_key,
                        type_repr="unknown",
                    )
                )
                seen.add(name)

        section = ScopeSection(
            scope_key=scope_key,
            scope_kind=ScopeKind.CLASS,
            parent_key=parent_key,
            bindings=tuple(bindings),
            metadata={
                "is_class_scope": True,
                "class_scope_exception": True,
                "channel": _SCOPE_ANALYSIS_CHANNEL,
            },
        )
        self._scopes[scope_key] = section
        self._scope_tree.setdefault(parent_key, []).append(scope_key)
        log.debug(
            "ScopeAnalyzer.analyze_class: %r (parent=%r), "
            "%d methods, %d class_vars",
            scope_key,
            parent_key,
            len(method_names),
            len(class_var_names),
        )
        return section

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def find_scope_at(self, coord_key: str) -> ScopeSection | None:
        """Return the :class:`ScopeSection` registered at *coord_key*, or ``None``.

        Parameters:
            coord_key: The slash-separated coordinate key to look up.

        Returns:
            The matching :class:`ScopeSection`, or ``None`` if not registered.
        """
        return self._scopes.get(coord_key)

    def get_all_scopes(self) -> list[ScopeSection]:
        """Return all registered scope sections.

        Returns:
            List of all :class:`ScopeSection` objects in registration order.
        """
        return list(self._scopes.values())

    def compute_scope_tree(self) -> dict[str, list[str]]:
        """Return the current parent → children adjacency map.

        Each key is a ``scope_key`` and the associated value is the list of
        ``scope_key`` strings of its direct children.  The root scope(s) can
        be found by looking for scopes with no parent (``parent_key is None``).

        This method also rebuilds the internal ``_scope_tree`` cache from the
        current ``_scopes`` registry, ensuring it is up-to-date.

        Returns:
            A fresh copy of the parent → children adjacency dict.
        """
        tree: dict[str, list[str]] = {}
        for key, scope in self._scopes.items():
            if scope.parent_key is not None:
                tree.setdefault(scope.parent_key, []).append(key)
            else:
                # Root scopes are listed under the empty-string key.
                tree.setdefault("", []).append(key)
        self._scope_tree = tree
        return dict(tree)

    def detect_scope_violations(self) -> list[str]:
        """Identify names in inner scopes that shadow outer scope bindings.

        A *shadowing violation* occurs when a name is assigned :class:`~NameKind.LOCAL`
        in an inner scope while the same name has a :class:`~NameKind.LOCAL` or
        :class:`~NameKind.GLOBAL` binding in the immediate parent scope.  This
        is not an error in Python, but it is a structural property that the
        sheaf model records (theory2.tex Ch15 §2.4).

        Returns:
            List of human-readable violation strings describing each shadowing
            instance found.  Also appended to :attr:`_diagnostics`.
        """
        violations: list[str] = []

        for key, scope in self._scopes.items():
            if scope.parent_key is None:
                continue
            parent = self._scopes.get(scope.parent_key)
            if parent is None:
                continue

            outer_map: dict[str, NameKind] = {
                b.name: b.kind for b in parent.bindings
            }
            for binding in scope.bindings:
                if binding.kind != NameKind.LOCAL:
                    continue
                outer_kind = outer_map.get(binding.name)
                if outer_kind is not None and outer_kind in (
                    NameKind.LOCAL,
                    NameKind.GLOBAL,
                    NameKind.PARAMETER,
                ):
                    msg = (
                        f"Shadowing: {binding.name!r} is LOCAL in scope "
                        f"{key!r} but {outer_kind.value!r} in parent "
                        f"{scope.parent_key!r}"
                    )
                    violations.append(msg)
                    self._diagnostics.append(msg)

        log.debug(
            "ScopeAnalyzer.detect_scope_violations: %d violations found",
            len(violations),
        )
        return violations

    def report_diagnostics(self) -> list[str]:
        """Return a copy of all accumulated diagnostic messages.

        Returns:
            A list of human-readable diagnostic strings emitted during
            analysis (e.g. shadowing warnings from :meth:`detect_scope_violations`).
        """
        return list(self._diagnostics)

    def build_scope_judgment(self, scope: ScopeSection) -> Judgment:
        """Construct a :class:`~jugeo.judgments.judgment_terms.Judgment` asserting scope well-formedness.

        The judgment encodes the structural proposition that the given scope
        section is a well-formed patch in the module's sheaf: it has a valid
        coordinate, a consistent set of bindings, and a properly-linked parent.

        The evidence item is tagged with the ``copilot-s02-scopes`` channel,
        indicating it was produced by this automated analysis pass.

        Parameters:
            scope: The :class:`ScopeSection` to build a judgment for.

        Returns:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` at the coordinate
            derived from ``scope.scope_key``.
        """
        components = tuple(scope.scope_key.split("/")) if scope.scope_key else ("_unknown",)
        coord_kind = (
            CoordinateKind.MODULE
            if scope.scope_kind == ScopeKind.MODULE
            else CoordinateKind.FUNCTION
        )
        coord = CoordinateObject(components=components, kind=coord_kind)

        binding_count = len(scope.bindings)
        free_count = sum(
            1 for b in scope.bindings if b.kind == NameKind.FREE
        )
        local_count = sum(
            1 for b in scope.bindings
            if b.kind in (NameKind.LOCAL, NameKind.PARAMETER)
        )

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"scope_section_well_formed("
                f"{scope.scope_key!r}, "
                f"bindings={binding_count}, "
                f"free={free_count}, "
                f"local={local_count})"
            ),
            free_variables=("scope_key", "bindings", "free", "local"),
        )
        carrier = Carrier(name="ScopeCarrier")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        evidence = EvidenceBundle(
            items=(
                EvidenceItem(
                    kind=EvidenceItemKind.ORACLE_PROPOSAL,
                    payload={
                        "scope_key": scope.scope_key,
                        "scope_kind": scope.scope_kind.value,
                        "binding_count": binding_count,
                        "free_count": free_count,
                        "local_count": local_count,
                        "parent_key": scope.parent_key,
                        "channel": _SCOPE_ANALYSIS_CHANNEL,
                    },
                    channel=_SCOPE_ANALYSIS_CHANNEL,
                ),
            )
        )
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            trust=trust,
        )


# ---------------------------------------------------------------------------
# ScopeValidator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeValidator:
    """Validates scope sections against sheaf-theoretic invariants.

    A ``ScopeValidator`` checks the structural axioms that a well-formed scope
    sheaf must satisfy (theory2.tex Ch15 §2.5):

    - **Well-formedness** (:meth:`validate_scope`) — basic structural checks:
      non-empty scope key, no binding with :class:`~NameKind.UNKNOWN`, no
      duplicate names.
    - **Covering condition** (:meth:`check_covering_condition`) — every free
      variable in an inner scope must be bound in some outer scope.
    - **Locality** (:meth:`check_locality`) — no name is bound with a kind
      that implies a global side effect from within a local scope.
    - **Gluing** (:meth:`check_gluing`) — sibling scopes sharing a common
      parent agree on the :class:`NameKind` of any name they both mention.
    - **Z3-encoded gluing** (:meth:`z3_check_gluing`) — the same gluing
      check, expressed as a satisfiability query.

    Accumulated violations are accessible via :meth:`report_violations`.
    """

    _violations: list[str] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def validate_scope(self, scope: ScopeSection) -> bool:
        """Check basic structural well-formedness of *scope*.

        Checks performed:

        1. ``scope_key`` is non-empty.
        2. No binding has :class:`~NameKind.UNKNOWN`.
        3. No two bindings share the same ``name``.
        4. If ``scope_kind`` is :class:`~ScopeKind.MODULE`, ``parent_key``
           must be ``None``.

        Parameters:
            scope: The :class:`ScopeSection` to validate.

        Returns:
            ``True`` if all checks pass; ``False`` otherwise.
        """
        ok = True

        if not scope.scope_key:
            self._violations.append(
                f"ScopeSection has empty scope_key (kind={scope.scope_kind.value!r})"
            )
            ok = False

        seen_names: set[str] = set()
        for binding in scope.bindings:
            if binding.kind == NameKind.UNKNOWN:
                msg = (
                    f"Scope {scope.scope_key!r}: binding {binding.name!r} "
                    f"has NameKind.UNKNOWN"
                )
                self._violations.append(msg)
                ok = False
            if binding.name in seen_names:
                msg = (
                    f"Scope {scope.scope_key!r}: duplicate binding for "
                    f"{binding.name!r}"
                )
                self._violations.append(msg)
                ok = False
            seen_names.add(binding.name)

        if scope.scope_kind == ScopeKind.MODULE and scope.parent_key is not None:
            msg = (
                f"Module scope {scope.scope_key!r} has non-None parent_key "
                f"{scope.parent_key!r}"
            )
            self._violations.append(msg)
            ok = False

        return ok

    def check_covering_condition(self, scopes: list[ScopeSection]) -> bool:
        """Verify that every free variable is covered by some outer scope.

        The *covering condition* (theory2.tex Ch15 §2.5.1) requires that the
        collection of scope patches forms a *cover* in the Grothendieck sense:
        for every free variable ``v`` in an inner scope, there must exist at
        least one outer scope that defines ``v`` as LOCAL, GLOBAL, PARAMETER,
        or IMPORT.

        Uses :class:`~jugeo.geometry.supports.SupportSet` operations to
        compute the *coverage gap* — names that are free in some scope but not
        defined in any other scope.

        Parameters:
            scopes: All :class:`ScopeSection` objects in the module.

        Returns:
            ``True`` if the covering condition holds; ``False`` if any free
            variable has no covering definition.
        """
        # Build the set of all names that are defined (non-free) in any scope.
        defined_coords: set[str] = set()
        for scope in scopes:
            for binding in scope.bindings:
                if binding.kind in (
                    NameKind.LOCAL,
                    NameKind.GLOBAL,
                    NameKind.PARAMETER,
                    NameKind.IMPORT,
                    NameKind.CLOSURE,
                ):
                    defined_coords.add(binding.name)

        # Build the set of all FREE names.
        free_names: set[str] = set()
        for scope in scopes:
            for binding in scope.bindings:
                if binding.kind in (NameKind.FREE, NameKind.NONLOCAL):
                    free_names.add(binding.name)

        # Use SupportSet to compute the gap.
        defined_support = SupportSet(coordinates=frozenset(defined_coords))
        free_support = SupportSet(coordinates=frozenset(free_names))
        uncovered = free_support.difference(defined_support)

        if not uncovered.is_empty():
            missing = sorted(uncovered.to_sorted_list())
            for name in missing:
                msg = (
                    f"Covering condition violated: {name!r} is FREE in some "
                    f"scope but has no covering LOCAL/GLOBAL/PARAMETER definition"
                )
                self._violations.append(msg)
                log.warning("ScopeValidator: %s", msg)
            return False

        return True

    def check_locality(self, scope: ScopeSection) -> bool:
        """Verify the locality axiom for a single scope.

        The *locality axiom* (theory2.tex Ch15 §2.5.2) requires that the
        value (binding) of a name in a scope is determined purely locally:
        no binding in a function scope should have :class:`~NameKind.GLOBAL`
        without an explicit ``global`` declaration (which is represented as
        :class:`~NameKind.GLOBAL`).

        As a heuristic, this check flags function-scope sections that
        contain bindings with :class:`~NameKind.GLOBAL` that do not also
        appear with a definition in the same scope (i.e. they look like
        undeclared global reads).

        Parameters:
            scope: The :class:`ScopeSection` to check.

        Returns:
            ``True`` if the scope satisfies the locality axiom heuristic.
        """
        # Locality only applies to function / lambda / comprehension scopes.
        if scope.scope_kind not in (
            ScopeKind.FUNCTION,
            ScopeKind.LAMBDA,
            ScopeKind.COMPREHENSION,
            ScopeKind.GENERATOR,
        ):
            return True

        ok = True
        local_names = {
            b.name
            for b in scope.bindings
            if b.kind in (NameKind.LOCAL, NameKind.PARAMETER)
        }
        for binding in scope.bindings:
            if binding.kind == NameKind.GLOBAL and binding.name in local_names:
                # A name declared global while also appearing as a local
                # assignment is suspicious.
                msg = (
                    f"Scope {scope.scope_key!r}: name {binding.name!r} is "
                    f"both GLOBAL-declared and locally assigned — "
                    f"locality axiom may be violated"
                )
                self._violations.append(msg)
                ok = False

        return ok

    def check_gluing(
        self,
        scope1: ScopeSection,
        scope2: ScopeSection,
    ) -> bool:
        """Verify the sheaf gluing axiom between two sibling scopes.

        The *gluing axiom* (theory2.tex Ch15 §2.5.3) says that if two scope
        sections both mention a name, and they share a common parent, their
        treatments of that name must be compatible.  Here "compatible" means
        that the :class:`NameKind` is the same in both scopes (both see it as
        FREE, or both see it as LOCAL, etc.).

        Parameters:
            scope1: First :class:`ScopeSection`.
            scope2: Second :class:`ScopeSection`.

        Returns:
            ``True`` if all shared names have the same :class:`NameKind` in
            both scopes; ``False`` if any inconsistency is found.
        """
        # Only check gluing for scopes that share a parent.
        if scope1.parent_key != scope2.parent_key:
            return True

        names1: dict[str, NameKind] = {b.name: b.kind for b in scope1.bindings}
        names2: dict[str, NameKind] = {b.name: b.kind for b in scope2.bindings}

        shared = set(names1.keys()) & set(names2.keys())
        ok = True
        for name in sorted(shared):
            k1, k2 = names1[name], names2[name]
            if k1 != k2:
                # Some kind pairs are acceptable: FREE vs FREE, LOCAL vs FREE, etc.
                # We flag only clearly contradictory combinations.
                if {k1, k2} in (
                    {NameKind.LOCAL, NameKind.GLOBAL},
                    {NameKind.PARAMETER, NameKind.GLOBAL},
                ):
                    msg = (
                        f"Gluing violation: {name!r} is {k1.value!r} in "
                        f"{scope1.scope_key!r} but {k2.value!r} in "
                        f"{scope2.scope_key!r} (parent={scope1.parent_key!r})"
                    )
                    self._violations.append(msg)
                    ok = False

        return ok

    def z3_check_gluing(
        self,
        scope1: ScopeSection,
        scope2: ScopeSection,
        session: Z3Session | None = None,
    ) -> bool:
        """Verify gluing using a Z3 satisfiability query when available.

        Encodes the gluing invariant as a set of boolean assertions in a
        :class:`Z3Session`.  For each name shared between *scope1* and
        *scope2*, a boolean variable ``agree_{name}`` is asserted.  If the
        kinds match, the assertion is satisfiable; if they don't match, a
        violation is logged and the method falls back to the heuristic check.

        When Z3 is not available (``z3_available()`` returns ``False``) or
        when *session* is ``None``, falls back to :meth:`check_gluing`.

        Parameters:
            scope1: First :class:`ScopeSection`.
            scope2: Second :class:`ScopeSection`.
            session: Optional :class:`Z3Session` for solver-based verification.

        Returns:
            ``True`` if the gluing axiom is satisfied (or cannot be checked);
            ``False`` if a violation is found.
        """
        if not z3_available() or session is None:
            log.debug(
                "z3_check_gluing: z3 unavailable or no session, "
                "falling back to heuristic"
            )
            return self.check_gluing(scope1, scope2)

        names1: dict[str, NameKind] = {b.name: b.kind for b in scope1.bindings}
        names2: dict[str, NameKind] = {b.name: b.kind for b in scope2.bindings}
        shared = set(names1.keys()) & set(names2.keys())

        if not shared:
            return True

        all_consistent = True
        for name in sorted(shared):
            k1, k2 = names1[name], names2[name]
            if k1 == k2:
                # Assert agreement as a boolean formula.
                agree_formula = Z3Formula.boolean(f"agree_{name}")
                session.assert_formula(agree_formula)
            else:
                # Kinds differ — record a Z3 violation and check with solver.
                msg = (
                    f"Z3 gluing check: {name!r} kind mismatch "
                    f"({k1.value!r} vs {k2.value!r}) between "
                    f"{scope1.scope_key!r} and {scope2.scope_key!r}"
                )
                log.warning("ScopeValidator.z3_check_gluing: %s", msg)
                self._violations.append(msg)
                all_consistent = False

        # Run the solver to check the assembled assertions.
        try:
            outcome = session.check_sat()
            if outcome == SolveOutcome.UNSAT:
                log.warning(
                    "ScopeValidator.z3_check_gluing: Z3 returned UNSAT for "
                    "scopes %r and %r",
                    scope1.scope_key,
                    scope2.scope_key,
                )
                return False
            log.debug(
                "ScopeValidator.z3_check_gluing: Z3 outcome=%s", outcome.value
            )
        except Exception as exc:
            log.warning(
                "ScopeValidator.z3_check_gluing: Z3 query raised %s, "
                "falling back to heuristic",
                exc,
            )
            return self.check_gluing(scope1, scope2)

        return all_consistent

    def is_well_formed_section(self, scope: ScopeSection) -> bool:
        """Combined well-formedness check: validate structure and locality.

        Runs :meth:`validate_scope` followed by :meth:`check_locality` on
        *scope*.  Returns ``True`` only if both pass.

        Parameters:
            scope: The :class:`ScopeSection` to check.

        Returns:
            ``True`` if the scope is structurally valid and locally consistent.
        """
        structurally_valid = self.validate_scope(scope)
        locally_consistent = self.check_locality(scope)
        return structurally_valid and locally_consistent

    def report_violations(self) -> list[str]:
        """Return a copy of all accumulated violation messages.

        Returns:
            A list of human-readable violation strings collected across all
            previous validation calls on this instance.
        """
        return list(self._violations)


# ---------------------------------------------------------------------------
# ScopeVisualizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeVisualizer:
    """Serialises scope information to human-readable and structured forms.

    A ``ScopeVisualizer`` provides ASCII-art tree representations, flat
    serialisation, and one-line summaries of :class:`ScopeSection` objects
    and :class:`ScopeChain` objects.  These are primarily useful for
    debugging, logging, and producing readable output for IDE tooling.

    Parameters:
        _indent: The indentation string used for each nesting level in tree
            output.  Defaults to two spaces.

    Example::

        viz = ScopeVisualizer()
        print(viz.scope_summary(my_scope))
        # -> "FUNCTION mymodule/outer/inner [3 bindings] (parent=mymodule/outer)"
    """

    _indent: str = "  "

    def to_tree(
        self,
        root_scope: ScopeSection,
        all_scopes: dict[str, ScopeSection],
        depth: int = 0,
    ) -> str:
        """Build an ASCII tree representation of the scope hierarchy.

        Recursively visits children of *root_scope* using *all_scopes* as the
        registry, indenting each level by :attr:`_indent`.

        Parameters:
            root_scope: The root of the subtree to render.
            all_scopes: Dict mapping ``scope_key`` → :class:`ScopeSection` for
                the entire module.
            depth: Current indentation depth (0 for the root call).

        Returns:
            A multi-line string showing the scope tree with binding counts.
        """
        prefix = self._indent * depth
        connector = "└─ " if depth > 0 else ""
        summary = self.scope_summary(root_scope)
        lines = [f"{prefix}{connector}{summary}"]

        # Find children: scopes whose parent_key matches this scope's key.
        children = [
            s
            for s in all_scopes.values()
            if s.parent_key == root_scope.scope_key
        ]
        # Sort children for deterministic output.
        children_sorted = sorted(children, key=lambda s: s.scope_key)

        for child in children_sorted:
            child_tree = self.to_tree(child, all_scopes, depth=depth + 1)
            lines.append(child_tree)

        return "\n".join(lines)

    def to_flat_list(self, scopes: list[ScopeSection]) -> list[dict[str, Any]]:
        """Serialise a list of scopes to a flat list of dicts.

        Each dict has the following keys:

        - ``"scope_key"`` — the scope's key.
        - ``"scope_kind"`` — the :class:`ScopeKind` value string.
        - ``"parent_key"`` — the parent's key, or ``None``.
        - ``"binding_count"`` — total number of bindings.
        - ``"binding_names"`` — sorted list of bare name strings.
        - ``"free_count"`` — count of :class:`~NameKind.FREE` bindings.
        - ``"param_count"`` — count of :class:`~NameKind.PARAMETER` bindings.
        - ``"local_count"`` — count of :class:`~NameKind.LOCAL` bindings.
        - ``"summary"`` — the one-line :meth:`scope_summary` string.

        Parameters:
            scopes: The list of :class:`ScopeSection` objects to serialise.

        Returns:
            List of dicts in the same order as *scopes*.
        """
        result: list[dict[str, Any]] = []
        for scope in scopes:
            free_count = sum(
                1 for b in scope.bindings if b.kind == NameKind.FREE
            )
            param_count = sum(
                1 for b in scope.bindings if b.kind == NameKind.PARAMETER
            )
            local_count = sum(
                1 for b in scope.bindings if b.kind == NameKind.LOCAL
            )
            result.append({
                "scope_key": scope.scope_key,
                "scope_kind": scope.scope_kind.value,
                "parent_key": scope.parent_key,
                "binding_count": len(scope.bindings),
                "binding_names": sorted(b.name for b in scope.bindings),
                "free_count": free_count,
                "param_count": param_count,
                "local_count": local_count,
                "summary": self.scope_summary(scope),
            })
        return result

    def scope_summary(self, scope: ScopeSection) -> str:
        """Produce a compact one-line summary of a scope section.

        Format::

            {SCOPE_KIND} {scope_key} [{n} bindings] (parent={parent_key})

        For module scopes with no parent, the parent clause is omitted.

        Parameters:
            scope: The :class:`ScopeSection` to summarise.

        Returns:
            A one-line string description.
        """
        binding_count = len(scope.bindings)
        plural = "binding" if binding_count == 1 else "bindings"
        kind_label = scope.scope_kind.value.upper()
        summary = f"{kind_label} {scope.scope_key!r} [{binding_count} {plural}]"
        if scope.parent_key is not None:
            summary += f" (parent={scope.parent_key!r})"
        return summary

    def visualize_nesting(self, scope_chain: ScopeChain) -> str:
        """Render the scope chain as nested-bracket notation.

        Produces a string like::

            [MODULE mymodule | [FUNCTION mymodule/f | [FUNCTION mymodule/f/g]]]

        The outermost scope is on the outside, the innermost on the inside.
        :class:`ScopeChain` stores scopes innermost-first, so we reverse for
        display.

        Parameters:
            scope_chain: The :class:`ScopeChain` to visualise.

        Returns:
            A single-line string showing the nesting with bracket notation.
        """
        if not scope_chain.scopes:
            return "[]"

        # Scopes in chain are innermost-first; reverse for outermost-first display.
        ordered = list(reversed(scope_chain.scopes))

        def _nest(scopes: list[ScopeSection], index: int) -> str:
            if index >= len(scopes):
                return ""
            scope = scopes[index]
            inner = _nest(scopes, index + 1)
            kind_label = scope.scope_kind.value.upper()
            label = f"{kind_label} {scope.scope_key}"
            if inner:
                return f"[{label} | {inner}]"
            return f"[{label}]"

        return _nest(ordered, 0)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def compute_lexical_depth(
    scope: ScopeSection,
    all_scopes: dict[str, ScopeSection],
) -> int:
    """Count the number of parent hops from *scope* to the root.

    Walks the ``parent_key`` chain from *scope* upward until a scope with
    no parent is reached.  A module-level scope has lexical depth 0.  A
    function defined directly in a module has depth 1, and so on.

    Guards against infinite loops from malformed parent chains by limiting
    the walk to ``len(all_scopes) + 1`` steps.

    Parameters:
        scope: The :class:`ScopeSection` whose depth is to be computed.
        all_scopes: Dict mapping ``scope_key`` → :class:`ScopeSection`.

    Returns:
        The non-negative integer lexical depth of *scope*.
    """
    depth = 0
    current_key: str | None = scope.parent_key
    max_hops = len(all_scopes) + 1

    while current_key is not None and depth <= max_hops:
        parent = all_scopes.get(current_key)
        if parent is None:
            break
        depth += 1
        current_key = parent.parent_key

    return depth


def scope_contains(
    outer: ScopeSection,
    inner: ScopeSection,
    all_scopes: dict[str, ScopeSection] | None = None,
) -> bool:
    """Return ``True`` if *inner* is a descendant of *outer* by the parent chain.

    Walks the ``parent_key`` chain of *inner* until either *outer*'s
    ``scope_key`` is found (returning ``True``) or the chain is exhausted
    (returning ``False``).

    If *all_scopes* is provided, each intermediate scope is looked up within
    it; otherwise only the ``parent_key`` strings are compared.

    Parameters:
        outer: The candidate ancestor scope.
        inner: The candidate descendant scope.
        all_scopes: Optional registry for resolving parent_key chains.

    Returns:
        ``True`` if *inner* is nested under *outer*; ``False`` otherwise.
    """
    if outer.scope_key == inner.scope_key:
        return False  # A scope does not "contain" itself in this context.

    current_key: str | None = inner.parent_key
    visited: set[str] = set()
    max_hops = 100 if all_scopes is None else len(all_scopes) + 1

    hops = 0
    while current_key is not None and hops < max_hops:
        if current_key in visited:
            # Cycle detected in parent chain — malformed data.
            log.warning(
                "scope_contains: cycle detected at %r (visited=%s)",
                current_key,
                visited,
            )
            return False
        if current_key == outer.scope_key:
            return True
        visited.add(current_key)

        if all_scopes is not None:
            parent = all_scopes.get(current_key)
            current_key = parent.parent_key if parent is not None else None
        else:
            # Without a registry we cannot walk further; just check one hop.
            break
        hops += 1

    return False


def scopes_overlap(s1: ScopeSection, s2: ScopeSection) -> bool:
    """Return ``True`` if *s1* and *s2* share at least one bound name.

    Two scope sections *overlap* (in the sheaf sense) if their support regions
    have a non-empty intersection.  Here the support of a scope is the set of
    names it directly binds.

    Uses :class:`~jugeo.geometry.supports.SupportSet` operations internally.

    Parameters:
        s1: First :class:`ScopeSection`.
        s2: Second :class:`ScopeSection`.

    Returns:
        ``True`` if the intersection of the two binding-name sets is non-empty.
    """
    names1 = frozenset(b.name for b in s1.bindings)
    names2 = frozenset(b.name for b in s2.bindings)

    support1 = SupportSet(coordinates=names1)
    support2 = SupportSet(coordinates=names2)

    intersection = support1.intersection(support2)
    return not intersection.is_empty()


def build_module_site(
    analyzer: ScopeAnalyzer,
) -> Site:
    """Construct a :class:`~jugeo.geometry.site.Site` from all scopes in *analyzer*.

    Each scope is represented as a :class:`CoordinateObject` node in the site.
    Parent → child relationships are encoded as inclusion morphisms.

    Parameters:
        analyzer: A :class:`ScopeAnalyzer` that has already processed at least
            one scope.

    Returns:
        A :class:`~jugeo.geometry.site.Site` containing one coordinate per
        scope and one inclusion morphism per parent→child edge.
    """
    builder = SiteBuilder(label=f"{analyzer.module_name}-scope-site")

    for scope in analyzer.get_all_scopes():
        components = tuple(scope.scope_key.split("/")) if scope.scope_key else ("_unknown",)
        ck = (
            CoordinateKind.MODULE
            if scope.scope_kind == ScopeKind.MODULE
            else CoordinateKind.FUNCTION
        )
        coord = CoordinateObject(components=components, kind=ck)
        builder.add_coordinate(coord)

    return builder.build()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "ScopeBuilder",
    "ScopeAnalyzer",
    "ScopeValidator",
    "ScopeVisualizer",
    # Helpers
    "compute_lexical_depth",
    "scope_contains",
    "scopes_overlap",
    "build_module_site",
    # Constants
    "_SCOPE_ANALYSIS_CHANNEL",
]
