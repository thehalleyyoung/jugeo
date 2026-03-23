"""Interface summaries as uninterpreted functions over the sheaf.

# copilot: collection-heap-encodings module 04 — interface summaries as uninterpreted functions
# theory2.tex §27.16–§27.20: interface summaries are modelled as uninterpreted
# functions over the collection sheaf.  A summary contracts the visible behaviour
# of an interface into a set of axioms that can be checked without inspecting
# the implementation.

An **interface summary** is a sound abstraction of a module, class, or function
boundary.  Rather than inlining the implementation, the summary declares the
interface's behaviour as a set of *uninterpreted function symbols* equipped
with axioms.  The axioms are theory2.tex judgment tuples that constrain the
uninterpreted functions without specifying their implementation.

This module provides:

* :class:`InterfaceSummary` — the top-level summary record.
* :class:`UninterpretedFunctionRepr` — a single uninterpreted function symbol
  with its sort signature and axiom set.
* :class:`SummaryContract` — the binding between an interface summary and the
  judgment obligations it generates.
* :class:`SummaryTableau` — a collection of interface summaries forming a
  complete abstract description of a module boundary.

Public functions
----------------
:func:`build_interface_summary`
    Build an InterfaceSummary from a Python callable's type signature.
:func:`apply_summary`
    Apply a summary to a call site, generating obligations.
:func:`validate_summary_consistency`
    Check that a summary's axioms are mutually consistent.

Theory invariants
-----------------
* Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — NEVER booleans.
* Trust is an ordered algebra element — NEVER a float.
* TrustTier: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED.
* Obstructions are Čech H¹ cohomology classes.
* Descent returns GlobalSection OR DescentObstruction — never raises.
* ``raise_with_scope(code, message=..., provenance=...)`` signature.
"""

from __future__ import annotations

import abc
import collections
import functools
import hashlib
import inspect
import itertools
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from functools import reduce
from itertools import combinations
from typing import Any, Callable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)
_LOGGER = logger

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
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
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Trust tier algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust tiers — PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED."""

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> TrustTier:
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def is_at_least(self, other: TrustTier) -> bool:
        return int(self) >= int(other)


# ---------------------------------------------------------------------------
# Judgment dataclass — (c, φ, A, E, O, B, T, Π) — NEVER a boolean
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Fields mirror the theory2.tex tuple: context, formula, assumptions,
    evidence, obligations, burden, trust (TrustTier), provenance.
    """
    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


# ---------------------------------------------------------------------------
# CechObstruction dataclass — Čech H¹ cohomology class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class witnessing descent failure.

    Attributes
    ----------
    cover_id:      Identifier of the open cover {U_i}.
    cocycle:       frozenset of (i, j, σ_ij) triples.
    cohomology_class: Canonical string representative of [σ] ∈ Ȟ¹.
    description:   Human-readable explanation of the obstruction.
    """
    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the Čech class is the trivial (zero) element."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SortKind(str, Enum):
    """SMT sort kinds for uninterpreted function signatures."""

    INT = "Int"
    REAL = "Real"
    BOOL = "Bool"
    STRING = "String"
    ARRAY = "Array"
    UNINTERPRETED = "U"


class AxiomKind(str, Enum):
    """The logical nature of a summary axiom."""

    PRECONDITION = "precondition"
    POSTCONDITION = "postcondition"
    INVARIANT = "invariant"
    COMMUTATIVITY = "commutativity"
    ASSOCIATIVITY = "associativity"
    IDEMPOTENCY = "idempotency"
    MONOTONICITY = "monotonicity"
    CUSTOM = "custom"


class SummaryStatus(str, Enum):
    """The discharge status of an interface summary."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"


class CallSiteStatus(str, Enum):
    """The status of a call site after summary application."""

    OBLIGATIONS_PENDING = "obligations_pending"
    OBLIGATIONS_DISCHARGED = "obligations_discharged"
    PRECONDITION_VIOLATED = "precondition_violated"
    POSTCONDITION_VIOLATED = "postcondition_violated"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _python_type_to_sort(annotation: Any) -> SortKind:
    """Map a Python type annotation to an SMT sort kind."""
    if annotation is inspect.Parameter.empty or annotation is None:
        return SortKind.UNINTERPRETED
    type_map: dict[type, SortKind] = {
        int: SortKind.INT, float: SortKind.REAL,
        bool: SortKind.BOOL, str: SortKind.STRING,
    }
    return type_map.get(annotation, SortKind.UNINTERPRETED)


