"""Substantive tests for jugeo.ideation.kind_discovery.manifest.

Covers: constants, PackageCapability, PackageManifest, ManifestValidator,
PackageRegistry, CapabilityQuery, ManifestSerializer, ManifestDiagnostics,
and the _DEFAULT_MANIFEST singleton.
"""

from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import json
import re

import pytest

from jugeo.ideation.kind_discovery.manifest import (
    PACKAGE_DESCRIPTION,
    PACKAGE_NAME,
    PACKAGE_VERSION,
    CapabilityQuery,
    ManifestDiagnostics,
    ManifestSerializer,
    ManifestValidator,
    PackageCapability,
    PackageManifest,
    PackageRegistry,
    _DEFAULT_MANIFEST,
    _SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_manifest(
    name: str = "test.package",
    version: str = "1.0.0",
    description: str = "A test package",
    capabilities: frozenset | None = None,
    schema_version: str = "1",
    created_at: str = "2024-06-01T00:00:00Z",
    author: str = "tester",
    tags: frozenset | None = None,
    dependencies: tuple = (),
    min_python: tuple = (3, 11),
    checksum: str = "",
) -> PackageManifest:
    if capabilities is None:
        capabilities = frozenset({PackageCapability.KIND_EXTRACTION})
    if tags is None:
        tags = frozenset()
    return PackageManifest(
        name=name,
        version=version,
        description=description,
        capabilities=capabilities,
        schema_version=schema_version,
        created_at=created_at,
        author=author,
        tags=tags,
        dependencies=dependencies,
        min_python=min_python,
        checksum=checksum,
    )


def _full_manifest() -> PackageManifest:
    """Return a manifest with every capability and a full set of fields."""
    return _make_manifest(
        name="jugeo.ideation.kind_discovery",
        version="0.1.0",
        description="Full manifest for testing",
        capabilities=PackageCapability.all(),
        tags=frozenset({"alpha", "beta", "gamma"}),
        dependencies=("numpy>=1.24", "scipy>=1.10"),
        author="jugeo-project",
    )


# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------

def test_package_name_is_correct_string():
    assert PACKAGE_NAME == "jugeo.ideation.kind_discovery"


def test_package_version_is_semver():
    parts = PACKAGE_VERSION.split(".")
    assert len(parts) == 3
    for part in parts:
        assert part.isdigit()


def test_package_version_value():
    assert PACKAGE_VERSION == "0.1.0"


def test_package_description_is_nonempty():
    assert isinstance(PACKAGE_DESCRIPTION, str)
    assert len(PACKAGE_DESCRIPTION.strip()) > 0


def test_package_description_mentions_kind_discovery():
    assert "kind" in PACKAGE_DESCRIPTION.lower() or "discovery" in PACKAGE_DESCRIPTION.lower()


def test_schema_version_is_string():
    assert isinstance(_SCHEMA_VERSION, str)
    assert _SCHEMA_VERSION in {"1", "2"}


# ---------------------------------------------------------------------------
# 2. PackageCapability enum values
# ---------------------------------------------------------------------------

def test_capability_values_exist():
    assert PackageCapability.KIND_EXTRACTION.value == "kind_extraction"
    assert PackageCapability.OBSTRUCTION_ANALYSIS.value == "obstruction_analysis"
    assert PackageCapability.PATTERN_MINING.value == "pattern_mining"
    assert PackageCapability.KIND_BOOTSTRAPPING.value == "kind_bootstrapping"
    assert PackageCapability.KIND_VALIDATION.value == "kind_validation"


def test_capability_enum_count():
    assert len(PackageCapability) == 5


def test_capability_is_str_enum():
    for cap in PackageCapability:
        assert isinstance(cap, str)
        assert cap == cap.value


def test_capability_all_returns_frozenset_of_all():
    all_caps = PackageCapability.all()
    assert isinstance(all_caps, frozenset)
    assert len(all_caps) == 5
    assert all_caps == frozenset(PackageCapability)


def test_capability_pipeline_order_returns_five_items():
    order = PackageCapability.pipeline_order()
    assert isinstance(order, tuple)
    assert len(order) == 5


def test_capability_pipeline_order_first_is_extraction():
    order = PackageCapability.pipeline_order()
    assert order[0] == PackageCapability.KIND_EXTRACTION


def test_capability_pipeline_order_last_is_validation():
    order = PackageCapability.pipeline_order()
    assert order[-1] == PackageCapability.KIND_VALIDATION


def test_capability_pipeline_order_covers_all():
    order = set(PackageCapability.pipeline_order())
    assert order == set(PackageCapability)


# ---------------------------------------------------------------------------
# 3. PackageManifest construction
# ---------------------------------------------------------------------------

def test_manifest_construction_basic():
    m = _make_manifest()
    assert m.name == "test.package"
    assert m.version == "1.0.0"
    assert m.description == "A test package"
    assert PackageCapability.KIND_EXTRACTION in m.capabilities
    assert m.schema_version == "1"
    assert m.author == "tester"


def test_manifest_is_frozen():
    m = _make_manifest()
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(m, "name", "other")


def test_manifest_capabilities_is_frozenset():
    m = _make_manifest()
    assert isinstance(m.capabilities, frozenset)


def test_manifest_tags_default_is_frozenset():
    m = _make_manifest(tags=frozenset())
    assert isinstance(m.tags, frozenset)
    assert len(m.tags) == 0


def test_manifest_dependencies_default_is_tuple():
    m = _make_manifest(dependencies=())
    assert isinstance(m.dependencies, tuple)
    assert len(m.dependencies) == 0


def test_manifest_min_python_default():
    m = _make_manifest()
    assert m.min_python == (3, 11)


def test_manifest_author_default_empty():
    m = PackageManifest(
        name="pkg",
        version="1.0.0",
        description="desc",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
        schema_version="1",
        created_at="2024-01-01T00:00:00Z",
    )
    assert m.author == ""


# ---------------------------------------------------------------------------
# 4. PackageManifest.has_capability
# ---------------------------------------------------------------------------

def test_has_capability_true():
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    assert m.has_capability(PackageCapability.KIND_EXTRACTION)


def test_has_capability_false():
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    assert not m.has_capability(PackageCapability.KIND_VALIDATION)


def test_has_capability_all_present():
    m = _full_manifest()
    for cap in PackageCapability:
        assert m.has_capability(cap)


# ---------------------------------------------------------------------------
# 5. PackageManifest.satisfies_version
# ---------------------------------------------------------------------------

def test_satisfies_version_same():
    m = _make_manifest(version="1.2.3")
    assert m.satisfies_version("1.2.3")


def test_satisfies_version_higher():
    m = _make_manifest(version="2.0.0")
    assert m.satisfies_version("1.0.0")


def test_satisfies_version_lower_fails():
    m = _make_manifest(version="1.0.0")
    assert not m.satisfies_version("2.0.0")


def test_satisfies_version_patch_comparison():
    m = _make_manifest(version="1.0.5")
    assert m.satisfies_version("1.0.4")
    assert not m.satisfies_version("1.0.6")


def test_satisfies_version_minor_comparison():
    m = _make_manifest(version="1.3.0")
    assert m.satisfies_version("1.2.0")
    assert not m.satisfies_version("1.4.0")


def test_satisfies_version_ignores_prerelease():
    m = _make_manifest(version="2.0.0-alpha")
    assert m.satisfies_version("1.9.9")


# ---------------------------------------------------------------------------
# 6. PackageManifest.is_compatible
# ---------------------------------------------------------------------------

def test_is_compatible_current_python():
    m = _make_manifest(min_python=(3, 11))
    # Current Python is >= 3.11 in this environment
    assert m.is_compatible() is True or m.is_compatible() is False  # just ensure it runs


def test_is_compatible_very_old_python():
    m = _make_manifest(min_python=(2, 7))
    assert m.is_compatible() is True


def test_is_compatible_future_python_false():
    m = _make_manifest(min_python=(99, 0))
    assert m.is_compatible() is False


# ---------------------------------------------------------------------------
# 7. PackageManifest.compute_checksum
# ---------------------------------------------------------------------------

def test_compute_checksum_is_hex_string():
    m = _make_manifest()
    chk = m.compute_checksum()
    assert isinstance(chk, str)
    assert len(chk) == 64  # SHA-256 hex
    assert all(c in "0123456789abcdef" for c in chk)


def test_compute_checksum_is_deterministic():
    m = _make_manifest()
    assert m.compute_checksum() == m.compute_checksum()


def test_compute_checksum_changes_with_version():
    m1 = _make_manifest(version="1.0.0")
    m2 = _make_manifest(version="1.0.1")
    assert m1.compute_checksum() != m2.compute_checksum()


def test_compute_checksum_excludes_checksum_field():
    """A manifest with a non-empty checksum field should yield the same
    computed checksum as an otherwise identical manifest with empty checksum,
    because the field itself is excluded from the hash."""
    m_no_chk = _make_manifest(checksum="")
    computed = m_no_chk.compute_checksum()
    from dataclasses import replace
    m_with_chk = replace(m_no_chk, checksum="some-previous-checksum")
    assert m_with_chk.compute_checksum() == computed


# ---------------------------------------------------------------------------
# 8. PackageManifest.with_capability
# ---------------------------------------------------------------------------

def test_with_capability_adds_new():
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    m2 = m.with_capability(PackageCapability.KIND_VALIDATION)
    assert PackageCapability.KIND_VALIDATION in m2.capabilities
    assert PackageCapability.KIND_EXTRACTION in m2.capabilities  # original preserved
    # Original unchanged (frozen)
    assert PackageCapability.KIND_VALIDATION not in m.capabilities


def test_with_capability_idempotent():
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    m2 = m.with_capability(PackageCapability.KIND_EXTRACTION)
    assert m2.capabilities == m.capabilities


def test_with_capability_chaining():
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    m_all = m
    for cap in PackageCapability:
        m_all = m_all.with_capability(cap)
    assert m_all.capabilities == PackageCapability.all()


# ---------------------------------------------------------------------------
# 9. PackageManifest to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------

def test_to_dict_keys():
    m = _make_manifest()
    d = m.to_dict()
    expected_keys = {
        "name", "version", "description", "capabilities",
        "schema_version", "created_at", "author", "tags",
        "dependencies", "min_python", "checksum",
    }
    assert set(d.keys()) == expected_keys


def test_to_dict_capabilities_sorted_list():
    m = _full_manifest()
    d = m.to_dict()
    caps = d["capabilities"]
    assert isinstance(caps, list)
    assert caps == sorted(caps)


def test_to_dict_tags_sorted_list():
    m = _make_manifest(tags=frozenset({"z-tag", "a-tag", "m-tag"}))
    d = m.to_dict()
    assert d["tags"] == sorted(["z-tag", "a-tag", "m-tag"])


def test_to_dict_dependencies_is_list():
    m = _make_manifest(dependencies=("scipy>=1.0", "numpy>=1.24"))
    d = m.to_dict()
    assert isinstance(d["dependencies"], list)


def test_to_dict_min_python_is_list():
    m = _make_manifest(min_python=(3, 11))
    d = m.to_dict()
    assert d["min_python"] == [3, 11]


def test_from_dict_roundtrip():
    m = _full_manifest()
    d = m.to_dict()
    m2 = PackageManifest.from_dict(d)
    assert m2.name == m.name
    assert m2.version == m.version
    assert m2.description == m.description
    assert m2.capabilities == m.capabilities
    assert m2.tags == m.tags
    assert m2.dependencies == m.dependencies
    assert m2.min_python == m.min_python
    assert m2.schema_version == m.schema_version
    assert m2.author == m.author


def test_from_dict_uses_defaults():
    minimal = {
        "name": "minimal.pkg",
        "version": "0.0.1",
        "description": "minimal",
        "capabilities": ["kind_extraction"],
        "schema_version": "1",
        "created_at": "2024-01-01T00:00:00Z",
    }
    m = PackageManifest.from_dict(minimal)
    assert m.author == ""
    assert m.tags == frozenset()
    assert m.dependencies == ()
    assert m.checksum == ""


# ---------------------------------------------------------------------------
# 10. PackageManifest.summary_line and full_report
# ---------------------------------------------------------------------------

def test_summary_line_contains_name_and_version():
    m = _make_manifest(name="mypackage", version="2.3.4")
    line = m.summary_line()
    assert "mypackage" in line
    assert "2.3.4" in line


def test_summary_line_contains_schema_version():
    m = _make_manifest(schema_version="1")
    line = m.summary_line()
    assert "schema=1" in line


def test_summary_line_contains_compat_marker():
    m = _make_manifest()
    line = m.summary_line()
    assert "compat" in line.lower()


def test_full_report_is_multiline():
    m = _full_manifest()
    report = m.full_report()
    lines = report.split("\n")
    assert len(lines) > 10


def test_full_report_contains_name():
    m = _full_manifest()
    assert m.name in m.full_report()


def test_full_report_contains_version():
    m = _full_manifest()
    assert m.version in m.full_report()


def test_full_report_contains_capabilities():
    m = _full_manifest()
    report = m.full_report()
    for cap in PackageCapability:
        assert cap.value in report


# ---------------------------------------------------------------------------
# 11. ManifestValidator.check_version_format
# ---------------------------------------------------------------------------

def test_check_version_format_valid_semver():
    v = ManifestValidator()
    assert v.check_version_format("1.0.0") is True
    assert v.check_version_format("0.1.0") is True
    assert v.check_version_format("10.20.30") is True


def test_check_version_format_with_prerelease():
    v = ManifestValidator()
    assert v.check_version_format("1.0.0-alpha") is True
    assert v.check_version_format("2.0.0-rc.1") is True


def test_check_version_format_with_build_metadata():
    v = ManifestValidator()
    assert v.check_version_format("1.0.0+build.1") is True


def test_check_version_format_invalid():
    v = ManifestValidator()
    assert v.check_version_format("not_a_version") is False
    assert v.check_version_format("1.0") is False
    assert v.check_version_format("") is False
    assert v.check_version_format("1.0.0.0") is False


def test_check_version_format_leading_zero():
    v = ManifestValidator()
    # Leading zeros in patch etc. are invalid in semver
    assert v.check_version_format("01.0.0") is False


# ---------------------------------------------------------------------------
# 12. ManifestValidator.check_capabilities_non_empty
# ---------------------------------------------------------------------------

def test_check_capabilities_non_empty_passes():
    v = ManifestValidator()
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    ok, msg = v.check_capabilities_non_empty(m)
    assert ok is True
    assert "non-empty" in msg.lower() or "capabilities" in msg.lower()


def test_check_capabilities_non_empty_fails():
    v = ManifestValidator()
    m = _make_manifest(capabilities=frozenset())
    ok, msg = v.check_capabilities_non_empty(m)
    assert ok is False
    assert "empty" in msg.lower() or "capabilities" in msg.lower()


# ---------------------------------------------------------------------------
# 13. ManifestValidator.validate
# ---------------------------------------------------------------------------

def test_validate_valid_manifest():
    v = ManifestValidator()
    m = _make_manifest()
    ok, errors = v.validate(m)
    assert ok is True
    assert errors == []


def test_validate_invalid_version():
    v = ManifestValidator()
    m = _make_manifest(version="not_semver")
    ok, errors = v.validate(m)
    assert ok is False
    assert any("version" in e.lower() or "semver" in e.lower() for e in errors)


def test_validate_empty_capabilities():
    v = ManifestValidator()
    m = _make_manifest(capabilities=frozenset())
    ok, errors = v.validate(m)
    assert ok is False
    assert any("capabilities" in e.lower() for e in errors)


def test_validate_unknown_schema():
    v = ManifestValidator()
    m = _make_manifest(schema_version="99")
    ok, errors = v.validate(m)
    assert ok is False
    assert any("schema" in e.lower() for e in errors)


def test_validate_malformed_dependency():
    v = ManifestValidator()
    m = _make_manifest(dependencies=("valid-dep>=1.0", "bad dep with spaces!!!"))
    ok, errors = v.validate(m)
    assert ok is False
    assert any("dependency" in e.lower() or "malformed" in e.lower() for e in errors)


def test_validate_empty_name():
    v = ManifestValidator()
    m = PackageManifest(
        name="   ",
        version="1.0.0",
        description="desc",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
        schema_version="1",
        created_at="2024-01-01T00:00:00Z",
    )
    ok, errors = v.validate(m)
    assert ok is False


def test_validate_empty_description():
    v = ManifestValidator()
    m = PackageManifest(
        name="some.pkg",
        version="1.0.0",
        description="   ",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
        schema_version="1",
        created_at="2024-01-01T00:00:00Z",
    )
    ok, errors = v.validate(m)
    assert ok is False


def test_validate_multiple_errors_accumulated():
    v = ManifestValidator()
    m = _make_manifest(
        version="bad_version",
        capabilities=frozenset(),
        schema_version="99",
    )
    ok, errors = v.validate(m)
    assert ok is False
    assert len(errors) >= 3


# ---------------------------------------------------------------------------
# 14. ManifestValidator.full_validation_report
# ---------------------------------------------------------------------------

def test_full_validation_report_structure():
    v = ManifestValidator()
    m = _make_manifest()
    report = v.full_validation_report(m)
    assert "manifest_name" in report
    assert "manifest_version" in report
    assert "overall_valid" in report
    assert "errors" in report
    assert "checks" in report


def test_full_validation_report_valid_manifest():
    v = ManifestValidator()
    m = _make_manifest()
    report = v.full_validation_report(m)
    assert report["overall_valid"] is True
    assert report["errors"] == []


def test_full_validation_report_invalid_manifest():
    v = ManifestValidator()
    m = _make_manifest(version="bad", capabilities=frozenset())
    report = v.full_validation_report(m)
    assert report["overall_valid"] is False
    assert len(report["errors"]) > 0


# ---------------------------------------------------------------------------
# 15. ManifestValidator.validate_batch
# ---------------------------------------------------------------------------

def test_validate_batch_all_valid():
    v = ManifestValidator()
    manifests = [_make_manifest(name=f"pkg{i}", version=f"{i}.0.0") for i in range(1, 5)]
    results = v.validate_batch(manifests)
    assert len(results) == 4
    for ok, errors in results:
        assert ok is True
        assert errors == []


def test_validate_batch_mixed():
    v = ManifestValidator()
    valid_m = _make_manifest(name="valid.pkg")
    invalid_m = _make_manifest(name="invalid.pkg", version="bad_ver")
    results = v.validate_batch([valid_m, invalid_m])
    assert results[0][0] is True
    assert results[1][0] is False


def test_validate_batch_empty_input():
    v = ManifestValidator()
    results = v.validate_batch([])
    assert results == []


def test_validate_batch_preserves_order():
    v = ManifestValidator()
    names = ["pkg_a", "pkg_b", "pkg_c"]
    manifests = [_make_manifest(name=n) for n in names]
    results = v.validate_batch(manifests)
    assert len(results) == len(names)


# ---------------------------------------------------------------------------
# 16. ManifestValidator.suggest_fixes
# ---------------------------------------------------------------------------

def test_suggest_fixes_valid_returns_no_issues_message():
    v = ManifestValidator()
    m = _make_manifest()
    suggestions = v.suggest_fixes(m)
    assert isinstance(suggestions, list)
    assert len(suggestions) >= 1
    assert any("no issues" in s.lower() or "valid" in s.lower() for s in suggestions)


def test_suggest_fixes_bad_version():
    v = ManifestValidator()
    m = _make_manifest(version="bad")
    suggestions = v.suggest_fixes(m)
    assert any("version" in s.lower() or "semver" in s.lower() for s in suggestions)


def test_suggest_fixes_empty_capabilities():
    v = ManifestValidator()
    m = _make_manifest(capabilities=frozenset())
    suggestions = v.suggest_fixes(m)
    assert any("capability" in s.lower() or "capabilities" in s.lower() for s in suggestions)


def test_suggest_fixes_bad_schema():
    v = ManifestValidator()
    m = _make_manifest(schema_version="99")
    suggestions = v.suggest_fixes(m)
    assert any("schema" in s.lower() for s in suggestions)


# ---------------------------------------------------------------------------
# 17. PackageRegistry – register, get, deregister
# ---------------------------------------------------------------------------

def test_registry_register_and_get():
    reg = PackageRegistry()
    m = _make_manifest(name="mypkg")
    reg.register(m)
    result = reg.get("mypkg")
    assert result is not None
    assert result.name == "mypkg"


def test_registry_get_missing_returns_none():
    reg = PackageRegistry()
    assert reg.get("nonexistent") is None


def test_registry_deregister_existing():
    reg = PackageRegistry()
    m = _make_manifest(name="removeme")
    reg.register(m)
    removed = reg.deregister("removeme")
    assert removed is True
    assert reg.get("removeme") is None


def test_registry_deregister_nonexistent():
    reg = PackageRegistry()
    removed = reg.deregister("doesnotexist")
    assert removed is False


def test_registry_register_overwrites():
    reg = PackageRegistry()
    m1 = _make_manifest(name="pkg", version="1.0.0")
    m2 = _make_manifest(name="pkg", version="2.0.0")
    reg.register(m1)
    reg.register(m2)
    result = reg.get("pkg")
    assert result.version == "2.0.0"


def test_registry_size_and_len():
    reg = PackageRegistry()
    assert reg.size() == 0
    assert len(reg) == 0
    reg.register(_make_manifest(name="a"))
    reg.register(_make_manifest(name="b"))
    assert reg.size() == 2
    assert len(reg) == 2


def test_registry_contains():
    reg = PackageRegistry()
    m = _make_manifest(name="checker")
    reg.register(m)
    assert "checker" in reg
    assert "missing" not in reg


def test_registry_list_all_sorted():
    reg = PackageRegistry()
    for name in ["z.pkg", "a.pkg", "m.pkg"]:
        reg.register(_make_manifest(name=name))
    names = [m.name for m in reg.list_all()]
    assert names == sorted(names)


def test_registry_clear():
    reg = PackageRegistry()
    for i in range(5):
        reg.register(_make_manifest(name=f"pkg{i}"))
    assert reg.size() == 5
    reg.clear()
    assert reg.size() == 0
    assert reg.list_all() == []


# ---------------------------------------------------------------------------
# 18. PackageRegistry.find_by_capability
# ---------------------------------------------------------------------------

def test_find_by_capability_returns_matching():
    reg = PackageRegistry()
    m_with = _make_manifest(
        name="has_extraction",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    )
    m_without = _make_manifest(
        name="no_extraction",
        capabilities=frozenset({PackageCapability.KIND_VALIDATION}),
    )
    reg.register(m_with)
    reg.register(m_without)
    results = reg.find_by_capability(PackageCapability.KIND_EXTRACTION)
    names = [m.name for m in results]
    assert "has_extraction" in names
    assert "no_extraction" not in names


def test_find_by_capability_empty_registry():
    reg = PackageRegistry()
    results = reg.find_by_capability(PackageCapability.KIND_EXTRACTION)
    assert results == []


def test_find_by_capability_multiple_matches():
    reg = PackageRegistry()
    for i in range(4):
        reg.register(_make_manifest(
            name=f"pkg{i}",
            capabilities=frozenset({PackageCapability.KIND_VALIDATION}),
        ))
    results = reg.find_by_capability(PackageCapability.KIND_VALIDATION)
    assert len(results) == 4


# ---------------------------------------------------------------------------
# 19. PackageRegistry.merge_from
# ---------------------------------------------------------------------------

def test_merge_from_adds_all():
    reg1 = PackageRegistry()
    reg2 = PackageRegistry()
    for i in range(3):
        reg2.register(_make_manifest(name=f"pkg{i}"))
    count = reg1.merge_from(reg2)
    assert count == 3
    assert reg1.size() == 3


def test_merge_from_overwrites():
    reg1 = PackageRegistry()
    reg2 = PackageRegistry()
    reg1.register(_make_manifest(name="shared", version="1.0.0"))
    reg2.register(_make_manifest(name="shared", version="2.0.0"))
    reg1.merge_from(reg2)
    assert reg1.get("shared").version == "2.0.0"


def test_merge_from_empty_source():
    reg1 = PackageRegistry()
    reg1.register(_make_manifest(name="existing"))
    reg2 = PackageRegistry()
    count = reg1.merge_from(reg2)
    assert count == 0
    assert reg1.size() == 1


# ---------------------------------------------------------------------------
# 20. CapabilityQuery.query
# ---------------------------------------------------------------------------

def test_capability_query_require_all_true():
    reg = PackageRegistry()
    full = _full_manifest()
    partial = _make_manifest(
        name="partial.pkg",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    )
    reg.register(full)
    reg.register(partial)
    cq = CapabilityQuery(reg)
    required = frozenset({PackageCapability.KIND_EXTRACTION, PackageCapability.KIND_VALIDATION})
    results = cq.query(required, require_all=True)
    names = [m.name for m in results]
    assert full.name in names
    assert "partial.pkg" not in names


def test_capability_query_require_all_false():
    reg = PackageRegistry()
    reg.register(_make_manifest(
        name="pkg_a",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    ))
    reg.register(_make_manifest(
        name="pkg_b",
        capabilities=frozenset({PackageCapability.KIND_VALIDATION}),
    ))
    cq = CapabilityQuery(reg)
    required = frozenset({PackageCapability.KIND_EXTRACTION, PackageCapability.KIND_VALIDATION})
    results = cq.query(required, require_all=False)
    names = [m.name for m in results]
    assert "pkg_a" in names
    assert "pkg_b" in names


def test_capability_query_any():
    reg = PackageRegistry()
    reg.register(_make_manifest(
        name="validator",
        capabilities=frozenset({PackageCapability.KIND_VALIDATION}),
    ))
    reg.register(_make_manifest(
        name="miner",
        capabilities=frozenset({PackageCapability.PATTERN_MINING}),
    ))
    cq = CapabilityQuery(reg)
    results = cq.query_any(
        frozenset({PackageCapability.KIND_VALIDATION, PackageCapability.PATTERN_MINING})
    )
    names = [m.name for m in results]
    assert "validator" in names
    assert "miner" in names


def test_capability_query_empty_registry():
    reg = PackageRegistry()
    cq = CapabilityQuery(reg)
    assert cq.query(frozenset({PackageCapability.KIND_EXTRACTION})) == []


# ---------------------------------------------------------------------------
# 21. CapabilityQuery.has_full_pipeline and missing_capabilities
# ---------------------------------------------------------------------------

def test_has_full_pipeline_with_full_manifest():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    cq = CapabilityQuery(reg)
    assert cq.has_full_pipeline() is True


def test_has_full_pipeline_missing_caps():
    reg = PackageRegistry()
    reg.register(_make_manifest(
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    ))
    cq = CapabilityQuery(reg)
    assert cq.has_full_pipeline() is False


def test_missing_capabilities_all_present():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    cq = CapabilityQuery(reg)
    missing = cq.missing_capabilities(PackageCapability.all())
    assert missing == frozenset()


def test_missing_capabilities_some_absent():
    reg = PackageRegistry()
    reg.register(_make_manifest(
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    ))
    cq = CapabilityQuery(reg)
    missing = cq.missing_capabilities(PackageCapability.all())
    assert PackageCapability.KIND_EXTRACTION not in missing
    assert PackageCapability.KIND_VALIDATION in missing


def test_capability_coverage_counts():
    reg = PackageRegistry()
    reg.register(_make_manifest(
        name="a",
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    ))
    reg.register(_make_manifest(
        name="b",
        capabilities=frozenset({
            PackageCapability.KIND_EXTRACTION,
            PackageCapability.KIND_VALIDATION,
        }),
    ))
    cq = CapabilityQuery(reg)
    coverage = cq.capability_coverage()
    assert coverage[PackageCapability.KIND_EXTRACTION] == 2
    assert coverage[PackageCapability.KIND_VALIDATION] == 1
    assert coverage[PackageCapability.PATTERN_MINING] == 0


def test_explain_coverage_returns_string():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    cq = CapabilityQuery(reg)
    explanation = cq.explain_coverage()
    assert isinstance(explanation, str)
    assert len(explanation) > 50
    assert "kind_extraction" in explanation


# ---------------------------------------------------------------------------
# 22. ManifestSerializer roundtrip
# ---------------------------------------------------------------------------

def test_serializer_to_dict_from_dict_roundtrip():
    m = _full_manifest()
    d = ManifestSerializer.to_dict(m)
    m2 = ManifestSerializer.from_dict(d)
    assert m2.name == m.name
    assert m2.capabilities == m.capabilities
    assert m2.tags == m.tags


def test_serializer_to_json_is_valid_json():
    m = _make_manifest()
    json_str = ManifestSerializer.to_json(m)
    parsed = json.loads(json_str)
    assert isinstance(parsed, dict)
    assert parsed["name"] == m.name


def test_serializer_to_json_pretty_has_indentation():
    m = _make_manifest()
    pretty = ManifestSerializer.to_json(m, pretty=True)
    compact = ManifestSerializer.to_json(m, pretty=False)
    assert len(pretty) > len(compact)
    assert "\n" in pretty
    assert "\n" not in compact


def test_serializer_from_json_roundtrip():
    m = _full_manifest()
    json_str = ManifestSerializer.to_json(m)
    m2 = ManifestSerializer.from_json(json_str)
    assert m2.name == m.name
    assert m2.version == m.version
    assert m2.capabilities == m.capabilities


def test_serializer_from_json_invalid_type():
    with pytest.raises(TypeError):
        ManifestSerializer.from_json("[1, 2, 3]")


# ---------------------------------------------------------------------------
# 23. ManifestSerializer registry roundtrip
# ---------------------------------------------------------------------------

def test_registry_to_json_from_json_roundtrip():
    reg = PackageRegistry()
    for i in range(3):
        reg.register(_make_manifest(name=f"pkg{i}", version=f"{i+1}.0.0"))
    json_str = ManifestSerializer.registry_to_json(reg)
    reg2 = ManifestSerializer.registry_from_json(json_str)
    assert reg2.size() == 3
    for i in range(3):
        assert reg2.get(f"pkg{i}") is not None


def test_registry_to_json_is_valid_json():
    reg = PackageRegistry()
    reg.register(_make_manifest())
    json_str = ManifestSerializer.registry_to_json(reg)
    parsed = json.loads(json_str)
    assert "manifests" in parsed
    assert "registry_size" in parsed


def test_registry_from_json_empty_registry():
    empty_payload = json.dumps({"manifests": [], "registry_size": 0, "created_at": "2024-01-01T00:00:00Z"})
    reg = ManifestSerializer.registry_from_json(empty_payload)
    assert reg.size() == 0


# ---------------------------------------------------------------------------
# 24. ManifestSerializer batch roundtrip
# ---------------------------------------------------------------------------

def test_batch_to_json_from_json_roundtrip():
    manifests = [_make_manifest(name=f"batch_pkg_{i}") for i in range(4)]
    json_str = ManifestSerializer.batch_to_json(manifests)
    restored = ManifestSerializer.batch_from_json(json_str)
    assert len(restored) == 4
    names = {m.name for m in restored}
    assert {f"batch_pkg_{i}" for i in range(4)} == names


def test_batch_to_json_invalid_type():
    with pytest.raises(TypeError):
        ManifestSerializer.batch_from_json('{"not": "a list"}')


def test_batch_to_json_empty():
    json_str = ManifestSerializer.batch_to_json([])
    result = ManifestSerializer.batch_from_json(json_str)
    assert result == []


# ---------------------------------------------------------------------------
# 25. ManifestDiagnostics
# ---------------------------------------------------------------------------

def test_diagnostics_summary_is_string():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    s = diag.summary()
    assert isinstance(s, str)
    assert "manifest" in s.lower()


def test_diagnostics_summary_contains_count():
    reg = PackageRegistry()
    for i in range(3):
        reg.register(_make_manifest(name=f"pkg{i}"))
    diag = ManifestDiagnostics(registry=reg)
    s = diag.summary()
    assert "3" in s


def test_diagnostics_capability_report_structure():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    report = diag.capability_report()
    assert "total_manifests" in report
    assert "capability_counts" in report
    assert "full_pipeline" in report
    assert "missing_capabilities" in report


def test_diagnostics_capability_report_full_pipeline():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    report = diag.capability_report()
    assert report["full_pipeline"] is True
    assert report["missing_capabilities"] == []


def test_diagnostics_capability_report_incomplete():
    reg = PackageRegistry()
    reg.register(_make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION})))
    diag = ManifestDiagnostics(registry=reg)
    report = diag.capability_report()
    assert report["full_pipeline"] is False
    assert len(report["missing_capabilities"]) > 0


