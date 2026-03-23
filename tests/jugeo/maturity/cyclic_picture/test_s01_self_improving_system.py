from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import json

from jugeo.maturity.cyclic_picture.s01_self_improving_system import (
    ImprovementStrategy,
    MetricsTracker,
    CapabilityExpander,
    SelfImprovementRunner,
    run_improvement_cycle,
    assess_improvement,
    compute_improvement_score,
    select_best_strategy,
)
from jugeo.maturity.cyclic_picture.models import ImprovementKind, MatureSystem, MaturityLevel


# ===========================================================================
# TestImprovementStrategy
# ===========================================================================


class TestImprovementStrategy:
    """Tests for ImprovementStrategy: creation, scoring, recommendation, and serialisation."""

    @pytest.fixture
    def basic_strategy(self):
        """Return a standard ImprovementStrategy for reuse in tests."""
        return ImprovementStrategy.create("test", "test strategy")

    # ------------------------------------------------------------------
    def test_create_assigns_id(self, basic_strategy):
        """Verify that create() assigns a non-empty strategy_id.

        The strategy_id is the primary key for tracking strategy instances
        across cycles.  An empty or missing id would break any lookup by id.
        """
        assert isinstance(basic_strategy.strategy_id, str)
        assert len(basic_strategy.strategy_id) > 0

    def test_create_stores_name(self, basic_strategy):
        """Verify that the name passed to create() is stored on the instance.

        Strategy names are surfaced in reports and logs; incorrect storage
        would make diagnostic output misleading.
        """
        assert basic_strategy.name == "test"

    def test_create_stores_description(self, basic_strategy):
        """Verify that the description passed to create() is stored unchanged.

        Descriptions provide human-readable context in improvement reports;
        truncation or corruption would reduce their value.
        """
        assert basic_strategy.description == "test strategy"

    def test_create_default_target_kinds(self, basic_strategy):
        """Verify that target_kinds is a non-None list after creation.

        The strategy must expose a list of kinds so that recommend_next_kind
        and select_best_strategy can operate on it.
        """
        target_kinds = basic_strategy.target_kinds
        assert target_kinds is not None
        assert isinstance(target_kinds, list)

    def test_score_opportunity_positive_gain(self, basic_strategy):
        """Verify that score_opportunity returns a positive value when after > before.

        A positive gain means the system improved; the score must reflect that.
        score_opportunity(0.5, 0.8) should be positive because 0.8 > 0.5.
        """
        result = basic_strategy.score_opportunity(0.5, 0.8)
        assert result > 0

    def test_score_opportunity_zero(self, basic_strategy):
        """Verify that score_opportunity returns 0.0 when before == after.

        No change means no improvement; the score should be exactly 0.0.
        """
        result = basic_strategy.score_opportunity(0.5, 0.5)
        assert result == 0.0

    def test_score_opportunity_negative_gain_clamped(self, basic_strategy):
        """Verify that a regression (after < before) yields 0.0, not a negative score.

        The strategy must never reward regression.  Clamping to 0.0 ensures
        that a cycle that worsens metrics does not count as negative progress
        in aggregated statistics.
        """
        result = basic_strategy.score_opportunity(0.8, 0.5)
        assert result == 0.0

    def test_recommend_next_kind_returns_string(self, basic_strategy):
        """Verify that recommend_next_kind always returns a non-empty string.

        Callers pass the returned string to downstream logic; an empty string
        or None would trigger errors in kind-dispatch code.
        """
        result = basic_strategy.recommend_next_kind()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_recommend_next_kind_in_target_kinds(self, basic_strategy):
        """Verify that the returned kind is one of the strategy's target_kinds.

        If target_kinds is non-empty the recommendation must come from within
        that list; returning a kind outside the list would violate the strategy
        contract.
        """
        if basic_strategy.target_kinds:
            result = basic_strategy.recommend_next_kind()
            assert result in basic_strategy.target_kinds

    def test_recommend_next_kind_empty_history(self, basic_strategy):
        """Verify that recommend_next_kind works correctly with an empty history.

        An empty history (len == 0) should index into position 0 of
        target_kinds without raising any errors.
        """
        result = basic_strategy.recommend_next_kind(history=[])
        assert isinstance(result, str)

    def test_recommend_next_kind_cycles_through(self, basic_strategy):
        """Verify that successive calls with increasing history length cycle kinds.

        The round-robin index is len(history) % len(target_kinds), so calling
        with histories of length 0, 1, 2, ... should return different values
        as long as target_kinds has at least two entries.
        """
        if len(basic_strategy.target_kinds) < 2:
            pytest.skip("Need at least 2 target_kinds to test cycling")
        results = [
            basic_strategy.recommend_next_kind(history=["x"] * i)
            for i in range(len(basic_strategy.target_kinds) + 1)
        ]
        # At minimum the first and last element of a full cycle should repeat
        assert results[0] == results[-1]
        # And the first and second should differ (assuming >= 2 kinds)
        assert results[0] != results[1]

    def test_to_dict_has_all_keys(self, basic_strategy):
        """Verify that to_dict() returns a dict containing all required keys.

        Downstream serialisation and persistence code depends on these keys
        being present; missing keys would cause KeyError at runtime.
        """
        d = basic_strategy.to_dict()
        assert "strategy_id" in d
        assert "name" in d
        assert "description" in d
        assert "target_kinds" in d

    def test_to_dict_serializable(self, basic_strategy):
        """Verify that the output of to_dict() can be JSON-encoded without error.

        Integration with REST APIs, logging pipelines, and evidence stores all
        require JSON-serialisable dicts; non-serialisable objects (e.g., Enum
        members or custom classes) would break those integrations.
        """
        d = basic_strategy.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_name_matches(self, basic_strategy):
        """Verify that dict['name'] equals strategy.name after round-tripping.

        Confirms that to_dict does not transform or truncate the name field.
        """
        d = basic_strategy.to_dict()
        assert d["name"] == basic_strategy.name

    @pytest.mark.parametrize(
        "before,after,expected_positive",
        [
            (0.0, 1.0, True),
            (1.0, 2.0, True),
            (0.5, 0.5, False),
            (0.9, 0.1, False),
        ],
    )
    def test_score_opportunity_parametrized(self, basic_strategy, before, after, expected_positive):
        """Parametrised coverage of score_opportunity across sign and magnitude cases.

        Ensures the clamp-at-zero behaviour holds for both improvement and
        regression scenarios, and that equal values always yield zero.
        """
        result = basic_strategy.score_opportunity(before, after)
        if expected_positive:
            assert result > 0, f"Expected positive score for ({before}, {after}), got {result}"
        else:
            assert result == 0.0, f"Expected 0.0 for ({before}, {after}), got {result}"


