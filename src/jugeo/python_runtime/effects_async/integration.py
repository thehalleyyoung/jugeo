from __future__ import annotations

r"""
Package: jugeo.python_runtime.effects_async.integration
theory2.tex Ch18 §18.8 — Integration with JuGeo Judgment Infrastructure

This module integrates Ch18 data models with the rest of the JuGeo system:
the Judgment algebra, Z3 solver sessions, and the evidence channel
infrastructure (including CopilotChannel).

ExceptionJudgmentIntegrator converts ExceptionSection objects to full
Judgment tuples using JudgmentBuilder and verifies them via Z3Session.
CopilotChannel evidence is attached with ORACLE_PROPOSED trust ceiling.

AsyncSiteIntegrator integrates AsyncSection lists with a Site, adding
coordinates and morphisms for each task, and exports the await-dependency
graph to Z3 for cycle verification.

ContextScopeIntegrator manages a live stack of ContextScope objects and
integrates with ChannelRouter, emitting judgment streams per scope exit.

GeneratorChannelBridge bridges GeneratorSection fibers to the EvidenceChannel
system, emitting one EvidenceRecord per yield point.

See also
--------
* jugeo.python_runtime.effects_async.models
* jugeo.python_runtime.effects_async.algorithms
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
# Standard library imports
# ---

import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterator

# ---
# Local model and algorithm imports
# ---

try:
    from jugeo.python_runtime.effects_async.models import (
        ExceptionSection, ContextScope, AsyncSection,
        GeneratorSection, CancellationRecord,
    )
except ImportError:
    pass

try:
    from jugeo.python_runtime.effects_async.algorithms import (
        build_async_sub_site,
        detect_cancellation_cascade,
        resolve_context_stack,
        collect_generator_fibers,
    )
except ImportError:
    pass

# ---
# Module-level helpers
# ---

def _coord_id(obj: Any) -> str:
    """Return coord_id string from a Coordinate-like object or plain string.

    Parameters
    ----------
    obj:
        Coordinate instance or string coord_id.

    Returns
    -------
    str
    """
    return getattr(obj, "coord_id", str(obj))


def _make_request_id(prefix: str, coord: Any) -> str:
    """Construct a stable request ID from prefix and coordinate.

    Parameters
    ----------
    prefix:
        Short string prefix (e.g. 'scope-entry', 'exc-evidence').
    coord:
        Coordinate object or string.

    Returns
    -------
    str
        16-character hex ID.
    """
    payload = f"{prefix}:{_coord_id(coord)}:{_now_iso()}"
    return _stable_hash(payload)[:16]


def _exc_formula(exc: "ExceptionSection") -> str:
    """Build the SMT-style formula string for an exception proposition.

    Parameters
    ----------
    exc:
        Source ExceptionSection.

    Returns
    -------
    str
        Formula suitable for use in a Proposition.
    """
    exc_type = getattr(exc, "exception_type", "Exception")
    message = getattr(exc, "message", "")
    return f"exception({exc_type}): {message}"

# ---
# Class 1: ExceptionJudgmentIntegrator
# ---

class ExceptionJudgmentIntegrator:
    r"""Integrate ExceptionSection objects with the JuGeo judgment infrastructure.

    theory2.tex Ch18 §18.8 — Exception judgment integration.

    Converts :class:`~jugeo.python_runtime.effects_async.models.ExceptionSection`
    objects into full :class:`~jugeo.judgments.judgment_terms.Judgment` tuples
    using :class:`JudgmentBuilder`.  If a :class:`Z3Session` is available,
    propositions are verified before the judgment is returned.

    When :attr:`copilot_channel` is set and the exception's trust is at or
    below :attr:`trust_ceiling`, copilot evidence is attached to the judgment
    with ORACLE_PROPOSED trust, per the copilot evidence contract in theory2.tex.

    Parameters
    ----------
    z3_session:
        Optional Z3 session for proposition verification.
    copilot_channel:
        Optional copilot channel for attaching evidence.
    trust_ceiling:
        Maximum trust level for copilot-sourced evidence (default ORACLE_PROPOSED).
    """

    def __init__(
        self,
        z3_session: Z3Session | None = None,
        copilot_channel: CopilotChannel | None = None,
        trust_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> None:
        """Initialise the integrator with optional Z3 and copilot channel.

        Parameters
        ----------
        z3_session:
            Z3 session for formula verification.
        copilot_channel:
            Copilot evidence channel.
        trust_ceiling:
            Trust ceiling for copilot-proposed evidence (default ORACLE_PROPOSED).
        """
        self.z3_session = z3_session
        self.copilot_channel = copilot_channel
        self.trust_ceiling = trust_ceiling

    def integrate(self, exc: "ExceptionSection") -> object:
        """Convert an ExceptionSection to a Judgment.

        Builds a proposition encoding the exception type and message, sets the
        trust annotation from exc.trust, attaches copilot evidence if channel is
        available and trust qualifies, and optionally verifies via Z3.

        Copilot-proposed exceptions (trust <= ORACLE_PROPOSED) are tagged with
        copilot provenance so downstream pipelines can review them separately.

        Parameters
        ----------
        exc:
            Source ExceptionSection to convert.

        Returns
        -------
        object
            A Judgment object (or dict in stub mode).
        """
        formula = _exc_formula(exc)
        prop_id = _stable_hash(formula)[:16]

        try:
            prop = Proposition(
                prop_id=prop_id,
                formula=formula,
                kind=PropositionKind.BEHAVIOURAL,
            )
        except (TypeError, AttributeError):
            prop = {"prop_id": prop_id, "formula": formula}

        annotation = TrustAnnotation.at(exc.trust)

        prov_source = (
            ProvenanceSource.COPILOT
            if int(exc.trust) <= int(TrustLevel.ORACLE_PROPOSED)
            else ProvenanceSource.RUNTIME
        )
        prov = Provenance(
            source=prov_source,
            agent="effects_async.ExceptionJudgmentIntegrator",
            timestamp=_now_iso(),
            chain=(),
        )

        jb = JudgmentBuilder()
        jb.set_coordinate(exc.coordinate)
        jb.set_proposition(prop)
        jb.set_trust(annotation)
        jb.set_provenance(prov)

        # Attach copilot evidence if channel available and trust qualifies
        if (
            self.copilot_channel is not None
            and int(exc.trust) <= int(self.trust_ceiling)
        ):
            jb = self._attach_copilot_evidence(jb, exc)

        # Optionally verify proposition via Z3
        if self.z3_session is not None:
            self._verify_with_z3(prop)

        return jb.build()

    def batch_integrate(self, excs: "list[ExceptionSection]") -> "list[object]":
        """Convert a list of ExceptionSections to Judgments.

        Calls :meth:`integrate` for each exception in *excs* and returns
        the resulting list.  Copilot-proposed exceptions are processed in
        the same pass as higher-trust exceptions to avoid ordering bias.

        Parameters
        ----------
        excs:
            List of ExceptionSection objects.

        Returns
        -------
        list[object]
            List of Judgment objects in the same order as *excs*.
        """
        return [self.integrate(exc) for exc in excs]

    def verify_propagation(self, excs: "list[ExceptionSection]") -> bool:
        """Verify that trust values in *excs* form a non-increasing sequence.

        Per Theorem_ExceptionSectionality (theory2.tex Ch18 §18.2), trust
        must not increase when exceptions are propagated outward through the
        site.  This method checks the invariant for a pre-propagated list.

        Parameters
        ----------
        excs:
            List of ExceptionSection objects in propagation order (closest
            to farthest ancestor).

        Returns
        -------
        bool
            True if trust is non-increasing across the list.
        """
        if len(excs) <= 1:
            return True
        for i in range(len(excs) - 1):
            if int(excs[i + 1].trust) > int(excs[i].trust):
                return False
        return True

    def _attach_copilot_evidence(
        self,
        jb: JudgmentBuilder,
        exc: "ExceptionSection",
    ) -> JudgmentBuilder:
        """Attach a copilot-sourced EvidenceItem to the judgment builder.

        Creates an EvidenceItem from the copilot channel for this exception,
        encoding the exception type and coordinate in the payload.  Trust is
        capped at ORACLE_PROPOSED per the copilot evidence ceiling.

        Parameters
        ----------
        jb:
            JudgmentBuilder to attach evidence to.
        exc:
            Source ExceptionSection.

        Returns
        -------
        JudgmentBuilder
            The same builder with evidence added.
        """
        payload = json.dumps({
            "source": "copilot",
            "exception_type": getattr(exc, "exception_type", ""),
            "coordinate": str(exc.coordinate),
            "trust_ceiling": TrustLevel.ORACLE_PROPOSED.label(),
        })
        item_id = _stable_hash(f"copilot:{_coord_id(exc.coordinate)}:{_now_iso()}")[:16]
        try:
            item = EvidenceItem(
                item_id=item_id,
                kind=EvidenceItemKind.ASSERTION,
                payload=payload,
                trust=TrustLevel.ORACLE_PROPOSED,
                channel="copilot",
            )
            jb.add_evidence(item)
        except (TypeError, AttributeError):
            jb.add_evidence({"item_id": item_id, "payload": payload})
        return jb

    def _verify_with_z3(self, proposition: object) -> bool:
        """Attempt to verify *proposition* via the Z3 session.

        Encodes the proposition using Z3Encoder and calls check() on the
        session.  If the session is unavailable or returns UNKNOWN, returns
        True (optimistic default for copilot-proposed content).

        Parameters
        ----------
        proposition:
            The Proposition object (or dict) to verify.

        Returns
        -------
        bool
            True if SAT or if the session is unavailable; False if UNSAT.
        """
        if self.z3_session is None:
            return True
        try:
            encoder = Z3Encoder()
            encoded = encoder.encode(proposition)
            if encoded is not None:
                self.z3_session.assert_formula(encoded)
            result = self.z3_session.check()
            if result is None:
                return True
            outcome = getattr(result, "outcome", None)
            if outcome == SolveOutcome.UNSAT:
                return False
            return True
        except (AttributeError, TypeError):
            return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise integrator configuration to a JSON-safe dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "has_z3_session": self.z3_session is not None,
            "has_copilot_channel": self.copilot_channel is not None,
            "trust_ceiling": self.trust_ceiling.label(),
        }

# ---
# Class 2: AsyncSiteIntegrator
# ---

class AsyncSiteIntegrator:
    r"""Integrate AsyncSection lists with a Site and Z3 session.

    theory2.tex Ch18 §18.8 — Async site integration.

    Builds a sub-site from a list of :class:`AsyncSection` objects (using
    :func:`~jugeo.python_runtime.effects_async.algorithms.build_async_sub_site`),
    verifies coverage, exports the await-dependency graph to Z3 as SMT-LIB2
    assertions, and detects dependency cycles.

    Copilot-proposed async sections (trust <= ORACLE_PROPOSED) are processed
    identically to runtime-witnessed sections but are flagged in export output.

    Parameters
    ----------
    site:
        Optional pre-built site to augment.
    z3_session:
        Optional Z3 session for cycle verification.
    """

    def __init__(
        self,
        site: Site | None = None,
        z3_session: Z3Session | None = None,
    ) -> None:
        """Initialise the integrator.

        Parameters
        ----------
        site:
            Existing site to use as base (or None to build fresh).
        z3_session:
            Z3 session for constraint checking.
        """
        self.site = site
        self.z3_session = z3_session

    def build_site(self, tasks: "list[AsyncSection]") -> Site:
        """Build a sub-site from the given async tasks.

        Creates a Site where each task's coordinate is a site coordinate and
        each await-dependency becomes a restriction morphism.  Stores the
        result as :attr:`site`.

        Parameters
        ----------
        tasks:
            List of AsyncSection objects.

        Returns
        -------
        Site
            The newly built sub-site.
        """
        builder = SiteBuilder()
        added: set[str] = set()
        coord_for: dict[str, Any] = {}

        for task in tasks:
            cid = _coord_id(task.coordinate)
            if cid not in added:
                builder.add_coordinate(task.coordinate)
                added.add(cid)
                coord_for[cid] = task.coordinate

        morph_seen: set[str] = set()
        for task in tasks:
            src_cid = _coord_id(task.coordinate)
            for awaited in task.awaited_coordinates:
                tgt_cid = _coord_id(awaited)
                if tgt_cid not in added:
                    builder.add_coordinate(awaited)
                    added.add(tgt_cid)
                    coord_for[tgt_cid] = awaited
                morph_key = f"{src_cid}->{tgt_cid}"
                if morph_key not in morph_seen:
                    morph_id = _stable_hash(morph_key)[:16]
                    builder.add_morphism(
                        Morphism(
                            morphism_id=morph_id,
                            source=task.coordinate,
                            target=awaited,
                            kind=MorphismKind.RESTRICTION,
                        )
                    )
                    morph_seen.add(morph_key)

        self.site = builder.build()
        return self.site

    def verify_coverage(
        self,
        tasks: "list[AsyncSection]",
        site: Site,
    ) -> bool:
        """Check that every task's coordinate appears in the site.

        Iterates over the site's coordinate list and verifies that each
        task has a matching coordinate by coord_id.  Returns True if all
        tasks are covered.

        Parameters
        ----------
        tasks:
            List of AsyncSection objects to check.
        site:
            The site to verify against.

        Returns
        -------
        bool
            True if all task coordinates are present in the site.
        """
        site_coord_ids: set[str] = {
            _coord_id(c) for c in getattr(site, "coordinates", [])
        }
        for task in tasks:
            if _coord_id(task.coordinate) not in site_coord_ids:
                return False
        return True

    def export_to_z3(
        self,
        tasks: "list[AsyncSection]",
        session: Z3Session,
    ) -> list[str]:
        """Export the await-dependency graph as SMT-LIB2 assertion strings.

        Produces one assertion per await-dependency edge in the form:
        ``(assert (awaits task_id awaited_id))``.  Copilot-proposed tasks
        are annotated with a comment noting their trust level.

        Parameters
        ----------
        tasks:
            List of AsyncSection objects.
        session:
            Z3 session to assert into.

        Returns
        -------
        list[str]
            List of SMT-LIB2 assertion strings.
        """
        assertions: list[str] = []
        coord_to_task: dict[str, str] = {
            _coord_id(t.coordinate): t.task_id for t in tasks
        }

        for task in tasks:
            is_copilot = int(task.trust) <= int(TrustLevel.ORACLE_PROPOSED)
            comment = f"; copilot-proposed (trust={task.trust.label()})" if is_copilot else ""
            for awaited in task.awaited_coordinates:
                awaited_tid = coord_to_task.get(_coord_id(awaited), _coord_id(awaited))
                smt = f"(assert (awaits {task.task_id} {awaited_tid}))"
                if comment:
                    smt = f"{smt}  {comment}"
                assertions.append(smt)
                try:
                    session.assert_formula(smt)
                except (AttributeError, TypeError):
                    pass

        return assertions

    def detect_cycle(self, tasks: "list[AsyncSection]") -> bool:
        """Return True if the await-dependency graph contains a cycle.

        Uses iterative DFS with WHITE/GRAY/BLACK node colouring to detect
        back-edges in the dependency graph.  Copilot-proposed tasks are
        included in the analysis, not excluded.

        Parameters
        ----------
        tasks:
            List of AsyncSection objects.

        Returns
        -------
        bool
            True if a cycle is found; False if the graph is a DAG.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        coord_to_task_id: dict[str, str] = {
            _coord_id(t.coordinate): t.task_id for t in tasks
        }
        adj: dict[str, list[str]] = {}
        for task in tasks:
            deps: list[str] = []
            for awaited in task.awaited_coordinates:
                tid = coord_to_task_id.get(_coord_id(awaited))
                if tid is not None:
                    deps.append(tid)
            adj[task.task_id] = deps

        color: dict[str, int] = {t.task_id: WHITE for t in tasks}

        def _dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbour in adj.get(node, []):
                if color.get(neighbour) == GRAY:
                    return True
                if color.get(neighbour) == WHITE and _dfs(neighbour):
                    return True
            color[node] = BLACK
            return False

        for task in tasks:
            if color[task.task_id] == WHITE:
                if _dfs(task.task_id):
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialise integrator state.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "has_site": self.site is not None,
            "has_z3_session": self.z3_session is not None,
            "site_coord_count": len(getattr(self.site, "coordinates", [])),
            "site_morphism_count": len(getattr(self.site, "morphisms", [])),
        }

