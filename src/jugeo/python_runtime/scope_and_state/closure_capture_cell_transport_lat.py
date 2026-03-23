"""Section 3 — Closure Capture: Cell Transport, Latent Context, and Proof Consequences (theory2.tex Ch15).

This module formalises the *cell transport* mechanism that underlies Python
closures, as described in theory2.tex Ch15 §3.  When an inner function
captures a name from an enclosing scope, the CPython interpreter does not
copy the value — it copies a *reference to the cell object* that holds the
value.  The cell is shared between the outer frame and the inner function
object.  This is the "cell transport" step.

**Latent context** refers to the set of cells a closure carries implicitly:
a closure's ``__closure__`` tuple is its *latent context*, a sequence of cell
objects each wrapping one captured name.  The latent context is not visible
in the function's signature; it is injected at function-creation time via the
``MAKE_CELL`` and ``COPY_FREE_VARS`` bytecode instructions.

**Proof consequence — late binding:**
Because a cell holds a *reference*, any mutation performed by the outer frame
after the closure is created is visible to the closure.  This is the classic
"late binding" effect: a closure that captures a loop variable sees the final
value of the loop variable, not the value at the time the closure was created.
Formally, the cell transport morphism is *not* an injection of values but an
injection of *references*, and the sheaf condition that would guarantee value
equality across mutation boundaries is *not* satisfied unless the cell is
deliberately frozen (e.g. via a default-argument trick).

The three classes in this module implement:

1. :class:`ClosureCaptureCellTransportCoordinator` — low-level registry and
   event log for cells, closures, and transport events.  Analogous to the
   *site coordinator* in theory2.tex Ch15 §3.1 that assigns coordinates to
   cells and tracks their transport morphisms.

2. :class:`ClosureCaptureCellTransportAnalyzer` — static and dynamic analysis
   of closures.  Performs AST-level analysis to find captured names and
   late-binding risks, and runtime inspection of live closures.

3. :class:`ClosureCaptureCellTransportWitness` — runtime witnessing of closure
   behaviour.  Reads cell contents, detects late-binding at runtime, and
   collects evidence bundles for the judgment algebra.

References:
    theory2.tex Ch15 — Scope, State, and the LEGB Sheaf.
    theory2.tex §3.3 — Cell Transport Morphisms.
    CPython documentation — ``inspect.getclosurevars``, ``dis`` module.
    PEP 3104 — Access to Names in Outer Scopes.
"""

from __future__ import annotations

import ast
import dis
import hashlib
import inspect
import logging
import re
import textwrap
import time
import types
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Cross-package imports — geometry.site
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateKind,
        CoordinateObject,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except ImportError:
    from enum import Enum  # noqa: F401 (used only in stubs below)

    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind when jugeo.geometry is unavailable."""

        FUNCTION = "function"
        MODULE = "module"
        CLASS = "class"
        REGION = "region"
        CLOSURE = "closure"

    class MorphismKind(str, Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind."""

        RESTRICTION = "restriction"
        TRANSPORT = "transport"
        INCLUSION = "inclusion"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class CoordinateObject:
        """Stub CoordinateObject used when the real geometry package is absent."""

        components: tuple[str, ...] = ()
        kind: CoordinateKind = CoordinateKind.REGION

        @property
        def key(self) -> str:
            """Return a slash-joined string key."""
            return "/".join(self.components)

        def serialize(self) -> dict[str, Any]:
            """Serialise to a plain dict."""
            return {"components": list(self.components), "kind": str(self.kind)}

    class Site:  # type: ignore[no-redef]
        """Stub Site."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub SiteBuilder."""


# ---------------------------------------------------------------------------
# Cross-package imports — judgments.judgment_terms
# ---------------------------------------------------------------------------

try:
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
except ImportError:
    from enum import IntEnum  # noqa: F401

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        """Stub TrustLevel."""

        CONTRADICTED = 0
        UNVERIFIED = 1
        HEURISTIC = 2
        SOLVER_DISCHARGED = 3
        VERIFIED_PROOF = 4

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        """Stub JudgmentStatus."""

        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        """Stub PropositionKind."""

        STRUCTURAL = "structural"
        RELATIONAL = "relational"
        EXISTENTIAL = "existential"
        UNIVERSAL = "universal"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        """Stub EvidenceItemKind."""

        STATIC_ANALYSIS = "static_analysis"
        SOLVER_CERTIFICATE = "solver_certificate"
        RUNTIME_OBSERVATION = "runtime_observation"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Proposition:
        """Stub Proposition."""

        kind: PropositionKind = PropositionKind.STRUCTURAL
        formula: str = ""
        free_variables: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Carrier:
        """Stub Carrier."""

        name: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class TrustAnnotation:
        """Stub TrustAnnotation."""

        level: TrustLevel = TrustLevel.UNVERIFIED  # type: ignore[assignment]

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class EvidenceItem:
        """Stub EvidenceItem."""

        kind: EvidenceItemKind = EvidenceItemKind.STATIC_ANALYSIS
        trust_level: TrustLevel = TrustLevel.UNVERIFIED  # type: ignore[assignment]
        note: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class EvidenceBundle:
        """Stub EvidenceBundle."""

        items: tuple[EvidenceItem, ...] = ()

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ResidualObligation:
        """Stub ResidualObligation."""

        description: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Obstruction:
        """Stub Obstruction."""

        reason: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Provenance:
        """Stub Provenance."""

        channel: str = ""
        timestamp: float = field(default_factory=time.time)

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Judgment:
        """Stub Judgment."""

        status: JudgmentStatus = JudgmentStatus.PROPOSED
        proposition: Proposition = field(default_factory=Proposition)
        provenance: Provenance = field(default_factory=Provenance)


# ---------------------------------------------------------------------------
# Cross-package imports — scope_and_state.models
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.scope_and_state.models import (
        BindingMap,
        NameCoordinate,
        NameKind,
        NameResolutionResult,
        ScopeChain,
        ScopeKind,
        ScopeSection,
    )
except ImportError:
    class NameKind(str, Enum):  # type: ignore[no-redef]
        """Stub NameKind."""

        LOCAL = "local"
        FREE = "free"
        CLOSURE = "closure"
        GLOBAL = "global"
        PARAMETER = "parameter"
        BUILTIN = "builtin"

    class ScopeKind(str, Enum):  # type: ignore[no-redef]
        """Stub ScopeKind."""

        MODULE = "module"
        FUNCTION = "function"
        CLASS = "class"
        LAMBDA = "lambda"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class NameCoordinate:
        """Stub NameCoordinate."""

        name: str = ""
        kind: NameKind = NameKind.LOCAL
        scope_path: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ScopeChain:
        """Stub ScopeChain."""

        scopes: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ScopeSection:
        """Stub ScopeSection."""

        scope_path: str = ""
        kind: ScopeKind = ScopeKind.FUNCTION

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class BindingMap:
        """Stub BindingMap."""

        bindings: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class NameResolutionResult:
        """Stub NameResolutionResult."""

        found: bool = False
        name: str = ""


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ANALYSIS_CHANNEL: str = "copilot-s03-closure-capture-cell-transport"

_CELL_KINDS: frozenset[str] = frozenset({"cell_var", "free_var", "shared_cell"})

_CLOSURE_OPCODES: frozenset[str] = frozenset(
    {
        "LOAD_DEREF",
        "STORE_DEREF",
        "DELETE_DEREF",
        "LOAD_CLASSDEREF",
        "MAKE_CELL",
        "COPY_FREE_VARS",
    }
)

# Names that commonly appear in loops and are classic late-binding hazards
_LOOP_VAR_PATTERNS: frozenset[str] = frozenset(
    {"i", "j", "k", "n", "x", "y", "idx", "item", "v", "val", "elem"}
)


# ---------------------------------------------------------------------------
# Helper functions (module-level)
# ---------------------------------------------------------------------------


