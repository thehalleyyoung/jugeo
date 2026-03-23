"""
jugeo.python_runtime.generated_contracts.integration
=====================================================

theory2.tex Ch21 — Integration Layer
bridge architecture

This module implements the Integration Layer for the jugeo contract system,
providing a bridge architecture that connects the generated-contract functor G
to the judgment lattice as specified in theory2.tex Chapter 21.

# copilot: ContractIntegration bridges the generated-contract functor G to the judgment lattice

The integration layer consists of three primary bridges:
  - SolverContractBridge: Encodes contracts as SMT formulae and submits to a solver
  - EvidenceContractBridge: Records runtime observations and aggregates evidence
  - SpecSatisfactionBridge: Checks whether contracts satisfy a given specification

These bridges are orchestrated by the ContractIntegrationCoordinator, which
produces consolidated judgment reports in the canonical tuple form
    (c, phi, A, E, O, B, T, Pi)
where c = coordinate, phi = formula, A = carrier, E = evidence, O = obligations,
B = obstructions, T = trust tier, Pi = provenance.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import functools
import json
import logging
import math
import time
import typing
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo geometry / contract imports with frozen-dataclass stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, SiteRegistry
except Exception:
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        """Stub for jugeo.geometry.site.CoordinateObject."""
        key: str = ""
        kind: str = ""

    @dataclass(frozen=True, slots=True)
    class SiteRegistry:
        """Stub for jugeo.geometry.site.SiteRegistry."""
        registry_id: str = ""
        entries: tuple = field(default_factory=tuple)

try:
    from jugeo.contracts.base import ContractEntry, ContractSpec
except Exception:
    @dataclass(frozen=True, slots=True)
    class ContractEntry:
        """Stub for jugeo.contracts.base.ContractEntry."""
        contract_id: str = ""
        label: str = ""
        payload: str = ""

    @dataclass(frozen=True, slots=True)
    class ContractSpec:
        """Stub for jugeo.contracts.base.ContractSpec."""
        spec_id: str = ""
        clauses: tuple = field(default_factory=tuple)

try:
    from jugeo.lattice.judgment import JudgmentTuple, TrustLevel
except Exception:
    @dataclass(frozen=True, slots=True)
    class JudgmentTuple:
        """Stub for jugeo.lattice.judgment.JudgmentTuple."""
        coordinate: str = ""
        formula: str = ""
        carrier: object = None
        evidence: dict = field(default_factory=dict)
        obligations: list = field(default_factory=list)
        obstructions: list = field(default_factory=list)
        trust_tier: str = ""
        provenance: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class TrustLevel:
        """Stub for jugeo.lattice.judgment.TrustLevel."""
        level: int = 1
        label: str = "PROPOSAL"

try:
    from jugeo.solver.interface import SolverSession, SolverBackend
except Exception:
    @dataclass(frozen=True, slots=True)
    class SolverSession:
        """Stub for jugeo.solver.interface.SolverSession."""
        session_id: str = ""
        backend: str = "heuristic"

    @dataclass(frozen=True, slots=True)
    class SolverBackend:
        """Stub for jugeo.solver.interface.SolverBackend."""
        name: str = "heuristic"
        version: str = "0.0.0"

try:
    from jugeo.evidence.store import EvidenceStore, ObservationRecord
except Exception:
    @dataclass(frozen=True, slots=True)
    class EvidenceStore:
        """Stub for jugeo.evidence.store.EvidenceStore."""
        store_id: str = ""

    @dataclass(frozen=True, slots=True)
    class ObservationRecord:
        """Stub for jugeo.evidence.store.ObservationRecord."""
        record_id: str = ""
        value: str = ""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(enum.IntEnum):
    """
    Ordered trust tiers for the judgment lattice.

    Mirrors the five-level trust hierarchy from theory2.tex Section 21.3.
    Each level represents an increasing degree of epistemic confidence:

      PROPOSAL          - An unreviewed claim submitted by a participant.
      REVIEWED          - A human reviewer has inspected and approved the claim.
      VERIFIED          - An automated tool (type-checker, linter) has verified it.
      RUNTIME_WITNESSED - The claim has been observed to hold at runtime.
      PROOF_BACKED      - A formal proof (SMT, Coq) backs the claim.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    @property
    def description(self) -> str:
        """Return a human-readable description of this trust tier."""
        _desc: Dict[int, str] = {
            1: "Unreviewed proposal",
            2: "Human-reviewed claim",
            3: "Automated-verification result",
            4: "Runtime-witnessed observation",
            5: "Formally proof-backed assertion",
        }
        return _desc[self.value]


class IntegrationKind(enum.Enum):
    """
    Classifies the primary integration strategy for a ContractIntegration.

    SOLVER            - Contract validity is decided by an SMT/SAT solver.
    EVIDENCE          - Contract validity is established by evidence accumulation.
    SPEC_SATISFACTION - Contract validity is measured against a formal spec.
    HYBRID            - Multiple strategies are combined.
    """

    SOLVER = "solver"
    EVIDENCE = "evidence"
    SPEC_SATISFACTION = "spec_satisfaction"
    HYBRID = "hybrid"


