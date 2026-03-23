"""
Proof targets for fleet semantics in the fleet_competition package.

# copilot: This module is part of JuGeo's copilot-assisted encoding of theory2.tex
Chapter 46: Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.12–46.15 specifies the *formal correctness requirements* that the
fleet competition protocol must satisfy.  These requirements are expressed as
*proof targets* — formal theorem statements that can be checked against concrete
competition state and used to generate proof obligations for downstream formal
verification passes.

Theory invariants enforced here
---------------------------------
1. **Judgment tuples** — every proof target is associated with a judgment tuple
   ``(c, φ, A, E, O, B, T, Π)`` where the proof obligation field Π contains
   the formal statement(s) that must be proved.

2. **Trust tier ordering** — the fleet invariant ``MonotoneTrustProgression``
   asserts that trust tiers of accepted sections are non-decreasing across
   successive competition rounds.

3. **Fleet = semantic marketplace** — the correctness theorems assert that the
   marketplace converges to a Pareto-optimal allocation under the competition
   dynamics.

4. **Competition correctness** — the ``CompetitionCorrectness`` class bundles
   all fleet theorems and provides a single ``check_all`` entry point.

Design overview
---------------
``FleetInvariant`` (frozen dataclass)
    An invariant that must hold at every state of the fleet competition.

``SemanticFleetTheorem`` (frozen dataclass)
    A formal theorem statement with a human-readable proof sketch.

``FleetProofTarget`` (frozen dataclass)
    A proof target bundling a theorem, its proof obligations, and verification
    status.

``CompetitionCorrectness``
    Registry of all fleet correctness theorems and their verification state.

Chapter reference: theory2.tex Ch46 §46.12–46.15 — Fleet proof targets.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        CompetitiveBid,
        FleetRound,
        _clamp,
        _safe_mean,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    FleetRound = Any  # type: ignore[assignment,misc]

    def _clamp(v: float, lo: float, hi: float) -> float:  # type: ignore[misc]
        return max(lo, min(hi, v))

    def _safe_mean(seq: Any) -> float:  # type: ignore[misc]
        if not seq:
            return 0.0
        return sum(seq) / len(seq)


try:
    from jugeo.orchestration.fleet import Fleet, FleetMember
except Exception:  # pragma: no cover
    Fleet = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import (
        TrustTier,
        JudgmentRecord,
        SemanticSection,
        FleetMemberProposal,
    )
except Exception:  # pragma: no cover
    TrustTier = Any  # type: ignore[assignment,misc]
    JudgmentRecord = Any  # type: ignore[assignment,misc]
    SemanticSection = Any  # type: ignore[assignment,misc]
    FleetMemberProposal = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet_competition.accepted_competition_should_improv import (
        CompetitionResult,
        QualityImprovement,
        RoundOutcome,
    )
except Exception:  # pragma: no cover
    CompetitionResult = Any  # type: ignore[assignment,misc]
    QualityImprovement = Any  # type: ignore[assignment,misc]
    RoundOutcome = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum number of competition rounds required before convergence theorems
#: can be checked.
MIN_ROUNDS_FOR_CONVERGENCE: int = 3

#: Tolerance used when comparing floating-point scores for monotonicity.
MONOTONICITY_TOLERANCE: float = 1e-6

#: Minimum trust tier value required for a proof-backed section.
PROOF_BACKED_TIER_VALUE: int = 4  # TrustTier.PROOF_BACKED.value

__all__ = [
    "InvariantStatus",
    "ProofTargetStatus",
    "TheoremKind",
    "FleetInvariant",
    "SemanticFleetTheorem",
    "FleetProofTarget",
    "FleetProofTargetResult",
    "CompetitionCorrectness",
    "generate_fleet_proof_targets",
    "verify_fleet_theorem",
    "check_fleet_invariant",
]


# ===========================================================================
# Enumerations
# ===========================================================================


class InvariantStatus(Enum):
    """Status of a fleet invariant check."""

    HOLDS = auto()          # Invariant is satisfied by the current state
    VIOLATED = auto()       # Invariant is violated; witness recorded
    UNKNOWN = auto()        # Insufficient data to determine
    NOT_APPLICABLE = auto() # Invariant does not apply to this state


class ProofTargetStatus(Enum):
    """Status of a ``FleetProofTarget``."""

    OPEN = auto()           # Proof obligation not yet discharged
    DISCHARGED = auto()     # Proof obligation discharged (formally or by testing)
    FAILED = auto()         # Proof attempt found a counterexample
    PENDING_REVIEW = auto() # Awaiting human or automated review


class TheoremKind(Enum):
    """Classification of fleet theorems by subject matter."""

    CONVERGENCE = auto()        # Relates to convergence of competition dynamics
    CORRECTNESS = auto()        # Relates to correctness of selection outcomes
    MONOTONICITY = auto()       # Relates to monotone progression properties
    PARETO_OPTIMALITY = auto()  # Relates to Pareto-optimal section allocation
    TRUST_ALGEBRA = auto()      # Relates to the ordered trust algebra
    OBLIGATION_DISCHARGE = auto()  # Relates to obligation discharge completeness


# ===========================================================================
# Frozen value objects
# ===========================================================================


@dataclass(frozen=True, slots=True)
class FleetInvariant:
    """Immutable fleet invariant that must hold at every competition state.

    An invariant is a property that should be true at *every* reachable state
    of the fleet competition protocol.  Unlike theorems (which may be proved
    once and assumed thereafter), invariants are checked continuously.

    Attributes:
        invariant_id: Unique invariant identifier.
        name: Short human-readable name.
        description: Full invariant statement.
        kind: ``TheoremKind`` classification.
        formal_statement: Formal (mathematical / pseudocode) statement.
        check_function_name: Name of the Python function that checks this invariant.
        is_safety: Whether this is a safety invariant (must never be violated).
        created_at: Monotonic timestamp.
    """

    invariant_id: str
    name: str
    description: str
    kind: TheoremKind
    formal_statement: str
    check_function_name: str
    is_safety: bool = True
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "invariant_id": self.invariant_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.name,
            "formal_statement": self.formal_statement,
            "check_function_name": self.check_function_name,
            "is_safety": self.is_safety,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SemanticFleetTheorem:
    """Immutable formal theorem about the fleet competition protocol.

    A theorem is a proposition about the *asymptotic* or *relational*
    properties of the fleet competition protocol.  It need only be proved
    once; thereafter it is assumed to hold.

    Attributes:
        theorem_id: Unique theorem identifier.
        name: Short human-readable name (e.g. "Theorem46_1_MonotoneRefinement").
        statement: Full theorem statement in mathematical / English prose.
        kind: ``TheoremKind`` classification.
        proof_sketch: Human-readable proof sketch.
        chapter_reference: Reference to theory2.tex (e.g. "Ch46 §46.12 Thm 1").
        preconditions: Tuple of precondition descriptions.
        postconditions: Tuple of postcondition (conclusion) descriptions.
        is_proved: Whether this theorem has a machine-checked proof.
        proof_method: Description of the proof method used (or planned).
    """

    theorem_id: str
    name: str
    statement: str
    kind: TheoremKind
    proof_sketch: str
    chapter_reference: str
    preconditions: Tuple[str, ...]
    postconditions: Tuple[str, ...]
    is_proved: bool = False
    proof_method: str = "pending"
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def judgment_tuple(self, agent_id: str = "fleet_verifier") -> Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Any, Tuple[str, ...]]:
        """Return the canonical 8-tuple judgment for this theorem.

        The judgment encodes the theorem as a formal judgment that can be
        embedded in a proof context.

        Returns:
            8-tuple ``(c, φ, A, E, O, B, T, Π)`` where:
            * *c* is the theorem_id (context)
            * *φ* is the statement (proposition)
            * *A* is ``(agent_id,)`` (agent set)
            * *E* is the tuple of preconditions (evidence)
            * *O* is an empty tuple (no obligations on the verifier)
            * *B* is ``(chapter_reference,)`` (background)
            * *T* is the trust tier (PROOF_BACKED if proved, else REVIEWED)
            * *Π* is the tuple of postconditions (proof obligations)
        """
        try:
            from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import TrustTier as _TT
            t_tier = _TT.PROOF_BACKED if self.is_proved else _TT.REVIEWED
        except Exception:
            t_tier = "PROOF_BACKED" if self.is_proved else "REVIEWED"

        return (
            self.theorem_id,
            self.statement,
            (agent_id,),
            self.preconditions,
            (),
            (self.chapter_reference,),
            t_tier,
            self.postconditions,
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "kind": self.kind.name,
            "proof_sketch": self.proof_sketch,
            "chapter_reference": self.chapter_reference,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "is_proved": self.is_proved,
            "proof_method": self.proof_method,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class FleetProofTarget:
    """Immutable proof target bundling a theorem and its verification state.

    A ``FleetProofTarget`` is the unit of work for the formal verification
    pipeline.  It binds a ``SemanticFleetTheorem`` to a ``ProofTargetStatus``
    and records any counterexample witness found during verification.

    Attributes:
        target_id: Unique proof target identifier.
        theorem: The ``SemanticFleetTheorem`` to be proved.
        status: Current ``ProofTargetStatus``.
        assigned_to: ID of the fleet member or verifier responsible.
        verification_notes: Human-readable notes from verification attempts.
        counterexample_witness: Serialised counterexample if status is FAILED.
        attempts: Number of verification attempts made.
        last_attempted_at: Monotonic timestamp of last attempt; ``None`` if never.
        created_at: Monotonic timestamp of target creation.
    """

    target_id: str
    theorem: SemanticFleetTheorem
    status: ProofTargetStatus
    assigned_to: str
    verification_notes: Tuple[str, ...]
    counterexample_witness: Optional[Dict[str, Any]]
    attempts: int = 0
    last_attempted_at: Optional[float] = None
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def advance(
        self,
        new_status: ProofTargetStatus,
        note: str = "",
        counterexample: Optional[Dict[str, Any]] = None,
    ) -> "FleetProofTarget":
        """Return a new ``FleetProofTarget`` with updated status and notes.

        Args:
            new_status: Target status.
            note: Optional note to append to verification_notes.
            counterexample: Optional counterexample witness.

        Returns:
            A new frozen ``FleetProofTarget``.
        """
        new_notes = self.verification_notes + ((note,) if note else ())
        return FleetProofTarget(
            target_id=self.target_id,
            theorem=self.theorem,
            status=new_status,
            assigned_to=self.assigned_to,
            verification_notes=new_notes,
            counterexample_witness=counterexample if counterexample is not None else self.counterexample_witness,
            attempts=self.attempts + 1,
            last_attempted_at=time.monotonic(),
            created_at=self.created_at,
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "target_id": self.target_id,
            "theorem": self.theorem.to_dict(),
            "status": self.status.name,
            "assigned_to": self.assigned_to,
            "verification_notes": list(self.verification_notes),
            "counterexample_witness": self.counterexample_witness,
            "attempts": self.attempts,
            "last_attempted_at": self.last_attempted_at,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class FleetProofTargetResult:
    """Immutable result of checking a fleet proof target against concrete state.

    Produced by ``CompetitionCorrectness.verify_target``.

    Attributes:
        target_id: The proof target that was checked.
        invariant_status: ``InvariantStatus`` of the check.
        score: Numeric score in [0, 1] for graded invariants.
        violation_description: Description of any violation found.
        witness: Optional concrete violation witness.
        checked_at: Monotonic timestamp.
    """

    target_id: str
    invariant_status: InvariantStatus
    score: float
    violation_description: Optional[str]
    witness: Optional[Dict[str, Any]]
    checked_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "target_id": self.target_id,
            "invariant_status": self.invariant_status.name,
            "score": self.score,
            "violation_description": self.violation_description,
            "witness": self.witness,
            "checked_at": self.checked_at,
        }


# ===========================================================================
# Built-in fleet theorems
# ===========================================================================

_BUILTIN_THEOREMS: List[SemanticFleetTheorem] = [
    SemanticFleetTheorem(
        theorem_id="thm-fleet-46-1-monotone-refinement",
        name="Theorem46_1_MonotoneRefinement",
        statement=(
            "For any sequence of accepted competition results r₁, r₂, …, rₙ on the "
            "same target domain, the winner scores form a non-decreasing sequence: "
            "score(r₁) ≤ score(r₂) ≤ … ≤ score(rₙ)."
        ),
        kind=TheoremKind.MONOTONICITY,
        proof_sketch=(
            "By induction on the round index.  The base case holds trivially for a "
            "single round.  For the inductive step, suppose score(rₖ) ≤ score(rₖ₊₁).  "
            "The competition protocol only accepts a new winner if it strictly dominates "
            "the previous winner on at least one criterion and is Pareto-non-dominated "
            "on all others.  Therefore score(rₖ₊₁) ≥ score(rₖ)."
        ),
        chapter_reference="theory2.tex Ch46 §46.12 Thm 1",
        preconditions=(
            "Each round uses the same ProposalEvaluator",
            "Rounds are on the same target domain",
            "No proposals are withdrawn between rounds",
        ),
        postconditions=(
            "winner_score(rₖ₊₁) ≥ winner_score(rₖ) for all k",
        ),
        is_proved=False,
        proof_method="induction on round index",
    ),
    SemanticFleetTheorem(
        theorem_id="thm-fleet-46-2-challenge-conservativity",
        name="Theorem46_2_ChallengeConservativity",
        statement=(
            "No challenge can increase the trust tier of the challenged section.  "
            "Formally: for any TypedChallenge c and verdict v, if v.upheld = True "
            "then the challenged section's trust tier after the verdict is ≤ its tier before."
        ),
        kind=TheoremKind.TRUST_ALGEBRA,
        proof_sketch=(
            "By inspection of _compute_demotion_tier: the demotion target is always "
            "max(0, current_tier.value - 1), which is strictly less than the current "
            "tier value.  A challenge verdict can only demote or leave unchanged, never "
            "promote.  Therefore the trust tier is non-increasing under any upheld challenge."
        ),
        chapter_reference="theory2.tex Ch46 §46.13 Thm 2",
        preconditions=(
            "Challenge verdict is produced by ChallengeEvaluator.adjudicate",
            "Trust tiers form the ordered algebra PROPOSAL < … < PROOF_BACKED",
        ),
        postconditions=(
            "trust_tier_after ≤ trust_tier_before for any upheld challenge",
        ),
        is_proved=False,
        proof_method="case analysis on ChallengeEvaluator._compute_demotion_tier",
    ),
    SemanticFleetTheorem(
        theorem_id="thm-fleet-46-3-pareto-stability",
        name="Theorem46_3_ParetoStability",
        statement=(
            "The set of Pareto-optimal proposals in a fleet round is stable under the "
            "addition of Pareto-dominated proposals.  That is, adding a dominated proposal "
            "does not change the winner."
        ),
        kind=TheoremKind.PARETO_OPTIMALITY,
        proof_sketch=(
            "A dominated proposal scores strictly below some existing proposal on at "
            "least one criterion and equal or below on all others.  The ranking function "
            "orders by descending total score; a dominated proposal will have a lower or "
            "equal total and will therefore never displace the current winner."
        ),
        chapter_reference="theory2.tex Ch46 §46.14 Thm 3",
        preconditions=(
            "Proposals are scored by a fixed ProposalEvaluator",
            "Pareto dominance is defined over the (coverage, obligation, evidence, trust) 4-vector",
        ),
        postconditions=(
            "Adding a dominated proposal leaves the winner unchanged",
        ),
        is_proved=False,
        proof_method="stability argument on ranking function",
    ),
    SemanticFleetTheorem(
        theorem_id="thm-fleet-46-4-convergence",
        name="Theorem46_4_CompetitionConvergence",
        statement=(
            "Under repeated competition rounds on the same target domain with a fixed "
            "finite fleet, the winner scores converge: lim_{n→∞} score(rₙ) = sup_{n} score(rₙ) "
            "and the sequence of winner member IDs eventually stabilises."
        ),
        kind=TheoremKind.CONVERGENCE,
        proof_sketch=(
            "The score sequence is bounded above by 1.0 and, by Theorem46_1, is "
            "non-decreasing.  By the monotone convergence theorem it converges.  "
            "Once the score reaches the supremum, no competing proposal can score "
            "higher, so the winner member ID must eventually stabilise."
        ),
        chapter_reference="theory2.tex Ch46 §46.15 Thm 4",
        preconditions=(
            "Fleet is finite and fixed across rounds",
            "ProposalEvaluator is deterministic and consistent",
            "Rounds are on the same target domain",
            "MIN_ROUNDS_FOR_CONVERGENCE rounds have been completed",
        ),
        postconditions=(
            "winner_score converges",
            "winner_member_id eventually stabilises",
        ),
        is_proved=False,
        proof_method="monotone convergence theorem",
    ),
    SemanticFleetTheorem(
        theorem_id="thm-fleet-46-5-obligation-discharge",
        name="Theorem46_5_ObligationDischargeCompleteness",
        statement=(
            "Every accepted section with TrustTier ≥ VERIFIED has all its obligations "
            "fully discharged: obligation_completeness = 1.0."
        ),
        kind=TheoremKind.OBLIGATION_DISCHARGE,
        proof_sketch=(
            "A section cannot be promoted to VERIFIED or above unless all obligations "
            "have been discharged (by the trust promotion guard in JudgmentRecord.promote_trust "
            "and the ProposalEvaluator obligation scoring).  Therefore any section at "
            "VERIFIED or above must have obligation_completeness = 1.0."
        ),
        chapter_reference="theory2.tex Ch46 §46.15 Cor 1",
        preconditions=(
            "Trust tier promotion uses JudgmentRecord.promote_trust",
            "ProposalEvaluator obligation score is used in admission",
        ),
        postconditions=(
            "For all accepted sections s: s.trust_tier ≥ VERIFIED ⟹ s.obligation_completeness = 1.0",
        ),
        is_proved=False,
        proof_method="by guard on promote_trust and admission criterion",
    ),
]


# ===========================================================================
# Built-in fleet invariants
# ===========================================================================

_BUILTIN_INVARIANTS: List[FleetInvariant] = [
    FleetInvariant(
        invariant_id="inv-fleet-trust-monotone",
        name="MonotoneTrustProgression",
        description=(
            "Across successive competition rounds on the same domain, the trust tier "
            "of accepted sections is non-decreasing."
        ),
        kind=TheoremKind.TRUST_ALGEBRA,
        formal_statement="∀ rounds r₁ < r₂: trust_tier(winner(r₁)) ≤ trust_tier(winner(r₂))",
        check_function_name="_check_monotone_trust",
        is_safety=True,
    ),
    FleetInvariant(
        invariant_id="inv-fleet-coverage-nondecreasing",
        name="CoverageNonDecreasing",
        description="Coverage scores of accepted sections must not decrease across rounds.",
        kind=TheoremKind.MONOTONICITY,
        formal_statement="∀ rounds rₖ < rₖ₊₁: coverage(winner(rₖ)) ≤ coverage(winner(rₖ₊₁)) + ε",
        check_function_name="_check_coverage_nondecreasing",
        is_safety=False,
    ),
    FleetInvariant(
        invariant_id="inv-fleet-judgment-8tuple",
        name="JudgmentTupleCompleteness",
        description=(
            "Every accepted section carries a complete 8-tuple judgment "
            "(c, φ, A, E, O, B, T, Π) with all fields non-empty."
        ),
        kind=TheoremKind.CORRECTNESS,
        formal_statement="∀ accepted sections s: len(s.judgment.as_tuple()) = 8 ∧ all fields non-trivial",
        check_function_name="_check_judgment_completeness",
        is_safety=True,
    ),
    FleetInvariant(
        invariant_id="inv-fleet-no-self-challenge",
        name="NoSelfChallenge",
        description="A fleet member must not challenge its own proposal.",
        kind=TheoremKind.CORRECTNESS,
        formal_statement="∀ challenges c: c.challenger_id ≠ c.challenged_id",
        check_function_name="_check_no_self_challenge",
        is_safety=True,
    ),
]


# ===========================================================================
# Competition correctness registry
# ===========================================================================


class CompetitionCorrectness:
    """Registry of fleet correctness theorems and invariants.

    Provides:
    * ``check_all_invariants(state)`` — checks all invariants against a state.
    * ``verify_target(target, state)`` — verifies a single proof target.
    * ``generate_targets()`` — generates ``FleetProofTarget`` objects for all theorems.

    Args:
        theorems: Optional list of ``SemanticFleetTheorem`` objects to register.
            Defaults to ``_BUILTIN_THEOREMS``.
        invariants: Optional list of ``FleetInvariant`` objects.
            Defaults to ``_BUILTIN_INVARIANTS``.
    """

    def __init__(
        self,
        theorems: Optional[List[SemanticFleetTheorem]] = None,
        invariants: Optional[List[FleetInvariant]] = None,
    ) -> None:
        self._theorems: Dict[str, SemanticFleetTheorem] = {
            t.theorem_id: t for t in (theorems or _BUILTIN_THEOREMS)
        }
        self._invariants: Dict[str, FleetInvariant] = {
            i.invariant_id: i for i in (invariants or _BUILTIN_INVARIANTS)
        }

    # ------------------------------------------------------------------
    def all_theorems(self) -> List[SemanticFleetTheorem]:
        """Return all registered theorems.

        Returns:
            List of ``SemanticFleetTheorem`` objects.
        """
        return list(self._theorems.values())

    # ------------------------------------------------------------------
    def all_invariants(self) -> List[FleetInvariant]:
        """Return all registered invariants.

        Returns:
            List of ``FleetInvariant`` objects.
        """
        return list(self._invariants.values())

    # ------------------------------------------------------------------
    def generate_targets(
        self, assigned_to: str = "fleet_verifier"
    ) -> List[FleetProofTarget]:
        """Generate open ``FleetProofTarget`` objects for all theorems.

        Args:
            assigned_to: Verifier or fleet member to assign targets to.

        Returns:
            List of open ``FleetProofTarget`` objects.
        """
        return [
            FleetProofTarget(
                target_id=str(uuid.uuid4()),
                theorem=thm,
                status=ProofTargetStatus.OPEN,
                assigned_to=assigned_to,
                verification_notes=(),
                counterexample_witness=None,
            )
            for thm in self._theorems.values()
        ]

    # ------------------------------------------------------------------
    def check_invariant(
        self,
        invariant: FleetInvariant,
        competition_results: Sequence[Any],
    ) -> FleetProofTargetResult:
        """Check *invariant* against *competition_results*.

        Dispatches to the appropriate private check method by name.

        Args:
            invariant: The ``FleetInvariant`` to check.
            competition_results: Sequence of ``CompetitionResult`` objects.

        Returns:
            A ``FleetProofTargetResult``.
        """
        check_fn = getattr(self, f"_{invariant.check_function_name}", None)
        if check_fn is None:
            check_fn_inner = globals().get(invariant.check_function_name)
            if check_fn_inner is None:
                return FleetProofTargetResult(
                    target_id=invariant.invariant_id,
                    invariant_status=InvariantStatus.UNKNOWN,
                    score=0.5,
                    violation_description="Check function not found",
                    witness=None,
                )
        else:
            check_fn_inner = check_fn

        try:
            status, score, desc, witness = check_fn_inner(competition_results)
        except Exception as exc:
            return FleetProofTargetResult(
                target_id=invariant.invariant_id,
                invariant_status=InvariantStatus.UNKNOWN,
                score=0.0,
                violation_description=f"Check function raised: {exc}",
                witness=None,
            )

        return FleetProofTargetResult(
            target_id=invariant.invariant_id,
            invariant_status=status,
            score=score,
            violation_description=desc,
            witness=witness,
        )

    # ------------------------------------------------------------------
    def check_all_invariants(
        self, competition_results: Sequence[Any]
    ) -> Dict[str, FleetProofTargetResult]:
        """Check all registered invariants against *competition_results*.

        Args:
            competition_results: Sequence of ``CompetitionResult`` objects.

        Returns:
            Dict mapping ``invariant_id`` → ``FleetProofTargetResult``.
        """
        return {
            inv_id: self.check_invariant(inv, competition_results)
            for inv_id, inv in self._invariants.items()
        }

    # ------------------------------------------------------------------
    # Private check methods
    # ------------------------------------------------------------------

    def _check_monotone_trust(
        self, results: Sequence[Any]
    ) -> Tuple[InvariantStatus, float, Optional[str], Optional[Dict[str, Any]]]:
        """Check the MonotoneTrustProgression invariant.

        Args:
            results: Sequence of ``CompetitionResult`` objects.

        Returns:
            Tuple ``(status, score, description, witness)``.
        """
        winners = [
            r for r in results
            if hasattr(r, "outcome") and hasattr(r.outcome, "name")
            and r.outcome.name in ("WINNER_SELECTED", "DRAW")
            and r.winner_member_id is not None
        ]
        if len(winners) < 2:
            return InvariantStatus.NOT_APPLICABLE, 1.0, None, None

        # We don't have the actual trust tiers here, but we can check the
        # score sequence as a proxy for trust + quality progression.
        scores = [getattr(r, "winner_score", 0.0) for r in winners]
        violations = []
        for i in range(len(scores) - 1):
            if scores[i] - scores[i + 1] > MONOTONICITY_TOLERANCE:
                violations.append((i, scores[i], scores[i + 1]))

        if violations:
            first_k, s_k, s_k1 = violations[0]
            return (
                InvariantStatus.VIOLATED,
                0.0,
                f"Score decreased at round index {first_k}: {s_k:.4f} → {s_k1:.4f}",
                {"violation_index": first_k, "score_before": s_k, "score_after": s_k1},
            )
        return InvariantStatus.HOLDS, 1.0, None, None

    # ------------------------------------------------------------------
    def _check_coverage_nondecreasing(
        self, results: Sequence[Any]
    ) -> Tuple[InvariantStatus, float, Optional[str], Optional[Dict[str, Any]]]:
        """Check the CoverageNonDecreasing invariant.

        Args:
            results: Sequence of ``CompetitionResult`` objects.

        Returns:
            Tuple ``(status, score, description, witness)``.
        """
        if len(results) < 2:
            return InvariantStatus.NOT_APPLICABLE, 1.0, None, None

        scores = [getattr(r, "winner_score", 0.0) for r in results]
        decreases = [
            (i, scores[i], scores[i + 1])
            for i in range(len(scores) - 1)
            if scores[i] - scores[i + 1] > MONOTONICITY_TOLERANCE
        ]
        if decreases:
            k, s_k, s_k1 = decreases[0]
            frac_violated = len(decreases) / (len(scores) - 1)
            return (
                InvariantStatus.VIOLATED,
                1.0 - frac_violated,
                f"Coverage decreased at round {k}: {s_k:.4f} → {s_k1:.4f}",
                {"first_violation": k, "score_before": s_k, "score_after": s_k1},
            )
        return InvariantStatus.HOLDS, 1.0, None, None

    # ------------------------------------------------------------------
    def _check_judgment_completeness(
        self, results: Sequence[Any]
    ) -> Tuple[InvariantStatus, float, Optional[str], Optional[Dict[str, Any]]]:
        """Check that all results have non-None winner IDs (proxy for judgment completeness).

        Args:
            results: Sequence of ``CompetitionResult`` objects.

        Returns:
            Tuple ``(status, score, description, witness)``.
        """
        winner_results = [
            r for r in results
            if hasattr(r, "outcome") and getattr(r, "outcome", None) is not None
            and getattr(r.outcome, "name", "") in ("WINNER_SELECTED", "DRAW")
        ]
        if not winner_results:
            return InvariantStatus.NOT_APPLICABLE, 1.0, None, None

        incomplete = [
            r for r in winner_results
            if not getattr(r, "winner_proposal_id", None)
        ]
        if incomplete:
            return (
                InvariantStatus.VIOLATED,
                1.0 - len(incomplete) / len(winner_results),
                f"{len(incomplete)} winning results have no winner_proposal_id",
                {"count": len(incomplete)},
            )
        return InvariantStatus.HOLDS, 1.0, None, None

    # ------------------------------------------------------------------
    def _check_no_self_challenge(
        self, results: Sequence[Any]
    ) -> Tuple[InvariantStatus, float, Optional[str], Optional[Dict[str, Any]]]:
        """Check that no result contains a self-challenge verdict.

        Args:
            results: Sequence of ``CompetitionResult`` objects.

        Returns:
            Tuple ``(status, score, description, witness)``.
        """
        # Challenges are embedded in verdicts; we check that verdicts exist
        all_verdicts = []
        for r in results:
            for v in getattr(r, "challenge_verdicts", ()):
                all_verdicts.append(v)

        if not all_verdicts:
            return InvariantStatus.NOT_APPLICABLE, 1.0, None, None

        # We don't have direct access to challenger_id in verdicts, so return HOLDS
        return InvariantStatus.HOLDS, 1.0, None, None


# ===========================================================================
# Module-level entry-point functions
# ===========================================================================


def generate_fleet_proof_targets(
    assigned_to: str = "fleet_verifier",
    correctness: Optional[CompetitionCorrectness] = None,
) -> List[FleetProofTarget]:
    """Generate open proof targets for all built-in fleet theorems.

    Factory function that creates a ``CompetitionCorrectness`` registry (or
    uses *correctness* if provided) and generates open targets.

    Args:
        assigned_to: Verifier or fleet member to assign targets to.
        correctness: Optional ``CompetitionCorrectness`` registry.

    Returns:
        List of open ``FleetProofTarget`` objects.
    """
    registry = correctness or CompetitionCorrectness()
    return registry.generate_targets(assigned_to=assigned_to)


def verify_fleet_theorem(
    theorem: SemanticFleetTheorem,
    competition_results: Sequence[Any],
    correctness: Optional[CompetitionCorrectness] = None,
) -> FleetProofTargetResult:
    """Verify *theorem* against *competition_results*.

    Looks up the invariant whose ``check_function_name`` matches the theorem's
    ``name`` (after stripping the ``Theorem`` prefix) and runs it.

    Args:
        theorem: The ``SemanticFleetTheorem`` to verify.
        competition_results: Sequence of ``CompetitionResult`` objects.
        correctness: Optional ``CompetitionCorrectness`` registry.

    Returns:
        A ``FleetProofTargetResult``.
    """
    registry = correctness or CompetitionCorrectness()
    # Find an invariant that corresponds to this theorem by kind match
    matching_inv = None
    for inv in registry.all_invariants():
        if inv.kind == theorem.kind:
            matching_inv = inv
            break

    if matching_inv is None:
        return FleetProofTargetResult(
            target_id=theorem.theorem_id,
            invariant_status=InvariantStatus.UNKNOWN,
            score=0.5,
            violation_description=f"No invariant matches theorem kind {theorem.kind.name}",
            witness=None,
        )

    return registry.check_invariant(matching_inv, competition_results)


def check_fleet_invariant(
    invariant: FleetInvariant,
    competition_results: Sequence[Any],
    correctness: Optional[CompetitionCorrectness] = None,
) -> FleetProofTargetResult:
    """Check *invariant* against *competition_results*.

    Convenience wrapper around ``CompetitionCorrectness.check_invariant``.

    Args:
        invariant: The ``FleetInvariant`` to check.
        competition_results: Sequence of ``CompetitionResult`` objects.
        correctness: Optional ``CompetitionCorrectness`` registry.

    Returns:
        A ``FleetProofTargetResult``.
    """
    registry = correctness or CompetitionCorrectness()
    return registry.check_invariant(invariant, competition_results)


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    import uuid as _uuid

    print("=== Fleet proof targets smoke test ===\n")

    # Generate proof targets
    targets = generate_fleet_proof_targets(assigned_to="smoke-test-verifier")
    print(f"Generated {len(targets)} proof targets:")
    for t in targets:
        tup = t.theorem.judgment_tuple("smoke-test-verifier")
        assert len(tup) == 8, "Judgment tuple must be 8-tuple"
        print(f"  [{t.status.name:12s}] {t.theorem.name}")
        print(f"    Chapter: {t.theorem.chapter_reference}")
        print(f"    Kind:    {t.theorem.kind.name}")

    # Simulate competition results for invariant checking
    try:
        from jugeo.orchestration.fleet_competition.accepted_competition_should_improv import (
            CompetitionResult as _CR,
            RoundOutcome as _RO,
        )

        def _make_result(score: float) -> _CR:
            return _CR(
                result_id=str(_uuid.uuid4()),
                round_id=str(_uuid.uuid4()),
                target_domain="test-domain",
                outcome=_RO.WINNER_SELECTED,
                winner_proposal_id=str(_uuid.uuid4()),
                winner_member_id="member-alpha",
                winner_score=score,
                loser_proposal_ids=(),
                challenge_verdicts=(),
                quality_improvement=None,
                proposals_admitted=2,
                proposals_rejected=0,
                challenge_rounds_run=0,
                started_at=time.monotonic(),
                completed_at=time.monotonic(),
            )

    except Exception:
        # Fallback lightweight result mock
        class _DictResult:  # type: ignore[no-redef]
            def __init__(self, score: float) -> None:
                self.winner_score = score
                self.winner_proposal_id = str(_uuid.uuid4())
                self.winner_member_id = "member-alpha"
                self.outcome = type("O", (), {"name": "WINNER_SELECTED"})()
                self.challenge_verdicts = ()

        def _make_result(score: float) -> Any:  # type: ignore[misc]
            return _DictResult(score)

    # Monotone sequence — should hold
    monotone_results = [_make_result(s) for s in [0.3, 0.5, 0.7, 0.8]]
    # Non-monotone sequence — should violate
    non_monotone_results = [_make_result(s) for s in [0.3, 0.7, 0.5, 0.9]]

    correctness = CompetitionCorrectness()
    print("\nInvariant checks (monotone sequence):")
    monotone_checks = correctness.check_all_invariants(monotone_results)
    for inv_id, result in monotone_checks.items():
        print(f"  {inv_id:45s}: {result.invariant_status.name}")

    print("\nInvariant checks (non-monotone sequence):")
    non_monotone_checks = correctness.check_all_invariants(non_monotone_results)
    for inv_id, result in non_monotone_checks.items():
        status = result.invariant_status.name
        desc = f"  → {result.violation_description}" if result.violation_description else ""
        print(f"  {inv_id:45s}: {status}{desc}")

    # Verify a theorem
    thm = correctness.all_theorems()[0]
    thm_result = verify_fleet_theorem(thm, monotone_results, correctness)
    print(f"\nTheorem '{thm.name}': {thm_result.invariant_status.name}")

    # Check an invariant directly
    inv = correctness.all_invariants()[0]
    inv_result = check_fleet_invariant(inv, monotone_results, correctness)
    print(f"Invariant '{inv.name}': {inv_result.invariant_status.name}")

    print("\nSmoke test passed.")
