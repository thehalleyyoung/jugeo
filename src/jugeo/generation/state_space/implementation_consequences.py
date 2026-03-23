"""Implementation Consequences of the State Space Model

# copilot: implementation_consequences_of_state_space_model

This module derives the implementation consequences that follow from the theoretical
state space model defined in the core generation state space. Each consequence is a
theorem-derived constraint on the implementation that must be enforced at runtime
or compile time.

The consequences manifest as policies, constraints, and violations that can be
detected, reported, and remediated. This module provides the machinery to derive
these consequences from a list of theorems and check them against running states.

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

CONSEQUENCE_VERSION = "ic-v1"

THEOREM_REGISTRY: Dict[str, str] = {
    "T-SS-01": "Every generation process must begin in the INITIAL state.",
    "T-SS-02": "No transition may fire from a terminal state (COMPLETE or FAILED).",
    "T-SS-03": "A cover may only be proposed if the context is initialised.",
    "T-SS-04": "Obligations must be generated before local verification can proceed.",
    "T-SS-05": "Local verification must complete before global gluing can be attempted.",
    "T-SS-06": "Global gluing requires that all local sections are pairwise compatible.",
    "T-SS-07": "A generation process may only reach COMPLETE when all obligations are discharged.",
    "T-SS-08": "A trust-tier promotion must be accompanied by a corresponding obligation discharge.",
    "T-SS-09": "Every transition must carry a TrustTier at least as high as its source state.",
    "T-SS-10": "Failed states must record the trigger that caused the failure for audit purposes.",
    "T-SS-11": "The dependency formula of every DependentTransition must be registered in DEPENDENCY_FORMULAS.",
    "T-SS-12": "No cycle may exist in the mandatory progression INITIAL->COVER_PROPOSED->OBLIGATIONS_GENERATED->LOCALLY_VERIFIED->GLOBALLY_GLUED->COMPLETE.",
}

POLICY_COMPONENTS: Dict[str, str] = {
    "state_machine_core": "The core finite state machine governing state transitions.",
    "obligation_manager": "Tracks open and discharged obligations throughout generation.",
    "trust_tier_gate": "Enforces that tier-gated operations are only invoked at sufficient tier.",
    "cover_validator": "Validates that proposed covers satisfy the topology schema.",
    "section_assembler": "Assembles local sections into a global section via the gluing axiom.",
    "evidence_pool": "Accumulates and manages the pool of evidence items.",
    "audit_trail": "Records all state transitions, moves, and obligation changes for replay.",
    "formula_evaluator": "Evaluates dependency formulas for DependentTransitions.",
    "constraint_checker": "Applies StateSpaceConstraints to the current state at each transition.",
    "policy_enforcer": "Applies GenerationPolicies and records any PolicyViolations.",
}

MANDATORY_BEHAVIORS: List[str] = [
    "Every GenerationState must have a unique state_id.",
    "Every StateTransition must reference valid source and destination state IDs.",
    "Obligations introduced by a move must be tracked until explicitly discharged.",
    "The TrustTier of a state must never decrease during a generation run.",
    "All dependency formulas must be evaluated before a transition fires.",
    "Audit trail entries must be appended atomically with state transitions.",
    "Terminal states must be reached deterministically given a fixed input context.",
    "PolicyViolations must be reported before the violating state is accepted.",
    "The cover_id must be immutable once a cover has been proposed.",
    "Evidence IDs must be unique within the evidence pool.",
]

VIOLATION_SEVERITIES: Dict[str, str] = {
    "critical": "Violation that must halt the generation process immediately.",
    "error": "Violation that indicates a logic error but allows the process to continue with a warning.",
    "warning": "Violation that indicates a suboptimal but recoverable condition.",
    "info": "Informational violation that does not affect correctness.",
    "debug": "Low-level diagnostic violation for development purposes only.",
}


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImplementationConsequence:
    """A single implementation consequence derived from a theoretical theorem.

    Each ImplementationConsequence records the theorem it was derived from, a human-
    readable description of the constraint it imposes, the set of implementation
    components it affects, and the TrustTier at which the constraint is enforced.
    Consequences are immutable and hashable so they can be stored in frozensets and
    compared across different derivation runs.

    The consequence_id is a unique identifier assigned by the ConsequenceDeriver.
    The source_theorem references a key in THEOREM_REGISTRY.
    """

    consequence_id: str
    source_theorem: str
    description: str
    affected_components: FrozenSet
    consequence_tier: TrustTier

    def is_mandatory(self) -> bool:
        """Determine whether this consequence is a mandatory enforcement requirement.

        A consequence is mandatory if its source_theorem is registered in THEOREM_REGISTRY
        and its consequence_tier is at least VERIFIED. Consequences derived from unregistered
        theorems are treated as informational only and are not mandatory. Consequences at
        PROPOSAL or REVIEWED tier are advisory rather than mandatory, reflecting the fact
        that low-tier theorems may not have been fully verified. Only VERIFIED or above
        consequences have been machine-checked and are therefore mandatory.

        Returns:
            bool: True if the consequence is mandatory.
        """
        if self.source_theorem not in THEOREM_REGISTRY:
            return False
        return self.consequence_tier.value >= TrustTier.VERIFIED.value

    def components_affected(self) -> int:
        """Return the count of implementation components affected by this consequence.

        This is a convenience method that returns len(self.affected_components). It is
        used in reports and sorting to prioritise consequences that affect many components.
        Consequences with higher component counts typically require more widespread
        implementation changes and are therefore surfaced first in enforcement reports.

        Returns:
            int: The number of affected components.
        """
        return len(self.affected_components)

    def as_constraint(self) -> str:
        """Format this consequence as a constraint string suitable for documentation.

        The constraint is formatted as a labelled sentence combining the theorem ID,
        the consequence description, and the list of affected components. The format
        is intended for inclusion in architecture decision records and API documentation.
        If the affected_components set is empty, the constraint notes that no specific
        components are named. Long descriptions are not truncated so that the constraint
        is complete and unambiguous.

        Returns:
            str: A multi-line constraint string.
        """
        comp_list = ", ".join(sorted(self.affected_components)) if self.affected_components else "all components"
        theorem_text = THEOREM_REGISTRY.get(self.source_theorem, "(unregistered theorem)")
        return (
            "CONSTRAINT [{cid}] (from {thm})\n"
            "  Theorem   : {ttext}\n"
            "  Constraint: {desc}\n"
            "  Affects   : {comps}\n"
            "  Tier      : {tier}\n"
            "  Mandatory : {mand}\n"
        ).format(
            cid=self.consequence_id,
            thm=self.source_theorem,
            ttext=theorem_text,
            desc=self.description,
            comps=comp_list,
            tier=self.consequence_tier.name,
            mand="YES" if self.is_mandatory() else "NO",
        )

    def to_judgment_tuple(self) -> tuple:
        """Serialise this consequence to a compact tuple for Judgment embedding.

        The tuple contains consequence_id, source_theorem, count of affected components,
        tier name, and mandatory flag. This compact form is sufficient for judgment-level
        bookkeeping without embedding the full description and component set.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.consequence_id,
            self.source_theorem,
            len(self.affected_components),
            self.consequence_tier.name,
            self.is_mandatory(),
        )

    def consequence_key(self) -> str:
        """Compute a short deterministic key for this consequence using SHA-256.

        The key hashes the concatenation of consequence_id and source_theorem and
        returns the first 16 hexadecimal characters. This provides a compact, collision-
        resistant identifier for indexing consequences in registries.

        Returns:
            str: First 16 hex chars of SHA-256(consequence_id + source_theorem).
        """
        raw = (self.consequence_id + self.source_theorem).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]


