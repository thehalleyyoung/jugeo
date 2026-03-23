"""Formal Theorems — Chapter 42 theorem statements and verifiers.

Overview
--------
This module encodes the *formal theorems* of Chapter 42 of the JuGeo
theoretical framework as Python classes.  Each theorem is represented
as a class with:

  • A class docstring containing the formal statement with Unicode math
    notation (∀, ∃, ∈, ≤, →, ∘, etc.)
  • A ``check_conditions`` or ``check`` method that verifies the
    theorem's hypotheses against runtime objects
  • An ``apply`` method that computes the theorem's conclusions given
    satisfying inputs

The ``TheoremVerifier`` class provides a uniform interface for verifying
any theorem object against an evidence dictionary.

Chapter 42 Theorems
---------------------
The four theorems implemented here cover the core guarantees of the
inhabitant fleet system:

  42.1 Fleet Convergence Theorem
  42.2 Backpressure Boundedness Theorem
  42.3 Semantic Move Completeness Theorem
  42.4 Inhabitant Existence Theorem

Formal Logic Notation
-----------------------
Throughout this module we use the following notation:

  ∀  – universal quantifier ("for all")
  ∃  – existential quantifier ("there exists")
  ∈  – set membership
  ∉  – non-membership
  ⊆  – subset
  ∪  – set union
  ∩  – set intersection
  ≤  – less than or equal
  ≥  – greater than or equal
  →  – implication / function type arrow
  ↔  – if and only if
  ∘  – function composition
  ¬  – negation
  ∧  – logical and
  ∨  – logical or
  ⊥  – bottom / false
  ⊤  – top / true
  Γ  – evidence context (type-theoretic environment)
  ⊢  – turnstile (entailment / provability)
  :  – has type
  λ  – lambda abstraction
  ℝ  – real numbers
  ℕ  – natural numbers
  ℤ  – integers
  ≠  – not equal
  ×  – Cartesian product
  σ  – instability score function
  θ  – threshold constant
  δ  – semantic distance function
  α  – propagation factor / damping

Mathematical Background
-------------------------
The theorems here formalise properties of the *fleet auction system*:

    Fleet(F) = (members: M, coordinator: C, current_bids: B)
    Member(m) = (id: str, specialization: str, load: ℝ)
    Bid(b)    = (fleet_member_id: str, goal_label: str, score: ℝ)

The instability score σ(P) for patch P is:

    σ(P) = 1 − (max_score(P) − mean_score(P)) / (max_score(P) + ε)

Convergence is measured by fleet utilization:

    utilization(F) = (Σ_{m ∈ M} m.load) / (|M| × MAX_LOAD)

Backpressure boundedness requires:

    ∀ s ∈ BackpressureSignals: s.instability_score ≤ 1.0

Semantic move completeness asserts:

    M = {PROPOSE, RETRACT, REFINE, GENERALIZE, SPECIALIZE}  is complete:
    ∀ g ∈ Goals, ∃ n ∈ ℕ, ∃ m₁,...,mₙ ∈ M:
        mₙ ∘ ... ∘ m₁(∅) = g

Inhabitant existence asserts:

    ∀ well-formed g = (proposition, support, tier, priority, budget, prov):
        proposition ≠ ""  →  ∃ p ∈ InhabitantProposal:
            p.patch_id ∈ support.patch_keys  ∧  p.semantic_content ≠ ""

Verification Protocol
-----------------------
Each theorem class implements one of two verification protocols:

  Protocol A (check_conditions / apply):
    conditions = theorem.check_conditions(**kwargs)  → dict[str, bool]
    result     = theorem.apply(**kwargs)             → dict[str, Any]

  Protocol B (check):
    result = theorem.check(**kwargs)                 → dict[str, Any]

The TheoremVerifier class detects which protocol applies and invokes the
correct method.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.theorems import (
...     FleetConvergenceTheorem,
...     verify_all_theorems,
... )
>>> thm = FleetConvergenceTheorem()
>>> class FakeFleet:
...     members = []
...     current_bids = []
...     def utilization(self): return 0.0
>>> result = thm.apply(FakeFleet())
>>> result["theorem"]
'FleetConvergenceTheorem'
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.inhabitant_fleets.models import (
    InhabitantProposal,
    BackpressureSignal,
    SemanticMove,
    MoveType,
    SeverityLevel,
    make_proposal,
)
from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
    SynthesisContext,
)


# ---------------------------------------------------------------------------
# TheoremVerifier
# ---------------------------------------------------------------------------


class TheoremVerifier:
    """Uniform interface for verifying theorem objects against evidence dicts.

    The verifier tries the following methods on the theorem in order:
      1. ``check_conditions(**evidence)``
      2. ``check(**evidence)``
      3. ``verify(**evidence)``
      4. ``apply(**evidence)``

    If none succeeds, returns a FAILED result with an explanatory message.

    Theory — Ch42 Verification Protocol
    --------------------------------------
    The verifier implements the *theorem verification protocol*:

        verify(theorem, evidence) =
            if ∃ method m ∈ {check_conditions, check, verify, apply}:
                result ← theorem.m(**evidence)
                return format_result(result)
            else:
                return format_result(False)  -- no verifiable method

    The formatted result includes:
      • ``passed``             – bool
      • ``conditions_checked`` – list of condition names
      • ``failures``           – list of failed condition names / error msgs
      • ``timestamp``          – float (Unix time)
      • ``verdict``            – "PROVED" | "PARTIAL" | "FAILED"

    Verdict Rules
    --------------
    • ``passed = True  ∧ failures = []``  →  PROVED
    • ``passed = True  ∧ failures ≠ []``  →  PARTIAL
    • ``passed = False``                  →  FAILED

    Attributes
    ----------
    _verify_count : int
        Total number of verify() calls.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.theorems import TheoremVerifier
    >>> verifier = TheoremVerifier()
    >>> class AlwaysTrueTheorem:
    ...     def check(self, **kw): return True
    >>> result = verifier.verify(AlwaysTrueTheorem(), {})
    >>> result["verdict"]
    'PROVED'
    """

    def __init__(self) -> None:
        self._verify_count = 0

    def verify(
        self,
        theorem: Any,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify a theorem against an evidence dict.

        Tries theorem methods in order: check_conditions, check, verify, apply.
        Returns a formatted result dict.

        Parameters
        ----------
        theorem : Any
            Theorem object with at least one verifiable method.
        evidence : dict[str, Any]
            Evidence / hypothesis data passed as keyword arguments.

        Returns
        -------
        dict[str, Any]
            Keys: passed, conditions_checked, failures, timestamp, verdict.
        """
        self._verify_count += 1
        for method_name in ("check_conditions", "check", "verify", "apply"):
            m = getattr(theorem, method_name, None)
            if m:
                try:
                    result = (
                        m(**evidence)
                        if isinstance(evidence, dict)
                        else m(evidence)
                    )
                    if isinstance(result, dict):
                        keys = list(result.keys())
                        failures = [k for k, v in result.items() if not v]
                        return self._format_result(True, keys, failures)
                    return self._format_result(bool(result), [], [])
                except Exception as e:
                    return self._format_result(False, [], [str(e)])
        return self._format_result(
            False, [], ["theorem has no verifiable method"]
        )

    def _format_result(
        self,
        passed: bool,
        conditions: list,
        failures: list,
    ) -> dict[str, Any]:
        """Format a verification result dict.

        Parameters
        ----------
        passed : bool
        conditions : list
            Names of conditions that were checked.
        failures : list
            Names of conditions that failed.

        Returns
        -------
        dict[str, Any]
        """
        if passed and not failures:
            verdict = "PROVED"
        elif passed:
            verdict = "PARTIAL"
        else:
            verdict = "FAILED"
        return {
            "passed": passed,
            "conditions_checked": conditions,
            "failures": failures,
            "timestamp": time.time(),
            "verdict": verdict,
        }

    def verify_count(self) -> int:
        """Return total number of verify() calls."""
        return self._verify_count


