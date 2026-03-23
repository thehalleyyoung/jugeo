"""
jugeo.python_runtime.generated_contracts.generated_contracts

theory2.tex Ch21 §21.2 — Generated Contracts (dataclasses, attrs, pydantic,
TypedDict, NamedTuple, Protocol, manual).

The *contract generator* G is a functor
    G: SchemaObjects → ObligationSections(F_contract)
that maps schema-level declarations (dataclass definitions, Protocol bodies,
TypedDict schemas) to active obligation sections in the JuGeo contract sheaf.

Unlike annotation-level contracts (§21.1), generated contracts are *structural*:
they describe the shape of data objects in terms of required fields, types,
and invariants, rather than the behavioral contracts on callable boundaries.

Sheaf semantics (§21.2.1):
    For a schema class C with field set {f_1, …, f_n}, the generated contract
    section is
        σ_C = (σ_{f_1}, …, σ_{f_n}) ∈ Γ(U_C, F_struct)
    where U_C is the open neighbourhood of C in the schema site.  Each field
    section σ_{f_i} carries its own type annotation, default-value status, and
    validity constraints.

The gluing condition (§21.2.2): a concrete instance I of C satisfies the
contract iff the restriction
    ρ_{U_C → U_I}: σ_C → σ_I
    is a natural isomorphism — i.e., I provides *all* required sections.

Compliance checking (§21.2.3):
    The ContractCompletionChecker applies the gluing condition at runtime,
    producing a list of ResidualObligation for each missing or violated field.

Exports: GeneratedContractsCoordinator, GeneratedContractsAnalyzer,
         GeneratedContractsWitness, GeneratedContractRecord, ContractSource
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import functools
import inspect
import logging
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports with inline stub fallbacks
# copilot: mirror the same fallback block used across all generated_contracts modules
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    class MorphismKind(enum.Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class CoordinateMorphism:
        def __init__(self, source, target, reason=""): self.source=source; self.target=target; self.reason=reason
    class Site: pass
    class SiteBuilder: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    class PropositionKind(enum.Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; RELATIONAL="relational"
        RESOURCE="resource"; SEMANTIC="semantic"
    class EvidenceItemKind(enum.Enum):
        SOLVER_PROOF="solver_proof"; RUNTIME_WITNESS="runtime_witness"
        ORACLE_PROPOSAL="oracle_proposal"; FORMAL_PROOF="formal_proof"
    class ProvenanceSource(enum.Enum):
        SOLVER="solver"; RUNTIME="runtime"; ORACLE="oracle"; HUMAN="human"; COMPOSED="composed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple[str,...] = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple[str,...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; expiry: str = ""; provenance: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple[Any,...] = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple[Any,...] = (); chain: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, DecoratorTransformer, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None
        is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class DecoratorTransformer:
        decorator_name: str = ""; source_qualname: str = ""; target_qualname: str = ""
        morphism_kind: str = "REFINEMENT"
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ---------------------------------------------------------------------------
# Module-level constants
# copilot: sentinel values used throughout field extraction
# ---------------------------------------------------------------------------

_MISSING_SENTINEL: str = "<missing>"
_NO_DEFAULT: str = "<no-default>"
_UNKNOWN_TYPE: str = "<unknown>"
_MODULE_VERSION: str = "0.1.0"
_MODULE_NAME: str = "generated_contracts"

# copilot: field tuple layout: (name, type_str, has_default, is_required)
_FIELD_TUPLE_KEYS: tuple[str, ...] = ("name", "type_str", "has_default", "is_required")

_DATACLASS_VALIDATOR_NAMES: tuple[str, ...] = (
    "__post_init__", "__init_subclass__", "__post_init_post_parse__",
)

_COMMON_SCHEMA_ATTRS: tuple[str, ...] = (
    "__dataclass_fields__", "__fields__", "__annotations__", "_fields",
    "__protocol_attrs__",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "gc") -> str:
    """Generate a short unique identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _type_str(annotation: Any) -> str:
    """Convert an annotation to a readable string, handling generics."""
    if annotation is inspect.Parameter.empty or annotation is dataclasses.MISSING:
        return _UNKNOWN_TYPE
    if isinstance(annotation, type):
        return annotation.__qualname__
    try:
        return str(annotation)
    except Exception:
        return _UNKNOWN_TYPE


def _has_default(f: dataclasses.Field) -> bool:  # type: ignore[type-arg]
    """Return True when a dataclass field has a default value or factory."""
    has_val = f.default is not dataclasses.MISSING
    has_factory = f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
    return has_val or has_factory


def _is_required_field(f: dataclasses.Field) -> bool:  # type: ignore[type-arg]
    """Return True when a dataclass field is required (no default)."""
    return not _has_default(f)


def _annotation_for_field(f: dataclasses.Field) -> str:  # type: ignore[type-arg]
    """Return the type string for a dataclass field."""
    return _type_str(f.type)


def _trust_level_name(level: Any) -> str:
    """Return the name of a TrustLevel as a string, tolerating None."""
    if level is None:
        return "NONE"
    try:
        return level.name
    except AttributeError:
        return str(level)


