from __future__ import annotations

r"""Package: jugeo.python_runtime.effects_async.exceptions_as_alternate_semantic_p
theory2.tex Ch18 §18.S01 — Exceptions as Alternate Semantic Paths

# copilot: exceptions-as-alternate-semantic-paths — models exception handling as coordinate forks in the judgment site, where each try/except bifurcates the semantic path into a normal coordinate and one or more exception handler coordinates

Python exceptions are modelled as alternate-path sections in the semantic site.
Each try/except block creates a coordinate fork: the normal path continues to
one coordinate, while the exception path routes to the handler coordinate.
The BaseException hierarchy defines a partial order on exception coordinates.

ExceptionPath is a coordinate in the judgment site at which the alternate
semantic path is located. When an exception is raised, execution "teleports"
to the handler coordinate. try/finally is modelled as a temporal obligation:
the finally block must execute regardless of which path is taken.

Exception chaining (raise X from Y) corresponds to section restriction:
the chained exception is the restriction of the original to the parent
coordinate. The raise/except pair is a sheaf morphism: raise creates a
section, except collapses it.

All copilot-assisted encoding of exception paths enters at ORACLE_PROPOSED
trust and requires runtime confirmation to advance to RUNTIME_WITNESSED.

See also
--------
* jugeo.python_runtime.effects_async.models — ExceptionSection, CancellationRecord
* jugeo.python_runtime.effects_async.algorithms — propagate_exception_through_site
* jugeo.python_runtime.effects_async.exceptions — lower-level exception sections
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

# ---
# Local model imports — stubs accepted if package not yet installed
# ---

try:
    from jugeo.python_runtime.effects_async.models import (
        ExceptionSection, ContextScope, AsyncSection, GeneratorSection, CancellationRecord,
    )
except ImportError:
    # Minimal stubs so this module is importable in isolation
    @dataclass(frozen=True, slots=True)
    class ExceptionSection:
        """Stub: an exception section in the judgment site."""
        section_id: str = ""; exc_type_name: str = ""; exc_message: str = ""
        coordinate: object = None; trust: object = None; chained_from: object = None
        traceback_summary: str = ""; is_suppressed: bool = False; raised_at: str = ""
    @dataclass(frozen=True, slots=True)
    class ContextScope:
        """Stub: a context manager scope in the judgment site."""
        scope_id: str = ""; entry_coordinate: object = None; exit_coordinate: object = None
        covering_family: object = None; trust: object = None; is_open: bool = False
        residuals: tuple = (); entered_at: str = ""
    @dataclass(frozen=True, slots=True)
    class AsyncSection:
        """Stub: an async coroutine section in the judgment site."""
        section_id: str = ""; coro_name: str = ""; status: str = "pending"
        coordinate: object = None; trust: object = None; result: object = None
        exception: object = None; created_at: str = ""
    @dataclass(frozen=True, slots=True)
    class GeneratorSection:
        """Stub: a generator section in the judgment site."""
        section_id: str = ""; gen_name: str = ""; yield_count: int = 0
        coordinate: object = None; trust: object = None; is_exhausted: bool = False
    @dataclass(frozen=True, slots=True)
    class CancellationRecord:
        """Stub: a cancellation record for an async task."""
        record_id: str = ""; task_id: str = ""; reason: str = ""
        coordinate: object = None; trust: object = None; cancelled_at: str = ""

# ---
# Module-level constants
# ---

_log = logging.getLogger(__name__)

# Channel name used when submitting evidence or analysis requests to copilot
_ANALYSIS_CHANNEL: str = "copilot-s01-exceptions-as-alternate-semantic-paths"

# Version string for this section — used in provenance records
_SECTION_VERSION: str = "s01.2"

# Maximum nesting depth for try/except blocks before we emit a warning
_MAX_HANDLER_DEPTH: int = 32

# Maximum length of an exception __cause__/__context__ chain before we truncate
_MAX_EXCEPTION_CHAIN_LENGTH: int = 16

# All copilot-proposed paths start at ORACLE_PROPOSED and are stepped up only
# when the runtime confirms execution actually traversed that coordinate
_DEFAULT_TRUST_LEVEL = TrustLevel.ORACLE_PROPOSED

# Bare-except clauses incur a scoring penalty because they shadow BaseException,
# KeyboardInterrupt, and SystemExit — widening the coordinate set unpredictably
_BARE_EXCEPT_PENALTY: float = 0.5

# Coordinate id prefix for exception-type coordinates
_EXCEPTION_COORDINATE_PREFIX: str = "exc"

# Coordinate id prefix for handler coordinates
_HANDLER_COORDINATE_PREFIX: str = "handler"

# Obligation id prefix for finally-block temporal obligations
_FINALLY_OBLIGATION_PREFIX: str = "finally-obligation"

# Divergence scores >= this threshold are flagged in the path report
_PATH_DIVERGENCE_THRESHOLD: float = 0.7

# Maximum number of traceback frames to include in a summary
_MAX_TRACEBACK_FRAMES: int = 50

# Module-level cache: exc_type_name -> ExceptionKindRecord, populated at runtime
_EXCEPTION_HIERARCHY_CACHE: dict = {}

# Pattern keys used in handler classification
_RERAISE_PATTERN_KEY: str = "reraise"
_BARE_EXCEPT_PATTERN_KEY: str = "bare_except"
_CHAINED_EXCEPTION_PATTERN_KEY: str = "chained"
_EXCEPTION_GROUP_PATTERN_KEY: str = "exception_group"

# ---
# Dataclasses
# ---


@dataclass(frozen=True, slots=True)
class ExceptionPath:
    r"""A coordinate fork created by a try/except block.

    theory2.tex Ch18 §18.S01 — each ``try``/``except`` block is modelled as a
    *bifurcation* in the semantic path: execution proceeds normally to
    ``normal_coordinate`` when no exception is raised, or teleports to one of
    the ``handler_coordinates`` when an exception matching the handler's type
    predicate is raised.

    Sheaf perspective
    -----------------
    The normal and each handler coordinate are *sections over disjoint opens*
    in the covering topology of the try-site.  The covering family
    ``{normal_open, handler_open_1, …, handler_open_n}`` is exactly the set
    of paths that the try/except creates.  Gluing is possible only at the
    finally-block boundary (if present), where all paths must pass.

    Trust annotation
    ----------------
    Newly constructed ``ExceptionPath`` objects enter at ``ORACLE_PROPOSED``
    trust.  The trust is stepped up to ``RUNTIME_WITNESSED`` once a live
    exception traverses the corresponding handler coordinate.

    Parameters
    ----------
    path_id:
        Unique identifier for this coordinate fork.
    try_site:
        Human-readable source location, e.g. ``"line:42"``.
    normal_coordinate:
        The ``Coordinate`` reached when the try-body completes without raising.
    handler_coordinates:
        Tuple of ``(exc_type_name: str, Coordinate)`` pairs — one per
        ``except`` clause, in source order.
    finally_obligation_id:
        Non-empty string identifying the ``ResidualObligation`` for the
        finally block; empty if no finally block is present.
    has_bare_except:
        ``True`` when at least one ``except:`` clause (no type) is present.
        Bare excepts widen the coordinate set to all of ``BaseException``,
        which increases the path divergence score by ``_BARE_EXCEPT_PENALTY``.
    has_finally:
        ``True`` when a ``finally:`` clause is present.  The finally block
        is modelled as a *gluing condition*: both paths must satisfy the
        obligation encoded by ``finally_obligation_id``.
    trust:
        Current trust level for this fork.
    created_at:
        ISO-8601 UTC timestamp of fork creation.
    """

    path_id: str
    try_site: str
    normal_coordinate: object          # Coordinate
    handler_coordinates: tuple         # tuple of (exc_type_name: str, coordinate: Coordinate)
    finally_obligation_id: str
    has_bare_except: bool
    has_finally: bool
    trust: object                      # TrustLevel
    created_at: str

    # ---

    def get_handler_for(self, exc_type: type) -> object:
        """Return the handler Coordinate for *exc_type*, or ``None``.

        Walks the MRO of *exc_type* (from most-specific to least-specific) and
        returns the first handler coordinate whose ``exc_type_name`` matches
        a class in the MRO.  This mirrors Python's own handler-matching logic,
        which also checks the MRO in order.

        If a bare-except handler is present it always matches and is returned
        as a fallback (mapped under the key ``"BaseException"``).

        Parameters
        ----------
        exc_type:
            The type of the exception being raised.

        Returns
        -------
        Coordinate | None
            Matching handler coordinate, or ``None`` if no handler matches.
        """
        try:
            mro_names = [c.__name__ for c in inspect.getmro(exc_type)]
        except TypeError:
            mro_names = [getattr(exc_type, "__name__", str(exc_type))]

        # Build a lookup from exc_type_name -> Coordinate for O(1) probing
        handler_lookup: dict[str, object] = {
            exc_name: coord
            for exc_name, coord in self.handler_coordinates
        }

        # Walk MRO: return the most-specific matching handler
        for name in mro_names:
            if name in handler_lookup:
                return handler_lookup[name]

        # Bare-except fallback — registered under "BaseException"
        if self.has_bare_except and "BaseException" in handler_lookup:
            return handler_lookup["BaseException"]

        return None

    def is_covered(self) -> bool:
        """Return ``True`` if all exceptions are covered by this fork.

        A fork is *fully covered* when it has a bare-except clause (which
        matches everything) or when ``BaseException`` is explicitly named in
        the handler list.  Partial coverage (only ``Exception`` handled, for
        instance) returns ``False`` because ``KeyboardInterrupt`` and
        ``SystemExit`` can escape.

        This predicate is used by the coordinator to decide whether a
        ``ResidualObligation`` must be emitted for unhandled paths.

        Returns
        -------
        bool
            ``True`` iff the handler set covers all of ``BaseException``.
        """
        if self.has_bare_except:
            return True
        covered_names = {exc_name for exc_name, _ in self.handler_coordinates}
        return "BaseException" in covered_names

    def path_count(self) -> int:
        """Return the total number of semantic paths created by this fork.

        The total is ``1`` (normal path) + the number of distinct handler
        clauses.  A finally block does *not* add a path because it is
        traversed by *all* paths; instead it adds a temporal obligation.

        Returns
        -------
        int
            1 + len(handler_coordinates).
        """
        return 1 + len(self.handler_coordinates)

    def divergence_adjusted_trust(self, divergence_score: float) -> object:
        """Return a trust level adjusted downward for high-divergence paths.

        When the divergence score between the normal and handler coordinates
        exceeds ``_PATH_DIVERGENCE_THRESHOLD``, the trust level is stepped
        down by one level.  This encodes the intuition that copilot proposals
        for exotic exception paths are less likely to be correct.

        Parameters
        ----------
        divergence_score:
            Float in ``[0.0, 1.0]`` from ``compute_path_divergence``.

        Returns
        -------
        TrustLevel
            Possibly-weakened trust level.
        """
        if divergence_score >= _PATH_DIVERGENCE_THRESHOLD and hasattr(self.trust, "step_weaker"):
            return self.trust.step_weaker()
        return self.trust


@dataclass(frozen=True, slots=True)
class ExceptionKindRecord:
    r"""Classification of an exception kind in the BaseException partial order.

    theory2.tex Ch18 §18.S01 — the BaseException hierarchy induces a partial
    order on exception coordinates.  Each exception type occupies a coordinate
    determined by its position in the MRO.  This record captures that position
    plus a set of boolean flags for well-known sub-hierarchies that have
    special treatment in CPython semantics.

    Instances are cached in ``_EXCEPTION_HIERARCHY_CACHE`` keyed by
    ``exc_type_name`` to avoid repeated ``inspect.getmro`` calls.

    Parameters
    ----------
    kind_id:
        Unique record identifier (UUID-based).
    exc_type_name:
        ``__name__`` of the classified exception type.
    mro_names:
        Tuple of ``__name__`` strings for the full MRO, from most-specific
        to ``object``.
    is_warning:
        ``True`` if ``Warning`` appears in the MRO.
    is_system_exit:
        ``True`` if ``SystemExit`` appears in the MRO.
    is_keyboard_interrupt:
        ``True`` if ``KeyboardInterrupt`` appears in the MRO.
    is_runtime_error:
        ``True`` if ``RuntimeError`` appears in the MRO.
    is_value_error:
        ``True`` if ``ValueError`` appears in the MRO.
    is_lookup_error:
        ``True`` if ``LookupError`` appears in the MRO.
    trust:
        Trust level for this classification.  Runtime-witnessed classifications
        are ``RUNTIME_WITNESSED``; static-analysis-only classifications are
        ``ORACLE_PROPOSED``.
    classified_at:
        ISO-8601 UTC timestamp.
    """

    kind_id: str
    exc_type_name: str
    mro_names: tuple           # tuple of str — full MRO from most to least specific
    is_warning: bool
    is_system_exit: bool
    is_keyboard_interrupt: bool
    is_runtime_error: bool
    is_value_error: bool
    is_lookup_error: bool
    trust: object              # TrustLevel
    classified_at: str

    def mro_depth(self) -> int:
        """Return the number of classes in the MRO (depth in the hierarchy).

        Deeper MRO chains indicate more specific exception types and therefore
        narrower handler coordinates.

        Returns
        -------
        int
            ``len(self.mro_names)``.
        """
        return len(self.mro_names)

    def is_recoverable(self) -> bool:
        """Return ``True`` if this exception kind is considered recoverable.

        An exception is considered *unrecoverable* if it is a ``SystemExit``
        or ``KeyboardInterrupt``.  All other exceptions (including bare
        ``BaseException`` subclasses that are not in those two branches) are
        considered recoverable for the purposes of the path analysis.

        Returns
        -------
        bool
        """
        return not (self.is_system_exit or self.is_keyboard_interrupt)

    def coordinate_label(self) -> str:
        """Return a canonical coordinate label for this exception kind.

        The label encodes the top-level category so that handler coordinates
        can be grouped by exception family in the path report.

        Returns
        -------
        str
            A dot-separated path such as ``"exc.warning.DeprecationWarning"``.
        """
        if self.is_warning:
            return f"exc.warning.{self.exc_type_name}"
        if self.is_system_exit:
            return f"exc.system.{self.exc_type_name}"
        if self.is_keyboard_interrupt:
            return f"exc.interrupt.{self.exc_type_name}"
        if self.is_runtime_error:
            return f"exc.runtime.{self.exc_type_name}"
        if self.is_value_error:
            return f"exc.value.{self.exc_type_name}"
        if self.is_lookup_error:
            return f"exc.lookup.{self.exc_type_name}"
        return f"exc.general.{self.exc_type_name}"


@dataclass(frozen=True, slots=True)
class ExceptionWitnessRecord:
    r"""A record of a single witnessed exception event in the runtime.

    theory2.tex Ch18 §18.S01 — each runtime exception event advances trust
    from ``ORACLE_PROPOSED`` to ``RUNTIME_WITNESSED`` for the coordinates that
    were actually traversed.  This record captures the full context needed to
    update the site topology after the fact.

    Witness records are the primary input to ``generate_path_evidence``, which
    converts them into ``EvidenceBundle`` objects that the judgment algebra can
    consume.

    Parameters
    ----------
    record_id:
        Unique identifier (UUID-based).
    event_type:
        One of ``"raised"``, ``"handled"``, ``"suppressed"``, ``"propagated"``,
        ``"chained"``.  Maps to the edge type in the exception-path graph.
    exc_type_name:
        ``__name__`` of the exception type.
    exc_message:
        Truncated ``str(exc)`` — at most 400 characters.
    site:
        Source location where the event occurred (file:line or function name).
    handler_type_name:
        ``__name__`` of the handler type that caught the exception; empty when
        the event is ``"raised"`` or ``"propagated"``.
    is_suppressed:
        ``True`` when the handler returned normally without re-raising.
    chained_from_id:
        A stable identifier derived from the ``__cause__`` or ``__context__``
        of the exception, or empty string if there is no chain.
    trust:
        Trust level for this record.  Always ``RUNTIME_WITNESSED`` for live
        exceptions; ``ORACLE_PROPOSED`` for synthetic/copilot-generated records.
    witnessed_at:
        ISO-8601 UTC timestamp.
    """

    record_id: str
    event_type: str            # "raised" | "handled" | "suppressed" | "propagated" | "chained"
    exc_type_name: str
    exc_message: str
    site: str
    handler_type_name: str     # empty if not yet handled
    is_suppressed: bool
    chained_from_id: str
    trust: object              # TrustLevel
    witnessed_at: str

    def is_terminal(self) -> bool:
        """Return ``True`` if this record ends the exception's path through the site.

        Terminal events are ``"suppressed"`` (handler swallowed the exception)
        or (implicitly) a top-level ``"propagated"`` that escapes the program.
        Non-terminal events are ``"raised"``, ``"handled"`` (caught but may
        re-raise), and ``"chained"`` (attached as ``__cause__``).

        Returns
        -------
        bool
        """
        return self.event_type in ("suppressed",)

    def to_evidence_payload(self) -> str:
        """Serialise this record to a JSON string for inclusion in an EvidenceItem.

        Only non-sensitive fields are included.  The message is truncated to
        200 characters.

        Returns
        -------
        str
            JSON-encoded payload string.
        """
        return json.dumps({
            "record_id": self.record_id,
            "event_type": self.event_type,
            "exc_type_name": self.exc_type_name,
            "exc_message": self.exc_message[:200],
            "site": self.site,
            "handler_type_name": self.handler_type_name,
            "is_suppressed": self.is_suppressed,
            "chained_from_id": self.chained_from_id,
            "witnessed_at": self.witnessed_at,
        })


# ---
# Main classes
# ---


class ExceptionsAlternateSemanticPathsCoordinator:
    r"""Main coordinator for exception path analysis in the judgment site.

    theory2.tex Ch18 §18.S01 — the coordinator orchestrates the static
    analysis (via ``ExceptionsAlternateSemanticPathsAnalyzer``) and the
    runtime witnessing (via ``ExceptionsAlternateSemanticPathsWitness``) and
    assembles the results into a coherent picture of the exception-path
    topology for a given Python source module or code fragment.

    Each ``try``/``except`` block creates a *coordinate fork*: the normal path
    continues to one coordinate, while the exception path routes to the handler
    coordinate.  This coordinator builds these forks as ``ExceptionPath``
    objects, registers them in a ``SiteBuilder``, and computes divergence
    scores between the normal and handler coordinates.

    Trust lifecycle
    ---------------
    1. ``analyze_exception_flow`` creates ``ExceptionPath`` objects at
       ``ORACLE_PROPOSED`` trust.
    2. ``classify_exception_kind`` steps up to ``RUNTIME_WITNESSED`` for live
       exceptions.
    3. The coordinator never asserts trust above ``RUNTIME_WITNESSED`` for
       exception paths — only the solver can reach ``SOLVER_DISCHARGED``.

    Thread safety
    -------------
    This class is **not** thread-safe.  Each thread or coroutine should
    construct its own coordinator.
    """

    def __init__(self, site_id: str = "") -> None:
        """Initialise the coordinator with an optional site identifier.

        Parameters
        ----------
        site_id:
            Unique identifier for the semantic site being analysed.  If empty,
            a random UUID-based ID is generated.
        """
        self._site_id = site_id or f"exc-site-{uuid.uuid4().hex[:8]}"
        # The analyzer performs pure static analysis on AST nodes
        self._analyzer = ExceptionsAlternateSemanticPathsAnalyzer()
        # The witness records live exception events from the runtime
        self._witness = ExceptionsAlternateSemanticPathsWitness()
        # Accumulated ExceptionPath forks from all analyzed try-blocks
        self._exception_paths: list[ExceptionPath] = []
        # ExceptionSection objects produced from live exceptions
        self._exception_sections: list[ExceptionSection] = []
        # SiteBuilder accumulates Coordinate and Morphism objects
        self._site_builder = SiteBuilder()
        # The built Site — None until build() is called explicitly
        self._built_site: object = None
        # Human-readable log of analysis steps for debugging / tracing
        self._analysis_log: list[str] = []
        # path_key -> divergence_score for all computed divergences
        self._path_divergence_scores: dict[str, float] = {}
        _log.debug("ExceptionsAlternateSemanticPathsCoordinator init site_id=%s", self._site_id)

    def analyze_exception_flow(self, source: str) -> dict[str, Any]:
        """Analyse the exception flow in the given Python source code.

        Parses *source*, finds all ``try``/``except`` blocks, builds
        ``ExceptionPath`` objects for each, and computes path divergence
        scores between the normal coordinate and each handler coordinate.

        The method is idempotent with respect to the coordinator's internal
        state: calling it multiple times with different sources accumulates
        paths.

        Parameters
        ----------
        source:
            Python source code to analyse.  Must be syntactically valid.

        Returns
        -------
        dict
            Summary dict with keys:

            * ``try_block_count`` — number of ``try`` statements found
            * ``handler_count`` — total number of ``except`` clauses
            * ``bare_except_count`` — number of bare ``except:`` clauses
            * ``reraise_count`` — number of bare ``raise`` statements inside
              handlers
            * ``finally_count`` — number of paths with a ``finally`` clause
            * ``exception_paths`` — list of ``path_id`` strings
            * ``divergence_scores`` — dict of ``"path_id:exc_name" -> float``
            * ``site_id`` — the coordinator's site identifier
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            _log.warning("SyntaxError parsing source for site %s: %s", self._site_id, e)
            return {"error": str(e), "try_block_count": 0, "site_id": self._site_id}

        try_blocks = self._analyzer.find_try_blocks(source)
        handler_info = self._analyzer.analyze_handler_chain(
            [h for tb in ast.walk(tree) if isinstance(tb, ast.Try) for h in tb.handlers]
        )
        bare_excepts = self._analyzer.detect_bare_except(tree)
        reraise_patterns = self._analyzer.find_reraise_patterns(tree)
        handler_map = self._analyzer.build_handler_map(tree)

        # Build ExceptionPath objects for each ast.Try node found
        paths: list[ExceptionPath] = []
        for try_node in (n for n in ast.walk(tree) if isinstance(n, ast.Try)):
            path = self.build_exception_paths(try_node)
            paths.append(path)
            self._exception_paths.append(path)

        self._analysis_log.append(
            f"analyze_exception_flow: {len(paths)} try-blocks found at {_now_iso()}"
        )

        # Compute pairwise divergence between normal and each handler coordinate
        divergence: dict[str, float] = {}
        for path in paths:
            for exc_name, handler_coord in path.handler_coordinates:
                score = self.compute_path_divergence(path.normal_coordinate, handler_coord)
                divergence[f"{path.path_id}:{exc_name}"] = score
                # Bump the trust level for bare-except paths down further
                if path.has_bare_except:
                    divergence[f"{path.path_id}:{exc_name}"] = min(
                        1.0, score + _BARE_EXCEPT_PENALTY
                    )

        return {
            "try_block_count": len(try_blocks),
            "handler_count": len(handler_map),
            "bare_except_count": len(bare_excepts),
            "reraise_count": len(reraise_patterns),
            "finally_count": sum(1 for p in paths if p.has_finally),
            "exception_paths": [p.path_id for p in paths],
            "divergence_scores": divergence,
            "site_id": self._site_id,
        }

    def build_exception_paths(self, try_node: ast.Try) -> ExceptionPath:
        """Build an ``ExceptionPath`` coordinate fork from an ``ast.Try`` node.

        Creates a *normal coordinate* for the try-body and *handler
        coordinates* for each ``except`` clause.  All coordinates and the
        restriction morphisms connecting them are registered in the internal
        ``SiteBuilder``.

        The normal → handler morphism is a ``RESTRICTION`` morphism: the
        handler section is the restriction of the normal section to the
        exception sub-coordinate.

        Parameters
        ----------
        try_node:
            An ``ast.Try`` node from a previously-parsed source tree.

        Returns
        -------
        ExceptionPath
            Frozen record describing the coordinate fork.
        """
        path_id = f"{_EXCEPTION_COORDINATE_PREFIX}-{uuid.uuid4().hex[:8]}"
        try_site = f"line:{getattr(try_node, 'lineno', 0)}"

        # Normal path: the coordinate reached when the try-body succeeds
        normal_coord = Coordinate(
            coord_id=f"{path_id}-normal",
            label=f"try-normal@{try_site}",
            kind=CoordinateKind.STATEMENT,
            path_components=(self._site_id, path_id, "normal"),
        )
        self._site_builder.add_coordinate(normal_coord)

        # Handler paths: one coordinate per except-clause
        handler_coords: list[tuple[str, object]] = []
        for handler in getattr(try_node, "handlers", []):
            exc_type_name = _extract_handler_type_name(handler)
            handler_coord = Coordinate(
                coord_id=f"{path_id}-handler-{exc_type_name}",
                label=f"handler:{exc_type_name}@{try_site}",
                kind=CoordinateKind.STATEMENT,
                path_components=(self._site_id, path_id, "handler", exc_type_name),
            )
            self._site_builder.add_coordinate(handler_coord)

            # The restriction morphism encodes that the handler coordinate is
            # reachable only when the exception type matches this handler
            divergence_morph = Morphism(
                morphism_id=f"exc-fork-{path_id}-{exc_type_name}",
                source=normal_coord,
                target=handler_coord,
                kind=MorphismKind.RESTRICTION,
            )
            self._site_builder.add_morphism(divergence_morph)
            handler_coords.append((exc_type_name, handler_coord))

        # Detect finally and bare-except
        has_finally = bool(getattr(try_node, "finalbody", None))
        finally_obligation_id = ""
        if has_finally:
            # The finally block is a temporal obligation: all paths must pass
            # through it.  We encode this as a ResidualObligation ID that the
            # downstream judgment algebra can resolve.
            finally_obligation_id = f"{_FINALLY_OBLIGATION_PREFIX}-{path_id}"

        has_bare = any(h.type is None for h in getattr(try_node, "handlers", []))

        return ExceptionPath(
            path_id=path_id,
            try_site=try_site,
            normal_coordinate=normal_coord,
            handler_coordinates=tuple(handler_coords),
            finally_obligation_id=finally_obligation_id,
            has_bare_except=has_bare,
            has_finally=has_finally,
            trust=_DEFAULT_TRUST_LEVEL,
            created_at=_now_iso(),
        )

    def map_handler_coordinates(self, handlers: list) -> dict[str, object]:
        """Map exception handler AST nodes to their ``Coordinate`` objects.

        Iterates over *handlers*, extracts the exception type name from each,
        and constructs a ``Coordinate`` for each handler.  Bare-except clauses
        (``handler.type is None``) are mapped under the key ``"BaseException"``.

        Parameters
        ----------
        handlers:
            List of ``ast.ExceptHandler`` nodes.

        Returns
        -------
        dict[str, Coordinate]
            Mapping from ``exc_type_name`` to ``Coordinate``.
        """
        result: dict[str, object] = {}
        for handler in handlers:
            exc_type_name = _extract_handler_type_name(handler)
            coord = Coordinate(
                coord_id=f"{_HANDLER_COORDINATE_PREFIX}-{exc_type_name}-{uuid.uuid4().hex[:6]}",
                label=f"handler:{exc_type_name}",
                kind=CoordinateKind.STATEMENT,
                path_components=(self._site_id, "handler", exc_type_name),
            )
            self._site_builder.add_coordinate(coord)
            result[exc_type_name] = coord
        return result

    def compute_path_divergence(self, normal_path: object, exception_path: object) -> float:
        """Compute a divergence score in ``[0.0, 1.0]`` between the normal and exception paths.

        The divergence is computed as the normalised Hamming distance between
        the ``path_components`` tuples of the two coordinates.  A score of
        ``1.0`` means maximal divergence (completely different path components);
        ``0.0`` means the paths converge (same components, e.g. a finally block
        where both paths re-enter the same coordinate).

        A tail penalty is added for paths that differ in length, ensuring that
        a short path and a long path diverge even when their common prefix matches.

        The result is cached in ``_path_divergence_scores`` for later retrieval
        by ``get_alternate_path_report``.

        Parameters
        ----------
        normal_path:
            The normal-flow ``Coordinate`` (or ``None``).
        exception_path:
            The handler ``Coordinate`` (or ``None``).

        Returns
        -------
        float
            Normalised divergence score in ``[0.0, 1.0]``.
        """
        if normal_path is None or exception_path is None:
            return 1.0
        n_comps = getattr(normal_path, "path_components", ())
        e_comps = getattr(exception_path, "path_components", ())
        if not n_comps and not e_comps:
            return 0.0
        max_len = max(len(n_comps), len(e_comps), 1)
        mismatches = sum(1 for a, b in zip(n_comps, e_comps) if a != b)
        tail_penalty = abs(len(n_comps) - len(e_comps))
        raw = (mismatches + tail_penalty) / max_len
        score = min(1.0, raw)
        cache_key = (
            f"{getattr(normal_path,'coord_id','')}:{getattr(exception_path,'coord_id','')}"
        )
        self._path_divergence_scores[cache_key] = score
        return score

    def get_alternate_path_report(self) -> dict[str, Any]:
        """Build a full report of all exception alternate paths discovered so far.

        Aggregates statistics from all ``ExceptionPath`` objects accumulated
        since the coordinator was created and returns a structured dict
        suitable for logging, display, or forwarding to the judgment system.

        Returns
        -------
        dict
            Report dict with keys:

            * ``site_id``
            * ``path_count``
            * ``bare_except_count``
            * ``finally_obligation_count``
            * ``handler_coordinate_count``
            * ``divergence_summary`` — sub-dict with mean / max / min / count
            * ``analysis_log_tail`` — last 5 log entries
            * ``paths`` — list of per-path summary dicts
            * ``generated_at`` — ISO-8601 timestamp
        """
        bare_count = sum(1 for p in self._exception_paths if p.has_bare_except)
        finally_count = sum(1 for p in self._exception_paths if p.has_finally)
        handler_count = sum(len(p.handler_coordinates) for p in self._exception_paths)

        divergence_values = list(self._path_divergence_scores.values())
        avg_divergence = (
            sum(divergence_values) / len(divergence_values)
            if divergence_values else 0.0
        )

        path_summaries = []
        for p in self._exception_paths:
            path_summaries.append({
                "path_id": p.path_id,
                "try_site": p.try_site,
                "handler_count": len(p.handler_coordinates),
                "has_bare_except": p.has_bare_except,
                "has_finally": p.has_finally,
                "finally_obligation_id": p.finally_obligation_id,
                "trust": str(p.trust),
                "path_count": p.path_count(),
                "is_covered": p.is_covered(),
            })

        return {
            "site_id": self._site_id,
            "path_count": len(self._exception_paths),
            "bare_except_count": bare_count,
            "finally_obligation_count": finally_count,
            "handler_coordinate_count": handler_count,
            "divergence_summary": {
                "mean": avg_divergence,
                "max": max(divergence_values, default=0.0),
                "min": min(divergence_values, default=0.0),
                "count": len(divergence_values),
            },
            "analysis_log_tail": self._analysis_log[-5:],
            "paths": path_summaries,
            "generated_at": _now_iso(),
        }

    def build_exception_coordinate(self, exc_type: type, site: str) -> object:
        """Build a ``Coordinate`` for a specific exception type at the given site.

        Uses ``inspect.getmro`` to traverse the exception hierarchy and produces
        ``path_components`` that reflect the full MRO chain — from the most
        specific type down to ``BaseException`` and ``object``.  This means
        that two exception coordinates for the same type at different sites will
        share a common prefix but differ in their last component, which is
        exactly what the Hamming-distance divergence metric expects.

        Parameters
        ----------
        exc_type:
            The exception type to build a coordinate for.
        site:
            Source location string, e.g. ``"mymodule.py:42"``.

        Returns
        -------
        Coordinate
            Newly constructed (and NOT yet registered in the site builder) coordinate.
        """
        try:
            mro = inspect.getmro(exc_type)
            mro_names = tuple(c.__name__ for c in mro)
        except TypeError:
            mro_names = (getattr(exc_type, "__name__", str(exc_type)),)

        coord_id = (
            f"{_EXCEPTION_COORDINATE_PREFIX}-{exc_type.__name__}-{_stable_hash(site)[:8]}"
        )
        return Coordinate(
            coord_id=coord_id,
            label=f"{exc_type.__name__}@{site}",
            kind=CoordinateKind.STATEMENT,
            path_components=(self._site_id,) + mro_names + (site,),
        )

    def classify_exception_kind(self, exc: BaseException) -> ExceptionKindRecord:
        """Classify a live exception into an ``ExceptionKindRecord``.

        Inspects the exception type's MRO via ``inspect.getmro`` and sets
        the boolean flags for well-known sub-hierarchies.  The result is
        cached in ``_EXCEPTION_HIERARCHY_CACHE`` so repeated classifications
        of the same type are O(1) after the first call.

        Parameters
        ----------
        exc:
            A live (caught) exception instance.

        Returns
        -------
        ExceptionKindRecord
            Classification record with ``RUNTIME_WITNESSED`` trust.
        """
        exc_type = type(exc)

        # Check cache first — MRO traversal is O(n) in hierarchy depth
        cached = _EXCEPTION_HIERARCHY_CACHE.get(exc_type.__name__)
        if cached is not None:
            return cached

        try:
            mro = inspect.getmro(exc_type)
            mro_names = tuple(c.__name__ for c in mro)
        except TypeError:
            mro_names = (exc_type.__name__,)

        name_set = set(mro_names)
        record = ExceptionKindRecord(
            kind_id=f"kind-{exc_type.__name__}-{uuid.uuid4().hex[:6]}",
            exc_type_name=exc_type.__name__,
            mro_names=mro_names,
            is_warning="Warning" in name_set,
            is_system_exit="SystemExit" in name_set,
            is_keyboard_interrupt="KeyboardInterrupt" in name_set,
            is_runtime_error="RuntimeError" in name_set,
            is_value_error="ValueError" in name_set,
            is_lookup_error="LookupError" in name_set,
            trust=TrustLevel.RUNTIME_WITNESSED,
            classified_at=_now_iso(),
        )
        _EXCEPTION_HIERARCHY_CACHE[exc_type.__name__] = record
        return record

    def build_site(self) -> object:
        """Finalise and return the ``Site`` built from all accumulated coordinates and morphisms.

        Calls ``SiteBuilder.build()`` and caches the result.  Subsequent calls
        return the cached site without rebuilding.

        Returns
        -------
        Site
        """
        if self._built_site is None:
            self._built_site = self._site_builder.build()
            self._analysis_log.append(f"build_site: site built at {_now_iso()}")
        return self._built_site

    def witness(self) -> "ExceptionsAlternateSemanticPathsWitness":
        """Return the runtime witness instance for this coordinator."""
        return self._witness


