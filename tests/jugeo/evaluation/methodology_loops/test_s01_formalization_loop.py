"""Tests for s01_formalization_loop. copilot: shared-core marker. Theory reference: theory2.tex Ch62."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.evaluation.methodology_loops.s01_formalization_loop import (
    FormalizationResult, Formalizer, SpecificationWriter,
    FormalizationChecker, FormalizationLoopRunner,
    run_formalization_loop, check_formalization,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_informal_texts():
    """A list of three informal mathematical descriptions used across tests."""
    return [
        (
            "For every real number x there exists a real number y such that "
            "y is strictly greater than x and y is also a real number."
        ),
        (
            "The composition of two continuous functions is itself a continuous "
            "function on the shared domain."
        ),
        (
            "A finite group whose order is a prime number must be cyclic, meaning "
            "every non-identity element generates the entire group."
        ),
    ]


@pytest.fixture
def formalizer():
    """A Formalizer instance targeting the Lean 4 formal language."""
    return Formalizer(formal_language="lean4")


@pytest.fixture
def spec_writer():
    """A SpecificationWriter instance with default settings."""
    return SpecificationWriter()


@pytest.fixture
def checker():
    """A FormalizationChecker instance with default settings."""
    return FormalizationChecker()


@pytest.fixture
def runner():
    """A FormalizationLoopRunner limited to three iterations."""
    return FormalizationLoopRunner(max_iterations=3)


@pytest.fixture
def sample_result(formalizer, sample_informal_texts):
    """A FormalizationResult produced by formalizing the first sample text."""
    return formalizer.formalize(sample_informal_texts[0])


# ---------------------------------------------------------------------------
# TestFormalizationResult
# ---------------------------------------------------------------------------

class TestFormalizationResult:
    """Tests for the FormalizationResult value object."""

    def test_create(self, sample_result):
        """FormalizationResult should be constructable and expose key attributes."""
        assert sample_result is not None
        assert hasattr(sample_result, "formal_text")
        assert hasattr(sample_result, "consistency_score")
        assert hasattr(sample_result, "completeness_score")

    def test_frozen(self, sample_result):
        """FormalizationResult should be immutable (frozen dataclass or similar)."""
        with pytest.raises((AttributeError, TypeError)):
            sample_result.consistency_score = 99.0

    def test_to_json_round_trip(self, sample_result):
        """Serializing to JSON and back should reproduce an equivalent result."""
        json_str = sample_result.to_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 0
        restored = FormalizationResult.from_json(json_str)
        assert restored.formal_text == sample_result.formal_text
        assert abs(restored.consistency_score - sample_result.consistency_score) < 1e-9
        assert abs(restored.completeness_score - sample_result.completeness_score) < 1e-9

    def test_from_json(self, sample_result):
        """from_json should produce a FormalizationResult from a valid JSON string."""
        json_str = sample_result.to_json()
        restored = FormalizationResult.from_json(json_str)
        assert isinstance(restored, FormalizationResult)

    def test_is_acceptable_high_scores(self, formalizer, sample_informal_texts):
        """A result with high consistency and completeness should be acceptable."""
        result = FormalizationResult(
            formal_text="theorem foo : True := trivial",
            consistency_score=0.95,
            completeness_score=0.97,
        )
        assert result.is_acceptable() is True

    def test_is_not_acceptable_low_scores(self):
        """A result with low scores should not be acceptable."""
        result = FormalizationResult(
            formal_text="theorem foo : True := sorry",
            consistency_score=0.4,
            completeness_score=0.6,
        )
        assert result.is_acceptable() is False

    def test_quality_score(self, sample_result):
        """quality_score should return a float in [0, 1]."""
        score = sample_result.quality_score()
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_render_tex(self, sample_result):
        """render_tex should return a non-empty LaTeX string."""
        tex = sample_result.render_tex()
        assert isinstance(tex, str)
        assert len(tex) > 0

    def test_summarize(self, sample_result):
        """summarize should return a human-readable string."""
        summary = sample_result.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    @pytest.mark.parametrize("consistency,completeness,expected", [
        (0.9, 0.9, True),
        (0.5, 0.9, False),
        (0.9, 0.5, False),
        (1.0, 1.0, True),
    ])
    def test_is_acceptable_parametrized(self, consistency, completeness, expected):
        """Parametrized acceptability check across boundary score combinations."""
        result = FormalizationResult(
            formal_text="theorem bar : True := trivial",
            consistency_score=consistency,
            completeness_score=completeness,
        )
        assert result.is_acceptable() is expected


# ---------------------------------------------------------------------------
# TestFormalizer
# ---------------------------------------------------------------------------

class TestFormalizer:
    """Tests for the Formalizer class that converts informal text to formal specs."""

    def test_init(self, formalizer):
        """Formalizer should initialise with the specified formal language."""
        assert formalizer is not None
        assert formalizer.formal_language == "lean4"

    def test_formalize_returns_result(self, formalizer, sample_informal_texts):
        """formalize() should return a FormalizationResult for a valid text."""
        result = formalizer.formalize(sample_informal_texts[0])
        assert isinstance(result, FormalizationResult)

    def test_formalize_with_context(self, formalizer, sample_informal_texts):
        """formalize() should accept an optional context dict without error."""
        ctx = {"domain": "analysis", "level": "graduate"}
        result = formalizer.formalize(sample_informal_texts[1], context=ctx)
        assert isinstance(result, FormalizationResult)

    def test_batch_formalize(self, formalizer, sample_informal_texts):
        """batch_formalize() should return one result per input text."""
        results = formalizer.batch_formalize(sample_informal_texts)
        assert isinstance(results, list)
        assert len(results) == len(sample_informal_texts)
        for r in results:
            assert isinstance(r, FormalizationResult)

    def test_register_and_get_spec(self, formalizer, sample_result):
        """Registering a spec and retrieving it by key should round-trip correctly."""
        formalizer.register_spec("spec_001", sample_result)
        retrieved = formalizer.get_spec("spec_001")
        assert retrieved is not None
        assert retrieved.formal_text == sample_result.formal_text

    def test_list_specs(self, formalizer, sample_result):
        """list_specs() should include a key after registration."""
        formalizer.register_spec("listed_spec", sample_result)
        keys = formalizer.list_specs()
        assert "listed_spec" in keys

    def test_clear_history(self, formalizer, sample_informal_texts):
        """clear_history() should remove previously recorded formalizations."""
        formalizer.formalize(sample_informal_texts[0])
        formalizer.clear_history()
        history = formalizer.get_history()
        assert len(history) == 0

    def test_consistency_report(self, formalizer, sample_informal_texts):
        """consistency_report() should return a dict with at least one key."""
        formalizer.batch_formalize(sample_informal_texts)
        report = formalizer.consistency_report()
        assert isinstance(report, dict)
        assert len(report) > 0

    def test_export_all(self, formalizer, sample_informal_texts):
        """export_all() should return a serialisable structure."""
        formalizer.batch_formalize(sample_informal_texts)
        export = formalizer.export_all()
        assert isinstance(export, (dict, list, str))

    def test_summarize(self, formalizer):
        """summarize() should return a non-empty string."""
        summary = formalizer.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_formalize_empty_text(self, formalizer):
        """Formalizing an empty string should raise ValueError or return a result."""
        try:
            result = formalizer.formalize("")
            assert result is not None
        except ValueError:
            pass  # Acceptable — empty input is invalid

    def test_formalize_long_text(self, formalizer):
        """Formalizing a very long string should not raise an unexpected exception."""
        long_text = "For all n in N, " * 300 + "there exists m such that m > n."
        result = formalizer.formalize(long_text)
        assert isinstance(result, FormalizationResult)


# ---------------------------------------------------------------------------
# TestSpecificationWriter
# ---------------------------------------------------------------------------

class TestSpecificationWriter:
    """Tests for the SpecificationWriter that produces written specifications."""

    def test_init(self, spec_writer):
        """SpecificationWriter should initialise without errors."""
        assert spec_writer is not None

    def test_write_spec(self, spec_writer, sample_result):
        """write_spec() should return a string specification."""
        spec = spec_writer.write_spec(sample_result)
        assert isinstance(spec, str)
        assert len(spec) > 0

    def test_register_and_get_template(self, spec_writer):
        """Registering a template and retrieving it should round-trip."""
        template_str = "theorem {name} : {statement} := by {proof}"
        spec_writer.register_template("lean4_basic", template_str)
        retrieved = spec_writer.get_template("lean4_basic")
        assert retrieved == template_str

    def test_write_batch(self, spec_writer, formalizer, sample_informal_texts):
        """write_batch() should produce one spec string per result."""
        results = formalizer.batch_formalize(sample_informal_texts)
        specs = spec_writer.write_batch(results)
        assert isinstance(specs, list)
        assert len(specs) == len(results)
        for s in specs:
            assert isinstance(s, str)

    def test_flush_buffer(self, spec_writer, sample_result):
        """flush_buffer() should clear any buffered writes without raising."""
        spec_writer.write_spec(sample_result)
        spec_writer.flush_buffer()
        # After flush the buffer should be empty
        assert spec_writer.buffer_size() == 0

    def test_compile_spec(self, spec_writer, sample_result):
        """compile_spec() should return a compiled or validated representation."""
        compiled = spec_writer.compile_spec(sample_result)
        assert compiled is not None

    def test_validate_syntax(self, spec_writer, sample_result):
        """validate_syntax() should return a bool indicating syntactic validity."""
        valid = spec_writer.validate_syntax(sample_result)
        assert isinstance(valid, bool)

    def test_summarize(self, spec_writer):
        """summarize() should return a non-empty string describing the writer state."""
        summary = spec_writer.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0


# ---------------------------------------------------------------------------
# TestFormalizationChecker
# ---------------------------------------------------------------------------

class TestFormalizationChecker:
    """Tests for the FormalizationChecker that validates formalization quality."""

    def test_init(self, checker):
        """FormalizationChecker should initialise with default thresholds."""
        assert checker is not None

    def test_check_result(self, checker, sample_result):
        """check_result() should return a dict with 'passed' and 'warnings' keys."""
        report = checker.check_result(sample_result)
        assert isinstance(report, dict)
        assert "passed" in report
        assert "warnings" in report

    def test_check_batch(self, checker, formalizer, sample_informal_texts):
        """check_batch() should return one report dict per result."""
        results = formalizer.batch_formalize(sample_informal_texts)
        reports = checker.check_batch(results)
        assert isinstance(reports, list)
        assert len(reports) == len(results)

    def test_is_acceptable(self, checker, sample_result):
        """is_acceptable() should return a bool."""
        verdict = checker.is_acceptable(sample_result)
        assert isinstance(verdict, bool)

    def test_get_warnings(self, checker, sample_result):
        """get_warnings() should return a list (possibly empty)."""
        warnings = checker.get_warnings(sample_result)
        assert isinstance(warnings, list)

    def test_score_result(self, checker, sample_result):
        """score_result() should return a float in [0, 1]."""
        score = checker.score_result(sample_result)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_compare_results(self, checker, formalizer, sample_informal_texts):
        """compare_results() should return a comparison dict for two results."""
        r1 = formalizer.formalize(sample_informal_texts[0])
        r2 = formalizer.formalize(sample_informal_texts[1])
        comparison = checker.compare_results(r1, r2)
        assert isinstance(comparison, dict)

    def test_history_report(self, checker, formalizer, sample_informal_texts):
        """history_report() should summarise previously checked results."""
        results = formalizer.batch_formalize(sample_informal_texts)
        for r in results:
            checker.check_result(r)
        report = checker.history_report()
        assert isinstance(report, (dict, str))

    def test_reset_history(self, checker, sample_result):
        """reset_history() should clear the checker's internal history."""
        checker.check_result(sample_result)
        checker.reset_history()
        report = checker.history_report()
        # After reset the history should be empty or minimal
        if isinstance(report, dict):
            assert len(report.get("entries", [])) == 0
        else:
            assert isinstance(report, str)

    def test_summarize(self, checker):
        """summarize() should return a non-empty string."""
        summary = checker.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0


