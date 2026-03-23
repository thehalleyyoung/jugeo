"""
Implementation consequences for inhabitant fleet management.

This module formalises the downstream obligations that arise when a fleet
management theory is committed to a configuration.  Every fleet management
decision has observable consequences on

    * the structural composition of the fleet (which inhabitant variants are
      admitted, in what numbers, and with what diversity constraints),
    * the behavioural envelope that the fleet must stay within at runtime
      (quorum rules, latency budgets, trust-tier floors),
    * the relational invariants that must hold between fleet members
      (co-location constraints, leader-follower topology, etc.).

This module provides the machinery to

    1. *derive* a concrete set of :class:`FleetImplementationConsequence`
       objects from a fleet theory dict produced by earlier pipeline stages,
    2. *audit* a live fleet state against a :class:`FleetPolicy` and a list of
       :class:`FleetConstraint` objects, and
    3. *enforce* individual consequences by refusing fleet configurations that
       violate them and emitting structured failures through the JuGeo error
       hierarchy.

Design notes
~~~~~~~~~~~~
All trust reasoning is carried out through the :class:`TrustTier` lattice –
never through floating-point scores alone.  Judgments are first-class objects
(:class:`Judgment`) so that audit trails are fully reconstructable.
Čech-cohomology obstructions (:class:`CechObstruction`) capture the cases
where local policy satisfaction does not glue to a global fleet invariant.

    # copilot: implementation consequences for inhabitant fleet management

References
~~~~~~~~~~
* Čech cohomology in distributed constraint satisfaction –
  see ``jugeo.geometry.cech`` for the cover algebra.
* Trust lattice algebra – see ``jugeo.judgments.trust_algebra``.
* Fleet theory format – see ``jugeo.generation.inhabitant_fleets.theory``.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import abc
import collections
import datetime
import functools
import hashlib
import itertools
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Conditional jugeo imports with fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_FLEET: int = 8
"""Default upper bound on the number of inhabitants in a single fleet."""

MIN_DIVERSITY_SCORE: float = 0.5
"""Minimum Shannon-normalised diversity score required for a healthy fleet.

A fleet with a single homogeneous inhabitant type scores 0.0; a fleet whose
inhabitants are uniformly distributed across all known variant types scores
1.0.  The default threshold of 0.5 ensures at least moderate heterogeneity.
"""

AUDIT_VERSION: str = "2.0"
"""Serialisation version tag embedded in every :class:`FleetAudit` export."""

MAX_POLICY_VIOLATIONS: int = 3
"""Maximum number of policy violations before the fleet is considered
administratively non-compliant and the audit is automatically failed."""

_CONSEQUENCE_KINDS: Tuple[str, ...] = (
    "structural",
    "behavioral",
    "relational",
    "topology",
    "latency",
    "quorum",
    "diversity",
    "trust",
    "lifecycle",
)
"""Enumeration of recognised consequence kinds used during derivation."""

_CONSTRAINT_KINDS: Tuple[str, ...] = (
    "size",
    "diversity",
    "latency",
    "throughput",
    "trust_floor",
    "quorum_ratio",
    "cohort_balance",
    "leader_count",
)
"""Enumeration of recognised constraint kinds used during evaluation."""

_DEFAULT_TIMEOUT_S: float = 30.0
"""Default search timeout in seconds for fleet operations."""

_HASH_ALGORITHM: str = "sha256"
"""Hash algorithm used when constructing deterministic consequence IDs."""

# ---------------------------------------------------------------------------
# Trust tier lattice
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The lattice is totally ordered::

        PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼ RUNTIME_WITNESSED ≼ PROOF_BACKED

    The binary operations ``join`` (⊕) and ``meet`` (⊖) implement the
    lattice-theoretic least-upper-bound and greatest-lower-bound respectively.
    ``promote`` (↑_π) and ``demote`` (↓_χ) are the unit-step coercion maps
    used when evidence accumulates or is retracted.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Return the least upper bound of *self* and *other* in the lattice."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Return the greatest lower bound of *self* and *other* in the lattice."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Advance one tier towards ``PROOF_BACKED``; idempotent at the top."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Retreat one tier towards ``PROPOSAL``; idempotent at the bottom."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    A judgment is the fundamental epistemic unit in the JuGeo reasoning
    framework.  It binds a *formula* φ (the claim being made) to the
    *context* c in which it was derived, together with

    * **A** – the set of assumptions on which the derivation depends,
    * **E** – the evidence that supports the claim,
    * **O** – the obligations that remain open (proof obligations, runtime
      checks, etc.),
    * **B** – the *burden* distribution (who owns which obligation),
    * **T** – a :class:`TrustTier` level summarising overall confidence, and
    * **Π** – provenance metadata (solver run, oracle call, human annotation).

    Judgments are immutable so they can be stored in sets and used as dict
    keys without defensive copying.
    """

    context: Any
    """The derivation context – typically a fleet theory id or audit session."""

    formula: Any
    """The logical formula / claim being judged."""

    assumptions: tuple
    """Ordered tuple of assumption labels or objects on which the claim relies."""

    evidence: tuple
    """Ordered tuple of :class:`EvidenceItemKind` values or raw evidence objects."""

    obligations: tuple
    """Remaining proof obligations that must be discharged before the judgment
    can be promoted to ``PROOF_BACKED``."""

    burden: Any
    """The party responsible for discharging the obligations (agent id, role, etc.)."""

    trust: TrustTier
    """The trust tier of the judgment under the current evidence."""

    provenance: Any
    """Free-form provenance record – solver run identifier, oracle call, etc."""


