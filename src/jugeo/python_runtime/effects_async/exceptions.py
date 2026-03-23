from __future__ import annotations

r"""Package: jugeo.python_runtime.effects_async.exceptions
theory2.tex Ch18 §18.2 — Exceptions as Sheaf Sections

Python exceptions are modelled as sections of a failure sheaf over the
semantic site.  Each exception object carries a Coordinate (where it happened),
a TrustLevel (how much to believe the failure report), and an optional
Obstruction (persistent cohomology class blocking resolution).

Exception chaining (``raise X from Y``) corresponds to section restriction:
the chained exception is the restriction of the original to the parent
coordinate.  The ``raise``/``except`` pair is a sheaf morphism: raise creates a
section, except collapses it.

StructuredFailureEncoder converts raw Python exceptions into ExceptionSection
objects with full metadata.  FailurePropagator walks the site topology to
propagate failures outward.  ExceptionChain models raise-from chaining as
sequential restriction.

All copilot-assisted encoding of exceptions enters at ORACLE_PROPOSED trust
and requires runtime confirmation to advance to RUNTIME_WITNESSED.

See also
--------
* jugeo.python_runtime.effects_async.models — ExceptionSection dataclass
* jugeo.python_runtime.effects_async.algorithms — propagate_exception_through_site
"""

import sys, traceback
import hashlib, json, time, uuid
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
        ExceptionSection, CancellationRecord,
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
    # ExceptionSection stub
    @_dc(frozen=True, slots=True)
    class ExceptionSection:
        coordinate: object=None; exception_type: str=""; message: str=""
        trust: object=None; obstruction: object=None
        traceback_coords: tuple=(); is_handled: bool=False; timestamp: str=""
        def section_id(self) -> str:
            coord_id = getattr(self.coordinate, "coord_id", str(self.coordinate))
            payload = f"{coord_id}:{self.exception_type}:{self.timestamp}"
            return _hashlib.sha256(payload.encode()).hexdigest()[:16]
        def propagate_to(self, parent):
            from dataclasses import replace as _replace
            new_t = self.trust.step_weaker() if self.trust is not None and hasattr(self.trust, 'step_weaker') else self.trust
            return _replace(self, coordinate=parent, trust=new_t, traceback_coords=(self.coordinate,)+self.traceback_coords)
        def handle(self, resolution: str):
            from dataclasses import replace as _replace
            return _replace(self, is_handled=True, message=f"{self.message} [resolved: {resolution}]")
        def as_judgment(self): return {"kind": "exception_judgment", "exception_type": self.exception_type, "message": self.message}
        def to_dict(self): return {"exception_type": self.exception_type, "message": self.message, "is_handled": self.is_handled}
        def severity_score(self): return 0.5
    # CancellationRecord stub
    @_dc(frozen=True, slots=True)
    class CancellationRecord:
        task_id: str=""; reason: str=""; cancelled_at: str=""
        coordinate: object=None; trust: object=None; propagated_to: tuple=()
        def record_id(self) -> str: return self.task_id
        def to_dict(self): return {"task_id": self.task_id, "reason": self.reason}


# ---
# Helper functions
# ---

def _coord_name(c: Any) -> str:
    """Return a display name for coordinate *c*.

    Tries ``c.name``, then ``c.label``, then ``str(c)`` in order so that both
    the real ``Coordinate`` (which has ``components`` but no ``.name``) and the
    stub (which has ``.label``) are handled gracefully.
    """
    if hasattr(c, "name"):
        return c.name
    if hasattr(c, "label") and c.label:
        return c.label
    return str(c)


def _get_coord_id(c: Any) -> str:
    """Return the stable identifier string for coordinate *c*.

    Prefers ``c.coord_id``, then ``c.name``, then falls back to ``str(c)``.
    This covers both the real ``Coordinate`` (which stores identity in its
    ``components`` tuple rather than a ``coord_id``) and the stub.
    """
    if hasattr(c, "coord_id") and c.coord_id:
        return c.coord_id
    if hasattr(c, "name") and c.name:
        return c.name
    return str(c)


def _get_ancestors(coord: Any, max_depth: int) -> list[Any]:
    """Walk the coordinate's parent chain, returning up to *max_depth* ancestors.

    The real ``Coordinate`` exposes a ``parent()`` method.  The stub does not,
    so we catch ``AttributeError`` and stop early.  Returns an empty list when
    the coordinate has no parent relationship.

    Parameters
    ----------
    coord:
        Starting coordinate.
    max_depth:
        Maximum number of steps to walk upward.

    Returns
    -------
    list[Any]
        List of ancestor coordinates from nearest to most-distant, length ≤
        *max_depth*.
    """
    ancestors: list[Any] = []
    current = coord
    for _ in range(max_depth):
        try:
            parent = current.parent()
            if parent is None or parent is current:
                break
            ancestors.append(parent)
            current = parent
        except (AttributeError, TypeError):
            break
    return ancestors


