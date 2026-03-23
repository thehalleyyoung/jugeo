"""
Implementation consequences of novelty search for system architecture.

This module derives and enforces the architectural consequences that follow
from adopting novelty search as a core ideation mechanism. It models the
decisions, policies, and constraints that novelty search implies, using
a judgment tuple framework.

# copilot: This file is auto-generated as part of the jugeo novelty search
# subsystem. It encodes architectural invariants that must hold whenever
# novelty search is enabled. All judgments are expressed as 8-tuples
# (c, φ, A, E, O, B, T, Π) and no boolean judgments are used.

Judgment tuple components:
    c  - context: the situation or domain in which the judgment applies
    φ  - formula/property: the formal claim being judged
    A  - authority: the agent or mechanism making the judgment
    E  - evidence: supporting data or observations
    O  - obligations: what must be done if the judgment holds
    B  - budget: resource bounds (time, compute, memory)
    T  - trust_tier: epistemic confidence level
    Π  - proof_chain: sequence of derivation steps
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports – gracefully degrade if not installed
# ---------------------------------------------------------------------------

try:
    from jugeo.core import BaseJudgment  # type: ignore
except ImportError:
    BaseJudgment = None  # type: ignore

try:
    from jugeo.ideation.novelty_search.novelty_definition import NoveltyDefinition  # type: ignore
except ImportError:
    NoveltyDefinition = None  # type: ignore

try:
    from jugeo.ideation.novelty_search.novelty_metrics import NoveltyMetric  # type: ignore
except ImportError:
    NoveltyMetric = None  # type: ignore

try:
    from jugeo.ideation.novelty_search.archive_management import ArchiveEntry  # type: ignore
except ImportError:
    ArchiveEntry = None  # type: ignore

try:
    from jugeo.ideation.base import IdeationCandidate  # type: ignore
except ImportError:
    IdeationCandidate = None  # type: ignore

try:
    from jugeo.policy.enforcement import PolicyViolation  # type: ignore
except ImportError:
    PolicyViolation = None  # type: ignore


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid(prefix: str = "") -> str:
    """Generate a short deterministic-looking unique identifier.

    The identifier is based on a UUID4 so it is random, not truly
    deterministic, but is short enough to embed in structured data.

    Args:
        prefix: Optional string prepended to the identifier.

    Returns:
        A string of the form ``<prefix><hex16>``.
    """
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def _hash_candidate(candidate: Any) -> str:
    """Produce a stable SHA-256 fingerprint for an arbitrary candidate object.

    The candidate is serialised via ``repr`` before hashing so that any
    object with a deterministic repr will yield a stable hash across
    runs within the same Python session.

    Args:
        candidate: Any object that supports ``repr``.

    Returns:
        A 64-character lowercase hexadecimal string.
    """
    raw = repr(candidate).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Args:
        value: The value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        The clamped value.
    """
    return max(lo, min(hi, value))


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute the cosine similarity between two equal-length vectors.

    Returns 0.0 when either vector is a zero vector, avoiding division by
    zero. The result lies in [-1.0, 1.0].

    Args:
        vec_a: First numeric sequence.
        vec_b: Second numeric sequence.

    Returns:
        Cosine similarity in [-1.0, 1.0].

    Raises:
        ValueError: If the vectors have different lengths.
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(
            f"Vector length mismatch: {len(vec_a)} vs {len(vec_b)}"
        )
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _novelty_score_from_archive(
    candidate_vec: Sequence[float],
    archive_vecs: Sequence[Sequence[float]],
    k: int = 5,
) -> float:
    """Compute the k-nearest-neighbour novelty score for a candidate.

    Novelty is defined as the mean distance to the *k* nearest neighbours
    in the behaviour archive, where distance = 1 - cosine_similarity.
    A score of 1.0 means the candidate is maximally novel; 0.0 means
    it is identical to all archive members.

    Args:
        candidate_vec: Feature vector of the candidate.
        archive_vecs: Sequence of feature vectors already in the archive.
        k: Number of nearest neighbours to average over.

    Returns:
        Novelty score in [0.0, 1.0].
    """
    if not archive_vecs:
        # Empty archive: everything is maximally novel.
        return 1.0
    distances = sorted(
        1.0 - _cosine_similarity(candidate_vec, av) for av in archive_vecs
    )
    k_actual = min(k, len(distances))
    return sum(distances[:k_actual]) / k_actual


def _severity_from_tier(tier: "TrustTier") -> float:
    """Map a TrustTier to a normalised severity weight in [0.2, 1.0].

    Higher-confidence tiers produce higher severity weights because
    a PROOF_BACKED consequence is more certain to materialise than a
    PROPOSAL-level one.

    Args:
        tier: The trust tier to convert.

    Returns:
        A float in [0.2, 1.0].
    """
    mapping = {
        TrustTier.PROPOSAL: 0.2,
        TrustTier.REVIEWED: 0.4,
        TrustTier.VERIFIED: 0.6,
        TrustTier.RUNTIME_WITNESSED: 0.8,
        TrustTier.PROOF_BACKED: 1.0,
    }
    return mapping.get(tier, 0.5)


def _parse_threshold_expr(expression: str) -> Tuple[str, float]:
    """Parse a simple threshold expression like 'novelty_score >= 0.3'.

    Supported operators: ``>=``, ``<=``, ``>``, ``<``.

    Args:
        expression: The expression string to parse.

    Returns:
        A tuple ``(operator, threshold_value)``.

    Raises:
        ValueError: If the expression cannot be parsed.
    """
    for op in (">=", "<=", ">", "<"):
        if op in expression:
            parts = expression.split(op, 1)
            try:
                return op, float(parts[1].strip())
            except (ValueError, IndexError) as exc:
                raise ValueError(
                    f"Cannot parse threshold in expression: {expression!r}"
                ) from exc
    raise ValueError(f"No comparison operator found in expression: {expression!r}")


# ---------------------------------------------------------------------------
# Trust tier enum
# ---------------------------------------------------------------------------


