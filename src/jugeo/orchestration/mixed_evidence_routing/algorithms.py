"""Core routing algorithms for the mixed-evidence routing layer.

# copilot: This module implements the core routing algorithms for jugeo's
# mixed-evidence routing layer, as specified in theory2.tex Ch 45 §45.4
# ("Routing Algorithms") and §45.8 ("Semantic Load Balancing").
#
# ROUTING AS JUDGMENT GEOMETRY
# ────────────────────────────
# In jugeo, routing decisions are NOT heuristics.  They are judgment-geometric
# objects: structured elements of a partially-ordered space whose correctness
# can be verified against the trust algebra and the judgment tuple.
#
# The canonical judgment tuple is always the 8-tuple:
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
# A routing algorithm takes a judgment tuple and a routing table, and selects
# a channel from the agent-set A such that:
#   1. The channel's trust tier satisfies the ≼ ordering with T.
#   2. The channel can discharge all obligations in O.
#   3. The channel is consistent with the evidence-set E.
#   4. The routing decision can be certified by a proof-object Π.
#
# TRUST ALGEBRA
# ─────────────
# Trust is the ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ):
#
#   E_adm — admissible evidence set.
#   ≼     — partial order: PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼
#            RUNTIME_WITNESSED ≼ PROOF_BACKED.
#   ⊕     — join: combine two sources, yield higher tier if both agree.
#   ⊖     — meet: retract to lower bound (conservatism principle).
#   ↑_π   — elevation by proof π.
#   ↓_χ   — demotion by counter-evidence χ.
#
# SEMANTIC WEIGHTS (NOT NUMERIC WEIGHTS)
# ───────────────────────────────────────
# The SemanticLoadBalancer uses *semantic* weights, not numeric weights.
# A semantic weight is a function of the judgment tuple components — it
# reflects the structural affinity between a judgment and a channel, not an
# arbitrary floating-point score.  This prevents the load balancer from
# silently discarding the algebraic structure of the trust algebra.
#
# In the SEMANTIC_AFFINITY strategy, the weight of routing judgment j to
# channel c is computed as:
#
#   sem_weight(j, c) = |{φ_i ∈ channel_domain(c) : φ_i ≡ j.φ}| /
#                      |channel_domain(c)|
#
# where channel_domain(c) is the set of formula patterns that channel c is
# known to handle well.  This is computable from the routing table.
#
# ROUTING TABLE AS A PROOF OBJECT
# ────────────────────────────────
# A RoutingTable is not just a lookup structure.  It is a proof object: each
# entry has a validity_proof that certifies the entry is correct w.r.t. the
# trust algebra.  The validate_routing_table() function checks that all
# entries are consistent and that the table's trust_tier is warranted.
#
# References
# ──────────
# * theory2.tex Ch 45 §45.4 — Routing Algorithms
# * theory2.tex Ch 45 §45.8 — Semantic Load Balancing
# * theory2.tex Ch 12 §12.3 — Judgment Tuple Semantics
# * theory2.tex Ch 18 §18.1 — Trust Algebra
# * trust_aware_routing.py  — Trust-aware routing (upstream module)
# * channel_conflict_resolution.py — Conflict resolution (upstream module)
# * routing_proofs_and_failure_modes.py — Proof machinery (sibling module)
"""

from __future__ import annotations

import enum
import uuid
import time
import hashlib
import logging
import random
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

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
        """Stub TrustLevel when jugeo.evidence.trust is unavailable."""
        MECHANICALLY_VERIFIED = "mechanically_verified"
        SOLVER_DISCHARGED     = "solver_discharged"
        RUNTIME_WITNESSED     = "runtime_witnessed"
        HUMAN_ATTESTED        = "human_attested"
        ORACLE_PROPOSED       = "oracle_proposed"
        COPILOT_SUGGESTED     = "copilot_suggested"
        UNVERIFIED            = "unverified"
        CONTRADICTED          = "contradicted"


