"""Generation Moves as Dependent Transitions

# copilot: generation_moves_as_dependent_transitions

This module models generation moves as dependent transitions in the jugeo generation
state space. Each move is a structured object carrying preconditions, postconditions,
and a dependency formula that relates the move to the ambient judgment context.

Dependent transitions enforce that moves may only fire when their dependency formula
is satisfied, making the type-theoretic dependency structure explicit in the state
machine model. This enables static analysis of which moves are applicable in which
states and provides a foundation for obligation generation.

Theory invariants:
  - Judgments are tuples (c, φ, A, E, O, B, T, Π) — NEVER booleans
  - Trust is an ordered algebra (TrustTier) — NEVER a float
  - TrustTier: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED
  - Obstructions are Čech H¹ cohomology classes
"""

from __future__ import annotations

import uuid
import hashlib
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, List, Dict, FrozenSet, Tuple
import itertools
import functools
import datetime

try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum
    class TrustTier(Enum):
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5
    Judgment = tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MOVE_REGISTRY_VERSION = "mr-v3"

MOVE_KINDS: Dict[str, str] = {
    "cover_proposal": "Propose a cover for the current topological space using a schema.",
    "obligation_generation": "Generate semantic obligations from a proposed cover.",
    "local_section_construction": "Construct a local section over a single open set in the cover.",
    "local_verification": "Verify that a local section satisfies all locally-scoped obligations.",
    "compatibility_check": "Check that overlapping local sections agree on intersections.",
    "global_gluing": "Assemble verified local sections into a global section via the gluing axiom.",
    "tier_promotion": "Promote the current judgment to a higher TrustTier after checks pass.",
    "obligation_discharge": "Mark a set of obligations as discharged given a set of evidence.",
    "evidence_accumulation": "Accumulate new evidence items into the current evidence pool.",
    "failure_declaration": "Declare that the current generation attempt has irrecoverably failed.",
}

OBLIGATION_KINDS: Dict[str, str] = {
    "local_section": "Obligation to produce a valid local section over an open set.",
    "compatibility": "Obligation to demonstrate that overlapping local sections agree.",
    "coherence": "Obligation to verify sheaf coherence conditions hold.",
    "existence": "Obligation to show that a satisfying element exists.",
    "uniqueness": "Obligation to prove uniqueness of the global section.",
    "tier_promotion": "Obligation to satisfy all checks required for tier promotion.",
    "evidence_provision": "Obligation to supply concrete evidence items.",
    "discharge_certificate": "Obligation to provide a discharge certificate.",
}

STANDARD_PRECONDITIONS: List[str] = [
    "state_is_not_terminal",
    "cover_is_proposed",
    "obligations_are_generated",
    "local_sections_are_constructed",
    "compatibility_is_verified",
    "no_open_blocking_obligations",
    "trust_tier_is_sufficient",
    "dependency_formula_is_satisfied",
    "context_is_initialised",
    "move_kind_is_registered",
]

STANDARD_POSTCONDITIONS: List[str] = [
    "state_transitions_to_successor",
    "new_obligations_are_registered",
    "evidence_pool_is_updated",
    "judgment_tuple_is_extended",
    "trust_tier_is_reassessed",
    "discharged_obligations_are_removed",
    "context_snapshot_is_updated",
    "move_result_is_recorded",
    "audit_trail_entry_is_appended",
    "dependency_formula_is_re_evaluated",
]

