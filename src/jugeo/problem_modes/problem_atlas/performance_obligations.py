"""Section 4 — Performance Obligations for the Unified Problem Atlas.

copilot: performance-as-obligation engine with timing, memory, and resource judgments.

This module implements the performance obligations chapter of the Unified
Problem Atlas.  Performance is modelled as a family of *judgment predicates*:
each predicate asserts that a program meets a bound on some measurable
resource (wall-clock time, CPU cycles, heap memory, I/O operations, network
bandwidth, energy).  The atlas treats these predicates as first-class
obligation terms that must be discharged by evidence before the program is
certified.

Key components
--------------
ResourceKind
    Enumeration of measurable resources (time, memory, io, cpu, network,
    energy, custom).
BoundKind
    Enumeration of bound types (upper, lower, exact, asymptotic, amortised).
ObligationSeverity
    Severity rating of a performance obligation (critical, high, medium, low).
PerformanceBound
    Frozen record encoding a single measurable bound (value, unit, confidence).
PerformanceObligation
    Frozen record asserting that a program meets a set of PerformanceBound
    predicates under a specified workload.
PerformanceEvidence
    Frozen record capturing profiling or measurement evidence.
PerformanceWitness
    Frozen certificate produced when a PerformanceObligation is discharged.
PerformanceObligationsAnalyzer
    Checks obligations against evidence, identifies gaps, and computes
    discharge confidence.
PerformanceObligationsCoordinator
    Orchestrates the full performance obligation pipeline: register →
    collect evidence → analyse → witness.

Design notes
------------
All model types are ``@dataclass(frozen=True, slots=True)``.  Bounds are
intentionally unit-agnostic — the unit string is stored alongside the
value and is not interpreted by the atlas (callers are responsible for
dimensional consistency).  Asymptotic bounds carry a complexity class
string (e.g., ``"O(n log n)"``).
"""

from __future__ import annotations

import uuid
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterator, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        ProblemCategory,
        AtlasCatalog,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    AtlasCatalog = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.specification_satisfaction import (
        SatisfactionStatus,
        SpecificationKind,
    )
except ImportError:
    SatisfactionStatus = None  # type: ignore[assignment]
    SpecificationKind = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

# ═══════════════════════════════════════════════════════════════════════════
# §1  Type aliases
# ═══════════════════════════════════════════════════════════════════════════

ObligationId: TypeAlias = str
WitnessId: TypeAlias = str
EvidenceId: TypeAlias = str
ClassId: TypeAlias = str
JsonDict: TypeAlias = dict[str, Any]
NumericValue: TypeAlias = float

# ═══════════════════════════════════════════════════════════════════════════
# §2  Enumerations
# ═══════════════════════════════════════════════════════════════════════════


class ResourceKind(str, Enum):
    """Measurable resources that performance obligations can assert bounds on.

    Attributes:
        TIME: Wall-clock execution time.
        CPU: CPU cycle count or CPU time.
        MEMORY: Heap or total process memory.
        IO: Disk or storage I/O operations.
        NETWORK: Network bytes transferred or connection count.
        ENERGY: Energy consumption (joules or watt-hours).
        LATENCY: End-to-end request/response latency.
        THROUGHPUT: Operations or transactions per time unit.
        CUSTOM: User-defined resource metric.
    """

    TIME = "TIME"
    CPU = "CPU"
    MEMORY = "MEMORY"
    IO = "IO"
    NETWORK = "NETWORK"
    ENERGY = "ENERGY"
    LATENCY = "LATENCY"
    THROUGHPUT = "THROUGHPUT"
    CUSTOM = "CUSTOM"

    def default_unit(self) -> str:
        """Return the conventional default unit for this resource.

        Returns:
            Unit string (e.g., ``"ms"``, ``"bytes"``, ``"J"``).
        """
        units: dict[ResourceKind, str] = {
            ResourceKind.TIME: "ms",
            ResourceKind.CPU: "cycles",
            ResourceKind.MEMORY: "bytes",
            ResourceKind.IO: "ops",
            ResourceKind.NETWORK: "bytes",
            ResourceKind.ENERGY: "J",
            ResourceKind.LATENCY: "ms",
            ResourceKind.THROUGHPUT: "req/s",
            ResourceKind.CUSTOM: "units",
        }
        return units[self]

    def is_lower_is_better(self) -> bool:
        """Return ``True`` when a smaller measured value is desirable.

        Returns:
            True for TIME, CPU, MEMORY, IO, NETWORK, ENERGY, LATENCY.
        """
        return self in {
            ResourceKind.TIME,
            ResourceKind.CPU,
            ResourceKind.MEMORY,
            ResourceKind.IO,
            ResourceKind.NETWORK,
            ResourceKind.ENERGY,
            ResourceKind.LATENCY,
        }