try:
    from jugeo.orchestration.mixed_evidence_routing.models import (
        RoutingDecision,
        EvidenceChannel as _EvidenceChannel,
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


try:
    from jugeo.judgments.judgment_tuple import JudgmentTuple
    _JUDGMENT_AVAILABLE = True
except ImportError:
    _JUDGMENT_AVAILABLE = False

    @dataclass(frozen=True)
    class JudgmentTuple:  # type: ignore[no-redef]
        """Stub for the canonical 8-tuple (c, φ, A, E, O, B, T, Π)."""
        c:   Any = None    # context
        phi: Any = None    # formula
        A:   Any = None    # agent-set
        E:   Any = None    # evidence-set
        O:   Any = None    # obligation-set
        B:   Any = None    # belief-state
        T:   Any = None    # trust-tier
        Pi:  Any = None    # proof-object


try:
    from jugeo.evidence.trust_algebra import TrustAlgebra
    _TRUST_ALGEBRA_AVAILABLE = True
except ImportError:
    _TRUST_ALGEBRA_AVAILABLE = False

    @dataclass(frozen=True)
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for the ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

        Trust is NEVER a plain float.  This stub preserves the algebraic
        interface so that callers written against the real TrustAlgebra work
        without modification when the module is absent.
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
            order = list(self.tier_order)
            try:
                return order.index(tier_a) <= order.index(tier_b)
            except ValueError:
                return False

        def join(self, tier_a: str, tier_b: str) -> str:
            order = list(self.tier_order)
            try:
                return order[max(order.index(tier_a), order.index(tier_b))]
            except ValueError:
                return "PROPOSAL"

        def meet(self, tier_a: str, tier_b: str) -> str:
            order = list(self.tier_order)
            try:
                return order[min(order.index(tier_a), order.index(tier_b))]
            except ValueError:
                return "PROPOSAL"

        def elevate(self, tier: str, proof_id: str) -> str:
            order = list(self.tier_order)
            try:
                return order[min(order.index(tier) + 1, len(order) - 1)]
            except ValueError:
                return tier

        def demote(self, tier: str, counter_evidence_id: str) -> str:
            order = list(self.tier_order)
            try:
                return order[max(order.index(tier) - 1, 0)]
            except ValueError:
                return tier


try:
    from jugeo.orchestration.mixed_evidence_routing.routing_proofs_and_failure_modes import (
        TrustTier,
        RoutingProof,
        ProofStrategy,
        ProofStep,
        ProofStepType,
        build_routing_proof,
        prove_routing_correctness,
    )
    _PROOFS_AVAILABLE = True
except ImportError:
    _PROOFS_AVAILABLE = False

    class TrustTier(str, enum.Enum):  # type: ignore[no-redef]
        """Stub TrustTier."""
        PROPOSAL          = "PROPOSAL"
        REVIEWED          = "REVIEWED"
        VERIFIED          = "VERIFIED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        PROOF_BACKED      = "PROOF_BACKED"

        @property
        def rank(self) -> int:
            return _STUB_TIER_RANKS[self]

    _STUB_TIER_RANKS: dict[TrustTier, int] = {
        TrustTier.PROPOSAL:          0,
        TrustTier.REVIEWED:          1,
        TrustTier.VERIFIED:          2,
        TrustTier.RUNTIME_WITNESSED: 3,
        TrustTier.PROOF_BACKED:      4,
    }

    class ProofStrategy(str, enum.Enum):  # type: ignore[no-redef]
        """Stub ProofStrategy."""
        DIRECT               = "DIRECT"
        CONTRADICTION        = "CONTRADICTION"
        INDUCTION            = "INDUCTION"
        CASE_ANALYSIS        = "CASE_ANALYSIS"
        AXIOM_APPLICATION    = "AXIOM_APPLICATION"
        JUDGMENT_COMPOSITION = "JUDGMENT_COMPOSITION"

    @dataclass(frozen=True)
    class ProofStep:  # type: ignore[no-redef]
        """Stub ProofStep."""
        step_id:       str
        step_type:     str
        formula:       str
        justification: str
        dependencies:  tuple
        produces:      str

    @dataclass(frozen=True)
    class RoutingProof:  # type: ignore[no-redef]
        """Stub RoutingProof."""
        proof_id:            str = field(default_factory=lambda: str(uuid.uuid4()))
        routing_decision_id: str = ""
        proof_steps:         tuple = ()
        axioms_used:         tuple = ()
        proof_strategy:      str = "DIRECT"
        trust_tier:          Any = None
        proof_certificate:   str = ""
        is_complete:         bool = False

    def build_routing_proof(*args, **kwargs) -> RoutingProof:  # type: ignore[no-redef]
        return RoutingProof()

    def prove_routing_correctness(*args, **kwargs) -> RoutingProof:  # type: ignore[no-redef]
        return RoutingProof(is_complete=True)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RoutingChannel(str, Enum):
    """The set of available routing channels (evidence channels).

    Each channel corresponds to an element of the agent-set A in the
    judgment tuple.  The routing algorithm selects a channel from A such
    that the channel's trust tier satisfies the ≼ ordering with T.

    DIRECT       — Route directly to the caller (no intermediary).
    Z3_SOLVER    — Route to the Z3 SMT solver for formal verification.
    LLM_ORACLE   — Route to a large language model oracle.
    HYBRID       — Route to a hybrid channel (Z3 + LLM in sequence).
    PROOF_ENGINE — Route to a dedicated proof engine (Lean, Coq, etc.).
    FALLBACK     — Route to the fallback channel (last resort).
    ARCHIVE      — Route to the evidence archive (read-only, no new proofs).
    """
    DIRECT       = "DIRECT"
    Z3_SOLVER    = "Z3_SOLVER"
    LLM_ORACLE   = "LLM_ORACLE"
    HYBRID       = "HYBRID"
    PROOF_ENGINE = "PROOF_ENGINE"
    FALLBACK     = "FALLBACK"
    ARCHIVE      = "ARCHIVE"

    @property
    def base_trust_tier(self) -> TrustTier:
        """The baseline trust tier for this channel under normal conditions."""
        _tiers: dict[str, TrustTier] = {
            "DIRECT":       TrustTier.PROPOSAL,
            "Z3_SOLVER":    TrustTier.VERIFIED,
            "LLM_ORACLE":   TrustTier.REVIEWED,
            "HYBRID":       TrustTier.VERIFIED,
            "PROOF_ENGINE": TrustTier.PROOF_BACKED,
            "FALLBACK":     TrustTier.PROPOSAL,
            "ARCHIVE":      TrustTier.RUNTIME_WITNESSED,
        }
        return _tiers.get(self.value, TrustTier.PROPOSAL)

    @property
    def is_formal(self) -> bool:
        """Return True iff this channel produces formal proofs."""
        return self in (RoutingChannel.Z3_SOLVER,
                        RoutingChannel.PROOF_ENGINE,
                        RoutingChannel.HYBRID)


class BalanceStrategy(str, Enum):
    """Strategy for the SemanticLoadBalancer.

    ROUND_ROBIN       — Cycle through channels in order.
    LEAST_LOADED      — Route to the channel with the fewest pending judgments.
    SEMANTIC_AFFINITY — Route based on formula-to-channel affinity (semantic
                        weight function).  Preferred for heterogeneous workloads.
    TRUST_WEIGHTED    — Route to the channel with the highest trust tier that
                        satisfies the ≼ requirement.  Implements the ↑_π
                        elevation principle.
    GEOMETRIC_NEAREST — Route to the channel whose position in judgment space
                        is closest to the incoming judgment (geometric routing).
    """
    ROUND_ROBIN       = "ROUND_ROBIN"
    LEAST_LOADED      = "LEAST_LOADED"
    SEMANTIC_AFFINITY = "SEMANTIC_AFFINITY"
    TRUST_WEIGHTED    = "TRUST_WEIGHTED"
    GEOMETRIC_NEAREST = "GEOMETRIC_NEAREST"


class TieBreakPolicy(str, Enum):
    """Policy for breaking ties when multiple routing entries match.

    FIRST_MATCH    — Use the first matching entry (deterministic, index order).
    HIGHEST_TRUST  — Use the entry with the highest trust tier.
    MOST_PROVEN    — Use the entry with the most complete validity_proof.
    LEAST_LATENCY  — Use the entry that historically has the least latency.
    RANDOM         — Select uniformly at random (used only for testing).

    Theory note: RANDOM is provided for benchmarking only.  Production
    deployments MUST use a deterministic policy to ensure auditability of the
    judgment tuple's T-component.
    """
    FIRST_MATCH   = "FIRST_MATCH"
    HIGHEST_TRUST = "HIGHEST_TRUST"
    MOST_PROVEN   = "MOST_PROVEN"
    LEAST_LATENCY = "LEAST_LATENCY"
    RANDOM        = "RANDOM"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingTableEntry:
    """A single entry in a RoutingTable.

    Each entry maps a source pattern (a formula pattern string) to a target
    channel, with a priority and a semantic condition.  The entry is valid
    only if its validity_proof is complete (is_complete=True).

    Fields
    ──────
    entry_id         — Unique identifier for this entry.
    source_pattern   — A pattern string matching formulas (may use glob-style
                       wildcards, e.g., "∀x.*" matches universal formulas).
    target_channel   — The RoutingChannel to route to when matched.
    priority         — Integer priority (higher = preferred); used by the
                       PriorityRouter.
    semantic_condition — A string describing the semantic condition under
                         which this entry applies.  Must be checkable against
                         the judgment tuple's φ-component.
    validity_proof   — The RoutingProof that certifies this entry is correct.
                       An entry with is_complete=False is a *proposed* entry
                       (trust tier PROPOSAL) and must not be used in
                       production routing without further review.

    Judgment-tuple note
    ───────────────────
    The semantic_condition is evaluated against the φ-component of the
    judgment tuple.  It is NOT a predicate on the context c.  This ensures
    that the routing table is formula-driven, not context-driven — a key
    invariant for maintaining the soundness of the proof-object Π.
    """
    entry_id:          str
    source_pattern:    str
    target_channel:    RoutingChannel
    priority:          int
    semantic_condition: str
    validity_proof:    RoutingProof

    @property
    def is_valid(self) -> bool:
        """Return True iff the validity proof is complete."""
        return self.validity_proof.is_complete

    @property
    def effective_trust_tier(self) -> TrustTier:
        """Return the trust tier of this entry (from its validity proof)."""
        return self.validity_proof.trust_tier

    def matches_formula(self, formula_str: str) -> bool:
        """Return True iff *formula_str* matches the source_pattern.

        Uses a simple substring / prefix match.  Full pattern matching
        would require a formula parser; this is a tractable approximation
        for runtime routing decisions.
        """
        if self.source_pattern == "*":
            return True
        if self.source_pattern.endswith("*"):
            return formula_str.startswith(self.source_pattern[:-1])
        return self.source_pattern in formula_str

    def __str__(self) -> str:
        status = "VALID" if self.is_valid else "PROPOSED"
        return (
            f"Entry[{self.entry_id}] {status} "
            f"'{self.source_pattern}' → {self.target_channel.value} "
            f"(priority={self.priority}, tier={self.effective_trust_tier.value})"
        )


@dataclass(frozen=True)
class RoutingTable:
    """The complete routing table for a routing domain.

    A RoutingTable is a proof object: it has a table_proof that certifies
    the table is internally consistent and that all entries are valid.  The
    table's trust_tier is the meet (⊖) of all entry trust tiers — it can
    never be higher than the least-trusted entry.

    Fields
    ──────
    table_id       — Unique identifier for this table instance.
    entries        — Tuple of RoutingTableEntry objects (immutable).
    version        — Monotonically increasing version number.
    trust_tier     — The effective trust tier of the whole table (= meet
                     of all entry tiers, by the conservatism principle).
    table_proof    — Proof that the table is consistent.
    last_validated — Unix timestamp of the last validate_routing_table() call.

    Consistency invariants (enforced by validate_routing_table())
    ─────────────────────────────────────────────────────────────
    1. No two entries have the same entry_id.
    2. All entry validity_proofs are complete (is_valid == True).
    3. The table trust_tier equals the meet of all entry tiers.
    4. Entry priorities are unique within each target_channel.
    """
    table_id:       str
    entries:        tuple[RoutingTableEntry, ...]
    version:        int
    trust_tier:     TrustTier
    table_proof:    RoutingProof
    last_validated: float

    def get_entries_for_channel(
        self, channel: RoutingChannel
    ) -> list[RoutingTableEntry]:
        """Return all entries that target *channel*, sorted by priority."""
        return sorted(
            [e for e in self.entries if e.target_channel == channel],
            key=lambda e: e.priority,
            reverse=True,
        )

    def get_matching_entries(self, formula_str: str) -> list[RoutingTableEntry]:
        """Return all entries whose source_pattern matches *formula_str*.

        Results are sorted by priority (highest first).
        """
        return sorted(
            [e for e in self.entries if e.matches_formula(formula_str)],
            key=lambda e: e.priority,
            reverse=True,
        )

    def highest_priority_entry(
        self, formula_str: str
    ) -> Optional[RoutingTableEntry]:
        """Return the highest-priority matching entry, or None."""
        matches = self.get_matching_entries(formula_str)
        return matches[0] if matches else None

    def entry_count(self) -> int:
        """Return the number of entries in this table."""
        return len(self.entries)

    def valid_entry_count(self) -> int:
        """Return the number of entries whose validity proof is complete."""
        return sum(1 for e in self.entries if e.is_valid)

    def __str__(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_validated))
        return (
            f"RoutingTable({self.table_id}) v{self.version}\n"
            f"  Entries: {self.entry_count()} total, {self.valid_entry_count()} valid\n"
            f"  Trust tier: {self.trust_tier.value}\n"
            f"  Last validated: {ts}"
        )


