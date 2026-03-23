from __future__ import annotations

r"""
Package: jugeo.python_runtime.effects_async.theorems
theory2.tex Ch18 §18.9 — Formal Theorems about Effects and Async

This module states and checks the formal theorems about Ch18 structures:

Theorem_ExceptionSectionality  — exception restriction monotonically decays trust
Theorem_ContextScopeCovers     — context scope covers entry/exit coordinates
Theorem_AsyncTopologicalOrder  — async await-dependency is a DAG
Theorem_GeneratorFiberSequence — generator fiber coords form a valid restriction seq
Theorem_CancellationPropagation — cancellation is monotone and cascade-complete

Each theorem is a dataclass with name, statement, proof_sketch, and a check()
method that runs structural verification logic (Z3 if available, else pure Python).

Copilot-assisted theorem proofs are tagged with ORACLE_PROPOSED trust and must
be independently verified before being promoted to SOLVER_DISCHARGED.

See also
--------
* jugeo.python_runtime.effects_async.models
* jugeo.python_runtime.effects_async.algorithms
"""

# ---
# jugeo imports — complete stubs provided when package is not installed
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
from dataclasses import dataclass, field
from typing import Any

# ---
# Model / algorithm imports — fail silently when stubs are used
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
        detect_cancellation_cascade,
        schedule_async_sections,
    )
except ImportError:
    pass

# ---
# Private helper functions
# ---

def _coord_id(obj: Any) -> str:
    """Return the coord_id string from a Coordinate-like object.

    Accepts any object that has a ``coord_id`` attribute (e.g. a real
    ``Coordinate``, a ``CoordinateObject``, or the stub dataclass).  Falls back
    to the empty string so callers can compare safely without branching.

    Copilot-generated coordinates that do not carry a ``coord_id`` are treated
    as the empty-string coordinate and will fail equality checks with legitimate
    coordinates.

    Parameters
    ----------
    obj:
        Any object that may expose a ``coord_id`` attribute.

    Returns
    -------
    str
        The ``coord_id`` string, or ``""`` if the attribute is absent.
    """
    if obj is None:
        return ""
    return getattr(obj, "coord_id", "") or ""


def _get_trust_int(obj: Any) -> int:
    """Return the integer value of a TrustLevel-like object.

    Handles both the real ``TrustLevel`` IntEnum and plain integer values so
    that theorem checks can compare trust levels uniformly.  Unknown objects
    are mapped to the integer value of ``TrustLevel.UNVERIFIED`` (1) as a
    conservative default.

    Parameters
    ----------
    obj:
        A ``TrustLevel`` instance, an ``int``, or any object with a numeric
        value for trust.

    Returns
    -------
    int
        Integer trust level, with higher values meaning stronger trust.
    """
    if isinstance(obj, int):
        return int(obj)
    if hasattr(obj, "__int__"):
        return int(obj)
    return int(TrustLevel.UNVERIFIED)

# ---
# Theorem_ExceptionSectionality
# ---

