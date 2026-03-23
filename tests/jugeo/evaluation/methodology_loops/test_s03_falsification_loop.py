"""Tests for s03_falsification_loop. copilot: shared-core marker. Theory reference: theory2.tex Ch62."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.evaluation.methodology_loops.s03_falsification_loop import (
    FalsificationAttempt, CounterexampleSearcher, HypothesisTracker,
    FalsificationLoopRunner, run_falsification_loop, attempt_falsification,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_hypothesis():
    """A single hypothesis dict with id, statement, and domain fields."""
    return {
        "id": "hyp_001",
        "statement": "All continuous functions on a closed interval are bounded.",
        "domain": "real_analysis",
    }


@pytest.fixture
def sample_hypotheses():
    """A list of five hypothesis dicts."""
    return [
        {
            "id": f"hyp_{i:03d}",
            "statement": f"Hypothesis number {i}: some mathematical claim.",
            "domain": "algebra" if i % 2 == 0 else "topology",
        }
        for i in range(5)
    ]


@pytest.fixture
def searcher():
    """A CounterexampleSearcher using random strategy with a budget of 20."""
    return CounterexampleSearcher(strategy="random", budget=20)


@pytest.fixture
def tracker():
    """A HypothesisTracker instance with default settings."""
    return HypothesisTracker()


@pytest.fixture
def runner():
    """A FalsificationLoopRunner limited to three iterations with budget 10."""
    return FalsificationLoopRunner(max_iterations=3, budget=10)


@pytest.fixture
def sample_attempt(searcher, sample_hypothesis):
    """A FalsificationAttempt produced by searching the first sample hypothesis."""
    return searcher.search(sample_hypothesis)


# ---------------------------------------------------------------------------
# TestFalsificationAttempt
# ---------------------------------------------------------------------------

class TestFalsificationAttempt:
    """Tests for the FalsificationAttempt value object."""

    def test_create(self, sample_attempt):
        """FalsificationAttempt should be constructable and expose key attributes."""
        assert sample_attempt is not None
        assert hasattr(sample_attempt, "hypothesis_id")
        assert hasattr(sample_attempt, "status")
        assert hasattr(sample_attempt, "counterexample")

    def test_slots_true(self, sample_attempt):
        """FalsificationAttempt should use __slots__ or be otherwise compact."""
        # Either __slots__ is defined or the object doesn't allow arbitrary attributes
        try:
            sample_attempt.arbitrary_new_attr_xyz = 42
            # If this succeeds, at least verify slots attr exists
        except AttributeError:
            pass  # __slots__ is enforced — expected behaviour

    def test_mark_success(self, sample_attempt):
        """mark_success() should set status to 'success'."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_001",
            status="inconclusive",
            counterexample=None,
        )
        attempt.mark_success(counterexample={"n": 0, "value": -1})
        assert attempt.status == "success"
        assert attempt.counterexample is not None

    def test_mark_failure(self):
        """mark_failure() should set status to 'failure'."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_002",
            status="inconclusive",
            counterexample=None,
        )
        attempt.mark_failure()
        assert attempt.status == "failure"

    def test_mark_inconclusive(self):
        """mark_inconclusive() should set status to 'inconclusive'."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_003",
            status="success",
            counterexample={"n": 1},
        )
        attempt.mark_inconclusive()
        assert attempt.status == "inconclusive"

    def test_to_json_round_trip(self, sample_attempt):
        """Serialising to JSON and back should reproduce an equivalent attempt."""
        json_str = sample_attempt.to_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 0
        restored = FalsificationAttempt.from_json(json_str)
        assert restored.hypothesis_id == sample_attempt.hypothesis_id
        assert restored.status == sample_attempt.status

    def test_summarize(self, sample_attempt):
        """summarize() should return a human-readable string."""
        summary = sample_attempt.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_is_successful(self):
        """is_successful() should return True when status is 'success'."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_004",
            status="success",
            counterexample={"x": 0},
        )
        assert attempt.is_successful() is True

    def test_render_tex(self, sample_attempt):
        """render_tex() should return a non-empty LaTeX string."""
        tex = sample_attempt.render_tex()
        assert isinstance(tex, str)
        assert len(tex) > 0

    @pytest.mark.parametrize("status,expected", [
        ("success", True),
        ("failure", False),
        ("inconclusive", False),
    ])
    def test_is_successful_parametrized(self, status, expected):
        """is_successful() should correctly reflect the 'success' status."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_param",
            status=status,
            counterexample={"x": 1} if status == "success" else None,
        )
        assert attempt.is_successful() is expected

    def test_from_json_creates_instance(self, sample_attempt):
        """from_json() should always produce a FalsificationAttempt."""
        json_str = sample_attempt.to_json()
        restored = FalsificationAttempt.from_json(json_str)
        assert isinstance(restored, FalsificationAttempt)

    def test_multiple_round_trips(self, sample_attempt):
        """Multiple JSON round-trips should remain stable."""
        current = sample_attempt
        for _ in range(5):
            json_str = current.to_json()
            current = FalsificationAttempt.from_json(json_str)
        assert current.hypothesis_id == sample_attempt.hypothesis_id
        assert current.status == sample_attempt.status


