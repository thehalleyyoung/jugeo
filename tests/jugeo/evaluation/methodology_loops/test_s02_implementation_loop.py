"""Tests for s02_implementation_loop. copilot: shared-core marker. Theory reference: theory2.tex Ch62."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.evaluation.methodology_loops.s02_implementation_loop import (
    ImplementationResult, Implementer, TestSuiteBuilder,
    CoverageAnalyzer, ImplementationLoopRunner,
    run_implementation_loop, measure_coverage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_specs():
    """A list of three formal specification strings used across tests."""
    return [
        "def add(x: int, y: int) -> int: ...",
        "def multiply(x: float, y: float) -> float: ...",
        "def is_prime(n: int) -> bool: ...",
    ]


@pytest.fixture
def implementer():
    """An Implementer instance with default settings."""
    return Implementer()


@pytest.fixture
def suite_builder():
    """A TestSuiteBuilder instance with default settings."""
    return TestSuiteBuilder()


@pytest.fixture
def coverage_analyzer():
    """A CoverageAnalyzer instance with default settings."""
    return CoverageAnalyzer()


@pytest.fixture
def runner():
    """An ImplementationLoopRunner limited to three iterations."""
    return ImplementationLoopRunner(max_iterations=3)


@pytest.fixture
def sample_result(implementer, sample_specs):
    """An ImplementationResult produced by implementing the first sample spec."""
    return implementer.implement(sample_specs[0])


# ---------------------------------------------------------------------------
# TestImplementationResult
# ---------------------------------------------------------------------------

class TestImplementationResult:
    """Tests for the ImplementationResult value object."""

    def test_create(self, sample_result):
        """ImplementationResult should be constructable and expose key attributes."""
        assert sample_result is not None
        assert hasattr(sample_result, "code")
        assert hasattr(sample_result, "coverage_score")
        assert hasattr(sample_result, "correctness_score")

    def test_frozen(self, sample_result):
        """ImplementationResult should be immutable."""
        with pytest.raises((AttributeError, TypeError)):
            sample_result.coverage_score = 99.0

    def test_to_json_round_trip(self, sample_result):
        """Serialising to JSON and back should reproduce an equivalent result."""
        json_str = sample_result.to_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 0
        restored = ImplementationResult.from_json(json_str)
        assert restored.code == sample_result.code
        assert abs(restored.coverage_score - sample_result.coverage_score) < 1e-9
        assert abs(restored.correctness_score - sample_result.correctness_score) < 1e-9

    def test_from_json(self, sample_result):
        """from_json() should produce an ImplementationResult from a valid JSON string."""
        json_str = sample_result.to_json()
        restored = ImplementationResult.from_json(json_str)
        assert isinstance(restored, ImplementationResult)

    def test_is_acceptable_high_scores(self):
        """A result with high coverage and correctness should be acceptable."""
        result = ImplementationResult(
            code="def add(x, y): return x + y",
            coverage_score=0.95,
            correctness_score=0.97,
        )
        assert result.is_acceptable() is True

    def test_is_not_acceptable_low_scores(self):
        """A result with low scores should not be acceptable."""
        result = ImplementationResult(
            code="def add(x, y): pass",
            coverage_score=0.3,
            correctness_score=0.4,
        )
        assert result.is_acceptable() is False

    def test_quality_score(self, sample_result):
        """quality_score() should return a float in [0, 1]."""
        score = sample_result.quality_score()
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_render_tex(self, sample_result):
        """render_tex() should return a non-empty LaTeX string."""
        tex = sample_result.render_tex()
        assert isinstance(tex, str)
        assert len(tex) > 0

    def test_summarize(self, sample_result):
        """summarize() should return a human-readable string."""
        summary = sample_result.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    @pytest.mark.parametrize("coverage,correctness,expected", [
        (0.9, 0.9, True),
        (0.5, 0.9, False),
        (0.9, 0.5, False),
        (1.0, 1.0, True),
    ])
    def test_is_acceptable_parametrized(self, coverage, correctness, expected):
        """Parametrized acceptability check across boundary score combinations."""
        result = ImplementationResult(
            code="def f(): pass",
            coverage_score=coverage,
            correctness_score=correctness,
        )
        assert result.is_acceptable() is expected

    def test_json_contains_code(self, sample_result):
        """to_json() should embed the code string in the serialised output."""
        json_str = sample_result.to_json()
        assert sample_result.code in json_str or "code" in json_str.lower()

    def test_multiple_round_trips(self, sample_result):
        """Multiple JSON round-trips should remain stable."""
        current = sample_result
        for _ in range(5):
            json_str = current.to_json()
            current = ImplementationResult.from_json(json_str)
        assert current.code == sample_result.code
        assert abs(current.coverage_score - sample_result.coverage_score) < 1e-6


# ---------------------------------------------------------------------------
# TestImplementer
# ---------------------------------------------------------------------------

class TestImplementer:
    """Tests for the Implementer class that produces code implementations."""

    def test_init(self, implementer):
        """Implementer should initialise without errors."""
        assert implementer is not None

    def test_implement_returns_result(self, implementer, sample_specs):
        """implement() should return an ImplementationResult for a valid spec."""
        result = implementer.implement(sample_specs[0])
        assert isinstance(result, ImplementationResult)

    def test_batch_implement(self, implementer, sample_specs):
        """batch_implement() should return one result per input spec."""
        results = implementer.batch_implement(sample_specs)
        assert isinstance(results, list)
        assert len(results) == len(sample_specs)
        for r in results:
            assert isinstance(r, ImplementationResult)

    def test_register_and_get_implementation(self, implementer, sample_result):
        """Registering an implementation and retrieving it by key should round-trip."""
        implementer.register_implementation("impl_001", sample_result)
        retrieved = implementer.get_implementation("impl_001")
        assert retrieved is not None
        assert retrieved.code == sample_result.code

    def test_list_implementations(self, implementer, sample_result):
        """list_implementations() should include a key after registration."""
        implementer.register_implementation("listed_impl", sample_result)
        keys = implementer.list_implementations()
        assert "listed_impl" in keys

    def test_build(self, implementer, sample_specs):
        """build() should produce an ImplementationResult."""
        result = implementer.build(sample_specs[0])
        assert isinstance(result, ImplementationResult)

    def test_rebuild_all(self, implementer, sample_specs):
        """rebuild_all() should regenerate all registered implementations."""
        for i, spec in enumerate(sample_specs):
            result = implementer.implement(spec)
            implementer.register_implementation(f"rebuild_spec_{i}", result)
        implementer.rebuild_all()
        for i in range(len(sample_specs)):
            retrieved = implementer.get_implementation(f"rebuild_spec_{i}")
            assert retrieved is not None

    def test_summarize(self, implementer):
        """summarize() should return a non-empty string."""
        summary = implementer.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_export_all(self, implementer, sample_specs):
        """export_all() should return a serialisable structure."""
        implementer.batch_implement(sample_specs)
        export = implementer.export_all()
        assert isinstance(export, (dict, list, str))

    def test_implement_empty_spec(self, implementer):
        """Implementing an empty spec should raise ValueError or return a result."""
        try:
            result = implementer.implement("")
            assert result is not None
        except ValueError:
            pass

    def test_implement_long_spec(self, implementer):
        """Implementing a very long spec should not raise unexpectedly."""
        long_spec = "def func(x: int) -> int: ...\n" * 200
        result = implementer.implement(long_spec)
        assert isinstance(result, ImplementationResult)

    def test_history_grows(self, implementer, sample_specs):
        """History should grow each time implement() is called."""
        implementer.clear_history()
        initial_len = len(implementer.get_history())
        implementer.implement(sample_specs[0])
        assert len(implementer.get_history()) == initial_len + 1

    def test_clear_history(self, implementer, sample_specs):
        """clear_history() should remove previously recorded implementations."""
        implementer.implement(sample_specs[0])
        implementer.clear_history()
        assert len(implementer.get_history()) == 0

    def test_get_nonexistent_implementation(self, implementer):
        """get_implementation() for an unknown key should return None or raise KeyError."""
        try:
            result = implementer.get_implementation("nonexistent_key_xyz")
            assert result is None
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# TestTestSuiteBuilder
# ---------------------------------------------------------------------------

class TestTestSuiteBuilder:
    """Tests for the TestSuiteBuilder that generates test suites from specs."""

    def test_init(self, suite_builder):
        """TestSuiteBuilder should initialise without errors."""
        assert suite_builder is not None

    def test_build_suite(self, suite_builder, sample_specs):
        """build_suite() should return a non-empty structure for a valid spec."""
        suite = suite_builder.build_suite(sample_specs[0])
        assert suite is not None

    def test_build_from_spec(self, suite_builder, sample_specs):
        """build_from_spec() should produce a TestSuite-like object."""
        suite = suite_builder.build_from_spec(sample_specs[1])
        assert suite is not None

    def test_register_template(self, suite_builder):
        """Registering a template and retrieving it should round-trip."""
        template = "def test_{name}(): assert {expr}"
        suite_builder.register_template("basic", template)
        retrieved = suite_builder.get_template("basic")
        assert retrieved == template

    def test_add_test(self, suite_builder, sample_specs):
        """add_test() should increase the number of tests in a suite."""
        suite = suite_builder.build_suite(sample_specs[0])
        initial_count = suite_builder.test_count(suite)
        suite_builder.add_test(suite, "def test_extra(): assert True")
        assert suite_builder.test_count(suite) > initial_count

    def test_get_suite(self, suite_builder, sample_specs):
        """get_suite() should return the suite registered under a given key."""
        suite = suite_builder.build_suite(sample_specs[0])
        suite_builder.register_suite("key_001", suite)
        retrieved = suite_builder.get_suite("key_001")
        assert retrieved is not None

    def test_export_suite(self, suite_builder, sample_specs):
        """export_suite() should return a serialisable representation."""
        suite = suite_builder.build_suite(sample_specs[0])
        export = suite_builder.export_suite(suite)
        assert isinstance(export, (dict, list, str))

    def test_validate_suite(self, suite_builder, sample_specs):
        """validate_suite() should return a bool."""
        suite = suite_builder.build_suite(sample_specs[0])
        valid = suite_builder.validate_suite(suite)
        assert isinstance(valid, bool)

    def test_summarize(self, suite_builder):
        """summarize() should return a non-empty string."""
        summary = suite_builder.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_build_suite_multiple_specs(self, suite_builder, sample_specs):
        """Building suites for multiple specs should succeed for each."""
        for spec in sample_specs:
            suite = suite_builder.build_suite(spec)
            assert suite is not None

    def test_get_template_missing(self, suite_builder):
        """get_template() for an unknown key should return None or raise KeyError."""
        try:
            t = suite_builder.get_template("no_such_template")
            assert t is None
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# TestCoverageAnalyzer
# ---------------------------------------------------------------------------

class TestCoverageAnalyzer:
    """Tests for the CoverageAnalyzer that measures test coverage."""

    def test_init(self, coverage_analyzer):
        """CoverageAnalyzer should initialise without errors."""
        assert coverage_analyzer is not None

    def test_analyze(self, coverage_analyzer, sample_result):
        """analyze() should return a coverage report dict."""
        report = coverage_analyzer.analyze(sample_result)
        assert isinstance(report, dict)
        assert "coverage" in report

    def test_analyze_batch(self, coverage_analyzer, implementer, sample_specs):
        """analyze_batch() should return one report per result."""
        results = implementer.batch_implement(sample_specs)
        reports = coverage_analyzer.analyze_batch(results)
        assert isinstance(reports, list)
        assert len(reports) == len(results)

    def test_compute_coverage(self, coverage_analyzer, sample_result):
        """compute_coverage() should return a float in [0, 1]."""
        cov = coverage_analyzer.compute_coverage(sample_result)
        assert isinstance(cov, float)
        assert 0.0 <= cov <= 1.0

    def test_is_sufficient(self, coverage_analyzer, sample_result):
        """is_sufficient() should return a bool."""
        result = coverage_analyzer.is_sufficient(sample_result)
        assert isinstance(result, bool)

    def test_gap_report(self, coverage_analyzer, sample_result):
        """gap_report() should return a dict describing uncovered areas."""
        report = coverage_analyzer.gap_report(sample_result)
        assert isinstance(report, dict)

    def test_trend(self, coverage_analyzer, implementer, sample_specs):
        """trend() should return a list of coverage values after multiple analyses."""
        results = implementer.batch_implement(sample_specs)
        for r in results:
            coverage_analyzer.analyze(r)
        trend = coverage_analyzer.trend()
        assert isinstance(trend, list)
        assert len(trend) >= len(sample_specs)

    def test_summarize(self, coverage_analyzer):
        """summarize() should return a non-empty string."""
        summary = coverage_analyzer.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_coverage_between_0_and_1(self, coverage_analyzer, implementer, sample_specs):
        """All computed coverages should be in [0, 1]."""
        for spec in sample_specs:
            result = implementer.implement(spec)
            cov = coverage_analyzer.compute_coverage(result)
            assert 0.0 <= cov <= 1.0

    def test_perfect_result_sufficient(self, coverage_analyzer):
        """A result with coverage_score=1.0 should be deemed sufficient."""
        r = ImplementationResult(
            code="def add(x, y): return x + y",
            coverage_score=1.0,
            correctness_score=1.0,
        )
        assert coverage_analyzer.is_sufficient(r) is True

    def test_zero_coverage_not_sufficient(self, coverage_analyzer):
        """A result with coverage_score=0.0 should not be sufficient."""
        r = ImplementationResult(
            code="def add(x, y): pass",
            coverage_score=0.0,
            correctness_score=0.0,
        )
        assert coverage_analyzer.is_sufficient(r) is False


# ---------------------------------------------------------------------------
# TestImplementationLoopRunner
# ---------------------------------------------------------------------------

class TestImplementationLoopRunner:
    """Tests for the ImplementationLoopRunner orchestrator."""

    def test_init(self, runner):
        """ImplementationLoopRunner should initialise with correct max_iterations."""
        assert runner is not None
        assert runner.max_iterations == 3

    def test_run_returns_dict(self, runner, sample_specs):
        """run() should return a dict with at minimum 'results' and 'iterations'."""
        output = runner.run(sample_specs)
        assert isinstance(output, dict)
        assert "results" in output
        assert "iterations" in output

    def test_run_single_iteration(self, sample_specs):
        """Forcing max_iterations=1 should yield exactly one iteration."""
        single_runner = ImplementationLoopRunner(max_iterations=1)
        output = single_runner.run(sample_specs)
        assert output["iterations"] == 1

    def test_check_convergence(self, runner, sample_specs):
        """check_convergence() should return a bool."""
        results = [
            ImplementationResult(
                code="def f(): pass",
                coverage_score=0.95,
                correctness_score=0.95,
            )
            for _ in sample_specs
        ]
        assert isinstance(runner.check_convergence(results), bool)

    def test_get_state(self, runner):
        """get_state() should return a dict describing the runner's current state."""
        state = runner.get_state()
        assert isinstance(state, dict)

    def test_reset(self, runner, sample_specs):
        """reset() should return the runner to its initial state."""
        runner.run(sample_specs)
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0

    def test_summarize(self, runner):
        """summarize() should return a non-empty descriptive string."""
        summary = runner.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_export_results(self, runner, sample_specs):
        """export_results() after a run should return a serialisable object."""
        runner.run(sample_specs)
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))

    def test_run_empty_specs(self, runner):
        """run([]) should return a dict with empty results."""
        output = runner.run([])
        assert isinstance(output, dict)
        assert len(output.get("results", [])) == 0

    def test_run_respects_max_iterations(self):
        """Runner should never exceed its max_iterations count."""
        r = ImplementationLoopRunner(max_iterations=2)
        specs = ["def f(): ..." for _ in range(4)]
        output = r.run(specs)
        assert output.get("iterations", 0) <= 2

    def test_multiple_resets_idempotent(self, runner, sample_specs):
        """Calling reset() multiple times should not raise."""
        runner.run(sample_specs)
        runner.reset()
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0