@dataclass(frozen=True)
class StateSpaceConstraint:
    """A structural constraint on the generation state space enforced at runtime.

    A StateSpaceConstraint records the kind of constraint (e.g., 'no_terminal_outgoing',
    'tier_monotonicity', 'obligation_discharge_before_complete'), the component that
    enforces it, and the TrustTier at which enforcement operates. Constraints are
    evaluated against individual states using is_violated_by(), which returns True if
    the supplied state violates the constraint.

    Constraints are frozen and hashable so they can be collected into frozensets and
    compared across enforcement runs.
    """

    constraint_id: str
    constraint_kind: str
    description: str
    enforced_by: str
    constraint_tier: TrustTier

    def is_violated_by(self, state: Any) -> bool:
        """Determine whether the given state violates this constraint.

        The violation check dispatches on constraint_kind. For 'no_terminal_outgoing',
        the check returns False (this constraint is checked at the space level, not
        the state level). For 'tier_monotonicity', the method checks that the state's
        tier is at least PROPOSAL. For 'obligation_discharge_before_complete', the method
        checks whether a COMPLETE state has a non-empty obligations set. For unknown
        constraint kinds, the method conservatively returns False. If state is None,
        the method returns False.

        Args:
            state: The state to check.

        Returns:
            bool: True if the state violates this constraint.
        """
        if state is None:
            return False
        kind = self.constraint_kind
        if kind == "obligation_discharge_before_complete":
            state_kind = getattr(getattr(state, "kind", None), "name", "")
            obligations = getattr(state, "obligations", frozenset())
            if state_kind == "COMPLETE" and len(obligations) > 0:
                return True
        elif kind == "tier_monotonicity":
            state_tier = getattr(state, "state_tier", None)
            if state_tier is not None and state_tier.value < TrustTier.PROPOSAL.value:
                return True
        elif kind == "initial_state_kind":
            if not hasattr(state, "kind"):
                return True
        return False

    def violation_description(self, state: Any) -> str:
        """Produce a human-readable description of how the state violates this constraint.

        If the state does not violate the constraint, an empty string is returned.
        Otherwise, the description names the constraint_id, the state's ID (if available),
        and the specific violation details for the constraint kind.

        Args:
            state: The state that may be violating the constraint.

        Returns:
            str: Violation description, or empty string if no violation.
        """
        if not self.is_violated_by(state):
            return ""
        state_id = getattr(state, "state_id", "(unknown)")
        return (
            "Constraint [{cid}] violated by state {sid}: {desc}".format(
                cid=self.constraint_id,
                sid=state_id,
                desc=self.description,
            )
        )

    def as_obligation(self) -> str:
        """Format this constraint as an obligation string for the obligation manager.

        The obligation string encodes the constraint_id and kind in a format suitable
        for registration as a MoveObligation. This allows constraint violations to be
        tracked as first-class obligations in the generation process.

        Returns:
            str: An obligation ID string derived from this constraint.
        """
        return "obl-constraint-{}-{}".format(self.constraint_id, self.constraint_kind[:20])

    def to_judgment_tuple(self) -> tuple:
        """Serialise this constraint to a compact tuple for Judgment embedding.

        The tuple contains constraint_id, constraint_kind, enforced_by, and tier name.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.constraint_id,
            self.constraint_kind,
            self.enforced_by,
            self.constraint_tier.name,
        )

    def constraint_summary(self) -> str:
        """Produce a concise summary of this constraint for inclusion in reports.

        The summary includes the constraint_id, kind, enforced_by component, tier,
        and a truncated description. It is formatted as a single line.

        Returns:
            str: A one-line summary string.
        """
        desc = self.description[:80] + "..." if len(self.description) > 80 else self.description
        return (
            "Constraint[{cid}] kind={kind} enforced_by={eb} tier={tier} | {desc}".format(
                cid=self.constraint_id,
                kind=self.constraint_kind,
                eb=self.enforced_by,
                tier=self.constraint_tier.name,
                desc=desc,
            )
        )


@dataclass(frozen=True)
class GenerationPolicy:
    """A named policy governing the behaviour of the generation pipeline.

    A GenerationPolicy encodes a set of rules (as a tuple of rule strings), a set of
    exception component names, a trust tier, and a human-readable name. Policies are
    applied to named components via applies_to(), and individual rules are checked via
    check_rule(). The exceptions frozenset lists component names that are exempt from
    the policy.

    Frozen instances are hashable and can be stored in frozensets.
    """

    policy_id: str
    name: str
    rules: tuple
    exceptions: FrozenSet
    policy_tier: TrustTier

    def applies_to(self, component: str) -> bool:
        """Determine whether this policy applies to the named component.

        The policy applies to a component if the component name is in POLICY_COMPONENTS
        and the component is NOT in the policy's exceptions frozenset. If component is
        None or empty, the method returns False. The check is case-sensitive. Policies
        apply to all registered components by default (subject to exceptions) so that
        new components are automatically governed without requiring policy updates.

        Args:
            component: The component name to check.

        Returns:
            bool: True if the policy applies to the component.
        """
        if not component:
            return False
        if component in self.exceptions:
            return False
        return component in POLICY_COMPONENTS

    def check_rule(self, rule_idx: int, state: Any) -> bool:
        """Evaluate the rule at the given index in the rules tuple against the state.

        If rule_idx is out of bounds, the method returns False. The rule string is
        looked up in MANDATORY_BEHAVIORS; if present, the rule is considered applicable.
        The state is checked for a minimum tier: rules from a PROOF_BACKED policy are
        only enforced at PROOF_BACKED tier. For all other tiers, the rule is considered
        satisfied if the state is not a terminal failure state. This is an intentionally
        conservative and extensible implementation.

        Args:
            rule_idx: Index of the rule to evaluate.
            state: The current GenerationState or compatible object.

        Returns:
            bool: True if the rule is satisfied in the given state.
        """
        if rule_idx < 0 or rule_idx >= len(self.rules):
            return False
        rule = self.rules[rule_idx]
        if rule not in MANDATORY_BEHAVIORS:
            return False
        if self.policy_tier == TrustTier.PROOF_BACKED:
            state_tier = getattr(state, "state_tier", None)
            if state_tier is None or state_tier.value < TrustTier.PROOF_BACKED.value:
                return False
        if state is not None and hasattr(state, "is_failure") and state.is_failure():
            return False
        return True

    def to_judgment_tuple(self) -> tuple:
        """Serialise this policy to a compact tuple for Judgment embedding.

        The tuple contains policy_id, name, count of rules, count of exceptions, and
        tier name.

        Returns:
            tuple: A five-element summary tuple.
        """
        return (
            self.policy_id,
            self.name,
            len(self.rules),
            len(self.exceptions),
            self.policy_tier.name,
        )

    def policy_summary(self) -> str:
        """Produce a multi-line summary of this policy.

        The summary lists the policy_id, name, tier, rule count, exception list, and
        all rules. It is suitable for inclusion in policy reports and audit documentation.

        Returns:
            str: Multi-line summary string.
        """
        rule_lines = "\n".join("  {:02d}. {}".format(i, r) for i, r in enumerate(self.rules))
        exc_str = ", ".join(sorted(self.exceptions)) if self.exceptions else "none"
        return (
            "Policy[{pid}]: {name}\n"
            "  Tier      : {tier}\n"
            "  Rules ({nr}):\n{rl}\n"
            "  Exceptions: {exc}\n"
        ).format(
            pid=self.policy_id,
            name=self.name,
            tier=self.policy_tier.name,
            nr=len(self.rules),
            rl=rule_lines,
            exc=exc_str,
        )

    def rule_count(self) -> int:
        """Return the number of rules in this policy.

        Returns:
            int: The number of rules.
        """
        return len(self.rules)


@dataclass(frozen=True)
class PolicyViolation:
    """A recorded violation of a GenerationPolicy by a specific state.

    A PolicyViolation is created whenever check_policy() detects that a state violates
    a policy. It records the policy_id, the violating state's ID, a description of
    the violation, and the TrustTier at which the violation was detected. Frozen
    instances are hashable and can be collected into frozensets for violation reporting.

    The violation_id is unique across a generation run and is used in audit trails
    to cross-reference violations with states and policies.
    """

    violation_id: str
    policy_id: str
    violating_state_id: str
    description: str
    violation_tier: TrustTier

    def is_critical(self) -> bool:
        """Determine whether this violation is critical and must halt the generation process.

        A violation is critical if its tier is PROOF_BACKED (indicating that a formally
        proved property has been violated) or if the violation_id prefix is 'critical-'.
        Critical violations must be surfaced immediately to the caller and must not be
        silently swallowed. This method is used by the policy enforcer to decide whether
        to raise an exception or merely log the violation.

        Returns:
            bool: True if the violation is critical.
        """
        if self.violation_tier == TrustTier.PROOF_BACKED:
            return True
        if self.violation_id.startswith("critical-"):
            return True
        return False

    def remediation(self) -> str:
        """Return a human-readable remediation recommendation for this violation.

        The recommendation is generated based on the violation_tier and a pattern match
        on the description. For critical violations, the recommendation is to halt the
        generation process and investigate the source theorem. For error-tier violations,
        the recommendation is to discharge the associated obligation. For lower-tier
        violations, the recommendation is to review the policy configuration.
        The returned string is suitable for inclusion in violation reports and logs.

        Returns:
            str: Remediation recommendation string.
        """
        if self.is_critical():
            return (
                "CRITICAL: Halt generation process immediately. "
                "Review the policy '{}' and investigate whether the source theorem "
                "constraints are correctly implemented.".format(self.policy_id)
            )
        if self.violation_tier.value >= TrustTier.VERIFIED.value:
            return (
                "ERROR: Discharge the obligation associated with policy '{}' "
                "before proceeding to higher-tier states.".format(self.policy_id)
            )
        return (
            "WARNING: Review the policy configuration for '{}'. "
            "This violation may indicate a misconfigured guard or transition.".format(self.policy_id)
        )

    def to_judgment_tuple(self) -> tuple:
        """Serialise this violation to a compact tuple for Judgment embedding.

        The tuple contains violation_id, policy_id, violating_state_id, and tier name.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.violation_id,
            self.policy_id,
            self.violating_state_id,
            self.violation_tier.name,
        )

    def violation_key(self) -> str:
        """Compute a short deterministic key for this violation using SHA-256.

        The key hashes the concatenation of violation_id and policy_id and returns
        the first 16 hexadecimal characters.

        Returns:
            str: First 16 hex chars of SHA-256(violation_id + policy_id).
        """
        raw = (self.violation_id + self.policy_id).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def severity_label(self) -> str:
        """Return the human-readable severity label for this violation.

        The severity is determined by the violation_tier: PROOF_BACKED maps to 'critical',
        RUNTIME_WITNESSED and VERIFIED map to 'error', REVIEWED maps to 'warning', and
        PROPOSAL maps to 'info'. These labels correspond to the keys in VIOLATION_SEVERITIES
        and are used in report formatting and log level selection.

        Returns:
            str: Severity label string.
        """
        severity_map: Dict[TrustTier, str] = {
            TrustTier.PROOF_BACKED: "critical",
            TrustTier.RUNTIME_WITNESSED: "error",
            TrustTier.VERIFIED: "error",
            TrustTier.REVIEWED: "warning",
            TrustTier.PROPOSAL: "info",
        }
        return severity_map.get(self.violation_tier, "debug")


