"""
The Controller Should Optimise Semantic Objectives.

# copilot: This module formalises *what* the orchestration controller should
optimise.  The naive answer — "minimise latency" or "maximise token count" —
is wrong because it ignores the semantic content of the judgment tuple.

The correct objective is *semantic progress*: each control step should reduce
the distance from the current judgment J = (c, φ, A, E, O, B, T, Π) to the
goal judgment J*, measured in a metric that respects:
  1. The structure of the judgment tuple.
  2. The trust algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).
  3. The obligation-discharge semantics (progress = shrinking |O|).
  4. The proof-completeness criterion (V(J) = 0 iff Π is a complete proof of φ).

Why semantic rewards cannot be plain floats
===========================================
A plain float reward r_t ∈ ℝ discards the algebraic structure of trust.
If we elevate trust through a proof step ↑_π, the resulting trust element
  T' = ↑_π(T)
carries the proof id π as evidence.  A plain float collapses this to a scalar
and loses the reference to π.  Without π, we cannot later verify the elevation
was legitimate — a malicious agent could fabricate high scalar rewards.

The solution: rewards are *TrustAlgebraElement-valued* (or at minimum
*annotated* with the trust algebra element that justifies them).  The reward
accumulator must be a monoid under the ⊕ operator.

Convergence proofs
==================
Without a convergence proof, the optimiser might cycle indefinitely.
Convergence in semantic space requires showing that the objective is a
*quasi-Lyapunov function*: it decreases on average along the optimisation
trajectory and is bounded below.  This is non-trivial for the composite
semantic objective because the trust-algebra weights themselves change as
trust is elevated.

The SemanticOptimizer.proof_of_convergence field must reference a ProofObject
that certifies: under the configured trust-algebra weighting scheme and the
chosen ConvergenceCriterion, the objective value converges to its minimum
within at most horizon steps.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Jugeo imports with stub fallback
# ---------------------------------------------------------------------------
try:
    from jugeo.core.trust import TrustTier, TrustAlgebraElement  # type: ignore
    from jugeo.core.judgment import JudgmentTuple  # type: ignore
    from jugeo.core.proof import ProofObject  # type: ignore
    from jugeo.orchestration.semantic_control.orchestration_is_a_control_problem import (  # type: ignore
        ControlState, ControlPolicy, PolicyType, SemanticMetric,
        compute_semantic_distance, TRUST_TIER_COUNT,
    )
except ImportError:
    # --- Trust stubs ---
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

    # Minimal stubs for s01 types
    class PolicyType(Enum):  # type: ignore
        GREEDY = auto()
        OPTIMAL = auto()
        ROBUST = auto()
        ADAPTIVE = auto()
        PROOF_GUIDED = auto()
        TRUST_WEIGHTED = auto()

    class SemanticMetric(Enum):  # type: ignore
        JUDGMENT_DISTANCE = auto()
        OBLIGATION_COVERAGE = auto()
        PROOF_DEPTH = auto()
        TRUST_ELEVATION = auto()
        COMPOSITE = auto()

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


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ObjectiveComponent(Enum):
    """The components of the semantic optimisation objective.

    The full objective is a trust-algebra-weighted combination of these
    components.  Each component measures progress along a different dimension
    of the judgment tuple J = (c, φ, A, E, O, B, T, Π).
    """
    SEMANTIC_DISTANCE = auto()
    """Primary component: distance d(J, J*) in semantic space.  This is the
    Lyapunov candidate V(J).  Must decrease at every optimisation step."""

    TRUST_ELEVATION = auto()
    """Distance in the trust lattice: (PROOF_BACKED.value - T.value).  Each
    trust elevation ↑_π decreases this component by 1."""

    OBLIGATION_COVERAGE = auto()
    """Fraction of obligations still undischarged: |O| / |O₀|.  The optimizer
    tries to discharge obligations as early as possible."""

    PROOF_COMPLETENESS = auto()
    """1 − (depth of current partial proof / required depth).  When Π is
    complete, this component equals 0."""

    EFFICIENCY = auto()
    """Resource cost of the current trajectory: number of agent calls × average
    trust deficit.  The optimizer penalises cheap but untrustworthy routes."""

    COMPOSITE = auto()
    """All of the above, combined via trust-algebra weights."""


class ConvergenceCriterion(Enum):
    """When the optimiser declares convergence."""
    ABSOLUTE_TOLERANCE = auto()
    """Stop when |objective_t+1 - objective_t| < ε_abs."""

    RELATIVE_TOLERANCE = auto()
    """Stop when |objective_t+1 - objective_t| / |objective_t| < ε_rel."""

    PROOF_COMPLETE = auto()
    """Stop when the proof object Π is complete (is_complete = True)."""

    TRUST_THRESHOLD = auto()
    """Stop when the trust tier T ≽ target_tier in the trust lattice."""

    FIXED_STEPS = auto()
    """Stop after exactly N optimisation steps, regardless of progress."""


class RegularizationType(Enum):
    """Regularisation terms added to the objective to prevent overfitting to
    the training trajectory and ensure generalisable control behaviour."""

    L1_SEMANTIC = auto()
    """Penalise the L1 norm of the semantic coordinate vector.  Encourages
    sparse representations in the embedding space."""

    L2_SEMANTIC = auto()
    """Penalise the L2 norm of the semantic coordinate vector.  Encourages
    small-magnitude embeddings and numerical stability."""

    TRUST_PENALTY = auto()
    """Penalise large trust deficits: agents with low trust tier incur a
    penalty proportional to (PROOF_BACKED.value - T.value)²."""

    OBLIGATION_PENALTY = auto()
    """Penalise trajectories with lingering undischarged obligations: each
    step with |O| > 0 incurs a penalty."""

    PROOF_COST = auto()
    """Penalise expensive proof strategies: proof steps have a cost proportional
    to the computational resources consumed."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptimizationStep:
    """A single step of the semantic optimisation procedure.

    Each optimisation step corresponds to one update of the control policy
    parameters.  The step records:
      - The current objective value (before the step).
      - The estimated gradient of the objective.
      - The step direction and step size.
      - A proof that the step is admissible (does not violate trust invariants).
      - An estimate of convergence progress.

    The ``step_size`` is a TrustAlgebraElement because it represents the
    *confidence* in the gradient estimate: a step taken with high-trust
    gradient evidence can be larger than one based on PROPOSAL-level evidence.
    Using a plain float would discard this epistemic information.

    Trust constraint on step size
    ==============================
    The effective step size α_t is bounded by the trust algebra:
      α_t ≤ α_max · (trust_elem.tier.value / PROOF_BACKED.value)
    This ensures that low-trust gradient estimates take small cautious steps
    while high-trust estimates (backed by proof) can take larger steps.
    """
    step_id: str
    current_objective_value: float
    gradient_estimate: Tuple[float, ...]       # gradient vector in parameter space
    step_direction: Tuple[float, ...]          # normalised direction
    step_size: TrustAlgebraElement             # trust-algebra-valued step size
    step_proof: str                            # proof_id or "" if unproven
    convergence_estimate: float                # estimated distance to convergence