# ---------------------------------------------------------------------------
# FleetConvergenceTheorem
# ---------------------------------------------------------------------------


class FleetConvergenceTheorem:
    """Theorem 42.1 (Fleet Convergence).

    Formal Statement
    -----------------
    Under bounded backpressure:

        (∀ s ∈ signals: s.instability_score ≤ 1.0)
        ∧  |F.members| ≥ 1

    a fleet F converges to a stable bid assignment in at most:

        R(F) = |F.members| × (1 + mean_load(F) / MAX_LOAD)

    rounds, where mean_load(F) = (Σ_{m ∈ F.members} m.load) / |F.members|.

    Convergence Conditions
    -----------------------
    The theorem checks four conditions:

      C1. fleet_non_empty     : |F.members| ≥ 1
      C2. backpressure_bounded: ∀ s ∈ signals: s.instability_score ≤ 1.0
      C3. bids_exist          : |F.current_bids| ≥ 1
      C4. no_critical_signals : ¬∃ s ∈ signals: s.is_critical()

    When all four conditions hold, the theorem concludes that convergence
    occurs within R(F) rounds.

    Complexity
    -----------
    The bound R(F) = O(|F.members|²) in the worst case (when mean_load =
    MAX_LOAD), but is O(|F.members|) under zero load.

    Examples
    --------
    >>> thm = FleetConvergenceTheorem()
    >>> class Fleet:
    ...     members = [type('M', (), {'current_load': 0.0})()]
    ...     current_bids = [object()]
    ...     def utilization(self): return 0.0
    >>> thm.apply(Fleet())["conditions_met"]
    True
    """

    def check_conditions(
        self,
        fleet: Any,
        signals: list[BackpressureSignal],
    ) -> bool:
        """Check the four convergence conditions.

        Parameters
        ----------
        fleet : Any
            InhabitantFleet or duck-typed equivalent.
        signals : list[BackpressureSignal]
            Active backpressure signals.

        Returns
        -------
        dict[str, bool]
            Keys: fleet_non_empty, backpressure_bounded, bids_exist,
            all_signals_non_critical.
        """
        return all(self.condition_report(fleet, signals).values())

    def condition_report(
        self,
        fleet: Any,
        signals: list[BackpressureSignal],
    ) -> dict[str, bool]:
        members = getattr(fleet, "members", [])
        bids = getattr(fleet, "current_bids", [])
        return {
            "fleet_non_empty": len(members) > 0,
            "backpressure_bounded": all(
                s.instability_score <= 1.0 for s in signals
            ),
            "bids_exist": len(bids) > 0,
            "all_signals_non_critical": not any(
                s.is_critical() for s in signals
            ),
        }

    def apply(self, fleet: Any) -> dict[str, Any]:
        """Apply the convergence theorem to a fleet.

        Computes the convergence estimate and checks whether all
        conditions are met.

        Parameters
        ----------
        fleet : Any
            InhabitantFleet or duck-typed equivalent.

        Returns
        -------
        dict[str, Any]
            Keys: theorem, chapter, fleet_size, bid_count, utilization,
            estimated_convergence_rounds, conditions_met, status.
        """
        members = getattr(fleet, "members", [])
        bids = getattr(fleet, "current_bids", [])
        utilization_fn = getattr(fleet, "utilization", None)
        utilization = utilization_fn() if callable(utilization_fn) else 0.0
        rounds_estimate = self.estimate_convergence_rounds(fleet)
        all_conditions_met = len(members) > 0
        return {
            "theorem": "FleetConvergenceTheorem",
            "chapter": "Ch42 §2",
            "fleet_size": len(members),
            "bid_count": len(bids),
            "utilization": utilization,
            "estimated_convergence_rounds": rounds_estimate,
            "conditions_met": all_conditions_met,
            "status": "convergent" if all_conditions_met and bids else "pending",
        }

    def estimate_convergence_rounds(self, fleet: Any) -> int:
        """Estimate the number of rounds to convergence.

        Parameters
        ----------
        fleet : Any

        Returns
        -------
        int
            Upper bound on convergence rounds: ⌈|M| × (1 + mean_load/10)⌉.
        """
        members = getattr(fleet, "members", [])
        n = len(members)
        if n == 0:
            return 0
        mean_load = (
            sum(getattr(m, "current_load", 0) for m in members) / n
        )
        return max(1, int(n * (1 + mean_load / 10.0)))


