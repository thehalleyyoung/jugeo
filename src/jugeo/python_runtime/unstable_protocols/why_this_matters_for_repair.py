"""Why unstable protocols matter for repair — JuGeo unstable protocols (Ch22 §4).

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §4  Repair feasibility — the core theorem states that automated repair of a
      symbol S is feasible if and only if:
        (a) the surface through which S is reached is stable (or known-unstable
            with a bounded delegation chain),
        (b) no step in the delegation chain applies a non-trivial arg-transform
            that would silently absorb the patch, and
        (c) the proposed patch does not introduce new protocol violations on any
            interface that S transitively satisfies.

      When JuGeo performs automated repair — hot-reload, monkey-patching, AST
      rewrite, or environment-level restart — each of these conditions can fail
      in a characteristic way:

      1. *Unstable surface* — patching through an unstable surface may not
         propagate to all callers because the surface may have leaked
         implementation details that are cached by callers.  The patch lands
         on the canonical object, but stale references in caller closures or
         module-level caches see the old implementation.

      2. *Delegation chain depth* — patching the head of a delegation chain
         does not automatically fix the tail.  A chain of depth d requires
         up to d individual patch operations.  Cyclic delegation makes this
         infeasible without a full restart.

      3. *Protocol violations introduced by the patch* — a repair that changes
         a method signature or removes a method may break existing
         protocol-satisfaction proofs, introducing new obligations that have
         not been discharged.

      4. *Proxy expiry* — if a ProxyRecord has expired, repair attempts that
         target the proxy object will silently fail; the expired proxy no
         longer forwards to any live object.

This module synthesises all previous analysis (s01…s03) into actionable
``RepairFeasibilityRecord`` objects and ``RepairReport`` objects that
summarise whether repair is safe, risky, requires a full restart, or is
outright impossible.

Usage::

    oracle = RepairFeasibilityOracle(
        stability_analyzer=StabilityRepairAnalyzer(),
        delegation_analyzer=DelegationRepairAnalyzer(),
        protocol_analyzer=ProtocolRepairAnalyzer(),
    )
    record = oracle.assess("my_pkg.Foo.bar", proposed_patch)
    print(record.summary())

See also: unstable_surfaces.py, delegation_chains.py,
          protocol_violations.py, theory2.tex §4.
"""

from __future__ import annotations

import enum
import functools
import hashlib
import inspect
import json
import logging
import sys
import time
import typing
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dc_replace
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports from the broader JuGeo runtime.  Fall back to lightweight
# stubs so this module can be imported in isolation (e.g. during unit tests or
# when the full geometry layer is not installed).
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection, StabilityLevel, ProxyRecord, ProxyRestriction,
        DelegationChain, DelegationKind, UnstableInterface, StabilityMonitor,
    )
except ImportError:
    class ProtocolSection: pass  # type: ignore[no-redef]
    class StabilityLevel: pass  # type: ignore[no-redef]
    class ProxyRecord: pass  # type: ignore[no-redef]
    class ProxyRestriction: pass  # type: ignore[no-redef]
    class DelegationChain: pass  # type: ignore[no-redef]
    class DelegationKind: pass  # type: ignore[no-redef]
    class UnstableInterface: pass  # type: ignore[no-redef]
    class StabilityMonitor: pass  # type: ignore[no-redef]

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind, Site, SiteBuilder,
    )
except Exception:
    import enum as _enum

    class CoordinateKind(_enum.Enum):  # type: ignore[no-redef]
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(_enum.Enum):  # type: ignore[no-redef]
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub CoordinateObject used when jugeo.geometry is absent."""
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        def __init__(self, source: Any, target: Any, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site: pass  # type: ignore[no-redef]
    class SiteBuilder: pass  # type: ignore[no-redef]

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:
    import enum as _enum

    class TrustLevel(_enum.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class JudgmentStatus(_enum.Enum):  # type: ignore[no-redef]
        PROPOSED = "proposed"; CHALLENGED = "challenged"
        SETTLED = "settled"; OBSTRUCTED = "obstructed"

    class PropositionKind(_enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
        RESOURCE = "resource"; SEMANTIC = "semantic"

    class EvidenceItemKind(_enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"; FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(_enum.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"
        HUMAN = "human"; COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: Any = None; formula: str = ""; free_variables: tuple[str, ...] = ()
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str = ""; parameters: tuple[str, ...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any = None; payload: dict = field(default_factory=dict)
        trust_level: Any = None; channel: str = ""; timestamp: str = ""
        expiry: str = ""; provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False

        def discharge(self, evidence: str = "") -> "ResidualObligation":
            return dc_replace(self, is_discharged=True)

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        description: str = ""; obstruction_id: str = ""; severity: int = 1

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: Any = None; rationale: str = ""

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        sources: tuple[Any, ...] = (); chain: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None


# ===========================================================================
# §4.1  RepairRisk — categorical risk levels for a single repair constraint
# ===========================================================================

class RepairRisk(enum.Enum):
    """Categorical risk levels attached to a single repair constraint.

    Theory alignment (Ch22 §4.1): each constraint on a repair operation is
    labelled with one of these levels.  The overall repair risk is the join
    (``combine``) of all individual constraint risks.

    Attributes
    ----------
    NONE:
        No risk.  The constraint is satisfied trivially.
    LOW:
        Minor risk.  Repair is safe but the operator should be informed.
    MEDIUM:
        Moderate risk.  Repair may partially fail; manual verification is
        recommended after applying the patch.
    HIGH:
        Serious risk.  Repair will likely fail for some callers.  A full
        module reload may be required.
    CRITICAL:
        Repair is not feasible without a full process restart or equivalent
        drastic measure.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    # copilot: ordered severity for comparison; mirrors Ch22 §4.1 lattice
    _order: typing.ClassVar[dict[str, int]] = {}

    def severity_score(self) -> float:
        """Return a normalised severity in [0, 1].

        Mapping (Ch22 §4.1, Table 1):
        NONE → 0.0, LOW → 0.25, MEDIUM → 0.5, HIGH → 0.75, CRITICAL → 1.0
        """
        _scores = {
            RepairRisk.NONE: 0.0,
            RepairRisk.LOW: 0.25,
            RepairRisk.MEDIUM: 0.50,
            RepairRisk.HIGH: 0.75,
            RepairRisk.CRITICAL: 1.0,
        }
        return _scores[self]

    def is_blocking(self) -> bool:
        """Return True iff this risk level blocks repair from proceeding.

        Per Ch22 §4.1, HIGH and CRITICAL risks are considered blocking: the
        automated repair engine should not attempt to apply the patch until the
        blocking constraints have been mitigated by the operator.
        """
        return self in (RepairRisk.HIGH, RepairRisk.CRITICAL)

    def combine(self, other: "RepairRisk") -> "RepairRisk":
        """Return the more severe of *self* and *other*.

        This implements the join operation on the severity lattice defined in
        Ch22 §4.1.  The result is the risk that dominates: if either side is
        CRITICAL the join is CRITICAL; otherwise if either is HIGH the join is
        HIGH; and so on down to NONE.

        Parameters
        ----------
        other:
            The other ``RepairRisk`` to combine with.

        Returns
        -------
        RepairRisk
            The higher-severity risk of the two operands.
        """
        # copilot: use severity_score as the total ordering on the lattice
        if self.severity_score() >= other.severity_score():
            return self
        return other


# ===========================================================================
# §4.2  RepairFeasibility — overall verdict for a repair operation
# ===========================================================================

class RepairFeasibility(enum.Enum):
    """Overall feasibility verdict for a proposed repair operation.

    Theory alignment (Ch22 §4.2): the oracle maps the join of all constraint
    risks to one of these verdicts.

    Attributes
    ----------
    FEASIBLE:
        The repair can proceed without any special precautions.
    RISKY:
        The repair can proceed but extra care is needed (manual verification,
        staged rollout, etc.).
    REQUIRES_RESTART:
        The repair cannot be applied in-place; the affected process or module
        must be restarted.
    IMPOSSIBLE:
        No form of automated repair can succeed given the current constraints.
        Human intervention is required.
    UNKNOWN:
        The oracle could not determine feasibility (insufficient information).
    """

    FEASIBLE = "feasible"
    RISKY = "risky"
    REQUIRES_RESTART = "requires_restart"
    IMPOSSIBLE = "impossible"
    UNKNOWN = "unknown"

    def is_actionable(self) -> bool:
        """Return True iff the repair framework can take *some* automated action.

        IMPOSSIBLE is not actionable — the only remedy is manual intervention.
        All other values, including UNKNOWN, allow at least partial automated
        action (e.g. emitting a warning or triggering a restart sequence).
        """
        return self is not RepairFeasibility.IMPOSSIBLE

    def requires_caution(self) -> bool:
        """Return True iff the repair should be executed with extra caution.

        Specifically returns True for RISKY (manual post-verification) and
        REQUIRES_RESTART (non-in-place operation needed).
        """
        return self in (RepairFeasibility.RISKY, RepairFeasibility.REQUIRES_RESTART)