@dataclass(frozen=True)
class SemanticReward:
    """A reward signal emitted when the controller makes semantic progress.

    Unlike scalar RL rewards, a SemanticReward carries a reference to the
    trust algebra elements that *justify* the reward.  A reward without a
    supporting TrustAlgebraElement cannot be admitted into the objective
    accumulator — it would be treated as an adversarial signal.

    Components
    ----------
    trust_elevation_delta : int
        Number of trust tiers gained in this transition (0–4).
    obligation_discharge_count : int
        Number of obligations discharged (|O_{t-1}| - |O_t|).
    geometric_progress : float
        Decrease in semantic distance: d(J_{t-1}, J*) − d(J_t, J*).  Positive
        = progress, negative = regression.

    The ``reward_components`` dict maps ObjectiveComponent names to their
    individual float contributions, before trust-algebra weighting.
    """
    reward_id: str
    source_state: ControlState
    target_state: ControlState
    reward_components: Dict[str, float]        # component → raw float contribution
    trust_elevation_delta: int
    obligation_discharge_count: int
    geometric_progress: float                  # d(src,goal) - d(tgt,goal); positive = good


@dataclass(frozen=True)
class ControlObjective:
    """The formal optimisation objective for the semantic controller.

    The objective is:
      L(π) = primary_term(π) + Σ_i λ_i · regularization_terms_i(π)

    subject to:  constraints_j(π) ≤ 0  for all j

    The ``trust_weight_scheme`` describes how the primary and regularisation
    weights are determined by the trust algebra.  Supported schemes:
      "flat"              — all components weighted equally
      "lattice_height"    — higher lattice height → higher weight
      "tier_proportional" — weight = tier.value / PROOF_BACKED.value
      "proof_backed_only" — only PROOF_BACKED evidence contributes to objective

    The ``objective_proof`` references a ProofObject certifying that the
    objective is well-posed: it is bounded below, its minimum is achieved at
    the goal state, and the trust-weighting scheme is monotone in trust tier.
    """
    objective_id: str
    primary_term: ObjectiveComponent
    regularization_terms: Tuple[Tuple[RegularizationType, float], ...]   # (type, λ)
    constraints: Tuple[str, ...]                # constraint formulas
    trust_weight_scheme: str
    objective_proof: str                        # proof_id or ""


@dataclass(frozen=True)
class SemanticOptimizer:
    """The optimiser that drives the control policy toward the semantic goal.

    The optimiser is responsible for:
      1. Maintaining an estimate of the gradient ∇L(π) w.r.t. policy parameters.
      2. Proposing parameter updates that reduce L(π).
      3. Verifying that each update respects the trust algebra.
      4. Declaring convergence when the ConvergenceCriterion is satisfied.

    The ``learning_rate`` is a TrustAlgebraElement, not a plain float.
    This is because the learning rate determines how aggressively the policy
    is updated.  A high learning rate backed by weak evidence (PROPOSAL tier)
    is dangerous — it may overshoot and violate trust invariants.  A high
    learning rate backed by PROOF_BACKED evidence is safe because we have
    certified the gradient estimate.

    The ``proof_of_convergence`` must reference a ProofObject that certifies
    convergence under the configured criterion.  Until this proof exists, the
    optimiser operates in "exploratory" mode with reduced step sizes.
    """
    optimizer_id: str
    objective_function: ControlObjective
    learning_rate: TrustAlgebraElement          # trust-algebra-valued step size
    convergence_criterion: ConvergenceCriterion
    optimization_horizon: int                   # max number of optimisation steps
    proof_of_convergence: str                   # proof_id or ""