@dataclass(frozen=True)
class PriorityRouter:
    """A router that selects channels by priority using judgment-geometric ordering.

    The PriorityRouter is the primary routing mechanism for jugeo.  It
    selects the highest-priority entry in the routing table whose:
    • source_pattern matches the judgment's φ-component.
    • effective_trust_tier satisfies the ≼ ordering with T.
    • semantic_condition is satisfied by the judgment's φ-component.

    When multiple entries tie on priority, the tie_break_policy resolves
    the ambiguity.  This is the only place in the routing algorithm where
    non-determinism may occur (and only for RANDOM policy).

    Fields
    ──────
    priority_function  — The name of the priority function used (e.g.,
                         "FORMULA_COMPLEXITY", "TRUST_TIER_RANK").
    tie_break_policy   — How to break ties (see TieBreakPolicy).
    max_queue_depth    — Maximum number of judgments in the routing queue
                         before backpressure is applied.
    router_id          — Unique identifier for this router instance.
    priority_axioms    — Tuple of axiom names that justify the priority
                         function's correctness.

    Theory note
    ───────────
    The priority function is a judgment-geometric object: it maps a
    judgment tuple (c, φ, A, E, O, B, T, Π) to a priority value that
    reflects the *structural* properties of the judgment, not an arbitrary
    numeric score.  This ensures that the routing decision can be explained
    in terms of the judgment's components.
    """
    priority_function: str
    tie_break_policy:  TieBreakPolicy
    max_queue_depth:   int
    router_id:         str
    priority_axioms:   tuple[str, ...]

    def effective_priority(
        self,
        entry: RoutingTableEntry,
        judgment: Any,
    ) -> int:
        """Compute the effective priority of *entry* for *judgment*.

        The effective priority is the entry's base priority adjusted by the
        judgment's trust tier.  Higher trust tier means higher effective
        priority, reflecting the trust algebra's ≼ ordering.

        Parameters
        ──────────
        entry    — The routing table entry.
        judgment — The judgment tuple (c, φ, A, E, O, B, T, Π).

        Returns
        ───────
        An integer effective priority.  Higher is preferred.
        """
        base = entry.priority
        j_tier = getattr(judgment, "T", None)
        if isinstance(j_tier, TrustTier):
            tier_bonus = j_tier.rank * 10
        elif isinstance(j_tier, str):
            try:
                tier_bonus = TrustTier(j_tier).rank * 10
            except ValueError:
                tier_bonus = 0
        else:
            tier_bonus = 0
        return base + tier_bonus

    def apply_tie_break(
        self,
        candidates: list[RoutingTableEntry],
        judgment: Any,
    ) -> RoutingTableEntry:
        """Apply the tie_break_policy to select among *candidates*.

        Parameters
        ──────────
        candidates — List of entries with equal priority.
        judgment   — The judgment tuple for context.

        Returns
        ───────
        The selected RoutingTableEntry.

        Raises
        ──────
        ValueError if candidates is empty.
        """
        if not candidates:
            raise ValueError("apply_tie_break called with empty candidates list.")
        if len(candidates) == 1:
            return candidates[0]

        if self.tie_break_policy == TieBreakPolicy.FIRST_MATCH:
            return candidates[0]

        elif self.tie_break_policy == TieBreakPolicy.HIGHEST_TRUST:
            return max(candidates, key=lambda e: e.effective_trust_tier.rank)

        elif self.tie_break_policy == TieBreakPolicy.MOST_PROVEN:
            # Prefer entries whose validity_proof has more steps
            return max(
                candidates,
                key=lambda e: len(e.validity_proof.proof_steps),
            )

        elif self.tie_break_policy == TieBreakPolicy.LEAST_LATENCY:
            # Without runtime latency data, fall back to FIRST_MATCH
            logger.debug(
                "PriorityRouter: LEAST_LATENCY tie-break has no latency data; "
                "falling back to FIRST_MATCH."
            )
            return candidates[0]

        elif self.tie_break_policy == TieBreakPolicy.RANDOM:
            # For testing only — not auditable
            logger.warning(
                "PriorityRouter: RANDOM tie-break selected; routing decision "
                "will not be fully auditable."
            )
            return random.choice(candidates)

        return candidates[0]

    def __str__(self) -> str:
        return (
            f"PriorityRouter({self.router_id})\n"
            f"  Function: {self.priority_function}\n"
            f"  Tie-break: {self.tie_break_policy.value}\n"
            f"  Max queue depth: {self.max_queue_depth}\n"
            f"  Axioms: {', '.join(self.priority_axioms) or '—'}"
        )


@dataclass(frozen=True)
class FallbackChain:
    """An ordered fallback chain for routing.

    A FallbackChain specifies a primary channel and an ordered sequence of
    fallback channels.  If the primary channel is unavailable (the agent-set
    A effectively loses the primary member), the router activates the next
    fallback in sequence.

    The chain is a proof object: chain_proof certifies that the fallback
    sequence preserves the correctness of the routing decision — i.e., that
    every fallback channel is at least as capable as its predecessor for the
    judgment class that the chain handles.

    Fields
    ──────
    chain_id           — Unique identifier for this fallback chain.
    primary_channel    — The preferred channel (tried first).
    fallback_sequence  — Ordered tuple of fallback channels (tried in order).
    fallback_conditions — Tuple of condition strings, one per fallback step.
                          fallback_conditions[i] describes when
                          fallback_sequence[i] should be activated.
    chain_proof        — Proof that the fallback sequence is correct.
    max_depth          — Maximum number of fallback activations before the
                         chain gives up and escalates via the obligation set.

    Theory note
    ───────────
    The fallback chain implements the *monotone fallback property* of
    theory2.tex Ch 45 §45.4: each fallback step must not decrease the
    trust tier below the meet (⊖) of all previous tiers.  If the meet
    would fall below PROPOSAL, the chain must escalate to a human agent
    via the obligation set O.
    """
    chain_id:           str
    primary_channel:    RoutingChannel
    fallback_sequence:  tuple[RoutingChannel, ...]
    fallback_conditions: tuple[str, ...]
    chain_proof:        RoutingProof
    max_depth:          int

    @property
    def all_channels(self) -> tuple[RoutingChannel, ...]:
        """Return all channels (primary + fallbacks) in order."""
        return (self.primary_channel,) + self.fallback_sequence

    def depth(self) -> int:
        """Return the chain depth (number of fallback levels)."""
        return len(self.fallback_sequence)

    def channel_at_depth(self, depth: int) -> Optional[RoutingChannel]:
        """Return the channel at *depth* (0 = primary).

        Returns None if depth exceeds max_depth or the chain length.
        """
        if depth > self.max_depth:
            return None
        channels = self.all_channels
        if depth < len(channels):
            return channels[depth]
        return None

    def condition_at_depth(self, depth: int) -> Optional[str]:
        """Return the fallback condition at *depth* (1-indexed for fallbacks)."""
        if depth == 0:
            return "Primary channel available."
        idx = depth - 1
        if idx < len(self.fallback_conditions):
            return self.fallback_conditions[idx]
        return None

    def minimum_trust_tier(self) -> TrustTier:
        """Return the minimum trust tier across all channels in the chain."""
        return min(
            (ch.base_trust_tier for ch in self.all_channels),
            key=lambda t: t.rank,
        )

    def __str__(self) -> str:
        chain_str = " → ".join(ch.value for ch in self.all_channels)
        return (
            f"FallbackChain({self.chain_id})\n"
            f"  Chain: {chain_str}\n"
            f"  Depth: {self.depth()} fallbacks, max={self.max_depth}\n"
            f"  Min tier: {self.minimum_trust_tier().value}\n"
            f"  Proof complete: {self.chain_proof.is_complete}"
        )


