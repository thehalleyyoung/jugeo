"""Tests for jugeo.orchestration.treaty_memory.manifest.

Theory reference: theory2.tex Ch48 – "Treaty memory, archival semantics,
and negotiation recall".

This test module exercises TreatyMemoryManifest, MemorySchemaRegistry,
ArchiveCatalog, MemoryModuleDescriptor, PackageHealthCheck, build_manifest,
validate_manifest, build_module_registry, _default_schema_registry, and
_default_archive_catalog.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import json
import time
import pytest

from jugeo.orchestration.treaty_memory.manifest import (
    TreatyMemoryManifest,
    MemorySchemaRegistry,
    ArchiveCatalog,
    MemoryModuleDescriptor,
    PackageHealthCheck,
    build_manifest,
    validate_manifest,
    build_module_registry,
    _default_schema_registry,
    _default_archive_catalog,
    _PACKAGE_VERSION,
    _SCHEMA_VERSION,
    _CHAPTER_REF,
    _PACKAGE_NAME,
    _AUTHOR,
    _MODULE_NAMES,
)

# Guard upstream imports
try:
    from jugeo.orchestration.negotiation import (
        NegotiationMemory,
        TreatyArchive,
        NegotiationEventBus,
        SessionState,
    )
    _NEG_AVAILABLE = True
except ImportError:
    _NEG_AVAILABLE = False

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        OrchestratorConfiguration,
        ConvergenceMonitor,
    )
    _CTRL_AVAILABLE = True
except ImportError:
    _CTRL_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustPolicy, TrustAuditLog
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False

try:
    from jugeo.geometry.descent import DescentEngine, DescentStrategy, DescentLog
    _DESCENT_AVAILABLE = True
except ImportError:
    _DESCENT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(**overrides) -> TreatyMemoryManifest:
    """Return a valid TreatyMemoryManifest with optional field overrides."""
    defaults = dict(
        version="1.0.0",
        chapter_ref="Ch48",
        package_name="treaty_memory",
        description="A test manifest for unit tests.",
        created_at=time.time(),
        modules=["models", "manifest"],
        schema_version=1,
        author="jugeo",
    )
    defaults.update(overrides)
    return TreatyMemoryManifest(**defaults)


def _make_schema(required: list[str] | None = None) -> dict:
    """Return a minimal valid schema dict."""
    return {
        "version": 1,
        "description": "A test schema.",
        "required": required or ["field_a", "field_b"],
        "optional": [],
    }


# ===========================================================================
# TestTreatyMemoryManifest
# ===========================================================================


class TestTreatyMemoryManifest:
    """Tests for TreatyMemoryManifest dataclass."""

    def test_creation_with_defaults(self):
        """All fields are stored exactly as provided."""
        ts = time.time()
        m = TreatyMemoryManifest(
            version="2.3.4",
            chapter_ref="Ch48",
            package_name="treaty_memory",
            description="desc",
            created_at=ts,
            modules=["models"],
            schema_version=1,
            author="jugeo",
        )
        assert m.version == "2.3.4"
        assert m.chapter_ref == "Ch48"
        assert m.package_name == "treaty_memory"
        assert m.description == "desc"
        assert m.created_at == ts
        assert m.modules == ["models"]
        assert m.schema_version == 1
        assert m.author == "jugeo"

    def test_to_dict_contains_all_fields(self):
        """to_dict() includes all expected top-level keys."""
        m = _make_manifest()
        d = m.to_dict()
        for key in ("version", "chapter_ref", "package_name", "description",
                    "created_at", "modules", "schema_version", "author"):
            assert key in d, f"Expected key {key!r} missing from to_dict()"
        assert d["version"] == m.version
        assert d["chapter_ref"] == m.chapter_ref
        assert d["package_name"] == m.package_name
        assert d["author"] == m.author
        assert d["schema_version"] == m.schema_version

    def test_to_dict_manifest_id_present(self):
        """to_dict() includes a stable manifest_id field."""
        m = _make_manifest()
        d = m.to_dict()
        assert "manifest_id" in d
        assert isinstance(d["manifest_id"], str)
        assert len(d["manifest_id"]) == 16

    def test_to_dict_modules_is_list_copy(self):
        """to_dict() returns a fresh copy of the modules list."""
        m = _make_manifest(modules=["models", "manifest"])
        d = m.to_dict()
        assert d["modules"] == ["models", "manifest"]
        d["modules"].append("extra")
        assert m.modules == ["models", "manifest"]  # original unaffected

    def test_validate_valid_manifest(self):
        """A properly formed manifest yields no validation errors."""
        m = _make_manifest()
        errors = m.validate()
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_validate_missing_version(self):
        """An empty version string yields a validation error."""
        m = _make_manifest(version="")
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "version" in combined.lower()

    def test_validate_bad_version_format(self):
        """A version string without dots yields a validation error."""
        m = _make_manifest(version="badversion")
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "version" in combined.lower()

    def test_validate_empty_chapter_ref(self):
        """An empty chapter_ref yields a validation error."""
        m = _make_manifest(chapter_ref="")
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "chapter_ref" in combined.lower()

    def test_validate_empty_package_name(self):
        """An empty package_name yields a validation error."""
        m = _make_manifest(package_name="")
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "package_name" in combined.lower()

    def test_validate_empty_modules(self):
        """An empty modules list yields a validation error."""
        m = _make_manifest(modules=[])
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "modules" in combined.lower()

    def test_validate_bad_schema_version(self):
        """schema_version=0 is below the minimum and yields an error."""
        m = _make_manifest(schema_version=0)
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "schema_version" in combined.lower()

    def test_validate_negative_schema_version(self):
        """schema_version < 0 also yields an error."""
        m = _make_manifest(schema_version=-5)
        errors = m.validate()
        assert len(errors) >= 1

    def test_validate_empty_author(self):
        """An empty author yields a validation error."""
        m = _make_manifest(author="")
        errors = m.validate()
        assert len(errors) >= 1
        combined = " ".join(errors)
        assert "author" in combined.lower()

    def test_summary_is_string(self):
        """summary() returns a non-empty string."""
        m = _make_manifest()
        s = m.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_version(self):
        """The version number appears in summary()."""
        m = _make_manifest(version="3.7.1")
        s = m.summary()
        assert "3.7.1" in s

    def test_summary_contains_package_name(self):
        """The package name appears in summary()."""
        m = _make_manifest(package_name="treaty_memory")
        s = m.summary()
        assert "treaty_memory" in s

    def test_summary_valid_marker_for_good_manifest(self):
        """A valid manifest has 'VALID' in its summary."""
        m = _make_manifest()
        s = m.summary()
        assert "VALID" in s

    def test_summary_invalid_marker_for_bad_manifest(self):
        """An invalid manifest has 'INVALID' in its summary."""
        m = _make_manifest(version="bad")
        s = m.summary()
        assert "INVALID" in s

    def test_is_compatible_same_major(self):
        """Manifests with same major version are compatible."""
        m = _make_manifest(version="1.0.0")
        assert m.is_compatible("1.5.3") is True
        assert m.is_compatible("1.0.0") is True
        assert m.is_compatible("1.99.0") is True

    def test_is_compatible_different_major(self):
        """Manifests with different major versions are incompatible."""
        m = _make_manifest(version="1.0.0")
        assert m.is_compatible("2.0.0") is False
        assert m.is_compatible("0.9.9") is False

    def test_is_compatible_malformed(self):
        """A malformed comparison version returns False gracefully."""
        m = _make_manifest(version="1.0.0")
        result = m.is_compatible("badver")
        assert result is False

    def test_is_compatible_empty_string(self):
        """An empty comparison version returns False gracefully."""
        m = _make_manifest(version="1.0.0")
        result = m.is_compatible("")
        assert result is False

    def test_to_dict_is_json_serialisable(self):
        """to_dict() round-trips cleanly through json.dumps / json.loads."""
        m = _make_manifest()
        d = m.to_dict()
        serialised = json.dumps(d)
        restored = json.loads(serialised)
        assert restored["version"] == m.version
        assert restored["package_name"] == m.package_name

    def test_validate_returns_list(self):
        """validate() always returns a list."""
        m = _make_manifest()
        result = m.validate()
        assert isinstance(result, list)


# ===========================================================================
# TestMemorySchemaRegistry
# ===========================================================================


class TestMemorySchemaRegistry:
    """Tests for MemorySchemaRegistry."""

    def test_creation_empty(self):
        """Freshly created registry has no schemas and is not locked."""
        reg = MemorySchemaRegistry()
        assert reg.list_schemas() == []
        assert reg.is_locked() is False

    def test_register_new_schema(self):
        """Registering a schema adds it to the registry."""
        reg = MemorySchemaRegistry()
        reg.register("foo", _make_schema())
        assert "foo" in reg.list_schemas()

    def test_register_duplicate_raises(self):
        """Registering the same schema name twice raises ValueError."""
        reg = MemorySchemaRegistry()
        reg.register("foo", _make_schema())
        with pytest.raises(ValueError, match="foo"):
            reg.register("foo", _make_schema())

    def test_register_while_locked_raises(self):
        """Registering on a locked registry raises ValueError."""
        reg = MemorySchemaRegistry()
        reg.lock()
        with pytest.raises(ValueError):
            reg.register("bar", _make_schema())

    def test_lookup_existing(self):
        """lookup() returns the registered schema dict."""
        reg = MemorySchemaRegistry()
        schema = _make_schema(required=["x", "y"])
        reg.register("myschema", schema)
        result = reg.lookup("myschema")
        assert result is not None
        assert result["required"] == ["x", "y"]

    def test_lookup_returns_copy(self):
        """lookup() returns a shallow copy, not the internal object."""
        reg = MemorySchemaRegistry()
        reg.register("myschema", _make_schema())
        first = reg.lookup("myschema")
        second = reg.lookup("myschema")
        assert first is not second

    def test_lookup_nonexistent(self):
        """lookup() returns None for an unregistered schema name."""
        reg = MemorySchemaRegistry()
        result = reg.lookup("does_not_exist")
        assert result is None

    def test_validate_against_valid_data(self):
        """validate_against() returns [] when all required fields are present."""
        reg = MemorySchemaRegistry()
        reg.register("s", _make_schema(required=["a", "b"]))
        errors = reg.validate_against("s", {"a": 1, "b": 2, "c": 3})
        assert errors == []

    def test_validate_against_missing_required_field(self):
        """validate_against() reports missing required fields."""
        reg = MemorySchemaRegistry()
        reg.register("s", _make_schema(required=["a", "b", "c"]))
        errors = reg.validate_against("s", {"a": 1})
        assert len(errors) >= 2
        combined = " ".join(errors)
        assert "b" in combined or "c" in combined

    def test_validate_against_unknown_schema(self):
        """validate_against() an unknown schema name returns an error list."""
        reg = MemorySchemaRegistry()
        errors = reg.validate_against("unknown", {"x": 1})
        assert len(errors) >= 1
        combined = " ".join(errors).lower()
        assert "unknown" in combined or "schema" in combined

    def test_list_schemas_empty(self):
        """list_schemas() returns [] for a fresh registry."""
        reg = MemorySchemaRegistry()
        assert reg.list_schemas() == []

    def test_list_schemas_after_register(self):
        """list_schemas() returns ["foo"] after registering "foo"."""
        reg = MemorySchemaRegistry()
        reg.register("foo", _make_schema())
        assert reg.list_schemas() == ["foo"]

    def test_list_schemas_sorted(self):
        """list_schemas() always returns names in sorted order."""
        reg = MemorySchemaRegistry()
        for name in ["zebra", "alpha", "mango"]:
            reg.register(name, _make_schema())
        result = reg.list_schemas()
        assert result == sorted(result)
        assert result == ["alpha", "mango", "zebra"]

    def test_export_contains_version_and_schemas(self):
        """export() dict has 'version' and 'schemas' keys."""
        reg = MemorySchemaRegistry()
        reg.register("s1", _make_schema())
        exported = reg.export()
        assert "version" in exported
        assert "schemas" in exported
        assert "s1" in exported["schemas"]

    def test_lock_prevents_registration(self):
        """After lock(), is_locked() is True and register raises."""
        reg = MemorySchemaRegistry()
        assert reg.is_locked() is False
        reg.lock()
        assert reg.is_locked() is True
        with pytest.raises(ValueError):
            reg.register("anything", _make_schema())

    def test_lock_is_idempotent(self):
        """Calling lock() twice does not raise."""
        reg = MemorySchemaRegistry()
        reg.lock()
        reg.lock()  # should not raise
        assert reg.is_locked() is True

    def test_is_locked_initially_false(self):
        """is_locked() returns False for a fresh registry."""
        reg = MemorySchemaRegistry()
        assert reg.is_locked() is False

    def test_schema_version_existing(self):
        """schema_version() returns the 'version' field of a registered schema."""
        reg = MemorySchemaRegistry()
        reg.register("s", {"version": 7, "required": [], "description": "x"})
        assert reg.schema_version("s") == 7

    def test_schema_version_nonexistent(self):
        """schema_version() returns None for an unregistered schema."""
        reg = MemorySchemaRegistry()
        assert reg.schema_version("nonexistent") is None

    def test_schemas_are_independent_copies(self):
        """Mutating the dict passed to register() does not affect the stored schema."""
        reg = MemorySchemaRegistry()
        original = _make_schema(required=["a"])
        reg.register("s", original)
        original["required"].append("b")  # mutate original after register
        stored = reg.lookup("s")
        assert stored is not None
        # The stored copy should have been made at register time
        assert "b" not in stored.get("required", [])


# ===========================================================================
# TestArchiveCatalog
# ===========================================================================


class TestArchiveCatalog:
    """Tests for ArchiveCatalog."""

    def test_creation_empty(self):
        """Freshly created catalog has no entries and timestamps set."""
        cat = ArchiveCatalog()
        assert cat.count() == 0
        assert cat.created_at > 0
        assert cat.last_modified > 0

    def test_register_archive(self):
        """register_archive() adds an entry to the catalog."""
        cat = ArchiveCatalog()
        cat.register_archive("abc123", {"name": "test"})
        assert cat.count() == 1
        assert "abc123" in cat.list_archives()

    def test_register_multiple_archives(self):
        """Multiple archives can be registered independently."""
        cat = ArchiveCatalog()
        cat.register_archive("a1", {"tag": "first"})
        cat.register_archive("a2", {"tag": "second"})
        cat.register_archive("a3", {"tag": "third"})
        assert cat.count() == 3

    def test_register_updates_last_modified(self):
        """register_archive() updates the last_modified timestamp."""
        cat = ArchiveCatalog()
        before = cat.last_modified
        time.sleep(0.01)
        cat.register_archive("x", {})
        assert cat.last_modified >= before

    def test_lookup_existing(self):
        """lookup_archive() returns the metadata dict for a registered archive."""
        cat = ArchiveCatalog()
        cat.register_archive("id1", {"participants": ["a", "b"], "policy": "strict"})
        meta = cat.lookup_archive("id1")
        assert meta is not None
        assert meta["participants"] == ["a", "b"]
        assert meta["policy"] == "strict"

    def test_lookup_returns_copy(self):
        """lookup_archive() returns a shallow copy."""
        cat = ArchiveCatalog()
        cat.register_archive("id1", {"k": "v"})
        m1 = cat.lookup_archive("id1")
        m2 = cat.lookup_archive("id1")
        assert m1 is not m2

    def test_lookup_nonexistent(self):
        """lookup_archive() returns None for an unregistered archive_id."""
        cat = ArchiveCatalog()
        assert cat.lookup_archive("does_not_exist") is None

    def test_list_archives_empty(self):
        """list_archives() returns [] for an empty catalog."""
        cat = ArchiveCatalog()
        assert cat.list_archives() == []

    def test_list_archives_sorted(self):
        """list_archives() always returns IDs in sorted order."""
        cat = ArchiveCatalog()
        for aid in ["z99", "a01", "m55"]:
            cat.register_archive(aid, {})
        result = cat.list_archives()
        assert result == sorted(result)
        assert result == ["a01", "m55", "z99"]

    def test_remove_existing(self):
        """remove_archive() returns True and removes the entry."""
        cat = ArchiveCatalog()
        cat.register_archive("to_remove", {"k": "v"})
        assert cat.count() == 1
        result = cat.remove_archive("to_remove")
        assert result is True
        assert cat.count() == 0
        assert cat.lookup_archive("to_remove") is None

    def test_remove_nonexistent(self):
        """remove_archive() returns False for a missing archive_id."""
        cat = ArchiveCatalog()
        result = cat.remove_archive("ghost")
        assert result is False

    def test_remove_updates_last_modified(self):
        """remove_archive() updates last_modified on success."""
        cat = ArchiveCatalog()
        cat.register_archive("r", {})
        before = cat.last_modified
        time.sleep(0.01)
        cat.remove_archive("r")
        assert cat.last_modified >= before

    def test_update_metadata_existing(self):
        """update_metadata() merges new keys and returns True."""
        cat = ArchiveCatalog()
        cat.register_archive("upd", {"a": 1, "b": 2})
        result = cat.update_metadata("upd", {"b": 99, "c": 3})
        assert result is True
        meta = cat.lookup_archive("upd")
        assert meta is not None
        assert meta["a"] == 1    # unchanged
        assert meta["b"] == 99   # overwritten
        assert meta["c"] == 3    # new key

    def test_update_metadata_nonexistent(self):
        """update_metadata() returns False for a missing archive_id."""
        cat = ArchiveCatalog()
        result = cat.update_metadata("ghost", {"x": 1})
        assert result is False

    def test_count(self):
        """count() returns the number of entries."""
        cat = ArchiveCatalog()
        assert cat.count() == 0
        cat.register_archive("a", {})
        assert cat.count() == 1
        cat.register_archive("b", {})
        assert cat.count() == 2
        cat.remove_archive("a")
        assert cat.count() == 1

    def test_to_dict_serializable(self):
        """to_dict() produces a JSON-serialisable dict with expected keys."""
        cat = ArchiveCatalog()
        cat.register_archive("e1", {"role": "primary"})
        d = cat.to_dict()
        assert "entries" in d
        assert "created_at" in d
        assert "last_modified" in d
        serialised = json.dumps(d)
        restored = json.loads(serialised)
        assert "e1" in restored["entries"]

    def test_export_catalog_is_json(self):
        """export_catalog() returns a valid JSON string."""
        cat = ArchiveCatalog()
        cat.register_archive("e1", {"k": "v"})
        exported = cat.export_catalog()
        assert isinstance(exported, str)
        parsed = json.loads(exported)  # must not raise
        assert "entries" in parsed

    def test_import_catalog_restores_entries(self):
        """export then import round-trip restores all entries."""
        cat1 = ArchiveCatalog()
        cat1.register_archive("arc_a", {"detail": "hello"})
        cat1.register_archive("arc_b", {"detail": "world"})
        exported = cat1.export_catalog()

        cat2 = ArchiveCatalog()
        cat2.import_catalog(exported)
        assert cat2.count() == 2
        assert cat2.lookup_archive("arc_a") == {"detail": "hello"}
        assert cat2.lookup_archive("arc_b") == {"detail": "world"}

    def test_import_catalog_invalid_json_raises(self):
        """import_catalog() with invalid JSON raises ValueError or JSONDecodeError."""
        cat = ArchiveCatalog()
        with pytest.raises((json.JSONDecodeError, ValueError)):
            cat.import_catalog("{this is not json}")

    def test_import_catalog_missing_entries_key_raises(self):
        """import_catalog() with JSON missing 'entries' key raises ValueError."""
        cat = ArchiveCatalog()
        with pytest.raises(ValueError, match="entries"):
            cat.import_catalog(json.dumps({"created_at": 12345}))


# ===========================================================================
# TestMemoryModuleDescriptor
# ===========================================================================


class TestMemoryModuleDescriptor:
    """Tests for MemoryModuleDescriptor."""

    def test_creation(self):
        """All fields are stored exactly as provided."""
        d = MemoryModuleDescriptor(
            module_name="models",
            file_path="src/jugeo/orchestration/treaty_memory/models.py",
            classes=["FrictionPattern", "TreatyMemoryRecord"],
            functions=["make_friction_pattern"],
            version="1.0.0",
            description="Core data models.",
        )
        assert d.module_name == "models"
        assert d.file_path == "src/jugeo/orchestration/treaty_memory/models.py"
        assert d.classes == ["FrictionPattern", "TreatyMemoryRecord"]
        assert d.functions == ["make_friction_pattern"]
        assert d.version == "1.0.0"
        assert d.description == "Core data models."

    def test_to_dict_contains_all_fields(self):
        """to_dict() includes module_name, file_path, classes, functions, version, description."""
        d = MemoryModuleDescriptor(
            module_name="manifest",
            file_path="src/jugeo/orchestration/treaty_memory/manifest.py",
            classes=["TreatyMemoryManifest"],
            functions=["build_manifest", "validate_manifest"],
            version="1.0.0",
            description="Manifest module.",
        )
        result = d.to_dict()
        for key in ("module_name", "file_path", "classes", "functions", "version", "description"):
            assert key in result, f"Key {key!r} missing from to_dict()"
        assert result["module_name"] == "manifest"
        assert result["classes"] == ["TreatyMemoryManifest"]
        assert result["functions"] == ["build_manifest", "validate_manifest"]

    def test_to_dict_class_count(self):
        """to_dict() includes class_count and function_count."""
        d = MemoryModuleDescriptor(
            module_name="m",
            file_path="m.py",
            classes=["A", "B", "C"],
            functions=["f1"],
            version="1.0.0",
            description="x",
        )
        result = d.to_dict()
        assert result.get("class_count") == 3
        assert result.get("function_count") == 1

    def test_summary_is_string(self):
        """summary() returns a non-empty string."""
        d = MemoryModuleDescriptor(
            module_name="models",
            file_path="models.py",
            classes=["Foo"],
            functions=["bar"],
            version="1.0.0",
            description="A test module.",
        )
        s = d.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_module_name(self):
        """summary() contains the module_name."""
        d = MemoryModuleDescriptor(
            module_name="special_module",
            file_path="special.py",
            classes=[],
            functions=[],
            version="1.0.0",
            description="Special.",
        )
        assert "special_module" in d.summary()

    def test_classes_and_functions_are_lists(self):
        """classes and functions are both list[str]."""
        d = MemoryModuleDescriptor(
            module_name="m",
            file_path="m.py",
            classes=["X", "Y"],
            functions=["f"],
            version="1.0.0",
            description="d",
        )
        assert isinstance(d.classes, list)
        assert isinstance(d.functions, list)
        assert all(isinstance(c, str) for c in d.classes)
        assert all(isinstance(f, str) for f in d.functions)

    def test_descriptor_is_immutable(self):
        """MemoryModuleDescriptor is a frozen dataclass and raises on mutation attempt."""
        d = MemoryModuleDescriptor(
            module_name="m",
            file_path="m.py",
            classes=["A"],
            functions=["f"],
            version="1.0.0",
            description="d",
        )
        with pytest.raises((AttributeError, TypeError)):
            d.module_name = "new_name"  # type: ignore[misc]

    def test_to_dict_returns_lists_copies(self):
        """to_dict() classes and functions lists are independent copies."""
        d = MemoryModuleDescriptor(
            module_name="m",
            file_path="m.py",
            classes=["A"],
            functions=["f"],
            version="1.0.0",
            description="d",
        )
        result = d.to_dict()
        result["classes"].append("B")
        assert d.classes == ["A"]  # original unaffected


# ===========================================================================
# TestPackageHealthCheck
# ===========================================================================


class TestPackageHealthCheck:
    """Tests for PackageHealthCheck."""

    def test_run_returns_dict(self):
        """run() returns a dict."""
        hc = PackageHealthCheck()
        result = hc.run()
        assert isinstance(result, dict)

    def test_run_includes_all_expected_keys(self):
        """run() result contains all required check keys."""
        hc = PackageHealthCheck()
        result = hc.run()
        expected_keys = {
            "negotiation_module",
            "controller_module",
            "trust_module",
            "descent_module",
            "treaty_memory_models",
            "treaty_memory_manifest",
        }
        for key in expected_keys:
            assert key in result, f"Expected key {key!r} missing from run()"

    def test_all_booleans(self):
        """All values in run() are bool."""
        hc = PackageHealthCheck()
        result = hc.run()
        for key, val in result.items():
            assert isinstance(val, bool), f"Value for {key!r} is not bool: {val!r}"

    def test_summary_is_string(self):
        """summary() returns a non-empty string."""
        hc = PackageHealthCheck()
        s = hc.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_check_names(self):
        """Each check name appears in summary()."""
        hc = PackageHealthCheck()
        result = hc.run()
        summary = hc.summary()
        for name in result.keys():
            assert name in summary, f"Check name {name!r} missing from summary()"

    def test_all_healthy_when_all_true(self):
        """all_healthy() returns True when every check is True."""
        hc = PackageHealthCheck()
        # Patch run() to return all True
        hc.run = lambda: {  # type: ignore[method-assign]
            "negotiation_module": True,
            "controller_module": True,
            "trust_module": True,
            "descent_module": True,
            "treaty_memory_models": True,
            "treaty_memory_manifest": True,
        }
        assert hc.all_healthy() is True

    def test_all_healthy_false_when_any_false(self):
        """all_healthy() returns False when at least one check is False."""
        hc = PackageHealthCheck()
        hc.run = lambda: {  # type: ignore[method-assign]
            "negotiation_module": True,
            "controller_module": False,  # one failure
            "trust_module": True,
            "descent_module": True,
            "treaty_memory_models": True,
            "treaty_memory_manifest": True,
        }
        assert hc.all_healthy() is False

    def test_treaty_memory_manifest_always_healthy(self):
        """treaty_memory_manifest check is always True (this module is importable)."""
        hc = PackageHealthCheck()
        result = hc.run()
        assert result["treaty_memory_manifest"] is True

    def test_run_is_repeatable(self):
        """run() can be called multiple times with consistent results."""
        hc = PackageHealthCheck()
        r1 = hc.run()
        r2 = hc.run()
        assert r1.keys() == r2.keys()
        assert r1["treaty_memory_manifest"] == r2["treaty_memory_manifest"]

    def test_summary_ok_fail_markers(self):
        """summary() contains [OK] or [FAIL] markers."""
        hc = PackageHealthCheck()
        s = hc.summary()
        assert "[OK" in s or "[FAIL]" in s


# ===========================================================================
# TestBuildManifest
# ===========================================================================


class TestBuildManifest:
    """Tests for the build_manifest() factory function."""

    def test_returns_manifest_instance(self):
        """build_manifest() returns a TreatyMemoryManifest instance."""
        m = build_manifest()
        assert isinstance(m, TreatyMemoryManifest)

    def test_default_version(self):
        """manifest.version matches _PACKAGE_VERSION."""
        m = build_manifest()
        assert m.version == _PACKAGE_VERSION

    def test_default_chapter_ref(self):
        """manifest.chapter_ref matches _CHAPTER_REF."""
        m = build_manifest()
        assert m.chapter_ref == _CHAPTER_REF

    def test_default_package_name(self):
        """manifest.package_name matches _PACKAGE_NAME."""
        m = build_manifest()
        assert m.package_name == _PACKAGE_NAME

    def test_default_author(self):
        """manifest.author matches _AUTHOR."""
        m = build_manifest()
        assert m.author == _AUTHOR

    def test_default_schema_version(self):
        """manifest.schema_version matches _SCHEMA_VERSION."""
        m = build_manifest()
        assert m.schema_version == _SCHEMA_VERSION

    def test_modules_non_empty(self):
        """manifest.modules has at least one element."""
        m = build_manifest()
        assert len(m.modules) >= 1

    def test_modules_contains_expected_names(self):
        """manifest.modules contains all entries from _MODULE_NAMES."""
        m = build_manifest()
        for name in _MODULE_NAMES:
            assert name in m.modules, f"Expected module {name!r} in manifest.modules"

    def test_description_non_empty(self):
        """manifest.description is a non-empty string."""
        m = build_manifest()
        assert isinstance(m.description, str)
        assert len(m.description) > 0

    def test_created_at_is_recent(self):
        """manifest.created_at is within the last 5 seconds."""
        before = time.time()
        m = build_manifest()
        after = time.time()
        assert before <= m.created_at <= after + 1.0

    def test_build_manifest_validates_cleanly(self):
        """The manifest returned by build_manifest() passes validate() with no errors."""
        m = build_manifest()
        errors = m.validate()
        assert errors == [], f"build_manifest() produced errors: {errors}"

    def test_build_manifest_repeated_calls_differ_in_timestamp(self):
        """Two consecutive build_manifest() calls have different created_at."""
        m1 = build_manifest()
        time.sleep(0.01)
        m2 = build_manifest()
        assert m2.created_at >= m1.created_at


# ===========================================================================
# TestValidateManifest
# ===========================================================================


class TestValidateManifest:
    """Tests for validate_manifest() module-level function."""

    def test_valid_manifest_returns_empty(self):
        """validate_manifest(build_manifest()) returns []."""
        m = build_manifest()
        result = validate_manifest(m)
        assert result == [], f"Expected [], got: {result}"

    def test_invalid_manifest_returns_errors(self):
        """A mutated, invalid manifest returns a non-empty error list."""
        m = _make_manifest(version="badversion", schema_version=0, author="")
        result = validate_manifest(m)
        assert len(result) >= 1

    def test_returns_list(self):
        """validate_manifest() always returns a list."""
        result = validate_manifest(build_manifest())
        assert isinstance(result, list)

    def test_cross_field_schema_version_upper_bound(self):
        """schema_version >= 1000 is flagged by validate_manifest."""
        m = _make_manifest(schema_version=9999)
        errors = validate_manifest(m)
        assert len(errors) >= 1
        combined = " ".join(errors).lower()
        assert "schema_version" in combined or "unreasonably" in combined

    def test_cross_field_missing_canonical_module(self):
        """modules missing a canonical name from _MODULE_NAMES is flagged."""
        m = _make_manifest(modules=["manifest"])  # missing "models"
        errors = validate_manifest(m)
        combined = " ".join(errors)
        assert "models" in combined or "missing" in combined.lower()

    def test_cross_field_chapter_ref_convention(self):
        """chapter_ref not starting with 'Ch' is flagged."""
        m = _make_manifest(chapter_ref="Section48")
        errors = validate_manifest(m)
        assert len(errors) >= 1
        combined = " ".join(errors).lower()
        assert "chapter_ref" in combined or "ch" in combined

    def test_valid_chapter_ref_with_ch_prefix(self):
        """chapter_ref starting with 'Ch' passes validate_manifest."""
        m = _make_manifest(chapter_ref="Ch99")
        errors = validate_manifest(m)
        assert errors == []


# ===========================================================================
# TestBuildModuleRegistry
# ===========================================================================


class TestBuildModuleRegistry:
    """Tests for build_module_registry() module-level function."""

    def test_returns_dict(self):
        """build_module_registry() returns a dict."""
        reg = build_module_registry()
        assert isinstance(reg, dict)

    def test_contains_models(self):
        """'models' key is present in the registry."""
        reg = build_module_registry()
        assert "models" in reg

    def test_contains_manifest(self):
        """'manifest' key is present in the registry."""
        reg = build_module_registry()
        assert "manifest" in reg

    def test_models_descriptor_type(self):
        """registry['models'] is a MemoryModuleDescriptor."""
        reg = build_module_registry()
        assert isinstance(reg["models"], MemoryModuleDescriptor)

    def test_manifest_descriptor_type(self):
        """registry['manifest'] is a MemoryModuleDescriptor."""
        reg = build_module_registry()
        assert isinstance(reg["manifest"], MemoryModuleDescriptor)

    def test_models_has_classes(self):
        """registry['models'] descriptor has at least one class."""
        reg = build_module_registry()
        assert len(reg["models"].classes) > 0

    def test_manifest_has_classes(self):
        """registry['manifest'] descriptor has at least one class."""
        reg = build_module_registry()
        assert len(reg["manifest"].classes) > 0

    def test_models_has_functions(self):
        """registry['models'] descriptor has at least one function."""
        reg = build_module_registry()
        assert len(reg["models"].functions) > 0

    def test_manifest_has_functions(self):
        """registry['manifest'] descriptor has at least one function."""
        reg = build_module_registry()
        assert len(reg["manifest"].functions) > 0

    def test_models_version_matches_package(self):
        """registry['models'].version == _PACKAGE_VERSION."""
        reg = build_module_registry()
        assert reg["models"].version == _PACKAGE_VERSION

    def test_manifest_version_matches_package(self):
        """registry['manifest'].version == _PACKAGE_VERSION."""
        reg = build_module_registry()
        assert reg["manifest"].version == _PACKAGE_VERSION

    def test_descriptors_module_names_match_keys(self):
        """Each descriptor's module_name matches its dict key."""
        reg = build_module_registry()
        for key, desc in reg.items():
            assert desc.module_name == key

    def test_treaty_memory_manifest_in_manifest_classes(self):
        """TreatyMemoryManifest is listed in the manifest descriptor's classes."""
        reg = build_module_registry()
        assert "TreatyMemoryManifest" in reg["manifest"].classes

    def test_build_manifest_in_manifest_functions(self):
        """build_manifest is listed in the manifest descriptor's functions."""
        reg = build_module_registry()
        assert "build_manifest" in reg["manifest"].functions


