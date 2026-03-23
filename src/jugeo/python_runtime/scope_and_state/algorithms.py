"""Core algorithmic machinery for scope and state analysis.

This module implements the core algorithmic machinery for scope and state
analysis in the JuGeo Python runtime analyser.  The algorithms implement
the LEGB (Local, Enclosing, Global, Builtin) name-resolution rule as a
sheaf-theoretic restriction operation: each scope lookup step is a morphism
application in the site category, and the ordered chain of scopes is the
covering family through which restrictions compose.

The scope tree algorithms mirror tree operations on the site category.  Each
scope nesting relation corresponds to a restriction morphism in the Grothendieck
topology, and tree-traversal algorithms (DFS, BFS, LCA) correspond to following
chains of restriction maps or computing fibre products over the common ancestor.

Closure analysis implements pullback computations.  When a function closes over
a variable from an enclosing scope the free-variable set is the fibre product of
the function's coordinate with the enclosing scope's coordinate, pulled back
along the enclosing morphism.  The :class:`ClosureAnalysisAlgorithm` exposes
this construction concretely.

The ``ReachabilityAnalyzer`` computes which names are visible from each scope,
which is equivalent to computing the global sections of the name-binding sheaf
restricted to the chain of open sets covering the given scope.

The ``ModuleStateDiffAlgorithm`` implements three-way merging of module
namespace snapshots; this is the semantic analogue of a patch in version control
applied to sections of the namespace sheaf.

Theory reference: theory2.tex Ch15 — *Scope, State, and Sheaf-Theoretic
Name Resolution in the JuGeo Python Runtime Analyser*.

Note: all copilot-integration judgment helpers in this module emit
:class:`~jugeo.judgments.judgment_terms.Judgment` objects with
:attr:`~jugeo.judgments.judgment_terms.PropositionKind.STRUCTURAL` kind so
that the broader JuGeo framework can ingest them without further promotion.
"""

from __future__ import annotations

import logging
from collections import deque
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
    ProvenanceSource,
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
# Module-level constants
# ---------------------------------------------------------------------------

#: Complete set of names in the Python builtins namespace (3.11+).
PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        # Built-in functions
        "abs", "aiter", "all", "anext", "any", "ascii",
        "bin", "bool", "breakpoint", "bytearray", "bytes",
        "callable", "chr", "classmethod", "compile", "complex",
        "delattr", "dict", "dir", "divmod",
        "enumerate", "eval", "exec",
        "filter", "float", "format", "frozenset",
        "getattr", "globals",
        "hasattr", "hash", "help", "hex",
        "id", "input", "int", "isinstance", "issubclass", "iter",
        "len", "list", "locals",
        "map", "max", "memoryview", "min",
        "next",
        "object", "oct", "open", "ord",
        "pow", "print", "property",
        "range", "repr", "reversed", "round",
        "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
        "tuple", "type",
        "vars",
        "zip",
        # Built-in constants
        "None", "True", "False", "NotImplemented", "Ellipsis", "__debug__",
        "__import__", "__loader__", "__name__", "__package__", "__spec__",
        "__build_class__",
        # Built-in exception types
        "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
        "BaseExceptionGroup", "BlockingIOError", "BrokenPipeError", "BufferError",
        "BytesWarning", "ChildProcessError", "ConnectionAbortedError",
        "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
        "DeprecationWarning", "EOFError", "EnvironmentError", "Exception",
        "ExceptionGroup", "FileExistsError", "FileNotFoundError",
        "FloatingPointError", "FutureWarning", "GeneratorExit", "IOError",
        "ImportError", "ImportWarning", "IndentationError", "IndexError",
        "InterruptedError", "IsADirectoryError", "KeyError", "KeyboardInterrupt",
        "LookupError", "MemoryError", "ModuleNotFoundError", "NameError",
        "NotADirectoryError", "NotImplementedError", "OSError", "OverflowError",
        "PendingDeprecationWarning", "PermissionError", "ProcessLookupError",
        "RecursionError", "ReferenceError", "ResourceWarning", "RuntimeError",
        "RuntimeWarning", "StopAsyncIteration", "StopIteration", "SyntaxError",
        "SyntaxWarning", "SystemError", "SystemExit", "TabError", "TimeoutError",
        "TypeError", "UnboundLocalError", "UnicodeDecodeError",
        "UnicodeEncodeError", "UnicodeError", "UnicodeTranslateError",
        "UnicodeWarning", "UserWarning", "ValueError", "Warning",
        "ZeroDivisionError",
    }
)

# Sentinel depth value returned by scope_distance when scopes are disconnected.
_DISCONNECTED: int = -1


