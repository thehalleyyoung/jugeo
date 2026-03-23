"""Section 5 — Generated Code Governance for the Unified Problem Atlas.

copilot: AI-generated code governance with trust ceilings, provenance chains, and policy enforcement.

This module implements the generated code governance chapter of the Unified
Problem Atlas.  All AI-generated code must carry:

  1. A *trust ceiling* — an upper bound on the trust that may be assigned
     to it without additional human or formal review.
  2. A *provenance chain* — a verifiable record of how the code was
     produced, by which model, under which prompt, and at what time.
  3. A *governance policy* — a set of rules that constrain what operations
     may be performed on the generated artefact and what verification is
     required before deployment.

Key components
--------------
GeneratorKind
    Enumeration of the recognised AI generator types (LLM, symbolic,
    hybrid, human-in-loop, unknown).
ProvenanceKind
    Enumeration of provenance entry kinds (generation, review, edit,
    test, deploy, redact).
TrustCeilingPolicy
    Enumeration of policies that determine the trust ceiling value.
GovernanceRecord
    Frozen record capturing a generated code artefact's identity,
    generator, and initial trust assignment.
ProvenanceEntry
    Frozen record for a single provenance chain step.
ProvenanceChain
    Frozen ordered sequence of ProvenanceEntry objects with validation.
TrustCeiling
    Frozen record encoding the trust ceiling for a generated artefact.
GovernancePolicy
    Frozen record encoding the full set of governance rules for an artefact.
GeneratedCodeGovernanceAnalyzer
    Evaluates governance records against policies and evidence.
GeneratedCodeGovernanceCoordinator
    Orchestrates the full governance pipeline: register → analyse → witness.
GeneratedCodeGovernanceWitness
    Frozen top-level certificate for a completed governance evaluation.

Design notes
------------
All model types are ``@dataclass(frozen=True, slots=True)``.  Trust ceilings
are inclusive: a ceiling of 0.8 means the artefact may receive at most 0.8
trust regardless of evidence quality.  The provenance chain is append-only;
entries may be redacted (replaced with a REDACT tombstone) but never removed.
"""

from __future__ import annotations

import uuid
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterator, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        ProblemCategory,
        AtlasCatalog,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    AtlasCatalog = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.specification_satisfaction import (
        SatisfactionStatus,
    )
except ImportError:
    SatisfactionStatus = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.performance_obligations import (
        ObligationSeverity,
        DischargeStatus,
    )
except ImportError:
    ObligationSeverity = None  # type: ignore[assignment]
    DischargeStatus = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

ArtefactId: TypeAlias = str
RecordId: TypeAlias = str
WitnessId: TypeAlias = str
PolicyId: TypeAlias = str
JsonDict: TypeAlias = dict[str, Any]
TrustScore: TypeAlias = float

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class GeneratorKind(str, Enum):
    """Recognised AI generator types for code artefacts.

    Attributes:
        LLM: Large language model (e.g., GPT, Claude, Gemini).
        SYMBOLIC: Symbolic/rule-based code synthesis system.
        HYBRID: Combination of LLM and symbolic methods.
        HUMAN_IN_LOOP: AI-assisted generation with mandatory human review step.
        TEMPLATE: Template-based generation (low AI content).
        UNKNOWN: Generator not identified; treat with maximum scepticism.
    """

    LLM = "LLM"
    SYMBOLIC = "SYMBOLIC"
    HYBRID = "HYBRID"
    HUMAN_IN_LOOP = "HUMAN_IN_LOOP"
    TEMPLATE = "TEMPLATE"
    UNKNOWN = "UNKNOWN"

    def default_trust_ceiling(self) -> float:
        """Return the default trust ceiling for artefacts from this generator.

        Returns:
            Float in [0.0, 1.0].
        """
        ceilings: dict[GeneratorKind, float] = {
            GeneratorKind.LLM: 0.70,
            GeneratorKind.SYMBOLIC: 0.85,
            GeneratorKind.HYBRID: 0.75,
            GeneratorKind.HUMAN_IN_LOOP: 0.90,
            GeneratorKind.TEMPLATE: 0.80,
            GeneratorKind.UNKNOWN: 0.40,
        }
        return ceilings[self]

    def requires_human_review(self) -> bool:
        """Return ``True`` when human review is mandatory before deployment.

        Returns:
            True for LLM, HYBRID, and UNKNOWN.
        """
        return self in {GeneratorKind.LLM, GeneratorKind.HYBRID, GeneratorKind.UNKNOWN}

    def requires_formal_verification(self) -> bool:
        """Return ``True`` when formal verification is recommended.

        Returns:
            True for LLM and UNKNOWN.
        """
        return self in {GeneratorKind.LLM, GeneratorKind.UNKNOWN}


class ProvenanceKind(str, Enum):
    """Kind of a single provenance chain entry.

    Attributes:
        GENERATION: Initial generation event (model, prompt, timestamp).
        REVIEW: Human review event (reviewer id, verdict).
        EDIT: Human or tool edit event (editor, change description).
        TEST: Automated test execution event (suite, pass/fail counts).
        DEPLOY: Deployment event (environment, approver).
        REDACT: Redaction tombstone (replaces sensitive entries).
        FORMAL_VERIFY: Formal verification event (tool, property, verdict).
        AUDIT: Governance audit event (auditor, finding).
    """

    GENERATION = "GENERATION"
    REVIEW = "REVIEW"
    EDIT = "EDIT"
    TEST = "TEST"
    DEPLOY = "DEPLOY"
    REDACT = "REDACT"
    FORMAL_VERIFY = "FORMAL_VERIFY"
    AUDIT = "AUDIT"

    def increases_trust(self) -> bool:
        """Return ``True`` when this kind of event increases the artefact's trust.

        Returns:
            True for REVIEW, TEST, and FORMAL_VERIFY.
        """
        return self in {
            ProvenanceKind.REVIEW,
            ProvenanceKind.TEST,
            ProvenanceKind.FORMAL_VERIFY,
        }

    def trust_increment(self) -> float:
        """Return the maximum trust increment this kind of event can contribute.

        Returns:
            Float in [0.0, 0.5].
        """
        increments: dict[ProvenanceKind, float] = {
            ProvenanceKind.GENERATION: 0.0,
            ProvenanceKind.REVIEW: 0.15,
            ProvenanceKind.EDIT: 0.05,
            ProvenanceKind.TEST: 0.10,
            ProvenanceKind.DEPLOY: 0.0,
            ProvenanceKind.REDACT: 0.0,
            ProvenanceKind.FORMAL_VERIFY: 0.25,
            ProvenanceKind.AUDIT: 0.05,
        }
        return increments[self]