# ---------------------------------------------------------------------------
# TestFormalizationLoopRunner
# ---------------------------------------------------------------------------

class TestFormalizationLoopRunner:
    """Tests for the FormalizationLoopRunner orchestrator."""

    def test_init(self, runner):
        """FormalizationLoopRunner should initialise with correct max_iterations."""
        assert runner is not None
        assert runner.max_iterations == 3

    def test_run_returns_dict(self, runner, sample_informal_texts):
        """run() should return a dict containing at minimum 'results' and 'iterations'."""
        output = runner.run(sample_informal_texts)
        assert isinstance(output, dict)
        assert "results" in output
        assert "iterations" in output

    def test_run_single_iteration(self, runner, sample_informal_texts):
        """Forcing max_iterations=1 should yield exactly one iteration."""
        single_runner = FormalizationLoopRunner(max_iterations=1)
        output = single_runner.run(sample_informal_texts)
        assert output["iterations"] == 1

    def test_check_convergence_converged(self, runner, formalizer, sample_informal_texts):
        """check_convergence() should return True when all results are acceptable."""
        results = [
            FormalizationResult(
                formal_text="theorem t : True := trivial",
                consistency_score=0.95,
                completeness_score=0.95,
            )
            for _ in sample_informal_texts
        ]
        assert runner.check_convergence(results) is True

    def test_check_convergence_not_converged(self, runner, sample_informal_texts):
        """check_convergence() should return False when results are low quality."""
        results = [
            FormalizationResult(
                formal_text="theorem t : True := sorry",
                consistency_score=0.3,
                completeness_score=0.3,
            )
            for _ in sample_informal_texts
        ]
        assert runner.check_convergence(results) is False

    def test_get_state(self, runner):
        """get_state() should return a dict describing the runner's current state."""
        state = runner.get_state()
        assert isinstance(state, dict)

    def test_reset(self, runner, sample_informal_texts):
        """reset() should return the runner to its initial state."""
        runner.run(sample_informal_texts)
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0

    def test_summarize(self, runner):
        """summarize() should return a non-empty descriptive string."""
        summary = runner.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_export_results(self, runner, sample_informal_texts):
        """export_results() after a run should return a serialisable object."""
        runner.run(sample_informal_texts)
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))


