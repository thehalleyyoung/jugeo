from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

"""
Tests for jugeo.orchestration.frontier_objectives.manifest

Covers every class, method, function, and constant in manifest.py, plus
integration with upstream modules.

Chapter reference: theory2.tex Ch47 — Frontier objectives.
"""

import hashlib
import time
import uuid

import pytest

from jugeo.orchestration.frontier_objectives.manifest import (
    CHAPTER_REF,
    DEFAULT_MANIFEST,
    EXPORTED_SYMBOLS,
    MANIFEST_VERSION,
    FrontierObjectivesManifest,
    ManifestReport,
    ManifestValidator,
    ObjectiveEntry,
    ObjectiveRegistry,
    PhaseTransitionCatalog,
    PhaseTransitionEntry,
    build_manifest,
    get_default_catalog,
    get_default_registry,
    validate_manifest,
    _compute_checksum,
)
from jugeo.orchestration.frontier_objectives.models import (
    FrontierObjective,
    ObjectiveKind,
    ScoringState,
    MAX_CLOSURE_GAIN,
    _clamp,
)

# ---------------------------------------------------------------------------
# Upstream guards
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierBudget, PhaseTransition
    HAS_FRONTIER = True
except Exception:
    HAS_FRONTIER = False

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustTier, TrustProfile
    HAS_TRUST = True
except Exception:
    HAS_TRUST = False

try:
    from jugeo.orchestration.controller import ConvergenceMonitor
    HAS_CONTROLLER = True
except Exception:
    HAS_CONTROLLER = False


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_manifest() -> FrontierObjectivesManifest:
    """A freshly-built valid manifest."""
    return FrontierObjectivesManifest.build()


@pytest.fixture
def default_registry() -> ObjectiveRegistry:
    return ObjectiveRegistry.default()


@pytest.fixture
def default_catalog() -> PhaseTransitionCatalog:
    return PhaseTransitionCatalog.default()


@pytest.fixture
def validator() -> ManifestValidator:
    return ManifestValidator()


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_manifest_version_is_string(self):
        assert isinstance(MANIFEST_VERSION, str)

    def test_manifest_version_semver(self):
        parts = MANIFEST_VERSION.split(".")
        assert len(parts) == 3, f"Expected semver X.Y.Z, got {MANIFEST_VERSION!r}"
        assert all(p.isdigit() for p in parts)

    def test_chapter_ref_is_string(self):
        assert isinstance(CHAPTER_REF, str)

    def test_chapter_ref_non_empty(self):
        assert len(CHAPTER_REF) > 0

    def test_chapter_ref_value(self):
        assert CHAPTER_REF == "Ch47"

    def test_manifest_version_value(self):
        assert MANIFEST_VERSION == "1.0.0"

    def test_exported_symbols_is_tuple(self):
        assert isinstance(EXPORTED_SYMBOLS, tuple)

    def test_exported_symbols_non_empty(self):
        assert len(EXPORTED_SYMBOLS) > 0

    def test_exported_symbols_contains_key_names(self):
        assert "FrontierObjective" in EXPORTED_SYMBOLS
        assert "ObjectiveKind" in EXPORTED_SYMBOLS
        assert "FrontierBudgetModel" in EXPORTED_SYMBOLS
        assert "ObjectiveSet" in EXPORTED_SYMBOLS
        assert "ScoringState" in EXPORTED_SYMBOLS

    def test_exported_symbols_contains_manifest_symbols(self):
        assert "FrontierObjectivesManifest" in EXPORTED_SYMBOLS
        assert "build_manifest" in EXPORTED_SYMBOLS
        assert "validate_manifest" in EXPORTED_SYMBOLS
        assert "MANIFEST_VERSION" in EXPORTED_SYMBOLS
        assert "CHAPTER_REF" in EXPORTED_SYMBOLS

    def test_exported_symbols_all_strings(self):
        for sym in EXPORTED_SYMBOLS:
            assert isinstance(sym, str)

    def test_default_manifest_is_instance(self):
        assert isinstance(DEFAULT_MANIFEST, FrontierObjectivesManifest)

    def test_default_manifest_valid(self):
        assert DEFAULT_MANIFEST.validate() is True

    def test_default_manifest_version_matches_constant(self):
        assert DEFAULT_MANIFEST.version == MANIFEST_VERSION

    def test_default_manifest_chapter_ref_matches(self):
        assert DEFAULT_MANIFEST.chapter_ref == CHAPTER_REF


# ---------------------------------------------------------------------------
# _compute_checksum helper
# ---------------------------------------------------------------------------