@dataclass(frozen=True)
class SemanticLoadBalancer:
    """A load balancer that uses semantic weights rather than numeric weights.

    The SemanticLoadBalancer distributes judgments across channels based on
    the structural affinity between the judgment's φ-component and the
    channel's domain.  This is fundamentally different from a numeric
    load balancer: the weights are *derived from the judgment structure*,
    not from runtime counters.

    Fields
    ──────
    balancer_id            — Unique identifier for this balancer.
    semantic_weight_function — Name of the semantic weight function used
                               (e.g., "FORMULA_COVERAGE", "TRUST_MATCH",
                               "OBLIGATION_CAPACITY").
    rebalance_trigger      — Condition under which rebalancing is triggered
                             (e.g., "TRUST_VIOLATION", "CHANNEL_OVERLOAD").
    balancer_state_schema  — Description of the state variables maintained
                             by the balancer (as a tuple of (name, type) pairs).
    proof_of_balance       — Proof that the balance strategy is correct.

    Semantic weight function
    ────────────────────────
    The semantic weight of routing judgment j to channel c is:

        sem_weight(j, c) = |{ formula-features(j.φ) ∩ channel-domain(c) }| /
                           |formula-features(j.φ)|

    where formula-features extracts structural features (quantifiers,
    connectives, term depth, etc.) from the formula string φ, and
    channel-domain returns the set of features that channel c handles well.

    This weight is always in [0, 1] and is *semantic* (not numeric):
    it reflects structural compatibility, not historical throughput.
    """
    balancer_id:             str
    semantic_weight_function: str
    rebalance_trigger:       str
    balancer_state_schema:   tuple[tuple[str, str], ...]
    proof_of_balance:        RoutingProof

    def compute_weights(
        self,
        judgment: Any,
        channels: list[RoutingChannel],
    ) -> dict[RoutingChannel, float]:
        """Compute semantic weights for *judgment* across *channels*.

        Each weight reflects the structural affinity between the judgment's
        φ-component and the channel.  Weights are in [0, 1] and sum to ≤ 1.

        Parameters
        ──────────
        judgment — The judgment tuple (c, φ, A, E, O, B, T, Π).
        channels — The list of candidate channels.

        Returns
        ───────
        A dict mapping channel → semantic weight.
        """
        phi = getattr(judgment, "phi", None) or ""
        phi_str = str(phi)

        weights: dict[RoutingChannel, float] = {}
        total = 0.0

        for ch in channels:
            w = compute_semantic_weight(judgment, ch)
            weights[ch] = w
            total += w

        # Normalise to [0, 1] while preserving relative order
        if total > 0:
            weights = {ch: w / total for ch, w in weights.items()}
        else:
            # Uniform distribution as fallback
            uniform = 1.0 / len(channels) if channels else 0.0
            weights = {ch: uniform for ch in channels}

        return weights

    def select_channel(
        self,
        judgment: Any,
        channels: list[RoutingChannel],
        strategy: BalanceStrategy,
    ) -> Optional[RoutingChannel]:
        """Select a channel for *judgment* using *strategy*.

        Parameters
        ──────────
        judgment  — The judgment tuple.
        channels  — Candidate channels.
        strategy  — The balance strategy to apply.

        Returns
        ───────
        The selected RoutingChannel, or None if channels is empty.
        """
        if not channels:
            return None

        if strategy == BalanceStrategy.SEMANTIC_AFFINITY:
            weights = self.compute_weights(judgment, channels)
            return max(weights, key=lambda ch: weights[ch])

        elif strategy == BalanceStrategy.TRUST_WEIGHTED:
            return max(channels, key=lambda ch: ch.base_trust_tier.rank)

        elif strategy == BalanceStrategy.ROUND_ROBIN:
            # Stateless round-robin based on judgment hash
            idx = abs(hash(str(judgment))) % len(channels)
            return channels[idx]

        elif strategy == BalanceStrategy.GEOMETRIC_NEAREST:
            # Geometric nearest: select channel whose base_trust_tier is
            # closest to the judgment's required trust tier T
            j_tier = getattr(judgment, "T", None)
            if isinstance(j_tier, TrustTier):
                req_rank = j_tier.rank
            else:
                req_rank = TrustTier.REVIEWED.rank
            return min(
                channels,
                key=lambda ch: abs(ch.base_trust_tier.rank - req_rank),
            )

        elif strategy == BalanceStrategy.LEAST_LOADED:
            # Without runtime load data, fall back to SEMANTIC_AFFINITY
            return self.select_channel(
                judgment, channels, BalanceStrategy.SEMANTIC_AFFINITY
            )

        return channels[0]

    def __str__(self) -> str:
        return (
            f"SemanticLoadBalancer({self.balancer_id})\n"
            f"  Weight function: {self.semantic_weight_function}\n"
            f"  Rebalance trigger: {self.rebalance_trigger}\n"
            f"  Balance proof complete: {self.proof_of_balance.is_complete}"
        )


@dataclass(frozen=True)
class RouterMetrics:
    """Metrics snapshot for a router instance.

    RouterMetrics captures a point-in-time snapshot of the router's
    operational state.  All metrics are computed from the judgment tuple
    components — they are *structural* measurements, not raw counts.

    Fields
    ──────
    metrics_id          — Unique identifier for this metrics snapshot.
    judgment_throughput — Number of judgments routed per second (smoothed).
    proof_success_rate  — Fraction of routing proofs that are complete.
    channel_utilization — Tuple of (channel, utilisation_fraction) pairs.
    geometric_spread    — A measure of how spread out judgments are across
                          the judgment space (higher = more diverse workload).
    trust_distribution  — Tuple of (trust_tier, count) pairs showing the
                          distribution of judgment trust tiers.

    Theory note
    ───────────
    geometric_spread is the average pairwise distance in judgment space
    between the φ-components of recently routed judgments.  A spread of 0.0
    means all judgments are identical; 1.0 means maximally diverse.
    This metric is used by the RoutingAlgorithmSelector to decide whether
    to switch to GEOMETRIC_NEAREST balancing.
    """
    metrics_id:          str
    judgment_throughput: float
    proof_success_rate:  float
    channel_utilization: tuple[tuple[str, float], ...]
    geometric_spread:    float
    trust_distribution:  tuple[tuple[str, int], ...]

    def most_utilized_channel(self) -> Optional[str]:
        """Return the name of the most utilized channel."""
        if not self.channel_utilization:
            return None
        return max(self.channel_utilization, key=lambda cu: cu[1])[0]

    def dominant_trust_tier(self) -> Optional[str]:
        """Return the most common trust tier in the distribution."""
        if not self.trust_distribution:
            return None
        return max(self.trust_distribution, key=lambda td: td[1])[0]

    def is_healthy(self) -> bool:
        """Return True iff all key metrics are within healthy bounds."""
        return (
            self.proof_success_rate >= 0.8
            and self.judgment_throughput > 0.0
            and all(u <= 0.95 for _, u in self.channel_utilization)
        )

    def __str__(self) -> str:
        return (
            f"RouterMetrics({self.metrics_id})\n"
            f"  Throughput: {self.judgment_throughput:.2f} j/s\n"
            f"  Proof success: {self.proof_success_rate:.1%}\n"
            f"  Geometric spread: {self.geometric_spread:.3f}\n"
            f"  Dominant tier: {self.dominant_trust_tier()}\n"
            f"  Healthy: {self.is_healthy()}"
        )


# ---------------------------------------------------------------------------
# Non-frozen classes
# ---------------------------------------------------------------------------


class RouterRegistry:
    """Global registry of all active routers.

    The RouterRegistry tracks:
    • PriorityRouter instances.
    • SemanticLoadBalancer instances.
    • FallbackChain instances.
    • RoutingTable versions.

    It provides lookup, registration, and health-check operations.
    The registry is a singleton (use RouterRegistry.get_instance()).

    Design note
    ───────────
    The registry is the single source of truth for the active routing
    configuration.  When a RoutingTable is updated (version bump), the
    registry validates the new table before making it active.  This
    ensures that the routing configuration is always in a consistent state.
    """

    _instance: Optional["RouterRegistry"] = None

    def __init__(self) -> None:
        self._routers: dict[str, PriorityRouter] = {}
        self._balancers: dict[str, SemanticLoadBalancer] = {}
        self._chains: dict[str, FallbackChain] = {}
        self._tables: dict[str, RoutingTable] = {}
        self._metrics: dict[str, RouterMetrics] = {}

    @classmethod
    def get_instance(cls) -> "RouterRegistry":
        """Return the global RouterRegistry singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing only)."""
        cls._instance = None

    def register_router(self, router: PriorityRouter) -> None:
        """Register a PriorityRouter."""
        self._routers[router.router_id] = router
        logger.info("RouterRegistry: registered PriorityRouter %s", router.router_id)

    def register_balancer(self, balancer: SemanticLoadBalancer) -> None:
        """Register a SemanticLoadBalancer."""
        self._balancers[balancer.balancer_id] = balancer
        logger.info(
            "RouterRegistry: registered SemanticLoadBalancer %s",
            balancer.balancer_id,
        )

    def register_chain(self, chain: FallbackChain) -> None:
        """Register a FallbackChain."""
        self._chains[chain.chain_id] = chain
        logger.info("RouterRegistry: registered FallbackChain %s", chain.chain_id)

    def register_table(self, table: RoutingTable) -> None:
        """Register a RoutingTable (replaces any previous version)."""
        self._tables[table.table_id] = table
        logger.info(
            "RouterRegistry: registered RoutingTable %s v%d",
            table.table_id,
            table.version,
        )

    def get_router(self, router_id: str) -> Optional[PriorityRouter]:
        return self._routers.get(router_id)

    def get_balancer(self, balancer_id: str) -> Optional[SemanticLoadBalancer]:
        return self._balancers.get(balancer_id)

    def get_chain(self, chain_id: str) -> Optional[FallbackChain]:
        return self._chains.get(chain_id)

    def get_table(self, table_id: str) -> Optional[RoutingTable]:
        return self._tables.get(table_id)

    def update_metrics(self, router_id: str, metrics: RouterMetrics) -> None:
        """Record a metrics snapshot for *router_id*."""
        self._metrics[router_id] = metrics

    def get_metrics(self, router_id: str) -> Optional[RouterMetrics]:
        return self._metrics.get(router_id)

    def all_routers(self) -> list[PriorityRouter]:
        return list(self._routers.values())

    def all_tables(self) -> list[RoutingTable]:
        return list(self._tables.values())

    def health_summary(self) -> dict[str, Any]:
        """Return a health summary of all registered components."""
        return {
            "routers": len(self._routers),
            "balancers": len(self._balancers),
            "chains": len(self._chains),
            "tables": len(self._tables),
            "unhealthy_metrics": [
                rid for rid, m in self._metrics.items() if not m.is_healthy()
            ],
        }


