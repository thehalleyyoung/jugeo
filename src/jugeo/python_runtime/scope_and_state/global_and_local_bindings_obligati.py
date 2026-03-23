from __future__ import annotations

"""Section 2 — Global and Local Bindings: Obligation Ownership and Module State (theory2.tex Ch15).

In the JuGeo formal-semantics framework, every binding in a Python program
carries an *ownership obligation*.  The ownership obligation describes which
scope is responsible for coordinating access to the bound name and, where
multiple scopes can see the same name, what coordination protocol governs
reads and writes.

Theory2.tex Ch15 distinguishes three principal obligation classes:

- **Local obligation** — the name is owned exclusively by the current frame.
  No coordination with other frames is required; the binding lives and dies
  with the frame.  This corresponds to names in ``co_varnames`` that are
  *not* in ``co_cellvars``.

- **Global obligation** — the name is owned by the module-level section.
  Any write creates a *shared mutable state* coordination obligation: all
  call sites that reference the module must behave as if they see the same
  current value.  The ``global`` keyword is the syntactic manifestation of
  the programmer declaring this obligation explicitly; CPython enforces it
  via the ``STORE_GLOBAL`` / ``LOAD_GLOBAL`` bytecodes.

- **Cell transport obligation** — the ``nonlocal`` keyword (and the implicit
  cell mechanism for closures) creates an obligation to transport the current
  binding value through a shared *cell object*.  The cell is a mutable
  indirection container; all scopes that share the cell see each other's
  writes.  Theory2.tex Ch15 models this as a *transport morphism* in the
  scope sheaf.

The three classes in this module implement the analysis, coordination, and
witnessing of these obligation kinds at different levels of abstraction:

- :class:`GlobalLocalBindingsObligationCoordinator` — mutable registry
  tracking ownership obligations for individual names.
- :class:`GlobalLocalBindingsObligationAnalyzer` — AST-based and live-object
  analysis that extracts obligation profiles from source code and live
  functions/modules.
- :class:`GlobalLocalBindingsObligationWitness` — runtime evidence collector
  that witnesses actual binding ownership and refutes incorrect claims.

This module was developed with **copilot** assistance as part of the JuGeo
Python-runtime formal-semantics layer.

Typical usage::

    from jugeo.python_runtime.scope_and_state.global_and_local_bindings_obligati import (
        GlobalLocalBindingsObligationAnalyzer,
        GlobalLocalBindingsObligationCoordinator,
        GlobalLocalBindingsObligationWitness,
    )

    coord = GlobalLocalBindingsObligationCoordinator()
    coord.register_global_binding("counter", 0, "mymodule")
    coord.register_local_binding("tmp", "mymodule.compute")
    obligations = coord.obligations_for_name("counter")

    analyzer = GlobalLocalBindingsObligationAnalyzer()
    result = analyzer.analyze_source(open("mymodule.py").read(), "mymodule")

    witness = GlobalLocalBindingsObligationWitness()
    import mymodule
    evidence = witness.witness_module_state(mymodule)
"""

import ast
import dis
import inspect
import logging
import types
import uuid
import time
import re
import textwrap
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Jugeo imports — all inside try/except so the module degrades gracefully
# when the full jugeo package is not installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateKind,
        MorphismKind,
        CoordinateObject,
        Site,
        SiteBuilder,
    )
except ImportError:  # pragma: no cover
    import enum

    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        MODULE = "module"
        FUNCTION = "function"
        CLASS = "class"
        CELL = "cell"
        BUILTIN = "builtin"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        IDENTITY = "identity"

    @dataclass(frozen=True)
    class CoordinateObject:  # type: ignore[no-redef]
        components: tuple[str, ...]
        kind: CoordinateKind = CoordinateKind.MODULE

    @dataclass
    class Site:  # type: ignore[no-redef]
        name: str = ""

    @dataclass
    class SiteBuilder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import (
        TrustLevel,
        JudgmentStatus,
        PropositionKind,
        EvidenceItemKind,
        Proposition,
        Carrier,
        EvidenceItem,
        EvidenceBundle,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        Judgment,
    )
