"""Stable serialization surfaces for JuGeo judgments and sections.

The authoritative semantics for this module come from ``preliminaries/theory2.tex``.
That text treats judgments and sections as the real semantic authorities, while
public certificates and diagnostics are projection surfaces that must remain:

* projection-faithful,
* scope-honest,
* residual-visible, and
* provenance-preserving.

This module keeps those promises by offering two canonical export records:
``JudgmentExport`` for raw local judgments and ``SectionExport`` for coherent
judgment sections.  The exports are intentionally deterministic and friendly to
humans, APIs, and LLM-oriented tooling.  They preserve enough structure for
faithful replay-oriented reporting without granting public projections more
authority than their source judgments.  Proposal channels such as copilot may
appear in evidence or provenance summaries, but they do not gain silent
certificate authority here.

The JSON-oriented ``to_dict()`` methods are the stable serialization surfaces
consumed by other shared JuGeo modules.  They preserve the historical fields
used elsewhere in the repository while extending the record with explicit clause,
residual, fragility, and provenance material demanded by ``theory2.tex``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, TypeAlias, cast

from jugeo.judgments.judgment_terms import JudgmentClause, JudgmentStatus, LocalJudgment
from jugeo.judgments.sections import JudgmentSection


JSONScalar: TypeAlias = str | int | float | bool | None
NormalizedValue: TypeAlias = JSONScalar | tuple["NormalizedValue", ...] | dict[str, "NormalizedValue"]


class ProjectionKind(str, Enum):
    """Stable projection kinds for downstream consumers.

    ``INTERNAL`` remains the least lossy compiled view.  ``PUBLIC`` and
    ``DIAGNOSTIC`` are declared lossy projections, which matters for scope and
    trust honesty.
    """

    INTERNAL = "internal"
    PUBLIC = "public"
    DIAGNOSTIC = "diagnostic"

    @property
    def declares_loss(self) -> bool:
        """Whether the projection must explicitly declare information loss."""

        return self is not ProjectionKind.INTERNAL


@dataclass(frozen=True, slots=True)
class ClauseExport:
    """Publicly serializable clause surface derived from ``JudgmentClause``.

    Theory2 insists that public artifacts remain clause-aware enough to separate
    positively established material from qualified or still-open material.  This
    small record is the unit that lets the larger export objects preserve that
    distinction.
    """

    name: str
    statement: str
    satisfied: bool | None
    evidence_channels: tuple[str, ...]
    obligations: tuple[str, ...]
    qualified: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary in canonical key order."""

        return {
            "name": self.name,
            "statement": self.statement,
            "satisfied": self.satisfied,
            "evidence_channels": list(self.evidence_channels),
            "obligations": list(self.obligations),
            "qualified": self.qualified,
        }


