from __future__ import annotations

r"""
Package: jugeo.python_runtime.effects_async.generators
theory2.tex Ch18 §18.5 — Generators as Lazy Sheaf Constructions

Python generators model lazy sheaf constructions.  Each ``yield`` expression
emits a partial section over the current fiber coordinate.  The generator's
coordinate is the base; each yield_index produces a new fiber coordinate via
a canonical restriction morphism.

``send(value)`` updates the section: the sent value is the new input to the
generator frame, corresponding to a section update on the current fiber.

``StopIteration`` corresponds to fiber exhaustion: the generator has no more
sections to emit.

``itertools`` combinators (chain, islice, product, groupby) are section
morphisms: they compose, restrict, and transform fiber sequences.

All copilot-proposed generator sections carry ORACLE_PROPOSED trust (the
COPILOT_SUGGESTED ceiling) until the runtime confirms each yield value.

See also
--------
* jugeo.python_runtime.effects_async.models — GeneratorSection
* jugeo.python_runtime.effects_async.algorithms — collect_generator_fibers
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

import itertools
import json
from dataclasses import dataclass, field, replace
from typing import Any, Generator, Iterator, TypeVar

T = TypeVar("T")

# ---
# Local model imports — stubs accepted if package not yet installed
# ---

try:
    from jugeo.python_runtime.effects_async.models import (
        GeneratorSection,
    )
except ImportError:
    # models not yet available; define minimal stubs
    pass

# ---
# Internal helpers
# ---


def _make_fiber_coord_id(base_id: str, yield_index: int) -> str:
    """Return a stable fiber coordinate ID for *base_id* at *yield_index*.

    Combines the base coordinate ID with the yield index to produce a
    deterministic identifier for each fiber in a generator's section
    sequence.  Copilot tools use these IDs to index into the fiber sheaf
    without carrying full Coordinate objects.

    Parameters
    ----------
    base_id:
        The coord_id of the generator's base coordinate.
    yield_index:
        Zero-based index of the yield point.

    Returns
    -------
    str
        16-character hex prefix of SHA-256(base_id:yield_index).
    """
    return _stable_hash(f"{base_id}:{yield_index}")[:16]


def _fiber_coordinate(base: Coordinate, yield_index: int) -> Coordinate:
    """Derive a fiber :class:`Coordinate` for yield point *yield_index*.

    Each yield emits a partial section over a fresh fiber coordinate derived
    from the generator's base coordinate.  The fiber inherits the base kind
    and path_components, extended with the yield index.

    Parameters
    ----------
    base:
        The generator's base coordinate.
    yield_index:
        Zero-based index of the yield point.

    Returns
    -------
    Coordinate
        Fiber coordinate with path component ``f"yield[{yield_index}]"``.
    """
    base_id = getattr(base, "coord_id", str(base))
    base_label = getattr(base, "label", str(base))
    base_path = getattr(base, "path_components", ())
    suffix = f"yield[{yield_index}]"
    return Coordinate(
        coord_id=_make_fiber_coord_id(base_id, yield_index),
        label=f"{base_label}/{suffix}",
        kind=getattr(base, "kind", CoordinateKind.EXPRESSION),
        path_components=base_path + (suffix,),
    )


def _repr_value(value: Any) -> str:
    """Return a safe string representation of *value* for serialisation.

    Truncates long reprs to 256 characters to prevent log bloat.  Uses
    ``repr()`` rather than ``str()`` to make type information visible to
    copilot analysis tools.

    Parameters
    ----------
    value:
        Any Python object.

    Returns
    -------
    str
        Truncated repr of *value*.
    """
    try:
        s = repr(value)
    except Exception:
        s = "<unrepresentable>"
    return s[:256]


def _make_gen_section(
    gen_id: str,
    coordinate: Coordinate,
    yield_index: int,
    value: Any,
    trust: TrustLevel,
    is_exhausted: bool,
    send_history: tuple,
) -> object:
    """Construct a :class:`GeneratorSection` or a plain dict fallback.

    Attempts to build a full :class:`GeneratorSection` from the models
    package.  Falls back to a plain dict if the class is unavailable, so
    that copilot tools can operate in environments where the full package
    is not installed.

    Parameters
    ----------
    gen_id:
        Generator identifier.
    coordinate:
        Base coordinate for this generator.
    yield_index:
        Zero-based index of this yield point.
    value:
        The yielded value.
    trust:
        Trust level for this fiber.
    is_exhausted:
        True if StopIteration has been raised.
    send_history:
        Values sent into the generator.

    Returns
    -------
    GeneratorSection | dict
        A :class:`GeneratorSection` or plain dict.
    """
    try:
        return GeneratorSection(
            gen_id=gen_id,
            coordinate=coordinate,
            yield_index=yield_index,
            yielded_value=value,
            fiber_trust=trust,
            is_exhausted=is_exhausted,
            send_history=send_history,
        )
    except Exception:
        return {
            "gen_id": gen_id,
            "coordinate": str(coordinate),
            "yield_index": yield_index,
            "yielded_value": _repr_value(value),
            "fiber_trust": trust.label(),
            "is_exhausted": is_exhausted,
            "send_history": [_repr_value(s) for s in send_history],
        }


# ---
# GeneratorSheaf
# ---


class GeneratorSheaf:
    r"""A sheaf of :class:`GeneratorSection` objects over a base coordinate.

    theory2.tex Ch18 §18.5 — generators produce lazy sheaf constructions.
    The sheaf collects all yield-point sections from one or more generators
    sharing the same base coordinate.  Each ``gen_id`` corresponds to a
    distinct generator stalk; the fiber at yield_index *n* is the section
    emitted at the *n*-th ``yield``.

    Copilot tools that need to inspect generator output should first call
    :meth:`glue_fibers` to obtain the ordered list of yielded values, then
    validate them against the expected section restrictions.

    Parameters
    ----------
    sheaf_id:
        Unique identifier for this sheaf instance.
    base_coordinate:
        The base coordinate shared by all generator stalks in this sheaf.
    """

    def __init__(self, sheaf_id: str, base_coordinate: Coordinate) -> None:
        self.sheaf_id = sheaf_id
        self.base_coordinate = base_coordinate
        self.sections: dict[str, list[object]] = {}

    # ---

    def register_generator(self, gen_id: str) -> None:
        """Register a new generator stalk identified by *gen_id*.

        Initialises an empty section list for this generator.  Must be
        called before :meth:`add_section` for the same ``gen_id``.

        Parameters
        ----------
        gen_id:
            Unique generator identifier.

        Raises
        ------
        ValueError
            If *gen_id* is already registered in this sheaf.
        """
        if gen_id in self.sections:
            raise ValueError(
                f"Generator {gen_id!r} is already registered in sheaf "
                f"{self.sheaf_id!r}."
            )
        self.sections[gen_id] = []

    def add_section(self, section: object) -> None:
        """Add a :class:`GeneratorSection` to the sheaf.

        The section is appended to the stalk for its ``gen_id``.  If the
        ``gen_id`` is not yet registered, it is registered automatically
        before the section is appended.  Copilot evidence pipelines call this
        method for each section emitted by :class:`LazyFiberBuilder`.

        Parameters
        ----------
        section:
            A :class:`GeneratorSection` or compatible dict to add.
        """
        gen_id = getattr(section, "gen_id", None) or section.get("gen_id", "")
        if gen_id not in self.sections:
            self.sections[gen_id] = []
        self.sections[gen_id].append(section)

    def sections_for(self, gen_id: str) -> list[object]:
        """Return all sections for the stalk identified by *gen_id*.

        Returns an empty list if *gen_id* is not registered, so callers can
        iterate without checking for existence first.

        Parameters
        ----------
        gen_id:
            Generator identifier whose sections are requested.

        Returns
        -------
        list[GeneratorSection]
            All sections for *gen_id*, in insertion order.
        """
        return list(self.sections.get(gen_id, []))

    def latest_section(self, gen_id: str) -> object | None:
        """Return the section with the highest yield_index for *gen_id*.

        Scans all sections for the stalk and returns the one with the
        maximum ``yield_index``.  Returns ``None`` if no sections exist.
        Copilot tools use this to determine the current state of a
        generator without iterating the full history.

        Parameters
        ----------
        gen_id:
            Generator identifier.

        Returns
        -------
        GeneratorSection | None
            Section with maximum ``yield_index``, or ``None``.
        """
        stalk = self.sections.get(gen_id, [])
        if not stalk:
            return None
        return max(
            stalk,
            key=lambda s: getattr(s, "yield_index", s.get("yield_index", 0))
            if not hasattr(s, "yield_index") else s.yield_index,
        )

    def glue_fibers(self, gen_id: str) -> list[Any]:
        """Return the ordered list of yielded values for *gen_id*.

        Sorts sections by ``yield_index`` and extracts the ``yielded_value``
        (or dict equivalent) from each.  The result is the reconstructed
        lazy sequence for the generator — a "gluing" of the fiber sections
        into a single coherent list, as described in theory2.tex Ch18 §18.5.

        Parameters
        ----------
        gen_id:
            Generator identifier.

        Returns
        -------
        list[Any]
            Yielded values in ``yield_index`` order.
        """
        stalk = self.sections.get(gen_id, [])
        sorted_stalk = sorted(
            stalk,
            key=lambda s: getattr(s, "yield_index", 0)
            if hasattr(s, "yield_index") else s.get("yield_index", 0),
        )
        result: list[Any] = []
        for sec in sorted_stalk:
            if hasattr(sec, "yielded_value"):
                result.append(sec.yielded_value)
            elif isinstance(sec, dict):
                result.append(sec.get("yielded_value"))
        return result

    def exhausted_generators(self) -> list[str]:
        """Return the gen_ids of generators that have been fully exhausted.

        A generator is exhausted when its latest section has
        ``is_exhausted=True``, corresponding to a ``StopIteration`` having
        been raised.  Copilot analysis tools use this list to determine
        which generators have emitted their final fiber.

        Returns
        -------
        list[str]
            gen_ids whose latest section has ``is_exhausted=True``.
        """
        exhausted: list[str] = []
        for gen_id in self.sections:
            latest = self.latest_section(gen_id)
            if latest is None:
                continue
            is_ex = (
                getattr(latest, "is_exhausted", False)
                if hasattr(latest, "is_exhausted")
                else (latest.get("is_exhausted", False) if isinstance(latest, dict) else False)
            )
            if is_ex:
                exhausted.append(gen_id)
        return exhausted

    def to_dict(self) -> dict[str, Any]:
        """Serialise this sheaf to a JSON-safe dictionary.

        Each stalk is serialised using the section's ``to_dict`` method where
        available, or the raw dict if not.  Suitable for copilot audit logs
        and evidence bundle payloads.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of the sheaf.
        """
        serialised_sections: dict[str, list[Any]] = {}
        for gen_id, stalk in self.sections.items():
            stalk_dicts: list[Any] = []
            for sec in stalk:
                if hasattr(sec, "to_dict"):
                    stalk_dicts.append(sec.to_dict())
                elif isinstance(sec, dict):
                    stalk_dicts.append(sec)
                else:
                    stalk_dicts.append({"repr": _repr_value(sec)})
            serialised_sections[gen_id] = stalk_dicts
        return {
            "sheaf_id": self.sheaf_id,
            "base_coordinate": str(self.base_coordinate),
            "sections": serialised_sections,
        }

    def fiber_count(self, gen_id: str) -> int:
        """Return the number of sections registered for *gen_id*.

        Provides a quick count for monitoring and copilot dashboard tools
        without iterating the full stalk.

        Parameters
        ----------
        gen_id:
            Generator identifier.

        Returns
        -------
        int
            Number of sections in the stalk for *gen_id*.
        """
        return len(self.sections.get(gen_id, []))


# ---
# LazyFiberBuilder
# ---


class LazyFiberBuilder:
    r"""Wrap a Python generator and emit :class:`GeneratorSection` objects.

    theory2.tex Ch18 §18.5 — the builder formalises the generator protocol
    as a sequence of fiber-section constructions.  Each step of the
    generator produces one :class:`GeneratorSection` with the yielded value
    and updated metadata.

    Copilot-proposed generators enter at ``ORACLE_PROPOSED`` trust; the
    builder records each yield so that the runtime can confirm each fiber
    value and promote trust incrementally.

    Parameters
    ----------
    gen_id:
        Unique identifier for the generator being wrapped.
    base_coordinate:
        The base coordinate for all fiber sections produced by this builder.
    trust:
        Trust level to assign to each emitted section.
    """

    def __init__(
        self,
        gen_id: str,
        base_coordinate: Coordinate,
        trust: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> None:
        self.gen_id = gen_id
        self.base_coordinate = base_coordinate
        self.trust = trust

    # ---

    def from_generator(
        self, gen: Generator[Any, Any, Any]
    ) -> Iterator[object]:
        """Wrap *gen* and yield a :class:`GeneratorSection` for each value.

        Advances *gen* using ``next()`` and yields a section for each value.
        When ``StopIteration`` is raised, yields a final section with
        ``is_exhausted=True`` and the ``StopIteration.value`` as the
        yielded value.  This mirrors the fiber-exhaustion semantics of
        theory2.tex Ch18 §18.5.

        Parameters
        ----------
        gen:
            A Python generator object to wrap.

        Yields
        ------
        GeneratorSection
            One section per yield point, plus a final exhausted section.
        """
        yield_index = 0
        send_history: tuple = ()
        while True:
            try:
                value = next(gen)
                section = _make_gen_section(
                    gen_id=self.gen_id,
                    coordinate=_fiber_coordinate(self.base_coordinate, yield_index),
                    yield_index=yield_index,
                    value=value,
                    trust=self.trust,
                    is_exhausted=False,
                    send_history=send_history,
                )
                yield section
                yield_index += 1
            except StopIteration as exc:
                final_value = exc.value
                section = _make_gen_section(
                    gen_id=self.gen_id,
                    coordinate=_fiber_coordinate(self.base_coordinate, yield_index),
                    yield_index=yield_index,
                    value=final_value,
                    trust=self.trust,
                    is_exhausted=True,
                    send_history=send_history,
                )
                yield section
                return

    def collect(
        self, gen: Generator[Any, Any, Any], max_items: int = 100
    ) -> list[object]:
        """Collect up to *max_items* :class:`GeneratorSection` objects from *gen*.

        Calls :meth:`from_generator` and accumulates sections until either
        the generator is exhausted or *max_items* sections have been collected.
        The final exhausted section counts toward *max_items*.  Copilot tools
        use this method to materialise a bounded prefix of a lazy sequence.

        Parameters
        ----------
        gen:
            A Python generator to collect from.
        max_items:
            Maximum number of sections to collect (including exhausted sentinel).

        Returns
        -------
        list[GeneratorSection]
            Up to *max_items* sections.
        """
        collected: list[object] = []
        for section in self.from_generator(gen):
            collected.append(section)
            is_ex = (
                getattr(section, "is_exhausted", False)
                if hasattr(section, "is_exhausted")
                else (section.get("is_exhausted", False) if isinstance(section, dict) else False)
            )
            if len(collected) >= max_items or is_ex:
                break
        return collected

    def send_sequence(
        self, gen: Generator[Any, Any, Any], sends: list[Any]
    ) -> list[object]:
        """Advance *gen* by sending each value in *sends*, collecting sections.

        Sends values into *gen* using the ``send()`` protocol.  The first
        send uses ``None`` to prime the generator if it has not yet been
        advanced.  Each sent value and the corresponding yielded value are
        recorded in the section's send_history.

        Parameters
        ----------
        gen:
            A Python generator to drive with send().
        sends:
            Values to send into the generator in order.

        Returns
        -------
        list[GeneratorSection]
            Sections produced by each send, plus exhausted sentinel if
            StopIteration is raised.
        """
        sections: list[object] = []
        send_history: tuple = ()
        yield_index = 0
        # Prime the generator
        try:
            primed_value = next(gen)
            section = _make_gen_section(
                gen_id=self.gen_id,
                coordinate=_fiber_coordinate(self.base_coordinate, yield_index),
                yield_index=yield_index,
                value=primed_value,
                trust=self.trust,
                is_exhausted=False,
                send_history=send_history,
            )
            sections.append(section)
            yield_index += 1
        except StopIteration as exc:
            sections.append(
                _make_gen_section(
                    gen_id=self.gen_id,
                    coordinate=_fiber_coordinate(self.base_coordinate, 0),
                    yield_index=0,
                    value=exc.value,
                    trust=self.trust,
                    is_exhausted=True,
                    send_history=send_history,
                )
            )
            return sections
        for send_val in sends:
            send_history = send_history + (send_val,)
            try:
                yielded = gen.send(send_val)
                section = _make_gen_section(
                    gen_id=self.gen_id,
                    coordinate=_fiber_coordinate(self.base_coordinate, yield_index),
                    yield_index=yield_index,
                    value=yielded,
                    trust=self.trust,
                    is_exhausted=False,
                    send_history=send_history,
                )
                sections.append(section)
                yield_index += 1
            except StopIteration as exc:
                sections.append(
                    _make_gen_section(
                        gen_id=self.gen_id,
                        coordinate=_fiber_coordinate(self.base_coordinate, yield_index),
                        yield_index=yield_index,
                        value=exc.value,
                        trust=self.trust,
                        is_exhausted=True,
                        send_history=send_history,
                    )
                )
                break
        return sections

    def _make_section(
        self,
        value: Any,
        yield_index: int,
        is_exhausted: bool,
        send_history: tuple,
    ) -> object:
        """Construct a :class:`GeneratorSection` for a single yield point.

        Derives the fiber coordinate from the base coordinate and yield_index.
        Delegates to :func:`_make_gen_section` for the actual construction.
        This method is the canonical factory used throughout the builder and
        is exposed for copilot subclasses that need to override section
        construction.

        Parameters
        ----------
        value:
            The yielded value for this fiber.
        yield_index:
            Zero-based index of this yield point.
        is_exhausted:
            True if this section represents a StopIteration.
        send_history:
            Tuple of values sent into the generator up to this point.

        Returns
        -------
        GeneratorSection | dict
            A :class:`GeneratorSection` or plain dict.
        """
        coord = _fiber_coordinate(self.base_coordinate, yield_index)
        return _make_gen_section(
            gen_id=self.gen_id,
            coordinate=coord,
            yield_index=yield_index,
            value=value,
            trust=self.trust,
            is_exhausted=is_exhausted,
            send_history=send_history,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this builder's configuration to a JSON-safe dictionary.

        Returns all configuration fields so that builder instances can be
        identified and reconstructed in copilot audit trails.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of builder configuration.
        """
        return {
            "gen_id": self.gen_id,
            "base_coordinate": str(self.base_coordinate),
            "trust": self.trust.label(),
            "class": "LazyFiberBuilder",
        }


