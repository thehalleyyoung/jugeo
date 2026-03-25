r"""Shared dataclass models for the ``jugeo.se_theory.testing`` package.

Theory (JuGeo — "Testing as Witness Construction", B3):
    In the judgment-geometry framework a *test* is a local evidence section
    at a coordinate.  A test suite is adequate when local witnesses glue into
    a global certificate — i.e. the descent condition is satisfied across
    all overlaps of the covering.

    Key theoretical correspondences:
    * TestObligation  ↔ local proposition to be witnessed at a coordinate
    * TestResult      ↔ observed evidence record produced by running the test
    * WitnessSection  ↔ local section of the evidence sheaf at a coordinate
    * CoverageReport  ↔ global view of which local sections exist
    * RegressionScope ↔ minimal invalidation set after a morphism changes

    The trust levels track the strength of evidence:
        CLAIM < CONJECTURE < HEURISTIC < PROOF < VERIFIED

    copilot: se-theory-testing-models
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "TestLevel",
    "ObligationStatus",
    # Dataclasses
    "TestObligation",
    "TestResult",
    "WitnessSection",
    "CoverageReport",
    "RegressionScope",
    "TestPrioritization",
    "TestSuiteReport",
    # Factories
    "make_obligation",
    "make_result",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestLevel(str, Enum):
    """Architectural level at which a test obligation lives.

    Corresponds to the layers of the covering hierarchy:
    * ``UNIT``        — single function/class coordinate
    * ``INTEGRATION`` — interaction between two or more coordinates
    * ``PACKAGE``     — intra-package module group
    * ``SYSTEM``      — cross-package or cross-service boundary
    * ``ACCEPTANCE``  — end-to-end user-visible behaviour
    """

    UNIT = "unit"
    INTEGRATION = "integration"
    PACKAGE = "package"
    SYSTEM = "system"
    ACCEPTANCE = "acceptance"


class ObligationStatus(str, Enum):
    """Lifecycle status of a single test obligation.

    * ``PENDING``   — not yet exercised
    * ``SATISFIED`` — evidence exists and is fresh
    * ``FAILED``    — test ran but proposition was falsified
    * ``SKIPPED``   — deliberately deferred (e.g. flaky, out of scope)
    * ``STALE``     — previously satisfied but the coordinate has since changed
    """

    PENDING = "pending"
    SATISFIED = "satisfied"
    FAILED = "failed"
    SKIPPED = "skipped"
    STALE = "stale"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TestObligation:
    """A single proposition that must be witnessed at a coordinate.

    Attributes
    ----------
    id:
        Unique identifier for this obligation.
    coordinate_id:
        The chart / module coordinate this obligation lives at.
    proposition:
        Human-readable statement of what must hold.
    level:
        Architectural level of the test (unit, integration, …).
    overlap_ids:
        List of overlap (intersection) IDs this obligation covers.
        An empty list means a purely local obligation.
    priority:
        Floating-point score; higher means more urgent.
    generated_from:
        Source identifier — either a cover_id or a change_id.
    status:
        Current lifecycle status.
    trust_target:
        Desired trust level once the obligation is satisfied (e.g. "proof").
    created_at:
        Unix timestamp of creation.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate_id: str = ""
    proposition: str = ""
    level: TestLevel = TestLevel.UNIT
    overlap_ids: list[str] = field(default_factory=list)
    priority: float = 0.5
    generated_from: str = ""
    status: ObligationStatus = ObligationStatus.PENDING
    trust_target: str = "proof"
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "level": self.level.value,
            "overlap_ids": list(self.overlap_ids),
            "priority": self.priority,
            "generated_from": self.generated_from,
            "status": self.status.value,
            "trust_target": self.trust_target,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestObligation:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            coordinate_id=data.get("coordinate_id", ""),
            proposition=data.get("proposition", ""),
            level=TestLevel(data.get("level", "unit")),
            overlap_ids=list(data.get("overlap_ids", [])),
            priority=float(data.get("priority", 0.5)),
            generated_from=data.get("generated_from", ""),
            status=ObligationStatus(data.get("status", "pending")),
            trust_target=data.get("trust_target", "proof"),
            created_at=float(data.get("created_at", time.time())),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TestObligation(id={self.id!r}, coord={self.coordinate_id!r}, "
            f"level={self.level.value}, status={self.status.value})"
        )


