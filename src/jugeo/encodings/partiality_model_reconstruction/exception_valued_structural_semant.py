"""Exception-valued structural semantics: extending the Z3 encoding framework to handle
Python operations whose semantics are exception-valued rather than total.

Python operations like `x[i]`, `int(s)`, `f(*args)`, and attribute access may raise
exceptions. Rather than pretending they are total, this module encodes them using
lifted types: Result[T] = Ok(T) | Err(ExceptionType). The Z3 encoding then carries
the exception path alongside the normal path.

Invariants:
- Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans
- Trust is an ordered algebra — never a float
- Obstructions are Čech H¹ cohomology classes

# copilot: exception-valued structural semantics — Result[T] lifting for Python operations in Z3 encodings
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import abc
import collections
import contextlib
import copy
import dataclasses
import enum
import functools
import hashlib
import itertools
import json
import logging
import math
import os
import pathlib
import re
import sys
import time
import typing
import uuid
import weakref
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from functools import lru_cache, reduce
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterator, List, Mapping
from typing import Optional, Sequence, Set, Tuple, Type, Union

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo error imports
# ---------------------------------------------------------------------------
try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, enum.Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, enum.Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maps Python operation kinds to the exception types they may raise.
EXCEPTION_TRIGGERS: Dict[str, List[str]] = {
    "subscript":        ["IndexError", "KeyError", "TypeError"],
    "int_conversion":   ["ValueError", "TypeError", "OverflowError"],
    "attribute_access": ["AttributeError"],
    "call":             ["TypeError", "RuntimeError", "Exception"],
    "division":         ["ZeroDivisionError", "FloatingPointError"],
    "iteration":        ["StopIteration", "TypeError"],
    "import":           ["ImportError", "ModuleNotFoundError"],
    "open_file":        ["FileNotFoundError", "PermissionError", "OSError"],
    "unpack":           ["ValueError", "TypeError"],
    "comparison":       ["TypeError"],
}

#: Template for a Result[T] type declaration in Z3-style pseudo-syntax.
RESULT_TYPE_TEMPLATE: str = (
    "Result[{T}] ::= Ok(value: {T}) | Err(exc_type: ExcType, message: String)\n"
    "  where ExcType ::= {exc_types}\n"
    "  and   Ok(v).is_ok  = True\n"
    "  and   Err(e).is_ok = False\n"
    "  and   ∀ v. Ok(v).unwrap() = v\n"
    "  and   ∀ e. Err(e).unwrap() raises e\n"
)

#: Preferred lifting strategy per operation kind.
LIFTING_PREFERENCE: Dict[str, str] = {
    "subscript":        "result_type",
    "int_conversion":   "result_type",
    "attribute_access": "option_type",
    "call":             "result_type",
    "division":         "checked_arithmetic",
    "iteration":        "result_type",
    "import":           "option_type",
    "open_file":        "result_type",
    "unpack":           "result_type",
    "comparison":       "bool_with_guard",
}


# ---------------------------------------------------------------------------
# TrustTier — ordered algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
# NEVER a float — this is an algebraic structure with join/meet/promote/demote
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    The five tiers form a total order:
        PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼ RUNTIME_WITNESSED ≼ PROOF_BACKED

    Operations:
        join(a, b) = a ⊕ b  (least upper bound)
        meet(a, b) = a ⊖ b  (greatest lower bound)
        promote()  = ↑_π    (step up the lattice)
        demote()   = ↓_χ    (step down the lattice)
        is_admissible(threshold) = (self ≽ threshold)

    Invariant: Trust is NEVER a float. It is always a TrustTier value.
    """

    PROPOSAL          = 1
    REVIEWED          = 2
    VERIFIED          = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED      = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Least upper bound (⊕) in the trust lattice."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Greatest lower bound (⊖) in the trust lattice."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Promote one tier upward (↑_π), capped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, 5))

    def demote(self) -> TrustTier:
        """Demote one tier downward (↓_χ), floored at PROPOSAL."""
        return TrustTier(max(self.value - 1, 1))

    def is_admissible(self, threshold: TrustTier) -> bool:
        """True iff self ≽ threshold in the admissibility order."""
        return self.value >= threshold.value


# ---------------------------------------------------------------------------
# Helper enums
# ---------------------------------------------------------------------------

class ExceptionCategory(str, enum.Enum):
    """Semantic category of an exception for encoding purposes.

    These categories guide how the Z3 encoding handles each exception path:
    - RECOVERABLE: The exception can be caught and handled gracefully.
    - FATAL:       The exception indicates an unrecoverable program error.
    - PARTIAL:     The operation is partial; the exception signals out-of-domain input.
    - CONTRACT:    The exception is a design-by-contract violation (precondition failed).
    - ENVIRONMENTAL: The exception arises from external system state (IO, network, OS).
    """
    RECOVERABLE    = "recoverable"
    FATAL          = "fatal"
    PARTIAL        = "partial"
    CONTRACT       = "contract"
    ENVIRONMENTAL  = "environmental"


class LiftingStrategy(str, enum.Enum):
    """Strategy for lifting a partial Python operation to a total Result[T].

    - RESULT_TYPE:      Encode as Ok(v) | Err(exc). The canonical lifting.
    - OPTION_TYPE:      Encode as Some(v) | None. Used when the error is unimportant.
    - CHECKED_ARITH:    Encode with a side guard (e.g., divisor != 0).
    - BOOL_WITH_GUARD:  Return Bool with a precondition guard formula.
    - SENTINEL:         Use a sentinel value (e.g., -1) to signal failure.
    - RAISE_ON_FAIL:    Do not lift; keep the partial semantics and add an obligation.
    """
    RESULT_TYPE     = "result_type"
    OPTION_TYPE     = "option_type"
    CHECKED_ARITH   = "checked_arithmetic"
    BOOL_WITH_GUARD = "bool_with_guard"
    SENTINEL        = "sentinel"
    RAISE_ON_FAIL   = "raise_on_fail"


class EncodeMode(str, enum.Enum):
    """Encoding mode controlling how much of the exception semantics to materialise.

    - STRICT:     Every possible exception path is represented in the Z3 encoding.
    - OPTIMISTIC: Only the normal path is encoded; exception obligations are deferred.
    - LAZY:       Exceptions are encoded on demand when a discharge attempt fails.
    - SYMBOLIC:   Exception types are represented as uninterpreted Z3 sorts.
    """
    STRICT    = "strict"
    OPTIMISTIC = "optimistic"
    LAZY      = "lazy"
    SYMBOLIC  = "symbolic"


class ExceptionPath(str, enum.Enum):
    """Identifies which execution path through a try/except block is being encoded.

    - NORMAL:   The try body completes without exception.
    - CAUGHT:   An exception was raised and caught by an except clause.
    - UNCAUGHT: An exception was raised and propagated out of the try block.
    - FINALLY:  The finally clause (always executed regardless of exception).
    - RERAISE:  An exception was caught and re-raised (possibly wrapped).
    """
    NORMAL   = "normal"
    CAUGHT   = "caught"
    UNCAUGHT = "uncaught"
    FINALLY  = "finally"
    RERAISE  = "reraise"


# ---------------------------------------------------------------------------
# Judgment dataclass — (c, φ, A, E, O, B, T, Π) — NEVER a boolean
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Each judgment is a structured tuple recording:
        context    — the context c (e.g. variable environment, code location)
        formula    — the proposition φ being asserted
        assumptions — the set A of background assumptions
        evidence   — the tuple E of evidence items supporting the judgment
        obligations — the tuple O of sub-obligations yet to be discharged
        burden     — the proof burden B (who/what must discharge the obligations)
        trust      — the TrustTier T (an ordered algebra — NEVER a float)
        provenance — the provenance Π (source of the judgment)

    Invariant: Judgments are NEVER reduced to booleans. A judgment with
    obligations remaining is still a Judgment, not False.
    """

    context:     Any    # c — typing context or code location
    formula:     Any    # φ — the proposition asserted
    assumptions: tuple  # A — background assumptions (immutable)
    evidence:    tuple  # E — evidence items
    obligations: tuple  # O — outstanding proof obligations
    burden:      Any    # B — proof burden descriptor
    trust:       TrustTier  # T — trust tier (ordered algebra)
    provenance:  Any    # Π — provenance source

    def is_fully_discharged(self) -> bool:
        """True iff all obligations are discharged and trust is at least VERIFIED."""
        return len(self.obligations) == 0 and self.trust.is_admissible(TrustTier.VERIFIED)

    def with_trust(self, new_tier: TrustTier) -> Judgment:
        """Return a new Judgment with an updated trust tier."""
        return dataclasses.replace(self, trust=new_tier)

    def with_obligations(self, new_obligations: tuple) -> Judgment:
        """Return a new Judgment with updated obligations."""
        return dataclasses.replace(self, obligations=new_obligations)

    def discharge_obligation(self, obligation: Any) -> Judgment:
        """Remove one obligation from the outstanding set."""
        remaining = tuple(o for o in self.obligations if o != obligation)
        return self.with_obligations(remaining)

    def add_evidence(self, item: Any) -> Judgment:
        """Return a new Judgment with an additional evidence item."""
        return dataclasses.replace(self, evidence=self.evidence + (item,))

    def promote_trust(self) -> Judgment:
        """Promote the trust tier one step."""
        return self.with_trust(self.trust.promote())

    def __str__(self) -> str:
        return (
            f"Judgment(φ={self.formula!r}, T={self.trust.name}, "
            f"|O|={len(self.obligations)}, |E|={len(self.evidence)})"
        )


# ---------------------------------------------------------------------------
# CechObstruction — Čech H¹ cohomology class for gluing obstructions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class representing a gluing obstruction.

    In the exception-sheaf framework, a gluing obstruction arises when the
    local sections over the exception paths cannot be consistently glued into
    a global section. The obstruction lives in H¹(cover, F) for the sheaf F
    of exception encodings.

    Fields:
        cover_id        — identifier of the open cover (e.g. a try block's region)
        cocycle         — frozenset of (overlap_id, section_mismatch) pairs
        cohomology_class — symbolic label for the H¹ class
        description     — human-readable description of what the obstruction means

    Invariant: Obstructions are Čech H¹ cohomology classes — NEVER booleans or errors.
    """

    cover_id:         str
    cocycle:          frozenset  # frozenset of (patch_pair, section_diff) tuples
    cohomology_class: str
    description:      str

    def is_trivial(self) -> bool:
        """True iff the cocycle is trivial (the obstruction vanishes — gluing succeeds)."""
        return len(self.cocycle) == 0

    def restrict_to(self, sub_cover_id: str) -> CechObstruction:
        """Restrict this obstruction to a sub-cover."""
        return CechObstruction(
            cover_id=sub_cover_id,
            cocycle=frozenset(
                item for item in self.cocycle
                if isinstance(item, tuple) and sub_cover_id in str(item[0])
            ),
            cohomology_class=f"res({self.cohomology_class}, {sub_cover_id})",
            description=f"Restriction of [{self.description}] to {sub_cover_id}",
        )

    def direct_sum(self, other: CechObstruction) -> CechObstruction:
        """Direct sum of two obstruction classes in H¹."""
        return CechObstruction(
            cover_id=f"{self.cover_id}⊕{other.cover_id}",
            cocycle=self.cocycle | other.cocycle,
            cohomology_class=f"({self.cohomology_class}) ⊕ ({other.cohomology_class})",
            description=f"Direct sum: {self.description} | {other.description}",
        )

    def __str__(self) -> str:
        status = "trivial" if self.is_trivial() else f"non-trivial({len(self.cocycle)} terms)"
        return f"CechObstruction[{self.cohomology_class}]({status})"


# ---------------------------------------------------------------------------
# Frozen dataclasses for the encoding pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExceptionValuedException:
    """An exception instance together with its semantic category and encoding metadata.

    This is the first-class value that appears in the Err branch of Result[T].
    It carries both the runtime exception and its static encoding information.

    Fields:
        exc_type     — the Python exception class name (e.g. "ValueError")
        message      — the exception message string
        category     — the ExceptionCategory (RECOVERABLE, FATAL, etc.)
        operation    — the Python operation that raised this exception
        path_id      — the ExceptionPath (NORMAL, CAUGHT, UNCAUGHT, etc.)
        z3_sort_name — the Z3 sort name for this exception type in the encoding
        trust        — the TrustTier at which this exception path was certified
        uid          — a unique identifier for this exception value instance
    """

    exc_type:     str           # e.g. "ValueError"
    message:      str           # e.g. "invalid literal for int()"
    category:     ExceptionCategory
    operation:    str           # e.g. "int_conversion"
    path_id:      ExceptionPath
    z3_sort_name: str           # e.g. "ExcValuerError"
    trust:        TrustTier
    uid:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def is_recoverable(self) -> bool:
        """True iff this exception is semantically recoverable."""
        return self.category == ExceptionCategory.RECOVERABLE

    def to_z3_term(self) -> str:
        """Generate a Z3-style term for this exception value."""
        return f"Err({self.z3_sort_name}, \"{self.message}\")"