@dataclass
class Theorem_ExceptionSectionality:
    r"""Theorem: Exception restriction monotonically decays trust.

    theory2.tex Ch18 §18.2 — Theorem 18.1.

    Statement: If e is an ExceptionSection at coordinate c with trust T(e),
    then for any parent coordinate c' (i.e., c' is an ancestor of c in the
    site), the restriction e|_{c'} = e.propagate_to(c') has trust T(e|_{c'})
    <= T(e).

    This is the fundamental sectionality property: trust cannot increase under
    restriction.  Copilot-proposed exceptions that violate this invariant are
    flagged for review.

    Proof sketch: propagate_to() calls _decay_trust() which decrements the
    TrustLevel by at least one step.  Since TrustLevel is an IntEnum with
    total order, the step_weaker() operation guarantees T(e|_{c'}) < T(e) for
    all c' != c, and T(e|_{c}) = T(e) (trivially).
    """

    name: str = "Theorem_ExceptionSectionality"
    statement: str = (
        "For any ExceptionSection e at coordinate c, the restriction "
        "e.propagate_to(c') has trust <= trust(e) for all ancestor c'."
    )
    proof_sketch: str = (
        "propagate_to calls _decay_trust which calls step_weaker on TrustLevel. "
        "TrustLevel is a strictly ordered IntEnum, so step_weaker returns a "
        "strictly lesser value (or the minimum). QED."
    )
    trust: TrustLevel = field(default_factory=lambda: TrustLevel.SOLVER_DISCHARGED)

    def check(self, exc: Any, parent_coord: Any) -> bool:
        """Verify the sectionality invariant for one exception/coordinate pair.

        Calls ``exc.propagate_to(parent_coord)`` and asserts that the resulting
        section's trust level is numerically <= the original section's trust.

        Parameters
        ----------
        exc:
            An ``ExceptionSection`` (or duck-typed equivalent) with a
            ``propagate_to(coord)`` method and a ``.trust`` attribute.
        parent_coord:
            A ``Coordinate`` representing the ancestor to restrict to.

        Returns
        -------
        bool
            ``True`` if the invariant ``trust(restricted) <= trust(exc)``
            holds; ``False`` otherwise (which constitutes a theorem violation).
        """
        try:
            restricted = exc.propagate_to(parent_coord)
        except Exception:
            return False
        original_trust = _get_trust_int(getattr(exc, "trust", TrustLevel.UNVERIFIED))
        restricted_trust = _get_trust_int(getattr(restricted, "trust", TrustLevel.UNVERIFIED))
        return restricted_trust <= original_trust

    def check_batch(self, pairs: list[tuple[Any, Any]]) -> list[bool]:
        """Run the sectionality check for a batch of (exc, parent_coord) pairs.

        This is useful when validating all exception sections produced during a
        copilot-assisted analysis pass, where many restrictions are generated at
        once.

        Parameters
        ----------
        pairs:
            A list of ``(ExceptionSection, Coordinate)`` tuples.

        Returns
        -------
        list[bool]
            A list of booleans in the same order as ``pairs``.  Each entry is
            the result of ``self.check(exc, coord)`` for the corresponding pair.
        """
        return [self.check(exc, coord) for exc, coord in pairs]

    def verify_with_z3(self, exc: Any, parent_coord: Any, session: Any = None) -> bool:
        """Attempt to verify the sectionality theorem using Z3.

        Constructs a simple string formula encoding the trust inequality and
        submits it to the provided Z3 session.  If the session is ``None`` or
        the Z3 call raises, falls back to the structural ``check()`` method.

        Parameters
        ----------
        exc:
            The ``ExceptionSection`` to verify.
        parent_coord:
            The parent coordinate to restrict to.
        session:
            An optional ``Z3Session`` instance.  When ``None``, structural
            verification is used instead.

        Returns
        -------
        bool
            ``True`` if the invariant holds according to Z3 (or the fallback
            structural check).
        """
        if session is None:
            return self.check(exc, parent_coord)
        try:
            original_trust = _get_trust_int(getattr(exc, "trust", TrustLevel.UNVERIFIED))
            formula = (
                f"(assert (<= trust_restricted {original_trust}))"
            )
            session.assert_formula(formula)
            result = session.check()
            if result is not None and hasattr(result, "outcome"):
                if getattr(result, "outcome", None) == SolveOutcome.UNSAT:
                    return False
            return self.check(exc, parent_coord)
        except Exception:
            return self.check(exc, parent_coord)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``name``, ``statement``, ``proof_sketch``,
            ``trust``, and ``is_copilot_assisted``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "trust": getattr(self.trust, "label", lambda: str(self.trust))(),
            "is_copilot_assisted": self.is_copilot_assisted(),
        }

    def is_copilot_assisted(self) -> bool:
        """Return whether this theorem's current trust level is copilot-proposed.

        Theorems at ``ORACLE_PROPOSED`` or below are considered to be driven by
        copilot inference rather than independently discharged.  Such theorems
        require further verification before being accepted into the trusted base.

        Returns
        -------
        bool
            ``True`` if ``trust <= ORACLE_PROPOSED``.
        """
        return _get_trust_int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)

# ---
# Theorem_ContextScopeCovers
# ---

