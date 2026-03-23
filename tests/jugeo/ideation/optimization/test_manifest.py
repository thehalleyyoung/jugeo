"""Tests for jugeo.ideation.optimization.manifest (Ch50)."""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
from jugeo.ideation.optimization.manifest import (
    AlgorithmDescriptor,
    OptimizationManifest,
    ManifestValidator,
    ManifestRegistry,
    AlgorithmRegistry,
    create_default_manifest,
    register_algorithm,
    lookup_algorithm,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_descriptor(
    algorithm_id: str = "test-algo",
    name: str = "Test Algorithm",
    complexity: str = "O(n)",
    params: dict | None = None,
    description: str = "A test algorithm.",
) -> AlgorithmDescriptor:
    """Return a minimal, valid AlgorithmDescriptor."""
    return AlgorithmDescriptor(
        algorithm_id=algorithm_id,
        name=name,
        complexity=complexity,
        parameters=params if params is not None else {"alpha": 0.5},
        description=description,
    )


def _make_manifest(
    version: str = "1.0.0",
    package_id: str = "test-pkg",
    description: str = "Test manifest.",
) -> OptimizationManifest:
    """Return a fresh, empty OptimizationManifest."""
    return OptimizationManifest(version=version, package_id=package_id, description=description)


def _make_algorithm_registry(*descriptors: AlgorithmDescriptor) -> AlgorithmRegistry:
    """Return an AlgorithmRegistry pre-loaded with the given descriptors."""
    registry = AlgorithmRegistry()
    for desc in descriptors:
        registry.register(desc)
    return registry


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestUnitManifest:
    """Unit tests for individual manifest classes and their public methods."""

    # -- AlgorithmDescriptor -------------------------------------------------

    def test_algorithm_descriptor_creation(self):
        """AlgorithmDescriptor stores all fields verbatim."""
        desc = _make_descriptor(
            algorithm_id="my-algo",
            name="My Algorithm",
            complexity="O(n^2)",
            params={"k": 10, "tol": 1e-4},
            description="Quadratic search algorithm.",
        )
        assert desc.algorithm_id == "my-algo"
        assert desc.name == "My Algorithm"
        assert desc.complexity == "O(n^2)"
        assert desc.parameters == {"k": 10, "tol": 1e-4}
        assert desc.description == "Quadratic search algorithm."

    def test_algorithm_descriptor_is_frozen(self):
        """AlgorithmDescriptor is frozen — attribute assignment raises."""
        desc = _make_descriptor()
        with pytest.raises((AttributeError, TypeError)):
            desc.name = "Modified"  # type: ignore[misc]

    def test_algorithm_descriptor_summary(self):
        """summary() returns a string mentioning the id, name and complexity."""
        desc = _make_descriptor(algorithm_id="algo-x", name="Algo X", complexity="O(n*k)")
        result = desc.summary()
        assert isinstance(result, str)
        assert "algo-x" in result
        assert "Algo X" in result
        assert "O(n*k)" in result

    def test_algorithm_descriptor_summary_contains_param_count(self):
        """summary() includes the number of parameters."""
        desc = _make_descriptor(params={"a": 1, "b": 2, "c": 3})
        summary = desc.summary()
        assert "3" in summary

    def test_algorithm_descriptor_parameter_count(self):
        """parameter_count() returns the number of keys in parameters."""
        desc = _make_descriptor(params={})
        assert desc.parameter_count() == 0

        desc2 = _make_descriptor(params={"lr": 0.01, "epochs": 100, "batch": 32})
        assert desc2.parameter_count() == 3

    def test_algorithm_descriptor_is_polynomial_true(self):
        """is_polynomial() returns True for O(n...) and O(log...) complexities."""
        assert _make_descriptor(complexity="O(n)").is_polynomial() is True
        assert _make_descriptor(complexity="O(n^2)").is_polynomial() is True
        assert _make_descriptor(complexity="O(n*k)").is_polynomial() is True
        assert _make_descriptor(complexity="O(log n)").is_polynomial() is True

    def test_algorithm_descriptor_is_polynomial_false(self):
        """is_polynomial() returns False for exponential or non-polynomial strings."""
        assert _make_descriptor(complexity="O(2^n)").is_polynomial() is False
        assert _make_descriptor(complexity="O(n!)").is_polynomial() is False
        assert _make_descriptor(complexity="O(k^n)").is_polynomial() is False

    # -- OptimizationManifest ------------------------------------------------

    def test_manifest_register_and_lookup(self):
        """register() stores a descriptor; lookup() retrieves it by id."""
        manifest = _make_manifest()
        desc = _make_descriptor(algorithm_id="algo-a")
        manifest.register(desc)
        found = manifest.lookup("algo-a")
        assert found is desc

    def test_manifest_lookup_missing_returns_none(self):
        """lookup() returns None for an algorithm_id that was never registered."""
        manifest = _make_manifest()
        assert manifest.lookup("does-not-exist") is None

    def test_manifest_remove(self):
        """remove() deletes a registered algorithm and returns True; second call False."""
        manifest = _make_manifest()
        manifest.register(_make_descriptor(algorithm_id="removable"))
        assert manifest.algorithm_count() == 1

        removed = manifest.remove("removable")
        assert removed is True
        assert manifest.lookup("removable") is None
        assert manifest.algorithm_count() == 0

        not_found = manifest.remove("removable")
        assert not_found is False

    def test_manifest_remove_nonexistent_returns_false(self):
        """remove() on an unknown id returns False without raising."""
        manifest = _make_manifest()
        assert manifest.remove("ghost") is False

    def test_manifest_algorithm_count(self):
        """algorithm_count() tracks registrations correctly."""
        manifest = _make_manifest()
        assert manifest.algorithm_count() == 0
        manifest.register(_make_descriptor("a1"))
        assert manifest.algorithm_count() == 1
        manifest.register(_make_descriptor("a2"))
        assert manifest.algorithm_count() == 2

    def test_manifest_summary_nonempty(self):
        """summary() returns a non-empty string containing version and package_id."""
        manifest = _make_manifest(version="2.3.1", package_id="my-pkg")
        manifest.register(_make_descriptor())
        result = manifest.summary()
        assert isinstance(result, str)
        assert len(result) > 0
        assert "2.3.1" in result
        assert "my-pkg" in result

    def test_manifest_summary_lists_algorithms(self):
        """summary() mentions each registered algorithm id."""
        manifest = _make_manifest()
        manifest.register(_make_descriptor("algo-a"))
        manifest.register(_make_descriptor("algo-b"))
        result = manifest.summary()
        assert "algo-a" in result
        assert "algo-b" in result

    def test_manifest_copilot_report(self):
        """copilot_report() returns a Markdown-formatted string with version info."""
        manifest = _make_manifest(version="1.0.0", package_id="copilot-pkg")
        manifest.register(_make_descriptor("cp-algo"))
        report = manifest.copilot_report()
        assert isinstance(report, str)
        assert "1.0.0" in report
        assert "copilot-pkg" in report
        assert "cp-algo" in report

    def test_manifest_copilot_report_empty_registry(self):
        """copilot_report() works when no algorithms are registered."""
        manifest = _make_manifest()
        report = manifest.copilot_report()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_manifest_register_overwrites_duplicate(self):
        """Registering a second descriptor with the same id overwrites the first."""
        manifest = _make_manifest()
        first = _make_descriptor("dup-id", name="First Version")
        second = _make_descriptor("dup-id", name="Second Version")
        manifest.register(first)
        manifest.register(second)
        assert manifest.algorithm_count() == 1
        assert manifest.lookup("dup-id").name == "Second Version"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegrationManifest:
    """Integration tests covering cross-class interactions."""

    @pytest.fixture(autouse=True)
    def _clear_singleton(self):
        """Ensure ManifestRegistry singleton is clean before and after each test."""
        ManifestRegistry.instance().clear()
        yield
        ManifestRegistry.instance().clear()

    def test_manifest_registry_add_and_get(self):
        """ManifestRegistry.add() stores; .get() retrieves by package_id."""
        registry = ManifestRegistry.instance()
        manifest = _make_manifest(package_id="pkg-alpha")
        registry.add(manifest)
        found = registry.get("pkg-alpha")
        assert found is manifest

    def test_manifest_registry_get_missing_returns_none(self):
        """ManifestRegistry.get() returns None for an unknown package_id."""
        registry = ManifestRegistry.instance()
        assert registry.get("no-such-package") is None

    def test_manifest_registry_list_all(self):
        """ManifestRegistry.list_all() returns every registered manifest."""
        registry = ManifestRegistry.instance()
        m1 = _make_manifest(package_id="pkg-1")
        m2 = _make_manifest(package_id="pkg-2")
        m3 = _make_manifest(package_id="pkg-3")
        registry.add(m1)
        registry.add(m2)
        registry.add(m3)
        all_manifests = registry.list_all()
        assert len(all_manifests) == 3
        package_ids = {m.package_id for m in all_manifests}
        assert package_ids == {"pkg-1", "pkg-2", "pkg-3"}

    def test_manifest_registry_duplicate(self):
        """Adding two manifests with the same package_id keeps the later one."""
        registry = ManifestRegistry.instance()
        old = _make_manifest(package_id="shared-id", description="old")
        new = _make_manifest(package_id="shared-id", description="new")
        registry.add(old)
        registry.add(new)
        assert len(registry.list_all()) == 1
        found = registry.get("shared-id")
        assert found.description == "new"

    def test_manifest_registry_remove(self):
        """ManifestRegistry.remove() deletes a manifest by package_id."""
        registry = ManifestRegistry.instance()
        manifest = _make_manifest(package_id="removable-pkg")
        registry.add(manifest)
        removed = registry.remove("removable-pkg")
        assert removed is True
        assert registry.get("removable-pkg") is None
        assert len(registry.list_all()) == 0

    def test_create_default_manifest_has_algorithms(self):
        """create_default_manifest() returns a manifest with ≥ 5 algorithms."""
        manifest = create_default_manifest()
        assert isinstance(manifest, OptimizationManifest)
        assert manifest.algorithm_count() >= 5
        for desc in manifest.algorithm_registry.values():
            assert isinstance(desc, AlgorithmDescriptor)
            assert desc.algorithm_id
            assert desc.name

    def test_create_default_manifest_known_ids(self):
        """create_default_manifest() includes the five documented algorithms."""
        manifest = create_default_manifest()
        known_ids = {"weighted-sum", "pareto-nsga2", "epsilon-constraint",
                     "simulated-annealing", "knapsack-dp"}
        registered = set(manifest.algorithm_registry.keys())
        assert known_ids.issubset(registered)

    def test_algorithm_registry_by_complexity(self):
        """AlgorithmRegistry.by_complexity() filters descriptors by complexity substring."""
        registry = AlgorithmRegistry()
        linear = _make_descriptor("linear-algo", complexity="O(n)")
        quadratic = _make_descriptor("quad-algo", complexity="O(n^2)")
        exp_algo = _make_descriptor("exp-algo", complexity="O(2^n)")
        registry.register(linear)
        registry.register(quadratic)
        registry.register(exp_algo)

        poly_results = registry.by_complexity("O(n")
        poly_ids = {d.algorithm_id for d in poly_results}
        assert "linear-algo" in poly_ids
        assert "quad-algo" in poly_ids
        assert "exp-algo" not in poly_ids

        exp_results = registry.by_complexity("O(2^n)")
        assert len(exp_results) == 1
        assert exp_results[0].algorithm_id == "exp-algo"

    def test_algorithm_registry_all_ids_sorted(self):
        """AlgorithmRegistry.all_ids() returns IDs in sorted order."""
        registry = _make_algorithm_registry(
            _make_descriptor("zzz-algo"),
            _make_descriptor("aaa-algo"),
            _make_descriptor("mmm-algo"),
        )
        ids = registry.all_ids()
        assert ids == sorted(ids)

    def test_manifest_validator_valid_manifest(self):
        """ManifestValidator.validate() returns an empty list for a well-formed manifest."""
        manifest = _make_manifest(version="1.2.3", package_id="valid-pkg")
        manifest.register(_make_descriptor("v-algo"))
        validator = ManifestValidator()
        issues = validator.validate(manifest)
        assert issues == []
        assert validator.is_valid(manifest) is True

    def test_manifest_validator_invalid_version(self):
        """ManifestValidator reports an issue when the version is malformed."""
        manifest = OptimizationManifest(version="bad-version", package_id="some-pkg")
        validator = ManifestValidator()
        issues = validator.validate(manifest)
        assert len(issues) > 0
        assert validator.is_valid(manifest) is False

    def test_manifest_validator_empty_package_id(self):
        """ManifestValidator reports an issue when package_id is empty."""
        manifest = OptimizationManifest(version="1.0.0", package_id="")
        validator = ManifestValidator()
        issues = validator.validate(manifest)
        assert len(issues) > 0
        assert validator.is_valid(manifest) is False

    def test_algorithm_registry_count(self):
        """AlgorithmRegistry.count() tracks the number of registered descriptors."""
        registry = AlgorithmRegistry()
        assert registry.count() == 0
        registry.register(_make_descriptor("x1"))
        assert registry.count() == 1
        registry.register(_make_descriptor("x2"))
        assert registry.count() == 2

    def test_algorithm_registry_summary_string(self):
        """AlgorithmRegistry.summary() returns a readable string with all IDs."""
        registry = _make_algorithm_registry(
            _make_descriptor("sum-a", name="Sum A"),
            _make_descriptor("sum-b", name="Sum B"),
        )
        summary = registry.summary()
        assert isinstance(summary, str)
        assert "sum-a" in summary
        assert "sum-b" in summary


# ---------------------------------------------------------------------------
# Standalone function tests (pure functions / module-level)
# ---------------------------------------------------------------------------


def test_register_algorithm_function():
    """register_algorithm() adds a descriptor to the supplied AlgorithmRegistry."""
    registry = AlgorithmRegistry()
    desc = _make_descriptor("fn-algo")
    register_algorithm(registry, desc)
    found = registry.lookup("fn-algo")
    assert found is desc
    assert registry.count() == 1


def test_register_algorithm_function_multiple():
    """register_algorithm() can be called multiple times on the same registry."""
    registry = AlgorithmRegistry()
    for i in range(4):
        desc = _make_descriptor(f"fn-algo-{i}")
        register_algorithm(registry, desc)
    assert registry.count() == 4


def test_lookup_algorithm_function_missing():
    """lookup_algorithm() returns None when the id is not registered."""
    registry = AlgorithmRegistry()
    result = lookup_algorithm(registry, "nonexistent-id")
    assert result is None


def test_lookup_algorithm_function_found():
    """lookup_algorithm() returns the correct descriptor after registration."""
    registry = AlgorithmRegistry()
    desc = _make_descriptor("look-me-up")
    register_algorithm(registry, desc)
    result = lookup_algorithm(registry, "look-me-up")
    assert result is desc


def test_algorithm_descriptor_equality():
    """Two AlgorithmDescriptors with identical fields compare equal (frozen)."""
    d1 = _make_descriptor("same-id", params={"k": 1})
    d2 = _make_descriptor("same-id", params={"k": 1})
    assert d1 == d2


def test_algorithm_descriptor_inequality():
    """Two descriptors with differing fields are not equal."""
    d1 = _make_descriptor("id-a")
    d2 = _make_descriptor("id-b")
    assert d1 != d2


def test_algorithm_descriptor_hashable():
    """Frozen AlgorithmDescriptor instances can be stored in a set."""
    d1 = _make_descriptor("hash-a", params={"x": 1})
    d2 = _make_descriptor("hash-b", params={"x": 2})
    s = {d1, d2}
    assert len(s) == 2


def test_manifest_version_stored_correctly():
    """OptimizationManifest stores the version string without modification."""
    manifest = _make_manifest(version="3.14.159")
    assert manifest.version == "3.14.159"


def test_manifest_created_at_is_string():
    """OptimizationManifest.created_at is a non-empty string (ISO timestamp)."""
    manifest = _make_manifest()
    assert isinstance(manifest.created_at, str)
    assert len(manifest.created_at) > 0


def test_manifest_registry_singleton_identity():
    """ManifestRegistry.instance() always returns the same object."""
    r1 = ManifestRegistry.instance()
    r2 = ManifestRegistry.instance()
    assert r1 is r2


def test_manifest_registry_clear():
    """ManifestRegistry.clear() empties the registry without error."""
    registry = ManifestRegistry.instance()
    registry.add(_make_manifest(package_id="to-clear"))
    assert len(registry.list_all()) >= 1
    registry.clear()
    assert registry.list_all() == []


def test_create_default_manifest_version():
    """create_default_manifest() sets version to '1.0.0'."""
    manifest = create_default_manifest()
    assert manifest.version == "1.0.0"


def test_create_default_manifest_passes_validation():
    """The default manifest passes ManifestValidator.is_valid()."""
    manifest = create_default_manifest()
    validator = ManifestValidator()
    assert validator.is_valid(manifest) is True


def test_algorithm_registry_empty_all_ids():
    """all_ids() returns an empty list for a fresh AlgorithmRegistry."""
    registry = AlgorithmRegistry()
    assert registry.all_ids() == []


def test_algorithm_registry_by_complexity_no_match():
    """by_complexity() returns an empty list when no descriptor matches."""
    registry = _make_algorithm_registry(_make_descriptor("a", complexity="O(n)"))
    result = registry.by_complexity("O(n!)")
    assert result == []


def test_manifest_validator_is_valid_empty_registry():
    """A manifest with an empty algorithm registry is still valid."""
    manifest = _make_manifest(version="1.0.0", package_id="empty-pkg")
    validator = ManifestValidator()
    assert validator.is_valid(manifest) is True


def test_manifest_summary_empty_registry():
    """summary() works and is non-empty even when no algorithms are registered."""
    manifest = _make_manifest(version="1.0.0", package_id="empty-summary")
    result = manifest.summary()
    assert isinstance(result, str)
    assert "1.0.0" in result


def test_algorithm_descriptor_no_params():
    """AlgorithmDescriptor with an empty parameters dict has parameter_count 0."""
    desc = _make_descriptor(params={})
    assert desc.parameter_count() == 0
    assert desc.is_polynomial() or not desc.is_polynomial()  # no error


def test_manifest_register_then_lookup_all():
    """All registered descriptors can be retrieved via the algorithm_registry dict."""
    manifest = _make_manifest()
    ids = ["a-one", "b-two", "c-three"]
    for aid in ids:
        manifest.register(_make_descriptor(aid))
    for aid in ids:
        assert manifest.lookup(aid) is not None
    assert manifest.algorithm_count() == len(ids)