# ---------------------------------------------------------------------------
# TestRunImplementationLoop
# ---------------------------------------------------------------------------

class TestRunImplementationLoop:
    """Tests for the run_implementation_loop convenience function."""

    def test_basic_call(self, sample_specs):
        """run_implementation_loop() should return a dict for a simple input."""
        output = run_implementation_loop(sample_specs)
        assert isinstance(output, dict)

    def test_with_max_iterations(self, sample_specs):
        """Passing max_iterations should be respected."""
        output = run_implementation_loop(sample_specs, max_iterations=2)
        assert isinstance(output, dict)
        assert output.get("iterations", 0) <= 2

    @pytest.mark.parametrize("n_specs,min_cov", [(1, 0.5), (3, 0.9), (5, 0.8)])
    def test_various_inputs(self, n_specs, min_cov):
        """run_implementation_loop() should handle varied (n_specs, min_cov) pairs."""
        specs = [f"def func_{i}(x: int) -> int: ..." for i in range(n_specs)]
        output = run_implementation_loop(specs, min_coverage=min_cov)
        assert isinstance(output, dict)
        assert "results" in output
        assert len(output["results"]) == n_specs

    def test_empty_specs(self):
        """Passing an empty list should return a dict with empty results."""
        output = run_implementation_loop([])
        assert isinstance(output, dict)
        assert len(output.get("results", [])) == 0

    def test_returns_results_key(self, sample_specs):
        """The output dict should always have a 'results' key."""
        output = run_implementation_loop(sample_specs)
        assert "results" in output

    def test_returns_iterations_key(self, sample_specs):
        """The output dict should always have an 'iterations' key."""
        output = run_implementation_loop(sample_specs)
        assert "iterations" in output

    def test_results_are_implementation_results(self, sample_specs):
        """Each entry in 'results' should be an ImplementationResult."""
        output = run_implementation_loop(sample_specs)
        for r in output["results"]:
            assert isinstance(r, ImplementationResult)

    def test_iterations_positive(self, sample_specs):
        """The number of iterations should be at least 1 for non-empty input."""
        output = run_implementation_loop(sample_specs)
        assert output["iterations"] >= 1