# ===========================================================================
# TestMetricsTracker
# ===========================================================================


class TestMetricsTracker:
    """Tests for MetricsTracker: creation, recording, statistics, and serialisation."""

    @pytest.fixture
    def empty_tracker(self):
        """Return a fresh MetricsTracker with default window_size."""
        return MetricsTracker.create()

    # ------------------------------------------------------------------
    def test_create_default_window_size(self, empty_tracker):
        """Verify that the default window_size is 20.

        The default of 20 is documented in the class spec; deviating from it
        would silently change rolling-statistics behaviour for all callers that
        rely on the default.
        """
        assert empty_tracker.window_size == 20

    def test_create_custom_window_size(self):
        """Verify that a custom window_size is stored correctly.

        Callers tune window_size to their expected cycle count; an incorrect
        stored value would cause windowed stats to use the wrong number of
        samples.
        """
        tracker = MetricsTracker.create(window_size=10)
        assert tracker.window_size == 10

    def test_create_empty_metrics(self, empty_tracker):
        """Verify that a freshly created tracker has an empty metrics dict.

        Any pre-existing data in metrics would contaminate the first cycle's
        statistics, producing incorrect deltas and averages.
        """
        assert empty_tracker.metrics == {}

    def test_record_adds_to_metrics(self, empty_tracker):
        """Verify that record() adds the metric key to the metrics dict.

        After the first record call the key must exist so that subsequent
        queries (current, delta, trend) can find the data.
        """
        empty_tracker.record("acc", 0.9)
        assert "acc" in empty_tracker.metrics

    def test_record_multiple_values(self, empty_tracker):
        """Verify that recording three values for the same key gives a list of length 3.

        The tracker must accumulate all observations, not overwrite; windowed
        stats depend on the full history being available.
        """
        empty_tracker.record("loss", 0.3)
        empty_tracker.record("loss", 0.2)
        empty_tracker.record("loss", 0.1)
        assert len(empty_tracker.metrics["loss"]) == 3

    def test_record_appends_in_order(self, empty_tracker):
        """Verify that values are stored in insertion order.

        Trend and delta calculations rely on temporal ordering; out-of-order
        storage would produce nonsensical slope estimates.
        """
        empty_tracker.record("x", 1.0)
        empty_tracker.record("x", 2.0)
        empty_tracker.record("x", 3.0)
        assert empty_tracker.metrics["x"] == [1.0, 2.0, 3.0]

    def test_current_returns_latest(self, empty_tracker):
        """Verify that current() returns the last recorded value.

        Callers use current() to get the system's latest observed metric;
        returning anything other than the last value would misrepresent state.
        """
        empty_tracker.record("acc", 0.5)
        empty_tracker.record("acc", 0.7)
        empty_tracker.record("acc", 0.9)
        assert empty_tracker.current("acc") == pytest.approx(0.9)

    def test_current_nonexistent_key(self, empty_tracker):
        """Verify that current() returns None for an unrecorded key.

        Code that checks for None to detect missing metrics relies on this
        contract; raising KeyError instead would break such checks.
        """
        assert empty_tracker.current("nonexistent") is None

    def test_delta_with_two_values(self, empty_tracker):
        """Verify that delta() returns last - second_last when two values exist.

        The one-step delta is used to detect sudden regressions between
        consecutive cycles; an incorrect delta would mask or falsely trigger
        such alerts.
        """
        empty_tracker.record("x", 0.5)
        empty_tracker.record("x", 0.8)
        assert empty_tracker.delta("x") == pytest.approx(0.3)

    def test_delta_single_value(self, empty_tracker):
        """Verify that delta() returns 0.0 when only one value has been recorded.

        A single observation has no predecessor, so the change is undefined;
        returning 0.0 is the least surprising default for callers that may
        aggregate deltas across cycles.
        """
        empty_tracker.record("y", 0.5)
        assert empty_tracker.delta("y") == 0.0

    def test_delta_nonexistent(self, empty_tracker):
        """Verify that delta() returns None for an unknown key.

        Callers check for None to detect that a metric has never been recorded;
        returning 0.0 for an unknown key would be indistinguishable from a
        genuine zero-change observation.
        """
        assert empty_tracker.delta("unknown_key") is None

    def test_trend_increasing(self, empty_tracker):
        """Verify that trend() returns a positive value for a monotone-increasing series.

        An increasing trend should guide the runner to continue the current
        strategy; a non-positive trend would incorrectly suggest stagnation.
        """
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            empty_tracker.record("m", v)
        assert empty_tracker.trend("m") > 0

    def test_trend_decreasing(self, empty_tracker):
        """Verify that trend() returns a negative value for a monotone-decreasing series.

        A decreasing trend signals regression and should trigger a strategy
        switch; a positive or zero trend would mask the problem.
        """
        for v in [5.0, 4.0, 3.0, 2.0, 1.0]:
            empty_tracker.record("m", v)
        assert empty_tracker.trend("m") < 0

    def test_trend_flat(self, empty_tracker):
        """Verify that trend() returns 0.0 for a constant series.

        A flat trend means the metric is stable; the runner should not treat
        it as improvement or regression.
        """
        for _ in range(3):
            empty_tracker.record("flat", 0.5)
        assert empty_tracker.trend("flat") == pytest.approx(0.0)

    def test_trend_empty(self, empty_tracker):
        """Verify that trend() returns 0.0 for a key that has no recorded values.

        Callers should be able to call trend() on any key without first
        checking whether data exists; 0.0 is the neutral baseline.
        """
        assert empty_tracker.trend("no_data") == 0.0

    def test_windowed_average_basic(self, empty_tracker):
        """Verify that windowed_average() returns the arithmetic mean of recorded values.

        With values [0.4, 0.6, 0.8] and a default window of 20 (larger than
        the series), the mean should be 0.6.
        """
        empty_tracker.record("a", 0.4)
        empty_tracker.record("a", 0.6)
        empty_tracker.record("a", 0.8)
        assert empty_tracker.windowed_average("a") == pytest.approx(0.6)

    def test_windowed_average_respects_window(self):
        """Verify that windowed_average() uses only the last window_size values.

        When window_size=3 and ten values are recorded, only the last three
        should contribute to the average.  This tests the windowing contract
        that prevents stale early observations from distorting the statistic.
        """
        tracker = MetricsTracker.create(window_size=3)
        for i in range(10):
            tracker.record("v", float(i))  # 0..9; last 3 = 7, 8, 9 → mean 8.0
        assert tracker.windowed_average("v") == pytest.approx(8.0)

    def test_windowed_average_empty(self, empty_tracker):
        """Verify that windowed_average() returns 0.0 for an unrecorded key.

        Returning 0.0 rather than raising allows callers to safely aggregate
        averages before any data has been collected.
        """
        assert empty_tracker.windowed_average("nothing") == 0.0

    def test_to_dict_has_required_keys(self, empty_tracker):
        """Verify that to_dict() contains at least tracker_id and window_size.

        Downstream consumers that reconstruct tracker state from a dict need
        these fields; missing keys would cause reconstruction failures.
        """
        d = empty_tracker.to_dict()
        assert "tracker_id" in d
        assert "window_size" in d

    def test_to_dict_serializable(self, empty_tracker):
        """Verify that to_dict() output can be JSON-encoded without error.

        Tracker state is logged and stored as JSON; non-serialisable objects
        in the dict would break persistence and log-processing pipelines.
        """
        empty_tracker.record("x", 1.0)
        d = empty_tracker.to_dict()
        json.dumps(d)  # must not raise

    @pytest.mark.parametrize("window_size", [5, 10, 20, 50])
    def test_create_various_window_sizes(self, window_size):
        """Parametrised check that window_size is stored correctly for several values.

        Ensures the constructor does not hard-code or clamp the window_size
        to a fixed value, which would silently break callers that tune it.
        """
        tracker = MetricsTracker.create(window_size=window_size)
        assert tracker.window_size == window_size