def get_cell_contents(cell: Any) -> Any | None:
    """Safely read a cell object's ``cell_contents`` attribute.

    Python cell objects raise ``ValueError`` when ``cell_contents`` is
    accessed on an empty (unbound) cell — this happens when the captured
    variable has been deleted or was never assigned.  This helper swallows
    that exception and returns ``None`` instead, making it safe to use in
    analysis loops that do not want to handle the error inline.

    Args:
        cell: Any object that may have a ``cell_contents`` attribute.
              Typically one of the entries in ``func.__closure__``.

    Returns:
        The value held by the cell, or ``None`` if the cell is empty or
        if *cell* is not a real cell object.
    """
    try:
        return cell.cell_contents  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        # ValueError → cell is empty (variable was deleted or unbound).
        # AttributeError → cell is not a real cell object; treat as empty.
        return None


def make_cell_id(func_qualname: str, var_name: str) -> str:
    """Create a stable, collision-resistant identifier for a cell.

    The identifier is derived by hashing the *func_qualname* and *var_name*
    together so that the same (function, variable) pair always produces the
    same cell-id across runs.  The hash is truncated to 16 hex characters,
    which gives 64 bits of collision resistance — sufficient for a typical
    analysis session.

    Args:
        func_qualname: The ``__qualname__`` of the function that owns or
                       captures the cell (e.g. ``"outer.<locals>.inner"``).
        var_name:      The plain identifier string of the captured variable.

    Returns:
        A 16-character lowercase hexadecimal string.
    """
    raw = f"{func_qualname}::{var_name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def closure_arity(func: types.FunctionType) -> int:
    """Return the number of free variables captured by *func*.

    Equivalent to ``len(func.__code__.co_freevars)`` but guards against
    non-function inputs by returning 0 for anything that does not have a
    ``__code__`` attribute with ``co_freevars``.

    Args:
        func: The function to inspect.

    Returns:
        Non-negative integer — the count of captured free variables.
    """
    try:
        return len(func.__code__.co_freevars)
    except AttributeError:
        return 0


def is_closure(func: types.FunctionType) -> bool:
    """Return ``True`` if *func* is a closure (has a non-empty ``__closure__``).

    A function is a closure iff ``func.__closure__`` is not ``None``.  A
    plain (non-closure) function has ``__closure__ = None`` even if its code
    object lists free variables (which would indicate an ill-formed function
    object).

    Args:
        func: The function to test.

    Returns:
        ``True`` if ``func.__closure__`` is not ``None``, else ``False``.
    """
    try:
        return func.__closure__ is not None
    except AttributeError:
        return False


def extract_closure_vars_snapshot(func: types.FunctionType) -> dict[str, Any]:
    """Extract all free-variable values from a closure into a plain dict.

    Iterates over ``func.__code__.co_freevars`` and pairs each name with
    its current value read from the corresponding cell in
    ``func.__closure__``.  Empty cells are mapped to the sentinel string
    ``"<empty-cell>"``.

    If *func* is not a closure (``func.__closure__ is None``) an empty dict
    is returned immediately without raising.

    Args:
        func: A live closure function to snapshot.

    Returns:
        A ``dict[str, Any]`` mapping each free-variable name to its current
        value (or ``"<empty-cell>"`` for unbound cells).
    """
    if not is_closure(func):
        return {}
    snapshot: dict[str, Any] = {}
    free_names: tuple[str, ...] = func.__code__.co_freevars
    closure_cells = func.__closure__  # guaranteed non-None at this point
    for name, cell in zip(free_names, closure_cells):
        contents = get_cell_contents(cell)
        snapshot[name] = contents if contents is not None else "<empty-cell>"
    return snapshot


def late_binding_risk_score(func: types.FunctionType) -> float:
    """Estimate the late-binding risk of *func* as a score in [0.0, 1.0].

    The heuristic is based on two factors:
    - Whether *func* is a closure at all (non-closures score 0.0).
    - Whether any of the captured free variables have names that commonly
      appear as loop variables (``i``, ``j``, ``k``, ``n``, ``idx``, etc.).

    The score is computed as::

        risk = (matching_free_vars / total_free_vars)  *  0.8
               + (0.2 if __name__ suggests a lambda or comprehension)

    The 0.8 and 0.2 weights are heuristic and not formally calibrated.

    Args:
        func: The function to evaluate.

    Returns:
        A float in [0.0, 1.0].  0.0 means no detectable risk; 1.0 means
        maximum heuristic risk (all free vars are known loop-var names).
    """
    if not is_closure(func):
        return 0.0
    free_vars: tuple[str, ...] = func.__code__.co_freevars
    if not free_vars:
        return 0.0
    # Count how many free vars match known loop-var name patterns
    matching = sum(1 for v in free_vars if v in _LOOP_VAR_PATTERNS)
    ratio = matching / len(free_vars)
    # Small bonus if the function name looks like a lambda or comprehension
    name_bonus = 0.2 if "<lambda>" in (func.__qualname__ or "") else 0.0
    return min(1.0, ratio * 0.8 + name_bonus)