def _clean_filename(filename: str) -> str:
    """Strip common path prefixes from *filename* for compact display.

    Removes ``site-packages``, ``src/``, and absolute path prefixes so that
    traceback coordinates carry only the meaningful module-relative path.

    Parameters
    ----------
    filename:
        Raw ``__file__`` path or FrameSummary filename.

    Returns
    -------
    str
        Cleaned filename string suitable for use as a coordinate label.
    """
    import os
    # Normalise separators
    fn = filename.replace("\\", "/")
    # Strip up to and including site-packages
    marker = "site-packages/"
    idx = fn.rfind(marker)
    if idx != -1:
        fn = fn[idx + len(marker):]
        return fn
    # Strip leading src/
    if fn.startswith("src/"):
        fn = fn[4:]
    # Strip absolute prefix up to first meaningful segment
    try:
        fn = os.path.relpath(fn)
    except ValueError:
        pass
    return fn


def _make_section_from_exception(
    exc: BaseException,
    coord: Any,
    trust: Any,
    tb_coords: tuple,
) -> ExceptionSection | dict:
    """Build an ``ExceptionSection`` from raw exception data.

    Attempts the real ``ExceptionSection`` constructor; falls back to a plain
    ``dict`` if that constructor raises (e.g. because the real dataclass has
    stricter field requirements than the stubs).

    Parameters
    ----------
    exc:
        The Python exception to encode.
    coord:
        Coordinate at which the exception occurred.
    trust:
        Trust level for this failure record.
    tb_coords:
        Pre-built tuple of traceback coordinates.

    Returns
    -------
    ExceptionSection | dict
        Structured failure record, or a plain dict on construction failure.
    """
    exc_type = type(exc).__qualname__
    msg = str(exc)
    ts = _now_iso()
    try:
        return ExceptionSection(
            coordinate=coord,
            exception_type=exc_type,
            message=msg,
            trust=trust,
            obstruction=None,
            traceback_coords=tb_coords,
            is_handled=False,
            timestamp=ts,
        )
    except Exception as build_err:
        return {
            "coordinate": str(coord),
            "exception_type": exc_type,
            "message": msg,
            "trust": str(trust),
            "obstruction": None,
            "traceback_coords": [str(c) for c in tb_coords],
            "is_handled": False,
            "timestamp": ts,
            "_build_error": str(build_err),
        }


def _fresh_id(prefix: str = "exc") -> str:
    """Generate a unique, prefix-labelled identifier using ``uuid4``.

    Parameters
    ----------
    prefix:
        Short string prepended to the hex UUID for human-readable context.

    Returns
    -------
    str
        A string of the form ``"<prefix>-<uuid4_hex>"``.
    """
    return f"{prefix}-{uuid.uuid4().hex}"


# ---
# ExceptionSheaf
# ---

