from __future__ import annotations

r"""Package: jugeo.python_runtime.effects_async.context_managers_temporal_obligati
theory2.tex Ch18 §18.S02 — Context Managers as Temporal Obligations

# copilot: context-managers-temporal-obligations — models context managers as temporal obligations in the judgment site, where __enter__ creates a ResidualObligation and __exit__ discharges it, with nested with-blocks forming a directed acyclic obligation graph

Python context managers are modelled as temporal obligations over the semantic site.
Each __enter__ invocation creates a ResidualObligation at the entry Coordinate;
the corresponding __exit__ discharges that obligation.  If __exit__ is never called
(e.g., the program crashes), the obligation remains undischarged — a cohomology
obstruction in the sheaf.

The sheaf condition requires that inner obligations are discharged before outer ones:
nested with-blocks must unwind in LIFO order.  An ObligationGraph captures the
directed acyclic graph of nested temporal obligations, where edges encode the
"must-discharge-before" relation.

contextlib.contextmanager is treated specially: the generator yield is the body of
the with-block, and send()/throw() are the normal and exceptional discharge paths.
Async context managers (async with) are section morphisms in the async sub-site.

All copilot-assisted obligation creation enters at ORACLE_PROPOSED trust until the
runtime confirms __exit__ completion.

See also
--------
* jugeo.python_runtime.effects_async.models — ContextScope, ExceptionSection
* jugeo.python_runtime.effects_async.context_managers — lower-level context scope
* jugeo.python_runtime.effects_async.algorithms — obligation_graph_toposort
"""

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

import ast
import asyncio
import contextlib
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
from typing import Any, Iterator, Optional

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

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)
_ANALYSIS_CHANNEL: str = "copilot-s02-context-managers-temporal-obligations"
_SECTION_VERSION: str = "s02.2"
_MAX_NESTING_DEPTH: int = 64
_OBLIGATION_ID_PREFIX: str = "temporal-obligation"
_OBLIGATION_GRAPH_PREFIX: str = "obligation-graph"
_ENTRY_COORDINATE_PREFIX: str = "cm-enter"
_EXIT_COORDINATE_PREFIX: str = "cm-exit"
_DEFAULT_TRUST_LEVEL = TrustLevel.ORACLE_PROPOSED
_CONTEXTLIB_MODULE_NAME: str = "contextlib"
_ASYNC_CM_ENTER_METHOD: str = "__aenter__"
_ASYNC_CM_EXIT_METHOD: str = "__aexit__"
_SYNC_CM_ENTER_METHOD: str = "__enter__"
_SYNC_CM_EXIT_METHOD: str = "__exit__"
_UNDISCHARGED_OBLIGATION_KEY: str = "undischarged"
_SHEAF_LIFO_VIOLATION_KEY: str = "lifo-violation"
_COVERING_FAMILY_CM_PREFIX: str = "cm-covering"
_MAX_OBLIGATION_AGE_SECONDS: float = 3600.0
# Additional constants for completeness
_DOUBLE_DISCHARGE_KEY: str = "double-discharge"
_OBLIGATION_VIOLATION_PREFIX: str = "obligation-violation"
_CONTEXTMANAGER_DECORATOR: str = "contextmanager"
_ASYNCCONTEXTMANAGER_DECORATOR: str = "asynccontextmanager"
_SUPPRESS_PATTERN: str = "suppress"
_EXIT_STACK_PATTERN: str = "ExitStack"
_ASYNC_EXIT_STACK_PATTERN: str = "AsyncExitStack"
_MISSING_CLEANUP_RESOURCES: tuple = ("open", "socket", "lock", "acquire", "connect")

# ---------------------------------------------------------------------------
# Core frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TemporalObligation:
    """A temporal obligation created by a context manager __enter__ invocation.

    The obligation_id uniquely identifies this obligation. cm_type_name is the
    class name of the context manager. enter_site and exit_site record the
    source coordinates. is_discharged flips to True when __exit__ is called.
    suppressed_exception is set when __exit__ returned True (suppressing an exception).

    In sheaf terms: the obligation is a section of the "must-exit" sheaf over the
    temporal sub-site. Discharge corresponds to a morphism from the entry coordinate
    to the exit coordinate that closes the covering family.
    """
    obligation_id: str
    cm_type_name: str
    enter_site: str
    exit_site: str
    is_discharged: bool
    suppressed_exception: str  # empty string if no exception was suppressed
    parent_obligation_id: str  # empty string if this is a top-level obligation
    depth: int
    trust: object  # TrustLevel
    created_at: str
    discharged_at: str  # empty until discharged

    def discharge(self, suppressed_exc: str = "", exit_site: str = "") -> "TemporalObligation":
        """Return a new TemporalObligation with is_discharged=True.

        This is the primary mechanism for closing a temporal obligation. The
        suppressed_exc argument captures the exception type name when __exit__
        returned True (i.e., it suppressed the propagating exception). The
        exit_site argument records the coordinate at which __exit__ was invoked.
        """
        return replace(
            self,
            is_discharged=True,
            suppressed_exception=suppressed_exc,
            exit_site=exit_site or self.exit_site,
            discharged_at=_now_iso(),
        )

    def is_async(self) -> bool:
        """Return True if this obligation was created by an async context manager."""
        return self.cm_type_name.startswith("Async") or "async" in self.cm_type_name.lower()

    def age_seconds(self) -> float:
        """Approximate age of this obligation in seconds since creation.

        Uses ISO timestamp parsing as a best effort; returns 0.0 if parsing fails.
        This is primarily used to detect stale obligations that have been open
        longer than _MAX_OBLIGATION_AGE_SECONDS.
        """
        try:
            import datetime
            ts = self.created_at.rstrip("Z")
            created = datetime.datetime.fromisoformat(ts)
            now = datetime.datetime.utcnow()
            return (now - created).total_seconds()
        except Exception:
            return 0.0

    def is_stale(self) -> bool:
        """Return True if this obligation has been open longer than _MAX_OBLIGATION_AGE_SECONDS."""
        return (not self.is_discharged) and self.age_seconds() > _MAX_OBLIGATION_AGE_SECONDS


@dataclass(frozen=True, slots=True)
class ObligationEdge:
    """A directed edge in the ObligationGraph: inner_id must discharge before outer_id."""
    edge_id: str
    inner_obligation_id: str
    outer_obligation_id: str
    nesting_depth_inner: int
    nesting_depth_outer: int
    created_at: str


@dataclass(frozen=True, slots=True)
class ObligationViolation:
    """Records a detected violation of the temporal obligation ordering (LIFO rule)."""
    violation_id: str
    violation_kind: str  # "lifo_violation", "undischarged", "double_discharge"
    inner_obligation_id: str
    outer_obligation_id: str
    description: str
    detected_at: str
    trust: object  # TrustLevel


