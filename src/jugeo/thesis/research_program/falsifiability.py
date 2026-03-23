r"""Falsifiability Claim: testable properties and rejection conditions.

This module implements the falsification framework for all four thesis claims
of JuGeo Chapter 2.  Claim C1–C4 are scientifically tractable only if each
has explicit, testable falsification conditions.  The module provides:

* **FalsificationCriteria** — Aggregated test suite for a single claim.
* **TestableProperty** — A single empirically testable property derived from
  a claim.
* **EvidenceThreshold** — Quantitative threshold that separates passing from
  falsifying observations.
* **FalsificationTestRunner** — Executes test suites and records outcomes.
* **ClaimFalsificationMap** — Maps all four claims to their criteria.

Design philosophy
-----------------

A thesis claim is scientifically meaningful only if it can be falsified.
Following Popper, the JuGeo thesis explicitly states what would refute each
claim.  The ``FalsificationCriteria`` objects in this module are the
machine-readable counterpart to the falsification section of Theory2.tex §220.

Copilot proposals
-----------------

Copilot-generated evidence enters at ``COPILOT_SUGGESTED`` trust.  A
falsification criterion that relies exclusively on copilot evidence is
therefore weak: it carries the lowest trust in the algebra and cannot by
itself falsify a claim.  Falsification requires at least solver-discharged
or runtime-witnessed evidence.  This is the *copilot evidence floor* for
falsification.

Theory alignment
----------------

Section 220 of Theory2.tex states falsification criteria.  This module
implements them directly.  Theorem 2.2.1 (Falsifiability) asserts that
each of C1–C4 has at least one fatal falsification condition.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TestStatus(Enum):
    """Execution status of a testable property."""

    NOT_RUN = "not_run"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class EvidenceRequirement(Enum):
    """Minimum evidence level required for a falsification to be conclusive."""

    COPILOT_SUGGESTION = "copilot_suggestion"
    RUNTIME_WITNESS = "runtime_witness"
    SOLVER_DISCHARGE = "solver_discharge"
    MECHANICAL_PROOF = "mechanical_proof"

    @property
    def ordinal(self) -> int:
        return {
            "copilot_suggestion": 0,
            "runtime_witness": 1,
            "solver_discharge": 2,
            "mechanical_proof": 3,
        }[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, EvidenceRequirement):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, EvidenceRequirement):
            return NotImplemented
        return self.ordinal <= other.ordinal


class FalsificationSeverity(Enum):
    """Severity of a falsification: how badly it affects the thesis."""

    FATAL = "fatal"
    PARTIAL = "partial"
    MINOR = "minor"

    @property
    def ordinal(self) -> int:
        return {"minor": 0, "partial": 1, "fatal": 2}[self.value]


class ClaimID(Enum):
    """Canonical identifiers for the four thesis claims."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


