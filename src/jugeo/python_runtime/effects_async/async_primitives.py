from __future__ import annotations

r"""
Package: jugeo.python_runtime.effects_async.async
theory2.tex Ch18 §18.3 — Async Coroutines as Suspended Section Morphisms

Python coroutines and asyncio Tasks are modelled as section morphisms in the
async sub-site.  A coroutine is a suspended morphism: it is defined at a
Coordinate but paused at a sub-coordinate pending an awaited value.

``await expr`` is a section restriction to the sub-coordinate of the awaited
expression.  When the awaited coroutine completes, the restriction is resolved
and execution resumes.

``asyncio.Task`` maps to AsyncSection: the task's coordinate is the function's
coordinate; the await-dependency edges are morphisms to the awaited coordinates.
The event loop is the covering topology for async time: it schedules sections
over the async sub-site.

Cancellation (task.cancel()) creates a CancellationRecord and propagates it
through the dependency graph.

All copilot-proposed async sections enter at ORACLE_PROPOSED trust (the
COPILOT_SUGGESTED ceiling) until the runtime confirms completion.

See also
--------
* jugeo.python_runtime.effects_async.models — AsyncSection, CancellationRecord
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

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Coroutine, Iterator

# ---
# Local model imports — stubs accepted if package not yet installed
# ---

try:
    from jugeo.python_runtime.effects_async.models import (
        AsyncSection, CancellationRecord, ExceptionSection,
    )
except ImportError:
    # models not yet available; define minimal stubs
    pass

# ---
# Internal helpers
# ---


def _make_coord_id(label: str) -> str:
    """Return a short stable identifier derived from *label*.

    Uses the first 16 hex characters of SHA-256 so IDs are deterministic and
    safe for use in dictionaries and site builders.  Copilot-generated labels
    pass through this function to avoid collisions.

    Parameters
    ----------
    label:
        Human-readable label to hash.

    Returns
    -------
    str
        16-character hex prefix of SHA-256(label).
    """
    return _stable_hash(label)[:16]


def _build_fiber_coordinate(base: Coordinate, suffix: str) -> Coordinate:
    """Derive a child Coordinate from *base* by appending *suffix*.

    The fiber coordinate inherits the base kind and path_components, extended
    with the suffix.  Used by CoroutineSection to build suspension-point
    coordinates without modifying the base coordinate.

    Parameters
    ----------
    base:
        Parent coordinate in the site hierarchy.
    suffix:
        A string suffix that identifies the sub-point (e.g. an await expression).

    Returns
    -------
    Coordinate
        New coordinate with coord_id derived from base + suffix.
    """
    combined = f"{getattr(base, 'coord_id', str(base))}:{suffix}"
    return Coordinate(
        coord_id=_make_coord_id(combined),
        label=f"{getattr(base, 'label', str(base))}/{suffix}",
        kind=getattr(base, 'kind', CoordinateKind.FUNCTION),
        path_components=getattr(base, 'path_components', ()) + (suffix,),
    )


def _status_from_bool(is_complete: bool, is_suspended: bool) -> str:
    """Derive an AsyncSection-compatible status string from boolean flags.

    Maps the CoroutineSection lifecycle flags to the four-value status
    vocabulary used by AsyncSection (theory2.tex Ch18 §18.3).

    Parameters
    ----------
    is_complete:
        True if the coroutine has finished execution.
    is_suspended:
        True if the coroutine is currently awaiting another task.

    Returns
    -------
    str
        One of ``"PENDING"``, ``"RUNNING"``, or ``"DONE"``.
    """
    if is_complete:
        return "DONE"
    if is_suspended:
        return "RUNNING"
    return "PENDING"


# ---
# CoroutineSection
# ---


@dataclass(frozen=True, slots=True)
class CoroutineSection:
    r"""A suspended coroutine modelled as a section morphism in the async sub-site.

    theory2.tex Ch18 §18.3 — each Python coroutine is a section morphism defined
    at ``coordinate`` and potentially paused at ``suspension_point`` while waiting
    for ``awaiting_task_id`` to resolve.

    Copilot-proposed coroutines enter the system at ``ORACLE_PROPOSED`` trust;
    they are stepped up to ``RUNTIME_WITNESSED`` once the runtime confirms that
    the coroutine has completed.

    Parameters
    ----------
    coro_id:
        Unique coroutine identifier (typically the qualified function name plus
        a UUID suffix).
    coordinate:
        The site coordinate for this coroutine's definition point.
    suspension_point:
        Sub-coordinate where the coroutine is currently ``await``-ing, or
        ``None`` if the coroutine is not currently suspended.
    awaiting_task_id:
        The ``task_id`` of the awaited AsyncSection, or ``None``.
    trust:
        Current trust level for this section.
    local_var_names:
        Frozen snapshot of local variable *names* (not values) at the last
        suspension point.  Values are intentionally omitted for privacy.
    created_at:
        ISO-8601 UTC timestamp of coroutine creation.
    is_complete:
        True once the coroutine has run to completion.
    """

    coro_id: str
    coordinate: Coordinate
    suspension_point: Coordinate | None
    awaiting_task_id: str | None
    trust: TrustLevel
    local_var_names: tuple[str, ...]
    created_at: str
    is_complete: bool

    # ---

    def suspend_at(self, point: Coordinate, task_id: str) -> CoroutineSection:
        """Return a new section paused at *point* awaiting *task_id*.

        Records the await edge as both a sub-coordinate and a task reference.
        The copilot pipeline uses this to build the await-dependency graph
        before handing it to the scheduler.

        Parameters
        ----------
        point:
            The sub-coordinate corresponding to the ``await`` expression.
        task_id:
            The identifier of the AsyncSection being awaited.

        Returns
        -------
        CoroutineSection
            New section with ``suspension_point=point`` and
            ``awaiting_task_id=task_id``.
        """
        return replace(self, suspension_point=point, awaiting_task_id=task_id)

    def resume(self) -> CoroutineSection:
        """Return a new section with the suspension cleared.

        Called when the awaited task completes and execution resumes.
        Corresponds to the resolution of a restriction morphism in the
        async sub-site topology.

        Returns
        -------
        CoroutineSection
            New section with ``suspension_point=None`` and
            ``awaiting_task_id=None``.
        """
        return replace(self, suspension_point=None, awaiting_task_id=None)

    def complete(self) -> CoroutineSection:
        """Return a new section marked as fully complete.

        Trust is stepped stronger toward ``RUNTIME_WITNESSED`` because the
        runtime has confirmed the coroutine finished.  Suspension fields are
        cleared since the coroutine is no longer active.

        Returns
        -------
        CoroutineSection
            New section with ``is_complete=True`` and no suspension.
        """
        new_trust = self.trust.step_stronger()
        if int(new_trust) > int(TrustLevel.RUNTIME_WITNESSED):
            new_trust = TrustLevel.RUNTIME_WITNESSED
        return replace(
            self,
            is_complete=True,
            suspension_point=None,
            awaiting_task_id=None,
            trust=new_trust,
        )

    def as_async_section(self) -> object:
        """Build and return an :class:`AsyncSection` from this coroutine section.

        Converts the coroutine lifecycle state to the AsyncSection status
        vocabulary.  The ``awaited_coordinates`` field is populated from
        ``suspension_point`` if present, forming the await-dependency edge
        in the async sub-site graph.  Copilot tools consume AsyncSection
        objects directly, so this bridge is the primary interop point.

        Returns
        -------
        AsyncSection | dict
            An :class:`AsyncSection` instance, or a plain dict if the model
            class is unavailable.
        """
        status = _status_from_bool(self.is_complete, self.suspension_point is not None)
        awaited: tuple[Coordinate, ...] = (
            (self.suspension_point,) if self.suspension_point is not None else ()
        )
        try:
            return AsyncSection(
                task_id=self.coro_id,
                coordinate=self.coordinate,
                status=status,
                awaited_coordinates=awaited,
                trust=self.trust,
                result_section=None,
                cancellation=None,
                created_at=self.created_at,
            )
        except Exception:
            return {
                "task_id": self.coro_id,
                "coordinate": str(self.coordinate),
                "status": status,
                "awaited_coordinates": [str(c) for c in awaited],
                "trust": self.trust.label(),
                "created_at": self.created_at,
            }

    def as_judgment(self) -> object:
        """Build a judgment encoding the coroutine's current section state.

        The proposition captures the coro_id, coordinate, suspension point,
        and trust level.  Copilot-proposed coroutines enter at
        ``ORACLE_PROPOSED`` and are promoted by the runtime.

        Returns
        -------
        object
            A :class:`Judgment` or plain dict if imports are unavailable.
        """
        susp_str = str(self.suspension_point) if self.suspension_point else "none"
        formula = (
            f"coroutine({self.coro_id}) @ {self.coordinate} "
            f"suspended_at={susp_str} complete={self.is_complete}"
        )
        try:
            prop = Proposition(
                prop_id=_make_coord_id(formula),
                formula=formula,
                kind=PropositionKind.BEHAVIOURAL,
            )
            prov = Provenance(
                source=ProvenanceSource.RUNTIME,
                agent="effects_async.CoroutineSection",
                timestamp=self.created_at,
                chain=(),
            )
            annotation = TrustAnnotation.at(self.trust)
            builder = JudgmentBuilder()
            builder.set_coordinate(self.coordinate)
            builder.set_proposition(prop)
            builder.set_trust(annotation)
            builder.set_provenance(prov)
            return builder.build()
        except (ImportError, AttributeError):
            return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this coroutine section to a JSON-safe dictionary.

        Includes all identifying fields, the suspension point (if any), trust
        label, and the list of captured local variable names.  Values are not
        serialised to preserve caller privacy, as recommended by the copilot
        toolchain guidelines.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this section.
        """
        return {
            "coro_id": self.coro_id,
            "coordinate": str(self.coordinate),
            "suspension_point": (
                str(self.suspension_point) if self.suspension_point else None
            ),
            "awaiting_task_id": self.awaiting_task_id,
            "trust": self.trust.label(),
            "local_var_names": list(self.local_var_names),
            "created_at": self.created_at,
            "is_complete": self.is_complete,
        }

    def is_suspended(self) -> bool:
        """Return ``True`` if the coroutine is currently awaiting another task.

        A coroutine is suspended when ``suspension_point`` is not ``None``.
        This corresponds to a pending restriction morphism in the site: the
        section is defined but its value at the suspension coordinate is not
        yet resolved.

        Returns
        -------
        bool
        """
        return self.suspension_point is not None

    def dependency_depth(self) -> int:
        """Return the immediate dependency depth of this coroutine.

        Returns 1 if the coroutine is currently awaiting another task, or 0
        if it is not suspended.  Deeper dependency chains are resolved by the
        event-loop topology's covering families, not tracked here.

        Returns
        -------
        int
            0 if not awaiting; 1 if awaiting.
        """
        return 1 if self.awaiting_task_id is not None else 0


