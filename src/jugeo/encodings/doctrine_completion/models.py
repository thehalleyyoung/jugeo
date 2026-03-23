"""
Models for the doctrine_completion encoding package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It defines the core data models used throughout the doctrine_completion package,
including statement types, evidence representations, completeness checks, gap
descriptors, and supporting structures for grounding verification.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "ClaimType",
    "StatementStatus",
    "EvidenceKind",
    "GapSeverity",
    "DoctrineStatement",
    "ImplementationEvidence",
    "CompletenessCheck",
    "DoctrineGap",
    "DoctrineCompletionReport",
    "ClaimGroundingMap",
    "EvidenceRequirement",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ClaimType(str, Enum):
    """Taxonomy of claim types appearing in the doctrine.

    STRUCTURAL — claims about structural relationships between components.
    BEHAVIORAL — claims about the dynamic behaviour of the system.
    RELATIONAL — claims about inter-entity relations in the model.
    RESOURCE  — claims about resource usage and capacity.
    SEMANTIC  — claims about the meaning or interpretation of constructs.
    """

    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    RELATIONAL = "relational"
    RESOURCE = "resource"
    SEMANTIC = "semantic"


class StatementStatus(str, Enum):
    """Lifecycle status of a doctrine statement with respect to grounding.

    UNGROUNDED — no evidence has been collected for this statement.
    PARTIAL    — some required evidence is present but not all.
    COMPLETE   — all required evidence kinds are present with sufficient confidence.
    VERIFIED   — the statement has been formally or empirically verified.
    """

    UNGROUNDED = "ungrounded"
    PARTIAL = "partial"
    COMPLETE = "complete"
    VERIFIED = "verified"


class EvidenceKind(str, Enum):
    """Kinds of implementation evidence that can ground doctrine statements.

    CODE         — direct code artefact (source file, module, class).
    TEST         — automated test or test suite.
    RUNTIME      — runtime observation, trace or log.
    PROOF        — formal proof or mechanised verification certificate.
    ORACLE       — oracle-based or property-based test result.
    BENCHMARK    — performance measurement or benchmark result.
    HUMAN_REVIEW — signed-off human review artefact.

    Note: COPILOT_REVIEW is defined separately in implementation_evidence.py
    as an extended variant of this enum.
    """

    CODE = "code"
    TEST = "test"
    RUNTIME = "runtime"
    PROOF = "proof"
    ORACLE = "oracle"
    BENCHMARK = "benchmark"
    HUMAN_REVIEW = "human_review"


class GapSeverity(str, Enum):
    """Severity level of an evidence gap in the doctrine.

    MINOR    — low-impact gap; system still largely functional.
    MODERATE — noticeable impact; some claims remain partially grounded.
    CRITICAL — important claims lack evidence; significant risk.
    BLOCKING — implementation cannot proceed until gap is resolved.
    """

    MINOR = "minor"
    MODERATE = "moderate"
    CRITICAL = "critical"
    BLOCKING = "blocking"


# ---------------------------------------------------------------------------
# DoctrineStatement
# ---------------------------------------------------------------------------


@dataclass
class DoctrineStatement:
    """A single statement within the implementation-complete thesis doctrine.

    Each DoctrineStatement represents one claim in the Ch37 doctrine that
    requires concrete implementation evidence for grounding.  The statement
    carries its own evidence requirements and status so that grounding
    progress can be tracked per-claim.

    Attributes:
        statement_id: Unique identifier generated with uuid.uuid4().
        claim_text: Human-readable text of the claim.
        claim_type: Categorical type from ClaimType enum.
        coordinate_key: Spatial/logical coordinate in the theory geometry.
        required_evidence_kinds: Ordered list of evidence kinds needed.
        status: Current grounding status (StatementStatus).
        created_at: Unix timestamp of statement creation.
        last_checked: Unix timestamp of the most recent completeness check.
        metadata: Arbitrary key-value pairs for extensions.
    """

    statement_id: str
    claim_text: str
    claim_type: ClaimType
    coordinate_key: str
    required_evidence_kinds: list[EvidenceKind]
    status: StatementStatus
    created_at: float
    last_checked: float
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        claim_text: str,
        claim_type: ClaimType,
        coordinate_key: str,
        required_evidence_kinds: list[EvidenceKind],
        metadata: Optional[dict[str, Any]] = None,
    ) -> DoctrineStatement:
        """Factory method that auto-assigns a UUID and current timestamps.

        Args:
            claim_text: Human-readable claim text.
            claim_type: The ClaimType category.
            coordinate_key: Geometry coordinate string.
            required_evidence_kinds: Kinds of evidence required.
            metadata: Optional extra metadata.

        Returns:
            A new DoctrineStatement with generated ID and current timestamp.
        """
        now = time.time()
        return cls(
            statement_id=str(uuid.uuid4()),
            claim_text=claim_text,
            claim_type=claim_type,
            coordinate_key=coordinate_key,
            required_evidence_kinds=list(required_evidence_kinds),
            status=StatementStatus.UNGROUNDED,
            created_at=now,
            last_checked=now,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Completeness helpers
    # ------------------------------------------------------------------

    def check_completeness(self, available_kinds: list[EvidenceKind]) -> StatementStatus:
        """Determine the completeness status given currently available evidence kinds.

        Compares the set of required evidence kinds against the available
        kinds.  Returns COMPLETE when every required kind is satisfied,
        PARTIAL when at least one is, and UNGROUNDED otherwise.

        Args:
            available_kinds: Evidence kinds currently collected for this statement.

        Returns:
            The appropriate StatementStatus value.
        """
        required = set(self.required_evidence_kinds)
        available = set(available_kinds)
        satisfied = required & available
        if not required:
            # No evidence needed — trivially complete
            return StatementStatus.COMPLETE
        if satisfied == required:
            return StatementStatus.COMPLETE
        if len(satisfied) > 0:
            return StatementStatus.PARTIAL
        return StatementStatus.UNGROUNDED

    def get_gaps(self, available_kinds: list[EvidenceKind]) -> list[EvidenceKind]:
        """Return the list of evidence kinds that are still missing.

        Args:
            available_kinds: Evidence kinds currently collected.

        Returns:
            List of EvidenceKind values that are required but absent.
        """
        available_set = set(available_kinds)
        return [k for k in self.required_evidence_kinds if k not in available_set]

    def mark_complete(self, evidence_ids: list[str]) -> None:
        """Mark this statement as COMPLETE and record evidence IDs.

        Updates the status to COMPLETE, refreshes last_checked, and
        stores the supplied evidence IDs in the metadata for traceability.

        Args:
            evidence_ids: IDs of the evidence items that satisfy the statement.
        """
        self.status = StatementStatus.COMPLETE
        self.last_checked = time.time()
        self.metadata["evidence_ids"] = list(evidence_ids)
        self.metadata["completed_at"] = self.last_checked

    def mark_partial(self, available_kinds: list[EvidenceKind]) -> None:
        """Mark this statement as PARTIAL given currently available kinds.

        Updates status to PARTIAL and records which kinds are still missing.

        Args:
            available_kinds: Evidence kinds present so far.
        """
        self.status = StatementStatus.PARTIAL
        self.last_checked = time.time()
        gaps = self.get_gaps(available_kinds)
        self.metadata["partial_since"] = self.last_checked
        self.metadata["remaining_gaps"] = [k.value for k in gaps]

    def to_proof_obligation(self) -> dict[str, Any]:
        """Serialise this statement as a proof-obligation record.

        The proof-obligation format is used when passing statements to
        external verification tools.  It includes the coordinate key,
        required evidence kinds, and the current status.

        Returns:
            A dictionary representing the proof obligation.
        """
        return {
            "obligation_id": str(uuid.uuid4()),
            "statement_id": self.statement_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type.value,
            "coordinate_key": self.coordinate_key,
            "required_kinds": [k.value for k in self.required_evidence_kinds],
            "status": self.status.value,
            "created_at": self.created_at,
            "generated_at": time.time(),
        }

    def render_tex(self) -> str:
        r"""Return a LaTeX snippet representing this doctrine statement.

        Generates a \begin{doctrinestatement}...\end{doctrinestatement}
        block with the statement ID, claim text, and required evidence.

        Returns:
            A multi-line LaTeX string.
        """
        kinds_tex = ", ".join(k.value for k in self.required_evidence_kinds)
        tex = (
            r"\begin{doctrinestatement}"
            f"\n  \\label{{stmt:{self.statement_id[:8]}}}"
            f"\n  \\claimtype{{{self.claim_type.value}}}"
            f"\n  \\coordinate{{{self.coordinate_key}}}"
            f"\n  {self.claim_text}"
            f"\n  \\requiredevidence{{{kinds_tex}}}"
            f"\n  \\status{{{self.status.value}}}"
            "\n" + r"\end{doctrinestatement}"
        )
        return tex

    def to_json(self) -> str:
        """Serialise this statement to a JSON string.

        Returns:
            A JSON-encoded string of the statement's fields.
        """
        data = {
            "statement_id": self.statement_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type.value,
            "coordinate_key": self.coordinate_key,
            "required_evidence_kinds": [k.value for k in self.required_evidence_kinds],
            "status": self.status.value,
            "created_at": self.created_at,
            "last_checked": self.last_checked,
            "metadata": self.metadata,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> DoctrineStatement:
        """Deserialise a DoctrineStatement from a JSON string.

        Args:
            data: JSON string previously produced by to_json().

        Returns:
            A reconstructed DoctrineStatement instance.
        """
        obj = json.loads(data)
        return cls(
            statement_id=obj["statement_id"],
            claim_text=obj["claim_text"],
            claim_type=ClaimType(obj["claim_type"]),
            coordinate_key=obj["coordinate_key"],
            required_evidence_kinds=[EvidenceKind(k) for k in obj["required_evidence_kinds"]],
            status=StatementStatus(obj["status"]),
            created_at=obj["created_at"],
            last_checked=obj["last_checked"],
            metadata=obj.get("metadata", {}),
        )

    def summarize(self) -> str:
        """Return a human-readable one-line summary of this statement.

        Includes the statement ID prefix, claim type, status, and the
        first 60 characters of the claim text.

        Returns:
            A concise summary string.
        """
        snippet = self.claim_text[:60].replace("\n", " ")
        return (
            f"[{self.statement_id[:8]}] {self.claim_type.value.upper()} "
            f"| {self.status.value} | \"{snippet}...\""
        )


# ---------------------------------------------------------------------------
# ImplementationEvidence
# ---------------------------------------------------------------------------


@dataclass
class ImplementationEvidence:
    """A single piece of implementation evidence grounding a doctrine statement.

    Evidence items link concrete artefacts (source files, test suites,
    runtime traces, etc.) to doctrine statements.  Each item carries a
    confidence score and a grounding depth to allow quality assessment.

    Attributes:
        evidence_id: Unique identifier (uuid4).
        statement_id: ID of the statement this evidence grounds.
        evidence_kind: The kind of evidence (EvidenceKind).
        artifact_ref: Opaque reference to the artefact (path, URL, hash).
        confidence: Confidence score in [0.0, 1.0].
        grounding_depth: Depth of the grounding chain (>= 1).
        timestamp: Unix timestamp when the evidence was recorded.
        author: Identifier of the agent or person who collected the evidence.
        copilot_assisted: Whether copilot assisted in collecting this evidence.
        metadata: Extension key-value store.
    """

    evidence_id: str
    statement_id: str
    evidence_kind: EvidenceKind
    artifact_ref: str
    confidence: float
    grounding_depth: int
    timestamp: float
    author: str
    copilot_assisted: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        statement_id: str,
        evidence_kind: EvidenceKind,
        artifact_ref: str,
        confidence: float,
        grounding_depth: int = 1,
        author: str = "system",
        copilot_assisted: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ImplementationEvidence:
        """Factory method with auto-generated ID and timestamp.

        Args:
            statement_id: The statement this evidence grounds.
            evidence_kind: Kind of evidence.
            artifact_ref: Reference to the evidence artefact.
            confidence: Confidence in [0.0, 1.0].
            grounding_depth: Depth of the grounding chain.
            author: Who collected this evidence.
            copilot_assisted: Whether copilot was involved.
            metadata: Optional extra metadata.

        Returns:
            A new ImplementationEvidence with generated ID.
        """
        return cls(
            evidence_id=str(uuid.uuid4()),
            statement_id=statement_id,
            evidence_kind=evidence_kind,
            artifact_ref=artifact_ref,
            confidence=max(0.0, min(1.0, confidence)),
            grounding_depth=max(1, grounding_depth),
            timestamp=time.time(),
            author=author,
            copilot_assisted=copilot_assisted,
            metadata=metadata or {},
        )

    def is_sufficient(self) -> bool:
        """Determine whether this single evidence item is individually sufficient.

        Sufficiency requires confidence >= 0.7 and grounding_depth >= 2.
        These thresholds encode the minimum quality bar from Ch37.

        Returns:
            True if the evidence meets sufficiency thresholds.
        """
        return self.confidence >= 0.7 and self.grounding_depth >= 2

    def confidence_adjusted_by(self, factor: float) -> ImplementationEvidence:
        """Return a copy of this evidence with confidence multiplied by factor.

        The factor is clamped so the resulting confidence stays in [0.0, 1.0].

        Args:
            factor: Multiplicative adjustment factor.

        Returns:
            A new ImplementationEvidence with adjusted confidence.
        """
        import dataclasses
        new_confidence = max(0.0, min(1.0, self.confidence * factor))
        adjusted = dataclasses.replace(self, confidence=new_confidence)
        return adjusted

    def to_evidence_item(self) -> dict[str, Any]:
        """Convert to an EvidenceItem-compatible dictionary.

        The returned dictionary matches the schema expected by
        jugeo.judgments.judgment_terms.EvidenceItem.

        Returns:
            Dictionary with EvidenceItem-compatible fields.
        """
        return {
            "item_id": self.evidence_id,
            "kind": self.evidence_kind.value,
            "artifact_ref": self.artifact_ref,
            "confidence": self.confidence,
            "grounding_depth": self.grounding_depth,
            "timestamp": self.timestamp,
            "author": self.author,
            "copilot_assisted": self.copilot_assisted,
            "statement_id": self.statement_id,
            "metadata": self.metadata,
        }

    def merge_with(self, other: ImplementationEvidence) -> ImplementationEvidence:
        """Merge two evidence items for the same statement into one.

        The merged item uses the maximum confidence and maximum grounding
        depth from the two inputs.  The merged item's author is a
        concatenation, and its timestamp is the later of the two.

        Args:
            other: Another ImplementationEvidence for the same statement.

        Returns:
            A new merged ImplementationEvidence item.
        """
        merged_confidence = max(self.confidence, other.confidence)
        merged_depth = max(self.grounding_depth, other.grounding_depth)
        merged_author = f"{self.author}+{other.author}"
        merged_ts = max(self.timestamp, other.timestamp)
        merged_copilot = self.copilot_assisted or other.copilot_assisted
        merged_meta = {**self.metadata, **other.metadata}
        return ImplementationEvidence(
            evidence_id=str(uuid.uuid4()),
            statement_id=self.statement_id,
            evidence_kind=self.evidence_kind,
            artifact_ref=self.artifact_ref,
            confidence=merged_confidence,
            grounding_depth=merged_depth,
            timestamp=merged_ts,
            author=merged_author,
            copilot_assisted=merged_copilot,
            metadata=merged_meta,
        )

    def validate(self) -> tuple[bool, list[str]]:
        """Validate all fields of this evidence item.

        Checks that required fields are non-empty, confidence is in range,
        and grounding_depth is positive.

        Returns:
            A (is_valid, errors) tuple where errors is a list of strings.
        """
        errors: list[str] = []
        if not self.evidence_id:
            errors.append("evidence_id must not be empty")
        if not self.statement_id:
            errors.append("statement_id must not be empty")
        if not self.artifact_ref:
            errors.append("artifact_ref must not be empty")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.grounding_depth < 1:
            errors.append(f"grounding_depth must be >= 1, got {self.grounding_depth}")
        if not self.author:
            errors.append("author must not be empty")
        return (len(errors) == 0, errors)

    def get_artifact(self) -> dict[str, Any]:
        """Return structured artifact reference information.

        Parses the artifact_ref string into a structured dictionary
        with type, path, and any fragment information.

        Returns:
            Dictionary with artifact type, path, and reference details.
        """
        parts = self.artifact_ref.split("://", 1)
        if len(parts) == 2:
            scheme, path = parts
        else:
            scheme, path = "file", self.artifact_ref
        return {
            "scheme": scheme,
            "path": path,
            "evidence_kind": self.evidence_kind.value,
            "confidence": self.confidence,
            "full_ref": self.artifact_ref,
        }

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of all evidence fields.
        """
        data = {
            "evidence_id": self.evidence_id,
            "statement_id": self.statement_id,
            "evidence_kind": self.evidence_kind.value,
            "artifact_ref": self.artifact_ref,
            "confidence": self.confidence,
            "grounding_depth": self.grounding_depth,
            "timestamp": self.timestamp,
            "author": self.author,
            "copilot_assisted": self.copilot_assisted,
            "metadata": self.metadata,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> ImplementationEvidence:
        """Deserialise from a JSON string.

        Args:
            data: JSON string previously produced by to_json().

        Returns:
            A reconstructed ImplementationEvidence instance.
        """
        obj = json.loads(data)
        return cls(
            evidence_id=obj["evidence_id"],
            statement_id=obj["statement_id"],
            evidence_kind=EvidenceKind(obj["evidence_kind"]),
            artifact_ref=obj["artifact_ref"],
            confidence=obj["confidence"],
            grounding_depth=obj["grounding_depth"],
            timestamp=obj["timestamp"],
            author=obj["author"],
            copilot_assisted=obj.get("copilot_assisted", False),
            metadata=obj.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# CompletenessCheck
# ---------------------------------------------------------------------------


@dataclass
class CompletenessCheck:
    """Result of a doctrine completeness check at a point in time.

    CompletenessCheck records the aggregate outcome of evaluating one or
    more DoctrineStatements against available evidence.  It provides
    counts, gap lists, and recommendations.

    Attributes:
        check_id: Unique identifier (uuid4).
        timestamp: When this check was performed.
        total_statements: Total number of statements evaluated.
        complete_count: Number of COMPLETE statements.
        partial_count: Number of PARTIAL statements.
        ungrounded_count: Number of UNGROUNDED statements.
        gap_list: List of statement IDs that have gaps.
        coverage_score: Fraction of statements that are COMPLETE.
        critical_gaps: Statement IDs with CRITICAL or BLOCKING gaps.
        recommendations: Actionable recommendations for resolving gaps.
    """

    check_id: str
    timestamp: float
    total_statements: int
    complete_count: int
    partial_count: int
    ungrounded_count: int
    gap_list: list[str]
    coverage_score: float
    critical_gaps: list[str]
    recommendations: list[str]

    @classmethod
    def create(
        cls,
        total_statements: int,
        complete_count: int,
        partial_count: int,
        ungrounded_count: int,
        gap_list: Optional[list[str]] = None,
        critical_gaps: Optional[list[str]] = None,
        recommendations: Optional[list[str]] = None,
    ) -> CompletenessCheck:
        """Factory method with auto-generated ID, timestamp, and coverage.

        Args:
            total_statements: Total number of statements evaluated.
            complete_count: Number complete.
            partial_count: Number partial.
            ungrounded_count: Number ungrounded.
            gap_list: Statement IDs with gaps.
            critical_gaps: Statement IDs with critical/blocking gaps.
            recommendations: List of recommendations.

        Returns:
            A new CompletenessCheck instance.
        """
        coverage = complete_count / total_statements if total_statements > 0 else 0.0
        return cls(
            check_id=str(uuid.uuid4()),
            timestamp=time.time(),
            total_statements=total_statements,
            complete_count=complete_count,
            partial_count=partial_count,
            ungrounded_count=ungrounded_count,
            gap_list=gap_list or [],
            coverage_score=coverage,
            critical_gaps=critical_gaps or [],
            recommendations=recommendations or [],
        )

    def is_passing(self, threshold: float = 0.8) -> bool:
        """Determine if this check passes the coverage threshold.

        Args:
            threshold: Minimum coverage score to pass (default 0.8).

        Returns:
            True if coverage_score >= threshold.
        """
        return self.coverage_score >= threshold

    def get_critical_gaps(self) -> list[str]:
        """Return the list of critical gap statement IDs.

        Returns:
            A copy of the critical_gaps list.
        """
        return list(self.critical_gaps)

    def compute_coverage(self) -> float:
        """Recompute coverage as complete_count / total_statements.

        Returns:
            Coverage fraction in [0.0, 1.0].  Returns 0.0 if total is zero.
        """
        if self.total_statements == 0:
            return 0.0
        return self.complete_count / self.total_statements

    def prioritize_gaps(self) -> list[str]:
        """Return gaps in priority order: critical first, then remaining.

        Critical gaps appear first, followed by other gaps not already
        in the critical list.

        Returns:
            Ordered list of statement IDs.
        """
        seen: set[str] = set()
        ordered: list[str] = []
        for gid in self.critical_gaps:
            if gid not in seen:
                ordered.append(gid)
                seen.add(gid)
        for gid in self.gap_list:
            if gid not in seen:
                ordered.append(gid)
                seen.add(gid)
        return ordered

    def to_report(self) -> dict[str, Any]:
        """Serialise this check as a report dictionary.

        Returns:
            Dictionary suitable for inclusion in a larger report.
        """
        return {
            "check_id": self.check_id,
            "timestamp": self.timestamp,
            "total_statements": self.total_statements,
            "complete_count": self.complete_count,
            "partial_count": self.partial_count,
            "ungrounded_count": self.ungrounded_count,
            "coverage_score": self.coverage_score,
            "gap_count": len(self.gap_list),
            "critical_gap_count": len(self.critical_gaps),
            "is_passing": self.is_passing(),
            "recommendations": self.recommendations,
        }

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded representation of this check.
        """
        data = {
            "check_id": self.check_id,
            "timestamp": self.timestamp,
            "total_statements": self.total_statements,
            "complete_count": self.complete_count,
            "partial_count": self.partial_count,
            "ungrounded_count": self.ungrounded_count,
            "gap_list": self.gap_list,
            "coverage_score": self.coverage_score,
            "critical_gaps": self.critical_gaps,
            "recommendations": self.recommendations,
        }
        return json.dumps(data, indent=2)

    def diff_with(self, other: CompletenessCheck) -> dict[str, Any]:
        """Compute the difference between two completeness checks.

        Useful for tracking progress over time.

        Args:
            other: A later CompletenessCheck to compare against.

        Returns:
            Dictionary describing the delta in each metric.
        """
        return {
            "coverage_delta": other.coverage_score - self.coverage_score,
            "complete_delta": other.complete_count - self.complete_count,
            "partial_delta": other.partial_count - self.partial_count,
            "ungrounded_delta": other.ungrounded_count - self.ungrounded_count,
            "gap_count_delta": len(other.gap_list) - len(self.gap_list),
            "critical_gap_delta": len(other.critical_gaps) - len(self.critical_gaps),
            "new_gaps": [g for g in other.gap_list if g not in self.gap_list],
            "resolved_gaps": [g for g in self.gap_list if g not in other.gap_list],
        }

    def summarize(self) -> str:
        """Return a one-line human-readable summary.

        Returns:
            Concise summary including check_id prefix and key metrics.
        """
        pct = self.coverage_score * 100
        return (
            f"[{self.check_id[:8]}] coverage={pct:.1f}% "
            f"({self.complete_count}/{self.total_statements} complete, "
            f"{len(self.critical_gaps)} critical gaps)"
        )


# ---------------------------------------------------------------------------
# DoctrineGap
# ---------------------------------------------------------------------------


@dataclass
class DoctrineGap:
    """Representation of an evidence gap in the doctrine.

    A DoctrineGap records which evidence kinds are missing for a given
    statement, together with severity, resolution advice, and assignment.

    Attributes:
        gap_id: Unique identifier (uuid4).
        statement_id: ID of the statement with the gap.
        missing_evidence_kinds: Evidence kinds that must still be collected.
        gap_severity: Severity from GapSeverity enum.
        description: Human-readable description of the gap.
        suggested_fix: Actionable suggestion for resolving the gap.
        created_at: When this gap was first detected.
        resolved_at: When this gap was resolved, or None.
        assigned_to: Agent ID to whom this gap is assigned, or None.
    """

    gap_id: str
    statement_id: str
    missing_evidence_kinds: list[EvidenceKind]
    gap_severity: GapSeverity
    description: str
    suggested_fix: str
    created_at: float
    resolved_at: Optional[float]
    assigned_to: Optional[str]

    @classmethod
    def create(
        cls,
        statement_id: str,
        missing_kinds: list[EvidenceKind],
        severity: GapSeverity,
        description: str = "",
        suggested_fix: str = "",
        assigned_to: Optional[str] = None,
    ) -> DoctrineGap:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            statement_id: Statement that has the gap.
            missing_kinds: Missing evidence kinds.
            severity: Gap severity level.
            description: Human-readable description.
            suggested_fix: Actionable suggestion.
            assigned_to: Optional agent assignment.

        Returns:
            A new DoctrineGap instance.
        """
        return cls(
            gap_id=str(uuid.uuid4()),
            statement_id=statement_id,
            missing_evidence_kinds=list(missing_kinds),
            gap_severity=severity,
            description=description or f"Missing evidence: {[k.value for k in missing_kinds]}",
            suggested_fix=suggested_fix or "Collect the required evidence kinds listed.",
            created_at=time.time(),
            resolved_at=None,
            assigned_to=assigned_to,
        )

    def is_resolved(self) -> bool:
        """Return True if this gap has been resolved.

        A gap is resolved when resolved_at is set to a non-None timestamp.

        Returns:
            Boolean indicating resolution.
        """
        return self.resolved_at is not None

    def resolve(self, evidence_id: str) -> None:
        """Mark this gap as resolved by a given evidence item.

        Sets resolved_at to the current time and records the resolving
        evidence ID in the metadata.

        Args:
            evidence_id: ID of the evidence that resolves this gap.
        """
        self.resolved_at = time.time()
        # Store resolution info; we don't have a formal metadata field,
        # so we attach it via description amendment.
        self.description = (
            f"{self.description} [RESOLVED by evidence={evidence_id}]"
        )

    def escalate(self) -> GapSeverity:
        """Bump the severity up one level and return the new level.

        MINOR -> MODERATE -> CRITICAL -> BLOCKING (stays at BLOCKING).

        Returns:
            The new GapSeverity after escalation.
        """
        order = [GapSeverity.MINOR, GapSeverity.MODERATE, GapSeverity.CRITICAL, GapSeverity.BLOCKING]
        current_idx = order.index(self.gap_severity)
        new_idx = min(current_idx + 1, len(order) - 1)
        self.gap_severity = order[new_idx]
        return self.gap_severity

    def compute_severity_score(self) -> float:
        """Map GapSeverity to a numeric score for priority calculations.

        MINOR=0.25, MODERATE=0.5, CRITICAL=0.75, BLOCKING=1.0.

        Returns:
            A float severity score in (0.0, 1.0].
        """
        scores: dict[GapSeverity, float] = {
            GapSeverity.MINOR: 0.25,
            GapSeverity.MODERATE: 0.50,
            GapSeverity.CRITICAL: 0.75,
            GapSeverity.BLOCKING: 1.00,
        }
        return scores[self.gap_severity]

    def to_obligation(self) -> dict[str, Any]:
        """Convert this gap to an obligation record for tracking systems.

        Returns:
            Dictionary representing this gap as a residual obligation.
        """
        return {
            "obligation_id": self.gap_id,
            "statement_id": self.statement_id,
            "missing_kinds": [k.value for k in self.missing_evidence_kinds],
            "severity": self.gap_severity.value,
            "severity_score": self.compute_severity_score(),
            "description": self.description,
            "suggested_fix": self.suggested_fix,
            "is_resolved": self.is_resolved(),
            "assigned_to": self.assigned_to,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    def assign(self, agent: str) -> None:
        """Assign this gap to an agent for resolution.

        Args:
            agent: Agent identifier string.
        """
        self.assigned_to = agent

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of gap fields.
        """
        data = {
            "gap_id": self.gap_id,
            "statement_id": self.statement_id,
            "missing_evidence_kinds": [k.value for k in self.missing_evidence_kinds],
            "gap_severity": self.gap_severity.value,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "assigned_to": self.assigned_to,
        }
        return json.dumps(data, indent=2)

    def summarize(self) -> str:
        """One-line summary of this gap.

        Returns:
            Concise summary string.
        """
        kinds = ", ".join(k.value for k in self.missing_evidence_kinds)
        resolved_str = "RESOLVED" if self.is_resolved() else "OPEN"
        return (
            f"[GAP {self.gap_id[:8]}] stmt={self.statement_id[:8]} "
            f"severity={self.gap_severity.value} missing=[{kinds}] {resolved_str}"
        )


# ---------------------------------------------------------------------------
# DoctrineCompletionReport
# ---------------------------------------------------------------------------


@dataclass
class DoctrineCompletionReport:
    """Aggregated report summarising doctrine completeness across all checks.

    DoctrineCompletionReport bundles multiple CompletenessCheck results
    into a single artefact suitable for audit trails, dashboards, and
    CI/CD integration.

    Attributes:
        report_id: Unique identifier (uuid4).
        manifest_id: ID of the DoctrineCompletionManifest being evaluated.
        timestamp: When this report was generated.
        checks: List of individual CompletenessCheck results.
        total_gaps: Total gap count across all checks.
        overall_score: Weighted average coverage score.
        status: Human-readable status label.
        summary: Short summary string.
    """

    report_id: str
    manifest_id: str
    timestamp: float
    checks: list[CompletenessCheck]
    total_gaps: int
    overall_score: float
    status: str
    summary: str

    @classmethod
    def create(
        cls,
        manifest_id: str,
        checks: list[CompletenessCheck],
        summary: str = "",
    ) -> DoctrineCompletionReport:
        """Build a report from a list of checks.

        Computes the overall_score and status from the supplied checks.

        Args:
            manifest_id: ID of the associated manifest.
            checks: List of CompletenessCheck results to bundle.
            summary: Optional summary override.

        Returns:
            A new DoctrineCompletionReport.
        """
        now = time.time()
        total_gaps = sum(len(c.gap_list) for c in checks)
        if checks:
            overall_score = sum(c.coverage_score for c in checks) / len(checks)
        else:
            overall_score = 0.0
        if overall_score >= 0.9:
            status = "EXCELLENT"
        elif overall_score >= 0.75:
            status = "ADEQUATE"
        elif overall_score >= 0.5:
            status = "PARTIAL"
        else:
            status = "INSUFFICIENT"
        if not summary:
            summary = (
                f"Doctrine completeness report: {len(checks)} checks, "
                f"overall coverage {overall_score * 100:.1f}%, status={status}"
            )
        return cls(
            report_id=str(uuid.uuid4()),
            manifest_id=manifest_id,
            timestamp=now,
            checks=checks,
            total_gaps=total_gaps,
            overall_score=overall_score,
            status=status,
            summary=summary,
        )

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded report.
        """
        data = {
            "report_id": self.report_id,
            "manifest_id": self.manifest_id,
            "timestamp": self.timestamp,
            "checks": [json.loads(c.to_json()) for c in self.checks],
            "total_gaps": self.total_gaps,
            "overall_score": self.overall_score,
            "status": self.status,
            "summary": self.summary,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> DoctrineCompletionReport:
        """Deserialise from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A reconstructed DoctrineCompletionReport.
        """
        obj = json.loads(data)
        checks = []
        for c in obj.get("checks", []):
            checks.append(CompletenessCheck(
                check_id=c["check_id"],
                timestamp=c["timestamp"],
                total_statements=c["total_statements"],
                complete_count=c["complete_count"],
                partial_count=c["partial_count"],
                ungrounded_count=c["ungrounded_count"],
                gap_list=c["gap_list"],
                coverage_score=c["coverage_score"],
                critical_gaps=c["critical_gaps"],
                recommendations=c["recommendations"],
            ))
        return cls(
            report_id=obj["report_id"],
            manifest_id=obj["manifest_id"],
            timestamp=obj["timestamp"],
            checks=checks,
            total_gaps=obj["total_gaps"],
            overall_score=obj["overall_score"],
            status=obj["status"],
            summary=obj["summary"],
        )

    def get_overall_status(self) -> str:
        """Return the overall status label.

        Returns:
            Status string: EXCELLENT, ADEQUATE, PARTIAL, or INSUFFICIENT.
        """
        return self.status

    def get_all_gaps(self) -> list[str]:
        """Return the deduplicated list of all gap statement IDs.

        Returns:
            Sorted list of statement IDs that have gaps.
        """
        all_gaps: set[str] = set()
        for check in self.checks:
            all_gaps.update(check.gap_list)
        return sorted(all_gaps)

    def summarize(self) -> str:
        """Return the summary string.

        Returns:
            The human-readable summary for this report.
        """
        return (
            f"[REPORT {self.report_id[:8]}] manifest={self.manifest_id[:8]} "
            f"score={self.overall_score * 100:.1f}% status={self.status} "
            f"gaps={self.total_gaps}"
        )


# ---------------------------------------------------------------------------
# ClaimGroundingMap
# ---------------------------------------------------------------------------


@dataclass
class ClaimGroundingMap:
    """Maps claim/statement IDs to their collected evidence IDs.

    Provides a lightweight index for quickly looking up which evidence
    items are associated with each claim.

    Attributes:
        map_id: Unique identifier (uuid4).
        mappings: Dictionary from statement_id to list of evidence_ids.
        created_at: When this map was created.
    """

    map_id: str
    mappings: dict[str, list[str]]
    created_at: float

    @classmethod
    def create(cls) -> ClaimGroundingMap:
        """Create an empty ClaimGroundingMap with a new UUID.

        Returns:
            An empty ClaimGroundingMap.
        """
        return cls(
            map_id=str(uuid.uuid4()),
            mappings={},
            created_at=time.time(),
        )

    def add_mapping(self, claim_id: str, evidence_ids: list[str]) -> None:
        """Add or extend the mapping for a given claim.

        Args:
            claim_id: The statement/claim identifier.
            evidence_ids: List of evidence IDs to associate.
        """
        if claim_id not in self.mappings:
            self.mappings[claim_id] = []
        existing = set(self.mappings[claim_id])
        for eid in evidence_ids:
            if eid not in existing:
                self.mappings[claim_id].append(eid)
                existing.add(eid)

    def get_evidence(self, claim_id: str) -> list[str]:
        """Return evidence IDs associated with a claim.

        Args:
            claim_id: The claim identifier.

        Returns:
            List of evidence IDs, or empty list if not found.
        """
        return list(self.mappings.get(claim_id, []))

    def all_claims(self) -> list[str]:
        """Return all claim IDs in this map.

        Returns:
            Sorted list of claim ID strings.
        """
        return sorted(self.mappings.keys())

    def is_grounded(self, claim_id: str) -> bool:
        """Return True if the claim has at least one evidence ID mapped.

        Args:
            claim_id: The claim identifier.

        Returns:
            True if there is at least one evidence ID for the claim.
        """
        return bool(self.mappings.get(claim_id))

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of the map.
        """
        data = {
            "map_id": self.map_id,
            "mappings": self.mappings,
            "created_at": self.created_at,
        }
        return json.dumps(data, indent=2)

    def coverage_ratio(self) -> float:
        """Compute the fraction of claims that are grounded.

        Returns:
            Float in [0.0, 1.0], or 0.0 if map is empty.
        """
        if not self.mappings:
            return 0.0
        grounded = sum(1 for v in self.mappings.values() if v)
        return grounded / len(self.mappings)


# ---------------------------------------------------------------------------
# EvidenceRequirement
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRequirement:
    """Specifies the evidence requirements for a single doctrine statement.

    EvidenceRequirement is a richer specification than the simple list of
    EvidenceKind values embedded in DoctrineStatement.  It also carries
    minimum confidence and depth thresholds.

    Attributes:
        req_id: Unique identifier (uuid4).
        statement_id: ID of the targeted statement.
        required_kinds: List of EvidenceKind values that must be present.
        minimum_confidence: Minimum acceptable confidence per evidence item.
        minimum_depth: Minimum grounding depth per evidence item.
    """

    req_id: str
    statement_id: str
    required_kinds: list[EvidenceKind]
    minimum_confidence: float
    minimum_depth: int

    @classmethod
    def create(
        cls,
        statement_id: str,
        required_kinds: list[EvidenceKind],
        minimum_confidence: float = 0.7,
        minimum_depth: int = 2,
    ) -> EvidenceRequirement:
        """Factory method with auto-generated ID.

        Args:
            statement_id: Target statement.
            required_kinds: Required evidence kinds.
            minimum_confidence: Confidence threshold.
            minimum_depth: Depth threshold.

        Returns:
            A new EvidenceRequirement.
        """
        return cls(
            req_id=str(uuid.uuid4()),
            statement_id=statement_id,
            required_kinds=list(required_kinds),
            minimum_confidence=minimum_confidence,
            minimum_depth=minimum_depth,
        )

    def is_met_by(self, evidences: list[ImplementationEvidence]) -> bool:
        """Return True if all required kinds are satisfied by the evidences.

        Each required kind must have at least one evidence item with
        confidence >= minimum_confidence and depth >= minimum_depth.

        Args:
            evidences: List of evidence items for the statement.

        Returns:
            True if requirements are fully met.
        """
        satisfied_kinds: set[EvidenceKind] = set()
        for ev in evidences:
            if (
                ev.confidence >= self.minimum_confidence
                and ev.grounding_depth >= self.minimum_depth
            ):
                satisfied_kinds.add(ev.evidence_kind)
        return set(self.required_kinds).issubset(satisfied_kinds)

    def satisfaction_score(self, evidences: list[ImplementationEvidence]) -> float:
        """Compute a [0.0, 1.0] satisfaction score for these evidences.

        Counts the fraction of required_kinds that are satisfied by at
        least one evidence item meeting the confidence and depth thresholds.

        Args:
            evidences: List of evidence items.

        Returns:
            Float satisfaction score.
        """
        if not self.required_kinds:
            return 1.0
        satisfied_kinds: set[EvidenceKind] = set()
        for ev in evidences:
            if (
                ev.confidence >= self.minimum_confidence
                and ev.grounding_depth >= self.minimum_depth
            ):
                satisfied_kinds.add(ev.evidence_kind)
        matched = len(set(self.required_kinds) & satisfied_kinds)
        return matched / len(self.required_kinds)

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded representation.
        """
        data = {
            "req_id": self.req_id,
            "statement_id": self.statement_id,
            "required_kinds": [k.value for k in self.required_kinds],
            "minimum_confidence": self.minimum_confidence,
            "minimum_depth": self.minimum_depth,
        }
        return json.dumps(data, indent=2)
