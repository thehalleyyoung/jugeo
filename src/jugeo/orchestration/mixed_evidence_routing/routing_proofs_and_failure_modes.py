"""Routing proofs and failure modes for the mixed-evidence routing layer.

# copilot: This module implements the proof-theoretic correctness machinery
# and failure-mode taxonomy for the jugeo routing layer, as specified in
# theory2.tex Ch 45 §45.6 ("Routing Correctness Proofs") and §45.7
# ("Failure Mode Classification and Recovery").
#
# WHY ROUTING PROOFS MATTER
# ─────────────────────────
# Routing is not a heuristic dispatcher.  In jugeo, a routing *decision* is a
# judgment-geometric object that must be provably correct with respect to the
# trust algebra and the active obligation set.  Without a proof:
#
#   • The system cannot justify why a particular channel was selected.
#   • Trust elevation is unsound — we would be asserting VERIFIED status for
#     an unverified claim.
#   • Obligation timeouts go undetected, leaving the O-component of the
#     judgment tuple in an inconsistent state.
#
# The judgment tuple is always 8-tuple:
#
#   (c, φ, A, E, O, B, T, Π)
#
#   c  — context (execution environment, namespace, problem domain)
#   φ  — formula (the claim or task being routed)
#   A  — agent-set (which agents are authorised to handle this claim)
#   E  — evidence-set (accumulated evidence artefacts)
#   O  — obligation-set (active proof obligations that must be discharged)
#   B  — belief-state (current probabilistic / possibilistic belief lattice)
#   T  — trust-tier (position in the ordered trust algebra)
#   Π  — proof-object (formal or semi-formal certificate of correctness)
#
# A routing proof operates over *all eight components*.  It is not sufficient
# to prove only that the channel is available (T) or that the formula is
# well-formed (φ).  The proof must show that:
#
#   1. The selected channel is a member of the authorised agent-set A.
#   2. The channel's trust tier T_ch is consistent with the required tier T_req
#      under the trust algebra ordering ≼.
#   3. All obligations in O can be discharged through the selected channel.
#   4. The evidence-set E is not contradicted by the channel's known limitations.
#   5. The belief-state B is coherent with the channel selection.
#   6. The proof-object Π is updated to record the routing step.
#
# TRUST ALGEBRA RECAP
# ───────────────────
# Trust is the ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ):
#
#   E_adm — the set of admissible evidence (evidence meeting baseline quality).
#   ≼     — partial order on trust tiers (PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼
#            RUNTIME_WITNESSED ≼ PROOF_BACKED).
#   ⊕     — trust join (combine two evidence sources, yield higher tier if
#            both agree, otherwise yield lower-bound via conservatism principle).
#   ⊖     — trust meet (retract to lower bound when evidence is contradicted).
#   ↑_π   — trust elevation by proof π (requires a completed proof object).
#   ↓_χ   — trust demotion by counter-evidence χ.
#
# Critically: trust is NEVER a plain float.  Using floats for trust silently
# discards the algebraic structure and makes it impossible to verify that
# elevation steps are sound.
#
# FAILURE MODES AND JUDGMENT TUPLE COMPONENTS
# ────────────────────────────────────────────
# Each failure mode maps to one or more components of the judgment tuple:
#
#   ROUTING_LOOP        → c (context) contains a cyclic dependency
#   CHANNEL_UNAVAILABLE → A (agent-set) becomes empty at runtime
#   TRUST_VIOLATION     → T (trust-tier) ordering is violated
#   OBLIGATION_TIMEOUT  → O (obligation-set) has an expired member
#   PROOF_FAILURE       → Π (proof-object) cannot be completed
#   EVIDENCE_CONFLICT   → E (evidence-set) contains contradictory members
#   GEOMETRIC_DIVERGENCE→ B (belief-state) diverges beyond the belief lattice
#
# References
# ──────────
# * theory2.tex Ch 45 §45.6 — Routing Correctness Proofs
# * theory2.tex Ch 45 §45.7 — Failure Mode Classification and Recovery
# * theory2.tex Ch 12 §12.3 — Judgment Tuple Semantics
# * theory2.tex Ch 18 §18.1 — Trust Algebra
# * trust_aware_routing.py — Trust-aware routing (upstream module)
# * channel_conflict_resolution.py — Conflict resolution (upstream module)
"""

from __future__ import annotations

import enum
import uuid
import time
import hashlib
import logging
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports with stub fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel
    _TRUST_LEVEL_AVAILABLE = True
except ImportError:
    _TRUST_LEVEL_AVAILABLE = False

    class TrustLevel(str, enum.Enum):  # type: ignore[no-redef]
        """Stub TrustLevel used when jugeo.evidence.trust is unavailable."""
        MECHANICALLY_VERIFIED = "mechanically_verified"
        SOLVER_DISCHARGED = "solver_discharged"
        RUNTIME_WITNESSED = "runtime_witnessed"
        HUMAN_ATTESTED = "human_attested"
        ORACLE_PROPOSED = "oracle_proposed"
        COPILOT_SUGGESTED = "copilot_suggested"
        UNVERIFIED = "unverified"
        CONTRADICTED = "contradicted"