@dataclass(frozen=True)
class CechObstruction:
    """A Čech-cohomology obstruction to globalising local fleet satisfiability.

    When a set of local policy checks all pass individually but cannot be
    assembled into a globally consistent fleet state, the failure is
    captured as a non-trivial cocycle in the Čech complex of the cover
    induced by the policy constraints.

    Fields
    ------
    cover_id:
        Identifier of the open cover under which the obstruction was detected.
    cocycle:
        The set of constraint pairs (as frozen edge-set) that constitute the
        non-trivial 1-cocycle.
    cohomology_class:
        Human-readable name of the cohomology class (e.g. ``"H¹_quorum"``).
    description:
        Natural-language explanation of why globalisation fails.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the cocycle is the zero element (no obstruction)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _stable_id(*parts: str) -> str:
    """Produce a stable, collision-resistant identifier from *parts*.

    Uses SHA-256 over the UTF-8 encoding of the ``|``-joined parts and
    returns the first 16 hex characters, which gives a 64-bit address space
    sufficient for in-process IDs.

    Parameters
    ----------
    *parts:
        String fragments that characterise the object being identified.

    Returns
    -------
    str
        A 16-character lowercase hex string.
    """
    raw = "|".join(parts).encode("utf-8")
    digest = hashlib.new(_HASH_ALGORITHM, raw).hexdigest()
    return digest[:16]


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _diversity_score(fleet_state: dict) -> float:
    """Compute the Shannon-normalised diversity score for *fleet_state*.

    The score is defined as

        H_norm = -Σ_i p_i log2(p_i) / log2(k)

    where *p_i* is the relative frequency of variant type *i* and *k* is the
    number of distinct types.  Returns 0.0 for an empty fleet or a fleet
    with a single type, and 1.0 for a perfectly uniform distribution.

    Parameters
    ----------
    fleet_state:
        Dict containing at least ``"members"`` (list of dicts with a
        ``"variant"`` key).

    Returns
    -------
    float
        Diversity score in [0.0, 1.0].
    """
    members: list = fleet_state.get("members", [])
    if not members:
        return 0.0
    counts: Dict[str, int] = collections.Counter(m.get("variant", "unknown") for m in members)
    k = len(counts)
    if k == 1:
        return 0.0
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    return entropy / math.log2(k)


def _quorum_ratio(fleet_state: dict) -> float:
    """Return the fraction of fleet members currently in quorum.

    Expects ``fleet_state["quorum_members"]`` (int) and
    ``fleet_state["total_members"]`` (int).  Returns 0.0 if the fleet is
    empty or the keys are absent.
    """
    total = fleet_state.get("total_members", len(fleet_state.get("members", [])))
    if total == 0:
        return 0.0
    quorum = fleet_state.get("quorum_members", total)
    return min(quorum / total, 1.0)


def _min_trust_in_fleet(fleet_state: dict) -> TrustTier:
    """Return the lowest :class:`TrustTier` observed across all fleet members.

    Members that do not carry a ``"trust_tier"`` field are assigned
    ``TrustTier.PROPOSAL`` (the minimum).
    """
    members: list = fleet_state.get("members", [])
    if not members:
        return TrustTier.PROPOSAL
    tiers = (TrustTier(m.get("trust_tier", TrustTier.PROPOSAL.value)) for m in members)
    return functools.reduce(TrustTier.meet, tiers)


def _parse_bound(raw: Any) -> Optional[float]:
    """Safely coerce *raw* to float, returning ``None`` on failure."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _consequence_from_rule(
    rule: dict,
    fleet_theory_id: str,
    index: int,
) -> "FleetImplementationConsequence":
    """Construct a :class:`FleetImplementationConsequence` from a theory rule.

    Parameters
    ----------
    rule:
        A rule dict from the fleet theory, expected to contain keys
        ``"kind"``, ``"description"``, ``"components"``, ``"mandatory"``, and
        ``"priority"``.
    fleet_theory_id:
        Identifier of the originating fleet theory.
    index:
        Positional index used to make the consequence id deterministic.

    Returns
    -------
    FleetImplementationConsequence
    """
    kind = rule.get("kind", "structural")
    description = rule.get("description", f"Consequence #{index} of {fleet_theory_id}")
    components = tuple(rule.get("components", []))
    mandatory = bool(rule.get("mandatory", True))
    priority = int(rule.get("priority", 5))
    raw_trust = rule.get("trust_tier", TrustTier.PROPOSAL.value)
    try:
        trust = TrustTier(int(raw_trust))
    except (ValueError, KeyError):
        trust = TrustTier.PROPOSAL

    cid = _stable_id(fleet_theory_id, kind, description, str(index))
    return FleetImplementationConsequence(
        consequence_id=cid,
        fleet_theory_id=fleet_theory_id,
        requirement_kind=kind,
        description=description,
        affected_components=components,
        trust_tier=trust,
        priority=priority,
        is_mandatory=mandatory,
    )


def _build_default_constraints_from_policy(policy: "FleetPolicy") -> List["FleetConstraint"]:
    """Materialise :class:`FleetConstraint` objects implied by *policy*.

    This is the bridge between the high-level :class:`FleetPolicy` and the
    low-level :class:`FleetConstraint` evaluator.  Every policy field that
    expresses a numeric or boolean limit becomes a constraint.
    """
    constraints: List[FleetConstraint] = []

    # Size constraint
    constraints.append(FleetConstraint(
        constraint_id=_stable_id(policy.policy_id, "size"),
        constraint_kind="size",
        expression="len(members) <= max_fleet_size",
        bound=float(policy.max_fleet_size),
        is_upper_bound=True,
        trust_tier=policy.min_trust_tier,
    ))

    # Diversity constraint
    constraints.append(FleetConstraint(
        constraint_id=_stable_id(policy.policy_id, "diversity"),
        constraint_kind="diversity",
        expression="diversity_score >= min_diversity_score",
        bound=policy.min_diversity_score,
        is_upper_bound=False,
        trust_tier=policy.min_trust_tier,
    ))

    # Prune-score constraint
    constraints.append(FleetConstraint(
        constraint_id=_stable_id(policy.policy_id, "prune"),
        constraint_kind="cohort_balance",
        expression="min_member_score >= prune_below_score",
        bound=policy.prune_below_score,
        is_upper_bound=False,
        trust_tier=policy.min_trust_tier,
    ))

    # Quorum constraint (if required)
    if policy.require_quorum:
        constraints.append(FleetConstraint(
            constraint_id=_stable_id(policy.policy_id, "quorum"),
            constraint_kind="quorum_ratio",
            expression="quorum_ratio >= 0.51",
            bound=0.51,
            is_upper_bound=False,
            trust_tier=policy.min_trust_tier,
        ))

    return constraints


