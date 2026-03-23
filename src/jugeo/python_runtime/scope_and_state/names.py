"""Section 1 — Names as Coordinates (theory2.tex Ch15 §1).

In the JuGeo formal-semantics framework, a *name* in a Python program is
modelled as a *coordinate* in the semantic site described in theory2.tex Ch15.
A name-lookup operation is a *morphism* from the use-site coordinate to the
binding-site coordinate; the morphism kind (restriction, inclusion, transport)
depends on the relationship between the use scope and the definition scope.

The different name kinds correspond to different coordinate *layers* in the
Grothendieck site that underlies the module:

- ``LOCAL``      — the name is defined in the current scope layer.
- ``PARAMETER``  — the name arrives via the formal-argument morphism.
- ``FREE``       — the name is resolved by restriction to an enclosing layer.
- ``CLOSURE``    — the name is a cell variable captured into a closure object.
- ``GLOBAL``     — the name lives in the module-level stratum.
- ``BUILTIN``    — the name is the terminal object of the site (builtins layer).
- ``NONLOCAL``   — the name is explicitly assigned via the ``nonlocal`` keyword.
- ``IMPORT``     — the name is introduced by an ``import`` statement.

The principal result of Ch15 §1 is that the Python LEGB rules (Local →
Enclosing → Global → Builtin) define a canonical *cover* of the module site,
and name resolution is precisely the computation of the *stalk* at a use-site
point under this cover.

Binding-site resolution is implemented by :class:`BindingSiteResolver`, which
walks a :class:`ScopeChain` and returns a :class:`CoordinateObject` locating
the binding.  A def–use chain is therefore an explicit representation of the
restriction morphism in the site.

This module was developed with **copilot** assistance as part of the JuGeo
Python-runtime formal-semantics layer.

Typical usage::

    from jugeo.python_runtime.scope_and_state.names import (
        NameClassifier, NameRegistry, NameNormalizer, BindingSiteResolver,
        name_to_coordinate, is_valid_identifier,
    )

    classifier = NameClassifier()
    kind = classifier.classify("x", context={"scope_locals": {"x"}})
    # -> NameKind.LOCAL

    registry = NameRegistry(module_name="mymodule")
    registry.register(name_to_coordinate("x", "mymodule", NameKind.LOCAL))

    resolver = BindingSiteResolver()
    binding = resolver.resolve_binding(nc, scope_chain)
"""

from __future__ import annotations

import keyword
import logging
import re
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
    ScopeSection,
    ScopeKind,
)
from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Analysis channel tag — referenced in evidence payloads
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s01-names"

# ---------------------------------------------------------------------------
# Complete Python builtin namespace (as of CPython 3.12)
# ---------------------------------------------------------------------------

_PYTHON_BUILTINS: frozenset[str] = frozenset({
    # Built-in functions
    "abs", "aiter", "all", "anext", "any", "ascii",
    "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex",
    "copyright", "credits", "delattr", "dict", "dir", "divmod",
    "enumerate", "eval", "exec",
    "filter", "float", "format", "frozenset",
    "getattr", "globals",
    "hasattr", "hash", "help", "hex",
    "id", "input", "int", "isinstance", "issubclass", "iter",
    "len", "license", "list", "locals",
    "map", "max", "memoryview", "min",
    "next",
    "object", "oct", "open", "ord",
    "pow", "print", "property",
    "range", "repr", "reversed", "round",
    "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
    "tuple", "type",
    "vars",
    "zip",
    "__build_class__", "__import__", "__loader__",
    "__name__", "__package__", "__spec__",
    # Built-in constants
    "None", "True", "False", "NotImplemented", "Ellipsis", "__debug__",
    # Built-in exception hierarchy
    "ArithmeticError", "AssertionError", "AttributeError",
    "BaseException", "BaseExceptionGroup", "BlockingIOError",
    "BrokenPipeError", "BufferError", "BytesWarning",
    "ChildProcessError", "ConnectionAbortedError", "ConnectionError",
    "ConnectionRefusedError", "ConnectionResetError",
    "DeprecationWarning",
    "EOFError", "EnvironmentError", "Exception", "ExceptionGroup",
    "FileExistsError", "FileNotFoundError", "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError", "ImportError", "ImportWarning", "IndentationError",
    "IndexError", "InterruptedError", "IsADirectoryError",
    "KeyError", "KeyboardInterrupt",
    "LookupError",
    "MemoryError", "ModuleNotFoundError",
    "NameError", "NotADirectoryError", "NotImplementedError",
    "OSError", "OverflowError",
    "PendingDeprecationWarning", "PermissionError", "ProcessLookupError",
    "RecursionError", "ReferenceError", "ResourceWarning",
    "RuntimeError", "RuntimeWarning",
    "StopAsyncIteration", "StopIteration",
    "SyntaxError", "SyntaxWarning",
    "SystemError", "SystemExit",
    "TabError", "TimeoutError", "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError", "UnicodeEncodeError", "UnicodeError",
    "UnicodeTranslateError", "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
})

# Pattern that matches Python dunder names: __foo__ (at least one char between)
_DUNDER_RE: re.Pattern[str] = re.compile(r"^__[a-zA-Z_][a-zA-Z0-9_]*__$")


