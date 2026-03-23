from __future__ import annotations

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
SOURCE_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "s01_judgment_geometry_as_the_semantic.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "thesis" / "semantic_center" / "test_s01_judgment_geometry_as_the_semantic.py"
PACKAGE_INIT_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "__init__.py"
MANIFEST_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "manifest.py"
MODELS_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "models.py"
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
_S01 = _load_source_module(
    "jugeo.thesis.semantic_center.s01_judgment_geometry_as_the_semantic",
    SOURCE_FILE,
)

CoordinateAxis = _S01.CoordinateAxis
OpenCoverElement = _S01.OpenCoverElement
RestrictionMap = _S01.RestrictionMap
GluingCondition = _S01.GluingCondition
SemanticPatchObservation = _S01.SemanticPatchObservation
SemanticOverlapDiscrepancy = _S01.SemanticOverlapDiscrepancy
JudgmentGeometrySemanticCenterWitness = _S01.JudgmentGeometrySemanticCenterWitness
SemanticProductSpace = _S01.SemanticProductSpace
JudgmentGeometryFoundation = _S01.JudgmentGeometryFoundation
SheafTheoreticalBasis = _S01.SheafTheoreticalBasis
JudgmentGeometrySemanticCenterAnalyzer = _S01.JudgmentGeometrySemanticCenterAnalyzer
JudgmentGeometrySemanticCenterCoordinator = _S01.JudgmentGeometrySemanticCenterCoordinator
CoordinatedVerification = _S01.CoordinatedVerification
SemanticCenter = _S01.SemanticCenter
SEMANTIC_CENTER_SPEC_PROVENANCE = _S01.SEMANTIC_CENTER_SPEC_PROVENANCE
SEMANTIC_CENTER_WORLDVIEW_LINES = _S01.SEMANTIC_CENTER_WORLDVIEW_LINES
SEMANTIC_CENTER_RUNTIME_OBJECTS = _S01.SEMANTIC_CENTER_RUNTIME_OBJECTS
SEMANTIC_CENTER_OPERATION_FAMILIES = _S01.SEMANTIC_CENTER_OPERATION_FAMILIES
DEFAULT_COORDINATE_AXES = _S01.DEFAULT_COORDINATE_AXES
DEFAULT_OPEN_COVER = _S01.DEFAULT_OPEN_COVER
DEFAULT_RESTRICTION_MAPS = _S01.DEFAULT_RESTRICTION_MAPS
DEFAULT_GLUING_CONDITIONS = _S01.DEFAULT_GLUING_CONDITIONS
DEFAULT_SEMANTIC_CENTER_COORDINATOR = _S01.DEFAULT_SEMANTIC_CENTER_COORDINATOR


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
def default_coordinator() -> JudgmentGeometrySemanticCenterCoordinator:
    return JudgmentGeometrySemanticCenterCoordinator.build_default()