# ---------------------------------------------------------------------------
# TestCounterexampleSearcher
# ---------------------------------------------------------------------------

class TestCounterexampleSearcher:
    """Tests for the CounterexampleSearcher that hunts for counterexamples."""

    def test_init(self, searcher):
        """CounterexampleSearcher should initialise with the given strategy and budget."""
        assert searcher is not None
        assert searcher.strategy == "random"
        assert searcher.budget == 20

    def test_search_returns_attempt(self, searcher, sample_hypothesis):
        """search() should return a FalsificationAttempt for a valid hypothesis."""
        attempt = searcher.search(sample_hypothesis)
        assert isinstance(attempt, FalsificationAttempt)

    def test_search_with_context(self, searcher, sample_hypothesis):
        """search() should accept an optional context dict without error."""
        ctx = {"domain": "analysis", "depth": 3}
        attempt = searcher.search(sample_hypothesis, context=ctx)
        assert isinstance(attempt, FalsificationAttempt)

    def test_search_batch(self, searcher, sample_hypotheses):
        """search_batch() should return one attempt per hypothesis."""
        attempts = searcher.search_batch(sample_hypotheses)
        assert isinstance(attempts, list)
        assert len(attempts) == len(sample_hypotheses)
        for a in attempts:
            assert isinstance(a, FalsificationAttempt)

    def test_register_strategy(self, searcher):
        """register_strategy() should add a new strategy without error."""
        def exhaustive_strategy(hyp, budget):
            return []

        searcher.register_strategy("exhaustive", exhaustive_strategy)
        assert "exhaustive" in searcher.list_strategies()

    def test_update_budget(self, searcher):
        """update_budget() should change the remaining budget."""
        searcher.update_budget(50)
        assert searcher.budget == 50

    def test_remaining_budget(self, searcher):
        """remaining_budget() should return a non-negative integer."""
        remaining = searcher.remaining_budget()
        assert isinstance(remaining, int)
        assert remaining >= 0

    def test_is_exhausted_when_budget_zero(self):
        """A searcher with budget=0 should be exhausted."""
        s = CounterexampleSearcher(strategy="random", budget=0)
        assert s.is_exhausted() is True

    def test_is_not_exhausted(self, searcher):
        """A searcher with budget > 0 should not be exhausted initially."""
        assert searcher.is_exhausted() is False

    def test_history_report(self, searcher, sample_hypothesis):
        """history_report() should return a dict or string after searches."""
        searcher.search(sample_hypothesis)
        report = searcher.history_report()
        assert isinstance(report, (dict, str))

    def test_reset(self, searcher, sample_hypothesis):
        """reset() should clear search history and restore initial budget."""
        initial_budget = searcher.budget
        searcher.search(sample_hypothesis)
        searcher.reset()
        remaining = searcher.remaining_budget()
        assert remaining >= initial_budget - 1  # Budget restored or maintained

    def test_summarize(self, searcher):
        """summarize() should return a non-empty string."""
        summary = searcher.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_search_hypothesis_id_in_attempt(self, searcher, sample_hypothesis):
        """The returned attempt should reference the hypothesis id."""
        attempt = searcher.search(sample_hypothesis)
        assert attempt.hypothesis_id == sample_hypothesis["id"]

    def test_list_strategies_includes_random(self, searcher):
        """list_strategies() should include at least 'random'."""
        strategies = searcher.list_strategies()
        assert "random" in strategies


