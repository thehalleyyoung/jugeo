from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import ast
import importlib
import importlib.util
import json
import sys
from typing import Any, Iterable, Mapping

import pypdf
import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src").exists())
SRC = ROOT / "src"
TESTS = ROOT / "tests"
SOURCE_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "manifest.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "thesis" / "semantic_center" / "test_manifest.py"
THEORY_TEX = ROOT / "preliminaries" / "theory2.tex"
THEORY_PDF = ROOT / "preliminaries" / "theory2.pdf"
BLUEPRINT_FILE = ROOT / "theory2-src-blueprint.json"
GEN_ORDER_FILE = ROOT / "theory2-generation-order.json"
CHAPTER_DIR = ROOT / "src" / "jugeo" / "thesis" / "semantic_center"


def _bootstrap_src_package() -> None:
    """Force imports to resolve against ``src/jugeo`` rather than tests."""

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    importlib.invalidate_caches()


_bootstrap_src_package()

from jugeo.errors import JuGeoError
from jugeo.package_manifest import build_package_manifest


def _load_source_module(module_name: str, file_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_MANIFEST_MODULE = _load_source_module(
    "jugeo.thesis.semantic_center.manifest",
    SOURCE_FILE,
)

CHAPTER_GOALS = _MANIFEST_MODULE.CHAPTER_GOALS
CHAPTER_MANIFEST = _MANIFEST_MODULE.CHAPTER_MANIFEST
DEPENDENCY_MAP = _MANIFEST_MODULE.DEPENDENCY_MAP
INTRODUCTION_SOURCE_SECTIONS = _MANIFEST_MODULE.INTRODUCTION_SOURCE_SECTIONS
MAIN_CONTRIBUTIONS = _MANIFEST_MODULE.MAIN_CONTRIBUTIONS
MANIFEST = _MANIFEST_MODULE.MANIFEST
MANIFEST_SPEC_PROVENANCE = _MANIFEST_MODULE.MANIFEST_SPEC_PROVENANCE
PROBLEM_CLASS_ATLAS = _MANIFEST_MODULE.PROBLEM_CLASS_ATLAS
SEMANTIC_CENTER_MANIFEST = _MANIFEST_MODULE.SEMANTIC_CENTER_MANIFEST
WORLDVIEW_COMMITMENTS = _MANIFEST_MODULE.WORLDVIEW_COMMITMENTS
BlueprintClassBridge = _MANIFEST_MODULE.BlueprintClassBridge
IntroductionJuGeoDependencyMap = _MANIFEST_MODULE.IntroductionJuGeoDependencyMap
IntroductionJuGeoManifest = _MANIFEST_MODULE.IntroductionJuGeoManifest
IntroductionModuleSurface = _MANIFEST_MODULE.IntroductionModuleSurface
ManifestDependencyMap = _MANIFEST_MODULE.ManifestDependencyMap
PackageManifest = _MANIFEST_MODULE.PackageManifest
build_introduction_dependency_map = _MANIFEST_MODULE.build_introduction_dependency_map
build_introduction_manifest = _MANIFEST_MODULE.build_introduction_manifest
validate_manifest_shape = _MANIFEST_MODULE.validate_manifest_shape


@pytest.fixture(scope="module")
def manifest() -> IntroductionJuGeoManifest:
    return MANIFEST


@pytest.fixture(scope="module")
def dependency_map() -> IntroductionJuGeoDependencyMap:
    return DEPENDENCY_MAP


@pytest.fixture(scope="module")
def manifest_payload(manifest: IntroductionJuGeoManifest) -> dict[str, Any]:
    return manifest.to_dict()


@pytest.fixture(scope="module")
def blueprint_payload() -> dict[str, Any]:
    return json.loads(BLUEPRINT_FILE.read_text())


@pytest.fixture(scope="module")
def generation_order_payload() -> dict[str, Any]:
    return json.loads(GEN_ORDER_FILE.read_text())


@pytest.fixture(scope="module")
def tex_text() -> str:
    return THEORY_TEX.read_text()


@pytest.fixture(scope="module")
def pdf_text() -> str:
    reader = pypdf.PdfReader(str(THEORY_PDF))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.fixture(scope="module")
def shared_package_manifest() -> Any:
    return build_package_manifest()


def _chapter_blueprint_entry(blueprint_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(
        entry
        for entry in blueprint_payload["chapterDirectories"]
        if entry["path"] == "src/jugeo/thesis/semantic_center"
    )


def _chapter_file_entry(
    blueprint_payload: Mapping[str, Any], *, file_name: str
) -> Mapping[str, Any]:
    chapter_entry = _chapter_blueprint_entry(blueprint_payload)
    return next(entry for entry in chapter_entry["files"] if entry["file"] == file_name)


def _generation_entry(
    generation_order_payload: Mapping[str, Any], *, target: str
) -> Mapping[str, Any]:
    return next(item for item in generation_order_payload["items"] if item["target"] == target)


def _read_module_exports(file_path: Path) -> tuple[str, ...]:
    tree = ast.parse(file_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if not isinstance(node.value, (ast.List, ast.Tuple)):
                    return ()
                values: list[str] = []
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        values.append(element.value)
                return tuple(values)
    return ()


def _bridge_map(
    manifest: IntroductionJuGeoManifest,
) -> dict[str, BlueprintClassBridge]:
    return {bridge.blueprint_class: bridge for bridge in manifest.blueprint_bridges}


def _surface_map(
    manifest: IntroductionJuGeoManifest,
) -> dict[str, IntroductionModuleSurface]:
    return {surface.module_name: surface for surface in manifest.module_surfaces}


def _section_surface_titles(manifest: IntroductionJuGeoManifest) -> tuple[str, ...]:
    return tuple(
        surface.source_sections[0]
        for surface in manifest.section_modules()
        if len(surface.source_sections) == 1
    )


def _payload_lists_are_text(values: Iterable[Any]) -> None:
    for value in values:
        assert isinstance(value, str)
        assert value.strip()


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_files_exist_and_are_nontrivial() -> None:
    for path in (THEORY_TEX, THEORY_PDF, BLUEPRINT_FILE, GEN_ORDER_FILE):
        assert path.exists()
        assert path.stat().st_size > 100


def test_canonical_aliases_preserve_expected_manifest_types() -> None:
    assert PackageManifest is IntroductionJuGeoManifest
    assert ManifestDependencyMap is IntroductionJuGeoDependencyMap
    assert CHAPTER_MANIFEST is MANIFEST
    assert SEMANTIC_CENTER_MANIFEST is MANIFEST


def test_manifest_and_dependency_map_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        MANIFEST.name = "other"
    with pytest.raises(FrozenInstanceError):
        DEPENDENCY_MAP.stage = "other-stage"


def test_manifest_provenance_preserves_semantic_and_structural_sources() -> None:
    assert MANIFEST_SPEC_PROVENANCE["semantic_source"] == "preliminaries/theory2.tex"
    assert MANIFEST_SPEC_PROVENANCE["semantic_source_role"] == "authoritative-semantic-source"
    assert MANIFEST_SPEC_PROVENANCE["semantic_source_pdf"] == "preliminaries/theory2.pdf"
    assert MANIFEST_SPEC_PROVENANCE["semantic_pdf_role"] == "compiled-reference-witness"
    assert MANIFEST_SPEC_PROVENANCE["structural_blueprint"] == "theory2-src-blueprint.json"
    assert MANIFEST_SPEC_PROVENANCE["structural_generation_order"] == "theory2-generation-order.json"
    assert MANIFEST_SPEC_PROVENANCE["structural_hint_role"] == "structure-only"
    assert MANIFEST_SPEC_PROVENANCE["target_file"] == "src/jugeo/thesis/semantic_center/manifest.py"
    assert MANIFEST_SPEC_PROVENANCE["target_test"] == "tests/jugeo/thesis/semantic_center/test_manifest.py"
    assert MANIFEST_SPEC_PROVENANCE["stage"] == "chapter-01"
    assert MANIFEST_SPEC_PROVENANCE["sequence"] == 59
    assert MANIFEST_SPEC_PROVENANCE["chapter_number"] == 1
    assert MANIFEST_SPEC_PROVENANCE["part_number"] == 1
    assert MANIFEST_SPEC_PROVENANCE["chapter_title"] == "Introduction: What JuGeo is"
    assert MANIFEST_SPEC_PROVENANCE["shared_package_semantic_source"] == "preliminaries/theory2.tex"


def test_blueprint_chapter_entry_matches_requested_metadata(
    blueprint_payload: Mapping[str, Any],
) -> None:
    entry = _chapter_blueprint_entry(blueprint_payload)
    assert entry["chapterNumber"] == 1
    assert entry["partNumber"] == 1
    assert entry["title"] == "Introduction: What JuGeo is"
    assert entry["path"] == "src/jugeo/thesis/semantic_center"
    assert tuple(entry["sourceSections"]) == INTRODUCTION_SOURCE_SECTIONS


def test_blueprint_manifest_entry_matches_requested_classes(
    blueprint_payload: Mapping[str, Any],
) -> None:
    entry = _chapter_file_entry(blueprint_payload, file_name="manifest.py")
    assert entry["estimatedLoC"] == 185
    assert tuple(entry["classes"]) == (
        "IntroductionJuGeoManifest",
        "IntroductionJuGeoDependencyMap",
    )


def test_generation_order_entry_matches_requested_stage_and_dependencies(
    generation_order_payload: Mapping[str, Any],
) -> None:
    entry = _generation_entry(
        generation_order_payload,
        target="src/jugeo/thesis/semantic_center/manifest.py",
    )
    assert entry["sequence"] == 59
    assert entry["scope"] == "chapter"
    assert entry["stage"] == "chapter-01"
    assert entry["dependsOn"] == [
        "src/jugeo/package_manifest.py",
        "src/jugeo/errors.py",
    ]
    assert entry["test"] == "tests/jugeo/thesis/semantic_center/test_manifest.py"
    assert entry["chapterNumber"] == 1


def test_dependency_map_preserves_chapter_target_order(
    dependency_map: IntroductionJuGeoDependencyMap,
) -> None:
    assert dependency_map.chapter_targets_in_order == (
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/s01_judgment_geometry_as_the_semantic.py",
        "src/jugeo/thesis/semantic_center/s02_jugeo_relative_to_theorem_provers.py",
        "src/jugeo/thesis/semantic_center/s03_the_ag_dtt_ai_thesis.py",
        "src/jugeo/thesis/semantic_center/s04_main_contributions.py",
        "src/jugeo/thesis/semantic_center/s05_problem_classes_addressed.py",
        "src/jugeo/thesis/semantic_center/algorithms.py",
        "src/jugeo/thesis/semantic_center/integration.py",
        "src/jugeo/thesis/semantic_center/theorems.py",
    )
    assert dependency_map.required_generated_dependencies == (
        "src/jugeo/package_manifest.py",
        "src/jugeo/errors.py",
    )
    assert dependency_map.stage == "chapter-01"
    assert dependency_map.sequence == 59


def test_dependency_map_immediate_dependencies_match_generation_wave(
    dependency_map: IntroductionJuGeoDependencyMap,
) -> None:
    assert dependency_map.immediate_dependencies(
        "src/jugeo/thesis/semantic_center/manifest.py"
    ) == (
        "src/jugeo/package_manifest.py",
        "src/jugeo/errors.py",
    )
    assert dependency_map.immediate_dependencies(
        "src/jugeo/thesis/semantic_center/models.py"
    ) == ("src/jugeo/thesis/semantic_center/manifest.py",)
    assert dependency_map.immediate_dependencies(
        "src/jugeo/thesis/semantic_center/algorithms.py"
    ) == (
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/s01_judgment_geometry_as_the_semantic.py",
        "src/jugeo/thesis/semantic_center/s02_jugeo_relative_to_theorem_provers.py",
        "src/jugeo/thesis/semantic_center/s03_the_ag_dtt_ai_thesis.py",
    )


def test_dependency_map_transitive_dependencies_keep_future_files_honest(
    dependency_map: IntroductionJuGeoDependencyMap,
) -> None:
    theorem_dependencies = dependency_map.transitive_dependencies(
        "src/jugeo/thesis/semantic_center/theorems.py"
    )
    assert "src/jugeo/thesis/semantic_center/algorithms.py" in theorem_dependencies
    assert "src/jugeo/thesis/semantic_center/models.py" in theorem_dependencies
    assert "src/jugeo/package_manifest.py" in theorem_dependencies
    assert "src/jugeo/errors.py" in theorem_dependencies


def test_dependency_map_dependency_edges_and_dependents_are_deterministic(
    dependency_map: IntroductionJuGeoDependencyMap,
) -> None:
    edges = dependency_map.dependency_edges()
    assert (
        "src/jugeo/package_manifest.py",
        "src/jugeo/thesis/semantic_center/manifest.py",
    ) in edges
    assert (
        "src/jugeo/thesis/semantic_center/algorithms.py",
        "src/jugeo/thesis/semantic_center/integration.py",
    ) in edges
    dependents = dependency_map.downstream_dependents(
        "src/jugeo/thesis/semantic_center/models.py"
    )
    assert "src/jugeo/thesis/semantic_center/algorithms.py" in dependents
    assert "src/jugeo/thesis/semantic_center/theorems.py" in dependents


def test_dependency_map_validate_targets_accepts_declared_frontier(
    dependency_map: IntroductionJuGeoDependencyMap,
) -> None:
    known_targets = set(dependency_map.chapter_targets_in_order)
    known_targets.update(dependency_map.required_generated_dependencies)
    assert dependency_map.validate_targets(known_targets) == ()


def test_build_dependency_map_returns_same_shape_as_constant() -> None:
    built = build_introduction_dependency_map()
    assert built.to_dict() == DEPENDENCY_MAP.to_dict()


def test_manifest_exposes_expected_top_level_metadata(
    manifest: IntroductionJuGeoManifest,
) -> None:
    assert manifest.name == "jugeo.thesis.semantic_center"
    assert manifest.package_name == manifest.name
    assert manifest.version == "0.1.0"
    assert manifest.chapter_number == 1
    assert manifest.part_number == 1
    assert manifest.chapter_title == "Introduction: What JuGeo is"
    assert manifest.package_path == "src/jugeo/thesis/semantic_center"
    assert manifest.target_file == "src/jugeo/thesis/semantic_center/manifest.py"
    assert manifest.target_test == "tests/jugeo/thesis/semantic_center/test_manifest.py"
    assert manifest.semantic_sources == (
        "preliminaries/theory2.tex",
        "preliminaries/theory2.pdf",
    )
    assert manifest.structural_hints == (
        "theory2-src-blueprint.json",
        "theory2-generation-order.json",
    )
    assert manifest.source_sections == INTRODUCTION_SOURCE_SECTIONS


def test_manifest_aligns_with_root_package_manifest(
    manifest: IntroductionJuGeoManifest,
    shared_package_manifest: Any,
) -> None:
    assert manifest.shared_manifest_name == shared_package_manifest.name
    assert manifest.shared_manifest_name == "jugeo"
    assert manifest.shared_semantic_source == shared_package_manifest.semantic_source
    assert manifest.shared_semantic_source == "preliminaries/theory2.tex"
    assert manifest.shared_structural_hints == shared_package_manifest.structural_hints
    assert manifest.shared_structural_hints == (
        "theory2-src-blueprint.json",
        "theory2-generation-order.json",
    )


def test_manifest_realized_and_unrealized_targets_are_explicit(
    manifest: IntroductionJuGeoManifest,
) -> None:
    assert manifest.realized_target_paths() == (
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/s01_judgment_geometry_as_the_semantic.py",
        "src/jugeo/thesis/semantic_center/s02_jugeo_relative_to_theorem_provers.py",
        "src/jugeo/thesis/semantic_center/s03_the_ag_dtt_ai_thesis.py",
        "src/jugeo/thesis/semantic_center/s04_main_contributions.py",
        "src/jugeo/thesis/semantic_center/s05_problem_classes_addressed.py",
        "src/jugeo/thesis/semantic_center/algorithms.py",
    )
    assert manifest.unrealized_target_paths() == (
        "src/jugeo/thesis/semantic_center/integration.py",
        "src/jugeo/thesis/semantic_center/theorems.py",
    )


def test_module_surfaces_cover_manifest_models_sections_and_algorithms(
    manifest: IntroductionJuGeoManifest,
) -> None:
    surface_map = _surface_map(manifest)
    assert tuple(surface_map) == (
        "manifest",
        "models",
        "s01_judgment_geometry_as_the_semantic",
        "s02_jugeo_relative_to_theorem_provers",
        "s03_the_ag_dtt_ai_thesis",
        "s04_main_contributions",
        "s05_problem_classes_addressed",
        "algorithms",
    )
    assert set(manifest.module_paths()) == {
        "jugeo.thesis.semantic_center.manifest",
        "jugeo.thesis.semantic_center.models",
        "jugeo.thesis.semantic_center.s01_judgment_geometry_as_the_semantic",
        "jugeo.thesis.semantic_center.s02_jugeo_relative_to_theorem_provers",
        "jugeo.thesis.semantic_center.s03_the_ag_dtt_ai_thesis",
        "jugeo.thesis.semantic_center.s04_main_contributions",
        "jugeo.thesis.semantic_center.s05_problem_classes_addressed",
        "jugeo.thesis.semantic_center.algorithms",
    }


def test_section_modules_preserve_authoritative_source_section_order(
    manifest: IntroductionJuGeoManifest,
) -> None:
    assert _section_surface_titles(manifest) == INTRODUCTION_SOURCE_SECTIONS
    assert tuple(surface.role for surface in manifest.section_modules()) == (
        "section-01-semantic-center",
        "section-02-comparative-positioning",
        "section-03-ag-dtt-ai",
        "section-04-contributions",
        "section-05-problem-classes",
    )


def test_surface_file_paths_exist_for_realized_modules(
    manifest: IntroductionJuGeoManifest,
) -> None:
    for surface in manifest.module_surfaces:
        path = ROOT / surface.file_path
        assert path.exists(), surface.file_path
        assert path.stat().st_size > 100


def test_surface_current_exports_match_live_module_all_values(
    manifest: IntroductionJuGeoManifest,
) -> None:
    surface_map = _surface_map(manifest)
    assert surface_map["models"].current_exports == _read_module_exports(
        CHAPTER_DIR / "models.py"
    )
    assert surface_map["algorithms"].current_exports == _read_module_exports(
        CHAPTER_DIR / "algorithms.py"
    )
    assert surface_map["s01_judgment_geometry_as_the_semantic"].current_exports == _read_module_exports(
        CHAPTER_DIR / "s01_judgment_geometry_as_the_semantic.py"
    )
    assert surface_map["s02_jugeo_relative_to_theorem_provers"].current_exports == _read_module_exports(
        CHAPTER_DIR / "s02_jugeo_relative_to_theorem_provers.py"
    )
    assert surface_map["s03_the_ag_dtt_ai_thesis"].current_exports == _read_module_exports(
        CHAPTER_DIR / "s03_the_ag_dtt_ai_thesis.py"
    )
    assert surface_map["s04_main_contributions"].current_exports == _read_module_exports(
        CHAPTER_DIR / "s04_main_contributions.py"
    )
    assert surface_map["s05_problem_classes_addressed"].current_exports == _read_module_exports(
        CHAPTER_DIR / "s05_problem_classes_addressed.py"
    )


def test_surface_boundaries_and_focus_are_populated(
    manifest: IntroductionJuGeoManifest,
) -> None:
    for surface in manifest.module_surfaces:
        assert isinstance(surface.authority_boundary, str)
        assert surface.authority_boundary.strip()
        assert isinstance(surface.trust_boundary, str)
        assert surface.trust_boundary.strip()
        assert surface.semantic_focus
        _payload_lists_are_text(surface.semantic_focus)
        assert surface.current_exports
        _payload_lists_are_text(surface.current_exports)


def test_blueprint_bridges_cover_present_and_future_blueprint_classes(
    manifest: IntroductionJuGeoManifest,
    blueprint_payload: Mapping[str, Any],
) -> None:
    chapter_entry = _chapter_blueprint_entry(blueprint_payload)
    blueprint_classes = {
        class_name
        for file_entry in chapter_entry["files"]
        for class_name in file_entry["classes"]
    }
    bridge_map = _bridge_map(manifest)
    assert blueprint_classes == set(bridge_map)
    assert len(bridge_map) >= 20


def test_blueprint_bridges_capture_current_manifest_classes_exactly(
    manifest: IntroductionJuGeoManifest,
) -> None:
    bridge_map = _bridge_map(manifest)
    assert bridge_map["IntroductionJuGeoManifest"].relation == "implemented-by"
    assert bridge_map["IntroductionJuGeoManifest"].current_symbols == (
        "IntroductionJuGeoManifest",
    )
    assert bridge_map["IntroductionJuGeoDependencyMap"].relation == "implemented-by"
    assert bridge_map["IntroductionJuGeoDependencyMap"].current_symbols == (
        "IntroductionJuGeoDependencyMap",
    )


def test_blueprint_bridges_are_honest_about_future_integration_and_theorems(
    manifest: IntroductionJuGeoManifest,
) -> None:
    bridge_map = _bridge_map(manifest)
    assert bridge_map["IntroductionJuGeoBridge"].is_planned_only
    assert bridge_map["IntroductionJuGeoBridge"].current_symbols == ()
    assert bridge_map["IntroductionJuGeoExportBundle"].is_planned_only
    assert bridge_map["IntroductionJuGeoTheoremSchema"].is_planned_only
    assert bridge_map["IntroductionJuGeoFalsificationSuite"].is_planned_only


def test_blueprint_bridges_reference_known_planned_module_paths(
    manifest: IntroductionJuGeoManifest,
) -> None:
    planned_paths = set(manifest.dependency_map.planned_module_paths())
    for bridge in manifest.blueprint_bridges:
        assert bridge.module_path in planned_paths
        assert bridge.relation
        assert bridge.rationale.strip()


def test_public_api_contains_manifest_and_neighbor_exports(
    manifest: IntroductionJuGeoManifest,
) -> None:
    api = set(manifest.public_api)
    expected = {
        "IntroductionJuGeoManifest",
        "IntroductionJuGeoDependencyMap",
        "BlueprintClassBridge",
        "IntroductionModuleSurface",
        "JuGeoWorldview",
        "ThesisClaim",
        "SemanticCenter",
        "ComparativePositioning",
        "AGDTTAIThesis",
        "ContributionCatalog",
        "ProblemClassCatalog",
        "JuGeoBootstrapAlgorithm",
        "ClaimVerificationAlgorithm",
    }
    assert expected.issubset(api)
    assert len(manifest.public_api) == len(api)


def test_export_index_points_symbols_to_expected_modules(
    manifest: IntroductionJuGeoManifest,
) -> None:
    export_index = manifest.export_index
    assert export_index["JuGeoWorldview"] == "jugeo.thesis.semantic_center.models"
    assert export_index["SemanticCenter"] == "jugeo.thesis.semantic_center.s01_judgment_geometry_as_the_semantic"
    assert export_index["ComparativePositioning"] == "jugeo.thesis.semantic_center.s02_jugeo_relative_to_theorem_provers"
    assert export_index["AGDTTAIThesis"] == "jugeo.thesis.semantic_center.s03_the_ag_dtt_ai_thesis"
    assert export_index["ContributionCatalog"] == "jugeo.thesis.semantic_center.s04_main_contributions"
    assert export_index["ProblemClassCatalog"] == "jugeo.thesis.semantic_center.s05_problem_classes_addressed"
    assert export_index["ClaimVerificationAlgorithm"] == "jugeo.thesis.semantic_center.algorithms"


def test_manifest_validation_and_payload_validation_succeed(
    manifest: IntroductionJuGeoManifest,
    manifest_payload: Mapping[str, Any],
) -> None:
    assert manifest.validate() == ()
    manifest.validate_or_raise()
    validate_manifest_shape(manifest_payload)


def test_manifest_payload_is_json_serializable(
    manifest: IntroductionJuGeoManifest,
    manifest_payload: Mapping[str, Any],
) -> None:
    dumped = json.dumps(manifest_payload, sort_keys=True)
    assert "jugeo.thesis.semantic_center" in dumped
    assert json.loads(manifest.to_json())["name"] == "jugeo.thesis.semantic_center"


def test_validate_manifest_shape_rejects_missing_required_field(
    manifest_payload: Mapping[str, Any],
) -> None:
    bad_payload = dict(manifest_payload)
    bad_payload.pop("summary")
    with pytest.raises(JuGeoError):
        validate_manifest_shape(bad_payload)


def test_validate_manifest_shape_rejects_duplicate_public_api_entries(
    manifest_payload: Mapping[str, Any],
) -> None:
    bad_payload = dict(manifest_payload)
    bad_payload["public_api"] = list(manifest_payload["public_api"]) + [
        manifest_payload["public_api"][0]
    ]
    with pytest.raises(JuGeoError):
        validate_manifest_shape(bad_payload)


def test_build_introduction_manifest_round_trips_to_constant(
    shared_package_manifest: Any,
) -> None:
    rebuilt = build_introduction_manifest(shared_manifest=shared_package_manifest)
    assert rebuilt.to_dict() == MANIFEST.to_dict()


def test_summary_mentions_worldview_and_realized_module_surface(
    manifest: IntroductionJuGeoManifest,
) -> None:
    summary = manifest.summary()
    assert "judgment-geometry machine" in summary
    assert "AG + DTT + AI" in summary
    assert "no-silent-promotion" in summary
    assert "models" in summary
    assert "integration.py, theorems.py" in summary


def test_worldview_commitments_track_theory2_worldview() -> None:
    assert len(WORLDVIEW_COMMITMENTS) >= 7
    worldview_text = " ".join(WORLDVIEW_COMMITMENTS).lower()
    assert "judgment geometry" in worldview_text
    assert "ag" in worldview_text
    assert "dtt" in worldview_text
    assert "ai" in worldview_text
    assert "descent" in worldview_text or "sheafification" in worldview_text
    assert "evidence provenance" in worldview_text
    assert "no silent trust promotion" in worldview_text


def test_main_contributions_preserve_the_five_core_claims() -> None:
    assert len(MAIN_CONTRIBUTIONS) == 5
    contributions_text = " ".join(MAIN_CONTRIBUTIONS).lower()
    assert "judgment geometry" in contributions_text
    assert "mixed-evidence" in contributions_text or "mixed-evidence discipline" in contributions_text
    assert "large-codebase generation" in contributions_text or "large-codebase" in contributions_text
    assert "mathematical discovery" in contributions_text
    assert "implementation-guiding" in contributions_text


def test_problem_class_atlas_tracks_breadth_without_claiming_equal_maturity() -> None:
    assert len(PROBLEM_CLASS_ATLAS) >= 10
    atlas = set(PROBLEM_CLASS_ATLAS)
    assert "specification satisfaction" in atlas
    assert "bug finding" in atlas
    assert "equivalence and refinement" in atlas
    assert "repair and transformation" in atlas
    assert "documentation alignment" in atlas
    assert "regression closure" in atlas
    assert "public-surface honesty" in atlas
    assert "performance reasoning" in atlas
    assert "concurrency and distributed failures" in atlas
    assert "large-scale code generation" in atlas
    assert "purpose-directed mathematical ideation" in atlas


def test_chapter_goals_are_developer_facing_and_implementation_guiding() -> None:
    assert len(CHAPTER_GOALS) >= 5
    for goal in CHAPTER_GOALS:
        assert isinstance(goal, str)
        assert goal.strip()
        assert len(goal.split()) >= 8


def test_tex_contains_all_authoritative_section_titles_in_order(
    tex_text: str,
) -> None:
    indices = [tex_text.index(title) for title in INTRODUCTION_SOURCE_SECTIONS]
    assert indices == sorted(indices)
    for title in INTRODUCTION_SOURCE_SECTIONS:
        assert title in tex_text


def test_pdf_contains_all_authoritative_section_titles(
    pdf_text: str,
) -> None:
    for title in INTRODUCTION_SOURCE_SECTIONS:
        assert title in pdf_text


def test_tex_and_pdf_support_manifest_section_order(
    tex_text: str,
    pdf_text: str,
    manifest: IntroductionJuGeoManifest,
) -> None:
    for title in _section_surface_titles(manifest):
        assert title in tex_text
        assert title in pdf_text


def test_manifest_source_sections_match_live_section_surface_titles(
    manifest: IntroductionJuGeoManifest,
) -> None:
    assert manifest.source_sections == _section_surface_titles(manifest)


def test_manifest_problem_classes_are_project_goals_shaped_by_theory(
    manifest: IntroductionJuGeoManifest,
) -> None:
    atlas = set(manifest.problem_classes)
    assert set(PROBLEM_CLASS_ATLAS).issubset(atlas)
    assert "public-surface honesty" in atlas
    assert "purpose-directed mathematical ideation" in atlas


def test_manifest_module_surface_map_is_complete_and_readable(
    manifest: IntroductionJuGeoManifest,
) -> None:
    surface_map = manifest.module_surface_map
    assert set(surface_map) == {
        "manifest",
        "models",
        "s01_judgment_geometry_as_the_semantic",
        "s02_jugeo_relative_to_theorem_provers",
        "s03_the_ag_dtt_ai_thesis",
        "s04_main_contributions",
        "s05_problem_classes_addressed",
        "algorithms",
    }
    for surface in surface_map.values():
        assert surface.module_path.startswith("jugeo.thesis.semantic_center")
        assert surface.file_path.startswith("src/jugeo/thesis/semantic_center")


def test_live_neighbor_modules_import_successfully() -> None:
    module_targets = (
        ("jugeo.thesis.semantic_center.models", CHAPTER_DIR / "models.py"),
        ("jugeo.thesis.semantic_center.algorithms", CHAPTER_DIR / "algorithms.py"),
        (
            "jugeo.thesis.semantic_center.s01_judgment_geometry_as_the_semantic",
            CHAPTER_DIR / "s01_judgment_geometry_as_the_semantic.py",
        ),
        (
            "jugeo.thesis.semantic_center.s02_jugeo_relative_to_theorem_provers",
            CHAPTER_DIR / "s02_jugeo_relative_to_theorem_provers.py",
        ),
        (
            "jugeo.thesis.semantic_center.s03_the_ag_dtt_ai_thesis",
            CHAPTER_DIR / "s03_the_ag_dtt_ai_thesis.py",
        ),
        (
            "jugeo.thesis.semantic_center.s04_main_contributions",
            CHAPTER_DIR / "s04_main_contributions.py",
        ),
        (
            "jugeo.thesis.semantic_center.s05_problem_classes_addressed",
            CHAPTER_DIR / "s05_problem_classes_addressed.py",
        ),
    )
    for module_name, file_path in module_targets:
        module = _load_source_module(module_name, file_path)
        exports = getattr(module, "__all__")
        assert isinstance(exports, list)
        assert exports


def test_manifest_bridge_relations_capture_current_symbol_sets(
    manifest: IntroductionJuGeoManifest,
) -> None:
    bridge_map = _bridge_map(manifest)
    assert bridge_map["IntroductionJuGeoRecord"].current_symbols == (
        "JuGeoWorldview",
        "ThesisClaim",
        "ContributionRecord",
        "ProblemClass",
    )
    assert bridge_map["MainContributionsCoordinator"].current_symbols == (
        "ContributionCatalog",
    )
    assert bridge_map["IntroductionJuGeoPlanner"].current_symbols == (
        "JuGeoBootstrapAlgorithm",
        "SemanticCenterDetectionAlgorithm",
    )


def test_manifest_payload_lists_are_nonempty_textual_sequences(
    manifest_payload: Mapping[str, Any],
) -> None:
    _payload_lists_are_text(manifest_payload["semantic_sources"])
    _payload_lists_are_text(manifest_payload["structural_hints"])
    _payload_lists_are_text(manifest_payload["source_sections"])
    _payload_lists_are_text(manifest_payload["worldview_commitments"])
    _payload_lists_are_text(manifest_payload["main_contributions"])
    _payload_lists_are_text(manifest_payload["problem_classes"])
    _payload_lists_are_text(manifest_payload["chapter_goals"])
    _payload_lists_are_text(manifest_payload["public_api"])


def test_manifest_payload_tracks_realized_and_unrealized_targets(
    manifest_payload: Mapping[str, Any],
) -> None:
    assert manifest_payload["realized_targets"] == list(MANIFEST.realized_target_paths())
    assert manifest_payload["unrealized_targets"] == [
        "src/jugeo/thesis/semantic_center/integration.py",
        "src/jugeo/thesis/semantic_center/theorems.py",
    ]


def test_manifest_summary_is_exposed_in_payload(
    manifest_payload: Mapping[str, Any],
) -> None:
    summary = manifest_payload["summary"]
    assert isinstance(summary, str)
    assert "judgment-geometry machine" in summary
    assert "future blueprint frontier" in summary


def test_neighbor_module_surface_problem_class_notes_are_nonempty(
    manifest: IntroductionJuGeoManifest,
) -> None:
    for surface in manifest.module_surfaces:
        assert surface.problem_classes
        _payload_lists_are_text(surface.problem_classes)


def test_manifest_does_not_claim_future_files_are_realized(
    manifest: IntroductionJuGeoManifest,
) -> None:
    realized = set(manifest.realized_target_paths())
    assert "src/jugeo/thesis/semantic_center/integration.py" not in realized
    assert "src/jugeo/thesis/semantic_center/theorems.py" not in realized
    unrealized = set(manifest.unrealized_target_paths())
    assert unrealized == {
        "src/jugeo/thesis/semantic_center/integration.py",
        "src/jugeo/thesis/semantic_center/theorems.py",
    }
