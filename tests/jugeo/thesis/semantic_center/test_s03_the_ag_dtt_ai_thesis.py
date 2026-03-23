from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import ast
import importlib
import importlib.util
import json
import sys
from typing import Any, Mapping

import pypdf
import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src").exists())
SRC = ROOT / "src"
SOURCE_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "s03_the_ag_dtt_ai_thesis.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "thesis" / "semantic_center" / "test_s03_the_ag_dtt_ai_thesis.py"
PACKAGE_INIT_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "__init__.py"
MANIFEST_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "manifest.py"
MODELS_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "models.py"
S01_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "s01_judgment_geometry_as_the_semantic.py"
S02_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "s02_jugeo_relative_to_theorem_provers.py"
DESCENT_FILE = ROOT / "src" / "jugeo" / "geometry" / "descent.py"
TRUST_FILE = ROOT / "src" / "jugeo" / "evidence" / "trust.py"
THEORY_TEX = ROOT / "preliminaries" / "theory2.tex"
THEORY_PDF = ROOT / "preliminaries" / "theory2.pdf"
BLUEPRINT_FILE = ROOT / "theory2-src-blueprint.json"
GEN_ORDER_FILE = ROOT / "theory2-generation-order.json"


def _bootstrap_src_package() -> None:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    importlib.invalidate_caches()


_bootstrap_src_package()

from jugeo.errors import JuGeoError
from jugeo.evidence.trust import TrustLevel
from jugeo.geometry.site import Coordinate, CoordinateKind


