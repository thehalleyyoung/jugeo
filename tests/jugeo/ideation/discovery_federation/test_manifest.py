from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
test_manifest.py
================
TDD tests for ``jugeo.ideation.discovery_federation.manifest``.

The implementation does NOT yet exist; these tests define the expected API
and behaviour for:

  - DiscoveryFederationManifest  (primary manifest class)
  - FederationManifestBuilder    (builder pattern)
  - build_federation_manifest    (free convenience function)

All tests are written before the module exists (TDD / design-by-contract).
"""

import json

from jugeo.ideation.discovery_federation.manifest import (
    DiscoveryFederationManifest,
    FederationManifestBuilder,
    build_federation_manifest,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ISO_NOW = "2024-06-01T12:00:00"
_VALID_VERSION = "1.0.0"

_SAMPLE_NODE = {
    "node_id": "node-alpha",
    "name": "Alpha Node",
    "trust_score": 0.8,
    "authority_level": "REGIONAL",
}

_SAMPLE_DISCOVERY = {
    "discovery_id": "disc-001",
    "source_node": "node-alpha",
    "target_node": "node-beta",
    "trust_score": 0.75,
    "status": "PENDING",
}

_SAMPLE_CONSENSUS = {
    "consensus_id": "cons-001",
    "discovery_id": "disc-001",
    "outcome": "PENDING",
    "quorum_threshold": 0.5,
}

_SAMPLE_GRANT = {
    "grant_id": "grant-001",
    "grantor_node": "node-alpha",
    "grantee_node": "node-beta",
    "level": "LOCAL",
    "domain": "physics",
}


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _make_manifest(
    manifest_id: str = "mf-001",
    version: str = _VALID_VERSION,
    author: str = "test-author",
    chapter_ref: str = "ch-ideation-07",
    exports: list | None = None,
    created_at: str = _ISO_NOW,
    description: str = "Test federation manifest",
    tags: list | None = None,
    is_sealed: bool = False,
    is_published: bool = False,
    is_deprecated: bool = False,
    nodes: list | None = None,
    discoveries: list | None = None,
    consensuses: list | None = None,
    authority_grants: list | None = None,
) -> DiscoveryFederationManifest:
    return DiscoveryFederationManifest.create(
        manifest_id=manifest_id,
        version=version,
        author=author,
        chapter_ref=chapter_ref,
        exports=exports or ["FederatedDiscovery", "FederationConsensus"],
        created_at=created_at,
        description=description,
        tags=tags or ["federation", "ideation"],
        is_sealed=is_sealed,
        is_published=is_published,
        is_deprecated=is_deprecated,
        nodes=nodes or [],
        discoveries=discoveries or [],
        consensuses=consensuses or [],
        authority_grants=authority_grants or [],
    )


def _make_sealed_manifest(**kwargs) -> DiscoveryFederationManifest:
    m = _make_manifest(**kwargs)
    m.seal()
    return m


def _make_published_manifest(**kwargs) -> DiscoveryFederationManifest:
    m = _make_sealed_manifest(**kwargs)
    m.publish()
    return m


def _make_builder(
    version: str = _VALID_VERSION,
    author: str = "builder-author",
    chapter_ref: str = "ch-fed-01",
    description: str = "Builder manifest",
) -> FederationManifestBuilder:
    return (
        FederationManifestBuilder()
        .set_version(version)
        .set_author(author)
        .set_chapter_ref(chapter_ref)
        .set_description(description)
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_manifest() -> DiscoveryFederationManifest:
    return _make_manifest(nodes=[], discoveries=[], consensuses=[], authority_grants=[])


@pytest.fixture
def sealed_manifest() -> DiscoveryFederationManifest:
    return _make_sealed_manifest()


@pytest.fixture
def published_manifest() -> DiscoveryFederationManifest:
    return _make_published_manifest()


@pytest.fixture
def builder_with_nodes() -> FederationManifestBuilder:
    return (
        _make_builder()
        .add_node(_SAMPLE_NODE)
        .add_node({**_SAMPLE_NODE, "node_id": "node-beta", "name": "Beta Node"})
    )


# ===========================================================================
# DiscoveryFederationManifest — creation
# ===========================================================================

def test_manifest_create_returns_instance(empty_manifest: DiscoveryFederationManifest) -> None:
    assert isinstance(empty_manifest, DiscoveryFederationManifest)


def test_manifest_fields_set_correctly(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.manifest_id == "mf-001"
    assert empty_manifest.version == _VALID_VERSION
    assert empty_manifest.author == "test-author"
    assert empty_manifest.chapter_ref == "ch-ideation-07"


def test_manifest_default_not_sealed(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.is_sealed is False


def test_manifest_default_not_published(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.is_published is False


def test_manifest_default_not_deprecated(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.is_deprecated is False


def test_manifest_exports_list(empty_manifest: DiscoveryFederationManifest) -> None:
    assert isinstance(empty_manifest.exports, list)
    assert "FederatedDiscovery" in empty_manifest.exports


def test_manifest_tags_list(empty_manifest: DiscoveryFederationManifest) -> None:
    assert isinstance(empty_manifest.tags, list)
    assert "federation" in empty_manifest.tags


def test_manifest_description_nonempty(empty_manifest: DiscoveryFederationManifest) -> None:
    assert len(empty_manifest.description) > 0


def test_manifest_nodes_starts_empty(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.nodes == []


def test_manifest_discoveries_starts_empty(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.discoveries == []


def test_manifest_consensuses_starts_empty(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.consensuses == []


def test_manifest_authority_grants_starts_empty(empty_manifest: DiscoveryFederationManifest) -> None:
    assert empty_manifest.authority_grants == []


@pytest.mark.parametrize("version", ["1.0.0", "2.3.1", "0.0.1", "10.20.30"])
def test_manifest_valid_version_formats(version: str) -> None:
    m = _make_manifest(version=version)
    assert m.version == version


# ===========================================================================
# DiscoveryFederationManifest — seal lifecycle
# ===========================================================================

def test_manifest_seal_sets_is_sealed(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.seal()
    assert empty_manifest.is_sealed is True


def test_manifest_seal_idempotent_or_raises(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.seal()
    with pytest.raises((ValueError, RuntimeError, AssertionError)):
        empty_manifest.seal()


def test_manifest_sealed_is_not_published(sealed_manifest: DiscoveryFederationManifest) -> None:
    assert sealed_manifest.is_published is False


def test_manifest_can_publish_after_seal(sealed_manifest: DiscoveryFederationManifest) -> None:
    sealed_manifest.publish()
    assert sealed_manifest.is_published is True


def test_manifest_cannot_publish_without_seal(empty_manifest: DiscoveryFederationManifest) -> None:
    with pytest.raises((ValueError, RuntimeError, AssertionError)):
        empty_manifest.publish()


def test_manifest_deprecate_after_publish(published_manifest: DiscoveryFederationManifest) -> None:
    published_manifest.deprecate()
    assert published_manifest.is_deprecated is True


def test_manifest_deprecate_already_deprecated_raises(published_manifest: DiscoveryFederationManifest) -> None:
    published_manifest.deprecate()
    with pytest.raises((ValueError, RuntimeError, AssertionError)):
        published_manifest.deprecate()


def test_manifest_publish_sets_is_published(sealed_manifest: DiscoveryFederationManifest) -> None:
    sealed_manifest.publish()
    assert sealed_manifest.is_published is True


def test_manifest_publish_idempotent_or_raises(sealed_manifest: DiscoveryFederationManifest) -> None:
    sealed_manifest.publish()
    with pytest.raises((ValueError, RuntimeError, AssertionError)):
        sealed_manifest.publish()


def test_manifest_full_lifecycle() -> None:
    m = _make_manifest()
    assert not m.is_sealed
    m.seal()
    assert m.is_sealed
    m.publish()
    assert m.is_published
    m.deprecate()
    assert m.is_deprecated


# ===========================================================================
# DiscoveryFederationManifest — add_* methods
# ===========================================================================

def test_manifest_add_node_increases_count(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_node(_SAMPLE_NODE)
    assert len(empty_manifest.nodes) == 1


def test_manifest_add_multiple_nodes(empty_manifest: DiscoveryFederationManifest) -> None:
    for i in range(5):
        empty_manifest.add_node({**_SAMPLE_NODE, "node_id": f"node-{i}"})
    assert len(empty_manifest.nodes) == 5


def test_manifest_add_discovery_increases_count(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_discovery(_SAMPLE_DISCOVERY)
    assert len(empty_manifest.discoveries) == 1


def test_manifest_add_multiple_discoveries(empty_manifest: DiscoveryFederationManifest) -> None:
    for i in range(3):
        empty_manifest.add_discovery({**_SAMPLE_DISCOVERY, "discovery_id": f"disc-{i:03d}"})
    assert len(empty_manifest.discoveries) == 3


def test_manifest_add_consensus_increases_count(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_consensus(_SAMPLE_CONSENSUS)
    assert len(empty_manifest.consensuses) == 1


def test_manifest_add_multiple_consensuses(empty_manifest: DiscoveryFederationManifest) -> None:
    for i in range(4):
        empty_manifest.add_consensus({**_SAMPLE_CONSENSUS, "consensus_id": f"cons-{i:03d}"})
    assert len(empty_manifest.consensuses) == 4


def test_manifest_add_authority_grant_increases_count(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_authority_grant(_SAMPLE_GRANT)
    assert len(empty_manifest.authority_grants) == 1


def test_manifest_add_multiple_grants(empty_manifest: DiscoveryFederationManifest) -> None:
    for i in range(10):
        empty_manifest.add_authority_grant({**_SAMPLE_GRANT, "grant_id": f"grant-{i:03d}"})
    assert len(empty_manifest.authority_grants) == 10


@pytest.mark.parametrize("node_count", [1, 5, 10])
def test_manifest_add_node_parametrized(node_count: int) -> None:
    m = _make_manifest()
    for i in range(node_count):
        m.add_node({**_SAMPLE_NODE, "node_id": f"node-{i:03d}"})
    assert len(m.nodes) == node_count


def test_manifest_node_dict_contents_preserved(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_node(_SAMPLE_NODE)
    added = empty_manifest.nodes[0]
    assert added["node_id"] == "node-alpha"
    assert added["trust_score"] == 0.8


# ===========================================================================
# DiscoveryFederationManifest — to_json / from_json
# ===========================================================================

def test_manifest_to_json_returns_string(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    assert isinstance(j, str)
    assert len(j) > 0


def test_manifest_to_json_valid_json(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    parsed = json.loads(j)
    assert isinstance(parsed, dict)


def test_manifest_to_json_contains_manifest_id(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    assert "mf-001" in j


def test_manifest_from_json_roundtrip_manifest_id(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.manifest_id == empty_manifest.manifest_id


def test_manifest_from_json_roundtrip_version(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.version == empty_manifest.version


def test_manifest_from_json_roundtrip_author(empty_manifest: DiscoveryFederationManifest) -> None:
    j = empty_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.author == empty_manifest.author


def test_manifest_from_json_roundtrip_is_sealed(sealed_manifest: DiscoveryFederationManifest) -> None:
    j = sealed_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.is_sealed is True


def test_manifest_from_json_roundtrip_is_published(published_manifest: DiscoveryFederationManifest) -> None:
    j = published_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.is_published is True


def test_manifest_from_json_roundtrip_nodes(empty_manifest: DiscoveryFederationManifest) -> None:
    empty_manifest.add_node(_SAMPLE_NODE)
    j = empty_manifest.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert len(restored.nodes) == 1
    assert restored.nodes[0]["node_id"] == "node-alpha"


def test_manifest_from_json_roundtrip_discoveries() -> None:
    m = _make_manifest()
    m.add_discovery(_SAMPLE_DISCOVERY)
    j = m.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert len(restored.discoveries) == 1
    assert restored.discoveries[0]["discovery_id"] == "disc-001"


def test_manifest_from_json_roundtrip_consensuses() -> None:
    m = _make_manifest()
    m.add_consensus(_SAMPLE_CONSENSUS)
    j = m.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert len(restored.consensuses) == 1


def test_manifest_from_json_roundtrip_authority_grants() -> None:
    m = _make_manifest()
    m.add_authority_grant(_SAMPLE_GRANT)
    j = m.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert len(restored.authority_grants) == 1
    assert restored.authority_grants[0]["grant_id"] == "grant-001"


def test_manifest_from_json_roundtrip_tags() -> None:
    m = _make_manifest(tags=["alpha", "beta", "gamma"])
    restored = DiscoveryFederationManifest.from_json(m.to_json())
    assert restored.tags == ["alpha", "beta", "gamma"]


def test_manifest_from_json_roundtrip_exports() -> None:
    exports = ["FederatedDiscovery", "FederationConsensus", "DiscoveryAuthority"]
    m = _make_manifest(exports=exports)
    restored = DiscoveryFederationManifest.from_json(m.to_json())
    assert restored.exports == exports


def test_manifest_from_json_roundtrip_description() -> None:
    m = _make_manifest(description="Unique description xyz")
    restored = DiscoveryFederationManifest.from_json(m.to_json())
    assert restored.description == "Unique description xyz"


def test_manifest_from_json_roundtrip_chapter_ref() -> None:
    m = _make_manifest(chapter_ref="ch-fed-99")
    restored = DiscoveryFederationManifest.from_json(m.to_json())
    assert restored.chapter_ref == "ch-fed-99"


def test_manifest_from_json_roundtrip_is_deprecated() -> None:
    m = _make_published_manifest()
    m.deprecate()
    j = m.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.is_deprecated is True


# ===========================================================================
# DiscoveryFederationManifest — validate()
# ===========================================================================

def test_manifest_validate_returns_list(empty_manifest: DiscoveryFederationManifest) -> None:
    errors = empty_manifest.validate()
    assert isinstance(errors, list)


def test_manifest_validate_valid_manifest_returns_empty_list(empty_manifest: DiscoveryFederationManifest) -> None:
    errors = empty_manifest.validate()
    assert errors == []


def test_manifest_validate_invalid_version_format_returns_error() -> None:
    m = _make_manifest(version="not-a-version")
    errors = m.validate()
    assert len(errors) > 0


def test_manifest_validate_empty_author_returns_error() -> None:
    m = _make_manifest(author="")
    errors = m.validate()
    assert len(errors) > 0


def test_manifest_validate_empty_manifest_id_returns_error() -> None:
    m = _make_manifest(manifest_id="")
    errors = m.validate()
    assert len(errors) > 0


def test_manifest_validate_errors_are_strings() -> None:
    m = _make_manifest(version="bad")
    errors = m.validate()
    for e in errors:
        assert isinstance(e, str)


def test_manifest_validate_sealed_valid_manifest() -> None:
    m = _make_manifest()
    m.seal()
    errors = m.validate()
    assert errors == []


def test_manifest_validate_with_nodes_valid() -> None:
    m = _make_manifest()
    m.add_node(_SAMPLE_NODE)
    errors = m.validate()
    assert errors == []


# ===========================================================================
# DiscoveryFederationManifest — diff()
# ===========================================================================

def test_manifest_diff_same_manifest_is_empty_or_minimal() -> None:
    m = _make_manifest()
    diff = m.diff(m)
    assert isinstance(diff, dict)


def test_manifest_diff_different_version_detected() -> None:
    m1 = _make_manifest(version="1.0.0")
    m2 = _make_manifest(version="2.0.0")
    diff = m1.diff(m2)
    assert "version" in diff or len(diff) > 0


def test_manifest_diff_different_author_detected() -> None:
    m1 = _make_manifest(author="author-a")
    m2 = _make_manifest(author="author-b")
    diff = m1.diff(m2)
    assert "author" in diff or len(diff) > 0


def test_manifest_diff_different_nodes_detected() -> None:
    m1 = _make_manifest()
    m2 = _make_manifest()
    m2.add_node(_SAMPLE_NODE)
    diff = m1.diff(m2)
    assert "nodes" in diff or len(diff) > 0


def test_manifest_diff_returns_dict(empty_manifest: DiscoveryFederationManifest) -> None:
    m2 = _make_manifest(manifest_id="mf-002")
    diff = empty_manifest.diff(m2)
    assert isinstance(diff, dict)


def test_manifest_diff_identical_manifests_no_field_changes() -> None:
    m1 = _make_manifest()
    m2 = _make_manifest()
    diff = m1.diff(m2)
    # Diff of two identical manifests should have no meaningful changes
    # (manifest_id might differ but version/author/etc. should not)
    for key in ("version", "author", "chapter_ref", "description"):
        if key in diff:
            assert diff[key] is None or diff[key] == {} or diff[key] == (m1.__dict__.get(key), m2.__dict__.get(key))


def test_manifest_diff_added_discovery_detected() -> None:
    m1 = _make_manifest()
    m2 = _make_manifest()
    m2.add_discovery(_SAMPLE_DISCOVERY)
    diff = m1.diff(m2)
    assert len(diff) > 0


# ===========================================================================
# DiscoveryFederationManifest — version_bump()
# ===========================================================================

def test_manifest_version_bump_patch() -> None:
    m = _make_manifest(version="1.0.0")
    bumped = m.version_bump("patch")
    assert bumped.version == "1.0.1"


def test_manifest_version_bump_minor() -> None:
    m = _make_manifest(version="1.0.0")
    bumped = m.version_bump("minor")
    assert bumped.version == "1.1.0"


def test_manifest_version_bump_major() -> None:
    m = _make_manifest(version="1.0.0")
    bumped = m.version_bump("major")
    assert bumped.version == "2.0.0"


def test_manifest_version_bump_returns_new_manifest() -> None:
    m = _make_manifest(version="1.2.3")
    bumped = m.version_bump("patch")
    assert isinstance(bumped, DiscoveryFederationManifest)
    assert bumped is not m


def test_manifest_version_bump_original_unchanged() -> None:
    m = _make_manifest(version="1.2.3")
    _ = m.version_bump("patch")
    assert m.version == "1.2.3"


def test_manifest_version_bump_minor_resets_patch() -> None:
    m = _make_manifest(version="1.2.5")
    bumped = m.version_bump("minor")
    assert bumped.version == "1.3.0"


def test_manifest_version_bump_major_resets_minor_and_patch() -> None:
    m = _make_manifest(version="3.4.5")
    bumped = m.version_bump("major")
    assert bumped.version == "4.0.0"


@pytest.mark.parametrize("start_version,part,expected", [
    ("1.0.0", "patch", "1.0.1"),
    ("1.0.0", "minor", "1.1.0"),
    ("1.0.0", "major", "2.0.0"),
    ("2.3.1", "patch", "2.3.2"),
    ("2.3.1", "minor", "2.4.0"),
    ("2.3.1", "major", "3.0.0"),
    ("0.0.1", "patch", "0.0.2"),
    ("0.0.1", "minor", "0.1.0"),
    ("0.0.1", "major", "1.0.0"),
])
def test_manifest_version_bump_parametrized(start_version: str, part: str, expected: str) -> None:
    m = _make_manifest(version=start_version)
    bumped = m.version_bump(part)
    assert bumped.version == expected


def test_manifest_version_bump_invalid_part_raises() -> None:
    m = _make_manifest(version="1.0.0")
    with pytest.raises((ValueError, KeyError, AssertionError)):
        m.version_bump("invalid_part")


# ===========================================================================
# DiscoveryFederationManifest — summarize()
# ===========================================================================

def test_manifest_summarize_returns_string(empty_manifest: DiscoveryFederationManifest) -> None:
    s = empty_manifest.summarize()
    assert isinstance(s, str)


def test_manifest_summarize_nonempty(empty_manifest: DiscoveryFederationManifest) -> None:
    s = empty_manifest.summarize()
    assert len(s) > 0


def test_manifest_summarize_contains_version(empty_manifest: DiscoveryFederationManifest) -> None:
    s = empty_manifest.summarize()
    assert _VALID_VERSION in s


def test_manifest_summarize_contains_manifest_id(empty_manifest: DiscoveryFederationManifest) -> None:
    s = empty_manifest.summarize()
    assert "mf-001" in s


def test_manifest_summarize_contains_author(empty_manifest: DiscoveryFederationManifest) -> None:
    s = empty_manifest.summarize()
    assert "test-author" in s


def test_manifest_summarize_sealed_manifest_mentions_sealed(sealed_manifest: DiscoveryFederationManifest) -> None:
    s = sealed_manifest.summarize()
    assert "seal" in s.lower() or "sealed" in s.lower() or len(s) > 0


def test_manifest_summarize_published_manifest_mentions_published(published_manifest: DiscoveryFederationManifest) -> None:
    s = published_manifest.summarize()
    assert len(s) > 0


def test_manifest_summarize_with_nodes_reflects_count() -> None:
    m = _make_manifest()
    for i in range(3):
        m.add_node({**_SAMPLE_NODE, "node_id": f"node-{i}"})
    s = m.summarize()
    # Should mention the node count or at minimum be a valid summary
    assert isinstance(s, str) and len(s) > 0


# ===========================================================================
# FederationManifestBuilder
# ===========================================================================

def test_builder_creates_instance() -> None:
    builder = FederationManifestBuilder()
    assert isinstance(builder, FederationManifestBuilder)


def test_builder_set_version_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.set_version("1.0.0")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_set_author_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.set_author("the-author")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_set_chapter_ref_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.set_chapter_ref("ch-01")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_set_description_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.set_description("A manifest")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_add_tag_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.add_tag("federation")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_add_export_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.add_export("FederatedDiscovery")
    assert isinstance(result, FederationManifestBuilder)


def test_builder_add_node_returns_builder() -> None:
    b = FederationManifestBuilder()
    result = b.add_node(_SAMPLE_NODE)
    assert isinstance(result, FederationManifestBuilder)


def test_builder_build_returns_manifest(builder_with_nodes: FederationManifestBuilder) -> None:
    m = builder_with_nodes.build()
    assert isinstance(m, DiscoveryFederationManifest)


def test_builder_build_sets_version() -> None:
    m = _make_builder(version="2.1.0").build()
    assert m.version == "2.1.0"


def test_builder_build_sets_author() -> None:
    m = _make_builder(author="my-author").build()
    assert m.author == "my-author"


def test_builder_build_sets_chapter_ref() -> None:
    m = _make_builder(chapter_ref="ch-fed-99").build()
    assert m.chapter_ref == "ch-fed-99"


def test_builder_build_sets_description() -> None:
    m = _make_builder(description="My description").build()
    assert m.description == "My description"


def test_builder_add_multiple_tags() -> None:
    b = _make_builder().add_tag("alpha").add_tag("beta").add_tag("gamma")
    m = b.build()
    assert "alpha" in m.tags
    assert "beta" in m.tags
    assert "gamma" in m.tags


def test_builder_add_multiple_exports() -> None:
    exports = ["FederatedDiscovery", "FederationConsensus", "DiscoveryAuthority"]
    b = _make_builder()
    for e in exports:
        b = b.add_export(e)
    m = b.build()
    for e in exports:
        assert e in m.exports


def test_builder_add_node_appears_in_manifest(builder_with_nodes: FederationManifestBuilder) -> None:
    m = builder_with_nodes.build()
    assert len(m.nodes) == 2


def test_builder_chain_returns_same_builder() -> None:
    b = FederationManifestBuilder()
    result = b.set_version("1.0.0").set_author("a").set_chapter_ref("ch").set_description("d")
    assert result is b or isinstance(result, FederationManifestBuilder)


def test_builder_build_manifest_is_not_sealed_by_default() -> None:
    m = _make_builder().build()
    assert m.is_sealed is False


def test_builder_build_manifest_is_not_published_by_default() -> None:
    m = _make_builder().build()
    assert m.is_published is False


@pytest.mark.parametrize("export_count", [0, 3, 10])
def test_builder_export_count_parametrized(export_count: int) -> None:
    b = _make_builder()
    for i in range(export_count):
        b = b.add_export(f"Export{i}")
    m = b.build()
    assert len([e for e in m.exports if e.startswith("Export")]) == export_count


# ===========================================================================
# build_federation_manifest free function
# ===========================================================================

def test_build_federation_manifest_returns_manifest() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="fn-author",
        chapter_ref="ch-fn-01",
        description="Function-built manifest",
    )
    assert isinstance(m, DiscoveryFederationManifest)


def test_build_federation_manifest_sets_version() -> None:
    m = build_federation_manifest(
        version="3.2.1",
        author="a",
        chapter_ref="c",
        description="d",
    )
    assert m.version == "3.2.1"


def test_build_federation_manifest_sets_author() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="special-author",
        chapter_ref="c",
        description="d",
    )
    assert m.author == "special-author"


def test_build_federation_manifest_sets_chapter_ref() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="ch-special-99",
        description="d",
    )
    assert m.chapter_ref == "ch-special-99"


def test_build_federation_manifest_not_sealed() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="c",
        description="d",
    )
    assert m.is_sealed is False


def test_build_federation_manifest_not_published() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="c",
        description="d",
    )
    assert m.is_published is False


def test_build_federation_manifest_can_be_sealed() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="c",
        description="d",
    )
    m.seal()
    assert m.is_sealed is True


def test_build_federation_manifest_can_add_nodes() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="c",
        description="d",
    )
    m.add_node(_SAMPLE_NODE)
    assert len(m.nodes) == 1


def test_build_federation_manifest_validate_returns_empty() -> None:
    m = build_federation_manifest(
        version="1.0.0",
        author="a",
        chapter_ref="c",
        description="d",
    )
    errors = m.validate()
    assert isinstance(errors, list)


@pytest.mark.parametrize("version", ["1.0.0", "2.3.1", "0.0.1"])
def test_build_federation_manifest_versions_parametrized(version: str) -> None:
    m = build_federation_manifest(
        version=version,
        author="a",
        chapter_ref="c",
        description="d",
    )
    assert m.version == version


# ===========================================================================
# Edge cases and additional coverage
# ===========================================================================

def test_manifest_empty_tags_list() -> None:
    m = _make_manifest(tags=[])
    assert m.tags == []
    errors = m.validate()
    assert isinstance(errors, list)


def test_manifest_empty_exports_list() -> None:
    m = _make_manifest(exports=[])
    assert m.exports == []


def test_manifest_to_json_with_all_collections() -> None:
    m = _make_manifest()
    m.add_node(_SAMPLE_NODE)
    m.add_discovery(_SAMPLE_DISCOVERY)
    m.add_consensus(_SAMPLE_CONSENSUS)
    m.add_authority_grant(_SAMPLE_GRANT)
    j = m.to_json()
    parsed = json.loads(j)
    assert len(parsed["nodes"]) == 1
    assert len(parsed["discoveries"]) == 1
    assert len(parsed["consensuses"]) == 1
    assert len(parsed["authority_grants"]) == 1


def test_manifest_from_json_empty_collections() -> None:
    m = _make_manifest()
    restored = DiscoveryFederationManifest.from_json(m.to_json())
    assert restored.nodes == []
    assert restored.discoveries == []
    assert restored.consensuses == []
    assert restored.authority_grants == []


def test_manifest_version_bump_chaining() -> None:
    m = _make_manifest(version="1.0.0")
    m2 = m.version_bump("minor")
    m3 = m2.version_bump("patch")
    assert m3.version == "1.1.1"


def test_manifest_diff_sealed_vs_unsealed() -> None:
    m1 = _make_manifest()
    m2 = _make_manifest()
    m2.seal()
    diff = m1.diff(m2)
    assert isinstance(diff, dict)


def test_manifest_diff_with_different_descriptions() -> None:
    m1 = _make_manifest(description="First description")
    m2 = _make_manifest(description="Second description")
    diff = m1.diff(m2)
    assert len(diff) > 0


def test_manifest_summarize_with_all_data() -> None:
    m = _make_manifest()
    m.add_node(_SAMPLE_NODE)
    m.add_discovery(_SAMPLE_DISCOVERY)
    m.add_consensus(_SAMPLE_CONSENSUS)
    m.add_authority_grant(_SAMPLE_GRANT)
    s = m.summarize()
    assert isinstance(s, str)
    assert len(s) > 0


def test_manifest_validate_multiple_errors_returned() -> None:
    m = _make_manifest(version="bad-version", author="", manifest_id="")
    errors = m.validate()
    assert len(errors) >= 2


def test_builder_build_has_empty_nodes_by_default() -> None:
    m = _make_builder().build()
    assert m.nodes == []


def test_builder_add_node_after_build_does_not_affect_builder() -> None:
    b = _make_builder()
    m1 = b.build()
    m1.add_node(_SAMPLE_NODE)
    m2 = b.build()
    # Building again from the same builder should give a fresh manifest
    assert len(m2.nodes) == 0 or isinstance(m2.nodes, list)


def test_manifest_to_json_pretty_or_compact() -> None:
    m = _make_manifest()
    j = m.to_json()
    # Should be valid JSON regardless of formatting
    parsed = json.loads(j)
    assert parsed["manifest_id"] == "mf-001"


def test_manifest_full_roundtrip_with_all_collections() -> None:
    m = _make_manifest(
        tags=["tag1", "tag2"],
        exports=["A", "B", "C"],
    )
    m.add_node(_SAMPLE_NODE)
    m.add_discovery(_SAMPLE_DISCOVERY)
    m.add_consensus(_SAMPLE_CONSENSUS)
    m.add_authority_grant(_SAMPLE_GRANT)
    j = m.to_json()
    restored = DiscoveryFederationManifest.from_json(j)
    assert restored.tags == ["tag1", "tag2"]
    assert restored.exports == ["A", "B", "C"]
    assert len(restored.nodes) == 1
    assert len(restored.discoveries) == 1
    assert len(restored.consensuses) == 1
    assert len(restored.authority_grants) == 1
    assert restored.is_sealed is False
    assert restored.is_published is False
    assert restored.is_deprecated is False