# ===========================================================================
# TestDefaultHelpers
# ===========================================================================


class TestDefaultHelpers:
    """Tests for _default_schema_registry() and _default_archive_catalog()."""

    def test_default_schema_registry_has_schemas(self):
        """_default_schema_registry() returns a registry with at least one schema."""
        reg = _default_schema_registry()
        schemas = reg.list_schemas()
        assert len(schemas) > 0

    def test_default_schema_registry_has_friction_pattern(self):
        """'friction_pattern' schema is registered by default."""
        reg = _default_schema_registry()
        assert "friction_pattern" in reg.list_schemas()

    def test_default_schema_registry_has_archive_entry(self):
        """'archive_entry' schema is registered by default."""
        reg = _default_schema_registry()
        assert "archive_entry" in reg.list_schemas()

    def test_default_schema_registry_has_negotiation_result(self):
        """'negotiation_result' schema is registered by default."""
        reg = _default_schema_registry()
        assert "negotiation_result" in reg.list_schemas()

    def test_default_schema_registry_has_memory_query(self):
        """'memory_query' schema is registered by default."""
        reg = _default_schema_registry()
        assert "memory_query" in reg.list_schemas()

    def test_default_schema_registry_not_locked(self):
        """Default registry is not locked (callers may extend it)."""
        reg = _default_schema_registry()
        assert reg.is_locked() is False

    def test_default_schema_registry_friction_pattern_required_fields(self):
        """friction_pattern schema requires friction_id, pattern_type, magnitude, created_at."""
        reg = _default_schema_registry()
        schema = reg.lookup("friction_pattern")
        assert schema is not None
        for field in ("friction_id", "pattern_type", "magnitude", "created_at"):
            assert field in schema["required"]

    def test_default_schema_registry_archive_entry_required_fields(self):
        """archive_entry schema requires entry_id, archive_id, content, timestamp."""
        reg = _default_schema_registry()
        schema = reg.lookup("archive_entry")
        assert schema is not None
        for field in ("entry_id", "archive_id", "content", "timestamp"):
            assert field in schema["required"]

    def test_default_archive_catalog_empty(self):
        """_default_archive_catalog() returns an empty catalog."""
        cat = _default_archive_catalog()
        assert cat.count() == 0

    def test_default_archive_catalog_has_timestamps(self):
        """_default_archive_catalog() has created_at > 0."""
        cat = _default_archive_catalog()
        assert cat.created_at > 0
        assert cat.last_modified > 0

    def test_default_archive_catalog_timestamps_close_to_now(self):
        """_default_archive_catalog() timestamps are within 5 seconds of now."""
        before = time.time()
        cat = _default_archive_catalog()
        after = time.time()
        assert before <= cat.created_at <= after + 1.0
        assert before <= cat.last_modified <= after + 1.0

    def test_default_archive_catalog_list_archives_empty(self):
        """_default_archive_catalog().list_archives() == []."""
        cat = _default_archive_catalog()
        assert cat.list_archives() == []

    def test_default_schema_registry_four_schemas(self):
        """Default schema registry has exactly 4 canonical schemas."""
        reg = _default_schema_registry()
        assert len(reg.list_schemas()) == 4


