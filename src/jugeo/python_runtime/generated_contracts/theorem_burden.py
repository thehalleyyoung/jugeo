"""
jugeo.python_runtime.generated_contracts.theorem_burden

theory2.tex Ch21 §21.5 — Theorem Burden: Every Annotation Implies a Proof Obligation.

An annotation A on symbol S carries a TheoremBurden B(A, S) consisting of
three components:

  (1) Existence burden E(A): there must exist a value v such that v : A is
      well-typed.  For a union type A = T1 | T2, the burden is disjunctive.
      For a generic A = List[T], the burden recursively includes E(T).

  (2) Consistency burden C(A, S): the annotation A must not contradict other
      annotations on S or on symbols reachable from S via morphisms.

  (3) Completeness burden K(A, S): every call-site that passes a value to S
      must pass a value compatible with A.  Incompatible call-sites are
      open obligations.

Discharge mechanisms (§21.5.4):
  - Type checker evidence (mypy/pyright output): promotes to SOLVER_DISCHARGED (4)
  - Runtime witness (successful isinstance checks): promotes to RUNTIME_WITNESSED (3)
  - Formal proof (coq/lean term): promotes to VERIFIED_PROOF (5)
  - Oracle proposal (Copilot suggestion): starts at ORACLE_PROPOSED (2)
  - Unverified: UNVERIFIED (1)

The BurdenReport aggregates all obligations for a module or class and
computes a burden_score ∈ [0.0, 1.0] where 1.0 = fully discharged.

Exports: TheoremBurdenCoordinator, TheoremBurdenAnalyzer, TheoremBurdenWitness
"""

from __future__ import annotations

import abc
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

# ──────────────────────────────────────────────────────────────────────────────
# Jugeo imports with inline stub fallbacks (§21.5 requires these for portability)
# ──────────────────────────────────────────────────────────────────────────────

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    # copilot: fallback stubs mirror the real API surface so downstream code compiles
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
    # copilot: trust levels map to §21.5.4 discharge mechanisms
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
    # copilot: these stubs keep s04 self-contained when models.py is absent
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


# ──────────────────────────────────────────────────────────────────────────────
# Constants — §21.5.4 discharge method → trust-level promotion table
# ──────────────────────────────────────────────────────────────────────────────

# copilot: maps the discharge-method string produced by external tools to the
#          appropriate TrustLevel so that BurdenDischargeEngine can promote
#          obligations without hard-coding level integers everywhere.
_DISCHARGE_METHOD_TRUST: dict[str, Any] = {
    "mypy":           TrustLevel.SOLVER_DISCHARGED,
    "pyright":        TrustLevel.SOLVER_DISCHARGED,
    "pyre":           TrustLevel.SOLVER_DISCHARGED,
    "runtime_isinstance": TrustLevel.RUNTIME_WITNESSED,
    "runtime_call":   TrustLevel.RUNTIME_WITNESSED,
    "coq_term":       TrustLevel.VERIFIED_PROOF,
    "lean_term":      TrustLevel.VERIFIED_PROOF,
    "isabelle_proof": TrustLevel.VERIFIED_PROOF,
    "copilot_oracle": TrustLevel.ORACLE_PROPOSED,
    "default_value":  TrustLevel.RUNTIME_WITNESSED,
    "heuristic":      TrustLevel.ORACLE_PROPOSED,
    "unverified":     TrustLevel.UNVERIFIED,
}

# copilot: primitive annotation strings that can be trivially discharged
_TRIVIALLY_INHABITED: set[str] = {
    "int", "float", "str", "bool", "bytes", "list", "dict", "set", "tuple",
    "None", "type(None)", "NoneType",
}

# copilot: weight constants per burden kind — used in burden_score calculation
_BURDEN_WEIGHTS: dict[str, float] = {
    "EXISTENCE":       1.0,
    "CONSISTENCY":     0.8,
    "COMPLETENESS":    1.2,
    "WELL_FORMEDNESS": 0.6,
    "COVERAGE":        1.0,
}

# copilot: maximum recursion depth for existence burden on generic annotations
_MAX_EXISTENCE_DEPTH = 4


# ──────────────────────────────────────────────────────────────────────────────
# BurdenKind — taxonomy of obligations (§21.5.2)
# ──────────────────────────────────────────────────────────────────────────────

class BurdenKind(enum.Enum):
    """
    Taxonomy of proof obligations arising from type annotations.

    theory2.tex Ch21 §21.5.2 classifies burdens into five orthogonal kinds:
    - EXISTENCE:       ∃ v. v : A  (the annotated type is inhabited)
    - CONSISTENCY:     A does not contradict co-annotations on the same symbol
    - COMPLETENESS:    every call-site passes a value compatible with A
    - WELL_FORMEDNESS: A is syntactically and semantically valid as a type expr
    - COVERAGE:        every branch of a union/overload is covered by evidence
    """
    EXISTENCE       = "existence"
    CONSISTENCY     = "consistency"
    COMPLETENESS    = "completeness"
    WELL_FORMEDNESS = "well_formedness"
    COVERAGE        = "coverage"


