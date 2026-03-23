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
SOURCE_FILE = ROOT / "src" / "jugeo" / "thesis" / "semantic_center" / "s02_jugeo_relative_to_theorem_provers.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "thesis" / "semantic_center" / "test_s02_jugeo_relative_to_theorem_provers.py"
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
_S02 = _load_source_module(
    "jugeo.thesis.semantic_center.s02_jugeo_relative_to_theorem_provers",
    SOURCE_FILE,
)

ToolKind = _S02.ToolKind
ComparisonVerdict = _S02.ComparisonVerdict
CapabilityKind = _S02.CapabilityKind
EvidenceMapping = _S02.EvidenceMapping
ToolProfile = _S02.ToolProfile
ComparativeCapability = _S02.ComparativeCapability
ComparativeObservation = _S02.ComparativeObservation
ComparativeGap = _S02.ComparativeGap
RepairComplexityEstimate = _S02.RepairComplexityEstimate
ComparativeAssessment = _S02.ComparativeAssessment
ComparativeScenarioReport = _S02.ComparativeScenarioReport
JuGeoRelativeTheoremProversWitness = _S02.JuGeoRelativeTheoremProversWitness
JuGeoRelativeTheoremProversAnalyzer = _S02.JuGeoRelativeTheoremProversAnalyzer
JuGeoRelativeTheoremProversCoordinator = _S02.JuGeoRelativeTheoremProversCoordinator
ComparativePositioning = _S02.ComparativePositioning
FormalToolRelation = _S02.FormalToolRelation
TheoremProverRelation = _S02.TheoremProverRelation
DepTypeRelation = _S02.DepTypeRelation
ModelCheckerRelation = _S02.ModelCheckerRelation
SolverRelation = _S02.SolverRelation
DEFAULT_WITNESS = _S02.DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_WITNESS
DEFAULT_COORDINATOR = _S02.DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_COORDINATOR
COMPARATIVE_POSITIONING = _S02.COMPARATIVE_POSITIONING
S02_SPEC_PROVENANCE = _S02.S02_SPEC_PROVENANCE
SECTION_WORLDVIEW_LINES = _S02.SECTION_WORLDVIEW_LINES
SECTION_ADVANTAGES = _S02.SECTION_ADVANTAGES
SECTION_RUNTIME_OBJECTS = _S02.SECTION_RUNTIME_OBJECTS
SECTION_CAPABILITY_IDS = _S02.SECTION_CAPABILITY_IDS


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
def coordinator() -> JuGeoRelativeTheoremProversCoordinator:
    return JuGeoRelativeTheoremProversCoordinator.build_default()


@pytest.fixture(scope="module")
def analyzer(coordinator: JuGeoRelativeTheoremProversCoordinator) -> JuGeoRelativeTheoremProversAnalyzer:
    return coordinator.analyzer


@pytest.fixture(scope="module")
def positioning(coordinator: JuGeoRelativeTheoremProversCoordinator) -> ComparativePositioning:
    return coordinator.compare_tooling_landscape()