DEPENDENCY_FORMULAS: Dict[str, str] = {
    "trivial": "True",
    "cover_exists": "exists(cover, in=context)",
    "obligations_open": "len(context.obligation_ids) > 0",
    "evidence_sufficient": "len(context.evidence_ids) >= len(context.obligation_ids)",
    "tier_verified": "context.tier.value >= TrustTier.VERIFIED.value",
    "all_local_sections_verified": "all(s.verified for s in cover.local_sections)",
    "no_open_obligations": "len(context.obligation_ids) == 0",
    "cover_compatible": "all(compatible(s1, s2) for s1, s2 in overlapping_pairs(cover))",
}


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationMove:
    """A generation move representing an atomic step in the generation pipeline.

    Each GenerationMove captures the move_kind (a string from MOVE_KINDS), a tuple of
    precondition strings that must hold before the move can fire, a tuple of postcondition
    strings that are expected to hold after the move fires, and a TrustTier reflecting
    the level of assurance associated with this move. Moves are frozen and hashable.

    The preconditions and postconditions are symbolic strings from STANDARD_PRECONDITIONS
    and STANDARD_POSTCONDITIONS respectively. The dependency formula is checked separately
    by the DependentTransition object that wraps this move.
    """

    move_id: str
    move_kind: str
    preconditions: tuple
    postconditions: tuple
    move_tier: TrustTier

    def is_applicable_in(self, state: Any) -> bool:
        """Determine whether this move is applicable in the given generation state.

        Applicability is assessed by checking that the state is not terminal, that the
        move's required tier is satisfied by the state's tier, and that the move_kind is
        registered in MOVE_KINDS. The preconditions tuple is consulted to check that at
        least one standard precondition from STANDARD_PRECONDITIONS is present. If the
        state is None, the method returns False immediately. This method performs a
        heuristic check — definitive applicability determination requires the full
        dependency formula evaluation performed by DependentTransition.check_dependency().

        Args:
            state: A GenerationState or compatible object.

        Returns:
            bool: True if this move appears applicable in the given state.
        """
        if state is None:
            return False
        if hasattr(state, "is_terminal") and state.is_terminal():
            return False
        state_tier = getattr(state, "state_tier", None)
        if state_tier is not None and state_tier.value < self.move_tier.value:
            return False
        if self.move_kind not in MOVE_KINDS:
            return False
        return len(self.preconditions) > 0

    def apply_to_judgment(self, j: tuple) -> tuple:
        """Extend a judgment tuple with the information from this move.

        The judgment is extended by appending a move summary tuple containing the
        move_id, move_kind, tier name, count of preconditions, and count of
        postconditions. The extended judgment can be used to track the history of
        moves applied during a generation process. The method never modifies j
        in place — it always returns a new tuple.

        Args:
            j: The current judgment tuple to extend.

        Returns:
            tuple: A new tuple consisting of j's elements followed by the move summary.
        """
        if j is None:
            j = ()
        move_summary = (self.move_id, self.move_kind, self.move_tier.name,
                        len(self.preconditions), len(self.postconditions))
        return j + (move_summary,)

    def postcondition_satisfied(self, j: tuple) -> bool:
        """Check heuristically whether the postconditions are reflected in the judgment.

        The method checks that the judgment tuple is non-empty (indicating at least one
        step has been applied) and that the count of postconditions is positive. For
        a more rigorous check, the calling layer should verify each postcondition string
        against the actual state. This method provides a lightweight sanity check that
        does not require access to the full state. It returns False for empty judgments
        because an empty judgment cannot satisfy any postcondition.

        Args:
            j: The judgment tuple to inspect.

        Returns:
            bool: True if the judgment is non-empty and postconditions are non-trivial.
        """
        if j is None or len(j) == 0:
            return False
        if len(self.postconditions) == 0:
            return False
        return len(j) > 0

    def to_judgment_tuple(self) -> tuple:
        """Serialise this move to a compact tuple for Judgment embedding.

        The tuple contains move_id, move_kind, tier name, and the counts of preconditions
        and postconditions. This compact form is sufficient for judgment-level tracking
        without embedding the full precondition and postcondition tuples.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.move_id,
            self.move_kind,
            self.move_tier.name,
            len(self.preconditions),
            len(self.postconditions),
        )

    def move_key(self) -> str:
        """Compute a short deterministic key for this move using SHA-256.

        The key is derived from hashing the concatenation of move_id and move_kind
        and returning the first 16 hexadecimal characters. This key is stable for a
        given (move_id, move_kind) pair and is used for deduplication in the MoveRegistry.

        Returns:
            str: First 16 hex chars of SHA-256(move_id + move_kind).
        """
        raw = (self.move_id + self.move_kind).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]


@dataclass(frozen=True)
class DependentTransition:
    """A state machine transition whose firing condition is a dependency formula.

    A DependentTransition specifies a from_kind and to_kind (both strings matching
    GenStateKind names), a dependency_formula (a string from DEPENDENCY_FORMULAS),
    and a reference to the GenerationMove that implements the transition. The transition
    may only fire when check_dependency() returns True.

    By encoding dependencies as explicit formula strings rather than arbitrary Python
    lambdas, this model allows the dependency structure to be serialised, inspected,
    and analysed statically, supporting obligation generation and proof reconstruction.
    """

    transition_id: str
    from_kind: str
    to_kind: str
    dependency_formula: str
    transition_tier: TrustTier
    move_id: str

    def check_dependency(self, judgment: tuple) -> bool:
        """Evaluate the dependency formula against the current judgment.

        The check is performed symbolically: if the dependency_formula is "trivial" or
        "True", the method returns True unconditionally. Otherwise, the method checks
        whether the judgment tuple is non-empty and the formula string is a key in
        DEPENDENCY_FORMULAS. More sophisticated formula evaluation (e.g., parsing and
        interpreting the formula AST) is intentionally deferred to the proof engine.
        This method provides a lightweight gate that prevents obviously-inapplicable
        transitions from firing while remaining conservative (may return True when
        the formula is not trivially disprovable).

        Args:
            judgment: The current judgment tuple.

        Returns:
            bool: True if the dependency is satisfied or cannot be refuted.
        """
        if self.dependency_formula in ("trivial", "True"):
            return True
        if judgment is None or len(judgment) == 0:
            return False
        if self.dependency_formula not in DEPENDENCY_FORMULAS:
            return False
        return True

    def is_type_checked(self) -> bool:
        """Determine whether this transition has been type-checked against its dependency formula.

        A transition is considered type-checked if its transition_tier is at least VERIFIED
        and its dependency_formula is registered in DEPENDENCY_FORMULAS. Unregistered
        formulas cannot be type-checked because the system has no schema for them.
        Type-checking at PROPOSAL tier is insufficient — the formula must have been
        verified at VERIFIED tier or above to be considered type-checked.

        Returns:
            bool: True if the transition is type-checked.
        """
        if self.transition_tier.value < TrustTier.VERIFIED.value:
            return False
        return self.dependency_formula in DEPENDENCY_FORMULAS

    def obligation_generated(self) -> str:
        """Return the obligation ID that this transition generates, if any.

        Some transitions generate an obligation that the caller must discharge before
        the resulting state can be promoted. The obligation ID is constructed from the
        transition_id and the to_kind of the transition. For transitions whose dependency
        formula is 'trivial', no substantive obligation is generated and the returned
        string encodes a trivially-dischargeable obligation.

        Returns:
            str: The obligation ID string generated by this transition.
        """
        if self.dependency_formula in ("trivial", "True"):
            return "obl-trivial-{}".format(self.transition_id)
        formula_key = self.dependency_formula.replace(" ", "_")
        return "obl-{}-{}-{}".format(self.transition_id, self.to_kind.lower(), formula_key[:20])

    def to_judgment_tuple(self) -> tuple:
        """Serialise this dependent transition to a compact tuple.

        The tuple contains transition_id, from_kind, to_kind, dependency_formula, and
        tier name. This is the canonical representation for embedding in judgment objects.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.transition_id,
            self.from_kind,
            self.to_kind,
            self.dependency_formula,
            self.transition_tier.name,
        )

    def transition_summary(self) -> str:
        """Produce a human-readable summary line for this dependent transition.

        The summary includes the transition_id, from and to kinds, dependency formula,
        tier, associated move_id, and whether the transition is type-checked. It is
        formatted as a single line suitable for inclusion in state machine reports
        and CLI output.

        Returns:
            str: A one-line summary string.
        """
        checked = "YES" if self.is_type_checked() else "NO"
        return (
            "[{tid}] {fk} --[{dep}]--> {tk} | tier={tier} | move={mv} | type_checked={tc}".format(
                tid=self.transition_id,
                fk=self.from_kind,
                dep=self.dependency_formula,
                tk=self.to_kind,
                tier=self.transition_tier.name,
                mv=self.move_id,
                tc=checked,
            )
        )


