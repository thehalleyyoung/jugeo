"""
implementation_consequences.py
====================================
How theorem ecology shapes system design — implementation consequences.

# copilot: theorem-ecology/implementation-consequences — encodes how ecology
# metrics (reuse, citation depth, coverage) drive concrete design decisions.

Judgment schema (8-tuple):
    (c, φ, A, E, O, B, T, Π)
    c  = context        module/sub-system being judged
    φ  = formula        formal property being asserted
    A  = authority      agent/process that issues the judgment
    E  = evidence       empirical or proof artefacts backing the assertion
    O  = obligations    actions that must remain satisfied
    B  = budget         resource budget for enforcement (float, arbitrary units)
    T  = trust_tier     TrustTier level
    Π  = proof_chain    ordered verification steps

Design Principles
=================
All value objects are frozen dataclasses with slots=True for memory efficiency
and guaranteed immutability.  No boolean judgments are issued — every verdict
is expressed through the richer TrustTier ordinal or a structured dataclass.

Ecology Metrics → Design Decisions
===================================
The core thesis of this module is that three ecology metrics mechanically drive
specific architecture decisions:

  reuse_ratio      (high ≥ 0.6)  → stable shared library; expose versioned API
  reuse_ratio      (low  < 0.4)  → refactor duplicate theorems; add shared layer
  citation_depth   (deep > 5)   → introduce abstraction layers; cap import depth
  coverage         (low  < 0.6)  → mandate theorem-backed tests; add CI gate
  dependency_fan_out (high > 12) → apply DIP; introduce interface abstractions
  theorem_count    (> 50, low-reuse) → audit and consolidate theorem taxonomy
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Optional jugeo imports — gracefully degrade when run standalone
# ---------------------------------------------------------------------------
try:
    from jugeo.core.context import JugeoContext          # type: ignore
except ImportError:
    JugeoContext = None  # type: ignore

try:
    from jugeo.ideation.theorem_ecologies.ecology_modeling import BaseEcology  # type: ignore
except ImportError:
    BaseEcology = None  # type: ignore

try:
    from jugeo.ideation.theorem_ecologies.ecological_metrics_reuse_breadth_c import EcologyMetrics  # type: ignore
except ImportError:
    EcologyMetrics = None  # type: ignore

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import datetime
import uuid
import enum
import math
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple


# ============================================================================
# Helper functions
# ============================================================================

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with microsecond precision."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a collision-resistant UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into the closed interval [lo, hi]."""
    return max(lo, min(hi, value))


def _pct(numerator: float, denominator: float) -> float:
    """Safe percentage: return 0.0 when *denominator* is zero."""
    if denominator == 0.0:
        return 0.0
    return (numerator / denominator) * 100.0


def _truncate(text: str, max_len: int = 120) -> str:
    """Truncate *text* to *max_len* characters, appending '…' when cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _fmt_bullets(items: Sequence[str], indent: int = 4) -> str:
    """Format a sequence as an indented bullet list."""
    pad = " " * indent
    return "\n".join(f"{pad}• {item}" for item in items) if items else f"{' ' * indent}(none)"


def _parse_numeric_condition(condition_str: str, values: Dict[str, float]) -> bool:
    """Evaluate a simple numeric condition string against *values*.

    Supports expressions of the form  ``metric op threshold`` where op is one
    of ``>=``, ``<=``, ``>``, ``<``, ``==``, ``!=``.  Multiple clauses
    joined by ``AND`` are all required to hold.  Unknown metrics are skipped.

    Parameters
    ----------
    condition_str : str
        Expression such as ``"reuse_ratio < 0.4 AND citation_depth > 5"``.
    values : dict[str, float]
        Metric name → current value mapping.

    Returns
    -------
    bool
        True when all evaluable sub-expressions hold; False otherwise.
        Returns False when no sub-expression could be evaluated.
    """
    pattern = re.compile(r"(\w+)\s*(>=|<=|>|<|==|!=)\s*([\d.]+)")
    results: List[bool] = []
    for m in pattern.finditer(condition_str):
        metric, op, thr = m.group(1), m.group(2), float(m.group(3))
        if metric not in values:
            continue
        val = values[metric]
        lookup = {
            ">=": val >= thr,
            "<=": val <= thr,
            ">":  val > thr,
            "<":  val < thr,
            "==": math.isclose(val, thr, rel_tol=1e-6),
            "!=": not math.isclose(val, thr, rel_tol=1e-6),
        }
        results.append(lookup[op])
    return all(results) if results else False


# ============================================================================
# TrustTier
# ============================================================================

class TrustTier(enum.IntEnum):
    """Ordered trust levels for ecology-derived judgments.

    Levels represent stricter validation gates:

    PROPOSAL
        Raw draft consequence; no independent review.  Must not gate
        production deployments.

    REVIEWED
        At least one domain-expert review confirmed plausibility.
        Suitable for prototyping and early integration.

    VERIFIED
        Automated analysis (type-checking, lint, unit tests) confirms
        correctness within stated scope.

    RUNTIME_WITNESSED
        Constraint observed to hold across at least one complete
        system-level test run under realistic load.

    PROOF_BACKED
        Formal proof (or mechanically checked certificate) guarantees
        the property for all inputs within the stated domain.
        Required for safety-critical obligations.
    """

    PROPOSAL          = 0
    REVIEWED          = 1
    VERIFIED          = 2
    RUNTIME_WITNESSED = 3
    PROOF_BACKED      = 4

    def label(self) -> str:
        """Human-readable label for display in reports."""
        return self.name.replace("_", " ").title()

    def can_gate_production(self) -> bool:
        """Return True for tiers strong enough to gate a production deployment."""
        return self >= TrustTier.RUNTIME_WITNESSED

    def next_tier(self) -> "TrustTier":
        """Return the next higher tier, or self if already at maximum."""
        members = list(TrustTier)
        idx = members.index(self)
        return members[idx + 1] if idx + 1 < len(members) else self


# ============================================================================
# ConsequenceJudgment   — the core 8-tuple
# ============================================================================

@dataclass(frozen=True, slots=True)
class ConsequenceJudgment:
    """Immutable 8-tuple judgment encoding an ecology-derived consequence.

    The tuple schema is  (c, φ, A, E, O, B, T, Π):

    context : str
        Module, sub-system, or component this judgment applies to.
    formula : str
        Formal property or invariant being asserted.  May be a logical
        expression, metric bound, or prose constraint description.
    authority : str
        Agent or process that issued the judgment
        (e.g. "EcologyPolicy", "human:alice", "prover:coq").
    evidence : Tuple[str, ...]
        Ordered evidence artefacts (file hashes, test IDs, CI URLs,
        proof certificates) that justify the judgment.
    obligations : Tuple[str, ...]
        Conditions that *must* remain satisfied to keep the judgment valid.
        Violating an obligation invalidates the judgment.
    budget : float
        Maximum resource cost allowed for enforcement (arbitrary units).
    trust_tier : TrustTier
        Current confidence level of this judgment.
    proof_chain : Tuple[str, ...]
        Ordered verification steps executed to reach *trust_tier*.
    """

    context:     str
    formula:     str
    authority:   str
    evidence:    Tuple[str, ...]
    obligations: Tuple[str, ...]
    budget:      float
    trust_tier:  TrustTier
    proof_chain: Tuple[str, ...]

    # ------------------------------------------------------------------
    def as_tuple(self) -> Tuple[str, str, str, Tuple, Tuple, float, TrustTier, Tuple]:
        """Return the canonical (c, φ, A, E, O, B, T, Π) 8-tuple."""
        return (
            self.context,
            self.formula,
            self.authority,
            self.evidence,
            self.obligations,
            self.budget,
            self.trust_tier,
            self.proof_chain,
        )

    def is_production_ready(self) -> bool:
        """Check whether this judgment is strong enough to gate a production system."""
        return self.trust_tier.can_gate_production()

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"[{self.trust_tier.label()}] {_truncate(self.formula, 80)} "
            f"(ctx={_truncate(self.context, 40)}, auth={self.authority})"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON serialisation."""
        return {
            "context":     self.context,
            "formula":     self.formula,
            "authority":   self.authority,
            "evidence":    list(self.evidence),
            "obligations": list(self.obligations),
            "budget":      self.budget,
            "trust_tier":  self.trust_tier.name,
            "proof_chain": list(self.proof_chain),
        }

    def elevate_tier(self, new_tier: TrustTier, new_evidence: Sequence[str] = ()) -> "ConsequenceJudgment":
        """Return a new judgment with an elevated trust tier and optional extra evidence.

        Raises ValueError if *new_tier* is not strictly higher than current.
        """
        if new_tier <= self.trust_tier:
            raise ValueError(
                f"Cannot elevate from {self.trust_tier.label()} to {new_tier.label()}; "
                "new tier must be strictly higher."
            )
        return ConsequenceJudgment(
            context=self.context,
            formula=self.formula,
            authority=self.authority,
            evidence=self.evidence + tuple(new_evidence),
            obligations=self.obligations,
            budget=self.budget,
            trust_tier=new_tier,
            proof_chain=self.proof_chain + (f"elevated_to:{new_tier.name}",),
        )