# ===========================================================================
# §4.3  RepairConstraint — a single named constraint on a repair operation
# ===========================================================================

@dataclass(frozen=True, slots=True)
class RepairConstraint:
    """A single, named constraint that limits or blocks a repair operation.

    Each constraint is produced by one of the three sub-analyzers (stability,
    delegation, protocol) and describes *why* and *how* the repair is
    constrained, together with a suggested mitigation.

    Theory alignment (Ch22 §4.3): constraints are the fundamental unit of
    repair analysis.  The oracle collects them, joins their risks, and decides
    the overall ``RepairFeasibility``.

    Attributes
    ----------
    constraint_id:
        Unique identifier for this constraint instance (UUID4).
    description:
        Human-readable description of the constraint.
    risk:
        The risk level associated with this constraint.
    affected_symbols:
        Tuple of fully-qualified symbol names affected by this constraint.
    mitigation:
        Suggested mitigation strategy for the operator.
    is_blocking:
        Whether this constraint by itself blocks the repair from proceeding.
        Derived from ``risk.is_blocking()`` at construction time.
    """

    constraint_id: str
    description: str
    risk: Any  # RepairRisk
    affected_symbols: tuple[str, ...]
    mitigation: str
    is_blocking: bool

    def to_dict(self) -> dict:
        """Serialise this constraint to a JSON-compatible dictionary.

        The ``risk`` field is represented as its string value so that the
        result can be round-tripped through ``json.dumps`` / ``json.loads``.
        """
        return {
            "constraint_id": self.constraint_id,
            "description": self.description,
            "risk": self.risk.value if hasattr(self.risk, "value") else str(self.risk),
            "affected_symbols": list(self.affected_symbols),
            "mitigation": self.mitigation,
            "is_blocking": self.is_blocking,
        }

    def summary(self) -> str:
        """Return a one-line summary string suitable for logging or display.

        Format::

            [RISK] description (mitigation: ...)
        """
        risk_tag = self.risk.value.upper() if hasattr(self.risk, "value") else str(self.risk).upper()
        blocking_tag = " [BLOCKING]" if self.is_blocking else ""
        return f"[{risk_tag}{blocking_tag}] {self.description} (mitigation: {self.mitigation})"


# ===========================================================================
# §4.4  RepairFeasibilityRecord — synthesised per-symbol repair verdict
# ===========================================================================

@dataclass(frozen=True, slots=True)
class RepairFeasibilityRecord:
    """Synthesised repair feasibility verdict for a single target symbol.

    Produced by ``RepairFeasibilityOracle.assess`` after collecting and joining
    all constraints from the three sub-analyzers.

    Theory alignment (Ch22 §4.4): a RepairFeasibilityRecord is the primary
    output of the oracle; it encodes the proof-theoretic verdict on whether
    automated repair is safe.

    Attributes
    ----------
    target_symbol:
        Fully-qualified name of the symbol being repaired.
    feasibility:
        Overall ``RepairFeasibility`` verdict.
    constraints:
        Tuple of all ``RepairConstraint`` objects collected during analysis.
    highest_risk:
        The join of all individual constraint risks.
    recommended_approach:
        Natural-language recommendation from the oracle.
    delegation_chain_depth:
        Depth of the delegation chain at the time of analysis.
    stability_level:
        Stability level of the target surface at the time of analysis.
    protocol_violations_introduced:
        Number of new protocol violations that the proposed patch would
        introduce.
    trust_level:
        The ``TrustLevel`` that the oracle assigns to this record.  Higher
        trust means the oracle has more evidence for its verdict.
    """

    target_symbol: str
    feasibility: Any  # RepairFeasibility
    constraints: tuple  # tuple[RepairConstraint, ...]
    highest_risk: Any  # RepairRisk
    recommended_approach: str
    delegation_chain_depth: int
    stability_level: Any  # StabilityLevel or str
    protocol_violations_introduced: int
    trust_level: Any  # TrustLevel

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary, including all constraints."""
        return {
            "target_symbol": self.target_symbol,
            "feasibility": self.feasibility.value if hasattr(self.feasibility, "value") else str(self.feasibility),
            "constraints": [c.to_dict() for c in self.constraints],
            "highest_risk": self.highest_risk.value if hasattr(self.highest_risk, "value") else str(self.highest_risk),
            "recommended_approach": self.recommended_approach,
            "delegation_chain_depth": self.delegation_chain_depth,
            "stability_level": str(self.stability_level),
            "protocol_violations_introduced": self.protocol_violations_introduced,
            "trust_level": int(self.trust_level) if isinstance(self.trust_level, int) else str(self.trust_level),
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this record.

        Includes the target symbol, feasibility verdict, highest risk,
        number of constraints, and the recommended approach.
        """
        lines = [
            f"RepairFeasibilityRecord for '{self.target_symbol}'",
            f"  feasibility          : {self.feasibility}",
            f"  highest_risk         : {self.highest_risk}",
            f"  delegation_depth     : {self.delegation_chain_depth}",
            f"  stability_level      : {self.stability_level}",
            f"  protocol_violations  : {self.protocol_violations_introduced}",
            f"  constraints          : {len(self.constraints)} total, "
            f"{len(self.blocking_constraints())} blocking",
            f"  recommended_approach : {self.recommended_approach}",
        ]
        return "\n".join(lines)

    def blocking_constraints(self) -> tuple:
        """Return the subset of constraints that are individually blocking.

        A constraint is blocking when its associated ``RepairRisk`` is HIGH or
        CRITICAL (see ``RepairRisk.is_blocking``).
        """
        return tuple(c for c in self.constraints if c.is_blocking)


# ===========================================================================
# §4.5  WitnessRecord — empirical record of a repair attempt
# ===========================================================================

@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Empirical record of a single repair attempt witnessed at runtime.

    Produced by ``WhyThisMattersRepairWitness.witness_repair_attempt``.  Each
    record captures whether the attempt succeeded and, on failure, which
    constraints were violated.

    Theory alignment (Ch22 §4.5): witness records provide empirical evidence
    that can be fed back into the Judgment system to update trust levels.

    Attributes
    ----------
    record_id:
        UUID4 string uniquely identifying this witness record.
    target:
        Fully-qualified name of the symbol that was being repaired.
    success:
        Whether the repair attempt succeeded.
    failure_reason:
        Human-readable description of the failure (empty string on success).
    timestamp:
        ISO-8601-like timestamp when the attempt was witnessed.
    repair_kind:
        The kind of repair attempted (e.g. "monkey_patch", "hot_reload",
        "ast_rewrite", "restart").
    constraints_violated:
        Tuple of constraint category keywords that were violated, inferred
        from the ``failure_reason`` text.
    metadata:
        Arbitrary additional data captured at witness time.
    """

    record_id: str
    target: str
    success: bool
    failure_reason: str
    timestamp: str
    repair_kind: str
    constraints_violated: tuple[str, ...]
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise this witness record to a JSON-compatible dictionary."""
        return {
            "record_id": self.record_id,
            "target": self.target,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "timestamp": self.timestamp,
            "repair_kind": self.repair_kind,
            "constraints_violated": list(self.constraints_violated),
            "metadata": self.metadata,
        }


# ===========================================================================
# §4.6  RepairReport — aggregate report for a single repair assessment session
# ===========================================================================

