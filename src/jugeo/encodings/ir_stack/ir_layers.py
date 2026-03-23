r"""IR layer management for the JuGeo encoding pipeline (theory2.tex Ch32 §2).

This module implements the layer management infrastructure for the JuGeo
intermediate representation (IR) stack.  The IR stack is modelled as a
*filtration* of layers:

.. math::

   \mathcal{L}_0 \hookrightarrow \mathcal{L}_1 \hookrightarrow \cdots
   \hookrightarrow \mathcal{L}_n

where each inclusion map carries the binding environment, accumulated
constraints, and node set of the lower layer into the next, potentially
extending them.  The filtration structure guarantees that every well-formed
reference in :math:`\mathcal{L}_k` remains valid in :math:`\mathcal{L}_{k+1}`,
making cross-layer navigation and diffing tractable.

Architecture
------------
Layer management is split into five collaborating components:

- :class:`LayerScope` — the innermost binding unit; a scope carries a finite
  map from names to IR values and tracks its parent in the scope chain via a
  module-level registry.
- :class:`BindingEnvironment` — a stack of :class:`LayerScope` objects
  representing the full lexical environment of a single IR layer.  Import
  bindings from lower layers are kept separately to enable efficient diffing.
- :class:`ConstraintAccumulator` — collects logical constraints (equality,
  inequality, type membership) as they are generated during lowering and
  elaboration.  The accumulator provides lightweight consistency checking and
  can emit Z3-ready assertion dicts.
- :class:`LayerDiffer` — computes, caches, applies, and composes structural
  diffs between pairs of IR layers.  Diffs are the unit of incremental
  re-elaboration and serve as the input to the copilot repair pipeline.
- :class:`CrossLayerRef` — a typed reference from a node in one layer to a
  node in a different layer.  Cross-layer refs are resolved against the live
  :class:`~jugeo.encodings.ir_stack.models.IRStack`.

References
----------
theory2.tex Ch32 §2 — IR Layer Management, pp. 317–341.
"""

from __future__ import annotations

import collections
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (
        IRNode, IRLayer, IRStack, IRNodeKind, IRLayerKind,
        AmbiguityMark, NormalForm, LoweringPass,
    )
except ImportError:
    pass  # type stubs will be used from try/except blocks below

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder
except ImportError:
    class Z3Session:  # type: ignore[no-redef]
        pass

    class Z3Formula:  # type: ignore[no-redef]
        pass

    class Z3Encoder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
except ImportError:
    class TrustAlgebra:  # type: ignore[no-redef]
        pass

    class TrustLevel:  # type: ignore[no-redef]
        pass

# ===================================================================== #
# Module-level scope registry for cross-scope parent traversal           #
# ===================================================================== #

_SCOPE_REGISTRY: dict[str, "LayerScope"] = {}


# ===================================================================== #
# Section 1: Layer creation and initialization                           #
# ===================================================================== #

