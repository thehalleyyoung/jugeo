from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import json
import time

from jugeo.evaluation.evaluation_design.manifest import (
    EvaluationDesignManifest,
    EvaluationManifestBuilder,
    build_evaluation_manifest,
    validate_manifest,
    merge_manifests,
    EvaluationManifestRegistry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_manifest():
    """Return a fully populated EvaluationDesignManifest for use in tests."""
    return EvaluationDesignManifest.create(
        version="1.0",
        author="Alice",
        design_name="DesignA",
        exports=["export_a", "export_b"],
        description="A sample design manifest for testing.",
        tags=["tag1", "tag2"],
        clause_count=5,
        ablation_count=2,
        calibration_methods=["method1"],
    )


@pytest.fixture
def sample_registry():
    """Return a freshly constructed, empty EvaluationManifestRegistry."""
    return EvaluationManifestRegistry()


@pytest.fixture
def filled_registry(sample_manifest):
    """Return an EvaluationManifestRegistry pre-populated with three manifests."""
    reg = EvaluationManifestRegistry()
    reg.register(sample_manifest)

    second = EvaluationDesignManifest.create(
        version="2.0",
        author="Bob",
        design_name="DesignB",
        exports=["export_c"],
        description="Second design for registry tests.",
        tags=["tag2", "tag3"],
    )
    reg.register(second)

    third = EvaluationDesignManifest.create(
        version="1.1",
        author="Alice",
        design_name="DesignC",
        exports=["export_d"],
        description="Third design for registry tests.",
        tags=["tag1"],
    )
    reg.register(third)

    return reg


# ---------------------------------------------------------------------------
# TestEvaluationDesignManifestCreate
# ---------------------------------------------------------------------------


class TestEvaluationDesignManifestCreate:
    """Tests for the EvaluationDesignManifest.create factory method."""

    def test_create_returns_instance(self):
        """Verify that create() returns an EvaluationDesignManifest instance."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice")
        assert isinstance(m, EvaluationDesignManifest)

    def test_create_sets_version_and_author(self):
        """Verify that version and author are stored on the created manifest."""
        m = EvaluationDesignManifest.create(version="3.2", author="Bob")
        assert m.version == "3.2"
        assert m.author == "Bob"

    def test_create_assigns_nonempty_manifest_id(self):
        """Verify that create() assigns a non-empty manifest_id automatically."""
        m = EvaluationDesignManifest.create(version="1.0", author="Carol")
        assert m.manifest_id != ""

    def test_create_unique_manifest_ids(self):
        """Verify that two consecutive create() calls produce distinct manifest_ids."""
        m1 = EvaluationDesignManifest.create(version="1.0", author="Alice")
        m2 = EvaluationDesignManifest.create(version="1.0", author="Alice")
        assert m1.manifest_id != m2.manifest_id

    def test_create_default_chapter_ref(self):
        """Verify that chapter_ref defaults to 'Ch63' when not overridden."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice")
        assert m.chapter_ref == "Ch63"

    def test_create_custom_chapter_ref(self):
        """Verify that a custom chapter_ref passed via kwargs is stored correctly."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice", chapter_ref="Ch99")
        assert m.chapter_ref == "Ch99"

    def test_create_exports_default_empty(self):
        """Verify that exports defaults to an empty list when not supplied."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice")
        assert m.exports == []

    def test_create_exports_provided(self):
        """Verify that provided exports are stored in the manifest."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice", exports=["e1", "e2"])
        assert "e1" in m.exports and "e2" in m.exports

    def test_create_tags_default_empty(self):
        """Verify that tags defaults to an empty list when not supplied."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice")
        assert m.tags == []

    def test_create_tags_provided(self):
        """Verify that provided tags are stored in the manifest."""
        m = EvaluationDesignManifest.create(version="1.0", author="Alice", tags=["fast", "slow"])
        assert "fast" in m.tags

    def test_create_created_at_is_recent(self):
        """Verify that created_at is set to a timestamp close to now."""
        before = time.time()
        m = EvaluationDesignManifest.create(version="1.0", author="Alice")
        after = time.time()
        assert before <= m.created_at <= after

    def test_create_clause_and_ablation_via_kwargs(self):
        """Verify that clause_count and ablation_count are set via kwargs."""
        m = EvaluationDesignManifest.create(
            version="1.0", author="Alice", clause_count=10, ablation_count=4
        )
        assert m.clause_count == 10
        assert m.ablation_count == 4


