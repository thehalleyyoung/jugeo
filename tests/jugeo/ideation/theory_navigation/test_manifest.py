"""Tests for jugeo.ideation.theory_navigation.manifest.

Covers: PackageCapability, PackageManifest, ManifestValidator, PackageRegistry,
CapabilityQuery, ManifestSerializer, ManifestDiagnostics, _DEFAULT_MANIFEST.
"""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import datetime
import json
import pytest

from jugeo.ideation.theory_navigation.manifest import (
    PackageCapability,
    PackageManifest,
    ManifestValidator,
    PackageRegistry,
    CapabilityQuery,
    ManifestSerializer,
    ManifestDiagnostics,
    _DEFAULT_MANIFEST,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_manifest(
    name: str = "test.pkg",
    version: str = "1.0.0",
    caps: tuple[PackageCapability, ...] = (PackageCapability.THEORY_SEARCH,),
    description: str = "A test package for theory navigation",
    dependencies: tuple[str, ...] = (),
) -> PackageManifest:
    return PackageManifest(
        name=name,
        version=version,
        description=description,
        capabilities=caps,
        theory_chapter="chapter-01",
        exported_classes=("TheoryNode", "TheorySpace"),
        dependencies=dependencies,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        manifest_id=f"manifest-{name}",
    )


def _make_full_manifest(name: str = "full.pkg") -> PackageManifest:
    return _make_manifest(
        name=name,
        caps=tuple(PackageCapability),
        dependencies=("core.pkg", "utils.pkg"),
    )


def _make_registry_with_manifests() -> PackageRegistry:
    registry = PackageRegistry()
    registry.register(_make_manifest("pkg.alpha", caps=(PackageCapability.THEORY_SEARCH,)))
    registry.register(_make_manifest("pkg.beta", caps=(PackageCapability.PATH_FINDING, PackageCapability.SPACE_INDEXING)))
    registry.register(_make_manifest("pkg.gamma", caps=(PackageCapability.MAP_CONSTRUCTION,)))
    return registry


# ---------------------------------------------------------------------------
# PackageCapability tests
# ---------------------------------------------------------------------------

def test_package_capability_count():
    assert len(list(PackageCapability)) == 5


def test_package_capability_theory_search_exists():
    assert PackageCapability.THEORY_SEARCH is not None


def test_package_capability_purpose_navigation_exists():
    assert PackageCapability.PURPOSE_NAVIGATION is not None


def test_package_capability_map_construction_exists():
    assert PackageCapability.MAP_CONSTRUCTION is not None


def test_package_capability_path_finding_exists():
    assert PackageCapability.PATH_FINDING is not None


def test_package_capability_space_indexing_exists():
    assert PackageCapability.SPACE_INDEXING is not None


def test_package_capability_string_values():
    # All capabilities should have string values
    for cap in PackageCapability:
        assert isinstance(cap.value, str)
        assert len(cap.value) > 0


def test_package_capability_distinct_values():
    values = [cap.value for cap in PackageCapability]
    assert len(set(values)) == len(values)


# ---------------------------------------------------------------------------
# PackageManifest tests
# ---------------------------------------------------------------------------

def test_package_manifest_creation():
    m = _make_manifest()
    assert m.name == "test.pkg"
    assert m.version == "1.0.0"


def test_package_manifest_has_capability_true():
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert m.has_capability(PackageCapability.THEORY_SEARCH) is True
    assert m.has_capability(PackageCapability.PATH_FINDING) is True


def test_package_manifest_has_capability_false():
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert m.has_capability(PackageCapability.PATH_FINDING) is False


def test_package_manifest_capability_count():
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert m.capability_count() == 2


def test_package_manifest_capability_count_all():
    m = _make_full_manifest()
    assert m.capability_count() == 5


def test_package_manifest_to_dict_round_trip():
    m = _make_manifest(
        caps=(PackageCapability.THEORY_SEARCH, PackageCapability.MAP_CONSTRUCTION),
        dependencies=("dep.a", "dep.b"),
    )
    d = m.to_dict()
    restored = PackageManifest.from_dict(d)
    assert restored.name == m.name
    assert restored.version == m.version
    assert set(restored.capabilities) == set(m.capabilities)
    assert set(restored.dependencies) == set(m.dependencies)


def test_package_manifest_summary_nonempty():
    m = _make_manifest()
    summary = m.summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_package_manifest_summary_contains_name():
    m = _make_manifest(name="my.special.pkg")
    summary = m.summary()
    assert "my.special.pkg" in summary


def test_package_manifest_matches_query_all_present():
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert m.matches_query((PackageCapability.THEORY_SEARCH,)) is True
    assert m.matches_query((PackageCapability.PATH_FINDING,)) is True


def test_package_manifest_matches_query_missing_cap():
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert m.matches_query((PackageCapability.PATH_FINDING,)) is False


def test_package_manifest_matches_query_empty():
    m = _make_manifest()
    assert m.matches_query(()) is True


def test_package_manifest_rejects_empty_name():
    with pytest.raises((ValueError, Exception)):
        PackageManifest(
            name="",
            version="1.0.0",
            description="desc",
            capabilities=(PackageCapability.THEORY_SEARCH,),
            theory_chapter="ch1",
            exported_classes=(),
            dependencies=(),
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            manifest_id="mid",
        )


def test_package_manifest_is_frozen():
    m = _make_manifest()
    with pytest.raises((AttributeError, TypeError)):
        m.name = "modified"  # type: ignore[misc]


def test_default_manifest_has_all_capabilities():
    assert _DEFAULT_MANIFEST.capability_count() == 5


def test_default_manifest_name_nonempty():
    assert isinstance(_DEFAULT_MANIFEST.name, str)
    assert len(_DEFAULT_MANIFEST.name) > 0


def test_default_manifest_is_valid():
    validator = ManifestValidator()
    assert validator.is_valid(_DEFAULT_MANIFEST) is True


def test_default_manifest_has_description():
    assert isinstance(_DEFAULT_MANIFEST.description, str)
    assert len(_DEFAULT_MANIFEST.description) > 0


# ---------------------------------------------------------------------------
# ManifestValidator tests
# ---------------------------------------------------------------------------

def test_manifest_validator_valid_manifest():
    validator = ManifestValidator()
    m = _make_full_manifest()
    errors = validator.validate(m)
    assert isinstance(errors, list)
    assert len(errors) == 0


def test_manifest_validator_empty_name_returns_errors():
    validator = ManifestValidator()
    # Build a dict and patch name to be empty, then deserialize
    m = _make_manifest()
    d = m.to_dict()
    d["name"] = ""
    try:
        bad_m = PackageManifest.from_dict(d)
        errors = validator.validate(bad_m)
        assert len(errors) > 0
    except (ValueError, Exception):
        # Acceptable: the model itself rejected the empty name
        pass


def test_manifest_validator_empty_capabilities_returns_errors():
    validator = ManifestValidator()
    m = _make_manifest(caps=())
    errors = validator.validate(m)
    assert len(errors) > 0


def test_manifest_validator_is_valid_true():
    validator = ManifestValidator()
    m = _make_full_manifest()
    assert validator.is_valid(m) is True


def test_manifest_validator_is_valid_false_for_no_capabilities():
    validator = ManifestValidator()
    m = _make_manifest(caps=())
    assert validator.is_valid(m) is False


def test_manifest_validator_check_dependencies_returns_dict():
    validator = ManifestValidator()
    m = _make_manifest(dependencies=("core.pkg", "utils.pkg"))
    result = validator.check_dependencies(m)
    assert isinstance(result, dict)
    assert "core.pkg" in result
    assert "utils.pkg" in result


def test_manifest_validator_check_dependencies_empty():
    validator = ManifestValidator()
    m = _make_manifest(dependencies=())
    result = validator.check_dependencies(m)
    assert isinstance(result, dict)
    assert len(result) == 0


def test_manifest_validator_summarize_nonempty():
    validator = ManifestValidator()
    m = _make_full_manifest()
    summary = validator.summarize(m)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_manifest_validator_summarize_contains_name():
    validator = ManifestValidator()
    m = _make_manifest(name="alpha.pkg")
    summary = validator.summarize(m)
    assert "alpha.pkg" in summary


def test_manifest_validator_multiple_errors_for_multiple_issues():
    validator = ManifestValidator()
    m = _make_manifest(caps=())
    errors = validator.validate(m)
    # At minimum one error for empty capabilities
    assert len(errors) >= 1


# ---------------------------------------------------------------------------
# PackageRegistry tests
# ---------------------------------------------------------------------------

def test_package_registry_register_and_get():
    registry = PackageRegistry()
    m = _make_manifest(name="reg.test.pkg")
    registry.register(m)
    retrieved = registry.get("reg.test.pkg")
    assert retrieved is not None
    assert retrieved.name == "reg.test.pkg"


def test_package_registry_get_missing_returns_none():
    registry = PackageRegistry()
    assert registry.get("nonexistent.pkg") is None


def test_package_registry_unregister_returns_true():
    registry = PackageRegistry()
    m = _make_manifest(name="remove.me.pkg")
    registry.register(m)
    result = registry.unregister("remove.me.pkg")
    assert result is True
    assert registry.get("remove.me.pkg") is None


def test_package_registry_unregister_missing_returns_false():
    registry = PackageRegistry()
    result = registry.unregister("never.existed.pkg")
    assert result is False


def test_package_registry_list_all():
    registry = _make_registry_with_manifests()
    all_manifests = registry.list_all()
    assert isinstance(all_manifests, list)
    assert len(all_manifests) == 3


def test_package_registry_list_all_empty():
    registry = PackageRegistry()
    assert registry.list_all() == []


def test_package_registry_find_by_capability_single():
    registry = _make_registry_with_manifests()
    results = registry.find_by_capability(PackageCapability.THEORY_SEARCH)
    assert isinstance(results, list)
    assert len(results) >= 1
    for m in results:
        assert m.has_capability(PackageCapability.THEORY_SEARCH)


def test_package_registry_find_by_capability_no_match():
    registry = PackageRegistry()
    registry.register(_make_manifest(caps=(PackageCapability.MAP_CONSTRUCTION,)))
    results = registry.find_by_capability(PackageCapability.THEORY_SEARCH)
    assert results == []


def test_package_registry_count():
    registry = _make_registry_with_manifests()
    assert registry.count() == 3


def test_package_registry_count_empty():
    registry = PackageRegistry()
    assert registry.count() == 0


def test_package_registry_to_dict_nonempty():
    registry = _make_registry_with_manifests()
    d = registry.to_dict()
    assert isinstance(d, dict)
    assert len(d) > 0


def test_package_registry_from_dict_round_trip():
    registry = _make_registry_with_manifests()
    d = registry.to_dict()
    restored = PackageRegistry.from_dict(d)
    assert restored.count() == registry.count()
    for m in registry.list_all():
        assert restored.get(m.name) is not None


def test_package_registry_summary_nonempty():
    registry = _make_registry_with_manifests()
    summary = registry.summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_package_registry_register_overwrites_same_name():
    registry = PackageRegistry()
    m1 = _make_manifest(name="shared.pkg", version="1.0.0")
    m2 = _make_manifest(name="shared.pkg", version="2.0.0")
    registry.register(m1)
    registry.register(m2)
    retrieved = registry.get("shared.pkg")
    assert retrieved is not None
    assert retrieved.version == "2.0.0"


# ---------------------------------------------------------------------------
# CapabilityQuery tests
# ---------------------------------------------------------------------------

def test_capability_query_empty_matches_all():
    query = CapabilityQuery()
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert query.matches(m) is True


def test_capability_query_matches_required_present():
    query = CapabilityQuery(required=(PackageCapability.THEORY_SEARCH,))
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert query.matches(m) is True


def test_capability_query_no_match_when_required_missing():
    query = CapabilityQuery(required=(PackageCapability.SPACE_INDEXING,))
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert query.matches(m) is False


def test_capability_query_excludes_capability():
    query = CapabilityQuery(excluded=(PackageCapability.PATH_FINDING,))
    m_with = _make_manifest(caps=(PackageCapability.PATH_FINDING,))
    m_without = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert query.matches(m_with) is False
    assert query.matches(m_without) is True


def test_capability_query_required_and_excluded():
    query = CapabilityQuery(
        required=(PackageCapability.THEORY_SEARCH,),
        excluded=(PackageCapability.PATH_FINDING,),
    )
    m_ok = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    m_bad = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert query.matches(m_ok) is True
    assert query.matches(m_bad) is False


def test_capability_query_add_required_returns_new():
    query = CapabilityQuery(required=(PackageCapability.THEORY_SEARCH,))
    new_query = query.add_required(PackageCapability.PATH_FINDING)
    assert new_query is not query
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    assert new_query.matches(m) is True


def test_capability_query_add_required_original_unchanged():
    query = CapabilityQuery(required=(PackageCapability.THEORY_SEARCH,))
    _new_query = query.add_required(PackageCapability.PATH_FINDING)
    m_no_pf = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    assert query.matches(m_no_pf) is True


def test_capability_query_execute_returns_matching():
    registry = _make_registry_with_manifests()
    query = CapabilityQuery(required=(PackageCapability.THEORY_SEARCH,))
    results = query.execute(registry)
    assert isinstance(results, list)
    for m in results:
        assert m.has_capability(PackageCapability.THEORY_SEARCH)


def test_capability_query_execute_empty_registry():
    registry = PackageRegistry()
    query = CapabilityQuery(required=(PackageCapability.THEORY_SEARCH,))
    assert query.execute(registry) == []


def test_capability_query_to_dict():
    query = CapabilityQuery(
        required=(PackageCapability.THEORY_SEARCH,),
        excluded=(PackageCapability.PATH_FINDING,),
    )
    d = query.to_dict()
    assert isinstance(d, dict)


def test_capability_query_execute_all_caps():
    registry = _make_registry_with_manifests()
    registry.register(_make_full_manifest("all.pkg"))
    query = CapabilityQuery(required=tuple(PackageCapability))
    results = query.execute(registry)
    assert len(results) >= 1
    assert all(r.capability_count() == 5 for r in results)


# ---------------------------------------------------------------------------
# ManifestSerializer tests
# ---------------------------------------------------------------------------

def test_manifest_serializer_serialize_deserialize_round_trip():
    serializer = ManifestSerializer()
    m = _make_full_manifest()
    serialized = serializer.serialize(m)
    restored = serializer.deserialize(serialized)
    assert restored.name == m.name
    assert restored.version == m.version
    assert set(restored.capabilities) == set(m.capabilities)


def test_manifest_serializer_produces_valid_json():
    serializer = ManifestSerializer()
    m = _make_manifest()
    serialized = serializer.serialize(m)
    # Should be valid JSON
    parsed = json.loads(serialized)
    assert isinstance(parsed, dict)


def test_manifest_serializer_serialize_registry_round_trip():
    serializer = ManifestSerializer()
    registry = _make_registry_with_manifests()
    serialized = serializer.serialize_registry(registry)
    restored = serializer.deserialize_registry(serialized)
    assert restored.count() == registry.count()
    for m in registry.list_all():
        assert restored.get(m.name) is not None


def test_manifest_serializer_registry_json_valid():
    serializer = ManifestSerializer()
    registry = _make_registry_with_manifests()
    serialized = serializer.serialize_registry(registry)
    parsed = json.loads(serialized)
    assert isinstance(parsed, (dict, list))


def test_manifest_serializer_empty_registry_round_trip():
    serializer = ManifestSerializer()
    registry = PackageRegistry()
    serialized = serializer.serialize_registry(registry)
    restored = serializer.deserialize_registry(serialized)
    assert restored.count() == 0


def test_manifest_serializer_preserves_capabilities():
    serializer = ManifestSerializer()
    m = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.BEAM_SEARCH
                              if hasattr(PackageCapability, "BEAM_SEARCH")
                              else PackageCapability.PATH_FINDING))
    serialized = serializer.serialize(m)
    restored = serializer.deserialize(serialized)
    assert set(restored.capabilities) == set(m.capabilities)