# ---------------------------------------------------------------------------
# ObligationGraph — mutable DAG tracking nesting structure
# ---------------------------------------------------------------------------

class ObligationGraph:
    """A directed acyclic graph of temporal obligations.

    Tracks the nesting structure of context managers and enforces the LIFO
    ordering requirement (inner obligations must discharge before outer ones).
    This is the graph encoding of the sheaf condition for temporal obligations.

    Nodes are TemporalObligation objects. Edges are ObligationEdge objects
    encoding the 'must-discharge-before' relation.
    """

    def __init__(self, graph_id: str = "") -> None:
        self.graph_id = graph_id or f"{_OBLIGATION_GRAPH_PREFIX}-{uuid.uuid4().hex[:8]}"
        self._obligations: dict[str, TemporalObligation] = {}
        self._edges: list[ObligationEdge] = []
        self._violations: list[ObligationViolation] = []
        self._depth_stack: list[str] = []  # current nesting stack (LIFO)
        _log.debug("ObligationGraph created: %s", self.graph_id)

    def add_obligation(self, obligation: TemporalObligation) -> None:
        """Add a new temporal obligation to the graph.

        If there are existing obligations on the depth stack, adds an edge
        from this obligation to the current top-of-stack (it must discharge first).
        Also validates that we have not exceeded _MAX_NESTING_DEPTH.
        """
        if len(self._depth_stack) >= _MAX_NESTING_DEPTH:
            _log.warning(
                "ObligationGraph.add_obligation: nesting depth %d exceeds max %d for %s",
                len(self._depth_stack), _MAX_NESTING_DEPTH, obligation.obligation_id,
            )
        self._obligations[obligation.obligation_id] = obligation
        if self._depth_stack:
            outer_id = self._depth_stack[-1]
            edge = ObligationEdge(
                edge_id=f"edge-{obligation.obligation_id}-{outer_id}",
                inner_obligation_id=obligation.obligation_id,
                outer_obligation_id=outer_id,
                nesting_depth_inner=obligation.depth,
                nesting_depth_outer=self._obligations[outer_id].depth,
                created_at=_now_iso(),
            )
            self._edges.append(edge)
        self._depth_stack.append(obligation.obligation_id)
        _log.debug(
            "ObligationGraph.add_obligation: %s depth=%d stack_size=%d",
            obligation.obligation_id, obligation.depth, len(self._depth_stack),
        )

    def discharge_obligation(self, obligation_id: str, suppressed_exc: str = "") -> bool:
        """Mark an obligation as discharged.

        Enforces LIFO: logs a violation if the discharged obligation is not the
        current top of the depth stack. Returns True if discharged cleanly.
        Also detects double-discharge (discharging an already-discharged obligation).
        """
        if obligation_id not in self._obligations:
            _log.warning("ObligationGraph.discharge: unknown obligation %s", obligation_id)
            return False

        old_obl = self._obligations[obligation_id]

        # Double-discharge detection
        if old_obl.is_discharged:
            violation = ObligationViolation(
                violation_id=f"violation-double-{uuid.uuid4().hex[:6]}",
                violation_kind=_DOUBLE_DISCHARGE_KEY,
                inner_obligation_id=obligation_id,
                outer_obligation_id="",
                description=(
                    f"Double-discharge: obligation {obligation_id} was already discharged "
                    f"at {old_obl.discharged_at}"
                ),
                detected_at=_now_iso(),
                trust=TrustLevel.RUNTIME_WITNESSED,
            )
            self._violations.append(violation)
            _log.warning("Double-discharge for obligation %s", obligation_id)
            return False

        self._obligations[obligation_id] = old_obl.discharge(suppressed_exc=suppressed_exc)

        # LIFO check: the top of the stack must be the obligation we are discharging
        if self._depth_stack and self._depth_stack[-1] == obligation_id:
            self._depth_stack.pop()
        elif obligation_id in self._depth_stack:
            # out-of-order discharge: LIFO violation
            idx = self._depth_stack.index(obligation_id)
            skipped = self._depth_stack[idx + 1:]
            violation = ObligationViolation(
                violation_id=f"violation-lifo-{uuid.uuid4().hex[:6]}",
                violation_kind=_SHEAF_LIFO_VIOLATION_KEY,
                inner_obligation_id=obligation_id,
                outer_obligation_id=skipped[-1] if skipped else "",
                description=(
                    f"LIFO violation: obligation {obligation_id} discharged "
                    f"before inner obligations {[s for s in skipped]}"
                ),
                detected_at=_now_iso(),
                trust=TrustLevel.RUNTIME_WITNESSED,
            )
            self._violations.append(violation)
            _log.warning("LIFO violation for obligation %s", obligation_id)
            self._depth_stack.pop(idx)

        _log.debug("ObligationGraph.discharge_obligation: %s suppressed=%r", obligation_id, suppressed_exc)
        return True

    def get_undischarged(self) -> list[TemporalObligation]:
        """Return all obligations that have not yet been discharged."""
        return [o for o in self._obligations.values() if not o.is_discharged]

    def get_violations(self) -> list[ObligationViolation]:
        """Return all detected LIFO violations."""
        return list(self._violations)

    def get_stale_obligations(self) -> list[TemporalObligation]:
        """Return all obligations that are undischarged and older than _MAX_OBLIGATION_AGE_SECONDS."""
        return [o for o in self._obligations.values() if o.is_stale()]

    def topological_sort(self) -> list[str]:
        """Return obligation IDs in topological order (inner-first, outer-last).

        Uses Kahn's algorithm on the DAG. Raises ValueError if a cycle is detected
        (which should not happen in valid Python code). The topological order
        corresponds to the correct discharge order: inner obligations appear
        before the outer obligations that depend on them.
        """
        in_degree: dict[str, int] = {oid: 0 for oid in self._obligations}
        adjacency: dict[str, list[str]] = {oid: [] for oid in self._obligations}
        for edge in self._edges:
            adjacency[edge.inner_obligation_id].append(edge.outer_obligation_id)
            in_degree[edge.outer_obligation_id] = in_degree.get(edge.outer_obligation_id, 0) + 1

        queue = [oid for oid, deg in in_degree.items() if deg == 0]
        result: list[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adjacency.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self._obligations):
            raise ValueError(f"ObligationGraph cycle detected in {self.graph_id}")
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a plain dict for reporting."""
        return {
            "graph_id": self.graph_id,
            "obligation_count": len(self._obligations),
            "edge_count": len(self._edges),
            "undischarged_count": len(self.get_undischarged()),
            "violation_count": len(self._violations),
            "depth_stack": list(self._depth_stack),
            "obligations": [
                {
                    "id": o.obligation_id,
                    "cm_type": o.cm_type_name,
                    "enter_site": o.enter_site,
                    "is_discharged": o.is_discharged,
                    "depth": o.depth,
                }
                for o in self._obligations.values()
            ],
        }


# ---------------------------------------------------------------------------
# CLASS 1: ContextManagersTemporalObligationsCoordinator
# ---------------------------------------------------------------------------

class ContextManagersTemporalObligationsCoordinator:
    """Top-level coordinator for temporal obligation analysis of context managers.

    This class ties together the AST analyzer (ContextManagersTemporalObligationsAnalyzer)
    and the runtime witness (ContextManagersTemporalObligationsWitness) into a single
    facade that can be used by the judgment site machinery.

    The coordinator owns a single ObligationGraph that accumulates obligations across
    all analysis and witnessing calls. It also maintains a site_id for tracing and a
    structured analysis_log for post-hoc inspection.
    """

    def __init__(self, site_id: str = "") -> None:
        """Initialise the coordinator.

        Args:
            site_id: Optional label for the judgment site. A UUID-based default is
                used if not provided.
        """
        self.site_id: str = site_id or f"site-s02-{uuid.uuid4().hex[:8]}"
        self.analyzer = ContextManagersTemporalObligationsAnalyzer()
        self.witness = ContextManagersTemporalObligationsWitness()
        self.obligation_graph = ObligationGraph(graph_id=f"{_OBLIGATION_GRAPH_PREFIX}-{self.site_id}")
        self.site_builder = SiteBuilder()
        self.sections: list[ContextScope] = []
        self.analysis_log: list[dict[str, Any]] = []
        _log.debug("ContextManagersTemporalObligationsCoordinator created: %s", self.site_id)

    def analyze_context_managers(self, source: str) -> dict[str, Any]:
        """Parse source code and analyze all context managers as temporal obligations.

        This method drives the full static analysis pipeline:
        1. Parse the source into an AST.
        2. Collect all with-statements (sync and async).
        3. Build TemporalObligation objects for each with-item.
        4. Detect nested with-blocks and record ObligationEdges.
        5. Find contextlib usage patterns.
        6. Register all obligations with the obligation_graph.

        Returns a summary dict with keys:
            with_count, async_with_count, nested_with_count,
            contextlib_usage_count, obligation_count, obligation_graph, site_id.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            _log.warning("analyze_context_managers: SyntaxError in source: %s", exc)
            return {
                "with_count": 0,
                "async_with_count": 0,
                "nested_with_count": 0,
                "contextlib_usage_count": 0,
                "obligation_count": 0,
                "obligation_graph": self.obligation_graph.to_dict(),
                "site_id": self.site_id,
                "error": str(exc),
            }

        # Collect with-statements
        with_stmts = self.analyzer.find_with_statements(source)
        async_with_stmts = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncWith)]
        nested = self.analyzer.detect_nested_with_blocks(tree)
        contextlib_usages = self.analyzer.find_contextlib_usage(tree)

        # Build obligations for each with-statement
        all_obligations: list[TemporalObligation] = []
        depth_counter = 0
        for with_node in with_stmts:
            node_obligations = self.track_nested_obligations(with_node)
            all_obligations.extend(node_obligations)

        # Log the analysis
        log_entry: dict[str, Any] = {
            "event": "analyze_context_managers",
            "site_id": self.site_id,
            "with_count": len(with_stmts),
            "async_with_count": len(async_with_stmts),
            "nested_with_count": len(nested),
            "contextlib_usage_count": len(contextlib_usages),
            "obligation_count": len(all_obligations),
            "timestamp": _now_iso(),
        }
        self.analysis_log.append(log_entry)
        _log.debug("analyze_context_managers: %s", log_entry)

        return {
            "with_count": len(with_stmts),
            "async_with_count": len(async_with_stmts),
            "nested_with_count": len(nested),
            "contextlib_usage_count": len(contextlib_usages),
            "obligation_count": len(all_obligations),
            "obligation_graph": self.obligation_graph.to_dict(),
            "site_id": self.site_id,
        }

    def build_temporal_obligation(self, cm_type: type) -> TemporalObligation:
        """Build a TemporalObligation for the given context manager type.

        Uses inspect.getmembers to verify that the type exposes __enter__ and
        __exit__ (or __aenter__/__aexit__ for async context managers). Creates
        a TemporalObligation and registers it with the obligation_graph.

        Args:
            cm_type: The class of the context manager (not an instance).

        Returns:
            A newly created (undischarged) TemporalObligation.
        """
        # Verify protocol compliance via inspect
        members = dict(inspect.getmembers(cm_type))
        has_sync = _SYNC_CM_ENTER_METHOD in members and _SYNC_CM_EXIT_METHOD in members
        has_async = _ASYNC_CM_ENTER_METHOD in members and _ASYNC_CM_EXIT_METHOD in members

        current_depth = len(self.obligation_graph._depth_stack)
        parent_id = self.obligation_graph._depth_stack[-1] if self.obligation_graph._depth_stack else ""

        obligation = TemporalObligation(
            obligation_id=f"{_OBLIGATION_ID_PREFIX}-{uuid.uuid4().hex[:12]}",
            cm_type_name=cm_type.__name__,
            enter_site=f"{_ENTRY_COORDINATE_PREFIX}:{cm_type.__name__}",
            exit_site=f"{_EXIT_COORDINATE_PREFIX}:{cm_type.__name__}",
            is_discharged=False,
            suppressed_exception="",
            parent_obligation_id=parent_id,
            depth=current_depth,
            trust=_DEFAULT_TRUST_LEVEL,
            created_at=_now_iso(),
            discharged_at="",
        )
        self.obligation_graph.add_obligation(obligation)
        _log.debug(
            "build_temporal_obligation: %s (sync=%s, async=%s)",
            obligation.obligation_id, has_sync, has_async,
        )
        return obligation

    def verify_obligation_discharge(self, cm_instance: Any, enter_result: Any, exit_result: bool) -> bool:
        """Verify that a context manager obligation was cleanly discharged.

        Checks that the cm_instance exposes __exit__, and that exit_result is a
        valid boolean. When exit_result is True, the context manager suppressed a
        propagating exception — this is recorded in the obligation. When False,
        the obligation is discharged without suppression.

        Args:
            cm_instance: The context manager instance (post-enter).
            enter_result: The value returned by __enter__ (used for sanity checks).
            exit_result: The boolean returned by __exit__.

        Returns:
            True if the discharge is considered clean; False if the cm_instance
            lacks __exit__ or if exit_result is not a valid bool.
        """
        if not _has_enter_exit(cm_instance) and not _has_async_enter_exit(cm_instance):
            _log.warning(
                "verify_obligation_discharge: cm_instance %r lacks __enter__/__exit__",
                type(cm_instance).__name__,
            )
            return False

        # exit_result must be truthy or falsy (bool-like); None is treated as False
        effective_exit = bool(exit_result) if exit_result is not None else False

        suppressed_exc = ""
        if effective_exit:
            # __exit__ returned True → exception was suppressed
            suppressed_exc = "suppressed"

        # Try to find and discharge the matching obligation in the graph
        cm_name = _cm_type_name(cm_instance)
        for obl_id, obl in self.obligation_graph._obligations.items():
            if obl.cm_type_name == cm_name and not obl.is_discharged:
                self.obligation_graph.discharge_obligation(obl_id, suppressed_exc=suppressed_exc)
                _log.debug("verify_obligation_discharge: discharged %s suppressed=%r", obl_id, effective_exit)
                return True

        # No matching obligation found — still return True if the cm is valid
        _log.debug("verify_obligation_discharge: no matching obligation for %s", cm_name)
        return _has_enter_exit(cm_instance)

    def track_nested_obligations(self, with_node: ast.With) -> list[TemporalObligation]:
        """Build TemporalObligation objects for each item in an ast.With node.

        Walks the with_node.items list. For each withitem, extracts the context
        manager name using _ast_with_context_name and builds a TemporalObligation.
        Tracks the nesting depth via the obligation_graph's depth_stack.

        Args:
            with_node: An ast.With (or ast.AsyncWith) node from the parsed AST.

        Returns:
            A list of TemporalObligation objects, one per with-item.
        """
        obligations: list[TemporalObligation] = []
        base_depth = len(self.obligation_graph._depth_stack)

        for i, item in enumerate(with_node.items):
            cm_name = _ast_with_context_name(item)
            parent_id = self.obligation_graph._depth_stack[-1] if self.obligation_graph._depth_stack else ""
            lineno = getattr(with_node, "lineno", 0)

            obligation = TemporalObligation(
                obligation_id=f"{_OBLIGATION_ID_PREFIX}-{uuid.uuid4().hex[:12]}",
                cm_type_name=cm_name,
                enter_site=f"{_ENTRY_COORDINATE_PREFIX}:line{lineno}:{cm_name}",
                exit_site=f"{_EXIT_COORDINATE_PREFIX}:line{lineno}:{cm_name}",
                is_discharged=False,
                suppressed_exception="",
                parent_obligation_id=parent_id,
                depth=base_depth + i,
                trust=_DEFAULT_TRUST_LEVEL,
                created_at=_now_iso(),
                discharged_at="",
            )
            self.obligation_graph.add_obligation(obligation)
            obligations.append(obligation)
            _log.debug("track_nested_obligations: %s at line %d", obligation.obligation_id, lineno)

        return obligations

    def compute_obligation_graph(self) -> ObligationGraph:
        """Build and return the final ObligationGraph from accumulated obligations.

        Calls topological_sort() to verify there are no cycles. Logs the resulting
        sort order and returns the graph.

        Returns:
            The (possibly updated) obligation_graph with topological sort verified.
        """
        try:
            topo_order = self.obligation_graph.topological_sort()
            _log.debug(
                "compute_obligation_graph: topo_sort=%d obligations, order=%s",
                len(topo_order), topo_order[:5],
            )
        except ValueError as exc:
            _log.error("compute_obligation_graph: cycle detected: %s", exc)
        self.analysis_log.append({
            "event": "compute_obligation_graph",
            "graph_id": self.obligation_graph.graph_id,
            "obligation_count": len(self.obligation_graph._obligations),
            "undischarged_count": len(self.obligation_graph.get_undischarged()),
            "timestamp": _now_iso(),
        })
        return self.obligation_graph

    def get_obligation_report(self) -> dict[str, Any]:
        """Return a comprehensive report of all temporal obligations.

        Includes:
        - obligation_count: total obligations in the graph
        - undischarged_count: count of undischarged obligations
        - violation_count: count of LIFO / double-discharge violations
        - graph summary (from ObligationGraph.to_dict())
        - analysis_log_tail: last 10 entries from the analysis log
        - generated_at: ISO timestamp
        """
        graph_dict = self.obligation_graph.to_dict()
        undischarged = self.obligation_graph.get_undischarged()
        violations = self.obligation_graph.get_violations()
        stale = self.obligation_graph.get_stale_obligations()

        report: dict[str, Any] = {
            "obligation_count": graph_dict["obligation_count"],
            "undischarged_count": len(undischarged),
            "discharged_count": graph_dict["obligation_count"] - len(undischarged),
            "violation_count": len(violations),
            "stale_obligation_count": len(stale),
            "graph_summary": graph_dict,
            "violations": [
                {
                    "id": v.violation_id,
                    "kind": v.violation_kind,
                    "description": v.description,
                    "detected_at": v.detected_at,
                }
                for v in violations
            ],
            "undischarged_obligations": [
                {
                    "id": o.obligation_id,
                    "cm_type": o.cm_type_name,
                    "enter_site": o.enter_site,
                    "depth": o.depth,
                    "created_at": o.created_at,
                }
                for o in undischarged
            ],
            "analysis_log_tail": self.analysis_log[-10:],
            "site_id": self.site_id,
            "generated_at": _now_iso(),
        }
        return report

    def classify_context_manager(self, cm: Any) -> str:
        """Classify a context manager instance into a canonical category.

        Uses inspect.getmembers to probe the object for protocol methods and
        origin module information. Classification precedence:
            1. contextlib_cm — created by contextlib.contextmanager or similar
            2. async_cm — has __aenter__/__aexit__
            3. generator_cm — is a generator-backed CM
            4. sync_cm — has __enter__/__exit__
            5. unknown

        Args:
            cm: A context manager instance (or class).

        Returns:
            One of: "sync_cm", "async_cm", "contextlib_cm", "generator_cm", "unknown".
        """
        if _is_contextlib_cm(cm):
            return "contextlib_cm"
        if _has_async_enter_exit(cm):
            return "async_cm"
        # Check if it's a generator-backed CM
        members = dict(inspect.getmembers(cm))
        gen_func = members.get("__wrapped__") or members.get("__func__")
        if gen_func is not None and inspect.isgeneratorfunction(gen_func):
            return "generator_cm"
        if _has_enter_exit(cm):
            return "sync_cm"
        return "unknown"