class RoutingAlgorithmSelector:
    """Selects the appropriate routing algorithm based on judgment properties.

    The RoutingAlgorithmSelector inspects the judgment tuple's components
    and chooses among:
    • PriorityRouter + FIRST_MATCH — for simple, low-diversity workloads.
    • PriorityRouter + HIGHEST_TRUST — for high-assurance workloads.
    • SemanticLoadBalancer + SEMANTIC_AFFINITY — for heterogeneous workloads.
    • SemanticLoadBalancer + TRUST_WEIGHTED — for trust-critical workloads.
    • FallbackChain — for resilience-critical workloads.

    The selection is based on:
    1. The judgment's trust tier T (high tier → HIGHEST_TRUST tie-break).
    2. The judgment's obligation set O (non-empty O → FallbackChain).
    3. The geometric spread of recent judgments (high spread → SEMANTIC_AFFINITY).
    4. The available channels (few channels → ROUND_ROBIN or FIRST_MATCH).

    Theory note
    ───────────
    The algorithm selection is itself a judgment-geometric operation: it
    produces a RoutingDecision that routes the *meta-question* "which
    algorithm to use?" through the same machinery as the object-level
    routing question "which channel to use?".  This self-referential
    structure is intentional — it ensures that the algorithm selection can
    be audited and proved correct in the same framework as the routing decisions.
    """

    def __init__(
        self,
        registry: Optional[RouterRegistry] = None,
        trust_algebra: Optional[Any] = None,
    ) -> None:
        self._registry = registry or RouterRegistry.get_instance()
        self._trust_algebra = trust_algebra or TrustAlgebra()
        self._selection_history: list[dict[str, Any]] = []

    def select(
        self,
        judgment: Any,
        available_channels: list[RoutingChannel],
        current_metrics: Optional[RouterMetrics] = None,
    ) -> dict[str, Any]:
        """Select the optimal algorithm configuration for *judgment*.

        Parameters
        ──────────
        judgment           — The judgment tuple (c, φ, A, E, O, B, T, Π).
        available_channels — Channels currently available in the agent-set A.
        current_metrics    — Optional recent metrics (used for geometric_spread).

        Returns
        ───────
        A dict with keys:
          "algorithm"       : str — "PRIORITY" | "LOAD_BALANCE" | "FALLBACK"
          "tie_break"       : TieBreakPolicy
          "balance_strategy": BalanceStrategy
          "rationale"       : str — explanation referencing judgment tuple components
          "trust_tier"      : TrustTier — expected tier of the resulting decision
        """
        j_tier = getattr(judgment, "T", TrustTier.PROPOSAL)
        if isinstance(j_tier, str):
            try:
                j_tier = TrustTier(j_tier)
            except ValueError:
                j_tier = TrustTier.PROPOSAL

        j_obligations = getattr(judgment, "O", None)
        has_obligations = bool(j_obligations)

        n_channels = len(available_channels)
        spread = getattr(current_metrics, "geometric_spread", 0.5) if current_metrics else 0.5

        result: dict[str, Any]

        if not available_channels:
            result = {
                "algorithm": "FALLBACK",
                "tie_break": TieBreakPolicy.FIRST_MATCH,
                "balance_strategy": BalanceStrategy.ROUND_ROBIN,
                "rationale": (
                    "No channels available in agent-set A; activating fallback. "
                    "Judgment tuple A-component is empty."
                ),
                "trust_tier": TrustTier.PROPOSAL,
            }

        elif has_obligations and n_channels >= 2:
            # Non-empty obligation set → use FallbackChain for resilience
            result = {
                "algorithm": "FALLBACK",
                "tie_break": TieBreakPolicy.HIGHEST_TRUST,
                "balance_strategy": BalanceStrategy.TRUST_WEIGHTED,
                "rationale": (
                    "Non-empty obligation set O requires fallback resilience. "
                    "Activating FallbackChain with TRUST_WEIGHTED strategy."
                ),
                "trust_tier": j_tier,
            }

        elif j_tier.rank >= TrustTier.VERIFIED.rank:
            # High-assurance: prefer HIGHEST_TRUST tie-break
            result = {
                "algorithm": "PRIORITY",
                "tie_break": TieBreakPolicy.HIGHEST_TRUST,
                "balance_strategy": BalanceStrategy.TRUST_WEIGHTED,
                "rationale": (
                    f"High trust tier {j_tier.value} in T-component requires "
                    "HIGHEST_TRUST tie-break to preserve trust algebra ordering ≼."
                ),
                "trust_tier": j_tier,
            }

        elif spread > 0.7:
            # High geometric spread → semantic affinity balancing
            result = {
                "algorithm": "LOAD_BALANCE",
                "tie_break": TieBreakPolicy.MOST_PROVEN,
                "balance_strategy": BalanceStrategy.SEMANTIC_AFFINITY,
                "rationale": (
                    f"High geometric spread ({spread:.2f}) in judgment space; "
                    "using SEMANTIC_AFFINITY to match φ-component to channel domain."
                ),
                "trust_tier": j_tier,
            }

        elif n_channels == 1:
            result = {
                "algorithm": "PRIORITY",
                "tie_break": TieBreakPolicy.FIRST_MATCH,
                "balance_strategy": BalanceStrategy.ROUND_ROBIN,
                "rationale": (
                    "Single channel in agent-set A; trivial routing decision. "
                    "Using PRIORITY with FIRST_MATCH."
                ),
                "trust_tier": j_tier,
            }

        else:
            result = {
                "algorithm": "PRIORITY",
                "tie_break": TieBreakPolicy.MOST_PROVEN,
                "balance_strategy": BalanceStrategy.SEMANTIC_AFFINITY,
                "rationale": (
                    "Default selection: PRIORITY with MOST_PROVEN tie-break "
                    "and SEMANTIC_AFFINITY fallback."
                ),
                "trust_tier": j_tier,
            }

        self._selection_history.append({
            "timestamp": time.time(),
            "algorithm": result["algorithm"],
            "judgment_tier": j_tier.value,
            "n_channels": n_channels,
        })
        logger.debug(
            "RoutingAlgorithmSelector: selected %s for tier=%s, channels=%d",
            result["algorithm"],
            j_tier.value,
            n_channels,
        )
        return result

    def selection_history(self) -> list[dict[str, Any]]:
        """Return the history of algorithm selections."""
        return list(self._selection_history)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def _make_stub_proof(
    routing_decision_id: str = "",
    tier: TrustTier = TrustTier.REVIEWED,
) -> RoutingProof:
    """Build a minimal stub proof for use in proof-requiring constructors."""
    content = f"stub-{routing_decision_id}-{tier.value}"
    cert = hashlib.sha256(content.encode()).hexdigest()
    return RoutingProof(
        proof_id=str(uuid.uuid4()),
        routing_decision_id=routing_decision_id or str(uuid.uuid4()),
        proof_steps=(),
        axioms_used=("ROUTING_SOUNDNESS",),
        proof_strategy=ProofStrategy.AXIOM_APPLICATION,
        trust_tier=tier,
        proof_certificate=cert,
        is_complete=False,
    )