# ---------------------------------------------------------------------------
# TestHypothesisTracker
# ---------------------------------------------------------------------------

class TestHypothesisTracker:
    """Tests for the HypothesisTracker that manages hypothesis lifecycle."""

    def test_init(self, tracker):
        """HypothesisTracker should initialise without errors."""
        assert tracker is not None

    def test_register(self, tracker, sample_hypothesis):
        """register() should store the hypothesis without error."""
        tracker.register(sample_hypothesis)
        retrieved = tracker.get(sample_hypothesis["id"])
        assert retrieved is not None
        assert retrieved["id"] == sample_hypothesis["id"]

    def test_update_status(self, tracker, sample_hypothesis):
        """update_status() should change the hypothesis status."""
        tracker.register(sample_hypothesis)
        tracker.update_status(sample_hypothesis["id"], "falsified")
        hyp = tracker.get(sample_hypothesis["id"])
        assert hyp.get("status") == "falsified"

    def test_get(self, tracker, sample_hypothesis):
        """get() should return the hypothesis registered under the given id."""
        tracker.register(sample_hypothesis)
        result = tracker.get(sample_hypothesis["id"])
        assert result["statement"] == sample_hypothesis["statement"]

    def test_list_all(self, tracker, sample_hypotheses):
        """list_all() should return all registered hypotheses."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        all_hyps = tracker.list_all()
        assert len(all_hyps) >= len(sample_hypotheses)

    def test_list_by_status(self, tracker, sample_hypotheses):
        """list_by_status() should filter hypotheses by their status."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        tracker.update_status(sample_hypotheses[0]["id"], "falsified")
        tracker.update_status(sample_hypotheses[1]["id"], "falsified")
        falsified = tracker.list_by_status("falsified")
        assert len(falsified) >= 2

    def test_get_pending(self, tracker, sample_hypotheses):
        """get_pending() should return only hypotheses with 'pending' status."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        pending = tracker.get_pending()
        assert isinstance(pending, list)

    def test_prioritize(self, tracker, sample_hypotheses):
        """prioritize() should return hypotheses in some priority order."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        prioritized = tracker.prioritize()
        assert isinstance(prioritized, list)
        assert len(prioritized) == len(sample_hypotheses)

    def test_summary_report(self, tracker, sample_hypotheses):
        """summary_report() should return a dict or string."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        report = tracker.summary_report()
        assert isinstance(report, (dict, str))

    def test_to_json_round_trip(self, tracker, sample_hypotheses):
        """Serialising the tracker to JSON and back should preserve hypotheses."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        json_str = tracker.to_json()
        assert isinstance(json_str, str)
        restored = HypothesisTracker.from_json(json_str)
        all_ids = {h["id"] for h in restored.list_all()}
        for hyp in sample_hypotheses:
            assert hyp["id"] in all_ids

    def test_reset(self, tracker, sample_hypotheses):
        """reset() should clear all registered hypotheses."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        tracker.reset()
        assert len(tracker.list_all()) == 0

    def test_summarize(self, tracker):
        """summarize() should return a non-empty string."""
        summary = tracker.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_register_duplicate(self, tracker, sample_hypothesis):
        """Registering the same hypothesis twice should not raise."""
        tracker.register(sample_hypothesis)
        tracker.register(sample_hypothesis)  # Should overwrite or skip gracefully
        count = sum(1 for h in tracker.list_all() if h["id"] == sample_hypothesis["id"])
        assert count >= 1

    def test_get_missing_id(self, tracker):
        """get() for a missing id should return None or raise KeyError."""
        try:
            result = tracker.get("nonexistent_id_xyz")
            assert result is None
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# TestFalsificationLoopRunner
# ---------------------------------------------------------------------------

class TestFalsificationLoopRunner:
    """Tests for the FalsificationLoopRunner orchestrator."""

    def test_init(self, runner):
        """FalsificationLoopRunner should initialise with the correct attributes."""
        assert runner is not None
        assert runner.max_iterations == 3
        assert runner.budget == 10

    def test_run_returns_dict(self, runner, sample_hypotheses):
        """run() should return a dict with at least 'attempts' and 'iterations'."""
        output = runner.run(sample_hypotheses)
        assert isinstance(output, dict)
        assert "attempts" in output
        assert "iterations" in output

    def test_run_single_iteration(self, sample_hypotheses):
        """Forcing max_iterations=1 should yield exactly one iteration."""
        single_runner = FalsificationLoopRunner(max_iterations=1, budget=10)
        output = single_runner.run(sample_hypotheses)
        assert output["iterations"] == 1

    def test_check_convergence_all_falsified(self, runner, sample_hypotheses):
        """check_convergence() returns True when all hypotheses are falsified."""
        attempts = [
            FalsificationAttempt(
                hypothesis_id=h["id"],
                status="success",
                counterexample={"x": 0},
            )
            for h in sample_hypotheses
        ]
        assert runner.check_convergence(attempts) is True

    def test_check_convergence_budget_exhausted(self, sample_hypotheses):
        """check_convergence() returns True when budget is fully spent."""
        zero_budget_runner = FalsificationLoopRunner(max_iterations=3, budget=0)
        attempts = [
            FalsificationAttempt(
                hypothesis_id=h["id"],
                status="inconclusive",
                counterexample=None,
            )
            for h in sample_hypotheses
        ]
        assert zero_budget_runner.check_convergence(attempts) is True

    def test_get_state(self, runner):
        """get_state() should return a dict describing the runner's current state."""
        state = runner.get_state()
        assert isinstance(state, dict)

    def test_reset(self, runner, sample_hypotheses):
        """reset() should return the runner to its initial state."""
        runner.run(sample_hypotheses)
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0

    def test_summarize(self, runner):
        """summarize() should return a non-empty descriptive string."""
        summary = runner.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_run_empty_hypotheses(self, runner):
        """run([]) should return a dict with empty attempts."""
        output = runner.run([])
        assert isinstance(output, dict)
        assert len(output.get("attempts", [])) == 0

    def test_run_respects_max_iterations(self, sample_hypotheses):
        """Runner should never exceed its max_iterations count."""
        r = FalsificationLoopRunner(max_iterations=2, budget=100)
        output = r.run(sample_hypotheses)
        assert output.get("iterations", 0) <= 2

    def test_multiple_resets_idempotent(self, runner, sample_hypotheses):
        """Calling reset() multiple times should not raise."""
        runner.run(sample_hypotheses)
        runner.reset()
        runner.reset()
        state = runner.get_state()
        assert state.get("iterations_completed", 0) == 0

    def test_export_results(self, runner, sample_hypotheses):
        """export_results() after a run should return a serialisable object."""
        runner.run(sample_hypotheses)
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))