# ---
# EventLoopTopology
# ---


class EventLoopTopology:
    r"""The asyncio event loop modelled as the covering topology for async time.

    theory2.tex Ch18 §18.3 — the event loop is the Grothendieck topology whose
    covering families are the sets of tasks scheduled in each event-loop tick.

    Each registered :class:`AsyncSection` is a section over its coordinate.
    The topology manages the lifecycle of all sections and produces the
    :class:`GrothendieckTopology` describing the async sub-site.

    Copilot-assisted async code generation should register each proposed task
    at ``ORACLE_PROPOSED`` trust; the loop upgrades trust to
    ``RUNTIME_WITNESSED`` upon task completion.

    Parameters
    ----------
    topology_id:
        Unique identifier for this event-loop topology instance.
    loop_coordinate:
        The "root" coordinate representing the event loop itself.
    trust:
        Default trust level for tasks registered without explicit trust.
    """

    def __init__(
        self,
        topology_id: str,
        loop_coordinate: Coordinate,
        trust: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> None:
        self.topology_id = topology_id
        self.loop_coordinate = loop_coordinate
        self.trust = trust
        self.registered_tasks: dict[str, AsyncSection] = {}

    # ---

    def register_task(self, task: AsyncSection) -> None:
        """Register *task* in this topology.

        Validates that the task's ``task_id`` is not already registered to
        prevent duplicate sections in the covering family.  Each registered
        task corresponds to a patch in the covering of the loop coordinate.

        Parameters
        ----------
        task:
            The :class:`AsyncSection` to register.

        Raises
        ------
        ValueError
            If a task with the same ``task_id`` is already registered.
        """
        task_id = getattr(task, "task_id", None)
        if task_id is None:
            raise ValueError("Task must have a task_id attribute.")
        if task_id in self.registered_tasks:
            raise ValueError(
                f"Task {task_id!r} is already registered in topology "
                f"{self.topology_id!r}."
            )
        self.registered_tasks[task_id] = task

    def deregister_task(self, task_id: str) -> AsyncSection | None:
        """Remove and return the task identified by *task_id*.

        Returns ``None`` if no task with that ID is registered.  Used when a
        task transitions out of the event loop's scope (e.g., after DONE or
        CANCELLED) and the copilot pipeline needs to reclaim its slot.

        Parameters
        ----------
        task_id:
            Identifier of the task to remove.

        Returns
        -------
        AsyncSection | None
            The removed task, or ``None`` if not found.
        """
        return self.registered_tasks.pop(task_id, None)

    def pending_tasks(self) -> list[AsyncSection]:
        """Return all tasks currently in PENDING or RUNNING status.

        These are the active sections of the event-loop covering: tasks that
        have not yet reached a terminal state.  The copilot scheduler uses
        this list to determine which tasks need evidence from the runtime
        channel.

        Returns
        -------
        list[AsyncSection]
            Tasks with status ``"PENDING"`` or ``"RUNNING"``.
        """
        return [
            t for t in self.registered_tasks.values()
            if getattr(t, "status", None) in ("PENDING", "RUNNING")
        ]

    def completed_tasks(self) -> list[AsyncSection]:
        """Return all tasks that have reached the DONE status.

        Completed tasks have had their trust promoted to at least
        ``RUNTIME_WITNESSED`` by the event loop.  They remain registered
        until explicitly deregistered to support audit and dependency
        resolution.

        Returns
        -------
        list[AsyncSection]
            Tasks with status ``"DONE"``.
        """
        return [
            t for t in self.registered_tasks.values()
            if getattr(t, "status", None) == "DONE"
        ]

    def cancelled_tasks(self) -> list[AsyncSection]:
        """Return all tasks that have been cancelled.

        Cancelled tasks carry a :class:`CancellationRecord` that encodes the
        cancellation reason and propagation chain.  The copilot analysis tools
        use this list to identify cancellation cascades.

        Returns
        -------
        list[AsyncSection]
            Tasks with status ``"CANCELLED"``.
        """
        return [
            t for t in self.registered_tasks.values()
            if getattr(t, "status", None) == "CANCELLED"
        ]

    def covering_families(self) -> list[CoveringFamily]:
        """Build a :class:`CoveringFamily` for each registered task.

        Each task's coordinate is a patch covering the loop coordinate.
        Together they form the covering topology for this event loop tick.
        The copilot site-builder uses these families to construct the
        Grothendieck topology of the async sub-site.

        Returns
        -------
        list[CoveringFamily]
            One CoveringFamily per registered task.
        """
        families: list[CoveringFamily] = []
        for task in self.registered_tasks.values():
            coord = getattr(task, "coordinate", self.loop_coordinate)
            families.append(
                CoveringFamily(base=self.loop_coordinate, patches=(coord,))
            )
        return families

    def to_grothendieck_topology(self) -> GrothendieckTopology:
        """Package all covering families into a :class:`GrothendieckTopology`.

        The resulting topology encodes the async sub-site for this event loop
        as a Grothendieck topology, which the copilot evidence pipeline can
        traverse to validate that all tasks are properly covered.

        Returns
        -------
        GrothendieckTopology
            Topology with all registered task covering families.
        """
        families = tuple(self.covering_families())
        return GrothendieckTopology(
            site_id=self.topology_id,
            covering_families=families,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this topology to a JSON-safe dictionary.

        Includes the topology ID, loop coordinate, trust level, and all
        registered tasks serialised via their own ``to_dict`` methods.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this event-loop topology.
        """
        tasks_serialised: dict[str, Any] = {}
        for tid, task in self.registered_tasks.items():
            if hasattr(task, "to_dict"):
                tasks_serialised[tid] = task.to_dict()
            else:
                tasks_serialised[tid] = {"task_id": tid, "status": str(task)}
        return {
            "topology_id": self.topology_id,
            "loop_coordinate": str(self.loop_coordinate),
            "trust": self.trust.label(),
            "registered_tasks": tasks_serialised,
        }

    def task_count(self) -> int:
        """Return the number of tasks currently registered in this topology.

        Provides a quick summary count for monitoring and copilot dashboard
        tools without needing to deserialise the full task dictionaries.

        Returns
        -------
        int
            Number of registered tasks.
        """
        return len(self.registered_tasks)


# ---
# AsyncSiteBuilder
# ---


class AsyncSiteBuilder:
    r"""Build a :class:`Site` from a collection of :class:`AsyncSection` objects.

    theory2.tex Ch18 §18.3 — the async sub-site is constructed by treating each
    task as a Coordinate and each await-dependency as a restriction Morphism.
    This builder performs that construction, including DAG validation and
    topological ordering to ensure that the copilot evidence pipeline processes
    tasks in dependency order.

    Parameters
    ----------
    site_id:
        Unique identifier for the constructed site.
    """

    def __init__(self, site_id: str) -> None:
        self.site_id = site_id

    # ---

    def build(self, tasks: list[AsyncSection]) -> Site:
        """Build a :class:`Site` from *tasks*.

        Each task becomes a :class:`Coordinate`; each await-dependency edge
        becomes a :class:`Morphism`.  The site builder accumulates coordinates
        and morphisms and returns the constructed site.  The copilot evidence
        pipeline can then traverse the site to collect coverage evidence.

        Parameters
        ----------
        tasks:
            List of :class:`AsyncSection` objects to incorporate.

        Returns
        -------
        Site
            A site whose coordinates are the task coordinates and whose
            morphisms are the await-dependency edges.
        """
        builder = SiteBuilder()
        coord_map: dict[str, Coordinate] = {}
        for task in tasks:
            coord = self._task_to_coordinate(task)
            coord_id = getattr(coord, "coord_id", str(coord))
            coord_map[coord_id] = coord
            builder.add_coordinate(coord)
        for task in tasks:
            source_coord = self._task_to_coordinate(task)
            awaited = getattr(task, "awaited_coordinates", ())
            for target_coord in awaited:
                morphism = self._await_to_morphism(task, target_coord)
                builder.add_morphism(morphism)
        return builder.build()

    def _task_to_coordinate(self, task: AsyncSection) -> Coordinate:
        """Convert *task* to a :class:`Coordinate` in the async sub-site.

        The coordinate kind is set to ``FUNCTION`` because each asyncio task
        corresponds to a coroutine function.  The coord_id is derived from
        the task's ``task_id`` so that it is stable across multiple builds.

        Parameters
        ----------
        task:
            Source :class:`AsyncSection`.

        Returns
        -------
        Coordinate
            Coordinate with ``kind=CoordinateKind.FUNCTION``.
        """
        task_id = getattr(task, "task_id", str(task))
        existing_coord = getattr(task, "coordinate", None)
        if existing_coord is not None:
            return existing_coord
        return Coordinate(
            coord_id=_make_coord_id(task_id),
            label=task_id,
            kind=CoordinateKind.FUNCTION,
            path_components=(task_id,),
        )

    def _await_to_morphism(
        self, source_task: AsyncSection, target_coord: Coordinate
    ) -> Morphism:
        """Create a restriction :class:`Morphism` for an await-dependency edge.

        The morphism models the ``await`` keyword as a restriction from the
        source task's coordinate to the target coordinate.  Copilot tools
        use these morphisms to traverse the await-dependency graph.

        Parameters
        ----------
        source_task:
            The task that performs the await.
        target_coord:
            The coordinate of the awaited task.

        Returns
        -------
        Morphism
            A morphism with ``kind=MorphismKind.RESTRICTION``.
        """
        source_coord = self._task_to_coordinate(source_task)
        src_id = getattr(source_coord, "coord_id", str(source_coord))
        tgt_id = getattr(target_coord, "coord_id", str(target_coord))
        morphism_id = _make_coord_id(f"{src_id}->await->{tgt_id}")
        return Morphism(
            morphism_id=morphism_id,
            source=source_coord,
            target=target_coord,
            kind=MorphismKind.RESTRICTION,
        )

    def verify_dag(self, tasks: list[AsyncSection]) -> bool:
        """Verify that the await-dependency graph is a DAG (no cycles).

        Uses iterative DFS with a three-colour marking scheme (WHITE=0,
        GREY=1, BLACK=2) to detect back edges.  A back edge indicates a
        cycle, which would cause deadlock in the event loop and invalidate
        the covering-topology invariant.

        Parameters
        ----------
        tasks:
            Tasks whose await-dependency edges form the graph to check.

        Returns
        -------
        bool
            ``True`` if no cycle is found; ``False`` otherwise.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {}
        # Build adjacency from task_id to awaited coord_ids
        adjacency: dict[str, list[str]] = {}
        for task in tasks:
            tid = getattr(task, "task_id", str(task))
            colour[tid] = WHITE
            awaited = getattr(task, "awaited_coordinates", ())
            adjacency[tid] = [
                getattr(c, "coord_id", str(c)) for c in awaited
            ]
        def _dfs(node: str) -> bool:
            colour[node] = GREY
            for neighbour in adjacency.get(node, []):
                if neighbour not in colour:
                    continue
                if colour[neighbour] == GREY:
                    return False  # back edge → cycle
                if colour[neighbour] == WHITE:
                    if not _dfs(neighbour):
                        return False
            colour[node] = BLACK
            return True
        for tid in list(colour.keys()):
            if colour[tid] == WHITE:
                if not _dfs(tid):
                    return False
        return True

    def topological_order(self, tasks: list[AsyncSection]) -> list[AsyncSection]:
        """Return tasks in topological order (leaves — no await-deps — first).

        Performs a post-order DFS over the await-dependency graph so that
        tasks with no dependencies come first.  This order is required by the
        copilot scheduler to ensure that evidence is collected bottom-up.

        Parameters
        ----------
        tasks:
            Tasks to order.

        Returns
        -------
        list[AsyncSection]
            Tasks ordered so that each task appears after all tasks it awaits.

        Raises
        ------
        ValueError
            If a cycle is detected in the await-dependency graph.
        """
        if not self.verify_dag(tasks):
            raise ValueError(
                "Cannot produce topological order: cycle detected in "
                "await-dependency graph."
            )
        task_map: dict[str, AsyncSection] = {
            getattr(t, "task_id", str(t)): t for t in tasks
        }
        adjacency: dict[str, list[str]] = {}
        for task in tasks:
            tid = getattr(task, "task_id", str(task))
            awaited = getattr(task, "awaited_coordinates", ())
            adjacency[tid] = [
                getattr(c, "coord_id", str(c)) for c in awaited
            ]
        visited: set[str] = set()
        order: list[AsyncSection] = []
        def _visit(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)
            for dep in adjacency.get(tid, []):
                if dep in task_map:
                    _visit(dep)
            if tid in task_map:
                order.append(task_map[tid])
        for tid in task_map:
            _visit(tid)
        return order

    def build_topology(self, tasks: list[AsyncSection]) -> GrothendieckTopology:
        """Build the Grothendieck topology for the async sub-site.

        Constructs a covering family for each task where the base is the
        task's coordinate and the patches are its awaited coordinates.
        The resulting topology can be passed to copilot evidence-collection
        tools that validate the async sub-site.

        Parameters
        ----------
        tasks:
            Tasks to include in the topology.

        Returns
        -------
        GrothendieckTopology
            Topology encoding the await-dependency covering families.
        """
        families: list[CoveringFamily] = []
        for task in tasks:
            coord = self._task_to_coordinate(task)
            awaited = tuple(getattr(task, "awaited_coordinates", ()))
            patches = awaited if awaited else (coord,)
            families.append(CoveringFamily(base=coord, patches=patches))
        return GrothendieckTopology(
            site_id=self.site_id,
            covering_families=tuple(families),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the builder's configuration to a JSON-safe dictionary.

        Returns the site_id so that builder instances can be identified in
        logs and copilot audit trails without carrying any mutable state.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of builder configuration.
        """
        return {"site_id": self.site_id, "class": "AsyncSiteBuilder"}


# ---
# TaskRegistry
# ---


class TaskRegistry:
    r"""Lifecycle manager for :class:`AsyncSection` tasks.

    theory2.tex Ch18 §18.3 — the task registry enforces the PENDING →
    RUNNING → DONE/CANCELLED state machine for each async section and
    propagates :class:`CancellationRecord` objects through the dependency
    graph when a task is cancelled.

    Copilot-proposed tasks enter at ``ORACLE_PROPOSED``; the registry promotes
    trust as tasks transition through lifecycle states.

    Parameters
    ----------
    registry_id:
        Unique identifier for this registry instance.
    """

    def __init__(self, registry_id: str) -> None:
        self.registry_id = registry_id
        self.tasks: dict[str, AsyncSection] = {}

    # ---

    def register(self, task: AsyncSection) -> None:
        """Register a new task in the registry.

        Only tasks in ``"PENDING"`` status may be registered; any other status
        indicates the task was created in a bad state.  The copilot agent uses
        this invariant to detect incorrectly initialised task objects.

        Parameters
        ----------
        task:
            The :class:`AsyncSection` to register.

        Raises
        ------
        ValueError
            If task status is not ``"PENDING"`` or task_id already exists.
        """
        tid = getattr(task, "task_id", None)
        if tid is None:
            raise ValueError("Task must have a task_id attribute.")
        if getattr(task, "status", None) != "PENDING":
            raise ValueError(
                f"Only PENDING tasks may be registered; got "
                f"status={getattr(task, 'status', '?')!r} for task {tid!r}."
            )
        if tid in self.tasks:
            raise ValueError(
                f"Task {tid!r} is already registered in registry "
                f"{self.registry_id!r}."
            )
        self.tasks[tid] = task

    def start(self, task_id: str) -> AsyncSection:
        """Transition the task identified by *task_id* to RUNNING.

        Delegates to :meth:`AsyncSection.mark_running` to enforce the
        PENDING → RUNNING invariant.  Updates the registry with the new
        section and returns it.

        Parameters
        ----------
        task_id:
            Identifier of the task to start.

        Returns
        -------
        AsyncSection
            Updated section in RUNNING status.

        Raises
        ------
        KeyError
            If *task_id* is not registered.
        """
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id!r} not found in registry {self.registry_id!r}.")
        task = self.tasks[task_id]
        updated = task.mark_running()
        self.tasks[task_id] = updated
        return updated

    def complete(self, task_id: str, result: dict[str, Any]) -> AsyncSection:
        """Transition a task to DONE and record its result payload.

        Delegates to :meth:`AsyncSection.mark_done` to enforce the
        RUNNING → DONE state transition and trust promotion.  The registry
        is updated with the completed section.  Copilot tools use the
        result payload to verify that the coroutine produced expected output.

        Parameters
        ----------
        task_id:
            Identifier of the task to complete.
        result:
            Result payload to attach to the DONE section.

        Returns
        -------
        AsyncSection
            Updated section in DONE status.

        Raises
        ------
        KeyError
            If *task_id* is not registered.
        """
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id!r} not found in registry {self.registry_id!r}.")
        task = self.tasks[task_id]
        updated = task.mark_done(result)
        self.tasks[task_id] = updated
        return updated

    def cancel(self, task_id: str, reason: str) -> list[AsyncSection]:
        """Cancel a task and propagate cancellation to all dependents.

        Builds a :class:`CancellationRecord`, cancels the direct task, then
        recursively cancels all tasks whose ``awaited_coordinates`` include
        the cancelled task's coordinate.  The propagation mirrors the
        asyncio cancellation cascade described in theory2.tex Ch18 §18.3.
        Copilot-assisted tools rely on this method to surface all affected
        tasks in a single call.

        Parameters
        ----------
        task_id:
            Identifier of the task to cancel.
        reason:
            Human-readable cancellation reason.

        Returns
        -------
        list[AsyncSection]
            All tasks that were cancelled (direct + cascaded).

        Raises
        ------
        KeyError
            If *task_id* is not registered.
        """
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id!r} not found in registry {self.registry_id!r}.")
        cancelled_sections: list[AsyncSection] = []
        queue: list[str] = [task_id]
        seen: set[str] = set()
        while queue:
            current_id = queue.pop(0)
            if current_id in seen or current_id not in self.tasks:
                continue
            seen.add(current_id)
            task = self.tasks[current_id]
            updated = task.cancel(reason)
            self.tasks[current_id] = updated
            cancelled_sections.append(updated)
            dependents = self.get_dependents(current_id)
            for dep in dependents:
                dep_id = getattr(dep, "task_id", None)
                if dep_id and dep_id not in seen:
                    queue.append(dep_id)
        return cancelled_sections

    def get_dependents(self, task_id: str) -> list[AsyncSection]:
        """Return all tasks that directly await the task identified by *task_id*.

        Scans all registered tasks for those whose ``awaited_coordinates``
        contain the coordinate of the specified task.  Used by :meth:`cancel`
        to identify tasks that need cascade cancellation and by copilot
        analysis tools to traverse the dependency graph.

        Parameters
        ----------
        task_id:
            Identifier of the task whose dependents are sought.

        Returns
        -------
        list[AsyncSection]
            Tasks whose awaited_coordinates include the target task's
            coordinate.
        """
        target_task = self.tasks.get(task_id)
        if target_task is None:
            return []
        target_coord = getattr(target_task, "coordinate", None)
        if target_coord is None:
            return []
        target_coord_id = getattr(target_coord, "coord_id", str(target_coord))
        dependents: list[AsyncSection] = []
        for tid, task in self.tasks.items():
            if tid == task_id:
                continue
            awaited = getattr(task, "awaited_coordinates", ())
            for ac in awaited:
                ac_id = getattr(ac, "coord_id", str(ac))
                if ac_id == target_coord_id:
                    dependents.append(task)
                    break
        return dependents

    def build_cancellation_record(
        self, task_id: str, reason: str
    ) -> object:
        """Build a :class:`CancellationRecord` for *task_id* without cancelling it.

        Useful for dry-run analysis: copilot tools can inspect the record to
        understand what a cancellation would affect before committing.

        Parameters
        ----------
        task_id:
            Identifier of the task to model cancellation for.
        reason:
            Human-readable reason for the hypothetical cancellation.

        Returns
        -------
        CancellationRecord | dict
            A :class:`CancellationRecord` instance, or a plain dict if the
            model class is unavailable.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return {"error": f"task {task_id!r} not found"}
        coord = getattr(task, "coordinate", None)
        trust = getattr(task, "trust", TrustLevel.UNVERIFIED)
        try:
            return CancellationRecord(
                task_id=task_id,
                reason=reason,
                cancelled_at=_now_iso(),
                coordinate=coord,
                trust=trust,
                propagated_to=(),
            )
        except Exception:
            return {
                "task_id": task_id,
                "reason": reason,
                "cancelled_at": _now_iso(),
                "coordinate": str(coord),
                "trust": trust.label(),
            }

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a JSON-safe dictionary.

        Serialises each registered task using its ``to_dict`` method where
        available.  The output is suitable for copilot audit logs and
        evidence-bundle payloads.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of the registry and all tasks.
        """
        serialised: dict[str, Any] = {}
        for tid, task in self.tasks.items():
            if hasattr(task, "to_dict"):
                serialised[tid] = task.to_dict()
            else:
                serialised[tid] = {"task_id": tid, "status": str(task)}
        return {
            "registry_id": self.registry_id,
            "tasks": serialised,
        }

    def summary(self) -> dict[str, int]:
        """Return a count of registered tasks grouped by status.

        Provides a quick overview for copilot monitoring dashboards without
        requiring full task deserialisation.

        Returns
        -------
        dict[str, int]
            Mapping of status string to task count.
        """
        counts: dict[str, int] = {}
        for task in self.tasks.values():
            status = getattr(task, "status", "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
        return counts


# ---
# Module public API
# ---

__all__ = [
    "CoroutineSection",
    "EventLoopTopology",
    "AsyncSiteBuilder",
    "TaskRegistry",
]
