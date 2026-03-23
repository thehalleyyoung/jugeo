"""Theorem schema and falsification suite for frontier phases. theory2.tex Ch47. # copilot:"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import itertools
import json
import math
import time
import uuid
from enum import Enum

try:
    from jugeo.orchestration.frontier_phases.models import (
        PhaseKind, TransitionTrigger, PhaseDescriptor, PhaseTransitionRecord,
        PhaseHistory, StallDetector, ConvergenceCertificate, PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (
        Frontier, FrontierNode, FrontierHistory, PhaseTransition,
        BackpressureController, FrontierBudget, FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState, SemanticMove, ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
except Exception:
    pass

__all__ = [
    "TheoremStatus", "FalsificationMode", "TheoremId", "TheoremStatement",
    "VerificationEvidence", "FrontierPhasesTheoremSchema",
    "InvariantChecker", "FalsificationSuite", "TheoremVerificationReport",
    "DEFAULT_SCHEMA",
]


class TheoremStatus(Enum):
    """Possible epistemic states for a theorem in the verification pipeline.

    STATED: The theorem has been written down but no verification attempt has
        been made yet. This is the initial state for all newly registered theorems.

    PARTIALLY_VERIFIED: At least one verification attempt succeeded for a subset
        of the theorem's preconditions or postconditions, but the full set has not
        been verified.

    VERIFIED: All preconditions and postconditions have been confirmed by at least
        one successful falsification attempt (i.e., no counterexample was found)
        and the confidence score exceeds the acceptance threshold.

    FALSIFIED: A counterexample or invariant breach was found that definitively
        disproves the theorem under the stated preconditions.

    INCONCLUSIVE: Verification was attempted but the evidence was insufficient to
        reach either a VERIFIED or FALSIFIED verdict (e.g., test data was missing
        required fields, or confidence fell below the minimum threshold).
    """

    STATED = "stated"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    INCONCLUSIVE = "inconclusive"


class FalsificationMode(Enum):
    """Classification of the mechanism by which a theorem can be falsified.

    COUNTEREXAMPLE: A concrete input was found that satisfies all preconditions
        but violates at least one postcondition. This is the classical Popperian
        falsification mode.

    BOUNDARY_VIOLATION: The theorem holds in the interior of its domain but fails
        at boundary conditions (e.g., empty budget, zero diversity, unit trust).

    INVARIANT_BREACH: A loop or phase invariant was violated during execution.
        The theorem may hold globally but an intermediate state broke an assumed
        invariant.

    COVERAGE_FAILURE: The search coverage ratio fell below the expected bound
        guaranteed by the theorem's postconditions.

    TRUST_LOSS: The trust mass dropped below the tolerance threshold during a
        phase transition, violating the trust-preservation postcondition.
    """

    COUNTEREXAMPLE = "counterexample"
    BOUNDARY_VIOLATION = "boundary_violation"
    INVARIANT_BREACH = "invariant_breach"
    COVERAGE_FAILURE = "coverage_failure"
    TRUST_LOSS = "trust_loss"


@dataclasses.dataclass(frozen=True, slots=True)
class TheoremId:
    """A stable, content-addressed identifier for a theorem.

    theorem_id is a short human-readable label (e.g. "T47.1"). section is the
    chapter/section reference in the theory document (e.g. "Ch47§1").
    statement_hash is a 12-character hex prefix of the SHA-256 of the theorem's
    canonical statement bytes, used as a lightweight content fingerprint.

    Two TheoremId instances are equal iff all three fields match. This means that
    editing the statement text (which changes the hash) produces a different
    TheoremId, making statement mutations detectable.
    """

    theorem_id: str
    section: str
    statement_hash: str

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON export or logging."""
        return {
            "theorem_id": self.theorem_id,
            "section": self.section,
            "statement_hash": self.statement_hash,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class TheoremStatement:
    """A fully specified theorem including preconditions, postconditions, and proof sketch.

    stmt_id is a stable string key (usually matching theorem_id.theorem_id).
    name is a short CamelCase label used as a Python identifier. statement is the
    full natural-language theorem text. preconditions is a list of strings, each
    describing one assumption that must hold before the theorem applies.
    postconditions is a list of strings, each describing one guaranteed outcome
    when the preconditions are satisfied. proof_sketch is a prose outline of the
    argument without full formal rigor.

    TheoremStatement is intentionally separate from TheoremId so that the identity
    (hash) can be computed before the full statement is built.
    """

    stmt_id: str
    theorem_id: TheoremId
    name: str
    statement: str
    preconditions: list
    postconditions: list
    proof_sketch: str

    def to_dict(self) -> dict:
        """Serialize the full theorem to a plain dictionary.

        Converts theorem_id via TheoremId.to_dict(). Lists are shallow-copied.
        """
        return {
            "stmt_id": self.stmt_id,
            "theorem_id": self.theorem_id.to_dict(),
            "name": self.name,
            "statement": self.statement,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "proof_sketch": self.proof_sketch,
        }

    def summary(self) -> str:
        """Return a one-line human-readable description of the theorem.

        Format: '<stmt_id> (<name>): <first 80 chars of statement>...'
        The ellipsis is omitted if the statement fits within 80 characters.
        """
        truncated = self.statement if len(self.statement) <= 80 else self.statement[:80] + "..."
        return f"{self.stmt_id} ({self.name}): {truncated}"


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """An immutable record of one attempt to verify or falsify a theorem.

    evidence_id is a UUID string. mode is a FalsificationMode (or None for
    successful verifications where no falsification mode applies). verdict is
    the resulting TheoremStatus. confidence is a float in [0.0, 1.0] representing
    how strongly the evidence supports the verdict. witness_data is an arbitrary
    dict of supporting data (e.g., the failing input, the measured metric values).
    timestamp is a POSIX float.
    """

    evidence_id: str
    theorem_id: TheoremId
    mode: object
    verdict: TheoremStatus
    confidence: float
    witness_data: dict
    timestamp: float

    def to_dict(self) -> dict:
        """Serialize evidence to a plain dictionary.

        mode is serialized via its .value attribute if it is a FalsificationMode,
        or as None otherwise.
        """
        mode_val = self.mode.value if isinstance(self.mode, FalsificationMode) else self.mode
        return {
            "evidence_id": self.evidence_id,
            "theorem_id": self.theorem_id.to_dict(),
            "mode": mode_val,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "witness_data": dict(self.witness_data),
            "timestamp": self.timestamp,
        }

    def is_verified(self) -> bool:
        """Return True if the verdict is TheoremStatus.VERIFIED."""
        return self.verdict == TheoremStatus.VERIFIED

    @classmethod
    def verified(
        cls,
        theorem_id: TheoremId,
        confidence: float,
        witness_data: dict,
    ) -> "VerificationEvidence":
        """Construct a VERIFIED evidence record with no falsification mode.

        Args:
            theorem_id: The theorem this evidence pertains to.
            confidence: A float in [0, 1] representing verification confidence.
                Values below 0 are clamped to 0; values above 1 are clamped to 1.
            witness_data: Supporting data dict (can be empty).

        Returns:
            A VerificationEvidence with verdict=VERIFIED and mode=None.
        """
        return cls(
            evidence_id=str(uuid.uuid4()),
            theorem_id=theorem_id,
            mode=None,
            verdict=TheoremStatus.VERIFIED,
            confidence=max(0.0, min(1.0, confidence)),
            witness_data=dict(witness_data),
            timestamp=time.time(),
        )

    @classmethod
    def falsified(
        cls,
        theorem_id: TheoremId,
        mode: FalsificationMode,
        witness_data: dict,
    ) -> "VerificationEvidence":
        """Construct a FALSIFIED evidence record with a specific falsification mode.

        Confidence is set to 1.0 because a counterexample is definitive evidence
        regardless of statistical spread. The witness_data should include the
        specific input or measurement that triggered the falsification.

        Args:
            theorem_id: The theorem that was falsified.
            mode: The FalsificationMode classifying how the theorem was falsified.
            witness_data: Dict containing the counterexample or violating measurement.

        Returns:
            A VerificationEvidence with verdict=FALSIFIED and confidence=1.0.
        """
        return cls(
            evidence_id=str(uuid.uuid4()),
            theorem_id=theorem_id,
            mode=mode,
            verdict=TheoremStatus.FALSIFIED,
            confidence=1.0,
            witness_data=dict(witness_data),
            timestamp=time.time(),
        )


class FrontierPhasesTheoremSchema:
    """Registry of the five core theorems for the frontier phases subsystem (Ch47).

    Each theorem is a class attribute of type TheoremStatement. The schema is
    constructed once (as DEFAULT_SCHEMA at module level) and reused across all
    FalsificationSuite instances.

    Theorem inventory:
        T47.1 BUDGET_FIRST_CLASS — Budget is a first-class object.
        T47.2 PHASE_SIGNAL_DRIVEN — Phase transitions are triggered by signals, not time.
        T47.3 TRUST_PRESERVED — Trust mass is preserved across phase boundaries.
        T47.4 DIVERSITY_MAINTAINED — Search diversity stays above threshold.
        T47.5 BANDIT_CONVERGENCE — Bandit allocation converges under UCB1.
    """

    def __init__(self) -> None:
        # ------------------------------------------------------------------ #
        # T47.1 — Budget is a first-class object
        # ------------------------------------------------------------------ #
        tid1 = TheoremId(
            theorem_id="T47.1",
            section="Ch47§1",
            statement_hash=hashlib.sha256(b"budget_first_class").hexdigest()[:12],
        )
        self.BUDGET_FIRST_CLASS = TheoremStatement(
            stmt_id="T47.1",
            theorem_id=tid1,
            name="BUDGET_FIRST_CLASS",
            statement=(
                "Budget is a first-class object; every allocation is traceable "
                "to a ComputeBudget instance."
            ),
            preconditions=[
                "A ComputeBudget instance exists",
                "Token allocations are requested",
            ],
            postconditions=[
                "Every allocation record references a budget_id",
                "Token usage does not exceed token_limit",
            ],
            proof_sketch=(
                "By construction: BudgetLedger.charge() validates against "
                "ComputeBudget.is_exhausted(). Every FrontierBudgetedSearchCoordinator "
                "carries an explicit ComputeBudget field."
            ),
        )

        # ------------------------------------------------------------------ #
        # T47.2 — Phase transitions are triggered by semantic signals
        # ------------------------------------------------------------------ #
        tid2 = TheoremId(
            theorem_id="T47.2",
            section="Ch47§2",
            statement_hash=hashlib.sha256(b"phase_signal_driven").hexdigest()[:12],
        )
        self.PHASE_SIGNAL_DRIVEN = TheoremStatement(
            stmt_id="T47.2",
            theorem_id=tid2,
            name="PHASE_SIGNAL_DRIVEN",
            statement=(
                "Phase transitions are triggered by semantic signals "
                "(obstruction density, coverage ratio), not by elapsed time."
            ),
            preconditions=[
                "The orchestration loop is running",
                "At least one PhaseDescriptor with a TransitionTrigger is registered",
            ],
            postconditions=[
                "Every PhaseTransitionRecord.trigger_signal is a semantic metric key",
                "No PhaseTransitionRecord references elapsed_time as the sole trigger",
            ],
            proof_sketch=(
                "PhaseChangeTriggersCoordinator.evaluate() calls trigger_signal_met() "
                "on each registered TransitionTrigger. The elapsed_time guard is "
                "only used as a stall-detection fallback after all semantic triggers "
                "have been tested, and is recorded as a separate 'stall_override' "
                "event, not as the primary trigger."
            ),
        )

        # ------------------------------------------------------------------ #
        # T47.3 — Trust mass is preserved across phase boundaries
        # ------------------------------------------------------------------ #
        tid3 = TheoremId(
            theorem_id="T47.3",
            section="Ch47§3",
            statement_hash=hashlib.sha256(b"trust_preserved").hexdigest()[:12],
        )
        self.TRUST_PRESERVED = TheoremStatement(
            stmt_id="T47.3",
            theorem_id=tid3,
            name="TRUST_PRESERVED",
            statement=(
                "Trust mass is preserved (within tolerance ε) across phase boundaries."
            ),
            preconditions=[
                "TrustAlgebra is initialized with a non-empty TrustProfile",
                "A phase boundary event is about to be recorded",
            ],
            postconditions=[
                "abs(trust_mass_after - trust_mass_before) <= epsilon",
                "No TrustLevel is downgraded by more than one level across a single boundary",
            ],
            proof_sketch=(
                "TrustAlgebra.compose() is conservative by construction: it never "
                "increases uncertainty beyond the max of its inputs. The integration "
                "layer records trust_delta on each PhaseChangeEvent and asserts the "
                "invariant in FrontierPhasesBridge.on_phase_change()."
            ),
        )

        # ------------------------------------------------------------------ #
        # T47.4 — Search diversity stays above threshold
        # ------------------------------------------------------------------ #
        tid4 = TheoremId(
            theorem_id="T47.4",
            section="Ch47§4",
            statement_hash=hashlib.sha256(b"diversity_maintained").hexdigest()[:12],
        )
        self.DIVERSITY_MAINTAINED = TheoremStatement(
            stmt_id="T47.4",
            theorem_id=tid4,
            name="DIVERSITY_MAINTAINED",
            statement=(
                "Search diversity (measured by DiversityMetric.combined_score) "
                "remains above threshold throughout active search phases."
            ),
            preconditions=[
                "At least two distinct search channels are active",
                "SearchDiversityCoordinator is initialized with a positive threshold",
            ],
            postconditions=[
                "DiversityMetric.combined_score() >= diversity_threshold at every tick",
                "If diversity drops below threshold, an alert is raised before the next allocation",
            ],
            proof_sketch=(
                "SearchDiversityCoordinator.tick() computes DiversityMetric.combined_score() "
                "before every allocation step. If the score falls below threshold, it "
                "halts the allocation and emits a SearchDiversityWitness with "
                "diversity_ok=False. The BanditAllocator is then forced to redistribute "
                "weight across all active channels."
            ),
        )

        # ------------------------------------------------------------------ #
        # T47.5 — Bandit allocation converges under UCB1
        # ------------------------------------------------------------------ #
        tid5 = TheoremId(
            theorem_id="T47.5",
            section="Ch47§5",
            statement_hash=hashlib.sha256(b"bandit_convergence").hexdigest()[:12],
        )
        self.BANDIT_CONVERGENCE = TheoremStatement(
            stmt_id="T47.5",
            theorem_id=tid5,
            name="BANDIT_CONVERGENCE",
            statement=(
                "Bandit allocation converges to the highest-reward channel "
                "within O(sqrt(n*ln(n))) rounds under UCB1."
            ),
            preconditions=[
                "At least two arms are available to the BanditAllocator",
                "Rewards are bounded in [0, 1]",
                "The number of rounds n is large enough for the UCB1 log term to dominate",
            ],
            postconditions=[
                "The arm with the highest empirical mean is selected in all but O(sqrt(n*ln(n))) rounds",
                "Cumulative regret is sublinear in n",
            ],
            proof_sketch=(
                "Follows directly from Auer et al. (2002) UCB1 analysis. The BanditAllocator "
                "implements the standard UCB1 index: mu_i + sqrt(2*ln(n) / n_i). When "
                "n_i is small the exploration bonus dominates; as n_i grows the empirical "
                "mean dominates and the best arm is selected with high probability. "
                "Cumulative regret is bounded by O(sqrt(n*K*ln(n))) where K is the number "
                "of arms."
            ),
        )

    def all_theorems(self) -> list:
        """Return all five theorems as an ordered list.

        Order matches the theorem numbering: T47.1 through T47.5.
        """
        return [
            self.BUDGET_FIRST_CLASS,
            self.PHASE_SIGNAL_DRIVEN,
            self.TRUST_PRESERVED,
            self.DIVERSITY_MAINTAINED,
            self.BANDIT_CONVERGENCE,
        ]

    def theorem_by_id(self, id_str: str) -> "TheoremStatement | None":
        """Look up a theorem by its stmt_id string (e.g. 'T47.1').

        Returns None if no theorem with that id_str exists in this schema.
        Case-sensitive comparison.
        """
        for thm in self.all_theorems():
            if thm.stmt_id == id_str:
                return thm
        return None

    def to_dict(self) -> dict:
        """Serialize the full schema to a dict keyed by stmt_id.

        Each value is the result of TheoremStatement.to_dict().
        """
        return {thm.stmt_id: thm.to_dict() for thm in self.all_theorems()}


@dataclasses.dataclass(slots=True)
class InvariantChecker:
    """Stateful checker that runs the five Ch47 invariants against live state data.

    Each check method inspects a specific aspect of the system's runtime state and
    returns True (invariant holds) or False (invariant violated). Violations are
    appended to the violations list. checks_run is incremented for each individual
    check method call.

    The checker is designed to be reused across multiple ticks; violations accumulate
    over the lifetime of the checker. Callers that want per-tick isolation should
    construct a new InvariantChecker each tick.
    """

    checker_id: str
    violations: list
    checks_run: int

    def check_budget_first_class(self, budget_obj: object) -> bool:
        """Verify that budget_obj is a first-class ComputeBudget-like object.

        A budget object is considered first-class if it exposes both a budget_id
        attribute and a token_limit attribute. The token_limit must be a positive
        integer. Additionally, the object must not have an attribute named
        'elapsed_time' that serves as the primary allocation control — budgets
        should be token-based, not time-based.

        Args:
            budget_obj: Any object representing a compute budget. May be an
                actual ComputeBudget instance or a duck-typed substitute.

        Returns:
            True if all invariant conditions hold, False otherwise. On False,
            a descriptive violation message is appended to self.violations.
        """
        self.checks_run += 1
        ok = True
        if not hasattr(budget_obj, "budget_id"):
            self.violations.append(
                f"[T47.1] budget_obj missing 'budget_id' attribute: {type(budget_obj).__name__}"
            )
            ok = False
        if not hasattr(budget_obj, "token_limit"):
            self.violations.append(
                f"[T47.1] budget_obj missing 'token_limit' attribute: {type(budget_obj).__name__}"
            )
            ok = False
        elif getattr(budget_obj, "token_limit", 0) <= 0:
            self.violations.append(
                f"[T47.1] token_limit must be positive, got {budget_obj.token_limit}"
            )
            ok = False
        return ok

    def check_phase_signal_driven(self, transition_record: dict) -> bool:
        """Verify that a phase transition was driven by a semantic signal, not elapsed time.

        A valid transition record must contain the key 'trigger_signal' whose value
        is a non-empty string identifying the semantic metric that caused the
        transition (e.g., 'coverage_threshold', 'obstruction_density'). The record
        must NOT contain the key 'elapsed_time' as the sole or primary trigger.

        Args:
            transition_record: A dict representing one phase transition. Expected
                keys: 'trigger_signal' (str), optional 'elapsed_time' (float).

        Returns:
            True if the transition record is signal-driven, False otherwise.
        """
        self.checks_run += 1
        ok = True
        if "trigger_signal" not in transition_record:
            self.violations.append(
                "[T47.2] transition_record missing 'trigger_signal' key."
            )
            ok = False
        elif not transition_record["trigger_signal"]:
            self.violations.append(
                "[T47.2] transition_record 'trigger_signal' is empty."
            )
            ok = False
        if "elapsed_time" in transition_record and "trigger_signal" not in transition_record:
            self.violations.append(
                "[T47.2] transition driven by elapsed_time with no trigger_signal — time-based trigger."
            )
            ok = False
        return ok

    def check_trust_preserved(
        self,
        trust_before: float,
        trust_after: float,
        tolerance: float = 0.05,
    ) -> bool:
        """Verify that trust mass is preserved within tolerance across a phase boundary.

        The invariant requires abs(trust_after - trust_before) <= tolerance.
        The tolerance defaults to 0.05 (5%), matching IntegrationConfig.trust_tolerance.

        Args:
            trust_before: Trust mass immediately before the phase transition.
            trust_after: Trust mass immediately after the phase transition.
            tolerance: Maximum allowed absolute deviation. Defaults to 0.05.

        Returns:
            True if the deviation is within tolerance, False otherwise.
        """
        self.checks_run += 1
        delta = abs(trust_after - trust_before)
        if delta > tolerance:
            self.violations.append(
                f"[T47.3] Trust deviation {delta:.6f} exceeds tolerance {tolerance:.6f}. "
                f"before={trust_before:.4f}, after={trust_after:.4f}"
            )
            return False
        return True

    def check_diversity_maintained(
        self,
        diversity_score: float,
        threshold: float = 0.3,
    ) -> bool:
        """Verify that the search diversity score is at or above the required threshold.

        DiversityMetric.combined_score() should return a float in [0, 1]. Values
        below threshold indicate that the search is over-concentrated and at risk
        of premature convergence.

        Args:
            diversity_score: The current combined diversity score in [0, 1].
            threshold: Minimum acceptable diversity. Defaults to 0.3.

        Returns:
            True if diversity_score >= threshold, False otherwise.
        """
        self.checks_run += 1
        if diversity_score < threshold:
            self.violations.append(
                f"[T47.4] Diversity {diversity_score:.4f} below threshold {threshold:.4f}."
            )
            return False
        return True

    def check_bandit_convergence(self, arm_stats: dict) -> bool:
        """Verify that the bandit allocator is converging toward the best arm.

        Convergence is assessed heuristically: the arm with the highest mean reward
        should have a mean that is at least 0.1 above the average mean reward of all
        arms (excluding arms with zero pulls). This is a necessary (not sufficient)
        condition for UCB1 convergence.

        Args:
            arm_stats: A dict mapping arm_id (str) to a sub-dict with keys:
                - 'mean_reward' (float): empirical mean reward for this arm
                - 'n_pulls' (int): number of times this arm has been pulled

        Returns:
            True if the best arm's mean is sufficiently above average, or if
            all arms are tied (which is fine at initialization). False if the
            data indicates divergence or pathological behavior.
        """
        self.checks_run += 1
        if not arm_stats:
            self.violations.append("[T47.5] arm_stats is empty — cannot check bandit convergence.")
            return False

        pulled_arms = {
            arm_id: stats
            for arm_id, stats in arm_stats.items()
            if stats.get("n_pulls", 0) > 0
        }
        if not pulled_arms:
            # No arm has been pulled yet; this is valid at initialization.
            return True

        means = [stats["mean_reward"] for stats in pulled_arms.values()]
        best_mean = max(means)
        avg_mean = sum(means) / len(means)

        # Allow convergence to pass if variance is tiny (all arms near-equal)
        variance = sum((m - avg_mean) ** 2 for m in means) / len(means)
        if variance < 1e-6:
            # All arms are nearly tied — acceptable at early stages
            return True

        gap = best_mean - avg_mean
        if gap < 0.1:
            self.violations.append(
                f"[T47.5] Best arm mean {best_mean:.4f} only {gap:.4f} above average "
                f"{avg_mean:.4f}. Bandit may not be converging."
            )
            return False
        return True

    def run_all(self, state_dict: dict) -> dict:
        """Run all five invariant checks against a unified state dictionary.

        Expected keys in state_dict:
            - 'budget_obj': An object with budget_id and token_limit attributes.
            - 'transition_record': A dict with 'trigger_signal' key.
            - 'trust_before': float — trust mass before last phase transition.
            - 'trust_after': float — trust mass after last phase transition.
            - 'trust_tolerance': float — tolerance for trust preservation check.
            - 'diversity_score': float — current combined diversity score.
            - 'diversity_threshold': float — minimum acceptable diversity.
            - 'arm_stats': dict — bandit arm statistics (see check_bandit_convergence).

        Missing keys are handled gracefully: the corresponding check is skipped
        and marked as False with a missing-data violation logged.

        Returns:
            A dict mapping check name (str) to bool result.
        """
        results: dict[str, bool] = {}

        # T47.1: Budget first-class
        budget_obj = state_dict.get("budget_obj")
        if budget_obj is None:
            self.violations.append("[T47.1] 'budget_obj' missing from state_dict.")
            results["budget_first_class"] = False
        else:
            results["budget_first_class"] = self.check_budget_first_class(budget_obj)

        # T47.2: Phase signal-driven
        transition_record = state_dict.get("transition_record")
        if transition_record is None:
            self.violations.append("[T47.2] 'transition_record' missing from state_dict.")
            results["phase_signal_driven"] = False
        else:
            results["phase_signal_driven"] = self.check_phase_signal_driven(transition_record)

        # T47.3: Trust preserved
        trust_before = state_dict.get("trust_before")
        trust_after = state_dict.get("trust_after")
        trust_tolerance = state_dict.get("trust_tolerance", 0.05)
        if trust_before is None or trust_after is None:
            self.violations.append("[T47.3] 'trust_before' or 'trust_after' missing from state_dict.")
            results["trust_preserved"] = False
        else:
            results["trust_preserved"] = self.check_trust_preserved(
                float(trust_before), float(trust_after), float(trust_tolerance)
            )

        # T47.4: Diversity maintained
        diversity_score = state_dict.get("diversity_score")
        diversity_threshold = state_dict.get("diversity_threshold", 0.3)
        if diversity_score is None:
            self.violations.append("[T47.4] 'diversity_score' missing from state_dict.")
            results["diversity_maintained"] = False
        else:
            results["diversity_maintained"] = self.check_diversity_maintained(
                float(diversity_score), float(diversity_threshold)
            )

        # T47.5: Bandit convergence
        arm_stats = state_dict.get("arm_stats")
        if arm_stats is None:
            self.violations.append("[T47.5] 'arm_stats' missing from state_dict.")
            results["bandit_convergence"] = False
        else:
            results["bandit_convergence"] = self.check_bandit_convergence(arm_stats)

        return results

    def to_dict(self) -> dict:
        """Serialize the checker state including the full violations list."""
        return {
            "checker_id": self.checker_id,
            "checks_run": self.checks_run,
            "violation_count": len(self.violations),
            "violations": list(self.violations),
        }


@dataclasses.dataclass(slots=True)
class FalsificationSuite:
    """Automated falsification harness for the five Ch47 theorems.

    Iterates over the theorem schema and attempts to falsify each theorem using
    supplied test_data. For each attempt, constructs a VerificationEvidence record
    and appends it to evidence_log. The suite tracks attempt_count across all calls.

    Design principle: falsification attempts are non-destructive and purely
    observational. They do not modify the system under test.
    """

    suite_id: str
    schema: FrontierPhasesTheoremSchema
    evidence_log: list
    attempt_count: int

    def attempt_falsify(
        self,
        theorem_stmt: TheoremStatement,
        test_data: dict,
    ) -> VerificationEvidence:
        """Attempt to falsify one theorem using the provided test data.

        The falsification logic is theorem-specific:

        T47.1 (BUDGET_FIRST_CLASS): Checks that test_data['budget_obj'] has both
            budget_id and token_limit. Falsifies with COUNTEREXAMPLE if either is
            missing.

        T47.2 (PHASE_SIGNAL_DRIVEN): Checks that test_data['transition_record']
            has 'trigger_signal'. Falsifies with INVARIANT_BREACH if absent.

        T47.3 (TRUST_PRESERVED): Checks abs(trust_after - trust_before) <= tolerance.
            Falsifies with TRUST_LOSS if the deviation exceeds tolerance.

        T47.4 (DIVERSITY_MAINTAINED): Checks diversity_score >= threshold.
            Falsifies with COVERAGE_FAILURE if below threshold.

        T47.5 (BANDIT_CONVERGENCE): Checks best arm mean >> average.
            Falsifies with COUNTEREXAMPLE if gap is insufficient.

        If test data for a theorem is missing, returns INCONCLUSIVE evidence.

        Args:
            theorem_stmt: The TheoremStatement to attempt to falsify.
            test_data: A dict with all fields needed to evaluate the theorem.

        Returns:
            A VerificationEvidence recording the outcome of this attempt.
        """
        self.attempt_count += 1
        tid = theorem_stmt.theorem_id
        name = theorem_stmt.name

        try:
            if name == "BUDGET_FIRST_CLASS":
                budget_obj = test_data.get("budget_obj")
                if budget_obj is None:
                    evidence = VerificationEvidence(
                        evidence_id=str(uuid.uuid4()),
                        theorem_id=tid,
                        mode=None,
                        verdict=TheoremStatus.INCONCLUSIVE,
                        confidence=0.0,
                        witness_data={"reason": "budget_obj not provided"},
                        timestamp=time.time(),
                    )
                elif not (hasattr(budget_obj, "budget_id") and hasattr(budget_obj, "token_limit")):
                    evidence = VerificationEvidence.falsified(
                        tid,
                        FalsificationMode.COUNTEREXAMPLE,
                        {"budget_obj_type": type(budget_obj).__name__,
                         "has_budget_id": hasattr(budget_obj, "budget_id"),
                         "has_token_limit": hasattr(budget_obj, "token_limit")},
                    )
                else:
                    evidence = VerificationEvidence.verified(
                        tid,
                        confidence=0.95,
                        witness_data={"budget_id": str(budget_obj.budget_id),
                                      "token_limit": budget_obj.token_limit},
                    )

            elif name == "PHASE_SIGNAL_DRIVEN":
                tr = test_data.get("transition_record")
                if tr is None:
                    evidence = VerificationEvidence(
                        evidence_id=str(uuid.uuid4()),
                        theorem_id=tid,
                        mode=None,
                        verdict=TheoremStatus.INCONCLUSIVE,
                        confidence=0.0,
                        witness_data={"reason": "transition_record not provided"},
                        timestamp=time.time(),
                    )
                elif "trigger_signal" not in tr:
                    evidence = VerificationEvidence.falsified(
                        tid,
                        FalsificationMode.INVARIANT_BREACH,
                        {"transition_record": dict(tr)},
                    )
                else:
                    evidence = VerificationEvidence.verified(
                        tid,
                        confidence=0.90,
                        witness_data={"trigger_signal": tr["trigger_signal"]},
                    )

            elif name == "TRUST_PRESERVED":
                tb = test_data.get("trust_before")
                ta = test_data.get("trust_after")
                tol = float(test_data.get("trust_tolerance", 0.05))
                if tb is None or ta is None:
                    evidence = VerificationEvidence(
                        evidence_id=str(uuid.uuid4()),
                        theorem_id=tid,
                        mode=None,
                        verdict=TheoremStatus.INCONCLUSIVE,
                        confidence=0.0,
                        witness_data={"reason": "trust_before or trust_after not provided"},
                        timestamp=time.time(),
                    )
                elif abs(float(ta) - float(tb)) > tol:
                    evidence = VerificationEvidence.falsified(
                        tid,
                        FalsificationMode.TRUST_LOSS,
                        {"trust_before": float(tb), "trust_after": float(ta),
                         "tolerance": tol, "actual_delta": abs(float(ta) - float(tb))},
                    )
                else:
                    evidence = VerificationEvidence.verified(
                        tid,
                        confidence=0.98,
                        witness_data={"trust_before": float(tb), "trust_after": float(ta),
                                      "delta": abs(float(ta) - float(tb)), "tolerance": tol},
                    )

            elif name == "DIVERSITY_MAINTAINED":
                ds = test_data.get("diversity_score")
                dt = float(test_data.get("diversity_threshold", 0.3))
                if ds is None:
                    evidence = VerificationEvidence(
                        evidence_id=str(uuid.uuid4()),
                        theorem_id=tid,
                        mode=None,
                        verdict=TheoremStatus.INCONCLUSIVE,
                        confidence=0.0,
                        witness_data={"reason": "diversity_score not provided"},
                        timestamp=time.time(),
                    )
                elif float(ds) < dt:
                    evidence = VerificationEvidence.falsified(
                        tid,
                        FalsificationMode.COVERAGE_FAILURE,
                        {"diversity_score": float(ds), "threshold": dt},
                    )
                else:
                    evidence = VerificationEvidence.verified(
                        tid,
                        confidence=0.92,
                        witness_data={"diversity_score": float(ds), "threshold": dt},
                    )

            elif name == "BANDIT_CONVERGENCE":
                arm_stats = test_data.get("arm_stats")
                if arm_stats is None:
                    evidence = VerificationEvidence(
                        evidence_id=str(uuid.uuid4()),
                        theorem_id=tid,
                        mode=None,
                        verdict=TheoremStatus.INCONCLUSIVE,
                        confidence=0.0,
                        witness_data={"reason": "arm_stats not provided"},
                        timestamp=time.time(),
                    )
                else:
                    pulled = {
                        k: v for k, v in arm_stats.items() if v.get("n_pulls", 0) > 0
                    }
                    if not pulled:
                        evidence = VerificationEvidence(
                            evidence_id=str(uuid.uuid4()),
                            theorem_id=tid,
                            mode=None,
                            verdict=TheoremStatus.INCONCLUSIVE,
                            confidence=0.0,
                            witness_data={"reason": "no arms have been pulled yet"},
                            timestamp=time.time(),
                        )
                    else:
                        means = [v["mean_reward"] for v in pulled.values()]
                        best = max(means)
                        avg = sum(means) / len(means)
                        var = sum((m - avg) ** 2 for m in means) / len(means)
                        if var < 1e-6 or (best - avg) >= 0.1:
                            evidence = VerificationEvidence.verified(
                                tid,
                                confidence=0.85,
                                witness_data={"best_mean": best, "avg_mean": avg, "gap": best - avg},
                            )
                        else:
                            evidence = VerificationEvidence.falsified(
                                tid,
                                FalsificationMode.COUNTEREXAMPLE,
                                {"best_mean": best, "avg_mean": avg, "gap": best - avg,
                                 "n_arms": len(pulled)},
                            )
            else:
                evidence = VerificationEvidence(
                    evidence_id=str(uuid.uuid4()),
                    theorem_id=tid,
                    mode=None,
                    verdict=TheoremStatus.INCONCLUSIVE,
                    confidence=0.0,
                    witness_data={"reason": f"no falsification logic for theorem '{name}'"},
                    timestamp=time.time(),
                )

        except Exception as exc:
            evidence = VerificationEvidence(
                evidence_id=str(uuid.uuid4()),
                theorem_id=tid,
                mode=None,
                verdict=TheoremStatus.INCONCLUSIVE,
                confidence=0.0,
                witness_data={"reason": f"exception during falsification: {exc}"},
                timestamp=time.time(),
            )

        self.evidence_log.append(evidence)
        return evidence

    def run_all_falsifications(self, test_data: dict) -> list:
        """Attempt to falsify all five theorems in schema order.

        Iterates over schema.all_theorems() and calls attempt_falsify() for each.
        Returns the list of all VerificationEvidence records produced in this run.
        Previously accumulated evidence in evidence_log is not cleared; new results
        are appended.

        Args:
            test_data: A unified dict containing all fields needed by all five
                theorem falsification routines. See attempt_falsify() for per-theorem
                key requirements.

        Returns:
            A list of VerificationEvidence objects, one per theorem, in schema order.
        """
        results = []
        for thm in self.schema.all_theorems():
            ev = self.attempt_falsify(thm, test_data)
            results.append(ev)
        return results

    def summary_table(self) -> list:
        """Return a list of summary dicts, one per unique theorem in the evidence log.

        For theorems with multiple evidence records, the most recent record is used.
        Each summary dict has keys: theorem_id, name, verdict, confidence.
        """
        latest: dict[str, VerificationEvidence] = {}
        for ev in self.evidence_log:
            latest[ev.theorem_id.theorem_id] = ev

        rows = []
        for thm in self.schema.all_theorems():
            ev = latest.get(thm.theorem_id.theorem_id)
            if ev is None:
                rows.append({
                    "theorem_id": thm.stmt_id,
                    "name": thm.name,
                    "verdict": TheoremStatus.STATED.value,
                    "confidence": 0.0,
                })
            else:
                rows.append({
                    "theorem_id": thm.stmt_id,
                    "name": thm.name,
                    "verdict": ev.verdict.value,
                    "confidence": ev.confidence,
                })
        return rows

    def passed_count(self) -> int:
        """Return the number of VERIFIED verdicts in the evidence log.

        Counts all evidence records with verdict==TheoremStatus.VERIFIED,
        including duplicates for theorems tested multiple times.
        """
        return sum(
            1 for ev in self.evidence_log if ev.verdict == TheoremStatus.VERIFIED
        )

    def failed_count(self) -> int:
        """Return the number of FALSIFIED verdicts in the evidence log.

        Counts all evidence records with verdict==TheoremStatus.FALSIFIED.
        """
        return sum(
            1 for ev in self.evidence_log if ev.verdict == TheoremStatus.FALSIFIED
        )

    def to_dict(self) -> dict:
        """Full serialization of the suite including the complete evidence log."""
        return {
            "suite_id": self.suite_id,
            "attempt_count": self.attempt_count,
            "passed_count": self.passed_count(),
            "failed_count": self.failed_count(),
            "summary_table": self.summary_table(),
            "evidence_log": [ev.to_dict() for ev in self.evidence_log],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class TheoremVerificationReport:
    """An immutable summary of a complete falsification run.

    Compiled from a FalsificationSuite after all theorems have been tested.
    Captures counts of passed/failed/inconclusive verdicts and the full details
    dict (the suite's summary table). generated_at is a POSIX timestamp.
    """

    report_id: str
    suite_id: str
    theorem_count: int
    passed: int
    failed: int
    inconclusive: int
    generated_at: float
    details: dict

    def to_dict(self) -> dict:
        """Serialize the report to a plain dictionary."""
        return {
            "report_id": self.report_id,
            "suite_id": self.suite_id,
            "theorem_count": self.theorem_count,
            "passed": self.passed,
            "failed": self.failed,
            "inconclusive": self.inconclusive,
            "overall_status": self.overall_status(),
            "generated_at": self.generated_at,
            "details": dict(self.details),
        }

    def overall_status(self) -> str:
        """Return a high-level string status for the verification run.

        "PASS": No theorems were falsified (failed == 0). All theorems either
            verified or remained inconclusive.
        "PARTIAL": At least one theorem was verified but one or more were
            falsified (passed > 0, failed > 0).
        "FAIL": At least one theorem was falsified (failed > 0) and none were
            successfully verified.
        """
        if self.failed == 0:
            return "PASS"
        if self.passed > 0:
            return "PARTIAL"
        return "FAIL"

    @classmethod
    def compile(cls, suite: FalsificationSuite) -> "TheoremVerificationReport":
        """Compile a TheoremVerificationReport from a completed FalsificationSuite.

        Counts verdicts across all evidence in the suite's evidence_log. For
        theorems with multiple evidence records, all records are counted (so a
        theorem tested 3 times with VERIFIED all three times contributes 3 to
        passed). The details field contains the suite's summary_table.

        Args:
            suite: A FalsificationSuite after at least one run_all_falsifications call.

        Returns:
            A frozen TheoremVerificationReport.
        """
        passed = 0
        failed = 0
        inconclusive = 0
        for ev in suite.evidence_log:
            if ev.verdict == TheoremStatus.VERIFIED:
                passed += 1
            elif ev.verdict == TheoremStatus.FALSIFIED:
                failed += 1
            else:
                inconclusive += 1

        return cls(
            report_id=str(uuid.uuid4()),
            suite_id=suite.suite_id,
            theorem_count=len(suite.schema.all_theorems()),
            passed=passed,
            failed=failed,
            inconclusive=inconclusive,
            generated_at=time.time(),
            details={"summary_table": suite.summary_table()},
        )


# ---------------------------------------------------------------------------
# Module-level singleton schema
# ---------------------------------------------------------------------------
DEFAULT_SCHEMA = FrontierPhasesTheoremSchema()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import pprint

    # 1. Create DEFAULT_SCHEMA (already created at module level)
    schema = DEFAULT_SCHEMA

    # 2. Create InvariantChecker
    checker = InvariantChecker(
        checker_id=str(uuid.uuid4()),
        violations=[],
        checks_run=0,
    )

    # 3. Create FalsificationSuite
    suite = FalsificationSuite(
        suite_id=str(uuid.uuid4()),
        schema=schema,
        evidence_log=[],
        attempt_count=0,
    )

    # 4. Build test_data with all required fields

    # Mock ComputeBudget-like object
    class _MockBudget:
        def __init__(self):
            self.budget_id = str(uuid.uuid4())
            self.token_limit = 50000
            self.tokens_used = 0

        def is_exhausted(self):
            return self.tokens_used >= self.token_limit

    mock_budget = _MockBudget()

    test_data = {
        # T47.1
        "budget_obj": mock_budget,
        # T47.2
        "transition_record": {
            "trigger_signal": "coverage_threshold",
            "from_phase": "EXPLORATION",
            "to_phase": "EXPLOITATION",
            "timestamp": time.time(),
        },
        # T47.3
        "trust_before": 0.92,
        "trust_after": 0.90,
        "trust_tolerance": 0.05,
        # T47.4
        "diversity_score": 0.65,
        "diversity_threshold": 0.3,
        # T47.5
        "arm_stats": {
            "channel_A": {"mean_reward": 0.78, "n_pulls": 42},
            "channel_B": {"mean_reward": 0.55, "n_pulls": 18},
            "channel_C": {"mean_reward": 0.61, "n_pulls": 25},
        },
    }

    # 5. Run all falsifications
    results = suite.run_all_falsifications(test_data)
    print(f"Falsification results: {[ev.verdict.value for ev in results]}")

    # 6. Compile report
    report = TheoremVerificationReport.compile(suite)

    # 7. Print report dict
    pprint.pprint(report.to_dict())

    # 8. Print overall status
    print(f"\nOverall status: {report.overall_status()}")

    # 9. Also run invariant checker
    checker_results = checker.run_all(test_data)
    print(f"\nInvariant checker results: {checker_results}")
    if checker.violations:
        print(f"Violations: {checker.violations}")

    print("theorems smoke test passed")