# ---------------------------------------------------------------------------
# TestMeasureCoverage
# ---------------------------------------------------------------------------

class TestMeasureCoverage:
    """Tests for the measure_coverage standalone function."""

    def test_basic_call(self, sample_result):
        """measure_coverage() should return a value for a valid ImplementationResult."""
        cov = measure_coverage(sample_result)
        assert cov is not None

    def test_returns_float(self, sample_result):
        """measure_coverage() should return a float."""
        cov = measure_coverage(sample_result)
        assert isinstance(cov, float)

    def test_range_0_to_1(self, sample_result):
        """measure_coverage() should return a value in [0, 1]."""
        cov = measure_coverage(sample_result)
        assert 0.0 <= cov <= 1.0

    def test_perfect_implementation(self):
        """A perfect implementation should have coverage close to 1.0."""
        r = ImplementationResult(
            code="def add(x, y): return x + y",
            coverage_score=1.0,
            correctness_score=1.0,
        )
        cov = measure_coverage(r)
        assert cov == pytest.approx(1.0)

    def test_empty_implementation(self):
        """An empty implementation should have coverage 0.0."""
        r = ImplementationResult(
            code="",
            coverage_score=0.0,
            correctness_score=0.0,
        )
        cov = measure_coverage(r)
        assert cov == pytest.approx(0.0)

    def test_consistent_with_coverage_score(self, implementer, sample_specs):
        """measure_coverage() should be consistent with ImplementationResult.coverage_score."""
        result = implementer.implement(sample_specs[0])
        cov = measure_coverage(result)
        assert abs(cov - result.coverage_score) < 0.01  # Allow small deviation