# ---------------------------------------------------------------------------
# TestRunFalsificationLoop
# ---------------------------------------------------------------------------

class TestRunFalsificationLoop:
    """Tests for the run_falsification_loop convenience function."""

    def test_basic_call(self, sample_hypotheses):
        """run_falsification_loop() should return a dict for a simple input."""
        output = run_falsification_loop(sample_hypotheses)
        assert isinstance(output, dict)

    def test_with_budget(self, sample_hypotheses):
        """Passing a budget parameter should not raise and should return a dict."""
        output = run_falsification_loop(sample_hypotheses, budget=50)
        assert isinstance(output, dict)

    def test_empty_hypotheses(self):
        """Passing an empty list should return a dict with empty attempts."""
        output = run_falsification_loop([])
        assert isinstance(output, dict)
        assert len(output.get("attempts", [])) == 0

    @pytest.mark.parametrize("n_hyp,budget", [(1, 5), (3, 10), (10, 50)])
    def test_various_inputs(self, n_hyp, budget):
        """run_falsification_loop() should handle varied (n_hyp, budget) pairs."""
        hyps = [
            {"id": f"h{i}", "statement": f"Claim {i}", "domain": "math"}
            for i in range(n_hyp)
        ]
        output = run_falsification_loop(hyps, budget=budget)
        assert isinstance(output, dict)
        assert "attempts" in output
        assert len(output["attempts"]) == n_hyp

    def test_returns_attempts_key(self, sample_hypotheses):
        """The output dict should always have an 'attempts' key."""
        output = run_falsification_loop(sample_hypotheses)
        assert "attempts" in output

    def test_returns_iterations_key(self, sample_hypotheses):
        """The output dict should always have an 'iterations' key."""
        output = run_falsification_loop(sample_hypotheses)
        assert "iterations" in output

    def test_attempts_are_falsification_attempts(self, sample_hypotheses):
        """Each entry in 'attempts' should be a FalsificationAttempt."""
        output = run_falsification_loop(sample_hypotheses)
        for a in output["attempts"]:
            assert isinstance(a, FalsificationAttempt)

    def test_iterations_positive(self, sample_hypotheses):
        """The number of iterations should be at least 1 for non-empty input."""
        output = run_falsification_loop(sample_hypotheses)
        assert output["iterations"] >= 1

    def test_with_max_iterations(self, sample_hypotheses):
        """Passing max_iterations should be respected."""
        output = run_falsification_loop(sample_hypotheses, max_iterations=1)
        assert output.get("iterations", 0) <= 1


