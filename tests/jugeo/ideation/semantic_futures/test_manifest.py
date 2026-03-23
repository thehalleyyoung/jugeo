"""Tests for jugeo.ideation.semantic_futures.manifest (Ch. 49 — Semantic Futures).

Covers SemanticFuturesManifest, FutureSpaceDescriptor, ManifestValidator,
ManifestRegistry, module-level factory/utility functions, and private helpers.
Integration tests probe the manifest layer against the wider ideation stack.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

pytest.importorskip("jugeo.ideation.semantic_futures.manifest")

from jugeo.ideation.semantic_futures.manifest import (
    SemanticFuturesManifest,
    FutureSpaceDescriptor,
    ManifestValidator,
    ManifestRegistry,
    create_default_manifest,
    validate_manifest,
    merge_manifests,
    _parse_version,
    _version_tuple,
    _newer_version,
    _merge_export_lists,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _make_manifest(
    *,
    name: str = "test-manifest",
    version: str = "1.0.0",
    exports: tuple[str, ...] = ("SemanticFuture", "FutureState"),
    description: str = "A test manifest",
) -> SemanticFuturesManifest:
    return SemanticFuturesManifest(
        name=name,
        version=version,
        exports=exports,
        description=description,
    )


def _make_descriptor(
    *,
    name: str = "semantic-space",
    dimensions: int = 3,
    coordinate_names: tuple[str, ...] = ("purpose", "reachability", "yield"),
) -> FutureSpaceDescriptor:
    return FutureSpaceDescriptor(
        name=name,
        dimensions=dimensions,
        coordinate_names=coordinate_names,
    )


# ---------------------------------------------------------------------------
# TestSemanticFuturesManifest
# ---------------------------------------------------------------------------

class TestSemanticFuturesManifest:
    """Tests for the SemanticFuturesManifest dataclass.

    Verifies creation, serialization round-trips, validation, string
    representation, and immutability.
    """

    def test_basic_creation(self) -> None:
        """Manifest can be created with required fields."""
        m = _make_manifest()
        assert m.name == "test-manifest"
        assert m.version == "1.0.0"
        assert "SemanticFuture" in m.exports
        assert isinstance(m.description, str)

    def test_creation_with_minimal_fields(self) -> None:
        """Manifest can be created with name, version, and one export."""
        m = SemanticFuturesManifest(
            name="minimal",
            version="0.0.1",
            exports=("A",),
        )
        assert m.name == "minimal"
        assert len(m.exports) >= 1

    def test_to_dict_returns_dict(self) -> None:
        """to_dict returns a plain dict with expected keys."""
        m = _make_manifest()
        d = m.to_dict()
        assert isinstance(d, dict)
        assert "name" in d
        assert "version" in d
        assert "exports" in d

    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(m)) produces an equivalent manifest."""
        m = _make_manifest()
        restored = SemanticFuturesManifest.from_dict(m.to_dict())
        assert restored.name == m.name
        assert restored.version == m.version
        assert set(restored.exports) == set(m.exports)
        assert restored.description == m.description

    def test_str_returns_string(self) -> None:
        """__str__ produces a non-empty string representation."""
        m = _make_manifest()
        s = str(m)
        assert isinstance(s, str)
        assert len(s) > 0

    def test_validate_passes_for_good_manifest(self) -> None:
        """validate() returns an empty list for a well-formed manifest."""
        m = _make_manifest()
        issues = m.validate()
        assert isinstance(issues, list)
        assert issues == []

    def test_validate_catches_empty_name(self) -> None:
        """validate() reports an issue when the name is empty."""
        m = SemanticFuturesManifest(name="", version="1.0.0", exports=("A",))
        issues = m.validate()
        assert len(issues) > 0

    def test_validate_catches_empty_exports(self) -> None:
        """validate() reports an issue when the exports tuple is empty."""
        m = SemanticFuturesManifest(name="x", version="1.0.0", exports=())
        issues = m.validate()
        assert len(issues) > 0

    def test_validate_catches_bad_version_format(self) -> None:
        """validate() reports an issue for a version string not matching X.Y.Z."""
        m = SemanticFuturesManifest(name="x", version="not-a-version", exports=("A",))
        issues = m.validate()
        assert len(issues) > 0

    def test_different_versions(self) -> None:
        """Manifests with different versions are not equal."""
        a = _make_manifest(version="1.0.0")
        b = _make_manifest(version="2.0.0")
        assert a.version != b.version

    def test_frozen_immutability(self) -> None:
        """SemanticFuturesManifest is immutable (frozen dataclass)."""
        m = _make_manifest()
        with pytest.raises((AttributeError, TypeError)):
            m.name = "mutated"  # type: ignore[misc]

    def test_from_dict_rejects_missing_name(self) -> None:
        """from_dict raises if required key 'name' is absent."""
        with pytest.raises((KeyError, TypeError, ValueError)):
            SemanticFuturesManifest.from_dict({"version": "1.0.0", "exports": ["A"]})

    def test_exports_preserved_as_tuple(self) -> None:
        """exports field is stored as a tuple-like sequence."""
        m = _make_manifest(exports=("Alpha", "Beta", "Gamma"))
        assert "Alpha" in m.exports
        assert "Beta" in m.exports
        assert "Gamma" in m.exports


