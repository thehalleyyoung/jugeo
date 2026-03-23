"""Canonical manifest for JuGeo Chapter 1: ``semantic_center``.

This module is intentionally shaped for human and LLM readers. It makes the
chapter-level contract explicit instead of requiring callers to reverse-engineer
it from import graphs, file names, or generation-order accidents.

Governing sources
-----------------
``preliminaries/theory2.tex``
    Authoritative semantic source for the worldview and chapter claims.

``preliminaries/theory2.pdf``
    Compiled witness of the same chapter text. This is treated as a semantic
    reference witness rather than a structural hint.

``theory2-src-blueprint.json``
    Structural blueprint describing the intended package layout and the future
    class names implied by the dissertation outline.

``theory2-generation-order.json``
    Deterministic generation schedule. It is not semantically authoritative,
    but it is authoritative about staging, ordering, and prerequisite waves.

Chapter 1 worldview
-------------------
Theory2 presents JuGeo as a judgment-geometry machine rather than as a theorem
prover plus code generator, or as a coding assistant plus verifier. The core
claim is that a project's semantic state should be modeled explicitly, with
local sections, covers, overlaps, gluing, and obstruction tracking all treated
as first-class operational objects.

This manifest therefore records:

* semantic provenance and structural hints separately;
* the explicit chapter dependency frontier;
* bridges from blueprint-implied future classes to today's generated modules;
* chapter module surfaces and their current exported symbols;
* trust and authority boundaries, especially around copilot/oracle proposal
  channels; and
* honest distinction between realized files today and planned files that are
  implied by the blueprint but not yet generated.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping, Sequence

from jugeo.errors import FailureClassification, FailureScope, raise_with_scope
from jugeo.package_manifest import (
    MANIFEST_SPEC_PROVENANCE as PACKAGE_MANIFEST_SPEC_PROVENANCE,
    PackageManifest as SharedPackageManifest,
    build_package_manifest,
)

CHAPTER_STAGE: Final[str] = "chapter-01"
CHAPTER_SEQUENCE: Final[int] = 59
CHAPTER_NUMBER: Final[int] = 1
PART_NUMBER: Final[int] = 1
CHAPTER_TITLE: Final[str] = "Introduction: What JuGeo is"
PACKAGE_NAME: Final[str] = "jugeo.thesis.semantic_center"
PACKAGE_PATH: Final[str] = "src/jugeo/thesis/semantic_center"
TARGET_FILE: Final[str] = f"{PACKAGE_PATH}/manifest.py"
TARGET_TEST: Final[str] = "tests/jugeo/thesis/semantic_center/test_manifest.py"
SEMANTIC_SOURCE_TEX: Final[str] = "preliminaries/theory2.tex"
SEMANTIC_SOURCE_PDF: Final[str] = "preliminaries/theory2.pdf"
STRUCTURAL_BLUEPRINT: Final[str] = "theory2-src-blueprint.json"
STRUCTURAL_GENERATION_ORDER: Final[str] = "theory2-generation-order.json"

INTRODUCTION_SOURCE_SECTIONS: Final[tuple[str, ...]] = (
    "Judgment geometry as the semantic center",
    "JuGeo relative to theorem provers, coding assistants, and agentic verifiers",
    "The AG+DTT+AI thesis",
    "Main contributions",
    "Problem classes addressed",
)

MANIFEST_SPEC_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": SEMANTIC_SOURCE_TEX,
        "semantic_source_role": "authoritative-semantic-source",
        "semantic_source_pdf": SEMANTIC_SOURCE_PDF,
        "semantic_pdf_role": "compiled-reference-witness",
        "structural_blueprint": STRUCTURAL_BLUEPRINT,
        "structural_generation_order": STRUCTURAL_GENERATION_ORDER,
        "structural_hint_role": "structure-only",
        "target_file": TARGET_FILE,
        "target_test": TARGET_TEST,
        "stage": CHAPTER_STAGE,
        "sequence": CHAPTER_SEQUENCE,
        "chapter_number": CHAPTER_NUMBER,
        "part_number": PART_NUMBER,
        "chapter_title": CHAPTER_TITLE,
        "shared_package_semantic_source": str(
            PACKAGE_MANIFEST_SPEC_PROVENANCE["semantic_source"]
        ),
    }
)

WORLDVIEW_COMMITMENTS: Final[tuple[str, ...]] = (
    "JuGeo is a single semantic machine whose primary state is a project's judgment geometry rather than a loose bundle of tools.",
    "Local artifacts are treated as sections over semantic coordinates, and global coherence is a descent or sheafification question.",
    "The AG layer contributes sites, covers, hypercovers, gluing laws, Cech complexes, and obstruction classes.",
    "The DTT layer contributes contexts, dependent claims, inhabitants, and residual obligations over those coordinates.",
    "The AI layer contributes controlled proposal, semantic search, and novelty over future semantic states rather than settlement authority.",
    "Evidence provenance and proof obligations remain explicit when JuGeo is compared to theorem provers, coding assistants, and agentic verifiers.",
    "No silent trust promotion is permitted: copilot-style proposals may help navigate the space but may not silently become certified truth.",
    "Implementation guidance is part of the theory claim: major semantic objects should induce real module surfaces, invalidation rules, and tests.",
)

MAIN_CONTRIBUTIONS: Final[tuple[str, ...]] = (
    "a common judgment geometry for software semantics, generation, repair, equivalence, orchestration, and mathematical ideation, and states that geometry explicitly in sheaf-theoretic and cohomological terms rather than by metaphor alone.",
    "a mixed-evidence discipline in which proofs, solver discharge, runtime witnesses, and controlled semantic judgments can coexist without being conflated.",
    "a project-scale account of large-codebase generation in terms of covers, hypercovers, descent data, obstruction classes, and support-aware closure rather than prompt sequencing alone.",
    "an account of theorem growth and mathematical discovery as purpose-conditioned search over future semantic state, including changes of cover, changes of coefficient theory, and changes of domain pack when obstruction classes persist.",
    "all of these claims be implementation-guiding: every major theoretical object should induce authority centers, compiled views, invalidation rules, and theorem or testing obligations in code.",
)

PROBLEM_CLASS_ATLAS: Final[tuple[str, ...]] = (
    "specification satisfaction",
    "bug finding",
    "equivalence and refinement",
    "repair and transformation",
    "documentation alignment",
    "migration",
    "regression closure",
    "public-surface honesty",
    "performance reasoning",
    "concurrency and distributed failures",
    "large-scale code generation",
    "purpose-directed mathematical ideation",
)

CHAPTER_GOALS: Final[tuple[str, ...]] = (
    "Keep semantic provenance explicit so later orchestration layers can distinguish theory authority from generation-order structure.",
    "Present the chapter as an implementation-driven package rather than as a chapter-name registry.",
    "Bridge blueprint-implied future class names to today's generated modules without pretending those future files already exist.",
    "Expose stable, deterministic data shapes that are easy to inspect in tests, CLIs, IDEs, and copilot-assisted workflows.",
    "Preserve trust, authority, and semantic boundary notes in a form that downstream files can consume without guesswork.",
)

CHAPTER_TARGETS_IN_ORDER: Final[tuple[str, ...]] = (
    "src/jugeo/thesis/semantic_center/manifest.py",
    "src/jugeo/thesis/semantic_center/models.py",
    "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
    "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
    "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
    "src/jugeo/thesis/semantic_center/main_contributions.py",
    "src/jugeo/thesis/semantic_center/problem_classes_addressed.py",
    "src/jugeo/thesis/semantic_center/algorithms.py",
    "src/jugeo/thesis/semantic_center/integration.py",
    "src/jugeo/thesis/semantic_center/theorems.py",
)

REQUIRED_GENERATED_DEPENDENCIES: Final[tuple[str, ...]] = (
    "src/jugeo/package_manifest.py",
    "src/jugeo/errors.py",
)

MODULE_DEPENDENCIES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "src/jugeo/thesis/semantic_center/manifest.py": REQUIRED_GENERATED_DEPENDENCIES,
        "src/jugeo/thesis/semantic_center/models.py": (
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/main_contributions.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/problem_classes_addressed.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/manifest.py",
        ),
        "src/jugeo/thesis/semantic_center/algorithms.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
            "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
            "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
        ),
        "src/jugeo/thesis/semantic_center/integration.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/algorithms.py",
        ),
        "src/jugeo/thesis/semantic_center/theorems.py": (
            "src/jugeo/thesis/semantic_center/models.py",
            "src/jugeo/thesis/semantic_center/algorithms.py",
        ),
    }
)

TARGET_AUTHORITY_BOUNDARIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "src/jugeo/thesis/semantic_center/manifest.py": "Declares chapter scope, provenance, and compatibility bridges but does not settle mathematical claims on behalf of section modules.",
        "src/jugeo/thesis/semantic_center/models.py": "Stabilizes chapter-level records and summaries but does not by itself certify gluing, equivalence, or theorem discharge.",
        "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py": "Owns the semantic-center framing and local-to-global geometry, but not comparative positioning or contribution cataloging.",
        "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py": "Owns comparative positioning across tool families, but does not claim those tools collapse into one authority regime.",
        "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py": "Owns the AG+DTT+AI synthesis claim, but it must still keep proposal channels below proof-backed settlement.",
        "src/jugeo/thesis/semantic_center/main_contributions.py": "Owns the dissertation's explicit contribution claims, but not downstream theorem schemas or integration bundles.",
        "src/jugeo/thesis/semantic_center/problem_classes_addressed.py": "Owns the broad problem atlas while remaining honest that not all classes are solved to the same maturity level.",
        "src/jugeo/thesis/semantic_center/algorithms.py": "Owns chapter-local procedures and execution flow, but not package-wide integration or theorem-catalog authority.",
        "src/jugeo/thesis/semantic_center/integration.py": "Reserved for future chapter-to-package bridge surfaces.",
        "src/jugeo/thesis/semantic_center/theorems.py": "Reserved for future theorem schemas and falsification suites implied by the blueprint.",
    }
)

MANIFEST_EXPORTS: Final[tuple[str, ...]] = (
    "MANIFEST_SPEC_PROVENANCE",
    "INTRODUCTION_SOURCE_SECTIONS",
    "WORLDVIEW_COMMITMENTS",
    "MAIN_CONTRIBUTIONS",
    "PROBLEM_CLASS_ATLAS",
    "CHAPTER_GOALS",
    "IntroductionModuleSurface",
    "BlueprintClassBridge",
    "IntroductionJuGeoDependencyMap",
    "IntroductionJuGeoManifest",
    "PackageManifest",
    "ManifestDependencyMap",
    "build_introduction_dependency_map",
    "build_introduction_manifest",
    "validate_manifest_shape",
    "DEPENDENCY_MAP",
    "MANIFEST",
    "CHAPTER_MANIFEST",
    "SEMANTIC_CENTER_MANIFEST",
)


def _discover_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / SEMANTIC_SOURCE_TEX).exists():
            return parent
    raise RuntimeError("Could not locate JuGeo project root from manifest.py")


PROJECT_ROOT: Final[Path] = _discover_project_root()
CHAPTER_ROOT: Final[Path] = PROJECT_ROOT / PACKAGE_PATH


def _normalize_required_text(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_text_tuple(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        normalized.append(_normalize_required_text(str(value), field_name=field_name))
    return tuple(normalized)


def _normalize_text_mapping(
    mapping: Mapping[str, str], *, field_name: str
) -> Mapping[str, str]:
    normalized: dict[str, str] = {}
    for key, value in mapping.items():
        key_text = _normalize_required_text(str(key), field_name=f"{field_name} key")
        value_text = _normalize_required_text(
            str(value), field_name=f"{field_name}[{key_text}]"
        )
        normalized[key_text] = value_text
    return MappingProxyType(normalized)


def _normalize_tuple_mapping(
    mapping: Mapping[str, Sequence[str]], *, field_name: str
) -> Mapping[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for key, values in mapping.items():
        key_text = _normalize_required_text(str(key), field_name=f"{field_name} key")
        normalized[key_text] = _normalize_text_tuple(
            values, field_name=f"{field_name}[{key_text}]"
        )
    return MappingProxyType(normalized)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _target_to_module_path(target: str) -> str:
    if not target.startswith("src/") or not target.endswith(".py"):
        raise ValueError(f"Unexpected target path {target!r}")
    return target[4:-3].replace("/", ".")


def _extract_literal_exports(file_path: Path) -> tuple[str, ...]:
    tree = ast.parse(file_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if not isinstance(node.value, (ast.List, ast.Tuple)):
                    return ()
                names: list[str] = []
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        names.append(element.value)
                return tuple(names)
    return ()


def _raise_manifest_error(
    code: str,
    summary: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise_with_scope(
        code,
        message=summary,
        scope=FailureScope.CHAPTER,
        classification=FailureClassification.INVALID_VALUE,
        provenance={
            "target_file": TARGET_FILE,
            "target_test": TARGET_TEST,
            "details": dict(details or {}),
        },
        notes=(
            "semantic_center manifest payloads must remain explicit, deterministic, and theory-aligned.",
        ),
    )


@dataclass(frozen=True, slots=True)
class IntroductionModuleSurface:
    """Stable description of one realized Chapter 1 module surface."""

    module_name: str
    module_path: str
    file_path: str
    role: str
    source_sections: tuple[str, ...] = ()
    blueprint_classes: tuple[str, ...] = ()
    current_exports: tuple[str, ...] = ()
    semantic_focus: tuple[str, ...] = ()
    authority_boundary: str = ""
    trust_boundary: str = ""
    problem_classes: tuple[str, ...] = ()
    future_work: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "module_name",
            _normalize_required_text(self.module_name, field_name="module_name"),
        )
        object.__setattr__(
            self,
            "module_path",
            _normalize_required_text(self.module_path, field_name="module_path"),
        )
        object.__setattr__(
            self,
            "file_path",
            _normalize_required_text(self.file_path, field_name="file_path"),
        )
        object.__setattr__(
            self, "role", _normalize_required_text(self.role, field_name="role")
        )
        object.__setattr__(
            self,
            "source_sections",
            _normalize_text_tuple(self.source_sections, field_name="source_sections")
            if self.source_sections
            else (),
        )
        object.__setattr__(
            self,
            "blueprint_classes",
            _normalize_text_tuple(
                self.blueprint_classes, field_name="blueprint_classes"
            )
            if self.blueprint_classes
            else (),
        )
        object.__setattr__(
            self,
            "current_exports",
            _normalize_text_tuple(self.current_exports, field_name="current_exports")
            if self.current_exports
            else (),
        )
        object.__setattr__(
            self,
            "semantic_focus",
            _normalize_text_tuple(self.semantic_focus, field_name="semantic_focus")
            if self.semantic_focus
            else (),
        )
        object.__setattr__(
            self,
            "authority_boundary",
            _normalize_required_text(
                self.authority_boundary, field_name="authority_boundary"
            ),
        )
        object.__setattr__(
            self,
            "trust_boundary",
            _normalize_required_text(self.trust_boundary, field_name="trust_boundary"),
        )
        object.__setattr__(
            self,
            "problem_classes",
            _normalize_text_tuple(self.problem_classes, field_name="problem_classes")
            if self.problem_classes
            else (),
        )
        object.__setattr__(
            self,
            "future_work",
            _normalize_text_tuple(self.future_work, field_name="future_work")
            if self.future_work
            else (),
        )
        if not self.file_path.startswith(PACKAGE_PATH):
            raise ValueError(
                "chapter module surfaces must stay within the semantic_center package path"
            )

    @property
    def section_count(self) -> int:
        return len(self.source_sections)

    def fully_qualified_exports(self) -> tuple[str, ...]:
        return tuple(f"{self.module_path}.{symbol}" for symbol in self.current_exports)

    def covers_section(self, section_title: str) -> bool:
        return section_title in self.source_sections

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "module_path": self.module_path,
            "file_path": self.file_path,
            "role": self.role,
            "source_sections": list(self.source_sections),
            "blueprint_classes": list(self.blueprint_classes),
            "current_exports": list(self.current_exports),
            "semantic_focus": list(self.semantic_focus),
            "authority_boundary": self.authority_boundary,
            "trust_boundary": self.trust_boundary,
            "problem_classes": list(self.problem_classes),
            "future_work": list(self.future_work),
        }


@dataclass(frozen=True, slots=True)
class BlueprintClassBridge:
    """Bridge from a blueprint-implied class name to today's live symbols."""

    blueprint_class: str
    current_symbols: tuple[str, ...]
    module_path: str
    relation: str
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blueprint_class",
            _normalize_required_text(
                self.blueprint_class, field_name="blueprint_class"
            ),
        )
        object.__setattr__(
            self,
            "current_symbols",
            _normalize_text_tuple(self.current_symbols, field_name="current_symbols")
            if self.current_symbols
            else (),
        )
        object.__setattr__(
            self,
            "module_path",
            _normalize_required_text(self.module_path, field_name="module_path"),
        )
        object.__setattr__(
            self,
            "relation",
            _normalize_required_text(self.relation, field_name="relation"),
        )
        object.__setattr__(
            self,
            "rationale",
            _normalize_required_text(self.rationale, field_name="rationale"),
        )

    @property
    def is_planned_only(self) -> bool:
        return self.relation.startswith("planned")

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_class": self.blueprint_class,
            "current_symbols": list(self.current_symbols),
            "module_path": self.module_path,
            "relation": self.relation,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class IntroductionJuGeoDependencyMap:
    """Explicit dependency frontier for Chapter 1 files and future targets."""

    chapter_targets_in_order: tuple[str, ...]
    required_generated_dependencies: tuple[str, ...]
    module_dependencies: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    authority_boundaries: Mapping[str, str] = field(default_factory=dict)
    stage: str = CHAPTER_STAGE
    sequence: int = CHAPTER_SEQUENCE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "chapter_targets_in_order",
            _normalize_text_tuple(
                self.chapter_targets_in_order, field_name="chapter_targets_in_order"
            ),
        )
        object.__setattr__(
            self,
            "required_generated_dependencies",
            _normalize_text_tuple(
                self.required_generated_dependencies,
                field_name="required_generated_dependencies",
            ),
        )
        object.__setattr__(
            self,
            "module_dependencies",
            _normalize_tuple_mapping(
                self.module_dependencies, field_name="module_dependencies"
            ),
        )
        object.__setattr__(
            self,
            "authority_boundaries",
            _normalize_text_mapping(
                self.authority_boundaries, field_name="authority_boundaries"
            ),
        )
        object.__setattr__(
            self, "stage", _normalize_required_text(self.stage, field_name="stage")
        )
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        for target in self.chapter_targets_in_order:
            if target not in self.module_dependencies:
                raise ValueError(
                    f"missing dependency declaration for chapter target {target!r}"
                )
        for target, dependencies in self.module_dependencies.items():
            if target not in self.chapter_targets_in_order:
                continue
            for dependency in dependencies:
                if (
                    dependency not in self.required_generated_dependencies
                    and dependency not in self.chapter_targets_in_order
                ):
                    raise ValueError(
                        f"dependency {dependency!r} for target {target!r} is not in the required or chapter-local frontier"
                    )

    def immediate_dependencies(self, target: str) -> tuple[str, ...]:
        return self.module_dependencies.get(target, ())

    def dependency_edges(self) -> tuple[tuple[str, str], ...]:
        edges: list[tuple[str, str]] = []
        for target in self.chapter_targets_in_order:
            for dependency in self.immediate_dependencies(target):
                edges.append((dependency, target))
        return tuple(edges)

    def transitive_dependencies(self, target: str) -> tuple[str, ...]:
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(current: str) -> None:
            for dependency in self.immediate_dependencies(current):
                if dependency in visited:
                    continue
                visited.add(dependency)
                visit(dependency)
                ordered.append(dependency)

        visit(target)
        return tuple(ordered)

    def downstream_dependents(self, target: str) -> tuple[str, ...]:
        dependents: list[str] = []
        for candidate in self.chapter_targets_in_order:
            if target in self.transitive_dependencies(candidate):
                dependents.append(candidate)
        return tuple(dependents)

    def planned_module_paths(self) -> tuple[str, ...]:
        return tuple(_target_to_module_path(target) for target in self.chapter_targets_in_order)

    def validate_targets(self, known_targets: Iterable[str]) -> tuple[str, ...]:
        known = set(known_targets)
        errors: list[str] = []
        for target in self.chapter_targets_in_order:
            if target not in known:
                errors.append(f"unknown chapter target: {target}")
            for dependency in self.immediate_dependencies(target):
                if (
                    dependency not in known
                    and dependency not in self.required_generated_dependencies
                ):
                    errors.append(
                        f"dependency {dependency!r} declared for {target!r} is not known"
                    )
        return tuple(errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_targets_in_order": list(self.chapter_targets_in_order),
            "required_generated_dependencies": list(
                self.required_generated_dependencies
            ),
            "module_dependencies": {
                target: list(dependencies)
                for target, dependencies in self.module_dependencies.items()
            },
            "authority_boundaries": dict(self.authority_boundaries),
            "stage": self.stage,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class IntroductionJuGeoManifest:
    """Canonical manifest record for Chapter 1 of the thesis package tree."""

    name: str
    version: str
    chapter_number: int
    part_number: int
    chapter_title: str
    package_path: str
    target_file: str
    target_test: str
    semantic_sources: tuple[str, ...]
    structural_hints: tuple[str, ...]
    source_sections: tuple[str, ...]
    dependency_map: IntroductionJuGeoDependencyMap
    module_surfaces: tuple[IntroductionModuleSurface, ...]
    blueprint_bridges: tuple[BlueprintClassBridge, ...]
    worldview_commitments: tuple[str, ...]
    main_contributions: tuple[str, ...]
    problem_classes: tuple[str, ...]
    chapter_goals: tuple[str, ...]
    public_api: tuple[str, ...]
    shared_manifest_name: str
    shared_semantic_source: str
    shared_structural_hints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _normalize_required_text(self.name, field_name="name")
        )
        object.__setattr__(
            self,
            "version",
            _normalize_required_text(self.version, field_name="version"),
        )
        object.__setattr__(
            self,
            "chapter_title",
            _normalize_required_text(self.chapter_title, field_name="chapter_title"),
        )
        object.__setattr__(
            self,
            "package_path",
            _normalize_required_text(self.package_path, field_name="package_path"),
        )
        object.__setattr__(
            self,
            "target_file",
            _normalize_required_text(self.target_file, field_name="target_file"),
        )
        object.__setattr__(
            self,
            "target_test",
            _normalize_required_text(self.target_test, field_name="target_test"),
        )
        object.__setattr__(
            self,
            "semantic_sources",
            _normalize_text_tuple(self.semantic_sources, field_name="semantic_sources"),
        )
        object.__setattr__(
            self,
            "structural_hints",
            _normalize_text_tuple(self.structural_hints, field_name="structural_hints"),
        )
        object.__setattr__(
            self,
            "source_sections",
            _normalize_text_tuple(self.source_sections, field_name="source_sections"),
        )
        object.__setattr__(self, "module_surfaces", tuple(self.module_surfaces))
        object.__setattr__(self, "blueprint_bridges", tuple(self.blueprint_bridges))
        object.__setattr__(
            self,
            "worldview_commitments",
            _normalize_text_tuple(
                self.worldview_commitments, field_name="worldview_commitments"
            ),
        )
        object.__setattr__(
            self,
            "main_contributions",
            _normalize_text_tuple(
                self.main_contributions, field_name="main_contributions"
            ),
        )
        object.__setattr__(
            self,
            "problem_classes",
            _normalize_text_tuple(self.problem_classes, field_name="problem_classes"),
        )
        object.__setattr__(
            self,
            "chapter_goals",
            _normalize_text_tuple(self.chapter_goals, field_name="chapter_goals"),
        )
        object.__setattr__(
            self,
            "public_api",
            _normalize_text_tuple(self.public_api, field_name="public_api"),
        )
        object.__setattr__(
            self,
            "shared_manifest_name",
            _normalize_required_text(
                self.shared_manifest_name, field_name="shared_manifest_name"
            ),
        )
        object.__setattr__(
            self,
            "shared_semantic_source",
            _normalize_required_text(
                self.shared_semantic_source, field_name="shared_semantic_source"
            ),
        )
        object.__setattr__(
            self,
            "shared_structural_hints",
            _normalize_text_tuple(
                self.shared_structural_hints, field_name="shared_structural_hints"
            ),
        )
        if self.chapter_number != CHAPTER_NUMBER:
            raise ValueError("chapter_number must match Chapter 1")
        if self.part_number != PART_NUMBER:
            raise ValueError("part_number must match Part 1")
        if not self.module_surfaces:
            raise ValueError("module_surfaces may not be empty")
        if len(self.public_api) != len(set(self.public_api)):
            raise ValueError("public_api must not contain duplicate symbols")

    @property
    def package_name(self) -> str:
        return self.name

    @property
    def module_surface_map(self) -> Mapping[str, IntroductionModuleSurface]:
        return MappingProxyType(
            {surface.module_name: surface for surface in self.module_surfaces}
        )

    @property
    def export_index(self) -> Mapping[str, str]:
        index: dict[str, str] = {}
        for surface in self.module_surfaces:
            for symbol in surface.current_exports:
                index.setdefault(symbol, surface.module_path)
        return MappingProxyType(index)

    @property
    def blueprint_bridge_map(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType(
            {
                bridge.blueprint_class: bridge.current_symbols
                for bridge in self.blueprint_bridges
            }
        )

    def module_paths(self) -> tuple[str, ...]:
        return tuple(surface.module_path for surface in self.module_surfaces)

    def section_modules(self) -> tuple[IntroductionModuleSurface, ...]:
        return tuple(
            surface
            for surface in self.module_surfaces
            if surface.role.startswith("section-")
        )

    def realized_target_paths(self) -> tuple[str, ...]:
        return tuple(surface.file_path for surface in self.module_surfaces)

    def unrealized_target_paths(self) -> tuple[str, ...]:
        realized = set(self.realized_target_paths())
        return tuple(
            target
            for target in self.dependency_map.chapter_targets_in_order
            if target not in realized
        )

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.name != PACKAGE_NAME:
            errors.append(f"name must be {PACKAGE_NAME!r}, got {self.name!r}")
        if self.chapter_title != CHAPTER_TITLE:
            errors.append(f"chapter_title must be {CHAPTER_TITLE!r}")
        if self.package_path != PACKAGE_PATH:
            errors.append(f"package_path must be {PACKAGE_PATH!r}")
        if self.target_file != TARGET_FILE:
            errors.append(f"target_file must be {TARGET_FILE!r}")
        if self.target_test != TARGET_TEST:
            errors.append(f"target_test must be {TARGET_TEST!r}")
        if self.semantic_sources != (SEMANTIC_SOURCE_TEX, SEMANTIC_SOURCE_PDF):
            errors.append("semantic_sources must preserve both tex and pdf governance")
        if self.structural_hints != (STRUCTURAL_BLUEPRINT, STRUCTURAL_GENERATION_ORDER):
            errors.append(
                "structural_hints must preserve the blueprint and generation-order files"
            )
        if self.source_sections != INTRODUCTION_SOURCE_SECTIONS:
            errors.append(
                "source_sections must match the authoritative chapter section list"
            )
        if self.target_file not in self.dependency_map.chapter_targets_in_order:
            errors.append("target_file must be present in the chapter dependency map")
        if self.dependency_map.sequence != CHAPTER_SEQUENCE:
            errors.append(
                "dependency map sequence must match the governing generation order entry"
            )
        if self.dependency_map.stage != CHAPTER_STAGE:
            errors.append(
                "dependency map stage must match the governing generation order entry"
            )
        if self.shared_manifest_name != "jugeo":
            errors.append(
                "shared_manifest_name must remain aligned with jugeo.package_manifest"
            )
        if self.shared_semantic_source != SEMANTIC_SOURCE_TEX:
            errors.append(
                "shared_semantic_source must inherit theory2.tex authority from the root package manifest"
            )
        if self.shared_structural_hints != self.structural_hints:
            errors.append(
                "shared_structural_hints must match this chapter manifest's structural hints"
            )
        section_titles = tuple(
            surface.source_sections[0]
            for surface in self.section_modules()
            if len(surface.source_sections) == 1
        )
        if section_titles != self.source_sections:
            errors.append("section modules must preserve the authoritative section order")
        realized_paths = set(self.realized_target_paths())
        for surface in self.module_surfaces:
            if not (PROJECT_ROOT / surface.file_path).exists():
                errors.append(
                    f"realized module surface points to missing file {surface.file_path!r}"
                )
            if surface.file_path not in self.dependency_map.chapter_targets_in_order:
                errors.append(
                    f"surface {surface.module_name!r} is not part of the declared dependency frontier"
                )
            if surface.file_path not in realized_paths:
                errors.append(
                    f"surface {surface.module_name!r} was dropped from realized target tracking"
                )
            if not surface.current_exports:
                errors.append(
                    f"surface {surface.module_name!r} must name at least one current export"
                )
        bridge_paths = set(self.dependency_map.planned_module_paths())
        for bridge in self.blueprint_bridges:
            if bridge.module_path not in bridge_paths:
                errors.append(
                    f"bridge {bridge.blueprint_class!r} points outside the chapter frontier"
                )
            if not bridge.current_symbols and not bridge.is_planned_only:
                errors.append(
                    f"bridge {bridge.blueprint_class!r} must either name current symbols or be marked planned-only"
                )
        return tuple(errors)

    def validate_or_raise(self) -> None:
        errors = self.validate()
        if errors:
            raise_with_scope(
                "semantic-center-manifest-invalid",
                message="semantic_center chapter manifest validation failed",
                scope=FailureScope.CHAPTER,
                classification=FailureClassification.INVALID_VALUE,
                provenance={
                    "target_file": self.target_file,
                    "errors": list(errors),
                },
                notes=errors,
            )

    def summary(self) -> str:
        realized = ", ".join(surface.module_name for surface in self.module_surfaces)
        planned = (
            ", ".join(Path(target).name for target in self.unrealized_target_paths())
            or "none"
        )
        return (
            f"{self.chapter_title} presents JuGeo as a judgment-geometry machine grounded in AG + DTT + AI. "
            f"This manifest keeps theory provenance explicit, records no-silent-promotion trust boundaries, "
            f"and maps the chapter's realized module surfaces ({realized}) to both current exports and the "
            f"future blueprint frontier. Planned-but-unrealized chapter files today: {planned}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "package_name": self.package_name,
            "version": self.version,
            "chapter_number": self.chapter_number,
            "part_number": self.part_number,
            "chapter_title": self.chapter_title,
            "package_path": self.package_path,
            "target_file": self.target_file,
            "target_test": self.target_test,
            "semantic_sources": list(self.semantic_sources),
            "structural_hints": list(self.structural_hints),
            "source_sections": list(self.source_sections),
            "dependency_map": self.dependency_map.to_dict(),
            "module_surfaces": [surface.to_dict() for surface in self.module_surfaces],
            "blueprint_bridges": [bridge.to_dict() for bridge in self.blueprint_bridges],
            "worldview_commitments": list(self.worldview_commitments),
            "main_contributions": list(self.main_contributions),
            "problem_classes": list(self.problem_classes),
            "chapter_goals": list(self.chapter_goals),
            "public_api": list(self.public_api),
            "shared_manifest_name": self.shared_manifest_name,
            "shared_semantic_source": self.shared_semantic_source,
            "shared_structural_hints": list(self.shared_structural_hints),
            "realized_targets": list(self.realized_target_paths()),
            "unrealized_targets": list(self.unrealized_target_paths()),
            "summary": self.summary(),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


PackageManifest = IntroductionJuGeoManifest
ManifestDependencyMap = IntroductionJuGeoDependencyMap


def _chapter_file_exports(file_name: str) -> tuple[str, ...]:
    return _extract_literal_exports(CHAPTER_ROOT / file_name)


def _build_module_surfaces() -> tuple[IntroductionModuleSurface, ...]:
    manifest_boundary = TARGET_AUTHORITY_BOUNDARIES[TARGET_FILE]
    return (
        IntroductionModuleSurface(
            module_name="manifest",
            module_path="jugeo.thesis.semantic_center.manifest",
            file_path=TARGET_FILE,
            role="chapter-manifest",
            source_sections=INTRODUCTION_SOURCE_SECTIONS,
            blueprint_classes=(
                "IntroductionJuGeoManifest",
                "IntroductionJuGeoDependencyMap",
            ),
            current_exports=MANIFEST_EXPORTS,
            semantic_focus=(
                "chapter provenance",
                "dependency frontier",
                "future blueprint bridges",
                "human-readable package introspection",
            ),
            authority_boundary=manifest_boundary,
            trust_boundary=(
                "copilot may help author or inspect this manifest, but the manifest remains descriptive and may not silently promote chapter evidence or proof status."
            ),
            problem_classes=(
                "public-surface honesty",
                "documentation alignment",
                "large-scale code generation",
            ),
            future_work=(
                "Keep the manifest compatible with future integration.py and theorems.py surfaces once those files are generated.",
            ),
        ),
        IntroductionModuleSurface(
            module_name="models",
            module_path="jugeo.thesis.semantic_center.models",
            file_path="src/jugeo/thesis/semantic_center/models.py",
            role="chapter-support-models",
            source_sections=INTRODUCTION_SOURCE_SECTIONS,
            blueprint_classes=(
                "IntroductionJuGeoRecord",
                "IntroductionJuGeoScope",
                "IntroductionJuGeoSummary",
            ),
            current_exports=_chapter_file_exports("models.py"),
            semantic_focus=(
                "worldview records",
                "thesis claim tracking",
                "contribution records",
                "problem-class summaries",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/models.py"
            ],
            trust_boundary=(
                "records and summaries preserve support-channel distinctions but do not themselves discharge solver- or runtime-backed obligations."
            ),
            problem_classes=(
                "documentation alignment",
                "public-surface honesty",
                "regression closure",
                "purpose-directed mathematical ideation",
            ),
            future_work=(
                "A later chapter-integration pass may introduce narrower record names that match the blueprint one-for-one.",
            ),
        ),
        IntroductionModuleSurface(
            module_name="judgment_geometry_as_the_semantic",
            module_path="jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
            file_path="src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py",
            role="section-01-semantic-center",
            source_sections=(INTRODUCTION_SOURCE_SECTIONS[0],),
            blueprint_classes=(
                "JudgmentGeometrySemanticCenterCoordinator",
                "JudgmentGeometrySemanticCenterAnalyzer",
                "JudgmentGeometrySemanticCenterWitness",
            ),
            current_exports=_chapter_file_exports(
                "judgment_geometry_as_the_semantic.py"
            ),
            semantic_focus=(
                "semantic product space",
                "open covers and restrictions",
                "gluing conditions",
                "coordinated verification",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/judgment_geometry_as_the_semantic.py"
            ],
            trust_boundary=(
                "geometric structure explains how local judgments should glue, but it does not relabel unresolved overlaps as globally settled."
            ),
            problem_classes=(
                "specification satisfaction",
                "bug finding",
                "equivalence and refinement",
                "repair and transformation",
            ),
        ),
        IntroductionModuleSurface(
            module_name="jugeo_relative_to_theorem_provers",
            module_path="jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
            file_path="src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py",
            role="section-02-comparative-positioning",
            source_sections=(INTRODUCTION_SOURCE_SECTIONS[1],),
            blueprint_classes=(
                "JuGeoRelativeTheoremProversCoordinator",
                "JuGeoRelativeTheoremProversAnalyzer",
                "JuGeoRelativeTheoremProversWitness",
            ),
            current_exports=_chapter_file_exports(
                "jugeo_relative_to_theorem_provers.py"
            ),
            semantic_focus=(
                "tool-family comparison",
                "evidence provenance",
                "theorem prover contrast",
                "agentic verifier contrast",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/jugeo_relative_to_theorem_provers.py"
            ],
            trust_boundary=(
                "comparative analysis preserves explicit provenance and refuses to collapse theorem proving, coding assistance, and JuGeo orchestration into one trust regime."
            ),
            problem_classes=(
                "public-surface honesty",
                "documentation alignment",
                "equivalence and refinement",
            ),
        ),
        IntroductionModuleSurface(
            module_name="the_ag_dtt_ai_thesis",
            module_path="jugeo.thesis.semantic_center.the_ag_dtt_ai_thesis",
            file_path="src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py",
            role="section-03-ag-dtt-ai",
            source_sections=(INTRODUCTION_SOURCE_SECTIONS[2],),
            blueprint_classes=(
                "TheAGDTTAICoordinator",
                "TheAGDTTAIAnalyzer",
                "TheAGDTTAIWitness",
            ),
            current_exports=_chapter_file_exports("the_ag_dtt_ai_thesis.py"),
            semantic_focus=(
                "AG backbone",
                "DTT fibers",
                "AI proposal and search",
                "thesis unification",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/the_ag_dtt_ai_thesis.py"
            ],
            trust_boundary=(
                "AI is modeled as a controlled proposal layer inside typed jurisdiction rather than an authority that can settle truth by plausibility alone."
            ),
            problem_classes=(
                "large-scale code generation",
                "purpose-directed mathematical ideation",
                "documentation alignment",
            ),
        ),
        IntroductionModuleSurface(
            module_name="main_contributions",
            module_path="jugeo.thesis.semantic_center.main_contributions",
            file_path="src/jugeo/thesis/semantic_center/main_contributions.py",
            role="section-04-contributions",
            source_sections=(INTRODUCTION_SOURCE_SECTIONS[3],),
            blueprint_classes=(
                "MainContributionsCoordinator",
                "MainContributionsAnalyzer",
                "MainContributionsWitness",
            ),
            current_exports=_chapter_file_exports("main_contributions.py"),
            semantic_focus=(
                "judgment geometry contribution",
                "evidence plurality contribution",
                "obstruction persistence contribution",
                "trust algebra contribution",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/main_contributions.py"
            ],
            trust_boundary=(
                "contribution claims may be recorded and organized here, but theorem-level settlement remains downstream and falsifiable."
            ),
            problem_classes=(
                "public-surface honesty",
                "regression closure",
                "large-scale code generation",
                "purpose-directed mathematical ideation",
            ),
        ),
        IntroductionModuleSurface(
            module_name="problem_classes_addressed",
            module_path="jugeo.thesis.semantic_center.problem_classes_addressed",
            file_path="src/jugeo/thesis/semantic_center/problem_classes_addressed.py",
            role="section-05-problem-classes",
            source_sections=(INTRODUCTION_SOURCE_SECTIONS[4],),
            blueprint_classes=(
                "ProblemClassesAddressedCoordinator",
                "ProblemClassesAddressedAnalyzer",
                "ProblemClassesAddressedWitness",
            ),
            current_exports=_chapter_file_exports("problem_classes_addressed.py"),
            semantic_focus=(
                "problem atlas",
                "semantic verification",
                "mixed evidence",
                "mathematical ideation",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/problem_classes_addressed.py"
            ],
            trust_boundary=(
                "the problem atlas is broad by design, but it remains honest that different problem classes sit at different maturity levels today."
            ),
            problem_classes=PROBLEM_CLASS_ATLAS,
        ),
        IntroductionModuleSurface(
            module_name="algorithms",
            module_path="jugeo.thesis.semantic_center.algorithms",
            file_path="src/jugeo/thesis/semantic_center/algorithms.py",
            role="chapter-procedural-companion",
            source_sections=(
                INTRODUCTION_SOURCE_SECTIONS[0],
                INTRODUCTION_SOURCE_SECTIONS[1],
                INTRODUCTION_SOURCE_SECTIONS[2],
            ),
            blueprint_classes=(
                "IntroductionJuGeoPlanner",
                "IntroductionJuGeoExecutor",
                "IntroductionJuGeoNormalizer",
            ),
            current_exports=_chapter_file_exports("algorithms.py"),
            semantic_focus=(
                "bootstrap procedure",
                "semantic-center detection",
                "claim verification flow",
            ),
            authority_boundary=TARGET_AUTHORITY_BOUNDARIES[
                "src/jugeo/thesis/semantic_center/algorithms.py"
            ],
            trust_boundary=(
                "procedures may consume oracle or copilot suggestions, but result trust remains bounded by the evidence channels that actually discharge obligations."
            ),
            problem_classes=(
                "bug finding",
                "equivalence and refinement",
                "repair and transformation",
                "large-scale code generation",
            ),
            future_work=(
                "Later integration and theorem files should consume these procedures without widening their authority boundary.",
            ),
        ),
    )


def _build_blueprint_bridges() -> tuple[BlueprintClassBridge, ...]:
    return (
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoManifest",
            current_symbols=("IntroductionJuGeoManifest",),
            module_path="jugeo.thesis.semantic_center.manifest",
            relation="implemented-by",
            rationale="The manifest file now exposes the exact blueprint class name as the canonical chapter manifest type.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoDependencyMap",
            current_symbols=("IntroductionJuGeoDependencyMap",),
            module_path="jugeo.thesis.semantic_center.manifest",
            relation="implemented-by",
            rationale="The manifest file now exposes the exact blueprint dependency-map class name as the canonical frontier record.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoRecord",
            current_symbols=(
                "JuGeoWorldview",
                "ThesisClaim",
                "ContributionRecord",
                "ProblemClass",
            ),
            module_path="jugeo.thesis.semantic_center.models",
            relation="approximated-by",
            rationale="The generated models module split the blueprint's single record concept into more specific worldview, claim, contribution, and problem-class records.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoScope",
            current_symbols=("ProblemDomain", "ContributionKind", "ClaimStatus"),
            module_path="jugeo.thesis.semantic_center.models",
            relation="approximated-by",
            rationale="Scope-like distinctions are currently represented by typed enums instead of a single scope record class.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoSummary",
            current_symbols=("JuGeoWorldview", "ProblemClass"),
            module_path="jugeo.thesis.semantic_center.models",
            relation="approximated-by",
            rationale="Summary behavior is distributed across the human-readable methods of the current data models.",
        ),
        BlueprintClassBridge(
            blueprint_class="JudgmentGeometrySemanticCenterCoordinator",
            current_symbols=("SemanticCenter", "CoordinatedVerification"),
            module_path="jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
            relation="approximated-by",
            rationale="The current section module divides coordination duties between the top-level SemanticCenter object and explicit coordination helpers.",
        ),
        BlueprintClassBridge(
            blueprint_class="JudgmentGeometrySemanticCenterAnalyzer",
            current_symbols=(
                "JudgmentGeometryFoundation",
                "SheafTheoreticalBasis",
                "SemanticProductSpace",
            ),
            module_path="jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
            relation="approximated-by",
            rationale="The analyzer role is currently realized as multiple theory-facing classes covering geometry, sheaf basis, and product-space structure.",
        ),
        BlueprintClassBridge(
            blueprint_class="JudgmentGeometrySemanticCenterWitness",
            current_symbols=(
                "OpenCoverElement",
                "RestrictionMap",
                "GluingCondition",
                "CoordinateAxis",
            ),
            module_path="jugeo.thesis.semantic_center.judgment_geometry_as_the_semantic",
            relation="approximated-by",
            rationale="Witness-level structures are currently explicit supporting records rather than one witness wrapper class.",
        ),
        BlueprintClassBridge(
            blueprint_class="JuGeoRelativeTheoremProversCoordinator",
            current_symbols=("ComparativePositioning",),
            module_path="jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
            relation="approximated-by",
            rationale="ComparativePositioning is the chapter's explicit coordination surface for JuGeo's relation to theorem provers and related tools.",
        ),
        BlueprintClassBridge(
            blueprint_class="JuGeoRelativeTheoremProversAnalyzer",
            current_symbols=(
                "TheoremProverRelation",
                "DepTypeRelation",
                "ModelCheckerRelation",
                "SolverRelation",
            ),
            module_path="jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
            relation="approximated-by",
            rationale="The current design uses specialized relation analyzers per tool family instead of a single monolithic analyzer type.",
        ),
        BlueprintClassBridge(
            blueprint_class="JuGeoRelativeTheoremProversWitness",
            current_symbols=("EvidenceMapping", "ToolKind", "COMPARATIVE_POSITIONING"),
            module_path="jugeo.thesis.semantic_center.jugeo_relative_to_theorem_provers",
            relation="approximated-by",
            rationale="Witness information currently appears as evidence maps, tool-kind taxonomy, and a canonical comparative-positioning instance.",
        ),
        BlueprintClassBridge(
            blueprint_class="TheAGDTTAICoordinator",
            current_symbols=("AGDTTAIThesis", "ThesisUnification"),
            module_path="jugeo.thesis.semantic_center.the_ag_dtt_ai_thesis",
            relation="approximated-by",
            rationale="The chapter's synthesis is organized around a thesis object and an explicit unification record.",
        ),
        BlueprintClassBridge(
            blueprint_class="TheAGDTTAIAnalyzer",
            current_symbols=(
                "AlgebraicGeometryComponent",
                "DependentTypeComponent",
                "AIComponent",
            ),
            module_path="jugeo.thesis.semantic_center.the_ag_dtt_ai_thesis",
            relation="approximated-by",
            rationale="Current implementation factors the analyzer role into one class per intellectual component of the synthesis.",
        ),
        BlueprintClassBridge(
            blueprint_class="TheAGDTTAIWitness",
            current_symbols=("ComponentInteraction", "THE_AG_DTT_AI_THESIS"),
            module_path="jugeo.thesis.semantic_center.the_ag_dtt_ai_thesis",
            relation="approximated-by",
            rationale="Interaction edges and the canonical thesis instance act as the witness surface for the synthesis.",
        ),
        BlueprintClassBridge(
            blueprint_class="MainContributionsCoordinator",
            current_symbols=("ContributionCatalog",),
            module_path="jugeo.thesis.semantic_center.main_contributions",
            relation="approximated-by",
            rationale="ContributionCatalog is the current coordination surface for the dissertation's main contribution claims.",
        ),
        BlueprintClassBridge(
            blueprint_class="MainContributionsAnalyzer",
            current_symbols=(
                "JudgmentGeometryContribution",
                "EvidencePluralityContribution",
                "ObstructionPersistenceContribution",
                "TrustAlgebraContribution",
            ),
            module_path="jugeo.thesis.semantic_center.main_contributions",
            relation="approximated-by",
            rationale="Each central contribution is modeled as its own analyzer-style class instead of being collapsed into one analyzer.",
        ),
        BlueprintClassBridge(
            blueprint_class="MainContributionsWitness",
            current_symbols=("CONTRIBUTION_CATALOG",),
            module_path="jugeo.thesis.semantic_center.main_contributions",
            relation="approximated-by",
            rationale="The canonical catalog instance is the concrete witness surface for the current contribution module.",
        ),
        BlueprintClassBridge(
            blueprint_class="ProblemClassesAddressedCoordinator",
            current_symbols=("ProblemClassCatalog",),
            module_path="jugeo.thesis.semantic_center.problem_classes_addressed",
            relation="approximated-by",
            rationale="ProblemClassCatalog coordinates the chapter's broad problem atlas today.",
        ),
        BlueprintClassBridge(
            blueprint_class="ProblemClassesAddressedAnalyzer",
            current_symbols=(
                "SemanticVerificationProblem",
                "LongHorizonGenerationProblem",
                "MixedEvidenceProblem",
                "MathematicalIdeationProblem",
            ),
            module_path="jugeo.thesis.semantic_center.problem_classes_addressed",
            relation="approximated-by",
            rationale="The current module uses one analyzer-style class per major problem family.",
        ),
        BlueprintClassBridge(
            blueprint_class="ProblemClassesAddressedWitness",
            current_symbols=("PROBLEM_CLASS_CATALOG",),
            module_path="jugeo.thesis.semantic_center.problem_classes_addressed",
            relation="approximated-by",
            rationale="The canonical problem-class catalog instance is the witness surface currently available.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoPlanner",
            current_symbols=(
                "JuGeoBootstrapAlgorithm",
                "SemanticCenterDetectionAlgorithm",
            ),
            module_path="jugeo.thesis.semantic_center.algorithms",
            relation="approximated-by",
            rationale="Planning behavior is currently split between bootstrap and semantic-center-detection procedures.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoExecutor",
            current_symbols=("ClaimVerificationAlgorithm",),
            module_path="jugeo.thesis.semantic_center.algorithms",
            relation="approximated-by",
            rationale="Execution of chapter-local verification flow is currently centered on ClaimVerificationAlgorithm.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoNormalizer",
            current_symbols=("AlgorithmState", "AlgorithmResult", "AlgorithmStatus"),
            module_path="jugeo.thesis.semantic_center.algorithms",
            relation="approximated-by",
            rationale="Normalization and procedural state reporting currently live in the shared algorithm support types.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoBridge",
            current_symbols=(),
            module_path="jugeo.thesis.semantic_center.integration",
            relation="planned-future-target",
            rationale="The blueprint reserves integration.py for later bridge surfaces; this manifest records the planned name without pretending the file exists yet.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoExportBundle",
            current_symbols=(),
            module_path="jugeo.thesis.semantic_center.integration",
            relation="planned-future-target",
            rationale="Export-bundle behavior is blueprint-implied future work for chapter integration surfaces.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoTheoremSchema",
            current_symbols=(),
            module_path="jugeo.thesis.semantic_center.theorems",
            relation="planned-future-target",
            rationale="Theorems.py is still unrealized, so the theorem-schema class is recorded as future-facing rather than claimed as present.",
        ),
        BlueprintClassBridge(
            blueprint_class="IntroductionJuGeoFalsificationSuite",
            current_symbols=(),
            module_path="jugeo.thesis.semantic_center.theorems",
            relation="planned-future-target",
            rationale="The blueprint promises a falsification suite in the future theorem file; the manifest names it now to keep the frontier explicit.",
        ),
    )


def build_introduction_dependency_map() -> IntroductionJuGeoDependencyMap:
    return IntroductionJuGeoDependencyMap(
        chapter_targets_in_order=CHAPTER_TARGETS_IN_ORDER,
        required_generated_dependencies=REQUIRED_GENERATED_DEPENDENCIES,
        module_dependencies=MODULE_DEPENDENCIES,
        authority_boundaries=TARGET_AUTHORITY_BOUNDARIES,
        stage=CHAPTER_STAGE,
        sequence=CHAPTER_SEQUENCE,
    )


DEPENDENCY_MAP: Final[IntroductionJuGeoDependencyMap] = build_introduction_dependency_map()


def build_introduction_manifest(
    *,
    version: str = "0.1.0",
    shared_manifest: SharedPackageManifest | None = None,
) -> IntroductionJuGeoManifest:
    root_manifest = shared_manifest if shared_manifest is not None else build_package_manifest()
    module_surfaces = _build_module_surfaces()
    public_api = _ordered_unique(
        symbol for surface in module_surfaces for symbol in surface.current_exports
    )
    manifest = IntroductionJuGeoManifest(
        name=PACKAGE_NAME,
        version=version,
        chapter_number=CHAPTER_NUMBER,
        part_number=PART_NUMBER,
        chapter_title=CHAPTER_TITLE,
        package_path=PACKAGE_PATH,
        target_file=TARGET_FILE,
        target_test=TARGET_TEST,
        semantic_sources=(SEMANTIC_SOURCE_TEX, SEMANTIC_SOURCE_PDF),
        structural_hints=(STRUCTURAL_BLUEPRINT, STRUCTURAL_GENERATION_ORDER),
        source_sections=INTRODUCTION_SOURCE_SECTIONS,
        dependency_map=DEPENDENCY_MAP,
        module_surfaces=module_surfaces,
        blueprint_bridges=_build_blueprint_bridges(),
        worldview_commitments=WORLDVIEW_COMMITMENTS,
        main_contributions=MAIN_CONTRIBUTIONS,
        problem_classes=PROBLEM_CLASS_ATLAS,
        chapter_goals=CHAPTER_GOALS,
        public_api=public_api,
        shared_manifest_name=root_manifest.name,
        shared_semantic_source=root_manifest.semantic_source,
        shared_structural_hints=root_manifest.structural_hints,
    )
    manifest.validate_or_raise()
    validate_manifest_shape(manifest.to_dict())
    return manifest


def validate_manifest_shape(payload: Mapping[str, Any]) -> None:
    required = {
        "name",
        "package_name",
        "version",
        "chapter_number",
        "part_number",
        "chapter_title",
        "package_path",
        "target_file",
        "target_test",
        "semantic_sources",
        "structural_hints",
        "source_sections",
        "dependency_map",
        "module_surfaces",
        "blueprint_bridges",
        "worldview_commitments",
        "main_contributions",
        "problem_classes",
        "chapter_goals",
        "public_api",
        "shared_manifest_name",
        "shared_semantic_source",
        "shared_structural_hints",
        "realized_targets",
        "unrealized_targets",
        "summary",
    }
    missing = sorted(required.difference(payload))
    if missing:
        _raise_manifest_error(
            "semantic-center-manifest-missing-fields",
            "semantic_center manifest payload is missing required fields",
            details={"missing": missing},
        )

    if payload["name"] != PACKAGE_NAME:
        _raise_manifest_error(
            "semantic-center-manifest-name",
            "semantic_center manifest payload must preserve its canonical package name",
            details={"name": payload["name"]},
        )

    if tuple(payload["semantic_sources"]) != (SEMANTIC_SOURCE_TEX, SEMANTIC_SOURCE_PDF):
        _raise_manifest_error(
            "semantic-center-manifest-semantic-sources",
            "semantic_center manifest payload must preserve both tex and pdf semantic sources",
            details={"semantic_sources": list(payload["semantic_sources"])},
        )

    if tuple(payload["structural_hints"]) != (
        STRUCTURAL_BLUEPRINT,
        STRUCTURAL_GENERATION_ORDER,
    ):
        _raise_manifest_error(
            "semantic-center-manifest-structural-hints",
            "semantic_center manifest payload must preserve both structural hint files",
            details={"structural_hints": list(payload["structural_hints"])},
        )

    dependency_map = payload["dependency_map"]
    if not isinstance(dependency_map, Mapping):
        _raise_manifest_error(
            "semantic-center-manifest-dependency-map-type",
            "dependency_map must be serialized as a mapping",
            details={"type": type(dependency_map).__name__},
        )

    dependency_required = {
        "chapter_targets_in_order",
        "required_generated_dependencies",
        "module_dependencies",
        "authority_boundaries",
        "stage",
        "sequence",
    }
    missing_dependency = sorted(dependency_required.difference(dependency_map))
    if missing_dependency:
        _raise_manifest_error(
            "semantic-center-manifest-dependency-map-fields",
            "dependency_map is missing required fields",
            details={"missing": missing_dependency},
        )

    module_surfaces = payload["module_surfaces"]
    if not isinstance(module_surfaces, list) or not module_surfaces:
        _raise_manifest_error(
            "semantic-center-manifest-module-surfaces",
            "module_surfaces must be a non-empty list of serialized surfaces",
            details={"type": type(module_surfaces).__name__},
        )
    for index, surface in enumerate(module_surfaces):
        if not isinstance(surface, Mapping):
            _raise_manifest_error(
                "semantic-center-manifest-module-surface-entry",
                "each module surface payload must be a mapping",
                details={"index": index, "type": type(surface).__name__},
            )
        for key in (
            "module_name",
            "module_path",
            "file_path",
            "role",
            "current_exports",
            "authority_boundary",
            "trust_boundary",
        ):
            if key not in surface:
                _raise_manifest_error(
                    "semantic-center-manifest-module-surface-fields",
                    "serialized module surfaces must preserve required keys",
                    details={"index": index, "missing": key},
                )

    blueprint_bridges = payload["blueprint_bridges"]
    if not isinstance(blueprint_bridges, list) or not blueprint_bridges:
        _raise_manifest_error(
            "semantic-center-manifest-blueprint-bridges",
            "blueprint_bridges must be a non-empty list",
            details={"type": type(blueprint_bridges).__name__},
        )

    public_api = payload["public_api"]
    if not isinstance(public_api, list) or not public_api:
        _raise_manifest_error(
            "semantic-center-manifest-public-api",
            "public_api must be a non-empty serialized list",
            details={"type": type(public_api).__name__},
        )
    if len(public_api) != len(set(public_api)):
        _raise_manifest_error(
            "semantic-center-manifest-public-api-duplicates",
            "public_api must not contain duplicate symbols",
            details={"public_api": list(public_api)},
        )


MANIFEST: Final[IntroductionJuGeoManifest] = build_introduction_manifest()
CHAPTER_MANIFEST: Final[IntroductionJuGeoManifest] = MANIFEST
SEMANTIC_CENTER_MANIFEST: Final[IntroductionJuGeoManifest] = MANIFEST

__all__ = list(MANIFEST_EXPORTS)