def _format_violation(violation: dict) -> str:
    """Format a violation dict as a human-readable one-liner."""
    cid = violation.get("constraint_id", "?")
    kind = violation.get("constraint_kind", "?")
    detail = violation.get("detail", "")
    return f"[{kind}:{cid[:8]}] {detail}"


def _collect_evidence_for_constraint(
    constraint: "FleetConstraint",
    fleet_state: dict,
    passed: bool,
) -> Tuple[Any, ...]:
    """Return an evidence tuple for *constraint* given the evaluation result."""
    return (
        EvidenceItemKind.RUNTIME_WITNESS,
        f"constraint={constraint.constraint_id[:8]}",
        f"kind={constraint.constraint_kind}",
        f"passed={passed}",
    )


# ---------------------------------------------------------------------------
# FleetImplementationConsequence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FleetImplementationConsequence:
    """A concrete implementation requirement arising from fleet management theory.

    Every fleet management decision induces *implementation consequences* –
    non-negotiable requirements that the fleet configuration must satisfy.
    This class reifies those consequences as first-class objects so they can
    be tracked, audited, and enforced independently.

    The :meth:`to_judgment` method lifts the consequence into the JuGeo
    judgment framework, enabling it to participate in the global reasoning
    graph.

    Parameters
    ----------
    consequence_id:
        Stable 16-hex-char identifier derived from the theory and kind.
    fleet_theory_id:
        Identifier of the fleet theory from which this consequence was derived.
    requirement_kind:
        One of the recognised consequence kinds in ``_CONSEQUENCE_KINDS``.
    description:
        Human-readable description of what the requirement demands.
    affected_components:
        Immutable tuple of component names / role identifiers that the
        requirement bears on.
    trust_tier:
        The :class:`TrustTier` level at which the consequence was derived.
    priority:
        Integer priority (1 = lowest, 10 = highest).  Higher-priority
        consequences are enforced first.
    is_mandatory:
        Whether a violation of this consequence constitutes a hard failure
        (``True``) or merely a warning (``False``).
    """

    consequence_id: str
    fleet_theory_id: str
    requirement_kind: str
    description: str
    affected_components: tuple
    trust_tier: TrustTier
    priority: int
    is_mandatory: bool

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def to_judgment(self) -> Judgment:
        """Lift this consequence into a :class:`Judgment`.

        The judgment formula is the consequence description.  Evidence consists
        of the consequence kind and the affected components.  Obligations are
        empty because the consequence itself does not carry proof obligations –
        those are attached by the audit machinery.

        Returns
        -------
        Judgment
        """
        evidence = (
            EvidenceItemKind.ORACLE_PROPOSAL,
            f"theory={self.fleet_theory_id}",
            f"kind={self.requirement_kind}",
        )
        return Judgment(
            context=self.fleet_theory_id,
            formula=self.description,
            assumptions=(f"theory:{self.fleet_theory_id}",),
            evidence=evidence,
            obligations=(),
            burden=f"fleet:{self.fleet_theory_id}",
            trust=self.trust_tier,
            provenance=ProvenanceSource.ORACLE,
        )

    def describe_requirement(self) -> str:
        """Return a multi-line human-readable summary of the requirement.

        Returns
        -------
        str
            Formatted description including id, kind, priority, mandatory
            flag, affected components, and trust tier.
        """
        mandatory_tag = "MANDATORY" if self.is_mandatory else "advisory"
        components_str = ", ".join(self.affected_components) if self.affected_components else "(all)"
        return (
            f"FleetImplementationConsequence\n"
            f"  id        : {self.consequence_id}\n"
            f"  theory    : {self.fleet_theory_id}\n"
            f"  kind      : {self.requirement_kind}\n"
            f"  priority  : {self.priority}/10\n"
            f"  status    : {mandatory_tag}\n"
            f"  trust     : {self.trust_tier.name}\n"
            f"  components: {components_str}\n"
            f"  desc      : {self.description}"
        )

    def affects(self, component: str) -> bool:
        """Return ``True`` if *component* is in :attr:`affected_components`.

        An empty ``affected_components`` is interpreted as *all components*
        and therefore always returns ``True``.

        Parameters
        ----------
        component:
            The component name to test.

        Returns
        -------
        bool
        """
        if not self.affected_components:
            return True
        return component in self.affected_components