@dataclass(frozen=True)
class LiftedOperation:
    """The result of lifting a partial Python operation to a total Result[T].

    A lifted operation replaces a potentially-raising Python operation with a
    total function returning Result[T] = Ok(T) | Err(ExcType). This dataclass
    records all information needed to reconstruct the Z3 encoding.

    Fields:
        operation_id     — unique identifier for this operation instance
        original_expr    — the original Python expression (as string)
        result_type      — the type T in Result[T]
        normal_term      — the Z3 term for the Ok(v) branch
        exception_terms  — tuple of ExceptionValuedException for each Err branch
        strategy         — which LiftingStrategy was applied
        mode             — the EncodeMode used
        guard_formula    — optional Z3 guard formula (for CHECKED_ARITH, BOOL_WITH_GUARD)
        trust            — trust level of the lifting
    """

    operation_id:    str
    original_expr:   str
    result_type:     str
    normal_term:     str
    exception_terms: tuple  # tuple[ExceptionValuedException, ...]
    strategy:        LiftingStrategy
    mode:            EncodeMode
    guard_formula:   Optional[str]
    trust:           TrustTier

    def all_paths(self) -> List[ExceptionPath]:
        """Return all execution paths represented in this lifted operation."""
        paths = [ExceptionPath.NORMAL]
        for exc in self.exception_terms:
            paths.append(exc.path_id)
        return paths

    def to_z3_decl(self) -> str:
        """Generate a Z3 datatype declaration for this lifted operation's result type."""
        exc_constructors = " | ".join(e.to_z3_term() for e in self.exception_terms)
        ok_branch = f"Ok(value: {self.result_type})"
        if exc_constructors:
            return f"Result[{self.result_type}] ::= {ok_branch} | {exc_constructors}"
        return f"Result[{self.result_type}] ::= {ok_branch}"


@dataclass(frozen=True)
class ExceptionSemanticsEncoding:
    """The complete Z3 encoding of a Python expression's exception semantics.

    This dataclass bundles together:
    - The lifted operation (Result[T] type + constructors)
    - The judgment asserting the encoding is correct
    - Any Čech obstructions blocking a clean global encoding
    - The set of proof obligations generated

    Fields:
        encoding_id    — unique identifier
        lifted_op      — the LiftedOperation that was encoded
        judgment       — the Judgment (c, φ, A, E, O, B, T, Π) for this encoding
        obstruction    — the CechObstruction (may be trivial if no gluing problem)
        obligations    — tuple of obligation strings to be discharged
        z3_assertions  — tuple of Z3 assertion strings generated
        metadata       — tuple of (key, value) pairs (frozen-friendly)
    """

    encoding_id:   str
    lifted_op:     LiftedOperation
    judgment:      Judgment
    obstruction:   CechObstruction
    obligations:   tuple   # tuple[str, ...]
    z3_assertions: tuple   # tuple[str, ...]
    metadata:      tuple   # tuple of (key, value) pairs

    def is_clean(self) -> bool:
        """True iff there are no outstanding obligations and no non-trivial obstruction."""
        return len(self.obligations) == 0 and self.obstruction.is_trivial()

    def get_metadata(self, key: str) -> Optional[Any]:
        """Retrieve a metadata value by key."""
        for k, v in self.metadata:
            if k == key:
                return v
        return None


@dataclass(frozen=True)
class ResultTypeDeclaration:
    """A Z3 datatype declaration for Result[T] = Ok(T) | Err(ExcType).

    This captures the full type-level information needed to declare the
    Result type in a Z3 context. It is used by the encoder to emit the
    correct sort declarations before encoding any lifted operations.

    Fields:
        type_name      — e.g. "Result_int" or "Result_str"
        value_type     — the T in Result[T]
        exc_sorts      — tuple of Z3 sort names for exception types
        ok_constructor — Z3 constructor term for the Ok branch
        err_constructor — Z3 constructor term for the Err branch
        z3_declaration — the full Z3 datatype declaration string
        is_recursive   — True if the result type is recursively defined
        schema_version — version of the declaration schema
    """

    type_name:       str
    value_type:      str
    exc_sorts:       tuple   # tuple[str, ...]
    ok_constructor:  str
    err_constructor: str
    z3_declaration:  str
    is_recursive:    bool
    schema_version:  str = "1.0"

    def render(self) -> str:
        """Render the full Z3 datatype declaration."""
        return self.z3_declaration

    def constructor_for(self, path: ExceptionPath) -> str:
        """Return the appropriate constructor term for a given execution path."""
        if path == ExceptionPath.NORMAL:
            return self.ok_constructor
        return self.err_constructor


@dataclass(frozen=True)
class ExceptionObligationBundle:
    """A bundle of proof obligations arising from exception-valued semantics.

    When encoding an exception-valued operation, multiple proof obligations are
    generated. This bundle groups them for tracking and discharge.

    Fields:
        bundle_id      — unique identifier for this bundle
        source_expr    — the Python expression that generated the obligations
        obligations    — tuple of (obligation_id, obligation_text) pairs
        trust_required — minimum TrustTier needed to discharge all obligations
        judgment       — the Judgment that owns this bundle
        discharged     — tuple of already-discharged obligation IDs
    """

    bundle_id:      str
    source_expr:    str
    obligations:    tuple   # tuple of (str, str) pairs: (id, text)
    trust_required: TrustTier
    judgment:       Judgment
    discharged:     tuple = ()  # tuple[str, ...]

    def pending(self) -> List[Tuple[str, str]]:
        """Return the list of obligations not yet discharged."""
        discharged_ids = set(self.discharged)
        return [(oid, otext) for (oid, otext) in self.obligations if oid not in discharged_ids]

    def is_fully_discharged(self) -> bool:
        """True iff all obligations in this bundle have been discharged."""
        return len(self.pending()) == 0

    def discharge(self, obligation_id: str) -> ExceptionObligationBundle:
        """Return a new bundle with the given obligation marked as discharged."""
        return dataclasses.replace(self, discharged=self.discharged + (obligation_id,))


# ---------------------------------------------------------------------------
# ExceptionPatternLibrary — catalogue of known exception patterns
# ---------------------------------------------------------------------------

class ExceptionPatternLibrary:
    """Catalogue of known Python exception-raising patterns and their liftings.

    This library maps Python operation kinds to their canonical lifted forms,
    including the exception types they may raise and the preferred encoding
    strategy for each.

    The library is used by ExceptionSemanticsEncoder to look up how to lift
    each operation it encounters.
    """

    # Internal registry: operation_kind -> {exc_type: ExceptionCategory}
    _patterns: ClassVar[Dict[str, Dict[str, ExceptionCategory]]] = {
        "subscript": {
            "IndexError": ExceptionCategory.PARTIAL,
            "KeyError":   ExceptionCategory.PARTIAL,
            "TypeError":  ExceptionCategory.CONTRACT,
        },
        "int_conversion": {
            "ValueError":   ExceptionCategory.PARTIAL,
            "TypeError":    ExceptionCategory.CONTRACT,
            "OverflowError": ExceptionCategory.RECOVERABLE,
        },
        "attribute_access": {
            "AttributeError": ExceptionCategory.PARTIAL,
        },
        "call": {
            "TypeError":    ExceptionCategory.CONTRACT,
            "RuntimeError": ExceptionCategory.RECOVERABLE,
        },
        "division": {
            "ZeroDivisionError":  ExceptionCategory.PARTIAL,
            "FloatingPointError": ExceptionCategory.ENVIRONMENTAL,
        },
        "iteration": {
            "StopIteration": ExceptionCategory.PARTIAL,
            "TypeError":     ExceptionCategory.CONTRACT,
        },
        "open_file": {
            "FileNotFoundError": ExceptionCategory.ENVIRONMENTAL,
            "PermissionError":   ExceptionCategory.ENVIRONMENTAL,
            "OSError":           ExceptionCategory.ENVIRONMENTAL,
        },
    }

    def __init__(self) -> None:
        """Initialise with a copy of the class-level pattern registry."""
        self._local: Dict[str, Dict[str, ExceptionCategory]] = copy.deepcopy(self._patterns)

    def lookup(self, operation_kind: str) -> Dict[str, ExceptionCategory]:
        """Return the exception map for a given operation kind.

        Returns an empty dict if the operation kind is not in the library.
        """
        return dict(self._local.get(operation_kind, {}))

    def register(
        self,
        operation_kind: str,
        exc_type: str,
        category: ExceptionCategory,
    ) -> None:
        """Register a new exception type for an operation kind."""
        if operation_kind not in self._local:
            self._local[operation_kind] = {}
        self._local[operation_kind][exc_type] = category
        _log.debug("Registered %s -> %s: %s", operation_kind, exc_type, category.value)

    def all_operations(self) -> List[str]:
        """Return all registered operation kinds."""
        return sorted(self._local.keys())

    def strategy_for(self, operation_kind: str) -> LiftingStrategy:
        """Return the preferred lifting strategy for an operation kind."""
        pref = LIFTING_PREFERENCE.get(operation_kind, "result_type")
        try:
            return LiftingStrategy(pref)
        except ValueError:
            return LiftingStrategy.RESULT_TYPE

    def exception_categories_for(self, operation_kind: str) -> List[ExceptionCategory]:
        """Return all exception categories for an operation kind (de-duplicated)."""
        cats = list(self._local.get(operation_kind, {}).values())
        return list(dict.fromkeys(cats))  # preserve insertion order, de-dup


# ---------------------------------------------------------------------------
# ResultTypeRegistry — manages Result[T] type declarations
# ---------------------------------------------------------------------------

