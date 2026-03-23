"""Typed chapter models for JuGeo's semantic-center introduction.

This module is the machine-readable companion to the chapter-1 introduction in
``preliminaries/theory2.tex`` and ``preliminaries/theory2.pdf``.  It keeps the
chapter's worldview explicit in stable Python shapes that are easy for humans,
tests, IDEs, and LLMs to inspect.

Why this file exists
--------------------
The governing blueprint names three chapter-facing classes for this file:

* :class:`IntroductionJuGeoRecord`
* :class:`IntroductionJuGeoScope`
* :class:`IntroductionJuGeoSummary`

At the same time, already-generated package files still expect the older,
chapter-2-flavored names ``JuGeoWorldview``, ``ThesisClaim``,
``ContributionRecord``, and ``ProblemClass``.  The implementation below treats
``IntroductionJuGeoScope``, ``IntroductionJuGeoRecord``, and
``IntroductionJuGeoSummary`` as the canonical chapter-1 surface while
continuing to export the older names as compatibility shims with real behavior.

Design commitments
------------------
* Frozen dataclasses and explicit enums.
* No silent trust promotion: trust boundaries are preserved in data and in
  helper methods.
* Stable JSON-shaped serialisation for later files and tests.
* Seam-friendly adapters into existing JuGeo shared runtime modules such as
  ``jugeo.errors``, ``jugeo.geometry.site``, ``jugeo.judgments.contexts``, and
  ``jugeo.judgments.judgment_terms``.
* Honest compatibility: this file provides usable logic now without pretending
  that the entire future dependency graph has already been generated.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping

from jugeo.errors import FailureClassification, FailureScope, StructuredFailure, raise_with_scope
from jugeo.evidence.trust import TrustAlgebra
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.judgments.contexts import ContextBinding, SemanticContext
from jugeo.judgments.judgment_terms import EvidenceBundle, EvidenceItem, TrustAnnotation, TrustLevel
from jugeo.thesis.semantic_center.manifest import (
    CHAPTER_GOALS,
    CHAPTER_NUMBER,
    CHAPTER_TITLE,
    INTRODUCTION_SOURCE_SECTIONS,
    MAIN_CONTRIBUTIONS,
    MANIFEST_SPEC_PROVENANCE,
    PACKAGE_PATH,
    PART_NUMBER,
    PROBLEM_CLASS_ATLAS,
    WORLDVIEW_COMMITMENTS,
)

__all__ = [
    "ClaimStatus",
    "ContributionKind",
    "ProblemDomain",
    "IntroductionJuGeoScope",
    "IntroductionJuGeoRecord",
    "IntroductionJuGeoSummary",
    "JuGeoWorldview",
    "ThesisClaim",
    "ContributionRecord",
    "ProblemClass",
    "INTRODUCTION_JUGEO_SCOPES",
    "INTRODUCTION_JUGEO_RECORDS",
    "INTRODUCTION_JUGEO_SUMMARY",
    "JUGEO_WORLDVIEW",
    "CONTRIBUTION_RECORDS",
    "PROBLEM_CLASSES",
    "INTRODUCTION_THESIS_CLAIMS",
    "build_introduction_summary",
    "build_jugeo_worldview",
    "build_scope_index",
    "iter_records_for_section",
]

_EMPTY_TEXTS: Final[tuple[str, ...]] = ()
_EMPTY_MAPPING: Final[Mapping[str, Any]] = MappingProxyType({})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_required_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_text_tuple(
    values: Iterable[str] | str | None,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if values is None:
        return ()
    items = (values,) if isinstance(values, str) else tuple(values)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_required_text(item, field_name=field_name)
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_mapping(
    values: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> Mapping[str, Any]:
    if values is None:
        return _EMPTY_MAPPING
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({str(key): values[key] for key in values})


def _keywordize(*text_groups: Iterable[str] | str) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    for group in text_groups:
        values = (group,) if isinstance(group, str) else tuple(group)
        for value in values:
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9+_-]{2,}", value.lower()):
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)
    return tuple(tokens)


def _claim_failure(message: str, *, coordinate: str, metadata: Mapping[str, Any] | None = None) -> StructuredFailure:
    return StructuredFailure(
        message=message,
        scope=FailureScope.CHAPTER,
        classification=FailureClassification.INVALID_VALUE,
        coordinate=coordinate,
        trust_boundary="chapter-introduction",
        metadata=metadata or {},
        recoverable=True,
    )


def _module_coordinate(*components: str) -> Coordinate:
    return Coordinate(components=components, kind=CoordinateKind.REGION)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ClaimStatus(str, Enum):
    """Lifecycle state for a thesis-facing claim."""

    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    OBSTRUCTED = "obstructed"
    RETRACTED = "retracted"

    def is_terminal(self) -> bool:
        return self in {self.VERIFIED, self.OBSTRUCTED, self.RETRACTED}

    def allows_new_evidence(self) -> bool:
        return self is not self.RETRACTED


class ContributionKind(str, Enum):
    """High-level kind of contribution claimed by the introduction."""

    THEORETICAL = "theoretical"
    ALGORITHMIC = "algorithmic"
    FRAMEWORK = "framework"
    EMPIRICAL = "empirical"
    IMPLEMENTATION = "implementation"


class ProblemDomain(str, Enum):
    """Broad JuGeo problem families used throughout the chapter."""

    SEMANTIC_VERIFICATION = "semantic_verification"
    LONG_HORIZON_GENERATION = "long_horizon_generation"
    MIXED_EVIDENCE = "mixed_evidence"
    MATHEMATICAL_IDEATION = "mathematical_ideation"
    TRUST_MANAGEMENT = "trust_management"
    OBSTRUCTION_TRACKING = "obstruction_tracking"


# ---------------------------------------------------------------------------
# Canonical chapter-1 models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntroductionJuGeoScope:
    """A semantically meaningful scope inside the chapter-1 introduction.

    ``IntroductionJuGeoScope`` records where a doctrinal statement lives, which
    problem domains it is responsible for, which nearby modules it constrains,
    and which trust/authority boundary it must not cross.
    """

    scope_id: str
    section_title: str
    semantic_coordinate: Coordinate
    problem_domains: tuple[ProblemDomain, ...]
    trust_boundary: str
    authority_boundary: str
    source_modules: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    semantic_tags: tuple[str, ...] = ()
    ambient_packs: tuple[str, ...] = ("ag", "dtt", "ai")
    provenance: tuple[str, ...] = ()
    maturity_level: str = "chapter-doctrine"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _normalize_required_text(self.scope_id, field_name="scope_id"))
        object.__setattr__(self, "section_title", _normalize_required_text(self.section_title, field_name="section_title"))
        object.__setattr__(self, "trust_boundary", _normalize_required_text(self.trust_boundary, field_name="trust_boundary"))
        object.__setattr__(self, "authority_boundary", _normalize_required_text(self.authority_boundary, field_name="authority_boundary"))
        object.__setattr__(self, "maturity_level", _normalize_required_text(self.maturity_level, field_name="maturity_level"))
        object.__setattr__(self, "source_modules", _normalize_text_tuple(self.source_modules, field_name="source_modules"))
        object.__setattr__(self, "dependencies", _normalize_text_tuple(self.dependencies, field_name="dependencies"))
        object.__setattr__(self, "semantic_tags", _normalize_text_tuple(self.semantic_tags, field_name="semantic_tags"))
        object.__setattr__(self, "ambient_packs", _normalize_text_tuple(self.ambient_packs, field_name="ambient_packs"))
        object.__setattr__(self, "provenance", _normalize_text_tuple(self.provenance, field_name="provenance"))
        if not isinstance(self.semantic_coordinate, Coordinate):
            raise TypeError("semantic_coordinate must be a jugeo.geometry.site.Coordinate")
        if not self.problem_domains:
            raise ValueError("problem_domains must be non-empty")
        for domain in self.problem_domains:
            if not isinstance(domain, ProblemDomain):
                raise TypeError("problem_domains entries must be ProblemDomain members")

    @property
    def coordinate_key(self) -> str:
        return self.semantic_coordinate.key

    def covers_problem_domain(self, domain: ProblemDomain) -> bool:
        return domain in self.problem_domains

    def mentions_module(self, module_path: str) -> bool:
        return module_path in self.source_modules

    def semantic_keywords(self) -> tuple[str, ...]:
        return _keywordize(
            self.scope_id,
            self.section_title,
            self.trust_boundary,
            self.authority_boundary,
            self.source_modules,
            self.dependencies,
            self.semantic_tags,
            (domain.value for domain in self.problem_domains),
        )

    def as_semantic_context(self) -> SemanticContext:
        bindings = (
            ContextBinding(
                name="scope_id",
                value=self.scope_id,
                provenance=self.provenance,
                scope_markers=("chapter-01", "semantic-center"),
            ),
            ContextBinding(
                name="section_title",
                value=self.section_title,
                provenance=self.provenance,
                scope_markers=("chapter-01",),
            ),
            ContextBinding(
                name="authority_boundary",
                value=self.authority_boundary,
                provenance=self.provenance + ("authority-boundary",),
                scope_markers=("authority",),
            ),
            ContextBinding(
                name="trust_boundary",
                value=self.trust_boundary,
                provenance=self.provenance + ("trust-boundary",),
                scope_markers=("trust",),
            ),
        )
        return SemanticContext(
            coordinate=self.semantic_coordinate,
            bindings=bindings,
            assumptions=(f"section:{self.section_title}",),
            ambient_packs=self.ambient_packs,
            trust_boundary=self.trust_boundary,
            dependent_scope=self.semantic_coordinate.path,
            support_labels=frozenset(self.semantic_tags),
            provenance=self.provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "section_title": self.section_title,
            "semantic_coordinate": self.semantic_coordinate.serialize(),
            "coordinate_key": self.coordinate_key,
            "problem_domains": [domain.value for domain in self.problem_domains],
            "trust_boundary": self.trust_boundary,
            "authority_boundary": self.authority_boundary,
            "source_modules": list(self.source_modules),
            "dependencies": list(self.dependencies),
            "semantic_tags": list(self.semantic_tags),
            "ambient_packs": list(self.ambient_packs),
            "provenance": list(self.provenance),
            "maturity_level": self.maturity_level,
        }


@dataclass(frozen=True, slots=True)
class IntroductionJuGeoRecord:
    """Stable record for one doctrine-bearing chapter statement.

    Each record binds a prose statement from the chapter to explicit scope,
    contribution categories, problem-domain coverage, provenance, and module
    surfaces that later generated files can rely on.
    """

    record_id: str
    title: str
    scope: IntroductionJuGeoScope
    thesis_position: str
    semantic_claim: str
    comparative_positioning: str
    worldview_commitments: tuple[str, ...]
    contribution_kinds: tuple[ContributionKind, ...]
    contributions: tuple[str, ...]
    problem_domains: tuple[ProblemDomain, ...]
    problem_classes: tuple[str, ...]
    trust_notes: tuple[str, ...]
    authority_notes: tuple[str, ...]
    related_modules: tuple[str, ...]
    related_symbols: tuple[str, ...]
    citations: tuple[str, ...]
    provenance: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    copilot_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _normalize_required_text(self.record_id, field_name="record_id"))
        object.__setattr__(self, "title", _normalize_required_text(self.title, field_name="title"))
        object.__setattr__(self, "thesis_position", _normalize_required_text(self.thesis_position, field_name="thesis_position"))
        object.__setattr__(self, "semantic_claim", _normalize_required_text(self.semantic_claim, field_name="semantic_claim"))
        object.__setattr__(self, "comparative_positioning", _normalize_required_text(self.comparative_positioning, field_name="comparative_positioning"))
        object.__setattr__(self, "worldview_commitments", _normalize_text_tuple(self.worldview_commitments, field_name="worldview_commitments"))
        object.__setattr__(self, "contributions", _normalize_text_tuple(self.contributions, field_name="contributions"))
        object.__setattr__(self, "problem_classes", _normalize_text_tuple(self.problem_classes, field_name="problem_classes"))
        object.__setattr__(self, "trust_notes", _normalize_text_tuple(self.trust_notes, field_name="trust_notes"))
        object.__setattr__(self, "authority_notes", _normalize_text_tuple(self.authority_notes, field_name="authority_notes"))
        object.__setattr__(self, "related_modules", _normalize_text_tuple(self.related_modules, field_name="related_modules"))
        object.__setattr__(self, "related_symbols", _normalize_text_tuple(self.related_symbols, field_name="related_symbols"))
        object.__setattr__(self, "citations", _normalize_text_tuple(self.citations, field_name="citations"))
        object.__setattr__(self, "copilot_notes", _normalize_text_tuple(self.copilot_notes, field_name="copilot_notes"))
        object.__setattr__(self, "provenance", _normalize_mapping(self.provenance, field_name="provenance"))
        if not isinstance(self.scope, IntroductionJuGeoScope):
            raise TypeError("scope must be an IntroductionJuGeoScope")
        if not self.problem_domains:
            raise ValueError("problem_domains must be non-empty")
        if not self.contribution_kinds:
            raise ValueError("contribution_kinds must be non-empty")
        for kind in self.contribution_kinds:
            if not isinstance(kind, ContributionKind):
                raise TypeError("contribution_kinds entries must be ContributionKind members")
        for domain in self.problem_domains:
            if not isinstance(domain, ProblemDomain):
                raise TypeError("problem_domains entries must be ProblemDomain members")

    @property
    def section_title(self) -> str:
        return self.scope.section_title

    def semantic_keywords(self) -> tuple[str, ...]:
        return _keywordize(
            self.record_id,
            self.title,
            self.section_title,
            self.thesis_position,
            self.semantic_claim,
            self.comparative_positioning,
            self.worldview_commitments,
            self.contributions,
            self.problem_classes,
            self.trust_notes,
            self.authority_notes,
            self.related_modules,
            self.related_symbols,
            self.citations,
        )

    def covers_problem_domain(self, domain: ProblemDomain) -> bool:
        return domain in self.problem_domains or self.scope.covers_problem_domain(domain)

    def as_semantic_context(self) -> SemanticContext:
        base = self.scope.as_semantic_context()
        bindings = base.bindings + (
            ContextBinding(
                name="record_id",
                value=self.record_id,
                provenance=("models.py", "introduction-record"),
                scope_markers=("chapter-01",),
            ),
            ContextBinding(
                name="record_title",
                value=self.title,
                provenance=("models.py", "introduction-record"),
                scope_markers=("chapter-01",),
            ),
            ContextBinding(
                name="semantic_claim",
                value=self.semantic_claim,
                provenance=("theory2.tex", "chapter-01"),
                scope_markers=("semantic-center",),
            ),
        )
        return SemanticContext(
            coordinate=base.coordinate,
            bindings=bindings,
            assumptions=base.assumptions + (f"record:{self.record_id}",),
            ambient_packs=base.ambient_packs,
            trust_boundary=base.trust_boundary,
            dependent_scope=base.dependent_scope,
            support_labels=base.support_labels,
            provenance=base.provenance + ("models.py",),
        )

    def validate(self) -> tuple[StructuredFailure, ...]:
        failures: list[StructuredFailure] = []
        if self.section_title not in INTRODUCTION_SOURCE_SECTIONS and self.section_title != CHAPTER_TITLE:
            failures.append(
                _claim_failure(
                    f"Unknown section title for chapter-1 record: {self.section_title}",
                    coordinate=self.record_id,
                    metadata={"section_title": self.section_title},
                )
            )
        missing_domains = [domain for domain in self.problem_domains if domain not in self.scope.problem_domains]
        if missing_domains:
            failures.append(
                _claim_failure(
                    "Record problem domains must be a subset of the enclosing scope domains",
                    coordinate=self.record_id,
                    metadata={"missing_domains": [domain.value for domain in missing_domains]},
                )
            )
        if not self.related_modules:
            failures.append(
                _claim_failure(
                    "IntroductionJuGeoRecord should reference at least one nearby implementation module",
                    coordinate=self.record_id,
                )
            )
        if not self.citations:
            failures.append(
                _claim_failure(
                    "IntroductionJuGeoRecord should preserve explicit citations",
                    coordinate=self.record_id,
                )
            )
        return tuple(failures)

    def summary_line(self) -> str:
        return f"{self.record_id} [{self.section_title}] {self.title}"

    def copilot_summary(self) -> str:
        wrapped_claim = textwrap.fill(self.semantic_claim, width=78)
        domains = ", ".join(domain.value for domain in self.problem_domains)
        return "\n".join(
            (
                self.summary_line(),
                f"Domains: {domains}",
                f"Claim: {wrapped_claim}",
                f"Trust boundary: {self.scope.trust_boundary}",
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "title": self.title,
            "section_title": self.section_title,
            "scope": self.scope.to_dict(),
            "thesis_position": self.thesis_position,
            "semantic_claim": self.semantic_claim,
            "comparative_positioning": self.comparative_positioning,
            "worldview_commitments": list(self.worldview_commitments),
            "contribution_kinds": [kind.value for kind in self.contribution_kinds],
            "contributions": list(self.contributions),
            "problem_domains": [domain.value for domain in self.problem_domains],
            "problem_classes": list(self.problem_classes),
            "trust_notes": list(self.trust_notes),
            "authority_notes": list(self.authority_notes),
            "related_modules": list(self.related_modules),
            "related_symbols": list(self.related_symbols),
            "citations": list(self.citations),
            "provenance": dict(self.provenance),
            "copilot_notes": list(self.copilot_notes),
        }


@dataclass(frozen=True, slots=True)
class IntroductionJuGeoSummary:
    """Aggregate view of chapter-1 records and their semantic coverage."""

    chapter_number: int
    part_number: int
    chapter_title: str
    package_path: str
    scopes: tuple[IntroductionJuGeoScope, ...]
    records: tuple[IntroductionJuGeoRecord, ...]
    worldview_commitments: tuple[str, ...]
    main_contributions: tuple[str, ...]
    problem_class_atlas: tuple[str, ...]
    chapter_goals: tuple[str, ...]
    provenance: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapter_title", _normalize_required_text(self.chapter_title, field_name="chapter_title"))
        object.__setattr__(self, "package_path", _normalize_required_text(self.package_path, field_name="package_path"))
        object.__setattr__(self, "worldview_commitments", _normalize_text_tuple(self.worldview_commitments, field_name="worldview_commitments"))
        object.__setattr__(self, "main_contributions", _normalize_text_tuple(self.main_contributions, field_name="main_contributions"))
        object.__setattr__(self, "problem_class_atlas", _normalize_text_tuple(self.problem_class_atlas, field_name="problem_class_atlas"))
        object.__setattr__(self, "chapter_goals", _normalize_text_tuple(self.chapter_goals, field_name="chapter_goals"))
        object.__setattr__(self, "provenance", _normalize_mapping(self.provenance, field_name="provenance"))
        if not self.scopes:
            raise ValueError("scopes must be non-empty")
        if not self.records:
            raise ValueError("records must be non-empty")

    def scope_index(self) -> dict[str, IntroductionJuGeoScope]:
        return {scope.scope_id: scope for scope in self.scopes}

    def record_index(self) -> dict[str, IntroductionJuGeoRecord]:
        return {record.record_id: record for record in self.records}

    def records_for_section(self, section_title: str) -> tuple[IntroductionJuGeoRecord, ...]:
        return tuple(record for record in self.records if record.section_title == section_title)

    def records_for_domain(self, domain: ProblemDomain) -> tuple[IntroductionJuGeoRecord, ...]:
        return tuple(record for record in self.records if record.covers_problem_domain(domain))

    def coverage_by_domain(self) -> dict[str, int]:
        return {
            domain.value: len(self.records_for_domain(domain))
            for domain in ProblemDomain
        }

    def semantic_keywords(self) -> tuple[str, ...]:
        return _keywordize(
            self.chapter_title,
            self.package_path,
            self.worldview_commitments,
            self.main_contributions,
            self.problem_class_atlas,
            self.chapter_goals,
            *(record.semantic_keywords() for record in self.records),
        )

    def validate(self) -> tuple[StructuredFailure, ...]:
        failures: list[StructuredFailure] = []
        if self.chapter_number != CHAPTER_NUMBER:
            failures.append(_claim_failure("Chapter number mismatch for introduction summary", coordinate="summary", metadata={"chapter_number": self.chapter_number}))
        if self.part_number != PART_NUMBER:
            failures.append(_claim_failure("Part number mismatch for introduction summary", coordinate="summary", metadata={"part_number": self.part_number}))
        record_sections = {record.section_title for record in self.records}
        missing_sections = [section for section in INTRODUCTION_SOURCE_SECTIONS if section not in record_sections]
        if missing_sections:
            failures.append(
                _claim_failure(
                    "Introduction summary is missing required chapter sections",
                    coordinate="summary",
                    metadata={"missing_sections": missing_sections},
                )
            )
        for record in self.records:
            failures.extend(record.validate())
        return tuple(failures)

    def public_api_snapshot(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "part_number": self.part_number,
            "chapter_title": self.chapter_title,
            "package_path": self.package_path,
            "scope_count": len(self.scopes),
            "record_count": len(self.records),
            "sections": list(INTRODUCTION_SOURCE_SECTIONS),
            "coverage_by_domain": self.coverage_by_domain(),
        }

    def copilot_summary(self) -> str:
        failures = self.validate()
        lines = [
            f"IntroductionJuGeoSummary for chapter {self.chapter_number}: {self.chapter_title}",
            f"Package path: {self.package_path}",
            f"Scopes: {len(self.scopes)} | Records: {len(self.records)} | Failures: {len(failures)}",
        ]
        for section in INTRODUCTION_SOURCE_SECTIONS:
            lines.append(f"- {section}: {len(self.records_for_section(section))} record(s)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "part_number": self.part_number,
            "chapter_title": self.chapter_title,
            "package_path": self.package_path,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "records": [record.to_dict() for record in self.records],
            "worldview_commitments": list(self.worldview_commitments),
            "main_contributions": list(self.main_contributions),
            "problem_class_atlas": list(self.problem_class_atlas),
            "chapter_goals": list(self.chapter_goals),
            "provenance": dict(self.provenance),
            "coverage_by_domain": self.coverage_by_domain(),
        }


# ---------------------------------------------------------------------------
# Compatibility surfaces expected by existing semantic_center package files
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JuGeoWorldview(IntroductionJuGeoRecord):
    """Backward-compatible worldview record expected by nearby package files."""

    def invariants_hold(self) -> bool:
        joined = " ".join(
            self.worldview_commitments + self.trust_notes + self.authority_notes + (self.semantic_claim, self.thesis_position)
        ).lower()
        has_ag_dtt_ai = "ag+dtt+ai" in joined or "ag, dtt, and ai" in joined
        return (
            "semantic center" in joined
            and has_ag_dtt_ai
            and "no silent" in joined
            and len(self.problem_domains) >= 3
        )

    def validate(self) -> StructuredFailure | None:  # type: ignore[override]
        base_failures = IntroductionJuGeoRecord.validate(self)
        if base_failures:
            return base_failures[0]
        if not self.invariants_hold():
            return _claim_failure(
                "JuGeoWorldview invariants failed: semantic center, AG+DTT+AI, and no-silent-promotion commitments must remain explicit",
                coordinate=self.record_id,
            )
        return None

    def one_line_summary(self) -> str:
        return f"{self.title}: {self.semantic_claim}"


@dataclass(frozen=True, slots=True)
class ThesisClaim:
    """Compatibility claim object with explicit trust and evidence behavior."""

    claim_id: str
    section: str
    statement: str
    status: ClaimStatus = ClaimStatus.PROPOSED
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
    open_obligations: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = ()
    trust: TrustAnnotation = field(default_factory=TrustAnnotation)
    copilot_annotation: str = ""
    related_claims: tuple[str, ...] = ()
    formalized: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _normalize_required_text(self.claim_id, field_name="claim_id"))
        object.__setattr__(self, "section", _normalize_required_text(self.section, field_name="section"))
        object.__setattr__(self, "statement", _normalize_required_text(self.statement, field_name="statement"))
        object.__setattr__(self, "open_obligations", _normalize_text_tuple(self.open_obligations, field_name="open_obligations"))
        object.__setattr__(self, "obstructions", _normalize_text_tuple(self.obstructions, field_name="obstructions"))
        object.__setattr__(self, "related_claims", _normalize_text_tuple(self.related_claims, field_name="related_claims"))
        object.__setattr__(self, "copilot_annotation", self.copilot_annotation.strip())

    def is_verified(self) -> bool:
        return self.status is ClaimStatus.VERIFIED

    def is_obstructed(self) -> bool:
        return self.status is ClaimStatus.OBSTRUCTED or bool(self.obstructions)

    def is_open(self) -> bool:
        return bool(self.open_obligations)

    def trust_level(self) -> TrustLevel:
        return self.trust.level

    def progress_fraction(self) -> float:
        if self.status is ClaimStatus.RETRACTED:
            return 0.0
        if self.is_verified() and not self.open_obligations:
            return 1.0
        pending = len(self.open_obligations)
        blocked = len(self.obstructions)
        if pending == 0 and blocked == 0:
            return 0.0 if self.status is ClaimStatus.PROPOSED else 1.0
        weighted_remaining = pending + (blocked * 2)
        baseline = max(weighted_remaining, len(self.evidence.items) + weighted_remaining)
        return max(0.0, min(0.95, 1.0 - (weighted_remaining / max(baseline, 1))))

    def with_evidence(self, item: EvidenceItem, *, new_status: ClaimStatus | None = None) -> "ThesisClaim":
        bundle = EvidenceBundle(items=self.evidence.items + (item,))
        key = getattr(item, "key", "") or item.canonical_key()
        algebra = TrustAlgebra()
        level = algebra.compose(self.trust.level, item.trust_level)
        updated_annotation = self.trust.with_evidence(key)
        new_trust = TrustAnnotation(
            level=level,
            evidence_basis=updated_annotation.evidence_basis,
            ceiling=self.trust.ceiling,
            floor=self.trust.floor,
            reasons=self.trust.reasons + (f"added evidence:{key}",),
        )
        return replace(
            self,
            evidence=bundle,
            status=new_status or self.status,
            trust=new_trust,
        )

    def discharge_obligation(self, obligation: str) -> "ThesisClaim":
        if obligation not in self.open_obligations:
            raise_with_scope(
                "semantic-center.missing-obligation",
                message=f"Obligation {obligation!r} not found for claim {self.claim_id!r}",
                scope=FailureScope.CHAPTER,
                classification=FailureClassification.MISSING_KEY,
                coordinate=self.claim_id,
                trust_boundary="claim-obligation-discharge",
                recoverable=True,
            )
        remaining = tuple(item for item in self.open_obligations if item != obligation)
        if not remaining and not self.obstructions:
            new_status = ClaimStatus.VERIFIED
        elif not remaining:
            new_status = ClaimStatus.OBSTRUCTED
        elif self.status is ClaimStatus.PROPOSED:
            new_status = ClaimStatus.UNDER_REVIEW
        else:
            new_status = ClaimStatus.PARTIALLY_VERIFIED
        return replace(self, open_obligations=remaining, status=new_status)

    def challenge(self, reason: str) -> "ThesisClaim":
        note = _normalize_required_text(reason, field_name="reason")
        return replace(
            self,
            status=ClaimStatus.UNDER_REVIEW,
            trust=self.trust.challenge(reason=note),
        )

    def add_obstruction(self, description: str) -> "ThesisClaim":
        text = _normalize_required_text(description, field_name="description")
        return replace(
            self,
            status=ClaimStatus.OBSTRUCTED,
            obstructions=self.obstructions + (text,),
        )

    def copilot_summary(self) -> str:
        lines = [
            f"Claim [{self.claim_id}] ({self.section})",
            f"Status: {self.status.value}",
            f"Trust: {self.trust.level.name}",
            f"Evidence items: {len(self.evidence.items)}",
            f"Statement: {textwrap.fill(self.statement, width=78)}",
        ]
        if self.open_obligations:
            lines.append("Open obligations:")
            lines.extend(f"  - {item}" for item in self.open_obligations)
        if self.obstructions:
            lines.append("Obstructions:")
            lines.extend(f"  - {item}" for item in self.obstructions)
        if self.copilot_annotation:
            lines.append(f"Copilot note: {self.copilot_annotation}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "section": self.section,
            "statement": self.statement,
            "status": self.status.value,
            "evidence_count": len(self.evidence.items),
            "open_obligations": list(self.open_obligations),
            "obstructions": list(self.obstructions),
            "trust": self.trust.to_mapping(),
            "copilot_annotation": self.copilot_annotation,
            "related_claims": list(self.related_claims),
            "formalized": self.formalized,
            "progress_fraction": self.progress_fraction(),
        }


@dataclass(frozen=True, slots=True)
class ContributionRecord:
    """Backward-compatible record for one contribution claim."""

    contribution_id: str
    title: str
    kind: ContributionKind
    description: str
    theory_section: str
    depends_on: tuple[str, ...] = ()
    realized_in_modules: tuple[str, ...] = ()
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    novelty_claim: str = ""
    copilot_note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "contribution_id", _normalize_required_text(self.contribution_id, field_name="contribution_id"))
        object.__setattr__(self, "title", _normalize_required_text(self.title, field_name="title"))
        object.__setattr__(self, "description", _normalize_required_text(self.description, field_name="description"))
        object.__setattr__(self, "theory_section", _normalize_required_text(self.theory_section, field_name="theory_section"))
        object.__setattr__(self, "depends_on", _normalize_text_tuple(self.depends_on, field_name="depends_on"))
        object.__setattr__(self, "realized_in_modules", _normalize_text_tuple(self.realized_in_modules, field_name="realized_in_modules"))
        object.__setattr__(self, "novelty_claim", self.novelty_claim.strip())
        object.__setattr__(self, "copilot_note", self.copilot_note.strip())

    def is_theoretical(self) -> bool:
        return self.kind is ContributionKind.THEORETICAL

    def is_implementation(self) -> bool:
        return self.kind is ContributionKind.IMPLEMENTATION

    def is_foundational_for(self, other: "ContributionRecord") -> bool:
        return self.contribution_id in other.depends_on

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.realized_in_modules:
            errors.append("ContributionRecord.realized_in_modules must be non-empty")
        if not self.novelty_claim:
            errors.append("ContributionRecord.novelty_claim should be non-empty")
        return errors

    def copilot_summary(self) -> str:
        deps = ", ".join(self.depends_on) if self.depends_on else "none"
        return (
            f"[{self.contribution_id}] {self.title} ({self.kind.value})\n"
            f"Section: {self.theory_section} | Trust: {self.trust_level.name}\n"
            f"Depends on: {deps}\n"
            f"{textwrap.fill(self.description, width=78)}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contribution_id": self.contribution_id,
            "title": self.title,
            "kind": self.kind.value,
            "description": self.description,
            "theory_section": self.theory_section,
            "depends_on": list(self.depends_on),
            "realized_in_modules": list(self.realized_in_modules),
            "trust_level": self.trust_level.name,
            "novelty_claim": self.novelty_claim,
            "copilot_note": self.copilot_note,
        }


@dataclass(frozen=True, slots=True)
class ProblemClass:
    """Backward-compatible problem-class description for chapter-1 doctrine."""

    problem_id: str
    name: str
    domain: ProblemDomain
    formal_definition: str
    why_hard: str
    jugeo_approach: str
    example_instances: tuple[str, ...]
    addressed_by_contributions: tuple[str, ...]
    theory_section: str
    open_questions: tuple[str, ...] = ()
    copilot_note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "problem_id", _normalize_required_text(self.problem_id, field_name="problem_id"))
        object.__setattr__(self, "name", _normalize_required_text(self.name, field_name="name"))
        object.__setattr__(self, "formal_definition", _normalize_required_text(self.formal_definition, field_name="formal_definition"))
        object.__setattr__(self, "why_hard", _normalize_required_text(self.why_hard, field_name="why_hard"))
        object.__setattr__(self, "jugeo_approach", _normalize_required_text(self.jugeo_approach, field_name="jugeo_approach"))
        object.__setattr__(self, "example_instances", _normalize_text_tuple(self.example_instances, field_name="example_instances"))
        object.__setattr__(self, "addressed_by_contributions", _normalize_text_tuple(self.addressed_by_contributions, field_name="addressed_by_contributions"))
        object.__setattr__(self, "theory_section", _normalize_required_text(self.theory_section, field_name="theory_section"))
        object.__setattr__(self, "open_questions", _normalize_text_tuple(self.open_questions, field_name="open_questions"))
        object.__setattr__(self, "copilot_note", self.copilot_note.strip())

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.example_instances:
            issues.append("ProblemClass.example_instances must be non-empty")
        if not self.addressed_by_contributions:
            issues.append("ProblemClass.addressed_by_contributions must be non-empty")
        return issues

    def copilot_summary(self) -> str:
        examples = ", ".join(self.example_instances[:3])
        return (
            f"[{self.problem_id}] {self.name} ({self.domain.value})\n"
            f"Section: {self.theory_section}\n"
            f"Approach: {textwrap.fill(self.jugeo_approach, width=78)}\n"
            f"Examples: {examples}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "name": self.name,
            "domain": self.domain.value,
            "formal_definition": self.formal_definition,
            "why_hard": self.why_hard,
            "jugeo_approach": self.jugeo_approach,
            "example_instances": list(self.example_instances),
            "addressed_by_contributions": list(self.addressed_by_contributions),
            "theory_section": self.theory_section,
            "open_questions": list(self.open_questions),
            "copilot_note": self.copilot_note,
        }


# ---------------------------------------------------------------------------
# Canonical chapter data
# ---------------------------------------------------------------------------


CHAPTER_SCOPE: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="chapter-01.semantic-center",
    section_title=CHAPTER_TITLE,
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "chapter_01"),
    problem_domains=tuple(ProblemDomain),
    trust_boundary="chapter-wide worldview is descriptive and implementation-guiding, but it does not by itself settle solver or theorem obligations.",
    authority_boundary="chapter-1 introduction defines semantic posture and scope; later section, algorithm, integration, and theorem modules refine executable authority.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/thesis/semantic_center/models.py",
    ),
    dependencies=(
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/errors.py",
        "src/jugeo/judgments/contexts.py",
    ),
    semantic_tags=("semantic-center", "chapter-01", "worldview", "trust-boundary"),
    provenance=("preliminaries/theory2.tex", "preliminaries/theory2.pdf", "theory2-src-blueprint.json"),
)

_SCOPE_1: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="intro.semantic-center",
    section_title=INTRODUCTION_SOURCE_SECTIONS[0],
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "section_01"),
    problem_domains=(ProblemDomain.SEMANTIC_VERIFICATION, ProblemDomain.TRUST_MANAGEMENT, ProblemDomain.OBSTRUCTION_TRACKING),
    trust_boundary="geometry may organize local and global judgment state, but proposals remain proposals until evidence crosses the relevant boundary.",
    authority_boundary="section 1 owns the semantic-center framing and the local-to-global story, not comparative tooling claims or theorem settlement.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
    ),
    dependencies=("src/jugeo/thesis/semantic_center/manifest.py",),
    semantic_tags=("judgment-geometry", "semantic-state", "gluing", "obstruction"),
    provenance=("preliminaries/theory2.tex", "section:judgment-geometry"),
)

_SCOPE_2: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="intro.relative-positioning",
    section_title=INTRODUCTION_SOURCE_SECTIONS[1],
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "section_02"),
    problem_domains=(ProblemDomain.MIXED_EVIDENCE, ProblemDomain.TRUST_MANAGEMENT, ProblemDomain.SEMANTIC_VERIFICATION),
    trust_boundary="JuGeo preserves proof obligations and evidence provenance instead of collapsing theorem provers, coding assistants, and agentic verifiers into one scalar trust score.",
    authority_boundary="section 2 can compare tool families and name capability differences, but it does not redefine their authority models.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
    ),
    dependencies=("src/jugeo/thesis/semantic_center/manifest.py",),
    semantic_tags=("comparative-positioning", "evidence-provenance", "tool-family"),
    provenance=("preliminaries/theory2.tex", "section:relative-positioning"),
)

_SCOPE_3: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="intro.ag-dtt-ai",
    section_title=INTRODUCTION_SOURCE_SECTIONS[2],
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "section_03"),
    problem_domains=(ProblemDomain.LONG_HORIZON_GENERATION, ProblemDomain.MATHEMATICAL_IDEATION, ProblemDomain.SEMANTIC_VERIFICATION),
    trust_boundary="AI contributes controlled proposal and semantic search inside typed jurisdiction, not final settlement authority.",
    authority_boundary="section 3 owns the synthesis claim tying AG, DTT, and AI together, while keeping solver- and proof-backed settlement downstream.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
    ),
    dependencies=("src/jugeo/thesis/semantic_center/manifest.py",),
    semantic_tags=("ag+dtt+ai", "typed-jurisdiction", "future-state-search"),
    provenance=("preliminaries/theory2.tex", "section:ag-dtt-ai"),
)

_SCOPE_4: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="intro.contributions",
    section_title=INTRODUCTION_SOURCE_SECTIONS[3],
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "section_04"),
    problem_domains=tuple(ProblemDomain),
    trust_boundary="contribution statements organize what the dissertation claims to add; they are not theorem certificates.",
    authority_boundary="section 4 catalogs contribution claims but does not silently promote them to proofs or globally discharged algorithms.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/main_contributions.py",
    ),
    dependencies=("src/jugeo/thesis/semantic_center/manifest.py",),
    semantic_tags=("contributions", "implementation-guiding", "thesis-surface"),
    provenance=("preliminaries/theory2.tex", "section:main-contributions"),
)

_SCOPE_5: Final[IntroductionJuGeoScope] = IntroductionJuGeoScope(
    scope_id="intro.problem-classes",
    section_title=INTRODUCTION_SOURCE_SECTIONS[4],
    semantic_coordinate=_module_coordinate("thesis", "semantic_center", "section_05"),
    problem_domains=tuple(ProblemDomain),
    trust_boundary="problem coverage is intentionally broad, but maturity remains uneven and must be stated honestly.",
    authority_boundary="section 5 owns the problem atlas and maturity notes, not the detailed algorithms that later modules will attach to each class.",
    source_modules=(
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/problem_classes_addressed.py",
    ),
    dependencies=("src/jugeo/thesis/semantic_center/manifest.py",),
    semantic_tags=("problem-atlas", "coverage", "maturity-honesty"),
    provenance=("preliminaries/theory2.tex", "section:problem-classes"),
)

INTRODUCTION_JUGEO_SCOPES: Final[tuple[IntroductionJuGeoScope, ...]] = (
    CHAPTER_SCOPE,
    _SCOPE_1,
    _SCOPE_2,
    _SCOPE_3,
    _SCOPE_4,
    _SCOPE_5,
)

INTRODUCTION_JUGEO_RECORDS: Final[tuple[IntroductionJuGeoRecord, ...]] = (
    IntroductionJuGeoRecord(
        record_id="intro-record.semantic-center",
        title="Judgment geometry is JuGeo's semantic center",
        scope=_SCOPE_1,
        thesis_position="JuGeo is one semantic machine whose primary object is a project's judgment state rather than a pile of disconnected verification and generation tools.",
        semantic_claim="Judgment geometry makes verification, generation, repair, equivalence, and ideation different operations on one geometric judgment state indexed by semantic coordinates, covers, overlaps, and obstruction classes.",
        comparative_positioning="This framing rejects both 'generator first, verifier later' and 'verifier first, generator later' narratives.",
        worldview_commitments=(WORLDVIEW_COMMITMENTS[0], WORLDVIEW_COMMITMENTS[1], WORLDVIEW_COMMITMENTS[6]),
        contribution_kinds=(ContributionKind.FRAMEWORK, ContributionKind.THEORETICAL),
        contributions=(MAIN_CONTRIBUTIONS[0], MAIN_CONTRIBUTIONS[4]),
        problem_domains=_SCOPE_1.problem_domains,
        problem_classes=("specification satisfaction", "repair and transformation", "regression closure"),
        trust_notes=(
            "No silent trust promotion is permitted when moving from local sections to global coherence.",
            "Obstruction objects must remain explicit rather than being collapsed into boolean failure.",
        ),
        authority_notes=(
            "The semantic-center record describes doctrine and implementation direction but does not certify theorem discharge.",
        ),
        related_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
            "src/jugeo/judgments/contexts.py",
        ),
        related_symbols=("IntroductionJuGeoScope", "IntroductionJuGeoRecord", "SemanticContext"),
        citations=("preliminaries/theory2.tex#section-judgment-geometry-as-the-semantic-center",),
        provenance=MANIFEST_SPEC_PROVENANCE,
        copilot_notes=("Copilot may help navigate section-local coordinates but must not declare global coherence on plausibility alone.",),
    ),
    IntroductionJuGeoRecord(
        record_id="intro-record.relative-positioning",
        title="JuGeo keeps provenance explicit relative to adjacent tool families",
        scope=_SCOPE_2,
        thesis_position="JuGeo preserves explicit proof obligations and evidence provenance from theorem provers while refusing to treat plausible generated text as success.",
        semantic_claim="Comparative advantage comes from keeping covers, overlap treaties, gluing reports, obstruction classes, and replay-local invalidation as first-class operational objects.",
        comparative_positioning="JuGeo differs from coding assistants and ordinary agentic verifiers because it exposes local-to-global structure instead of only queueing tools over files.",
        worldview_commitments=(WORLDVIEW_COMMITMENTS[4], WORLDVIEW_COMMITMENTS[5], WORLDVIEW_COMMITMENTS[6]),
        contribution_kinds=(ContributionKind.FRAMEWORK, ContributionKind.IMPLEMENTATION),
        contributions=(MAIN_CONTRIBUTIONS[1], MAIN_CONTRIBUTIONS[2]),
        problem_domains=_SCOPE_2.problem_domains,
        problem_classes=("documentation alignment", "public-surface honesty", "equivalence and refinement"),
        trust_notes=(
            "Evidence provenance must survive comparison across tool families.",
            "Proposal channels remain below proof-backed settlement.",
        ),
        authority_notes=(
            "Comparative positioning names capability differences without claiming jurisdiction over external systems.",
        ),
        related_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
            "src/jugeo/errors.py",
        ),
        related_symbols=("ClaimStatus", "ContributionKind", "ProblemDomain"),
        citations=("preliminaries/theory2.tex#section-jugeo-relative-to-theorem-provers-coding-assistants-and-agentic-verifiers",),
        provenance=MANIFEST_SPEC_PROVENANCE,
    ),
    IntroductionJuGeoRecord(
        record_id="intro-record.ag-dtt-ai",
        title="The AG+DTT+AI thesis is a synthesis claim about complementary strengths",
        scope=_SCOPE_3,
        thesis_position="AG supplies the geometric backbone, DTT supplies typed contexts and inhabitants, and AI supplies controlled proposal and search over future semantic states.",
        semantic_claim="Only the combination of AG, DTT, and AI is claimed to be strong enough for long-codebase generation, verification, and mathematical discovery without collapsing into proof fetishism or prompt theater.",
        comparative_positioning="The AI layer is explicitly subordinated to typed jurisdiction and trust policy rather than being allowed to self-certify.",
        worldview_commitments=(WORLDVIEW_COMMITMENTS[2], WORLDVIEW_COMMITMENTS[3], WORLDVIEW_COMMITMENTS[4]),
        contribution_kinds=(ContributionKind.THEORETICAL, ContributionKind.FRAMEWORK),
        contributions=(MAIN_CONTRIBUTIONS[0], MAIN_CONTRIBUTIONS[3]),
        problem_domains=_SCOPE_3.problem_domains,
        problem_classes=("large-scale code generation", "purpose-directed mathematical ideation"),
        trust_notes=(
            "AI contributes proposal and search, not silent settlement.",
            "Global artifacts are treated as H^0-like objects and failures as obstruction data rather than as free-form chat output.",
        ),
        authority_notes=(
            "The synthesis claim remains falsifiable and should induce later algorithm and theorem surfaces.",
        ),
        related_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
            "src/jugeo/judgments/contexts.py",
        ),
        related_symbols=("JuGeoWorldview", "IntroductionJuGeoSummary"),
        citations=("preliminaries/theory2.tex#section-the-ag-dtt-ai-thesis",),
        provenance=MANIFEST_SPEC_PROVENANCE,
        copilot_notes=("Copilot orchestration belongs inside typed semantic control, not outside it.",),
    ),
    IntroductionJuGeoRecord(
        record_id="intro-record.main-contributions",
        title="The introduction states implementation-guiding contributions rather than metaphor alone",
        scope=_SCOPE_4,
        thesis_position="Every major theoretical object should induce authority centers, compiled views, invalidation rules, and theorem or testing obligations in code.",
        semantic_claim="The dissertation's five main contributions are intentionally stated in a way that downstream modules can operationalize into records, algorithms, integrations, and tests.",
        comparative_positioning="This is stricter than a chapter whose claims remain descriptive prose; the contributions are meant to be implementation-driving surfaces.",
        worldview_commitments=(WORLDVIEW_COMMITMENTS[7],),
        contribution_kinds=(ContributionKind.THEORETICAL, ContributionKind.ALGORITHMIC, ContributionKind.IMPLEMENTATION),
        contributions=MAIN_CONTRIBUTIONS,
        problem_domains=_SCOPE_4.problem_domains,
        problem_classes=("large-scale code generation", "regression closure", "public-surface honesty"),
        trust_notes=(
            "Contribution claims should preserve provenance and remain falsifiable.",
        ),
        authority_notes=(
            "Implementation-guiding is not equivalent to already implemented; the record stays honest about current generation state.",
        ),
        related_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/main_contributions.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        related_symbols=("ContributionRecord", "IntroductionJuGeoRecord"),
        citations=("preliminaries/theory2.tex#section-main-contributions",),
        provenance=MANIFEST_SPEC_PROVENANCE,
    ),
    IntroductionJuGeoRecord(
        record_id="intro-record.problem-classes",
        title="JuGeo's problem classes are broad but must remain honest about maturity",
        scope=_SCOPE_5,
        thesis_position="The thesis claims that many software and theorem tasks can be placed inside one judgment language strongly enough that progress in one class can improve the others.",
        semantic_claim="Covered problem classes span specification satisfaction, bug finding, equivalence, repair, documentation alignment, migration, regression closure, public-surface honesty, performance reasoning, concurrency, large-scale generation, and mathematical ideation.",
        comparative_positioning="The breadth claim is paired with an explicit maturity disclaimer rather than pretending every problem class is equally solved today.",
        worldview_commitments=(WORLDVIEW_COMMITMENTS[0], WORLDVIEW_COMMITMENTS[7]),
        contribution_kinds=(ContributionKind.FRAMEWORK, ContributionKind.IMPLEMENTATION),
        contributions=(MAIN_CONTRIBUTIONS[2], MAIN_CONTRIBUTIONS[4]),
        problem_domains=_SCOPE_5.problem_domains,
        problem_classes=PROBLEM_CLASS_ATLAS,
        trust_notes=(
            "Honest problem coverage requires preserving maturity differences instead of flattening them into a single marketing claim.",
        ),
        authority_notes=(
            "The atlas is chapter doctrine; later files should add narrower algorithms and theorem surfaces per problem family.",
        ),
        related_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/problem_classes_addressed.py",
        ),
        related_symbols=("ProblemClass", "IntroductionJuGeoScope"),
        citations=("preliminaries/theory2.tex#section-problem-classes-addressed",),
        provenance=MANIFEST_SPEC_PROVENANCE,
    ),
)

INTRODUCTION_JUGEO_SUMMARY: Final[IntroductionJuGeoSummary] = IntroductionJuGeoSummary(
    chapter_number=CHAPTER_NUMBER,
    part_number=PART_NUMBER,
    chapter_title=CHAPTER_TITLE,
    package_path=PACKAGE_PATH,
    scopes=INTRODUCTION_JUGEO_SCOPES,
    records=INTRODUCTION_JUGEO_RECORDS,
    worldview_commitments=WORLDVIEW_COMMITMENTS,
    main_contributions=MAIN_CONTRIBUTIONS,
    problem_class_atlas=PROBLEM_CLASS_ATLAS,
    chapter_goals=CHAPTER_GOALS,
    provenance=MANIFEST_SPEC_PROVENANCE,
)

JUGEO_WORLDVIEW: Final[JuGeoWorldview] = JuGeoWorldview(
    record_id="worldview.chapter-01",
    title=CHAPTER_TITLE,
    scope=CHAPTER_SCOPE,
    thesis_position="JuGeo should be described as a single semantic machine whose primary object is project judgment state.",
    semantic_claim="Judgment geometry is the semantic center; AG, DTT, and AI are integrated as complementary layers under explicit provenance and trust boundaries.",
    comparative_positioning="JuGeo is not merely a theorem prover, coding assistant, or agentic verifier; it keeps local-to-global semantic structure first-class.",
    worldview_commitments=WORLDVIEW_COMMITMENTS,
    contribution_kinds=(ContributionKind.FRAMEWORK, ContributionKind.THEORETICAL, ContributionKind.IMPLEMENTATION),
    contributions=MAIN_CONTRIBUTIONS,
    problem_domains=tuple(ProblemDomain),
    problem_classes=PROBLEM_CLASS_ATLAS,
    trust_notes=(
        "No silent trust promotion is permitted.",
        "Copilot and oracle channels remain proposal channels until discharged by stronger evidence.",
    ),
    authority_notes=(
        "This worldview stabilizes chapter-1 semantics but does not alone certify gluing, equivalence, or theorem discharge.",
    ),
    related_modules=(
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/judgments/contexts.py",
    ),
    related_symbols=("JuGeoWorldview", "IntroductionJuGeoSummary", "ThesisClaim"),
    citations=(
        "preliminaries/theory2.tex#chapter-introduction-what-jugeo-is",
        "theory2-src-blueprint.json#semantic_center.models",
    ),
    provenance=MANIFEST_SPEC_PROVENANCE,
    copilot_notes=("Copilot may summarize the worldview and propose edits, but settlement authority stays with explicit evidence and review.",),
)

CONTRIBUTION_RECORDS: Final[tuple[ContributionRecord, ...]] = (
    ContributionRecord(
        contribution_id="CONTRIB-01",
        title="Common judgment geometry",
        kind=ContributionKind.THEORETICAL,
        description=MAIN_CONTRIBUTIONS[0],
        theory_section=INTRODUCTION_SOURCE_SECTIONS[3],
        realized_in_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        novelty_claim="JuGeo states one common semantic geometry for software semantics, verification, repair, generation, and ideation.",
    ),
    ContributionRecord(
        contribution_id="CONTRIB-02",
        title="Mixed-evidence discipline",
        kind=ContributionKind.FRAMEWORK,
        description=MAIN_CONTRIBUTIONS[1],
        theory_section=INTRODUCTION_SOURCE_SECTIONS[3],
        depends_on=("CONTRIB-01",),
        realized_in_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        novelty_claim="Distinct evidence channels coexist without collapsing into one undifferentiated trust scalar.",
    ),
    ContributionRecord(
        contribution_id="CONTRIB-03",
        title="Project-scale generation by covers and descent",
        kind=ContributionKind.ALGORITHMIC,
        description=MAIN_CONTRIBUTIONS[2],
        theory_section=INTRODUCTION_SOURCE_SECTIONS[3],
        depends_on=("CONTRIB-01", "CONTRIB-02"),
        realized_in_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/algorithms.py",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        novelty_claim="Large-codebase generation is framed as a local-to-global descent problem rather than prompt sequencing alone.",
    ),
    ContributionRecord(
        contribution_id="CONTRIB-04",
        title="Mathematical discovery as future-state search",
        kind=ContributionKind.THEORETICAL,
        description=MAIN_CONTRIBUTIONS[3],
        theory_section=INTRODUCTION_SOURCE_SECTIONS[3],
        depends_on=("CONTRIB-01",),
        realized_in_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        novelty_claim="Theorem growth is treated as purpose-conditioned navigation over future semantic states.",
    ),
    ContributionRecord(
        contribution_id="CONTRIB-05",
        title="Implementation-guiding theory objects",
        kind=ContributionKind.IMPLEMENTATION,
        description=MAIN_CONTRIBUTIONS[4],
        theory_section=INTRODUCTION_SOURCE_SECTIONS[3],
        depends_on=("CONTRIB-01", "CONTRIB-02", "CONTRIB-03", "CONTRIB-04"),
        realized_in_modules=(
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
            "src/jugeo/thesis/semantic_center/integration.py",
            "src/jugeo/thesis/semantic_center/theorems.py",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        novelty_claim="The chapter insists that abstract theory induce concrete module, theorem, and test surfaces.",
    ),
)

PROBLEM_CLASSES: Final[tuple[ProblemClass, ...]] = (
    ProblemClass(
        problem_id="PC-01",
        name="Semantic verification",
        domain=ProblemDomain.SEMANTIC_VERIFICATION,
        formal_definition="Determine whether local and global semantic judgments satisfy explicit obligations under the chapter's judgment geometry.",
        why_hard="Verification conditions are distributed across coordinates, overlaps, and heterogeneous evidence channels.",
        jugeo_approach="Represent judgments, clauses, evidence, obstructions, and repair frontiers explicitly so local reasoning can descend to global semantic closure.",
        example_instances=("specification satisfaction", "bug finding", "public-surface honesty"),
        addressed_by_contributions=("CONTRIB-01", "CONTRIB-02", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
        open_questions=("Which fragments admit exact repair lower bounds in production codebases?",),
    ),
    ProblemClass(
        problem_id="PC-02",
        name="Mixed evidence",
        domain=ProblemDomain.MIXED_EVIDENCE,
        formal_definition="Combine proof, solver, runtime, and semantic proposal evidence without erasing channel identity.",
        why_hard="Evidence families have different strengths, coverage shapes, and failure modes; naive aggregation causes silent trust promotion.",
        jugeo_approach="Keep trust annotations, provenance, and channel distinctions explicit at the model layer so later routing and validation stay honest.",
        example_instances=("documentation alignment", "equivalence and refinement", "regression closure"),
        addressed_by_contributions=("CONTRIB-02", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
    ),
    ProblemClass(
        problem_id="PC-03",
        name="Long-horizon generation",
        domain=ProblemDomain.LONG_HORIZON_GENERATION,
        formal_definition="Generate and maintain large, interdependent artifacts across many semantic regions without losing coherence.",
        why_hard="Long-horizon tasks accumulate local inconsistency, replay invalidation, and cross-region obligations.",
        jugeo_approach="Use covers, hypercovers, overlap bookkeeping, and support-aware closure rather than treating generation as one prompt-response event.",
        example_instances=("migration", "large-scale code generation", "repair and transformation"),
        addressed_by_contributions=("CONTRIB-01", "CONTRIB-03", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
    ),
    ProblemClass(
        problem_id="PC-04",
        name="Mathematical ideation",
        domain=ProblemDomain.MATHEMATICAL_IDEATION,
        formal_definition="Search over future semantic states for novel statements, covers, coefficient regimes, and theorem-development moves.",
        why_hard="Novelty must be useful and semantically constrained; unconstrained ideation collapses into prompt theater.",
        jugeo_approach="Treat ideation as typed navigation guided by current obstructions, purposes, and domain packs rather than free-form generation.",
        example_instances=("purpose-directed mathematical ideation", "theorem growth"),
        addressed_by_contributions=("CONTRIB-01", "CONTRIB-04", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
    ),
    ProblemClass(
        problem_id="PC-05",
        name="Trust management",
        domain=ProblemDomain.TRUST_MANAGEMENT,
        formal_definition="Preserve explicit provenance and lawful trust transitions as evidence moves across subsystem boundaries.",
        why_hard="Proposal, solver, runtime, and human channels have distinct ceilings and should not be silently promoted or conflated.",
        jugeo_approach="Keep trust annotations structured and auditable so every promotion or challenge remains explicit in the semantic record.",
        example_instances=("public-surface honesty", "documentation alignment", "bug finding"),
        addressed_by_contributions=("CONTRIB-02", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
    ),
    ProblemClass(
        problem_id="PC-06",
        name="Obstruction tracking",
        domain=ProblemDomain.OBSTRUCTION_TRACKING,
        formal_definition="Represent persistent failures and repair lower bounds as first-class semantic objects rather than ephemeral diagnostics.",
        why_hard="Distributed inconsistencies often persist across overlaps and may require change-of-cover or theory-extension moves, not a single local patch.",
        jugeo_approach="Record obstruction objects, supports, and residual obligations explicitly so repair planning stays geometric and auditable.",
        example_instances=("concurrency and distributed failures", "performance reasoning", "repair and transformation"),
        addressed_by_contributions=("CONTRIB-01", "CONTRIB-03", "CONTRIB-05"),
        theory_section=INTRODUCTION_SOURCE_SECTIONS[4],
    ),
)

INTRODUCTION_THESIS_CLAIMS: Final[tuple[ThesisClaim, ...]] = (
    ThesisClaim(
        claim_id="IC-01",
        section=INTRODUCTION_SOURCE_SECTIONS[0],
        statement=INTRODUCTION_JUGEO_RECORDS[0].semantic_claim,
        open_obligations=("show that local judgments and overlaps are first-class runtime objects",),
        trust=TrustAnnotation(level=TrustLevel.UNVERIFIED, reasons=("chapter introduction claim",)),
        formalized=True,
    ),
    ThesisClaim(
        claim_id="IC-02",
        section=INTRODUCTION_SOURCE_SECTIONS[1],
        statement=INTRODUCTION_JUGEO_RECORDS[1].semantic_claim,
        open_obligations=("preserve evidence provenance across adjacent tool families",),
        trust=TrustAnnotation(level=TrustLevel.UNVERIFIED, reasons=("chapter introduction claim",)),
        formalized=True,
    ),
    ThesisClaim(
        claim_id="IC-03",
        section=INTRODUCTION_SOURCE_SECTIONS[2],
        statement=INTRODUCTION_JUGEO_RECORDS[2].semantic_claim,
        open_obligations=("show AG, DTT, and AI remain complementary under typed jurisdiction",),
        trust=TrustAnnotation(level=TrustLevel.UNVERIFIED, reasons=("chapter introduction claim",)),
        formalized=True,
    ),
)


# ---------------------------------------------------------------------------
# Public builders and queries
# ---------------------------------------------------------------------------


def build_scope_index() -> dict[str, IntroductionJuGeoScope]:
    """Return the canonical introduction scopes indexed by ``scope_id``."""

    return {scope.scope_id: scope for scope in INTRODUCTION_JUGEO_SCOPES}


def iter_records_for_section(section_title: str) -> tuple[IntroductionJuGeoRecord, ...]:
    """Return the canonical records that belong to one chapter section."""

    title = _normalize_required_text(section_title, field_name="section_title")
    return INTRODUCTION_JUGEO_SUMMARY.records_for_section(title)


def build_introduction_summary() -> IntroductionJuGeoSummary:
    """Return the canonical chapter-1 summary object."""

    return INTRODUCTION_JUGEO_SUMMARY


def build_jugeo_worldview() -> JuGeoWorldview:
    """Return the canonical compatibility worldview record."""

    return JUGEO_WORLDVIEW


# Keep one deterministic JSON rendering handy for debugging and future adapters.
INTRODUCTION_MODELS_SNAPSHOT_JSON: Final[str] = json.dumps(
    INTRODUCTION_JUGEO_SUMMARY.public_api_snapshot(),
    sort_keys=True,
    indent=2,
)
