"""
Orchestration IS a Control Problem.

# copilot: This module formalises orchestration as a semantic control problem. The
orchestrator is not a scheduler or a dispatcher — it is a *controller* in the
control-theoretic sense: it observes a semantic state, applies a control law, and
drives the system toward a goal state while guaranteeing invariants.

Core insight
============
Classical control theory: given a state x(t) in state-space X and a goal x*, find
a control input u(t) such that x(t) → x* as t → ∞ while x(t) satisfies safety
constraints.

Semantic control theory (this module): given a judgment tuple
  J = (c, φ, A, E, O, B, T, Π)
and a goal formula φ*, find a sequence of routing decisions u₀, u₁, … such that
  J(t) → J*(t) as t → ∞
where J* is a judgment tuple satisfying φ*, with trust T elevated to PROOF_BACKED,
obligations O fully discharged, and proof object Π complete.

Trust algebra constraint
========================
Admissible controls u must respect the ordered algebra
  (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
A control u is admissible only if applying it does not lower the trust tier below
the current tier's floor — i.e. trust is monotone along the control trajectory.

Lyapunov stability
==================
We seek a Lyapunov candidate V: J-space → ℝ≥0 such that:
  1. V(J*) = 0
  2. V(J) > 0 for J ≠ J*
  3. ΔV = V(J_{t+1}) − V(J_t) < 0 along any admissible trajectory

The semantic distance d(J, J*) serves as V.  Proof that ΔV < 0 is a *discharge* of
the liveness obligation O_progress.
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
except ImportError:
    # Stub definitions used when jugeo core is not installed.
    class TrustTier(Enum):  # type: ignore
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5

    @dataclass(frozen=True)
    class TrustAlgebraElement:  # type: ignore
        """Stub: element of the ordered trust algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)."""
        tier: TrustTier = TrustTier.PROPOSAL
        evidence_ids: Tuple[str, ...] = ()
        lattice_height: int = 0

        def join(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            """⊕ — least upper bound in the trust lattice."""
            higher = max(self.tier.value, other.tier.value)
            combined = self.evidence_ids + other.evidence_ids
            return TrustAlgebraElement(
                tier=TrustTier(higher),
                evidence_ids=combined,
                lattice_height=max(self.lattice_height, other.lattice_height) + 1,
            )

        def meet(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            """⊖ — greatest lower bound."""
            lower = min(self.tier.value, other.tier.value)
            return TrustAlgebraElement(
                tier=TrustTier(lower),
                evidence_ids=(),
                lattice_height=min(self.lattice_height, other.lattice_height),
            )

        def elevate(self, proof_id: str) -> TrustAlgebraElement:
            """↑_π — elevate trust by one tier upon proof discharge."""
            new_tier_val = min(self.tier.value + 1, max(t.value for t in TrustTier))
            return TrustAlgebraElement(
                tier=TrustTier(new_tier_val),
                evidence_ids=self.evidence_ids + (proof_id,),
                lattice_height=self.lattice_height + 1,
            )

        def demote(self, counterexample_id: str) -> TrustAlgebraElement:
            """↓_χ — demote trust upon counterexample."""
            new_tier_val = max(self.tier.value - 1, 1)
            return TrustAlgebraElement(
                tier=TrustTier(new_tier_val),
                evidence_ids=self.evidence_ids,
                lattice_height=max(self.lattice_height - 1, 0),
            )

    @dataclass(frozen=True)
    class JudgmentTuple:  # type: ignore
        """Stub: J = (c, φ, A, E, O, B, T, Π)."""
        c: str = ""          # context
        phi: str = ""        # formula
        A: Tuple[str, ...] = ()   # agent-set
        E: Tuple[str, ...] = ()   # evidence-set
        O: Tuple[str, ...] = ()   # obligation-set
        B: str = ""          # belief-state
        T: TrustTier = TrustTier.PROPOSAL
        Pi: str = ""         # proof-object id

    @dataclass(frozen=True)
    class ProofObject:  # type: ignore
        """Stub: a proof object referenced from judgment tuples."""
        proof_id: str = ""
        strategy: str = "none"
        steps: Tuple[str, ...] = ()
        is_complete: bool = False

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PolicyType(Enum):
    """The type of control policy employed by the orchestrator.

    Each value corresponds to a different trade-off between optimality,
    robustness, computational cost, and formal verifiability.
    """
    GREEDY = auto()
    """Take the locally best control action at each step.  Fast but may miss the
    globally optimal trajectory.  Requires only REVIEWED trust tier."""

    OPTIMAL = auto()
    """Solve for the globally optimal control sequence.  Requires full model and
    VERIFIED trust tier.  Corresponds to solving the Bellman equation in semantic
    space."""

    ROBUST = auto()
    """Minimise worst-case semantic distance under model uncertainty.  Requires
    RUNTIME_WITNESSED trust tier — the uncertainty set is bounded by observed
    evidence."""

    ADAPTIVE = auto()
    """Adapt the policy online as new evidence arrives.  The policy is a function
    of the current judgment tuple, allowing the control law to change as trust
    is elevated."""

    PROOF_GUIDED = auto()
    """Each control step is guided by a partial proof of the goal formula.  The
    policy selects actions that discharge proof obligations.  Requires PROOF_BACKED
    trust tier for the policy itself."""

    TRUST_WEIGHTED = auto()
    """Control actions are weighted by the trust algebra element associated with
    each candidate agent.  The ⊕ operator resolves conflicts."""


class ControlHorizon(Enum):
    """The time horizon over which the control problem is formulated."""
    SINGLE_STEP = auto()
    """React optimally to the current state without considering future states.
    Appropriate for stateless routing decisions."""

    FINITE_HORIZON = auto()
    """Plan over a fixed number of future steps.  The control policy is a sequence
    u₀, u₁, …, u_T.  Requires the semantic state model to be Markovian."""

    INFINITE_HORIZON = auto()
    """Plan over an unbounded future.  Requires a discount factor and a Lyapunov
    stability certificate for the closed-loop system."""

    RECEDING_HORIZON = auto()
    """Model-predictive control: solve a finite-horizon problem at each step and
    apply only the first control action.  Balances optimality and adaptivity."""


class SemanticMetric(Enum):
    """The metric used to measure distance between semantic states."""
    JUDGMENT_DISTANCE = auto()
    """Structural distance between judgment tuples: count of differing components,
    normalised by the total number of components (8 for the standard tuple)."""

    OBLIGATION_COVERAGE = auto()
    """Fraction of obligations in O that have been discharged.  Higher coverage
    = smaller distance from the goal (all obligations discharged)."""

    PROOF_DEPTH = auto()
    """Depth of the current partial proof Π.  Deeper proofs are closer to the
    goal certificate."""

    TRUST_ELEVATION = auto()
    """Distance in the trust lattice: number of elevation steps ↑_π needed to
    reach PROOF_BACKED from the current tier T."""

    COMPOSITE = auto()
    """Weighted combination of all metrics above.  The weights themselves are
    elements of the trust algebra — higher-trust components get higher weight."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlPolicy:
    """A control policy that maps semantic states to control actions.

    In classical control theory a policy π: X → U maps states to inputs.
    Here the state space X is the space of judgment tuples and the input
    space U is the space of routing / generation decisions.

    The ``policy_proof`` field is a reference to a proof object that certifies
    the policy is admissible: it does not violate trust monotonicity and it
    makes progress toward the goal under the chosen metric.

    Trust requirement: the policy's trust_tier must be ≽ REVIEWED before the
    policy is eligible for deployment.  It must be ≽ PROOF_BACKED before it
    can be used in a PROOF_GUIDED horizon.
    """
    policy_id: str
    policy_type: PolicyType
    policy_parameters: Dict[str, Any]
    coverage_region: str           # description of the state-space region covered
    trust_tier: TrustTier
    policy_proof: str              # proof_id or empty string if unproven
    last_validated: float          # Unix timestamp


