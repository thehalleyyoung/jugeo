"""Comprehensive tests for jugeo.orchestration.semantic_control.manifest.

Covers all public classes and factory functions:
  - SemanticControlManifest: construction, add_move_type, add_law_type,
    validate, is_valid, summary, to_dict
  - MoveRegistry: construction, register, lookup, instantiate, list_types,
    validate_spec, merge, to_dict
  - ControlLawCatalog: construction, register, get, instantiate, set_default,
    get_default, list_laws, to_dict
  - Factory functions: build_manifest, validate_manifest,
    build_default_registry, build_default_catalog
  - Integration tests with upstream modules (skipped when unavailable)

All upstream imports are wrapped in try/except; tests that depend on them are
decorated with ``pytest.mark.skipif`` to keep the suite green in minimal
environments.
"""

from __future__ import annotations

import uuid
from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Canonical sys.path bootstrap
# ---------------------------------------------------------------------------

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Primary imports under test
# ---------------------------------------------------------------------------

import pytest

from jugeo.orchestration.semantic_control.manifest import (
    DEFAULT_CHAPTER_REF,
    LAW_SPEC_REQUIRED_KEYS,
    MANIFEST_MIN_LAW_TYPES,
    MANIFEST_MIN_MOVE_TYPES,
    MANIFEST_VERSION,
    MOVE_SPEC_REQUIRED_KEYS,
    STANDARD_LAW_TYPE_IDS,
    STANDARD_MOVE_TYPE_IDS,
    ControlLawCatalog,
    MoveRegistry,
    SemanticControlManifest,
    build_default_catalog,
    build_default_registry,
    build_manifest,
    validate_manifest,
    _default_move_types,
    _default_law_types,
    _default_invariants,
)
from jugeo.orchestration.semantic_control.models import (
    AdmissibleMove,
    ControlLaw,
    ControlLawKind,
    MoveKind,
    SemanticControlState,
)

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import (
        MoveKind as ControllerMoveKind,
        GreedyControl,
    )
    _HAS_CONTROLLER = True
except Exception:
    _HAS_CONTROLLER = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, CompetitiveSearch
    _HAS_FLEET = True
except Exception:
    _HAS_FLEET = False


# ===========================================================================
# Helpers
# ===========================================================================


def _make_move_spec(
    move_type_id: str = "test.move",
    kind: str = "extend_cover",
    *,
    description: str = "A test move type.",
    default_cost: float = 1.0,
    default_priority: float = 0.5,
    **extra,
) -> dict:
    """Return a minimal valid move type specification dict."""
    spec: dict = {
        "move_type_id": move_type_id,
        "kind": kind,
        "description": description,
        "default_cost": default_cost,
        "default_priority": default_priority,
    }
    spec.update(extra)
    return spec


def _make_law_spec(
    law_type_id: str = "test.law",
    kind: str = "greedy",
    *,
    description: str = "A test law type.",
    default_parameters: dict | None = None,
    **extra,
) -> dict:
    """Return a minimal valid law type specification dict."""
    spec: dict = {
        "law_type_id": law_type_id,
        "kind": kind,
        "description": description,
        "default_parameters": default_parameters or {},
    }
    spec.update(extra)
    return spec


def _make_valid_manifest(n_moves: int = 4, n_laws: int = 2) -> SemanticControlManifest:
    """Return a SemanticControlManifest with the minimum valid content."""
    m = SemanticControlManifest()
    for i in range(n_moves):
        m.add_move_type(_make_move_spec(move_type_id=f"test.move.{i}"))
    for i in range(n_laws):
        m.add_law_type(_make_law_spec(law_type_id=f"test.law.{i}"))
    m.invariants.append("INV-TEST-1: coverage is monotone non-decreasing")
    return m


def _make_valid_registry(n: int = 3) -> MoveRegistry:
    """Return a MoveRegistry with *n* registered entries."""
    reg = MoveRegistry()
    for i in range(n):
        reg.register(
            f"test.move.{i}",
            {
                "kind": "extend_cover",
                "description": f"Test move {i}",
                "default_cost": float(i + 1),
                "default_priority": 0.5,
                "default_expected_gain": float(i + 2),
            },
        )
    return reg


def _make_valid_catalog(n: int = 3) -> ControlLawCatalog:
    """Return a ControlLawCatalog with *n* registered entries."""
    cat = ControlLawCatalog()
    kinds = list(ControlLawKind)
    for i in range(n):
        kind = kinds[i % len(kinds)]
        cat.register(
            f"test.law.{i}",
            {
                "kind": kind.value,
                "description": f"Test law {i}",
                "default_parameters": {"alpha": 0.5},
            },
        )
    return cat


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def basic_manifest() -> SemanticControlManifest:
    """A SemanticControlManifest with the minimum valid structure."""
    return _make_valid_manifest()


@pytest.fixture
def full_manifest() -> SemanticControlManifest:
    """The default manifest produced by build_manifest()."""
    return build_manifest()


@pytest.fixture
def basic_registry() -> MoveRegistry:
    """A MoveRegistry with three registered test entries."""
    return _make_valid_registry(3)


@pytest.fixture
def full_registry() -> MoveRegistry:
    """The default registry produced by build_default_registry()."""
    return build_default_registry()


@pytest.fixture
def basic_catalog() -> ControlLawCatalog:
    """A ControlLawCatalog with three registered test entries."""
    return _make_valid_catalog(3)


@pytest.fixture
def full_catalog() -> ControlLawCatalog:
    """The default catalog produced by build_default_catalog()."""
    return build_default_catalog()


# ===========================================================================
# Section 1 — Module-level constants
# ===========================================================================