# ============================================================================
# EcologyImplementationConsequence
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyImplementationConsequence:
    """A concrete consequence that the theorem ecology imposes on implementation.

    Each instance records *what* ecology aspect (e.g. high citation depth,
    low reuse ratio) causes *which* design impact, identifying affected modules
    and the confidence level.

    Attributes
    ----------
    consequence_id : str
        Globally unique identifier.
    description : str
        Full rationale for this consequence.
    ecology_aspect : str
        The ecology metric/property that triggered this consequence
        (e.g. ``"reuse_ratio"``, ``"citation_depth"``, ``"coverage_gap"``).
    design_impact : str
        Required design change or constraint (concise imperative statement).
    affected_modules : Tuple[str, ...]
        Software modules that must implement or respect this consequence.
    trust_tier : TrustTier
        Confidence level for this consequence record.
    created_at : str
        ISO-8601 UTC timestamp of creation.
    """

    consequence_id:   str
    description:      str
    ecology_aspect:   str
    design_impact:    str
    affected_modules: Tuple[str, ...]
    trust_tier:       TrustTier
    created_at:       str

    # ------------------------------------------------------------------
    @staticmethod
    def make(
        description: str,
        ecology_aspect: str,
        design_impact: str,
        affected_modules: Sequence[str],
        trust_tier: TrustTier = TrustTier.PROPOSAL,
    ) -> "EcologyImplementationConsequence":
        """Factory helper that auto-generates *consequence_id* and *created_at*."""
        return EcologyImplementationConsequence(
            consequence_id=_uid(),
            description=description,
            ecology_aspect=ecology_aspect,
            design_impact=design_impact,
            affected_modules=tuple(affected_modules),
            trust_tier=trust_tier,
            created_at=_now_iso(),
        )

    def short_repr(self) -> str:
        """Compact one-line representation for log output."""
        mods = ", ".join(self.affected_modules[:3])
        if len(self.affected_modules) > 3:
            mods += f" (+{len(self.affected_modules) - 3} more)"
        return (
            f"Consequence(aspect={self.ecology_aspect!r} → "
            f"{_truncate(self.design_impact, 55)} "
            f"| tier={self.trust_tier.label()} | mods=[{mods}])"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "consequence_id":   self.consequence_id,
            "description":      self.description,
            "ecology_aspect":   self.ecology_aspect,
            "design_impact":    self.design_impact,
            "affected_modules": list(self.affected_modules),
            "trust_tier":       self.trust_tier.name,
            "created_at":       self.created_at,
        }


# ============================================================================
# EcologyConstraint
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyConstraint:
    """A quantitative constraint derived from ecology analysis.

    Constraints carry a formal numeric bound (*formal_bound*) and a severity
    flag (*is_critical*).  Critical constraints must be satisfied before any
    deployment; non-critical ones generate warnings.

    Attributes
    ----------
    constraint_id : str
        Unique identifier.
    name : str
        Short machine-readable name (snake_case).
    ecology_invariant : str
        The ecology-level invariant enforced, as a metric formula
        (e.g. ``"reuse_ratio >= 0.6"``).
    formal_bound : float
        Numeric threshold that must not be violated.
    is_critical : bool
        True → violation blocks progression; False → advisory warning.
    violation_penalty : float
        Estimated cost (same units as ConsequenceJudgment.budget) of
        allowing a violation to persist.
    created_at : str
        ISO-8601 UTC timestamp.
    """

    constraint_id:     str
    name:              str
    ecology_invariant: str
    formal_bound:      float
    is_critical:       bool
    violation_penalty: float
    created_at:        str

    # ------------------------------------------------------------------
    @staticmethod
    def make(
        name: str,
        ecology_invariant: str,
        formal_bound: float,
        is_critical: bool = False,
        violation_penalty: float = 1.0,
    ) -> "EcologyConstraint":
        """Convenience factory."""
        return EcologyConstraint(
            constraint_id=_uid(),
            name=name,
            ecology_invariant=ecology_invariant,
            formal_bound=formal_bound,
            is_critical=is_critical,
            violation_penalty=violation_penalty,
            created_at=_now_iso(),
        )

    def check(self, observed_value: float) -> "ConstraintCheckResult":
        """Evaluate *observed_value* against this constraint\'s *formal_bound*.

        Returns a :class:`ConstraintCheckResult` describing satisfaction and
        any violation magnitude.
        """
        satisfied = observed_value >= self.formal_bound
        deficit = max(0.0, self.formal_bound - observed_value)
        penalty = 0.0 if satisfied else self.violation_penalty * (1.0 + deficit)
        return ConstraintCheckResult(
            constraint=self,
            observed_value=observed_value,
            satisfied=satisfied,
            deficit=deficit,
            penalty_incurred=penalty,
        )


# ============================================================================
# ConstraintCheckResult
# ============================================================================

@dataclass(frozen=True, slots=True)
class ConstraintCheckResult:
    """Result of evaluating an :class:`EcologyConstraint` against an observed value.

    Attributes
    ----------
    constraint : EcologyConstraint
        The constraint that was checked.
    observed_value : float
        The metric value compared to the formal bound.
    satisfied : bool
        True iff *observed_value* >= *constraint.formal_bound*.
    deficit : float
        Distance below the bound (0.0 when satisfied).
    penalty_incurred : float
        Computed enforcement penalty (0.0 when satisfied).
    """

    constraint:       EcologyConstraint
    observed_value:   float
    satisfied:        bool
    deficit:          float
    penalty_incurred: float

    def describe(self) -> str:
        """Human-readable description of this check result."""
        status = (
            "PASS" if self.satisfied
            else ("CRITICAL FAIL" if self.constraint.is_critical else "WARN")
        )
        return (
            f"{status}: {self.constraint.name} "
            f"(required>={self.constraint.formal_bound:.3f}, "
            f"observed={self.observed_value:.3f}, "
            f"deficit={self.deficit:.3f}, "
            f"penalty={self.penalty_incurred:.2f})"
        )