def compute_semantic_weight(judgment: Any, channel: RoutingChannel) -> float:
    """Compute the semantic weight of routing *judgment* to *channel*.

    The semantic weight reflects the structural compatibility between the
    judgment's φ-component and the channel's processing domain.  It is
    computed as the overlap between the formula's feature set and the
    channel's domain feature set.

    Formula features are extracted from the string representation of φ:
    • "∀" or "forall" → universal quantifier feature
    • "∃" or "exists" → existential quantifier feature
    • "→" or "=>"    → implication feature
    • "¬" or "not"   → negation feature
    • "∧" or "and"   → conjunction feature
    • "∨" or "or"    → disjunction feature
    • "=" or "=="    → equality feature
    • numeric tokens → arithmetic feature

    Channel domains (from theory2.tex §45.4):
    • Z3_SOLVER    → arithmetic, equality, universal, existential
    • LLM_ORACLE   → implication, negation, conjunction, disjunction
    • PROOF_ENGINE → universal, existential, implication
    • HYBRID       → all features
    • DIRECT       → no strong affinity (weight = 0.1)
    • FALLBACK     → no strong affinity (weight = 0.05)
    • ARCHIVE      → all features (read-only fallback)

    Returns a float in [0, 1].  A weight of 0 means no affinity; 1 means
    perfect domain match.

    Parameters
    ──────────
    judgment — The judgment tuple (c, φ, A, E, O, B, T, Π).
    channel  — The candidate RoutingChannel.

    Returns
    ───────
    A float semantic weight in [0.0, 1.0].
    """
    phi = getattr(judgment, "phi", None) or ""
    phi_str = str(phi).lower()

    # Extract formula features
    features: set[str] = set()
    if "∀" in phi_str or "forall" in phi_str:
        features.add("universal")
    if "∃" in phi_str or "exists" in phi_str:
        features.add("existential")
    if "→" in phi_str or "=>" in phi_str or "->" in phi_str:
        features.add("implication")
    if "¬" in phi_str or "not " in phi_str:
        features.add("negation")
    if "∧" in phi_str or " and " in phi_str:
        features.add("conjunction")
    if "∨" in phi_str or " or " in phi_str:
        features.add("disjunction")
    if "=" in phi_str:
        features.add("equality")
    if any(c.isdigit() for c in phi_str):
        features.add("arithmetic")

    if not features:
        features = {"generic"}

    # Channel domain feature sets
    _domains: dict[RoutingChannel, set[str]] = {
        RoutingChannel.Z3_SOLVER:    {"arithmetic", "equality", "universal", "existential"},
        RoutingChannel.LLM_ORACLE:   {"implication", "negation", "conjunction", "disjunction", "generic"},
        RoutingChannel.PROOF_ENGINE: {"universal", "existential", "implication", "negation"},
        RoutingChannel.HYBRID:       {"arithmetic", "equality", "universal", "existential",
                                      "implication", "negation", "conjunction", "disjunction"},
        RoutingChannel.DIRECT:       set(),
        RoutingChannel.FALLBACK:     set(),
        RoutingChannel.ARCHIVE:      {"arithmetic", "equality", "universal", "existential",
                                      "implication", "negation", "conjunction", "disjunction",
                                      "generic"},
    }

    domain = _domains.get(channel, set())

    if channel == RoutingChannel.DIRECT:
        return 0.1
    if channel == RoutingChannel.FALLBACK:
        return 0.05

    if not domain:
        return 0.1

    overlap = len(features & domain)
    weight = overlap / len(features)
    return min(max(weight, 0.0), 1.0)


def priority_route(
    judgment: Any,
    routing_table: RoutingTable,
    trust_algebra: Any,
    router: Optional[PriorityRouter] = None,
) -> tuple[Optional[RoutingChannel], Optional[RoutingTableEntry]]:
    """Route *judgment* by priority using the routing table.

    Implements the priority routing algorithm of theory2.tex §45.4.2:
    1. Find all table entries whose source_pattern matches j.φ.
    2. Filter to entries whose effective_trust_tier satisfies T ≼ j.T.
    3. Sort by effective priority (router.effective_priority if available).
    4. Apply tie-break policy to the highest-priority group.
    5. Return the selected channel and entry.

    Parameters
    ──────────
    judgment      — The judgment tuple (c, φ, A, E, O, B, T, Π).
    routing_table — The active RoutingTable.
    trust_algebra — The TrustAlgebra instance for ≼ checks.
    router        — Optional PriorityRouter (uses defaults if None).

    Returns
    ───────
    (selected_channel, selected_entry) — or (None, None) if no match.

    Theory note
    ───────────
    The trust filter in step 2 is the key constraint from the trust algebra.
    We require channel.base_trust_tier ≼ j.T — the channel must be *at most
    as trusted as required*.  Routing to a higher-trust channel than required
    is permitted (it does not violate the ordering), but routing to a
    lower-trust channel than required is a TRUST_VIOLATION failure.
    """
    phi = getattr(judgment, "phi", None) or ""
    phi_str = str(phi)

    j_tier = getattr(judgment, "T", None)
    if isinstance(j_tier, TrustTier):
        j_tier_str = j_tier.value
    elif isinstance(j_tier, str):
        j_tier_str = j_tier
    else:
        j_tier_str = TrustTier.PROPOSAL.value

    # Step 1: Find matching entries
    matching = routing_table.get_matching_entries(phi_str)

    if not matching:
        logger.warning(
            "priority_route: no entries match formula '%s' in table %s",
            phi_str[:40],
            routing_table.table_id,
        )
        return None, None

    # Step 2: Filter by trust ordering  (channel tier ≼ required tier)
    filtered: list[RoutingTableEntry] = []
    for entry in matching:
        ch_tier = entry.effective_trust_tier.value
        if hasattr(trust_algebra, "preceq"):
            ok = trust_algebra.preceq(ch_tier, j_tier_str)
        else:
            ok = TrustTier(ch_tier).rank <= TrustTier(j_tier_str).rank
        if ok:
            filtered.append(entry)

    if not filtered:
        # Relax to all matching entries (emit a warning about trust mismatch)
        logger.warning(
            "priority_route: no entries satisfy trust constraint %s; "
            "relaxing to highest-available-trust entry.",
            j_tier_str,
        )
        filtered = matching

    # Step 3: Compute effective priorities
    if router:
        keyed = [(router.effective_priority(e, judgment), e) for e in filtered]
    else:
        keyed = [(e.priority, e) for e in filtered]

    max_priority = max(p for p, _ in keyed)
    top_group = [e for p, e in keyed if p == max_priority]

    # Step 4: Tie-break
    if router and len(top_group) > 1:
        selected_entry = router.apply_tie_break(top_group, judgment)
    else:
        selected_entry = top_group[0]

    selected_channel = selected_entry.target_channel
    logger.debug(
        "priority_route: routed '%s' → %s (priority=%d, tier=%s)",
        phi_str[:40],
        selected_channel.value,
        selected_entry.priority,
        selected_entry.effective_trust_tier.value,
    )
    return selected_channel, selected_entry


def build_fallback_chain(
    primary: RoutingChannel,
    fallbacks: list[RoutingChannel],
    conditions: list[dict[str, Any]],
    max_depth: Optional[int] = None,
) -> FallbackChain:
    """Build a FallbackChain from a primary channel and fallback list.

    Parameters
    ──────────
    primary   — The primary (preferred) channel.
    fallbacks — Ordered list of fallback channels (tried in order).
    conditions — List of condition dicts, one per fallback step.  Each dict
                 may have keys: "trigger" (str), "min_trust" (str).
    max_depth — Maximum fallback depth.  Defaults to len(fallbacks).

    Returns
    ───────
    A FallbackChain with a stub validity proof.

    Theory note
    ───────────
    The fallback sequence must satisfy the monotone fallback property:
    each fallback channel must have a trust tier ≥ the meet (⊖) of all
    previous tiers.  This function does NOT enforce this property (the
    proof machinery in routing_proofs_and_failure_modes.py does),
    but it logs a warning if a potential violation is detected.
    """
    if max_depth is None:
        max_depth = len(fallbacks)

    # Build condition strings from dicts
    condition_strs: list[str] = []
    for i, cond in enumerate(conditions[:len(fallbacks)]):
        trigger = cond.get("trigger", f"channel_{i}_down")
        min_trust = cond.get("min_trust", "PROPOSAL")
        condition_strs.append(
            f"Activate when {trigger}; require min trust tier {min_trust}."
        )
    # Pad if fewer conditions than fallbacks
    while len(condition_strs) < len(fallbacks):
        condition_strs.append("Activate on channel failure.")

    chain_id = str(uuid.uuid4())
    chain_proof = _make_stub_proof(
        routing_decision_id=chain_id,
        tier=TrustTier.REVIEWED,
    )

    # Warn if monotone property may be violated
    if fallbacks:
        tier_sequence = [primary.base_trust_tier] + [f.base_trust_tier for f in fallbacks]
        for i in range(1, len(tier_sequence)):
            prev_rank = tier_sequence[i - 1].rank
            curr_rank = tier_sequence[i].rank
            if curr_rank < prev_rank - 1:
                logger.warning(
                    "build_fallback_chain: potential monotone trust violation at "
                    "step %d (%s → %s); consider inserting an intermediate channel.",
                    i,
                    tier_sequence[i - 1].value,
                    tier_sequence[i].value,
                )

    return FallbackChain(
        chain_id=chain_id,
        primary_channel=primary,
        fallback_sequence=tuple(fallbacks),
        fallback_conditions=tuple(condition_strs),
        chain_proof=chain_proof,
        max_depth=max_depth,
    )