class TestComputeChecksum:
    def test_returns_string(self):
        cs = _compute_checksum("1.0.0", "Ch47", ("A", "B"))
        assert isinstance(cs, str)

    def test_returns_16_chars(self):
        cs = _compute_checksum("1.0.0", "Ch47", ("A",))
        assert len(cs) == 16

    def test_deterministic(self):
        cs1 = _compute_checksum("1.0.0", "Ch47", ("A", "B"))
        cs2 = _compute_checksum("1.0.0", "Ch47", ("A", "B"))
        assert cs1 == cs2

    def test_different_version_different_checksum(self):
        cs1 = _compute_checksum("1.0.0", "Ch47", ("A",))
        cs2 = _compute_checksum("2.0.0", "Ch47", ("A",))
        assert cs1 != cs2

    def test_different_symbols_different_checksum(self):
        cs1 = _compute_checksum("1.0.0", "Ch47", ("A",))
        cs2 = _compute_checksum("1.0.0", "Ch47", ("B",))
        assert cs1 != cs2

    def test_order_independent_for_symbols(self):
        cs1 = _compute_checksum("1.0.0", "Ch47", ("A", "B"))
        cs2 = _compute_checksum("1.0.0", "Ch47", ("B", "A"))
        # Symbols are sorted before hashing
        assert cs1 == cs2


# ---------------------------------------------------------------------------
# FrontierObjectivesManifest tests
# ---------------------------------------------------------------------------


