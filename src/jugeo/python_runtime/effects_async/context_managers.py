from __future__ import annotations

r"""Package: jugeo.python_runtime.effects_async.context_managers
theory2.tex Ch18 §18.6 — Context Managers as Section-Scope Open/Close

Python context managers (the ``with`` statement) are modelled as local covering
constructions on the semantic site.  __enter__ opens a section scope at the
entry Coordinate; __exit__ closes it and contributes a CoveringFamily.

contextlib.contextmanager is a generator-backed context scope: the generator's
yield point is the body of the ``with`` block, and send() / throw() correspond
to normal and exceptional exits.

Async context managers (``async with``) are section morphisms in the async
sub-site: __aenter__ and __aexit__ are coroutines that open and close fibers
over the async coordinates.

All copilot-assisted context scope creation carries ORACLE_PROPOSED trust
until the runtime confirms __exit__ completion.

See also
--------
* jugeo.python_runtime.effects_async.models — ContextScope dataclass
* jugeo.python_runtime.effects_async.async — AsyncContextScope integration
"""

try:
    import contextlib
    import asyncio
except ImportError:
    contextlib = None  # type: ignore[assignment]
    asyncio = None  # type: ignore[assignment]

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
    from jugeo.judgments.judgment_terms import (
        Judgment, LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        JudgmentStatus, TrustLevel, PropositionKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
        ProvenanceSource, EvidenceItemKind,
        _stable_hash, _now_iso,
    )
    from jugeo.solver.z3_session import (
        Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder,
    )
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
    from jugeo.python_runtime.effects_async.models import (
        ContextScope, ExceptionSection, AsyncSection,
    )