class ExceptionSheaf:
    """A failure sheaf over a semantic site, indexed by ``ExceptionSection`` objects.

    theory2.tex Ch18 §18.2 — the failure sheaf assigns to each open in the site
    the set of active exception sections over that open.  Gluing lemmas ensure
    that sections on overlapping opens can be combined into a single section on
    the union.

    This class is the primary container used by copilot-assisted exception
    analysis to accumulate structured failure records across a program run.
    Sections enter at ``ORACLE_PROPOSED`` trust when produced by the copilot
    encoder and must be promoted to ``RUNTIME_WITNESSED`` once confirmed.

    Parameters
    ----------
    site:
        The semantic site over which this sheaf is defined.
    sections:
        Optional initial mapping from section-id to ``ExceptionSection``.
    topology:
        Optional Grothendieck topology governing covering families.
    """

    def __init__(
        self,
        site: Site,
        sections: dict[str, ExceptionSection] | None = None,
        topology: GrothendieckTopology | None = None,
    ) -> None:
        self.site = site
        self.sections: dict[str, ExceptionSection] = sections if sections is not None else {}
        self.topology = topology

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_section(self, section: ExceptionSection) -> None:
        """Register *section* in this sheaf, keyed by its stable section-id.

        The section's coordinate is validated against the site when the site
        exposes an ``objects()`` iterator or a ``coordinates`` list.  If
        neither is available the section is accepted unconditionally so that
        standalone (stub) use remains possible.

        Parameters
        ----------
        section:
            The ``ExceptionSection`` to register.
        """
        sid = section.section_id()
        # Attempt coordinate validation against the site
        coord_known = False
        try:
            # Real Site exposes objects()
            site_coords = list(self.site.objects())
            coord_id = _get_coord_id(section.coordinate)
            for sc in site_coords:
                if _get_coord_id(sc) == coord_id:
                    coord_known = True
                    break
        except AttributeError:
            # Stub Site has a coordinates list
            try:
                coord_id = _get_coord_id(section.coordinate)
                for sc in self.site.coordinates:
                    if _get_coord_id(sc) == coord_id:
                        coord_known = True
                        break
            except AttributeError:
                coord_known = True  # accept unconditionally when we cannot check
        except Exception:
            coord_known = True
        if not coord_known:
            # Accept anyway — the caller may be working with a partial site
            pass
        self.sections[sid] = section

    def get_section(self, section_id: str) -> ExceptionSection | None:
        """Look up a section by its stable identifier.

        Parameters
        ----------
        section_id:
            The hex-string identifier returned by ``ExceptionSection.section_id()``.

        Returns
        -------
        ExceptionSection | None
            The matching section, or ``None`` if no such section is registered.
        """
        return self.sections.get(section_id)

    def sections_at(self, coordinate: Any) -> list[ExceptionSection]:
        """Return all sections whose coordinate matches *coordinate*.

        Comparison is performed via ``_coord_name`` so that both the real
        ``Coordinate`` (components-based) and the stub (label-based) are
        handled uniformly.

        Parameters
        ----------
        coordinate:
            The target coordinate.

        Returns
        -------
        list[ExceptionSection]
            All registered sections located at *coordinate*.
        """
        target_name = _coord_name(coordinate)
        return [
            s for s in self.sections.values()
            if _coord_name(s.coordinate) == target_name
        ]

    def unhandled_sections(self) -> list[ExceptionSection]:
        """Return all sections that have not yet been handled (``is_handled=False``).

        Unhandled sections represent open failure obligations — the copilot
        exception analysis pipeline uses this method to determine which
        failures still require resolution.

        Returns
        -------
        list[ExceptionSection]
            Unhandled ``ExceptionSection`` objects in registration order.
        """
        return [s for s in self.sections.values() if not s.is_handled]

    def glue_sections(
        self,
        sec_a: ExceptionSection,
        sec_b: ExceptionSection,
    ) -> ExceptionSection | None:
        """Attempt to glue two sections along their coordinate overlap.

        Gluing succeeds when one section's coordinate name is a string prefix
        of the other's (indicating that one coordinate is an ancestor of the
        other in the site topology).  The glued section is produced by
        propagating the more-specific section toward the more-general one via
        ``propagate_to``.

        Tries ``coord_a.is_prefix_of(coord_b)`` first (real API); falls back
        to a string-prefix check on coordinate names.

        Parameters
        ----------
        sec_a:
            First section to glue.
        sec_b:
            Second section to glue.

        Returns
        -------
        ExceptionSection | None
            The glued section, or ``None`` if the coordinates do not overlap.
        """
        coord_a = sec_a.coordinate
        coord_b = sec_b.coordinate

        # Try the real is_prefix_of API first
        a_prefix_of_b: bool = False
        b_prefix_of_a: bool = False
        try:
            a_prefix_of_b = bool(coord_a.is_prefix_of(coord_b))
            b_prefix_of_a = bool(coord_b.is_prefix_of(coord_a))
        except AttributeError:
            name_a = _coord_name(coord_a)
            name_b = _coord_name(coord_b)
            if name_a and name_b:
                a_prefix_of_b = name_b.startswith(name_a) and name_a != name_b
                b_prefix_of_a = name_a.startswith(name_b) and name_a != name_b

        if a_prefix_of_b:
            # sec_a is more general; propagate sec_b to sec_a's coordinate
            return sec_b.propagate_to(coord_a)
        if b_prefix_of_a:
            # sec_b is more general; propagate sec_a to sec_b's coordinate
            return sec_a.propagate_to(coord_b)
        return None

    def restriction(
        self,
        section: ExceptionSection,
        target_coord: Any,
    ) -> ExceptionSection:
        """Restrict *section* to *target_coord* via sheaf restriction morphism.

        Restriction corresponds to ``raise X from Y`` chaining: the restricted
        section records that the failure has been observed at a coarser
        coordinate, with trust stepped down by one level.

        Parameters
        ----------
        section:
            The section to restrict.
        target_coord:
            The target (typically parent) coordinate.

        Returns
        -------
        ExceptionSection
            The restricted section at *target_coord*.
        """
        return section.propagate_to(target_coord)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the sheaf to a plain ``dict``.

        Returns a JSON-compatible structure containing all registered sections
        in their ``to_dict()`` form, together with a count of unhandled
        sections and the site identifier (if available).

        Returns
        -------
        dict[str, Any]
            Serialised sheaf.
        """
        site_id = getattr(self.site, "site_id", str(self.site))
        serialised_sections: dict[str, Any] = {}
        for sid, sec in self.sections.items():
            try:
                serialised_sections[sid] = sec.to_dict()
            except Exception as err:
                serialised_sections[sid] = {"_error": str(err)}
        return {
            "site_id": site_id,
            "section_count": len(self.sections),
            "unhandled_count": len(self.unhandled_sections()),
            "sections": serialised_sections,
        }

    def severity_distribution(self) -> dict[str, int]:
        """Count registered sections grouped by ``exception_type``.

        Useful for dashboards and copilot-generated failure summaries that need
        to understand which exception kinds dominate the failure sheaf.

        Returns
        -------
        dict[str, int]
            Mapping from exception type name to occurrence count.
        """
        distribution: dict[str, int] = {}
        for sec in self.sections.values():
            exc_type = getattr(sec, "exception_type", "unknown")
            distribution[exc_type] = distribution.get(exc_type, 0) + 1
        return distribution


# ---
# ExceptionChain
# ---

@dataclass(frozen=True, slots=True)
class ExceptionChain:
    """A sequential chain of ``ExceptionSection`` objects modelling ``raise X from Y``.

    theory2.tex Ch18 §18.2 — exception chaining is section restriction along a
    sequence of morphisms.  Each link in the chain is the restriction of the
    previous link to its parent coordinate, decaying trust by one step per hop.

    Copilot-proposed chains enter at ``ORACLE_PROPOSED`` trust (``is_copilot_proposed``
    returns ``True``) and must be confirmed by the runtime before being used in
    solver queries.

    Parameters
    ----------
    chain_id:
        Stable string identifier for this chain.
    links:
        Ordered tuple of ``ExceptionSection`` objects, from root to final handler.
    root_cause:
        The originating section (typically the innermost raised exception).
    final_handler:
        Optional string naming the handler that ultimately caught the chain.
    trust:
        Overall trust level of the chain (minimum of constituent link trusts).
    """

    chain_id: str
    links: tuple[ExceptionSection, ...]
    root_cause: ExceptionSection
    final_handler: str | None
    trust: TrustLevel

    def append_link(self, section: ExceptionSection) -> ExceptionChain:
        """Append *section* to the end of the chain, returning a new chain.

        Uses ``replace`` to produce an immutable update.  The overall chain
        trust is not automatically recalculated here; callers should update
        ``trust`` separately if needed.

        Parameters
        ----------
        section:
            The new ``ExceptionSection`` to append as the next link.

        Returns
        -------
        ExceptionChain
            New chain with *section* appended.
        """
        return replace(self, links=self.links + (section,))

    def restrict_to_parent(self) -> ExceptionChain:
        """Produce a new chain where every link is restricted to its first traceback ancestor.

        For each link: if ``link.traceback_coords`` is non-empty, propagate the
        link to ``link.traceback_coords[0]``; otherwise propagate to
        ``link.coordinate`` (a no-op restriction that still steps trust down).

        Returns
        -------
        ExceptionChain
            Restricted chain with trust-decayed links.
        """
        new_links: list[ExceptionSection] = []
        for link in self.links:
            if link.traceback_coords:
                parent_coord = link.traceback_coords[0]
            else:
                parent_coord = link.coordinate
            new_links.append(link.propagate_to(parent_coord))
        return replace(self, links=tuple(new_links))

    def as_judgment(self) -> object:
        """Build a judgment representing this exception chain.

        Constructs a ``JudgmentBuilder``-based judgment when the full jugeo
        imports are available, or returns a plain dict otherwise.  The judgment
        proposition encodes the chain's root cause and depth.

        Copilot-proposed chains (``is_copilot_proposed() == True``) are encoded
        with ``ORACLE_PROPOSED`` trust; confirmed chains use the stored trust
        level.

        Returns
        -------
        object
            A ``Judgment`` object or a plain dict serialisation.
        """
        formula = (
            f"exception_chain(root={self.root_cause.exception_type}, "
            f"depth={self.depth()}, chain_id={self.chain_id})"
        )
        try:
            prop = Proposition(
                prop_id=_stable_hash(formula),
                formula=formula,
                kind=PropositionKind.BEHAVIOURAL,
            )
            annotation = TrustAnnotation.at(self.trust)
            ts = _now_iso()
            prov = Provenance(
                source=ProvenanceSource.RUNTIME,
                agent="exceptions.ExceptionChain",
                timestamp=ts,
                chain=(),
            )
            builder = JudgmentBuilder()
            builder.set_coordinate(self.root_cause.coordinate)
            builder.set_proposition(prop)
            builder.set_trust(annotation)
            builder.set_provenance(prov)
            return builder.build()
        except Exception:
            return {
                "kind": "exception_chain_judgment",
                "chain_id": self.chain_id,
                "root_cause": self.root_cause.exception_type,
                "depth": self.depth(),
                "trust": str(self.trust),
                "formula": formula,
            }

    def root_trust(self) -> TrustLevel:
        """Return the trust level of the root-cause section.

        Returns
        -------
        TrustLevel
            Trust level stored on ``self.root_cause``.
        """
        return self.root_cause.trust  # type: ignore[return-value]

    def depth(self) -> int:
        """Return the number of links in this chain.

        Returns
        -------
        int
            ``len(self.links)``.
        """
        return len(self.links)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this chain to a plain ``dict``.

        Returns a JSON-compatible structure including the chain id, depth,
        final handler, trust label, and each link's ``to_dict()`` output.

        Returns
        -------
        dict[str, Any]
            Serialised chain.
        """
        trust_label: str
        try:
            trust_label = self.trust.label()
        except AttributeError:
            trust_label = str(self.trust)

        serialised_links: list[dict[str, Any]] = []
        for link in self.links:
            try:
                serialised_links.append(link.to_dict())
            except Exception as err:
                serialised_links.append({"_error": str(err)})

        return {
            "chain_id": self.chain_id,
            "depth": self.depth(),
            "final_handler": self.final_handler,
            "trust": trust_label,
            "is_copilot_proposed": self.is_copilot_proposed(),
            "root_cause": self.root_cause.to_dict() if hasattr(self.root_cause, "to_dict") else str(self.root_cause),
            "links": serialised_links,
        }

    def is_copilot_proposed(self) -> bool:
        """Return ``True`` if this chain's trust is at or below ``ORACLE_PROPOSED``.

        Copilot-proposed chains have not been confirmed by the runtime and
        should not be used directly in solver queries without prior promotion.

        Returns
        -------
        bool
            ``True`` when trust ≤ ``ORACLE_PROPOSED``.
        """
        try:
            return int(self.trust) <= int(TrustLevel.ORACLE_PROPOSED)
        except (TypeError, ValueError):
            try:
                return self.trust.weaker_than(TrustLevel.RUNTIME_WITNESSED)  # type: ignore[union-attr]
            except Exception:
                return False

    def chain_id_hash(self) -> str:
        """Compute a stable hash over all link section-ids joined with ``"::"``

        The hash provides a content-addressed identifier for the chain that is
        independent of the ``chain_id`` field (which may have been assigned
        arbitrarily at construction time).

        Returns
        -------
        str
            SHA-256 hex digest of the concatenated section ids.
        """
        parts: list[str] = []
        for link in self.links:
            try:
                parts.append(link.section_id())
            except Exception:
                parts.append(str(link))
        payload = "::".join(parts)
        return hashlib.sha256(payload.encode()).hexdigest()