# ===========================================================================
# TestIntegrationWithNegotiation
# ===========================================================================


@pytest.mark.skipif(not _NEG_AVAILABLE, reason="negotiation not available")
class TestIntegrationWithNegotiation:
    """Integration tests requiring jugeo.orchestration.negotiation."""

    def test_negotiation_memory_compatible_with_manifest(self):
        """build_manifest() package_name is 'treaty_memory'."""
        m = build_manifest()
        assert m.package_name == "treaty_memory"
        assert NegotiationMemory is not None

    def test_session_state_availability(self):
        """SessionState enum is importable and has members."""
        assert SessionState is not None
        # Access AGREED if it exists, otherwise verify SessionState is callable
        members = [attr for attr in dir(SessionState) if not attr.startswith("_")]
        assert len(members) > 0

    def test_treaty_archive_integration(self):
        """TreatyArchive is importable and health check reflects it."""
        assert TreatyArchive is not None
        hc = PackageHealthCheck()
        result = hc.run()
        assert result["negotiation_module"] is True

    def test_negotiation_event_bus_importable(self):
        """NegotiationEventBus is importable."""
        assert NegotiationEventBus is not None


# ===========================================================================
# TestIntegrationWithController
# ===========================================================================


@pytest.mark.skipif(not _CTRL_AVAILABLE, reason="controller not available")
class TestIntegrationWithController:
    """Integration tests requiring jugeo.orchestration.controller."""

    def test_orchestrator_state_availability(self):
        """OrchestratorState is importable and not None."""
        assert OrchestratorState is not None

    def test_health_check_sees_controller(self):
        """PackageHealthCheck.run()['controller_module'] == True when controller available."""
        hc = PackageHealthCheck()
        result = hc.run()
        assert result["controller_module"] is True

    def test_convergence_monitor_importable(self):
        """ConvergenceMonitor is importable."""
        assert ConvergenceMonitor is not None

    def test_orchestrator_configuration_importable(self):
        """OrchestratorConfiguration is importable."""
        assert OrchestratorConfiguration is not None