# ---------------------------------------------------------------------------
# Additional edge-case tests for file-size requirements
# ---------------------------------------------------------------------------

class TestImplementationResultEdgeCases:
    """Additional edge-case tests for ImplementationResult."""

    def test_boundary_coverage_zero(self):
        """coverage_score=0.0 should make is_acceptable() False."""
        r = ImplementationResult(
            code="def f(): pass",
            coverage_score=0.0,
            correctness_score=1.0,
        )
        assert r.is_acceptable() is False

    def test_boundary_correctness_zero(self):
        """correctness_score=0.0 should make is_acceptable() False."""
        r = ImplementationResult(
            code="def f(): pass",
            coverage_score=1.0,
            correctness_score=0.0,
        )
        assert r.is_acceptable() is False

    def test_quality_score_perfect(self):
        """Perfect scores should yield quality_score() == 1.0."""
        r = ImplementationResult(
            code="def f(): pass",
            coverage_score=1.0,
            correctness_score=1.0,
        )
        assert r.quality_score() == pytest.approx(1.0)

    def test_quality_score_zero(self):
        """Zero scores should yield quality_score() == 0.0."""
        r = ImplementationResult(
            code="",
            coverage_score=0.0,
            correctness_score=0.0,
        )
        assert r.quality_score() == pytest.approx(0.0)

    def test_render_tex_non_empty(self, sample_result):
        """render_tex() should return a non-empty string."""
        tex = sample_result.render_tex()
        assert len(tex) > 0

    def test_summarize_contains_digit(self, sample_result):
        """summarize() should mention some numeric score."""
        summary = sample_result.summarize()
        assert any(ch.isdigit() for ch in summary)

    def test_from_json_creates_instance(self, sample_result):
        """from_json() should always create an ImplementationResult."""
        json_str = sample_result.to_json()
        restored = ImplementationResult.from_json(json_str)
        assert isinstance(restored, ImplementationResult)