# ---------------------------------------------------------------------------
# FleetPolicy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FleetPolicy:
    """A policy governing the behavioural envelope of an inhabitant fleet.

    A :class:`FleetPolicy` specifies *what the fleet is allowed to look like*
    at any point in time.  It does not specify *how* the fleet reaches that
    state – that is the responsibility of the planner and the scheduler.

    Parameters
    ----------
    policy_id:
        Stable identifier for this policy instance.
    max_fleet_size:
        Hard upper bound on the number of active fleet members.
    min_diversity_score:
        Minimum Shannon-normalised diversity score (see :func:`_diversity_score`).
    search_timeout_s:
        Maximum wall-clock seconds to spend on fleet search / planning.
    require_quorum:
        Whether the fleet must maintain a quorum (>50 % of members active).
    min_trust_tier:
        The lowest :class:`TrustTier` that a fleet member may carry.
    prune_below_score:
        Fleet members with a score below this threshold are eligible for
        pruning during the next rebalancing cycle.
    """

    policy_id: str
    max_fleet_size: int = DEFAULT_MAX_FLEET
    min_diversity_score: float = MIN_DIVERSITY_SCORE
    search_timeout_s: float = _DEFAULT_TIMEOUT_S
    require_quorum: bool = True
    min_trust_tier: TrustTier = TrustTier.REVIEWED
    prune_below_score: float = 0.2

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def validate(self, fleet_config: dict) -> List[str]:
        """Check *fleet_config* against the policy and return a list of errors.

        Each error is a human-readable string.  An empty list means the
        configuration is compliant.

        Parameters
        ----------
        fleet_config:
            Dict describing the proposed fleet configuration.  Expected keys:
            ``"size"`` (int), ``"diversity_score"`` (float),
            ``"timeout_s"`` (float), ``"min_trust_tier"`` (int).

        Returns
        -------
        list[str]
            List of human-readable violation messages.
        """
        errors: List[str] = []

        size = fleet_config.get("size", 0)
        if size > self.max_fleet_size:
            errors.append(
                f"Fleet size {size} exceeds policy maximum {self.max_fleet_size}."
            )

        diversity = fleet_config.get("diversity_score", 0.0)
        if diversity < self.min_diversity_score:
            errors.append(
                f"Diversity score {diversity:.3f} is below policy minimum "
                f"{self.min_diversity_score:.3f}."
            )

        timeout = fleet_config.get("timeout_s", 0.0)
        if timeout > self.search_timeout_s:
            errors.append(
                f"Requested timeout {timeout}s exceeds policy limit "
                f"{self.search_timeout_s}s."
            )

        raw_trust = fleet_config.get("min_trust_tier", TrustTier.PROPOSAL.value)
        try:
            config_trust = TrustTier(int(raw_trust))
        except (ValueError, KeyError):
            config_trust = TrustTier.PROPOSAL
        if config_trust < self.min_trust_tier:
            errors.append(
                f"Fleet trust tier {config_trust.name} is below policy floor "
                f"{self.min_trust_tier.name}."
            )

        return errors

    def to_judgment(self) -> Judgment:
        """Reify this policy as a :class:`Judgment` at ``REVIEWED`` trust.

        Returns
        -------
        Judgment
        """
        formula = (
            f"policy:{self.policy_id} requires max_size={self.max_fleet_size}, "
            f"min_diversity={self.min_diversity_score}, "
            f"min_trust={self.min_trust_tier.name}"
        )
        return Judgment(
            context=f"policy:{self.policy_id}",
            formula=formula,
            assumptions=("fleet_policy_registry",),
            evidence=(EvidenceItemKind.ORACLE_PROPOSAL, f"policy_id={self.policy_id}"),
            obligations=(),
            burden="policy_enforcer",
            trust=TrustTier.REVIEWED,
            provenance=ProvenanceSource.HUMAN,
        )

    def is_satisfied_by(self, fleet_state: dict) -> bool:
        """Return ``True`` if the live *fleet_state* satisfies every policy rule.

        Parameters
        ----------
        fleet_state:
            Dict describing the current fleet state.  Must contain
            ``"members"`` (list of member dicts).

        Returns
        -------
        bool
        """
        members = fleet_state.get("members", [])
        if len(members) > self.max_fleet_size:
            return False
        if _diversity_score(fleet_state) < self.min_diversity_score:
            return False
        if self.require_quorum and _quorum_ratio(fleet_state) < 0.51:
            return False
        if _min_trust_in_fleet(fleet_state) < self.min_trust_tier:
            return False
        # prune-score check
        scores = [m.get("score", 1.0) for m in members]
        if scores and min(scores) < self.prune_below_score:
            return False
        return True


# ---------------------------------------------------------------------------
# FleetConstraint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FleetConstraint:
    """A hard constraint that the fleet must satisfy at all times.

    Unlike :class:`FleetPolicy` (which describes the desired envelope),
    a :class:`FleetConstraint` is a single, machine-checkable predicate that
    is either satisfied or violated – no partial credit.

    Parameters
    ----------
    constraint_id:
        Stable identifier for this constraint.
    constraint_kind:
        One of the kinds in ``_CONSTRAINT_KINDS``.
    expression:
        Human-readable expression of the constraint (not evaluated by Python
        ``eval`` – see :meth:`evaluate` for the dispatch logic).
    bound:
        Numeric threshold associated with the constraint, or ``None`` if the
        constraint is purely Boolean.
    is_upper_bound:
        If ``True`` the constraint checks that the measured value is
        *at most* ``bound``; if ``False`` it checks that the value is
        *at least* ``bound``.
    trust_tier:
        The :class:`TrustTier` assigned to judgments produced from this
        constraint.
    """

    constraint_id: str
    constraint_kind: str
    expression: str
    bound: Optional[float]
    is_upper_bound: bool
    trust_tier: TrustTier

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def evaluate(self, fleet_state: dict) -> bool:
        """Evaluate the constraint against *fleet_state*.

        Dispatches to the appropriate metric function based on
        :attr:`constraint_kind`.

        Parameters
        ----------
        fleet_state:
            Current fleet state dict.

        Returns
        -------
        bool
            ``True`` if the constraint is satisfied.
        """
        if self.bound is None:
            flag = fleet_state.get(f"constraint_{self.constraint_id}", True)
            return bool(flag)

        kind = self.constraint_kind
        if kind == "size":
            measured = float(len(fleet_state.get("members", [])))
        elif kind == "diversity":
            measured = _diversity_score(fleet_state)
        elif kind == "quorum_ratio":
            measured = _quorum_ratio(fleet_state)
        elif kind == "trust_floor":
            measured = float(_min_trust_in_fleet(fleet_state).value)
        elif kind == "cohort_balance":
            scores = [m.get("score", 1.0) for m in fleet_state.get("members", [])]
            measured = min(scores) if scores else 0.0
        elif kind == "latency":
            measured = float(fleet_state.get("p99_latency_ms", 0.0))
        elif kind == "throughput":
            measured = float(fleet_state.get("throughput_rps", 0.0))
        elif kind == "leader_count":
            leaders = [m for m in fleet_state.get("members", []) if m.get("is_leader")]
            measured = float(len(leaders))
        else:
            logger.warning("Unknown constraint kind %r – treating as satisfied.", kind)
            return True

        if self.is_upper_bound:
            return measured <= self.bound
        else:
            return measured >= self.bound

    def to_judgment(self) -> Judgment:
        """Reify this constraint as a :class:`Judgment`.

        Returns
        -------
        Judgment
        """
        direction = "≤" if self.is_upper_bound else "≥"
        formula = (
            f"constraint:{self.constraint_id[:8]} "
            f"{self.constraint_kind} {direction} {self.bound}"
        )
        return Judgment(
            context=f"constraint:{self.constraint_id}",
            formula=formula,
            assumptions=("fleet_state",),
            evidence=(EvidenceItemKind.RUNTIME_WITNESS, f"kind={self.constraint_kind}"),
            obligations=(f"evaluate:{self.constraint_id}",),
            burden="fleet_auditor",
            trust=self.trust_tier,
            provenance=ProvenanceSource.RUNTIME,
        )

    def describe(self) -> str:
        """Return a one-line description of this constraint.

        Returns
        -------
        str
        """
        direction = "at most" if self.is_upper_bound else "at least"
        bound_str = f"{direction} {self.bound}" if self.bound is not None else "Boolean"
        return (
            f"[{self.constraint_kind}] {self.expression} "
            f"(bound: {bound_str}, trust: {self.trust_tier.name})"
        )


