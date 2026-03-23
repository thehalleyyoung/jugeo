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
SOURCE_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "models.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "thesis" / "semantic_center" / "test_models.py"
MANIFEST_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "manifest.py"
PACKAGE_INIT_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "__init__.py"
THEORY_TEX = ROOT / "preliminaries" / "theory2.tex"
THEORY_PDF = ROOT / "preliminaries" / "theory2.pdf"
BLUEPRINT_FILE = ROOT / "theory2-src-blueprint.json"
GEN_ORDER_FILE = ROOT / "theory2-generation-order.json"
CONTEXTS_FILE = ROOT / "src" / "jugeo" / "judgments" / "contexts.py"


def _bootstrap_src_package() -> None:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    importlib.invalidate_caches()


_bootstrap_src_package()

from jugeo.geometry.site import Coordinate
from jugeo.judgments.contexts import SemanticContext
from jugeo.judgments.judgment_terms import EvidenceBundle, EvidenceItem, EvidenceItemKind, TrustAnnotation, TrustLevel


def _load_source_module(module_name: str, file_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_MODELS = _load_source_module("jugeo.thesis.semantic_center.models", SOURCE_FILE)
_MANIFEST = _load_source_module("jugeo.thesis.semantic_center.manifest", MANIFEST_FILE)

ClaimStatus = _MODELS.ClaimStatus
ContributionKind = _MODELS.ContributionKind
ProblemDomain = _MODELS.ProblemDomain
IntroductionJuGeoScope = _MODELS.IntroductionJuGeoScope
IntroductionJuGeoRecord = _MODELS.IntroductionJuGeoRecord
IntroductionJuGeoSummary = _MODELS.IntroductionJuGeoSummary
JuGeoWorldview = _MODELS.JuGeoWorldview
ThesisClaim = _MODELS.ThesisClaim
ContributionRecord = _MODELS.ContributionRecord
ProblemClass = _MODELS.ProblemClass
INTRODUCTION_JUGEO_SCOPES = _MODELS.INTRODUCTION_JUGEO_SCOPES
INTRODUCTION_JUGEO_RECORDS = _MODELS.INTRODUCTION_JUGEO_RECORDS
INTRODUCTION_JUGEO_SUMMARY = _MODELS.INTRODUCTION_JUGEO_SUMMARY
JUGEO_WORLDVIEW = _MODELS.JUGEO_WORLDVIEW
CONTRIBUTION_RECORDS = _MODELS.CONTRIBUTION_RECORDS
PROBLEM_CLASSES = _MODELS.PROBLEM_CLASSES
INTRODUCTION_THESIS_CLAIMS = _MODELS.INTRODUCTION_THESIS_CLAIMS
build_introduction_summary = _MODELS.build_introduction_summary
build_jugeo_worldview = _MODELS.build_jugeo_worldview
build_scope_index = _MODELS.build_scope_index
iter_records_for_section = _MODELS.iter_records_for_section

INTRODUCTION_SOURCE_SECTIONS = _MANIFEST.INTRODUCTION_SOURCE_SECTIONS
MAIN_CONTRIBUTIONS = _MANIFEST.MAIN_CONTRIBUTIONS
PROBLEM_CLASS_ATLAS = _MANIFEST.PROBLEM_CLASS_ATLAS
CHAPTER_GOALS = _MANIFEST.CHAPTER_GOALS
CHAPTER_TITLE = _MANIFEST.CHAPTER_TITLE


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
def summary() -> IntroductionJuGeoSummary:
    return INTRODUCTION_JUGEO_SUMMARY


@pytest.fixture(scope="module")
def scope_index() -> dict[str, IntroductionJuGeoScope]:
    return build_scope_index()


@pytest.fixture(scope="module")
def record_index() -> dict[str, IntroductionJuGeoRecord]:
    return {record.record_id: record for record in INTRODUCTION_JUGEO_RECORDS}


@pytest.fixture(scope="module")
def models_surface() -> Any:
    return next(
        surface
        for surface in _MANIFEST.MANIFEST.module_surfaces
        if surface.module_name == "models"
    )


def _chapter_blueprint_entry(blueprint_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(
        entry
        for entry in blueprint_payload["chapterDirectories"]
        if entry["path"] == "src/jugeo/thesis/semantic_center"
    )


def _chapter_file_entry(blueprint_payload: Mapping[str, Any], *, file_name: str) -> Mapping[str, Any]:
    chapter_entry = _chapter_blueprint_entry(blueprint_payload)
    return next(entry for entry in chapter_entry["files"] if entry["file"] == file_name)


def _generation_entry(generation_order_payload: Mapping[str, Any], *, target: str) -> Mapping[str, Any]:
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


def _iter_string_values(values: Iterable[Any]) -> None:
    for value in values:
        assert isinstance(value, str)
        assert value.strip()


def _module_has_class(file_path: Path, class_name: str) -> bool:
    tree = ast.parse(file_path.read_text())
    return any(isinstance(node, ast.ClassDef) and node.name == class_name for node in tree.body)


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_source_file_compiles_and_defines_expected_classes() -> None:
    tree = ast.parse(SOURCE_FILE.read_text())
    assert tree.body
    for class_name in (
        "IntroductionJuGeoRecord",
        "IntroductionJuGeoScope",
        "IntroductionJuGeoSummary",
        "JuGeoWorldview",
        "ThesisClaim",
        "ContributionRecord",
        "ProblemClass",
    ):
        assert _module_has_class(SOURCE_FILE, class_name)


def test_governing_spec_files_exist_and_are_nontrivial() -> None:
    for path in (THEORY_TEX, THEORY_PDF, BLUEPRINT_FILE, GEN_ORDER_FILE, CONTEXTS_FILE):
        assert path.exists()
        assert path.stat().st_size > 100


def test_blueprint_models_entry_matches_requested_classes(
    blueprint_payload: Mapping[str, Any],
) -> None:
    entry = _chapter_file_entry(blueprint_payload, file_name="models.py")
    assert entry["estimatedLoC"] == 247
    assert tuple(entry["classes"]) == (
        "IntroductionJuGeoRecord",
        "IntroductionJuGeoScope",
        "IntroductionJuGeoSummary",
    )


def test_generation_order_models_entry_matches_requested_metadata(
    generation_order_payload: Mapping[str, Any],
) -> None:
    entry = _generation_entry(
        generation_order_payload,
        target="src/jugeo/thesis/semantic_center/models.py",
    )
    assert entry["sequence"] == 60
    assert entry["scope"] == "chapter"
    assert entry["stage"] == "chapter-01"
    assert entry["dependsOn"] == [
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/errors.py",
        "src/jugeo/judgments/contexts.py",
    ]
    assert entry["test"] == "tests/jugeo/thesis/semantic_center/test_models.py"
    assert entry["chapterNumber"] == 1


def test_module_exports_include_blueprint_and_compatibility_surfaces() -> None:
    exports = _read_module_exports(SOURCE_FILE)
    for symbol in (
        "IntroductionJuGeoRecord",
        "IntroductionJuGeoScope",
        "IntroductionJuGeoSummary",
        "JuGeoWorldview",
        "ThesisClaim",
        "ContributionRecord",
        "ProblemClass",
        "JUGEO_WORLDVIEW",
        "INTRODUCTION_JUGEO_SUMMARY",
    ):
        assert symbol in exports
        assert hasattr(_MODELS, symbol)


def test_models_surface_in_manifest_tracks_current_exports(models_surface: Any) -> None:
    exports = _read_module_exports(SOURCE_FILE)
    assert tuple(models_surface.current_exports) == exports
    assert tuple(models_surface.blueprint_classes) == (
        "IntroductionJuGeoRecord",
        "IntroductionJuGeoScope",
        "IntroductionJuGeoSummary",
    )
    assert "worldview records" in models_surface.semantic_focus
    assert "thesis claim tracking" in models_surface.semantic_focus


def test_package_init_expected_model_imports_are_satisfied_by_source_exports() -> None:
    init_text = PACKAGE_INIT_FILE.read_text()
    for legacy_symbol in (
        "ClaimStatus",
        "ContributionKind",
        "ProblemDomain",
        "JuGeoWorldview",
        "ThesisClaim",
        "ContributionRecord",
        "ProblemClass",
        "JUGEO_WORLDVIEW",
    ):
        assert legacy_symbol in init_text
        assert hasattr(_MODELS, legacy_symbol)


def test_summary_and_worldview_constants_are_stable_aliases(summary: IntroductionJuGeoSummary) -> None:
    assert build_introduction_summary() is summary
    assert build_jugeo_worldview() is JUGEO_WORLDVIEW
    assert JUGEO_WORLDVIEW.title == CHAPTER_TITLE
    assert JUGEO_WORLDVIEW.scope.section_title == CHAPTER_TITLE


def test_summary_payload_has_stable_machine_readable_shape(summary: IntroductionJuGeoSummary) -> None:
    payload = summary.to_dict()
    assert payload["chapter_number"] == 1
    assert payload["part_number"] == 1
    assert payload["chapter_title"] == CHAPTER_TITLE
    assert payload["package_path"] == "src/jugeo/thesis/semantic_center"
    assert isinstance(payload["scopes"], list)
    assert isinstance(payload["records"], list)
    assert payload["coverage_by_domain"] == summary.coverage_by_domain()
    assert set(payload["coverage_by_domain"]) == {domain.value for domain in ProblemDomain}


def test_summary_is_frozen_and_valid(summary: IntroductionJuGeoSummary) -> None:
    with pytest.raises(FrozenInstanceError):
        summary.chapter_title = "other"
    assert summary.validate() == ()


def test_summary_covers_each_governing_source_section(summary: IntroductionJuGeoSummary) -> None:
    assert summary.records_for_section(CHAPTER_TITLE) == ()
    section_titles = tuple(record.section_title for record in summary.records)
    assert section_titles == INTRODUCTION_SOURCE_SECTIONS
    for section in INTRODUCTION_SOURCE_SECTIONS:
        records = summary.records_for_section(section)
        assert len(records) == 1
        assert isinstance(records[0], IntroductionJuGeoRecord)


def test_records_are_frozen_and_reference_existing_nearby_modules() -> None:
    for record in INTRODUCTION_JUGEO_RECORDS:
        with pytest.raises(FrozenInstanceError):
            record.title = "other"
        assert record.validate() == ()
        for module_path in record.related_modules:
            assert (ROOT / module_path).exists(), module_path


def test_scope_index_is_deterministic(scope_index: Mapping[str, IntroductionJuGeoScope]) -> None:
    assert set(scope_index) == {scope.scope_id for scope in INTRODUCTION_JUGEO_SCOPES}
    assert scope_index["intro.semantic-center"].section_title == INTRODUCTION_SOURCE_SECTIONS[0]
    assert scope_index["intro.ag-dtt-ai"].covers_problem_domain(ProblemDomain.MATHEMATICAL_IDEATION)


def test_scopes_project_into_semantic_contexts() -> None:
    for scope in INTRODUCTION_JUGEO_SCOPES:
        context = scope.as_semantic_context()
        assert isinstance(context, SemanticContext)
        assert isinstance(context.coordinate, Coordinate)
        assert context.coordinate.key == scope.coordinate_key
        assert context.trust_boundary == scope.trust_boundary
        binding_names = {binding.name for binding in context.bindings}
        assert {"scope_id", "section_title", "authority_boundary", "trust_boundary"} <= binding_names
        assert scope.section_title in context.assumptions[0]


def test_records_project_into_semantic_contexts_with_record_bindings() -> None:
    for record in INTRODUCTION_JUGEO_RECORDS:
        context = record.as_semantic_context()
        assert isinstance(context, SemanticContext)
        binding_names = {binding.name for binding in context.bindings}
        assert {"record_id", "record_title", "semantic_claim"} <= binding_names
        assert f"record:{record.record_id}" in context.assumptions


def test_theory_tex_and_pdf_both_reflect_the_governing_sections(
    tex_text: str,
    pdf_text: str,
) -> None:
    for section in INTRODUCTION_SOURCE_SECTIONS:
        assert section in tex_text
        assert section in pdf_text
    assert "single semantic machine" in tex_text
    assert "single semantic machine" in pdf_text.lower()


def test_records_quote_worldview_and_problem_atlas_honestly(tex_text: str) -> None:
    combined = "\n".join(record.semantic_claim for record in INTRODUCTION_JUGEO_RECORDS)
    assert "judgment geometry" in combined.lower()
    assert "ag, dtt, and ai" in combined.lower() or "ag+dtt+ai" in combined.lower()
    assert "problem coverage" not in combined.lower()  # ensure the test checks real phrases, not placeholders
    for problem_class in PROBLEM_CLASS_ATLAS:
        assert problem_class in tex_text
    for contribution in MAIN_CONTRIBUTIONS:
        assert contribution in tex_text


def test_summary_coverage_by_domain_is_nonzero_and_semantically_plausible(summary: IntroductionJuGeoSummary) -> None:
    coverage = summary.coverage_by_domain()
    assert coverage[ProblemDomain.SEMANTIC_VERIFICATION.value] >= 3
    assert coverage[ProblemDomain.TRUST_MANAGEMENT.value] >= 2
    assert coverage[ProblemDomain.LONG_HORIZON_GENERATION.value] >= 2
    assert coverage[ProblemDomain.MATHEMATICAL_IDEATION.value] >= 2


def test_summary_keywords_include_core_chapter_vocabulary(summary: IntroductionJuGeoSummary) -> None:
    keywords = set(summary.semantic_keywords())
    assert "semantic" in keywords
    assert "center" in keywords
    assert "ag+dtt+ai" in keywords or "ai" in keywords
    assert "obstruction" in keywords
    assert "trust" in keywords


def test_worldview_object_preserves_invariants_and_validation_contract() -> None:
    assert isinstance(JUGEO_WORLDVIEW, JuGeoWorldview)
    assert JUGEO_WORLDVIEW.invariants_hold()
    assert JUGEO_WORLDVIEW.validate() is None
    assert "semantic center" in JUGEO_WORLDVIEW.one_line_summary().lower()
    worldview_payload = JUGEO_WORLDVIEW.to_dict()
    assert worldview_payload["title"] == CHAPTER_TITLE
    assert worldview_payload["problem_classes"] == list(PROBLEM_CLASS_ATLAS)


def test_compatibility_thesis_claim_supports_evidence_and_obligation_updates() -> None:
    item = EvidenceItem(
        kind=EvidenceItemKind.SOLVER_PROOF,
        payload={"lemma": "semantic-center-locality"},
        trust_level=TrustLevel.SOLVER_DISCHARGED,
        channel="pytest",
        provenance=("unit-test",),
    )
    claim = ThesisClaim(
        claim_id="T-TEST",
        section=INTRODUCTION_SOURCE_SECTIONS[0],
        statement="Local sections should glue only with explicit overlap agreement.",
        evidence=EvidenceBundle(),
        open_obligations=("prove overlap agreement", "prove trust boundary preservation"),
        trust=TrustAnnotation(level=TrustLevel.UNVERIFIED, reasons=("seed",)),
    )
    updated = claim.with_evidence(item, new_status=ClaimStatus.UNDER_REVIEW)
    assert updated.status is ClaimStatus.UNDER_REVIEW
    assert len(updated.evidence.items) == 1
    assert updated.trust.level == TrustLevel.UNVERIFIED
    assert updated.trust.evidence_basis

    partial = updated.discharge_obligation("prove overlap agreement")
    assert partial.status in {ClaimStatus.UNDER_REVIEW, ClaimStatus.PARTIALLY_VERIFIED}
    assert partial.open_obligations == ("prove trust boundary preservation",)

    verified = partial.discharge_obligation("prove trust boundary preservation")
    assert verified.status is ClaimStatus.VERIFIED
    assert verified.progress_fraction() == 1.0

    challenged = verified.challenge("new counterexample on overlap transport")
    assert challenged.status is ClaimStatus.UNDER_REVIEW
    assert challenged.trust.level < verified.trust.level

    obstructed = challenged.add_obstruction("change-of-cover still required")
    assert obstructed.is_obstructed()
    assert "change-of-cover still required" in obstructed.obstructions


def test_compatibility_claim_rejects_missing_obligation() -> None:
    claim = ThesisClaim(
        claim_id="T-MISSING",
        section=INTRODUCTION_SOURCE_SECTIONS[1],
        statement="Provenance should remain explicit.",
        open_obligations=("track proof origin",),
    )
    with pytest.raises(Exception) as exc_info:
        claim.discharge_obligation("not-there")
    assert "Obligation" in str(exc_info.value)


def test_contribution_records_are_substantial_and_dependency_aware() -> None:
    assert len(CONTRIBUTION_RECORDS) == 5
    ids = {record.contribution_id for record in CONTRIBUTION_RECORDS}
    assert ids == {f"CONTRIB-0{i}" for i in range(1, 6)}
    assert CONTRIBUTION_RECORDS[-1].depends_on == (
        "CONTRIB-01",
        "CONTRIB-02",
        "CONTRIB-03",
        "CONTRIB-04",
    )
    for record in CONTRIBUTION_RECORDS:
        assert record.validate() == []
        assert record.realized_in_modules
        assert record.novelty_claim
        assert record.to_dict()["kind"] == record.kind.value


def test_problem_classes_cover_multiple_domains_without_overclaiming() -> None:
    assert len(PROBLEM_CLASSES) == 6
    domains = {problem.domain for problem in PROBLEM_CLASSES}
    assert domains == set(ProblemDomain)
    for problem in PROBLEM_CLASSES:
        assert problem.validate() == []
        assert problem.example_instances
        assert problem.addressed_by_contributions
        assert problem.theory_section == INTRODUCTION_SOURCE_SECTIONS[4]


def test_intro_thesis_claims_are_formalized_and_section_aligned() -> None:
    assert len(INTRODUCTION_THESIS_CLAIMS) == 3
    for claim in INTRODUCTION_THESIS_CLAIMS:
        assert claim.formalized is True
        assert claim.section in INTRODUCTION_SOURCE_SECTIONS
        assert claim.open_obligations
        assert claim.status is ClaimStatus.PROPOSED


def test_iter_records_for_section_matches_summary_lookup() -> None:
    for section in INTRODUCTION_SOURCE_SECTIONS:
        assert iter_records_for_section(section) == INTRODUCTION_JUGEO_SUMMARY.records_for_section(section)
    with pytest.raises(ValueError):
        iter_records_for_section("   ")


def test_scope_and_record_keywords_overlap_with_chapter_goals() -> None:
    summary_keywords = set(INTRODUCTION_JUGEO_SUMMARY.semantic_keywords())
    goals_text = " ".join(CHAPTER_GOALS).lower()
    for token in ("semantic", "trust", "chapter", "implementation"):
        assert token in summary_keywords
        assert token in goals_text


def test_snapshot_json_is_machine_readable() -> None:
    snapshot = json.loads(_MODELS.INTRODUCTION_MODELS_SNAPSHOT_JSON)
    assert snapshot["chapter_number"] == 1
    assert snapshot["package_path"] == "src/jugeo/thesis/semantic_center"
    assert snapshot["record_count"] == len(INTRODUCTION_JUGEO_RECORDS)
    assert snapshot["scope_count"] == len(INTRODUCTION_JUGEO_SCOPES)


def test_source_mentions_copilot_only_as_proposal_or_navigation_channel() -> None:
    text = SOURCE_FILE.read_text().lower()
    assert "copilot" in text
    assert "no silent trust promotion" in text or "no silent" in text
    assert "proposal channels" in text or "proposal channel" in text


def test_module_is_honest_about_current_integration_surface() -> None:
    text = SOURCE_FILE.read_text()
    assert "compatibility" in text.lower()
    assert "IntroductionJuGeo*" not in text  # confirm the source contains concrete names rather than only wildcard prose
    assert "JuGeoWorldview" in text
    assert "ThesisClaim" in text