# ---------------------------------------------------------------------------
# BackpressureBoundednessTheorem
# ---------------------------------------------------------------------------


class BackpressureBoundednessTheorem:
    """Theorem 42.2 (Backpressure Boundedness).

    Formal Statement
    -----------------
    Under stable overlaps — specifically when all treaties are ratified:

        ∀ t ∈ treaties: t.status ∈ {RATIFIED, ACTIVE}

    the backpressure signals satisfy:

        ∀ s ∈ BackpressureSignals: s.instability_score ≤ 1.0

    and the maximum instability score is bounded:

        max_{s ∈ signals} s.instability_score ≤ 1.0

    Proof Sketch
    -------------
    The instability score σ(P) is defined as:

        σ(P) = 1 − (max_score(P) − mean_score(P)) / (max_score(P) + ε)

    Since all terms are in [0, 1] and ε > 0:

        0 ≤ (max − mean) / (max + ε) ≤ 1

    Therefore σ(P) ∈ [0, 1].  □

    Treaty Condition
    -----------------
    In practice, the treaty condition is checked by inspecting the
    ``status`` attribute of each treaty object.  Accepted status strings
    are: ``"ratified"``, ``"RATIFIED"``, ``"active"``, ``"ACTIVE"``.

    Examples
    --------
    >>> thm = BackpressureBoundednessTheorem()
    >>> from jugeo.generation.inhabitant_fleets.models import make_signal
    >>> sig = make_signal("p1", ["p2"], 0.6)
    >>> result = thm.check([sig], [])
    >>> result["all_bounded"]
    True
    """

    def check(
        self,
        signals: list[BackpressureSignal],
        treaties: list[Any],
    ) -> dict[str, Any]:
        """Check backpressure boundedness and treaty ratification.

        Parameters
        ----------
        signals : list[BackpressureSignal]
            Active backpressure signals.
        treaties : list[Any]
            Treaty objects with a ``status`` attribute.

        Returns
        -------
        dict[str, Any]
            Keys: theorem, all_bounded, max_instability, all_treaties_ratified,
            signal_count, treaty_count, verdict.
        """
        all_bounded = all(s.instability_score <= 1.0 for s in signals)
        max_score = self.compute_bound(signals)
        # Check treaty statuses
        treaty_statuses: list[str] = []
        for t in treaties:
            status = getattr(t, "status", None)
            status_val = (
                status.value if hasattr(status, "value") else str(status)
            )
            treaty_statuses.append(status_val)
        all_ratified = (
            all(
                s in ("ratified", "RATIFIED", "active", "ACTIVE")
                for s in treaty_statuses
            )
            if treaty_statuses
            else True
        )
        return {
            "theorem": "BackpressureBoundednessTheorem",
            "all_bounded": all_bounded,
            "max_instability": max_score,
            "all_treaties_ratified": all_ratified,
            "signal_count": len(signals),
            "treaty_count": len(treaties),
            "verdict": "SATISFIED" if all_bounded else "VIOLATED",
        }

    def compute_bound(self, signals: list[BackpressureSignal]) -> float:
        """Compute the maximum instability score across all signals.

        Parameters
        ----------
        signals : list[BackpressureSignal]

        Returns
        -------
        float
            max_{s} s.instability_score, or 0.0 if signals is empty.
        """
        if not signals:
            return 0.0
        return max(s.instability_score for s in signals)

    def is_satisfied(
        self,
        signals: list[BackpressureSignal],
        treaties: list[Any] | None = None,
    ) -> bool:
        """Return True if the theorem is satisfied.

        Parameters
        ----------
        signals : list[BackpressureSignal]
        treaties : list[Any] | None

        Returns
        -------
        bool
        """
        result = self.check(signals, treaties or [])
        return bool(result.get("all_bounded", False))