@dataclass
class Theorem_ContextScopeCovers:
    r"""Theorem: ContextScope.to_covering_family() satisfies covering axioms.

    theory2.tex Ch18 §18.6 — Theorem 18.2.

    Statement: For any ContextScope s with entry_coordinate c_enter and
    exit_coordinate c_exit (if set), the CoveringFamily F = s.to_covering_family()
    satisfies:
    1. F.base == c_enter
    2. F.patches is non-empty
    3. c_enter appears in F.patches (by coord_id equality)

    This is a minimal Grothendieck covering axiom: the base coordinate is
    covered by the family's patches.

    Proof sketch: to_covering_family() always includes entry_coordinate in
    patches, so patches contains c_enter. Since entry_coordinate is a valid
    coordinate, F.base = c_enter is covered. QED.
    """

    name: str = "Theorem_ContextScopeCovers"
    statement: str = (
        "For any ContextScope s, s.to_covering_family() has non-empty "
        "patches containing the entry coordinate."
    )
    proof_sketch: str = (
        "to_covering_family() constructs patches = (entry_coordinate,) if "
        "exit_coordinate is None, else (entry_coordinate, exit_coordinate). "
        "In both cases entry_coordinate is included and patches is non-empty."
    )
    trust: TrustLevel = field(default_factory=lambda: TrustLevel.SOLVER_DISCHARGED)

    def check(self, scope: Any) -> bool:
        """Verify the covering axiom for a single ContextScope.

        Calls ``scope.to_covering_family()`` and verifies three conditions:

        1. The family's ``base`` attribute is not ``None``.
        2. The family's ``patches`` sequence is non-empty.
        3. The scope's ``entry_coordinate`` appears in ``patches`` by
           ``coord_id`` equality.

        Parameters
        ----------
        scope:
            A ``ContextScope`` (or duck-typed equivalent) exposing
            ``to_covering_family()`` and ``entry_coordinate``.

        Returns
        -------
        bool
            ``True`` if all three covering conditions are satisfied.
        """
        try:
            family = scope.to_covering_family()
        except Exception:
            return False
        base = getattr(family, "base", None)
        if base is None:
            return False
        patches = getattr(family, "patches", ())
        if not patches:
            return False
        entry = getattr(scope, "entry_coordinate", None)
        entry_id = _coord_id(entry)
        patch_ids = {_coord_id(p) for p in patches}
        return entry_id in patch_ids

    def check_batch(self, scopes: list[Any]) -> list[bool]:
        """Run the covering axiom check for a list of ContextScope objects.

        Copilot may generate multiple scopes during a single analysis pass;
        this method validates all of them in one call.

        Parameters
        ----------
        scopes:
            A list of ``ContextScope`` instances.

        Returns
        -------
        list[bool]
            Results of ``self.check(scope)`` for each element.
        """
        return [self.check(scope) for scope in scopes]

    def verify_with_z3(self, scope: Any, session: Any = None) -> bool:
        """Verify covering axiom; Z3 is not required for this structural axiom.

        The covering axiom is purely structural (membership in a tuple), so
        Z3 encoding provides no additional guarantee.  This method therefore
        always delegates to the structural ``check()``.

        Parameters
        ----------
        scope:
            A ``ContextScope`` instance.
        session:
            Ignored; present for interface uniformity.

        Returns
        -------
        bool
            Result of ``self.check(scope)``.
        """
        return self.check(scope)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``name``, ``statement``, ``proof_sketch``,
            ``trust``, and ``is_copilot_assisted``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "trust": getattr(self.trust, "label", lambda: str(self.trust))(),
            "is_copilot_assisted": self.is_copilot_assisted(),
        }

    def is_copilot_assisted(self) -> bool:
        """Return whether this theorem relies on copilot inference.

        Returns
        -------
        bool
            ``True`` if ``trust <= ORACLE_PROPOSED``.
        """
        return _get_trust_int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)

# ---
# Theorem_AsyncTopologicalOrder
# ---

