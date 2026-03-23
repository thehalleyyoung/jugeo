from __future__ import annotations

r"""Data models for JuGeo python_runtime effects_async package.

theory2.tex Ch18 §18.1 — Exception Sections, Context Scopes, Async Sections,
Generator Sections, and Cancellation Records model the typed effects of Python's
exception machinery, context managers, async coroutines, and generators as
typed judgment sections over a semantic site.

Provides:
  ExceptionSection   — failure sheaf section at a coordinate
  ContextScope       — section-scope open/close record
  AsyncSection       — pending/running/done/cancelled async fiber
  GeneratorSection   — lazy partial-section from a generator yield
  CancellationRecord — propagation record for task cancellation

All copilot-assisted generation in this module enters at COPILOT_SUGGESTED
trust and must be promoted through explicit review.

See also
--------
* jugeo.python_runtime.effects_async.exceptions
* jugeo.python_runtime.effects_async.async
* jugeo.python_runtime.effects_async.generators
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
    # Stubs for standalone execution
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
        return hashlib.sha256(payload.encode()).hexdigest()
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---
# Standard library imports
# ---

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any

# ---
# Helper functions
# ---


def _make_coord_id(label: str) -> str:
    """Return a stable 12-character coordinate ID derived from *label*.

    Uses :func:`_stable_hash` (SHA-256) so that the same label always
    produces the same identifier regardless of process or platform.

    Parameters
    ----------
    label:
        Human-readable label string to hash.

    Returns
    -------
    str
        First 12 hex characters of the SHA-256 digest of *label*.
    """
    return _stable_hash(label)[:12]


def _decay_trust(trust: TrustLevel, steps: int = 1) -> TrustLevel:
    """Decay *trust* downward by *steps* levels, with a floor of UNVERIFIED.

    Decay is used when propagating evidence through indirect hops (e.g.,
    propagating an exception from a child coordinate to a parent).  Trust
    can never drop below :attr:`TrustLevel.UNVERIFIED` through normal decay;
    reaching :attr:`TrustLevel.CONTRADICTED` requires explicit contradiction.

    Parameters
    ----------
    trust:
        The starting :class:`TrustLevel`.
    steps:
        How many levels to decay.  Must be >= 0.

    Returns
    -------
    TrustLevel
        The decayed trust level, clamped at UNVERIFIED.
    """
    if steps < 0:
        raise ValueError(f"steps must be >= 0, got {steps!r}")
    levels = list(TrustLevel)
    current_idx = levels.index(trust)
    # Floor is UNVERIFIED (index 1), not CONTRADICTED (index 0)
    floor_idx = levels.index(TrustLevel.UNVERIFIED)
    new_idx = max(floor_idx, current_idx - steps)
    return levels[new_idx]


# ---
# ExceptionSection
# ---


@dataclass(frozen=True, slots=True)
class ExceptionSection:
    r"""A section of the failure sheaf at a semantic coordinate.

    theory2.tex Ch18 §18.2 — Exceptions as sheaf sections.

    An ExceptionSection records a structured failure at a specific ``coordinate``
    in the semantic site.  The ``trust`` field encodes how much to believe the
    failure report itself (a copilot-proposed exception has lower trust than one
    witnessed by the runtime).  The ``obstruction`` field, if present, records
    a persistent cohomology obstruction that blocks resolution.

    Copilot-assisted encoding follows the COPILOT_SUGGESTED trust ceiling;
    runtime-witnessed exceptions enter at RUNTIME_WITNESSED.

    Parameters
    ----------
    coordinate:
        Where in the site this failure occurred.
    exception_type:
        Fully-qualified Python exception class name.
    message:
        Human-readable failure message.
    trust:
        Trust level for this failure record.
    obstruction:
        Optional persistent obstruction (cohomology class).
    traceback_coords:
        Tuple of coordinates tracing the call stack.
    is_handled:
        Whether this exception has been caught and resolved.
    timestamp:
        ISO-8601 UTC timestamp of occurrence.
    """

    coordinate: Coordinate
    exception_type: str
    message: str
    trust: TrustLevel
    obstruction: Obstruction | None
    traceback_coords: tuple[Coordinate, ...]
    is_handled: bool
    timestamp: str

    def as_judgment(self) -> object:
        """Build a :class:`~jugeo.judgments.judgment_terms.Judgment` from this section.

        Constructs a BEHAVIOURAL proposition encoding the exception type and
        message, annotated with this section's trust level.  Provenance is set
        to RUNTIME to reflect that this section arises from observed runtime
        behaviour, not from copilot speculation.

        Returns
        -------
        object
            A fully-built Judgment (or a plain dict if the jugeo imports are
            unavailable or an AttributeError is raised during construction).

        Notes
        -----
        Copilot-assisted sections that have not been runtime-confirmed should
        have their trust downgraded before calling this method.
        """
        try:
            formula = f"exception({self.exception_type}): {self.message}"
            prop = Proposition(
                prop_id=_make_coord_id(formula),
                formula=formula,
                kind=PropositionKind.BEHAVIOURAL,
            )
            annotation = TrustAnnotation.at(self.trust)
            prov = Provenance(
                source=ProvenanceSource.RUNTIME,
                agent="effects_async.ExceptionSection",
                timestamp=self.timestamp,
                chain=(),
            )
            builder = JudgmentBuilder()
            builder.set_coordinate(self.coordinate)
            builder.set_proposition(prop)
            builder.set_trust(annotation)
            builder.set_provenance(prov)
            return builder.build()
        except (ImportError, AttributeError):
            return self.to_dict()

    def propagate_to(self, parent: Coordinate) -> ExceptionSection:
        """Return a new ExceptionSection propagated to *parent* coordinate.

        The trust is decayed one step to reflect the additional distance from
        the original failure site.  The current coordinate is prepended to
        ``traceback_coords`` so that the propagation history is preserved.

        Parameters
        ----------
        parent:
            The parent coordinate to propagate the exception to.

        Returns
        -------
        ExceptionSection
            A new section at *parent* with decayed trust and extended traceback.
        """
        new_trust = _decay_trust(self.trust, steps=1)
        new_traceback = (self.coordinate,) + self.traceback_coords
        return replace(
            self,
            coordinate=parent,
            trust=new_trust,
            traceback_coords=new_traceback,
        )

    def handle(self, resolution: str) -> ExceptionSection:
        """Return a resolved copy of this section.

        Marks the exception as handled and appends *resolution* to the
        message so that the handling context is recorded for audit purposes.
        No trust change is applied; the handling agent should promote trust
        separately if appropriate.

        Parameters
        ----------
        resolution:
            A short description of how the exception was resolved.

        Returns
        -------
        ExceptionSection
            A new section with ``is_handled=True`` and updated message.
        """
        new_message = f"{self.message} [resolved: {resolution}]"
        return replace(self, is_handled=True, message=new_message)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this section to a JSON-safe dictionary.

        All jugeo domain objects are converted to their string or dict
        representations so that the result can be passed to :func:`json.dumps`
        without further processing.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of all fields.
        """
        obstruction_dict: dict[str, Any] | None = None
        if self.obstruction is not None:
            obs = self.obstruction
            obstruction_dict = {
                "obstruction_id": getattr(obs, "obstruction_id", ""),
                "description": getattr(obs, "description", ""),
                "coordinate": str(getattr(obs, "coordinate", "")),
                "trust": (
                    obs.trust.label()
                    if hasattr(getattr(obs, "trust", None), "label")
                    else str(getattr(obs, "trust", ""))
                ),
            }
        return {
            "coordinate": str(self.coordinate),
            "exception_type": self.exception_type,
            "message": self.message,
            "trust": self.trust.label(),
            "obstruction": obstruction_dict,
            "traceback_coords": [str(c) for c in self.traceback_coords],
            "is_handled": self.is_handled,
            "timestamp": self.timestamp,
            "section_id": self.section_id(),
        }

    def severity_score(self) -> float:
        """Return a severity score in ``[0.0, 1.0]`` for this exception.

        Higher trust means the exception is more reliably real, and therefore
        more severe (unless it has been handled, which halves the score).

        Formula::

            base = (5 - int(trust)) / 5.0
            score = base * 0.5 if is_handled else base
            score = clamp(score, 0.0, 1.0)

        Returns
        -------
        float
            Severity score between 0.0 (negligible) and 1.0 (critical).
        """
        base = (5 - int(self.trust)) / 5.0
        score = base * 0.5 if self.is_handled else base
        return max(0.0, min(1.0, score))

    def chain_with(self, other: ExceptionSection) -> ExceptionSection:
        """Return a new ExceptionSection representing the chaining of *self* and *other*.

        The chained section inherits *self*'s coordinate and the minimum trust
        of the two sections.  The combined traceback includes all coordinates
        from both sections (deduped by coord_id while preserving order).

        Parameters
        ----------
        other:
            The downstream or cause exception to chain with.

        Returns
        -------
        ExceptionSection
            A new section whose message is ``self.message -> other.message``.
        """
        chained_message = f"{self.message} -> {other.message}"
        min_trust = self.trust if int(self.trust) <= int(other.trust) else other.trust
        seen_ids: set[str] = set()
        combined_traceback: list[Coordinate] = []
        for coord in self.traceback_coords + other.traceback_coords:
            cid = getattr(coord, "coord_id", str(coord))
            if cid not in seen_ids:
                seen_ids.add(cid)
                combined_traceback.append(coord)
        return replace(
            self,
            message=chained_message,
            trust=min_trust,
            traceback_coords=tuple(combined_traceback),
            is_handled=self.is_handled and other.is_handled,
        )

    def is_propagated(self) -> bool:
        """Return ``True`` if this exception has been propagated from a child coordinate.

        An exception is considered propagated if its traceback contains at
        least one coordinate, indicating it originated elsewhere and was
        re-raised or forwarded to the current ``coordinate``.

        Returns
        -------
        bool
        """
        return len(self.traceback_coords) > 0

    def section_id(self) -> str:
        """Return a stable identifier for this section.

        The ID is a 16-character prefix of the SHA-256 hash of the
        concatenation of ``coord_id``, ``exception_type``, and ``timestamp``.

        Returns
        -------
        str
            16-character stable hex identifier.
        """
        coord_id = getattr(self.coordinate, "coord_id", str(self.coordinate))
        payload = f"{coord_id}:{self.exception_type}:{self.timestamp}"
        return _stable_hash(payload)[:16]