class ResultTypeRegistry:
    """Registry of Result[T] type declarations for Z3 encoding.

    Each distinct (value_type, frozenset_of_exc_types) combination gets exactly
    one ResultTypeDeclaration. The registry ensures declarations are not duplicated.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._decls: Dict[str, ResultTypeDeclaration] = {}
        self._lookup: Dict[Tuple[str, FrozenSet[str]], str] = {}

    def get_or_create(
        self,
        value_type: str,
        exc_types: FrozenSet[str],
        *,
        mode: EncodeMode = EncodeMode.STRICT,
    ) -> ResultTypeDeclaration:
        """Return an existing declaration or create a new one.

        The declaration is keyed on (value_type, frozenset(exc_types)).
        """
        key = (value_type, exc_types)
        if key in self._lookup:
            return self._decls[self._lookup[key]]

        type_name = _build_result_type_name(value_type, exc_types)
        ok_ctor   = _build_ok_constructor(value_type)
        err_ctor  = _build_err_constructor(exc_types)
        decl_text = RESULT_TYPE_TEMPLATE.format(
            T=value_type,
            exc_types=" | ".join(sorted(exc_types)) if exc_types else "Never",
        )
        decl = ResultTypeDeclaration(
            type_name=type_name,
            value_type=value_type,
            exc_sorts=tuple(sorted(exc_types)),
            ok_constructor=ok_ctor,
            err_constructor=err_ctor,
            z3_declaration=decl_text,
            is_recursive=False,
        )
        self._decls[type_name] = decl
        self._lookup[key] = type_name
        _log.debug("Registered Result type: %s", type_name)
        return decl

    def all_declarations(self) -> List[ResultTypeDeclaration]:
        """Return all registered ResultTypeDeclarations."""
        return list(self._decls.values())

    def declaration_count(self) -> int:
        """Return the number of registered declarations."""
        return len(self._decls)


# ---------------------------------------------------------------------------
# ExceptionSemanticsEncoder — core encoding engine
# ---------------------------------------------------------------------------

class ExceptionSemanticsEncoder:
    """Core engine for encoding Python exception semantics into Z3.

    Given a Python expression and its possible exception types, this encoder
    produces an ExceptionSemanticsEncoding that represents the expression's
    semantics as a lifted Result[T] type in Z3.

    The encoder follows the theory invariants:
    - Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans
    - Trust is an ordered algebra — never a float
    - Obstructions are Čech H¹ cohomology classes

    Usage::

        encoder = ExceptionSemanticsEncoder(mode=EncodeMode.STRICT)
        enc = encoder.encode("x[i]", "subscript", context={"x": "list", "i": "int"})
        print(enc.lifted_op.to_z3_decl())
    """

    def __init__(
        self,
        mode: EncodeMode = EncodeMode.STRICT,
        library: Optional[ExceptionPatternLibrary] = None,
        registry: Optional[ResultTypeRegistry] = None,
    ) -> None:
        """Initialise the encoder.

        Args:
            mode:     The EncodeMode controlling how exception paths are materialised.
            library:  The ExceptionPatternLibrary. Defaults to a fresh instance.
            registry: The ResultTypeRegistry. Defaults to a fresh instance.
        """
        self.mode     = mode
        self.library  = library or ExceptionPatternLibrary()
        self.registry = registry or ResultTypeRegistry()
        self._counter = itertools.count(1)

    def encode(
        self,
        expression: str,
        operation_kind: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        result_type: str = "Any",
        trust: TrustTier = TrustTier.PROPOSAL,
    ) -> ExceptionSemanticsEncoding:
        """Encode a Python expression's exception semantics.

        Args:
            expression:     The Python expression string (e.g. "x[i]").
            operation_kind: The kind of operation (key in EXCEPTION_TRIGGERS).
            context:        Optional variable type environment.
            result_type:    The T in Result[T] for the normal-path value.
            trust:          The initial trust tier for the encoding.

        Returns:
            An ExceptionSemanticsEncoding bundling the lifted operation,
            judgment, obstruction, and proof obligations.
        """
        ctx = context or {}
        encoding_id = (
            f"enc_{next(self._counter):04d}_"
            f"{hashlib.md5(expression.encode()).hexdigest()[:6]}"
        )

        # 1. Look up exception patterns for this operation
        exc_map  = self.library.lookup(operation_kind)
        strategy = self.library.strategy_for(operation_kind)

        # 2. Build ExceptionValuedException instances for each exc type
        exc_values = tuple(
            ExceptionValuedException(
                exc_type=exc_type,
                message=f"{exc_type} from {operation_kind}({expression!r})",
                category=category,
                operation=operation_kind,
                path_id=ExceptionPath.UNCAUGHT,
                z3_sort_name=f"Exc{exc_type}",
                trust=trust,
            )
            for exc_type, category in exc_map.items()
        )

        # 3. Build the normal-path Z3 term
        normal_term = encode_normal_path(expression, result_type, operation_kind)

        # 4. Build the LiftedOperation
        lifted_op = LiftedOperation(
            operation_id=encoding_id,
            original_expr=expression,
            result_type=result_type,
            normal_term=normal_term,
            exception_terms=exc_values,
            strategy=strategy,
            mode=self.mode,
            guard_formula=None,
            trust=trust,
        )

        # 5. Get or create the ResultTypeDeclaration
        exc_sort_names: FrozenSet[str] = frozenset(e.exc_type for e in exc_values)
        self.registry.get_or_create(result_type, exc_sort_names, mode=self.mode)

        # 6. Build proof obligations
        obligations = tuple(extract_exception_obligations(lifted_op, ctx))

        # 7. Build the Judgment (c, φ, A, E, O, B, T, Π)
        judgment = Judgment(
            context=ctx,
            formula=(
                f"Result[{result_type}] correctly encodes "
                f"{expression!r} via {operation_kind}"
            ),
            assumptions=tuple(f"ctx:{k}={v}" for k, v in ctx.items()),
            evidence=(),
            obligations=obligations,
            burden=f"ExceptionSemanticsEncoder({self.mode.value})",
            trust=trust,
            provenance=(
                ProvenanceSource.ORACLE if _JUGEO_JUDGMENTS else "oracle"
            ),
        )

        # 8. Build the CechObstruction
        obstruction = _build_cech_obstruction(encoding_id, lifted_op, self.mode)

        # 9. Build Z3 assertions for each exception path
        assertions = tuple(
            encode_exception_path(expression, exc_val, operation_kind)
            for exc_val in exc_values
        )

        return ExceptionSemanticsEncoding(
            encoding_id=encoding_id,
            lifted_op=lifted_op,
            judgment=judgment,
            obstruction=obstruction,
            obligations=obligations,
            z3_assertions=assertions,
            metadata=(("mode", self.mode.value), ("operation_kind", operation_kind)),
        )

    def encode_batch(
        self,
        expressions: List[Tuple[str, str]],
        *,
        trust: TrustTier = TrustTier.PROPOSAL,
    ) -> List[ExceptionSemanticsEncoding]:
        """Encode a batch of (expression, operation_kind) pairs.

        Args:
            expressions: List of (expression, operation_kind) tuples.
            trust:       Uniform initial trust tier for all encodings.

        Returns:
            A list of ExceptionSemanticsEncoding instances in the same order.
        """
        return [self.encode(expr, kind, trust=trust) for expr, kind in expressions]


# ---------------------------------------------------------------------------
# ExceptionValuedStructuralSemanticsAnalyzer
# ---------------------------------------------------------------------------

class ExceptionValuedStructuralSemanticsAnalyzer:
    """Analyzes a set of ExceptionSemanticsEncodings for structural properties.

    This analyzer checks global invariants across a collection of encodings:
    - Are all exception paths covered?
    - Are there non-trivial Čech obstructions?
    - Are all proof obligations within the required trust threshold?
    - Are there exception-type conflicts between encodings?

    The analysis produces a report as a list of Judgments.
    """

    def __init__(self, trust_threshold: TrustTier = TrustTier.VERIFIED) -> None:
        """Initialise the analyzer.

        Args:
            trust_threshold: Minimum trust required for an encoding to be
                             considered structurally sound.
        """
        self.trust_threshold = trust_threshold
        self._encodings: List[ExceptionSemanticsEncoding] = []

    def add_encoding(self, enc: ExceptionSemanticsEncoding) -> None:
        """Add an encoding to the analysis set."""
        self._encodings.append(enc)

    def add_batch(self, encs: List[ExceptionSemanticsEncoding]) -> None:
        """Add multiple encodings to the analysis set."""
        self._encodings.extend(encs)

    def analyze(self) -> List[Judgment]:
        """Run the full structural analysis.

        Returns:
            A list of Judgments, one per encoding, plus one summary judgment.
        """
        judgments: List[Judgment] = []
        for enc in self._encodings:
            j = self._analyze_single(enc)
            judgments.append(j)
        summary = self._build_summary_judgment(judgments)
        judgments.append(summary)
        return judgments

    def _analyze_single(self, enc: ExceptionSemanticsEncoding) -> Judgment:
        """Analyze a single encoding and return a Judgment about it."""
        issues: List[str] = []

        # Check trust admissibility
        if not enc.judgment.trust.is_admissible(self.trust_threshold):
            issues.append(
                f"trust {enc.judgment.trust.name} below threshold "
                f"{self.trust_threshold.name}"
            )

        # Check for non-trivial obstructions
        if not enc.obstruction.is_trivial():
            issues.append(
                f"non-trivial Čech obstruction: {enc.obstruction.cohomology_class}"
            )

        # Check outstanding obligations
        if enc.obligations:
            issues.append(f"{len(enc.obligations)} outstanding obligations")

        if issues:
            return Judgment(
                context={"encoding_id": enc.encoding_id},
                formula=f"encoding {enc.encoding_id!r} has structural issues",
                assumptions=(),
                evidence=(),
                obligations=("; ".join(issues),),
                burden="ExceptionValuedStructuralSemanticsAnalyzer",
                trust=TrustTier.PROPOSAL,
                provenance="analysis",
            )
        return Judgment(
            context={"encoding_id": enc.encoding_id},
            formula=f"encoding {enc.encoding_id!r} is structurally sound",
            assumptions=(),
            evidence=(f"trust={enc.judgment.trust.name}", "obstruction=trivial"),
            obligations=(),
            burden="ExceptionValuedStructuralSemanticsAnalyzer",
            trust=enc.judgment.trust,
            provenance="analysis",
        )

    def _build_summary_judgment(self, judgments: List[Judgment]) -> Judgment:
        """Build a summary Judgment over all per-encoding judgments."""
        n_clean = sum(1 for j in judgments if j.is_fully_discharged())
        n_total = len(judgments)
        combined_trust = functools.reduce(
            lambda a, b: a.meet(b),
            (j.trust for j in judgments),
            TrustTier.PROOF_BACKED,
        )
        return Judgment(
            context={"analyzer": "ExceptionValuedStructuralSemanticsAnalyzer"},
            formula=f"{n_clean}/{n_total} encodings are structurally sound",
            assumptions=(),
            evidence=tuple(j.formula for j in judgments[:5]),
            obligations=tuple(obl for j in judgments for obl in j.obligations),
            burden="ExceptionValuedStructuralSemanticsAnalyzer.summary",
            trust=combined_trust,
            provenance="analysis",
        )


# ---------------------------------------------------------------------------
# ExceptionValuedStructuralSemanticsWitness
# ---------------------------------------------------------------------------

class ExceptionValuedStructuralSemanticsWitness:
    """A witness for the structural soundness of an exception-valued encoding.

    A witness is a concrete artifact (runtime test, solver certificate, etc.)
    that discharges one or more proof obligations in an ExceptionSemanticsEncoding.

    Invariant: A witness is NEVER a boolean. It is a structured record that
    can be independently verified.
    """

    def __init__(
        self,
        witness_id: str,
        encoding: ExceptionSemanticsEncoding,
        kind: EvidenceItemKind,
    ) -> None:
        """Initialise the witness."""
        self.witness_id = witness_id
        self.encoding   = encoding
        self.kind       = kind
        self._discharged: List[str] = []
        self._artifacts: List[Any]  = []

    def add_artifact(self, artifact: Any) -> None:
        """Add a concrete artifact (e.g. solver proof, runtime trace) to the witness."""
        self._artifacts.append(artifact)

    def discharge_obligation(self, obligation: str) -> None:
        """Mark an obligation as discharged by this witness."""
        self._discharged.append(obligation)

    def to_judgment(self) -> Judgment:
        """Convert this witness to a Judgment that can be merged into the encoding."""
        return Judgment(
            context={
                "witness_id":  self.witness_id,
                "encoding_id": self.encoding.encoding_id,
            },
            formula=(
                f"Witness {self.witness_id!r} discharges "
                f"{len(self._discharged)} obligations"
            ),
            assumptions=(),
            evidence=tuple(str(a) for a in self._artifacts),
            obligations=tuple(
                o for o in self.encoding.obligations if o not in self._discharged
            ),
            burden=f"Witness({self.kind})",
            trust=(
                TrustTier.RUNTIME_WITNESSED
                if self.kind == EvidenceItemKind.RUNTIME_WITNESS
                else TrustTier.VERIFIED
            ),
            provenance=self.kind,
        )

    def __repr__(self) -> str:
        return (
            f"ExceptionValuedStructuralSemanticsWitness("
            f"id={self.witness_id!r}, discharged={len(self._discharged)})"
        )


# ---------------------------------------------------------------------------
# ExceptionValuedStructuralSemanticsCoordinator
# ---------------------------------------------------------------------------

class ExceptionValuedStructuralSemanticsCoordinator:
    """Top-level coordinator for the exception-valued structural semantics pipeline.

    This class orchestrates the full workflow:
    1. Accepts Python expressions to be encoded
    2. Delegates encoding to ExceptionSemanticsEncoder
    3. Analyzes the encodings with ExceptionValuedStructuralSemanticsAnalyzer
    4. Manages witnesses and obligation discharge
    5. Returns final Judgments and a summary CechObstruction

    Invariants:
    - Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans
    - Trust is an ordered algebra — never a float
    - Obstructions are Čech H¹ cohomology classes
    """

    def __init__(
        self,
        mode: EncodeMode = EncodeMode.STRICT,
        trust_threshold: TrustTier = TrustTier.VERIFIED,
    ) -> None:
        """Initialise the coordinator."""
        self.mode            = mode
        self.trust_threshold = trust_threshold
        self.encoder         = ExceptionSemanticsEncoder(mode=mode)
        self.analyzer        = ExceptionValuedStructuralSemanticsAnalyzer(trust_threshold)
        self._encodings:  List[ExceptionSemanticsEncoding] = []
        self._witnesses:  List[ExceptionValuedStructuralSemanticsWitness] = []
        self._judgments:  List[Judgment] = []

    def submit(
        self,
        expression: str,
        operation_kind: str,
        *,
        result_type: str = "Any",
        context: Optional[Dict[str, Any]] = None,
        trust: TrustTier = TrustTier.PROPOSAL,
    ) -> ExceptionSemanticsEncoding:
        """Submit a Python expression for encoding."""
        enc = self.encoder.encode(
            expression, operation_kind,
            context=context, result_type=result_type, trust=trust,
        )
        self._encodings.append(enc)
        self.analyzer.add_encoding(enc)
        _log.info("Submitted encoding %s for %r", enc.encoding_id, expression)
        return enc

    def submit_batch(
        self,
        items: List[Tuple[str, str]],
        *,
        trust: TrustTier = TrustTier.PROPOSAL,
    ) -> List[ExceptionSemanticsEncoding]:
        """Submit multiple (expression, operation_kind) pairs at once."""
        return [self.submit(expr, kind, trust=trust) for expr, kind in items]

    def add_witness(
        self,
        encoding: ExceptionSemanticsEncoding,
        kind: EvidenceItemKind,
        *,
        artifacts: Optional[List[Any]] = None,
        discharged: Optional[List[str]] = None,
    ) -> ExceptionValuedStructuralSemanticsWitness:
        """Create and register a witness for an encoding."""
        wid = f"wit_{uuid.uuid4().hex[:8]}"
        w   = ExceptionValuedStructuralSemanticsWitness(wid, encoding, kind)
        for a in (artifacts or []):
            w.add_artifact(a)
        for o in (discharged or []):
            w.discharge_obligation(o)
        self._witnesses.append(w)
        return w

    def run_analysis(self) -> List[Judgment]:
        """Run the structural analysis and return all Judgments."""
        self._judgments = self.analyzer.analyze()
        return self._judgments

    def global_obstruction(self) -> CechObstruction:
        """Compute the global Čech obstruction by combining all per-encoding obstructions."""
        if not self._encodings:
            return CechObstruction(
                cover_id="empty",
                cocycle=frozenset(),
                cohomology_class="0",
                description="No encodings submitted",
            )
        result = self._encodings[0].obstruction
        for enc in self._encodings[1:]:
            result = result.direct_sum(enc.obstruction)
        return result

    def summary(self) -> Dict[str, Any]:
        """Return a summary dictionary of the coordinator's state."""
        return {
            "mode":               self.mode.value,
            "trust_threshold":    self.trust_threshold.name,
            "encodings":          len(self._encodings),
            "witnesses":          len(self._witnesses),
            "judgments":          len(self._judgments),
            "clean_encodings":    sum(1 for e in self._encodings if e.is_clean()),
            "global_obstruction": str(self.global_obstruction()),
        }


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def encode_exception_semantics(
    expression: str,
    operation_kind: str,
    *,
    mode: EncodeMode = EncodeMode.STRICT,
    context: Optional[Dict[str, Any]] = None,
    result_type: str = "Any",
    trust: TrustTier = TrustTier.PROPOSAL,
) -> ExceptionSemanticsEncoding:
    """Encode the exception semantics of a Python expression.

    This is the primary entry point for encoding a single expression.
    It creates a fresh encoder and returns the ExceptionSemanticsEncoding.

    Args:
        expression:     The Python expression string.
        operation_kind: The kind of operation (key in EXCEPTION_TRIGGERS).
        mode:           Encoding mode (STRICT, OPTIMISTIC, LAZY, SYMBOLIC).
        context:        Optional variable type environment.
        result_type:    The T in Result[T].
        trust:          Initial trust tier for the encoding.

    Returns:
        An ExceptionSemanticsEncoding representing the lifted operation.

    Example::

        enc = encode_exception_semantics("x[i]", "subscript",
                                         context={"x": "list[int]", "i": "int"})
        print(enc.lifted_op.to_z3_decl())
    """
    encoder = ExceptionSemanticsEncoder(mode=mode)
    return encoder.encode(
        expression, operation_kind,
        context=context, result_type=result_type, trust=trust,
    )