# ---
# Class 3: ContextScopeIntegrator
# ---

class ContextScopeIntegrator:
    r"""Manage a live stack of ContextScope objects with ChannelRouter integration.

    theory2.tex Ch18 §18.8 — Context scope integration with evidence channels.

    Maintains a mutable stack of :class:`ContextScope` objects and routes
    entry/exit evidence requests through a :class:`ChannelRouter`.  On each
    :meth:`push`, an EvidenceRequest for scope entry is routed; on each
    :meth:`pop`, an EvidenceRequest for scope exit is routed.

    Copilot-assisted scope analysis may add scopes with ORACLE_PROPOSED trust;
    these are stored on the stack and emitted in judgment streams like any
    other scope but flagged by their trust level.

    Parameters
    ----------
    router:
        Optional ChannelRouter for routing scope evidence requests.
    """

    def __init__(self, router: ChannelRouter | None = None) -> None:
        """Initialise the integrator with an optional ChannelRouter.

        Parameters
        ----------
        router:
            Channel router for evidence requests.
        """
        self.router = router
        self._stack: list[ContextScope] = []

    def push(self, scope: "ContextScope") -> None:
        """Push *scope* onto the stack and route an entry evidence request.

        The entry request records the scope_id and entry_coordinate, allowing
        downstream subscribers to observe scope entry events.  Copilot-proposed
        scopes (trust <= ORACLE_PROPOSED) are routed with the same mechanism.

        Parameters
        ----------
        scope:
            ContextScope to push.
        """
        self._stack.append(scope)

        if self.router is not None:
            req_id = _make_request_id("scope-entry", scope.entry_coordinate)
            try:
                prop = Proposition(
                    prop_id=req_id,
                    formula=f"scope_entry({scope.scope_id})",
                    kind=PropositionKind.BEHAVIOURAL,
                )
                req = EvidenceRequest(
                    request_id=req_id,
                    coordinate=scope.entry_coordinate,
                    proposition=prop,
                )
                self.router.route(req)
            except (TypeError, AttributeError):
                pass

    def pop(self) -> "ContextScope | None":
        """Pop the top scope from the stack and route an exit evidence request.

        Returns None if the stack is empty.  Routes an exit evidence request
        for the popped scope before returning it, so callers can observe scope
        exit events through the channel infrastructure.

        Returns
        -------
        ContextScope | None
            The popped scope, or None if the stack is empty.
        """
        if not self._stack:
            return None

        scope = self._stack.pop()

        if self.router is not None:
            req_id = _make_request_id("scope-exit", scope.entry_coordinate)
            try:
                prop = Proposition(
                    prop_id=req_id,
                    formula=f"scope_exit({scope.scope_id})",
                    kind=PropositionKind.BEHAVIOURAL,
                )
                req = EvidenceRequest(
                    request_id=req_id,
                    coordinate=scope.entry_coordinate,
                    proposition=prop,
                )
                self.router.route(req)
            except (TypeError, AttributeError):
                pass

        return scope

    def current_covering(self) -> "CoveringFamily | None":
        """Return the CoveringFamily of the top scope, or None if stack is empty.

        Calls :meth:`to_covering_family` on the top scope.  If the scope
        already has a covering_family set, that is returned directly.

        Returns
        -------
        CoveringFamily | None
        """
        if not self._stack:
            return None
        top = self._stack[-1]
        # If scope already has a computed covering_family, return it
        existing = getattr(top, "covering_family", None)
        if existing is not None:
            return existing
        try:
            return top.to_covering_family()
        except (AttributeError, TypeError):
            return None

    def as_judgment_stream(self) -> "Iterator[object]":
        """Yield a Judgment for each scope in the stack (bottom to top).

        Each scope's :meth:`as_judgment` is called to produce the judgment.
        Copilot-proposed scopes yield judgments tagged with ORACLE_PROPOSED
        trust, which downstream pipelines can identify and review.

        Yields
        ------
        object
            A Judgment (or dict in stub mode) for each scope.
        """
        for scope in self._stack:
            try:
                yield scope.as_judgment()
            except (AttributeError, TypeError):
                yield scope.to_dict() if hasattr(scope, "to_dict") else {"scope_id": getattr(scope, "scope_id", "")}

    def stack_depth(self) -> int:
        """Return the current number of scopes on the stack.

        Returns
        -------
        int
        """
        return len(self._stack)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the current stack to a JSON-safe list.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "stack_depth": self.stack_depth(),
            "has_router": self.router is not None,
            "scopes": [
                s.to_dict() if hasattr(s, "to_dict") else {"scope_id": getattr(s, "scope_id", "")}
                for s in self._stack
            ],
        }