# ===========================================================================
# TestCapabilityExpander
# ===========================================================================


class TestCapabilityExpander:
    """Tests for CapabilityExpander: creation, mutation, proposals, serialisation."""

    def test_create_empty(self):
        """Verify that create() with no arguments starts with an empty capability list.

        An expander with no initial capabilities represents a clean slate;
        any pre-populated list would incorrectly constrain subsequent proposals.
        """
        expander = CapabilityExpander.create()
        assert expander.known_capabilities == []

    def test_create_with_initial(self):
        """Verify that initial_capabilities are preserved after create().

        Callers pass a seed list to represent capabilities that the system
        already has; losing them would cause duplicate proposals.
        """
        caps = ["vision", "planning"]
        expander = CapabilityExpander.create(initial_capabilities=caps)
        assert expander.known_capabilities == caps

    def test_create_has_expander_id(self):
        """Verify that create() assigns a non-empty expander_id string.

        The expander_id is used for tracing which expander instance produced a
        given proposal; an empty id breaks traceability.
        """
        expander = CapabilityExpander.create()
        assert isinstance(expander.expander_id, str)
        assert len(expander.expander_id) > 0

    def test_add_capability(self):
        """Verify that add_capability() appends a new capability to the list.

        After adding a capability it must appear in known_capabilities so
        that subsequent proposal filtering excludes it correctly.
        """
        expander = CapabilityExpander.create()
        expander.add_capability("reasoning")
        assert "reasoning" in expander.known_capabilities

    def test_add_duplicate_capability(self):
        """Verify that adding the same capability twice does not create duplicates.

        Idempotency is required so that callers do not need to guard add calls
        with existence checks; duplicates would also skew length-based tests.
        """
        expander = CapabilityExpander.create()
        expander.add_capability("planning")
        expander.add_capability("planning")
        assert expander.known_capabilities.count("planning") == 1

    def test_add_multiple_capabilities(self):
        """Verify that adding several distinct capabilities accumulates them all.

        The expander must retain all additions; losing any one capability
        would allow it to be proposed again in future cycles.
        """
        expander = CapabilityExpander.create()
        for cap in ["a", "b", "c", "d"]:
            expander.add_capability(cap)
        assert len(expander.known_capabilities) == 4

    def test_remove_capability(self):
        """Verify that remove_capability() removes a present capability.

        Removal is used when a capability is deprecated or superseded;
        it must actually disappear from the list so it can be re-proposed.
        """
        expander = CapabilityExpander.create(initial_capabilities=["old_cap"])
        expander.remove_capability("old_cap")
        assert "old_cap" not in expander.known_capabilities

    def test_remove_nonexistent(self):
        """Verify that remove_capability() is a no-op for absent capabilities.

        Callers should not need to guard remove calls; raising on a missing
        capability would force defensive coding throughout the codebase.
        """
        expander = CapabilityExpander.create()
        expander.remove_capability("does_not_exist")  # must not raise

    def test_propose_expansions_returns_list(self):
        """Verify that propose_expansions() always returns a list.

        Callers iterate over the result; returning None or a non-list would
        raise TypeError at the iteration site.
        """
        expander = CapabilityExpander.create()
        result = expander.propose_expansions()
        assert isinstance(result, list)

    def test_propose_expansions_excludes_known(self):
        """Verify that propose_expansions() does not include already-known capabilities.

        Proposing a capability the system already has wastes cycles and
        produces misleading improvement reports.
        """
        expander = CapabilityExpander.create(initial_capabilities=["streaming_inference"])
        proposals = expander.propose_expansions()
        assert "streaming_inference" not in proposals

    def test_propose_expansions_from_pool(self):
        """Verify that a given pool is filtered to exclude known capabilities.

        When pool=["a","b","c"] and "a" is known, only ["b","c"] should be
        proposed.  This is the standard use case for targeted expansion.
        """
        expander = CapabilityExpander.create(initial_capabilities=["a"])
        proposals = expander.propose_expansions(candidate_pool=["a", "b", "c"])
        assert "a" not in proposals
        assert "b" in proposals
        assert "c" in proposals

    def test_propose_expansions_all_known(self):
        """Verify that propose_expansions() returns an empty list when all pool items are known.

        If the system already has every capability in the pool, no upward
        move is possible in that sub-lattice; the result must be empty.
        """
        expander = CapabilityExpander.create(initial_capabilities=["x", "y"])
        proposals = expander.propose_expansions(candidate_pool=["x", "y"])
        assert proposals == []

    def test_to_dict_has_expander_id(self):
        """Verify that to_dict() includes the expander_id key.

        Serialised expanders are stored in cycle reports; missing the id
        would prevent correlating a report entry back to its expander.
        """
        expander = CapabilityExpander.create()
        d = expander.to_dict()
        assert "expander_id" in d

    def test_to_dict_has_known_capabilities(self):
        """Verify that to_dict() includes the known_capabilities key.

        Downstream consumers reconstruct the capability set from this key;
        its absence would require a full re-scan to determine what is known.
        """
        expander = CapabilityExpander.create(initial_capabilities=["p", "q"])
        d = expander.to_dict()
        assert "known_capabilities" in d
        assert d["known_capabilities"] == ["p", "q"]

    def test_to_dict_serializable(self):
        """Verify that to_dict() output is JSON-serialisable.

        Expander state is stored alongside cycle reports in JSON format;
        non-serialisable objects would break the storage layer.
        """
        expander = CapabilityExpander.create(initial_capabilities=["cap1"])
        d = expander.to_dict()
        json.dumps(d)  # must not raise