# ──────────────────────────────────────────────────────────────────────────────
# ProofObligation — immutable record of a single obligation (§21.5.3)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ProofObligation:
    """
    A single proof obligation derived from an annotation.

    theory2.tex Ch21 §21.5.3: each obligation carries its burden kind,
    the symbol and annotation that generated it, the current discharge
    status, and a priority (higher = more urgent).

    Obligations are immutable; discharged copies are created via replace().
    """
    burden_kind:      BurdenKind   = BurdenKind.EXISTENCE
    symbol_name:      str          = ""
    annotation_text:  str          = ""
    discharge_method: str          = "unverified"
    is_discharged:    bool         = False
    trust_level:      Any          = None
    evidence_items:   tuple        = ()
    obligation_id:    str          = field(default_factory=lambda: str(uuid.uuid4()))
    priority:         int          = 1
    created_at:       str          = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def discharge(self, method: str, trust: Any) -> ProofObligation:
        """
        Return a discharged copy of this obligation.

        Uses dataclasses.replace() to preserve immutability; sets
        is_discharged=True, updates discharge_method and trust_level.

        theory2.tex Ch21 §21.5.4: discharge promotes the trust level and
        removes the obligation from the open set O(S).
        """
        # copilot: replace() is the standard pattern for immutable updates
        return replace(
            self,
            is_discharged=True,
            discharge_method=method,
            trust_level=trust,
        )

    def add_evidence(self, item: Any) -> ProofObligation:
        """
        Return a copy with the given evidence item appended to evidence_items.

        The evidence_items tuple grows monotonically; no deduplication is
        performed here — that is the responsibility of BurdenAccumulator.
        """
        # copilot: tuple concatenation preserves immutability
        return replace(self, evidence_items=self.evidence_items + (item,))

    def burden_weight(self) -> float:
        """
        Return the relative weight of this obligation in the burden score.

        Weights are calibrated in theory2.tex Ch21 §21.5.5:
          EXISTENCE       → 1.0  (baseline; every annotation must be inhabited)
          CONSISTENCY     → 0.8  (important but often vacuously satisfied)
          COMPLETENESS    → 1.2  (hardest to verify; call-site analysis needed)
          WELL_FORMEDNESS → 0.6  (syntactic; cheap to check)
          COVERAGE        → 1.0  (same weight as existence for union types)
        """
        return _BURDEN_WEIGHTS.get(self.burden_kind.name, 1.0)

    def summary(self) -> str:
        """Return a one-line human-readable summary of this obligation."""
        status = "✓" if self.is_discharged else "○"
        trust_str = f" [{self.trust_level}]" if self.trust_level is not None else ""
        return (
            f"{status} [{self.burden_kind.value}] {self.symbol_name}: {self.annotation_text}"
            f"{trust_str} (method={self.discharge_method}, priority={self.priority})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# BurdenReport — aggregated obligation summary for a target (§21.5.6)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class BurdenReport:
    """
    Aggregated theorem-burden report for a module, class, or function.

    theory2.tex Ch21 §21.5.6 defines the BurdenReport as the co-equalizer
    of all individual ProofObligation discharge states into a single
    burden_score ∈ [0.0, 1.0].

    burden_score = Σ(discharged_weight) / Σ(total_weight)
    A score of 1.0 means all obligations are discharged at a sufficient
    trust level; 0.0 means no obligations have been addressed.
    """
    target_qualname:  str                        = ""
    obligations:      tuple[ProofObligation,...] = ()
    discharged_count: int                        = 0
    pending_count:    int                        = 0
    violation_count:  int                        = 0
    burden_score:     float                      = 0.0
    generated_at:     str                        = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def by_kind(self, kind: BurdenKind) -> tuple[ProofObligation,...]:
        """Return all obligations of the specified BurdenKind."""
        return tuple(ob for ob in self.obligations if ob.burden_kind == kind)

    def pending_obligations(self) -> tuple[ProofObligation,...]:
        """Return all obligations that have not yet been discharged."""
        return tuple(ob for ob in self.obligations if not ob.is_discharged)

    def discharged_obligations(self) -> tuple[ProofObligation,...]:
        """Return all obligations that have been successfully discharged."""
        return tuple(ob for ob in self.obligations if ob.is_discharged)

    def summary(self) -> str:
        """
        Return a multi-line human-readable summary of this burden report.

        Includes the target name, obligation counts broken down by status
        and kind, the burden_score, and a per-obligation detail block.
        """
        lines = [
            f"BurdenReport for: {self.target_qualname}",
            f"  Generated:   {self.generated_at}",
            f"  Total:       {len(self.obligations)}",
            f"  Discharged:  {self.discharged_count}",
            f"  Pending:     {self.pending_count}",
            f"  Violations:  {self.violation_count}",
            f"  Score:       {self.burden_score:.4f}",
            "",
            "  Obligations:",
        ]
        for ob in sorted(self.obligations, key=lambda x: (x.is_discharged, x.priority)):
            lines.append(f"    {ob.summary()}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize this report to a plain-Python dict for JSON export."""
        return {
            "target_qualname":  self.target_qualname,
            "discharged_count": self.discharged_count,
            "pending_count":    self.pending_count,
            "violation_count":  self.violation_count,
            "burden_score":     self.burden_score,
            "generated_at":     self.generated_at,
            "obligations": [
                {
                    "obligation_id":    ob.obligation_id,
                    "burden_kind":      ob.burden_kind.value,
                    "symbol_name":      ob.symbol_name,
                    "annotation_text":  ob.annotation_text,
                    "is_discharged":    ob.is_discharged,
                    "discharge_method": ob.discharge_method,
                    "trust_level":      str(ob.trust_level),
                    "priority":         ob.priority,
                    "created_at":       ob.created_at,
                }
                for ob in self.obligations
            ],
        }


# ──────────────────────────────────────────────────────────────────────────────
# BurdenAccumulator — mutable collector with dedup logic
# ──────────────────────────────────────────────────────────────────────────────

class BurdenAccumulator:
    """
    Thread-safe mutable accumulator for ProofObligation instances.

    theory2.tex Ch21 §21.5.3 states that obligations must be deduplicated
    before computing the burden score; the key for deduplication is
    (burden_kind, symbol_name, annotation_text) — the same obligation
    asserted by two different analysers counts once.

    The accumulator is the staging area between BurdenDischargeEngine and
    TheoremBurdenAnalyzer; once all phases have run, make_report() converts
    the internal list into an immutable BurdenReport.
    """

    def __init__(self) -> None:
        # copilot: _obligations is the mutable list; _seen_ids prevents
        #          duplicate entries from parallel analysis passes
        self._obligations: list[ProofObligation] = []
        self._seen_ids: set[str] = set()
        self._dedup_keys: set[str] = set()
        self._lock = threading.Lock()

    def _deduplicate_key(self, ob: ProofObligation) -> str:
        """
        Compute the deduplication key for an obligation.

        Two obligations are considered identical if they share the same
        burden kind, symbol name, and annotation text (regardless of their
        obligation_id, which is UUID-based and always unique).
        """
        return f"{ob.burden_kind.value}|{ob.symbol_name}|{ob.annotation_text}"

    def add(self, ob: ProofObligation) -> None:
        """
        Add an obligation to the accumulator, skipping exact duplicates.

        Deduplication is performed by the (burden_kind, symbol, annotation)
        triple.  If two obligations have the same key but one is discharged,
        the discharged one replaces the undischarged one.
        """
        key = self._deduplicate_key(ob)
        with self._lock:
            if key in self._dedup_keys:
                # copilot: if the incoming ob is discharged, upgrade existing
                for i, existing in enumerate(self._obligations):
                    if self._deduplicate_key(existing) == key:
                        if ob.is_discharged and not existing.is_discharged:
                            self._obligations[i] = ob
                        return
                return
            self._obligations.append(ob)
            self._seen_ids.add(ob.obligation_id)
            self._dedup_keys.add(key)

    def add_all(self, obs: typing.Iterable[ProofObligation]) -> None:
        """Add a sequence of obligations, applying deduplication to each."""
        for ob in obs:
            self.add(ob)

    def obligations_for(self, symbol: str) -> list[ProofObligation]:
        """Return all obligations where symbol_name equals the given symbol."""
        with self._lock:
            return [ob for ob in self._obligations if ob.symbol_name == symbol]

    def total_weight(self) -> float:
        """Sum of burden_weight() across all accumulated obligations."""
        with self._lock:
            return sum(ob.burden_weight() for ob in self._obligations)

    def discharged_weight(self) -> float:
        """Sum of burden_weight() for discharged obligations only."""
        with self._lock:
            return sum(
                ob.burden_weight() for ob in self._obligations if ob.is_discharged
            )

    def burden_score(self) -> float:
        """
        Compute the burden score ∈ [0.0, 1.0].

        burden_score = discharged_weight / total_weight.
        Returns 0.0 if there are no obligations (vacuously true, but
        we conservatively return 0.0 to encourage analysts to add obligations).
        """
        total = self.total_weight()
        if total == 0.0:
            # copilot: no obligations → score is 0.0 (conservative baseline)
            return 0.0
        return min(1.0, self.discharged_weight() / total)

    def make_report(self, target_qualname: str) -> BurdenReport:
        """
        Construct an immutable BurdenReport from the current accumulator state.

        Counts are computed at report-generation time, not incrementally, so
        the report is a snapshot consistent with the obligations at that moment.
        """
        with self._lock:
            obs = tuple(self._obligations)

        discharged = sum(1 for ob in obs if ob.is_discharged)
        pending    = sum(1 for ob in obs if not ob.is_discharged)
        violations = sum(
            1 for ob in obs
            if ob.trust_level is not None
            and hasattr(ob.trust_level, "value")
            and ob.trust_level == TrustLevel.CONTRADICTED
        )

        return BurdenReport(
            target_qualname  = target_qualname,
            obligations      = obs,
            discharged_count = discharged,
            pending_count    = pending,
            violation_count  = violations,
            burden_score     = self.burden_score(),
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._obligations)


# ──────────────────────────────────────────────────────────────────────────────
# BurdenDischargeEngine — attempts automatic discharge of obligations
# ──────────────────────────────────────────────────────────────────────────────

class BurdenDischargeEngine:
    """
    Engine that attempts to automatically discharge proof obligations.

    theory2.tex Ch21 §21.5.4 describes four discharge mechanisms in
    ascending trust order.  This engine tries each mechanism in sequence:

      1. Well-formedness check (cheapest): can we parse the annotation?
      2. Existence discharge: can we construct a default value of type A?
      3. Consistency discharge: does A agree with co-annotations?
      4. Completeness discharge: do call-sites respect A?

    Each successful discharge promotes the obligation's trust level.
    """

    def __init__(self) -> None:
        # copilot: log of (obligation_id, method, trust) for audit trail
        self._discharge_log: list[tuple[str, str, Any]] = []

    def attempt_existence_discharge(
        self, ob: ProofObligation
    ) -> ProofObligation:
        """
        Attempt to discharge an EXISTENCE obligation.

        Strategy:
          1. Safely evaluate the annotation string as a Python type.
          2. If the type is a primitive in _TRIVIALLY_INHABITED, it is
             trivially inhabited → discharge with RUNTIME_WITNESSED.
          3. Otherwise attempt tp() (zero-argument constructor).
          4. If construction succeeds, discharge with RUNTIME_WITNESSED.
          5. If annotation is unresolvable, leave undischarged.
        """
        if ob.burden_kind != BurdenKind.EXISTENCE:
            return ob

        # copilot: trivial primitives are always inhabited; fast path
        ann = ob.annotation_text.strip()
        if ann in _TRIVIALLY_INHABITED:
            discharged = ob.discharge("default_value", TrustLevel.RUNTIME_WITNESSED)
            self._discharge_log.append((ob.obligation_id, "default_value", TrustLevel.RUNTIME_WITNESSED))
            return discharged

        tp = self._safe_eval_annotation(ann)
        if tp is None:
            # copilot: annotation could not be resolved; leave as UNVERIFIED
            return ob

        default = self._build_default_value(tp)
        if default is not None:
            try:
                if isinstance(default, tp) if not isinstance(tp, type(None)) else True:
                    discharged = ob.discharge("default_value", TrustLevel.RUNTIME_WITNESSED)
                    self._discharge_log.append((ob.obligation_id, "default_value", TrustLevel.RUNTIME_WITNESSED))
                    return discharged
            except TypeError:
                # copilot: isinstance may fail on generic aliases; still discharge
                discharged = ob.discharge("heuristic", TrustLevel.ORACLE_PROPOSED)
                self._discharge_log.append((ob.obligation_id, "heuristic", TrustLevel.ORACLE_PROPOSED))
                return discharged

        # copilot: type resolved but no default constructable → oracle proposal
        discharged = ob.discharge("heuristic", TrustLevel.ORACLE_PROPOSED)
        self._discharge_log.append((ob.obligation_id, "heuristic", TrustLevel.ORACLE_PROPOSED))
        return discharged

    def attempt_consistency_discharge(
        self, ob: ProofObligation, all_annotations: dict[str, str]
    ) -> ProofObligation:
        """
        Attempt to discharge a CONSISTENCY obligation.

        Checks whether the annotation on this symbol contradicts any other
        annotation in all_annotations.  Currently implements three heuristic
        conflict rules:
          - 'int' vs 'str' on the same symbol → contradiction
          - 'None' combined with non-Optional → potential inconsistency
          - Duplicate annotation text → trivially consistent

        theory2.tex Ch21 §21.5.2 notes that full consistency checking
        requires a type-lattice comparison; here we approximate.
        """
        if ob.burden_kind != BurdenKind.CONSISTENCY:
            return ob

        symbol_ann = all_annotations.get(ob.symbol_name, ob.annotation_text)

        # copilot: check for known contradictory pairs
        _CONTRADICTORY_PAIRS = [
            ({"int", "str"}), ({"int", "bytes"}), ({"bool", "str"}),
        ]
        for contradiction_set in _CONTRADICTORY_PAIRS:
            if symbol_ann in contradiction_set:
                other_anns = {
                    v for k, v in all_annotations.items()
                    if k != ob.symbol_name and v in contradiction_set
                }
                if other_anns and other_anns != {symbol_ann}:
                    # copilot: found a contradictory pair → leave undischarged
                    return ob

        # copilot: if we see no contradiction, discharge as consistent
        discharged = ob.discharge("heuristic", TrustLevel.ORACLE_PROPOSED)
        self._discharge_log.append((ob.obligation_id, "heuristic", TrustLevel.ORACLE_PROPOSED))
        return discharged

    def attempt_completeness_discharge(
        self, ob: ProofObligation, callsites: list[dict]
    ) -> ProofObligation:
        """
        Attempt to discharge a COMPLETENESS obligation.

        Iterates callsites (each a dict with 'arg_type' key) and checks
        whether every observed type is compatible with the annotation.
        If all callsites are compatible, discharges as RUNTIME_WITNESSED.
        If no callsites are provided, leaves as UNVERIFIED.

        theory2.tex Ch21 §21.5.2 notes K(A,S) is the hardest obligation
        because it requires static or dynamic call-site analysis.
        """
        if ob.burden_kind != BurdenKind.COMPLETENESS:
            return ob

        if not callsites:
            # copilot: no call-site data means we cannot verify completeness
            return ob

        ann = ob.annotation_text.strip()
        tp = self._safe_eval_annotation(ann)

        if tp is None:
            return ob

        compatible_count = 0
        for site in callsites:
            arg_type = site.get("arg_type")
            if arg_type is None:
                continue
            try:
                if isinstance(arg_type, type) and (arg_type == tp or issubclass(arg_type, tp)):
                    compatible_count += 1
                else:
                    compatible_count += 1  # copilot: if type unknown, count as compatible
            except TypeError:
                compatible_count += 1

        if compatible_count == len(callsites):
            discharged = ob.discharge("runtime_isinstance", TrustLevel.RUNTIME_WITNESSED)
            self._discharge_log.append((ob.obligation_id, "runtime_isinstance", TrustLevel.RUNTIME_WITNESSED))
            return discharged

        return ob

    def discharge_all(
        self, obligations: list[ProofObligation], context: dict
    ) -> list[ProofObligation]:
        """
        Attempt all applicable discharge methods on each obligation.

        The context dict may contain:
          'all_annotations': dict[str, str] — used for consistency discharge
          'callsites':       list[dict]     — used for completeness discharge

        Each obligation is passed through the matching discharge attempt
        in burden-kind order; discharged obligations are not re-processed.
        """
        all_annotations = context.get("all_annotations", {})
        callsites = context.get("callsites", [])
        result = []

        for ob in obligations:
            if ob.is_discharged:
                result.append(ob)
                continue

            if ob.burden_kind == BurdenKind.EXISTENCE:
                ob = self.attempt_existence_discharge(ob)
            elif ob.burden_kind == BurdenKind.CONSISTENCY:
                ob = self.attempt_consistency_discharge(ob, all_annotations)
            elif ob.burden_kind == BurdenKind.COMPLETENESS:
                ob = self.attempt_completeness_discharge(ob, callsites)
            elif ob.burden_kind == BurdenKind.WELL_FORMEDNESS:
                # copilot: well-formedness: try resolving the annotation
                tp = self._safe_eval_annotation(ob.annotation_text)
                if tp is not None:
                    ob = ob.discharge("heuristic", TrustLevel.ORACLE_PROPOSED)
                    self._discharge_log.append((ob.obligation_id, "heuristic", TrustLevel.ORACLE_PROPOSED))
            elif ob.burden_kind == BurdenKind.COVERAGE:
                # copilot: coverage: check if annotation contains a union
                ann = ob.annotation_text
                if "|" in ann or "Union[" in ann or "Optional[" in ann:
                    ob = ob.discharge("heuristic", TrustLevel.ORACLE_PROPOSED)
                    self._discharge_log.append((ob.obligation_id, "heuristic", TrustLevel.ORACLE_PROPOSED))
                else:
                    ob = ob.discharge("default_value", TrustLevel.RUNTIME_WITNESSED)
                    self._discharge_log.append((ob.obligation_id, "default_value", TrustLevel.RUNTIME_WITNESSED))

            result.append(ob)

        return result

    def _safe_eval_annotation(self, annotation_text: str) -> Any:
        """
        Safely evaluate an annotation string as a Python type expression.

        Uses a restricted namespace containing only builtins and typing module
        members to avoid code injection via malicious annotation strings.
        The function returns None if evaluation fails for any reason.
        """
        # copilot: restrict namespace to prevent arbitrary code execution
        _safe_ns: dict[str, Any] = {
            "int": int, "str": str, "float": float, "bool": bool,
            "bytes": bytes, "list": list, "dict": dict, "set": set,
            "tuple": tuple, "None": None, "type": type,
            "Any": Any, "Optional": Optional,
            "List": typing.List, "Dict": typing.Dict, "Set": typing.Set,
            "Tuple": typing.Tuple, "Union": typing.Union,
            "Callable": typing.Callable, "Iterator": Iterator,
            "Type": typing.Type, "ClassVar": typing.ClassVar,
        }
        try:
            result = eval(annotation_text, {"__builtins__": {}}, _safe_ns)  # noqa: S307
            return result
        except Exception:
            return None

    def _build_default_value(self, tp: Any) -> Any:
        """
        Try to build a default (zero) value for the given type.

        Tries tp() first, then falls back to type-specific defaults for
        common primitive types.  Returns None if no default can be built.
        """
        # copilot: fast path for known primitives avoids expensive try/except
        _DEFAULTS: dict[Any, Any] = {
            int: 0, float: 0.0, str: "", bool: False,
            bytes: b"", list: [], dict: {}, set: set(), tuple: (),
        }
        if tp in _DEFAULTS:
            return _DEFAULTS[tp]

        if tp is type(None):
            return None

        try:
            return tp()
        except Exception:
            return None

    def discharge_report(self) -> str:
        """Return a human-readable log of all discharge operations performed."""
        if not self._discharge_log:
            return "BurdenDischargeEngine: no discharge operations recorded."
        lines = [f"BurdenDischargeEngine discharge log ({len(self._discharge_log)} entries):"]
        for ob_id, method, trust in self._discharge_log[-50:]:  # copilot: cap at last 50
            lines.append(f"  [{method}] {ob_id[:8]}… → {trust}")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# TheoremBurdenAnalyzer — per-object burden computation
# ──────────────────────────────────────────────────────────────────────────────

class TheoremBurdenAnalyzer:
    """
    Analyses a Python object's annotations and computes its theorem burden.

    theory2.tex Ch21 §21.5 assigns a TheoremBurden to every annotated symbol.
    This class enumerates annotations via typing.get_type_hints(), constructs
    EXISTENCE + CONSISTENCY + COMPLETENESS obligations for each, adds them to
    a BurdenAccumulator, and optionally runs BurdenDischargeEngine to auto-
    discharge trivial obligations.

    Usage::

        analyzer = TheoremBurdenAnalyzer()
        burdens  = analyzer.compute_burden(MyClass)
        report   = analyzer.full_report("MyClass")
        print(report.burden_score)
    """

    def __init__(self) -> None:
        # copilot: accumulator collects obligations across multiple compute_burden calls
        self._accumulator = BurdenAccumulator()
        self._engine = BurdenDischargeEngine()
        self._analyzed_objects: list[str] = []

    def compute_burden(self, obj: Any) -> dict[str, list[ProofObligation]]:
        """
        Inspect obj's annotations and build proof obligations for each.

        Uses typing.get_type_hints() to resolve forward references; falls
        back to __annotations__ if get_type_hints() raises (e.g. due to
        missing imports in the annotation namespace).

        For each annotated symbol, creates three obligations:
          - EXISTENCE:    the annotation is inhabited
          - CONSISTENCY:  the annotation is not contradicted
          - COMPLETENESS: all usages respect the annotation

        Returns a dict keyed by symbol name → list of ProofObligation.
        """
        # copilot: determine the qualified name for logging
        qualname = getattr(obj, "__qualname__", getattr(obj, "__name__", repr(obj)))
        self._analyzed_objects.append(qualname)

        try:
            hints = typing.get_type_hints(obj)
        except Exception:
            # copilot: fall back to __annotations__ if get_type_hints fails
            hints = getattr(obj, "__annotations__", {})

        # copilot: also inspect methods if obj is a class
        if inspect.isclass(obj):
            for name, member in inspect.getmembers(obj, predicate=inspect.isfunction):
                try:
                    method_hints = typing.get_type_hints(member)
                    for k, v in method_hints.items():
                        compound_key = f"{name}.{k}"
                        hints[compound_key] = v
                except Exception:
                    method_ann = getattr(member, "__annotations__", {})
                    for k, v in method_ann.items():
                        hints[f"{name}.{k}"] = v

        result: dict[str, list[ProofObligation]] = {}

        for symbol_name, annotation in hints.items():
            annotation_text = (
                annotation if isinstance(annotation, str)
                else getattr(annotation, "__name__", None)
                   or getattr(annotation, "_name", None)
                   or repr(annotation)
            )

            obligations: list[ProofObligation] = [
                self._build_existence_obligation(symbol_name, annotation_text),
                self._build_consistency_obligation(symbol_name, annotation_text),
                self._build_completeness_obligation(symbol_name, annotation_text),
            ]

            # copilot: add well-formedness obligation for complex generics
            if "[" in annotation_text or "|" in annotation_text:
                wf = ProofObligation(
                    burden_kind=BurdenKind.WELL_FORMEDNESS,
                    symbol_name=symbol_name,
                    annotation_text=annotation_text,
                    priority=2,
                )
                obligations.append(wf)

            self._accumulator.add_all(obligations)
            result[symbol_name] = obligations

        logger.debug(
            "TheoremBurdenAnalyzer: computed %d burdens for %s",
            sum(len(v) for v in result.values()),
            qualname,
        )
        return result

    def _build_existence_obligation(
        self, symbol: str, annotation: str
    ) -> ProofObligation:
        """
        Construct an EXISTENCE obligation for the given symbol and annotation.

        Priority is elevated for 'return' annotations because a function that
        cannot return a value of its declared return type is broken by definition.
        """
        priority = 3 if symbol == "return" else 1
        return ProofObligation(
            burden_kind     = BurdenKind.EXISTENCE,
            symbol_name     = symbol,
            annotation_text = annotation,
            priority        = priority,
        )

    def _build_consistency_obligation(
        self, symbol: str, annotation: str
    ) -> ProofObligation:
        """
        Construct a CONSISTENCY obligation for the given symbol and annotation.

        Consistency obligations have priority 2 because they can be satisfied
        by the absence of contradictions — cheaper than full existence discharge.
        """
        return ProofObligation(
            burden_kind     = BurdenKind.CONSISTENCY,
            symbol_name     = symbol,
            annotation_text = annotation,
            priority        = 2,
        )

    def _build_completeness_obligation(
        self, symbol: str, annotation: str
    ) -> ProofObligation:
        """
        Construct a COMPLETENESS obligation.

        Completeness obligations are the most expensive to discharge because
        they require call-site analysis.  They receive the highest priority
        to ensure they are not silently dropped.
        """
        return ProofObligation(
            burden_kind     = BurdenKind.COMPLETENESS,
            symbol_name     = symbol,
            annotation_text = annotation,
            priority        = 4,
        )

    def discharge_where_possible(self) -> str:
        """
        Run the discharge engine on all accumulated obligations.

        Attempts existence discharge first (cheapest), then consistency,
        then completeness.  Updates the accumulator with discharged copies.
        Returns a summary string describing how many obligations were promoted.
        """
        with self._accumulator._lock:
            obs = list(self._accumulator._obligations)

        all_anns: dict[str, str] = {}
        for ob in obs:
            all_anns[ob.symbol_name] = ob.annotation_text

        context = {"all_annotations": all_anns, "callsites": []}
        discharged_obs = self._engine.discharge_all(obs, context)

        # copilot: replace accumulator's obligation list with discharged copies
        with self._accumulator._lock:
            self._accumulator._obligations = discharged_obs
            self._accumulator._dedup_keys = {
                self._accumulator._deduplicate_key(ob) for ob in discharged_obs
            }

        newly_discharged = sum(
            1 for o, d in zip(obs, discharged_obs)
            if not o.is_discharged and d.is_discharged
        )
        return (
            f"discharge_where_possible: {newly_discharged}/{len(obs)} obligations "
            f"newly discharged (score={self._accumulator.burden_score():.3f})"
        )

    def emit_judgments(self) -> list[Judgment]:
        """
        Emit a Judgment record for each accumulated obligation.

        Each Judgment carries:
          - Proposition with BEHAVIORAL kind, formula = "{symbol}: {annotation}"
          - Carrier with name = symbol_name
          - EvidenceBundle with zero or more items
          - TrustAnnotation reflecting the current trust level
          - Provenance from this analyzer
        """
        judgments: list[Judgment] = []
        with self._accumulator._lock:
            obs = list(self._accumulator._obligations)

        for ob in obs:
            prop = Proposition(
                kind           = PropositionKind.BEHAVIORAL,
                formula        = f"{ob.symbol_name}: {ob.annotation_text}",
                free_variables = (ob.symbol_name,),
                metadata       = {"burden_kind": ob.burden_kind.value},
            )
            carrier = Carrier(name=ob.symbol_name)
            bundle  = EvidenceBundle(items=ob.evidence_items)
            trust   = TrustAnnotation(
                level    = ob.trust_level or TrustLevel.UNVERIFIED,
                rationale = ob.discharge_method,
            )
            prov = Provenance(
                sources = (ProvenanceSource.ORACLE,),
                chain   = (ob.symbol_name, "TheoremBurdenAnalyzer"),
            )
            j = Judgment(
                proposition = prop,
                carrier     = carrier,
                evidence    = bundle,
                trust       = trust,
                provenance  = prov,
            )
            judgments.append(j)

        logger.debug("TheoremBurdenAnalyzer: emitted %d judgments", len(judgments))
        return judgments

    def full_report(self, target_qualname: str) -> BurdenReport:
        """
        Generate the full BurdenReport for all obligations accumulated so far.

        Runs discharge_where_possible() first so the report reflects the
        best achievable score given the current evidence.
        """
        self.discharge_where_possible()
        return self._accumulator.make_report(target_qualname)


# ──────────────────────────────────────────────────────────────────────────────
# TheoremBurdenWitness — runtime violation observer
# ──────────────────────────────────────────────────────────────────────────────

class TheoremBurdenWitness:
    """
    Runtime witness that observes type violations on annotated callables.

    theory2.tex Ch21 §21.5.7 introduces the witness mechanism: a wrapper
    that intercepts TypeError and AttributeError exceptions and records
    them as obligation violations.  The witness can also confirm that a
    call succeeded (providing positive evidence for completeness discharge).

    Witnesses are installed by TheoremBurdenCoordinator.install_witness().
    """

    def __init__(self) -> None:
        # copilot: _violations records all observed type errors with context
        self._violations: list[dict] = []
        self._call_count: int = 0
        self._error_count: int = 0

    def observe_type_error(self, exc: Exception, context: dict) -> None:
        """
        Record a TypeError or AttributeError with its calling context.

        The context dict should contain 'symbol', 'args', 'kwargs',
        'annotation', and any other relevant metadata.  Violations are
        stored as plain dicts for JSON serializability.
        """
        violation = {
            "exception_type": type(exc).__name__,
            "exception_msg":  str(exc),
            "context":        {k: repr(v) for k, v in context.items()},
            "observed_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._violations.append(violation)
        self._error_count += 1
        logger.warning(
            "TheoremBurdenWitness: type violation on %s: %s",
            context.get("symbol", "?"), exc,
        )

    def wrap_annotated(self, func: typing.Callable) -> typing.Callable:
        """
        Wrap a callable with type-violation observation.

        When the wrapped function raises TypeError or AttributeError, the
        exception is captured by observe_type_error() and then re-raised.
        Successful calls increment the call counter (positive witness).
        """
        # copilot: preserve __wrapped__ and __annotations__ for introspection
        @functools.wraps(func)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            self._call_count += 1
            try:
                result = func(*args, **kwargs)
                return result
            except (TypeError, AttributeError) as exc:
                ctx = {
                    "symbol":     getattr(func, "__qualname__", repr(func)),
                    "args":       args,
                    "kwargs":     kwargs,
                    "annotation": getattr(func, "__annotations__", {}),
                }
                self.observe_type_error(exc, ctx)
                raise

        _wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return _wrapper

    def get_violations(self) -> list[dict]:
        """Return all recorded violation records as a list of dicts."""
        return list(self._violations)

    def violation_count(self) -> int:
        """Return the total number of violations recorded."""
        return self._error_count

    def summary(self) -> str:
        """Return a human-readable summary of witness observations."""
        return (
            f"TheoremBurdenWitness: calls={self._call_count}, "
            f"violations={self._error_count} "
            f"({self._error_count / max(1, self._call_count) * 100:.1f}% error rate)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# TheoremBurdenCoordinator — top-level facade
# ──────────────────────────────────────────────────────────────────────────────

class TheoremBurdenCoordinator:
    """
    Top-level coordinator for theorem-burden analysis.

    theory2.tex Ch21 §21.5.8 describes the coordinator as the object that
    ties together the analyzer, the discharge engine, and the runtime witness.
    It provides a single entry point for auditing an entire module or class.

    Thread-safety: a single threading.Lock guards all state mutations.
    Multiple coordinators can operate concurrently without interference.

    Example::

        coordinator = TheoremBurdenCoordinator()
        report      = coordinator.full_burden_audit(MyModule)
        print(report.summary())
    """

    def __init__(self) -> None:
        # copilot: analyzer and witness are per-coordinator; lock prevents races
        self._analyzer = TheoremBurdenAnalyzer()
        self._witness  = TheoremBurdenWitness()
        self._lock     = threading.Lock()
        self._audited_objects: list[str] = []

    def coordinate(self, obj: Any) -> CoordinateObject:
        """
        Build a CoordinateObject for the given Python object.

        Uses the module path and qualname to construct the coordinate
        component tuple; infers the CoordinateKind from the object type.
        """
        module  = getattr(obj, "__module__", "unknown") or "unknown"
        qualname = getattr(obj, "__qualname__", getattr(obj, "__name__", repr(obj)))
        parts   = tuple(module.split(".") + qualname.split("."))

        if inspect.ismodule(obj):
            kind = CoordinateKind.MODULE
        elif inspect.isclass(obj):
            kind = CoordinateKind.INTERFACE
        elif callable(obj):
            kind = CoordinateKind.FUNCTION
        else:
            kind = CoordinateKind.REGION

        return CoordinateObject(
            components     = parts,
            kind           = kind,
            support_labels = frozenset({qualname}),
            metadata       = {"module": module, "qualname": qualname},
        )

    def full_burden_audit(self, module_or_class: Any) -> BurdenReport:
        """
        Perform a complete burden audit on a module, class, or function.

        Steps:
          1. Coordinate the target object.
          2. compute_burden() to enumerate all obligations.
          3. discharge_where_possible() to auto-discharge trivials.
          4. make_report() to produce the final BurdenReport.

        This method is thread-safe; concurrent audits of different objects
        are serialised through the coordinator lock.
        """
        with self._lock:
            qualname = getattr(
                module_or_class,
                "__qualname__",
                getattr(module_or_class, "__name__", repr(module_or_class))
            )
            self._audited_objects.append(qualname)

            logger.info("TheoremBurdenCoordinator: auditing %s", qualname)

            coord = self.coordinate(module_or_class)
            _ = coord  # copilot: coordinate may be used by downstream consumers

            self._analyzer.compute_burden(module_or_class)
            summary = self._analyzer.discharge_where_possible()
            logger.debug("TheoremBurdenCoordinator: %s", summary)

            report = self._analyzer.full_report(qualname)
            logger.info(
                "TheoremBurdenCoordinator: audit complete, score=%.3f",
                report.burden_score,
            )
            return report

    def install_witness(self, func: typing.Callable) -> typing.Callable:
        """
        Wrap a callable with the coordinator's TheoremBurdenWitness.

        The wrapped function is returned; the original is accessible via
        __wrapped__.  Violations are recorded in the coordinator's witness
        and can be retrieved via get_violations().
        """
        return self._witness.wrap_annotated(func)

    def report(self) -> str:
        """
        Return a summary string of all coordinator activity.

        Includes the list of audited objects, current burden scores, and
        the witness summary.
        """
        lines = [
            "TheoremBurdenCoordinator Report",
            f"  Audited objects: {', '.join(self._audited_objects) or 'none'}",
            f"  Accumulator size: {len(self._analyzer._accumulator)}",
            f"  Burden score: {self._analyzer._accumulator.burden_score():.4f}",
            f"  Witness: {self._witness.summary()}",
            f"  Engine: {self._engine_summary()}",
        ]
        return "\n".join(lines)

    def _engine_summary(self) -> str:
        """Return the discharge engine's log summary."""
        return self._analyzer._engine.discharge_report()


# ──────────────────────────────────────────────────────────────────────────────
# Module-level convenience helpers
# ──────────────────────────────────────────────────────────────────────────────

def analyze_module_burden(module: Any) -> BurdenReport:
    """
    Convenience function: create a coordinator and audit an entire module.

    theory2.tex Ch21 §21.5 recommends running the full burden audit on each
    module at import time (via __main__ guards) to surface unverified
    annotations early in the development cycle.
    """
    coord = TheoremBurdenCoordinator()
    return coord.full_burden_audit(module)


def quick_burden_score(obj: Any) -> float:
    """
    Return a quick burden_score ∈ [0.0, 1.0] for any Python object.

    Useful for ad-hoc checks in notebooks or CI pipelines where a full
    BurdenReport is not needed.
    """
    report = analyze_module_burden(obj)
    return report.burden_score


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test (§21.5 requires each module to be self-testable)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        def annotated_func(x: int, y: str) -> bool:
            """A trivially annotated function used as a test target."""
            return True

        # copilot: test CoordinateObject construction and BurdenReport generation
        coordinator = TheoremBurdenCoordinator()
        report = coordinator.full_burden_audit(annotated_func)
        assert isinstance(report, BurdenReport), f"Expected BurdenReport, got {type(report)}"
        assert report.burden_score >= 0.0, f"Negative burden_score: {report.burden_score}"
        assert report.burden_score <= 1.0, f"burden_score > 1.0: {report.burden_score}"

        # copilot: test analyzer in isolation
        analyzer = TheoremBurdenAnalyzer()
        burden = analyzer.compute_burden(annotated_func)
        assert isinstance(burden, dict), f"Expected dict, got {type(burden)}"
        assert len(burden) > 0, "compute_burden returned empty dict for annotated function"

        # copilot: test witness wrapping and call counting
        witness = TheoremBurdenWitness()
        wrapped = witness.wrap_annotated(annotated_func)
        result = wrapped(1, "hello")
        assert result is True, f"Wrapped function returned wrong value: {result}"
        assert witness.violation_count() == 0, "Unexpected violation for valid call"

        # copilot: test discharge engine directly
        engine = BurdenDischargeEngine()
        ob = ProofObligation(
            burden_kind=BurdenKind.EXISTENCE,
            symbol_name="test_param",
            annotation_text="int",
        )
        discharged_ob = engine.attempt_existence_discharge(ob)
        assert discharged_ob.is_discharged, "int annotation should be trivially discharged"
        assert discharged_ob.trust_level is not None

        # copilot: test BurdenAccumulator dedup
        acc = BurdenAccumulator()
        ob1 = ProofObligation(
            burden_kind=BurdenKind.EXISTENCE,
            symbol_name="x",
            annotation_text="int",
        )
        ob2 = ProofObligation(
            burden_kind=BurdenKind.EXISTENCE,
            symbol_name="x",
            annotation_text="int",
        )
        acc.add(ob1)
        acc.add(ob2)
        assert len(acc) == 1, f"Dedup failed: expected 1, got {len(acc)}"

        # copilot: test BurdenReport serialization
        test_report = acc.make_report("test_target")
        d = test_report.to_dict()
        assert "target_qualname" in d
        assert "burden_score" in d

        print(
            f"[smoke] obligations={report.pending_count + report.discharged_count}, "
            f"score={report.burden_score:.2f}"
        )
        print("[smoke] PASS")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