def extract_exception_obligations(
    lifted_op: LiftedOperation,
    context: Dict[str, Any],
) -> List[str]:
    """Extract the proof obligations arising from a lifted operation.

    For each exception path in the lifted operation, generate an obligation
    that asserts:
    - The exception path is reachable (or unreachable, with proof)
    - The exception type is correctly classified
    - The guard formula (if any) is correctly formulated

    Args:
        lifted_op: The LiftedOperation whose obligations to extract.
        context:   The variable type environment.

    Returns:
        A list of obligation strings (never empty in STRICT mode).
    """
    obligations: List[str] = []

    # Well-formedness obligation for the result type
    obligations.append(
        f"WELL_FORMED: Result[{lifted_op.result_type}] is a valid Z3 datatype "
        f"for expression {lifted_op.original_expr!r}"
    )

    # Reachability obligation for each exception path
    for exc_val in lifted_op.exception_terms:
        obligations.append(
            f"REACHABILITY: {exc_val.exc_type} is reachable from "
            f"{lifted_op.original_expr!r} in context {context}"
        )

    # Normal path reachability
    obligations.append(
        f"NORMAL_PATH: Ok({lifted_op.result_type}) is reachable from "
        f"{lifted_op.original_expr!r}"
    )

    # Guard obligation for checked arithmetic
    if lifted_op.strategy == LiftingStrategy.CHECKED_ARITH and not lifted_op.guard_formula:
        obligations.append(
            f"GUARD_MISSING: CHECKED_ARITH strategy for {lifted_op.original_expr!r} "
            f"requires a guard formula but none was provided"
        )

    return obligations


def analyze_exception_paths(
    encoding: ExceptionSemanticsEncoding,
) -> Dict[ExceptionPath, List[str]]:
    """Analyze the exception paths in an encoding and return a path -> info map.

    For each path in the encoding (NORMAL, CAUGHT, UNCAUGHT, etc.), return
    a list of descriptive strings about that path.

    Args:
        encoding: The ExceptionSemanticsEncoding to analyze.

    Returns:
        A dict mapping ExceptionPath -> list of description strings.
    """
    result: Dict[ExceptionPath, List[str]] = defaultdict(list)
    trust_note = f"[trust={encoding.judgment.trust.name}]"

    # Normal path
    result[ExceptionPath.NORMAL].append(
        f"Ok({encoding.lifted_op.result_type}) — normal execution of "
        f"{encoding.lifted_op.original_expr!r} {trust_note}"
    )

    # Exception paths
    for exc_val in encoding.lifted_op.exception_terms:
        path = exc_val.path_id
        result[path].append(
            f"Err({exc_val.exc_type}) — {exc_val.category.value} exception "
            f"from {encoding.lifted_op.original_expr!r}: {exc_val.message} {trust_note}"
        )

    return dict(result)


def lift_to_result_type(
    expression: str,
    operation_kind: str,
    result_type: str = "Any",
    *,
    mode: EncodeMode = EncodeMode.STRICT,
) -> ResultTypeDeclaration:
    """Lift a Python expression to a Result[T] type declaration.

    This is a convenience wrapper that encodes the expression and extracts
    the ResultTypeDeclaration.

    Args:
        expression:     The Python expression.
        operation_kind: The operation kind.
        result_type:    The T in Result[T].
        mode:           Encoding mode.

    Returns:
        The ResultTypeDeclaration for Result[result_type].
    """
    enc = encode_exception_semantics(expression, operation_kind,
                                     mode=mode, result_type=result_type)
    exc_types: FrozenSet[str] = frozenset(
        e.exc_type for e in enc.lifted_op.exception_terms
    )
    return build_result_type_declaration(result_type, exc_types)


def build_result_type_declaration(
    value_type: str,
    exc_types: FrozenSet[str],
) -> ResultTypeDeclaration:
    """Build a ResultTypeDeclaration for Result[value_type].

    Args:
        value_type: The T in Result[T].
        exc_types:  The set of exception type names for the Err branch.

    Returns:
        A ResultTypeDeclaration with the appropriate Z3 constructor terms.
    """
    type_name = _build_result_type_name(value_type, exc_types)
    ok_ctor   = _build_ok_constructor(value_type)
    err_ctor  = _build_err_constructor(exc_types)
    decl_text = RESULT_TYPE_TEMPLATE.format(
        T=value_type,
        exc_types=" | ".join(sorted(exc_types)) if exc_types else "Never",
    )
    return ResultTypeDeclaration(
        type_name=type_name,
        value_type=value_type,
        exc_sorts=tuple(sorted(exc_types)),
        ok_constructor=ok_ctor,
        err_constructor=err_ctor,
        z3_declaration=decl_text,
        is_recursive=False,
    )


def encode_normal_path(
    expression: str,
    result_type: str,
    operation_kind: str,
) -> str:
    """Build the Z3 term for the normal (non-exception) execution path.

    The normal path is encoded as Ok(result_type, <expression_term>).

    Args:
        expression:     The Python expression string.
        result_type:    The type of the normal-path value.
        operation_kind: The kind of operation.

    Returns:
        A Z3 term string for the Ok branch.

    Example::

        encode_normal_path("x[i]", "int", "subscript")
        # => "Ok(int, subscript_result_x_i_)"
    """
    safe_expr = re.sub(r"[^a-zA-Z0-9_]", "_", expression)
    return f"Ok({result_type}, {operation_kind}_result_{safe_expr})"


def encode_exception_path(
    expression: str,
    exc_val: ExceptionValuedException,
    operation_kind: str,
) -> str:
    """Build the Z3 assertion for one exception path.

    The exception path is encoded as an Err(ExcType) constructor term
    paired with an assertion that the exception condition holds.

    Args:
        expression:     The Python expression string.
        exc_val:        The ExceptionValuedException for this path.
        operation_kind: The kind of operation.

    Returns:
        A Z3 assertion string for the Err branch.

    Example::

        encode_exception_path("x[i]", exc_index, "subscript")
        # => "(assert (=> cond (= result Err(ExcIndexError, ...)))) ; IndexError"
    """
    safe_expr = re.sub(r"[^a-zA-Z0-9_]", "_", expression)
    condition = f"{operation_kind}_raises_{exc_val.exc_type}_{safe_expr}"
    term      = exc_val.to_z3_term()
    return (
        f"(assert (=> {condition} (= result_{safe_expr} {term})))"
        f"  ; {exc_val.exc_type} on {expression!r} [{exc_val.category.value}]"
    )


def merge_exception_paths(
    encodings: List[ExceptionSemanticsEncoding],
) -> ExceptionSemanticsEncoding:
    """Merge multiple exception-valued encodings into one.

    When an expression has multiple encodings (e.g. from different call sites),
    merge them by taking the union of all exception paths and the meet of
    all trust tiers.

    Args:
        encodings: A non-empty list of ExceptionSemanticsEncodings to merge.

    Returns:
        A single merged ExceptionSemanticsEncoding.

    Raises:
        ValueError: If encodings is empty.
    """
    if not encodings:
        raise ValueError("Cannot merge an empty list of encodings")
    if len(encodings) == 1:
        return encodings[0]

    # Collect all exception values, de-duplicated by exc_type
    all_exc: List[ExceptionValuedException] = []
    seen: Set[str] = set()
    for enc in encodings:
        for exc_val in enc.lifted_op.exception_terms:
            if exc_val.exc_type not in seen:
                seen.add(exc_val.exc_type)
                all_exc.append(exc_val)

    # Meet of trust tiers
    merged_trust = functools.reduce(
        lambda a, b: a.meet(b),
        (enc.judgment.trust for enc in encodings),
        TrustTier.PROOF_BACKED,
    )

    base = encodings[0]
    merged_lifted = LiftedOperation(
        operation_id=f"merged_{base.lifted_op.operation_id}",
        original_expr=base.lifted_op.original_expr,
        result_type=base.lifted_op.result_type,
        normal_term=base.lifted_op.normal_term,
        exception_terms=tuple(all_exc),
        strategy=base.lifted_op.strategy,
        mode=base.lifted_op.mode,
        guard_formula=base.lifted_op.guard_formula,
        trust=merged_trust,
    )

    merged_obs = functools.reduce(
        lambda a, b: a.direct_sum(b),
        (enc.obstruction for enc in encodings),
    )

    all_obls = tuple(
        itertools.chain.from_iterable(enc.obligations for enc in encodings)
    )

    merged_judgment = Judgment(
        context={"merged_from": [e.encoding_id for e in encodings]},
        formula=f"Merged encoding of {base.lifted_op.original_expr!r}",
        assumptions=(),
        evidence=tuple(enc.encoding_id for enc in encodings),
        obligations=all_obls,
        burden="merge_exception_paths",
        trust=merged_trust,
        provenance="merge",
    )

    return ExceptionSemanticsEncoding(
        encoding_id=f"merged_{base.encoding_id}",
        lifted_op=merged_lifted,
        judgment=merged_judgment,
        obstruction=merged_obs,
        obligations=all_obls,
        z3_assertions=tuple(
            itertools.chain.from_iterable(enc.z3_assertions for enc in encodings)
        ),
        metadata=(("merged", True), ("count", len(encodings))),
    )


def classify_exception_risk(
    exc_val: ExceptionValuedException,
    context: Dict[str, Any],
) -> Tuple[ExceptionCategory, TrustTier]:
    """Classify the risk level of an exception value in a given context.

    Uses the exception category and context to determine both the semantic
    category and the trust tier appropriate for encoding this exception.

    Args:
        exc_val: The ExceptionValuedException to classify.
        context: The variable type environment.

    Returns:
        A (ExceptionCategory, TrustTier) pair.
    """
    cat = exc_val.category

    if cat == ExceptionCategory.PARTIAL:
        return cat, TrustTier.REVIEWED

    if cat == ExceptionCategory.CONTRACT:
        return cat, TrustTier.RUNTIME_WITNESSED

    if cat == ExceptionCategory.ENVIRONMENTAL:
        return cat, TrustTier.RUNTIME_WITNESSED

    if cat == ExceptionCategory.FATAL:
        return cat, TrustTier.PROPOSAL

    # RECOVERABLE: trust at REVIEWED for well-known exception types
    known_types = {"ValueError", "TypeError", "KeyError", "IndexError"}
    if exc_val.exc_type in known_types:
        return cat, TrustTier.REVIEWED

    return cat, TrustTier.PROPOSAL


def discharge_exception_obligation(
    obligation: str,
    encoding: ExceptionSemanticsEncoding,
    evidence: Any,
    *,
    trust: TrustTier = TrustTier.VERIFIED,
) -> Judgment:
    """Discharge a single exception obligation and return an updated Judgment.

    This function represents the act of providing evidence for one proof
    obligation. It returns a new Judgment with the obligation removed and
    the trust tier updated.

    Args:
        obligation: The obligation string to discharge.
        encoding:   The encoding the obligation belongs to.
        evidence:   The evidence artifact that discharges the obligation.
        trust:      The trust tier at which the evidence is accepted.

    Returns:
        An updated Judgment with the obligation discharged.

    Raises:
        JuGeoError: If the obligation is not found in the encoding.
    """
    if obligation not in encoding.obligations:
        if _JUGEO_ERRORS:
            raise_with_scope(
                "OBLIGATION_NOT_FOUND",
                message=(
                    f"Obligation {obligation!r} not in encoding "
                    f"{encoding.encoding_id}"
                ),
                provenance=encoding.encoding_id,
            )
        else:
            raise JuGeoError(
                f"[OBLIGATION_NOT_FOUND] Obligation {obligation!r} not in "
                f"encoding {encoding.encoding_id}"
            )

    updated_j = encoding.judgment.discharge_obligation(obligation)
    updated_j = updated_j.add_evidence(evidence)
    updated_j = updated_j.with_trust(encoding.judgment.trust.join(trust))
    return updated_j


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _operation_can_raise(operation_kind: str) -> bool:
    """Return True iff the given operation kind is known to raise exceptions.

    This is a conservative check: if the operation kind is in EXCEPTION_TRIGGERS
    and has at least one associated exception type, it can raise.

    Args:
        operation_kind: The Python operation kind to check.

    Returns:
        True if the operation can raise, False otherwise.
    """
    return (
        operation_kind in EXCEPTION_TRIGGERS
        and len(EXCEPTION_TRIGGERS[operation_kind]) > 0
    )


