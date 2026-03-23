r"""Shared dataclass models for the ``jugeo.generation.semantic_closure`` package.

Theory (theory2.tex §39 — Semantic Closure):
    Semantic closure is the property that every obligation generated during
    local construction has been discharged — either by producing direct
    evidence, by satisfying a treaty clause, or by propagating the obligation
    to a neighbouring chart where it is handled.  A construction that fails
    closure leaves *gaps*: unsatisfied obligations that represent potential
    semantic inconsistencies in the global section.

    §39.8 formalises the closure condition:

        closed(O) ⟺ ∀ o ∈ O, ∃ e ∈ Evidence : satisfies(e, o)

    where *satisfies* is defined coinductively over the descent data.  The
    models in this file represent the runtime witnesses of closure — checks,
    gaps, regression tests, and regression records — that are produced by
    the checking and regression-testing engines.

    A :class:`ClosureCheck` is the atomic unit: one check of one obligation
    in one patch.  A :class:`ClosureGap` describes an *unsatisfied* check
    together with its severity and any suggested remediation.

    A :class:`RegressionTest` pins a past closure check result as a baseline
    and verifies that subsequent construction runs do not weaken it.  A
    :class:`RegressionRecord` documents a detected weakening: an obligation
    that was formerly closed but is now open (or partially open).

    copilot: semantic-closure-models
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "ClosureResult",
    "CheckType",
    "GapSeverity",
    "RegressionStatus",
    "RegressionKind",
    # Dataclasses
    "ClosureCheck",
    "ClosureGap",
    "RegressionTest",
    "RegressionRecord",
    "SemanticClosure",
    # Constants
    "SEVERITY_ORDER",
    "CHECK_TYPES",
    "GAP_TYPES",
    # Factories
    "make_check",
    "make_gap",
    "empty_closure",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ClosureResult(str, Enum):
    """Possible outcomes of a single closure check.

    Corresponds to the three-valued result lattice of theory2.tex §39.8:

    * ``OPEN``    — no evidence found; obligation is unsatisfied.
    * ``PARTIAL`` — some evidence present but insufficient for full closure.
    * ``CLOSED``  — obligation is fully satisfied by available evidence.
    """

    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"


class CheckType(str, Enum):
    """The mode used to perform a :class:`ClosureCheck`.

    * ``SEMANTIC``  — evidence-based reasoning over content tags.
    * ``TREATY``    — evaluation against ratified :class:`OverlapTreaty` clauses.
    * ``DESCENT``   — structural check via a :class:`DescentResult`.
    * ``COMBINED``  — two or more of the above run together.
    """

    SEMANTIC = "semantic"
    TREATY = "treaty"
    DESCENT = "descent"
    COMBINED = "combined"


class GapSeverity(str, Enum):
    """Severity classification for a :class:`ClosureGap`.

    Severity determines scheduling priority in the repair frontier.
    """

    INFO = "info"
    MINOR = "minor"
    MODERATE = "moderate"
    CRITICAL = "critical"
    BLOCKING = "blocking"


class RegressionStatus(str, Enum):
    """Lifecycle status of a :class:`RegressionTest`."""

    UNKNOWN = "unknown"
    PASSING = "passing"
    FAILING = "failing"
    SKIPPED = "skipped"


class RegressionKind(str, Enum):
    """Classification of a detected regression.

    * ``SEMANTIC``   — a semantic obligation weakened or became unsatisfied.
    * ``SYNTACTIC``  — a structural/syntactic property degraded.
    * ``COVERAGE``   — evidence coverage dropped below threshold.
    """

    SEMANTIC = "semantic"
    SYNTACTIC = "syntactic"
    COVERAGE = "coverage"


# ---------------------------------------------------------------------------
# ClosureCheck
# ---------------------------------------------------------------------------


@dataclass
class ClosureCheck:
    """Atomic record of a single semantic closure check.

    Attributes
    ----------
    check_id:
        Unique identifier for this check instance.
    obligation_id:
        The obligation string that was checked.
    patch_id:
        Identifier of the coordinate patch in which the check was performed.
    result:
        One of ``"open"``, ``"partial"``, or ``"closed"``.
    confidence:
        Float in ``[0, 1]`` expressing the checker's confidence in *result*.
    evidence:
        Tuple of evidence tags that supported the result.
    check_type:
        The checking mode used (see :class:`CheckType`).
    timestamp:
        Unix epoch time at which the check was created.
    notes:
        Free-form annotation for debugging or audit purposes.
    """

    check_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    obligation_id: str = ""
    patch_id: str = ""
    result: str = ClosureResult.OPEN.value
    confidence: float = 0.0
    evidence: tuple[str, ...] = field(default_factory=tuple)
    check_type: str = CheckType.SEMANTIC.value
    timestamp: float = field(default_factory=time.time)
    notes: str = ""

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    def is_closed(self) -> bool:
        """Return ``True`` iff *result* is ``"closed"``."""
        return self.result == ClosureResult.CLOSED.value

    def is_open(self) -> bool:
        """Return ``True`` iff *result* is ``"open"``."""
        return self.result == ClosureResult.OPEN.value

    def is_partial(self) -> bool:
        """Return ``True`` iff *result* is ``"partial"``."""
        return self.result == ClosureResult.PARTIAL.value

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain :class:`dict`."""
        return {
            "check_id": self.check_id,
            "obligation_id": self.obligation_id,
            "patch_id": self.patch_id,
            "result": self.result,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "check_type": self.check_type,
            "timestamp": self.timestamp,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosureCheck:
        """Deserialise from a plain :class:`dict`."""
        return cls(
            check_id=data.get("check_id", uuid.uuid4().hex[:16]),
            obligation_id=data.get("obligation_id", ""),
            patch_id=data.get("patch_id", ""),
            result=data.get("result", ClosureResult.OPEN.value),
            confidence=float(data.get("confidence", 0.0)),
            evidence=tuple(data.get("evidence", [])),
            check_type=data.get("check_type", CheckType.SEMANTIC.value),
            timestamp=float(data.get("timestamp", time.time())),
            notes=data.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# ClosureGap
# ---------------------------------------------------------------------------


@dataclass
class ClosureGap:
    """Record of an unsatisfied closure obligation.

    A :class:`ClosureGap` is created whenever a :class:`ClosureCheck`
    returns a non-``"closed"`` result that exceeds the reporter's severity
    threshold.  Gaps feed the repair frontier so that construction loops
    can attempt to discharge them.

    Attributes
    ----------
    gap_id:
        Unique identifier for this gap.
    obligation_id:
        The obligation that was not closed.
    description:
        Human-readable description of why the obligation is open.
    severity:
        One of ``"info"``, ``"minor"``, ``"moderate"``, ``"critical"``,
        ``"blocking"`` (see :class:`GapSeverity`).
    patch_id:
        The coordinate patch in which the gap was detected.
    suggested_fix:
        Optional repair suggestion produced by a heuristic.
    timestamp:
        When the gap was recorded.
    source_check_id:
        The :attr:`ClosureCheck.check_id` that originated this gap.
    """

    gap_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    obligation_id: str = ""
    description: str = ""
    severity: str = GapSeverity.MINOR.value
    patch_id: str = ""
    suggested_fix: str = ""
    timestamp: float = field(default_factory=time.time)
    source_check_id: str = ""

    def is_blocking(self) -> bool:
        """Return ``True`` if this gap would block construction."""
        return self.severity in {GapSeverity.BLOCKING.value, GapSeverity.CRITICAL.value}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain :class:`dict`."""
        return {
            "gap_id": self.gap_id,
            "obligation_id": self.obligation_id,
            "description": self.description,
            "severity": self.severity,
            "patch_id": self.patch_id,
            "suggested_fix": self.suggested_fix,
            "timestamp": self.timestamp,
            "source_check_id": self.source_check_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosureGap:
        """Deserialise from a plain :class:`dict`."""
        return cls(
            gap_id=data.get("gap_id", uuid.uuid4().hex[:16]),
            obligation_id=data.get("obligation_id", ""),
            description=data.get("description", ""),
            severity=data.get("severity", GapSeverity.MINOR.value),
            patch_id=data.get("patch_id", ""),
            suggested_fix=data.get("suggested_fix", ""),
            timestamp=float(data.get("timestamp", time.time())),
            source_check_id=data.get("source_check_id", ""),
        )


# ---------------------------------------------------------------------------
# RegressionTest
# ---------------------------------------------------------------------------


@dataclass
class RegressionTest:
    """A pinned expectation that a closure check should remain passing.

    A :class:`RegressionTest` records the expected closure status for a
    given obligation at a specific baseline point in time, identified by
    *baseline_snapshot_id*.  On each run the test evaluates whether the
    current construction state still satisfies the same closure criteria.

    Attributes
    ----------
    test_id:
        Unique identifier.
    obligation_id:
        The obligation this test guards.
    baseline_snapshot_id:
        Identifier of the baseline snapshot against which the test was created.
    status:
        Current status: ``"unknown"``, ``"passing"``, ``"failing"``, or
        ``"skipped"``.
    last_run:
        Unix epoch timestamp of the most recent run.
    failure_reason:
        If *status* is ``"failing"``, a short description of the failure.
    notes:
        Free-form annotation.
    expected_result:
        The closure result expected by this test (default ``"closed"``).
    expected_confidence_min:
        The minimum confidence the check must return to be considered passing.
    tags:
        Optional set of tags for filtering.
    """

    test_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    obligation_id: str = ""
    baseline_snapshot_id: str = ""
    status: str = RegressionStatus.UNKNOWN.value
    last_run: float = 0.0
    failure_reason: str = ""
    notes: str = ""
    expected_result: str = ClosureResult.CLOSED.value
    expected_confidence_min: float = 0.5
    tags: frozenset[str] = field(default_factory=frozenset)

    def is_passing(self) -> bool:
        """Return ``True`` iff *status* is ``"passing"``."""
        return self.status == RegressionStatus.PASSING.value

    def is_failing(self) -> bool:
        """Return ``True`` iff *status* is ``"failing"``."""
        return self.status == RegressionStatus.FAILING.value

    def evaluate(self, check: ClosureCheck) -> bool:
        """Evaluate *check* against this test's expectations.

        Returns ``True`` if *check* meets or exceeds the expected result and
        minimum confidence threshold.

        Parameters
        ----------
        check:
            The :class:`ClosureCheck` to evaluate.
        """
        result_ok = check.result == self.expected_result
        confidence_ok = check.confidence >= self.expected_confidence_min
        return result_ok and confidence_ok

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain :class:`dict`."""
        return {
            "test_id": self.test_id,
            "obligation_id": self.obligation_id,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "status": self.status,
            "last_run": self.last_run,
            "failure_reason": self.failure_reason,
            "notes": self.notes,
            "expected_result": self.expected_result,
            "expected_confidence_min": self.expected_confidence_min,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionTest:
        """Deserialise from a plain :class:`dict`."""
        return cls(
            test_id=data.get("test_id", uuid.uuid4().hex[:16]),
            obligation_id=data.get("obligation_id", ""),
            baseline_snapshot_id=data.get("baseline_snapshot_id", ""),
            status=data.get("status", RegressionStatus.UNKNOWN.value),
            last_run=float(data.get("last_run", 0.0)),
            failure_reason=data.get("failure_reason", ""),
            notes=data.get("notes", ""),
            expected_result=data.get("expected_result", ClosureResult.CLOSED.value),
            expected_confidence_min=float(data.get("expected_confidence_min", 0.5)),
            tags=frozenset(data.get("tags", [])),
        )


# ---------------------------------------------------------------------------
# RegressionRecord
# ---------------------------------------------------------------------------


@dataclass
class RegressionRecord:
    """A detected regression: a property that was present but is now absent.

    A :class:`RegressionRecord` is produced by the :class:`RegressionDetector`
    when it finds that a key that was truthy in the baseline snapshot is now
    falsy or missing in the current snapshot, or that a closure check result
    has weakened from ``"closed"`` to ``"open"`` or ``"partial"``.

    Attributes
    ----------
    record_id:
        Unique identifier.
    key:
        The state key or obligation ID that regressed.
    baseline_value:
        The value of *key* in the baseline snapshot.
    current_value:
        The current value of *key* (may be ``None`` if missing).
    regression_type:
        Classification: ``"semantic"``, ``"syntactic"``, or ``"coverage"``.
    severity:
        One of ``"minor"``, ``"major"``, ``"critical"``.
    cause_analysis:
        Short explanation of the inferred cause.
    timestamp:
        When the regression was detected.
    patch_id:
        Optional patch context.
    """

    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    key: str = ""
    baseline_value: Any = None
    current_value: Any = None
    regression_type: str = RegressionKind.SEMANTIC.value
    severity: str = "minor"
    cause_analysis: str = ""
    timestamp: float = field(default_factory=time.time)
    patch_id: str = ""

    def is_critical(self) -> bool:
        """Return ``True`` iff *severity* is ``"critical"``."""
        return self.severity == "critical"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain :class:`dict`."""
        return {
            "record_id": self.record_id,
            "key": self.key,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "regression_type": self.regression_type,
            "severity": self.severity,
            "cause_analysis": self.cause_analysis,
            "timestamp": self.timestamp,
            "patch_id": self.patch_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionRecord:
        """Deserialise from a plain :class:`dict`."""
        return cls(
            record_id=data.get("record_id", uuid.uuid4().hex[:16]),
            key=data.get("key", ""),
            baseline_value=data.get("baseline_value"),
            current_value=data.get("current_value"),
            regression_type=data.get("regression_type", RegressionKind.SEMANTIC.value),
            severity=data.get("severity", "minor"),
            cause_analysis=data.get("cause_analysis", ""),
            timestamp=float(data.get("timestamp", time.time())),
            patch_id=data.get("patch_id", ""),
        )


# ---------------------------------------------------------------------------
# Constants derived from GapSeverity / CheckType / ClosureResult enums
# ---------------------------------------------------------------------------

#: Maps GapSeverity strings to numeric ranks (higher = more severe).
SEVERITY_ORDER: dict[str, int] = {
    GapSeverity.INFO.value: 0,
    GapSeverity.MINOR.value: 1,
    GapSeverity.MODERATE.value: 2,
    GapSeverity.CRITICAL.value: 3,
    GapSeverity.BLOCKING.value: 4,
}

#: All valid check_type strings (mirrors CheckType values).
CHECK_TYPES: list[str] = [ct.value for ct in CheckType]

#: All valid gap type strings recognised by the closure engine.
GAP_TYPES: list[str] = [sv.value for sv in GapSeverity]


# ---------------------------------------------------------------------------
# SemanticClosure — aggregate closure record
# ---------------------------------------------------------------------------


@dataclass
class SemanticClosure:
    """Aggregate closure record produced by the integration closure engine.

    Summarises the full outcome of one closure pass: which checks were run,
    which gaps remain, and the scalar ``fraction_closed`` in [0, 1] measuring
    how much of the integration scope has been successfully covered.

    Attributes
    ----------
    closure_id:
        Auto-generated unique identifier for this closure record.
    integration_id:
        Identifier of the integration being closed.
    patches:
        Tuple of patch identifiers covered by this closure.
    checks:
        All ClosureCheck instances produced during this pass.
    gaps:
        Residual ClosureGap instances that remain unresolved.
    fraction_closed:
        Scalar in [0, 1]; 1.0 means every obligation has been closed.
    timestamp:
        Wall-clock epoch time at which the closure was computed.
    metadata:
        Arbitrary key/value pairs for downstream consumers.
    """

    closure_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    integration_id: str = ""
    patches: tuple[str, ...] = field(default_factory=tuple)
    checks: list["ClosureCheck"] = field(default_factory=list)
    gaps: list["ClosureGap"] = field(default_factory=list)
    fraction_closed: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_fully_closed(self) -> bool:
        """Return True if fraction_closed >= 1.0 and no gaps remain."""
        return self.fraction_closed >= 1.0 and len(self.gaps) == 0

    def blocking_gaps(self) -> list["ClosureGap"]:
        """Return only blocking/critical gaps."""
        return [g for g in self.gaps if g.is_blocking()]

    def passed_checks(self) -> list["ClosureCheck"]:
        """Return checks with result == closed."""
        return [c for c in self.checks if c.is_closed()]

    def failed_checks(self) -> list["ClosureCheck"]:
        """Return checks with result != closed."""
        return [c for c in self.checks if not c.is_closed()]

    def average_confidence(self) -> float:
        """Mean confidence across all checks; 0.0 if no checks."""
        if not self.checks:
            return 0.0
        return sum(c.confidence for c in self.checks) / len(self.checks)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "closure_id": self.closure_id,
            "integration_id": self.integration_id,
            "patches": list(self.patches),
            "checks": [c.to_dict() for c in self.checks],
            "gaps": [g.to_dict() for g in self.gaps],
            "fraction_closed": self.fraction_closed,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticClosure":
        """Deserialise from a plain dict."""
        return cls(
            closure_id=data.get("closure_id", uuid.uuid4().hex[:16]),
            integration_id=data.get("integration_id", ""),
            patches=tuple(data.get("patches", [])),
            checks=[ClosureCheck.from_dict(c) for c in data.get("checks", [])],
            gaps=[ClosureGap.from_dict(g) for g in data.get("gaps", [])],
            fraction_closed=float(data.get("fraction_closed", 0.0)),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"SemanticClosure({self.closure_id[:8]}) "
            f"integration={self.integration_id!r} "
            f"patches={len(self.patches)} "
            f"fraction={self.fraction_closed:.1%} "
            f"gaps={len(self.gaps)} checks={len(self.checks)}"
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_check(
    obligation_id: str,
    patch_id: str = "",
    result: str = ClosureResult.CLOSED.value,
    confidence: float = 1.0,
    evidence: tuple[str, ...] = (),
    check_type: str = CheckType.SEMANTIC.value,
    notes: str = "",
) -> ClosureCheck:
    """Create a ClosureCheck with an auto-generated ID and current timestamp.

    Convenience constructor that fills in ``check_id`` and ``timestamp``
    automatically so callers do not have to import :mod:`uuid` or :mod:`time`.

    Args:
        obligation_id: The obligation that was checked.
        patch_id:      The coordinate patch the check applies to.
        result:        One of "open", "partial", or "closed".
        confidence:    Checker confidence in result in [0, 1].
        evidence:      Evidence item IDs supporting the verdict.
        check_type:    The checking mode (default "semantic").
        notes:         Optional free-form annotation.

    Returns:
        A new ClosureCheck instance.
    """
    return ClosureCheck(
        obligation_id=obligation_id,
        patch_id=patch_id,
        result=result,
        confidence=confidence,
        evidence=evidence,
        check_type=check_type,
        notes=notes,
    )


def make_gap(
    obligation_id: str,
    severity: str = GapSeverity.MINOR.value,
    description: str = "",
    patch_id: str = "",
    suggested_fix: str = "",
    source_check_id: str = "",
) -> ClosureGap:
    """Create a ClosureGap with an auto-generated ID and current timestamp.

    Convenience constructor that fills in ``gap_id`` and ``timestamp``
    automatically.

    Args:
        obligation_id:   The open obligation this gap documents.
        severity:        One of the GapSeverity string values.
        description:     Human-readable description of why the gap exists.
        patch_id:        Coordinate patch where the gap was detected.
        suggested_fix:   Optional repair hint.
        source_check_id: ID of the ClosureCheck that found the gap.

    Returns:
        A new ClosureGap instance.
    """
    return ClosureGap(
        obligation_id=obligation_id,
        description=description,
        severity=severity,
        patch_id=patch_id,
        suggested_fix=suggested_fix,
        source_check_id=source_check_id,
    )


def empty_closure(
    integration_id: str,
    patches: list[str] | None = None,
) -> SemanticClosure:
    """Create an empty SemanticClosure for the given integration.

    The returned record has no checks and no gaps, with ``fraction_closed``
    initialised to 0.0.  Use the integration closure engine to populate it.

    Args:
        integration_id: Identifier of the integration being closed.
        patches:        Optional list of patch identifiers in scope.

    Returns:
        A new SemanticClosure with no checks and no gaps.
    """
    return SemanticClosure(
        integration_id=integration_id,
        patches=tuple(patches or []),
        checks=[],
        gaps=[],
        fraction_closed=0.0,
    )


# ---------------------------------------------------------------------------
# SemanticClosure
# ---------------------------------------------------------------------------


@dataclass
class SemanticClosure:
    """Top-level result object summarising the closure state of an integration.

    A :class:`SemanticClosure` is the final output of the
    :class:`~jugeo.generation.semantic_closure.integration.SemanticClosurePipeline`.
    It bundles the closure fraction, the full :class:`ClosureReport`, a history
    of per-round fractions, and the lists of closed/open obligation IDs.

    Attributes
    ----------
    integration_id:
        Identifier of the integration run that produced this closure.
    fraction_closed:
        Float in ``[0, 1]``.  1.0 means all obligations are closed.
    report:
        The :class:`~jugeo.generation.semantic_closure.closure_checking.ClosureReport`
        from the final round.  May be ``None`` when used as a placeholder.
    fractions:
        List of closure fractions recorded at the end of each round.
    closed_obligations:
        Tuple of obligation IDs that are closed.
    open_obligations:
        Tuple of obligation IDs that remain open.
    notes:
        Free-form annotation.
    """

    integration_id: str = ""
    fraction_closed: float = 0.0
    report: Any = None
    fractions: list[float] = field(default_factory=list)
    closed_obligations: tuple[str, ...] = field(default_factory=tuple)
    open_obligations: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def is_fully_closed(self) -> bool:
        """Return ``True`` iff all obligations are closed."""
        return self.fraction_closed >= 1.0

    def has_open_obligations(self) -> bool:
        """Return ``True`` iff at least one obligation remains open."""
        return len(self.open_obligations) > 0 or self.fraction_closed < 1.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain :class:`dict`."""
        report_dict: Any = None
        try:
            report_dict = self.report.to_dict() if self.report is not None else None
        except Exception:
            report_dict = str(self.report) if self.report is not None else None
        return {
            "integration_id": self.integration_id,
            "fraction_closed": self.fraction_closed,
            "report": report_dict,
            "fractions": list(self.fractions),
            "closed_obligations": list(self.closed_obligations),
            "open_obligations": list(self.open_obligations),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticClosure":
        """Deserialise from a plain :class:`dict`."""
        return cls(
            integration_id=data.get("integration_id", ""),
            fraction_closed=float(data.get("fraction_closed", 0.0)),
            report=data.get("report"),
            fractions=list(data.get("fractions", [])),
            closed_obligations=tuple(data.get("closed_obligations", [])),
            open_obligations=tuple(data.get("open_obligations", [])),
            notes=data.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_check(
    check_id: str = "",
    obligation_id: str = "",
    patch_id: str = "",
    result: str = "open",
    confidence: float = 0.0,
    evidence: tuple[str, ...] = (),
    check_type: str = "semantic",
    notes: str = "",
) -> ClosureCheck:
    """Construct a :class:`ClosureCheck` with sensible defaults.

    Parameters
    ----------
    check_id:
        If empty, a random UUID hex is assigned.
    obligation_id:
        The obligation being checked.
    patch_id:
        The patch context.
    result:
        ``"open"``, ``"partial"``, or ``"closed"``.
    confidence:
        Float in ``[0, 1]``.
    evidence:
        Tuple of evidence tag strings.
    check_type:
        ``"semantic"``, ``"treaty"``, ``"descent"``, or ``"combined"``.
    notes:
        Free-form annotation.
    """
    return ClosureCheck(
        check_id=check_id or uuid.uuid4().hex[:16],
        obligation_id=obligation_id,
        patch_id=patch_id,
        result=result,
        confidence=confidence,
        evidence=evidence,
        check_type=check_type,
        notes=notes,
    )


def make_gap(
    gap_id: str = "",
    obligation_id: str = "",
    description: str = "",
    severity: str = "minor",
    patch_id: str = "",
    suggested_fix: str = "",
    source_check_id: str = "",
) -> ClosureGap:
    """Construct a :class:`ClosureGap` with sensible defaults.

    Parameters
    ----------
    gap_id:
        If empty, a random UUID hex is assigned.
    obligation_id:
        The obligation that is not yet closed.
    description:
        Human-readable description of the gap.
    severity:
        One of ``"info"``, ``"minor"``, ``"moderate"``, ``"critical"``,
        ``"blocking"``.
    patch_id:
        The patch in which the gap was detected.
    suggested_fix:
        Optional repair suggestion.
    source_check_id:
        The :attr:`ClosureCheck.check_id` that produced this gap.
    """
    return ClosureGap(
        gap_id=gap_id or uuid.uuid4().hex[:16],
        obligation_id=obligation_id,
        description=description,
        severity=severity,
        patch_id=patch_id,
        suggested_fix=suggested_fix,
        source_check_id=source_check_id,
    )


def empty_closure(
    integration_id: str = "",
    fraction_closed: float = 0.0,
    report: Any = None,
    fractions: "list[float] | None" = None,
) -> SemanticClosure:
    """Return an empty (or minimally-populated) :class:`SemanticClosure`.

    Useful as a neutral starting point or sentinel value.

    Parameters
    ----------
    integration_id:
        Optional identifier for the integration run.
    fraction_closed:
        Initial fraction closed (default ``0.0``).
    report:
        Optional report to attach.
    fractions:
        Optional list of historical fractions.
    """
    return SemanticClosure(
        integration_id=integration_id,
        fraction_closed=fraction_closed,
        report=report,
        fractions=fractions if fractions is not None else [],
    )


# ---------------------------------------------------------------------------
# Constants derived from GapSeverity / CheckType / ClosureResult enums
# ---------------------------------------------------------------------------

#: Maps GapSeverity strings to numeric ranks (higher = more severe).
SEVERITY_ORDER: dict[str, int] = {
    GapSeverity.INFO.value: 0,
    GapSeverity.MINOR.value: 1,
    GapSeverity.MODERATE.value: 2,
    GapSeverity.CRITICAL.value: 3,
    GapSeverity.BLOCKING.value: 4,
}

#: All valid check_type strings (mirrors CheckType values).
CHECK_TYPES: list[str] = [ct.value for ct in CheckType]

#: All valid gap type strings recognised by the closure engine.
GAP_TYPES: list[str] = [sv.value for sv in GapSeverity]