# ---------------------------------------------------------------------------
# TestEvaluationDesignManifestValidate
# ---------------------------------------------------------------------------


class TestEvaluationDesignManifestValidate:
    """Tests for EvaluationDesignManifest.validate()."""

    def test_valid_manifest_no_errors(self, sample_manifest):
        """A fully populated manifest should return no validation errors."""
        errors = sample_manifest.validate()
        assert errors == []

    def test_empty_manifest_id_produces_error(self, sample_manifest):
        """An empty manifest_id must appear in the validation error list."""
        sample_manifest.manifest_id = ""
        errors = sample_manifest.validate()
        assert any("manifest_id" in e.lower() or "id" in e.lower() for e in errors)

    def test_empty_version_produces_error(self, sample_manifest):
        """An empty version must produce a validation error."""
        sample_manifest.version = ""
        errors = sample_manifest.validate()
        assert any("version" in e.lower() for e in errors)

    def test_empty_author_produces_error(self, sample_manifest):
        """An empty author must produce a validation error."""
        sample_manifest.author = ""
        errors = sample_manifest.validate()
        assert any("author" in e.lower() for e in errors)

    def test_empty_design_name_produces_warning(self, sample_manifest):
        """An empty design_name must appear in the validate() result."""
        sample_manifest.design_name = ""
        errors = sample_manifest.validate()
        assert any("design" in e.lower() for e in errors)

    def test_empty_description_produces_warning(self, sample_manifest):
        """An empty description must appear in the validate() result."""
        sample_manifest.description = ""
        errors = sample_manifest.validate()
        assert any("description" in e.lower() for e in errors)

    def test_negative_clause_count_produces_error(self, sample_manifest):
        """A negative clause_count must produce a validation error."""
        sample_manifest.clause_count = -1
        errors = sample_manifest.validate()
        assert any("clause" in e.lower() for e in errors)

    def test_negative_ablation_count_produces_error(self, sample_manifest):
        """A negative ablation_count must produce a validation error."""
        sample_manifest.ablation_count = -3
        errors = sample_manifest.validate()
        assert any("ablation" in e.lower() for e in errors)

    @pytest.mark.parametrize("bad_version", ["1", "alpha", "release", ""])
    def test_version_without_dot_produces_error(self, sample_manifest, bad_version):
        """Version strings that lack a '.' must produce a validation error."""
        sample_manifest.version = bad_version
        errors = sample_manifest.validate()
        assert len(errors) > 0

    @pytest.mark.parametrize("good_version", ["1.0", "2.3", "10.0.1", "0.1"])
    def test_valid_version_formats(self, sample_manifest, good_version):
        """Version strings with at least one '.' should not trigger a version error."""
        sample_manifest.version = good_version
        errors = sample_manifest.validate()
        assert not any("version" in e.lower() for e in errors)

    def test_empty_string_in_exports_produces_error(self, sample_manifest):
        """An empty string inside exports must produce a validation error."""
        sample_manifest.exports = ["good_export", ""]
        errors = sample_manifest.validate()
        assert any("export" in e.lower() for e in errors)

    def test_empty_string_in_tags_produces_error(self, sample_manifest):
        """An empty string inside tags must produce a validation error."""
        sample_manifest.tags = [""]
        errors = sample_manifest.validate()
        assert any("tag" in e.lower() for e in errors)

    def test_validate_function_delegates_to_method(self, sample_manifest):
        """validate_manifest() must return the same result as manifest.validate()."""
        assert validate_manifest(sample_manifest) == sample_manifest.validate()


# ---------------------------------------------------------------------------
# TestEvaluationDesignManifestMutations
# ---------------------------------------------------------------------------