# ---------------------------------------------------------------------------
# NameClassifier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NameClassifier:
    """Classifies Python identifiers into :class:`~jugeo.python_runtime.scope_and_state.models.NameKind` values.

    A ``NameClassifier`` is the entry point for assigning coordinate *kinds*
    to bare identifier strings.  Classification follows the priority chain
    mandated by Ch15 §1.3:

    1. Builtin names (terminal layer) take highest priority.
    2. Explicitly declared ``nonlocal`` / ``global`` annotations override
       positional evidence.
    3. Formal parameters are classified before local assignments.
    4. Free variables (resolved in enclosing scopes) come next.
    5. Local assignments and imports fill the remainder.
    6. Dunder names (``__foo__``) at module level default to ``GLOBAL``.

    Results are memoised in ``_cache`` to avoid repeated heuristic work.
    Statistics on how many names of each kind have been classified are
    accumulated in ``_stats``.

    Parameters:
        _builtin_names: Set of names that belong to the Python builtins layer.
            Defaults to :data:`_PYTHON_BUILTINS`, but can be overridden for
            testing or for non-CPython runtimes.

    Example::

        clf = NameClassifier()
        assert clf.classify("len") == NameKind.BUILTIN
        assert clf.classify("x", {"scope_locals": {"x"}}) == NameKind.LOCAL
        assert clf.classify("__all__", {"scope": "module"}) == NameKind.GLOBAL
    """

    _builtin_names: frozenset[str] = field(default_factory=lambda: _PYTHON_BUILTINS)
    _cache: dict[str, NameKind] = field(default_factory=dict, init=False)
    _stats: dict[str, int] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        name: str,
        context: dict[str, Any] | None = None,
    ) -> NameKind:
        """Classify a single name, consulting the context when available.

        The ``context`` dictionary may contain any of the following keys;
        missing keys are treated as empty sets / the default string:

        - ``"scope_locals"``  — names locally assigned in the current scope.
        - ``"scope_globals"`` — names explicitly declared ``global``.
        - ``"free_vars"``     — names that are free (resolved in enclosing scope).
        - ``"nonlocals"``     — names explicitly declared ``nonlocal``.
        - ``"params"``        — formal parameter names.
        - ``"imports"``       — names introduced via ``import``.
        - ``"scope"``         — ``"module"`` if classifying at module level.

        Parameters:
            name: The bare Python identifier string.
            context: Optional classification context dict (see above).

        Returns:
            The :class:`NameKind` for *name* in the given context.
        """
        # Build a stable cache key that incorporates the relevant context keys.
        if context is None:
            cache_key = name
        else:
            # Only include non-empty, hashable context entries in the key.
            relevant = {
                k: frozenset(v) if isinstance(v, (set, list, frozenset)) else v
                for k, v in context.items()
                if v
            }
            sorted_items = tuple(
                (k, relevant[k]) for k in sorted(relevant.keys())
            )
            cache_key = f"{name}|{sorted_items}"

        if cache_key in self._cache:
            self._stats["cache_hits"] = self._stats.get("cache_hits", 0) + 1
            return self._cache[cache_key]

        kind: NameKind = self._compute_kind(name, context)

        self._cache[cache_key] = kind
        self._stats[kind.value] = self._stats.get(kind.value, 0) + 1
        log.debug("NameClassifier.classify(%r) -> %s", name, kind.value)
        return kind

    def _compute_kind(
        self,
        name: str,
        context: dict[str, Any] | None,
    ) -> NameKind:
        """Internal classification logic, bypassing the cache.

        Parameters:
            name: The bare identifier.
            context: Optional context dict (see :meth:`classify`).

        Returns:
            The computed :class:`NameKind`.
        """
        # Builtins have the highest priority — they live in the terminal layer.
        if self.is_builtin(name):
            return NameKind.BUILTIN

        if context is None:
            # Without context: dunder names default to GLOBAL (module-level dunder).
            return NameKind.GLOBAL if self.is_dunder(name) else NameKind.LOCAL

        # Extract context sets, converting to proper Python sets.
        nonlocals: set[str] = set(context.get("nonlocals") or set())
        params: set[str] = set(context.get("params") or set())
        free_vars: set[str] = set(context.get("free_vars") or set())
        scope_locals: set[str] = set(context.get("scope_locals") or set())
        scope_globals: set[str] = set(context.get("scope_globals") or set())
        imports: set[str] = set(context.get("imports") or set())
        scope: str = str(context.get("scope", "local"))

        # Explicit declarations take priority over positional evidence.
        if name in nonlocals:
            return NameKind.NONLOCAL
        if name in scope_globals:
            return NameKind.GLOBAL
        # Formal parameters come before local assignments.
        if name in params:
            return NameKind.PARAMETER
        # Free variables are resolved via closure morphisms.
        if name in free_vars:
            return NameKind.FREE
        # Locally assigned names.
        if name in scope_locals:
            return NameKind.LOCAL
        # Import-introduced names.
        if name in imports:
            return NameKind.IMPORT
        # Dunder names at module scope default to GLOBAL.
        if self.is_dunder(name) and scope == "module":
            return NameKind.GLOBAL
        # Default: treat as a local name.
        return NameKind.LOCAL

    def classify_all(
        self,
        names: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, NameKind]:
        """Classify multiple names under a shared context.

        This is more efficient than calling :meth:`classify` repeatedly because
        the same context object is reused for every name, avoiding redundant
        cache-key construction.

        Parameters:
            names: List of bare identifier strings to classify.
            context: Optional shared context dict (see :meth:`classify`).

        Returns:
            A dictionary mapping each name to its :class:`NameKind`.
        """
        result: dict[str, NameKind] = {}
        for name in names:
            result[name] = self.classify(name, context)
        return result

    def batch_classify(
        self,
        names: list[str],
        scope_locals: set[str],
        scope_globals: set[str],
        free_vars: set[str],
    ) -> dict[str, NameKind]:
        """Classify names using explicit, pre-computed scope membership sets.

        This method bypasses context dictionary overhead and is suitable for
        use in performance-critical analysis loops where the scope sets are
        already available as Python ``set`` objects.

        Nonlocal names cannot be inferred from the three sets alone; callers
        that need nonlocal classification should use :meth:`infer_kind` directly.

        Parameters:
            names: List of bare identifiers to classify.
            scope_locals: Names assigned locally in the current scope.
            scope_globals: Names explicitly declared ``global``.
            free_vars: Names that are free in the current scope.

        Returns:
            Dictionary mapping each name to its :class:`NameKind`.
        """
        result: dict[str, NameKind] = {}
        for name in names:
            result[name] = self.infer_kind(
                name,
                scope_locals=scope_locals,
                scope_globals=scope_globals,
                free_vars=free_vars,
                nonlocals=set(),
            )
        return result

    def infer_kind(
        self,
        name: str,
        scope_locals: set[str],
        scope_globals: set[str],
        free_vars: set[str],
        nonlocals: set[str],
    ) -> NameKind:
        """Pure inference function: infer kind from explicit scope membership.

        This function has no side effects and does not touch the cache.  It
        implements the LEGB priority order:

        1. ``nonlocals`` → :attr:`~NameKind.NONLOCAL`
        2. ``scope_globals`` → :attr:`~NameKind.GLOBAL`
        3. ``free_vars`` → :attr:`~NameKind.FREE`
        4. ``scope_locals`` → :attr:`~NameKind.LOCAL`
        5. ``_builtin_names`` → :attr:`~NameKind.BUILTIN`
        6. dunder pattern → :attr:`~NameKind.GLOBAL`
        7. fallback → :attr:`~NameKind.LOCAL`

        Parameters:
            name: The bare identifier.
            scope_locals: Names locally assigned.
            scope_globals: Names explicitly declared ``global``.
            free_vars: Names free in the current scope.
            nonlocals: Names explicitly declared ``nonlocal``.

        Returns:
            The inferred :class:`NameKind`.
        """
        if name in nonlocals:
            return NameKind.NONLOCAL
        if name in scope_globals:
            return NameKind.GLOBAL
        if name in free_vars:
            return NameKind.FREE
        if name in scope_locals:
            return NameKind.LOCAL
        if self.is_builtin(name):
            return NameKind.BUILTIN
        if self.is_dunder(name):
            return NameKind.GLOBAL
        return NameKind.LOCAL

    def is_builtin(self, name: str) -> bool:
        """Return ``True`` if *name* is a Python builtin identifier.

        Membership is tested against :attr:`_builtin_names`, which defaults to
        the complete CPython 3.12 builtins namespace.

        Parameters:
            name: The bare identifier to test.

        Returns:
            ``True`` if *name* is in the builtins layer.
        """
        return name in self._builtin_names

    def is_dunder(self, name: str) -> bool:
        """Return ``True`` if *name* matches the ``__foo__`` dunder pattern.

        A dunder name has the form ``__identifier__``, where the inner part is
        at least one character long and starts with a letter or underscore.
        Single-underscore names (``_``) and names with only one pair of underscores
        on one side do not match.

        Parameters:
            name: The bare identifier to test.

        Returns:
            ``True`` if *name* is a dunder.
        """
        return bool(_DUNDER_RE.match(name))

    def reset_cache(self) -> None:
        """Clear the classification cache, forcing re-evaluation of all names.

        This should be called when the scope context changes significantly and
        cached classifications may no longer be valid.
        """
        count = len(self._cache)
        self._cache.clear()
        log.debug("NameClassifier.reset_cache: cleared %d entries", count)

    def statistics(self) -> dict[str, int]:
        """Return a snapshot of classification statistics.

        The returned dictionary maps each :class:`NameKind` value (as a string)
        and the special key ``"cache_hits"`` to the count of times that outcome
        was reached.

        Returns:
            A shallow copy of the internal stats dict.
        """
        return dict(self._stats)


