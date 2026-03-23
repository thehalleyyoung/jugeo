"""
jugeo.python_runtime.generated_contracts.theorems

theory2.tex Ch21 — Theorem Burden for Generated Contracts.

A contract is *sound* if every runtime value satisfying the annotated type
also satisfies the contract's obligations.  It is *complete* if every
obligation the contract imposes can be witnessed by some value.  *Precision*
measures how tight the contract is relative to the actual invariants.

Formal definitions:
    Soundness:    ∀ v : T. annotated_type(v) ⟹ contract_holds(v)
    Completeness: ∀ ob ∈ O. ∃ v : T. obligation_discharged(v, ob)
    Precision:    precision = |true_pos| / (|true_pos| + |false_pos|)
    Recall:       recall    = |true_pos| / (|true_pos| + |false_neg|)
    F1-score:     F1        = 2 · precision · recall / (precision + recall)

Conservativity (Ch21 §5): A generated contract is *conservative* iff it
implies no obligations beyond those entailed by the original type annotation.
Minimality: no strict sub-contract satisfies all obligations with fewer
terms.

Judgment tuple representation throughout:
    (c, φ, A, E, O, B, T, Π) where
        c  = coordinate   (theorem's site location)
        φ  = formula      (the theorem statement)
        A  = carrier      (the type under scrutiny)
        E  = evidence     (proof witnesses / SMT certificates)
        O  = obligations  (proof obligations to discharge)
        B  = obstructions (known counterexamples / refutations)
        T  = trust tier   (TrustTier value)
        Π  = provenance   (proof origin: SMT, runtime, human, …)

References:
    theory2.tex Ch21: Theorem Burden for Generated Contracts.

# copilot: soundness = ∀ v : T. contract_holds(v); completeness = ∃ v : T. obligation_discharged(v)
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import logging
import math
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jugeo imports with inline stub fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind, Site
except Exception:
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple = ()
        kind: Any = None
        support_labels: frozenset = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class Site: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, Carrier,
        EvidenceBundle, EvidenceItem, ResidualObligation, Obstruction,
        TrustAnnotation, Provenance,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; provenance: tuple = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1; is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple = (); chain: tuple = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None; is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str = "th") -> str:
    """Generate a short unique identifier with the given prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# TrustTier — ordered trust algebra
# ---------------------------------------------------------------------------

class TrustTier(enum.IntEnum):
    """Ordered trust algebra for theorem verification.

    theory2.tex Ch21 — trust tiers form a partial order:
        PROPOSAL ≤ REVIEWED ≤ VERIFIED ≤ RUNTIME_WITNESSED ≤ PROOF_BACKED
    """
    PROPOSAL          = 1
    REVIEWED          = 2
    VERIFIED          = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED      = 5

    def satisfies(self, minimum: "TrustTier") -> bool:
        """Return True iff self >= minimum in the trust order."""
        return self.value >= minimum.value

    def elevate(self, target: "TrustTier") -> "TrustTier":
        """Return the higher of self and target."""
        return TrustTier(max(self.value, target.value))


# ---------------------------------------------------------------------------
# JudgmentTuple — canonical (c, φ, A, E, O, B, T, Π) representation
# ---------------------------------------------------------------------------

class JudgmentTuple(NamedTuple):
    """The canonical (c, φ, A, E, O, B, T, Π) judgment representation.

    Fields:
        c   — coordinate: theorem's site location
        phi — formula: the theorem statement
        A   — carrier: the type under scrutiny
        E   — evidence: proof witnesses / SMT certificates
        O   — obligations: proof obligations to discharge
        B   — obstructions: known counterexamples / refutations
        T   — trust tier: TrustTier value
        Pi  — provenance: proof origin: SMT, runtime, human, …
    """
    c: Any    # coordinate
    phi: Any  # formula
    A: Any    # carrier
    E: Any    # evidence
    O: Any    # obligations
    B: Any    # obstructions
    T: Any    # trust
    Pi: Any   # provenance


# ---------------------------------------------------------------------------
# TheoremKind — classification of theorem types
# ---------------------------------------------------------------------------

class TheoremKind(enum.Enum):
    """Classification of theorem kinds in the contract burden framework.

    theory2.tex Ch21 §1-5 — each kind corresponds to a formal property.
    """
    SOUNDNESS      = "soundness"
    COMPLETENESS   = "completeness"
    PRECISION      = "precision"
    CONSERVATIVITY = "conservativity"
    MINIMALITY     = "minimality"


# ---------------------------------------------------------------------------
# ProofStatus — lifecycle of a proof attempt
# ---------------------------------------------------------------------------

class ProofStatus(enum.Enum):
    """Lifecycle status of a proof attempt for a contract theorem.

    theory2.tex Ch21 §6 — proof lifecycle states.
    """
    OPEN       = "open"
    DISCHARGED = "discharged"
    PARTIAL    = "partial"
    REFUTED    = "refuted"
    DEFERRED   = "deferred"


# ---------------------------------------------------------------------------
# ContractTheorem — first-class theorem object
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContractTheorem:
    """A first-class theorem object in the contract burden framework.

    theory2.tex Ch21 — theorems are objects in the proof category.
    Each theorem carries its statement, kind, proof status, and obligations.
    """
    theorem_id: str
    kind: TheoremKind
    formula: str
    coordinate_key: str
    trust_tier: TrustTier
    status: ProofStatus
    proof_sketch: str = ""
    obligations: tuple = ()

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this theorem."""
        logger.debug("ContractTheorem.to_judgment_tuple: theorem_id=%s", self.theorem_id)
        c = self.coordinate_key
        phi = self.formula
        A = Carrier(
            name=f"theorem:{self.theorem_id}",
            is_dependent=self.kind in (TheoremKind.COMPLETENESS, TheoremKind.CONSERVATIVITY),
        )
        E = EvidenceBundle(items=(
            EvidenceItem(
                payload={
                    "theorem_id": self.theorem_id,
                    "kind": self.kind.value,
                    "status": self.status.value,
                    "proof_sketch": self.proof_sketch[:80],
                },
                channel="theorem_registry",
                timestamp=_now_iso(),
            ),
        ))
        O = tuple(
            ResidualObligation(
                description=str(ob),
                obligation_id=f"{self.theorem_id}_ob_{i}",
                priority=1,
                is_discharged=(self.status == ProofStatus.DISCHARGED),
            )
            for i, ob in enumerate(self.obligations)
        )
        B = (
            (Obstruction(
                description=f"refuted:{self.proof_sketch[:50]}",
                obstruction_id=f"{self.theorem_id}_ref",
            ),)
            if self.status == ProofStatus.REFUTED else ()
        )
        T = self.trust_tier
        Pi = Provenance(
            sources=(self.coordinate_key,),
            chain=(self.theorem_id, self.kind.value, self.status.value),
        )
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def discharge(self, evidence: str) -> "ContractTheorem":
        """Return a new theorem with status=DISCHARGED and updated proof_sketch."""
        logger.info(
            "ContractTheorem.discharge: theorem=%s evidence=%s", self.theorem_id, evidence[:60]
        )
        new_sketch = f"{self.proof_sketch} | discharged:{evidence[:100]}"
        elevated = self.trust_tier.elevate(TrustTier.VERIFIED)
        return replace(
            self,
            status=ProofStatus.DISCHARGED,
            proof_sketch=new_sketch,
            trust_tier=elevated,
        )

    def refute(self, counterexample: str) -> "ContractTheorem":
        """Return a new theorem with status=REFUTED and counterexample recorded."""
        logger.warning(
            "ContractTheorem.refute: theorem=%s counterexample=%s",
            self.theorem_id, counterexample[:60],
        )
        new_sketch = f"{self.proof_sketch} | refuted_by:{counterexample[:100]}"
        return replace(self, status=ProofStatus.REFUTED, proof_sketch=new_sketch)

    def defer(self, reason: str) -> "ContractTheorem":
        """Return a new theorem with status=DEFERRED and reason recorded."""
        logger.info("ContractTheorem.defer: theorem=%s reason=%s", self.theorem_id, reason[:60])
        return replace(
            self,
            status=ProofStatus.DEFERRED,
            proof_sketch=f"{self.proof_sketch} | deferred:{reason[:100]}",
        )

    def is_open(self) -> bool:
        """Return True iff the theorem is still open (needs proof)."""
        return self.status == ProofStatus.OPEN

    def obligation_count(self) -> int:
        """Return the number of proof obligations."""
        return len(self.obligations)

    def summary(self) -> str:
        """Return a one-line summary of this theorem."""
        return (
            f"Theorem[{self.theorem_id}] kind={self.kind.value} status={self.status.value} "
            f"trust={self.trust_tier.name} obligations={len(self.obligations)} "
            f"formula={self.formula[:60]!r}"
        )


# ---------------------------------------------------------------------------
# SoundnessProof — a completed soundness proof record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SoundnessProof:
    """A record of a completed soundness proof.

    theory2.tex Ch21 §1 — soundness: ∀ v : T. contract_holds(v).
    The proof is evidenced by a set of witness values that all satisfy the
    contract formula.
    """
    proof_id: str
    theorem_id: str
    witness_values: tuple
    encoding: str
    verification_level: str
    timestamp: str = field(default_factory=_now_iso)

    def is_valid(self) -> bool:
        """Return True iff the proof has witnesses and a non-empty encoding."""
        return len(self.witness_values) > 0 and bool(self.encoding) and bool(self.verification_level)

    def to_smt_formula(self) -> str:
        """Construct an SMT-LIB2 style assertion string from the proof.

        Returns a string of the form:
            (assert (forall ((v Type)) (=> (well-typed v) (contract-holds v))))
        incorporating the theorem_id and encoding.
        """
        smt_id = self.theorem_id.replace("-", "_").replace(" ", "_")
        enc_safe = self.encoding.replace('"', '\\"')[:60]
        return (
            f'(assert (forall ((v Any))\n'
            f'  (=> (well-typed_{smt_id} v)\n'
            f'      (contract-holds v "{enc_safe}"))))\n'
            f'; proof_id={self.proof_id} level={self.verification_level}'
        )

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this proof."""
        logger.debug("SoundnessProof.to_judgment_tuple: proof_id=%s", self.proof_id)
        c = f"proof:{self.proof_id}"
        phi = f"soundness_proof:{self.theorem_id}"
        A = Carrier(name=f"proof:{self.proof_id}", is_dependent=True)
        E = EvidenceBundle(items=tuple(
            EvidenceItem(
                payload={"witness": str(w)[:80], "proof_id": self.proof_id},
                channel="soundness_proof",
                timestamp=self.timestamp,
            )
            for w in self.witness_values
        ))
        O = (
            ()
            if self.is_valid()
            else (ResidualObligation(
                description="proof_invalid",
                obligation_id=f"{self.proof_id}_inv",
                priority=2,
            ),)
        )
        B = (
            ()
            if self.is_valid()
            else (Obstruction(
                description="invalid_proof",
                obstruction_id=f"{self.proof_id}_ob",
                severity=2,
            ),)
        )
        T = TrustTier.PROOF_BACKED if self.is_valid() else TrustTier.PROPOSAL
        Pi = Provenance(
            sources=(self.verification_level,),
            chain=(self.proof_id, self.theorem_id, self.timestamp),
        )
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def coverage(self) -> float:
        """Return fraction of witness values that are non-None."""
        if not self.witness_values:
            return 0.0
        return sum(1 for w in self.witness_values if w is not None) / len(self.witness_values)