# ===========================================================================
# TestSelfImprovementRunner
# ===========================================================================


class TestSelfImprovementRunner:
    """Tests for SelfImprovementRunner: creation, cycle execution, reporting, serialisation."""

    def test_create(self):
        """Verify that create() returns a runner with all required sub-components.

        The runner must have a strategy, tracker, expander, and runner_id
        immediately after construction; missing any component would cause
        AttributeError on first use.
        """
        runner = SelfImprovementRunner.create()
        assert runner.strategy is not None
        assert runner.tracker is not None
        assert runner.expander is not None
        assert runner.runner_id is not None

    def test_create_has_empty_cycle_history(self):
        """Verify that a new runner starts with an empty cycle_history.

        Any pre-existing history would contaminate total_cycles and avg_gain
        computations from the very first report.
        """
        runner = SelfImprovementRunner.create()
        assert runner.cycle_history == []

    def test_run_one_cycle_returns_dict(self):
        """Verify that run_one_cycle() returns a dictionary.

        Callers inspect the returned dict for cycle_id and gain; returning
        None or a non-dict would raise TypeError at the key-access site.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_one_cycle({"acc": 0.5}, {"acc": 0.8})
        assert isinstance(result, dict)

    def test_run_one_cycle_has_cycle_id(self):
        """Verify that the result dict contains a 'cycle_id' key.

        cycle_id enables callers to deduplicate or trace individual cycles;
        its absence would make cycle logs impossible to index.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_one_cycle({"acc": 0.5}, {"acc": 0.8})
        assert "cycle_id" in result

    def test_run_one_cycle_has_gain(self):
        """Verify that the result dict contains a 'gain' key.

        The gain value drives downstream decisions about strategy continuation
        or switching; its absence would break those decision branches.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_one_cycle({"acc": 0.5}, {"acc": 0.8})
        assert "gain" in result

    def test_run_one_cycle_gain_positive_when_improved(self):
        """Verify that gain > 0 when after metrics are better than before.

        An improvement from acc=0.5 to acc=0.9 is unambiguously positive;
        a zero or negative gain would misclassify the cycle as stagnation.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_one_cycle({"acc": 0.5}, {"acc": 0.9})
        assert result["gain"] > 0

    def test_run_one_cycle_appends_to_history(self):
        """Verify that running one cycle grows cycle_history by exactly one entry.

        The history is the persistent audit trail of all cycles; missing an
        entry would cause total_cycles in the report to be incorrect.
        """
        runner = SelfImprovementRunner.create()
        assert len(runner.cycle_history) == 0
        runner.run_one_cycle({"x": 0.4}, {"x": 0.7})
        assert len(runner.cycle_history) == 1

    def test_run_n_cycles_returns_list(self):
        """Verify that run_n_cycles() returns a list.

        Callers iterate over the returned list; returning a non-list would
        raise TypeError on the first loop iteration.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_n_cycles(3)
        assert isinstance(result, list)

    @pytest.mark.parametrize("n", [1, 3, 5])
    def test_run_n_cycles_correct_length(self, n):
        """Verify that run_n_cycles(n) returns exactly n results.

        The length of the returned list must equal n so that callers can
        confirm that the requested number of cycles actually ran.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_n_cycles(n)
        assert len(result) == n

    def test_run_n_cycles_zero(self):
        """Verify that run_n_cycles(0) returns an empty list without running anything.

        A zero-cycle run is a valid no-op; raising or returning a non-empty
        list would confuse callers that check len(results) > 0.
        """
        runner = SelfImprovementRunner.create()
        result = runner.run_n_cycles(0)
        assert result == []

    def test_run_n_cycles_with_metric_fn(self):
        """Verify that a custom metric_fn is used when provided to run_n_cycles.

        The metric_fn allows callers to inject real metric measurements;
        ignoring it and using dummy dicts would defeat the purpose of the hook.
        """
        runner = SelfImprovementRunner.create()
        calls = []

        def metric_fn():
            calls.append(1)
            return {"custom": 0.3}, {"custom": 0.9}

        runner.run_n_cycles(3, metric_fn=metric_fn)
        assert len(calls) == 3

    def test_generate_report_returns_dict(self):
        """Verify that generate_report() returns a dictionary.

        Reports are consumed by downstream monitoring and evidence layers;
        returning a non-dict would raise AttributeError on key access.
        """
        runner = SelfImprovementRunner.create()
        report = runner.generate_report()
        assert isinstance(report, dict)

    def test_generate_report_has_runner_id(self):
        """Verify that the report contains the runner's runner_id.

        The runner_id is the link between a report and its producing runner;
        its absence would break cross-referencing in audit logs.
        """
        runner = SelfImprovementRunner.create()
        report = runner.generate_report()
        assert report["runner_id"] == runner.runner_id

    def test_generate_report_has_total_cycles(self):
        """Verify that total_cycles in the report equals the number of cycles run.

        total_cycles is used by the maturity advancement logic to confirm
        that sufficient evidence has been gathered; an incorrect count could
        prematurely trigger or block advancement.
        """
        runner = SelfImprovementRunner.create()
        runner.run_n_cycles(4)
        report = runner.generate_report()
        assert report["total_cycles"] == 4

    def test_to_dict_has_runner_id(self):
        """Verify that to_dict() includes the runner_id key.

        The runner_id is the primary identifier of the runner in serialised
        form; its absence would prevent reconstruction from stored state.
        """
        runner = SelfImprovementRunner.create()
        d = runner.to_dict()
        assert "runner_id" in d


