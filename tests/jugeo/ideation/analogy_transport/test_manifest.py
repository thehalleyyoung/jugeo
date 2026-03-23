"""Tests for jugeo.ideation.analogy_transport.manifest (theory2.tex Ch60).

Covers PackageCapability, PackageManifest, ManifestValidator, PackageRegistry,
CapabilityQuery, ManifestSerializer, ManifestDiagnostics, and the module-level
_DEFAULT_MANIFEST constant.

Run with::

    pytest tests/jugeo/ideation/analogy_transport/test_manifest.py -v
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
import pytest

from jugeo.ideation.analogy_transport.manifest import (
    PackageCapability,
    PackageManifest,
    ManifestValidator,
    PackageRegistry,
    CapabilityQuery,
    ManifestSerializer,
    ManifestDiagnostics,
    _DEFAULT_MANIFEST,
    PACKAGE_NAME,
    PACKAGE_VERSION,
    THEORY_CHAPTER,
    MIN_FAITHFULNESS,
    DEFAULT_FAITHFULNESS_THRESHOLD,
    MAX_CORRESPONDENCES,
    _clamp,
    _now_iso,
    _tokenize,
    _jaccard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def minimal_manifest() -> PackageManifest:
    """Return the smallest valid PackageManifest for use in tests."""
    return PackageManifest(
        name="jugeo.test_pkg",
        version="1.0.0",
        capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        theory_chapter="Ch1",
        description="A minimal test manifest.",
        author="tester",
        dependencies=(),
        created_at=_now_iso(),
    )


@pytest.fixture()
def full_manifest() -> PackageManifest:
    """Return a manifest with all capabilities and several dependencies."""
    return PackageManifest(
        name="jugeo.full_pkg",
        version="2.3.4",
        capabilities=frozenset(PackageCapability),
        theory_chapter="Ch60",
        description="Full-featured test manifest with all capabilities.",
        author="jugeo-team",
        dependencies=(
            "jugeo.ideation.ideas",
            "jugeo.evidence.trust",
            "jugeo.ideation.novelty",
        ),
        created_at=_now_iso(),
    )


@pytest.fixture()
def validator() -> ManifestValidator:
    return ManifestValidator()


@pytest.fixture()
def serializer() -> ManifestSerializer:
    return ManifestSerializer()


@pytest.fixture()
def diagnostics() -> ManifestDiagnostics:
    return ManifestDiagnostics()


@pytest.fixture()
def registry_with_manifests(minimal_manifest, full_manifest) -> PackageRegistry:
    """Return a registry pre-loaded with two manifests."""
    r = PackageRegistry()
    r.register(minimal_manifest)
    r.register(full_manifest)
    return r


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants():
    """Module-level constants must have the exact documented values."""
    assert PACKAGE_NAME == "jugeo.ideation.analogy_transport"
    assert PACKAGE_VERSION == "0.1.0"
    assert THEORY_CHAPTER == "Ch60"
    assert MIN_FAITHFULNESS == 0.1
    assert DEFAULT_FAITHFULNESS_THRESHOLD == 0.7
    assert MAX_CORRESPONDENCES == 512


def test_min_faithfulness_in_unit_interval():
    """MIN_FAITHFULNESS must be a float in [0, 1]."""
    assert isinstance(MIN_FAITHFULNESS, float)
    assert 0.0 <= MIN_FAITHFULNESS <= 1.0


def test_default_faithfulness_threshold_greater_than_min():
    """DEFAULT_FAITHFULNESS_THRESHOLD must exceed MIN_FAITHFULNESS."""
    assert DEFAULT_FAITHFULNESS_THRESHOLD > MIN_FAITHFULNESS


def test_max_correspondences_positive_int():
    """MAX_CORRESPONDENCES must be a positive integer."""
    assert isinstance(MAX_CORRESPONDENCES, int)
    assert MAX_CORRESPONDENCES > 0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_clamp_within_range():
    assert _clamp(0.5) == 0.5


def test_clamp_below_zero():
    assert _clamp(-1.0) == 0.0


def test_clamp_above_one():
    assert _clamp(2.0) == 1.0


def test_clamp_custom_bounds():
    assert _clamp(5.0, 2.0, 10.0) == 5.0
    assert _clamp(1.0, 2.0, 10.0) == 2.0
    assert _clamp(11.0, 2.0, 10.0) == 10.0


def test_now_iso_returns_string():
    ts = _now_iso()
    assert isinstance(ts, str)
    assert "T" in ts  # ISO-8601 contains a 'T' separator


def test_tokenize_basic():
    tokens = _tokenize("Hello World")
    assert "hello" in tokens
    assert "world" in tokens


def test_tokenize_filters_single_chars():
    tokens = _tokenize("a b c word")
    assert "a" not in tokens
    assert "b" not in tokens
    assert "word" in tokens


def test_jaccard_identical():
    s = frozenset({"foo", "bar"})
    assert _jaccard(s, s) == 1.0


def test_jaccard_disjoint():
    a = frozenset({"foo"})
    b = frozenset({"bar"})
    assert _jaccard(a, b) == 0.0


def test_jaccard_both_empty():
    assert _jaccard(frozenset(), frozenset()) == 1.0


def test_jaccard_partial_overlap():
    a = frozenset({"a", "b", "c"})
    b = frozenset({"b", "c", "d"})
    # intersection = {b, c}, union = {a, b, c, d}
    assert abs(_jaccard(a, b) - 2 / 4) < 1e-9


# ---------------------------------------------------------------------------
# PackageCapability
# ---------------------------------------------------------------------------


def test_package_capability_values():
    """All five capability members must exist with correct values."""
    assert PackageCapability.ANALOGY_CONSTRUCTION.value == "analogy_construction"
    assert PackageCapability.STRUCTURE_PRESERVATION.value == "structure_preservation"
    assert PackageCapability.PURPOSE_PRESERVATION.value == "purpose_preservation"
    assert PackageCapability.TRANSPORT_VERIFICATION.value == "transport_verification"
    assert PackageCapability.BRIDGE_FINDING.value == "bridge_finding"


def test_package_capability_count():
    """There must be exactly five capability members."""
    assert len(list(PackageCapability)) == 5


def test_package_capability_label():
    """label() must return a title-cased human-friendly string."""
    label = PackageCapability.ANALOGY_CONSTRUCTION.label()
    assert "Analogy" in label
    assert "Construction" in label


def test_package_capability_short_code():
    """short_code() must be uppercase initials of the member name."""
    assert PackageCapability.ANALOGY_CONSTRUCTION.short_code() == "AC"
    assert PackageCapability.STRUCTURE_PRESERVATION.short_code() == "SP"
    assert PackageCapability.BRIDGE_FINDING.short_code() == "BF"


def test_package_capability_description_non_empty():
    """Every capability must return a non-empty description."""
    for cap in PackageCapability:
        desc = cap.description()
        assert isinstance(desc, str)
        assert len(desc) > 5, f"{cap.value!r} description is too short"


def test_package_capability_is_str_subclass():
    """PackageCapability inherits from str so members are usable as strings."""
    cap = PackageCapability.ANALOGY_CONSTRUCTION
    assert isinstance(cap, str)
    assert cap == "analogy_construction"


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


def test_package_manifest_creation(minimal_manifest):
    """A minimal manifest must be constructible and have correct fields."""
    m = minimal_manifest
    assert m.name == "jugeo.test_pkg"
    assert m.version == "1.0.0"
    assert m.theory_chapter == "Ch1"
    assert m.author == "tester"
    assert isinstance(m.capabilities, frozenset)
    assert isinstance(m.dependencies, tuple)
    assert isinstance(m.created_at, str)


def test_package_manifest_is_frozen(minimal_manifest):
    """Frozen dataclass must raise FrozenInstanceError on attribute assignment."""
    with pytest.raises(Exception):
        minimal_manifest.name = "new_name"  # type: ignore[misc]


def test_package_manifest_has_capability_true(minimal_manifest):
    """has_capability must return True for a present capability."""
    assert minimal_manifest.has_capability(PackageCapability.ANALOGY_CONSTRUCTION)


def test_package_manifest_has_capability_false(minimal_manifest):
    """has_capability must return False for an absent capability."""
    assert not minimal_manifest.has_capability(PackageCapability.BRIDGE_FINDING)


def test_package_manifest_has_capability_type_error(minimal_manifest):
    """has_capability must raise TypeError for non-PackageCapability input."""
    with pytest.raises(TypeError):
        minimal_manifest.has_capability("analogy_construction")  # type: ignore[arg-type]


def test_package_manifest_capability_names_sorted(full_manifest):
    """capability_names() must return a sorted tuple of value strings."""
    names = full_manifest.capability_names()
    assert isinstance(names, tuple)
    assert names == tuple(sorted(names))
    assert len(names) == 5


def test_package_manifest_capability_names_single(minimal_manifest):
    """capability_names() on a single-cap manifest returns a one-element tuple."""
    names = minimal_manifest.capability_names()
    assert names == ("analogy_construction",)


def test_package_manifest_to_dict_keys(minimal_manifest):
    """to_dict() must return a dict with all expected keys."""
    d = minimal_manifest.to_dict()
    expected_keys = {
        "name", "version", "capabilities", "theory_chapter",
        "description", "author", "dependencies", "created_at",
    }
    assert expected_keys == set(d.keys())


def test_package_manifest_to_dict_types(minimal_manifest):
    """to_dict() must serialize capabilities as a list and deps as a list."""
    d = minimal_manifest.to_dict()
    assert isinstance(d["capabilities"], list)
    assert isinstance(d["dependencies"], list)
    assert isinstance(d["name"], str)
    assert isinstance(d["version"], str)


def test_package_manifest_to_dict_capabilities_are_strings(full_manifest):
    """to_dict() capabilities list must contain plain strings."""
    d = full_manifest.to_dict()
    for cap_str in d["capabilities"]:
        assert isinstance(cap_str, str)


def test_package_manifest_summary_non_empty(minimal_manifest, full_manifest):
    """summary() must return a non-empty string for any valid manifest."""
    for m in (minimal_manifest, full_manifest):
        s = m.summary()
        assert isinstance(s, str)
        assert len(s) > 0


def test_package_manifest_summary_contains_name(minimal_manifest):
    """summary() must include the package name."""
    assert minimal_manifest.name in minimal_manifest.summary()


def test_package_manifest_summary_contains_version(minimal_manifest):
    """summary() must include the version string."""
    assert minimal_manifest.version in minimal_manifest.summary()


def test_package_manifest_repr(minimal_manifest):
    """repr() must include the name and version."""
    r = repr(minimal_manifest)
    assert "jugeo.test_pkg" in r
    assert "1.0.0" in r


def test_package_manifest_str_equals_summary(minimal_manifest):
    """str() must equal summary()."""
    assert str(minimal_manifest) == minimal_manifest.summary()


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


def test_manifest_validator_valid(validator, minimal_manifest):
    """A properly constructed manifest must produce no errors."""
    errors = validator.validate(minimal_manifest)
    assert errors == []


def test_manifest_validator_is_valid_true(validator, minimal_manifest):
    """is_valid must return True for a valid manifest."""
    assert validator.is_valid(minimal_manifest) is True


def test_manifest_validator_empty_name(validator, minimal_manifest):
    """An empty name must produce a validation error."""
    bad = PackageManifest(
        name="",
        version=minimal_manifest.version,
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    errors = validator.validate(bad)
    assert any("name" in e.lower() for e in errors)


def test_manifest_validator_name_missing_jugeo_prefix(validator, minimal_manifest):
    """A name that does not start with 'jugeo.' must produce an error."""
    bad = PackageManifest(
        name="mylib.something",
        version=minimal_manifest.version,
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    errors = validator.validate(bad)
    assert any("jugeo." in e for e in errors)


def test_manifest_validator_bad_version(validator, minimal_manifest):
    """A version string that does not match semver must produce an error."""
    bad = PackageManifest(
        name=minimal_manifest.name,
        version="abc",
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    errors = validator.validate(bad)
    assert any("version" in e.lower() for e in errors)


def test_manifest_validator_no_capabilities(validator, minimal_manifest):
    """An empty capability set must produce an error."""
    bad = PackageManifest(
        name=minimal_manifest.name,
        version=minimal_manifest.version,
        capabilities=frozenset(),
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    errors = validator.validate(bad)
    assert any("capabilit" in e.lower() for e in errors)


def test_manifest_validator_is_valid_false_empty_name(validator, minimal_manifest):
    """is_valid must return False when name is empty."""
    bad = PackageManifest(
        name="",
        version=minimal_manifest.version,
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    assert validator.is_valid(bad) is False


def test_manifest_validator_assert_valid_raises(validator):
    """assert_valid must raise ValueError for an invalid manifest."""
    bad = PackageManifest(
        name="",
        version="bad-version",
        capabilities=frozenset(),
        theory_chapter="",
        description="",
        author="",
        dependencies=(),
        created_at="not-a-date",
    )
    with pytest.raises(ValueError, match="validation failed"):
        validator.assert_valid(bad)


def test_manifest_validator_assert_valid_no_raise(validator, minimal_manifest):
    """assert_valid must not raise for a valid manifest."""
    validator.assert_valid(minimal_manifest)  # should not raise


def test_manifest_validator_empty_description(validator, minimal_manifest):
    """An empty description must produce a validation error."""
    bad = PackageManifest(
        name=minimal_manifest.name,
        version=minimal_manifest.version,
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description="",
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at=minimal_manifest.created_at,
    )
    errors = validator.validate(bad)
    assert any("description" in e.lower() for e in errors)


def test_manifest_validator_bad_created_at(validator, minimal_manifest):
    """A non-ISO-8601 created_at must produce a validation error."""
    bad = PackageManifest(
        name=minimal_manifest.name,
        version=minimal_manifest.version,
        capabilities=minimal_manifest.capabilities,
        theory_chapter=minimal_manifest.theory_chapter,
        description=minimal_manifest.description,
        author=minimal_manifest.author,
        dependencies=minimal_manifest.dependencies,
        created_at="yesterday",
    )
    errors = validator.validate(bad)
    assert any("created_at" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------


def test_package_registry_register_get(minimal_manifest):
    """register() then get() must return the same manifest."""
    r = PackageRegistry()
    r.register(minimal_manifest)
    retrieved = r.get(minimal_manifest.name)
    assert retrieved == minimal_manifest


def test_package_registry_get_missing():
    """get() must return None for an unknown package name."""
    r = PackageRegistry()
    assert r.get("jugeo.nonexistent") is None


def test_package_registry_contains(minimal_manifest):
    """contains() and __contains__ must reflect registered names."""
    r = PackageRegistry()
    assert not r.contains(minimal_manifest.name)
    r.register(minimal_manifest)
    assert r.contains(minimal_manifest.name)
    assert minimal_manifest.name in r


def test_package_registry_all_manifests(registry_with_manifests):
    """all_manifests() must return all registered manifests."""
    manifests = registry_with_manifests.all_manifests()
    assert len(manifests) == 2
    names = {m.name for m in manifests}
    assert "jugeo.test_pkg" in names
    assert "jugeo.full_pkg" in names


def test_package_registry_len(registry_with_manifests):
    """__len__ must reflect the number of registered manifests."""
    assert len(registry_with_manifests) == 2


def test_package_registry_find_by_capability_present(registry_with_manifests):
    """find_by_capability must return manifests that have the capability."""
    results = registry_with_manifests.find_by_capability(
        PackageCapability.ANALOGY_CONSTRUCTION
    )
    # Both minimal and full manifests have ANALOGY_CONSTRUCTION
    names = {m.name for m in results}
    assert "jugeo.test_pkg" in names
    assert "jugeo.full_pkg" in names


def test_package_registry_find_by_capability_filtered(registry_with_manifests):
    """find_by_capability must NOT return manifests lacking the capability."""
    # Only full_manifest has BRIDGE_FINDING
    results = registry_with_manifests.find_by_capability(
        PackageCapability.BRIDGE_FINDING
    )
    names = {m.name for m in results}
    assert "jugeo.full_pkg" in names
    assert "jugeo.test_pkg" not in names


def test_package_registry_find_by_capability_type_error():
    """find_by_capability must raise TypeError for non-capability input."""
    r = PackageRegistry()
    with pytest.raises(TypeError):
        r.find_by_capability("bridge_finding")  # type: ignore[arg-type]


def test_package_registry_unregister(minimal_manifest):
    """unregister() must remove the manifest and return True."""
    r = PackageRegistry()
    r.register(minimal_manifest)
    removed = r.unregister(minimal_manifest.name)
    assert removed is True
    assert r.get(minimal_manifest.name) is None


def test_package_registry_unregister_missing():
    """unregister() must return False for an unknown name."""
    r = PackageRegistry()
    assert r.unregister("jugeo.nonexistent") is False


def test_package_registry_dependency_graph(registry_with_manifests):
    """dependency_graph() must return a dict mapping names to dep lists."""
    graph = registry_with_manifests.dependency_graph()
    assert isinstance(graph, dict)
    assert "jugeo.test_pkg" in graph
    assert "jugeo.full_pkg" in graph
    # minimal_manifest has no deps
    assert graph["jugeo.test_pkg"] == []


def test_package_registry_dependency_graph_missing_deps(full_manifest):
    """Deps not in registry should appear with [missing] prefix."""
    r = PackageRegistry()
    r.register(full_manifest)
    graph = r.dependency_graph()
    deps = graph["jugeo.full_pkg"]
    # full_manifest has deps that are NOT registered, so they get [missing] prefix
    for dep in deps:
        assert dep.startswith("[missing]")


def test_package_registry_resolve_load_order_no_deps(minimal_manifest):
    """resolve_load_order for a manifest with no registered deps returns [name]."""
    r = PackageRegistry()
    r.register(minimal_manifest)
    order = r.resolve_load_order(minimal_manifest.name)
    assert minimal_manifest.name in order


def test_package_registry_resolve_load_order_with_deps():
    """resolve_load_order must put dependencies before dependents."""
    r = PackageRegistry()
    dep_manifest = PackageManifest(
        name="jugeo.dep",
        version="1.0.0",
        capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        theory_chapter="Ch1",
        description="A dependency.",
        author="tester",
        dependencies=(),
        created_at=_now_iso(),
    )
    consumer_manifest = PackageManifest(
        name="jugeo.consumer",
        version="1.0.0",
        capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
        theory_chapter="Ch2",
        description="A consumer.",
        author="tester",
        dependencies=("jugeo.dep",),
        created_at=_now_iso(),
    )
    r.register(dep_manifest)
    r.register(consumer_manifest)
    order = r.resolve_load_order("jugeo.consumer")
    assert "jugeo.dep" in order
    assert "jugeo.consumer" in order
    dep_idx = order.index("jugeo.dep")
    consumer_idx = order.index("jugeo.consumer")
    assert dep_idx < consumer_idx


def test_package_registry_resolve_load_order_unknown_name():
    """resolve_load_order for an unregistered name returns [name]."""
    r = PackageRegistry()
    order = r.resolve_load_order("jugeo.unknown")
    assert order == ["jugeo.unknown"]


def test_package_registry_repr(registry_with_manifests):
    """__repr__ must contain the registered names."""
    r = repr(registry_with_manifests)
    assert "jugeo.test_pkg" in r
    assert "jugeo.full_pkg" in r


# ---------------------------------------------------------------------------
# CapabilityQuery
# ---------------------------------------------------------------------------


def test_capability_query_matches_all_required(full_manifest):
    """A query requiring caps that are all present must match."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset(),
    )
    assert q.matches(full_manifest) is True