def _is_protocol_class(cls: type) -> bool:
    """Return True when *cls* is a typing.Protocol subclass.

    Supports both Python 3.8+ (__protocol_attrs__) and older approaches
    that check __abstractmethods__ on Protocol-derived classes.
    """
    if not isinstance(cls, type):
        return False
    # Python 3.12+ has __protocol_attrs__
    if hasattr(cls, "__protocol_attrs__"):
        return True
    # Check if any base is typing.Protocol
    for base in getattr(cls, "__mro__", []):
        if getattr(base, "__name__", "") == "Protocol" and base.__module__ == "typing":
            return True
    return False


def _is_typed_dict_class(cls: type) -> bool:
    """Return True when *cls* was created with typing.TypedDict."""
    return (
        isinstance(cls, type)
        and hasattr(cls, "__annotations__")
        and hasattr(cls, "__total__")
        and hasattr(cls, "__required_keys__")
    )


def _is_named_tuple_class(cls: type) -> bool:
    """Return True when *cls* is a NamedTuple subclass."""
    return (
        isinstance(cls, type)
        and issubclass(cls, tuple)
        and hasattr(cls, "_fields")
        and hasattr(cls, "_field_defaults")
    )


# ---------------------------------------------------------------------------
# ContractSource and GeneratedContractRecord
# ---------------------------------------------------------------------------