@dataclass(frozen=True)
class ControlState:
    """A point in the semantic control state space.

    The judgment tuple J = (c, φ, A, E, O, B, T, Π) *is* the state.  The
    ``semantic_coordinates`` field is an auxiliary real-valued vector that
    embeds J into ℝⁿ for distance computations — it is derived from J, not
    independently authoritative.

    ``distance_to_goal`` is the current value of the Lyapunov candidate V(J).
    It must be non-negative and must strictly decrease along any admissible
    trajectory (Lyapunov stability condition).

    ``trajectory_segment`` identifies which numbered step in the current
    trajectory this state corresponds to (0 = initial state).
    """
    state_id: str
    judgment_tuple: JudgmentTuple
    semantic_coordinates: Tuple[float, ...]   # embedding of J into ℝⁿ
    distance_to_goal: float                   # V(J) ≥ 0; V(J*) = 0
    trust_level: TrustAlgebraElement
    timestamp: float                          # Unix timestamp
    trajectory_segment: int                   # step index in trajectory


@dataclass(frozen=True)
class SemanticControlProblem:
    """The formal specification of a semantic control problem.

    Analogous to the standard LQR/MPC problem specification but over the
    space of judgment tuples rather than ℝⁿ.

    Fields
    ------
    initial_state   : the starting judgment tuple J₀
    goal_state      : the desired judgment tuple J* (may be partially specified)
    semantic_metric : the metric d: J-space × J-space → ℝ≥0
    constraints     : a sequence of constraint formulas that must hold along
                      the trajectory (safety constraints)
    horizon         : the control horizon type
    admissible_controls : descriptions of the admissible control input set U

    The well-formedness condition: the goal_state must have T = PROOF_BACKED
    (we are aiming for a provably correct endpoint) and all obligations in
    goal_state.O must be the empty tuple (all discharged).
    """
    problem_id: str
    initial_state: ControlState
    goal_state: ControlState
    semantic_metric: SemanticMetric
    constraints: Tuple[str, ...]          # safety constraint formulas
    horizon: ControlHorizon
    admissible_controls: Tuple[str, ...]  # descriptions of admissible inputs