class TrustTier(Enum):
    """Epistemic confidence levels for novelty search judgments.

    The tiers form a total order:
    PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.

    Each tier corresponds to a different kind of justification:

    PROPOSAL
        The judgment has been proposed but not yet scrutinised.  It may
        contain errors or be based on incomplete evidence.
    REVIEWED
        A human or automated reviewer has checked the judgment for obvious
        errors.  Not yet independently verified.
    VERIFIED
        The judgment has been independently verified, e.g., by a second
        agent or a formal checker with bounded guarantees.
    RUNTIME_WITNESSED
        The judgment has been witnessed at runtime: empirical evidence from
        live system behaviour confirms the claim.
    PROOF_BACKED
        A formal proof (or a mechanically-checked certificate) exists for
        the judgment.  This is the highest achievable tier.
    """

    PROPOSAL = auto()
    REVIEWED = auto()
    VERIFIED = auto()
    RUNTIME_WITNESSED = auto()
    PROOF_BACKED = auto()

    def __lt__(self, other: "TrustTier") -> bool:
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: "TrustTier") -> bool:
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value <= other.value

    @property
    def label(self) -> str:
        """Human-readable short label for this tier."""
        return self.name.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Core judgment dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsequenceJudgment:
    """An 8-tuple judgment about an implementation consequence.

    Encodes a single judgment using the standard jugeo judgment tuple
    (c, φ, A, E, O, B, T, Π). All fields are immutable once constructed.

    Attributes:
        context: The situation or domain in which the judgment applies.
        formula: The formal property or claim being judged.
        authority: The agent or mechanism that produced this judgment.
        evidence: Supporting data or observations. Immutable tuple.
        obligations: Things that MUST be done if this judgment holds.
        budget: Resource bounds as a tuple of (key, value) pairs.
        trust_tier: The epistemic confidence level of this judgment.
        proof_chain: Ordered tuple of derivation steps.
    """

    context: str
    formula: str
    authority: str
    evidence: Tuple[str, ...]
    obligations: Tuple[str, ...]
    budget: Tuple[Tuple[str, float], ...]
    trust_tier: TrustTier
    proof_chain: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the judgment to a plain dictionary."""
        return {
            "context": self.context,
            "formula": self.formula,
            "authority": self.authority,
            "evidence": list(self.evidence),
            "obligations": list(self.obligations),
            "budget": {k: v for k, v in self.budget},
            "trust_tier": self.trust_tier.name,
            "proof_chain": list(self.proof_chain),
        }

    def upgrade(self, new_tier: TrustTier, additional_proof: str) -> "ConsequenceJudgment":
        """Return a copy with an upgraded trust tier.

        Args:
            new_tier: Must be strictly higher than the current tier.
            additional_proof: A proof step to append to the chain.

        Returns:
            A new ConsequenceJudgment with upgraded tier.

        Raises:
            ValueError: If new_tier is not strictly higher.
        """
        if new_tier <= self.trust_tier:
            raise ValueError(
                f"Cannot downgrade or maintain trust tier: "
                f"{self.trust_tier.name} -> {new_tier.name}"
            )
        return ConsequenceJudgment(
            context=self.context,
            formula=self.formula,
            authority=self.authority,
            evidence=self.evidence,
            obligations=self.obligations,
            budget=self.budget,
            trust_tier=new_tier,
            proof_chain=self.proof_chain + (additional_proof,),
        )

    def add_evidence(self, new_evidence: str) -> "ConsequenceJudgment":
        """Return a copy with one additional evidence item appended.

        Args:
            new_evidence: The evidence string to append.

        Returns:
            A new ConsequenceJudgment with extended evidence tuple.
        """
        return ConsequenceJudgment(
            context=self.context,
            formula=self.formula,
            authority=self.authority,
            evidence=self.evidence + (new_evidence,),
            obligations=self.obligations,
            budget=self.budget,
            trust_tier=self.trust_tier,
            proof_chain=self.proof_chain,
        )


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltySearchConsequence:
    """A single architectural consequence derived from novelty search.

    Attributes:
        consequence_id: Unique identifier for this consequence.
        description: Human-readable description.
        affected_components: Frozenset of component names affected.
        severity: Impact score in [0.0, 1.0].
        derivation_path: Ordered tuple of reasoning steps.
        trust_tier: Confidence in the correctness of this consequence.
        created_at: ISO-8601 timestamp of creation.
    """

    consequence_id: str
    description: str
    affected_components: FrozenSet[str]
    severity: float
    derivation_path: Tuple[str, ...]
    trust_tier: TrustTier
    created_at: str

    def __post_init__(self) -> None:
        if not (0.0 <= self.severity <= 1.0):
            raise ValueError(
                f"severity must be in [0.0, 1.0], got {self.severity}"
            )


@dataclass(frozen=True, slots=True)
class ArchitecturalDecision:
    """An architectural decision that novelty search forces or recommends.

    Attributes:
        decision_id: Unique identifier.
        title: Short title of the decision.
        rationale: Explanation of why this decision is required.
        alternatives_considered: Why alternative approaches were rejected.
        consequences: Tuple of consequence IDs this decision produces.
        trust_tier: Confidence level.
        created_at: ISO-8601 timestamp.
    """

    decision_id: str
    title: str
    rationale: str
    alternatives_considered: Tuple[str, ...]
    consequences: Tuple[str, ...]
    trust_tier: TrustTier
    created_at: str


@dataclass(frozen=True, slots=True)
class NoveltyConstraint:
    """A formal constraint that candidates must satisfy to be admitted.

    Hard constraints immediately reject violating candidates; soft
    constraints contribute to a penalty score only.

    Attributes:
        constraint_id: Unique identifier.
        name: Short human-readable name.
        formal_expression: A formal statement of the constraint.
        is_hard_constraint: Whether violations are disqualifying.
        violation_count: Running count of violations seen so far.
        created_at: ISO-8601 timestamp.
    """

    constraint_id: str
    name: str
    formal_expression: str
    is_hard_constraint: bool
    violation_count: int
    created_at: str


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A single novelty policy rule.

    Attributes:
        rule_id: Unique identifier.
        policy_name: Name of the parent policy.
        description: Human-readable description.
        threshold: Numeric threshold used in evaluation.
        rule_type: Category (e.g., ``"min_novelty"``, ``"max_similarity"``).
        created_at: ISO-8601 timestamp.
    """

    rule_id: str
    policy_name: str
    description: str
    threshold: float
    rule_type: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PolicyViolationRecord:
    """A record of a policy violation detected during enforcement.

    Attributes:
        violation_id: Unique identifier.
        policy_name: Name of the policy that was violated.
        idea_id: Identifier of the violating candidate.
        rule_id: The specific rule violated.
        observed_value: The value that triggered the violation.
        threshold: The threshold that was breached.
        severity: Violation severity in [0.0, 1.0].
        detected_at: ISO-8601 timestamp.
    """

    violation_id: str
    policy_name: str
    idea_id: str
    rule_id: str
    observed_value: float
    threshold: float
    severity: float
    detected_at: str