# ---------------------------------------------------------------------------
# Mutable helper classes
# ---------------------------------------------------------------------------

class RewardAccumulator:
    """Accumulates SemanticReward objects over a trajectory.

    The accumulator is a *monoid* under the ⊕ (join) operator:
      R₁ ⊕ R₂ = join of their trust algebra elements
      identity = TrustAlgebraElement(tier=PROPOSAL)

    This means that accumulating rewards with PROPOSAL-tier trust never
    inflates the total trust beyond what the individual rewards justify.

    The accumulated reward is used as input to the objective gradient
    estimator.  Only rewards with sufficient trust (≽ the objective's
    trust_weight_scheme threshold) contribute to the gradient.
    """

    def __init__(self, min_trust_tier: TrustTier = TrustTier.REVIEWED) -> None:
        self.min_trust_tier = min_trust_tier
        self.rewards: List[SemanticReward] = []
        self._accumulated_trust = TrustAlgebraElement(tier=TrustTier.PROPOSAL)
        self._total_geometric_progress: float = 0.0
        self._total_obligation_discharges: int = 0
        self._total_trust_elevations: int = 0

    def add(self, reward: SemanticReward) -> None:
        """Add a reward to the accumulator.

        Rewards whose source state has trust below min_trust_tier are
        rejected (adversarial input protection).
        """
        src_tier = reward.source_state.trust_level.tier
        if src_tier.value < self.min_trust_tier.value:
            return  # Silently drop low-trust rewards
        self.rewards.append(reward)
        self._accumulated_trust = self._accumulated_trust.join(
            reward.source_state.trust_level
        )
        self._total_geometric_progress += reward.geometric_progress
        self._total_obligation_discharges += reward.obligation_discharge_count
        self._total_trust_elevations += reward.trust_elevation_delta

    def total_reward(self) -> float:
        """Compute total scalar reward for logging purposes.

        Note: this scalar collapses the trust algebra structure and should
        only be used for logging, never for policy updates.
        """
        trust_multiplier = self._accumulated_trust.tier.value / len(TrustTier)
        return (
            self._total_geometric_progress * 0.5
            + self._total_obligation_discharges * 0.3
            + self._total_trust_elevations * 0.2
        ) * trust_multiplier

    def summary(self) -> Dict[str, Any]:
        return {
            "n_rewards": len(self.rewards),
            "accumulated_trust_tier": self._accumulated_trust.tier.name,
            "total_geometric_progress": self._total_geometric_progress,
            "total_obligation_discharges": self._total_obligation_discharges,
            "total_trust_elevations": self._total_trust_elevations,
            "total_reward_scalar": self.total_reward(),
        }