# ============================================================================
# EcologyDesignRule
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyDesignRule:
    """A design rule encoding structural requirements driven by ecology health.

    Design rules are higher-level than constraints: they combine one or more
    ecology conditions with a required architectural response.

    Attributes
    ----------
    rule_id : str
        Unique identifier.
    name : str
        Short human-readable rule name.
    trigger_condition : str
        Ecology state that activates this rule
        (e.g. ``"citation_depth > 5 AND reuse_ratio < 0.4"``).
    required_action : str
        Architectural action mandated when the trigger fires.
    rationale : str
        Why this rule exists in the context of ecology health.
    priority : int
        Ordering priority (lower = higher priority; 0 is highest).
    trust_tier : TrustTier
        Minimum trust tier at which this rule should be enforced.
    tags : Tuple[str, ...]
        Categorisation tags (e.g. ``"modularity"``, ``"reuse"``).
    created_at : str
        ISO-8601 UTC timestamp.
    """

    rule_id:           str
    name:              str
    trigger_condition: str
    required_action:   str
    rationale:         str
    priority:          int
    trust_tier:        TrustTier
    tags:              Tuple[str, ...]
    created_at:        str

    # ------------------------------------------------------------------
    @staticmethod
    def make(
        name: str,
        trigger_condition: str,
        required_action: str,
        rationale: str,
        priority: int = 50,
        trust_tier: TrustTier = TrustTier.PROPOSAL,
        tags: Sequence[str] = (),
    ) -> "EcologyDesignRule":
        """Factory with sensible defaults."""
        return EcologyDesignRule(
            rule_id=_uid(),
            name=name,
            trigger_condition=trigger_condition,
            required_action=required_action,
            rationale=rationale,
            priority=priority,
            trust_tier=trust_tier,
            tags=tuple(tags),
            created_at=_now_iso(),
        )

    def matches(self, ecology_state: Dict[str, float]) -> bool:
        """Evaluate whether *ecology_state* satisfies this rule\'s trigger condition.

        Uses :func:`_parse_numeric_condition` for heuristic numeric evaluation.
        Returns False when no evaluable sub-expression exists.
        """
        return _parse_numeric_condition(self.trigger_condition, ecology_state)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "rule_id":           self.rule_id,
            "name":              self.name,
            "trigger_condition": self.trigger_condition,
            "required_action":   self.required_action,
            "rationale":         self.rationale,
            "priority":          self.priority,
            "trust_tier":        self.trust_tier.name,
            "tags":              list(self.tags),
            "created_at":        self.created_at,
        }


# ============================================================================
# EcologyViolation
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyViolation:
    """Immutable audit record of a detected ecology constraint/rule violation.

    Collected by :class:`EcologyCompliance` and summarised in compliance
    reports.  Violations are never mutated after creation.

    Attributes
    ----------
    violation_id : str
        Unique identifier for this violation record.
    constraint_or_rule_id : str
        ID of the violated :class:`EcologyConstraint` or :class:`EcologyDesignRule`.
    constraint_or_rule_name : str
        Human-readable name of the violated artifact.
    module_name : str
        Module or component where the violation was detected.
    observed_value : float
        Metric value that caused the violation.
    formal_bound : float
        Required bound that was not met.
    severity : str
        One of ``"critical"``, ``"warning"``, ``"info"``.
    remediation_hint : str
        Suggested fix or next step.
    detected_at : str
        ISO-8601 UTC timestamp.
    """

    violation_id:             str
    constraint_or_rule_id:    str
    constraint_or_rule_name:  str
    module_name:              str
    observed_value:           float
    formal_bound:             float
    severity:                 str
    remediation_hint:         str
    detected_at:              str

    # ------------------------------------------------------------------
    @staticmethod
    def make(
        constraint_or_rule_id: str,
        constraint_or_rule_name: str,
        module_name: str,
        observed_value: float,
        formal_bound: float,
        severity: str = "warning",
        remediation_hint: str = "",
    ) -> "EcologyViolation":
        """Factory helper."""
        return EcologyViolation(
            violation_id=_uid(),
            constraint_or_rule_id=constraint_or_rule_id,
            constraint_or_rule_name=constraint_or_rule_name,
            module_name=module_name,
            observed_value=observed_value,
            formal_bound=formal_bound,
            severity=severity,
            remediation_hint=remediation_hint,
            detected_at=_now_iso(),
        )

    def is_blocking(self) -> bool:
        """Critical violations must be resolved before deployment."""
        return self.severity == "critical"

    def describe(self) -> str:
        """Single-line violation description."""
        return (
            f"[{self.severity.upper()}] {self.constraint_or_rule_name} "
            f"in {self.module_name}: "
            f"observed={self.observed_value:.3f} req={self.formal_bound:.3f} "
            f"| hint: {_truncate(self.remediation_hint, 55)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "violation_id":            self.violation_id,
            "constraint_or_rule_id":   self.constraint_or_rule_id,
            "constraint_or_rule_name": self.constraint_or_rule_name,
            "module_name":             self.module_name,
            "observed_value":          self.observed_value,
            "formal_bound":            self.formal_bound,
            "severity":                self.severity,
            "remediation_hint":        self.remediation_hint,
            "detected_at":             self.detected_at,
        }


# ============================================================================
# EcologyModuleProfile  (frozen dataclass #7)
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyModuleProfile:
    """Snapshot of ecology-relevant metrics for a single software module.

    This is the primary input record for :class:`EcologyCompliance` and
    :class:`EcologyPolicy`.

    Attributes
    ----------
    module_name : str
        Fully-qualified module name.
    reuse_ratio : float
        Fraction of theorems in this module reused elsewhere [0, 1].
    citation_depth : float
        Average proof citation depth (DAG longest-path heuristic).
    coverage : float
        Fraction of code paths covered by theorems or tests [0, 1].
    theorem_count : int
        Number of theorems defined in this module.
    dependency_fan_out : int
        Number of distinct modules this module directly depends on.
    last_verified_at : str
        ISO-8601 timestamp of the most recent verification run.
    tags : Tuple[str, ...]
        Categorisation tags (e.g. "core", "ui", "remediation").
    """

    module_name:        str
    reuse_ratio:        float
    citation_depth:     float
    coverage:           float
    theorem_count:      int
    dependency_fan_out: int
    last_verified_at:   str
    tags:               Tuple[str, ...]

    # ------------------------------------------------------------------
    @staticmethod
    def make(
        module_name: str,
        reuse_ratio: float = 0.0,
        citation_depth: float = 1.0,
        coverage: float = 0.0,
        theorem_count: int = 0,
        dependency_fan_out: int = 0,
        tags: Sequence[str] = (),
    ) -> "EcologyModuleProfile":
        """Factory with input clamping and sensible defaults."""
        return EcologyModuleProfile(
            module_name=module_name,
            reuse_ratio=_clamp(reuse_ratio, 0.0, 1.0),
            citation_depth=max(1.0, citation_depth),
            coverage=_clamp(coverage, 0.0, 1.0),
            theorem_count=max(0, theorem_count),
            dependency_fan_out=max(0, dependency_fan_out),
            last_verified_at=_now_iso(),
            tags=tuple(tags),
        )

    def health_score(self) -> float:
        """Composite [0, 1] ecology health score for this module.

        Weights applied:
            reuse_ratio       0.35  — shared knowledge value
            coverage          0.35  — verification completeness
            citation_depth    0.15  — penalised above depth threshold of 4
            dependency_fan_out 0.15 — penalised above fan-out threshold of 10

        Formula::

            h = 0.35 * reuse
              + 0.35 * coverage
              + 0.15 * clamp(1 - max(0, depth - 4) / 10, 0, 1)
              + 0.15 * clamp(1 - max(0, fan_out - 10) / 20, 0, 1)
        """
        depth_score  = _clamp(1.0 - max(0.0, self.citation_depth - 4.0) / 10.0, 0.0, 1.0)
        fanout_score = _clamp(1.0 - max(0.0, self.dependency_fan_out - 10) / 20.0, 0.0, 1.0)
        return (
            0.35 * self.reuse_ratio
            + 0.35 * self.coverage
            + 0.15 * depth_score
            + 0.15 * fanout_score
        )

    def as_metric_dict(self) -> Dict[str, float]:
        """Return numeric metrics as a flat dict for rule matching."""
        return {
            "reuse_ratio":        self.reuse_ratio,
            "citation_depth":     self.citation_depth,
            "coverage":           self.coverage,
            "theorem_count":      float(self.theorem_count),
            "dependency_fan_out": float(self.dependency_fan_out),
            "health_score":       self.health_score(),
        }