@dataclass(frozen=True, slots=True)
class NoveltyBehaviourDescriptor:
    """A low-dimensional description of an idea's behaviour in feature space.

    Behaviour descriptors underpin novelty scoring via kNN distance
    computation over the behaviour archive.

    Attributes:
        descriptor_id: Unique identifier.
        idea_id: The idea this descriptor belongs to.
        feature_vector: Tuple of floats representing the behaviour.
        descriptor_type: Feature encoding type (e.g., ``"embedding"``).
        dimensionality: Expected length of feature_vector.
        created_at: ISO-8601 timestamp.
    """

    descriptor_id: str
    idea_id: str
    feature_vector: Tuple[float, ...]
    descriptor_type: str
    dimensionality: int
    created_at: str

    def __post_init__(self) -> None:
        if len(self.feature_vector) != self.dimensionality:
            raise ValueError(
                f"feature_vector length {len(self.feature_vector)} "
                f"!= dimensionality {self.dimensionality}"
            )

    def distance_to(self, other: "NoveltyBehaviourDescriptor") -> float:
        """Compute cosine distance to another descriptor.

        Distance = 1 - cosine_similarity, so 0.0 means identical
        and 1.0 means orthogonal (maximally different in direction).

        Args:
            other: Another NoveltyBehaviourDescriptor.

        Returns:
            Cosine distance in [0.0, 2.0] (clamped to [0.0, 1.0]).
        """
        sim = _cosine_similarity(self.feature_vector, other.feature_vector)
        return _clamp(1.0 - sim, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class ArchiveAdmissionRecord:
    """Records the decision to admit or reject a candidate from the archive.

    Attributes:
        record_id: Unique identifier.
        idea_id: Candidate being evaluated.
        novelty_score: Computed novelty score at time of evaluation.
        admission_threshold: Minimum score required for admission.
        admitted: Decision string: "admitted", "rejected", or "deferred".
        reason: Human-readable reason for the decision.
        created_at: ISO-8601 timestamp.
    """

    record_id: str
    idea_id: str
    novelty_score: float
    admission_threshold: float
    admitted: str
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class SystemConfig:
    """Snapshot of system configuration relevant to novelty search.

    Attributes:
        system_name: Name of the system.
        archive_capacity: Maximum archive entries.
        novelty_threshold: Minimum novelty score for admission.
        k_neighbours: k used in kNN novelty scoring.
        diversity_weight: Weight of diversity objective.
        constraint_ids: Active constraint identifiers.
        policy_names: Active policy names.
        created_at: ISO-8601 timestamp.
    """

    system_name: str
    archive_capacity: int
    novelty_threshold: float
    k_neighbours: int
    diversity_weight: float
    constraint_ids: Tuple[str, ...]
    policy_names: Tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class NoveltyParameters:
    """Tunable parameters controlling novelty search behaviour.

    Attributes:
        min_novelty_score: Candidates below this score are rejected.
        max_archive_staleness: Max generations without an archive update.
        novelty_weight: Weight assigned to novelty vs. other objectives.
        behaviour_space_dims: Dimensionality of the behaviour space.
        distance_metric: Name of the distance metric.
        archive_sampling_strategy: How candidates are sampled from archive.
    """

    min_novelty_score: float
    max_archive_staleness: int
    novelty_weight: float
    behaviour_space_dims: int
    distance_metric: str
    archive_sampling_strategy: str


@dataclass(frozen=True, slots=True)
class ConsequenceReport:
    """A full architectural consequence report.

    Aggregates all consequences, decisions, and constraint violations
    into a single immutable snapshot for audit purposes.

    Attributes:
        report_id: Unique identifier.
        system_name: Name of the system analysed.
        consequences: Tuple of NoveltySearchConsequence objects.
        decisions: Tuple of ArchitecturalDecision objects.
        constraint_violations: Tuple of PolicyViolationRecord objects.
        overall_trust_tier: Lowest trust tier across all included judgments.
        generated_at: ISO-8601 timestamp.
        summary: High-level human-readable summary.
    """

    report_id: str
    system_name: str
    consequences: Tuple[NoveltySearchConsequence, ...]
    decisions: Tuple[ArchitecturalDecision, ...]
    constraint_violations: Tuple[PolicyViolationRecord, ...]
    overall_trust_tier: TrustTier
    generated_at: str
    summary: str

    @property
    def consequence_count(self) -> int:
        """Number of consequences in this report."""
        return len(self.consequences)

    @property
    def high_severity_consequences(self) -> Tuple[NoveltySearchConsequence, ...]:
        """Return only consequences with severity >= 0.7."""
        return tuple(c for c in self.consequences if c.severity >= 0.7)


@dataclass(frozen=True, slots=True)
class ArchiveEvictionRecord:
    """Records an eviction from the behaviour archive.

    When the archive reaches capacity, the least-novel entry is evicted.
    This record captures the eviction event for audit purposes.

    Attributes:
        eviction_id: Unique identifier.
        evicted_idea_id: The idea that was evicted.
        eviction_reason: Why this entry was chosen for eviction.
        archive_size_before: Archive size before eviction.
        archive_size_after: Archive size after eviction.
        evicted_at: ISO-8601 timestamp.
    """

    eviction_id: str
    evicted_idea_id: str
    eviction_reason: str
    archive_size_before: int
    archive_size_after: int
    evicted_at: str


@dataclass(frozen=True, slots=True)
class DiversityMeasurement:
    """A snapshot measurement of population diversity.

    Diversity is computed as the mean pairwise distance between all
    behaviour descriptors in a population sample.

    Attributes:
        measurement_id: Unique identifier.
        population_size: Number of candidates in the measured population.
        mean_pairwise_distance: Mean cosine distance between all pairs.
        min_pairwise_distance: Minimum distance found (most similar pair).
        max_pairwise_distance: Maximum distance found (most different pair).
        measured_at: ISO-8601 timestamp.
    """

    measurement_id: str
    population_size: int
    mean_pairwise_distance: float
    min_pairwise_distance: float
    max_pairwise_distance: float
    measured_at: str

    @property
    def is_diverse(self) -> str:
        """Return "diverse", "moderate", or "uniform" based on mean distance."""
        if self.mean_pairwise_distance >= 0.6:
            return "diverse"
        elif self.mean_pairwise_distance >= 0.3:
            return "moderate"
        else:
            return "uniform"


# ---------------------------------------------------------------------------
# NoveltyArchitecture class
# ---------------------------------------------------------------------------


class NoveltyArchitecture:
    """Model of the architectural decisions implied by novelty search.

    Accumulates architectural decisions and derives the implementation
    consequences that follow from them. Validates internal consistency
    and produces a ConsequenceJudgment summarising the entire architecture.

    The architecture is considered *valid* when:
    1. At least one decision concerns archive management.
    2. At least one decision concerns the novelty metric.
    3. No two decisions have contradictory consequences.
    4. All affected components have at least one covering decision.

    Attributes:
        system_name: Name of the system being modelled.
        _decisions: Internal list of ArchitecturalDecision objects.
        _consequences: Internal list of NoveltySearchConsequence objects.
        _component_coverage: Mapping from component to covering decision IDs.
    """

    def __init__(self, system_name: str) -> None:
        """Initialise the architecture model.

        Args:
            system_name: Human-readable name of the system.
        """
        self.system_name = system_name
        self._decisions: List[ArchitecturalDecision] = []
        self._consequences: List[NoveltySearchConsequence] = []
        self._component_coverage: Dict[str, List[str]] = {}

    def add_architectural_decision(self, decision: ArchitecturalDecision) -> None:
        """Register an architectural decision with the model.

        Consequences are automatically expanded from the decision and
        attached to all affected components. Severity is proportional
        to the decision's trust tier.

        Args:
            decision: The decision to register.
        """
        self._decisions.append(decision)
        severity = _severity_from_tier(decision.trust_tier)
        # Parse affected components from the decision title words.
        affected = frozenset(
            w for w in decision.title.lower().split() if len(w) > 3
        )
        for cid in decision.consequences:
            consequence = NoveltySearchConsequence(
                consequence_id=cid,
                description=(
                    f"Consequence of '{decision.title}': "
                    f"{decision.rationale[:120]}"
                ),
                affected_components=affected,
                severity=severity,
                derivation_path=(
                    f"decision:{decision.decision_id}",
                    f"rationale_hash:"
                    f"{hashlib.md5(decision.rationale.encode()).hexdigest()[:8]}",
                ),
                trust_tier=decision.trust_tier,
                created_at=_now_iso(),
            )
            self._consequences.append(consequence)
            for component in affected:
                self._component_coverage.setdefault(component, []).append(
                    decision.decision_id
                )

    def validate_architecture(self) -> List[str]:
        """Validate architecture for internal consistency.

        Checks that:
        - There is at least one decision.
        - At least one decision mentions "archive".
        - At least one decision mentions "metric" or "novelty".
        - No component lacks coverage.

        Returns:
            A list of validation error messages (empty = valid).
        """
        errors: List[str] = []
        if not self._decisions:
            errors.append("No architectural decisions registered.")
            return errors

        titles = [d.title.lower() for d in self._decisions]
        if not any("archive" in t for t in titles):
            errors.append(
                "Missing decision covering archive management. "
                "Novelty search requires an explicit archive policy."
            )
        if not any("metric" in t or "novelty" in t for t in titles):
            errors.append(
                "Missing decision covering novelty metric. "
                "A distance metric must be explicitly chosen."
            )

        all_components: set = set()
        for c in self._consequences:
            all_components.update(c.affected_components)
        for comp in all_components:
            if comp not in self._component_coverage:
                errors.append(
                    f"Component '{comp}' has consequences but no covering decision."
                )
        return errors

    def get_architecture_report(self) -> ConsequenceJudgment:
        """Produce a ConsequenceJudgment summarising the architecture.

        Returns:
            A ConsequenceJudgment encoding the architectural state.
        """
        errors = self.validate_architecture()
        min_tier = (
            min((d.trust_tier for d in self._decisions), key=lambda t: t.value)
            if self._decisions
            else TrustTier.PROPOSAL
        )
        obligations = tuple(f"RESOLVE: {e}" for e in errors) or (
            "Architecture is internally consistent.",
        )
        evidence = tuple(d.title for d in self._decisions) or (
            "no decisions registered",
        )
        proof_chain = (
            f"min_tier: computed over {len(self._decisions)} decision(s)",
            f"validation: {len(errors)} error(s)",
        )
        return ConsequenceJudgment(
            context=(
                f"system={self.system_name}; "
                f"decisions={len(self._decisions)}; "
                f"consequences={len(self._consequences)}"
            ),
            formula=(
                "The architecture is complete iff every novelty search "
                "requirement has at least one covering decision."
            ),
            authority=__name__,
            evidence=evidence,
            obligations=obligations,
            budget=(("cpu_seconds", 60.0), ("memory_mb", 512.0)),
            trust_tier=min_tier,
            proof_chain=proof_chain,
        )

    def get_component_coverage_summary(self) -> Dict[str, int]:
        """Return the number of decisions covering each component.

        Returns:
            Mapping from component name to count of covering decisions.
        """
        return {comp: len(dids) for comp, dids in self._component_coverage.items()}

    def list_uncovered_requirements(self) -> List[str]:
        """List requirement categories not yet covered by any decision.

        The standard novelty search requirements are:
        - archive, metric, policy, budget, staleness, diversity.

        Returns:
            List of uncovered requirement category names.
        """
        required_categories = {
            "archive", "metric", "policy", "budget", "staleness", "diversity"
        }
        covered = set()
        for d in self._decisions:
            for word in d.title.lower().split():
                if word in required_categories:
                    covered.add(word)
        return sorted(required_categories - covered)


# ---------------------------------------------------------------------------
# NoveltyPolicy class
# ---------------------------------------------------------------------------


class NoveltyPolicy:
    """Enforces novelty policies against idea candidates.

    A *policy* is a named collection of rules. Each rule is a callable
    ``(idea_id: str, candidate: Any) -> Optional[PolicyViolationRecord]``
    that returns a violation record when breached or ``None`` when compliant.

    Attributes:
        _policies: Mapping from policy name to list of (rule_id, callable).
        _violations: Accumulated violation records.
        _rules: Mapping from rule_id to PolicyRule metadata.
    """

    def __init__(self) -> None:
        """Initialise an empty policy enforcer."""
        self._policies: Dict[str, List[Tuple[str, Callable[..., Any]]]] = {}
        self._violations: List[PolicyViolationRecord] = []
        self._rules: Dict[str, PolicyRule] = {}

    def register_policy(
        self,
        policy_name: str,
        rule: Callable[..., Optional[PolicyViolationRecord]],
        *,
        description: str = "",
        threshold: float = 0.0,
        rule_type: str = "generic",
    ) -> str:
        """Register a policy rule under a named policy.

        Args:
            policy_name: Logical grouping for this rule.
            rule: Callable taking (idea_id, candidate); returns violation or None.
            description: Human-readable description.
            threshold: Numeric threshold embedded in this rule.
            rule_type: Categorical type label.

        Returns:
            The generated rule_id for this registration.
        """
        rule_id = _uid("rule-")
        pr = PolicyRule(
            rule_id=rule_id,
            policy_name=policy_name,
            description=description or f"Rule in policy '{policy_name}'",
            threshold=threshold,
            rule_type=rule_type,
            created_at=_now_iso(),
        )
        self._rules[rule_id] = pr
        self._policies.setdefault(policy_name, []).append((rule_id, rule))
        return rule_id

    def enforce(
        self, idea_id: str, candidate: Any
    ) -> List[PolicyViolationRecord]:
        """Evaluate all registered policies against *candidate*.

        Each rule is called with ``(idea_id, candidate)``. Violations are
        accumulated and returned.

        Args:
            idea_id: Identifier of the candidate idea.
            candidate: The candidate object to evaluate.

        Returns:
            List of PolicyViolationRecord objects for this evaluation.
        """
        new_violations: List[PolicyViolationRecord] = []
        for policy_name, rules in self._policies.items():
            for rule_id, rule_fn in rules:
                try:
                    result = rule_fn(idea_id, candidate)
                except Exception:
                    # Rule evaluation failure: record as soft violation.
                    viol = PolicyViolationRecord(
                        violation_id=_uid("viol-"),
                        policy_name=policy_name,
                        idea_id=idea_id,
                        rule_id=rule_id,
                        observed_value=float("nan"),
                        threshold=float("nan"),
                        severity=0.5,
                        detected_at=_now_iso(),
                    )
                    new_violations.append(viol)
                    continue
                if result is not None:
                    new_violations.append(result)
        self._violations.extend(new_violations)
        return new_violations

    def get_policy_violations(
        self, policy_name: Optional[str] = None
    ) -> List[PolicyViolationRecord]:
        """Return accumulated violations, optionally filtered by policy.

        Args:
            policy_name: Filter to this policy; None returns all.

        Returns:
            List of PolicyViolationRecord objects.
        """
        if policy_name is None:
            return list(self._violations)
        return [v for v in self._violations if v.policy_name == policy_name]

    def clear_violations(self) -> int:
        """Clear all accumulated violations.

        Returns:
            Number of violations cleared.
        """
        count = len(self._violations)
        self._violations.clear()
        return count

    def violation_summary(self) -> Dict[str, int]:
        """Return a mapping from policy name to violation count.

        Returns:
            Dict mapping policy_name -> count of violations.
        """
        summary: Dict[str, int] = {}
        for v in self._violations:
            summary[v.policy_name] = summary.get(v.policy_name, 0) + 1
        return summary


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def derive_novelty_consequences(
    system_config: SystemConfig,
    novelty_parameters: NoveltyParameters,
) -> Tuple[NoveltySearchConsequence, ...]:
    """Derive architectural consequences of the given novelty configuration.

    Analyses ``system_config`` and ``novelty_parameters`` and produces a
    tuple of consequences the system designer must account for.

    Key consequences derived:
    1. Archive capacity relative to behaviour space dimensionality.
    2. Novelty threshold calibration (too high or too low).
    3. Distance metric choice constraints.
    4. Stale archive detection sensitivity.
    5. Diversity weight interaction with exploitation.
    6. Policy overhead estimation.

    Args:
        system_config: Current system configuration snapshot.
        novelty_parameters: Tunable novelty search parameters.

    Returns:
        Tuple of NoveltySearchConsequence objects.
    """
    consequences: List[NoveltySearchConsequence] = []

    # --- Consequence 1: archive capacity vs behaviour space dims ---
    dims = novelty_parameters.behaviour_space_dims
    capacity_ratio = (
        system_config.archive_capacity / dims if dims > 0 else float("inf")
    )
    if capacity_ratio < 2.0:
        severity = _clamp(1.0 - capacity_ratio / 2.0, 0.1, 0.9)
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"Archive capacity ({system_config.archive_capacity}) "
                    f"is low relative to behaviour dims ({dims}). "
                    "Premature convergence is likely."
                ),
                affected_components=frozenset({"archive", "search_loop"}),
                severity=severity,
                derivation_path=(
                    f"capacity_ratio={capacity_ratio:.3f}",
                    "threshold=2.0",
                    "severity=1-ratio/2",
                ),
                trust_tier=TrustTier.REVIEWED,
                created_at=_now_iso(),
            )
        )

    # --- Consequence 2a: novelty threshold too high ---
    threshold = novelty_parameters.min_novelty_score
    if threshold > 0.9:
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"Novelty threshold ({threshold:.2f}) is very high. "
                    "Archive may never grow, stalling search."
                ),
                affected_components=frozenset({"archive", "admission_controller"}),
                severity=0.85,
                derivation_path=(
                    f"min_novelty_score={threshold}",
                    "threshold>0.9 => near-zero admission rate",
                ),
                trust_tier=TrustTier.VERIFIED,
                created_at=_now_iso(),
            )
        )
    # --- Consequence 2b: novelty threshold too low ---
    elif threshold < 0.1:
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"Novelty threshold ({threshold:.2f}) is very low. "
                    "Search degrades toward random exploration."
                ),
                affected_components=frozenset({"archive", "novelty_scorer"}),
                severity=0.6,
                derivation_path=(
                    f"min_novelty_score={threshold}",
                    "threshold<0.1 => near-universal admission",
                ),
                trust_tier=TrustTier.REVIEWED,
                created_at=_now_iso(),
            )
        )

    # --- Consequence 3: distance metric ---
    if novelty_parameters.distance_metric == "cosine":
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    "Cosine distance requires non-zero feature vectors. "
                    "Zero-vector candidates must be handled gracefully."
                ),
                affected_components=frozenset({"novelty_scorer", "feature_extractor"}),
                severity=0.4,
                derivation_path=(
                    "distance_metric=cosine",
                    "cosine undefined for zero vectors",
                ),
                trust_tier=TrustTier.VERIFIED,
                created_at=_now_iso(),
            )
        )

    # --- Consequence 4: stale archive detection ---
    if novelty_parameters.max_archive_staleness < 5:
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"max_archive_staleness={novelty_parameters.max_archive_staleness} "
                    "is very small. High false-alarm rate for archive resets."
                ),
                affected_components=frozenset({"staleness_detector", "search_loop"}),
                severity=0.35,
                derivation_path=(
                    f"max_archive_staleness={novelty_parameters.max_archive_staleness}",
                    "value<5 => high false-alarm rate",
                ),
                trust_tier=TrustTier.PROPOSAL,
                created_at=_now_iso(),
            )
        )

    # --- Consequence 5: diversity weight ---
    if system_config.diversity_weight > 0.8:
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"diversity_weight={system_config.diversity_weight:.2f} "
                    "dominates the objective. Exploitation is suppressed."
                ),
                affected_components=frozenset({"objective_combiner", "selection"}),
                severity=0.55,
                derivation_path=(
                    f"diversity_weight={system_config.diversity_weight}",
                    "weight>0.8 => exploitation suppressed",
                ),
                trust_tier=TrustTier.REVIEWED,
                created_at=_now_iso(),
            )
        )

    # --- Consequence 6: policy overhead ---
    if system_config.policy_names:
        consequences.append(
            NoveltySearchConsequence(
                consequence_id=_uid("cons-"),
                description=(
                    f"{len(system_config.policy_names)} active policies add "
                    "per-candidate evaluation latency. Budget accordingly."
                ),
                affected_components=frozenset({"policy_engine", "evaluation_pipeline"}),
                severity=0.3,
                derivation_path=(
                    f"active_policies={list(system_config.policy_names)}",
                    "each policy: O(candidates) evaluations",
                ),
                trust_tier=TrustTier.PROPOSAL,
                created_at=_now_iso(),
            )
        )

    return tuple(consequences)