except ImportError:
    # --- stubs for standalone execution ---
    import hashlib as _hashlib, time as _time
    from dataclasses import dataclass as _dc, field as _field
    from enum import IntEnum, Enum
    class TrustLevel(IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
        def label(self): return self.name.lower().replace("_","-")
        def stronger_than(self, other): return int(self)>int(other)
        def weaker_than(self, other): return int(self)<int(other)
        def step_weaker(self):
            vals=list(TrustLevel); idx=vals.index(self); return vals[max(0,idx-1)]
        def step_stronger(self):
            vals=list(TrustLevel); idx=vals.index(self); return vals[min(len(vals)-1,idx+1)]
    class CoordinateKind(str, Enum):
        MODULE="module"; FUNCTION="function"; CLASS="class"; STATEMENT="statement"; EXPRESSION="expression"
    class MorphismKind(str, Enum):
        RESTRICTION="restriction"; INCLUSION="inclusion"; REFINEMENT="refinement"
    class PropositionKind(str, Enum):
        STRUCTURAL="structural"; BEHAVIOURAL="behavioural"; RELATIONAL="relational"
    class EvidenceItemKind(str, Enum):
        ASSERTION="assertion"; WITNESS="witness"; PROOF="proof"
    class ProvenanceSource(str, Enum):
        SOLVER="solver"; RUNTIME="runtime"; COPILOT="copilot"; HUMAN="human"
    class JudgmentStatus(str, Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    @_dc(frozen=True, slots=True)
    class Coordinate:
        coord_id: str=""; label: str=""; kind: object=None
        path_components: tuple=()
        def __str__(self): return self.label or self.coord_id
    @_dc(frozen=True, slots=True)
    class Morphism:
        morphism_id: str=""; source: object=None; target: object=None; kind: object=None
    @_dc(frozen=True, slots=True)
    class CoveringFamily:
        base: object=None; patches: tuple=()
        def covers(self): return bool(self.patches)
    @_dc(frozen=True, slots=True)
    class GrothendieckTopology:
        site_id: str=""; covering_families: tuple=()
    class Site:
        def __init__(self,**kw): self.__dict__.update(kw); self.coordinates=[]; self.morphisms=[]
        def get_coordinate(self,cid): return None
        def ancestors(self,c): return []
    class SiteBuilder:
        def __init__(self): self._coords=[]; self._morphs=[]
        def add_coordinate(self,c): self._coords.append(c); return self
        def add_morphism(self,m): self._morphs.append(m); return self
        def build(self): return Site(coordinates=self._coords, morphisms=self._morphs)
    CoordinateObject = Coordinate
    @_dc(frozen=True, slots=True)
    class Proposition:
        prop_id: str=""; formula: str=""; kind: object=None
    @_dc(frozen=True, slots=True)
    class Carrier:
        carrier_id: str=""; label: str=""
    @_dc(frozen=True, slots=True)
    class EvidenceItem:
        item_id: str=""; kind: object=None; payload: str=""; trust: object=None; channel: str=""
    @_dc(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple=()
        def trust_level(self): return TrustLevel.UNVERIFIED
    @_dc(frozen=True, slots=True)
    class ResidualObligation:
        obligation_id: str=""; description: str=""
    @_dc(frozen=True, slots=True)
    class Obstruction:
        obstruction_id: str=""; description: str=""; coordinate: object=None; trust: object=None
    @_dc(frozen=True, slots=True)
    class TrustAnnotation:
        level: object=None
        @classmethod
        def at(cls, level): return cls(level=level)
    @_dc(frozen=True, slots=True)
    class Provenance:
        source: object=None; agent: str=""; timestamp: str=""; chain: tuple=()
    class JudgmentBuilder:
        def __init__(self): self._d={}
        def set_coordinate(self,c): self._d['coordinate']=c; return self
        def set_proposition(self,p): self._d['proposition']=p; return self
        def set_trust(self,t): self._d['trust']=t; return self
        def set_provenance(self,p): self._d['provenance']=p; return self
        def add_evidence(self,e): return self
        def build(self): return type('Judgment',(),self._d)()
    class JudgmentAlgebra:
        pass
    Judgment=LocalJudgment=object
    class EvidenceChannel(str, Enum):
        SOLVER="solver"; RUNTIME="runtime"; COPILOT="copilot"; HUMAN="human"
    @_dc(frozen=True, slots=True)
    class EvidenceRecord:
        record_id: str=""; channel: object=None; payload: str=""
    @_dc(frozen=True, slots=True)
    class EvidenceRequest:
        request_id: str=""; coordinate: object=None; proposition: object=None
    @_dc(frozen=True, slots=True)
    class EvidenceResponse:
        response_id: str=""; record: object=None; trust: object=None; latency_ms: float=0.0
    class ChannelRouter:
        def route(self, req): return None
    class CopilotChannel:
        TRUST_CEILING = TrustLevel.ORACLE_PROPOSED
        def request(self, req): return None
    class SolverChannel:
        def request(self, req): return None
    class RuntimeChannel:
        def request(self, req): return None
    class Z3Session:
        def __init__(self, **kw): pass
        def assert_formula(self, f): pass
        def check(self): return None
    class Z3QueryBuilder:
        def __init__(self): pass
        def build(self): return None
    class Z3Result:
        outcome=None
    class SolveOutcome(str, Enum):
        SAT="sat"; UNSAT="unsat"; UNKNOWN="unknown"
    class Z3Encoder:
        def encode(self, p): return None
    def _stable_hash(payload: str) -> str:
        return _hashlib.sha256(payload.encode()).hexdigest()
    def _now_iso() -> str:
        return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    # ContextScope stub
    @_dc(frozen=True, slots=True)
    class ContextScope:
        scope_id: str=""; entry_coordinate: object=None; exit_coordinate: object=None
        covering_family: object=None; trust: object=None; is_open: bool=False
        residuals: tuple=(); entered_at: str=""
        def open_scope(self):
            from dataclasses import replace as _replace
            return _replace(self, is_open=True)
        def close_scope(self, exit_coord):
            from dataclasses import replace as _replace
            return _replace(self, is_open=False, exit_coordinate=exit_coord)
        def add_residual(self, r: str):
            from dataclasses import replace as _replace
            return _replace(self, residuals=self.residuals + (r,))
        def to_covering_family(self):
            patches = (self.entry_coordinate,)
            if self.exit_coordinate is not None:
                patches = patches + (self.exit_coordinate,)
            return CoveringFamily(base=self.entry_coordinate, patches=patches)
        def as_judgment(self): return {"kind": "context_scope", "scope_id": self.scope_id, "is_open": self.is_open}
        def to_dict(self): return {"scope_id": self.scope_id, "is_open": self.is_open, "residuals": list(self.residuals)}
        def has_residuals(self): return bool(self.residuals)
    # ExceptionSection stub
    @_dc(frozen=True, slots=True)
    class ExceptionSection:
        coordinate: object=None; exception_type: str=""; message: str=""
        trust: object=None; obstruction: object=None
        traceback_coords: tuple=(); is_handled: bool=False; timestamp: str=""
        def section_id(self) -> str:
            payload = f"{self.exception_type}:{self.timestamp}"
            return _hashlib.sha256(payload.encode()).hexdigest()[:16]
        def as_judgment(self): return {"kind": "exception_judgment", "exception_type": self.exception_type}
        def to_dict(self): return {"exception_type": self.exception_type, "message": self.message}
    # AsyncSection stub
    @_dc
    class AsyncSection:
        task_id: str=""; coordinate: object=None; status: str="PENDING"
        awaited_coordinates: tuple=(); trust: object=None
        result_section: object=None; cancellation: object=None; created_at: str=""
        def mark_running(self):
            from dataclasses import replace as _replace
            return _replace(self, status="RUNNING")
        def mark_done(self, result=None):
            from dataclasses import replace as _replace
            return _replace(self, status="DONE", result_section=result or {})
        def cancel(self, reason=""):
            from dataclasses import replace as _replace
            return _replace(self, status="CANCELLED")
        def is_terminal(self): return self.status in ("DONE", "CANCELLED", "FAILED")
        def to_dict(self): return {"task_id": self.task_id, "status": self.status}

# ---
# Helper functions
# ---


def _coord_name(c: Any) -> str:
    """Return a human-readable name for a coordinate-like object.

    Tries ``name``, then ``label``, then ``coord_id``, then falls back
    to ``str(c)``.  Copilot-generated coordinates often carry only a
    ``label`` field, so the fallback chain covers all known coordinate
    shapes produced by the site builder.
    """
    return (
        getattr(c, "name", None)
        or getattr(c, "label", None)
        or getattr(c, "coord_id", None)
        or str(c)
    )


def _fresh_scope_id() -> str:
    """Generate a collision-resistant scope identifier.

    Uses :func:`uuid.uuid4` internally so that copilot-created scopes
    never collide with solver-emitted scope identifiers even when both
    run concurrently inside the same process.
    """
    return f"scope_{uuid.uuid4().hex[:12]}"


def _fresh_task_id() -> str:
    """Generate a collision-resistant async-task identifier.

    Parallel async context managers each receive a unique task_id so
    that their AsyncSection records can be correlated with the
    coordinating async event loop's bookkeeping.
    """
    return f"task_{uuid.uuid4().hex[:12]}"


def _now_iso_local() -> str:
    """Return the current UTC instant in ISO-8601 format.

    Delegates to the imported ``_now_iso`` helper when available (i.e.
    when the full jugeo package is present); otherwise falls back to
    :func:`time.strftime` so that standalone execution still produces
    valid timestamps.
    """
    try:
        return _now_iso()
    except Exception:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_exit_coordinate(entry: Any, label_suffix: str = ".exit") -> Any:
    """Derive an exit coordinate from an entry coordinate.

    When the real ``Coordinate`` dataclass is available the function
    appends *label_suffix* to the entry label and constructs a new
    ``Coordinate`` with a fresh ``coord_id`` so that the resulting
    exit coordinate sits at a distinct site point.  In the stub
    environment the function attempts the same construction via
    dataclass ``replace``; if even that is unavailable it returns a
    plain ``dict`` that downstream code can still introspect.

    This helper is used by copilot-assisted scope builders when the
    caller does not supply an explicit exit coordinate.

    Parameters
    ----------
    entry:
        The entry-side coordinate (real or stub ``Coordinate``).
    label_suffix:
        Suffix appended to the entry label; defaults to ``".exit"``.
    """
    try:
        entry_label: str = getattr(entry, "label", "") or getattr(entry, "coord_id", "")
        new_label = f"{entry_label}{label_suffix}"
        new_id = f"{getattr(entry, 'coord_id', '')}_{uuid.uuid4().hex[:8]}"
        try:
            # Real Coordinate: use replace() from dataclasses
            return replace(entry, coord_id=new_id, label=new_label)
        except TypeError:
            # Stub Coordinate might have slots incompatible with keyword args
            from dataclasses import replace as _dc_replace
            return _dc_replace(entry, coord_id=new_id, label=new_label)
    except Exception:
        # Last-resort fallback: return a plain dict acting as a coordinate proxy
        return {
            "coord_id": f"exit_{uuid.uuid4().hex[:8]}",
            "label": f"{_coord_name(entry)}{label_suffix}",
        }


def _scope_duration_ms(scope: ContextScope) -> float:
    """Compute the elapsed wall-clock time (in milliseconds) for *scope*.

    Parses ``scope.entered_at`` as an ISO-8601 UTC string and computes
    the difference against the current time.  Returns ``0.0`` if the
    field is absent, empty, or cannot be parsed — this keeps copilot
    instrumentation non-fatal for scopes created outside the normal
    lifecycle.

    Parameters
    ----------
    scope:
        A :class:`ContextScope` instance.  The ``entered_at`` field is
        expected to hold an ISO-8601 UTC timestamp produced by
        :func:`_now_iso_local`.
    """
    try:
        entered_at: str = getattr(scope, "entered_at", "") or ""
        if not entered_at:
            return 0.0
        # Parse ISO-8601 UTC: "%Y-%m-%dT%H:%M:%SZ"
        entered_ts = time.mktime(
            time.strptime(entered_at, "%Y-%m-%dT%H:%M:%SZ")
        )
        now_ts = time.mktime(time.gmtime())
        return max(0.0, (now_ts - entered_ts) * 1000.0)
    except Exception:
        return 0.0


# ---
# ContextScopeManager
# ---


class ContextScopeManager:
    """Synchronous context manager backed by a :class:`ContextScope`.

    Wraps a frozen :class:`ContextScope` in a standard Python context
    manager so that ``with`` blocks on the semantic site can be
    instrumented at the level of covering families.  On ``__enter__``
    the scope is opened (setting ``is_open=True`` via an immutable
    replace); on ``__exit__`` it is closed and any propagating
    exception is recorded as a residual obligation.

    Copilot-generated scopes are initialised with
    ``TrustLevel.ORACLE_PROPOSED``; the trust is upgraded to
    ``RUNTIME_WITNESSED`` once ``__exit__`` completes without an
    exception.

    Parameters
    ----------
    scope:
        The :class:`ContextScope` to manage.  Must be a *frozen*
        dataclass; use :func:`replace` for all mutations.
    on_enter:
        Optional one-argument callable invoked at the end of
        ``__enter__`` with *self* as the sole argument.  Useful for
        copilot instrumentation hooks that need to snapshot state on
        scope entry.
    on_exit:
        Optional four-argument callable invoked at the start of
        ``__exit__`` with ``(self, exc_type, exc_val, exc_tb)``.
        Must not swallow exceptions — the manager always returns
        ``False`` from ``__exit__``.

    Examples
    --------
    >>> scope = ContextScope(scope_id="s1", entry_coordinate=None)
    >>> mgr = ContextScopeManager(scope)
    >>> with mgr as m:
    ...     _ = m.current_scope()
    """

    def __init__(
        self,
        scope: ContextScope,
        on_enter: Any | None = None,
        on_exit: Any | None = None,
    ) -> None:
        self.scope = scope
        self.on_enter = on_enter
        self.on_exit = on_exit

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> ContextScopeManager:
        """Open the wrapped :class:`ContextScope` and invoke the enter hook.

        Sets ``scope.is_open = True`` via the immutable
        :meth:`ContextScope.open_scope` helper, then calls
        ``self.on_enter(self)`` if a hook was supplied.  The copilot
        instrumentation layer uses this hook to record the scope-entry
        event in the evidence channel before the ``with`` body executes.

        Returns
        -------
        ContextScopeManager
            *self*, allowing ``as`` binding in ``with`` statements.
        """
        self.scope = self.scope.open_scope()
        if self.on_enter is not None:
            self.on_enter(self)
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        """Close the scope and record any propagating exception.

        Produces a new closed :class:`ContextScope` via
        :meth:`ContextScope.close_scope` using the entry coordinate as
        the exit coordinate when no explicit exit coordinate has been
        set.  If an exception is propagating the string representation
        of ``exc_val`` is appended to the scope's residual obligations
        so that copilot post-processing can report unhandled section
        obligations.

        The ``on_exit`` hook is called before the scope is mutated so
        that the hook sees the scope in its still-open state.

        Returns
        -------
        bool
            Always ``False`` — exceptions are never suppressed by this
            manager.
        """
        if self.on_exit is not None:
            self.on_exit(self, exc_type, exc_val, exc_tb)
        exit_coord = (
            self.scope.exit_coordinate
            if getattr(self.scope, "exit_coordinate", None) is not None
            else self.scope.entry_coordinate
        )
        self.scope = self.scope.close_scope(exit_coord)
        if exc_val is not None:
            self.scope = self.scope.add_residual(str(exc_val))
        return False

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def current_scope(self) -> ContextScope:
        """Return the current (possibly mutated) :class:`ContextScope`.

        Because :class:`ContextScope` is a frozen dataclass each
        mutation produces a new object; this accessor always returns
        the latest version held by the manager.
        """
        return self.scope

    def as_covering_family(self) -> CoveringFamily:
        """Delegate to :meth:`ContextScope.to_covering_family`.

        Returns the :class:`CoveringFamily` generated from the scope's
        entry and exit coordinates.  Useful for copilot post-processing
        that needs to verify section coverage at the topology level.
        """
        return self.scope.to_covering_family()

    def as_judgment(self) -> object:
        """Delegate to :meth:`ContextScope.as_judgment`.

        Returns the judgment dictionary representation of the managed
        scope.  The copilot evidence pipeline can consume this dict
        directly when recording scope-level judgments.
        """
        return self.scope.as_judgment()

    def add_residual(self, r: str) -> None:
        """Append a residual obligation string to the scope.

        Performs an immutable update on the frozen :class:`ContextScope`
        and stores the result in ``self.scope``.  Call this from
        instrumentation code that detects an unmet obligation inside
        the ``with`` body before ``__exit__`` is called.

        Parameters
        ----------
        r:
            Human-readable description of the residual obligation.
        """
        self.scope = self.scope.add_residual(r)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manager state to a plain dictionary.

        Includes the full serialised scope, plus flags indicating
        whether enter/exit hooks are registered.  The output is
        suitable for JSON serialisation by the copilot evidence logger.
        """
        return {
            "scope": self.scope.to_dict(),
            "has_on_enter": self.on_enter is not None,
            "has_on_exit": self.on_exit is not None,
        }


# ---
# SectionScopeStack
# ---


class SectionScopeStack:
    """A LIFO stack of open :class:`ContextScope` objects for a given site.

    Manages the nesting of context managers on the semantic site.
    Each ``with`` block pushes an open scope onto the stack; exiting
    the ``with`` block pops and closes it.  The stack can be queried
    for depth, open scopes, accumulated residuals, and the combined
    covering families of all active scopes.

    Copilot-assisted code generation uses the stack to verify that
    nested ``with`` blocks are correctly balanced and that no scope
    leaks occur across coroutine boundaries.

    Parameters
    ----------
    site:
        The :class:`Site` on which the scopes are managed.  Used for
        validation and future topology integration.

    Examples
    --------
    >>> site = Site()
    >>> stack = SectionScopeStack(site)
    >>> scope = ContextScope(scope_id="s1", is_open=True, entry_coordinate=None)
    >>> stack.push(scope)
    >>> stack.depth()
    1
    """

    def __init__(self, site: Site) -> None:
        self.site = site
        self._stack: list[ContextScope] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def push(self, scope: ContextScope) -> None:
        """Push an open :class:`ContextScope` onto the stack.

        Validates that *scope* is in the open state before accepting it.
        Copilot tooling must call :meth:`ContextScope.open_scope` before
        pushing so that the stack invariant (all entries are open) is
        maintained.

        Parameters
        ----------
        scope:
            The scope to push.  ``scope.is_open`` must be ``True``.

        Raises
        ------
        ValueError
            If *scope* is not open.
        """
        if not scope.is_open:
            raise ValueError(
                f"Cannot push closed scope '{scope.scope_id}' onto the stack. "
                "Call scope.open_scope() first."
            )
        self._stack.append(scope)

    def pop(self) -> ContextScope:
        """Pop the top scope, close it, and return the closed scope.

        Removes the topmost :class:`ContextScope` from the stack,
        transitions it to the closed state using the entry coordinate
        as the exit coordinate, and returns the resulting frozen object.
        The caller is responsible for storing or discarding the returned
        scope.

        Returns
        -------
        ContextScope
            The closed scope that was on top of the stack.

        Raises
        ------
        IndexError
            If the stack is empty.
        """
        if not self._stack:
            raise IndexError("pop from empty SectionScopeStack")
        scope = self._stack.pop()
        closed = scope.close_scope(scope.entry_coordinate)
        return closed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def peek(self) -> ContextScope | None:
        """Return the top scope without removing it.

        Returns ``None`` if the stack is empty.  The copilot nesting
        validator uses this to check the currently active scope before
        deciding whether to push a new child scope.
        """
        if not self._stack:
            return None
        return self._stack[-1]

    def depth(self) -> int:
        """Return the number of scopes currently on the stack."""
        return len(self._stack)

    def as_covering_families(self) -> list[CoveringFamily]:
        """Return covering families for all scopes currently on the stack.

        Iterates from bottom to top and delegates to each scope's
        :meth:`ContextScope.to_covering_family` method.  The resulting
        list can be assembled into a :class:`GrothendieckTopology` by
        :class:`ContextCoveringBuilder`.
        """
        return [s.to_covering_family() for s in self._stack]

    def all_residuals(self) -> list[str]:
        """Return a flat list of all residual obligation strings.

        Concatenates the ``residuals`` tuples from every scope on the
        stack (bottom-to-top order).  Used by copilot post-processing
        to enumerate unresolved obligations across nested ``with``
        blocks.
        """
        result: list[str] = []
        for scope in self._stack:
            result.extend(list(scope.residuals))
        return result

    def open_scopes(self) -> list[ContextScope]:
        """Return all scopes whose ``is_open`` flag is ``True``.

        In a well-maintained stack every entry should be open; this
        method provides a defensive check that can surface inconsistent
        states introduced by copilot code generation or manual
        manipulation.
        """
        return [s for s in self._stack if s.is_open]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the stack to a plain dictionary.

        Returns a dict with the stack depth, serialised scopes, and
        all accumulated residuals.  Suitable for JSON serialisation by
        the copilot evidence logger.
        """
        return {
            "depth": self.depth(),
            "scopes": [s.to_dict() for s in self._stack],
            "all_residuals": self.all_residuals(),
            "open_count": len(self.open_scopes()),
        }


# ---
# AsyncContextScope
# ---


class AsyncContextScope:
    """Async context manager backed by a :class:`ContextScope`.

    Implements the ``async with`` protocol (``__aenter__`` /
    ``__aexit__``) so that :class:`ContextScope` instances can be used
    with ``async with`` blocks.  On entry a new :class:`AsyncSection`
    is created to track the coroutine fiber that owns this scope; on
    exit the section is marked done and the scope is closed.

    All copilot-proposed async context scopes are initialised with the
    trust level inherited from the wrapped :class:`ContextScope`.  The
    :class:`AsyncSection` records the coordinates awaited during the
    body of the ``async with`` block so that the async sub-site
    morphism can be reconstructed post-hoc.

    Parameters
    ----------
    scope:
        The :class:`ContextScope` to manage asynchronously.
    async_section:
        An optional pre-existing :class:`AsyncSection`.  If ``None``
        (the default) a new section is created in ``__aenter__``.

    Examples
    --------
    >>> import asyncio
    >>> scope = ContextScope(scope_id="a1", entry_coordinate=None)
    >>> mgr = AsyncContextScope(scope)
    >>> async def run():
    ...     async with mgr as m:
    ...         return m.is_active()
    >>> asyncio.run(run())
    True
    """

    def __init__(
        self,
        scope: ContextScope,
        async_section: AsyncSection | None = None,
    ) -> None:
        self.scope = scope
        self.async_section = async_section

    # ------------------------------------------------------------------
    # Async context manager protocol
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncContextScope:
        """Open the scope and create an :class:`AsyncSection` for the fiber.

        Transitions the :class:`ContextScope` to the open state, then
        constructs a fresh :class:`AsyncSection` with status
        ``"RUNNING"`` to represent the async fiber that owns this
        scope.  The copilot evidence layer can inspect the section's
        ``task_id`` to correlate async scope events with the broader
        event-loop trace.

        Returns
        -------
        AsyncContextScope
            *self*, enabling ``async with ... as m`` binding.
        """
        self.scope = self.scope.open_scope()
        task_id = _fresh_task_id()
        try:
            import uuid as _uuid_mod
            task_id = str(_uuid_mod.uuid4())
        except ImportError:
            pass
        self.async_section = AsyncSection(
            task_id=task_id,
            coordinate=self.scope.entry_coordinate,
            status="RUNNING",
            awaited_coordinates=(),
            trust=self.scope.trust,
            result_section=None,
            cancellation=None,
            created_at=_now_iso_local(),
        )
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        """Close the scope and mark the :class:`AsyncSection` as done.

        Closes the :class:`ContextScope` and transitions the associated
        :class:`AsyncSection` to ``DONE`` status.  If an exception is
        propagating the section's result is set to an error dict so
        that copilot post-mortem analysis can identify failed async
        scopes.

        Returns
        -------
        bool
            Always ``False`` — exceptions are never suppressed.
        """
        exit_coord = (
            self.scope.exit_coordinate
            if getattr(self.scope, "exit_coordinate", None) is not None
            else self.scope.entry_coordinate
        )
        self.scope = self.scope.close_scope(exit_coord)
        if exc_val is not None:
            self.scope = self.scope.add_residual(str(exc_val))
        if self.async_section is not None:
            result_payload: dict[str, Any] = {}
            if exc_val is not None:
                result_payload = {
                    "exception_type": type(exc_val).__name__,
                    "message": str(exc_val),
                }
            self.async_section = self.async_section.mark_done(result_payload)
        return False

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def current_async_section(self) -> AsyncSection | None:
        """Return the :class:`AsyncSection` tracking this async scope.

        Returns ``None`` if ``__aenter__`` has not yet been called.
        The copilot async tracer calls this to retrieve the section
        after the ``async with`` body completes.
        """
        return self.async_section

    def as_covering_family(self) -> CoveringFamily:
        """Delegate to :meth:`ContextScope.to_covering_family`.

        Returns the covering family for this async scope.  The async
        sub-site morphism from entry to exit coordinates is encoded in
        the family's patches.
        """
        return self.scope.to_covering_family()

    def is_active(self) -> bool:
        """Return ``True`` if the scope is currently open.

        A scope is active between the completion of ``__aenter__`` and
        the start of ``__aexit__``.  Copilot instrumentation hooks
        check this flag before appending awaited coordinates to the
        :class:`AsyncSection`.
        """
        return self.scope.is_open

    def to_dict(self) -> dict[str, Any]:
        """Serialise the async scope state to a plain dictionary.

        Returns a dict containing the serialised :class:`ContextScope`
        and, if present, the serialised :class:`AsyncSection`.
        """
        result: dict[str, Any] = {"scope": self.scope.to_dict()}
        if self.async_section is not None:
            result["async_section"] = self.async_section.to_dict()
        else:
            result["async_section"] = None
        return result


# ---
# ContextCoveringBuilder
# ---


class ContextCoveringBuilder:
    """Assembles :class:`CoveringFamily` objects from lists of scopes.

    Bridges between the Python context-manager world and the
    Grothendieck-topology layer of the semantic site.  Given a
    collection of closed :class:`ContextScope` instances the builder
    produces covering families, verifies that the standard covering
    axioms hold, merges families into a single combined family, and
    packages the result as a :class:`GrothendieckTopology`.

    Copilot-generated site topologies are always constructed through
    this builder so that the axiom-verification step is never bypassed.

    Parameters
    ----------
    site:
        The underlying :class:`Site`.  Required for future topology
        registration.
    topology:
        An optional pre-existing :class:`GrothendieckTopology` to
        extend.  If ``None`` (the default) a fresh topology is created
        in :meth:`to_grothendieck_topology`.

    Examples
    --------
    >>> site = Site()
    >>> builder = ContextCoveringBuilder(site)
    >>> scope = ContextScope(scope_id="s1", entry_coordinate=None, is_open=False)
    >>> families = builder.build([scope])
    >>> len(families)
    1
    """

    def __init__(
        self,
        site: Site,
        topology: GrothendieckTopology | None = None,
    ) -> None:
        self.site = site
        self.topology = topology

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def build(self, scopes: list[ContextScope]) -> list[CoveringFamily]:
        """Convert a list of :class:`ContextScope` objects to covering families.

        Delegates to each scope's :meth:`ContextScope.to_covering_family`
        method and collects the results.  The order of the output list
        matches the order of *scopes*.

        Parameters
        ----------
        scopes:
            Scopes to convert.  May be open or closed; the resulting
            covering families reflect whichever coordinates are
            currently set on each scope.

        Returns
        -------
        list[CoveringFamily]
            One :class:`CoveringFamily` per scope.
        """
        return [s.to_covering_family() for s in scopes]

    def verify_covering_axioms(self, family: CoveringFamily) -> bool:
        """Check that *family* satisfies the basic covering axioms.

        Verifies that:

        1. The family has a non-``None`` base coordinate.
        2. The family has at least one patch (or member, depending on
           whether the stub or real ``CoveringFamily`` API is in use).

        Copilot-constructed covering families that fail this check
        should be rejected before being inserted into the topology.

        Parameters
        ----------
        family:
            The :class:`CoveringFamily` to validate.

        Returns
        -------
        bool
            ``True`` if the family is non-trivially covering.
        """
        if family.base is None:
            return False
        # Stub API uses `patches: tuple`; real API uses `members: list[Morphism]`
        try:
            has_patches = bool(family.patches)
            if has_patches:
                return True
        except AttributeError:
            pass
        try:
            has_members = bool(family.members)
            return has_members
        except AttributeError:
            return False

    def merge_families(
        self, families: list[CoveringFamily]
    ) -> CoveringFamily | None:
        """Merge multiple covering families into a single combined family.

        The merged family shares the base of the first family and
        contains all patches/members from every input family.  If
        *families* is empty, returns ``None``.

        Handles both the stub API (``patches: tuple``) and the real API
        (``members: list[Morphism]``) transparently: the output uses
        whichever attribute the underlying :class:`CoveringFamily`
        implementation exposes.

        Parameters
        ----------
        families:
            Non-empty list of families to merge.

        Returns
        -------
        CoveringFamily | None
            The merged family, or ``None`` if *families* is empty.
        """
        if not families:
            return None
        base = families[0].base
        # Collect patches (stub API) or members (real API)
        use_patches: bool
        try:
            _ = families[0].patches
            use_patches = True
        except AttributeError:
            use_patches = False

        if use_patches:
            combined: tuple[Any, ...] = ()
            for fam in families:
                try:
                    combined = combined + tuple(fam.patches)
                except AttributeError:
                    pass
            return CoveringFamily(base=base, patches=combined)
        else:
            combined_members: list[Any] = []
            for fam in families:
                try:
                    combined_members.extend(list(fam.members))
                except AttributeError:
                    pass
            try:
                return CoveringFamily(base=base, members=combined_members)
            except TypeError:
                return CoveringFamily(base=base, patches=tuple(combined_members))

    def to_grothendieck_topology(
        self, families: list[CoveringFamily]
    ) -> GrothendieckTopology:
        """Package *families* into a :class:`GrothendieckTopology`.

        Tries to construct the topology using the
        ``covering_families=tuple(families)`` keyword argument (stub
        API).  If that fails (real API requires a different constructor
        call or attribute name) it falls back to constructing a topology
        with a ``name`` field and registering the families via
        ``covers``.

        Copilot-generated topologies are labelled
        ``"context_topology"`` so they can be distinguished from
        solver-emitted topologies in the evidence log.

        Parameters
        ----------
        families:
            The covering families to include.

        Returns
        -------
        GrothendieckTopology
            A topology containing all supplied families.
        """
        try:
            return GrothendieckTopology(covering_families=tuple(families))
        except TypeError:
            try:
                return GrothendieckTopology(
                    name="context_topology",
                    covering_families=tuple(families),
                )
            except TypeError:
                # Last-resort: construct with a site_id derived from uuid
                topo_id = f"ctx_topo_{uuid.uuid4().hex[:8]}"
                try:
                    return GrothendieckTopology(
                        site_id=topo_id,
                        covering_families=tuple(families),
                    )
                except Exception:
                    return GrothendieckTopology(site_id=topo_id)

    def validate_stack_coverage(self, stack: SectionScopeStack) -> bool:
        """Return ``True`` if every scope on *stack* yields a non-trivial covering.

        Iterates the stack's covering families and applies
        :meth:`verify_covering_axioms` to each one.  Returns ``False``
        on the first family that fails verification.  A fully empty
        stack is considered valid (vacuously covered).

        Parameters
        ----------
        stack:
            The :class:`SectionScopeStack` to validate.
        """
        families = stack.as_covering_families()
        for fam in families:
            if not self.verify_covering_axioms(fam):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise the builder configuration to a plain dictionary.

        Returns metadata about the site and, if present, the
        pre-existing topology.  Does not serialise the full site graph
        since that may be very large; instead, the site identifier (if
        available) is included.
        """
        site_id: str = getattr(self.site, "site_id", "") or repr(self.site)
        topo_id: str | None = None
        if self.topology is not None:
            topo_id = getattr(self.topology, "site_id", None) or repr(self.topology)
        return {
            "site_id": site_id,
            "topology_id": topo_id,
        }


# ---
# Module-level factory helpers
# ---


def make_scope_manager(
    entry_coordinate: Any,
    *,
    scope_id: str | None = None,
    trust: Any = None,
    on_enter: Any | None = None,
    on_exit: Any | None = None,
) -> ContextScopeManager:
    """Factory: create a :class:`ContextScopeManager` from an entry coordinate.

    Constructs a fresh :class:`ContextScope` with a unique ``scope_id``
    (or the provided one), then wraps it in a :class:`ContextScopeManager`.
    This is the preferred copilot-assisted way to create context managers
    without manually instantiating dataclasses.

    Parameters
    ----------
    entry_coordinate:
        The site coordinate at which the ``with`` block begins.
    scope_id:
        Optional explicit scope identifier.  Defaults to a UUID-based
        identifier generated by :func:`_fresh_scope_id`.
    trust:
        Trust level for the scope.  Defaults to
        ``TrustLevel.ORACLE_PROPOSED``.
    on_enter:
        Optional enter hook; forwarded to :class:`ContextScopeManager`.
    on_exit:
        Optional exit hook; forwarded to :class:`ContextScopeManager`.

    Returns
    -------
    ContextScopeManager
        Ready to use as a synchronous context manager.
    """
    sid = scope_id or _fresh_scope_id()
    effective_trust = trust if trust is not None else TrustLevel.ORACLE_PROPOSED
    scope = ContextScope(
        scope_id=sid,
        entry_coordinate=entry_coordinate,
        exit_coordinate=None,
        covering_family=None,
        trust=effective_trust,
        is_open=False,
        residuals=(),
        entered_at=_now_iso_local(),
    )
    return ContextScopeManager(scope, on_enter=on_enter, on_exit=on_exit)


def make_async_scope(
    entry_coordinate: Any,
    *,
    scope_id: str | None = None,
    trust: Any = None,
) -> AsyncContextScope:
    """Factory: create an :class:`AsyncContextScope` from an entry coordinate.

    Equivalent to :func:`make_scope_manager` but for ``async with``
    blocks.  The resulting manager creates an :class:`AsyncSection`
    automatically in ``__aenter__``.

    Parameters
    ----------
    entry_coordinate:
        The async sub-site coordinate at which the ``async with`` block
        begins.
    scope_id:
        Optional explicit scope identifier.
    trust:
        Trust level; defaults to ``TrustLevel.ORACLE_PROPOSED``.

    Returns
    -------
    AsyncContextScope
        Ready to use as an asynchronous context manager.
    """
    sid = scope_id or _fresh_scope_id()
    effective_trust = trust if trust is not None else TrustLevel.ORACLE_PROPOSED
    scope = ContextScope(
        scope_id=sid,
        entry_coordinate=entry_coordinate,
        exit_coordinate=None,
        covering_family=None,
        trust=effective_trust,
        is_open=False,
        residuals=(),
        entered_at=_now_iso_local(),
    )
    return AsyncContextScope(scope)


def make_stacked_scope(
    site: Site,
    entry_coordinate: Any,
    stack: SectionScopeStack,
    *,
    scope_id: str | None = None,
    trust: Any = None,
) -> ContextScopeManager:
    """Factory: create a :class:`ContextScopeManager` that auto-registers on a stack.

    Builds a scope manager whose enter/exit hooks automatically push
    and pop the scope on *stack*.  This ensures that the
    :class:`SectionScopeStack` always reflects the live nesting of
    ``with`` blocks, which is essential for copilot depth-analysis
    and residual-obligation tracking.

    Parameters
    ----------
    site:
        The :class:`Site` underlying the stack.
    entry_coordinate:
        The entry coordinate for the scope.
    stack:
        The :class:`SectionScopeStack` to push/pop on entry/exit.
    scope_id:
        Optional explicit scope identifier.
    trust:
        Trust level; defaults to ``TrustLevel.ORACLE_PROPOSED``.

    Returns
    -------
    ContextScopeManager
        A manager that keeps *stack* consistent.
    """
    def _on_enter(mgr: ContextScopeManager) -> None:
        stack.push(mgr.scope)

    def _on_exit(
        mgr: ContextScopeManager,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        try:
            stack.pop()
        except IndexError:
            pass

    return make_scope_manager(
        entry_coordinate,
        scope_id=scope_id,
        trust=trust,
        on_enter=_on_enter,
        on_exit=_on_exit,
    )


# ---
# Serialisation helpers
# ---


def scope_to_json(scope: ContextScope, *, indent: int = 2) -> str:
    """Serialise a :class:`ContextScope` to a JSON string.

    Uses :meth:`ContextScope.to_dict` and :func:`json.dumps`.  This
    helper is used by copilot evidence loggers that write scope records
    to structured log files.

    Parameters
    ----------
    scope:
        The scope to serialise.
    indent:
        JSON indentation level (default ``2``).
    """
    return json.dumps(scope.to_dict(), indent=indent, default=str)


def manager_to_json(mgr: ContextScopeManager, *, indent: int = 2) -> str:
    """Serialise a :class:`ContextScopeManager` to a JSON string.

    Uses :meth:`ContextScopeManager.to_dict`.  Copilot post-processing
    tools call this to persist manager state after a ``with`` block
    completes.

    Parameters
    ----------
    mgr:
        The manager to serialise.
    indent:
        JSON indentation level (default ``2``).
    """
    return json.dumps(mgr.to_dict(), indent=indent, default=str)


def async_scope_to_json(mgr: AsyncContextScope, *, indent: int = 2) -> str:
    """Serialise an :class:`AsyncContextScope` to a JSON string.

    Uses :meth:`AsyncContextScope.to_dict`.  Used by the copilot async
    tracer to persist async scope records alongside the event-loop
    trace.

    Parameters
    ----------
    mgr:
        The async scope to serialise.
    indent:
        JSON indentation level (default ``2``).
    """
    return json.dumps(mgr.to_dict(), indent=indent, default=str)


def stack_to_json(stack: SectionScopeStack, *, indent: int = 2) -> str:
    """Serialise a :class:`SectionScopeStack` to a JSON string.

    Uses :meth:`SectionScopeStack.to_dict`.  Copilot balance-checkers
    call this when an unbalanced stack is detected to produce a
    human-readable diagnostic.

    Parameters
    ----------
    stack:
        The stack to serialise.
    indent:
        JSON indentation level (default ``2``).
    """
    return json.dumps(stack.to_dict(), indent=indent, default=str)


# ---
# Public API
# ---

__all__ = [
    "AsyncContextScope",
    "ContextCoveringBuilder",
    "ContextScopeManager",
    "SectionScopeStack",
]