@pytest.fixture(scope="module")
def semantic_center(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> SemanticCenter:
    return SemanticCenter(default_coordinator)


def _chapter_file_entry(blueprint_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    chapter_entry = next(
        entry for entry in blueprint_payload["chapterDirectories"]
        if entry["path"] == "src/jugeo/thesis/semantic_center"
    )
    return next(entry for entry in chapter_entry["files"] if entry["file"] == "s01_judgment_geometry_as_the_semantic.py")


def _generation_entry(generation_order_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(
        item for item in generation_order_payload["items"]
        if item["target"] == "src/jugeo/thesis/semantic_center/s01_judgment_geometry_as_the_semantic.py"
    )


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


def _module_has_class(file_path: Path, class_name: str) -> bool:
    tree = ast.parse(file_path.read_text())
    return any(isinstance(node, ast.ClassDef) and node.name == class_name for node in tree.body)


def _patch(
    patch_id: str,
    *,
    claim: str,
    clauses: tuple[str, ...],
    trust_level: TrustLevel,
    support_regions: tuple[str, ...] = ("module", "specification"),
    treaty_tags: tuple[str, ...] = ("same-contract",),
    residual_obligations: tuple[str, ...] = (),
) -> SemanticPatchObservation:
    return SemanticPatchObservation(
        patch_id=patch_id,
        coordinate=Coordinate(("src", "jugeo", patch_id), kind=CoordinateKind.REGION),
        target_coordinate=Coordinate(("src", "jugeo", "semantic-center"), kind=CoordinateKind.REGION),
        claim=claim,
        clauses=clauses,
        evidence_keys=(f"evidence:{patch_id}",),
        residual_obligations=residual_obligations,
        support_regions=support_regions,
        treaty_tags=treaty_tags,
        trust_level=trust_level,
        provenance=("pytest",),
        metadata={"patch_id": patch_id},
    )


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_and_dependency_files_exist() -> None:
    for path in (SOURCE_FILE, TEST_FILE, THEORY_TEX, THEORY_PDF, BLUEPRINT_FILE, GEN_ORDER_FILE, MANIFEST_FILE, MODELS_FILE, DESCENT_FILE, TRUST_FILE):
        assert path.exists()
        assert path.stat().st_size > 100


def test_source_file_compiles_and_defines_required_classes() -> None:
    ast.parse(SOURCE_FILE.read_text())
    for class_name in (
        "JudgmentGeometrySemanticCenterCoordinator",
        "JudgmentGeometrySemanticCenterAnalyzer",
        "JudgmentGeometrySemanticCenterWitness",
        "SemanticCenter",
        "JudgmentGeometryFoundation",
        "SheafTheoreticalBasis",
        "SemanticProductSpace",
        "CoordinatedVerification",
        "CoordinateAxis",
        "OpenCoverElement",
        "RestrictionMap",
        "GluingCondition",
        "SemanticPatchObservation",
        "SemanticOverlapDiscrepancy",
    ):
        assert _module_has_class(SOURCE_FILE, class_name)


def test_blueprint_entry_matches_required_file_shape(blueprint_payload: Mapping[str, Any]) -> None:
    entry = _chapter_file_entry(blueprint_payload)
    assert entry["estimatedLoC"] == 230
    assert tuple(entry["classes"]) == (
        "JudgmentGeometrySemanticCenterCoordinator",
        "JudgmentGeometrySemanticCenterAnalyzer",
        "JudgmentGeometrySemanticCenterWitness",
    )
    assert tuple(entry["sectionIndexes"]) == (1,)


def test_generation_order_entry_matches_requested_dependencies(generation_order_payload: Mapping[str, Any]) -> None:
    entry = _generation_entry(generation_order_payload)
    assert entry["sequence"] == 61
    assert entry["scope"] == "chapter"
    assert entry["stage"] == "chapter-01"
    assert entry["dependsOn"] == [
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/geometry/descent.py",
        "src/jugeo/evidence/trust.py",
    ]
    assert entry["test"] == "tests/jugeo/thesis/semantic_center/test_s01_judgment_geometry_as_the_semantic.py"


def test_module_exports_include_new_and_compatibility_surfaces() -> None:
    exports = _read_module_exports(SOURCE_FILE)
    for symbol in (
        "JudgmentGeometrySemanticCenterCoordinator",
        "JudgmentGeometrySemanticCenterAnalyzer",
        "JudgmentGeometrySemanticCenterWitness",
        "SemanticCenter",
        "JudgmentGeometryFoundation",
        "SheafTheoreticalBasis",
        "SemanticProductSpace",
        "CoordinatedVerification",
        "CoordinateAxis",
        "OpenCoverElement",
        "RestrictionMap",
        "GluingCondition",
        "DEFAULT_SEMANTIC_CENTER_COORDINATOR",
    ):
        assert symbol in exports
        assert hasattr(_S01, symbol)


def test_package_init_expected_imports_are_satisfied() -> None:
    init_text = PACKAGE_INIT_FILE.read_text()
    for symbol in (
        "SemanticCenter",
        "JudgmentGeometryFoundation",
        "SheafTheoreticalBasis",
        "SemanticProductSpace",
        "CoordinatedVerification",
        "CoordinateAxis",
        "OpenCoverElement",
        "RestrictionMap",
        "GluingCondition",
    ):
        assert symbol in init_text
        assert hasattr(_S01, symbol)
    with pytest.raises(ImportError) as excinfo:
        _load_source_module("jugeo.thesis.semantic_center", PACKAGE_INIT_FILE)
    assert "ManifestDependency" in str(excinfo.value)


def test_spec_provenance_is_explicit_and_honest() -> None:
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["semantic_source"] == "preliminaries/theory2.tex"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["semantic_source_pdf"] == "preliminaries/theory2.pdf"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["structural_blueprint"] == "theory2-src-blueprint.json"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["structural_generation_order"] == "theory2-generation-order.json"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["target_file"] == "src/jugeo/thesis/semantic_center/s01_judgment_geometry_as_the_semantic.py"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["target_test"] == "tests/jugeo/thesis/semantic_center/test_s01_judgment_geometry_as_the_semantic.py"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["sequence"] == 61
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["stage"] == "chapter-01"
    assert SEMANTIC_CENTER_SPEC_PROVENANCE["section_title"] == "Judgment geometry as the semantic center"


def test_worldview_lines_echo_theory_without_collapsing_trust_boundaries(tex_text: str, pdf_text: str) -> None:
    joined = "\n".join(SEMANTIC_CENTER_WORLDVIEW_LINES).lower()
    assert "single semantic machine" in joined
    assert "judgment state" in joined
    assert "obstruction classes" in joined
    assert "controlled ai proposal" in joined
    assert "semantic machine whose primary object is the judgment state of a project" in tex_text.lower()
    assert "judgment geometry as the semantic center" in pdf_text.lower()


def test_default_surface_is_substantial_and_readable() -> None:
    assert len(DEFAULT_COORDINATE_AXES) >= 8
    assert len(DEFAULT_OPEN_COVER) == 4
    assert len(DEFAULT_RESTRICTION_MAPS) >= 3
    assert len(DEFAULT_GLUING_CONDITIONS) >= 4
    assert "verification" in SEMANTIC_CENTER_OPERATION_FAMILIES
    assert "repair frontier" in SEMANTIC_CENTER_RUNTIME_OBJECTS


def test_default_coordinator_contract_has_foundation_product_space_and_sheaf_basis(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    contract = default_coordinator.runtime_contract()
    assert set(contract) == {"foundation", "product_space", "sheaf_basis"}
    assert contract["foundation"]["worldview_record_id"] == _MODELS.JUGEO_WORLDVIEW.record_id
    assert contract["product_space"]["axes"]
    assert contract["sheaf_basis"]["cover"]


def test_semantic_center_summary_mentions_chapter_and_operations(semantic_center: SemanticCenter) -> None:
    summary = semantic_center.summary().lower()
    assert "chapter: 1 - introduction: what jugeo is" in summary
    assert "judgment geometry" in summary
    assert "verification" in summary
    assert "orchestration" in summary


def test_positive_analysis_produces_publishable_witness(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-proof", claim="The semantic center keeps local and global judgment state aligned.", clauses=("local sections", "global descent"), trust_level=TrustLevel.MECHANICALLY_VERIFIED),
        _patch("patch-solver", claim="The semantic center keeps local and global judgment state aligned.", clauses=("global descent", "local sections"), trust_level=TrustLevel.SOLVER_DISCHARGED),
        _patch("patch-runtime", claim="The semantic center keeps local and global judgment state aligned.", clauses=("local sections", "global descent"), trust_level=TrustLevel.RUNTIME_WITNESSED),
    )
    witness = default_coordinator.coordinate(Coordinate(("src", "jugeo", "semantic-center"), kind=CoordinateKind.REGION), observations, objective="publishable-artifact")
    assert isinstance(witness, JudgmentGeometrySemanticCenterWitness)
    assert witness.publishable is True
    assert witness.is_globally_coherent is True
    assert witness.discrepancies == ()
    assert witness.residual_obligations == ()
    assert witness.trust_level == TrustLevel.RUNTIME_WITNESSED
    assert witness.patch_count == 3
    assert witness.semantic_state["canonical_digest"] == witness.canonical_digest
    assert "jugeo.geometry.descent.GluingData" in witness.provenance


def test_obstruction_path_reports_discrepancies_and_repair_frontier(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-left", claim="A globally coherent artifact descends from compatible local sections.", clauses=("compatible local sections", "global artifact"), trust_level=TrustLevel.RUNTIME_WITNESSED),
        _patch("patch-right", claim="A globally coherent artifact descends from compatible local sections.", clauses=("compatible local sections", "obstruction class"), trust_level=TrustLevel.RUNTIME_WITNESSED, support_regions=("deployment",), treaty_tags=("different-treaty",)),
    )
    witness = default_coordinator.coordinate("src/jugeo/semantic-center", observations)
    assert witness.publishable is False
    assert witness.is_globally_coherent is False
    assert witness.discrepancies
    assert any("clauses" in discrepancy.disagreements for discrepancy in witness.discrepancies)
    assert "local repair within the present cover" in witness.repair_frontier
    assert "change of cover or hypercover" in witness.repair_frontier
    assert "strengthen the overlap treaty" in witness.repair_frontier


def test_residual_obligations_block_publication_even_when_claims_glue(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-proof", claim="Residual obligations are the living part of the system.", clauses=("residual obligations", "living system"), trust_level=TrustLevel.MECHANICALLY_VERIFIED, residual_obligations=("discharge public theorem obligation",)),
        _patch("patch-runtime", claim="Residual obligations are the living part of the system.", clauses=("residual obligations", "living system"), trust_level=TrustLevel.RUNTIME_WITNESSED, residual_obligations=("discharge public theorem obligation",)),
    )
    witness = default_coordinator.coordinate("src/jugeo/semantic-center", observations)
    assert witness.discrepancies == ()
    assert witness.publishable is False
    assert witness.residual_obligations == ("discharge public theorem obligation",)
    assert "discharge residual obligations with admissible evidence" in witness.repair_frontier


def test_oracle_only_trust_stays_below_settlement_floor(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-oracle-a", claim="The semantic center is a single semantic machine.", clauses=("single semantic machine", "judgment state"), trust_level=TrustLevel.ORACLE_PROPOSED),
        _patch("patch-oracle-b", claim="The semantic center is a single semantic machine.", clauses=("single semantic machine", "judgment state"), trust_level=TrustLevel.ORACLE_PROPOSED),
    )
    witness = default_coordinator.coordinate("src/jugeo/semantic-center", observations)
    assert witness.discrepancies == ()
    assert witness.publishable is False
    assert witness.trust_level == TrustLevel.ORACLE_PROPOSED
    assert "raise trust above the settlement floor with explicit non-oracle evidence" in witness.repair_frontier
    assert any("controlled proposal" in note.lower() for note in witness.notes)


def test_sheaf_basis_builds_pairwise_overlaps_for_patch_family(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-a", claim="Judgment geometry is the semantic center.", clauses=("judgment geometry", "semantic center"), trust_level=TrustLevel.SOLVER_DISCHARGED),
        _patch("patch-b", claim="Judgment geometry is the semantic center.", clauses=("judgment geometry", "semantic center"), trust_level=TrustLevel.RUNTIME_WITNESSED),
        _patch("patch-c", claim="Judgment geometry is the semantic center.", clauses=("judgment geometry", "semantic center"), trust_level=TrustLevel.HUMAN_ATTESTED),
    )
    gluing = default_coordinator.sheaf_basis.build_gluing_data(observations)
    assert gluing.patch_count == 3
    assert gluing.overlap_count == 3
    overlaps = gluing.verify_all_overlaps()
    assert all(overlap.status.value == "satisfied" for overlap in overlaps)


def test_stable_serialization_keeps_digest_deterministic(default_coordinator: JudgmentGeometrySemanticCenterCoordinator) -> None:
    observations = (
        _patch("patch-one", claim="Globally coherent artifacts are descended sections.", clauses=("descended sections", "global coherence"), trust_level=TrustLevel.SOLVER_DISCHARGED),
        _patch("patch-two", claim="Globally coherent artifacts are descended sections.", clauses=("global coherence", "descended sections"), trust_level=TrustLevel.HUMAN_ATTESTED),
    )
    witness_a = default_coordinator.coordinate("src/jugeo/semantic-center", observations)
    witness_b = default_coordinator.coordinate("src/jugeo/semantic-center", observations)
    assert witness_a.to_dict() == witness_b.to_dict()
    assert witness_a.canonical_digest == witness_b.canonical_digest


def test_default_singleton_coordinator_is_usable() -> None:
    witness = DEFAULT_SEMANTIC_CENTER_COORDINATOR.coordinate(
        "src/jugeo/semantic-center",
        (
            _patch("singleton-a", claim="Verification and generation are operations on one semantic object.", clauses=("verification", "generation"), trust_level=TrustLevel.HUMAN_ATTESTED),
            _patch("singleton-b", claim="Verification and generation are operations on one semantic object.", clauses=("generation", "verification"), trust_level=TrustLevel.RUNTIME_WITNESSED),
        ),
    )
    assert isinstance(witness, JudgmentGeometrySemanticCenterWitness)
    assert witness.patch_count == 2


def test_source_references_required_dependencies_and_semantic_terms() -> None:
    text = SOURCE_FILE.read_text().lower()
    assert "jugeo.geometry.descent" in text
    assert "jugeo.evidence.trust" in text
    assert "judgment geometry" in text
    assert "semantic center" in text
    assert "obstruction" in text
    assert "local-to-global" in text
    assert "repair frontier" in text


def test_module_is_honest_about_current_surface() -> None:
    text = SOURCE_FILE.read_text()
    assert "Compatibility-friendly top-level semantic-center object" in text
    assert "future dependency graph" in text
    assert "This module is the machine-readable companion" in text
    assert "it does not certify all downstream theorem obligations by itself" in text