class TestEvaluationDesignManifestMutations:
    """Tests for add_export, add_tag, and other mutating methods."""

    def test_add_export_appends_new_name(self, sample_manifest):
        """add_export should append a new export name to the exports list."""
        sample_manifest.add_export("new_export")
        assert "new_export" in sample_manifest.exports

    def test_add_export_is_idempotent(self, sample_manifest):
        """Calling add_export with an existing name must not create duplicates."""
        initial_len = len(sample_manifest.exports)
        existing = sample_manifest.exports[0]
        sample_manifest.add_export(existing)
        assert len(sample_manifest.exports) == initial_len

    def test_add_tag_appends_new_tag(self, sample_manifest):
        """add_tag should append a new tag to the tags list."""
        sample_manifest.add_tag("newtag")
        assert "newtag" in sample_manifest.tags

    def test_add_tag_is_idempotent(self, sample_manifest):
        """Calling add_tag with an existing tag must not create duplicates."""
        initial_len = len(sample_manifest.tags)
        existing = sample_manifest.tags[0]
        sample_manifest.add_tag(existing)
        assert len(sample_manifest.tags) == initial_len

    def test_add_multiple_exports(self, sample_manifest):
        """Multiple distinct add_export calls each append a unique entry."""
        sample_manifest.add_export("x1")
        sample_manifest.add_export("x2")
        assert "x1" in sample_manifest.exports
        assert "x2" in sample_manifest.exports

    def test_add_multiple_tags(self, sample_manifest):
        """Multiple distinct add_tag calls each append a unique entry."""
        sample_manifest.add_tag("alpha")
        sample_manifest.add_tag("beta")
        assert "alpha" in sample_manifest.tags
        assert "beta" in sample_manifest.tags


# ---------------------------------------------------------------------------
# TestEvaluationDesignManifestSerialisation
# ---------------------------------------------------------------------------


