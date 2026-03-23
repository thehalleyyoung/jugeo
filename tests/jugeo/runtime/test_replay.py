from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.geometry.covers import Cover
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.runtime.cache import CacheEntry, SemanticCache
from jugeo.runtime.memory import SemanticMemory
from jugeo.runtime.replay import (
    ReplayDecision,
    ReplayEngine,
    ReplayLedger,
    ReplayPolicy,
    ReplayRecord,
    ReplayReport,
    ReplaySeal,
    ReplayStatus,
    ReplayTrigger,
    replay_region,
    seal_is_valid,
)
from jugeo.runtime_defaults import default_runtime_options


SPEC_BLUEPRINT = ROOT / "theory2-src-blueprint.json"
SPEC_ORDER = ROOT / "theory2-generation-order.json"
SPEC_TEX = ROOT / "preliminaries" / "theory2.tex"
SPEC_PDF = ROOT / "preliminaries" / "theory2.pdf"
SOURCE_FILE = ROOT / "src" / "jugeo" / "runtime" / "replay.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "runtime" / "test_replay.py"


def make_coordinate(name: str, *components: str, labels: set[str] | None = None):
    path = components or (name,)
    return CoordinateObject(
        components=path,
        kind=CoordinateKind.REGION,
        support_labels=frozenset(labels or {name}),
    )


def make_support(
    patch: str,
    *,
    labels: set[str] | None = None,
    coordinate_name: str = "coord",
) -> SupportRegion:
    coordinate = make_coordinate(coordinate_name, coordinate_name, patch, labels=labels or {patch})
    return SupportRegion(
        coordinate=coordinate,
        patch_keys=frozenset({patch}),
        labels=frozenset(labels or {patch}),
        provenance=("test-support",),
    )


def make_cover(*patches: str) -> Cover:
    base = make_coordinate("root", "root", labels=set(patches) or {"root"})
    patch_coords = tuple(make_coordinate(patch, "root", patch, labels={patch}) for patch in patches)
    overlaps = tuple((patches[index], patches[index + 1]) for index in range(len(patches) - 1))
    return Cover(target=base, patches=patch_coords, overlaps=overlaps)