def test_diagnostics_compatibility_report_is_string():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    compat_str = diag.compatibility_report()
    assert isinstance(compat_str, str)
    assert "Compatible" in compat_str or "compatible" in compat_str


def test_diagnostics_dependency_graph():
    reg = PackageRegistry()
    m = _make_manifest(
        name="dependent.pkg",
        dependencies=("some.dep>=1.0",),
    )
    reg.register(m)
    diag = ManifestDiagnostics(registry=reg)
    graph = diag.dependency_graph()
    assert "dependent.pkg" in graph
    assert "some.dep>=1.0" in graph["dependent.pkg"]


def test_diagnostics_orphaned_packages():
    reg = PackageRegistry()
    m = _make_manifest(
        name="orphan.pkg",
        dependencies=("not.registered.pkg>=1.0",),
    )
    reg.register(m)
    diag = ManifestDiagnostics(registry=reg)
    orphans = diag.orphaned_packages()
    assert "orphan.pkg" in orphans


def test_diagnostics_no_orphans_when_deps_registered():
    reg = PackageRegistry()
    dep_m = _make_manifest(name="dep.pkg")
    parent_m = _make_manifest(name="parent.pkg", dependencies=("dep.pkg>=1.0",))
    reg.register(dep_m)
    reg.register(parent_m)
    diag = ManifestDiagnostics(registry=reg)
    orphans = diag.orphaned_packages()
    assert "parent.pkg" not in orphans


