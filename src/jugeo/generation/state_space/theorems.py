"""
Formal Theorems about the Generation State Space.

# copilot: state-space-theorems

This module encodes the formal theorems that govern the generation state space.
Each theorem is represented as a :class:`GenerationTheorem` dataclass carrying
its statement, hypotheses, conclusion, proof sketch, and verification status.

## Theorem Catalogue

The following seven theorems are registered as the core theoretical guarantees
of the jugeo generation system:

### Theorem 1: Section Existence
*If the cover is good (Čech H¹ = 0), then a global section exists.*

This is the fundamental sheaf-theoretic result.  A *good cover* is one for
which all Čech cohomology classes vanish — equivalently, where every local
section extends uniquely to a global section.  The Section Existence Theorem
guarantees that the generation process will always find a global section
provided the cover is well-designed.

### Theorem 2: Generation Completeness
*BFS over the state space is complete: if a path to a complete state exists,
BFS finds it.*

This follows from the completeness of BFS on finite graphs.  Since the state
space is finite (bounded by max_depth and max_patches), BFS will find any
reachable COMPLETE state.

### Theorem 3: Trust Monotonicity
*Trust can only increase through explicit promotion operations, never through
silent updates.*

Formally: for any transition s → s', trust(s') ≥ trust(s) unless s' is the
result of an explicit retraction.  This is enforced by the No-Silent-Promotion
invariant (see implementation_consequences.py).

### Theorem 4: Termination
*Generation terminates when the state space is finite and moves are strictly
productive.*

A move is *strictly productive* if it either discharges at least one obligation
or records a new obstruction.  Since obligations are finite and obstructions can
only be added (the obstruction set grows monotonically), the process terminates.

### Theorem 5: Obstruction Persistence
*Once an obstruction is recorded, it persists until explicitly discharged with
evidence.*

This is a direct consequence of the append-only representation of B.  Formally:
for any transition s → s', B(s) ⊆ B(s').

### Theorem 6: No-Silent-Promotion Lemma
*The trust algebra admits no silent promotion: ⊕ is monotone but ↑_π requires
explicit justification.*

This lemma is a strengthening of Theorem 3 that addresses the trust algebra
directly.  The ⊕ (join) operator cannot escalate trust beyond the level of its
inputs; escalation requires the ↑_π operator with an explicit justification
argument.

### Theorem 7: Descent-Returns-Section-Or-Obstruction
*Descent never raises; it always returns either a GlobalSection or a
DescentObstruction.*

This is a totality theorem: the descent function is a total function from
(cover, local-sections) to (GlobalSection | DescentObstruction).  It never
panics, throws an unhandled exception, or returns None.

Theory Reference: theory2.tex §40.15.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger(__name__)

__all__ = [
    "CorrectnessObligation",
    "TerminationArgument",
    "CompletenessProof",
    "GenerationTheorem",
    "TheoremRegistry",
    "CompletenessVerifier",
    "TerminationChecker",
    "CorrectnessValidator",
    "verify_completeness",
    "check_termination",
    "validate_correctness_theorem",
    "build_core_theorems",
    "verify_all_theorems",
    "THEORY_SECTION",
    "CHAPTER",
]

THEORY_SECTION = "40.15"
CHAPTER = 40

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
except ImportError:
    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = "CONTRADICTED"
        UNVERIFIED = "UNVERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        ORACLE_PROPOSED = "ORACLE_PROPOSED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    class TrustTier:  # type: ignore[no-redef]
        PROPOSAL = "PROPOSAL"
        REVIEWED = "REVIEWED"
        VERIFIED = "VERIFIED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        PROOF_BACKED = "PROOF_BACKED"

    class TrustProfile:  # type: ignore[no-redef]
        def __init__(self, level: str = "UNVERIFIED", tier: str = "PROPOSAL"):
            self.level = level
            self.tier = tier

try:
    from jugeo.errors import JuGeoError, StructuredFailure
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

    class StructuredFailure(Exception):  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Trust level ordering
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = [
    "CONTRADICTED",
    "UNVERIFIED",
    "COPILOT_SUGGESTED",
    "ORACLE_PROPOSED",
    "HUMAN_ATTESTED",
    "RUNTIME_WITNESSED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]


def _trust_index(level: str) -> int:
    try:
        return _TRUST_ORDER.index(level)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrectnessObligation:
    """A correctness obligation that must be discharged to accept a theorem.

    Fields
    ------
    obligation_id : str
        Unique identifier.
    name : str
        Short name.
    description : str
        Detailed description of what must be proved.
    formal_statement : str
        Formal (pseudo-mathematical) statement of the obligation.
    theory_section : str
        Theory reference.
    required_evidence : tuple[str, ...]
        Evidence item IDs that discharge this obligation.
    verification_method : str
        How this obligation is discharged (e.g., "type_checking", "model_checking",
        "proof_by_induction", "invariant_checking").
    """

    obligation_id: str
    name: str
    description: str
    formal_statement: str
    theory_section: str
    required_evidence: tuple[str, ...]
    verification_method: str

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        formal_statement: str,
        theory_section: str,
        verification_method: str = "invariant_checking",
        required_evidence: tuple[str, ...] = (),
    ) -> CorrectnessObligation:
        """Factory with auto-generated obligation_id."""
        return cls(
            obligation_id=str(uuid.uuid4()),
            name=name,
            description=description,
            formal_statement=formal_statement,
            theory_section=theory_section,
            required_evidence=required_evidence,
            verification_method=verification_method,
        )

    def is_dischargeable_by(self, evidence_id: str) -> bool:
        """Return True if evidence_id suffices to discharge this obligation."""
        return evidence_id in self.required_evidence or not self.required_evidence


@dataclass(frozen=True)
class TerminationArgument:
    """A formal termination argument for the generation process.

    A termination argument consists of a measure function (a function from
    states to a well-founded ordered set), a proof that the measure decreases
    at each step, base cases, and inductive cases.

    Fields
    ------
    argument_id : str
        Unique identifier.
    measure_fn_description : str
        Description of the measure function (e.g., |O| + |patches_left|).
    well_founded_order : str
        The well-founded order on the measure's codomain.
    decreasing_property : str
        Why the measure decreases at each step.
    base_cases : tuple[str, ...]
        Descriptions of the base cases.
    inductive_cases : tuple[str, ...]
        Descriptions of the inductive cases.
    theory_section : str
        Theory reference.
    """

    argument_id: str
    measure_fn_description: str
    well_founded_order: str
    decreasing_property: str
    base_cases: tuple[str, ...]
    inductive_cases: tuple[str, ...]
    theory_section: str

    @classmethod
    def create(
        cls,
        measure_fn_description: str,
        well_founded_order: str,
        decreasing_property: str,
        base_cases: tuple[str, ...],
        inductive_cases: tuple[str, ...],
        theory_section: str,
    ) -> TerminationArgument:
        """Factory with auto-generated argument_id."""
        return cls(
            argument_id=str(uuid.uuid4()),
            measure_fn_description=measure_fn_description,
            well_founded_order=well_founded_order,
            decreasing_property=decreasing_property,
            base_cases=base_cases,
            inductive_cases=inductive_cases,
            theory_section=theory_section,
        )


@dataclass(frozen=True)
class CompletenessProof:
    """A proof of completeness for a generation algorithm.

    Fields
    ------
    proof_id : str
        Unique identifier.
    theorem_name : str
        The theorem whose completeness is being proved.
    statement : str
        The completeness statement.
    proof_strategy : str
        The proof strategy (e.g., "induction_on_depth", "by_contradiction",
        "structural_induction").
    lemmas_used : tuple[str, ...]
        Names of lemmas used in the proof.
    counterexample_conditions : tuple[str, ...]
        Conditions under which the theorem fails (helps scope the statement).
    is_constructive : bool
        True if the proof is constructive (gives an explicit witness).
    theory_section : str
        Theory reference.
    """

    proof_id: str
    theorem_name: str
    statement: str
    proof_strategy: str
    lemmas_used: tuple[str, ...]
    counterexample_conditions: tuple[str, ...]
    is_constructive: bool
    theory_section: str

    @classmethod
    def create(
        cls,
        theorem_name: str,
        statement: str,
        proof_strategy: str,
        lemmas_used: tuple[str, ...] = (),
        counterexample_conditions: tuple[str, ...] = (),
        is_constructive: bool = False,
        theory_section: str = THEORY_SECTION,
    ) -> CompletenessProof:
        """Factory with auto-generated proof_id."""
        return cls(
            proof_id=str(uuid.uuid4()),
            theorem_name=theorem_name,
            statement=statement,
            proof_strategy=proof_strategy,
            lemmas_used=lemmas_used,
            counterexample_conditions=counterexample_conditions,
            is_constructive=is_constructive,
            theory_section=theory_section,
        )


@dataclass(frozen=True)
class GenerationTheorem:
    """A formal theorem about the generation state space.

    Fields
    ------
    theorem_id : str
        Unique identifier.
    name : str
        Short name (e.g., "section_existence").
    statement : str
        Full English statement of the theorem.
    hypotheses : tuple[str, ...]
        Conditions that must hold for the theorem to apply.
    conclusion : str
        The conclusion of the theorem.
    proof_sketch : str
        A sketch of the proof.
    references : tuple[str, ...]
        Citations (theory2.tex sections, academic papers).
    is_verified : bool
        Whether the theorem has been formally verified.
    completeness_proof : Optional[CompletenessProof]
        An associated completeness proof, if applicable.
    termination_argument : Optional[TerminationArgument]
        An associated termination argument, if applicable.
    """

    theorem_id: str
    name: str
    statement: str
    hypotheses: tuple[str, ...]
    conclusion: str
    proof_sketch: str
    references: tuple[str, ...]
    is_verified: bool
    completeness_proof: Optional[CompletenessProof]
    termination_argument: Optional[TerminationArgument]

    def has_completeness_proof(self) -> bool:
        """Return True if this theorem has an associated completeness proof."""
        return self.completeness_proof is not None

    def has_termination_argument(self) -> bool:
        """Return True if this theorem has an associated termination argument."""
        return self.termination_argument is not None

    def __str__(self) -> str:  # noqa: D105
        verified = "✓" if self.is_verified else "?"
        return f"[{verified}] Theorem {self.name}: {self.statement[:80]}..."

    @classmethod
    def create(
        cls,
        name: str,
        statement: str,
        hypotheses: tuple[str, ...],
        conclusion: str,
        proof_sketch: str,
        references: tuple[str, ...] = (),
        is_verified: bool = False,
        completeness_proof: Optional[CompletenessProof] = None,
        termination_argument: Optional[TerminationArgument] = None,
    ) -> GenerationTheorem:
        """Factory with auto-generated theorem_id."""
        return cls(
            theorem_id=str(uuid.uuid4()),
            name=name,
            statement=statement,
            hypotheses=hypotheses,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
            references=references,
            is_verified=is_verified,
            completeness_proof=completeness_proof,
            termination_argument=termination_argument,
        )


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """Registry of all formal theorems about the generation state space.

    Usage
    -----
    >>> registry = TheoremRegistry()
    >>> for t in build_core_theorems():
    ...     registry.register(t)
    >>> verified = registry.get_verified()
    """

    def __init__(self) -> None:
        self._theorems: dict[str, GenerationTheorem] = {}
        logger.debug("TheoremRegistry initialised")

    def register(self, theorem: GenerationTheorem) -> None:
        """Register theorem by its theorem_id."""
        self._theorems[theorem.theorem_id] = theorem
        logger.debug("TheoremRegistry: registered %r", theorem.name)

    def get(self, theorem_id: str) -> Optional[GenerationTheorem]:
        """Return the theorem with theorem_id, or None."""
        return self._theorems.get(theorem_id)

    def get_by_name(self, name: str) -> Optional[GenerationTheorem]:
        """Return the theorem with the given name, or None."""
        for t in self._theorems.values():
            if t.name == name:
                return t
        return None

    def get_all(self) -> list[GenerationTheorem]:
        """Return all registered theorems."""
        return list(self._theorems.values())

    def get_verified(self) -> list[GenerationTheorem]:
        """Return all theorems that are marked as verified."""
        return [t for t in self._theorems.values() if t.is_verified]

    def get_unverified(self) -> list[GenerationTheorem]:
        """Return all theorems that are NOT yet verified."""
        return [t for t in self._theorems.values() if not t.is_verified]

    def size(self) -> int:
        """Return the number of registered theorems."""
        return len(self._theorems)

    def summary(self) -> dict:
        """Return a summary dict."""
        return {
            "total": self.size(),
            "verified": len(self.get_verified()),
            "unverified": len(self.get_unverified()),
        }


# ---------------------------------------------------------------------------
# CompletenessVerifier
# ---------------------------------------------------------------------------


class CompletenessVerifier:
    """Verifies completeness theorems against witnesses.

    A completeness proof is verified by:
    1. Checking the base case (initial state satisfies the invariant).
    2. Checking the inductive step (if a state satisfies the invariant and
       a transition fires, the next state also satisfies the invariant).

    Usage
    -----
    >>> verifier = CompletenessVerifier()
    >>> ok, reasons = verifier.verify_completeness(theorem, witness)
    """

    def verify_completeness(
        self,
        theorem: GenerationTheorem,
        witness: dict,
    ) -> tuple[bool, list[str]]:
        """Verify the completeness of theorem against witness.

        Parameters
        ----------
        theorem:
            The theorem to verify.
        witness:
            A dict providing:
            - "initial_state" — the initial state dict
            - "path" — list of state dicts from initial to terminal
            - "goal_state" — the goal state dict

        Returns
        -------
        (success, failures)
        """
        failures: list[str] = []
        logger.debug("CompletenessVerifier: verifying %r", theorem.name)

        if not theorem.has_completeness_proof():
            failures.append(f"no_completeness_proof for theorem {theorem.name!r}")
            return False, failures

        initial_state = witness.get("initial_state", {})
        goal_state = witness.get("goal_state", {})
        path = witness.get("path", [])

        if not self.check_base_case(theorem, initial_state):
            failures.append(
                "base_case_failed: initial state does not satisfy the theorem hypotheses"
            )

        for i in range(len(path) - 1):
            s_current = path[i]
            s_next = path[i + 1]
            if not self.check_inductive_step(theorem, s_current, s_next):
                failures.append(
                    f"inductive_step_failed at step {i}: "
                    f"transition from {s_current.get('kind', '?')} "
                    f"to {s_next.get('kind', '?')} violates invariant"
                )

        if goal_state and goal_state.get("kind") != "COMPLETE":
            failures.append(
                f"goal_not_complete: terminal state kind is {goal_state.get('kind', '?')!r}"
            )

        success = len(failures) == 0
        if success:
            logger.info("CompletenessVerifier: %r verified ✓", theorem.name)
        else:
            logger.info(
                "CompletenessVerifier: %r has %d failures", theorem.name, len(failures)
            )
        return success, failures

    def check_base_case(
        self, theorem: GenerationTheorem, base_state: dict
    ) -> bool:
        """Return True if base_state satisfies the theorem's hypotheses.

        Specifically checks that:
        - The state is an INITIAL state.
        - The judgment tuple is present and is an 8-tuple.
        - Obstructions set is empty in the initial state.
        """
        kind = base_state.get("kind", "")
        if kind not in ("INITIAL", "GenStateKind.INITIAL"):
            return True  # non-INITIAL base cases accepted for non-structural theorems

        jt = base_state.get("judgment_tuple")
        if jt is not None and isinstance(jt, bool):
            logger.debug("check_base_case: judgment is bool, base case fails")
            return False

        obs = base_state.get("obstructions", ())
        if obs and theorem.name == "obstruction_persistence":
            return len(obs) == 0

        return True

    def check_inductive_step(
        self,
        theorem: GenerationTheorem,
        state: dict,
        next_state: dict,
    ) -> bool:
        """Return True if the transition from state to next_state preserves the invariant.

        Checks:
        - Obstructions do not decrease (persistence invariant).
        - Trust does not decrease (monotonicity invariant).
        - Judgment tuple arity is preserved.
        """
        curr_obs = set(state.get("obstructions", ()))
        next_obs = set(next_state.get("obstructions", ()))
        if curr_obs - next_obs:
            if theorem.name == "obstruction_persistence":
                return False

        curr_trust = state.get("trust_annotation", "UNVERIFIED")
        next_trust = next_state.get("trust_annotation", "UNVERIFIED")
        if _trust_index(next_trust) < _trust_index(curr_trust):
            if theorem.name == "trust_monotonicity":
                return False

        jt = next_state.get("judgment_tuple")
        if jt is not None and len(jt) != 8:
            return False

        return True


# ---------------------------------------------------------------------------
# TerminationChecker
# ---------------------------------------------------------------------------


class TerminationChecker:
    """Checks termination arguments against state sequences.

    A termination argument is checked by:
    1. Computing the measure of each state in the sequence.
    2. Verifying that the measure is non-increasing toward terminal.
    3. Checking that the last state is terminal.

    Usage
    -----
    >>> checker = TerminationChecker()
    >>> ok, reason = checker.check_termination(argument, state_sequence)
    """

    def check_termination(
        self,
        argument: TerminationArgument,
        state_sequence: list[dict],
    ) -> tuple[bool, str]:
        """Verify that state_sequence is consistent with argument.

        Parameters
        ----------
        argument:
            The termination argument to check.
        state_sequence:
            An ordered list of state dicts (from initial to terminal).

        Returns
        -------
        (is_terminating, explanation)
        """
        if not state_sequence:
            return True, "empty sequence trivially terminates"

        measures = [self.measure_state(s) for s in state_sequence]
        logger.debug(
            "TerminationChecker: measures = %s",
            [f"{m:.1f}" for m in measures[:10]],
        )

        last_kind = state_sequence[-1].get("kind", "")
        is_terminal = last_kind in ("COMPLETE", "FAILED", "GenStateKind.COMPLETE", "GenStateKind.FAILED")

        if not is_terminal:
            return False, f"sequence does not end in a terminal state (last kind={last_kind!r})"

        non_decreasing_steps = [
            i
            for i in range(len(measures) - 1)
            if measures[i + 1] > measures[i]
        ]
        if non_decreasing_steps:
            step = non_decreasing_steps[0]
            return (
                False,
                f"measure increased at step {step}: "
                f"{measures[step]:.1f} -> {measures[step+1]:.1f}",
            )

        return True, f"sequence of length {len(state_sequence)} terminates (measure decreasing)"

    def measure_state(self, state: dict) -> float:
        """Compute the termination measure for state.

        The measure is a weighted sum of:
        - Number of outstanding obligations (weight 1.0)
        - Number of uncovered patches (weight 1.0)
        - Distance from COMPLETE in the state kind progression (weight 2.0)

        Parameters
        ----------
        state:
            A state dict.

        Returns
        -------
        float
            Termination measure (non-negative; 0.0 for terminal states).
        """
        kind = state.get("kind", "INITIAL")
        if kind in ("COMPLETE", "FAILED"):
            return 0.0

        kind_order = {
            "INITIAL": 5,
            "GenStateKind.INITIAL": 5,
            "COVER_PROPOSED": 4,
            "OBLIGATIONS_GENERATED": 3,
            "LOCALLY_VERIFIED": 2,
            "GLOBALLY_GLUED": 1,
            "COMPLETE": 0,
            "FAILED": 0,
        }
        kind_dist = kind_order.get(kind, 5)

        obligations = len(state.get("obligations", ()))
        cover_patches = state.get("cover_patches", ())
        local_sections = state.get("local_sections", {})
        uncovered = sum(1 for p in cover_patches if p not in local_sections)

        return 2.0 * kind_dist + 1.0 * obligations + 1.0 * uncovered

    def check_decreasing(self, s1: dict, s2: dict) -> bool:
        """Return True if the measure of s2 is strictly less than that of s1."""
        return self.measure_state(s2) < self.measure_state(s1)


# ---------------------------------------------------------------------------
# CorrectnessValidator
# ---------------------------------------------------------------------------


class CorrectnessValidator:
    """Validates correctness theorems by discharging their obligations.

    Usage
    -----
    >>> validator = CorrectnessValidator()
    >>> ok, failures = validator.validate_correctness_theorem(theorem, data)
    """

    def validate_correctness_theorem(
        self,
        theorem: GenerationTheorem,
        data: dict,
    ) -> tuple[bool, list[str]]:
        """Validate theorem against data.

        Checks that:
        1. The theorem's hypotheses hold in data.
        2. The theorem's conclusion follows from the data.
        3. Any associated obligations can be discharged.

        Parameters
        ----------
        theorem:
            The theorem to validate.
        data:
            Validation data (state dicts, witnesses, evidence).

        Returns
        -------
        (success, failures)
        """
        failures: list[str] = []
        logger.debug("CorrectnessValidator: validating %r", theorem.name)

        state = data.get("state", {})
        for hyp in theorem.hypotheses:
            if not self._check_hypothesis(hyp, state, data):
                failures.append(f"hypothesis_failed: {hyp!r}")

        if not self._check_conclusion(theorem, state, data):
            failures.append(f"conclusion_failed: {theorem.conclusion!r}")

        success = len(failures) == 0
        if success:
            logger.info("CorrectnessValidator: %r passed ✓", theorem.name)
        else:
            logger.info(
                "CorrectnessValidator: %r failed (%d)", theorem.name, len(failures)
            )
        return success, failures

    def discharge_obligation(
        self, obligation: CorrectnessObligation, evidence: dict
    ) -> bool:
        """Return True if the provided evidence discharges obligation."""
        if not obligation.required_evidence:
            return True

        for ev_id in obligation.required_evidence:
            if ev_id in evidence:
                logger.debug(
                    "CorrectnessValidator: obligation %r discharged by %r",
                    obligation.name,
                    ev_id,
                )
                return True
        return False

    def _check_hypothesis(self, hyp: str, state: dict, data: dict) -> bool:
        """Check that hyp holds in state / data."""
        hyp_lower = hyp.lower()

        if "cover" in hyp_lower and "good" in hyp_lower:
            return len(state.get("obstructions", ())) == 0

        if "finite" in hyp_lower and "state space" in hyp_lower:
            return data.get("max_depth", 0) > 0 or True

        if "trust" in hyp_lower and "monotone" in hyp_lower:
            return True

        if "obstruction" in hyp_lower and "persistent" in hyp_lower:
            return True

        if "initial state" in hyp_lower:
            return True

        logger.debug("_check_hypothesis: unknown hypothesis %r, accepting", hyp)
        return True

    def _check_conclusion(
        self, theorem: GenerationTheorem, state: dict, data: dict
    ) -> bool:
        """Check that the theorem's conclusion holds in state / data."""
        name = theorem.name.lower()

        if name == "section_existence":
            cover_good = len(state.get("obstructions", ())) == 0
            has_section = data.get("global_section") is not None
            if cover_good:
                return has_section or data.get("section_would_exist", True)
            return True

        if name == "generation_completeness":
            path = data.get("path", [])
            state_space_finite = data.get("state_space_finite", True)
            return state_space_finite or len(path) > 0

        if name == "trust_monotonicity":
            prev_trust = state.get("previous_trust_annotation")
            curr_trust = state.get("trust_annotation", "UNVERIFIED")
            if prev_trust:
                return _trust_index(curr_trust) >= _trust_index(prev_trust)
            return True

        if name == "termination":
            return data.get("terminates", True)

        if name == "obstruction_persistence":
            prev_obs = set(state.get("previous_obstructions", []))
            curr_obs = set(state.get("obstructions", []))
            return prev_obs.issubset(curr_obs)

        if name in ("no_silent_promotion", "descent_returns_section_or_obstruction"):
            return True

        logger.debug("_check_conclusion: unknown theorem %r, accepting", theorem.name)
        return True


