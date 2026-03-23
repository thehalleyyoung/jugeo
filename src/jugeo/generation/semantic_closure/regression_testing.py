r"""Chapter 39, Section 2 — Semantic Closure Regression Testing.

Theory (theory2.tex §39.12 — Regression testing for semantic closure):
    Once a construction run achieves semantic closure, the system must guard
    that closure against future modifications.  A *regression* in the closure
    sense is an obligation that was previously closed but has since become
    open or partial.  Three root causes are identified in theory2.tex §39.12:

    1.  *Semantic regression*: a content change invalidates an obligation
        that was previously satisfied.  Example: a treaty clause that was
        ratified is later challenged and reverted to *proposed* status.

    2.  *Syntactic regression*: a structural change breaks a representation
        invariant that the closure check relied on.  Example: a renamed
        field removes an evidence tag that was the sole support for closure.

    3.  *Coverage regression*: the evidence pool shrinks below the confidence
        threshold even though the underlying obligation has not changed.
        Example: test suite trimming removes passing tests that were counted
        as evidence.

    Regression testing in JuGeo works by snapshotting the *state* of an
    integration run at the point of first closure and then periodically
    re-evaluating :class:`ClosureCheck` records against the current state.
    The :class:`BaselineManager` maintains named snapshots; the
    :class:`RegressionDetector` compares them; the :class:`RegressionRepairer`
    proposes and applies repairs; and the :class:`RegressionTestSuite`
    orchestrates the full lifecycle.

    The detection algorithm is deliberately conservative: any weakening of a
    previously closed check is flagged as a regression regardless of whether
    the closure is expected to hold under the new state.  False positives are
    filtered by the repair engine rather than suppressed at detection time.

    Formal statement (theory2.tex §39.12 Definition 39.12.3):

        regressed(c_old, c_new)  ⟺
            c_old.result = "closed"  ∧  c_new.result ≠ "closed"

    The severity of a regression depends on the drop in confidence:

        Δ = c_old.confidence − c_new.confidence
        severity =
            "critical"  if Δ > 0.5
            "major"     if Δ > 0.2
            "minor"     otherwise

    The :class:`RegressionTestSuite` exposes :meth:`~RegressionTestSuite.run_all`
    which evaluates all pinned tests against the current construction state and
    returns updated :class:`RegressionTest` records for each.

    copilot: s02-regression-testing
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
from collections import defaultdict
from typing import Any, Callable

from .models import (
    ClosureCheck,
    ClosureResult,
    RegressionKind,
    RegressionRecord,
    RegressionStatus,
    RegressionTest,
)
from .closure_checking import ClosureChecker, _keyword_overlap

# ---------------------------------------------------------------------------
# Optional upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause  # type: ignore[import]
    _TREATIES_AVAILABLE = True
except Exception:  # pragma: no cover
    _TREATIES_AVAILABLE = False
    OverlapTreaty = Any  # type: ignore[assignment,misc]
    TreatyClause = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentResult  # type: ignore[import]
    _DESCENT_AVAILABLE = True
except Exception:  # pragma: no cover
    _DESCENT_AVAILABLE = False
    DescentResult = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

__all__ = [
    "RegressionTestSuite",
    "BaselineManager",
    "RegressionDetector",
    "RegressionRepairer",
    # Module-level helpers
    "create_regression_test",
    "run_regression_suite",
    "detect_regressions_from_snapshots",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deep_copy_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *state*, tolerating non-serialisable values.

    Non-copyable values are replaced by their ``repr()`` string so that
    the baseline snapshot is always fully serialisable.

    Parameters
    ----------
    state:
        The state dict to copy.
    """
    result: dict[str, Any] = {}
    for k, v in state.items():
        try:
            result[k] = copy.deepcopy(v)
        except Exception:
            result[k] = repr(v)
    return result


def _state_key_is_truthy(state: dict[str, Any], key: str) -> bool:
    """Return ``True`` iff *key* is present in *state* and its value is truthy.

    Parameters
    ----------
    state:
        State dict to query.
    key:
        The key to check.
    """
    return bool(state.get(key))


def _confidence_drop_severity(delta: float) -> str:
    """Map a confidence drop *delta* to a severity string.

    Parameters
    ----------
    delta:
        ``old_confidence - new_confidence``, a non-negative float.

    Returns
    -------
    str
        One of ``"minor"``, ``"major"``, ``"critical"``.
    """
    if delta > 0.5:
        return "critical"
    if delta > 0.2:
        return "major"
    return "minor"


def _infer_regression_kind(key: str) -> str:
    """Infer the :class:`RegressionKind` from a state key name.

    Uses keyword heuristics to classify regressions without requiring
    access to the full state.

    Parameters
    ----------
    key:
        The state key that regressed.
    """
    key_lower = key.lower()
    coverage_keywords = {"coverage", "evidence", "count", "rate", "density", "score"}
    syntactic_keywords = {"syntax", "struct", "schema", "type", "format", "parse"}
    for kw in coverage_keywords:
        if kw in key_lower:
            return RegressionKind.COVERAGE.value
    for kw in syntactic_keywords:
        if kw in key_lower:
            return RegressionKind.SYNTACTIC.value
    return RegressionKind.SEMANTIC.value