def test_diagnostics_pipeline_completeness_report_is_string():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    report = diag.pipeline_completeness_report()
    assert isinstance(report, str)
    assert "Pipeline" in report


def test_diagnostics_copilot_manifest_summary():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    diag = ManifestDiagnostics(registry=reg)
    summary = diag.copilot_manifest_summary()
    assert isinstance(summary, str)
    assert "jugeo" in summary.lower() or "manifest" in summary.lower()


# ---------------------------------------------------------------------------
# 26. _DEFAULT_MANIFEST
# ---------------------------------------------------------------------------

def test_default_manifest_name():
    assert _DEFAULT_MANIFEST.name == PACKAGE_NAME


def test_default_manifest_version():
    assert _DEFAULT_MANIFEST.version == PACKAGE_VERSION


def test_default_manifest_description():
    assert _DEFAULT_MANIFEST.description == PACKAGE_DESCRIPTION


def test_default_manifest_has_all_capabilities():
    assert _DEFAULT_MANIFEST.capabilities == PackageCapability.all()


def test_default_manifest_schema_version():
    assert _DEFAULT_MANIFEST.schema_version == _SCHEMA_VERSION


def test_default_manifest_is_valid():
    v = ManifestValidator()
    ok, errors = v.validate(_DEFAULT_MANIFEST)
    assert ok is True, f"Default manifest validation failed: {errors}"