@dataclass
class LayerScope:
    """A single binding scope within an IR layer's lexical environment.

    Scopes are arranged in a parent-chain: each scope records the id of its
    parent and delegates unresolved lookups upward.  A module-level registry
    maps scope ids to live objects so that parent traversal works without
    passing explicit references through every call site.

    Attributes
    ----------
    scope_id:
        Unique identifier for this scope, assigned on construction.
    parent_scope_id:
        Identifier of the enclosing scope, or ``None`` for the root scope.
    bindings:
        Mutable map from names to their IR values within this scope.
    scope_depth:
        Distance from the root scope; root has depth 0.
    is_sealed:
        When ``True``, no new bindings may be added.  Sealing is permanent.
    created_at:
        Unix timestamp recorded at construction time.
    """

    scope_id: str
    parent_scope_id: str | None
    bindings: dict[str, Any]
    scope_depth: int
    is_sealed: bool
    created_at: float

    def __post_init__(self) -> None:
        """Register this scope in the module-level registry on construction."""
        _SCOPE_REGISTRY[self.scope_id] = self

    # --- mutation ---

    def bind(self, name: str, value: Any) -> None:
        """Add or overwrite a binding in this scope.

        Parameters
        ----------
        name:
            The name to bind.  Must be a non-empty string.
        value:
            The IR value to associate with the name.

        Raises
        ------
        ValueError
            If the scope is sealed or if *name* is an empty string.
        """
        if self.is_sealed:
            raise ValueError(
                f"Scope '{self.scope_id}' (depth {self.scope_depth}) is sealed; "
                f"cannot bind name '{name}'."
            )
        if not name:
            raise ValueError("Binding name must be a non-empty string.")
        self.bindings[name] = value

    def seal(self) -> None:
        """Mark this scope as sealed, preventing any further bindings.

        Sealing is idempotent: calling ``seal()`` on an already-sealed scope
        has no effect and raises no error.  Once sealed, ``bind()`` will
        raise :class:`ValueError` for any subsequent call.
        """
        self.is_sealed = True

    # --- query ---

    def lookup(self, name: str, traverse_parent: bool = True) -> Any | None:
        """Return the value bound to *name*, or ``None`` if unbound.

        The search starts in this scope.  If *name* is not found and
        *traverse_parent* is ``True``, the search continues upward through
        the parent chain by consulting the module-level scope registry.
        The *scope_depth* counter is used as a bound to prevent cycles in
        pathological registry states.

        Parameters
        ----------
        name:
            The name to look up.
        traverse_parent:
            If ``True`` (default) the parent chain is searched when *name*
            is absent from this scope's local bindings.
        """
        if name in self.bindings:
            return self.bindings[name]
        if not traverse_parent or self.parent_scope_id is None:
            return None
        steps_remaining = self.scope_depth
        current_parent_id: str | None = self.parent_scope_id
        while current_parent_id is not None and steps_remaining >= 0:
            parent = _SCOPE_REGISTRY.get(current_parent_id)
            if parent is None:
                break
            if name in parent.bindings:
                return parent.bindings[name]
            current_parent_id = parent.parent_scope_id
            steps_remaining -= 1
        return None

    def child_scope(self) -> LayerScope:
        """Create and return a new child scope whose parent is this scope.

        The child is registered in the module-level registry immediately.
        Its depth is ``self.scope_depth + 1`` and it starts with an empty
        bindings dict.
        """
        child = LayerScope(
            scope_id=str(uuid.uuid4()),
            parent_scope_id=self.scope_id,
            bindings={},
            scope_depth=self.scope_depth + 1,
            is_sealed=False,
            created_at=time.time(),
        )
        return child

    def all_bindings(self, include_parent: bool = False) -> dict[str, Any]:
        """Return the full binding map visible from this scope.

        Parameters
        ----------
        include_parent:
            If ``True``, the returned dict includes all bindings reachable
            through the parent chain.  Local bindings shadow parent bindings
            when names collide (innermost wins).
        """
        if not include_parent or self.parent_scope_id is None:
            return dict(self.bindings)
        merged: dict[str, Any] = {}
        steps_remaining = self.scope_depth
        current_id: str | None = self.parent_scope_id
        while current_id is not None and steps_remaining >= 0:
            scope = _SCOPE_REGISTRY.get(current_id)
            if scope is None:
                break
            for k, v in scope.bindings.items():
                if k not in merged:
                    merged[k] = v
            current_id = scope.parent_scope_id
            steps_remaining -= 1
        merged.update(self.bindings)
        return merged

    def has_free_variable(self, name: str) -> bool:
        """Return ``True`` if *name* is unbound throughout the full scope chain.

        A name is *free* in a scope when ``lookup(name, traverse_parent=True)``
        returns ``None``.  This is used by the constraint accumulator to
        identify open terms that require further elaboration.
        """
        return self.lookup(name, traverse_parent=True) is None

    def free_variables(self) -> set[str]:
        """Return all names that appear as values but are not themselves bound.

        Only string-typed values are considered as candidate variable names;
        dict-typed values are recursively scanned for nested string leaves.
        A name appearing as a value is *free* when it is not bound anywhere
        in the scope chain.
        """
        all_bound = set(self.all_bindings(include_parent=True).keys())
        candidates: set[str] = set()

        def _scan(obj: Any) -> None:
            if isinstance(obj, str):
                # Heuristic: identifiers are non-empty, start with a letter or _
                if obj and (obj[0].isalpha() or obj[0] == "_"):
                    candidates.add(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _scan(v)
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    _scan(item)

        for v in self.bindings.values():
            _scan(v)

        return candidates - all_bound

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this scope."""
        return {
            "scope_id": self.scope_id,
            "parent_scope_id": self.parent_scope_id,
            "bindings": {k: _safe_serialize(v) for k, v in self.bindings.items()},
            "scope_depth": self.scope_depth,
            "is_sealed": self.is_sealed,
            "created_at": self.created_at,
        }


# ===================================================================== #
# Section 2: Binding scopes                                              #
# ===================================================================== #

@dataclass
class BindingEnvironment:
    """Full binding environment for an IR layer, implemented as a scope stack.

    The environment maintains an ordered list of :class:`LayerScope` objects
    (innermost last) and a flat index for O(1) scope retrieval by id.
    Import bindings sourced from lower layers are stored separately so that
    ``diff`` can distinguish local-layer changes from inherited ones.

    Attributes
    ----------
    env_id:
        Unique identifier for this environment.
    scopes:
        Ordered scope stack; the last element is the current (innermost) scope.
    _scope_index:
        Internal dict mapping scope ids to :class:`LayerScope` objects for
        fast lookup without linear scans.
    layer_kind:
        The :class:`~jugeo.encodings.ir_stack.models.IRLayerKind` of the
        owning layer — used when importing symbols to enforce visibility rules.
    import_bindings:
        Bindings imported from lower layers in the filtration.  These are
        read-only from the perspective of the current layer.
    """

    env_id: str
    scopes: list[LayerScope]
    _scope_index: dict[str, LayerScope]
    layer_kind: Any  # IRLayerKind when models are available
    import_bindings: dict[str, Any]

    # copilot: BindingEnvironment.import_from assists copilot oracle in cross-layer symbol resolution

    def push_scope(self) -> LayerScope:
        """Create a new child scope on top of the current scope and return it.

        If the stack is non-empty, the new scope's parent is the current top
        scope.  If the stack is empty, the new scope is a root scope with
        depth 0.  The new scope is appended to ``self.scopes`` and registered
        in ``self._scope_index``.
        """
        if self.scopes:
            new_scope = self.scopes[-1].child_scope()
        else:
            new_scope = LayerScope(
                scope_id=str(uuid.uuid4()),
                parent_scope_id=None,
                bindings={},
                scope_depth=0,
                is_sealed=False,
                created_at=time.time(),
            )
        self.scopes.append(new_scope)
        self._scope_index[new_scope.scope_id] = new_scope
        return new_scope

    def pop_scope(self) -> LayerScope | None:
        """Remove and return the top (innermost) scope.

        Returns ``None`` when the scope stack is empty.  The popped scope
        is removed from ``_scope_index`` so that stale lookups will miss.
        """
        if not self.scopes:
            return None
        top = self.scopes.pop()
        self._scope_index.pop(top.scope_id, None)
        return top

    def current_scope(self) -> LayerScope | None:
        """Return the innermost scope without modifying the stack."""
        return self.scopes[-1] if self.scopes else None

    def bind(self, name: str, value: Any) -> None:
        """Bind *name* to *value* in the current (innermost) scope.

        Raises
        ------
        RuntimeError
            If the scope stack is empty.
        ValueError
            If the current scope is sealed (propagated from :class:`LayerScope`).
        """
        scope = self.current_scope()
        if scope is None:
            raise RuntimeError(
                f"BindingEnvironment '{self.env_id}' has no active scope; "
                "call push_scope() first."
            )
        scope.bind(name, value)

    def lookup(self, name: str) -> Any | None:
        """Search the scope chain top-down, then fall back to import_bindings.

        Returns the first value found for *name*, or ``None`` if absent
        throughout the environment.
        """
        for scope in reversed(self.scopes):
            if name in scope.bindings:
                return scope.bindings[name]
        return self.import_bindings.get(name)

    def import_from(
        self,
        other_env: BindingEnvironment,
        names: list[str],
    ) -> int:
        """Import named bindings from *other_env* into ``self.import_bindings``.

        Only bindings that exist in *other_env* (searched via its full scope
        chain and its own import_bindings) are copied.  Existing keys in
        ``self.import_bindings`` are overwritten when a name is found in
        *other_env*.

        Parameters
        ----------
        other_env:
            The source environment; typically a lower layer's environment.
        names:
            List of names to attempt to import.

        Returns
        -------
        int
            Number of names successfully imported (i.e. found in *other_env*).
        """
        imported = 0
        for name in names:
            value = other_env.lookup(name)
            if value is not None:
                self.import_bindings[name] = value
                imported += 1
        return imported

    def snapshot(self) -> dict[str, Any]:
        """Capture the full environment state as a serialisable dict.

        The snapshot includes the ordered scope list (each serialised via
        :meth:`LayerScope.to_dict`) and the current import_bindings.  It
        can be passed to :meth:`restore` to roll back to this point.
        """
        return {
            "env_id": self.env_id,
            "scopes": [s.to_dict() for s in self.scopes],
            "import_bindings": {k: _safe_serialize(v) for k, v in self.import_bindings.items()},
            "layer_kind": str(self.layer_kind) if self.layer_kind is not None else None,
            "captured_at": time.time(),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore environment state from a previously captured snapshot.

        The scope stack is rebuilt from the snapshot data.  Each scope is
        reconstructed and re-registered in the module-level ``_SCOPE_REGISTRY``
        and in ``self._scope_index``.  The current scope stack is replaced
        entirely.
        """
        self.scopes.clear()
        self._scope_index.clear()
        for scope_data in snapshot.get("scopes", []):
            restored_scope = LayerScope(
                scope_id=scope_data["scope_id"],
                parent_scope_id=scope_data.get("parent_scope_id"),
                bindings=dict(scope_data.get("bindings", {})),
                scope_depth=scope_data.get("scope_depth", 0),
                is_sealed=scope_data.get("is_sealed", False),
                created_at=scope_data.get("created_at", 0.0),
            )
            self.scopes.append(restored_scope)
            self._scope_index[restored_scope.scope_id] = restored_scope
        self.import_bindings = dict(snapshot.get("import_bindings", {}))

    def diff(self, other: BindingEnvironment) -> dict[str, Any]:
        """Return a dict describing the binding differences between environments.

        Collects all names visible in each environment (scope chain +
        import_bindings), then categorises each name as added (present only
        in *other*), removed (present only in *self*), or changed (present in
        both but with different values).

        Parameters
        ----------
        other:
            The environment to compare against.

        Returns
        -------
        dict
            Keys ``"added"``, ``"removed"``, ``"changed"`` each map to a list
            of name strings.
        """
        def _collect_all(env: BindingEnvironment) -> dict[str, Any]:
            result: dict[str, Any] = {}
            result.update(env.import_bindings)
            for scope in env.scopes:
                result.update(scope.bindings)
            return result

        self_bindings = _collect_all(self)
        other_bindings = _collect_all(other)
        self_names = set(self_bindings.keys())
        other_names = set(other_bindings.keys())

        added = sorted(other_names - self_names)
        removed = sorted(self_names - other_names)
        changed = sorted(
            name
            for name in self_names & other_names
            if _safe_serialize(self_bindings[name]) != _safe_serialize(other_bindings[name])
        )
        return {"added": added, "removed": removed, "changed": changed}


# ===================================================================== #
# Section 3: Constraint accumulation                                     #
# ===================================================================== #

@dataclass
class ConstraintAccumulator:
    """Accumulates logical constraints generated during IR lowering.

    Constraints are stored as typed dicts so that they remain solver-agnostic
    until explicitly converted to Z3 assertions.  Three constraint kinds are
    natively understood: ``equality``, ``inequality``, and ``type_membership``.
    Any additional kinds are stored verbatim and counted.

    Consistency is tracked lazily: the ``is_consistent`` flag is reset to
    ``None`` whenever a new constraint is added, and recomputed only when
    :meth:`check_consistency` is explicitly called.

    Attributes
    ----------
    accumulator_id:
        Unique identifier for this accumulator.
    constraints:
        Ordered list of constraint dicts.  Each dict must have a ``"kind"`` key.
    constraint_kinds:
        Counter mapping constraint kind strings to occurrence counts.
    is_consistent:
        ``True`` / ``False`` after a consistency check, ``None`` when stale.
    last_checked:
        Unix timestamp of the most recent :meth:`check_consistency` call.
    """

    accumulator_id: str
    constraints: list[dict[str, Any]]
    constraint_kinds: dict[str, int]
    is_consistent: bool | None
    last_checked: float

    def add(self, constraint: dict[str, Any]) -> None:
        """Append a constraint dict to the accumulator.

        The ``"kind"`` key is required.  Adding a constraint invalidates the
        ``is_consistent`` flag, requiring a fresh call to
        :meth:`check_consistency`.

        Parameters
        ----------
        constraint:
            Must contain at least a ``"kind"`` key with a non-empty string value.

        Raises
        ------
        ValueError
            If *constraint* is missing the ``"kind"`` key or the kind is empty.
        """
        kind = constraint.get("kind", "")
        if not kind:
            raise ValueError(
                "Constraint dict must contain a non-empty 'kind' key."
            )
        self.constraints.append(constraint)
        self.constraint_kinds[kind] = self.constraint_kinds.get(kind, 0) + 1
        self.is_consistent = None  # stale — needs recheck

    def add_equality(self, lhs: str, rhs: str) -> None:
        """Record an equality constraint ``lhs = rhs``.

        Parameters
        ----------
        lhs:
            Left-hand side term identifier.
        rhs:
            Right-hand side term identifier.
        """
        self.add({
            "kind": "equality",
            "lhs": lhs,
            "rhs": rhs,
            "added_at": time.time(),
        })

    def add_inequality(self, lhs: str, rhs: str, strict: bool = False) -> None:
        """Record an inequality constraint.

        Parameters
        ----------
        lhs:
            Left-hand side term identifier.
        rhs:
            Right-hand side term identifier.
        strict:
            If ``True``, records a strict inequality (``<``); otherwise records
            a non-strict inequality (``<=``).
        """
        self.add({
            "kind": "inequality",
            "lhs": lhs,
            "rhs": rhs,
            "strict": strict,
            "added_at": time.time(),
        })

    def add_type_constraint(self, term: str, type_repr: str) -> None:
        """Record a type membership constraint ``term : type_repr``.

        Parameters
        ----------
        term:
            The term whose type is being constrained.
        type_repr:
            String representation of the expected type.
        """
        self.add({
            "kind": "type_membership",
            "term": term,
            "type_repr": type_repr,
            "added_at": time.time(),
        })

    def check_consistency(self) -> bool:
        """Perform a lightweight syntactic consistency check.

        The check scans equality constraints to find contradictions of the
        form ``x = A`` and ``x = B`` where ``A != B``.  No solver is invoked.
        More subtle inconsistencies (e.g. cyclic equalities, type conflicts)
        are not detected here and require a full Z3 call.

        Sets ``self.is_consistent`` and ``self.last_checked`` as side effects.

        Returns
        -------
        bool
            ``True`` if no syntactic contradiction was found.
        """
        equalities: dict[str, str] = {}
        for c in self.constraints:
            if c.get("kind") != "equality":
                continue
            lhs = c.get("lhs", "")
            rhs = c.get("rhs", "")
            if lhs in equalities and equalities[lhs] != rhs:
                self.is_consistent = False
                self.last_checked = time.time()
                return False
            equalities[lhs] = rhs
        # Also check for type contradictions: term typed as two different types
        type_map: dict[str, str] = {}
        for c in self.constraints:
            if c.get("kind") != "type_membership":
                continue
            term = c.get("term", "")
            typ = c.get("type_repr", "")
            if term in type_map and type_map[term] != typ:
                self.is_consistent = False
                self.last_checked = time.time()
                return False
            type_map[term] = typ
        self.is_consistent = True
        self.last_checked = time.time()
        return True

    def simplify(self) -> int:
        """Remove duplicate constraints, returning the number removed.

        Two constraints are considered duplicates when their JSON serialisation
        (with keys sorted) is identical.  The first occurrence is kept and
        later duplicates are dropped.  The ``constraint_kinds`` counter is
        rebuilt from scratch after deduplication.
        """
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for c in self.constraints:
            key = json.dumps(c, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        removed = len(self.constraints) - len(unique)
        self.constraints = unique
        # Rebuild kind counts
        self.constraint_kinds = {}
        for c in self.constraints:
            kind = c.get("kind", "unknown")
            self.constraint_kinds[kind] = self.constraint_kinds.get(kind, 0) + 1
        return removed

    def to_z3_assertions(self) -> list[dict[str, Any]]:
        """Convert accumulated constraints to Z3-ready assertion dicts.

        Each constraint is translated to a dict with keys ``"op"``, ``"args"``,
        and ``"meta"`` suitable for consumption by a Z3Encoder.  Unsupported
        constraint kinds are passed through as ``"raw"`` assertions.

        Returns
        -------
        list[dict[str, Any]]
            List of Z3-ready assertion dicts.
        """
        assertions: list[dict[str, Any]] = []
        for c in self.constraints:
            kind = c.get("kind", "")
            if kind == "equality":
                assertions.append({
                    "op": "eq",
                    "args": [c.get("lhs"), c.get("rhs")],
                    "meta": {"source_kind": "equality"},
                })
            elif kind == "inequality":
                op = "lt" if c.get("strict") else "le"
                assertions.append({
                    "op": op,
                    "args": [c.get("lhs"), c.get("rhs")],
                    "meta": {"source_kind": "inequality", "strict": c.get("strict", False)},
                })
            elif kind == "type_membership":
                assertions.append({
                    "op": "type_check",
                    "args": [c.get("term"), c.get("type_repr")],
                    "meta": {"source_kind": "type_membership"},
                })
            else:
                assertions.append({
                    "op": "raw",
                    "args": [],
                    "meta": {"source_kind": kind, "raw": c},
                })
        return assertions

    def merge(self, other: ConstraintAccumulator) -> ConstraintAccumulator:
        """Return a new accumulator containing the union of both constraint sets.

        Duplicate constraints (by JSON key) are deduplicated in the result.
        The returned accumulator has ``is_consistent = None`` since consistency
        of the union must be rechecked.

        Parameters
        ----------
        other:
            The accumulator to merge with this one.
        """
        combined = ConstraintAccumulator(
            accumulator_id=str(uuid.uuid4()),
            constraints=list(self.constraints) + list(other.constraints),
            constraint_kinds=dict(self.constraint_kinds),
            is_consistent=None,
            last_checked=0.0,
        )
        for kind, count in other.constraint_kinds.items():
            combined.constraint_kinds[kind] = (
                combined.constraint_kinds.get(kind, 0) + count
            )
        combined.simplify()
        return combined

    def statistics(self) -> dict[str, Any]:
        """Return a summary dict of constraint counts and consistency status."""
        return {
            "accumulator_id": self.accumulator_id,
            "total": len(self.constraints),
            "by_kind": dict(self.constraint_kinds),
            "is_consistent": self.is_consistent,
            "last_checked": self.last_checked,
        }


# ===================================================================== #
# Section 4: Layer diffing and merging                                   #
# ===================================================================== #

@dataclass
class LayerDiffer:
    """Computes structural diffs between pairs of IR layers.

    Diffs are keyed by the string ``"<before_id>:<after_id>"`` and stored in
    an internal cache to avoid redundant recomputation.  The diff format is
    a plain dict with four top-level lists: ``nodes_added``, ``nodes_removed``,
    ``nodes_changed``, and ``bindings_changed``.

    Attributes
    ----------
    diff_id:
        Unique identifier for this differ instance.
    diff_strategy:
        Human-readable label for the comparison strategy (e.g.
        ``"structural"`` or ``"semantic"``).
    _diff_cache:
        Internal dict mapping ``"before_id:after_id"`` to cached diff dicts.
    """

    diff_id: str
    diff_strategy: str
    _diff_cache: dict[str, dict]

    def _layer_node_map(self, layer: Any) -> dict[str, Any]:
        """Extract a {node_id: node} dict from an IRLayer-like object."""
        nodes: dict[str, Any] = {}
        node_list = getattr(layer, "nodes", None) or []
        for node in node_list:
            nid = getattr(node, "node_id", None) or getattr(node, "id", None)
            if nid is not None:
                nodes[str(nid)] = node
        return nodes

    def _layer_binding_map(self, layer: Any) -> dict[str, Any]:
        """Extract a flat bindings dict from an IRLayer-like object."""
        bindings = getattr(layer, "bindings", None)
        if isinstance(bindings, dict):
            return dict(bindings)
        env = getattr(layer, "environment", None)
        if env is not None:
            all_b: dict[str, Any] = {}
            for scope in getattr(env, "scopes", []):
                all_b.update(getattr(scope, "bindings", {}))
            all_b.update(getattr(env, "import_bindings", {}))
            return all_b
        return {}

    def compute_diff(self, before: Any, after: Any) -> dict[str, Any]:
        """Compute the structural diff between two IR layers.

        Compares node sets (by node_id) and flat binding maps.  Returns a
        dict with keys ``nodes_added``, ``nodes_removed``, ``nodes_changed``,
        ``bindings_changed``, ``before_id``, ``after_id``, and
        ``computed_at``.

        Parameters
        ----------
        before:
            The earlier IR layer.
        after:
            The later IR layer.
        """
        before_id = str(getattr(before, "layer_id", id(before)))
        after_id = str(getattr(after, "layer_id", id(after)))
        cache_key = f"{before_id}:{after_id}"
        if cache_key in self._diff_cache:
            return self._diff_cache[cache_key]

        before_nodes = self._layer_node_map(before)
        after_nodes = self._layer_node_map(after)
        before_bindings = self._layer_binding_map(before)
        after_bindings = self._layer_binding_map(after)

        nodes_added = sorted(set(after_nodes) - set(before_nodes))
        nodes_removed = sorted(set(before_nodes) - set(after_nodes))
        nodes_changed: list[str] = []
        for nid in set(before_nodes) & set(after_nodes):
            b_repr = json.dumps(getattr(before_nodes[nid], "payload", {}), sort_keys=True, default=str)
            a_repr = json.dumps(getattr(after_nodes[nid], "payload", {}), sort_keys=True, default=str)
            if b_repr != a_repr:
                nodes_changed.append(nid)
        nodes_changed.sort()

        bindings_changed: dict[str, Any] = {}
        all_binding_names = set(before_bindings) | set(after_bindings)
        for name in sorted(all_binding_names):
            bv = _safe_serialize(before_bindings.get(name))
            av = _safe_serialize(after_bindings.get(name))
            if bv != av:
                bindings_changed[name] = {"before": bv, "after": av}

        diff: dict[str, Any] = {
            "before_id": before_id,
            "after_id": after_id,
            "nodes_added": nodes_added,
            "nodes_removed": nodes_removed,
            "nodes_changed": nodes_changed,
            "bindings_changed": bindings_changed,
            "computed_at": time.time(),
            "strategy": self.diff_strategy,
        }
        self._diff_cache[cache_key] = diff
        return diff

    def is_empty_diff(self, diff: dict[str, Any]) -> bool:
        """Return ``True`` when the diff contains no changes.

        A diff is empty when all four change lists/dicts are empty.
        """
        return (
            not diff.get("nodes_added")
            and not diff.get("nodes_removed")
            and not diff.get("nodes_changed")
            and not diff.get("bindings_changed")
        )

    def apply_diff(self, base: Any, diff: dict[str, Any]) -> dict[str, Any]:
        """Apply a diff to a base layer and return the result as a plain dict.

        The result dict contains the merged node set and binding map.  Node
        removals are applied first, then additions, then in-place changes are
        recorded.  This method does not mutate *base*.

        Parameters
        ----------
        base:
            The baseline IR layer.
        diff:
            A diff dict as returned by :meth:`compute_diff`.
        """
        node_map = dict(self._layer_node_map(base))
        bindings = dict(self._layer_binding_map(base))

        for nid in diff.get("nodes_removed", []):
            node_map.pop(nid, None)
        for nid in diff.get("nodes_added", []):
            node_map[nid] = {"node_id": nid, "status": "added_by_diff"}
        for name, change in diff.get("bindings_changed", {}).items():
            bindings[name] = change.get("after")

        return {
            "layer_id": diff.get("after_id", str(uuid.uuid4())),
            "node_ids": sorted(node_map.keys()),
            "bindings": bindings,
            "applied_at": time.time(),
        }

    def invert_diff(self, diff: dict[str, Any]) -> dict[str, Any]:
        """Return the inverse diff that undoes the changes in *diff*.

        The inverse swaps ``before_id`` / ``after_id``, reverses
        ``nodes_added`` / ``nodes_removed``, and inverts each binding change
        entry so that ``"before"`` and ``"after"`` values are swapped.

        Parameters
        ----------
        diff:
            A diff dict as returned by :meth:`compute_diff`.
        """
        inverted_bindings: dict[str, Any] = {}
        for name, change in diff.get("bindings_changed", {}).items():
            inverted_bindings[name] = {
                "before": change.get("after"),
                "after": change.get("before"),
            }
        return {
            "before_id": diff.get("after_id"),
            "after_id": diff.get("before_id"),
            "nodes_added": list(diff.get("nodes_removed", [])),
            "nodes_removed": list(diff.get("nodes_added", [])),
            "nodes_changed": list(diff.get("nodes_changed", [])),
            "bindings_changed": inverted_bindings,
            "computed_at": time.time(),
            "strategy": self.diff_strategy,
            "is_inverse": True,
        }

    def compose_diffs(self, diff1: dict, diff2: dict) -> dict:
        """Combine two sequential diffs into one.

        *diff1* is applied first, *diff2* second.  The composed diff records
        the union of additions, removals, and binding changes, resolving
        conflicts by preferring *diff2* (later diff wins).

        Parameters
        ----------
        diff1:
            The earlier diff.
        diff2:
            The later diff to compose on top of *diff1*.
        """
        added1 = set(diff1.get("nodes_added", []))
        removed1 = set(diff1.get("nodes_removed", []))
        added2 = set(diff2.get("nodes_added", []))
        removed2 = set(diff2.get("nodes_removed", []))

        composed_added = sorted((added1 - removed2) | (added2 - removed1))
        composed_removed = sorted((removed1 - added2) | (removed2 - added1))
        composed_changed = sorted(
            set(diff1.get("nodes_changed", [])) | set(diff2.get("nodes_changed", []))
        )

        composed_bindings: dict[str, Any] = {}
        composed_bindings.update(diff1.get("bindings_changed", {}))
        for name, change in diff2.get("bindings_changed", {}).items():
            if name in composed_bindings:
                composed_bindings[name] = {
                    "before": composed_bindings[name].get("before"),
                    "after": change.get("after"),
                }
            else:
                composed_bindings[name] = change

        return {
            "before_id": diff1.get("before_id"),
            "after_id": diff2.get("after_id"),
            "nodes_added": composed_added,
            "nodes_removed": composed_removed,
            "nodes_changed": composed_changed,
            "bindings_changed": composed_bindings,
            "computed_at": time.time(),
            "strategy": self.diff_strategy,
            "composed_from": [diff1.get("before_id"), diff2.get("after_id")],
        }

    def diff_summary(self, diff: dict) -> str:
        """Return a human-readable one-line summary of the diff.

        The summary format is::

            [before_id → after_id] +N nodes, -M nodes, ~K nodes, B bindings changed

        Parameters
        ----------
        diff:
            A diff dict as returned by :meth:`compute_diff`.
        """
        before_id = (diff.get("before_id") or "?")[:8]
        after_id = (diff.get("after_id") or "?")[:8]
        n_add = len(diff.get("nodes_added", []))
        n_rem = len(diff.get("nodes_removed", []))
        n_chg = len(diff.get("nodes_changed", []))
        n_bind = len(diff.get("bindings_changed", {}))
        return (
            f"[{before_id}→{after_id}] "
            f"+{n_add} nodes, -{n_rem} nodes, ~{n_chg} nodes, {n_bind} bindings changed"
        )

    def cache_diff(self, before_id: str, after_id: str, diff: dict) -> None:
        """Store a precomputed diff in the internal cache.

        Parameters
        ----------
        before_id:
            Layer id of the before-layer.
        after_id:
            Layer id of the after-layer.
        diff:
            The diff dict to cache.
        """
        self._diff_cache[f"{before_id}:{after_id}"] = diff

    def cached_diff(self, before_id: str, after_id: str) -> dict | None:
        """Retrieve a previously cached diff, or ``None`` if not present.

        Parameters
        ----------
        before_id:
            Layer id of the before-layer.
        after_id:
            Layer id of the after-layer.
        """
        return self._diff_cache.get(f"{before_id}:{after_id}")


# ===================================================================== #
# Section 5: Cross-layer references                                      #
# ===================================================================== #

@dataclass
class CrossLayerRef:
    """A typed reference from a node in one IR layer to a node in another.

    Cross-layer refs capture the provenance of IR nodes that are defined
    in one layer but used in another.  The ``ref_kind`` attribute records
    the semantic relationship (e.g. ``"definition"``, ``"use"``,
    ``"instantiation"``, ``"proof_obligation"``).

    Resolution is performed lazily via :meth:`resolve`, which looks up the
    target node in a live :class:`~jugeo.encodings.ir_stack.models.IRStack`.

    Attributes
    ----------
    ref_id:
        Unique identifier for this reference.
    source_layer_id:
        ID of the layer containing the referring node.
    source_node_id:
        ID of the referring node.
    target_layer_id:
        ID of the layer containing the referenced node.
    target_node_id:
        ID of the referenced node.
    ref_kind:
        Semantic kind of the reference (free-form string).
    metadata:
        Arbitrary additional data attached to the reference.
    """

    ref_id: str
    source_layer_id: str
    source_node_id: str
    target_layer_id: str
    target_node_id: str
    ref_kind: str
    metadata: dict[str, Any]

    def is_forward_ref(self) -> bool:
        """Return ``True`` when the target layer comes after the source layer.

        Layer ordering is determined lexicographically on layer ids, which
        must follow the ``"layer_<N>"`` convention for this comparison to be
        meaningful.  For opaque ids, the comparison still gives a stable but
        potentially arbitrary ordering.
        """
        return self.target_layer_id > self.source_layer_id

    def is_backward_ref(self) -> bool:
        """Return ``True`` when the target layer comes before the source layer.

        The complement of :meth:`is_forward_ref`; same ordering semantics.
        """
        return self.target_layer_id < self.source_layer_id

    def resolve(self, stack: Any) -> Any | None:
        """Resolve this reference by looking up the target node in *stack*.

        Navigates to the target layer via ``stack.get_layer(target_layer_id)``
        then looks up the target node from the layer's node list.  Returns
        ``None`` if either the layer or the node is not found.

        Parameters
        ----------
        stack:
            A live :class:`~jugeo.encodings.ir_stack.models.IRStack` instance.
        """
        get_layer = getattr(stack, "get_layer", None)
        if get_layer is None:
            # Fallback: try indexing into a layers list
            layers = getattr(stack, "layers", [])
            target_layer = next(
                (l for l in layers if getattr(l, "layer_id", None) == self.target_layer_id),
                None,
            )
        else:
            target_layer = get_layer(self.target_layer_id)

        if target_layer is None:
            return None

        nodes = getattr(target_layer, "nodes", [])
        for node in nodes:
            nid = getattr(node, "node_id", None) or getattr(node, "id", None)
            if str(nid) == self.target_node_id:
                return node
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this reference."""
        return {
            "ref_id": self.ref_id,
            "source_layer_id": self.source_layer_id,
            "source_node_id": self.source_node_id,
            "target_layer_id": self.target_layer_id,
            "target_node_id": self.target_node_id,
            "ref_kind": self.ref_kind,
            "metadata": {k: _safe_serialize(v) for k, v in self.metadata.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrossLayerRef:
        """Reconstruct a :class:`CrossLayerRef` from a serialised dict.

        Parameters
        ----------
        data:
            A dict as returned by :meth:`to_dict`.
        """
        return cls(
            ref_id=data.get("ref_id", str(uuid.uuid4())),
            source_layer_id=data["source_layer_id"],
            source_node_id=data["source_node_id"],
            target_layer_id=data["target_layer_id"],
            target_node_id=data["target_node_id"],
            ref_kind=data.get("ref_kind", "unknown"),
            metadata=dict(data.get("metadata", {})),
        )

    def validate(self) -> list[str]:
        """Return a list of validation errors for this reference.

        An empty list means the reference is structurally valid.  Errors are
        returned (not raised) so that batch validation remains non-fatal.
        """
        errors: list[str] = []
        if not self.ref_id:
            errors.append("ref_id must be non-empty.")
        if not self.source_layer_id:
            errors.append("source_layer_id must be non-empty.")
        if not self.source_node_id:
            errors.append("source_node_id must be non-empty.")
        if not self.target_layer_id:
            errors.append("target_layer_id must be non-empty.")
        if not self.target_node_id:
            errors.append("target_node_id must be non-empty.")
        if not self.ref_kind:
            errors.append("ref_kind must be non-empty.")
        if self.source_layer_id == self.target_layer_id and self.source_node_id == self.target_node_id:
            errors.append(
                "Self-references (source == target) are not valid CrossLayerRefs."
            )
        return errors


# ===================================================================== #
# Section 6: Layer serialization                                         #
# ===================================================================== #

def _safe_serialize(value: Any) -> Any:
    """Return a JSON-safe representation of *value*.

    Strings, ints, floats, booleans, and ``None`` pass through unchanged.
    Dicts and lists are recursively processed.  Anything else is converted
    via :func:`str`.

    Parameters
    ----------
    value:
        The value to sanitise.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_serialize(item) for item in value]
    return str(value)


# ===================================================================== #
# Module-level factory and utility functions                             #
# ===================================================================== #

def create_layer_scope(parent: LayerScope | None = None) -> LayerScope:
    """Create a new :class:`LayerScope`, optionally parented to *parent*.

    When *parent* is provided, the new scope's ``parent_scope_id`` and
    ``scope_depth`` are derived from it.  When ``None``, a root scope with
    depth 0 is created.

    Parameters
    ----------
    parent:
        An existing scope to nest under, or ``None`` for a root scope.
    """
    return LayerScope(
        scope_id=str(uuid.uuid4()),
        parent_scope_id=parent.scope_id if parent is not None else None,
        bindings={},
        scope_depth=(parent.scope_depth + 1) if parent is not None else 0,
        is_sealed=False,
        created_at=time.time(),
    )


def create_binding_environment(layer_kind: Any) -> BindingEnvironment:
    """Create a fresh :class:`BindingEnvironment` for a given layer kind.

    The environment starts with a single root scope already pushed onto the
    stack.  The root scope is the outermost scope for the layer and remains
    open until explicitly sealed.

    Parameters
    ----------
    layer_kind:
        An :class:`~jugeo.encodings.ir_stack.models.IRLayerKind` value, or any
        string-like object identifying the layer kind.
    """
    env = BindingEnvironment(
        env_id=str(uuid.uuid4()),
        scopes=[],
        _scope_index={},
        layer_kind=layer_kind,
        import_bindings={},
    )
    env.push_scope()
    return env


def diff_layers(before: Any, after: Any) -> dict[str, Any]:
    """Compute and return the structural diff between two IR layers.

    This is a convenience wrapper around :class:`LayerDiffer` for one-off
    diff computation.  A temporary differ with strategy ``"structural"`` is
    created, used, and discarded.

    Parameters
    ----------
    before:
        The earlier IR layer.
    after:
        The later IR layer (post-transformation).
    """
    differ = LayerDiffer(
        diff_id=str(uuid.uuid4()),
        diff_strategy="structural",
        _diff_cache={},
    )
    return differ.compute_diff(before, after)


def merge_layers(layers: list[Any]) -> dict[str, Any]:
    """Merge multiple IR layers into a single consolidated layer dict.

    Merging proceeds left-to-right: each layer's nodes and bindings are
    folded into the accumulator.  Binding name collisions are resolved by
    last-writer-wins (rightmost layer in the list wins).

    Parameters
    ----------
    layers:
        An ordered list of IR layers to merge.  Must contain at least one
        element.

    Returns
    -------
    dict
        A plain dict with ``layer_id``, ``node_ids``, ``bindings``, and
        ``source_layer_ids`` keys.

    Raises
    ------
    ValueError
        If *layers* is empty.
    """
    if not layers:
        raise ValueError("merge_layers requires at least one layer.")

    merged_nodes: dict[str, Any] = {}
    merged_bindings: dict[str, Any] = {}
    source_ids: list[str] = []

    for layer in layers:
        lid = str(getattr(layer, "layer_id", id(layer)))
        source_ids.append(lid)
        for node in getattr(layer, "nodes", []):
            nid = str(getattr(node, "node_id", None) or getattr(node, "id", id(node)))
            merged_nodes[nid] = node
        layer_bindings = getattr(layer, "bindings", {})
        if isinstance(layer_bindings, dict):
            merged_bindings.update(layer_bindings)

    return {
        "layer_id": str(uuid.uuid4()),
        "node_ids": sorted(merged_nodes.keys()),
        "bindings": merged_bindings,
        "source_layer_ids": source_ids,
        "merged_at": time.time(),
    }


def collect_cross_layer_refs(stack: Any) -> list[CrossLayerRef]:
    """Collect all :class:`CrossLayerRef` objects stored in *stack*.

    Iterates over all layers in the stack and collects any ``cross_refs``
    attribute (expected to be a list of :class:`CrossLayerRef` objects or
    compatible dicts) into a single flat list.

    Parameters
    ----------
    stack:
        A live :class:`~jugeo.encodings.ir_stack.models.IRStack` instance.

    Returns
    -------
    list[CrossLayerRef]
        All cross-layer references found across all layers.
    """
    refs: list[CrossLayerRef] = []
    layers = getattr(stack, "layers", [])
    for layer in layers:
        raw_refs = getattr(layer, "cross_refs", []) or []
        for raw in raw_refs:
            if isinstance(raw, CrossLayerRef):
                refs.append(raw)
            elif isinstance(raw, dict):
                try:
                    refs.append(CrossLayerRef.from_dict(raw))
                except (KeyError, TypeError):
                    pass
    return refs