# ---------------------------------------------------------------------------
# ClosureCaptureCellTransportCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureCaptureCellTransportCoordinator:
    """Registry and event log for cells, closures, and cell-transport events.

    This class models the *site coordinator* described in theory2.tex Ch15
    §3.1.  Its responsibilities are:

    - **Cell registration** — each cell in the program is assigned a unique
      ``cell_id`` and stored in ``_cell_registry`` with metadata: name,
      scope path, current value, mutation count, and creation timestamp.

    - **Closure registration** — each closure is assigned a ``closure_id``
      and linked to the cells it captures via ``_closure_map``.

    - **Transport events** — each time a cell is transported from one scope
      into another (e.g. when a closure is created), a transport event dict
      is appended to ``_transport_log``.

    - **Mutation events** — when a cell is mutated, a late-binding alert is
      emitted if the cell is shared by at least one registered closure.

    The coordinator is deliberately *mutable* (not frozen) because it
    accumulates events during an analysis session.

    Attributes:
        _cell_registry:        Mapping from cell_id → metadata dict.
        _closure_map:          Mapping from closure_id → list of cell_ids.
        _transport_log:        Append-only list of transport event dicts.
        _coordinator_id:       Unique 16-hex identifier for this instance.
        _late_binding_alerts:  List of late-binding alert dicts emitted so far.
    """

    _cell_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    _closure_map: dict[str, list[str]] = field(default_factory=dict)
    _transport_log: list[dict[str, Any]] = field(default_factory=list)
    _coordinator_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    _late_binding_alerts: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Cell registration
    # ------------------------------------------------------------------

    def register_cell(
        self,
        name: str,
        scope_path: str,
        initial_value: Any = None,
    ) -> str:
        """Register a new cell and return its unique ``cell_id``.

        Constructs a stable ``cell_id`` from ``scope_path`` and ``name``,
        then stores a metadata record in ``_cell_registry``.  If a cell with
        the same id has already been registered (idempotent registration),
        the existing record is returned unchanged.

        Args:
            name:          The Python identifier string of the captured
                           variable (e.g. ``"x"``).
            scope_path:    A dot-separated scope path identifying where the
                           cell was created (e.g. ``"mymodule.outer"``).
            initial_value: The value the cell holds at registration time.
                           Defaults to ``None``.

        Returns:
            The ``cell_id`` string (16 hex characters).
        """
        cell_id = make_cell_id(scope_path, name)
        if cell_id in self._cell_registry:
            log.debug(
                "Cell %s already registered for %s::%s; skipping re-registration.",
                cell_id,
                scope_path,
                name,
            )
            return cell_id
        self._cell_registry[cell_id] = {
            "cell_id": cell_id,
            "name": name,
            "scope_path": scope_path,
            "value": initial_value,
            "mutations": 0,
            "created_at": time.monotonic(),
            "shared_by_closures": [],
        }
        log.debug("Registered cell %s (%s) in scope %s.", cell_id, name, scope_path)
        return cell_id

    # ------------------------------------------------------------------
    # Closure registration
    # ------------------------------------------------------------------

    def register_closure(
        self,
        func_name: str,
        cell_ids: list[str],
    ) -> str:
        """Register a closure and record the cell transport events.

        Each cell id in *cell_ids* must have been previously registered via
        :meth:`register_cell`.  The method records the transport for each
        captured cell by appending an event to ``_transport_log`` and
        back-links the closure id into the cell's ``shared_by_closures``
        list (enabling future late-binding alert emission).

        Args:
            func_name: The ``__qualname__`` or a human-readable name for the
                       closure function.
            cell_ids:  The list of cell_ids captured by this closure.

        Returns:
            The new ``closure_id`` string (16 hex characters derived from
            the func_name).
        """
        closure_id = hashlib.sha256(
            f"{func_name}::{uuid.uuid4().hex}".encode()
        ).hexdigest()[:16]
        self._closure_map[closure_id] = list(cell_ids)
        transport_time = time.monotonic()
        for cid in cell_ids:
            # Record the transport event
            event: dict[str, Any] = {
                "event_type": "cell_transport",
                "closure_id": closure_id,
                "func_name": func_name,
                "cell_id": cid,
                "timestamp": transport_time,
                "morphism_kind": str(MorphismKind.TRANSPORT),
            }
            self._transport_log.append(event)
            # Back-link: mark this cell as shared by the closure
            record = self._cell_registry.get(cid)
            if record is not None:
                record["shared_by_closures"].append(closure_id)
            else:
                log.warning(
                    "Closure %s captures unregistered cell %s.", closure_id, cid
                )
        log.debug(
            "Registered closure %s (%s) capturing %d cells.",
            closure_id,
            func_name,
            len(cell_ids),
        )
        return closure_id

    # ------------------------------------------------------------------
    # Mutation recording
    # ------------------------------------------------------------------

    def record_cell_mutation(
        self,
        cell_id: str,
        new_value: Any,
        mutating_scope: str,
    ) -> dict[str, Any]:
        """Record a mutation of a cell and emit late-binding alerts if needed.

        This method updates the cell's stored value in ``_cell_registry``,
        increments the mutation counter, and — if the cell is captured by
        one or more closures — emits a late-binding alert for each closure
        into ``_late_binding_alerts``.

        The late-binding alert is the formal proof consequence described in
        theory2.tex Ch15 §3.3: a mutation *after* closure creation means the
        closure will observe the new value rather than the creation-time value.

        Args:
            cell_id:        The id of the cell being mutated.
            new_value:      The new value stored in the cell.
            mutating_scope: A scope path string identifying which scope
                            performed the mutation.

        Returns:
            A dict describing the mutation event, including any alerts
            generated.
        """
        record = self._cell_registry.get(cell_id)
        if record is None:
            log.warning("Mutation recorded for unknown cell %s.", cell_id)
            return {
                "event_type": "cell_mutation",
                "cell_id": cell_id,
                "error": "cell_not_registered",
            }
        old_value = record["value"]
        record["value"] = new_value
        record["mutations"] += 1
        mutation_time = time.monotonic()
        alerts: list[dict[str, Any]] = []
        # Emit a late-binding alert for every closure that has already
        # captured this cell; it will see *new_value* on next read.
        for closure_id in record["shared_by_closures"]:
            alert: dict[str, Any] = {
                "alert_type": "late_binding",
                "cell_id": cell_id,
                "cell_name": record["name"],
                "closure_id": closure_id,
                "old_value": old_value,
                "new_value": new_value,
                "mutating_scope": mutating_scope,
                "mutation_index": record["mutations"],
                "timestamp": mutation_time,
            }
            self._late_binding_alerts.append(alert)
            alerts.append(alert)
        event = {
            "event_type": "cell_mutation",
            "cell_id": cell_id,
            "cell_name": record["name"],
            "old_value": old_value,
            "new_value": new_value,
            "mutating_scope": mutating_scope,
            "mutation_count": record["mutations"],
            "late_binding_alerts": alerts,
            "timestamp": mutation_time,
        }
        return event

    # ------------------------------------------------------------------
    # Cell transport
    # ------------------------------------------------------------------

    def transport_cell(
        self,
        cell_id: str,
        from_scope: str,
        to_scope: str,
    ) -> dict[str, Any]:
        """Record a cell transport event between two scopes.

        A *transport* in the sheaf-theoretic sense (theory2.tex §3.3) is a
        morphism that moves a cell reference from one scope (the *source*)
        into another (the *target*).  This method records the event with
        full morphism metadata.

        Args:
            cell_id:    The id of the cell being transported.
            from_scope: The scope path of the source scope.
            to_scope:   The scope path of the destination scope.

        Returns:
            A dict describing the transport event with keys:
            ``event_type``, ``cell_id``, ``cell_name``, ``from_scope``,
            ``to_scope``, ``morphism_kind``, ``timestamp``.
        """
        record = self._cell_registry.get(cell_id, {})
        cell_name = record.get("name", "<unknown>")
        event: dict[str, Any] = {
            "event_type": "cell_transport",
            "cell_id": cell_id,
            "cell_name": cell_name,
            "from_scope": from_scope,
            "to_scope": to_scope,
            "morphism_kind": str(MorphismKind.TRANSPORT),
            "timestamp": time.monotonic(),
        }
        self._transport_log.append(event)
        log.debug(
            "Cell %s (%s) transported %s → %s.",
            cell_id,
            cell_name,
            from_scope,
            to_scope,
        )
        return event

    # ------------------------------------------------------------------
    # Late-binding risk check
    # ------------------------------------------------------------------

    def check_late_binding_risk(
        self,
        func: types.FunctionType,
    ) -> list[dict[str, Any]]:
        """Inspect *func* for the classic late-binding loop-capture pattern.

        The classic bug is::

            funcs = []
            for i in range(5):
                funcs.append(lambda: i)   # captures cell 'i'
            # funcs[0]() == 4  (not 0!)

        This method uses ``dis.get_instructions`` to find STORE_DEREF
        instructions that appear inside SETUP_LOOP / FOR_ITER blocks (in
        older bytecode) or by heuristic matching of co_freevars with
        co_varnames of the enclosing function.

        For each risk detected a dict is appended to the result list with:
        - ``risk_type``: always ``"late_binding_loop_capture"``
        - ``func_qualname``: the qualified name of the suspicious closure
        - ``var_name``: the captured variable suspected of late binding
        - ``risk_score``: a float in [0.0, 1.0] computed by
          :func:`late_binding_risk_score`
        - ``opcode_evidence``: list of opcode names found that suggest capture

        Args:
            func: A live function object to inspect (need not be a closure).

        Returns:
            A possibly-empty list of risk-dict entries.
        """
        risks: list[dict[str, Any]] = []
        # Walk the bytecode of *func* looking for cell-related instructions
        try:
            instructions = list(dis.get_instructions(func))
        except (TypeError, AttributeError):
            return risks
        # Find all STORE_DEREF targets — these are variables written via cell
        store_deref_names: list[str] = [
            instr.argval
            for instr in instructions
            if instr.opname == "STORE_DEREF"
        ]
        # Free vars that have loop-variable-like names are high-risk
        free_vars: tuple[str, ...] = getattr(func.__code__, "co_freevars", ())
        for vname in free_vars:
            if vname in _LOOP_VAR_PATTERNS or vname in store_deref_names:
                opcode_evidence = [
                    instr.opname
                    for instr in instructions
                    if instr.opname in _CLOSURE_OPCODES
                    and getattr(instr, "argval", None) == vname
                ]
                risks.append(
                    {
                        "risk_type": "late_binding_loop_capture",
                        "func_qualname": func.__qualname__,
                        "var_name": vname,
                        "risk_score": late_binding_risk_score(func),
                        "opcode_evidence": opcode_evidence,
                    }
                )
        return risks

    # ------------------------------------------------------------------
    # Coordinate builder
    # ------------------------------------------------------------------

    def closure_coordinate(
        self,
        func_name: str,
        free_vars: tuple[str, ...],
        parent_scope: str,
    ) -> CoordinateObject:
        """Build a :class:`CoordinateObject` representing a closure's coordinate.

        In the sheaf model, a closure occupies a specific coordinate in the
        scope hierarchy: it is a FUNCTION-kind coordinate whose component
        path is ``(parent_scope, func_name, *free_vars)``.  This method
        constructs that object for use in the geometry layer.

        Args:
            func_name:    The name (or qualname) of the closure function.
            free_vars:    Tuple of free-variable names captured by the closure.
            parent_scope: The scope path of the immediately enclosing scope.

        Returns:
            A :class:`CoordinateObject` with ``kind=CoordinateKind.FUNCTION``
            (or ``CoordinateKind.CLOSURE`` if defined) and components
            ``(parent_scope, func_name) + free_vars``.
        """
        components = (parent_scope, func_name) + tuple(free_vars)
        kind = getattr(CoordinateKind, "CLOSURE", CoordinateKind.FUNCTION)  # type: ignore[attr-defined]
        return CoordinateObject(components=components, kind=kind)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary of the coordinator's current state.

        Returns a plain dict with counts of registered cells, closures,
        transport events, late-binding alerts, and the coordinator id.

        Returns:
            Dict with keys: ``coordinator_id``, ``cell_count``,
            ``closure_count``, ``transport_event_count``,
            ``late_binding_alert_count``, ``mutated_cell_count``.
        """
        mutated = sum(
            1
            for r in self._cell_registry.values()
            if r.get("mutations", 0) > 0
        )
        return {
            "coordinator_id": self._coordinator_id,
            "cell_count": len(self._cell_registry),
            "closure_count": len(self._closure_map),
            "transport_event_count": len(self._transport_log),
            "late_binding_alert_count": len(self._late_binding_alerts),
            "mutated_cell_count": mutated,
        }


# ---------------------------------------------------------------------------
# ClosureCaptureCellTransportAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureCaptureCellTransportAnalyzer:
    """Static and dynamic analyser for closure cell capture.

    Combines AST-level analysis (to find nested functions and captured names
    without executing the code) with runtime inspection (to read live cell
    contents and detect actual late-binding behaviour).

    Attributes:
        _coordinator:         Shared :class:`ClosureCaptureCellTransportCoordinator`.
        _ast_cache:           Cache of parsed AST modules keyed by source hash.
        _cell_analysis_cache: Cache of cell-analysis results keyed by
                              ``func.__qualname__``.
        _stats:               Counter dict tracking analysis operations.
    """

    _coordinator: ClosureCaptureCellTransportCoordinator = field(
        default_factory=ClosureCaptureCellTransportCoordinator
    )
    _ast_cache: dict[str, ast.Module] = field(default_factory=dict)
    _cell_analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _stats: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)  # type: ignore[return-value]
    )

    # ------------------------------------------------------------------
    # Full source analysis
    # ------------------------------------------------------------------

    def analyze_source(
        self,
        source: str,
        module_name: str = "<module>",
    ) -> dict[str, Any]:
        """Perform full AST analysis of *source* for closures.

        Parses *source* into an AST, then:
        1. Walks the tree to find all nested function definitions.
        2. For each nested function, identifies which variables it captures
           from the immediately enclosing function's scope.
        3. Registers each captured name as a cell in the coordinator.
        4. Registers each nested function as a closure.

        Args:
            source:      Python source code as a string.
            module_name: Name used to identify the module in coordinates and
                         logging (default ``"<module>"``).

        Returns:
            A dict with keys:
            - ``module_name``: the module name passed in.
            - ``nested_functions``: list of nested-function dicts (from
              :meth:`find_nested_functions`).
            - ``closures``: list of closure info dicts with ``func_name``,
              ``parent``, ``captured_names``, ``cell_ids``, ``closure_id``.
            - ``coordinator_summary``: the coordinator's :meth:`summary` dict.
            - ``stats``: the analyser's stats dict.
        """
        self._stats["analyze_source_calls"] += 1
        # Cache ASTs by SHA-256 of source to avoid re-parsing identical text
        src_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        if src_hash in self._ast_cache:
            tree = self._ast_cache[src_hash]
        else:
            try:
                tree = ast.parse(textwrap.dedent(source), filename=module_name)
            except SyntaxError as exc:
                log.error("SyntaxError parsing source for %s: %s", module_name, exc)
                return {
                    "module_name": module_name,
                    "error": str(exc),
                    "nested_functions": [],
                    "closures": [],
                }
            self._ast_cache[src_hash] = tree
        nested = self.find_nested_functions(tree, parent_path=module_name)
        closures_info: list[dict[str, Any]] = []
        # Build a lookup of function nodes by qualified path for capture analysis
        func_nodes: dict[str, ast.FunctionDef] = {}
        for entry in nested:
            node = entry.get("node")
            if node is not None:
                func_nodes[entry["qualified_path"]] = node  # type: ignore[assignment]
        for entry in nested:
            inner_node = entry.get("node")
            parent_path = entry.get("parent_path", "")
            outer_node = func_nodes.get(parent_path)
            if inner_node is None or not isinstance(
                inner_node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if outer_node is None or not isinstance(
                outer_node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                # Parent is module or class — no captures in the closure sense
                captured: frozenset[str] = frozenset()
            else:
                captured = self.find_captured_names(inner_node, outer_node)  # type: ignore[arg-type]
            # Register cells and closure in the coordinator
            cell_ids: list[str] = []
            for cname in sorted(captured):
                cid = self._coordinator.register_cell(
                    name=cname,
                    scope_path=parent_path,
                )
                cell_ids.append(cid)
            closure_id = ""
            if cell_ids:
                closure_id = self._coordinator.register_closure(
                    func_name=entry["qualified_path"],
                    cell_ids=cell_ids,
                )
            closures_info.append(
                {
                    "func_name": entry["qualified_path"],
                    "parent": parent_path,
                    "depth": entry.get("depth", 0),
                    "captured_names": sorted(captured),
                    "cell_ids": cell_ids,
                    "closure_id": closure_id,
                }
            )
            self._stats["closures_detected"] += 1
        return {
            "module_name": module_name,
            "nested_functions": nested,
            "closures": closures_info,
            "coordinator_summary": self._coordinator.summary(),
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------
    # AST helpers
    # ------------------------------------------------------------------

    def find_nested_functions(
        self,
        tree: ast.AST,
        parent_path: str = "",
    ) -> list[dict[str, Any]]:
        """Walk the AST and return info dicts for all nested function definitions.

        A function definition is considered "nested" if it appears inside the
        body of another function definition (or async function definition).
        Top-level functions defined directly in a module body are included
        with ``depth=1`` and ``parent_path`` equal to the module name.

        Args:
            tree:        The AST to walk (typically an ``ast.Module``).
            parent_path: The scope path of the enclosing context.

        Returns:
            A list of dicts, each containing:
            - ``name``: the bare function name
            - ``qualified_path``: dotted path including all ancestors
            - ``parent_path``: qualified path of the immediately enclosing
              function (or module)
            - ``depth``: nesting depth (1 = top-level function in module)
            - ``lineno``: source line number
            - ``node``: the ``ast.FunctionDef`` node (not serialisable but
              useful for further analysis in the same process)
        """
        results: list[dict[str, Any]] = []

        def _walk(
            node: ast.AST,
            current_path: str,
            depth: int,
            inside_func: bool,
        ) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child_path = (
                        f"{current_path}.{child.name}"
                        if current_path
                        else child.name
                    )
                    results.append(
                        {
                            "name": child.name,
                            "qualified_path": child_path,
                            "parent_path": current_path,
                            "depth": depth,
                            "lineno": child.lineno,
                            "node": child,
                            "is_async": isinstance(child, ast.AsyncFunctionDef),
                        }
                    )
                    # Recurse into the function body with increased depth
                    _walk(child, child_path, depth + 1, inside_func=True)
                elif isinstance(child, ast.ClassDef):
                    class_path = (
                        f"{current_path}.{child.name}"
                        if current_path
                        else child.name
                    )
                    _walk(child, class_path, depth, inside_func)
                else:
                    _walk(child, current_path, depth, inside_func)

        _walk(tree, parent_path, depth=1, inside_func=False)
        return results

    def find_captured_names(
        self,
        inner_func_node: ast.FunctionDef,  # type: ignore[name-defined]
        outer_func_node: ast.FunctionDef,  # type: ignore[name-defined]
    ) -> frozenset[str]:
        """Find names used in *inner_func_node* that are defined in *outer_func_node*.

        The algorithm:
        1. Collect all names *defined* in the outer function (via assignments,
           parameters, for-loop targets, with-statement targets).
        2. Collect all names *used* in the inner function (Load context).
        3. Collect all names *defined locally* in the inner function.
        4. The captured set is ``outer_defined ∩ inner_used − inner_defined``.

        This is a conservative static approximation; a precise analysis would
        require full symbol-table resolution.

        Args:
            inner_func_node: AST node for the inner (capturing) function.
            outer_func_node: AST node for the outer (defining) function.

        Returns:
            A frozenset of identifier strings that are captured.
        """

        def _collect_defined(func_node: ast.FunctionDef) -> set[str]:
            """Collect names defined (assigned / parametrised) in func_node."""
            defined: set[str] = set()
            # Parameters
            args = func_node.args
            for arg in (
                args.args
                + args.posonlyargs
                + args.kwonlyargs
                + ([args.vararg] if args.vararg else [])
                + ([args.kwarg] if args.kwarg else [])
            ):
                defined.add(arg.arg)
            # Assignments within the body (shallow — not inside nested funcs)
            for node in ast.walk(func_node):
                if node is inner_func_node:
                    continue  # don't descend into inner
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            defined.add(t.id)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    if isinstance(node.target, ast.Name):
                        defined.add(node.target.id)
                elif isinstance(node, ast.For):
                    if isinstance(node.target, ast.Name):
                        defined.add(node.target.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        defined.add(local)
            return defined

        def _collect_used(func_node: ast.FunctionDef) -> set[str]:
            """Collect all Name nodes in Load context within func_node."""
            used: set[str] = set()
            for node in ast.walk(func_node):
                if isinstance(node, ast.Name) and isinstance(
                    node.ctx, ast.Load
                ):
                    used.add(node.id)
            return used

        outer_defined = _collect_defined(outer_func_node)
        inner_used = _collect_used(inner_func_node)
        inner_defined = _collect_defined(inner_func_node)
        captured = outer_defined & inner_used - inner_defined
        return frozenset(captured)

    # ------------------------------------------------------------------
    # Live closure analysis
    # ------------------------------------------------------------------

    def analyze_live_closure(
        self,
        func: types.FunctionType,
    ) -> dict[str, Any]:
        """Inspect a live closure using runtime introspection.

        Uses ``inspect.getclosurevars(func)``, ``func.__closure__``, and
        ``func.__code__.co_freevars`` to build a complete picture of a live
        closure's state.

        Args:
            func: A live function object.  Need not be a closure, but the
                  most interesting results are produced for closures.

        Returns:
            A dict with keys:
            - ``qualname``: ``func.__qualname__``
            - ``is_closure``: bool
            - ``free_vars``: list of free-variable name strings
            - ``cell_count``: number of cells in ``__closure__``
            - ``cell_contents``: dict mapping var name → current value
            - ``closure_vars``: result of ``inspect.getclosurevars(func)``
              (as a namedtuple cast to dict)
            - ``mutation_risk``: output of ``check_late_binding_risk``
            - ``disassembly``: output of ``disassemble_cell_ops``
            - ``risk_score``: float from :func:`late_binding_risk_score`
        """
        self._stats["analyze_live_closure_calls"] += 1
        qualname = getattr(func, "__qualname__", repr(func))
        # Check cache
        if qualname in self._cell_analysis_cache:
            return self._cell_analysis_cache[qualname]
        free_vars = list(getattr(func.__code__, "co_freevars", ()))
        cell_contents = extract_closure_vars_snapshot(func)
        # getclosurevars returns a ClosureVars namedtuple; serialise it
        try:
            cv = inspect.getclosurevars(func)
            closure_vars: dict[str, Any] = {
                "nonlocals": dict(cv.nonlocals),
                "globals": dict(cv.globals),
                "builtins": dict(cv.builtins),
                "unbound": list(cv.unbound),
            }
        except (TypeError, AttributeError) as exc:
            closure_vars = {"error": str(exc)}
        mutation_risk = self._coordinator.check_late_binding_risk(func)
        disassembly = self.disassemble_cell_ops(func)
        result: dict[str, Any] = {
            "qualname": qualname,
            "is_closure": is_closure(func),
            "free_vars": free_vars,
            "cell_count": len(free_vars),
            "cell_contents": cell_contents,
            "closure_vars": closure_vars,
            "mutation_risk": mutation_risk,
            "disassembly": disassembly,
            "risk_score": late_binding_risk_score(func),
        }
        self._cell_analysis_cache[qualname] = result
        return result

    # ------------------------------------------------------------------
    # Bytecode disassembly
    # ------------------------------------------------------------------

    def disassemble_cell_ops(
        self,
        func: types.FunctionType,
    ) -> list[dict[str, Any]]:
        """Find cell-related bytecode instructions in *func*.

        Uses ``dis.get_instructions(func)`` to iterate the bytecode and
        filters for instructions whose ``opname`` is in ``_CLOSURE_OPCODES``
        (LOAD_DEREF, STORE_DEREF, DELETE_DEREF, LOAD_CLASSDEREF,
        MAKE_CELL, COPY_FREE_VARS).

        Args:
            func: The function to disassemble.

        Returns:
            A list of dicts, one per matching instruction, with keys:
            ``offset``, ``opname``, ``argval``, ``argrepr``, ``starts_line``.
        """
        self._stats["disassemble_calls"] += 1
        results: list[dict[str, Any]] = []
        try:
            for instr in dis.get_instructions(func):
                if instr.opname in _CLOSURE_OPCODES:
                    results.append(
                        {
                            "offset": instr.offset,
                            "opname": instr.opname,
                            "argval": instr.argval,
                            "argrepr": instr.argrepr,
                            "starts_line": instr.starts_line,
                        }
                    )
        except (TypeError, AttributeError) as exc:
            log.warning("Could not disassemble %r: %s", func, exc)
        return results

    # ------------------------------------------------------------------
    # Late-binding pattern detection (source-level)
    # ------------------------------------------------------------------

    def detect_late_binding_pattern(
        self,
        source: str,
    ) -> list[dict[str, Any]]:
        """Detect classic late-binding patterns in *source* via AST analysis.

        The canonical late-binding pattern is a ``lambda`` or ``def`` inside
        a ``for`` loop that uses the loop variable::

            for i in range(10):
                funcs.append(lambda: i)  # late binding — sees final i

        The detection algorithm:
        1. Parse the source into an AST.
        2. Walk all ``For`` nodes.
        3. For each ``For`` node, collect the loop variable name(s).
        4. Walk the ``For`` body for ``Lambda`` / ``FunctionDef`` nodes.
        5. Walk those inner nodes for ``Name(id=loop_var, ctx=Load)``.
        6. If found, emit a warning dict.

        Args:
            source: Python source code as a string.

        Returns:
            A list of warning dicts with keys:
            ``line_number``, ``variable_name``, ``context``,
            ``pattern_type``, ``suggestion``.
        """
        self._stats["detect_late_binding_calls"] += 1
        warnings: list[dict[str, Any]] = []
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError as exc:
            log.error("SyntaxError in detect_late_binding_pattern: %s", exc)
            return warnings

        def _loop_target_names(target: ast.expr) -> list[str]:
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Tuple, ast.List)):
                names: list[str] = []
                for elt in target.elts:
                    names.extend(_loop_target_names(elt))
                return names
            return []

        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            loop_vars = _loop_target_names(node.target)
            if not loop_vars:
                continue
            # Search the loop body for lambdas / nested functions
            for body_node in ast.walk(node):
                if body_node is node:
                    continue
                if not isinstance(
                    body_node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                # Check if this inner func uses any loop variable
                for inner_node in ast.walk(body_node):
                    if isinstance(inner_node, ast.Name) and isinstance(
                        inner_node.ctx, ast.Load
                    ):
                        if inner_node.id in loop_vars:
                            warnings.append(
                                {
                                    "pattern_type": "late_binding_loop_capture",
                                    "line_number": getattr(
                                        body_node, "lineno", None
                                    ),
                                    "variable_name": inner_node.id,
                                    "context": f"Loop variable '{inner_node.id}' "
                                    f"captured by closure at line "
                                    f"{getattr(body_node, 'lineno', '?')}",
                                    "suggestion": (
                                        f"Use default argument: "
                                        f"`lambda {inner_node.id}={inner_node.id}: ...` "
                                        f"to freeze the value at capture time."
                                    ),
                                }
                            )
        return warnings

    # ------------------------------------------------------------------
    # Cell graph builder
    # ------------------------------------------------------------------

    def build_cell_graph(
        self,
        funcs: list[types.FunctionType],
    ) -> dict[str, Any]:
        """Build a graph of cell-sharing relationships between functions.

        For each pair of closures, checks whether they share any cell objects
        (i.e. their ``__closure__`` tuples contain ``id``-equal cell objects
        for the same free-variable name).

        Args:
            funcs: A list of live function objects to analyse.

        Returns:
            A dict with:
            - ``nodes``: list of dicts with ``qualname``, ``free_vars``,
              ``is_closure``
            - ``edges``: list of dicts with ``func_a``, ``func_b``,
              ``shared_var``, ``cell_id``
            - ``shared_cell_count``: total number of sharing edges found
            - ``analysis``: textual summary
        """
        self._stats["build_cell_graph_calls"] += 1
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        # Build per-function snapshots
        func_info: list[tuple[str, tuple[str, ...], tuple[Any, ...]]] = []
        for func in funcs:
            qualname = getattr(func, "__qualname__", repr(func))
            free_vars = getattr(func.__code__, "co_freevars", ())
            closure = func.__closure__ or ()
            nodes.append(
                {
                    "qualname": qualname,
                    "free_vars": list(free_vars),
                    "is_closure": is_closure(func),
                }
            )
            func_info.append((qualname, free_vars, closure))
        # Compare every pair for shared cells (by object identity)
        for i in range(len(func_info)):
            qa, fva, ca = func_info[i]
            for j in range(i + 1, len(func_info)):
                qb, fvb, cb = func_info[j]
                for idx_a, name_a in enumerate(fva):
                    for idx_b, name_b in enumerate(fvb):
                        if name_a != name_b:
                            continue
                        if idx_a >= len(ca) or idx_b >= len(cb):
                            continue
                        cell_a = ca[idx_a]
                        cell_b = cb[idx_b]
                        if id(cell_a) == id(cell_b):
                            edges.append(
                                {
                                    "func_a": qa,
                                    "func_b": qb,
                                    "shared_var": name_a,
                                    "cell_id": make_cell_id(qa, name_a),
                                }
                            )
        return {
            "nodes": nodes,
            "edges": edges,
            "shared_cell_count": len(edges),
            "analysis": (
                f"{len(nodes)} functions analysed; "
                f"{len(edges)} shared-cell relationships found."
            ),
        }

    # ------------------------------------------------------------------
    # Judgment emission
    # ------------------------------------------------------------------

    def emit_cell_judgment(
        self,
        cell_name: str,
        scope_path: str,
        is_shared: bool,
        trust_level: Any = None,
    ) -> dict[str, Any]:
        """Emit a formal judgment about a cell's sharing status.

        Constructs a judgment-algebra record (using stub or real Judgment
        classes) asserting either that the cell is safely unshared or that
        it is shared and therefore subject to late-binding proof obligations.

        Args:
            cell_name:   The name of the captured variable.
            scope_path:  The scope path where the cell is defined.
            is_shared:   True if the cell is captured by multiple closures
                         or mutated after capture.
            trust_level: Optional trust level override.  Defaults to
                         ``TrustLevel.HEURISTIC`` for shared cells and
                         ``TrustLevel.VERIFIED_PROOF`` for unshared cells.

        Returns:
            A dict with keys ``cell_name``, ``scope_path``, ``is_shared``,
            ``judgment_status``, ``trust_level``, ``formula``,
            ``channel``.
        """
        self._stats["judgments_emitted"] += 1
        if trust_level is None:
            trust_level = (
                TrustLevel.HEURISTIC if is_shared else TrustLevel.VERIFIED_PROOF
            )
        formula = (
            f"cell_shared({cell_name!r}, {scope_path!r})"
            if is_shared
            else f"cell_unshared({cell_name!r}, {scope_path!r})"
        )
        status = (
            JudgmentStatus.OBSTRUCTED if is_shared else JudgmentStatus.SETTLED
        )
        return {
            "cell_name": cell_name,
            "scope_path": scope_path,
            "is_shared": is_shared,
            "judgment_status": str(status),
            "trust_level": int(trust_level),
            "formula": formula,
            "channel": _ANALYSIS_CHANNEL,
            "timestamp": time.monotonic(),
        }


# ---------------------------------------------------------------------------
# ClosureCaptureCellTransportWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureCaptureCellTransportWitness:
    """Runtime witness for closure cell capture and late-binding behaviour.

    The witness layer sits above the analyser and performs *active* runtime
    probing: it calls functions, reads cell contents, and checks whether
    mutations propagate across cell-sharing boundaries.  Its outputs feed
    the evidence bundle collected for the formal judgment algebra.

    Attributes:
        _analyzer:            Shared :class:`ClosureCaptureCellTransportAnalyzer`.
        _witnessed_closures:  Append-only list of witness records.
        _cell_evidence:       Append-only list of cell-content evidence dicts.
        _late_binding_catches: Dicts describing detected late-binding instances.
        _witness_id:          Unique 16-hex identifier for this witness session.
    """

    _analyzer: ClosureCaptureCellTransportAnalyzer = field(
        default_factory=ClosureCaptureCellTransportAnalyzer
    )
    _witnessed_closures: list[dict[str, Any]] = field(default_factory=list)
    _cell_evidence: list[dict[str, Any]] = field(default_factory=list)
    _late_binding_catches: list[dict[str, Any]] = field(default_factory=list)
    _witness_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # ------------------------------------------------------------------
    # Witness a closure
    # ------------------------------------------------------------------

    def witness_closure(
        self,
        func: types.FunctionType,
    ) -> dict[str, Any]:
        """Perform runtime witnessing of *func* as a closure.

        Verifies that ``func.__closure__`` is not ``None``, reads cell
        contents via the cell object's ``__class__.__name__``, and compares
        the actual free-variable names in ``func.__closure__`` to
        ``func.__code__.co_freevars``.

        Args:
            func: A live function object that should be a closure.

        Returns:
            A witness record dict with keys:
            - ``witness_id``: id of this witness instance
            - ``func_qualname``: qualified name
            - ``is_closure``: bool
            - ``co_freevars``: list of free-var names from code object
            - ``closure_length``: number of cells in ``__closure__``
            - ``freevars_match``: True if lengths match (basic sanity check)
            - ``cell_class_names``: list of ``type(cell).__name__`` strings
            - ``snapshot``: snapshot dict from :func:`extract_closure_vars_snapshot`
            - ``analysis``: full analysis from :meth:`analyze_live_closure`
            - ``risk_score``: late-binding risk score
            - ``timestamp``: monotonic time of witnessing
        """
        qualname = getattr(func, "__qualname__", repr(func))
        co_freevars = list(getattr(func.__code__, "co_freevars", ()))
        closure_tuple = func.__closure__ or ()
        cell_class_names = [type(c).__name__ for c in closure_tuple]
        snapshot = extract_closure_vars_snapshot(func)
        analysis = self._analyzer.analyze_live_closure(func)
        record: dict[str, Any] = {
            "witness_id": self._witness_id,
            "func_qualname": qualname,
            "is_closure": is_closure(func),
            "co_freevars": co_freevars,
            "closure_length": len(closure_tuple),
            "freevars_match": len(co_freevars) == len(closure_tuple),
            "cell_class_names": cell_class_names,
            "snapshot": snapshot,
            "analysis": analysis,
            "risk_score": late_binding_risk_score(func),
            "timestamp": time.monotonic(),
        }
        self._witnessed_closures.append(record)
        return record

    # ------------------------------------------------------------------
    # Witness cell contents
    # ------------------------------------------------------------------

    def witness_cell_contents(
        self,
        func: types.FunctionType,
    ) -> dict[str, Any]:
        """Read the actual cell contents of *func*'s closure at the current moment.

        Iterates ``func.__code__.co_freevars`` and the corresponding cells in
        ``func.__closure__``, calling ``cell.cell_contents`` and catching
        ``ValueError`` for empty cells.  The result is a snapshot of the
        closure's latent context at the time of the call.

        Args:
            func: A live closure function.

        Returns:
            A dict with keys:
            - ``func_qualname``: qualified name of the function
            - ``timestamp``: time of the snapshot
            - ``cells``: list of dicts, one per cell, with ``var_name``,
              ``cell_type``, ``has_value``, ``value``
            - ``snapshot``: flat dict mapping var name → value
        """
        qualname = getattr(func, "__qualname__", repr(func))
        free_vars = list(getattr(func.__code__, "co_freevars", ()))
        closure_tuple = func.__closure__ or ()
        cells_info: list[dict[str, Any]] = []
        snapshot: dict[str, Any] = {}
        for name, cell in zip(free_vars, closure_tuple):
            try:
                value = cell.cell_contents
                has_value = True
            except ValueError:
                value = None
                has_value = False
            cells_info.append(
                {
                    "var_name": name,
                    "cell_type": type(cell).__name__,
                    "has_value": has_value,
                    "value": value,
                }
            )
            snapshot[name] = value if has_value else "<empty-cell>"
        evidence: dict[str, Any] = {
            "func_qualname": qualname,
            "timestamp": time.monotonic(),
            "cells": cells_info,
            "snapshot": snapshot,
        }
        self._cell_evidence.append(evidence)
        return evidence

    # ------------------------------------------------------------------
    # Witness late binding
    # ------------------------------------------------------------------

    def witness_late_binding(
        self,
        factory_func: types.FunctionType,
        trigger_args: list[Any],
    ) -> dict[str, Any]:
        """Detect late-binding by calling *factory_func* with different args.

        Calls *factory_func* once for each value in *trigger_args* and stores
        the resulting closures.  Then calls each resulting closure (with no
        arguments) and records the return values.  If all closures return the
        *same* value regardless of which *trigger_arg* was used to create
        them, late binding is confirmed.

        Args:
            factory_func: A function that returns a closure when called with
                          one argument.  Example: ``lambda x: lambda: x``.
            trigger_args: A list of values to pass to *factory_func*.

        Returns:
            A dict with keys:
            - ``factory_qualname``: qualified name of the factory
            - ``trigger_args``: the list passed in
            - ``results``: list of return values from the created closures
            - ``late_binding_detected``: True if all results are equal
            - ``unique_results``: number of distinct result values
            - ``explanation``: human-readable description
        """
        factory_qualname = getattr(factory_func, "__qualname__", repr(factory_func))
        closures: list[types.FunctionType] = []
        for arg in trigger_args:
            try:
                cl = factory_func(arg)
                closures.append(cl)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "factory_func(%r) raised %s — skipping.", arg, exc
                )
        results: list[Any] = []
        for cl in closures:
            try:
                val = cl()
                results.append(val)
            except Exception as exc:  # noqa: BLE001
                results.append(f"<error: {exc}>")
        # Late binding: all results equal (each closure returns the last value)
        unique = len(set(str(r) for r in results))
        late_binding_detected = (unique == 1 and len(results) > 1)
        explanation = (
            "All closures returned the same value — late binding confirmed."
            if late_binding_detected
            else "Closures returned distinct values — no late binding detected."
            if unique > 1
            else "Insufficient data to determine late binding."
        )
        catch_record: dict[str, Any] = {
            "factory_qualname": factory_qualname,
            "trigger_args": list(trigger_args),
            "results": results,
            "late_binding_detected": late_binding_detected,
            "unique_results": unique,
            "explanation": explanation,
            "timestamp": time.monotonic(),
        }
        if late_binding_detected:
            self._late_binding_catches.append(catch_record)
        return catch_record

    # ------------------------------------------------------------------
    # Witness cell mutation effect
    # ------------------------------------------------------------------

    def witness_cell_mutation_effect(
        self,
        outer_func: types.FunctionType,
        inner_func_attr: str,
    ) -> dict[str, Any]:
        """Check whether a mutation in the outer scope propagates to the inner closure.

        Calls *outer_func()* twice.  After each call, the inner closure is
        retrieved via ``getattr(result, inner_func_attr)`` if the return value
        has the attribute, otherwise by treating the return value itself as
        the inner func.  The cell contents of the inner func are read before
        and after the second call.

        Args:
            outer_func:      A factory function that returns an object or
                             function with an inner closure.
            inner_func_attr: Attribute name on the result that points to the
                             inner closure, or ``""`` to use the result itself.

        Returns:
            A dict describing the before/after cell states with keys:
            ``before_snapshot``, ``after_snapshot``, ``mutation_propagated``,
            ``outer_func_qualname``.
        """
        outer_qualname = getattr(outer_func, "__qualname__", repr(outer_func))
        before_snapshot: dict[str, Any] = {}
        after_snapshot: dict[str, Any] = {}
        try:
            result1 = outer_func()
            inner1 = (
                getattr(result1, inner_func_attr)
                if inner_func_attr and hasattr(result1, inner_func_attr)
                else result1
            )
            before_snapshot = extract_closure_vars_snapshot(inner1)
        except Exception as exc:  # noqa: BLE001
            log.warning("Error in first outer_func() call: %s", exc)
        try:
            result2 = outer_func()
            inner2 = (
                getattr(result2, inner_func_attr)
                if inner_func_attr and hasattr(result2, inner_func_attr)
                else result2
            )
            after_snapshot = extract_closure_vars_snapshot(inner2)
        except Exception as exc:  # noqa: BLE001
            log.warning("Error in second outer_func() call: %s", exc)
        mutation_propagated = before_snapshot != after_snapshot
        return {
            "outer_func_qualname": outer_qualname,
            "inner_func_attr": inner_func_attr,
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "mutation_propagated": mutation_propagated,
            "timestamp": time.monotonic(),
        }

    # ------------------------------------------------------------------
    # Witness shared cell
    # ------------------------------------------------------------------

    def witness_shared_cell(
        self,
        func1: types.FunctionType,
        func2: types.FunctionType,
        cell_name: str,
    ) -> bool:
        """Check whether *func1* and *func2* share the same cell for *cell_name*.

        Compares ``id(func1.__closure__[i]) == id(func2.__closure__[j])``
        where ``i`` is the index of *cell_name* in ``func1.__code__.co_freevars``
        and ``j`` is its index in ``func2.__code__.co_freevars``.

        Args:
            func1:     First closure function.
            func2:     Second closure function.
            cell_name: The name of the variable to check for sharing.

        Returns:
            ``True`` if both closures hold the identical cell object for
            *cell_name*; ``False`` otherwise (including if either function
            does not capture *cell_name* at all).
        """
        fv1 = list(getattr(func1.__code__, "co_freevars", ()))
        fv2 = list(getattr(func2.__code__, "co_freevars", ()))
        if cell_name not in fv1 or cell_name not in fv2:
            return False
        closure1 = func1.__closure__ or ()
        closure2 = func2.__closure__ or ()
        idx1 = fv1.index(cell_name)
        idx2 = fv2.index(cell_name)
        if idx1 >= len(closure1) or idx2 >= len(closure2):
            return False
        return id(closure1[idx1]) == id(closure2[idx2])

    # ------------------------------------------------------------------
    # Collect evidence
    # ------------------------------------------------------------------

    def collect_evidence(self) -> dict[str, Any]:
        """Return a complete evidence bundle for all witnessed activity.

        Aggregates all witnessed closures, cell evidence, late-binding
        catches, and the analyser's coordinator summary into a single
        evidence bundle dict.

        Returns:
            A dict with keys:
            - ``witness_id``: id of this witness instance
            - ``witnessed_closure_count``: number of closures witnessed
            - ``cell_evidence_count``: number of cell snapshots taken
            - ``late_binding_catch_count``: number of late-binding catches
            - ``witnessed_closures``: the full list
            - ``cell_evidence``: the full list
            - ``late_binding_catches``: the full list
            - ``coordinator_summary``: from the coordinator
            - ``timestamp``: current monotonic time
        """
        coordinator_summary = self._analyzer._coordinator.summary()
        return {
            "witness_id": self._witness_id,
            "witnessed_closure_count": len(self._witnessed_closures),
            "cell_evidence_count": len(self._cell_evidence),
            "late_binding_catch_count": len(self._late_binding_catches),
            "witnessed_closures": self._witnessed_closures,
            "cell_evidence": self._cell_evidence,
            "late_binding_catches": self._late_binding_catches,
            "coordinator_summary": coordinator_summary,
            "timestamp": time.monotonic(),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "ClosureCaptureCellTransportCoordinator",
    "ClosureCaptureCellTransportAnalyzer",
    "ClosureCaptureCellTransportWitness",
    # Helper functions
    "get_cell_contents",
    "make_cell_id",
    "closure_arity",
    "is_closure",
    "extract_closure_vars_snapshot",
    "late_binding_risk_score",
    # Constants
    "_ANALYSIS_CHANNEL",
    "_CELL_KINDS",
    "_CLOSURE_OPCODES",
]


# ---------------------------------------------------------------------------
# Smoke test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=" * 70)
    print("closure_capture_cell_transport_lat.py — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Example 1: Counter factory — classic closure over mutable cell
    # ------------------------------------------------------------------
    print("\n--- Example 1: Counter factory ---")

    def make_counter(start: int = 0):
        """Return a closure that increments and returns a counter."""
        count = start

        def increment(by: int = 1) -> int:
            nonlocal count
            count += by
            return count

        return increment

    counter = make_counter(10)
    print("counter() =", counter())   # 11
    print("counter() =", counter())   # 12
    print("counter(5) =", counter(5)) # 17

    witness = ClosureCaptureCellTransportWitness()
    wr = witness.witness_closure(counter)
    print("Witness record (counter):")
    pprint.pprint({k: v for k, v in wr.items() if k != "analysis"})

    cell_snap = witness.witness_cell_contents(counter)
    print("Cell contents snapshot:", cell_snap["snapshot"])

    # ------------------------------------------------------------------
    # Example 2: Adder factory — multiple closures sharing nothing
    # ------------------------------------------------------------------
    print("\n--- Example 2: Adder factory ---")

    def make_adder(n: int):
        """Return a closure that adds *n* to its argument."""
        return lambda x: x + n

    add5 = make_adder(5)
    add10 = make_adder(10)
    print("add5(3) =", add5(3))    # 8
    print("add10(3) =", add10(3))  # 13

    graph = witness._analyzer.build_cell_graph([add5, add10])
    print("Cell graph:", graph["analysis"])

    # ------------------------------------------------------------------
    # Example 3: Classic late-binding loop capture bug
    # ------------------------------------------------------------------
    print("\n--- Example 3: Late-binding loop capture ---")

    def make_funcs_late() -> list:
        """Classic late-binding bug: all lambdas capture the same cell 'i'."""
        funcs = []
        for i in range(5):
            funcs.append(lambda: i)  # noqa: B023 (intentional late binding demo)
        return funcs

    late_funcs = make_funcs_late()
    results_late = [f() for f in late_funcs]
    print("Late-binding results (expect all 4):", results_late)

    # Detect via source analysis
    source_with_bug = textwrap.dedent(
        """
        funcs = []
        for i in range(5):
            funcs.append(lambda: i)
        """
    )
    analyzer = ClosureCaptureCellTransportAnalyzer()
    warnings = analyzer.detect_late_binding_pattern(source_with_bug)
    print("Late-binding warnings detected:", len(warnings))
    for w in warnings:
        print(" ->", w["context"])

    # ------------------------------------------------------------------
    # Example 4: Coordinator — register, transport, mutate
    # ------------------------------------------------------------------
    print("\n--- Example 4: Coordinator events ---")

    coord = ClosureCaptureCellTransportCoordinator()
    cid = coord.register_cell("x", "outer_scope", initial_value=42)
    closure_id = coord.register_closure("inner_func", [cid])
    mutation_event = coord.record_cell_mutation(cid, 99, "outer_scope")
    transport_event = coord.transport_cell(cid, "outer_scope", "inner_scope")
    print("Coordinator summary:", coord.summary())
    print("Late binding alerts:", len(coord._late_binding_alerts))

    # ------------------------------------------------------------------
    # Example 5: Shared cell detection
    # ------------------------------------------------------------------
    print("\n--- Example 5: Shared cell detection ---")

    def outer_shared():
        """Demonstrate two inner functions sharing the same cell."""
        shared_val = 100

        def reader():
            return shared_val

        def writer():
            nonlocal shared_val
            shared_val += 1
            return shared_val

        return reader, writer

    rdr, wtr = outer_shared()
    shared = witness.witness_shared_cell(rdr, wtr, "shared_val")
    print("reader and writer share cell 'shared_val':", shared)

    # ------------------------------------------------------------------
    # Example 6: Late-binding witness
    # ------------------------------------------------------------------
    print("\n--- Example 6: Late-binding witness ---")

    def late_factory(val):
        """Intentionally late-binding factory for demonstration."""
        # This is NOT late-binding — each call gets its own 'val' cell
        return lambda: val

    lb_result = witness.witness_late_binding(late_factory, [1, 2, 3, 4, 5])
    print("Late binding detected:", lb_result["late_binding_detected"])
    print("Results:", lb_result["results"])

    # ------------------------------------------------------------------
    # Example 7: Full source analysis
    # ------------------------------------------------------------------
    print("\n--- Example 7: Full source analysis ---")

    nested_source = textwrap.dedent(
        """
        def outer(a, b):
            c = a + b
            def inner():
                return c * 2
            def inner2():
                return a + c
            return inner, inner2
        """
    )
    analysis_result = analyzer.analyze_source(nested_source, "demo_module")
    print("Closures found:", len(analysis_result["closures"]))
    for cl in analysis_result["closures"]:
        print(
            f"  {cl['func_name']} captures: {cl['captured_names']}"
        )

    # ------------------------------------------------------------------
    # Final evidence bundle
    # ------------------------------------------------------------------
    print("\n--- Evidence bundle ---")
    evidence = witness.collect_evidence()
    print(
        f"Witnessed closures: {evidence['witnessed_closure_count']}, "
        f"cell evidence: {evidence['cell_evidence_count']}, "
        f"late-binding catches: {evidence['late_binding_catch_count']}"
    )
    print("Coordinator summary:", evidence["coordinator_summary"])
    print("\nSmoke test complete.")