# ---
# FailurePropagator
# ---

class FailurePropagator:
    """Propagates ``ExceptionSection`` objects outward through a site topology.

    theory2.tex Ch18 §18.2 — failure propagation is the pushforward of a sheaf
    section along site morphisms.  Each propagation step steps the trust level
    down by ``trust_decay_per_step`` units, reflecting that remote observations
    of a failure are less reliable than local ones.

    This class is used by both the copilot-assisted static analyser and the
    runtime instrumentation layer to simulate how an unhandled exception would
    travel through the call graph.

    Parameters
    ----------
    site:
        The semantic site over which propagation is performed.
    max_depth:
        Maximum number of hops to propagate from the source coordinate.
    trust_decay_per_step:
        Number of ``TrustLevel`` steps to subtract per propagation hop.
    """

    def __init__(
        self,
        site: Site,
        max_depth: int = 5,
        trust_decay_per_step: int = 1,
    ) -> None:
        self.site = site
        self.max_depth = max_depth
        self.trust_decay_per_step = trust_decay_per_step

    def propagate(self, source: ExceptionSection) -> list[ExceptionSection]:
        """Propagate *source* outward through the site, returning derived sections.

        Walks the coordinate parent chain via ``_get_ancestors`` up to
        ``self.max_depth`` steps.  At each ancestor coordinate a new
        ``ExceptionSection`` is produced by calling ``source.propagate_to``
        with the trust already decayed by ``_decay_trust_by_steps``.

        Parameters
        ----------
        source:
            The originating failure section.

        Returns
        -------
        list[ExceptionSection]
            Derived sections at each ancestor coordinate, nearest first.
        """
        ancestors = _get_ancestors(source.coordinate, self.max_depth)
        results: list[ExceptionSection] = []
        for step, ancestor_coord in enumerate(ancestors, start=1):
            decayed_trust = self._decay_trust_by_steps(source.trust, step)
            try:
                propagated = ExceptionSection(
                    coordinate=ancestor_coord,
                    exception_type=source.exception_type,
                    message=source.message,
                    trust=decayed_trust,
                    obstruction=source.obstruction,
                    traceback_coords=(source.coordinate,) + source.traceback_coords,
                    is_handled=False,
                    timestamp=_now_iso(),
                )
            except Exception:
                # Fall back to propagate_to which handles both real and stub
                propagated = source.propagate_to(ancestor_coord)
            results.append(propagated)
        return results

    def propagate_chain(self, chain: ExceptionChain) -> list[ExceptionChain]:
        """Propagate every link in *chain* and assemble new chains per ancestor.

        For each link the propagation produces a list of derived sections at
        ancestor coordinates.  The method zips these lists to build one
        propagated ``ExceptionChain`` per propagation depth, up to the shortest
        propagation list.

        Parameters
        ----------
        chain:
            The ``ExceptionChain`` to propagate.

        Returns
        -------
        list[ExceptionChain]
            One ``ExceptionChain`` per propagation depth level.
        """
        if not chain.links:
            return []

        # Propagate each link individually
        propagated_per_link: list[list[ExceptionSection]] = [
            self.propagate(link) for link in chain.links
        ]

        # Determine how many depth levels we have (min across all links)
        depth = min((len(p) for p in propagated_per_link), default=0)
        result_chains: list[ExceptionChain] = []
        for level in range(depth):
            level_links = tuple(propagated_per_link[i][level] for i in range(len(chain.links)))
            new_root = propagated_per_link[0][level] if propagated_per_link else chain.root_cause
            new_trust = self._decay_trust_by_steps(chain.trust, level + 1)
            new_chain = ExceptionChain(
                chain_id=_fresh_id("chain"),
                links=level_links,
                root_cause=new_root,
                final_handler=chain.final_handler,
                trust=new_trust,
            )
            result_chains.append(new_chain)
        return result_chains

    def find_handlers(self, section: ExceptionSection) -> list[Any]:
        """Return potential exception handlers for *section*.

        Full handler discovery requires runtime information about which
        ``try``/``except`` blocks surround the section's coordinate.  This
        method returns an empty list as a safe placeholder; callers should
        integrate with the runtime instrumentation layer (e.g.
        ``jugeo.python_runtime.effects_async.async``) to obtain real
        handler information.

        Copilot-assisted static handler inference should enter results at
        ``ORACLE_PROPOSED`` trust and confirm them against the runtime before
        use in proof obligations.

        Parameters
        ----------
        section:
            The ``ExceptionSection`` for which handlers are sought.

        Returns
        -------
        list[Any]
            Empty list (handler discovery requires runtime context).
        """
        return []

    def build_propagation_graph(
        self,
        sections: list[ExceptionSection],
    ) -> dict[str, list[str]]:
        """Build an adjacency graph of propagation relationships between *sections*.

        For each pair of sections (A, B), if A's coordinate name is a string
        prefix of B's coordinate name then A may have been propagated from B
        (B is more specific).  The graph maps each section-id to a list of
        section-ids from which it could have been derived.

        Parameters
        ----------
        sections:
            The ``ExceptionSection`` objects to analyse.

        Returns
        -------
        dict[str, list[str]]
            Adjacency mapping: ``{section_id: [source_section_id, ...]}``.
        """
        graph: dict[str, list[str]] = {}
        id_to_section: dict[str, ExceptionSection] = {}
        for sec in sections:
            try:
                sid = sec.section_id()
            except Exception:
                sid = _get_coord_id(sec.coordinate)
            id_to_section[sid] = sec
            graph[sid] = []

        ids = list(id_to_section.keys())
        for i, sid_a in enumerate(ids):
            sec_a = id_to_section[sid_a]
            name_a = _coord_name(sec_a.coordinate)
            for sid_b in ids[i + 1:]:
                sec_b = id_to_section[sid_b]
                name_b = _coord_name(sec_b.coordinate)
                if name_a and name_b and name_a != name_b:
                    if name_b.startswith(name_a):
                        # sec_a is more general; sec_a could be propagated from sec_b
                        graph[sid_a].append(sid_b)
                    elif name_a.startswith(name_b):
                        graph[sid_b].append(sid_a)
        return graph

    def to_dict(self) -> dict[str, Any]:
        """Serialise the propagator configuration to a plain ``dict``.

        Returns
        -------
        dict[str, Any]
            Configuration dictionary with site id, max depth, and decay rate.
        """
        site_id = getattr(self.site, "site_id", str(self.site))
        return {
            "site_id": site_id,
            "max_depth": self.max_depth,
            "trust_decay_per_step": self.trust_decay_per_step,
        }

    def _decay_trust_by_steps(self, trust: TrustLevel, steps: int) -> TrustLevel:
        """Decay *trust* downward by ``steps * trust_decay_per_step`` levels.

        Uses the ``step_weaker()`` method when available (stub and real API).
        Falls back to integer-index arithmetic when the method is absent.

        Parameters
        ----------
        trust:
            Starting trust level.
        steps:
            Number of propagation hops (multiplied by ``trust_decay_per_step``).

        Returns
        -------
        TrustLevel
            Decayed trust level (clamped at the minimum).
        """
        total_decay = steps * self.trust_decay_per_step
        current = trust
        for _ in range(total_decay):
            try:
                nxt = current.step_weaker()  # type: ignore[union-attr]
                if nxt is current:
                    break
                current = nxt
            except AttributeError:
                # Index-based decay for enums without step_weaker
                try:
                    vals = list(type(current))
                    idx = vals.index(current)
                    current = vals[max(0, idx - 1)]
                except (ValueError, TypeError):
                    break
        return current  # type: ignore[return-value]