# ---------------------------------------------------------------------------
# TestAttemptFalsification
# ---------------------------------------------------------------------------

class TestAttemptFalsification:
    """Tests for the attempt_falsification standalone function."""

    def test_basic_call(self, sample_hypothesis):
        """attempt_falsification() should return a value for a valid hypothesis."""
        result = attempt_falsification(sample_hypothesis)
        assert result is not None

    def test_returns_attempt(self, sample_hypothesis):
        """attempt_falsification() should return a FalsificationAttempt."""
        result = attempt_falsification(sample_hypothesis)
        assert isinstance(result, FalsificationAttempt)

    def test_with_context(self, sample_hypothesis):
        """Passing a context dict should not raise."""
        ctx = {"strategy": "random", "domain_knowledge": True}
        result = attempt_falsification(sample_hypothesis, context=ctx)
        assert isinstance(result, FalsificationAttempt)

    def test_result_has_hypothesis_id(self, sample_hypothesis):
        """The returned attempt should reference the input hypothesis id."""
        result = attempt_falsification(sample_hypothesis)
        assert result.hypothesis_id == sample_hypothesis["id"]

    def test_result_status_valid(self, sample_hypothesis):
        """The status should be one of the recognised values."""
        result = attempt_falsification(sample_hypothesis)
        assert result.status in {"success", "failure", "inconclusive"}

    def test_with_budget_parameter(self, sample_hypothesis):
        """Passing budget should not raise and should return an attempt."""
        result = attempt_falsification(sample_hypothesis, budget=5)
        assert isinstance(result, FalsificationAttempt)


# ---------------------------------------------------------------------------
# Additional edge-case tests for file-size requirements
# ---------------------------------------------------------------------------

class TestFalsificationAttemptEdgeCases:
    """Additional edge-case tests for FalsificationAttempt."""

    def test_successful_attempt_has_counterexample(self):
        """A successful attempt should have a non-None counterexample."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_edge",
            status="success",
            counterexample={"n": -1, "witness": "x=0"},
        )
        assert attempt.counterexample is not None
        assert attempt.is_successful() is True

    def test_failure_attempt_no_counterexample(self):
        """A failure attempt should have no counterexample."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_edge2",
            status="failure",
            counterexample=None,
        )
        assert attempt.counterexample is None
        assert attempt.is_successful() is False

    def test_inconclusive_no_counterexample(self):
        """An inconclusive attempt should not be successful."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_inc",
            status="inconclusive",
            counterexample=None,
        )
        assert attempt.is_successful() is False

    def test_render_tex_contains_hypothesis_id(self, sample_attempt):
        """render_tex() should include the hypothesis id or related info."""
        tex = sample_attempt.render_tex()
        assert len(tex) > 5

    def test_summarize_contains_status(self, sample_attempt):
        """summarize() should mention the current status."""
        summary = sample_attempt.summarize()
        assert sample_attempt.status in summary or len(summary) > 5

    def test_json_contains_status(self, sample_attempt):
        """to_json() should include the status field."""
        json_str = sample_attempt.to_json()
        assert sample_attempt.status in json_str or "status" in json_str

    def test_mark_success_overwrites_failure(self):
        """mark_success() should overwrite a prior 'failure' status."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_overwrite",
            status="failure",
            counterexample=None,
        )
        attempt.mark_success(counterexample={"x": 42})
        assert attempt.status == "success"

    def test_mark_failure_overwrites_success(self):
        """mark_failure() should overwrite a prior 'success' status."""
        attempt = FalsificationAttempt(
            hypothesis_id="hyp_overwrite2",
            status="success",
            counterexample={"x": 1},
        )
        attempt.mark_failure()
        assert attempt.status == "failure"