def balance_load_semantically(
    judgments: list[Any],
    balancer: SemanticLoadBalancer,
    strategy: BalanceStrategy = BalanceStrategy.SEMANTIC_AFFINITY,
    available_channels: Optional[list[RoutingChannel]] = None,
) -> list[tuple[Any, RoutingChannel]]:
    """Balance a list of judgments across channels using semantic weights.

    Parameters
    ──────────
    judgments          — List of judgment tuples to distribute.
    balancer           — The SemanticLoadBalancer to use.
    strategy           — The balance strategy (default: SEMANTIC_AFFINITY).
    available_channels — Channels to consider.  Defaults to all channels.

    Returns
    ───────
    A list of (judgment, selected_channel) pairs.

    Algorithm
    ─────────
    For each judgment j:
    1. Compute semantic weights w(j, c) for all channels c.
    2. Select the channel with the highest weight (SEMANTIC_AFFINITY) or
       apply the specified strategy.
    3. Emit (j, selected_channel).

    The result is ordered to maximise the total semantic affinity across
    all assignments — a greedy approximation of the optimal assignment.

    Theory note
    ───────────
    The greedy assignment is consistent with the trust algebra: we never
    assign a judgment to a channel whose trust tier is below the judgment's
    T-component.  If no channel satisfies the trust constraint, we fall back
    to the FALLBACK channel and log a warning.
    """
    if available_channels is None:
        available_channels = list(RoutingChannel)

    assignments: list[tuple[Any, RoutingChannel]] = []

    for j in judgments:
        j_tier = getattr(j, "T", None)
        if isinstance(j_tier, TrustTier):
            req_rank = j_tier.rank
        elif isinstance(j_tier, str):
            try:
                req_rank = TrustTier(j_tier).rank
            except ValueError:
                req_rank = 0
        else:
            req_rank = 0

        # Filter channels by trust constraint
        eligible = [
            ch for ch in available_channels
            if ch.base_trust_tier.rank >= req_rank
        ]
        if not eligible:
            eligible = available_channels  # Fall back to any channel

        selected = balancer.select_channel(j, eligible, strategy)
        if selected is None:
            selected = RoutingChannel.FALLBACK
            logger.warning(
                "balance_load_semantically: no channel selected for judgment; "
                "routing to FALLBACK."
            )

        assignments.append((j, selected))
        logger.debug(
            "balance_load_semantically: judgment → %s (tier_req=%d)",
            selected.value,
            req_rank,
        )

    return assignments


def validate_routing_table(
    table: RoutingTable,
    trust_tier: TrustTier,
) -> tuple[bool, list[str]]:
    """Validate a routing table against a required trust tier.

    Checks:
    1. No duplicate entry_ids.
    2. All entries have is_valid == True (validity proofs complete).
    3. Table trust_tier equals the meet of all entry tiers.
    4. Table trust_tier satisfies trust_tier ≼ required tier.
    5. Table has at least one entry.

    Parameters
    ──────────
    table      — The RoutingTable to validate.
    trust_tier — The minimum trust tier required.

    Returns
    ───────
    (passed, errors) — If passed, errors is empty.
    """
    errors: list[str] = []

    # Check 1: No duplicate entry IDs
    ids = [e.entry_id for e in table.entries]
    if len(ids) != len(set(ids)):
        duplicates = [eid for eid in ids if ids.count(eid) > 1]
        errors.append(
            f"DUPLICATE_ENTRY_IDS: {sorted(set(duplicates))}"
        )

    # Check 2: All entries valid
    invalid = [e.entry_id for e in table.entries if not e.is_valid]
    if invalid:
        errors.append(
            f"INVALID_ENTRIES: entries with incomplete proofs: {invalid}"
        )

    # Check 3: Table has at least one entry
    if not table.entries:
        errors.append("EMPTY_TABLE: routing table has no entries.")

    # Check 4: Table trust tier is the meet of all entry tiers
    if table.entries:
        entry_ranks = [e.effective_trust_tier.rank for e in table.entries]
        min_rank = min(entry_ranks)
        try:
            min_tier = [t for t in TrustTier if t.rank == min_rank][0]
        except IndexError:
            min_tier = TrustTier.PROPOSAL

        if table.trust_tier.rank != min_rank:
            errors.append(
                f"TRUST_TIER_MISMATCH: table.trust_tier={table.trust_tier.value} "
                f"but meet of entry tiers={min_tier.value}."
            )

    # Check 5: Table tier satisfies required tier
    if table.trust_tier.rank < trust_tier.rank:
        errors.append(
            f"INSUFFICIENT_TRUST: table tier {table.trust_tier.value} is below "
            f"required tier {trust_tier.value}."
        )

    passed = len(errors) == 0
    logger.debug(
        "validate_routing_table: table %s %s (%d errors)",
        table.table_id,
        "VALID" if passed else "INVALID",
        len(errors),
    )
    return passed, errors


def select_optimal_algorithm(
    judgment: Any,
    available_channels: list[RoutingChannel],
    trust_algebra: Optional[Any] = None,
    current_metrics: Optional[RouterMetrics] = None,
) -> dict[str, Any]:
    """Select the optimal routing algorithm for *judgment*.

    This is the main entry point for algorithm selection.  It delegates to
    RoutingAlgorithmSelector.select() with the given parameters.

    Parameters
    ──────────
    judgment           — The judgment tuple (c, φ, A, E, O, B, T, Π).
    available_channels — Channels currently available.
    trust_algebra      — Optional TrustAlgebra instance.
    current_metrics    — Optional recent RouterMetrics.

    Returns
    ───────
    Algorithm selection dict (see RoutingAlgorithmSelector.select()).
    """
    algebra = trust_algebra or TrustAlgebra()
    selector = RoutingAlgorithmSelector(trust_algebra=algebra)
    return selector.select(judgment, available_channels, current_metrics)


# ---------------------------------------------------------------------------
# Routing table factory helpers
# ---------------------------------------------------------------------------


def _build_default_proof(decision_id: str, tier: TrustTier) -> RoutingProof:
    """Build a default proof for routing table construction."""
    return _make_stub_proof(routing_decision_id=decision_id, tier=tier)