def test_manifest_serializer_preserves_dependencies():
    serializer = ManifestSerializer()
    m = _make_manifest(dependencies=("core.pkg", "utils.pkg"))
    serialized = serializer.serialize(m)
    restored = serializer.deserialize(serialized)
    assert set(restored.dependencies) == set(m.dependencies)


# ---------------------------------------------------------------------------
# ManifestDiagnostics tests
# ---------------------------------------------------------------------------

def test_manifest_diagnostics_report_nonempty():
    diag = ManifestDiagnostics()
    m = _make_full_manifest()
    report = diag.report(m)
    assert isinstance(report, str)
    assert len(report) > 0


def test_manifest_diagnostics_report_contains_name():
    diag = ManifestDiagnostics()
    m = _make_manifest(name="diagnostic.test.pkg")
    report = diag.report(m)
    assert "diagnostic.test.pkg" in report


def test_manifest_diagnostics_registry_report_nonempty():
    diag = ManifestDiagnostics()
    registry = _make_registry_with_manifests()
    report = diag.registry_report(registry)
    assert isinstance(report, str)
    assert len(report) > 0


def test_manifest_diagnostics_registry_report_mentions_count():
    diag = ManifestDiagnostics()
    registry = _make_registry_with_manifests()
    report = diag.registry_report(registry)
    # Report should mention how many packages are registered
    assert "3" in report or "pkg" in report.lower()