# ===========================================================================
# TestFreeFunctions
# ===========================================================================


class TestFreeFunctions:
    """Tests for the module-level free functions."""

    def test_run_improvement_cycle_returns_dict(self):
        """Verify that run_improvement_cycle() returns a dictionary.

        The function is a convenience wrapper; returning a non-dict would
        break any caller that accesses cycle_id or gain by key.
        """
        result = run_improvement_cycle()
        assert isinstance(result, dict)

    def test_run_improvement_cycle_has_cycle_id(self):
        """Verify that the returned dict contains a 'cycle_id' key.

        cycle_id is needed for idempotency checks in integration pipelines;
        its absence would force callers to generate their own ids.
        """
        result = run_improvement_cycle()
        assert "cycle_id" in result

    def test_run_improvement_cycle_has_gain(self):
        """Verify that the returned dict contains a 'gain' key.

        gain drives improvement-advancement decisions; its absence means the
        caller has no signal to act on.
        """
        result = run_improvement_cycle()
        assert "gain" in result

    def test_run_improvement_cycle_with_system(self):
        """Verify that run_improvement_cycle() works when passed a MatureSystem.

        The function accepts an optional system argument for API compatibility;
        it must not raise when a real MatureSystem instance is provided.
        """
        system = MatureSystem.create("test-sys")
        result = run_improvement_cycle(system=system)
        assert isinstance(result, dict)

    def test_assess_improvement_positive_gains(self):
        """Verify that assess_improvement() yields positive deltas when after > before.

        A genuine improvement is indicated by a positive delta per metric;
        zero or negative deltas would misclassify the cycle.
        """
        result = assess_improvement({"acc": 0.5}, {"acc": 0.9})
        assert result["acc"] > 0

    def test_assess_improvement_all_keys(self):
        """Verify that all before keys appear in the result, even if not in after.

        Every key in before must be covered; missing keys would silently omit
        metrics from the improvement assessment.
        """
        before = {"acc": 0.5, "loss": 0.3, "f1": 0.7}
        after = {"acc": 0.8}
        result = assess_improvement(before, after)
        for k in before:
            assert k in result

    def test_assess_improvement_zero_gain(self):
        """Verify that equal before and after values yield a delta of 0.0.

        No change should produce exactly zero; a non-zero delta would
        fabricate improvement or regression that did not occur.
        """
        result = assess_improvement({"x": 0.5}, {"x": 0.5})
        assert result["x"] == pytest.approx(0.0)

    def test_assess_improvement_negative_gain(self):
        """Verify that assess_improvement() yields a negative delta when after < before.

        Regression must be represented as a negative value so that callers
        can detect and respond to it appropriately.
        """
        result = assess_improvement({"acc": 0.9}, {"acc": 0.6})
        assert result["acc"] < 0

    def test_assess_improvement_empty_dicts(self):
        """Verify that assess_improvement() returns {} when both dicts are empty.

        An empty assessment for an empty cycle is well-defined; returning
        anything else would confuse length-based checks.
        """
        result = assess_improvement({}, {})
        assert result == {}

    def test_compute_improvement_score_range(self):
        """Verify that compute_improvement_score() returns a float.

        The score is used in arithmetic comparisons; returning a non-float
        type would raise TypeError when compared to threshold values.
        """
        score = compute_improvement_score({"a": 0.5}, {"a": 0.9})
        assert isinstance(score, float)

    def test_compute_improvement_score_positive(self):
        """Verify that the score is positive when all shared metrics improve.

        A positive score is required to trigger advancement decisions;
        a zero or negative score would block legitimate progress.
        """
        score = compute_improvement_score({"acc": 0.5, "f1": 0.6}, {"acc": 0.8, "f1": 0.9})
        assert score > 0

    def test_compute_improvement_score_zero(self):
        """Verify that the score is 0.0 when no metric changes.

        Stability (no change) should score as 0.0, not as improvement;
        a positive score for a flat cycle would incorrectly advance the system.
        """
        score = compute_improvement_score({"acc": 0.7}, {"acc": 0.7})
        assert score == pytest.approx(0.0)

    def test_compute_improvement_score_negative(self):
        """Verify that the score is negative when all metrics regress.

        Regression must produce a negative score so that the runner can
        detect and penalise deteriorating cycles.
        """
        score = compute_improvement_score({"acc": 0.9}, {"acc": 0.5})
        assert score < 0

    def test_compute_improvement_score_empty_dicts(self):
        """Verify that compute_improvement_score() returns 0.0 for empty dicts.

        With no shared keys the mean is undefined; 0.0 is the conventional
        neutral default and prevents division-by-zero errors.
        """
        score = compute_improvement_score({}, {})
        assert score == pytest.approx(0.0)

    def test_select_best_strategy_returns_strategy(self):
        """Verify that select_best_strategy() returns an ImprovementStrategy instance.

        Callers use the returned object to drive the next cycle; returning a
        non-strategy object would raise AttributeError on attribute access.
        """
        strategies = [ImprovementStrategy.create("s1", "desc1")]
        result = select_best_strategy(strategies)
        assert isinstance(result, ImprovementStrategy)

    def test_select_best_strategy_picks_most_kinds(self):
        """Verify that the strategy with more target_kinds is chosen.

        The selection criterion is the length of target_kinds; a strategy
        that covers more improvement dimensions should be preferred as it
        provides the broadest improvement coverage.
        """
        s_broad = ImprovementStrategy.create("broad", "covers all kinds")
        s_narrow = ImprovementStrategy(
            strategy_id="narrow",
            name="narrow",
            description="only one kind",
            target_kinds=["capability"],
        )
        result = select_best_strategy([s_narrow, s_broad])
        assert result is s_broad

    def test_select_best_strategy_single_strategy(self):
        """Verify that select_best_strategy() works correctly with a single-element list.

        A list of one strategy must return that strategy; raising or returning
        a different object would break single-strategy pipelines.
        """
        s = ImprovementStrategy.create("only", "the only strategy")
        result = select_best_strategy([s])
        assert result is s

    def test_select_best_strategy_ties(self):
        """Verify that on a tie, select_best_strategy() returns the first strategy.

        Deterministic tie-breaking ensures that repeated calls with the same
        list always choose the same strategy, preventing non-deterministic
        pipeline behaviour.
        """
        s1 = ImprovementStrategy(
            strategy_id="s1", name="first", description="d", target_kinds=["a", "b"]
        )
        s2 = ImprovementStrategy(
            strategy_id="s2", name="second", description="d", target_kinds=["c", "d"]
        )
        result = select_best_strategy([s1, s2])
        assert result is s1

    @pytest.mark.parametrize("kind", list(ImprovementKind))
    def test_run_improvement_cycle_all_kinds(self, kind):
        """Verify that run_improvement_cycle() accepts every ImprovementKind value.

        Each kind.value is a valid string that callers may pass; failing for
        any one kind would leave a gap in the improvement coverage.
        """
        result = run_improvement_cycle(kind=kind.value)
        assert isinstance(result, dict)
        assert result["kind"] == kind.value

    def test_full_improvement_workflow(self):
        """Integration test: create a runner, run 5 cycles, verify the report.

        This test exercises the complete S01 workflow end-to-end: runner
        creation, multi-cycle execution, and report generation.  It confirms
        that the components are correctly wired together and that the report
        accurately reflects the number of cycles run.  total_cycles must equal
        5 to confirm that no cycles were silently dropped or duplicated.
        """
        runner = SelfImprovementRunner.create()
        runner.run_n_cycles(5)
        report = runner.generate_report()
        assert isinstance(report, dict)
        assert report["total_cycles"] == 5
        assert "avg_gain" in report
        assert "cycle_history" in report
        assert len(report["cycle_history"]) == 5
        # Sanity-check that every cycle entry has a cycle_id and gain
        for entry in report["cycle_history"]:
            assert "cycle_id" in entry
            assert "gain" in entry

    def test_assess_improvement_missing_after_key_uses_zero(self):
        """Verify that assess_improvement() treats missing after-keys as 0.0.

        When a metric disappears after the cycle (e.g., feature removed), the
        delta should be 0.0 - before_val, reflecting a complete loss of that
        metric value rather than being silently omitted.
        """
        result = assess_improvement({"acc": 0.8, "extra": 0.5}, {"acc": 0.9})
        assert "extra" in result
        assert result["extra"] == pytest.approx(0.0 - 0.5)

    def test_compute_improvement_score_non_overlapping_keys(self):
        """Verify that compute_improvement_score() returns 0.0 when no keys overlap.

        With no shared keys there are no terms to average; 0.0 is the correct
        neutral default, not an error.
        """
        score = compute_improvement_score({"a": 1.0}, {"b": 2.0})
        assert score == pytest.approx(0.0)

    def test_run_improvement_cycle_gain_is_float(self):
        """Verify that the 'gain' value in run_improvement_cycle output is a float.

        Downstream arithmetic on gain assumes a numeric type; a string or None
        gain would raise TypeError in comparisons and aggregations.
        """
        result = run_improvement_cycle()
        assert isinstance(result["gain"], float)

    def test_improvement_strategy_to_dict_target_kinds_is_list(self):
        """Verify that target_kinds in to_dict() is a JSON array (list).

        Deserialising consumers expect a list; a dict or string serialisation
        would break iteration over the returned target_kinds.
        """
        s = ImprovementStrategy.create("s", "d")
        d = s.to_dict()
        assert isinstance(d["target_kinds"], list)

    def test_metrics_tracker_records_float_coercion(self):
        """Verify that MetricsTracker.record() coerces int inputs to float.

        Metric sources often return integers; the tracker must store them as
        floats to keep the internal list homogeneous and avoid type errors in
        arithmetic operations like delta and trend.
        """
        tracker = MetricsTracker.create()
        tracker.record("count", 5)
        assert isinstance(tracker.metrics["count"][0], float)

    def test_capability_expander_create_copies_initial_list(self):
        """Verify that CapabilityExpander.create() copies the initial_capabilities list.

        If create() stored a reference to the caller's list, mutations to that
        list after construction would silently corrupt the expander's state.
        """
        original = ["alpha", "beta"]
        expander = CapabilityExpander.create(initial_capabilities=original)
        original.append("gamma")
        assert "gamma" not in expander.known_capabilities

    def test_self_improvement_runner_to_dict_has_strategy(self):
        """Verify that runner.to_dict() contains a 'strategy' key.

        The strategy dict is used to reconstruct or log the runner's
        configuration; its absence would make the serialised form incomplete.
        """
        runner = SelfImprovementRunner.create()
        d = runner.to_dict()
        assert "strategy" in d
        assert isinstance(d["strategy"], dict)

    def test_self_improvement_runner_to_dict_serializable(self):
        """Verify that runner.to_dict() output is fully JSON-serialisable.

        Runner state is stored in evidence records and pipeline logs; any
        non-serialisable nested object would silently corrupt those records.
        """
        runner = SelfImprovementRunner.create()
        runner.run_n_cycles(2)
        d = runner.to_dict()
        json.dumps(d)  # must not raise