def _chapter_file_entry(blueprint_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    chapter_entry = next(
        entry for entry in blueprint_payload["chapterDirectories"]
        if entry["path"] == "src/jugeo/thesis/semantic_center"
    )
    return next(entry for entry in chapter_entry["files"] if entry["file"] == "s02_jugeo_relative_to_theorem_provers.py")


def _generation_entry(generation_order_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(
        item for item in generation_order_payload["items"]
        if item["target"] == "src/jugeo/thesis/semantic_center/s02_jugeo_relative_to_theorem_provers.py"
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


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().replace("--", " ").replace("—", " ").split())


def _observation(
    observation_id: str,
    *,
    tool_kind: ToolKind,
    claim: str,
    clauses: tuple[str, ...],
    trust_level: TrustLevel,
    coordinate_suffix: str,
    residual_obligations: tuple[str, ...] = (),
    overlap_tags: tuple[str, ...] = ("same-contract",),
) -> ComparativeObservation:
    return ComparativeObservation(
        observation_id=observation_id,
        coordinate=Coordinate(("src", "jugeo", "comparative", coordinate_suffix), kind=CoordinateKind.REGION),
        tool_kind=tool_kind,
        claim=claim,
        clauses=clauses,
        trust_level=trust_level,
        residual_obligations=residual_obligations,
        provenance=("pytest", observation_id),
        overlap_tags=overlap_tags,
        metadata={"observation_id": observation_id},
    )


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_and_dependency_files_exist() -> None:
    for path in (
        SOURCE_FILE,
        TEST_FILE,
        THEORY_TEX,
        THEORY_PDF,
        BLUEPRINT_FILE,
        GEN_ORDER_FILE,
        MANIFEST_FILE,
        MODELS_FILE,
        DESCENT_FILE,
        TRUST_FILE,
    ):
        assert path.exists()
        assert path.stat().st_size > 100


def test_source_file_compiles_and_defines_required_classes() -> None:
    ast.parse(SOURCE_FILE.read_text())
    for class_name in (
        "JuGeoRelativeTheoremProversCoordinator",
        "JuGeoRelativeTheoremProversAnalyzer",
        "JuGeoRelativeTheoremProversWitness",
        "ComparativePositioning",
        "TheoremProverRelation",
        "DepTypeRelation",
        "ModelCheckerRelation",
        "SolverRelation",
        "ComparativeObservation",
        "ComparativeGap",
        "ComparativeScenarioReport",
    ):
        assert _module_has_class(SOURCE_FILE, class_name)


def test_blueprint_entry_matches_required_file_shape(blueprint_payload: Mapping[str, Any]) -> None:
    entry = _chapter_file_entry(blueprint_payload)
    assert entry["estimatedLoC"] == 230
    assert tuple(entry["classes"]) == (
        "JuGeoRelativeTheoremProversCoordinator",
        "JuGeoRelativeTheoremProversAnalyzer",
        "JuGeoRelativeTheoremProversWitness",
    )
    assert tuple(entry["sectionIndexes"]) == (2,)


def test_generation_order_entry_matches_requested_dependencies(generation_order_payload: Mapping[str, Any]) -> None:
    entry = _generation_entry(generation_order_payload)
    assert entry["sequence"] == 62
    assert entry["scope"] == "chapter"
    assert entry["stage"] == "chapter-01"
    assert entry["dependsOn"] == [
        "src/jugeo/thesis/semantic_center/models.py",
        "src/jugeo/thesis/semantic_center/manifest.py",
        "src/jugeo/geometry/descent.py",
        "src/jugeo/evidence/trust.py",
    ]
    assert entry["test"] == "tests/jugeo/thesis/semantic_center/test_s02_jugeo_relative_to_theorem_provers.py"


def test_module_exports_include_new_and_compatibility_surfaces() -> None:
    exports = _read_module_exports(SOURCE_FILE)
    for symbol in (
        "JuGeoRelativeTheoremProversCoordinator",
        "JuGeoRelativeTheoremProversAnalyzer",
        "JuGeoRelativeTheoremProversWitness",
        "ComparativePositioning",
        "TheoremProverRelation",
        "DepTypeRelation",
        "ModelCheckerRelation",
        "SolverRelation",
        "COMPARATIVE_POSITIONING",
        "DEFAULT_JUGEO_RELATIVE_THEOREM_PROVERS_COORDINATOR",
    ):
        assert symbol in exports
        assert hasattr(_S02, symbol)


def test_package_init_expected_imports_are_satisfied() -> None:
    init_text = PACKAGE_INIT_FILE.read_text()
    for symbol in (
        "ToolKind",
        "EvidenceMapping",
        "ComparativePositioning",
        "TheoremProverRelation",
        "DepTypeRelation",
        "ModelCheckerRelation",
        "SolverRelation",
        "COMPARATIVE_POSITIONING",
    ):
        assert symbol in init_text
        assert hasattr(_S02, symbol)


def test_spec_provenance_matches_manifest_and_requested_target() -> None:
    assert S02_SPEC_PROVENANCE["semantic_source"] == "preliminaries/theory2.tex"
    assert S02_SPEC_PROVENANCE["semantic_source_pdf"] == "preliminaries/theory2.pdf"
    assert S02_SPEC_PROVENANCE["structural_blueprint"] == "theory2-src-blueprint.json"
    assert S02_SPEC_PROVENANCE["structural_generation_order"] == "theory2-generation-order.json"
    assert S02_SPEC_PROVENANCE["target_file"] == "src/jugeo/thesis/semantic_center/s02_jugeo_relative_to_theorem_provers.py"
    assert S02_SPEC_PROVENANCE["target_test"] == "tests/jugeo/thesis/semantic_center/test_s02_jugeo_relative_to_theorem_provers.py"
    assert S02_SPEC_PROVENANCE["sequence"] == 62
    assert S02_SPEC_PROVENANCE["chapter_number"] == _MANIFEST.CHAPTER_NUMBER
    assert S02_SPEC_PROVENANCE["part_number"] == _MANIFEST.PART_NUMBER
    assert S02_SPEC_PROVENANCE["section_title"] == _MANIFEST.INTRODUCTION_SOURCE_SECTIONS[1]


def test_section_worldview_and_advantages_quote_theory_honestly(tex_text: str, pdf_text: str) -> None:
    normalized_tex = _normalize_text(tex_text)
    normalized_pdf = _normalize_text(pdf_text)
    assert "jugeo relative to theorem provers, coding assistants, and agentic verifiers" in normalized_tex
    assert "jugeo relative to theorem provers, coding assistants, and agentic verifiers" in normalized_pdf
    for phrase in (
        "explicit proof obligations and evidence provenance",
        "local-to-global state model",
        "generated text as success",
        "obstruction classes",
        "replay-local invalidation",
        "repair complexity",
        "mayer vietoris",
        "product-cover",
    ):
        assert phrase in normalized_tex
    assert "repair complexity" in normalized_pdf
    assert any("proof obligations" in line.lower() for line in SECTION_WORLDVIEW_LINES)
    assert any("repair complexity" in line.lower() for line in SECTION_ADVANTAGES)
    assert "explicit covers and hypercovers" in SECTION_RUNTIME_OBJECTS


def test_default_witness_is_stable_and_theory_shaped() -> None:
    assert isinstance(DEFAULT_WITNESS, JuGeoRelativeTheoremProversWitness)
    assert DEFAULT_WITNESS.section_title == _MANIFEST.INTRODUCTION_SOURCE_SECTIONS[1]
    assert len(DEFAULT_WITNESS.profiles) >= 7
    assert len(DEFAULT_WITNESS.capability_catalog) == len(SECTION_CAPABILITY_IDS)
    assert DEFAULT_WITNESS.provenance["sequence"] == 62
    assert DEFAULT_WITNESS.provenance["worldview_record_id"] == _MODELS.JUGEO_WORLDVIEW.record_id
    assert DEFAULT_WITNESS.render_digest()
    profile_kinds = {profile.tool_kind for profile in DEFAULT_WITNESS.profiles}
    assert {
        ToolKind.THEOREM_PROVER,
        ToolKind.CODING_ASSISTANT,
        ToolKind.AGENTIC_VERIFIER,
        ToolKind.JUGEO,
    }.issubset(profile_kinds)


def test_default_profiles_preserve_expected_boundaries() -> None:
    theorem_profile = DEFAULT_WITNESS.profile_by_kind(ToolKind.THEOREM_PROVER)
    coding_profile = DEFAULT_WITNESS.profile_by_kind(ToolKind.CODING_ASSISTANT)
    agentic_profile = DEFAULT_WITNESS.profile_by_kind(ToolKind.AGENTIC_VERIFIER)
    jugeo_profile = DEFAULT_WITNESS.profile_by_kind(ToolKind.JUGEO)
    assert theorem_profile.evidence_mapping.trust_ceiling is TrustLevel.MECHANICALLY_VERIFIED
    assert coding_profile.evidence_mapping.trust_ceiling is TrustLevel.COPILOT_SUGGESTED
    assert agentic_profile.evidence_mapping.trust_ceiling is TrustLevel.UNVERIFIED
    assert jugeo_profile.supports_global_descent
    assert "covers and hypercovers" in " ".join(agentic_profile.missing_semantic_objects)
    assert theorem_profile.preserves_explicit_provenance()


def test_capability_catalog_names_the_section_advantages() -> None:
    capability_ids = tuple(capability.capability_id for capability in DEFAULT_WITNESS.capability_catalog)
    assert capability_ids == SECTION_CAPABILITY_IDS
    capability_map = {capability.capability_id: capability for capability in DEFAULT_WITNESS.capability_catalog}
    assert capability_map["repair-complexity-quantification"].kind is CapabilityKind.REPAIR_ANALYSIS
    assert "Mayer-Vietoris" in capability_map["mayer-vietoris-incrementality"].title
    assert "product-cover" in capability_map["product-cover-specification-checking"].capability_id


def test_relation_classes_wrap_default_profiles() -> None:
    theorem_relation = TheoremProverRelation()
    dep_type_relation = DepTypeRelation()
    model_checker_relation = ModelCheckerRelation()
    solver_relation = SolverRelation()
    assert theorem_relation.profile.tool_kind is ToolKind.THEOREM_PROVER
    assert dep_type_relation.profile.tool_kind is ToolKind.DEPENDENT_TYPE_ASSISTANT
    assert model_checker_relation.profile.tool_kind is ToolKind.MODEL_CHECKER
    assert solver_relation.profile.tool_kind is ToolKind.SMT_SOLVER
    assert "local-to-global" in theorem_relation.what_jugeo_adds().lower()
    assert solver_relation.evidence_mapping().trust_ceiling is TrustLevel.SOLVER_DISCHARGED
    assert "specialized local reasoning" in theorem_relation.what_jugeo_does_not_replace().lower()


def test_compare_tool_returns_expected_verdicts(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    theorem_assessment = analyzer.compare_tool(ToolKind.THEOREM_PROVER)
    coding_assessment = analyzer.compare_tool(ToolKind.CODING_ASSISTANT)
    agentic_assessment = analyzer.compare_tool(ToolKind.AGENTIC_VERIFIER)
    jugeo_assessment = analyzer.compare_tool(ToolKind.JUGEO)
    assert theorem_assessment.verdict is ComparisonVerdict.PRESERVED_AND_EXTENDED
    assert coding_assessment.verdict is ComparisonVerdict.PROPOSAL_ONLY
    assert agentic_assessment.verdict is ComparisonVerdict.ORCHESTRATION_WITHOUT_GEOMETRY
    assert jugeo_assessment.verdict is ComparisonVerdict.NATIVE_JUGEO_CAPABILITY
    assert "local strengths" in theorem_assessment.rationale.lower()


def test_compare_tooling_landscape_returns_assessments_for_selected_kinds(coordinator: JuGeoRelativeTheoremProversCoordinator) -> None:
    positioning = coordinator.compare_tooling_landscape(
        (
            ToolKind.THEOREM_PROVER,
            ToolKind.CODING_ASSISTANT,
            ToolKind.AGENTIC_VERIFIER,
        )
    )
    assert isinstance(positioning, ComparativePositioning)
    assert len(positioning.assessments) == 3
    assert positioning.assessment_by_kind(ToolKind.CODING_ASSISTANT).verdict is ComparisonVerdict.PROPOSAL_ONLY
    rendered = positioning.render_table().lower()
    assert "jugeo comparative positioning" in rendered
    assert "coding assistant" in rendered


def test_global_constant_positioning_stays_compatible() -> None:
    assert isinstance(COMPARATIVE_POSITIONING, ComparativePositioning)
    assert COMPARATIVE_POSITIONING.assessment_by_kind(ToolKind.JUGEO).verdict is ComparisonVerdict.NATIVE_JUGEO_CAPABILITY
    payload = COMPARATIVE_POSITIONING.to_dict()
    assert payload["worldview_record_id"] == _MODELS.JUGEO_WORLDVIEW.record_id
    assert len(payload["assessments"]) >= 7


def test_observation_validation_rejects_empty_clauses() -> None:
    with pytest.raises(ValueError):
        ComparativeObservation(
            observation_id="bad",
            coordinate=Coordinate(("x",), kind=CoordinateKind.REGION),
            tool_kind=ToolKind.CODING_ASSISTANT,
            claim="something",
            clauses=(),
            trust_level=TrustLevel.COPILOT_SUGGESTED,
        )


def test_analyzer_requires_nonempty_observation_sequence(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    with pytest.raises(JuGeoError):
        analyzer.analyze_observations(())


def test_observation_to_local_section_preserves_locality_and_partiality() -> None:
    observation = _observation(
        "obs-local",
        tool_kind=ToolKind.CODING_ASSISTANT,
        claim="Patch may satisfy contract",
        clauses=("proposed code path",),
        trust_level=TrustLevel.COPILOT_SUGGESTED,
        coordinate_suffix="assistant",
        residual_obligations=("prove postcondition",),
    )
    local_section = observation.to_local_section()
    assert local_section.coordinate.endswith("assistant")
    assert local_section.is_partial
    assert local_section.residual_obligations == ["prove postcondition"]
    assert local_section.judgment_data["claim"] == "patch may satisfy contract"


def test_scenario_analysis_detects_conflicting_overlap_and_reports_repairs(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    theorem_obs = _observation(
        "theorem-proof",
        tool_kind=ToolKind.THEOREM_PROVER,
        claim="Function contract holds",
        clauses=("precondition discharged", "postcondition discharged"),
        trust_level=TrustLevel.MECHANICALLY_VERIFIED,
        coordinate_suffix="contract-left",
    )
    coding_obs = _observation(
        "assistant-draft",
        tool_kind=ToolKind.CODING_ASSISTANT,
        claim="Function contract holds",
        clauses=("precondition discharged", "postcondition maybe holds"),
        trust_level=TrustLevel.COPILOT_SUGGESTED,
        coordinate_suffix="contract-right",
        residual_obligations=("prove postcondition",),
    )
    report = analyzer.analyze_observations((theorem_obs, coding_obs))
    assert isinstance(report, ComparativeScenarioReport)
    assert not report.honest_to_publish
    assert len(report.gaps) == 1
    assert report.repair_estimate.independent_fix_lower_bound == 1
    assert report.aggregate_trust is TrustLevel.COPILOT_SUGGESTED
    assert "strengthen overlap treaty" in " ".join(report.recommended_next_moves)
    gap = report.gaps[0]
    assert "postcondition maybe holds" in gap.conflicting_clauses
    assert "precondition discharged" in gap.shared_clauses


def test_scenario_analysis_can_report_glued_family_when_clauses_match(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    theorem_obs = _observation(
        "theorem-proof",
        tool_kind=ToolKind.THEOREM_PROVER,
        claim="Refinement relation holds",
        clauses=("simulation proof complete",),
        trust_level=TrustLevel.MECHANICALLY_VERIFIED,
        coordinate_suffix="refinement-left",
        overlap_tags=("refinement",),
    )
    solver_obs = _observation(
        "solver-check",
        tool_kind=ToolKind.SMT_SOLVER,
        claim="Refinement relation holds",
        clauses=("simulation proof complete",),
        trust_level=TrustLevel.SOLVER_DISCHARGED,
        coordinate_suffix="refinement-right",
        overlap_tags=("refinement",),
    )
    report = analyzer.analyze_observations((theorem_obs, solver_obs), trust_floor=TrustLevel.SOLVER_DISCHARGED)
    assert report.honest_to_publish
    assert len(report.gaps) == 0
    assert report.repair_estimate.binary_conflict_rank == 0
    assert report.aggregate_trust is TrustLevel.SOLVER_DISCHARGED
    assert "glues" in report.recommended_next_moves[0].lower()


def test_scenario_analysis_keeps_claims_separate_when_no_overlap(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    proof_obs = _observation(
        "proof-one",
        tool_kind=ToolKind.THEOREM_PROVER,
        claim="Parser contract holds",
        clauses=("proof complete",),
        trust_level=TrustLevel.MECHANICALLY_VERIFIED,
        coordinate_suffix="parser",
        overlap_tags=("parser",),
    )
    model_obs = _observation(
        "model-two",
        tool_kind=ToolKind.MODEL_CHECKER,
        claim="Scheduler is deadlock free",
        clauses=("state graph explored",),
        trust_level=TrustLevel.SOLVER_DISCHARGED,
        coordinate_suffix="scheduler",
        overlap_tags=("scheduler",),
    )
    report = analyzer.analyze_observations((proof_obs, model_obs), trust_floor=TrustLevel.SOLVER_DISCHARGED)
    assert report.honest_to_publish
    assert len(report.gaps) == 0
    assert report.repair_estimate.independent_fix_lower_bound == 0


def test_repair_complexity_estimate_is_exact_for_single_clause_binary_conflicts() -> None:
    gap = ComparativeGap(
        gap_id="g1",
        claim="Equivalence claim",
        left_observation_id="left",
        right_observation_id="right",
        overlap_key="left∩right",
        shared_clauses=("same context",),
        conflicting_clauses=("different relation witness",),
        cocycle_payload={"relation": {"left": "a", "right": "b"}},
        repair_hints=("align relation witness",),
    )
    estimate = RepairComplexityEstimate.from_gaps((gap,))
    assert estimate.binary_conflict_rank == 1
    assert estimate.independent_fix_lower_bound == 1
    assert estimate.exact_fragment_fix_count == 1


def test_repair_complexity_estimate_is_lower_bound_only_for_multi_clause_gaps() -> None:
    gap = ComparativeGap(
        gap_id="g2",
        claim="Complex claim",
        left_observation_id="left",
        right_observation_id="right",
        overlap_key="left∩right",
        shared_clauses=(),
        conflicting_clauses=("a", "b"),
        cocycle_payload={"clauses": {"left": ["a"], "right": ["b"]}},
        repair_hints=("refine cover",),
    )
    estimate = RepairComplexityEstimate.from_gaps((gap,))
    assert estimate.binary_conflict_rank == 1
    assert estimate.exact_fragment_fix_count is None
    assert "lower bound" in estimate.explanation.lower()


def test_default_coordinator_briefing_mentions_section_worldview(coordinator: JuGeoRelativeTheoremProversCoordinator) -> None:
    briefing = coordinator.render_operator_briefing().lower()
    assert "jugeo relative to theorem provers" in briefing
    assert "covers" in briefing
    assert "coding assistant" in briefing


def test_json_shapes_are_stable_and_serializable(positioning: ComparativePositioning) -> None:
    payload = positioning.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert "tool_profile" in encoded
    assert "worldview_record_id" in encoded
    witness_payload = DEFAULT_WITNESS.to_dict()
    assert json.loads(json.dumps(witness_payload))["section_title"] == _MANIFEST.INTRODUCTION_SOURCE_SECTIONS[1]


def test_worldview_bridge_stays_compatible_with_models_module() -> None:
    positioning = COMPARATIVE_POSITIONING
    assert positioning.worldview is _MODELS.JUGEO_WORLDVIEW
    assert positioning.worldview.title == _MODELS.JUGEO_WORLDVIEW.title
    assert any("single semantic machine" in line.lower() for line in _MANIFEST.WORLDVIEW_COMMITMENTS)


def test_source_mentions_trust_and_semantic_boundaries_in_plain_text() -> None:
    text = SOURCE_FILE.read_text().lower()
    for phrase in (
        "trust boundary",
        "semantic boundary",
        "settlement authority",
        "local-to-global",
        "obstruction",
        "replay-local invalidation",
    ):
        assert phrase in text


def test_module_is_honest_about_current_scope() -> None:
    text = SOURCE_FILE.read_text().lower()
    assert "does not claim to replace" in text or "does not replace" in text
    assert "future theorem catalog" in text
    assert "package-wide integration layer" in text


def test_can_lookup_profiles_and_capabilities_by_key() -> None:
    theorem_profile = DEFAULT_WITNESS.profile_by_kind(ToolKind.THEOREM_PROVER)
    capability = DEFAULT_WITNESS.capability_by_id("equivalence-by-descent")
    assert theorem_profile.tool_name == "Theorem prover"
    assert capability.kind is CapabilityKind.EQUIVALENCE
    with pytest.raises(JuGeoError):
        DEFAULT_WITNESS.capability_by_id("missing-capability")


def test_compare_tool_accepts_relation_wrapper(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    assessment = analyzer.compare_tool(TheoremProverRelation())
    assert isinstance(assessment, ComparativeAssessment)
    assert assessment.tool_kind is ToolKind.THEOREM_PROVER


def test_report_summary_is_readable(analyzer: JuGeoRelativeTheoremProversAnalyzer) -> None:
    observation = _observation(
        "summary-proof",
        tool_kind=ToolKind.THEOREM_PROVER,
        claim="Invariant holds",
        clauses=("proof complete",),
        trust_level=TrustLevel.MECHANICALLY_VERIFIED,
        coordinate_suffix="summary",
    )
    report = analyzer.analyze_observations((observation,), trust_floor=TrustLevel.MECHANICALLY_VERIFIED)
    summary = report.render_summary().lower()
    assert "publishable" in summary
    assert "trust=mechanically_verified" in summary