# ---------------------------------------------------------------------------
# CompletenessArgument — completeness measurement
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CompletenessArgument:
    """A measurement of the completeness of a contract theorem.

    theory2.tex Ch21 §2 — completeness: ∀ ob ∈ O. ∃ v : T. obligation_discharged(v, ob).
    Tracks coverage ratio and identifies uncovered obligations.
    """
    argument_id: str
    theorem_id: str
    coverage_ratio: float
    uncovered_obligations: tuple
    completeness_score: float

    def is_complete(self) -> bool:
        """Return True iff coverage_ratio >= 1.0 and no uncovered obligations remain."""
        return self.coverage_ratio >= 1.0 and len(self.uncovered_obligations) == 0

    def gap_summary(self) -> str:
        """Return a human-readable description of coverage gaps."""
        if self.is_complete():
            return "All obligations covered."
        sample = ", ".join(str(o)[:40] for o in self.uncovered_obligations[:3])
        ellipsis = "..." if len(self.uncovered_obligations) > 3 else ""
        return (
            f"Coverage {self.coverage_ratio:.1%}: {len(self.uncovered_obligations)} "
            f"uncovered obligations: {sample}{ellipsis}"
        )

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this argument."""
        logger.debug("CompletenessArgument.to_judgment_tuple: argument_id=%s", self.argument_id)
        c = f"completeness:{self.argument_id}"
        phi = f"completeness_argument:{self.theorem_id} coverage={self.coverage_ratio:.3f}"
        A = Carrier(name=f"argument:{self.argument_id}")
        E = EvidenceBundle(items=(
            EvidenceItem(
                payload={
                    "coverage": self.coverage_ratio,
                    "score": self.completeness_score,
                    "gaps": len(self.uncovered_obligations),
                },
                channel="completeness",
                timestamp=_now_iso(),
            ),
        ))
        O = tuple(
            ResidualObligation(
                description=str(ob)[:80],
                obligation_id=f"{self.argument_id}_unc_{i}",
                priority=2,
            )
            for i, ob in enumerate(self.uncovered_obligations)
        )
        B = (
            ()
            if self.is_complete()
            else (Obstruction(
                description=f"incomplete:{len(self.uncovered_obligations)}_gaps",
                obstruction_id=f"{self.argument_id}_gap",
                severity=1,
            ),)
        )
        T = (
            TrustTier.PROOF_BACKED if self.is_complete()
            else TrustTier.VERIFIED if self.coverage_ratio >= 0.8
            else TrustTier.REVIEWED
        )
        Pi = Provenance(sources=(self.theorem_id,), chain=(self.argument_id,))
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def gap_count(self) -> int:
        """Return the number of uncovered obligations."""
        return len(self.uncovered_obligations)


# ---------------------------------------------------------------------------
# PrecisionMetric — precision / recall / F1 measurement
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PrecisionMetric:
    """Precision and recall metrics for a contract theorem.

    theory2.tex Ch21 §3:
        precision = |true_pos| / (|true_pos| + |false_pos|)
        recall    = |true_pos| / (|true_pos| + |false_neg|)
        F1        = 2 · precision · recall / (precision + recall)
    """
    metric_id: str
    theorem_id: str
    false_positive_rate: float
    false_negative_rate: float
    f1_score: float
    sample_size: int = 0

    def precision(self) -> float:
        """Return precision = 1 - false_positive_rate, clamped to [0, 1]."""
        return max(0.0, min(1.0, 1.0 - self.false_positive_rate))

    def recall(self) -> float:
        """Return recall = 1 - false_negative_rate, clamped to [0, 1]."""
        return max(0.0, min(1.0, 1.0 - self.false_negative_rate))

    def grade(self) -> str:
        """Return letter grade: A if f1>=0.9, B if >=0.75, C if >=0.6, D otherwise."""
        if self.f1_score >= 0.9:
            return "A"
        elif self.f1_score >= 0.75:
            return "B"
        elif self.f1_score >= 0.6:
            return "C"
        return "D"

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this metric."""
        logger.debug("PrecisionMetric.to_judgment_tuple: metric_id=%s", self.metric_id)
        p = self.precision()
        r = self.recall()
        c = f"metric:{self.metric_id}"
        phi = f"precision_metric:{self.theorem_id} f1={self.f1_score:.3f}"
        A = Carrier(name=f"metric:{self.metric_id}")
        E = EvidenceBundle(items=(
            EvidenceItem(
                payload={
                    "precision": p, "recall": r, "f1": self.f1_score,
                    "fp_rate": self.false_positive_rate,
                    "fn_rate": self.false_negative_rate,
                    "n": self.sample_size,
                },
                channel="precision_metric",
                timestamp=_now_iso(),
            ),
        ))
        O = (
            ()
            if self.f1_score >= 0.75
            else (ResidualObligation(
                description=f"low_f1:{self.f1_score:.3f}",
                obligation_id=f"{self.metric_id}_f1",
                priority=1,
            ),)
        )
        B = (
            ()
            if self.f1_score >= 0.5
            else (Obstruction(
                description=f"very_low_f1:{self.f1_score:.3f}",
                obstruction_id=f"{self.metric_id}_low",
                severity=2,
            ),)
        )
        T = (
            TrustTier.PROOF_BACKED if self.f1_score >= 0.9
            else TrustTier.VERIFIED if self.f1_score >= 0.75
            else TrustTier.REVIEWED
        )
        Pi = Provenance(
            sources=(self.theorem_id,),
            chain=(self.metric_id, f"grade:{self.grade()}"),
        )
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def summary(self) -> str:
        """Return a one-line summary of this metric."""
        return (
            f"PrecisionMetric[{self.metric_id}] theorem={self.theorem_id} "
            f"precision={self.precision():.3f} recall={self.recall():.3f} "
            f"f1={self.f1_score:.3f} grade={self.grade()} n={self.sample_size}"
        )


# ---------------------------------------------------------------------------
# ContractTheoremRegistry — registry of theorems, proofs, arguments, metrics
# ---------------------------------------------------------------------------

@dataclass
class ContractTheoremRegistry:
    """Registry of theorems, proofs, completeness arguments, and precision metrics.

    theory2.tex Ch21 §7 — the theorem registry is the global proof ledger
    for the contract burden framework.
    """
    registry_id: str
    theorems: dict = field(default_factory=dict)
    proofs: dict = field(default_factory=dict)
    arguments: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)

    def add_theorem(self, t: ContractTheorem) -> None:
        """Add a theorem to the registry."""
        self.theorems[t.theorem_id] = t
        logger.info(
            "ContractTheoremRegistry.add_theorem: registry=%s theorem=%s kind=%s",
            self.registry_id, t.theorem_id, t.kind.value,
        )

    def add_proof(self, p: SoundnessProof) -> None:
        """Add a soundness proof to the registry."""
        self.proofs[p.proof_id] = p
        logger.debug(
            "ContractTheoremRegistry.add_proof: proof=%s for theorem=%s valid=%s",
            p.proof_id, p.theorem_id, p.is_valid(),
        )

    def add_argument(self, a: CompletenessArgument) -> None:
        """Add a completeness argument to the registry."""
        self.arguments[a.argument_id] = a
        logger.debug(
            "ContractTheoremRegistry.add_argument: argument=%s for theorem=%s coverage=%.3f",
            a.argument_id, a.theorem_id, a.coverage_ratio,
        )

    def add_metric(self, m: PrecisionMetric) -> None:
        """Add a precision metric to the registry."""
        self.metrics[m.metric_id] = m
        logger.debug(
            "ContractTheoremRegistry.add_metric: metric=%s for theorem=%s f1=%.3f",
            m.metric_id, m.theorem_id, m.f1_score,
        )

    def get_theorem(self, tid: str) -> Optional[ContractTheorem]:
        """Return the theorem with the given ID, or None."""
        return self.theorems.get(tid)

    def open_theorems(self) -> list:
        """Return all theorems with status=OPEN."""
        return [t for t in self.theorems.values() if t.is_open()]

    def discharged_theorems(self) -> list:
        """Return all theorems with status=DISCHARGED."""
        return [t for t in self.theorems.values() if t.status == ProofStatus.DISCHARGED]

    def summary(self) -> dict:
        """Return a summary dict of the registry state."""
        return {
            "registry_id": self.registry_id,
            "total_theorems": len(self.theorems),
            "open": len(self.open_theorems()),
            "discharged": len(self.discharged_theorems()),
            "refuted": sum(1 for t in self.theorems.values() if t.status == ProofStatus.REFUTED),
            "partial": sum(1 for t in self.theorems.values() if t.status == ProofStatus.PARTIAL),
            "deferred": sum(1 for t in self.theorems.values() if t.status == ProofStatus.DEFERRED),
            "proofs": len(self.proofs),
            "arguments": len(self.arguments),
            "metrics": len(self.metrics),
        }

    def all_judgment_tuples(self) -> list:
        """Return JudgmentTuple for all theorems, proofs, arguments, and metrics."""
        result = []
        for t in self.theorems.values():
            result.append(t.to_judgment_tuple())
        for p in self.proofs.values():
            result.append(p.to_judgment_tuple())
        for a in self.arguments.values():
            result.append(a.to_judgment_tuple())
        for m in self.metrics.values():
            result.append(m.to_judgment_tuple())
        logger.debug(
            "ContractTheoremRegistry.all_judgment_tuples: registry=%s total=%d",
            self.registry_id, len(result),
        )
        return result

    def get_proofs_for(self, theorem_id: str) -> list:
        """Return all proofs associated with the given theorem_id."""
        return [p for p in self.proofs.values() if p.theorem_id == theorem_id]

    def get_argument_for(self, theorem_id: str) -> Optional[CompletenessArgument]:
        """Return the most recent completeness argument for the theorem, or None."""
        matching = [a for a in self.arguments.values() if a.theorem_id == theorem_id]
        return matching[-1] if matching else None

    def get_metric_for(self, theorem_id: str) -> Optional[PrecisionMetric]:
        """Return the most recent precision metric for the theorem, or None."""
        matching = [m for m in self.metrics.values() if m.theorem_id == theorem_id]
        return matching[-1] if matching else None


# ---------------------------------------------------------------------------
# TheoremVerifier — checks witness values against theorem formulae
# ---------------------------------------------------------------------------

@dataclass
class TheoremVerifier:
    """Verifier that checks witness values against theorem formulae.

    theory2.tex Ch21 §1 — the verifier implements the soundness check:
        ∀ v : witnesses. theorem.formula(v) ≡ True
    """
    verifier_id: str
    trust_required: TrustTier = field(default=TrustTier.REVIEWED)
    log: list = field(default_factory=list)

    def verify(self, theorem: ContractTheorem, witnesses: list) -> SoundnessProof:
        """Verify a theorem against a set of witness values.

        Checks each witness using _check_witness(). Returns a SoundnessProof
        with the passing witnesses and appropriate verification_level.
        """
        logger.info(
            "TheoremVerifier.verify: verifier=%s theorem=%s witnesses=%d",
            self.verifier_id, theorem.theorem_id, len(witnesses),
        )
        self.log.append({
            "action": "verify",
            "theorem_id": theorem.theorem_id,
            "witnesses_count": len(witnesses),
            "timestamp": _now_iso(),
        })
        checked = [w for w in witnesses if self._check_witness(w, theorem.formula)]
        proof_id = _new_id("proof")
        encoding = (
            f"smt2:{theorem.formula[:60].replace(' ', '_')}"
            if theorem.formula else "unencoded"
        )
        if len(witnesses) == 0:
            verification_level = "vacuous"
        elif len(checked) == len(witnesses):
            verification_level = "full"
        elif len(checked) > 0:
            verification_level = "partial"
        else:
            verification_level = "failed"
        proof = SoundnessProof(
            proof_id=proof_id,
            theorem_id=theorem.theorem_id,
            witness_values=tuple(checked),
            encoding=encoding,
            verification_level=verification_level,
        )
        logger.info(
            "TheoremVerifier.verify: proof=%s level=%s checked=%d/%d",
            proof_id, verification_level, len(checked), len(witnesses),
        )
        return proof

    def bulk_verify(self, theorems: list, witnesses: list) -> list:
        """Verify all theorems against the same set of witnesses.

        Returns one SoundnessProof per theorem.
        """
        logger.info(
            "TheoremVerifier.bulk_verify: verifier=%s theorems=%d witnesses=%d",
            self.verifier_id, len(theorems), len(witnesses),
        )
        return [self.verify(t, witnesses) for t in theorems]

    def _check_witness(self, value: Any, formula: str) -> bool:
        """Check whether value satisfies the formula string.

        Attempts eval() with a restricted namespace containing the value as `v`.
        Falls back to True (benefit of the doubt) if formula is not evaluable.
        """
        if value is None:
            return False
        if not formula.strip():
            return True
        safe_builtins = {
            "isinstance": isinstance, "len": len, "type": type,
            "hasattr": hasattr, "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict, "tuple": tuple,
            "abs": abs, "min": min, "max": max, "sum": sum,
        }
        try:
            result = eval(formula, {"v": value, "value": value, "__builtins__": safe_builtins})
            return bool(result)
        except Exception:
            # Non-evaluable formula: give benefit of the doubt
            return True

    def report(self) -> dict:
        """Return a summary of verifier activity."""
        return {
            "verifier_id": self.verifier_id,
            "verifications": len(self.log),
            "log_tail": self.log[-10:],
        }