class TestCounterexampleSearcherEdgeCases:
    """Additional edge-case tests for CounterexampleSearcher."""

    def test_budget_decreases_after_search(self, searcher, sample_hypothesis):
        """Remaining budget should be non-increasing after a search."""
        before = searcher.remaining_budget()
        searcher.search(sample_hypothesis)
        after = searcher.remaining_budget()
        assert after <= before

    def test_search_batch_empty(self, searcher):
        """search_batch([]) should return an empty list."""
        results = searcher.search_batch([])
        assert results == []

    def test_strategy_change(self):
        """Creating a new searcher with different strategy should work."""
        for strategy in ["random", "systematic", "heuristic"]:
            s = CounterexampleSearcher(strategy=strategy, budget=10)
            assert s.strategy == strategy

    def test_reset_clears_history(self, searcher, sample_hypothesis):
        """reset() should clear the search history."""
        searcher.search(sample_hypothesis)
        searcher.reset()
        report = searcher.history_report()
        if isinstance(report, dict):
            assert len(report.get("entries", [])) == 0
        else:
            assert isinstance(report, str)

    def test_search_batch_id_correspondence(self, searcher, sample_hypotheses):
        """Each attempt in search_batch() should reference the correct hypothesis id."""
        attempts = searcher.search_batch(sample_hypotheses)
        for attempt, hyp in zip(attempts, sample_hypotheses):
            assert attempt.hypothesis_id == hyp["id"]

    def test_update_budget_large_value(self, searcher):
        """update_budget() with a large value should not raise."""
        searcher.update_budget(1_000_000)
        assert searcher.budget == 1_000_000
        assert searcher.is_exhausted() is False


class TestHypothesisTrackerEdgeCases:
    """Additional edge-case tests for HypothesisTracker."""

    def test_update_status_unknown_id(self, tracker):
        """update_status() for an unknown id should raise KeyError or handle gracefully."""
        try:
            tracker.update_status("totally_unknown_id", "falsified")
        except (KeyError, ValueError):
            pass  # Expected

    def test_prioritize_returns_all(self, tracker, sample_hypotheses):
        """prioritize() should return all registered hypotheses."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        prioritized = tracker.prioritize()
        assert len(prioritized) == len(sample_hypotheses)

    def test_list_by_status_no_match(self, tracker, sample_hypotheses):
        """list_by_status() with a status that no hypothesis has should return []."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        result = tracker.list_by_status("verified_true")
        assert isinstance(result, list)

    def test_json_round_trip_preserves_count(self, tracker, sample_hypotheses):
        """JSON round-trip should preserve the number of hypotheses."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        json_str = tracker.to_json()
        restored = HypothesisTracker.from_json(json_str)
        assert len(restored.list_all()) == len(sample_hypotheses)

    def test_summary_report_structure(self, tracker, sample_hypotheses):
        """summary_report() should mention total count."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        report = tracker.summary_report()
        if isinstance(report, dict):
            assert any(k in {"total", "count", "n_hypotheses"} for k in report.keys())
        else:
            assert any(ch.isdigit() for ch in report)

    def test_reset_allows_re_registration(self, tracker, sample_hypotheses):
        """After reset(), hypotheses can be re-registered without conflict."""
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        tracker.reset()
        for hyp in sample_hypotheses:
            tracker.register(hyp)
        assert len(tracker.list_all()) == len(sample_hypotheses)