class TestEvaluationDesignManifestSerialisation:
    """Tests for to_json / from_json roundtrip."""

    def test_to_json_returns_string(self, sample_manifest):
        """to_json() must return a str."""
        assert isinstance(sample_manifest.to_json(), str)

    def test_to_json_is_valid_json(self, sample_manifest):
        """to_json() output must be parseable by json.loads."""
        data = json.loads(sample_manifest.to_json())
        assert isinstance(data, dict)

    def test_from_json_roundtrip_manifest_id(self, sample_manifest):
        """from_json(to_json()) must preserve manifest_id."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.manifest_id == sample_manifest.manifest_id

    def test_from_json_roundtrip_version(self, sample_manifest):
        """from_json(to_json()) must preserve version."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.version == sample_manifest.version

    def test_from_json_roundtrip_author(self, sample_manifest):
        """from_json(to_json()) must preserve author."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.author == sample_manifest.author

    def test_from_json_roundtrip_exports(self, sample_manifest):
        """from_json(to_json()) must preserve exports list."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.exports == sample_manifest.exports

    def test_from_json_roundtrip_tags(self, sample_manifest):
        """from_json(to_json()) must preserve tags list."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.tags == sample_manifest.tags

    def test_from_json_roundtrip_clause_count(self, sample_manifest):
        """from_json(to_json()) must preserve clause_count."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.clause_count == sample_manifest.clause_count

    def test_from_json_roundtrip_description(self, sample_manifest):
        """from_json(to_json()) must preserve description."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.description == sample_manifest.description

    def test_from_json_roundtrip_created_at(self, sample_manifest):
        """from_json(to_json()) must preserve created_at timestamp."""
        restored = EvaluationDesignManifest.from_json(sample_manifest.to_json())
        assert restored.created_at == sample_manifest.created_at


# ---------------------------------------------------------------------------
# TestEvaluationDesignManifestRendering
# ---------------------------------------------------------------------------


class TestEvaluationDesignManifestRendering:
    """Tests for summarize(), render_tex(), is_complete(), and to_registry_entry()."""

    def test_summarize_returns_string(self, sample_manifest):
        """summarize() must return a non-empty string."""
        result = sample_manifest.summarize()
        assert isinstance(result, str) and len(result) > 0

    def test_summarize_contains_design_name(self, sample_manifest):
        """summarize() output must contain the design_name."""
        assert sample_manifest.design_name in sample_manifest.summarize()

    def test_render_tex_returns_string(self, sample_manifest):
        """render_tex() must return a non-empty string."""
        result = sample_manifest.render_tex()
        assert isinstance(result, str) and len(result) > 0

    def test_is_complete_true_for_full_manifest(self, sample_manifest):
        """is_complete() must return True for a fully populated manifest."""
        assert sample_manifest.is_complete() is True

    def test_is_complete_false_when_missing_description(self, sample_manifest):
        """is_complete() must return False when description is empty."""
        sample_manifest.description = ""
        assert sample_manifest.is_complete() is False

    def test_is_complete_false_when_missing_design_name(self, sample_manifest):
        """is_complete() must return False when design_name is empty."""
        sample_manifest.design_name = ""
        assert sample_manifest.is_complete() is False

    def test_is_complete_false_when_exports_empty(self, sample_manifest):
        """is_complete() must return False when exports list is empty."""
        sample_manifest.exports = []
        assert sample_manifest.is_complete() is False

    def test_to_registry_entry_type_key(self, sample_manifest):
        """to_registry_entry() must include a 'type' key."""
        entry = sample_manifest.to_registry_entry()
        assert "type" in entry

    def test_to_registry_entry_all_required_keys(self, sample_manifest):
        """to_registry_entry() must include all documented keys."""
        required = {
            "type", "manifest_id", "version", "author", "chapter_ref",
            "theory_section", "design_name", "exports", "created_at",
            "description", "clause_count", "ablation_count",
            "calibration_methods", "tags",
        }
        entry = sample_manifest.to_registry_entry()
        assert required.issubset(entry.keys())

    def test_to_registry_entry_values_match(self, sample_manifest):
        """to_registry_entry() values must match the manifest fields."""
        entry = sample_manifest.to_registry_entry()
        assert entry["manifest_id"] == sample_manifest.manifest_id
        assert entry["version"] == sample_manifest.version
        assert entry["author"] == sample_manifest.author


# ---------------------------------------------------------------------------
# TestEvaluationManifestBuilder
# ---------------------------------------------------------------------------


class TestEvaluationManifestBuilder:
    """Tests for EvaluationManifestBuilder fluent API and build()."""

    def test_builder_basic_construction(self):
        """Constructing a builder with version and author should not raise."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        assert builder.version == "1.0"
        assert builder.author == "Alice"

    def test_builder_set_design_name_returns_self(self):
        """set_design_name() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.set_design_name("MyDesign")
        assert result is builder

    def test_builder_add_export_returns_self(self):
        """add_export() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.add_export("export_x")
        assert result is builder

    def test_builder_add_tag_returns_self(self):
        """add_tag() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.add_tag("mytag")
        assert result is builder

    def test_builder_set_clause_count_returns_self(self):
        """set_clause_count() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.set_clause_count(5)
        assert result is builder

    def test_builder_set_ablation_count_returns_self(self):
        """set_ablation_count() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.set_ablation_count(3)
        assert result is builder

    def test_builder_set_calibration_methods_returns_self(self):
        """set_calibration_methods() must return the builder itself for chaining."""
        builder = EvaluationManifestBuilder(version="1.0", author="Alice")
        result = builder.set_calibration_methods(["m1"])
        assert result is builder

    def test_builder_build_returns_manifest(self):
        """build() must return an EvaluationDesignManifest instance."""
        manifest = (
            EvaluationManifestBuilder(version="2.0", author="Bob")
            .set_design_name("D1")
            .add_export("e1")
            .build()
        )
        assert isinstance(manifest, EvaluationDesignManifest)

    def test_builder_build_stores_design_name(self):
        """build() must produce a manifest with the set design_name."""
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_design_name("TestDesign")
            .build()
        )
        assert manifest.design_name == "TestDesign"

    def test_builder_add_export_idempotent(self):
        """Adding the same export twice must produce only one entry in the manifest."""
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .add_export("dup")
            .add_export("dup")
            .build()
        )
        assert manifest.exports.count("dup") == 1

    def test_builder_add_tag_idempotent(self):
        """Adding the same tag twice must produce only one entry in the manifest."""
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .add_tag("dup_tag")
            .add_tag("dup_tag")
            .build()
        )
        assert manifest.tags.count("dup_tag") == 1

    def test_builder_clause_count_clamped_to_zero(self):
        """set_clause_count with a negative value must clamp to 0."""
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_clause_count(-10)
            .build()
        )
        assert manifest.clause_count >= 0

    def test_builder_ablation_count_clamped_to_zero(self):
        """set_ablation_count with a negative value must clamp to 0."""
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_ablation_count(-5)
            .build()
        )
        assert manifest.ablation_count >= 0

    def test_builder_set_calibration_methods_defensive_copy(self):
        """set_calibration_methods stores a copy; mutating the original list must not affect the manifest."""
        methods = ["cal1", "cal2"]
        manifest = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_calibration_methods(methods)
            .build()
        )
        methods.append("cal3")
        assert "cal3" not in manifest.calibration_methods

    def test_builder_reset_clears_state(self):
        """reset() must clear accumulated state so the next build uses defaults."""
        builder = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_design_name("Before")
            .add_export("e1")
            .add_tag("t1")
        )
        builder.reset()
        manifest = builder.build()
        assert manifest.design_name == ""
        assert manifest.exports == []
        assert manifest.tags == []

    def test_builder_reset_preserves_version_author(self):
        """reset() must not change the builder's version or author."""
        builder = EvaluationManifestBuilder(version="9.9", author="Zara")
        builder.reset()
        assert builder.version == "9.9"
        assert builder.author == "Zara"

    def test_builder_clone_is_independent(self):
        """clone() must produce a separate builder; changes to the clone must not affect the original."""
        original = (
            EvaluationManifestBuilder(version="1.0", author="Alice")
            .set_design_name("OriginalDesign")
            .add_export("e_orig")
        )
        cloned = original.clone()
        cloned.set_design_name("ClonedDesign").add_export("e_clone")
        m_orig = original.build()
        m_clone = cloned.build()
        assert m_orig.design_name == "OriginalDesign"
        assert m_clone.design_name == "ClonedDesign"
        assert "e_clone" not in m_orig.exports

    def test_builder_fluent_chain(self):
        """A full fluent chain must produce a manifest with all fields set correctly."""
        manifest = (
            EvaluationManifestBuilder(version="2.1", author="Charlie")
            .set_design_name("ChainDesign")
            .add_export("out1")
            .add_export("out2")
            .add_tag("production")
            .set_clause_count(7)
            .set_ablation_count(3)
            .set_calibration_methods(["cm1", "cm2"])
            .build()
        )
        assert manifest.version == "2.1"
        assert manifest.author == "Charlie"
        assert manifest.design_name == "ChainDesign"
        assert "out1" in manifest.exports and "out2" in manifest.exports
        assert "production" in manifest.tags
        assert manifest.clause_count == 7
        assert manifest.ablation_count == 3
        assert manifest.calibration_methods == ["cm1", "cm2"]