# ---


class ExceptionsAlternateSemanticPathsAnalyzer:
    r"""Static AST-based analyser for exception handling patterns in Python source.

    theory2.tex Ch18 §18.S01 — the analyser operates purely at the syntactic
    level: it parses Python source into an AST and walks it to extract
    structural information about try/except/finally blocks.  No code is
    executed, so all results enter at ``ORACLE_PROPOSED`` trust.

    The analyser's primary outputs are:

    * ``find_try_blocks`` — all ``ast.Try`` nodes in the source
    * ``analyze_handler_chain`` — structured info about each except clause
    * ``detect_bare_except`` — list of bare-except handlers
    * ``find_reraise_patterns`` — list of bare-raise re-raise sites
    * ``analyze_exception_hierarchy`` — static class hierarchy from ClassDef nodes
    * ``build_handler_map`` — exc_type_name → [lineno, …]
    * ``classify_handler_pattern`` — named pattern for an individual handler

    Caching
    -------
    The analyser caches parse trees keyed by ``hash(source)`` to avoid
    repeated parsing when the same source is analysed multiple times.
    """

    def __init__(self) -> None:
        """Initialise with an empty parse cache and results list."""
        # Parse cache: hash(source) -> ast.Module
        self._parse_cache: dict[int, ast.Module] = {}
        # Accumulated structured analysis results from all calls
        self._analysis_results: list[dict] = []

    def find_try_blocks(self, source: str) -> list[ast.Try]:
        """Parse *source* and return all ``ast.Try`` nodes found via ``ast.walk``.

        Walks the entire AST depth-first and collects all ``ast.Try`` nodes,
        including nested ones.  Each ``ast.Try`` node may have handlers,
        an else clause, and a finally clause.

        Parameters
        ----------
        source:
            Python source code string.

        Returns
        -------
        list[ast.Try]
            All try-block nodes, in walk order.
        """
        tree = self._cached_parse(source)
        blocks = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        self._analysis_results.append({
            "operation": "find_try_blocks",
            "count": len(blocks),
            "source_hash": hash(source),
        })
        return blocks

    def analyze_handler_chain(self, handlers: list[ast.ExceptHandler]) -> list[dict]:
        """Analyse a list of ``ast.ExceptHandler`` nodes and return structured info.

        For each handler, this method extracts:

        * ``exc_type_name`` — the exception type name (``"BaseException"`` for bare)
        * ``handler_name`` — the ``as``-clause variable name (empty if absent)
        * ``lineno`` — source line number
        * ``is_bare`` — True for bare ``except:``
        * ``is_tuple_type`` — True for ``except (A, B):``
        * ``pattern`` — classified pattern name from ``classify_handler_pattern``

        Parameters
        ----------
        handlers:
            List of ``ast.ExceptHandler`` nodes, typically from a single
            ``ast.Try.handlers`` list.

        Returns
        -------
        list[dict]
            One dict per handler.
        """
        results = []
        for h in handlers:
            exc_type_name = _extract_handler_type_name(h)
            pattern = self.classify_handler_pattern(h)
            results.append({
                "exc_type_name": exc_type_name,
                "handler_name": h.name or "",
                "lineno": getattr(h, "lineno", 0),
                "is_bare": h.type is None,
                "is_tuple_type": isinstance(h.type, ast.Tuple),
                "pattern": pattern,
            })
        return results

    def detect_bare_except(self, tree: ast.Module) -> list[ast.ExceptHandler]:
        """Find all bare ``except:`` clauses in *tree*.

        A bare except catches everything, including ``BaseException``,
        ``KeyboardInterrupt``, and ``SystemExit``.  In the path topology
        this widens the exception coordinate to the full ``BaseException``
        lattice, which is modelled as a ``_BARE_EXCEPT_PENALTY`` addition to
        the divergence score.

        Parameters
        ----------
        tree:
            An already-parsed ``ast.Module``.

        Returns
        -------
        list[ast.ExceptHandler]
            All bare-except handler nodes.
        """
        bare = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        bare.append(handler)
        return bare

    def find_reraise_patterns(self, tree: ast.Module) -> list[dict]:
        """Find all re-raise patterns (bare ``raise`` statements inside except blocks).

        A bare ``raise`` re-raises the current exception.  In the sheaf model
        this is a *pass-through restriction morphism*: the section is restricted
        to the parent coordinate and re-emitted without modification.

        Parameters
        ----------
        tree:
            An already-parsed ``ast.Module``.

        Returns
        -------
        list[dict]
            One dict per re-raise site, with keys ``lineno``,
            ``handler_type``, and ``pattern`` (always ``_RERAISE_PATTERN_KEY``).
        """
        reraises = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    for child in ast.walk(handler):
                        if isinstance(child, ast.Raise) and child.exc is None:
                            reraises.append({
                                "lineno": getattr(child, "lineno", 0),
                                "handler_type": _extract_handler_type_name(handler),
                                "pattern": _RERAISE_PATTERN_KEY,
                            })
        return reraises

    def analyze_exception_hierarchy(self, tree: ast.Module) -> dict[str, list[str]]:
        """Attempt static analysis of exception class hierarchy from ClassDef nodes.

        Walks the AST for ``ClassDef`` nodes whose bases include names
        containing ``"Error"``, ``"Exception"``, or ``"Warning"``, and builds
        a partial inheritance map.  This is necessarily incomplete (it cannot
        follow imports), but it captures locally-defined exception hierarchies.

        Parameters
        ----------
        tree:
            An already-parsed ``ast.Module``.

        Returns
        -------
        dict[str, list[str]]
            ``{class_name: [base_name, ...]}`` for exception-adjacent class
            definitions.
        """
        hierarchy: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases: list[str] = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        bases.append(f"{_ast_name(base.value)}.{base.attr}")
                # Only record classes that look like exception subclasses
                if any(
                    "Error" in b or "Exception" in b or "Warning" in b
                    for b in bases
                ):
                    hierarchy[node.name] = bases
        return hierarchy

    def build_handler_map(self, tree: ast.Module) -> dict[str, list[int]]:
        """Build a map from exception type name to list of line numbers where it is handled.

        Walks all ``ast.Try`` nodes in the tree and accumulates the line
        numbers for each handled exception type.  Useful for quickly
        identifying which exception types receive the most attention in a
        codebase.

        Parameters
        ----------
        tree:
            An already-parsed ``ast.Module``.

        Returns
        -------
        dict[str, list[int]]
            ``{exc_type_name: [lineno, ...]}``.
        """
        handler_map: dict[str, list[int]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    name = _extract_handler_type_name(handler)
                    handler_map.setdefault(name, []).append(
                        getattr(handler, "lineno", 0)
                    )
        return handler_map

    def classify_handler_pattern(self, handler_node: ast.ExceptHandler) -> str:
        """Classify an except handler into a named pattern.

        The classification is based on the structure of the handler body:

        * ``bare_except`` — no exception type specified
        * ``reraise`` — contains a bare ``raise``
        * ``chained`` — contains ``raise X from Y``
        * ``convert_exception`` — contains ``raise SomeOtherException(...)``
        * ``suppress_and_log`` — contains a logging/print call (no re-raise)
        * ``specific_handler`` — everything else

        Parameters
        ----------
        handler_node:
            An ``ast.ExceptHandler`` node to classify.

        Returns
        -------
        str
            One of the pattern key strings listed above.
        """
        if handler_node.type is None:
            return _BARE_EXCEPT_PATTERN_KEY

        body = handler_node.body
        # Wrap body statements in a temporary Module so ast.walk works uniformly
        body_module = ast.Module(body=body, type_ignores=[])

        # Scan for raise statements — order matters: check chained before bare raise
        for node in ast.walk(body_module):
            if isinstance(node, ast.Raise):
                if node.exc is None:
                    # Bare raise — re-raise current exception
                    return _RERAISE_PATTERN_KEY
                if node.cause is not None:
                    # raise X from Y — exception chaining
                    return _CHAINED_EXCEPTION_PATTERN_KEY

        # If we get here, check for raise with a new exception (convert)
        for node in ast.walk(body_module):
            if isinstance(node, ast.Raise) and node.exc is not None:
                return "convert_exception"

        # Check for logging or print calls => suppress_and_log
        for node in ast.walk(body_module):
            if isinstance(node, ast.Call):
                func_name = _ast_call_name(node)
                if any(x in func_name for x in ("log", "warn", "error", "print", "debug", "info")):
                    return "suppress_and_log"

        return "specific_handler"

    def _cached_parse(self, source: str) -> ast.Module:
        """Parse *source* and cache the result by ``hash(source)``.

        On a cache miss, parses with ``ast.parse``.  If parsing fails with
        ``SyntaxError``, caches an empty module so subsequent calls do not
        re-attempt the failing parse.

        Parameters
        ----------
        source:
            Python source code string.

        Returns
        -------
        ast.Module
            Parsed (or empty fallback) module.
        """
        key = hash(source)
        if key not in self._parse_cache:
            try:
                self._parse_cache[key] = ast.parse(source)
            except SyntaxError:
                _log.debug("_cached_parse: SyntaxError, caching empty module")
                self._parse_cache[key] = ast.parse("")
        return self._parse_cache[key]


# ---


class ExceptionsAlternateSemanticPathsWitness:
    r"""Runtime witness that observes exception paths as they execute.

    theory2.tex Ch18 §18.S01 — the witness pattern advances trust from
    ``ORACLE_PROPOSED`` (copilot-proposed paths) to ``RUNTIME_WITNESSED``
    once a live exception actually traverses a coordinate.

    The witness records four event types that together span the lifecycle
    of an exception:

    1. ``witness_exception_raised`` — the exception is first raised
    2. ``witness_handler_entered`` — an except clause is entered
    3. ``witness_exception_suppressed`` — the handler returns without re-raising
    4. ``witness_exception_propagated`` — the exception escapes the handler

    ``generate_path_evidence`` converts all accumulated records into an
    ``EvidenceBundle`` that the judgment algebra can consume.

    Counters
    --------
    The witness maintains four integer counters (``_raised_count``,
    ``_handled_count``, ``_suppressed_count``, ``_propagated_count``) for
    quick summary statistics without iterating over all records.
    """

    def __init__(self) -> None:
        """Initialise with empty record list and zeroed counters."""
        self._records: list[ExceptionWitnessRecord] = []
        self._suppressed_count: int = 0
        self._propagated_count: int = 0
        self._raised_count: int = 0
        self._handled_count: int = 0

    def witness_exception_raised(self, exc: BaseException, site: str) -> ExceptionWitnessRecord:
        """Record that *exc* was raised at *site*.

        Extracts the exception type name and truncated message from the live
        exception object and creates a ``RUNTIME_WITNESSED`` record.

        Parameters
        ----------
        exc:
            The live exception that was raised.
        site:
            Human-readable source location, e.g. ``"module.py:42"``.

        Returns
        -------
        ExceptionWitnessRecord
            The newly created witness record.
        """
        self._raised_count += 1
        record = ExceptionWitnessRecord(
            record_id=f"witness-raised-{uuid.uuid4().hex[:8]}",
            event_type="raised",
            exc_type_name=type(exc).__name__,
            exc_message=str(exc)[:400],
            site=site,
            handler_type_name="",
            is_suppressed=False,
            chained_from_id=_get_chained_id(exc),
            trust=TrustLevel.RUNTIME_WITNESSED,
            witnessed_at=_now_iso(),
        )
        self._records.append(record)
        _log.debug("witness_exception_raised: %s at %s", type(exc).__name__, site)
        return record

    def witness_handler_entered(
        self, handler_type: type, exc: BaseException
    ) -> ExceptionWitnessRecord:
        """Record that the handler for *handler_type* has been entered to handle *exc*.

        The site is inferred from the current call-stack frame via
        ``sys._getframe(1)``.  If frame introspection is unavailable, the
        site is set to ``"unknown"``.

        Parameters
        ----------
        handler_type:
            The exception type matched by the handler (the type in the
            ``except`` clause).
        exc:
            The live exception that was caught.

        Returns
        -------
        ExceptionWitnessRecord
        """
        self._handled_count += 1
        frame = sys._getframe(1) if hasattr(sys, "_getframe") else None
        if frame is not None:
            fname = getattr(getattr(frame, "f_code", None), "co_filename", "unknown")
            lineno = getattr(frame, "f_lineno", 0)
            site = f"{fname}:{lineno}"
        else:
            site = "unknown"
        record = ExceptionWitnessRecord(
            record_id=f"witness-handled-{uuid.uuid4().hex[:8]}",
            event_type="handled",
            exc_type_name=type(exc).__name__,
            exc_message=str(exc)[:400],
            site=site,
            handler_type_name=getattr(handler_type, "__name__", str(handler_type)),
            is_suppressed=False,
            chained_from_id=_get_chained_id(exc),
            trust=TrustLevel.RUNTIME_WITNESSED,
            witnessed_at=_now_iso(),
        )
        self._records.append(record)
        return record

    def witness_exception_suppressed(self, exc: BaseException) -> ExceptionWitnessRecord:
        """Record that *exc* was suppressed by the handler.

        Suppression occurs when the handler returns normally without re-raising
        the exception.  This corresponds to a *section collapse* in the sheaf
        model: the exception section is annihilated at the handler coordinate.

        Parameters
        ----------
        exc:
            The suppressed exception.

        Returns
        -------
        ExceptionWitnessRecord
        """
        self._suppressed_count += 1
        record = ExceptionWitnessRecord(
            record_id=f"witness-suppressed-{uuid.uuid4().hex[:8]}",
            event_type="suppressed",
            exc_type_name=type(exc).__name__,
            exc_message=str(exc)[:400],
            site="suppressed",
            handler_type_name="",
            is_suppressed=True,
            chained_from_id=_get_chained_id(exc),
            trust=TrustLevel.RUNTIME_WITNESSED,
            witnessed_at=_now_iso(),
        )
        self._records.append(record)
        return record

    def witness_exception_propagated(self, exc: BaseException) -> ExceptionWitnessRecord:
        """Record that *exc* propagated out of a handler (was re-raised or not caught).

        Propagation corresponds to a section that passes through a coordinate
        unchanged and continues up the call-stack graph.  A compact traceback
        summary is included for post-hoc debugging.

        Parameters
        ----------
        exc:
            The propagating exception.

        Returns
        -------
        ExceptionWitnessRecord
        """
        self._propagated_count += 1
        tb_summary = _format_traceback_summary(exc)
        record = ExceptionWitnessRecord(
            record_id=f"witness-propagated-{uuid.uuid4().hex[:8]}",
            event_type="propagated",
            exc_type_name=type(exc).__name__,
            exc_message=(str(exc)[:400] + " | tb:" + tb_summary[:200]),
            site="propagated",
            handler_type_name="",
            is_suppressed=False,
            chained_from_id=_get_chained_id(exc),
            trust=TrustLevel.RUNTIME_WITNESSED,
            witnessed_at=_now_iso(),
        )
        self._records.append(record)
        return record

    def generate_path_evidence(self) -> object:
        """Convert witness records into an ``EvidenceBundle`` for the judgment system.

        Each ``ExceptionWitnessRecord`` becomes an ``EvidenceItem`` with
        ``WITNESS`` kind and ``RUNTIME_WITNESSED`` trust.  The payload is the
        JSON-serialised record (via ``to_evidence_payload``).

        Returns
        -------
        EvidenceBundle
            Bundle of evidence items, one per witness record.
        """
        items = []
        for rec in self._records:
            item = EvidenceItem(
                item_id=rec.record_id,
                kind=EvidenceItemKind.WITNESS,
                payload=rec.to_evidence_payload(),
                trust=TrustLevel.RUNTIME_WITNESSED,
                channel=_ANALYSIS_CHANNEL,
            )
            items.append(item)
        return EvidenceBundle(items=tuple(items))

    def get_exception_summary(self) -> dict[str, Any]:
        """Return a summary dict of all witnessed exception events.

        Aggregates per-type counts and overall lifecycle counters into a
        single dict suitable for logging or reporting.

        Returns
        -------
        dict
            Keys: ``total_events``, ``raised_count``, ``handled_count``,
            ``suppressed_count``, ``propagated_count``,
            ``by_exception_type``, ``channel``.
        """
        by_type: dict[str, int] = {}
        for rec in self._records:
            by_type[rec.exc_type_name] = by_type.get(rec.exc_type_name, 0) + 1
        return {
            "total_events": len(self._records),
            "raised_count": self._raised_count,
            "handled_count": self._handled_count,
            "suppressed_count": self._suppressed_count,
            "propagated_count": self._propagated_count,
            "by_exception_type": by_type,
            "channel": _ANALYSIS_CHANNEL,
        }

    def clear(self) -> None:
        """Reset all records and counters.

        Useful when re-using a witness instance across multiple test runs or
        analysis passes.  Clears all accumulated evidence.
        """
        self._records.clear()
        self._suppressed_count = 0
        self._propagated_count = 0
        self._raised_count = 0
        self._handled_count = 0


# ---
# Module-level helper functions
# ---


def _extract_handler_type_name(handler: ast.ExceptHandler) -> str:
    """Extract the exception type name string from an ``ast.ExceptHandler`` node.

    Handles bare except (returns ``"BaseException"``), simple names
    (``except ValueError:``), attribute access (``except pkg.Error:``),
    and tuple types (``except (A, B):`` returns ``"A|B"``).

    Parameters
    ----------
    handler:
        An ``ast.ExceptHandler`` node.

    Returns
    -------
    str
        Canonical exception type name string.
    """
    if handler.type is None:
        return "BaseException"
    if isinstance(handler.type, ast.Name):
        return handler.type.id
    if isinstance(handler.type, ast.Attribute):
        return f"{_ast_name(handler.type.value)}.{handler.type.attr}"
    if isinstance(handler.type, ast.Tuple):
        names = []
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Attribute):
                names.append(f"{_ast_name(elt.value)}.{elt.attr}")
            else:
                names.append("<expr>")
        return "|".join(names)
    return "Unknown"