@dataclass(frozen=True, slots=True)
class JudgmentExport:
    """Stable export surface for a local judgment.

    The structure mirrors theory2's canonical judgment and certificate guidance:
    it keeps proposition, status, trust, residuals, obstructions, clause-level
    summaries, and provenance visible while remaining deterministic under stable
    ordering rules.
    """

    projection: ProjectionKind
    coordinate: str
    proposition: str
    status: str
    residual_count: int
    obstruction_count: int
    trust_summary: Mapping[str, Any]
    provenance_length: int
    loss_declared: bool
    evidence_refs: tuple[str, ...]
    residuals: tuple[str, ...]
    obstructions: tuple[str, ...]
    provenance: tuple[str, ...]
    positive_clauses: tuple[str, ...]
    qualified_clauses: tuple[str, ...]
    public_evidence: Mapping[str, Any]
    fragility: tuple[str, ...]
    artifact_summary: Mapping[str, Any]
    clauses: tuple[ClauseExport, ...]

    @property
    def residual_visibility(self) -> bool:
        """Judgment exports always preserve residual visibility."""

        return True

    @property
    def provenance_visible(self) -> bool:
        """Judgment exports always preserve provenance visibility."""

        return True

    def certificate_surface(self) -> dict[str, Any]:
        """Return the certificate-oriented projection described by theory2.

        This surface mirrors the semantic tuple
        ``(scope, claims, residuals, projection, policy, seal)`` and the richer
        certificate tuple carrying positive claims, qualified claims, public
        evidence, residuals, fragility, and provenance.  A pure judgment has no
        certified scope of its own, so scope appears only on ``SectionExport``.
        """

        return {
            "claims_positive": list(self.positive_clauses),
            "claims_qualified": list(self.qualified_clauses),
            "public_evidence": _json_ready_mapping(self.public_evidence),
            "public_residuals": list(self.residuals),
            "fragility": list(self.fragility),
            "provenance": list(self.provenance),
            "loss_declared": self.loss_declared,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the export into a deterministic, JSON-ready mapping."""

        return {
            "projection": self.projection.value,
            "coordinate": self.coordinate,
            "proposition": self.proposition,
            "status": self.status,
            "residual_count": self.residual_count,
            "obstruction_count": self.obstruction_count,
            "trust_summary": _json_ready_mapping(self.trust_summary),
            "provenance_length": self.provenance_length,
            "loss_declared": self.loss_declared,
            "evidence_refs": list(self.evidence_refs),
            "residuals": list(self.residuals),
            "obstructions": list(self.obstructions),
            "provenance": list(self.provenance),
            "positive_clauses": list(self.positive_clauses),
            "qualified_clauses": list(self.qualified_clauses),
            "public_evidence": _json_ready_mapping(self.public_evidence),
            "fragility": list(self.fragility),
            "artifact_summary": _json_ready_mapping(self.artifact_summary),
            "clauses": [clause.to_dict() for clause in self.clauses],
            "residual_visibility": self.residual_visibility,
            "provenance_visible": self.provenance_visible,
            "certificate_surface": self.certificate_surface(),
        }

    # ------------------------------------------------------------------ #
    # Cross-subsystem integration methods
    # ------------------------------------------------------------------ #

    def to_evaluation(self) -> dict[str, Any]:
        """Convert this export to an evaluation design input.

        Uses ``jugeo.evaluation`` subsystem classes (when available)
        to produce a structured evaluation input from this judgment
        export.  The evaluation design captures proposition, trust
        summary, residuals, and clause structure as evaluation clauses.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"evaluation_available"``,
            ``"design_input"``, and ``"clause_count"`` keys.  When the
            evaluation subsystem is available, includes a full
            ``"design"`` key with the serialised evaluation design.
        """
        try:
            from jugeo.evaluation_design.models import EvaluationDesign  # type: ignore[import-untyped]
        except Exception:
            clauses = [
                {
                    "name": clause.name,
                    "statement": clause.statement,
                    "satisfied": clause.satisfied,
                }
                for clause in self.clauses
            ]
            return {
                "evaluation_available": False,
                "design_input": {
                    "coordinate": self.coordinate,
                    "proposition": self.proposition,
                    "status": self.status,
                    "trust_summary": dict(self.trust_summary),
                    "clauses": clauses,
                    "residuals": list(self.residuals),
                    "obstructions": list(self.obstructions),
                },
                "clause_count": len(self.clauses),
            }

        eval_clauses = [
            {
                "name": clause.name,
                "statement": clause.statement,
                "satisfied": clause.satisfied,
                "evidence_channels": list(clause.evidence_channels),
            }
            for clause in self.clauses
        ]
        design = EvaluationDesign.create(
            name=f"eval-{self.coordinate}-{self.proposition[:40]}",
            clauses=eval_clauses,
            criteria=None,
            ablation_plan=None,
            calibration_config=None,
        )
        return {
            "evaluation_available": True,
            "design_input": {
                "coordinate": self.coordinate,
                "proposition": self.proposition,
                "status": self.status,
                "trust_summary": dict(self.trust_summary),
                "clauses": eval_clauses,
                "residuals": list(self.residuals),
                "obstructions": list(self.obstructions),
            },
            "clause_count": len(self.clauses),
            "design": design.to_json() if hasattr(design, "to_json") else str(design),
        }

    def to_benchmark(self) -> dict[str, Any]:
        """Convert this export to a benchmark case.

        Uses ``jugeo.benchmarks`` (when available) to produce a
        structured benchmark case from this judgment export.  The
        benchmark captures the expected verification outcome, trust
        tier, residuals, and obstructions for regression testing.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"benchmark_available"``,
            ``"case"`` keys.  The case contains fields matching
            ``BenchmarkJudgment`` from ``jugeo.benchmarks.models``.
        """
        try:
            from jugeo.benchmarks.models import BenchmarkJudgment  # type: ignore[import-untyped]
        except Exception:
            return {
                "benchmark_available": False,
                "case": {
                    "category": "judgment",
                    "case_id": f"{self.coordinate}:{self.proposition[:40]}",
                    "expected": self.status,
                    "predicted": None,
                    "passed": None,
                    "trust_tier": dict(self.trust_summary),
                    "residuals": list(self.residuals),
                    "obstructions": list(self.obstructions),
                },
            }

        case = BenchmarkJudgment(
            category="judgment",
            case_id=f"{self.coordinate}:{self.proposition[:40]}",
            expected=self.status,
            predicted=None,
            passed=None,
            trust_tier=dict(self.trust_summary),
            residuals=list(self.residuals),
            obstructions=list(self.obstructions),
        )
        return {
            "benchmark_available": True,
            "case": case,
        }

    # ------------------------------------------------------------------ #
    # Sheaf-theoretic enrichments
    # ------------------------------------------------------------------ #

    def to_evaluation_input(self) -> dict[str, Any]:
        """Convert this export to a structured evaluation input.

        Uses ``jugeo.evaluation`` to build a typed evaluation input
        that the evaluation pipeline can score across quality dimensions
        (evidence coverage, trust adequacy, clause satisfaction).

        Returns
        -------
        dict[str, Any]
            Evaluation input with ``"available"``, ``"input"``, and
            ``"dimension_count"`` keys.
        """
        try:
            from jugeo.evaluation import EvaluationInput
        except Exception:
            return {
                "available": False,
                "input": {
                    "coordinate": self.coordinate,
                    "proposition": self.proposition,
                    "status": self.status,
                    "clause_count": len(self.clauses),
                    "residual_count": self.residual_count,
                    "obstruction_count": self.obstruction_count,
                },
                "dimension_count": 4,
            }
        return EvaluationInput.from_export(self.to_dict())

    def to_benchmark_case(self) -> dict[str, Any]:
        """Convert this export to a regression benchmark case.

        Uses ``jugeo.benchmarks.models`` to produce a structured
        benchmark case capturing the expected verification outcome,
        trust tier, and residual/obstruction state for regression
        testing of the judgment pipeline.

        Returns
        -------
        dict[str, Any]
            Benchmark case with ``"available"``, ``"case_id"``,
            ``"expected_status"``, and ``"trust_summary"`` keys.
        """
        try:
            from jugeo.benchmarks.models import BenchmarkCase
        except Exception:
            return {
                "available": False,
                "case_id": f"{self.coordinate}:{self.proposition[:40]}",
                "expected_status": self.status,
                "trust_summary": dict(self.trust_summary),
                "residual_count": self.residual_count,
                "obstruction_count": self.obstruction_count,
            }
        return BenchmarkCase.from_export(self.to_dict())

    def to_api_response(self) -> dict[str, Any]:
        """Convert this export to an API response payload.

        Uses ``jugeo.interfaces.api`` to format the export as a
        structured API response suitable for external consumers.
        The response respects the projection kind: public projections
        strip internal provenance; diagnostic projections include
        extra debugging metadata.

        Returns
        -------
        dict[str, Any]
            API-ready response with ``"status_code"``, ``"body"``,
            and ``"projection"`` keys.
        """
        try:
            from jugeo.interfaces.api import format_judgment_response
        except Exception:
            body = {
                "coordinate": self.coordinate,
                "proposition": self.proposition,
                "status": self.status,
                "trust_summary": dict(self.trust_summary),
                "residual_count": self.residual_count,
                "obstruction_count": self.obstruction_count,
                "loss_declared": self.loss_declared,
                "positive_clauses": list(self.positive_clauses),
                "qualified_clauses": list(self.qualified_clauses),
                "fragility": list(self.fragility),
            }
            return {
                "status_code": 200,
                "body": body,
                "projection": self.projection.value,
                "api_available": False,
            }
        return format_judgment_response(self.to_dict())


@dataclass(frozen=True, slots=True)
class SectionExport(JudgmentExport):
    """Stable export surface for a coherent judgment section.

    Sections are the first objects that carry scope honestly.  The section export
    therefore extends the raw judgment export with support scope, patch identity,
    support labels, support provenance, and section-specific provenance.
    """

    scope: tuple[str, ...]
    patch: str
    support_labels: tuple[str, ...]
    support_provenance: tuple[str, ...]
    section_provenance: tuple[str, ...]

    @property
    def section_provenance_length(self) -> int:
        return len(self.section_provenance)

    @property
    def scope_size(self) -> int:
        return len(self.scope)

    def certificate_surface(self) -> dict[str, Any]:
        payload = JudgmentExport.certificate_surface(self)
        payload.update(
            {
                "certified_scope": list(self.scope),
                "scope_size": self.scope_size,
                "scope_honest": True,
            }
        )
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Serialize the section export in backward-compatible key order."""

        return {
            "projection": self.projection.value,
            "coordinate": self.coordinate,
            "proposition": self.proposition,
            "status": self.status,
            "scope": list(self.scope),
            "residual_count": self.residual_count,
            "obstruction_count": self.obstruction_count,
            "trust_summary": _json_ready_mapping(self.trust_summary),
            "provenance_length": self.provenance_length,
            "loss_declared": self.loss_declared,
            "patch": self.patch,
            "support_labels": list(self.support_labels),
            "support_provenance": list(self.support_provenance),
            "section_provenance": list(self.section_provenance),
            "section_provenance_length": self.section_provenance_length,
            "scope_size": self.scope_size,
            "evidence_refs": list(self.evidence_refs),
            "residuals": list(self.residuals),
            "obstructions": list(self.obstructions),
            "provenance": list(self.provenance),
            "positive_clauses": list(self.positive_clauses),
            "qualified_clauses": list(self.qualified_clauses),
            "public_evidence": _json_ready_mapping(self.public_evidence),
            "fragility": list(self.fragility),
            "artifact_summary": _json_ready_mapping(self.artifact_summary),
            "clauses": [clause.to_dict() for clause in self.clauses],
            "residual_visibility": self.residual_visibility,
            "provenance_visible": self.provenance_visible,
            "certificate_surface": self.certificate_surface(),
        }


ExportRecord = SectionExport


def export_judgment(
    judgment: LocalJudgment,
    *,
    projection: ProjectionKind | str = ProjectionKind.PUBLIC,
) -> JudgmentExport:
    """Export a ``LocalJudgment`` into a stable projection record.

    The export is deterministic and intentionally non-forgetting: open residuals,
    obstruction families, trust structure, and provenance are all preserved in
    JSON-ready form.  Public and diagnostic projections declare their lossiness,
    while internal projections remain explicit about being the least lossy
    compiled view.
    """

    realized_projection = _coerce_projection(projection)
    clauses = _clause_exports(judgment.clauses)
    positive_clauses, qualified_clauses = _classify_clauses(judgment, clauses)
    trust_summary = cast(Mapping[str, Any], _normalize_mapping(judgment.trust_vector))
    public_evidence = _public_evidence_summary(judgment, clauses)
    artifact_summary = _artifact_summary(judgment)
    residuals = _ordered_strings(judgment.obligations)
    obstructions = _ordered_strings(judgment.obstructions)
    provenance = _ordered_strings(judgment.provenance)
    evidence_refs = _ordered_strings(judgment.evidence_refs)
    fragility = _fragility_flags(
        projection=realized_projection,
        status=judgment.status,
        residuals=residuals,
        obstructions=obstructions,
        qualified_clauses=qualified_clauses,
        provenance=provenance,
        trust_summary=trust_summary,
    )
    return JudgmentExport(
        projection=realized_projection,
        coordinate=judgment.coordinate.key,
        proposition=judgment.proposition,
        status=judgment.status.value,
        residual_count=len(residuals),
        obstruction_count=len(obstructions),
        trust_summary=trust_summary,
        provenance_length=len(provenance),
        loss_declared=realized_projection.declares_loss,
        evidence_refs=evidence_refs,
        residuals=residuals,
        obstructions=obstructions,
        provenance=provenance,
        positive_clauses=positive_clauses,
        qualified_clauses=qualified_clauses,
        public_evidence=public_evidence,
        fragility=fragility,
        artifact_summary=artifact_summary,
        clauses=clauses,
    )


def export_section(
    section: JudgmentSection,
    *,
    projection: ProjectionKind | str = ProjectionKind.PUBLIC,
) -> SectionExport:
    """Export a ``JudgmentSection`` into a scope-honest stable record."""

    judgment_export = export_judgment(section.judgment, projection=projection)
    scope = _sorted_strings(section.support.patch_keys)
    support_labels = _sorted_strings(section.support.labels)
    support_provenance = _ordered_strings(section.support.provenance)
    section_provenance = _ordered_strings(section.provenance)
    return SectionExport(
        projection=judgment_export.projection,
        coordinate=section.coordinate.key,
        proposition=judgment_export.proposition,
        status=judgment_export.status,
        residual_count=judgment_export.residual_count,
        obstruction_count=judgment_export.obstruction_count,
        trust_summary=judgment_export.trust_summary,
        provenance_length=judgment_export.provenance_length,
        loss_declared=judgment_export.loss_declared,
        evidence_refs=judgment_export.evidence_refs,
        residuals=judgment_export.residuals,
        obstructions=judgment_export.obstructions,
        provenance=judgment_export.provenance,
        positive_clauses=judgment_export.positive_clauses,
        qualified_clauses=judgment_export.qualified_clauses,
        public_evidence=judgment_export.public_evidence,
        fragility=judgment_export.fragility,
        artifact_summary=judgment_export.artifact_summary,
        clauses=judgment_export.clauses,
        scope=scope,
        patch=section.patch,
        support_labels=support_labels,
        support_provenance=support_provenance,
        section_provenance=section_provenance,
    )


def _coerce_projection(projection: ProjectionKind | str) -> ProjectionKind:
    if isinstance(projection, ProjectionKind):
        return projection
    return ProjectionKind(str(projection))


def _sorted_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted(str(value) for value in values))