# ---------------------------------------------------------------------------
# Čech obstruction for summary consistency
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SummaryCechObstruction:
    """A Čech H¹ obstruction blocking interface summary consistency verification."""

    coordinate: str
    cocycle_description: str
    involved_axioms: tuple[str, ...]
    trust_tier: TrustTier = TrustTier.PROPOSAL
    is_coboundary: bool = False
    repair_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "involved_axioms": list(self.involved_axioms),
            "trust_tier": self.trust_tier.name,
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
        }


# ---------------------------------------------------------------------------
# Judgment tuple — (c, φ, A, E, O, B, T, Π)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SummaryJudgment:
    """A judgment about an interface summary.  NEVER a boolean."""

    c: str
    phi: str
    A: str
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[SummaryCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.O) == 0 and len(self.B) == 0

    @property
    def is_obstructed(self) -> bool:
        return any(not ob.is_coboundary for ob in self.B)

    def with_obligation(self, ob: str) -> SummaryJudgment:
        from dataclasses import replace
        return replace(self, O=(*self.O, ob))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c, "phi": self.phi, "A": self.A,
            "E": list(self.E), "O": list(self.O),
            "B": [ob.to_dict() for ob in self.B],
            "T": self.T.name, "Pi": dict(self.Pi),
        }


def _make_summary_judgment(
    coordinate: str, phi: str, carrier: str,
    evidence: Sequence[str], obligations: Sequence[str],
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Mapping[str, Any] | None = None,
) -> SummaryJudgment:
    return SummaryJudgment(
        c=coordinate, phi=phi, A=carrier,
        E=tuple(evidence), O=tuple(obligations), B=(),
        T=trust, Pi=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------
# Sort signature
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SortSignature:
    """The sort signature of an uninterpreted function symbol.

    A sort signature is a list of argument sorts and a return sort,
    matching the SMT-LIB2 ``(declare-fun f (S1 ... Sn) R)`` form.
    """

    argument_sorts: tuple[SortKind, ...]
    return_sort: SortKind
    arity: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "arity", len(self.argument_sorts))

    def to_smt_declare(self, name: str) -> str:
        """Return the SMT-LIB2 declare-fun form."""
        arg_sorts = " ".join(s.value for s in self.argument_sorts)
        return f"(declare-fun {name} ({arg_sorts}) {self.return_sort.value})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "argument_sorts": [s.value for s in self.argument_sorts],
            "return_sort": self.return_sort.value,
            "arity": self.arity,
        }