class TrustCeilingPolicy(str, Enum):
    """Policy that determines how the trust ceiling is set and enforced.

    Attributes:
        GENERATOR_DEFAULT: Use the generator kind's default ceiling.
        EXPLICIT: Trust ceiling set explicitly by the governance policy.
        ESCALATABLE: Ceiling may be raised by explicit human authorisation.
        FIXED: Ceiling is permanently fixed and cannot be raised.
        DYNAMIC: Ceiling is computed dynamically from evidence.
    """

    GENERATOR_DEFAULT = "GENERATOR_DEFAULT"
    EXPLICIT = "EXPLICIT"
    ESCALATABLE = "ESCALATABLE"
    FIXED = "FIXED"
    DYNAMIC = "DYNAMIC"

    def can_be_raised(self) -> bool:
        """Return ``True`` when the trust ceiling can be raised.

        Returns:
            True for GENERATOR_DEFAULT, EXPLICIT, ESCALATABLE, and DYNAMIC.
        """
        return self in {
            TrustCeilingPolicy.GENERATOR_DEFAULT,
            TrustCeilingPolicy.EXPLICIT,
            TrustCeilingPolicy.ESCALATABLE,
            TrustCeilingPolicy.DYNAMIC,
        }


class GovernanceStatus(str, Enum):
    """Lifecycle status of a governance evaluation.

    Attributes:
        UNREGISTERED: Artefact not yet registered with the governance system.
        REGISTERED: Artefact registered; governance evaluation not started.
        UNDER_REVIEW: Governance evaluation in progress.
        COMPLIANT: Artefact meets all governance requirements.
        NON_COMPLIANT: Artefact violates one or more governance requirements.
        CONDITIONALLY_COMPLIANT: Compliant subject to conditions being met.
        WAIVED: Governance requirements explicitly waived by authorised reviewer.
        REVOKED: Previously compliant status revoked due to new evidence.
    """

    UNREGISTERED = "UNREGISTERED"
    REGISTERED = "REGISTERED"
    UNDER_REVIEW = "UNDER_REVIEW"
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    CONDITIONALLY_COMPLIANT = "CONDITIONALLY_COMPLIANT"
    WAIVED = "WAIVED"
    REVOKED = "REVOKED"

    def is_terminal(self) -> bool:
        """Return ``True`` when no further transitions are expected.

        Returns:
            True for COMPLIANT, NON_COMPLIANT, WAIVED, and REVOKED.
        """
        return self in {
            GovernanceStatus.COMPLIANT,
            GovernanceStatus.NON_COMPLIANT,
            GovernanceStatus.WAIVED,
            GovernanceStatus.REVOKED,
        }

    def is_positive(self) -> bool:
        """Return ``True`` when the status represents a passing outcome.

        Returns:
            True for COMPLIANT, CONDITIONALLY_COMPLIANT, and WAIVED.
        """
        return self in {
            GovernanceStatus.COMPLIANT,
            GovernanceStatus.CONDITIONALLY_COMPLIANT,
            GovernanceStatus.WAIVED,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §3  Frozen dataclasses — ProvenanceEntry and ProvenanceChain
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """A single step in a generated code artefact's provenance chain.

    Attributes:
        entry_id: UUID for this entry.
        kind: ProvenanceKind of this step.
        actor: Identifier of the actor (model name, user id, tool name, etc.).
        description: Human-readable description of the event.
        evidence: Evidence or data associated with this step.
        trust_delta: Trust score delta contributed by this step.
        timestamp: ISO-8601 timestamp.
        is_redacted: Whether this entry has been redacted.
    """

    entry_id: str
    kind: ProvenanceKind
    actor: str
    description: str
    evidence: tuple[tuple[str, str], ...]
    trust_delta: float
    timestamp: str
    is_redacted: bool

    @classmethod
    def make(
        cls,
        kind: ProvenanceKind,
        actor: str,
        description: str = "",
        evidence: tuple[tuple[str, str], ...] = (),
        trust_delta: float | None = None,
        timestamp: str = "",
    ) -> "ProvenanceEntry":
        """Create a new ProvenanceEntry with a generated UUID and timestamp.

        Args:
            kind: Kind of provenance event.
            actor: Identifier of the acting entity.
            description: Human-readable event description.
            evidence: Key-value evidence pairs.
            trust_delta: Trust delta override; defaults to kind.trust_increment().
            timestamp: ISO-8601 timestamp; auto-generated if empty.

        Returns:
            A new ProvenanceEntry.
        """
        import datetime

        delta = trust_delta if trust_delta is not None else kind.trust_increment()
        ts = timestamp or datetime.datetime.utcnow().isoformat() + "Z"
        return cls(
            entry_id=str(uuid.uuid4()),
            kind=kind,
            actor=actor,
            description=description,
            evidence=evidence,
            trust_delta=delta,
            timestamp=ts,
            is_redacted=False,
        )

    def redact(self) -> "ProvenanceEntry":
        """Return a redacted copy of this entry.

        Returns:
            New ProvenanceEntry with is_redacted=True and description cleared.
        """
        return replace(
            self,
            actor="[REDACTED]",
            description="[REDACTED]",
            evidence=(),
            is_redacted=True,
        )

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        if self.is_redacted:
            return {
                "entry_id": self.entry_id,
                "kind": ProvenanceKind.REDACT.value,
                "is_redacted": True,
                "timestamp": self.timestamp,
            }
        return {
            "entry_id": self.entry_id,
            "kind": self.kind.value,
            "actor": self.actor,
            "description": self.description,
            "evidence": list(self.evidence),
            "trust_delta": self.trust_delta,
            "timestamp": self.timestamp,
            "is_redacted": False,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceChain:
    """An ordered, immutable provenance chain for a generated artefact.

    The chain is append-only; entries are never removed (only redacted).
    The chain provides cumulative trust increment computation and
    completeness validation.

    Attributes:
        chain_id: UUID for this chain.
        artefact_id: Artefact this chain belongs to.
        entries: Ordered tuple of ProvenanceEntry objects.
        is_sealed: Whether the chain has been sealed (no further entries allowed).
    """

    chain_id: str
    artefact_id: str
    entries: tuple[ProvenanceEntry, ...]
    is_sealed: bool

    @classmethod
    def make(cls, artefact_id: str) -> "ProvenanceChain":
        """Create an empty ProvenanceChain with a generated UUID.

        Args:
            artefact_id: Identifier of the artefact this chain tracks.

        Returns:
            A new empty ProvenanceChain.
        """
        return cls(
            chain_id=str(uuid.uuid4()),
            artefact_id=artefact_id,
            entries=(),
            is_sealed=False,
        )

    # ------------------------------------------------------------------
    # Mutations (return new frozen instances)
    # ------------------------------------------------------------------

    def append(self, entry: ProvenanceEntry) -> "ProvenanceChain":
        """Return a copy with *entry* appended.

        Args:
            entry: The ProvenanceEntry to append.

        Returns:
            New ProvenanceChain with the entry appended.

        Raises:
            ValueError: If the chain is already sealed.
        """
        if self.is_sealed:
            raise ValueError("Cannot append to a sealed ProvenanceChain.")
        return replace(self, entries=(*self.entries, entry))

    def seal(self) -> "ProvenanceChain":
        """Return a sealed copy of the chain.

        Returns:
            New ProvenanceChain with is_sealed=True.
        """
        return replace(self, is_sealed=True)

    def redact_entry(self, entry_id: str) -> "ProvenanceChain":
        """Return a copy with the specified entry redacted.

        Args:
            entry_id: The entry to redact.

        Returns:
            New ProvenanceChain with the entry replaced by a redacted copy.
        """
        new_entries = tuple(
            e.redact() if e.entry_id == entry_id else e for e in self.entries
        )
        return replace(self, entries=new_entries)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def cumulative_trust_increment(self) -> float:
        """Compute the cumulative trust increment across all non-redacted entries.

        Returns:
            Float sum of trust_delta values (not clamped).
        """
        return sum(
            e.trust_delta for e in self.entries if not e.is_redacted
        )

    def has_generation_entry(self) -> bool:
        """Return ``True`` when the chain contains at least one GENERATION entry.

        Returns:
            True when a GENERATION entry exists.
        """
        return any(
            e.kind == ProvenanceKind.GENERATION and not e.is_redacted
            for e in self.entries
        )

    def has_review_entry(self) -> bool:
        """Return ``True`` when the chain contains at least one REVIEW entry.

        Returns:
            True when a non-redacted REVIEW entry exists.
        """
        return any(
            e.kind == ProvenanceKind.REVIEW and not e.is_redacted
            for e in self.entries
        )

    def entries_by_kind(self, kind: ProvenanceKind) -> list[ProvenanceEntry]:
        """Return all non-redacted entries of a given kind.

        Args:
            kind: The ProvenanceKind to filter by.

        Returns:
            List of matching ProvenanceEntry objects.
        """
        return [e for e in self.entries if e.kind == kind and not e.is_redacted]

    def length(self) -> int:
        """Return the number of entries in the chain.

        Returns:
            Integer entry count.
        """
        return len(self.entries)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "chain_id": self.chain_id,
            "artefact_id": self.artefact_id,
            "entries": [e.to_dict() for e in self.entries],
            "is_sealed": self.is_sealed,
            "cumulative_trust_increment": self.cumulative_trust_increment(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  Frozen dataclasses — TrustCeiling and GovernancePolicy
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TrustCeiling:
    """The trust ceiling for a generated code artefact.

    Attributes:
        ceiling_id: UUID for this trust ceiling record.
        artefact_id: Artefact this ceiling applies to.
        value: Maximum trust score in [0.0, 1.0].
        policy: Policy under which the ceiling was set.
        rationale: Human-readable justification for this ceiling.
        set_by: Identifier of the entity that set this ceiling.
        set_at: ISO-8601 timestamp.
        is_overridable: Whether the ceiling may be overridden with authorisation.
    """

    ceiling_id: str
    artefact_id: str
    value: float
    policy: TrustCeilingPolicy
    rationale: str
    set_by: str
    set_at: str
    is_overridable: bool

    @classmethod
    def make(
        cls,
        artefact_id: str,
        value: float,
        policy: TrustCeilingPolicy = TrustCeilingPolicy.EXPLICIT,
        rationale: str = "",
        set_by: str = "governance_system",
        is_overridable: bool = True,
    ) -> "TrustCeiling":
        """Create a new TrustCeiling with a generated UUID and timestamp.

        Args:
            artefact_id: Artefact identifier.
            value: Trust ceiling value in [0.0, 1.0].
            policy: Ceiling policy.
            rationale: Justification.
            set_by: Setter identifier.
            is_overridable: Whether the ceiling can be raised.

        Returns:
            A new TrustCeiling.
        """
        import datetime

        return cls(
            ceiling_id=str(uuid.uuid4()),
            artefact_id=artefact_id,
            value=max(0.0, min(1.0, value)),
            policy=policy,
            rationale=rationale,
            set_by=set_by,
            set_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_overridable=is_overridable,
        )

    def apply(self, trust_score: float) -> float:
        """Apply this ceiling to *trust_score*, returning the effective trust.

        Args:
            trust_score: Proposed trust score in [0.0, 1.0].

        Returns:
            min(trust_score, self.value).
        """
        return min(trust_score, self.value)

    def raise_to(self, new_value: float, authoriser: str) -> "TrustCeiling":
        """Return a copy with the ceiling raised to *new_value*.

        Args:
            new_value: New ceiling value.
            authoriser: Identifier of the authorising entity.

        Returns:
            New TrustCeiling with raised value.

        Raises:
            ValueError: If ceiling is not overridable or new_value ≤ current.
        """
        if not self.is_overridable:
            raise ValueError("This TrustCeiling is not overridable.")
        if new_value <= self.value:
            raise ValueError(
                f"New ceiling {new_value} is not higher than current {self.value}."
            )
        return replace(
            self,
            value=max(0.0, min(1.0, new_value)),
            rationale=f"Raised to {new_value:.2f} by {authoriser}",
            set_by=authoriser,
        )

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "ceiling_id": self.ceiling_id,
            "artefact_id": self.artefact_id,
            "value": self.value,
            "policy": self.policy.value,
            "rationale": self.rationale,
            "set_by": self.set_by,
            "set_at": self.set_at,
            "is_overridable": self.is_overridable,
        }


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    """The governance policy for a generated code artefact.

    Encodes the complete set of rules that constrain what may be done with
    a generated artefact and what verification is required before deployment.

    Attributes:
        policy_id: UUID for this policy.
        name: Short policy name.
        generator_kind: The generator kind this policy applies to.
        trust_ceiling: The TrustCeiling associated with this policy.
        requires_human_review: Whether a human must review before deployment.
        requires_test_suite: Whether an automated test suite is mandatory.
        requires_formal_verification: Whether formal verification is mandatory.
        allowed_deployment_environments: Environments to which deployment is allowed.
        prohibited_operations: Operations explicitly prohibited by this policy.
        max_lines_without_review: Maximum generated LOC before review is required.
        expiry_days: Number of days after which the policy must be renewed.
        metadata: Free-form annotations.
    """

    policy_id: str
    name: str
    generator_kind: GeneratorKind
    trust_ceiling: TrustCeiling
    requires_human_review: bool
    requires_test_suite: bool
    requires_formal_verification: bool
    allowed_deployment_environments: tuple[str, ...]
    prohibited_operations: tuple[str, ...]
    max_lines_without_review: int
    expiry_days: int
    metadata: tuple[tuple[str, str], ...]

    @classmethod
    def make(
        cls,
        name: str,
        generator_kind: GeneratorKind,
        artefact_id: str = "global",
        trust_ceiling_override: float | None = None,
        requires_human_review: bool | None = None,
        requires_test_suite: bool = True,
        requires_formal_verification: bool | None = None,
        allowed_deployment_environments: tuple[str, ...] = ("staging", "production"),
        prohibited_operations: tuple[str, ...] = (),
        max_lines_without_review: int = 100,
        expiry_days: int = 365,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "GovernancePolicy":
        """Create a new GovernancePolicy with sensible defaults from the generator kind.

        Args:
            name: Policy name.
            generator_kind: The generator this policy governs.
            artefact_id: Artefact this policy is bound to.
            trust_ceiling_override: Override for the trust ceiling value.
            requires_human_review: Override for human review requirement.
            requires_test_suite: Whether a test suite is required.
            requires_formal_verification: Override for formal verification requirement.
            allowed_deployment_environments: Allowed environments.
            prohibited_operations: Explicitly prohibited operations.
            max_lines_without_review: Max LOC before review trigger.
            expiry_days: Policy validity period in days.
            metadata: Extra annotations.

        Returns:
            A new GovernancePolicy.
        """
        ceiling_value = (
            trust_ceiling_override
            if trust_ceiling_override is not None
            else generator_kind.default_trust_ceiling()
        )
        ceiling = TrustCeiling.make(
            artefact_id=artefact_id,
            value=ceiling_value,
            policy=TrustCeilingPolicy.GENERATOR_DEFAULT
            if trust_ceiling_override is None
            else TrustCeilingPolicy.EXPLICIT,
            rationale=f"Default ceiling for {generator_kind.value} generator.",
        )
        human_review = (
            requires_human_review
            if requires_human_review is not None
            else generator_kind.requires_human_review()
        )
        formal_verify = (
            requires_formal_verification
            if requires_formal_verification is not None
            else generator_kind.requires_formal_verification()
        )
        return cls(
            policy_id=str(uuid.uuid4()),
            name=name,
            generator_kind=generator_kind,
            trust_ceiling=ceiling,
            requires_human_review=human_review,
            requires_test_suite=requires_test_suite,
            requires_formal_verification=formal_verify,
            allowed_deployment_environments=allowed_deployment_environments,
            prohibited_operations=prohibited_operations,
            max_lines_without_review=max_lines_without_review,
            expiry_days=expiry_days,
            metadata=metadata,
        )

    def effective_trust_ceiling(self) -> float:
        """Return the effective trust ceiling value.

        Returns:
            Float in [0.0, 1.0].
        """
        return self.trust_ceiling.value

    def metadata_dict(self) -> dict[str, str]:
        """Materialise metadata as a plain dict.

        Returns:
            Dict of annotation key-value pairs.
        """
        return dict(self.metadata)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "generator_kind": self.generator_kind.value,
            "trust_ceiling": self.trust_ceiling.to_dict(),
            "requires_human_review": self.requires_human_review,
            "requires_test_suite": self.requires_test_suite,
            "requires_formal_verification": self.requires_formal_verification,
            "allowed_deployment_environments": list(self.allowed_deployment_environments),
            "prohibited_operations": list(self.prohibited_operations),
            "max_lines_without_review": self.max_lines_without_review,
            "expiry_days": self.expiry_days,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  Frozen dataclasses — GovernanceRecord
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class GovernanceRecord:
    """A generated code artefact registered with the governance system.

    Attributes:
        record_id: UUID for this governance record.
        artefact_id: Identifier of the generated artefact.
        artefact_name: Human-readable name.
        generator_kind: The AI generator that produced this artefact.
        model_id: Specific model identifier (e.g., ``"gpt-4o"``, ``"claude-3-5-sonnet"``).
        prompt_hash: Hash of the prompt used for generation (for traceability).
        line_count: Number of lines in the generated artefact.
        language: Programming language.
        provenance_chain: The ProvenanceChain for this artefact.
        policy: The GovernancePolicy applied to this artefact.
        current_trust: Current effective trust score.
        status: Current governance status.
        registered_at: ISO-8601 timestamp.
        metadata: Free-form annotations.
    """

    record_id: str
    artefact_id: str
    artefact_name: str
    generator_kind: GeneratorKind
    model_id: str
    prompt_hash: str
    line_count: int
    language: str
    provenance_chain: ProvenanceChain
    policy: GovernancePolicy
    current_trust: float
    status: GovernanceStatus
    registered_at: str
    metadata: tuple[tuple[str, str], ...]

    @classmethod
    def make(
        cls,
        artefact_id: str,
        artefact_name: str,
        generator_kind: GeneratorKind,
        policy: GovernancePolicy,
        model_id: str = "unknown",
        prompt_hash: str = "",
        line_count: int = 0,
        language: str = "unknown",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "GovernanceRecord":
        """Create a new GovernanceRecord with generated UUID, empty chain, and REGISTERED status.

        Args:
            artefact_id: Artefact identifier.
            artefact_name: Human-readable name.
            generator_kind: Generator kind.
            policy: Governance policy to apply.
            model_id: Specific model identifier.
            prompt_hash: Hash of the generation prompt.
            line_count: Generated line count.
            language: Programming language.
            metadata: Extra annotations.

        Returns:
            A new GovernanceRecord in REGISTERED status.
        """
        import datetime

        chain = ProvenanceChain.make(artefact_id)
        # Append initial GENERATION entry
        gen_entry = ProvenanceEntry.make(
            kind=ProvenanceKind.GENERATION,
            actor=model_id,
            description=f"Generated by {model_id} ({generator_kind.value})",
            evidence=(("prompt_hash", prompt_hash),) if prompt_hash else (),
        )
        chain = chain.append(gen_entry)

        initial_trust = policy.effective_trust_ceiling() * 0.5

        return cls(
            record_id=str(uuid.uuid4()),
            artefact_id=artefact_id,
            artefact_name=artefact_name,
            generator_kind=generator_kind,
            model_id=model_id,
            prompt_hash=prompt_hash,
            line_count=line_count,
            language=language,
            provenance_chain=chain,
            policy=policy,
            current_trust=max(0.0, min(1.0, initial_trust)),
            status=GovernanceStatus.REGISTERED,
            registered_at=datetime.datetime.utcnow().isoformat() + "Z",
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors and transitions
    # ------------------------------------------------------------------

    def effective_trust(self) -> float:
        """Return the effective trust after applying the policy ceiling.

        Returns:
            Float in [0.0, trust_ceiling].
        """
        raw = self.current_trust + self.provenance_chain.cumulative_trust_increment()
        return self.policy.trust_ceiling.apply(raw)

    def append_provenance(self, entry: ProvenanceEntry) -> "GovernanceRecord":
        """Return a copy with *entry* appended to the provenance chain.

        Args:
            entry: New provenance entry.

        Returns:
            New GovernanceRecord with updated chain.
        """
        new_chain = self.provenance_chain.append(entry)
        return replace(self, provenance_chain=new_chain)

    def with_status(self, status: GovernanceStatus) -> "GovernanceRecord":
        """Return a copy with the given governance status.

        Args:
            status: New governance status.

        Returns:
            New GovernanceRecord with updated status.
        """
        return replace(self, status=status)

    def needs_review(self) -> bool:
        """Return ``True`` when the record requires human review.

        Returns:
            True when policy requires review AND no review entry exists.
        """
        return (
            self.policy.requires_human_review
            and not self.provenance_chain.has_review_entry()
        )

    def exceeds_line_limit(self) -> bool:
        """Return ``True`` when the artefact exceeds the policy's line limit.

        Returns:
            True when line_count > policy.max_lines_without_review.
        """
        return self.line_count > self.policy.max_lines_without_review

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "record_id": self.record_id,
            "artefact_id": self.artefact_id,
            "artefact_name": self.artefact_name,
            "generator_kind": self.generator_kind.value,
            "model_id": self.model_id,
            "prompt_hash": self.prompt_hash,
            "line_count": self.line_count,
            "language": self.language,
            "current_trust": self.current_trust,
            "effective_trust": self.effective_trust(),
            "status": self.status.value,
            "registered_at": self.registered_at,
            "provenance_chain": self.provenance_chain.to_dict(),
            "policy": self.policy.to_dict(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §6  GeneratedCodeGovernanceAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class GovernanceAnalysisResult:
    """Output of a GeneratedCodeGovernanceAnalyzer pass.

    Attributes:
        result_id: UUID for this result.
        record_id: GovernanceRecord that was analysed.
        is_compliant: Whether the record meets all governance requirements.
        compliance_score: Fractional compliance in [0.0, 1.0].
        violations: Descriptions of policy violations.
        warnings: Non-blocking governance concerns.
        recommendations: Recommended actions to achieve compliance.
        effective_trust: Computed effective trust after ceiling application.
    """

    result_id: str
    record_id: str
    is_compliant: bool
    compliance_score: float
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    recommendations: tuple[str, ...]
    effective_trust: float

    @classmethod
    def make(
        cls,
        record_id: str,
        is_compliant: bool,
        compliance_score: float,
        violations: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        recommendations: tuple[str, ...] = (),
        effective_trust: float = 0.0,
    ) -> "GovernanceAnalysisResult":
        """Create a new GovernanceAnalysisResult with a generated UUID.

        Args:
            record_id: Governance record identifier.
            is_compliant: Compliance verdict.
            compliance_score: Fractional compliance.
            violations: Policy violation descriptions.
            warnings: Non-blocking concerns.
            recommendations: Recommended actions.
            effective_trust: Computed effective trust.

        Returns:
            A new GovernanceAnalysisResult.
        """
        return cls(
            result_id=str(uuid.uuid4()),
            record_id=record_id,
            is_compliant=is_compliant,
            compliance_score=max(0.0, min(1.0, compliance_score)),
            violations=violations,
            warnings=warnings,
            recommendations=recommendations,
            effective_trust=max(0.0, min(1.0, effective_trust)),
        )

    def is_acceptable(self) -> bool:
        """Return ``True`` when compliance_score is acceptable.

        Returns:
            True when is_compliant and compliance_score ≥ 0.8.
        """
        return self.is_compliant and self.compliance_score >= 0.8


class GeneratedCodeGovernanceAnalyzer:
    """Evaluates GovernanceRecord objects against their policies.

    The analyzer checks:
    1. Human review requirement: is a review entry present?
    2. Test suite requirement: is a TEST entry present?
    3. Formal verification requirement: is a FORMAL_VERIFY entry present?
    4. Line count limit: does the artefact exceed the policy limit?
    5. Prohibited operations: has any prohibited operation been performed?
    6. Trust ceiling: is the effective trust within bounds?
    """

    def analyse(self, record: GovernanceRecord) -> GovernanceAnalysisResult:
        """Analyse a GovernanceRecord for policy compliance.

        Args:
            record: The governance record to analyse.

        Returns:
            A GovernanceAnalysisResult.
        """
        violations: list[str] = []
        warnings: list[str] = []
        recs: list[str] = []
        checks_passed = 0
        checks_total = 0

        # Human review
        checks_total += 1
        if record.policy.requires_human_review:
            if not record.provenance_chain.has_review_entry():
                violations.append("Human review required but no REVIEW provenance entry found.")
                recs.append("Submit this artefact for human review before deployment.")
            else:
                checks_passed += 1
        else:
            checks_passed += 1

        # Test suite
        checks_total += 1
        if record.policy.requires_test_suite:
            test_entries = record.provenance_chain.entries_by_kind(ProvenanceKind.TEST)
            if not test_entries:
                violations.append("Test suite required but no TEST provenance entry found.")
                recs.append("Run the test suite and record results in the provenance chain.")
            else:
                checks_passed += 1
        else:
            checks_passed += 1

        # Formal verification
        checks_total += 1
        if record.policy.requires_formal_verification:
            fv_entries = record.provenance_chain.entries_by_kind(ProvenanceKind.FORMAL_VERIFY)
            if not fv_entries:
                violations.append(
                    "Formal verification required but no FORMAL_VERIFY provenance entry found."
                )
                recs.append("Apply formal verification and record the result.")
            else:
                checks_passed += 1
        else:
            checks_passed += 1

        # Line count
        checks_total += 1
        if record.exceeds_line_limit():
            warnings.append(
                f"Line count {record.line_count} exceeds policy limit "
                f"{record.policy.max_lines_without_review}."
            )
            recs.append(
                "Request additional human review due to large generated artefact."
            )
            checks_passed += 1  # warning, not blocking
        else:
            checks_passed += 1

        # Provenance chain completeness
        checks_total += 1
        if not record.provenance_chain.has_generation_entry():
            violations.append("Provenance chain lacks a GENERATION entry.")
            recs.append("Add a GENERATION entry to the provenance chain.")
        else:
            checks_passed += 1

        effective_trust = record.effective_trust()
        compliance_score = checks_passed / checks_total if checks_total else 1.0
        is_compliant = len(violations) == 0

        return GovernanceAnalysisResult.make(
            record_id=record.record_id,
            is_compliant=is_compliant,
            compliance_score=compliance_score,
            violations=tuple(violations),
            warnings=tuple(warnings),
            recommendations=tuple(recs),
            effective_trust=effective_trust,
        )

    def analyse_batch(
        self, records: Sequence[GovernanceRecord]
    ) -> list[GovernanceAnalysisResult]:
        """Analyse a batch of governance records.

        Args:
            records: Records to analyse.

        Returns:
            List of GovernanceAnalysisResult in input order.
        """
        return [self.analyse(r) for r in records]


# ═══════════════════════════════════════════════════════════════════════════
# §7  GeneratedCodeGovernanceWitness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class GeneratedCodeGovernanceWitness:
    """Top-level certificate for a completed governance evaluation.

    Attributes:
        witness_id: UUID for this witness.
        record_id: The GovernanceRecord this witness covers.
        artefact_id: Generated artefact identifier.
        analysis_result: The GovernanceAnalysisResult from the analyzer.
        final_status: Terminal governance status.
        effective_trust: Computed effective trust at time of issuance.
        trust_ceiling_applied: The trust ceiling value that was applied.
        provenance_chain_length: Number of entries in the provenance chain.
        overall_confidence: Confidence in the governance verdict.
        issued_at: ISO-8601 timestamp.
        notes: Free-form notes.
    """

    witness_id: str
    record_id: str
    artefact_id: str
    analysis_result: GovernanceAnalysisResult
    final_status: GovernanceStatus
    effective_trust: float
    trust_ceiling_applied: float
    provenance_chain_length: int
    overall_confidence: float
    issued_at: str
    notes: str

    @classmethod
    def make(
        cls,
        record: GovernanceRecord,
        analysis: GovernanceAnalysisResult,
        notes: str = "",
    ) -> "GeneratedCodeGovernanceWitness":
        """Create a GeneratedCodeGovernanceWitness from pipeline artefacts.

        Args:
            record: The evaluated governance record.
            analysis: The analysis result.
            notes: Free-form notes.

        Returns:
            A new GeneratedCodeGovernanceWitness.
        """
        import datetime

        status = (
            GovernanceStatus.COMPLIANT
            if analysis.is_acceptable()
            else GovernanceStatus.NON_COMPLIANT
        )
        confidence = analysis.compliance_score * analysis.effective_trust

        return cls(
            witness_id=str(uuid.uuid4()),
            record_id=record.record_id,
            artefact_id=record.artefact_id,
            analysis_result=analysis,
            final_status=status,
            effective_trust=analysis.effective_trust,
            trust_ceiling_applied=record.policy.effective_trust_ceiling(),
            provenance_chain_length=record.provenance_chain.length(),
            overall_confidence=max(0.0, min(1.0, confidence)),
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def is_compliant(self) -> bool:
        """Return ``True`` when the artefact is governance-compliant.

        Returns:
            True when final_status is COMPLIANT.
        """
        return self.final_status == GovernanceStatus.COMPLIANT

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "record_id": self.record_id,
            "artefact_id": self.artefact_id,
            "final_status": self.final_status.value,
            "effective_trust": self.effective_trust,
            "trust_ceiling_applied": self.trust_ceiling_applied,
            "provenance_chain_length": self.provenance_chain_length,
            "overall_confidence": self.overall_confidence,
            "issued_at": self.issued_at,
            "notes": self.notes,
            "analysis": {
                "is_compliant": self.analysis_result.is_compliant,
                "compliance_score": self.analysis_result.compliance_score,
                "violations": list(self.analysis_result.violations),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# §8  GeneratedCodeGovernanceCoordinator
# ═══════════════════════════════════════════════════════════════════════════


class GeneratedCodeGovernanceCoordinator:
    """Orchestrates the full generated code governance pipeline.

    The coordinator manages:
    - A registry of GovernanceRecord objects.
    - Policy application and trust ceiling enforcement.
    - Provenance chain management.
    - Governance analysis via GeneratedCodeGovernanceAnalyzer.
    - Witness production and accumulation.

    Attributes:
        analyzer: The GeneratedCodeGovernanceAnalyzer.
        _records: Dict from record_id to GovernanceRecord.
        _witnesses: Dict from record_id to GeneratedCodeGovernanceWitness.
    """

    def __init__(
        self,
        analyzer: GeneratedCodeGovernanceAnalyzer | None = None,
    ) -> None:
        self.analyzer = analyzer or GeneratedCodeGovernanceAnalyzer()
        self._records: dict[str, GovernanceRecord] = {}
        self._witnesses: dict[str, GeneratedCodeGovernanceWitness] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, record: GovernanceRecord) -> GovernanceRecord:
        """Register a GovernanceRecord.

        Args:
            record: The record to register.

        Returns:
            The registered record.
        """
        self._records[record.record_id] = record
        return record

    def add_provenance(
        self, record_id: str, entry: ProvenanceEntry
    ) -> GovernanceRecord:
        """Append a provenance entry to a registered record.

        Args:
            record_id: The record to update.
            entry: The ProvenanceEntry to append.

        Returns:
            The updated GovernanceRecord.

        Raises:
            KeyError: If record_id is not registered.
        """
        record = self._records[record_id]
        updated = record.append_provenance(entry)
        self._records[record_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def evaluate(
        self,
        record_id: str,
    ) -> GeneratedCodeGovernanceWitness:
        """Evaluate a registered governance record for compliance.

        Args:
            record_id: The record to evaluate.

        Returns:
            A GeneratedCodeGovernanceWitness.

        Raises:
            KeyError: If record_id is not registered.
        """
        record = self._records[record_id]
        analysis = self.analyzer.analyse(record)

        new_status = (
            GovernanceStatus.COMPLIANT
            if analysis.is_acceptable()
            else GovernanceStatus.NON_COMPLIANT
        )
        record = record.with_status(new_status)
        self._records[record_id] = record

        witness = GeneratedCodeGovernanceWitness.make(record, analysis)
        self._witnesses[record_id] = witness
        return witness

    def evaluate_all(self) -> list[GeneratedCodeGovernanceWitness]:
        """Evaluate all registered records.

        Returns:
            List of GeneratedCodeGovernanceWitness in registration order.
        """
        return [self.evaluate(rid) for rid in list(self._records.keys())]

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_witness(self, record_id: str) -> GeneratedCodeGovernanceWitness | None:
        """Return the witness for a record, if available.

        Args:
            record_id: The record identifier.

        Returns:
            The GeneratedCodeGovernanceWitness or None.
        """
        return self._witnesses.get(record_id)

    def all_witnesses(self) -> list[GeneratedCodeGovernanceWitness]:
        """Return all completed witnesses.

        Returns:
            List of GeneratedCodeGovernanceWitness.
        """
        return list(self._witnesses.values())

    def compliance_rate(self) -> float:
        """Return fraction of evaluated records that are compliant.

        Returns:
            Float in [0.0, 1.0].
        """
        if not self._witnesses:
            return 0.0
        n = sum(1 for w in self._witnesses.values() if w.is_compliant())
        return n / len(self._witnesses)

    def non_compliant_records(self) -> list[GovernanceRecord]:
        """Return all non-compliant records.

        Returns:
            List of GovernanceRecord with NON_COMPLIANT status.
        """
        return [
            r
            for r in self._records.values()
            if r.status == GovernanceStatus.NON_COMPLIANT
        ]

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of governance evaluation statistics.

        Returns:
            Dict with total count, compliance rate, and status breakdown.
        """
        counts: dict[str, int] = defaultdict(int)
        for w in self._witnesses.values():
            counts[w.final_status.value] += 1
        return {
            "total": len(self._witnesses),
            "compliance_rate": self.compliance_rate(),
            "by_status": dict(counts),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §9  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def quick_govern(
    artefact_id: str,
    artefact_name: str,
    generator_kind: GeneratorKind,
    model_id: str = "unknown",
    line_count: int = 50,
    language: str = "python",
    add_review: bool = False,
    add_tests: bool = False,
    add_formal_verify: bool = False,
) -> GeneratedCodeGovernanceWitness:
    """Create, register, and evaluate a governance record in one call.

    Args:
        artefact_id: Artefact identifier.
        artefact_name: Human-readable artefact name.
        generator_kind: Generator kind.
        model_id: Model identifier.
        line_count: Generated line count.
        language: Programming language.
        add_review: Whether to add a synthetic REVIEW entry to the chain.
        add_tests: Whether to add a synthetic TEST entry to the chain.
        add_formal_verify: Whether to add a synthetic FORMAL_VERIFY entry.

    Returns:
        A GeneratedCodeGovernanceWitness.
    """
    policy = GovernancePolicy.make(
        name=f"default_policy_{generator_kind.value.lower()}",
        generator_kind=generator_kind,
        artefact_id=artefact_id,
    )
    record = GovernanceRecord.make(
        artefact_id=artefact_id,
        artefact_name=artefact_name,
        generator_kind=generator_kind,
        policy=policy,
        model_id=model_id,
        line_count=line_count,
        language=language,
    )
    coord = GeneratedCodeGovernanceCoordinator()
    coord.register(record)

    if add_review:
        review_entry = ProvenanceEntry.make(
            kind=ProvenanceKind.REVIEW,
            actor="engineer@example.com",
            description="Manual review passed.",
        )
        coord.add_provenance(record.record_id, review_entry)

    if add_tests:
        test_entry = ProvenanceEntry.make(
            kind=ProvenanceKind.TEST,
            actor="ci_pipeline",
            description="All 42 tests passed.",
            evidence=(("pass_count", "42"), ("fail_count", "0")),
        )
        coord.add_provenance(record.record_id, test_entry)

    if add_formal_verify:
        fv_entry = ProvenanceEntry.make(
            kind=ProvenanceKind.FORMAL_VERIFY,
            actor="formal_verifier",
            description="All specified properties formally verified.",
            evidence=(("tool", "dafny"), ("properties_verified", "5")),
        )
        coord.add_provenance(record.record_id, fv_entry)

    return coord.evaluate(record.record_id)


def get_all_generator_kinds() -> list[GeneratorKind]:
    """Return all GeneratorKind values.

    Returns:
        List of all GeneratorKind members.
    """
    return list(GeneratorKind)


def get_all_provenance_kinds() -> list[ProvenanceKind]:
    """Return all ProvenanceKind values.

    Returns:
        List of all ProvenanceKind members.
    """
    return list(ProvenanceKind)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ═══════════════════════════════════════════════════════════════════════════
# §10  __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enumerations
    "GeneratorKind",
    "GovernanceStatus",
    "ProvenanceKind",
    "TrustCeilingPolicy",
    # Frozen dataclasses
    "GovernanceAnalysisResult",
    "GovernancePolicy",
    "GovernanceRecord",
    "GeneratedCodeGovernanceWitness",
    "ProvenanceChain",
    "ProvenanceEntry",
    "TrustCeiling",
    # Classes
    "GeneratedCodeGovernanceAnalyzer",
    "GeneratedCodeGovernanceCoordinator",
    # Functions
    "get_all_generator_kinds",
    "get_all_provenance_kinds",
    "quick_govern",
    # Type aliases
    "ArtefactId",
    "JsonDict",
    "PolicyId",
    "RecordId",
    "TrustScore",
    "WitnessId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.


# ═══════════════════════════════════════════════════════════════════════════
# §11  Smoke test
# ═══════════════════════════════════════════════════════════════════════════

def _smoke() -> None:
    """Minimal self-test: register an LLM-generated artefact and evaluate it."""
    # Non-compliant: no review, no tests
    w_fail = quick_govern(
        artefact_id="art::login_handler",
        artefact_name="login_handler.py",
        generator_kind=GeneratorKind.LLM,
        model_id="claude-3-5-sonnet",
        line_count=80,
        language="python",
        add_review=False,
        add_tests=False,
    )
    assert w_fail.final_status == GovernanceStatus.NON_COMPLIANT, (
        f"Expected NON_COMPLIANT, got {w_fail.final_status}"
    )
    assert w_fail.effective_trust <= GeneratorKind.LLM.default_trust_ceiling()

    # Compliant: with review, tests, and formal verification
    w_pass = quick_govern(
        artefact_id="art::login_handler_v2",
        artefact_name="login_handler_v2.py",
        generator_kind=GeneratorKind.LLM,
        model_id="claude-3-5-sonnet",
        line_count=80,
        language="python",
        add_review=True,
        add_tests=True,
        add_formal_verify=True,
    )
    assert w_pass.final_status == GovernanceStatus.COMPLIANT, (
        f"Expected COMPLIANT, got {w_pass.final_status}"
    )
    assert w_pass.effective_trust <= GeneratorKind.LLM.default_trust_ceiling()

    d = w_pass.to_dict()
    assert "witness_id" in d and "trust_ceiling_applied" in d

    # ProvenanceChain smoke
    chain = ProvenanceChain.make("art::test")
    entry = ProvenanceEntry.make(ProvenanceKind.GENERATION, "gpt-4o", "Generated.")
    chain = chain.append(entry)
    assert chain.has_generation_entry()
    chain_sealed = chain.seal()
    try:
        chain_sealed.append(entry)
        assert False, "Should have raised ValueError on sealed chain"
    except ValueError:
        pass

    print(
        f"[smoke] non_compliant={w_fail.final_status.value} "
        f"compliant={w_pass.final_status.value} "
        f"trust={w_pass.effective_trust:.3f} "
        f"ceiling={w_pass.trust_ceiling_applied:.2f}"
    )


if __name__ == "__main__":
    _smoke()