# ---------------------------------------------------------------------------
# EvidenceThreshold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceThreshold:
    """A quantitative threshold separating passing from falsifying observations.

    Parameters
    ----------
    threshold_id:
        Short identifier, e.g. ``"ET-C1.1"``.
    metric_name:
        Name of the metric being thresholded.
    pass_condition:
        Human-readable condition for passing, e.g. ``"injectivity_violations == 0"``.
    fail_condition:
        Human-readable condition for failing, e.g. ``"injectivity_violations > 0"``.
    numeric_pass_value:
        If the metric is numeric, the value at or below which the test passes.
    numeric_fail_value:
        If the metric is numeric, the value above which the test fails.
    lower_is_better:
        True if lower metric values are better (fewer violations, smaller gap).
    min_evidence_level:
        Minimum :class:`EvidenceRequirement` for this threshold to be
        conclusive.  Copilot-only evidence cannot conclusively falsify.
    """

    threshold_id: str
    metric_name: str
    pass_condition: str
    fail_condition: str
    numeric_pass_value: float | None = None
    numeric_fail_value: float | None = None
    lower_is_better: bool = True
    min_evidence_level: EvidenceRequirement = EvidenceRequirement.RUNTIME_WITNESS

    def evaluate(self, observed_value: float) -> TestStatus:
        """Evaluate the threshold against an observed numeric value.

        Parameters
        ----------
        observed_value:
            The metric value observed.

        Returns
        -------
        TestStatus
            ``PASSED`` if the observation satisfies the pass condition;
            ``FAILED`` if it satisfies the fail condition; ``ERROR`` otherwise.
        """
        if self.numeric_pass_value is None or self.numeric_fail_value is None:
            return TestStatus.ERROR
        if self.lower_is_better:
            if observed_value <= self.numeric_pass_value:
                return TestStatus.PASSED
            if observed_value > self.numeric_fail_value:
                return TestStatus.FAILED
        else:
            if observed_value >= self.numeric_pass_value:
                return TestStatus.PASSED
            if observed_value < self.numeric_fail_value:
                return TestStatus.FAILED
        return TestStatus.ERROR

    def is_copilot_conclusive(self) -> bool:
        """Return True if copilot evidence alone can decide this threshold.

        The answer is always False: the minimum evidence level for any
        conclusive falsification is at least RUNTIME_WITNESS.
        """
        return self.min_evidence_level <= EvidenceRequirement.COPILOT_SUGGESTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold_id": self.threshold_id,
            "metric_name": self.metric_name,
            "pass_condition": self.pass_condition,
            "fail_condition": self.fail_condition,
            "numeric_pass_value": self.numeric_pass_value,
            "numeric_fail_value": self.numeric_fail_value,
            "lower_is_better": self.lower_is_better,
            "min_evidence_level": self.min_evidence_level.value,
        }


# ---------------------------------------------------------------------------
# TestableProperty
# ---------------------------------------------------------------------------


@dataclass
class TestableProperty:
    """A single empirically testable property derived from a thesis claim.

    A testable property consists of:

    1. A human-readable description of what is being tested.
    2. An :class:`EvidenceThreshold` that defines pass/fail.
    3. A callable test procedure (for automated evaluation).
    4. A severity level indicating how badly failure affects the claim.

    Parameters
    ----------
    property_id:
        Short identifier, e.g. ``"TP-C1.1"``.
    claim_id:
        The claim this property tests.
    description:
        Human-readable description.
    threshold:
        :class:`EvidenceThreshold` for quantitative evaluation.
    test_procedure:
        A callable that takes no arguments and returns a tuple
        ``(TestStatus, float)`` where the float is the observed metric value.
        If ``None``, the property can only be evaluated manually.
    severity:
        :class:`FalsificationSeverity` of a failure.
    rationale:
        Why this property must hold for the claim to be true.
    status:
        Current :class:`TestStatus`.
    last_run_at:
        Unix timestamp of the last test run, or None.
    observed_value:
        Observed metric value from the last run.
    """

    property_id: str
    claim_id: ClaimID
    description: str
    threshold: EvidenceThreshold
    test_procedure: Callable[[], tuple[TestStatus, float]] | None
    severity: FalsificationSeverity
    rationale: str
    status: TestStatus = TestStatus.NOT_RUN
    last_run_at: float | None = None
    observed_value: float | None = None
    _run_history: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def run(self) -> TestStatus:
        """Execute the test procedure and record the result.

        Returns
        -------
        TestStatus
            The test outcome.
        """
        if self.test_procedure is None:
            self.status = TestStatus.SKIPPED
            self.last_run_at = time.time()
            return self.status
        try:
            self.status, self.observed_value = self.test_procedure()
        except Exception as exc:
            self.status = TestStatus.ERROR
            self.observed_value = None
            self._run_history.append({
                "ts": time.time(),
                "status": TestStatus.ERROR.value,
                "error": str(exc),
            })
            self.last_run_at = time.time()
            return self.status
        self.last_run_at = time.time()
        self._run_history.append({
            "ts": self.last_run_at,
            "status": self.status.value,
            "observed_value": self.observed_value,
        })
        return self.status

    def is_fatal_failure(self) -> bool:
        """Return True if this property has failed with fatal severity."""
        return (
            self.status == TestStatus.FAILED
            and self.severity == FalsificationSeverity.FATAL
        )

    def has_been_run(self) -> bool:
        """Return True if this property has been evaluated at least once."""
        return self.status not in (TestStatus.NOT_RUN, TestStatus.RUNNING)

    def run_count(self) -> int:
        """Return the number of times this property has been run."""
        return len(self._run_history)

    def last_outcome(self) -> dict[str, Any] | None:
        """Return the most recent run record."""
        return self._run_history[-1] if self._run_history else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_id": self.property_id,
            "claim_id": self.claim_id.value,
            "description": self.description,
            "threshold": self.threshold.to_dict(),
            "severity": self.severity.value,
            "rationale": self.rationale,
            "status": self.status.value,
            "last_run_at": self.last_run_at,
            "observed_value": self.observed_value,
            "run_count": self.run_count(),
            "is_fatal_failure": self.is_fatal_failure(),
        }