# ---------------------------------------------------------------------------
# TestFutureSpaceDescriptor
# ---------------------------------------------------------------------------

class TestFutureSpaceDescriptor:
    """Tests for FutureSpaceDescriptor dataclass.

    A descriptor encodes the shape and coordinate system of a future space.
    """

    def test_basic_creation(self) -> None:
        """Descriptor is created correctly with name, dimensions, coordinates."""
        d = _make_descriptor()
        assert d.name == "semantic-space"
        assert d.dimensions == 3
        assert len(d.coordinate_names) == 3

    def test_is_finite_dimensional_true_for_positive_dims(self) -> None:
        """is_finite_dimensional is True when dimensions > 0."""
        d = _make_descriptor(dimensions=5)
        assert d.is_finite_dimensional is True

    def test_is_finite_dimensional_false_for_zero(self) -> None:
        """is_finite_dimensional is False when dimensions == 0."""
        d = FutureSpaceDescriptor(name="empty", dimensions=0, coordinate_names=())
        assert d.is_finite_dimensional is False

    def test_to_dict_contains_dimensions(self) -> None:
        """to_dict includes dimensions key."""
        d = _make_descriptor()
        dd = d.to_dict()
        assert "dimensions" in dd
        assert dd["dimensions"] == 3

    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(d)) restores the descriptor faithfully."""
        d = _make_descriptor()
        restored = FutureSpaceDescriptor.from_dict(d.to_dict())
        assert restored.name == d.name
        assert restored.dimensions == d.dimensions
        assert tuple(restored.coordinate_names) == tuple(d.coordinate_names)

    def test_high_dimensional_descriptor(self) -> None:
        """Descriptor with many dimensions is valid."""
        coords = tuple(f"dim_{i}" for i in range(100))
        d = FutureSpaceDescriptor(
            name="hyperdimensional", dimensions=100, coordinate_names=coords
        )
        assert d.dimensions == 100
        assert d.is_finite_dimensional

    def test_frozen(self) -> None:
        """FutureSpaceDescriptor is immutable."""
        d = _make_descriptor()
        with pytest.raises((AttributeError, TypeError)):
            d.dimensions = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestManifestValidator
# ---------------------------------------------------------------------------

class TestManifestValidator:
    """Tests for ManifestValidator.

    The validator independently checks manifests without modifying them.
    """

    def test_valid_manifest_passes(self) -> None:
        """A well-formed manifest raises no issues."""
        v = ManifestValidator()
        m = _make_manifest()
        issues = v.validate(m)
        assert issues == []

    def test_is_valid_returns_true_for_good(self) -> None:
        """is_valid returns True for a valid manifest."""
        v = ManifestValidator()
        m = _make_manifest()
        assert v.is_valid(m) is True

    def test_is_valid_returns_false_for_empty_name(self) -> None:
        """is_valid returns False when name is empty."""
        v = ManifestValidator()
        m = SemanticFuturesManifest(name="", version="1.0.0", exports=("A",))
        assert v.is_valid(m) is False

    def test_invalid_version_format_fails(self) -> None:
        """Validator reports error for a version string not in X.Y.Z format."""
        v = ManifestValidator()
        m = SemanticFuturesManifest(name="x", version="v1", exports=("A",))
        issues = v.validate(m)
        assert len(issues) > 0

    def test_empty_exports_fails(self) -> None:
        """Validator reports error for an empty exports list."""
        v = ManifestValidator()
        m = SemanticFuturesManifest(name="x", version="1.0.0", exports=())
        issues = v.validate(m)
        assert len(issues) > 0

    def test_missing_fields_fails(self) -> None:
        """from_dict with missing fields raises before validation reaches it."""
        with pytest.raises((KeyError, TypeError, ValueError)):
            SemanticFuturesManifest.from_dict({})

    def test_validate_returns_list_of_strings(self) -> None:
        """validate() always returns a list of strings."""
        v = ManifestValidator()
        m = SemanticFuturesManifest(name="", version="bad", exports=())
        issues = v.validate(m)
        assert isinstance(issues, list)
        assert all(isinstance(i, str) for i in issues)

    def test_multiple_issues_reported(self) -> None:
        """Validator accumulates multiple issues instead of short-circuiting."""
        v = ManifestValidator()
        m = SemanticFuturesManifest(name="", version="bad-version", exports=())
        issues = v.validate(m)
        assert len(issues) >= 2


# ---------------------------------------------------------------------------
# TestManifestRegistry
# ---------------------------------------------------------------------------

class TestManifestRegistry:
    """Tests for ManifestRegistry — stores and retrieves named manifests.

    Covers register, get, list_all, remove, merge_all, and edge cases like
    empty registry and duplicate registration.
    """

    def test_empty_registry(self) -> None:
        """A freshly created registry lists no manifests."""
        reg = ManifestRegistry()
        assert reg.list_all() == []

    def test_register_and_get(self) -> None:
        """register then get returns the same manifest."""
        reg = ManifestRegistry()
        m = _make_manifest(name="alpha")
        reg.register(m)
        retrieved = reg.get("alpha")
        assert retrieved is not None
        assert retrieved.name == "alpha"

    def test_get_missing_returns_none(self) -> None:
        """get on an absent name returns None (not an exception)."""
        reg = ManifestRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all_single_item(self) -> None:
        """After registering one manifest, list_all returns length 1."""
        reg = ManifestRegistry()
        reg.register(_make_manifest(name="single"))
        assert len(reg.list_all()) == 1

    def test_list_all_multiple_items(self) -> None:
        """After registering several manifests, list_all returns all."""
        reg = ManifestRegistry()
        for i in range(5):
            reg.register(_make_manifest(name=f"m{i}", version=f"1.0.{i}"))
        all_manifests = reg.list_all()
        assert len(all_manifests) == 5

    def test_remove_existing(self) -> None:
        """remove() eliminates a registered manifest from the registry."""
        reg = ManifestRegistry()
        reg.register(_make_manifest(name="to-remove"))
        reg.remove("to-remove")
        assert reg.get("to-remove") is None

    def test_remove_missing_raises(self) -> None:
        """remove() raises KeyError (or ValueError) for an absent name."""
        reg = ManifestRegistry()
        with pytest.raises((KeyError, ValueError)):
            reg.remove("ghost")

    def test_duplicate_registration_raises(self) -> None:
        """Registering a manifest with an already-registered name raises."""
        reg = ManifestRegistry()
        m = _make_manifest(name="dup")
        reg.register(m)
        with pytest.raises((KeyError, ValueError)):
            reg.register(m)

    def test_merge_all_empty_registry(self) -> None:
        """merge_all on empty registry returns None."""
        reg = ManifestRegistry()
        result = reg.merge_all()
        assert result is None

    def test_merge_all_single_item(self) -> None:
        """merge_all with one manifest returns a manifest with same exports."""
        reg = ManifestRegistry()
        m = _make_manifest(name="solo", exports=("X", "Y"))
        reg.register(m)
        merged = reg.merge_all()
        assert merged is not None
        assert "X" in merged.exports
        assert "Y" in merged.exports

    def test_merge_all_multiple_items(self) -> None:
        """merge_all unions exports from all registered manifests."""
        reg = ManifestRegistry()
        reg.register(_make_manifest(name="a", exports=("A1", "A2")))
        reg.register(_make_manifest(name="b", exports=("B1", "B2")))
        merged = reg.merge_all()
        assert merged is not None
        for name in ("A1", "A2", "B1", "B2"):
            assert name in merged.exports


# ---------------------------------------------------------------------------
# TestModuleFunctions
# ---------------------------------------------------------------------------

class TestModuleFunctions:
    """Tests for module-level helper functions: create_default_manifest,
    validate_manifest, and merge_manifests.
    """

    def test_create_default_manifest_returns_manifest(self) -> None:
        """create_default_manifest() returns a SemanticFuturesManifest."""
        m = create_default_manifest()
        assert isinstance(m, SemanticFuturesManifest)

    def test_create_default_manifest_is_valid(self) -> None:
        """The default manifest passes validation without issues."""
        m = create_default_manifest()
        issues = validate_manifest(m)
        assert issues == []

    def test_validate_manifest_function(self) -> None:
        """validate_manifest() is equivalent to ManifestValidator().validate()."""
        m = _make_manifest()
        v = ManifestValidator()
        assert validate_manifest(m) == v.validate(m)

    def test_validate_manifest_bad_input(self) -> None:
        """validate_manifest() on a bad manifest returns non-empty issues."""
        m = SemanticFuturesManifest(name="", version="bad", exports=())
        issues = validate_manifest(m)
        assert len(issues) > 0

    def test_merge_manifests_unions_exports(self) -> None:
        """merge_manifests combines exports from both manifests."""
        a = _make_manifest(name="a", exports=("Foo", "Bar"), version="1.0.0")
        b = _make_manifest(name="b", exports=("Baz", "Qux"), version="1.1.0")
        merged = merge_manifests(a, b)
        for name in ("Foo", "Bar", "Baz", "Qux"):
            assert name in merged.exports

    def test_merge_manifests_version_conflict_takes_newer(self) -> None:
        """merge_manifests takes the higher version string."""
        a = _make_manifest(name="a", version="1.0.0")
        b = _make_manifest(name="b", version="2.3.1")
        merged = merge_manifests(a, b)
        assert merged.version == "2.3.1"

    def test_merge_manifests_identical_exports_no_duplicates(self) -> None:
        """Merging two manifests with the same exports yields no duplicates."""
        exports = ("X", "Y", "Z")
        a = _make_manifest(name="a", exports=exports)
        b = _make_manifest(name="b", exports=exports)
        merged = merge_manifests(a, b)
        assert len(set(merged.exports)) == len(list(merged.exports))

    def test_merge_manifests_empty_exports_one_side(self) -> None:
        """Merging a manifest with empty exports keeps the other side's exports."""
        a = _make_manifest(name="a", exports=("OnlyInA",))
        b = SemanticFuturesManifest(name="b", version="1.0.0", exports=())
        # b has no exports; merged should still have 'OnlyInA'
        # NOTE: b.validate() would flag empty exports, but merge should still work
        merged = merge_manifests(a, b)
        assert "OnlyInA" in merged.exports