# ============================================================================
# EcologyPolicyRecord  (frozen dataclass #8)
# ============================================================================

@dataclass(frozen=True, slots=True)
class EcologyPolicyRecord:
    """Immutable record of a registered ecology policy.

    Stored inside :class:`EcologyPolicy` after :meth:`EcologyPolicy.register_policy`.

    Attributes
    ----------
    policy_id : str
        Unique identifier.
    name : str
        Policy name (snake_case).
    rule : str
        Human-readable rule description; optionally parseable as
        ``"metric op threshold"`` (e.g. ``"reuse_ratio >= 0.5"``).
    ecology_aspect : str
        Primary ecology metric this policy governs.
    registered_at : str
        ISO-8601 UTC timestamp of registration.
    """

    policy_id:     str
    name:          str
    rule:          str
    ecology_aspect: str
    registered_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "policy_id":      self.policy_id,
            "name":           self.name,
            "rule":           self.rule,
            "ecology_aspect": self.ecology_aspect,
            "registered_at":  self.registered_at,
        }


# ============================================================================
# EcologyPolicy
# ============================================================================

class EcologyPolicy:
    """Encodes and enforces ecological policies for system design.

    An :class:`EcologyPolicy` is a mutable registry of rules that translate
    ecology metrics into pass/fail verdicts for module configurations.  It
    produces :class:`ConsequenceJudgment` reports summarising policy state.

    Usage::

        policy = EcologyPolicy(authority="ecology-engine-v1")
        policy.register_policy("min_reuse", "reuse_ratio >= 0.5", "reuse_ratio")
        violations = policy.enforce(module_profile)
        judgment   = policy.get_policy_report()

    Implementation notes
    --------------------
    *  ``register_policy`` stores an :class:`EcologyPolicyRecord` (frozen).
    *  ``enforce`` iterates registered policies, parses each rule\'s numeric
       threshold, and compares against the corresponding metric from the
       module profile.
    *  ``get_policy_report`` produces a full :class:`ConsequenceJudgment`
       reflecting the cumulative enforcement history.
    """

    def __init__(self, authority: str = "EcologyPolicy", budget: float = 100.0) -> None:
        """Initialise an empty policy registry.

        Parameters
        ----------
        authority : str
            Identifier embedded in produced judgments.
        budget : float
            Default resource budget for generated judgments.
        """
        self.authority = authority
        self.budget    = budget
        self._policies: List[EcologyPolicyRecord] = []
        self._enforcement_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def register_policy(
        self,
        name: str,
        rule: str,
        ecology_aspect: str,
    ) -> EcologyPolicyRecord:
        """Register a new policy rule.

        Parameters
        ----------
        name : str
            Short machine-readable policy name (snake_case).
        rule : str
            Rule description, optionally parseable as
            ``"metric op threshold"`` (e.g. ``"reuse_ratio >= 0.5"``).
        ecology_aspect : str
            The primary ecology metric this rule addresses.

        Returns
        -------
        EcologyPolicyRecord
            The newly registered (frozen) policy record.
        """
        record = EcologyPolicyRecord(
            policy_id=_uid(),
            name=name,
            rule=rule,
            ecology_aspect=ecology_aspect,
            registered_at=_now_iso(),
        )
        self._policies.append(record)
        return record

    # ------------------------------------------------------------------
    def enforce(
        self,
        module_config: EcologyModuleProfile,
    ) -> List[EcologyViolation]:
        """Evaluate all registered policies against *module_config*.

        For each policy whose *ecology_aspect* is a numeric metric on the
        module profile, the rule\'s numeric threshold is parsed and compared.
        Violations are collected and returned.

        Parameters
        ----------
        module_config : EcologyModuleProfile
            Module snapshot to evaluate.

        Returns
        -------
        list[EcologyViolation]
            All detected violations.  An empty list indicates full compliance.
        """
        op_pattern = re.compile(r"([\w_]+)\s*(>=|<=|>|<|==|!=)\s*([\d.]+)")
        metrics = module_config.as_metric_dict()
        violations: List[EcologyViolation] = []

        for pol in self._policies:
            observed = metrics.get(pol.ecology_aspect)
            if observed is None:
                continue

            m = op_pattern.search(pol.rule)
            if not m:
                continue

            op, threshold = m.group(2), float(m.group(3))
            lookup = {
                ">=": observed >= threshold,
                "<=": observed <= threshold,
                ">":  observed > threshold,
                "<":  observed < threshold,
                "==": math.isclose(observed, threshold, rel_tol=1e-6),
                "!=": not math.isclose(observed, threshold, rel_tol=1e-6),
            }
            if not lookup.get(op, True):
                sev  = "critical" if threshold >= 0.8 else "warning"
                hint = (
                    f"Improve {pol.ecology_aspect} from {observed:.3f} "
                    f"to satisfy: {pol.rule}"
                )
                violations.append(
                    EcologyViolation.make(
                        constraint_or_rule_id=pol.policy_id,
                        constraint_or_rule_name=pol.name,
                        module_name=module_config.module_name,
                        observed_value=observed,
                        formal_bound=threshold,
                        severity=sev,
                        remediation_hint=hint,
                    )
                )

        self._enforcement_log.append(
            {
                "module":     module_config.module_name,
                "violations": len(violations),
                "timestamp":  _now_iso(),
            }
        )
        return violations

    # ------------------------------------------------------------------
    def get_policy_report(self) -> ConsequenceJudgment:
        """Produce a :class:`ConsequenceJudgment` summarising policy state.

        Trust tier:
            PROPOSAL  if no enforcement has occurred yet.
            REVIEWED  once at least one enforcement event is recorded.

        Returns
        -------
        ConsequenceJudgment
            8-tuple judgment for the current policy registry.
        """
        policy_count     = len(self._policies)
        enforcement_count = len(self._enforcement_log)
        total_violations  = sum(e["violations"] for e in self._enforcement_log)
        tier = TrustTier.REVIEWED if enforcement_count > 0 else TrustTier.PROPOSAL

        formula = (
            f"EcologyPolicy({policy_count} rules, "
            f"{enforcement_count} enforcements, "
            f"{total_violations} total violations)"
        )
        evidence = tuple(
            f"enforcement:{e['module']}@{e['timestamp']}"
            for e in self._enforcement_log[-5:]
        )
        obligations = tuple(
            f"policy:{p.name} must hold for aspect={p.ecology_aspect}"
            for p in self._policies
        )
        proof_chain: Tuple[str, ...] = (
            ("policy_registration", "enforcement_run", "violation_count_aggregated")
            if enforcement_count > 0
            else ("policy_registration",)
        )
        return ConsequenceJudgment(
            context=f"EcologyPolicy[authority={self.authority}]",
            formula=formula,
            authority=self.authority,
            evidence=evidence,
            obligations=obligations,
            budget=self.budget,
            trust_tier=tier,
            proof_chain=proof_chain,
        )

    def policy_names(self) -> List[str]:
        """Return a list of all registered policy names."""
        return [p.name for p in self._policies]

    def policy_count(self) -> int:
        """Return the number of registered policies."""
        return len(self._policies)