def _build_ok_constructor(value_type: str) -> str:
    """Build the Z3 Ok constructor term for a given value type.

    Args:
        value_type: The T in Result[T].

    Returns:
        A Z3 constructor string, e.g. "Ok_int(value: Int)".
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", value_type)
    return f"Ok_{safe}(value: {value_type})"


def _build_err_constructor(exc_types: FrozenSet[str]) -> str:
    """Build the Z3 Err constructor term for a set of exception types.

    Args:
        exc_types: The set of exception type names for the Err branch.

    Returns:
        A Z3 constructor string, e.g. "Err(exc_type: ValueError | KeyError, ...)".
    """
    if not exc_types:
        return "Err(exc_type: Never)"
    sorted_excs = " | ".join(sorted(exc_types))
    return f"Err(exc_type: {sorted_excs}, message: String)"


def _build_result_type_name(value_type: str, exc_types: FrozenSet[str]) -> str:
    """Build a stable, unique name for a Result[T] type.

    Uses a hash of the (value_type, sorted exc_types) tuple to ensure
    uniqueness across different exception type sets.

    Args:
        value_type: The T in Result[T].
        exc_types:  The set of exception type names.

    Returns:
        A string like "Result_int_a1b2c3".
    """
    safe_vt = re.sub(r"[^a-zA-Z0-9_]", "_", value_type)
    key     = f"{value_type}::{','.join(sorted(exc_types))}"
    digest  = hashlib.md5(key.encode()).hexdigest()[:6]
    return f"Result_{safe_vt}_{digest}"


def _build_cech_obstruction(
    encoding_id: str,
    lifted_op: LiftedOperation,
    mode: EncodeMode,
) -> CechObstruction:
    """Build the Čech obstruction for a lifted operation encoding.

    In STRICT mode, if there are multiple exception paths, a non-trivial
    cocycle is generated representing the gluing requirement. In OPTIMISTIC
    mode, the obstruction is always trivial.

    Args:
        encoding_id: The encoding identifier.
        lifted_op:   The lifted operation.
        mode:        The encoding mode.

    Returns:
        A CechObstruction (may be trivial).
    """
    if mode == EncodeMode.OPTIMISTIC or not lifted_op.exception_terms:
        return CechObstruction(
            cover_id=encoding_id,
            cocycle=frozenset(),
            cohomology_class="0",
            description=(
                f"Trivial obstruction for {lifted_op.original_expr!r} "
                f"(optimistic/no-exc)"
            ),
        )

    # Generate cocycle terms for each pair of exception paths
    exc_names = [e.exc_type for e in lifted_op.exception_terms]
    pairs: List[Tuple[str, str]] = [
        (f"{ea}∩{eb}", f"section_diff({ea},{eb})")
        for ea, eb in itertools.combinations(exc_names, 2)
    ]
    # Add normal-path / exception-path cocycle terms
    for exc_name in exc_names:
        pairs.append((f"normal∩{exc_name}", f"section_diff(Ok,{exc_name})"))

    h1_class = hashlib.md5(
        (encoding_id + ":".join(exc_names)).encode()
    ).hexdigest()[:8]

    return CechObstruction(
        cover_id=encoding_id,
        cocycle=frozenset(pairs),
        cohomology_class=f"H1_{h1_class}",
        description=(
            f"Gluing obstruction for {lifted_op.original_expr!r}: "
            f"normal path vs {len(exc_names)} exception path(s)"
        ),
    )



# ---------------------------------------------------------------------------
# Required sheaf-theory interface classes for exception encoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExceptionValueEncoding:
    """The canonical encoding of a Python exception value as a sheaf section.

    An exception value is encoded as a stalk over the exceptional execution
    path.  This dataclass is the primary output type of ExceptionEncoder.

    Fields
    ------
    encoding_id    : unique identifier
    exc_type       : Python exception class name (e.g. "ValueError")
    message        : exception message string
    path_id        : which ExceptionPath this stalk lives on
    z3_term        : Z3 term encoding this exception value
    trust          : TrustTier of this encoding
    judgment       : the 8-tuple Judgment backing this encoding
    obstruction    : the Čech H¹ obstruction class (trivial if no gluing issue)
    """

    encoding_id: str
    exc_type: str
    message: str
    path_id: ExceptionPath
    z3_term: str
    trust: TrustTier
    judgment: Judgment
    obstruction: CechObstruction

    def is_trivial(self) -> bool:
        """Return True iff the obstruction is trivial (gluing succeeds)."""
        return self.obstruction.is_trivial()

    def to_z3_assert(self) -> str:
        """Return a Z3 assert statement for this exception value."""
        return f"(assert (=> {self.path_id.value}_path (= exc_val {self.z3_term})))"

    def to_judgment_tuple(self) -> tuple:
        """Return (c, φ, A, E, O, B, T, Π) for this encoding."""
        return (
            self.encoding_id,
            f"exception_value_encoding({self.exc_type})",
            "ExceptionValueEncoding",
            self.z3_term,
            "" if self.is_trivial() else "GLUING_OBSTRUCTION",
            f"path:{self.path_id.value}",
            self.trust.name,
            "exception_value_encoding",
        )

    def with_trust(self, tier: TrustTier) -> ExceptionValueEncoding:
        """Return a new encoding with updated trust tier."""
        return dataclasses.replace(self, trust=tier)

    def describe(self) -> str:
        """Return a human-readable description."""
        return (
            f"ExceptionValueEncoding[{self.encoding_id}]: "
            f"{self.exc_type}({self.message!r}) "
            f"path={self.path_id.value} trust={self.trust.name}"
        )


@dataclass(frozen=True)
class ThrowSection:
    """A local section of the exception sheaf over a throw site.

    A throw site is a syntactic location where a Python exception can be raised.
    The ThrowSection records the stalk at that site: the exception type, message,
    and the Z3 encoding of the throw condition.

    Fields
    ------
    section_id   : unique identifier
    exc_type     : exception class name
    message      : exception message
    site         : source location (e.g. "module.py:42")
    throw_cond   : Z3 condition under which the throw occurs
    trust        : TrustTier
    judgment     : 8-tuple Judgment for this throw section
    """

    section_id: str
    exc_type: str
    message: str
    site: str
    throw_cond: str
    trust: TrustTier
    judgment: Judgment

    def to_z3_assert(self) -> str:
        """Return the Z3 assertion for this throw site."""
        return f"(assert (=> {self.throw_cond} (raises {self.exc_type})))"

    def to_exception_value(self) -> ExceptionValueEncoding:
        """Lift this throw section into a full ExceptionValueEncoding."""
        obs = CechObstruction(
            cover_id=self.section_id,
            cocycle=frozenset(),
            cohomology_class="0",
            description="trivial: single throw site",
        )
        return ExceptionValueEncoding(
            encoding_id=f"exc_{self.section_id}",
            exc_type=self.exc_type,
            message=self.message,
            path_id=ExceptionPath.UNCAUGHT,
            z3_term=f"Err({self.exc_type}, \"{self.message}\")",
            trust=self.trust,
            judgment=self.judgment,
            obstruction=obs,
        )

    def to_judgment_tuple(self) -> tuple:
        """Return (c, φ, A, E, O, B, T, Π) as 8 strings."""
        return (
            self.section_id,
            f"throw_section({self.exc_type})",
            "ThrowSection",
            self.site,
            self.throw_cond,
            f"exc_type:{self.exc_type}",
            self.trust.name,
            "throw_section",
        )

    def restrict_to_caught(self) -> ThrowSection:
        """Return a new ThrowSection representing the caught path."""
        return dataclasses.replace(
            self,
            section_id=f"{self.section_id}_caught",
            throw_cond=f"({self.throw_cond} AND caught)",
        )

    def obligation_string(self) -> str:
        """Return the proof obligation for this throw section."""
        return (
            f"REACHABILITY: {self.exc_type} is reachable at {self.site!r} "
            f"under condition {self.throw_cond!r}"
        )


@dataclass(frozen=True)
class CatchHandler:
    """A handler for one exception type in a try/except block.

    A CatchHandler encodes the local section of the exception sheaf over the
    except branch for one exception type.  Multiple CatchHandlers correspond to
    multiple except clauses.

    Fields
    ------
    handler_id    : unique identifier
    exc_type      : exception class caught by this handler (or "Exception" for bare)
    handler_body  : description of what the handler does
    catch_cond    : Z3 condition asserting the exception was caught
    result_term   : Z3 term for the handler's return value
    trust         : TrustTier
    judgment      : 8-tuple Judgment for this handler
    """

    handler_id: str
    exc_type: str
    handler_body: str
    catch_cond: str
    result_term: str
    trust: TrustTier
    judgment: Judgment

    def handles(self, exc_type: str) -> bool:
        """Return True iff this handler catches the given exception type."""
        return self.exc_type in ("Exception", "BaseException", exc_type)

    def to_z3_assert(self) -> str:
        """Return the Z3 assertion for this catch handler."""
        return (
            f"(assert (=> {self.catch_cond} "
            f"(= handler_result_{self.handler_id} {self.result_term})))"
        )

    def to_judgment_tuple(self) -> tuple:
        """Return (c, φ, A, E, O, B, T, Π) as 8 strings."""
        return (
            self.handler_id,
            f"catch_handler({self.exc_type})",
            "CatchHandler",
            self.handler_body,
            self.catch_cond,
            f"result:{self.result_term}",
            self.trust.name,
            "catch_handler",
        )

    def compatibility_obligation(self, throw: ThrowSection) -> str:
        """Return the compatibility obligation between this handler and a throw."""
        return (
            f"COMPATIBILITY: CatchHandler({self.handler_id}) handles "
            f"ThrowSection({throw.section_id}) "
            f"iff {self.exc_type!r} covers {throw.exc_type!r}"
        )

    def restrict_to(self, exc_type: str) -> CatchHandler:
        """Return a new CatchHandler restricted to a specific exception type."""
        return dataclasses.replace(
            self,
            handler_id=f"{self.handler_id}_{exc_type}",
            exc_type=exc_type,
            catch_cond=f"({self.catch_cond} AND exc_type={exc_type})",
        )


@dataclass(frozen=True)
class ExceptionSheafMap:
    """The global section of the exception sheaf over a try/except block.

    An ExceptionSheafMap records the complete exception semantics of a
    try/except block: all throw sections, all catch handlers, and the global
    Čech obstruction to gluing them into a consistent section.

    Fields
    ------
    map_id         : unique identifier
    try_description: description of the try body
    throw_sections : tuple of ThrowSection objects
    catch_handlers : tuple of CatchHandler objects
    obstruction    : global Čech H¹ obstruction (trivial if gluing succeeds)
    trust          : TrustTier of this map
    judgment       : 8-tuple Judgment for the try/except block
    """

    map_id: str
    try_description: str
    throw_sections: tuple
    catch_handlers: tuple
    obstruction: CechObstruction
    trust: TrustTier
    judgment: Judgment

    def is_fully_handled(self) -> bool:
        """Return True iff every throw section is handled by some catch handler."""
        for throw in self.throw_sections:
            if not any(h.handles(throw.exc_type) for h in self.catch_handlers):
                return False
        return True

    def unhandled_throws(self) -> list:
        """Return throw sections not covered by any catch handler."""
        return [
            t for t in self.throw_sections
            if not any(h.handles(t.exc_type) for h in self.catch_handlers)
        ]

    def to_judgment_tuple(self) -> tuple:
        """Return (c, φ, A, E, O, B, T, Π) as 8 strings."""
        return (
            self.map_id,
            f"exception_sheaf_map({len(self.throw_sections)} throws, "
            f"{len(self.catch_handlers)} handlers)",
            "ExceptionSheafMap",
            self.try_description,
            "" if self.is_fully_handled() else "UNHANDLED_THROWS",
            f"obstruction:{self.obstruction.cohomology_class}",
            self.trust.name,
            "exception_sheaf_map",
        )

    def global_obstruction(self) -> CechObstruction:
        """Return the combined obstruction from unhandled throws."""
        unhandled = self.unhandled_throws()
        if not unhandled:
            return self.obstruction
        cocycle = frozenset(
            (t.section_id, f"unhandled:{t.exc_type}")
            for t in unhandled
        )
        return CechObstruction(
            cover_id=self.map_id,
            cocycle=cocycle,
            cohomology_class=f"H1_unhandled_{self.map_id[:8]}",
            description=f"{len(unhandled)} unhandled throw sections",
        )


class ExceptionEncoder:
    """Stateful encoder for exception-valued Python code.

    ExceptionEncoder coordinates the creation of ThrowSections, CatchHandlers,
    and ExceptionSheafMaps.  It is the primary operational interface for building
    exception sheaf encodings.

    Invariants
    ----------
    - Judgments produced are 8-tuples — never booleans
    - Trust is an ordered algebra — never a float
    - Obstructions are Čech H¹ cohomology classes
    """

    def __init__(self, trust_tier: TrustTier = TrustTier.PROPOSAL) -> None:
        """Initialise the encoder with a default trust tier."""
        self._trust = trust_tier
        self._throw_sections: List[ThrowSection] = []
        self._catch_handlers: List[CatchHandler] = []
        self._sheaf_maps: List[ExceptionSheafMap] = []

    def make_throw_section(
        self,
        exc_type: str,
        message: str,
        site: str,
        *,
        tier: TrustTier | None = None,
    ) -> ThrowSection:
        """Create and register a ThrowSection for an exception raise site."""
        t = tier or self._trust
        section_id = f"throw_{exc_type}_{site.replace(':', '_')}"
        j = Judgment(
            context={"site": site, "exc_type": exc_type},
            formula=f"throw_section({exc_type}) at {site}",
            assumptions=(),
            evidence=(),
            obligations=(f"REACHABILITY:{exc_type}@{site}",),
            burden="ExceptionEncoder",
            trust=t,
            provenance=site,
        )
        ts = ThrowSection(
            section_id=section_id,
            exc_type=exc_type,
            message=message,
            site=site,
            throw_cond=f"raises_{exc_type}_at_{site.replace(':', '_')}",
            trust=t,
            judgment=j,
        )
        self._throw_sections.append(ts)
        return ts

    def make_catch_handler(
        self,
        exc_type: str,
        handler_body: str,
        result_term: str,
        *,
        tier: TrustTier | None = None,
    ) -> CatchHandler:
        """Create and register a CatchHandler for an except clause."""
        t = tier or self._trust
        handler_id = f"catch_{exc_type}_{handler_body[:16].replace(' ', '_')}"
        j = Judgment(
            context={"exc_type": exc_type, "handler_body": handler_body},
            formula=f"catch_handler({exc_type})",
            assumptions=(),
            evidence=(),
            obligations=(),
            burden="ExceptionEncoder",
            trust=t,
            provenance="catch_handler",
        )
        ch = CatchHandler(
            handler_id=handler_id,
            exc_type=exc_type,
            handler_body=handler_body,
            catch_cond=f"caught_{exc_type}",
            result_term=result_term,
            trust=t,
            judgment=j,
        )
        self._catch_handlers.append(ch)
        return ch

    def build_sheaf_map(
        self,
        try_description: str,
        throws: tuple | None = None,
        handlers: tuple | None = None,
        *,
        tier: TrustTier | None = None,
    ) -> ExceptionSheafMap:
        """Assemble throws and handlers into an ExceptionSheafMap."""
        t = tier or self._trust
        ts = throws if throws is not None else tuple(self._throw_sections)
        hs = handlers if handlers is not None else tuple(self._catch_handlers)
        map_id = f"sheaf_map_{try_description[:16].replace(' ', '_')}"

        unhandled = [throw for throw in ts if not any(h.handles(throw.exc_type) for h in hs)]
        cocycle = frozenset(
            (throw.section_id, f"unhandled:{throw.exc_type}") for throw in unhandled
        )
        obs = CechObstruction(
            cover_id=map_id,
            cocycle=cocycle,
            cohomology_class="0" if not cocycle else f"H1_{map_id[:8]}",
            description=(
                "trivial" if not cocycle
                else f"{len(unhandled)} unhandled throws"
            ),
        )
        j = Judgment(
            context={"map_id": map_id, "try_description": try_description},
            formula=f"exception_sheaf_map({len(ts)} throws, {len(hs)} handlers)",
            assumptions=(),
            evidence=tuple(th.section_id for th in ts),
            obligations=tuple(
                f"UNHANDLED:{th.exc_type}" for th in unhandled
            ),
            burden="ExceptionEncoder",
            trust=t,
            provenance="exception_sheaf_map",
        )
        esm = ExceptionSheafMap(
            map_id=map_id,
            try_description=try_description,
            throw_sections=ts,
            catch_handlers=hs,
            obstruction=obs,
            trust=t,
            judgment=j,
        )
        self._sheaf_maps.append(esm)
        return esm

    def encode(self, expression: str, operation_kind: str) -> ExceptionValueEncoding:
        """Encode a Python expression as an ExceptionValueEncoding."""
        section_id = f"enc_{operation_kind}_{expression[:16].replace(' ', '_')}"
        obs = CechObstruction(
            cover_id=section_id,
            cocycle=frozenset(),
            cohomology_class="0",
            description="trivial encoding obstruction",
        )
        j = Judgment(
            context={"expression": expression, "operation_kind": operation_kind},
            formula=f"exception_value_encoding({operation_kind})",
            assumptions=(),
            evidence=(),
            obligations=(f"WELL_FORMED:{operation_kind}",),
            burden="ExceptionEncoder",
            trust=self._trust,
            provenance="exception_encoder",
        )
        return ExceptionValueEncoding(
            encoding_id=section_id,
            exc_type=operation_kind,
            message=expression,
            path_id=ExceptionPath.UNCAUGHT,
            z3_term=f"Enc({operation_kind}, {expression!r})",
            trust=self._trust,
            judgment=j,
            obstruction=obs,
        )

    def all_sheaf_maps(self) -> List[ExceptionSheafMap]:
        """Return all ExceptionSheafMaps built by this encoder."""
        return list(self._sheaf_maps)


# ---------------------------------------------------------------------------
# Module-level sheaf-interface functions
# ---------------------------------------------------------------------------


def lift_exception_to_section(
    exc_type: str,
    message: str,
    site: str,
    tier: TrustTier,
) -> ThrowSection:
    """Lift an exception raise site into a ThrowSection.

    Parameters
    ----------
    exc_type : Python exception class name (e.g. "ValueError")
    message  : exception message string
    site     : source location string (e.g. "module.py:42")
    tier     : TrustTier for the section

    Returns
    -------
    ThrowSection encoding this raise site
    """
    enc = ExceptionEncoder(trust_tier=tier)
    return enc.make_throw_section(exc_type, message, site, tier=tier)


def encode_try_catch(
    try_desc: str,
    handlers: List[Tuple[str, str]],
    *,
    tier: TrustTier = TrustTier.PROPOSAL,
) -> ExceptionSheafMap:
    """Encode a try/except block as an ExceptionSheafMap.

    Parameters
    ----------
    try_desc : description of the try body
    handlers : list of (exc_type, handler_body) pairs
    tier     : TrustTier for the encoding

    Returns
    -------
    ExceptionSheafMap encoding the try/except structure
    """
    enc = ExceptionEncoder(trust_tier=tier)
    for exc_type, body in handlers:
        enc.make_catch_handler(exc_type, body, result_term=f"result_{exc_type}")
    return enc.build_sheaf_map(try_desc, throws=(), tier=tier)


def exception_obligation(section: ThrowSection) -> str:
    """Return the proof obligation string for a ThrowSection.

    Parameters
    ----------
    section : the ThrowSection to generate an obligation for

    Returns
    -------
    Obligation string (never empty)
    """
    return section.obligation_string()


def build_exception_sheaf(
    throws: List[ThrowSection],
    handlers: List[CatchHandler],
    *,
    try_desc: str = "try_block",
    tier: TrustTier = TrustTier.PROPOSAL,
) -> ExceptionSheafMap:
    """Construct an ExceptionSheafMap from throw sections and catch handlers.

    Parameters
    ----------
    throws   : list of ThrowSection objects
    handlers : list of CatchHandler objects
    try_desc : description of the try body
    tier     : TrustTier for the sheaf map

    Returns
    -------
    ExceptionSheafMap representing the complete try/except encoding
    """
    enc = ExceptionEncoder(trust_tier=tier)
    return enc.build_sheaf_map(
        try_desc,
        throws=tuple(throws),
        handlers=tuple(handlers),
        tier=tier,
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CatchHandler",
    "CechObstruction",
    "EncodeMode",
    "ExceptionCategory",
    "ExceptionEncoder",
    "ExceptionObligationBundle",
    "ExceptionPath",
    "ExceptionPatternLibrary",
    "ExceptionSemanticsEncoding",
    "ExceptionSheafMap",
    "ExceptionValuedException",
    "ExceptionValueEncoding",
    "ExceptionValuedStructuralSemanticsAnalyzer",
    "ExceptionValuedStructuralSemanticsCoordinator",
    "ExceptionValuedStructuralSemanticsWitness",
    "Judgment",
    "LiftedOperation",
    "LiftingStrategy",
    "ResultTypeDeclaration",
    "ThrowSection",
    "TrustTier",
    "analyze_exception_paths",
    "build_exception_sheaf",
    "build_result_type_declaration",
    "classify_exception_risk",
    "discharge_exception_obligation",
    "encode_exception_path",
    "encode_exception_semantics",
    "encode_normal_path",
    "encode_try_catch",
    "exception_obligation",
    "extract_exception_obligations",
    "lift_exception_to_section",
    "lift_to_result_type",
    "merge_exception_paths",
]


# ---------------------------------------------------------------------------
# Smoke test (exception-valued semantics)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== s02 exception-valued structural semantics smoke test ===\n")

    # --- TrustTier algebra ---
    assert TrustTier.PROPOSAL.join(TrustTier.VERIFIED) == TrustTier.VERIFIED
    assert TrustTier.PROOF_BACKED.meet(TrustTier.REVIEWED) == TrustTier.REVIEWED
    assert TrustTier.REVIEWED.promote() == TrustTier.VERIFIED
    assert TrustTier.PROPOSAL.demote() == TrustTier.PROPOSAL  # floor
    assert TrustTier.VERIFIED.is_admissible(TrustTier.REVIEWED)
    assert not TrustTier.PROPOSAL.is_admissible(TrustTier.VERIFIED)
    print("TrustTier algebra: OK")

    # --- Judgment (c, φ, A, E, O, B, T, Π) — never a boolean ---
    j = Judgment(
        context={"var": "x", "type": "list"},
        formula="x[i] is exception-valued",
        assumptions=("x: list", "i: int"),
        evidence=(),
        obligations=("REACHABILITY: IndexError",),
        burden="encoder",
        trust=TrustTier.PROPOSAL,
        provenance="oracle",
    )
    assert j.trust == TrustTier.PROPOSAL
    assert not j.is_fully_discharged()
    j2 = j.discharge_obligation("REACHABILITY: IndexError").with_trust(TrustTier.VERIFIED)
    assert j2.is_fully_discharged()
    print(f"Judgment: {j}")

    # --- CechObstruction ---
    obs = CechObstruction(
        cover_id="test_cover",
        cocycle=frozenset([("normal∩IndexError", "section_diff(Ok,IndexError)")]),
        cohomology_class="H1_test",
        description="Test obstruction",
    )
    assert not obs.is_trivial()
    trivial_obs = CechObstruction(
        cover_id="trivial_cover", cocycle=frozenset(),
        cohomology_class="0", description="Trivial",
    )
    assert trivial_obs.is_trivial()
    merged_obs = obs.direct_sum(trivial_obs)
    assert not merged_obs.is_trivial()
    print(f"CechObstruction: {obs}")

    # --- ExceptionPatternLibrary ---
    lib = ExceptionPatternLibrary()
    subscript_exc = lib.lookup("subscript")
    assert "IndexError" in subscript_exc
    assert subscript_exc["IndexError"] == ExceptionCategory.PARTIAL
    lib.register("custom_op", "MyError", ExceptionCategory.RECOVERABLE)
    assert "MyError" in lib.lookup("custom_op")
    print(f"ExceptionPatternLibrary: {lib.all_operations()}")

    # --- ResultTypeRegistry ---
    reg = ResultTypeRegistry()
    decl1 = reg.get_or_create("int", frozenset({"ValueError", "OverflowError"}))
    decl2 = reg.get_or_create("int", frozenset({"ValueError", "OverflowError"}))
    assert decl1 is decl2, "Registry should return same instance for same key"
    assert reg.declaration_count() == 1
    print(f"ResultTypeRegistry: {decl1.type_name}")

    # --- encode_normal_path / encode_exception_path ---
    normal_term = encode_normal_path("x[i]", "int", "subscript")
    assert "Ok" in normal_term
    print(f"Normal path term: {normal_term}")

    exc_sample = ExceptionValuedException(
        exc_type="IndexError",
        message="list index out of range",
        category=ExceptionCategory.PARTIAL,
        operation="subscript",
        path_id=ExceptionPath.UNCAUGHT,
        z3_sort_name="ExcIndexError",
        trust=TrustTier.REVIEWED,
    )
    exc_term = encode_exception_path("x[i]", exc_sample, "subscript")
    assert "IndexError" in exc_term
    print(f"Exception path term: {exc_term}")

    # --- encode_exception_semantics ---
    enc = encode_exception_semantics(
        "x[i]", "subscript",
        context={"x": "list[int]", "i": "int"},
        result_type="int",
        trust=TrustTier.REVIEWED,
    )
    assert enc.lifted_op.result_type == "int"
    assert len(enc.lifted_op.exception_terms) > 0
    assert len(enc.obligations) > 0
    print(f"Encoding: {enc.encoding_id}, clean={enc.is_clean()}")
    print(f"  Z3 decl: {enc.lifted_op.to_z3_decl()}")

    # --- extract_exception_obligations ---
    obls = extract_exception_obligations(enc.lifted_op, {"x": "list[int]", "i": "int"})
    assert any("WELL_FORMED" in o for o in obls)
    assert any("REACHABILITY" in o for o in obls)
    print(f"Obligations (first 2): {obls[:2]}")

    # --- analyze_exception_paths ---
    paths = analyze_exception_paths(enc)
    assert ExceptionPath.NORMAL in paths
    print(f"Paths: {list(paths.keys())}")

    # --- lift_to_result_type ---
    decl = lift_to_result_type("int(s)", "int_conversion", "int")
    assert "Result" in decl.type_name
    print(f"ResultTypeDeclaration: {decl.type_name}")

    # --- classify_exception_risk ---
    cat, tier = classify_exception_risk(exc_sample, {"x": "list"})
    assert cat == ExceptionCategory.PARTIAL
    assert tier == TrustTier.REVIEWED
    print(f"classify_exception_risk: ({cat.value}, {tier.name})")

    # --- merge_exception_paths ---
    enc2 = encode_exception_semantics(
        "x[i]", "subscript",
        context={"x": "list[str]", "i": "int"},
        result_type="str",
    )
    merged_enc = merge_exception_paths([enc, enc2])
    assert "merged" in merged_enc.encoding_id
    print(f"Merged encoding: {merged_enc.encoding_id}")

    # --- discharge_exception_obligation ---
    first_obl = enc.obligations[0]
    updated_j = discharge_exception_obligation(
        first_obl, enc, evidence="solver_certificate_xyz", trust=TrustTier.VERIFIED
    )
    assert first_obl not in updated_j.obligations
    print(f"Discharged obligation; remaining: {len(updated_j.obligations)}")

    # --- _operation_can_raise ---
    assert _operation_can_raise("subscript")
    assert _operation_can_raise("division")
    assert not _operation_can_raise("noop")

    # --- build_result_type_declaration ---
    custom_decl = build_result_type_declaration("str", frozenset({"ValueError"}))
    assert "str" in custom_decl.value_type
    assert custom_decl.is_recursive is False
    print(f"Custom decl: {custom_decl.render()[:80]}...")

    # --- ExceptionSemanticsEncoder batch ---
    encoder = ExceptionSemanticsEncoder(mode=EncodeMode.STRICT)
    batch = encoder.encode_batch([
        ("int(s)", "int_conversion"),
        ("d[k]",   "subscript"),
        ("a / b",  "division"),
    ])
    assert len(batch) == 3
    print(f"Batch encodings: {[e.encoding_id for e in batch]}")

    # --- ExceptionValuedStructuralSemanticsAnalyzer ---
    analyzer = ExceptionValuedStructuralSemanticsAnalyzer(TrustTier.PROPOSAL)
    analyzer.add_batch(batch)
    judgments = analyzer.analyze()
    assert len(judgments) == len(batch) + 1  # +1 summary
    print(f"Analyzer judgments: {len(judgments)}")

    # --- ExceptionValuedStructuralSemanticsCoordinator ---
    coord = ExceptionValuedStructuralSemanticsCoordinator(
        mode=EncodeMode.STRICT,
        trust_threshold=TrustTier.PROPOSAL,
    )
    e1 = coord.submit("f(x)", "call", result_type="Any", trust=TrustTier.REVIEWED)
    e2 = coord.submit("open(path)", "open_file", result_type="file",
                      trust=TrustTier.PROPOSAL)
    w  = coord.add_witness(
        e1, EvidenceItemKind.ORACLE_PROPOSAL,
        artifacts=["mock_cert"], discharged=list(e1.obligations[:1]),
    )
    jj  = coord.run_analysis()
    g_obs = coord.global_obstruction()
    smry  = coord.summary()
    assert smry["encodings"] == 2
    print(f"Coordinator summary: {smry}")
    print(f"Global obstruction: {g_obs}")
    print(f"Witness: {w}")

    # --- Verify all module constants are present ---
    assert "subscript" in EXCEPTION_TRIGGERS
    assert "result_type" in RESULT_TYPE_TEMPLATE
    assert "subscript" in LIFTING_PREFERENCE
    print("Module constants: OK")

    print("\n=== s02 smoke test PASSED ===")
    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore
        """Judgment tuple (c, phi, A, E, O, B, T, Pi) — NEVER a boolean."""
        c: Any
        phi: str
        A: str
        E: tuple
        O: tuple
        B: dict
        T: TrustTier
        Pi: str

        def with_tier(self, new_tier: TrustTier) -> Judgment:
            return Judgment(c=self.c, phi=self.phi, A=self.A, E=self.E,
                            O=self.O, B=self.B, T=new_tier, Pi=self.Pi)

        def discharged(self) -> bool:
            return len(self.O) == 0 and self.T >= TrustTier.VERIFIED

        def __str__(self) -> str:
            return (f"Judgment(phi={self.phi!r}, T={self.T.name}, "
                    f"obligations={len(self.O)})")


# ---------------------------------------------------------------------------
# CechH1Obstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CechH1Obstruction:
    """Čech H¹ cohomology class representing a gluing obstruction for exception sections."""
    cover_ids: tuple[str, ...]
    cocycle: dict[tuple[str, str], str]
    coboundary_class: str

    def is_trivial(self) -> bool:
        return all(v == "0" for v in self.cocycle.values())

    def restrict(self, subcover_ids: tuple[str, ...]) -> CechH1Obstruction:
        sub = set(subcover_ids)
        return CechH1Obstruction(
            cover_ids=subcover_ids,
            cocycle={k: v for k, v in self.cocycle.items() if k[0] in sub and k[1] in sub},
            coboundary_class=f"restrict({self.coboundary_class})",
        )

    def merge(self, other: CechH1Obstruction) -> CechH1Obstruction:
        """Merge two obstruction classes (direct sum in H¹)."""
        merged_cocycle = dict(self.cocycle)
        for k, v in other.cocycle.items():
            if k in merged_cocycle:
                existing = merged_cocycle[k]
                merged_cocycle[k] = f"({existing}) + ({v})" if existing != "0" and v != "0" else (existing if v == "0" else v)
            else:
                merged_cocycle[k] = v
        combined_ids = tuple(dict.fromkeys(list(self.cover_ids) + list(other.cover_ids)))
        return CechH1Obstruction(
            cover_ids=combined_ids,
            cocycle=merged_cocycle,
            coboundary_class=f"{self.coboundary_class} ⊕ {other.coboundary_class}",
        )

    def __str__(self) -> str:
        trivial = "trivial" if self.is_trivial() else "non-trivial"
        return f"CechH1({self.coboundary_class}, {trivial})"


# ---------------------------------------------------------------------------
# ExceptionValueEncoding — lift Python exceptions to first-class values
# ---------------------------------------------------------------------------

@dataclass
class ExceptionValueEncoding:
    """
    Lifts Python values and exceptions to first-class stalk elements of the exception sheaf.

    The stalk at any code point is V + Exc (a tagged union of normal values and exceptions).
    """
    value: Any
    exception: Optional[Exception]
    is_exception: bool

    # ------------------------------------------------------------------
    @classmethod
    def lift(cls, value: Any) -> ExceptionValueEncoding:
        """Lift a normal Python value to the exception sheaf."""
        return cls(value=value, exception=None, is_exception=False)

    # ------------------------------------------------------------------
    @classmethod
    def lift_exc(cls, exc: Exception) -> ExceptionValueEncoding:
        """Lift an exception to the exception sheaf."""
        return cls(value=None, exception=exc, is_exception=True)

    # ------------------------------------------------------------------
    @classmethod
    def run(cls, thunk: Callable[[], Any]) -> ExceptionValueEncoding:
        """Evaluate a thunk, catching any exception and lifting to the sheaf."""
        try:
            result = thunk()
            return cls.lift(result)
        except Exception as exc:
            return cls.lift_exc(exc)

    # ------------------------------------------------------------------
    def project_normal(self) -> Any:
        """Extract the normal value, raising if this is an exception encoding."""
        if self.is_exception:
            raise ValueError(
                f"Cannot project normal value from exception encoding: {self.exception!r}"
            )
        return self.value

    # ------------------------------------------------------------------
    def project_exception(self) -> Optional[Exception]:
        """Extract the exception component (None if this is a normal value)."""
        return self.exception

    # ------------------------------------------------------------------
    def bind(self, f: Callable[[Any], ExceptionValueEncoding]) -> ExceptionValueEncoding:
        """Monadic bind: if this is an exception, propagate it; otherwise apply f to the value.

        This implements the standard 'exception monad' or 'error monad':
            bind (Right v) f = f v
            bind (Left exc) f = Left exc
        """
        if self.is_exception:
            return self  # propagate the exception
        try:
            result = f(self.value)
            if not isinstance(result, ExceptionValueEncoding):
                return ExceptionValueEncoding.lift(result)
            return result
        except Exception as exc:
            return ExceptionValueEncoding.lift_exc(exc)

    # ------------------------------------------------------------------
    def is_total(self) -> bool:
        """True if this encoding holds a normal (non-exception) value."""
        return not self.is_exception

    # ------------------------------------------------------------------
    def map(self, f: Callable[[Any], Any]) -> ExceptionValueEncoding:
        """Apply a pure function f to the value, lifting any exceptions."""
        if self.is_exception:
            return self
        try:
            return ExceptionValueEncoding.lift(f(self.value))
        except Exception as exc:
            return ExceptionValueEncoding.lift_exc(exc)

    # ------------------------------------------------------------------
    def or_else(self, default: Any) -> Any:
        """Return value if normal, else return default."""
        return self.value if not self.is_exception else default

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        if self.is_exception:
            return f"ExcVal(Left({type(self.exception).__name__}: {self.exception}))"
        return f"ExcVal(Right({self.value!r}))"


# ---------------------------------------------------------------------------
# ThrowSection — section of the exception sheaf over a code region
# ---------------------------------------------------------------------------

@dataclass
class ThrowSection:
    """
    A section of the exception sheaf over a code region.

    The code region is partitioned into paths (e.g. normal path, each except branch).
    Each path_id maps to an ExceptionValueEncoding.
    """
    region_id: str
    paths: dict = field(default_factory=dict)     # path_id -> ExceptionValueEncoding
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def compose_sequential(self, other: ThrowSection) -> ThrowSection:
        """Sequentially compose two sections: output of self feeds into other.

        For each path in self:
        - If self's path is a normal value, compose with each path in other
        - If self's path is an exception, propagate it without invoking other
        """
        composed_paths: dict = {}
        for self_path_id, self_enc in self.paths.items():
            if self_enc.is_exception:
                # Exception in first block short-circuits
                composed_paths[f"{self_path_id}>>THREW"] = self_enc
            else:
                # Normal value: compose with other's paths
                for other_path_id, other_enc in other.paths.items():
                    new_key = f"{self_path_id}>>{other_path_id}"
                    composed_paths[new_key] = other_enc
        return ThrowSection(
            region_id=f"({self.region_id} ; {other.region_id})",
            paths=composed_paths,
            metadata={**self.metadata, **other.metadata},
        )

    # ------------------------------------------------------------------
    def merge_branches(self, branches: list[ThrowSection]) -> ThrowSection:
        """Merge sections from parallel branches (e.g. if/else arms) into one section.

        The merged section includes all paths from all branches, prefixed by branch label.
        """
        merged_paths: dict = dict(self.paths)
        for i, branch in enumerate(branches):
            for path_id, enc in branch.paths.items():
                merged_paths[f"branch{i}::{path_id}"] = enc
        return ThrowSection(
            region_id=f"merge({self.region_id},[{','.join(b.region_id for b in branches)}])",
            paths=merged_paths,
            metadata={},
        )

    # ------------------------------------------------------------------
    def throw_set(self) -> set[type]:
        """Return the set of exception types thrown in any path of this section."""
        result: set[type] = set()
        for enc in self.paths.values():
            if enc.is_exception and enc.exception is not None:
                result.add(type(enc.exception))
        return result

    # ------------------------------------------------------------------
    def restrict(self, subregion: set[str]) -> ThrowSection:
        """Restrict the section to a subset of path ids."""
        return ThrowSection(
            region_id=f"restrict({self.region_id})",
            paths={k: v for k, v in self.paths.items() if k in subregion},
            metadata=self.metadata,
        )

    # ------------------------------------------------------------------
    def normal_paths(self) -> dict:
        """Return only paths that don't throw."""
        return {k: v for k, v in self.paths.items() if not v.is_exception}

    # ------------------------------------------------------------------
    def exception_paths(self) -> dict:
        """Return only paths that throw."""
        return {k: v for k, v in self.paths.items() if v.is_exception}

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        n_throw = sum(1 for v in self.paths.values() if v.is_exception)
        return (f"ThrowSection(region={self.region_id!r}, "
                f"paths={len(self.paths)}, throwing={n_throw})")