# ---------------------------------------------------------------------------
# FleetAudit
# ---------------------------------------------------------------------------

class FleetAudit:
    """A structured compliance report for a fleet against its policy and constraints.

    :class:`FleetAudit` aggregates the results of evaluating every
    :class:`FleetConstraint` in its list against a live fleet state and
    checking the :class:`FleetPolicy` rules.  The audit is the authoritative
    record of fleet compliance for a given point in time.

    Parameters
    ----------
    fleet_id:
        Identifier of the fleet being audited.
    policy:
        The governing :class:`FleetPolicy`.
    constraints:
        Initial list of :class:`FleetConstraint` objects to evaluate.
    """

    def __init__(
        self,
        fleet_id: str,
        policy: FleetPolicy,
        constraints: Optional[List[FleetConstraint]] = None,
    ) -> None:
        self.audit_id: str = str(uuid.uuid4())
        self.fleet_id: str = fleet_id
        self.policy: FleetPolicy = policy
        self.constraints: List[FleetConstraint] = list(constraints or [])
        self.violations: List[dict] = []
        self.passed: List[dict] = []
        self.timestamp: str = _now_iso()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def run(self, fleet_state: dict) -> dict:
        """Evaluate all constraints and policy rules against *fleet_state*.

        Populates :attr:`violations` and :attr:`passed` and returns a summary
        dict.

        Parameters
        ----------
        fleet_state:
            Current fleet state dict.

        Returns
        -------
        dict
            A structured audit result dict containing ``"passed"``,
            ``"violations"``, ``"compliant"``, and ``"audit_id"``.
        """
        self.violations.clear()
        self.passed.clear()
        self.timestamp = _now_iso()

        # Check policy-level rules
        policy_errors = self.policy.validate(_fleet_state_to_config(fleet_state))
        for err in policy_errors:
            self.violations.append({
                "source": "policy",
                "constraint_id": self.policy.policy_id,
                "constraint_kind": "policy",
                "detail": err,
            })

        # Check individual constraints
        for constraint in self.constraints:
            ok = constraint.evaluate(fleet_state)
            record: dict = {
                "source": "constraint",
                "constraint_id": constraint.constraint_id,
                "constraint_kind": constraint.constraint_kind,
                "detail": constraint.describe(),
                "passed": ok,
            }
            if ok:
                self.passed.append(record)
            else:
                self.violations.append(record)

        compliant = (
            len(self.violations) == 0
            or (
                len(self.violations) <= MAX_POLICY_VIOLATIONS
                and not any(
                    v.get("constraint_kind") in ("size", "quorum_ratio")
                    for v in self.violations
                )
            )
        )

        logger.info(
            "FleetAudit %s completed: %d passed, %d violations, compliant=%s",
            self.audit_id[:8],
            len(self.passed),
            len(self.violations),
            compliant,
        )

        return {
            "audit_id": self.audit_id,
            "fleet_id": self.fleet_id,
            "timestamp": self.timestamp,
            "passed": len(self.passed),
            "violations": len(self.violations),
            "compliant": compliant,
        }

    def add_constraint(self, c: FleetConstraint) -> None:
        """Append *c* to the constraint list.

        Parameters
        ----------
        c:
            The :class:`FleetConstraint` to add.
        """
        self.constraints.append(c)
        logger.debug("Added constraint %s to audit %s.", c.constraint_id[:8], self.audit_id[:8])

    def summarize(self) -> str:
        """Return a multi-line human-readable audit summary.

        Returns
        -------
        str
        """
        lines = [
            f"FleetAudit Summary",
            f"  audit_id  : {self.audit_id}",
            f"  fleet_id  : {self.fleet_id}",
            f"  timestamp : {self.timestamp}",
            f"  policy    : {self.policy.policy_id}",
            f"  passed    : {len(self.passed)}",
            f"  violations: {len(self.violations)}",
        ]
        if self.violations:
            lines.append("  Violation details:")
            for v in self.violations:
                lines.append(f"    * {_format_violation(v)}")
        if self.passed:
            lines.append("  Passed checks:")
            for p in self.passed:
                lines.append(f"    + [{p.get('constraint_kind', '?')}] {p.get('detail', '')}")
        return "\n".join(lines)

    def export(self) -> dict:
        """Export the audit as a serialisable dict (suitable for JSON).

        Returns
        -------
        dict
        """
        return {
            "audit_version": AUDIT_VERSION,
            "audit_id": self.audit_id,
            "fleet_id": self.fleet_id,
            "policy_id": self.policy.policy_id,
            "timestamp": self.timestamp,
            "passed": [dict(p) for p in self.passed],
            "violations": [dict(v) for v in self.violations],
            "constraint_count": len(self.constraints),
            "compliant": len(self.violations) == 0,
        }


# ---------------------------------------------------------------------------
# ConsequenceManager
# ---------------------------------------------------------------------------