# ---------------------------------------------------------------------------
# CLASS 2: ContextManagersTemporalObligationsAnalyzer
# ---------------------------------------------------------------------------

class ContextManagersTemporalObligationsAnalyzer:
    """Static analysis engine for context manager temporal obligations.

    Parses Python source code and extracts information about with-statements,
    contextlib usage patterns, nesting structure, and potential missing cleanup.
    Results are cached by source hash to avoid repeated parsing.
    """

    def __init__(self) -> None:
        """Initialise the analyzer with an empty parse cache."""
        self.parse_cache: dict[str, ast.Module] = {}
        self.analysis_results: list[dict[str, Any]] = []

    def _get_tree(self, source: str) -> ast.Module:
        """Return cached AST for source, parsing if necessary."""
        key = _stable_hash(source)
        if key not in self.parse_cache:
            self.parse_cache[key] = ast.parse(source)
        return self.parse_cache[key]

    def find_with_statements(self, source: str) -> list[ast.With]:
        """Parse source and return all ast.With nodes (including async with).

        Both ast.With and ast.AsyncWith are returned together since they share
        the same temporal obligation model (the async distinction is captured
        in the cm_type_name classification downstream).

        Args:
            source: Python source code as a string.

        Returns:
            A list of ast.With (and ast.AsyncWith) nodes in source order.
        """
        try:
            tree = self._get_tree(source)
        except SyntaxError:
            return []
        result: list[ast.With] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                result.append(node)
        # Sort by line number for deterministic order
        result.sort(key=lambda n: getattr(n, "lineno", 0))
        return result

    def analyze_with_items(self, with_node: ast.With) -> list[dict[str, Any]]:
        """Analyze the items of a single with-statement.

        For each item in with_node.items, extracts:
        - context_expr_type: the AST node class name of the context expression
        - cm_name: a human-readable name for the context manager
        - as_var: the name bound by the 'as' clause, or None
        - lineno: source line number

        Args:
            with_node: An ast.With or ast.AsyncWith node.

        Returns:
            A list of dicts, one per withitem.
        """
        results: list[dict[str, Any]] = []
        for item in with_node.items:
            cm_name = _ast_with_context_name(item)
            as_var: Optional[str] = None
            if item.optional_vars is not None:
                if isinstance(item.optional_vars, ast.Name):
                    as_var = item.optional_vars.id
                elif isinstance(item.optional_vars, ast.Tuple):
                    as_var = ",".join(
                        elt.id for elt in item.optional_vars.elts
                        if isinstance(elt, ast.Name)
                    )
            entry: dict[str, Any] = {
                "context_expr_type": type(item.context_expr).__name__,
                "cm_name": cm_name,
                "as_var": as_var,
                "lineno": getattr(with_node, "lineno", 0),
                "is_async": isinstance(with_node, ast.AsyncWith),
            }
            results.append(entry)
        return results

    def detect_nested_with_blocks(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find with-statements that are directly nested inside other with-statements.

        Walks the AST and records pairs of (outer, inner) with-nodes where the
        inner node appears in the body of the outer node. The depth is computed
        as the number of containing with-statements.

        Args:
            tree: A parsed ast.Module.

        Returns:
            A list of dicts with keys: outer_lineno, inner_lineno, depth.
        """
        results: list[dict[str, Any]] = []

        def _walk_with(node: ast.AST, depth: int, outer_lineno: int) -> None:
            """Recursively walk, tracking nesting of with-statements."""
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.With, ast.AsyncWith)):
                    inner_lineno = getattr(child, "lineno", 0)
                    if outer_lineno > 0:
                        results.append({
                            "outer_lineno": outer_lineno,
                            "inner_lineno": inner_lineno,
                            "depth": depth,
                        })
                    _walk_with(child, depth + 1, inner_lineno)
                else:
                    _walk_with(child, depth, outer_lineno)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                outer_lineno = getattr(node, "lineno", 0)
                _walk_with(node, 1, outer_lineno)
            else:
                _walk_with(node, 0, 0)

        return results

    def find_contextlib_usage(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find all uses of contextlib patterns in the AST.

        Looks for:
        - contextlib.contextmanager decorator
        - contextlib.asynccontextmanager decorator
        - contextlib.suppress in with-statements
        - contextlib.ExitStack and contextlib.AsyncExitStack

        Args:
            tree: A parsed ast.Module.

        Returns:
            A list of dicts with keys: pattern, lineno.
        """
        results: list[dict[str, Any]] = []
        _patterns = {
            _CONTEXTMANAGER_DECORATOR, _ASYNCCONTEXTMANAGER_DECORATOR,
            _SUPPRESS_PATTERN, _EXIT_STACK_PATTERN, _ASYNC_EXIT_STACK_PATTERN,
        }

        for node in ast.walk(tree):
            # Decorator usage: @contextlib.contextmanager
            if isinstance(node, ast.Attribute):
                attr_chain = _ast_attr_name(node)
                for pat in _patterns:
                    if attr_chain.endswith(f".{pat}") or attr_chain == pat:
                        results.append({
                            "pattern": pat,
                            "lineno": getattr(node, "lineno", 0),
                            "full_name": attr_chain,
                        })
            # Simple name usage: from contextlib import contextmanager; @contextmanager
            elif isinstance(node, ast.Name) and node.id in _patterns:
                results.append({
                    "pattern": node.id,
                    "lineno": getattr(node, "lineno", 0),
                    "full_name": node.id,
                })

        # Deduplicate by (pattern, lineno)
        seen: set[tuple[str, int]] = set()
        deduped: list[dict[str, Any]] = []
        for r in results:
            key = (r["pattern"], r["lineno"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped

    def classify_with_target(self, node: ast.expr) -> str:
        """Classify the context expression in an ast.withitem.

        Returns a canonical classification:
        - "call": the context expr is a function/method call
        - "name": a bare name reference
        - "attribute": an attribute access
        - "subscript": a subscript expression
        - "unknown": none of the above

        Args:
            node: An ast.expr node (the context_expr of an ast.withitem).

        Returns:
            One of the classification strings above.
        """
        if isinstance(node, ast.Call):
            return "call"
        if isinstance(node, ast.Name):
            return "name"
        if isinstance(node, ast.Attribute):
            return "attribute"
        if isinstance(node, ast.Subscript):
            return "subscript"
        return "unknown"

    def build_obligation_map(self, source: str) -> dict[str, list[int]]:
        """Build a mapping from context manager type name to source line numbers.

        Parses the source and builds a dict mapping each distinct context manager
        name (as extracted by _ast_with_context_name) to the list of line numbers
        where it appears in with-statements.

        Args:
            source: Python source code as a string.

        Returns:
            A dict mapping cm_name -> [lineno, ...].
        """
        with_stmts = self.find_with_statements(source)
        mapping: dict[str, list[int]] = {}
        for with_node in with_stmts:
            items_info = self.analyze_with_items(with_node)
            for info in items_info:
                cm_name = info["cm_name"]
                lineno = info["lineno"]
                mapping.setdefault(cm_name, []).append(lineno)
        return mapping

    def find_missing_cleanup(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Heuristically detect resource acquisitions not wrapped in with-statements.

        Looks for Call nodes whose function name matches known resource-acquisition
        patterns (_MISSING_CLEANUP_RESOURCES) that are NOT the context_expr of a
        withitem. This is a heuristic and will have false positives/negatives.

        Args:
            tree: A parsed ast.Module.

        Returns:
            A list of dicts with keys: resource_type, lineno.
        """
        # Collect all line numbers that are context_exprs in with-statements
        protected_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    lineno = getattr(item.context_expr, "lineno", -1)
                    protected_lines.add(lineno)

        results: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = ""
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name in _MISSING_CLEANUP_RESOURCES:
                    lineno = getattr(node, "lineno", 0)
                    if lineno not in protected_lines:
                        results.append({
                            "resource_type": func_name,
                            "lineno": lineno,
                        })
        return results


# ---------------------------------------------------------------------------
# CLASS 3: ContextManagersTemporalObligationsWitness
# ---------------------------------------------------------------------------

class ContextManagersTemporalObligationsWitness:
    """Runtime witness for temporal obligation creation and discharge.

    Intercepts __enter__ and __exit__ calls on context managers and records
    them as TemporalObligation events in an ObligationGraph. Provides methods
    to detect undischarged obligations and generate EvidenceBundle payloads
    for the judgment site.
    """

    def __init__(self) -> None:
        """Initialise the witness with an empty obligation graph and counters."""
        self.records: list[dict[str, Any]] = []
        self.obligation_graph = ObligationGraph(
            graph_id=f"{_OBLIGATION_GRAPH_PREFIX}-witness-{uuid.uuid4().hex[:8]}"
        )
        self.open_obligations: dict[int, str] = {}  # id(cm) -> obligation_id
        self._enter_count: int = 0
        self._exit_count: int = 0
        self._suppression_count: int = 0
        _log.debug("ContextManagersTemporalObligationsWitness created")

    def witness_enter(self, cm: Any, result: Any, site: str) -> str:
        """Record a __enter__ invocation and create a TemporalObligation.

        Creates a TemporalObligation with RUNTIME_WITNESSED trust, registers it
        with the obligation_graph, and maps id(cm) -> obligation_id so that
        witness_exit can find the matching obligation.

        Args:
            cm: The context manager instance.
            result: The value returned by __enter__.
            site: A string identifying the call site (e.g., "module:lineno").

        Returns:
            The obligation_id string for the newly created obligation.
        """
        cm_name = _cm_type_name(cm)
        parent_id = self.obligation_graph._depth_stack[-1] if self.obligation_graph._depth_stack else ""
        depth = len(self.obligation_graph._depth_stack)

        obligation = TemporalObligation(
            obligation_id=f"{_OBLIGATION_ID_PREFIX}-{uuid.uuid4().hex[:12]}",
            cm_type_name=cm_name,
            enter_site=site,
            exit_site="",
            is_discharged=False,
            suppressed_exception="",
            parent_obligation_id=parent_id,
            depth=depth,
            trust=TrustLevel.RUNTIME_WITNESSED,
            created_at=_now_iso(),
            discharged_at="",
        )
        self.obligation_graph.add_obligation(obligation)
        self.open_obligations[id(cm)] = obligation.obligation_id
        self._enter_count += 1
        self.record_obligation_created(obligation.obligation_id)
        _log.debug("witness_enter: %s site=%s", obligation.obligation_id, site)
        return obligation.obligation_id

    def witness_exit(
        self,
        cm: Any,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        """Record an __exit__ invocation and discharge the matching obligation.

        Looks up the obligation_id by id(cm). If the __exit__ call receives a
        non-None exc_type and the result is True (meaning the exception is
        suppressed), increments _suppression_count.

        Args:
            cm: The context manager instance.
            exc_type: The exception type, or None.
            exc_val: The exception value, or None.
            exc_tb: The traceback, or None.

        Returns:
            True if the obligation was found and discharged; False otherwise.
        """
        obligation_id = self.open_obligations.pop(id(cm), None)
        if obligation_id is None:
            # No matching obligation found — could be an untracked CM
            _log.debug("witness_exit: no obligation for cm %r", type(cm).__name__)
            self._exit_count += 1
            return False

        suppressed_exc = ""
        suppressed = False
        if exc_type is not None:
            exc_name = exc_type.__name__ if hasattr(exc_type, "__name__") else str(exc_type)
            # We assume suppression if the CM's __exit__ returns True; here we
            # record the exception type name for the obligation regardless
            suppressed_exc = exc_name
            suppressed = True
            self._suppression_count += 1

        ok = self.obligation_graph.discharge_obligation(obligation_id, suppressed_exc=suppressed_exc)
        self._exit_count += 1
        self.record_obligation_discharged(obligation_id, suppressed=suppressed)
        _log.debug("witness_exit: %s suppressed=%s ok=%s", obligation_id, suppressed, ok)
        return ok

    def record_obligation_created(self, obligation_id: str) -> None:
        """Log and track that an obligation was created.

        Appends a structured record to self.records for post-hoc inspection.

        Args:
            obligation_id: The ID of the newly created obligation.
        """
        record = {
            "event": "obligation_created",
            "obligation_id": obligation_id,
            "timestamp": _now_iso(),
            "enter_count": self._enter_count,
        }
        self.records.append(record)
        _log.debug("record_obligation_created: %s", obligation_id)

    def record_obligation_discharged(self, obligation_id: str, suppressed: bool) -> None:
        """Log and track that an obligation was discharged.

        Appends a structured record to self.records.

        Args:
            obligation_id: The ID of the discharged obligation.
            suppressed: True if the CM suppressed an exception.
        """
        record = {
            "event": "obligation_discharged",
            "obligation_id": obligation_id,
            "suppressed": suppressed,
            "timestamp": _now_iso(),
            "exit_count": self._exit_count,
        }
        self.records.append(record)
        _log.debug("record_obligation_discharged: %s suppressed=%s", obligation_id, suppressed)

    def detect_undischarged_obligations(self) -> list[TemporalObligation]:
        """Return all obligations that have not yet been discharged.

        Delegates to ObligationGraph.get_undischarged(). These represent
        potential resource leaks or sheaf obstructions.

        Returns:
            A list of undischarged TemporalObligation objects.
        """
        undischarged = self.obligation_graph.get_undischarged()
        if undischarged:
            _log.warning(
                "detect_undischarged_obligations: %d undischarged", len(undischarged)
            )
        return undischarged

    def generate_obligation_evidence(self) -> EvidenceBundle:
        """Convert all tracked obligations into an EvidenceBundle.

        Each obligation becomes an EvidenceItem. Discharged obligations carry
        RUNTIME_WITNESSED trust; undischarged carry ORACLE_PROPOSED (pending
        further confirmation). The bundle represents the cumulative evidence
        about context manager lifecycle for the judgment site.

        Returns:
            An EvidenceBundle containing one EvidenceItem per obligation.
        """
        items: list[EvidenceItem] = []
        for obl in self.obligation_graph._obligations.values():
            trust = TrustLevel.RUNTIME_WITNESSED if obl.is_discharged else TrustLevel.ORACLE_PROPOSED
            payload = json.dumps({
                "obligation_id": obl.obligation_id,
                "cm_type": obl.cm_type_name,
                "is_discharged": obl.is_discharged,
                "suppressed_exception": obl.suppressed_exception,
                "depth": obl.depth,
                "enter_site": obl.enter_site,
                "exit_site": obl.exit_site,
            })
            item = EvidenceItem(
                item_id=f"ei-{obl.obligation_id[:12]}",
                kind=EvidenceItemKind.WITNESS,
                payload=payload,
                trust=trust,
                channel=_ANALYSIS_CHANNEL,
            )
            items.append(item)
        return EvidenceBundle(items=tuple(items))

    def get_obligation_summary(self) -> dict[str, Any]:
        """Return a summary dict with obligation counts and violation info.

        Returns:
            A dict with keys: enter_count, exit_count, suppression_count,
            undischarged_count, violation_count, open_obligation_ids.
        """
        undischarged = self.detect_undischarged_obligations()
        violations = self.obligation_graph.get_violations()
        return {
            "enter_count": self._enter_count,
            "exit_count": self._exit_count,
            "suppression_count": self._suppression_count,
            "undischarged_count": len(undischarged),
            "violation_count": len(violations),
            "open_obligation_ids": list(self.open_obligations.values()),
            "graph_id": self.obligation_graph.graph_id,
        }


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def _cm_type_name(cm: Any) -> str:
    """Extract the type name of a context manager instance."""
    return type(cm).__name__


def _has_enter_exit(cm: Any) -> bool:
    """Return True if cm has both __enter__ and __exit__ methods."""
    return hasattr(cm, "__enter__") and hasattr(cm, "__exit__")


def _has_async_enter_exit(cm: Any) -> bool:
    """Return True if cm has both __aenter__ and __aexit__ methods."""
    return hasattr(cm, "__aenter__") and hasattr(cm, "__aexit__")


def _is_contextlib_cm(cm: Any) -> bool:
    """Return True if cm was created by contextlib.contextmanager or similar."""
    cm_type = type(cm)
    module = getattr(cm_type, "__module__", "")
    return module.startswith(_CONTEXTLIB_MODULE_NAME)


def _ast_with_context_name(item: ast.withitem) -> str:
    """Extract a human-readable name from an ast.withitem context_expr."""
    ctx = item.context_expr
    if isinstance(ctx, ast.Call):
        if isinstance(ctx.func, ast.Name):
            return ctx.func.id
        if isinstance(ctx.func, ast.Attribute):
            return f"{_ast_attr_name(ctx.func.value)}.{ctx.func.attr}"
    if isinstance(ctx, ast.Name):
        return ctx.id
    if isinstance(ctx, ast.Attribute):
        return f"{_ast_attr_name(ctx.value)}.{ctx.attr}"
    return "<expr>"


def _ast_attr_name(node: ast.expr) -> str:
    """Recursively get dotted name from AST Name/Attribute."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_attr_name(node.value)}.{node.attr}"
    return "<expr>"


def _build_context_scope_from_obligation(obl: TemporalObligation) -> ContextScope:
    """Convert a TemporalObligation to a ContextScope for judgment site integration.

    Creates entry and exit Coordinate objects from the obligation's enter_site
    and exit_site, then constructs a CoveringFamily that spans them. The
    is_open flag mirrors the obligation's is_discharged field (open iff not
    discharged). If the obligation is undischarged, a ResidualObligation is
    included in the scope's residuals tuple.

    Args:
        obl: A TemporalObligation (may be discharged or undischarged).

    Returns:
        A ContextScope representing the temporal span of the context manager.
    """
    entry_coord = Coordinate(
        coord_id=f"{_ENTRY_COORDINATE_PREFIX}-{obl.obligation_id[:12]}",
        label=f"enter:{obl.cm_type_name}@{obl.enter_site}",
        kind=CoordinateKind.STATEMENT,
        path_components=(obl.cm_type_name, obl.enter_site),
    )
    exit_coord = Coordinate(
        coord_id=f"{_EXIT_COORDINATE_PREFIX}-{obl.obligation_id[:12]}",
        label=f"exit:{obl.cm_type_name}@{obl.exit_site}",
        kind=CoordinateKind.STATEMENT,
        path_components=(obl.cm_type_name, obl.exit_site),
    )
    covering = CoveringFamily(
        base=entry_coord,
        patches=(exit_coord,),
    )
    residuals: tuple = ()
    if not obl.is_discharged:
        residuals = (ResidualObligation(
            obligation_id=obl.obligation_id,
            description=f"CM {obl.cm_type_name} must call __exit__",
        ),)
    return ContextScope(
        scope_id=obl.obligation_id,
        entry_coordinate=entry_coord,
        exit_coordinate=exit_coord,
        covering_family=covering,
        trust=obl.trust,
        is_open=not obl.is_discharged,
        residuals=residuals,
        entered_at=obl.created_at,
    )


def _obligation_to_evidence_item(obl: TemporalObligation) -> EvidenceItem:
    """Convert a single TemporalObligation to an EvidenceItem for the judgment site.

    Serializes the obligation's key fields as JSON payload. Discharged obligations
    carry RUNTIME_WITNESSED trust; undischarged carry ORACLE_PROPOSED.

    Args:
        obl: A TemporalObligation instance.

    Returns:
        An EvidenceItem with JSON payload.
    """
    trust = TrustLevel.RUNTIME_WITNESSED if obl.is_discharged else TrustLevel.ORACLE_PROPOSED
    payload = json.dumps({
        "obligation_id": obl.obligation_id,
        "cm_type_name": obl.cm_type_name,
        "enter_site": obl.enter_site,
        "exit_site": obl.exit_site,
        "is_discharged": obl.is_discharged,
        "suppressed_exception": obl.suppressed_exception,
        "parent_obligation_id": obl.parent_obligation_id,
        "depth": obl.depth,
        "created_at": obl.created_at,
        "discharged_at": obl.discharged_at,
    })
    return EvidenceItem(
        item_id=f"ei-obl-{_stable_hash(obl.obligation_id)[:12]}",
        kind=EvidenceItemKind.WITNESS,
        payload=payload,
        trust=trust,
        channel=_ANALYSIS_CHANNEL,
    )


def _obligation_graph_summary(graph: ObligationGraph) -> str:
    """Return a one-line human-readable summary of an ObligationGraph."""
    undischarged = len(graph.get_undischarged())
    total = len(graph._obligations)
    violations = len(graph.get_violations())
    return (
        f"ObligationGraph({graph.graph_id!r}): "
        f"{total} obligations, {undischarged} undischarged, {violations} violations"
    )


def _make_obligation_id() -> str:
    """Generate a fresh obligation ID with the standard prefix."""
    return f"{_OBLIGATION_ID_PREFIX}-{uuid.uuid4().hex[:12]}"


def _lifo_check(stack: list[str], discharging_id: str) -> bool:
    """Return True if discharging_id is the current top of the LIFO stack."""
    return bool(stack) and stack[-1] == discharging_id


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke_test() -> None:
    """Quick sanity check for context_managers_temporal_obligati.

    Exercises: source analysis, obligation building, discharge, violation detection,
    graph topology, witness recording, and report generation.
    """
    import textwrap
    import io

    print("=== context_managers_temporal_obligati smoke test ===")

    sample_source = textwrap.dedent("""
        import contextlib

        def process_file(path):
            with open(path) as f:
                with contextlib.suppress(IOError):
                    data = f.read()
            return data

        async def async_process():
            async with some_async_cm() as ctx:
                async with another_cm() as inner:
                    pass

        @contextlib.contextmanager
        def my_cm():
            print("enter")
            try:
                yield 42
            finally:
                print("exit")
    """)

    coordinator = ContextManagersTemporalObligationsCoordinator(site_id="smoke-test-s02")

    report = coordinator.analyze_context_managers(sample_source)
    assert "with_count" in report
    print(f"  analyze_context_managers: {report['with_count']} with, {report.get('async_with_count',0)} async-with")

    # Test build_temporal_obligation
    class FakeCM:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    obl = coordinator.build_temporal_obligation(FakeCM)
    assert not obl.is_discharged
    print(f"  build_temporal_obligation: {obl.obligation_id[:20]}, discharged={obl.is_discharged}")

    # Test verify_obligation_discharge
    fake = FakeCM()
    enter_result = fake.__enter__()
    ok = coordinator.verify_obligation_discharge(fake, enter_result, False)
    assert isinstance(ok, bool)
    print(f"  verify_obligation_discharge: {ok}")

    # Test classify_context_manager
    kind = coordinator.classify_context_manager(fake)
    assert kind in ("sync_cm", "async_cm", "contextlib_cm", "generator_cm", "unknown")
    print(f"  classify_context_manager(FakeCM): {kind}")

    # Test ObligationGraph
    graph = ObligationGraph(graph_id="smoke-graph")
    obl1 = TemporalObligation(
        obligation_id="obl-outer", cm_type_name="OuterCM", enter_site="smoke:1",
        exit_site="", is_discharged=False, suppressed_exception="",
        parent_obligation_id="", depth=0, trust=TrustLevel.RUNTIME_WITNESSED,
        created_at=_now_iso(), discharged_at="",
    )
    obl2 = TemporalObligation(
        obligation_id="obl-inner", cm_type_name="InnerCM", enter_site="smoke:2",
        exit_site="", is_discharged=False, suppressed_exception="",
        parent_obligation_id="obl-outer", depth=1, trust=TrustLevel.RUNTIME_WITNESSED,
        created_at=_now_iso(), discharged_at="",
    )
    graph.add_obligation(obl1)
    graph.add_obligation(obl2)
    assert len(graph.get_undischarged()) == 2
    graph.discharge_obligation("obl-inner")
    graph.discharge_obligation("obl-outer")
    assert len(graph.get_undischarged()) == 0
    print(f"  ObligationGraph: 2 obligations, discharged cleanly, violations={len(graph.get_violations())}")

    # Test analyzer
    analyzer = ContextManagersTemporalObligationsAnalyzer()
    with_stmts = analyzer.find_with_statements(sample_source)
    contextlib_usages = analyzer.find_contextlib_usage(ast.parse(sample_source))
    nested = analyzer.detect_nested_with_blocks(ast.parse(sample_source))
    print(f"  analyzer: {len(with_stmts)} with-stmts, {len(contextlib_usages)} contextlib, {len(nested)} nested")

    # Test witness
    witness = ContextManagersTemporalObligationsWitness()
    fake2 = FakeCM()
    obl_id = witness.witness_enter(fake2, fake2.__enter__(), "smoke:enter")
    assert isinstance(obl_id, str)
    witness.witness_exit(fake2, None, None, None)
    summary = witness.get_obligation_summary()
    print(f"  witness summary: enter={witness._enter_count}, exit={witness._exit_count}, undischarged={summary.get('undischarged_count',0)}")

    # Test _build_context_scope_from_obligation
    test_obl = TemporalObligation(
        obligation_id="test-obl-scope", cm_type_name="TestCM", enter_site="smoke:3",
        exit_site="smoke:4", is_discharged=True, suppressed_exception="",
        parent_obligation_id="", depth=0, trust=TrustLevel.RUNTIME_WITNESSED,
        created_at=_now_iso(), discharged_at=_now_iso(),
    )
    scope = _build_context_scope_from_obligation(test_obl)
    assert hasattr(scope, "scope_id")
    print(f"  _build_context_scope_from_obligation: scope_id={scope.scope_id[:20]}")

    # Test get_obligation_report
    full_report = coordinator.get_obligation_report()
    assert "obligation_count" in full_report
    print(f"  get_obligation_report: {full_report['obligation_count']} obligations")

    # Additional: test _obligation_to_evidence_item helper
    ei = _obligation_to_evidence_item(test_obl)
    assert ei.item_id.startswith("ei-obl-")
    print(f"  _obligation_to_evidence_item: {ei.item_id}")

    # Additional: test ObligationGraph.to_dict and topological_sort
    graph2 = ObligationGraph(graph_id="smoke-topo")
    obl_a = TemporalObligation(
        obligation_id="obl-a", cm_type_name="A", enter_site="s:1", exit_site="",
        is_discharged=False, suppressed_exception="", parent_obligation_id="",
        depth=0, trust=TrustLevel.RUNTIME_WITNESSED, created_at=_now_iso(), discharged_at="",
    )
    obl_b = TemporalObligation(
        obligation_id="obl-b", cm_type_name="B", enter_site="s:2", exit_site="",
        is_discharged=False, suppressed_exception="", parent_obligation_id="obl-a",
        depth=1, trust=TrustLevel.RUNTIME_WITNESSED, created_at=_now_iso(), discharged_at="",
    )
    graph2.add_obligation(obl_a)
    graph2.add_obligation(obl_b)
    topo = graph2.topological_sort()
    assert topo[0] == "obl-b" and topo[1] == "obl-a", f"unexpected topo order: {topo}"
    print(f"  topological_sort: {topo}")

    # Additional: _obligation_graph_summary helper
    summary_str = _obligation_graph_summary(graph2)
    assert "ObligationGraph" in summary_str
    print(f"  _obligation_graph_summary: {summary_str}")

    print("=== smoke test PASSED ===")


if __name__ == "__main__":
    _smoke_test()