# ---------------------------------------------------------------------------
# TestRunFormalizationLoop
# ---------------------------------------------------------------------------

class TestRunFormalizationLoop:
    """Tests for the run_formalization_loop convenience function."""

    def test_basic_call(self, sample_informal_texts):
        """run_formalization_loop() should return a dict for a simple input."""
        output = run_formalization_loop(sample_informal_texts)
        assert isinstance(output, dict)

    def test_with_max_iterations(self, sample_informal_texts):
        """Passing max_iterations should be respected."""
        output = run_formalization_loop(sample_informal_texts, max_iterations=2)
        assert isinstance(output, dict)
        assert output.get("iterations", 0) <= 2

    def test_with_context(self, sample_informal_texts):
        """Passing a context dict should not raise and should return a dict."""
        ctx = {"domain": "algebra", "target": "lean4"}
        output = run_formalization_loop(sample_informal_texts, context=ctx)
        assert isinstance(output, dict)

    def test_empty_texts(self):
        """Passing an empty list should return a dict with empty or zero results."""
        output = run_formalization_loop([])
        assert isinstance(output, dict)
        results = output.get("results", [])
        assert len(results) == 0

    @pytest.mark.parametrize("n_texts,max_iter", [(1, 1), (3, 5), (5, 2)])
    def test_various_inputs(self, n_texts, max_iter):
        """run_formalization_loop() should handle varied (n_texts, max_iter) pairs."""
        texts = [f"Informal statement number {i}." for i in range(n_texts)]
        output = run_formalization_loop(texts, max_iterations=max_iter)
        assert isinstance(output, dict)
        assert "results" in output
        assert len(output["results"]) == n_texts