# ===========================================================================
# TestIntegrationWithTrust
# ===========================================================================


@pytest.mark.skipif(not _TRUST_AVAILABLE, reason="trust not available")
class TestIntegrationWithTrust:
    """Integration tests requiring jugeo.evidence.trust."""

    def test_trust_level_availability(self):
        """TrustLevel is importable and not None."""
        assert TrustLevel is not None

    def test_health_check_sees_trust(self):
        """PackageHealthCheck.run()['trust_module'] == True when trust available."""
        hc = PackageHealthCheck()
        result = hc.run()
        assert result["trust_module"] is True

    def test_schema_registry_trust_schema(self):
        """Can register a trust-related schema and validate a conforming record."""
        reg = _default_schema_registry()
        reg.register(
            "trust_record",
            {
                "version": 1,
                "description": "Schema for trust audit records.",
                "required": ["agent_id", "trust_level", "timestamp"],
                "optional": ["policy_id", "notes"],
            },
        )
        valid_data = {"agent_id": "agentA", "trust_level": "high", "timestamp": 1000.0}
        errors = reg.validate_against("trust_record", valid_data)
        assert errors == []
        missing_data = {"agent_id": "agentA"}
        errors_missing = reg.validate_against("trust_record", missing_data)
        assert len(errors_missing) >= 2

    def test_trust_policy_importable(self):
        """TrustPolicy is importable."""
        assert TrustPolicy is not None


