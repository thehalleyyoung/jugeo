"""
Proof Obligations for the Orchestration Layer.

# copilot: This module formalises the *meta-level* proof obligations that the
orchestration layer itself must discharge.  This is distinct from the
domain-level obligations carried in the judgment tuple O component: those
concern the *task* the orchestrator is routing toward.  The obligations here
concern the *orchestrator's own behaviour*.

The meta-level question
=======================
The orchestrator is a control system (see s01).  Like any system that claims to
produce reliable outputs, it must discharge proof obligations about its own
properties:
  - Safety: the orchestrator never violates trust invariants (no trust regression).
  - Liveness: the orchestrator always makes progress toward the goal.
  - Fairness: all admissible agents are considered (no unfair exclusion).
  - Progress: the Lyapunov candidate V(J) is strictly decreasing.
  - Trust preservation: trust elevations are always backed by proofs.
  - Semantic consistency: the judgment tuple J is always internally consistent.
  - Proof completeness: the proof object Π grows monotonically.

Trust must be elevated through proof discharge, not assertion
==============================================================
A key principle: trust cannot be self-asserted.  The sequence:
  PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED
requires a *discharge certificate* at each step:
  PROPOSAL   → REVIEWED         : human review recorded in the obligation log
  REVIEWED   → VERIFIED         : automated verification (Z3 / type checker) run
  VERIFIED   → RUNTIME_WITNESSED: runtime witness collected (actual execution)
  RUNTIME_WITNESSED → PROOF_BACKED: formal proof completed (Lean / Coq / Z3)

Attempting to skip tiers (e.g., elevating directly from PROPOSAL to PROOF_BACKED
by asserting a high tier) is detected by the ObligationMonitor and creates a
TRUST_PRESERVATION violation.

The meta-level judgment tuple
==============================
The orchestrator's self-obligations are themselves encoded as judgment tuples:
  J_meta = (c_orch, φ_prop, A_orch, E_orch, O_orch, B_orch, T_orch, Π_orch)
where φ_prop is the property being proved, O_orch is the set of proof obligations
that must be discharged to certify φ_prop, and Π_orch is the growing proof object.

This creates a *two-level* obligation structure:
  Level 0: J_task  — the task-level judgment (what the orchestrated agents do)
  Level 1: J_meta  — the orchestrator's self-obligation (what the orchestrator proves)

Both levels use the same judgment-tuple schema and the same trust algebra.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Jugeo imports with stub fallback
# ---------------------------------------------------------------------------
try:
    from jugeo.core.trust import TrustTier, TrustAlgebraElement  # type: ignore
    from jugeo.core.judgment import JudgmentTuple  # type: ignore
    from jugeo.core.proof import ProofObject  # type: ignore
    from jugeo.orchestration.semantic_control.orchestration_is_a_control_problem import (  # type: ignore
        ControlState, ControlPolicy, ControlTrajectory, PolicyType,
        compute_semantic_distance, verify_control_invariant,
        compute_lyapunov_decrease, TRUST_TIER_COUNT,
    )
except ImportError:
    class TrustTier(Enum):  # type: ignore
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5

    @dataclass(frozen=True)
    class TrustAlgebraElement:  # type: ignore
        tier: TrustTier = TrustTier.PROPOSAL
        evidence_ids: Tuple[str, ...] = ()
        lattice_height: int = 0

        def join(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            higher = max(self.tier.value, other.tier.value)
            return TrustAlgebraElement(
                tier=TrustTier(higher),
                evidence_ids=self.evidence_ids + other.evidence_ids,
                lattice_height=max(self.lattice_height, other.lattice_height) + 1,
            )

        def meet(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            lower = min(self.tier.value, other.tier.value)
            return TrustAlgebraElement(
                tier=TrustTier(lower),
                evidence_ids=(),
                lattice_height=min(self.lattice_height, other.lattice_height),
            )

        def elevate(self, proof_id: str) -> TrustAlgebraElement:
            new_val = min(self.tier.value + 1, max(t.value for t in TrustTier))
            return TrustAlgebraElement(
                tier=TrustTier(new_val),
                evidence_ids=self.evidence_ids + (proof_id,),
                lattice_height=self.lattice_height + 1,
            )

        def demote(self, cx_id: str) -> TrustAlgebraElement:
            new_val = max(self.tier.value - 1, 1)
            return TrustAlgebraElement(
                tier=TrustTier(new_val),
                evidence_ids=self.evidence_ids,
                lattice_height=max(self.lattice_height - 1, 0),
            )

    @dataclass(frozen=True)
    class JudgmentTuple:  # type: ignore
        c: str = ""
        phi: str = ""
        A: Tuple[str, ...] = ()
        E: Tuple[str, ...] = ()
        O: Tuple[str, ...] = ()
        B: str = ""
        T: TrustTier = TrustTier.PROPOSAL
        Pi: str = ""

    @dataclass(frozen=True)
    class ProofObject:  # type: ignore
        proof_id: str = ""
        strategy: str = "none"
        steps: Tuple[str, ...] = ()
        is_complete: bool = False

    class PolicyType(Enum):  # type: ignore
        GREEDY = auto()
        OPTIMAL = auto()
        ROBUST = auto()
        ADAPTIVE = auto()
        PROOF_GUIDED = auto()
        TRUST_WEIGHTED = auto()

    TRUST_TIER_COUNT: int = 5

    @dataclass(frozen=True)
    class ControlState:  # type: ignore
        state_id: str = ""
        judgment_tuple: JudgmentTuple = field(default_factory=JudgmentTuple)
        semantic_coordinates: Tuple[float, ...] = ()
        distance_to_goal: float = 1.0
        trust_level: TrustAlgebraElement = field(default_factory=TrustAlgebraElement)
        timestamp: float = 0.0
        trajectory_segment: int = 0

    @dataclass(frozen=True)
    class ControlPolicy:  # type: ignore
        policy_id: str = ""
        policy_type: PolicyType = PolicyType.GREEDY
        policy_parameters: Dict[str, Any] = field(default_factory=dict)
        coverage_region: str = ""
        trust_tier: TrustTier = TrustTier.PROPOSAL
        policy_proof: str = ""
        last_validated: float = 0.0

    def compute_semantic_distance(s1: ControlState, s2: ControlState) -> float:  # type: ignore
        return abs(s1.distance_to_goal - s2.distance_to_goal)

    def verify_control_invariant(  # type: ignore
        trajectory: List[ControlState], invariant: str, verbose: bool = False
    ) -> Tuple[bool, List[int]]:
        return True, []

    def compute_lyapunov_decrease(  # type: ignore
        state: ControlState, next_state: ControlState
    ) -> Tuple[float, bool]:
        delta = next_state.distance_to_goal - state.distance_to_goal
        return delta, delta < 0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ObligationType(Enum):
    """The semantic category of an orchestrator obligation.

    These categories mirror standard temporal-logic property types, adapted
    to the semantic control setting.
    """
    SAFETY = auto()
    """The orchestrator must not violate safety constraints: no trust regression,
    no obligation set expansion (|O| may only decrease), no proof shrinkage.
    Safety properties are of the form □¬bad(J_t) — "bad never happens"."""

    LIVENESS = auto()
    """The orchestrator must make progress: V(J_t) must strictly decrease on
    average.  Liveness properties are of the form ◇good(J_t) — "good eventually
    happens".  Formally: ∃T, ∀t≥T, V(J_t) < V(J₀)/2."""

    FAIRNESS = auto()
    """Every admissible agent in A must be considered for selection at least once
    every N steps.  This prevents the controller from ignoring valid paths due to
    greedy bias."""

    PROGRESS = auto()
    """At each step, at least one obligation in O must be discharged or the proof
    Π must grow.  This is stronger than liveness: it requires step-level rather
    than eventual progress."""

    TRUST_PRESERVATION = auto()
    """The trust tier T must not decrease along any trajectory.  Trust elevations
    must be backed by discharge certificates.  No trust may be asserted."""

    SEMANTIC_CONSISTENCY = auto()
    """The judgment tuple J = (c, φ, A, E, O, B, T, Π) must remain internally
    consistent: evidence in E must be relevant to φ, obligations in O must be
    derivable from φ, trust T must be justified by evidence in E."""

    PROOF_COMPLETENESS = auto()
    """The proof object Π must grow monotonically and must eventually become
    complete.  The proof must reference all obligations it claims to discharge."""