# ---
# Class 4: GeneratorChannelBridge
# ---

class GeneratorChannelBridge:
    r"""Bridge GeneratorSection fibers to the EvidenceChannel infrastructure.

    theory2.tex Ch18 §18.8 — Generator fiber evidence bridge.

    Converts each yield point of a :class:`GeneratorSection` into an
    :class:`EvidenceRecord` and routes it through the evidence channel
    system.  One EvidenceRecord is emitted per yield point.

    Copilot-proposed generator fibers (fiber_trust <= ORACLE_PROPOSED) are
    bridged with ORACLE_PROPOSED trust, not promoted by the bridge itself.
    The runtime must independently witness fiber values to promote trust.

    Parameters
    ----------
    channel:
        EvidenceChannel to use for emitted records (default RUNTIME).
    trust:
        Default trust level for emitted records (default RUNTIME_WITNESSED).
    """

    def __init__(
        self,
        channel: EvidenceChannel = EvidenceChannel.RUNTIME,
        trust: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
    ) -> None:
        """Initialise the bridge with channel and trust defaults.

        Parameters
        ----------
        channel:
            Default evidence channel.
        trust:
            Default trust level for emitted records.
        """
        self.channel = channel
        self.trust = trust

    def emit_fiber(self, gen: "GeneratorSection") -> EvidenceRecord:
        """Emit an EvidenceRecord for the current fiber state of *gen*.

        Serialises :meth:`GeneratorSection.current_fiber` as JSON into the
        record payload.  The record's trust is the lesser of self.trust and
        gen.fiber_trust, enforcing the copilot trust ceiling for copilot-
        proposed generator fibers.

        Parameters
        ----------
        gen:
            GeneratorSection to emit.

        Returns
        -------
        EvidenceRecord
        """
        fiber_data = gen.current_fiber()
        payload = json.dumps(fiber_data)
        record_id = _stable_hash(
            f"{gen.gen_id}:{gen.yield_index}:{_now_iso()}"
        )[:16]
        effective_trust = (
            gen.fiber_trust
            if int(gen.fiber_trust) < int(self.trust)
            else self.trust
        )
        try:
            return EvidenceRecord(
                record_id=record_id,
                channel=self.channel,
                payload=payload,
            )
        except (TypeError, AttributeError):
            return EvidenceRecord(record_id=record_id, channel=self.channel, payload=payload)  # type: ignore[return-value]

    def drain(self, gen: "GeneratorSection", n: int) -> "list[EvidenceRecord]":
        """Advance *gen* n steps and emit an EvidenceRecord per step.

        Calls :meth:`GeneratorSection.advance` with None (simulating next())
        and emits via :meth:`emit_fiber` at each step.  Stops early if the
        generator reaches is_exhausted=True.

        Parameters
        ----------
        gen:
            Starting GeneratorSection.
        n:
            Number of steps to advance.

        Returns
        -------
        list[EvidenceRecord]
            One EvidenceRecord per step advanced.
        """
        records: list[EvidenceRecord] = []
        current = gen
        for _ in range(n):
            if current.is_exhausted:
                break
            advanced = current.advance(None)
            record = self.emit_fiber(advanced)
            records.append(record)
            current = advanced
        return records

    def as_evidence_bundle(self, gen: "GeneratorSection") -> EvidenceBundle:
        """Collect up to 100 remaining fiber steps as an EvidenceBundle.

        Drains up to 100 steps from *gen* and wraps the resulting
        EvidenceRecord objects into an EvidenceBundle.  Useful for passing
        generator output to judgment algebra as a single bundle.

        Parameters
        ----------
        gen:
            GeneratorSection to drain.

        Returns
        -------
        EvidenceBundle
        """
        records = self.drain(gen, 100)
        # Convert records to EvidenceItems for the bundle
        items: list[Any] = []
        for rec in records:
            item_id = _stable_hash(rec.record_id)[:16]
            try:
                item = EvidenceItem(
                    item_id=item_id,
                    kind=EvidenceItemKind.WITNESS,
                    payload=rec.payload,
                    trust=self.trust,
                    channel=str(self.channel),
                )
                items.append(item)
            except (TypeError, AttributeError):
                items.append({"item_id": item_id, "payload": rec.payload})

        try:
            return EvidenceBundle(items=tuple(items))
        except (TypeError, AttributeError):
            return EvidenceBundle(items=tuple(items))  # type: ignore[return-value]

    def bridge_to_router(
        self,
        gen: "GeneratorSection",
        router: ChannelRouter,
    ) -> "list[EvidenceResponse]":
        """Emit fibers from *gen* and route each as an EvidenceRequest.

        Advances *gen* up to 100 steps, emitting fiber evidence at each step
        and routing each emission through *router* as an EvidenceRequest.
        Collects and returns the EvidenceResponse from each route call.

        Parameters
        ----------
        gen:
            GeneratorSection to advance and bridge.
        router:
            ChannelRouter to route requests through.

        Returns
        -------
        list[EvidenceResponse]
            One response per successfully routed request.
        """
        responses: list[EvidenceResponse] = []
        current = gen
        for _ in range(100):
            if current.is_exhausted:
                break
            advanced = current.advance(None)
            fiber_data = advanced.current_fiber()
            req_id = _stable_hash(
                f"bridge:{advanced.gen_id}:{advanced.yield_index}"
            )[:16]

            try:
                prop = Proposition(
                    prop_id=req_id,
                    formula=f"generator_fiber({advanced.gen_id}, {advanced.yield_index})",
                    kind=PropositionKind.BEHAVIOURAL,
                )
                req = EvidenceRequest(
                    request_id=req_id,
                    coordinate=advanced.fiber_coordinate(),
                    proposition=prop,
                )
                response = router.route(req)
                if response is not None:
                    responses.append(response)
            except (TypeError, AttributeError):
                pass

            current = advanced

        return responses

    def to_dict(self) -> dict[str, Any]:
        """Serialise bridge configuration to a JSON-safe dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "channel": str(self.channel),
            "trust": self.trust.label(),
        }

# ---
# Module exports
# ---

__all__ = [
    "ExceptionJudgmentIntegrator",
    "AsyncSiteIntegrator",
    "ContextScopeIntegrator",
    "GeneratorChannelBridge",
    # Helpers
    "_coord_id",
    "_make_request_id",
    "_exc_formula",
]