@dataclass(frozen=True, slots=True)
class RepairReport:
    """Aggregate repair report for a single assessment session.

    Bundles a ``RepairFeasibilityRecord``, all constraints collected, all
    witness records for the target, and a list of human-readable
    recommendations.

    Theory alignment (Ch22 §4.6): a RepairReport is the deliverable of the
    full §4 pipeline; it is the artefact consumed by the operator or by an
    automated orchestration layer.

    Attributes
    ----------
    report_id:
        UUID4 string uniquely identifying this report.
    target_symbol:
        Fully-qualified name of the symbol that was assessed.
    feasibility_record:
        The ``RepairFeasibilityRecord`` produced by the oracle.
    constraints:
        All constraints collected during analysis (may include constraints
        not stored on the record if collected from witnesses).
    witness_records:
        All witness records for ``target_symbol``.
    recommendations:
        Ordered tuple of recommendation strings.
    generated_at:
        ISO-8601-like timestamp when the report was generated.
    """

    report_id: str
    target_symbol: str
    feasibility_record: Any  # RepairFeasibilityRecord
    constraints: tuple  # tuple[RepairConstraint, ...]
    witness_records: tuple  # tuple[WitnessRecord, ...]
    recommendations: tuple[str, ...]
    generated_at: str

    def to_dict(self) -> dict:
        """Serialise the full report to a JSON-compatible dictionary."""
        return {
            "report_id": self.report_id,
            "target_symbol": self.target_symbol,
            "feasibility_record": self.feasibility_record.to_dict(),
            "constraints": [c.to_dict() for c in self.constraints],
            "witness_records": [w.to_dict() for w in self.witness_records],
            "recommendations": list(self.recommendations),
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the report.

        Includes the top-level feasibility verdict, highest risk, number of
        constraints and witness records, and the first three recommendations.
        """
        feas = self.feasibility_record.feasibility
        risk = self.feasibility_record.highest_risk
        lines = [
            f"=== RepairReport {self.report_id[:8]}... ===",
            f"  target        : {self.target_symbol}",
            f"  feasibility   : {feas}",
            f"  highest_risk  : {risk}",
            f"  constraints   : {len(self.constraints)}",
            f"  witnesses     : {len(self.witness_records)}",
            f"  generated_at  : {self.generated_at}",
            "  recommendations:",
        ]
        for i, rec in enumerate(self.recommendations[:5], 1):
            lines.append(f"    {i}. {rec}")
        if len(self.recommendations) > 5:
            lines.append(f"    … and {len(self.recommendations) - 5} more")
        return "\n".join(lines)

    def all_risks(self) -> list:
        """Return a list of all ``RepairRisk`` values from all constraints.

        Useful for aggregating risk statistics across a report.
        """
        # copilot: return risks from the feasibility_record's constraints
        return [c.risk for c in self.constraints]

    def highest_risk(self) -> "RepairRisk":
        """Return the highest ``RepairRisk`` across all constraints in the report.

        Falls back to ``RepairRisk.NONE`` if there are no constraints.
        """
        risks = self.all_risks()
        if not risks:
            return RepairRisk.NONE
        result = RepairRisk.NONE
        for r in risks:
            result = result.combine(r)
        return result


# ===========================================================================
# §4.7  StabilityRepairAnalyzer
# ===========================================================================

class StabilityRepairAnalyzer:
    """Analyses repair feasibility with respect to surface stability.

    Theory alignment (Ch22 §4.7): a patch applied through an unstable surface
    may not reach all callers.  This analyzer examines the stability level of
    the surface and any leaked implementation details to produce constraints.

    The stability-to-risk mapping follows Table 2 of Ch22:

    ============  ===========
    Stability     Risk
    ============  ===========
    stable        NONE
    degrading     LOW
    unstable      MEDIUM
    retracting    HIGH
    collapsed     CRITICAL
    (unknown)     MEDIUM
    ============  ===========

    In addition, each leaked implementation detail adds a separate LOW-to-MEDIUM
    constraint, because callers that have captured a reference to the leaked
    detail will not see the patch.
    """

    def __init__(self) -> None:
        # copilot: internal list of constraints accumulated during analysis
        self._constraints: list[RepairConstraint] = []

    def analyze(self, target_symbol: str, stability_info: dict) -> list[RepairConstraint]:
        """Analyse stability constraints for *target_symbol*.

        Parameters
        ----------
        target_symbol:
            Fully-qualified name of the symbol being patched.
        stability_info:
            Dictionary with the following optional keys:

            ``stability_level`` (str)
                One of ``"stable"``, ``"degrading"``, ``"unstable"``,
                ``"retracting"``, ``"collapsed"``, or ``"unknown"``.
                Defaults to ``"unknown"`` if absent.
            ``leaked_details`` (list[str])
                Names of implementation details that have leaked through the
                surface.  Each leaked detail will produce an additional
                constraint.
            ``is_deprecated`` (bool)
                Whether the surface is marked as deprecated.  Deprecated
                surfaces receive a LOW constraint warning the operator.

        Returns
        -------
        list[RepairConstraint]
            The list of constraints generated.  Also stored in ``_constraints``.
        """
        # copilot: reset before analysis to avoid stale data from prior calls
        self._constraints = []

        stability_str = stability_info.get("stability_level", "unknown")
        leaked_details: list[str] = stability_info.get("leaked_details", [])
        is_deprecated: bool = bool(stability_info.get("is_deprecated", False))

        # Map stability level to risk and add a surface constraint
        base_risk = self._stability_to_risk(stability_str)
        surface_constraint = self._make_surface_constraint(target_symbol, base_risk)
        self._constraints.append(surface_constraint)

        # copilot: each leaked detail is an independent constraint — stale
        # references held by callers will not see the patch
        for leaked in leaked_details:
            leak_constraint = self._make_leak_constraint(target_symbol, leaked)
            self._constraints.append(leak_constraint)

        # Deprecated surfaces get a warning constraint regardless of other risks
        if is_deprecated:
            dep_constraint = RepairConstraint(
                constraint_id=str(uuid.uuid4()),
                description=(
                    f"Symbol '{target_symbol}' is deprecated. Repairing deprecated "
                    "surfaces may conflict with planned removal timelines and can "
                    "confuse dependent callers that have been warned to migrate."
                ),
                risk=RepairRisk.LOW,
                affected_symbols=(target_symbol,),
                mitigation=(
                    "Consider migrating callers away from the deprecated surface "
                    "instead of repairing it.  If repair is still required, annotate "
                    "the patch with a deprecation notice."
                ),
                is_blocking=RepairRisk.LOW.is_blocking(),
            )
            self._constraints.append(dep_constraint)

        logger.debug(
            "StabilityRepairAnalyzer: %d constraints for %s (stability=%s)",
            len(self._constraints), target_symbol, stability_str,
        )
        return list(self._constraints)

    def _stability_to_risk(self, stability_str: str) -> "RepairRisk":
        """Map a stability-level string to a ``RepairRisk``.

        Implements the mapping from Table 2 of Ch22 §4.7.  Unknown values are
        mapped to MEDIUM as a conservative default.
        """
        mapping = {
            "stable": RepairRisk.NONE,
            "degrading": RepairRisk.LOW,
            "unstable": RepairRisk.MEDIUM,
            "retracting": RepairRisk.HIGH,
            "collapsed": RepairRisk.CRITICAL,
        }
        # copilot: normalise to lower-case and strip whitespace for robustness
        return mapping.get(stability_str.strip().lower(), RepairRisk.MEDIUM)

    def _make_leak_constraint(self, symbol: str, leaked: str) -> "RepairConstraint":
        """Create a ``RepairConstraint`` for a leaked implementation detail.

        Leaked details produce MEDIUM risk constraints: callers that have
        captured a reference to the leaked attribute will bypass the patch.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Implementation detail '{leaked}' has leaked from '{symbol}'. "
                "Callers holding a direct reference to this detail will not observe "
                "the patch — the patch lands on the canonical symbol but stale "
                "closures or module-level caches retain the old object."
            ),
            risk=RepairRisk.MEDIUM,
            affected_symbols=(symbol,),
            mitigation=(
                f"Invalidate all module-level caches referencing '{leaked}' after "
                "applying the patch.  Consider making the detail non-public (prefix "
                "with '_') or replacing the cache with a property to ensure "
                "callers always resolve through the surface."
            ),
            is_blocking=RepairRisk.MEDIUM.is_blocking(),
        )

    def _make_surface_constraint(self, symbol: str, risk: "RepairRisk") -> "RepairConstraint":
        """Create a ``RepairConstraint`` reflecting the surface stability level.

        The description and mitigation text are tailored to the risk level so
        that the operator receives actionable guidance.
        """
        if risk == RepairRisk.NONE:
            description = (
                f"Surface for '{symbol}' is stable.  Patch propagation is "
                "reliable; no stability-related constraints apply."
            )
            mitigation = "No action required."
        elif risk == RepairRisk.LOW:
            description = (
                f"Surface for '{symbol}' is degrading.  Some callers may be "
                "caching resolved attributes; patch propagation is mostly reliable "
                "but not guaranteed."
            )
            mitigation = (
                "Perform a staged rollout and monitor for 60 s post-patch.  "
                "Invalidate any known caches of this surface."
            )
        elif risk == RepairRisk.MEDIUM:
            description = (
                f"Surface for '{symbol}' is unstable.  The surface contract is "
                "not guaranteed to remain consistent; patches applied now may be "
                "partially overwritten by concurrent stability transitions."
            )
            mitigation = (
                "Schedule the patch during a quiescent period.  After patching, "
                "verify that the updated implementation is reachable from all "
                "known call sites.  Consider a module-level reload if spot checks "
                "reveal inconsistencies."
            )
        elif risk == RepairRisk.HIGH:
            description = (
                f"Surface for '{symbol}' is retracting.  The surface is in the "
                "process of being withdrawn; patches are unlikely to propagate "
                "correctly to all callers because the surface contract is being "
                "dismantled."
            )
            mitigation = (
                "A full module reload (or process restart if cross-process) is "
                "recommended.  Do not rely on in-place monkey-patching."
            )
        else:  # CRITICAL / collapsed
            description = (
                f"Surface for '{symbol}' has collapsed.  The surface is no longer "
                "functional; repair through this surface is impossible without "
                "rebuilding the surface from scratch."
            )
            mitigation = (
                "Trigger a full process restart and re-initialise the surface.  "
                "Automated repair is not possible; manual intervention is required."
            )

        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=description,
            risk=risk,
            affected_symbols=(symbol,),
            mitigation=mitigation,
            is_blocking=risk.is_blocking(),
        )