@dataclass(frozen=True)
class OrchestrationController:
    """The top-level controller object for semantic orchestration.

    This is the *design-time* description of the controller.  At runtime,
    the controller executes ``control_policy`` against the current state to
    produce the next routing decision.

    ``lyapunov_candidate`` is a formula string describing V(J).  It must be
    instantiable at runtime to a non-negative real number for any J.  Typical
    choices:
      - "obligation_count(J)" — number of undischarged obligations
      - "trust_gap(J)" — number of trust elevation steps to PROOF_BACKED
      - "proof_completeness_gap(J)" — fraction of proof steps not yet completed
      - "composite_weighted(J)" — trust-algebra-weighted combination

    ``state_space_dimension`` is the dimensionality n of the embedding ℝⁿ
    used for ``ControlState.semantic_coordinates``.  For the standard 8-component
    judgment tuple, n ≥ 8.
    """
    controller_id: str
    control_horizon: ControlHorizon
    state_space_dimension: int
    semantic_goal: str             # natural-language or formal goal formula
    control_policy: ControlPolicy
    trust_tier: TrustTier
    lyapunov_candidate: str        # formula for V(J)


# ---------------------------------------------------------------------------
# Mutable helper classes
# ---------------------------------------------------------------------------

class SemanticStateSpace:
    """Represents the space of all possible semantic states (judgment tuples).

    This class is *not* frozen because the state space itself evolves as new
    agents are registered, new evidence is admitted, and the trust algebra is
    updated.

    The key operation is ``distance(s1, s2)`` which computes d(J₁, J₂) under
    the configured metric.  The distance is *not* required to be a metric in
    the strict mathematical sense (it need not satisfy the triangle inequality
    when trust tiers differ), but it must satisfy:
      1. d(J, J) = 0
      2. d(J₁, J₂) ≥ 0
      3. d(J₁, J₂) = 0 ⟹ J₁ structurally equivalent to J₂
    """

    def __init__(
        self,
        dimension: int,
        metric: SemanticMetric,
        trust_floor: TrustTier = TrustTier.PROPOSAL,
    ) -> None:
        self.dimension = dimension
        self.metric = metric
        self.trust_floor = trust_floor
        self._states: Dict[str, ControlState] = {}
        self._distance_cache: Dict[Tuple[str, str], float] = {}

    def register_state(self, state: ControlState) -> None:
        """Register a new state in the space."""
        self._states[state.state_id] = state
        # Invalidate any cached distances involving this state
        keys_to_remove = [k for k in self._distance_cache if state.state_id in k]
        for k in keys_to_remove:
            del self._distance_cache[k]

    def distance(self, s1: ControlState, s2: ControlState) -> float:
        """Compute semantic distance d(J₁, J₂) under self.metric.

        The computation dispatches on self.metric and delegates to the
        appropriate component-specific distance function.
        """
        cache_key = (s1.state_id, s2.state_id)
        if cache_key in self._distance_cache:
            return self._distance_cache[cache_key]

        d = compute_semantic_distance(s1, s2)
        self._distance_cache[cache_key] = d
        return d

    def is_goal(self, state: ControlState, goal: ControlState, tol: float = 1e-6) -> bool:
        """Return True if state is within tolerance of the goal."""
        return self.distance(state, goal) < tol

    def admissible_from(self, state: ControlState, policy: ControlPolicy) -> List[str]:
        """Return the list of admissible control action identifiers from this state.

        Admissibility is determined by:
          1. The policy type (GREEDY, OPTIMAL, etc.)
          2. The trust floor: only agents with tier ≽ trust_floor are eligible
          3. The coverage region of the policy
        """
        # In a full implementation this would query the agent registry.
        # Here we return a symbolic list.
        if state.trust_level.tier.value < self.trust_floor.value:
            return []  # No admissible controls below the trust floor
        return [f"action_{i}" for i in range(3)]  # Stub: 3 candidate actions