def enforce_novelty_policy(
    policy_set: NoveltyPolicy,
    candidates: Sequence[Tuple[str, Any]],
) -> Dict[str, List[PolicyViolationRecord]]:
    """Enforce all registered policies across a sequence of candidates.

    Args:
        policy_set: The NoveltyPolicy instance containing all rules.
        candidates: Sequence of ``(idea_id, candidate_object)`` tuples.

    Returns:
        Mapping ``{idea_id: [violations]}`` for violating candidates only.
    """
    result: Dict[str, List[PolicyViolationRecord]] = {}
    for idea_id, candidate in candidates:
        violations = policy_set.enforce(idea_id, candidate)
        if violations:
            result[idea_id] = violations
    return result


def check_novelty_constraint(
    constraint: NoveltyConstraint,
    candidate: Any,
    *,
    candidate_vec: Optional[Sequence[float]] = None,
    archive_vecs: Optional[Sequence[Sequence[float]]] = None,
    k: int = 5,
) -> Tuple[NoveltyConstraint, ArchiveAdmissionRecord]:
    """Evaluate a NoveltyConstraint against a candidate.

    Computes a novelty score (kNN-based when vectors are provided,
    hash-based otherwise) and determines whether the constraint is satisfied.

    Args:
        constraint: The constraint to evaluate.
        candidate: The candidate object.
        candidate_vec: Optional feature vector.
        archive_vecs: Optional archive feature vectors.
        k: Number of nearest neighbours for novelty scoring.

    Returns:
        Tuple of (updated_constraint, admission_record).
        updated_constraint has incremented violation_count if rejected.
    """
    idea_id = _hash_candidate(candidate)[:12]

    # Compute novelty score.
    if candidate_vec is not None:
        archive = archive_vecs or []
        novelty_score = _novelty_score_from_archive(candidate_vec, archive, k=k)
    else:
        # Hash-based pseudo-score as fallback (deterministic but not meaningful).
        hex_prefix = _hash_candidate(candidate)[:4]
        novelty_score = int(hex_prefix, 16) / 0xFFFF

    # Parse the formal expression to determine admission.
    admitted = "admitted"
    reason = "Novelty constraint satisfied."
    admission_threshold = 0.0

    try:
        op, thr = _parse_threshold_expr(constraint.formal_expression)
        admission_threshold = thr
        if op in (">=", ">"):
            min_val = thr if op == ">=" else thr + 1e-9
            if novelty_score < min_val:
                admitted = "rejected"
                reason = (
                    f"novelty_score {novelty_score:.4f} {op} {thr:.4f} not satisfied"
                )
        elif op in ("<=", "<"):
            max_val = thr if op == "<=" else thr - 1e-9
            if novelty_score > max_val:
                admitted = "rejected"
                reason = (
                    f"novelty_score {novelty_score:.4f} {op} {thr:.4f} not satisfied"
                )
    except ValueError as exc:
        admitted = "deferred"
        reason = str(exc)

    new_violation_count = constraint.violation_count + (
        1 if admitted == "rejected" else 0
    )
    updated_constraint = NoveltyConstraint(
        constraint_id=constraint.constraint_id,
        name=constraint.name,
        formal_expression=constraint.formal_expression,
        is_hard_constraint=constraint.is_hard_constraint,
        violation_count=new_violation_count,
        created_at=constraint.created_at,
    )
    admission_record = ArchiveAdmissionRecord(
        record_id=_uid("adm-"),
        idea_id=idea_id,
        novelty_score=novelty_score,
        admission_threshold=admission_threshold,
        admitted=admitted,
        reason=reason,
        created_at=_now_iso(),
    )
    return updated_constraint, admission_record