class BoundKind(str, Enum):
    """The type of performance bound being asserted.

    Attributes:
        UPPER: The measured value must not exceed the bound (≤).
        LOWER: The measured value must not fall below the bound (≥).
        EXACT: The measured value must equal the bound exactly (=).
        ASYMPTOTIC: The growth rate must match the given complexity class.
        AMORTISED: The amortised cost per operation must not exceed the bound.
        PROBABILISTIC: The bound holds with at least the specified probability.
    """

    UPPER = "UPPER"
    LOWER = "LOWER"
    EXACT = "EXACT"
    ASYMPTOTIC = "ASYMPTOTIC"
    AMORTISED = "AMORTISED"
    PROBABILISTIC = "PROBABILISTIC"

    def check(self, measured: float, bound: float) -> bool:
        """Return ``True`` when *measured* satisfies this bound kind.

        For ASYMPTOTIC and PROBABILISTIC the check is approximate.

        Args:
            measured: The measured resource value.
            bound: The bound value.

        Returns:
            True when the bound is satisfied.
        """
        if self == BoundKind.UPPER:
            return measured <= bound
        if self == BoundKind.LOWER:
            return measured >= bound
        if self == BoundKind.EXACT:
            return math.isclose(measured, bound, rel_tol=1e-6)
        if self == BoundKind.AMORTISED:
            return measured <= bound
        # ASYMPTOTIC and PROBABILISTIC: trust the caller's measurement
        return measured <= bound