class ControlTrajectory:
    """A sequence of control states forming a trajectory toward the goal.

    A trajectory τ = J₀, J₁, …, J_T is *valid* if:
      1. J₀ = initial_state of the control problem
      2. d(J_t, J*) > d(J_{t+1}, J*) for all t (strict Lyapunov decrease)
      3. Each J_t satisfies all safety constraints
      4. trust_level(J_t) is non-decreasing in the trust lattice

    The ``is_valid`` property performs a lightweight check (O(T) in trajectory
    length).  Full verification requires calling ``verify_control_invariant``.
    """

    def __init__(self, problem: SemanticControlProblem) -> None:
        self.problem = problem
        self.states: List[ControlState] = []
        self.control_actions: List[str] = []
        self._lyapunov_values: List[float] = []

    def append(self, state: ControlState, action: str = "") -> None:
        """Append a new state to the trajectory."""
        self.states.append(state)
        self.control_actions.append(action)
        self._lyapunov_values.append(state.distance_to_goal)

    @property
    def length(self) -> int:
        return len(self.states)

    @property
    def is_lyapunov_decreasing(self) -> bool:
        """Check that distance_to_goal is strictly decreasing."""
        for i in range(1, len(self._lyapunov_values)):
            if self._lyapunov_values[i] >= self._lyapunov_values[i - 1]:
                return False
        return True

    @property
    def is_trust_monotone(self) -> bool:
        """Check that trust level is non-decreasing in the lattice."""
        for i in range(1, len(self.states)):
            prev = self.states[i - 1].trust_level.tier.value
            curr = self.states[i].trust_level.tier.value
            if curr < prev:
                return False
        return True

    @property
    def is_valid(self) -> bool:
        return (
            self.length > 0
            and self.is_lyapunov_decreasing
            and self.is_trust_monotone
        )

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict for logging / inspection."""
        return {
            "length": self.length,
            "initial_distance": self._lyapunov_values[0] if self._lyapunov_values else None,
            "final_distance": self._lyapunov_values[-1] if self._lyapunov_values else None,
            "lyapunov_decreasing": self.is_lyapunov_decreasing,
            "trust_monotone": self.is_trust_monotone,
            "valid": self.is_valid,
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JUDGMENT_TUPLE_COMPONENTS: int = 8
"""The canonical number of components in a judgment tuple (c, φ, A, E, O, B, T, Π)."""

TRUST_TIER_COUNT: int = len(TrustTier)
"""Number of trust tiers in the ordered algebra."""

DEFAULT_LYAPUNOV_TOLERANCE: float = 1e-6
"""Default tolerance for declaring V(J) ≈ 0 (goal reached)."""

MAX_CONTROL_ITERATIONS: int = 1000
"""Safety limit on the number of control steps to prevent infinite loops."""

SEMANTIC_EMBEDDING_DIM: int = 32
"""Default dimensionality of the semantic embedding space ℝⁿ."""

CONTROL_STEP_LOG_TEMPLATE: str = (
    "[CTRL] step={step:04d} | dist={dist:.4f} | trust={trust} | action={action}"
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _judgment_tuple_to_coords(j: JudgmentTuple, dim: int = SEMANTIC_EMBEDDING_DIM) -> Tuple[float, ...]:
    """Embed a judgment tuple into ℝⁿ.

    This is a *deterministic* embedding — the same judgment tuple always maps
    to the same coordinates.  We use a hash-based projection so that structurally
    similar tuples land close together in the embedding space.

    In a production system this would use a learned semantic embedding trained
    on a corpus of judgment tuples.  Here we use a simple hash projection.
    """
    canonical = f"{j.c}|{j.phi}|{j.A}|{j.E}|{j.O}|{j.B}|{j.T}|{j.Pi}"
    digest = hashlib.sha256(canonical.encode()).digest()
    # Map each byte pair to a float in [-1, 1]
    coords: List[float] = []
    for i in range(min(dim, len(digest) // 2)):
        val = (digest[2 * i] * 256 + digest[2 * i + 1]) / 32767.5 - 1.0
        coords.append(val)
    # Pad with zeros if dim > len(digest) // 2
    while len(coords) < dim:
        coords.append(0.0)
    return tuple(coords[:dim])


def _euclidean_distance(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    """Compute Euclidean distance between two coordinate vectors."""
    if len(a) != len(b):
        raise ValueError(f"Coordinate dimension mismatch: {len(a)} vs {len(b)}")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _obligation_distance(j1: JudgmentTuple, j2: JudgmentTuple) -> float:
    """Compute the obligation-coverage distance between two judgment tuples.

    d_O(J₁, J₂) = |O₁ Δ O₂| / max(|O₁|, |O₂|, 1)
    where Δ is the symmetric difference.
    """
    o1 = set(j1.O)
    o2 = set(j2.O)
    symmetric_diff = o1.symmetric_difference(o2)
    denom = max(len(o1), len(o2), 1)
    return len(symmetric_diff) / denom


def _trust_distance(t1: TrustTier, t2: TrustTier) -> float:
    """Distance in the trust lattice: |tier(T₁) - tier(T₂)| / (TRUST_TIER_COUNT - 1)."""
    return abs(t1.value - t2.value) / (TRUST_TIER_COUNT - 1)


def _proof_depth_distance(pi1: str, pi2: str) -> float:
    """Proxy distance based on proof object IDs.

    In a full system this would inspect the actual proof trees.  Here we use
    string length as a proxy for proof depth (longer IDs = deeper proofs).
    """
    max_len = max(len(pi1), len(pi2), 1)
    return abs(len(pi1) - len(pi2)) / max_len


def _make_state_id() -> str:
    return f"cs-{uuid.uuid4().hex[:12]}"


def _make_policy_id() -> str:
    return f"cp-{uuid.uuid4().hex[:12]}"


def _make_problem_id() -> str:
    return f"scp-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_semantic_distance(state1: ControlState, state2: ControlState) -> float:
    """Compute the semantic distance d(J₁, J₂) between two control states.

    The composite distance is a trust-weighted combination:
      d(J₁, J₂) = w_coord · d_coord + w_oblig · d_O + w_trust · d_T + w_proof · d_Π

    where:
      d_coord — Euclidean distance in semantic embedding space
      d_O     — obligation-coverage distance
      d_T     — trust-lattice distance
      d_Π     — proof-depth distance proxy

    Weights are fixed at design time.  In an adaptive controller the weights
    would be updated by the trust algebra as new evidence arrives.

    Returns
    -------
    float
        Non-negative semantic distance.  Equal to 0.0 iff the two states are
        structurally equivalent in all judgment-tuple components.
    """
    # Component distances
    d_coord = _euclidean_distance(
        state1.semantic_coordinates, state2.semantic_coordinates
    )
    d_oblig = _obligation_distance(state1.judgment_tuple, state2.judgment_tuple)
    d_trust = _trust_distance(
        state1.judgment_tuple.T, state2.judgment_tuple.T
    )
    d_proof = _proof_depth_distance(
        state1.judgment_tuple.Pi, state2.judgment_tuple.Pi
    )

    # Trust-weighted combination.  Higher-trust states contribute more weight
    # to the trust and proof components, reflecting that they carry more
    # semantic information.
    trust_weight = state1.trust_level.tier.value / TRUST_TIER_COUNT
    w_coord = 0.4
    w_oblig = 0.3
    w_trust = 0.15 + 0.10 * trust_weight
    w_proof = 0.15 - 0.10 * trust_weight  # proof matters less when already trusted

    return w_coord * d_coord + w_oblig * d_oblig + w_trust * d_trust + w_proof * d_proof


def formulate_control_problem(
    initial_judgment: JudgmentTuple,
    goal_formula: str,
    constraints: Sequence[str],
    horizon: ControlHorizon,
    metric: SemanticMetric = SemanticMetric.COMPOSITE,
    trust_floor: TrustTier = TrustTier.PROPOSAL,
) -> SemanticControlProblem:
    """Formulate the semantic control problem.

    This function is the entry point for the orchestrator.  It takes a
    description of the current situation (initial_judgment) and the desired
    outcome (goal_formula) and returns a formal SemanticControlProblem.

    The goal state is constructed from the goal_formula by:
      1. Setting φ = goal_formula
      2. Setting O = () (all obligations discharged)
      3. Setting T = TrustTier.PROOF_BACKED
      4. Setting Π to a placeholder proof object ID

    Parameters
    ----------
    initial_judgment : JudgmentTuple
        The starting state J₀ = (c, φ₀, A, E, O₀, B₀, T₀, Π₀).
    goal_formula : str
        The formula φ* that must be satisfied at the goal state.
    constraints : Sequence[str]
        Safety constraint formulas that must hold throughout the trajectory.
    horizon : ControlHorizon
        The time horizon for the control problem.
    metric : SemanticMetric
        The semantic metric to use for distance computations.
    trust_floor : TrustTier
        Minimum trust tier required for admissible control actions.

    Returns
    -------
    SemanticControlProblem
        The fully specified control problem.
    """
    # Build the initial control state
    init_coords = _judgment_tuple_to_coords(initial_judgment)
    init_trust = TrustAlgebraElement(tier=initial_judgment.T)
    initial_state = ControlState(
        state_id=_make_state_id(),
        judgment_tuple=initial_judgment,
        semantic_coordinates=init_coords,
        distance_to_goal=float("inf"),  # Will be computed once goal is set
        trust_level=init_trust,
        timestamp=time.time(),
        trajectory_segment=0,
    )

    # Build the goal judgment tuple
    goal_judgment = JudgmentTuple(
        c=initial_judgment.c,          # Same context as initial
        phi=goal_formula,
        A=initial_judgment.A,          # Same agent set (may change in adaptive systems)
        E=initial_judgment.E,          # Evidence inherited
        O=(),                           # All obligations discharged
        B=initial_judgment.B,
        T=TrustTier.PROOF_BACKED,      # Goal requires PROOF_BACKED trust
        Pi=f"goal-proof-{uuid.uuid4().hex[:8]}",
    )
    goal_coords = _judgment_tuple_to_coords(goal_judgment)
    goal_trust = TrustAlgebraElement(tier=TrustTier.PROOF_BACKED)
    goal_state = ControlState(
        state_id=_make_state_id(),
        judgment_tuple=goal_judgment,
        semantic_coordinates=goal_coords,
        distance_to_goal=0.0,          # By definition, goal is at distance 0
        trust_level=goal_trust,
        timestamp=time.time(),
        trajectory_segment=-1,         # Sentinel: not part of the trajectory yet
    )

    # Update initial_state with the actual distance-to-goal
    actual_distance = compute_semantic_distance(initial_state, goal_state)
    initial_state = ControlState(
        state_id=initial_state.state_id,
        judgment_tuple=initial_state.judgment_tuple,
        semantic_coordinates=initial_state.semantic_coordinates,
        distance_to_goal=actual_distance,
        trust_level=initial_state.trust_level,
        timestamp=initial_state.timestamp,
        trajectory_segment=0,
    )

    admissible = tuple(
        f"agent-class:trust≽{trust_floor.name}" for _ in range(3)
    )

    return SemanticControlProblem(
        problem_id=_make_problem_id(),
        initial_state=initial_state,
        goal_state=goal_state,
        semantic_metric=metric,
        constraints=tuple(constraints),
        horizon=horizon,
        admissible_controls=admissible,
    )


def design_control_policy(
    problem: SemanticControlProblem,
    strategy: PolicyType,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> ControlPolicy:
    """Design a control policy for the given semantic control problem.

    The policy design procedure:
      1. Determine the policy parameters from the problem specification and strategy.
      2. Construct a coverage region description.
      3. Assign the initial trust tier (REVIEWED by default; elevated later).
      4. Leave policy_proof empty — the policy is unproven until verified.

    For PROOF_GUIDED policies the trust_tier must be ≽ VERIFIED; otherwise
    the policy is only allowed to execute GREEDY or OPTIMAL steps.

    Parameters
    ----------
    problem : SemanticControlProblem
        The control problem for which the policy is being designed.
    strategy : PolicyType
        The policy strategy to use.
    trust_tier : TrustTier
        The initial trust tier of the policy.

    Returns
    -------
    ControlPolicy
        A new control policy object.
    """
    # Enforce trust requirement for PROOF_GUIDED
    if strategy == PolicyType.PROOF_GUIDED and trust_tier.value < TrustTier.VERIFIED.value:
        raise ValueError(
            f"PROOF_GUIDED policy requires VERIFIED trust tier or higher; "
            f"got {trust_tier.name}"
        )

    params: Dict[str, Any] = {
        "metric": problem.semantic_metric.name,
        "horizon": problem.horizon.name,
        "constraints": list(problem.constraints),
        "n_admissible_controls": len(problem.admissible_controls),
    }

    if strategy == PolicyType.GREEDY:
        params["lookahead"] = 1
    elif strategy == PolicyType.OPTIMAL:
        params["lookahead"] = (
            100 if problem.horizon == ControlHorizon.INFINITE_HORIZON else 10
        )
        params["discount_factor"] = 0.95
    elif strategy == PolicyType.ROBUST:
        params["uncertainty_radius"] = 0.1
        params["worst_case_discount"] = 0.9
    elif strategy == PolicyType.ADAPTIVE:
        params["update_rate"] = 0.01
        params["evidence_window"] = 50
    elif strategy == PolicyType.PROOF_GUIDED:
        params["proof_strategy"] = "obligation_discharge_first"
        params["min_trust_for_expansion"] = TrustTier.VERIFIED.name
    elif strategy == PolicyType.TRUST_WEIGHTED:
        params["trust_weight_scheme"] = "lattice_height_normalized"

    coverage = (
        f"full-state-space:dim={problem.initial_state.judgment_tuple.T.value}"
    )

    return ControlPolicy(
        policy_id=_make_policy_id(),
        policy_type=strategy,
        policy_parameters=params,
        coverage_region=coverage,
        trust_tier=trust_tier,
        policy_proof="",      # Unproven at design time
        last_validated=time.time(),
    )


def execute_control_step(
    state: ControlState,
    policy: ControlPolicy,
    trust_algebra: TrustAlgebraElement,
    goal_state: Optional[ControlState] = None,
    verbose: bool = False,
) -> Tuple[ControlState, str]:
    """Execute one step of the control policy.

    Given the current state J_t and the control policy π, compute the next
    state J_{t+1} = f(J_t, π(J_t)) and return it together with the control
    action taken.

    The execution respects the trust algebra constraint: the control action is
    only applied if the resulting trust level satisfies
      trust_algebra ≽ policy.trust_tier
    in the ordered trust lattice.

    In this implementation the state transition is *simulated* by:
      1. Picking the action that minimises estimated distance to goal (GREEDY-like).
      2. Applying a symbolic state update (trust elevation + obligation reduction).
      3. Recomputing semantic coordinates for the new state.

    Parameters
    ----------
    state : ControlState
        The current control state J_t.
    policy : ControlPolicy
        The control policy π to apply.
    trust_algebra : TrustAlgebraElement
        The current trust algebra element constraining admissible controls.
    goal_state : Optional[ControlState]
        The goal state J*.  If None, the function makes local progress.
    verbose : bool
        If True, print a log line per step.

    Returns
    -------
    (ControlState, str)
        The next control state J_{t+1} and the action label taken.
    """
    # Trust admissibility check
    if trust_algebra.tier.value < policy.trust_tier.value:
        raise PermissionError(
            f"Control step blocked: trust algebra tier {trust_algebra.tier.name} "
            f"is below policy requirement {policy.trust_tier.name}"
        )

    j = state.judgment_tuple
    # Simulate obligation discharge: remove first pending obligation
    new_obligations = j.O[1:] if j.O else ()
    # Simulate trust elevation if an obligation was discharged
    new_tier = j.T
    new_pi = j.Pi
    action_label = "no-op"
    if j.O:
        action_label = f"discharge:{j.O[0]}"
        if j.T.value < TrustTier.PROOF_BACKED.value:
            new_tier = TrustTier(j.T.value + 1)
            new_pi = f"{j.Pi}+step{state.trajectory_segment}"
    elif policy.policy_type == PolicyType.PROOF_GUIDED and not j.Pi:
        # Start a proof if there are no obligations but proof is empty
        action_label = "initiate-proof"
        new_pi = f"proof-{uuid.uuid4().hex[:8]}"

    new_judgment = JudgmentTuple(
        c=j.c,
        phi=j.phi,
        A=j.A,
        E=j.E + (f"ev-{state.trajectory_segment}",),   # New evidence per step
        O=new_obligations,
        B=j.B,
        T=new_tier,
        Pi=new_pi,
    )
    new_coords = _judgment_tuple_to_coords(new_judgment)
    new_trust = trust_algebra.elevate(f"step-{state.trajectory_segment}") if j.O else trust_algebra

    # Compute distance to goal
    new_distance = (
        compute_semantic_distance(
            ControlState(
                state_id="tmp",
                judgment_tuple=new_judgment,
                semantic_coordinates=new_coords,
                distance_to_goal=0.0,
                trust_level=new_trust,
                timestamp=0.0,
                trajectory_segment=0,
            ),
            goal_state,
        )
        if goal_state is not None
        else max(0.0, state.distance_to_goal - 0.05)
    )

    next_state = ControlState(
        state_id=_make_state_id(),
        judgment_tuple=new_judgment,
        semantic_coordinates=new_coords,
        distance_to_goal=new_distance,
        trust_level=new_trust,
        timestamp=time.time(),
        trajectory_segment=state.trajectory_segment + 1,
    )

    if verbose:
        print(
            CONTROL_STEP_LOG_TEMPLATE.format(
                step=next_state.trajectory_segment,
                dist=new_distance,
                trust=new_trust.tier.name,
                action=action_label,
            )
        )

    return next_state, action_label


def verify_control_invariant(
    trajectory: List[ControlState],
    invariant_formula: str,
    verbose: bool = False,
) -> Tuple[bool, List[int]]:
    """Verify that an invariant formula holds along a control trajectory.

    The invariant is expressed as a formula string.  In a full system this
    would be discharged by a theorem prover (Z3, Lean, etc.).  Here we
    implement a *structural* check on the judgment tuples:

    Supported invariant keywords:
      "trust_monotone"    — trust tier is non-decreasing
      "obligation_decreasing" — |O| is non-increasing
      "lyapunov_decreasing"   — distance_to_goal is non-increasing
      "no_trust_regression"   — trust never drops below initial tier

    Parameters
    ----------
    trajectory : List[ControlState]
        The trajectory to check.
    invariant_formula : str
        The invariant formula to verify.
    verbose : bool
        If True, print diagnostic information.

    Returns
    -------
    (bool, List[int])
        (holds, violation_indices) — True if invariant holds everywhere,
        False with a list of step indices where violations occur.
    """
    if not trajectory:
        return True, []

    violations: List[int] = []

    for i in range(1, len(trajectory)):
        prev = trajectory[i - 1]
        curr = trajectory[i]
        violated = False

        if "trust_monotone" in invariant_formula:
            if curr.trust_level.tier.value < prev.trust_level.tier.value:
                violated = True
        if "obligation_decreasing" in invariant_formula:
            if len(curr.judgment_tuple.O) > len(prev.judgment_tuple.O):
                violated = True
        if "lyapunov_decreasing" in invariant_formula:
            if curr.distance_to_goal >= prev.distance_to_goal:
                violated = True
        if "no_trust_regression" in invariant_formula:
            init_tier = trajectory[0].trust_level.tier.value
            if curr.trust_level.tier.value < init_tier:
                violated = True

        if violated:
            violations.append(i)
            if verbose:
                print(
                    f"  [INV VIOLATION] step {i}: invariant={invariant_formula!r} "
                    f"prev_trust={prev.trust_level.tier.name} "
                    f"curr_trust={curr.trust_level.tier.name} "
                    f"prev_dist={prev.distance_to_goal:.4f} "
                    f"curr_dist={curr.distance_to_goal:.4f}"
                )

    holds = len(violations) == 0
    if verbose and holds:
        print(f"  [INV OK] invariant={invariant_formula!r} holds for all {len(trajectory)} states")
    return holds, violations


def compute_lyapunov_decrease(
    state: ControlState,
    next_state: ControlState,
) -> Tuple[float, bool]:
    """Check and quantify the Lyapunov stability condition.

    The Lyapunov stability condition is:
      ΔV = V(J_{t+1}) − V(J_t) < 0

    where V(J) = distance_to_goal.

    This function returns (ΔV, is_stable) where:
      is_stable = True  iff ΔV < 0  (strict decrease)

    A value of ΔV = 0 is not considered stable (we require *strict* decrease
    for liveness — the system must make progress).

    Note: ΔV ≥ 0 does not necessarily indicate a bug; it may indicate:
      - The system is at the goal (ΔV = 0 and V ≈ 0)
      - A constraint is forcing the system to take a detour
      - The trust algebra is blocking an otherwise progress-making action

    Parameters
    ----------
    state : ControlState
        The current state J_t with V(J_t) = state.distance_to_goal.
    next_state : ControlState
        The next state J_{t+1}.

    Returns
    -------
    (float, bool)
        (ΔV, is_stable)
    """
    delta_v = next_state.distance_to_goal - state.distance_to_goal
    is_stable = delta_v < 0.0
    return delta_v, is_stable


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Smoke test: orchestration_is_a_control_problem.py")
    print("=" * 70)

    # 1. Construct a judgment tuple representing the initial state
    init_j = JudgmentTuple(
        c="task:generate-report",
        phi="report_generated ∧ quality_verified",
        A=("agent-alpha", "agent-beta"),
        E=("ev-001",),
        O=("oblig-draft", "oblig-review", "oblig-sign-off"),
        B="partial-knowledge",
        T=TrustTier.PROPOSAL,
        Pi="",
    )
    print(f"Initial judgment: φ={init_j.phi}, |O|={len(init_j.O)}, T={init_j.T.name}")

    # 2. Formulate the control problem
    problem = formulate_control_problem(
        initial_judgment=init_j,
        goal_formula="report_generated ∧ quality_verified",
        constraints=["trust_monotone", "no_trust_regression"],
        horizon=ControlHorizon.RECEDING_HORIZON,
    )
    print(f"Control problem: id={problem.problem_id}")
    print(f"  Initial dist-to-goal: {problem.initial_state.distance_to_goal:.4f}")
    print(f"  Metric: {problem.semantic_metric.name}")
    print(f"  Horizon: {problem.horizon.name}")

    # 3. Design a control policy
    policy = design_control_policy(problem, PolicyType.GREEDY, TrustTier.REVIEWED)
    print(f"\nControl policy: id={policy.policy_id}, type={policy.policy_type.name}")
    print(f"  Trust tier: {policy.trust_tier.name}")
    print(f"  Parameters: {policy.policy_parameters}")

    # 4. Build an orchestration controller
    controller = OrchestrationController(
        controller_id=f"ctrl-{uuid.uuid4().hex[:8]}",
        control_horizon=problem.horizon,
        state_space_dimension=SEMANTIC_EMBEDDING_DIM,
        semantic_goal=problem.goal_state.judgment_tuple.phi,
        control_policy=policy,
        trust_tier=TrustTier.REVIEWED,
        lyapunov_candidate="obligation_count(J) + trust_gap(J)",
    )
    print(f"\nOrchestration controller: id={controller.controller_id}")
    print(f"  Lyapunov candidate: {controller.lyapunov_candidate}")

    # 5. Execute a control trajectory
    state_space = SemanticStateSpace(SEMANTIC_EMBEDDING_DIM, SemanticMetric.COMPOSITE)
    trajectory = ControlTrajectory(problem)
    current = problem.initial_state
    state_space.register_state(current)
    trajectory.append(current)

    trust_elem = TrustAlgebraElement(tier=TrustTier.REVIEWED)

    print("\nExecuting control trajectory (verbose):")
    for step in range(6):
        next_s, action = execute_control_step(
            current, policy, trust_elem, problem.goal_state, verbose=True
        )
        delta_v, stable = compute_lyapunov_decrease(current, next_s)
        trust_elem = next_s.trust_level
        state_space.register_state(next_s)
        trajectory.append(next_s, action)
        current = next_s
        if current.distance_to_goal < DEFAULT_LYAPUNOV_TOLERANCE:
            print("  Goal reached!")
            break

    # 6. Verify invariants
    print("\nVerifying control invariants:")
    for inv in ["trust_monotone", "lyapunov_decreasing", "obligation_decreasing"]:
        holds, viols = verify_control_invariant(trajectory.states, inv, verbose=True)
        print(f"  [{('PASS' if holds else 'FAIL')}] {inv}")

    # 7. Trajectory summary
    print("\nTrajectory summary:")
    for k, v in trajectory.summary().items():
        print(f"  {k}: {v}")

    # 8. Compute pairwise distance (sanity check)
    if len(trajectory.states) >= 2:
        d = compute_semantic_distance(trajectory.states[0], trajectory.states[-1])
        print(f"\nDistance(initial → final): {d:.4f}")

    print("\nSmoke test complete.")