# ---------------------------------------------------------------------------
# TestBuildEvaluationManifest
# ---------------------------------------------------------------------------


class TestBuildEvaluationManifest:
    """Tests for the build_evaluation_manifest convenience function."""

    def test_returns_manifest_instance(self):
        """build_evaluation_manifest must return an EvaluationDesignManifest."""
        m = build_evaluation_manifest("D", "1.0", "Alice", ["e1"])
        assert isinstance(m, EvaluationDesignManifest)

    def test_sets_design_name(self):
        """build_evaluation_manifest must set design_name correctly."""
        m = build_evaluation_manifest("MyDesign", "1.0", "Alice", ["e1"])
        assert m.design_name == "MyDesign"

    def test_sets_version(self):
        """build_evaluation_manifest must set version correctly."""
        m = build_evaluation_manifest("D", "3.5", "Alice", ["e1"])
        assert m.version == "3.5"

    def test_sets_author(self):
        """build_evaluation_manifest must set author correctly."""
        m = build_evaluation_manifest("D", "1.0", "Dave", ["e1"])
        assert m.author == "Dave"

    def test_sets_exports(self):
        """build_evaluation_manifest must store the provided exports list."""
        m = build_evaluation_manifest("D", "1.0", "Alice", ["x", "y"])
        assert "x" in m.exports and "y" in m.exports

    def test_kwargs_forwarded(self):
        """Extra kwargs must be forwarded to the resulting manifest."""
        m = build_evaluation_manifest(
            "D", "1.0", "Alice", ["e1"], clause_count=8, description="hello"
        )
        assert m.clause_count == 8
        assert m.description == "hello"

    @pytest.mark.parametrize(
        "design_name,version,author,exports",
        [
            ("D1", "1.0", "Alice", ["a"]),
            ("D2", "2.3", "Bob", ["b", "c"]),
            ("D3", "0.1", "Carol", []),
        ],
    )
    def test_parametrized_construction(self, design_name, version, author, exports):
        """build_evaluation_manifest must work across varied inputs without raising."""
        m = build_evaluation_manifest(design_name, version, author, exports)
        assert m.design_name == design_name
        assert m.version == version
        assert m.author == author