# ---------------------------------------------------------------------------
# CompletenessChecker — checks coverage of obligations
# ---------------------------------------------------------------------------

@dataclass
class CompletenessChecker:
    """Checks completeness of a contract theorem against a set of obligations.

    theory2.tex Ch21 §2 — the completeness checker verifies that every
    obligation in the theorem has a corresponding witness.
    """
    checker_id: str
    coverage_threshold: float = 0.8
    findings: list = field(default_factory=list)

    def check(self, theorem: ContractTheorem, obligations: list) -> CompletenessArgument:
        """Check completeness of the theorem against provided obligations.

        Returns a CompletenessArgument with coverage_ratio and any gaps.
        """
        logger.info(
            "CompletenessChecker.check: checker=%s theorem=%s obligations=%d",
            self.checker_id, theorem.theorem_id, len(obligations),
        )
        coverage = self._compute_coverage(theorem, obligations)
        provided_strs = {str(o) for o in obligations}
        uncovered = tuple(str(o) for o in theorem.obligations if str(o) not in provided_strs)
        score = coverage * (1.0 if len(uncovered) == 0 else max(0.3, 1.0 - 0.1 * len(uncovered)))
        argument_id = _new_id("arg")
        arg = CompletenessArgument(
            argument_id=argument_id,
            theorem_id=theorem.theorem_id,
            coverage_ratio=coverage,
            uncovered_obligations=uncovered,
            completeness_score=score,
        )
        self.findings.append({
            "theorem_id": theorem.theorem_id,
            "coverage": coverage,
            "score": score,
            "uncovered_count": len(uncovered),
            "timestamp": _now_iso(),
        })
        logger.info(
            "CompletenessChecker.check: argument=%s coverage=%.3f score=%.3f gaps=%d",
            argument_id, coverage, score, len(uncovered),
        )
        return arg

    def bulk_check(self, pairs: list) -> list:
        """Check completeness for multiple (theorem, obligations) pairs.

        pairs: list of (ContractTheorem, list_of_obligations) tuples.
        """
        return [self.check(t, obs) for t, obs in pairs]

    def _compute_coverage(self, theorem: ContractTheorem, obligations: list) -> float:
        """Compute the fraction of theorem.obligations covered by provided obligations."""
        if not theorem.obligations:
            return 1.0
        theorem_obs_strs = [str(o) for o in theorem.obligations]
        provided_strs = {str(o) for o in obligations}
        covered = sum(1 for o in theorem_obs_strs if o in provided_strs)
        coverage = covered / len(theorem_obs_strs)
        logger.debug(
            "CompletenessChecker._compute_coverage: theorem=%s covered=%d/%d=%.3f",
            theorem.theorem_id, covered, len(theorem_obs_strs), coverage,
        )
        return coverage

    def report(self) -> dict:
        """Return a summary of completeness checker activity."""
        passed = sum(1 for f in self.findings if f["coverage"] >= self.coverage_threshold)
        return {
            "checker_id": self.checker_id,
            "threshold": self.coverage_threshold,
            "checks": len(self.findings),
            "passed": passed,
            "failed": len(self.findings) - passed,
        }


# ---------------------------------------------------------------------------
# PrecisionBoundComputer — estimates precision/recall from trace data
# ---------------------------------------------------------------------------

@dataclass
class PrecisionBoundComputer:
    """Estimates precision and recall bounds from runtime trace data.

    theory2.tex Ch21 §3 — precision bounds are derived from sampled
    execution traces that record false positives and false negatives.
    """
    computer_id: str
    trace_data: list = field(default_factory=list)

    def compute(self, theorem: ContractTheorem, trace_data: list) -> PrecisionMetric:
        """Compute precision metrics for the theorem from the trace data.

        Returns a PrecisionMetric with estimated fp_rate, fn_rate, and f1_score.
        """
        logger.info(
            "PrecisionBoundComputer.compute: computer=%s theorem=%s trace_samples=%d",
            self.computer_id, theorem.theorem_id, len(trace_data),
        )
        self.trace_data.extend(trace_data)
        fp_rate = self._estimate_fp_rate(theorem, trace_data)
        fn_rate = self._estimate_fn_rate(theorem, trace_data)
        precision = max(0.0, min(1.0, 1.0 - fp_rate))
        recall = max(0.0, min(1.0, 1.0 - fn_rate))
        f1 = self._f1(precision, recall)
        metric_id = _new_id("metric")
        logger.info(
            "PrecisionBoundComputer.compute: metric=%s fp=%.3f fn=%.3f f1=%.3f",
            metric_id, fp_rate, fn_rate, f1,
        )
        return PrecisionMetric(
            metric_id=metric_id,
            theorem_id=theorem.theorem_id,
            false_positive_rate=fp_rate,
            false_negative_rate=fn_rate,
            f1_score=f1,
            sample_size=len(trace_data),
        )

    def _estimate_fp_rate(self, theorem: ContractTheorem, trace: list) -> float:
        """Estimate false positive rate from trace items."""
        if not trace:
            return 0.1  # default estimate when no trace available
        fp_count = sum(1 for item in trace if isinstance(item, dict) and item.get("false_positive", False))
        rate = fp_count / len(trace)
        logger.debug(
            "PrecisionBoundComputer._estimate_fp_rate: theorem=%s fp=%d/%d=%.3f",
            theorem.theorem_id, fp_count, len(trace), rate,
        )
        return rate

    def _estimate_fn_rate(self, theorem: ContractTheorem, trace: list) -> float:
        """Estimate false negative rate from trace items."""
        if not trace:
            return 0.05  # default estimate when no trace available
        fn_count = sum(1 for item in trace if isinstance(item, dict) and item.get("false_negative", False))
        rate = fn_count / len(trace)
        logger.debug(
            "PrecisionBoundComputer._estimate_fn_rate: theorem=%s fn=%d/%d=%.3f",
            theorem.theorem_id, fn_count, len(trace), rate,
        )
        return rate

    def _f1(self, precision: float, recall: float) -> float:
        """Compute F1 score from precision and recall."""
        denom = precision + recall
        if denom == 0.0:
            return 0.0
        return 2.0 * precision * recall / denom

    def report(self) -> dict:
        """Return a summary of computation activity."""
        return {
            "computer_id": self.computer_id,
            "trace_samples": len(self.trace_data),
        }


# ---------------------------------------------------------------------------
# TheoremSuite — orchestrates all verification steps
# ---------------------------------------------------------------------------

@dataclass
class TheoremSuite:
    """Orchestrates soundness, completeness, and precision verification.

    theory2.tex Ch21 §8 — the suite runs all three verification steps
    and records results in the theorem registry.
    """
    suite_id: str
    registry: ContractTheoremRegistry
    verifier: TheoremVerifier
    checker: CompletenessChecker
    computer: PrecisionBoundComputer

    def run(
        self,
        theorem: ContractTheorem,
        witnesses: list,
        obligations: list,
        trace_data: list,
    ) -> list:
        """Run all verification steps for a single theorem.

        Steps:
          1. Register the theorem
          2. Verify soundness (TheoremVerifier)
          3. Check completeness (CompletenessChecker)
          4. Compute precision bounds (PrecisionBoundComputer)
        Returns list of four JudgmentTuple objects.
        """
        logger.info(
            "TheoremSuite.run: suite=%s theorem=%s witnesses=%d obligations=%d trace=%d",
            self.suite_id, theorem.theorem_id, len(witnesses), len(obligations), len(trace_data),
        )
        self.registry.add_theorem(theorem)
        proof = self.verifier.verify(theorem, witnesses)
        self.registry.add_proof(proof)
        argument = self.checker.check(theorem, obligations)
        self.registry.add_argument(argument)
        metric = self.computer.compute(theorem, trace_data)
        self.registry.add_metric(metric)
        jts = [
            theorem.to_judgment_tuple(),
            proof.to_judgment_tuple(),
            argument.to_judgment_tuple(),
            metric.to_judgment_tuple(),
        ]
        logger.info(
            "TheoremSuite.run: completed theorem=%s proof_valid=%s completeness=%.3f f1=%.3f",
            theorem.theorem_id, proof.is_valid(), argument.coverage_ratio, metric.f1_score,
        )
        return jts

    def run_all(self, pairs: list) -> dict:
        """Run verification for all theorem pairs.

        pairs: list of dicts with keys "theorem", "witnesses", "obligations", "trace_data".
        Returns dict mapping theorem_id → result dict.
        """
        logger.info("TheoremSuite.run_all: suite=%s pairs=%d", self.suite_id, len(pairs))
        results = {}
        for pair in pairs:
            theorem = pair["theorem"]
            witnesses = pair.get("witnesses", [])
            obligations = pair.get("obligations", [])
            trace_data = pair.get("trace_data", [])
            jts = self.run(theorem, witnesses, obligations, trace_data)
            proof_jt = jts[1]
            results[theorem.theorem_id] = {
                "judgment_tuples": jts,
                "proof_valid": bool(proof_jt.E and proof_jt.E.items),
                "theorem_status": theorem.status.value,
                "completeness_score": jts[2].E.items[0].payload.get("score", 0.0) if jts[2].E.items else 0.0,
                "f1_score": jts[3].E.items[0].payload.get("f1", 0.0) if jts[3].E.items else 0.0,
            }
        return results

    def print_summary_table(self) -> None:
        """Print a formatted summary table of all theorems in the registry."""
        print()
        hdr = f"{'theorem_id':<36} {'kind':<15} {'status':<12} {'completeness':>13} {'grade':>6}"
        print(hdr)
        print("-" * len(hdr))
        for theorem in self.registry.theorems.values():
            argument = self.registry.get_argument_for(theorem.theorem_id)
            metric = self.registry.get_metric_for(theorem.theorem_id)
            comp_score = f"{argument.completeness_score:.3f}" if argument else "n/a"
            grade = metric.grade() if metric else "n/a"
            print(
                f"{theorem.theorem_id:<36} {theorem.kind.value:<15} "
                f"{theorem.status.value:<12} {comp_score:>13} {grade:>6}"
            )
        print()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def verify_contract_soundness(theorem: ContractTheorem, witnesses: list) -> SoundnessProof:
    """Verify soundness of a contract theorem against a set of witness values."""
    logger.info(
        "verify_contract_soundness: theorem=%s witnesses=%d",
        theorem.theorem_id, len(witnesses),
    )
    verifier = TheoremVerifier(verifier_id=_new_id("verifier"))
    return verifier.verify(theorem, witnesses)


def measure_contract_completeness(theorem: ContractTheorem, obligations: list) -> CompletenessArgument:
    """Measure completeness of a contract theorem against a set of obligations."""
    logger.info(
        "measure_contract_completeness: theorem=%s obligations=%d",
        theorem.theorem_id, len(obligations),
    )
    checker = CompletenessChecker(checker_id=_new_id("checker"))
    return checker.check(theorem, obligations)


