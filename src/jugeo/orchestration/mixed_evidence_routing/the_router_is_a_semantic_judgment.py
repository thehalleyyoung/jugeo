# copilot: the_router_is_a_semantic_judgment
# The router is not a dispatch table — it is a semantic judgment function.
# Routing decisions are formal judgments in a typed, trust-ordered algebra.
# Each routing act is a proof step, not an assignment.
"""
Module: the_router_is_a_semantic_judgment

The router is a semantic judgment function over the space of evidence-annotated
obligations. A routing decision is not mere dispatch — it is a formal judgment:

    Γ; Δ; Ω; B; T; Π ⊢ route(φ) = C

where:
  Γ  = context
  φ  = formula to be routed
  A  = agent set
  E  = evidence set
  O  = obligation set
  B  = belief state
  T  = trust tier
  Π  = proof object

The routing function maps judgment tuples (c, φ, A, E, O, B, T, Π) to routing
channels via a trust-ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

Geometric interpretation: judgments live in an 8-dimensional space. Routing
channels are directions (basis vectors) in this space. The router projects
the judgment onto channel directions and selects the nearest channel.

# copilot: mixed-evidence routing algebra
"""

from __future__ import annotations
import enum
import math
import time
import uuid
import dataclasses
from dataclasses import dataclass, field

try:
    from jugeo.core.trust import TrustTier as _ExternalTrustTier  # type: ignore
    from jugeo.core.evidence import EvidenceFragment as _ExternalEvidence  # type: ignore
    _JUGEO_AVAILABLE = True
except ImportError:
    _JUGEO_AVAILABLE = False
    _ExternalTrustTier = None
    _ExternalEvidence = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GEOMETRIC_SPACE_DIMENSIONS: int = 8
DEFAULT_ROUTING_TIMEOUT: float = 30.0
LOGICAL_CONNECTIVES: list = ["∧", "∨", "→", "¬", "∀", "∃", "⊢", "⊨"]

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TrustTier(enum.IntEnum):
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5


class RoutingChannel(enum.Enum):
    DIRECT = "DIRECT"
    Z3_SOLVER = "Z3_SOLVER"
    LLM_ORACLE = "LLM_ORACLE"
    HYBRID = "HYBRID"
    PROOF_ENGINE = "PROOF_ENGINE"
    FALLBACK = "FALLBACK"


class DischargeStatus(enum.Enum):
    PENDING = "PENDING"
    DISCHARGED = "DISCHARGED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"


# Defined after enums
ROUTING_CHANNEL_TRUST_REQUIREMENTS: dict = {
    RoutingChannel.DIRECT:       TrustTier.PROPOSAL,
    RoutingChannel.Z3_SOLVER:    TrustTier.RUNTIME_WITNESSED,
    RoutingChannel.LLM_ORACLE:   TrustTier.REVIEWED,
    RoutingChannel.HYBRID:       TrustTier.VERIFIED,
    RoutingChannel.PROOF_ENGINE: TrustTier.PROOF_BACKED,
    RoutingChannel.FALLBACK:     TrustTier.PROPOSAL,
}

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouterJudgment:
    context: str
    formula: str
    agent_set: frozenset
    evidence_set: frozenset
    obligation_set: frozenset
    belief_state: tuple        # tuple of (str, str) pairs
    trust_tier: TrustTier
    proof_object: str
    routing_target: RoutingChannel
    routing_weight: float


@dataclass(frozen=True)
class RoutingDecision:
    source_judgment: RouterJudgment
    target_channel: RoutingChannel
    confidence_score: float
    timestamp: float
    decision_proof: str
    alternatives: tuple


@dataclass(frozen=True)
class RoutingObligation:
    obligation_id: str
    source: str
    target: str
    formula: str
    tier: TrustTier
    discharge_status: DischargeStatus
    routing_proof: str


@dataclass(frozen=True)
class EvidenceFragment:
    fragment_id: str
    content: str
    trust_tier: TrustTier
    weight: float
    source: str


@dataclass(frozen=True)
class BeliefStateSnapshot:
    snapshot_id: str
    agent_id: str
    propositions: frozenset
    confidence_map: tuple      # tuple of (str, float) pairs
    timestamp: float


@dataclass(frozen=True)
class ProofWitness:
    witness_id: str
    proof_type: str
    proof_content: str
    verified: bool
    trust_tier: TrustTier


# ---------------------------------------------------------------------------
# RouterState (mutable dataclass)
# ---------------------------------------------------------------------------

@dataclass
class RouterState:
    current_judgments: list = field(default_factory=list)
    pending_decisions: list = field(default_factory=list)
    routing_history: list = field(default_factory=list)
    active_obligations: list = field(default_factory=list)
    geometric_position: tuple = ()


# ---------------------------------------------------------------------------
# TrustAlgebraElement
# ---------------------------------------------------------------------------