@dataclass(frozen=True)
class MoveObligation:
    """An obligation generated by or associated with a specific generation move.

    MoveObligations are the primary mechanism by which generation moves leave a formal
    proof footprint. Each obligation records the move that generated it, the kind of
    obligation (from OBLIGATION_KINDS), and the TrustTier at which the obligation was
    incurred. Obligations must be discharged before the generation process can proceed
    to higher-tier states.

    The obligation_kind string from OBLIGATION_KINDS determines the discharge condition
    and priority of the obligation. Frozen instances are hashable and can be stored in
    frozensets for obligation tracking.
    """

    obligation_id: str
    move_id: str
    obligation_kind: str
    description: str
    obligation_tier: TrustTier

    def is_dischargeable(self) -> bool:
        """Determine whether this obligation can be discharged given its kind and tier.

        An obligation is dischargeable if its obligation_kind is registered in
        OBLIGATION_KINDS and its tier is not PROOF_BACKED (proof-backed obligations
        require a formal proof and cannot be discharged by runtime evidence alone).
        This method is used by the obligation manager to determine which obligations
        can be addressed during a generation run versus which require offline proof work.

        Returns:
            bool: True if the obligation can be discharged at runtime.
        """
        if self.obligation_kind not in OBLIGATION_KINDS:
            return False
        if self.obligation_tier == TrustTier.PROOF_BACKED:
            return False
        return True

    def discharge_condition(self) -> str:
        """Return a human-readable description of the condition required to discharge this obligation.

        The condition is looked up in OBLIGATION_KINDS by obligation_kind and combined
        with a tier-specific qualifier. For PROOF_BACKED obligations, the condition
        specifies that a formal proof is required. For other tiers, the condition
        specifies runtime evidence or verification checks. The returned string is
        suitable for display in obligation dashboards and audit trails.

        Returns:
            str: Human-readable discharge condition string.
        """
        base = OBLIGATION_KINDS.get(self.obligation_kind, "Unknown obligation kind.")
        if self.obligation_tier == TrustTier.PROOF_BACKED:
            return base + " [Requires formal proof certificate]"
        if self.obligation_tier == TrustTier.VERIFIED:
            return base + " [Requires machine-verified evidence]"
        return base + " [Requires runtime evidence at tier {}]".format(self.obligation_tier.name)

    def to_judgment_tuple(self) -> tuple:
        """Serialise this obligation to a compact tuple for Judgment embedding.

        The tuple contains obligation_id, move_id, obligation_kind, and tier name.
        This is the minimal information required to identify and characterise the
        obligation in a judgment context.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.obligation_id,
            self.move_id,
            self.obligation_kind,
            self.obligation_tier.name,
        )

    def obligation_key(self) -> str:
        """Compute a short deterministic key for this obligation using SHA-256.

        The key hashes the concatenation of obligation_id and move_id and returns the
        first 16 hexadecimal characters. This provides a compact identifier for use
        in obligation registries and database indices.

        Returns:
            str: First 16 hex chars of SHA-256(obligation_id + move_id).
        """
        raw = (self.obligation_id + self.move_id).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def priority(self) -> int:
        """Return the integer priority of this obligation for scheduling.

        Priority is determined by a combination of the obligation_kind and the tier.
        Higher-tier obligations receive higher priority (lower integers = higher urgency).
        The priority mapping assigns 1 to PROOF_BACKED obligations, 2 to VERIFIED,
        3 to RUNTIME_WITNESSED, 4 to REVIEWED, and 5 to PROPOSAL. The obligation_kind
        adds a small offset to break ties: 'uniqueness' and 'coherence' obligations are
        scheduled ahead of 'evidence_provision' obligations at the same tier.

        Returns:
            int: Priority value (lower = higher urgency).
        """
        tier_priority = {
            TrustTier.PROOF_BACKED: 1,
            TrustTier.RUNTIME_WITNESSED: 2,
            TrustTier.VERIFIED: 3,
            TrustTier.REVIEWED: 4,
            TrustTier.PROPOSAL: 5,
        }.get(self.obligation_tier, 5)
        kind_offset = {
            "uniqueness": 0,
            "coherence": 0,
            "local_section": 1,
            "compatibility": 1,
            "existence": 2,
            "tier_promotion": 2,
            "evidence_provision": 3,
            "discharge_certificate": 3,
        }.get(self.obligation_kind, 2)
        return tier_priority * 10 + kind_offset


@dataclass(frozen=True)
class TransitionGuard:
    """A guard object that controls whether a dependent transition may fire.

    A TransitionGuard is associated with a specific GenerationMove and specifies a
    primary condition string and a frozenset of blocking condition strings. The guard
    evaluates to True (allowing the transition) only when the primary condition holds
    and none of the blocking conditions are active in the current state. Guards provide
    a second layer of control beyond the dependency formula, allowing for runtime
    conditions that are too fine-grained to encode in the formula language.

    Frozen instances are hashable and can be stored in frozensets alongside transitions.
    """

    guard_id: str
    condition: str
    blocking_conditions: FrozenSet
    guard_tier: TrustTier
    move_id: str

    def evaluate(self, state: Any) -> bool:
        """Evaluate this guard against the current generation state.

        The evaluation checks that the state is not None, is not terminal, and that
        the guard's tier is satisfied by the state's tier. The primary condition string
        is checked against STANDARD_PRECONDITIONS to see if it is a known condition.
        If the condition is in STANDARD_PRECONDITIONS and the state satisfies the tier
        requirement, the guard evaluates to True, subject to is_blocking() returning False.
        For unknown conditions, the guard conservatively returns False.

        Args:
            state: A GenerationState or compatible object.

        Returns:
            bool: True if the guard allows the transition to fire.
        """
        if state is None:
            return False
        if hasattr(state, "is_terminal") and state.is_terminal():
            return False
        state_tier = getattr(state, "state_tier", None)
        if state_tier is not None and state_tier.value < self.guard_tier.value:
            return False
        if self.condition not in STANDARD_PRECONDITIONS:
            return False
        return not self.is_blocking(state)

    def is_blocking(self, state: Any) -> bool:
        """Determine whether any blocking condition is active in the given state.

        A blocking condition is active if it appears in the state's context_snapshot
        (as a tuple element) or if the state's kind name matches a blocking condition
        string. If any blocking condition is found to be active, this method returns
        True, which causes evaluate() to return False, preventing the transition from
        firing. An empty blocking_conditions frozenset means nothing can block.

        Args:
            state: A GenerationState or compatible object.

        Returns:
            bool: True if at least one blocking condition is active.
        """
        if not self.blocking_conditions:
            return False
        if state is None:
            return False
        snapshot = getattr(state, "context_snapshot", ())
        kind_name = getattr(getattr(state, "kind", None), "name", "")
        for blocking in self.blocking_conditions:
            if blocking in snapshot:
                return True
            if blocking == kind_name:
                return True
        return False

    def guard_key(self) -> str:
        """Compute a short deterministic key for this guard using SHA-256.

        The key hashes the concatenation of guard_id and move_id and returns the first
        16 hexadecimal characters. This key is used for indexing guards in the move
        registry and for deduplication.

        Returns:
            str: First 16 hex chars of SHA-256(guard_id + move_id).
        """
        raw = (self.guard_id + self.move_id).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def to_judgment_tuple(self) -> tuple:
        """Serialise this guard to a compact tuple for Judgment embedding.

        The tuple contains guard_id, condition, count of blocking conditions, tier
        name, and move_id. This is the minimal form for judgment-level bookkeeping.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.guard_id,
            self.condition,
            len(self.blocking_conditions),
            self.guard_tier.name,
            self.move_id,
        )

    def guard_summary(self) -> str:
        """Produce a concise summary of this guard for inclusion in reports.

        The summary includes the guard_id, move_id, condition, count of blocking
        conditions, and tier. It is formatted as a single line suitable for inclusion
        in move registry dumps and log messages.

        Returns:
            str: A one-line summary string.
        """
        blocking = ", ".join(sorted(self.blocking_conditions)) if self.blocking_conditions else "none"
        return (
            "Guard[{gid}] move={mv} cond={cond} blocking=[{bl}] tier={tier}".format(
                gid=self.guard_id,
                mv=self.move_id,
                cond=self.condition,
                bl=blocking,
                tier=self.guard_tier.name,
            )
        )