# ---
# CancellationRecord
# ---


@dataclass(frozen=True, slots=True)
class CancellationRecord:
    r"""A record of task cancellation in the async site.

    theory2.tex Ch18 §18.4 — Cancellation as obstruction morphism.

    A CancellationRecord is created when an async task is cancelled.  It carries
    the reason, timestamp, coordinate, trust level, and a set of task IDs to
    which this cancellation has been propagated.  Cascade cancellations are
    monotone: if task t1 depends on t2 and t2 is cancelled, t1 must also be
    cancelled (Theorem_CancellationPropagation).

    Copilot-assisted cancellation records enter at ORACLE_PROPOSED trust until
    confirmed by the runtime.

    Parameters
    ----------
    task_id:
        The ID of the cancelled task.
    reason:
        Human-readable cancellation reason.
    cancelled_at:
        ISO-8601 UTC timestamp.
    coordinate:
        The site coordinate of the cancelled task.
    trust:
        Trust level for this cancellation record.
    propagated_to:
        Tuple of task IDs this cancellation has been propagated to.
    """

    task_id: str
    reason: str
    cancelled_at: str
    coordinate: Coordinate
    trust: TrustLevel
    propagated_to: tuple[str, ...]

    def propagate(self, task_ids: tuple[str, ...]) -> CancellationRecord:
        """Return a new record extended with additional propagation targets.

        Deduplicates the combined set of task IDs so that repeated propagation
        calls are idempotent.  The copilot propagation contract guarantees
        monotonicity: once a task ID appears in ``propagated_to`` it is never
        removed.

        Parameters
        ----------
        task_ids:
            Additional task IDs to which this cancellation propagates.

        Returns
        -------
        CancellationRecord
            New record with extended ``propagated_to`` (no duplicates).
        """
        combined = dict.fromkeys(self.propagated_to + task_ids)
        return replace(self, propagated_to=tuple(combined.keys()))

    def as_obstruction(self) -> Obstruction:
        """Convert this cancellation record to an :class:`Obstruction`.

        Returns an Obstruction whose description encodes the task ID and
        reason, suitable for insertion into a cohomology obstruction set.

        Returns
        -------
        Obstruction
            Obstruction representing this cancellation event.
        """
        description = f"Cancellation of task {self.task_id}: {self.reason}"
        obs_id = _stable_hash(f"{self.task_id}:{self.cancelled_at}")[:16]
        return Obstruction(
            obstruction_id=obs_id,
            description=description,
            coordinate=self.coordinate,
            trust=self.trust,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of all fields.
        """
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "cancelled_at": self.cancelled_at,
            "coordinate": str(self.coordinate),
            "trust": self.trust.label(),
            "propagated_to": list(self.propagated_to),
            "record_id": self.record_id(),
            "is_cascade": self.is_cascade(),
        }

    def is_cascade(self) -> bool:
        """Return ``True`` if this cancellation has been propagated to other tasks.

        A cascade cancellation is one where the original cancellation event has
        been forwarded to one or more dependent tasks, per the monotonicity
        theorem in theory2.tex Ch18 §18.4.

        Returns
        -------
        bool
        """
        return len(self.propagated_to) > 0

    def record_id(self) -> str:
        """Return a stable 16-character identifier for this record.

        Based on a hash of ``task_id`` and ``cancelled_at`` so that the same
        cancellation event always maps to the same record ID across processes.

        Returns
        -------
        str
        """
        payload = f"{self.task_id}:{self.cancelled_at}"
        return _stable_hash(payload)[:16]

    def affects(self, task_id: str) -> bool:
        """Return ``True`` if *task_id* is directly or transitively affected.

        A task is affected if it is the cancelled task itself (``self.task_id``)
        or if it appears in ``propagated_to``.  Does not perform deep graph
        traversal; callers with multi-hop dependencies must compose calls.

        Parameters
        ----------
        task_id:
            The task ID to test.

        Returns
        -------
        bool
        """
        return task_id == self.task_id or task_id in self.propagated_to


# ---
# AsyncSection
# ---


@dataclass(frozen=True, slots=True)
class AsyncSection:
    r"""A section of the async sub-site representing one coroutine task.

    theory2.tex Ch18 §18.3 — Async coroutines as suspended section morphisms.

    An AsyncSection models a single asyncio Task as a fiber over its coordinate.
    The status field tracks the task lifecycle: PENDING → RUNNING → DONE/CANCELLED.
    The awaited_coordinates field records the coordinates of tasks this task awaits,
    forming the await-dependency morphisms in the async sub-site.

    The event loop is the covering topology for async time; each await point is
    a restriction morphism to a sub-coordinate.  Copilot-proposed async sections
    carry ORACLE_PROPOSED trust until the runtime confirms completion.

    Parameters
    ----------
    task_id:
        Unique task identifier.
    coordinate:
        The site coordinate for this task.
    status:
        One of "PENDING", "RUNNING", "DONE", "CANCELLED".
    awaited_coordinates:
        Coordinates of tasks this task awaits (await-dependency edges).
    trust:
        Trust level for this section.
    result_section:
        The result dict when status is DONE; None otherwise.
    cancellation:
        CancellationRecord if status is CANCELLED; None otherwise.
    created_at:
        ISO-8601 UTC timestamp of task creation.
    """

    task_id: str
    coordinate: Coordinate
    status: str
    awaited_coordinates: tuple[Coordinate, ...]
    trust: TrustLevel
    result_section: dict[str, Any] | None
    cancellation: CancellationRecord | None
    created_at: str

    def mark_running(self) -> AsyncSection:
        """Transition this section from PENDING to RUNNING.

        Enforces the lifecycle invariant: only PENDING tasks may be started.
        This mirrors the state machine in theory2.tex Ch18 §18.3 which treats
        each task transition as a restriction morphism in the event-loop topology.

        Returns
        -------
        AsyncSection
            New section with ``status="RUNNING"``.

        Raises
        ------
        ValueError
            If the current status is not ``"PENDING"``.
        """
        if self.status != "PENDING":
            raise ValueError(
                f"Cannot mark_running: task {self.task_id!r} is in status "
                f"{self.status!r}, expected 'PENDING'."
            )
        return replace(self, status="RUNNING")

    def mark_done(self, result: dict[str, Any]) -> AsyncSection:
        """Transition this section to DONE and record the result.

        Trust is stepped stronger (up to RUNTIME_WITNESSED) because the
        runtime has confirmed task completion — a copilot proposal has been
        witnessed.

        Parameters
        ----------
        result:
            The result payload to attach to this section.

        Returns
        -------
        AsyncSection
            New section with ``status="DONE"`` and updated trust/result.
        """
        new_trust = self.trust.step_stronger()
        # Cap at RUNTIME_WITNESSED — solver discharge requires separate evidence
        if int(new_trust) > int(TrustLevel.RUNTIME_WITNESSED):
            new_trust = TrustLevel.RUNTIME_WITNESSED
        return replace(
            self,
            status="DONE",
            result_section=result,
            trust=new_trust,
        )

    def cancel(self, reason: str) -> AsyncSection:
        """Cancel this task and attach a :class:`CancellationRecord`.

        Creates a new :class:`CancellationRecord` timestamped now, then
        returns a new AsyncSection with status="CANCELLED".

        Parameters
        ----------
        reason:
            Human-readable cancellation reason.

        Returns
        -------
        AsyncSection
            New section with ``status="CANCELLED"`` and ``cancellation`` set.
        """
        record = CancellationRecord(
            task_id=self.task_id,
            reason=reason,
            cancelled_at=_now_iso(),
            coordinate=self.coordinate,
            trust=self.trust,
            propagated_to=(),
        )
        return replace(self, status="CANCELLED", cancellation=record)

    def await_coordinate(self, c: Coordinate) -> AsyncSection:
        """Register an await-dependency on coordinate *c*.

        Deduplicates by ``coord_id`` so that repeated registrations of the
        same coordinate are idempotent.  Each added coordinate corresponds to
        an await-dependency morphism in the async sub-site.

        Parameters
        ----------
        c:
            The coordinate to add as an await dependency.

        Returns
        -------
        AsyncSection
            New section with *c* appended to ``awaited_coordinates`` if not
            already present.
        """
        existing_ids = {getattr(ac, "coord_id", str(ac)) for ac in self.awaited_coordinates}
        new_coord_id = getattr(c, "coord_id", str(c))
        if new_coord_id in existing_ids:
            return self
        return replace(self, awaited_coordinates=self.awaited_coordinates + (c,))

    def as_judgment(self) -> object:
        """Build a judgment encoding the current state of this async task.

        The judgment proposition captures the task ID, site coordinate, and
        current status.  Copilot-proposed tasks carry ORACLE_PROPOSED trust
        until the runtime witnesses completion.

        Returns
        -------
        object
            A Judgment or dict representation if imports are unavailable.
        """
        coord_str = str(self.coordinate)
        formula = f"async_task({self.task_id}) @ {coord_str} status={self.status}"
        try:
            prop = Proposition(
                prop_id=_make_coord_id(formula),
                formula=formula,
                kind=PropositionKind.BEHAVIOURAL,
            )
            annotation = TrustAnnotation.at(self.trust)
            prov = Provenance(
                source=ProvenanceSource.RUNTIME,
                agent="effects_async.AsyncSection",
                timestamp=self.created_at,
                chain=(),
            )
            builder = JudgmentBuilder()
            builder.set_coordinate(self.coordinate)
            builder.set_proposition(prop)
            builder.set_trust(annotation)
            builder.set_provenance(prov)
            return builder.build()
        except (ImportError, AttributeError):
            return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this section to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation including nested cancellation record.
        """
        return {
            "task_id": self.task_id,
            "coordinate": str(self.coordinate),
            "status": self.status,
            "awaited_coordinates": [str(c) for c in self.awaited_coordinates],
            "trust": self.trust.label(),
            "result_section": self.result_section,
            "cancellation": (
                self.cancellation.to_dict() if self.cancellation is not None else None
            ),
            "created_at": self.created_at,
        }

    def is_terminal(self) -> bool:
        """Return ``True`` if this task has reached a terminal state.

        Terminal states are ``"DONE"`` and ``"CANCELLED"``; tasks in
        ``"PENDING"`` or ``"RUNNING"`` are not yet terminal.

        Returns
        -------
        bool
        """
        return self.status in ("DONE", "CANCELLED")

    def dependency_ids(self) -> tuple[str, ...]:
        """Return the ``coord_id`` values of all awaited coordinates.

        Provides a lightweight view of the await-dependency graph edges
        originating from this task, without carrying full Coordinate objects.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(
            getattr(c, "coord_id", str(c)) for c in self.awaited_coordinates
        )


# ---
# GeneratorSection
# ---


@dataclass(frozen=True, slots=True)
class GeneratorSection:
    r"""A partial section emitted by a generator yield point.

    theory2.tex Ch18 §18.5 — Generators as lazy sheaf constructions.

    A GeneratorSection models one yield point of a Python generator.  Each
    yield emits a partial section (the yielded value) over a fiber coordinate
    derived from the generator's coordinate and yield_index.  The sequence
    of GeneratorSection objects forms a restriction sequence in the site.

    send() history is recorded in send_history; StopIteration corresponds to
    is_exhausted=True.  Copilot-assisted generators carry ORACLE_PROPOSED trust
    for their fiber values until runtime confirms each yield.

    Parameters
    ----------
    gen_id:
        Unique generator identifier.
    coordinate:
        Base coordinate for this generator.
    yield_index:
        Zero-based index of this yield point.
    yielded_value:
        The value emitted at this yield point.
    fiber_trust:
        Trust level for this fiber's yielded value.
    is_exhausted:
        True if StopIteration has been raised.
    send_history:
        Values sent into the generator via send().
    """

    gen_id: str
    coordinate: Coordinate
    yield_index: int
    yielded_value: Any
    fiber_trust: TrustLevel
    is_exhausted: bool
    send_history: tuple[Any, ...]

    def advance(self, value: Any) -> GeneratorSection:
        """Advance this generator to the next yield point with *value*.

        Increments ``yield_index`` by one, sets ``yielded_value`` to *value*,
        and appends *value* to ``send_history`` to preserve the full send
        history for audit.

        Parameters
        ----------
        value:
            The value yielded at the next step.

        Returns
        -------
        GeneratorSection
            New section with incremented index and extended send history.
        """
        return replace(
            self,
            yield_index=self.yield_index + 1,
            yielded_value=value,
            send_history=self.send_history + (value,),
        )

    def exhaust(self) -> GeneratorSection:
        """Mark this generator as exhausted (StopIteration raised).

        Returns a new section with ``is_exhausted=True``.  Once exhausted,
        no further advance() calls are meaningful.

        Returns
        -------
        GeneratorSection
            New section with ``is_exhausted=True``.
        """
        return replace(self, is_exhausted=True)

    def current_fiber(self) -> dict[str, Any]:
        """Return a lightweight dict describing the current fiber state.

        Useful for logging, debugging, or passing to copilot-assisted tools
        that need to inspect the generator without consuming it.

        Returns
        -------
        dict[str, Any]
            Dict with gen_id, yield_index, yielded_value, fiber_trust label,
            and is_exhausted flag.
        """
        return {
            "gen_id": self.gen_id,
            "yield_index": self.yield_index,
            "yielded_value": self.yielded_value,
            "fiber_trust": self.fiber_trust.label(),
            "is_exhausted": self.is_exhausted,
        }

    def as_evidence_item(self) -> object:
        """Build an :class:`EvidenceItem` witnessing this fiber's yielded value.

        The payload is a JSON object encoding the gen_id, yield_index, and
        repr of the yielded value.  Trust is taken from ``fiber_trust``.
        Channel is "runtime" since generators are runtime constructs.

        Returns
        -------
        object
            An :class:`EvidenceItem` or a plain dict if imports are unavailable.
        """
        payload = json.dumps({
            "gen_id": self.gen_id,
            "yield_index": self.yield_index,
            "value": repr(self.yielded_value),
        })
        item_id = _stable_hash(f"{self.gen_id}:{self.yield_index}")[:16]
        try:
            return EvidenceItem(
                item_id=item_id,
                kind=EvidenceItemKind.WITNESS,
                payload=payload,
                trust=self.fiber_trust,
                channel="runtime",
            )
        except (ImportError, AttributeError):
            return {
                "item_id": item_id,
                "kind": "witness",
                "payload": payload,
                "trust": self.fiber_trust.label(),
                "channel": "runtime",
            }

    def to_dict(self) -> dict[str, Any]:
        """Serialise this section to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of all fields.  ``yielded_value`` and
            ``send_history`` are repr'd to ensure serializability.
        """
        return {
            "gen_id": self.gen_id,
            "coordinate": str(self.coordinate),
            "yield_index": self.yield_index,
            "yielded_value": repr(self.yielded_value),
            "fiber_trust": self.fiber_trust.label(),
            "is_exhausted": self.is_exhausted,
            "send_history": [repr(v) for v in self.send_history],
            "section_id": self.section_id(),
            "fiber_coordinate": str(self.fiber_coordinate()),
        }

    def fiber_coordinate(self) -> Coordinate:
        """Return the fiber coordinate for this yield point.

        Derives a new :class:`Coordinate` from the base coordinate by
        appending the yield index to the label.  The ``coord_id`` is a
        16-character hash of the base ``coord_id`` and ``yield_index``.

        Returns
        -------
        Coordinate
            Derived fiber coordinate for this yield point.
        """
        base_id = getattr(self.coordinate, "coord_id", str(self.coordinate))
        base_label = getattr(self.coordinate, "label", str(self.coordinate))
        new_label = f"{base_label}::yield[{self.yield_index}]"
        new_id = _stable_hash(f"{base_id}:{self.yield_index}")[:16]
        return Coordinate(coord_id=new_id, label=new_label)

    def section_id(self) -> str:
        """Return a stable 16-character identifier for this section.

        Based on ``gen_id`` and ``yield_index`` so that the same generator
        state always maps to the same section ID.

        Returns
        -------
        str
        """
        payload = f"{self.gen_id}:{self.yield_index}"
        return _stable_hash(payload)[:16]

    def remaining_sends(self) -> int:
        """Return the count of values sent into this generator so far.

        This is the length of ``send_history``.  It does not represent a
        remaining budget; copilot-assisted tools should treat this as a
        monotonically increasing counter.

        Returns
        -------
        int
        """
        return len(self.send_history)


# ---
# ContextScope
# ---


@dataclass(frozen=True, slots=True)
class ContextScope:
    r"""A section-scope record for a Python context manager.

    theory2.tex Ch18 §18.6 — Context managers as section-scope open/close.

    A ContextScope records the opening and optional closing of a ``with`` block
    as a local covering construction on the semantic site.  The entry_coordinate
    marks where the scope opens; exit_coordinate marks where it closes.  The
    covering_family, if present, is the CoveringFamily contributed by this scope.

    Residuals are obligations that remain after scope exit (e.g., cleanup tasks
    not completed).  Copilot-suggested scopes carry ORACLE_PROPOSED trust until
    the runtime confirms __exit__ completion.

    Parameters
    ----------
    scope_id:
        Unique scope identifier.
    entry_coordinate:
        Where __enter__ was called.
    exit_coordinate:
        Where __exit__ was called, or None if still open.
    covering_family:
        The CoveringFamily contributed by this scope, if computed.
    trust:
        Trust level for this scope record.
    is_open:
        True while the context manager is active.
    residuals:
        Tuple of residual obligation strings.
    entered_at:
        ISO-8601 UTC timestamp of __enter__.
    """

    scope_id: str
    entry_coordinate: Coordinate
    exit_coordinate: Coordinate | None
    covering_family: CoveringFamily | None
    trust: TrustLevel
    is_open: bool
    residuals: tuple[str, ...]
    entered_at: str

    def open_scope(self) -> ContextScope:
        """Return a new scope record with ``is_open=True``.

        Validates that ``exit_coordinate`` is None — a scope cannot be opened
        if it has already been closed.

        Returns
        -------
        ContextScope
            New scope with ``is_open=True``.

        Raises
        ------
        ValueError
            If ``exit_coordinate`` is not None.
        """
        if self.exit_coordinate is not None:
            raise ValueError(
                f"Cannot open scope {self.scope_id!r}: exit_coordinate is already set "
                f"({self.exit_coordinate}).  A scope that has been closed cannot be reopened."
            )
        return replace(self, is_open=True)

    def close_scope(self, exit_coord: Coordinate) -> ContextScope:
        """Return a new scope record reflecting __exit__ completion.

        Sets ``is_open=False`` and records ``exit_coord`` as the
        ``exit_coordinate``.  The covering family should be computed
        separately via :meth:`to_covering_family` if needed.

        Parameters
        ----------
        exit_coord:
            The coordinate at which __exit__ was called.

        Returns
        -------
        ContextScope
            New closed scope with ``exit_coordinate`` set.
        """
        return replace(self, is_open=False, exit_coordinate=exit_coord)

    def add_residual(self, r: str) -> ContextScope:
        """Append a residual obligation string to this scope.

        Residuals are cleanup tasks or promises that must be discharged
        after scope exit.  Copilot-assisted analysis may add residuals
        during static analysis; the runtime confirms or removes them on exit.

        Parameters
        ----------
        r:
            Short description of the residual obligation.

        Returns
        -------
        ContextScope
            New scope with *r* appended to ``residuals``.
        """
        return replace(self, residuals=self.residuals + (r,))

    def to_covering_family(self) -> CoveringFamily:
        """Compute the covering family contributed by this scope.

        If the scope is still open (no exit coordinate), the family contains
        only the entry coordinate.  If closed, it contains both entry and exit
        coordinates, forming a minimal two-patch covering of the scope interval.

        Returns
        -------
        CoveringFamily
            The covering family for this scope.
        """
        if self.exit_coordinate is None:
            patches: tuple[Coordinate, ...] = (self.entry_coordinate,)
        else:
            patches = (self.entry_coordinate, self.exit_coordinate)
        return CoveringFamily(base=self.entry_coordinate, patches=patches)

    def as_judgment(self) -> object:
        """Build a judgment encoding the current state of this context scope.

        The proposition formula captures the scope ID and open/closed status,
        suitable for tracking scope lifecycle in the semantic site.  Copilot-
        suggested scopes carry ORACLE_PROPOSED trust until __exit__ confirms.

        Returns
        -------
        object
            A Judgment or dict representation if imports are unavailable.
        """
        formula = f"context_scope({self.scope_id}) open={self.is_open}"
        try:
            prop = Proposition(
                prop_id=_make_coord_id(formula),
                formula=formula,
                kind=PropositionKind.STRUCTURAL,
            )
            annotation = TrustAnnotation.at(self.trust)
            prov = Provenance(
                source=ProvenanceSource.RUNTIME,
                agent="effects_async.ContextScope",
                timestamp=self.entered_at,
                chain=(),
            )
            builder = JudgmentBuilder()
            builder.set_coordinate(self.entry_coordinate)
            builder.set_proposition(prop)
            builder.set_trust(annotation)
            builder.set_provenance(prov)
            return builder.build()
        except (ImportError, AttributeError):
            return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this scope record to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of all fields including optional
            exit_coordinate and covering_family.
        """
        cf_dict: dict[str, Any] | None = None
        if self.covering_family is not None:
            cf = self.covering_family
            cf_dict = {
                "base": str(getattr(cf, "base", "")),
                "patches": [str(p) for p in getattr(cf, "patches", ())],
            }
        return {
            "scope_id": self.scope_id,
            "entry_coordinate": str(self.entry_coordinate),
            "exit_coordinate": (
                str(self.exit_coordinate) if self.exit_coordinate is not None else None
            ),
            "covering_family": cf_dict,
            "trust": self.trust.label(),
            "is_open": self.is_open,
            "residuals": list(self.residuals),
            "entered_at": self.entered_at,
            "duration_estimate": self.duration_estimate(),
        }

    def duration_estimate(self) -> str:
        """Return a human-readable estimate of this scope's duration.

        If the scope is still open, returns ``"open"``.  If closed and both
        timestamps are available, returns a string combining entry and exit
        timestamps.

        Returns
        -------
        str
        """
        if self.is_open:
            return "open"
        if self.exit_coordinate is not None:
            return f"closed (entered={self.entered_at}, exit_coord={self.exit_coordinate})"
        return "closed"

    def has_residuals(self) -> bool:
        """Return ``True`` if there are unresolved residual obligations.

        Residuals indicate that cleanup work remains after scope exit.
        Copilot-assisted tracking should flag scopes with residuals for
        human review.

        Returns
        -------
        bool
        """
        return len(self.residuals) > 0


# ---
# Public API
# ---

__all__ = [
    "ExceptionSection",
    "ContextScope",
    "AsyncSection",
    "GeneratorSection",
    "CancellationRecord",
]