class TestModuleConstants:
    def test_manifest_version_is_string(self) -> None:
        assert isinstance(MANIFEST_VERSION, str)
        assert MANIFEST_VERSION  # non-empty

    def test_default_chapter_ref_is_ch44(self) -> None:
        assert DEFAULT_CHAPTER_REF == "Ch44"

    def test_min_move_types_is_positive(self) -> None:
        assert MANIFEST_MIN_MOVE_TYPES > 0

    def test_min_law_types_is_positive(self) -> None:
        assert MANIFEST_MIN_LAW_TYPES > 0

    def test_move_spec_required_keys_is_frozenset(self) -> None:
        assert isinstance(MOVE_SPEC_REQUIRED_KEYS, frozenset)

    def test_law_spec_required_keys_is_frozenset(self) -> None:
        assert isinstance(LAW_SPEC_REQUIRED_KEYS, frozenset)

    def test_standard_move_type_ids_is_nonempty_tuple(self) -> None:
        assert isinstance(STANDARD_MOVE_TYPE_IDS, tuple)
        assert len(STANDARD_MOVE_TYPE_IDS) >= MANIFEST_MIN_MOVE_TYPES

    def test_standard_law_type_ids_is_nonempty_tuple(self) -> None:
        assert isinstance(STANDARD_LAW_TYPE_IDS, tuple)
        assert len(STANDARD_LAW_TYPE_IDS) >= MANIFEST_MIN_LAW_TYPES

    def test_move_spec_required_keys_content(self) -> None:
        for key in ("move_type_id", "kind", "description", "default_cost", "default_priority"):
            assert key in MOVE_SPEC_REQUIRED_KEYS

    def test_law_spec_required_keys_content(self) -> None:
        for key in ("law_type_id", "kind", "description", "default_parameters"):
            assert key in LAW_SPEC_REQUIRED_KEYS


# ===========================================================================
# Section 2 — SemanticControlManifest construction
# ===========================================================================


class TestSemanticControlManifestConstruction:
    def test_default_construction(self) -> None:
        m = SemanticControlManifest()
        assert isinstance(m.manifest_id, str)
        assert m.manifest_id  # non-empty
        assert m.version == MANIFEST_VERSION
        assert m.chapter_ref == DEFAULT_CHAPTER_REF
        assert m.move_types == []
        assert m.law_types == []
        assert m.invariants == []
        assert isinstance(m.metadata, dict)
        assert m.description  # non-empty default

    def test_default_manifest_id_is_uuid(self) -> None:
        m = SemanticControlManifest()
        parsed = uuid.UUID(m.manifest_id)
        assert str(parsed) == m.manifest_id

    def test_two_defaults_have_distinct_ids(self) -> None:
        m1, m2 = SemanticControlManifest(), SemanticControlManifest()
        assert m1.manifest_id != m2.manifest_id

    def test_custom_chapter_ref(self) -> None:
        m = SemanticControlManifest(chapter_ref="Ch99")
        assert m.chapter_ref == "Ch99"

    def test_custom_description(self) -> None:
        m = SemanticControlManifest(description="My custom manifest")
        assert m.description == "My custom manifest"

    def test_created_at_is_positive_float(self) -> None:
        import time
        before = time.time()
        m = SemanticControlManifest()
        after = time.time()
        assert before <= m.created_at <= after


# ===========================================================================
# Section 3 — SemanticControlManifest.add_move_type
# ===========================================================================


class TestManifestAddMoveType:
    def test_add_valid_spec(self, basic_manifest: SemanticControlManifest) -> None:
        initial_count = len(basic_manifest.move_types)
        basic_manifest.add_move_type(_make_move_spec("extra.move"))
        assert len(basic_manifest.move_types) == initial_count + 1

    def test_added_spec_appears_in_move_types(self) -> None:
        m = SemanticControlManifest()
        spec = _make_move_spec("my.move")
        m.add_move_type(spec)
        ids = [mt["move_type_id"] for mt in m.move_types]
        assert "my.move" in ids

    def test_duplicate_id_raises_value_error(self) -> None:
        m = SemanticControlManifest()
        m.add_move_type(_make_move_spec("dup.move"))
        with pytest.raises(ValueError, match="already registered"):
            m.add_move_type(_make_move_spec("dup.move"))

    def test_missing_required_key_raises_value_error(self) -> None:
        m = SemanticControlManifest()
        bad_spec = {"move_type_id": "bad.move"}  # missing kind, description, etc.
        with pytest.raises(ValueError, match="missing required keys"):
            m.add_move_type(bad_spec)

    def test_updates_updated_at_timestamp(self) -> None:
        import time
        m = SemanticControlManifest()
        before = m.updated_at
        time.sleep(0.01)
        m.add_move_type(_make_move_spec("ts.move"))
        assert m.updated_at >= before

    def test_spec_is_stored_as_copy(self) -> None:
        m = SemanticControlManifest()
        spec = _make_move_spec("copy.move")
        m.add_move_type(spec)
        spec["injected"] = "evil"
        assert "injected" not in m.move_types[0]


# ===========================================================================
# Section 4 — SemanticControlManifest.add_law_type
# ===========================================================================


class TestManifestAddLawType:
    def test_add_valid_law_spec(self) -> None:
        m = SemanticControlManifest()
        m.add_law_type(_make_law_spec("my.law"))
        assert len(m.law_types) == 1

    def test_added_spec_has_correct_id(self) -> None:
        m = SemanticControlManifest()
        m.add_law_type(_make_law_spec("check.law"))
        assert m.law_types[0]["law_type_id"] == "check.law"

    def test_duplicate_law_id_raises_value_error(self) -> None:
        m = SemanticControlManifest()
        m.add_law_type(_make_law_spec("dup.law"))
        with pytest.raises(ValueError, match="already registered"):
            m.add_law_type(_make_law_spec("dup.law"))

    def test_missing_required_key_raises_value_error(self) -> None:
        m = SemanticControlManifest()
        bad_spec = {"law_type_id": "bad.law"}  # missing kind etc.
        with pytest.raises(ValueError, match="missing required keys"):
            m.add_law_type(bad_spec)

    def test_law_spec_is_stored_as_copy(self) -> None:
        m = SemanticControlManifest()
        spec = _make_law_spec("clone.law")
        m.add_law_type(spec)
        spec["injected"] = "evil"
        assert "injected" not in m.law_types[0]