# ---------------------------------------------------------------------------
# TestIntegrationManifest
# ---------------------------------------------------------------------------

class TestIntegrationManifest:
    """Integration tests: manifest layer working with the wider ideation stack."""

    def test_manifest_round_trip_preserves_validity(self) -> None:
        """A valid manifest serialised to dict and restored is still valid."""
        m = create_default_manifest()
        restored = SemanticFuturesManifest.from_dict(m.to_dict())
        assert validate_manifest(restored) == []

    def test_manifest_can_describe_semantic_futures_package(self) -> None:
        """A manifest can be constructed that describes the semantic_futures package."""
        from jugeo.ideation.semantic_futures import __version__, __all__ as pkg_all
        exports = tuple(
            name for name in pkg_all if not name.startswith("_")
        )[:10]  # take first 10 to keep fixture small
        m = SemanticFuturesManifest(
            name="jugeo.ideation.semantic_futures",
            version=__version__,
            exports=exports,
            description="Package manifest for semantic_futures",
        )
        issues = validate_manifest(m)
        assert issues == []

    def test_registry_stores_default_manifest(self) -> None:
        """The default manifest can be registered and retrieved by name."""
        reg = ManifestRegistry()
        m = create_default_manifest()
        reg.register(m)
        retrieved = reg.get(m.name)
        assert retrieved is not None
        assert retrieved.version == m.version

    def test_ideation_modules_guarded(self) -> None:
        """Importing ideation sibling modules succeeds or fails gracefully."""
        try:
            from jugeo.ideation.regimes import IdeationRegime  # noqa: F401
            _has_regimes = True
        except ImportError:
            _has_regimes = False

        # Whether or not regimes is available, the manifest layer is unaffected
        m = create_default_manifest()
        assert validate_manifest(m) == []
        _ = _has_regimes  # silence lint

    def test_merge_then_validate_is_clean(self) -> None:
        """Merging two valid manifests yields a manifest that is itself valid."""
        a = _make_manifest(name="a", exports=("SemanticFuture", "FutureState"))
        b = _make_manifest(
            name="b",
            version="1.2.0",
            exports=("PurposeFunction", "IdeationState"),
        )
        merged = merge_manifests(a, b)
        assert validate_manifest(merged) == []


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge-case tests: boundary versions, empty inputs, identical manifests."""

    def test_merge_identical_manifests(self) -> None:
        """Merging a manifest with itself yields no duplicate exports."""
        m = _make_manifest(exports=("A", "B", "C"))
        merged = merge_manifests(m, m)
        unique = set(merged.exports)
        assert len(unique) == len(list(merged.exports))

    @pytest.mark.parametrize("version", ["0.0.1", "1.0.0", "10.2.3", "99.99.99"])
    def test_valid_version_strings(self, version: str) -> None:
        """Manifests with various valid version strings pass validation."""
        m = SemanticFuturesManifest(name="test", version=version, exports=("A",))
        issues = validate_manifest(m)
        assert issues == []

    @pytest.mark.parametrize(
        "bad_version",
        ["", "1", "1.0", "1.0.0.0", "v1.0.0", "1.x.0", "latest"],
    )
    def test_invalid_version_strings(self, bad_version: str) -> None:
        """Manifests with malformed version strings fail validation."""
        m = SemanticFuturesManifest(name="test", version=bad_version, exports=("A",))
        issues = validate_manifest(m)
        assert len(issues) > 0

    def test_large_exports_list(self) -> None:
        """Manifest with 100 exports is valid and round-trips cleanly."""
        exports = tuple(f"Symbol{i}" for i in range(100))
        m = SemanticFuturesManifest(name="big", version="1.0.0", exports=exports)
        issues = validate_manifest(m)
        assert issues == []
        restored = SemanticFuturesManifest.from_dict(m.to_dict())
        assert len(restored.exports) == 100

    def test_description_optional(self) -> None:
        """A manifest without an explicit description is still valid."""
        m = SemanticFuturesManifest(name="nodesc", version="1.0.0", exports=("A",))
        assert validate_manifest(m) == []


# ---------------------------------------------------------------------------
# Tests for private helpers
# ---------------------------------------------------------------------------

class TestPrivateHelpers:
    """Tests for private helper functions in the manifest module."""

    @pytest.mark.parametrize(
        "version_str, expected",
        [
            ("1.0.0", (1, 0, 0)),
            ("0.0.1", (0, 0, 1)),
            ("10.2.3", (10, 2, 3)),
            ("99.99.99", (99, 99, 99)),
        ],
    )
    def test_parse_version(self, version_str: str, expected: tuple) -> None:
        """_parse_version converts 'X.Y.Z' string to an (X, Y, Z) int tuple."""
        result = _parse_version(version_str)
        assert result == expected

    @pytest.mark.parametrize(
        "version_str, expected",
        [
            ("2.0.0", (2, 0, 0)),
            ("0.1.0", (0, 1, 0)),
        ],
    )
    def test_version_tuple(self, version_str: str, expected: tuple) -> None:
        """_version_tuple is equivalent to _parse_version."""
        assert _version_tuple(version_str) == expected

    @pytest.mark.parametrize(
        "a, b, expected_newer",
        [
            ("1.0.0", "2.0.0", "2.0.0"),
            ("2.0.0", "1.0.0", "2.0.0"),
            ("1.0.0", "1.0.0", "1.0.0"),
            ("1.9.9", "2.0.0", "2.0.0"),
            ("0.0.1", "0.0.2", "0.0.2"),
            ("10.2.3", "10.2.2", "10.2.3"),
        ],
    )
    def test_newer_version(self, a: str, b: str, expected_newer: str) -> None:
        """_newer_version returns the higher version of two X.Y.Z strings."""
        assert _newer_version(a, b) == expected_newer

    def test_merge_export_lists_basic(self) -> None:
        """_merge_export_lists returns sorted, deduplicated union."""
        result = _merge_export_lists(("B", "A"), ("C", "A"))
        assert "A" in result
        assert "B" in result
        assert "C" in result
        # No duplicates
        assert len(set(result)) == len(list(result))

    def test_merge_export_lists_empty_inputs(self) -> None:
        """_merge_export_lists with two empty inputs returns empty."""
        result = _merge_export_lists((), ())
        assert list(result) == []

    def test_merge_export_lists_one_empty(self) -> None:
        """_merge_export_lists with one empty input returns the other."""
        result = _merge_export_lists(("X", "Y"), ())
        assert "X" in result
        assert "Y" in result