# ---------------------------------------------------------------------------
# CatchHandler — encodes a catch clause as a sheaf morphism
# ---------------------------------------------------------------------------

@dataclass
class CatchHandler:
    """Encodes a Python except clause as a morphism on ThrowSections."""
    handled_types: tuple      # tuple of exception types (may contain None for bare except)
    handler_body: str         # string representation of handler body
    fallback: Any = None      # value returned when exception is caught

    # ------------------------------------------------------------------
    def _handles(self, enc: ExceptionValueEncoding) -> bool:
        """True if this handler catches the exception in enc."""
        if not enc.is_exception or enc.exception is None:
            return False
        for htype in self.handled_types:
            if htype is None:  # bare except
                return True
            if isinstance(enc.exception, htype):
                return True
        return False

    # ------------------------------------------------------------------
    def apply(self, section: ThrowSection) -> ThrowSection:
        """Apply this handler to a ThrowSection, catching matching exceptions.

        Caught exceptions become normal values (the fallback or re-raised value).
        Un-caught exceptions remain as exception encodings.
        """
        new_paths: dict = {}
        for path_id, enc in section.paths.items():
            if self._handles(enc):
                # Caught: replace with fallback wrapped as normal value
                new_paths[f"caught::{path_id}"] = ExceptionValueEncoding.lift(self.fallback)
            else:
                new_paths[path_id] = enc
        return ThrowSection(
            region_id=f"handled({section.region_id}, {[t.__name__ if t else '*' for t in self.handled_types]})",
            paths=new_paths,
            metadata=section.metadata,
        )

    # ------------------------------------------------------------------
    def residual(self, section: ThrowSection) -> ThrowSection:
        """Return the section of exceptions that this handler does *not* catch."""
        uncaught_paths: dict = {}
        for path_id, enc in section.paths.items():
            if enc.is_exception and not self._handles(enc):
                uncaught_paths[path_id] = enc
        return ThrowSection(
            region_id=f"residual({section.region_id})",
            paths=uncaught_paths,
            metadata=section.metadata,
        )

    # ------------------------------------------------------------------
    def is_complete(self, section: ThrowSection) -> bool:
        """True if this handler catches all exceptions in the section."""
        for enc in section.paths.values():
            if enc.is_exception and not self._handles(enc):
                return False
        return True

    # ------------------------------------------------------------------
    def generate_exhaustiveness_obligation(self, section: ThrowSection) -> str:
        """Generate a proof obligation for exhaustive exception handling."""
        thrown = section.throw_set()
        handled = {t for t in self.handled_types if t is not None}
        unhandled = thrown - handled
        if not unhandled:
            return "SATISFIED: all thrown exceptions are handled"
        unhandled_names = ", ".join(t.__name__ for t in unhandled)
        return (
            f"OBLIGATION: exhaustiveness check failed — "
            f"unhandled exception types: {{{unhandled_names}}} "
            f"in region {section.region_id!r}"
        )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        names = [t.__name__ if t else "*" for t in self.handled_types]
        return f"CatchHandler(types={names}, body={self.handler_body!r})"


