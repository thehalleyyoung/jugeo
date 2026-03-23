from __future__ import annotations

r"""Package: jugeo.python_runtime.effects_async.async_and_task_semantics_suspended
theory2.tex Ch18 §18.S03 — Async and Task Semantics: Suspended State

# copilot: async-task-semantics-suspended — models async/await coroutines as suspended sections in the judgment site, where each await point freezes the coroutine's state at a suspension coordinate and resumes it at a distinct resumption coordinate after the awaitable resolves

Python async coroutines are modelled as suspended sections over the async sub-site.
Each 'await' expression is a suspension coordinate: the coroutine's section is frozen
at that coordinate and only resumes when the awaited object resolves.

A Task is a coordinate in the event loop's site. asyncio.gather creates a covering
family over the async sub-site. Cancellation (task.cancel()) corresponds to an
Obstruction in the sheaf: the cancelled task can no longer provide its section.

The AwaitGraph is a directed graph of await-dependencies between coroutines: an
edge from A to B means coroutine A awaits coroutine B at some suspension point.
This graph encodes the concurrency topology of the async program.

All copilot-assisted async section creation enters at ORACLE_PROPOSED trust until
the runtime confirms task completion.

See also
--------
* jugeo.python_runtime.effects_async.models — AsyncSection, CancellationRecord
* jugeo.python_runtime.effects_async.async — lower-level async section morphisms
* jugeo.python_runtime.effects_async.algorithms — schedule_async_sections
"""

# ---
# Runtime imports — graceful fallback to stubs for standalone execution
# ---

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
except ImportError:
    import hashlib, time
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
    class JudgmentAlgebra: pass
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
        return hashlib.sha256(payload.encode()).hexdigest()
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---
# Standard-library and typing imports
# ---

import ast
import asyncio
import asyncio.coroutines
import hashlib
import inspect
import json
import logging
import sys
import time
import traceback
import types
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Coroutine, Iterator, Optional

# ---
# Local model imports — stubs accepted if package not yet installed
# ---