@dataclass
class Theorem_AsyncTopologicalOrder:
    r"""Theorem: Async await-dependency graphs are DAGs.

    theory2.tex Ch18 §18.3 — Theorem 18.3.

    Statement: For a valid set of AsyncSections, the directed graph G where
    task t1 -> t2 iff t2.coordinate is in t1.awaited_coordinates is a DAG
    (directed acyclic graph). Equivalently, there exist no circular await
    dependencies.

    Circular awaits would cause deadlock, which is disallowed by the
    well-formedness condition on the async sub-site.  Copilot-proposed tasks
    that introduce cycles are flagged with ORACLE_PROPOSED trust and rejected.

    Proof sketch: The check() method runs DFS cycle detection. If a back-edge
    is found, the theorem is falsified for that task set.
    """

    name: str = "Theorem_AsyncTopologicalOrder"
    statement: str = (
        "The await-dependency graph of any valid set of AsyncSections is "
        "acyclic (a DAG)."
    )
    proof_sketch: str = (
        "By structural induction on task registration: each task may only "
        "await coordinates of already-registered tasks, preventing cycles. "
        "The DFS check provides the runtime verification."
    )
    trust: TrustLevel = field(default_factory=lambda: TrustLevel.SOLVER_DISCHARGED)

    # DFS colour constants
    _WHITE: int = field(default=0, init=False, repr=False, compare=False)
    _GRAY: int = field(default=1, init=False, repr=False, compare=False)
    _BLACK: int = field(default=2, init=False, repr=False, compare=False)

    def _build_adjacency(self, tasks: list[Any]) -> dict[str, list[str]]:
        """Build the await-dependency adjacency list.

        The graph has an edge t1_id -> t2_id when ``t2`` awaits a coordinate
        whose ``coord_id`` matches ``t1.coordinate.coord_id``.  This maps
        "awaited" relationships to task identifiers so that cycle detection can
        operate on task_ids directly.

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        dict[str, list[str]]
            Mapping from task_id to the list of task_ids it is awaited by (i.e.
            its *dependents* in the graph).  Copilot should note that this is the
            reverse of the raw await edge for downstream propagation analysis.
        """
        coord_to_task: dict[str, str] = {}
        for t in tasks:
            cid = _coord_id(getattr(t, "coordinate", None))
            tid = getattr(t, "task_id", "") or ""
            if cid:
                coord_to_task[cid] = tid

        adj: dict[str, list[str]] = {
            getattr(t, "task_id", "") or "": [] for t in tasks
        }
        for t in tasks:
            tid = getattr(t, "task_id", "") or ""
            awaited = getattr(t, "awaited_coordinates", ()) or ()
            for coord in awaited:
                dep_id = coord_to_task.get(_coord_id(coord))
                if dep_id and dep_id != tid:
                    adj.setdefault(dep_id, []).append(tid)
        return adj

    def check(self, tasks: list[Any]) -> bool:
        """Verify that the await-dependency graph contains no cycles.

        Performs a DFS with WHITE/GRAY/BLACK colouring on the directed graph
        produced by ``_build_adjacency``.  A GRAY node encountered during DFS
        indicates a back-edge (cycle).

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        bool
            ``True`` if the graph is a DAG (no cycle found); ``False`` if a
            cycle exists, falsifying the theorem for this task set.
        """
        adj = self._build_adjacency(tasks)
        colour: dict[str, int] = {node: 0 for node in adj}

        def dfs(node: str) -> bool:
            colour[node] = 1
            for neighbour in adj.get(node, []):
                if colour.get(neighbour, 0) == 1:
                    return False  # back-edge: cycle detected
                if colour.get(neighbour, 0) == 0:
                    if not dfs(neighbour):
                        return False
            colour[node] = 2
            return True

        for node in list(adj.keys()):
            if colour.get(node, 0) == 0:
                if not dfs(node):
                    return False
        return True

    def find_cycle(self, tasks: list[Any]) -> list[str] | None:
        """Return the task_ids forming a cycle, or None if the graph is acyclic.

        Uses DFS with explicit path tracking so that the cycle can be reported
        to callers for diagnostic purposes.  Copilot-generated task graphs that
        fail this check will have the offending cycle surfaced here.

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        list[str] | None
            A list of ``task_id`` strings forming the cycle (starting and ending
            at the same node), or ``None`` if no cycle is present.
        """
        adj = self._build_adjacency(tasks)
        colour: dict[str, int] = {node: 0 for node in adj}
        path: list[str] = []
        path_set: set[str] = set()
        found: list[list[str]] = []

        def dfs(node: str) -> bool:
            colour[node] = 1
            path.append(node)
            path_set.add(node)
            for neighbour in adj.get(node, []):
                if colour.get(neighbour, 0) == 1 and neighbour in path_set:
                    cycle_start = path.index(neighbour)
                    found.append(path[cycle_start:] + [neighbour])
                    return True
                if colour.get(neighbour, 0) == 0:
                    if dfs(neighbour):
                        return True
            colour[node] = 2
            path.pop()
            path_set.discard(node)
            return False

        for node in list(adj.keys()):
            if colour.get(node, 0) == 0:
                if dfs(node):
                    return found[0] if found else None
        return None

    def verify_with_z3(self, tasks: list[Any], session: Any = None) -> bool:
        """Verify the DAG theorem; falls back to structural DFS check.

        Encoding cycle-freeness in Z3 would require transitive closure encoding
        which is expensive; the structural DFS is exact and sufficient.  The
        Z3 session parameter is accepted for interface uniformity but not used.

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.
        session:
            An optional ``Z3Session`` (unused here).

        Returns
        -------
        bool
            Result of ``self.check(tasks)``.
        """
        return self.check(tasks)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``name``, ``statement``, ``proof_sketch``,
            ``trust``, and ``is_copilot_assisted``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "trust": getattr(self.trust, "label", lambda: str(self.trust))(),
            "is_copilot_assisted": self.is_copilot_assisted(),
        }

    def is_copilot_assisted(self) -> bool:
        """Return whether this theorem relies on copilot inference.

        Returns
        -------
        bool
            ``True`` if ``trust <= ORACLE_PROPOSED``.
        """
        return _get_trust_int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)

# ---
# Theorem_GeneratorFiberSequence
# ---