# ---------------------------------------------------------------------------
# NameRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NameRegistry:
    """Central registry of all known names in a module.

    A ``NameRegistry`` maintains two parallel indices over
    :class:`~jugeo.python_runtime.scope_and_state.models.NameCoordinate` objects:

    - ``_names`` — maps bare name string → coordinate (for O(1) lookup).
    - ``_by_kind`` — maps :class:`NameKind` → list of name strings (for kind
      filtering without a full scan).

    Invariants (checked by :meth:`validate_integrity`):

    1. Every name in ``_by_kind`` is also in ``_names``.
    2. The kind stored in ``_names[n].kind`` matches the key in ``_by_kind``
       that lists *n*.
    3. No name appears twice in any ``_by_kind`` list.
    4. Every name in ``_names`` appears in exactly one ``_by_kind`` list.

    Parameters:
        module_name: The dotted module name this registry belongs to
            (e.g. ``"mypackage.mymodule"``).

    Example::

        reg = NameRegistry(module_name="mymodule")
        reg.register(name_to_coordinate("x", "mymodule", NameKind.LOCAL))
        assert reg.count() == 1
        assert reg.lookup("x") is not None
    """

    module_name: str
    _names: dict[str, NameCoordinate] = field(default_factory=dict, init=False)
    _by_kind: dict[NameKind, list[str]] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, coord: NameCoordinate) -> None:
        """Add *coord* to the registry, updating both internal indices.

        If a coordinate with the same name is already registered, it is
        replaced.  If the new coordinate has a different kind, the old entry
        is removed from the kind index first.

        Parameters:
            coord: The :class:`NameCoordinate` to register.
        """
        prev = self._names.get(coord.name)
        if prev is not None and prev.kind != coord.kind:
            log.warning(
                "NameRegistry[%s]: re-registering %r with kind change %s -> %s",
                self.module_name,
                coord.name,
                prev.kind.value,
                coord.kind.value,
            )
            # Remove from old kind index.
            old_list = self._by_kind.get(prev.kind)
            if old_list is not None and coord.name in old_list:
                old_list.remove(coord.name)

        self._names[coord.name] = coord
        kind_list = self._by_kind.setdefault(coord.kind, [])
        if coord.name not in kind_list:
            kind_list.append(coord.name)
        log.debug(
            "NameRegistry[%s].register: %r as %s",
            self.module_name,
            coord.name,
            coord.kind.value,
        )

    def unregister(self, name: str) -> bool:
        """Remove the coordinate for *name* from both internal indices.

        Parameters:
            name: The bare identifier to remove.

        Returns:
            ``True`` if the name was present and has been removed;
            ``False`` if the name was not in the registry.
        """
        coord = self._names.pop(name, None)
        if coord is None:
            return False
        kind_list = self._by_kind.get(coord.kind)
        if kind_list is not None and name in kind_list:
            kind_list.remove(name)
        log.debug("NameRegistry[%s].unregister: %r", self.module_name, name)
        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> NameCoordinate | None:
        """Return the :class:`NameCoordinate` for *name*, or ``None``.

        Parameters:
            name: The bare identifier to look up.

        Returns:
            The registered coordinate, or ``None`` if *name* is not known.
        """
        return self._names.get(name)

    def all_names(self) -> list[NameCoordinate]:
        """Return all registered coordinates as a list.

        The order is insertion order (CPython dict semantics).

        Returns:
            List of all :class:`NameCoordinate` objects in the registry.
        """
        return list(self._names.values())

    def filter_by_kind(self, kind: NameKind) -> list[NameCoordinate]:
        """Return all coordinates with the given :class:`NameKind`.

        Parameters:
            kind: The kind to filter by.

        Returns:
            A list of :class:`NameCoordinate` objects whose ``kind`` field
            matches *kind*.  May be empty.
        """
        names = self._by_kind.get(kind, [])
        return [self._names[n] for n in names if n in self._names]

    def merge_registry(self, other: NameRegistry) -> NameRegistry:
        """Return a new registry containing the union of both registries.

        In case of conflict (same name, different coordinate), the entry from
        *other* takes precedence.

        Parameters:
            other: The registry whose entries should win on conflict.

        Returns:
            A new :class:`NameRegistry` whose ``module_name`` is taken from
            ``self``, and whose entries are the union with *other* overriding.
        """
        merged = NameRegistry(module_name=self.module_name)
        # Register self first, then other (other wins on conflict).
        for coord in self._names.values():
            merged.register(coord)
        for coord in other._names.values():
            merged.register(coord)
        log.debug(
            "NameRegistry.merge_registry: %d + %d -> %d entries",
            self.count(),
            other.count(),
            merged.count(),
        )
        return merged

    def count(self) -> int:
        """Return the number of names registered.

        Returns:
            The integer count of registered :class:`NameCoordinate` objects.
        """
        return len(self._names)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise the entire registry to a JSON-safe dict.

        Returns:
            Dict with ``module_name`` and ``names`` (list of serialised
            :class:`NameCoordinate` objects).
        """
        return {
            "module_name": self.module_name,
            "names": [coord.serialize() for coord in self._names.values()],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> NameRegistry:
        """Reconstruct a registry from a dict produced by :meth:`serialize`.

        Parameters:
            data: Serialised registry dict.

        Returns:
            A freshly constructed :class:`NameRegistry` with all coordinates
            re-registered.

        Raises:
            KeyError: If required keys are absent.
            ValueError: If any ``NameCoordinate`` cannot be parsed.
        """
        reg = cls(module_name=data["module_name"])
        for entry in data.get("names", []):
            reg.register(NameCoordinate.parse(entry))
        return reg

    # ------------------------------------------------------------------
    # Integrity checking
    # ------------------------------------------------------------------

    def validate_integrity(self) -> list[str]:
        """Check internal invariants and return a list of violation strings.

        An empty list means the registry is fully consistent.  Callers can use
        this in assertion-heavy debugging paths or in test suites.

        Returns:
            List of human-readable violation descriptions.  Empty if the
            registry is well-formed.
        """
        violations: list[str] = []

        # --- Invariant 1: every name in _by_kind is in _names. ---
        for kind, names in self._by_kind.items():
            for name in names:
                if name not in self._names:
                    violations.append(
                        f"Name {name!r} listed in _by_kind[{kind.value!r}] "
                        f"but absent from _names"
                    )
                elif self._names[name].kind != kind:
                    actual_kind = self._names[name].kind
                    violations.append(
                        f"Kind mismatch for {name!r}: _by_kind says "
                        f"{kind.value!r}, _names says {actual_kind.value!r}"
                    )

        # --- Invariant 2: every name in _names appears in _by_kind. ---
        for name, coord in self._names.items():
            kind_list = self._by_kind.get(coord.kind, [])
            if name not in kind_list:
                violations.append(
                    f"Name {name!r} (kind={coord.kind.value!r}) is in _names "
                    f"but absent from _by_kind[{coord.kind.value!r}]"
                )

        # --- Invariant 3: no duplicates within any _by_kind list. ---
        for kind, names in self._by_kind.items():
            seen: set[str] = set()
            for name in names:
                if name in seen:
                    violations.append(
                        f"Duplicate entry {name!r} in _by_kind[{kind.value!r}]"
                    )
                seen.add(name)

        return violations


# ---------------------------------------------------------------------------
# NameNormalizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NameNormalizer:
    """Normalises Python identifiers to their canonical qualified form.

    In the coordinate system of theory2.tex Ch15, names are referenced via
    their *fully-qualified* path ``{module}.{identifier}``.  This class
    provides the bijective normalisation / de-normalisation maps and helper
    utilities for splitting and joining dotted paths.

    Results are cached in ``_canonical_cache`` keyed by
    ``"{module}:{name}"``.

    Example::

        nn = NameNormalizer()
        assert nn.normalize("x", "mymodule") == "mymodule.x"
        assert nn.denormalize("mymodule.x") == ("mymodule", "x")
        assert nn.canonicalize_module("My-Module") == "my_module"
    """

    _canonical_cache: dict[str, str] = field(default_factory=dict, init=False)

    def normalize(self, name: str, module: str) -> str:
        """Return the fully-qualified canonical form ``{module}.{name}``.

        The module component is first canonicalised (lowercased, hyphens
        replaced with underscores) before joining.

        Parameters:
            name: The bare identifier (e.g. ``"my_function"``).
            module: The dotted module path (e.g. ``"mypackage.mymodule"``).

        Returns:
            The canonical form ``"{canon_module}.{name}"``
            (e.g. ``"mypackage.mymodule.my_function"``).
        """
        cache_key = f"{module}:{name}"
        if cache_key not in self._canonical_cache:
            canon_mod = self.canonicalize_module(module)
            self._canonical_cache[cache_key] = f"{canon_mod}.{name}"
        return self._canonical_cache[cache_key]

    def denormalize(self, canonical: str) -> tuple[str, str]:
        """Split a canonical name back into ``(module, name)`` components.

        The split is performed at the *last* dot, so deeply-qualified names
        like ``"a.b.c.d"`` are split into ``("a.b.c", "d")``.

        Parameters:
            canonical: A canonical name string (e.g. ``"mypackage.mymodule.x"``).

        Returns:
            A tuple ``(module_part, identifier_part)``.  If there is no dot,
            returns ``("", canonical)``.
        """
        last_dot = canonical.rfind(".")
        if last_dot == -1:
            return ("", canonical)
        return (canonical[:last_dot], canonical[last_dot + 1:])

    def canonicalize_module(self, module: str) -> str:
        """Convert a module path to its canonical lower-snake-case form.

        Transformations applied:

        1. Lowercase the entire string.
        2. Replace hyphens ``-`` with underscores ``_``.
        3. Replace spaces with underscores.
        4. Strip leading / trailing whitespace.

        Parameters:
            module: A dotted module path, possibly in non-canonical form.

        Returns:
            The canonicalised module path.
        """
        return module.strip().lower().replace("-", "_").replace(" ", "_")

    def split_qualified(self, name: str) -> list[str]:
        """Split a dotted name into its component parts.

        Parameters:
            name: A dotted name such as ``"a.b.c"``.

        Returns:
            A list of string parts: ``["a", "b", "c"]``.  Returns a
            single-element list if there are no dots.
        """
        return name.split(".")

    def join_qualified(self, parts: list[str]) -> str:
        """Join a list of name parts into a dotted qualified name.

        Parameters:
            parts: List of string parts, e.g. ``["a", "b", "c"]``.

        Returns:
            The joined name ``"a.b.c"``.  Returns an empty string for an
            empty list.
        """
        return ".".join(parts)

    def is_canonical(self, name: str) -> bool:
        """Return ``True`` if *name* is already in canonical qualified form.

        A name is canonical if:

        - It contains at least one dot.
        - The final component is a valid Python identifier.
        - All module-path components are valid lowercase-underscore identifiers.

        Parameters:
            name: The name to check.

        Returns:
            ``True`` iff *name* is in canonical ``{module}.{identifier}`` form.
        """
        parts = name.split(".")
        if len(parts) < 2:
            return False
        # The last part must be a valid identifier.
        if not parts[-1].isidentifier():
            return False
        # All module-path components must be lowercase identifiers.
        for part in parts[:-1]:
            # Allow digits and underscores but require valid identifier chars.
            if not part.replace("_", "a").isidentifier():
                return False
            if part != part.lower():
                return False
        return True


# ---------------------------------------------------------------------------
# BindingSiteResolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BindingSiteResolver:
    """Resolves each name coordinate to its binding-site coordinate object.

    In the sheaf model of theory2.tex Ch15 §1.4, a *binding-site* is the
    coordinate in the site at which a name is introduced (defined or imported).
    The ``BindingSiteResolver`` walks a :class:`ScopeChain` to locate that
    coordinate for each use-site name.

    Resolved coordinates are cached in ``_resolved`` (keyed by bare name).
    Names that could not be resolved are recorded in ``_unresolved`` for
    diagnostic purposes.

    The :meth:`compute_def_use_chains` method computes a first-approximation
    def–use chain by finding all scopes in which each name appears, then
    linking them as a directed graph via :meth:`build_name_graph`.

    The :meth:`verify_resolution_consistency` method uses a :class:`Z3Session`
    (when available) to check that resolution results are mutually consistent —
    specifically, that no name is simultaneously classified as both
    ``LOCAL`` and ``FREE``.
    """

    _resolved: dict[str, CoordinateObject] = field(default_factory=dict, init=False)
    _unresolved: list[str] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------
    # Core resolution
    # ------------------------------------------------------------------

    def resolve_binding(
        self,
        coord: NameCoordinate,
        scope_chain: ScopeChain,
    ) -> CoordinateObject | None:
        """Find the binding-site coordinate for *coord* in *scope_chain*.

        Walks the scope chain (innermost scope first) until a scope that
        directly binds ``coord.name`` is found, then constructs a
        :class:`CoordinateObject` whose components are derived from that
        scope's ``scope_key``.

        Parameters:
            coord: The :class:`NameCoordinate` whose binding site is sought.
            scope_chain: The active :class:`ScopeChain` to search.

        Returns:
            A :class:`CoordinateObject` for the binding scope, or ``None``
            if the name is not bound anywhere in the chain.
        """
        result = scope_chain.resolve(coord.name)
        if not result.resolved or result.scope_key is None:
            if coord.name not in self._unresolved:
                self._unresolved.append(coord.name)
            log.debug(
                "BindingSiteResolver: could not resolve %r (path=%s)",
                coord.name,
                result.resolution_path,
            )
            return None

        binding_coord = _scope_key_to_coordinate_object(
            result.scope_key,
            result.coordinate.kind if result.coordinate else NameKind.LOCAL,
        )
        self._resolved[coord.name] = binding_coord
        log.debug(
            "BindingSiteResolver: resolved %r -> %s", coord.name, binding_coord.key
        )
        return binding_coord

    def resolve_all(
        self,
        coords: list[NameCoordinate],
        scope_chain: ScopeChain,
    ) -> dict[str, CoordinateObject]:
        """Batch-resolve a list of name coordinates.

        Parameters:
            coords: List of :class:`NameCoordinate` objects to resolve.
            scope_chain: The active :class:`ScopeChain` to search.

        Returns:
            Dictionary mapping each successfully resolved bare name to its
            binding-site :class:`CoordinateObject`.  Unresolved names are
            absent from the dict (and recorded in :attr:`_unresolved`).
        """
        result_map: dict[str, CoordinateObject] = {}
        for coord in coords:
            binding = self.resolve_binding(coord, scope_chain)
            if binding is not None:
                result_map[coord.name] = binding
        log.debug(
            "BindingSiteResolver.resolve_all: %d/%d resolved",
            len(result_map),
            len(coords),
        )
        return result_map

    def find_binding_coordinate(
        self,
        name: str,
        scopes: list[ScopeSection],
    ) -> CoordinateObject | None:
        """Search a flat list of scopes for the binding site of *name*.

        The list is searched from index 0 onward; callers should order it
        innermost-first to match LEGB semantics.

        Parameters:
            name: The bare identifier to locate.
            scopes: Ordered list of :class:`ScopeSection` objects (innermost
                first).

        Returns:
            A :class:`CoordinateObject` for the first scope that binds *name*,
            or ``None`` if none do.
        """
        for scope in scopes:
            for binding in scope.bindings:
                if binding.name == name:
                    return _scope_key_to_coordinate_object(
                        scope.scope_key, binding.kind
                    )
        return None

    def compute_def_use_chains(
        self,
        registry: NameRegistry,
        scopes: list[ScopeSection],
    ) -> dict[str, list[str]]:
        """Build a def→uses map for all names in *registry*.

        For each name in the registry, this method collects the scope keys of
        every scope that directly binds that name.  The result is a first
        approximation of the def–use chain in the sheaf sense: the definition
        site is the scope with the ``LOCAL``/``PARAMETER`` binding, and the
        use sites are all scopes that reference the name as ``FREE`` or
        ``NONLOCAL``.

        The method also tracks a :class:`~jugeo.geometry.supports.SupportSet`
        for each name — the set of scope keys across which the name has support.

        Parameters:
            registry: The :class:`NameRegistry` providing the universe of names.
            scopes: All :class:`ScopeSection` objects in the module.

        Returns:
            Dictionary mapping bare name strings to lists of scope keys where
            that name appears.
        """
        chains: dict[str, list[str]] = {}
        all_coords = registry.all_names()

        for coord in all_coords:
            name = coord.name
            defining_scopes: list[str] = []
            using_scopes: list[str] = []

            for scope in scopes:
                for binding in scope.bindings:
                    if binding.name == name:
                        if binding.kind in (
                            NameKind.LOCAL,
                            NameKind.PARAMETER,
                            NameKind.GLOBAL,
                            NameKind.IMPORT,
                        ):
                            defining_scopes.append(scope.scope_key)
                        elif binding.kind in (
                            NameKind.FREE,
                            NameKind.NONLOCAL,
                            NameKind.CLOSURE,
                        ):
                            using_scopes.append(scope.scope_key)

            # Merge definition and use sites; definitions first.
            all_sites = defining_scopes + using_scopes
            if all_sites:
                chains[name] = all_sites

            # Build the support set for this name (for sheaf-theoretic tracking).
            support = SupportSet(coordinates=frozenset(all_sites))
            log.debug(
                "compute_def_use_chains: %r defined in %d scopes, used in %d scopes",
                name,
                len(defining_scopes),
                len(using_scopes),
            )

        return chains

    def build_name_graph(
        self,
        registry: NameRegistry,
    ) -> dict[str, list[str]]:
        """Build an adjacency list representing name→name reference edges.

        Each entry ``graph[name]`` is the list of names that *name* references
        (directly or transitively via free-variable capture).  This graph can
        be used for cycle detection, reachability analysis, and closure
        boundary detection.

        The graph is built from previously cached resolution results
        (populated by :meth:`resolve_binding` calls).  Names whose bindings
        have not yet been resolved produce empty adjacency lists.

        Parameters:
            registry: The :class:`NameRegistry` from which to enumerate names.

        Returns:
            Adjacency dict mapping each bare name to a list of names it
            references.
        """
        graph: dict[str, list[str]] = {}
        all_coords = registry.all_names()

        for coord in all_coords:
            refs: list[str] = []

            if coord.kind in (NameKind.FREE, NameKind.NONLOCAL, NameKind.CLOSURE):
                # Free/nonlocal/closure names reference their binding-site name.
                binding_coord = self._resolved.get(coord.name)
                if binding_coord is not None:
                    # The binding site's name is the last component of the key.
                    refs.append(binding_coord.name)

            elif coord.kind == NameKind.IMPORT:
                # Imported names reference the root module component.
                parts = coord.name.split(".")
                if len(parts) > 1:
                    refs.append(parts[0])

            graph[coord.name] = refs

        return graph

    def verify_resolution_consistency(
        self,
        results: list[NameResolutionResult],
        session: Z3Session | None = None,
    ) -> bool:
        """Check that resolution results are mutually consistent.

        A set of resolution results is *consistent* if no name is classified
        simultaneously as both ``LOCAL`` and ``FREE`` (which would indicate a
        contradiction in the scope analysis).  When Z3 is available and a
        ``session`` is provided, the check is encoded as a satisfiability
        query; otherwise a simple Python set-intersection heuristic is used.

        Parameters:
            results: List of :class:`NameResolutionResult` objects to validate.
            session: Optional :class:`Z3Session` to use for solver-based
                verification.  If ``None`` or if Z3 is unavailable, falls back
                to heuristic checking.

        Returns:
            ``True`` if no consistency violation was detected;
            ``False`` if a contradiction was found.
        """
        resolved = [r for r in results if r.resolved and r.coordinate is not None]

        # Build a set of (name, kind) pairs.
        name_kinds: dict[str, list[str]] = {}
        for r in resolved:
            assert r.coordinate is not None  # narrowing
            name_kinds.setdefault(r.name, []).append(r.coordinate.kind.value)

        # Heuristic: flag names that appear with contradictory kinds.
        contradictions: list[str] = []
        incompatible_pairs = {
            (NameKind.LOCAL.value, NameKind.FREE.value),
            (NameKind.FREE.value, NameKind.LOCAL.value),
            (NameKind.LOCAL.value, NameKind.NONLOCAL.value),
            (NameKind.NONLOCAL.value, NameKind.LOCAL.value),
            (NameKind.GLOBAL.value, NameKind.LOCAL.value),
            (NameKind.LOCAL.value, NameKind.GLOBAL.value),
        }
        for name, kinds in name_kinds.items():
            kind_set = set(kinds)
            for k1, k2 in incompatible_pairs:
                if k1 in kind_set and k2 in kind_set:
                    contradictions.append(
                        f"Name {name!r} has contradictory kinds: {k1!r} and {k2!r}"
                    )

        if contradictions:
            for msg in contradictions:
                log.warning("verify_resolution_consistency: %s", msg)
            return False

        # Solver-based check when Z3 is available.
        if z3_available() and session is not None:
            for name, kinds in name_kinds.items():
                # Assert that each name has exactly one kind (encoded as boolean).
                unique_formula = Z3Formula.boolean(f"unique_kind_{name}")
                session.assert_formula(unique_formula)
            outcome = session.check_sat()
            if outcome == SolveOutcome.UNSAT:
                log.warning(
                    "verify_resolution_consistency: Z3 found UNSAT for %d names",
                    len(name_kinds),
                )
                return False
            log.debug(
                "verify_resolution_consistency: Z3 outcome=%s", outcome.value
            )

        return True

    def unresolved_names(self) -> list[str]:
        """Return all names that could not be resolved.

        Returns:
            A copy of the internal ``_unresolved`` list.
        """
        return list(self._unresolved)

    def resolved_count(self) -> int:
        """Return the number of successfully resolved names.

        Returns:
            Integer count of entries in the resolution cache.
        """
        return len(self._resolved)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def name_to_coordinate(
    name: str,
    module: str,
    kind: NameKind,
    depth: int = 0,
) -> NameCoordinate:
    """Construct a :class:`NameCoordinate` from constituent parts.

    Derives the ``scope_key`` from *module* and *depth*: the module is
    converted from dotted notation to slash-separated notation, and if
    *depth* is greater than zero, a ``"/d{depth}"`` suffix is appended to
    indicate nesting.

    Parameters:
        name: The bare Python identifier.
        module: The dotted module path (e.g. ``"mypackage.mymodule"``).
        kind: The :class:`NameKind` of this name.
        depth: Lexical nesting depth (0 = module level).

    Returns:
        A new :class:`NameCoordinate` with the derived ``scope_key``.

    Example::

        nc = name_to_coordinate("x", "mymodule", NameKind.LOCAL, depth=1)
        assert nc.scope_key == "mymodule/d1"
    """
    scope_key = module.replace(".", "/")
    if depth > 0:
        scope_key = f"{scope_key}/d{depth}"
    return NameCoordinate(
        name=name,
        kind=kind,
        scope_key=scope_key,
        metadata={"source_module": module, "depth": depth},
    )


def coordinate_to_name(coord: CoordinateObject) -> str:
    """Extract the leaf name from a :class:`CoordinateObject`.

    Returns the ``coord.name`` property, which is the dot-joined version of
    ``coord.components``.  For coordinates with a single component, this is
    just the component string itself.

    Parameters:
        coord: A geometry-layer :class:`CoordinateObject`.

    Returns:
        The ``coord.name`` string (dot-joined components).
    """
    return coord.name


def is_valid_identifier(name: str) -> bool:
    """Return ``True`` if *name* is a syntactically valid Python identifier.

    A valid identifier must:

    1. Be a non-empty string.
    2. Pass ``str.isidentifier()`` — i.e. match the Python identifier grammar.
    3. Not be a reserved keyword (``keyword.iskeyword``).
    4. Not be a soft keyword that should be avoided (``keyword.issoftkeyword``
       in Python 3.12+, ignored if not available).

    Parameters:
        name: The string to validate.

    Returns:
        ``True`` if *name* is a valid non-keyword identifier.

    Example::

        assert is_valid_identifier("my_var")
        assert not is_valid_identifier("for")
        assert not is_valid_identifier("123bad")
    """
    if not name or not name.isidentifier():
        return False
    if keyword.iskeyword(name):
        return False
    return True


def split_dotted_name(name: str) -> tuple[str, str]:
    """Split a dotted name into ``(module_part, identifier_part)``.

    The split is performed at the *last* dot.  If the name contains no dot,
    the module part is an empty string.

    Parameters:
        name: A potentially dotted name, e.g. ``"mymodule.my_function"``.

    Returns:
        A two-tuple ``(module_part, identifier_part)``.

    Example::

        assert split_dotted_name("a.b.c") == ("a.b", "c")
        assert split_dotted_name("standalone") == ("", "standalone")
    """
    last_dot = name.rfind(".")
    if last_dot == -1:
        return ("", name)
    return (name[:last_dot], name[last_dot + 1:])


def build_name_judgment(
    coord: NameCoordinate,
    module_coordinate: CoordinateObject,
) -> Judgment:
    """Construct a :class:`~jugeo.judgments.judgment_terms.Judgment` for a name binding.

    The judgment asserts that *coord* is a well-formed binding in the scope
    identified by ``coord.scope_key``.  The evidence item carries the
    ``copilot-s01-names`` analysis channel tag.

    Parameters:
        coord: The :class:`NameCoordinate` to build a judgment for.
        module_coordinate: The :class:`CoordinateObject` of the enclosing module.

    Returns:
        A :class:`~jugeo.judgments.judgment_terms.Judgment` at ``module_coordinate``
        with a ``STRUCTURAL`` proposition and ``ORACLE_PROPOSAL`` evidence.
    """
    prop = Proposition(
        kind=PropositionKind.STRUCTURAL,
        formula=f"name_binding({coord.name!r}, {coord.kind.value!r}, {coord.scope_key!r})",
        free_variables=("name", "kind", "scope_key"),
    )
    carrier = Carrier(name="NameCarrier")
    evidence = EvidenceBundle(
        items=(
            EvidenceItem(
                kind=EvidenceItemKind.ORACLE_PROPOSAL,
                payload={
                    "name": coord.name,
                    "kind": coord.kind.value,
                    "scope_key": coord.scope_key,
                    "channel": _ANALYSIS_CHANNEL,
                },
                channel=_ANALYSIS_CHANNEL,
            ),
        )
    )
    trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
    return Judgment(
        coordinate=module_coordinate,
        proposition=prop,
        carrier=carrier,
        evidence=evidence,
        trust=trust,
    )


def _scope_key_to_coordinate_object(
    scope_key: str,
    name_kind: NameKind,
) -> CoordinateObject:
    """Convert a slash-separated scope key to a :class:`CoordinateObject`.

    Parameters:
        scope_key: A slash-separated scope path, e.g. ``"mypkg/mymod/myfunc"``.
        name_kind: The :class:`NameKind` of the name being resolved; used to
            select the appropriate :class:`CoordinateKind`.

    Returns:
        A :class:`CoordinateObject` whose components are the path segments of
        *scope_key*.
    """
    components = tuple(scope_key.split("/")) if scope_key else ("_unknown",)
    if len(components) == 1:
        ck = CoordinateKind.MODULE
    elif name_kind in (NameKind.LOCAL, NameKind.PARAMETER, NameKind.FREE,
                       NameKind.NONLOCAL, NameKind.CLOSURE):
        ck = CoordinateKind.FUNCTION
    else:
        ck = CoordinateKind.MODULE
    return CoordinateObject(components=components, kind=ck)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "NameClassifier",
    "NameRegistry",
    "NameNormalizer",
    "BindingSiteResolver",
    # Helpers
    "name_to_coordinate",
    "coordinate_to_name",
    "is_valid_identifier",
    "split_dotted_name",
    "build_name_judgment",
    # Constants
    "_PYTHON_BUILTINS",
    "_ANALYSIS_CHANNEL",
]