# ---------------------------------------------------------------------------
# TestMergeManifests
# ---------------------------------------------------------------------------


class TestMergeManifests:
    """Tests for merge_manifests(a, b)."""

    @pytest.fixture
    def manifest_a(self):
        """Manifest A used as the primary source in merge tests."""
        return EvaluationDesignManifest.create(
            version="1.0",
            author="Alice",
            design_name="DesignA",
            exports=["e1", "e2"],
            description="Manifest A",
            tags=["t1", "t2"],
            clause_count=3,
            ablation_count=1,
            calibration_methods=["cm1"],
            chapter_ref="Ch10",
        )

    @pytest.fixture
    def manifest_b(self):
        """Manifest B used as the secondary source in merge tests."""
        return EvaluationDesignManifest.create(
            version="2.0",
            author="Bob",
            design_name="DesignB",
            exports=["e2", "e3"],
            description="Manifest B",
            tags=["t2", "t3"],
            clause_count=4,
            ablation_count=2,
            calibration_methods=["cm1", "cm2"],
            chapter_ref="Ch20",
        )

    def test_merge_returns_manifest(self, manifest_a, manifest_b):
        """merge_manifests must return an EvaluationDesignManifest."""
        result = merge_manifests(manifest_a, manifest_b)
        assert isinstance(result, EvaluationDesignManifest)

    def test_merge_new_manifest_id(self, manifest_a, manifest_b):
        """merge_manifests must produce a new manifest_id distinct from both inputs."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.manifest_id not in (manifest_a.manifest_id, manifest_b.manifest_id)

    def test_merge_version_from_a(self, manifest_a, manifest_b):
        """Merged manifest must use version from manifest_a."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.version == manifest_a.version

    def test_merge_chapter_ref_from_a(self, manifest_a, manifest_b):
        """Merged manifest must use chapter_ref from manifest_a."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.chapter_ref == manifest_a.chapter_ref

    def test_merge_author_combined_when_different(self, manifest_a, manifest_b):
        """When authors differ, merged author must contain both names."""
        result = merge_manifests(manifest_a, manifest_b)
        assert manifest_a.author in result.author
        assert manifest_b.author in result.author

    def test_merge_author_unchanged_when_same(self, manifest_a):
        """When both manifests share the same author, merged author equals that author."""
        manifest_b_same = EvaluationDesignManifest.create(
            version="1.5", author="Alice", design_name="D2", exports=["e9"]
        )
        result = merge_manifests(manifest_a, manifest_b_same)
        assert result.author == "Alice"

    def test_merge_design_name_from_a_when_nonempty(self, manifest_a, manifest_b):
        """When a's design_name is non-empty, it must be used in the merge."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.design_name == manifest_a.design_name

    def test_merge_design_name_from_b_when_a_empty(self, manifest_a, manifest_b):
        """When a's design_name is empty, b's design_name must be used."""
        manifest_a.design_name = ""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.design_name == manifest_b.design_name

    def test_merge_exports_union(self, manifest_a, manifest_b):
        """Merged exports must contain all exports from both manifests (deduped)."""
        result = merge_manifests(manifest_a, manifest_b)
        for exp in manifest_a.exports + manifest_b.exports:
            assert exp in result.exports

    def test_merge_exports_no_duplicates(self, manifest_a, manifest_b):
        """Merged exports must not contain duplicates."""
        result = merge_manifests(manifest_a, manifest_b)
        assert len(result.exports) == len(set(result.exports))

    def test_merge_tags_union(self, manifest_a, manifest_b):
        """Merged tags must contain all tags from both manifests (deduped)."""
        result = merge_manifests(manifest_a, manifest_b)
        for tag in manifest_a.tags + manifest_b.tags:
            assert tag in result.tags

    def test_merge_tags_no_duplicates(self, manifest_a, manifest_b):
        """Merged tags must not contain duplicates."""
        result = merge_manifests(manifest_a, manifest_b)
        assert len(result.tags) == len(set(result.tags))

    def test_merge_clause_count_summed(self, manifest_a, manifest_b):
        """Merged clause_count must equal a.clause_count + b.clause_count."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.clause_count == manifest_a.clause_count + manifest_b.clause_count

    def test_merge_ablation_count_summed(self, manifest_a, manifest_b):
        """Merged ablation_count must equal a.ablation_count + b.ablation_count."""
        result = merge_manifests(manifest_a, manifest_b)
        assert result.ablation_count == manifest_a.ablation_count + manifest_b.ablation_count

    def test_merge_calibration_methods_union(self, manifest_a, manifest_b):
        """Merged calibration_methods must be the union of both manifests' methods."""
        result = merge_manifests(manifest_a, manifest_b)
        for cm in manifest_a.calibration_methods + manifest_b.calibration_methods:
            assert cm in result.calibration_methods