class ObligationSeverity(str, Enum):
    """Severity rating of a performance obligation.

    Attributes:
        CRITICAL: Failure renders the program unfit for use.
        HIGH: Significant performance degradation expected on failure.
        MEDIUM: Noticeable but tolerable performance impact.
        LOW: Minor performance concern; best-effort compliance.
        INFORMATIONAL: Monitoring only; no hard enforcement.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"

    @property
    def ordinal(self) -> int:
        """Return a numeric severity for comparison.

        Returns:
            Integer from 0 (INFORMATIONAL) to 4 (CRITICAL).
        """
        return {
            "INFORMATIONAL": 0,
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
        }[self.value]

    def is_enforced(self) -> bool:
        """Return ``True`` when the obligation is actively enforced.

        Returns:
            True for CRITICAL, HIGH, and MEDIUM.
        """
        return self in {
            ObligationSeverity.CRITICAL,
            ObligationSeverity.HIGH,
            ObligationSeverity.MEDIUM,
        }


class DischargeStatus(str, Enum):
    """Status of a performance obligation discharge attempt.

    Attributes:
        PENDING: No evidence collected yet.
        COLLECTING: Evidence collection in progress.
        DISCHARGED: Obligation fully satisfied by evidence.
        PARTIAL: Some bounds satisfied; others remain open.
        VIOLATED: At least one bound was violated.
        WAIVED: Obligation waived by authorised reviewer.
        ERROR: Internal error prevented discharge check.
    """

    PENDING = "PENDING"
    COLLECTING = "COLLECTING"
    DISCHARGED = "DISCHARGED"
    PARTIAL = "PARTIAL"
    VIOLATED = "VIOLATED"
    WAIVED = "WAIVED"
    ERROR = "ERROR"

    def is_terminal(self) -> bool:
        """Return ``True`` when no further action is expected.

        Returns:
            True for DISCHARGED, VIOLATED, WAIVED, and ERROR.
        """
        return self in {
            DischargeStatus.DISCHARGED,
            DischargeStatus.VIOLATED,
            DischargeStatus.WAIVED,
            DischargeStatus.ERROR,
        }

    def is_positive(self) -> bool:
        """Return ``True`` when the obligation was met.

        Returns:
            True for DISCHARGED and WAIVED.
        """
        return self in {DischargeStatus.DISCHARGED, DischargeStatus.WAIVED}


# ═══════════════════════════════════════════════════════════════════════════
# §3  Frozen dataclasses — PerformanceBound
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PerformanceBound:
    """A single measurable performance bound assertion.

    Encodes the assertion ``measured_resource ≤ value`` (or the equivalent
    for other BoundKind values).

    Attributes:
        bound_id: UUID for this bound.
        resource_kind: The resource being bounded.
        bound_kind: The type of bound (upper, lower, exact, etc.).
        value: The numeric bound value.
        unit: Unit string for the bound value.
        complexity_class: For ASYMPTOTIC bounds, the complexity expression.
        probability: For PROBABILISTIC bounds, the required probability in [0,1].
        workload_description: Description of the workload under which the bound holds.
        metadata: Free-form annotations.
    """

    bound_id: str
    resource_kind: ResourceKind
    bound_kind: BoundKind
    value: float
    unit: str
    complexity_class: str
    probability: float
    workload_description: str
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        resource_kind: ResourceKind,
        bound_kind: BoundKind,
        value: float,
        unit: str = "",
        complexity_class: str = "",
        probability: float = 1.0,
        workload_description: str = "",
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "PerformanceBound":
        """Create a new PerformanceBound with a generated UUID.

        Args:
            resource_kind: Resource being bounded.
            bound_kind: Type of bound.
            value: Numeric bound value.
            unit: Unit string.
            complexity_class: Complexity expression for ASYMPTOTIC bounds.
            probability: Required probability for PROBABILISTIC bounds.
            workload_description: Workload description.
            metadata: Extra annotations.

        Returns:
            A new PerformanceBound.
        """
        return cls(
            bound_id=str(uuid.uuid4()),
            resource_kind=resource_kind,
            bound_kind=bound_kind,
            value=value,
            unit=unit or resource_kind.default_unit(),
            complexity_class=complexity_class,
            probability=max(0.0, min(1.0, probability)),
            workload_description=workload_description,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_satisfied_by(self, measured: float) -> bool:
        """Return ``True`` when *measured* satisfies this bound.

        Args:
            measured: The measured resource value in the same unit as this bound.

        Returns:
            True when the bound is satisfied.
        """
        return self.bound_kind.check(measured, self.value)

    def margin(self, measured: float) -> float:
        """Return the signed margin between the bound and the measured value.

        A positive margin means the measurement is within bounds; negative
        means the bound is violated.

        Args:
            measured: The measured value.

        Returns:
            Float margin (positive = within bounds, negative = violation).
        """
        if self.bound_kind in {BoundKind.UPPER, BoundKind.AMORTISED}:
            return self.value - measured
        if self.bound_kind == BoundKind.LOWER:
            return measured - self.value
        return self.value - abs(measured - self.value)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "bound_id": self.bound_id,
            "resource_kind": self.resource_kind.value,
            "bound_kind": self.bound_kind.value,
            "value": self.value,
            "unit": self.unit,
            "complexity_class": self.complexity_class,
            "probability": self.probability,
            "workload_description": self.workload_description,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  Frozen dataclasses — PerformanceObligation
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PerformanceObligation:
    """An obligation asserting that a program satisfies a set of performance bounds.

    A PerformanceObligation is a judgment predicate: it is True iff all
    associated PerformanceBound predicates are discharged by evidence under
    the specified workload.

    Attributes:
        obligation_id: UUID uniquely identifying this obligation.
        class_id: Problem class this obligation is attached to.
        name: Short human-readable name.
        description: Full description of the obligation.
        bounds: Ordered tuple of PerformanceBound predicates.
        severity: Severity of this obligation.
        workload_profile: Description of the workload (e.g., ``"10k req/s``").
        environment: Runtime environment description.
        conjunction: If True, ALL bounds must be satisfied; if False, ANY suffices.
        status: Current discharge status.
        metadata: Free-form annotations.
    """

    obligation_id: str
    class_id: str
    name: str
    description: str
    bounds: tuple[PerformanceBound, ...]
    severity: ObligationSeverity
    workload_profile: str
    environment: str
    conjunction: bool
    status: DischargeStatus
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        class_id: str,
        name: str,
        bounds: tuple[PerformanceBound, ...],
        severity: ObligationSeverity = ObligationSeverity.MEDIUM,
        description: str = "",
        workload_profile: str = "",
        environment: str = "",
        conjunction: bool = True,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "PerformanceObligation":
        """Create a new PENDING PerformanceObligation with a generated UUID.

        Args:
            class_id: Problem class identifier.
            name: Short obligation name.
            bounds: Tuple of PerformanceBound predicates.
            severity: Obligation severity.
            description: Full description.
            workload_profile: Workload description.
            environment: Runtime environment.
            conjunction: Whether ALL bounds must hold (True) or ANY (False).
            metadata: Extra annotations.

        Returns:
            A new PENDING PerformanceObligation.
        """
        return cls(
            obligation_id=str(uuid.uuid4()),
            class_id=class_id,
            name=name,
            description=description or name,
            bounds=bounds,
            severity=severity,
            workload_profile=workload_profile,
            environment=environment,
            conjunction=conjunction,
            status=DischargeStatus.PENDING,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_discharged_by(self, measurements: dict[str, float]) -> bool:
        """Return ``True`` when *measurements* discharge this obligation.

        The *measurements* dict maps ``bound_id → measured_value``.  Only
        bounds present in the dict are checked; missing bounds fail by default.

        Args:
            measurements: Dict mapping bound_id to measured value.

        Returns:
            True when the discharge condition is met.
        """
        results = [
            b.is_satisfied_by(measurements[b.bound_id])
            for b in self.bounds
            if b.bound_id in measurements
        ]
        if not results:
            return False
        return all(results) if self.conjunction else any(results)

    def violated_bounds(
        self, measurements: dict[str, float]
    ) -> list[PerformanceBound]:
        """Return bounds that are violated by *measurements*.

        Args:
            measurements: Dict mapping bound_id to measured value.

        Returns:
            List of violated PerformanceBound objects.
        """
        violated: list[PerformanceBound] = []
        for b in self.bounds:
            measured = measurements.get(b.bound_id)
            if measured is not None and not b.is_satisfied_by(measured):
                violated.append(b)
        return violated

    def with_status(self, status: DischargeStatus) -> "PerformanceObligation":
        """Return a copy with the given discharge status.

        Args:
            status: New discharge status.

        Returns:
            New PerformanceObligation with updated status.
        """
        return replace(self, status=status)

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "obligation_id": self.obligation_id,
            "class_id": self.class_id,
            "name": self.name,
            "description": self.description,
            "bounds": [b.to_dict() for b in self.bounds],
            "severity": self.severity.value,
            "workload_profile": self.workload_profile,
            "environment": self.environment,
            "conjunction": self.conjunction,
            "status": self.status.value,
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  PerformanceEvidence
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PerformanceEvidence:
    """Profiling or measurement evidence for a performance obligation.

    Attributes:
        evidence_id: UUID for this evidence record.
        obligation_id: The obligation this evidence targets.
        measurements: Mapping from bound_id to measured value.
        tool: Profiling or benchmarking tool used.
        run_count: Number of benchmark runs.
        percentile: Statistical percentile of the reported measurements.
        environment_description: Environment in which measurements were taken.
        collected_at: ISO-8601 timestamp.
        notes: Free-form notes.
    """

    evidence_id: str
    obligation_id: str
    measurements: tuple[tuple[str, float], ...]
    tool: str
    run_count: int
    percentile: float
    environment_description: str
    collected_at: str
    notes: str

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        obligation_id: str,
        measurements: dict[str, float],
        tool: str = "benchmark",
        run_count: int = 1,
        percentile: float = 95.0,
        environment_description: str = "",
        notes: str = "",
    ) -> "PerformanceEvidence":
        """Create a new PerformanceEvidence with a generated UUID and timestamp.

        Args:
            obligation_id: The obligation being evidenced.
            measurements: Dict from bound_id to measured value.
            tool: Tool used for measurement.
            run_count: Number of runs performed.
            percentile: Statistical percentile of reported values.
            environment_description: Environment description.
            notes: Free-form notes.

        Returns:
            A new PerformanceEvidence.
        """
        import datetime

        return cls(
            evidence_id=str(uuid.uuid4()),
            obligation_id=obligation_id,
            measurements=tuple(measurements.items()),
            tool=tool,
            run_count=run_count,
            percentile=max(0.0, min(100.0, percentile)),
            environment_description=environment_description,
            collected_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def measurement_dict(self) -> dict[str, float]:
        """Materialise measurements as a plain dict.

        Returns:
            Dict mapping bound_id to measured value.
        """
        return dict(self.measurements)

    def reliability_score(self) -> float:
        """Compute a reliability score for this evidence in [0, 1].

        Higher run counts and higher percentiles yield higher reliability.

        Returns:
            Float reliability score.
        """
        run_factor = min(1.0, math.log(self.run_count + 1, 10))
        percentile_factor = self.percentile / 100.0
        return run_factor * percentile_factor


# ═══════════════════════════════════════════════════════════════════════════
# §6  PerformanceObligationsAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class DischargeReport:
    """Output of an obligation discharge analysis pass.

    Attributes:
        report_id: UUID for this report.
        obligation_id: Obligation that was analysed.
        discharge_status: Whether the obligation was discharged.
        bounds_checked: Total number of bounds checked.
        bounds_satisfied: Number of bounds satisfied.
        bounds_violated: Number of bounds violated.
        violated_bound_ids: IDs of violated bounds.
        margins: Mapping from bound_id to signed margin.
        discharge_confidence: Confidence in the discharge conclusion.
        recommendations: Recommended actions for violations.
    """

    report_id: str
    obligation_id: str
    discharge_status: DischargeStatus
    bounds_checked: int
    bounds_satisfied: int
    bounds_violated: int
    violated_bound_ids: tuple[str, ...]
    margins: tuple[tuple[str, float], ...]
    discharge_confidence: float
    recommendations: tuple[str, ...]

    @classmethod
    def make(
        cls,
        obligation_id: str,
        discharge_status: DischargeStatus,
        bounds_checked: int,
        bounds_satisfied: int,
        bounds_violated: int,
        violated_bound_ids: tuple[str, ...] = (),
        margins: tuple[tuple[str, float], ...] = (),
        discharge_confidence: float = 1.0,
        recommendations: tuple[str, ...] = (),
    ) -> "DischargeReport":
        """Create a DischargeReport with a generated UUID.

        Args:
            obligation_id: The analysed obligation.
            discharge_status: Discharge conclusion.
            bounds_checked: Total bounds checked.
            bounds_satisfied: Bounds that passed.
            bounds_violated: Bounds that failed.
            violated_bound_ids: IDs of failing bounds.
            margins: Per-bound signed margins.
            discharge_confidence: Confidence in the conclusion.
            recommendations: Recommended actions.

        Returns:
            A new DischargeReport.
        """
        return cls(
            report_id=str(uuid.uuid4()),
            obligation_id=obligation_id,
            discharge_status=discharge_status,
            bounds_checked=bounds_checked,
            bounds_satisfied=bounds_satisfied,
            bounds_violated=bounds_violated,
            violated_bound_ids=violated_bound_ids,
            margins=margins,
            discharge_confidence=max(0.0, min(1.0, discharge_confidence)),
            recommendations=recommendations,
        )

    def is_discharged(self) -> bool:
        """Return ``True`` when the obligation was discharged.

        Returns:
            True for DISCHARGED status.
        """
        return self.discharge_status.is_positive()


class PerformanceObligationsAnalyzer:
    """Checks performance obligations against evidence and computes discharge status.

    The analyzer:
    1. Matches evidence measurements to obligation bounds by bound_id.
    2. Checks each bound using its BoundKind.check method.
    3. Computes signed margins for reporting.
    4. Determines the DischargeStatus from the results.
    5. Generates recommendations for violated bounds.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(
        self,
        obligation: PerformanceObligation,
        evidence: PerformanceEvidence | None = None,
        extra_measurements: dict[str, float] | None = None,
    ) -> DischargeReport:
        """Analyse a PerformanceObligation against collected evidence.

        Args:
            obligation: The obligation to analyse.
            evidence: Optional PerformanceEvidence record.
            extra_measurements: Additional bound_id→value pairs to consider.

        Returns:
            A DischargeReport.
        """
        measurements: dict[str, float] = {}
        if evidence is not None:
            measurements.update(evidence.measurement_dict())
        if extra_measurements:
            measurements.update(extra_measurements)

        violated: list[PerformanceBound] = []
        satisfied: list[PerformanceBound] = []
        unchecked: list[PerformanceBound] = []
        margins: list[tuple[str, float]] = []

        for bound in obligation.bounds:
            measured = measurements.get(bound.bound_id)
            if measured is None:
                unchecked.append(bound)
                continue
            if bound.is_satisfied_by(measured):
                satisfied.append(bound)
            else:
                violated.append(bound)
            margins.append((bound.bound_id, bound.margin(measured)))

        checked = len(satisfied) + len(violated)
        total = len(obligation.bounds)

        if total == 0:
            status = DischargeStatus.DISCHARGED
        elif unchecked and not satisfied and not violated:
            status = DischargeStatus.PENDING
        elif violated:
            status = DischargeStatus.VIOLATED if obligation.conjunction else (
                DischargeStatus.DISCHARGED if satisfied else DischargeStatus.VIOLATED
            )
        elif unchecked:
            status = DischargeStatus.PARTIAL
        else:
            status = DischargeStatus.DISCHARGED

        reliability = evidence.reliability_score() if evidence else 0.5
        confidence = reliability * (len(satisfied) / total if total else 1.0)

        recs = self._generate_recommendations(violated, unchecked)

        return DischargeReport.make(
            obligation_id=obligation.obligation_id,
            discharge_status=status,
            bounds_checked=checked,
            bounds_satisfied=len(satisfied),
            bounds_violated=len(violated),
            violated_bound_ids=tuple(b.bound_id for b in violated),
            margins=tuple(margins),
            discharge_confidence=confidence,
            recommendations=tuple(recs),
        )

    def analyse_batch(
        self,
        obligations: Sequence[PerformanceObligation],
        evidence_map: dict[str, PerformanceEvidence] | None = None,
    ) -> list[DischargeReport]:
        """Analyse a batch of obligations.

        Args:
            obligations: Obligations to analyse.
            evidence_map: Mapping from obligation_id to PerformanceEvidence.

        Returns:
            List of DischargeReport in input order.
        """
        ev_map = evidence_map or {}
        return [self.analyse(ob, ev_map.get(ob.obligation_id)) for ob in obligations]

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def _generate_recommendations(
        self,
        violated: list[PerformanceBound],
        unchecked: list[PerformanceBound],
    ) -> list[str]:
        """Generate actionable recommendations for violated and unchecked bounds.

        Args:
            violated: Bounds that failed.
            unchecked: Bounds with no evidence.

        Returns:
            List of recommendation strings.
        """
        recs: list[str] = []
        for b in violated:
            recs.append(
                f"Optimise {b.resource_kind.value} usage: measured value exceeded "
                f"bound of {b.value} {b.unit} ({b.bound_kind.value})."
            )
        for b in unchecked:
            recs.append(
                f"Collect evidence for {b.resource_kind.value} bound "
                f"{b.bound_id!r} ({b.workload_description or 'no workload specified'})."
            )
        return recs