def _ast_name(node: object) -> str:
    """Recursively extract a dotted name from an AST ``Name``/``Attribute`` node.

    Parameters
    ----------
    node:
        An AST node, typically a ``Name`` or ``Attribute``.

    Returns
    -------
    str
        Dotted name string, e.g. ``"pkg.sub.Cls"``, or ``"<expr>"`` for
        unsupported node types.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_name(node.value)}.{node.attr}"
    return "<expr>"


def _ast_call_name(node: ast.Call) -> str:
    """Extract the function name from an ``ast.Call`` node.

    Delegates to ``_ast_name`` applied to ``node.func``.

    Parameters
    ----------
    node:
        An ``ast.Call`` node.

    Returns
    -------
    str
        Function name or dotted attribute chain.
    """
    return _ast_name(node.func)


def _get_chained_id(exc: BaseException) -> str:
    """Return a stable ID for the chained cause/context of *exc*, or empty string.

    Checks ``__cause__`` first (explicit ``raise X from Y``), then
    ``__context__`` (implicit chaining during exception handling).  Returns
    an empty string if there is no chained exception.

    Parameters
    ----------
    exc:
        A live (caught) exception instance.

    Returns
    -------
    str
        ``"{TypeName}:{truncated_message}"`` or ``""``.
    """
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is None:
        return ""
    return f"{type(cause).__name__}:{str(cause)[:40]}"


def _format_traceback_summary(exc: BaseException) -> str:
    """Format a compact traceback summary string for *exc*.

    Uses ``traceback.format_exception`` and truncates to a safe maximum
    length.  Returns a plain string representation if formatting fails.

    Parameters
    ----------
    exc:
        A live (caught) exception instance.

    Returns
    -------
    str
        Multi-line traceback string, truncated to at most 2000 characters.
    """
    try:
        lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        # Take the last _MAX_TRACEBACK_FRAMES lines to avoid enormous tracebacks
        summary = "".join(lines[-_MAX_TRACEBACK_FRAMES:])
        return summary[:2000]
    except Exception:
        return f"{type(exc).__name__}: {exc}"


def _exception_mro_depth(exc_type: type) -> int:
    """Return the depth of *exc_type* in the ``BaseException`` hierarchy.

    The depth is defined as the length of the MRO chain returned by
    ``inspect.getmro``.  For ``BaseException`` itself this is 2
    (``[BaseException, object]``); for deeply nested application exceptions
    it may be 6 or more.

    Parameters
    ----------
    exc_type:
        An exception class (subclass of ``BaseException``).

    Returns
    -------
    int
        MRO length, or 1 if ``inspect.getmro`` raises ``TypeError``.
    """
    try:
        return len(inspect.getmro(exc_type))
    except TypeError:
        return 1


def _build_exception_section_from_live(exc: BaseException, site: str) -> ExceptionSection:
    """Convert a live ``BaseException`` into an ``ExceptionSection`` for the judgment site.

    Constructs the section with a ``Coordinate`` derived from *site*, a
    compact traceback summary, and — if the exception has a ``__cause__`` or
    ``__context__`` — a nested ``ExceptionSection`` for the chained exception.

    Parameters
    ----------
    exc:
        A live (caught) exception instance.
    site:
        Source location string used as the coordinate label.

    Returns
    -------
    ExceptionSection
        Fully populated exception section at ``RUNTIME_WITNESSED`` trust.
    """
    tb_summary = _format_traceback_summary(exc)
    chained = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    chained_section = None
    if chained is not None:
        # Build a minimal nested section for the chained exception; we do not
        # recurse further to avoid unbounded chain following
        chained_section = ExceptionSection(
            section_id=f"exc-section-chained-{uuid.uuid4().hex[:6]}",
            exc_type_name=type(chained).__name__,
            exc_message=str(chained)[:200],
            coordinate=None,
            trust=TrustLevel.RUNTIME_WITNESSED,
            chained_from=None,
            traceback_summary="",
            is_suppressed=False,
            raised_at=_now_iso(),
        )
    return ExceptionSection(
        section_id=f"exc-section-{uuid.uuid4().hex[:8]}",
        exc_type_name=type(exc).__name__,
        exc_message=str(exc)[:400],
        coordinate=Coordinate(
            coord_id=f"exc-coord-{_stable_hash(site)[:8]}",
            label=site,
            kind=CoordinateKind.STATEMENT,
            path_components=(site,),
        ),
        trust=TrustLevel.RUNTIME_WITNESSED,
        chained_from=chained_section,
        traceback_summary=tb_summary[:1000],
        is_suppressed=False,
        raised_at=_now_iso(),
    )


def _count_exception_paths_in_source(source: str) -> dict[str, int]:
    """Count exception paths in *source* without constructing full path objects.

    A lightweight alternative to the full coordinator for quick source
    scanning.  Returns a dict of counts: ``try``, ``except``, ``finally``,
    ``bare_except``, ``reraise``.

    Parameters
    ----------
    source:
        Python source code string.

    Returns
    -------
    dict[str, int]
        Path-count summary.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"try": 0, "except": 0, "finally": 0, "bare_except": 0, "reraise": 0}

    try_count = 0
    except_count = 0
    finally_count = 0
    bare_except_count = 0
    reraise_count = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            try_count += 1
            for handler in node.handlers:
                except_count += 1
                if handler.type is None:
                    bare_except_count += 1
                for child in ast.walk(handler):
                    if isinstance(child, ast.Raise) and child.exc is None:
                        reraise_count += 1
            if getattr(node, "finalbody", None):
                finally_count += 1

    return {
        "try": try_count,
        "except": except_count,
        "finally": finally_count,
        "bare_except": bare_except_count,
        "reraise": reraise_count,
    }