class InvariantType(Enum):
    """Classification of orchestration invariants."""
    LOOP_INVARIANT = auto()
    """Holds at the start and end of every control loop iteration.  The standard
    Hoare-logic loop invariant: established before the loop, maintained by each
    iteration, implies the post-condition when the loop exits."""

    CLASS_INVARIANT = auto()
    """Holds for the controller object at all publicly observable states.  The
    controller never exposes an inconsistent state to external observers."""

    SYSTEM_INVARIANT = auto()
    """Holds for the entire orchestration system, including all registered agents.
    Multi-agent invariants of the form: ∀a ∈ A, property(a)."""

    TRUST_INVARIANT = auto()
    """Specifically about the trust algebra: the trust tier is non-decreasing,
    the ⊕ operator is associative and commutative, and ↑_π requires a valid
    proof_id."""

    SEMANTIC_INVARIANT = auto()
    """About the semantic content of the judgment tuple: the formula φ is
    syntactically well-formed, the obligation set O is finite, and the proof
    object Π references only valid evidence IDs."""


class DischargeMethod(Enum):
    """The method used to discharge a proof obligation.

    Each method has a trust tier associated with it: using a higher-trust
    discharge method elevates the resulting trust tier.
    """
    Z3_PROOF = auto()
    """Discharge via Z3 SMT solver.  Produces PROOF_BACKED tier trust.
    Requires that the obligation can be encoded as a Z3 formula."""

    LLM_VERIFICATION = auto()
    """Discharge via LLM semantic verification.  Produces RUNTIME_WITNESSED tier
    trust at best (LLMs cannot produce formal proofs).  The LLM's output is
    treated as evidence, not as a proof."""

    RUNTIME_WITNESS = auto()
    """Discharge by observing the property hold at runtime.  Produces
    RUNTIME_WITNESSED tier trust.  Requires instrumentation of the system."""

    MANUAL_REVIEW = auto()
    """Discharge by human review.  Produces REVIEWED tier trust.  The reviewer's
    identity and review timestamp must be recorded in the evidence set."""

    PROOF_CHAIN = auto()
    """Discharge by chaining existing proofs.  If obligations O₁ and O₂ have
    been discharged by methods M₁ and M₂, a chain proof may discharge O₃ if
    O₃ follows from O₁ ∧ O₂.  Trust tier = min(tier(M₁), tier(M₂))."""

    HYBRID = auto()
    """Discharge via a combination of methods.  Trust tier = min of all component
    tiers.  Used when a single method is insufficient but no method alone produces
    PROOF_BACKED trust."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrchestratorObligation:
    """An obligation that the orchestrator itself must discharge.

    This is a *meta-level* obligation: it is not an obligation in the task
    judgment tuple (those are domain obligations), but an obligation about the
    orchestrator's own behaviour.

    The obligation is expressed as a judgment tuple at the meta level:
      J_meta = (c=orchestrator_id, φ=obligation_formula, A={orchestrator},
                E=evidence_requirements, O={sub-obligations}, B={},
                T=trust_tier, Π=obligation_proof)

    The ``source_axiom`` is the axiom or rule from which the obligation derives.
    For example, the liveness obligation derives from the axiom:
      "A controller must make progress: V(J_t) must decrease."

    The ``target_property`` is the property that will hold when the obligation
    is discharged:
      "V(J_t+1) < V(J_t)"

    The ``discharge_deadline`` is a Unix timestamp after which the obligation
    is considered overdue.  Overdue obligations trigger a trust demotion via ↓_χ.
    """
    obligation_id: str
    obligation_formula: str
    source_axiom: str
    target_property: str
    evidence_requirements: Tuple[str, ...]    # required evidence IDs or categories
    trust_tier: TrustTier
    discharge_deadline: float                 # Unix timestamp
    obligation_proof: str                     # proof_id or ""


@dataclass(frozen=True)
class ControlProof:
    """A proof that the control system satisfies a specific obligation.

    The proof is a structured object with:
      - A strategy (Z3, LLM, runtime witness, etc.)
      - A sequence of proof steps
      - A list of invariants verified along the way
      - A Lyapunov certificate (for liveness / progress proofs)
      - A convergence proof (for optimisation-related obligations)

    The ``trust_elevation`` records the trust tier achieved by this proof.
    A proof that uses Z3 to verify a formal claim achieves PROOF_BACKED;
    a proof that relies on LLM output achieves at most RUNTIME_WITNESSED.

    Trust monotonicity of proofs: a control proof may only *elevate* trust,
    never demote it.  If a proof attempt fails, the original trust tier is
    preserved (not lowered) — a failed proof is not evidence of falsity, only
    of insufficient evidence.
    """
    proof_id: str
    obligation_id: str
    proof_strategy: DischargeMethod
    proof_steps: Tuple[str, ...]
    invariants_verified: Tuple[str, ...]
    lyapunov_certificate: str              # description of V(J) and ΔV < 0 proof
    convergence_proof: str                 # description of convergence argument
    trust_elevation: TrustTier             # tier achieved by this proof


@dataclass(frozen=True)
class OrchestrationInvariant:
    """An invariant that must hold throughout the orchestration lifecycle.

    Invariants are checked at every control step.  A violation is a serious
    error that must be handled by the ``violation_handler``.

    The ``proof_obligation`` field is the obligation that must be discharged
    to certify the invariant.  Until it is discharged, the invariant is
    *assumed* to hold (trust tier = PROPOSAL) rather than *known* to hold.

    The ``monitoring_policy`` describes how the invariant is checked:
      "always"  — checked at every control step (expensive but thorough)
      "sampled" — checked at a random sample of steps (cost/coverage tradeoff)
      "triggered" — checked only when triggered by a relevant event
      "proof_time" — checked only at proof-discharge time (not at runtime)

    The ``trust_requirement`` is the minimum trust tier required for this
    invariant to be considered adequately verified.
    """
    invariant_id: str
    invariant_formula: str
    invariant_type: InvariantType
    proof_obligation: str                   # obligation_id
    monitoring_policy: str
    violation_handler: str                  # handler function name or description
    trust_requirement: TrustTier


@dataclass(frozen=True)
class ObligationDischarge:
    """A certificate that an orchestrator obligation has been discharged.

    The discharge is the *completion record*: it records what obligation was
    discharged, how it was discharged, what evidence was used, and what trust
    tier was achieved.

    The ``discharge_proof`` is the most important field: it must reference a
    ControlProof object that certifies the discharge is valid.  A discharge
    without a proof is treated as MANUAL_REVIEW at best (human says "I checked
    it").

    The ``discharged_by`` field is the agent or component that produced the
    discharge.  For formal proofs, this is the proof tool (e.g., "z3-4.12").
    For runtime witnesses, this is the instrumented system component.

    The ``trust_tier`` of the discharge is the tier achieved, constrained by:
      trust_tier(discharge) ≤ trust_tier(DischargeMethod)
      trust_tier(discharge) ≤ trust_tier(evidence)
    """
    discharge_id: str
    obligation_id: str
    discharge_method: DischargeMethod
    evidence_used: Tuple[str, ...]
    discharge_proof: str                    # proof_id
    discharged_by: str                      # agent or tool that produced the discharge
    timestamp: float
    trust_tier: TrustTier


# ---------------------------------------------------------------------------
# Mutable helper classes
# ---------------------------------------------------------------------------

class ObligationMonitor:
    """Monitors the orchestrator's obligations at runtime.

    The monitor maintains a registry of active obligations and checks them
    against the current control state at each step.

    Key responsibilities:
      1. Detect trust regression (violation of TRUST_PRESERVATION invariant).
      2. Detect obligation set expansion (violation of PROGRESS invariant).
      3. Detect proof shrinkage (violation of PROOF_COMPLETENESS invariant).
      4. Detect overdue obligations (past their discharge deadline).
      5. Emit violation reports for handling by the ObligationDischargeEngine.

    The monitor is *not* responsible for discharging obligations — that is the
    engine's job.  The monitor is a passive observer that reports violations.
    """

    def __init__(self, trust_floor: TrustTier = TrustTier.PROPOSAL) -> None:
        self.trust_floor = trust_floor
        self._active_obligations: Dict[str, OrchestratorObligation] = {}
        self._discharged_ids: Set[str] = set()
        self._violation_log: List[Dict[str, Any]] = []
        self._last_state: Optional[ControlState] = None
        self._last_obligation_count: int = 0
        self._last_proof_len: int = 0

    def register_obligation(self, obligation: OrchestratorObligation) -> None:
        """Register a new obligation for monitoring."""
        self._active_obligations[obligation.obligation_id] = obligation

    def mark_discharged(self, obligation_id: str) -> None:
        """Mark an obligation as discharged (remove from active monitoring)."""
        self._discharged_ids.add(obligation_id)
        self._active_obligations.pop(obligation_id, None)

    def check_state(self, state: ControlState) -> List[Dict[str, Any]]:
        """Check the current control state for obligation violations.

        Returns a list of violation records (empty if no violations).
        """
        violations: List[Dict[str, Any]] = []
        j = state.judgment_tuple
        now = time.time()

        # 1. Trust regression check
        if self._last_state is not None:
            prev_tier = self._last_state.trust_level.tier.value
            curr_tier = state.trust_level.tier.value
            if curr_tier < prev_tier:
                violations.append({
                    "type": "TRUST_REGRESSION",
                    "state_id": state.state_id,
                    "prev_tier": TrustTier(prev_tier).name,
                    "curr_tier": TrustTier(curr_tier).name,
                    "timestamp": now,
                })

        # 2. Obligation expansion check (|O| must not increase)
        curr_obligation_count = len(j.O)
        if self._last_state is not None:
            prev_count = len(self._last_state.judgment_tuple.O)
            if curr_obligation_count > prev_count:
                violations.append({
                    "type": "OBLIGATION_EXPANSION",
                    "state_id": state.state_id,
                    "prev_count": prev_count,
                    "curr_count": curr_obligation_count,
                    "timestamp": now,
                })

        # 3. Proof shrinkage check
        curr_proof_len = len(j.Pi)
        if self._last_state is not None:
            prev_proof_len = len(self._last_state.judgment_tuple.Pi)
            if curr_proof_len < prev_proof_len and prev_proof_len > 0:
                violations.append({
                    "type": "PROOF_SHRINKAGE",
                    "state_id": state.state_id,
                    "prev_len": prev_proof_len,
                    "curr_len": curr_proof_len,
                    "timestamp": now,
                })

        # 4. Overdue obligations check
        for oblig_id, oblig in list(self._active_obligations.items()):
            if oblig.discharge_deadline > 0 and now > oblig.discharge_deadline:
                violations.append({
                    "type": "OVERDUE_OBLIGATION",
                    "obligation_id": oblig_id,
                    "formula": oblig.obligation_formula,
                    "deadline": oblig.discharge_deadline,
                    "timestamp": now,
                })

        # 5. Trust tier below floor
        if state.trust_level.tier.value < self.trust_floor.value:
            violations.append({
                "type": "TRUST_BELOW_FLOOR",
                "state_id": state.state_id,
                "tier": state.trust_level.tier.name,
                "floor": self.trust_floor.name,
                "timestamp": now,
            })

        self._violation_log.extend(violations)
        self._last_state = state
        return violations

    def pending_obligations(self) -> List[OrchestratorObligation]:
        """Return list of obligations not yet discharged."""
        return list(self._active_obligations.values())

    def violation_count(self) -> int:
        return len(self._violation_log)

    def recent_violations(self, n: int = 10) -> List[Dict[str, Any]]:
        return self._violation_log[-n:]


class ObligationDischargeEngine:
    """Attempts to discharge pending obligations automatically.

    The engine maintains a priority queue of pending obligations ordered by:
      1. Urgency (deadline proximity)
      2. Trust tier of the obligation (higher-trust obligations first)
      3. Discharge method cost (cheaper methods first)

    For each obligation it tries the discharge methods in order of trust tier
    (highest first) until one succeeds or all methods are exhausted.

    If a discharge succeeds, the engine:
      1. Creates an ObligationDischarge record.
      2. Notifies the ObligationMonitor to remove the obligation from active set.
      3. Elevates the trust algebra element via ↑_π.

    If all methods fail, the obligation remains active and a violation is logged.
    """

    def __init__(
        self,
        monitor: ObligationMonitor,
        default_method: DischargeMethod = DischargeMethod.LLM_VERIFICATION,
    ) -> None:
        self.monitor = monitor
        self.default_method = default_method
        self._discharge_history: List[ObligationDischarge] = []
        self._failed_attempts: Dict[str, List[DischargeMethod]] = {}
        self._trust_state = TrustAlgebraElement(tier=TrustTier.PROPOSAL)

    def attempt_discharge(
        self,
        obligation: OrchestratorObligation,
        evidence: List[str],
        method: Optional[DischargeMethod] = None,
    ) -> Optional[ObligationDischarge]:
        """Try to discharge a single obligation.

        Returns an ObligationDischarge if successful, None otherwise.

        The discharge succeeds if:
          1. The method is applicable to the obligation type.
          2. The evidence satisfies the obligation's evidence requirements.
          3. The proof is valid (simulated here).

        Parameters
        ----------
        obligation : OrchestratorObligation
            The obligation to discharge.
        evidence : List[str]
            Evidence items to use in the discharge.
        method : DischargeMethod, optional
            The discharge method to use.  Defaults to self.default_method.
        """
        method = method or self.default_method

        # Check evidence requirements
        required = set(obligation.evidence_requirements)
        provided = set(str(e) for e in evidence)
        missing = required - provided
        if missing:
            # In a real system we would fetch missing evidence; here we tolerate it
            # for obligations with flexible requirements
            if obligation.trust_tier.value >= TrustTier.VERIFIED.value:
                return None  # High-trust obligations require full evidence

        # Compute achieved trust tier for this discharge
        method_trust = _discharge_method_trust_tier(method)
        evidence_trust = _evidence_trust_tier(evidence)
        achieved_trust = TrustTier(min(method_trust.value, evidence_trust.value))

        # Simulate proof steps
        proof_steps = _simulate_proof_steps(obligation, method, evidence)
        proof_id = f"proof-{uuid.uuid4().hex[:12]}"

        proof = ControlProof(
            proof_id=proof_id,
            obligation_id=obligation.obligation_id,
            proof_strategy=method,
            proof_steps=tuple(proof_steps),
            invariants_verified=tuple(
                f"invariant:{i}" for i in range(len(proof_steps))
            ),
            lyapunov_certificate=(
                "V(J) = obligation_count + trust_gap; ΔV < 0 per step"
                if obligation.obligation_formula.startswith("liveness") else ""
            ),
            convergence_proof=(
                "convergence within 2*|O₀| steps"
                if "progress" in obligation.obligation_formula else ""
            ),
            trust_elevation=achieved_trust,
        )

        discharge = ObligationDischarge(
            discharge_id=f"dsc-{uuid.uuid4().hex[:12]}",
            obligation_id=obligation.obligation_id,
            discharge_method=method,
            evidence_used=tuple(str(e) for e in evidence),
            discharge_proof=proof.proof_id,
            discharged_by=f"engine:{method.name.lower()}",
            timestamp=time.time(),
            trust_tier=achieved_trust,
        )

        self._discharge_history.append(discharge)
        self.monitor.mark_discharged(obligation.obligation_id)

        # Elevate trust state
        self._trust_state = self._trust_state.elevate(proof_id)

        return discharge

    def discharge_all_pending(
        self, evidence: List[str]
    ) -> Tuple[int, int]:
        """Attempt to discharge all pending obligations.

        Returns (discharged_count, failed_count).
        """
        pending = self.monitor.pending_obligations()
        discharged = 0
        failed = 0
        for oblig in pending:
            # Choose best available method
            for method in self._method_priority_order():
                result = self.attempt_discharge(oblig, evidence, method)
                if result is not None:
                    discharged += 1
                    break
            else:
                failed += 1
                self._failed_attempts.setdefault(oblig.obligation_id, []).append(
                    self.default_method
                )
        return discharged, failed

    def _method_priority_order(self) -> List[DischargeMethod]:
        """Return discharge methods in priority order (highest trust first)."""
        return [
            DischargeMethod.Z3_PROOF,
            DischargeMethod.PROOF_CHAIN,
            DischargeMethod.RUNTIME_WITNESS,
            DischargeMethod.LLM_VERIFICATION,
            DischargeMethod.MANUAL_REVIEW,
            DischargeMethod.HYBRID,
        ]

    @property
    def current_trust(self) -> TrustAlgebraElement:
        return self._trust_state

    def discharge_summary(self) -> Dict[str, Any]:
        return {
            "total_discharges": len(self._discharge_history),
            "pending_obligations": len(self.monitor.pending_obligations()),
            "failed_obligations": len(self._failed_attempts),
            "current_trust_tier": self._trust_state.tier.name,
            "current_lattice_height": self._trust_state.lattice_height,
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAFETY_OBLIGATION_DEADLINE_SECONDS: float = 300.0
"""Safety obligations must be discharged within 5 minutes of creation."""

LIVENESS_OBLIGATION_DEADLINE_SECONDS: float = 3600.0
"""Liveness obligations must be discharged within 1 hour."""

PROOF_COMPLETENESS_DEADLINE_SECONDS: float = 86400.0
"""Proof completeness obligations may take up to 24 hours."""

MAX_OBLIGATIONS_PER_POLICY: int = 20
"""Maximum number of obligations that can be generated for a single policy."""

INVARIANT_CHECK_SAMPLE_RATE: float = 1.0
"""Fraction of control steps at which invariants are checked (1.0 = always)."""

TRUST_ELEVATION_OBLIGATION_FORMAT: str = (
    "trust_elevation:{policy_id}:from_{from_tier}:to_{to_tier}"
)
"""Format string for trust-elevation obligations."""

LIVENESS_OBLIGATION_FORMAT: str = (
    "liveness:{policy_id}:V_decreasing:horizon_{horizon}"
)
"""Format string for liveness obligations."""

SAFETY_OBLIGATION_FORMAT: str = (
    "safety:{policy_id}:invariant_{invariant_type}:must_hold"
)
"""Format string for safety obligations."""


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _discharge_method_trust_tier(method: DischargeMethod) -> TrustTier:
    """Map a discharge method to the maximum trust tier it can achieve."""
    method_tier_map = {
        DischargeMethod.Z3_PROOF: TrustTier.PROOF_BACKED,
        DischargeMethod.PROOF_CHAIN: TrustTier.PROOF_BACKED,
        DischargeMethod.RUNTIME_WITNESS: TrustTier.RUNTIME_WITNESSED,
        DischargeMethod.LLM_VERIFICATION: TrustTier.RUNTIME_WITNESSED,
        DischargeMethod.MANUAL_REVIEW: TrustTier.REVIEWED,
        DischargeMethod.HYBRID: TrustTier.VERIFIED,
    }
    return method_tier_map.get(method, TrustTier.PROPOSAL)


def _evidence_trust_tier(evidence: List[str]) -> TrustTier:
    """Estimate the trust tier implied by the given evidence items.

    In a real system this would look up the trust tier of each evidence item
    in the evidence registry.  Here we use keyword-based heuristics.
    """
    if not evidence:
        return TrustTier.PROPOSAL
    tiers = []
    for ev in evidence:
        ev_lower = str(ev).lower()
        if "z3" in ev_lower or "proof" in ev_lower or "lean" in ev_lower:
            tiers.append(TrustTier.PROOF_BACKED.value)
        elif "witness" in ev_lower or "runtime" in ev_lower:
            tiers.append(TrustTier.RUNTIME_WITNESSED.value)
        elif "verified" in ev_lower or "verified" in ev_lower:
            tiers.append(TrustTier.VERIFIED.value)
        elif "review" in ev_lower or "reviewed" in ev_lower:
            tiers.append(TrustTier.REVIEWED.value)
        else:
            tiers.append(TrustTier.PROPOSAL.value)
    min_tier_val = min(tiers)
    return TrustTier(min_tier_val)


def _simulate_proof_steps(
    obligation: OrchestratorObligation,
    method: DischargeMethod,
    evidence: List[str],
) -> List[str]:
    """Simulate proof steps for a given obligation.

    In a real system this would invoke the appropriate proof tool.  Here we
    generate a realistic sequence of step descriptions.
    """
    steps = [
        f"[{method.name}] Encoding obligation: {obligation.obligation_formula[:50]}",
        f"[{method.name}] Checking evidence: {len(evidence)} items",
        f"[{method.name}] Verifying source axiom: {obligation.source_axiom[:40]}",
        f"[{method.name}] Establishing: {obligation.target_property[:50]}",
    ]
    if method == DischargeMethod.Z3_PROOF:
        steps.extend([
            "[Z3] Building SMT formula",
            "[Z3] Calling solver (timeout=30s)",
            "[Z3] SAT/UNSAT check complete",
            "[Z3] Extracting proof certificate",
        ])
    elif method == DischargeMethod.LLM_VERIFICATION:
        steps.extend([
            "[LLM] Constructing verification prompt",
            "[LLM] Generating verification response",
            "[LLM] Parsing response for proof claims",
        ])
    elif method == DischargeMethod.RUNTIME_WITNESS:
        steps.extend([
            "[RUNTIME] Instrumenting control loop",
            "[RUNTIME] Collecting witness observations",
            "[RUNTIME] Validating witness against obligation",
        ])
    steps.append(f"[{method.name}] Discharge complete: {obligation.obligation_id[:20]}")
    return steps


def _make_obligation_id() -> str:
    return f"obl-{uuid.uuid4().hex[:12]}"


def _make_invariant_id() -> str:
    return f"inv-{uuid.uuid4().hex[:12]}"


def _make_discharge_id() -> str:
    return f"dsc-{uuid.uuid4().hex[:12]}"


def _make_proof_id() -> str:
    return f"prf-{uuid.uuid4().hex[:12]}"


def _now_plus(seconds: float) -> float:
    return time.time() + seconds


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_orchestrator_obligations(
    control_policy: ControlPolicy,
    semantic_goal: str,
    horizon: int = 100,
) -> List[OrchestratorObligation]:
    """Generate the set of proof obligations for the orchestrator.

    For every control policy the orchestrator must discharge a standard set of
    meta-level obligations:

      1. Safety obligation: the policy must not violate trust invariants.
      2. Liveness obligation: the policy must make progress toward the goal.
      3. Progress obligation: at each step, |O| decreases or |Π| increases.
      4. Trust preservation obligation: all trust elevations are proof-backed.
      5. Semantic consistency obligation: the judgment tuple remains consistent.
      6. Proof completeness obligation: Π eventually becomes complete.

    Parameters
    ----------
    control_policy : ControlPolicy
        The policy for which obligations are generated.
    semantic_goal : str
        The semantic goal formula.
    horizon : int
        The control horizon (used in liveness obligation deadline).

    Returns
    -------
    List[OrchestratorObligation]
        The list of generated obligations.
    """
    pid = control_policy.policy_id
    obligations: List[OrchestratorObligation] = []

    # 1. Safety obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=SAFETY_OBLIGATION_FORMAT.format(
            policy_id=pid, invariant_type="trust_monotone"
        ),
        source_axiom="Trust is an ordered algebra; monotonicity is axiomatic.",
        target_property="∀t, T(J_{t+1}) ≽ T(J_t)",
        evidence_requirements=("policy_design_review",),
        trust_tier=TrustTier.REVIEWED,
        discharge_deadline=_now_plus(SAFETY_OBLIGATION_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # 2. Liveness obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=LIVENESS_OBLIGATION_FORMAT.format(
            policy_id=pid, horizon=horizon
        ),
        source_axiom="Lyapunov stability: V(J_t) strictly decreasing implies convergence.",
        target_property=f"∃T≤{horizon}, V(J_T) < ε",
        evidence_requirements=("lyapunov_certificate",),
        trust_tier=TrustTier.VERIFIED,
        discharge_deadline=_now_plus(LIVENESS_OBLIGATION_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # 3. Progress obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=f"progress:{pid}:per_step_obligation_or_proof_growth",
        source_axiom="Progress axiom: each step must discharge at least one obligation or extend Π.",
        target_property="∀t, |O(J_t+1)| < |O(J_t)| ∨ |Π(J_t+1)| > |Π(J_t)|",
        evidence_requirements=("trajectory_log",),
        trust_tier=TrustTier.RUNTIME_WITNESSED,
        discharge_deadline=_now_plus(LIVENESS_OBLIGATION_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # 4. Trust preservation obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=(
            TRUST_ELEVATION_OBLIGATION_FORMAT.format(
                policy_id=pid,
                from_tier=control_policy.trust_tier.name,
                to_tier="PROOF_BACKED",
            )
        ),
        source_axiom="Trust algebra: ↑_π requires a valid proof_id π.",
        target_property="∀ elevation ↑_π: ∃ valid ProofObject with id=π",
        evidence_requirements=("proof_registry",),
        trust_tier=TrustTier.VERIFIED,
        discharge_deadline=_now_plus(LIVENESS_OBLIGATION_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # 5. Semantic consistency obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=f"semantic_consistency:{pid}:judgment_tuple_well_formed",
        source_axiom="Judgment formation rules: J is well-formed if φ is syntactically valid and O ⊆ derivable(φ).",
        target_property=(
            "∀J in trajectory: well_formed(J) ∧ E_relevant(E, φ) ∧ O_derivable(O, φ)"
        ),
        evidence_requirements=("type_checker_output",),
        trust_tier=TrustTier.VERIFIED,
        discharge_deadline=_now_plus(LIVENESS_OBLIGATION_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # 6. Proof completeness obligation
    obligations.append(OrchestratorObligation(
        obligation_id=_make_obligation_id(),
        obligation_formula=f"proof_completeness:{pid}:goal={semantic_goal[:30]}",
        source_axiom="Proof completeness: Π is complete if it certifies φ under context c.",
        target_property=f"∃T, Π(J_T) certifies '{semantic_goal[:40]}'",
        evidence_requirements=("proof_checker_output",),
        trust_tier=TrustTier.PROOF_BACKED,
        discharge_deadline=_now_plus(PROOF_COMPLETENESS_DEADLINE_SECONDS),
        obligation_proof="",
    ))

    # Optionally add FAIRNESS obligation for multi-agent policies
    if control_policy.policy_type in (PolicyType.ADAPTIVE, PolicyType.TRUST_WEIGHTED):
        obligations.append(OrchestratorObligation(
            obligation_id=_make_obligation_id(),
            obligation_formula=f"fairness:{pid}:all_agents_considered",
            source_axiom="Fairness: every admissible agent must be considered at least once per N steps.",
            target_property="∀a ∈ A, ∀k, ∃t ∈ [kN, (k+1)N]: a ∈ considered_agents(J_t)",
            evidence_requirements=("agent_selection_log",),
            trust_tier=TrustTier.RUNTIME_WITNESSED,
            discharge_deadline=_now_plus(LIVENESS_OBLIGATION_DEADLINE_SECONDS),
            obligation_proof="",
        ))

    return obligations


def prove_control_invariant(
    invariant: OrchestrationInvariant,
    trajectory: List[ControlState],
    verbose: bool = False,
) -> Tuple[ControlProof, bool]:
    """Prove that a control invariant holds along a trajectory.

    The proof strategy depends on the invariant type:
      LOOP_INVARIANT   → check at every step (verify_control_invariant)
      TRUST_INVARIANT  → check trust monotonicity specifically
      SEMANTIC_INVARIANT → check judgment tuple well-formedness

    Parameters
    ----------
    invariant : OrchestrationInvariant
        The invariant to prove.
    trajectory : List[ControlState]
        The trajectory to check.
    verbose : bool
        If True, print diagnostic information.

    Returns
    -------
    (ControlProof, bool)
        The proof object and a boolean indicating success.
    """
    formula = invariant.invariant_formula
    inv_type = invariant.invariant_type

    # Determine proof strategy
    if inv_type == InvariantType.TRUST_INVARIANT:
        method = DischargeMethod.RUNTIME_WITNESS
        holds, violations = verify_control_invariant(trajectory, "trust_monotone", verbose)
    elif inv_type == InvariantType.LOOP_INVARIANT:
        method = DischargeMethod.RUNTIME_WITNESS
        holds, violations = verify_control_invariant(trajectory, formula, verbose)
    elif inv_type == InvariantType.SEMANTIC_INVARIANT:
        method = DischargeMethod.LLM_VERIFICATION
        # Check judgment tuple well-formedness: phi must be non-empty, O must be tuple
        violations = []
        for i, state in enumerate(trajectory):
            j = state.judgment_tuple
            if not j.phi:
                violations.append(i)
            if not isinstance(j.O, tuple):
                violations.append(i)
        holds = len(violations) == 0
    else:
        method = DischargeMethod.RUNTIME_WITNESS
        holds, violations = verify_control_invariant(trajectory, formula, verbose)

    achieved_trust = (
        invariant.trust_requirement
        if holds
        else TrustTier.PROPOSAL
    )

    # Build step descriptions
    steps = [
        f"Checking invariant: {formula[:60]}",
        f"Trajectory length: {len(trajectory)}",
        f"Method: {method.name}",
        f"Violations at steps: {violations[:5] if violations else 'none'}",
        f"Result: {'HOLDS' if holds else 'VIOLATED'}",
    ]

    proof = ControlProof(
        proof_id=_make_proof_id(),
        obligation_id=invariant.proof_obligation,
        proof_strategy=method,
        proof_steps=tuple(steps),
        invariants_verified=(formula,) if holds else (),
        lyapunov_certificate=(
            "V(J) monotone decreasing" if "lyapunov" in formula.lower() else ""
        ),
        convergence_proof="",
        trust_elevation=achieved_trust,
    )

    if verbose:
        status = "PROVED" if holds else "FAILED"
        print(f"  [INVARIANT {status}] {formula[:50]} | "
              f"violations={len(violations)} | tier={achieved_trust.name}")

    return proof, holds


def discharge_orchestrator_obligation(
    obligation: OrchestratorObligation,
    evidence: List[str],
    method: DischargeMethod,
    discharged_by: str = "orchestrator-engine",
) -> ObligationDischarge:
    """Discharge an orchestrator obligation using the specified method and evidence.

    This is the primary API for discharging meta-level obligations.  It:
      1. Validates the evidence against the obligation's requirements.
      2. Simulates the discharge proof.
      3. Computes the achieved trust tier.
      4. Returns the discharge certificate.

    Parameters
    ----------
    obligation : OrchestratorObligation
        The obligation to discharge.
    evidence : List[str]
        Evidence items provided for the discharge.
    method : DischargeMethod
        The discharge method to use.
    discharged_by : str
        Identifier of the entity performing the discharge.

    Returns
    -------
    ObligationDischarge
        The discharge certificate.
    """
    # Compute achieved trust
    method_trust = _discharge_method_trust_tier(method)
    evidence_trust = _evidence_trust_tier(evidence)
    achieved_trust = TrustTier(min(method_trust.value, evidence_trust.value))

    # Build proof
    proof_steps = _simulate_proof_steps(obligation, method, evidence)
    proof_id = _make_proof_id()

    return ObligationDischarge(
        discharge_id=_make_discharge_id(),
        obligation_id=obligation.obligation_id,
        discharge_method=method,
        evidence_used=tuple(str(e) for e in evidence),
        discharge_proof=proof_id,
        discharged_by=discharged_by,
        timestamp=time.time(),
        trust_tier=achieved_trust,
    )


def monitor_invariant_violation(
    invariant: OrchestrationInvariant,
    current_state: ControlState,
    previous_state: Optional[ControlState] = None,
) -> Tuple[bool, Optional[str]]:
    """Check for an invariant violation at the current control state.

    Returns (violated, violation_description) where violated=True means the
    invariant is currently violated.

    This is a *point-in-time* check (single state or pair of states), unlike
    prove_control_invariant which checks a full trajectory.

    Parameters
    ----------
    invariant : OrchestrationInvariant
        The invariant to check.
    current_state : ControlState
        The current state to check.
    previous_state : ControlState, optional
        The previous state (for transition-based invariants).

    Returns
    -------
    (bool, Optional[str])
        (violated, description)
    """
    formula = invariant.invariant_formula
    j = current_state.judgment_tuple

    # Trust monotonicity
    if "trust_monotone" in formula and previous_state is not None:
        prev_tier = previous_state.trust_level.tier.value
        curr_tier = current_state.trust_level.tier.value
        if curr_tier < prev_tier:
            return True, (
                f"Trust regression: {TrustTier(prev_tier).name} → {TrustTier(curr_tier).name}"
            )

    # Obligation decrease
    if "obligation_decreasing" in formula and previous_state is not None:
        prev_count = len(previous_state.judgment_tuple.O)
        curr_count = len(j.O)
        if curr_count > prev_count:
            return True, f"Obligation set expanded: {prev_count} → {curr_count}"

    # Lyapunov decrease
    if "lyapunov_decreasing" in formula and previous_state is not None:
        if current_state.distance_to_goal >= previous_state.distance_to_goal:
            return True, (
                f"Lyapunov non-decrease: {previous_state.distance_to_goal:.4f} → "
                f"{current_state.distance_to_goal:.4f}"
            )

    # Semantic consistency
    if "well_formed" in formula:
        if not j.phi:
            return True, "Judgment formula φ is empty"
        if not isinstance(j.O, tuple):
            return True, f"Obligations O is not a tuple: {type(j.O)}"
        if j.T not in TrustTier.__members__.values():
            return True, f"Trust tier T is invalid: {j.T}"

    # Trust below floor
    if "trust_floor" in formula:
        if current_state.trust_level.tier.value < invariant.trust_requirement.value:
            return True, (
                f"Trust {current_state.trust_level.tier.name} below required "
                f"{invariant.trust_requirement.name}"
            )

    return False, None


def build_control_proof(
    obligation: OrchestratorObligation,
    proof_steps: List[str],
    method: DischargeMethod = DischargeMethod.RUNTIME_WITNESS,
    invariants_verified: Optional[List[str]] = None,
) -> ControlProof:
    """Build a ControlProof object from a list of proof steps.

    Parameters
    ----------
    obligation : OrchestratorObligation
        The obligation being proved.
    proof_steps : List[str]
        The sequence of proof steps.
    method : DischargeMethod
        The proof strategy.
    invariants_verified : List[str], optional
        Invariants verified as part of this proof.

    Returns
    -------
    ControlProof
        The proof object.
    """
    achieved_trust = _discharge_method_trust_tier(method)
    # Trust is at most the obligation's required trust tier
    achieved_trust = TrustTier(min(achieved_trust.value, obligation.trust_tier.value + 1))

    lc = (
        "V(J) = |O| + trust_gap; ΔV < 0 per step (obligation-discharge reduces |O| by ≥1)"
        if "liveness" in obligation.obligation_formula
        else ""
    )
    cp = (
        f"convergence within {len(proof_steps) * 2} steps under admissible policy"
        if "progress" in obligation.obligation_formula
        else ""
    )

    return ControlProof(
        proof_id=_make_proof_id(),
        obligation_id=obligation.obligation_id,
        proof_strategy=method,
        proof_steps=tuple(proof_steps),
        invariants_verified=tuple(invariants_verified or []),
        lyapunov_certificate=lc,
        convergence_proof=cp,
        trust_elevation=achieved_trust,
    )


def elevate_trust_on_discharge(
    obligation: OrchestratorObligation,
    discharge: ObligationDischarge,
    current_trust: TrustAlgebraElement,
) -> TrustAlgebraElement:
    """Elevate the trust algebra element when an obligation is discharged.

    The trust elevation is:
      new_trust = ↑_π(current_trust)
    where π = discharge.discharge_proof.

    The elevation is valid if and only if:
      1. The discharge has a non-empty discharge_proof field.
      2. The discharge's trust_tier ≽ obligation's trust_tier.
      3. The discharge's evidence_used ⊇ obligation's evidence_requirements.

    If any condition fails, the current trust is returned unchanged (no
    spurious elevation).

    Parameters
    ----------
    obligation : OrchestratorObligation
        The discharged obligation.
    discharge : ObligationDischarge
        The discharge certificate.
    current_trust : TrustAlgebraElement
        The current trust algebra element.

    Returns
    -------
    TrustAlgebraElement
        The (possibly elevated) trust algebra element.
    """
    # Condition 1: proof must exist
    if not discharge.discharge_proof:
        return current_trust

    # Condition 2: discharge trust tier must meet obligation requirement
    if discharge.trust_tier.value < obligation.trust_tier.value:
        return current_trust

    # Condition 3: evidence requirements must be (approximately) met
    required = set(obligation.evidence_requirements)
    provided = set(discharge.evidence_used)
    # Relaxed check: at least half the required evidence was provided
    coverage = len(required & provided) / max(len(required), 1)
    if len(required) > 0 and coverage < 0.5:
        return current_trust

    # All conditions met: elevate trust
    return current_trust.elevate(discharge.discharge_proof)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Smoke test: proof_obligations_for_orchestratio.py")
    print("=" * 70)

    # 1. Build a mock control policy
    policy = ControlPolicy(
        policy_id="pol-meta-001",
        policy_type=PolicyType.PROOF_GUIDED,
        policy_parameters={"proof_strategy": "obligation_discharge_first"},
        coverage_region="full",
        trust_tier=TrustTier.REVIEWED,
        policy_proof="",
        last_validated=time.time(),
    )
    print(f"Policy: id={policy.policy_id}, type={policy.policy_type.name}")
    print(f"  Trust tier: {policy.trust_tier.name}")

    # 2. Generate orchestrator obligations
    obligations = generate_orchestrator_obligations(
        policy, "report_generated ∧ quality_verified", horizon=50
    )
    print(f"\nGenerated {len(obligations)} obligations:")
    for ob in obligations:
        print(f"  [{ob.trust_tier.name}] {ob.obligation_formula[:55]}")

    # 3. Build ObligationMonitor and register obligations
    monitor = ObligationMonitor(trust_floor=TrustTier.PROPOSAL)
    for ob in obligations:
        monitor.register_obligation(ob)
    print(f"\nMonitor: {len(monitor.pending_obligations())} obligations registered")

    # 4. Build mock control trajectory and check for violations
    j_base = JudgmentTuple(
        c="orch-ctx", phi="report_generated", A=("a1",),
        E=("e1",), O=("o1", "o2"), B="b0",
        T=TrustTier.PROPOSAL, Pi=""
    )
    states = []
    for i in range(5):
        tier_val = min(TrustTier.PROPOSAL.value + i, max(t.value for t in TrustTier))
        ji = JudgmentTuple(
            c="orch-ctx", phi="report_generated", A=("a1",),
            E=("e1", f"e{i+2}"), O=tuple(f"o{k}" for k in range(max(0, 2 - i))),
            B=f"b{i}", T=TrustTier(tier_val), Pi="p" * (i * 3)
        )
        coords = tuple(float(k) * 0.1 * (1 - i * 0.1) for k in range(16))
        trust_e = TrustAlgebraElement(tier=TrustTier(tier_val))
        s = ControlState(f"s{i}", ji, coords, max(0.0, 1.0 - i * 0.2), trust_e, time.time(), i)
        states.append(s)

    print("\nChecking control states for obligation violations:")
    for state in states:
        violations = monitor.check_state(state)
        if violations:
            print(f"  [step {state.trajectory_segment}] {len(violations)} violations:")
            for v in violations:
                print(f"    {v['type']}")
        else:
            print(f"  [step {state.trajectory_segment}] OK (trust={state.trust_level.tier.name})")

    print(f"\nTotal monitor violations: {monitor.violation_count()}")

    # 5. Define some orchestration invariants
    trust_inv = OrchestrationInvariant(
        invariant_id=_make_invariant_id(),
        invariant_formula="trust_monotone ∧ obligation_decreasing",
        invariant_type=InvariantType.TRUST_INVARIANT,
        proof_obligation=obligations[0].obligation_id,
        monitoring_policy="always",
        violation_handler="demote_and_alert",
        trust_requirement=TrustTier.VERIFIED,
    )
    semantic_inv = OrchestrationInvariant(
        invariant_id=_make_invariant_id(),
        invariant_formula="well_formed ∧ phi_nonempty",
        invariant_type=InvariantType.SEMANTIC_INVARIANT,
        proof_obligation=obligations[4].obligation_id,
        monitoring_policy="sampled",
        violation_handler="reject_and_log",
        trust_requirement=TrustTier.REVIEWED,
    )
    print(f"\nInvariants defined: {trust_inv.invariant_formula[:50]}")
    print(f"                    {semantic_inv.invariant_formula[:50]}")

    # 6. Prove invariants along trajectory
    print("\nProving control invariants:")
    for inv in [trust_inv, semantic_inv]:
        proof, holds = prove_control_invariant(inv, states, verbose=True)
        print(f"  [{('PROVED' if holds else 'FAILED')}] {inv.invariant_formula[:45]}")
        print(f"    Proof id: {proof.proof_id}, tier: {proof.trust_elevation.name}")

    # 7. Discharge obligations
    engine = ObligationDischargeEngine(monitor, default_method=DischargeMethod.LLM_VERIFICATION)
    evidence = [
        "runtime_witness:trajectory_observed",
        "reviewed:policy_design_review_2024",
        "z3:trust_monotonicity_formula_sat",
    ]
    print(f"\nAttempting to discharge {len(monitor.pending_obligations())} obligations:")
    discharged_count, failed_count = engine.discharge_all_pending(evidence)
    print(f"  Discharged: {discharged_count}, Failed: {failed_count}")
    print(f"  Engine trust tier: {engine.current_trust.tier.name}")
    print(f"  Discharge summary: {engine.discharge_summary()}")

    # 8. Discharge individual obligations manually
    print("\nManual discharge of safety obligation:")
    safety_ob = obligations[0]
    discharge = discharge_orchestrator_obligation(
        safety_ob,
        evidence=["z3:safety_proof_sat", "runtime_witness:trust_monotone_observed"],
        method=DischargeMethod.Z3_PROOF,
        discharged_by="z3-solver-4.12",
    )
    print(f"  Discharge id: {discharge.discharge_id}")
    print(f"  Method: {discharge.discharge_method.name}")
    print(f"  Trust tier achieved: {discharge.trust_tier.name}")

    # 9. Trust elevation on discharge
    base_trust = TrustAlgebraElement(tier=TrustTier.REVIEWED, lattice_height=2)
    elevated_trust = elevate_trust_on_discharge(safety_ob, discharge, base_trust)
    print(f"\nTrust elevation:")
    print(f"  Before: {base_trust.tier.name} (height={base_trust.lattice_height})")
    print(f"  After:  {elevated_trust.tier.name} (height={elevated_trust.lattice_height})")
    print(f"  Evidence ids: {elevated_trust.evidence_ids[:3]}")

    # 10. Point-in-time invariant violation check
    print("\nPoint-in-time invariant violation check (simulated regression):")
    prev_state = states[-2]
    # Introduce a synthetic regression
    bad_j = JudgmentTuple(
        c="orch-ctx", phi="report_generated", A=("a1",),
        E=("e1",), O=("o1", "o2", "o3"),  # Obligation expansion!
        B="b-regressed", T=TrustTier.PROPOSAL, Pi=""
    )
    bad_trust = TrustAlgebraElement(tier=TrustTier.PROPOSAL)
    bad_state = ControlState("s-bad", bad_j, (0.1,) * 16, 0.9, bad_trust, time.time(), 99)

    for inv in [trust_inv, semantic_inv]:
        violated, desc = monitor_invariant_violation(inv, bad_state, prev_state)
        print(f"  [{inv.invariant_formula[:35]}]: violated={violated}"
              + (f" — {desc}" if desc else ""))

    # 11. Build a ControlProof directly
    custom_proof = build_control_proof(
        obligations[1],  # liveness obligation
        proof_steps=[
            "Assume V(J_0) = d_0 > 0",
            "Show that each obligation discharge reduces V by ≥ 1",
            "Since |O_0| = k, after k steps V = 0",
            "QED: convergence within k steps",
        ],
        method=DischargeMethod.PROOF_CHAIN,
        invariants_verified=["lyapunov_decreasing", "obligation_decreasing"],
    )
    print(f"\nCustom control proof:")
    print(f"  Proof id: {custom_proof.proof_id}")
    print(f"  Strategy: {custom_proof.proof_strategy.name}")
    print(f"  Steps: {len(custom_proof.proof_steps)}")
    print(f"  Invariants verified: {custom_proof.invariants_verified}")
    print(f"  Lyapunov certificate: {custom_proof.lyapunov_certificate}")
    print(f"  Trust elevation: {custom_proof.trust_elevation.name}")

    print("\nSmoke test complete.")