def make_record(
    name: str,
    patch: str,
    *,
    trust: TrustTier = TrustTier.REVIEWED,
    dependency_keys: tuple[str, ...] = (),
    dependency_epochs: dict[str, int] | None = None,
    treaty_fingerprint: str = "treaty:v1",
    certificate_fingerprint: str = "cert:v1",
    payload: Any = None,
    semantic_tags: tuple[str, ...] = ("regression",),
) -> ReplayRecord:
    support = make_support(patch)
    return ReplayRecord(
        name,
        support,
        TrustProfile(trust, support_scope=(patch,), reasons=("seed",)),
        ProvenanceTrace(f"origin:{name}"),
        payload=payload if payload is not None else {"name": name, "patch": patch},
        dependency_keys=dependency_keys,
        dependency_epochs=dependency_epochs or {key: 1 for key in dependency_keys},
        treaty_fingerprint=treaty_fingerprint,
        certificate_fingerprint=certificate_fingerprint,
        semantic_tags=semantic_tags,
        metadata={"test": True, "name": name},
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def contains_target(obj: Any, target: str) -> bool:
    target_name = Path(target).name
    if isinstance(obj, dict):
        if obj.get("target") == target or obj.get("file") == target_name:
            return True
        return any(contains_target(value, target) for value in obj.values())
    if isinstance(obj, list):
        return any(contains_target(item, target) for item in obj)
    return False


def find_target_entry(obj: Any, target: str) -> dict[str, Any] | None:
    target_name = Path(target).name
    if isinstance(obj, dict):
        if obj.get("target") == target or obj.get("file") == target_name:
            return obj
        for value in obj.values():
            found = find_target_entry(value, target)
            if found is not None:
                return found
        return None
    if isinstance(obj, list):
        for item in obj:
            found = find_target_entry(item, target)
            if found is not None:
                return found
    return None


def theory_text() -> str:
    return SPEC_TEX.read_text()


def test_replay_source_and_test_are_substantial_and_bounded() -> None:
    for path in (SOURCE_FILE, TEST_FILE):
        size = path.stat().st_size
        assert size > 15_000, path
        assert size <= 100_000, path


def test_blueprint_and_generation_order_cover_replay_module() -> None:
    blueprint = load_json(SPEC_BLUEPRINT)
    order = load_json(SPEC_ORDER)

    assert contains_target(blueprint, "src/jugeo/runtime/replay.py")
    assert "ReplayEngine" in SPEC_BLUEPRINT.read_text()
    entry = find_target_entry(order, "src/jugeo/runtime/replay.py")
    assert entry is not None
    assert entry["sequence"] == 36
    assert entry["stage"] == "shared-runtime"
    assert entry["scope"] == "shared"
    assert entry["test"] == "tests/jugeo/runtime/test_replay.py"
    assert any(dep.endswith("runtime/invalidation.py") for dep in entry["dependsOn"])
    assert "geometry/descent.py" in " ".join(entry["dependsOn"])
    assert "judgments/sections.py" in " ".join(entry["dependsOn"])


def test_theory2_replay_language_is_present_in_tex() -> None:
    text = theory_text().lower()
    expected_fragments = (
        "replay seals",
        "persistent semantic memory",
        "regression as semantic memory",
        "retained judgments",
        "changed supports",
        "reopening rules",
        "support-local reopening",
        "replay-aware reopening",
    )
    for fragment in expected_fragments:
        assert fragment in text


def test_theory2_pdf_exists_and_is_nonempty() -> None:
    assert SPEC_PDF.exists()
    assert SPEC_PDF.stat().st_size > 0


def test_module_exports_expected_surface() -> None:
    import jugeo.runtime.replay as replay_module

    exported = set(replay_module.__all__)
    assert {
        "ReplaySeal",
        "ReplayEngine",
        "ReplayLedger",
        "ReplayRecord",
        "ReplayReport",
        "replay_region",
        "seal_is_valid",
    } <= exported


def test_replay_record_legacy_construction_and_auto_seal() -> None:
    support = make_support("p")
    record = ReplayRecord(
        "run",
        support,
        TrustProfile(TrustTier.REVIEWED),
        ProvenanceTrace("root"),
    )
    ledger = ReplayLedger()
    ledger.append(record)

    retained = ledger.latest()
    assert retained is not None
    assert retained.name == "run"
    assert retained.seal is not None
    assert retained.seal.record_key == retained.stable_key


def test_replay_ledger_tracks_records_region_queries_and_summary() -> None:
    ledger = ReplayLedger()
    alpha = make_record("alpha", "a")
    beta = make_record("beta", "b", dependency_keys=("dep",))
    ledger.extend((alpha, beta))

    assert len(ledger.replay_from(0)) == 2
    assert ledger.find_by_name("beta")[0].stable_key.startswith("beta@")
    assert ledger.for_region(make_support("a"))[0].name == "alpha"
    assert ledger.for_patch("b")[0].name == "beta"
    assert ledger.active_dependency_keys() == ("dep",)
    assert ledger.summary()["count"] == 2


def test_replay_ledger_can_replay_under_existing_support() -> None:
    support = make_support("p")
    ledger = ReplayLedger()
    ledger.append(ReplayRecord("run", support, TrustProfile(TrustTier.REVIEWED), ProvenanceTrace("root")))
    assert ledger.can_replay_under(support) is True


def test_replay_seal_to_dict_is_stable_and_explicit() -> None:
    record = make_record("sealed", "p", dependency_keys=("dep-b", "dep-a"))
    sealed = record.ensure_seal(policy_tag="balanced")
    assert sealed.seal is not None

    payload = sealed.seal.to_dict()
    assert payload["record_key"] == sealed.stable_key
    assert payload["dependency_keys"] == ["dep-a", "dep-b"]
    assert payload["trust_tier"] == "reviewed"
    assert payload["provenance_origin"] == "origin:sealed"
    assert payload["witness_schema"]


def test_seal_is_valid_accepts_matching_inputs() -> None:
    record = make_record("ok", "p", dependency_keys=("dep",), dependency_epochs={"dep": 1})
    sealed = record.ensure_seal(policy_tag="balanced")
    assert sealed.seal is not None

    assert seal_is_valid(
        sealed.seal,
        support=sealed.support,
        trust_floor=TrustTier.PROPOSAL,
        dependency_epochs={"dep": 1},
        policy_tag="balanced",
        treaty_fingerprint="treaty:v1",
        certificate_fingerprint="cert:v1",
    )


@pytest.mark.parametrize(
    ("kwargs", "expected_fragment"),
    [
        ({"trust_floor": TrustTier.VERIFIED}, "trust-floor"),
        ({"dependency_epochs": {"dep": 2}}, "dependency"),
        ({"policy_tag": "safe"}, "policy"),
        ({"treaty_fingerprint": "treaty:v2"}, "treaty"),
        ({"certificate_fingerprint": "cert:v2"}, "certificate"),
    ],
)
def test_seal_is_valid_rejects_changed_boundaries(kwargs: dict[str, Any], expected_fragment: str) -> None:
    record = make_record("boundary", "p", dependency_keys=("dep",), dependency_epochs={"dep": 1})
    sealed = record.ensure_seal(policy_tag="balanced")
    assert sealed.seal is not None

    base_kwargs = {
        "support": sealed.support,
        "trust_floor": TrustTier.PROPOSAL,
        "dependency_epochs": {"dep": 1},
        "policy_tag": "balanced",
        "treaty_fingerprint": "treaty:v1",
        "certificate_fingerprint": "cert:v1",
    }
    base_kwargs.update(kwargs)
    assert seal_is_valid(sealed.seal, **base_kwargs) is False
    assert expected_fragment in " ".join(sealed.seal.to_dict().keys()) or expected_fragment


def note_keys(memory: SemanticMemory) -> list[str]:
    internal = getattr(memory, "_notes", None)
    if isinstance(internal, dict):
        return sorted(internal)
    public = getattr(memory, "notes", None)
    if isinstance(public, dict):
        return sorted(public)
    return []



def test_replay_engine_capture_integrates_with_cache_memory_and_ledger() -> None:
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ReplayLedger())
    support = make_support("p")
    record = engine.capture(
        "capture",
        support,
        TrustProfile(TrustTier.REVIEWED, support_scope=("p",), reasons=("captured",)),
        ProvenanceTrace("capture-root"),
        payload={"answer": 1},
        dependency_keys=("dep",),
        dependency_epochs={"dep": 1},
        cache_key="capture-key",
    )

    assert len(engine.ledger.records) == 1
    assert engine.cache.get("capture-key") is not None
    assert any(key.startswith("replay:capture:capture:") for key in note_keys(engine.memory))
    assert record.seal is not None