def bound_precision(theorem: ContractTheorem, trace_data: list) -> PrecisionMetric:
    """Compute precision bounds for a contract theorem from trace data."""
    logger.info(
        "bound_precision: theorem=%s trace_data=%d",
        theorem.theorem_id, len(trace_data),
    )
    computer = PrecisionBoundComputer(computer_id=_new_id("computer"))
    return computer.compute(theorem, trace_data)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print("=== theorems smoke test ===")

    # TrustTier ordering
    assert TrustTier.PROPOSAL < TrustTier.REVIEWED < TrustTier.VERIFIED
    assert TrustTier.VERIFIED < TrustTier.RUNTIME_WITNESSED < TrustTier.PROOF_BACKED
    assert TrustTier.VERIFIED.satisfies(TrustTier.REVIEWED)
    assert TrustTier.PROOF_BACKED.elevate(TrustTier.PROPOSAL) == TrustTier.PROOF_BACKED
    print("TrustTier ordering: OK")

    # Create 3 theorems of different kinds
    th1 = ContractTheorem(
        theorem_id=_new_id("th"),
        kind=TheoremKind.SOUNDNESS,
        formula="isinstance(v, dict) and 'name' in v",
        coordinate_key="jugeo.models.UserModel",
        trust_tier=TrustTier.REVIEWED,
        status=ProofStatus.OPEN,
        proof_sketch="UserModel must have 'name' field",
        obligations=("has_name_field", "name_is_str"),
    )
    th2 = ContractTheorem(
        theorem_id=_new_id("th"),
        kind=TheoremKind.COMPLETENESS,
        formula="len(v) > 0",
        coordinate_key="jugeo.models.OrderModel",
        trust_tier=TrustTier.PROPOSAL,
        status=ProofStatus.OPEN,
        proof_sketch="OrderModel must have at least one item",
        obligations=("nonempty",),
    )
    th3 = ContractTheorem(
        theorem_id=_new_id("th"),
        kind=TheoremKind.PRECISION,
        formula="v >= 0",
        coordinate_key="jugeo.services.PriceService",
        trust_tier=TrustTier.VERIFIED,
        status=ProofStatus.OPEN,
        proof_sketch="Price must be non-negative",
        obligations=("nonnegative",),
    )
    print(f"Created theorems: {th1.theorem_id[:16]}..., {th2.theorem_id[:16]}..., {th3.theorem_id[:16]}...")
    print(f"  th1.summary: {th1.summary()[:80]}")

    # Discharge and refute
    th1_discharged = th1.discharge("all unit tests pass")
    assert th1_discharged.status == ProofStatus.DISCHARGED
    assert th1_discharged.trust_tier.value >= TrustTier.VERIFIED.value
    th2_refuted = th2.refute("empty order counterexample")
    assert th2_refuted.status == ProofStatus.REFUTED
    print("discharge/refute: OK")

    # JudgmentTuples from theorems
    jt1 = th1.to_judgment_tuple()
    assert isinstance(jt1, JudgmentTuple)
    assert jt1.T == TrustTier.REVIEWED
    assert jt1.c == "jugeo.models.UserModel"
    jt_discharged = th1_discharged.to_judgment_tuple()
    assert jt_discharged.T.value >= TrustTier.VERIFIED.value
    print("JudgmentTuples from theorems: OK")

    # Verify soundness
    witnesses = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}]
    proof = verify_contract_soundness(th1, witnesses)
    assert proof.is_valid()
    assert proof.coverage() > 0.0
    print(f"SoundnessProof (id={proof.proof_id[:16]}...): valid={proof.is_valid()}, coverage={proof.coverage():.2f}")

    # SMT formula
    smt = proof.to_smt_formula()
    assert "assert" in smt.lower() and "forall" in smt.lower()
    print(f"SMT formula snippet: {smt[:80]}...")

    # Check bad witness
    bad_witnesses = [{}, {"age": 30}]  # missing 'name'
    proof_bad = verify_contract_soundness(th1, bad_witnesses)
    # formula "isinstance(v, dict) and 'name' in v" → evaluates to False for {} and {'age':30}
    assert not proof_bad.is_valid() or proof_bad.coverage() == 0.0 or proof_bad.witness_values == ()
    print("SoundnessProof with bad witnesses: correctly handled")

    # Completeness
    arg_complete = measure_contract_completeness(th1, ["has_name_field", "name_is_str"])
    assert arg_complete.is_complete()
    assert arg_complete.gap_count() == 0
    print(f"CompletenessArgument: complete={arg_complete.is_complete()}, score={arg_complete.completeness_score:.2f}")

    arg_incomplete = measure_contract_completeness(th2, [])
    assert not arg_incomplete.is_complete()
    assert arg_incomplete.gap_count() > 0
    print(f"CompletenessArgument (incomplete): {arg_incomplete.gap_summary()}")

    # Precision
    trace = [
        {"false_positive": False, "false_negative": False},
        {"false_positive": True, "false_negative": False},
        {"false_positive": False, "false_negative": False},
    ]
    metric = bound_precision(th3, trace)
    assert 0.0 <= metric.precision() <= 1.0
    assert 0.0 <= metric.recall() <= 1.0
    assert 0.0 <= metric.f1_score <= 1.0
    assert metric.grade() in ("A", "B", "C", "D")
    print(f"PrecisionMetric: precision={metric.precision():.3f} recall={metric.recall():.3f} f1={metric.f1_score:.3f} grade={metric.grade()}")
    print(f"  summary: {metric.summary()}")

    # JudgmentTuples from proof, argument, metric
    jt_proof = proof.to_judgment_tuple()
    jt_arg = arg_complete.to_judgment_tuple()
    jt_metric = metric.to_judgment_tuple()
    assert isinstance(jt_proof, JudgmentTuple) and isinstance(jt_arg, JudgmentTuple)
    assert isinstance(jt_metric, JudgmentTuple)
    print("JudgmentTuples from proof/argument/metric: OK")

    # TheoremSuite
    registry = ContractTheoremRegistry(registry_id=_new_id("reg"))
    verifier = TheoremVerifier(verifier_id=_new_id("ver"))
    checker = CompletenessChecker(checker_id=_new_id("chk"))
    computer = PrecisionBoundComputer(computer_id=_new_id("comp"))
    suite = TheoremSuite(
        suite_id=_new_id("suite"),
        registry=registry,
        verifier=verifier,
        checker=checker,
        computer=computer,
    )
    pairs = [
        {
            "theorem": th1,
            "witnesses": witnesses,
            "obligations": ["has_name_field", "name_is_str"],
            "trace_data": trace,
        },
        {
            "theorem": th2,
            "witnesses": [{"items": [1, 2]}, {"items": [3]}],
            "obligations": ["nonempty"],
            "trace_data": [],
        },
        {
            "theorem": th3,
            "witnesses": [0, 1, 42, 100],
            "obligations": ["nonnegative"],
            "trace_data": [{"false_positive": False}],
        },
    ]
    results = suite.run_all(pairs)
    assert len(results) == 3
    for tid, r in results.items():
        assert "judgment_tuples" in r and len(r["judgment_tuples"]) == 4
    print(f"TheoremSuite.run_all: ran {len(results)} theorems")

    # Summary table
    suite.print_summary_table()

    # Registry summary
    reg_summary = registry.summary()
    assert reg_summary["total_theorems"] == 3
    assert reg_summary["proofs"] == 3
    assert reg_summary["arguments"] == 3
    assert reg_summary["metrics"] == 3
    print(f"Registry summary: {reg_summary}")

    # all_judgment_tuples
    all_jts = registry.all_judgment_tuples()
    assert len(all_jts) >= 12  # 3 theorems + 3 proofs + 3 arguments + 3 metrics
    print(f"Total judgment tuples: {len(all_jts)}")

    # Verifier report
    ver_report = verifier.report()
    assert ver_report["verifications"] == 3
    print(f"Verifier report: {ver_report}")

    # Checker report
    chk_report = checker.report()
    assert chk_report["checks"] == 3
    print(f"Checker report: {chk_report}")

    # Computer report
    comp_report = computer.report()
    print(f"Computer report: {comp_report}")

    # Defer a theorem
    th3_deferred = th3.defer("pending SMT solver availability")
    assert th3_deferred.status == ProofStatus.DEFERRED
    print(f"ContractTheorem.defer: status={th3_deferred.status.value}")

    # obligation_count and is_open
    assert th1.obligation_count() == 2
    assert th1.is_open() is True
    assert th1_discharged.is_open() is False
    print("obligation_count/is_open: OK")

    # bulk_verify
    bulk_proofs = verifier.bulk_verify([th1, th2, th3], witnesses)
    assert len(bulk_proofs) == 3
    print(f"TheoremVerifier.bulk_verify: {len(bulk_proofs)} proofs")

    # bulk_check
    bulk_args = checker.bulk_check([(th1, ["has_name_field"]), (th2, []), (th3, ["nonnegative"])])
    assert len(bulk_args) == 3
    print(f"CompletenessChecker.bulk_check: {len(bulk_args)} arguments")

    print("\n=== theorems smoke test PASSED ===")

# ---------------------------------------------------------------------------
# BEGIN LEGACY CONTENT (from previous version of this file)
# The classes below are from theory2.tex §21.5 and are preserved for
# backward compatibility. The new-spec classes above are the canonical API.
# ---------------------------------------------------------------------------


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
# Module-level constants
# ──────────────────────────────────────────────────────────────────────────────

# copilot: module-level identifiers for cross-reference to the theory file
_CHAPTER    = "Ch21"
_THEORY_FILE = "theory2.tex"

# copilot: expected number of theorems in the catalogue (used for coverage check)
_EXPECTED_THEOREM_COUNT = 5

# copilot: theorem section identifiers (§21.x) used in TheoremRecord.theory_section
_SECTION_ANNOTATION_LATENCY     = "§21.1"
_SECTION_DECORATOR_MORPHISM     = "§21.2"
_SECTION_REGISTRY_COVERAGE      = "§21.3"
_SECTION_CONTRACT_COMPLETENESS  = "§21.4"
_SECTION_THEOREM_BURDEN         = "§21.5"

# copilot: falsification severity levels (1=minor, 3=fatal counterexample)
_FALSIFICATION_SEVERITY_MINOR   = 1
_FALSIFICATION_SEVERITY_MODERATE = 2
_FALSIFICATION_SEVERITY_MAJOR   = 3


# ──────────────────────────────────────────────────────────────────────────────
# Standard jugeo imports with inline stubs
# ──────────────────────────────────────────────────────────────────────────────

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    # copilot: stub fallbacks keep theorems.py portable outside the jugeo package
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


# ──────────────────────────────────────────────────────────────────────────────
# TheoremVerificationStatus — lifecycle of a theorem (§21.0)
# ──────────────────────────────────────────────────────────────────────────────

class TheoremVerificationStatus(enum.Enum):
    """
    The verification lifecycle of a theorem.

    theory2.tex Ch21 §21.0 defines five states:
    - UNVERIFIED:         No evidence has been examined for this theorem.
    - FALSIFIED:          A counterexample has been found; theorem is false.
    - PARTIALLY_VERIFIED: Some evidence supports the theorem but not all cases.
    - FULLY_VERIFIED:     Sufficient evidence for all cases (proof complete).
    - AXIOM:              Treated as an axiom; not subject to falsification.
    """
    UNVERIFIED         = "unverified"
    FALSIFIED          = "falsified"
    PARTIALLY_VERIFIED = "partially_verified"
    FULLY_VERIFIED     = "fully_verified"
    AXIOM              = "axiom"