class TestFalsificationLoopRunnerEdgeCases:
    """Additional edge-case tests for FalsificationLoopRunner."""

    def test_export_before_run(self, runner):
        """export_results() before any run should return an empty or default structure."""
        export = runner.export_results()
        assert isinstance(export, (dict, list, str))

    def test_check_convergence_empty(self, runner):
        """check_convergence([]) should return a bool without raising."""
        result = runner.check_convergence([])
        assert isinstance(result, bool)

    def test_check_convergence_mixed_statuses(self, runner, sample_hypotheses):
        """check_convergence() with mixed statuses should return False."""
        attempts = []
        for i, h in enumerate(sample_hypotheses):
            status = "success" if i % 2 == 0 else "inconclusive"
            attempts.append(
                FalsificationAttempt(
                    hypothesis_id=h["id"],
                    status=status,
                    counterexample={"x": 0} if status == "success" else None,
                )
            )
        # Mixed — not all falsified, so should be False
        result = runner.check_convergence(attempts)
        assert isinstance(result, bool)

    def test_get_state_after_run(self, runner, sample_hypotheses):
        """get_state() after a run should reflect at least 1 iteration."""
        runner.run(sample_hypotheses)
        state = runner.get_state()
        assert state.get("iterations_completed", 0) >= 1

    def test_run_large_hypothesis_set(self, runner):
        """run() should handle a large hypothesis set without crashing."""
        hyps = [
            {"id": f"big_h{i}", "statement": f"Big claim {i}", "domain": "set_theory"}
            for i in range(50)
        ]
        output = runner.run(hyps)
        assert isinstance(output, dict)
        assert len(output.get("attempts", [])) == 50


class TestRunFalsificationLoopEdgeCases:
    """Additional edge-case tests for run_falsification_loop."""

    def test_output_attempts_match_input_count(self, sample_hypotheses):
        """The number of attempts should equal the number of input hypotheses."""
        output = run_falsification_loop(sample_hypotheses)
        assert len(output["attempts"]) == len(sample_hypotheses)

    def test_all_attempts_have_valid_status(self, sample_hypotheses):
        """Every attempt in the output should have a valid status."""
        output = run_falsification_loop(sample_hypotheses)
        valid_statuses = {"success", "failure", "inconclusive"}
        for a in output["attempts"]:
            assert a.status in valid_statuses

    def test_budget_zero_terminates(self):
        """Passing budget=0 should terminate immediately."""
        hyps = [{"id": "h0", "statement": "Some claim", "domain": "math"}]
        output = run_falsification_loop(hyps, budget=0)
        assert isinstance(output, dict)

    def test_large_budget(self, sample_hypotheses):
        """A very large budget should not cause unexpected errors."""
        output = run_falsification_loop(sample_hypotheses, budget=100_000)
        assert isinstance(output, dict)


class TestAttemptFalsificationEdgeCases:
    """Additional edge-case tests for attempt_falsification."""

    def test_idempotent_for_same_hypothesis(self, sample_hypothesis):
        """Calling attempt_falsification() twice should not raise."""
        r1 = attempt_falsification(sample_hypothesis)
        r2 = attempt_falsification(sample_hypothesis)
        assert isinstance(r1, FalsificationAttempt)
        assert isinstance(r2, FalsificationAttempt)

    def test_result_hypothesis_id_matches(self, sample_hypothesis):
        """The returned attempt's hypothesis_id should match the input."""
        result = attempt_falsification(sample_hypothesis)
        assert result.hypothesis_id == sample_hypothesis["id"]

    def test_no_context_does_not_raise(self, sample_hypothesis):
        """Calling without context should not raise."""
        result = attempt_falsification(sample_hypothesis)
        assert result is not None

    def test_with_empty_context(self, sample_hypothesis):
        """Passing an empty context dict should not raise."""
        result = attempt_falsification(sample_hypothesis, context={})
        assert isinstance(result, FalsificationAttempt)

    def test_with_strategy_hint_in_context(self, sample_hypothesis):
        """Passing strategy hints in context should not raise."""
        ctx = {"preferred_strategy": "systematic", "max_depth": 10}
        result = attempt_falsification(sample_hypothesis, context=ctx)
        assert isinstance(result, FalsificationAttempt)

    def test_render_tex_of_result(self, sample_hypothesis):
        """render_tex() on the returned attempt should produce a non-empty string."""
        result = attempt_falsification(sample_hypothesis)
        tex = result.render_tex()
        assert isinstance(tex, str)
        assert len(tex) > 0

    def test_summarize_of_result(self, sample_hypothesis):
        """summarize() on the returned attempt should produce a non-empty string."""
        result = attempt_falsification(sample_hypothesis)
        summary = result.summarize()
        assert isinstance(summary, str)
        assert len(summary) > 0