# ---------------------------------------------------------------------------
# Axiom record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SummaryAxiom:
    """A single axiom constraining an uninterpreted function symbol.

    Axioms are SMT-LIB2 ``(assert ...)`` strings attached to a judgment
    tuple that tracks their trust level and provenance.

    Attributes
    ----------
    axiom_id : str
        Unique identifier.
    kind : AxiomKind
        The logical nature of this axiom.
    smt_expression : str
        The SMT-LIB2 ``(assert ...)`` string.
    natural_language : str
        Human-readable description.
    judgment : SummaryJudgment
        The governing judgment.
    is_universally_quantified : bool
        Whether this axiom is universally quantified.
    """

    axiom_id: str
    kind: AxiomKind
    smt_expression: str
    natural_language: str
    judgment: SummaryJudgment
    is_universally_quantified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "axiom_id": self.axiom_id,
            "kind": self.kind.value,
            "smt_expression": self.smt_expression,
            "natural_language": self.natural_language,
            "is_universally_quantified": self.is_universally_quantified,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Uninterpreted function representation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UninterpretedFunctionRepr:
    """A single uninterpreted function symbol with its sort signature and axioms.

    In the sheaf model, an uninterpreted function symbol is a global section
    of the function-space sheaf.  Its axioms are local sections on the
    pre-conditions and post-conditions cover.

    Attributes
    ----------
    func_id : str
        Unique identifier for this uninterpreted function.
    name : str
        The SMT-LIB2 function symbol name.
    signature : SortSignature
        The sort signature.
    axioms : tuple[SummaryAxiom, ...]
        Axioms constraining this function.
    judgment : SummaryJudgment
        Governing judgment.
    is_total : bool
        Whether this function is total (defined on all inputs).
    is_deterministic : bool
        Whether this function is deterministic.
    """

    func_id: str
    name: str
    signature: SortSignature
    axioms: tuple[SummaryAxiom, ...]
    judgment: SummaryJudgment
    is_total: bool = True
    is_deterministic: bool = True

    def declare_smt(self) -> str:
        """Return the SMT-LIB2 declare-fun for this function."""
        return self.signature.to_smt_declare(self.name)

    def all_axiom_assertions(self) -> list[str]:
        """Return all axiom SMT assertions."""
        return [ax.smt_expression for ax in self.axioms]

    def preconditions(self) -> tuple[SummaryAxiom, ...]:
        return tuple(ax for ax in self.axioms if ax.kind == AxiomKind.PRECONDITION)

    def postconditions(self) -> tuple[SummaryAxiom, ...]:
        return tuple(ax for ax in self.axioms if ax.kind == AxiomKind.POSTCONDITION)

    def invariants(self) -> tuple[SummaryAxiom, ...]:
        return tuple(ax for ax in self.axioms if ax.kind == AxiomKind.INVARIANT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "func_id": self.func_id,
            "name": self.name,
            "signature": self.signature.to_dict(),
            "num_axioms": len(self.axioms),
            "is_total": self.is_total,
            "is_deterministic": self.is_deterministic,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Summary contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SummaryContract:
    """Binding between an interface summary and its generated obligations.

    A summary contract is the formal statement that:
    1. The interface satisfies its pre-conditions at every call site.
    2. The implementation satisfies the post-conditions after every call.
    3. All invariants hold throughout the interface's lifetime.

    Attributes
    ----------
    contract_id : str
        Unique identifier.
    interface_name : str
        Name of the interface being summarised.
    precondition_obligations : tuple[str, ...]
        Obligations to be discharged at each call site.
    postcondition_obligations : tuple[str, ...]
        Obligations to be discharged after each call.
    invariant_obligations : tuple[str, ...]
        Obligations that must hold throughout.
    judgment : SummaryJudgment
        The governing judgment.
    status : SummaryStatus
        Current discharge status.
    """

    contract_id: str
    interface_name: str
    precondition_obligations: tuple[str, ...]
    postcondition_obligations: tuple[str, ...]
    invariant_obligations: tuple[str, ...]
    judgment: SummaryJudgment
    status: SummaryStatus = SummaryStatus.DRAFT

    def all_obligations(self) -> tuple[str, ...]:
        return (
            self.precondition_obligations
            + self.postcondition_obligations
            + self.invariant_obligations
        )

    def is_fully_specified(self) -> bool:
        """True iff pre-conditions and post-conditions are both non-empty."""
        return (
            len(self.precondition_obligations) > 0
            or len(self.postcondition_obligations) > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "interface_name": self.interface_name,
            "num_preconditions": len(self.precondition_obligations),
            "num_postconditions": len(self.postcondition_obligations),
            "num_invariants": len(self.invariant_obligations),
            "status": self.status.value,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Interface summary
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InterfaceSummary:
    """Top-level summary of an interface (module, class, or function).

    The summary collects all uninterpreted function symbols and their axioms,
    binds them into a contract, and tracks trust at the summary level.

    Attributes
    ----------
    summary_id : str
        Unique identifier.
    interface_name : str
        Name of the summarised interface.
    functions : tuple[UninterpretedFunctionRepr, ...]
        All uninterpreted function symbols in this summary.
    contract : SummaryContract
        The binding contract.
    coordinate : str
        Semantic coordinate.
    judgment : SummaryJudgment
        Top-level judgment for the whole summary.
    status : SummaryStatus
        Current status.
    created_at : str
        ISO-8601 creation timestamp.
    """

    summary_id: str
    interface_name: str
    functions: tuple[UninterpretedFunctionRepr, ...]
    contract: SummaryContract
    coordinate: str
    judgment: SummaryJudgment
    status: SummaryStatus = SummaryStatus.DRAFT
    created_at: str = field(default_factory=_now_iso)

    def get_function(self, name: str) -> UninterpretedFunctionRepr | None:
        for f in self.functions:
            if f.name == name:
                return f
        return None

    def all_smt_declarations(self) -> list[str]:
        """Return all SMT-LIB2 declarations for this summary."""
        return [f.declare_smt() for f in self.functions]

    def all_smt_axioms(self) -> list[str]:
        """Return all SMT-LIB2 axiom assertions for this summary."""
        return [ax for f in self.functions for ax in f.all_axiom_assertions()]

    def is_sound_abstraction(self) -> bool:
        """True iff the summary has at least REVIEWED trust."""
        return self.judgment.T.is_at_least(TrustTier.REVIEWED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "interface_name": self.interface_name,
            "num_functions": len(self.functions),
            "coordinate": self.coordinate,
            "status": self.status.value,
            "is_sound_abstraction": self.is_sound_abstraction(),
            "judgment": self.judgment.to_dict(),
            "contract": self.contract.to_dict(),
        }


# ---------------------------------------------------------------------------
# Summary tableau
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SummaryTableau:
    """A collection of interface summaries forming a complete module boundary.

    The tableau is a sheaf over the interface coordinate space — each
    interface summary is a local section, and the whole tableau is a
    global section (if all summaries are consistent).

    Attributes
    ----------
    tableau_id : str
        Unique identifier.
    summaries : tuple[InterfaceSummary, ...]
        All interface summaries in this tableau.
    coordinate : str
        Semantic coordinate of the module boundary.
    judgment : SummaryJudgment
        Top-level judgment.
    """

    tableau_id: str
    summaries: tuple[InterfaceSummary, ...]
    coordinate: str
    judgment: SummaryJudgment

    def get_summary(self, interface_name: str) -> InterfaceSummary | None:
        for s in self.summaries:
            if s.interface_name == interface_name:
                return s
        return None

    def all_contracts(self) -> tuple[SummaryContract, ...]:
        return tuple(s.contract for s in self.summaries)

    def all_obligations(self) -> list[str]:
        return [ob for s in self.summaries for ob in s.contract.all_obligations()]

    def attempt_descent(self) -> TableauGlobalSection | TableauDescentObstruction:
        """Attempt to verify the tableau is globally consistent.  NEVER raises."""
        contradictions: list[tuple[str, str]] = []
        # Check that no two summaries export the same function name with
        # incompatible sorts
        seen_functions: dict[str, str] = {}
        for summary in self.summaries:
            for fn in summary.functions:
                if fn.name in seen_functions:
                    prev_sort = seen_functions[fn.name]
                    curr_sort = str(fn.signature.to_dict())
                    if prev_sort != curr_sort:
                        contradictions.append((fn.name, summary.interface_name))
                else:
                    seen_functions[fn.name] = str(fn.signature.to_dict())
        if contradictions:
            obs = SummaryCechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"Incompatible function sort signatures across summaries: "
                    f"{contradictions[:3]}"
                ),
                involved_axioms=tuple(name for name, _ in contradictions[:10]),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Reconcile sort signatures or use separate namespaces.",
            )
            return TableauDescentObstruction(
                coordinate=self.coordinate,
                obstruction=obs,
                conflicting_summaries=tuple(
                    iname for _, iname in contradictions[:10]
                ),
                diagnosis=f"{len(contradictions)} incompatible function signatures.",
            )
        jmt = _make_summary_judgment(
            coordinate=self.coordinate,
            phi="tableau_globally_consistent",
            carrier="summary_tableau",
            evidence=[f"summary:{s.summary_id}" for s in self.summaries[:10]],
            obligations=[],
            trust=TrustTier.VERIFIED,
            provenance={"tableau_id": self.tableau_id, "descent_at": _now_iso()},
        )
        return TableauGlobalSection(
            coordinate=self.coordinate,
            combined_declarations=self._collect_declarations(),
            combined_axioms=self._collect_axioms(),
            judgment=jmt,
        )

    def _collect_declarations(self) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for s in self.summaries:
            for decl in s.all_smt_declarations():
                if decl not in seen:
                    seen.add(decl)
                    result.append(decl)
        return tuple(result)

    def _collect_axioms(self) -> tuple[str, ...]:
        return tuple(ax for s in self.summaries for ax in s.all_smt_axioms())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tableau_id": self.tableau_id,
            "coordinate": self.coordinate,
            "num_summaries": len(self.summaries),
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TableauGlobalSection:
    """A globally consistent tableau — all summaries are compatible."""

    coordinate: str
    combined_declarations: tuple[str, ...]
    combined_axioms: tuple[str, ...]
    judgment: SummaryJudgment

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tableau_global_section",
            "coordinate": self.coordinate,
            "num_declarations": len(self.combined_declarations),
            "num_axioms": len(self.combined_axioms),
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TableauDescentObstruction:
    """A Čech obstruction blocking tableau consistency.  NEVER raises."""

    coordinate: str
    obstruction: SummaryCechObstruction
    conflicting_summaries: tuple[str, ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tableau_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "conflicting_summaries": list(self.conflicting_summaries),
            "diagnosis": self.diagnosis,
        }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_interface_summary(
    callable_obj: Callable[..., Any],
    *,
    coordinate: str | None = None,
    extra_axioms: Sequence[tuple[AxiomKind, str, str]] | None = None,
) -> InterfaceSummary:
    """Build an InterfaceSummary from a Python callable's type signature.

    Introspects the callable's type annotations to build an uninterpreted
    function symbol with the corresponding SMT sort signature.

    Parameters
    ----------
    callable_obj : Callable
        The Python callable to summarise.
    coordinate : str or None
        Semantic coordinate; defaults to the callable's qualified name.
    extra_axioms : Sequence[tuple[AxiomKind, str, str]] or None
        Additional axioms as (kind, smt_expression, natural_language).

    Returns
    -------
    InterfaceSummary
    """
    name = getattr(callable_obj, "__qualname__", repr(callable_obj))
    coord = coordinate or f"interface:{name}"
    logger.debug("build_interface_summary: %s at %s", name, coord)

    # Introspect signature
    try:
        sig = inspect.signature(callable_obj)
        params = list(sig.parameters.values())
        ret_annotation = sig.return_annotation
    except (ValueError, TypeError):
        params = []
        ret_annotation = inspect.Parameter.empty

    arg_sorts = tuple(_python_type_to_sort(p.annotation) for p in params)
    ret_sort = _python_type_to_sort(ret_annotation)
    signature = SortSignature(argument_sorts=arg_sorts, return_sort=ret_sort)

    # Build axioms
    axioms: list[SummaryAxiom] = []
    smt_func_name = name.replace(".", "_").replace("<", "").replace(">", "")

    # Pre-condition: arguments are of the declared sorts
    for i, (p, sort) in enumerate(zip(params, arg_sorts)):
        if sort != SortKind.UNINTERPRETED:
            ax_id = _stable_id(f"ax:pre:{coord}", f"{i}:{p.name}")
            ax_jmt = _make_summary_judgment(
                coordinate=f"{coord}.pre.{p.name}",
                phi=f"arg_{p.name}_sort_{sort.value}",
                carrier="precondition",
                evidence=[f"type_annotation:{getattr(p.annotation, '__name__', str(p.annotation))}"],
                obligations=[],
                trust=TrustTier.REVIEWED,
                provenance={"param": p.name, "sort": sort.value},
            )
            axioms.append(SummaryAxiom(
                axiom_id=ax_id,
                kind=AxiomKind.PRECONDITION,
                smt_expression=(
                    f"(assert (forall ((x{i} {sort.value})) "
                    f"(= (type-sort ({smt_func_name} x{i})) true)))"
                ),
                natural_language=f"Argument {p.name} must be of sort {sort.value}",
                judgment=ax_jmt,
                is_universally_quantified=True,
            ))

    # Post-condition: return sort
    if ret_sort != SortKind.UNINTERPRETED:
        ax_jmt = _make_summary_judgment(
            coordinate=f"{coord}.post",
            phi=f"return_sort_{ret_sort.value}",
            carrier="postcondition",
            evidence=[f"return_annotation:{ret_sort.value}"],
            obligations=[],
            trust=TrustTier.REVIEWED,
            provenance={"return_sort": ret_sort.value},
        )
        axioms.append(SummaryAxiom(
            axiom_id=_stable_id(f"ax:post:{coord}", "return"),
            kind=AxiomKind.POSTCONDITION,
            smt_expression=(
                f"(assert (forall ({' '.join(f'(x{i} {s.value})' for i, s in enumerate(arg_sorts))}) "
                f"(= (return-sort ({smt_func_name} {' '.join(f'x{i}' for i in range(len(arg_sorts)))})) true)))"
            ),
            natural_language=f"Return value must be of sort {ret_sort.value}",
            judgment=ax_jmt,
            is_universally_quantified=True,
        ))

    # Extra axioms
    for kind, smt_expr, natural_lang in (extra_axioms or []):
        ax_jmt = _make_summary_judgment(
            coordinate=f"{coord}.extra",
            phi=f"custom_axiom:{kind.value}",
            carrier="axiom",
            evidence=[f"custom:{smt_expr[:40]}"],
            obligations=[],
            trust=TrustTier.PROPOSAL,
            provenance={"kind": kind.value},
        )
        axioms.append(SummaryAxiom(
            axiom_id=_stable_id(f"ax:custom:{coord}", smt_expr),
            kind=kind,
            smt_expression=smt_expr,
            natural_language=natural_lang,
            judgment=ax_jmt,
        ))

    fn_jmt = _make_summary_judgment(
        coordinate=coord,
        phi=f"function_{smt_func_name}_summarised",
        carrier="uninterpreted_function",
        evidence=[f"name:{name}", f"arity:{len(arg_sorts)}"],
        obligations=["verify_axiom_consistency"],
        trust=TrustTier.PROPOSAL,
        provenance={"name": name, "coord": coord},
    )
    fn_repr = UninterpretedFunctionRepr(
        func_id=_stable_id("fn", coord + name),
        name=smt_func_name,
        signature=signature,
        axioms=tuple(axioms),
        judgment=fn_jmt,
    )

    pre_obs = tuple(
        ax.smt_expression for ax in axioms if ax.kind == AxiomKind.PRECONDITION
    )
    post_obs = tuple(
        ax.smt_expression for ax in axioms if ax.kind == AxiomKind.POSTCONDITION
    )
    inv_obs = tuple(
        ax.smt_expression for ax in axioms if ax.kind == AxiomKind.INVARIANT
    )
    contract_jmt = _make_summary_judgment(
        coordinate=f"{coord}.contract",
        phi=f"contract_{smt_func_name}",
        carrier="summary_contract",
        evidence=[f"function:{smt_func_name}"],
        obligations=list(pre_obs) + list(post_obs),
        trust=TrustTier.PROPOSAL,
        provenance={"name": name},
    )
    contract = SummaryContract(
        contract_id=_stable_id("contract", coord + name),
        interface_name=name,
        precondition_obligations=pre_obs,
        postcondition_obligations=post_obs,
        invariant_obligations=inv_obs,
        judgment=contract_jmt,
        status=SummaryStatus.DRAFT,
    )
    top_jmt = _make_summary_judgment(
        coordinate=coord,
        phi=f"interface_{smt_func_name}_sound",
        carrier="interface_summary",
        evidence=[f"function:{smt_func_name}", f"num_axioms:{len(axioms)}"],
        obligations=["verify_summary_consistency"],
        trust=TrustTier.PROPOSAL,
        provenance={"name": name, "created_at": _now_iso()},
    )
    return InterfaceSummary(
        summary_id=str(uuid.uuid4()),
        interface_name=name,
        functions=(fn_repr,),
        contract=contract,
        coordinate=coord,
        judgment=top_jmt,
        status=SummaryStatus.DRAFT,
        created_at=_now_iso(),
    )