@dataclass
class Theorem_GeneratorFiberSequence:
    r"""Theorem: Generator fiber coordinates form a valid restriction sequence.

    theory2.tex Ch18 §18.5 — Theorem 18.4.

    Statement: For a sequence of GeneratorSection objects gs[0], gs[1], ...,
    gs[n] produced by advancing the same generator (same gen_id, sequential
    yield_indices 0, 1, ..., n), the fiber_coordinate() values form a valid
    restriction sequence: each fiber_coordinate(gs[i+1]) is derived from
    fiber_coordinate(gs[i]) by incrementing yield_index, and the coord_ids
    are consistently derived via _stable_hash.

    This ensures the generator's fiber coordinates are monotonically indexed
    and form a chain in the site's restriction order.

    Proof sketch: fiber_coordinate() derives coord_id = _stable_hash(
    f"{base_coord_id}:{yield_index}")[:16]. Sequential yield indices produce
    a chain of distinct coordinates with a canonical derivation. QED.
    """

    name: str = "Theorem_GeneratorFiberSequence"
    statement: str = (
        "The fiber_coordinate() values of a generator's sequential "
        "GeneratorSections form a valid restriction sequence."
    )
    proof_sketch: str = (
        "fiber_coordinate() derives each coordinate from the base "
        "coordinate and the yield_index via _stable_hash. Monotonically "
        "increasing yield_indices produce distinct, consistently derived "
        "coordinates forming a chain in the site."
    )
    trust: TrustLevel = field(default_factory=lambda: TrustLevel.SOLVER_DISCHARGED)

    def check(self, sections: list[Any]) -> bool:
        """Verify the fiber restriction sequence for a list of GeneratorSections.

        Three conditions are tested:

        1. All sections share the same ``gen_id``.
        2. The ``yield_index`` values are exactly ``0, 1, ..., n-1`` in order.
        3. For each section, ``fiber_coordinate().coord_id`` equals
           ``_stable_hash(f"{base_coord_id}:{yield_index}")[:16]``.

        Parameters
        ----------
        sections:
            A list of ``GeneratorSection`` objects, expected to be in
            yield-index order.

        Returns
        -------
        bool
            ``True`` if all three conditions hold; ``False`` otherwise.
        """
        if not sections:
            return True

        gen_ids = {getattr(s, "gen_id", None) for s in sections}
        if len(gen_ids) != 1:
            return False

        if not self.check_yield_monotonicity(sections):
            return False

        for section in sections:
            base_coord_id = _coord_id(getattr(section, "coordinate", None))
            yield_index = getattr(section, "yield_index", -1)
            expected_id = _stable_hash(f"{base_coord_id}:{yield_index}")[:16]
            try:
                fiber_coord = section.fiber_coordinate()
            except Exception:
                return False
            actual_id = _coord_id(fiber_coord)
            if actual_id != expected_id:
                return False

        return True

    def check_yield_monotonicity(self, sections: list[Any]) -> bool:
        """Verify that yield_indices are exactly 0, 1, 2, …, n-1 without gaps.

        A generator's sections must be presented in strict sequential order
        for the restriction chain to be valid.  Copilot-generated section lists
        that skip or repeat indices violate the chain condition.

        Parameters
        ----------
        sections:
            A list of ``GeneratorSection`` objects.

        Returns
        -------
        bool
            ``True`` if the yield_index sequence is exactly ``range(len(sections))``.
        """
        if not sections:
            return True
        indices = [getattr(s, "yield_index", None) for s in sections]
        if any(idx is None for idx in indices):
            return False
        return indices == list(range(len(sections)))

    def verify_with_z3(self, sections: list[Any], session: Any = None) -> bool:
        """Verify the fiber sequence theorem; delegates to structural check.

        The correctness of ``_stable_hash``-derived coord_ids is a deterministic
        computation that does not benefit from SMT encoding.

        Parameters
        ----------
        sections:
            A list of ``GeneratorSection`` objects.
        session:
            An optional ``Z3Session`` (unused here).

        Returns
        -------
        bool
            Result of ``self.check(sections)``.
        """
        return self.check(sections)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``name``, ``statement``, ``proof_sketch``,
            ``trust``, and ``is_copilot_assisted``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "trust": getattr(self.trust, "label", lambda: str(self.trust))(),
            "is_copilot_assisted": self.is_copilot_assisted(),
        }

    def is_copilot_assisted(self) -> bool:
        """Return whether this theorem relies on copilot inference.

        Returns
        -------
        bool
            ``True`` if ``trust <= ORACLE_PROPOSED``.
        """
        return _get_trust_int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)

# ---
# Theorem_CancellationPropagation
# ---