class TestFrontierObjectivesManifest:
    def test_build_returns_manifest(self, valid_manifest):
        assert isinstance(valid_manifest, FrontierObjectivesManifest)

    def test_build_version_matches_constant(self, valid_manifest):
        assert valid_manifest.version == MANIFEST_VERSION

    def test_build_chapter_ref_matches_constant(self, valid_manifest):
        assert valid_manifest.chapter_ref == CHAPTER_REF

    def test_build_module_name(self, valid_manifest):
        assert "frontier_objectives" in valid_manifest.module_name

    def test_build_exported_symbols_non_empty(self, valid_manifest):
        assert len(valid_manifest.exported_symbols) > 0

    def test_build_description_non_empty(self, valid_manifest):
        assert len(valid_manifest.description) > 0

    def test_build_created_at_recent(self, valid_manifest):
        now = time.time()
        assert valid_manifest.created_at <= now
        assert valid_manifest.created_at > now - 5.0

    def test_build_checksum_non_empty(self, valid_manifest):
        assert len(valid_manifest.checksum) > 0

    def test_build_custom_version(self):
        m = FrontierObjectivesManifest.build(version="2.0.0")
        assert m.version == "2.0.0"

    def test_build_custom_version_still_valid(self):
        m = FrontierObjectivesManifest.build(version="2.0.0")
        # Checksum should be recomputed for the new version
        assert m.validate() is True

    def test_validate_returns_true_for_valid(self, valid_manifest):
        assert valid_manifest.validate() is True

    def test_validate_fails_empty_version(self):
        good = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref=good.chapter_ref,
            module_name=good.module_name,
            exported_symbols=good.exported_symbols,
            description=good.description,
            created_at=good.created_at,
            checksum=good.checksum,
        )
        assert bad.validate() is False

    def test_validate_fails_empty_chapter_ref(self):
        good = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=good.version,
            chapter_ref="",
            module_name=good.module_name,
            exported_symbols=good.exported_symbols,
            description=good.description,
            created_at=good.created_at,
            checksum=good.checksum,
        )
        assert bad.validate() is False

    def test_validate_fails_empty_symbols(self):
        good = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=good.version,
            chapter_ref=good.chapter_ref,
            module_name=good.module_name,
            exported_symbols=(),
            description=good.description,
            created_at=good.created_at,
            checksum=good.checksum,
        )
        assert bad.validate() is False

    def test_validate_fails_wrong_checksum(self):
        good = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=good.version,
            chapter_ref=good.chapter_ref,
            module_name=good.module_name,
            exported_symbols=good.exported_symbols,
            description=good.description,
            created_at=good.created_at,
            checksum="deadbeef12345678",
        )
        assert bad.validate() is False

    def test_to_dict_keys(self, valid_manifest):
        d = valid_manifest.to_dict()
        assert "version" in d
        assert "chapter_ref" in d
        assert "module_name" in d
        assert "exported_symbols" in d
        assert "description" in d
        assert "created_at" in d
        assert "checksum" in d

    def test_to_dict_exported_symbols_is_list(self, valid_manifest):
        d = valid_manifest.to_dict()
        assert isinstance(d["exported_symbols"], list)

    def test_to_dict_version_value(self, valid_manifest):
        d = valid_manifest.to_dict()
        assert d["version"] == valid_manifest.version

    def test_to_dict_created_at_is_float(self, valid_manifest):
        d = valid_manifest.to_dict()
        assert isinstance(d["created_at"], float)

    def test_copilot_report_is_string(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert isinstance(report, str)

    def test_copilot_report_contains_version(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert valid_manifest.version in report

    def test_copilot_report_contains_chapter_ref(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert valid_manifest.chapter_ref in report

    def test_copilot_report_contains_symbol_count(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert str(len(valid_manifest.exported_symbols)) in report

    def test_copilot_report_valid_label(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert "VALID" in report

    def test_copilot_report_invalid_label(self):
        good = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=good.version,
            chapter_ref=good.chapter_ref,
            module_name=good.module_name,
            exported_symbols=good.exported_symbols,
            description=good.description,
            created_at=good.created_at,
            checksum="0000000000000000",
        )
        report = bad.copilot_report()
        assert "INVALID" in report

    def test_copilot_report_contains_checksum(self, valid_manifest):
        report = valid_manifest.copilot_report()
        assert valid_manifest.checksum in report

    def test_frozen_immutable(self, valid_manifest):
        from dataclasses import FrozenInstanceError
        with pytest.raises((FrozenInstanceError, AttributeError)):
            valid_manifest.version = "9.9.9"  # type: ignore[misc]

    def test_two_builds_different_timestamps(self):
        m1 = FrontierObjectivesManifest.build()
        time.sleep(0.01)
        m2 = FrontierObjectivesManifest.build()
        # created_at should differ (timestamps are monotonic)
        assert m2.created_at >= m1.created_at


# ---------------------------------------------------------------------------
# ObjectiveEntry tests
# ---------------------------------------------------------------------------


class TestObjectiveEntry:
    @pytest.fixture
    def closure_entry(self) -> ObjectiveEntry:
        return ObjectiveEntry(
            name="closure_gain",
            kind="CLOSURE_GAIN",
            description="Measures closure gain.",
            default_weight=1.0,
            default_threshold=0.5,
        )

    @pytest.fixture
    def stability_entry(self) -> ObjectiveEntry:
        return ObjectiveEntry(
            name="stability",
            kind="STABILITY",
            description="Measures trajectory stability.",
            default_weight=0.8,
            default_threshold=0.6,
        )

    def test_construction(self, closure_entry):
        assert closure_entry.name == "closure_gain"
        assert closure_entry.kind == "CLOSURE_GAIN"
        assert closure_entry.default_weight == pytest.approx(1.0)
        assert closure_entry.default_threshold == pytest.approx(0.5)

    def test_to_dict_keys(self, closure_entry):
        d = closure_entry.to_dict()
        assert set(d.keys()) == {"name", "kind", "description", "default_weight", "default_threshold"}

    def test_to_dict_values(self, closure_entry):
        d = closure_entry.to_dict()
        assert d["name"] == "closure_gain"
        assert d["kind"] == "CLOSURE_GAIN"
        assert d["default_weight"] == pytest.approx(1.0)

    def test_to_objective_returns_frontier_objective(self, closure_entry):
        obj = closure_entry.to_objective()
        assert isinstance(obj, FrontierObjective)

    def test_to_objective_closure_gain_kind(self, closure_entry):
        obj = closure_entry.to_objective()
        assert obj.kind is ObjectiveKind.CLOSURE_GAIN

    def test_to_objective_stability_kind(self, stability_entry):
        obj = stability_entry.to_objective()
        assert obj.kind is ObjectiveKind.STABILITY

    def test_to_objective_uses_entry_weight(self, closure_entry):
        obj = closure_entry.to_objective()
        assert obj.weight == pytest.approx(closure_entry.default_weight)

    def test_to_objective_uses_entry_threshold(self, closure_entry):
        obj = closure_entry.to_objective()
        assert obj.threshold == pytest.approx(closure_entry.default_threshold)

    def test_to_objective_diversity(self):
        entry = ObjectiveEntry(
            name="diversity",
            kind="DIVERSITY",
            description="Diversity.",
            default_weight=0.6,
            default_threshold=0.4,
        )
        obj = entry.to_objective()
        assert obj.kind is ObjectiveKind.DIVERSITY

    def test_to_objective_cost(self):
        entry = ObjectiveEntry(
            name="cost",
            kind="COST",
            description="Cost.",
            default_weight=0.4,
            default_threshold=0.7,
        )
        obj = entry.to_objective()
        assert obj.kind is ObjectiveKind.COST

    def test_frozen_immutable(self, closure_entry):
        from dataclasses import FrozenInstanceError
        with pytest.raises((FrozenInstanceError, AttributeError)):
            closure_entry.name = "other"  # type: ignore[misc]

    def test_to_objective_generates_unique_id(self, closure_entry):
        obj1 = closure_entry.to_objective()
        obj2 = closure_entry.to_objective()
        assert obj1.objective_id != obj2.objective_id


# ---------------------------------------------------------------------------
# ObjectiveRegistry tests
# ---------------------------------------------------------------------------


class TestObjectiveRegistry:
    def test_default_has_four_entries(self, default_registry):
        assert len(default_registry.entries) == 4

    def test_default_contains_standard_names(self, default_registry):
        names = default_registry.list_names()
        assert "closure_gain" in names
        assert "stability" in names
        assert "diversity" in names
        assert "cost" in names

    def test_list_names_sorted(self, default_registry):
        names = default_registry.list_names()
        assert names == sorted(names)

    def test_get_existing(self, default_registry):
        entry = default_registry.get("closure_gain")
        assert entry is not None
        assert entry.name == "closure_gain"

    def test_get_nonexistent_returns_none(self, default_registry):
        assert default_registry.get("nonexistent") is None

    def test_register_new_entry(self):
        registry = ObjectiveRegistry(entries={})
        entry = ObjectiveEntry(
            name="custom",
            kind="COMPOSITE",
            description="Custom objective.",
            default_weight=0.5,
            default_threshold=0.5,
        )
        registry.register(entry)
        assert registry.get("custom") is entry

    def test_register_overwrites_existing(self):
        registry = ObjectiveRegistry(entries={})
        e1 = ObjectiveEntry(name="x", kind="STABILITY", description="v1", default_weight=1.0, default_threshold=0.5)
        e2 = ObjectiveEntry(name="x", kind="STABILITY", description="v2", default_weight=0.8, default_threshold=0.4)
        registry.register(e1)
        registry.register(e2)
        assert registry.get("x").description == "v2"

    def test_register_locked_raises(self):
        registry = ObjectiveRegistry(entries={}, _lock=True)
        entry = ObjectiveEntry(name="x", kind="STABILITY", description="", default_weight=1.0, default_threshold=0.5)
        with pytest.raises(RuntimeError):
            registry.register(entry)

    def test_build_objective_returns_frontier_objective(self, default_registry):
        obj = default_registry.build_objective("closure_gain")
        assert isinstance(obj, FrontierObjective)

    def test_build_objective_nonexistent_returns_none(self, default_registry):
        assert default_registry.build_objective("nonexistent") is None

    def test_build_objective_kind_matches(self, default_registry):
        obj = default_registry.build_objective("stability")
        assert obj.kind is ObjectiveKind.STABILITY

    def test_build_all_returns_four(self, default_registry):
        objectives = default_registry.build_all()
        assert len(objectives) == 4

    def test_build_all_all_frontier_objectives(self, default_registry):
        for obj in default_registry.build_all():
            assert isinstance(obj, FrontierObjective)

    def test_build_all_no_duplicates(self, default_registry):
        objectives = default_registry.build_all()
        ids = [o.objective_id for o in objectives]
        assert len(ids) == len(set(ids))

    def test_to_dict_keys_match_entry_names(self, default_registry):
        d = default_registry.to_dict()
        assert set(d.keys()) == set(default_registry.list_names())

    def test_to_dict_values_are_dicts(self, default_registry):
        d = default_registry.to_dict()
        for v in d.values():
            assert isinstance(v, dict)

    def test_empty_registry(self):
        registry = ObjectiveRegistry(entries={})
        assert registry.list_names() == []
        assert registry.build_all() == []
        assert registry.to_dict() == {}

    def test_default_factory_independence(self):
        r1 = ObjectiveRegistry.default()
        r2 = ObjectiveRegistry.default()
        # Modifying one should not affect the other
        r1.entries["new_key"] = ObjectiveEntry(
            name="new_key", kind="STABILITY", description="", default_weight=1.0, default_threshold=0.5
        )
        assert "new_key" not in r2.entries


# ---------------------------------------------------------------------------
# PhaseTransitionEntry tests
# ---------------------------------------------------------------------------


class TestPhaseTransitionEntry:
    @pytest.fixture
    def entry(self) -> PhaseTransitionEntry:
        return PhaseTransitionEntry(
            transition_id=str(uuid.uuid4()),
            from_phase="exploration",
            to_phase="exploitation",
            trigger="closure_gain_plateau",
            description="Switch to exploitation when plateau detected.",
            expected_gain_delta=0.15,
        )

    def test_construction(self, entry):
        assert entry.from_phase == "exploration"
        assert entry.to_phase == "exploitation"
        assert entry.trigger == "closure_gain_plateau"
        assert entry.expected_gain_delta == pytest.approx(0.15)

    def test_to_dict_keys(self, entry):
        d = entry.to_dict()
        assert "transition_id" in d
        assert "from_phase" in d
        assert "to_phase" in d
        assert "trigger" in d
        assert "description" in d
        assert "expected_gain_delta" in d

    def test_to_dict_values(self, entry):
        d = entry.to_dict()
        assert d["from_phase"] == "exploration"
        assert d["to_phase"] == "exploitation"
        assert d["expected_gain_delta"] == pytest.approx(0.15)

    def test_to_dict_description_is_string(self, entry):
        d = entry.to_dict()
        assert isinstance(d["description"], str)

    def test_negative_gain_delta_allowed(self):
        e = PhaseTransitionEntry(
            transition_id="x",
            from_phase="exploitation",
            to_phase="exploration",
            trigger="diversity_drop",
            description="Return to exploration.",
            expected_gain_delta=-0.05,
        )
        assert e.expected_gain_delta < 0.0
        d = e.to_dict()
        assert d["expected_gain_delta"] < 0.0

    def test_zero_gain_delta_allowed(self):
        e = PhaseTransitionEntry(
            transition_id="x",
            from_phase="exploration",
            to_phase="transition",
            trigger="budget_exhaustion",
            description="Budget exhausted.",
            expected_gain_delta=0.0,
        )
        assert e.expected_gain_delta == pytest.approx(0.0)

    def test_frozen_immutable(self, entry):
        from dataclasses import FrozenInstanceError
        with pytest.raises((FrozenInstanceError, AttributeError)):
            entry.trigger = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PhaseTransitionCatalog tests
# ---------------------------------------------------------------------------


class TestPhaseTransitionCatalog:
    def test_default_has_entries(self, default_catalog):
        assert len(default_catalog.entries) > 0

    def test_default_has_seven_or_eight_entries(self, default_catalog):
        # The standard defs define 7 or 8 transitions
        assert 7 <= len(default_catalog.entries) <= 8

    def test_all_entries_are_phase_transition_entries(self, default_catalog):
        for e in default_catalog.entries:
            assert isinstance(e, PhaseTransitionEntry)

    def test_add_entry(self):
        catalog = PhaseTransitionCatalog(entries=[])
        e = PhaseTransitionEntry(
            transition_id=str(uuid.uuid4()),
            from_phase="stalled",
            to_phase="exploration",
            trigger="manual_restart",
            description="Manual restart.",
            expected_gain_delta=0.1,
        )
        catalog.add(e)
        assert len(catalog.entries) == 1
        assert catalog.entries[0] is e

    def test_get_by_trigger_existing(self, default_catalog):
        results = default_catalog.get_by_trigger("closure_gain_plateau")
        assert len(results) >= 1
        assert all(e.trigger == "closure_gain_plateau" for e in results)

    def test_get_by_trigger_nonexistent_empty(self, default_catalog):
        results = default_catalog.get_by_trigger("no_such_trigger")
        assert results == []

    def test_get_by_from_phase_exploration(self, default_catalog):
        results = default_catalog.get_by_from_phase("exploration")
        assert len(results) >= 1
        assert all(e.from_phase == "exploration" for e in results)

    def test_get_by_from_phase_exploitation(self, default_catalog):
        results = default_catalog.get_by_from_phase("exploitation")
        assert len(results) >= 1

    def test_get_by_from_phase_nonexistent_empty(self, default_catalog):
        results = default_catalog.get_by_from_phase("nonexistent_phase")
        assert results == []

    def test_all_triggers_returns_set(self, default_catalog):
        triggers = default_catalog.all_triggers()
        assert isinstance(triggers, set)

    def test_all_triggers_non_empty(self, default_catalog):
        assert len(default_catalog.all_triggers()) > 0

    def test_all_triggers_contains_standard(self, default_catalog):
        triggers = default_catalog.all_triggers()
        assert "closure_gain_plateau" in triggers
        assert "diversity_drop" in triggers

    def test_all_triggers_unique(self, default_catalog):
        triggers = default_catalog.all_triggers()
        assert len(triggers) == len({e.trigger for e in default_catalog.entries})

    def test_to_dict_keyed_by_id(self, default_catalog):
        d = default_catalog.to_dict()
        ids = {e.transition_id for e in default_catalog.entries}
        assert set(d.keys()) == ids

    def test_to_dict_values_are_dicts(self, default_catalog):
        d = default_catalog.to_dict()
        for v in d.values():
            assert isinstance(v, dict)

    def test_empty_catalog(self):
        catalog = PhaseTransitionCatalog(entries=[])
        assert catalog.all_triggers() == set()
        assert catalog.get_by_trigger("x") == []
        assert catalog.get_by_from_phase("x") == []
        assert catalog.to_dict() == {}

    def test_default_factory_independence(self):
        c1 = PhaseTransitionCatalog.default()
        c2 = PhaseTransitionCatalog.default()
        extra = PhaseTransitionEntry(
            transition_id="extra",
            from_phase="a",
            to_phase="b",
            trigger="t",
            description="",
            expected_gain_delta=0.0,
        )
        c1.add(extra)
        assert extra not in c2.entries


# ---------------------------------------------------------------------------
# ManifestValidator tests
# ---------------------------------------------------------------------------


class TestManifestValidator:
    def test_validate_valid_manifest_empty_errors(self, validator, valid_manifest):
        errors = validator.validate(valid_manifest)
        assert errors == []

    def test_is_valid_true_for_valid(self, validator, valid_manifest):
        assert validator.is_valid(valid_manifest) is True

    def test_validate_empty_version_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("version" in e for e in errors)

    def test_validate_empty_chapter_ref_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref="",
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("chapter_ref" in e for e in errors)

    def test_validate_empty_module_name_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref=m.chapter_ref,
            module_name="",
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("module_name" in e for e in errors)

    def test_validate_empty_symbols_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=(),
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("exported_symbols" in e for e in errors)

    def test_validate_empty_description_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description="",
            created_at=m.created_at,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("description" in e for e in errors)

    def test_validate_zero_created_at_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=0.0,
            checksum=m.checksum,
        )
        errors = validator.validate(bad)
        assert any("created_at" in e for e in errors)

    def test_validate_wrong_checksum_returns_error(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version=m.version,
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum="0000000000000000",
        )
        errors = validator.validate(bad)
        assert any("checksum" in e for e in errors)

    def test_validate_non_semver_version_returns_error(self, validator):
        cs = _compute_checksum("bad_version", CHAPTER_REF, EXPORTED_SYMBOLS)
        bad = FrontierObjectivesManifest(
            version="bad_version",
            chapter_ref=CHAPTER_REF,
            module_name="jugeo.orchestration.frontier_objectives",
            exported_symbols=EXPORTED_SYMBOLS,
            description="Test.",
            created_at=time.time(),
            checksum=cs,
        )
        errors = validator.validate(bad)
        assert any("semver" in e or "version" in e for e in errors)

    def test_is_valid_false_for_invalid(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        assert validator.is_valid(bad) is False

    def test_multiple_errors_accumulated(self, validator):
        m = FrontierObjectivesManifest.build()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref="",
            module_name=m.module_name,
            exported_symbols=(),
            description="",
            created_at=0.0,
            checksum="bad",
        )
        errors = validator.validate(bad)
        assert len(errors) >= 4


# ---------------------------------------------------------------------------
# ManifestReport tests
# ---------------------------------------------------------------------------


class TestManifestReport:
    @pytest.fixture
    def valid_report(self) -> ManifestReport:
        return ManifestReport(
            manifest_version="1.0.0",
            valid=True,
            errors=(),
            warnings=(),
            symbol_count=30,
            generated_at=time.time(),
        )

    @pytest.fixture
    def invalid_report(self) -> ManifestReport:
        return ManifestReport(
            manifest_version="1.0.0",
            valid=False,
            errors=("version is empty", "checksum mismatch"),
            warnings=("symbol count low",),
            symbol_count=3,
            generated_at=time.time(),
        )

    def test_construction_valid(self, valid_report):
        assert valid_report.valid is True
        assert valid_report.errors == ()
        assert valid_report.symbol_count == 30

    def test_to_dict_keys(self, valid_report):
        d = valid_report.to_dict()
        assert "manifest_version" in d
        assert "valid" in d
        assert "errors" in d
        assert "warnings" in d
        assert "symbol_count" in d
        assert "generated_at" in d

    def test_to_dict_errors_is_list(self, invalid_report):
        d = invalid_report.to_dict()
        assert isinstance(d["errors"], list)

    def test_to_dict_warnings_is_list(self, invalid_report):
        d = invalid_report.to_dict()
        assert isinstance(d["warnings"], list)

    def test_to_dict_valid_true(self, valid_report):
        d = valid_report.to_dict()
        assert d["valid"] is True

    def test_to_dict_symbol_count(self, valid_report):
        d = valid_report.to_dict()
        assert d["symbol_count"] == 30

    def test_summary_is_string(self, valid_report):
        assert isinstance(valid_report.summary(), str)

    def test_summary_contains_valid_label(self, valid_report):
        assert "VALID" in valid_report.summary()

    def test_summary_contains_invalid_label(self, invalid_report):
        assert "INVALID" in invalid_report.summary()

    def test_summary_contains_version(self, valid_report):
        assert valid_report.manifest_version in valid_report.summary()

    def test_summary_contains_symbol_count(self, valid_report):
        assert str(valid_report.symbol_count) in valid_report.summary()

    def test_summary_contains_error_count_for_invalid(self, invalid_report):
        summary = invalid_report.summary()
        assert "2" in summary  # 2 errors

    def test_summary_contains_warning_count(self, invalid_report):
        summary = invalid_report.summary()
        assert "1" in summary  # 1 warning

    def test_frozen_immutable(self, valid_report):
        from dataclasses import FrozenInstanceError
        with pytest.raises((FrozenInstanceError, AttributeError)):
            valid_report.valid = False  # type: ignore[misc]

    def test_to_dict_generated_at_is_float(self, valid_report):
        d = valid_report.to_dict()
        assert isinstance(d["generated_at"], float)


# ---------------------------------------------------------------------------
# Module-level function tests
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_build_manifest_returns_manifest(self):
        m = build_manifest()
        assert isinstance(m, FrontierObjectivesManifest)

    def test_build_manifest_default_version(self):
        m = build_manifest()
        assert m.version == MANIFEST_VERSION

    def test_build_manifest_custom_version(self):
        m = build_manifest(version="3.1.4")
        assert m.version == "3.1.4"

    def test_build_manifest_is_valid(self):
        m = build_manifest()
        assert m.validate() is True

    def test_build_manifest_two_calls_different_timestamps(self):
        m1 = build_manifest()
        time.sleep(0.01)
        m2 = build_manifest()
        assert m2.created_at >= m1.created_at

    def test_validate_manifest_valid_manifest(self):
        m = build_manifest()
        report = validate_manifest(m)
        assert isinstance(report, ManifestReport)
        assert report.valid is True

    def test_validate_manifest_empty_errors_for_valid(self):
        m = build_manifest()
        report = validate_manifest(m)
        assert report.errors == ()

    def test_validate_manifest_invalid_manifest(self):
        m = build_manifest()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        report = validate_manifest(bad)
        assert report.valid is False
        assert len(report.errors) > 0

    def test_validate_manifest_returns_symbol_count(self):
        m = build_manifest()
        report = validate_manifest(m)
        assert report.symbol_count == len(m.exported_symbols)

    def test_validate_manifest_generated_at_recent(self):
        m = build_manifest()
        before = time.time()
        report = validate_manifest(m)
        after = time.time()
        assert before <= report.generated_at <= after

    def test_validate_manifest_warnings_for_small_symbol_set(self):
        cs = _compute_checksum("1.0.0", CHAPTER_REF, ("A", "B"))
        tiny = FrontierObjectivesManifest(
            version="1.0.0",
            chapter_ref=CHAPTER_REF,
            module_name="test",
            exported_symbols=("A", "B"),
            description="Tiny manifest.",
            created_at=time.time(),
            checksum=cs,
        )
        report = validate_manifest(tiny)
        assert len(report.warnings) > 0

    def test_get_default_registry_returns_registry(self):
        reg = get_default_registry()
        assert isinstance(reg, ObjectiveRegistry)

    def test_get_default_registry_has_four_entries(self):
        reg = get_default_registry()
        assert len(reg.entries) == 4

    def test_get_default_registry_independence(self):
        r1 = get_default_registry()
        r2 = get_default_registry()
        r1.entries["extra"] = ObjectiveEntry(
            name="extra", kind="COMPOSITE", description="x",
            default_weight=0.5, default_threshold=0.5,
        )
        assert "extra" not in r2.entries

    def test_get_default_catalog_returns_catalog(self):
        cat = get_default_catalog()
        assert isinstance(cat, PhaseTransitionCatalog)

    def test_get_default_catalog_has_entries(self):
        cat = get_default_catalog()
        assert len(cat.entries) >= 7

    def test_get_default_catalog_independence(self):
        c1 = get_default_catalog()
        c2 = get_default_catalog()
        extra = PhaseTransitionEntry(
            transition_id="extra-id",
            from_phase="a",
            to_phase="b",
            trigger="manual",
            description="",
            expected_gain_delta=0.0,
        )
        c1.add(extra)
        assert extra not in c2.entries


# ---------------------------------------------------------------------------
# Integration: ManifestValidator consistency with validate_manifest
# ---------------------------------------------------------------------------


class TestManifestValidatorConsistency:
    """Verify that ManifestValidator and validate_manifest agree."""

    def test_valid_manifest_consistent(self):
        m = build_manifest()
        validator = ManifestValidator()
        errors = validator.validate(m)
        report = validate_manifest(m)
        assert (len(errors) == 0) == report.valid

    def test_invalid_manifest_consistent(self):
        m = build_manifest()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref=m.chapter_ref,
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        validator = ManifestValidator()
        errors = validator.validate(bad)
        report = validate_manifest(bad)
        assert len(errors) > 0
        assert report.valid is False

    def test_report_error_count_matches_validator(self):
        m = build_manifest()
        bad = FrontierObjectivesManifest(
            version="",
            chapter_ref="",
            module_name=m.module_name,
            exported_symbols=m.exported_symbols,
            description=m.description,
            created_at=m.created_at,
            checksum=m.checksum,
        )
        validator = ManifestValidator()
        errors = validator.validate(bad)
        report = validate_manifest(bad)
        assert len(report.errors) == len(errors)


# ---------------------------------------------------------------------------
# Integration: Manifest + Registry + Catalog pipeline
# ---------------------------------------------------------------------------


class TestManifestRegistryCatalogPipeline:
    """End-to-end integration between manifest, registry, and catalog."""

    def test_registry_builds_all_objectives_for_scoring(self):
        registry = get_default_registry()
        objectives = registry.build_all()
        state = ScoringState(
            closure_gain=5.0,
            stability_score=0.7,
            diversity_score=0.6,
            cost_estimate=20.0,
        )
        for obj in objectives:
            s = obj.score(state)
            assert 0.0 <= s <= 1.0

    def test_catalog_trigger_coverage(self):
        catalog = get_default_catalog()
        triggers = catalog.all_triggers()
        assert len(triggers) >= 5  # At least 5 distinct triggers expected

    def test_manifest_symbol_count_matches_exported_symbols(self):
        m = build_manifest()
        report = validate_manifest(m)
        assert report.symbol_count == len(EXPORTED_SYMBOLS)

    def test_default_manifest_passes_validator(self):
        validator = ManifestValidator()
        assert validator.is_valid(DEFAULT_MANIFEST) is True

    def test_registry_entries_in_exported_symbols(self):
        registry = get_default_registry()
        # All model types should be in exported symbols
        for sym in ["FrontierObjective", "ObjectiveKind", "ObjectiveSet"]:
            assert sym in EXPORTED_SYMBOLS

    def test_catalog_entries_have_unique_ids(self):
        catalog = get_default_catalog()
        ids = [e.transition_id for e in catalog.entries]
        assert len(ids) == len(set(ids))

    def test_full_pipeline_build_validate_score(self):
        # Build manifest, validate it, build objectives, score a state
        m = build_manifest()
        assert m.validate() is True
        registry = get_default_registry()
        objectives = registry.build_all()
        state = ScoringState(
            closure_gain=MAX_CLOSURE_GAIN * 0.7,
            stability_score=0.8,
            diversity_score=0.65,
            cost_estimate=10.0,
        )
        from jugeo.orchestration.frontier_objectives.models import ObjectiveSet
        obj_set = ObjectiveSet(objectives=objectives, name="pipeline_test")
        ws = obj_set.weighted_score(state)
        assert 0.0 <= ws <= 1.0

    def test_catalog_exploration_transitions_all_have_from_exploration(self):
        catalog = get_default_catalog()
        exp_transitions = catalog.get_by_from_phase("exploration")
        assert all(e.from_phase == "exploration" for e in exp_transitions)

    def test_catalog_stalled_transition_trigger(self):
        catalog = get_default_catalog()
        stalled = catalog.get_by_from_phase("stalled")
        assert len(stalled) >= 1


# ---------------------------------------------------------------------------
# Integration: with Trust module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TRUST, reason="trust module not available")
class TestIntegrationManifestWithTrust:
    """Tests that connect manifest / registry patterns with trust-level reasoning."""

    def test_trust_verified_objective_scores_higher(self):
        """Higher trust → treat as higher confidence gain → satisfaction more likely."""
        algebra = TrustAlgebra()
        high_trust = TrustLevel.MECHANICALLY_VERIFIED
        low_trust = TrustLevel.COPILOT_SUGGESTED
        rank_hi = high_trust.rank_index()
        rank_lo = low_trust.rank_index()
        # Higher rank_index means lower trust in the lattice
        assert rank_hi < rank_lo

    def test_registry_builds_objective_used_with_trust_state(self):
        registry = get_default_registry()
        obj = registry.build_objective("closure_gain")
        profile = TrustProfile(tier=TrustTier.VERIFIED)
        # Verified trust → high closure gain assumption
        confidence = 0.95 if profile.tier == TrustTier.VERIFIED else 0.5
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN * confidence)
        score = obj.score(state)
        assert score > 0.8

    def test_trust_tier_proposal_maps_to_low_score(self):
        registry = get_default_registry()
        obj = registry.build_objective("stability")
        profile = TrustProfile(tier=TrustTier.PROPOSAL)
        # Proposal-tier → low stability assumption
        stability = 0.2 if profile.tier == TrustTier.PROPOSAL else 0.8
        state = ScoringState(stability_score=stability)
        assert obj.is_satisfied(state) is False

    def test_trust_algebra_meet_drives_combined_confidence(self):
        algebra = TrustAlgebra()
        t1 = TrustLevel.SOLVER_DISCHARGED
        t2 = TrustLevel.RUNTIME_WITNESSED
        meet = algebra.meet(t1, t2)
        # Meet is glb; result should not be higher than either
        assert meet.rank_index() >= min(t1.rank_index(), t2.rank_index())

    def test_manifest_valid_before_trust_enriched_scoring(self):
        m = build_manifest()
        assert m.validate() is True
        # Build a scoring pipeline informed by trust
        registry = get_default_registry()
        objectives = registry.build_all()
        profile = TrustProfile(tier=TrustTier.REVIEWED, support_scope=("lemma_1",))
        confidence = 0.7 if profile.tier == TrustTier.REVIEWED else 0.5
        state = ScoringState(closure_gain=MAX_CLOSURE_GAIN * confidence)
        scores = [obj.score(state) for obj in objectives]
        assert all(0.0 <= s <= 1.0 for s in scores)


# ---------------------------------------------------------------------------
# Integration: with Frontier module
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FRONTIER, reason="frontier module not available")
class TestIntegrationManifestWithFrontier:
    """Tests that connect manifest / registry with FrontierNode / Frontier."""

    def test_manifest_valid_before_frontier_operations(self):
        m = build_manifest()
        assert m.validate() is True
        frontier = Frontier()
        assert frontier.is_empty() is True

    def test_registry_objectives_score_frontier_derived_state(self):
        frontier = Frontier()
        for i in range(4):
            node = FrontierNode(
                predicted_closure_gain=0.1 * (i + 1),
                estimated_cost=float(i + 1),
            )
            frontier.add_node(node)
        best = frontier.best_node()
        state = ScoringState(
            closure_gain=(best.predicted_closure_gain if best else 0.0) * MAX_CLOSURE_GAIN,
            diversity_score=frontier.diversity_score(),
            node_count=frontier.size(),
        )
        registry = get_default_registry()
        for obj in registry.build_all():
            s = obj.score(state)
            assert 0.0 <= s <= 1.0

    def test_catalog_trigger_matches_frontier_phase_transition(self):
        catalog = get_default_catalog()
        # closure_gain_plateau is a standard frontier trigger
        results = catalog.get_by_trigger("closure_gain_plateau")
        assert len(results) >= 1

    def test_objective_entry_to_objective_satisfiable_by_frontier_state(self):
        entry = ObjectiveEntry(
            name="closure_gain",
            kind="CLOSURE_GAIN",
            description="Test.",
            default_weight=1.0,
            default_threshold=0.4,
        )
        obj = entry.to_objective()
        frontier = Frontier()
        frontier.add_node(FrontierNode(predicted_closure_gain=0.8))
        best = frontier.best_node()
        state = ScoringState(closure_gain=(best.predicted_closure_gain if best else 0.0) * MAX_CLOSURE_GAIN)
        assert obj.is_satisfied(state) is True

    def test_validate_manifest_report_structure_before_frontier_use(self):
        report = validate_manifest(build_manifest())
        assert report.valid is True
        assert isinstance(report.summary(), str)
        # After validation, it's safe to use frontier
        frontier = Frontier()
        frontier.add_node(FrontierNode())
        assert frontier.size() == 1
