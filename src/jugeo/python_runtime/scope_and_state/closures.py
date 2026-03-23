"""Section 3 — Closures as Restricted Sections (theory2.tex Ch15 §3).

In Ch15, a **closure** is modelled as a *restricted section*: it takes the
outer scope's section (which assigns values to names) and restricts it to only
the names the inner function actually uses — the *free variables*.  The
restriction is a pullback along the inclusion morphism

    i : inner_scope ↪ outer_scope

so the closure section σ|_inner = i* σ_outer contains exactly the bindings
that the inner function references.  This gives a clean categorical account of
why closures capture *exactly* the names they reference, no more and no less.

When two or more inner functions capture the same outer name, they share a
*cell* — a single mutable reference that all copies of the closed-over
function read and write through.  The :class:`CellVariableTracker` models
these shared cells, and :class:`ClosureLifter` exposes the explicit section
representation so that downstream analyses can reason about captured state
without inspecting CPython bytecode.

All copilot-generated; see theory2.tex Ch15 §3 for the formal development.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.site import (
    CoordinateObject,
    CoordinateKind,
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
from jugeo.solver.z3_session import (
    SolveOutcome,
    Z3Formula,
    Z3Session,
    z3_available,
)

from jugeo.python_runtime.scope_and_state.models import (
    BindingMap,
    ClosureRecord,
    ModuleStateManifest,
    NameCoordinate,
    NameKind,
    NameResolutionResult,
    ScopeChain,
    ScopeKind,
    ScopeSection,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def free_vars_of(scope: ScopeSection) -> list[NameCoordinate]:
    """Return all bindings in *scope* whose kind is FREE or CLOSURE.

    Parameters:
        scope: The scope section to inspect.

    Returns:
        List of :class:`NameCoordinate` objects with ``kind`` in
        ``{NameKind.FREE, NameKind.CLOSURE}``.  Order follows the order of
        ``scope.bindings``.
    """
    return [
        b
        for b in scope.bindings
        if b.kind in (NameKind.FREE, NameKind.CLOSURE)
    ]


def bound_vars_of(scope: ScopeSection) -> list[NameCoordinate]:
    """Return all bindings in *scope* whose kind is LOCAL or PARAMETER.

    Parameters:
        scope: The scope section to inspect.

    Returns:
        List of :class:`NameCoordinate` objects with ``kind`` in
        ``{NameKind.LOCAL, NameKind.PARAMETER}``.
    """
    return [
        b
        for b in scope.bindings
        if b.kind in (NameKind.LOCAL, NameKind.PARAMETER)
    ]


def closure_cells_of(record: ClosureRecord) -> list[str]:
    """Return the plain name strings of all free variables in *record*.

    Convenience wrapper that returns ``list(record.all_free_names)``.

    Parameters:
        record: The closure record to query.

    Returns:
        List of bare identifier strings (possibly empty).
    """
    return list(record.all_free_names)


# ---------------------------------------------------------------------------
# ClosureDetector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureDetector:
    """Detects which functions form closures across a collection of scopes.

    Iterates over a flat list of :class:`ScopeSection` objects and identifies
    any whose ``scope_kind`` is FUNCTION or LAMBDA and that reference names
    not locally defined — i.e. names that must be resolved from an enclosing
    scope.

    In the sheaf model (theory2.tex Ch15 §3) these are precisely the functions
    for which the pullback ``i* σ_outer`` is non-trivial.

    Parameters:
        module_name: Dotted name of the module being analysed.

    Example::

        detector = ClosureDetector(module_name="mypackage.foo")
        records = detector.detect_closures(all_scopes)
    """

    module_name: str
    _closure_coords: dict[str, ClosureRecord] = field(default_factory=dict)
    _detection_log: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def detect_closures(
        self, scopes: list[ScopeSection]
    ) -> list[ClosureRecord]:
        """Scan all function scopes for free variables and build records.

        A scope forms a closure iff it is a FUNCTION or LAMBDA scope and
        contains at least one binding whose :class:`NameKind` is FREE or
        CLOSURE.

        Parameters:
            scopes: Flat list of all scopes in the module, in any order.

        Returns:
            List of :class:`ClosureRecord` objects, one per detected closure.
            The list is ordered by ``scope_key`` for determinism.
        """
        scope_map: dict[str, ScopeSection] = {s.scope_key: s for s in scopes}
        records: list[ClosureRecord] = []

        for scope in sorted(scopes, key=lambda s: s.scope_key):
            if not self.is_closure(scope):
                continue

            # Collect all enclosing scopes by walking parent_key chain
            enclosing: list[str] = []
            cursor: str | None = scope.parent_key
            while cursor and cursor in scope_map:
                enclosing.append(cursor)
                cursor = scope_map[cursor].parent_key

            # Find free variables that actually live in an outer scope
            outer_list = [
                scope_map[k] for k in enclosing if k in scope_map
            ]
            free_vars = self.find_free_variables(scope, outer_list)

            depth = self.compute_closure_depth(
                scope.scope_key,
                {
                    k: [
                        s.scope_key
                        for s in scopes
                        if s.parent_key == k
                    ]
                    for k in scope_map
                },
            )

            record = self.build_closure_record(
                scope, free_vars, enclosing
            )
            self._closure_coords[scope.scope_key] = record
            records.append(record)
            self._detection_log.append(
                f"Detected closure: {scope.scope_key} "
                f"(free_vars={[v.name for v in free_vars]})"
            )
            logger.debug(
                "Closure detected in %s: %d free variable(s)",
                scope.scope_key,
                len(free_vars),
            )

        return records

    def is_closure(self, scope: ScopeSection) -> bool:
        """Return ``True`` if *scope* qualifies as a closure.

        A scope qualifies when all three conditions hold:

        1. Its ``scope_kind`` is FUNCTION or LAMBDA.
        2. It has a non-``None`` ``parent_key`` (i.e. it is nested).
        3. It contains at least one FREE or CLOSURE binding.

        Parameters:
            scope: The scope to test.

        Returns:
            Boolean indicating whether the scope forms a closure.
        """
        if scope.scope_kind not in (ScopeKind.FUNCTION, ScopeKind.LAMBDA):
            return False
        if scope.parent_key is None:
            return False
        return len(free_vars_of(scope)) > 0

    def find_free_variables(
        self,
        inner_scope: ScopeSection,
        outer_scopes: list[ScopeSection],
    ) -> list[NameCoordinate]:
        """Identify names used in *inner_scope* but defined in *outer_scopes*.

        A name is "free" in *inner_scope* if:

        - Its :class:`NameKind` is FREE or CLOSURE (as recorded in
          ``inner_scope.bindings``), **and**
        - It appears (as a LOCAL or PARAMETER binding) in at least one of
          the provided *outer_scopes*.

        If a free variable is listed in ``inner_scope`` but not found in any
        outer scope, it is still included (it may be a global or builtin).

        Parameters:
            inner_scope: The scope whose free names we want.
            outer_scopes: Ordered list of enclosing scopes, innermost first.

        Returns:
            List of :class:`NameCoordinate` objects from *inner_scope* that
            are free.  Each item's ``scope_key`` reflects where it was
            *referenced* (i.e. the inner scope).
        """
        free: list[NameCoordinate] = []
        outer_bound: set[str] = set()
        for outer in outer_scopes:
            for b in outer.bindings:
                if b.kind in (NameKind.LOCAL, NameKind.PARAMETER, NameKind.CLOSURE):
                    outer_bound.add(b.name)

        for binding in inner_scope.bindings:
            if binding.kind in (NameKind.FREE, NameKind.CLOSURE):
                free.append(binding)
            elif binding.name in outer_bound and binding.kind not in (
                NameKind.LOCAL,
                NameKind.PARAMETER,
                NameKind.GLOBAL,
                NameKind.BUILTIN,
            ):
                # Promote to free implicitly
                promoted = NameCoordinate(
                    name=binding.name,
                    kind=NameKind.FREE,
                    scope_key=binding.scope_key,
                    type_repr=binding.type_repr,
                    metadata=binding.metadata,
                )
                free.append(promoted)
        return free

    def find_nonlocal_names(self, scope: ScopeSection) -> list[str]:
        """Return names with :attr:`NameKind.FREE` or :attr:`NameKind.CLOSURE`.

        Parameters:
            scope: The scope to inspect.

        Returns:
            Alphabetically sorted list of bare identifier strings.
        """
        return sorted(
            {
                b.name
                for b in scope.bindings
                if b.kind in (NameKind.FREE, NameKind.CLOSURE)
            }
        )

    def detect_nested_functions(
        self, scopes: list[ScopeSection]
    ) -> list[tuple[str, str]]:
        """Return ``(inner_key, outer_key)`` pairs for all nested functions.

        A function is "nested" if its ``scope_kind`` is FUNCTION or LAMBDA
        and its ``parent_key`` points to another FUNCTION or LAMBDA scope.

        Parameters:
            scopes: All scopes in the module.

        Returns:
            List of ``(inner_scope_key, outer_scope_key)`` tuples, sorted
            lexicographically by inner key.

        Example::

            pairs = detector.detect_nested_functions(scopes)
            # [("outer/inner", "outer"), ...]
        """
        scope_map: dict[str, ScopeSection] = {s.scope_key: s for s in scopes}
        pairs: list[tuple[str, str]] = []
        for scope in scopes:
            if scope.scope_kind not in (ScopeKind.FUNCTION, ScopeKind.LAMBDA):
                continue
            if not scope.parent_key:
                continue
            parent = scope_map.get(scope.parent_key)
            if parent and parent.scope_kind in (
                ScopeKind.FUNCTION,
                ScopeKind.LAMBDA,
            ):
                pairs.append((scope.scope_key, scope.parent_key))
        return sorted(pairs)

    def compute_closure_depth(
        self,
        scope_key: str,
        scope_tree: dict[str, list[str]],
    ) -> int:
        """Count the nesting depth of *scope_key* in *scope_tree*.

        The depth is defined as the number of ancestors of *scope_key* in the
        tree rooted at ``scope_tree``.  Module-level scopes have depth 0;
        direct children of the module have depth 1, and so on.

        Parameters:
            scope_key: The key whose depth to compute.
            scope_tree: Mapping from parent key to list of direct child keys.

        Returns:
            Non-negative integer depth.
        """
        # Build reverse map: child -> parent
        child_to_parent: dict[str, str] = {}
        for parent, children in scope_tree.items():
            for child in children:
                child_to_parent[child] = parent

        depth = 0
        current = scope_key
        visited: set[str] = set()
        while current in child_to_parent:
            if current in visited:
                # Cycle guard
                break
            visited.add(current)
            current = child_to_parent[current]
            depth += 1
        return depth

    def build_closure_record(
        self,
        scope: ScopeSection,
        free_vars: list[NameCoordinate],
        enclosing_keys: list[str],
    ) -> ClosureRecord:
        """Construct a :class:`ClosureRecord` from detection data.

        Parameters:
            scope: The inner (closing) function scope.
            free_vars: The list of free :class:`NameCoordinate` objects.
            enclosing_keys: Ordered list of enclosing scope keys.

        Returns:
            A new :class:`ClosureRecord`.
        """
        all_free_names = tuple(sorted({v.name for v in free_vars}))
        depth = len(enclosing_keys)
        return ClosureRecord(
            function_key=scope.scope_key,
            enclosing_keys=tuple(enclosing_keys),
            free_variables=tuple(free_vars),
            all_free_names=all_free_names,
            depth=depth,
            metadata={
                "scope_kind": scope.scope_kind.value,
                "source_location": scope.source_location,
            },
        )


# ---------------------------------------------------------------------------
# ClosureLifter  (KEY CLASS)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureLifter:
    """Lifts closures to explicit section representations.

    The *lifting* operation (theory2.tex Ch15 §3) makes the pullback
    ``i* σ_outer`` concrete: given a :class:`ClosureRecord` and the outer
    scope section, it produces a new :class:`ScopeSection` that contains only
    the bindings the inner function actually uses.  This *restricted section*
    is the closure's captured environment.

    The class also maintains a *cell graph* — a mapping from captured name to
    the set of function keys that share that cell — which is useful for
    detecting shared mutable state.

    Parameters:
        module_name: Dotted module name owning this lifter.

    Example::

        lifter = ClosureLifter(module_name="mypackage.foo")
        lifted_scope = lifter.lift_closure(record, outer_scope)
    """

    module_name: str
    _lifted: dict[str, ScopeSection] = field(default_factory=dict)
    _cell_graph: dict[str, set[str]] = field(default_factory=dict)
    _judgments: list[Judgment] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Core lifting
    # ------------------------------------------------------------------

    def lift_closure(
        self,
        record: ClosureRecord,
        outer_scope: ScopeSection,
    ) -> ScopeSection:
        """Create a new :class:`ScopeSection` for the closure's captured env.

        The result contains exactly the bindings from *outer_scope* whose
        names appear in ``record.all_free_names``.  Names not found in
        *outer_scope* are fabricated as FREE-kind stubs.

        Parameters:
            record: The closure record describing what is captured.
            outer_scope: The enclosing scope from which names are pulled.

        Returns:
            A new ``ScopeSection`` whose ``scope_key`` is the function key
            suffixed with ``".__closure__"``, containing only the restricted
            bindings.
        """
        outer_by_name: dict[str, NameCoordinate] = {
            b.name: b for b in outer_scope.bindings
        }

        restricted_bindings: list[NameCoordinate] = []
        for free_name in sorted(record.all_free_names):
            if free_name in outer_by_name:
                original = outer_by_name[free_name]
                # Re-stamp with CLOSURE kind and inner scope_key
                captured = NameCoordinate(
                    name=original.name,
                    kind=NameKind.CLOSURE,
                    scope_key=record.function_key,
                    type_repr=original.type_repr,
                    metadata={
                        **dict(original.metadata),
                        "captured_from": outer_scope.scope_key,
                    },
                )
            else:
                # Name not found in the direct outer scope — create a stub
                captured = NameCoordinate(
                    name=free_name,
                    kind=NameKind.FREE,
                    scope_key=record.function_key,
                    type_repr="unknown",
                    metadata={
                        "captured_from": outer_scope.scope_key,
                        "stub": True,
                    },
                )
            restricted_bindings.append(captured)

        lifted_key = f"{record.function_key}.__closure__"
        lifted = ScopeSection(
            scope_key=lifted_key,
            scope_kind=ScopeKind.FUNCTION,
            parent_key=outer_scope.scope_key,
            bindings=tuple(restricted_bindings),
            source_location=outer_scope.source_location,
            metadata={
                "is_lifted_closure": True,
                "origin_function": record.function_key,
                "free_names": list(record.all_free_names),
            },
        )
        self._lifted[record.function_key] = lifted
        logger.debug(
            "Lifted closure for %s: %d binding(s) captured",
            record.function_key,
            len(restricted_bindings),
        )
        return lifted

    def lift_all_closures(
        self,
        records: list[ClosureRecord],
        all_scopes: dict[str, ScopeSection],
    ) -> dict[str, ScopeSection]:
        """Lift every closure record to an explicit section representation.

        For each record in *records*, finds the best matching outer scope
        (first enclosing key that is present in *all_scopes*) and delegates to
        :meth:`lift_closure`.

        Parameters:
            records: List of :class:`ClosureRecord` objects to lift.
            all_scopes: Mapping from ``scope_key`` to :class:`ScopeSection`
                covering all scopes in the module.

        Returns:
            Dict mapping ``record.function_key`` to the lifted
            :class:`ScopeSection`.  Records whose outer scope cannot be found
            are silently skipped with a warning.
        """
        result: dict[str, ScopeSection] = {}
        for record in records:
            outer: ScopeSection | None = None
            for ek in record.enclosing_keys:
                if ek in all_scopes:
                    outer = all_scopes[ek]
                    break
            if outer is None:
                logger.warning(
                    "Cannot lift closure %s: no enclosing scope found "
                    "among %s",
                    record.function_key,
                    record.enclosing_keys,
                )
                continue
            lifted = self.lift_closure(record, outer)
            result[record.function_key] = lifted

        # Update cell graph for all lifted records
        self._cell_graph = self.build_cell_graph(records)
        return result

    # ------------------------------------------------------------------
    # Cell-variable analysis
    # ------------------------------------------------------------------

    def compute_captured_names(self, record: ClosureRecord) -> list[str]:
        """Return the alphabetically-sorted list of captured name strings.

        Parameters:
            record: The closure record to query.

        Returns:
            Sorted list of captured variable names.
        """
        return sorted(record.all_free_names)

    def build_cell_graph(
        self, records: list[ClosureRecord]
    ) -> dict[str, set[str]]:
        """Build a mapping from captured name to functions that share it.

        In CPython, when two closures capture the same outer variable they
        share a *cell* object.  This graph exposes that sharing at the model
        level.

        Parameters:
            records: All closure records in the module.

        Returns:
            Dict mapping bare name strings to sets of ``function_key`` values
            that have that name as a free variable.
        """
        graph: dict[str, set[str]] = {}
        for record in records:
            for name in record.all_free_names:
                graph.setdefault(name, set()).add(record.function_key)
        return graph

    def find_shared_cells(self, records: list[ClosureRecord]) -> list[str]:
        """Return names captured as free variables by two or more closures.

        Parameters:
            records: All closure records in the module.

        Returns:
            Sorted list of cell names that are shared across multiple
            closures.

        Example::

            shared = lifter.find_shared_cells(records)
            # ["counter", "state"]
        """
        graph = self.build_cell_graph(records)
        return sorted(name for name, fns in graph.items() if len(fns) >= 2)

    def detect_mutable_cells(
        self, record: ClosureRecord
    ) -> list[NameCoordinate]:
        """Return free variables that may be mutated inside the closure.

        Uses a heuristic: a free variable is considered potentially mutable
        if its ``metadata`` dict contains a key ``"assigned"`` with a truthy
        value, or if its ``kind`` is exactly :attr:`NameKind.CLOSURE`
        (CPython only creates cell objects for variables that are *assigned*
        inside a nested scope).

        Parameters:
            record: The closure record to inspect.

        Returns:
            List of :class:`NameCoordinate` objects that may be mutated.
        """
        mutable: list[NameCoordinate] = []
        for fv in record.free_variables:
            is_assigned = bool(fv.metadata.get("assigned", False))
            is_cell = fv.kind == NameKind.CLOSURE
            if is_assigned or is_cell:
                mutable.append(fv)
        return mutable

    def build_restriction_morphism(
        self, inner_key: str, outer_key: str
    ) -> dict[str, Any]:
        """Build a dictionary representing the restriction morphism.

        The restriction morphism ``i* : outer_scope → inner_closure`` is the
        categorical pullback that extracts only the free-variable bindings.
        We represent it as a plain dict so it can be passed to downstream
        tools without requiring a full Site object.

        Parameters:
            inner_key: ``scope_key`` of the inner (closing) function.
            outer_key: ``scope_key`` of the outer (enclosing) scope.

        Returns:
            Dict with keys ``"source"``, ``"target"``, ``"kind"``,
            ``"captured_names"``, and ``"morphism_kind"``.
        """
        inner_lifted = self._lifted.get(inner_key)
        captured = (
            [b.name for b in inner_lifted.bindings]
            if inner_lifted
            else []
        )
        return {
            "source": outer_key,
            "target": inner_key,
            "kind": MorphismKind.RESTRICTION.value,
            "captured_names": captured,
            "morphism_kind": "pullback_along_inclusion",
            "label": f"restrict({outer_key} → {inner_key})",
        }

    def annotate_with_judgment(self, record: ClosureRecord) -> Judgment:
        """Build a :class:`Judgment` asserting the closure is well-formed.

        The judgment has :attr:`PropositionKind.STRUCTURAL` and states that
        the free variables of the inner function are exactly those captured
        from the enclosing scope.  Trust level is
        :attr:`TrustLevel.UNVERIFIED` since no solver discharge has been
        attempted.

        Parameters:
            record: The closure record to annotate.

        Returns:
            A :class:`Judgment` whose ``status`` is ``PROPOSED``.
        """
        coord = CoordinateObject(
            components=tuple(record.function_key.split("/")),
            kind=CoordinateKind.FUNCTION,
        )
        free_names_str = ", ".join(sorted(record.all_free_names))
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"closure_well_formed({record.function_key}) ∧ "
                f"free_vars = {{{free_names_str}}}"
            ),
            free_variables=record.all_free_names,
        )
        carrier = Carrier(name="ClosureSection")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        j = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )
        self._judgments.append(j)
        return j


# ---------------------------------------------------------------------------
# CellVariableTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CellVariableTracker:
    """Tracks CPython *cell* variables across closure boundaries.

    In CPython, a variable that is both assigned in an inner function and read
    in that inner function (or shared between siblings) is stored as a
    ``cell`` object.  This class maintains a registry of such cells and
    records mutation events so that downstream analyses can detect aliasing or
    unexpected re-binding.

    Parameters:
        (no constructor arguments; all fields default to empty)

    Example::

        tracker = CellVariableTracker()
        tracker.track_cell(name_coord, "outer/inner")
        tracker.record_mutation("counter", "outer/inner", time.time())
    """

    _cells: dict[str, NameCoordinate] = field(default_factory=dict)
    _function_cells: dict[str, list[str]] = field(default_factory=dict)
    _mutation_log: list[dict[str, Any]] = field(default_factory=list)

    def track_cell(self, name: NameCoordinate, function_key: str) -> None:
        """Register *name* as a cell variable for *function_key*.

        If the same bare name is already tracked from a different scope this
        entry is overwritten with a warning — callers should use
        :meth:`is_shared_cell` first if they need to avoid clobbers.

        Parameters:
            name: The :class:`NameCoordinate` of the cell variable.
            function_key: The scope key of the function that owns the cell.

        Returns:
            None.
        """
        if name.name in self._cells and self._cells[name.name] != name:
            logger.warning(
                "Cell '%s' already tracked from %s; overwriting with %s",
                name.name,
                self._cells[name.name].scope_key,
                name.scope_key,
            )
        self._cells[name.name] = name
        self._function_cells.setdefault(function_key, [])
        if name.name not in self._function_cells[function_key]:
            self._function_cells[function_key].append(name.name)

    def untrack_cell(self, name: str, function_key: str) -> bool:
        """Remove the cell named *name* from tracking for *function_key*.

        Parameters:
            name: Bare identifier string.
            function_key: The function whose reference to this cell should be
                removed.

        Returns:
            ``True`` if the cell was found and removed, ``False`` otherwise.
        """
        if function_key in self._function_cells:
            cell_list = self._function_cells[function_key]
            if name in cell_list:
                cell_list.remove(name)
                # Remove from global cells dict only if no other function refs it
                still_used = any(
                    name in cells
                    for fk, cells in self._function_cells.items()
                    if fk != function_key
                )
                if not still_used and name in self._cells:
                    del self._cells[name]
                return True
        return False

    def get_cell(self, name: str) -> NameCoordinate | None:
        """Look up the :class:`NameCoordinate` for cell *name*.

        Parameters:
            name: Bare identifier string.

        Returns:
            The :class:`NameCoordinate`, or ``None`` if not tracked.
        """
        return self._cells.get(name)

    def all_cells(self) -> list[NameCoordinate]:
        """Return all currently tracked cell variables.

        Returns:
            List of :class:`NameCoordinate` objects, sorted by name.
        """
        return sorted(self._cells.values(), key=lambda c: c.name)

    def cells_for_function(self, function_key: str) -> list[NameCoordinate]:
        """Return all cells associated with *function_key*.

        Parameters:
            function_key: Scope key of the function to query.

        Returns:
            List of :class:`NameCoordinate` objects.  Empty list if the
            function key is not registered.
        """
        names = self._function_cells.get(function_key, [])
        return [self._cells[n] for n in names if n in self._cells]

    def is_shared_cell(self, name: str) -> bool:
        """Return ``True`` if *name* appears in two or more function cell lists.

        Parameters:
            name: Bare identifier string.

        Returns:
            Boolean.
        """
        count = sum(
            1
            for cells in self._function_cells.values()
            if name in cells
        )
        return count >= 2

    def mutation_events(self) -> list[dict[str, Any]]:
        """Return a copy of the mutation event log.

        Returns:
            List of dicts, each with keys ``"name"``, ``"function_key"``,
            and ``"timestamp"``.  Ordered chronologically.
        """
        return list(self._mutation_log)

    def record_mutation(
        self, name: str, function_key: str, timestamp: float
    ) -> None:
        """Append a mutation event to the log.

        Parameters:
            name: The bare variable name that was mutated.
            function_key: The function scope in which the mutation occurred.
            timestamp: POSIX timestamp (e.g. from ``time.time()``).

        Returns:
            None.
        """
        event: dict[str, Any] = {
            "name": name,
            "function_key": function_key,
            "timestamp": timestamp,
        }
        self._mutation_log.append(event)
        logger.debug(
            "Cell mutation recorded: %s in %s at %.6f",
            name,
            function_key,
            timestamp,
        )

    def serialize(self) -> dict[str, Any]:
        """Serialise the tracker's state to a plain dictionary.

        Returns:
            JSON-compatible dict with keys ``"cells"``,
            ``"function_cells"``, and ``"mutation_log"``.
        """
        return {
            "cells": {
                name: coord.serialize()
                for name, coord in self._cells.items()
            },
            "function_cells": dict(self._function_cells),
            "mutation_log": list(self._mutation_log),
        }


# ---------------------------------------------------------------------------
# ClosureJudgmentBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureJudgmentBuilder:
    """Constructs :class:`Judgment` objects for closure-related properties.

    Each method targets a distinct property of a closure: correct variable
    capture, safe handling of mutable cells, absence of re-entrancy hazards,
    and lifetime safety of captured variables.

    Parameters:
        module_coordinate: The :class:`CoordinateObject` for the owning module.

    Example::

        builder = ClosureJudgmentBuilder(
            module_coordinate=CoordinateObject(
                components=("mypackage", "foo"),
                kind=CoordinateKind.MODULE,
            )
        )
        j = builder.build_capture_judgment(record)
    """

    module_coordinate: CoordinateObject

    # ------------------------------------------------------------------
    # Judgment factories
    # ------------------------------------------------------------------

    def _make_coord(self, record: ClosureRecord) -> CoordinateObject:
        """Build a :class:`CoordinateObject` for *record*'s function."""
        parts = tuple(record.function_key.split("/"))
        return CoordinateObject(components=parts, kind=CoordinateKind.FUNCTION)

    def build_capture_judgment(self, record: ClosureRecord) -> Judgment:
        """Build a judgment that the closure correctly captures its free vars.

        The proposition asserts ``captured_set = free_vars(inner_function)``
        which is a structural invariant that can be verified statically.
        Trust level is :attr:`TrustLevel.UNVERIFIED` pending static analysis.

        Parameters:
            record: The closure record to annotate.

        Returns:
            A :class:`Judgment` with ``status=PROPOSED``.
        """
        coord = self._make_coord(record)
        names = ", ".join(sorted(record.all_free_names)) or "(none)"
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"∀ name ∈ {{{names}}} . "
                f"name ∈ free_vars({record.function_key}) "
                f"↔ name ∈ captured_env({record.function_key})"
            ),
            free_variables=record.all_free_names,
        )
        carrier = Carrier(name="CaptureCarrier")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )

    def build_cell_mutation_judgment(
        self,
        record: ClosureRecord,
        mutable_cells: list[NameCoordinate],
    ) -> Judgment:
        """Build a judgment about mutable cell variables.

        When cell variables are mutated, the mutation is visible to *all*
        functions sharing the cell.  The proposition flags this as a
        potentially surprising behavioral property.

        Parameters:
            record: The closure record whose cells are being annotated.
            mutable_cells: The subset of free variables identified as mutable.

        Returns:
            A :class:`Judgment` with ``status=PROPOSED`` and trust
            :attr:`TrustLevel.UNVERIFIED`.
        """
        coord = self._make_coord(record)
        cell_names = ", ".join(c.name for c in mutable_cells) or "(none)"
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=(
                f"mutable_cells({record.function_key}) = {{{cell_names}}} ∧ "
                f"∀ cell ∈ {{{cell_names}}} . "
                f"mutation(cell) is visible to all sharing closures"
            ),
            free_variables=tuple(c.name for c in mutable_cells),
        )
        carrier = Carrier(name="CellMutationCarrier")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )

    def build_reentrancy_judgment(self, record: ClosureRecord) -> Judgment:
        """Build a judgment that the closure does not cause re-entrancy issues.

        A closure is re-entrant-safe if it does not mutate any shared cells
        while a concurrent execution of the same closure may be reading them.
        This is an undecidable property in general; the judgment is left at
        :attr:`TrustLevel.UNVERIFIED` and requires manual or runtime evidence.

        Parameters:
            record: The closure record to annotate.

        Returns:
            A :class:`Judgment` with ``status=PROPOSED``.
        """
        coord = self._make_coord(record)
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=(
                f"reentrant_safe({record.function_key}) ↔ "
                f"¬ (∃ cell . shared_cell(cell, {record.function_key}) ∧ "
                f"concurrent_mutation(cell))"
            ),
            free_variables=record.all_free_names,
        )
        carrier = Carrier(name="ReentrancyCarrier")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )

    def build_lifetime_judgment(self, record: ClosureRecord) -> Judgment:
        """Build a judgment about the lifetime of captured variables.

        The proposition asserts that every captured name has a lifetime
        that extends at least as long as the closure object itself; a
        violation would cause a use-after-free in languages with manual
        memory management, but in CPython is instead a subtle scope-escape
        bug (e.g., loop variable capture).

        Parameters:
            record: The closure record to annotate.

        Returns:
            A :class:`Judgment` with ``status=PROPOSED``.
        """
        coord = self._make_coord(record)
        names = ", ".join(sorted(record.all_free_names)) or "(none)"
        prop = Proposition(
            kind=PropositionKind.RESOURCE,
            formula=(
                f"∀ name ∈ {{{names}}} . "
                f"lifetime(name) ≥ lifetime(closure({record.function_key}))"
            ),
            free_variables=record.all_free_names,
        )
        carrier = Carrier(name="LifetimeCarrier")
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Helpers
    "free_vars_of",
    "bound_vars_of",
    "closure_cells_of",
    # Classes
    "ClosureDetector",
    "ClosureLifter",
    "CellVariableTracker",
    "ClosureJudgmentBuilder",
]