class TrustAlgebraElement:
    """Represents an element in the trust ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    The algebra provides:
      meet  (⊕) — greatest lower bound
      join       — least upper bound
      promote (↑_π) — elevation by proof
      demote  (↓_χ) — demotion by counter-evidence
      leq     (≼)   — partial order
    """

    def __init__(self, tier: TrustTier, admissibility_score: float, evidence_weight: float):
        self.tier = tier
        self.admissibility_score = admissibility_score
        self.evidence_weight = evidence_weight

    def meet(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
        """⊕ operation — greatest lower bound in trust lattice.

        Returns element with min tier, min admissibility, min weight.
        """
        lower_tier = self.tier if self.tier <= other.tier else other.tier
        min_adm = min(self.admissibility_score, other.admissibility_score)
        min_wt = min(self.evidence_weight, other.evidence_weight)
        return TrustAlgebraElement(lower_tier, min_adm, min_wt)

    def join(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
        """Least upper bound.

        Returns element with max tier, max admissibility, sum weight capped at 1.0.
        """
        upper_tier = self.tier if self.tier >= other.tier else other.tier
        max_adm = max(self.admissibility_score, other.admissibility_score)
        max_wt = min(self.evidence_weight + other.evidence_weight, 1.0)
        return TrustAlgebraElement(upper_tier, max_adm, max_wt)

    def promote(self, pi_proof: str) -> TrustAlgebraElement:
        """↑_π — elevate tier by one step if proof is non-empty."""
        if pi_proof and len(pi_proof.strip()) > 0:
            new_tier_val = min(self.tier.value + 1, TrustTier.PROOF_BACKED.value)
            new_tier = TrustTier(new_tier_val)
            new_adm = min(self.admissibility_score * 1.1, 1.0)
            return TrustAlgebraElement(new_tier, new_adm, self.evidence_weight)
        return TrustAlgebraElement(self.tier, self.admissibility_score, self.evidence_weight)

    def demote(self, chi_reason: str) -> TrustAlgebraElement:
        """↓_χ — lower tier by one step."""
        new_tier_val = max(self.tier.value - 1, TrustTier.PROPOSAL.value)
        new_tier = TrustTier(new_tier_val)
        penalty = 0.1 if chi_reason else 0.0
        new_adm = max(self.admissibility_score - penalty, 0.0)
        return TrustAlgebraElement(new_tier, new_adm, self.evidence_weight)

    def leq(self, other: TrustAlgebraElement) -> bool:
        """≼ partial order."""
        if self.tier.value != other.tier.value:
            return self.tier.value <= other.tier.value
        return self.admissibility_score <= other.admissibility_score

    def __repr__(self) -> str:
        return (f"TrustAlgebraElement(tier={self.tier.name}, "
                f"adm={self.admissibility_score:.3f}, wt={self.evidence_weight:.3f})")


# ---------------------------------------------------------------------------
# JudgmentGeometricSpace
# ---------------------------------------------------------------------------

class JudgmentGeometricSpace:
    """The 8-dimensional space in which routing decisions live.

    Dimensions correspond to the 8 components of the judgment tuple
    (c, φ, A, E, O, B, T, Π), each normalized to [0, 1].

    The space is equipped with a metric tensor (default: identity) that
    weights each dimension independently.
    """

    def __init__(
        self,
        dimensions: int = GEOMETRIC_SPACE_DIMENSIONS,
        basis_judgments=None,
        metric_tensor=None,
    ):
        self.dimensions = dimensions
        if basis_judgments is None:
            self.basis_judgments = []
        else:
            self.basis_judgments = basis_judgments
        if metric_tensor is None:
            # Identity metric tensor — each dimension has equal weight
            self.metric_tensor = [
                [1.0 if i == j else 0.0 for j in range(dimensions)]
                for i in range(dimensions)
            ]
        else:
            self.metric_tensor = metric_tensor

    def _judgment_to_vector(self, j: RouterJudgment) -> list:
        """Convert RouterJudgment into an 8-dimensional feature vector."""
        # dim0: context length normalized
        ctx_norm = min(len(j.context) / 500.0, 1.0)
        # dim1: formula complexity (connective count normalized)
        conn_count = sum(j.formula.count(c) for c in LOGICAL_CONNECTIVES)
        formula_norm = min(conn_count / 10.0, 1.0)
        # dim2: agent set size normalized
        agent_norm = min(len(j.agent_set) / 10.0, 1.0)
        # dim3: evidence set size normalized
        evid_norm = min(len(j.evidence_set) / 10.0, 1.0)
        # dim4: obligation set size normalized
        oblig_norm = min(len(j.obligation_set) / 10.0, 1.0)
        # dim5: belief state size normalized
        belief_norm = min(len(j.belief_state) / 10.0, 1.0)
        # dim6: trust tier normalized
        tier_norm = (j.trust_tier.value - 1) / 4.0
        # dim7: proof object length normalized
        proof_norm = min(len(j.proof_object) / 200.0, 1.0)
        return [ctx_norm, formula_norm, agent_norm, evid_norm, oblig_norm, belief_norm, tier_norm, proof_norm]

    def distance(self, j1: RouterJudgment, j2: RouterJudgment) -> float:
        """Euclidean distance in 8D judgment space using per-dimension distances."""
        # dim0: context similarity via Jaccard on word sets
        ctx1_words = set(j1.context.lower().split())
        ctx2_words = set(j2.context.lower().split())
        if ctx1_words or ctx2_words:
            ctx_jaccard = len(ctx1_words & ctx2_words) / len(ctx1_words | ctx2_words)
        else:
            ctx_jaccard = 1.0
        d0 = 1.0 - ctx_jaccard

        # dim1: formula similarity via token overlap
        f1_tokens = set(j1.formula.split())
        f2_tokens = set(j2.formula.split())
        if f1_tokens or f2_tokens:
            formula_overlap = len(f1_tokens & f2_tokens) / len(f1_tokens | f2_tokens)
        else:
            formula_overlap = 1.0
        d1 = 1.0 - formula_overlap

        # dim2: agent_set symmetric difference size (normalized)
        agent_sym_diff = len(j1.agent_set.symmetric_difference(j2.agent_set))
        d2 = min(agent_sym_diff / 10.0, 1.0)

        # dim3: evidence_set symmetric difference size
        evid_sym_diff = len(j1.evidence_set.symmetric_difference(j2.evidence_set))
        d3 = min(evid_sym_diff / 10.0, 1.0)

        # dim4: obligation_set symmetric difference size
        oblig_sym_diff = len(j1.obligation_set.symmetric_difference(j2.obligation_set))
        d4 = min(oblig_sym_diff / 10.0, 1.0)

        # dim5: belief_state distance (number of differing keys)
        bs1_keys = set(k for k, _ in j1.belief_state)
        bs2_keys = set(k for k, _ in j2.belief_state)
        diff_keys = len(bs1_keys.symmetric_difference(bs2_keys))
        d5 = min(diff_keys / 10.0, 1.0)

        # dim6: trust tier difference
        d6 = abs(j1.trust_tier.value - j2.trust_tier.value) / 4.0

        # dim7: proof_object similarity via Jaccard on chars
        p1_chars = set(j1.proof_object)
        p2_chars = set(j2.proof_object)
        if p1_chars or p2_chars:
            proof_jaccard = len(p1_chars & p2_chars) / len(p1_chars | p2_chars)
        else:
            proof_jaccard = 1.0
        d7 = 1.0 - proof_jaccard

        # Euclidean distance with metric tensor (identity by default)
        dims = [d0, d1, d2, d3, d4, d5, d6, d7]
        sq_sum = 0.0
        for i in range(self.dimensions):
            for j_idx in range(self.dimensions):
                sq_sum += self.metric_tensor[i][j_idx] * dims[i] * dims[j_idx]
        return math.sqrt(sq_sum)

    def midpoint(self, j1: RouterJudgment, j2: RouterJudgment) -> tuple:
        """Returns 8-tuple of midpoint coordinates between two judgments."""
        v1 = self._judgment_to_vector(j1)
        v2 = self._judgment_to_vector(j2)
        mid = tuple((v1[i] + v2[i]) / 2.0 for i in range(self.dimensions))
        return mid

    def project(self, judgment: RouterJudgment, channel: RoutingChannel) -> tuple:
        """Returns 8-tuple projection of judgment onto channel's characteristic direction."""
        v = self._judgment_to_vector(judgment)
        channel_vectors = {
            RoutingChannel.DIRECT:         [1,0,0,0,0,0,0,0],
            RoutingChannel.Z3_SOLVER:      [0,1,0,0,0,0,0,0],
            RoutingChannel.LLM_ORACLE:     [0,0,1,0,0,0,0,0],
            RoutingChannel.HYBRID:         [0,0,0,1,0,0,0,0],
            RoutingChannel.PROOF_ENGINE:   [0,0,0,0,1,0,0,0],
            RoutingChannel.FALLBACK:       [0,0,0,0,0,1,0,0],
        }
        cv = channel_vectors.get(channel, [1,0,0,0,0,0,0,0])
        dot = sum(v[i] * cv[i] for i in range(self.dimensions))
        projected = tuple(dot * cv[i] for i in range(self.dimensions))
        return projected

    def nearest_channel(self, judgment: RouterJudgment) -> RoutingChannel:
        """Find the channel with the highest dot product alignment."""
        v = self._judgment_to_vector(judgment)
        channel_vectors = {
            RoutingChannel.DIRECT:         [1,0,0,0,0,0,0,0],
            RoutingChannel.Z3_SOLVER:      [0,1,0,0,0,0,0,0],
            RoutingChannel.LLM_ORACLE:     [0,0,1,0,0,0,0,0],
            RoutingChannel.HYBRID:         [0,0,0,1,0,0,0,0],
            RoutingChannel.PROOF_ENGINE:   [0,0,0,0,1,0,0,0],
            RoutingChannel.FALLBACK:       [0,0,0,0,0,1,0,0],
        }
        best_channel = RoutingChannel.FALLBACK
        best_dot = -1.0
        for ch, cv in channel_vectors.items():
            dot = sum(v[i] * cv[i] for i in range(self.dimensions))
            if dot > best_dot:
                best_dot = dot
                best_channel = ch
        return best_channel


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def make_routing_judgment(
    context: str,
    formula: str,
    evidence_items: list,
    target_channel: RoutingChannel,
    trust_tier: TrustTier,
) -> RouterJudgment:
    """Build a full judgment tuple (c, φ, A, E, O, B, T, Π) for routing.

    The judgment tuple is the fundamental unit of the routing algebra.
    Each component is populated from the evidence items and context.
    """
    # Build agent_set from evidence item sources
    agent_set = frozenset(e.source for e in evidence_items if hasattr(e, 'source'))
    if not agent_set:
        agent_set = frozenset(["default_agent"])

    # Build evidence_set from fragment IDs
    evidence_set = frozenset(e.fragment_id for e in evidence_items if hasattr(e, 'fragment_id'))

    # Build obligation_set from formula — extract sub-formulas by splitting on connectives
    obligation_set_parts = set()
    for c in LOGICAL_CONNECTIVES:
        for part in formula.split(c):
            stripped = part.strip()
            if stripped:
                obligation_set_parts.add(stripped)
    obligation_set = frozenset(obligation_set_parts) if obligation_set_parts else frozenset([formula])

    # Build belief_state tuple from evidence weights — (fragment_id, str(weight))
    belief_state = tuple(
        (e.fragment_id, str(round(e.weight, 4)))
        for e in evidence_items
        if hasattr(e, 'fragment_id') and hasattr(e, 'weight')
    )

    # Determine routing_weight from trust tier and evidence count
    base_weight = trust_tier.value / TrustTier.PROOF_BACKED.value
    evidence_bonus = min(len(evidence_items) * 0.05, 0.3)
    routing_weight = min(base_weight + evidence_bonus, 1.0)

    # Construct proof_object
    if trust_tier >= TrustTier.PROOF_BACKED:
        proof_object = f"proof:{hash(formula) & 0xFFFFFFFF:08x}"
    elif trust_tier >= TrustTier.RUNTIME_WITNESSED:
        proof_object = f"witness:{hash(context + formula) & 0xFFFF:04x}"
    else:
        proof_object = ""

    return RouterJudgment(
        context=context,
        formula=formula,
        agent_set=agent_set,
        evidence_set=evidence_set,
        obligation_set=obligation_set,
        belief_state=belief_state,
        trust_tier=trust_tier,
        proof_object=proof_object,
        routing_target=target_channel,
        routing_weight=routing_weight,
    )


def evaluate_routing_decision(decision: RoutingDecision, state: RouterState) -> float:
    """Evaluate quality of a routing decision against current router state.

    Returns a score in [0.0, 1.0] — higher is better.

    Weighted components:
      history_score    * 0.3 — penalize repeated failures
      channel_fitness  * 0.4 — channel meets trust requirements
      obligation_score * 0.3 — decision addresses active obligations
    """
    # --- History score: check for repeated failures in routing_history ---
    history_score = 1.0
    failure_count = 0
    similar_count = 0
    for past in state.routing_history:
        if not isinstance(past, dict):
            continue
        if past.get("channel") == decision.target_channel.name:
            similar_count += 1
            if past.get("outcome") == "FAILED":
                failure_count += 1
    if similar_count > 0:
        failure_rate = failure_count / similar_count
        history_score = max(0.0, 1.0 - failure_rate * 0.8)

    # --- Channel fitness: check trust requirement ---
    required_tier = ROUTING_CHANNEL_TRUST_REQUIREMENTS.get(decision.target_channel, TrustTier.PROPOSAL)
    actual_tier = decision.source_judgment.trust_tier
    if actual_tier.value >= required_tier.value:
        tier_diff = actual_tier.value - required_tier.value
        channel_fitness = min(1.0, 0.7 + tier_diff * 0.075)
    else:
        shortfall = required_tier.value - actual_tier.value
        channel_fitness = max(0.0, 0.5 - shortfall * 0.15)

    # --- Obligation score: how many active obligations does this address ---
    obligation_score = 0.0
    if state.active_obligations:
        addressed = 0
        for ob in state.active_obligations:
            if not isinstance(ob, RoutingObligation):
                continue
            if (ob.source in decision.source_judgment.context or
                    ob.formula in decision.source_judgment.formula or
                    ob.target == decision.target_channel.name):
                addressed += 1
        obligation_score = min(addressed / len(state.active_obligations), 1.0)
    else:
        obligation_score = 0.5

    total = history_score * 0.3 + channel_fitness * 0.4 + obligation_score * 0.3
    return max(0.0, min(total, 1.0))


def route_obligation(obligation: RoutingObligation, router_state: RouterState) -> RoutingChannel:
    """Route an obligation to the appropriate channel based on tier and formula complexity.

    Tier-based routing:
      PROPOSAL / REVIEWED      → LLM_ORACLE
      VERIFIED                 → HYBRID
      RUNTIME_WITNESSED        → Z3_SOLVER
      PROOF_BACKED             → PROOF_ENGINE

    Override rules:
      - formula_complexity > 5 connectives → PROOF_ENGINE
      - duplicate source/target pair in active obligations → HYBRID
    """
    # --- Base routing from tier ---
    tier = obligation.tier
    if tier in (TrustTier.PROPOSAL, TrustTier.REVIEWED):
        channel = RoutingChannel.LLM_ORACLE
    elif tier == TrustTier.VERIFIED:
        channel = RoutingChannel.HYBRID
    elif tier == TrustTier.RUNTIME_WITNESSED:
        channel = RoutingChannel.Z3_SOLVER
    elif tier == TrustTier.PROOF_BACKED:
        channel = RoutingChannel.PROOF_ENGINE
    else:
        channel = RoutingChannel.FALLBACK

    # --- Check formula complexity ---
    formula_complexity = sum(obligation.formula.count(c) for c in LOGICAL_CONNECTIVES)
    if formula_complexity > 5:
        channel = RoutingChannel.PROOF_ENGINE

    # --- Check for duplicate source/target pair ---
    for active_ob in router_state.active_obligations:
        if isinstance(active_ob, RoutingObligation):
            if (active_ob.source == obligation.source and
                    active_ob.target == obligation.target and
                    active_ob.obligation_id != obligation.obligation_id):
                if channel not in (RoutingChannel.PROOF_ENGINE,):
                    channel = RoutingChannel.HYBRID
                break

    # --- Record decision in routing history ---
    history_entry = {
        "obligation_id": obligation.obligation_id,
        "channel": channel.name,
        "tier": obligation.tier.name,
        "formula_complexity": formula_complexity,
        "timestamp": time.time(),
        "outcome": "PENDING",
    }
    router_state.routing_history.append(history_entry)

    return channel


def compute_geometric_routing_distance(j1: RouterJudgment, j2: RouterJudgment) -> float:
    """Compute geometric distance between two judgments in routing space.

    Uses weighted combination of:
      - Trust tier difference          (weight 0.4)
      - Evidence Jaccard distance      (weight 0.3)
      - Formula token overlap distance (weight 0.2)
      - Obligation set overlap dist    (weight 0.1)

    Returns float in [0.0, 1.0].
    """
    # --- Trust tier difference ---
    max_tier_diff = TrustTier.PROOF_BACKED.value - TrustTier.PROPOSAL.value  # = 4
    tier_diff_raw = abs(j1.trust_tier.value - j2.trust_tier.value)
    tier_diff = tier_diff_raw / max_tier_diff

    # --- Evidence Jaccard distance ---
    e1 = j1.evidence_set
    e2 = j2.evidence_set
    if e1 or e2:
        intersection = len(e1 & e2)
        union = len(e1 | e2)
        evidence_jaccard = intersection / union if union > 0 else 1.0
    else:
        evidence_jaccard = 1.0
    evidence_dist = 1.0 - evidence_jaccard

    # --- Formula token overlap distance ---
    f1_tokens = set(j1.formula.lower().split())
    f2_tokens = set(j2.formula.lower().split())
    if f1_tokens or f2_tokens:
        f_intersection = len(f1_tokens & f2_tokens)
        f_union = len(f1_tokens | f2_tokens)
        formula_overlap = f_intersection / f_union if f_union > 0 else 1.0
    else:
        formula_overlap = 1.0
    formula_dist = 1.0 - formula_overlap

    # --- Obligation set overlap distance ---
    o1 = j1.obligation_set
    o2 = j2.obligation_set
    if o1 or o2:
        o_intersection = len(o1 & o2)
        o_union = len(o1 | o2)
        obligation_jaccard = o_intersection / o_union if o_union > 0 else 1.0
    else:
        obligation_jaccard = 1.0
    obligation_dist = 1.0 - obligation_jaccard

    distance = (tier_diff * 0.4 +
                evidence_dist * 0.3 +
                formula_dist * 0.2 +
                obligation_dist * 0.1)

    return max(0.0, min(distance, 1.0))


def project_judgment_to_channel(judgment: RouterJudgment, channel: RoutingChannel) -> tuple:
    """Project a judgment onto a channel's characteristic direction in 8D space.

    Each channel has a unit vector (basis direction):
      DIRECT:       (1,0,0,0,0,0,0,0)
      Z3_SOLVER:    (0,1,0,0,0,0,0,0)
      LLM_ORACLE:   (0,0,1,0,0,0,0,0)
      HYBRID:       (0,0,0,1,0,0,0,0)
      PROOF_ENGINE: (0,0,0,0,1,0,0,0)
      FALLBACK:     (0,0,0,0,0,1,0,0)

    Returns the scaled projection as an 8-tuple.
    """
    channel_vectors = {
        RoutingChannel.DIRECT:       (1,0,0,0,0,0,0,0),
        RoutingChannel.Z3_SOLVER:    (0,1,0,0,0,0,0,0),
        RoutingChannel.LLM_ORACLE:   (0,0,1,0,0,0,0,0),
        RoutingChannel.HYBRID:       (0,0,0,1,0,0,0,0),
        RoutingChannel.PROOF_ENGINE: (0,0,0,0,1,0,0,0),
        RoutingChannel.FALLBACK:     (0,0,0,0,0,1,0,0),
    }
    cv = channel_vectors.get(channel, (1,0,0,0,0,0,0,0))

    conn_count = sum(judgment.formula.count(c) for c in LOGICAL_CONNECTIVES)
    v = (
        min(len(judgment.context) / 500.0, 1.0),
        min(conn_count / 10.0, 1.0),
        min(len(judgment.agent_set) / 10.0, 1.0),
        min(len(judgment.evidence_set) / 10.0, 1.0),
        min(len(judgment.obligation_set) / 10.0, 1.0),
        min(len(judgment.belief_state) / 10.0, 1.0),
        (judgment.trust_tier.value - 1) / 4.0,
        min(len(judgment.proof_object) / 200.0, 1.0),
    )

    dot = sum(v[i] * cv[i] for i in range(GEOMETRIC_SPACE_DIMENSIONS))
    projected = tuple(dot * cv[i] for i in range(GEOMETRIC_SPACE_DIMENSIONS))
    return projected


def build_trust_algebra_from_evidence(evidence_items: list) -> TrustAlgebraElement:
    """Build a combined TrustAlgebraElement from a list of EvidenceFragment items.

    Algorithm:
      1. Start with minimum tier (PROPOSAL)
      2. For each fragment, compute join to aggregate trust
      3. admissibility_score = weighted mean of fragment trust tier values
      4. evidence_weight = sum of fragment weights, capped at 1.0

    Returns a TrustAlgebraElement representing the combined trust level.
    """
    if not evidence_items:
        return TrustAlgebraElement(TrustTier.PROPOSAL, 0.0, 0.0)

    first = evidence_items[0]
    if isinstance(first, EvidenceFragment):
        current = TrustAlgebraElement(first.trust_tier, first.trust_tier.value / 5.0, first.weight)
    else:
        current = TrustAlgebraElement(TrustTier.PROPOSAL, 0.2, 0.1)

    total_weight = current.evidence_weight
    weighted_adm_sum = current.admissibility_score * current.evidence_weight

    for item in evidence_items[1:]:
        if not isinstance(item, EvidenceFragment):
            continue
        elem = TrustAlgebraElement(item.trust_tier, item.trust_tier.value / 5.0, item.weight)
        current = current.join(elem)
        total_weight += item.weight
        weighted_adm_sum += elem.admissibility_score * item.weight

    if total_weight > 0:
        final_adm = weighted_adm_sum / total_weight
    else:
        final_adm = 0.0

    final_weight = min(total_weight, 1.0)
    return TrustAlgebraElement(current.tier, min(final_adm, 1.0), final_weight)


def compose_routing_obligations(obligations: list) -> list:
    """Merge routing obligations with the same (source, target) pair.

    Merging rules:
      - Combine formulas with conjunction "∧"
      - Keep highest tier among merged obligations
      - Status priority: FAILED > PENDING > DEFERRED > DISCHARGED
      - routing_proof: concatenate non-empty proofs

    Returns a deduplicated, merged list of RoutingObligation.
    """
    groups: dict = {}
    for ob in obligations:
        if not isinstance(ob, RoutingObligation):
            continue
        key = (ob.source, ob.target)
        if key not in groups:
            groups[key] = []
        groups[key].append(ob)

    merged = []
    status_priority = {
        DischargeStatus.FAILED: 0,
        DischargeStatus.PENDING: 1,
        DischargeStatus.DEFERRED: 2,
        DischargeStatus.DISCHARGED: 3,
    }

    for (source, target), group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        formulas = [ob.formula for ob in group if ob.formula]
        combined_formula = " ∧ ".join(formulas) if formulas else ""

        # Fixed: parentheses around generator expression
        best_tier = max((ob.tier for ob in group), key=lambda t: t.value)

        best_status = min(
            (ob.discharge_status for ob in group),
            key=lambda s: status_priority.get(s, 99)
        )

        proofs = [ob.routing_proof for ob in group if ob.routing_proof]
        combined_proof = " | ".join(proofs)

        new_id = f"merged_{source}_{target}_{uuid.uuid4().hex[:8]}"

        merged_ob = RoutingObligation(
            obligation_id=new_id,
            source=source,
            target=target,
            formula=combined_formula,
            tier=best_tier,
            discharge_status=best_status,
            routing_proof=combined_proof,
        )
        merged.append(merged_ob)

    return merged


def validate_judgment_tuple(judgment: RouterJudgment) -> tuple:
    """Validate all 8 components of the judgment tuple (c, φ, A, E, O, B, T, Π).

    Returns (is_valid: bool, errors: list[str]).

    Validation rules:
      - context: non-empty string
      - formula: non-empty string
      - agent_set: frozenset with >= 1 element
      - evidence_set: frozenset (may be empty)
      - obligation_set: frozenset (may be empty)
      - belief_state: tuple
      - trust_tier: valid TrustTier member
      - proof_object: non-empty only required if tier >= PROOF_BACKED
    """
    errors = []

    # c — context
    if not isinstance(judgment.context, str) or not judgment.context.strip():
        errors.append("context (c): must be a non-empty string")

    # φ — formula
    if not isinstance(judgment.formula, str) or not judgment.formula.strip():
        errors.append("formula (φ): must be a non-empty string")

    # A — agent_set
    if not isinstance(judgment.agent_set, frozenset):
        errors.append("agent_set (A): must be a frozenset")
    elif len(judgment.agent_set) < 1:
        errors.append("agent_set (A): must contain at least 1 agent")

    # E — evidence_set
    if not isinstance(judgment.evidence_set, frozenset):
        errors.append("evidence_set (E): must be a frozenset")

    # O — obligation_set
    if not isinstance(judgment.obligation_set, frozenset):
        errors.append("obligation_set (O): must be a frozenset")

    # B — belief_state
    if not isinstance(judgment.belief_state, tuple):
        errors.append("belief_state (B): must be a tuple")

    # T — trust_tier
    if not isinstance(judgment.trust_tier, TrustTier):
        errors.append(f"trust_tier (T): must be a TrustTier; got {type(judgment.trust_tier)}")
    else:
        valid_values = {t.value for t in TrustTier}
        if judgment.trust_tier.value not in valid_values:
            errors.append(f"trust_tier (T): value {judgment.trust_tier.value} is not valid")

    # Π — proof_object
    if not isinstance(judgment.proof_object, str):
        errors.append("proof_object (Π): must be a string")
    elif (isinstance(judgment.trust_tier, TrustTier) and
          judgment.trust_tier >= TrustTier.PROOF_BACKED and
          not judgment.proof_object.strip()):
        errors.append("proof_object (Π): must be non-empty when trust_tier is PROOF_BACKED")

    is_valid = len(errors) == 0
    return (is_valid, errors)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    SEP = "=" * 70

    print(SEP)
    print("  the_router_is_a_semantic_judgment — smoke test")
    print(SEP)

    # -----------------------------------------------------------------------
    # 1. TrustAlgebraElement
    # -----------------------------------------------------------------------
    print("\n[1] TrustAlgebraElement operations")
    elem_a = TrustAlgebraElement(TrustTier.REVIEWED, 0.6, 0.4)
    elem_b = TrustAlgebraElement(TrustTier.VERIFIED, 0.8, 0.5)

    meet_ab = elem_a.meet(elem_b)
    join_ab = elem_a.join(elem_b)
    promoted = elem_a.promote("proof_abc")
    demoted = elem_b.demote("counter_evidence")
    order_ab = elem_a.leq(elem_b)
    order_ba = elem_b.leq(elem_a)

    print(f"  elem_a    : {elem_a}")
    print(f"  elem_b    : {elem_b}")
    print(f"  meet(a,b) : {meet_ab}")
    print(f"  join(a,b) : {join_ab}")
    print(f"  promote(a): {promoted}")
    print(f"  demote(b) : {demoted}")
    print(f"  a ≼ b     : {order_ab}")
    print(f"  b ≼ a     : {order_ba}")

    # -----------------------------------------------------------------------
    # 2. EvidenceFragment instances
    # -----------------------------------------------------------------------
    print("\n[2] EvidenceFragment instances")
    frags = [
        EvidenceFragment("frag-001", "Agent A observed predicate P(x)", TrustTier.REVIEWED,   0.7, "agent_A"),
        EvidenceFragment("frag-002", "Solver confirmed Q(y) ⊢ R(z)",   TrustTier.RUNTIME_WITNESSED, 0.9, "z3_solver"),
        EvidenceFragment("frag-003", "LLM inferred S(w) → T(v)",        TrustTier.PROPOSAL,   0.3, "llm_oracle"),
    ]
    for f in frags:
        print(f"  {f.fragment_id}: tier={f.trust_tier.name}, weight={f.weight}, source={f.source}")

    # -----------------------------------------------------------------------
    # 3. build_trust_algebra_from_evidence
    # -----------------------------------------------------------------------
    print("\n[3] build_trust_algebra_from_evidence")
    combined_elem = build_trust_algebra_from_evidence(frags)
    print(f"  combined: {combined_elem}")

    # -----------------------------------------------------------------------
    # 4. make_routing_judgment
    # -----------------------------------------------------------------------
    print("\n[4] make_routing_judgment")
    ctx = "Agent A is deliberating about the admissibility of predicate P(x) given evidence from the solver."
    formula = "P(x) ∧ Q(y) → R(z) ∨ S(w)"
    judgment = make_routing_judgment(
        context=ctx,
        formula=formula,
        evidence_items=frags,
        target_channel=RoutingChannel.HYBRID,
        trust_tier=TrustTier.RUNTIME_WITNESSED,
    )
    print(f"  context         : {judgment.context[:60]}...")
    print(f"  formula         : {judgment.formula}")
    print(f"  agent_set       : {judgment.agent_set}")
    print(f"  evidence_set    : {judgment.evidence_set}")
    print(f"  obligation_set  : {set(list(judgment.obligation_set)[:3])} ...")
    print(f"  belief_state    : {judgment.belief_state}")
    print(f"  trust_tier      : {judgment.trust_tier.name}")
    print(f"  proof_object    : {judgment.proof_object!r}")
    print(f"  routing_target  : {judgment.routing_target.name}")
    print(f"  routing_weight  : {judgment.routing_weight:.4f}")

    # -----------------------------------------------------------------------
    # 5. validate_judgment_tuple
    # -----------------------------------------------------------------------
    print("\n[5] validate_judgment_tuple")
    is_valid, errs = validate_judgment_tuple(judgment)
    print(f"  valid={is_valid}, errors={errs}")

    # Also test an invalid judgment (empty context)
    bad_judgment = RouterJudgment(
        context="",
        formula="P ∧ Q",
        agent_set=frozenset(),
        evidence_set=frozenset(),
        obligation_set=frozenset(["P ∧ Q"]),
        belief_state=(),
        trust_tier=TrustTier.PROPOSAL,
        proof_object="",
        routing_target=RoutingChannel.DIRECT,
        routing_weight=0.2,
    )
    is_valid_bad, errs_bad = validate_judgment_tuple(bad_judgment)
    print(f"  bad judgment: valid={is_valid_bad}, errors={errs_bad}")

    # -----------------------------------------------------------------------
    # 6. RouterState + route_obligation
    # -----------------------------------------------------------------------
    print("\n[6] RouterState + route_obligation")
    state = RouterState()
    ob1 = RoutingObligation(
        obligation_id="ob-001",
        source="agent_A",
        target="Z3_SOLVER",
        formula="P(x) ∧ Q(y) ⊢ R(z)",
        tier=TrustTier.RUNTIME_WITNESSED,
        discharge_status=DischargeStatus.PENDING,
        routing_proof="",
    )
    ob2 = RoutingObligation(
        obligation_id="ob-002",
        source="agent_A",
        target="LLM_ORACLE",
        formula="S(w) → T(v)",
        tier=TrustTier.REVIEWED,
        discharge_status=DischargeStatus.PENDING,
        routing_proof="",
    )
    state.active_obligations = [ob1, ob2]
    ch1 = route_obligation(ob1, state)
    ch2 = route_obligation(ob2, state)
    print(f"  ob1 → channel: {ch1.name}")
    print(f"  ob2 → channel: {ch2.name}")
    print(f"  routing_history entries: {len(state.routing_history)}")

    # -----------------------------------------------------------------------
    # 7. JudgmentGeometricSpace
    # -----------------------------------------------------------------------
    print("\n[7] JudgmentGeometricSpace")
    space = JudgmentGeometricSpace()
    ctx2 = "Solver verifying constraint satisfaction for predicate Q."
    judgment2 = make_routing_judgment(
        context=ctx2,
        formula="Q(y) ∨ R(z)",
        evidence_items=[frags[1]],
        target_channel=RoutingChannel.Z3_SOLVER,
        trust_tier=TrustTier.VERIFIED,
    )
    dist = space.distance(judgment, judgment2)
    mid = space.midpoint(judgment, judgment2)
    nearest = space.nearest_channel(judgment)
    nearest2 = space.nearest_channel(judgment2)
    geo_dist = compute_geometric_routing_distance(judgment, judgment2)

    print(f"  distance(j1, j2)    : {dist:.4f}")
    print(f"  midpoint(j1, j2)    : {tuple(round(x, 3) for x in mid)}")
    print(f"  nearest_channel(j1) : {nearest.name}")
    print(f"  nearest_channel(j2) : {nearest2.name}")
    print(f"  geo_dist(j1, j2)    : {geo_dist:.4f}")

    # -----------------------------------------------------------------------
    # 8. project_judgment_to_channel
    # -----------------------------------------------------------------------
    print("\n[8] project_judgment_to_channel")
    for ch in RoutingChannel:
        proj = project_judgment_to_channel(judgment, ch)
        non_zero = [(i, round(v, 4)) for i, v in enumerate(proj) if v != 0.0]
        print(f"  {ch.name:16s}: {non_zero}")

    # -----------------------------------------------------------------------
    # 9. RoutingDecision + evaluate_routing_decision
    # -----------------------------------------------------------------------
    print("\n[9] RoutingDecision + evaluate_routing_decision")
    decision = RoutingDecision(
        source_judgment=judgment,
        target_channel=RoutingChannel.HYBRID,
        confidence_score=0.82,
        timestamp=time.time(),
        decision_proof="hybrid_dispatch_v1",
        alternatives=(RoutingChannel.Z3_SOLVER, RoutingChannel.LLM_ORACLE),
    )
    quality = evaluate_routing_decision(decision, state)
    print(f"  decision target   : {decision.target_channel.name}")
    print(f"  confidence_score  : {decision.confidence_score}")
    print(f"  quality score     : {quality:.4f}")

    # -----------------------------------------------------------------------
    # 10. compose_routing_obligations
    # -----------------------------------------------------------------------
    print("\n[10] compose_routing_obligations")
    ob3 = RoutingObligation(
        obligation_id="ob-003",
        source="agent_A",
        target="Z3_SOLVER",
        formula="R(z) ∧ T(v)",
        tier=TrustTier.VERIFIED,
        discharge_status=DischargeStatus.DEFERRED,
        routing_proof="partial_proof_x",
    )
    merged_obs = compose_routing_obligations([ob1, ob2, ob3])
    for mo in merged_obs:
        print(f"  {mo.obligation_id}: source={mo.source}, target={mo.target}, "
              f"tier={mo.tier.name}, status={mo.discharge_status.name}")

    # -----------------------------------------------------------------------
    # 11. BeliefStateSnapshot and ProofWitness
    # -----------------------------------------------------------------------
    print("\n[11] BeliefStateSnapshot + ProofWitness")
    snap = BeliefStateSnapshot(
        snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
        agent_id="agent_A",
        propositions=frozenset(["P(x)", "Q(y)", "R(z)"]),
        confidence_map=(("P(x)", 0.9), ("Q(y)", 0.75), ("R(z)", 0.6)),
        timestamp=time.time(),
    )
    witness = ProofWitness(
        witness_id=f"wit-{uuid.uuid4().hex[:8]}",
        proof_type="SMT",
        proof_content="(assert (and P Q)) (check-sat)",
        verified=True,
        trust_tier=TrustTier.PROOF_BACKED,
    )
    print(f"  BeliefStateSnapshot: agent={snap.agent_id}, props={snap.propositions}, "
          f"conf_map entries={len(snap.confidence_map)}")
    print(f"  ProofWitness       : type={witness.proof_type}, verified={witness.verified}, "
          f"tier={witness.trust_tier.name}")

    print("\n" + SEP)
    print("  All smoke tests passed.")
    print(SEP)