# ---------------------------------------------------------------------------
# NameResolutionEngine
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NameResolutionEngine:
    """Full name-resolution algorithm implementing the LEGB rule.

    In the sheaf-theoretic framing (theory2.tex Ch15), resolving a name ``x``
    from scope ``S`` is a sequence of restriction maps:

    .. code-block:: text

        res(x, S_local) → res(x, S_enclosing) → res(x, S_global)
                        → res(x, S_builtin)

    Each step is a restriction of the name-binding section to a smaller open
    set in the Grothendieck topology.  The :meth:`resolve` method implements
    this chain for the standard Python LEGB order.

    Attributes:
        module_name: Dotted name of the module being analysed, used to
            construct synthetic builtins coordinates and evidence payloads.
        _resolution_cache: Memoisation cache keyed by ``"<name>@<module_key>"``
            strings.  Cleared explicitly with :meth:`clear_cache`.
        _conflict_log: Ordered list of human-readable conflict messages
            accumulated by :meth:`detect_resolution_conflicts`.
        _call_count: Monotonically increasing counter incremented on every
            call to :meth:`resolve`.
        _cache_hits: Count of cache hits accumulated across all :meth:`resolve`
            calls.
    """

    module_name: str
    _resolution_cache: dict[str, NameResolutionResult] = field(
        default_factory=dict
    )
    _conflict_log: list[str] = field(default_factory=list)
    _call_count: int = 0
    _cache_hits: int = 0

    # ------------------------------------------------------------------
    # Primary resolution entry-point
    # ------------------------------------------------------------------

    def resolve(
        self, name: str, scope_chain: ScopeChain
    ) -> NameResolutionResult:
        """Resolve a name via the LEGB rule through *scope_chain*.

        Implements the restriction chain described in theory2.tex Ch15 §3.2.
        Each scope in *scope_chain* is tried from innermost to outermost; if
        the name is not found in any user scope the builtins namespace is
        consulted last.

        Parameters:
            name: The bare Python identifier to resolve (e.g. ``"x"``).
            scope_chain: Ordered sequence of scopes, innermost first.

        Returns:
            A :class:`~jugeo.python_runtime.scope_and_state.models.NameResolutionResult`
            describing where the name was found (or not found).
        """
        self._call_count += 1
        cache_key = f"{name}@{scope_chain.module_key}"
        if cache_key in self._resolution_cache:
            self._cache_hits += 1
            return self._resolution_cache[cache_key]

        path: list[str] = []
        for scope in scope_chain.scopes:
            path.append(scope.scope_key)
            coord = self.resolve_in_scope(name, scope)
            if coord is not None:
                result = NameResolutionResult(
                    name=name,
                    resolved=True,
                    coordinate=coord,
                    scope_key=scope.scope_key,
                    resolution_path=tuple(path),
                )
                self._resolution_cache[cache_key] = result
                logger.debug(
                    "Resolved '%s' in scope '%s' (path depth %d)",
                    name,
                    scope.scope_key,
                    len(path),
                )
                return result

        # Fallback: builtins
        builtin_coord = self.resolve_builtin(name)
        if builtin_coord is not None:
            result = NameResolutionResult(
                name=name,
                resolved=True,
                coordinate=builtin_coord,
                scope_key="__builtins__",
                resolution_path=tuple(path),
            )
            self._resolution_cache[cache_key] = result
            return result

        result = NameResolutionResult.not_found(name, tuple(path))
        self._resolution_cache[cache_key] = result
        logger.debug(
            "Failed to resolve '%s'; searched %d scopes", name, len(path)
        )
        return result

    # ------------------------------------------------------------------
    # Single-scope helpers
    # ------------------------------------------------------------------

    def resolve_in_scope(
        self, name: str, scope: ScopeSection
    ) -> NameCoordinate | None:
        """Look up *name* in the bindings of a single *scope*.

        Performs a linear scan of ``scope.bindings``.  The caller is
        responsible for iterating scopes in LEGB order.

        Parameters:
            name: The bare identifier to find.
            scope: The :class:`ScopeSection` whose bindings are searched.

        Returns:
            The matching :class:`NameCoordinate`, or ``None`` if *name* is not
            bound in *scope*.
        """
        for binding in scope.bindings:
            if binding.name == name:
                return binding
        return None

    def resolve_global(
        self, name: str, module_scope: ScopeSection
    ) -> NameCoordinate | None:
        """Look up *name* directly in the module-level scope.

        This is a thin wrapper around :meth:`resolve_in_scope` that is
        called explicitly when a ``global`` statement has been encountered
        and the resolution should skip straight to module scope.

        Parameters:
            name: The bare identifier to find.
            module_scope: The top-level module :class:`ScopeSection`.

        Returns:
            The matching :class:`NameCoordinate`, or ``None`` if not found.
        """
        return self.resolve_in_scope(name, module_scope)

    def resolve_builtin(self, name: str) -> NameCoordinate | None:
        """Create a synthetic :class:`NameCoordinate` if *name* is a builtin.

        Checks membership in :data:`PYTHON_BUILTINS` and, if found, constructs
        a coordinate pinned to the ``"__builtins__"`` scope with kind
        :attr:`~jugeo.python_runtime.scope_and_state.models.NameKind.BUILTIN`.

        Parameters:
            name: The bare Python identifier to check.

        Returns:
            A freshly constructed :class:`NameCoordinate` with
            ``kind=NameKind.BUILTIN``, or ``None`` if *name* is not a builtin.
        """
        if name not in PYTHON_BUILTINS:
            return None
        return NameCoordinate(
            name=name,
            kind=NameKind.BUILTIN,
            scope_key="__builtins__",
            type_repr="builtin",
        )

    # ------------------------------------------------------------------
    # Qualified name resolution
    # ------------------------------------------------------------------

    def resolve_qualified(
        self, qualified_name: str, scopes: list[ScopeSection]
    ) -> NameResolutionResult:
        """Resolve a dotted name (e.g. ``"os.path.join"``) step by step.

        Splits *qualified_name* on ``"."`` and resolves the first component
        through the provided *scopes* list (innermost first).  Subsequent
        components represent attribute accesses and cannot be resolved through
        scope lookup alone; the method returns the coordinate of the first
        successfully resolved component while recording the full qualified path
        in the result's ``resolution_path``.

        Parameters:
            qualified_name: A dotted identifier string, e.g. ``"os.path.join"``.
            scopes: Ordered list of :class:`ScopeSection` objects (innermost
                first) to search for the root component.

        Returns:
            A :class:`NameResolutionResult` for the resolved root component.
            If the root cannot be resolved the result has ``resolved=False``.

        Raises:
            ValueError: If *qualified_name* is empty.
        """
        if not qualified_name:
            raise ValueError("qualified_name must not be empty")

        parts = qualified_name.split(".")
        root = parts[0]
        path: list[str] = []

        root_coord: NameCoordinate | None = None
        for scope in scopes:
            path.append(scope.scope_key)
            root_coord = self.resolve_in_scope(root, scope)
            if root_coord is not None:
                break

        if root_coord is None:
            root_coord = self.resolve_builtin(root)
            if root_coord is None:
                return NameResolutionResult.not_found(
                    qualified_name, tuple(path)
                )
            path.append("__builtins__")

        # Walk remaining attribute parts (best-effort: search all scopes)
        current_coord = root_coord
        for attr_part in parts[1:]:
            found: NameCoordinate | None = None
            for scope in scopes:
                found = self.resolve_in_scope(attr_part, scope)
                if found is not None:
                    current_coord = found
                    break
            # If not found as a scope binding, keep current_coord; attribute
            # access resolution requires type information beyond scope analysis.

        return NameResolutionResult(
            name=qualified_name,
            resolved=True,
            coordinate=current_coord,
            scope_key=current_coord.scope_key,
            resolution_path=tuple(path),
        )

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def build_resolution_graph(
        self, names: list[str], scope_chain: ScopeChain
    ) -> dict[str, NameResolutionResult]:
        """Resolve all *names* and return a name → result mapping.

        Parameters:
            names: List of bare identifiers to resolve.
            scope_chain: The scope chain to resolve against.

        Returns:
            Dictionary mapping each name to its
            :class:`NameResolutionResult`.
        """
        return {name: self.resolve(name, scope_chain) for name in names}

    def detect_resolution_conflicts(
        self, names: list[str], scope_chain: ScopeChain
    ) -> list[str]:
        """Return names that are ambiguous or that shadow a Python builtin.

        A name is considered conflicting if:

        * It appears in more than one scope in *scope_chain* (shadowing), **or**
        * It resolves to a user-defined binding while also being a known Python
          builtin (shadow-of-builtin).

        Parameters:
            names: List of bare identifiers to check.
            scope_chain: The scope chain to inspect.

        Returns:
            Sorted list of conflicting name strings.
        """
        conflicts: list[str] = []
        for name in names:
            result = self.resolve(name, scope_chain)
            if not result.resolved:
                continue

            # Count how many scopes bind this name
            binding_count = sum(
                1
                for scope in scope_chain.scopes
                for binding in scope.bindings
                if binding.name == name
            )
            if binding_count > 1:
                msg = (
                    f"Name '{name}' is bound in {binding_count} scopes "
                    f"(shadowing detected)"
                )
                self._conflict_log.append(msg)
                conflicts.append(name)
                continue

            # Check for shadow-of-builtin
            if (
                name in PYTHON_BUILTINS
                and result.coordinate is not None
                and result.coordinate.kind != NameKind.BUILTIN
            ):
                msg = (
                    f"Name '{name}' shadows Python builtin in scope "
                    f"'{result.scope_key}'"
                )
                self._conflict_log.append(msg)
                conflicts.append(name)

        return sorted(set(conflicts))

    # ------------------------------------------------------------------
    # Judgment construction
    # ------------------------------------------------------------------

    def build_resolution_judgment(
        self, result: NameResolutionResult, coord: CoordinateObject
    ) -> Judgment:
        """Build a :class:`Judgment` encapsulating a resolution outcome.

        Constructs a :attr:`PropositionKind.STRUCTURAL` judgment at *coord*
        whose formula encodes whether resolution succeeded and, if so, the
        scope key and name kind.  An :attr:`EvidenceItemKind.ORACLE_PROPOSAL`
        evidence item is attached carrying the full resolution payload.

        Parameters:
            result: The :class:`NameResolutionResult` to wrap.
            coord: The :class:`CoordinateObject` at which the judgment is
                issued (typically the coordinate of the call site).

        Returns:
            A :class:`Judgment` suitable for ingestion by the JuGeo framework.
        """
        if result.resolved and result.coordinate is not None:
            formula = (
                f"name_resolved('{result.name}', "
                f"scope='{result.coordinate.scope_key}', "
                f"kind='{result.coordinate.kind.value}')"
            )
        else:
            path_repr = list(result.resolution_path)
            formula = (
                f"name_unresolved('{result.name}', path={path_repr})"
            )

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(result.name,),
        )
        carrier = Carrier(name="NameResolutionCarrier")
        bundle = EvidenceBundle()
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "name": result.name,
                "resolved": result.resolved,
                "scope_key": result.scope_key,
                "resolution_path": list(result.resolution_path),
                "module": self.module_name,
                "error_message": result.error_message,
            },
        )
        bundle.add_evidence(item)
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
        )

    # ------------------------------------------------------------------
    # Cache management and statistics
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Evict all memoised resolution results.

        Should be called whenever the set of scopes changes (e.g., after an
        incremental re-analysis step) to avoid stale cache entries.
        """
        self._resolution_cache.clear()
        logger.debug(
            "NameResolutionEngine cache cleared for module '%s'",
            self.module_name,
        )

    def statistics(self) -> dict[str, Any]:
        """Return runtime statistics for this engine instance.

        Returns:
            Dictionary with keys ``call_count``, ``cache_hits``,
            ``cache_size``, and ``conflict_count``.
        """
        return {
            "call_count": self._call_count,
            "cache_hits": self._cache_hits,
            "cache_size": len(self._resolution_cache),
            "conflict_count": len(self._conflict_log),
        }


# ---------------------------------------------------------------------------
# ScopeTreeAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeTreeAlgorithm:
    """Tree algorithms over the scope nesting structure.

    Manages a scope tree — the directed acyclic graph whose nodes are scope
    keys and whose edges represent the parent–child (enclosing–enclosed)
    relationship.  In the sheaf-theoretic framing this corresponds to the
    restriction-morphism poset of the Grothendieck topology on the site of
    Python scopes (theory2.tex Ch15 §2.3).

    Attributes:
        _scope_map: Dictionary mapping scope keys to their
            :class:`ScopeSection` objects, populated by
            :meth:`build_scope_tree`.
        _parent_map: Dictionary mapping each scope key to its parent scope
            key (or ``None`` for root scopes), populated by
            :meth:`build_scope_tree`.
    """

    _scope_map: dict[str, ScopeSection] = field(default_factory=dict)
    _parent_map: dict[str, str | None] = field(default_factory=dict)

    def build_scope_tree(
        self, scopes: list[ScopeSection]
    ) -> dict[str, list[str]]:
        """Construct the parent→children adjacency dictionary.

        Iterates over *scopes*, records each scope in ``_scope_map`` and
        ``_parent_map``, and returns a dictionary mapping every parent scope
        key to an ordered list of its direct children's keys.

        Parameters:
            scopes: All :class:`ScopeSection` objects in the module being
                analysed.

        Returns:
            Dictionary ``{parent_key: [child_key, …]}`` representing the
            scope tree.  Root scopes (``parent_key is None``) are collected
            under the synthetic key ``"<roots>"``.
        """
        tree: dict[str, list[str]] = {}
        for scope in scopes:
            key = scope.scope_key
            self._scope_map[key] = scope
            self._parent_map[key] = scope.parent_key
            if key not in tree:
                tree[key] = []
            parent = scope.parent_key
            if parent is not None:
                if parent not in tree:
                    tree[parent] = []
                tree[parent].append(key)
        return tree

    def dfs_scopes(self, root_key: str) -> list[str]:
        """Return scope keys in depth-first pre-order starting at *root_key*.

        Parameters:
            root_key: The scope key of the traversal root.

        Returns:
            Ordered list of scope keys in DFS pre-order.
        """
        result: list[str] = []
        stack: list[str] = [root_key]
        while stack:
            current = stack.pop()
            result.append(current)
            children = [
                k for k, p in self._parent_map.items() if p == current
            ]
            # Push in reverse order so leftmost child is processed first.
            for child in reversed(sorted(children)):
                stack.append(child)
        return result

    def bfs_scopes(self, root_key: str) -> list[str]:
        """Return scope keys in breadth-first order starting at *root_key*.

        Parameters:
            root_key: The scope key of the traversal root.

        Returns:
            Ordered list of scope keys in BFS level order.
        """
        result: list[str] = []
        queue: deque[str] = deque([root_key])
        while queue:
            current = queue.popleft()
            result.append(current)
            children = sorted(
                k for k, p in self._parent_map.items() if p == current
            )
            queue.extend(children)
        return result

    def lca_scope(self, key1: str, key2: str) -> str | None:
        """Compute the lowest common ancestor of two scopes.

        Finds the deepest scope that is an ancestor of both *key1* and *key2*.
        In the restriction-morphism poset this corresponds to the meet
        (greatest lower bound) of the two open sets.

        Parameters:
            key1: Scope key of the first scope.
            key2: Scope key of the second scope.

        Returns:
            The scope key of the LCA, or ``None`` if the two scopes are in
            disconnected subtrees.
        """
        ancestors1 = set(self._get_ancestors(key1))
        ancestors1.add(key1)
        path2 = self._get_ancestors(key2)
        path2.append(key2)
        for node in reversed(path2):
            if node in ancestors1:
                return node
        return None

    def scope_distance(self, key1: str, key2: str) -> int:
        """Return the number of edges on the path between two scopes.

        Parameters:
            key1: Scope key of the first scope.
            key2: Scope key of the second scope.

        Returns:
            Non-negative integer edge count, or ``-1`` if the scopes are in
            disconnected subtrees.
        """
        lca = self.lca_scope(key1, key2)
        if lca is None:
            return _DISCONNECTED
        path1 = self._get_ancestors(key1)
        path1.append(key1)
        path2 = self._get_ancestors(key2)
        path2.append(key2)
        try:
            dist1 = len(path1) - 1 - path1.index(lca)
            dist2 = len(path2) - 1 - path2.index(lca)
            return dist1 + dist2
        except ValueError:
            return _DISCONNECTED

    def scope_path(self, key1: str, key2: str) -> list[str]:
        """Return the sequence of scope keys forming the path from *key1* to *key2*.

        The path goes upward from *key1* to the LCA, then downward to *key2*.

        Parameters:
            key1: Source scope key.
            key2: Destination scope key.

        Returns:
            Ordered list of scope keys along the path, including both
            endpoints.  Returns an empty list if the scopes are disconnected.
        """
        lca = self.lca_scope(key1, key2)
        if lca is None:
            return []

        # Build upward path from key1 to lca
        up: list[str] = []
        cur = key1
        while cur != lca:
            up.append(cur)
            parent = self._parent_map.get(cur)
            if parent is None:
                break
            cur = parent
        up.append(lca)

        # Build downward path from lca to key2
        down_rev: list[str] = []
        cur = key2
        while cur != lca:
            down_rev.append(cur)
            parent = self._parent_map.get(cur)
            if parent is None:
                break
            cur = parent
        down = list(reversed(down_rev))

        return up + down

    def all_ancestors(self, key: str) -> list[str]:
        """Return the ordered list of ancestor scope keys from root to parent.

        Parameters:
            key: The scope key whose ancestors are desired.

        Returns:
            List of scope keys from the root down to (but not including) *key*.
        """
        return self._get_ancestors(key)

    def all_descendants(self, key: str) -> list[str]:
        """Return all descendant scope keys via DFS (not including *key*).

        Parameters:
            key: The scope key whose subtree is traversed.

        Returns:
            List of descendant scope keys in DFS pre-order.
        """
        result = self.dfs_scopes(key)
        return [k for k in result if k != key]

    def _get_ancestors(self, key: str) -> list[str]:
        """Collect ancestor keys from root down to the immediate parent of *key*.

        Parameters:
            key: The scope key to trace upward from.

        Returns:
            Ordered list of ancestor keys (root first, immediate parent last).
        """
        ancestors: list[str] = []
        current = self._parent_map.get(key)
        while current is not None:
            ancestors.append(current)
            current = self._parent_map.get(current)
        ancestors.reverse()
        return ancestors


# ---------------------------------------------------------------------------
# ClosureAnalysisAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureAnalysisAlgorithm:
    """Algorithms for analysing closure semantics.

    Implements the pullback computation described in theory2.tex Ch15 §4.1:
    the free-variable set of a closure is the fibre product of the function's
    coordinate with the set of names visible in its enclosing scopes, pulled
    back along the restriction morphism.

    Attributes:
        module_name: Dotted module name, used for logging and cache keys.
        _analysis_cache: Memoised free-variable lists keyed by
            ``scope_key``.
        _mutation_warnings: Accumulated human-readable warnings about
            detected mutations through closures.
    """

    module_name: str
    _analysis_cache: dict[str, list[NameCoordinate]] = field(
        default_factory=dict
    )
    _mutation_warnings: list[str] = field(default_factory=list)

    def compute_free_variables_recursive(
        self,
        scope: ScopeSection,
        all_scopes: dict[str, ScopeSection],
    ) -> list[NameCoordinate]:
        """Compute all free variables of *scope*, including from nested closures.

        Collects :class:`NameCoordinate` objects with ``kind`` in
        ``{FREE, NONLOCAL, CLOSURE}`` from *scope* and then recurses into
        all child scopes (those whose ``parent_key`` equals
        ``scope.scope_key``), merging the child free-variable sets while
        deduplicating by name.

        Parameters:
            scope: The :class:`ScopeSection` to analyse.
            all_scopes: Complete mapping from scope key to
                :class:`ScopeSection` for the module.

        Returns:
            Deduplicated list of free :class:`NameCoordinate` objects
            reachable from *scope* or any of its nested closures.
        """
        if scope.scope_key in self._analysis_cache:
            return list(self._analysis_cache[scope.scope_key])

        free_kinds = {NameKind.FREE, NameKind.NONLOCAL, NameKind.CLOSURE}
        free: list[NameCoordinate] = [
            b for b in scope.bindings if b.kind in free_kinds
        ]
        seen_names: set[str] = {nc.name for nc in free}

        for child_scope in all_scopes.values():
            if child_scope.parent_key != scope.scope_key:
                continue
            child_free = self.compute_free_variables_recursive(
                child_scope, all_scopes
            )
            for nc in child_free:
                if nc.name not in seen_names:
                    free.append(nc)
                    seen_names.add(nc.name)

        self._analysis_cache[scope.scope_key] = free
        return list(free)

    def build_capture_graph(
        self, records: list[ClosureRecord]
    ) -> dict[str, list[str]]:
        """Build adjacency map: function_key → list of captured name strings.

        Parameters:
            records: List of :class:`ClosureRecord` objects to graph.

        Returns:
            Dictionary mapping each function's scope key to the sorted list
            of free-variable name strings it captures.
        """
        return {
            record.function_key: sorted(record.all_free_names)
            for record in records
        }

    def detect_mutation_through_closure(
        self,
        record: ClosureRecord,
        assignment_sites: list[str],
    ) -> list[str]:
        """Find free-variable names that are mutated from inside the closure.

        Checks whether any name in ``record.all_free_names`` appears in
        *assignment_sites*, which represents names that are assigned (written)
        somewhere inside the function body.  Such mutations affect the shared
        cell object and may cause surprising behaviour.

        Parameters:
            record: The :class:`ClosureRecord` describing the closure.
            assignment_sites: List of name strings that are assigned within
                the closure body.

        Returns:
            Sorted list of free-variable names that are also assigned.
        """
        free_set = set(record.all_free_names)
        mutated = sorted(
            name for name in assignment_sites if name in free_set
        )
        for name in mutated:
            warning = (
                f"Closure '{record.function_key}' mutates captured "
                f"variable '{name}' (depth={record.depth})"
            )
            self._mutation_warnings.append(warning)
            logger.warning(warning)
        return mutated

    def find_escaping_closures(
        self,
        records: list[ClosureRecord],
        return_sites: dict[str, list[str]],
    ) -> list[ClosureRecord]:
        """Find closures that may outlive their defining enclosing scope.

        A closure *escapes* if the name of its function appears in the
        ``return_sites`` list of any of its enclosing scopes, indicating
        that the closure object is returned (or assigned to a longer-lived
        variable) from a scope that will eventually be popped.

        Parameters:
            records: List of all :class:`ClosureRecord` objects for the module.
            return_sites: Mapping from scope key to a list of name strings
                that are returned from that scope.

        Returns:
            Filtered list of :class:`ClosureRecord` objects that escape.
        """
        escaping: list[ClosureRecord] = []
        for record in records:
            func_name = record.function_key.split("/")[-1]
            for enc_key in record.enclosing_keys:
                returned = return_sites.get(enc_key, [])
                if func_name in returned:
                    escaping.append(record)
                    logger.debug(
                        "Closure '%s' escapes via scope '%s'",
                        record.function_key,
                        enc_key,
                    )
                    break
        return escaping

    def compute_closure_complexity(self, record: ClosureRecord) -> int:
        """Heuristic complexity score for a single closure.

        Computed as ``len(free_variables) * len(enclosing_keys) + 1``, which
        grows with both the number of captured names and the nesting depth.

        Parameters:
            record: The :class:`ClosureRecord` to score.

        Returns:
            Positive integer complexity score (minimum 1).
        """
        cell_count = len(record.all_free_names)
        enc_count = len(record.enclosing_keys)
        return cell_count * enc_count + 1

    def analyze_all(
        self,
        records: list[ClosureRecord],
        all_scopes: dict[str, ScopeSection],
    ) -> dict[str, Any]:
        """Run all closure analyses and return a consolidated summary.

        Runs :meth:`build_capture_graph`, computes complexity for every
        record, and accumulates mutation warnings.

        Parameters:
            records: List of all :class:`ClosureRecord` objects for the module.
            all_scopes: Complete mapping from scope key to
                :class:`ScopeSection`.

        Returns:
            Dictionary with keys ``capture_graph``, ``complexity_scores``,
            ``mutation_warnings``, ``total_free_variables``, and
            ``max_depth``.
        """
        capture_graph = self.build_capture_graph(records)
        complexity_scores = {
            r.function_key: self.compute_closure_complexity(r)
            for r in records
        }
        total_free = sum(len(r.all_free_names) for r in records)
        max_depth = max((r.depth for r in records), default=0)
        return {
            "capture_graph": capture_graph,
            "complexity_scores": complexity_scores,
            "mutation_warnings": list(self._mutation_warnings),
            "total_free_variables": total_free,
            "max_depth": max_depth,
        }


# ---------------------------------------------------------------------------
# ModuleStateDiffAlgorithm
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModuleStateDiffAlgorithm:
    """Diff and patch operations on module namespace snapshots.

    Treats a module's global namespace as a section of the name-binding
    sheaf over the module coordinate.  Diffs represent the difference between
    two consecutive sections (two snapshot epochs), and patches represent
    the transition morphism between them (theory2.tex Ch15 §4.2).

    All methods are pure (no mutable state); the class is a stateless
    algorithm container.
    """

    def diff(
        self,
        snap1: dict[str, str],
        snap2: dict[str, str],
    ) -> dict[str, Any]:
        """Compute the structural diff between two namespace snapshots.

        Parameters:
            snap1: First snapshot: mapping from name string to type
                annotation string.
            snap2: Second snapshot: same format.

        Returns:
            Dictionary with keys:
            * ``"added"`` — names present in *snap2* but not *snap1*
              (``{name: type_str}``).
            * ``"removed"`` — names present in *snap1* but not *snap2*
              (``{name: type_str}``).
            * ``"changed"`` — names present in both but with different
              type strings (``{name: (old_type, new_type)}``).
        """
        added: dict[str, str] = {}
        removed: dict[str, str] = {}
        changed: dict[str, tuple[str, str]] = {}

        for name, type_str in snap2.items():
            if name not in snap1:
                added[name] = type_str
            elif snap1[name] != type_str:
                changed[name] = (snap1[name], type_str)

        for name, type_str in snap1.items():
            if name not in snap2:
                removed[name] = type_str

        return {"added": added, "removed": removed, "changed": changed}

    def patch(
        self,
        state: dict[str, str],
        diff_result: dict[str, Any],
    ) -> dict[str, str]:
        """Apply *diff_result* to *state* and return the patched namespace.

        Parameters:
            state: Current namespace snapshot to patch.
            diff_result: A diff dictionary as produced by :meth:`diff`.

        Returns:
            New namespace snapshot with additions, removals, and changes
            applied.
        """
        added: dict[str, str] = diff_result.get("added", {})
        removed_map: dict[str, str] = diff_result.get("removed", {})
        changed_map: dict[str, tuple[str, str]] = diff_result.get(
            "changed", {}
        )
        new_types = {name: new for name, (_, new) in changed_map.items()}
        return self.apply_patch(
            state,
            added=added,
            removed=set(removed_map.keys()),
            changed=new_types,
        )

    def apply_patch(
        self,
        snap: dict[str, str],
        added: dict[str, str],
        removed: set[str],
        changed: dict[str, str],
    ) -> dict[str, str]:
        """Apply explicit add/remove/change sets to a namespace snapshot.

        Parameters:
            snap: Base namespace snapshot.
            added: Names and types to add.
            removed: Set of name strings to remove.
            changed: Names whose type strings should be updated.

        Returns:
            New namespace snapshot (does not mutate *snap*).
        """
        result = dict(snap)
        for name in removed:
            result.pop(name, None)
        result.update(added)
        result.update(changed)
        return result

    def invert_patch(
        self, diff_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the inverse diff that undoes *diff_result*.

        Swaps ``"added"`` and ``"removed"`` and inverts each ``(old, new)``
        pair in ``"changed"`` to ``(new, old)``.

        Parameters:
            diff_result: A diff dictionary as produced by :meth:`diff`.

        Returns:
            An inverted diff dictionary.
        """
        added = diff_result.get("added", {})
        removed = diff_result.get("removed", {})
        changed: dict[str, tuple[str, str]] = diff_result.get("changed", {})
        inverted_changed = {
            name: (new, old) for name, (old, new) in changed.items()
        }
        return {
            "added": dict(removed),
            "removed": dict(added),
            "changed": inverted_changed,
        }

    def merge_patches(
        self,
        patch1: dict[str, Any],
        patch2: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two diffs into a single combined diff (second wins on conflict).

        Parameters:
            patch1: First diff dictionary.
            patch2: Second diff dictionary (takes precedence on conflicts).

        Returns:
            Combined diff dictionary.
        """
        merged_added = {**patch1.get("added", {}), **patch2.get("added", {})}
        merged_removed = {
            **patch1.get("removed", {}),
            **patch2.get("removed", {}),
        }
        merged_changed = {
            **patch1.get("changed", {}),
            **patch2.get("changed", {}),
        }
        return {
            "added": merged_added,
            "removed": merged_removed,
            "changed": merged_changed,
        }

    def three_way_merge(
        self,
        base: dict[str, str],
        ours: dict[str, str],
        theirs: dict[str, str],
    ) -> tuple[dict[str, str], list[str]]:
        """Three-way merge of two namespace snapshots against a common base.

        Implements the standard three-way merge algorithm: a name is
        non-conflicting if only one side changed it relative to *base*; it
        is conflicting if both sides changed it to different values.  In the
        conflict case *ours* wins by default but the name is recorded.

        Parameters:
            base: The common ancestor namespace snapshot.
            ours: Our modified namespace snapshot.
            theirs: Their modified namespace snapshot.

        Returns:
            A 2-tuple ``(merged, conflicts)`` where *merged* is the merged
            namespace snapshot and *conflicts* is a sorted list of conflicting
            name strings.
        """
        merged: dict[str, str] = {}
        conflicts: list[str] = []
        all_keys = set(base) | set(ours) | set(theirs)

        for key in sorted(all_keys):
            in_base = key in base
            in_ours = key in ours
            in_theirs = key in theirs
            base_val = base.get(key)
            our_val = ours.get(key)
            their_val = theirs.get(key)

            if in_ours and in_theirs:
                if our_val == their_val:
                    merged[key] = our_val  # type: ignore[assignment]
                elif our_val == base_val:
                    merged[key] = their_val  # type: ignore[assignment]
                elif their_val == base_val:
                    merged[key] = our_val  # type: ignore[assignment]
                else:
                    conflicts.append(key)
                    merged[key] = our_val  # type: ignore[assignment]
            elif in_ours:
                if not in_base:
                    merged[key] = our_val  # type: ignore[assignment]
                else:
                    # Deleted in theirs, kept in ours — conflict.
                    conflicts.append(key)
                    merged[key] = our_val  # type: ignore[assignment]
            elif in_theirs:
                if not in_base:
                    merged[key] = their_val  # type: ignore[assignment]
                else:
                    # Deleted in ours, kept in theirs — conflict.
                    conflicts.append(key)
                    merged[key] = their_val  # type: ignore[assignment]
            # else: present only in base and deleted by both sides — omit.

        return merged, sorted(conflicts)


# ---------------------------------------------------------------------------
# ReachabilityAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReachabilityAnalyzer:
    """Computes which names are reachable (visible) from each scope.

    In sheaf terms, a name is *reachable* from scope *S* if there exists a
    restriction morphism from some scope in the covering family of *S* to the
    scope where the name is bound.  For the standard LEGB topology this means
    the name is bound in *S* or any of its ancestors in the scope chain
    (theory2.tex Ch15 §3.3).

    Attributes:
        _reachability_cache: Memoisation mapping ``"<scope_key>:<module_key>"``
            to the set of reachable name strings.
    """

    _reachability_cache: dict[str, set[str]] = field(default_factory=dict)

    def reachable_names(
        self,
        scope: ScopeSection,
        scope_chain: ScopeChain,
    ) -> set[str]:
        """Return all name strings visible from *scope* through *scope_chain*.

        Walks from the position of *scope* in *scope_chain* outward to the
        outermost scope, collecting every binding name encountered.  Names
        bound in inner scopes shadow outer ones (the inner binding is
        recorded first and the outer is ignored).

        Parameters:
            scope: The :class:`ScopeSection` whose visibility is being
                computed.
            scope_chain: The full LEGB scope chain (innermost first).

        Returns:
            Set of name strings visible from *scope*.
        """
        cache_key = f"{scope.scope_key}:{scope_chain.module_key}"
        if cache_key in self._reachability_cache:
            return set(self._reachability_cache[cache_key])

        names: set[str] = set()
        in_chain = False
        for chain_scope in scope_chain.scopes:
            if chain_scope.scope_key == scope.scope_key:
                in_chain = True
            if in_chain:
                for binding in chain_scope.bindings:
                    names.add(binding.name)

        # Always include the scope's own bindings regardless of chain position.
        for binding in scope.bindings:
            names.add(binding.name)

        self._reachability_cache[cache_key] = names
        return names

    def reachable_scopes(
        self,
        scope_key: str,
        tree: dict[str, list[str]],
    ) -> set[str]:
        """Return all scope keys reachable from *scope_key* in the scope tree.

        Performs a BFS over the children adjacency dictionary *tree*,
        starting at *scope_key*.

        Parameters:
            scope_key: The starting scope key.
            tree: Adjacency dict ``{parent_key: [child_key, …]}`` as
                produced by
                :meth:`ScopeTreeAlgorithm.build_scope_tree`.

        Returns:
            Set of all reachable scope keys (including *scope_key* itself).
        """
        visited: set[str] = set()
        queue: deque[str] = deque([scope_key])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for child in tree.get(current, []):
                queue.append(child)
        return visited

    def build_reachability_matrix(
        self,
        scopes: list[ScopeSection],
        scope_chain: ScopeChain,
    ) -> dict[str, set[str]]:
        """Compute the reachability matrix for all scopes in *scopes*.

        Parameters:
            scopes: All :class:`ScopeSection` objects to include.
            scope_chain: The LEGB scope chain used for name visibility.

        Returns:
            Dictionary mapping each scope key to its set of reachable name
            strings.
        """
        return {
            scope.scope_key: self.reachable_names(scope, scope_chain)
            for scope in scopes
        }

    def strongly_connected_components(
        self,
        name_graph: dict[str, list[str]],
    ) -> list[list[str]]:
        """Compute strongly-connected components of a name-reference graph.

        Uses Tarjan's algorithm to find SCCs in the directed graph where
        nodes are name strings and edges represent *name A references name B*
        relationships (e.g. as extracted from AST analysis).

        Parameters:
            name_graph: Adjacency list ``{name: [referenced_name, …]}``.

        Returns:
            List of SCCs, each SCC being a list of name strings.
            Each SCC is returned in reverse topological order (Tarjan order).
        """
        return self._tarjan_scc(name_graph)

    def _tarjan_scc(
        self, graph: dict[str, list[str]]
    ) -> list[list[str]]:
        """Tarjan's SCC algorithm implementation.

        Parameters:
            graph: Directed graph as adjacency list
                ``{node: [neighbour, …]}``.

        Returns:
            List of strongly-connected components (each as a list of node
            strings) in the order they are completed.
        """
        index_counter: list[int] = [0]
        stack: list[str] = []
        lowlinks: dict[str, int] = {}
        index_map: dict[str, int] = {}
        on_stack: dict[str, bool] = {}
        sccs: list[list[str]] = []

        def strongconnect(v: str) -> None:
            index_map[v] = index_counter[0]
            lowlinks[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for w in graph.get(v, []):
                if w not in index_map:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif on_stack.get(w, False):
                    lowlinks[v] = min(lowlinks[v], index_map[w])

            if lowlinks[v] == index_map[v]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        for v in graph:
            if v not in index_map:
                strongconnect(v)

        return sccs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PYTHON_BUILTINS",
    "NameResolutionEngine",
    "ScopeTreeAlgorithm",
    "ClosureAnalysisAlgorithm",
    "ModuleStateDiffAlgorithm",
    "ReachabilityAnalyzer",
]