class ConsequenceManager:
    """Derives and tracks implementation consequences for a fleet theory.

    The :class:`ConsequenceManager` is the top-level orchestrator for
    consequence reasoning.  It receives a fleet theory dict, derives the
    complete set of :class:`FleetImplementationConsequence` objects, tracks
    them in an internal registry, and enforces them against live fleet states
    on demand.

    Parameters
    ----------
    fleet_theory_id:
        Identifier of the fleet theory being managed.
    """

    def __init__(self, fleet_theory_id: str) -> None:
        self.manager_id: str = str(uuid.uuid4())
        self.fleet_theory_id: str = fleet_theory_id
        self.consequences: List[FleetImplementationConsequence] = []
        self.derived_count: int = 0
        self.enforcement_log: List[dict] = []

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def derive(self, theory: dict) -> List[FleetImplementationConsequence]:
        """Derive :class:`FleetImplementationConsequence` objects from *theory*.

        Analyses the rules and invariants declared in *theory* and produces a
        consequence for each.  The results are also stored in
        :attr:`consequences`.

        Parameters
        ----------
        theory:
            Fleet theory dict.  Expected structure::

                {
                    "theory_id": str,
                    "rules": [
                        {
                            "kind": str,
                            "description": str,
                            "components": list[str],
                            "mandatory": bool,
                            "priority": int,
                            "trust_tier": int,
                        },
                        ...
                    ],
                }

        Returns
        -------
        list[FleetImplementationConsequence]
        """
        rules = theory.get("rules", [])
        theory_id = theory.get("theory_id", self.fleet_theory_id)
        derived: List[FleetImplementationConsequence] = []
        for idx, rule in enumerate(rules):
            c = _consequence_from_rule(rule, theory_id, idx)
            derived.append(c)

        # Sort by descending priority so mandatory high-priority items come first
        derived.sort(key=lambda c: (-c.priority, not c.is_mandatory))

        self.consequences.extend(derived)
        self.derived_count += len(derived)
        logger.info(
            "ConsequenceManager %s derived %d consequences from theory %s.",
            self.manager_id[:8],
            len(derived),
            theory_id,
        )
        return derived

    def track(self, consequence: FleetImplementationConsequence) -> None:
        """Add *consequence* to the internal registry.

        Idempotent: if the same :attr:`~FleetImplementationConsequence.consequence_id`
        is already tracked, the call is a no-op.

        Parameters
        ----------
        consequence:
            The consequence to track.
        """
        existing_ids = {c.consequence_id for c in self.consequences}
        if consequence.consequence_id not in existing_ids:
            self.consequences.append(consequence)
            logger.debug("Tracking new consequence %s.", consequence.consequence_id[:8])

    def enforce(self, consequence_id: str, fleet_state: dict) -> bool:
        """Enforce the consequence identified by *consequence_id*.

        Looks up the consequence and checks that the fleet state does not
        violate it.  For mandatory consequences a :class:`JuGeoError` is
        raised on violation; for advisory consequences the violation is
        logged and ``False`` is returned.

        Parameters
        ----------
        consequence_id:
            ID of the consequence to enforce.
        fleet_state:
            The live fleet state to check against.

        Returns
        -------
        bool
            ``True`` if the constraint is satisfied, ``False`` otherwise.

        Raises
        ------
        JuGeoError
            If the consequence is mandatory and is violated.
        """
        target = next(
            (c for c in self.consequences if c.consequence_id == consequence_id),
            None,
        )
        if target is None:
            logger.warning("Consequence %s not found in manager.", consequence_id[:8])
            return True  # unknown => assume satisfied

        # Structural heuristic: check that required components are present
        fleet_components = set(fleet_state.get("components", []))
        required = set(target.affected_components)
        missing = required - fleet_components

        satisfied = len(missing) == 0

        log_entry = {
            "consequence_id": consequence_id,
            "fleet_theory_id": target.fleet_theory_id,
            "kind": target.requirement_kind,
            "satisfied": satisfied,
            "missing_components": sorted(missing),
            "timestamp": _now_iso(),
        }
        self.enforcement_log.append(log_entry)

        if not satisfied:
            detail = f"Missing components: {sorted(missing)}"
            if target.is_mandatory:
                raise_with_scope(
                    "FLEET_CONSEQUENCE_VIOLATION",
                    message=f"Mandatory consequence {consequence_id[:8]} violated. {detail}",
                    provenance=target.to_judgment(),
                )
            else:
                logger.warning(
                    "Advisory consequence %s violated: %s", consequence_id[:8], detail
                )

        return satisfied

    def get_status(self) -> dict:
        """Return a status snapshot of the manager.

        Returns
        -------
        dict
            Keys: ``"manager_id"``, ``"fleet_theory_id"``,
            ``"consequence_count"``, ``"derived_count"``,
            ``"enforcement_count"``, ``"last_enforcement"``.
        """
        last = self.enforcement_log[-1] if self.enforcement_log else None
        return {
            "manager_id": self.manager_id,
            "fleet_theory_id": self.fleet_theory_id,
            "consequence_count": len(self.consequences),
            "derived_count": self.derived_count,
            "enforcement_count": len(self.enforcement_log),
            "last_enforcement": last,
        }


# ---------------------------------------------------------------------------
# Additional private helpers needed by public functions
# ---------------------------------------------------------------------------

def _fleet_state_to_config(fleet_state: dict) -> dict:
    """Convert a live fleet state dict to the config format expected by
    :meth:`FleetPolicy.validate`.

    Parameters
    ----------
    fleet_state:
        Live fleet state dict.

    Returns
    -------
    dict
        A configuration-style dict with keys ``"size"``, ``"diversity_score"``,
        ``"timeout_s"``, and ``"min_trust_tier"``.
    """
    members = fleet_state.get("members", [])
    return {
        "size": len(members),
        "diversity_score": _diversity_score(fleet_state),
        "timeout_s": fleet_state.get("last_search_timeout_s", 0.0),
        "min_trust_tier": _min_trust_in_fleet(fleet_state).value,
    }


def _enrich_constraints(
    constraints: List[FleetConstraint],
    policy: FleetPolicy,
) -> List[FleetConstraint]:
    """Return *constraints* enriched with any policy-implied constraints not
    already present.

    Deduplication is by :attr:`~FleetConstraint.constraint_kind`: if a
    constraint of the same kind already exists it is kept as-is.

    Parameters
    ----------
    constraints:
        Caller-supplied constraints.
    policy:
        Policy from which to derive additional constraints.

    Returns
    -------
    list[FleetConstraint]
    """
    existing_kinds = {c.constraint_kind for c in constraints}
    implied = _build_default_constraints_from_policy(policy)
    return constraints + [c for c in implied if c.constraint_kind not in existing_kinds]


# ---------------------------------------------------------------------------
# Module-level public functions
# ---------------------------------------------------------------------------