@dataclass
class Theorem_CancellationPropagation:
    r"""Theorem: Cancellation propagation is monotone and cascade-complete.

    theory2.tex Ch18 §18.4 — Theorem 18.5.

    Statement: Given a set of CancellationRecord objects R and a dependency
    graph D (where t2 depends on t1 iff t1.coordinate is in t2.awaited_coordinates),
    if t1 is cancelled (there exists r in R with r.task_id == t1), then for
    all t2 such that (t2 depends on t1), t2 must also be cancelled (there
    exists r' in R with r'.task_id == t2 or t2 in r.propagated_to).

    Monotonicity: if t is cancelled, any task depending on t is also cancelled.
    Cascade-completeness: the propagated_to sets are transitively closed.

    Copilot-proposed cancellation records that violate this theorem are flagged
    for review with ORACLE_PROPOSED trust.

    Proof sketch: TaskRegistry.cancel() propagates to all dependents.
    detect_cancellation_cascade() builds the full propagation graph.
    The check() method verifies transitivity by BFS from each cancelled task.
    """

    name: str = "Theorem_CancellationPropagation"
    statement: str = (
        "Cancellation propagation is monotone: if t1 is cancelled and t2 "
        "depends on t1, then t2 is also cancelled."
    )
    proof_sketch: str = (
        "TaskRegistry.cancel() propagates to all direct dependents. "
        "check() verifies that for each cancelled task t1, all tasks in "
        "t1.propagated_to are also present in the cancellation record set, "
        "and their propagated_to sets are similarly closed (BFS)."
    )
    trust: TrustLevel = field(default_factory=lambda: TrustLevel.SOLVER_DISCHARGED)

    def _build_cancelled_set(self, records: list[Any]) -> set[str]:
        """Collect all task_ids that are considered cancelled from records.

        Includes both ``record.task_id`` values and all ids listed in each
        record's ``propagated_to`` tuple.

        Parameters
        ----------
        records:
            A list of ``CancellationRecord`` objects.

        Returns
        -------
        set[str]
            The full set of cancelled task_ids.
        """
        cancelled: set[str] = set()
        for rec in records:
            tid = getattr(rec, "task_id", None)
            if tid:
                cancelled.add(tid)
            for pid in getattr(rec, "propagated_to", ()):
                if pid:
                    cancelled.add(pid)
        return cancelled

    def _build_dependent_graph(self, tasks: list[Any]) -> dict[str, list[str]]:
        """Build a map from coord_id -> [task_ids that await that coordinate].

        Used to determine which tasks must be cancelled when a given task is
        cancelled: any task that awaits a cancelled task's coordinate must also
        be cancelled.

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        dict[str, list[str]]
            Mapping ``coord_id`` -> list of ``task_id`` strings that list that
            coord_id in their ``awaited_coordinates``.
        """
        coord_to_awaiters: dict[str, list[str]] = {}
        for task in tasks:
            tid = getattr(task, "task_id", "") or ""
            awaited = getattr(task, "awaited_coordinates", ()) or ()
            for coord in awaited:
                cid = _coord_id(coord)
                coord_to_awaiters.setdefault(cid, []).append(tid)
        return coord_to_awaiters

    def check(self, records: list[Any], tasks: list[Any]) -> bool:
        """Verify monotone cancellation propagation for a set of records and tasks.

        For each cancelled task, finds all tasks that directly await it (via
        coord_id matching) and checks that those dependent tasks are also in the
        cancelled set.

        Parameters
        ----------
        records:
            A list of ``CancellationRecord`` objects.
        tasks:
            A list of ``AsyncSection`` objects whose await dependencies are used
            to determine what must be cancelled.

        Returns
        -------
        bool
            ``True`` if every task that awaits a cancelled task is itself
            cancelled; ``False`` if a monotonicity violation is found.
        """
        cancelled_ids = self._build_cancelled_set(records)
        coord_to_awaiters = self._build_dependent_graph(tasks)

        coord_of_task: dict[str, str] = {}
        for task in tasks:
            tid = getattr(task, "task_id", "") or ""
            cid = _coord_id(getattr(task, "coordinate", None))
            if tid and cid:
                coord_of_task[tid] = cid

        for cancelled_tid in list(cancelled_ids):
            own_coord = coord_of_task.get(cancelled_tid, "")
            if not own_coord:
                continue
            dependent_tids = coord_to_awaiters.get(own_coord, [])
            for dep_tid in dependent_tids:
                if dep_tid not in cancelled_ids:
                    return False
        return True

    def find_violations(self, records: list[Any], tasks: list[Any]) -> list[str]:
        """Return task_ids that should be cancelled but are missing from records.

        Identifies all tasks that await a cancelled task yet are themselves not
        present in the cancellation record set.  An empty list means the theorem
        holds for this input.

        Copilot-generated cancellation sets often require manual review;
        this method surfaces the exact violations for triage.

        Parameters
        ----------
        records:
            A list of ``CancellationRecord`` objects.
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        list[str]
            Sorted list of ``task_id`` strings that violate the monotonicity
            condition.
        """
        cancelled_ids = self._build_cancelled_set(records)
        coord_to_awaiters = self._build_dependent_graph(tasks)

        coord_of_task: dict[str, str] = {}
        for task in tasks:
            tid = getattr(task, "task_id", "") or ""
            cid = _coord_id(getattr(task, "coordinate", None))
            if tid and cid:
                coord_of_task[tid] = cid

        violations: set[str] = set()
        for cancelled_tid in list(cancelled_ids):
            own_coord = coord_of_task.get(cancelled_tid, "")
            if not own_coord:
                continue
            for dep_tid in coord_to_awaiters.get(own_coord, []):
                if dep_tid not in cancelled_ids:
                    violations.add(dep_tid)
        return sorted(violations)

    def verify_with_z3(self, records: list[Any], tasks: list[Any], session: Any = None) -> bool:
        """Verify cancellation monotonicity; falls back to structural BFS check.

        The monotonicity condition is a graph reachability property.  SMT
        encoding is possible but the structural BFS is exact for finite task
        sets, so this method delegates to ``self.check``.

        Parameters
        ----------
        records:
            A list of ``CancellationRecord`` objects.
        tasks:
            A list of ``AsyncSection`` objects.
        session:
            An optional ``Z3Session`` (unused here).

        Returns
        -------
        bool
            Result of ``self.check(records, tasks)``.
        """
        return self.check(records, tasks)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``name``, ``statement``, ``proof_sketch``,
            ``trust``, and ``is_copilot_assisted``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "trust": getattr(self.trust, "label", lambda: str(self.trust))(),
            "is_copilot_assisted": self.is_copilot_assisted(),
        }

    def is_copilot_assisted(self) -> bool:
        """Return whether this theorem relies on copilot inference.

        Returns
        -------
        bool
            ``True`` if ``trust <= ORACLE_PROPOSED``.
        """
        return _get_trust_int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)