class BridgeStatus(enum.Enum):
    """
    Lifecycle status of a ContractIntegration bridge session.

    PENDING    - Created but not yet activated.
    ACTIVE     - Currently processing contracts.
    DISCHARGED - All obligations have been discharged successfully.
    FAILED     - One or more obligations could not be discharged.
    SUSPENDED  - Processing is paused, awaiting external input.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DISCHARGED = "discharged"
    FAILED = "failed"
    SUSPENDED = "suspended"


# ---------------------------------------------------------------------------
# SolverFormula
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SolverFormula:
    """
    An SMT-LIB2 formula derived from a contract entry.

    Attributes
    ----------
    formula_id     : Unique identifier for this formula.
    smt_text       : Raw SMT-LIB2 assertion text.
    coordinate_key : The coordinate (site key) this formula is attached to.
    contract_id    : The originating contract identifier.
    trust_tier     : Name of the TrustTier at submission time.
    """

    formula_id: str
    smt_text: str
    coordinate_key: str
    contract_id: str
    trust_tier: str

    def to_smt2(self) -> str:
        """
        Produce a self-contained SMT-LIB2 script for this formula.

        Returns
        -------
        str
            A minimal SMT-LIB2 script that asserts the formula and
            requests a satisfiability check.
        """
        lines = [
            "(set-logic QF_LIA)",
            f"; formula_id: {self.formula_id}",
            f"; contract_id: {self.contract_id}",
            f"; coordinate: {self.coordinate_key}",
            f"; trust_tier: {self.trust_tier}",
            "",
            self.smt_text,
            "",
            "(check-sat)",
            "(get-model)",
        ]
        return "\n".join(lines)

    def negate(self) -> "SolverFormula":
        """
        Return a new SolverFormula whose assertion is the negation of this one.

        This is used to generate counterexample-search queries: if the
        negation is *unsatisfiable* the original formula is *valid*.

        Returns
        -------
        SolverFormula
            A new formula with ``(assert (not ...))`` wrapping the inner text.
        """
        stripped = self.smt_text.strip()
        if stripped.startswith("(assert ") and stripped.endswith(")"):
            inner_expr = stripped[len("(assert "):-1].strip()
        else:
            inner_expr = stripped
        negated_text = f"(assert (not {inner_expr}))"
        logger.debug("Negating formula %s -> %s", self.formula_id, negated_text)
        return dataclasses.replace(
            self,
            formula_id=f"{self.formula_id}_negated",
            smt_text=negated_text,
        )


# ---------------------------------------------------------------------------
# SolverResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SolverResult:
    """
    The outcome produced by submitting a SolverFormula to a solver backend.

    Attributes
    ----------
    result_id      : Unique identifier for this result.
    formula_id     : The formula that was checked.
    outcome        : One of "sat", "unsat", "unknown".
    model          : A satisfying model (variable assignments), if any.
    counterexample : A counterexample dict when outcome is "unsat", or None.
    elapsed_ms     : Wall-clock time consumed by the solver call.
    """

    result_id: str
    formula_id: str
    outcome: str
    model: dict = field(default_factory=dict)
    counterexample: Optional[dict] = None
    elapsed_ms: float = 0.0

    def is_sat(self) -> bool:
        """Return True iff the outcome is satisfiable."""
        return self.outcome == "sat"

    def to_judgment_tuple(self) -> Tuple:
        """
        Produce a canonical judgment tuple (c, phi, A, E, O, B, T, Pi).

        The eight slots are:
          c   - coordinate string (formula_id as proxy when coordinate unavailable)
          phi - the SMT formula identifier
          A   - carrier: a summary dict derived from this SolverResult
          E   - evidence: the model dict (or empty dict)
          O   - obligations list derived from outcome (never a plain bool)
          B   - obstructions list (never a plain bool)
          T   - trust tier string
          Pi  - provenance metadata dict

        Returns
        -------
        tuple of length 8
        """
        c = self.formula_id
        phi = f"smt:{self.formula_id}"
        carrier: Dict[str, Any] = {
            "result_id": self.result_id,
            "outcome": self.outcome,
            "elapsed_ms": self.elapsed_ms,
        }
        evidence: Dict[str, Any] = dict(self.model) if self.model else {}
        if self.outcome == "unsat":
            obligations: List[str] = ["formula_valid"]
            obstructions: List[str] = []
            trust_tier = TrustTier.PROOF_BACKED.name
        elif self.outcome == "sat":
            obligations = ["model_found"]
            obstructions = ["negation_satisfiable"]
            trust_tier = TrustTier.VERIFIED.name
        else:
            obligations = []
            obstructions = ["solver_inconclusive"]
            trust_tier = TrustTier.REVIEWED.name
        provenance: Dict[str, Any] = {
            "result_id": self.result_id,
            "formula_id": self.formula_id,
            "elapsed_ms": self.elapsed_ms,
            "backend": "z3_or_heuristic",
            "timestamp": time.time(),
        }
        logger.debug(
            "SolverResult %s -> judgment tuple tier=%s", self.result_id, trust_tier
        )
        return (c, phi, carrier, evidence, obligations, obstructions, trust_tier, provenance)


# ---------------------------------------------------------------------------
# EvidenceRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """
    A single runtime observation recorded against a contract.

    Attributes
    ----------
    record_id        : Unique identifier for this observation.
    contract_id      : The contract this observation is attached to.
    witness_value    : String-serialised value that was observed.
    observation_time : Unix timestamp at which the observation was made.
    trust_tier       : Name of the TrustTier assigned to this observation.
    observation_type : Category label (e.g. "runtime", "test", "manual").
    """

    record_id: str
    contract_id: str
    witness_value: str
    observation_time: float
    trust_tier: str
    observation_type: str

    def age_seconds(self) -> float:
        """
        Return the number of seconds elapsed since this observation was made.

        Returns
        -------
        float
            Non-negative elapsed seconds.
        """
        return max(0.0, time.time() - self.observation_time)

    def to_judgment_tuple(self) -> Tuple:
        """
        Produce a canonical judgment tuple (c, phi, A, E, O, B, T, Pi).

        Slots:
          c   - contract_id (as coordinate proxy)
          phi - "evidence:<observation_type>"
          A   - carrier summary dict
          E   - evidence dict containing witness_value and observation_time
          O   - obligations list: ["observation_recorded"]
          B   - obstructions list: populated if observation is stale (>3600 s)
          T   - trust_tier string
          Pi  - provenance dict

        Returns
        -------
        tuple of length 8
        """
        c = self.contract_id
        phi = f"evidence:{self.observation_type}"
        carrier: Dict[str, Any] = {
            "record_id": self.record_id,
            "witness_value": self.witness_value,
            "observation_type": self.observation_type,
        }
        evidence: Dict[str, Any] = {
            "witness_value": self.witness_value,
            "observation_time": self.observation_time,
            "age_seconds": self.age_seconds(),
        }
        obligations: List[str] = ["observation_recorded"]
        obstructions: List[str] = (
            ["observation_stale"] if self.age_seconds() > 3600.0 else []
        )
        provenance: Dict[str, Any] = {
            "record_id": self.record_id,
            "contract_id": self.contract_id,
            "observation_time": self.observation_time,
            "observation_type": self.observation_type,
        }
        logger.debug("EvidenceRecord %s -> judgment tuple", self.record_id)
        return (c, phi, carrier, evidence, obligations, obstructions, self.trust_tier, provenance)
        annotation = (
            getattr(record, "annotation_text", None)
            or getattr(record, "description", None)
            or getattr(record, "formula", None)
            or "Any"
        )
        trust = getattr(record, "trust_level", None) or TrustLevel.UNVERIFIED
        source = getattr(record, "source", "duck_typed_record")

        if not symbol:
            return None

        return self.emit(
            symbol     = str(symbol),
            annotation = str(annotation),
            trust      = trust,
            evidence   = [],
            source     = source,
        )

    def emit_batch(self, records: list) -> list[Judgment]:
        """
        Emit a Judgment for each record in the batch.

        Records that fail emit_from_record() are silently skipped.
        Returns a list of successfully emitted Judgments.
        """
        judgments: list[Judgment] = []
        for record in records:
            try:
                j = self.emit_from_record(record)
                if j is not None:
                    judgments.append(j)
            except Exception as exc:
                logger.debug("JudgmentEmitter.emit_batch: skipped record: %s", exc)
        return judgments

    def flush(self) -> list[Judgment]:
        """
        Return all buffered Judgments and clear the buffer.

        After flush(), _emitted is empty.  This follows the producer-consumer
        pattern: the emitter fills the buffer; the bridge flushes it.
        """
        result = list(self._emitted)
        self._emitted.clear()
        logger.debug("JudgmentEmitter: flushed %d judgments", len(result))
        return result

    def count(self) -> int:
        """Return the number of judgments currently in the buffer."""
        return len(self._emitted)


# ──────────────────────────────────────────────────────────────────────────────
# CoordinateMapper — maps Python objects to CoordinateObjects
# ──────────────────────────────────────────────────────────────────────────────

class CoordinateMapper:
    """
    Maps Python objects to CoordinateObject instances.

    theory2.tex Ch21 §21.1.3 describes the coordinate functor: it assigns
    a CoordinateObject (a path in the site) to every Python module, class,
    or function.  The mapper caches previously mapped objects by their
    fully-qualified name to avoid redundant work.

    Morphisms between coordinates are built by build_morphism() and
    decorator_morphism(), modelling the decorator as a coordinate-level
    transformation.
    """

    def __init__(self) -> None:
        # copilot: _cache maps qualname → CoordinateObject to avoid recomputation
        self._cache: dict[str, CoordinateObject] = {}

    def map_module(self, module: Any) -> CoordinateObject:
        """
        Map a Python module to a CoordinateObject.

        Components = module.__name__.split(".").
        Kind = CoordinateKind.MODULE.
        """
        name = getattr(module, "__name__", repr(module))
        if name in self._cache:
            return self._cache[name]

        components = tuple(name.split("."))
        coord = CoordinateObject(
            components     = components,
            kind           = CoordinateKind.MODULE,
            support_labels = frozenset({name}),
            metadata       = {"module_name": name},
        )
        self._cache[name] = coord
        return coord

    def map_class(self, cls: Any) -> CoordinateObject:
        """
        Map a Python class to a CoordinateObject.

        Components = module_parts + (class.__name__,).
        Kind = CoordinateKind.INTERFACE.
        """
        module_name = getattr(cls, "__module__", "unknown") or "unknown"
        class_name  = getattr(cls, "__name__", repr(cls))
        qualname    = f"{module_name}.{class_name}"

        if qualname in self._cache:
            return self._cache[qualname]

        components = tuple(module_name.split(".")) + (class_name,)
        coord = CoordinateObject(
            components     = components,
            kind           = CoordinateKind.INTERFACE,
            support_labels = frozenset({class_name, qualname}),
            metadata       = {"module": module_name, "class_name": class_name},
        )
        self._cache[qualname] = coord
        return coord

    def map_function(self, func: Any) -> CoordinateObject:
        """
        Map a Python function to a CoordinateObject.

        Components = module_parts + qualname_parts (split on ".").
        Kind = CoordinateKind.FUNCTION.
        """
        module_name = getattr(func, "__module__", "unknown") or "unknown"
        qualname    = getattr(func, "__qualname__", getattr(func, "__name__", repr(func)))
        cache_key   = f"{module_name}.{qualname}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        module_parts  = tuple(module_name.split("."))
        qualname_parts = tuple(qualname.split("."))
        components    = module_parts + qualname_parts

        coord = CoordinateObject(
            components     = components,
            kind           = CoordinateKind.FUNCTION,
            support_labels = frozenset({qualname, cache_key}),
            metadata       = {"module": module_name, "qualname": qualname},
        )
        self._cache[cache_key] = coord
        return coord

    def map_any(self, obj: Any) -> CoordinateObject:
        """
        Dispatch to the correct mapper based on the object's type.

        Uses inspect.ismodule / isclass / callable to determine kind.
        Falls back to CoordinateKind.REGION for non-callable non-module objects.
        """
        if inspect.ismodule(obj):
            return self.map_module(obj)
        elif inspect.isclass(obj):
            return self.map_class(obj)
        elif callable(obj):
            return self.map_function(obj)
        else:
            # copilot: region for arbitrary objects (module-level values, etc.)
            qualname = repr(obj)[:32]
            if qualname in self._cache:
                return self._cache[qualname]
            coord = CoordinateObject(
                components     = ("unknown", qualname),
                kind           = CoordinateKind.REGION,
                support_labels = frozenset({qualname}),
                metadata       = {"repr": repr(obj)[:64]},
            )
            self._cache[qualname] = coord
            return coord

    def build_morphism(
        self,
        source: CoordinateObject,
        target: CoordinateObject,
        kind:   str,
    ) -> CoordinateMorphism:
        """
        Build a CoordinateMorphism between two CoordinateObjects.

        The reason string is set to "build_morphism({kind})" to provide
        a human-readable audit trail.
        """
        return CoordinateMorphism(
            source = source,
            target = target,
            reason = f"build_morphism({kind})",
        )

    def decorator_morphism(
        self,
        before_qualname: str,
        after_qualname:  str,
    ) -> CoordinateMorphism:
        """
        Build a CoordinateMorphism representing a decorator transformation.

        theory2.tex Ch21 §21.2 models the decorator @D applied to symbol S
        as a morphism φ_D: coord(S) → coord(D(S)).

        The source CoordinateObject is built from before_qualname and the
        target from after_qualname.
        """
        source_parts = tuple(before_qualname.split("."))
        target_parts = tuple(after_qualname.split("."))

        source = CoordinateObject(
            components     = source_parts,
            kind           = CoordinateKind.FUNCTION,
            support_labels = frozenset({before_qualname}),
        )
        target = CoordinateObject(
            components     = target_parts,
            kind           = CoordinateKind.FUNCTION,
            support_labels = frozenset({after_qualname}),
        )
        return CoordinateMorphism(
            source = source,
            target = target,
            reason = f"decorator_morphism({before_qualname} → {after_qualname})",
        )

    def all_mapped(self) -> list[CoordinateObject]:
        """Return all CoordinateObjects in the cache."""
        return list(self._cache.values())


# ──────────────────────────────────────────────────────────────────────────────
# SolverInterface — Z3/heuristic discharge interface
# ──────────────────────────────────────────────────────────────────────────────

class SolverInterface:
    """
    Attempts to discharge ResidualObligations via Z3 SMT solver or heuristics.

    theory2.tex Ch21 §21.5.4 defines the solver as an external discharge
    mechanism that promotes an obligation's trust level to SOLVER_DISCHARGED (4).

    When Z3 is unavailable, falls back to heuristic_discharge() which handles
    primitive type annotations (int, str, etc.) — these are trivially inhabited
    and can be discharged without a solver.
    """

    def __init__(self) -> None:
        # copilot: lazily check for z3 availability
        self._z3_available: bool = False
        self._z3_module: Any = None
        self._discharge_cache: dict[str, tuple[bool, str]] = {}

        try:
            import z3  # type: ignore[import]
            self._z3_module = z3
            self._z3_available = True
            logger.debug("SolverInterface: Z3 is available")
        except ImportError:
            logger.debug("SolverInterface: Z3 not available; using heuristics")

    def encode_annotation(self, symbol: str, annotation_text: str) -> str | None:
        """
        Encode an annotation as a Z3 formula string (if Z3 available).

        Returns a string like "(declare-const symbol Int)" for annotation_text="int",
        or None if Z3 is unavailable or the annotation cannot be encoded.

        theory2.tex Ch21 §21.5.4 uses SMT encoding to prove that a type is
        inhabited (i.e., the formula has a satisfying model).
        """
        if not self._z3_available:
            return None

        # copilot: map Python type annotations to Z3 sort declarations
        _type_to_z3_sort: dict[str, str] = {
            "int":   "Int",
            "float": "Real",
            "bool":  "Bool",
            "str":   "String",
        }
        z3_sort = _type_to_z3_sort.get(annotation_text)
        if z3_sort:
            return f"(declare-const {symbol} {z3_sort})"
        return f"(declare-const {symbol} Any)"

    def try_discharge(
        self, ob: ResidualObligation
    ) -> tuple[bool, str]:
        """
        Attempt to discharge a ResidualObligation.

        Strategy:
          1. Check _discharge_cache for a prior result.
          2. If Z3 available, attempt Z3-based discharge.
          3. Fall back to heuristic_discharge().
          4. Cache and return the result.

        Returns (discharged: bool, explanation: str).
        """
        cache_key = f"{ob.description}|{ob.obligation_id}"
        if cache_key in self._discharge_cache:
            return self._discharge_cache[cache_key]

        # copilot: try Z3 if available
        if self._z3_available:
            try:
                result = self._z3_discharge(ob)
                self._discharge_cache[cache_key] = result
                return result
            except Exception as exc:
                logger.debug("SolverInterface: Z3 discharge failed: %s", exc)

        result = self.heuristic_discharge(ob)
        self._discharge_cache[cache_key] = result
        return result

    def _z3_discharge(self, ob: ResidualObligation) -> tuple[bool, str]:
        """
        Attempt Z3-based discharge for an obligation.

        Creates a Z3 Solver, adds a satisfiability constraint for the
        obligation type, and checks for sat/unsat.  Returns (True, "z3_sat")
        if satisfiable, (False, "z3_unsat") if not.
        """
        z3 = self._z3_module
        solver = z3.Solver()

        # copilot: extract annotation from obligation description heuristically
        desc = ob.description
        ann_text = desc.split(":")[-1].strip() if ":" in desc else desc.strip()

        # copilot: map annotation to Z3 sort for sat check
        sort_map = {
            "int":   z3.IntSort(),
            "float": z3.RealSort(),
            "bool":  z3.BoolSort(),
        }
        z3_sort = sort_map.get(ann_text)
        if z3_sort is None:
            return self.heuristic_discharge(ob)

        # copilot: existence check: ∃ x: sort. True  ≡  sat
        x = z3.Const("x", z3_sort)
        solver.add(x == x)  # tautology; we're just checking sat of the sort
        status = solver.check()
        if str(status) == "sat":
            return (True, "z3_sat")
        return (False, "z3_unsat")

    def heuristic_discharge(
        self, ob: ResidualObligation
    ) -> tuple[bool, str]:
        """
        Heuristic discharge for common primitive type annotations.

        theory2.tex Ch21 §21.5.4 notes that primitive types are trivially
        inhabited: int ∋ 0, str ∋ "", float ∋ 0.0, bool ∋ False, bytes ∋ b"".

        Returns (True, "heuristic_primitive") for these types.
        Returns (False, "heuristic_complex") for other annotations.
        """
        desc     = ob.description or ""
        ann_text = desc.split(":")[-1].strip() if ":" in desc else desc.strip()

        # copilot: strip Optional wrapper
        if ann_text.startswith("Optional[") and ann_text.endswith("]"):
            ann_text = ann_text[9:-1].strip()

        if ann_text in _PRIMITIVE_ANNOTATIONS:
            return (True, "heuristic_primitive")

        # copilot: list, dict, set, tuple are also trivially inhabited (empty)
        if ann_text in {"list", "dict", "set", "tuple"}:
            return (True, "heuristic_primitive")

        # copilot: List[X], Dict[K,V], Set[X], Tuple[...] are inhabited if their
        # type arguments are inhabited (simplify: assume inhabited for now)
        for container in ("List[", "Dict[", "Set[", "Tuple[", "Sequence["):
            if ann_text.startswith(container):
                return (True, "heuristic_container")

        return (False, "heuristic_complex")

    def batch_discharge(
        self, obligations: list[ResidualObligation]
    ) -> list[tuple[bool, str]]:
        """
        Discharge a batch of obligations.

        Applies try_discharge() to each obligation and returns a list of
        (discharged, explanation) tuples in the same order.
        """
        return [self.try_discharge(ob) for ob in obligations]

    def is_z3_available(self) -> bool:
        """Return True if Z3 was successfully imported."""
        return self._z3_available

    def summary(self) -> str:
        """Return a human-readable summary of solver status and cache."""
        return (
            f"SolverInterface: z3_available={self._z3_available}, "
            f"cache_entries={len(self._discharge_cache)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# CopilotAdvisor — annotation proposal and advisory text generator
# ──────────────────────────────────────────────────────────────────────────────

class CopilotAdvisor:
    """
    Generates advisory text and type annotation proposals for unannotated symbols.

    theory2.tex Ch21 §21.3.7 introduces the Copilot advisory system: it
    uses heuristic pattern matching on parameter names and return type
    patterns to propose annotations and actionable remediation advice.

    The advisor is informed by _PARAM_NAME_TYPE_HINTS and naming conventions
    from PEP 8 and the JuGeo annotation standard.
    """

    def __init__(self) -> None:
        # copilot: _advice_log records all advisory calls for audit
        self._advice_log: list[dict] = []

    def advise_missing_annotation(self, symbol: str, context: dict) -> str:
        """
        Generate advisory text for a symbol with a missing annotation.

        Uses the symbol name and any available context (containing class,
        module, docstring) to generate a specific recommendation.
        """
        # copilot: check if name hints at a type
        proposed = _PARAM_NAME_TYPE_HINTS.get(symbol, None)
        if proposed:
            advice = (
                f"Consider annotating '{symbol}' with '{proposed}' "
                f"based on naming convention."
            )
        else:
            advice = (
                f"Symbol '{symbol}' is missing a type annotation. "
                f"Adding an annotation will enable JuGeo burden analysis."
            )

        entry = {
            "kind":    "missing_annotation",
            "symbol":  symbol,
            "advice":  advice,
            "context": {k: repr(v) for k, v in context.items()},
            "at":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._advice_log.append(entry)
        logger.debug("CopilotAdvisor: %s", advice)
        return advice

    def advise_fix_annotation(
        self, symbol: str, current: str, issue: str
    ) -> str:
        """
        Generate advisory text for a symbol with a problematic annotation.

        Provides a specific fix recommendation based on the issue type.
        Common issues: forward_ref_unresolved, contradicts_co_annotation,
        too_wide (Any or object), too_narrow (literal with many exclusions).
        """
        _fix_templates: dict[str, str] = {
            "forward_ref_unresolved": (
                f"Annotation '{current}' on '{symbol}' is a forward reference "
                f"that could not be resolved. Ensure the referenced type is "
                f"imported or defined before this annotation."
            ),
            "contradicts_co_annotation": (
                f"Annotation '{current}' on '{symbol}' contradicts another "
                f"annotation on the same symbol. Use a Union type or resolve "
                f"the contradiction by choosing one annotation."
            ),
            "too_wide": (
                f"Annotation 'Any' on '{symbol}' is too wide for burden analysis. "
                f"Consider narrowing it to a more specific type."
            ),
            "too_narrow": (
                f"Annotation '{current}' on '{symbol}' may be too narrow. "
                f"Consider using Optional[{current}] or a Union type."
            ),
        }

        advice = _fix_templates.get(
            issue,
            f"Annotation '{current}' on '{symbol}' has issue '{issue}'. "
            f"Review the annotation for correctness."
        )
        entry = {
            "kind":    "fix_annotation",
            "symbol":  symbol,
            "current": current,
            "issue":   issue,
            "advice":  advice,
            "at":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._advice_log.append(entry)
        return advice

    def propose_type_annotation(self, func: Any) -> dict[str, str]:
        """
        Propose type annotations for all unannotated parameters of func.

        Uses the _PARAM_NAME_TYPE_HINTS table and naming heuristics to
        generate proposals.  The 'return' key maps to a proposed return type.

        Returns a dict mapping parameter name → proposed type string.
        """
        proposals: dict[str, str] = {}

        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            return proposals

        existing_hints: dict[str, Any] = {}
        try:
            existing_hints = typing.get_type_hints(func)
        except Exception:
            existing_hints = getattr(func, "__annotations__", {})

        for param_name, param in sig.parameters.items():
            if param_name in existing_hints:
                continue  # copilot: skip already-annotated params
            if param_name in ("self", "cls"):
                continue  # copilot: skip implicit instance/class params

            # copilot: try exact name match first
            proposed = _PARAM_NAME_TYPE_HINTS.get(param_name)

            if proposed is None:
                # copilot: try suffix-based heuristics
                lower = param_name.lower()
                if lower.endswith("_count") or lower.endswith("_num") or lower.endswith("_id"):
                    proposed = "int"
                elif lower.endswith("_name") or lower.endswith("_label") or lower.endswith("_path"):
                    proposed = "str"
                elif lower.endswith("_list") or lower.endswith("_items") or lower.endswith("_set"):
                    proposed = "list"
                elif lower.endswith("_dict") or lower.endswith("_map") or lower.endswith("_config"):
                    proposed = "dict"
                elif lower.endswith("_flag") or lower.endswith("_enabled") or lower.startswith("is_") or lower.startswith("has_"):
                    proposed = "bool"
                else:
                    # copilot: check if default value provides a type hint
                    if param.default is not inspect.Parameter.empty:
                        default_type = type(param.default).__name__
                        if default_type in {"int", "str", "float", "bool", "bytes"}:
                            proposed = default_type
                        else:
                            proposed = "Any"
                    else:
                        proposed = "Any"

            proposals[param_name] = proposed

        # copilot: propose a return type if not annotated
        if "return" not in existing_hints:
            # copilot: simple heuristic: functions named is_*/has_*/check_* return bool
            fname = getattr(func, "__name__", "")
            if fname.startswith(("is_", "has_", "check_", "validate_", "can_")):
                proposals["return"] = "bool"
            elif fname.startswith(("get_", "fetch_", "load_", "read_", "find_")):
                proposals["return"] = "Any"
            elif fname.startswith(("build_", "create_", "make_", "construct_")):
                proposals["return"] = "Any"
            else:
                proposals["return"] = "None"

        entry = {
            "kind":      "propose_annotations",
            "func":      getattr(func, "__qualname__", repr(func)),
            "proposals": proposals,
            "at":        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._advice_log.append(entry)
        return proposals

    def generate_full_advisory(self, result: Any) -> str:
        """
        Generate a multi-paragraph advisory from any analysis result object.

        Inspects the result for judgment counts, burden_score, annotations_found,
        and errors to produce a comprehensive advisory message.
        """
        judgments_count = len(getattr(result, "judgments", []))
        obligations     = getattr(result, "obligations", [])
        errors          = getattr(result, "errors", [])
        burden_score    = getattr(result, "burden_score", 0.0)
        ann_count       = getattr(result, "annotations_found", 0)

        discharged      = sum(1 for ob in obligations if getattr(ob, "is_discharged", False))
        pending         = len(obligations) - discharged

        paragraphs: list[str] = []

        # copilot: paragraph 1 — overall health assessment
        if burden_score >= 0.8:
            health = "excellent"
        elif burden_score >= 0.5:
            health = "moderate"
        elif burden_score >= 0.2:
            health = "poor"
        else:
            health = "critical"

        paragraphs.append(
            f"Overall annotation health: {health} "
            f"(burden_score={burden_score:.2f}). "
            f"{judgments_count} judgments emitted across {ann_count} annotations."
        )

        # copilot: paragraph 2 — obligation status
        if pending == 0:
            paragraphs.append(
                "All annotation obligations are discharged. "
                "The codebase satisfies all theorem burdens at the current trust level."
            )
        else:
            paragraphs.append(
                f"{pending} obligation(s) remain undischarged. "
                f"Consider running a type checker (mypy/pyright) to discharge these "
                f"to SOLVER_DISCHARGED level (trust=4). "
                f"Current discharged: {discharged}/{len(obligations)}."
            )

        # copilot: paragraph 3 — errors
        if errors:
            error_summary = "; ".join(errors[:3])
            paragraphs.append(
                f"Analysis encountered {len(errors)} error(s). "
                f"First few: {error_summary}. "
                f"These may indicate missing imports or unannotated third-party code."
            )

        # copilot: paragraph 4 — recommendations
        if burden_score < 0.8:
            paragraphs.append(
                "Recommendations:\n"
                "  1. Add type annotations to all public API symbols.\n"
                "  2. Run mypy/pyright to discharge EXISTENCE obligations.\n"
                "  3. Use @dataclass(frozen=True) for immutable data structures.\n"
                "  4. Consult theory2.tex Ch21 §21.5 for burden discharge guidelines."
            )

        advisory = "\n\n".join(paragraphs)
        self._advice_log.append({
            "kind": "full_advisory",
            "burden_score": burden_score,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return advisory

    def advice_count(self) -> int:
        """Return the total number of advisory calls made."""
        return len(self._advice_log)


# ──────────────────────────────────────────────────────────────────────────────
# AnnotationsDecoratorsRegistriesGeneratedBridge — top-level integration facade
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationsDecoratorsRegistriesGeneratedBridge:
    """
    Thread-safe integration bridge connecting generated_contracts analysis to
    the wider JuGeo framework.

    theory2.tex Ch21 §21.7 describes the bridge as the terminal morphism:
    it takes any Python object, runs the full analysis pipeline, and
    produces an ExportBundle suitable for downstream consumption by the
    judgment registry, solver pipeline, or Copilot advisory system.

    Usage::

        bridge = AnnotationsDecoratorsRegistriesGeneratedBridge()
        bundle = bridge.analyze_and_emit(MyClass)
        print(bridge.export_to_json(bundle))
    """

    def __init__(self) -> None:
        # copilot: one instance of each sub-component per bridge
        self._emitter   = JudgmentEmitter()
        self._mapper    = CoordinateMapper()
        self._solver    = SolverInterface()
        self._advisor   = CopilotAdvisor()
        self._bundles:  list[ExportBundle] = []
        self._lock      = threading.Lock()

    def analyze_and_emit(self, target: Any) -> ExportBundle:
        """
        Run the full integration pipeline on the given target object.

        Steps:
          1. Map target to CoordinateObject via CoordinateMapper.
          2. Extract all annotations via _extract_annotations().
          3. Emit Judgments for each annotation.
          4. Attempt solver discharge on each obligation.
          5. Generate CopilotAdvisor advisory.
          6. Build ExportBundle.
          7. Cache bundle and return.

        Thread-safe via self._lock.
        """
        with self._lock:
            qualname = getattr(
                target,
                "__qualname__",
                getattr(target, "__name__", repr(target))
            )
            logger.info("Bridge: analyzing %s", qualname)

            coord = self._mapper.map_any(target)
            annotations = self._extract_annotations(target)

            # copilot: emit judgments for each annotation found
            judgments: list[Judgment] = []
            obligations: list[ResidualObligation] = []

            for ann_info in annotations:
                symbol     = ann_info.get("symbol", "unknown")
                ann_text   = ann_info.get("annotation", "Any")
                is_trivial = ann_text in _PRIMITIVE_ANNOTATIONS

                trust = (
                    TrustLevel.RUNTIME_WITNESSED if is_trivial
                    else TrustLevel.UNVERIFIED
                )

                j = self._emitter.emit(
                    symbol     = symbol,
                    annotation = ann_text,
                    trust      = trust,
                    evidence   = [],
                    source     = f"bridge.{qualname}",
                )
                judgments.append(j)

                # copilot: create a ResidualObligation for each non-trivial annotation
                if not is_trivial:
                    ob = ResidualObligation(
                        description = f"{symbol}: {ann_text}",
                        obligation_id = str(uuid.uuid4()),
                        priority    = 1,
                        is_discharged = False,
                    )
                    # copilot: attempt solver discharge
                    discharged, explanation = self._solver.try_discharge(ob)
                    if discharged:
                        ob = ob.discharge(explanation)
                    obligations.append(ob)
                else:
                    # copilot: trivial annotations get pre-discharged obligations
                    ob = ResidualObligation(
                        description   = f"{symbol}: {ann_text}",
                        obligation_id = str(uuid.uuid4()),
                        priority      = 1,
                        is_discharged = True,
                    )
                    obligations.append(ob)

            # copilot: generate advisory (side-effect: logged to advisor)
            class _ResultProxy:
                pass

            proxy = _ResultProxy()
            proxy.judgments         = judgments
            proxy.obligations       = obligations
            proxy.errors            = []
            proxy.burden_score      = sum(1 for ob in obligations if ob.is_discharged) / max(1, len(obligations))
            proxy.annotations_found = len(annotations)

            _advisory = self._advisor.generate_full_advisory(proxy)

            # copilot: build trust_summary from emitted judgments
            trust_summary = self._build_trust_summary(judgments)

            # copilot: build coordinate_map for all mapped symbols
            coord_map: dict[str, str] = {}
            for ann_info in annotations:
                sym = ann_info.get("symbol", "?")
                coord_map[sym] = repr(coord.components)

            bundle = ExportBundle(
                bundle_id      = str(uuid.uuid4()),
                source_package = _SOURCE_PACKAGE,
                judgments      = tuple(judgments),
                obligations    = tuple(obligations),
                obstructions   = (),
                coordinate_map = coord_map,
                trust_summary  = trust_summary,
                schema_version = _SCHEMA_VERSION,
            )

            self._bundles.append(bundle)
            logger.info(
                "Bridge: emitted bundle %s with %d judgments, %d obligations",
                bundle.bundle_id[:8], len(judgments), len(obligations)
            )
            return bundle

    def _extract_annotations(self, target: Any) -> list[dict]:
        """
        Extract all annotations from target as a list of dicts.

        Each dict has keys: 'symbol', 'annotation', 'source'.
        Uses typing.get_type_hints() with __annotations__ fallback.
        Also extracts method annotations for classes.
        """
        annotations: list[dict] = []

        try:
            hints = typing.get_type_hints(target)
        except Exception:
            hints = getattr(target, "__annotations__", {})

        for symbol, ann in hints.items():
            ann_text = (
                ann if isinstance(ann, str)
                else getattr(ann, "__name__", None)
                   or getattr(ann, "_name", None)
                   or repr(ann)
            )
            annotations.append({
                "symbol":     symbol,
                "annotation": ann_text,
                "source":     "class_annotations" if inspect.isclass(target) else "function_annotations",
            })

        # copilot: for classes, also extract method annotations
        if inspect.isclass(target):
            for method_name, method in inspect.getmembers(target, predicate=inspect.isfunction):
                try:
                    method_hints = typing.get_type_hints(method)
                except Exception:
                    method_hints = getattr(method, "__annotations__", {})
                for param_name, ann in method_hints.items():
                    ann_text = (
                        ann if isinstance(ann, str)
                        else getattr(ann, "__name__", None)
                           or getattr(ann, "_name", None)
                           or repr(ann)
                    )
                    annotations.append({
                        "symbol":     f"{method_name}.{param_name}",
                        "annotation": ann_text,
                        "source":     f"method.{method_name}",
                    })

        # copilot: use inspect.getmembers to pick up module-level hints
        if inspect.ismodule(target):
            for name, val in inspect.getmembers(target):
                if inspect.isclass(val) or inspect.isfunction(val):
                    try:
                        sub_hints = typing.get_type_hints(val)
                        for sym, ann in sub_hints.items():
                            ann_text = (
                                ann if isinstance(ann, str)
                                else getattr(ann, "__name__", None)
                                   or repr(ann)
                            )
                            annotations.append({
                                "symbol":     f"{name}.{sym}",
                                "annotation": ann_text,
                                "source":     f"module.{name}",
                            })
                    except Exception:
                        pass

        return annotations

    def _build_trust_summary(self, judgments: list) -> dict:
        """
        Build a trust_summary dict from a list of Judgment records.

        Returns {trust_level_str: count} sorted by trust level.
        """
        summary: dict[str, int] = {}
        for j in judgments:
            level = getattr(getattr(j, "trust", None), "level", None)
            key   = str(level) if level is not None else "unknown"
            summary[key] = summary.get(key, 0) + 1
        return summary

    def export_to_json(self, bundle: ExportBundle) -> str:
        """
        Serialize an ExportBundle to a JSON string.

        Uses json.dumps with indent=2.  Non-JSON-serializable objects are
        converted to their repr() string via a custom default handler.
        """
        def _default(obj: Any) -> Any:
            if hasattr(obj, "to_dict"):
                try:
                    return obj.to_dict()
                except Exception:
                    pass
            if hasattr(obj, "value"):  # copilot: handles enum values
                return obj.value
            if isinstance(obj, (set, frozenset)):
                return list(obj)
            if isinstance(obj, type):
                return obj.__name__
            return repr(obj)

        try:
            return json.dumps(bundle.to_dict(), indent=2, default=_default)
        except Exception as exc:
            logger.warning("Bridge.export_to_json: serialization error: %s", exc)
            return json.dumps({"error": str(exc), "bundle_id": bundle.bundle_id})

    def import_from_json(self, data: str) -> ExportBundle:
        """
        Parse a JSON string and reconstruct an ExportBundle.

        Reconstructs the bundle with string representations of judgments and
        obligations (full reconstruction requires the judgment term types).
        """
        try:
            d = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Bridge.import_from_json: invalid JSON: %s", exc)
            return ExportBundle()

        # copilot: reconstruct bundle from dict fields; keep judgments as raw dicts
        return ExportBundle(
            bundle_id      = d.get("bundle_id", str(uuid.uuid4())),
            source_package = d.get("source_package", _SOURCE_PACKAGE),
            judgments      = tuple(d.get("judgments", [])),
            obligations    = tuple(d.get("obligations", [])),
            obstructions   = tuple(d.get("obstructions", [])),
            coordinate_map = d.get("coordinate_map", {}),
            trust_summary  = d.get("trust_summary", {}),
            generated_at   = d.get("generated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            schema_version = d.get("schema_version", _SCHEMA_VERSION),
        )

    def merge_bundles(self, *bundles: ExportBundle) -> ExportBundle:
        """
        Merge multiple ExportBundles into a single canonical bundle.

        Deduplicates judgments by proposition formula and obligations by
        description.  Uses the earliest generated_at timestamp.

        theory2.tex Ch21 §21.7.4 defines bundle merging as a pushout in the
        judgment category: the merged bundle is the colimit of all inputs.
        """
        if not bundles:
            return ExportBundle()
        if len(bundles) == 1:
            return bundles[0]

        # copilot: collect all items from all bundles
        all_judgments:   list = []
        all_obligations: list = []
        all_obstructions: list = []
        coord_map: dict  = {}
        trust_summary: dict = {}
        earliest_at: str = bundles[0].generated_at

        for bundle in bundles:
            all_judgments.extend(bundle.judgments)
            all_obligations.extend(bundle.obligations)
            all_obstructions.extend(bundle.obstructions)
            coord_map.update(bundle.coordinate_map)

            for k, v in bundle.trust_summary.items():
                trust_summary[k] = trust_summary.get(k, 0) + v

            # copilot: keep earliest generated_at
            if bundle.generated_at < earliest_at:
                earliest_at = bundle.generated_at

        # copilot: deduplicate judgments by proposition formula
        seen_formulas: set[str] = set()
        deduped_judgments: list = []
        for j in all_judgments:
            formula = ""
            try:
                formula = j.proposition.formula if j.proposition else repr(j)
            except AttributeError:
                formula = repr(j)
            if formula not in seen_formulas:
                seen_formulas.add(formula)
                deduped_judgments.append(j)

        # copilot: deduplicate obligations by description
        seen_descs: set[str] = set()
        deduped_obligations: list = []
        for ob in all_obligations:
            desc = getattr(ob, "description", repr(ob))
            if desc not in seen_descs:
                seen_descs.add(desc)
                deduped_obligations.append(ob)

        return ExportBundle(
            bundle_id      = str(uuid.uuid4()),
            source_package = _SOURCE_PACKAGE,
            judgments      = tuple(deduped_judgments),
            obligations    = tuple(deduped_obligations),
            obstructions   = tuple(all_obstructions),
            coordinate_map = coord_map,
            trust_summary  = trust_summary,
            generated_at   = earliest_at,
            schema_version = _SCHEMA_VERSION,
        )

    def list_bundles(self) -> list[ExportBundle]:
        """Return all bundles produced by this bridge, oldest first."""
        with self._lock:
            return list(self._bundles)

    def report(self) -> str:
        """Return a summary report of all bundles produced by this bridge."""
        with self._lock:
            bundles = list(self._bundles)

        lines = [
            f"AnnotationsDecoratorsRegistriesGeneratedBridge Report",
            f"  Source package:  {_SOURCE_PACKAGE}",
            f"  Schema version:  {_SCHEMA_VERSION}",
            f"  Solver:          {self._solver.summary()}",
            f"  Advisor calls:   {self._advisor.advice_count()}",
            f"  Bundles:         {len(bundles)}",
        ]
        for i, bundle in enumerate(bundles):
            lines.append(f"  Bundle[{i}]: {bundle.bundle_id[:8]}… — {bundle.judgment_count()} judgments")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        # copilot: test target — a simple annotated function
        def my_function(x: int, name: str, items: list) -> bool:
            """A simple annotated function for smoke testing."""
            return True

        bridge = AnnotationsDecoratorsRegistriesGeneratedBridge()

        # copilot: test analyze_and_emit
        bundle1 = bridge.analyze_and_emit(my_function)
        assert isinstance(bundle1, ExportBundle), f"Expected ExportBundle, got {type(bundle1)}"
        assert bundle1.judgment_count() >= 0

        # copilot: test export_to_json / import_from_json round-trip
        json_str = bridge.export_to_json(bundle1)
        assert isinstance(json_str, str) and len(json_str) > 0

        bundle_reimported = bridge.import_from_json(json_str)
        assert bundle_reimported.bundle_id == bundle1.bundle_id

        # copilot: test with a class target
        @dataclass(frozen=True, slots=True)
        class SampleDataclass:
            x: int = 0
            name: str = ""
            items: list = field(default_factory=list)

        bundle2 = bridge.analyze_and_emit(SampleDataclass)
        assert isinstance(bundle2, ExportBundle)

        # copilot: test merge_bundles
        merged = bridge.merge_bundles(bundle1, bundle2)
        assert isinstance(merged, ExportBundle)
        assert merged.judgment_count() <= bundle1.judgment_count() + bundle2.judgment_count()

        # copilot: test CoordinateMapper
        mapper = CoordinateMapper()
        coord = mapper.map_function(my_function)
        assert len(coord.components) > 0

        # copilot: test CopilotAdvisor proposals
        advisor = CopilotAdvisor()
        proposals = advisor.propose_type_annotation(my_function)
        assert isinstance(proposals, dict)

        # copilot: test SolverInterface heuristic discharge
        solver = SolverInterface()
        ob = ResidualObligation(description="x: int", obligation_id=str(uuid.uuid4()))
        discharged, explanation = solver.try_discharge(ob)
        assert discharged, f"Expected int to be heuristically discharged: {explanation}"

        report = bridge.report()
        assert "Bridge Report" in report

        print(f"[smoke] judgments={bundle1.judgment_count()}, merged={merged.judgment_count()}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