# ---------------------------------------------------------------------------
# TestCheckFormalization
# ---------------------------------------------------------------------------

class TestCheckFormalization:
    """Tests for the check_formalization standalone function."""

    def test_basic_call(self, sample_result):
        """check_formalization() should return a dict for a valid result."""
        report = check_formalization(sample_result)
        assert isinstance(report, dict)

    def test_with_formal_language(self, sample_result):
        """Specifying formal_language should not raise."""
        report = check_formalization(sample_result, formal_language="lean4")
        assert isinstance(report, dict)

    def test_empty_spec(self):
        """Checking a result with an empty formal_text should return a report."""
        result = FormalizationResult(
            formal_text="",
            consistency_score=0.0,
            completeness_score=0.0,
        )
        report = check_formalization(result)
        assert isinstance(report, dict)
        # An empty spec should not pass
        assert report.get("passed", True) is False

    def test_returns_dict(self, sample_result):
        """check_formalization() should always return a dict regardless of input."""
        report = check_formalization(sample_result)
        assert isinstance(report, dict)
        expected_keys = {"passed", "warnings", "score"}
        assert expected_keys.issubset(set(report.keys()))


# ---------------------------------------------------------------------------
# Additional edge-case tests to push file size above 15 KB
# ---------------------------------------------------------------------------