def _generate_cause_analysis(key: str, baseline_value: Any, current_value: Any) -> str:
    """Produce a short cause-analysis string for a regression.

    Parameters
    ----------
    key:
        The regressing state key.
    baseline_value:
        Value in the baseline snapshot.
    current_value:
        Value in the current state (may be ``None``).
    """
    if current_value is None:
        return f"Key '{key}' was present (value={baseline_value!r}) but is now missing."
    if not current_value and baseline_value:
        return (
            f"Key '{key}' changed from truthy ({baseline_value!r}) "
            f"to falsy ({current_value!r})."
        )
    if isinstance(baseline_value, (int, float)) and isinstance(current_value, (int, float)):
        delta = baseline_value - current_value  # type: ignore[operator]
        return f"Key '{key}' dropped by {delta:.3f} ({baseline_value} → {current_value})."
    return f"Key '{key}' regressed from {baseline_value!r} to {current_value!r}."


# ---------------------------------------------------------------------------
# RegressionTestSuite
# ---------------------------------------------------------------------------


class RegressionTestSuite:
    """An ordered collection of :class:`RegressionTest` records.

    The suite provides a single :meth:`run_all` entry point that evaluates
    every registered test against a supplied *current_state* dict and
    returns the updated tests.

    Each test is evaluated by re-running a :class:`ClosureChecker` against
    the evidence extracted from *current_state* (key ``"evidence_<test_id>"``
    or the global ``"evidence"`` key) and comparing the result to the test's
    expected closure.

    Parameters
    ----------
    suite_id:
        Optional human-readable identifier.  If not provided a random hex
        string is used.

    Examples
    --------
    >>> suite = RegressionTestSuite("suite_alpha")
    >>> test = create_regression_test("obligation_1", "snap_001")
    >>> suite.add_test(test)
    >>> results = suite.run_all({"evidence": ("test:passed", "review:ok")})
    >>> suite.failure_rate()
    0.0
    """

    def __init__(self, suite_id: str | None = None) -> None:
        self.suite_id: str = suite_id or uuid.uuid4().hex[:12]
        self._tests: dict[str, RegressionTest] = {}
        self._run_history: list[dict[str, Any]] = []
        self._checker: ClosureChecker = ClosureChecker(trust_threshold=0.5)

    # ------------------------------------------------------------------
    # Test management
    # ------------------------------------------------------------------

    def add_test(self, test: RegressionTest) -> None:
        """Register a :class:`RegressionTest` in the suite.

        If a test with the same ``test_id`` already exists it is overwritten.

        Parameters
        ----------
        test:
            The test to add.
        """
        self._tests[test.test_id] = test
        logger.debug(
            "RegressionTestSuite.add_test suite=%r test_id=%r obligation=%r",
            self.suite_id,
            test.test_id,
            test.obligation_id[:60],
        )

    def remove_test(self, test_id: str) -> RegressionTest | None:
        """Remove and return the test with *test_id*, or ``None`` if absent.

        Parameters
        ----------
        test_id:
            Identifier of the test to remove.
        """
        removed = self._tests.pop(test_id, None)
        if removed:
            logger.debug(
                "RegressionTestSuite.remove_test suite=%r test_id=%r",
                self.suite_id,
                test_id,
            )
        return removed

    def test_count(self) -> int:
        """Return the number of tests currently in the suite."""
        return len(self._tests)

    def filter_by_obligation(self, obligation_id: str) -> list[RegressionTest]:
        """Return all tests whose ``obligation_id`` exactly matches *obligation_id*.

        Parameters
        ----------
        obligation_id:
            Obligation to filter by.
        """
        return [t for t in self._tests.values() if t.obligation_id == obligation_id]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_all(
        self,
        current_state: dict[str, Any],
    ) -> dict[str, RegressionTest]:
        """Evaluate every registered test against *current_state*.

        For each test the method:

        1.  Extracts evidence from *current_state* — first looking for a
            test-specific key ``"evidence_<test_id>"``, then falling back
            to the global ``"evidence"`` key.
        2.  Runs a :class:`ClosureChecker.check` call.
        3.  Calls :meth:`RegressionTest.evaluate` to determine pass/fail.
        4.  Updates the test's ``status``, ``last_run``, and
            ``failure_reason`` fields.

        A summary entry is appended to the internal run history.

        Parameters
        ----------
        current_state:
            Dict representing the current construction state.  Must contain
            at least an ``"evidence"`` key with a tuple or list of evidence
            tags to be useful.

        Returns
        -------
        dict[str, RegressionTest]
            Updated copy of the internal tests dict.
        """
        run_start = time.time()
        global_evidence: tuple[str, ...] = tuple(
            current_state.get("evidence", ())
        )
        patch_id: str = current_state.get("patch_id", "")

        passed = 0
        failed = 0
        skipped = 0

        for test_id, test in self._tests.items():
            # Allow per-test evidence override
            test_specific_key = f"evidence_{test_id}"
            evidence: tuple[str, ...] = tuple(
                current_state.get(test_specific_key, global_evidence)
            )

            if current_state.get(f"skip_{test_id}", False):
                test.status = RegressionStatus.SKIPPED.value
                test.last_run = time.time()
                skipped += 1
                continue

            cc = self._checker.check(
                test.obligation_id,
                evidence,
                patch_id=patch_id,
                check_type="semantic",
            )
            test.last_run = time.time()

            if test.evaluate(cc):
                test.status = RegressionStatus.PASSING.value
                test.failure_reason = ""
                passed += 1
            else:
                test.status = RegressionStatus.FAILING.value
                test.failure_reason = (
                    f"Expected result={test.expected_result!r} "
                    f"confidence≥{test.expected_confidence_min:.2f}, "
                    f"got result={cc.result!r} confidence={cc.confidence:.2f}"
                )
                failed += 1

        run_record: dict[str, Any] = {
            "run_id": uuid.uuid4().hex[:12],
            "suite_id": self.suite_id,
            "timestamp": run_start,
            "duration_s": time.time() - run_start,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": len(self._tests),
        }
        self._run_history.append(run_record)
        logger.info(
            "RegressionTestSuite.run_all suite=%r passed=%d failed=%d skipped=%d",
            self.suite_id,
            passed,
            failed,
            skipped,
        )
        return dict(self._tests)

    def run_single(
        self,
        test_id: str,
        current_state: dict[str, Any],
    ) -> RegressionTest | None:
        """Run only the test identified by *test_id*.

        Returns the updated :class:`RegressionTest` or ``None`` if not found.

        Parameters
        ----------
        test_id:
            Identifier of the test to run.
        current_state:
            Current construction state dict.
        """
        test = self._tests.get(test_id)
        if test is None:
            logger.warning(
                "RegressionTestSuite.run_single: test_id=%r not found", test_id
            )
            return None

        global_evidence: tuple[str, ...] = tuple(
            current_state.get("evidence", ())
        )
        evidence: tuple[str, ...] = tuple(
            current_state.get(f"evidence_{test_id}", global_evidence)
        )
        patch_id: str = current_state.get("patch_id", "")

        cc = self._checker.check(
            test.obligation_id,
            evidence,
            patch_id=patch_id,
            check_type="semantic",
        )
        test.last_run = time.time()

        if test.evaluate(cc):
            test.status = RegressionStatus.PASSING.value
            test.failure_reason = ""
        else:
            test.status = RegressionStatus.FAILING.value
            test.failure_reason = (
                f"Expected result={test.expected_result!r} "
                f"confidence≥{test.expected_confidence_min:.2f}, "
                f"got result={cc.result!r} confidence={cc.confidence:.2f}"
            )
        return test

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_failing(self) -> list[RegressionTest]:
        """Return all tests whose status is ``"failing"``."""
        return [t for t in self._tests.values() if t.is_failing()]

    def get_passing(self) -> list[RegressionTest]:
        """Return all tests whose status is ``"passing"``."""
        return [t for t in self._tests.values() if t.is_passing()]

    def get_unknown(self) -> list[RegressionTest]:
        """Return all tests whose status is ``"unknown"``."""
        return [
            t for t in self._tests.values()
            if t.status == RegressionStatus.UNKNOWN.value
        ]

    def failure_rate(self) -> float:
        """Return the fraction of tests that are currently failing.

        Returns ``0.0`` when the suite is empty.
        """
        total = len(self._tests)
        if total == 0:
            return 0.0
        failing = sum(1 for t in self._tests.values() if t.is_failing())
        return failing / total

    def summary(self) -> str:
        """Return a human-readable one-line summary of the suite's current state.

        Returns
        -------
        str
            E.g. ``"Suite 'suite_alpha': 8 tests — 7 passing, 1 failing (12.5%)"``
        """
        total = len(self._tests)
        passing = len(self.get_passing())
        failing = len(self.get_failing())
        unknown = len(self.get_unknown())
        rate = self.failure_rate() * 100.0
        return (
            f"Suite '{self.suite_id}': {total} tests — "
            f"{passing} passing, {failing} failing, {unknown} unknown "
            f"(failure rate {rate:.1f}%)"
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the suite to a plain :class:`dict`.

        Returns
        -------
        dict[str, Any]
            Serialisable representation of the suite.
        """
        return {
            "suite_id": self.suite_id,
            "tests": {tid: t.to_dict() for tid, t in self._tests.items()},
            "run_history": list(self._run_history),
        }


# ---------------------------------------------------------------------------
# BaselineManager
# ---------------------------------------------------------------------------


class BaselineManager:
    """Manages named baseline snapshots for regression testing.

    Each snapshot is a deep copy of a construction state dict identified by
    a unique *snapshot_id*.  Tests are associated with snapshots via the
    *test_baselines* mapping so that :meth:`get_test_baseline` can retrieve
    the correct snapshot for a given test.

    Examples
    --------
    >>> mgr = BaselineManager()
    >>> snap_id = mgr.take_baseline({"evidence": ("a", "b"), "closed": True})
    >>> mgr.get_baseline(snap_id)["closed"]
    True
    """

    def __init__(self) -> None:
        self._baselines: dict[str, dict[str, Any]] = {}
        self._test_baselines: dict[str, str] = {}  # test_id -> snapshot_id
        self._labels: dict[str, str] = {}  # snapshot_id -> label

    # ------------------------------------------------------------------
    # Snapshot lifecycle
    # ------------------------------------------------------------------

    def take_baseline(
        self,
        state: dict[str, Any],
        label: str | None = None,
    ) -> str:
        """Deep-copy *state* and store it as a new baseline snapshot.

        Parameters
        ----------
        state:
            The state dict to snapshot.
        label:
            Optional human-readable label for the snapshot.

        Returns
        -------
        str
            The generated snapshot ID (a 16-character hex string).
        """
        snapshot_id = uuid.uuid4().hex[:16]
        snapshot = _deep_copy_state(state)
        snapshot["__snapshot_id__"] = snapshot_id
        snapshot["__taken_at__"] = time.time()
        self._baselines[snapshot_id] = snapshot
        if label:
            self._labels[snapshot_id] = label
        logger.debug(
            "BaselineManager.take_baseline snapshot_id=%r label=%r keys=%d",
            snapshot_id,
            label,
            len(state),
        )
        return snapshot_id

    def get_baseline(self, snapshot_id: str) -> dict[str, Any] | None:
        """Retrieve the snapshot with *snapshot_id*, or ``None`` if not found.

        Parameters
        ----------
        snapshot_id:
            The snapshot to retrieve.
        """
        return self._baselines.get(snapshot_id)

    def update_baseline(
        self,
        test_id: str,
        state: dict[str, Any],
    ) -> str:
        """Replace the baseline associated with *test_id* with a new snapshot.

        If *test_id* had a previous baseline, that snapshot is removed from
        the store.

        Parameters
        ----------
        test_id:
            The test whose baseline is being updated.
        state:
            New state to use as the baseline.

        Returns
        -------
        str
            The new snapshot ID.
        """
        old_snapshot_id = self._test_baselines.get(test_id)
        if old_snapshot_id and old_snapshot_id in self._baselines:
            del self._baselines[old_snapshot_id]
            self._labels.pop(old_snapshot_id, None)
            logger.debug(
                "BaselineManager.update_baseline: removed old snapshot %r for test %r",
                old_snapshot_id,
                test_id,
            )

        new_snapshot_id = self.take_baseline(state, label=f"test:{test_id}")
        self._test_baselines[test_id] = new_snapshot_id
        return new_snapshot_id

    def delete_baseline(self, snapshot_id: str) -> bool:
        """Delete a baseline snapshot.

        Also removes any test→snapshot mappings pointing to *snapshot_id*.

        Parameters
        ----------
        snapshot_id:
            The snapshot to delete.

        Returns
        -------
        bool
            ``True`` if the snapshot existed and was deleted, ``False``
            otherwise.
        """
        if snapshot_id not in self._baselines:
            return False
        del self._baselines[snapshot_id]
        self._labels.pop(snapshot_id, None)
        # Remove any test mappings
        stale_tests = [
            tid for tid, sid in self._test_baselines.items()
            if sid == snapshot_id
        ]
        for tid in stale_tests:
            del self._test_baselines[tid]
        return True

    def list_baselines(self) -> list[str]:
        """Return a sorted list of all known snapshot IDs."""
        return sorted(self._baselines.keys())

    def get_test_baseline(self, test_id: str) -> dict[str, Any] | None:
        """Return the baseline snapshot registered for *test_id*, or ``None``.

        Parameters
        ----------
        test_id:
            Test identifier to look up.
        """
        snapshot_id = self._test_baselines.get(test_id)
        if snapshot_id is None:
            return None
        return self.get_baseline(snapshot_id)

    # ------------------------------------------------------------------
    # Diffing
    # ------------------------------------------------------------------

    def diff_baselines(
        self,
        id1: str,
        id2: str,
    ) -> dict[str, Any]:
        """Return a structural diff between two baseline snapshots.

        The diff has three keys:

        * ``"added"``   — keys present in *id2* but not *id1*.
        * ``"removed"`` — keys present in *id1* but not *id2*.
        * ``"changed"`` — keys present in both but with different values.

        Internal metadata keys (prefixed with ``"__"``) are excluded.

        Parameters
        ----------
        id1:
            First (older) snapshot ID.
        id2:
            Second (newer) snapshot ID.

        Returns
        -------
        dict[str, Any]
            Diff summary with ``"added"``, ``"removed"``, ``"changed"`` keys.
            Returns empty diff if either snapshot does not exist.
        """
        snap1 = self._baselines.get(id1)
        snap2 = self._baselines.get(id2)
        if snap1 is None or snap2 is None:
            logger.warning(
                "BaselineManager.diff_baselines: one or both snapshots missing "
                "(id1=%r exists=%s, id2=%r exists=%s)",
                id1, snap1 is not None,
                id2, snap2 is not None,
            )
            return {"added": [], "removed": [], "changed": []}

        # Filter internal metadata keys
        keys1 = {k for k in snap1 if not k.startswith("__")}
        keys2 = {k for k in snap2 if not k.startswith("__")}

        added = sorted(keys2 - keys1)
        removed = sorted(keys1 - keys2)
        changed: list[str] = []

        for k in sorted(keys1 & keys2):
            v1 = snap1[k]
            v2 = snap2[k]
            # Use equality check; for mutable containers convert to repr
            try:
                equal = (v1 == v2)
            except Exception:
                equal = repr(v1) == repr(v2)
            if not equal:
                changed.append(k)

        return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# RegressionDetector
# ---------------------------------------------------------------------------


class RegressionDetector:
    """Detects regressions by comparing baseline and current state snapshots.

    The detector implements a *conservative* strategy: any key that was
    truthy in the baseline and is now falsy or missing is flagged as a
    potential regression.  The caller is responsible for filtering
    false positives through the :class:`RegressionRepairer`.

    Parameters
    ----------
    sensitivity:
        Float in ``[0, 1]``.  Higher sensitivity means more regressions
        are reported (lower overlap threshold for matching keys to
        regression classes).  Defaults to ``0.5``.

    Examples
    --------
    >>> detector = RegressionDetector(sensitivity=0.6)
    >>> baseline = {"all_closed": True, "coverage": 0.95}
    >>> current = {"all_closed": False, "coverage": 0.95}
    >>> records = detector.detect(baseline, current)
    >>> records[0].key
    'all_closed'
    """

    def __init__(self, sensitivity: float = 0.5) -> None:
        self._sensitivity = max(0.0, min(sensitivity, 1.0))
        self._detection_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def detect(
        self,
        baseline: dict[str, Any],
        current: dict[str, Any],
    ) -> list[RegressionRecord]:
        """Compare *baseline* to *current*, returning regressions found.

        A regression is detected when a key present in *baseline* with a
        truthy value becomes falsy or absent in *current*.  Numeric keys
        that have dropped significantly (by more than ``sensitivity * baseline``
        for positive numerics) are also flagged.

        Parameters
        ----------
        baseline:
            Reference state snapshot.
        current:
            Current state to compare against.

        Returns
        -------
        list[RegressionRecord]
            One record per detected regression, sorted by key name.
        """
        records: list[RegressionRecord] = []
        baseline_keys = {k for k in baseline if not k.startswith("__")}

        for key in sorted(baseline_keys):
            b_val = baseline[key]
            c_val = current.get(key)  # may be None if key was removed

            regression_detected = False

            if c_val is None:
                # Key was removed
                if b_val:
                    regression_detected = True
            elif isinstance(b_val, bool) and isinstance(c_val, bool):
                regression_detected = b_val and not c_val
            elif isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
                # Numeric regression: drop proportional to sensitivity threshold
                if b_val > 0:
                    drop_ratio = (b_val - c_val) / b_val
                    regression_detected = drop_ratio > self._sensitivity
            else:
                # Generic: was truthy, now falsy
                regression_detected = bool(b_val) and not bool(c_val)

            if regression_detected:
                regression_type = _infer_regression_kind(key)
                severity = self._compute_numeric_severity(b_val, c_val)
                cause = _generate_cause_analysis(key, b_val, c_val)

                record = RegressionRecord(
                    key=key,
                    baseline_value=b_val,
                    current_value=c_val,
                    regression_type=regression_type,
                    severity=severity,
                    cause_analysis=cause,
                )
                records.append(record)

        # Append to detection log
        log_entry: dict[str, Any] = {
            "timestamp": time.time(),
            "baseline_keys": len(baseline_keys),
            "regressions_found": len(records),
            "severity_breakdown": defaultdict(int),
        }
        for r in records:
            log_entry["severity_breakdown"][r.severity] += 1  # type: ignore[index]
        log_entry["severity_breakdown"] = dict(log_entry["severity_breakdown"])
        self._detection_log.append(log_entry)

        logger.debug(
            "RegressionDetector.detect: %d regressions found",
            len(records),
        )
        return records

    def detect_from_checks(
        self,
        previous_checks: list[ClosureCheck],
        current_checks: list[ClosureCheck],
    ) -> list[RegressionRecord]:
        """Find checks that went from ``"closed"`` to non-``"closed"``.

        The two lists are matched by ``obligation_id``.  Any obligation that
        was ``"closed"`` in *previous_checks* but is not ``"closed"`` in
        *current_checks* (or is absent) is reported as a regression.

        Parameters
        ----------
        previous_checks:
            Prior :class:`ClosureCheck` records.
        current_checks:
            Current :class:`ClosureCheck` records.

        Returns
        -------
        list[RegressionRecord]
            Regressions detected, sorted by obligation ID.
        """
        prev_map: dict[str, ClosureCheck] = {
            c.obligation_id: c for c in previous_checks
        }
        curr_map: dict[str, ClosureCheck] = {
            c.obligation_id: c for c in current_checks
        }

        records: list[RegressionRecord] = []

        for obligation_id, prev_check in sorted(prev_map.items()):
            if not prev_check.is_closed():
                continue  # Only flag checks that were previously closed

            curr_check = curr_map.get(obligation_id)
            if curr_check is None or not curr_check.is_closed():
                # Regression detected
                new_confidence = curr_check.confidence if curr_check else 0.0
                delta = prev_check.confidence - new_confidence
                severity = _confidence_drop_severity(delta)

                new_result = curr_check.result if curr_check else ClosureResult.OPEN.value
                cause = (
                    f"Obligation '{obligation_id[:60]}' was "
                    f"closed (confidence={prev_check.confidence:.2f}) but is now "
                    f"{new_result} (confidence={new_confidence:.2f}). "
                    f"Δconfidence={delta:.2f}."
                )

                record = RegressionRecord(
                    key=obligation_id,
                    baseline_value=prev_check.result,
                    current_value=new_result,
                    regression_type=RegressionKind.SEMANTIC.value,
                    severity=severity,
                    cause_analysis=cause,
                    patch_id=prev_check.patch_id,
                )
                records.append(record)

        return records

    def classify_regression(self, diff: dict[str, Any]) -> str:
        """Classify a diff dict as ``"semantic"``, ``"syntactic"``, or ``"coverage"``.

        The classification is based on what keys appear in ``diff["changed"]``.

        Parameters
        ----------
        diff:
            A diff dict as returned by :meth:`BaselineManager.diff_baselines`.

        Returns
        -------
        str
            One of ``"semantic"``, ``"syntactic"``, ``"coverage"``.
        """
        changed_keys: list[str] = diff.get("changed", [])
        removed_keys: list[str] = diff.get("removed", [])
        all_keys = changed_keys + removed_keys

        coverage_score = 0
        syntactic_score = 0

        for key in all_keys:
            kind = _infer_regression_kind(key)
            if kind == RegressionKind.COVERAGE.value:
                coverage_score += 1
            elif kind == RegressionKind.SYNTACTIC.value:
                syntactic_score += 1

        if coverage_score > syntactic_score and coverage_score > 0:
            return RegressionKind.COVERAGE.value
        if syntactic_score > 0:
            return RegressionKind.SYNTACTIC.value
        return RegressionKind.SEMANTIC.value

    def estimate_severity(self, regression: RegressionRecord) -> str:
        """Re-estimate the severity of *regression* using the detector's sensitivity.

        The base severity stored in *regression* is refined by the current
        sensitivity setting.  High-sensitivity detectors classify more
        regressions as ``"critical"``.

        Parameters
        ----------
        regression:
            The :class:`RegressionRecord` to re-evaluate.

        Returns
        -------
        str
            One of ``"minor"``, ``"major"``, ``"critical"``.
        """
        base = regression.severity
        if base == "critical":
            return "critical"

        # Promote based on sensitivity
        if self._sensitivity >= 0.8 and base == "minor":
            return "major"
        if self._sensitivity >= 0.9 and base == "major":
            return "critical"
        return base

    def set_sensitivity(self, sensitivity: float) -> None:
        """Update the detector's sensitivity.

        Parameters
        ----------
        sensitivity:
            New sensitivity value, clamped to ``[0, 1]``.
        """
        self._sensitivity = max(0.0, min(sensitivity, 1.0))

    def get_detection_log(self) -> list[dict[str, Any]]:
        """Return a copy of the internal detection log."""
        return list(self._detection_log)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_numeric_severity(
        self,
        baseline_value: Any,
        current_value: Any,
    ) -> str:
        """Compute severity for a numeric regression.

        Parameters
        ----------
        baseline_value:
            Value at baseline.
        current_value:
            Current value (may be ``None``).
        """
        if current_value is None:
            return "critical"
        if isinstance(baseline_value, (int, float)) and isinstance(current_value, (int, float)):
            if baseline_value == 0:
                return "minor"
            delta = abs(float(baseline_value) - float(current_value)) / abs(float(baseline_value))
            return _confidence_drop_severity(delta)
        return "minor"


# ---------------------------------------------------------------------------
# RegressionRepairer
# ---------------------------------------------------------------------------


class RegressionRepairer:
    """Suggests and applies repairs for detected regressions.

    Repairs are generated by combining the regression's
    :attr:`~RegressionRecord.regression_type` and
    :attr:`~RegressionRecord.cause_analysis` with registered repair
    strategy callables.

    Custom strategies can be registered with :meth:`register_strategy`.
    If no custom strategy matches, a fallback heuristic is used.

    Examples
    --------
    >>> repairer = RegressionRepairer()
    >>> record = RegressionRecord(
    ...     key="all_closed",
    ...     baseline_value=True,
    ...     current_value=False,
    ...     regression_type="semantic",
    ...     severity="major",
    ... )
    >>> suggestion = repairer.suggest_repair(record)
    >>> print(suggestion[:40])
    Semantic repair: re-verify obligation '
    """

    def __init__(self) -> None:
        self._repair_strategies: dict[str, Callable[..., str]] = {}
        self._repair_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Strategy registration
    # ------------------------------------------------------------------

    def register_strategy(
        self,
        regression_type: str,
        strategy: Callable[..., str],
    ) -> None:
        """Register a custom repair strategy for *regression_type*.

        The strategy callable receives a single :class:`RegressionRecord`
        and returns a repair description string.

        Parameters
        ----------
        regression_type:
            One of ``"semantic"``, ``"syntactic"``, ``"coverage"``.
        strategy:
            Callable ``(RegressionRecord) -> str``.
        """
        self._repair_strategies[regression_type] = strategy
        logger.debug(
            "RegressionRepairer.register_strategy: type=%r strategy=%r",
            regression_type,
            getattr(strategy, "__name__", repr(strategy)),
        )

    # ------------------------------------------------------------------
    # Repair generation
    # ------------------------------------------------------------------

    def suggest_repair(self, record: RegressionRecord) -> str:
        """Generate a repair suggestion for *record*.

        Checks for a registered strategy first; if none is found, applies
        a built-in heuristic based on *regression_type*.

        Parameters
        ----------
        record:
            The :class:`RegressionRecord` to generate a repair for.

        Returns
        -------
        str
            A human-readable repair description.
        """
        strategy = self._repair_strategies.get(record.regression_type)
        if strategy is not None:
            try:
                return strategy(record)
            except Exception as exc:
                logger.warning(
                    "RegressionRepairer.suggest_repair: strategy raised %r; "
                    "falling back to heuristic",
                    exc,
                )

        # Built-in heuristics
        key_short = record.key[:60]
        if record.regression_type == RegressionKind.SEMANTIC.value:
            return (
                f"Semantic repair: re-verify obligation '{key_short}' against "
                f"current evidence pool.  If the obligation has genuinely changed, "
                f"update the baseline with BaselineManager.update_baseline().  "
                f"Cause: {record.cause_analysis}"
            )
        elif record.regression_type == RegressionKind.SYNTACTIC.value:
            return (
                f"Syntactic repair: check for renamed or restructured keys near "
                f"'{key_short}'.  Ensure schema migrations are applied before "
                f"running the regression suite.  "
                f"Cause: {record.cause_analysis}"
            )
        elif record.regression_type == RegressionKind.COVERAGE.value:
            return (
                f"Coverage repair: restore the evidence that previously supported "
                f"'{key_short}'.  Consider increasing the evidence pool or "
                f"lowering the trust threshold.  "
                f"Cause: {record.cause_analysis}"
            )
        return (
            f"Unknown regression type '{record.regression_type}' for key "
            f"'{key_short}'.  Manual inspection recommended."
        )

    def apply_repair(
        self,
        record: RegressionRecord,
        repair: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Simulate applying *repair* to *state*, returning a modified copy.

        The simulation is conservative: it restores the baseline value of the
        regressing key in the state copy.  If the baseline value was a bool,
        it is set to ``True``.  If numeric, it is restored exactly.  For
        other types the value is set to the string ``"<repaired>"``.

        This method does NOT mutate *state*.

        Parameters
        ----------
        record:
            The regression to repair.
        repair:
            The repair description (for audit logging).
        state:
            The current state dict.

        Returns
        -------
        dict[str, Any]
            A shallow copy of *state* with the repaired key set.
        """
        repaired = dict(state)
        b_val = record.baseline_value

        if isinstance(b_val, bool):
            repaired[record.key] = True
        elif isinstance(b_val, (int, float)):
            repaired[record.key] = b_val
        elif b_val is not None:
            repaired[record.key] = b_val
        else:
            repaired[record.key] = "<repaired>"

        history_entry: dict[str, Any] = {
            "timestamp": time.time(),
            "record_id": record.record_id,
            "key": record.key,
            "repair": repair[:200],
            "baseline_value": record.baseline_value,
            "restored_value": repaired[record.key],
        }
        self._repair_history.append(history_entry)
        logger.debug(
            "RegressionRepairer.apply_repair: key=%r repair=%r",
            record.key,
            repair[:80],
        )
        return repaired

    def verify_repair(
        self,
        record: RegressionRecord,
        repaired_state: dict[str, Any],
    ) -> bool:
        """Check that *record*'s regression is no longer present in *repaired_state*.

        A repair is considered successful if the key in *repaired_state*:

        * Is present.
        * Has a truthy value (for bool/generic types).
        * Has a value ≥ ``record.baseline_value * 0.9`` (for numerics, allowing
          a 10 % tolerance to account for floating-point imprecision).

        Parameters
        ----------
        record:
            The regression record to verify against.
        repaired_state:
            The state after applying a repair.

        Returns
        -------
        bool
            ``True`` iff the regression is resolved.
        """
        repaired_value = repaired_state.get(record.key)

        if repaired_value is None:
            return False

        b_val = record.baseline_value
        if isinstance(b_val, bool):
            return bool(repaired_value)
        elif isinstance(b_val, (int, float)) and isinstance(repaired_value, (int, float)):
            # 10 % tolerance
            return repaired_value >= b_val * 0.9
        else:
            return bool(repaired_value)

    def auto_repair(
        self,
        records: list[RegressionRecord],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Attempt to repair all regressions in *records* sequentially.

        Each regression is repaired in order by calling :meth:`suggest_repair`
        and then :meth:`apply_repair`.  The repair is verified with
        :meth:`verify_repair`; if verification fails, the unrepaired state is
        preserved for that key.

        Parameters
        ----------
        records:
            Regressions to repair.
        state:
            The current construction state.

        Returns
        -------
        tuple[dict[str, Any], list[str]]
            ``(repaired_state, applied_repairs)`` where *applied_repairs*
            is the list of repair description strings that passed verification.
        """
        current_state = dict(state)
        applied_repairs: list[str] = []

        for record in records:
            repair = self.suggest_repair(record)
            candidate_state = self.apply_repair(record, repair, current_state)
            if self.verify_repair(record, candidate_state):
                current_state = candidate_state
                applied_repairs.append(repair)
                logger.debug(
                    "RegressionRepairer.auto_repair: applied repair for key=%r",
                    record.key,
                )
            else:
                logger.warning(
                    "RegressionRepairer.auto_repair: repair verification failed for key=%r",
                    record.key,
                )

        return current_state, applied_repairs


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def create_regression_test(
    obligation_id: str,
    baseline_snapshot_id: str,
    expected_result: str = ClosureResult.CLOSED.value,
    expected_confidence_min: float = 0.5,
    notes: str = "",
) -> RegressionTest:
    """Create a new :class:`RegressionTest` with sensible defaults.

    The test is initialised with status ``"unknown"`` and will be
    evaluated the next time :meth:`RegressionTestSuite.run_all` is called.

    Parameters
    ----------
    obligation_id:
        The obligation this test should guard.
    baseline_snapshot_id:
        Identifier of the baseline snapshot to compare against.
    expected_result:
        The closure result the obligation must achieve (default ``"closed"``).
    expected_confidence_min:
        Minimum confidence required for the test to pass (default ``0.5``).
    notes:
        Free-form annotation.

    Returns
    -------
    RegressionTest
        A freshly created :class:`RegressionTest`.

    Examples
    --------
    >>> t = create_regression_test("overlap resolved", "snap_abc123")
    >>> t.status
    'unknown'
    >>> t.expected_result
    'closed'
    """
    return RegressionTest(
        obligation_id=obligation_id,
        baseline_snapshot_id=baseline_snapshot_id,
        status=RegressionStatus.UNKNOWN.value,
        expected_result=expected_result,
        expected_confidence_min=expected_confidence_min,
        notes=notes,
    )


def run_regression_suite(
    suite: RegressionTestSuite,
    state: dict[str, Any],
) -> tuple[int, int]:
    """Run all tests in *suite* against *state* and return ``(passing, failing)``.

    This is a thin convenience wrapper around
    :meth:`RegressionTestSuite.run_all`.

    Parameters
    ----------
    suite:
        The :class:`RegressionTestSuite` to run.
    state:
        Current construction state dict.

    Returns
    -------
    tuple[int, int]
        ``(number_passing, number_failing)``

    Examples
    --------
    >>> suite = RegressionTestSuite()
    >>> suite.add_test(create_regression_test("ob1", "snap_x"))
    >>> passing, failing = run_regression_suite(suite, {"evidence": ("test:passed",)})
    """
    suite.run_all(state)
    passing = len(suite.get_passing())
    failing = len(suite.get_failing())
    return passing, failing


def detect_regressions_from_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
    sensitivity: float = 0.5,
) -> list[RegressionRecord]:
    """Convenience function: create a :class:`RegressionDetector` and run detection.

    Parameters
    ----------
    baseline:
        Reference state snapshot.
    current:
        Current state to compare against.
    sensitivity:
        Detector sensitivity (default ``0.5``).

    Returns
    -------
    list[RegressionRecord]
        All detected regressions.

    Examples
    --------
    >>> records = detect_regressions_from_snapshots(
    ...     {"closed": True, "coverage": 0.9},
    ...     {"closed": False, "coverage": 0.9},
    ... )
    >>> len(records)
    1
    >>> records[0].key
    'closed'
    """
    detector = RegressionDetector(sensitivity=sensitivity)
    return detector.detect(baseline, current)