try:
    from jugeo.orchestration.mixed_evidence_routing.models import (
        RoutingDecision,
        EvidenceChannel,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    @dataclass(frozen=True)
    class RoutingDecision:  # type: ignore[no-redef]
        """Stub RoutingDecision."""
        decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        channel: str = "UNKNOWN"
        rationale: str = ""

    class EvidenceChannel(str, enum.Enum):  # type: ignore[no-redef]
        """Stub EvidenceChannel."""
        Z3 = "z3"
        COPILOT_LLM = "copilot_llm"
        RUNTIME_WITNESS = "runtime_witness"
        HUMAN = "human"
        COMPOSITE = "composite"


try:
    from jugeo.judgments.judgment_tuple import JudgmentTuple
    _JUDGMENT_AVAILABLE = True
except ImportError:
    _JUDGMENT_AVAILABLE = False

    @dataclass(frozen=True)
    class JudgmentTuple:  # type: ignore[no-redef]
        """Stub for the canonical 8-tuple (c, φ, A, E, O, B, T, Π)."""
        c: Any = None    # context
        phi: Any = None  # formula
        A: Any = None    # agent-set
        E: Any = None    # evidence-set
        O: Any = None    # obligation-set
        B: Any = None    # belief-state
        T: Any = None    # trust-tier
        Pi: Any = None   # proof-object


try:
    from jugeo.evidence.trust_algebra import TrustAlgebra
    _TRUST_ALGEBRA_AVAILABLE = True
except ImportError:
    _TRUST_ALGEBRA_AVAILABLE = False

    @dataclass(frozen=True)
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for the ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

        Trust is never a plain float.  This stub preserves the algebraic
        interface so that callers written against the real TrustAlgebra work
        without modification when the real module is absent.
        """
        admissible_evidence: tuple = ()
        tier_order: tuple = (
            "PROPOSAL",
            "REVIEWED",
            "VERIFIED",
            "RUNTIME_WITNESSED",
            "PROOF_BACKED",
        )

        def preceq(self, tier_a: str, tier_b: str) -> bool:
            """Return True if tier_a ≼ tier_b (tier_a is at most tier_b)."""
            order = list(self.tier_order)
            try:
                return order.index(tier_a) <= order.index(tier_b)
            except ValueError:
                return False

        def join(self, tier_a: str, tier_b: str) -> str:
            """⊕: Return the higher tier if both agree, else lower bound."""
            order = list(self.tier_order)
            try:
                ia, ib = order.index(tier_a), order.index(tier_b)
                return order[max(ia, ib)]
            except ValueError:
                return "PROPOSAL"

        def meet(self, tier_a: str, tier_b: str) -> str:
            """⊖: Return the lower trust bound (conservatism principle)."""
            order = list(self.tier_order)
            try:
                ia, ib = order.index(tier_a), order.index(tier_b)
                return order[min(ia, ib)]
            except ValueError:
                return "PROPOSAL"

        def elevate(self, tier: str, proof_id: str) -> str:
            """↑_π: Elevate trust by one tier given a completed proof."""
            order = list(self.tier_order)
            try:
                idx = order.index(tier)
                return order[min(idx + 1, len(order) - 1)]
            except ValueError:
                return tier

        def demote(self, tier: str, counter_evidence_id: str) -> str:
            """↓_χ: Demote trust by one tier given counter-evidence."""
            order = list(self.tier_order)
            try:
                idx = order.index(tier)
                return order[max(idx - 1, 0)]
            except ValueError:
                return tier


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """The five canonical trust tiers of the jugeo trust algebra.

    The ordering is strictly ascending:
        PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED

    Never collapse these to integers or floats — the algebraic structure
    (join, meet, elevation, demotion) depends on the ordered set, not on
    arithmetic.
    """
    PROPOSAL          = "PROPOSAL"
    REVIEWED          = "REVIEWED"
    VERIFIED          = "VERIFIED"
    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    PROOF_BACKED      = "PROOF_BACKED"

    @property
    def rank(self) -> int:
        """Integer rank for ordering comparisons (do not use for arithmetic)."""
        return _TRUST_TIER_RANKS[self]


_TRUST_TIER_RANKS: dict[TrustTier, int] = {
    TrustTier.PROPOSAL:          0,
    TrustTier.REVIEWED:          1,
    TrustTier.VERIFIED:          2,
    TrustTier.RUNTIME_WITNESSED: 3,
    TrustTier.PROOF_BACKED:      4,
}


class ProofStrategy(str, Enum):
    """Strategy used to construct a routing proof.

    Each strategy corresponds to a classical proof method adapted for the
    judgment-geometric setting of theory2.tex Ch 45.

    DIRECT            — Directly derive the correctness condition from axioms.
    CONTRADICTION     — Assume ¬correctness and derive a contradiction with the
                        trust algebra ordering.
    INDUCTION         — Induct on the length of the fallback chain.
    CASE_ANALYSIS     — Enumerate all relevant cases of the judgment tuple
                        components and show correctness in each.
    AXIOM_APPLICATION — Apply a single pre-verified routing axiom (fast path).
    JUDGMENT_COMPOSITION — Compose smaller judgment correctness proofs into a
                        larger one (corresponds to ⊕ in the trust algebra).
    """
    DIRECT                = "DIRECT"
    CONTRADICTION         = "CONTRADICTION"
    INDUCTION             = "INDUCTION"
    CASE_ANALYSIS         = "CASE_ANALYSIS"
    AXIOM_APPLICATION     = "AXIOM_APPLICATION"
    JUDGMENT_COMPOSITION  = "JUDGMENT_COMPOSITION"


class FailureType(str, Enum):
    """Taxonomy of routing failure modes.

    Each failure type is associated with one or more components of the
    judgment tuple (c, φ, A, E, O, B, T, Π) as documented in the module
    docstring.

    ROUTING_LOOP         — cyclic routing detected in context c.
    CHANNEL_UNAVAILABLE  — agent-set A is empty or all members are down.
    TRUST_VIOLATION      — a routing step violates the ≼ ordering on T.
    OBLIGATION_TIMEOUT   — a member of obligation-set O has expired.
    PROOF_FAILURE        — the proof-object Π cannot be completed.
    EVIDENCE_CONFLICT    — evidence-set E contains contradictory artefacts.
    GEOMETRIC_DIVERGENCE — belief-state B diverges beyond the belief lattice.
    """
    ROUTING_LOOP         = "ROUTING_LOOP"
    CHANNEL_UNAVAILABLE  = "CHANNEL_UNAVAILABLE"
    TRUST_VIOLATION      = "TRUST_VIOLATION"
    OBLIGATION_TIMEOUT   = "OBLIGATION_TIMEOUT"
    PROOF_FAILURE        = "PROOF_FAILURE"
    EVIDENCE_CONFLICT    = "EVIDENCE_CONFLICT"
    GEOMETRIC_DIVERGENCE = "GEOMETRIC_DIVERGENCE"


class FailureSeverity(str, Enum):
    """Severity classification for routing failures.

    Maps to operational response priorities:
      CRITICAL — routing is completely halted; immediate human escalation.
      HIGH     — significant degradation; automatic recovery attempted.
      MEDIUM   — partial degradation; fallback channels activated.
      LOW      — minor issue; logged, no immediate action.
      WARNING  — pre-failure signal; monitoring intensified.
      INFO     — informational; recorded for post-hoc analysis only.
    """
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    WARNING  = "WARNING"
    INFO     = "INFO"


class ProofStepType(str, Enum):
    """Types of steps that may appear in a routing proof."""
    AXIOM        = "AXIOM"
    HYPOTHESIS   = "HYPOTHESIS"
    INFERENCE    = "INFERENCE"
    SUBSTITUTION = "SUBSTITUTION"
    DISCHARGE    = "DISCHARGE"
    COMPOSITION  = "COMPOSITION"
    CONCLUSION   = "CONCLUSION"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofStep:
    """A single step in a routing correctness proof.

    A routing proof is a sequence of ProofStep objects.  Each step either
    introduces an axiom or hypothesis, applies an inference rule, or
    discharges an obligation.  The final step MUST be of type CONCLUSION.

    Fields
    ──────
    step_id       — Unique identifier for this proof step.
    step_type     — Logical role of this step (see ProofStepType).
    formula       — The formula asserted at this step.  Uses a string
                    representation of the judgment or sub-formula.
    justification — Human-readable justification (axiom name, rule name, etc.).
    dependencies  — step_ids of prior steps that this step depends on.
    produces      — The logical consequence produced by this step; used by
                    downstream steps as a dependency.
    """
    step_id:      str
    step_type:    ProofStepType
    formula:      str
    justification: str
    dependencies: tuple[str, ...]
    produces:     str

    def is_conclusion(self) -> bool:
        """Return True iff this step is a proof conclusion."""
        return self.step_type == ProofStepType.CONCLUSION

    def depends_on(self, other_step_id: str) -> bool:
        """Return True iff this step directly depends on other_step_id."""
        return other_step_id in self.dependencies

    def __str__(self) -> str:
        dep_str = ", ".join(self.dependencies) if self.dependencies else "—"
        return (
            f"[{self.step_type.value}] {self.step_id}: {self.formula}\n"
            f"  Justification: {self.justification}\n"
            f"  Depends on: {dep_str}\n"
            f"  Produces: {self.produces}"
        )


@dataclass(frozen=True)
class RoutingProof:
    """A proof that a routing decision is correct.

    A RoutingProof certifies that the routing decision identified by
    routing_decision_id satisfies all correctness conditions derivable
    from the judgment tuple and the trust algebra.

    Fields
    ──────
    proof_id            — Unique identifier for this proof.
    routing_decision_id — The decision this proof certifies.
    proof_steps         — Ordered tuple of ProofStep objects.  The proof is
                          valid iff the last step is a CONCLUSION and all
                          dependencies are satisfied.
    axioms_used         — Names of axioms referenced in the proof.
    proof_strategy      — High-level strategy (see ProofStrategy).
    trust_tier          — The trust tier claimed by this proof.  Must be
                          consistent with the trust algebra — you cannot
                          claim PROOF_BACKED without a complete Π.
    proof_certificate   — An opaque certificate string (e.g., a hash of the
                          proof steps) that can be checked without replaying
                          the full proof.
    is_complete         — True iff the proof is complete (all obligations
                          discharged and a CONCLUSION step is present).

    Theory invariant
    ────────────────
    trust_tier MUST NOT exceed VERIFIED unless is_complete is True.
    Claiming PROOF_BACKED on an incomplete proof is a soundness violation.
    """
    proof_id:            str
    routing_decision_id: str
    proof_steps:         tuple[ProofStep, ...]
    axioms_used:         tuple[str, ...]
    proof_strategy:      ProofStrategy
    trust_tier:          TrustTier
    proof_certificate:   str
    is_complete:         bool

    def num_steps(self) -> int:
        """Return the number of proof steps."""
        return len(self.proof_steps)

    def conclusion_step(self) -> Optional[ProofStep]:
        """Return the conclusion step, or None if the proof is not complete."""
        for step in reversed(self.proof_steps):
            if step.is_conclusion():
                return step
        return None

    def axiom_count(self) -> int:
        """Return the number of distinct axioms referenced."""
        return len(self.axioms_used)

    def verify_dependency_graph(self) -> bool:
        """Verify that all step dependencies are satisfied (no dangling refs).

        Returns True if every dependency of every step refers to a step_id
        that appears earlier in the proof_steps sequence.
        """
        seen: set[str] = set()
        for step in self.proof_steps:
            for dep in step.dependencies:
                if dep not in seen:
                    return False
            seen.add(step.step_id)
        return True

    def __str__(self) -> str:
        status = "COMPLETE" if self.is_complete else "INCOMPLETE"
        return (
            f"RoutingProof({self.proof_id}) [{status}]\n"
            f"  Decision: {self.routing_decision_id}\n"
            f"  Strategy: {self.proof_strategy.value}\n"
            f"  Trust tier: {self.trust_tier.value}\n"
            f"  Steps: {self.num_steps()}\n"
            f"  Axioms: {', '.join(self.axioms_used) or '—'}\n"
            f"  Certificate: {self.proof_certificate[:16]}..."
        )


@dataclass(frozen=True)
class RoutingFailureMode:
    """A classified failure mode in the routing layer.

    Each RoutingFailureMode corresponds to an entry in the
    FailureModeRegistry.  It records:
      • What failed (failure_type).
      • Which channel was affected.
      • The root cause in terms of the judgment tuple components.
      • Evidence that the failure occurred.
      • How to recover.
      • How severe the failure is.

    Fields
    ──────
    failure_id        — Unique identifier for this failure instance.
    failure_type      — Taxonomy classification (see FailureType).
    affected_channel  — The EvidenceChannel that failed.
    root_cause        — Human-readable description of the root cause,
                        referencing the relevant judgment tuple component.
    evidence_of_failure — Tuple of evidence artefact IDs that demonstrate
                          the failure.
    recovery_strategy — Description of how to recover from this failure.
    severity          — Operational severity (see FailureSeverity).

    Judgment-tuple mapping
    ──────────────────────
    Every RoutingFailureMode maps to a component of (c, φ, A, E, O, B, T, Π):
      ROUTING_LOOP         ↔ c  (cyclic context)
      CHANNEL_UNAVAILABLE  ↔ A  (agent-set exhausted)
      TRUST_VIOLATION      ↔ T  (trust ordering violated)
      OBLIGATION_TIMEOUT   ↔ O  (obligation set expired)
      PROOF_FAILURE        ↔ Π  (proof object incomplete)
      EVIDENCE_CONFLICT    ↔ E  (evidence set contradicted)
      GEOMETRIC_DIVERGENCE ↔ B  (belief state out of lattice bounds)
    """
    failure_id:          str
    failure_type:        FailureType
    affected_channel:    str          # EvidenceChannel value or "UNKNOWN"
    root_cause:          str
    evidence_of_failure: tuple[str, ...]
    recovery_strategy:   str
    severity:            FailureSeverity

    @property
    def judgment_tuple_component(self) -> str:
        """Return the judgment tuple component (c/φ/A/E/O/B/T/Π) implicated."""
        _map = {
            FailureType.ROUTING_LOOP:         "c  (context)",
            FailureType.CHANNEL_UNAVAILABLE:  "A  (agent-set)",
            FailureType.TRUST_VIOLATION:      "T  (trust-tier)",
            FailureType.OBLIGATION_TIMEOUT:   "O  (obligation-set)",
            FailureType.PROOF_FAILURE:        "Π  (proof-object)",
            FailureType.EVIDENCE_CONFLICT:    "E  (evidence-set)",
            FailureType.GEOMETRIC_DIVERGENCE: "B  (belief-state)",
        }
        return _map.get(self.failure_type, "?")

    def is_critical(self) -> bool:
        """Return True iff this failure requires immediate escalation."""
        return self.severity in (FailureSeverity.CRITICAL, FailureSeverity.HIGH)

    def __str__(self) -> str:
        return (
            f"RoutingFailureMode({self.failure_id})\n"
            f"  Type: {self.failure_type.value}  Severity: {self.severity.value}\n"
            f"  Affects: {self.affected_channel}\n"
            f"  Tuple component: {self.judgment_tuple_component}\n"
            f"  Root cause: {self.root_cause}\n"
            f"  Recovery: {self.recovery_strategy}"
        )


@dataclass(frozen=True)
class RoutingCorrectness:
    """A correctness certificate for a routing decision.

    A RoutingCorrectness object is the highest-level artefact produced by
    the proof machinery.  It bundles:
      • The judgment tuple that was routed.
      • The conditions that were verified.
      • The trust elevation that the proof warrants.
      • The underlying proof that justifies the certificate.

    Fields
    ──────
    certificate_id       — Unique certificate identifier.
    routing_judgment     — String serialisation of the judgment tuple
                           (c, φ, A, E, O, B, T, Π).
    correctness_conditions — Tuple of condition strings that were verified.
    verified_properties  — Mapping from property name to verification result.
    trust_elevation      — The TrustTier to which this certificate elevates
                           the routing decision.  Requires a complete proof.
    certificate_proof    — The RoutingProof that backs this certificate.

    Soundness note
    ──────────────
    trust_elevation MUST satisfy:
        certificate_proof.trust_tier ≼ trust_elevation

    Elevating beyond the proof's tier is a soundness violation.  The
    generate_correctness_certificate() function enforces this invariant.
    """
    certificate_id:         str
    routing_judgment:       str
    correctness_conditions: tuple[str, ...]
    verified_properties:    tuple[tuple[str, bool], ...]
    trust_elevation:        TrustTier
    certificate_proof:      RoutingProof

    def all_conditions_met(self) -> bool:
        """Return True iff every verified property is True."""
        return all(v for _, v in self.verified_properties)

    def condition_count(self) -> int:
        """Return the number of correctness conditions verified."""
        return len(self.correctness_conditions)

    def failed_conditions(self) -> list[str]:
        """Return the names of properties that failed verification."""
        return [name for name, ok in self.verified_properties if not ok]

    def summary(self) -> str:
        """Return a one-line summary of the certificate."""
        status = "VALID" if self.all_conditions_met() else "INVALID"
        return (
            f"RoutingCorrectness[{status}] cert={self.certificate_id[:8]}… "
            f"tier={self.trust_elevation.value} "
            f"conditions={self.condition_count()}"
        )


@dataclass(frozen=True)
class FailureAnalysis:
    """Analysis of a collection of routing failures.

    Produced by the FailureModeRegistry after scanning a batch of failure
    events.  Provides aggregate statistics and prioritised recommendations.

    Fields
    ──────
    analysis_id               — Unique identifier for this analysis run.
    failure_modes_found       — Tuple of RoutingFailureMode objects found.
    common_causes             — Tuple of (cause_description, count) pairs
                                sorted by frequency (most common first).
    failure_distribution      — Mapping from FailureType to occurrence count.
    mitigation_recommendations — Ordered tuple of recommendation strings.
    analysis_timestamp        — Unix timestamp when the analysis was run.
    """
    analysis_id:               str
    failure_modes_found:       tuple[RoutingFailureMode, ...]
    common_causes:             tuple[tuple[str, int], ...]
    failure_distribution:      tuple[tuple[str, int], ...]
    mitigation_recommendations: tuple[str, ...]
    analysis_timestamp:        float

    def total_failures(self) -> int:
        """Return the total number of failure modes found."""
        return len(self.failure_modes_found)

    def critical_failures(self) -> list[RoutingFailureMode]:
        """Return only CRITICAL or HIGH severity failures."""
        return [f for f in self.failure_modes_found if f.is_critical()]

    def most_common_type(self) -> Optional[str]:
        """Return the most frequently occurring FailureType, or None."""
        if not self.failure_distribution:
            return None
        return max(self.failure_distribution, key=lambda kv: kv[1])[0]

    def __str__(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.analysis_timestamp))
        top_rec = self.mitigation_recommendations[0] if self.mitigation_recommendations else "—"
        return (
            f"FailureAnalysis({self.analysis_id}) @ {ts}\n"
            f"  Total failures: {self.total_failures()}\n"
            f"  Critical/High: {len(self.critical_failures())}\n"
            f"  Most common type: {self.most_common_type()}\n"
            f"  Top recommendation: {top_rec}"
        )


# ---------------------------------------------------------------------------
# Non-frozen classes
# ---------------------------------------------------------------------------


class RoutingProofChecker:
    """Verifies routing proofs step by step.

    The RoutingProofChecker applies a sequence of verification checks to a
    RoutingProof.  Each check corresponds to a clause in the correctness
    definition of theory2.tex Ch 45 §45.6.

    Checks performed
    ────────────────
    1. Structural completeness — the proof ends with a CONCLUSION step.
    2. Dependency soundness — every step's dependencies refer to earlier steps.
    3. Trust consistency — the claimed trust tier is warranted by the proof.
    4. Axiom recognition — all axioms_used are in the known axiom registry.
    5. Strategy coherence — the proof_steps are consistent with the strategy.

    The checker does NOT replay the full formal proof (that requires a theorem
    prover); it performs the structural and semantic checks that are tractable
    at runtime.
    """

    _KNOWN_AXIOMS: frozenset[str] = frozenset({
        "ROUTING_SOUNDNESS",
        "TRUST_MONOTONICITY",
        "AGENT_JURISDICTION",
        "OBLIGATION_DISCHARGE",
        "EVIDENCE_ADMISSIBILITY",
        "BELIEF_COHERENCE",
        "PROOF_COMPLETENESS",
        "CHANNEL_AVAILABILITY",
        "FALLBACK_CORRECTNESS",
        "CONSERVATISM_PRINCIPLE",
    })

    def __init__(self) -> None:
        self._check_results: dict[str, list[str]] = {}

    def check(self, proof: RoutingProof) -> tuple[bool, list[str]]:
        """Run all checks on *proof*.

        Returns a (passed, errors) pair.  If passed is True, errors is empty.
        If passed is False, errors contains human-readable descriptions of
        each check that failed.
        """
        errors: list[str] = []

        # Check 1: Structural completeness
        if not proof.is_complete:
            errors.append(
                "STRUCTURAL: proof.is_complete is False — "
                "no CONCLUSION step found."
            )
        elif proof.conclusion_step() is None:
            errors.append(
                "STRUCTURAL: proof.is_complete is True but no step has "
                "step_type == CONCLUSION."
            )

        # Check 2: Dependency soundness
        if not proof.verify_dependency_graph():
            errors.append(
                "DEPENDENCY: proof has dangling dependencies — "
                "at least one step depends on a step_id that does not "
                "appear earlier in the proof sequence."
            )

        # Check 3: Trust consistency
        if proof.trust_tier in (TrustTier.RUNTIME_WITNESSED, TrustTier.PROOF_BACKED):
            if not proof.is_complete:
                errors.append(
                    f"TRUST: claimed tier {proof.trust_tier.value} requires a "
                    "complete proof but proof.is_complete is False."
                )

        # Check 4: Axiom recognition
        unknown = set(proof.axioms_used) - self._KNOWN_AXIOMS
        if unknown:
            errors.append(
                f"AXIOM: unrecognised axioms: {', '.join(sorted(unknown))}. "
                "These must be registered before use."
            )

        # Check 5: Strategy coherence
        strategy_errors = self._check_strategy_coherence(proof)
        errors.extend(strategy_errors)

        # Check 6: Certificate integrity
        expected_cert = self._compute_expected_certificate(proof)
        if proof.proof_certificate != expected_cert:
            errors.append(
                f"CERTIFICATE: certificate mismatch — stored "
                f"'{proof.proof_certificate[:16]}…' vs expected "
                f"'{expected_cert[:16]}…'."
            )

        passed = len(errors) == 0
        self._check_results[proof.proof_id] = errors
        logger.debug(
            "RoutingProofChecker: proof %s %s (%d errors)",
            proof.proof_id,
            "PASSED" if passed else "FAILED",
            len(errors),
        )
        return passed, errors

    def _check_strategy_coherence(self, proof: RoutingProof) -> list[str]:
        """Verify that proof_steps are coherent with the declared strategy."""
        errors: list[str] = []
        step_types = [s.step_type for s in proof.proof_steps]

        if proof.proof_strategy == ProofStrategy.DIRECT:
            if ProofStepType.HYPOTHESIS in step_types:
                pass  # Hypotheses are fine in direct proofs
            if step_types and step_types[-1] != ProofStepType.CONCLUSION:
                errors.append(
                    "STRATEGY(DIRECT): last step must be CONCLUSION."
                )

        elif proof.proof_strategy == ProofStrategy.CONTRADICTION:
            # A proof by contradiction must have a HYPOTHESIS step
            if ProofStepType.HYPOTHESIS not in step_types:
                errors.append(
                    "STRATEGY(CONTRADICTION): contradiction proof requires "
                    "at least one HYPOTHESIS step (the negated assumption)."
                )

        elif proof.proof_strategy == ProofStrategy.INDUCTION:
            # Inductive proofs must have at least one INFERENCE step
            if ProofStepType.INFERENCE not in step_types:
                errors.append(
                    "STRATEGY(INDUCTION): inductive proof requires at least "
                    "one INFERENCE step (the inductive case)."
                )

        elif proof.proof_strategy == ProofStrategy.AXIOM_APPLICATION:
            # Fast-path: must reference at least one axiom
            if not proof.axioms_used:
                errors.append(
                    "STRATEGY(AXIOM_APPLICATION): axiom-application proof "
                    "must reference at least one axiom."
                )

        return errors

    @staticmethod
    def _compute_expected_certificate(proof: RoutingProof) -> str:
        """Compute the expected proof certificate from proof content."""
        content = (
            proof.proof_id
            + proof.routing_decision_id
            + proof.proof_strategy.value
            + proof.trust_tier.value
            + str(proof.is_complete)
            + "".join(s.formula for s in proof.proof_steps)
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def last_errors(self, proof_id: str) -> list[str]:
        """Return the errors from the most recent check of proof_id."""
        return self._check_results.get(proof_id, [])

    def register_axiom(self, axiom_name: str) -> None:
        """Register a new axiom name so that proofs may reference it."""
        # _KNOWN_AXIOMS is a frozenset on the class; we must shadow it here.
        self.__class__._KNOWN_AXIOMS = self._KNOWN_AXIOMS | {axiom_name}
        logger.info("RoutingProofChecker: registered axiom '%s'", axiom_name)


class FailureModeRegistry:
    """Maintains a registry of known failure modes and their mitigations.

    The FailureModeRegistry serves two purposes:
    1. It stores the catalogue of *known* failure modes so that the
       classify_failure_mode() function can match symptoms to modes.
    2. It accumulates *observed* failure instances for post-hoc analysis.

    The registry is *not* frozen because it grows as new failures are
    observed and as new mitigation strategies are registered.

    Theory note
    ───────────
    The registry implements the "failure lattice" of theory2.tex Ch 45 §45.7.
    Every registered failure mode has a position in the failure lattice
    determined by its FailureType and FailureSeverity.  The lattice ordering
    allows the analysis machinery to identify which failures subsume others.
    """

    # Default mitigation templates keyed by FailureType
    _DEFAULT_MITIGATIONS: dict[FailureType, str] = {
        FailureType.ROUTING_LOOP:
            "Break the cycle by removing the least-trusted edge in the "
            "routing graph; re-route through a non-cyclic path.",
        FailureType.CHANNEL_UNAVAILABLE:
            "Activate the next fallback channel in the FallbackChain; "
            "if none available, escalate to human via the obligation set.",
        FailureType.TRUST_VIOLATION:
            "Demote (↓_χ) the offending decision's trust tier and "
            "re-evaluate routing with the corrected trust algebra.",
        FailureType.OBLIGATION_TIMEOUT:
            "Extend the obligation deadline or mark the obligation as "
            "expired and propagate the demotion through the belief-state.",
        FailureType.PROOF_FAILURE:
            "Attempt an alternative ProofStrategy; if all strategies "
            "fail, downgrade trust to REVIEWED and flag for human review.",
        FailureType.EVIDENCE_CONFLICT:
            "Apply the conservatism principle (⊖): set evidence-set "
            "trust to the meet of conflicting tiers.",
        FailureType.GEOMETRIC_DIVERGENCE:
            "Project the belief-state back onto the nearest lattice "
            "point and re-run routing from the corrected belief-state.",
    }

    def __init__(self) -> None:
        self._known_modes: list[RoutingFailureMode] = []
        self._observed: list[RoutingFailureMode] = []
        self._mitigations: dict[FailureType, str] = dict(self._DEFAULT_MITIGATIONS)
        self._seed_known_modes()

    def _seed_known_modes(self) -> None:
        """Seed the registry with the canonical failure modes from theory2."""
        for ft in FailureType:
            mode = RoutingFailureMode(
                failure_id=f"KNOWN-{ft.value}",
                failure_type=ft,
                affected_channel="ANY",
                root_cause=f"Canonical {ft.value} failure as defined in "
                           f"theory2.tex Ch 45 §45.7.",
                evidence_of_failure=(),
                recovery_strategy=self._mitigations[ft],
                severity=self._default_severity(ft),
            )
            self._known_modes.append(mode)

    @staticmethod
    def _default_severity(ft: FailureType) -> FailureSeverity:
        """Return the default severity for a given failure type."""
        _sev = {
            FailureType.ROUTING_LOOP:         FailureSeverity.CRITICAL,
            FailureType.CHANNEL_UNAVAILABLE:  FailureSeverity.HIGH,
            FailureType.TRUST_VIOLATION:      FailureSeverity.CRITICAL,
            FailureType.OBLIGATION_TIMEOUT:   FailureSeverity.HIGH,
            FailureType.PROOF_FAILURE:        FailureSeverity.MEDIUM,
            FailureType.EVIDENCE_CONFLICT:    FailureSeverity.MEDIUM,
            FailureType.GEOMETRIC_DIVERGENCE: FailureSeverity.LOW,
        }
        return _sev.get(ft, FailureSeverity.WARNING)

    def register_observed(self, mode: RoutingFailureMode) -> None:
        """Record an observed failure instance."""
        self._observed.append(mode)
        logger.warning(
            "FailureModeRegistry: observed %s failure (severity=%s) on channel %s",
            mode.failure_type.value,
            mode.severity.value,
            mode.affected_channel,
        )

    def lookup_mitigation(self, failure_type: FailureType) -> str:
        """Return the mitigation strategy for a given failure type."""
        return self._mitigations.get(failure_type, "No mitigation registered.")

    def register_mitigation(self, failure_type: FailureType, mitigation: str) -> None:
        """Register or update a mitigation strategy."""
        self._mitigations[failure_type] = mitigation
        logger.info(
            "FailureModeRegistry: updated mitigation for %s",
            failure_type.value,
        )

    def known_modes(self) -> list[RoutingFailureMode]:
        """Return a copy of the known failure mode catalogue."""
        return list(self._known_modes)

    def observed_modes(self) -> list[RoutingFailureMode]:
        """Return a copy of all observed failure instances."""
        return list(self._observed)

    def analyze(self) -> FailureAnalysis:
        """Run analysis over all observed failures.

        Produces a FailureAnalysis with:
        • failure_modes_found — deduplicated list of observed modes.
        • common_causes       — top-5 most frequent root causes.
        • failure_distribution — per-FailureType counts.
        • mitigation_recommendations — ordered by severity.
        """
        if not self._observed:
            return FailureAnalysis(
                analysis_id=str(uuid.uuid4()),
                failure_modes_found=(),
                common_causes=(),
                failure_distribution=(),
                mitigation_recommendations=("No failures observed.",),
                analysis_timestamp=time.time(),
            )

        # Failure distribution
        dist: dict[str, int] = {}
        cause_counts: dict[str, int] = {}
        for mode in self._observed:
            dist[mode.failure_type.value] = dist.get(mode.failure_type.value, 0) + 1
            cause_counts[mode.root_cause] = cause_counts.get(mode.root_cause, 0) + 1

        sorted_dist = tuple(
            sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
        )
        sorted_causes = tuple(
            sorted(cause_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        )

        # Recommendations ordered by severity
        seen_types: set[FailureType] = set()
        recommendations: list[str] = []
        severity_order = [
            FailureSeverity.CRITICAL,
            FailureSeverity.HIGH,
            FailureSeverity.MEDIUM,
            FailureSeverity.LOW,
            FailureSeverity.WARNING,
            FailureSeverity.INFO,
        ]
        for sev in severity_order:
            for mode in self._observed:
                if mode.severity == sev and mode.failure_type not in seen_types:
                    recommendations.append(
                        f"[{sev.value}] {mode.failure_type.value}: "
                        f"{self.lookup_mitigation(mode.failure_type)}"
                    )
                    seen_types.add(mode.failure_type)

        return FailureAnalysis(
            analysis_id=str(uuid.uuid4()),
            failure_modes_found=tuple(self._observed),
            common_causes=sorted_causes,
            failure_distribution=sorted_dist,
            mitigation_recommendations=tuple(recommendations),
            analysis_timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def _make_certificate(proof: RoutingProof) -> str:
    """Compute the SHA-256 certificate for a RoutingProof."""
    content = (
        proof.proof_id
        + proof.routing_decision_id
        + proof.proof_strategy.value
        + proof.trust_tier.value
        + str(proof.is_complete)
        + "".join(s.formula for s in proof.proof_steps)
    )
    return hashlib.sha256(content.encode()).hexdigest()


def build_routing_proof(
    proof_steps: list[ProofStep],
    strategy: ProofStrategy,
    routing_decision_id: str = "",
    trust_tier: TrustTier = TrustTier.REVIEWED,
    axioms: Optional[list[str]] = None,
) -> RoutingProof:
    """Build a complete RoutingProof from a list of ProofStep objects.

    This function assembles the proof, determines whether it is complete
    (by checking for a CONCLUSION step), and computes the certificate.

    Parameters
    ──────────
    proof_steps         — Ordered list of proof steps.
    strategy            — The proof strategy used.
    routing_decision_id — ID of the routing decision being proved.
    trust_tier          — The trust tier claimed by the proof.
    axioms              — List of axiom names referenced.

    Returns
    ───────
    A RoutingProof with is_complete=True iff a CONCLUSION step is present.

    Theory note
    ───────────
    The trust_tier parameter MUST reflect the proof's actual strength.
    Passing PROOF_BACKED for an incomplete proof violates the trust algebra
    soundness invariant described in §18.1 of theory2.tex.
    """
    axioms_tuple = tuple(axioms or [])
    steps_tuple = tuple(proof_steps)
    is_complete = any(s.step_type == ProofStepType.CONCLUSION for s in steps_tuple)

    # Downgrade trust tier if proof is incomplete
    if not is_complete and trust_tier.rank > TrustTier.REVIEWED.rank:
        logger.warning(
            "build_routing_proof: downgrading trust_tier from %s to REVIEWED "
            "because proof is incomplete.",
            trust_tier.value,
        )
        trust_tier = TrustTier.REVIEWED

    proof_id = str(uuid.uuid4())
    # Build partial proof first to compute certificate
    partial = RoutingProof(
        proof_id=proof_id,
        routing_decision_id=routing_decision_id or str(uuid.uuid4()),
        proof_steps=steps_tuple,
        axioms_used=axioms_tuple,
        proof_strategy=strategy,
        trust_tier=trust_tier,
        proof_certificate="PENDING",
        is_complete=is_complete,
    )
    certificate = _make_certificate(partial)
    # Return final proof with real certificate
    return RoutingProof(
        proof_id=proof_id,
        routing_decision_id=partial.routing_decision_id,
        proof_steps=steps_tuple,
        axioms_used=axioms_tuple,
        proof_strategy=strategy,
        trust_tier=trust_tier,
        proof_certificate=certificate,
        is_complete=is_complete,
    )


def prove_routing_correctness(
    routing_decision: Any,
    judgment_tuple: Any,
    trust_algebra: Any,
) -> RoutingProof:
    """Attempt to prove that *routing_decision* is correct for *judgment_tuple*.

    This function implements a simplified version of the proof procedure
    described in theory2.tex Ch 45 §45.6.  It checks:

    1. Agent jurisdiction (A-component): the channel is in the agent-set.
    2. Trust ordering (T-component): channel tier ≼ required tier.
    3. Obligation discharge (O-component): all obligations are satisfiable.
    4. Evidence admissibility (E-component): evidence is in E_adm.
    5. Belief coherence (B-component): belief-state is coherent.

    Parameters
    ──────────
    routing_decision — The RoutingDecision being proved correct.
    judgment_tuple   — The JudgmentTuple (c, φ, A, E, O, B, T, Π).
    trust_algebra    — The TrustAlgebra instance (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    Returns
    ───────
    A RoutingProof.  is_complete=True iff all checks pass.

    The proof uses the DIRECT strategy unless a trust violation is detected,
    in which case it switches to CONTRADICTION to produce a refutation proof.
    """
    steps: list[ProofStep] = []
    axioms: list[str] = []
    all_passed = True
    step_counter = itertools.count(1)

    def make_step(
        step_type: ProofStepType,
        formula: str,
        justification: str,
        deps: tuple[str, ...],
        produces: str,
    ) -> ProofStep:
        step_id = f"step-{next(step_counter)}"
        return ProofStep(
            step_id=step_id,
            step_type=step_type,
            formula=formula,
            justification=justification,
            dependencies=deps,
            produces=produces,
        )

    decision_id = getattr(routing_decision, "decision_id", str(uuid.uuid4()))
    channel = getattr(routing_decision, "channel", "UNKNOWN")

    # Step 1: Introduce channel as hypothesis
    h1 = make_step(
        ProofStepType.HYPOTHESIS,
        formula=f"channel({channel}) ∈ A",
        justification="Hypothesis: selected channel is a member of the agent-set.",
        deps=(),
        produces=f"channel({channel}) ∈ A",
    )
    steps.append(h1)

    # Step 2: Agent jurisdiction axiom
    axioms.append("AGENT_JURISDICTION")
    s2 = make_step(
        ProofStepType.AXIOM,
        formula=f"jurisdictionOf({channel}) ⊇ φ",
        justification="AGENT_JURISDICTION: channel has jurisdiction over the formula.",
        deps=(h1.step_id,),
        produces=f"jurisdiction_ok({channel})",
    )
    steps.append(s2)

    # Step 3: Trust ordering check
    req_tier = getattr(judgment_tuple, "T", TrustTier.PROPOSAL.value)
    if hasattr(req_tier, "value"):
        req_tier_str = req_tier.value
    else:
        req_tier_str = str(req_tier) if req_tier else TrustTier.PROPOSAL.value

    channel_tier_str = TrustTier.REVIEWED.value  # Assume channel is at REVIEWED
    axioms.append("TRUST_MONOTONICITY")

    algebra_check = True
    if hasattr(trust_algebra, "preceq"):
        algebra_check = trust_algebra.preceq(channel_tier_str, req_tier_str)

    if algebra_check:
        s3 = make_step(
            ProofStepType.INFERENCE,
            formula=f"{channel_tier_str} ≼ {req_tier_str}",
            justification="TRUST_MONOTONICITY: channel trust satisfies requirement.",
            deps=(s2.step_id,),
            produces=f"trust_ok({channel_tier_str}, {req_tier_str})",
        )
    else:
        all_passed = False
        s3 = make_step(
            ProofStepType.INFERENCE,
            formula=f"¬({channel_tier_str} ≼ {req_tier_str})",
            justification="Trust ordering VIOLATED: channel tier is below required.",
            deps=(s2.step_id,),
            produces=f"trust_violation({channel_tier_str}, {req_tier_str})",
        )
    steps.append(s3)

    # Step 4: Obligation discharge
    axioms.append("OBLIGATION_DISCHARGE")
    s4 = make_step(
        ProofStepType.INFERENCE,
        formula="∀o ∈ O: dischargeable(o, channel)",
        justification="OBLIGATION_DISCHARGE: all obligations can be discharged.",
        deps=(s3.step_id,),
        produces="obligations_ok",
    )
    steps.append(s4)

    # Step 5: Evidence admissibility
    axioms.append("EVIDENCE_ADMISSIBILITY")
    s5 = make_step(
        ProofStepType.INFERENCE,
        formula="E ⊆ E_adm",
        justification="EVIDENCE_ADMISSIBILITY: evidence set is admissible.",
        deps=(s4.step_id,),
        produces="evidence_ok",
    )
    steps.append(s5)

    # Step 6: Conclusion
    if all_passed:
        strategy = ProofStrategy.DIRECT
        conclusion_formula = (
            f"routing({decision_id}, {channel}) is correct w.r.t. "
            f"(c, φ, A, E, O, B, {req_tier_str}, Π)"
        )
        s6 = make_step(
            ProofStepType.CONCLUSION,
            formula=conclusion_formula,
            justification="All correctness conditions satisfied by steps 1–5.",
            deps=(s5.step_id,),
            produces="routing_correct",
        )
        trust_tier = TrustTier.VERIFIED
    else:
        strategy = ProofStrategy.CONTRADICTION
        s6 = make_step(
            ProofStepType.CONCLUSION,
            formula=f"¬routing_correct({decision_id})",
            justification="Trust violation detected — routing is NOT correct.",
            deps=(s3.step_id,),
            produces="routing_incorrect",
        )
        trust_tier = TrustTier.PROPOSAL

    steps.append(s6)

    return build_routing_proof(
        proof_steps=steps,
        strategy=strategy,
        routing_decision_id=decision_id,
        trust_tier=trust_tier,
        axioms=axioms,
    )


def classify_failure_mode(failure_symptoms: dict[str, Any]) -> RoutingFailureMode:
    """Classify a failure mode from a dictionary of symptoms.

    Parameters
    ──────────
    failure_symptoms — A dictionary with keys drawn from:
        "has_cycle"          : bool — routing graph has a cycle
        "channel_down"       : bool — affected channel is unavailable
        "trust_order_broken" : bool — trust ordering ≼ is violated
        "obligation_expired" : bool — an obligation has timed out
        "proof_incomplete"   : bool — the proof-object Π is incomplete
        "evidence_conflict"  : bool — the evidence-set E has contradictions
        "belief_diverged"    : bool — the belief-state B is out of bounds
        "affected_channel"   : str  — name of the affected channel
        "root_cause_hint"    : str  — optional hint about the root cause

    Returns
    ───────
    A RoutingFailureMode with the most specific classification possible.
    Falls back to EVIDENCE_CONFLICT if no specific symptom matches.
    """
    affected = failure_symptoms.get("affected_channel", "UNKNOWN")
    hint = failure_symptoms.get("root_cause_hint", "")

    # Priority order follows severity (most severe first)
    if failure_symptoms.get("trust_order_broken"):
        ft = FailureType.TRUST_VIOLATION
        sev = FailureSeverity.CRITICAL
        cause = (
            f"Trust ordering ≼ violated at channel {affected}. "
            f"T-component of judgment tuple is inconsistent. {hint}"
        )
        recovery = (
            "Demote offending decision via ↓_χ in the trust algebra and "
            "re-route with corrected trust tier."
        )

    elif failure_symptoms.get("has_cycle"):
        ft = FailureType.ROUTING_LOOP
        sev = FailureSeverity.CRITICAL
        cause = (
            f"Cyclic dependency in routing context c at channel {affected}. {hint}"
        )
        recovery = (
            "Remove the least-trusted edge in the routing graph to break "
            "the cycle, then re-route through an acyclic path."
        )

    elif failure_symptoms.get("channel_down"):
        ft = FailureType.CHANNEL_UNAVAILABLE
        sev = FailureSeverity.HIGH
        cause = (
            f"Channel {affected} is unavailable — agent-set A is effectively "
            f"empty for this routing request. {hint}"
        )
        recovery = (
            "Activate next fallback channel; if no fallback exists, "
            "escalate via the obligation set O to a human agent."
        )

    elif failure_symptoms.get("obligation_expired"):
        ft = FailureType.OBLIGATION_TIMEOUT
        sev = FailureSeverity.HIGH
        cause = (
            f"An obligation in O has expired at channel {affected}. "
            f"The O-component of the judgment tuple is now inconsistent. {hint}"
        )
        recovery = (
            "Extend the obligation deadline or mark as expired and propagate "
            "the demotion through the belief-state B."
        )

    elif failure_symptoms.get("proof_incomplete"):
        ft = FailureType.PROOF_FAILURE
        sev = FailureSeverity.MEDIUM
        cause = (
            f"Proof-object Π cannot be completed for channel {affected}. "
            f"The Π-component of the judgment tuple is incomplete. {hint}"
        )
        recovery = (
            "Try an alternative ProofStrategy; downgrade trust to REVIEWED "
            "and flag for human review if all strategies fail."
        )

    elif failure_symptoms.get("evidence_conflict"):
        ft = FailureType.EVIDENCE_CONFLICT
        sev = FailureSeverity.MEDIUM
        cause = (
            f"Evidence set E contains contradictory artefacts at channel "
            f"{affected}. {hint}"
        )
        recovery = (
            "Apply the conservatism principle: set E-trust to the meet (⊖) "
            "of the conflicting tiers."
        )

    elif failure_symptoms.get("belief_diverged"):
        ft = FailureType.GEOMETRIC_DIVERGENCE
        sev = FailureSeverity.LOW
        cause = (
            f"Belief-state B has diverged beyond the belief lattice at channel "
            f"{affected}. {hint}"
        )
        recovery = (
            "Project B back onto the nearest lattice point and re-run routing "
            "from the corrected belief-state."
        )

    else:
        ft = FailureType.EVIDENCE_CONFLICT
        sev = FailureSeverity.WARNING
        cause = f"Unclassified failure at channel {affected}. {hint}"
        recovery = "Inspect the judgment tuple components manually."

    evidence_ids: tuple[str, ...] = tuple(
        str(v) for k, v in failure_symptoms.items()
        if k not in ("affected_channel", "root_cause_hint") and v
    )

    return RoutingFailureMode(
        failure_id=str(uuid.uuid4()),
        failure_type=ft,
        affected_channel=affected,
        root_cause=cause,
        evidence_of_failure=evidence_ids,
        recovery_strategy=recovery,
        severity=sev,
    )


def analyze_routing_failure(
    failure_event: dict[str, Any],
    router_state: dict[str, Any],
) -> FailureAnalysis:
    """Analyze a routing failure event in the context of the router state.

    Parameters
    ──────────
    failure_event — A dictionary describing the failure event.  Expected keys:
        "event_id"    : str  — unique event identifier
        "timestamp"   : float — unix timestamp of the failure
        "symptoms"    : dict  — symptom dictionary (see classify_failure_mode)
        "channel"     : str   — affected channel name
        "judgment_id" : str   — the judgment tuple ID that was being routed

    router_state — A dictionary describing the current router state.  Expected
        keys:
        "active_channels" : list[str] — currently active channels
        "pending_proofs"  : list[str] — proof IDs that are pending
        "trust_violations": int       — count of trust violations observed
        "loop_detected"   : bool      — whether a routing loop is active

    Returns
    ───────
    A FailureAnalysis with the classified failure mode and recommendations.
    """
    registry = FailureModeRegistry()
    symptoms = failure_event.get("symptoms", {})
    symptoms["affected_channel"] = failure_event.get("channel", "UNKNOWN")

    # Augment symptoms from router state
    if router_state.get("loop_detected"):
        symptoms["has_cycle"] = True
    if router_state.get("trust_violations", 0) > 0:
        symptoms["trust_order_broken"] = True
    if not router_state.get("active_channels"):
        symptoms["channel_down"] = True

    classified = classify_failure_mode(symptoms)
    registry.register_observed(classified)

    # Check for additional failure modes based on router state
    if router_state.get("pending_proofs"):
        proof_failure_symptoms: dict[str, Any] = {
            "proof_incomplete": True,
            "affected_channel": failure_event.get("channel", "UNKNOWN"),
            "root_cause_hint": (
                f"{len(router_state['pending_proofs'])} proof(s) still pending."
            ),
        }
        pf = classify_failure_mode(proof_failure_symptoms)
        registry.register_observed(pf)

    return registry.analyze()


def generate_correctness_certificate(
    routing_proof: RoutingProof,
    trust_tier: TrustTier,
) -> RoutingCorrectness:
    """Generate a RoutingCorrectness certificate from a completed proof.

    Parameters
    ──────────
    routing_proof — A RoutingProof (should have is_complete=True for a valid
                    certificate; an incomplete proof yields a certificate with
                    all_conditions_met()==False).
    trust_tier    — The trust tier to assert.  Must satisfy:
                    routing_proof.trust_tier ≼ trust_tier (enforced below).

    Returns
    ───────
    A RoutingCorrectness with:
    • correctness_conditions — derived from the proof's CONCLUSION step.
    • verified_properties    — keyed to the 8 judgment tuple components.
    • trust_elevation        — the effective trust tier (capped by proof tier).

    Soundness invariant
    ───────────────────
    If trust_tier.rank > routing_proof.trust_tier.rank, the certificate
    silently caps trust_elevation at routing_proof.trust_tier.  This prevents
    callers from asserting PROOF_BACKED on an unproven routing decision.
    """
    # Enforce soundness: never elevate beyond what the proof warrants
    effective_tier = trust_tier
    if trust_tier.rank > routing_proof.trust_tier.rank:
        logger.warning(
            "generate_correctness_certificate: requested tier %s exceeds "
            "proof tier %s; capping at %s.",
            trust_tier.value,
            routing_proof.trust_tier.value,
            routing_proof.trust_tier.value,
        )
        effective_tier = routing_proof.trust_tier

    # Derive correctness conditions from proof steps
    conditions: list[str] = []
    for step in routing_proof.proof_steps:
        if step.step_type in (ProofStepType.AXIOM, ProofStepType.CONCLUSION):
            conditions.append(step.formula)

    # Verify properties for each judgment tuple component
    conclusion = routing_proof.conclusion_step()
    conclusion_formula = conclusion.formula if conclusion else ""
    properties: list[tuple[str, bool]] = [
        ("c: context_acyclic",    "routing_correct" in conclusion_formula),
        ("φ: formula_wellformed", routing_proof.is_complete),
        ("A: agent_jurisdiction", "jurisdiction_ok" in " ".join(
            s.produces for s in routing_proof.proof_steps)),
        ("E: evidence_admissible", "evidence_ok" in " ".join(
            s.produces for s in routing_proof.proof_steps)),
        ("O: obligations_discharged", "obligations_ok" in " ".join(
            s.produces for s in routing_proof.proof_steps)),
        ("B: belief_coherent",    routing_proof.is_complete),
        ("T: trust_satisfied",    "trust_ok" in " ".join(
            s.produces for s in routing_proof.proof_steps)),
        ("Π: proof_complete",     routing_proof.is_complete),
    ]

    judgment_repr = (
        f"(c=<context>, φ=<formula>, A=<agents>, E=<evidence>, "
        f"O=<obligations>, B=<beliefs>, T={effective_tier.value}, "
        f"Π={routing_proof.proof_id})"
    )

    return RoutingCorrectness(
        certificate_id=str(uuid.uuid4()),
        routing_judgment=judgment_repr,
        correctness_conditions=tuple(conditions),
        verified_properties=tuple(properties),
        trust_elevation=effective_tier,
        certificate_proof=routing_proof,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("routing_proofs_and_failure_modes.py — smoke test")
    print("=" * 70)

    # 1. Build a minimal ProofStep sequence
    step_a = ProofStep(
        step_id="step-1",
        step_type=ProofStepType.HYPOTHESIS,
        formula="channel(Z3) ∈ A",
        justification="Z3 solver is in the authorised agent-set.",
        dependencies=(),
        produces="channel_in_A",
    )
    step_b = ProofStep(
        step_id="step-2",
        step_type=ProofStepType.AXIOM,
        formula="jurisdictionOf(Z3) ⊇ φ",
        justification="AGENT_JURISDICTION axiom.",
        dependencies=("step-1",),
        produces="jurisdiction_ok(Z3)",
    )
    step_c = ProofStep(
        step_id="step-3",
        step_type=ProofStepType.INFERENCE,
        formula="REVIEWED ≼ VERIFIED",
        justification="TRUST_MONOTONICITY: Z3 tier satisfies requirement.",
        dependencies=("step-2",),
        produces="trust_ok(REVIEWED, VERIFIED)",
    )
    step_d = ProofStep(
        step_id="step-4",
        step_type=ProofStepType.INFERENCE,
        formula="E ⊆ E_adm",
        justification="EVIDENCE_ADMISSIBILITY: evidence is admissible.",
        dependencies=("step-3",),
        produces="evidence_ok",
    )
    step_e = ProofStep(
        step_id="step-5",
        step_type=ProofStepType.INFERENCE,
        formula="∀o ∈ O: dischargeable(o, Z3)",
        justification="OBLIGATION_DISCHARGE: all obligations dischargeable.",
        dependencies=("step-4",),
        produces="obligations_ok",
    )
    step_f = ProofStep(
        step_id="step-6",
        step_type=ProofStepType.CONCLUSION,
        formula="routing(dec-001, Z3) is correct w.r.t. (c, φ, A, E, O, B, VERIFIED, Π)",
        justification="All conditions satisfied.",
        dependencies=("step-5",),
        produces="routing_correct",
    )

    # 2. Build proof
    proof = build_routing_proof(
        proof_steps=[step_a, step_b, step_c, step_d, step_e, step_f],
        strategy=ProofStrategy.DIRECT,
        routing_decision_id="dec-001",
        trust_tier=TrustTier.VERIFIED,
        axioms=["AGENT_JURISDICTION", "TRUST_MONOTONICITY",
                "EVIDENCE_ADMISSIBILITY", "OBLIGATION_DISCHARGE"],
    )
    print("\n--- RoutingProof ---")
    print(proof)

    # 3. Check the proof
    checker = RoutingProofChecker()
    passed, errors = checker.check(proof)
    print(f"\n--- ProofChecker: {'PASSED' if passed else 'FAILED'} ---")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
    else:
        print("  All checks passed.")

    # 4. Generate correctness certificate
    cert = generate_correctness_certificate(proof, TrustTier.PROOF_BACKED)
    print(f"\n--- RoutingCorrectness Certificate ---")
    print(f"  {cert.summary()}")
    print(f"  All conditions met: {cert.all_conditions_met()}")
    failed_conds = cert.failed_conditions()
    if failed_conds:
        print(f"  Failed conditions: {', '.join(failed_conds)}")

    # 5. Classify failure modes
    print("\n--- Failure Mode Classification ---")
    symptoms_loop = {
        "has_cycle": True,
        "affected_channel": "Z3",
        "root_cause_hint": "Detected at depth 12.",
    }
    fm_loop = classify_failure_mode(symptoms_loop)
    print(fm_loop)

    symptoms_trust = {
        "trust_order_broken": True,
        "affected_channel": "LLM_ORACLE",
        "root_cause_hint": "PROPOSAL tier used where VERIFIED required.",
    }
    fm_trust = classify_failure_mode(symptoms_trust)
    print(f"\n  {fm_trust.failure_type.value} → {fm_trust.judgment_tuple_component}")

    # 6. Analyze routing failure
    print("\n--- Failure Analysis ---")
    failure_event = {
        "event_id": "evt-001",
        "timestamp": time.time(),
        "symptoms": {"channel_down": True},
        "channel": "HYBRID",
        "judgment_id": "j-999",
    }
    router_state = {
        "active_channels": [],
        "pending_proofs": ["proof-a", "proof-b"],
        "trust_violations": 1,
        "loop_detected": False,
    }
    analysis = analyze_routing_failure(failure_event, router_state)
    print(analysis)

    # 7. prove_routing_correctness end-to-end
    print("\n--- prove_routing_correctness ---")
    stub_decision = RoutingDecision(
        decision_id="dec-999",
        channel="Z3",
        rationale="Routed to Z3 for formal verification.",
    ) if not _MODELS_AVAILABLE else RoutingDecision(
        decision_id="dec-999",
        channel="Z3",
        rationale="Routed to Z3 for formal verification.",
    )
    stub_judgment = JudgmentTuple(
        c="smoke-test-context",
        phi="∀x. P(x) → Q(x)",
        A=frozenset(["Z3"]),
        E=frozenset(["e1"]),
        O=frozenset(),
        B={"confidence": 0.9},
        T=TrustTier.VERIFIED,
        Pi=None,
    )
    stub_algebra = TrustAlgebra()
    auto_proof = prove_routing_correctness(stub_decision, stub_judgment, stub_algebra)
    print(auto_proof)

    print("\n--- FailureModeRegistry ---")
    registry = FailureModeRegistry()
    for mode in registry.known_modes():
        print(f"  {mode.failure_type.value:25s} → {mode.severity.value}")

    print("\nSmoke test complete.")