# ---------------------------------------------------------------------------
# TestEvaluationManifestRegistry
# ---------------------------------------------------------------------------


class TestEvaluationManifestRegistry:
    """Tests for EvaluationManifestRegistry CRUD and query methods."""

    def test_empty_registry_is_empty(self, sample_registry):
        """A newly created registry must report is_empty() == True."""
        assert sample_registry.is_empty() is True

    def test_empty_registry_count_zero(self, sample_registry):
        """A newly created registry must have count() == 0."""
        assert sample_registry.count() == 0

    def test_register_increases_count(self, sample_registry, sample_manifest):
        """Registering a manifest must increase count by one."""
        sample_registry.register(sample_manifest)
        assert sample_registry.count() == 1

    def test_register_then_not_empty(self, sample_registry, sample_manifest):
        """After registering one manifest, is_empty() must return False."""
        sample_registry.register(sample_manifest)
        assert sample_registry.is_empty() is False

    def test_has_after_register(self, sample_registry, sample_manifest):
        """has() must return True immediately after a manifest is registered."""
        sample_registry.register(sample_manifest)
        assert sample_registry.has(sample_manifest.manifest_id)

    def test_get_returns_manifest(self, sample_registry, sample_manifest):
        """get() must return the exact manifest that was registered."""
        sample_registry.register(sample_manifest)
        retrieved = sample_registry.get(sample_manifest.manifest_id)
        assert retrieved is not None
        assert retrieved.manifest_id == sample_manifest.manifest_id

    def test_get_missing_returns_none(self, sample_registry):
        """get() must return None for an unregistered manifest_id."""
        assert sample_registry.get("nonexistent-id") is None

    def test_remove_existing_returns_true(self, sample_registry, sample_manifest):
        """remove() must return True when the manifest_id exists."""
        sample_registry.register(sample_manifest)
        assert sample_registry.remove(sample_manifest.manifest_id) is True

    def test_remove_decreases_count(self, sample_registry, sample_manifest):
        """After remove(), count must decrease by one."""
        sample_registry.register(sample_manifest)
        sample_registry.remove(sample_manifest.manifest_id)
        assert sample_registry.count() == 0

    def test_remove_missing_returns_false(self, sample_registry):
        """remove() must return False when the manifest_id does not exist."""
        assert sample_registry.remove("ghost-id") is False

    def test_register_overwrites_existing(self, sample_registry, sample_manifest):
        """Registering a manifest with an existing id must overwrite it."""
        sample_registry.register(sample_manifest)
        sample_manifest.author = "Updated"
        sample_registry.register(sample_manifest)
        assert sample_registry.count() == 1
        assert sample_registry.get(sample_manifest.manifest_id).author == "Updated"

    def test_list_all_returns_all_manifests(self, filled_registry):
        """list_all() must return all three manifests in the filled registry."""
        assert len(filled_registry.list_all()) == 3

    def test_find_by_tag_returns_correct_manifests(self, filled_registry):
        """find_by_tag('tag1') must return only manifests that carry 'tag1'."""
        results = filled_registry.find_by_tag("tag1")
        assert all("tag1" in m.tags for m in results)

    def test_find_by_tag_empty_list_for_unknown_tag(self, filled_registry):
        """find_by_tag with a tag not present must return an empty list."""
        assert filled_registry.find_by_tag("nonexistent_tag") == []

    def test_find_by_author(self, filled_registry):
        """find_by_author('Alice') must return exactly the manifests authored by Alice."""
        results = filled_registry.find_by_author("Alice")
        assert all(m.author == "Alice" for m in results)
        assert len(results) == 2

    def test_find_by_design_name(self, filled_registry):
        """find_by_design_name must return manifests matching the given design_name."""
        results = filled_registry.find_by_design_name("DesignB")
        assert len(results) == 1
        assert results[0].design_name == "DesignB"

    def test_find_by_chapter_ref(self, filled_registry, sample_manifest):
        """find_by_chapter_ref must return manifests with the given chapter_ref."""
        results = filled_registry.find_by_chapter_ref("Ch63")
        assert all(m.chapter_ref == "Ch63" for m in results)

    def test_latest_returns_most_recent(self, sample_registry):
        """latest() must return the manifest with the highest created_at value."""
        m1 = EvaluationDesignManifest.create(version="1.0", author="A", design_name="D1")
        time.sleep(0.01)
        m2 = EvaluationDesignManifest.create(version="1.0", author="A", design_name="D2")
        sample_registry.register(m1)
        sample_registry.register(m2)
        assert sample_registry.latest().manifest_id == m2.manifest_id

    def test_latest_on_empty_registry_returns_none(self, sample_registry):
        """latest() on an empty registry must return None."""
        assert sample_registry.latest() is None