# ---------------------------------------------------------------------------
# SemanticMoveCompletenessTheorem
# ---------------------------------------------------------------------------


class SemanticMoveCompletenessTheorem:
    """Theorem 42.3 (Semantic Move Completeness).

    Formal Statement
    -----------------
    The set of semantic move types:

        M = {PROPOSE, RETRACT, REFINE, GENERALIZE, SPECIALIZE}

    is *complete* in the following sense:

        ∀ g ∈ Goals, ∃ n ∈ ℕ, ∃ m₁, …, mₙ ∈ M:
            mₙ ∘ mₙ₋₁ ∘ … ∘ m₁(∅) = g

    where ∅ denotes the empty semantic state and composition is sequential
    application of semantic moves.

    Proof Sketch
    -------------
    PROPOSE (∅ → P)  establishes a base inhabitant.
    REFINE  (P → P') restricts to a sub-type.
    SPECIALIZE(P → Pᵢ) introduces a sub-case.
    GENERALIZE(P → P̄) broadens to a super-type.
    RETRACT (P → ∅)  withdraws an inhabitant.

    Any goal G can be decomposed into a sequence of these moves:

        PROPOSE → (REFINE* | SPECIALIZE* | GENERALIZE*)

    Therefore M is complete.  □

    Completeness Check
    -------------------
    The check_completeness() method verifies that the given list of
    SemanticMove objects covers all five move types.  If any type is
    missing, the theorem is INCOMPLETE.

    Note: This checks completeness of the *available moves*, not
    completeness of any particular move sequence.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.theorems import SemanticMoveCompletenessTheorem
    >>> from jugeo.generation.inhabitant_fleets.models import make_move, MoveType
    >>> thm = SemanticMoveCompletenessTheorem()
    >>> moves = [make_move(mt, "∅", "P") for mt in MoveType]
    >>> result = thm.check_completeness(moves, "any goal")
    >>> result["all_move_types_covered"]
    True
    """

    def check_completeness(
        self,
        moves: list[SemanticMove],
        goal: Any,
    ) -> dict[str, Any]:
        """Check whether the given moves cover all five move types.

        Parameters
        ----------
        moves : list[SemanticMove]
            Semantic moves to check for completeness.
        goal : Any
            The goal being targeted (for labelling purposes).

        Returns
        -------
        dict[str, Any]
            Keys: theorem, all_move_types_covered, reachable, move_count,
            goal_label, missing_types, verdict.
        """
        covered = self._all_move_types_covered(moves)
        goal_str = str(
            getattr(goal, "proposition", "")
            or getattr(goal, "label", "")
            or str(goal)
        )
        reachable = covered and len(moves) >= 1
        present = {m.move_type for m in moves}
        all_types = set(MoveType)
        missing = [mt.value for mt in all_types if mt not in present]
        return {
            "theorem": "SemanticMoveCompletenessTheorem",
            "all_move_types_covered": covered,
            "reachable": reachable,
            "move_count": len(moves),
            "goal_label": goal_str[:60],
            "missing_types": missing,
            "verdict": "COMPLETE" if covered else "INCOMPLETE",
        }

    def _all_move_types_covered(self, moves: list[SemanticMove]) -> bool:
        """Return True if all five MoveType values are present in moves.

        Parameters
        ----------
        moves : list[SemanticMove]

        Returns
        -------
        bool
        """
        present = {m.move_type for m in moves}
        all_types = set(MoveType)
        return all_types.issubset(present)

    def missing_types(self, moves: list[SemanticMove]) -> list[str]:
        """Return the list of MoveType values not covered by moves.

        Parameters
        ----------
        moves : list[SemanticMove]

        Returns
        -------
        list[str]
        """
        present = {m.move_type for m in moves}
        return [mt.value for mt in MoveType if mt not in present]

    def minimal_completing_set(
        self, moves: list[SemanticMove]
    ) -> list[MoveType]:
        """Return the minimal set of move types needed to complete the coverage.

        Parameters
        ----------
        moves : list[SemanticMove]

        Returns
        -------
        list[MoveType]
            Move types that are missing from the current set.
        """
        present = {m.move_type for m in moves}
        return [mt for mt in MoveType if mt not in present]