def derive_fleet_consequences(
    fleet_theory: dict,
) -> List[FleetImplementationConsequence]:
    """Analyse *fleet_theory* and return its implementation consequences.

    This is the primary entry point for consequence derivation.  It creates a
    temporary :class:`ConsequenceManager` internally and returns the derived
    consequences sorted by descending priority.

    Parameters
    ----------
    fleet_theory:
        Fleet theory dict in the format expected by
        :meth:`ConsequenceManager.derive`.

    Returns
    -------
    list[FleetImplementationConsequence]
        Ordered list of derived consequences (highest priority first).

    Examples
    --------
    >>> theory = {
    ...     "theory_id": "t_demo",
    ...     "rules": [
    ...         {"kind": "structural", "description": "Need >= 2 members.",
    ...          "components": ["core"], "mandatory": True, "priority": 9},
    ...     ],
    ... }
    >>> consequences = derive_fleet_consequences(theory)
    >>> len(consequences)
    1
    """
    theory_id = fleet_theory.get("theory_id", _stable_id("anon"))
    manager = ConsequenceManager(fleet_theory_id=theory_id)
    return manager.derive(fleet_theory)


def audit_fleet(
    fleet_id: str,
    fleet_state: dict,
    policy: FleetPolicy,
    constraints: List[FleetConstraint],
) -> dict:
    """Produce a :class:`FleetAudit` report and return its export dict.

    Convenience wrapper that constructs a :class:`FleetAudit`, enriches its
    constraint list with policy-implied constraints, runs the audit, and
    returns the serialisable result.

    Parameters
    ----------
    fleet_id:
        Identifier of the fleet being audited.
    fleet_state:
        Current fleet state dict.
    policy:
        The governing :class:`FleetPolicy`.
    constraints:
        Caller-supplied :class:`FleetConstraint` list.

    Returns
    -------
    dict
        Serialisable audit export dict (see :meth:`FleetAudit.export`).
    """
    enriched = _enrich_constraints(constraints, policy)
    audit = FleetAudit(fleet_id=fleet_id, policy=policy, constraints=enriched)
    audit.run(fleet_state)
    return audit.export()


def enforce_fleet_policy(
    policy: FleetPolicy,
    fleet_config: dict,
) -> dict:
    """Apply *policy* to *fleet_config* and return an enforcement report.

    If the configuration violates the policy, the report contains the
    violations and ``"compliant": False``.  If the number of violations
    exceeds :data:`MAX_POLICY_VIOLATIONS`, a :class:`JuGeoError` is raised.

    Parameters
    ----------
    policy:
        The :class:`FleetPolicy` to enforce.
    fleet_config:
        Proposed fleet configuration dict.

    Returns
    -------
    dict
        Keys: ``"policy_id"``, ``"compliant"``, ``"violations"``,
        ``"violation_count"``.

    Raises
    ------
    JuGeoError
        If the number of violations exceeds :data:`MAX_POLICY_VIOLATIONS`.
    """
    errors = policy.validate(fleet_config)
    compliant = len(errors) == 0
    if len(errors) > MAX_POLICY_VIOLATIONS:
        raise_with_scope(
            "FLEET_POLICY_EXCEEDED",
            message=(
                f"Fleet config violates policy {policy.policy_id} on "
                f"{len(errors)} points (limit={MAX_POLICY_VIOLATIONS})."
            ),
            provenance=policy.to_judgment(),
        )

    logger.info(
        "enforce_fleet_policy: policy=%s compliant=%s violations=%d",
        policy.policy_id,
        compliant,
        len(errors),
    )
    return {
        "policy_id": policy.policy_id,
        "compliant": compliant,
        "violations": errors,
        "violation_count": len(errors),
    }