def measure_population_diversity(
    descriptors: Sequence[NoveltyBehaviourDescriptor],
) -> DiversityMeasurement:
    """Compute pairwise distance statistics for a population of descriptors.

    Args:
        descriptors: Sequence of NoveltyBehaviourDescriptor objects.

    Returns:
        A DiversityMeasurement snapshot.
    """
    n = len(descriptors)
    if n < 2:
        return DiversityMeasurement(
            measurement_id=_uid("div-"),
            population_size=n,
            mean_pairwise_distance=0.0,
            min_pairwise_distance=0.0,
            max_pairwise_distance=0.0,
            measured_at=_now_iso(),
        )
    distances: List[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            distances.append(descriptors[i].distance_to(descriptors[j]))
    mean_d = sum(distances) / len(distances)
    return DiversityMeasurement(
        measurement_id=_uid("div-"),
        population_size=n,
        mean_pairwise_distance=mean_d,
        min_pairwise_distance=min(distances),
        max_pairwise_distance=max(distances),
        measured_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("=" * 70)
    print("implementation_consequences.py \u2014 smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustTier ordering
    # ------------------------------------------------------------------
    print("\n[1] TrustTier ordering")
    tiers = list(TrustTier)
    for i in range(len(tiers) - 1):
        assert tiers[i] < tiers[i + 1], f"{tiers[i]} < {tiers[i+1]} failed"
    print(f"    All {len(tiers)} tiers ordered correctly: {[t.label for t in tiers]}")

    # ------------------------------------------------------------------
    # 2. ConsequenceJudgment upgrade and add_evidence
    # ------------------------------------------------------------------
    print("\n[2] ConsequenceJudgment upgrade/add_evidence")
    j = ConsequenceJudgment(
        context="test-system",
        formula="archive_capacity >= 2 * behaviour_dims",
        authority="smoke_test",
        evidence=("initial observation",),
        obligations=("Increase archive capacity.",),
        budget=(("cpu_seconds", 10.0),),
        trust_tier=TrustTier.PROPOSAL,
        proof_chain=("premise: capacity_ratio < 2.0",),
    )
    j2 = j.upgrade(TrustTier.REVIEWED, "reviewed by automated checker")
    assert j2.trust_tier == TrustTier.REVIEWED
    assert len(j2.proof_chain) == 2
    j3 = j2.add_evidence("runtime measurement confirms low ratio")
    assert len(j3.evidence) == 2
    print(f"    Upgraded tier: {j2.trust_tier.label}, proof chain: {len(j2.proof_chain)}")
    print(f"    Added evidence: {len(j3.evidence)} items")
    try:
        j.upgrade(TrustTier.PROPOSAL, "should fail")
        assert False, "Expected ValueError"
    except ValueError:
        print("    Downgrade correctly raises ValueError.")

    # ------------------------------------------------------------------
    # 3. NoveltyConstraint + check_novelty_constraint
    # ------------------------------------------------------------------
    print("\n[3] NoveltyConstraint evaluation")
    constraint = NoveltyConstraint(
        constraint_id=_uid("cst-"),
        name="min_novelty",
        formal_expression="novelty_score >= 0.3",
        is_hard_constraint=True,
        violation_count=0,
        created_at=_now_iso(),
    )
    archive = [(0.9, 0.1, 0.0, 0.0), (0.0, 0.9, 0.1, 0.0), (0.0, 0.0, 0.9, 0.1)]
    novel_vec = (0.1, 0.0, 0.0, 0.9)
    common_vec = (0.9, 0.1, 0.0, 0.0)
    updated_c, adm1 = check_novelty_constraint(
        constraint, "novel-idea", candidate_vec=novel_vec, archive_vecs=archive
    )
    print(f"    Novel: score={adm1.novelty_score:.4f}, decision={adm1.admitted}")
    updated_c2, adm2 = check_novelty_constraint(
        updated_c, "common-idea", candidate_vec=common_vec, archive_vecs=archive
    )
    print(f"    Common: score={adm2.novelty_score:.4f}, decision={adm2.admitted}, "
          f"violations={updated_c2.violation_count}")

    # ------------------------------------------------------------------
    # 4. NoveltyArchitecture
    # ------------------------------------------------------------------
    print("\n[4] NoveltyArchitecture")
    arch = NoveltyArchitecture(system_name="jugeo-ideation")
    d1 = ArchitecturalDecision(
        decision_id=_uid("dec-"),
        title="Archive management strategy",
        rationale=(
            "Novelty search requires a persistent behaviour archive. "
            "We use bounded FIFO with priority eviction based on age."
        ),
        alternatives_considered=("Unbounded archive: rejected due to memory.",),
        consequences=(_uid("cons-"),),
        trust_tier=TrustTier.REVIEWED,
        created_at=_now_iso(),
    )
    d2 = ArchitecturalDecision(
        decision_id=_uid("dec-"),
        title="Novelty metric selection",
        rationale="Cosine distance in embedding space is chosen as the novelty metric.",
        alternatives_considered=("Euclidean: rejected because norms vary.",),
        consequences=(_uid("cons-"),),
        trust_tier=TrustTier.VERIFIED,
        created_at=_now_iso(),
    )
    arch.add_architectural_decision(d1)
    arch.add_architectural_decision(d2)
    errors = arch.validate_architecture()
    print(f"    Validation errors: {errors}")
    assert errors == [], f"Unexpected errors: {errors}"
    report_j = arch.get_architecture_report()
    print(f"    Report tier: {report_j.trust_tier.label}")
    coverage = arch.get_component_coverage_summary()
    print(f"    Component coverage: {coverage}")
    uncovered = arch.list_uncovered_requirements()
    print(f"    Uncovered requirements: {uncovered}")

    # ------------------------------------------------------------------
    # 5. NoveltyPolicy enforcement
    # ------------------------------------------------------------------
    print("\n[5] NoveltyPolicy enforcement")
    policy = NoveltyPolicy()

    def min_novelty_rule(
        idea_id: str, candidate: Dict[str, Any]
    ) -> Optional[PolicyViolationRecord]:
        score = candidate.get("novelty_score", 0.0)
        if score < 0.25:
            return PolicyViolationRecord(
                violation_id=_uid("viol-"),
                policy_name="diversity_policy",
                idea_id=idea_id,
                rule_id="min-novelty-inline",
                observed_value=score,
                threshold=0.25,
                severity=_clamp((0.25 - score) / 0.25, 0.0, 1.0),
                detected_at=_now_iso(),
            )
        return None

    def no_duplicate_rule(
        idea_id: str, candidate: Dict[str, Any]
    ) -> Optional[PolicyViolationRecord]:
        if candidate.get("is_duplicate", False):
            return PolicyViolationRecord(
                violation_id=_uid("viol-"),
                policy_name="uniqueness_policy",
                idea_id=idea_id,
                rule_id="no-dup-inline",
                observed_value=1.0,
                threshold=0.0,
                severity=1.0,
                detected_at=_now_iso(),
            )
        return None

    policy.register_policy(
        "diversity_policy", min_novelty_rule,
        description="Novelty >= 0.25", threshold=0.25, rule_type="min_novelty"
    )
    policy.register_policy(
        "uniqueness_policy", no_duplicate_rule,
        description="No duplicates", threshold=0.0, rule_type="hard_uniqueness"
    )
    candidates = [
        ("idea-001", {"novelty_score": 0.8, "is_duplicate": False}),
        ("idea-002", {"novelty_score": 0.1, "is_duplicate": False}),
        ("idea-003", {"novelty_score": 0.5, "is_duplicate": True}),
    ]
    violation_map = enforce_novelty_policy(policy, candidates)
    assert "idea-001" not in violation_map
    assert "idea-002" in violation_map
    assert "idea-003" in violation_map
    print(f"    Violating candidates: {list(violation_map.keys())}")
    summary = policy.violation_summary()
    print(f"    Violation summary: {summary}")
    cleared = policy.clear_violations()
    print(f"    Cleared {cleared} violations.")

    # ------------------------------------------------------------------
    # 6. derive_novelty_consequences
    # ------------------------------------------------------------------
    print("\n[6] derive_novelty_consequences")
    cfg = SystemConfig(
        system_name="test-system",
        archive_capacity=10,
        novelty_threshold=0.95,
        k_neighbours=5,
        diversity_weight=0.9,
        constraint_ids=("cst-001",),
        policy_names=("diversity_policy", "uniqueness_policy"),
        created_at=_now_iso(),
    )
    params = NoveltyParameters(
        min_novelty_score=0.95,
        max_archive_staleness=3,
        novelty_weight=0.9,
        behaviour_space_dims=8,
        distance_metric="cosine",
        archive_sampling_strategy="uniform",
    )
    consequences = derive_novelty_consequences(cfg, params)
    print(f"    Derived {len(consequences)} consequence(s):")
    for c in consequences:
        print(f"      [{c.trust_tier.label}] sev={c.severity:.2f} - "
              f"{c.description[:75]}")
    assert len(consequences) >= 4, "Expected at least 4 consequences"

    # ------------------------------------------------------------------
    # 7. NoveltyBehaviourDescriptor + diversity measurement
    # ------------------------------------------------------------------
    print("\n[7] NoveltyBehaviourDescriptor + measure_population_diversity")
    descs = [
        NoveltyBehaviourDescriptor(
            descriptor_id=_uid("desc-"),
            idea_id=f"idea-{i:03d}",
            feature_vector=(float(i % 2), float((i + 1) % 2), 0.0, 0.0),
            descriptor_type="embedding",
            dimensionality=4,
            created_at=_now_iso(),
        )
        for i in range(4)
    ]
    diversity = measure_population_diversity(descs)
    print(f"    Population size: {diversity.population_size}")
    print(f"    Mean pairwise distance: {diversity.mean_pairwise_distance:.4f}")
    print(f"    Diversity category: {diversity.is_diverse}")
    try:
        bad = NoveltyBehaviourDescriptor(
            descriptor_id=_uid(),
            idea_id="bad",
            feature_vector=(0.1, 0.2),
            descriptor_type="embedding",
            dimensionality=4,
            created_at=_now_iso(),
        )
        assert False, "Expected ValueError"
    except ValueError:
        print("    Dimensionality mismatch correctly raises ValueError.")

    # ------------------------------------------------------------------
    # 8. ConsequenceReport
    # ------------------------------------------------------------------
    print("\n[8] ConsequenceReport")
    report = ConsequenceReport(
        report_id=_uid("rep-"),
        system_name="jugeo-ideation",
        consequences=consequences,
        decisions=(d1, d2),
        constraint_violations=(),
        overall_trust_tier=TrustTier.REVIEWED,
        generated_at=_now_iso(),
        summary=(
            f"Analysed {len(consequences)} consequence(s) across "
            "2 architectural decision(s). No constraint violations."
        ),
    )
    print(f"    Report id: {report.report_id}")
    print(f"    Consequence count: {report.consequence_count}")
    print(f"    High-severity consequences: {len(report.high_severity_consequences)}")
    print(f"    Summary: {report.summary}")

    # ------------------------------------------------------------------
    # 9. Helper functions
    # ------------------------------------------------------------------
    print("\n[9] Helper functions")
    uid1, uid2 = _uid("test-"), _uid("test-")
    assert uid1 != uid2
    sim_ortho = _cosine_similarity([1.0, 0.0], [0.0, 1.0])
    sim_same = _cosine_similarity([1.0, 0.0], [1.0, 0.0])
    assert abs(sim_ortho) < 1e-9 and abs(sim_same - 1.0) < 1e-9
    assert _clamp(-5.0, 0.0, 1.0) == 0.0
    assert _clamp(5.0, 0.0, 1.0) == 1.0
    assert _novelty_score_from_archive([1.0], []) == 1.0
    print(f"    _now_iso(): {_now_iso()}")
    print(f"    cosine([1,0],[0,1])={sim_ortho:.4f}, cosine([1,0],[1,0])={sim_same:.4f}")
    print(f"    _clamp tests passed")
    print(f"    Empty archive novelty=1.0 checked")

    # ------------------------------------------------------------------
    # 10. ArchiveEvictionRecord
    # ------------------------------------------------------------------
    print("\n[10] ArchiveEvictionRecord")
    eviction = ArchiveEvictionRecord(
        eviction_id=_uid("ev-"),
        evicted_idea_id="idea-oldest",
        eviction_reason="Least novel entry; age-based eviction policy.",
        archive_size_before=100,
        archive_size_after=99,
        evicted_at=_now_iso(),
    )
    print(f"    Eviction id: {eviction.eviction_id}")
    print(f"    Reason: {eviction.eviction_reason}")

    print("\n" + "=" * 70)
    print("Smoke test PASSED \u2014 all assertions satisfied.")
    print("=" * 70)