try:
    from jugeo.python_runtime.effects_async.models import (
        ExceptionSection, ContextScope, AsyncSection, GeneratorSection, CancellationRecord,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class ExceptionSection:
        section_id: str = ""; exc_type_name: str = ""; exc_message: str = ""
        coordinate: object = None; trust: object = None; chained_from: object = None
        traceback_summary: str = ""; is_suppressed: bool = False; raised_at: str = ""
    @dataclass(frozen=True, slots=True)
    class ContextScope:
        scope_id: str = ""; entry_coordinate: object = None; exit_coordinate: object = None
        covering_family: object = None; trust: object = None; is_open: bool = False
        residuals: tuple = (); entered_at: str = ""
    @dataclass(frozen=True, slots=True)
    class AsyncSection:
        section_id: str = ""; coro_name: str = ""; status: str = "pending"
        coordinate: object = None; trust: object = None; result: object = None
        exception: object = None; created_at: str = ""
    @dataclass(frozen=True, slots=True)
    class GeneratorSection:
        section_id: str = ""; gen_name: str = ""; yield_count: int = 0
        coordinate: object = None; trust: object = None; is_exhausted: bool = False
    @dataclass(frozen=True, slots=True)
    class CancellationRecord:
        record_id: str = ""; task_id: str = ""; reason: str = ""
        coordinate: object = None; trust: object = None; cancelled_at: str = ""

# ---
# Module-level constants
# ---

_log = logging.getLogger(__name__)
_ANALYSIS_CHANNEL: str = "copilot-s03-async-task-semantics-suspended"
_SECTION_VERSION: str = "s03.2"
_SUSPENSION_COORDINATE_PREFIX: str = "suspend"
_RESUMPTION_COORDINATE_PREFIX: str = "resume"
_TASK_COORDINATE_PREFIX: str = "task"
_CORO_ID_PREFIX: str = "coro"
_AWAIT_GRAPH_PREFIX: str = "await-graph"
_DEFAULT_TRUST_LEVEL = TrustLevel.ORACLE_PROPOSED
_MAX_SUSPENSION_POINTS: int = 1024
_MAX_TASK_GRAPH_DEPTH: int = 256
_CANCELLED_SECTION_STATUS: str = "cancelled"
_COMPLETED_SECTION_STATUS: str = "completed"
_PENDING_SECTION_STATUS: str = "pending"
_RUNNING_SECTION_STATUS: str = "running"
_GATHER_COVERING_PREFIX: str = "gather-covering"
_CANCELLATION_OBSTRUCTION_PREFIX: str = "cancel-obstruction"
_EVENT_LOOP_SITE_PREFIX: str = "event-loop-site"
_AWAIT_EDGE_PREFIX: str = "await-edge"

# ---
# Core dataclasses — sheaf-theoretic representations of async state
# ---


@dataclass(frozen=True, slots=True)
class SuspendedSection:
    """A coroutine section frozen at an await expression.

    A SuspendedSection represents the state of a coroutine at the moment it
    suspends execution via 'await'. The suspension_site is the source coordinate
    of the await expression. The resume_coordinate is the coordinate at which the
    coroutine will resume once the awaited value is available.

    In sheaf terms: the coroutine is a section of the async sub-site. When it
    suspends, the section is restricted to the suspension_site. When it resumes,
    the section is extended to the resume_coordinate via a morphism.

    local_frame_vars captures a snapshot of local variable names at suspension
    (not the actual values, for safety and serializability).

    Attributes
    ----------
    section_id:
        Globally unique identifier for this suspended section instance.
    coro_id:
        Stable identifier for the coroutine owning this section.
    coro_name:
        Human-readable name of the coroutine function (``co_name``).
    suspension_site:
        Source location string of the await expression, e.g. ``"module:42"``.
    resume_coordinate:
        The Coordinate in the async sub-site where execution will resume.
    suspended_at:
        ISO-8601 timestamp of when the suspension occurred.
    resumed_at:
        ISO-8601 timestamp of when the section was resumed; empty until resumed.
    local_frame_vars:
        Tuple of local variable *names* present at suspension time.
    trust:
        TrustLevel assigned to this section (usually RUNTIME_WITNESSED).
    awaited_coro_id:
        ID of the coroutine being awaited, or empty string if not a coroutine.
    """

    section_id: str
    coro_id: str
    coro_name: str
    suspension_site: str  # source location of the await expression
    resume_coordinate: object  # Coordinate where execution will resume
    suspended_at: str  # ISO timestamp
    resumed_at: str  # ISO timestamp, empty until resumed
    local_frame_vars: tuple  # tuple of str (variable names only)
    trust: object  # TrustLevel
    awaited_coro_id: str  # ID of the coroutine being awaited, empty if not a coroutine

    def is_resumed(self) -> bool:
        """Return True if this section has been resumed.

        A section is considered resumed as soon as ``resumed_at`` is non-empty,
        regardless of whether the coroutine has actually completed execution.
        This matches the sheaf semantics: the section has been extended from
        the suspension coordinate to the resume coordinate.

        Returns
        -------
        bool
            True iff ``resumed_at`` is a non-empty string.
        """
        return bool(self.resumed_at)

    def resume(self, resume_value: object = None) -> "SuspendedSection":
        """Return a new SuspendedSection with resumed_at set to now.

        This is a pure functional update (frozen dataclass); the original
        section is unmodified.  The ``resume_value`` parameter is accepted for
        API symmetry but is not stored (values are not tracked here for safety
        and serializability reasons).

        Parameters
        ----------
        resume_value:
            The value that the awaited expression resolved to.  Not stored.

        Returns
        -------
        SuspendedSection
            New instance identical to self but with ``resumed_at`` filled in.
        """
        return replace(self, resumed_at=_now_iso())

    def suspension_duration_seconds(self) -> float:
        """Estimate suspension duration in seconds from timestamps.

        Parses ``suspended_at`` and ``resumed_at`` (or now if not yet resumed)
        and returns the wall-clock duration of the suspension.  Returns 0.0 on
        any parse error, which can happen when running with stub timestamps.

        Returns
        -------
        float
            Elapsed seconds between suspension and resumption (or now).
        """
        try:
            import datetime
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            t0 = datetime.datetime.strptime(self.suspended_at, fmt)
            t1 = datetime.datetime.strptime(self.resumed_at, fmt) if self.resumed_at else datetime.datetime.utcnow()
            return (t1 - t0).total_seconds()
        except Exception:
            return 0.0


@dataclass(frozen=True, slots=True)
class TaskCoordinate:
    """A coordinate in the event loop's site representing an asyncio Task.

    Each Task is a coordinate in the event loop. Parent tasks are connected
    via morphisms (the 'spawned-by' relation). The status field tracks the
    lifecycle of the task: pending, running, completed, cancelled.

    In sheaf terms: the event loop is a covering topology over the async sub-site.
    Each Task is a patch in the covering. asyncio.gather creates a covering family
    of Tasks over the same base coordinate.

    The ``parent_task_id`` field encodes the spawn-tree morphism: if task B was
    created inside task A, then there is a morphism A → B in the event loop site.
    Root tasks (those spawned directly by ``asyncio.run`` or the REPL) have an
    empty ``parent_task_id``.

    Attributes
    ----------
    task_id:
        Globally unique identifier for this task.
    task_name:
        Human-readable name (usually the coroutine function name).
    event_loop_id:
        Stable string identifier for the hosting event loop.
    parent_task_id:
        ID of the task that spawned this one, or empty for root tasks.
    created_at:
        ISO-8601 timestamp of task creation.
    status:
        One of ``"pending"``, ``"running"``, ``"completed"``, ``"cancelled"``.
    coro_name:
        The ``__name__`` of the underlying coroutine function.
    coordinate:
        The Coordinate in the async sub-site for this task.
    """

    task_id: str
    task_name: str
    event_loop_id: str
    parent_task_id: str  # empty if this is a root task
    created_at: str
    status: str  # "pending", "running", "completed", "cancelled"
    coro_name: str
    coordinate: object  # Coordinate in the async sub-site

    def is_terminal(self) -> bool:
        """Return True if the task is in a terminal state.

        Terminal states are ``"completed"`` and ``"cancelled"``; the task
        cannot transition further from these states.  Used by the AwaitGraph
        to determine when edges can be pruned.

        Returns
        -------
        bool
            True iff ``status`` is ``"completed"`` or ``"cancelled"``.
        """
        return self.status in (_COMPLETED_SECTION_STATUS, _CANCELLED_SECTION_STATUS)

    def is_root(self) -> bool:
        """Return True if this task has no parent task.

        Root tasks sit at the top of the spawn-tree; they correspond to the
        base coordinate in a CoveringFamily when asyncio.gather is called at
        the top level.

        Returns
        -------
        bool
            True iff ``parent_task_id`` is empty.
        """
        return not self.parent_task_id

    def transition(self, new_status: str) -> "TaskCoordinate":
        """Return a new TaskCoordinate with the given status.

        Pure functional update; original is unmodified.  The status string
        must be one of the four lifecycle values; no validation is enforced
        here to keep the dataclass lightweight.

        Parameters
        ----------
        new_status:
            One of ``"pending"``, ``"running"``, ``"completed"``, ``"cancelled"``.

        Returns
        -------
        TaskCoordinate
            New instance with updated status.
        """
        return replace(self, status=new_status)


@dataclass(frozen=True, slots=True)
class AwaitEdge:
    """A directed edge in the AwaitGraph: awaiter_id awaits awaitee_id at site.

    An AwaitEdge encodes a single await-dependency between two coroutines.
    The direction is ``awaiter → awaitee``: the awaiter is blocked waiting for
    the awaitee to produce a value.  In sheaf terms, this is a restriction
    morphism from the awaiter's section to the awaitee's section.

    Attributes
    ----------
    edge_id:
        Globally unique identifier for this edge.
    awaiter_coro_id:
        ID of the coroutine that contains the await expression.
    awaitee_coro_id:
        ID of the coroutine or Future being awaited.
    suspension_site:
        Source location string of the await expression.
    suspension_section_id:
        ID of the SuspendedSection that was created at this edge.
    created_at:
        ISO-8601 timestamp of when the edge was recorded.
    """

    edge_id: str
    awaiter_coro_id: str  # coroutine that contains the await expression
    awaitee_coro_id: str  # coroutine or object being awaited
    suspension_site: str  # source location of the await expression
    suspension_section_id: str  # ID of the SuspendedSection created
    created_at: str


@dataclass(frozen=True, slots=True)
class SuspensionPoint:
    """Metadata about a single await expression found in source code.

    Extracted by the Analyzer from ast.Await nodes.  Each SuspensionPoint
    records the static (compile-time) information about a single await
    expression: its line number, enclosing function, and what kind of object
    is being awaited.

    Attributes
    ----------
    point_id:
        Stable identifier derived from (enclosing_function, lineno, col_offset).
    lineno:
        Line number of the await expression in the source file.
    col_offset:
        Column offset of the await expression.
    awaited_expr_kind:
        One of ``"call"``, ``"name"``, ``"attribute"``, ``"subscript"``, ``"other"``.
    awaited_expr_text:
        Human-readable text representation of the awaited expression.
    enclosing_function:
        Name of the enclosing async function definition.
    is_in_async_for:
        True if this await is part of an ``async for`` loop body.
    is_in_async_with:
        True if this await is part of an ``async with`` block body.
    """

    point_id: str
    lineno: int
    col_offset: int
    awaited_expr_kind: str  # "call", "name", "attribute", "subscript", "other"
    awaited_expr_text: str  # human-readable text of the awaited expression
    enclosing_function: str  # name of the enclosing async function
    is_in_async_for: bool
    is_in_async_with: bool


# ---
# AwaitGraph — mutable directed graph of coroutine await-dependencies
# ---


class AwaitGraph:
    """A directed graph of await-dependencies between coroutines.

    An edge from A to B means coroutine A awaits coroutine B at some point.
    This graph encodes the concurrency topology: it must be a DAG (no circular
    awaiting), otherwise the program would deadlock.

    The graph is used to:
    1. Detect potential deadlocks (cycles in the await graph).
    2. Find the covering families for asyncio.gather calls.
    3. Propagate CancellationRecords through the dependency chain.

    In sheaf-theoretic terms, the AwaitGraph is the nerve of the covering
    topology induced by the event loop.  Each node is a patch (TaskCoordinate)
    and each edge is a restriction morphism.  The covering families extracted
    from ``asyncio.gather`` groups are the open covers of the base coordinate.

    This class is intentionally mutable because it accumulates records as
    coroutines run; it is NOT a frozen dataclass.
    """

    def __init__(self, graph_id: str = "") -> None:
        """Initialise a fresh AwaitGraph.

        Parameters
        ----------
        graph_id:
            Optional stable identifier; one is generated from a UUID hex if
            not provided.
        """
        self.graph_id = graph_id or f"{_AWAIT_GRAPH_PREFIX}-{uuid.uuid4().hex[:8]}"
        # coro_id -> TaskCoordinate (or compatible object)
        self._coroutines: dict[str, object] = {}
        # ordered list of AwaitEdge records
        self._edges: list[AwaitEdge] = []
        # section_id -> SuspendedSection
        self._suspended_sections: dict[str, SuspendedSection] = {}
        # ordered list of CancellationRecord
        self._cancellation_records: list[CancellationRecord] = []
        # gather_id -> list of coro_ids
        self._gather_groups: dict[str, list[str]] = {}
        _log.debug("AwaitGraph created: %s", self.graph_id)

    def add_coroutine(self, task_coord: TaskCoordinate) -> None:
        """Register a coroutine/task in the graph.

        Idempotent: re-registering the same task_id overwrites the previous
        entry.  Used by the coordinator when tasks are created.

        Parameters
        ----------
        task_coord:
            TaskCoordinate to register.
        """
        self._coroutines[task_coord.task_id] = task_coord
        _log.debug("AwaitGraph.add_coroutine: %s (%s)", task_coord.task_id, task_coord.task_name)

    def add_await_edge(self, edge: AwaitEdge) -> None:
        """Add an await-dependency edge to the graph.

        Edges are stored in insertion order.  Duplicate edges (same
        awaiter/awaitee pair) are allowed because the same coroutine pair may
        have multiple await points.

        Parameters
        ----------
        edge:
            AwaitEdge to record.
        """
        self._edges.append(edge)

    def add_suspended_section(self, section: SuspendedSection) -> None:
        """Record a new suspended section.

        The section is indexed by its ``section_id`` for O(1) lookup during
        resumption.

        Parameters
        ----------
        section:
            SuspendedSection to record.
        """
        self._suspended_sections[section.section_id] = section

    def resume_section(self, section_id: str, resume_value: object = None) -> bool:
        """Mark a suspended section as resumed. Returns True if found.

        Updates the SuspendedSection in-place (by replacing the dict entry).
        The ``resume_value`` is passed through to ``SuspendedSection.resume``
        for API compatibility but is not stored.

        Parameters
        ----------
        section_id:
            ID of the SuspendedSection to resume.
        resume_value:
            The value the awaited expression resolved to.

        Returns
        -------
        bool
            True if the section was found and updated; False otherwise.
        """
        if section_id not in self._suspended_sections:
            _log.warning("AwaitGraph.resume_section: unknown section %s", section_id)
            return False
        old = self._suspended_sections[section_id]
        self._suspended_sections[section_id] = old.resume(resume_value)
        return True

    def add_gather_group(self, gather_id: str, coro_ids: list[str]) -> None:
        """Record an asyncio.gather covering family.

        Each gather group corresponds to a CoveringFamily in the sheaf: the
        base is the caller's coordinate, and the patches are the individual
        task coordinates gathered over.

        Parameters
        ----------
        gather_id:
            Stable identifier for this gather call (often derived from source).
        coro_ids:
            List of coroutine/task IDs that form the patches of the covering.
        """
        self._gather_groups[gather_id] = list(coro_ids)
        _log.debug("AwaitGraph.add_gather_group: %s coros=%s", gather_id, coro_ids)

    def record_cancellation(self, task_id: str, reason: str) -> CancellationRecord:
        """Record a task cancellation as an Obstruction in the sheaf.

        A cancelled task can no longer provide its section: the section is
        obstructed.  This method creates a CancellationRecord, appends it to
        the cancellation log, and updates the task's status in the graph.

        Parameters
        ----------
        task_id:
            ID of the task being cancelled.
        reason:
            Human-readable reason for the cancellation.

        Returns
        -------
        CancellationRecord
            The newly created record.
        """
        task = self._coroutines.get(task_id)
        coord = getattr(task, "coordinate", None) if task else None
        record = CancellationRecord(
            record_id=f"{_CANCELLATION_OBSTRUCTION_PREFIX}-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            reason=reason,
            coordinate=coord,
            trust=TrustLevel.RUNTIME_WITNESSED,
            cancelled_at=_now_iso(),
        )
        self._cancellation_records.append(record)
        # Transition the task's status to cancelled in the graph.
        if task_id in self._coroutines:
            old = self._coroutines[task_id]
            if hasattr(old, "transition"):
                self._coroutines[task_id] = old.transition(_CANCELLED_SECTION_STATUS)
        _log.debug("AwaitGraph.record_cancellation: %s reason=%s", task_id, reason)
        return record

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the await graph using DFS. Returns list of cycles found.

        A cycle in the await graph indicates a potential deadlock: two coroutines
        are mutually waiting on each other (e.g. A awaits B while B awaits A).
        The DFS uses a ``visited`` set and a ``in_stack`` set to detect back
        edges; each back edge corresponds to a cycle.

        In sheaf terms, a cycle in the AwaitGraph means the descent data is
        inconsistent: the gluing conditions cannot be satisfied because there
        is no acyclic order in which to evaluate the sections.

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle represented as a list of coroutine IDs
            in traversal order.  Empty list means no cycles detected.
        """
        adjacency: dict[str, list[str]] = {}
        for edge in self._edges:
            adjacency.setdefault(edge.awaiter_coro_id, []).append(edge.awaitee_coro_id)

        visited: set[str] = set()
        in_stack: set[str] = set()
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            in_stack.add(node)
            path.append(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in in_stack:
                    # Back edge found — record the cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(list(path[cycle_start:]))
            path.pop()
            in_stack.discard(node)

        for node in list(adjacency.keys()):
            if node not in visited:
                dfs(node)
        return cycles

    def get_covering_families(self) -> list[CoveringFamily]:
        """Convert asyncio.gather groups into CoveringFamily objects for the sheaf.

        Each gather group becomes a CoveringFamily where the base is the gather
        coordinate and the patches are the individual task coordinates.  This
        makes the gather semantics explicit in the Grothendieck topology: the
        event loop covers the gather coordinate by the union of the gathered
        task patches.

        Returns
        -------
        list[CoveringFamily]
            One CoveringFamily per ``asyncio.gather`` call recorded in this graph.
        """
        families = []
        for gather_id, coro_ids in self._gather_groups.items():
            # The base coordinate represents the gather call itself.
            base_coord = Coordinate(
                coord_id=f"{_GATHER_COVERING_PREFIX}-{gather_id}",
                label=f"gather:{gather_id}",
                kind=CoordinateKind.FUNCTION,
                path_components=(self.graph_id, "gather", gather_id),
            )
            patches = []
            for cid in coro_ids:
                task = self._coroutines.get(cid)
                if task is not None:
                    patches.append(getattr(task, "coordinate", base_coord))
            families.append(CoveringFamily(base=base_coord, patches=tuple(patches)))
        return families

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a plain dict for reporting.

        Produces a JSON-serializable summary suitable for inclusion in
        suspension reports, log messages, and evidence payloads.

        Returns
        -------
        dict
            Keys: graph_id, coroutine_count, edge_count, suspended_section_count,
            cancellation_count, gather_group_count, cycles, coroutines.
        """
        suspended = [s for s in self._suspended_sections.values() if not s.is_resumed()]
        return {
            "graph_id": self.graph_id,
            "coroutine_count": len(self._coroutines),
            "edge_count": len(self._edges),
            "suspended_section_count": len(suspended),
            "cancellation_count": len(self._cancellation_records),
            "gather_group_count": len(self._gather_groups),
            "cycles": self.detect_cycles(),
            "coroutines": [
                {
                    "task_id": tc.task_id if hasattr(tc, "task_id") else str(tc),
                    "task_name": tc.task_name if hasattr(tc, "task_name") else "",
                    "status": tc.status if hasattr(tc, "status") else "",
                }
                for tc in self._coroutines.values()
            ],
        }


# ---
# Analyzer — static AST analysis of async source code
# ---


class AsyncTaskSemanticsSuspendedAnalyzer:
    """Static AST analyzer for async source code.

    This class is responsible for extracting structural information about
    async/await patterns from Python source code.  It uses the ``ast`` module
    to parse source and walk the tree, identifying:

    * async function definitions (``async def``)
    * await expressions (``await <expr>``)
    * asyncio.gather call patterns (CoveringFamily generators)
    * async for loops and async with blocks

    The results drive the coordinator's ``analyze_async_functions`` method and
    populate SuspensionPoint records for the judgment site.

    In sheaf terms, this class performs the *static* portion of the section
    analysis: it finds all potential suspension coordinates before any runtime
    evidence is collected.
    """

    def __init__(self) -> None:
        """Initialise the analyzer with an empty parse cache."""
        # Maps SHA-256(source) -> ast.Module to avoid redundant parses
        self._parse_cache: dict[str, ast.Module] = {}
        # Accumulated analysis results from previous calls
        self._analysis_results: list[dict[str, Any]] = []

    def find_async_functions(self, source: str) -> list[ast.AsyncFunctionDef]:
        """Parse source and return all async function definitions.

        Uses ``ast.walk`` to traverse the full AST, collecting every
        ``ast.AsyncFunctionDef`` node regardless of nesting depth.

        Parameters
        ----------
        source:
            Python source code to analyze.

        Returns
        -------
        list[ast.AsyncFunctionDef]
            All async function definition nodes found in the source.
        """
        tree = self._cached_parse(source)
        return [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]

    def find_await_expressions(self, tree: ast.Module) -> list[ast.Await]:
        """Walk an already-parsed AST and return all await expression nodes.

        Each ``ast.Await`` node corresponds to a single suspension point in
        the sheaf: the coroutine is restricted to that node's coordinate while
        waiting for the awaited expression to resolve.

        Parameters
        ----------
        tree:
            Already-parsed AST module node.

        Returns
        -------
        list[ast.Await]
            All await expression nodes found in the tree.
        """
        return [node for node in ast.walk(tree) if isinstance(node, ast.Await)]

    def detect_asyncio_gather(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find calls to asyncio.gather and related covering-family patterns.

        Covers ``asyncio.gather(...)``, ``gather(...)``, and
        ``asyncio.create_task(...)`` as they all create CoveringFamily patches
        in the event loop's covering topology.

        Parameters
        ----------
        tree:
            Already-parsed AST module node.

        Returns
        -------
        list[dict]
            Each dict has keys: ``pattern`` (str), ``lineno`` (int),
            ``arg_count`` (int).
        """
        results = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_asyncio_gather_call(node):
                results.append({
                    "pattern": "asyncio.gather",
                    "lineno": getattr(node, "lineno", 0),
                    "arg_count": len(node.args) + len(node.keywords),
                })
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "create_task":
                results.append({
                    "pattern": "create_task",
                    "lineno": getattr(node, "lineno", 0),
                    "arg_count": len(node.args),
                })
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "ensure_future":
                results.append({
                    "pattern": "ensure_future",
                    "lineno": getattr(node, "lineno", 0),
                    "arg_count": len(node.args),
                })
        return results

    def find_async_for_loops(self, tree: ast.Module) -> list[ast.AsyncFor]:
        """Walk the AST and return all async for loop nodes.

        Async for loops iterate over an asynchronous iterator; each iteration
        is an implicit suspension point in the sheaf.

        Parameters
        ----------
        tree:
            Already-parsed AST module node.

        Returns
        -------
        list[ast.AsyncFor]
            All async for loop nodes.
        """
        return [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFor)]

    def find_async_with_blocks(self, tree: ast.Module) -> list[ast.AsyncWith]:
        """Walk the AST and return all async with block nodes.

        Async with blocks use an asynchronous context manager; the ``__aenter__``
        and ``__aexit__`` calls are implicit suspension points.

        Parameters
        ----------
        tree:
            Already-parsed AST module node.

        Returns
        -------
        list[ast.AsyncWith]
            All async with block nodes.
        """
        return [node for node in ast.walk(tree) if isinstance(node, ast.AsyncWith)]

    def classify_awaitable_pattern(self, node: ast.Await) -> str:
        """Classify the awaited value expression into a known pattern.

        Inspects the AST structure of the awaited expression to determine
        what kind of awaitable it is.  This classification drives the
        SuspensionPoint's ``awaited_expr_kind`` field.

        Parameters
        ----------
        node:
            The ``ast.Await`` node whose value expression is to be classified.

        Returns
        -------
        str
            One of: ``"coroutine_call"``, ``"task_creation"``, ``"future"``,
            ``"sleep"``, ``"gather"``, ``"shield"``, ``"timeout"``,
            ``"name"``, ``"other"``.
        """
        value = node.value
        if isinstance(value, ast.Call):
            func = value.func
            # Check for asyncio.sleep
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                return "sleep"
            if isinstance(func, ast.Name) and func.id == "sleep":
                return "sleep"
            # Check for asyncio.gather
            if _is_asyncio_gather_call(value):
                return "gather"
            # Check for asyncio.shield
            if isinstance(func, ast.Attribute) and func.attr == "shield":
                return "shield"
            if isinstance(func, ast.Name) and func.id == "shield":
                return "shield"
            # Check for asyncio.wait_for (timeout pattern)
            if isinstance(func, ast.Attribute) and func.attr in ("wait_for", "timeout"):
                return "timeout"
            # Check for create_task / ensure_future
            if isinstance(func, ast.Attribute) and func.attr in ("create_task", "ensure_future"):
                return "task_creation"
            # Default: generic coroutine call
            return "coroutine_call"
        if isinstance(value, ast.Name):
            return "name"
        if isinstance(value, ast.Attribute):
            return "other"
        if isinstance(value, ast.Subscript):
            return "other"
        return "other"

    def build_suspension_map(self, source: str) -> dict[str, list[SuspensionPoint]]:
        """Build a map from async function name to its suspension points.

        Parses the source, walks all async functions, and for each await
        expression inside each function creates a SuspensionPoint.  The
        result is a dict mapping function name to the list of SuspensionPoint
        objects within that function.

        Parameters
        ----------
        source:
            Python source code to analyze.

        Returns
        -------
        dict[str, list[SuspensionPoint]]
            Maps each async function name to its list of SuspensionPoints.
        """
        tree = self._cached_parse(source)
        suspension_map: dict[str, list[SuspensionPoint]] = {}

        for fn_node in ast.walk(tree):
            if not isinstance(fn_node, ast.AsyncFunctionDef):
                continue
            fn_name = fn_node.name
            points: list[SuspensionPoint] = []
            # Collect await expressions within this function body
            for child in ast.walk(fn_node):
                if not isinstance(child, ast.Await):
                    continue
                lineno = getattr(child, "lineno", 0)
                col = getattr(child, "col_offset", 0)
                kind = self.classify_awaitable_pattern(child)
                # Determine if inside async for / async with
                in_async_for = self._is_descendant_of(child, ast.AsyncFor, fn_node)
                in_async_with = self._is_descendant_of(child, ast.AsyncWith, fn_node)
                point_id = f"{_SUSPENSION_COORDINATE_PREFIX}-{_stable_hash(f'{fn_name}:{lineno}:{col}')[:10]}"
                sp = SuspensionPoint(
                    point_id=point_id,
                    lineno=lineno,
                    col_offset=col,
                    awaited_expr_kind=kind,
                    awaited_expr_text=_ast_await_text(child),
                    enclosing_function=fn_name,
                    is_in_async_for=in_async_for,
                    is_in_async_with=in_async_with,
                )
                points.append(sp)
            suspension_map[fn_name] = points

        return suspension_map

    def _cached_parse(self, source: str) -> ast.Module:
        """Parse source code and cache the result by content hash.

        Avoids redundant parses when the same source is analyzed multiple times
        (e.g. during incremental analysis or hot-reload scenarios).

        Parameters
        ----------
        source:
            Python source code to parse.

        Returns
        -------
        ast.Module
            The parsed AST module node.
        """
        key = hashlib.sha256(source.encode()).hexdigest()
        if key not in self._parse_cache:
            self._parse_cache[key] = ast.parse(source)
        return self._parse_cache[key]

    def _find_enclosing_function(self, node: ast.AST, tree: ast.Module) -> str:
        """Find the name of the enclosing AsyncFunctionDef for a given node.

        Walks the entire tree to find which ``ast.AsyncFunctionDef`` contains
        ``node`` in its subtree.  Returns ``"<module>"`` if no enclosing
        async function is found.

        Parameters
        ----------
        node:
            The AST node whose enclosing function we seek.
        tree:
            The root AST module node.

        Returns
        -------
        str
            Name of the enclosing async function, or ``"<module>"``.
        """
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for child in ast.walk(fn):
                if child is node:
                    return fn.name
        return "<module>"

    def _is_descendant_of(self, node: ast.AST, ancestor_type: type, root: ast.AST) -> bool:
        """Return True if ``node`` is a descendant of any ``ancestor_type`` node under ``root``.

        Used to determine if an await expression is inside an ``async for``
        or ``async with`` block.

        Parameters
        ----------
        node:
            The node to test.
        ancestor_type:
            The AST node type to search for as an ancestor.
        root:
            The root of the subtree to search.

        Returns
        -------
        bool
            True if any ancestor of ``node`` (within ``root``) is an instance
            of ``ancestor_type``.
        """
        for candidate in ast.walk(root):
            if not isinstance(candidate, ancestor_type):
                continue
            for child in ast.walk(candidate):
                if child is node:
                    return True
        return False


# ---
# Witness — runtime observation of coroutine lifecycle events
# ---


class AsyncTaskSemanticsSuspendedWitness:
    """Runtime observer for coroutine suspension and resumption events.

    The Witness records the dynamic (runtime) portion of the async section
    analysis.  It observes coroutine creation, suspension, resumption,
    completion, and cancellation, and converts these observations into
    EvidenceItems and SuspendedSections.

    In sheaf terms: the Witness is the evidence-collection layer.  Each
    observation is an EvidenceItem at RUNTIME_WITNESSED trust level.  The
    ``generate_suspension_evidence`` method bundles all items into an
    EvidenceBundle that can be attached to a Judgment.

    This class is mutable and stateful; it accumulates records for the
    lifetime of the program (or until ``clear()`` is called).
    """

    def __init__(self) -> None:
        """Initialise a fresh Witness with empty record stores."""
        # Ordered list of raw observation dicts
        self._records: list[dict[str, Any]] = []
        # coro_id -> coroutine metadata dict
        self._coroutine_registry: dict[str, dict[str, Any]] = {}
        # coro_id -> SuspendedSection (most recent per coro)
        self._suspension_records: dict[str, SuspendedSection] = {}
        # task_id -> TaskCoordinate
        self._task_registry: dict[str, TaskCoordinate] = {}
        # Shared AwaitGraph for this witness
        self._await_graph = AwaitGraph(graph_id=f"{_AWAIT_GRAPH_PREFIX}-witness-{uuid.uuid4().hex[:6]}")
        # Lifecycle counters
        self.create_count: int = 0
        self.suspend_count: int = 0
        self.resume_count: int = 0
        self.complete_count: int = 0
        self.cancel_count: int = 0

    def witness_coroutine_created(self, coro: object, name: str) -> str:
        """Observe a coroutine being created and assign it a stable ID.

        Extracts available metadata from the coroutine object (frame info,
        code object name) and registers it in the coroutine registry.  The
        returned ``coro_id`` is used in subsequent witness calls to correlate
        events with this coroutine.

        Parameters
        ----------
        coro:
            The coroutine object just created (must pass ``inspect.iscoroutine``
            or at minimum be an awaitable).
        name:
            Human-readable name for the coroutine (usually the function name).

        Returns
        -------
        str
            Stable coro_id for this coroutine.
        """
        coro_id = f"{_CORO_ID_PREFIX}-{uuid.uuid4().hex[:10]}"
        # Try to extract frame-level metadata if the coroutine has a frame.
        frame_info: dict[str, Any] = {}
        if inspect.iscoroutine(coro):
            frame = getattr(coro, "cr_frame", None)
            code = getattr(coro, "cr_code", None)
            if frame is not None:
                frame_info["filename"] = getattr(frame, "f_code", None) and frame.f_code.co_filename or ""
                frame_info["lineno"] = getattr(frame, "f_lineno", 0)
            if code is not None:
                frame_info["co_name"] = getattr(code, "co_name", name)
        self._coroutine_registry[coro_id] = {
            "coro_id": coro_id,
            "name": name,
            "frame_info": frame_info,
            "created_at": _now_iso(),
            "status": _PENDING_SECTION_STATUS,
        }
        self._records.append({
            "event": "coroutine_created",
            "coro_id": coro_id,
            "name": name,
            "at": _now_iso(),
        })
        self.create_count += 1
        _log.debug("Witness.witness_coroutine_created: %s name=%s", coro_id, name)
        return coro_id

    def witness_suspension(self, coro_id: str, await_site: str) -> SuspendedSection:
        """Observe a coroutine suspending at an await expression.

        Creates a SuspendedSection for the suspension and registers it in the
        await graph.  If the coroutine ID is not in the registry, a minimal
        entry is created to avoid KeyError.

        Parameters
        ----------
        coro_id:
            ID returned by ``witness_coroutine_created``.
        await_site:
            Human-readable source location of the await expression.

        Returns
        -------
        SuspendedSection
            The newly created suspended section.
        """
        meta = self._coroutine_registry.get(coro_id, {})
        frame_vars: tuple = ()
        # Build a suspended section from available metadata
        section = _suspend_snapshot(coro_id, await_site, frame_vars)
        self._suspension_records[coro_id] = section
        self._await_graph.add_suspended_section(section)
        self._records.append({
            "event": "suspension",
            "coro_id": coro_id,
            "await_site": await_site,
            "section_id": section.section_id,
            "at": _now_iso(),
        })
        self.suspend_count += 1
        # Update registry status to running
        if coro_id in self._coroutine_registry:
            self._coroutine_registry[coro_id]["status"] = _RUNNING_SECTION_STATUS
        _log.debug("Witness.witness_suspension: %s at %s -> %s", coro_id, await_site, section.section_id)
        return section

    def witness_resumption(self, coro_id: str, resume_value: object) -> bool:
        """Observe a coroutine resuming after an awaitable resolves.

        Finds the most recent SuspendedSection for this coroutine and marks
        it as resumed in the await graph.

        Parameters
        ----------
        coro_id:
            ID of the coroutine being resumed.
        resume_value:
            The value that the awaited expression resolved to.

        Returns
        -------
        bool
            True if a suspended section was found and resumed; False otherwise.
        """
        section = self._suspension_records.get(coro_id)
        if section is None:
            _log.warning("Witness.witness_resumption: no suspended section for %s", coro_id)
            return False
        ok = self._await_graph.resume_section(section.section_id, resume_value)
        if ok:
            # Update the local record too
            self._suspension_records[coro_id] = section.resume(resume_value)
            self._records.append({
                "event": "resumption",
                "coro_id": coro_id,
                "section_id": section.section_id,
                "at": _now_iso(),
            })
            self.resume_count += 1
            if coro_id in self._coroutine_registry:
                self._coroutine_registry[coro_id]["status"] = _RUNNING_SECTION_STATUS
        _log.debug("Witness.witness_resumption: %s ok=%s", coro_id, ok)
        return ok

    def witness_task_completed(self, task_id: str, result: object) -> TaskCoordinate:
        """Observe a task completing successfully.

        Updates the task's status in the registry and await graph.  Creates
        a minimal TaskCoordinate entry if the task was not previously registered.

        Parameters
        ----------
        task_id:
            ID of the completed task.
        result:
            The return value of the task's coroutine (not stored for safety).

        Returns
        -------
        TaskCoordinate
            The updated TaskCoordinate with status ``"completed"``.
        """
        task = self._task_registry.get(task_id)
        if task is None:
            # Create a minimal entry for tasks not explicitly registered
            coord = Coordinate(
                coord_id=f"{_TASK_COORDINATE_PREFIX}-{task_id[:10]}",
                label=f"task:{task_id[:10]}",
                kind=CoordinateKind.FUNCTION,
                path_components=(task_id,),
            )
            task = TaskCoordinate(
                task_id=task_id, task_name=task_id[:16],
                event_loop_id=_event_loop_id(), parent_task_id="",
                created_at=_now_iso(), status=_PENDING_SECTION_STATUS,
                coro_name=task_id[:16], coordinate=coord,
            )
        completed = task.transition(_COMPLETED_SECTION_STATUS)
        self._task_registry[task_id] = completed
        # Update the await graph
        if task_id in self._await_graph._coroutines:
            old = self._await_graph._coroutines[task_id]
            if hasattr(old, "transition"):
                self._await_graph._coroutines[task_id] = old.transition(_COMPLETED_SECTION_STATUS)
        self._records.append({
            "event": "task_completed",
            "task_id": task_id,
            "at": _now_iso(),
        })
        self.complete_count += 1
        _log.debug("Witness.witness_task_completed: %s", task_id)
        return completed

    def witness_task_cancelled(self, task_id: str) -> CancellationRecord:
        """Observe a task being cancelled.

        Delegates to ``AwaitGraph.record_cancellation`` to create a
        CancellationRecord (Obstruction in the sheaf) and update task status.

        Parameters
        ----------
        task_id:
            ID of the cancelled task.

        Returns
        -------
        CancellationRecord
            The newly created cancellation record.
        """
        record = self._await_graph.record_cancellation(task_id, "witnessed-cancellation")
        # Also update the local task registry
        if task_id in self._task_registry:
            self._task_registry[task_id] = self._task_registry[task_id].transition(_CANCELLED_SECTION_STATUS)
        self._records.append({
            "event": "task_cancelled",
            "task_id": task_id,
            "record_id": record.record_id,
            "at": _now_iso(),
        })
        self.cancel_count += 1
        _log.debug("Witness.witness_task_cancelled: %s -> %s", task_id, record.record_id)
        return record

    def generate_suspension_evidence(self) -> EvidenceBundle:
        """Convert all witness records to an EvidenceBundle.

        Each raw record in ``self._records`` is converted to an EvidenceItem
        at RUNTIME_WITNESSED trust.  The bundle is suitable for attaching to
        a Judgment in the judgment site.

        Returns
        -------
        EvidenceBundle
            Bundle of all evidence items collected by this witness.
        """
        items = []
        for record in self._records:
            event = record.get("event", "unknown")
            payload = json.dumps({k: str(v) for k, v in record.items()}, separators=(",", ":"))
            item_id = f"evidence-{_stable_hash(payload)[:10]}"
            item = EvidenceItem(
                item_id=item_id,
                kind=EvidenceItemKind.WITNESS,
                payload=payload,
                trust=TrustLevel.RUNTIME_WITNESSED,
                channel=_ANALYSIS_CHANNEL,
            )
            items.append(item)
        return EvidenceBundle(items=tuple(items))

    def get_suspension_summary(self) -> dict[str, Any]:
        """Return a summary dict of all suspension observations.

        Provides aggregate counts and a per-coroutine breakdown of suspension
        state.  Useful for reporting and debugging.

        Returns
        -------
        dict
            Keys: create_count, suspend_count, resume_count, complete_count,
            cancel_count, currently_suspended, by_coro, await_graph.
        """
        currently_suspended = [
            sec for sec in self._suspension_records.values()
            if not sec.is_resumed()
        ]
        by_coro = {}
        for coro_id, meta in self._coroutine_registry.items():
            sec = self._suspension_records.get(coro_id)
            by_coro[coro_id] = {
                "name": meta.get("name", ""),
                "status": meta.get("status", ""),
                "is_suspended": sec is not None and not sec.is_resumed(),
                "section_id": sec.section_id[:16] if sec else "",
            }
        return {
            "create_count": self.create_count,
            "suspend_count": self.suspend_count,
            "resume_count": self.resume_count,
            "complete_count": self.complete_count,
            "cancel_count": self.cancel_count,
            "currently_suspended": len(currently_suspended),
            "by_coro": by_coro,
            "await_graph": self._await_graph.to_dict(),
        }


# ---
# Coordinator — top-level orchestrator for async suspended section analysis
# ---


class AsyncTaskSemanticsSuspendedCoordinator:
    """Top-level coordinator for async/await suspended section analysis.

    This class is the primary entry point for the s03 module.  It orchestrates
    the Analyzer (static AST analysis) and the Witness (runtime observation)
    to build a complete picture of the async/await topology of a program.

    In sheaf terms, the coordinator is the gluing engine: it takes the static
    suspension coordinates (from the Analyzer) and the dynamic evidence (from
    the Witness) and constructs the CoveringFamilies and TaskCoordinates that
    populate the async sub-site.

    Usage
    -----
    ::

        coordinator = AsyncTaskSemanticsSuspendedCoordinator(site_id="my-app")
        report = coordinator.analyze_async_functions(source_code)
        tc = coordinator.build_task_coordinate(my_async_fn, "task-001")
        full = coordinator.get_suspension_report()

    All coroutine sections and task coordinates are accumulated in the
    coordinator's internal state and are available via ``get_suspension_report``.
    """

    def __init__(self, site_id: str = "") -> None:
        """Initialise the coordinator with a fresh site and empty state.

        Parameters
        ----------
        site_id:
            Optional stable identifier for the async sub-site.  One is
            generated from a UUID hex if not provided.
        """
        self.site_id = site_id or f"{_EVENT_LOOP_SITE_PREFIX}-{uuid.uuid4().hex[:8]}"
        self._analyzer = AsyncTaskSemanticsSuspendedAnalyzer()
        self._witness = AsyncTaskSemanticsSuspendedWitness()
        self._await_graph = AwaitGraph(graph_id=f"{_AWAIT_GRAPH_PREFIX}-{self.site_id}")
        self._site_builder = SiteBuilder()
        # task_id -> TaskCoordinate
        self._task_coordinates: dict[str, TaskCoordinate] = {}
        # Accumulated SuspendedSection instances
        self._suspended_sections: list[SuspendedSection] = []
        # Log of analysis events for reporting
        self._analysis_log: list[str] = []
        _log.debug("AsyncTaskSemanticsSuspendedCoordinator created: site_id=%s", self.site_id)

    def analyze_async_functions(self, source: str) -> dict[str, Any]:
        """Parse source and return a summary of async/await patterns found.

        Delegates to the Analyzer to count async functions, await expressions,
        async for loops, async with blocks, and asyncio.gather patterns.  Also
        builds the suspension map (function name -> list of SuspensionPoints).

        Parameters
        ----------
        source:
            Python source code to analyze.

        Returns
        -------
        dict
            Keys: async_function_count, await_expression_count, async_for_count,
            async_with_count, gather_pattern_count, suspension_points, site_id.
        """
        tree = self._analyzer._cached_parse(source)
        async_fns = self._analyzer.find_async_functions(source)
        await_exprs = self._analyzer.find_await_expressions(tree)
        async_fors = self._analyzer.find_async_for_loops(tree)
        async_withs = self._analyzer.find_async_with_blocks(tree)
        gathers = self._analyzer.detect_asyncio_gather(tree)
        suspension_map = self._analyzer.build_suspension_map(source)

        # Flatten suspension points across all functions for the report
        all_points = [
            {
                "function": fn_name,
                "lineno": sp.lineno,
                "kind": sp.awaited_expr_kind,
                "text": sp.awaited_expr_text,
                "in_async_for": sp.is_in_async_for,
                "in_async_with": sp.is_in_async_with,
            }
            for fn_name, points in suspension_map.items()
            for sp in points
        ]

        entry = (
            f"analyze_async_functions: {len(async_fns)} fns, {len(await_exprs)} awaits, "
            f"{len(gathers)} gathers"
        )
        self._analysis_log.append(entry)
        _log.debug(entry)

        return {
            "async_function_count": len(async_fns),
            "await_expression_count": len(await_exprs),
            "async_for_count": len(async_fors),
            "async_with_count": len(async_withs),
            "gather_pattern_count": len(gathers),
            "suspension_points": all_points,
            "site_id": self.site_id,
        }

    def build_task_coordinate(self, coro_func: Callable, task_id: str) -> TaskCoordinate:
        """Build a TaskCoordinate for a coroutine function and register it.

        Verifies that ``coro_func`` is an async function using
        ``inspect.iscoroutinefunction``, then creates a TaskCoordinate and
        registers it in both the internal task store and the await graph.

        Parameters
        ----------
        coro_func:
            The async function (not a coroutine object) to build a coordinate for.
        task_id:
            Stable unique identifier for this task.

        Returns
        -------
        TaskCoordinate
            The newly created task coordinate.

        Raises
        ------
        TypeError
            If ``coro_func`` is not a coroutine function (skipped — we log a
            warning and proceed with the provided name to avoid crashing).
        """
        if not inspect.iscoroutinefunction(coro_func):
            _log.warning("build_task_coordinate: %s is not a coroutine function", coro_func)
        fn_name = getattr(coro_func, "__name__", str(coro_func))
        # Derive a stable event loop ID from the current loop's identity
        loop_id = _event_loop_id()
        # Build the Coordinate for this task in the async sub-site
        coord = Coordinate(
            coord_id=f"{_TASK_COORDINATE_PREFIX}-{_stable_hash(task_id)[:12]}",
            label=f"task:{fn_name}:{task_id[:8]}",
            kind=CoordinateKind.FUNCTION,
            path_components=(self.site_id, "tasks", task_id),
        )
        tc = TaskCoordinate(
            task_id=task_id,
            task_name=fn_name,
            event_loop_id=loop_id,
            parent_task_id="",
            created_at=_now_iso(),
            status=_PENDING_SECTION_STATUS,
            coro_name=fn_name,
            coordinate=coord,
        )
        self._task_coordinates[task_id] = tc
        self._await_graph.add_coroutine(tc)
        self._site_builder.add_coordinate(coord)
        self._analysis_log.append(f"build_task_coordinate: {task_id} fn={fn_name}")
        _log.debug("build_task_coordinate: %s fn=%s", task_id, fn_name)
        return tc

    def map_suspension_points(self, async_fn: Callable) -> list[SuspensionPoint]:
        """Extract all suspension points from an async function's source.

        Uses ``inspect.getsource`` to retrieve the source of the function,
        then delegates to the Analyzer to extract SuspensionPoint records.
        Falls back gracefully to an empty list if the source is unavailable
        (e.g. for built-in or C-extension functions).

        Parameters
        ----------
        async_fn:
            The async function to analyze.

        Returns
        -------
        list[SuspensionPoint]
            All suspension points found in the function's source code.
        """
        try:
            source = inspect.getsource(async_fn)
            # Dedent to handle methods and nested functions
            import textwrap
            source = textwrap.dedent(source)
            fn_name = getattr(async_fn, "__name__", "<unknown>")
            suspension_map = self._analyzer.build_suspension_map(source)
            # Return points for the specific function, or all points if name not found
            return suspension_map.get(fn_name, [])
        except (OSError, TypeError, IndentationError) as exc:
            _log.warning("map_suspension_points: could not get source for %s: %s", async_fn, exc)
            return []

    def compute_resume_coordinates(self, coro: object) -> list[Coordinate]:
        """Compute potential resume coordinates for a coroutine object.

        Inspects the coroutine's current frame to determine what coordinates
        are plausible resume points.  Uses ``coro.cr_frame`` if available.
        Returns a list of Coordinate objects, one per potential resume point.

        Parameters
        ----------
        coro:
            A coroutine object (must pass ``inspect.iscoroutine`` or be a
            ``types.CoroutineType``).

        Returns
        -------
        list[Coordinate]
            List of potential resume coordinates derived from frame info.
        """
        if not (inspect.iscoroutine(coro) or isinstance(coro, types.CoroutineType)):
            _log.debug("compute_resume_coordinates: not a coroutine: %s", type(coro))
            return []
        coords: list[Coordinate] = []
        frame = getattr(coro, "cr_frame", None)
        if frame is not None:
            lineno = getattr(frame, "f_lineno", 0)
            filename = getattr(frame.f_code, "co_filename", "<unknown>") if frame.f_code else "<unknown>"
            co_name = getattr(frame.f_code, "co_name", "<unknown>") if frame.f_code else "<unknown>"
            # Primary resume coordinate: current suspended line
            resume_coord = Coordinate(
                coord_id=f"{_RESUMPTION_COORDINATE_PREFIX}-{_stable_hash(f'{filename}:{lineno}')[:12]}",
                label=f"resume:{co_name}:{lineno}",
                kind=CoordinateKind.STATEMENT,
                path_components=(filename, co_name, str(lineno)),
            )
            coords.append(resume_coord)
            # Secondary: the function's entry coordinate
            entry_coord = Coordinate(
                coord_id=f"{_RESUMPTION_COORDINATE_PREFIX}-entry-{_stable_hash(f'{filename}:{co_name}')[:10]}",
                label=f"entry:{co_name}",
                kind=CoordinateKind.FUNCTION,
                path_components=(filename, co_name, "entry"),
            )
            coords.append(entry_coord)
        else:
            # No frame info available; return a generic resume coordinate
            coro_name = _coro_name(coro)
            generic_coord = Coordinate(
                coord_id=f"{_RESUMPTION_COORDINATE_PREFIX}-generic-{uuid.uuid4().hex[:8]}",
                label=f"resume:{coro_name}",
                kind=CoordinateKind.FUNCTION,
                path_components=(coro_name, "resume"),
            )
            coords.append(generic_coord)
        return coords

    def analyze_task_graph(self, tasks: list) -> dict[str, Any]:
        """Analyze a list of task-like objects and build the await graph.

        Accepts either ``asyncio.Task`` objects or ``TaskCoordinate`` objects.
        For asyncio.Task objects, extracts coroutine name and task identity.
        Builds the await graph and returns a summary.

        Parameters
        ----------
        tasks:
            List of task-like objects to analyze.

        Returns
        -------
        dict
            Keys: task_count, edge_count, suspended_count, covering_family_count,
            cycle_count.
        """
        for task in tasks:
            if isinstance(task, TaskCoordinate):
                self._await_graph.add_coroutine(task)
                self._task_coordinates[task.task_id] = task
            else:
                # Try to extract info from asyncio.Task or similar
                task_id = f"{_TASK_COORDINATE_PREFIX}-{id(task):x}"
                coro = getattr(task, "get_coro", lambda: None)()
                fn_name = _coro_name(coro) if coro is not None else type(task).__name__
                coord = Coordinate(
                    coord_id=f"{_TASK_COORDINATE_PREFIX}-{_stable_hash(task_id)[:10]}",
                    label=f"task:{fn_name}",
                    kind=CoordinateKind.FUNCTION,
                    path_components=(self.site_id, "tasks", task_id),
                )
                tc = TaskCoordinate(
                    task_id=task_id,
                    task_name=fn_name,
                    event_loop_id=_event_loop_id(),
                    parent_task_id="",
                    created_at=_now_iso(),
                    status=_RUNNING_SECTION_STATUS,
                    coro_name=fn_name,
                    coordinate=coord,
                )
                self._await_graph.add_coroutine(tc)
                self._task_coordinates[task_id] = tc

        covering_families = self._await_graph.get_covering_families()
        cycles = self._await_graph.detect_cycles()
        suspended = [s for s in self._await_graph._suspended_sections.values() if not s.is_resumed()]

        return {
            "task_count": len(self._await_graph._coroutines),
            "edge_count": len(self._await_graph._edges),
            "suspended_count": len(suspended),
            "covering_family_count": len(covering_families),
            "cycle_count": len(cycles),
        }

    def get_suspension_report(self) -> dict[str, Any]:
        """Generate a comprehensive suspension report for the async sub-site.

        Aggregates information from the coordinator's internal state, the
        await graph, and the witness to produce a full report suitable for
        logging, monitoring dashboards, or evidence payloads.

        Returns
        -------
        dict
            Keys: section_count, suspended_count, resumed_count, task_count,
            await_graph, analysis_log_tail, generated_at.
        """
        all_sections = list(self._await_graph._suspended_sections.values())
        suspended = [s for s in all_sections if not s.is_resumed()]
        resumed = [s for s in all_sections if s.is_resumed()]
        return {
            "section_count": len(all_sections),
            "suspended_count": len(suspended),
            "resumed_count": len(resumed),
            "task_count": len(self._task_coordinates),
            "await_graph": self._await_graph.to_dict(),
            "analysis_log_tail": self._analysis_log[-10:],
            "generated_at": _now_iso(),
        }

    def classify_awaitable(self, obj: object) -> str:
        """Classify an object as one of the known awaitable kinds.

        Uses a priority-ordered series of ``inspect`` and ``types`` checks to
        determine what kind of awaitable ``obj`` is.  The classification drives
        the ``awaited_coro_id`` field and the edge kind in the AwaitGraph.

        Parameters
        ----------
        obj:
            The object to classify.

        Returns
        -------
        str
            One of: ``"coroutine"``, ``"coroutine_function"``, ``"future"``,
            ``"task"``, ``"async_generator"``, ``"awaitable"``, ``"unknown"``.
        """
        # asyncio.Task is a subclass of asyncio.Future; check it first
        if isinstance(obj, asyncio.Task):
            return "task"
        if isinstance(obj, asyncio.Future):
            return "future"
        # Coroutine objects (already instantiated)
        if inspect.iscoroutine(obj) or isinstance(obj, types.CoroutineType):
            return "coroutine"
        # Async generators
        if isinstance(obj, types.AsyncGeneratorType):
            return "async_generator"
        # Coroutine functions (not yet called)
        if inspect.iscoroutinefunction(obj):
            return "coroutine_function"
        # Generic awaitable (has __await__)
        if inspect.isawaitable(obj):
            return "awaitable"
        return "unknown"


# ---
# Module-level helper functions
# ---


def _coro_name(coro: object) -> str:
    """Extract the name of a coroutine object.

    Tries multiple attributes in priority order: ``__name__``, then
    ``cr_code.co_name``.  Falls back to the type name.

    Parameters
    ----------
    coro:
        Coroutine object whose name is needed.

    Returns
    -------
    str
        Human-readable name of the coroutine.
    """
    if inspect.iscoroutine(coro):
        return (
            getattr(coro, "__name__", None)
            or getattr(getattr(coro, "cr_code", None), "co_name", None)
            or "unknown"
        )
    return getattr(coro, "__name__", type(coro).__name__)


def _coro_frame_vars(coro: object) -> tuple:
    """Extract local variable names from a coroutine's current frame.

    Only variable *names* are extracted, not values, for safety and
    serializability.  Returns an empty tuple if the frame is unavailable
    or an exception occurs.

    Parameters
    ----------
    coro:
        Coroutine object whose frame local names are needed.

    Returns
    -------
    tuple
        Tuple of local variable name strings at the current suspension point.
    """
    try:
        frame = getattr(coro, "cr_frame", None)
        if frame is not None:
            return tuple(frame.f_locals.keys())
    except Exception:
        pass
    return ()


def _event_loop_id() -> str:
    """Get a stable string ID for the current event loop.

    Uses ``id(loop)`` as a hex string, prefixed with ``"loop-"``.  Returns
    ``"loop-none"`` if no event loop is running or accessible.

    Returns
    -------
    str
        String identifier for the current event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        return f"loop-{id(loop):x}"
    except RuntimeError:
        return "loop-none"


def _build_async_section_from_task_coord(tc: TaskCoordinate) -> AsyncSection:
    """Convert a TaskCoordinate to an AsyncSection for judgment site integration.

    Maps the TaskCoordinate's fields to the AsyncSection fields used by the
    judgment site.  The trust level is set to _DEFAULT_TRUST_LEVEL since the
    task coordinate was created by copilot-assisted analysis.

    Parameters
    ----------
    tc:
        TaskCoordinate to convert.

    Returns
    -------
    AsyncSection
        Equivalent AsyncSection suitable for inclusion in a Judgment.
    """
    return AsyncSection(
        section_id=f"async-section-{tc.task_id[:12]}",
        coro_name=tc.coro_name,
        status=tc.status,
        coordinate=tc.coordinate,
        trust=_DEFAULT_TRUST_LEVEL,
        result=None,
        exception=None,
        created_at=tc.created_at,
    )


def _ast_await_text(node: ast.Await) -> str:
    """Get a human-readable text representation of an await expression.

    Produces a concise string like ``"await fetch(...)"`` by inspecting the
    AST node's value expression without evaluating it.

    Parameters
    ----------
    node:
        The ``ast.Await`` node to render.

    Returns
    -------
    str
        Human-readable representation of the await expression.
    """
    value = node.value
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Name):
            return f"await {value.func.id}(...)"
        if isinstance(value.func, ast.Attribute):
            return f"await <expr>.{value.func.attr}(...)"
    if isinstance(value, ast.Name):
        return f"await {value.id}"
    if isinstance(value, ast.Attribute):
        return f"await <expr>.{value.attr}"
    return "await <expr>"


def _is_asyncio_gather_call(node: ast.Call) -> bool:
    """Return True if the call node is asyncio.gather or gather.

    Matches both ``asyncio.gather(...)`` (attribute call) and ``gather(...)``
    (bare name call) to cover cases where ``gather`` was imported directly.

    Parameters
    ----------
    node:
        The ``ast.Call`` node to inspect.

    Returns
    -------
    bool
        True iff the call is a gather invocation.
    """
    if isinstance(node.func, ast.Name) and node.func.id == "gather":
        return True
    if isinstance(node.func, ast.Attribute):
        if node.func.attr == "gather":
            return True
    return False


def _build_cancellation_obstruction(task_id: str, reason: str, coord: object) -> Obstruction:
    """Build an Obstruction representing a cancelled task.

    A cancelled task can no longer provide its section in the sheaf; this
    Obstruction records that fact with a stable ID derived from the task ID.

    Parameters
    ----------
    task_id:
        ID of the cancelled task.
    reason:
        Human-readable reason for the cancellation.
    coord:
        The Coordinate of the cancelled task in the async sub-site.

    Returns
    -------
    Obstruction
        The Obstruction representing the cancelled task's sheaf failure.
    """
    return Obstruction(
        obstruction_id=f"{_CANCELLATION_OBSTRUCTION_PREFIX}-{_stable_hash(task_id)[:8]}",
        description=f"Task {task_id} cancelled: {reason}",
        coordinate=coord,
        trust=TrustLevel.RUNTIME_WITNESSED,
    )


def _suspend_snapshot(coro_id: str, await_site: str, frame_vars: tuple) -> SuspendedSection:
    """Create a SuspendedSection snapshot at a suspension point.

    Builds the resume coordinate from the coro_id and await_site, then
    creates a fresh SuspendedSection with all required fields populated.

    Parameters
    ----------
    coro_id:
        ID of the coroutine being suspended.
    await_site:
        Human-readable source location of the await expression.
    frame_vars:
        Tuple of local variable names at the suspension point.

    Returns
    -------
    SuspendedSection
        A new SuspendedSection ready to be added to the AwaitGraph.
    """
    # The resume coordinate is where execution will continue once the awaitable resolves.
    resume_coord = Coordinate(
        coord_id=f"{_RESUMPTION_COORDINATE_PREFIX}-{coro_id[:8]}-{_stable_hash(await_site)[:6]}",
        label=f"resume:{coro_id[:8]}@{await_site}",
        kind=CoordinateKind.STATEMENT,
        path_components=(coro_id, "resume", await_site),
    )
    return SuspendedSection(
        section_id=f"suspended-{uuid.uuid4().hex[:8]}",
        coro_id=coro_id,
        coro_name=coro_id,
        suspension_site=await_site,
        resume_coordinate=resume_coord,
        suspended_at=_now_iso(),
        resumed_at="",
        local_frame_vars=frame_vars,
        trust=TrustLevel.RUNTIME_WITNESSED,
        awaited_coro_id="",
    )


# ---
# Smoke test
# ---


def _smoke_test() -> None:
    """Quick sanity check for async_and_task_semantics_suspended.

    Exercises: source analysis, task coordinate building, suspension witnessing,
    await graph construction, cancellation recording, and report generation.
    """
    import textwrap

    print("=== async_and_task_semantics_suspended smoke test ===")

    sample_source = textwrap.dedent("""
        import asyncio

        async def fetch_data(url: str) -> bytes:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    return await resp.read()

        async def process_all(urls):
            results = await asyncio.gather(
                *[fetch_data(u) for u in urls],
                return_exceptions=True,
            )
            return results

        async def main():
            try:
                data = await fetch_data("http://example.com")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                pass

            async for item in async_generator():
                await asyncio.sleep(0)
    """)

    coordinator = AsyncTaskSemanticsSuspendedCoordinator(site_id="smoke-test-s03")

    # Test analyze_async_functions
    report = coordinator.analyze_async_functions(sample_source)
    assert report["async_function_count"] >= 3, f"Expected >=3, got {report['async_function_count']}"
    assert report["await_expression_count"] >= 4
    print(f"  analyze_async_functions: {report['async_function_count']} async fns, {report['await_expression_count']} awaits")

    # Test classify_awaitable
    async def _dummy_coro(): pass
    coro = _dummy_coro()
    kind = coordinator.classify_awaitable(coro)
    assert kind in ("coroutine", "coroutine_function", "future", "task", "async_generator", "awaitable", "unknown")
    print(f"  classify_awaitable(coroutine): {kind}")
    try:
        coro.close()  # clean up
    except Exception:
        pass

    # Test build_task_coordinate
    async def _sample(): pass
    tc = coordinator.build_task_coordinate(_sample, f"task-{uuid.uuid4().hex[:6]}")
    assert hasattr(tc, "task_id")
    assert tc.coro_name == "_sample"
    print(f"  build_task_coordinate: {tc.task_id[:20]}, coro={tc.coro_name}")

    # Test SuspendedSection
    sec = _suspend_snapshot("coro-abc", "smoke:10", ("x", "y", "z"))
    assert not sec.is_resumed()
    resumed_sec = sec.resume("some-value")
    assert resumed_sec.is_resumed()
    print(f"  SuspendedSection: suspension_site={sec.suspension_site}, resumed={resumed_sec.is_resumed()}")

    # Test TaskCoordinate
    tc2 = TaskCoordinate(
        task_id="task-smoke-01", task_name="smoke_task", event_loop_id="loop-abc",
        parent_task_id="", created_at=_now_iso(), status=_PENDING_SECTION_STATUS,
        coro_name="smoke_coro",
        coordinate=Coordinate(coord_id="tc-coord", label="smoke", kind=CoordinateKind.FUNCTION, path_components=()),
    )
    assert tc2.is_root()
    tc3 = tc2.transition(_COMPLETED_SECTION_STATUS)
    assert tc3.is_terminal()
    print(f"  TaskCoordinate: is_root={tc2.is_root()}, terminal after transition={tc3.is_terminal()}")

    # Test AwaitGraph
    graph = AwaitGraph(graph_id="smoke-await-graph")
    graph.add_coroutine(tc2)
    sec2 = _suspend_snapshot("task-smoke-01", "smoke:20", ("a", "b"))
    graph.add_suspended_section(sec2)
    edge = AwaitEdge(
        edge_id=f"{_AWAIT_EDGE_PREFIX}-01",
        awaiter_coro_id="task-smoke-01",
        awaitee_coro_id="task-smoke-02",
        suspension_site="smoke:20",
        suspension_section_id=sec2.section_id,
        created_at=_now_iso(),
    )
    graph.add_await_edge(edge)
    cycles = graph.detect_cycles()
    assert isinstance(cycles, list)
    cancel_rec = graph.record_cancellation("task-smoke-01", "smoke-test-cancel")
    assert cancel_rec.task_id == "task-smoke-01"
    print(f"  AwaitGraph: coroutines={len(graph._coroutines)}, edges={len(graph._edges)}, cycles={cycles}, cancel={cancel_rec.record_id[:20]}")

    # Test analyzer
    analyzer = AsyncTaskSemanticsSuspendedAnalyzer()
    async_fns = analyzer.find_async_functions(sample_source)
    tree = ast.parse(sample_source)
    awaits = analyzer.find_await_expressions(tree)
    gathers = analyzer.detect_asyncio_gather(tree)
    async_fors = analyzer.find_async_for_loops(tree)
    async_withs = analyzer.find_async_with_blocks(tree)
    print(f"  analyzer: {len(async_fns)} async fns, {len(awaits)} awaits, {len(gathers)} gathers, {len(async_fors)} async-for, {len(async_withs)} async-with")

    # Test witness
    witness = AsyncTaskSemanticsSuspendedWitness()
    async def _witness_coro(): pass
    wc = _witness_coro()
    coro_id = witness.witness_coroutine_created(wc, "_witness_coro")
    assert isinstance(coro_id, str)
    susp_sec = witness.witness_suspension(coro_id, "smoke:witness-suspend")
    assert hasattr(susp_sec, "section_id")
    resumed_ok = witness.witness_resumption(coro_id, "resume-value")
    print(f"  witness: coro_id={coro_id[:16]}, suspended={susp_sec.section_id[:16]}, resumed={resumed_ok}")
    try:
        wc.close()
    except Exception:
        pass

    # Test get_suspension_report
    full_report = coordinator.get_suspension_report()
    assert "section_count" in full_report
    print(f"  get_suspension_report: {full_report['section_count']} sections, {full_report['task_count']} tasks")

    # Test evidence generation
    evidence = witness.generate_suspension_evidence()
    assert hasattr(evidence, "items")
    print(f"  evidence_bundle: {len(evidence.items)} items")

    print("=== smoke test PASSED ===")


if __name__ == "__main__":
    _smoke_test()