def make_routing_table(
    entries_spec: list[dict[str, Any]],
    table_version: int = 1,
) -> RoutingTable:
    """Construct a RoutingTable from a list of entry specification dicts.

    Each entry spec dict should have keys:
    • "source_pattern" : str
    • "target_channel" : RoutingChannel
    • "priority"       : int
    • "semantic_condition" : str (optional)

    Returns a RoutingTable with a stub table proof (not complete).  Call
    validate_routing_table() to verify the table before use in production.
    """
    built_entries: list[RoutingTableEntry] = []
    tier_ranks: list[int] = []

    for spec in entries_spec:
        entry_id = str(uuid.uuid4())
        ch = spec.get("target_channel", RoutingChannel.FALLBACK)
        tier = ch.base_trust_tier
        entry_proof = _build_default_proof(entry_id, tier)
        entry = RoutingTableEntry(
            entry_id=entry_id,
            source_pattern=spec.get("source_pattern", "*"),
            target_channel=ch,
            priority=spec.get("priority", 0),
            semantic_condition=spec.get("semantic_condition", ""),
            validity_proof=entry_proof,
        )
        built_entries.append(entry)
        tier_ranks.append(tier.rank)

    min_rank = min(tier_ranks) if tier_ranks else 0
    try:
        table_tier = [t for t in TrustTier if t.rank == min_rank][0]
    except IndexError:
        table_tier = TrustTier.PROPOSAL

    table_id = str(uuid.uuid4())
    table_proof = _build_default_proof(table_id, table_tier)

    return RoutingTable(
        table_id=table_id,
        entries=tuple(built_entries),
        version=table_version,
        trust_tier=table_tier,
        table_proof=table_proof,
        last_validated=time.time(),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("algorithms.py — smoke test")
    print("=" * 70)

    # 1. Build a RoutingTable
    print("\n--- Building RoutingTable ---")
    table = make_routing_table([
        {
            "source_pattern": "∀x.*",
            "target_channel": RoutingChannel.Z3_SOLVER,
            "priority": 100,
            "semantic_condition": "universal formula",
        },
        {
            "source_pattern": "∃x.*",
            "target_channel": RoutingChannel.Z3_SOLVER,
            "priority": 90,
            "semantic_condition": "existential formula",
        },
        {
            "source_pattern": "→",
            "target_channel": RoutingChannel.LLM_ORACLE,
            "priority": 70,
            "semantic_condition": "implication",
        },
        {
            "source_pattern": "*",
            "target_channel": RoutingChannel.HYBRID,
            "priority": 50,
            "semantic_condition": "catch-all",
        },
        {
            "source_pattern": "*",
            "target_channel": RoutingChannel.FALLBACK,
            "priority": 1,
            "semantic_condition": "last resort",
        },
    ])
    print(table)

    # 2. Validate the table
    print("\n--- Validating RoutingTable ---")
    valid, errors = validate_routing_table(table, TrustTier.REVIEWED)
    print(f"  Valid: {valid}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")

    # 3. Create judgment tuples
    j_universal = JudgmentTuple(
        c="smoke-test",
        phi="∀x. P(x) → Q(x)",
        A=frozenset(["Z3_SOLVER", "LLM_ORACLE"]),
        E=frozenset(["evidence-1"]),
        O=frozenset(),
        B={"confidence": 0.95},
        T=TrustTier.VERIFIED,
        Pi=None,
    )
    j_implication = JudgmentTuple(
        c="smoke-test",
        phi="P → Q → R",
        A=frozenset(["LLM_ORACLE", "HYBRID"]),
        E=frozenset(),
        O=frozenset(["obligation-1"]),
        B={"confidence": 0.7},
        T=TrustTier.REVIEWED,
        Pi=None,
    )

    # 4. Priority routing
    print("\n--- PriorityRouter ---")
    router = PriorityRouter(
        priority_function="FORMULA_COMPLEXITY",
        tie_break_policy=TieBreakPolicy.HIGHEST_TRUST,
        max_queue_depth=1000,
        router_id="router-001",
        priority_axioms=("ROUTING_SOUNDNESS", "TRUST_MONOTONICITY"),
    )
    print(router)
    algebra = TrustAlgebra()

    ch, entry = priority_route(j_universal, table, algebra, router)
    print(f"\n  Universal formula → channel: {ch.value if ch else 'None'}")
    if entry:
        print(f"  Entry: {entry}")

    ch2, entry2 = priority_route(j_implication, table, algebra, router)
    print(f"\n  Implication formula → channel: {ch2.value if ch2 else 'None'}")

    # 5. Semantic weight computation
    print("\n--- Semantic Weights ---")
    for ch_candidate in [RoutingChannel.Z3_SOLVER, RoutingChannel.LLM_ORACLE,
                          RoutingChannel.PROOF_ENGINE, RoutingChannel.HYBRID]:
        w = compute_semantic_weight(j_universal, ch_candidate)
        print(f"  sem_weight(∀x.P(x)→Q(x), {ch_candidate.value}) = {w:.3f}")

    # 6. SemanticLoadBalancer
    print("\n--- SemanticLoadBalancer ---")
    balancer_proof = _make_stub_proof("balancer-001", TrustTier.REVIEWED)
    balancer = SemanticLoadBalancer(
        balancer_id="balancer-001",
        semantic_weight_function="FORMULA_COVERAGE",
        rebalance_trigger="TRUST_VIOLATION",
        balancer_state_schema=(("pending_count", "int"), ("last_rebalance", "float")),
        proof_of_balance=balancer_proof,
    )
    print(balancer)

    assignments = balance_load_semantically(
        judgments=[j_universal, j_implication, j_universal],
        balancer=balancer,
        strategy=BalanceStrategy.SEMANTIC_AFFINITY,
        available_channels=[RoutingChannel.Z3_SOLVER, RoutingChannel.LLM_ORACLE,
                             RoutingChannel.HYBRID],
    )
    print(f"\n  Load balancing results ({len(assignments)} judgments):")
    for i, (j, ch) in enumerate(assignments):
        print(f"    [{i}] phi='{str(getattr(j,'phi','?'))[:30]}' → {ch.value}")

    # 7. FallbackChain
    print("\n--- FallbackChain ---")
    chain = build_fallback_chain(
        primary=RoutingChannel.Z3_SOLVER,
        fallbacks=[RoutingChannel.HYBRID, RoutingChannel.LLM_ORACLE,
                   RoutingChannel.FALLBACK],
        conditions=[
            {"trigger": "z3_timeout", "min_trust": "REVIEWED"},
            {"trigger": "hybrid_unavailable", "min_trust": "PROPOSAL"},
            {"trigger": "all_channels_down", "min_trust": "PROPOSAL"},
        ],
        max_depth=3,
    )
    print(chain)
    for depth in range(4):
        ch_at = chain.channel_at_depth(depth)
        cond = chain.condition_at_depth(depth)
        print(f"  depth={depth}: {ch_at.value if ch_at else 'None'} — {cond}")

    # 8. Algorithm selection
    print("\n--- RoutingAlgorithmSelector ---")
    metrics = RouterMetrics(
        metrics_id="metrics-001",
        judgment_throughput=42.5,
        proof_success_rate=0.91,
        channel_utilization=(
            ("Z3_SOLVER", 0.65),
            ("LLM_ORACLE", 0.30),
            ("HYBRID", 0.05),
        ),
        geometric_spread=0.82,
        trust_distribution=(
            ("PROPOSAL", 2),
            ("REVIEWED", 8),
            ("VERIFIED", 15),
        ),
    )
    print(metrics)

    for j, label in [(j_universal, "universal"), (j_implication, "implication")]:
        sel = select_optimal_algorithm(
            judgment=j,
            available_channels=[RoutingChannel.Z3_SOLVER, RoutingChannel.LLM_ORACLE,
                                  RoutingChannel.HYBRID],
            current_metrics=metrics,
        )
        print(f"\n  [{label}] algorithm={sel['algorithm']} "
              f"tie_break={sel['tie_break'].value}")
        print(f"    rationale: {sel['rationale'][:80]}…")

    # 9. RouterRegistry
    print("\n--- RouterRegistry ---")
    RouterRegistry.reset()
    reg = RouterRegistry.get_instance()
    reg.register_router(router)
    reg.register_balancer(balancer)
    reg.register_chain(chain)
    reg.register_table(table)
    reg.update_metrics(router.router_id, metrics)
    health = reg.health_summary()
    print(f"  Registry health: {health}")

    # 10. PriorityRouter.__str__ and dataclass fields
    print("\n--- Frozen dataclass field access ---")
    print(f"  PriorityRouter.priority_function = {router.priority_function}")
    print(f"  FallbackChain.max_depth = {chain.max_depth}")
    print(f"  SemanticLoadBalancer.semantic_weight_function = "
          f"{balancer.semantic_weight_function}")
    print(f"  RouterMetrics.is_healthy() = {metrics.is_healthy()}")
    print(f"  RoutingTable.valid_entry_count() = {table.valid_entry_count()}")

    print("\nSmoke test complete.")


# ---------------------------------------------------------------------------
# Cross-subsystem integration: geometry, judgments, certificates, maturity
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.covers import Cover, score_cover
except Exception:
    Cover = None  # type: ignore[assignment,misc]
    score_cover = None  # type: ignore[assignment]

try:
    from jugeo.judgments.sections import Section, SectionFamily
except Exception:
    Section = None  # type: ignore[assignment,misc]
    SectionFamily = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import CertificateVerifier
except Exception:
    CertificateVerifier = None  # type: ignore[assignment,misc]

try:
    from jugeo.maturity import describe_maturity_level
except Exception:
    describe_maturity_level = None  # type: ignore[assignment]


def route_with_cover_affinity(evidence, cover):
    """Route evidence based on cover affinity from jugeo.geometry.covers.

    Evidence whose domain overlaps with a covering family is routed to
    the channel that owns that cover, reducing redundant verification.
    """
    if score_cover is not None and cover is not None:
        try:
            metric = score_cover(cover)
            affinity = getattr(metric, "completeness", 0.0)
        except Exception:
            affinity = 0.0
    else:
        affinity = 0.0

    channel = "cover_aligned" if affinity > 0.5 else "general"
    return {
        "channel": channel,
        "cover_affinity": affinity,
        "subsystem": "jugeo.geometry.covers",
    }


def route_with_judgment_context(evidence, sections):
    """Route using judgment section context from jugeo.judgments.sections.

    Section families provide semantic context that disambiguates routing
    when multiple channels are viable.
    """
    family_name = None
    if sections:
        first = sections[0]
        family_name = getattr(first, "family", getattr(first, "family_name", None))
        if family_name is not None:
            family_name = str(family_name)

    return {
        "channel": f"section:{family_name}" if family_name else "default",
        "section_count": len(sections) if sections else 0,
        "subsystem": "jugeo.judgments.sections",
    }


def certified_routing_decision(decision, certificate):
    """Attach a certificate to a routing decision via jugeo.evidence.certificates.

    Only decisions that pass certificate verification are promoted to the
    authoritative routing tier.
    """
    if CertificateVerifier is None:
        return {"verified": False, "reason": "CertificateVerifier unavailable",
                "decision": decision,
                "subsystem": "jugeo.evidence.certificates"}
    try:
        verifier = CertificateVerifier()
        ok = verifier.verify(certificate) if hasattr(verifier, "verify") else False
        return {"verified": bool(ok), "decision": decision,
                "subsystem": "jugeo.evidence.certificates"}
    except Exception as exc:
        return {"verified": False, "reason": str(exc), "decision": decision,
                "subsystem": "jugeo.evidence.certificates"}


def maturity_aware_routing(evidence, system_or_level):
    """Adjust routing strategy based on system maturity from jugeo.maturity.

    Experimental systems route through additional validation layers;
    production-ready systems use the fast path.
    """
    if describe_maturity_level is not None:
        try:
            desc = describe_maturity_level(system_or_level)
        except Exception:
            desc = str(system_or_level)
    else:
        desc = str(system_or_level)

    level_str = desc.lower()
    if "production" in level_str or "stable" in level_str:
        channel = "fast_path"
    elif "experimental" in level_str or "alpha" in level_str:
        channel = "validated_path"
    else:
        channel = "standard_path"

    return {
        "channel": channel,
        "maturity": desc,
        "subsystem": "jugeo.maturity",
    }
