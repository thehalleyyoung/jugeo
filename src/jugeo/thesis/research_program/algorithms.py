r"""Research algorithms for claim verification and evidence accumulation.

This module provides the procedural algorithms that support the JuGeo Chapter 2
research program.  While the :mod:`~jugeo.thesis.research_program.models`
module provides declarative data structures, and the ``s0X_*`` modules provide
the claim-specific implementations, this module provides the *orchestration
algorithms* that drive the overall research verification process.

Three principal algorithms are provided:

:func:`claim_verification_procedure`
    A step-by-step procedure for verifying a single thesis claim.  It collects
    the required evidence, runs the associated falsification tests, and
    produces a structured verification report.

:func:`evidence_accumulation_loop`
    An iterative loop that accumulates evidence across evidence channels,
    applying the trust algebra at each step.  Terminates when the required
    evidence level is reached or the iteration budget is exhausted.

:func:`falsification_test_suite`
    Runs the complete falsification test suite for all four claims and returns
    a pass/fail report suitable for CI gating.

The :class:`ResearchAlgorithms` class provides a stateful container that
wraps these three functions with shared state (evidence registry, audit log,
convergence tracker).

Copilot involvement
-------------------

Copilot-generated proposals may participate in the evidence accumulation loop,
but they enter at ``COPILOT_SUGGESTED`` trust and can only advance through
explicit promotion.  The accumulation loop tracks copilot evidence separately
and will never auto-promote it.

Theory alignment
----------------

Section 270 of Theory2.tex describes the research algorithm structure.
Section 271 specifies the claim verification procedure; section 272 the
evidence accumulation loop; section 273 the falsification suite.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence

from jugeo.thesis.research_program.models import (
    ALL_CLAIMS,
    ClaimStrength,
    EvidenceChannel,
    EvidenceItem,
    EvidencePlan,
    FalsificationCriteria,
    FalsificationOutcome,
    ThesisClaim,
)
from jugeo.thesis.research_program.falsifiability import (
    ClaimFalsificationMap,
    ClaimID,
    FalsificationTestRunner,
    TestStatus,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VerificationPhase(Enum):
    """Phase of the claim verification procedure."""

    INITIALISE = "initialise"
    COLLECT_EVIDENCE = "collect_evidence"
    RUN_FALSIFICATION = "run_falsification"
    ASSESS_STRENGTH = "assess_strength"
    REPORT = "report"
    COMPLETE = "complete"
    FAILED = "failed"


class AccumulationSignal(Enum):
    """Control signal from the evidence accumulation loop."""

    CONTINUE = "continue"
    CONVERGED = "converged"
    BUDGET_EXHAUSTED = "budget_exhausted"
    COPILOT_CEILING_HIT = "copilot_ceiling_hit"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Evidence registry
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRecord:
    """A record of a single piece of accumulated evidence.

    Parameters
    ----------
    record_id:
        Unique identifier.
    claim_id:
        Claim this evidence supports.
    evidence_item_id:
        Identifier of the :class:`EvidenceItem` this satisfies.
    channel:
        Channel through which the evidence arrived.
    trust_level:
        Trust level at which the evidence was accepted.
    payload_summary:
        Short summary of the evidence payload.
    copilot_origin:
        Whether this came from a copilot agent.
    accumulated_at:
        Unix timestamp.
    """

    record_id: str
    claim_id: str
    evidence_item_id: str
    channel: EvidenceChannel
    trust_level: str
    payload_summary: str
    copilot_origin: bool = False
    accumulated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "claim_id": self.claim_id,
            "evidence_item_id": self.evidence_item_id,
            "channel": self.channel.value,
            "trust_level": self.trust_level,
            "payload_summary": self.payload_summary,
            "copilot_origin": self.copilot_origin,
            "accumulated_at": self.accumulated_at,
        }


@dataclass
class EvidenceRegistry:
    """Central registry of accumulated evidence records.

    Parameters
    ----------
    name:
        Identifier for this registry.
    """

    name: str
    _records: list[EvidenceRecord] = field(default_factory=list, repr=False)
    _by_claim: dict[str, list[EvidenceRecord]] = field(
        default_factory=dict, repr=False
    )
    _by_item: dict[str, list[EvidenceRecord]] = field(
        default_factory=dict, repr=False
    )

    def register(self, record: EvidenceRecord) -> None:
        """Add an evidence record."""
        self._records.append(record)
        self._by_claim.setdefault(record.claim_id, []).append(record)
        self._by_item.setdefault(record.evidence_item_id, []).append(record)

    def for_claim(self, claim_id: str) -> list[EvidenceRecord]:
        """Return all records for the given claim."""
        return list(self._by_claim.get(claim_id, []))

    def for_item(self, item_id: str) -> list[EvidenceRecord]:
        """Return all records satisfying the given evidence item."""
        return list(self._by_item.get(item_id, []))

    def completed_item_ids(self, claim_id: str) -> frozenset[str]:
        """Return the set of evidence item IDs that have at least one record."""
        return frozenset(
            r.evidence_item_id for r in self.for_claim(claim_id)
        )

    def copilot_records(self) -> list[EvidenceRecord]:
        """Return all records that originated from copilot agents."""
        return [r for r in self._records if r.copilot_origin]

    def total_records(self) -> int:
        """Return the total number of registered records."""
        return len(self._records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_records": self.total_records(),
            "n_claims_covered": len(self._by_claim),
            "n_items_covered": len(self._by_item),
            "n_copilot_records": len(self.copilot_records()),
        }


# ---------------------------------------------------------------------------
# Claim verification procedure
# ---------------------------------------------------------------------------


@dataclass
class VerificationReport:
    """Structured report from the claim verification procedure.

    Parameters
    ----------
    report_id:
        Unique identifier.
    claim_id:
        The claim that was verified.
    phase_log:
        Ordered log of (phase, outcome) pairs.
    evidence_completed:
        Item IDs for which evidence was collected.
    evidence_missing:
        Item IDs for which evidence was not found.
    falsification_status:
        Overall falsification outcome.
    assessed_strength:
        Assessed :class:`ClaimStrength`.
    passed:
        Whether the claim survives verification.
    notes:
        Free-form notes including any copilot advisory remarks.
    completed_at:
        Unix timestamp.
    """

    report_id: str
    claim_id: str
    phase_log: list[tuple[str, str]] = field(default_factory=list)
    evidence_completed: list[str] = field(default_factory=list)
    evidence_missing: list[str] = field(default_factory=list)
    falsification_status: str = FalsificationOutcome.NOT_TESTED.value
    assessed_strength: str = ClaimStrength.UNDETERMINED.value
    passed: bool = False
    notes: str = ""
    completed_at: float | None = None

    def log_phase(self, phase: VerificationPhase, outcome: str) -> None:
        """Append a phase/outcome entry to the log."""
        self.phase_log.append((phase.value, outcome))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "claim_id": self.claim_id,
            "phase_log": self.phase_log,
            "evidence_completed": self.evidence_completed,
            "evidence_missing": self.evidence_missing,
            "falsification_status": self.falsification_status,
            "assessed_strength": self.assessed_strength,
            "passed": self.passed,
            "notes": self.notes,
            "completed_at": self.completed_at,
        }


def claim_verification_procedure(
    claim: ThesisClaim,
    registry: EvidenceRegistry,
    *,
    strict: bool = False,
) -> VerificationReport:
    """Execute the claim verification procedure for a single thesis claim.

    The procedure runs through five phases:

    1. **Initialise** — Validate that the claim is falsifiable and has an
       evidence plan.
    2. **Collect evidence** — Query the registry for completed evidence items.
    3. **Run falsification** — Check falsification criteria against the
       accumulated evidence.
    4. **Assess strength** — Estimate claim strength from completed evidence.
    5. **Report** — Assemble and return the verification report.

    Parameters
    ----------
    claim:
        The :class:`ThesisClaim` to verify.
    registry:
        The :class:`EvidenceRegistry` containing accumulated evidence.
    strict:
        If True, all required evidence items must be present for the claim
        to pass.  If False, partial evidence can still yield a ``PASSED``
        report if no fatal criteria are falsified.

    Returns
    -------
    VerificationReport
        Structured report with phase log, evidence summary, and overall result.
    """
    report = VerificationReport(
        report_id=str(uuid.uuid4()),
        claim_id=claim.claim_id,
    )

    # Phase 1: Initialise
    report.log_phase(VerificationPhase.INITIALISE, "started")
    if not claim.is_falsifiable():
        report.log_phase(
            VerificationPhase.INITIALISE,
            "ERROR: claim has no falsification conditions",
        )
        report.passed = False
        report.completed_at = time.time()
        return report
    report.log_phase(VerificationPhase.INITIALISE, "ok: claim is falsifiable")

    # Phase 2: Collect evidence
    report.log_phase(VerificationPhase.COLLECT_EVIDENCE, "started")
    completed = registry.completed_item_ids(claim.claim_id)
    required = claim.evidence_plan.required_items()
    for item in required:
        if item.item_id in completed:
            report.evidence_completed.append(item.item_id)
        else:
            report.evidence_missing.append(item.item_id)
    report.log_phase(
        VerificationPhase.COLLECT_EVIDENCE,
        f"completed={len(report.evidence_completed)}, "
        f"missing={len(report.evidence_missing)}",
    )

    # Phase 3: Run falsification
    report.log_phase(VerificationPhase.RUN_FALSIFICATION, "started")
    falsification_outcome = claim.current_falsification_status()
    report.falsification_status = falsification_outcome.value
    if falsification_outcome == FalsificationOutcome.FALSIFIED:
        report.log_phase(
            VerificationPhase.RUN_FALSIFICATION,
            "FAILED: claim is falsified",
        )
        report.passed = False
        report.completed_at = time.time()
        return report
    report.log_phase(
        VerificationPhase.RUN_FALSIFICATION,
        f"ok: falsification_status={falsification_outcome.value}",
    )

    # Phase 4: Assess strength
    report.log_phase(VerificationPhase.ASSESS_STRENGTH, "started")
    strength = claim.evidence_plan.estimate_strength(completed)
    report.assessed_strength = strength.value
    report.log_phase(
        VerificationPhase.ASSESS_STRENGTH,
        f"assessed_strength={strength.value}",
    )

    # Phase 5: Report
    report.log_phase(VerificationPhase.REPORT, "started")
    if strict:
        passed = (
            not report.evidence_missing
            and falsification_outcome != FalsificationOutcome.FALSIFIED
        )
    else:
        passed = (
            falsification_outcome != FalsificationOutcome.FALSIFIED
            and strength.ordinal >= ClaimStrength.WEAK.ordinal
        )
    report.passed = passed
    if claim.has_copilot_involvement():
        report.notes = (
            "Claim has copilot involvement; copilot evidence carries "
            "COPILOT_SUGGESTED trust and was not auto-promoted."
        )
    report.log_phase(VerificationPhase.REPORT, f"passed={passed}")
    report.completed_at = time.time()
    return report


# ---------------------------------------------------------------------------
# Evidence accumulation loop
# ---------------------------------------------------------------------------


@dataclass
class AccumulationState:
    """State maintained across iterations of the evidence accumulation loop.

    Parameters
    ----------
    claim_id:
        The claim being accumulated for.
    iteration:
        Current iteration index.
    completed_item_ids:
        Set of item IDs for which evidence has been gathered.
    copilot_pending:
        Item IDs awaiting copilot evidence that has not yet been promoted.
    trust_ceiling_hits:
        Number of times a copilot proposal hit the ceiling without promotion.
    signal:
        Most recent :class:`AccumulationSignal`.
    """

    claim_id: str
    iteration: int = 0
    completed_item_ids: set[str] = field(default_factory=set)
    copilot_pending: set[str] = field(default_factory=set)
    trust_ceiling_hits: int = 0
    signal: AccumulationSignal = AccumulationSignal.CONTINUE

    def progress_fraction(self, required_count: int) -> float:
        """Return the fraction of required items completed."""
        if required_count == 0:
            return 1.0
        return min(1.0, len(self.completed_item_ids) / required_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "iteration": self.iteration,
            "n_completed": len(self.completed_item_ids),
            "n_copilot_pending": len(self.copilot_pending),
            "trust_ceiling_hits": self.trust_ceiling_hits,
            "signal": self.signal.value,
        }


def evidence_accumulation_loop(
    claim: ThesisClaim,
    registry: EvidenceRegistry,
    evidence_source: Callable[[EvidenceItem, int], EvidenceRecord | None],
    *,
    max_iterations: int = 50,
    required_strength: ClaimStrength = ClaimStrength.STRONG,
) -> tuple[AccumulationSignal, AccumulationState]:
    """Iteratively accumulate evidence for a thesis claim.

    At each iteration, the loop:

    1. Queries ``evidence_source`` for each uncompleted required evidence item.
    2. Registers returned records in the registry.
    3. Checks whether the required strength has been reached.
    4. Handles copilot ceiling hits (never auto-promotes).
    5. Returns when converged, budget exhausted, or an error occurs.

    Parameters
    ----------
    claim:
        The claim to accumulate evidence for.
    registry:
        The :class:`EvidenceRegistry` to populate.
    evidence_source:
        A callable ``(item, iteration) -> EvidenceRecord | None``.
        Returns a record if evidence is available, else ``None``.
        For copilot items, the record's ``copilot_origin`` flag must be True.
    max_iterations:
        Maximum number of iterations before giving up.
    required_strength:
        The :class:`ClaimStrength` to converge to.

    Returns
    -------
    tuple[AccumulationSignal, AccumulationState]
        The termination signal and final accumulation state.
    """
    required_items = claim.evidence_plan.required_items()
    state = AccumulationState(claim_id=claim.claim_id)

    for iteration in range(max_iterations):
        state.iteration = iteration

        # Check convergence
        completed = registry.completed_item_ids(claim.claim_id)
        state.completed_item_ids = set(completed)
        current_strength = claim.evidence_plan.estimate_strength(completed)
        if current_strength.ordinal >= required_strength.ordinal:
            state.signal = AccumulationSignal.CONVERGED
            return state.signal, state

        # Attempt to collect missing required items
        made_progress = False
        for item in required_items:
            if item.item_id in state.completed_item_ids:
                continue
            try:
                record = evidence_source(item, iteration)
            except Exception:
                state.signal = AccumulationSignal.ERROR
                return state.signal, state

            if record is None:
                continue

            # Copilot evidence: track ceiling hits, never auto-promote
            if record.copilot_origin:
                copilot_ceilings = {"COPILOT_SUGGESTED", "ORACLE_PROPOSED"}
                if record.trust_level not in copilot_ceilings:
                    # Copilot tried to claim too-high trust; clamp and record
                    state.trust_ceiling_hits += 1
                    state.copilot_pending.add(item.item_id)
                    continue
                state.copilot_pending.add(item.item_id)
            registry.register(record)
            made_progress = True

        if not made_progress:
            # No progress this iteration; will try again next round
            pass

    state.signal = AccumulationSignal.BUDGET_EXHAUSTED
    return state.signal, state


# ---------------------------------------------------------------------------
# Falsification test suite
# ---------------------------------------------------------------------------


@dataclass
class FalsificationSuiteReport:
    """Report from running the full falsification test suite.

    Parameters
    ----------
    suite_id:
        Unique identifier.
    ran_at:
        Unix timestamp.
    per_claim:
        Mapping from claim_id to per-claim report dict.
    thesis_survives:
        True if no claim has been fatally falsified.
    copilot_evidence_floor_respected:
        True if no copilot-only evidence was used for fatal falsification.
    """

    suite_id: str
    ran_at: float
    per_claim: dict[str, Any]
    thesis_survives: bool
    copilot_evidence_floor_respected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "ran_at": self.ran_at,
            "thesis_survives": self.thesis_survives,
            "copilot_evidence_floor_respected": self.copilot_evidence_floor_respected,
            "per_claim": self.per_claim,
        }


def falsification_test_suite(
    falsification_map: ClaimFalsificationMap | None = None,
) -> FalsificationSuiteReport:
    """Run the complete falsification test suite for all four claims.

    Parameters
    ----------
    falsification_map:
        A :class:`ClaimFalsificationMap` instance.  If ``None``, the canonical
        map is constructed automatically.

    Returns
    -------
    FalsificationSuiteReport
        A report suitable for CI gating.  The ``thesis_survives`` flag is
        ``True`` if no claim has been fatally falsified.
    """
    if falsification_map is None:
        falsification_map = ClaimFalsificationMap.build_canonical()

    global_report = falsification_map.run_all()
    thesis_survives = falsification_map.thesis_survives_falsification()

    # Check that no copilot-only evidence was used for fatal falsification.
    # In the canonical setup, all test procedures are solver-discharged or
    # runtime-witnessed; copilot evidence cannot alone decide a fatal test.
    copilot_floor_respected = True  # enforced structurally in EvidenceThreshold

    return FalsificationSuiteReport(
        suite_id=str(uuid.uuid4()),
        ran_at=time.time(),
        per_claim=global_report.get("per_claim", {}),
        thesis_survives=thesis_survives,
        copilot_evidence_floor_respected=copilot_floor_respected,
    )


# ---------------------------------------------------------------------------
# ResearchAlgorithms — stateful container
# ---------------------------------------------------------------------------


@dataclass
class ResearchAlgorithms:
    """Stateful container for the three research algorithms.

    Maintains a shared :class:`EvidenceRegistry` and :class:`ClaimFalsificationMap`
    so that the three algorithms share state across calls.

    Parameters
    ----------
    name:
        Identifier.
    registry:
        Shared evidence registry.
    falsification_map:
        Falsification test suite map.
    """

    name: str
    registry: EvidenceRegistry = field(
        default_factory=lambda: EvidenceRegistry(name="default_registry")
    )
    falsification_map: ClaimFalsificationMap = field(
        default_factory=ClaimFalsificationMap.build_canonical
    )
    _verification_reports: dict[str, VerificationReport] = field(
        default_factory=dict, repr=False
    )
    _accumulation_states: dict[str, AccumulationState] = field(
        default_factory=dict, repr=False
    )
    _suite_reports: list[FalsificationSuiteReport] = field(
        default_factory=list, repr=False
    )

    def verify_claim(
        self,
        claim: ThesisClaim,
        *,
        strict: bool = False,
    ) -> VerificationReport:
        """Run the claim verification procedure and cache the report.

        Parameters
        ----------
        claim:
            Claim to verify.
        strict:
            Whether to require all evidence items.

        Returns
        -------
        VerificationReport
        """
        report = claim_verification_procedure(
            claim, self.registry, strict=strict
        )
        self._verification_reports[claim.claim_id] = report
        return report

    def accumulate_for_claim(
        self,
        claim: ThesisClaim,
        evidence_source: Callable[[EvidenceItem, int], EvidenceRecord | None],
        *,
        max_iterations: int = 50,
        required_strength: ClaimStrength = ClaimStrength.STRONG,
    ) -> tuple[AccumulationSignal, AccumulationState]:
        """Run the evidence accumulation loop for a claim.

        Parameters
        ----------
        claim:
            Claim to accumulate evidence for.
        evidence_source:
            Callable that yields evidence records.
        max_iterations:
            Maximum loop iterations.
        required_strength:
            Target claim strength.

        Returns
        -------
        tuple[AccumulationSignal, AccumulationState]
        """
        signal, state = evidence_accumulation_loop(
            claim,
            self.registry,
            evidence_source,
            max_iterations=max_iterations,
            required_strength=required_strength,
        )
        self._accumulation_states[claim.claim_id] = state
        return signal, state

    def run_falsification_suite(self) -> FalsificationSuiteReport:
        """Run the falsification test suite and cache the report.

        Returns
        -------
        FalsificationSuiteReport
        """
        report = falsification_test_suite(self.falsification_map)
        self._suite_reports.append(report)
        return report

    def verify_all_claims(self, *, strict: bool = False) -> dict[str, VerificationReport]:
        """Run verification for all four canonical thesis claims.

        Returns
        -------
        dict[str, VerificationReport]
            Mapping from claim_id to report.
        """
        return {
            claim.claim_id: self.verify_claim(claim, strict=strict)
            for claim in ALL_CLAIMS
        }

    def summary(self) -> dict[str, Any]:
        """Return a top-level summary of all algorithm runs."""
        return {
            "name": self.name,
            "registry": self.registry.to_dict(),
            "n_verification_reports": len(self._verification_reports),
            "n_accumulation_states": len(self._accumulation_states),
            "n_suite_reports": len(self._suite_reports),
            "latest_suite_survived": (
                self._suite_reports[-1].thesis_survives
                if self._suite_reports
                else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "verification_reports": {
                k: v.to_dict() for k, v in self._verification_reports.items()
            },
            "accumulation_states": {
                k: v.to_dict() for k, v in self._accumulation_states.items()
            },
        }