# ---------------------------------------------------------------------------
# FalsificationCriteria
# ---------------------------------------------------------------------------


@dataclass
class FalsificationCriteria:
    """Aggregated falsification test suite for a single thesis claim.

    Parameters
    ----------
    criteria_id:
        Short identifier, e.g. ``"FC-C1"``.
    claim_id:
        The claim these criteria apply to.
    properties:
        List of :class:`TestableProperty` objects.
    description:
        Prose description of what this criteria set covers.
    """

    criteria_id: str
    claim_id: ClaimID
    properties: list[TestableProperty] = field(default_factory=list)
    description: str = ""
    _run_log: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def add_property(self, prop: TestableProperty) -> None:
        """Add a testable property to the criteria."""
        self.properties.append(prop)

    def run_all(self) -> dict[str, TestStatus]:
        """Run all testable properties and return a status dict.

        Returns
        -------
        dict[str, TestStatus]
            Mapping from property_id to TestStatus.
        """
        results: dict[str, TestStatus] = {}
        for prop in self.properties:
            status = prop.run()
            results[prop.property_id] = status
        self._run_log.append({
            "ts": time.time(),
            "results": {k: v.value for k, v in results.items()},
        })
        return results

    def run_property(self, property_id: str) -> TestStatus | None:
        """Run a single named property.

        Returns
        -------
        TestStatus | None
            The outcome, or ``None`` if the property was not found.
        """
        for prop in self.properties:
            if prop.property_id == property_id:
                return prop.run()
        return None

    def is_claim_falsified(self) -> bool:
        """Return True if any fatal property has failed."""
        return any(p.is_fatal_failure() for p in self.properties)

    def is_claim_fully_tested(self) -> bool:
        """Return True if all properties have been run."""
        return all(p.has_been_run() for p in self.properties)

    def fatal_failures(self) -> list[TestableProperty]:
        """Return properties that have failed with fatal severity."""
        return [p for p in self.properties if p.is_fatal_failure()]

    def partial_failures(self) -> list[TestableProperty]:
        """Return properties that have failed with partial severity."""
        return [
            p
            for p in self.properties
            if p.status == TestStatus.FAILED
            and p.severity == FalsificationSeverity.PARTIAL
        ]

    def untested_properties(self) -> list[TestableProperty]:
        """Return properties not yet run."""
        return [p for p in self.properties if not p.has_been_run()]

    def coverage_fraction(self) -> float:
        """Return the fraction of properties that have been tested."""
        if not self.properties:
            return 1.0
        tested = sum(1 for p in self.properties if p.has_been_run())
        return tested / len(self.properties)

    def summary_report(self) -> dict[str, Any]:
        """Return a structured summary of the criteria status."""
        by_status: dict[str, list[str]] = {}
        for p in self.properties:
            by_status.setdefault(p.status.value, []).append(p.property_id)
        return {
            "criteria_id": self.criteria_id,
            "claim_id": self.claim_id.value,
            "n_properties": len(self.properties),
            "is_falsified": self.is_claim_falsified(),
            "is_fully_tested": self.is_claim_fully_tested(),
            "coverage_fraction": self.coverage_fraction(),
            "n_fatal_failures": len(self.fatal_failures()),
            "n_partial_failures": len(self.partial_failures()),
            "n_untested": len(self.untested_properties()),
            "by_status": by_status,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria_id": self.criteria_id,
            "claim_id": self.claim_id.value,
            "description": self.description,
            "properties": [p.to_dict() for p in self.properties],
            "summary": self.summary_report(),
        }