# ---
# TheoremSuite — unified bundle of all five Ch18 theorems
# ---

@dataclass
class TheoremSuite:
    r"""Bundle of all five Ch18 formal theorems.

    theory2.tex Ch18 §18.9 — Theorem suite for formal verification.

    Provides a unified interface for running all five Ch18 theorems.
    Copilot-assisted theorem proofs are tagged appropriately; the suite
    reports which theorems required copilot assistance.

    Parameters
    ----------
    exc_sectionality:
        Theorem_ExceptionSectionality instance.
    scope_covers:
        Theorem_ContextScopeCovers instance.
    async_dag:
        Theorem_AsyncTopologicalOrder instance.
    gen_fiber_seq:
        Theorem_GeneratorFiberSequence instance.
    cancel_prop:
        Theorem_CancellationPropagation instance.
    """

    exc_sectionality: Theorem_ExceptionSectionality = field(
        default_factory=Theorem_ExceptionSectionality
    )
    scope_covers: Theorem_ContextScopeCovers = field(
        default_factory=Theorem_ContextScopeCovers
    )
    async_dag: Theorem_AsyncTopologicalOrder = field(
        default_factory=Theorem_AsyncTopologicalOrder
    )
    gen_fiber_seq: Theorem_GeneratorFiberSequence = field(
        default_factory=Theorem_GeneratorFiberSequence
    )
    cancel_prop: Theorem_CancellationPropagation = field(
        default_factory=Theorem_CancellationPropagation
    )

    def check_all_exceptions(self, pairs: list[tuple[Any, Any]]) -> list[bool]:
        """Run the exception sectionality check for all provided pairs.

        Delegates directly to
        ``Theorem_ExceptionSectionality.check_batch(pairs)``.

        Parameters
        ----------
        pairs:
            A list of ``(ExceptionSection, Coordinate)`` tuples.

        Returns
        -------
        list[bool]
            One boolean per pair indicating whether the theorem holds.
        """
        return self.exc_sectionality.check_batch(pairs)

    def check_all_scopes(self, scopes: list[Any]) -> list[bool]:
        """Run the covering axiom check for all provided ContextScope objects.

        Delegates directly to
        ``Theorem_ContextScopeCovers.check_batch(scopes)``.

        Parameters
        ----------
        scopes:
            A list of ``ContextScope`` instances.

        Returns
        -------
        list[bool]
            One boolean per scope indicating whether the theorem holds.
        """
        return self.scope_covers.check_batch(scopes)

    def check_async_dag(self, tasks: list[Any]) -> bool:
        """Verify that the await-dependency graph over tasks is a DAG.

        Delegates to ``Theorem_AsyncTopologicalOrder.check(tasks)``.

        Parameters
        ----------
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        bool
            ``True`` if the graph is acyclic.
        """
        return self.async_dag.check(tasks)

    def check_generator_sequence(self, sections: list[Any]) -> bool:
        """Verify that generator sections form a valid fiber restriction chain.

        Delegates to ``Theorem_GeneratorFiberSequence.check(sections)``.

        Parameters
        ----------
        sections:
            A list of ``GeneratorSection`` objects in yield-index order.

        Returns
        -------
        bool
            ``True`` if all three fiber-sequence conditions hold.
        """
        return self.gen_fiber_seq.check(sections)

    def check_cancellation_monotone(self, records: list[Any], tasks: list[Any]) -> bool:
        """Verify that cancellation records satisfy the monotonicity condition.

        Delegates to ``Theorem_CancellationPropagation.check(records, tasks)``.

        Parameters
        ----------
        records:
            A list of ``CancellationRecord`` objects.
        tasks:
            A list of ``AsyncSection`` objects.

        Returns
        -------
        bool
            ``True`` if the monotonicity condition holds.
        """
        return self.cancel_prop.check(records, tasks)

    def run_all(
        self,
        exc_pairs: list[tuple[Any, Any]] | None = None,
        scopes: list[Any] | None = None,
        tasks: list[Any] | None = None,
        gen_sections: list[Any] | None = None,
        cancel_records: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Run all applicable theorem checks and return a consolidated report.

        Only runs theorems for which data is provided.  Copilot-generated
        inputs are verified and violations are included in the summary.

        Parameters
        ----------
        exc_pairs:
            List of ``(ExceptionSection, Coordinate)`` pairs for
            ``Theorem_ExceptionSectionality``.  Pass ``None`` to skip.
        scopes:
            List of ``ContextScope`` objects for
            ``Theorem_ContextScopeCovers``.  Pass ``None`` to skip.
        tasks:
            List of ``AsyncSection`` objects for
            ``Theorem_AsyncTopologicalOrder`` and, together with
            ``cancel_records``, for ``Theorem_CancellationPropagation``.
        gen_sections:
            List of ``GeneratorSection`` objects for
            ``Theorem_GeneratorFiberSequence``.  Pass ``None`` to skip.
        cancel_records:
            List of ``CancellationRecord`` objects for
            ``Theorem_CancellationPropagation``.  Pass ``None`` to skip.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``exc_sectionality``, ``scope_covers``,
            ``async_dag``, ``gen_fiber_seq``, ``cancel_prop`` (each
            ``True`` / ``False`` / ``None``), and a ``summary`` sub-dict
            with ``passed``, ``failed``, and ``skipped`` counts.
        """
        results: dict[str, Any] = {
            "exc_sectionality": None,
            "scope_covers": None,
            "async_dag": None,
            "gen_fiber_seq": None,
            "cancel_prop": None,
        }

        if exc_pairs is not None:
            batch = self.check_all_exceptions(exc_pairs)
            results["exc_sectionality"] = all(batch) if batch else True

        if scopes is not None:
            batch = self.check_all_scopes(scopes)
            results["scope_covers"] = all(batch) if batch else True

        if tasks is not None:
            results["async_dag"] = self.check_async_dag(tasks)

        if gen_sections is not None:
            results["gen_fiber_seq"] = self.check_generator_sequence(gen_sections)

        if cancel_records is not None and tasks is not None:
            results["cancel_prop"] = self.check_cancellation_monotone(cancel_records, tasks)
        elif cancel_records is not None:
            results["cancel_prop"] = self.check_cancellation_monotone(cancel_records, [])

        passed = sum(1 for v in results.values() if v is True)
        failed = sum(1 for v in results.values() if v is False)
        skipped = sum(1 for v in results.values() if v is None)

        results["summary"] = {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": passed + failed + skipped,
        }
        return results

    def to_dict(self) -> dict[str, Any]:
        """Serialise all five theorems to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary keyed by theorem attribute name, each value being the
            result of that theorem's ``to_dict()`` method.
        """
        return {
            "exc_sectionality": self.exc_sectionality.to_dict(),
            "scope_covers": self.scope_covers.to_dict(),
            "async_dag": self.async_dag.to_dict(),
            "gen_fiber_seq": self.gen_fiber_seq.to_dict(),
            "cancel_prop": self.cancel_prop.to_dict(),
        }

    def copilot_assisted_theorems(self) -> list[str]:
        """Return the names of theorems that are tagged as copilot-assisted.

        A theorem is copilot-assisted when its trust level is at or below
        ``ORACLE_PROPOSED``.  Such theorems require independent human or solver
        verification before being admitted into the trusted proof base.

        Returns
        -------
        list[str]
            List of theorem ``name`` strings for which ``is_copilot_assisted()``
            returns ``True``.
        """
        assisted: list[str] = []
        for theorem in (
            self.exc_sectionality,
            self.scope_covers,
            self.async_dag,
            self.gen_fiber_seq,
            self.cancel_prop,
        ):
            if theorem.is_copilot_assisted():
                assisted.append(theorem.name)
        return assisted

# ---
# Public API
# ---

__all__ = [
    "Theorem_ExceptionSectionality",
    "Theorem_ContextScopeCovers",
    "Theorem_AsyncTopologicalOrder",
    "Theorem_GeneratorFiberSequence",
    "Theorem_CancellationPropagation",
    "TheoremSuite",
]