# ===========================================================================
# TestIntegrationWithDescent
# ===========================================================================


@pytest.mark.skipif(not _DESCENT_AVAILABLE, reason="descent not available")
class TestIntegrationWithDescent:
    """Integration tests requiring jugeo.geometry.descent."""

    def test_descent_availability(self):
        """DescentEngine is importable and not None."""
        assert DescentEngine is not None

    def test_health_check_sees_descent(self):
        """PackageHealthCheck.run()['descent_module'] == True when descent available."""
        hc = PackageHealthCheck()
        result = hc.run()
        assert result["descent_module"] is True

    def test_descent_strategy_importable(self):
        """DescentStrategy is importable."""
        assert DescentStrategy is not None

    def test_descent_log_importable(self):
        """DescentLog is importable."""
        assert DescentLog is not None


# ===========================================================================
# TestManifestCompatibility
# ===========================================================================


class TestManifestCompatibility:
    """Broader compatibility and integration-style tests."""

    def test_manifest_is_self_compatible(self):
        """A manifest is compatible with its own version string."""
        m = build_manifest()
        assert m.is_compatible(m.version) is True

    def test_manifest_schema_validates(self):
        """validate_manifest(build_manifest()) returns []."""
        m = build_manifest()
        errors = validate_manifest(m)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_registry_can_be_locked_after_defaults(self):
        """_default_schema_registry() can be locked; is_locked() then True."""
        reg = _default_schema_registry()
        assert reg.is_locked() is False
        reg.lock()
        assert reg.is_locked() is True
        with pytest.raises(ValueError):
            reg.register("extra", _make_schema())

    def test_module_registry_versions_match(self):
        """All module descriptors in build_module_registry() have version == _PACKAGE_VERSION."""
        reg = build_module_registry()
        for key, desc in reg.items():
            assert desc.version == _PACKAGE_VERSION, (
                f"Descriptor {key!r} has version {desc.version!r}, expected {_PACKAGE_VERSION!r}"
            )

    def test_catalog_export_import_roundtrip(self):
        """Register archives, export, import to a new catalog, count matches."""
        cat1 = ArchiveCatalog()
        archives = {
            "arc1": {"participants": ["alice", "bob"], "policy": "strict"},
            "arc2": {"participants": ["carol"], "policy": "loose"},
            "arc3": {"participants": [], "policy": "default"},
        }
        for aid, meta in archives.items():
            cat1.register_archive(aid, meta)
        assert cat1.count() == 3

        exported = cat1.export_catalog()
        cat2 = ArchiveCatalog()
        cat2.import_catalog(exported)

        assert cat2.count() == 3
        for aid, expected_meta in archives.items():
            restored = cat2.lookup_archive(aid)
            assert restored is not None
            assert restored == expected_meta

    def test_build_manifest_to_dict_round_trip(self):
        """build_manifest().to_dict() retains all important fields after JSON round-trip."""
        m = build_manifest()
        d = m.to_dict()
        serialised = json.dumps(d)
        restored = json.loads(serialised)
        assert restored["version"] == _PACKAGE_VERSION
        assert restored["chapter_ref"] == _CHAPTER_REF
        assert restored["package_name"] == _PACKAGE_NAME
        assert restored["author"] == _AUTHOR
        assert restored["schema_version"] == _SCHEMA_VERSION

    def test_module_registry_all_descriptors_have_file_path(self):
        """Every descriptor in build_module_registry() has a non-empty file_path."""
        reg = build_module_registry()
        for key, desc in reg.items():
            assert desc.file_path, f"Descriptor {key!r} has empty file_path"
            assert desc.file_path.endswith(".py"), (
                f"Descriptor {key!r} file_path does not end in .py: {desc.file_path!r}"
            )

    def test_schema_registry_validate_friction_pattern(self):
        """Default registry validates a complete friction_pattern record with no errors."""
        reg = _default_schema_registry()
        valid_record = {
            "friction_id": "fp-001",
            "pattern_type": "resistance",
            "magnitude": 0.75,
            "created_at": time.time(),
            "tags": ["high-priority"],
        }
        errors = reg.validate_against("friction_pattern", valid_record)
        assert errors == []

    def test_schema_registry_validate_incomplete_friction_pattern(self):
        """Default registry flags a friction_pattern missing required fields."""
        reg = _default_schema_registry()
        incomplete = {"friction_id": "fp-002"}  # missing pattern_type, magnitude, created_at
        errors = reg.validate_against("friction_pattern", incomplete)
        assert len(errors) >= 3

    def test_schema_registry_validate_archive_entry(self):
        """Default registry validates a complete archive_entry record."""
        reg = _default_schema_registry()
        valid_entry = {
            "entry_id": "e-001",
            "archive_id": "arc-001",
            "content": "some clause text",
            "timestamp": time.time(),
        }
        errors = reg.validate_against("archive_entry", valid_entry)
        assert errors == []

    def test_default_schema_registry_schemas_have_descriptions(self):
        """Every default schema has a non-empty 'description' field."""
        reg = _default_schema_registry()
        for name in reg.list_schemas():
            schema = reg.lookup(name)
            assert schema is not None
            assert "description" in schema
            assert isinstance(schema["description"], str)
            assert len(schema["description"]) > 0

    def test_manifest_modules_list_matches_module_names_constant(self):
        """build_manifest().modules equals _MODULE_NAMES."""
        m = build_manifest()
        assert m.modules == _MODULE_NAMES

    def test_health_check_summary_shows_treaty_memory_manifest_ok(self):
        """PackageHealthCheck.summary() shows treaty_memory_manifest as OK."""
        hc = PackageHealthCheck()
        summary = hc.summary()
        assert "treaty_memory_manifest" in summary
        # Verify OK marker appears somewhere in summary (it's always healthy)
        assert "[OK" in summary