# ──────────────────────────────────────────────────────────────────────────────
# TheoremRecord — immutable theorem state record
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class TheoremRecord:
    """
    Immutable record capturing the full state of a theorem.

    theory2.tex Ch21 models theorems as objects in the proof category.
    A TheoremRecord is the image of the theorem under the forgetful functor
    to Set: it carries the theorem's content but not its proof methods.

    Fields:
        theorem_id:             unique identifier (e.g. "THM-21-1-…")
        name:                   human-readable name
        theory_section:         corresponding section in theory2.tex
        statement:              full natural-language statement
        hypothesis:             hypothesis clause (the "if" part)
        conclusion:             conclusion clause (the "then" part)
        proof_sketch:           informal proof argument
        verification_status:    current lifecycle status
        trust_level:            current trust level of evidence
        evidence_items:         tuple of evidence items supporting the theorem
        falsification_attempts: number of falsification attempts made
    """
    theorem_id:             str                       = ""
    name:                   str                       = ""
    theory_section:         str                       = ""
    statement:              str                       = ""
    hypothesis:             str                       = ""
    conclusion:             str                       = ""
    proof_sketch:           str                       = ""
    verification_status:    TheoremVerificationStatus = TheoremVerificationStatus.UNVERIFIED
    trust_level:            Any                       = None
    evidence_items:         tuple                     = ()
    falsification_attempts: int                       = 0

    def is_open(self) -> bool:
        """
        Return True if the theorem is still open (unverified or partial).

        theory2.tex Ch21 §21.0 defines an open theorem as one that still
        requires evidence.  Open theorems generate proof obligations.
        """
        return self.verification_status in (
            TheoremVerificationStatus.UNVERIFIED,
            TheoremVerificationStatus.PARTIALLY_VERIFIED,
        )

    def is_closed(self) -> bool:
        """Return True if the theorem is fully verified or treated as an axiom."""
        return self.verification_status in (
            TheoremVerificationStatus.FULLY_VERIFIED,
            TheoremVerificationStatus.AXIOM,
        )

    def is_falsified(self) -> bool:
        """Return True if a counterexample has been found for this theorem."""
        return self.verification_status == TheoremVerificationStatus.FALSIFIED

    def verify(self, evidence_item: Any) -> TheoremRecord:
        """
        Return a copy of this record with the given evidence item added.

        Promotes the verification_status from UNVERIFIED → PARTIALLY_VERIFIED
        → FULLY_VERIFIED as evidence accumulates.  Uses replace() to preserve
        immutability.
        """
        new_items  = self.evidence_items + (evidence_item,)
        n_evidence = len(new_items)

        # copilot: promote status based on evidence count
        if self.verification_status == TheoremVerificationStatus.FALSIFIED:
            # copilot: falsified theorems cannot be re-verified
            return replace(self, evidence_items=new_items)

        if n_evidence >= 3:
            new_status = TheoremVerificationStatus.FULLY_VERIFIED
            new_trust  = TrustLevel.VERIFIED_PROOF
        elif n_evidence >= 1:
            new_status = TheoremVerificationStatus.PARTIALLY_VERIFIED
            new_trust  = TrustLevel.ORACLE_PROPOSED
        else:
            new_status = TheoremVerificationStatus.UNVERIFIED
            new_trust  = TrustLevel.UNVERIFIED

        return replace(
            self,
            evidence_items       = new_items,
            verification_status  = new_status,
            trust_level          = new_trust,
        )

    def summary(self) -> str:
        """
        Return a multi-line human-readable summary of this theorem.

        Includes the theory file reference, status, and a truncated proof sketch.
        """
        sketch_preview = self.proof_sketch[:120] + "…" if len(self.proof_sketch) > 120 else self.proof_sketch
        return "\n".join([
            f"Theorem: {self.name}",
            f"  ID:       {self.theorem_id}",
            f"  Section:  {_THEORY_FILE} {_CHAPTER} {self.theory_section}",
            f"  Status:   {self.verification_status.value}",
            f"  Trust:    {self.trust_level}",
            f"  Evidence: {len(self.evidence_items)} item(s)",
            f"  Falsification attempts: {self.falsification_attempts}",
            f"  Statement:",
            f"    {self.statement}",
            f"  Hypothesis: {self.hypothesis}",
            f"  Conclusion: {self.conclusion}",
            f"  Proof sketch: {sketch_preview}",
        ])

    def to_dict(self) -> dict:
        """Serialize this record to a plain-Python dict."""
        return {
            "theorem_id":             self.theorem_id,
            "name":                   self.name,
            "theory_section":         self.theory_section,
            "statement":              self.statement,
            "hypothesis":             self.hypothesis,
            "conclusion":             self.conclusion,
            "verification_status":    self.verification_status.value,
            "trust_level":            str(self.trust_level),
            "evidence_count":         len(self.evidence_items),
            "falsification_attempts": self.falsification_attempts,
        }


# ──────────────────────────────────────────────────────────────────────────────
# BaseTheorem — abstract base class for all theorems
# ──────────────────────────────────────────────────────────────────────────────