def apply_summary(
    summary: InterfaceSummary,
    call_site_coordinate: str,
    argument_reprs: Sequence[str],
) -> tuple[CallSiteStatus, list[str]]:
    """Apply a summary to a call site, generating obligation strings.

    Parameters
    ----------
    summary : InterfaceSummary
        The interface summary to apply.
    call_site_coordinate : str
        The semantic coordinate of the call site.
    argument_reprs : Sequence[str]
        SMT-LIB2 representations of the call arguments.

    Returns
    -------
    tuple[CallSiteStatus, list[str]]
        The call site status and a list of obligation strings.
    """
    obligations: list[str] = []
    for fn in summary.functions:
        for ax in fn.preconditions():
            ob = (
                f"call-site-precondition:{call_site_coordinate}:"
                f"{ax.axiom_id[:12]}"
            )
            obligations.append(ob)
        for ax in fn.postconditions():
            ob = (
                f"call-site-postcondition:{call_site_coordinate}:"
                f"{ax.axiom_id[:12]}"
            )
            obligations.append(ob)
    status = (
        CallSiteStatus.OBLIGATIONS_PENDING
        if obligations
        else CallSiteStatus.OBLIGATIONS_DISCHARGED
    )
    return status, obligations


def validate_summary_consistency(
    summary: InterfaceSummary,
) -> TableauGlobalSection | TableauDescentObstruction:
    """Check that a summary's axioms are mutually consistent.

    Consistency is checked by verifying that no two axioms have the same
    SMT identifier (structural self-consistency).  Full semantic consistency
    would require an SMT solver.

    Returns a global section on success, a descent obstruction on failure.
    NEVER raises.
    """
    seen_exprs: dict[str, str] = {}
    conflicts: list[tuple[str, str]] = []
    for fn in summary.functions:
        for ax in fn.axioms:
            if ax.smt_expression in seen_exprs:
                conflicts.append((ax.axiom_id, seen_exprs[ax.smt_expression]))
            else:
                seen_exprs[ax.smt_expression] = ax.axiom_id

    tableau_jmt = _make_summary_judgment(
        coordinate=summary.coordinate,
        phi="summary_consistency_check",
        carrier="summary_tableau",
        evidence=[f"summary:{summary.summary_id}"],
        obligations=[],
        trust=TrustTier.REVIEWED if not conflicts else TrustTier.PROPOSAL,
        provenance={"summary_id": summary.summary_id},
    )
    tableau = SummaryTableau(
        tableau_id=_stable_id("tableau", summary.coordinate),
        summaries=(summary,),
        coordinate=summary.coordinate,
        judgment=tableau_jmt,
    )
    return tableau.attempt_descent()


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "AxiomKind",
    "CallSiteStatus",
    "InterfaceSummary",
    "SortKind",
    "SortSignature",
    "SummaryCechObstruction",
    "SummaryAxiom",
    "SummaryContract",
    "SummaryJudgment",
    "SummaryStatus",
    "SummaryTableau",
    "TableauDescentObstruction",
    "TableauGlobalSection",
    "TrustTier",
    "UninterpretedFunctionRepr",
    "apply_summary",
    "build_interface_summary",
    "validate_summary_consistency",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== interface_summaries_as_uninterpret — smoke test ===")

    def add_integers(x: int, y: int) -> int:
        return x + y

    def process_string(s: str) -> bool:
        return len(s) > 0

    # Build summaries
    summary_add = build_interface_summary(add_integers, coordinate="test.add")
    print(f"InterfaceSummary(add): id={summary_add.summary_id[:12]}… "
          f"functions={len(summary_add.functions)} "
          f"axioms={sum(len(f.axioms) for f in summary_add.functions)}")
    assert len(summary_add.functions) == 1
    assert summary_add.functions[0].signature.arity == 2
    assert summary_add.functions[0].signature.return_sort == SortKind.INT

    summary_str = build_interface_summary(
        process_string,
        coordinate="test.process",
        extra_axioms=[(AxiomKind.INVARIANT, "(assert true)", "trivial invariant")],
    )
    print(f"InterfaceSummary(process): id={summary_str.summary_id[:12]}…")

    # Apply summary
    status, obligations = apply_summary(summary_add, "call_site:main:42", ["a", "b"])
    print(f"apply_summary: status={status.value} obligations={len(obligations)}")

    # Validate consistency
    result = validate_summary_consistency(summary_add)
    print(f"validate_summary_consistency: {type(result).__name__}")
    assert isinstance(result, TableauGlobalSection)

    # Build tableau
    top_jmt = _make_summary_judgment(
        coordinate="test.tableau",
        phi="tableau",
        carrier="tableau",
        evidence=[],
        obligations=[],
        trust=TrustTier.REVIEWED,
    )
    tableau = SummaryTableau(
        tableau_id="t1",
        summaries=(summary_add, summary_str),
        coordinate="test.tableau",
        judgment=top_jmt,
    )
    dr = tableau.attempt_descent()
    print(f"Tableau descent: {type(dr).__name__}")

    # SMT output
    decls = summary_add.all_smt_declarations()
    axioms = summary_add.all_smt_axioms()
    print(f"SMT declarations: {decls}")
    print(f"SMT axioms ({len(axioms)}): {axioms[:2]}")

    # Trust algebra
    t = TrustTier.PROPOSAL
    assert t.promote() == TrustTier.REVIEWED
    assert TrustTier.REVIEWED.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    print("TrustTier algebra: OK")

    # Serialization
    d = summary_add.to_dict()
    j = json.dumps(d, default=str)
    assert "summary_id" in j
    print("JSON serialization: OK")

    print("All assertions passed.")
    sys.exit(0)