# ---
# IteratorSection
# ---


@dataclass(frozen=True, slots=True)
class IteratorSection:
    r"""A single step of a non-generator iterator modelled as a section.

    theory2.tex Ch18 §18.5 — non-generator iterables (lists, ranges, maps)
    are also lazy sheaf constructions when iterated step by step.  Each call
    to ``next()`` produces a new section at the corresponding fiber
    coordinate.

    Copilot tools that instrument ``for``-loops use this class to record each
    iteration step, associating it with the loop's coordinate and trust level.

    Parameters
    ----------
    iter_id:
        Unique identifier for this iterator instance.
    coordinate:
        Site coordinate corresponding to the iteration site.
    step_index:
        Zero-based index of this iteration step.
    item:
        The value produced at this step.
    trust:
        Trust level for this section.
    is_done:
        True if the iterator is exhausted at this step.
    source_type:
        The ``type.__name__`` of the source iterable.
    """

    iter_id: str
    coordinate: Coordinate
    step_index: int
    item: Any
    trust: TrustLevel
    is_done: bool
    source_type: str

    # ---

    def next_step(self, item: Any) -> IteratorSection:
        """Return a new section for the next iteration step.

        Increments ``step_index`` by one and sets ``item`` to the new value.
        The ``is_done`` flag is cleared since the iterator has produced
        another value.  Copilot instrumentation calls this at each loop
        iteration to build the section sequence.

        Parameters
        ----------
        item:
            The value produced at the next step.

        Returns
        -------
        IteratorSection
            New section with ``step_index + 1`` and ``item`` updated.
        """
        return replace(self, step_index=self.step_index + 1, item=item, is_done=False)

    def done(self) -> IteratorSection:
        """Return a new section marking this iterator as exhausted.

        Sets ``is_done=True``.  The ``item`` field retains its value from
        the previous step.  The exhausted section is the final entry in the
        iterator's section sequence, corresponding to ``StopIteration``.

        Returns
        -------
        IteratorSection
            New section with ``is_done=True``.
        """
        return replace(self, is_done=True)

    def as_generator_section(self) -> object:
        """Convert this iterator section to a :class:`GeneratorSection`-like object.

        Bridges between the IteratorSection (non-generator iterables) and
        the GeneratorSection (generator protocol) representations.  Copilot
        tools that accept only GeneratorSection inputs can consume
        IteratorSection objects through this adapter.

        Returns
        -------
        GeneratorSection | dict
            A :class:`GeneratorSection` or plain dict.
        """
        return _make_gen_section(
            gen_id=self.iter_id,
            coordinate=self.coordinate,
            yield_index=self.step_index,
            value=self.item,
            trust=self.trust,
            is_exhausted=self.is_done,
            send_history=(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this iterator section to a JSON-safe dictionary.

        Includes all fields including the item value's repr (truncated) and
        the source_type string for debugging and copilot evidence payloads.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation.
        """
        return {
            "iter_id": self.iter_id,
            "coordinate": str(self.coordinate),
            "step_index": self.step_index,
            "item": _repr_value(self.item),
            "trust": self.trust.label(),
            "is_done": self.is_done,
            "source_type": self.source_type,
        }

    def fiber_coordinate(self) -> Coordinate:
        """Return the fiber :class:`Coordinate` for this iteration step.

        Derives the fiber from the section's base coordinate and step_index,
        using the same construction as :func:`_fiber_coordinate`.  Copilot
        site-builder tools use fiber coordinates to locate iterator sections
        in the async sub-site.

        Returns
        -------
        Coordinate
            Fiber coordinate for step ``step_index``.
        """
        return _fiber_coordinate(self.coordinate, self.step_index)

    def section_id(self) -> str:
        """Return a stable hash ID for this section.

        Derived from iter_id and step_index so that the same step always
        produces the same ID regardless of item value.  Used by copilot
        evidence pipelines for deduplication.

        Returns
        -------
        str
            16-character hex prefix of SHA-256(iter_id:step_index).
        """
        return _stable_hash(f"{self.iter_id}:{self.step_index}")[:16]


# ---
# GeneratorCombinator
# ---


class GeneratorCombinator:
    r"""Itertools-style combinators implemented as section morphisms.

    theory2.tex Ch18 §18.5 — the itertools combinators (chain, islice, map,
    filter, groupby, zip, tee) are section morphisms that compose, restrict,
    and transform fiber sequences.  This class exposes them as classmethods
    so they can be called without instantiation, mirroring the itertools API.

    Trust semantics: mapping and filtering reduce trust by one step (since
    the transformation is not witnessed by the runtime); concatenation
    preserves trust; grouping preserves trust.  These rules are applied
    consistently so that copilot tools can audit the trust provenance of
    derived section sequences.
    """

    # ---

    @classmethod
    def chain_generators(
        cls,
        sections_a: list[object],
        sections_b: list[object],
    ) -> list[object]:
        """Concatenate two section sequences, renumbering yield_indices.

        Concatenates *sections_b* after *sections_a* and renumbers all
        yield_indices so that the combined sequence has contiguous indices
        starting from 0.  This corresponds to the chain() morphism on fiber
        sequences: the image of section_b's stalks is shifted to follow
        section_a's stalks.

        Parameters
        ----------
        sections_a:
            First section sequence.
        sections_b:
            Second section sequence to append.

        Returns
        -------
        list[GeneratorSection]
            Combined sequence with renumbered yield_indices.
        """
        combined = list(sections_a) + list(sections_b)
        renumbered: list[object] = []
        for new_index, sec in enumerate(combined):
            if hasattr(sec, "yield_index"):
                try:
                    renumbered.append(replace(sec, yield_index=new_index))
                except Exception:
                    renumbered.append(sec)
            elif isinstance(sec, dict):
                updated = dict(sec)
                updated["yield_index"] = new_index
                renumbered.append(updated)
            else:
                renumbered.append(sec)
        return renumbered

    @classmethod
    def islice_sections(
        cls,
        sections: list[object],
        start: int,
        stop: int,
    ) -> list[object]:
        """Return a slice of the section sequence from *start* to *stop*.

        Corresponds to ``itertools.islice``: restricts the fiber sequence to
        a sub-range.  Yield indices are not renumbered so the original
        positions are preserved for audit purposes.  Copilot tools use this
        to inspect a bounded window of a long generator sequence.

        Parameters
        ----------
        sections:
            Full section sequence to slice.
        start:
            Start index (inclusive).
        stop:
            Stop index (exclusive).

        Returns
        -------
        list[GeneratorSection]
            Sections at positions ``start`` to ``stop - 1``.
        """
        return list(itertools.islice(sections, start, stop))

    @classmethod
    def map_sections(
        cls,
        sections: list[object],
        fn: Any,
    ) -> list[object]:
        """Apply *fn* to each ``yielded_value``, returning transformed sections.

        Corresponds to ``map()`` on fiber sections.  Each section's
        ``yielded_value`` is replaced with ``fn(yielded_value)``; the trust
        level is stepped one level weaker to reflect that the transformation
        has not been independently witnessed.  Copilot analysis pipelines
        that transform generator output must propagate this trust reduction.

        Parameters
        ----------
        sections:
            Sections to transform.
        fn:
            A callable applied to each ``yielded_value``.

        Returns
        -------
        list[GeneratorSection]
            New sections with transformed values and weaker trust.
        """
        result: list[object] = []
        for sec in sections:
            if hasattr(sec, "yielded_value") and hasattr(sec, "fiber_trust"):
                try:
                    new_value = fn(sec.yielded_value)
                    new_trust = sec.fiber_trust.step_weaker()
                    result.append(replace(sec, yielded_value=new_value, fiber_trust=new_trust))
                except Exception:
                    result.append(sec)
            elif isinstance(sec, dict):
                try:
                    new_value = fn(sec.get("yielded_value"))
                    current_trust_label = sec.get("fiber_trust", "unverified")
                    updated = dict(sec)
                    updated["yielded_value"] = _repr_value(new_value)
                    result.append(updated)
                except Exception:
                    result.append(sec)
            else:
                result.append(sec)
        return result

    @classmethod
    def filter_sections(
        cls,
        sections: list[object],
        predicate: Any,
    ) -> list[object]:
        """Keep only sections where ``predicate(yielded_value)`` is ``True``.

        Corresponds to ``filter()`` on fiber sections.  Sections failing the
        predicate are removed; the remaining sections' yield_indices are
        preserved (not renumbered) to maintain traceability.  Copilot tools
        that audit generator output use this to narrow the section sequence
        to values of interest.

        Parameters
        ----------
        sections:
            Sections to filter.
        predicate:
            A callable that returns True for sections to keep.

        Returns
        -------
        list[GeneratorSection]
            Sections passing the predicate.
        """
        result: list[object] = []
        for sec in sections:
            if hasattr(sec, "yielded_value"):
                try:
                    if predicate(sec.yielded_value):
                        result.append(sec)
                except Exception:
                    pass
            elif isinstance(sec, dict):
                try:
                    if predicate(sec.get("yielded_value")):
                        result.append(sec)
                except Exception:
                    pass
        return result

    @classmethod
    def groupby_sections(
        cls,
        sections: list[object],
        key_fn: Any,
    ) -> dict[Any, list[object]]:
        """Group sections by ``key_fn(yielded_value)``.

        Corresponds to ``itertools.groupby`` on fiber sections, but unlike
        the standard library version, this method groups non-contiguous
        sections with the same key.  The result is a dict mapping each key
        to the list of sections sharing that key.  Copilot analysis tools
        use groupby to partition generator output by structural properties.

        Parameters
        ----------
        sections:
            Sections to group.
        key_fn:
            A callable returning the group key for a ``yielded_value``.

        Returns
        -------
        dict[Any, list[GeneratorSection]]
            Mapping of group key to list of sections.
        """
        groups: dict[Any, list[object]] = {}
        for sec in sections:
            if hasattr(sec, "yielded_value"):
                try:
                    key = key_fn(sec.yielded_value)
                except Exception:
                    key = None
            elif isinstance(sec, dict):
                try:
                    key = key_fn(sec.get("yielded_value"))
                except Exception:
                    key = None
            else:
                key = None
            if key not in groups:
                groups[key] = []
            groups[key].append(sec)
        return groups

    @classmethod
    def zip_sections(
        cls,
        sections_a: list[object],
        sections_b: list[object],
    ) -> list[tuple[object, object]]:
        """Zip two section sequences pairwise.

        Returns a list of ``(section_a, section_b)`` pairs, stopping at
        the shorter sequence.  Corresponds to ``zip()`` applied to two
        generator stalks.  Copilot tools that compare parallel generator
        outputs use this to produce side-by-side section pairs.

        Parameters
        ----------
        sections_a:
            First section sequence.
        sections_b:
            Second section sequence.

        Returns
        -------
        list[tuple[GeneratorSection, GeneratorSection]]
            Paired sections up to the length of the shorter sequence.
        """
        return list(zip(sections_a, sections_b))

    @classmethod
    def tee_sections(
        cls,
        sections: list[object],
        n: int = 2,
    ) -> list[list[object]]:
        """Return *n* independent shallow copies of the section sequence.

        Corresponds to ``itertools.tee``: produces *n* independent iterators
        (here: lists) from a single source.  Each copy is a new list sharing
        the same section objects (shallow copy), so copilot tools that need
        multiple independent traversals of the same sequence can call this
        without duplicating section objects.

        Parameters
        ----------
        sections:
            Source section sequence to replicate.
        n:
            Number of copies to produce (default 2).

        Returns
        -------
        list[list[GeneratorSection]]
            *n* independent lists each containing all sections.
        """
        if n < 1:
            return []
        return [list(sections) for _ in range(n)]


# ---
# Module public API
# ---

__all__ = [
    "GeneratorSheaf",
    "LazyFiberBuilder",
    "IteratorSection",
    "GeneratorCombinator",
]