# ============================================================================
# EcologyCompliance
# ============================================================================

class EcologyCompliance:
    """Aggregates constraint checks and design rule evaluations across modules.

    Works in three phases:

    1. **Registration** — add :class:`EcologyConstraint` via *add_constraint*
       and :class:`EcologyDesignRule` via *add_rule*.
    2. **Evaluation** — call *evaluate(profiles)* to run all checks.
    3. **Reporting** — call *compliance_report()* for a structured summary
       or *as_judgment()* for a :class:`ConsequenceJudgment`.

    Implementation notes
    --------------------
    *  Constraints are checked quantitatively: each constraint\'s metric name
       is extracted from *ecology_invariant* and matched against module metrics.
    *  Design rules fire when *matches()* returns True; if the module lacks a
       ``"remediation"`` tag, an advisory info violation is logged.
    *  Violations accumulate across multiple *evaluate* calls until *clear()*.
    """

    def __init__(self, authority: str = "EcologyCompliance") -> None:
        """Initialise an empty compliance checker.

        Parameters
        ----------
        authority : str
            Identifier embedded in produced judgments.
        """
        self.authority = authority
        self._constraints: List[EcologyConstraint] = []
        self._rules: List[EcologyDesignRule] = []
        self._violations: List[EcologyViolation] = []
        self._profiles_evaluated: int = 0

    # ------------------------------------------------------------------
    def add_constraint(self, constraint: EcologyConstraint) -> None:
        """Register an ecology constraint."""
        self._constraints.append(constraint)

    def add_rule(self, rule: EcologyDesignRule) -> None:
        """Register a design rule."""
        self._rules.append(rule)

    # ------------------------------------------------------------------
    def evaluate(self, profiles: Sequence[EcologyModuleProfile]) -> List[EcologyViolation]:
        """Run all constraints and rules against *profiles*.

        Parameters
        ----------
        profiles : sequence of EcologyModuleProfile
            Module snapshots to check.

        Returns
        -------
        list[EcologyViolation]
            Newly detected violations from this evaluation run.
        """
        new_violations: List[EcologyViolation] = []
        metric_names = {
            "reuse_ratio", "citation_depth", "coverage",
            "theorem_count", "dependency_fan_out", "health_score",
        }

        for profile in profiles:
            self._profiles_evaluated += 1
            metrics = profile.as_metric_dict()

            # ---- Quantitative constraint checks ----
            for constraint in self._constraints:
                # Detect which metric the invariant references
                matched_metric: Optional[str] = None
                for mn in metric_names:
                    if mn in constraint.ecology_invariant:
                        matched_metric = mn
                        break
                if matched_metric is None:
                    continue

                result = constraint.check(metrics[matched_metric])
                if not result.satisfied:
                    sev = "critical" if constraint.is_critical else "warning"
                    v = EcologyViolation.make(
                        constraint_or_rule_id=constraint.constraint_id,
                        constraint_or_rule_name=constraint.name,
                        module_name=profile.module_name,
                        observed_value=metrics[matched_metric],
                        formal_bound=constraint.formal_bound,
                        severity=sev,
                        remediation_hint=(
                            f"Bring {matched_metric} above "
                            f"{constraint.formal_bound:.3f} "
                            f"(currently {metrics[matched_metric]:.3f})"
                        ),
                    )
                    new_violations.append(v)

            # ---- Design rule evaluation ----
            for rule in self._rules:
                if rule.matches(metrics):
                    # Rule fires → advisory unless module has remediation tag
                    if "remediation" not in profile.tags:
                        v = EcologyViolation.make(
                            constraint_or_rule_id=rule.rule_id,
                            constraint_or_rule_name=rule.name,
                            module_name=profile.module_name,
                            observed_value=0.0,
                            formal_bound=0.0,
                            severity="info",
                            remediation_hint=rule.required_action,
                        )
                        new_violations.append(v)

        self._violations.extend(new_violations)
        return new_violations

    # ------------------------------------------------------------------
    def compliance_report(self) -> Dict[str, Any]:
        """Return a structured compliance summary as a JSON-serialisable dict.

        Returns
        -------
        dict with keys:
            authority, profiles_evaluated, constraints_registered,
            rules_registered, total_violations, blocking_violations,
            by_severity, by_module, deployment_allowed.
        """
        by_severity: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        by_module:   Dict[str, List[str]] = {}

        for v in self._violations:
            by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
            by_module.setdefault(v.module_name, []).append(v.describe())

        blocking = [v for v in self._violations if v.is_blocking()]

        return {
            "authority":              self.authority,
            "profiles_evaluated":     self._profiles_evaluated,
            "constraints_registered": len(self._constraints),
            "rules_registered":       len(self._rules),
            "total_violations":       len(self._violations),
            "blocking_violations":    len(blocking),
            "by_severity":            by_severity,
            "by_module":              by_module,
            "deployment_allowed":     len(blocking) == 0,
        }

    # ------------------------------------------------------------------
    def as_judgment(self) -> ConsequenceJudgment:
        """Produce a :class:`ConsequenceJudgment` from the current compliance state.

        Trust tier escalation:
            0 profiles  → PROPOSAL
            1–4         → REVIEWED
            5–19        → VERIFIED
            20+         → RUNTIME_WITNESSED
        """
        report = self.compliance_report()
        n = self._profiles_evaluated
        if n == 0:
            tier = TrustTier.PROPOSAL
        elif n < 5:
            tier = TrustTier.REVIEWED
        elif n < 20:
            tier = TrustTier.VERIFIED
        else:
            tier = TrustTier.RUNTIME_WITNESSED

        formula = (
            f"EcologyCompliance("
            f"profiles={n}, "
            f"violations={report['total_violations']}, "
            f"blocking={report['blocking_violations']}, "
            f"deployment_allowed={report['deployment_allowed']})"
        )
        evidence = tuple(
            f"module_eval:{mod}"
            for mod in list(report["by_module"].keys())[:8]
        )
        proof_chain: Tuple[str, ...] = (
            "constraint_registration",
            "rule_registration",
            f"profile_evaluation_x{n}",
            "violation_aggregation",
        )
        return ConsequenceJudgment(
            context="EcologyCompliance",
            formula=formula,
            authority=self.authority,
            evidence=evidence,
            obligations=(
                "resolve all critical violations before deployment",
                "re-run compliance after any module change",
            ),
            budget=float(len(self._constraints) + len(self._rules)) * 10.0,
            trust_tier=tier,
            proof_chain=proof_chain,
        )

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Reset violation state while keeping registered constraints and rules."""
        self._violations.clear()
        self._profiles_evaluated = 0

    def violation_count(self) -> int:
        """Return total accumulated violations."""
        return len(self._violations)

    def blocking_count(self) -> int:
        """Return number of blocking (critical) violations."""
        return sum(1 for v in self._violations if v.is_blocking())


# ============================================================================
# Module-level functions
# ============================================================================

def derive_ecology_consequences(
    ecology_state: Dict[str, float],
    design_config: Dict[str, Any],
) -> List[EcologyImplementationConsequence]:
    """Derive implementation consequences from an ecology state snapshot.

    This is the primary entry-point for higher-level orchestration layers.
    It maps raw ecology metrics to concrete design imperatives using the
    heuristics documented in the module docstring.

    Parameters
    ----------
    ecology_state : dict[str, float]
        Mapping from metric name to current value.  Recognised keys:
        ``reuse_ratio``, ``citation_depth``, ``coverage``,
        ``theorem_count``, ``dependency_fan_out``.
    design_config : dict[str, Any]
        Contextual design metadata with keys:
            ``affected_modules`` – list[str]: modules in scope
            ``context``          – str: description of the design context
            ``trust_tier``       – TrustTier (optional, defaults to PROPOSAL)

    Returns
    -------
    list[EcologyImplementationConsequence]
        Derived consequences, ordered by estimated severity (most severe first).

    Heuristics applied
    ------------------
    citation_depth > 5
        Introduce abstraction layers; cap import depth at 3 per boundary.

    reuse_ratio < 0.4
        Refactor shared lemmas into a dedicated shared_theorems module with
        a versioned public API.

    coverage < 0.6
        Mandate theorem-backed tests for uncovered paths; add CI gate.

    dependency_fan_out > 12
        Apply Dependency Inversion Principle; introduce interface abstractions;
        target fan-out ≤ 8.

    theorem_count > 50 AND reuse_ratio < 0.5
        Audit and consolidate theorems into a taxonomy module.
    """
    consequences: List[EcologyImplementationConsequence] = []
    affected = tuple(design_config.get("affected_modules", ["<unknown>"]))
    ctx  = design_config.get("context", "unspecified design context")
    tier = design_config.get("trust_tier", TrustTier.PROPOSAL)

    reuse    = ecology_state.get("reuse_ratio",        1.0)
    depth    = ecology_state.get("citation_depth",     1.0)
    coverage = ecology_state.get("coverage",           1.0)
    fan_out  = ecology_state.get("dependency_fan_out", 0.0)
    thm_count = ecology_state.get("theorem_count",     0.0)

    # --- High citation depth ---
    if depth > 5.0:
        consequences.append(
            EcologyImplementationConsequence.make(
                description=(
                    f"Citation depth {depth:.1f} exceeds threshold 5.0 in "
                    f"context '{ctx}'.  Deep proof chains create fragile "
                    "dependency graphs; callers must be insulated from deep "
                    "proof trees via intermediate abstraction layers."
                ),
                ecology_aspect="citation_depth",
                design_impact=(
                    "Introduce abstraction layer between depth-sensitive modules; "
                    "cap maximum import depth at 3 levels per module boundary."
                ),
                affected_modules=affected,
                trust_tier=tier,
            )
        )

    # --- Low reuse ratio ---
    if reuse < 0.4:
        consequences.append(
            EcologyImplementationConsequence.make(
                description=(
                    f"Reuse ratio {reuse:.2f} is below threshold 0.40 in "
                    f"context '{ctx}'.  Low reuse indicates theorem duplication "
                    "and knowledge silos, increasing maintenance cost and "
                    "inconsistency risk across modules."
                ),
                ecology_aspect="reuse_ratio",
                design_impact=(
                    "Refactor shared lemmas and utility theorems into a dedicated "
                    "shared_theorems module; expose a stable versioned public API."
                ),
                affected_modules=affected,
                trust_tier=tier,
            )
        )

    # --- Low coverage ---
    if coverage < 0.6:
        consequences.append(
            EcologyImplementationConsequence.make(
                description=(
                    f"Coverage {coverage:.2f} is below threshold 0.60 in "
                    f"context '{ctx}'.  Uncovered code paths represent "
                    "unverified behaviour; defect escape rate is "
                    "disproportionately high in uncovered regions."
                ),
                ecology_aspect="coverage",
                design_impact=(
                    "Mandate theorem-backed unit tests for all uncovered paths; "
                    "add CI gate that blocks merges when coverage < 0.60."
                ),
                affected_modules=affected,
                trust_tier=tier,
            )
        )

    # --- High fan-out ---
    if fan_out > 12.0:
        consequences.append(
            EcologyImplementationConsequence.make(
                description=(
                    f"Dependency fan-out {fan_out:.0f} exceeds threshold 12 in "
                    f"context '{ctx}'.  High fan-out creates brittle modules; "
                    "a single upstream change propagates across too many "
                    "dependents, amplifying regression risk."
                ),
                ecology_aspect="dependency_fan_out",
                design_impact=(
                    "Apply Dependency Inversion Principle; introduce interface "
                    "abstractions to reduce direct coupling; target fan-out ≤ 8."
                ),
                affected_modules=affected,
                trust_tier=tier,
            )
        )

    # --- Theorem sprawl ---
    if thm_count > 50.0 and reuse < 0.5:
        consequences.append(
            EcologyImplementationConsequence.make(
                description=(
                    f"Theorem count {thm_count:.0f} combined with reuse ratio "
                    f"{reuse:.2f} indicates theorem sprawl in context '{ctx}'.  "
                    "Large sets of poorly-reused theorems create maintenance debt "
                    "and make proof search slow and unpredictable."
                ),
                ecology_aspect="theorem_count",
                design_impact=(
                    "Audit all theorems; consolidate equivalent or near-duplicate "
                    "theorems; organise into a taxonomy module with stable IDs."
                ),
                affected_modules=affected,
                trust_tier=tier,
            )
        )

    # Sort most-severe first: high depth/fan-out/count first, then low coverage/reuse
    def _severity_key(c: EcologyImplementationConsequence) -> float:
        raw = ecology_state.get(c.ecology_aspect, 0.0)
        if c.ecology_aspect in ("citation_depth", "dependency_fan_out", "theorem_count"):
            return -raw   # higher is worse → sort descending
        return raw        # lower is worse  → sort ascending

    consequences.sort(key=_severity_key)
    return consequences


def enforce_ecology_policy(
    policy_set: EcologyPolicy,
    system_modules: Sequence[EcologyModuleProfile],
) -> Dict[str, Any]:
    """Enforce *policy_set* across all *system_modules* and return a summary.

    Drives the :class:`EcologyPolicy` enforcement loop and collects results
    into a structured summary dict.

    Parameters
    ----------
    policy_set : EcologyPolicy
        A populated :class:`EcologyPolicy` with registered rules.
    system_modules : sequence of EcologyModuleProfile
        Module profiles to evaluate.

    Returns
    -------
    dict with keys:
        total_modules, compliant_modules, non_compliant_modules,
        violations_by_module (dict[str, list[str]]),
        blocking_violations (int),
        policy_judgment (ConsequenceJudgment).
    """
    violations_by_module: Dict[str, List[str]] = {}
    blocking_count = 0

    for profile in system_modules:
        module_viols = policy_set.enforce(profile)
        if module_viols:
            violations_by_module[profile.module_name] = [v.describe() for v in module_viols]
            blocking_count += sum(1 for v in module_viols if v.is_blocking())

    total        = len(system_modules)
    non_compliant = len(violations_by_module)

    return {
        "total_modules":       total,
        "compliant_modules":   total - non_compliant,
        "non_compliant_modules": non_compliant,
        "violations_by_module":  violations_by_module,
        "blocking_violations":   blocking_count,
        "policy_judgment":       policy_set.get_policy_report(),
    }


def build_default_policy(authority: str = "default-ecology-engine") -> EcologyPolicy:
    """Construct an :class:`EcologyPolicy` pre-loaded with standard ecology rules.

    The default rule set enforces the thresholds documented in the module
    docstring: reuse >= 0.5, coverage >= 0.6, citation_depth <= 5,
    dependency_fan_out <= 12, health_score >= 0.5.

    Parameters
    ----------
    authority : str
        Authority label for the produced policy.

    Returns
    -------
    EcologyPolicy
        Ready-to-use policy with 5 pre-registered rules.
    """
    policy = EcologyPolicy(authority=authority, budget=500.0)
    policy.register_policy("min_reuse",      "reuse_ratio >= 0.5",          "reuse_ratio")
    policy.register_policy("min_coverage",   "coverage >= 0.6",             "coverage")
    policy.register_policy("max_depth",      "citation_depth <= 5.0",       "citation_depth")
    policy.register_policy("max_fan_out",    "dependency_fan_out <= 12.0",  "dependency_fan_out")
    policy.register_policy("min_health",     "health_score >= 0.5",         "health_score")
    return policy


def summarise_ecology_state(profiles: Sequence[EcologyModuleProfile]) -> Dict[str, float]:
    """Compute aggregate ecology metrics across a collection of module profiles.

    Parameters
    ----------
    profiles : sequence of EcologyModuleProfile
        Module snapshots to aggregate.

    Returns
    -------
    dict[str, float]
        Aggregate metrics: mean values for each metric, plus
        ``module_count``, ``total_theorem_count``.
    """
    if not profiles:
        return {"module_count": 0.0, "total_theorem_count": 0.0}

    n = len(profiles)
    return {
        "module_count":        float(n),
        "total_theorem_count": float(sum(p.theorem_count for p in profiles)),
        "mean_reuse_ratio":    sum(p.reuse_ratio for p in profiles) / n,
        "mean_citation_depth": sum(p.citation_depth for p in profiles) / n,
        "mean_coverage":       sum(p.coverage for p in profiles) / n,
        "mean_fan_out":        sum(p.dependency_fan_out for p in profiles) / n,
        "mean_health_score":   sum(p.health_score() for p in profiles) / n,
        "min_health_score":    min(p.health_score() for p in profiles),
        "max_health_score":    max(p.health_score() for p in profiles),
    }


# ============================================================================
# Smoke test
# ============================================================================

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("implementation_consequences.py  —  smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustTier enum
    # ------------------------------------------------------------------
    print("\n[1] TrustTier enum")
    for t in TrustTier:
        print(f"  {t.value}  {t.label():<26} can_gate_prod={t.can_gate_production()}")
    assert TrustTier.PROOF_BACKED > TrustTier.PROPOSAL
    assert TrustTier.RUNTIME_WITNESSED.can_gate_production()
    assert not TrustTier.VERIFIED.can_gate_production()
    assert TrustTier.PROPOSAL.next_tier() == TrustTier.REVIEWED
    assert TrustTier.PROOF_BACKED.next_tier() == TrustTier.PROOF_BACKED
    print("  ✓ ordering, gating, next_tier correct")

    # ------------------------------------------------------------------
    # 2. ConsequenceJudgment 8-tuple
    # ------------------------------------------------------------------
    print("\n[2] ConsequenceJudgment (8-tuple)")
    j = ConsequenceJudgment(
        context="jugeo.core.prover",
        formula="reuse_ratio >= 0.6 ∧ citation_depth <= 4",
        authority="test_harness",
        evidence=("sha256:abc123", "ci_run:42"),
        obligations=("maintain_reuse", "cap_citation_depth"),
        budget=50.0,
        trust_tier=TrustTier.VERIFIED,
        proof_chain=("lint_pass", "unit_test_pass", "integration_pass"),
    )
    tup = j.as_tuple()
    assert len(tup) == 8
    assert tup[6] == TrustTier.VERIFIED
    assert not j.is_production_ready()
    d = j.to_dict()
    assert d["trust_tier"] == "VERIFIED"
    j2 = j.elevate_tier(TrustTier.RUNTIME_WITNESSED, ["extra_evidence"])
    assert j2.trust_tier == TrustTier.RUNTIME_WITNESSED
    assert j2.is_production_ready()
    print(f"  summary: {j.summary()}")
    print("  ✓ 8-tuple schema, immutability, elevation OK")

    # ------------------------------------------------------------------
    # 3. EcologyModuleProfile + health_score
    # ------------------------------------------------------------------
    print("\n[3] EcologyModuleProfile")
    profiles = [
        EcologyModuleProfile.make("jugeo.core.prover",    reuse_ratio=0.75, citation_depth=3.0, coverage=0.82, theorem_count=28, dependency_fan_out=6,  tags=("core",)),
        EcologyModuleProfile.make("jugeo.core.resolver",  reuse_ratio=0.30, citation_depth=6.5, coverage=0.45, theorem_count=55, dependency_fan_out=14, tags=("core",)),
        EcologyModuleProfile.make("jugeo.ui.renderer",    reuse_ratio=0.10, citation_depth=2.0, coverage=0.35, theorem_count=8,  dependency_fan_out=4,  tags=("ui",)),
        EcologyModuleProfile.make("jugeo.analysis.graph", reuse_ratio=0.60, citation_depth=4.0, coverage=0.70, theorem_count=33, dependency_fan_out=9,  tags=("analysis", "remediation")),
        EcologyModuleProfile.make("jugeo.storage.cache",  reuse_ratio=0.50, citation_depth=2.5, coverage=0.65, theorem_count=20, dependency_fan_out=7,  tags=("storage",)),
    ]
    for p in profiles:
        print(f"  {p.module_name:<35}  health={p.health_score():.3f}")
    assert profiles[0].health_score() > profiles[2].health_score()
    print("  ✓ health scores ordered correctly")

    # ------------------------------------------------------------------
    # 4. EcologyConstraint + ConstraintCheckResult
    # ------------------------------------------------------------------
    print("\n[4] EcologyConstraint checks")
    c_reuse = EcologyConstraint.make("min_reuse",    "reuse_ratio >= 0.5",  formal_bound=0.5, is_critical=True,  violation_penalty=5.0)
    c_cover = EcologyConstraint.make("min_coverage", "coverage >= 0.6",     formal_bound=0.6, is_critical=False, violation_penalty=2.0)
    for p in profiles[:3]:
        r1 = c_reuse.check(p.reuse_ratio)
        r2 = c_cover.check(p.coverage)
        print(f"  {p.module_name:<35}  {r1.describe()}")
        print(f"  {'':35}  {r2.describe()}")
    print("  ✓ constraint checks run")

    # ------------------------------------------------------------------
    # 5. EcologyDesignRule + matches
    # ------------------------------------------------------------------
    print("\n[5] EcologyDesignRule")
    rule_deep = EcologyDesignRule.make(
        name="deep_citation_abstraction",
        trigger_condition="citation_depth > 5 AND reuse_ratio < 0.4",
        required_action="Introduce abstraction layer; cap import depth at 3",
        rationale="Deep citations with low reuse create fragile monolithic proof chains",
        priority=10,
        trust_tier=TrustTier.REVIEWED,
        tags=("coupling", "abstraction"),
    )
    rule_sprawl = EcologyDesignRule.make(
        name="theorem_sprawl_consolidation",
        trigger_condition="theorem_count > 50",
        required_action="Audit and consolidate theorems into taxonomy module",
        rationale="Excessive theorem count indicates knowledge silos",
        priority=20,
        trust_tier=TrustTier.PROPOSAL,
        tags=("taxonomy",),
    )
    for p in profiles:
        state = p.as_metric_dict()
        fd = rule_deep.matches(state)
        fs = rule_sprawl.matches(state)
        print(f"  {p.module_name:<35}  deep={fd}  sprawl={fs}")
    resolver_state = {"citation_depth": 6.5, "reuse_ratio": 0.30}
    assert rule_deep.matches(resolver_state), "deep rule should fire for resolver"
    print("  ✓ design rule matching correct")

    # ------------------------------------------------------------------
    # 6. EcologyViolation
    # ------------------------------------------------------------------
    print("\n[6] EcologyViolation")
    v = EcologyViolation.make(
        constraint_or_rule_id="test-id",
        constraint_or_rule_name="min_reuse",
        module_name="jugeo.ui.renderer",
        observed_value=0.10,
        formal_bound=0.50,
        severity="critical",
        remediation_hint="Add reuse-oriented refactoring sprint",
    )
    print(f"  {v.describe()}")
    assert v.is_blocking()
    print("  ✓ EcologyViolation blocking logic correct")

    # ------------------------------------------------------------------
    # 7. EcologyImplementationConsequence
    # ------------------------------------------------------------------
    print("\n[7] EcologyImplementationConsequence")
    c = EcologyImplementationConsequence.make(
        description="Test consequence for smoke test",
        ecology_aspect="reuse_ratio",
        design_impact="Refactor shared module",
        affected_modules=["mod_a", "mod_b"],
        trust_tier=TrustTier.REVIEWED,
    )
    print(f"  {c.short_repr()}")
    assert c.trust_tier == TrustTier.REVIEWED
    print("  ✓ EcologyImplementationConsequence created")

    # ------------------------------------------------------------------
    # 8. EcologyPolicy
    # ------------------------------------------------------------------
    print("\n[8] EcologyPolicy")
    policy = build_default_policy("test_engine")
    print(f"  Policies: {policy.policy_names()}")
    for p in profiles:
        viols = policy.enforce(p)
        print(f"  {p.module_name:<35}  violations={len(viols)}")
    report_j = policy.get_policy_report()
    print(f"  Policy judgment: {report_j.summary()}")
    assert report_j.trust_tier >= TrustTier.REVIEWED
    print("  ✓ EcologyPolicy enforce + report correct")

    # ------------------------------------------------------------------
    # 9. EcologyCompliance
    # ------------------------------------------------------------------
    print("\n[9] EcologyCompliance")
    compliance = EcologyCompliance(authority="test_compliance")
    compliance.add_constraint(c_reuse)
    compliance.add_constraint(c_cover)
    compliance.add_rule(rule_deep)
    compliance.add_rule(rule_sprawl)
    compliance.evaluate(profiles)
    rpt = compliance.compliance_report()
    print(f"  Profiles evaluated : {rpt['profiles_evaluated']}")
    print(f"  Total violations   : {rpt['total_violations']}")
    print(f"  Blocking           : {rpt['blocking_violations']}")
    print(f"  Deployment allowed : {rpt['deployment_allowed']}")
    j3 = compliance.as_judgment()
    assert j3.trust_tier >= TrustTier.REVIEWED
    print(f"  Compliance judgment: {j3.summary()}")
    print("  ✓ EcologyCompliance evaluation + judgment correct")

    # ------------------------------------------------------------------
    # 10. derive_ecology_consequences
    # ------------------------------------------------------------------
    print("\n[10] derive_ecology_consequences")
    eco_state = {
        "reuse_ratio":        0.25,
        "citation_depth":     7.5,
        "coverage":           0.40,
        "dependency_fan_out": 15.0,
        "theorem_count":      62.0,
    }
    design_cfg = {
        "affected_modules": ["jugeo.core.resolver", "jugeo.core.engine"],
        "context":          "resolver redesign sprint",
        "trust_tier":       TrustTier.REVIEWED,
    }
    consequences = derive_ecology_consequences(eco_state, design_cfg)
    print(f"  Derived {len(consequences)} consequences:")
    for con in consequences:
        print(f"    {con.short_repr()}")
    assert len(consequences) >= 4, f"expected ≥4, got {len(consequences)}"
    print("  ✓ derive_ecology_consequences correct")

    # ------------------------------------------------------------------
    # 11. enforce_ecology_policy
    # ------------------------------------------------------------------
    print("\n[11] enforce_ecology_policy")
    summary = enforce_ecology_policy(policy, profiles)
    print(f"  Total modules      : {summary['total_modules']}")
    print(f"  Compliant          : {summary['compliant_modules']}")
    print(f"  Non-compliant      : {summary['non_compliant_modules']}")
    print(f"  Blocking violations: {summary['blocking_violations']}")
    assert isinstance(summary["policy_judgment"], ConsequenceJudgment)
    print("  ✓ enforce_ecology_policy summary correct")

    # ------------------------------------------------------------------
    # 12. summarise_ecology_state
    # ------------------------------------------------------------------
    print("\n[12] summarise_ecology_state")
    agg = summarise_ecology_state(profiles)
    print(f"  module_count={agg['module_count']:.0f}  mean_health={agg['mean_health_score']:.3f}")
    assert agg["module_count"] == len(profiles)
    print("  ✓ summarise_ecology_state correct")

    # ------------------------------------------------------------------
    # 13. EcologyPolicyRecord frozen check
    # ------------------------------------------------------------------
    print("\n[13] EcologyPolicyRecord frozen check")
    rec = EcologyPolicyRecord(
        policy_id=_uid(), name="p", rule="x >= 0", ecology_aspect="x",
        registered_at=_now_iso(),
    )
    try:
        object.__setattr__(rec, "name", "mutated")
        print("  ✗ should have raised FrozenInstanceError")
        sys.exit(1)
    except Exception:
        print("  ✓ EcologyPolicyRecord correctly frozen")

    # ------------------------------------------------------------------
    # 14. ConsequenceJudgment frozen check
    # ------------------------------------------------------------------
    print("\n[14] ConsequenceJudgment frozen check")
    try:
        object.__setattr__(j, "formula", "mutated")
        print("  ✗ should have raised FrozenInstanceError")
        sys.exit(1)
    except Exception:
        print("  ✓ ConsequenceJudgment correctly frozen")

    # ------------------------------------------------------------------
    # 15. Helpers
    # ------------------------------------------------------------------
    print("\n[15] Helper functions")
    assert _now_iso().endswith("Z")
    u1, u2 = _uid(), _uid()
    assert u1 != u2
    assert _clamp(5.0, 0.0, 1.0) == 1.0
    assert _clamp(-1.0, 0.0, 1.0) == 0.0
    assert abs(_pct(1, 4) - 25.0) < 1e-9
    assert _pct(0, 0) == 0.0
    assert _parse_numeric_condition("reuse_ratio < 0.4 AND citation_depth > 5", {"reuse_ratio": 0.3, "citation_depth": 6.0})
    assert not _parse_numeric_condition("reuse_ratio < 0.4", {"reuse_ratio": 0.9})
    print("  ✓ all helpers correct")

    # ------------------------------------------------------------------
    # 16. JSON round-trip
    # ------------------------------------------------------------------
    print("\n[16] JSON serialisation round-trip")
    j_dict = report_j.to_dict()
    j_json = json.dumps(j_dict)
    j_back = json.loads(j_json)
    assert j_back["trust_tier"] == report_j.trust_tier.name
    print(f"  {j_json[:80]}...")
    print("  ✓ JSON round-trip OK")

    # ------------------------------------------------------------------
    # 17. EcologyConstraint immutability
    # ------------------------------------------------------------------
    print("\n[17] EcologyConstraint frozen check")
    try:
        object.__setattr__(c_reuse, "formal_bound", 0.0)
        print("  ✗ should have raised FrozenInstanceError")
        sys.exit(1)
    except Exception:
        print("  ✓ EcologyConstraint correctly frozen")

    # ------------------------------------------------------------------
    # 18. EcologyDesignRule to_dict
    # ------------------------------------------------------------------
    print("\n[18] EcologyDesignRule to_dict")
    rd = rule_deep.to_dict()
    assert rd["name"] == "deep_citation_abstraction"
    assert "trust_tier" in rd
    print(f"  rule keys: {sorted(rd.keys())}")
    print("  ✓ EcologyDesignRule.to_dict correct")

    print("\n" + "=" * 70)
    print("All smoke-test assertions passed.")
    print("=" * 70)