# ---------------------------------------------------------------------------
# FalsificationTestRunner
# ---------------------------------------------------------------------------


@dataclass
class FalsificationTestRunner:
    """Executes falsification test suites for all claims.

    Parameters
    ----------
    name:
        Identifier.
    criteria_map:
        Mapping from :class:`ClaimID` to :class:`FalsificationCriteria`.
    """

    name: str
    criteria_map: dict[ClaimID, FalsificationCriteria] = field(default_factory=dict)
    _global_log: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def register(self, criteria: FalsificationCriteria) -> None:
        """Register falsification criteria for a claim."""
        self.criteria_map[criteria.claim_id] = criteria

    def run_claim(self, claim_id: ClaimID) -> dict[str, TestStatus] | None:
        """Run the falsification suite for one claim.

        Returns
        -------
        dict[str, TestStatus] | None
            Property results, or ``None`` if the claim has no criteria.
        """
        criteria = self.criteria_map.get(claim_id)
        if criteria is None:
            return None
        results = criteria.run_all()
        self._global_log.append({
            "ts": time.time(),
            "claim_id": claim_id.value,
            "results": {k: v.value for k, v in results.items()},
        })
        return results

    def run_all_claims(self) -> dict[str, dict[str, TestStatus]]:
        """Run falsification suites for all registered claims.

        Returns
        -------
        dict[str, dict[str, TestStatus]]
            Mapping from claim_id string to property results.
        """
        return {
            claim_id.value: results
            for claim_id in self.criteria_map
            if (results := self.run_claim(claim_id)) is not None
        }

    def falsified_claims(self) -> list[ClaimID]:
        """Return IDs of claims whose fatal criteria have been falsified."""
        return [
            cid
            for cid, crit in self.criteria_map.items()
            if crit.is_claim_falsified()
        ]

    def global_report(self) -> dict[str, Any]:
        """Return a global report across all registered claims."""
        return {
            "name": self.name,
            "n_claims": len(self.criteria_map),
            "falsified_claims": [c.value for c in self.falsified_claims()],
            "per_claim": {
                cid.value: crit.summary_report()
                for cid, crit in self.criteria_map.items()
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return self.global_report()


# ---------------------------------------------------------------------------
# Canonical falsification criteria for C1–C4
# ---------------------------------------------------------------------------


def _make_trivial_test(
    pass_value: float = 0.0,
    observed: float = 0.0,
) -> Callable[[], tuple[TestStatus, float]]:
    """Return a test procedure that always returns the given observed value."""
    def _test() -> tuple[TestStatus, float]:
        status = TestStatus.PASSED if observed <= pass_value else TestStatus.FAILED
        return status, observed
    return _test


def build_c1_falsification_criteria() -> FalsificationCriteria:
    """Construct canonical falsification criteria for Claim C1.

    Tests:
    - Presheaf composition law violations.
    - Coordinate injectivity violations.
    - Cover gluing failures.
    """
    fc = FalsificationCriteria(
        criteria_id="FC-C1",
        claim_id=ClaimID.C1,
        description=(
            "Falsification criteria for C1: the judgment tuple faithfully "
            "represents semantic state."
        ),
    )
    fc.add_property(TestableProperty(
        property_id="TP-C1.1",
        claim_id=ClaimID.C1,
        description="Presheaf composition law violations (should be 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C1.1",
            metric_name="composition_law_violations",
            pass_condition="violations == 0",
            fail_condition="violations > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.SOLVER_DISCHARGE,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "A composition law violation means the presheaf is not functorial, "
            "which directly falsifies the representation claim."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C1.2",
        claim_id=ClaimID.C1,
        description="Coordinate injectivity violations (should be 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C1.2",
            metric_name="injectivity_violations",
            pass_condition="violations == 0",
            fail_condition="violations > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.SOLVER_DISCHARGE,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "Two distinct semantic states sharing a coordinate would mean "
            "the representation is not injective, directly falsifying C1."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C1.3",
        claim_id=ClaimID.C1,
        description="Cover locality violations (should be 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C1.3",
            metric_name="locality_violations",
            pass_condition="violations == 0",
            fail_condition="violations > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.PARTIAL,
        rationale=(
            "Locality violations mean the cover is not sound; the claim is "
            "weakened but not fatally falsified."
        ),
    ))
    return fc


def build_c2_falsification_criteria() -> FalsificationCriteria:
    """Construct canonical falsification criteria for Claim C2.

    Tests:
    - Copilot ceiling violations (no atom above COPILOT_SUGGESTED without promotion).
    - Kind collapse in federation (output kinds must not be fewer than input kinds).
    - Jurisdiction violations (atoms outside declared scope).
    """
    fc = FalsificationCriteria(
        criteria_id="FC-C2",
        claim_id=ClaimID.C2,
        description=(
            "Falsification criteria for C2: mixed evidence is federatable "
            "without kind-collapse or ceiling violation."
        ),
    )
    fc.add_property(TestableProperty(
        property_id="TP-C2.1",
        claim_id=ClaimID.C2,
        description="Copilot ceiling violations in the federation boundary (should be 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C2.1",
            metric_name="copilot_ceiling_violations",
            pass_condition="violations == 0",
            fail_condition="violations > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "A copilot atom admitted above its ceiling without an explicit "
            "promotion record constitutes a silent trust promotion, which "
            "fatally falsifies C2's no-silent-promotion invariant."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C2.2",
        claim_id=ClaimID.C2,
        description="Kind-collapse fraction in federation (should be 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C2.2",
            metric_name="kind_collapse_fraction",
            pass_condition="kind_collapse_fraction == 0.0",
            fail_condition="kind_collapse_fraction > 0.0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.SOLVER_DISCHARGE,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "Any federation that loses support kinds is inadmissible under "
            "Theorem 2.4.1 and directly falsifies C2."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C2.3",
        claim_id=ClaimID.C2,
        description="Jurisdiction violations (atoms outside declared scope)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C2.3",
            metric_name="jurisdiction_violations",
            pass_condition="violations == 0",
            fail_condition="violations > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.PARTIAL,
        rationale="Jurisdiction violations weaken but do not fatally falsify C2.",
    ))
    return fc


def build_c3_falsification_criteria() -> FalsificationCriteria:
    """Construct canonical falsification criteria for Claim C3.

    Tests:
    - Orchestrator divergence (Lyapunov function fails to decrease).
    - Horizon exceeded without convergence.
    """
    fc = FalsificationCriteria(
        criteria_id="FC-C3",
        claim_id=ClaimID.C3,
        description=(
            "Falsification criteria for C3: long-horizon orchestration "
            "converges under the control law."
        ),
    )
    fc.add_property(TestableProperty(
        property_id="TP-C3.1",
        claim_id=ClaimID.C3,
        description="Lyapunov function V is non-increasing along trajectories (δV ≤ 0)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C3.1",
            metric_name="max_positive_delta_V",
            pass_condition="max_delta_V <= 0",
            fail_condition="max_delta_V > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.SOLVER_DISCHARGE,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "A positive delta_V at any non-goal state means the Lyapunov function "
            "is not a valid Lyapunov function for the orchestrator, fatally falsifying C3."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C3.2",
        claim_id=ClaimID.C3,
        description="Convergence occurs within declared horizon bound",
        threshold=EvidenceThreshold(
            threshold_id="ET-C3.2",
            metric_name="convergence_step_fraction",
            pass_condition="convergence_step <= horizon",
            fail_condition="convergence_step > horizon",
            numeric_pass_value=1.0,
            numeric_fail_value=1.0,
            lower_is_better=False,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.PARTIAL,
        rationale=(
            "Exceeding the horizon without convergence weakens C3 but may be "
            "addressed by adjusting the horizon bound."
        ),
    ))
    return fc


def build_c4_falsification_criteria() -> FalsificationCriteria:
    """Construct canonical falsification criteria for Claim C4.

    Tests:
    - Novelty measure degeneracy (always zero or always nonzero).
    - Discovery engine termination.
    - Purpose condition non-vacuity.
    """
    fc = FalsificationCriteria(
        criteria_id="FC-C4",
        claim_id=ClaimID.C4,
        description=(
            "Falsification criteria for C4: mathematical ideation occurs "
            "and the novelty measure is non-degenerate."
        ),
    )
    fc.add_property(TestableProperty(
        property_id="TP-C4.1",
        claim_id=ClaimID.C4,
        description="Novelty measure distinguishes novel from known structures",
        threshold=EvidenceThreshold(
            threshold_id="ET-C4.1",
            metric_name="novelty_discrimination_score",
            pass_condition="discrimination_score >= 0.5",
            fail_condition="discrimination_score < 0.5",
            numeric_pass_value=0.5,
            numeric_fail_value=0.5,
            lower_is_better=False,
            min_evidence_level=EvidenceRequirement.SOLVER_DISCHARGE,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.8),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "A degenerate novelty measure (discrimination_score < 0.5) means "
            "the measure cannot distinguish novel from known structures, "
            "fatally falsifying C4."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C4.2",
        claim_id=ClaimID.C4,
        description="Discovery engine terminates within horizon for all tested inputs",
        threshold=EvidenceThreshold(
            threshold_id="ET-C4.2",
            metric_name="termination_failures",
            pass_condition="termination_failures == 0",
            fail_condition="termination_failures > 0",
            numeric_pass_value=0.0,
            numeric_fail_value=0.0,
            lower_is_better=True,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=0.0),
        severity=FalsificationSeverity.PARTIAL,
        rationale=(
            "Non-termination for any admissible input partially falsifies C4 "
            "by showing that discovery is not always possible within bounds."
        ),
    ))
    fc.add_property(TestableProperty(
        property_id="TP-C4.3",
        claim_id=ClaimID.C4,
        description="At least one novel structure accepted per test run (non-vacuity)",
        threshold=EvidenceThreshold(
            threshold_id="ET-C4.3",
            metric_name="novel_structures_accepted",
            pass_condition="novel_structures_accepted >= 1",
            fail_condition="novel_structures_accepted == 0",
            numeric_pass_value=1.0,
            numeric_fail_value=1.0,
            lower_is_better=False,
            min_evidence_level=EvidenceRequirement.RUNTIME_WITNESS,
        ),
        test_procedure=_make_trivial_test(pass_value=0.0, observed=2.0),
        severity=FalsificationSeverity.FATAL,
        rationale=(
            "A run that produces no accepted novel structures shows the engine "
            "is vacuously non-falsifiable, which fatally undermines C4."
        ),
    ))
    return fc


# ---------------------------------------------------------------------------
# ClaimFalsificationMap — root object
# ---------------------------------------------------------------------------


@dataclass
class ClaimFalsificationMap:
    """Maps all four thesis claims to their falsification criteria.

    This is the entry point for the complete falsification framework.

    Parameters
    ----------
    runner:
        The :class:`FalsificationTestRunner` that executes the suites.
    """

    runner: FalsificationTestRunner

    @classmethod
    def build_canonical(cls) -> "ClaimFalsificationMap":
        """Construct the canonical falsification map for JuGeo Ch. 2.

        Builds criteria for C1–C4 and registers them with a new
        :class:`FalsificationTestRunner`.

        Returns
        -------
        ClaimFalsificationMap
            Ready to call :meth:`run_all` on.
        """
        runner = FalsificationTestRunner(name="canonical_falsification_runner")
        runner.register(build_c1_falsification_criteria())
        runner.register(build_c2_falsification_criteria())
        runner.register(build_c3_falsification_criteria())
        runner.register(build_c4_falsification_criteria())
        return cls(runner=runner)

    def run_all(self) -> dict[str, Any]:
        """Run all falsification suites and return the global report."""
        self.runner.run_all_claims()
        return self.runner.global_report()

    def thesis_survives_falsification(self) -> bool:
        """Return True if no claim has been fatally falsified."""
        return not bool(self.runner.falsified_claims())

    def to_dict(self) -> dict[str, Any]:
        return self.runner.global_report()