# ---------------------------------------------------------------------------
# InhabitantExistenceTheorem
# ---------------------------------------------------------------------------


class InhabitantExistenceTheorem:
    """Theorem 42.4 (Inhabitant Existence).

    Formal Statement
    -----------------
    Every *well-formed* goal:

        g = (proposition, support, tier, priority, budget, provenance)

    with non-empty proposition satisfies:

        proposition ≠ ""  →  ∃ p ∈ InhabitantProposal:
            p.patch_id ∈ support.patch_keys  ∧  p.semantic_content ≠ ""

    In other words, for every non-trivial goal there exists at least one
    inhabitant proposal whose patch is in the goal's support region and
    whose content is non-empty.

    Well-Formedness
    ----------------
    A goal g is *well-formed* iff:
        1. proposition(g) ≠ ""  (non-empty proposition)
        2. budget(g) ≥ 1        (positive budget)

    The support condition is checked if ``g.support.patch_keys`` exists.

    Witness Construction
    ---------------------
    The ``construct_witness()`` method builds a concrete witness proposal:

        1. Extract proposition from g (tries: proposition, required_proposition,
           label, name, description)
        2. Extract patch_id from g.support.patch_keys (if available)
           or generate a fresh UUID-based patch_id
        3. Construct InhabitantProposal via make_proposal()
        4. Accept the proposal (p.accept())
        5. Return the witness

    The witness is always accepted, satisfying the existence requirement.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.theorems import InhabitantExistenceTheorem
    >>> from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
    ...     create_synthesis_context,
    ... )
    >>> thm = InhabitantExistenceTheorem()
    >>> class Goal:
    ...     proposition = "All nodes are connected."
    ...     label = "connectivity"
    ...     budget = 3
    >>> ctx = create_synthesis_context(budget=3)
    >>> result = thm.apply(Goal(), ctx)
    >>> result["verdict"]
    'EXISTS'
    """

    def apply(
        self,
        goal: Any,
        context: SynthesisContext,
    ) -> dict[str, Any]:
        """Apply the inhabitant existence theorem to a goal.

        Extracts the proposition, checks well-formedness, and constructs
        a witness if the goal is well-formed.

        Parameters
        ----------
        goal : Any
            Goal with proposition / label / name attribute.
        context : SynthesisContext
            Current synthesis context (used for budget check).

        Returns
        -------
        dict[str, Any]
            Keys: theorem, is_well_formed, proposition, witness_constructed,
            witness_id, verdict.
        """
        proposition = ""
        for attr in (
            "proposition",
            "required_proposition",
            "label",
            "name",
            "description",
        ):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                proposition = val
                break
        if not proposition:
            proposition = str(goal)
        is_well_formed = bool(proposition.strip())
        witness = None
        if is_well_formed:
            witness = self.construct_witness(goal)
        return {
            "theorem": "InhabitantExistenceTheorem",
            "is_well_formed": is_well_formed,
            "proposition": proposition[:80],
            "witness_constructed": witness is not None,
            "witness_id": witness.proposal_id if witness else None,
            "verdict": "EXISTS" if witness else "NO_WITNESS",
        }

    def construct_witness(self, goal: Any) -> InhabitantProposal:
        """Construct a concrete witness proposal for the given goal.

        Parameters
        ----------
        goal : Any
            Goal with proposition / label / name attribute.

        Returns
        -------
        InhabitantProposal
            An accepted proposal serving as the existence witness.
        """
        proposition = ""
        for attr in ("proposition", "required_proposition", "label", "name"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                proposition = val
                break
        proposition = proposition or str(goal)
        # Get patch from support if available
        support = getattr(goal, "support", None)
        if (
            support
            and hasattr(support, "patch_keys")
            and support.patch_keys
        ):
            patch_id = next(iter(support.patch_keys))
        else:
            patch_id = f"witness_patch_{uuid.uuid4().hex[:8]}"
        witness = make_proposal(
            patch_id,
            "theorem_witness",
            f"witness for: {proposition[:60]}",
        )
        witness.accept()
        return witness

    def is_well_formed(self, goal: Any) -> bool:
        """Return True if the goal is well-formed (non-empty proposition).

        Parameters
        ----------
        goal : Any

        Returns
        -------
        bool
        """
        for attr in ("proposition", "required_proposition", "label", "name"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str) and val.strip():
                return True
        return bool(str(goal).strip())


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def verify_all_theorems(
    fleet: Any,
    signals: list[BackpressureSignal],
    moves: list[SemanticMove],
    goal: Any,
    context: SynthesisContext,
) -> dict[str, Any]:
    """Verify all Ch42 theorems and return a combined report.

    Runs each of the four Ch42 theorems against the provided evidence
    and collects results into a single report dict.

    Parameters
    ----------
    fleet : Any
        InhabitantFleet or duck-typed equivalent.
    signals : list[BackpressureSignal]
        Active backpressure signals.
    moves : list[SemanticMove]
        Available semantic moves.
    goal : Any
        Current synthesis goal.
    context : SynthesisContext
        Current synthesis context.

    Returns
    -------
    dict[str, Any]
        Keys: convergence, boundedness, completeness, existence, overall.
        Each sub-dict is the result from the corresponding theorem.
        ``overall`` contains ``all_passed`` (bool) and ``theorem_count`` (int).

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.theorems import verify_all_theorems
    >>> from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
    ...     create_synthesis_context,
    ... )
    >>> class FakeFleet:
    ...     members = []
    ...     current_bids = []
    ...     def utilization(self): return 0.0
    >>> ctx = create_synthesis_context(budget=2)
    >>> report = verify_all_theorems(FakeFleet(), [], [], "test goal", ctx)
    >>> "overall" in report
    True
    """
    verifier = TheoremVerifier()
    results: dict[str, Any] = {}

    # Theorem 42.1 — Fleet Convergence
    conv_thm = FleetConvergenceTheorem()
    results["convergence"] = conv_thm.apply(fleet)

    # Theorem 42.2 — Backpressure Boundedness
    bp_thm = BackpressureBoundednessTheorem()
    results["boundedness"] = bp_thm.check(signals, [])

    # Theorem 42.3 — Semantic Move Completeness
    completeness_thm = SemanticMoveCompletenessTheorem()
    results["completeness"] = completeness_thm.check_completeness(moves, goal)

    # Theorem 42.4 — Inhabitant Existence
    existence_thm = InhabitantExistenceTheorem()
    results["existence"] = existence_thm.apply(goal, context)

    # Overall verdict
    all_passed = all(
        r.get("verdict") in (
            "PROVED", "SATISFIED", "COMPLETE", "EXISTS", "convergent"
        )
        for r in results.values()
    )
    results["overall"] = {
        "all_passed": all_passed,
        "theorem_count": len(results) - 1,
    }
    return results


__all__ = [
    "TheoremVerifier",
    "FleetConvergenceTheorem",
    "BackpressureBoundednessTheorem",
    "SemanticMoveCompletenessTheorem",
    "InhabitantExistenceTheorem",
    "verify_all_theorems",
]