# ===========================================================================
# §4.8  DelegationRepairAnalyzer
# ===========================================================================

class DelegationRepairAnalyzer:
    """Analyses repair feasibility with respect to delegation chain structure.

    Theory alignment (Ch22 §4.8): patching the head of a delegation chain does
    not automatically fix the tail.  Deep chains, cyclic chains, and chains
    that apply argument transformations all increase repair risk.

    Depth-to-risk mapping (Ch22 §4.8, Table 3):

    ======  ===========
    Depth   Risk
    ======  ===========
    0–2     NONE
    3–4     LOW
    5–7     MEDIUM
    8–10    HIGH
    >10     CRITICAL
    ======  ===========
    """

    def __init__(self) -> None:
        # copilot: internal list of constraints accumulated during analysis
        self._constraints: list[RepairConstraint] = []

    def analyze(self, target_symbol: str, chain_info: dict) -> list[RepairConstraint]:
        """Analyse delegation-chain constraints for *target_symbol*.

        Parameters
        ----------
        target_symbol:
            Fully-qualified name of the symbol being patched.
        chain_info:
            Dictionary with the following optional keys:

            ``depth`` (int)
                Length of the delegation chain.  Defaults to 0.
            ``has_cycle`` (bool)
                Whether the chain contains a cycle.  Cyclic chains always
                produce a CRITICAL constraint.
            ``links`` (list[dict])
                Descriptions of individual links in the chain.  Each dict
                may contain ``"transforms_args"`` (bool) and ``"name"`` (str).

        Returns
        -------
        list[RepairConstraint]
            The list of constraints generated.  Also stored in ``_constraints``.
        """
        # copilot: reset before analysis to avoid stale data from prior calls
        self._constraints = []

        depth: int = int(chain_info.get("depth", 0))
        has_cycle: bool = bool(chain_info.get("has_cycle", False))
        links: list[dict] = chain_info.get("links", [])

        # Depth constraint — even without a cycle, deep chains are problematic
        depth_risk = self._depth_to_risk(depth)
        if depth_risk != RepairRisk.NONE:
            depth_constraint = self._make_depth_constraint(target_symbol, depth, depth_risk)
            self._constraints.append(depth_constraint)

        # Cycle constraint — always CRITICAL; repair is impossible in-process
        if has_cycle:
            # copilot: try to identify where the cycle occurs from link names
            cycle_at = links[-1].get("name", "unknown") if links else "unknown"
            cycle_constraint = self._make_cycle_constraint(target_symbol, cycle_at)
            self._constraints.append(cycle_constraint)

        # Per-link arg-transformation constraints
        for link in links:
            if link.get("transforms_args", False):
                link_name = link.get("name", "<unnamed>")
                transform_constraint = self._make_transform_constraint(target_symbol, link_name)
                self._constraints.append(transform_constraint)

        logger.debug(
            "DelegationRepairAnalyzer: %d constraints for %s (depth=%d, cycle=%s)",
            len(self._constraints), target_symbol, depth, has_cycle,
        )
        return list(self._constraints)

    def _depth_to_risk(self, depth: int) -> "RepairRisk":
        """Map delegation chain depth to a ``RepairRisk``.

        Implements the depth-to-risk mapping from Table 3 of Ch22 §4.8.
        """
        if depth <= 2:
            return RepairRisk.NONE
        elif depth <= 4:
            return RepairRisk.LOW
        elif depth <= 7:
            return RepairRisk.MEDIUM
        elif depth <= 10:
            return RepairRisk.HIGH
        else:
            return RepairRisk.CRITICAL

    def _make_depth_constraint(
        self, symbol: str, depth: int, risk: "RepairRisk"
    ) -> "RepairConstraint":
        """Create a ``RepairConstraint`` for excessive delegation chain depth.

        Per Ch22 §4.8, a patch on the head of a delegation chain of depth *d*
        must be propagated to up to *d* objects.  At greater depths, the
        probability of missing at least one link grows substantially.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Delegation chain for '{symbol}' has depth {depth}. "
                "Patching only the chain head will leave tail delegates "
                "unreachable by the patch.  Each additional link in the chain "
                "is a separate object that must be patched independently."
            ),
            risk=risk,
            affected_symbols=(symbol,),
            mitigation=(
                f"Enumerate all {depth} links in the delegation chain and apply "
                "the patch to each.  If the chain is longer than ~4, consider a "
                "full module reload to ensure complete propagation."
            ),
            is_blocking=risk.is_blocking(),
        )

    def _make_cycle_constraint(self, symbol: str, cycle_at: str) -> "RepairConstraint":
        """Create a CRITICAL ``RepairConstraint`` for a cyclic delegation chain.

        A cyclic chain makes it impossible to enumerate all links; in-process
        patch propagation will loop indefinitely or terminate early, leaving
        some links un-patched.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Delegation chain for '{symbol}' contains a cycle "
                f"(detected at link '{cycle_at}'). "
                "Automated patch propagation through a cyclic chain will either "
                "loop indefinitely or terminate early, leaving the cycle un-patched.  "
                "In-process repair is not feasible."
            ),
            risk=RepairRisk.CRITICAL,
            affected_symbols=(symbol,),
            mitigation=(
                "Break the delegation cycle before attempting repair, or trigger "
                "a full process restart to clear all stale references.  Cyclic "
                "delegation is often a design defect; consider refactoring."
            ),
            is_blocking=RepairRisk.CRITICAL.is_blocking(),
        )

    def _make_transform_constraint(self, symbol: str, link_name: str) -> "RepairConstraint":
        """Create a MEDIUM ``RepairConstraint`` for an arg-transforming link.

        A link that transforms arguments before forwarding may silently absorb
        the effect of the patch: the callee sees transformed arguments that the
        patch was not designed to handle, producing incorrect behaviour without
        raising an obvious error.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Delegation link '{link_name}' in the chain for '{symbol}' "
                "applies an argument transformation before forwarding the call.  "
                "A patch on the callee may receive arguments that differ from "
                "what the caller intended, silently producing incorrect behaviour."
            ),
            risk=RepairRisk.MEDIUM,
            affected_symbols=(symbol,),
            mitigation=(
                f"Audit the argument transformation at '{link_name}' to confirm "
                "it is compatible with the patched behaviour.  Consider patching "
                "the link itself rather than (or in addition to) the callee."
            ),
            is_blocking=RepairRisk.MEDIUM.is_blocking(),
        )


# ===========================================================================
# §4.9  ProtocolRepairAnalyzer
# ===========================================================================

class ProtocolRepairAnalyzer:
    """Analyses whether a proposed repair would introduce protocol violations.

    Theory alignment (Ch22 §4.9): a repair that removes methods or changes
    signatures may break existing protocol-satisfaction proofs.  This analyzer
    examines the proposed patch description and generates constraints for each
    potential violation.

    Risk mapping:
    * removed method → CRITICAL (breaks LSP-style substitutability)
    * signature incompatibility → HIGH (callers will receive wrong type)
    * affected protocol surface → MEDIUM (protocol proof must be re-checked)
    """

    def __init__(self) -> None:
        # copilot: internal list of constraints accumulated during analysis
        self._constraints: list[RepairConstraint] = []

    def analyze(self, target_symbol: str, protocol_info: dict) -> list[RepairConstraint]:
        """Analyse protocol constraints for *target_symbol*.

        Parameters
        ----------
        target_symbol:
            Fully-qualified name of the symbol being patched.
        protocol_info:
            Dictionary with the following optional keys:

            ``protocols_affected`` (list[str])
                Names of protocols that *target_symbol* participates in and
                that would be affected by the proposed patch.
            ``proposed_signature_change`` (bool)
                Whether the proposed patch changes any method signature.
            ``removed_methods`` (list[str])
                Names of methods that the proposed patch would remove.
            ``signature_incompatibilities`` (list[str])
                Descriptions of individual signature mismatches introduced.

        Returns
        -------
        list[RepairConstraint]
            The list of constraints generated.  Also stored in ``_constraints``.
        """
        # copilot: reset before analysis to avoid stale data from prior calls
        self._constraints = []

        protocols_affected: list[str] = protocol_info.get("protocols_affected", [])
        removed_methods: list[str] = protocol_info.get("removed_methods", [])
        signature_incompatibilities: list[str] = protocol_info.get("signature_incompatibilities", [])

        # CRITICAL constraint for each removed method
        for method in removed_methods:
            removal_constraint = self._make_removal_constraint(target_symbol, method)
            self._constraints.append(removal_constraint)

        # HIGH constraint for each signature incompatibility
        for mismatch in signature_incompatibilities:
            compat_constraint = self._make_compat_constraint(target_symbol, mismatch)
            self._constraints.append(compat_constraint)

        # MEDIUM constraint for each affected protocol surface
        for protocol in protocols_affected:
            protocol_constraint = self._make_protocol_constraint(target_symbol, protocol)
            self._constraints.append(protocol_constraint)

        logger.debug(
            "ProtocolRepairAnalyzer: %d constraints for %s (%d removed methods, "
            "%d compat issues, %d protocols)",
            len(self._constraints), target_symbol,
            len(removed_methods), len(signature_incompatibilities), len(protocols_affected),
        )
        return list(self._constraints)

    def _make_removal_constraint(self, symbol: str, method: str) -> "RepairConstraint":
        """Create a CRITICAL ``RepairConstraint`` for a removed method.

        Removing a method that is part of a protocol contract violates the
        Liskov Substitution Principle and breaks any existing protocol-
        satisfaction proof.  Callers relying on the removed method will receive
        ``AttributeError`` at runtime.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Proposed patch on '{symbol}' removes method '{method}'.  "
                "This breaks the protocol contract and invalidates all existing "
                "protocol-satisfaction proofs that include this method.  "
                "Callers relying on the removed method will encounter "
                "``AttributeError`` immediately after the patch is applied."
            ),
            risk=RepairRisk.CRITICAL,
            affected_symbols=(symbol,),
            mitigation=(
                f"Do not remove '{method}' as part of this patch.  Instead, "
                "deprecate the method and schedule removal for a later breaking "
                "change.  If removal is unavoidable, update all protocol "
                "satisfaction proofs and migrate all callers before applying."
            ),
            is_blocking=RepairRisk.CRITICAL.is_blocking(),
        )

    def _make_compat_constraint(self, symbol: str, mismatch: str) -> "RepairConstraint":
        """Create a HIGH ``RepairConstraint`` for a signature incompatibility.

        A signature change that is not backward-compatible will cause type
        errors for callers that pass arguments using the old convention.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Proposed patch on '{symbol}' introduces a signature "
                f"incompatibility: {mismatch}.  "
                "Callers using the old calling convention will receive "
                "``TypeError`` at runtime, and type-checked code will fail "
                "static analysis."
            ),
            risk=RepairRisk.HIGH,
            affected_symbols=(symbol,),
            mitigation=(
                "Maintain backward compatibility by adding keyword-only parameters "
                "with default values rather than changing positional parameter order "
                "or types.  Update all call sites and re-run the protocol satisfaction "
                "check after applying the patch."
            ),
            is_blocking=RepairRisk.HIGH.is_blocking(),
        )

    def _make_protocol_constraint(self, symbol: str, protocol: str) -> "RepairConstraint":
        """Create a MEDIUM ``RepairConstraint`` for an affected protocol surface.

        Even if the patch does not remove methods or change signatures
        incompatibly, touching a symbol that participates in a protocol
        requires re-validating the protocol satisfaction proof.
        """
        return RepairConstraint(
            constraint_id=str(uuid.uuid4()),
            description=(
                f"Symbol '{symbol}' participates in protocol '{protocol}'.  "
                "The proposed patch changes the symbol's implementation; the "
                f"existing protocol satisfaction proof for '{protocol}' must be "
                "re-verified after the patch is applied."
            ),
            risk=RepairRisk.MEDIUM,
            affected_symbols=(symbol,),
            mitigation=(
                f"After applying the patch, re-run the protocol satisfaction "
                f"checker for '{protocol}'.  If the checker is unavailable, "
                "perform a manual review of all methods required by the protocol "
                "and confirm that each still satisfies its contract."
            ),
            is_blocking=RepairRisk.MEDIUM.is_blocking(),
        )


# ===========================================================================
# §4.10  RepairFeasibilityOracle — synthesises all three sub-analyzers
# ===========================================================================

class RepairFeasibilityOracle:
    """Synthesises stability, delegation, and protocol analyses into a verdict.

    This is the primary entry point for repair feasibility assessment.  It
    orchestrates the three sub-analyzers, joins all constraint risks, maps the
    result to a ``RepairFeasibility`` verdict, and emits a Judgment.

    Theory alignment (Ch22 §4.10): the oracle implements the *repair oracle*
    construction from the proof of Theorem 4.2 in theory2.tex.  The theorem
    states that a repair is feasible iff the join of all constraint risks is
    at most MEDIUM (i.e. the repair can be attempted with caution).

    Parameters
    ----------
    stability_analyzer:
        An instance of ``StabilityRepairAnalyzer``.
    delegation_analyzer:
        An instance of ``DelegationRepairAnalyzer``.
    protocol_analyzer:
        An instance of ``ProtocolRepairAnalyzer``.
    """

    def __init__(
        self,
        stability_analyzer: StabilityRepairAnalyzer,
        delegation_analyzer: DelegationRepairAnalyzer,
        protocol_analyzer: ProtocolRepairAnalyzer,
    ) -> None:
        self.stability_analyzer = stability_analyzer
        self.delegation_analyzer = delegation_analyzer
        self.protocol_analyzer = protocol_analyzer

    def assess(self, target: str, proposed_patch: dict) -> RepairFeasibilityRecord:
        """Assess the repair feasibility for *target* given *proposed_patch*.

        Parameters
        ----------
        target:
            Fully-qualified name of the symbol being patched.
        proposed_patch:
            Dictionary describing the proposed patch.  Expected keys:

            ``stability_info`` (dict)
                Passed to ``StabilityRepairAnalyzer.analyze``.
            ``chain_info`` (dict)
                Passed to ``DelegationRepairAnalyzer.analyze``.
            ``protocol_info`` (dict)
                Passed to ``ProtocolRepairAnalyzer.analyze``.
            ``delegation_chain_depth`` (int)
                Pre-computed delegation chain depth (used in record).
            ``protocol_violations_introduced`` (int)
                Number of new protocol violations introduced (used in record).
            ``current_stability_level`` (str)
                Current stability level string (used in record).

        Returns
        -------
        RepairFeasibilityRecord
            The synthesised repair verdict.
        """
        # copilot: extract sub-dicts, defaulting to empty dicts for safety
        stability_info = proposed_patch.get("stability_info", {})
        chain_info = proposed_patch.get("chain_info", {})
        protocol_info = proposed_patch.get("protocol_info", {})

        # Run all three sub-analyzers
        s_constraints = self.stability_analyzer.analyze(target, stability_info)
        d_constraints = self.delegation_analyzer.analyze(target, chain_info)
        p_constraints = self.protocol_analyzer.analyze(target, protocol_info)

        all_constraints: list[RepairConstraint] = s_constraints + d_constraints + p_constraints

        # Find the join of all constraint risks (highest risk wins)
        highest_risk = RepairRisk.NONE
        for constraint in all_constraints:
            highest_risk = highest_risk.combine(constraint.risk)

        # Map highest_risk to RepairFeasibility per Ch22 §4.10 Corollary 4.3
        feasibility = self._risk_to_feasibility(highest_risk)

        # Gather metadata from the proposed_patch dict
        delegation_chain_depth = int(proposed_patch.get("delegation_chain_depth", 0))
        protocol_violations_introduced = int(proposed_patch.get("protocol_violations_introduced", 0))
        current_stability_level = proposed_patch.get("current_stability_level", "unknown")

        # Determine trust level: fewer constraints → higher trust in verdict
        trust_level = self._compute_trust_level(all_constraints, highest_risk)

        # Build a preliminary record (without recommended_approach) for recommend_approach
        preliminary = RepairFeasibilityRecord(
            target_symbol=target,
            feasibility=feasibility,
            constraints=tuple(all_constraints),
            highest_risk=highest_risk,
            recommended_approach="",  # filled in next
            delegation_chain_depth=delegation_chain_depth,
            stability_level=current_stability_level,
            protocol_violations_introduced=protocol_violations_introduced,
            trust_level=trust_level,
        )

        recommended_approach = self.recommend_approach(preliminary)

        # copilot: build the final immutable record
        record = dc_replace(preliminary, recommended_approach=recommended_approach)
        return record

    def _risk_to_feasibility(self, risk: "RepairRisk") -> RepairFeasibility:
        """Map the highest constraint risk to an overall ``RepairFeasibility``.

        Implements Corollary 4.3 from Ch22 §4.10:
        NONE/LOW → FEASIBLE, MEDIUM → RISKY, HIGH → REQUIRES_RESTART,
        CRITICAL → IMPOSSIBLE.
        """
        mapping = {
            RepairRisk.NONE: RepairFeasibility.FEASIBLE,
            RepairRisk.LOW: RepairFeasibility.FEASIBLE,
            RepairRisk.MEDIUM: RepairFeasibility.RISKY,
            RepairRisk.HIGH: RepairFeasibility.REQUIRES_RESTART,
            RepairRisk.CRITICAL: RepairFeasibility.IMPOSSIBLE,
        }
        return mapping.get(risk, RepairFeasibility.UNKNOWN)

    def _compute_trust_level(
        self, constraints: list[RepairConstraint], highest_risk: "RepairRisk"
    ) -> Any:
        """Compute the oracle's self-reported trust level in its verdict.

        More constraints → more information → higher trust.  Blocking
        constraints lower trust because they indicate known unknowns.
        """
        blocking_count = sum(1 for c in constraints if c.is_blocking)
        total_count = len(constraints)
        if total_count == 0:
            return TrustLevel.ORACLE_PROPOSED
        if blocking_count > 0:
            return TrustLevel.ORACLE_PROPOSED
        if highest_risk.severity_score() <= 0.25:
            return TrustLevel.SOLVER_DISCHARGED
        if highest_risk.severity_score() <= 0.5:
            return TrustLevel.RUNTIME_WITNESSED
        return TrustLevel.ORACLE_PROPOSED

    def recommend_approach(self, record: RepairFeasibilityRecord) -> str:
        """Generate a natural-language repair recommendation from *record*.

        The recommendation explains the suggested approach, why specific
        constraints matter, and what mitigations are available.  It is
        tailored to the ``RepairFeasibility`` verdict and the set of
        blocking constraints.

        Theory alignment (Ch22 §4.10, Remark 4.4): recommendations should
        be actionable and refer back to the specific constraints that drove
        the verdict.
        """
        feas = record.feasibility
        risk = record.highest_risk
        blocking = record.blocking_constraints()
        depth = record.delegation_chain_depth
        violations = record.protocol_violations_introduced

        if feas == RepairFeasibility.FEASIBLE:
            base = (
                f"Repair of '{record.target_symbol}' is FEASIBLE.  "
                "No blocking constraints were found.  "
                "Apply the patch directly and monitor for 30 s post-patch to "
                "confirm propagation."
            )
            if depth > 0:
                base += (
                    f"  Trace the delegation chain (depth {depth}) to confirm "
                    "all links received the update."
                )
            return base

        if feas == RepairFeasibility.RISKY:
            mitigation_hints = "; ".join(
                c.mitigation for c in record.constraints if c.risk == RepairRisk.MEDIUM
            )
            return (
                f"Repair of '{record.target_symbol}' is RISKY (highest risk: {risk.value}).  "
                "The patch can be applied but extra care is required.  "
                f"Mitigations: {mitigation_hints or 'see individual constraint mitigations'}.  "
                f"Protocol violations introduced: {violations}.  "
                "Run the protocol satisfaction checker after applying the patch."
            )

        if feas == RepairFeasibility.REQUIRES_RESTART:
            blocking_descs = " | ".join(c.description[:80] for c in blocking)
            return (
                f"Repair of '{record.target_symbol}' REQUIRES A RESTART.  "
                "In-place monkey-patching is insufficient due to blocking constraints: "
                f"{blocking_descs}.  "
                "Trigger a full module reload or process restart, then re-apply the patch "
                "in a clean environment.  Verify all delegation-chain links after restart."
            )

        if feas == RepairFeasibility.IMPOSSIBLE:
            blocking_descs = " | ".join(c.description[:80] for c in blocking)
            return (
                f"Repair of '{record.target_symbol}' is IMPOSSIBLE via automated means.  "
                "The following blocking constraints cannot be resolved automatically: "
                f"{blocking_descs}.  "
                "Manual intervention is required.  Consider redesigning the affected "
                "surface to reduce delegation depth and remove cyclic dependencies."
            )

        # UNKNOWN fallback
        return (
            f"Feasibility of repairing '{record.target_symbol}' is UNKNOWN.  "
            "Insufficient information was provided to make a determination.  "
            "Collect stability, delegation-chain, and protocol information and re-assess."
        )

    def emit_judgment(self, record: RepairFeasibilityRecord) -> Judgment:
        """Build a ``Judgment`` from a ``RepairFeasibilityRecord``.

        The judgment captures the oracle's verdict and provides evidence in the
        form of the serialised record payload.  Obstructions are created for
        each blocking constraint.

        Theory alignment (Ch22 §4.10): judgments bridge the repair oracle and
        the JuGeo judgment system; they allow the repair verdict to be
        consumed by the broader proof-management infrastructure.
        """
        coord = CoordinateObject(
            components=tuple(record.target_symbol.split(".")),
            kind=CoordinateKind.FUNCTION,
        )
        prop = Proposition(
            kind=PropositionKind.BEHAVIORAL,
            formula=f"repair_feasible({record.target_symbol})",
        )
        carrier = Carrier(name=record.target_symbol)

        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload=record.to_dict(),
            trust_level=record.trust_level,
            channel="repair_oracle",
            timestamp=str(time.time()),
        )
        bundle = EvidenceBundle(items=(evidence_item,))

        obstructions = tuple(
            Obstruction(
                description=c.description[:200],
                obstruction_id=c.constraint_id,
                severity=int(c.risk.severity_score() * 10),
            )
            for c in record.blocking_constraints()
        )

        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=obstructions,
            trust=TrustAnnotation(level=record.trust_level, rationale=record.recommended_approach),
            provenance=Provenance(sources=(ProvenanceSource.ORACLE,)),
        )


# ===========================================================================
# §4.11  WhyThisMattersRepairAnalyzer
# ===========================================================================

class WhyThisMattersRepairAnalyzer:
    """High-level analyzer that ties together the oracle and the §4 narrative.

    This class is the primary consumer of ``RepairFeasibilityOracle``.  It
    accepts natural-language patch descriptions, parses them for risk keywords,
    constructs a ``proposed_patch`` dict, and delegates to the oracle.

    Theory alignment (Ch22 §4.11): the analyzer corresponds to the *analysis
    pass* described in the proof of Proposition 4.5 — a lightweight static
    analysis that extracts repair constraints from a textual description of
    the proposed change.

    Parameters
    ----------
    oracle:
        A ``RepairFeasibilityOracle`` instance.
    """

    def __init__(self, oracle: RepairFeasibilityOracle) -> None:
        self.oracle = oracle
        self._records: list[RepairFeasibilityRecord] = []
        self._judgments: list[Judgment] = []

    def analyze_repair_target(
        self, symbol_name: str, proposed_patch_description: str
    ) -> RepairFeasibilityRecord:
        """Analyse a repair target from a natural-language description.

        The description is parsed for keywords to infer stability, delegation,
        and protocol characteristics.  The inferred characteristics are used to
        construct a ``proposed_patch`` dict that is passed to the oracle.

        Recognised keywords (case-insensitive):
        * ``"removes"`` / ``"remove"`` → adds a removed_method entry
        * ``"changes signature"`` / ``"change signature"`` → signature change
        * ``"unstable"`` → stability_level = "unstable"
        * ``"retracting"`` → stability_level = "retracting"
        * ``"collapsed"`` → stability_level = "collapsed"
        * ``"deep chain"`` / ``"deep delegation"`` → depth = 8
        * ``"cycle"`` / ``"cyclic"`` → has_cycle = True
        * ``"deprecated"`` → is_deprecated = True

        Parameters
        ----------
        symbol_name:
            Fully-qualified name of the symbol being repaired.
        proposed_patch_description:
            A free-text description of the proposed repair.

        Returns
        -------
        RepairFeasibilityRecord
            The oracle's verdict for this repair target.
        """
        desc_lower = proposed_patch_description.lower()

        # copilot: infer stability_level from description keywords
        if "collapsed" in desc_lower:
            stability_level = "collapsed"
        elif "retracting" in desc_lower:
            stability_level = "retracting"
        elif "unstable" in desc_lower:
            stability_level = "unstable"
        elif "degrading" in desc_lower:
            stability_level = "degrading"
        elif "stable" in desc_lower:
            stability_level = "stable"
        else:
            stability_level = "unknown"

        leaked_details: list[str] = []
        if "leak" in desc_lower or "leaked" in desc_lower or "cache" in desc_lower:
            leaked_details.append("_inferred_cache")
        is_deprecated = "deprecated" in desc_lower

        # copilot: infer delegation chain from description keywords
        if "deep chain" in desc_lower or "deep delegation" in desc_lower:
            chain_depth = 8
        elif "chain" in desc_lower or "delegation" in desc_lower:
            chain_depth = 3
        else:
            chain_depth = 1

        has_cycle = "cycle" in desc_lower or "cyclic" in desc_lower
        links: list[dict] = []
        if "transform" in desc_lower or "wrap" in desc_lower:
            links.append({"transforms_args": True, "name": "inferred-wrapper"})

        # copilot: infer protocol info from description keywords
        removed_methods: list[str] = []
        if "removes" in desc_lower or "remove" in desc_lower:
            # Extract a plausible method name from the description
            words = proposed_patch_description.split()
            for i, w in enumerate(words):
                if w.lower() in ("removes", "remove") and i + 1 < len(words):
                    removed_methods.append(words[i + 1].strip(".,;:'\"()"))
                    break
            if not removed_methods:
                removed_methods.append("__inferred_method__")

        sig_incompatibilities: list[str] = []
        if "changes signature" in desc_lower or "change signature" in desc_lower:
            sig_incompatibilities.append("inferred signature change from description")

        protocols_affected: list[str] = []
        if "protocol" in desc_lower:
            protocols_affected.append("__inferred_protocol__")

        proposed_patch = {
            "stability_info": {
                "stability_level": stability_level,
                "leaked_details": leaked_details,
                "is_deprecated": is_deprecated,
            },
            "chain_info": {
                "depth": chain_depth,
                "has_cycle": has_cycle,
                "links": links,
            },
            "protocol_info": {
                "protocols_affected": protocols_affected,
                "proposed_signature_change": bool(sig_incompatibilities),
                "removed_methods": removed_methods,
                "signature_incompatibilities": sig_incompatibilities,
            },
            "delegation_chain_depth": chain_depth,
            "protocol_violations_introduced": len(removed_methods) + len(sig_incompatibilities),
            "current_stability_level": stability_level,
        }

        record = self.oracle.assess(symbol_name, proposed_patch)
        self._records.append(record)
        return record

    def summarize_risks(self) -> dict:
        """Summarise the distribution of risks across all assessed records.

        Returns
        -------
        dict
            A dictionary with:
            ``"risk_counts"`` — mapping of RepairRisk → count of constraints,
            ``"feasibility_distribution"`` — mapping of RepairFeasibility → count,
            ``"total_records"`` — total number of records assessed,
            ``"total_blocking_constraints"`` — total blocking constraint count.
        """
        risk_counts: dict[str, int] = defaultdict(int)
        feasibility_dist: dict[str, int] = defaultdict(int)
        total_blocking = 0

        for record in self._records:
            feas_key = record.feasibility.value if hasattr(record.feasibility, "value") else str(record.feasibility)
            feasibility_dist[feas_key] += 1
            for constraint in record.constraints:
                risk_key = constraint.risk.value if hasattr(constraint.risk, "value") else str(constraint.risk)
                risk_counts[risk_key] += 1
                if constraint.is_blocking:
                    total_blocking += 1

        return {
            "risk_counts": dict(risk_counts),
            "feasibility_distribution": dict(feasibility_dist),
            "total_records": len(self._records),
            "total_blocking_constraints": total_blocking,
        }

    def emit_judgments(self) -> list[Judgment]:
        """Build Judgment objects for all assessed records.

        Each record is converted to a Judgment via the oracle's
        ``emit_judgment`` method.  The resulting judgments are cached in
        ``_judgments`` and returned.
        """
        self._judgments = [self.oracle.emit_judgment(r) for r in self._records]
        return list(self._judgments)

    def full_report(self) -> str:
        """Produce a comprehensive text report of all assessments.

        The report includes per-symbol summaries, the overall risk
        distribution, and a summary of recommendations across all records.
        """
        lines = ["=" * 70, "WhyThisMattersRepairAnalyzer — Full Report", "=" * 70]
        lines.append(f"Total records assessed: {len(self._records)}")
        summary = self.summarize_risks()
        lines.append(f"Risk distribution: {summary['risk_counts']}")
        lines.append(f"Feasibility distribution: {summary['feasibility_distribution']}")
        lines.append(f"Total blocking constraints: {summary['total_blocking_constraints']}")
        lines.append("")
        for i, record in enumerate(self._records, 1):
            lines.append(f"--- Record {i}: {record.target_symbol} ---")
            lines.append(record.summary())
            lines.append("")
        return "\n".join(lines)


# ===========================================================================
# §4.12  WhyThisMattersRepairWitness
# ===========================================================================

class WhyThisMattersRepairWitness:
    """Witnesses repair attempts at runtime and builds an empirical record.

    Repair witnesses provide empirical evidence that complements the oracle's
    static analysis.  Failed repairs are analysed for constraint-violation
    keywords so that the witness can update its understanding of which
    constraints are most commonly triggered.

    Theory alignment (Ch22 §4.12): the witness corresponds to the *empirical
    feedback loop* described in Remark 4.6 — failed repair attempts are fed
    back into the constraint knowledge base to improve future assessments.
    """

    def __init__(self) -> None:
        self._records: list[WitnessRecord] = []
        # copilot: tracks how often each constraint category was violated
        self._constraint_violations: dict[str, int] = defaultdict(int)

    def witness_repair_attempt(
        self,
        target: str,
        success: bool,
        failure_reason: str = "",
        repair_kind: str = "monkey_patch",
    ) -> WitnessRecord:
        """Witness a single repair attempt and record the outcome.

        Parameters
        ----------
        target:
            Fully-qualified name of the symbol that was being repaired.
        success:
            Whether the repair succeeded.
        failure_reason:
            Human-readable description of why the repair failed.  Only
            meaningful when ``success=False``.
        repair_kind:
            The kind of repair attempted.  Common values: "monkey_patch",
            "hot_reload", "ast_rewrite", "restart".

        Returns
        -------
        WitnessRecord
            The newly created witness record.
        """
        # copilot: infer constraint violation categories from failure_reason
        constraints_violated: list[str] = []
        if not success and failure_reason:
            reason_lower = failure_reason.lower()
            keyword_map = {
                "delegation": "delegation",
                "chain": "delegation",
                "protocol": "protocol",
                "stability": "stability",
                "unstable": "stability",
                "proxy": "proxy_expiry",
                "expired": "proxy_expiry",
                "cycle": "delegation_cycle",
                "signature": "protocol_compat",
                "attribute": "protocol_removal",
            }
            seen: set[str] = set()
            for keyword, category in keyword_map.items():
                if keyword in reason_lower and category not in seen:
                    constraints_violated.append(category)
                    seen.add(category)
                    self._constraint_violations[category] += 1

        record = WitnessRecord(
            record_id=str(uuid.uuid4()),
            target=target,
            success=success,
            failure_reason=failure_reason,
            timestamp=str(time.time()),
            repair_kind=repair_kind,
            constraints_violated=tuple(constraints_violated),
            metadata={"repair_kind": repair_kind},
        )
        self._records.append(record)
        logger.debug(
            "WitnessRecord: target=%s success=%s violated=%s",
            target, success, constraints_violated,
        )
        return record

    def all_records(self) -> list[WitnessRecord]:
        """Return all witness records collected so far."""
        return list(self._records)

    def failed_repairs(self) -> list[WitnessRecord]:
        """Return only the witness records for failed repair attempts."""
        return [r for r in self._records if not r.success]

    def success_rate(self) -> float:
        """Return the fraction of repair attempts that succeeded.

        Returns 0.0 if no attempts have been witnessed yet.
        """
        if not self._records:
            return 0.0
        successes = sum(1 for r in self._records if r.success)
        return successes / len(self._records)

    def summarize(self) -> dict:
        """Summarise all witnessed repair attempts.

        Returns
        -------
        dict
            A dictionary with:
            ``"total"`` — total number of attempts,
            ``"successes"`` — number of successful attempts,
            ``"failures"`` — number of failed attempts,
            ``"success_rate"`` — fraction of successes (float),
            ``"constraint_violations"`` — mapping of category → count.
        """
        total = len(self._records)
        successes = sum(1 for r in self._records if r.success)
        return {
            "total": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": self.success_rate(),
            "constraint_violations": dict(self._constraint_violations),
        }


# ===========================================================================
# §4.13  WhyThisMattersRepairCoordinator — top-level coordinator
# ===========================================================================

class WhyThisMattersRepairCoordinator:
    """Top-level coordinator for the §4 repair feasibility pipeline.

    Combines the analyzer and witness into a single façade that produces
    ``RepairReport`` objects and ordered repair plans.

    Theory alignment (Ch22 §4.13): the coordinator implements the *repair
    orchestration layer* described in Algorithm 4.1 — it sequences the
    analysis, witness, and reporting steps and presents the result in a form
    that can be consumed by an automated repair orchestrator or a human
    operator.

    Parameters
    ----------
    analyzer:
        A ``WhyThisMattersRepairAnalyzer`` instance.
    witness:
        A ``WhyThisMattersRepairWitness`` instance.
    """

    def __init__(
        self,
        analyzer: WhyThisMattersRepairAnalyzer,
        witness: WhyThisMattersRepairWitness,
    ) -> None:
        self.analyzer = analyzer
        self.witness = witness

    def coordinate(self, target: str) -> CoordinateObject:
        """Build a ``CoordinateObject`` identifying *target* in the site.

        The components of the coordinate are the dot-separated parts of the
        fully-qualified symbol name.

        Parameters
        ----------
        target:
            Fully-qualified name of the symbol (e.g. ``"my_pkg.MyClass.method"``).

        Returns
        -------
        CoordinateObject
            A coordinate identifying the target within the JuGeo site.
        """
        parts = tuple(target.split("."))
        # copilot: choose kind based on whether the last component looks like a method
        kind = CoordinateKind.FUNCTION if len(parts) >= 2 else CoordinateKind.MODULE
        return CoordinateObject(
            components=parts,
            kind=kind,
            support_labels=frozenset({"repair_target"}),
            metadata={"qualified_name": target},
        )

    def full_repair_assessment(self, symbol_name: str) -> RepairReport:
        """Run a full repair assessment for *symbol_name* and return a report.

        Steps:
        1. Call the analyzer to assess the symbol with a synthesised description.
        2. Collect any witness records that mention this symbol.
        3. Generate recommendations from the oracle.
        4. Build and return a ``RepairReport``.

        Parameters
        ----------
        symbol_name:
            Fully-qualified name of the symbol to assess.

        Returns
        -------
        RepairReport
            A complete repair report.
        """
        # copilot: synthesise a description that covers the most common risk factors
        description = (
            f"Automated repair assessment for {symbol_name}.  "
            "Assess for unstable surface, delegation chain, and protocol constraints."
        )
        record = self.analyzer.analyze_repair_target(symbol_name, description)

        # copilot: collect witness records for this specific target
        relevant_witnesses = tuple(
            w for w in self.witness.all_records() if w.target == symbol_name
        )

        # Generate recommendations via the oracle
        recommendations_text = self.analyzer.oracle.recommend_approach(record)
        # Split into individual sentences to produce a tuple of recommendation strings
        recommendations = tuple(
            s.strip() for s in recommendations_text.replace("  ", "\n").splitlines()
            if s.strip()
        )

        report = RepairReport(
            report_id=str(uuid.uuid4()),
            target_symbol=symbol_name,
            feasibility_record=record,
            constraints=record.constraints,
            witness_records=relevant_witnesses,
            recommendations=recommendations,
            generated_at=str(time.time()),
        )
        return report

    def emit_judgments(self) -> list[Judgment]:
        """Delegate judgment emission to the analyzer.

        Returns
        -------
        list[Judgment]
            All judgments emitted by the analyzer for its assessed records.
        """
        return self.analyzer.emit_judgments()

    def generate_repair_plan(self, report: RepairReport) -> list[str]:
        """Generate an ordered, actionable repair plan from *report*.

        The plan is tailored to the ``RepairFeasibility`` verdict and the
        specific constraints found.  If the feasibility is IMPOSSIBLE, the
        plan contains only a single blocking step advising the operator.

        Theory alignment (Ch22 §4.13, Algorithm 4.1): the repair plan is the
        output of the orchestration layer and is consumed by automated
        repair tooling or presented to the human operator.

        Parameters
        ----------
        report:
            A ``RepairReport`` produced by ``full_repair_assessment``.

        Returns
        -------
        list[str]
            Ordered list of repair step descriptions.
        """
        feas = report.feasibility_record.feasibility
        target = report.target_symbol
        depth = report.feasibility_record.delegation_chain_depth
        violations = report.feasibility_record.protocol_violations_introduced
        blocking = report.feasibility_record.blocking_constraints()

        # copilot: if repair is impossible, return a single advisory step
        if feas == RepairFeasibility.IMPOSSIBLE:
            return [
                f"[BLOCKED] Repair of '{target}' is IMPOSSIBLE via automated means.  "
                f"Blocking constraints: {'; '.join(c.description[:60] for c in blocking)}.  "
                "Manual intervention required — do not proceed with automated repair."
            ]

        steps: list[str] = []

        # Step 1: always start by confirming the current stability level
        steps.append(
            f"[1] Assess current stability level of '{target}'.  "
            f"Expected level: {report.feasibility_record.stability_level}.  "
            "Abort if level has changed to RETRACTING or COLLAPSED since assessment."
        )

        # Step 2: trace the delegation chain
        if depth > 0:
            steps.append(
                f"[2] Trace delegation chain for '{target}' to depth {depth}.  "
                "Identify all links that will require independent patching.  "
                "Check for cycles — if a cycle is detected, abort and restart."
            )
        else:
            steps.append(
                f"[2] Confirm '{target}' has no delegation chain (depth=0).  "
                "If a chain is discovered at runtime, re-run the analysis."
            )

        # Step 3: check protocol satisfaction before patching
        if violations > 0:
            steps.append(
                f"[3] WARNING: patch introduces {violations} protocol violation(s).  "
                "Review each violation and confirm that affected protocols can be "
                "re-satisfied by updating the protocol implementations alongside the patch."
            )
        else:
            steps.append(
                f"[3] Verify protocol satisfaction for '{target}'.  "
                "Confirm that the proposed patch does not remove any required methods "
                "or change any signatures in a backward-incompatible way."
            )

        # Step 4: apply the patch
        if depth > 1:
            steps.append(
                f"[4] Apply the patch to the tail of the delegation chain "
                f"(not just the head) — {depth} link(s) require patching.  "
                "Patch innermost objects first, then work outward toward the head."
            )
        else:
            steps.append(
                f"[4] Apply the patch directly to '{target}'."
            )

        # Step 5: caution steps based on feasibility
        if feas == RepairFeasibility.RISKY:
            steps.append(
                "[5] Post-patch verification (REQUIRED due to RISKY verdict).  "
                "Exercise all public entry points of the patched symbol.  "
                "Run the protocol satisfaction checker.  Monitor logs for 60 s."
            )
        elif feas == RepairFeasibility.REQUIRES_RESTART:
            steps.append(
                "[5] Trigger a controlled module reload (or process restart if "
                "cross-process).  The in-place patch is insufficient — a restart "
                "is required to clear all stale references in the delegation chain."
            )
        else:
            steps.append(
                "[5] Monitor for 30 s post-patch.  "
                "Confirm that the updated implementation is reachable from all "
                "known call sites using spot-checks or existing test suite."
            )

        # Step 6: witness recording
        steps.append(
            f"[6] Record the repair attempt as a WitnessRecord for '{target}'.  "
            "On failure, capture the full error message so that constraint-violation "
            "keywords can be extracted and fed back into the analysis."
        )

        # Step 7: emit judgment
        steps.append(
            f"[7] Emit a Judgment for '{target}' reflecting the repair outcome.  "
            "If the repair succeeded, the judgment trust level should be "
            "RUNTIME_WITNESSED.  If it failed, ORACLE_PROPOSED with obstructions."
        )

        # Append any blocking constraint mitigations as advisory steps
        for i, bc in enumerate(blocking, 8):
            steps.append(
                f"[{i}] [ADVISORY — blocking constraint] {bc.mitigation}"
            )

        return steps


# ===========================================================================
# §4 smoke test — verifies the full repair feasibility assessment pipeline
# ===========================================================================

if __name__ == "__main__":
    import sys
    # copilot: smoke test — verifies repair feasibility assessment pipeline
    print(f"[smoke] {__file__}")
    try:
        stability_analyzer = StabilityRepairAnalyzer()
        constraints = stability_analyzer.analyze(
            "my_module.MyClass.method",
            {"stability_level": "unstable", "leaked_details": ["_internal_cache", "_impl"]},
        )
        print(f"[smoke] stability constraints: {len(constraints)}")

        delegation_analyzer = DelegationRepairAnalyzer()
        d_constraints = delegation_analyzer.analyze(
            "my_module.MyClass.method",
            {"depth": 4, "has_cycle": False, "links": [{"transforms_args": True, "name": "outer->inner"}]},
        )
        print(f"[smoke] delegation constraints: {len(d_constraints)}")

        protocol_analyzer = ProtocolRepairAnalyzer()
        p_constraints = protocol_analyzer.analyze(
            "my_module.MyClass.method",
            {"protocols_affected": ["Drawable", "Serializable"], "proposed_signature_change": True,
             "removed_methods": [], "signature_incompatibilities": ["draw signature changed"]},
        )
        print(f"[smoke] protocol constraints: {len(p_constraints)}")

        oracle = RepairFeasibilityOracle(
            stability_analyzer=StabilityRepairAnalyzer(),
            delegation_analyzer=DelegationRepairAnalyzer(),
            protocol_analyzer=ProtocolRepairAnalyzer(),
        )
        proposed = {
            "stability_info": {"stability_level": "unstable", "leaked_details": []},
            "chain_info": {"depth": 2, "has_cycle": False, "links": []},
            "protocol_info": {"protocols_affected": [], "proposed_signature_change": False,
                              "removed_methods": [], "signature_incompatibilities": []},
            "delegation_chain_depth": 2,
            "protocol_violations_introduced": 0,
            "current_stability_level": "unstable",
        }
        record = oracle.assess("my_module.MyClass", proposed)
        print(f"[smoke] feasibility: {record.feasibility} highest_risk={record.highest_risk}")

        analyzer = WhyThisMattersRepairAnalyzer(oracle=oracle)
        rec = analyzer.analyze_repair_target("my_module.MyClass", "changes signature of unstable method")
        print(f"[smoke] WhyThisMatters record: {rec.target_symbol}")

        witness = WhyThisMattersRepairWitness()
        wr = witness.witness_repair_attempt("my_module.MyClass", False, "delegation chain blocked patch")
        print(f"[smoke] witness: success={wr.success} violated={wr.constraints_violated}")

        coordinator = WhyThisMattersRepairCoordinator(analyzer=analyzer, witness=witness)
        coord = coordinator.coordinate("my_module.MyClass")
        print(f"[smoke] coordinate: {coord.components}")
        report = coordinator.full_repair_assessment("my_module.MyClass")
        plan = coordinator.generate_repair_plan(report)
        print(f"[smoke] repair plan steps: {len(plan)}")
        for step in plan[:3]:
            print(f"[smoke]   {step}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