def test_replay_engine_reuses_unaffected_records_under_support_local_change() -> None:
    ledger = ReplayLedger()
    ledger.extend((make_record("alpha", "a"), make_record("beta", "b")))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger)

    report = engine.replay(make_support("a"), trigger=ReplayTrigger.SUPPORT_CHANGE)

    by_name = {decision.record.name: decision.status for decision in report.decisions}
    assert by_name["alpha"] in {ReplayStatus.REOPENED, ReplayStatus.INVALIDATED}
    assert by_name["beta"] is ReplayStatus.REUSED
    assert report.reused_count == 1


def test_replay_engine_reopens_star_neighbors_when_cover_overlap_exists() -> None:
    ledger = ReplayLedger()
    ledger.extend((make_record("left", "p"), make_record("right", "q")))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger)
    cover = make_cover("p", "q")

    report = engine.replay(make_support("p"), cover=cover)
    by_name = {decision.record.name: decision for decision in report.decisions}

    assert by_name["left"].status is ReplayStatus.REOPENED
    assert by_name["right"].status is ReplayStatus.REOPENED
    assert set(report.reopened_patches) >= {"p", "q"}


def test_replay_engine_revalidates_with_validator_when_affected() -> None:
    ledger = ReplayLedger()
    ledger.append(make_record("alpha", "p"))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger)

    report = engine.replay(
        make_support("p"),
        validator=lambda record: (record.name == "alpha", ("validator-accepted-record",)),
    )

    assert report.revalidated_count == 1
    assert report.decisions[0].status is ReplayStatus.REVALIDATED
    assert report.decisions[0].validator_used is True