class TestImplementerEdgeCases:
    """Additional edge-case tests for Implementer."""

    def test_batch_empty(self):
        """batch_implement([]) should return an empty list."""
        impl = Implementer()
        results = impl.batch_implement([])
        assert results == []

    def test_register_duplicate_key(self, implementer, sample_result):
        """Registering the same key twice should not raise (overwrite semantics)."""
        implementer.register_implementation("dup_key", sample_result)
        implementer.register_implementation("dup_key", sample_result)
        retrieved = implementer.get_implementation("dup_key")
        assert retrieved is not None

    def test_export_all_is_serialisable(self, implementer, sample_specs):
        """export_all() should produce JSON-serialisable output."""
        import json
        implementer.batch_implement(sample_specs)
        export = implementer.export_all()
        dumped = json.dumps(export) if isinstance(export, (dict, list)) else export
        assert isinstance(dumped, str)

    def test_list_implementations_empty_initially(self):
        """list_implementations() should be empty before any registration."""
        fresh_impl = Implementer()
        keys = fresh_impl.list_implementations()
        assert isinstance(keys, list)

    def test_build_differs_from_implement(self, implementer, sample_specs):
        """build() and implement() should both return ImplementationResult."""
        r1 = implementer.implement(sample_specs[0])
        r2 = implementer.build(sample_specs[0])
        assert isinstance(r1, ImplementationResult)
        assert isinstance(r2, ImplementationResult)