def _ordered_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _normalize_mapping(mapping: Mapping[str, Any]) -> dict[str, NormalizedValue]:
    return {
        str(key): _normalize_value(value)
        for key, value in sorted(mapping.items(), key=lambda item: str(item[0]))
    }


def _normalize_value(value: Any) -> NormalizedValue:
    if isinstance(value, Enum):
        label = getattr(value, "label", None)
        if callable(label):
            labeled = label()
            if labeled is None or isinstance(labeled, (str, int, float, bool)):
                return labeled
        if isinstance(value.value, str):
            return value.value
        return value.name.lower()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _normalize_mapping({str(key): item for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        normalized_items = [_normalize_value(item) for item in value]
        return tuple(sorted(normalized_items, key=_sort_key))
    return repr(value)


def _sort_key(value: NormalizedValue) -> str:
    if isinstance(value, dict):
        return repr(tuple((key, _sort_key(item)) for key, item in value.items()))
    if isinstance(value, tuple):
        return repr(tuple(_sort_key(item) for item in value))
    return repr(value)


def _json_ready(value: NormalizedValue) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _json_ready_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_ready(_normalize_value(value)) for key, value in mapping.items()}


def _clause_exports(clauses: tuple[JudgmentClause, ...]) -> tuple[ClauseExport, ...]:
    return tuple(_export_clause(clause) for clause in clauses)


def _export_clause(clause: JudgmentClause) -> ClauseExport:
    obligations = _ordered_strings(clause.obligations)
    qualified = clause.satisfied is not True or bool(obligations)
    return ClauseExport(
        name=clause.name,
        statement=clause.statement,
        satisfied=clause.satisfied,
        evidence_channels=_ordered_strings(clause.evidence_channels),
        obligations=obligations,
        qualified=qualified,
    )


def _classify_clauses(
    judgment: LocalJudgment,
    clauses: tuple[ClauseExport, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if clauses:
        positive = tuple(clause.name for clause in clauses if not clause.qualified)
        qualified = tuple(clause.name for clause in clauses if clause.qualified)
        return positive, qualified
    if judgment.status is JudgmentStatus.SETTLED and not judgment.obligations and not judgment.obstructions:
        return (judgment.proposition,), ()
    return (), (judgment.proposition,)


def _public_evidence_summary(
    judgment: LocalJudgment,
    clauses: tuple[ClauseExport, ...],
) -> dict[str, NormalizedValue]:
    channels = []
    seen_channels: set[str] = set()
    for clause in clauses:
        for channel in clause.evidence_channels:
            if channel not in seen_channels:
                seen_channels.add(channel)
                channels.append(channel)
    return {
        "evidence_refs": tuple(_ordered_strings(judgment.evidence_refs)),
        "evidence_ref_count": len(judgment.evidence_refs),
        "clause_channels": tuple(channels),
        "channel_count": len(channels),
        "clause_count": len(clauses),
    }


def _artifact_summary(judgment: LocalJudgment) -> dict[str, NormalizedValue]:
    keys = _sorted_strings(judgment.artifact.keys())
    return {
        "keys": keys,
        "item_count": len(judgment.artifact),
    }


def _fragility_flags(
    *,
    projection: ProjectionKind,
    status: JudgmentStatus,
    residuals: tuple[str, ...],
    obstructions: tuple[str, ...],
    qualified_clauses: tuple[str, ...],
    provenance: tuple[str, ...],
    trust_summary: Mapping[str, Any],
) -> tuple[str, ...]:
    flags: list[str] = []
    if projection.declares_loss:
        flags.append("lossy-projection")
    if residuals:
        flags.append("residual-open")
    if obstructions:
        flags.append("obstruction-present")
    if qualified_clauses:
        flags.append("qualified-claims")
    if status is not JudgmentStatus.SETTLED:
        flags.append(f"status:{status.value}")
    if not provenance:
        flags.append("thin-provenance")
    if not trust_summary:
        flags.append("trust-unspecified")
    return tuple(flags)


__all__ = [
    "ProjectionKind",
    "ClauseExport",
    "JudgmentExport",
    "SectionExport",
    "ExportRecord",
    "export_judgment",
    "export_section",
]

# copilot: shared-core marker for future LLM orchestration.