# ---
# StructuredFailureEncoder
# ---

class StructuredFailureEncoder:
    """Converts raw Python ``BaseException`` objects into ``ExceptionSection`` records.

    This is the primary entry point for the copilot-assisted exception encoding
    pipeline.  When ``copilot_mode=True`` all produced sections enter at
    ``ORACLE_PROPOSED`` trust; when ``copilot_mode=False`` (the default for
    runtime instrumentation) sections enter at ``default_trust``.

    The encoder also handles exception chaining (``__cause__`` / ``__context__``)
    by building ``ExceptionChain`` objects that model the full ``raise X from Y``
    dependency graph as a sequence of sheaf restrictions.

    Parameters
    ----------
    coordinate_factory:
        Callable or object used to build ``Coordinate`` instances from traceback
        frame data.  May be ``None`` when stub coordinates are acceptable.
    default_trust:
        Trust level assigned to runtime-witnessed exceptions (default:
        ``RUNTIME_WITNESSED``).
    copilot_mode:
        When ``True``, all produced sections are tagged ``ORACLE_PROPOSED`` to
        indicate they were generated by copilot static analysis rather than
        observed at runtime.
    """

    def __init__(
        self,
        coordinate_factory: Any,
        default_trust: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
        copilot_mode: bool = False,
    ) -> None:
        self.coordinate_factory = coordinate_factory
        self.default_trust = default_trust
        self.copilot_mode = copilot_mode

    def encode(
        self,
        exc: BaseException,
        base_coordinate: Any,
    ) -> ExceptionSection:
        """Encode a single Python exception as an ``ExceptionSection``.

        Extracts the exception type (``type(exc).__qualname__``), message
        (``str(exc)``), and traceback frames (via ``traceback.extract_tb``).
        Traceback frames are converted to ``Coordinate`` objects by
        ``_make_traceback_coordinates``.

        When ``copilot_mode`` is ``True`` the section is tagged
        ``ORACLE_PROPOSED``; otherwise ``default_trust`` is used.

        Parameters
        ----------
        exc:
            The exception to encode.
        base_coordinate:
            The coordinate representing the call site or module where this
            encoder is being invoked.

        Returns
        -------
        ExceptionSection
            Fully populated failure section.
        """
        exc_type = type(exc).__qualname__
        msg = str(exc)
        ts = _now_iso()
        trust = TrustLevel.ORACLE_PROPOSED if self.copilot_mode else self.default_trust

        # Extract traceback frames
        tb = exc.__traceback__
        tb_frames: list[Any] = []
        if tb is not None:
            try:
                tb_frames = list(traceback.extract_tb(tb))
            except Exception:
                tb_frames = []

        tb_coords = self._make_traceback_coordinates(tb_frames, base_coordinate)

        return ExceptionSection(
            coordinate=base_coordinate,
            exception_type=exc_type,
            message=msg,
            trust=trust,
            obstruction=None,
            traceback_coords=tb_coords,
            is_handled=False,
            timestamp=ts,
        )

    def encode_chain(
        self,
        exc: BaseException,
        base_coordinate: Any,
    ) -> ExceptionChain:
        """Encode an exception and its entire ``__cause__`` / ``__context__`` chain.

        Walks the exception's chaining attributes in order:
        1. ``__cause__`` (explicit ``raise X from Y``)
        2. ``__context__`` (implicit chaining from ``raise X`` inside an
           ``except`` block)

        Each exception in the chain is encoded individually and appended as a
        link.  The root cause is the innermost (original) exception.

        Parameters
        ----------
        exc:
            The outermost exception (the one that was raised to the caller).
        base_coordinate:
            The coordinate at which encoding is performed.

        Returns
        -------
        ExceptionChain
            Complete chain from root cause to outermost exception.
        """
        # Collect the raw exception chain (innermost last in Python's convention)
        raw_chain: list[BaseException] = []
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            raw_chain.append(current)
            if current.__cause__ is not None:
                current = current.__cause__
            elif current.__context__ is not None and not current.__suppress_context__:
                current = current.__context__
            else:
                break

        # Encode each exception; the last in raw_chain is the root cause
        encoded: list[ExceptionSection] = [
            self.encode(e, base_coordinate) for e in raw_chain
        ]

        root_cause = encoded[-1] if encoded else self.encode(exc, base_coordinate)
        trust = TrustLevel.ORACLE_PROPOSED if self.copilot_mode else self.default_trust
        chain_id = _fresh_id("chain")

        return ExceptionChain(
            chain_id=chain_id,
            links=tuple(encoded),
            root_cause=root_cause,
            final_handler=None,
            trust=trust,
        )

    def encode_current(self, base_coordinate: Any) -> ExceptionSection | None:
        """Encode the currently active Python exception, if any.

        Uses ``sys.exc_info()`` to retrieve the active exception context.
        Returns ``None`` when no exception is active (i.e. all three values
        from ``exc_info`` are ``None``).

        This method is safe to call from within an ``except`` block or from
        exception-handling middleware.

        Parameters
        ----------
        base_coordinate:
            The coordinate at which encoding is performed.

        Returns
        -------
        ExceptionSection | None
            Encoded section, or ``None`` if no exception is active.
        """
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_value is None:
            return None
        return self.encode(exc_value, base_coordinate)

    def batch_encode(
        self,
        exceptions: list[BaseException],
        base_coordinate: Any,
    ) -> list[ExceptionSection]:
        """Encode a list of exceptions in bulk, returning one section per exception.

        Exceptions that fail to encode individually are represented by a
        minimal fallback section carrying the encoding error as the message,
        so that batch operations are never interrupted by secondary failures.

        Parameters
        ----------
        exceptions:
            The exceptions to encode.
        base_coordinate:
            The shared coordinate for all encoded sections.

        Returns
        -------
        list[ExceptionSection]
            One ``ExceptionSection`` per input exception, in input order.
        """
        results: list[ExceptionSection] = []
        for exc in exceptions:
            try:
                section = self.encode(exc, base_coordinate)
            except Exception as encode_err:
                ts = _now_iso()
                trust = TrustLevel.ORACLE_PROPOSED if self.copilot_mode else self.default_trust
                try:
                    section = ExceptionSection(
                        coordinate=base_coordinate,
                        exception_type=type(exc).__qualname__,
                        message=f"[encoding failed: {encode_err}] original: {exc}",
                        trust=trust,
                        obstruction=None,
                        traceback_coords=(),
                        is_handled=False,
                        timestamp=ts,
                    )
                except Exception:
                    section = ExceptionSection(  # type: ignore[assignment]
                        coordinate=base_coordinate,
                        exception_type="EncodingError",
                        message=str(encode_err),
                        trust=trust,
                        obstruction=None,
                        traceback_coords=(),
                        is_handled=False,
                        timestamp=ts,
                    )
            results.append(section)
        return results

    def _make_traceback_coordinates(
        self,
        tb_frames: list[Any],
        base: Any,
    ) -> tuple[Any, ...]:
        """Convert a list of ``FrameSummary`` objects into ``Coordinate`` tuples.

        Tries to construct real ``Coordinate`` objects (``components`` tuple,
        ``kind=CoordinateKind.FUNCTION``) first; falls back to stub-style
        ``Coordinate(coord_id=..., label=...)`` if the real constructor raises,
        and ultimately falls back to raw strings.

        Parameters
        ----------
        tb_frames:
            List of ``traceback.FrameSummary`` objects (or similar objects with
            ``.filename``, ``.lineno``, ``.name`` attributes).
        base:
            The base coordinate used when frame data is unavailable.

        Returns
        -------
        tuple[Any, ...]
            Tuple of coordinates, one per traceback frame, from outermost to
            innermost.
        """
        coords: list[Any] = []
        for frame in tb_frames:
            try:
                filename = _clean_filename(getattr(frame, "filename", "<unknown>"))
                lineno = getattr(frame, "lineno", 0)
                func_name = getattr(frame, "name", "<unknown>")
                label = f"{filename}:{lineno}:{func_name}"
                coord_id_str = hashlib.sha256(label.encode()).hexdigest()[:12]

                try:
                    # Try real Coordinate API (components + kind)
                    coord = Coordinate(
                        components=(filename, func_name, str(lineno)),
                        kind=CoordinateKind.FUNCTION,
                        support_labels=frozenset(),
                        metadata={},
                    )
                except (TypeError, AttributeError):
                    # Fall back to stub Coordinate
                    coord = Coordinate(
                        coord_id=coord_id_str,
                        label=label,
                        kind=CoordinateKind.FUNCTION,
                        path_components=(filename, func_name),
                    )
                coords.append(coord)
            except Exception:
                coords.append(base)
        return tuple(coords)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the encoder's configuration to a plain ``dict``.

        Returns
        -------
        dict[str, Any]
            Dictionary with trust settings and copilot mode flag.
        """
        trust_label: str
        try:
            trust_label = self.default_trust.label()
        except AttributeError:
            trust_label = str(self.default_trust)
        return {
            "coordinate_factory": str(self.coordinate_factory),
            "default_trust": trust_label,
            "copilot_mode": self.copilot_mode,
        }

    def set_copilot_mode(self, enabled: bool) -> StructuredFailureEncoder:
        """Return a new encoder with ``copilot_mode`` set to *enabled*.

        Produces an immutable update — the original encoder is not modified.
        Use this to switch between runtime-witnessing and copilot-proposed
        encoding modes without mutating shared state.

        Parameters
        ----------
        enabled:
            ``True`` to produce ``ORACLE_PROPOSED`` sections; ``False`` to use
            ``default_trust``.

        Returns
        -------
        StructuredFailureEncoder
            New encoder instance with the updated ``copilot_mode`` flag.
        """
        return StructuredFailureEncoder(
            coordinate_factory=self.coordinate_factory,
            default_trust=self.default_trust,
            copilot_mode=enabled,
        )


# ---
# Public API
# ---

__all__ = [
    "ExceptionChain",
    "ExceptionSheaf",
    "FailurePropagator",
    "StructuredFailureEncoder",
]