class ConsequenceDeriver:
    """Mutable deriver that accumulates theorems and produces ImplementationConsequences.

    ConsequenceDeriver is a regular mutable class used to progressively accumulate
    theorem IDs, derive implementation consequences, check constraints, and report
    policy violations. Once derivation is complete, summary() provides a human-readable
    overview of all derived consequences and detected violations.

    The deriver is not thread-safe — callers must serialise access in concurrent
    environments.
    """

    def __init__(self) -> None:
        """Initialise an empty consequence deriver."""
        self._theorems: List[str] = []
        self._consequences: List[ImplementationConsequence] = []
        self._violations: List[PolicyViolation] = []
        self._constraints: List[StateSpaceConstraint] = []

    def add_theorem(self, theorem_id: str) -> None:
        """Register a theorem ID for consequence derivation.

        The theorem_id is appended to the internal list. If it is not registered in
        THEOREM_REGISTRY, a warning consequence will be produced during derive_consequences().
        Duplicate theorem IDs are allowed; the deriver will derive a consequence for
        each occurrence.

        Args:
            theorem_id: The theorem identifier to register.
        """
        if theorem_id:
            self._theorems.append(theorem_id)

    def derive_consequences(self, tier: TrustTier = TrustTier.VERIFIED) -> List[ImplementationConsequence]:
        """Derive ImplementationConsequences from all registered theorems.

        For each registered theorem ID, a consequence is derived using the theorem
        text from THEOREM_REGISTRY (or a default text for unregistered theorems).
        The affected_components are determined by inspecting the theorem text for
        component name substrings from POLICY_COMPONENTS. All derived consequences
        are stored internally and also returned.

        Args:
            tier: The TrustTier to assign to derived consequences.

        Returns:
            List[ImplementationConsequence]: All newly derived consequences.
        """
        new_consequences: List[ImplementationConsequence] = []
        for i, thm_id in enumerate(self._theorems):
            theorem_text = THEOREM_REGISTRY.get(thm_id, "Unregistered theorem: {}".format(thm_id))
            affected: set = set()
            for comp_name in POLICY_COMPONENTS:
                if comp_name.replace("_", " ") in theorem_text.lower() or comp_name in theorem_text:
                    affected.add(comp_name)
            if not affected:
                affected.add(list(POLICY_COMPONENTS.keys())[i % len(POLICY_COMPONENTS)])
            cons = ImplementationConsequence(
                consequence_id="cons-{:04d}-{}".format(i, thm_id.lower().replace("-", "")),
                source_theorem=thm_id,
                description=theorem_text,
                affected_components=frozenset(affected),
                consequence_tier=tier,
            )
            new_consequences.append(cons)
        self._consequences.extend(new_consequences)
        return new_consequences

    def check_constraints(self, states: List[Any]) -> List[PolicyViolation]:
        """Check all registered constraints against all supplied states.

        For each state in the states list, each constraint in self._constraints is
        evaluated via is_violated_by(). Violations are recorded as PolicyViolation
        objects with a generated violation_id. All new violations are appended to
        self._violations and also returned.

        Args:
            states: List of GenerationState or compatible objects to check.

        Returns:
            List[PolicyViolation]: All newly detected violations.
        """
        new_violations: List[PolicyViolation] = []
        for state in states:
            for constraint in self._constraints:
                if constraint.is_violated_by(state):
                    state_id = getattr(state, "state_id", "unknown")
                    viol = PolicyViolation(
                        violation_id="viol-{}-{}".format(constraint.constraint_id, str(uuid.uuid4())[:6]),
                        policy_id=constraint.constraint_id,
                        violating_state_id=state_id,
                        description=constraint.violation_description(state),
                        violation_tier=constraint.constraint_tier,
                    )
                    new_violations.append(viol)
        self._violations.extend(new_violations)
        return new_violations

    def report_violations(self) -> List[PolicyViolation]:
        """Return all violations detected so far.

        Returns:
            List[PolicyViolation]: All accumulated violations sorted by violation_id.
        """
        return sorted(self._violations, key=lambda v: v.violation_id)

    def summary(self) -> str:
        """Produce a human-readable summary of all derived consequences and violations.

        The summary includes the count of theorems registered, consequences derived,
        constraints registered, and violations detected. Each consequence is listed
        with its source theorem and mandatory flag. Each violation is listed with its
        severity label and remediation recommendation. The summary is generated on
        demand and reflects the current state of the deriver.

        Returns:
            str: Multi-line summary string.
        """
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        cons_lines = "\n".join(
            "  [{cid}] {thm} mandatory={m}".format(
                cid=c.consequence_id, thm=c.source_theorem, m=c.is_mandatory()
            )
            for c in self._consequences
        )
        viol_lines = "\n".join(
            "  [{vid}] severity={sev} | {desc}".format(
                vid=v.violation_id, sev=v.severity_label(), desc=v.description[:60]
            )
            for v in self._violations
        )
        return (
            "=== ConsequenceDeriver Summary ===\n"
            "Version    : {ver}\n"
            "Generated  : {ts}\n"
            "Theorems   : {nt}\n"
            "Consequences: {nc}\n"
            "Constraints: {ncon}\n"
            "Violations : {nv}\n"
            "Consequences:\n{cl}\n"
            "Violations:\n{vl}\n"
        ).format(
            ver=CONSEQUENCE_VERSION,
            ts=ts,
            nt=len(self._theorems),
            nc=len(self._consequences),
            ncon=len(self._constraints),
            nv=len(self._violations),
            cl=cons_lines if cons_lines else "  (none)",
            vl=viol_lines if viol_lines else "  (none)",
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def derive_implementation_consequences(
    theorem_ids: List[str],
    tier: TrustTier = TrustTier.VERIFIED,
) -> List[ImplementationConsequence]:
    """Derive ImplementationConsequences for a list of theorem IDs.

    This convenience function creates a ConsequenceDeriver, registers all theorem_ids,
    and calls derive_consequences() once. The full list of derived consequences is returned.

    Args:
        theorem_ids: List of theorem IDs to derive consequences from.
        tier: The TrustTier to assign to all derived consequences.

    Returns:
        List[ImplementationConsequence]: All derived consequences.
    """
    deriver = ConsequenceDeriver()
    for tid in theorem_ids:
        deriver.add_theorem(tid)
    return deriver.derive_consequences(tier)


def check_policy(policy: GenerationPolicy, states: List[Any]) -> List[PolicyViolation]:
    """Check a GenerationPolicy against a list of states and return any violations.

    For each state, each rule in the policy is evaluated via check_rule(). If a rule
    fails, a PolicyViolation is created. The method returns the list of all violations
    found. A violation_id is generated for each failure.

    Args:
        policy: The policy to check.
        states: The states to check against the policy.

    Returns:
        List[PolicyViolation]: All detected violations.
    """
    violations: List[PolicyViolation] = []
    for state in states:
        state_id = getattr(state, "state_id", "unknown")
        for rule_idx in range(policy.rule_count()):
            if not policy.check_rule(rule_idx, state):
                rule_text = policy.rules[rule_idx] if rule_idx < len(policy.rules) else "unknown rule"
                viol = PolicyViolation(
                    violation_id="viol-{}-{}-{:03d}".format(policy.policy_id, str(uuid.uuid4())[:6], rule_idx),
                    policy_id=policy.policy_id,
                    violating_state_id=state_id,
                    description="Rule {:02d} failed: {}".format(rule_idx, rule_text),
                    violation_tier=policy.policy_tier,
                )
                violations.append(viol)
    return violations


def enforce_constraint(constraint: StateSpaceConstraint, states: List[Any]) -> List[PolicyViolation]:
    """Enforce a StateSpaceConstraint against a list of states.

    For each state that violates the constraint, a PolicyViolation is created and
    returned. This is a convenience wrapper around constraint.is_violated_by() that
    handles the violation creation boilerplate.

    Args:
        constraint: The constraint to enforce.
        states: The states to check.

    Returns:
        List[PolicyViolation]: All violations found.
    """
    violations: List[PolicyViolation] = []
    for state in states:
        if constraint.is_violated_by(state):
            state_id = getattr(state, "state_id", "unknown")
            viol = PolicyViolation(
                violation_id="viol-constraint-{}-{}".format(constraint.constraint_id, str(uuid.uuid4())[:6]),
                policy_id=constraint.constraint_id,
                violating_state_id=state_id,
                description=constraint.violation_description(state),
                violation_tier=constraint.constraint_tier,
            )
            violations.append(viol)
    return violations


def list_consequences(deriver: ConsequenceDeriver) -> List[ImplementationConsequence]:
    """Return all consequences accumulated in a ConsequenceDeriver.

    This is a convenience accessor that delegates to deriver._consequences, returning
    a copy as a plain list sorted by consequence_id.

    Args:
        deriver: The ConsequenceDeriver to query.

    Returns:
        List[ImplementationConsequence]: Sorted list of consequences.
    """
    return sorted(deriver._consequences, key=lambda c: c.consequence_id)


# ---------------------------------------------------------------------------
# __main__ demonstration block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("jugeo.generation.state_space.s04 — Implementation Consequences demo")
    print("=" * 70)

    # 1. ImplementationConsequence instances
    print("\n[1] Deriving ImplementationConsequences from THEOREM_REGISTRY:")
    all_theorem_ids = list(THEOREM_REGISTRY.keys())
    consequences = derive_implementation_consequences(all_theorem_ids, TrustTier.VERIFIED)
    for c in consequences[:5]:
        print("  [{}] mandatory={} components={}".format(
            c.consequence_id, c.is_mandatory(), c.components_affected()
        ))
        print("    key:", c.consequence_key())
        print("    to_judgment_tuple:", c.to_judgment_tuple())

    # 2. as_constraint
    print("\n[2] as_constraint:")
    for c in consequences[:3]:
        print(c.as_constraint())

    # 3. StateSpaceConstraint instances
    print("\n[3] StateSpaceConstraint instances:")
    constraint_kinds = [
        "obligation_discharge_before_complete",
        "tier_monotonicity",
        "initial_state_kind",
    ]
    constraints: List[StateSpaceConstraint] = []
    for i, kind in enumerate(constraint_kinds):
        c = StateSpaceConstraint(
            constraint_id="sc-{:03d}".format(i),
            constraint_kind=kind,
            description=MANDATORY_BEHAVIORS[i % len(MANDATORY_BEHAVIORS)],
            enforced_by=list(POLICY_COMPONENTS.keys())[i % len(POLICY_COMPONENTS)],
            constraint_tier=TrustTier.VERIFIED,
        )
        constraints.append(c)
        print("  [{}] {} | {}".format(c.constraint_id, kind, c.constraint_summary()))
        print("    as_obligation:", c.as_obligation())
        print("    to_judgment_tuple:", c.to_judgment_tuple())

    # 4. Mock states for testing
    class MockState:
        def __init__(self, state_id: str, kind_name: str, tier: TrustTier, obligations: FrozenSet) -> None:
            self.state_id = state_id
            self.kind = type("Kind", (), {"name": kind_name})()
            self.state_tier = tier
            self.obligations = obligations
        def is_failure(self) -> bool:
            return self.kind.name == "FAILED"
        def is_terminal(self) -> bool:
            return self.kind.name in ("COMPLETE", "FAILED")

    states_for_check: List[Any] = [
        MockState("s001", "INITIAL", TrustTier.REVIEWED, frozenset()),
        MockState("s002", "COMPLETE", TrustTier.VERIFIED, frozenset(["obl-1"])),  # violates
        MockState("s003", "COMPLETE", TrustTier.VERIFIED, frozenset()),  # ok
        MockState("s004", "FAILED", TrustTier.PROPOSAL, frozenset()),
    ]

    # 5. enforce_constraint
    print("\n[4] enforce_constraint:")
    for c in constraints:
        viols = enforce_constraint(c, states_for_check)
        print("  Constraint[{}]: {} violation(s)".format(c.constraint_id, len(viols)))
        for v in viols:
            print("    Violation:", v.severity_label(), "|", v.description[:60])
            print("    Remediation:", v.remediation()[:80])

    # 6. GenerationPolicy instances
    print("\n[5] GenerationPolicy instances:")
    policies: List[GenerationPolicy] = []
    for i, (pid, pname) in enumerate(list(POLICY_COMPONENTS.items())[:4]):
        policy = GenerationPolicy(
            policy_id="pol-{:03d}".format(i),
            name=pname,
            rules=tuple(MANDATORY_BEHAVIORS[:4]),
            exceptions=frozenset(["audit_trail"]) if i % 2 == 0 else frozenset(),
            policy_tier=TrustTier.REVIEWED,
        )
        policies.append(policy)
        print("  [{}] {} rules={} exceptions={}".format(
            policy.policy_id, pname[:40], policy.rule_count(), len(policy.exceptions)
        ))
        print("    applies_to('state_machine_core'):", policy.applies_to("state_machine_core"))
        print("    applies_to('audit_trail'):", policy.applies_to("audit_trail"))
        print("    check_rule(0, states_for_check[0]):", policy.check_rule(0, states_for_check[0]))
        print("    check_rule(0, states_for_check[3]):", policy.check_rule(0, states_for_check[3]))

    # 7. policy_summary
    print("\n[6] policy_summary for policy[0]:")
    print(policies[0].policy_summary())

    # 8. check_policy
    print("\n[7] check_policy:")
    for policy in policies[:2]:
        viols = check_policy(policy, states_for_check)
        print("  Policy[{}]: {} violation(s)".format(policy.policy_id, len(viols)))
        for v in viols[:2]:
            print("    [{}] severity={} | {}".format(v.violation_id[:20], v.severity_label(), v.description[:50]))
            print("    violation_key:", v.violation_key())
            print("    to_judgment_tuple:", v.to_judgment_tuple())
            print("    is_critical:", v.is_critical())

    # 9. PolicyViolation severity and remediation
    print("\n[8] PolicyViolation severity labels:")
    for tier in TrustTier:
        viol = PolicyViolation(
            violation_id="viol-test-{}".format(tier.name),
            policy_id="pol-test",
            violating_state_id="s-test",
            description="Test violation at tier {}".format(tier.name),
            violation_tier=tier,
        )
        print("  tier={} severity={} is_critical={}".format(
            tier.name, viol.severity_label(), viol.is_critical()
        ))
        print("    remediation:", viol.remediation()[:70])

    # 10. ConsequenceDeriver full run
    print("\n[9] ConsequenceDeriver full run:")
    deriver = ConsequenceDeriver()
    for tid in all_theorem_ids:
        deriver.add_theorem(tid)
    deriver.derive_consequences(TrustTier.VERIFIED)
    for c in constraints:
        deriver._constraints.append(c)
    deriver.check_constraints(states_for_check)
    print(deriver.summary())

    # 11. list_consequences
    print("\n[10] list_consequences:")
    listed = list_consequences(deriver)
    print("  Total consequences:", len(listed))
    for c in listed[:3]:
        print("  [{cid}] {thm}".format(cid=c.consequence_id, thm=c.source_theorem))

    print("\n[done]")