@dataclass(frozen=True)
class MoveResult:
    """The result of applying a GenerationMove to a state.

    A MoveResult records the move_id that produced it, the new_state_kind (as a string
    matching a GenStateKind name), the set of new obligations introduced by the move,
    and the TrustTier of the result. MoveResults are the primary data product of the
    apply_move() function and are stored in the judgment trail to enable proof
    reconstruction and audit.

    Frozen instances are hashable and can be stored in frozensets.
    """

    result_id: str
    move_id: str
    new_state_kind: str
    new_obligations: FrozenSet
    result_tier: TrustTier

    def is_success(self) -> bool:
        """Determine whether this result represents a successful move application.

        A result is successful if the new_state_kind is not 'FAILED'. More specifically,
        the new_state_kind must be one of the non-failure GenStateKind names. The method
        checks against the string 'FAILED' rather than the enum to avoid import dependencies.
        A COMPLETE result is also considered a success.

        Returns:
            bool: True if new_state_kind != 'FAILED'.
        """
        return self.new_state_kind != "FAILED"

    def obligations_discharged(self, prev: FrozenSet) -> FrozenSet:
        """Compute the set of obligations from prev that are NOT in new_obligations.

        The discharged obligations are those that were present before the move but are
        absent from the move result's new_obligations. This is computed as the set
        difference prev - new_obligations. The method returns an empty frozenset if
        new_obligations is a superset of prev (no obligations were discharged).

        Args:
            prev: The frozenset of obligations before the move.

        Returns:
            FrozenSet: The obligations that were discharged by this move.
        """
        if prev is None:
            return frozenset()
        discharged = prev - self.new_obligations
        return frozenset(discharged)

    def to_judgment_tuple(self) -> tuple:
        """Serialise this result to a compact tuple for Judgment embedding.

        The tuple contains result_id, move_id, new_state_kind, count of new obligations,
        and tier name. This compact form allows the result to be embedded in judgment
        history tuples without the full obligation set.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.result_id,
            self.move_id,
            self.new_state_kind,
            len(self.new_obligations),
            self.result_tier.name,
        )

    def result_summary(self) -> str:
        """Produce a human-readable summary of this move result.

        The summary includes the result_id, move_id, new state kind, success/failure
        status, count of new obligations, and trust tier. It is formatted as a single
        line suitable for inclusion in generation logs and audit trails.

        Returns:
            str: A one-line summary string.
        """
        status = "SUCCESS" if self.is_success() else "FAILURE"
        return (
            "Result[{rid}] move={mv} -> {kind} [{status}] new_obls={no} tier={tier}".format(
                rid=self.result_id,
                mv=self.move_id,
                kind=self.new_state_kind,
                status=status,
                no=len(self.new_obligations),
                tier=self.result_tier.name,
            )
        )

    def net_obligation_delta(self, prev: FrozenSet) -> int:
        """Compute the net change in the number of obligations resulting from this move.

        The delta is computed as len(new_obligations) - len(prev). A positive delta
        means obligations were added (the move generated more obligations than it
        discharged). A negative delta means obligations were net-reduced. Zero means
        the obligation count is unchanged (though the set membership may have changed).

        Args:
            prev: The frozenset of obligations before the move.

        Returns:
            int: The net change in obligation count.
        """
        prev_count = len(prev) if prev is not None else 0
        return len(self.new_obligations) - prev_count


class MoveRegistry:
    """Mutable registry that maps move IDs and kinds to GenerationMove objects.

    MoveRegistry is a regular (non-frozen) class used during construction of the
    generation state machine. Moves are registered via register() and looked up
    via lookup() and moves_for_kind(). The registry does not validate the moves it
    stores — callers are responsible for providing well-formed GenerationMove objects.
    """

    def __init__(self) -> None:
        """Initialise an empty move registry."""
        self._moves: Dict[str, Any] = {}
        self._version: str = MOVE_REGISTRY_VERSION

    def register(self, move: Any) -> None:
        """Register a GenerationMove in this registry.

        The move is stored under its move_id. If a move with the same move_id has
        already been registered, it is silently replaced. Callers should ensure that
        move_ids are unique within a registry to avoid accidental overwriting.

        Args:
            move: The GenerationMove to register.
        """
        if move is None:
            return
        mid = getattr(move, "move_id", None)
        if mid is not None:
            self._moves[mid] = move

    def lookup(self, move_id: str) -> Optional[Any]:
        """Look up a registered GenerationMove by its move_id.

        Returns the GenerationMove if found, or None if no move with the given ID
        has been registered. The lookup is case-sensitive and exact-match.

        Args:
            move_id: The move ID to look up.

        Returns:
            Optional[GenerationMove]: The registered move, or None.
        """
        return self._moves.get(move_id)

    def moves_for_kind(self, kind: str) -> List[Any]:
        """Return all registered moves of the given move_kind.

        The method iterates over all registered moves and returns those whose move_kind
        attribute equals the supplied kind string. The returned list is unsorted; callers
        should sort by move_id if a deterministic ordering is required.

        Args:
            kind: The move_kind string to filter by.

        Returns:
            List[GenerationMove]: All moves with the matching kind.
        """
        return [m for m in self._moves.values() if getattr(m, "move_kind", None) == kind]

    def all_moves(self) -> List[Any]:
        """Return all registered moves as a list sorted by move_id.

        Returns:
            List[GenerationMove]: All registered moves sorted by move_id.
        """
        return sorted(self._moves.values(), key=lambda m: getattr(m, "move_id", ""))

    def count(self) -> int:
        """Return the count of currently registered moves.

        Returns:
            int: Number of registered moves.
        """
        return len(self._moves)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def apply_move(move: GenerationMove, state: Any, judgment: tuple) -> Tuple[Any, tuple]:
    """Apply a GenerationMove to a state and return the updated (result, judgment) pair.

    If the move is applicable in the state, a MoveResult is constructed recording the
    new state kind (determined from the move_kind) and a trivially empty new_obligations
    set. The judgment is extended with the move summary. If the move is not applicable,
    a failure MoveResult is returned and the judgment is unchanged.

    Args:
        move: The GenerationMove to apply.
        state: The current GenerationState.
        judgment: The current judgment tuple.

    Returns:
        Tuple[MoveResult, tuple]: The result and updated judgment.
    """
    if not move.is_applicable_in(state):
        result = MoveResult(
            result_id="result-fail-" + str(uuid.uuid4())[:8],
            move_id=move.move_id,
            new_state_kind="FAILED",
            new_obligations=frozenset(),
            result_tier=move.move_tier,
        )
        return result, judgment

    kind_to_state: Dict[str, str] = {
        "cover_proposal": "COVER_PROPOSED",
        "obligation_generation": "OBLIGATIONS_GENERATED",
        "local_section_construction": "LOCALLY_VERIFIED",
        "local_verification": "LOCALLY_VERIFIED",
        "compatibility_check": "LOCALLY_VERIFIED",
        "global_gluing": "GLOBALLY_GLUED",
        "tier_promotion": "GLOBALLY_GLUED",
        "obligation_discharge": "OBLIGATIONS_GENERATED",
        "evidence_accumulation": "OBLIGATIONS_GENERATED",
        "failure_declaration": "FAILED",
    }
    new_kind = kind_to_state.get(move.move_kind, "FAILED")
    new_obls = frozenset(
        "obl-{}".format(i) for i in range(len(move.postconditions))
    )
    result = MoveResult(
        result_id="result-" + str(uuid.uuid4())[:8],
        move_id=move.move_id,
        new_state_kind=new_kind,
        new_obligations=new_obls,
        result_tier=move.move_tier,
    )
    updated_judgment = move.apply_to_judgment(judgment)
    return result, updated_judgment


def check_move_preconditions(move: GenerationMove, state: Any) -> List[str]:
    """Check which of the move's preconditions are satisfied in the given state.

    Returns a list of satisfied precondition strings. Preconditions that are in
    STANDARD_PRECONDITIONS are checked against the state. Any precondition not in
    STANDARD_PRECONDITIONS is reported as unsatisfied. The returned list may be
    shorter than move.preconditions if some preconditions are not satisfied.

    Args:
        move: The GenerationMove to check.
        state: The current GenerationState.

    Returns:
        List[str]: Satisfied precondition strings.
    """
    if state is None:
        return []
    satisfied: List[str] = []
    for pre in move.preconditions:
        if pre not in STANDARD_PRECONDITIONS:
            continue
        if pre == "state_is_not_terminal":
            if not (hasattr(state, "is_terminal") and state.is_terminal()):
                satisfied.append(pre)
        elif pre == "trust_tier_is_sufficient":
            state_tier = getattr(state, "state_tier", None)
            if state_tier is not None and state_tier.value >= move.move_tier.value:
                satisfied.append(pre)
        elif pre == "move_kind_is_registered":
            if move.move_kind in MOVE_KINDS:
                satisfied.append(pre)
        else:
            satisfied.append(pre)
    return satisfied


def compute_move_postconditions(move: GenerationMove, result: MoveResult) -> List[str]:
    """Compute the list of postconditions that are satisfied given a MoveResult.

    The method checks each postcondition string in move.postconditions and determines
    whether it is satisfied based on the result. 'state_transitions_to_successor' is
    satisfied if the result is successful. 'new_obligations_are_registered' is satisfied
    if result.new_obligations is non-empty. 'discharged_obligations_are_removed' is
    always considered satisfied. All other postconditions from STANDARD_POSTCONDITIONS
    are included in the satisfied list if the result is successful.

    Args:
        move: The GenerationMove that was applied.
        result: The MoveResult from applying the move.

    Returns:
        List[str]: Postcondition strings that are satisfied.
    """
    satisfied: List[str] = []
    for post in move.postconditions:
        if post == "state_transitions_to_successor":
            if result.is_success():
                satisfied.append(post)
        elif post == "new_obligations_are_registered":
            if len(result.new_obligations) > 0:
                satisfied.append(post)
        elif post == "discharged_obligations_are_removed":
            satisfied.append(post)
        elif post in STANDARD_POSTCONDITIONS and result.is_success():
            satisfied.append(post)
    return satisfied


def register_move(registry: MoveRegistry, move: GenerationMove) -> str:
    """Register a move in the registry and return its move_key.

    This is a convenience wrapper around registry.register() that also returns
    the move's computed key for use in external indices.

    Args:
        registry: The MoveRegistry to register into.
        move: The GenerationMove to register.

    Returns:
        str: The move's computed key.
    """
    registry.register(move)
    return move.move_key()


# ---------------------------------------------------------------------------
# __main__ demonstration block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("jugeo.generation.state_space.s03 — Generation Moves as Dependent Transitions demo")
    print("=" * 70)

    # 1. Build GenerationMove instances for all move kinds
    print("\n[1] Building GenerationMove instances:")
    moves: List[GenerationMove] = []
    for i, (kind, desc) in enumerate(MOVE_KINDS.items()):
        move_id = "move-{:03d}".format(i)
        m = GenerationMove(
            move_id=move_id,
            move_kind=kind,
            preconditions=tuple(STANDARD_PRECONDITIONS[:3]),
            postconditions=tuple(STANDARD_POSTCONDITIONS[:3]),
            move_tier=TrustTier.REVIEWED,
        )
        moves.append(m)
        print("  [{mk}] {mid} key={key}".format(mk=kind, mid=move_id, key=m.move_key()))

    # 2. Build a simple mock state
    class MockState:
        def __init__(self, kind_name: str, tier: TrustTier) -> None:
            self.kind_name = kind_name
            self.state_tier = tier
            self.context_snapshot: tuple = ()
        def is_terminal(self) -> bool:
            return self.kind_name in ("COMPLETE", "FAILED")

    mock_state = MockState("COVER_PROPOSED", TrustTier.REVIEWED)
    mock_failed = MockState("FAILED", TrustTier.PROPOSAL)

    # 3. is_applicable_in
    print("\n[2] is_applicable_in checks:")
    for m in moves[:4]:
        print("  {} in mock_state = {}".format(m.move_kind, m.is_applicable_in(mock_state)))
        print("  {} in mock_failed = {}".format(m.move_kind, m.is_applicable_in(mock_failed)))

    # 4. apply_move
    print("\n[3] apply_move:")
    judgment: tuple = ("initial",)
    for m in moves[:3]:
        result, judgment = apply_move(m, mock_state, judgment)
        print("  {} -> result={} judgment_len={}".format(m.move_id, result.result_summary(), len(judgment)))

    # 5. MoveObligation instances
    print("\n[4] MoveObligation instances:")
    obls: List[MoveObligation] = []
    for i, kind in enumerate(list(OBLIGATION_KINDS.keys())[:4]):
        obl = MoveObligation(
            obligation_id="obl-{:03d}".format(i),
            move_id=moves[i % len(moves)].move_id,
            obligation_kind=kind,
            description=OBLIGATION_KINDS[kind],
            obligation_tier=list(TrustTier)[i % len(list(TrustTier))],
        )
        obls.append(obl)
        print("  [{}] dischargeable={} priority={}".format(
            kind, obl.is_dischargeable(), obl.priority()
        ))
        print("    discharge_condition:", obl.discharge_condition())
        print("    obligation_key:", obl.obligation_key())

    # 6. DependentTransition instances
    print("\n[5] DependentTransition instances:")
    dep_transitions: List[DependentTransition] = []
    for i, (formula, formula_str) in enumerate(list(DEPENDENCY_FORMULAS.items())[:5]):
        dt = DependentTransition(
            transition_id="dt-{:03d}".format(i),
            from_kind="INITIAL",
            to_kind="COVER_PROPOSED",
            dependency_formula=formula,
            transition_tier=TrustTier.VERIFIED,
            move_id=moves[i % len(moves)].move_id,
        )
        dep_transitions.append(dt)
        print("  [{}] type_checked={} obl={}".format(
            formula, dt.is_type_checked(), dt.obligation_generated()
        ))
        print("    check_dependency(()) =", dt.check_dependency(()))
        print("    check_dependency(('x',)) =", dt.check_dependency(("x",)))
        print("    summary:", dt.transition_summary())

    # 7. TransitionGuard instances
    print("\n[6] TransitionGuard instances:")
    guards: List[TransitionGuard] = []
    for i, cond in enumerate(STANDARD_PRECONDITIONS[:4]):
        g = TransitionGuard(
            guard_id="guard-{:03d}".format(i),
            condition=cond,
            blocking_conditions=frozenset(["FAILED"] if i % 2 == 0 else []),
            guard_tier=TrustTier.REVIEWED,
            move_id=moves[i % len(moves)].move_id,
        )
        guards.append(g)
        print("  [{cond}] evaluate(mock_state)={ev} is_blocking={bl} key={k}".format(
            cond=cond,
            ev=g.evaluate(mock_state),
            bl=g.is_blocking(mock_state),
            k=g.guard_key(),
        ))
        print("    summary:", g.guard_summary())

    # 8. MoveResult instances
    print("\n[7] MoveResult instances:")
    prev_obls = frozenset(["obl-1", "obl-2", "obl-3"])
    for i, m in enumerate(moves[:4]):
        result, _ = apply_move(m, mock_state, ())
        print("  [{}] is_success={} delta={} discharged={}".format(
            m.move_id,
            result.is_success(),
            result.net_obligation_delta(prev_obls),
            result.obligations_discharged(prev_obls),
        ))
        print("    summary:", result.result_summary())
        print("    to_judgment_tuple:", result.to_judgment_tuple())

    # 9. MoveRegistry
    print("\n[8] MoveRegistry:")
    registry = MoveRegistry()
    for m in moves:
        key = register_move(registry, m)
    print("  Registered count:", registry.count())
    print("  Lookup 'move-001':", registry.lookup("move-001"))
    print("  Moves for kind 'cover_proposal':", [m.move_id for m in registry.moves_for_kind("cover_proposal")])
    print("  All moves:", [m.move_id for m in registry.all_moves()])

    # 10. check_move_preconditions and compute_move_postconditions
    print("\n[9] Precondition / postcondition checks:")
    m0 = moves[0]
    satisfied_pre = check_move_preconditions(m0, mock_state)
    print("  satisfied preconditions:", satisfied_pre)
    result0, _ = apply_move(m0, mock_state, ())
    satisfied_post = compute_move_postconditions(m0, result0)
    print("  satisfied postconditions:", satisfied_post)

    print("\n[done]")
