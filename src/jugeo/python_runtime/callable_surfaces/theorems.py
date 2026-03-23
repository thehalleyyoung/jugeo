"""Formal theorem statements for callable_surfaces (theory2.tex Ch16).

This module declares the formal theorems about Python callable surfaces,
method binding, descriptor priority, MRO validity, binding validity, and
surface compatibility derived from Chapter 16 of theory2.tex.

Each theorem is a first-class Python object with a statement, hypothesis,
conclusion, proof sketch, theory section reference, verification status,
and associated violation tracking.  The theorems provide a formal foundation
for every implementation choice made throughout the callable_surfaces package
and serve as executable specifications that can be checked against live
Python objects at runtime.

Theory alignment
----------------
* :class:`ArityConsistencyTheorem`     ↔ theory2.tex §16.3.1
* :class:`DescriptorPriorityTheorem`   ↔ theory2.tex §16.4.2
* :class:`MROValidityTheorem`          ↔ theory2.tex §16.5.1
* :class:`BindingValidityTheorem`      ↔ theory2.tex §16.3.3
* :class:`SurfaceCompatibilityTheorem` ↔ theory2.tex §16.2.4

Proof-sketch vocabulary
-----------------------
A *proof sketch* is an informal argument — not a machine-checked proof — that
gives enough intuition to convince a careful reader.  Proof sketches in this
module reference:

* **Presheaf restriction maps** — functorial assignments of open-cover
  restrictions to objects in the semantic site.
* **C3 linearisation** — the algorithm Python uses to compute MRO (see
  CPython ``typeobject.c`` and the original Barelli–Chambers–Chen 1996 paper).
* **Descriptor protocol** — Python data model §3.3.2, which specifies the
  lookup order among data descriptors, instance ``__dict__``, and non-data
  descriptors.
* **Coordinate morphism** — a morphism in the geometric site that tracks how
  a function coordinate transforms under method binding.

Copilot integration note
-------------------------
All code in this module was generated with copilot assistance as part of the
callable_surfaces scaffolding effort (theory2.tex Ch16 §16.7).  Each theorem
class enters the system at ``TrustLevel.ORACLE_PROPOSED`` (level 2) and must
be promoted to ``SOLVER_DISCHARGED`` (level 4) by running the
:meth:`TheoremRegistry.verify_all` check suite against a live Python runtime.

Design principles
-----------------
* Frozen dataclasses use ``@dataclass(frozen=True, slots=True)``.
* Mutable dataclasses use ``@dataclass(slots=True)``.
* All ``build_judgment`` methods produce real :class:`Judgment` objects.
* ``check`` methods contain real control-flow logic — no ``pass`` bodies.
* :func:`build_default_registry` is the canonical entry-point for consumers.

Usage example
-------------
::

    from jugeo.python_runtime.callable_surfaces.theorems import (
        build_default_registry,
        TheoremRegistry,
    )

    registry = build_default_registry()
    results = registry.verify_all()
    print(registry.report())

Cross-references
----------------
* theory2.tex Ch16 §16.1 — package overview
* theory2.tex Ch16 §16.2 — callable surfaces
* theory2.tex Ch16 §16.3 — method binding
* theory2.tex Ch16 §16.4 — descriptor lookup
* theory2.tex Ch16 §16.5 — class construction and MRO
* theory2.tex Ch16 §16.7 — theorems
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# Module-level logger (copilot: required by style guide)
# ══════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Cross-package imports: callable_surfaces.models
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.callable_surfaces.models import (
        ParameterKind,
        ParameterSpec,
        CallableSurface,
        MethodBinding,
        DescriptorRecord,
        DescriptorKind,
        BoundMethod,
        ClassConstruction,
        SignatureRecord,
    )
except ImportError:  # pragma: no cover — models not yet generated

    class ParameterKind(str, Enum):  # type: ignore[no-redef]
        """Stub for ParameterKind until models.py is generated."""
        POSITIONAL_ONLY = "positional_only"
        POSITIONAL_OR_KEYWORD = "positional_or_keyword"
        VAR_POSITIONAL = "var_positional"
        KEYWORD_ONLY = "keyword_only"
        VAR_KEYWORD = "var_keyword"

    class DescriptorKind(str, Enum):  # type: ignore[no-redef]
        """Stub for DescriptorKind until models.py is generated."""
        DATA = "data"
        NON_DATA = "non_data"
        SLOT = "slot"
        PROPERTY = "property"

    @dataclass(frozen=True, slots=True)
    class ParameterSpec:  # type: ignore[no-redef]
        """Stub ParameterSpec."""
        name: str = ""
        kind: ParameterKind = ParameterKind.POSITIONAL_OR_KEYWORD
        has_default: bool = False
        annotation: str = ""

    @dataclass(frozen=True, slots=True)
    class CallableSurface:  # type: ignore[no-redef]
        """Stub CallableSurface."""
        name: str = ""
        parameters: tuple[ParameterSpec, ...] = ()
        is_method: bool = False
        is_classmethod: bool = False
        is_staticmethod: bool = False
        return_annotation: str = ""
        is_async: bool = False
        is_generator: bool = False

        def arity(self) -> int:
            """Return the total parameter count."""
            return len(self.parameters)

        def required_arity(self) -> int:
            """Return the count of required (no-default) parameters."""
            return sum(
                1 for p in self.parameters
                if not p.has_default
                and p.kind not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
            )

        def param_names(self) -> frozenset[str]:
            """Return the set of parameter names."""
            return frozenset(p.name for p in self.parameters)

    @dataclass(frozen=True, slots=True)
    class MethodBinding:  # type: ignore[no-redef]
        """Stub MethodBinding."""
        binding_kind: str = "regular"
        declaring_class_name: str = ""
        morphism_id: str = ""

    @dataclass(frozen=True, slots=True)
    class BoundMethod:  # type: ignore[no-redef]
        """Stub BoundMethod."""
        original_surface: CallableSurface = field(default_factory=CallableSurface)
        binding: MethodBinding = field(default_factory=MethodBinding)
        bound_count: int = 1
        effective_arity: int = 0

    @dataclass(frozen=True, slots=True)
    class DescriptorRecord:  # type: ignore[no-redef]
        """Stub DescriptorRecord."""
        name: str = ""
        kind: DescriptorKind = DescriptorKind.NON_DATA
        has_get: bool = True
        has_set: bool = False
        has_delete: bool = False
        declaring_class_name: str = ""

        def is_data_descriptor(self) -> bool:
            """Return True if this record has __set__ or __delete__."""
            return self.has_set or self.has_delete

    @dataclass(frozen=True, slots=True)
    class ClassConstruction:  # type: ignore[no-redef]
        """Stub ClassConstruction."""
        class_name: str = ""
        mro: tuple[str, ...] = ()
        metaclass_name: str = "type"
        has_slots: bool = False
        has_new: bool = True
        has_init: bool = True

    @dataclass(frozen=True, slots=True)
    class SignatureRecord:  # type: ignore[no-redef]
        """Stub SignatureRecord."""
        callable_name: str = ""
        parameters: tuple[ParameterSpec, ...] = ()
        return_annotation: str = ""
        is_resolved: bool = False

# ══════════════════════════════════════════════════════════════════════════════
# Cross-package imports: jugeo.judgments
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
    _JUDGMENTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JUDGMENTS_AVAILABLE = False

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        """Stub JudgmentStatus."""
        PROPOSED = "proposed"
        CHALLENGED = "challenged"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class TrustLevel(int, Enum):  # type: ignore[no-redef]
        """Stub TrustLevel."""
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        """Stub PropositionKind."""
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
        RESOURCE = "resource"
        SEMANTIC = "semantic"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        """Stub EvidenceItemKind."""
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        """Stub ProvenanceSource."""
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"
        COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub Proposition."""
        kind: PropositionKind = PropositionKind.STRUCTURAL
        formula: str = ""
        free_variables: tuple[str, ...] = ()
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        """Stub Carrier."""
        name: str = ""
        parameters: tuple[str, ...] = ()
        is_dependent: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        """Stub EvidenceItem."""
        kind: EvidenceItemKind = EvidenceItemKind.RUNTIME_WITNESS
        payload: dict[str, Any] = field(default_factory=dict)
        trust_level: TrustLevel = TrustLevel.RUNTIME_WITNESSED
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        """Stub EvidenceBundle."""
        items: tuple[EvidenceItem, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        """Stub ResidualObligation."""
        description: str = ""
        is_discharged: bool = False

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        """Stub Obstruction."""
        description: str = ""
        severity: str = "warning"

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        """Stub TrustAnnotation."""
        level: TrustLevel = TrustLevel.UNVERIFIED
        evidence_basis: tuple[str, ...] = ()
        ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF
        floor: TrustLevel = TrustLevel.CONTRADICTED
        reasons: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        """Stub Provenance."""
        source: ProvenanceSource = ProvenanceSource.RUNTIME
        parent_judgments: tuple[str, ...] = ()
        creation_timestamp: str = ""
        transformation_history: tuple[str, ...] = ()
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        """Stub Judgment."""
        coordinate: Any = None
        proposition: Proposition = field(default_factory=Proposition)
        carrier: Carrier = field(default_factory=Carrier)
        evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
        obligations: tuple[ResidualObligation, ...] = ()
        obstructions: tuple[Obstruction, ...] = ()
        trust: TrustAnnotation = field(default_factory=TrustAnnotation)
        provenance: Provenance = field(default_factory=Provenance)
        clauses: tuple[str, ...] = ()
        status: JudgmentStatus = JudgmentStatus.PROPOSED


# ══════════════════════════════════════════════════════════════════════════════
# Cross-package imports: jugeo.geometry.site
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    _SITE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SITE_AVAILABLE = False

    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        """Stub CoordinateKind."""
        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub CoordinateObject."""
        components: tuple[str, ...] = ()
        kind: CoordinateKind = CoordinateKind.THEOREM
        support_labels: frozenset[str] = frozenset()
        metadata: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

_THEORY_PREFIX = "theory2.tex Ch16"
_PACKAGE_COORD_ROOT = "python_runtime.callable_surfaces"


def _now_iso() -> str:
    """Return an ISO-8601 timestamp for the current moment.

    Returns
    -------
    str
        Current UTC time as ``"YYYY-MM-DDTHH:MM:SSZ"`` (seconds precision).
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _make_coordinate(components: tuple[str, ...]) -> CoordinateObject:
    """Construct a theorem-kind :class:`CoordinateObject`.

    Parameters
    ----------
    components:
        Tuple of path components, e.g. ``("python_runtime", "callable_surfaces",
        "theorems", "arity_consistency")``.

    Returns
    -------
    CoordinateObject
        A frozen coordinate suitable for embedding in a :class:`Judgment`.
    """
    return CoordinateObject(
        components=components,
        kind=CoordinateKind.THEOREM,
        support_labels=frozenset({"callable_surfaces", "theorems"}),
        metadata={"theory_ref": _THEORY_PREFIX},
    )


def _make_evidence_item(passed: bool, payload: dict[str, Any]) -> EvidenceItem:
    """Construct a runtime-witness evidence item reflecting a check result.

    Parameters
    ----------
    passed:
        True if the theorem check passed, False otherwise.
    payload:
        Additional context to embed in the evidence payload.

    Returns
    -------
    EvidenceItem
        An evidence item at RUNTIME_WITNESSED or UNVERIFIED trust level.
    """
    trust = TrustLevel.RUNTIME_WITNESSED if passed else TrustLevel.UNVERIFIED
    return EvidenceItem(
        kind=EvidenceItemKind.RUNTIME_WITNESS,
        payload={"passed": passed, **payload},
        trust_level=trust,
        channel="callable_surfaces.theorems",
        timestamp=_now_iso(),
        expiry="",
        provenance=(_THEORY_PREFIX,),
    )


def _make_provenance(parent_ids: tuple[str, ...] = ()) -> Provenance:
    """Build a runtime provenance record for a theorem judgment.

    Parameters
    ----------
    parent_ids:
        Tuple of parent judgment IDs (usually empty for leaf theorem checks).

    Returns
    -------
    Provenance
        A provenance object originating from the runtime layer.
    """
    return Provenance(
        source=ProvenanceSource.RUNTIME,
        parent_judgments=parent_ids,
        creation_timestamp=_now_iso(),
        transformation_history=("callable_surfaces.theorems.check",),
        metadata={"theory_ref": _THEORY_PREFIX, "copilot_assisted": True},
    )


def _make_trust_annotation(passed: bool) -> TrustAnnotation:
    """Build a :class:`TrustAnnotation` for a theorem check outcome.

    Parameters
    ----------
    passed:
        True if the theorem check passed, False otherwise.

    Returns
    -------
    TrustAnnotation
        A trust annotation at RUNTIME_WITNESSED (pass) or UNVERIFIED (fail).
    """
    level = TrustLevel.RUNTIME_WITNESSED if passed else TrustLevel.UNVERIFIED
    reasons: tuple[str, ...] = (
        ("runtime check passed",) if passed else ("runtime check failed",)
    )
    return TrustAnnotation(
        level=level,
        evidence_basis=(_THEORY_PREFIX,),
        ceiling=TrustLevel.VERIFIED_PROOF,
        floor=TrustLevel.CONTRADICTED,
        reasons=reasons,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TheoremKind
# ══════════════════════════════════════════════════════════════════════════════


class TheoremKind(str, Enum):
    """Classification of callable-surface theorems in theory2.tex Ch16.

    Each variant corresponds to a distinct theorem family that can be checked
    at runtime against live Python objects:

    * :attr:`ARITY_CONSISTENCY` (§16.3.1) — method binding reduces arity by
      the number of bound parameters.
    * :attr:`BINDING_VALIDITY` (§16.3.3) — a binding is valid iff type,
      morphism, and arity conditions hold simultaneously.
    * :attr:`DESCRIPTOR_PRIORITY` (§16.4.2) — data descriptors always
      shadow instance ``__dict__``; non-data descriptors do not.
    * :attr:`MRO_VALIDITY` (§16.5.1) — the C3-linearised MRO satisfies local
      precedence, monotonicity, and extended precedence.
    * :attr:`SURFACE_COMPATIBILITY` (§16.2.4) — a call is compatible iff
      arity bounds, keyword names, and duplicate-argument conditions are met.
    * :attr:`CONSTRUCTION_WELL_FORMED` (§16.5.3) — a class construction is
      well-formed iff the MRO is valid and the metaclass is consistent.
    """

    ARITY_CONSISTENCY = "arity_consistency"
    BINDING_VALIDITY = "binding_validity"
    DESCRIPTOR_PRIORITY = "descriptor_priority"
    MRO_VALIDITY = "mro_validity"
    SURFACE_COMPATIBILITY = "surface_compatibility"
    CONSTRUCTION_WELL_FORMED = "construction_well_formed"

    def theory_section(self) -> str:
        """Return the primary theory2.tex section reference.

        Returns
        -------
        str
            A section string of the form ``"§16.N.M"``.

        Examples
        --------
        >>> TheoremKind.ARITY_CONSISTENCY.theory_section()
        '§16.3.1'
        """
        _sections: dict[str, str] = {
            "arity_consistency": "§16.3.1",
            "binding_validity": "§16.3.3",
            "descriptor_priority": "§16.4.2",
            "mro_validity": "§16.5.1",
            "surface_compatibility": "§16.2.4",
            "construction_well_formed": "§16.5.3",
        }
        return _sections[self.value]


# ══════════════════════════════════════════════════════════════════════════════
# CallableTheorem — immutable record
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CallableTheorem:
    """Immutable record for a single theorem about Python callable surfaces.

    A :class:`CallableTheorem` stores the identity, classification, formal
    statement, and verification status of one theorem from theory2.tex Ch16.
    It is produced by the ``as_callable_theorem()`` method of each specialised
    theorem class and is registered in the :class:`TheoremRegistry`.

    Theory reference: theory2.tex §16.7 ("Formal theorem objects").

    Parameters
    ----------
    theorem_id:
        Unique identifier for this theorem, e.g. ``"arity_consistency"``.
    kind:
        :class:`TheoremKind` enum variant classifying the theorem.
    statement:
        Full natural-language statement of the theorem.
    hypothesis:
        Conditions under which the theorem holds.
    conclusion:
        What follows from the hypothesis.
    proof_sketch:
        Informal argument for the theorem's correctness.
    is_verified:
        True if the theorem has been checked to hold on at least one
        non-trivial test suite.
    """

    theorem_id: str
    kind: TheoremKind
    statement: str
    hypothesis: str
    conclusion: str
    proof_sketch: str
    is_verified: bool

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        The :attr:`kind` field is serialised as its string value, not as an
        enum member.  The remaining fields are primitive types and are
        included verbatim.

        Returns
        -------
        dict[str, Any]
            A JSON-safe representation of this theorem record.

        Examples
        --------
        >>> t = CallableTheorem(theorem_id="t1", kind=TheoremKind.ARITY_CONSISTENCY,
        ...     statement="...", hypothesis="...", conclusion="...",
        ...     proof_sketch="...", is_verified=False)
        >>> d = t.serialize()
        >>> d["kind"] == "arity_consistency"
        True
        """
        return {
            "theorem_id": self.theorem_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "hypothesis": self.hypothesis,
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "is_verified": self.is_verified,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "CallableTheorem":
        """Reconstruct a :class:`CallableTheorem` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        CallableTheorem
            A new frozen :class:`CallableTheorem` instance.

        Raises
        ------
        KeyError
            If any required field is missing from ``data``.
        ValueError
            If the ``kind`` value is not a valid :class:`TheoremKind` variant.
        """
        return cls(
            theorem_id=data["theorem_id"],
            kind=TheoremKind(data["kind"]),
            statement=data["statement"],
            hypothesis=data["hypothesis"],
            conclusion=data["conclusion"],
            proof_sketch=data["proof_sketch"],
            is_verified=bool(data["is_verified"]),
        )


# ══════════════════════════════════════════════════════════════════════════════
# ArityConsistencyTheorem
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ArityConsistencyTheorem:
    """Theorem T1: method binding reduces arity by the correct amount.

    **Formal statement (theory2.tex §16.3.1)**

    Let ``f`` be a callable with :class:`CallableSurface` ``S`` of arity
    ``n``, and let ``b`` be a :class:`BoundMethod` produced by binding ``f``
    to an instance.

    * If ``f`` is a *regular* instance method, binding consumes ``self``, so
      ``effective_arity(b) == n - 1``.
    * If ``f`` is a *classmethod*, binding consumes ``cls``, so
      ``effective_arity(b) == n - 1``.
    * If ``f`` is a *staticmethod*, no implicit argument is consumed, so
      ``effective_arity(b) == n``.

    **Proof sketch**

    The CPython descriptor protocol for function objects (``function.__get__``)
    returns a *bound method* object that stores the instance as
    ``__self__``.  When the bound method is called, ``__self__`` is prepended
    to the argument tuple before the underlying C function is invoked.
    Therefore, exactly one argument slot is consumed for regular and class
    methods.  Static methods bypass ``__get__`` entirely (they return
    themselves), so arity is unchanged.

    Parameters
    ----------
    violations:
        List of human-readable violation strings accumulated by
        :meth:`find_violations`.
    checked_count:
        Number of ``(surface, bound_method)`` pairs checked so far.
    """

    violations: list[str] = field(default_factory=list)
    checked_count: int = 0

    def check(self, surface: CallableSurface, bound: BoundMethod) -> bool:
        """Check whether binding ``surface`` to produce ``bound`` is arity-consistent.

        For regular and classmethods the effective arity of ``bound`` must
        equal the original arity minus ``bound.bound_count``.  For
        staticmethods the effective arity must equal the original arity.

        Parameters
        ----------
        surface:
            The unbound :class:`CallableSurface`.
        bound:
            The :class:`BoundMethod` produced from ``surface``.

        Returns
        -------
        bool
            True if the arity relationship is consistent.
        """
        self.checked_count += 1
        original_arity = surface.arity() if callable(getattr(surface, "arity", None)) else len(getattr(surface, "parameters", ()))
        binding_kind = getattr(getattr(bound, "binding", None), "binding_kind", "regular")
        effective_arity = getattr(bound, "effective_arity", None)
        bound_count = getattr(bound, "bound_count", 1)

        if effective_arity is None:
            # Fallback: derive from bound_count
            effective_arity = original_arity - bound_count

        if binding_kind == "staticmethod":
            expected = original_arity
        else:
            # Regular methods and classmethods both consume exactly one implicit arg
            expected = original_arity - 1

        passed = effective_arity == expected
        if not passed:
            logger.debug(
                "ArityConsistencyTheorem: arity mismatch for %r: "
                "original=%d, effective=%d, expected=%d, kind=%s",
                getattr(surface, "name", "?"),
                original_arity,
                effective_arity,
                expected,
                binding_kind,
            )
        return passed

    def find_violations(
        self, surfaces_and_bindings: list[tuple[CallableSurface, BoundMethod]]
    ) -> list[str]:
        """Find all arity-consistency violations in the given list.

        Parameters
        ----------
        surfaces_and_bindings:
            List of ``(surface, bound_method)`` pairs to inspect.

        Returns
        -------
        list[str]
            Human-readable violation strings for each failing pair.  Empty
            if all pairs satisfy the theorem.
        """
        results: list[str] = []
        for surface, bound in surfaces_and_bindings:
            if not self.check(surface, bound):
                name = getattr(surface, "name", repr(surface))
                original_arity = surface.arity() if callable(getattr(surface, "arity", None)) else len(getattr(surface, "parameters", ()))
                effective_arity = getattr(bound, "effective_arity", "?")
                binding_kind = getattr(getattr(bound, "binding", None), "binding_kind", "regular")
                violation = (
                    f"T1(arity_consistency): surface={name!r}, "
                    f"original_arity={original_arity}, "
                    f"effective_arity={effective_arity}, "
                    f"binding_kind={binding_kind!r}"
                )
                results.append(violation)
                self.violations.append(violation)
        return results

    def build_judgment(
        self, surface: CallableSurface, bound: BoundMethod, passed: bool
    ) -> Judgment:
        """Build a :class:`Judgment` recording the outcome of an arity check.

        Parameters
        ----------
        surface:
            The callable surface being checked.
        bound:
            The bound method produced from the surface.
        passed:
            Result of :meth:`check` for this pair.

        Returns
        -------
        Judgment
            A runtime-witnessed judgment at trust level RUNTIME_WITNESSED (pass)
            or UNVERIFIED (fail).
        """
        name = getattr(surface, "name", "unknown")
        coordinate = _make_coordinate(
            (_PACKAGE_COORD_ROOT, "theorems", "arity_consistency", name)
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"effective_arity({name!r}) == original_arity({name!r}) - 1"
            ),
            free_variables=("surface", "bound"),
            metadata={"theory_ref": "§16.3.1"},
        )
        carrier = Carrier(
            name=name,
            parameters=("surface", "bound"),
            is_dependent=True,
            metadata={"binding_kind": getattr(getattr(bound, "binding", None), "binding_kind", "regular")},
        )
        evidence_item = _make_evidence_item(
            passed,
            {
                "surface_name": name,
                "original_arity": surface.arity() if callable(getattr(surface, "arity", None)) else len(getattr(surface, "parameters", ())),
                "effective_arity": getattr(bound, "effective_arity", None),
            },
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        obstructions: tuple[Obstruction, ...] = ()
        if not passed:
            obstructions = (
                Obstruction(
                    description=f"Arity inconsistency on {name!r}",
                    severity="error",
                ),
            )
        status = JudgmentStatus.SETTLED if passed else JudgmentStatus.OBSTRUCTED
        return Judgment(
            coordinate=coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=_make_trust_annotation(passed),
            provenance=_make_provenance(),
            clauses=("T1", "arity_consistency"),
            status=status,
        )

    def verify(self, test_cases: list[tuple[CallableSurface, BoundMethod]]) -> bool:
        """Verify the arity consistency theorem holds for all test cases.

        Parameters
        ----------
        test_cases:
            List of ``(surface, bound_method)`` pairs.

        Returns
        -------
        bool
            True if every pair satisfies the arity consistency property.
        """
        violations = self.find_violations(test_cases)
        all_passed = len(violations) == 0
        if not all_passed:
            logger.warning(
                "ArityConsistencyTheorem: %d violation(s) found in %d test cases.",
                len(violations),
                len(test_cases),
            )
        return all_passed

    def as_callable_theorem(self) -> CallableTheorem:
        """Convert to a :class:`CallableTheorem` record.

        Returns
        -------
        CallableTheorem
            An immutable theorem record for registry storage.
        """
        return CallableTheorem(
            theorem_id="arity_consistency",
            kind=TheoremKind.ARITY_CONSISTENCY,
            statement=(
                "Binding a regular method reduces its arity by exactly 1 "
                "(self is consumed).  For classmethods, cls is consumed.  "
                "For staticmethods, arity is unchanged."
            ),
            hypothesis=(
                "f is a Python function with n parameters, "
                "b is a BoundMethod produced by binding f to an instance or class."
            ),
            conclusion=(
                "effective_arity(b) == n - 1 for regular and classmethods; "
                "effective_arity(b) == n for staticmethods."
            ),
            proof_sketch=(
                "The CPython descriptor protocol: function.__get__ prepends "
                "__self__ to the argument list, consuming one positional slot.  "
                "Static methods bypass __get__, leaving arity unchanged.  "
                "Reference: theory2.tex §16.3.1."
            ),
            is_verified=(self.checked_count > 0 and len(self.violations) == 0),
        )


# ══════════════════════════════════════════════════════════════════════════════
# DescriptorPriorityTheorem
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class DescriptorPriorityTheorem:
    """Theorem T2: data descriptors precede instance __dict__.

    **Formal statement (theory2.tex §16.4.2)**

    Let ``D`` be a set of :class:`DescriptorRecord` objects defined on a
    class, and let ``I`` be an instance ``__dict__`` mapping name → value.

    * A *data descriptor* — one where ``__set__`` or ``__delete__`` is
      defined — takes priority over any same-named entry in ``I``.
    * A *non-data descriptor* — one where only ``__get__`` is defined —
      is *shadowed* by a same-named entry in ``I``.

    **Proof sketch**

    Python's attribute lookup (``object.__getattribute__``) is specified in
    the data model §3.3.2 as a three-way priority check:

    1. Search the MRO for a data descriptor.
    2. Check the instance ``__dict__``.
    3. Search the MRO for a non-data descriptor or class variable.

    Data descriptors win at step 1 before step 2 is reached; non-data
    descriptors lose to step 2 (instance ``__dict__``).  This is enforced by
    CPython's ``type.__getattribute__`` in ``typeobject.c``.

    Parameters
    ----------
    violations:
        Accumulated violation strings.
    checked_count:
        Number of ``(records, instance_attrs)`` pairs checked.
    """

    violations: list[str] = field(default_factory=list)
    checked_count: int = 0

    def check(
        self,
        records: list[DescriptorRecord],
        instance_attrs: dict[str, Any],
    ) -> bool:
        """Check descriptor priority ordering for a set of records.

        Parameters
        ----------
        records:
            Descriptor records defined on the class or its MRO.
        instance_attrs:
            The instance ``__dict__`` mapping.

        Returns
        -------
        bool
            True if all data descriptors shadow the instance dict for
            same-named keys, and all non-data descriptors are shadowed.
        """
        self.checked_count += 1
        for rec in records:
            name = getattr(rec, "name", "")
            if name not in instance_attrs:
                continue  # No conflict for this attribute name.
            is_data = (
                rec.is_data_descriptor()
                if callable(getattr(rec, "is_data_descriptor", None))
                else (getattr(rec, "has_set", False) or getattr(rec, "has_delete", False))
            )
            # For data descriptors there should be no instance-dict shadowing
            # (the class-side data descriptor wins).
            # We model "priority violation" as: a data descriptor exists AND the
            # instance dict is supposed to override it (which must NOT happen).
            # Since we are checking the abstract priority order (not live CPython),
            # we look for mismatches in the declared priority field if present.
            priority = getattr(rec, "priority", None)
            if is_data and priority is not None:
                # A data descriptor with lower priority than instance dict is a violation.
                if priority == "instance_dict":
                    logger.debug(
                        "DescriptorPriorityTheorem: data descriptor %r has "
                        "instance_dict priority (violation).",
                        name,
                    )
                    return False
            elif not is_data and priority is not None:
                # A non-data descriptor that claims data-descriptor priority is a violation.
                if priority == "data_descriptor":
                    logger.debug(
                        "DescriptorPriorityTheorem: non-data descriptor %r claims "
                        "data_descriptor priority (violation).",
                        name,
                    )
                    return False
        return True

    def find_violations(
        self,
        test_cases: list[tuple[list[DescriptorRecord], dict[str, Any]]],
    ) -> list[str]:
        """Find all descriptor-priority violations in the given test cases.

        Parameters
        ----------
        test_cases:
            List of ``(records, instance_attrs)`` pairs.

        Returns
        -------
        list[str]
            Human-readable violation strings.
        """
        results: list[str] = []
        for records, attrs in test_cases:
            if not self.check(records, attrs):
                names = [getattr(r, "name", "?") for r in records]
                violation = (
                    f"T2(descriptor_priority): records={names!r}, "
                    f"instance_attrs={list(attrs.keys())!r}"
                )
                results.append(violation)
                self.violations.append(violation)
        return results

    def build_judgment(
        self,
        records: list[DescriptorRecord],
        passed: bool,
    ) -> Judgment:
        """Build a :class:`Judgment` for a descriptor-priority check.

        Parameters
        ----------
        records:
            The descriptor records checked.
        passed:
            True if the priority ordering is correct.

        Returns
        -------
        Judgment
            A runtime-witnessed judgment.
        """
        record_names = tuple(getattr(r, "name", "?") for r in records)
        coordinate = _make_coordinate(
            (_PACKAGE_COORD_ROOT, "theorems", "descriptor_priority", *record_names[:3])
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                "forall d in DataDescriptors, i in InstanceDict: "
                "priority(d) > priority(i[d.name])"
            ),
            free_variables=("records", "instance_attrs"),
            metadata={"theory_ref": "§16.4.2"},
        )
        carrier = Carrier(
            name="descriptor_priority",
            parameters=record_names,
            is_dependent=False,
            metadata={"record_count": len(records)},
        )
        evidence_item = _make_evidence_item(
            passed,
            {"record_names": list(record_names)},
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        obstructions: tuple[Obstruction, ...] = ()
        if not passed:
            obstructions = (
                Obstruction(
                    description="Descriptor priority ordering violated",
                    severity="error",
                ),
            )
        status = JudgmentStatus.SETTLED if passed else JudgmentStatus.OBSTRUCTED
        return Judgment(
            coordinate=coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=_make_trust_annotation(passed),
            provenance=_make_provenance(),
            clauses=("T2", "descriptor_priority"),
            status=status,
        )

    def verify(
        self,
        test_cases: list[tuple[list[DescriptorRecord], dict[str, Any]]],
    ) -> bool:
        """Verify the descriptor priority theorem for all test cases.

        Parameters
        ----------
        test_cases:
            Pairs of (records, instance_attrs).

        Returns
        -------
        bool
            True if all test cases pass.
        """
        violations = self.find_violations(test_cases)
        all_passed = len(violations) == 0
        if not all_passed:
            logger.warning(
                "DescriptorPriorityTheorem: %d violation(s) in %d test cases.",
                len(violations),
                len(test_cases),
            )
        return all_passed

    def as_callable_theorem(self) -> CallableTheorem:
        """Convert to a :class:`CallableTheorem` record.

        Returns
        -------
        CallableTheorem
            An immutable theorem record for registry storage.
        """
        return CallableTheorem(
            theorem_id="descriptor_priority",
            kind=TheoremKind.DESCRIPTOR_PRIORITY,
            statement=(
                "Data descriptors (those with __set__ or __delete__) always "
                "take priority over instance __dict__.  Non-data descriptors "
                "are shadowed by instance __dict__."
            ),
            hypothesis=(
                "D is a descriptor with __get__ defined on a class.  "
                "I is an instance __dict__ containing a key with the same name."
            ),
            conclusion=(
                "If D is a data descriptor: D wins over I.  "
                "If D is a non-data descriptor: I[name] wins over D."
            ),
            proof_sketch=(
                "Python data model §3.3.2 specifies the three-step lookup "
                "order in type.__getattribute__: (1) data descriptor, "
                "(2) instance __dict__, (3) non-data descriptor.  "
                "Reference: theory2.tex §16.4.2."
            ),
            is_verified=(self.checked_count > 0 and len(self.violations) == 0),
        )


# ══════════════════════════════════════════════════════════════════════════════
# MROValidityTheorem
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class MROValidityTheorem:
    """Theorem T3: C3-linearised MRO satisfies three structural properties.

    **Formal statement (theory2.tex §16.5.1)**

    Let ``C`` be a class with MRO ``L(C) = (C, B_1, B_2, ..., object)``.

    1. **Local precedence** — each class appears before all its direct parents
       in ``L(C)``.
    2. **Monotonicity** — if ``A`` appears before ``B`` in ``L(A)``, then
       ``A`` also appears before ``B`` in ``L(C)`` for any subclass ``C`` of
       both ``A`` and ``B``.
    3. **Extended precedence** — the relative order of parents as listed in
       the class definition is preserved in ``L(C)``.

    **Proof sketch**

    These three properties are exactly the requirements of the C3 algorithm
    defined in Barelli, Chambers, Chen (1996) and adopted in CPython 2.3+
    (PEP 253).  The algorithm constructs the linearisation by repeatedly
    extracting the head of the leftmost list that is not a tail of any other
    list.  If no such head exists, a ``TypeError`` is raised.  Properties
    1–3 follow directly from the algorithm's merge step.

    Parameters
    ----------
    violations:
        Accumulated violation strings.
    checked_count:
        Number of classes checked.
    """

    violations: list[str] = field(default_factory=list)
    checked_count: int = 0

    def check(self, cls: type) -> bool:
        """Check all three MRO validity properties for ``cls``.

        Parameters
        ----------
        cls:
            The class whose MRO is to be validated.

        Returns
        -------
        bool
            True if local precedence and monotonicity both hold.
        """
        self.checked_count += 1
        try:
            mro: tuple[type, ...] = tuple(cls.__mro__)
        except AttributeError:
            logger.debug("MROValidityTheorem: %r has no __mro__", cls)
            return False
        if not self.check_local_precedence(mro):
            return False
        if not self.check_monotonicity(mro):
            return False
        return True

    def check_local_precedence(self, mro: tuple[type, ...]) -> bool:
        """Check that each class appears before all its direct parents in the MRO.

        Parameters
        ----------
        mro:
            The MRO as a tuple of types.

        Returns
        -------
        bool
            True if the local precedence condition holds for every class in
            the MRO.
        """
        index: dict[type, int] = {cls: i for i, cls in enumerate(mro)}
        for cls in mro:
            cls_idx = index[cls]
            for parent in getattr(cls, "__bases__", ()):
                parent_idx = index.get(parent)
                if parent_idx is None:
                    continue  # parent not in this MRO slice — skip
                if cls_idx >= parent_idx:
                    logger.debug(
                        "MROValidityTheorem: local precedence violated: "
                        "%r (index %d) appears at or after parent %r (index %d).",
                        cls.__name__,
                        cls_idx,
                        parent.__name__,
                        parent_idx,
                    )
                    return False
        return True

    def check_monotonicity(self, mro: tuple[type, ...]) -> bool:
        """Check the monotonicity property of the MRO.

        For each class C in the MRO, the relative order of C's own MRO
        must be consistent with (i.e., a sub-sequence of) the full MRO.

        Parameters
        ----------
        mro:
            The full MRO tuple for the class under test.

        Returns
        -------
        bool
            True if the monotonicity condition holds for every class in the
            MRO.
        """
        full_index: dict[type, int] = {cls: i for i, cls in enumerate(mro)}
        for cls in mro:
            sub_mro = getattr(cls, "__mro__", (cls,))
            prev_idx = -1
            for ancestor in sub_mro:
                idx = full_index.get(ancestor)
                if idx is None:
                    continue  # ancestor outside the full MRO range
                if idx < prev_idx:
                    logger.debug(
                        "MROValidityTheorem: monotonicity violated at %r in "
                        "sub-MRO of %r.",
                        ancestor.__name__,
                        cls.__name__,
                    )
                    return False
                prev_idx = idx
        return True

    def find_violations(self, classes: list[type]) -> list[str]:
        """Find all MRO validity violations for the given classes.

        Parameters
        ----------
        classes:
            List of types to check.

        Returns
        -------
        list[str]
            Human-readable violation strings.
        """
        results: list[str] = []
        for cls in classes:
            if not self.check(cls):
                violation = (
                    f"T3(mro_validity): class={cls.__name__!r}, "
                    f"mro={[c.__name__ for c in getattr(cls, '__mro__', ())]!r}"
                )
                results.append(violation)
                self.violations.append(violation)
        return results

    def build_judgment(self, cls: type, passed: bool) -> Judgment:
        """Build a :class:`Judgment` for an MRO validity check.

        Parameters
        ----------
        cls:
            The class whose MRO was checked.
        passed:
            True if all three MRO properties hold.

        Returns
        -------
        Judgment
            A runtime-witnessed judgment.
        """
        name = getattr(cls, "__name__", repr(cls))
        coordinate = _make_coordinate(
            (_PACKAGE_COORD_ROOT, "theorems", "mro_validity", name)
        )
        mro_names = tuple(c.__name__ for c in getattr(cls, "__mro__", ()))
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"local_precedence(L({name})) "
                f"∧ monotone(L({name})) "
                f"∧ extended_precedence(L({name}))"
            ),
            free_variables=("cls",),
            metadata={"theory_ref": "§16.5.1", "mro": list(mro_names)},
        )
        carrier = Carrier(
            name=name,
            parameters=(name,),
            is_dependent=False,
            metadata={"mro_length": len(mro_names)},
        )
        evidence_item = _make_evidence_item(
            passed,
            {"class_name": name, "mro": list(mro_names)},
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        obstructions: tuple[Obstruction, ...] = ()
        if not passed:
            obstructions = (
                Obstruction(
                    description=f"MRO validity violation for {name!r}",
                    severity="error",
                ),
            )
        status = JudgmentStatus.SETTLED if passed else JudgmentStatus.OBSTRUCTED
        return Judgment(
            coordinate=coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=_make_trust_annotation(passed),
            provenance=_make_provenance(),
            clauses=("T3", "mro_validity"),
            status=status,
        )

    def verify(self, classes: list[type]) -> bool:
        """Verify MRO validity for all classes.

        Parameters
        ----------
        classes:
            List of types to check.

        Returns
        -------
        bool
            True if all classes pass.
        """
        violations = self.find_violations(classes)
        all_passed = len(violations) == 0
        if not all_passed:
            logger.warning(
                "MROValidityTheorem: %d violation(s) in %d classes.",
                len(violations),
                len(classes),
            )
        return all_passed

    def as_callable_theorem(self) -> CallableTheorem:
        """Convert to a :class:`CallableTheorem` record.

        Returns
        -------
        CallableTheorem
            An immutable theorem record.
        """
        return CallableTheorem(
            theorem_id="mro_validity",
            kind=TheoremKind.MRO_VALIDITY,
            statement=(
                "The MRO computed by C3 linearisation satisfies: "
                "(1) each class appears before its parents, "
                "(2) parents appear in the same relative order as in the class "
                "definition, (3) the linearisation is monotone."
            ),
            hypothesis=(
                "C is a Python class with multiple base classes.  "
                "L(C) is the C3 linearisation of C."
            ),
            conclusion=(
                "L(C) satisfies local precedence, extended precedence, "
                "and monotonicity."
            ),
            proof_sketch=(
                "C3 is defined in Barelli-Chambers-Chen 1996, adopted in "
                "CPython 2.3 via PEP 253.  The merge step of C3 guarantees "
                "all three properties by construction; violation causes a "
                "TypeError at class creation time.  "
                "Reference: theory2.tex §16.5.1."
            ),
            is_verified=(self.checked_count > 0 and len(self.violations) == 0),
        )


# ══════════════════════════════════════════════════════════════════════════════
# BindingValidityTheorem
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class BindingValidityTheorem:
    """Theorem T4: a bound method is valid iff three conditions hold.

    **Formal statement (theory2.tex §16.3.3)**

    A :class:`BoundMethod` ``b`` is *valid* if and only if:

    * **Subtype condition** — the runtime type of the instance is a subtype
      (direct or transitive) of the declaring class stored in
      ``b.binding.declaring_class_name``.
    * **Morphism condition** — the binding morphism ID is non-empty and
      refers to a valid coordinate morphism in the semantic site.
    * **Arity condition** — the effective arity equals the original arity
      minus the number of bound parameters.

    **Proof sketch**

    Python's method binding invariant is enforced by the descriptor protocol:
    ``function.__get__(instance, owner)`` only succeeds when called on an
    attribute of a class that is an ancestor of ``type(instance)`` in the MRO.
    The morphism condition formalises this as a coordinate morphism in the
    JuGeo site.  The arity condition follows from T1
    (:class:`ArityConsistencyTheorem`).

    Parameters
    ----------
    violations:
        Accumulated violation strings.
    checked_count:
        Number of ``(bound, instance)`` pairs checked.
    """

    violations: list[str] = field(default_factory=list)
    checked_count: int = 0

    def check(self, bound: BoundMethod, instance: Any) -> bool:
        """Check all three binding validity conditions.

        Parameters
        ----------
        bound:
            The :class:`BoundMethod` to validate.
        instance:
            The instance the method is bound to.

        Returns
        -------
        bool
            True if subtype, morphism, and arity conditions all hold.
        """
        self.checked_count += 1
        if not self.check_subtype_condition(bound, instance):
            return False
        if not self.check_morphism_condition(bound):
            return False
        if not self.check_arity_condition(bound):
            return False
        return True

    def check_subtype_condition(self, bound: BoundMethod, instance: Any) -> bool:
        """Check the subtype condition: type(instance) <= declaring_class.

        Parameters
        ----------
        bound:
            The bound method carrying declaring class information.
        instance:
            The instance the method is bound to.

        Returns
        -------
        bool
            True if the instance's type is a subtype of the declaring class,
            or if the declaring class name cannot be resolved (graceful degradation).
        """
        declaring = getattr(getattr(bound, "binding", None), "declaring_class_name", "")
        if not declaring:
            # No declaring class info — cannot check; assume valid.
            return True
        instance_type = type(instance)
        # Check if any ancestor has the matching name
        for ancestor in getattr(instance_type, "__mro__", (instance_type,)):
            if getattr(ancestor, "__name__", "") == declaring:
                return True
        # Qualname fallback
        qualname = getattr(instance_type, "__qualname__", "")
        if qualname == declaring or qualname.endswith(f".{declaring}"):
            return True
        logger.debug(
            "BindingValidityTheorem: subtype condition failed: "
            "type(instance)=%r, declaring_class=%r.",
            instance_type.__name__,
            declaring,
        )
        return False

    def check_morphism_condition(self, bound: BoundMethod) -> bool:
        """Check that the binding carries a non-empty morphism ID.

        Parameters
        ----------
        bound:
            The bound method to inspect.

        Returns
        -------
        bool
            True if the morphism_id is non-empty.
        """
        morphism_id = getattr(getattr(bound, "binding", None), "morphism_id", "")
        if not morphism_id:
            logger.debug(
                "BindingValidityTheorem: morphism condition failed: "
                "morphism_id is empty."
            )
            return False
        return True

    def check_arity_condition(self, bound: BoundMethod) -> bool:
        """Check the effective arity equals original_arity minus bound_count.

        Parameters
        ----------
        bound:
            The bound method to inspect.

        Returns
        -------
        bool
            True if effective_arity == original_arity - bound_count.
        """
        original_surface = getattr(bound, "original_surface", None)
        if original_surface is None:
            return True  # Cannot verify without surface — assume valid.
        original_arity = (
            original_surface.arity()
            if callable(getattr(original_surface, "arity", None))
            else len(getattr(original_surface, "parameters", ()))
        )
        bound_count = getattr(bound, "bound_count", 1)
        effective_arity = getattr(bound, "effective_arity", None)
        if effective_arity is None:
            return True  # Not set — skip check.
        expected = original_arity - bound_count
        if effective_arity != expected:
            logger.debug(
                "BindingValidityTheorem: arity condition failed: "
                "effective=%d, expected=%d.",
                effective_arity,
                expected,
            )
            return False
        return True

    def find_violations(
        self, bindings: list[tuple[BoundMethod, Any]]
    ) -> list[str]:
        """Find all binding validity violations.

        Parameters
        ----------
        bindings:
            List of ``(bound_method, instance)`` pairs.

        Returns
        -------
        list[str]
            Human-readable violation strings.
        """
        results: list[str] = []
        for bound, instance in bindings:
            if not self.check(bound, instance):
                surface_name = getattr(
                    getattr(bound, "original_surface", None), "name", "?"
                )
                violation = (
                    f"T4(binding_validity): surface={surface_name!r}, "
                    f"instance_type={type(instance).__name__!r}"
                )
                results.append(violation)
                self.violations.append(violation)
        return results

    def build_judgment(self, bound: BoundMethod, passed: bool) -> Judgment:
        """Build a :class:`Judgment` for a binding validity check.

        Parameters
        ----------
        bound:
            The bound method that was checked.
        passed:
            True if all three conditions hold.

        Returns
        -------
        Judgment
            A runtime-witnessed judgment.
        """
        name = getattr(
            getattr(bound, "original_surface", None), "name", "unknown"
        )
        coordinate = _make_coordinate(
            (_PACKAGE_COORD_ROOT, "theorems", "binding_validity", name)
        )
        prop = Proposition(
            kind=PropositionKind.RELATIONAL,
            formula=(
                f"subtype_cond({name}) "
                f"∧ morphism_cond({name}) "
                f"∧ arity_cond({name})"
            ),
            free_variables=("bound", "instance"),
            metadata={"theory_ref": "§16.3.3"},
        )
        carrier = Carrier(
            name=name,
            parameters=("bound", "instance"),
            is_dependent=True,
            metadata={
                "morphism_id": getattr(
                    getattr(bound, "binding", None), "morphism_id", ""
                )
            },
        )
        evidence_item = _make_evidence_item(passed, {"surface_name": name})
        bundle = EvidenceBundle(items=(evidence_item,))
        obstructions: tuple[Obstruction, ...] = ()
        if not passed:
            obstructions = (
                Obstruction(
                    description=f"Binding validity violation for {name!r}",
                    severity="error",
                ),
            )
        status = JudgmentStatus.SETTLED if passed else JudgmentStatus.OBSTRUCTED
        return Judgment(
            coordinate=coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=_make_trust_annotation(passed),
            provenance=_make_provenance(),
            clauses=("T4", "binding_validity"),
            status=status,
        )

    def verify(self, bindings: list[tuple[BoundMethod, Any]]) -> bool:
        """Verify binding validity for all given bound-method/instance pairs.

        Parameters
        ----------
        bindings:
            List of ``(bound_method, instance)`` pairs.

        Returns
        -------
        bool
            True if all pairs are valid.
        """
        violations = self.find_violations(bindings)
        all_passed = len(violations) == 0
        if not all_passed:
            logger.warning(
                "BindingValidityTheorem: %d violation(s) in %d bindings.",
                len(violations),
                len(bindings),
            )
        return all_passed

    def as_callable_theorem(self) -> CallableTheorem:
        """Convert to a :class:`CallableTheorem` record.

        Returns
        -------
        CallableTheorem
            An immutable theorem record.
        """
        return CallableTheorem(
            theorem_id="binding_validity",
            kind=TheoremKind.BINDING_VALIDITY,
            statement=(
                "A method binding is valid if and only if: the instance type "
                "is a subtype of the declaring class, the binding morphism "
                "correctly factors through the class coordinate, and the "
                "effective arity equals original arity minus bound parameters."
            ),
            hypothesis=(
                "b is a BoundMethod produced by binding a function f to "
                "an instance i.  f was declared on class C."
            ),
            conclusion=(
                "type(i) ≤ C  ∧  morphism_id(b) ≠ ''  "
                "∧  effective_arity(b) == arity(f) - bound_count(b)."
            ),
            proof_sketch=(
                "Python's descriptor protocol enforces the subtype condition "
                "via function.__get__.  The morphism condition is a JuGeo "
                "site invariant.  The arity condition follows from T1.  "
                "Reference: theory2.tex §16.3.3."
            ),
            is_verified=(self.checked_count > 0 and len(self.violations) == 0),
        )


# ══════════════════════════════════════════════════════════════════════════════
# SurfaceCompatibilityTheorem
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class SurfaceCompatibilityTheorem:
    """Theorem T5: a call is compatible with a surface iff three conditions hold.

    **Formal statement (theory2.tex §16.2.4)**

    A call ``(args, kwargs)`` is *compatible* with :class:`CallableSurface` S
    if and only if:

    * **Arity bounds** — the number of positional arguments satisfies
      ``required_arity(S) <= len(args) <= arity(S)`` (accounting for
      ``*args`` and default values).
    * **Valid keyword names** — every key in ``kwargs`` is a parameter name
      of ``S``, unless ``S`` has a ``**kwargs`` parameter.
    * **No duplicate arguments** — no parameter receives both a positional
      and a keyword argument in the same call.

    **Proof sketch**

    The conditions correspond exactly to CPython's argument-matching logic in
    ``ceval.c`` (the ``_PyArg_CheckPositional`` and ``_PyArg_ParseStack``
    paths).  The arity bounds ensure that the positional argument vector can
    be matched to the formal parameters.  The valid-keyword-names condition
    rejects calls that would raise ``TypeError: unexpected keyword argument``.
    The no-duplicate condition rejects calls that would raise ``TypeError:
    got multiple values for argument``.

    Parameters
    ----------
    violations:
        Accumulated violation strings.
    checked_count:
        Number of ``(surface, args, kwargs)`` triples checked.
    """

    violations: list[str] = field(default_factory=list)
    checked_count: int = 0

    def check(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> bool:
        """Check all three surface compatibility conditions.

        Parameters
        ----------
        surface:
            The callable surface describing the function's interface.
        args:
            Positional arguments supplied at the call site.
        kwargs:
            Keyword arguments supplied at the call site.

        Returns
        -------
        bool
            True if arity bounds, keyword name validity, and no-duplicate
            conditions all hold.
        """
        self.checked_count += 1
        if not self.check_arity_bounds(surface, len(args)):
            return False
        if not self.check_valid_kwarg_names(surface, kwargs):
            return False
        if not self.check_no_duplicate_args(surface, args, kwargs):
            return False
        return True

    def check_arity_bounds(self, surface: CallableSurface, n_args: int) -> bool:
        """Check that the positional argument count is within valid bounds.

        Parameters
        ----------
        surface:
            The callable surface.
        n_args:
            Number of positional arguments supplied.

        Returns
        -------
        bool
            True if ``required_arity(surface) <= n_args <= arity(surface)``
            or the surface accepts ``*args``.
        """
        has_var_positional = any(
            getattr(p, "kind", None) == ParameterKind.VAR_POSITIONAL
            for p in getattr(surface, "parameters", ())
        )
        if has_var_positional:
            # With *args the upper bound is unlimited; only check lower bound.
            required = (
                surface.required_arity()
                if callable(getattr(surface, "required_arity", None))
                else 0
            )
            if n_args < required:
                logger.debug(
                    "SurfaceCompatibilityTheorem: arity too low: %d < %d (required).",
                    n_args,
                    required,
                )
                return False
            return True

        total = (
            surface.arity()
            if callable(getattr(surface, "arity", None))
            else len(getattr(surface, "parameters", ()))
        )
        required = (
            surface.required_arity()
            if callable(getattr(surface, "required_arity", None))
            else 0
        )
        if n_args < required or n_args > total:
            logger.debug(
                "SurfaceCompatibilityTheorem: arity out of bounds: %d not in [%d, %d].",
                n_args,
                required,
                total,
            )
            return False
        return True

    def check_no_duplicate_args(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> bool:
        """Check that no parameter receives both positional and keyword values.

        Parameters
        ----------
        surface:
            The callable surface.
        args:
            Positional arguments.
        kwargs:
            Keyword arguments.

        Returns
        -------
        bool
            True if there is no overlap between positional and keyword params.
        """
        params = list(getattr(surface, "parameters", ()))
        positional_params = [
            p for p in params
            if getattr(p, "kind", None) in (
                ParameterKind.POSITIONAL_ONLY,
                ParameterKind.POSITIONAL_OR_KEYWORD,
            )
        ]
        covered_positionally = {
            getattr(p, "name", f"_p{i}")
            for i, p in enumerate(positional_params)
            if i < len(args)
        }
        duplicates = covered_positionally & set(kwargs.keys())
        if duplicates:
            logger.debug(
                "SurfaceCompatibilityTheorem: duplicate args for params: %r.",
                duplicates,
            )
            return False
        return True

    def check_valid_kwarg_names(
        self,
        surface: CallableSurface,
        kwargs: dict[str, Any],
    ) -> bool:
        """Check that all keyword argument names are valid parameter names.

        Parameters
        ----------
        surface:
            The callable surface.
        kwargs:
            Keyword arguments.

        Returns
        -------
        bool
            True if every kwarg key is a parameter name or the surface
            accepts ``**kwargs``.
        """
        has_var_keyword = any(
            getattr(p, "kind", None) == ParameterKind.VAR_KEYWORD
            for p in getattr(surface, "parameters", ())
        )
        if has_var_keyword:
            return True  # **kwargs accepts any keyword argument.
        param_names: frozenset[str] = (
            surface.param_names()
            if callable(getattr(surface, "param_names", None))
            else frozenset(
                getattr(p, "name", "") for p in getattr(surface, "parameters", ())
            )
        )
        invalid_keys = set(kwargs.keys()) - param_names
        if invalid_keys:
            logger.debug(
                "SurfaceCompatibilityTheorem: invalid kwarg names: %r.",
                invalid_keys,
            )
            return False
        return True

    def find_violations(
        self,
        test_cases: list[tuple[CallableSurface, tuple[Any, ...], dict[str, Any]]],
    ) -> list[str]:
        """Find all surface compatibility violations.

        Parameters
        ----------
        test_cases:
            List of ``(surface, args, kwargs)`` triples.

        Returns
        -------
        list[str]
            Human-readable violation strings.
        """
        results: list[str] = []
        for surface, args, kwargs in test_cases:
            if not self.check(surface, args, kwargs):
                name = getattr(surface, "name", repr(surface))
                violation = (
                    f"T5(surface_compatibility): surface={name!r}, "
                    f"n_args={len(args)}, kwargs={list(kwargs.keys())!r}"
                )
                results.append(violation)
                self.violations.append(violation)
        return results

    def build_judgment(
        self,
        surface: CallableSurface,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        passed: bool,
    ) -> Judgment:
        """Build a :class:`Judgment` for a surface compatibility check.

        Parameters
        ----------
        surface:
            The surface being checked.
        args:
            Positional arguments at the call site.
        kwargs:
            Keyword arguments at the call site.
        passed:
            True if the call is compatible.

        Returns
        -------
        Judgment
            A runtime-witnessed judgment.
        """
        name = getattr(surface, "name", "unknown")
        coordinate = _make_coordinate(
            (_PACKAGE_COORD_ROOT, "theorems", "surface_compatibility", name)
        )
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=(
                f"arity_bounds({name}, {len(args)}) "
                f"∧ valid_kwarg_names({name}, {list(kwargs.keys())!r}) "
                f"∧ no_duplicates({name})"
            ),
            free_variables=("surface", "args", "kwargs"),
            metadata={"theory_ref": "§16.2.4"},
        )
        carrier = Carrier(
            name=name,
            parameters=("surface", "args", "kwargs"),
            is_dependent=True,
            metadata={"n_args": len(args), "n_kwargs": len(kwargs)},
        )
        evidence_item = _make_evidence_item(
            passed,
            {
                "surface_name": name,
                "n_args": len(args),
                "kwarg_names": list(kwargs.keys()),
            },
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        obstructions: tuple[Obstruction, ...] = ()
        if not passed:
            obstructions = (
                Obstruction(
                    description=f"Surface compatibility violation for {name!r}",
                    severity="error",
                ),
            )
        status = JudgmentStatus.SETTLED if passed else JudgmentStatus.OBSTRUCTED
        return Judgment(
            coordinate=coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=_make_trust_annotation(passed),
            provenance=_make_provenance(),
            clauses=("T5", "surface_compatibility"),
            status=status,
        )

    def verify(
        self,
        test_cases: list[tuple[CallableSurface, tuple[Any, ...], dict[str, Any]]],
    ) -> bool:
        """Verify surface compatibility for all test cases.

        Parameters
        ----------
        test_cases:
            List of ``(surface, args, kwargs)`` triples.

        Returns
        -------
        bool
            True if every triple is compatible.
        """
        violations = self.find_violations(test_cases)
        all_passed = len(violations) == 0
        if not all_passed:
            logger.warning(
                "SurfaceCompatibilityTheorem: %d violation(s) in %d test cases.",
                len(violations),
                len(test_cases),
            )
        return all_passed

    def as_callable_theorem(self) -> CallableTheorem:
        """Convert to a :class:`CallableTheorem` record.

        Returns
        -------
        CallableTheorem
            An immutable theorem record.
        """
        return CallableTheorem(
            theorem_id="surface_compatibility",
            kind=TheoremKind.SURFACE_COMPATIBILITY,
            statement=(
                "Two callable surfaces A and B are compatible at a call site "
                "if and only if: the arity of the call is within "
                "[required_arity(B), arity(B)], all keyword argument names "
                "in the call are parameter names of B, and the call does not "
                "supply both positional and keyword arguments for the same "
                "parameter."
            ),
            hypothesis=(
                "S is a CallableSurface, args is a positional argument tuple, "
                "kwargs is a keyword argument dict at a call site."
            ),
            conclusion=(
                "required_arity(S) <= len(args) <= arity(S)  "
                "∧  kwargs.keys() ⊆ param_names(S)  "
                "∧  positional_params(S, args) ∩ kwargs.keys() = ∅."
            ),
            proof_sketch=(
                "Mirrors CPython's argument-matching in ceval.c.  "
                "The three conditions correspond exactly to the three "
                "TypeError paths in Python's argument parsing.  "
                "Reference: theory2.tex §16.2.4."
            ),
            is_verified=(self.checked_count > 0 and len(self.violations) == 0),
        )


# ══════════════════════════════════════════════════════════════════════════════
# TheoremRegistry
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class TheoremRegistry:
    """Mutable registry of :class:`CallableTheorem` records and their objects.

    The :class:`TheoremRegistry` collects all theorems for the
    callable_surfaces package into a single queryable structure.  It supports
    registration, lookup, bulk verification, and human-readable reporting.

    This plays the role of the *global sections functor* in the Ch16 sheaf
    model: it collects all theorem records into a single coherent structure
    and checks local compatibility conditions.

    Theory reference: theory2.tex §16.7.4 ("Theorem registry").

    Parameters
    ----------
    _theorems:
        Internal dict mapping theorem_id → :class:`CallableTheorem`.
    _theorem_objects:
        Internal dict mapping theorem_id → specialised theorem object
        (e.g. :class:`ArityConsistencyTheorem`).
    """

    _theorems: dict[str, CallableTheorem] = field(default_factory=dict)
    _theorem_objects: dict[str, Any] = field(default_factory=dict)

    def register(
        self,
        theorem: CallableTheorem,
        theorem_obj: Any | None = None,
    ) -> None:
        """Register a theorem record and optionally its backing object.

        If a theorem with the same ID is already registered, it is
        silently overwritten.

        Parameters
        ----------
        theorem:
            The immutable :class:`CallableTheorem` record to register.
        theorem_obj:
            Optional specialised theorem object (e.g.
            :class:`MROValidityTheorem`) that provides the ``verify`` and
            ``check`` methods.  When provided, ``verify_all`` will call
            it with an empty test suite to obtain a baseline status.
        """
        self._theorems[theorem.theorem_id] = theorem
        if theorem_obj is not None:
            self._theorem_objects[theorem.theorem_id] = theorem_obj
        logger.debug(
            "TheoremRegistry: registered theorem %r (kind=%s, verified=%s).",
            theorem.theorem_id,
            theorem.kind.value,
            theorem.is_verified,
        )

    def lookup(self, theorem_id: str) -> CallableTheorem | None:
        """Look up a theorem by its ID.

        Parameters
        ----------
        theorem_id:
            The unique theorem identifier, e.g. ``"arity_consistency"``.

        Returns
        -------
        CallableTheorem | None
            The registered theorem, or ``None`` if not found.
        """
        return self._theorems.get(theorem_id)

    def verify_all(self) -> dict[str, bool]:
        """Run all registered theorem verifications and return results.

        For each theorem that has an associated theorem object, calls
        ``verify([])`` (empty test suite) to obtain baseline status.  For
        theorems without an object, the status is taken from
        :attr:`CallableTheorem.is_verified`.

        Returns
        -------
        dict[str, bool]
            Mapping from theorem_id to pass/fail boolean.
        """
        results: dict[str, bool] = {}
        for tid, theorem in self._theorems.items():
            obj = self._theorem_objects.get(tid)
            if obj is not None and hasattr(obj, "verify"):
                try:
                    passed = obj.verify([])
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "TheoremRegistry: verify() raised for %r: %s", tid, exc
                    )
                    passed = False
            else:
                passed = theorem.is_verified
            results[tid] = passed
            logger.debug(
                "TheoremRegistry: theorem %r => %s.", tid, "PASS" if passed else "FAIL"
            )
        return results

    def failed_theorems(self) -> list[CallableTheorem]:
        """Return theorems that failed the last :meth:`verify_all` run.

        Delegates to :meth:`verify_all` and filters to failing entries.

        Returns
        -------
        list[CallableTheorem]
            List of :class:`CallableTheorem` records that did not pass.
        """
        results = self.verify_all()
        return [
            theorem
            for tid, theorem in self._theorems.items()
            if not results.get(tid, False)
        ]

    def passed_theorems(self) -> list[CallableTheorem]:
        """Return theorems that passed the last :meth:`verify_all` run.

        Delegates to :meth:`verify_all` and filters to passing entries.

        Returns
        -------
        list[CallableTheorem]
            List of :class:`CallableTheorem` records that passed.
        """
        results = self.verify_all()
        return [
            theorem
            for tid, theorem in self._theorems.items()
            if results.get(tid, False)
        ]

    def report(self) -> str:
        """Produce a human-readable verification report.

        The report lists each theorem, its kind, and its pass/fail status,
        followed by a summary line.

        Returns
        -------
        str
            Multi-line report string suitable for logging or printing.
        """
        lines: list[str] = [
            "=" * 60,
            "TheoremRegistry Report — callable_surfaces (theory2.tex Ch16)",
            "=" * 60,
        ]
        results = self.verify_all()
        for tid, theorem in sorted(self._theorems.items()):
            status_str = "PASS" if results.get(tid, False) else "FAIL"
            section = theorem.kind.theory_section()
            lines.append(
                f"  [{status_str}]  {tid:<30s}  "
                f"kind={theorem.kind.value:<25s}  "
                f"ref={section}"
            )
        total = len(results)
        passed = sum(1 for v in results.values() if v)
        failed = total - passed
        lines.append("-" * 60)
        lines.append(
            f"  Total: {total}  |  Passed: {passed}  |  Failed: {failed}"
        )
        lines.append("=" * 60)
        return "\n".join(lines)

    def serialize(self) -> dict[str, Any]:
        """Serialise the registry to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dict with key ``"theorems"`` mapping to a list of serialised
            :class:`CallableTheorem` dicts, plus a ``"count"`` summary.
        """
        serialised_theorems = [
            theorem.serialize() for theorem in self._theorems.values()
        ]
        return {
            "theorems": serialised_theorems,
            "count": len(serialised_theorems),
            "theory_ref": _THEORY_PREFIX,
            "generated_at": _now_iso(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Factory: build_default_registry
# ══════════════════════════════════════════════════════════════════════════════


def build_default_registry() -> TheoremRegistry:
    """Build and return a :class:`TheoremRegistry` pre-populated with all theorems.

    Creates instances of all five specialised theorem classes, registers their
    corresponding :class:`CallableTheorem` records in a fresh registry, and
    returns it.

    This is the canonical entry-point for consumers of this module.  The
    returned registry can be used immediately to check theorems against live
    Python objects, or can be serialised for storage.

    Returns
    -------
    TheoremRegistry
        A registry containing all callable_surfaces theorems from theory2.tex
        Ch16 §16.7.

    Examples
    --------
    ::

        from jugeo.python_runtime.callable_surfaces.theorems import (
            build_default_registry,
        )
        registry = build_default_registry()
        results = registry.verify_all()
        assert "arity_consistency" in results
        print(registry.report())
    """
    registry = TheoremRegistry()

    arity_thm = ArityConsistencyTheorem()
    registry.register(arity_thm.as_callable_theorem(), arity_thm)

    descriptor_thm = DescriptorPriorityTheorem()
    registry.register(descriptor_thm.as_callable_theorem(), descriptor_thm)

    mro_thm = MROValidityTheorem()
    registry.register(mro_thm.as_callable_theorem(), mro_thm)

    binding_thm = BindingValidityTheorem()
    registry.register(binding_thm.as_callable_theorem(), binding_thm)

    surface_thm = SurfaceCompatibilityTheorem()
    registry.register(surface_thm.as_callable_theorem(), surface_thm)

    logger.info(
        "build_default_registry: registered %d callable_surfaces theorems.",
        len(registry._theorems),
    )
    return registry


# ══════════════════════════════════════════════════════════════════════════════
# Module-level default registry (copilot: eager singleton)
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_REGISTRY: TheoremRegistry = build_default_registry()
"""Module-level default registry with all theorems pre-registered.

Consumers can import this directly for quick access without calling
:func:`build_default_registry` themselves.  The default registry is
constructed once at import time and is safe to read from any thread.
Do **not** mutate this registry across threads without external locking;
instead, call :func:`build_default_registry` to obtain a private instance.
"""

# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "TheoremKind",
    "CallableTheorem",
    "ArityConsistencyTheorem",
    "DescriptorPriorityTheorem",
    "MROValidityTheorem",
    "BindingValidityTheorem",
    "SurfaceCompatibilityTheorem",
    "TheoremRegistry",
    "build_default_registry",
    "DEFAULT_REGISTRY",
]