# ---
# Smoke test
# ---


def _smoke_test() -> None:
    """Quick sanity check for exceptions_as_alternate_semantic_p.

    Exercises: source analysis, coordinate building, exception classification,
    witness recording, and report generation.
    """
    import textwrap

    print("=== exceptions_as_alternate_semantic_p smoke test ===")

    sample_source = textwrap.dedent("""
        def divide(a, b):
            try:
                result = a / b
            except ZeroDivisionError as e:
                print("zero division:", e)
                return None
            except (TypeError, ValueError):
                raise
            finally:
                pass
            return result

        try:
            x = int("not-a-number")
        except ValueError:
            pass
        except Exception as e:
            raise RuntimeError("unexpected") from e
    """)

    coordinator = ExceptionsAlternateSemanticPathsCoordinator(site_id="smoke-test-site")

    # Test analyze_exception_flow
    report = coordinator.analyze_exception_flow(sample_source)
    assert report["try_block_count"] >= 2, (
        f"Expected >=2 try blocks, got {report['try_block_count']}"
    )
    assert report["handler_count"] >= 3, (
        f"Expected >=3 handlers, got {report['handler_count']}"
    )
    print(
        f"  analyze_exception_flow: {report['try_block_count']} try-blocks, "
        f"{report['handler_count']} handlers"
    )

    # Test build_exception_coordinate
    coord = coordinator.build_exception_coordinate(ValueError, "smoke-test:10")
    assert "ValueError" in str(coord.label)
    print(f"  build_exception_coordinate(ValueError): {coord.label}")

    # Test classify_exception_kind
    try:
        raise ZeroDivisionError("smoke test zero")
    except ZeroDivisionError as exc:
        kind_rec = coordinator.classify_exception_kind(exc)
        assert kind_rec.exc_type_name == "ZeroDivisionError"
        assert not kind_rec.is_warning
        print(
            f"  classify_exception_kind: {kind_rec.exc_type_name}, "
            f"mro_depth={len(kind_rec.mro_names)}"
        )

    # Test witness
    witness = ExceptionsAlternateSemanticPathsWitness()
    try:
        raise RuntimeError("smoke-test-witness")
    except RuntimeError as exc:
        wr = witness.witness_exception_raised(exc, "smoke:main")
        wr2 = witness.witness_handler_entered(RuntimeError, exc)
        wr3 = witness.witness_exception_suppressed(exc)

    summary = witness.get_exception_summary()
    assert summary["raised_count"] == 1
    assert summary["suppressed_count"] == 1
    print(
        f"  witness summary: {summary['total_events']} events, "
        f"suppressed={summary['suppressed_count']}"
    )

    # Test get_alternate_path_report
    path_report = coordinator.get_alternate_path_report()
    assert "site_id" in path_report
    assert path_report["path_count"] >= 2
    print(
        f"  alternate_path_report: {path_report['path_count']} paths, "
        f"{path_report['bare_except_count']} bare excepts"
    )

    # Test analyzer
    analyzer = ExceptionsAlternateSemanticPathsAnalyzer()
    try_blocks = analyzer.find_try_blocks(sample_source)
    assert len(try_blocks) >= 2
    bare = analyzer.detect_bare_except(ast.parse(sample_source))
    reraises = analyzer.find_reraise_patterns(ast.parse(sample_source))
    hierarchy = analyzer.analyze_exception_hierarchy(ast.parse(sample_source))
    print(
        f"  analyzer: {len(try_blocks)} try-blocks, {len(bare)} bare, "
        f"{len(reraises)} reraises"
    )

    # Test evidence generation
    evidence_bundle = witness.generate_path_evidence()
    assert hasattr(evidence_bundle, "items")
    print(f"  evidence_bundle: {len(evidence_bundle.items)} items")

    # Test _build_exception_section_from_live
    try:
        raise KeyError("smoke-key")
    except KeyError as exc:
        section = _build_exception_section_from_live(exc, "smoke:section-test")
        assert section.exc_type_name == "KeyError"
        print(
            f"  _build_exception_section_from_live: {section.exc_type_name}, "
            f"id={section.section_id[:16]}"
        )

    # Test ExceptionPath.get_handler_for
    path_obj = coordinator._exception_paths[0] if coordinator._exception_paths else None
    if path_obj is not None:
        h = path_obj.get_handler_for(ZeroDivisionError)
        print(f"  ExceptionPath.get_handler_for(ZeroDivisionError): {h}")

    # Test _count_exception_paths_in_source
    counts = _count_exception_paths_in_source(sample_source)
    assert counts["try"] >= 2
    print(f"  _count_exception_paths_in_source: {counts}")

    # Test ExceptionKindRecord helper methods
    try:
        raise DeprecationWarning("smoke-warning")
    except DeprecationWarning as exc:
        wrec = coordinator.classify_exception_kind(exc)
        assert wrec.is_warning
        assert wrec.is_recoverable()
        print(
            f"  DeprecationWarning: is_warning={wrec.is_warning}, "
            f"coord_label={wrec.coordinate_label()}"
        )

    print("=== smoke test PASSED ===")


if __name__ == "__main__":
    _smoke_test()