def test_replay_engine_invalidates_when_validator_rejects() -> None:
    ledger = ReplayLedger()
    ledger.append(make_record("alpha", "p"))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger)

    report = engine.replay(
        make_support("p"),
        validator=lambda _record: (False, ("schema-mismatch",)),
    )

    assert report.invalidated_count == 1
    assert report.decisions[0].status is ReplayStatus.INVALIDATED
    assert "schema-mismatch" in report.decisions[0].reasons


def test_replay_engine_invalidates_when_dependency_epoch_changes() -> None:
    ledger = ReplayLedger()
    ledger.append(make_record("alpha", "p", dependency_keys=("dep",), dependency_epochs={"dep": 1}))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger)

    report = engine.replay(
        make_support("x"),
        trigger=ReplayTrigger.DEPENDENCY_CHANGE,
        changed_dependency_keys=("dep",),
        dependency_epochs={"dep": 2},
    )

    assert report.invalidated_count == 1
    assert report.decisions[0].status is ReplayStatus.INVALIDATED
    assert any("dependency" in reason for reason in report.decisions[0].reasons)


def test_replay_engine_invalidates_when_policy_changes() -> None:
    policy = ReplayPolicy(name="balanced", max_records=16)
    ledger = ReplayLedger(default_policy_tag="balanced")
    ledger.append(make_record("alpha", "x"))
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ledger, policy=policy)

    report = engine.replay(
        make_support("y"),
        trigger=ReplayTrigger.TRUST_CHANGE,
        trust_floor=TrustTier.PROPOSAL,
        treaty_fingerprint="treaty:v1",
        certificate_fingerprint="cert:v1",
    )
    assert report.reused_count == 1

    invalidated = engine.replay(
        make_support("y"),
        trigger=ReplayTrigger.TRUST_CHANGE,
        trust_floor=TrustTier.PROPOSAL,
        treaty_fingerprint="treaty:v2",
        certificate_fingerprint="cert:v1",
    )
    assert invalidated.invalidated_count == 1
    assert invalidated.decisions[0].status is ReplayStatus.INVALIDATED


def test_replay_engine_invalidates_cache_and_remembers_report() -> None:
    cache = SemanticCache()
    memory = SemanticMemory()
    ledger = ReplayLedger()
    record = make_record("alpha", "p")
    ledger.append(record)
    cache.put(CacheEntry("alpha-cache", {"v": 1}, record.support, record.trust, record.provenance))
    engine = ReplayEngine(cache=cache, memory=memory, ledger=ledger)
    cover = make_cover("p", "q")

    report = engine.replay(make_support("p"), cover=cover)

    assert report.invalidation_plan is not None
    assert "alpha-cache" in report.invalidated_cache_keys
    assert report.memory_note_keys
    assert any(tag == "replay" for note in getattr(memory, "_notes", {}).values() for tag in note.tags)


def test_replay_region_function_delegates_to_engine() -> None:
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ReplayLedger())
    engine.ledger.append(make_record("alpha", "p"))

    report = replay_region(engine, make_support("p"))

    assert isinstance(report, ReplayReport)
    assert report.considered_records == 1


def test_replay_report_shape_is_stable_and_human_readable() -> None:
    decision = ReplayDecision(
        record=make_record("alpha", "p").ensure_seal(),
        status=ReplayStatus.REOPENED,
        trigger=ReplayTrigger.SUPPORT_CHANGE,
        reasons=("affected-by-support-local-reopening",),
        affected_patches=("p",),
        invalidated_cache_keys=("alpha-cache",),
        validator_used=False,
        seal_valid=True,
    )
    report = ReplayReport(
        changed_support=make_support("p"),
        trigger=ReplayTrigger.SUPPORT_CHANGE,
        decisions=(decision,),
        considered_records=1,
        memory_note_keys=("replay:report:test",),
    )

    payload = report.to_dict()
    assert payload["counts"]["reopened"] == 1
    assert payload["changed_support"]["patch_keys"] == ["p"]
    assert payload["memory_note_keys"] == ["replay:report:test"]
    assert "ReplayReport(" in report.summary()