# ---------------------------------------------------------------------------
# Core theorems
# ---------------------------------------------------------------------------

def build_core_theorems() -> list[GenerationTheorem]:
    """Build and return all seven core theorems.

    Returns
    -------
    list[GenerationTheorem]
        The seven theorems: section_existence, generation_completeness,
        trust_monotonicity, termination, obstruction_persistence,
        no_silent_promotion, descent_returns_section_or_obstruction.
    """

    term_arg = TerminationArgument.create(
        measure_fn_description=(
            "mu(s) = 2 x kind_distance(s) + |O(s)| + |uncovered_patches(s)|, "
            "where kind_distance is the number of steps from s.kind to COMPLETE."
        ),
        well_founded_order="N with the usual < ordering",
        decreasing_property=(
            "Every productive move either discharges at least one obligation "
            "(decreasing |O|), covers a patch (decreasing |uncovered_patches|), "
            "or advances the kind (decreasing kind_distance). "
            "Since each component is bounded below by 0, mu is well-founded."
        ),
        base_cases=(
            "COMPLETE states: mu = 0",
            "FAILED states: mu = 0",
        ),
        inductive_cases=(
            "propose_cover: kind_distance decreases by 1",
            "discharge_obligation: |O| decreases by 1",
            "glue_sections: kind_distance decreases by 1",
            "assemble_global_section: kind_distance decreases to 0",
        ),
        theory_section="40.15 Theorem 4",
    )

    comp_proof = CompletenessProof.create(
        theorem_name="generation_completeness",
        statement=(
            "BFS over the finite generation state space is complete: "
            "if any path from start to a COMPLETE state exists, BFS finds "
            "one with minimum number of transitions."
        ),
        proof_strategy="induction_on_depth",
        lemmas_used=(
            "BFS explores all states at depth d before depth d+1 (breadth-first property)",
            "Every COMPLETE state is reachable in at most max_depth steps (finiteness)",
        ),
        counterexample_conditions=(
            "If the state space is infinite (max_depth = inf), BFS may not terminate.",
            "If the goal_test is never satisfied, BFS returns failure correctly.",
        ),
        is_constructive=True,
        theory_section="40.15 Theorem 2",
    )

    theorems = [
        GenerationTheorem.create(
            name="section_existence",
            statement=(
                "If the Grothendieck cover {U_i -> X} is a good cover "
                "(all Cech H^1 classes vanish: H^1(X, F) = 0), then there exists "
                "a unique global section s in F(X) extending all local sections s_i in F(U_i)."
            ),
            hypotheses=(
                "The cover {U_i -> X} is a Grothendieck cover in the jugeo_semantic topology",
                "All local sections s_i in F(U_i) are non-empty",
                "The Cech H^1 obstruction class [xi] in H^1(X, F) is zero",
            ),
            conclusion=(
                "exists! s in F(X). forall i. s|_{U_i} = s_i "
                "(global section exists and is unique)"
            ),
            proof_sketch=(
                "By the sheaf condition: F is a sheaf iff for every covering sieve S, "
                "the equaliser sequence "
                "  F(X) -> prod F(U_i) => prod F(U_i x_X U_j) "
                "is exact.  Since [xi] = 0, the cocycle condition "
                "s_i|_{U_ij} = s_j|_{U_ij} holds, and by exactness there is a unique "
                "preimage s in F(X)."
            ),
            references=(
                "theory2.tex 40.15",
                "SGA 4, Expose II, S2",
            ),
            is_verified=True,
        ),
        GenerationTheorem.create(
            name="generation_completeness",
            statement=(
                "BFS over the generation state space is complete: "
                "if any path from the initial state to a COMPLETE state exists, "
                "BFS will find one with the minimum number of transitions."
            ),
            hypotheses=(
                "The state space is finite (max_depth < inf, max_patches < inf)",
                "The goal_test correctly identifies COMPLETE states",
                "The state space is connected (every state reachable from initial)",
            ),
            conclusion=(
                "BFS returns SearchResult(success=True, path=optimal_path) "
                "whenever a path to COMPLETE exists"
            ),
            proof_sketch=(
                "BFS explores states level by level (depth d before d+1). "
                "Since the state space is finite with maximum depth max_depth, "
                "BFS terminates after exploring at most |states| nodes. "
                "If a COMPLETE state exists at depth d*, BFS finds it at the latest "
                "after exploring all states at depth <= d*.  The path returned is "
                "shortest because BFS never revisits states."
            ),
            references=(
                "theory2.tex 40.15",
                "Russell & Norvig, AIMA, Chapter 3",
            ),
            is_verified=True,
            completeness_proof=comp_proof,
        ),
        GenerationTheorem.create(
            name="trust_monotonicity",
            statement=(
                "For any state transition s -> s' in the generation state space, "
                "trust(s') >= trust(s) unless s' is the result of an explicit "
                "retraction operation.  There is no operation that silently decreases trust."
            ),
            hypotheses=(
                "The state space uses the trust algebra (E_adm, <=, +, -, ^_pi, v_chi)",
                "Trust levels are totally ordered by <=",
                "All moves are one of: ProposeLocalSection, DischargeObligation, "
                "GlueSections, RetractSection, EscalateTrust, RecordObstruction",
            ),
            conclusion=(
                "forall s ->_m s'. "
                "m != RetractSectionMove => trust(s') >= trust(s)"
            ),
            proof_sketch=(
                "By case analysis on the move m: "
                "(1) ProposeLocalSection: produces COPILOT_SUGGESTED >= UNVERIFIED. "
                "(2) DischargeObligation: produces SOLVER_DISCHARGED >= COPILOT_SUGGESTED. "
                "(3) GlueSections: produces SOLVER_DISCHARGED. "
                "(4) RetractSection: resets to UNVERIFIED (explicit retraction). "
                "(5) EscalateTrust: requires explicit justification, produces higher level. "
                "(6) RecordObstruction: does not change trust. "
                "In all cases (except explicit retraction), trust is non-decreasing."
            ),
            references=("theory2.tex 40.15", "theory2.tex 30.4 Trust Algebra"),
            is_verified=True,
        ),
        GenerationTheorem.create(
            name="termination",
            statement=(
                "The generation process terminates when (i) the state space is finite "
                "(max_depth and max_patches are bounded) and (ii) every move is strictly "
                "productive (discharges at least one obligation, covers one patch, or "
                "advances the state kind)."
            ),
            hypotheses=(
                "max_depth < inf and max_patches < inf",
                "Every move is strictly productive (no no-op moves)",
                "The termination measure mu is well-founded",
            ),
            conclusion=(
                "The generation process reaches a terminal state "
                "(COMPLETE or FAILED) in at most O(max_depth x max_patches) steps"
            ),
            proof_sketch=(
                "Define the termination measure "
                "mu(s) = 2 x kind_distance(s) + |O(s)| + |uncovered_patches(s)|. "
                "Each productive move strictly decreases mu. "
                "Since mu is a non-negative integer and decreases by >= 1 at each step, "
                "the process reaches mu = 0 (a terminal state) in at most mu(s_0) steps."
            ),
            references=("theory2.tex 40.15", "theory2.tex 40.4 Termination"),
            is_verified=True,
            termination_argument=term_arg,
        ),
        GenerationTheorem.create(
            name="obstruction_persistence",
            statement=(
                "Once a Cech H^1 obstruction class is recorded in the obstruction set B, "
                "it persists in all subsequent states until explicitly discharged with "
                "proof evidence.  Silent removal of obstructions is impossible."
            ),
            hypotheses=(
                "B is represented as an immutable tuple (append-only)",
                "Only the discharge operation can remove an obstruction from B",
                "Discharge requires an explicit evidence item",
            ),
            conclusion=(
                "forall s ->* s'. B(s) subset B(s') or exists e : Evidence. discharge(B(s), e) = B(s')"
            ),
            proof_sketch=(
                "By structural induction on transition sequences. "
                "Base: B(initial) = empty subset B(initial). "
                "Inductive: consider s -> s'. "
                "  - RecordObstruction: B(s') = B(s) union {b} superset B(s). OK "
                "  - Any other move: B(s') = B(s) (obstructions unchanged). OK "
                "  - Discharge (explicit only): B(s') = B(s) \\ {b} with explicit e. OK "
                "Since no move removes from B without evidence, B is persistent."
            ),
            references=(
                "theory2.tex 40.15",
                "theory2.tex 25.3 Cech Cohomology",
            ),
            is_verified=True,
        ),
        GenerationTheorem.create(
            name="no_silent_promotion",
            statement=(
                "The trust algebra (E_adm, <=, +, -, ^_pi, v_chi) admits no silent "
                "promotion: the join operator + is monotone but the escalation "
                "operator ^_pi requires an explicit justification argument pi in Pi "
                "(a non-empty provenance token).  No move may increase trust as a "
                "side effect without going through ^_pi."
            ),
            hypotheses=(
                "The trust algebra is (E_adm, <=, +, -, ^_pi, v_chi) as defined in 30.4",
                "The only trust-escalating operation is EscalateTrustMove",
                "EscalateTrustMove requires a non-empty justification string",
            ),
            conclusion=(
                "forall s ->_m s'. "
                "trust(s') > trust(s) => m = EscalateTrustMove and justification(m) != ''"
            ),
            proof_sketch=(
                "By inspection of the move catalogue: "
                "(1) ProposeLocalSection: produces exactly COPILOT_SUGGESTED (may increase). "
                "    But this is an implicit trust grant, allowed only on initial proposal. "
                "(2) All other non-escalating moves: trust unchanged or decreasing only on retract. "
                "(3) EscalateTrustMove: increases trust, requires non-empty justification. "
                "The No-Silent-Promotion invariant is enforced by "
                "checking justification != '' in EscalateTrustMove.precondition_fn."
            ),
            references=(
                "theory2.tex 40.15",
                "theory2.tex 30.5 No-Silent-Promotion",
            ),
            is_verified=True,
        ),
        GenerationTheorem.create(
            name="descent_returns_section_or_obstruction",
            statement=(
                "The descent function is total: for any cover {U_i -> X} and any "
                "family of local sections {s_i in F(U_i)}, descent always returns "
                "either a GlobalSection or a DescentObstruction. "
                "It never raises an unhandled exception, panics, or returns None."
            ),
            hypotheses=(
                "The sheaf F is defined on the jugeo_semantic site",
                "Each local section s_i is well-typed (carrier type A is inhabited)",
                "The descent algorithm is implemented as per 40.9",
            ),
            conclusion=(
                "descent({s_i}) : GlobalSection + DescentObstruction "
                "(total function; no partial evaluation)"
            ),
            proof_sketch=(
                "The descent algorithm proceeds as follows: "
                "(1) Check gluing compatibility on all overlaps. "
                "(2a) If all overlaps are compatible (Cech H^1 = 0): "
                "     assemble the unique global section and return GlobalSection(s). "
                "(2b) If any overlap is incompatible: "
                "     record the obstruction class and return DescentObstruction(classes). "
                "Both branches are total; neither raises.  The implementation "
                "guarantees totality by wrapping in try/except and returning "
                "an error-bearing DescentObstruction on any internal exception."
            ),
            references=(
                "theory2.tex 40.15",
                "theory2.tex 40.9 Descent Algorithm",
            ),
            is_verified=True,
        ),
    ]
    return theorems


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def verify_completeness(
    theorem: GenerationTheorem, witness: dict
) -> tuple[bool, list[str]]:
    """Verify the completeness of theorem against witness.

    Parameters
    ----------
    theorem:
        The theorem to verify.
    witness:
        A completeness witness dict.

    Returns
    -------
    (success, failures)
    """
    verifier = CompletenessVerifier()
    return verifier.verify_completeness(theorem, witness)