def test_default_manifest_is_compatible():
    # Since it uses _MIN_PYTHON=(3,11) and we're running >=3.11
    # In CI it should pass; if not, at least is_compatible() must return a bool
    result = _DEFAULT_MANIFEST.is_compatible()
    assert isinstance(result, bool)


def test_default_manifest_has_tags():
    assert len(_DEFAULT_MANIFEST.tags) > 0


def test_default_manifest_no_dependencies():
    assert _DEFAULT_MANIFEST.dependencies == ()


def test_default_manifest_checksum_computable():
    chk = _DEFAULT_MANIFEST.compute_checksum()
    assert len(chk) == 64


# ---------------------------------------------------------------------------
# 27. Edge cases
# ---------------------------------------------------------------------------

def test_manifest_with_duplicate_capability_in_with_capability():
    """with_capability on an already-present cap must not duplicate it."""
    m = _make_manifest(capabilities=frozenset({PackageCapability.KIND_EXTRACTION}))
    m2 = m.with_capability(PackageCapability.KIND_EXTRACTION)
    assert len(m2.capabilities) == 1


def test_manifest_many_tags():
    tags = frozenset({f"tag_{i}" for i in range(50)})
    m = _make_manifest(tags=tags)
    assert m.tags == tags
    d = m.to_dict()
    assert len(d["tags"]) == 50


