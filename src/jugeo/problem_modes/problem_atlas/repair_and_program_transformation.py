"""Section 2 — Repair and Program Transformation for the Unified Problem Atlas.

copilot: repair-as-section-replacement and transformation-as-section-morphism engine.

This module implements the repair and program transformation chapter of the
Unified Problem Atlas.  In the atlas, *repair* is modelled as a localised
section replacement: a faulty section of a program is excised and a
corrected section is spliced in its place.  *Transformation* is the more
general operation — a section morphism that maps one program structure to
another while preserving (or improving) semantic properties.

Key components
--------------
RepairKind
    Enumeration of recognised repair strategies (patch, splice, rewrite,
    semantic, generative).
TransformationKind
    Enumeration of transformation classes (refactoring, optimisation,
    migration, normalisation, synthesis).
RepairEntry
    Frozen record capturing a single repair specification: which section is
    faulty, what the replacement is, and the expected semantic effect.
TransformationEntry
    Frozen record capturing a transformation specification: source section,
    target section, the morphism type, and correctness obligations.
RepairProgramTransformationAnalyzer
    Analyses repair and transformation entries for feasibility, safety, and
    semantic preservation.
RepairProgramTransformationCoordinator
    Orchestrates the full pipeline: register → analyse → apply → witness.
RepairProgramTransformationWitness
    Frozen certificate produced after a repair or transformation is applied
    and verified.
TransformationWitness
    Lightweight sub-witness for a single transformation step.

Design notes
------------
All model types are ``@dataclass(frozen=True, slots=True)``.  The coordinator
maintains an ordered log of all applied repairs and transformations, enabling
replay and audit.  Section identifiers are opaque strings; the atlas does not
inspect program internals.
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
        SpecificationKind,
    )
except ImportError:
    SatisfactionStatus = None  # type: ignore[assignment]
    SpecificationKind = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

SectionId: TypeAlias = str
EntryId: TypeAlias = str
WitnessId: TypeAlias = str
JsonDict: TypeAlias = dict[str, Any]
SemanticEffect: TypeAlias = str

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class RepairKind(str, Enum):
    """Recognised repair strategies in the atlas.

    Each kind reflects a different semantic granularity of the repair
    operation.  The atlas tracks which kind was applied in the RepairEntry.

    Attributes:
        PATCH: Minimal textual diff applied to the faulty section.
        SPLICE: Replacement of a contiguous section with a new fragment.
        REWRITE: Full semantic rewrite of the section from specification.
        SEMANTIC: Semantic-preserving transformation that corrects a bug.
        GENERATIVE: AI- or tool-generated replacement with new functionality.
        ROLLBACK: Revert the section to a previously known good state.
    """

    PATCH = "PATCH"
    SPLICE = "SPLICE"
    REWRITE = "REWRITE"
    SEMANTIC = "SEMANTIC"
    GENERATIVE = "GENERATIVE"
    ROLLBACK = "ROLLBACK"

    def is_conservative(self) -> bool:
        """Return ``True`` when the strategy minimises semantic disruption.

        Returns:
            True for PATCH, SPLICE, and ROLLBACK.
        """
        return self in {RepairKind.PATCH, RepairKind.SPLICE, RepairKind.ROLLBACK}

    def requires_specification(self) -> bool:
        """Return ``True`` when the strategy requires a formal specification.

        Returns:
            True for REWRITE and SEMANTIC.
        """
        return self in {RepairKind.REWRITE, RepairKind.SEMANTIC}

    def trust_cost(self) -> float:
        """Return the trust cost associated with this repair kind.

        More invasive repairs carry a higher trust cost because they are harder
        to verify.

        Returns:
            Float trust cost in [0.0, 1.0].
        """
        costs: dict[RepairKind, float] = {
            RepairKind.PATCH: 0.05,
            RepairKind.SPLICE: 0.10,
            RepairKind.REWRITE: 0.20,
            RepairKind.SEMANTIC: 0.15,
            RepairKind.GENERATIVE: 0.35,
            RepairKind.ROLLBACK: 0.02,
        }
        return costs[self]


class TransformationKind(str, Enum):
    """Transformation classes recognised by the atlas.

    Each kind corresponds to a distinct category of section morphism with
    its own correctness obligations.

    Attributes:
        REFACTORING: Structure-preserving rewrite with identical semantics.
        OPTIMISATION: Semantics-preserving rewrite with improved performance.
        MIGRATION: Language/framework migration preserving external behaviour.
        NORMALISATION: Canonical form reduction (e.g., α-normalisation).
        SYNTHESIS: Generation of a new section from a specification.
        ABSTRACTION: Extraction of a reusable component from an inline section.
        SPECIALISATION: Instantiation of a generic component for a specific context.
    """

    REFACTORING = "REFACTORING"
    OPTIMISATION = "OPTIMISATION"
    MIGRATION = "MIGRATION"
    NORMALISATION = "NORMALISATION"
    SYNTHESIS = "SYNTHESIS"
    ABSTRACTION = "ABSTRACTION"
    SPECIALISATION = "SPECIALISATION"

    def is_semantics_preserving(self) -> bool:
        """Return ``True`` when the transformation must preserve full semantics.

        Returns:
            True for REFACTORING and NORMALISATION.
        """
        return self in {TransformationKind.REFACTORING, TransformationKind.NORMALISATION}

    def requires_proof_of_equivalence(self) -> bool:
        """Return ``True`` when a proof of semantic equivalence is mandatory.

        Returns:
            True for REFACTORING, OPTIMISATION, and MIGRATION.
        """
        return self in {
            TransformationKind.REFACTORING,
            TransformationKind.OPTIMISATION,
            TransformationKind.MIGRATION,
        }

    def default_evidence_channel(self) -> str:
        """Return the canonical evidence channel for this transformation kind.

        Returns:
            A string channel identifier.
        """
        mapping: dict[TransformationKind, str] = {
            TransformationKind.REFACTORING: "formal_proof",
            TransformationKind.OPTIMISATION: "profiler",
            TransformationKind.MIGRATION: "test_suite",
            TransformationKind.NORMALISATION: "solver",
            TransformationKind.SYNTHESIS: "oracle",
            TransformationKind.ABSTRACTION: "human",
            TransformationKind.SPECIALISATION: "solver",
        }
        return mapping[self]


class RepairStatus(str, Enum):
    """Lifecycle status of a repair or transformation entry.

    Attributes:
        PROPOSED: Entry created but not yet applied.
        ANALYSING: Feasibility and safety analysis in progress.
        APPROVED: Analysis passed; ready for application.
        APPLIED: Repair/transformation applied to the target section.
        VERIFIED: Post-application verification successful.
        REJECTED: Analysis or verification failed; entry rejected.
        ROLLED_BACK: Applied repair reverted due to regression.
    """

    PROPOSED = "PROPOSED"
    ANALYSING = "ANALYSING"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"

    def is_terminal(self) -> bool:
        """Return ``True`` when no further transitions are expected.

        Returns:
            True for VERIFIED, REJECTED, and ROLLED_BACK.
        """
        return self in {
            RepairStatus.VERIFIED,
            RepairStatus.REJECTED,
            RepairStatus.ROLLED_BACK,
        }

    def is_positive(self) -> bool:
        """Return ``True`` when the status represents successful completion.

        Returns:
            True for APPLIED and VERIFIED.
        """
        return self in {RepairStatus.APPLIED, RepairStatus.VERIFIED}


# ═══════════════════════════════════════════════════════════════════════════
# §3  Frozen dataclasses — RepairEntry
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class RepairEntry:
    """A single repair specification in the atlas.

    A RepairEntry documents a localised section replacement: it names the
    faulty section, characterises the fault, specifies the replacement
    content, and records the expected semantic effect.

    Attributes:
        entry_id: UUID uniquely identifying this repair entry.
        class_id: Problem class this repair belongs to.
        faulty_section_id: Identifier of the section being repaired.
        fault_description: Human-readable description of the fault.
        replacement_content: The replacement section content or reference.
        repair_kind: Strategy used for this repair.
        expected_effect: Expected semantic effect after repair.
        pre_conditions: Conditions that must hold before repair.
        post_conditions: Conditions that must hold after repair.
        trust_delta: Expected change in trust score (may be negative).
        status: Current lifecycle status.
        provenance: Ordered provenance chain entries.
        metadata: Free-form annotation key-value pairs.
    """

    entry_id: str
    class_id: str
    faulty_section_id: str
    fault_description: str
    replacement_content: str
    repair_kind: RepairKind
    expected_effect: str
    pre_conditions: tuple[str, ...]
    post_conditions: tuple[str, ...]
    trust_delta: float
    status: RepairStatus
    provenance: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        faulty_section_id: str,
        fault_description: str,
        replacement_content: str,
        repair_kind: RepairKind,
        expected_effect: str = "",
        pre_conditions: tuple[str, ...] = (),
        post_conditions: tuple[str, ...] = (),
        trust_delta: float = 0.0,
        provenance: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "RepairEntry":
        """Create a new PROPOSED RepairEntry with a generated UUID.

        Args:
            class_id: Problem class identifier.
            faulty_section_id: Section identifier for the faulty code.
            fault_description: Description of the fault.
            replacement_content: Content of the replacement section.
            repair_kind: Repair strategy.
            expected_effect: Expected semantic effect of the repair.
            pre_conditions: Preconditions for safe application.
            post_conditions: Postconditions verifying success.
            trust_delta: Anticipated trust score change.
            provenance: Provenance chain entries.
            metadata: Extra annotations.

        Returns:
            A new PROPOSED RepairEntry.
        """
        return cls(
            entry_id=str(uuid.uuid4()),
            class_id=class_id,
            faulty_section_id=faulty_section_id,
            fault_description=fault_description,
            replacement_content=replacement_content,
            repair_kind=repair_kind,
            expected_effect=expected_effect,
            pre_conditions=pre_conditions,
            post_conditions=post_conditions,
            trust_delta=trust_delta,
            status=RepairStatus.PROPOSED,
            provenance=provenance,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors and transitions
    # ------------------------------------------------------------------

    def metadata_dict(self) -> dict[str, str]:
        """Materialise the metadata tuple as a plain dict.

        Returns:
            Dict of annotation key-value pairs.
        """
        return dict(self.metadata)

    def effective_trust_delta(self) -> float:
        """Return trust_delta adjusted by the repair kind's trust cost.

        Returns:
            Float trust delta.
        """
        return self.trust_delta - self.repair_kind.trust_cost()

    def with_status(self, status: RepairStatus) -> "RepairEntry":
        """Return a copy with the given status.

        Args:
            status: New lifecycle status.

        Returns:
            New RepairEntry with updated status.
        """
        return replace(self, status=status)

    def add_provenance(self, entry: str) -> "RepairEntry":
        """Return a copy with *entry* appended to the provenance chain.

        Args:
            entry: New provenance entry string.

        Returns:
            New RepairEntry with updated provenance.
        """
        return replace(self, provenance=(*self.provenance, entry))

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "entry_id": self.entry_id,
            "class_id": self.class_id,
            "faulty_section_id": self.faulty_section_id,
            "fault_description": self.fault_description,
            "repair_kind": self.repair_kind.value,
            "expected_effect": self.expected_effect,
            "pre_conditions": list(self.pre_conditions),
            "post_conditions": list(self.post_conditions),
            "trust_delta": self.trust_delta,
            "status": self.status.value,
            "provenance": list(self.provenance),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  Frozen dataclasses — TransformationEntry
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TransformationEntry:
    """A single transformation specification in the atlas.

    A TransformationEntry records a section morphism: the source and target
    sections, the kind of transformation, and the correctness obligations.

    Attributes:
        entry_id: UUID uniquely identifying this transformation entry.
        class_id: Problem class this transformation belongs to.
        source_section_id: Identifier of the section being transformed.
        target_section_id: Identifier of the resulting section.
        transformation_kind: Category of transformation.
        morphism_description: Human-readable description of the morphism.
        obligation_ids: IDs of correctness obligations that must be discharged.
        expected_properties: Properties the result must satisfy.
        evidence_channels: Evidence channels used for verification.
        status: Current lifecycle status.
        confidence: Analyst confidence in the transformation [0, 1].
        provenance: Ordered provenance chain entries.
        metadata: Free-form annotation key-value pairs.
    """

    entry_id: str
    class_id: str
    source_section_id: str
    target_section_id: str
    transformation_kind: TransformationKind
    morphism_description: str
    obligation_ids: tuple[str, ...]
    expected_properties: tuple[str, ...]
    evidence_channels: tuple[str, ...]
    status: RepairStatus
    confidence: float
    provenance: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        source_section_id: str,
        target_section_id: str,
        transformation_kind: TransformationKind,
        morphism_description: str = "",
        obligation_ids: tuple[str, ...] = (),
        expected_properties: tuple[str, ...] = (),
        evidence_channels: tuple[str, ...] = (),
        confidence: float = 1.0,
        provenance: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "TransformationEntry":
        """Create a new PROPOSED TransformationEntry with a generated UUID.

        Args:
            class_id: Problem class identifier.
            source_section_id: Source section identifier.
            target_section_id: Target section identifier.
            transformation_kind: Kind of transformation.
            morphism_description: Human-readable description.
            obligation_ids: IDs of correctness obligations to discharge.
            expected_properties: Properties the result must satisfy.
            evidence_channels: Evidence channels to use.
            confidence: Analyst confidence.
            provenance: Provenance chain entries.
            metadata: Extra annotations.

        Returns:
            A new PROPOSED TransformationEntry.
        """
        return cls(
            entry_id=str(uuid.uuid4()),
            class_id=class_id,
            source_section_id=source_section_id,
            target_section_id=target_section_id,
            transformation_kind=transformation_kind,
            morphism_description=morphism_description,
            obligation_ids=obligation_ids,
            expected_properties=expected_properties,
            evidence_channels=evidence_channels
            or (transformation_kind.default_evidence_channel(),),
            status=RepairStatus.PROPOSED,
            confidence=max(0.0, min(1.0, confidence)),
            provenance=provenance,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Accessors and transitions
    # ------------------------------------------------------------------

    def metadata_dict(self) -> dict[str, str]:
        """Materialise metadata as a plain dict.

        Returns:
            Dict of annotation key-value pairs.
        """
        return dict(self.metadata)

    def is_semantics_preserving(self) -> bool:
        """Return ``True`` when the transformation kind guarantees semantic preservation.

        Returns:
            True for REFACTORING and NORMALISATION transformation kinds.
        """
        return self.transformation_kind.is_semantics_preserving()

    def with_status(self, status: RepairStatus) -> "TransformationEntry":
        """Return a copy with the given status.

        Args:
            status: New lifecycle status.

        Returns:
            New TransformationEntry with updated status.
        """
        return replace(self, status=status)

    def with_confidence(self, confidence: float) -> "TransformationEntry":
        """Return a copy with the given confidence score.

        Args:
            confidence: New confidence score in [0, 1].

        Returns:
            New TransformationEntry with updated confidence.
        """
        return replace(self, confidence=max(0.0, min(1.0, confidence)))

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "entry_id": self.entry_id,
            "class_id": self.class_id,
            "source_section_id": self.source_section_id,
            "target_section_id": self.target_section_id,
            "transformation_kind": self.transformation_kind.value,
            "morphism_description": self.morphism_description,
            "obligation_ids": list(self.obligation_ids),
            "expected_properties": list(self.expected_properties),
            "evidence_channels": list(self.evidence_channels),
            "status": self.status.value,
            "confidence": self.confidence,
            "provenance": list(self.provenance),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  TransformationWitness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TransformationWitness:
    """Lightweight sub-witness for a single transformation step.

    TransformationWitness is produced for each TransformationEntry that
    passes verification.  It records which obligations were discharged and
    what evidence was collected.

    Attributes:
        witness_id: UUID for this witness.
        entry_id: The TransformationEntry this witness covers.
        class_id: Problem class.
        transformation_kind: Kind of transformation.
        discharged_obligations: IDs of obligations confirmed discharged.
        evidence_scores: Mapping channel → trust score.
        is_semantics_preserved: Whether semantic preservation was confirmed.
        confidence: Witness confidence [0, 1].
        rationale: Human-readable explanation.
        issued_at: ISO-8601 timestamp.
    """

    witness_id: str
    entry_id: str
    class_id: str
    transformation_kind: TransformationKind
    discharged_obligations: tuple[str, ...]
    evidence_scores: tuple[tuple[str, float], ...]
    is_semantics_preserved: bool
    confidence: float
    rationale: str
    issued_at: str

    @classmethod
    def make(
        cls,
        entry_id: str,
        class_id: str,
        transformation_kind: TransformationKind,
        discharged_obligations: tuple[str, ...] = (),
        evidence_scores: tuple[tuple[str, float], ...] = (),
        is_semantics_preserved: bool = True,
        confidence: float = 1.0,
        rationale: str = "",
    ) -> "TransformationWitness":
        """Create a new TransformationWitness with generated UUID and timestamp.

        Args:
            entry_id: TransformationEntry identifier.
            class_id: Problem class identifier.
            transformation_kind: Kind of transformation.
            discharged_obligations: Obligation IDs confirmed discharged.
            evidence_scores: Channel evidence score pairs.
            is_semantics_preserved: Whether semantics are preserved.
            confidence: Witness confidence.
            rationale: Human-readable explanation.

        Returns:
            A new TransformationWitness.
        """
        import datetime

        return cls(
            witness_id=str(uuid.uuid4()),
            entry_id=entry_id,
            class_id=class_id,
            transformation_kind=transformation_kind,
            discharged_obligations=discharged_obligations,
            evidence_scores=evidence_scores,
            is_semantics_preserved=is_semantics_preserved,
            confidence=max(0.0, min(1.0, confidence)),
            rationale=rationale,
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
        )

    def evidence_dict(self) -> dict[str, float]:
        """Materialise evidence scores as a plain dict.

        Returns:
            Dict mapping channel ID to trust score.
        """
        return dict(self.evidence_scores)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "entry_id": self.entry_id,
            "class_id": self.class_id,
            "transformation_kind": self.transformation_kind.value,
            "discharged_obligations": list(self.discharged_obligations),
            "evidence_scores": list(self.evidence_scores),
            "is_semantics_preserved": self.is_semantics_preserved,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "issued_at": self.issued_at,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §6  RepairProgramTransformationAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    """The output of a feasibility analysis pass over a repair or transformation.

    Attributes:
        report_id: UUID for this report.
        entry_id: Entry that was analysed.
        is_feasible: Whether the repair/transformation is feasible.
        is_safe: Whether application is safe (no regressions expected).
        risk_score: Aggregate risk in [0.0, 1.0].
        blocking_issues: Issues that prevent application.
        warnings: Non-blocking concerns.
        recommendations: Recommended actions to reduce risk.
    """

    report_id: str
    entry_id: str
    is_feasible: bool
    is_safe: bool
    risk_score: float
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    recommendations: tuple[str, ...]

    @classmethod
    def make(
        cls,
        entry_id: str,
        is_feasible: bool,
        is_safe: bool,
        risk_score: float = 0.0,
        blocking_issues: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        recommendations: tuple[str, ...] = (),
    ) -> "FeasibilityReport":
        """Create a new FeasibilityReport with a generated UUID.

        Args:
            entry_id: Entry identifier.
            is_feasible: Feasibility verdict.
            is_safe: Safety verdict.
            risk_score: Aggregate risk score.
            blocking_issues: Issues blocking application.
            warnings: Non-blocking concerns.
            recommendations: Risk-reduction recommendations.

        Returns:
            A new FeasibilityReport.
        """
        return cls(
            report_id=str(uuid.uuid4()),
            entry_id=entry_id,
            is_feasible=is_feasible,
            is_safe=is_safe,
            risk_score=max(0.0, min(1.0, risk_score)),
            blocking_issues=blocking_issues,
            warnings=warnings,
            recommendations=recommendations,
        )

    def can_proceed(self) -> bool:
        """Return ``True`` when the repair may proceed.

        Returns:
            True when is_feasible, is_safe, and no blocking issues exist.
        """
        return self.is_feasible and self.is_safe and len(self.blocking_issues) == 0


class RepairProgramTransformationAnalyzer:
    """Analyses repair and transformation entries for feasibility and safety.

    The analyzer applies a sequence of lightweight checks to determine
    whether a given entry is safe and feasible to apply:

    1. Pre-condition completeness: are pre-conditions specified?
    2. Post-condition completeness: are post-conditions specified?
    3. Trust delta sanity: is the expected trust change realistic?
    4. Semantics preservation: does the transformation kind demand a proof?
    5. Risk aggregation: accumulate risk factors into a composite score.

    The analyzer is stateless.
    """

    # ------------------------------------------------------------------
    # Repair analysis
    # ------------------------------------------------------------------

    def analyse_repair(
        self,
        entry: RepairEntry,
        evidence: dict[str, float] | None = None,
    ) -> FeasibilityReport:
        """Analyse a RepairEntry for feasibility and safety.

        Args:
            entry: The repair entry to analyse.
            evidence: Optional evidence scores for the repair's section.

        Returns:
            A FeasibilityReport capturing the analysis verdict.
        """
        evidence = evidence or {}
        blocking: list[str] = []
        warnings: list[str] = []
        recs: list[str] = []
        risk = entry.repair_kind.trust_cost()

        if entry.repair_kind.requires_specification() and not entry.pre_conditions:
            blocking.append(
                f"RepairKind.{entry.repair_kind.value} requires pre_conditions."
            )
        if not entry.post_conditions:
            warnings.append("No post_conditions specified; verification will be limited.")
            risk += 0.05

        if entry.trust_delta < -0.3:
            warnings.append(
                f"Large negative trust_delta={entry.trust_delta:.2f} suggests a high-risk repair."
            )
            risk += 0.1

        if entry.repair_kind == RepairKind.GENERATIVE and not evidence:
            warnings.append("GENERATIVE repair lacks supporting evidence.")
            recs.append("Provide test_suite or oracle evidence for GENERATIVE repairs.")
            risk += 0.15

        if evidence:
            avg_evidence = sum(evidence.values()) / len(evidence)
            if avg_evidence < 0.5:
                warnings.append(f"Low average evidence quality: {avg_evidence:.2f}")
                risk += 0.1
        else:
            recs.append("Gather evidence before applying this repair.")

        is_feasible = len(blocking) == 0
        is_safe = risk < 0.5 and len(blocking) == 0
        return FeasibilityReport.make(
            entry_id=entry.entry_id,
            is_feasible=is_feasible,
            is_safe=is_safe,
            risk_score=min(1.0, risk),
            blocking_issues=tuple(blocking),
            warnings=tuple(warnings),
            recommendations=tuple(recs),
        )

    # ------------------------------------------------------------------
    # Transformation analysis
    # ------------------------------------------------------------------

    def analyse_transformation(
        self,
        entry: TransformationEntry,
        evidence: dict[str, float] | None = None,
    ) -> FeasibilityReport:
        """Analyse a TransformationEntry for feasibility and safety.

        Args:
            entry: The transformation entry to analyse.
            evidence: Optional evidence scores for the transformation's obligations.

        Returns:
            A FeasibilityReport.
        """
        evidence = evidence or {}
        blocking: list[str] = []
        warnings: list[str] = []
        recs: list[str] = []
        risk = 1.0 - entry.confidence

        if entry.transformation_kind.requires_proof_of_equivalence():
            needed = "formal_proof"
            if needed not in evidence:
                warnings.append(
                    f"{entry.transformation_kind.value} typically requires {needed!r} evidence."
                )
                recs.append(f"Provide {needed!r} evidence to support this transformation.")
                risk += 0.2

        undischarged = set(entry.obligation_ids) - set(evidence.keys())
        if undischarged:
            warnings.append(
                f"{len(undischarged)} obligation(s) not yet discharged: "
                + ", ".join(sorted(undischarged))
            )
            risk += 0.05 * len(undischarged)

        if entry.confidence < 0.5:
            blocking.append(
                f"Confidence too low to proceed: {entry.confidence:.2f} < 0.50"
            )

        is_feasible = len(blocking) == 0
        is_safe = risk < 0.6 and len(blocking) == 0
        return FeasibilityReport.make(
            entry_id=entry.entry_id,
            is_feasible=is_feasible,
            is_safe=is_safe,
            risk_score=min(1.0, risk),
            blocking_issues=tuple(blocking),
            warnings=tuple(warnings),
            recommendations=tuple(recs),
        )

    # ------------------------------------------------------------------
    # Batch analysis
    # ------------------------------------------------------------------

    def analyse_repairs_batch(
        self,
        entries: Sequence[RepairEntry],
        evidence_map: dict[str, dict[str, float]] | None = None,
    ) -> list[FeasibilityReport]:
        """Analyse a batch of repair entries.

        Args:
            entries: Repair entries to analyse.
            evidence_map: Mapping from entry_id to evidence dict.

        Returns:
            List of FeasibilityReport in input order.
        """
        ev_map = evidence_map or {}
        return [self.analyse_repair(e, ev_map.get(e.entry_id)) for e in entries]

    def analyse_transformations_batch(
        self,
        entries: Sequence[TransformationEntry],
        evidence_map: dict[str, dict[str, float]] | None = None,
    ) -> list[FeasibilityReport]:
        """Analyse a batch of transformation entries.

        Args:
            entries: Transformation entries to analyse.
            evidence_map: Mapping from entry_id to evidence dict.

        Returns:
            List of FeasibilityReport in input order.
        """
        ev_map = evidence_map or {}
        return [self.analyse_transformation(e, ev_map.get(e.entry_id)) for e in entries]


# ═══════════════════════════════════════════════════════════════════════════
# §7  RepairProgramTransformationWitness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class RepairProgramTransformationWitness:
    """Top-level certificate for a completed repair or transformation.

    Bundles the RepairEntry or TransformationEntry, the FeasibilityReport,
    the per-step TransformationWitness (if applicable), and the final
    verification verdict into a single immutable record.

    Attributes:
        witness_id: UUID for this top-level witness.
        entry_id: The repair/transformation entry this witness covers.
        entry_kind: ``"repair"`` or ``"transformation"``.
        class_id: Problem class.
        feasibility_report: The FeasibilityReport from the analyzer.
        transformation_witness: Optional per-step TransformationWitness.
        final_status: Terminal RepairStatus.
        overall_confidence: Aggregate confidence score.
        semantic_delta: Net semantic change description.
        issued_at: ISO-8601 timestamp.
        notes: Free-form notes.
    """

    witness_id: str
    entry_id: str
    entry_kind: str
    class_id: str
    feasibility_report: FeasibilityReport
    transformation_witness: TransformationWitness | None
    final_status: RepairStatus
    overall_confidence: float
    semantic_delta: str
    issued_at: str
    notes: str

    @classmethod
    def for_repair(
        cls,
        entry: RepairEntry,
        report: FeasibilityReport,
        notes: str = "",
    ) -> "RepairProgramTransformationWitness":
        """Create a witness for a completed repair.

        Args:
            entry: The RepairEntry that was applied.
            report: The FeasibilityReport from analysis.
            notes: Free-form notes.

        Returns:
            A new RepairProgramTransformationWitness.
        """
        import datetime

        status = (
            RepairStatus.VERIFIED if report.can_proceed() else RepairStatus.REJECTED
        )
        confidence = max(0.0, 1.0 - report.risk_score)
        return cls(
            witness_id=str(uuid.uuid4()),
            entry_id=entry.entry_id,
            entry_kind="repair",
            class_id=entry.class_id,
            feasibility_report=report,
            transformation_witness=None,
            final_status=status,
            overall_confidence=confidence,
            semantic_delta=entry.expected_effect,
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    @classmethod
    def for_transformation(
        cls,
        entry: TransformationEntry,
        report: FeasibilityReport,
        tw: TransformationWitness | None = None,
        notes: str = "",
    ) -> "RepairProgramTransformationWitness":
        """Create a witness for a completed transformation.

        Args:
            entry: The TransformationEntry that was applied.
            report: The FeasibilityReport from analysis.
            tw: Optional per-step TransformationWitness.
            notes: Free-form notes.

        Returns:
            A new RepairProgramTransformationWitness.
        """
        import datetime

        status = (
            RepairStatus.VERIFIED if report.can_proceed() else RepairStatus.REJECTED
        )
        confidence = max(0.0, min(1.0, entry.confidence * (1.0 - report.risk_score)))
        return cls(
            witness_id=str(uuid.uuid4()),
            entry_id=entry.entry_id,
            entry_kind="transformation",
            class_id=entry.class_id,
            feasibility_report=report,
            transformation_witness=tw,
            final_status=status,
            overall_confidence=confidence,
            semantic_delta=entry.morphism_description,
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def is_successful(self) -> bool:
        """Return ``True`` when the repair/transformation succeeded and was verified.

        Returns:
            True when final_status is VERIFIED.
        """
        return self.final_status == RepairStatus.VERIFIED

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "entry_id": self.entry_id,
            "entry_kind": self.entry_kind,
            "class_id": self.class_id,
            "final_status": self.final_status.value,
            "overall_confidence": self.overall_confidence,
            "semantic_delta": self.semantic_delta,
            "issued_at": self.issued_at,
            "notes": self.notes,
            "feasibility": {
                "is_feasible": self.feasibility_report.is_feasible,
                "is_safe": self.feasibility_report.is_safe,
                "risk_score": self.feasibility_report.risk_score,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# §8  RepairProgramTransformationCoordinator
# ═══════════════════════════════════════════════════════════════════════════


class RepairProgramTransformationCoordinator:
    """Orchestrates the full repair and transformation pipeline.

    The coordinator manages:
    - A registry of RepairEntry and TransformationEntry objects.
    - Feasibility analysis via RepairProgramTransformationAnalyzer.
    - Witness production and accumulation.
    - A cumulative audit log of all applied operations.

    Attributes:
        analyzer: The RepairProgramTransformationAnalyzer.
        _repairs: Dict from entry_id to RepairEntry.
        _transformations: Dict from entry_id to TransformationEntry.
        _witnesses: Dict from entry_id to witness.
        _audit_log: Ordered list of (entry_id, action, timestamp) tuples.
    """

    def __init__(
        self,
        analyzer: RepairProgramTransformationAnalyzer | None = None,
    ) -> None:
        self.analyzer = analyzer or RepairProgramTransformationAnalyzer()
        self._repairs: dict[str, RepairEntry] = {}
        self._transformations: dict[str, TransformationEntry] = {}
        self._witnesses: dict[str, RepairProgramTransformationWitness] = {}
        self._audit_log: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_repair(self, entry: RepairEntry) -> RepairEntry:
        """Register a RepairEntry for analysis and application.

        Args:
            entry: The repair entry to register.

        Returns:
            The registered RepairEntry (unchanged).
        """
        self._repairs[entry.entry_id] = entry
        self._log(entry.entry_id, "repair_registered")
        return entry

    def register_transformation(self, entry: TransformationEntry) -> TransformationEntry:
        """Register a TransformationEntry for analysis and application.

        Args:
            entry: The transformation entry to register.

        Returns:
            The registered TransformationEntry (unchanged).
        """
        self._transformations[entry.entry_id] = entry
        self._log(entry.entry_id, "transformation_registered")
        return entry

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def apply_repair(
        self,
        entry_id: str,
        evidence: dict[str, float] | None = None,
    ) -> RepairProgramTransformationWitness:
        """Analyse and apply a registered repair, producing a witness.

        Args:
            entry_id: The entry_id of the RepairEntry to apply.
            evidence: Optional evidence scores.

        Returns:
            A RepairProgramTransformationWitness.

        Raises:
            KeyError: If entry_id is not registered.
        """
        entry = self._repairs[entry_id]
        report = self.analyzer.analyse_repair(entry, evidence)
        if report.can_proceed():
            entry = entry.with_status(RepairStatus.VERIFIED)
        else:
            entry = entry.with_status(RepairStatus.REJECTED)
        self._repairs[entry_id] = entry
        witness = RepairProgramTransformationWitness.for_repair(entry, report)
        self._witnesses[entry_id] = witness
        self._log(entry_id, f"repair_{entry.status.value.lower()}")
        return witness

    def apply_transformation(
        self,
        entry_id: str,
        evidence: dict[str, float] | None = None,
    ) -> RepairProgramTransformationWitness:
        """Analyse and apply a registered transformation, producing a witness.

        Args:
            entry_id: The entry_id of the TransformationEntry to apply.
            evidence: Optional evidence scores.

        Returns:
            A RepairProgramTransformationWitness.

        Raises:
            KeyError: If entry_id is not registered.
        """
        entry = self._transformations[entry_id]
        report = self.analyzer.analyse_transformation(entry, evidence)

        tw: TransformationWitness | None = None
        if report.can_proceed():
            entry = entry.with_status(RepairStatus.VERIFIED)
            tw = TransformationWitness.make(
                entry_id=entry.entry_id,
                class_id=entry.class_id,
                transformation_kind=entry.transformation_kind,
                discharged_obligations=entry.obligation_ids,
                evidence_scores=tuple((evidence or {}).items()),
                is_semantics_preserved=entry.is_semantics_preserving(),
                confidence=entry.confidence,
                rationale=f"Transformation {entry.transformation_kind.value} verified.",
            )
        else:
            entry = entry.with_status(RepairStatus.REJECTED)

        self._transformations[entry_id] = entry
        witness = RepairProgramTransformationWitness.for_transformation(entry, report, tw)
        self._witnesses[entry_id] = witness
        self._log(entry_id, f"transformation_{entry.status.value.lower()}")
        return witness

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_witness(self, entry_id: str) -> RepairProgramTransformationWitness | None:
        """Return the witness for a given entry, if available.

        Args:
            entry_id: The entry identifier.

        Returns:
            The RepairProgramTransformationWitness or None.
        """
        return self._witnesses.get(entry_id)

    def all_witnesses(self) -> list[RepairProgramTransformationWitness]:
        """Return all completed witnesses.

        Returns:
            List of RepairProgramTransformationWitness.
        """
        return list(self._witnesses.values())

    def success_rate(self) -> float:
        """Return fraction of entries that were successfully verified.

        Returns:
            Float in [0.0, 1.0].
        """
        if not self._witnesses:
            return 0.0
        n = sum(1 for w in self._witnesses.values() if w.is_successful())
        return n / len(self._witnesses)

    def audit_log(self) -> list[tuple[str, str, str]]:
        """Return a copy of the audit log.

        Returns:
            List of (entry_id, action, timestamp) tuples.
        """
        return list(self._audit_log)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, entry_id: str, action: str) -> None:
        import datetime

        self._audit_log.append(
            (entry_id, action, datetime.datetime.utcnow().isoformat() + "Z")
        )


# ═══════════════════════════════════════════════════════════════════════════
# §9  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def quick_repair(
    class_id: str,
    faulty_section_id: str,
    fault_description: str,
    replacement_content: str,
    repair_kind: RepairKind = RepairKind.SPLICE,
    evidence: dict[str, float] | None = None,
) -> RepairProgramTransformationWitness:
    """Create, register, and apply a repair in one call.

    Args:
        class_id: Problem class identifier.
        faulty_section_id: Section identifier for the faulty code.
        fault_description: Human-readable description of the fault.
        replacement_content: Replacement section content.
        repair_kind: Repair strategy to use.
        evidence: Optional evidence scores.

    Returns:
        A RepairProgramTransformationWitness.
    """
    coord = RepairProgramTransformationCoordinator()
    entry = RepairEntry.make(
        class_id=class_id,
        faulty_section_id=faulty_section_id,
        fault_description=fault_description,
        replacement_content=replacement_content,
        repair_kind=repair_kind,
    )
    coord.register_repair(entry)
    return coord.apply_repair(entry.entry_id, evidence)


def quick_transform(
    class_id: str,
    source_section_id: str,
    target_section_id: str,
    transformation_kind: TransformationKind,
    evidence: dict[str, float] | None = None,
) -> RepairProgramTransformationWitness:
    """Create, register, and apply a transformation in one call.

    Args:
        class_id: Problem class identifier.
        source_section_id: Source section identifier.
        target_section_id: Target section identifier.
        transformation_kind: Kind of transformation to apply.
        evidence: Optional evidence scores.

    Returns:
        A RepairProgramTransformationWitness.
    """
    coord = RepairProgramTransformationCoordinator()
    entry = TransformationEntry.make(
        class_id=class_id,
        source_section_id=source_section_id,
        target_section_id=target_section_id,
        transformation_kind=transformation_kind,
    )
    coord.register_transformation(entry)
    return coord.apply_transformation(entry.entry_id, evidence)


def get_all_repair_kinds() -> list[RepairKind]:
    """Return all RepairKind values in declaration order.

    Returns:
        List of all RepairKind members.
    """
    return list(RepairKind)


def get_all_transformation_kinds() -> list[TransformationKind]:
    """Return all TransformationKind values in declaration order.

    Returns:
        List of all TransformationKind members.
    """
    return list(TransformationKind)




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
    "RepairKind",
    "RepairStatus",
    "TransformationKind",
    # Frozen dataclasses
    "FeasibilityReport",
    "RepairEntry",
    "RepairProgramTransformationWitness",
    "TransformationEntry",
    "TransformationWitness",
    # Classes
    "RepairProgramTransformationAnalyzer",
    "RepairProgramTransformationCoordinator",
    # Functions
    "get_all_repair_kinds",
    "get_all_transformation_kinds",
    "quick_repair",
    "quick_transform",
    # Type aliases
    "EntryId",
    "JsonDict",
    "SemanticEffect",
    "SectionId",
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
    """Minimal self-test: create a repair and a transformation and verify them."""
    # Repair smoke
    rw = quick_repair(
        class_id="BUG_FIX",
        faulty_section_id="section::off_by_one",
        fault_description="Index calculation is off by one in loop exit condition.",
        replacement_content="return items[len(items) - 1]",
        repair_kind=RepairKind.PATCH,
        evidence={"test_suite": 0.93, "formal_proof": 0.88},
    )
    assert rw.is_successful(), f"Repair should succeed, got {rw.final_status}"
    d = rw.to_dict()
    assert d["entry_kind"] == "repair"

    # Transformation smoke
    tw = quick_transform(
        class_id="REFACTOR",
        source_section_id="section::inline_sort",
        target_section_id="section::timsort",
        transformation_kind=TransformationKind.REFACTORING,
        evidence={"formal_proof": 0.97},
    )
    d2 = tw.to_dict()
    assert d2["entry_kind"] == "transformation"
    assert tw.overall_confidence >= 0.0
    print(
        f"[smoke] repair={rw.final_status.value} "
        f"transform={tw.final_status.value}"
    )


if __name__ == "__main__":
    _smoke()