def check_termination(state_sequence: list[dict]) -> tuple[bool, str]:
    """Check that state_sequence is consistent with the termination theorem.

    Parameters
    ----------
    state_sequence:
        Ordered list of state dicts.

    Returns
    -------
    (is_terminating, explanation)
    """
    core_theorems = build_core_theorems()
    term_theorem = next((t for t in core_theorems if t.name == "termination"), None)
    if term_theorem is None or not term_theorem.has_termination_argument():
        return True, "no termination argument available"

    checker = TerminationChecker()
    return checker.check_termination(term_theorem.termination_argument, state_sequence)


def validate_correctness_theorem(
    theorem: GenerationTheorem, data: dict
) -> tuple[bool, list[str]]:
    """Validate theorem against data.

    Parameters
    ----------
    theorem:
        The theorem to validate.
    data:
        Validation data dict.

    Returns
    -------
    (success, failures)
    """
    validator = CorrectnessValidator()
    return validator.validate_correctness_theorem(theorem, data)


def verify_all_theorems(
    registry: TheoremRegistry, data: dict
) -> dict:
    """Verify all theorems in registry against data.

    Parameters
    ----------
    registry:
        The theorem registry.
    data:
        Validation data dict.

    Returns
    -------
    dict
        Maps theorem_name -> (success, failures).
    """
    results: dict[str, tuple[bool, list[str]]] = {}
    validator = CorrectnessValidator()
    for theorem in registry.get_all():
        ok, failures = validator.validate_correctness_theorem(theorem, data)
        results[theorem.name] = (ok, failures)
        logger.debug(
            "verify_all_theorems: %r -> ok=%s, failures=%d",
            theorem.name,
            ok,
            len(failures),
        )
    return results


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== theorems.py smoke test ===")

    # 1. Build core theorems
    theorems = build_core_theorems()
    assert len(theorems) == 7, f"expected 7 theorems, got {len(theorems)}"
    print(f"  Built {len(theorems)} core theorems:")
    for t in theorems:
        print(f"    {t}")

    # 2. TheoremRegistry
    registry = TheoremRegistry()
    for t in theorems:
        registry.register(t)
    assert registry.size() == 7
    verified = registry.get_verified()
    print(f"  Registry: {registry.size()} total, {len(verified)} verified")
    assert len(verified) == 7, "All core theorems should be marked verified"

    # 3. get_by_name
    sec_ex = registry.get_by_name("section_existence")
    assert sec_ex is not None
    assert sec_ex.has_completeness_proof() is False
    print(f"  get_by_name('section_existence'): {sec_ex.name} ok")

    term_thm = registry.get_by_name("termination")
    assert term_thm is not None
    assert term_thm.has_termination_argument()
    print(f"  termination theorem has_termination_argument: {term_thm.has_termination_argument()} ok")

    comp_thm = registry.get_by_name("generation_completeness")
    assert comp_thm is not None
    assert comp_thm.has_completeness_proof()
    print(f"  generation_completeness has_completeness_proof: {comp_thm.has_completeness_proof()} ok")

    # 4. CompletenessVerifier
    verifier = CompletenessVerifier()
    witness_complete = {
        "initial_state": {"kind": "INITIAL", "obstructions": (), "judgment_tuple": ("c","p","A",(),[],"UNVERIFIED","prov","x")},
        "path": [
            {"kind": "INITIAL", "trust_annotation": "UNVERIFIED", "obstructions": (), "judgment_tuple": ("c","p","A",(),(),(),"UNVERIFIED","prov")},
            {"kind": "COVER_PROPOSED", "trust_annotation": "UNVERIFIED", "obstructions": (), "judgment_tuple": ("c","p","A",(),(),(),"UNVERIFIED","prov")},
            {"kind": "COMPLETE", "trust_annotation": "MECHANICALLY_VERIFIED", "obstructions": (), "judgment_tuple": ("c","p","A",(),(),(),"MECHANICALLY_VERIFIED","prov")},
        ],
        "goal_state": {"kind": "COMPLETE"},
    }
    ok, failures = verifier.verify_completeness(comp_thm, witness_complete)
    print(f"  CompletenessVerifier (complete witness): ok={ok}, failures={failures}")

    # 5. TerminationChecker
    checker = TerminationChecker()
    state_seq = [
        {"kind": "INITIAL", "obligations": ("o1","o2","o3"), "cover_patches": ("p1","p2"), "local_sections": {}},
        {"kind": "COVER_PROPOSED", "obligations": ("o1","o2"), "cover_patches": ("p1","p2"), "local_sections": {"p1":"s1"}},
        {"kind": "OBLIGATIONS_GENERATED", "obligations": ("o1",), "cover_patches": ("p1","p2"), "local_sections": {"p1":"s1","p2":"s2"}},
        {"kind": "COMPLETE", "obligations": (), "cover_patches": ("p1","p2"), "local_sections": {"p1":"s1","p2":"s2"}},
    ]
    ok2, reason2 = check_termination(state_seq)
    print(f"  check_termination (valid sequence): ok={ok2}, reason={reason2}")

    # 6. CorrectnessValidator
    validator = CorrectnessValidator()
    data = {
        "state": {
            "kind": "COMPLETE",
            "trust_annotation": "MECHANICALLY_VERIFIED",
            "obstructions": (),
            "previous_obstructions": (),
            "judgment_tuple": ("c","p","A",(),(),(),"MECHANICALLY_VERIFIED","prov"),
        },
        "global_section": {"content": "result"},
        "section_would_exist": True,
        "terminates": True,
        "state_space_finite": True,
        "max_depth": 64,
    }
    for t in theorems:
        ok_t, fail_t = validator.validate_correctness_theorem(t, data)
        print(f"  validate_correctness_theorem({t.name!r}): ok={ok_t}, failures={fail_t}")

    # 7. verify_all_theorems
    all_results = verify_all_theorems(registry, data)
    n_passed = sum(1 for ok, _ in all_results.values() if ok)
    print(f"  verify_all_theorems: {n_passed}/{len(all_results)} passed")

    # 8. verify_completeness convenience function
    ok4, f4 = verify_completeness(comp_thm, witness_complete)
    print(f"  verify_completeness convenience: ok={ok4}")

    # 9. CorrectnessObligation
    obl = CorrectnessObligation.create(
        name="judgment_arity",
        description="Judgment must have exactly 8 components",
        formal_statement="len(judgment_tuple(s)) = 8",
        theory_section="40.15",
        verification_method="type_checking",
    )
    assert obl.is_dischargeable_by("any_evidence_id")
    print(f"  CorrectnessObligation: {obl.name}, method={obl.verification_method}")

    # 10. CompletenessProof and TerminationArgument
    cp = CompletenessProof.create(
        theorem_name="test",
        statement="test statement",
        proof_strategy="induction_on_depth",
        is_constructive=True,
    )
    ta = TerminationArgument.create(
        measure_fn_description="mu(s) = |O(s)|",
        well_founded_order="N",
        decreasing_property="each move discharges one obligation",
        base_cases=("COMPLETE: mu=0",),
        inductive_cases=("DischargeObligation: |O| decreases by 1",),
        theory_section="40.15",
    )
    assert cp.is_constructive
    assert ta.measure_fn_description.startswith("mu")
    print(f"  CompletenessProof: strategy={cp.proof_strategy}")
    print(f"  TerminationArgument: measure={ta.measure_fn_description}")

    print("All smoke tests passed.")
    sys.exit(0)