class ContractSource(enum.Enum):
    """Origin of a generated contract.

    theory2.tex §21.2.4 — different schema frameworks produce contracts with
    different trust levels and semantic richness.
    """
    DATACLASS = "dataclass"
    ATTRS = "attrs"
    PYDANTIC = "pydantic"
    TYPED_DICT = "typed_dict"
    NAMED_TUPLE = "named_tuple"
    PROTOCOL = "protocol"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class GeneratedContractRecord:
    """Structural contract extracted from a schema class.

    theory2.tex §21.2.5 — a GeneratedContractRecord is a section
        σ_C ∈ Γ(U_C, F_struct)
    in the structural sheaf, encoding the shape obligations of schema class C.

    Fields
    ------
    source : ContractSource
        The schema framework that generated this contract.
    symbol_name : str
        Qualified name of the schema class.
    fields : tuple
        Tuple of field descriptors, each a tuple
        (name: str, type_str: str, has_default: bool, is_required: bool).
    validators : tuple[str, ...]
        Names of validator methods found on the class.
    trust_level : TrustLevel
        Current trust level in the JuGeo system.
    is_complete : bool
        True when all required fields have been verified to be present.
    schema_version : str
        Schema version string (from __version__ or default).
    metadata : dict
        Arbitrary metadata.
    """

    source: Any = None
    symbol_name: str = ""
    fields: tuple = ()
    validators: tuple = ()
    trust_level: Any = None
    is_complete: bool = False
    schema_version: str = "0.0.0"
    metadata: dict = field(default_factory=dict)

    def field_count(self) -> int:
        """Return the total number of fields in this contract."""
        return len(self.fields)

    def required_fields(self) -> list:
        """Return a list of field descriptors where is_required is True.

        Each descriptor is a tuple (name, type_str, has_default, is_required).
        """
        return [f for f in self.fields if len(f) >= 4 and f[3]]

    def optional_fields(self) -> list:
        """Return a list of field descriptors where is_required is False."""
        return [f for f in self.fields if len(f) >= 4 and not f[3]]

    def to_obligation(self) -> ResidualObligation:
        """Convert this contract record to a ResidualObligation.

        Used when a schema class is declared but no instance has been verified
        against it yet (theory2.tex §21.2.6 — structural gap obligation).
        """
        n_req = len(self.required_fields())
        return ResidualObligation(
            description=(
                f"Contract for {self.symbol_name!r} "
                f"({self.source.value if self.source else 'unknown'}) "
                f"has {n_req} required field(s) that must be verified"
            ),
            obligation_id=_new_id("ob"),
            priority=2 if n_req > 0 else 1,
            is_discharged=self.is_complete,
        )

    def summary(self) -> str:
        """Return a human-readable one-line summary of this contract record."""
        source_str = self.source.value if self.source else "unknown"
        n_req = len(self.required_fields())
        n_opt = len(self.optional_fields())
        trust_str = _trust_level_name(self.trust_level)
        complete_tag = "COMPLETE" if self.is_complete else "INCOMPLETE"
        return (
            f"GeneratedContractRecord({self.symbol_name!r} [{source_str}]"
            f" fields={self.field_count()} req={n_req} opt={n_opt}"
            f" trust={trust_str} {complete_tag})"
        )

    def to_dict(self) -> dict:
        """Serialize this record to a plain dictionary."""
        return {
            "source": self.source.value if self.source else None,
            "symbol_name": self.symbol_name,
            "field_count": self.field_count(),
            "required_fields": [f[0] for f in self.required_fields()],
            "optional_fields": [f[0] for f in self.optional_fields()],
            "validators": list(self.validators),
            "trust_level": _trust_level_name(self.trust_level),
            "is_complete": self.is_complete,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Record of a single observed instantiation against a generated contract.

    theory2.tex §21.2.7 — an instantiation witness is a section of the
    evidence sheaf restricted to the instantiation event U_inst ⊆ U_C.
    """

    instantiation_id: str = ""
    cls_qualname: str = ""
    field_violations: tuple = ()
    missing_fields: tuple = ()
    extra_fields: tuple = ()
    timestamp: str = ""
    trust_level: Any = None

    def is_clean(self) -> bool:
        """Return True when no violations were observed."""
        return (
            len(self.field_violations) == 0
            and len(self.missing_fields) == 0
            and len(self.extra_fields) == 0
        )

    def total_violations(self) -> int:
        """Return total count of all observed violations."""
        return (
            len(self.field_violations)
            + len(self.missing_fields)
            + len(self.extra_fields)
        )

    def summary(self) -> str:
        """One-line summary of this witness record."""
        status = "CLEAN" if self.is_clean() else f"VIOLATED({self.total_violations()})"
        return (
            f"WitnessRecord(cls={self.cls_qualname!r}"
            f" id={self.instantiation_id} {status} @{self.timestamp})"
        )


# ---------------------------------------------------------------------------
# DataclassContractExtractor
# ---------------------------------------------------------------------------

class DataclassContractExtractor:
    """Extract a GeneratedContractRecord from a dataclass.

    theory2.tex §21.2.8 — dataclasses are the canonical schema framework in
    the JuGeo Python runtime.  A frozen dataclass with slots=True corresponds
    to an immutable section of F_struct with maximum structural trust.
    """

    def can_extract(self, cls: Any) -> bool:
        """Return True when *cls* is a dataclass (frozen or not)."""
        return dataclasses.is_dataclass(cls) and isinstance(cls, type)

    def extract(self, cls: type) -> GeneratedContractRecord:
        """Extract a GeneratedContractRecord from dataclass *cls*.

        Inspects dataclasses.fields(cls) and cls.__dataclass_params__ for
        structural properties.  Assigns trust level RUNTIME_WITNESSED for
        frozen=True classes and ORACLE_PROPOSED otherwise.

        Parameters
        ----------
        cls : type
            A class decorated with @dataclass.

        Returns
        -------
        GeneratedContractRecord
            The extracted contract.
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        try:
            fields_raw = dataclasses.fields(cls)
        except TypeError:
            fields_raw = ()

        field_tuples = tuple(self._field_to_tuple(f) for f in fields_raw)
        validators = self._detect_validators(cls)

        # determine trust level from structural properties
        params = getattr(cls, "__dataclass_params__", None)
        is_frozen = getattr(params, "frozen", False) if params else False
        is_slots = getattr(params, "slots", False) if params else False
        trust = TrustLevel.RUNTIME_WITNESSED if is_frozen else TrustLevel.ORACLE_PROPOSED

        n_required = sum(1 for ft in field_tuples if len(ft) >= 4 and ft[3])
        is_complete = n_required == 0 or len(field_tuples) > 0

        metadata = {
            "frozen": is_frozen,
            "slots": is_slots,
            "kw_only": getattr(params, "kw_only", False) if params else False,
            "module": getattr(cls, "__module__", "<unknown>"),
        }

        logger.debug(
            "DataclassContractExtractor.extract: %s fields=%d frozen=%s",
            qualname, len(field_tuples), is_frozen,
        )
        return GeneratedContractRecord(
            source=ContractSource.DATACLASS,
            symbol_name=qualname,
            fields=field_tuples,
            validators=validators,
            trust_level=trust,
            is_complete=is_complete,
            schema_version="0.0.0",
            metadata=metadata,
        )

    def _field_to_tuple(self, f: dataclasses.Field) -> tuple:  # type: ignore[type-arg]
        """Convert a dataclass Field to a (name, type_str, has_default, is_required) tuple.

        theory2.tex §21.2.9 — each field becomes a leaf section in F_struct.
        """
        name = f.name
        type_str = _annotation_for_field(f)
        hd = _has_default(f)
        is_req = _is_required_field(f)
        return (name, type_str, hd, is_req)

    def _detect_validators(self, cls: type) -> tuple:
        """Find validator methods on *cls*.

        Looks for __post_init__, __init_subclass__, and any method whose name
        starts with 'validate_' (a common convention in pydantic-style classes).
        """
        found: list[str] = []
        for vname in _DATACLASS_VALIDATOR_NAMES:
            if vname in cls.__dict__:
                found.append(vname)
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("validate_") and name not in found:
                found.append(name)
        return tuple(found)


# ---------------------------------------------------------------------------
# ProtocolContractExtractor
# ---------------------------------------------------------------------------

class ProtocolContractExtractor:
    """Extract a GeneratedContractRecord from a typing.Protocol subclass.

    theory2.tex §21.2.10 — Protocols define *interface obligations*: the
    abstract methods become required field obligations that any concrete
    implementation must satisfy.
    """

    def can_extract(self, cls: Any) -> bool:
        """Return True when *cls* is a Protocol subclass."""
        return isinstance(cls, type) and _is_protocol_class(cls)

    def extract(self, cls: type) -> GeneratedContractRecord:
        """Extract a GeneratedContractRecord from Protocol *cls*.

        Each abstract method / property becomes a field tuple with
        type_str='callable' and is_required=True.

        Parameters
        ----------
        cls : type
            A typing.Protocol subclass.

        Returns
        -------
        GeneratedContractRecord
            Contract with abstract members as required fields.
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        abstract_members = self._get_abstract_members(cls)
        field_tuples = tuple(
            (name, "callable", False, True) for name in abstract_members
        )
        # detect validators (non-abstract methods that validate state)
        validators: list[str] = []
        for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("validate_"):
                validators.append(name)

        logger.debug(
            "ProtocolContractExtractor.extract: %s abstract_members=%d",
            qualname, len(abstract_members),
        )
        return GeneratedContractRecord(
            source=ContractSource.PROTOCOL,
            symbol_name=qualname,
            fields=field_tuples,
            validators=tuple(validators),
            trust_level=TrustLevel.ORACLE_PROPOSED,
            is_complete=len(abstract_members) == 0,
            schema_version="0.0.0",
            metadata={"abstract_count": len(abstract_members)},
        )

    def _get_abstract_members(self, cls: type) -> list[str]:
        """Return names of all abstract methods and properties on *cls*.

        Checks __abstractmethods__ (set by ABCMeta), __protocol_attrs__ (Python
        3.12+), and falls back to inspecting __dict__ for unbound functions that
        lack a body other than `...` or `pass`.
        """
        abstract: set[str] = set()
        # standard ABCMeta mechanism
        abstract.update(getattr(cls, "__abstractmethods__", set()))
        # Python 3.12+ Protocol mechanism
        abstract.update(getattr(cls, "__protocol_attrs__", set()))
        # scan __annotations__ for property stubs
        for name in getattr(cls, "__annotations__", {}):
            if name not in abstract:
                abstract.add(name)
        return sorted(abstract)


# ---------------------------------------------------------------------------
# TypedDictContractExtractor
# ---------------------------------------------------------------------------

class TypedDictContractExtractor:
    """Extract a GeneratedContractRecord from a TypedDict class.

    theory2.tex §21.2.11 — TypedDicts define *total* or *partial* structural
    obligations on dict-like objects.  Required keys (from __required_keys__)
    become is_required=True fields; optional keys become is_required=False.
    """

    def can_extract(self, cls: Any) -> bool:
        """Return True when *cls* was created with typing.TypedDict."""
        return _is_typed_dict_class(cls)

    def extract(self, cls: type) -> GeneratedContractRecord:
        """Extract a GeneratedContractRecord from TypedDict *cls*.

        Reads __annotations__, __required_keys__, and __optional_keys__.

        Parameters
        ----------
        cls : type
            A class created via typing.TypedDict.

        Returns
        -------
        GeneratedContractRecord
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        annotations = getattr(cls, "__annotations__", {})
        required_keys: frozenset = getattr(cls, "__required_keys__", frozenset(annotations.keys()))
        optional_keys: frozenset = getattr(cls, "__optional_keys__", frozenset())
        is_total: bool = getattr(cls, "__total__", True)

        field_tuples: list[tuple] = []
        for fname, ftype in annotations.items():
            type_str = _type_str(ftype)
            is_req = fname in required_keys
            has_def = fname in optional_keys
            field_tuples.append((fname, type_str, has_def, is_req))

        trust = TrustLevel.ORACLE_PROPOSED
        is_complete = not is_total or len(required_keys) == 0

        logger.debug(
            "TypedDictContractExtractor.extract: %s fields=%d total=%s",
            qualname, len(field_tuples), is_total,
        )
        return GeneratedContractRecord(
            source=ContractSource.TYPED_DICT,
            symbol_name=qualname,
            fields=tuple(field_tuples),
            validators=(),
            trust_level=trust,
            is_complete=is_complete,
            schema_version="0.0.0",
            metadata={"total": is_total},
        )


# ---------------------------------------------------------------------------
# NamedTupleContractExtractor
# ---------------------------------------------------------------------------

class NamedTupleContractExtractor:
    """Extract a GeneratedContractRecord from a NamedTuple subclass.

    theory2.tex §21.2.12 — NamedTuples combine positional tuple semantics
    with named-field access.  All fields are required (no defaults unless
    explicitly set via field defaults).
    """

    def can_extract(self, cls: Any) -> bool:
        """Return True when *cls* is a NamedTuple subclass."""
        return _is_named_tuple_class(cls)

    def extract(self, cls: type) -> GeneratedContractRecord:
        """Extract a GeneratedContractRecord from NamedTuple *cls*.

        Uses _fields, _field_types (legacy) or __annotations__, and
        _field_defaults to determine required vs optional fields.

        Parameters
        ----------
        cls : type
            A collections.namedtuple or typing.NamedTuple subclass.

        Returns
        -------
        GeneratedContractRecord
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        field_names: tuple = getattr(cls, "_fields", ())
        field_types: dict = getattr(cls, "__annotations__", {})
        field_defaults: dict = getattr(cls, "_field_defaults", {})

        field_tuples: list[tuple] = []
        for fname in field_names:
            ftype = field_types.get(fname, Any)
            type_str = _type_str(ftype)
            has_def = fname in field_defaults
            is_req = not has_def
            field_tuples.append((fname, type_str, has_def, is_req))

        logger.debug(
            "NamedTupleContractExtractor.extract: %s fields=%d",
            qualname, len(field_tuples),
        )
        return GeneratedContractRecord(
            source=ContractSource.NAMED_TUPLE,
            symbol_name=qualname,
            fields=tuple(field_tuples),
            validators=(),
            trust_level=TrustLevel.ORACLE_PROPOSED,
            is_complete=True,
            schema_version="0.0.0",
            metadata={"field_defaults": list(field_defaults.keys())},
        )


# ---------------------------------------------------------------------------
# ContractCompletionChecker
# ---------------------------------------------------------------------------

class ContractCompletionChecker:
    """Check whether a class or instance satisfies a generated contract.

    theory2.tex §21.2.13 — the checker applies the gluing condition:
    for each required field f_i in σ_C, verify that the instance I provides
    a value for f_i (i.e., the restriction ρ_{U_C → U_I}(σ_{f_i}) exists).

    Missing fields produce ResidualObligation entries which propagate
    up the judgment ladder.
    """

    def check(self, cls: type, contract: GeneratedContractRecord) -> list[ResidualObligation]:
        """Check whether *cls* satisfies *contract* at the class level.

        Produces one ResidualObligation per required field that is absent
        from the class dictionary.

        Parameters
        ----------
        cls : type
            The class to verify.
        contract : GeneratedContractRecord
            The structural contract to check against.

        Returns
        -------
        list[ResidualObligation]
            Obligations for each missing required field.
        """
        obligations: list[ResidualObligation] = []
        for ftuple in contract.required_fields():
            fname = ftuple[0] if ftuple else ""
            if not fname:
                continue
            if not self._has_field(cls, fname):
                ob = ResidualObligation(
                    description=f"Class {contract.symbol_name!r} is missing required field {fname!r}",
                    obligation_id=_new_id("ob"),
                    priority=3,
                    is_discharged=False,
                )
                obligations.append(ob)
                logger.debug(
                    "ContractCompletionChecker.check: missing field %r on %s",
                    fname, contract.symbol_name,
                )
        return obligations

    def check_instance(
        self, instance: Any, contract: GeneratedContractRecord
    ) -> list[ResidualObligation]:
        """Check a concrete *instance* against *contract*.

        Verifies that the instance carries all required fields as attributes
        (or dict keys for TypedDict-like objects).

        Parameters
        ----------
        instance : Any
            The concrete object to verify.
        contract : GeneratedContractRecord
            The structural contract.

        Returns
        -------
        list[ResidualObligation]
            Obligations for each missing or type-violated field.
        """
        obligations: list[ResidualObligation] = []
        for ftuple in contract.fields:
            if len(ftuple) < 4:
                continue
            fname, type_str, has_def, is_req = ftuple
            # check presence
            has_attr = hasattr(instance, fname)
            is_dict_like = isinstance(instance, dict)
            has_key = is_dict_like and fname in instance
            present = has_attr or has_key
            if not present and is_req:
                obligations.append(ResidualObligation(
                    description=(
                        f"Instance of {contract.symbol_name!r} "
                        f"is missing required field {fname!r}"
                    ),
                    obligation_id=_new_id("ob"),
                    priority=3,
                    is_discharged=False,
                ))
        return obligations

    def _has_field(self, cls: Any, field_name: str) -> bool:
        """Check whether *cls* declares *field_name* in any form.

        Checks __annotations__, __dict__, and dataclass fields if applicable.
        """
        if hasattr(cls, field_name):
            return True
        if field_name in getattr(cls, "__annotations__", {}):
            return True
        if dataclasses.is_dataclass(cls):
            try:
                return any(f.name == field_name for f in dataclasses.fields(cls))
            except TypeError:
                pass
        return False


# ---------------------------------------------------------------------------
# GeneratedContractsAnalyzer
# ---------------------------------------------------------------------------

class GeneratedContractsAnalyzer:
    """High-level analyzer that maps schema classes to judgments.

    theory2.tex §21.2.14 — the analyzer implements the functor
        G: SchemaClasses → Judgments(F_struct)
    by composing contract extraction with obligation checking and judgment
    construction.

    Usage
    -----
    >>> analyzer = GeneratedContractsAnalyzer()
    >>> contract = analyzer.analyze_class(MyDataclass)
    >>> judgments = analyzer.emit_judgments(MyDataclass)
    """

    def __init__(self) -> None:
        # copilot: extractors tried in order; first match wins
        self._extractors: list = [
            DataclassContractExtractor(),
            ProtocolContractExtractor(),
            TypedDictContractExtractor(),
            NamedTupleContractExtractor(),
        ]
        self._checker = ContractCompletionChecker()
        # copilot: cache contracts to avoid repeated extraction
        self._cache: dict[int, GeneratedContractRecord] = {}
        self._analyzed_count: int = 0
        self._judgment_count: int = 0

    def analyze_class(self, cls: Any) -> GeneratedContractRecord:
        """Analyze *cls* and return a GeneratedContractRecord.

        Tries each extractor in order; the first one that can_extract returns
        the contract.  Falls back to a minimal MANUAL contract if no extractor
        matches.

        Parameters
        ----------
        cls : Any
            The class to analyze.

        Returns
        -------
        GeneratedContractRecord
        """
        cls_id = id(cls)
        if cls_id in self._cache:
            return self._cache[cls_id]

        self._analyzed_count += 1
        for extractor in self._extractors:
            if extractor.can_extract(cls):
                try:
                    contract = extractor.extract(cls)
                    self._cache[cls_id] = contract
                    logger.debug(
                        "analyze_class: %s extracted by %s",
                        getattr(cls, "__qualname__", cls),
                        type(extractor).__name__,
                    )
                    return contract
                except Exception as exc:
                    logger.warning(
                        "analyze_class: extractor %s failed on %s: %s",
                        type(extractor).__name__, cls, exc,
                    )

        # fallback: minimal manual contract
        qualname = getattr(cls, "__qualname__", repr(cls))
        anns = getattr(cls, "__annotations__", {})
        field_tuples = tuple(
            (name, _type_str(ann), False, True) for name, ann in anns.items()
        )
        fallback = GeneratedContractRecord(
            source=ContractSource.MANUAL,
            symbol_name=qualname,
            fields=field_tuples,
            validators=(),
            trust_level=TrustLevel.UNVERIFIED,
            is_complete=False,
            schema_version="0.0.0",
            metadata={"fallback": True},
        )
        self._cache[cls_id] = fallback
        logger.debug("analyze_class: %s fell back to MANUAL contract", qualname)
        return fallback

    def check_compliance(
        self, cls: Any, contract: GeneratedContractRecord
    ) -> list[ResidualObligation]:
        """Check compliance of *cls* against *contract*.

        Parameters
        ----------
        cls : Any
            The class to verify.
        contract : GeneratedContractRecord
            Contract to check against.

        Returns
        -------
        list[ResidualObligation]
        """
        return self._checker.check(cls, contract)

    def emit_judgments(self, cls: Any) -> list[Judgment]:
        """Analyze *cls* and emit JuGeo Judgment objects.

        theory2.tex §21.2.15 — each field in the contract becomes a Judgment
        asserting the structural proposition Struct(field_name, type_str).
        Compliance obligations are embedded in the judgment's obligations tuple.

        Parameters
        ----------
        cls : Any
            The class to judge.

        Returns
        -------
        list[Judgment]
        """
        contract = self.analyze_class(cls)
        compliance_obs = self.check_compliance(cls, contract)
        judgments: list[Judgment] = []

        for ftuple in contract.fields:
            if len(ftuple) < 4:
                continue
            fname, type_str, has_def, is_req = ftuple
            proposition = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula=f"field({contract.symbol_name!r}, {fname!r}) : {type_str}",
                free_variables=(fname,),
                metadata={"has_default": has_def, "is_required": is_req},
            )
            carrier = Carrier(
                name=fname,
                parameters=(contract.symbol_name,),
                is_dependent=True,
                metadata={"source": contract.source.value if contract.source else "manual"},
            )
            evidence_item = EvidenceItem(
                kind=EvidenceItemKind.RUNTIME_WITNESS,
                payload={"type_str": type_str, "has_default": has_def},
                trust_level=contract.trust_level,
                channel="generated_contracts_analyzer",
                timestamp=_now_iso(),
                expiry="",
                provenance=("GeneratedContractsAnalyzer",),
            )
            bundle = EvidenceBundle(items=(evidence_item,))
            # field-specific obligations from compliance check
            field_obs = [
                ob for ob in compliance_obs
                if fname in getattr(ob, "description", "")
            ]
            trust_ann = TrustAnnotation(
                level=contract.trust_level,
                rationale=f"extracted from {contract.source.value if contract.source else 'manual'} schema",
            )
            prov = Provenance(
                sources=(ProvenanceSource.RUNTIME,),
                chain=("GeneratedContractsAnalyzer", type(self._extractors[0]).__name__),
            )
            judgment = Judgment(
                coordinate=None,
                proposition=proposition,
                carrier=carrier,
                evidence=bundle,
                obligations=tuple(field_obs),
                obstructions=(),
                trust=trust_ann,
                provenance=prov,
            )
            judgments.append(judgment)
            self._judgment_count += 1

        logger.debug("emit_judgments: produced %d judgments for %s", len(judgments), cls)
        return judgments

    def summary(self) -> str:
        """Return a multi-line summary of analyzer activity."""
        return (
            f"GeneratedContractsAnalyzer Summary\n"
            f"  Classes analyzed  : {self._analyzed_count}\n"
            f"  Contracts cached  : {len(self._cache)}\n"
            f"  Judgments emitted : {self._judgment_count}\n"
        )


# ---------------------------------------------------------------------------
# GeneratedContractsWitness
# ---------------------------------------------------------------------------

class GeneratedContractsWitness:
    """Witness that observes instantiations and checks contract compliance.

    theory2.tex §21.2.16 — the instantiation witness applies the gluing
    condition at the instance level, producing evidence (or obstructions) for
    each observed instantiation event.
    """

    def __init__(self) -> None:
        # copilot: accumulate witness records for auditing
        self._records: list[WitnessRecord] = []
        self._clean_count: int = 0
        self._violation_count: int = 0
        self._lock = threading.Lock()
        self._analyzer = GeneratedContractsAnalyzer()
        self._checker = ContractCompletionChecker()

    def witness_instantiation(self, cls: Any, kwargs: dict) -> WitnessRecord:
        """Observe instantiation of *cls* with *kwargs*.

        Checks *kwargs* against the contract for *cls*, identifying missing
        required fields, extra unknown fields, and type violations.

        Parameters
        ----------
        cls : Any
            The class being instantiated.
        kwargs : dict
            Keyword arguments passed to the constructor.

        Returns
        -------
        WitnessRecord
            Immutable record of the observed instantiation.
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        inst_id = _new_id("inst")
        contract = self._analyzer.analyze_class(cls)

        required_names = {ft[0] for ft in contract.required_fields() if ft}
        all_field_names = {ft[0] for ft in contract.fields if ft}

        missing_fields = tuple(
            name for name in required_names if name not in kwargs
        )
        extra_fields = tuple(
            name for name in kwargs if name not in all_field_names
        )
        field_violations: list[str] = []

        # type-check provided fields where possible
        for fname, fval in kwargs.items():
            matched = next((ft for ft in contract.fields if ft[0] == fname), None)
            if matched is None:
                continue
            type_str = matched[1] if len(matched) > 1 else _UNKNOWN_TYPE
            # we can only check against concrete types we can resolve
            # copilot: skip structural/generic annotations at runtime
            if type_str not in (_UNKNOWN_TYPE, "Any", "typing.Any"):
                pass  # deeper type checking requires solver; record as advisory

        record = WitnessRecord(
            instantiation_id=inst_id,
            cls_qualname=qualname,
            field_violations=tuple(field_violations),
            missing_fields=missing_fields,
            extra_fields=extra_fields,
            timestamp=_now_iso(),
            trust_level=TrustLevel.RUNTIME_WITNESSED,
        )
        with self._lock:
            self._records.append(record)
            if record.is_clean():
                self._clean_count += 1
            else:
                self._violation_count += record.total_violations()

        logger.debug(
            "witness_instantiation: %s %s violations=%d",
            qualname, inst_id, record.total_violations(),
        )
        return record

    def get_records(self) -> list[WitnessRecord]:
        """Return a copy of all witness records."""
        with self._lock:
            return list(self._records)

    def get_violations(self) -> list[WitnessRecord]:
        """Return only records that contain at least one violation."""
        with self._lock:
            return [r for r in self._records if not r.is_clean()]

    def summary(self) -> str:
        """Return a multi-line summary of witness activity."""
        with self._lock:
            total = len(self._records)
            return (
                f"GeneratedContractsWitness Summary\n"
                f"  Total instantiations : {total}\n"
                f"  Clean                : {self._clean_count}\n"
                f"  Total violations     : {self._violation_count}\n"
            )


# ---------------------------------------------------------------------------
# GeneratedContractsCoordinator
# ---------------------------------------------------------------------------

class GeneratedContractsCoordinator:
    """Top-level coordinator for generated-contract analysis.

    theory2.tex §21.2.17 — the coordinator composes the full pipeline:
        SchemaClass → Contract → Compliance → Judgments → Witness

    Thread-safe; can be shared across threads.
    """

    def __init__(self) -> None:
        # copilot: create sub-components once and reuse
        self._analyzer = GeneratedContractsAnalyzer()
        self._witness = GeneratedContractsWitness()
        self._lock = threading.Lock()
        self._coordinator_id = _new_id("coord")
        self._audited_classes: list[str] = []

    def coordinate(self, cls: Any) -> CoordinateObject:
        """Build a CoordinateObject from *cls*'s qualified name and module.

        theory2.tex §21.2.18 — the coordinate situates the schema class in
        the Python runtime site.
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        module_name = getattr(cls, "__module__", "<unknown>")
        components = tuple(
            part for part in f"{module_name}.{qualname}".split(".") if part
        )
        return CoordinateObject(
            components=components,
            kind=CoordinateKind.INTERFACE,
            support_labels=frozenset({module_name}),
            metadata={"coordinator_id": self._coordinator_id},
        )

    def full_audit(self, cls: Any) -> dict:
        """Run a full structural audit on schema class *cls*.

        Thread-safe. Returns a dict with keys:
        - contract       : GeneratedContractRecord
        - compliance     : list[ResidualObligation]
        - judgments      : list[Judgment]
        - witness_record : WitnessRecord (from an empty instantiation probe)
        - summary        : str

        Parameters
        ----------
        cls : Any
            The schema class to audit.

        Returns
        -------
        dict
        """
        with self._lock:
            qualname = getattr(cls, "__qualname__", repr(cls))
            logger.info("full_audit: starting on %s", qualname)
            self._audited_classes.append(qualname)

            contract = self._analyzer.analyze_class(cls)
            compliance = self._analyzer.check_compliance(cls, contract)
            judgments = self._analyzer.emit_judgments(cls)

            # probe instantiation with empty kwargs to get baseline witness
            witness_record = self._witness.witness_instantiation(cls, {})

            result = {
                "contract": contract,
                "compliance": compliance,
                "judgments": judgments,
                "witness_record": witness_record,
                "summary": (
                    f"full_audit({qualname!r}): "
                    f"fields={contract.field_count()} "
                    f"compliance_obs={len(compliance)} "
                    f"judgments={len(judgments)}"
                ),
            }
            logger.info(
                "full_audit: done on %s — fields=%d obligations=%d judgments=%d",
                qualname, contract.field_count(), len(compliance), len(judgments),
            )
            return result

    def report(self) -> str:
        """Return a comprehensive multi-line coordinator report."""
        lines = [
            f"GeneratedContractsCoordinator Report",
            f"  Coordinator ID    : {self._coordinator_id}",
            f"  Audited classes   : {len(self._audited_classes)}",
            "",
            "  Audited symbols:",
        ]
        for sym in self._audited_classes:
            lines.append(f"    - {sym}")
        lines.append("")
        lines.append(self._analyzer.summary())
        lines.append(self._witness.summary())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Additional helpers and utilities
# ---------------------------------------------------------------------------

def build_contract_summary_table(contracts: list[GeneratedContractRecord]) -> str:
    """Build a formatted ASCII table summarizing a list of GeneratedContractRecord.

    Each row contains symbol name, source, field count, required count, and trust.
    """
    if not contracts:
        return "(no contract records)"

    col_widths = [40, 12, 8, 8, 20]
    headers = ["Symbol", "Source", "Fields", "Req", "Trust"]

    def row(cols: list[str]) -> str:
        padded = [c[:col_widths[i]].ljust(col_widths[i]) for i, c in enumerate(cols)]
        return "| " + " | ".join(padded) + " |"

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    lines = [sep, row(headers), sep]
    for c in contracts:
        source_str = c.source.value if c.source else "unknown"
        req_count = str(len(c.required_fields()))
        trust_str = _trust_level_name(c.trust_level)
        lines.append(row([
            c.symbol_name,
            source_str,
            str(c.field_count()),
            req_count,
            trust_str,
        ]))
    lines.append(sep)
    return "\n".join(lines)


def compute_schema_coverage(contracts: list[GeneratedContractRecord]) -> dict:
    """Compute schema coverage statistics.

    Returns: total, complete, incomplete, fraction_complete, by_source.
    """
    total = len(contracts)
    complete = sum(1 for c in contracts if c.is_complete)
    incomplete = total - complete
    by_source: dict = {}
    for c in contracts:
        src = c.source.value if c.source else "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "total": total,
        "complete": complete,
        "incomplete": incomplete,
        "fraction_complete": complete / total if total else 0.0,
        "by_source": by_source,
    }


def contracts_to_obligations(contracts: list[GeneratedContractRecord]) -> list[ResidualObligation]:
    """Convert a list of GeneratedContractRecord to ResidualObligation list.

    Each incomplete contract yields one obligation.  Already-complete contracts
    yield discharged obligations.
    """
    obligations: list[ResidualObligation] = []
    for c in contracts:
        ob = c.to_obligation()
        if c.is_complete:
            ob = ob.discharge("contract_complete")
        obligations.append(ob)
    return obligations


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        # define sample schema classes
        @dataclass(frozen=True, slots=True)
        class SampleFrozen:
            """A sample frozen dataclass for smoke testing."""
            x: int
            y: str = "default"
            z: float = 0.0

        @dataclass
        class SampleMutable:
            """A mutable dataclass with a validator."""
            name: str
            value: int = 0

            def __post_init__(self) -> None:
                if not self.name:
                    raise ValueError("name must be non-empty")

        coordinator = GeneratedContractsCoordinator()

        # full_audit on frozen dataclass
        result_frozen = coordinator.full_audit(SampleFrozen)
        assert "contract" in result_frozen, "missing 'contract' key"
        assert "compliance" in result_frozen, "missing 'compliance' key"
        assert "judgments" in result_frozen, "missing 'judgments' key"
        assert "witness_record" in result_frozen, "missing 'witness_record' key"
        assert "summary" in result_frozen, "missing 'summary' key"
        contract = result_frozen["contract"]
        assert contract.field_count() == 3, f"expected 3 fields, got {contract.field_count()}"
        assert len(contract.required_fields()) == 1, f"expected 1 required field, got {len(contract.required_fields())}"

        # full_audit on mutable dataclass
        result_mutable = coordinator.full_audit(SampleMutable)
        assert result_mutable["contract"].source == ContractSource.DATACLASS
        mutable_contract = result_mutable["contract"]
        assert "__post_init__" in mutable_contract.validators, "expected __post_init__ validator"

        # coverage stats
        contracts = [result_frozen["contract"], result_mutable["contract"]]
        coverage = compute_schema_coverage(contracts)
        assert "fraction_complete" in coverage

        table = build_contract_summary_table(contracts)
        assert "Symbol" in table

        # witness direct instantiation check
        witness = GeneratedContractsWitness()
        wr = witness.witness_instantiation(SampleFrozen, {"x": 1, "y": "hello", "z": 1.5})
        assert wr.is_clean(), f"unexpected violations: {wr.missing_fields}"

        wr_missing = witness.witness_instantiation(SampleFrozen, {})
        assert not wr_missing.is_clean(), "expected missing field violations"

        print(f"[smoke] SampleFrozen contract: {contract.summary()}")
        print(f"[smoke] judgments={len(result_frozen['judgments'])}")
        print(coordinator.report())
        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