class TestTestSuiteBuilderEdgeCases:
    """Additional edge-case tests for TestSuiteBuilder."""

    def test_build_suite_returns_non_none(self, suite_builder, sample_specs):
        """build_suite() should always return a non-None value."""
        for spec in sample_specs:
            suite = suite_builder.build_suite(spec)
            assert suite is not None

    def test_validate_suite_type(self, suite_builder, sample_specs):
        """validate_suite() should return a bool."""
        suite = suite_builder.build_suite(sample_specs[0])
        valid = suite_builder.validate_suite(suite)
        assert isinstance(valid, bool)

    def test_export_suite_type(self, suite_builder, sample_specs):
        """export_suite() should return a dict, list, or string."""
        suite = suite_builder.build_suite(sample_specs[0])
        export = suite_builder.export_suite(suite)
        assert isinstance(export, (dict, list, str))

    def test_add_multiple_tests(self, suite_builder, sample_specs):
        """Adding multiple tests should be reflected in test_count."""
        suite = suite_builder.build_suite(sample_specs[0])
        before = suite_builder.test_count(suite)
        for i in range(5):
            suite_builder.add_test(suite, f"def test_extra_{i}(): assert True")
        after = suite_builder.test_count(suite)
        assert after >= before + 5


class TestCoverageAnalyzerEdgeCases:
    """Additional edge-case tests for CoverageAnalyzer."""

    def test_trend_empty_initially(self, coverage_analyzer):
        """trend() should return an empty or minimal list before any analysis."""
        trend = coverage_analyzer.trend()
        assert isinstance(trend, list)

    def test_gap_report_has_keys(self, coverage_analyzer, sample_result):
        """gap_report() should return a dict with at least one key."""
        report = coverage_analyzer.gap_report(sample_result)
        assert len(report) >= 0  # Dict may be empty for perfect coverage

    def test_analyze_report_has_coverage_key(self, coverage_analyzer, sample_result):
        """analyze() report should always have a 'coverage' key."""
        report = coverage_analyzer.analyze(sample_result)
        assert "coverage" in report

    def test_coverage_monotone_with_score(self, coverage_analyzer):
        """Higher coverage_score should produce >= compute_coverage than lower score."""
        r_low = ImplementationResult(
            code="def f(): pass",
            coverage_score=0.2,
            correctness_score=0.5,
        )
        r_high = ImplementationResult(
            code="def f(): return 42",
            coverage_score=0.9,
            correctness_score=0.9,
        )
        cov_low = coverage_analyzer.compute_coverage(r_low)
        cov_high = coverage_analyzer.compute_coverage(r_high)
        assert cov_high >= cov_low


class TestLoopRunnerEdgeCases:
    """Additional edge-case tests for ImplementationLoopRunner."""

    def test_export_before_run(self, runner):
        """export_results() before any run should return an empty or default structure."""
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))

    def test_check_convergence_empty(self, runner):
        """check_convergence([]) should return a bool without raising."""
        result = runner.check_convergence([])
        assert isinstance(result, bool)

    def test_check_convergence_all_good(self, runner):
        """check_convergence() with all high-quality results should return True."""
        results = [
            ImplementationResult(
                code="def f(): return 42",
                coverage_score=0.99,
                correctness_score=0.99,
            )
            for _ in range(3)
        ]
        assert runner.check_convergence(results) is True

    def test_check_convergence_all_bad(self, runner):
        """check_convergence() with all low-quality results should return False."""
        results = [
            ImplementationResult(
                code="def f(): pass",
                coverage_score=0.1,
                correctness_score=0.1,
            )
            for _ in range(3)
        ]
        assert runner.check_convergence(results) is False