@dataclass
class TestResult:
    """Observed evidence produced by exercising a single TestObligation.

    Attributes
    ----------
    id:
        Unique result identifier.
    obligation_id:
        The obligation this result satisfies (or fails).
    coordinate_id:
        The coordinate at which the test ran.
    channel:
        Execution channel: "pytest", "hypothesis", "manual", etc.
    trust_achieved:
        Actual trust level attained by the evidence.
    passed:
        True iff the proposition was witnessed successfully.
    duration_ms:
        Wall-clock time consumed by the test run (milliseconds).
    evidence_id:
        Optional reference to a stored evidence record.
    failure_detail:
        Optional human-readable explanation when ``passed`` is False.
    timestamp:
        Unix timestamp of when the result was recorded.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    obligation_id: str = ""
    coordinate_id: str = ""
    channel: str = "pytest"
    trust_achieved: str = "heuristic"
    passed: bool = False
    duration_ms: float = 0.0
    evidence_id: Optional[str] = None
    failure_detail: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "obligation_id": self.obligation_id,
            "coordinate_id": self.coordinate_id,
            "channel": self.channel,
            "trust_achieved": self.trust_achieved,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "evidence_id": self.evidence_id,
            "failure_detail": self.failure_detail,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:16]),
            obligation_id=data.get("obligation_id", ""),
            coordinate_id=data.get("coordinate_id", ""),
            channel=data.get("channel", "pytest"),
            trust_achieved=data.get("trust_achieved", "heuristic"),
            passed=bool(data.get("passed", False)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            evidence_id=data.get("evidence_id"),
            failure_detail=data.get("failure_detail"),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class WitnessSection:
    """A local section of the evidence sheaf at one coordinate.

    In sheaf-theoretic terms this is the *local data* — a collection of
    evidence records whose restrictions to overlapping coordinates must agree
    for the global section (adequate test suite) to exist.

    Attributes
    ----------
    coordinate_id:
        The chart this section lives at.
    proposition:
        The primary proposition being witnessed.
    evidence_records:
        Raw evidence dicts (channel, trust_level, timestamp, …).
    trust_level:
        Highest trust level achieved across all evidence records.
    is_complete:
        True when every required proposition has at least one record.
    staleness_days:
        How many calendar days since the evidence was last valid.
    """

    coordinate_id: str = ""
    proposition: str = ""
    evidence_records: list[dict[str, Any]] = field(default_factory=list)
    trust_level: str = "none"
    is_complete: bool = False
    staleness_days: float = 0.0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "evidence_records": [dict(r) for r in self.evidence_records],
            "trust_level": self.trust_level,
            "is_complete": self.is_complete,
            "staleness_days": self.staleness_days,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WitnessSection:
        return cls(
            coordinate_id=data.get("coordinate_id", ""),
            proposition=data.get("proposition", ""),
            evidence_records=list(data.get("evidence_records", [])),
            trust_level=data.get("trust_level", "none"),
            is_complete=bool(data.get("is_complete", False)),
            staleness_days=float(data.get("staleness_days", 0.0)),
        )


@dataclass
class CoverageReport:
    """Global view of geometric (sheaf-theoretic) coverage across a site.

    Attributes
    ----------
    site_id:
        Identifier for the site/project being analysed.
    total_coordinates:
        Total number of coordinate charts in the covering.
    covered_coordinates:
        Number of charts that have at least one fresh witness.
    uncovered_coordinates:
        List of coordinate IDs lacking evidence.
    total_overlaps:
        Total number of non-trivial intersections in the cover.
    tested_overlaps:
        Number of overlaps that have at least one interface test.
    untested_overlaps:
        List of overlap IDs lacking a test.
    geometric_coverage:
        ``covered_coordinates / total_coordinates`` (0-1).
    overlap_coverage:
        ``tested_overlaps / total_overlaps`` (0-1).
    trust_distribution:
        Map from trust-level name to count of coordinates at that level.
    stale_evidence_count:
        Number of coordinates whose evidence has become stale.
    computed_at:
        ISO-8601 timestamp of when this report was computed.
    """

    site_id: str = ""
    total_coordinates: int = 0
    covered_coordinates: int = 0
    uncovered_coordinates: list[str] = field(default_factory=list)
    total_overlaps: int = 0
    tested_overlaps: int = 0
    untested_overlaps: list[str] = field(default_factory=list)
    geometric_coverage: float = 0.0
    overlap_coverage: float = 0.0
    trust_distribution: dict[str, int] = field(default_factory=dict)
    stale_evidence_count: int = 0
    computed_at: str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "total_coordinates": self.total_coordinates,
            "covered_coordinates": self.covered_coordinates,
            "uncovered_coordinates": list(self.uncovered_coordinates),
            "total_overlaps": self.total_overlaps,
            "tested_overlaps": self.tested_overlaps,
            "untested_overlaps": list(self.untested_overlaps),
            "geometric_coverage": self.geometric_coverage,
            "overlap_coverage": self.overlap_coverage,
            "trust_distribution": dict(self.trust_distribution),
            "stale_evidence_count": self.stale_evidence_count,
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageReport:
        return cls(
            site_id=data.get("site_id", ""),
            total_coordinates=int(data.get("total_coordinates", 0)),
            covered_coordinates=int(data.get("covered_coordinates", 0)),
            uncovered_coordinates=list(data.get("uncovered_coordinates", [])),
            total_overlaps=int(data.get("total_overlaps", 0)),
            tested_overlaps=int(data.get("tested_overlaps", 0)),
            untested_overlaps=list(data.get("untested_overlaps", [])),
            geometric_coverage=float(data.get("geometric_coverage", 0.0)),
            overlap_coverage=float(data.get("overlap_coverage", 0.0)),
            trust_distribution=dict(data.get("trust_distribution", {})),
            stale_evidence_count=int(data.get("stale_evidence_count", 0)),
            computed_at=data.get("computed_at", ""),
        )


@dataclass
class RegressionScope:
    """Minimal set of tests that must re-run after a set of coordinate changes.

    Computed from the invalidation graph:  for every changed coordinate c,
    any overlap containing c is invalidated, and any test that covers an
    invalidated overlap is added to ``required_retests``.

    Attributes
    ----------
    change_id:
        Identifier for the change-set that triggered this scope computation.
    changed_coordinates:
        Coordinates directly modified by the change.
    invalidated_overlaps:
        Overlaps whose evidence is no longer valid.
    required_retests:
        Minimal list of TestObligation instances that must re-run.
    estimated_cost:
        Rough estimate of total test duration (sum of historical durations).
    """

    change_id: str = ""
    changed_coordinates: list[str] = field(default_factory=list)
    invalidated_overlaps: list[str] = field(default_factory=list)
    required_retests: list[TestObligation] = field(default_factory=list)
    estimated_cost: float = 0.0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "changed_coordinates": list(self.changed_coordinates),
            "invalidated_overlaps": list(self.invalidated_overlaps),
            "required_retests": [o.to_dict() for o in self.required_retests],
            "estimated_cost": self.estimated_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionScope:
        return cls(
            change_id=data.get("change_id", ""),
            changed_coordinates=list(data.get("changed_coordinates", [])),
            invalidated_overlaps=list(data.get("invalidated_overlaps", [])),
            required_retests=[
                TestObligation.from_dict(o)
                for o in data.get("required_retests", [])
            ],
            estimated_cost=float(data.get("estimated_cost", 0.0)),
        )


@dataclass
class TestPrioritization:
    """Priority record for a single test obligation.

    Attributes
    ----------
    obligation_id:
        The obligation being ranked.
    score:
        Overall priority score (higher = run sooner).
    reasons:
        Human-readable list of factors contributing to the score.
        E.g. ``["high-coupling overlap", "trust deficit", "critical path"]``.
    """

    obligation_id: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "score": self.score,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPrioritization:
        return cls(
            obligation_id=data.get("obligation_id", ""),
            score=float(data.get("score", 0.0)),
            reasons=list(data.get("reasons", [])),
        )


@dataclass
class TestSuiteReport:
    """Summary statistics for an entire test suite run.

    Attributes
    ----------
    suite_id:
        Identifier for the suite run.
    total_obligations:
        Total obligations in the suite.
    satisfied:
        Number of obligations with status SATISFIED.
    failed:
        Number of obligations with status FAILED.
    skipped:
        Number of obligations with status SKIPPED.
    stale:
        Number of obligations with status STALE.
    geometric_coverage:
        Fraction of coordinates covered by at least one passing test.
    trust_floor:
        Minimum trust level achieved across all satisfied obligations.
    pass_rate:
        ``satisfied / total_obligations`` (0-1).
    timestamp:
        ISO-8601 timestamp of when the suite ran.
    """

    suite_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    total_obligations: int = 0
    satisfied: int = 0
    failed: int = 0
    skipped: int = 0
    stale: int = 0
    geometric_coverage: float = 0.0
    trust_floor: str = "none"
    pass_rate: float = 0.0
    timestamp: str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "total_obligations": self.total_obligations,
            "satisfied": self.satisfied,
            "failed": self.failed,
            "skipped": self.skipped,
            "stale": self.stale,
            "geometric_coverage": self.geometric_coverage,
            "trust_floor": self.trust_floor,
            "pass_rate": self.pass_rate,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestSuiteReport:
        return cls(
            suite_id=data.get("suite_id", uuid.uuid4().hex[:12]),
            total_obligations=int(data.get("total_obligations", 0)),
            satisfied=int(data.get("satisfied", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            stale=int(data.get("stale", 0)),
            geometric_coverage=float(data.get("geometric_coverage", 0.0)),
            trust_floor=data.get("trust_floor", "none"),
            pass_rate=float(data.get("pass_rate", 0.0)),
            timestamp=data.get("timestamp", ""),
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_obligation(
    coordinate_id: str,
    proposition: str,
    level: TestLevel = TestLevel.UNIT,
    overlap_ids: Optional[list[str]] = None,
    priority: float = 0.5,
    generated_from: str = "",
    trust_target: str = "proof",
) -> TestObligation:
    """Convenience constructor for a TestObligation."""
    return TestObligation(
        coordinate_id=coordinate_id,
        proposition=proposition,
        level=level,
        overlap_ids=overlap_ids or [],
        priority=priority,
        generated_from=generated_from,
        trust_target=trust_target,
    )


def make_result(
    obligation_id: str,
    coordinate_id: str,
    passed: bool,
    trust_achieved: str = "heuristic",
    channel: str = "pytest",
    duration_ms: float = 0.0,
    failure_detail: Optional[str] = None,
) -> TestResult:
    """Convenience constructor for a TestResult."""
    return TestResult(
        obligation_id=obligation_id,
        coordinate_id=coordinate_id,
        passed=passed,
        trust_achieved=trust_achieved,
        channel=channel,
        duration_ms=duration_ms,
        failure_detail=failure_detail,
    )