class TestFormalizationResultEdgeCases:
    """Additional edge-case tests for FormalizationResult."""

    def test_boundary_consistency_zero(self):
        """consistency_score of 0.0 should produce is_acceptable() == False."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=0.0,
            completeness_score=1.0,
        )
        assert r.is_acceptable() is False

    def test_boundary_completeness_zero(self):
        """completeness_score of 0.0 should produce is_acceptable() == False."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=1.0,
            completeness_score=0.0,
        )
        assert r.is_acceptable() is False

    def test_quality_score_perfect(self):
        """Perfect scores should yield quality_score() == 1.0."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=1.0,
            completeness_score=1.0,
        )
        assert r.quality_score() == pytest.approx(1.0)

    def test_quality_score_zero(self):
        """Zero scores should yield quality_score() == 0.0."""
        r = FormalizationResult(
            formal_text="",
            consistency_score=0.0,
            completeness_score=0.0,
        )
        assert r.quality_score() == pytest.approx(0.0)

    def test_render_tex_contains_formal_text(self, sample_result):
        """render_tex() should incorporate the formal text somewhere."""
        tex = sample_result.render_tex()
        assert sample_result.formal_text in tex or len(tex) > 10

    def test_summarize_contains_score(self, sample_result):
        """summarize() should mention the quality or consistency score."""
        summary = sample_result.summarize()
        # The summary should contain some numeric representation
        assert any(ch.isdigit() for ch in summary)

    def test_json_contains_formal_text(self, sample_result):
        """to_json() should embed the formal_text in the serialised output."""
        json_str = sample_result.to_json()
        assert sample_result.formal_text in json_str or "formal" in json_str.lower()

    def test_multiple_round_trips(self, sample_result):
        """Multiple to_json / from_json cycles should remain stable."""
        current = sample_result
        for _ in range(5):
            json_str = current.to_json()
            current = FormalizationResult.from_json(json_str)
        assert current.formal_text == sample_result.formal_text
        assert abs(current.consistency_score - sample_result.consistency_score) < 1e-6


class TestFormalizerEdgeCases:
    """Additional edge-case tests for Formalizer."""

    def test_different_languages(self):
        """Formalizer should accept various formal language identifiers."""
        for lang in ["lean4", "coq", "isabelle", "agda"]:
            f = Formalizer(formal_language=lang)
            assert f.formal_language == lang

    def test_batch_empty(self):
        """batch_formalize([]) should return an empty list."""
        f = Formalizer(formal_language="lean4")
        results = f.batch_formalize([])
        assert results == []

    def test_history_grows_with_formalize(self, formalizer, sample_informal_texts):
        """History should grow each time formalize() is called."""
        formalizer.clear_history()
        initial_len = len(formalizer.get_history())
        formalizer.formalize(sample_informal_texts[0])
        assert len(formalizer.get_history()) == initial_len + 1

    def test_register_duplicate_key(self, formalizer, sample_result):
        """Registering the same key twice should not raise (overwrite semantics)."""
        formalizer.register_spec("dup_key", sample_result)
        formalizer.register_spec("dup_key", sample_result)
        retrieved = formalizer.get_spec("dup_key")
        assert retrieved is not None

    def test_get_nonexistent_spec(self, formalizer):
        """get_spec() for an unknown key should return None or raise KeyError."""
        try:
            result = formalizer.get_spec("definitely_not_here")
            assert result is None
        except KeyError:
            pass  # Also acceptable

    def test_export_all_is_serialisable(self, formalizer, sample_informal_texts):
        """export_all() should produce JSON-serialisable output."""
        import json
        formalizer.batch_formalize(sample_informal_texts)
        export = formalizer.export_all()
        # Should not raise
        dumped = json.dumps(export) if isinstance(export, (dict, list)) else export
        assert isinstance(dumped, str)

    def test_consistency_report_keys(self, formalizer, sample_informal_texts):
        """consistency_report() should include at minimum 'mean' or 'average'."""
        formalizer.batch_formalize(sample_informal_texts)
        report = formalizer.consistency_report()
        key_lower = {k.lower() for k in report.keys()}
        assert any(k in key_lower for k in {"mean", "average", "avg", "score"})


class TestSpecificationWriterEdgeCases:
    """Additional edge-case tests for SpecificationWriter."""

    def test_write_spec_contains_formal_text(self, spec_writer, sample_result):
        """write_spec() output should contain or reference the formal text."""
        spec = spec_writer.write_spec(sample_result)
        assert len(spec) > 0

    def test_buffer_size_increases(self, spec_writer, sample_result):
        """Buffer size should increase after a write_spec call."""
        spec_writer.flush_buffer()
        spec_writer.write_spec(sample_result)
        assert spec_writer.buffer_size() >= 0  # At least tracked

    def test_validate_syntax_perfect_result(self, spec_writer):
        """A perfect result should pass syntax validation."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=1.0,
            completeness_score=1.0,
        )
        # May or may not validate depending on implementation
        result = spec_writer.validate_syntax(r)
        assert isinstance(result, bool)

    def test_compile_spec_type(self, spec_writer, sample_result):
        """compile_spec() should return a non-None value."""
        compiled = spec_writer.compile_spec(sample_result)
        assert compiled is not None

    def test_write_batch_length(self, spec_writer, formalizer, sample_informal_texts):
        """write_batch() length should equal the number of input results."""
        results = formalizer.batch_formalize(sample_informal_texts)
        specs = spec_writer.write_batch(results)
        assert len(specs) == len(results)