def test_capability_query_matches_missing_required(minimal_manifest):
    """A query requiring a cap that is absent must not match."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
        optional_capabilities=frozenset(),
    )
    assert q.matches(minimal_manifest) is False


def test_capability_query_matches_empty_required(minimal_manifest):
    """An empty required set vacuously matches any manifest."""
    q = CapabilityQuery(
        required_capabilities=frozenset(),
        optional_capabilities=frozenset(),
    )
    assert q.matches(minimal_manifest) is True


def test_capability_query_score_full_match(full_manifest):
    """A query satisfied on all required and optional caps scores near 1.0."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
    )
    score = q.score(full_manifest)
    assert 0.0 <= score <= 1.0
    assert score > 0.5


def test_capability_query_score_no_match(minimal_manifest):
    """A query not matching on required caps yields a low score."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
        optional_capabilities=frozenset(),
    )
    score = q.score(minimal_manifest)
    assert score < 0.5


def test_capability_query_score_range(full_manifest, minimal_manifest):
    """Scores must always be in [0, 1]."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset(PackageCapability),
    )
    for m in (full_manifest, minimal_manifest):
        s = q.score(m)
        assert 0.0 <= s <= 1.0


def test_capability_query_min_faithfulness_clamped():
    """min_faithfulness must be clamped to [0, 1] in __post_init__."""
    q = CapabilityQuery(
        required_capabilities=frozenset(),
        optional_capabilities=frozenset(),
        min_faithfulness=-5.0,
    )
    assert q.min_faithfulness == 0.0

    q2 = CapabilityQuery(
        required_capabilities=frozenset(),
        optional_capabilities=frozenset(),
        min_faithfulness=99.0,
    )
    assert q2.min_faithfulness == 1.0