# ---------------------------------------------------------------------------
# ExceptionSheafMap — a morphism between two ExceptionValueEncodings
# ---------------------------------------------------------------------------

@dataclass
class ExceptionSheafMap:
    """A morphism (natural transformation) between two exception sheaf sections."""
    source_encoding: ExceptionValueEncoding
    target_encoding: ExceptionValueEncoding
    morphism_map: dict   # exc_type -> exc_type (or value -> value)

    # ------------------------------------------------------------------
    def compose(self, other: ExceptionSheafMap) -> ExceptionSheafMap:
        """Compose two sheaf maps (self then other)."""
        # Compose morphism maps
        composed: dict = {}
        for k, v in self.morphism_map.items():
            if v in other.morphism_map:
                composed[k] = other.morphism_map[v]
            else:
                composed[k] = v
        # Compose the value transformations
        intermediate = self._apply_map(self.source_encoding)
        final = other._apply_map(intermediate)
        return ExceptionSheafMap(
            source_encoding=self.source_encoding,
            target_encoding=final,
            morphism_map=composed,
        )

    # ------------------------------------------------------------------
    def _apply_map(self, enc: ExceptionValueEncoding) -> ExceptionValueEncoding:
        """Apply this morphism map to an encoding."""
        if not enc.is_exception:
            mapped_val = self.morphism_map.get(type(enc.value), enc.value)
            return ExceptionValueEncoding.lift(mapped_val)
        exc_type = type(enc.exception) if enc.exception else type(None)
        if exc_type in self.morphism_map:
            new_type = self.morphism_map[exc_type]
            if isinstance(new_type, type) and issubclass(new_type, Exception):
                new_exc = new_type(str(enc.exception))
                return ExceptionValueEncoding.lift_exc(new_exc)
        return enc

    # ------------------------------------------------------------------
    def pushforward_exception_set(self, exc_set: set) -> set:
        """Push forward a set of exception types through this morphism."""
        pushed: set = set()
        for exc_type in exc_set:
            mapped = self.morphism_map.get(exc_type, exc_type)
            pushed.add(mapped)
        return pushed

    # ------------------------------------------------------------------
    def check_commutativity(self) -> bool:
        """Check that applying the morphism to source yields target (up to equality)."""
        computed = self._apply_map(self.source_encoding)
        # Commutativity: computed matches target_encoding in terms of exception status
        if computed.is_exception != self.target_encoding.is_exception:
            return False
        if computed.is_exception:
            return type(computed.exception) == type(self.target_encoding.exception)
        return computed.value == self.target_encoding.value

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (f"ExceptionSheafMap("
                f"src={self.source_encoding!r} -> tgt={self.target_encoding!r})")