# ===========================================================================
# Section 5 — SemanticControlManifest.validate
# ===========================================================================


class TestManifestValidate:
    def test_valid_manifest_returns_empty_list(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert basic_manifest.validate() == []

    def test_too_few_move_types_gives_error(self) -> None:
        m = SemanticControlManifest()
        # Add only 1 law type and 1 invariant, but 0 move types
        m.add_law_type(_make_law_spec("l1"))
        m.add_law_type(_make_law_spec("l2"))
        m.invariants.append("INV-1")
        errors = m.validate()
        assert any("move type" in e.lower() for e in errors)

    def test_too_few_law_types_gives_error(self) -> None:
        m = SemanticControlManifest()
        for i in range(MANIFEST_MIN_MOVE_TYPES):
            m.add_move_type(_make_move_spec(f"m{i}"))
        m.invariants.append("INV-1")
        errors = m.validate()
        assert any("law type" in e.lower() for e in errors)

    def test_missing_invariants_gives_error(self) -> None:
        m = SemanticControlManifest()
        for i in range(MANIFEST_MIN_MOVE_TYPES):
            m.add_move_type(_make_move_spec(f"m{i}"))
        for i in range(MANIFEST_MIN_LAW_TYPES):
            m.add_law_type(_make_law_spec(f"l{i}"))
        # No invariants added
        errors = m.validate()
        assert any("invariant" in e.lower() for e in errors)

    def test_validate_returns_list(self) -> None:
        m = SemanticControlManifest()
        assert isinstance(m.validate(), list)

    def test_duplicate_move_type_id_raises_before_validate(self) -> None:
        """add_move_type raises for duplicates; they can't be created via the public API."""
        m = _make_valid_manifest()
        with pytest.raises(ValueError):
            m.add_move_type(_make_move_spec("test.move.0"))  # duplicate id


# ===========================================================================
# Section 6 — SemanticControlManifest.is_valid, summary, to_dict
# ===========================================================================


class TestManifestIsValidSummaryToDice:
    def test_is_valid_for_valid_manifest(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert basic_manifest.is_valid() is True

    def test_is_valid_false_for_empty_manifest(self) -> None:
        m = SemanticControlManifest()
        assert m.is_valid() is False

    def test_is_valid_returns_bool(self) -> None:
        assert isinstance(SemanticControlManifest().is_valid(), bool)

    def test_summary_returns_string(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        s = basic_manifest.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_version(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert basic_manifest.version in basic_manifest.summary()

    def test_summary_contains_move_count(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert str(len(basic_manifest.move_types)) in basic_manifest.summary()

    def test_to_dict_returns_dict(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert isinstance(basic_manifest.to_dict(), dict)

    def test_to_dict_contains_required_keys(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        d = basic_manifest.to_dict()
        for key in (
            "manifest_id", "version", "chapter_ref", "description",
            "move_types", "law_types", "invariants", "metadata", "is_valid",
        ):
            assert key in d, f"Missing key in to_dict: {key}"

    def test_to_dict_move_types_is_list(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert isinstance(basic_manifest.to_dict()["move_types"], list)

    def test_to_dict_is_valid_reflects_state(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        d = basic_manifest.to_dict()
        assert d["is_valid"] == basic_manifest.is_valid()

    def test_repr_is_string(
        self, basic_manifest: SemanticControlManifest
    ) -> None:
        assert isinstance(repr(basic_manifest), str)


# ===========================================================================
# Section 7 — MoveRegistry construction
# ===========================================================================


class TestMoveRegistryConstruction:
    def test_default_construction(self) -> None:
        reg = MoveRegistry()
        assert reg.entries == {}
        assert isinstance(reg.version, str)

    def test_custom_entries_at_construction(self) -> None:
        spec = {
            "move_type_id": "init.move",
            "kind": "extend_cover",
            "description": "Initialised move",
            "default_cost": 1.0,
            "default_priority": 0.5,
        }
        reg = MoveRegistry(entries={"init.move": spec})
        assert "init.move" in reg.entries


# ===========================================================================
# Section 8 — MoveRegistry.register
# ===========================================================================


class TestMoveRegistryRegister:
    def test_register_valid_spec(self, basic_registry: MoveRegistry) -> None:
        initial = len(basic_registry.entries)
        basic_registry.register(
            "new.move",
            {
                "kind": "extend_cover",
                "description": "New move",
                "default_cost": 2.0,
                "default_priority": 0.5,
            },
        )
        assert len(basic_registry.entries) == initial + 1

    def test_register_missing_kind_raises(self) -> None:
        reg = MoveRegistry()
        with pytest.raises(ValueError, match="Invalid move spec"):
            reg.register("bad", {"description": "x", "default_cost": 1.0, "default_priority": 0.5})

    def test_register_missing_description_raises(self) -> None:
        reg = MoveRegistry()
        with pytest.raises(ValueError, match="Invalid move spec"):
            reg.register("bad", {"kind": "extend_cover", "default_cost": 1.0, "default_priority": 0.5})

    def test_register_missing_default_cost_raises(self) -> None:
        reg = MoveRegistry()
        with pytest.raises(ValueError, match="Invalid move spec"):
            reg.register("bad", {"kind": "extend_cover", "description": "x", "default_priority": 0.5})

    def test_register_missing_default_priority_raises(self) -> None:
        reg = MoveRegistry()
        with pytest.raises(ValueError, match="Invalid move spec"):
            reg.register("bad", {"kind": "extend_cover", "description": "x", "default_cost": 1.0})

    def test_register_injects_move_type_id(self) -> None:
        reg = MoveRegistry()
        reg.register(
            "injected.move",
            {
                "kind": "extend_cover",
                "description": "x",
                "default_cost": 1.0,
                "default_priority": 0.5,
            },
        )
        stored = reg.entries["injected.move"]
        assert stored["move_type_id"] == "injected.move"

    def test_register_overwrites_existing_entry(self) -> None:
        reg = MoveRegistry()
        spec1 = {
            "kind": "extend_cover",
            "description": "v1",
            "default_cost": 1.0,
            "default_priority": 0.5,
        }
        spec2 = {
            "kind": "lift_section",
            "description": "v2",
            "default_cost": 2.0,
            "default_priority": 0.5,
        }
        reg.register("over.move", spec1)
        reg.register("over.move", spec2)  # overwrite
        assert reg.entries["over.move"]["kind"] == "lift_section"

    def test_priority_out_of_range_raises(self) -> None:
        reg = MoveRegistry()
        with pytest.raises(ValueError):
            reg.register(
                "bad.priority",
                {
                    "kind": "extend_cover",
                    "description": "x",
                    "default_cost": 1.0,
                    "default_priority": 1.5,  # > 1.0
                },
            )


# ===========================================================================
# Section 9 — MoveRegistry.lookup
# ===========================================================================


class TestMoveRegistryLookup:
    def test_lookup_existing_entry(self, basic_registry: MoveRegistry) -> None:
        spec = basic_registry.lookup("test.move.0")
        assert spec is not None
        assert spec["move_type_id"] == "test.move.0"

    def test_lookup_missing_entry_returns_none(self, basic_registry: MoveRegistry) -> None:
        assert basic_registry.lookup("nonexistent.move") is None

    def test_lookup_returns_deep_copy(self, basic_registry: MoveRegistry) -> None:
        spec = basic_registry.lookup("test.move.0")
        spec["injected"] = True
        # Original entry should be unaffected
        fresh = basic_registry.lookup("test.move.0")
        assert "injected" not in fresh


# ===========================================================================
# Section 10 — MoveRegistry.instantiate
# ===========================================================================


class TestMoveRegistryInstantiate:
    def test_instantiate_returns_admissible_move(
        self, basic_registry: MoveRegistry
    ) -> None:
        move = basic_registry.instantiate("test.move.0")
        assert isinstance(move, AdmissibleMove)

    def test_instantiate_missing_id_raises_key_error(
        self, basic_registry: MoveRegistry
    ) -> None:
        with pytest.raises(KeyError):
            basic_registry.instantiate("ghost.move")

    def test_instantiate_overrides_cost(self, basic_registry: MoveRegistry) -> None:
        move = basic_registry.instantiate("test.move.0", cost=99.9)
        assert move.cost == pytest.approx(99.9)

    def test_instantiate_gets_default_cost_from_spec(
        self, basic_registry: MoveRegistry
    ) -> None:
        # test.move.0 has default_cost=1.0 in _make_valid_registry
        move = basic_registry.instantiate("test.move.0")
        assert move.cost == pytest.approx(1.0)

    def test_instantiate_sets_move_type_id_in_metadata(
        self, basic_registry: MoveRegistry
    ) -> None:
        move = basic_registry.instantiate("test.move.0")
        assert move.metadata.get("move_type_id") == "test.move.0"

    def test_instantiate_from_full_registry(
        self, full_registry: MoveRegistry
    ) -> None:
        """All entries in the default registry should be instantiable."""
        for move_type_id in full_registry.list_types():
            move = full_registry.instantiate(move_type_id)
            assert isinstance(move, AdmissibleMove)
            assert move.validate() == []


# ===========================================================================
# Section 11 — MoveRegistry.list_types
# ===========================================================================


class TestMoveRegistryListTypes:
    def test_empty_registry_returns_empty_list(self) -> None:
        assert MoveRegistry().list_types() == []

    def test_returns_all_registered_ids(self, basic_registry: MoveRegistry) -> None:
        types = basic_registry.list_types()
        assert set(types) == {"test.move.0", "test.move.1", "test.move.2"}

    def test_returns_sorted_list(self) -> None:
        reg = MoveRegistry()
        for name in ["z.move", "a.move", "m.move"]:
            reg.register(
                name,
                {
                    "kind": "extend_cover",
                    "description": "x",
                    "default_cost": 1.0,
                    "default_priority": 0.5,
                },
            )
        types = reg.list_types()
        assert types == sorted(types)


# ===========================================================================
# Section 12 — MoveRegistry.validate_spec
# ===========================================================================


class TestMoveRegistryValidateSpec:
    def test_valid_spec_returns_empty_list(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {
                "kind": "extend_cover",
                "description": "x",
                "default_cost": 1.0,
                "default_priority": 0.5,
            }
        )
        assert errors == []

    def test_missing_kind_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {"description": "x", "default_cost": 1.0, "default_priority": 0.5}
        )
        assert any("kind" in e for e in errors)

    def test_missing_description_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {"kind": "extend_cover", "default_cost": 1.0, "default_priority": 0.5}
        )
        assert any("description" in e for e in errors)

    def test_missing_default_cost_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {"kind": "extend_cover", "description": "x", "default_priority": 0.5}
        )
        assert any("default_cost" in e for e in errors)

    def test_missing_default_priority_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {"kind": "extend_cover", "description": "x", "default_cost": 1.0}
        )
        assert any("default_priority" in e for e in errors)

    def test_non_numeric_cost_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {
                "kind": "extend_cover",
                "description": "x",
                "default_cost": "not-a-number",
                "default_priority": 0.5,
            }
        )
        assert any("default_cost" in e for e in errors)

    def test_priority_out_of_range_returns_error(self) -> None:
        reg = MoveRegistry()
        errors = reg.validate_spec(
            {
                "kind": "extend_cover",
                "description": "x",
                "default_cost": 1.0,
                "default_priority": 2.0,
            }
        )
        assert any("default_priority" in e for e in errors)

    def test_returns_list(self) -> None:
        assert isinstance(MoveRegistry().validate_spec({}), list)


# ===========================================================================
# Section 13 — MoveRegistry.to_dict and merge
# ===========================================================================


class TestMoveRegistryToDiceAndMerge:
    def test_to_dict_returns_dict(self, basic_registry: MoveRegistry) -> None:
        assert isinstance(basic_registry.to_dict(), dict)

    def test_to_dict_has_entries_key(self, basic_registry: MoveRegistry) -> None:
        d = basic_registry.to_dict()
        assert "entries" in d

    def test_to_dict_entries_count_matches(self, basic_registry: MoveRegistry) -> None:
        d = basic_registry.to_dict()
        assert len(d["entries"]) == len(basic_registry.entries)

    def test_merge_combines_entries(self) -> None:
        reg_a = _make_valid_registry(2)  # test.move.0, test.move.1
        reg_b = MoveRegistry()
        reg_b.register(
            "extra.move",
            {
                "kind": "extend_cover",
                "description": "extra",
                "default_cost": 1.0,
                "default_priority": 0.5,
            },
        )
        merged = reg_a.merge(reg_b)
        assert "test.move.0" in merged.entries
        assert "extra.move" in merged.entries

    def test_merge_does_not_mutate_originals(self) -> None:
        reg_a = _make_valid_registry(2)
        reg_b = _make_valid_registry(2)
        len_a_before = len(reg_a.entries)
        _ = reg_a.merge(reg_b)
        assert len(reg_a.entries) == len_a_before

    def test_merge_other_entries_overwrite_self(self) -> None:
        reg_a = MoveRegistry()
        reg_a.register(
            "shared.move",
            {
                "kind": "extend_cover",
                "description": "from-a",
                "default_cost": 1.0,
                "default_priority": 0.5,
            },
        )
        reg_b = MoveRegistry()
        reg_b.register(
            "shared.move",
            {
                "kind": "lift_section",
                "description": "from-b",
                "default_cost": 2.0,
                "default_priority": 0.5,
            },
        )
        merged = reg_a.merge(reg_b)
        assert merged.entries["shared.move"]["description"] == "from-b"


# ===========================================================================
# Section 14 — ControlLawCatalog construction
# ===========================================================================


class TestControlLawCatalogConstruction:
    def test_default_construction(self) -> None:
        cat = ControlLawCatalog()
        assert cat.entries == {}
        assert cat.default_law_id is None

    def test_custom_entries_at_construction(self) -> None:
        spec = {
            "law_type_id": "init.law",
            "kind": "greedy",
            "description": "init",
            "default_parameters": {},
        }
        cat = ControlLawCatalog(entries={"init.law": spec})
        assert "init.law" in cat.entries


# ===========================================================================
# Section 15 — ControlLawCatalog.register
# ===========================================================================


class TestControlLawCatalogRegister:
    def test_register_valid_spec(self, basic_catalog: ControlLawCatalog) -> None:
        initial = len(basic_catalog.entries)
        basic_catalog.register(
            "new.law",
            {"kind": "greedy", "description": "new", "default_parameters": {}},
        )
        assert len(basic_catalog.entries) == initial + 1

    def test_register_missing_kind_raises(self) -> None:
        cat = ControlLawCatalog()
        with pytest.raises(ValueError):
            cat.register("bad.law", {"description": "x", "default_parameters": {}})

    def test_register_missing_description_raises(self) -> None:
        cat = ControlLawCatalog()
        with pytest.raises(ValueError):
            cat.register("bad.law", {"kind": "greedy", "default_parameters": {}})

    def test_register_missing_default_parameters_raises(self) -> None:
        cat = ControlLawCatalog()
        with pytest.raises(ValueError):
            cat.register("bad.law", {"kind": "greedy", "description": "x"})

    def test_register_injects_law_type_id(self) -> None:
        cat = ControlLawCatalog()
        cat.register(
            "inj.law",
            {"kind": "greedy", "description": "x", "default_parameters": {}},
        )
        assert cat.entries["inj.law"]["law_type_id"] == "inj.law"

    def test_register_overwrites_existing_entry(self) -> None:
        cat = ControlLawCatalog()
        cat.register(
            "over.law",
            {"kind": "greedy", "description": "v1", "default_parameters": {}},
        )
        cat.register(
            "over.law",
            {"kind": "balanced", "description": "v2", "default_parameters": {"alpha": 0.6}},
        )
        assert cat.entries["over.law"]["kind"] == "balanced"


# ===========================================================================
# Section 16 — ControlLawCatalog.get
# ===========================================================================


class TestControlLawCatalogGet:
    def test_get_existing_returns_dict(self, basic_catalog: ControlLawCatalog) -> None:
        spec = basic_catalog.get("test.law.0")
        assert spec is not None
        assert isinstance(spec, dict)

    def test_get_missing_returns_none(self, basic_catalog: ControlLawCatalog) -> None:
        assert basic_catalog.get("ghost.law") is None

    def test_get_returns_deep_copy(self, basic_catalog: ControlLawCatalog) -> None:
        spec = basic_catalog.get("test.law.0")
        spec["injected"] = True
        fresh = basic_catalog.get("test.law.0")
        assert "injected" not in fresh


# ===========================================================================
# Section 17 — ControlLawCatalog.list_laws
# ===========================================================================


class TestControlLawCatalogListLaws:
    def test_empty_catalog_returns_empty_list(self) -> None:
        assert ControlLawCatalog().list_laws() == []

    def test_returns_all_registered_ids(self, basic_catalog: ControlLawCatalog) -> None:
        laws = basic_catalog.list_laws()
        for i in range(3):
            assert f"test.law.{i}" in laws


# ===========================================================================
# Section 18 — ControlLawCatalog.instantiate
# ===========================================================================


class TestControlLawCatalogInstantiate:
    def test_instantiate_returns_control_law(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        law = basic_catalog.instantiate("test.law.0")
        assert isinstance(law, ControlLaw)

    def test_instantiate_missing_id_raises_key_error(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        with pytest.raises(KeyError):
            basic_catalog.instantiate("ghost.law")

    def test_instantiate_kind_is_control_law_kind(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        law = basic_catalog.instantiate("test.law.0")
        assert isinstance(law.kind, ControlLawKind)

    def test_instantiate_parameters_are_from_spec(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        law = basic_catalog.instantiate("test.law.0")
        assert isinstance(law.parameters, dict)
        assert law.parameters.get("alpha") == pytest.approx(0.5)

    def test_instantiate_all_entries_in_full_catalog(
        self, full_catalog: ControlLawCatalog
    ) -> None:
        for law_id in full_catalog.list_laws():
            law = full_catalog.instantiate(law_id)
            assert isinstance(law, ControlLaw)


# ===========================================================================
# Section 19 — ControlLawCatalog.set_default / get_default
# ===========================================================================


class TestControlLawCatalogDefault:
    def test_default_is_none_initially(self) -> None:
        assert ControlLawCatalog().get_default() is None

    def test_set_default_valid_id(self, basic_catalog: ControlLawCatalog) -> None:
        basic_catalog.set_default("test.law.1")
        default = basic_catalog.get_default()
        assert isinstance(default, ControlLaw)

    def test_get_default_returns_law_from_catalog(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        basic_catalog.set_default("test.law.2")
        law = basic_catalog.get_default()
        assert isinstance(law, ControlLaw)

    def test_set_default_unknown_id_raises_key_error(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        with pytest.raises(KeyError):
            basic_catalog.set_default("nonexistent.law")

    def test_set_default_updates_default_law_id(
        self, basic_catalog: ControlLawCatalog
    ) -> None:
        basic_catalog.set_default("test.law.0")
        assert basic_catalog.default_law_id == "test.law.0"

    def test_full_catalog_default_is_greedy(
        self, full_catalog: ControlLawCatalog
    ) -> None:
        default = full_catalog.get_default()
        assert default is not None
        assert default.kind == ControlLawKind.GREEDY


# ===========================================================================
# Section 20 — ControlLawCatalog.to_dict
# ===========================================================================


class TestControlLawCatalogToDice:
    def test_returns_dict(self, basic_catalog: ControlLawCatalog) -> None:
        assert isinstance(basic_catalog.to_dict(), dict)

    def test_has_catalog_id_key(self, basic_catalog: ControlLawCatalog) -> None:
        assert "catalog_id" in basic_catalog.to_dict()

    def test_has_laws_key(self, basic_catalog: ControlLawCatalog) -> None:
        assert "laws" in basic_catalog.to_dict()

    def test_has_default_key(self, basic_catalog: ControlLawCatalog) -> None:
        assert "default" in basic_catalog.to_dict()

    def test_laws_count_matches(self, basic_catalog: ControlLawCatalog) -> None:
        d = basic_catalog.to_dict()
        assert len(d["laws"]) == len(basic_catalog.entries)


# ===========================================================================
# Section 21 — build_manifest
# ===========================================================================


class TestBuildManifest:
    def test_returns_semantic_control_manifest(self) -> None:
        m = build_manifest()
        assert isinstance(m, SemanticControlManifest)

    def test_chapter_ref_is_ch44(self) -> None:
        m = build_manifest()
        assert m.chapter_ref == "Ch44"

    def test_manifest_is_valid(self) -> None:
        m = build_manifest()
        assert m.is_valid(), f"build_manifest() returned invalid manifest: {m.validate()}"

    def test_has_minimum_move_types(self) -> None:
        m = build_manifest()
        assert len(m.move_types) >= MANIFEST_MIN_MOVE_TYPES

    def test_has_minimum_law_types(self) -> None:
        m = build_manifest()
        assert len(m.law_types) >= MANIFEST_MIN_LAW_TYPES

    def test_has_invariants(self) -> None:
        m = build_manifest()
        assert len(m.invariants) > 0

    def test_version_is_manifest_version(self) -> None:
        m = build_manifest()
        assert m.version == MANIFEST_VERSION

    def test_move_types_have_required_keys(self) -> None:
        m = build_manifest()
        for spec in m.move_types:
            for key in MOVE_SPEC_REQUIRED_KEYS:
                assert key in spec, (
                    f"move type spec missing key '{key}': {spec}"
                )

    def test_law_types_have_required_keys(self) -> None:
        m = build_manifest()
        for spec in m.law_types:
            for key in LAW_SPEC_REQUIRED_KEYS:
                assert key in spec, (
                    f"law type spec missing key '{key}': {spec}"
                )

    def test_standard_move_type_ids_present(self) -> None:
        m = build_manifest()
        registered_ids = {mt["move_type_id"] for mt in m.move_types}
        for std_id in STANDARD_MOVE_TYPE_IDS:
            assert std_id in registered_ids, f"Missing standard move type ID: {std_id}"

    def test_standard_law_type_ids_present(self) -> None:
        m = build_manifest()
        registered_ids = {lt["law_type_id"] for lt in m.law_types}
        for std_id in STANDARD_LAW_TYPE_IDS:
            assert std_id in registered_ids, f"Missing standard law type ID: {std_id}"

    def test_two_calls_produce_different_ids(self) -> None:
        m1, m2 = build_manifest(), build_manifest()
        assert m1.manifest_id != m2.manifest_id

    def test_description_is_nonempty(self) -> None:
        m = build_manifest()
        assert m.description.strip()


# ===========================================================================
# Section 22 — validate_manifest
# ===========================================================================


class TestValidateManifest:
    def test_valid_manifest_returns_true_empty_errors(
        self, full_manifest: SemanticControlManifest
    ) -> None:
        ok, errors = validate_manifest(full_manifest)
        assert ok is True
        assert errors == []

    def test_invalid_manifest_returns_false_nonempty_errors(self) -> None:
        m = SemanticControlManifest()  # empty → not valid
        ok, errors = validate_manifest(m)
        assert ok is False
        assert len(errors) > 0

    def test_returns_tuple(
        self, full_manifest: SemanticControlManifest
    ) -> None:
        result = validate_manifest(full_manifest)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_errors_are_strings(self) -> None:
        ok, errors = validate_manifest(SemanticControlManifest())
        assert all(isinstance(e, str) for e in errors)


# ===========================================================================
# Section 23 — build_default_registry
# ===========================================================================


class TestBuildDefaultRegistry:
    def test_returns_move_registry(self) -> None:
        assert isinstance(build_default_registry(), MoveRegistry)

    def test_has_entries(self) -> None:
        reg = build_default_registry()
        assert len(reg.entries) > 0

    def test_number_of_entries_matches_standard_ids(self) -> None:
        reg = build_default_registry()
        assert len(reg.entries) >= len(STANDARD_MOVE_TYPE_IDS)

    def test_standard_move_type_ids_registered(self) -> None:
        reg = build_default_registry()
        for std_id in STANDARD_MOVE_TYPE_IDS:
            assert std_id in reg.entries, f"Missing standard type: {std_id}"

    def test_all_entries_are_instantiable(self) -> None:
        reg = build_default_registry()
        for move_type_id in reg.list_types():
            move = reg.instantiate(move_type_id)
            assert isinstance(move, AdmissibleMove)
            assert move.validate() == []

    def test_all_entries_pass_validate_spec(self) -> None:
        reg = build_default_registry()
        for move_type_id, spec in reg.entries.items():
            errors = reg.validate_spec(spec)
            assert errors == [], (
                f"validate_spec failed for '{move_type_id}': {errors}"
            )

    def test_two_calls_return_independent_registries(self) -> None:
        r1 = build_default_registry()
        r2 = build_default_registry()
        r1.register(
            "extra.move",
            {
                "kind": "extend_cover",
                "description": "x",
                "default_cost": 1.0,
                "default_priority": 0.5,
            },
        )
        assert "extra.move" not in r2.entries


# ===========================================================================
# Section 24 — build_default_catalog
# ===========================================================================


class TestBuildDefaultCatalog:
    def test_returns_control_law_catalog(self) -> None:
        assert isinstance(build_default_catalog(), ControlLawCatalog)

    def test_has_entries(self) -> None:
        cat = build_default_catalog()
        assert len(cat.entries) > 0

    def test_number_of_entries_matches_standard_ids(self) -> None:
        cat = build_default_catalog()
        assert len(cat.entries) >= len(STANDARD_LAW_TYPE_IDS)

    def test_standard_law_type_ids_registered(self) -> None:
        cat = build_default_catalog()
        for std_id in STANDARD_LAW_TYPE_IDS:
            assert std_id in cat.entries, f"Missing standard law: {std_id}"

    def test_has_default_law(self) -> None:
        cat = build_default_catalog()
        assert cat.get_default() is not None

    def test_default_law_is_greedy(self) -> None:
        cat = build_default_catalog()
        default = cat.get_default()
        assert default.kind == ControlLawKind.GREEDY

    def test_all_entries_instantiable(self) -> None:
        cat = build_default_catalog()
        for law_id in cat.list_laws():
            law = cat.instantiate(law_id)
            assert isinstance(law, ControlLaw)

    def test_two_calls_return_independent_catalogs(self) -> None:
        c1 = build_default_catalog()
        c2 = build_default_catalog()
        c1.register(
            "extra.law",
            {"kind": "greedy", "description": "x", "default_parameters": {}},
        )
        assert "extra.law" not in c2.entries


# ===========================================================================
# Section 25 — Private helper functions
# ===========================================================================


class TestPrivateHelpers:
    def test_default_move_types_returns_list(self) -> None:
        result = _default_move_types()
        assert isinstance(result, list)
        assert len(result) >= MANIFEST_MIN_MOVE_TYPES

    def test_default_move_types_have_required_keys(self) -> None:
        for spec in _default_move_types():
            for key in MOVE_SPEC_REQUIRED_KEYS:
                assert key in spec

    def test_default_law_types_returns_list(self) -> None:
        result = _default_law_types()
        assert isinstance(result, list)
        assert len(result) >= MANIFEST_MIN_LAW_TYPES

    def test_default_law_types_have_required_keys(self) -> None:
        for spec in _default_law_types():
            for key in LAW_SPEC_REQUIRED_KEYS:
                assert key in spec

    def test_default_invariants_returns_nonempty_list(self) -> None:
        result = _default_invariants()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_default_invariants_are_strings(self) -> None:
        for inv in _default_invariants():
            assert isinstance(inv, str) and inv.strip()


# ===========================================================================
# Section 26 — Integration tests
# ===========================================================================


@pytest.mark.skipif(
    not _HAS_CONTROLLER,
    reason="jugeo.orchestration.controller unavailable",
)
class TestIntegrationWithController:
    def test_registry_instantiate_with_controller_move_kind(self) -> None:
        """Instantiated moves have a valid ControllerMoveKind as their kind."""
        reg = build_default_registry()
        for move_type_id in reg.list_types():
            move = reg.instantiate(move_type_id)
            # Kind should be a MoveKind (models stub or real controller kind)
            assert hasattr(move.kind, "value"), (
                f"move.kind for '{move_type_id}' should have .value"
            )

    def test_catalog_greedy_law_selects_from_candidates(self) -> None:
        cat = build_default_catalog()
        law = cat.get_default()
        assert law is not None
        state = SemanticControlState()
        candidates = [
            AdmissibleMove(
                move_id=f"c{i}",
                kind=ControllerMoveKind.VERIFY,
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=float(i + 1),
            )
            for i in range(4)
        ]
        selected = law.select_move(state, candidates)
        assert isinstance(selected, AdmissibleMove)

    def test_all_controller_move_kinds_appear_in_default_registry(self) -> None:
        """Every ControllerMoveKind value string should be a key in the registry."""
        reg = build_default_registry()
        registered_kinds = {
            spec["kind"] for spec in reg.entries.values()
        }
        for kind in ControllerMoveKind:
            assert kind.value in registered_kinds, (
                f"ControllerMoveKind.{kind.name} ({kind.value!r}) not in registry kinds"
            )


class TestIntegrationFullWorkflow:
    """End-to-end: build_manifest → validate → get registry → instantiate move → apply."""

    def test_manifest_to_registry_to_move_workflow(self) -> None:
        manifest = build_manifest()
        ok, errors = validate_manifest(manifest)
        assert ok, f"Manifest invalid: {errors}"

        registry = build_default_registry()
        assert len(registry.list_types()) > 0

        first_id = registry.list_types()[0]
        move = registry.instantiate(first_id)
        assert isinstance(move, AdmissibleMove)
        assert move.validate() == []

    def test_catalog_to_law_to_select_workflow(self) -> None:
        catalog = build_default_catalog()
        default_law = catalog.get_default()
        assert default_law is not None

        registry = build_default_registry()
        state = SemanticControlState(
            cover_ids=["c1"],
            section_ids=["s1"],
            obligation_ids=[],
        )
        candidates = [
            registry.instantiate(mid) for mid in registry.list_types()
        ]
        result = default_law.select_move(state, candidates)
        assert isinstance(result, AdmissibleMove)

    def test_manifest_registry_catalog_all_consistent(self) -> None:
        """The move kinds in the registry must match the move types in the manifest."""
        manifest = build_manifest()
        registry = build_default_registry()

        manifest_kinds = {mt["kind"] for mt in manifest.move_types}
        registry_kinds = {spec["kind"] for spec in registry.entries.values()}

        # All registry kinds must be covered by the manifest
        for kind in registry_kinds:
            assert kind in manifest_kinds, (
                f"Registry kind '{kind}' not in manifest move_types"
            )

    def test_manifest_law_kinds_covered_by_catalog(self) -> None:
        """The law kinds in the catalog must match those in the manifest."""
        manifest = build_manifest()
        catalog = build_default_catalog()

        manifest_law_kinds = {lt["kind"] for lt in manifest.law_types}
        catalog_law_kinds = {
            spec["kind"] for spec in catalog.entries.values()
        }

        for kind in catalog_law_kinds:
            assert kind in manifest_law_kinds, (
                f"Catalog law kind '{kind}' not in manifest law_types"
            )

    def test_instantiated_move_can_be_applied_to_state(self) -> None:
        registry = build_default_registry()
        # Find the "extend_cover" move type (should be present in default)
        extend_cover_ids = [
            mid for mid, spec in registry.entries.items()
            if spec.get("kind") == "extend_cover"
        ]
        if not extend_cover_ids:
            pytest.skip("No 'extend_cover' move type in default registry")

        move = registry.instantiate(extend_cover_ids[0], postconditions=["new-cover"])
        state = SemanticControlState()
        successor = move.apply(state)
        assert "new-cover" in successor.cover_ids

    def test_full_convergence_episode_using_manifest_primitives(self) -> None:
        """Simulate a short convergence episode using only manifest primitives."""
        catalog = build_default_catalog()
        registry = build_default_registry()
        law = catalog.get_default()
        assert law is not None

        state = SemanticControlState(
            cover_ids=[f"c{i}" for i in range(5)],
            section_ids=[],
            obligation_ids=["o1", "o2"],
        )

        # Discharge obligations using custom moves
        discharge_moves = [
            AdmissibleMove(
                move_id=f"dis-{obl}",
                kind=MoveKind.DISCHARGE_OBLIGATION,
                preconditions=[obl],
                postconditions=[obl],
                cost=1.0,
                priority=0.5,
                expected_gain=3.0,
            )
            for obl in state.obligation_ids
        ]
        # Lift sections
        lift_moves = [
            AdmissibleMove(
                move_id=f"lift-{i}",
                kind=MoveKind.LIFT_SECTION,
                preconditions=[],
                postconditions=[f"s{i}"],
                cost=0.5,
                priority=0.5,
                expected_gain=2.0,
            )
            for i in range(5)
        ]
        candidates = discharge_moves + lift_moves

        current = state
        steps = 0
        while steps < 20:
            move = law.select_move(current, candidates)
            if move is None:
                break
            try:
                current = move.apply(current)
                candidates = [c for c in candidates if c.move_id != move.move_id]
                steps += 1
            except ValueError:
                break

        # Score should have improved
        assert current.attainability_score() >= state.attainability_score()