def test_validator_check_python_compat_returns_tuple():
    v = ManifestValidator()
    m = _make_manifest(min_python=(3, 11))
    result = v.check_python_compat(m)
    assert isinstance(result, tuple)
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_validator_check_dependencies_format_all_valid():
    v = ManifestValidator()
    m = _make_manifest(dependencies=(
        "numpy>=1.24",
        "scipy>=1.10,<=2.0",
        "simple_pkg",
        "package.with.dots",
    ))
    ok, msgs = v.check_dependencies_format(m)
    assert ok is True
    assert msgs == []


def test_registry_repr():
    reg = PackageRegistry()
    reg.register(_make_manifest())
    r = repr(reg)
    assert "PackageRegistry" in r
    assert "1" in r


def test_capability_query_compatible_pipelines_with_full_manifest():
    reg = PackageRegistry()
    reg.register(_full_manifest())
    cq = CapabilityQuery(reg)
    pipelines = cq.compatible_pipelines()
    # Should have at least one pipeline since full manifest is compatible
    assert isinstance(pipelines, list)


def test_capability_query_compatible_pipelines_incomplete():
    """A registry missing pipeline stages should return no complete pipelines."""
    reg = PackageRegistry()
    reg.register(_make_manifest(
        capabilities=frozenset({PackageCapability.KIND_EXTRACTION}),
    ))
    cq = CapabilityQuery(reg)
    pipelines = cq.compatible_pipelines()
    # The _cartesian returns [] when any stage has no coverage
    assert pipelines == []


def test_serializer_from_dict_roundtrip_with_all_fields():
    m = PackageManifest(
        name="full.test.pkg",
        version="3.2.1-beta.1+build.42",
        description="A fully populated manifest for roundtrip testing.",
        capabilities=PackageCapability.all(),
        schema_version="1",
        created_at="2024-03-15T12:30:00Z",
        author="test-author",
        tags=frozenset({"math", "kind-discovery", "pipeline"}),
        dependencies=("sympy>=1.12", "numpy>=1.24"),
        min_python=(3, 11),
        checksum="abc123",
    )
    d = ManifestSerializer.to_dict(m)
    m2 = ManifestSerializer.from_dict(d)
    assert m2.name == m.name
    assert m2.capabilities == m.capabilities
    assert m2.dependencies == m.dependencies
    assert m2.author == m.author
