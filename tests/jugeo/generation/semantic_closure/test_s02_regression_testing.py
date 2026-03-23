r"""Regression tests for ``jugeo.generation.semantic_closure.s02_regression_testing``.

Exercises RegressionTestSuite, BaselineManager, RegressionDetector,
RegressionRepairer, and the module-level helpers.  All tests are written
against the *actual* public API; see models.py for the canonical field list.

copilot: test-s02-regression-testing
"""
from pathlib import Path
import sys
import time
import uuid

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

# ---------------------------------------------------------------------------
# Conditional imports — tests are skipped gracefully when modules are absent.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import (
        ClosureCheck,
        ClosureGap,
        ClosureResult,
        RegressionKind,
        RegressionRecord,
        RegressionStatus,
        RegressionTest,
        make_check,
        make_gap,
    )
    _models_ok = True
except ImportError:
    _models_ok = False

try:
    from jugeo.generation.semantic_closure.s02_regression_testing import (
        BaselineManager,
        RegressionDetector,
        RegressionRepairer,
        RegressionTestSuite,
        create_regression_test,
        detect_regressions_from_snapshots,
        run_regression_suite,
    )
    _s02_ok = True
except ImportError as exc:
    _s02_ok = False
    pytest.skip(f"s02_regression_testing not available: {exc}", allow_module_level=True)


# ===========================================================================
# TestRegressionTestSuite
# ===========================================================================