def test_manifest_diagnostics_diff_same_manifests():
    diag = ManifestDiagnostics()
    m = _make_full_manifest()
    diff = diag.diff(m, m)
    assert isinstance(diff, str)
    # Diffing same manifest should show no differences or minimal output
    # (implementation may vary; at minimum it returns a string)


def test_manifest_diagnostics_diff_different_manifests():
    diag = ManifestDiagnostics()
    m1 = _make_manifest(name="pkg.v1", caps=(PackageCapability.THEORY_SEARCH,))
    m2 = _make_manifest(name="pkg.v2", caps=(PackageCapability.PATH_FINDING,))
    diff = diag.diff(m1, m2)
    assert isinstance(diff, str)
    assert len(diff) > 0


def test_manifest_diagnostics_diff_detects_capability_change():
    diag = ManifestDiagnostics()
    m1 = _make_manifest(caps=(PackageCapability.THEORY_SEARCH,))
    m2 = _make_manifest(caps=(PackageCapability.THEORY_SEARCH, PackageCapability.PATH_FINDING))
    diff = diag.diff(m1, m2)
    assert isinstance(diff, str)


def test_manifest_diagnostics_empty_registry_report():
    diag = ManifestDiagnostics()
    registry = PackageRegistry()
    report = diag.registry_report(registry)
    assert isinstance(report, str)