# ---------------------------------------------------------------------------
# TestRegistrySerialisation
# ---------------------------------------------------------------------------


class TestRegistrySerialisation:
    """Tests for EvaluationManifestRegistry JSON serialisation roundtrip."""

    def test_to_json_returns_string(self, filled_registry):
        """to_json() must return a str."""
        assert isinstance(filled_registry.to_json(), str)

    def test_to_json_is_valid_json(self, filled_registry):
        """to_json() output must be parseable with json.loads."""
        data = json.loads(filled_registry.to_json())
        assert isinstance(data, (dict, list))

    def test_from_json_roundtrip_count(self, filled_registry):
        """Roundtrip via to_json/from_json must preserve the manifest count."""
        restored = EvaluationManifestRegistry.from_json(filled_registry.to_json())
        assert restored.count() == filled_registry.count()

    def test_from_json_roundtrip_manifest_ids(self, filled_registry):
        """All manifest_ids must be present in the restored registry."""
        original_ids = {m.manifest_id for m in filled_registry.list_all()}
        restored = EvaluationManifestRegistry.from_json(filled_registry.to_json())
        restored_ids = {m.manifest_id for m in restored.list_all()}
        assert original_ids == restored_ids

    def test_from_json_empty_registry_roundtrip(self, sample_registry):
        """Roundtripping an empty registry must produce another empty registry."""
        restored = EvaluationManifestRegistry.from_json(sample_registry.to_json())
        assert restored.is_empty()

    def test_from_json_restores_authors(self, filled_registry):
        """Authors must be preserved across the JSON roundtrip."""
        restored = EvaluationManifestRegistry.from_json(filled_registry.to_json())
        original_authors = {m.manifest_id: m.author for m in filled_registry.list_all()}
        for m in restored.list_all():
            assert m.author == original_authors[m.manifest_id]