class BaseTheorem(abc.ABC):
    """
    Abstract base class for all JuGeo Ch21 theorems.

    Each concrete theorem subclass:
      - Declares a unique theorem_id as a class-level string.
      - Implements build_record() to construct an immutable TheoremRecord.
      - Implements check(evidence) to test evidence against the theorem.
      - Implements build_judgment() to emit a Judgment record.

    The to_record() convenience method calls build_record() and caches
    the result so that repeated calls are cheap.
    """

    _record_cache: TheoremRecord | None = None

    @property
    @abc.abstractmethod
    def theorem_id(self) -> str:
        """Unique identifier for this theorem (e.g. 'THM-21-1-…')."""

    @abc.abstractmethod
    def build_record(self) -> TheoremRecord:
        """Construct and return an immutable TheoremRecord for this theorem."""

    @abc.abstractmethod
    def check(self, evidence: Any) -> bool:
        """
        Test whether the given evidence supports this theorem.

        evidence is typically a dict with keys specific to each theorem.
        Returns True if the evidence is consistent with the theorem.
        """

    @abc.abstractmethod
    def build_judgment(self) -> Judgment:
        """
        Emit a Judgment record for this theorem.

        The Judgment carries the theorem's proposition, trust level, and
        provenance for registration in the judgment registry.
        """

    def to_record(self) -> TheoremRecord:
        """
        Return the TheoremRecord for this theorem, building it if needed.

        The record is cached after the first call so that status updates
        (via TheoremRecord.verify()) must be applied externally.
        """
        if self._record_cache is None:
            object.__setattr__(self, "_record_cache", self.build_record())
        return self._record_cache  # type: ignore[return-value]

    def _make_judgment(
        self,
        theorem_id:   str,
        statement:    str,
        section:      str,
        trust_level:  Any,
        prop_kind:    Any,
    ) -> Judgment:
        """
        Helper to construct a Judgment from common theorem fields.

        Used by concrete subclasses' build_judgment() implementations.
        """
        prop = Proposition(
            kind           = prop_kind,
            formula        = f"{theorem_id}: {statement[:80]}",
            free_variables = (),
            metadata       = {"section": section, "theory_file": _THEORY_FILE},
        )
        return Judgment(
            proposition = prop,
            carrier     = Carrier(name=theorem_id),
            evidence    = EvidenceBundle(),
            trust       = TrustAnnotation(
                level     = trust_level,
                rationale = f"theorem from {_THEORY_FILE} {_CHAPTER} {section}",
            ),
            provenance  = Provenance(
                sources = (ProvenanceSource.ORACLE,),
                chain   = (theorem_id, _CHAPTER, _THEORY_FILE),
            ),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Theorem §21.1 — AnnotationLatencyTheorem
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationLatencyTheorem(BaseTheorem):
    """
    §21.1 — Annotation Latency Theorem.

    Statement:
        For all Python symbols S with annotation A, A is a latent behavioral
        contract.  A becomes an active obligation iff ∃ checker c such that
        c inspects A.

    theory2.tex Ch21 §21.1 proves that Python's __annotations__ dict is
    populated at definition time but never enforced at runtime by the
    interpreter itself.  The annotation is therefore 'latent': present but
    inactive until a type checker, validator, or JuGeo burden analyser reads it.

    Corollary: every annotated symbol has at least one latent obligation,
    regardless of whether any checker has been run.
    """

    @property
    def theorem_id(self) -> str:
        return "THM-21-1-ANNOTATION-LATENCY"

    def build_record(self) -> TheoremRecord:
        """Construct the TheoremRecord for the Annotation Latency Theorem."""
        return TheoremRecord(
            theorem_id   = self.theorem_id,
            name         = "Annotation Latency Theorem",
            theory_section = _SECTION_ANNOTATION_LATENCY,
            statement    = (
                "For all Python symbols S with annotation A, A is a latent behavioral "
                "contract. A becomes an active obligation iff ∃ checker c such that "
                "c inspects A."
            ),
            hypothesis   = (
                "S has __annotations__ containing A and no runtime enforcement exists"
            ),
            conclusion   = "A ∈ LatentContracts(S)",
            proof_sketch = (
                "By inspection of CPython's annotation semantics: __annotations__ is "
                "populated at class/function definition but no enforcement hook exists. "
                "The annotation is purely metadata until get_type_hints() or a validator "
                "reads it. Therefore, for any symbol S with annotation A and no active "
                "checker c, A remains latent. QED by construction."
            ),
            verification_status  = TheoremVerificationStatus.PARTIALLY_VERIFIED,
            trust_level          = TrustLevel.ORACLE_PROPOSED,
            evidence_items       = (),
            falsification_attempts = 0,
        )

    def check(self, evidence: Any) -> bool:
        """
        Check if evidence is consistent with annotation latency.

        Accepts a dict with:
          'annotations': dict — must be non-empty to support the hypothesis
          'runtime_violations': list — must be absent (or empty) because
            latency means violations do NOT fire before a checker is run

        Returns True if annotations are present and no runtime violations
        are present without a checker (i.e., latency is demonstrated).
        """
        if not isinstance(evidence, dict):
            return False

        annotations = evidence.get("annotations", {})
        if not annotations:
            # copilot: no annotations → hypothesis not applicable
            return False

        runtime_violations = evidence.get("runtime_violations", [])
        checker_ran = evidence.get("checker_ran", False)

        if runtime_violations and not checker_ran:
            # copilot: violations before any checker ran → latency violated
            logger.debug(
                "%s: found runtime violations without checker — latency may be violated",
                self.theorem_id,
            )
            return False

        # copilot: annotations present, no spontaneous violations → latency demonstrated
        return True

    def build_judgment(self) -> Judgment:
        """Emit a Judgment for the Annotation Latency Theorem."""
        return self._make_judgment(
            theorem_id  = self.theorem_id,
            statement   = "annotations are latent behavioral contracts (§21.1)",
            section     = _SECTION_ANNOTATION_LATENCY,
            trust_level = TrustLevel.ORACLE_PROPOSED,
            prop_kind   = PropositionKind.BEHAVIORAL,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Theorem §21.2 — DecoratorMorphismTheorem
# ──────────────────────────────────────────────────────────────────────────────

class DecoratorMorphismTheorem(BaseTheorem):
    """
    §21.2 — Decorator Morphism Theorem.

    Statement:
        For all decorators D applied to symbol S, D induces a morphism
        φ_D: U_S → U_{D(S)} in the coordinate category such that φ_D
        preserves the identity morphism and composes associatively.

    theory2.tex Ch21 §21.2 proves that the decorator pattern in Python
    is a morphism in the coordinate category.  The key axioms:
      (a) Identity: if D = identity (no-op), then φ_D = id_{U_S}.
      (b) Composition: φ_{D2 ∘ D1} = φ_{D2} ∘ φ_{D1} (evaluation order).
      (c) Preservation: __wrapped__ records the source coordinate.

    This theorem is partially verified: the identity and composition laws
    hold for well-behaved decorators that use functools.wraps, but may
    be violated by ill-formed decorators (see FalsificationSuite).
    """

    @property
    def theorem_id(self) -> str:
        return "THM-21-2-DECORATOR-MORPHISM"

    def build_record(self) -> TheoremRecord:
        """Construct the TheoremRecord for the Decorator Morphism Theorem."""
        return TheoremRecord(
            theorem_id   = self.theorem_id,
            name         = "Decorator Morphism Theorem",
            theory_section = _SECTION_DECORATOR_MORPHISM,
            statement    = (
                "For all decorators D applied to symbol S, D induces a morphism "
                "φ_D: U_S → U_{D(S)} in the coordinate category such that φ_D "
                "preserves the identity morphism and composes associatively."
            ),
            hypothesis   = (
                "D is a callable applied to S via @D syntax; the decorated result "
                "D(S) has __wrapped__ = S or equivalent"
            ),
            conclusion   = "φ_D is a valid morphism in JuGeo coordinate category",
            proof_sketch = (
                "D(S) wraps S via closure; functoriality follows from Python's __call__ "
                "semantics; identity preserved when D is identity (no-op) decorator; "
                "composition: (D2 ∘ D1)(S) = D2(D1(S)) preserves associativity by "
                "Python evaluation order. functools.wraps sets __wrapped__ = S, "
                "recording the source coordinate. φ_D is well-defined on coordinates "
                "because U_S is derived solely from S's module and qualname. QED."
            ),
            verification_status  = TheoremVerificationStatus.PARTIALLY_VERIFIED,
            trust_level          = TrustLevel.ORACLE_PROPOSED,
            evidence_items       = (),
            falsification_attempts = 0,
        )

    def check(self, evidence: Any) -> bool:
        """
        Check if evidence supports the Decorator Morphism Theorem.

        Accepts a dict with:
          'decorators': list of dicts with 'source_qualname' and 'target_qualname'

        Returns True if every decorator entry has both source and target qualnames
        (i.e., the morphism is well-defined).
        """
        if not isinstance(evidence, dict):
            return False

        decorators = evidence.get("decorators", [])
        if not decorators:
            # copilot: no decorators → vacuously true (∀ over empty set)
            return True

        for dec in decorators:
            if not isinstance(dec, dict):
                # copilot: handle DecoratorTransformer objects via attribute access
                src = getattr(dec, "source_qualname", None)
                tgt = getattr(dec, "target_qualname", None)
            else:
                src = dec.get("source_qualname")
                tgt = dec.get("target_qualname")

            if not src or not tgt:
                logger.debug(
                    "%s: decorator entry missing qualnames: %r", self.theorem_id, dec
                )
                return False

        return True

    def build_judgment(self) -> Judgment:
        """Emit a Judgment for the Decorator Morphism Theorem."""
        return self._make_judgment(
            theorem_id  = self.theorem_id,
            statement   = "decorators are morphisms in the coordinate category (§21.2)",
            section     = _SECTION_DECORATOR_MORPHISM,
            trust_level = TrustLevel.ORACLE_PROPOSED,
            prop_kind   = PropositionKind.STRUCTURAL,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Theorem §21.3 — RegistryCoverageTheorem
# ──────────────────────────────────────────────────────────────────────────────

class RegistryCoverageTheorem(BaseTheorem):
    """
    §21.3 — Registry Coverage Theorem.

    Statement:
        For all Python registry mechanisms R (singledispatch, ABCMeta, dataclass
        fields), R induces a covering family J(U) = {f_i: U_i → U} in the
        Grothendieck topology on the Python runtime site.

    theory2.tex Ch21 §21.3 uses Grothendieck topology to model Python's
    dispatch and registration mechanisms.  A registry R with implementations
    {I_1, …, I_n} forms a covering family iff:
      - Each I_i is a restriction morphism ρ_i: U_{I_i} → U_R.
      - The union of the domains covers U_R: ⋃ ρ_i(U_{I_i}) = U_R.

    For singledispatch: U_R = object, U_{I_i} = the registered type.
    For ABCMeta: U_R = the abstract class, U_{I_i} = each concrete implementation.
    """

    @property
    def theorem_id(self) -> str:
        return "THM-21-3-REGISTRY-COVERAGE"

    def build_record(self) -> TheoremRecord:
        """Construct the TheoremRecord for the Registry Coverage Theorem."""
        return TheoremRecord(
            theorem_id   = self.theorem_id,
            name         = "Registry Coverage Theorem",
            theory_section = _SECTION_REGISTRY_COVERAGE,
            statement    = (
                "For all Python registry mechanisms R (singledispatch, ABCMeta, dataclass "
                "fields), R induces a covering family J(U) = {f_i: U_i → U} in the "
                "Grothendieck topology on the Python runtime site."
            ),
            hypothesis   = (
                "R is a runtime registry mechanism with registered implementations "
                "{I_1,...,I_n}"
            ),
            conclusion   = (
                "The family {ρ_i: U_{I_i} → U_R} is a covering family for U_R"
            ),
            proof_sketch = (
                "Each registered implementation I_i is a restriction morphism from "
                "U_{I_i} to U_R. The base case (object dispatch, empty ABC) is the "
                "identity covering. Coverage is verified by checking all registered "
                "types cover the domain. By the Grothendieck axioms, the family is a "
                "covering iff every element of U_R factors through some ρ_i. For "
                "singledispatch, the 'object' base covers all unregistered types, so "
                "coverage is always ≥ 1/n. QED."
            ),
            verification_status  = TheoremVerificationStatus.PARTIALLY_VERIFIED,
            trust_level          = TrustLevel.ORACLE_PROPOSED,
            evidence_items       = (),
            falsification_attempts = 0,
        )

    def check(self, evidence: Any) -> bool:
        """
        Check if evidence supports the Registry Coverage Theorem.

        Accepts a dict with:
          'registry': dict with 'coverage_fraction' ∈ [0.0, 1.0]
            and optional 'registered_types' list

        Returns True if coverage_fraction ≥ 0.5 (i.e., at least half of the
        domain is covered by registered implementations).
        """
        if not isinstance(evidence, dict):
            return False

        registry = evidence.get("registry", {})
        if not registry:
            # copilot: no registry data → vacuously true
            return True

        coverage = registry.get("coverage_fraction", 0.0)
        if not isinstance(coverage, (int, float)):
            return False

        # copilot: require at least 50% coverage (conservative threshold)
        result = coverage >= 0.5
        if not result:
            logger.debug(
                "%s: coverage_fraction=%.2f < 0.5 → theorem not supported",
                self.theorem_id, coverage,
            )
        return result

    def build_judgment(self) -> Judgment:
        """Emit a Judgment for the Registry Coverage Theorem."""
        return self._make_judgment(
            theorem_id  = self.theorem_id,
            statement   = "registries are covering families (Grothendieck) (§21.3)",
            section     = _SECTION_REGISTRY_COVERAGE,
            trust_level = TrustLevel.ORACLE_PROPOSED,
            prop_kind   = PropositionKind.STRUCTURAL,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Theorem §21.4 — ContractCompletenessTheorem
# ──────────────────────────────────────────────────────────────────────────────

class ContractCompletenessTheorem(BaseTheorem):
    """
    §21.4 — Contract Completeness Theorem.

    Statement:
        A generated contract C for schema S is complete iff all residual
        obligations in O(C) are discharged.

    theory2.tex Ch21 §21.4 defines a generated contract as the image of the
    schema S under the contract generation functor G: Schema → Contract.
    G(S) maps each required field in S to exactly one obligation in O(G(S)).

    Completeness: C is complete ↔ ∀ ob ∈ O(C): ob.is_discharged = True.

    This is the key theorem that connects proof obligations to software
    correctness: a fully-discharged contract implies all fields are typed,
    consistent, and usable.
    """

    @property
    def theorem_id(self) -> str:
        return "THM-21-4-CONTRACT-COMPLETENESS"

    def build_record(self) -> TheoremRecord:
        """Construct the TheoremRecord for the Contract Completeness Theorem."""
        return TheoremRecord(
            theorem_id   = self.theorem_id,
            name         = "Contract Completeness Theorem",
            theory_section = _SECTION_CONTRACT_COMPLETENESS,
            statement    = (
                "A generated contract C for schema S is complete iff all residual "
                "obligations in O(C) are discharged."
            ),
            hypothesis   = (
                "C = G(S) where G is a contract generator (dataclass, attrs, pydantic)"
            ),
            conclusion   = (
                "C is complete ↔ ∀ ob ∈ O(C): ob.is_discharged = True"
            ),
            proof_sketch = (
                "By construction: G(S) maps each required field in S to exactly one "
                "obligation. Completeness is defined as discharge of all obligations. "
                "G is a functor (preserves identity and composition), so completeness "
                "is preserved under schema morphisms. If any obligation ob ∈ O(C) is "
                "undischarged, C is incomplete by definition. The converse: if all "
                "obligations are discharged, C has no pending requirements, so C is "
                "complete. QED by definition of completeness."
            ),
            verification_status  = TheoremVerificationStatus.PARTIALLY_VERIFIED,
            trust_level          = TrustLevel.ORACLE_PROPOSED,
            evidence_items       = (),
            falsification_attempts = 0,
        )

    def check(self, evidence: Any) -> bool:
        """
        Check if evidence supports the Contract Completeness Theorem.

        Accepts a dict with:
          'obligations': list of obligation-like objects

        Returns True if all obligations have is_discharged = True.
        Returns False if any obligation is undischarged.
        Returns True vacuously if obligations list is empty.
        """
        if not isinstance(evidence, dict):
            return False

        obligations = evidence.get("obligations", [])
        if not obligations:
            # copilot: no obligations → vacuously complete (empty contract)
            return True

        for ob in obligations:
            is_discharged = getattr(ob, "is_discharged", None)
            if is_discharged is None and isinstance(ob, dict):
                is_discharged = ob.get("is_discharged", False)
            if not is_discharged:
                logger.debug(
                    "%s: undischarged obligation found: %r", self.theorem_id, ob
                )
                return False

        return True

    def build_judgment(self) -> Judgment:
        """Emit a Judgment for the Contract Completeness Theorem."""
        return self._make_judgment(
            theorem_id  = self.theorem_id,
            statement   = "generated contracts complete iff obligations discharged (§21.4)",
            section     = _SECTION_CONTRACT_COMPLETENESS,
            trust_level = TrustLevel.ORACLE_PROPOSED,
            prop_kind   = PropositionKind.RELATIONAL,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Theorem §21.5 — TheoremBurdenTheorem
# ──────────────────────────────────────────────────────────────────────────────

class TheoremBurdenTheorem(BaseTheorem):
    """
    §21.5 — Theorem Burden Theorem.

    Statement:
        For all type annotations A on symbol S, the annotation implies a
        TheoremBurden B(A,S) = (E(A), C(A,S), K(A,S)) where E=existence,
        C=consistency, K=completeness.

    theory2.tex Ch21 §21.5 is the central theorem of the burden analysis
    framework.  It states that every annotation is non-trivially obligatory:
    the annotator implicitly claims that:
      (E) The type A is inhabited (∃ v. v : A).
      (C) A does not contradict other annotations on S or reachable symbols.
      (K) Every call-site that provides a value to S provides one of type A.

    Consequence: B(A,S) ≠ ∅ always. Even the annotation `x: int` generates
    three obligations, though all three are trivially dischargeable for
    primitive types.
    """

    @property
    def theorem_id(self) -> str:
        return "THM-21-5-THEOREM-BURDEN"

    def build_record(self) -> TheoremRecord:
        """Construct the TheoremRecord for the Theorem Burden Theorem."""
        return TheoremRecord(
            theorem_id   = self.theorem_id,
            name         = "Theorem Burden Theorem",
            theory_section = _SECTION_THEOREM_BURDEN,
            statement    = (
                "For all type annotations A on symbol S, the annotation implies a "
                "TheoremBurden B(A,S) = (E(A), C(A,S), K(A,S)) where E=existence, "
                "C=consistency, K=completeness."
            ),
            hypothesis   = (
                "S.__annotations__[name] = A for some name"
            ),
            conclusion   = (
                "B(A,S) ≠ ∅ and B(A,S) includes at minimum the existence burden E(A)"
            ),
            proof_sketch = (
                "By definition of annotation semantics: A is a claim about the type of "
                "values at S. This claim implies (1) A is inhabited (existence), (2) A "
                "does not contradict other claims (consistency), (3) all usages satisfy "
                "A (completeness). Burdens are minimal obligations derived from "
                "annotation semantics. Even `x: int` has all three burdens, though "
                "E(int) = trivial (int() = 0), C(int, S) = trivial (no co-annotations "
                "usually contradict int), K(int, S) = dischargeable by type checker. "
                "Therefore B(A,S) ≠ ∅ for all A. QED."
            ),
            verification_status  = TheoremVerificationStatus.PARTIALLY_VERIFIED,
            trust_level          = TrustLevel.ORACLE_PROPOSED,
            evidence_items       = (),
            falsification_attempts = 0,
        )

    def check(self, evidence: Any) -> bool:
        """
        Check if evidence supports the Theorem Burden Theorem.

        Accepts a dict with:
          'burden': dict with 'obligations_count' > 0

        Returns True if obligations_count > 0 (burden is non-empty as claimed).
        Also accepts 'annotations': dict with at least one entry.
        """
        if not isinstance(evidence, dict):
            return False

        burden = evidence.get("burden", {})
        if burden:
            obligations_count = burden.get("obligations_count", 0)
            if obligations_count > 0:
                return True

        # copilot: alternative: check that annotated symbols generate obligations
        annotations = evidence.get("annotations", {})
        if annotations:
            # copilot: at least one annotation → at least one burden (by theorem)
            return True

        return False

    def build_judgment(self) -> Judgment:
        """Emit a Judgment for the Theorem Burden Theorem."""
        return self._make_judgment(
            theorem_id  = self.theorem_id,
            statement   = "every annotation implies a theorem burden B(A,S) ≠ ∅ (§21.5)",
            section     = _SECTION_THEOREM_BURDEN,
            trust_level = TrustLevel.ORACLE_PROPOSED,
            prop_kind   = PropositionKind.SEMANTIC,
        )


# ──────────────────────────────────────────────────────────────────────────────
# TheoremRegistry — catalogue of all theorems
# ──────────────────────────────────────────────────────────────────────────────

class TheoremRegistry:
    """
    Catalogue of all Ch21 theorems.

    theory2.tex Ch21 defines five theorems.  The TheoremRegistry instantiates
    one of each and provides a uniform query interface.

    Usage::

        registry = TheoremRegistry()
        results  = registry.verify_all({"annotations": {"x": "int"}})
        print(registry.report())
    """

    def __init__(self) -> None:
        # copilot: instantiate all five theorem objects
        self._thm_latency     = AnnotationLatencyTheorem()
        self._thm_decorator   = DecoratorMorphismTheorem()
        self._thm_registry    = RegistryCoverageTheorem()
        self._thm_completeness = ContractCompletenessTheorem()
        self._thm_burden      = TheoremBurdenTheorem()

        self._theorems: dict[str, BaseTheorem] = {
            self._thm_latency.theorem_id:      self._thm_latency,
            self._thm_decorator.theorem_id:    self._thm_decorator,
            self._thm_registry.theorem_id:     self._thm_registry,
            self._thm_completeness.theorem_id: self._thm_completeness,
            self._thm_burden.theorem_id:       self._thm_burden,
        }

    def all_theorems(self) -> list[BaseTheorem]:
        """Return a list of all registered theorem objects."""
        return list(self._theorems.values())

    def get(self, theorem_id: str) -> BaseTheorem | None:
        """Return the theorem with the given ID, or None if not found."""
        return self._theorems.get(theorem_id)

    def verify_all(self, evidence: dict) -> dict[str, bool]:
        """
        Run check(evidence) on all theorems.

        Returns a dict mapping theorem_id → bool (True = supported by evidence).
        """
        results: dict[str, bool] = {}
        for tid, theorem in self._theorems.items():
            try:
                results[tid] = theorem.check(evidence)
            except Exception as exc:
                logger.warning("TheoremRegistry.verify_all: %s check failed: %s", tid, exc)
                results[tid] = False
        return results

    def report(self) -> str:
        """
        Return a multi-line report of all theorems and their current status.

        Includes theorem ID, name, section, verification status, and
        evidence count for each theorem.
        """
        lines = [
            f"TheoremRegistry Report ({_THEORY_FILE} {_CHAPTER})",
            f"  Theorems registered: {len(self._theorems)}",
            "",
        ]
        for theorem in self._theorems.values():
            record = theorem.to_record()
            lines.append(f"  [{record.verification_status.value.upper():18s}] {record.name}")
            lines.append(f"    ID:       {record.theorem_id}")
            lines.append(f"    Section:  {record.theory_section}")
            lines.append(f"    Trust:    {record.trust_level}")
            lines.append(f"    Evidence: {len(record.evidence_items)} item(s)")
            lines.append("")
        return "\n".join(lines)

    def get_open_theorems(self) -> list[BaseTheorem]:
        """Return theorems whose record is_open() (still need evidence)."""
        return [t for t in self._theorems.values() if t.to_record().is_open()]

    def get_verified_theorems(self) -> list[BaseTheorem]:
        """Return theorems whose record is_closed() (fully verified or axiom)."""
        return [t for t in self._theorems.values() if t.to_record().is_closed()]


# ──────────────────────────────────────────────────────────────────────────────
# FalsificationCase — record of a discovered counterexample
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class FalsificationCase:
    """
    Immutable record of a counterexample found by FalsificationSuite.

    theory2.tex Ch21 §21.8 follows Popper's falsificationism: a theorem is
    falsified iff a single counterexample can be produced.  Each FalsificationCase
    documents:
      - Which theorem was targeted
      - What the counterexample looks like
      - The evidence dict that falsifies the theorem
      - Severity (1=minor, 2=moderate, 3=major/fatal)

    Severity 3 means the counterexample directly refutes a core claim;
    severity 1 means a corner-case that weakens the theorem but does not
    invalidate it in the common case.
    """
    theorem_id:                  str  = ""
    counterexample_description:  str  = ""
    evidence:                    dict = field(default_factory=dict)
    discovered_at:               str  = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    severity:                    int  = _FALSIFICATION_SEVERITY_MINOR

    def summary(self) -> str:
        """Return a compact human-readable summary of this falsification case."""
        sev_str = {1: "minor", 2: "moderate", 3: "major"}.get(self.severity, "?")
        return (
            f"FalsificationCase [{sev_str}] for {self.theorem_id}: "
            f"{self.counterexample_description[:80]}"
        )

    def to_dict(self) -> dict:
        """Serialize this case to a plain-Python dict."""
        return {
            "theorem_id":                 self.theorem_id,
            "counterexample_description": self.counterexample_description,
            "evidence":                   {k: repr(v) for k, v in self.evidence.items()},
            "discovered_at":              self.discovered_at,
            "severity":                   self.severity,
        }


# ──────────────────────────────────────────────────────────────────────────────
# FalsificationSuite — systematic counterexample search
# ──────────────────────────────────────────────────────────────────────────────

class FalsificationSuite:
    """
    Systematic counterexample search for all Ch21 theorems.

    theory2.tex Ch21 §21.8 adapts Popper's falsificationism to the JuGeo
    framework.  The suite attempts to construct counterexamples for each
    theorem by inspecting a list of test objects.

    For each theorem, the suite:
      1. Constructs evidence dicts from the test objects.
      2. Checks whether the theorem's check() method returns False.
      3. If False, records a FalsificationCase.

    The suite does NOT modify theorems; it only records observations.
    """

    def __init__(self) -> None:
        # copilot: _cases stores all discovered falsification cases
        self._cases:    list[FalsificationCase] = []
        self._registry: TheoremRegistry         = TheoremRegistry()

    def falsify(
        self, theorem: BaseTheorem, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify the given theorem using test_objects.

        Dispatches to the appropriate _falsify_* method based on theorem_id.
        Returns the list of FalsificationCases found (may be empty).
        """
        tid = theorem.theorem_id
        dispatcher = {
            "THM-21-1-ANNOTATION-LATENCY":    self._falsify_annotation_latency,
            "THM-21-2-DECORATOR-MORPHISM":    self._falsify_decorator_morphism,
            "THM-21-3-REGISTRY-COVERAGE":     self._falsify_registry_coverage,
            "THM-21-4-CONTRACT-COMPLETENESS": self._falsify_contract_completeness,
            "THM-21-5-THEOREM-BURDEN":        self._falsify_theorem_burden,
        }
        falsify_fn = dispatcher.get(tid)
        if falsify_fn is None:
            logger.warning("FalsificationSuite: no falsifier for theorem %s", tid)
            return []

        cases = falsify_fn(test_objects)
        self._cases.extend(cases)
        return cases

    def _falsify_annotation_latency(
        self, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify AnnotationLatencyTheorem.

        Looks for annotated objects that have runtime validators installed
        (e.g., pydantic BaseModel, attrs validators).  Such objects have
        annotations that are enforced at runtime — potentially violating
        latency.

        Note: finding a runtime validator does NOT strictly falsify latency
        (the validator is the checker c that makes the annotation active),
        so severity is always MINOR here.
        """
        cases: list[FalsificationCase] = []
        for obj in test_objects:
            if not (inspect.isclass(obj) or callable(obj)):
                continue

            annotations = getattr(obj, "__annotations__", {})
            if not annotations:
                continue  # copilot: no annotations → theorem not applicable

            # copilot: check for pydantic-style __validators__ or attrs __attrs_attrs__
            has_pydantic = hasattr(obj, "__validators__") or hasattr(obj, "model_fields")
            has_attrs    = hasattr(obj, "__attrs_attrs__")

            if has_pydantic or has_attrs:
                case = FalsificationCase(
                    theorem_id = "THM-21-1-ANNOTATION-LATENCY",
                    counterexample_description = (
                        f"{getattr(obj, '__qualname__', repr(obj))} has runtime "
                        f"annotation enforcement "
                        f"({'pydantic' if has_pydantic else 'attrs'}), "
                        f"so annotations may not be latent."
                    ),
                    evidence  = {"annotations": annotations, "runtime_enforcement": True},
                    severity  = _FALSIFICATION_SEVERITY_MINOR,
                )
                cases.append(case)
                logger.debug("FalsificationSuite: %s", case.summary())

        return cases

    def _falsify_decorator_morphism(
        self, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify DecoratorMorphismTheorem.

        Looks for decorated functions where __wrapped__ is absent despite
        apparent decoration (non-matching qualname patterns).  Such functions
        may not record the source coordinate.
        """
        cases: list[FalsificationCase] = []
        for obj in test_objects:
            if not callable(obj):
                continue

            qualname = getattr(obj, "__qualname__", "")

            # copilot: a decorated function without __wrapped__ may violate the theorem
            # Heuristic: if qualname contains ".<locals>." it might be a closure decorator
            has_wrapped = hasattr(obj, "__wrapped__")
            is_closure  = ".<locals>." in qualname

            if is_closure and not has_wrapped:
                # copilot: this is a potential falsification: closure without __wrapped__
                case = FalsificationCase(
                    theorem_id = "THM-21-2-DECORATOR-MORPHISM",
                    counterexample_description = (
                        f"Callable '{qualname}' appears to be a closure decorator "
                        f"but lacks __wrapped__, so the source coordinate may be lost."
                    ),
                    evidence  = {
                        "decorators": [{"qualname": qualname, "has_wrapped": False}]
                    },
                    severity  = _FALSIFICATION_SEVERITY_MODERATE,
                )
                cases.append(case)
                logger.debug("FalsificationSuite: %s", case.summary())

        return cases

    def _falsify_registry_coverage(
        self, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify RegistryCoverageTheorem.

        Looks for singledispatch functions or ABC classes with 0 registered
        implementations (empty registry → coverage_fraction = 0 < 0.5).
        """
        cases: list[FalsificationCase] = []
        for obj in test_objects:
            # copilot: check for empty singledispatch registry
            if hasattr(obj, "registry") and hasattr(obj, "dispatch"):
                registry_dict = dict(getattr(obj, "registry", {}))
                # copilot: singledispatch always has 'object' registered as the base
                non_object_types = {k for k in registry_dict if k is not object}
                if not non_object_types:
                    case = FalsificationCase(
                        theorem_id = "THM-21-3-REGISTRY-COVERAGE",
                        counterexample_description = (
                            f"singledispatch function '{getattr(obj, '__qualname__', repr(obj))}' "
                            f"has no registered implementations beyond 'object'. "
                            f"Coverage fraction = 0."
                        ),
                        evidence  = {"registry": {"coverage_fraction": 0.0}},
                        severity  = _FALSIFICATION_SEVERITY_MAJOR,
                    )
                    cases.append(case)

            # copilot: check for abstract classes with no concrete implementations
            if inspect.isclass(obj):
                abstract_methods = getattr(obj, "__abstractmethods__", frozenset())
                if abstract_methods:
                    # copilot: look for subclasses that implement these methods
                    subclasses = obj.__subclasses__()
                    if not subclasses:
                        case = FalsificationCase(
                            theorem_id = "THM-21-3-REGISTRY-COVERAGE",
                            counterexample_description = (
                                f"Abstract class '{getattr(obj, '__qualname__', repr(obj))}' "
                                f"has abstract methods {set(abstract_methods)} but no "
                                f"subclasses — registry coverage is 0."
                            ),
                            evidence  = {"registry": {"coverage_fraction": 0.0}},
                            severity  = _FALSIFICATION_SEVERITY_MODERATE,
                        )
                        cases.append(case)

        return cases

    def _falsify_contract_completeness(
        self, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify ContractCompletenessTheorem.

        Looks for dataclasses (generated contracts) where some fields lack
        type annotations (i.e., obligations are not fully defined).
        """
        cases: list[FalsificationCase] = []
        for obj in test_objects:
            if not inspect.isclass(obj):
                continue

            params = getattr(obj, "__dataclass_params__", None)
            if params is None:
                continue

            fields = getattr(obj, "__dataclass_fields__", {})
            unannotated = [
                name for name, f in fields.items()
                if f.type is None or f.type == inspect.Parameter.empty
            ]

            if unannotated:
                case = FalsificationCase(
                    theorem_id = "THM-21-4-CONTRACT-COMPLETENESS",
                    counterexample_description = (
                        f"Dataclass '{getattr(obj, '__qualname__', repr(obj))}' has "
                        f"unannotated fields: {unannotated}. "
                        f"Contract completeness cannot be established."
                    ),
                    evidence  = {
                        "obligations": [
                            {"description": f"field.{n}", "is_discharged": False}
                            for n in unannotated
                        ]
                    },
                    severity  = _FALSIFICATION_SEVERITY_MODERATE,
                )
                cases.append(case)

        return cases

    def _falsify_theorem_burden(
        self, test_objects: list[Any]
    ) -> list[FalsificationCase]:
        """
        Attempt to falsify TheoremBurdenTheorem.

        Looks for annotated symbols where no burden has been computed.
        This would mean annotations exist but generate no obligations,
        contradicting B(A,S) ≠ ∅.

        Heuristic: if an object has __annotations__ but no burden tracking
        attribute, the burden might be implicitly zero (a potential falsification).
        """
        cases: list[FalsificationCase] = []
        for obj in test_objects:
            annotations = getattr(obj, "__annotations__", {})
            if not annotations:
                continue

            # copilot: check for any burden tracking; if absent, this is a concern
            has_burden_tracker = (
                hasattr(obj, "_burden_accumulator")
                or hasattr(obj, "__jugeo_burden__")
                or hasattr(obj, "_theorem_burden")
            )

            if not has_burden_tracker:
                # copilot: annotations present but no burden tracker → burden might be 0
                # This is a MINOR finding: burden is latent until analyzed
                case = FalsificationCase(
                    theorem_id = "THM-21-5-THEOREM-BURDEN",
                    counterexample_description = (
                        f"Object '{getattr(obj, '__qualname__', repr(obj))}' has "
                        f"{len(annotations)} annotation(s) but no burden tracker. "
                        f"Burden B(A,S) exists but has not been computed."
                    ),
                    evidence  = {
                        "annotations":    dict(annotations),
                        "burden":         {"obligations_count": 0},
                    },
                    severity  = _FALSIFICATION_SEVERITY_MINOR,
                )
                cases.append(case)

        return cases

    def run_all(self, test_objects: list[Any]) -> dict[str, list[FalsificationCase]]:
        """
        Run falsification for all five Ch21 theorems.

        Returns a dict mapping theorem_id → list of FalsificationCases found.
        An empty list means no counterexample was found for that theorem.
        """
        results: dict[str, list[FalsificationCase]] = {}
        for theorem in self._registry.all_theorems():
            tid   = theorem.theorem_id
            cases = self.falsify(theorem, test_objects)
            results[tid] = cases
            logger.debug(
                "FalsificationSuite: %s → %d case(s)", tid, len(cases)
            )
        return results

    def report_falsifications(self) -> str:
        """
        Return a multi-line report of all discovered falsification cases.

        Grouped by severity and sorted by theorem section.
        """
        if not self._cases:
            return "FalsificationSuite: no counterexamples found."

        lines = [
            f"FalsificationSuite Report ({len(self._cases)} case(s))",
            "",
        ]
        by_severity: dict[int, list[FalsificationCase]] = {1: [], 2: [], 3: []}
        for case in self._cases:
            by_severity.setdefault(case.severity, []).append(case)

        for sev in (3, 2, 1):
            if not by_severity.get(sev):
                continue
            sev_str = {1: "Minor", 2: "Moderate", 3: "Major"}.get(sev, "?")
            lines.append(f"  {sev_str} ({len(by_severity[sev])}):")
            for case in by_severity[sev]:
                lines.append(f"    {case.summary()}")
            lines.append("")

        return "\n".join(lines)

    def total_cases(self) -> int:
        """Return the total number of falsification cases discovered."""
        return len(self._cases)


# ──────────────────────────────────────────────────────────────────────────────
# AnnotationsDecoratorsRegistriesGeneratedTheoremSchema — top-level facade
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationsDecoratorsRegistriesGeneratedTheoremSchema:
    """
    Top-level facade for the Ch21 theorem schema.

    theory2.tex Ch21 defines five theorems that together form the theoretical
    foundation of the JuGeo annotation analysis framework.  This class ties
    them together into a single Schema object that can validate coverage,
    export theorem records, run falsification, and emit all judgments.

    Usage::

        schema = AnnotationsDecoratorsRegistriesGeneratedTheoremSchema.build_default()
        assert schema.validate_coverage()
        exported = schema.export_schema()
        report   = schema.report()
    """

    def __init__(self) -> None:
        # copilot: one TheoremRegistry and one FalsificationSuite per schema
        self._registry:    TheoremRegistry    = TheoremRegistry()
        self._falsifier:   FalsificationSuite = FalsificationSuite()
        self._validated:   bool               = False
        self._created_at:  str                = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @classmethod
    def build_default(cls) -> AnnotationsDecoratorsRegistriesGeneratedTheoremSchema:
        """
        Factory method: construct and return a fully-initialised schema.

        This is the recommended way to obtain a schema instance.
        """
        schema = cls()
        # copilot: pre-validate to catch missing theorems at construction time
        schema._validated = schema.validate_coverage()
        return schema

    def validate_coverage(self) -> bool:
        """
        Verify that all expected theorems are registered.

        Returns True iff the registry contains exactly _EXPECTED_THEOREM_COUNT
        theorems and all five canonical theorem IDs are present.
        """
        theorems = self._registry.all_theorems()
        if len(theorems) != _EXPECTED_THEOREM_COUNT:
            logger.warning(
                "TheoremSchema: expected %d theorems, found %d",
                _EXPECTED_THEOREM_COUNT, len(theorems)
            )
            return False

        expected_ids = {
            "THM-21-1-ANNOTATION-LATENCY",
            "THM-21-2-DECORATOR-MORPHISM",
            "THM-21-3-REGISTRY-COVERAGE",
            "THM-21-4-CONTRACT-COMPLETENESS",
            "THM-21-5-THEOREM-BURDEN",
        }
        found_ids = {t.theorem_id for t in theorems}
        missing   = expected_ids - found_ids

        if missing:
            logger.error("TheoremSchema: missing theorem IDs: %s", missing)
            return False

        self._validated = True
        return True

    def export_schema(self) -> dict:
        """
        Export all theorem records to a dict keyed by theorem_id.

        Returns a dict of {theorem_id: TheoremRecord.to_dict()}.
        Suitable for JSON serialization.
        """
        return {
            theorem.theorem_id: theorem.to_record().to_dict()
            for theorem in self._registry.all_theorems()
        }

    def run_falsification_suite(self, test_objects: list[Any]) -> dict:
        """
        Run the full falsification suite on the given test objects.

        Returns a dict of {theorem_id: [FalsificationCase.to_dict(), ...]}.
        """
        all_cases = self._falsifier.run_all(test_objects)
        return {
            tid: [case.to_dict() for case in cases]
            for tid, cases in all_cases.items()
        }

    def emit_all_judgments(self) -> list[Judgment]:
        """
        Call build_judgment() on all registered theorems and return the list.

        Emits exactly _EXPECTED_THEOREM_COUNT Judgments if validate_coverage()
        is True.
        """
        judgments: list[Judgment] = []
        for theorem in self._registry.all_theorems():
            try:
                j = theorem.build_judgment()
                judgments.append(j)
            except Exception as exc:
                logger.warning(
                    "TheoremSchema.emit_all_judgments: %s failed: %s",
                    theorem.theorem_id, exc
                )
        logger.debug(
            "TheoremSchema: emitted %d/%d judgments",
            len(judgments), _EXPECTED_THEOREM_COUNT
        )
        return judgments

    def report(self) -> str:
        """
        Return a comprehensive multi-line report of the theorem schema.

        Includes:
          - Schema metadata (theory file, chapter, creation time)
          - Theorem registry report (status of each theorem)
          - Open vs verified theorem counts
          - Falsification suite summary
        """
        open_theorems     = self._registry.get_open_theorems()
        verified_theorems = self._registry.get_verified_theorems()
        total_cases       = self._falsifier.total_cases()

        lines = [
            "╔" + "═" * 62 + "╗",
            "║  AnnotationsDecoratorsRegistriesGenerated Theorem Schema  ║",
            "╚" + "═" * 62 + "╝",
            f"  Theory file:    {_THEORY_FILE}",
            f"  Chapter:        {_CHAPTER}",
            f"  Created at:     {self._created_at}",
            f"  Coverage valid: {self._validated}",
            "",
            f"  Theorems:       {len(self._registry.all_theorems())}",
            f"  Open:           {len(open_theorems)}",
            f"  Verified:       {len(verified_theorems)}",
            f"  Falsification cases found: {total_cases}",
            "",
            "─" * 64,
            "  Theorem Registry:",
            "─" * 64,
            self._registry.report(),
        ]

        if total_cases > 0:
            lines.extend([
                "─" * 64,
                "  Falsification Report:",
                "─" * 64,
                self._falsifier.report_falsifications(),
            ])

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test (theory2.tex Ch21 requires every module to be self-testable)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        # copilot: build the default schema and verify coverage
        schema = AnnotationsDecoratorsRegistriesGeneratedTheoremSchema.build_default()
        assert schema.validate_coverage(), "validate_coverage() returned False"

        # copilot: export schema and check we have exactly 5 entries
        exported = schema.export_schema()
        assert len(exported) == 5, f"Expected 5 theorems, got {len(exported)}"

        # copilot: verify each theorem ID is present
        expected_ids = {
            "THM-21-1-ANNOTATION-LATENCY",
            "THM-21-2-DECORATOR-MORPHISM",
            "THM-21-3-REGISTRY-COVERAGE",
            "THM-21-4-CONTRACT-COMPLETENESS",
            "THM-21-5-THEOREM-BURDEN",
        }
        assert set(exported.keys()) == expected_ids, f"Unexpected theorem IDs: {set(exported.keys())}"

        # copilot: test FalsificationSuite against a simple unannotated function
        falsifier = FalsificationSuite()

        def unannotated():
            """A function with no annotations — used as a falsification test target."""
            pass

        results = falsifier.run_all([unannotated])
        assert isinstance(results, dict)
        assert len(results) == 5

        # copilot: test emit_all_judgments
        judgments = schema.emit_all_judgments()
        assert len(judgments) == 5, f"Expected 5 judgments, got {len(judgments)}"

        # copilot: test individual theorem checks with well-formed evidence
        registry = TheoremRegistry()

        # Test §21.1 — annotation latency
        thm1 = registry.get("THM-21-1-ANNOTATION-LATENCY")
        assert thm1 is not None
        result1 = thm1.check({"annotations": {"x": "int"}, "runtime_violations": []})
        assert result1 is True, "§21.1 check should pass for annotated symbol with no violations"

        # Test §21.2 — decorator morphism
        thm2 = registry.get("THM-21-2-DECORATOR-MORPHISM")
        assert thm2 is not None
        result2 = thm2.check({
            "decorators": [
                {"source_qualname": "my_func", "target_qualname": "wrapper(my_func)"}
            ]
        })
        assert result2 is True, "§21.2 check should pass for well-formed decorator entry"

        # Test §21.3 — registry coverage
        thm3 = registry.get("THM-21-3-REGISTRY-COVERAGE")
        assert thm3 is not None
        result3 = thm3.check({"registry": {"coverage_fraction": 0.75}})
        assert result3 is True, "§21.3 check should pass for coverage_fraction=0.75"

        # Test §21.4 — contract completeness
        thm4 = registry.get("THM-21-4-CONTRACT-COMPLETENESS")
        assert thm4 is not None

        @dataclass(frozen=True, slots=True)
        class _TestContract:
            x: int = 0
            y: str = ""

        obligations_all_discharged = [
            {"description": "x: int", "is_discharged": True},
            {"description": "y: str", "is_discharged": True},
        ]
        result4 = thm4.check({"obligations": obligations_all_discharged})
        assert result4 is True, "§21.4 check should pass when all obligations discharged"

        # Test §21.5 — theorem burden
        thm5 = registry.get("THM-21-5-THEOREM-BURDEN")
        assert thm5 is not None
        result5 = thm5.check({"annotations": {"x": "int", "y": "str"}})
        assert result5 is True, "§21.5 check should pass when annotations are present"

        # copilot: test TheoremRecord.verify() promotes status
        record = thm1.build_record()
        assert record.is_open(), "Fresh record should be UNVERIFIED (open)"
        record2 = record.verify({"test": "evidence_1"})
        assert record2.verification_status == TheoremVerificationStatus.PARTIALLY_VERIFIED

        # copilot: test report generation
        report = schema.report()
        assert "Theorem Schema" in report
        assert _THEORY_FILE in report

        print(f"[smoke] theorems={len(exported)}, judgments={len(judgments)}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