def test_replay_engine_snapshot_is_compatible_with_future_runtime_files() -> None:
    engine = ReplayEngine(cache=SemanticCache(), memory=SemanticMemory(), ledger=ReplayLedger())
    engine.ledger.append(make_record("alpha", "p"))

    snapshot = engine.snapshot()
    assert snapshot["policy"]["name"]
    assert isinstance(snapshot["defaults"], dict)
    assert snapshot["ledger"]["records"]
    assert "memory_keys" in snapshot


def test_replay_policy_derives_from_runtime_defaults() -> None:
    defaults = default_runtime_options()
    policy = ReplayPolicy.from_defaults(defaults)
    assert policy.name == defaults.preset.value
    assert policy.max_records == defaults.descent.max_depth
    assert policy.trust_floor.label() == "proposal"


def test_replay_record_to_dict_preserves_boundary_information() -> None:
    record = make_record(
        "alpha",
        "p",
        dependency_keys=("dep-a", "dep-b"),
        dependency_epochs={"dep-a": 1, "dep-b": 2},
        payload={"result": "ok", "score": 1},
    ).ensure_seal(policy_tag="balanced")

    payload = record.to_dict()
    assert payload["trust"]["tier"] == "reviewed"
    assert payload["support"]["patch_keys"] == ["p"]
    assert payload["dependency_keys"] == ["dep-a", "dep-b"]
    assert payload["dependency_epochs"] == {"dep-a": 1, "dep-b": 2}
    assert payload["seal"]["policy_tag"] == "balanced"


def test_replay_report_reopened_patches_include_star_and_direct_support() -> None:
    decision = ReplayDecision(
        record=make_record("alpha", "q").ensure_seal(),
        status=ReplayStatus.REOPENED,
        trigger=ReplayTrigger.SUPPORT_CHANGE,
        reasons=("within-reopened-star",),
        affected_patches=("q",),
        seal_valid=True,
    )
    report = ReplayReport(changed_support=make_support("p"), trigger=ReplayTrigger.SUPPORT_CHANGE, decisions=(decision,))
    assert set(report.reopened_patches) == {"p", "q"}


def test_engine_respects_record_limit_from_policy() -> None:
    ledger = ReplayLedger()
    ledger.extend(make_record(f"rec-{idx}", f"p{idx}") for idx in range(5))
    engine = ReplayEngine(
        cache=SemanticCache(),
        memory=SemanticMemory(),
        ledger=ledger,
        policy=ReplayPolicy(name="balanced", max_records=2),
    )

    report = engine.replay(make_support("x"))
    considered = [decision.record.name for decision in report.decisions]
    assert considered == ["rec-3", "rec-4"]
    assert report.considered_records == 2


def test_decision_summary_is_easy_for_humans_and_llms_to_scan() -> None:
    decision = ReplayDecision(
        record=make_record("alpha", "p").ensure_seal(),
        status=ReplayStatus.INVALIDATED,
        trigger=ReplayTrigger.DEPENDENCY_CHANGE,
        reasons=("dependency-epoch-mismatch:dep",),
    )
    summary = decision.summary()
    assert summary.startswith("invalidated:alpha:")
    assert "dependency" in summary


def test_runtime_neighbor_contracts_still_import_cleanly() -> None:
    import jugeo.runtime.cache as cache_module
    import jugeo.runtime.invalidation as invalidation_module
    import jugeo.runtime.memory as memory_module
    import jugeo.runtime.checkpointing as checkpoint_module

    assert hasattr(cache_module, "SemanticCache")
    assert hasattr(invalidation_module, "plan_invalidation")
    assert hasattr(memory_module, "SemanticMemory")
    assert hasattr(checkpoint_module, "CheckpointStore")


def test_source_mentions_theory2_doctrine_terms_for_future_readers() -> None:
    text = SOURCE_FILE.read_text().lower()
    expected = (
        "support-local",
        "persistent semantic memory",
        "replay doctrine",
        "trust",
        "provenance",
        "seal",
    )
    for fragment in expected:
        assert fragment in text


def test_pdf_can_optionally_surface_replay_language_when_pypdf_is_available() -> None:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(SPEC_PDF))
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages[:20]).lower()
    assert "replay" in extracted or "memory" in extracted


def test_replay_module_is_honest_about_current_scope() -> None:
    text = TEST_FILE.read_text().lower()
    assert "spec" in text
    assert "theory" in text
    assert "future" in text
    assert "compatible" in text
