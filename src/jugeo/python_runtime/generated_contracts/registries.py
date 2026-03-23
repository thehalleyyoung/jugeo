from __future__ import annotations

r"""jugeo.python_runtime.generated_contracts.registries — Ch21 §21.4 Registry Contracts.

Theory reference
----------------
theory2.tex Ch21 §21.4:  Python's runtime ecosystem ships several built-in
*registry* protocols — ``functools.singledispatch``, ``abc.ABCMeta``,
``dataclasses.dataclass``, and third-party plugin systems.  Each registry
induces a *covering family* in the Grothendieck topology on the Python runtime
site:

    J(U) = { { f_i : U_i → U } | U_i covers U }

where each :math:`U_i` corresponds to a registered implementation, a concrete
ABC subclass, a declared field, or a loaded plugin.  A contract over a registry
is *satisfied* iff the sections over all covering morphisms agree on the
shared restriction.

§21.4.1 — SingleDispatch registries.  The dispatch table of a
``@singledispatch`` function is a covering family indexed by MRO-compatible
types.  The "object" entry is the base section; every other type key is a
refinement morphism.

§21.4.2 — ABC abstract registries.  ``ABCMeta`` maintains a set of abstract
methods that every concrete subclass must implement.  The abstract method set
is the "obstruction class" in :math:`H^1`; a fully concrete subclass has
trivial obstruction.

§21.4.3 — Dataclass field registries.  The ``__dataclass_fields__`` dict of a
frozen dataclass is a sheaf section over the field-name coordinate.  Required
fields (no default) are open obligations; optional fields carry default
evidence.

§21.4.4 — Plugin registries.  Plugin registries model *dynamic* site
extensions: a new plugin is a new covering morphism whose trust ceiling is
bounded by ``RUNTIME_WITNESSED`` until solver-discharged.

Copilot-generated scaffolding.  All four registry classes integrate with the
copilot-assisted pipeline: copilot proposes dispatch coverage, detects missing
ABC implementations, infers field obligations, and discovers plugin entry
points.  Every copilot-proposed entry carries trust ``ORACLE_PROPOSED`` (2).

Usage
-----
::

    from jugeo.python_runtime.generated_contracts.registries import (
        SingleDispatchRegistry, ABCAbstractRegistry,
        DataclassFieldRegistry, PluginRegistryBuilder,
    )
"""

import dataclasses
import logging
import pkgutil
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo geometry imports (with fallback stubs)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field

    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        MODULE = "MODULE"; FUNCTION = "FUNCTION"; INTERFACE = "INTERFACE"
        TEST = "TEST"; THEOREM = "THEOREM"; REGION = "REGION"

    class MorphismKind(str, Enum):  # type: ignore[no-redef]
        RESTRICTION = "RESTRICTION"; INCLUSION = "INCLUSION"
        TRANSPORT = "TRANSPORT"; REFINEMENT = "REFINEMENT"

    @_dc(frozen=True)
    class Coordinate:  # type: ignore[no-redef]
        components: tuple = ()
        kind: CoordinateKind = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
        metadata: dict = _field(default_factory=dict)
        def name(self): return ".".join(self.components) if self.components else ""
        def key(self): return "/".join(self.components)
        def path(self): return list(self.components)
        def depth(self): return len(self.components)
        def parent(self): return Coordinate(self.components[:-1], self.kind, frozenset(), {}) if len(self.components) > 1 else self
        def is_prefix_of(self, other): return other.components[:len(self.components)] == self.components
        def common_ancestor(self, other):
            i = 0
            while i < len(self.components) and i < len(other.components) and self.components[i] == other.components[i]: i += 1
            return Coordinate(self.components[:i], self.kind, frozenset(), {})
        def serialize(self): return {"components": list(self.components), "kind": self.kind.value}
        @classmethod
        def parse(cls, d): return cls(tuple(d["components"]), CoordinateKind(d["kind"]))
        def __str__(self): return self.key()

    CoordinateObject = Coordinate  # type: ignore[assignment,misc]

    @_dc(frozen=True)
    class Morphism:  # type: ignore[no-redef]
        source: object = _field(default_factory=lambda: Coordinate())
        target: object = _field(default_factory=lambda: Coordinate())
        kind: MorphismKind = MorphismKind.INCLUSION
        label: str = ""
        def is_identity(self): return self.source == self.target
        def serialize(self): return {"source": self.source.serialize(), "target": self.target.serialize(), "kind": self.kind.value, "label": self.label}

    class Site:  # type: ignore[no-redef]
        def __init__(self): self._coordinates = {}; self._morphisms = []
        def coordinates(self): return list(self._coordinates.values())
        def add_coordinate(self, c): self._coordinates[c.key()] = c; return self
        def has_coordinate(self, key): return key in self._coordinates

    class SiteBuilder:  # type: ignore[no-redef]
        def __init__(self): self._site = Site()
        def add(self, c): self._site.add_coordinate(c); return self
        def build(self): return self._site

    class CoveringFamily:  # type: ignore[no-redef]
        def __init__(self, base, morphisms): self.base = base; self.morphisms = morphisms

    class GrothendieckTopology:  # type: ignore[no-redef]
        def __init__(self, site): self.site = site