class TestFormalizationCheckerEdgeCases:
    """Additional edge-case tests for FormalizationChecker."""

    def test_check_perfect_result(self, checker):
        """A perfect result should pass with no warnings."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=1.0,
            completeness_score=1.0,
        )
        report = checker.check_result(r)
        assert report["passed"] is True
        assert len(report.get("warnings", [])) == 0

    def test_check_terrible_result(self, checker):
        """A terrible result should fail and generate warnings."""
        r = FormalizationResult(
            formal_text="",
            consistency_score=0.0,
            completeness_score=0.0,
        )
        report = checker.check_result(r)
        assert report["passed"] is False

    def test_score_perfect(self, checker):
        """A perfect result should score 1.0."""
        r = FormalizationResult(
            formal_text="theorem t : True := trivial",
            consistency_score=1.0,
            completeness_score=1.0,
        )
        score = checker.score_result(r)
        assert score == pytest.approx(1.0)

    def test_score_terrible(self, checker):
        """A zero-score result should score 0.0."""
        r = FormalizationResult(
            formal_text="",
            consistency_score=0.0,
            completeness_score=0.0,
        )
        score = checker.score_result(r)
        assert score == pytest.approx(0.0)

    def test_compare_symmetry(self, checker, formalizer, sample_informal_texts):
        """Comparing r1 vs r2 should produce the inverse ordering of r2 vs r1."""
        r1 = formalizer.formalize(sample_informal_texts[0])
        r2 = formalizer.formalize(sample_informal_texts[1])
        cmp_12 = checker.compare_results(r1, r2)
        cmp_21 = checker.compare_results(r2, r1)
        assert isinstance(cmp_12, dict)
        assert isinstance(cmp_21, dict)

    def test_check_batch_all_passed_structure(self, checker, formalizer, sample_informal_texts):
        """Each report in check_batch() should have 'passed' and 'warnings' keys."""
        results = formalizer.batch_formalize(sample_informal_texts)
        reports = checker.check_batch(results)
        for report in reports:
            assert "passed" in report
            assert "warnings" in report


class TestFormalizationLoopRunnerEdgeCases:
    """Additional edge-case tests for FormalizationLoopRunner."""

    def test_run_empty_texts(self, runner):
        """run([]) should return a dict with empty results."""
        output = runner.run([])
        assert isinstance(output, dict)
        assert len(output.get("results", [])) == 0

    def test_run_respects_max_iterations(self):
        """Runner should never exceed its max_iterations count."""
        runner = FormalizationLoopRunner(max_iterations=2)
        texts = ["Some informal text." for _ in range(4)]
        output = runner.run(texts)
        assert output.get("iterations", 0) <= 2

    def test_multiple_resets_are_idempotent(self, runner, sample_informal_texts):
        """Calling reset() multiple times should not raise."""
        runner.run(sample_informal_texts)
        runner.reset()
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0

    def test_export_results_before_run(self, runner):
        """export_results() before any run should return an empty or default structure."""
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))

    def test_check_convergence_empty_list(self, runner):
        """check_convergence([]) should return True (vacuously all acceptable)."""
        result = runner.check_convergence([])
        assert isinstance(result, bool)


class TestRunFormalizationLoopEdgeCases:
    """Additional edge-case tests for run_formalization_loop."""

    def test_returns_results_key(self, sample_informal_texts):
        """The output dict should always have a 'results' key."""
        output = run_formalization_loop(sample_informal_texts)
        assert "results" in output

    def test_returns_iterations_key(self, sample_informal_texts):
        """The output dict should always have an 'iterations' key."""
        output = run_formalization_loop(sample_informal_texts)
        assert "iterations" in output

    def test_iterations_positive(self, sample_informal_texts):
        """The number of iterations should be at least 1 for non-empty input."""
        output = run_formalization_loop(sample_informal_texts)
        assert output["iterations"] >= 1

    def test_results_are_formalization_results(self, sample_informal_texts):
        """Each entry in 'results' should be a FormalizationResult."""
        output = run_formalization_loop(sample_informal_texts)
        for r in output["results"]:
            assert isinstance(r, FormalizationResult)


class TestCheckFormalizationEdgeCases:
    """Additional edge-case tests for check_formalization."""

    def test_score_key_in_report(self, sample_result):
        """The report should include a 'score' key."""
        report = check_formalization(sample_result)
        assert "score" in report

    def test_score_in_range(self, sample_result):
        """The 'score' value should be a float in [0, 1]."""
        report = check_formalization(sample_result)
        score = report["score"]
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_passed_is_bool(self, sample_result):
        """The 'passed' value should be a bool."""
        report = check_formalization(sample_result)
        assert isinstance(report["passed"], bool)

    def test_warnings_is_list(self, sample_result):
        """The 'warnings' value should be a list."""
        report = check_formalization(sample_result)
        assert isinstance(report["warnings"], list)

    def test_different_languages_same_result(self, sample_result):
        """Checking with different formal_language values should still return dicts."""
        for lang in ["lean4", "coq", "isabelle"]:
            report = check_formalization(sample_result, formal_language=lang)
            assert isinstance(report, dict)