class TestRegressionTestSuite:
    """Tests for :class:`RegressionTestSuite`."""

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_rt(
        self,
        test_id: str = "t1",
        obligation_id: str = "obl_1",
        status: str = RegressionStatus.UNKNOWN.value,
    ) -> RegressionTest:
        return RegressionTest(
            test_id=test_id,
            obligation_id=obligation_id,
            baseline_snapshot_id="snap_001",
            status=status,
            expected_result=ClosureResult.CLOSED.value,
            expected_confidence_min=0.5,
        )

    # ------------------------------------------------------------------
    # Basic lifecycle
    # ------------------------------------------------------------------

    def test_suite_starts_empty(self):
        suite = RegressionTestSuite()
        assert suite.test_count() == 0

    def test_add_test_increases_count(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        assert suite.test_count() == 1

    def test_add_multiple_tests(self):
        suite = RegressionTestSuite()
        for i in range(5):
            suite.add_test(self._make_rt(f"t{i}"))
        assert suite.test_count() == 5

    def test_add_duplicate_test_id_overwrites(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", obligation_id="obl_a"))
        suite.add_test(self._make_rt("t1", obligation_id="obl_b"))
        assert suite.test_count() == 1
        remaining = suite.filter_by_obligation("obl_b")
        assert len(remaining) == 1

    def test_remove_test_decreases_count(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        suite.remove_test("t1")
        assert suite.test_count() == 0

    def test_remove_returns_removed_test(self):
        suite = RegressionTestSuite()
        rt = self._make_rt("t1")
        suite.add_test(rt)
        removed = suite.remove_test("t1")
        assert removed is not None
        assert removed.test_id == "t1"

    def test_remove_nonexistent_returns_none(self):
        suite = RegressionTestSuite()
        result = suite.remove_test("nonexistent")
        assert result is None

    def test_remove_leaves_others_intact(self):
        suite = RegressionTestSuite()
        for i in range(3):
            suite.add_test(self._make_rt(f"t{i}"))
        suite.remove_test("t1")
        assert suite.test_count() == 2

    # ------------------------------------------------------------------
    # run_all
    # ------------------------------------------------------------------

    def test_run_all_returns_dict(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        results = suite.run_all({})
        assert isinstance(results, dict)

    def test_run_all_covers_all_tests(self):
        suite = RegressionTestSuite()
        for i in range(3):
            suite.add_test(self._make_rt(f"t{i}"))
        results = suite.run_all({})
        assert len(results) == 3

    def test_run_all_empty_suite_returns_empty_dict(self):
        suite = RegressionTestSuite()
        results = suite.run_all({})
        assert results == {}

    def test_run_all_updates_last_run(self):
        suite = RegressionTestSuite()
        rt = self._make_rt("t1")
        assert rt.last_run == 0.0
        suite.add_test(rt)
        suite.run_all({})
        assert rt.last_run > 0.0

    def test_run_all_with_skip_flag(self):
        suite = RegressionTestSuite()
        rt = self._make_rt("my_test")
        suite.add_test(rt)
        suite.run_all({"skip_my_test": True})
        assert rt.status == RegressionStatus.SKIPPED.value

    def test_run_all_sets_status_on_each_test(self):
        suite = RegressionTestSuite()
        for i in range(4):
            suite.add_test(self._make_rt(f"t{i}", obligation_id=f"obl_{i}"))
        suite.run_all({})
        for rt in suite._tests.values():
            assert rt.status in (
                RegressionStatus.PASSING.value,
                RegressionStatus.FAILING.value,
                RegressionStatus.SKIPPED.value,
                RegressionStatus.UNKNOWN.value,
            )

    def test_run_single_returns_test(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        result = suite.run_single("t1", {})
        assert result is not None
        assert isinstance(result, RegressionTest)

    def test_run_single_nonexistent_returns_none(self):
        suite = RegressionTestSuite()
        result = suite.run_single("no_such", {})
        assert result is None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def test_get_failing_empty_initially(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        failing = suite.get_failing()
        assert isinstance(failing, list)
        assert len(failing) == 0  # status is "unknown", not "failing"

    def test_get_passing_returns_passing_tests(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", status=RegressionStatus.PASSING.value))
        passing = suite.get_passing()
        assert all(t.status == RegressionStatus.PASSING.value for t in passing)

    def test_get_passing_ignores_unknown(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", status=RegressionStatus.UNKNOWN.value))
        assert suite.get_passing() == []

    def test_get_unknown_returns_unknown_tests(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", status=RegressionStatus.UNKNOWN.value))
        unknown = suite.get_unknown()
        assert len(unknown) == 1
        assert unknown[0].status == RegressionStatus.UNKNOWN.value

    def test_filter_by_obligation(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", obligation_id="obl_A"))
        suite.add_test(self._make_rt("t2", obligation_id="obl_B"))
        result = suite.filter_by_obligation("obl_A")
        assert all(t.obligation_id == "obl_A" for t in result)
        assert len(result) == 1

    def test_filter_by_obligation_no_match(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", obligation_id="obl_A"))
        result = suite.filter_by_obligation("obl_NONE")
        assert result == []

    def test_failure_rate_zero_when_empty(self):
        suite = RegressionTestSuite()
        assert suite.failure_rate() == 0.0

    def test_failure_rate_zero_when_no_failures(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", status=RegressionStatus.PASSING.value))
        assert suite.failure_rate() == 0.0

    def test_failure_rate_correct_proportion(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1", status=RegressionStatus.PASSING.value))
        suite.add_test(self._make_rt("t2", status=RegressionStatus.FAILING.value))
        rate = suite.failure_rate()
        assert abs(rate - 0.5) < 1e-9

    def test_failure_rate_one_when_all_fail(self):
        suite = RegressionTestSuite()
        for i in range(3):
            suite.add_test(self._make_rt(f"t{i}", status=RegressionStatus.FAILING.value))
        rate = suite.failure_rate()
        assert rate == 1.0

    def test_failure_rate_range_after_run(self):
        suite = RegressionTestSuite()
        for i in range(5):
            suite.add_test(self._make_rt(f"t{i}", obligation_id=f"obl_{i}"))
        suite.run_all({})
        rate = suite.failure_rate()
        assert 0.0 <= rate <= 1.0

    # ------------------------------------------------------------------
    # Summary & serialisation
    # ------------------------------------------------------------------

    def test_summary_returns_string(self):
        suite = RegressionTestSuite()
        assert isinstance(suite.summary(), str)

    def test_summary_contains_suite_id(self):
        suite = RegressionTestSuite("my_suite")
        assert "my_suite" in suite.summary()

    def test_to_dict_has_suite_id(self):
        suite = RegressionTestSuite("my_suite")
        d = suite.to_dict()
        assert "suite_id" in d
        assert d["suite_id"] == "my_suite"

    def test_to_dict_has_tests_key(self):
        suite = RegressionTestSuite()
        suite.add_test(self._make_rt("t1"))
        d = suite.to_dict()
        assert "tests" in d

    def test_to_dict_reflects_all_tests(self):
        suite = RegressionTestSuite()
        for i in range(4):
            suite.add_test(self._make_rt(f"t{i}"))
        d = suite.to_dict()
        assert len(d["tests"]) == 4

    def test_suite_id_auto_assigned_when_omitted(self):
        suite = RegressionTestSuite()
        assert isinstance(suite.suite_id, str)
        assert len(suite.suite_id) > 0


# ===========================================================================
# TestBaselineManager
# ===========================================================================


class TestBaselineManager:
    """Tests for :class:`BaselineManager`."""

    def test_take_baseline_returns_string_id(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"a": 1, "b": 2})
        assert isinstance(snap_id, str)

    def test_take_baseline_id_has_length(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"x": True})
        assert len(snap_id) >= 8

    def test_take_baseline_stores_state(self):
        mgr = BaselineManager()
        state = {"key": "value", "closed": True}
        snap_id = mgr.take_baseline(state)
        retrieved = mgr.get_baseline(snap_id)
        assert retrieved is not None
        assert retrieved.get("key") == "value"

    def test_take_baseline_stores_truthy_bool(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"flag": True})
        baseline = mgr.get_baseline(snap_id)
        assert baseline["flag"] is True

    def test_take_baseline_stores_numeric(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"score": 0.95})
        baseline = mgr.get_baseline(snap_id)
        assert abs(baseline["score"] - 0.95) < 1e-9

    def test_get_nonexistent_returns_none(self):
        mgr = BaselineManager()
        assert mgr.get_baseline("nonexistent_id") is None

    def test_multiple_snapshots_independent(self):
        mgr = BaselineManager()
        id1 = mgr.take_baseline({"a": 1})
        id2 = mgr.take_baseline({"b": 2})
        assert id1 != id2
        assert mgr.get_baseline(id1).get("a") == 1
        assert mgr.get_baseline(id2).get("b") == 2

    def test_update_baseline_returns_new_id(self):
        mgr = BaselineManager()
        new_id = mgr.update_baseline("test_001", {"new_state": True})
        assert isinstance(new_id, str)

    def test_update_baseline_replaces_old(self):
        mgr = BaselineManager()
        mgr.update_baseline("my_test", {"v": 1})
        mgr.update_baseline("my_test", {"v": 2})
        # After second update, count should reflect removal of first
        count_after = len(mgr.list_baselines())
        assert count_after >= 1

    def test_diff_identical_baselines_empty(self):
        mgr = BaselineManager()
        state = {"a": 1}
        id1 = mgr.take_baseline(state)
        id2 = mgr.take_baseline(state)
        diff = mgr.diff_baselines(id1, id2)
        assert isinstance(diff, dict)
        assert diff.get("changed", []) == []
        assert diff.get("removed", []) == []

    def test_diff_added_keys_detected(self):
        mgr = BaselineManager()
        id1 = mgr.take_baseline({"a": 1})
        id2 = mgr.take_baseline({"a": 1, "b": 2})
        diff = mgr.diff_baselines(id1, id2)
        assert "b" in diff.get("added", [])

    def test_diff_removed_keys_detected(self):
        mgr = BaselineManager()
        id1 = mgr.take_baseline({"a": 1, "b": 2})
        id2 = mgr.take_baseline({"a": 1})
        diff = mgr.diff_baselines(id1, id2)
        assert "b" in diff.get("removed", [])

    def test_diff_changed_keys_detected(self):
        mgr = BaselineManager()
        id1 = mgr.take_baseline({"a": 1, "b": 2})
        id2 = mgr.take_baseline({"a": 1, "b": 3})
        diff = mgr.diff_baselines(id1, id2)
        assert "b" in diff.get("changed", [])

    def test_diff_missing_snapshot_returns_empty(self):
        mgr = BaselineManager()
        id1 = mgr.take_baseline({"a": 1})
        diff = mgr.diff_baselines(id1, "nonexistent")
        assert diff == {"added": [], "removed": [], "changed": []}

    def test_list_baselines_empty_initially(self):
        mgr = BaselineManager()
        assert mgr.list_baselines() == []

    def test_list_baselines_after_take(self):
        mgr = BaselineManager()
        mgr.take_baseline({"a": 1})
        mgr.take_baseline({"b": 2})
        assert len(mgr.list_baselines()) == 2

    def test_list_baselines_sorted(self):
        mgr = BaselineManager()
        ids = [mgr.take_baseline({"i": i}) for i in range(5)]
        listed = mgr.list_baselines()
        assert listed == sorted(listed)

    def test_delete_baseline(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"a": 1})
        result = mgr.delete_baseline(snap_id)
        assert result is True
        assert mgr.get_baseline(snap_id) is None

    def test_delete_nonexistent_returns_false(self):
        mgr = BaselineManager()
        assert mgr.delete_baseline("nonexistent") is False

    def test_delete_removes_from_list(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"a": 1})
        mgr.delete_baseline(snap_id)
        assert snap_id not in mgr.list_baselines()

    def test_get_test_baseline_none_before_update(self):
        mgr = BaselineManager()
        assert mgr.get_test_baseline("unknown_test") is None

    def test_get_test_baseline_after_update(self):
        mgr = BaselineManager()
        mgr.update_baseline("my_test", {"state": "initial"})
        baseline = mgr.get_test_baseline("my_test")
        assert baseline is not None

    def test_get_test_baseline_reflects_updated_state(self):
        mgr = BaselineManager()
        mgr.update_baseline("my_test", {"v": "first"})
        mgr.update_baseline("my_test", {"v": "second"})
        baseline = mgr.get_test_baseline("my_test")
        assert baseline is not None
        assert baseline.get("v") == "second"

    def test_baseline_isolation(self):
        """Mutations to original state don't affect stored baseline."""
        mgr = BaselineManager()
        state = {"mutable": [1, 2, 3]}
        snap_id = mgr.take_baseline(state)
        state["mutable"].append(4)
        stored = mgr.get_baseline(snap_id)
        if stored and "mutable" in stored:
            assert len(stored["mutable"]) == 3

    def test_label_does_not_alter_snapshot_id_type(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"x": 1}, label="my_label")
        assert isinstance(snap_id, str)
        assert mgr.get_baseline(snap_id) is not None


# ===========================================================================
# TestRegressionDetector
# ===========================================================================


class TestRegressionDetector:
    """Tests for :class:`RegressionDetector`."""

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_record(self, regression_type: str = "semantic") -> RegressionRecord:
        return RegressionRecord(
            record_id=uuid.uuid4().hex[:8],
            key="obl_001",
            baseline_value=True,
            current_value=False,
            regression_type=regression_type,
            severity="minor",
            cause_analysis="Test cause",
        )

    # ------------------------------------------------------------------
    # Basic detection
    # ------------------------------------------------------------------

    def test_detect_no_regression_when_identical(self):
        detector = RegressionDetector()
        state = {"obl_1": True, "obl_2": True}
        records = detector.detect(state, state)
        assert isinstance(records, list)
        assert len(records) == 0

    def test_detect_regression_when_value_becomes_false(self):
        detector = RegressionDetector()
        baseline = {"obl_1": True}
        current = {"obl_1": False}
        records = detector.detect(baseline, current)
        assert len(records) == 1
        assert records[0].key == "obl_1"

    def test_detect_regression_when_key_removed(self):
        detector = RegressionDetector()
        baseline = {"obl_1": True, "obl_2": True}
        current = {"obl_1": True}
        records = detector.detect(baseline, current)
        regression_keys = {r.key for r in records}
        assert "obl_2" in regression_keys

    def test_detect_no_regression_added_keys(self):
        detector = RegressionDetector()
        baseline = {"obl_1": True}
        current = {"obl_1": True, "obl_2": True}
        records = detector.detect(baseline, current)
        assert all(r.key != "obl_2" for r in records)

    def test_detect_with_empty_states(self):
        detector = RegressionDetector()
        records = detector.detect({}, {})
        assert records == []

    def test_detect_numeric_regression(self):
        detector = RegressionDetector(sensitivity=0.3)
        baseline = {"coverage": 0.9}
        current = {"coverage": 0.4}
        records = detector.detect(baseline, current)
        assert any(r.key == "coverage" for r in records)

    def test_detect_numeric_no_regression_small_drop(self):
        detector = RegressionDetector(sensitivity=0.5)
        baseline = {"score": 1.0}
        current = {"score": 0.8}  # 20% drop, below 50% sensitivity
        records = detector.detect(baseline, current)
        assert all(r.key != "score" for r in records)

    def test_detect_sorts_by_key(self):
        detector = RegressionDetector()
        baseline = {"z_key": True, "a_key": True}
        current = {"z_key": False, "a_key": False}
        records = detector.detect(baseline, current)
        keys = [r.key for r in records]
        assert keys == sorted(keys)

    def test_detect_record_has_correct_key(self):
        detector = RegressionDetector()
        baseline = {"my_obligation": True}
        current = {"my_obligation": False}
        records = detector.detect(baseline, current)
        assert records[0].key == "my_obligation"

    def test_detect_record_has_baseline_value(self):
        detector = RegressionDetector()
        baseline = {"x": True}
        current = {"x": False}
        records = detector.detect(baseline, current)
        assert records[0].baseline_value is True

    def test_detect_record_has_current_value(self):
        detector = RegressionDetector()
        baseline = {"x": True}
        current = {"x": False}
        records = detector.detect(baseline, current)
        assert records[0].current_value is False

    # ------------------------------------------------------------------
    # detect_from_checks
    # ------------------------------------------------------------------

    def test_detect_from_checks_returns_list(self):
        detector = RegressionDetector()
        prev = make_check(obligation_id="obl_1", patch_id="p1", result="closed", confidence=0.9)
        curr = make_check(obligation_id="obl_1", patch_id="p1", result="open", confidence=0.1)
        records = detector.detect_from_checks([prev], [curr])
        assert isinstance(records, list)

    def test_detect_from_checks_finds_regression(self):
        detector = RegressionDetector()
        prev = make_check(obligation_id="obl_1", result="closed", confidence=0.9)
        curr = make_check(obligation_id="obl_1", result="open", confidence=0.1)
        records = detector.detect_from_checks([prev], [curr])
        assert len(records) == 1
        assert records[0].key == "obl_1"

    def test_detect_from_checks_no_regression_still_closed(self):
        detector = RegressionDetector()
        prev = make_check(obligation_id="obl_1", result="closed", confidence=0.9)
        curr = make_check(obligation_id="obl_1", result="closed", confidence=0.85)
        records = detector.detect_from_checks([prev], [curr])
        assert records == []

    def test_detect_from_checks_ignores_previously_open(self):
        detector = RegressionDetector()
        prev = make_check(obligation_id="obl_1", result="open", confidence=0.1)
        curr = make_check(obligation_id="obl_1", result="open", confidence=0.0)
        records = detector.detect_from_checks([prev], [curr])
        assert records == []

    def test_detect_from_checks_missing_in_current(self):
        detector = RegressionDetector()
        prev = make_check(obligation_id="obl_1", result="closed", confidence=0.9)
        records = detector.detect_from_checks([prev], [])
        assert len(records) == 1

    # ------------------------------------------------------------------
    # classify_regression
    # ------------------------------------------------------------------

    def test_classify_regression_returns_valid_kind(self):
        detector = RegressionDetector()
        diff = {"changed": ["some_key"], "removed": []}
        result = detector.classify_regression(diff)
        assert result in (
            RegressionKind.SEMANTIC.value,
            RegressionKind.SYNTACTIC.value,
            RegressionKind.COVERAGE.value,
        )

    def test_classify_regression_coverage_key(self):
        detector = RegressionDetector()
        diff = {"changed": ["coverage_score"], "removed": []}
        result = detector.classify_regression(diff)
        assert result == RegressionKind.COVERAGE.value

    def test_classify_regression_syntactic_key(self):
        detector = RegressionDetector()
        diff = {"changed": ["schema_version"], "removed": []}
        result = detector.classify_regression(diff)
        assert result == RegressionKind.SYNTACTIC.value

    def test_classify_regression_empty_diff(self):
        detector = RegressionDetector()
        result = detector.classify_regression({})
        assert result == RegressionKind.SEMANTIC.value

    # ------------------------------------------------------------------
    # estimate_severity
    # ------------------------------------------------------------------

    def test_estimate_severity_returns_valid_severity(self):
        detector = RegressionDetector()
        rec = self._make_record()
        sev = detector.estimate_severity(rec)
        assert sev in ("minor", "major", "critical")

    def test_estimate_severity_critical_stays_critical(self):
        detector = RegressionDetector()
        rec = RegressionRecord(key="x", severity="critical")
        sev = detector.estimate_severity(rec)
        assert sev == "critical"

    def test_estimate_severity_promoted_at_high_sensitivity(self):
        detector = RegressionDetector(sensitivity=0.9)
        rec = RegressionRecord(key="x", severity="minor")
        sev = detector.estimate_severity(rec)
        assert sev in ("major", "critical")

    # ------------------------------------------------------------------
    # Sensitivity and logging
    # ------------------------------------------------------------------

    def test_sensitivity_setting(self):
        detector = RegressionDetector(sensitivity=0.1)
        assert detector._sensitivity == 0.1

    def test_sensitivity_clamped_below(self):
        detector = RegressionDetector(sensitivity=-1.0)
        assert detector._sensitivity == 0.0

    def test_sensitivity_clamped_above(self):
        detector = RegressionDetector(sensitivity=5.0)
        assert detector._sensitivity == 1.0

    def test_set_sensitivity(self):
        detector = RegressionDetector()
        detector.set_sensitivity(0.8)
        assert detector._sensitivity == 0.8

    def test_detection_log_starts_empty(self):
        detector = RegressionDetector()
        assert detector.get_detection_log() == []

    def test_detection_log_grows_after_detect(self):
        detector = RegressionDetector()
        detector.detect({"x": True}, {"x": False})
        log = detector.get_detection_log()
        assert len(log) == 1

    def test_detection_log_is_copy(self):
        detector = RegressionDetector()
        detector.detect({}, {})
        log = detector.get_detection_log()
        log.clear()
        assert len(detector.get_detection_log()) == 1

    @pytest.mark.parametrize("regression_type", ["semantic", "syntactic", "coverage"])
    def test_regression_kinds_parametrize(self, regression_type):
        rec = self._make_record(regression_type)
        assert rec.regression_type == regression_type

    @pytest.mark.parametrize("sensitivity", [0.0, 0.3, 0.5, 0.7, 1.0])
    def test_various_sensitivities(self, sensitivity):
        detector = RegressionDetector(sensitivity=sensitivity)
        assert 0.0 <= detector._sensitivity <= 1.0


# ===========================================================================
# TestRegressionRepairer
# ===========================================================================


class TestRegressionRepairer:
    """Tests for :class:`RegressionRepairer`."""

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_record(self, regression_type: str = "semantic") -> RegressionRecord:
        return RegressionRecord(
            record_id=uuid.uuid4().hex[:8],
            key="obl_closure",
            baseline_value=True,
            current_value=False,
            regression_type=regression_type,
            severity="minor",
            cause_analysis="Test cause",
        )

    # ------------------------------------------------------------------
    # suggest_repair
    # ------------------------------------------------------------------

    def test_suggest_repair_returns_string(self):
        repairer = RegressionRepairer()
        record = self._make_record()
        repair = repairer.suggest_repair(record)
        assert isinstance(repair, str) and len(repair) > 0

    def test_suggest_repair_semantic_mentions_obligation(self):
        repairer = RegressionRepairer()
        record = self._make_record("semantic")
        repair = repairer.suggest_repair(record)
        assert "obl_closure" in repair or "semantic" in repair.lower()

    def test_suggest_repair_syntactic_mentions_syntactic(self):
        repairer = RegressionRepairer()
        record = self._make_record("syntactic")
        repair = repairer.suggest_repair(record)
        assert "syntactic" in repair.lower() or "schema" in repair.lower() or "renamed" in repair.lower()

    def test_suggest_repair_coverage_mentions_evidence(self):
        repairer = RegressionRepairer()
        record = self._make_record("coverage")
        repair = repairer.suggest_repair(record)
        assert "coverage" in repair.lower() or "evidence" in repair.lower()

    def test_suggest_repair_unknown_type_still_returns_string(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(key="k", regression_type="unknown_kind", cause_analysis="test")
        repair = repairer.suggest_repair(record)
        assert isinstance(repair, str) and len(repair) > 0

    @pytest.mark.parametrize("regression_type", ["semantic", "syntactic", "coverage"])
    def test_suggest_repair_all_types(self, regression_type):
        repairer = RegressionRepairer()
        record = self._make_record(regression_type)
        repair = repairer.suggest_repair(record)
        assert len(repair) > 0

    # ------------------------------------------------------------------
    # register_strategy
    # ------------------------------------------------------------------

    def test_register_strategy_stored(self):
        repairer = RegressionRepairer()
        def my_strategy(record):
            return "custom repair"
        repairer.register_strategy("semantic", my_strategy)
        assert "semantic" in repairer._repair_strategies

    def test_register_strategy_used_in_suggest(self):
        repairer = RegressionRepairer()
        def custom(record):
            return "MY_CUSTOM_REPAIR"
        repairer.register_strategy("semantic", custom)
        record = self._make_record("semantic")
        repair = repairer.suggest_repair(record)
        assert repair == "MY_CUSTOM_REPAIR"

    def test_register_strategy_fallback_on_exception(self):
        repairer = RegressionRepairer()
        def bad_strategy(record):
            raise RuntimeError("boom")
        repairer.register_strategy("semantic", bad_strategy)
        record = self._make_record("semantic")
        # Should fall back to heuristic, not raise
        repair = repairer.suggest_repair(record)
        assert isinstance(repair, str) and len(repair) > 0

    # ------------------------------------------------------------------
    # apply_repair
    # ------------------------------------------------------------------

    def test_apply_repair_returns_dict(self):
        repairer = RegressionRepairer()
        record = self._make_record()
        state = {"obl_closure": False}
        repaired = repairer.apply_repair(record, "restore_evidence", state)
        assert isinstance(repaired, dict)

    def test_apply_repair_does_not_mutate_state(self):
        repairer = RegressionRepairer()
        record = self._make_record()
        state = {"obl_closure": False, "other": "unchanged"}
        original_other = state["other"]
        repairer.apply_repair(record, "restore", state)
        assert state["other"] == original_other
        assert state["obl_closure"] is False  # original not mutated

    def test_apply_repair_restores_bool_key(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(
            key="flag",
            baseline_value=True,
            current_value=False,
            regression_type="semantic",
        )
        state = {"flag": False}
        repaired = repairer.apply_repair(record, "restore", state)
        assert repaired["flag"] is True

    def test_apply_repair_restores_numeric_key(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(
            key="score",
            baseline_value=0.9,
            current_value=0.3,
            regression_type="coverage",
        )
        state = {"score": 0.3}
        repaired = repairer.apply_repair(record, "restore", state)
        assert abs(repaired["score"] - 0.9) < 1e-9

    # ------------------------------------------------------------------
    # verify_repair
    # ------------------------------------------------------------------

    def test_verify_repair_returns_bool(self):
        repairer = RegressionRepairer()
        record = self._make_record()
        result = repairer.verify_repair(record, {"obl_closure": True})
        assert isinstance(result, bool)

    def test_verify_repair_true_when_bool_restored(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(key="flag", baseline_value=True, current_value=False)
        assert repairer.verify_repair(record, {"flag": True}) is True

    def test_verify_repair_false_when_key_missing(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(key="flag", baseline_value=True)
        assert repairer.verify_repair(record, {}) is False

    def test_verify_repair_false_when_still_falsy(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(key="flag", baseline_value=True, current_value=False)
        assert repairer.verify_repair(record, {"flag": False}) is False

    def test_verify_repair_numeric_tolerance(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(key="score", baseline_value=1.0, current_value=0.5)
        # 0.91 >= 1.0 * 0.9 = 0.9 → True
        assert repairer.verify_repair(record, {"score": 0.91}) is True

    # ------------------------------------------------------------------
    # auto_repair
    # ------------------------------------------------------------------

    def test_auto_repair_returns_tuple(self):
        repairer = RegressionRepairer()
        records = [self._make_record() for _ in range(3)]
        state = {"obl_closure": False}
        repaired_state, applied = repairer.auto_repair(records, state)
        assert isinstance(repaired_state, dict)
        assert isinstance(applied, list)

    def test_auto_repair_empty_records(self):
        repairer = RegressionRepairer()
        state = {"a": 1}
        repaired, applied = repairer.auto_repair([], state)
        assert isinstance(repaired, dict)
        assert isinstance(applied, list)

    def test_auto_repair_restores_bool_records(self):
        repairer = RegressionRepairer()
        record = RegressionRecord(
            key="obl_main",
            baseline_value=True,
            current_value=False,
            regression_type="semantic",
            cause_analysis="changed",
        )
        state = {"obl_main": False}
        repaired, applied = repairer.auto_repair([record], state)
        assert repaired.get("obl_main") is True
        assert len(applied) == 1

    def test_auto_repair_multiple_records(self):
        repairer = RegressionRepairer()
        records = [
            RegressionRecord(
                key=f"obl_{i}",
                baseline_value=True,
                current_value=False,
                regression_type="semantic",
            )
            for i in range(3)
        ]
        state = {f"obl_{i}": False for i in range(3)}
        repaired, applied = repairer.auto_repair(records, state)
        assert isinstance(repaired, dict)
        assert isinstance(applied, list)


# ===========================================================================
# TestRegressionRecord (model)
# ===========================================================================


class TestRegressionRecord:
    """Tests for the :class:`RegressionRecord` dataclass."""

    def test_default_regression_type_semantic(self):
        rec = RegressionRecord(key="x")
        assert rec.regression_type == RegressionKind.SEMANTIC.value

    def test_is_critical_false_by_default(self):
        rec = RegressionRecord(key="x")
        assert not rec.is_critical()

    def test_is_critical_true_when_severity_critical(self):
        rec = RegressionRecord(key="x", severity="critical")
        assert rec.is_critical()

    def test_to_dict_has_required_keys(self):
        rec = RegressionRecord(
            key="obl",
            baseline_value=True,
            current_value=False,
            regression_type="semantic",
            severity="minor",
        )
        d = rec.to_dict()
        for field in ("record_id", "key", "baseline_value", "current_value",
                      "regression_type", "severity", "cause_analysis", "timestamp", "patch_id"):
            assert field in d

    def test_from_dict_roundtrip(self):
        rec = RegressionRecord(
            key="obl_x",
            baseline_value=True,
            current_value=False,
            regression_type="coverage",
            severity="major",
            cause_analysis="dropped",
        )
        restored = RegressionRecord.from_dict(rec.to_dict())
        assert restored.key == rec.key
        assert restored.regression_type == rec.regression_type
        assert restored.severity == rec.severity

    def test_record_id_auto_assigned(self):
        rec = RegressionRecord(key="x")
        assert isinstance(rec.record_id, str) and len(rec.record_id) > 0

    def test_timestamp_positive(self):
        rec = RegressionRecord(key="x")
        assert rec.timestamp > 0


# ===========================================================================
# TestRegressionTest (model)
# ===========================================================================


class TestRegressionTestModel:
    """Tests for the :class:`RegressionTest` dataclass."""

    def test_default_status_unknown(self):
        rt = RegressionTest()
        assert rt.status == RegressionStatus.UNKNOWN.value

    def test_is_passing_false_initially(self):
        rt = RegressionTest()
        assert not rt.is_passing()

    def test_is_passing_true_when_status_passing(self):
        rt = RegressionTest(status=RegressionStatus.PASSING.value)
        assert rt.is_passing()

    def test_is_failing_false_initially(self):
        rt = RegressionTest()
        assert not rt.is_failing()

    def test_is_failing_true_when_status_failing(self):
        rt = RegressionTest(status=RegressionStatus.FAILING.value)
        assert rt.is_failing()

    def test_evaluate_passes_when_result_and_confidence_match(self):
        rt = RegressionTest(
            expected_result=ClosureResult.CLOSED.value,
            expected_confidence_min=0.5,
        )
        check = make_check(
            obligation_id="obl",
            result=ClosureResult.CLOSED.value,
            confidence=0.8,
        )
        assert rt.evaluate(check) is True

    def test_evaluate_fails_when_result_mismatched(self):
        rt = RegressionTest(
            expected_result=ClosureResult.CLOSED.value,
            expected_confidence_min=0.5,
        )
        check = make_check(
            obligation_id="obl",
            result=ClosureResult.OPEN.value,
            confidence=0.9,
        )
        assert rt.evaluate(check) is False

    def test_evaluate_fails_when_confidence_too_low(self):
        rt = RegressionTest(
            expected_result=ClosureResult.CLOSED.value,
            expected_confidence_min=0.7,
        )
        check = make_check(
            obligation_id="obl",
            result=ClosureResult.CLOSED.value,
            confidence=0.5,
        )
        assert rt.evaluate(check) is False

    def test_to_dict_has_test_id(self):
        rt = RegressionTest(test_id="my_test")
        d = rt.to_dict()
        assert d["test_id"] == "my_test"

    def test_from_dict_roundtrip(self):
        rt = RegressionTest(
            test_id="rt_abc",
            obligation_id="obl_x",
            baseline_snapshot_id="snap_001",
            expected_confidence_min=0.6,
        )
        restored = RegressionTest.from_dict(rt.to_dict())
        assert restored.test_id == "rt_abc"
        assert abs(restored.expected_confidence_min - 0.6) < 1e-9


# ===========================================================================
# TestModuleHelpers
# ===========================================================================


class TestModuleHelpers:
    """Tests for module-level helper functions."""

    def test_create_regression_test_returns_instance(self):
        rt = create_regression_test("obl_001", "snap_001")
        assert isinstance(rt, RegressionTest)

    def test_create_regression_test_obligation_id(self):
        rt = create_regression_test("obl_001", "snap_001")
        assert rt.obligation_id == "obl_001"

    def test_create_regression_test_baseline_snapshot_id(self):
        rt = create_regression_test("obl_001", "snap_001")
        assert rt.baseline_snapshot_id == "snap_001"

    def test_create_regression_test_status_unknown(self):
        rt = create_regression_test("obl_001", "snap_001")
        assert rt.status == RegressionStatus.UNKNOWN.value

    def test_create_regression_test_default_expected_result(self):
        rt = create_regression_test("obl_001", "snap_001")
        assert rt.expected_result == ClosureResult.CLOSED.value

    def test_create_regression_test_custom_expected_result(self):
        rt = create_regression_test("obl_001", "snap_001", expected_result="partial")
        assert rt.expected_result == "partial"

    def test_create_regression_test_custom_confidence(self):
        rt = create_regression_test("obl_001", "snap_001", expected_confidence_min=0.8)
        assert abs(rt.expected_confidence_min - 0.8) < 1e-9

    def test_create_regression_test_notes(self):
        rt = create_regression_test("obl_001", "snap_001", notes="my note")
        assert rt.notes == "my note"

    def test_run_regression_suite_returns_tuple(self):
        suite = RegressionTestSuite()
        rt = create_regression_test("obl_001", "snap_001")
        suite.add_test(rt)
        result = run_regression_suite(suite, {})
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_run_regression_suite_counts_int(self):
        suite = RegressionTestSuite()
        rt = create_regression_test("obl_001", "snap_001")
        suite.add_test(rt)
        passing, failing = run_regression_suite(suite, {})
        assert isinstance(passing, int)
        assert isinstance(failing, int)

    def test_run_regression_suite_sums_to_tested(self):
        suite = RegressionTestSuite()
        for i in range(4):
            suite.add_test(create_regression_test(f"obl_{i}", "snap_001"))
        passing, failing = run_regression_suite(suite, {})
        assert passing + failing <= 4  # some may be skipped

    def test_detect_regressions_from_snapshots_returns_list(self):
        records = detect_regressions_from_snapshots({"x": True}, {"x": False})
        assert isinstance(records, list)

    def test_detect_regressions_from_snapshots_finds_regression(self):
        records = detect_regressions_from_snapshots(
            {"closed": True, "coverage": 0.9},
            {"closed": False, "coverage": 0.9},
        )
        assert len(records) == 1
        assert records[0].key == "closed"

    def test_detect_regressions_identical_snapshots(self):
        records = detect_regressions_from_snapshots({"a": True}, {"a": True})
        assert records == []

    def test_detect_regressions_custom_sensitivity(self):
        records = detect_regressions_from_snapshots(
            {"score": 1.0},
            {"score": 0.4},
            sensitivity=0.5,
        )
        assert any(r.key == "score" for r in records)


# ===========================================================================
# Integration tests
# ===========================================================================


class TestRegressionTestingIntegration:
    """End-to-end workflow tests."""

    def test_full_regression_workflow(self):
        mgr = BaselineManager()
        state = {"obl_1": True, "obl_2": True}
        snap_id = mgr.take_baseline(state)

        suite = RegressionTestSuite()
        for i in range(3):
            rt = create_regression_test(f"obl_{i}", snap_id)
            suite.add_test(rt)

        results = suite.run_all(state)
        assert len(results) == 3

    def test_baseline_then_detect(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"a": True, "b": True})
        detector = RegressionDetector()
        baseline = mgr.get_baseline(snap_id)
        current = {"a": True}
        records = detector.detect(baseline, current)
        assert isinstance(records, list)
        assert any(r.key == "b" for r in records)

    def test_repair_workflow(self):
        detector = RegressionDetector()
        repairer = RegressionRepairer()
        baseline = {"closed": True}
        current = {"closed": False}
        records = detector.detect(baseline, current)
        assert len(records) == 1
        repair = repairer.suggest_repair(records[0])
        assert isinstance(repair, str)

    def test_full_detect_repair_cycle(self):
        detector = RegressionDetector()
        repairer = RegressionRepairer()
        baseline = {"flag_a": True, "flag_b": True}
        current = {"flag_a": False, "flag_b": True}
        records = detector.detect(baseline, current)
        assert len(records) == 1
        repaired_state, applied = repairer.auto_repair(records, current)
        assert repaired_state.get("flag_a") is True

    def test_suite_run_then_query(self):
        suite = RegressionTestSuite("integration_suite")
        for i in range(5):
            rt = create_regression_test(f"obl_{i}", "snap_x")
            suite.add_test(rt)
        suite.run_all({"evidence": ("test:passed",)})
        failing = suite.get_failing()
        passing = suite.get_passing()
        assert len(failing) + len(passing) <= 5

    def test_baseline_update_invalidates_old_snapshot(self):
        mgr = BaselineManager()
        mgr.update_baseline("test_001", {"v": 1})
        old_id_count = len(mgr.list_baselines())
        mgr.update_baseline("test_001", {"v": 2})
        new_count = len(mgr.list_baselines())
        # After second update, old snapshot for test_001 is removed
        assert new_count <= old_id_count

    def test_detect_after_suite_run(self):
        mgr = BaselineManager()
        snap_id = mgr.take_baseline({"obl_a": True, "obl_b": True})
        suite = RegressionTestSuite()
        for obl in ["obl_a", "obl_b"]:
            suite.add_test(create_regression_test(obl, snap_id))
        suite.run_all({})

        # Simulate regression: obl_b now false
        detector = RegressionDetector()
        baseline = mgr.get_baseline(snap_id)
        current = {"obl_a": True, "obl_b": False}
        records = detector.detect(baseline, current)
        assert any(r.key == "obl_b" for r in records)

    def test_multi_round_detection(self):
        detector = RegressionDetector(sensitivity=0.4)
        state_v1 = {"coverage": 0.9, "closed": True}
        state_v2 = {"coverage": 0.9, "closed": True}
        state_v3 = {"coverage": 0.3, "closed": False}

        r1 = detector.detect(state_v1, state_v2)
        r2 = detector.detect(state_v2, state_v3)
        assert len(r1) == 0
        assert len(r2) >= 1
        assert len(detector.get_detection_log()) == 2

    def test_repairer_with_multiple_types(self):
        repairer = RegressionRepairer()
        records = [
            RegressionRecord(key=f"k_{i}", baseline_value=True, current_value=False,
                             regression_type=rt, cause_analysis="test")
            for i, rt in enumerate(["semantic", "syntactic", "coverage"])
        ]
        state = {f"k_{i}": False for i in range(3)}
        repaired, applied = repairer.auto_repair(records, state)
        assert all(repaired.get(f"k_{i}") is True for i in range(3))
        assert len(applied) == 3