def check_fleet_constraint(
    constraint: FleetConstraint,
    fleet_state: dict,
) -> Judgment:
    """Verify that *constraint* holds for *fleet_state* and return a :class:`Judgment`.

    The judgment trust tier is promoted to ``RUNTIME_WITNESSED`` on a pass
    and demoted to ``PROPOSAL`` on a fail.

    Parameters
    ----------
    constraint:
        The :class:`FleetConstraint` to check.
    fleet_state:
        Current fleet state dict.

    Returns
    -------
    Judgment
        A judgment reflecting the evaluation result and its trust tier.
    """
    passed = constraint.evaluate(fleet_state)
    evidence = _collect_evidence_for_constraint(constraint, fleet_state, passed)
    trust = constraint.trust_tier.promote() if passed else constraint.trust_tier.demote()

    formula = (
        f"constraint:{constraint.constraint_id[:8]} "
        f"{'PASSED' if passed else 'FAILED'} on fleet_state"
    )
    obligations: tuple = () if passed else (f"remediate:{constraint.constraint_id}",)

    return Judgment(
        context=f"fleet_state:{fleet_state.get('fleet_id', 'unknown')}",
        formula=formula,
        assumptions=("fleet_state_current",),
        evidence=evidence,
        obligations=obligations,
        burden="fleet_auditor" if not passed else None,
        trust=trust,
        provenance=ProvenanceSource.RUNTIME,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("FleetImplementationConsequences - smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------ #
    # 1. Build a fleet policy                                              #
    # ------------------------------------------------------------------ #
    policy = FleetPolicy(
        policy_id="pol_smoke_01",
        max_fleet_size=6,
        min_diversity_score=0.4,
        search_timeout_s=20.0,
        require_quorum=True,
        min_trust_tier=TrustTier.REVIEWED,
        prune_below_score=0.15,
    )
    print("\n[1] Policy created:")
    print(f"    id={policy.policy_id}, max_size={policy.max_fleet_size}, "
          f"min_diversity={policy.min_diversity_score}, "
          f"min_trust={policy.min_trust_tier.name}")

    # ------------------------------------------------------------------ #
    # 2. Create constraints                                                #
    # ------------------------------------------------------------------ #
    c_size = FleetConstraint(
        constraint_id=_stable_id("smoke", "size"),
        constraint_kind="size",
        expression="len(members) <= 6",
        bound=6.0,
        is_upper_bound=True,
        trust_tier=TrustTier.VERIFIED,
    )
    c_diversity = FleetConstraint(
        constraint_id=_stable_id("smoke", "diversity"),
        constraint_kind="diversity",
        expression="diversity_score >= 0.4",
        bound=0.4,
        is_upper_bound=False,
        trust_tier=TrustTier.VERIFIED,
    )
    c_quorum = FleetConstraint(
        constraint_id=_stable_id("smoke", "quorum"),
        constraint_kind="quorum_ratio",
        expression="quorum_ratio >= 0.51",
        bound=0.51,
        is_upper_bound=False,
        trust_tier=TrustTier.RUNTIME_WITNESSED,
    )
    print("\n[2] Constraints created:")
    for c in (c_size, c_diversity, c_quorum):
        print(f"    - {c.describe()}")

    # ------------------------------------------------------------------ #
    # 3. Derive consequences from a theory dict                            #
    # ------------------------------------------------------------------ #
    theory = {
        "theory_id": "theory_smoke_fleet",
        "rules": [
            {
                "kind": "structural",
                "description": "Fleet must include at least one sentinel inhabitant.",
                "components": ["sentinel"],
                "mandatory": True,
                "priority": 9,
                "trust_tier": TrustTier.VERIFIED.value,
            },
            {
                "kind": "behavioral",
                "description": "All fleet members must respond within the latency budget.",
                "components": ["core", "sentinel", "relay"],
                "mandatory": True,
                "priority": 8,
                "trust_tier": TrustTier.RUNTIME_WITNESSED.value,
            },
            {
                "kind": "relational",
                "description": "Leader count must be exactly one at any point in time.",
                "components": ["core"],
                "mandatory": False,
                "priority": 6,
                "trust_tier": TrustTier.REVIEWED.value,
            },
            {
                "kind": "diversity",
                "description": "Fleet variant distribution must not be degenerate.",
                "components": [],
                "mandatory": True,
                "priority": 7,
                "trust_tier": TrustTier.VERIFIED.value,
            },
        ],
    }

    consequences = derive_fleet_consequences(theory)
    print(f"\n[3] Derived {len(consequences)} consequence(s) from theory '{theory['theory_id']}':")
    for con in consequences:
        print()
        print(con.describe_requirement())

    # ------------------------------------------------------------------ #
    # 4. Build a mock fleet state and run an audit                         #
    # ------------------------------------------------------------------ #
    mock_fleet_state: dict = {
        "fleet_id": "fleet_smoke_001",
        "members": [
            {"id": "m1", "variant": "alpha", "score": 0.8, "trust_tier": 3, "is_leader": True},
            {"id": "m2", "variant": "beta",  "score": 0.7, "trust_tier": 3, "is_leader": False},
            {"id": "m3", "variant": "gamma", "score": 0.6, "trust_tier": 2, "is_leader": False},
            {"id": "m4", "variant": "alpha", "score": 0.5, "trust_tier": 2, "is_leader": False},
        ],
        "quorum_members": 4,
        "total_members": 4,
        "p99_latency_ms": 120.0,
        "throughput_rps": 450.0,
        "components": ["core", "sentinel", "relay"],
        "last_search_timeout_s": 5.0,
    }

    audit_result = audit_fleet(
        fleet_id=mock_fleet_state["fleet_id"],
        fleet_state=mock_fleet_state,
        policy=policy,
        constraints=[c_size, c_diversity, c_quorum],
    )
    print(f"\n[4] Audit result:")
    for k, v in audit_result.items():
        if k not in ("passed", "violations"):
            print(f"    {k}: {v}")
    print(f"    passed checks : {audit_result['passed']}")
    print(f"    violations    : {audit_result['violations']}")

    # ------------------------------------------------------------------ #
    # 5. Enforce policy on a proposed config                               #
    # ------------------------------------------------------------------ #
    proposed_config = {
        "size": 5,
        "diversity_score": 0.55,
        "timeout_s": 15.0,
        "min_trust_tier": TrustTier.REVIEWED.value,
    }
    enforcement = enforce_fleet_policy(policy, proposed_config)
    print(f"\n[5] Policy enforcement on proposed config:")
    print(f"    compliant={enforcement['compliant']}, violations={enforcement['violation_count']}")

    # ------------------------------------------------------------------ #
    # 6. Check individual constraints and inspect judgments                #
    # ------------------------------------------------------------------ #
    print("\n[6] Individual constraint judgments:")
    for c in (c_size, c_diversity, c_quorum):
        j = check_fleet_constraint(c, mock_fleet_state)
        print(f"    [{c.constraint_kind}] formula='{j.formula}' trust={j.trust.name}")

    # ------------------------------------------------------------------ #
    # 7. ConsequenceManager enforcement                                    #
    # ------------------------------------------------------------------ #
    manager = ConsequenceManager(fleet_theory_id=theory["theory_id"])
    manager.derive(theory)
    print(f"\n[7] ConsequenceManager status: {manager.get_status()}")

    # Enforce the sentinel structural consequence
    sentinel_con = next(
        (c for c in manager.consequences if "sentinel" in c.affected_components), None
    )
    if sentinel_con:
        ok = manager.enforce(sentinel_con.consequence_id, mock_fleet_state)
        print(f"\n    Enforce sentinel consequence -> satisfied={ok}")
        j = sentinel_con.to_judgment()
        print(f"    Judgment: trust={j.trust.name} formula='{j.formula}'")

    # ------------------------------------------------------------------ #
    # 8. CechObstruction demo                                              #
    # ------------------------------------------------------------------ #
    obstruction = CechObstruction(
        cover_id="cover_quorum_diversity",
        cocycle=frozenset({("quorum_ratio", "diversity"), ("diversity", "size")}),
        cohomology_class="H1_quorum_diversity",
        description=(
            "Quorum and diversity requirements cannot simultaneously be "
            "satisfied for a fleet of size 3 with the given variant distribution."
        ),
    )
    print(f"\n[8] CechObstruction trivial={obstruction.is_trivial()} "
          f"class='{obstruction.cohomology_class}'")

    # ------------------------------------------------------------------ #
    # 9. TrustTier lattice operations                                      #
    # ------------------------------------------------------------------ #
    t1 = TrustTier.REVIEWED
    t2 = TrustTier.RUNTIME_WITNESSED
    print(f"\n[9] TrustTier lattice: "
          f"join({t1.name},{t2.name})={t1.join(t2).name}, "
          f"meet={t1.meet(t2).name}, "
          f"promote({t1.name})={t1.promote().name}, "
          f"demote({t2.name})={t2.demote().name}")

    print("\n" + "=" * 70)
    print("Smoke test complete - all sections executed successfully.")
    print("=" * 70)