def test_capability_query_filter_registry(registry_with_manifests):
    """filter_registry must return only manifests satisfying required caps."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
        optional_capabilities=frozenset(),
    )
    results = q.filter_registry(registry_with_manifests)
    names = {m.name for m in results}
    assert "jugeo.full_pkg" in names
    assert "jugeo.test_pkg" not in names


def test_capability_query_filter_registry_sorted_by_score(registry_with_manifests):
    """filter_registry results must be sorted highest score first."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
    )
    results = q.filter_registry(registry_with_manifests)
    scores = [q.score(m) for m in results]
    assert scores == sorted(scores, reverse=True)


def test_capability_query_repr():
    """repr must return a non-empty descriptive string."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset(),
    )
    r = repr(q)
    assert "analogy_construction" in r
    assert "CapabilityQuery" in r


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------


def test_manifest_serializer_round_trip_json(serializer, minimal_manifest):
    """JSON round-trip must produce an equal manifest."""
    payload = serializer.to_json(minimal_manifest)
    assert isinstance(payload, str)
    reconstructed = serializer.from_json(payload)
    assert reconstructed == minimal_manifest


def test_manifest_serializer_round_trip_json_full(serializer, full_manifest):
    """JSON round-trip must work for a manifest with all capabilities."""
    payload = serializer.to_json(full_manifest)
    reconstructed = serializer.from_json(payload)
    assert reconstructed == full_manifest


def test_manifest_serializer_round_trip_dict(serializer, minimal_manifest):
    """Dict round-trip must produce an equal manifest."""
    d = serializer.to_dict(minimal_manifest)
    reconstructed = serializer.from_dict(d)
    assert reconstructed == minimal_manifest


def test_manifest_serializer_round_trip_dict_full(serializer, full_manifest):
    """Dict round-trip must work for a manifest with all capabilities."""
    d = serializer.to_dict(full_manifest)
    reconstructed = serializer.from_dict(d)
    assert reconstructed == full_manifest


def test_manifest_serializer_to_json_is_valid_json(serializer, full_manifest):
    """to_json output must be parseable by json.loads."""
    payload = serializer.to_json(full_manifest)
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)


def test_manifest_serializer_to_json_sorted_keys(serializer, minimal_manifest):
    """to_json must produce JSON with sorted keys."""
    payload = serializer.to_json(minimal_manifest)
    parsed = json.loads(payload)
    keys = list(parsed.keys())
    assert keys == sorted(keys)


def test_manifest_serializer_from_json_bad_json(serializer):
    """from_json must raise json.JSONDecodeError for invalid JSON."""
    with pytest.raises(json.JSONDecodeError):
        serializer.from_json("{not valid json}")


def test_manifest_serializer_from_dict_missing_key(serializer, minimal_manifest):
    """from_dict must raise KeyError when a required key is missing."""
    d = serializer.to_dict(minimal_manifest)
    del d["name"]
    with pytest.raises(KeyError):
        serializer.from_dict(d)


def test_manifest_serializer_capabilities_sorted_in_output(serializer, full_manifest):
    """to_json must serialize capabilities as a sorted list."""
    payload = serializer.to_json(full_manifest)
    parsed = json.loads(payload)
    caps = parsed["capabilities"]
    assert caps == sorted(caps)


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


def test_manifest_diagnostics_report_non_empty(diagnostics, minimal_manifest):
    """report() must return a non-empty string."""
    r = diagnostics.report(minimal_manifest)
    assert isinstance(r, str)
    assert len(r) > 0


def test_manifest_diagnostics_report_contains_name(diagnostics, minimal_manifest):
    """report() must include the manifest name."""
    r = diagnostics.report(minimal_manifest)
    assert minimal_manifest.name in r


def test_manifest_diagnostics_report_contains_version(diagnostics, minimal_manifest):
    """report() must include the manifest version."""
    r = diagnostics.report(minimal_manifest)
    assert minimal_manifest.version in r


def test_manifest_diagnostics_report_valid_marker(diagnostics, minimal_manifest):
    """report() must show 'OK' for a valid manifest."""
    r = diagnostics.report(minimal_manifest)
    assert "OK" in r or "✓" in r


def test_manifest_diagnostics_capability_summary_all_caps(diagnostics, full_manifest):
    """capability_summary must return True for all caps when all are present."""
    summary = diagnostics.capability_summary(full_manifest)
    assert isinstance(summary, dict)
    for cap in PackageCapability:
        assert summary[cap.value] is True


def test_manifest_diagnostics_capability_summary_partial(diagnostics, minimal_manifest):
    """capability_summary must return False for missing caps."""
    summary = diagnostics.capability_summary(minimal_manifest)
    assert summary[PackageCapability.ANALOGY_CONSTRUCTION.value] is True
    assert summary[PackageCapability.BRIDGE_FINDING.value] is False


def test_manifest_diagnostics_capability_summary_keys(diagnostics, minimal_manifest):
    """capability_summary must have exactly one key per PackageCapability."""
    summary = diagnostics.capability_summary(minimal_manifest)
    expected_keys = {cap.value for cap in PackageCapability}
    assert set(summary.keys()) == expected_keys


def test_manifest_diagnostics_dependency_report_non_empty(
    diagnostics, registry_with_manifests
):
    """dependency_report() must return a non-empty string for a non-empty registry."""
    r = diagnostics.dependency_report(registry_with_manifests)
    assert isinstance(r, str)
    assert len(r) > 0


def test_manifest_diagnostics_dependency_report_contains_names(
    diagnostics, registry_with_manifests
):
    """dependency_report() must include all registered manifest names."""
    r = diagnostics.dependency_report(registry_with_manifests)
    assert "jugeo.test_pkg" in r
    assert "jugeo.full_pkg" in r


def test_manifest_diagnostics_dependency_report_empty_registry(diagnostics):
    """dependency_report() must return '(empty registry)' for an empty registry."""
    r = diagnostics.dependency_report(PackageRegistry())
    assert r == "(empty registry)"


def test_manifest_diagnostics_validate_all_returns_dict(
    diagnostics, registry_with_manifests
):
    """validate_all must return a dict mapping name -> list of errors."""
    result = diagnostics.validate_all(registry_with_manifests)
    assert isinstance(result, dict)
    for name, errors in result.items():
        assert isinstance(name, str)
        assert isinstance(errors, list)


def test_manifest_diagnostics_validate_all_valid_manifests(
    diagnostics, registry_with_manifests
):
    """validate_all must return empty error lists for valid manifests."""
    result = diagnostics.validate_all(registry_with_manifests)
    for name, errors in result.items():
        assert errors == [], f"{name!r} unexpectedly has errors: {errors}"


def test_manifest_diagnostics_validate_all_covers_all(
    diagnostics, registry_with_manifests
):
    """validate_all must include an entry for every registered manifest."""
    result = diagnostics.validate_all(registry_with_manifests)
    registered_names = {m.name for m in registry_with_manifests.all_manifests()}
    assert set(result.keys()) == registered_names


def test_manifest_diagnostics_score_report(diagnostics, registry_with_manifests):
    """score_report must return a non-empty string."""
    q = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
    )
    r = diagnostics.score_report(q, registry_with_manifests)
    assert isinstance(r, str)
    assert len(r) > 0


def test_manifest_diagnostics_repr(diagnostics):
    """repr must return a predictable string."""
    assert repr(diagnostics) == "ManifestDiagnostics()"


# ---------------------------------------------------------------------------
# _DEFAULT_MANIFEST
# ---------------------------------------------------------------------------


def test_default_manifest_exists():
    """_DEFAULT_MANIFEST must be a PackageManifest instance."""
    assert isinstance(_DEFAULT_MANIFEST, PackageManifest)


def test_default_manifest_name():
    """_DEFAULT_MANIFEST must use the documented PACKAGE_NAME."""
    assert _DEFAULT_MANIFEST.name == PACKAGE_NAME


def test_default_manifest_version():
    """_DEFAULT_MANIFEST must use the documented PACKAGE_VERSION."""
    assert _DEFAULT_MANIFEST.version == PACKAGE_VERSION


def test_default_manifest_theory_chapter():
    """_DEFAULT_MANIFEST must reference THEORY_CHAPTER."""
    assert _DEFAULT_MANIFEST.theory_chapter == THEORY_CHAPTER


def test_default_manifest_has_all_capabilities():
    """_DEFAULT_MANIFEST must include every PackageCapability member."""
    for cap in PackageCapability:
        assert cap in _DEFAULT_MANIFEST.capabilities, (
            f"_DEFAULT_MANIFEST missing capability {cap.value!r}"
        )


def test_default_manifest_is_valid():
    """_DEFAULT_MANIFEST must pass all ManifestValidator rules."""
    v = ManifestValidator()
    assert v.is_valid(_DEFAULT_MANIFEST), v.validate(_DEFAULT_MANIFEST)


def test_default_manifest_has_dependencies():
    """_DEFAULT_MANIFEST must declare at least one dependency."""
    assert len(_DEFAULT_MANIFEST.dependencies) >= 1


def test_default_manifest_created_at_is_iso():
    """_DEFAULT_MANIFEST.created_at must be a parseable ISO-8601 string."""
    from datetime import datetime
    dt = datetime.fromisoformat(_DEFAULT_MANIFEST.created_at)
    assert dt is not None


# ---------------------------------------------------------------------------
# Full workflow integration test
# ---------------------------------------------------------------------------


def test_full_workflow():
    """Create a manifest, validate it, register it, query it, and serialize it."""
    # 1. Create
    manifest = PackageManifest(
        name="jugeo.workflow_test",
        version="3.0.1",
        capabilities=frozenset({
            PackageCapability.ANALOGY_CONSTRUCTION,
            PackageCapability.TRANSPORT_VERIFICATION,
        }),
        theory_chapter="Ch60",
        description="Integration test manifest exercising the full workflow.",
        author="integration-test",
        dependencies=("jugeo.ideation.ideas",),
        created_at=_now_iso(),
    )

    # 2. Validate
    validator = ManifestValidator()
    assert validator.is_valid(manifest)
    validator.assert_valid(manifest)  # must not raise

    # 3. Register
    registry = PackageRegistry()
    registry.register(manifest)
    registry.register(_DEFAULT_MANIFEST)
    assert len(registry) == 2
    assert registry.get(manifest.name) == manifest

    # 4. Query
    query = CapabilityQuery(
        required_capabilities=frozenset({PackageCapability.ANALOGY_CONSTRUCTION}),
        optional_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
    )
    matches = query.filter_registry(registry)
    names = {m.name for m in matches}
    assert manifest.name in names
    assert _DEFAULT_MANIFEST.name in names

    score = query.score(manifest)
    assert 0.0 <= score <= 1.0

    # 5. Serialize
    serializer = ManifestSerializer()
    json_str = serializer.to_json(manifest)
    reconstructed = serializer.from_json(json_str)
    assert reconstructed == manifest

    d = serializer.to_dict(manifest)
    reconstructed_dict = serializer.from_dict(d)
    assert reconstructed_dict == manifest

    # 6. Diagnostics
    diag = ManifestDiagnostics()
    report = diag.report(manifest)
    assert manifest.name in report
    summary = diag.capability_summary(manifest)
    assert summary[PackageCapability.ANALOGY_CONSTRUCTION.value] is True
    assert summary[PackageCapability.BRIDGE_FINDING.value] is False

    dep_report = diag.dependency_report(registry)
    assert manifest.name in dep_report

    val_result = diag.validate_all(registry)
    assert manifest.name in val_result
    assert val_result[manifest.name] == []

    # 7. Load-order resolution
    order = registry.resolve_load_order(manifest.name)
    assert manifest.name in order