def _load_source_module(module_name: str, file_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_MANIFEST = _load_source_module("jugeo.thesis.semantic_center.manifest", MANIFEST_FILE)
_MODELS = _load_source_module("jugeo.thesis.semantic_center.models", MODELS_FILE)
_S01 = _load_source_module("jugeo.thesis.semantic_center.s01_judgment_geometry_as_the_semantic", S01_FILE)
_S02 = _load_source_module("jugeo.thesis.semantic_center.s02_jugeo_relative_to_theorem_provers", S02_FILE)
_S03 = _load_source_module("jugeo.thesis.semantic_center.s03_the_ag_dtt_ai_thesis", SOURCE_FILE)

ThesisComponentKind = _S03.ThesisComponentKind
AGDTTAIObservation = _S03.AGDTTAIObservation
AGDTTAIDiscrepancy = _S03.AGDTTAIDiscrepancy
ComponentInteraction = _S03.ComponentInteraction
AlgebraicGeometryComponent = _S03.AlgebraicGeometryComponent
DependentTypeComponent = _S03.DependentTypeComponent
AIComponent = _S03.AIComponent
ThesisUnification = _S03.ThesisUnification
AGDTTAIThesis = _S03.AGDTTAIThesis
TheAGDTTAIWitness = _S03.TheAGDTTAIWitness
TheAGDTTAIAnalyzer = _S03.TheAGDTTAIAnalyzer
TheAGDTTAICoordinator = _S03.TheAGDTTAICoordinator
S03_SPEC_PROVENANCE = _S03.S03_SPEC_PROVENANCE
THESIS_WORLDVIEW_LINES = _S03.THESIS_WORLDVIEW_LINES
THESIS_RUNTIME_OBJECTS = _S03.THESIS_RUNTIME_OBJECTS
THESIS_AUTHORITY_CENTERS = _S03.THESIS_AUTHORITY_CENTERS
THESIS_COMPONENT_ORDER = _S03.THESIS_COMPONENT_ORDER
DEFAULT_COMPONENT_INTERACTIONS = _S03.DEFAULT_COMPONENT_INTERACTIONS
DEFAULT_THE_AG_DTT_AI_COORDINATOR = _S03.DEFAULT_THE_AG_DTT_AI_COORDINATOR
THE_AG_DTT_AI_THESIS = _S03.THE_AG_DTT_AI_THESIS


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
def coordinator() -> TheAGDTTAICoordinator:
    return TheAGDTTAICoordinator.build_default()


@pytest.fixture(scope="module")
def thesis() -> AGDTTAIThesis:
    return THE_AG_DTT_AI_THESIS


@pytest.fixture(scope="module")
def target_coordinate() -> Coordinate:
    return Coordinate(("src", "jugeo", "thesis", "semantic_center", "s03"), kind=CoordinateKind.REGION)


@pytest.fixture(scope="module")
def default_observations(thesis: AGDTTAIThesis, target_coordinate: Coordinate) -> tuple[AGDTTAIObservation, ...]:
    return thesis.default_observations(target_coordinate)


@pytest.fixture(scope="module")
def default_witness(coordinator: TheAGDTTAICoordinator, target_coordinate: Coordinate, default_observations: tuple[AGDTTAIObservation, ...]) -> TheAGDTTAIWitness:
    return coordinator.coordinate(target_coordinate, default_observations)


def _chapter_file_entry(blueprint_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    chapter_entry = next(entry for entry in blueprint_payload["chapterDirectories"] if entry["path"] == "src/jugeo/thesis/semantic_center")
    return next(entry for entry in chapter_entry["files"] if entry["file"] == "s03_the_ag_dtt_ai_thesis.py")


def _generation_entry(generation_order_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(item for item in generation_order_payload["items"] if item["target"] == "src/jugeo/thesis/semantic_center/s03_the_ag_dtt_ai_thesis.py")


def _read_module_exports(file_path: Path) -> tuple[str, ...]:
    tree = ast.parse(file_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return tuple(element.value for element in node.value.elts if isinstance(element, ast.Constant) and isinstance(element.value, str))
    return ()


def _module_has_class(file_path: Path, class_name: str) -> bool:
    tree = ast.parse(file_path.read_text())
    return any(isinstance(node, ast.ClassDef) and node.name == class_name for node in tree.body)


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().replace("--", " ").replace("—", " ").split())


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_and_dependency_files_exist() -> None:
    for path in (SOURCE_FILE, TEST_FILE, THEORY_TEX, THEORY_PDF, BLUEPRINT_FILE, GEN_ORDER_FILE, MANIFEST_FILE, MODELS_FILE, S01_FILE, S02_FILE, DESCENT_FILE, TRUST_FILE):
        assert path.exists()
        assert path.stat().st_size > 100


def test_source_file_compiles_and_defines_required_classes() -> None:
    ast.parse(SOURCE_FILE.read_text())
    for class_name in ("TheAGDTTAICoordinator", "TheAGDTTAIAnalyzer", "TheAGDTTAIWitness", "AGDTTAIThesis", "ThesisUnification", "AlgebraicGeometryComponent", "DependentTypeComponent", "AIComponent", "ComponentInteraction", "AGDTTAIObservation", "AGDTTAIDiscrepancy"):
        assert _module_has_class(SOURCE_FILE, class_name)


def test_blueprint_entry_matches_required_file_shape(blueprint_payload: Mapping[str, Any]) -> None:
    entry = _chapter_file_entry(blueprint_payload)
    assert entry["estimatedLoC"] == 230
    assert tuple(entry["classes"]) == ("TheAGDTTAICoordinator", "TheAGDTTAIAnalyzer", "TheAGDTTAIWitness")
    assert tuple(entry["sectionIndexes"]) == (3,)


def test_generation_order_entry_matches_requested_dependencies(generation_order_payload: Mapping[str, Any]) -> None:
    entry = _generation_entry(generation_order_payload)
    assert entry["sequence"] == 63
    assert entry["scope"] == "chapter"
    assert entry["stage"] == "chapter-01"
    assert entry["dependsOn"] == ["src/jugeo/thesis/semantic_center/models.py", "src/jugeo/thesis/semantic_center/manifest.py", "src/jugeo/geometry/descent.py", "src/jugeo/evidence/trust.py"]
    assert entry["test"] == "tests/jugeo/thesis/semantic_center/test_s03_the_ag_dtt_ai_thesis.py"


def test_module_exports_include_blueprint_and_package_compatibility_surfaces() -> None:
    exports = _read_module_exports(SOURCE_FILE)
    for symbol in ("TheAGDTTAICoordinator", "TheAGDTTAIAnalyzer", "TheAGDTTAIWitness", "AGDTTAIThesis", "ThesisUnification", "AlgebraicGeometryComponent", "DependentTypeComponent", "AIComponent", "ComponentInteraction", "THE_AG_DTT_AI_THESIS", "DEFAULT_THE_AG_DTT_AI_COORDINATOR"):
        assert symbol in exports
        assert hasattr(_S03, symbol)


def test_package_init_expected_imports_are_satisfied() -> None:
    init_text = PACKAGE_INIT_FILE.read_text()
    for symbol in ("AlgebraicGeometryComponent", "DependentTypeComponent", "AIComponent", "ThesisUnification", "AGDTTAIThesis", "ComponentInteraction", "THE_AG_DTT_AI_THESIS"):
        assert symbol in init_text
        assert hasattr(_S03, symbol)


def test_spec_provenance_is_explicit_and_honest() -> None:
    assert S03_SPEC_PROVENANCE["semantic_source"] == "preliminaries/theory2.tex"
    assert S03_SPEC_PROVENANCE["semantic_source_pdf"] == "preliminaries/theory2.pdf"
    assert S03_SPEC_PROVENANCE["structural_blueprint"] == "theory2-src-blueprint.json"
    assert S03_SPEC_PROVENANCE["structural_generation_order"] == "theory2-generation-order.json"
    assert S03_SPEC_PROVENANCE["target_file"] == "src/jugeo/thesis/semantic_center/s03_the_ag_dtt_ai_thesis.py"
    assert S03_SPEC_PROVENANCE["target_test"] == "tests/jugeo/thesis/semantic_center/test_s03_the_ag_dtt_ai_thesis.py"
    assert S03_SPEC_PROVENANCE["sequence"] == 63
    assert S03_SPEC_PROVENANCE["section_title"] == _MANIFEST.INTRODUCTION_SOURCE_SECTIONS[2]


def test_worldview_lines_quote_theory_and_preserve_authority_boundaries(tex_text: str, pdf_text: str) -> None:
    normalized_tex = _normalize_text(tex_text)
    normalized_pdf = _normalize_text(pdf_text)
    worldview = _normalize_text("\n".join(THESIS_WORLDVIEW_LINES))
    assert "the ag+dtt+ai thesis" in normalized_tex
    assert "the ag+dtt+ai thesis" in normalized_pdf
    assert "project-scale generation and verification" in worldview
    assert "settlement authority" in worldview
    assert "remove any one of these three" in normalized_tex


def test_default_thesis_surface_is_substantial_and_readable(thesis: AGDTTAIThesis) -> None:
    assert thesis.thesis_id == "chapter-01.s03.ag-dtt-ai-thesis"
    assert thesis.section_title == _MANIFEST.INTRODUCTION_SOURCE_SECTIONS[2]
    assert thesis.worldview_record_id == _MODELS.JUGEO_WORLDVIEW.record_id
    assert thesis.supports_worldview()
    assert THESIS_COMPONENT_ORDER == ("ag", "dtt", "ai")
    assert len(THESIS_RUNTIME_OBJECTS) >= 8
    assert len(THESIS_AUTHORITY_CENTERS) >= 10
    assert thesis.unification.requires_all_three_components()


def test_component_records_preserve_distinct_roles_and_boundaries() -> None:
    assert "cover" in " ".join(THE_AG_DTT_AI_THESIS.algebraic_geometry.key_objects).lower()
    assert "context" in " ".join(THE_AG_DTT_AI_THESIS.dependent_type_theory.key_objects).lower()
    assert "proposal" in " ".join(THE_AG_DTT_AI_THESIS.ai.key_objects).lower()
    assert THE_AG_DTT_AI_THESIS.ai.proposal_ceiling == TrustLevel.ORACLE_PROPOSED
    assert THE_AG_DTT_AI_THESIS.ai.prohibits_silent_settlement()


def test_component_interactions_are_explicit_and_future_facing() -> None:
    assert len(DEFAULT_COMPONENT_INTERACTIONS) == 3
    ids = tuple(item.interaction_id for item in DEFAULT_COMPONENT_INTERACTIONS)
    assert ids == ("ag-dtt", "ag-ai", "dtt-ai")
    joined = " ".join(item.semantic_payoff for item in DEFAULT_COMPONENT_INTERACTIONS).lower()
    assert "typed evidence" in joined
    assert "search" in joined


def test_coordinator_runtime_contract_has_thesis_authority_and_component_roles(coordinator: TheAGDTTAICoordinator) -> None:
    contract = coordinator.runtime_contract()
    assert set(contract) == {"thesis", "authority_contract", "component_roles"}
    assert contract["thesis"]["worldview_record_id"] == _MODELS.JUGEO_WORLDVIEW.record_id
    assert "AIRoutingAuthority" in contract["authority_contract"]["authority_centers"]


def test_thesis_summary_mentions_chapter_strictness_and_component_names(thesis: AGDTTAIThesis) -> None:
    summary = thesis.summary().lower()
    assert "chapter: 1 - introduction: what jugeo is" in summary
    assert "the ag+dtt+ai thesis" in summary
    assert "remove any one of these three" in summary
    assert "[ag]" in summary and "[dtt]" in summary and "[ai]" in summary


def test_default_observations_cover_all_three_components(default_observations: tuple[AGDTTAIObservation, ...], target_coordinate: Coordinate) -> None:
    assert len(default_observations) == 3
    assert tuple(item.component for item in default_observations) == (ThesisComponentKind.AG, ThesisComponentKind.DTT, ThesisComponentKind.AI)
    assert all(item.target_coordinate == target_coordinate for item in default_observations)


def test_default_witness_is_publishable_but_not_proof_level(default_witness: TheAGDTTAIWitness) -> None:
    assert default_witness.publishable is True
    assert default_witness.observation_count == 3
    assert default_witness.discrepancy_count == 0
    assert default_witness.trust_level == TrustLevel.HUMAN_ATTESTED


def test_conflicting_clause_produces_discrepancy_and_repair_frontier(coordinator: TheAGDTTAICoordinator, target_coordinate: Coordinate, default_observations: tuple[AGDTTAIObservation, ...]) -> None:
    conflicting = (default_observations[0], replace(default_observations[1], thesis_clause="different-thesis-clause"), default_observations[2])
    witness = coordinator.coordinate(target_coordinate, conflicting)
    assert witness.publishable is False
    assert witness.discrepancy_count >= 1
    assert any("align overlap claims" in item.lower() for item in witness.repair_frontier)


def test_low_trust_or_residual_obligations_block_publication_honestly(coordinator: TheAGDTTAICoordinator, target_coordinate: Coordinate, default_observations: tuple[AGDTTAIObservation, ...]) -> None:
    weakened = (default_observations[0], replace(default_observations[1], residual_obligations=("prove witness transport",)), replace(default_observations[2], trust_level=TrustLevel.ORACLE_PROPOSED))
    witness = coordinator.coordinate(target_coordinate, weakened)
    assert witness.publishable is False
    assert witness.trust_level == TrustLevel.ORACLE_PROPOSED
    assert any("settlement floor" in item for item in witness.residual_obligations)


def test_analyzer_rejects_empty_observation_sets(coordinator: TheAGDTTAICoordinator, target_coordinate: Coordinate) -> None:
    with pytest.raises(JuGeoError):
        coordinator.coordinate(target_coordinate, ())


def test_component_observation_local_section_shape_is_real_and_descent_friendly(target_coordinate: Coordinate) -> None:
    observation = THE_AG_DTT_AI_THESIS.component("ag").as_observation(target_coordinate)
    local_section = observation.to_local_section()
    assert local_section.coordinate.startswith("ag:")
    assert local_section.judgment_data["thesis_clause"] == "strict-ag-dtt-ai-synthesis"
    assert 0.0 <= local_section.trust_level <= 1.0


def test_module_stays_compatible_with_nearby_semantic_center_modules(thesis: AGDTTAIThesis) -> None:
    s01_runtime = tuple(getattr(_S01, "SEMANTIC_CENTER_RUNTIME_OBJECTS"))
    thesis_runtime = tuple(thesis.runtime_objects)
    assert any(item in thesis_runtime for item in s01_runtime)
    assert thesis.worldview_record_id == _MODELS.JUGEO_WORLDVIEW.record_id


def test_theory_contract_matches_manifest_worldview_and_authority_notes(thesis: AGDTTAIThesis) -> None:
    joined_commitments = " ".join(_MANIFEST.WORLDVIEW_COMMITMENTS).lower()
    joined_worldview = " ".join(thesis.worldview_lines).lower()
    assert "ag layer" in joined_commitments or "algebraic-geometric" in joined_commitments
    assert "settlement authority" in joined_worldview


def test_source_text_is_honest_about_scope_and_trust_boundaries() -> None:
    text = SOURCE_FILE.read_text().lower()
    assert "proposal" in text
    assert "settlement authority" in text
    assert "future semantic states" in text


def test_public_api_uses_explicit_mapping_and_digest_surfaces(thesis: AGDTTAIThesis, default_witness: TheAGDTTAIWitness) -> None:
    thesis_payload = thesis.to_dict()
    witness_payload = default_witness.to_dict()
    assert thesis_payload["canonical_digest"] == thesis.canonical_digest
    assert witness_payload["canonical_digest"] == default_witness.canonical_digest


def test_component_lookup_accepts_strings_and_enums(thesis: AGDTTAIThesis) -> None:
    assert thesis.component("ag").component_name == "AG"
    assert thesis.component(ThesisComponentKind.DTT).component_name == "DTT"


def test_discrepancy_summary_is_readable(coordinator: TheAGDTTAICoordinator, target_coordinate: Coordinate, default_observations: tuple[AGDTTAIObservation, ...]) -> None:
    conflict = (replace(default_observations[0], semantic_tags=("geometry-only",)), replace(default_observations[1], semantic_tags=("typing-only",)), default_observations[2])
    witness = coordinator.coordinate(target_coordinate, conflict)
    line = witness.discrepancies[0].summary_line().lower()
    assert "high" in line and "vs" in line


def test_theory_objects_are_json_serializable(thesis: AGDTTAIThesis, default_witness: TheAGDTTAIWitness) -> None:
    json.dumps(thesis.to_dict(), sort_keys=True)
    json.dumps(default_witness.to_dict(), sort_keys=True)


def test_default_coordinator_constant_matches_factory() -> None:
    built = TheAGDTTAICoordinator.build_default()
    assert DEFAULT_THE_AG_DTT_AI_COORDINATOR.thesis.thesis_id == built.thesis.thesis_id