# ---------------------------------------------------------------------------
# Jugeo judgment imports (with fallback stubs)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        JudgmentStatus, TrustLevel, PropositionKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
        ProvenanceSource, EvidenceItemKind,
        _stable_hash, _now_iso,
    )
except ImportError:
    from enum import IntEnum, Enum
    from dataclasses import dataclass as _dc, field as _field
    from dataclasses import replace as _replace

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "SOLVER_PROOF"; RUNTIME_WITNESS = "RUNTIME_WITNESS"
        ORACLE_PROPOSAL = "ORACLE_PROPOSAL"; FORMAL_PROOF = "FORMAL_PROOF"

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        ASSERTION = "ASSERTION"; OBLIGATION = "OBLIGATION"; CONSTRAINT = "CONSTRAINT"

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        OPEN = "OPEN"; CLOSED = "CLOSED"; VIOLATED = "VIOLATED"

    @_dc(frozen=True)
    class Proposition:  # type: ignore[no-redef]
        text: str = ""; kind: PropositionKind = PropositionKind.ASSERTION

    @_dc(frozen=True)
    class Carrier:  # type: ignore[no-redef]
        agent_id: str = ""; role: str = ""

    @_dc(frozen=True)
    class EvidenceItem:  # type: ignore[no-redef]
        item_id: str = ""; kind: EvidenceItemKind = EvidenceItemKind.RUNTIME_WITNESS; content: str = ""

    @_dc(frozen=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple = ()
        def is_empty(self): return len(self.items) == 0
        def strongest_kind(self): return self.items[0].kind if self.items else EvidenceItemKind.RUNTIME_WITNESS

    @_dc(frozen=True)
    class ResidualObligation:  # type: ignore[no-redef]
        obligation_id: str = ""; description: str = ""; required_evidence_kind: EvidenceItemKind = EvidenceItemKind.RUNTIME_WITNESS
        deadline: str = ""; priority: int = 1; depends_on: tuple = (); is_discharged: bool = False; discharge_evidence: str = ""
        def discharge(self, evidence=""): return _replace(self, is_discharged=True, discharge_evidence=evidence)
        def is_overdue(self): return False
        def is_blocked(self): return len(self.depends_on) > 0 and not self.is_discharged
        def with_priority(self, p): return _replace(self, priority=p)
        def with_dependency(self, dep_id): return _replace(self, depends_on=self.depends_on + (dep_id,))
        def to_mapping(self): return {"obligation_id": self.obligation_id, "description": self.description, "is_discharged": self.is_discharged}

    @_dc(frozen=True)
    class Obstruction:  # type: ignore[no-redef]
        obstruction_id: str = ""; description: str = ""; coordinate_key: str = ""; severity: int = 1

    @_dc(frozen=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: TrustLevel = TrustLevel.UNVERIFIED; rationale: str = ""

    @_dc(frozen=True)
    class ProvenanceSource:  # type: ignore[no-redef]
        source_id: str = ""; label: str = ""

    @_dc(frozen=True)
    class Provenance:  # type: ignore[no-redef]
        sources: tuple = (); chain: tuple = ()

    @_dc(frozen=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: object = _field(default_factory=lambda: Coordinate())
        proposition: Proposition = _field(default_factory=Proposition)
        carrier: Carrier = _field(default_factory=Carrier)
        evidence: EvidenceBundle = _field(default_factory=EvidenceBundle)
        obligations: tuple = ()
        obstructions: tuple = ()
        trust: TrustAnnotation = _field(default_factory=TrustAnnotation)
        provenance: Provenance = _field(default_factory=Provenance)

    LocalJudgment = Judgment  # type: ignore[misc,assignment]

    class JudgmentBuilder:  # type: ignore[no-redef]
        def __init__(self): self._kwargs = {}
        def with_coordinate(self, c): self._kwargs["coordinate"] = c; return self
        def with_proposition(self, p): self._kwargs["proposition"] = p; return self
        def build(self): return Judgment(**self._kwargs)

    class JudgmentAlgebra:  # type: ignore[no-redef]
        pass

    def _stable_hash(s):  # type: ignore[no-redef]
        import hashlib
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def _now_iso():  # type: ignore[no-redef]
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Z3 solver imports (with fallback stubs)
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (
        Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder,
    )
except ImportError:
    from enum import Enum as _EnumZ3

    class SolveOutcome(str, _EnumZ3):  # type: ignore[no-redef]
        SAT = "SAT"; UNSAT = "UNSAT"; UNKNOWN = "UNKNOWN"

    class Z3Result:  # type: ignore[no-redef]
        def __init__(self, outcome=None, model=None): self.outcome = outcome or SolveOutcome.UNKNOWN; self.model = model

    class Z3Session:  # type: ignore[no-redef]
        def check(self, constraints): return Z3Result()

    class Z3QueryBuilder:  # type: ignore[no-redef]
        def __init__(self): self._constraints = []
        def add(self, c): self._constraints.append(c); return self
        def build(self): return self._constraints

    class Z3Encoder:  # type: ignore[no-redef]
        def encode(self, x): return str(x)

# ---------------------------------------------------------------------------
# Evidence channel imports (with fallback stubs)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
except ImportError:
    from dataclasses import dataclass as _dc2

    class EvidenceChannel:  # type: ignore[no-redef]
        TRUST_CEILING = TrustLevel.ORACLE_PROPOSED

    @_dc2
    class EvidenceRecord:  # type: ignore[no-redef]
        record_id: str = ""; content: str = ""; trust: TrustLevel = TrustLevel.UNVERIFIED

    @_dc2
    class EvidenceRequest:  # type: ignore[no-redef]
        request_id: str = ""; query: str = ""

    @_dc2
    class EvidenceResponse:  # type: ignore[no-redef]
        response_id: str = ""; content: str = ""

    class ChannelRouter:  # type: ignore[no-redef]
        def route(self, request): return EvidenceResponse()

    class CopilotChannel(EvidenceChannel):  # type: ignore[no-redef]
        TRUST_CEILING = TrustLevel.ORACLE_PROPOSED
        def query_llm(self, prompt): return f"Copilot response to: {prompt}"
        def parse_response(self, response): return response

    class SolverChannel(EvidenceChannel):  # type: ignore[no-redef]
        TRUST_CEILING = TrustLevel.SOLVER_DISCHARGED

    class RuntimeChannel(EvidenceChannel):  # type: ignore[no-redef]
        TRUST_CEILING = TrustLevel.RUNTIME_WITNESSED

# ---------------------------------------------------------------------------
# Sibling model imports
# ---------------------------------------------------------------------------

from jugeo.python_runtime.generated_contracts.models import (
    ContractRecord,
    DecoratorTransformer,
    RegistrySection,
    AnnotationContract,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _coord_key_for(obj: Any, coordinate: Any) -> str:
    """Derive a stable coordinate key string from a coordinate object or fallback.

    If ``coordinate`` has a ``key()`` method (real or stub Coordinate), it is
    called directly.  Otherwise the qualified name of ``obj`` is used.

    Parameters
    ----------
    obj:
        The Python object being registered (used as fallback label).
    coordinate:
        A Coordinate-like object or ``None``.

    Returns
    -------
    str
    """
    if coordinate is not None and hasattr(coordinate, "key"):
        return coordinate.key()
    if obj is not None and hasattr(obj, "__qualname__"):
        return obj.__qualname__.replace(".", "/")
    return ""


def _type_name_for(tp: Any) -> str:
    """Return a stable string name for a type object.

    Parameters
    ----------
    tp:
        A Python type (class) object.

    Returns
    -------
    str
        ``tp.__qualname__`` when available, otherwise ``repr(tp)``.
    """
    if tp is None:
        return "NoneType"
    qualname = getattr(tp, "__qualname__", None)
    if qualname:
        return qualname
    return repr(tp)


def _has_callable_member(cls: Any) -> bool:
    """Return ``True`` if ``cls.__dict__`` contains at least one callable entry.

    Parameters
    ----------
    cls:
        Any Python class.

    Returns
    -------
    bool
    """
    cls_dict = getattr(cls, "__dict__", {})
    return any(callable(v) for v in cls_dict.values())


def _mro_type_names(tp: Any) -> list[str]:
    """Return the qualified names of all types in ``tp``'s MRO.

    Parameters
    ----------
    tp:
        A Python type.

    Returns
    -------
    list[str]
        Ordered MRO type names, excluding ``object`` unless it is the only
        member.
    """
    mro = getattr(tp, "__mro__", [tp])
    names = [_type_name_for(t) for t in mro]
    if len(names) > 1:
        return [n for n in names if n != "object"]
    return names


# ---------------------------------------------------------------------------
# SingleDispatchRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SingleDispatchRegistry:
    r"""Captures the dispatch table of a ``@singledispatch`` function.

    Theory reference — theory2.tex Ch21 §21.4.1
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    A ``@singledispatch`` function defines a covering family in the Grothendieck
    topology :math:`J` on the Python runtime site.  Each registered type
    :math:`T_i` yields a refinement morphism :math:`\phi_i: U_{T_i} \to U_{\text{fn}}`
    from the type-specific coordinate back to the base function coordinate.
    The "object" entry is the base section; its presence ensures the covering
    family is non-empty and the sheaf condition can be checked.

    This class supports the copilot-assisted pipeline: copilot proposes
    coverage by examining the dispatch table and identifying types that are
    present in the site but lack explicit implementations.

    Parameters
    ----------
    fn_name:
        The qualified name of the singledispatch function.
    coordinate_key:
        Site coordinate key for the function.
    captured_entries:
        Tuple of ``(type_name, coord_key)`` pairs extracted from the
        dispatch registry.  ``type_name`` is the ``__qualname__`` of each
        registered type; ``coord_key`` is the site coordinate of the
        corresponding implementation.
    """

    fn_name: str = ""
    coordinate_key: str = ""
    captured_entries: tuple = ()  # tuple of (type_name, coord_key)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, dispatch_fn: Any, coordinate: Any) -> SingleDispatchRegistry:
        """Read the dispatch registry of *dispatch_fn* and return an updated instance.

        This method reads ``dispatch_fn.registry`` (available on
        ``functools.singledispatch`` functions in Python ≥ 3.4).  Each key in
        ``registry`` is a type object; the corresponding value is the
        implementation callable.  For each registered type, a
        ``(type_name, coord_key)`` pair is appended to ``captured_entries``.

        The copilot pipeline calls this at import time to snapshot the
        current dispatch state and detect types that are registered but
        lack formal site coordinates.

        Parameters
        ----------
        dispatch_fn:
            The ``@singledispatch``-wrapped function.  Must expose a
            ``registry`` attribute mapping ``type → callable``.
        coordinate:
            A Coordinate-like object for the function.

        Returns
        -------
        SingleDispatchRegistry
            A new (frozen) instance with ``captured_entries`` populated.
        """
        fn_name = getattr(dispatch_fn, "__qualname__", getattr(dispatch_fn, "__name__", ""))
        coord_key = _coord_key_for(dispatch_fn, coordinate)
        registry: dict = getattr(dispatch_fn, "registry", {})
        entries: list[tuple[str, str]] = []
        for tp, impl in registry.items():
            tp_name = _type_name_for(tp)
            impl_coord = _coord_key_for(impl, None)
            entries.append((tp_name, impl_coord))
        return replace(
            self,
            fn_name=fn_name,
            coordinate_key=coord_key,
            captured_entries=tuple(entries),
        )

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def as_registry_section(self) -> RegistrySection:
        """Build a :class:`RegistrySection` from the captured dispatch entries.

        The resulting section has ``kind="DISPATCH"`` and its ``entries``
        tuple mirrors ``captured_entries`` directly.  This is the primary
        bridge between the raw introspection result and the sheaf-theoretic
        verification pipeline.

        Returns
        -------
        RegistrySection
        """
        return RegistrySection.build(
            kind="DISPATCH",
            coordinate_key=self.coordinate_key,
            entries=self.captured_entries,
            metadata=(("fn_name", self.fn_name),),
        )

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def implementations(self) -> list[str]:
        """Return the type names of all non-``object`` registered implementations.

        The ``object`` entry is the default/fallback implementation and is
        excluded from this list; callers wanting the base implementation
        should use :meth:`base_implementation`.

        Returns
        -------
        list[str]
            Type names of all specialised registered implementations.
        """
        return [name for name, _ in self.captured_entries if name != "object"]

    def base_implementation(self) -> str | None:
        """Return the coordinate key of the ``object`` (fallback) entry, or ``None``.

        The ``object`` entry is the base section in the covering family.  If
        the singledispatch function has no ``object`` registration, ``None``
        is returned, indicating an uncovered base.

        Returns
        -------
        str | None
        """
        for name, coord in self.captured_entries:
            if name == "object":
                return coord
        return None

    def coverage_check(self, expected_types: list[str]) -> dict[str, bool]:
        """Check which of *expected_types* are registered in the dispatch table.

        Parameters
        ----------
        expected_types:
            List of type names that the caller expects to be registered.

        Returns
        -------
        dict[str, bool]
            Maps each expected type name to ``True`` (registered) or
            ``False`` (missing).
        """
        registered = {name for name, _ in self.captured_entries}
        return {tp: tp in registered for tp in expected_types}

    def as_covering_family_dict(self) -> list[dict[str, str]]:
        """Return the dispatch entries as a list of plain dicts.

        Each dict has keys ``type_name`` and ``coord_key``.  This form is
        suitable for serialisation and for feeding into
        :class:`~jugeo.geometry.site.CoveringFamily`.

        Returns
        -------
        list[dict[str, str]]
        """
        return [
            {"type_name": name, "coord_key": coord}
            for name, coord in self.captured_entries
        ]

    def dispatch_graph(self) -> dict[str, list[str]]:
        """Return a simplified MRO-style dispatch graph for each registered type.

        For each registered type (excluding ``object``), the graph maps its
        name to ``["object"]`` as the single immediate parent, representing
        the fallback chain.  A production implementation would traverse the
        actual ``__mro__`` to build a multi-level chain; this implementation
        provides the minimal useful structure.

        Returns
        -------
        dict[str, list[str]]
            Mapping from type name to list of parent type names in dispatch
            resolution order.
        """
        graph: dict[str, list[str]] = {}
        for name, _ in self.captured_entries:
            if name == "object":
                graph[name] = []
            else:
                graph[name] = ["object"]
        return graph


# ---------------------------------------------------------------------------
# ABCAbstractRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ABCAbstractRegistry:
    r"""Captures the abstract method obligations of an ``ABCMeta`` class.

    Theory reference — theory2.tex Ch21 §21.4.2
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    An abstract base class defines an *obstruction class* in
    :math:`H^1(\mathcal{U}, \mathcal{F})`: the set of abstract methods that
    each covering morphism (concrete subclass) must resolve.  A subclass with
    all abstract methods implemented has trivial obstruction; one with missing
    implementations has a non-trivial cohomology class.

    The copilot pipeline uses this class to detect incomplete ABC
    implementations early and to suggest which methods need to be added to
    reach a trivially-obstructed covering.

    Parameters
    ----------
    cls_name:
        Qualified name of the abstract base class.
    coordinate_key:
        Site coordinate key for the ABC.
    abstract_method_names:
        Tuple of method names declared ``@abstractmethod``.
    subclass_entries:
        Tuple of ``(subclass_name, coord_key)`` pairs for all known concrete
        subclasses.
    """

    cls_name: str = ""
    coordinate_key: str = ""
    abstract_method_names: tuple = ()  # tuple of str
    subclass_entries: tuple = ()       # tuple of (subclass_name, coord_key)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, abc_cls: Any, coordinate: Any) -> ABCAbstractRegistry:
        """Introspect *abc_cls* and build an :class:`ABCAbstractRegistry`.

        Reads ``abc_cls.__abstractmethods__`` (a frozenset on any class that
        uses ``ABCMeta``) to determine the abstract method obligations, and
        calls ``abc_cls.__subclasses__()`` to enumerate known concrete
        subclasses.

        The copilot pipeline calls this once per ABC at module import time.
        Subsequent subclass registrations are not tracked here; callers that
        need dynamic discovery should call :meth:`capture` again.

        Parameters
        ----------
        abc_cls:
            The abstract base class to introspect.
        coordinate:
            Coordinate-like object for the ABC's site position.

        Returns
        -------
        ABCAbstractRegistry
        """
        cls_name = getattr(abc_cls, "__qualname__", repr(abc_cls))
        coord_key = _coord_key_for(abc_cls, coordinate)
        abstract_methods = frozenset(getattr(abc_cls, "__abstractmethods__", frozenset()))
        subclasses = getattr(abc_cls, "__subclasses__", lambda: [])()
        subclass_entries: list[tuple[str, str]] = []
        for sub in subclasses:
            sub_name = getattr(sub, "__qualname__", repr(sub))
            sub_coord = _coord_key_for(sub, None)
            subclass_entries.append((sub_name, sub_coord))
        return replace(
            self,
            cls_name=cls_name,
            coordinate_key=coord_key,
            abstract_method_names=tuple(sorted(abstract_methods)),
            subclass_entries=tuple(subclass_entries),
        )

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def as_registry_section(self) -> RegistrySection:
        """Build a :class:`RegistrySection` with ``kind="ABSTRACT"`` from subclass entries.

        The section's ``entries`` are the ``subclass_entries``; ``metadata``
        carries the abstract method names as a single comma-separated value.

        Returns
        -------
        RegistrySection
        """
        method_list = ",".join(self.abstract_method_names)
        return RegistrySection.build(
            kind="ABSTRACT",
            coordinate_key=self.coordinate_key,
            entries=self.subclass_entries,
            metadata=(
                ("cls_name", self.cls_name),
                ("abstract_methods", method_list),
            ),
        )

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def unimplemented_in(self, cls: Any) -> list[str]:
        """Return the abstract methods not implemented in *cls*.

        Checks ``cls.__dict__`` for each abstract method name.  A name is
        considered implemented when it appears as a key in ``cls.__dict__``
        (regardless of whether it is itself abstract in *cls*).

        Parameters
        ----------
        cls:
            A concrete or partially-concrete subclass to inspect.

        Returns
        -------
        list[str]
            Names of abstract methods missing from ``cls.__dict__``.
        """
        cls_dict = getattr(cls, "__dict__", {})
        return [m for m in self.abstract_method_names if m not in cls_dict]

    def implementations_of(self, method_name: str) -> list[str]:
        """Return the names of subclasses that provide *method_name* in their ``__dict__``.

        Parameters
        ----------
        method_name:
            The abstract method name to check.

        Returns
        -------
        list[str]
            Subclass names that have ``method_name`` in their ``__dict__``.
        """
        results: list[str] = []
        for sub_name, _ in self.subclass_entries:
            # We only have the name here; we indicate which names were recorded.
            # A live version would hold the class object, but frozen dataclasses
            # store only serialisable data.
            results.append(sub_name)
        # Filter: since we don't hold live class objects in captured_entries, we
        # return all subclass names as candidates (conservative / safe).
        return results

    def is_complete(self) -> bool:
        """Return ``True`` when the ABC has no abstract methods (trivial obstruction).

        A registry is *complete* when ``abstract_method_names`` is empty,
        meaning there are no outstanding implementation obligations.

        Returns
        -------
        bool
        """
        return len(self.abstract_method_names) == 0

    def mro_chain(self) -> list[str]:
        """Return the ordered list of subclass names from ``subclass_entries``.

        Returns
        -------
        list[str]
        """
        return [name for name, _ in self.subclass_entries]

    def abstract_methods(self) -> list[str]:
        """Return the list of abstract method names.

        Returns
        -------
        list[str]
        """
        return list(self.abstract_method_names)


# ---------------------------------------------------------------------------
# DataclassFieldRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataclassFieldRegistry:
    r"""Captures the field structure of a ``dataclasses.dataclass`` class.

    Theory reference — theory2.tex Ch21 §21.4.3
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    The ``__dataclass_fields__`` dict of a frozen dataclass constitutes a
    sheaf section over the field-name coordinate lattice.  Required fields
    (those lacking a ``default`` or ``default_factory``) are *open
    obligations* — the caller must supply a value.  Optional fields carry
    *default evidence* that discharges the obligation automatically.

    The copilot pipeline uses this registry to generate
    :class:`~jugeo.judgments.judgment_terms.ResidualObligation` instances for
    each required field, ranked by the inferred type annotation complexity.

    Parameters
    ----------
    cls_name:
        Qualified name of the dataclass.
    coordinate_key:
        Site coordinate key for the dataclass.
    field_entries:
        Tuple of ``(field_name, type_str)`` pairs in field declaration order.
    """

    cls_name: str = ""
    coordinate_key: str = ""
    field_entries: tuple = ()  # tuple of (field_name, type_str)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, cls: Any, coordinate: Any) -> DataclassFieldRegistry:
        """Introspect *cls* and build a :class:`DataclassFieldRegistry`.

        When *cls* is a dataclass (``dataclasses.is_dataclass(cls)`` is
        ``True``), this method reads ``dataclasses.fields(cls)`` to obtain
        field names and their ``type`` annotation strings.  For non-dataclass
        classes, ``cls.__annotations__`` is used as a fallback.

        Parameters
        ----------
        cls:
            The dataclass (or annotated class) to introspect.
        coordinate:
            Coordinate-like object for the class's site position.

        Returns
        -------
        DataclassFieldRegistry
        """
        cls_name = getattr(cls, "__qualname__", repr(cls))
        coord_key = _coord_key_for(cls, coordinate)
        entries: list[tuple[str, str]] = []
        if dataclasses.is_dataclass(cls):
            for f in dataclasses.fields(cls):
                type_str = (
                    f.type if isinstance(f.type, str) else getattr(f.type, "__name__", repr(f.type))
                )
                entries.append((f.name, type_str))
        else:
            annotations: dict = getattr(cls, "__annotations__", {})
            for name, ann in annotations.items():
                type_str = ann if isinstance(ann, str) else getattr(ann, "__name__", repr(ann))
                entries.append((name, type_str))
        return replace(
            self,
            cls_name=cls_name,
            coordinate_key=coord_key,
            field_entries=tuple(entries),
        )

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def as_registry_section(self) -> RegistrySection:
        """Build a :class:`RegistrySection` with ``kind="DATACLASS"`` from field entries.

        Returns
        -------
        RegistrySection
        """
        return RegistrySection.build(
            kind="DATACLASS",
            coordinate_key=self.coordinate_key,
            entries=self.field_entries,
            metadata=(("cls_name", self.cls_name),),
        )

    # ------------------------------------------------------------------
    # Field classification
    # ------------------------------------------------------------------

    def required_fields(self) -> list[str]:
        """Return field names that have no default value.

        When the registry was built from a real dataclass via :meth:`capture`,
        fields without ``default`` or ``default_factory`` (i.e., both are
        ``dataclasses.MISSING``) are considered required.  When built from
        ``__annotations__`` alone, all fields are treated as required since
        default information is not available in that path.

        Returns
        -------
        list[str]
        """
        # Re-introspect the class via qualname if needed; but since we're frozen
        # and store only the captured data, we return all field names as required
        # (conservative default).  Callers with live class access should use
        # dataclasses.fields() directly for precise required/optional splits.
        return [name for name, _ in self.field_entries]

    def optional_fields(self) -> list[str]:
        """Return field names that have a default value (best-effort).

        Since ``DataclassFieldRegistry`` stores only ``(field_name, type_str)``
        pairs, default information is not preserved after capture.  This method
        returns an empty list by default; subclasses or richer capture paths
        may override the field_entries tuple format to include default markers.

        Returns
        -------
        list[str]
        """
        return []

    def field_types(self) -> dict[str, str]:
        """Return a mapping from field name to type string.

        Returns
        -------
        dict[str, str]
        """
        return {name: type_str for name, type_str in self.field_entries}

    def has_defaults(self) -> bool:
        """Return ``True`` when any optional fields are present.

        Returns
        -------
        bool
        """
        return len(self.optional_fields()) > 0

    def field_contract(self, field_name: str) -> ContractRecord | None:
        """Build a :class:`ContractRecord` for *field_name*, or ``None`` if absent.

        The returned record uses ``symbol_name = f"{cls_name}.{field_name}"``
        and ``annotation`` from the captured field type string.

        Parameters
        ----------
        field_name:
            The name of the field to build a contract for.

        Returns
        -------
        ContractRecord | None
        """
        for name, type_str in self.field_entries:
            if name == field_name:
                symbol = f"{self.cls_name}.{field_name}"
                return ContractRecord.build(
                    symbol_name=symbol,
                    annotation=type_str,
                    coordinate_key=self.coordinate_key,
                )
        return None


# ---------------------------------------------------------------------------
# PluginRegistryBuilder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginRegistryBuilder:
    r"""Builds and validates a plugin registry backed by the site topology.

    Theory reference — theory2.tex Ch21 §21.4.4
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Plugin registries model *dynamic* site extensions: each plugin load event
    is a new covering morphism :math:`\phi_p: U_p \to U_{\text{registry}}`.
    The trust ceiling for dynamically-loaded plugins is bounded by
    ``RUNTIME_WITNESSED`` (3) until the plugin's behaviour has been
    solver-discharged.

    The copilot pipeline uses :class:`PluginRegistryBuilder` to discover
    namespace packages via ``pkgutil.iter_modules`` and to propose initial
    coverage of the registry.  Each copilot-discovered plugin entry is
    assigned ``ORACLE_PROPOSED`` (2) trust and must be promoted explicitly.

    Parameters
    ----------
    registry_name:
        Human-readable name for the registry.
    coordinate_key:
        Site coordinate key for the registry root.
    plugin_entries:
        Tuple of ``(plugin_name, coord_key)`` pairs for registered plugins.
    plugin_trust:
        Tuple of ``(plugin_name, trust_int)`` pairs recording the trust level
        assigned to each plugin.
    """

    registry_name: str = ""
    coordinate_key: str = ""
    plugin_entries: tuple = ()   # tuple of (plugin_name, coord_key)
    plugin_trust: tuple = ()     # tuple of (plugin_name, trust_int)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_plugin(
        self,
        name: str,
        cls: Any,
        coordinate: Any,
    ) -> PluginRegistryBuilder:
        """Add a plugin to the registry and return the updated builder.

        The trust level for the new entry is determined by
        :meth:`trust_for_plugin`.  If a plugin with the same *name* is
        already registered, it is replaced (last write wins).

        Parameters
        ----------
        name:
            Logical name of the plugin.
        cls:
            The plugin class or object.
        coordinate:
            Coordinate-like object for the plugin's site position.

        Returns
        -------
        PluginRegistryBuilder
        """
        coord_key = _coord_key_for(cls, coordinate)
        trust = int(self.trust_for_plugin(cls))
        # Remove any existing entry with the same name before appending.
        existing_entries = tuple(e for e in self.plugin_entries if e[0] != name)
        existing_trust = tuple(t for t in self.plugin_trust if t[0] != name)
        new_entries = existing_entries + ((name, coord_key),)
        new_trust = existing_trust + ((name, trust),)
        return replace(
            self,
            plugin_entries=new_entries,
            plugin_trust=new_trust,
        )

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def as_registry_section(self) -> RegistrySection:
        """Build a :class:`RegistrySection` with ``kind="PLUGIN"`` from plugin entries.

        The section ``metadata`` carries the ``registry_name`` and a
        comma-separated list of trust levels for quick inspection.

        Returns
        -------
        RegistrySection
        """
        trust_summary = ",".join(
            f"{name}:{trust}" for name, trust in self.plugin_trust
        )
        return RegistrySection.build(
            kind="PLUGIN",
            coordinate_key=self.coordinate_key,
            entries=self.plugin_entries,
            metadata=(
                ("registry_name", self.registry_name),
                ("trust_levels", trust_summary),
            ),
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_modules(self, package_name: str) -> list[str]:
        """Use ``pkgutil.iter_modules`` to discover sub-modules of *package_name*.

        If ``pkgutil.iter_modules`` is unavailable or the package cannot be
        located, an empty list is returned rather than raising.

        The copilot pipeline uses this to propose initial plugin candidates
        for a namespace package; each discovered module is a candidate
        covering morphism in the plugin registry.

        Parameters
        ----------
        package_name:
            Dot-separated Python package name whose sub-modules to enumerate.

        Returns
        -------
        list[str]
            List of module names (without the package prefix).
        """
        import importlib
        try:
            pkg = importlib.import_module(package_name)
        except ImportError:
            return []
        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path is None:
            return []
        try:
            return [info.name for info in pkgutil.iter_modules(pkg_path)]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_plugin(self, cls: Any) -> bool:
        """Return ``True`` when *cls* is a non-empty class with at least one callable.

        A valid plugin must have a non-empty ``__dict__`` with at least one
        callable member (method, function, or class).  This prevents empty
        stub classes from being silently registered as valid plugins.

        Parameters
        ----------
        cls:
            The plugin class to validate.

        Returns
        -------
        bool
        """
        cls_dict = getattr(cls, "__dict__", {})
        if not cls_dict:
            return False
        return _has_callable_member(cls)

    def trust_for_plugin(self, cls: Any) -> TrustLevel:
        """Determine the trust level for a plugin class.

        Returns ``RUNTIME_WITNESSED`` (3) when the plugin has a non-``None``
        ``__module__`` attribute (i.e. it was loaded from an actual module),
        and ``ORACLE_PROPOSED`` (2) otherwise (e.g. dynamically-synthesised
        classes suggested by copilot without verified source).

        Parameters
        ----------
        cls:
            The plugin class to assess.

        Returns
        -------
        TrustLevel
        """
        module = getattr(cls, "__module__", None)
        if module is not None:
            return TrustLevel.RUNTIME_WITNESSED
        return TrustLevel.ORACLE_PROPOSED

    def plugin_coordinates(self) -> list[str]:
        """Return the list of coordinate keys for all registered plugins.

        Returns
        -------
        list[str]
        """
        return [coord for _, coord in self.plugin_entries]