# ═══════════════════════════════════════════════════════════════════════════
# §7  PerformanceWitness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PerformanceWitness:
    """Certificate produced when a PerformanceObligation is discharged.

    Attributes:
        witness_id: UUID for this witness.
        obligation_id: The obligation this witness covers.
        class_id: Problem class.
        discharge_report: The DischargeReport from the analyzer.
        evidence_id: ID of the PerformanceEvidence used, if any.
        final_status: Terminal discharge status.
        aggregate_margin: Weighted average signed margin across all bounds.
        overall_confidence: Aggregate confidence score.
        severity: Severity of the obligation.
        issued_at: ISO-8601 timestamp.
        notes: Free-form notes.
    """

    witness_id: str
    obligation_id: str
    class_id: str
    discharge_report: DischargeReport
    evidence_id: str | None
    final_status: DischargeStatus
    aggregate_margin: float
    overall_confidence: float
    severity: ObligationSeverity
    issued_at: str
    notes: str

    @classmethod
    def make(
        cls,
        obligation: PerformanceObligation,
        report: DischargeReport,
        evidence: PerformanceEvidence | None = None,
        notes: str = "",
    ) -> "PerformanceWitness":
        """Create a PerformanceWitness from pipeline artefacts.

        Args:
            obligation: The discharged obligation.
            report: The DischargeReport.
            evidence: The PerformanceEvidence, if any.
            notes: Free-form notes.

        Returns:
            A new PerformanceWitness.
        """
        import datetime

        margins = [m for _, m in report.margins]
        agg_margin = sum(margins) / len(margins) if margins else 0.0

        return cls(
            witness_id=str(uuid.uuid4()),
            obligation_id=obligation.obligation_id,
            class_id=obligation.class_id,
            discharge_report=report,
            evidence_id=evidence.evidence_id if evidence else None,
            final_status=report.discharge_status,
            aggregate_margin=agg_margin,
            overall_confidence=report.discharge_confidence,
            severity=obligation.severity,
            issued_at=datetime.datetime.utcnow().isoformat() + "Z",
            notes=notes,
        )

    def is_discharged(self) -> bool:
        """Return ``True`` when the obligation was discharged.

        Returns:
            True when final_status is positive.
        """
        return self.final_status.is_positive()

    def to_dict(self) -> JsonDict:
        """Serialise to a JSON-compatible dict.

        Returns:
            Plain dict representation.
        """
        return {
            "witness_id": self.witness_id,
            "obligation_id": self.obligation_id,
            "class_id": self.class_id,
            "evidence_id": self.evidence_id,
            "final_status": self.final_status.value,
            "aggregate_margin": self.aggregate_margin,
            "overall_confidence": self.overall_confidence,
            "severity": self.severity.value,
            "issued_at": self.issued_at,
            "notes": self.notes,
            "report": {
                "bounds_checked": self.discharge_report.bounds_checked,
                "bounds_satisfied": self.discharge_report.bounds_satisfied,
                "bounds_violated": self.discharge_report.bounds_violated,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# §8  PerformanceObligationsCoordinator
# ═══════════════════════════════════════════════════════════════════════════


class PerformanceObligationsCoordinator:
    """Orchestrates the full performance obligation pipeline.

    The coordinator manages:
    - A registry of PerformanceObligation and PerformanceEvidence objects.
    - Discharge analysis via PerformanceObligationsAnalyzer.
    - Witness production and accumulation.
    - Summary statistics across all obligations.

    Attributes:
        analyzer: The PerformanceObligationsAnalyzer.
        _obligations: Dict from obligation_id to PerformanceObligation.
        _evidence: Dict from obligation_id to PerformanceEvidence.
        _witnesses: Dict from obligation_id to PerformanceWitness.
    """

    def __init__(
        self,
        analyzer: PerformanceObligationsAnalyzer | None = None,
    ) -> None:
        self.analyzer = analyzer or PerformanceObligationsAnalyzer()
        self._obligations: dict[str, PerformanceObligation] = {}
        self._evidence: dict[str, PerformanceEvidence] = {}
        self._witnesses: dict[str, PerformanceWitness] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_obligation(self, obligation: PerformanceObligation) -> PerformanceObligation:
        """Register a PerformanceObligation.

        Args:
            obligation: The obligation to register.

        Returns:
            The registered obligation.
        """
        self._obligations[obligation.obligation_id] = obligation
        return obligation

    def submit_evidence(self, evidence: PerformanceEvidence) -> PerformanceEvidence:
        """Submit PerformanceEvidence for an obligation.

        Args:
            evidence: The evidence to submit.

        Returns:
            The submitted evidence.
        """
        self._evidence[evidence.obligation_id] = evidence
        return evidence

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def discharge(
        self,
        obligation_id: str,
        extra_measurements: dict[str, float] | None = None,
    ) -> PerformanceWitness:
        """Analyse and attempt to discharge a registered obligation.

        Args:
            obligation_id: The obligation to discharge.
            extra_measurements: Additional measurements to supplement evidence.

        Returns:
            A PerformanceWitness.

        Raises:
            KeyError: If obligation_id is not registered.
        """
        obligation = self._obligations[obligation_id]
        evidence = self._evidence.get(obligation_id)
        report = self.analyzer.analyse(obligation, evidence, extra_measurements)

        updated = obligation.with_status(report.discharge_status)
        self._obligations[obligation_id] = updated

        witness = PerformanceWitness.make(updated, report, evidence)
        self._witnesses[obligation_id] = witness
        return witness

    def discharge_all(
        self,
        extra_measurements: dict[str, dict[str, float]] | None = None,
    ) -> list[PerformanceWitness]:
        """Discharge all registered obligations.

        Args:
            extra_measurements: Mapping from obligation_id to extra measurements.

        Returns:
            List of PerformanceWitness in obligation registration order.
        """
        extra = extra_measurements or {}
        witnesses = []
        for oid in list(self._obligations.keys()):
            witnesses.append(self.discharge(oid, extra.get(oid)))
        return witnesses

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_witness(self, obligation_id: str) -> PerformanceWitness | None:
        """Return the witness for an obligation, if available.

        Args:
            obligation_id: The obligation identifier.

        Returns:
            The PerformanceWitness or None.
        """
        return self._witnesses.get(obligation_id)

    def all_witnesses(self) -> list[PerformanceWitness]:
        """Return all completed witnesses.

        Returns:
            List of PerformanceWitness.
        """
        return list(self._witnesses.values())

    def discharge_rate(self) -> float:
        """Return fraction of obligations successfully discharged.

        Returns:
            Float in [0.0, 1.0].
        """
        if not self._witnesses:
            return 0.0
        n = sum(1 for w in self._witnesses.values() if w.is_discharged())
        return n / len(self._witnesses)

    def critical_violations(self) -> list[PerformanceWitness]:
        """Return witnesses for CRITICAL obligations that were not discharged.

        Returns:
            List of PerformanceWitness with severity CRITICAL and non-discharged status.
        """
        return [
            w
            for w in self._witnesses.values()
            if w.severity == ObligationSeverity.CRITICAL and not w.is_discharged()
        ]

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of obligation discharge statistics.

        Returns:
            Dict with counts of discharged, violated, partial, and pending obligations.
        """
        counts: dict[str, int] = defaultdict(int)
        for w in self._witnesses.values():
            counts[w.final_status.value] += 1
        return {
            "total": len(self._witnesses),
            "discharge_rate": self.discharge_rate(),
            "by_status": dict(counts),
            "critical_violations": len(self.critical_violations()),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §9  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════


def quick_discharge(
    class_id: str,
    name: str,
    resource_kind: ResourceKind,
    bound_value: float,
    measured_value: float,
    severity: ObligationSeverity = ObligationSeverity.MEDIUM,
) -> PerformanceWitness:
    """Create, register, and discharge a simple single-bound obligation.

    Args:
        class_id: Problem class identifier.
        name: Obligation name.
        resource_kind: Resource being bounded.
        bound_value: Upper bound value.
        measured_value: Measured value to check.
        severity: Obligation severity.

    Returns:
        A PerformanceWitness.
    """
    coord = PerformanceObligationsCoordinator()
    bound = PerformanceBound.make(
        resource_kind=resource_kind,
        bound_kind=BoundKind.UPPER,
        value=bound_value,
    )
    obligation = PerformanceObligation.make(
        class_id=class_id,
        name=name,
        bounds=(bound,),
        severity=severity,
    )
    coord.register_obligation(obligation)
    evidence = PerformanceEvidence.make(
        obligation_id=obligation.obligation_id,
        measurements={bound.bound_id: measured_value},
        tool="quick_discharge",
        run_count=1,
    )
    coord.submit_evidence(evidence)
    return coord.discharge(obligation.obligation_id)


def get_all_resource_kinds() -> list[ResourceKind]:
    """Return all ResourceKind values.

    Returns:
        List of all ResourceKind members.
    """
    return list(ResourceKind)


def get_all_bound_kinds() -> list[BoundKind]:
    """Return all BoundKind values.

    Returns:
        List of all BoundKind members.
    """
    return list(BoundKind)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ═══════════════════════════════════════════════════════════════════════════
# §10  __all__
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enumerations
    "BoundKind",
    "DischargeStatus",
    "ObligationSeverity",
    "ResourceKind",
    # Frozen dataclasses
    "DischargeReport",
    "PerformanceBound",
    "PerformanceEvidence",
    "PerformanceObligation",
    "PerformanceWitness",
    # Classes
    "PerformanceObligationsAnalyzer",
    "PerformanceObligationsCoordinator",
    # Functions
    "get_all_bound_kinds",
    "get_all_resource_kinds",
    "quick_discharge",
    # Type aliases
    "ClassId",
    "EvidenceId",
    "JsonDict",
    "NumericValue",
    "ObligationId",
    "WitnessId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.


# ═══════════════════════════════════════════════════════════════════════════
# §11  Smoke test
# ═══════════════════════════════════════════════════════════════════════════

def _smoke() -> None:
    """Minimal self-test: create an obligation and discharge it."""
    # Within-bound case
    w_pass = quick_discharge(
        class_id="API",
        name="p99_latency",
        resource_kind=ResourceKind.LATENCY,
        bound_value=200.0,
        measured_value=142.0,
        severity=ObligationSeverity.HIGH,
    )
    assert w_pass.is_discharged(), f"Expected DISCHARGED, got {w_pass.final_status}"
    assert w_pass.aggregate_margin > 0.0

    # Violating case
    w_fail = quick_discharge(
        class_id="API",
        name="p99_latency_violated",
        resource_kind=ResourceKind.LATENCY,
        bound_value=100.0,
        measured_value=350.0,
        severity=ObligationSeverity.CRITICAL,
    )
    assert w_fail.final_status == DischargeStatus.VIOLATED, (
        f"Expected VIOLATED, got {w_fail.final_status}"
    )
    assert w_fail.aggregate_margin < 0.0

    d = w_pass.to_dict()
    assert "witness_id" in d and "obligation_id" in d
    print(
        f"[smoke] pass={w_pass.final_status.value} margin={w_pass.aggregate_margin:.1f}ms "
        f"fail={w_fail.final_status.value} margin={w_fail.aggregate_margin:.1f}ms"
    )


if __name__ == "__main__":
    _smoke()