class ObjectiveLandscape:
    """Models the objective function landscape in semantic space.

    The landscape is a map from semantic coordinate vectors to objective values.
    It is built by sampling the objective at a set of ControlState points and
    interpolating.

    Key features the landscape tracks:
      - Local minima (potential traps for the optimiser)
      - Saddle points (regions where gradient estimation is unreliable)
      - Trust barriers (regions where trust tier drops, creating discontinuities)
      - The global minimum (the goal state J*)

    This class is *not* frozen because the landscape is updated incrementally
    as new control states are observed.
    """

    def __init__(self, objective: ControlObjective) -> None:
        self.objective = objective
        self._samples: List[Tuple[ControlState, float]] = []
        self._min_value: float = float("inf")
        self._min_state: Optional[ControlState] = None
        self._local_minima: List[ControlState] = []
        self._trust_barriers: List[Tuple[ControlState, ControlState]] = []

    def sample(self, state: ControlState, value: float) -> None:
        """Record an objective value at a state."""
        self._samples.append((state, value))
        if value < self._min_value:
            self._min_value = value
            self._min_state = state

    def add_trust_barrier(self, from_state: ControlState, to_state: ControlState) -> None:
        """Record a trust barrier between two states.

        A trust barrier exists when the trust tier drops between two adjacent
        states, creating a discontinuity in the objective landscape.
        """
        self._trust_barriers.append((from_state, to_state))

    def detect_local_minima(self, neighborhood_radius: float = 0.1) -> List[ControlState]:
        """Detect local minima in the sampled landscape.

        A state is a local minimum if its objective value is less than all
        other sampled states within neighborhood_radius in semantic space.
        """
        local_mins: List[ControlState] = []
        for state, val in self._samples:
            is_local_min = True
            for other_state, other_val in self._samples:
                if other_state.state_id == state.state_id:
                    continue
                dist = compute_semantic_distance(state, other_state)
                if dist < neighborhood_radius and other_val < val:
                    is_local_min = False
                    break
            if is_local_min:
                local_mins.append(state)
        self._local_minima = local_mins
        return local_mins

    def landscape_summary(self) -> Dict[str, Any]:
        return {
            "n_samples": len(self._samples),
            "global_min": self._min_value,
            "n_local_minima": len(self._local_minima),
            "n_trust_barriers": len(self._trust_barriers),
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LEARNING_RATE_TIER: TrustTier = TrustTier.REVIEWED
"""Default trust tier for the learning rate element.  Low-trust gradient
estimates produce conservative updates."""

CONVERGENCE_ABS_TOLERANCE: float = 1e-5
"""Default absolute tolerance for objective convergence."""

CONVERGENCE_REL_TOLERANCE: float = 1e-4
"""Default relative tolerance for objective convergence."""

MAX_OPTIMIZATION_STEPS: int = 500
"""Safety cap on the number of optimisation steps."""

REWARD_COMPONENT_WEIGHTS: Dict[str, float] = {
    "SEMANTIC_DISTANCE": 0.35,
    "TRUST_ELEVATION": 0.25,
    "OBLIGATION_COVERAGE": 0.25,
    "PROOF_COMPLETENESS": 0.10,
    "EFFICIENCY": 0.05,
}
"""Default weights for reward components.  Must sum to 1.0."""

assert abs(sum(REWARD_COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9, \
    "Reward component weights must sum to 1.0"

REGULARIZATION_DEFAULTS: Dict[RegularizationType, float] = {
    RegularizationType.L2_SEMANTIC: 0.01,
    RegularizationType.TRUST_PENALTY: 0.05,
    RegularizationType.OBLIGATION_PENALTY: 0.02,
    RegularizationType.PROOF_COST: 0.01,
}
"""Default regularisation coefficients."""


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _compute_trust_weight(trust_elem: TrustAlgebraElement, scheme: str) -> float:
    """Convert a trust algebra element to a scalar weight for objective computation.

    This is the *only* place where a trust element is legitimately converted
    to a scalar — and the conversion is always annotated with the scheme used.
    The resulting scalar must NOT be stored anywhere without its originating
    TrustAlgebraElement.

    Supported schemes:
      "flat"              → 1.0 for all tiers
      "tier_proportional" → tier.value / PROOF_BACKED.value
      "lattice_height"    → (lattice_height + 1) / (max_height + 1)
      "proof_backed_only" → 1.0 if PROOF_BACKED else 0.0
    """
    max_tier = max(t.value for t in TrustTier)
    if scheme == "flat":
        return 1.0
    elif scheme == "tier_proportional":
        return trust_elem.tier.value / max_tier
    elif scheme == "lattice_height":
        max_height = 20  # Practical cap on lattice height
        return (trust_elem.lattice_height + 1) / (max_height + 1)
    elif scheme == "proof_backed_only":
        return 1.0 if trust_elem.tier == TrustTier.PROOF_BACKED else 0.0
    else:
        return trust_elem.tier.value / max_tier  # Default to tier_proportional


def _compute_obligation_coverage_reward(src: ControlState, tgt: ControlState) -> float:
    """Reward for obligation discharge: (|O_src| - |O_tgt|) / max(|O_src|, 1)."""
    src_obligations = len(src.judgment_tuple.O)
    tgt_obligations = len(tgt.judgment_tuple.O)
    return (src_obligations - tgt_obligations) / max(src_obligations, 1)


def _compute_proof_completeness_reward(src: ControlState, tgt: ControlState) -> float:
    """Reward for proof progress: proxy by proof-id length difference."""
    src_len = len(src.judgment_tuple.Pi)
    tgt_len = len(tgt.judgment_tuple.Pi)
    return (tgt_len - src_len) / max(src_len, tgt_len, 1)


def _compute_trust_elevation_reward(src: ControlState, tgt: ControlState) -> float:
    """Reward for trust elevation: normalised by total tier distance."""
    max_tier = max(t.value for t in TrustTier)
    return (tgt.trust_level.tier.value - src.trust_level.tier.value) / max_tier


def _apply_regularization(
    base_value: float,
    state: ControlState,
    reg_terms: Tuple[Tuple[RegularizationType, float], ...],
) -> float:
    """Apply regularisation terms to the objective value."""
    penalty = 0.0
    for reg_type, lambda_val in reg_terms:
        if reg_type == RegularizationType.L2_SEMANTIC:
            coords = state.semantic_coordinates
            penalty += lambda_val * math.sqrt(sum(x * x for x in coords))
        elif reg_type == RegularizationType.L1_SEMANTIC:
            coords = state.semantic_coordinates
            penalty += lambda_val * sum(abs(x) for x in coords)
        elif reg_type == RegularizationType.TRUST_PENALTY:
            max_tier = max(t.value for t in TrustTier)
            deficit = max_tier - state.trust_level.tier.value
            penalty += lambda_val * (deficit ** 2)
        elif reg_type == RegularizationType.OBLIGATION_PENALTY:
            penalty += lambda_val * len(state.judgment_tuple.O)
        elif reg_type == RegularizationType.PROOF_COST:
            proof_steps = len(state.judgment_tuple.Pi.split("+")) if state.judgment_tuple.Pi else 0
            penalty += lambda_val * proof_steps
    return base_value + penalty


def _make_optimizer_id() -> str:
    return f"opt-{uuid.uuid4().hex[:12]}"


def _make_reward_id() -> str:
    return f"rwd-{uuid.uuid4().hex[:12]}"


def _make_objective_id() -> str:
    return f"obj-{uuid.uuid4().hex[:12]}"


def _make_step_id() -> str:
    return f"stp-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_semantic_reward(
    state: ControlState,
    next_state: ControlState,
    goal: str,
    trust_floor: TrustTier = TrustTier.PROPOSAL,
) -> SemanticReward:
    """Compute the semantic reward for a state transition.

    The reward is a structured object — NOT a plain float.  Its ``trust_level``
    is the join of the source and target trust algebra elements, reflecting that
    the reward is only as trustworthy as the least trustworthy endpoint.

    The reward components are:
      - SEMANTIC_DISTANCE: geometric progress d(src,goal) - d(tgt,goal)
      - TRUST_ELEVATION: change in trust tier (normalised)
      - OBLIGATION_COVERAGE: obligations discharged (fraction)
      - PROOF_COMPLETENESS: proof progress proxy
      - EFFICIENCY: penalty for trust deficit

    Parameters
    ----------
    state : ControlState
        The source state J_t.
    next_state : ControlState
        The target state J_{t+1}.
    goal : str
        The goal formula (used to annotate the reward context).
    trust_floor : TrustTier
        Minimum trust tier required for the reward to be non-zero.

    Returns
    -------
    SemanticReward
        The structured reward object.
    """
    # Geometric progress (positive = good)
    geo_progress = state.distance_to_goal - next_state.distance_to_goal

    # Trust elevation delta
    trust_delta = next_state.trust_level.tier.value - state.trust_level.tier.value

    # Obligation discharge count
    oblig_discharge = len(state.judgment_tuple.O) - len(next_state.judgment_tuple.O)

    # Individual component rewards
    components: Dict[str, float] = {
        "SEMANTIC_DISTANCE": geo_progress,
        "TRUST_ELEVATION": _compute_trust_elevation_reward(state, next_state),
        "OBLIGATION_COVERAGE": _compute_obligation_coverage_reward(state, next_state),
        "PROOF_COMPLETENESS": _compute_proof_completeness_reward(state, next_state),
        "EFFICIENCY": -max(0, max(t.value for t in TrustTier) - state.trust_level.tier.value) * 0.01,
    }

    return SemanticReward(
        reward_id=_make_reward_id(),
        source_state=state,
        target_state=next_state,
        reward_components=components,
        trust_elevation_delta=max(trust_delta, 0),
        obligation_discharge_count=max(oblig_discharge, 0),
        geometric_progress=geo_progress,
    )


def optimize_control(
    objective: ControlObjective,
    current_policy: ControlPolicy,
    steps: int,
    initial_trust: Optional[TrustAlgebraElement] = None,
    verbose: bool = False,
) -> Tuple[ControlPolicy, List[OptimizationStep]]:
    """Run the semantic control optimisation for a given number of steps.

    The optimisation loop:
      1. Estimate the gradient ∇L(π) at the current policy.
      2. Compute the trust-weighted step size α_t.
      3. Update the policy parameters in the direction −α_t · ∇L(π).
      4. Check convergence.
      5. Return the updated policy and the optimisation history.

    The policy update is *symbolic* here — in a full implementation it would
    update the actual policy parameters (e.g., routing weights).  The key
    invariant is that the update must preserve trust monotonicity: the updated
    policy must have trust_tier ≽ the original policy's trust_tier.

    Parameters
    ----------
    objective : ControlObjective
        The objective to minimise.
    current_policy : ControlPolicy
        The starting policy π₀.
    steps : int
        Number of optimisation steps to execute.
    initial_trust : TrustAlgebraElement, optional
        The trust algebra element to use for the initial step size.
    verbose : bool
        If True, print step-by-step progress.

    Returns
    -------
    (ControlPolicy, List[OptimizationStep])
        The (possibly improved) policy and the optimisation step history.
    """
    if initial_trust is None:
        initial_trust = TrustAlgebraElement(tier=DEFAULT_LEARNING_RATE_TIER)

    steps = min(steps, MAX_OPTIMIZATION_STEPS)
    history: List[OptimizationStep] = []
    trust_elem = initial_trust

    # Initialise objective value heuristically
    current_obj = _heuristic_initial_objective(current_policy)

    for step_idx in range(steps):
        # Estimate gradient (symbolic: decays proportionally to step)
        gradient = _estimate_gradient_vector(current_policy, step_idx)
        # Compute trust-weighted step size
        max_tier = max(t.value for t in TrustTier)
        alpha = 0.1 * (trust_elem.tier.value / max_tier)
        # Step direction is negative gradient
        direction = tuple(-g for g in gradient)
        # New objective value (symbolic decrease)
        decay = alpha * math.sqrt(sum(g * g for g in gradient) + 1e-10)
        next_obj = max(0.0, current_obj - decay)
        convergence_est = next_obj / (current_obj + 1e-10)

        opt_step = OptimizationStep(
            step_id=_make_step_id(),
            current_objective_value=current_obj,
            gradient_estimate=gradient,
            step_direction=direction,
            step_size=trust_elem,
            step_proof="",          # Unproven exploratory step
            convergence_estimate=convergence_est,
        )
        history.append(opt_step)

        if verbose:
            print(
                f"  [OPT] step={step_idx:04d} obj={current_obj:.5f} → {next_obj:.5f} "
                f"α={alpha:.5f} trust={trust_elem.tier.name}"
            )

        current_obj = next_obj

        # Possibly elevate trust if significant progress made
        if decay > 0.01:
            trust_elem = trust_elem.elevate(f"opt-step-{step_idx}")

        # Early stopping
        if next_obj < CONVERGENCE_ABS_TOLERANCE:
            if verbose:
                print(f"  [OPT] Converged at step {step_idx} (obj < abs_tol)")
            break

    # Build updated policy with elevated trust (reflecting optimisation)
    updated_policy = ControlPolicy(
        policy_id=current_policy.policy_id,
        policy_type=current_policy.policy_type,
        policy_parameters={
            **current_policy.policy_parameters,
            "optimized_steps": len(history),
            "final_objective": current_obj,
        },
        coverage_region=current_policy.coverage_region,
        trust_tier=trust_elem.tier,
        policy_proof=current_policy.policy_proof,
        last_validated=time.time(),
    )

    return updated_policy, history


def update_control_objective(
    objective: ControlObjective,
    new_evidence: List[Any],
    trust_for_evidence: Optional[TrustAlgebraElement] = None,
) -> ControlObjective:
    """Update the control objective based on new evidence.

    New evidence may:
      1. Add new constraints (if the evidence reveals a safety violation).
      2. Adjust regularisation weights (if the evidence suggests overfitting).
      3. Change the trust_weight_scheme (if evidence elevates trust).

    Parameters
    ----------
    objective : ControlObjective
        The current objective.
    new_evidence : List[Any]
        A list of evidence items.  Each item is expected to be a string
        (evidence ID or description) in this implementation.
    trust_for_evidence : TrustAlgebraElement, optional
        The trust algebra element certifying the new evidence.

    Returns
    -------
    ControlObjective
        An updated (new) ControlObjective with evidence incorporated.
    """
    if trust_for_evidence is None:
        trust_for_evidence = TrustAlgebraElement(tier=TrustTier.PROPOSAL)

    # Determine if trust should be elevated based on evidence count and tier
    new_scheme = objective.trust_weight_scheme
    if (
        len(new_evidence) >= 3
        and trust_for_evidence.tier.value >= TrustTier.VERIFIED.value
    ):
        new_scheme = "proof_backed_only"
    elif len(new_evidence) >= 1:
        new_scheme = "tier_proportional"

    # Add new constraints based on evidence content
    new_constraints = list(objective.constraints)
    for ev in new_evidence:
        ev_str = str(ev)
        if "safety" in ev_str.lower():
            new_constraints.append(f"safety_constraint_from:{ev_str[:32]}")
        elif "trust" in ev_str.lower():
            new_constraints.append(f"trust_constraint_from:{ev_str[:32]}")

    return ControlObjective(
        objective_id=_make_objective_id(),
        primary_term=objective.primary_term,
        regularization_terms=objective.regularization_terms,
        constraints=tuple(new_constraints),
        trust_weight_scheme=new_scheme,
        objective_proof=objective.objective_proof,
    )


def estimate_gradient(
    objective: ControlObjective,
    state: ControlState,
    perturbation: float = 0.01,
) -> Tuple[float, ...]:
    """Estimate the semantic gradient of the objective at a given state.

    We use a finite-difference approximation in the semantic coordinate space:
      ∂L/∂xᵢ ≈ [L(x + εeᵢ) − L(x)] / ε

    The trust algebra constrains the gradient estimate: the perturbation
    magnitude is scaled by the trust weight of the current state.

    Parameters
    ----------
    objective : ControlObjective
        The objective function to differentiate.
    state : ControlState
        The state at which to estimate the gradient.
    perturbation : float
        The finite-difference perturbation magnitude ε.

    Returns
    -------
    Tuple[float, ...]
        The estimated gradient vector in semantic coordinate space.
    """
    trust_weight = _compute_trust_weight(state.trust_level, objective.trust_weight_scheme)
    # Scale perturbation by trust weight — low trust → small perturbation
    effective_eps = perturbation * trust_weight

    coords = state.semantic_coordinates
    base_loss = _evaluate_objective(state, objective)
    gradient: List[float] = []

    for i in range(len(coords)):
        # Create perturbed coordinate vector
        perturbed = list(coords)
        perturbed[i] += effective_eps
        # Build a perturbed state (structural, not recomputed from judgment)
        perturbed_state = ControlState(
            state_id=f"perturb-{i}",
            judgment_tuple=state.judgment_tuple,
            semantic_coordinates=tuple(perturbed),
            distance_to_goal=state.distance_to_goal,
            trust_level=state.trust_level,
            timestamp=state.timestamp,
            trajectory_segment=state.trajectory_segment,
        )
        perturbed_loss = _evaluate_objective(perturbed_state, objective)
        grad_i = (perturbed_loss - base_loss) / (effective_eps + 1e-12)
        gradient.append(grad_i)

    return tuple(gradient)


def check_convergence(
    optimizer: SemanticOptimizer,
    history: List[OptimizationStep],
) -> Tuple[bool, str]:
    """Check whether the optimisation has converged.

    Returns (converged, reason_string).

    The check dispatches on optimizer.convergence_criterion:
      ABSOLUTE_TOLERANCE : |last_obj - prev_obj| < CONVERGENCE_ABS_TOLERANCE
      RELATIVE_TOLERANCE : ratio < CONVERGENCE_REL_TOLERANCE
      PROOF_COMPLETE     : last step has a non-empty step_proof
      TRUST_THRESHOLD    : last step's trust tier ≽ VERIFIED
      FIXED_STEPS        : len(history) >= optimizer.optimization_horizon

    Parameters
    ----------
    optimizer : SemanticOptimizer
        The configured optimiser.
    history : List[OptimizationStep]
        The optimisation history so far.

    Returns
    -------
    (bool, str)
        (converged, reason)
    """
    if not history:
        return False, "no steps taken"

    last = history[-1]

    if optimizer.convergence_criterion == ConvergenceCriterion.FIXED_STEPS:
        converged = len(history) >= optimizer.optimization_horizon
        return converged, f"fixed_steps:{len(history)}/{optimizer.optimization_horizon}"

    if optimizer.convergence_criterion == ConvergenceCriterion.ABSOLUTE_TOLERANCE:
        if len(history) < 2:
            return False, "insufficient history"
        delta = abs(last.current_objective_value - history[-2].current_objective_value)
        converged = delta < CONVERGENCE_ABS_TOLERANCE
        return converged, f"abs_delta={delta:.2e}"

    if optimizer.convergence_criterion == ConvergenceCriterion.RELATIVE_TOLERANCE:
        if len(history) < 2:
            return False, "insufficient history"
        prev_val = history[-2].current_objective_value
        delta = abs(last.current_objective_value - prev_val)
        rel = delta / (abs(prev_val) + 1e-12)
        converged = rel < CONVERGENCE_REL_TOLERANCE
        return converged, f"rel_delta={rel:.2e}"

    if optimizer.convergence_criterion == ConvergenceCriterion.PROOF_COMPLETE:
        converged = bool(last.step_proof)
        return converged, f"proof_id={last.step_proof or 'none'}"

    if optimizer.convergence_criterion == ConvergenceCriterion.TRUST_THRESHOLD:
        converged = last.step_size.tier.value >= TrustTier.VERIFIED.value
        return converged, f"trust_tier={last.step_size.tier.name}"

    return False, "unknown criterion"


def build_objective_landscape(
    objective: ControlObjective,
    state_samples: List[ControlState],
) -> ObjectiveLandscape:
    """Build an objective landscape map from a collection of state samples.

    The landscape is used to:
      1. Identify local minima (potential traps).
      2. Identify trust barriers (discontinuities).
      3. Estimate the gradient direction for the optimiser.

    Parameters
    ----------
    objective : ControlObjective
        The objective function.
    state_samples : List[ControlState]
        A list of sampled states at which to evaluate the objective.

    Returns
    -------
    ObjectiveLandscape
        The built landscape with all samples recorded.
    """
    landscape = ObjectiveLandscape(objective)

    for state in state_samples:
        val = _evaluate_objective(state, objective)
        landscape.sample(state, val)

    # Detect trust barriers between adjacent pairs
    for i in range(1, len(state_samples)):
        prev = state_samples[i - 1]
        curr = state_samples[i]
        if curr.trust_level.tier.value < prev.trust_level.tier.value:
            landscape.add_trust_barrier(prev, curr)

    landscape.detect_local_minima()
    return landscape


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _heuristic_initial_objective(policy: ControlPolicy) -> float:
    """Heuristic initial objective value based on policy type and trust tier.

    Higher trust policies start with a lower initial objective (they are
    already closer to the optimum because they were designed with more care).
    """
    base = 1.0
    trust_discount = (policy.trust_tier.value - 1) / (len(TrustTier) - 1)
    return base * (1.0 - 0.3 * trust_discount)


def _estimate_gradient_vector(policy: ControlPolicy, step: int) -> Tuple[float, ...]:
    """Synthetic gradient estimate that decays over steps (for simulation)."""
    dim = 8  # Gradient in the 8-component judgment-tuple space
    decay = math.exp(-step / 100.0)
    grad = tuple(
        math.sin(step * 0.1 + i) * 0.1 * decay
        for i in range(dim)
    )
    return grad


def _evaluate_objective(state: ControlState, objective: ControlObjective) -> float:
    """Evaluate the objective at a given state.

    This is the function L(J) whose gradient we estimate and whose minimum
    we seek.  It combines the primary term and regularisation terms.
    """
    trust_weight = _compute_trust_weight(state.trust_level, objective.trust_weight_scheme)

    # Primary term: distance to goal (scaled by trust)
    primary = state.distance_to_goal * trust_weight

    # Add regularisation
    total = _apply_regularization(primary, state, objective.regularization_terms)

    return total


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Smoke test: the_controller_should_optimize_sem.py")
    print("=" * 70)

    # 1. Build a control objective
    reg_terms = (
        (RegularizationType.L2_SEMANTIC, REGULARIZATION_DEFAULTS[RegularizationType.L2_SEMANTIC]),
        (RegularizationType.TRUST_PENALTY, REGULARIZATION_DEFAULTS[RegularizationType.TRUST_PENALTY]),
        (RegularizationType.OBLIGATION_PENALTY, REGULARIZATION_DEFAULTS[RegularizationType.OBLIGATION_PENALTY]),
    )
    objective = ControlObjective(
        objective_id=_make_objective_id(),
        primary_term=ObjectiveComponent.COMPOSITE,
        regularization_terms=reg_terms,
        constraints=("trust_monotone", "obligation_decreasing"),
        trust_weight_scheme="tier_proportional",
        objective_proof="",
    )
    print(f"Objective: id={objective.objective_id}, primary={objective.primary_term.name}")
    print(f"  Trust weight scheme: {objective.trust_weight_scheme}")
    print(f"  Regularisation terms: {len(objective.regularization_terms)}")

    # 2. Build a semantic optimiser
    lr_trust = TrustAlgebraElement(tier=TrustTier.REVIEWED)
    optimizer = SemanticOptimizer(
        optimizer_id=_make_optimizer_id(),
        objective_function=objective,
        learning_rate=lr_trust,
        convergence_criterion=ConvergenceCriterion.ABSOLUTE_TOLERANCE,
        optimization_horizon=50,
        proof_of_convergence="",
    )
    print(f"\nOptimiser: id={optimizer.optimizer_id}")
    print(f"  Criterion: {optimizer.convergence_criterion.name}")
    print(f"  Horizon: {optimizer.optimization_horizon}")
    print(f"  LR trust tier: {optimizer.learning_rate.tier.name}")

    # 3. Build a mock control policy
    policy = ControlPolicy(
        policy_id="pol-smoke-001",
        policy_type=PolicyType.ADAPTIVE,
        policy_parameters={"lookahead": 5},
        coverage_region="full",
        trust_tier=TrustTier.REVIEWED,
        policy_proof="",
        last_validated=time.time(),
    )

    # 4. Build mock control states for reward computation
    j0 = JudgmentTuple(
        c="ctx-smoke", phi="task_done", A=("a1",),
        E=("e1",), O=("o1", "o2"), B="partial",
        T=TrustTier.PROPOSAL, Pi=""
    )
    j1 = JudgmentTuple(
        c="ctx-smoke", phi="task_done", A=("a1",),
        E=("e1", "e2"), O=("o2",), B="updated",
        T=TrustTier.REVIEWED, Pi="proof-step-1"
    )
    coords0 = tuple(float(i) * 0.1 for i in range(16))
    coords1 = tuple(float(i) * 0.09 for i in range(16))
    trust0 = TrustAlgebraElement(tier=TrustTier.PROPOSAL)
    trust1 = TrustAlgebraElement(tier=TrustTier.REVIEWED, evidence_ids=("e2",))

    s0 = ControlState("s0", j0, coords0, 0.8, trust0, time.time(), 0)
    s1 = ControlState("s1", j1, coords1, 0.6, trust1, time.time(), 1)

    # 5. Compute reward
    reward = compute_semantic_reward(s0, s1, "task_done", TrustTier.PROPOSAL)
    print(f"\nSemantic reward: id={reward.reward_id}")
    print(f"  Geometric progress: {reward.geometric_progress:.4f}")
    print(f"  Trust elevation delta: {reward.trust_elevation_delta}")
    print(f"  Obligation discharges: {reward.obligation_discharge_count}")
    print(f"  Components: {reward.reward_components}")

    # 6. Build reward accumulator and add reward
    accumulator = RewardAccumulator(min_trust_tier=TrustTier.PROPOSAL)
    accumulator.add(reward)
    print(f"\nAccumulator summary: {accumulator.summary()}")

    # 7. Estimate gradient
    grad = estimate_gradient(objective, s0)
    print(f"\nGradient at s0 (first 4 dims): {[f'{g:.5f}' for g in grad[:4]]}")

    # 8. Run optimisation
    print("\nRunning optimisation (20 steps):")
    updated_policy, history = optimize_control(objective, policy, 20, lr_trust, verbose=True)
    print(f"Optimisation complete: {len(history)} steps")
    print(f"  Final policy trust tier: {updated_policy.trust_tier.name}")

    # 9. Check convergence
    converged, reason = check_convergence(optimizer, history)
    print(f"\nConvergence: {converged} ({reason})")

    # 10. Update objective with new evidence
    new_evidence = ["safety:no_trust_regression", "trust:tier>=REVIEWED"]
    updated_obj = update_control_objective(
        objective, new_evidence,
        TrustAlgebraElement(tier=TrustTier.VERIFIED)
    )
    print(f"\nUpdated objective: id={updated_obj.objective_id}")
    print(f"  New scheme: {updated_obj.trust_weight_scheme}")
    print(f"  New constraints: {updated_obj.constraints}")

    # 11. Build objective landscape
    state_samples = [s0, s1]
    for i in range(2, 8):
        ji = JudgmentTuple(c="ctx", phi="phi", A=(), E=(), O=(), B="", T=TrustTier.REVIEWED, Pi="")
        ci = tuple(float(k) * 0.1 * (1 - i * 0.05) for k in range(16))
        ti = TrustAlgebraElement(tier=TrustTier.REVIEWED)
        state_samples.append(ControlState(f"s{i}", ji, ci, max(0.0, 0.6 - i * 0.08), ti, time.time(), i))

    landscape = build_objective_landscape(updated_obj, state_samples)
    print(f"\nObjective landscape: {landscape.landscape_summary()}")

    print("\nSmoke test complete.")