# ---------------------------------------------------------------------------
# AST-based ExceptionEncoder
# ---------------------------------------------------------------------------

class _ExceptionBodyVisitor(ast.NodeVisitor):
    """Visit a try body and record what exception types may be raised."""

    def __init__(self) -> None:
        self.raised_types: list[Optional[str]] = []
        self.has_bare_raise: bool = False

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is None:
            self.has_bare_raise = True
        elif isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Name):
                self.raised_types.append(node.exc.func.id)
            elif isinstance(node.exc.func, ast.Attribute):
                self.raised_types.append(
                    f"{ast.unparse(node.exc.func)}" if hasattr(ast, "unparse") else "?"
                )
        elif isinstance(node.exc, ast.Name):
            self.raised_types.append(node.exc.id)
        self.generic_visit(node)


_BUILTIN_EXCEPTIONS: dict[str, type] = {
    cls.__name__: cls for cls in [
        Exception, ValueError, TypeError, KeyError, IndexError,
        AttributeError, RuntimeError, NotImplementedError, OverflowError,
        ZeroDivisionError, OSError, IOError, ImportError, NameError,
        StopIteration, RecursionError, MemoryError, ArithmeticError,
    ]
}


class ExceptionEncoder:
    """Orchestrates encoding of try/except/finally blocks into ThrowSections."""

    # ------------------------------------------------------------------
    def encode_try_body(self, body_ast: ast.AST) -> ThrowSection:
        """Encode the try body as a ThrowSection."""
        visitor = _ExceptionBodyVisitor()
        visitor.visit(body_ast)

        paths: dict = {}
        # Always include a normal execution path
        paths["normal"] = ExceptionValueEncoding.lift("<try_body_result>")

        # Add paths for each raised exception type
        for i, exc_name in enumerate(visitor.raised_types):
            exc_type = _BUILTIN_EXCEPTIONS.get(exc_name or "", RuntimeError)
            try:
                exc_instance = exc_type(f"raised in try body: {exc_name}")
            except Exception:
                exc_instance = RuntimeError(f"raised in try body: {exc_name}")
            paths[f"raise_{i}_{exc_name}"] = ExceptionValueEncoding.lift_exc(exc_instance)

        if visitor.has_bare_raise:
            paths["bare_raise"] = ExceptionValueEncoding.lift_exc(
                RuntimeError("bare raise in try body")
            )

        region_id = f"try_body@line{getattr(body_ast, 'lineno', '?')}"
        return ThrowSection(region_id=region_id, paths=paths)

    # ------------------------------------------------------------------
    def encode_except_clause(self, handler: ast.ExceptHandler) -> CatchHandler:
        """Encode an except clause as a CatchHandler."""
        if handler.type is None:
            # bare except: catches everything
            handled_types: tuple = (None,)
        elif isinstance(handler.type, ast.Name):
            t = _BUILTIN_EXCEPTIONS.get(handler.type.id, Exception)
            handled_types = (t,)
        elif isinstance(handler.type, ast.Tuple):
            names = [
                elt.id for elt in handler.type.elts if isinstance(elt, ast.Name)
            ]
            handled_types = tuple(_BUILTIN_EXCEPTIONS.get(n, Exception) for n in names)
        else:
            handled_types = (Exception,)

        body_src = (
            ast.unparse(handler) if hasattr(ast, "unparse")
            else f"except {handler.type}: ..."
        )
        return CatchHandler(
            handled_types=handled_types,
            handler_body=body_src,
            fallback=None,
        )

    # ------------------------------------------------------------------
    def encode_finally(self, finally_ast: ast.AST) -> ThrowSection:
        """Encode a finally block as a ThrowSection (always-executed section)."""
        visitor = _ExceptionBodyVisitor()
        visitor.visit(finally_ast)
        paths: dict = {"finally_normal": ExceptionValueEncoding.lift("<finally_result>")}
        for i, exc_name in enumerate(visitor.raised_types):
            exc_type = _BUILTIN_EXCEPTIONS.get(exc_name or "", RuntimeError)
            try:
                exc_instance = exc_type(f"raised in finally: {exc_name}")
            except Exception:
                exc_instance = RuntimeError(f"raised in finally: {exc_name}")
            paths[f"finally_raise_{i}"] = ExceptionValueEncoding.lift_exc(exc_instance)
        return ThrowSection(
            region_id=f"finally@line{getattr(finally_ast, 'lineno', '?')}",
            paths=paths,
        )

    # ------------------------------------------------------------------
    def encode_full(self, try_node: ast.Try) -> tuple[ThrowSection, list[str]]:
        """Encode a complete try/except/finally node.

        Returns (final_section, list_of_obligations).
        """
        # 1. Encode try body
        try_body = ast.Module(body=try_node.body, type_ignores=[])
        try_section = self.encode_try_body(try_body)

        # 2. Apply each except handler in order
        obligations: list[str] = []
        current_section = try_section
        for handler in try_node.handlers:
            catch = self.encode_except_clause(handler)
            obl = catch.generate_exhaustiveness_obligation(current_section)
            obligations.append(obl)
            current_section = catch.apply(current_section)

        # 3. Compose with finally if present
        if try_node.finalbody:
            finally_body = ast.Module(body=try_node.finalbody, type_ignores=[])
            finally_section = self.encode_finally(finally_body)
            current_section = current_section.compose_sequential(finally_section)

        return current_section, obligations

    # ------------------------------------------------------------------
    @staticmethod
    def encode_function(func: Callable) -> dict[str, tuple[ThrowSection, list[str]]]:
        """Encode all try blocks in a function."""
        try:
            src = inspect.getsource(func)
        except OSError:
            src = "def _unknown(): pass"
        src = textwrap.dedent(src)
        tree = ast.parse(src)

        encoder = ExceptionEncoder()
        results: dict = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                region_id = f"try@line{getattr(node, 'lineno', '?')}"
                section, obls = encoder.encode_full(node)
                results[region_id] = (section, obls)
        return results


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # --- TrustTier ---
    assert TrustTier.PROPOSAL < TrustTier.VERIFIED
    assert TrustTier.PROOF_BACKED > TrustTier.RUNTIME_WITNESSED

    # --- Judgment ---
    j = Judgment(
        c={}, phi="exception handling is exhaustive", A="encoder",
        E=(), O=("catch all exc types",), B={}, T=TrustTier.PROPOSAL, Pi=""
    )
    assert j.T == TrustTier.PROPOSAL
    print(f"Judgment: {j}")

    # --- ExceptionValueEncoding ---
    normal = ExceptionValueEncoding.lift(42)
    assert normal.is_total()
    assert normal.project_normal() == 42
    assert normal.project_exception() is None

    exc_enc = ExceptionValueEncoding.lift_exc(ValueError("bad input"))
    assert not exc_enc.is_total()
    assert isinstance(exc_enc.project_exception(), ValueError)

    # Bind: normal propagates
    result = normal.bind(lambda x: ExceptionValueEncoding.lift(x * 2))
    assert result.project_normal() == 84, f"Expected 84, got {result}"

    # Bind: exception propagates
    result_exc = exc_enc.bind(lambda x: ExceptionValueEncoding.lift(x * 2))
    assert result_exc.is_exception
    print(f"ExcVal normal: {normal}, exc: {exc_enc}")
    print(f"Bind result: {result}")

    # --- ThrowSection ---
    section = ThrowSection(
        region_id="test_region",
        paths={
            "normal": ExceptionValueEncoding.lift(10),
            "raise_0": ExceptionValueEncoding.lift_exc(ValueError("oops")),
            "raise_1": ExceptionValueEncoding.lift_exc(KeyError("missing")),
        },
    )
    assert section.throw_set() == {ValueError, KeyError}
    assert len(section.exception_paths()) == 2
    assert len(section.normal_paths()) == 1
    print(f"ThrowSection: {section}")

    # --- CatchHandler ---
    handler = CatchHandler(handled_types=(ValueError,), handler_body="pass", fallback=-1)
    handled_section = handler.apply(section)
    # ValueError should be caught, KeyError should remain
    remaining_throw = handled_section.throw_set()
    assert KeyError in remaining_throw, f"KeyError should remain: {remaining_throw}"
    assert ValueError not in remaining_throw, f"ValueError should be caught: {remaining_throw}"

    obl = handler.generate_exhaustiveness_obligation(section)
    assert "OBLIGATION" in obl  # KeyError unhandled
    print(f"Handler obligation: {obl}")

    residual = handler.residual(section)
    assert KeyError in residual.throw_set()

    # --- ExceptionSheafMap ---
    src_enc = ExceptionValueEncoding.lift_exc(ValueError("v"))
    tgt_enc = ExceptionValueEncoding.lift_exc(RuntimeError("r"))
    morphism = ExceptionSheafMap(
        source_encoding=src_enc,
        target_encoding=tgt_enc,
        morphism_map={ValueError: RuntimeError},
    )
    pushed = morphism.pushforward_exception_set({ValueError, KeyError})
    assert RuntimeError in pushed
    print(f"Pushed exception set: {pushed}")

    # --- ExceptionEncoder on real code ---
    def _sample_try():
        try:
            x = int("bad")
        except ValueError as e:
            x = -1
        finally:
            pass
        return x

    encoder = ExceptionEncoder()
    results = ExceptionEncoder.encode_function(_sample_try)
    assert len(results) > 0, "Expected at least one try block encoded"
    for region, (sec, obls) in results.items():
        print(f"  Region {region}: {sec}, obligations={obls}")

    # --- CechH1Obstruction ---
    obs = CechH1Obstruction(
        cover_ids=("try_body", "except_val", "finally"),
        cocycle={
            ("try_body", "except_val"): "section_diff_01",
            ("except_val", "finally"): "0",
            ("try_body", "finally"): "section_diff_02",
        },
        coboundary_class="exception_gluing_obstruction",
    )
    assert not obs.is_trivial()
    restricted = obs.restrict(("try_body", "except_val"))
    assert "finally" not in restricted.cover_ids
    print(f"Obstruction: {obs}")

    print("\n=== s02 smoke test PASSED ===")