except ImportError:  # pragma: no cover
    import enum

    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        UNTRUSTED = 0
        LOW = 1
        MEDIUM = 2
        HIGH = 3
        VERIFIED = 4

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        PENDING = "pending"
        ACCEPTED = "accepted"
        REJECTED = "rejected"
        PARTIAL = "partial"

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        OWNERSHIP = "ownership"
        OBLIGATION = "obligation"
        INVARIANT = "invariant"
        REACHABILITY = "reachability"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        STATIC = "static"
        DYNAMIC = "dynamic"
        WITNESS = "witness"
        REFUTATION = "refutation"

    @dataclass(frozen=True)
    class Proposition:  # type: ignore[no-redef]
        text: str
        kind: PropositionKind = PropositionKind.OBLIGATION

    @dataclass(frozen=True)
    class Carrier:  # type: ignore[no-redef]
        value: Any
        label: str = ""

    @dataclass(frozen=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: EvidenceItemKind
        payload: Any
        label: str = ""

    @dataclass(frozen=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[EvidenceItem, ...] = ()
        label: str = ""

    @dataclass(frozen=True)
    class ResidualObligation:  # type: ignore[no-redef]
        description: str
        kind: str = "unknown"

    @dataclass(frozen=True)
    class Obstruction:  # type: ignore[no-redef]
        reason: str

    @dataclass(frozen=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: TrustLevel = TrustLevel.MEDIUM
        note: str = ""

    @dataclass(frozen=True)
    class Provenance:  # type: ignore[no-redef]
        source: str = ""
        channel: str = ""

    @dataclass
    class Judgment:  # type: ignore[no-redef]
        proposition: Proposition
        status: JudgmentStatus = JudgmentStatus.PENDING
        evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
        trust: TrustAnnotation = field(default_factory=TrustAnnotation)

try:
    from jugeo.python_runtime.scope_and_state.models import (
        NameKind,
        ScopeKind,
        NameCoordinate,
        ScopeChain,
        ScopeSection,
        BindingMap,
        NameResolutionResult,
    )
except ImportError:  # pragma: no cover
    import enum

    class NameKind(enum.Enum):  # type: ignore[no-redef]
        LOCAL = "local"
        PARAMETER = "parameter"
        FREE = "free"
        CLOSURE = "closure"
        GLOBAL = "global"
        BUILTIN = "builtin"
        NONLOCAL = "nonlocal"
        IMPORT = "import"

    class ScopeKind(enum.Enum):  # type: ignore[no-redef]
        MODULE = "module"
        FUNCTION = "function"
        CLASS = "class"
        COMPREHENSION = "comprehension"
        LAMBDA = "lambda"

    @dataclass(frozen=True)
    class NameCoordinate:  # type: ignore[no-redef]
        name: str
        scope_path: str
        kind: NameKind = NameKind.LOCAL

    @dataclass
    class ScopeChain:  # type: ignore[no-redef]
        scopes: list[Any] = field(default_factory=list)

    @dataclass
    class ScopeSection:  # type: ignore[no-redef]
        scope_key: str = ""
        kind: ScopeKind = ScopeKind.FUNCTION
        bindings: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class BindingMap:  # type: ignore[no-redef]
        data: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class NameResolutionResult:  # type: ignore[no-redef]
        name: str
        found: bool
        scope_key: str = ""
        kind: NameKind = NameKind.LOCAL


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s02-global-local-bindings-obligation"
_GLOBAL_SENTINEL: str = "__jugeo_global__"
_LOCAL_SENTINEL: str = "__jugeo_local__"
_OBLIGATION_KINDS: frozenset[str] = frozenset({
    "exclusive_write",
    "shared_read",
    "atomic_update",
    "initialization",
    "deletion",
    "import_binding",
    "augmented_assignment",
})


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def is_mutable_value(value: Any) -> bool:
    """Return ``True`` if *value* is a mutable object that creates coordination obligations.

    Mutable global state is the primary source of coordination obligations in
    theory2.tex Ch15.  Immutable values (int, str, float, bytes, tuple,
    frozenset, NoneType, bool) do not require coordination because no writer
    can modify them in place; mutable containers (list, dict, set) and
    arbitrary class instances do require coordination.

    Parameters:
        value: Any Python object to test.

    Returns:
        ``True`` if *value* is a list, dict, set, bytearray, or an instance
        of a user-defined class; ``False`` for builtin immutables.

    Examples:
        >>> is_mutable_value([1, 2, 3])
        True
        >>> is_mutable_value((1, 2, 3))
        False
        >>> is_mutable_value(42)
        False
        >>> is_mutable_value({"a": 1})
        True
    """
    # Builtin immutable types — no coordination obligation.
    _IMMUTABLE_TYPES = (
        int, float, complex, str, bytes, bool, type(None),
        tuple, frozenset,
    )
    if isinstance(value, _IMMUTABLE_TYPES):
        return False
    # Builtin mutable containers — definite coordination obligation.
    if isinstance(value, (list, dict, set, bytearray)):
        return True
    # Functions, classes, modules are technically mutable (attributes can be
    # added) but are not normally treated as shared mutable state for the
    # purposes of binding obligation analysis.
    if isinstance(value, (types.FunctionType, types.ModuleType, type)):
        return False
    # Everything else (user-defined class instances, etc.) is mutable.
    return True


def global_names_from_code(code: types.CodeType) -> frozenset[str]:
    """Return the set of names in *code* that are global references.

    Uses ``co_names`` which contains all names referenced by ``LOAD_NAME``,
    ``STORE_NAME``, ``LOAD_GLOBAL``, and ``STORE_GLOBAL`` instructions.  Names
    that appear in ``co_varnames`` are locals (not globals); we exclude those.

    Parameters:
        code: A CPython code object (e.g. from ``func.__code__``).

    Returns:
        A frozenset of bare identifier strings that are loaded or stored as
        global names in this code object.

    Examples:
        >>> def f():
        ...     global x
        ...     x = 1
        >>> global_names_from_code(f.__code__)
        frozenset({'x'})
    """
    locals_set = frozenset(code.co_varnames)
    cells_set = frozenset(code.co_cellvars) | frozenset(code.co_freevars)
    # co_names includes globals, builtins, and attribute names; filter to
    # names that are NOT locals or cells.
    candidates = frozenset(code.co_names)
    # Anything that is a local or cell variable is not a module global.
    return candidates - locals_set - cells_set


def local_names_from_code(code: types.CodeType) -> frozenset[str]:
    """Return all local variable names from *code*.

    Parameters:
        code: A CPython code object.

    Returns:
        A frozenset of names from ``co_varnames`` (all local variables
        including parameters).
    """
    return frozenset(code.co_varnames)


def cell_names_from_code(code: types.CodeType) -> frozenset[str]:
    """Return all cell variable names from *code*.

    Cell variables are locals that are shared with at least one inner
    (closure) function.  They create a *cell transport obligation* because
    writes inside the outer function are visible inside the inner function
    through the shared cell object.

    Parameters:
        code: A CPython code object.

    Returns:
        A frozenset of names from ``co_cellvars``.
    """
    return frozenset(code.co_cellvars)


def free_names_from_code(code: types.CodeType) -> frozenset[str]:
    """Return all free variable names from *code*.

    Free variables are names that are resolved from an enclosing scope via the
    closure mechanism (``LOAD_DEREF`` / ``STORE_DEREF`` instructions).

    Parameters:
        code: A CPython code object.

    Returns:
        A frozenset of names from ``co_freevars``.
    """
    return frozenset(code.co_freevars)


def binding_kind_from_opcode(opname: str) -> str:
    """Map a CPython dis opcode name to a JuGeo binding-kind string.

    The opcode reveals the *mechanism* by which a binding is created or
    accessed, which determines the obligation kind in theory2.tex Ch15.

    Parameters:
        opname: A string opcode name as returned by ``dis`` (e.g.
            ``"STORE_FAST"``, ``"STORE_GLOBAL"``).

    Returns:
        One of the strings in :data:`_OBLIGATION_KINDS`, or ``"unknown"``
        if the opcode does not correspond to a recognised binding operation.

    Examples:
        >>> binding_kind_from_opcode("STORE_GLOBAL")
        'exclusive_write'
        >>> binding_kind_from_opcode("IMPORT_NAME")
        'import_binding'
    """
    _OPCODE_MAP: dict[str, str] = {
        "STORE_FAST": "exclusive_write",
        "STORE_DEREF": "exclusive_write",
        "STORE_GLOBAL": "exclusive_write",
        "STORE_NAME": "exclusive_write",
        "LOAD_FAST": "shared_read",
        "LOAD_DEREF": "shared_read",
        "LOAD_GLOBAL": "shared_read",
        "LOAD_NAME": "shared_read",
        "IMPORT_NAME": "import_binding",
        "IMPORT_FROM": "import_binding",
        "IMPORT_STAR": "import_binding",
        "INPLACE_ADD": "augmented_assignment",
        "INPLACE_SUBTRACT": "augmented_assignment",
        "INPLACE_MULTIPLY": "augmented_assignment",
        "INPLACE_DIVIDE": "augmented_assignment",
        "INPLACE_TRUE_DIVIDE": "augmented_assignment",
        "INPLACE_FLOOR_DIVIDE": "augmented_assignment",
        "INPLACE_MODULO": "augmented_assignment",
        "INPLACE_POWER": "augmented_assignment",
        "INPLACE_LSHIFT": "augmented_assignment",
        "INPLACE_RSHIFT": "augmented_assignment",
        "INPLACE_AND": "augmented_assignment",
        "INPLACE_OR": "augmented_assignment",
        "INPLACE_XOR": "augmented_assignment",
        "DELETE_FAST": "deletion",
        "DELETE_DEREF": "deletion",
        "DELETE_GLOBAL": "deletion",
        "DELETE_NAME": "deletion",
    }
    return _OPCODE_MAP.get(opname, "unknown")


def obligation_priority(obligation_kind: str) -> int:
    """Return an integer priority for sorting obligations.

    Higher numbers indicate higher priority (more urgent / more dangerous).
    Shared mutable global state writes are the highest priority because they
    affect all call sites; pure reads and deletions are lower.

    Parameters:
        obligation_kind: One of the strings in :data:`_OBLIGATION_KINDS`.

    Returns:
        An integer in the range [0, 100].  Unknown kinds return 0.

    Examples:
        >>> obligation_priority("atomic_update")
        90
        >>> obligation_priority("shared_read")
        10
    """
    _PRIORITY_MAP: dict[str, int] = {
        "atomic_update": 90,
        "exclusive_write": 80,
        "augmented_assignment": 75,
        "initialization": 60,
        "import_binding": 50,
        "deletion": 40,
        "shared_read": 10,
    }
    return _PRIORITY_MAP.get(obligation_kind, 0)


# ---------------------------------------------------------------------------
# GlobalLocalBindingsObligationCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GlobalLocalBindingsObligationCoordinator:
    """Mutable registry tracking binding ownership obligations for individual names.

    The coordinator maintains three parallel data structures:

    - ``_module_state`` — a dict simulating the module's global namespace.
      Writing to this dict is the coordinated action that creates obligations.
    - ``_obligation_registry`` — a per-name list of obligation records.  Each
      record is a plain dict with keys ``kind``, ``scope_path``, ``timestamp``,
      ``priority``, and ``metadata``.
    - ``_ownership_map`` — a per-name string describing the current owner:
      ``"local"``, ``"global"``, ``"cell"``, or ``"imported"``.

    The coordinator is intentionally *not* thread-safe; concurrency control is
    the responsibility of the caller (consistent with CPython's GIL assumption
    in theory2.tex Ch15).

    Parameters (dataclass fields):
        _module_state: Initial simulated global state (default empty dict).
        _obligation_registry: Initial obligation registry (default empty dict).
        _ownership_map: Initial ownership map (default empty dict).
        _coordinator_id: Unique hex id (auto-generated via uuid4).
    """

    _module_state: dict[str, Any] = field(default_factory=dict)
    _obligation_registry: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict
    )
    _ownership_map: dict[str, str] = field(default_factory=dict)
    _coordinator_id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:16]
    )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def register_global_binding(
        self,
        name: str,
        value: Any,
        source_scope: str,
    ) -> dict[str, Any]:
        """Record a global binding and create the associated coordination obligation.

        A global binding is shared mutable state: any function that uses the
        ``global`` keyword to write to *name* must coordinate with all other
        readers.  This method writes the value into ``_module_state``,
        records the obligation, and marks ownership as ``"global"``.

        Parameters:
            name: The bare identifier being bound.
            value: The current value of the binding.
            source_scope: The dotted scope path of the scope that performs
                the binding (e.g. ``"mymodule"`` or ``"mymodule.my_func"``).

        Returns:
            The obligation record dict with keys:
            ``name``, ``kind``, ``scope_path``, ``ownership``,
            ``timestamp``, ``priority``, ``is_mutable``, ``value_type``,
            ``coordinator_id``.

        Examples:
            >>> coord = GlobalLocalBindingsObligationCoordinator()
            >>> rec = coord.register_global_binding("counter", 0, "mymodule")
            >>> rec["kind"]
            'exclusive_write'
            >>> rec["ownership"]
            'global'
        """
        # Write to simulated module state — this is the "action" being obligated.
        self._module_state[name] = value

        # Determine the obligation kind: if the name already has an
        # obligation (i.e. is being re-assigned), this is an atomic_update.
        existing = self._obligation_registry.get(name, [])
        if existing:
            kind = "atomic_update"
        else:
            kind = "initialization" if name not in self._module_state else "exclusive_write"

        # Build the obligation record.
        record: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "scope_path": source_scope,
            "ownership": "global",
            "timestamp": time.monotonic(),
            "priority": obligation_priority(kind),
            "is_mutable": is_mutable_value(value),
            "value_type": type(value).__name__,
            "coordinator_id": self._coordinator_id,
            "channel": _ANALYSIS_CHANNEL,
        }

        # Register the obligation.
        if name not in self._obligation_registry:
            self._obligation_registry[name] = []
        self._obligation_registry[name].append(record)

        # Update ownership map.
        self._ownership_map[name] = "global"

        log.debug(
            "Coordinator %s: global binding %r from %s (kind=%s, mutable=%s)",
            self._coordinator_id,
            name,
            source_scope,
            kind,
            record["is_mutable"],
        )
        return record

    def register_local_binding(
        self,
        name: str,
        scope_path: str,
    ) -> dict[str, Any]:
        """Record a local binding with exclusive frame ownership.

        Local bindings do not require coordination with other scopes; they are
        owned exclusively by the frame described by *scope_path*.  This method
        records the obligation but does NOT write to ``_module_state``.

        Parameters:
            name: The bare identifier being bound.
            scope_path: The dotted scope path (e.g.
                ``"mymodule.my_func"``).

        Returns:
            The obligation record dict.

        Examples:
            >>> coord = GlobalLocalBindingsObligationCoordinator()
            >>> rec = coord.register_local_binding("tmp", "mymodule.compute")
            >>> rec["ownership"]
            'local'
        """
        kind = "exclusive_write"
        record: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "scope_path": scope_path,
            "ownership": "local",
            "timestamp": time.monotonic(),
            "priority": obligation_priority(kind),
            "is_mutable": False,  # Unknown at registration time for locals.
            "value_type": "unknown",
            "coordinator_id": self._coordinator_id,
            "channel": _ANALYSIS_CHANNEL,
        }
        if name not in self._obligation_registry:
            self._obligation_registry[name] = []
        self._obligation_registry[name].append(record)
        self._ownership_map[name] = "local"
        log.debug(
            "Coordinator %s: local binding %r in %s",
            self._coordinator_id,
            name,
            scope_path,
        )
        return record

    def register_nonlocal_binding(
        self,
        name: str,
        declaring_scope: str,
        owning_scope: str,
    ) -> dict[str, Any]:
        """Record a nonlocal cell transport obligation.

        The ``nonlocal`` keyword creates a cell-transport obligation: the cell
        object is shared between *owning_scope* (the outer function that has
        the name as a cell variable) and *declaring_scope* (the inner function
        that declares it ``nonlocal``).

        Parameters:
            name: The bare identifier declared nonlocal.
            declaring_scope: The inner scope that uses ``nonlocal name``.
            owning_scope: The enclosing scope that originally binds *name*.

        Returns:
            The obligation record dict.
        """
        kind = "exclusive_write"
        record: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "scope_path": declaring_scope,
            "owning_scope": owning_scope,
            "ownership": "cell",
            "transport_kind": "nonlocal_cell",
            "timestamp": time.monotonic(),
            "priority": obligation_priority(kind) + 5,  # Slightly higher than plain local.
            "is_mutable": True,  # Cell variables are always mutable.
            "value_type": "cell",
            "coordinator_id": self._coordinator_id,
            "channel": _ANALYSIS_CHANNEL,
        }
        if name not in self._obligation_registry:
            self._obligation_registry[name] = []
        self._obligation_registry[name].append(record)
        self._ownership_map[name] = "cell"
        log.debug(
            "Coordinator %s: nonlocal binding %r (declaring=%s, owning=%s)",
            self._coordinator_id,
            name,
            declaring_scope,
            owning_scope,
        )
        return record

    def compute_ownership(
        self,
        name: str,
        code: types.CodeType,
    ) -> str:
        """Determine the ownership kind for *name* in *code*.

        Inspects the code object's ``co_varnames``, ``co_cellvars``,
        ``co_freevars``, and ``co_names`` arrays to classify the name.

        Parameters:
            name: The bare identifier to classify.
            code: The CPython code object to inspect.

        Returns:
            One of ``"local"``, ``"cell"``, ``"free"``, or ``"global"``.
            Returns ``"global"`` if the name is not found in any of the
            local/cell/free arrays (it may be a global or builtin reference).

        Examples:
            >>> def outer():
            ...     x = 1
            ...     def inner():
            ...         return x
            ...     return inner
            >>> coord = GlobalLocalBindingsObligationCoordinator()
            >>> coord.compute_ownership("x", outer.__code__)
            'cell'
        """
        if name in code.co_cellvars:
            return "cell"
        if name in code.co_freevars:
            return "free"
        if name in code.co_varnames:
            return "local"
        # If not in any of the above, treat as global (or builtin reference).
        return "global"

    def transfer_ownership(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
        reason: str,
    ) -> None:
        """Transfer binding ownership of *name* from *from_scope* to *to_scope*.

        Records a transfer obligation event in the registry.  This models the
        semantics of the ``global`` keyword (transferring a name from local
        frame ownership to module ownership) or of closure capture (transferring
        from plain-local to cell ownership).

        Parameters:
            name: The bare identifier whose ownership is being transferred.
            from_scope: The dotted scope path that previously owned the binding.
            to_scope: The dotted scope path that will take ownership.
            reason: A human-readable reason string (e.g. ``"global_declaration"``
                or ``"closure_capture"``).

        Returns:
            ``None``.
        """
        transfer_record: dict[str, Any] = {
            "name": name,
            "kind": "atomic_update",
            "scope_path": to_scope,
            "from_scope": from_scope,
            "reason": reason,
            "ownership": self._ownership_map.get(name, "unknown"),
            "timestamp": time.monotonic(),
            "priority": obligation_priority("atomic_update"),
            "is_mutable": True,
            "value_type": "transfer_event",
            "coordinator_id": self._coordinator_id,
            "channel": _ANALYSIS_CHANNEL,
        }
        # Update ownership to the destination scope kind.
        if "global" in to_scope or reason == "global_declaration":
            self._ownership_map[name] = "global"
        elif reason in ("closure_capture", "cell_capture"):
            self._ownership_map[name] = "cell"
        else:
            self._ownership_map[name] = "local"

        if name not in self._obligation_registry:
            self._obligation_registry[name] = []
        self._obligation_registry[name].append(transfer_record)
        log.debug(
            "Coordinator %s: transferred ownership of %r: %s → %s (%s)",
            self._coordinator_id,
            name,
            from_scope,
            to_scope,
            reason,
        )

    def obligations_for_name(self, name: str) -> list[dict[str, Any]]:
        """Return all recorded obligations for *name*, sorted by priority descending.

        Parameters:
            name: The bare identifier to query.

        Returns:
            A list of obligation record dicts, sorted by ``priority`` from
            highest to lowest.  Returns an empty list if *name* has no
            registered obligations.
        """
        obligations = self._obligation_registry.get(name, [])
        return sorted(obligations, key=lambda r: r.get("priority", 0), reverse=True)

    def module_state_snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of the simulated module state.

        Returns:
            A dict mapping each name in ``_module_state`` to a metadata record
            with keys ``value_repr``, ``value_type``, ``is_mutable``,
            ``ownership``, ``obligation_count``, ``top_obligation_kind``.
        """
        snapshot: dict[str, Any] = {}
        for name, value in self._module_state.items():
            obligations = self.obligations_for_name(name)
            top_kind = obligations[0]["kind"] if obligations else "none"
            snapshot[name] = {
                "value_repr": repr(value)[:120],
                "value_type": type(value).__name__,
                "is_mutable": is_mutable_value(value),
                "ownership": self._ownership_map.get(name, "unknown"),
                "obligation_count": len(obligations),
                "top_obligation_kind": top_kind,
            }
        return snapshot

    def check_write_conflict(self, name: str, scope_path: str) -> bool:
        """Check if writing to *name* from *scope_path* conflicts with existing obligations.

        A conflict occurs when:
        - *name* is owned as ``"global"`` and *scope_path* is a function scope
          that has not declared the name global (undeclared global write).
        - *name* is owned as ``"cell"`` and *scope_path* is not in the closure
          chain for the cell.
        - There are existing ``"atomic_update"`` obligations from a different
          scope_path (concurrent-write scenario).

        Parameters:
            name: The bare identifier to check.
            scope_path: The dotted scope path attempting the write.

        Returns:
            ``True`` if a write conflict is detected, ``False`` otherwise.
        """
        current_ownership = self._ownership_map.get(name, "unknown")
        existing_obligations = self._obligation_registry.get(name, [])

        if current_ownership == "global":
            # Any function-scope write to a global without re-registering is a conflict.
            if "." in scope_path and scope_path != name.split(".")[0]:
                # Function scope attempting to write a module global directly.
                log.warning(
                    "Coordinator %s: write conflict detected for %r from %s (global owned)",
                    self._coordinator_id,
                    name,
                    scope_path,
                )
                return True

        if current_ownership == "cell":
            # Cell writes from scopes not in the original ownership chain are conflicts.
            owning_scopes = {
                rec.get("owning_scope", "") for rec in existing_obligations
                if rec.get("ownership") == "cell"
            }
            declaring_scopes = {
                rec.get("scope_path", "") for rec in existing_obligations
                if rec.get("ownership") == "cell"
            }
            all_allowed = owning_scopes | declaring_scopes
            if scope_path not in all_allowed and all_allowed:
                log.warning(
                    "Coordinator %s: write conflict for %r from %s (cell ownership)",
                    self._coordinator_id,
                    name,
                    scope_path,
                )
                return True

        # Check for concurrent atomic-update obligations from different scopes.
        atomic_scopes = {
            rec["scope_path"] for rec in existing_obligations
            if rec.get("kind") == "atomic_update" and rec["scope_path"] != scope_path
        }
        if atomic_scopes:
            log.warning(
                "Coordinator %s: concurrent write conflict for %r (other scopes: %s)",
                self._coordinator_id,
                name,
                atomic_scopes,
            )
            return True

        return False


# ---------------------------------------------------------------------------
# GlobalLocalBindingsObligationAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GlobalLocalBindingsObligationAnalyzer:
    """AST-based and live-object analyzer that extracts obligation profiles.

    The analyzer operates at two levels:

    1. **Static AST analysis** — parses Python source code, walks the AST to
       find ``global`` and ``nonlocal`` declarations, classifies every
       assignment node, and returns an obligation profile without executing
       the code.

    2. **Live object analysis** — inspects live :class:`types.ModuleType` and
       :class:`types.FunctionType` objects using ``inspect`` and ``dis`` to
       observe the actual binding structure at runtime.

    Parameters (dataclass fields):
        _coordinator: The obligation coordinator (auto-instantiated).
        _ast_cache: Cache of parsed AST trees keyed by source hash.
        _global_decls: Maps function name → list of names declared global.
        _nonlocal_decls: Maps function name → list of names declared nonlocal.
        _stats: Defaultdict of integer counters for analysis statistics.
    """

    _coordinator: GlobalLocalBindingsObligationCoordinator = field(
        default_factory=GlobalLocalBindingsObligationCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _global_decls: dict[str, list[str]] = field(default_factory=dict)
    _nonlocal_decls: dict[str, list[str]] = field(default_factory=dict)
    _stats: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def analyze_source(
        self,
        source: str,
        module_name: str = "<module>",
    ) -> dict[str, Any]:
        """Perform full AST obligation analysis of Python source code.

        Parses *source*, collects all ``global`` and ``nonlocal`` declarations,
        finds all assignment nodes, classifies each binding, and registers
        obligations with the coordinator.

        Parameters:
            source: Python source code as a string.
            module_name: The module name to use as the root scope path
                (default ``"<module>"``).

        Returns:
            A dict with keys:
            ``module_name``, ``global_declarations``, ``nonlocal_declarations``,
            ``assignments``, ``obligations``, ``stats``, ``source_hash``.

        Raises:
            SyntaxError: If *source* cannot be parsed.
        """
        # Compute source hash for caching.
        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
        self._stats["analyze_source_calls"] += 1

        # Parse and cache.
        if source_hash not in self._ast_cache:
            tree = ast.parse(source, filename=module_name)
            self._ast_cache[source_hash] = tree
            self._stats["ast_parses"] += 1
        else:
            tree = self._ast_cache[source_hash]
            self._stats["ast_cache_hits"] += 1

        # Collect declarations.
        global_decls = self.find_global_declarations(tree)
        nonlocal_decls = self.find_nonlocal_declarations(tree)
        self._global_decls.update(global_decls)
        self._nonlocal_decls.update(nonlocal_decls)

        # Find all assignment nodes.
        assignments = self.find_all_assignments(tree)
        self._stats["assignments_found"] += len(assignments)

        # Classify each assignment and register obligations.
        obligation_records: list[dict[str, Any]] = []
        for assignment in assignments:
            name = assignment.get("name", "")
            scope_path = assignment.get("scope_path", module_name)
            kind = assignment.get("assignment_kind", "unknown")
            obligation_kind = self.classify_binding_obligation(
                name, scope_path, kind, global_decls, nonlocal_decls
            )
            # Register with coordinator.
            if obligation_kind == "global_obligation":
                rec = self._coordinator.register_global_binding(name, None, scope_path)
            elif obligation_kind == "cell_obligation":
                owning = self._find_owning_scope(name, scope_path, nonlocal_decls)
                rec = self._coordinator.register_nonlocal_binding(name, scope_path, owning)
            elif obligation_kind == "import_obligation":
                rec = self._coordinator.register_global_binding(name, None, scope_path)
                rec["kind"] = "import_binding"
            else:
                rec = self._coordinator.register_local_binding(name, scope_path)
            rec["obligation_kind"] = obligation_kind
            obligation_records.append(rec)

        self._stats["obligations_registered"] += len(obligation_records)

        return {
            "module_name": module_name,
            "source_hash": source_hash,
            "global_declarations": global_decls,
            "nonlocal_declarations": nonlocal_decls,
            "assignments": assignments,
            "obligations": obligation_records,
            "stats": dict(self._stats),
            "channel": _ANALYSIS_CHANNEL,
        }

    def _find_owning_scope(
        self,
        name: str,
        declaring_scope: str,
        nonlocal_decls: dict[str, list[str]],
    ) -> str:
        """Return the scope path that owns *name* for a nonlocal declaration.

        Walks up the dotted scope path, looking for a scope that does NOT
        declare *name* as nonlocal (i.e. it is the original owner).

        Parameters:
            name: The bare identifier.
            declaring_scope: The scope that declares ``nonlocal name``.
            nonlocal_decls: The nonlocal declarations map.

        Returns:
            The owning scope path string, or ``"<module>"`` if not found.
        """
        parts = declaring_scope.split(".")
        # Walk from outermost to innermost, stopping at declaring scope.
        for i in range(1, len(parts)):
            candidate = ".".join(parts[:i])
            nonlocals_here = nonlocal_decls.get(parts[i - 1], []) if i > 0 else []
            if name not in nonlocals_here:
                return candidate
        return "<module>"

    def find_global_declarations(self, tree: ast.AST) -> dict[str, list[str]]:
        """Walk *tree* and collect all ``global`` declarations.

        Parameters:
            tree: A parsed AST (from ``ast.parse``).

        Returns:
            A dict mapping each enclosing function name (or ``"<module>"`` for
            module-level) to the list of names it declares global.

        Examples:
            >>> import ast
            >>> src = "def f():\\n    global x, y\\n    x = 1\\n"
            >>> analyzer = GlobalLocalBindingsObligationAnalyzer()
            >>> analyzer.find_global_declarations(ast.parse(src))
            {'f': ['x', 'y']}
        """
        result: dict[str, list[str]] = {}

        class _Visitor(ast.NodeVisitor):
            _scope_stack: list[str]

            def __init__(self) -> None:
                self._scope_stack = ["<module>"]

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scope_stack.append(node.name)
                self.generic_visit(node)
                self._scope_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Global(self, node: ast.Global) -> None:
                scope = self._scope_stack[-1]
                if scope not in result:
                    result[scope] = []
                result[scope].extend(node.names)
                self.generic_visit(node)

        _Visitor().visit(tree)
        self._stats["global_decls_found"] += sum(len(v) for v in result.values())
        return result

    def find_nonlocal_declarations(self, tree: ast.AST) -> dict[str, list[str]]:
        """Walk *tree* and collect all ``nonlocal`` declarations.

        Parameters:
            tree: A parsed AST.

        Returns:
            A dict mapping each enclosing function name to the list of names
            it declares nonlocal.
        """
        result: dict[str, list[str]] = {}

        class _Visitor(ast.NodeVisitor):
            _scope_stack: list[str]

            def __init__(self) -> None:
                self._scope_stack = ["<module>"]

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scope_stack.append(node.name)
                self.generic_visit(node)
                self._scope_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
                scope = self._scope_stack[-1]
                if scope not in result:
                    result[scope] = []
                result[scope].extend(node.names)
                self.generic_visit(node)

        _Visitor().visit(tree)
        self._stats["nonlocal_decls_found"] += sum(len(v) for v in result.values())
        return result

    def find_all_assignments(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Walk *tree* and return a record for every binding-creating node.

        Covers: ``Assign``, ``AugAssign``, ``AnnAssign``, ``For`` target,
        ``With`` target, ``Import``, ``ImportFrom``, ``FunctionDef`` name,
        ``AsyncFunctionDef`` name, ``ClassDef`` name, and comprehension
        targets.

        Parameters:
            tree: A parsed AST.

        Returns:
            A list of dicts, each with keys:
            ``name``, ``scope_path``, ``assignment_kind``, ``lineno``.
        """
        records: list[dict[str, Any]] = []

        def _extract_names(target: ast.expr) -> list[str]:
            """Recursively extract bare names from an assignment target."""
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Tuple, ast.List)):
                names: list[str] = []
                for elt in target.elts:
                    names.extend(_extract_names(elt))
                return names
            if isinstance(target, ast.Starred):
                return _extract_names(target.value)
            return []

        class _Visitor(ast.NodeVisitor):
            _scope_stack: list[str]

            def __init__(self) -> None:
                self._scope_stack = ["<module>"]

            @property
            def _scope(self) -> str:
                return ".".join(self._scope_stack)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                records.append({
                    "name": node.name,
                    "scope_path": self._scope,
                    "assignment_kind": "function_def",
                    "lineno": node.lineno,
                })
                self._scope_stack.append(node.name)
                self.generic_visit(node)
                self._scope_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                records.append({
                    "name": node.name,
                    "scope_path": self._scope,
                    "assignment_kind": "class_def",
                    "lineno": node.lineno,
                })
                self._scope_stack.append(node.name)
                self.generic_visit(node)
                self._scope_stack.pop()

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    for name in _extract_names(target):
                        records.append({
                            "name": name,
                            "scope_path": self._scope,
                            "assignment_kind": "assign",
                            "lineno": node.lineno,
                        })
                self.generic_visit(node)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                for name in _extract_names(node.target):
                    records.append({
                        "name": name,
                        "scope_path": self._scope,
                        "assignment_kind": "augmented_assignment",
                        "lineno": node.lineno,
                    })
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    for name in _extract_names(node.target):
                        records.append({
                            "name": name,
                            "scope_path": self._scope,
                            "assignment_kind": "annotated_assign",
                            "lineno": node.lineno,
                        })
                self.generic_visit(node)

            def visit_For(self, node: ast.For) -> None:
                for name in _extract_names(node.target):
                    records.append({
                        "name": name,
                        "scope_path": self._scope,
                        "assignment_kind": "for_target",
                        "lineno": node.lineno,
                    })
                self.generic_visit(node)

            def visit_With(self, node: ast.With) -> None:
                for item in node.items:
                    if item.optional_vars is not None:
                        for name in _extract_names(item.optional_vars):
                            records.append({
                                "name": name,
                                "scope_path": self._scope,
                                "assignment_kind": "with_target",
                                "lineno": node.lineno,
                            })
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    bound_name = alias.asname if alias.asname else alias.name.split(".")[0]
                    records.append({
                        "name": bound_name,
                        "scope_path": self._scope,
                        "assignment_kind": "import",
                        "lineno": node.lineno,
                    })
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                for alias in node.names:
                    bound_name = alias.asname if alias.asname else alias.name
                    if bound_name != "*":
                        records.append({
                            "name": bound_name,
                            "scope_path": self._scope,
                            "assignment_kind": "import_from",
                            "lineno": node.lineno,
                        })
                self.generic_visit(node)

        _Visitor().visit(tree)
        return records

    def classify_binding_obligation(
        self,
        name: str,
        scope_path: str,
        assignment_kind: str,
        global_decls: dict[str, list[str]],
        nonlocal_decls: dict[str, list[str]],
    ) -> str:
        """Determine the obligation class for a single binding.

        Rules (in precedence order):
        1. If *assignment_kind* is ``"import"`` or ``"import_from"`` → ``"import_obligation"``.
        2. If *name* is in the global declarations for the enclosing function → ``"global_obligation"``.
        3. If *name* is in the nonlocal declarations for the enclosing function → ``"cell_obligation"``.
        4. If *scope_path* is the module scope (no dots) → ``"global_obligation"``.
        5. Otherwise → ``"local_obligation"``.

        Parameters:
            name: The bare identifier.
            scope_path: The dotted scope path.
            assignment_kind: The kind string from :meth:`find_all_assignments`.
            global_decls: From :meth:`find_global_declarations`.
            nonlocal_decls: From :meth:`find_nonlocal_declarations`.

        Returns:
            One of ``"local_obligation"``, ``"global_obligation"``,
            ``"cell_obligation"``, ``"import_obligation"``.
        """
        if assignment_kind in ("import", "import_from"):
            return "import_obligation"

        # The immediate enclosing function is the last component of scope_path.
        scope_parts = scope_path.split(".")
        immediate_scope = scope_parts[-1] if scope_parts else "<module>"

        if name in global_decls.get(immediate_scope, []):
            return "global_obligation"

        if name in nonlocal_decls.get(immediate_scope, []):
            return "cell_obligation"

        # Module-level scope: dots indicate nesting; no dot means module scope.
        if "." not in scope_path or scope_path == "<module>":
            return "global_obligation"

        return "local_obligation"

    def analyze_live_module(self, module: types.ModuleType) -> dict[str, Any]:
        """Analyze a live module object's global state.

        Uses ``inspect.getmembers(module)`` and ``module.__dict__`` to
        categorize public vs private names, mutable vs immutable values, and
        identifies coordination obligations for mutable globals.

        Parameters:
            module: A live Python module object.

        Returns:
            A dict with keys:
            ``module_name``, ``public_names``, ``private_names``,
            ``mutable_names``, ``immutable_names``, ``obligation_count``,
            ``obligations``, ``module_dict_size``.
        """
        module_name = getattr(module, "__name__", repr(module))
        module_dict = module.__dict__

        public_names: list[str] = []
        private_names: list[str] = []
        mutable_names: list[str] = []
        immutable_names: list[str] = []
        obligations: list[dict[str, Any]] = []

        for name, value in module_dict.items():
            # Skip dunder attributes (module metadata).
            if name.startswith("__") and name.endswith("__"):
                continue
            # Private vs public.
            if name.startswith("_"):
                private_names.append(name)
            else:
                public_names.append(name)
            # Mutable vs immutable.
            if is_mutable_value(value):
                mutable_names.append(name)
                rec = self._coordinator.register_global_binding(name, value, module_name)
                obligations.append(rec)
            else:
                immutable_names.append(name)

        self._stats["live_module_analyses"] += 1

        return {
            "module_name": module_name,
            "public_names": sorted(public_names),
            "private_names": sorted(private_names),
            "mutable_names": sorted(mutable_names),
            "immutable_names": sorted(immutable_names),
            "obligation_count": len(obligations),
            "obligations": obligations,
            "module_dict_size": len(module_dict),
            "channel": _ANALYSIS_CHANNEL,
        }

    def analyze_function_bindings(self, func: types.FunctionType) -> dict[str, Any]:
        """Inspect a live function's code object and build its obligation profile.

        Uses the code object attributes ``co_varnames``, ``co_cellvars``,
        ``co_freevars``, and ``co_names`` to classify every name referenced by
        *func*.

        Parameters:
            func: A live Python function object.

        Returns:
            A dict with keys:
            ``function_name``, ``local_names``, ``cell_names``, ``free_names``,
            ``global_names``, ``obligations``, ``bytecode_obligations``.
        """
        code = func.__code__
        func_name = func.__qualname__

        local_names = list(local_names_from_code(code))
        cell_names = list(cell_names_from_code(code))
        free_names = list(free_names_from_code(code))
        global_refs = list(global_names_from_code(code))

        obligations: list[dict[str, Any]] = []

        # Register local obligations.
        for name in local_names:
            if name not in cell_names:
                rec = self._coordinator.register_local_binding(name, func_name)
                obligations.append(rec)

        # Register cell obligations.
        for name in cell_names:
            rec = self._coordinator.register_nonlocal_binding(name, func_name, func_name)
            obligations.append(rec)

        # Inspect bytecode for obligation kinds.
        bytecode_obligations: list[dict[str, Any]] = []
        try:
            for instr in dis.get_instructions(func):
                kind = binding_kind_from_opcode(instr.opname)
                if kind != "unknown":
                    bytecode_obligations.append({
                        "opname": instr.opname,
                        "argval": instr.argval,
                        "kind": kind,
                        "offset": instr.offset,
                        "priority": obligation_priority(kind),
                    })
        except Exception as exc:  # noqa: BLE001
            log.debug("analyze_function_bindings: dis failed: %s", exc)

        self._stats["live_function_analyses"] += 1

        return {
            "function_name": func_name,
            "local_names": sorted(local_names),
            "cell_names": sorted(cell_names),
            "free_names": sorted(free_names),
            "global_names": sorted(global_refs),
            "obligations": obligations,
            "bytecode_obligations": bytecode_obligations,
            "channel": _ANALYSIS_CHANNEL,
        }

    def emit_obligation_judgment(
        self,
        name: str,
        kind: str,
        scope_path: str,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Create a judgment dict for a binding obligation.

        Parameters:
            name: The bare identifier.
            kind: One of the obligation kind strings.
            scope_path: The dotted scope path.
            trust_level: Optional trust level (defaults to ``TrustLevel.MEDIUM``).

        Returns:
            A judgment dict with keys:
            ``name``, ``kind``, ``scope_path``, ``trust_level``,
            ``status``, ``timestamp``, ``priority``, ``channel``.
        """
        if trust_level is None:
            try:
                trust_level = TrustLevel.MEDIUM
            except Exception:  # noqa: BLE001
                trust_level = 2

        return {
            "name": name,
            "kind": kind,
            "scope_path": scope_path,
            "trust_level": int(trust_level),
            "status": "accepted" if kind in _OBLIGATION_KINDS else "pending",
            "timestamp": time.monotonic(),
            "priority": obligation_priority(kind),
            "channel": _ANALYSIS_CHANNEL,
        }


# ---------------------------------------------------------------------------
# GlobalLocalBindingsObligationWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GlobalLocalBindingsObligationWitness:
    """Runtime evidence collector for binding ownership obligations.

    The witness observes live Python objects at runtime and records evidence
    confirming or refuting ownership claims made by
    :class:`GlobalLocalBindingsObligationAnalyzer`.  It is the *verification*
    layer in theory2.tex Ch15: the analyzer makes predictions; the witness
    confirms them against running code.

    Parameters (dataclass fields):
        _analyzer: The analyzer to delegate static analysis to.
        _witnessed_bindings: Accumulated list of witnessing records.
        _obligation_evidence: Accumulated list of obligation evidence records.
        _witness_id: Unique hex id (auto-generated).
    """

    _analyzer: GlobalLocalBindingsObligationAnalyzer = field(
        default_factory=GlobalLocalBindingsObligationAnalyzer
    )
    _witnessed_bindings: list[dict[str, Any]] = field(default_factory=list)
    _obligation_evidence: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(
        default_factory=lambda: uuid.uuid4().hex[:16]
    )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def witness_module_state(self, module: types.ModuleType) -> dict[str, Any]:
        """Perform runtime witnessing of a module's global state.

        Inspects ``module.__dict__``, identifies mutable shared state, and
        records coordination obligations for each mutable global.

        Parameters:
            module: A live Python module.

        Returns:
            An evidence record dict with keys:
            ``witness_id``, ``module_name``, ``mutable_globals``,
            ``immutable_globals``, ``obligations``, ``timestamp``.
        """
        module_name = getattr(module, "__name__", repr(module))
        mutable_globals: list[dict[str, Any]] = []
        immutable_globals: list[str] = []
        obligations: list[dict[str, Any]] = []

        for name, value in list(module.__dict__.items()):
            # Exclude dunder attributes from obligation analysis.
            if name.startswith("__") and name.endswith("__"):
                continue
            if is_mutable_value(value):
                entry = {
                    "name": name,
                    "value_type": type(value).__name__,
                    "value_repr": repr(value)[:80],
                    "is_mutable": True,
                    "scope_path": module_name,
                    "witness_id": self._witness_id,
                }
                mutable_globals.append(entry)
                judgment = self._analyzer.emit_obligation_judgment(
                    name, "exclusive_write", module_name
                )
                obligations.append(judgment)
            else:
                immutable_globals.append(name)

        evidence_record = {
            "witness_id": self._witness_id,
            "module_name": module_name,
            "mutable_globals": mutable_globals,
            "immutable_globals": immutable_globals,
            "obligations": obligations,
            "timestamp": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }
        self._witnessed_bindings.append(evidence_record)
        self._obligation_evidence.extend(obligations)
        return evidence_record

    def witness_function_globals(self, func: types.FunctionType) -> dict[str, Any]:
        """Witness what globals a function actually accesses at runtime.

        Uses ``func.__globals__`` (the module dict of the module where the
        function was defined) and ``func.__code__.co_names`` to identify
        every global name the function may reference.

        Parameters:
            func: A live Python function.

        Returns:
            A witness record dict with keys:
            ``witness_id``, ``function_name``, ``referenced_globals``,
            ``resolved_globals``, ``unresolved_globals``, ``obligations``,
            ``timestamp``.
        """
        code = func.__code__
        func_name = func.__qualname__
        func_globals = func.__globals__

        referenced = list(global_names_from_code(code))
        resolved_globals: list[dict[str, Any]] = []
        unresolved_globals: list[str] = []
        obligations: list[dict[str, Any]] = []

        for name in referenced:
            if name in func_globals:
                value = func_globals[name]
                resolved_globals.append({
                    "name": name,
                    "value_type": type(value).__name__,
                    "is_mutable": is_mutable_value(value),
                })
                if is_mutable_value(value):
                    # Mutable global accessed from function → coordination obligation.
                    judgment = self._analyzer.emit_obligation_judgment(
                        name, "shared_read", func_name
                    )
                    obligations.append(judgment)
            else:
                # Name in co_names but not in __globals__ — might be a builtin.
                import builtins as _builtins
                if not hasattr(_builtins, name):
                    unresolved_globals.append(name)

        record = {
            "witness_id": self._witness_id,
            "function_name": func_name,
            "referenced_globals": referenced,
            "resolved_globals": resolved_globals,
            "unresolved_globals": unresolved_globals,
            "obligations": obligations,
            "timestamp": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }
        self._witnessed_bindings.append(record)
        self._obligation_evidence.extend(obligations)
        return record

    def witness_binding_ownership(
        self,
        func: types.FunctionType,
        expected_locals: frozenset[str],
        expected_globals: frozenset[str],
    ) -> bool:
        """Verify that a function's code object matches expected binding ownership.

        Compares the actual ``co_varnames`` and global references in the code
        object against the *expected_locals* and *expected_globals* sets.
        Records evidence for each comparison outcome.

        Parameters:
            func: The live function to inspect.
            expected_locals: Names expected to be local in the function.
            expected_globals: Names expected to be global references.

        Returns:
            ``True`` if the function's ownership matches all expectations;
            ``False`` if any discrepancy is found.
        """
        code = func.__code__
        func_name = func.__qualname__
        actual_locals = local_names_from_code(code)
        actual_globals = global_names_from_code(code)

        # Check local expectations.
        unexpected_locals = expected_locals - actual_locals
        unexpected_globals = expected_globals - actual_globals

        matches = True

        if unexpected_locals:
            # Some expected locals are not actually local.
            self.refute_binding(
                name=", ".join(sorted(unexpected_locals)),
                claimed_scope=func_name,
                actual_scope="unknown",
                reason=f"expected local but not in co_varnames: {unexpected_locals}",
            )
            matches = False

        if unexpected_globals:
            # Some expected globals are not referenced as globals.
            self.refute_binding(
                name=", ".join(sorted(unexpected_globals)),
                claimed_scope=func_name,
                actual_scope="unknown",
                reason=f"expected global ref but not in co_names: {unexpected_globals}",
            )
            matches = False

        # Record a positive witness if everything matched.
        if matches:
            witness_rec = {
                "witness_id": self._witness_id,
                "function_name": func_name,
                "outcome": "matched",
                "expected_locals": sorted(expected_locals),
                "expected_globals": sorted(expected_globals),
                "timestamp": time.monotonic(),
                "channel": _ANALYSIS_CHANNEL,
            }
            self._witnessed_bindings.append(witness_rec)

        return matches

    def witness_nonlocal_cell(
        self,
        outer_func: types.FunctionType,
        inner_func_name: str,
    ) -> dict[str, Any]:
        """Witness cell variable transport between closure levels.

        Inspects the cell variables of *outer_func* and verifies that an inner
        function named *inner_func_name* exists in the outer function's
        closure constants and shares at least one cell variable.

        Parameters:
            outer_func: The enclosing function whose cells to inspect.
            inner_func_name: The name of the inner (closure) function.

        Returns:
            A witness record with keys:
            ``witness_id``, ``outer_func``, ``inner_func_name``,
            ``cell_names``, ``found_inner``, ``shared_cells``, ``timestamp``.
        """
        code = outer_func.__code__
        outer_name = outer_func.__qualname__
        cell_names_outer = list(cell_names_from_code(code))

        # Search for inner function code objects in the outer function's constants.
        inner_code_objs: list[types.CodeType] = [
            c for c in code.co_consts
            if isinstance(c, types.CodeType) and c.co_name == inner_func_name
        ]

        found_inner = len(inner_code_objs) > 0
        shared_cells: list[str] = []

        if found_inner:
            inner_code = inner_code_objs[0]
            inner_free = frozenset(inner_code.co_freevars)
            outer_cells = frozenset(cell_names_outer)
            # Cells that the inner function uses from the outer scope.
            shared_cells = sorted(inner_free & outer_cells)

        record = {
            "witness_id": self._witness_id,
            "outer_func": outer_name,
            "inner_func_name": inner_func_name,
            "cell_names": cell_names_outer,
            "found_inner": found_inner,
            "shared_cells": shared_cells,
            "timestamp": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }
        self._witnessed_bindings.append(record)

        # Record obligation evidence for each shared cell.
        for cell_name in shared_cells:
            ev = self._analyzer.emit_obligation_judgment(
                cell_name, "exclusive_write", outer_name
            )
            self._obligation_evidence.append(ev)

        return record

    def collect_evidence(self) -> dict[str, Any]:
        """Return all witnessed bindings and obligations as an evidence bundle.

        Returns:
            A dict with keys:
            ``witness_id``, ``witnessed_bindings_count``,
            ``obligation_evidence_count``, ``witnessed_bindings``,
            ``obligation_evidence``, ``timestamp``.
        """
        return {
            "witness_id": self._witness_id,
            "witnessed_bindings_count": len(self._witnessed_bindings),
            "obligation_evidence_count": len(self._obligation_evidence),
            "witnessed_bindings": list(self._witnessed_bindings),
            "obligation_evidence": list(self._obligation_evidence),
            "timestamp": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }

    def refute_binding(
        self,
        name: str,
        claimed_scope: str,
        actual_scope: str,
        reason: str,
    ) -> None:
        """Record a refutation when binding ownership doesn't match a claim.

        Parameters:
            name: The bare identifier whose ownership is disputed.
            claimed_scope: The scope that was claimed to own the binding.
            actual_scope: The scope where the binding was actually found.
            reason: A human-readable explanation of the discrepancy.

        Returns:
            ``None``.
        """
        refutation: dict[str, Any] = {
            "witness_id": self._witness_id,
            "name": name,
            "claimed_scope": claimed_scope,
            "actual_scope": actual_scope,
            "reason": reason,
            "outcome": "refuted",
            "timestamp": time.monotonic(),
            "channel": _ANALYSIS_CHANNEL,
        }
        self._witnessed_bindings.append(refutation)
        self._obligation_evidence.append(refutation)
        log.warning(
            "Witness %s: refutation for %r (claimed=%s, actual=%s): %s",
            self._witness_id,
            name,
            claimed_scope,
            actual_scope,
            reason,
        )


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "GlobalLocalBindingsObligationCoordinator",
    "GlobalLocalBindingsObligationAnalyzer",
    "GlobalLocalBindingsObligationWitness",
    # Helper functions
    "is_mutable_value",
    "global_names_from_code",
    "local_names_from_code",
    "cell_names_from_code",
    "free_names_from_code",
    "binding_kind_from_opcode",
    "obligation_priority",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_GLOBAL_SENTINEL",
    "_LOCAL_SENTINEL",
    "_OBLIGATION_KINDS",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import textwrap

    print("=" * 72)
    print("global_and_local_bindings_obligati.py — smoke test")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. Coordinator
    # ------------------------------------------------------------------
    print("\n--- GlobalLocalBindingsObligationCoordinator ---")
    coord = GlobalLocalBindingsObligationCoordinator()

    rec_g = coord.register_global_binding("counter", 0, "mymodule")
    print(f"  global binding 'counter': kind={rec_g['kind']}, ownership={rec_g['ownership']}")

    rec_l = coord.register_local_binding("tmp", "mymodule.compute")
    print(f"  local  binding 'tmp':     kind={rec_l['kind']}, ownership={rec_l['ownership']}")

    rec_n = coord.register_nonlocal_binding("state", "mymodule.outer.inner", "mymodule.outer")
    print(f"  nonlocal binding 'state': kind={rec_n['kind']}, ownership={rec_n['ownership']}")

    def _outer():
        x = 10
        def _inner():
            return x
        return _inner

    ownership_x = coord.compute_ownership("x", _outer.__code__)
    print(f"  compute_ownership('x', _outer): {ownership_x}")

    coord.transfer_ownership("counter", "mymodule", "mymodule.updater", "global_declaration")
    print(f"  after transfer, ownership_map['counter'] = {coord._ownership_map.get('counter')}")

    obligations = coord.obligations_for_name("counter")
    print(f"  obligations for 'counter': {len(obligations)} records")

    snapshot = coord.module_state_snapshot()
    print(f"  module_state_snapshot keys: {list(snapshot.keys())}")

    conflict = coord.check_write_conflict("counter", "mymodule.some_func")
    print(f"  write conflict check for 'counter' from function scope: {conflict}")

    # ------------------------------------------------------------------
    # 2. Analyzer
    # ------------------------------------------------------------------
    print("\n--- GlobalLocalBindingsObligationAnalyzer ---")
    source = textwrap.dedent("""\
        import os
        _data: list = []
        CONSTANT = 42

        def update(value):
            global _data
            _data.append(value)

        def make_counter(start):
            count = start
            def increment():
                nonlocal count
                count += 1
                return count
            return increment
    """)

    analyzer = GlobalLocalBindingsObligationAnalyzer()
    result = analyzer.analyze_source(source, "demo_module")
    print(f"  assignments found: {len(result['assignments'])}")
    print(f"  global decls: {result['global_declarations']}")
    print(f"  nonlocal decls: {result['nonlocal_declarations']}")
    print(f"  obligations: {len(result['obligations'])}")

    # Live function analysis.
    def _sample_func(x, y):
        global counter  # noqa: PLW0602
        z = x + y
        return z

    func_profile = analyzer.analyze_function_bindings(_sample_func)
    print(f"  _sample_func locals: {func_profile['local_names']}")
    print(f"  _sample_func globals: {func_profile['global_names']}")

    # Live module analysis.
    import types as _types_mod
    mod_profile = analyzer.analyze_live_module(_types_mod)
    print(f"  types module public names count: {len(mod_profile['public_names'])}")
    print(f"  types module mutable globals: {len(mod_profile['mutable_names'])}")

    # emit_obligation_judgment
    judgment = analyzer.emit_obligation_judgment("_data", "exclusive_write", "demo_module")
    print(f"  obligation judgment: name={judgment['name']}, status={judgment['status']}")

    # ------------------------------------------------------------------
    # 3. Witness
    # ------------------------------------------------------------------
    print("\n--- GlobalLocalBindingsObligationWitness ---")
    witness = GlobalLocalBindingsObligationWitness()

    # Witness module state.
    import json as _json_mod
    mod_ev = witness.witness_module_state(_json_mod)
    print(f"  json module mutable globals: {len(mod_ev['mutable_globals'])}")

    # Witness function globals.
    func_ev = witness.witness_function_globals(_sample_func)
    print(f"  _sample_func resolved globals: {[g['name'] for g in func_ev['resolved_globals']]}")

    # Witness binding ownership.
    match = witness.witness_binding_ownership(
        _sample_func,
        expected_locals=frozenset({"x", "y", "z"}),
        expected_globals=frozenset(),
    )
    print(f"  binding ownership match: {match}")

    # Witness nonlocal cell.
    cell_ev = witness.witness_nonlocal_cell(_outer, "_inner")
    print(f"  nonlocal cell witness: found_inner={cell_ev['found_inner']}, shared={cell_ev['shared_cells']}")

    # Refute a binding.
    witness.refute_binding("bad_name", "scope_a", "scope_b", "test refutation")

    # Collect evidence.
    evidence = witness.collect_evidence()
    print(f"  evidence bundle: {evidence['witnessed_bindings_count']} bindings, "
          f"{evidence['obligation_evidence_count']} obligations")

    # ------------------------------------------------------------------
    # Helper functions
    # ------------------------------------------------------------------
    print("\n--- Helper functions ---")
    print(f"  is_mutable_value([]):        {is_mutable_value([])}")
    print(f"  is_mutable_value((1,2)):     {is_mutable_value((1, 2))}")
    print(f"  is_mutable_value(42):        {is_mutable_value(42)}")
    print(f"  binding_kind_from_opcode('STORE_GLOBAL'): {binding_kind_from_opcode('STORE_GLOBAL')}")
    print(f"  obligation_priority('atomic_update'):     {obligation_priority('atomic_update')}")

    def _cell_func():
        val = 0
        def _inner():
            nonlocal val
            val += 1
        return _inner

    print(f"  cell_names_from_code(_cell_func): {cell_names_from_code(_cell_func.__code__)}")
    print(f"  free_names_from_code(_inner):     "
          f"{free_names_from_code(_cell_func().__code__)}")

    print("\n[smoke test passed]")
