"""Data models for jugeo.generation.hypercover_treaties.

Chapter 41 of theory2.tex introduces hypercover treaty synthesis as the
process by which locally-verified overlap laws are assembled into a globally
consistent descent datum.  A *hypercover* of a semantic coordinate is a cover
U → X such that all iterated fiber products U^{×_X (n+1)} are again covers.
Under such a cover, descent data (local sections + overlap compatibilities)
uniquely determine global sections.

This module provides the core value objects used throughout the synthesis
pipeline:

* HypercoverSynthesisRecord  — full trace of one synthesis run
* TreatyCandidate            — a proposed treaty awaiting acceptance
* OverlapLaw                 — a stabilized behavioral law on an overlap
* DependentTreaty            — a treaty whose clauses depend on other treaties
* SynthesisOutcome           — final result of a complete synthesis pass

These types are intentionally immutable-by-default (frozen dataclasses) to
allow safe sharing across pipeline stages and to support snapshot comparison.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy, OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal, GoalPriority, GoalStatus, OverlapGoal,
    )
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SynthesisPhase(str, Enum):
    """Lifecycle phases of a hypercover synthesis run.

    theory2.tex §41.2 defines synthesis as a five-phase state machine.
    The transitions are strictly monotone: once a run reaches FINALIZING it
    never returns to DECOMPOSING.  FAILED is a terminal absorbing state.
    """

    DECOMPOSING = "decomposing"
    """Goal structure is being parsed and patch keys extracted."""

    COVERING = "covering"
    """A Cover object is being assembled from the parsed patch keys."""

    VALIDATING = "validating"
    """The augmented nerve condition is being checked on the constructed cover."""

    REFINING = "refining"
    """The cover is being refined to repair failed augmented nerve checks."""

    FINALIZING = "finalizing"
    """Laws are being mined and the SynthesisOutcome is being assembled."""

    COMPLETE = "complete"
    """Synthesis succeeded; a SynthesisOutcome is available."""

    FAILED = "failed"
    """Synthesis failed irrecoverably within the allowed budget."""


class LawStability(str, Enum):
    """Stability classification for an OverlapLaw.

    theory2.tex §41.5 defines four levels of stability for an overlap law,
    ranging from UNSTABLE (provisional hypothesis) to PROVEN (formally
    verified against the descent data).  Laws must reach at least STABLE
    before being admitted into the OverlapLawLibrary as canonical.
    """

    UNSTABLE = "unstable"
    """Law has been proposed but lacks sufficient supporting evidence."""

    PROVISIONAL = "provisional"
    """Law is supported by evidence but has not been independently verified."""

    STABLE = "stable"
    """Law has been cross-validated across multiple synthesis records."""

    PROVEN = "proven"
    """Law has been formally verified against a verified descent datum."""


class CandidateSource(str, Enum):
    """Provenance of a TreatyCandidate.

    Tracks where a candidate originated, which informs its prior confidence
    and the scrutiny it should receive during acceptance review.
    """

    MINED = "mined"
    """Extracted from observed synthesis history via pattern mining."""

    HYPOTHESIZED = "hypothesized"
    """Proposed by an external agent (e.g. a language model or oracle)."""

    INHERITED = "inherited"
    """Propagated from a parent synthesis record via the descent functor."""

    SYNTHESIZED = "synthesized"
    """Constructed by combining simpler laws through the law-composition rule."""


class TreatyRole(str, Enum):
    """Functional role played by a treaty within a synthesis record.

    theory2.tex §41.4 distinguishes four roles.  The PRIMARY treaty governs
    the main overlap assertion.  AUXILIARY treaties handle edge cases.
    DERIVED treaties are logical consequences of others.  FOUNDATIONAL
    treaties are axioms imported from the base theory.
    """

    PRIMARY = "primary"
    AUXILIARY = "auxiliary"
    DERIVED = "derived"
    FOUNDATIONAL = "foundational"


class OutcomeKind(str, Enum):
    """Classification of a SynthesisOutcome.

    Allows callers to quickly dispatch on the result without inspecting
    internal fields.
    """

    SUCCESS = "success"
    """All hypercover conditions satisfied, full descent datum produced."""

    PARTIAL_SUCCESS = "partial_success"
    """Some patches succeeded; at least one overlap law could not be stabilized."""

    FAILURE = "failure"
    """No admissible cover could be found within the budget."""

    TIMEOUT = "timeout"
    """Wall-clock limit was exceeded before synthesis could complete."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    """The integer token/step budget was consumed before completion."""


# ---------------------------------------------------------------------------
# Core frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HypercoverSynthesisRecord:
    """Immutable trace of a single hypercover synthesis run.

    A synthesis run corresponds to one invocation of the five-phase procedure
    described in theory2.tex §41.3.  Records accumulate steps in
    ``synthesis_steps`` to support post-hoc debugging and reproducibility.

    The record is deliberately append-only: use ``with_phase`` and
    ``with_step`` to produce updated copies rather than mutating in place.
    This design allows concurrent pipeline stages to snapshot the record at
    any point without risk of data races.

    Fields
    ------
    record_id
        UUID identifying this synthesis run.
    goal_proposition
        The proposition string from the ConstructionGoal that triggered this run.
    target_coordinate_key
        String key of the target coordinate for descent.
    phase
        Current SynthesisPhase in the state machine.
    cover_patch_keys
        Tuple of string keys for the patches making up the cover U → X.
    overlap_pairs
        Tuple of (patch_a, patch_b) pairs where the two patches overlap.
    synthesis_steps
        Human-readable log of steps executed during synthesis.
    accepted_treaty_ids
        IDs of treaties accepted during FINALIZING.
    rejected_candidate_ids
        IDs of candidates explicitly rejected.
    elapsed_seconds
        Wall-clock time consumed so far.
    budget_consumed
        Integer budget units consumed so far.
    provenance
        Tuple of provenance strings for auditability.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_proposition: str = ""
    target_coordinate_key: str = ""
    phase: SynthesisPhase = SynthesisPhase.DECOMPOSING
    cover_patch_keys: tuple[str, ...] = ()
    overlap_pairs: tuple[tuple[str, str], ...] = ()
    synthesis_steps: tuple[str, ...] = ()
    accepted_treaty_ids: tuple[str, ...] = ()
    rejected_candidate_ids: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    budget_consumed: int = 0
    provenance: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return True iff the synthesis run has reached the COMPLETE phase.

        A complete run guarantees that a SynthesisOutcome has been produced
        and all accepted laws have been recorded in accepted_treaty_ids.
        """
        return self.phase == SynthesisPhase.COMPLETE

    def is_failed(self) -> bool:
        """Return True iff the run has entered the terminal FAILED state.

        Failed runs may still contain partial information in cover_patch_keys
        and accepted_treaty_ids that is useful for debugging.
        """
        return self.phase == SynthesisPhase.FAILED

    def is_terminal(self) -> bool:
        """Return True iff the run is in any terminal phase (COMPLETE or FAILED)."""
        return self.phase in (SynthesisPhase.COMPLETE, SynthesisPhase.FAILED)

    def patch_count(self) -> int:
        """Return the number of patches in the current cover."""
        return len(self.cover_patch_keys)

    def overlap_count(self) -> int:
        """Return the number of overlap pairs recorded."""
        return len(self.overlap_pairs)

    def step_count(self) -> int:
        """Return the number of steps recorded in synthesis_steps."""
        return len(self.synthesis_steps)

    def acceptance_ratio(self) -> float:
        """Fraction of candidates that were accepted vs total (accepted + rejected).

        Returns 0.0 when no candidates have been processed yet.
        """
        total = len(self.accepted_treaty_ids) + len(self.rejected_candidate_ids)
        if total == 0:
            return 0.0
        return len(self.accepted_treaty_ids) / total

    def budget_fraction(self, max_budget: int) -> float:
        """Return fraction of max_budget consumed so far (clamped to [0, 1])."""
        if max_budget <= 0:
            return 1.0
        return min(1.0, self.budget_consumed / max_budget)

    # ------------------------------------------------------------------
    # Builder helpers (return new frozen copies)
    # ------------------------------------------------------------------

    def with_phase(self, phase: SynthesisPhase) -> "HypercoverSynthesisRecord":
        """Return a new record advanced to *phase*.

        Raises ValueError if the requested transition is illegal (e.g.
        transitioning out of FAILED, or regressing from COMPLETE to COVERING).
        """
        terminal = {SynthesisPhase.COMPLETE, SynthesisPhase.FAILED}
        if self.phase in terminal and phase not in terminal:
            raise ValueError(
                f"Cannot transition from terminal phase {self.phase!r} to {phase!r}"
            )
        return replace(self, phase=phase)

    def with_step(self, step: str) -> "HypercoverSynthesisRecord":
        """Return a new record with *step* appended to synthesis_steps."""
        return replace(self, synthesis_steps=self.synthesis_steps + (step,))

    def with_treaty_accepted(self, treaty_id: str) -> "HypercoverSynthesisRecord":
        """Return a record with *treaty_id* added to accepted_treaty_ids."""
        return replace(self, accepted_treaty_ids=self.accepted_treaty_ids + (treaty_id,))

    def with_candidate_rejected(self, candidate_id: str) -> "HypercoverSynthesisRecord":
        """Return a record with *candidate_id* added to rejected_candidate_ids."""
        return replace(
            self, rejected_candidate_ids=self.rejected_candidate_ids + (candidate_id,)
        )

    def with_budget(self, consumed: int) -> "HypercoverSynthesisRecord":
        """Return a record with budget_consumed set to *consumed*."""
        return replace(self, budget_consumed=consumed)

    def with_elapsed(self, elapsed: float) -> "HypercoverSynthesisRecord":
        """Return a record with elapsed_seconds updated to *elapsed*."""
        return replace(self, elapsed_seconds=elapsed)

    def with_cover(
        self,
        patch_keys: tuple[str, ...],
        overlap_pairs: tuple[tuple[str, str], ...],
    ) -> "HypercoverSynthesisRecord":
        """Return a record with the cover topology recorded."""
        return replace(self, cover_patch_keys=patch_keys, overlap_pairs=overlap_pairs)

    def with_provenance_entry(self, entry: str) -> "HypercoverSynthesisRecord":
        """Return a record with *entry* appended to provenance."""
        return replace(self, provenance=self.provenance + (entry,))

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"HypercoverSynthesisRecord("
            f"id={self.record_id[:8]!r}, "
            f"phase={self.phase.value!r}, "
            f"patches={self.patch_count()}, "
            f"overlaps={self.overlap_count()}, "
            f"steps={len(self.synthesis_steps)}, "
            f"budget={self.budget_consumed})"
        )


@dataclass(frozen=True, slots=True)
class TreatyCandidate:
    """A proposed treaty awaiting acceptance into the synthesis record.

    Candidates are produced by law-mining (source=MINED), external oracles
    (source=HYPOTHESIZED), or by composition of accepted laws (source=SYNTHESIZED).

    Acceptance is governed by the acceptance score: a candidate is accepted
    when ``acceptance_score() >= 0.5`` and rejected when it falls below
    ``0.2``.  Candidates between those thresholds enter a deferred queue for
    further evidence collection.

    The acceptance_score blends:
    - raw confidence (from the mining/hypothesis step)
    - a log-scale bonus for breadth of supporting evidence
    - a linear penalty for each known counterexample

    This scoring model reflects the Bayesian perspective of theory2.tex §41.4
    where evidence is collected incrementally and candidates are accepted once
    the posterior crosses a decision threshold.
    """

    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: CandidateSource = CandidateSource.MINED
    patch_keys: tuple[str, ...] = ()
    proposed_clauses: tuple[str, ...] = ()
    confidence: float = 0.0
    counterexample_count: int = 0
    supporting_evidence: tuple[str, ...] = ()
    role: TreatyRole = TreatyRole.PRIMARY
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Status predicates
    # ------------------------------------------------------------------

    def is_accepted(self) -> bool:
        """Return True when this candidate meets the acceptance threshold.

        The threshold is 0.5 (50% acceptance score).  Candidates above this
        level are added to the record's accepted_treaty_ids list.
        """
        return self.acceptance_score() >= 0.5

    def is_rejected(self) -> bool:
        """Return True when this candidate is firmly below threshold.

        The rejection floor is 0.2.  Candidates below this level are added
        to the record's rejected_candidate_ids list and are not reconsidered
        in the current synthesis run.
        """
        return self.acceptance_score() < 0.2

    def is_deferred(self) -> bool:
        """Return True when the candidate is in the uncertain region [0.2, 0.5)."""
        score = self.acceptance_score()
        return 0.2 <= score < 0.5

    def acceptance_score(self) -> float:
        """Compute a composite acceptance score in [0, 1].

        Formula (theory2.tex §41.4, Eq. 41.7):

            score = confidence
                    + 0.05 * log(1 + |supporting_evidence|)
                    - 0.1  * counterexample_count

        clamped to [0, 1].  The log bonus rewards breadth of support
        without allowing evidence spam to dominate.
        """
        bonus = 0.05 * math.log1p(len(self.supporting_evidence))
        penalty = 0.1 * self.counterexample_count
        raw = self.confidence + bonus - penalty
        return max(0.0, min(1.0, raw))

    def with_confidence(self, c: float) -> "TreatyCandidate":
        """Return a new candidate with confidence updated to *c* (clamped to [0,1])."""
        return replace(self, confidence=max(0.0, min(1.0, float(c))))

    def with_counterexample(self) -> "TreatyCandidate":
        """Return a new candidate with counterexample_count incremented by one."""
        return replace(self, counterexample_count=self.counterexample_count + 1)

    def with_evidence(self, ev_id: str) -> "TreatyCandidate":
        """Return a new candidate with *ev_id* appended to supporting_evidence."""
        return replace(self, supporting_evidence=self.supporting_evidence + (ev_id,))

    def with_clause(self, clause_description: str) -> "TreatyCandidate":
        """Return a new candidate with an additional proposed clause."""
        return replace(self, proposed_clauses=self.proposed_clauses + (clause_description,))

    def evidence_count(self) -> int:
        """Return the number of supporting evidence items."""
        return len(self.supporting_evidence)

    def clause_count(self) -> int:
        """Return the number of proposed clauses."""
        return len(self.proposed_clauses)

    def patch_count(self) -> int:
        """Return the number of patches covered by this candidate."""
        return len(self.patch_keys)

    def age_seconds(self) -> float:
        """Return how many seconds old this candidate is (since created_at)."""
        return time.time() - self.created_at

    def __repr__(self) -> str:
        return (
            f"TreatyCandidate("
            f"id={self.candidate_id[:8]!r}, "
            f"source={self.source.value!r}, "
            f"score={self.acceptance_score():.3f}, "
            f"clauses={self.clause_count()}, "
            f"patches={self.patch_count()})"
        )


@dataclass(frozen=True, slots=True)
class OverlapLaw:
    """A stabilized behavioral law governing an overlap between two patches.

    An overlap law is the canonical product of the induction procedure
    described in theory2.tex §41.5.  It states a *predicate* on the shared
    fiber U_i ×_X U_j and records the empirical support and violation counts
    that determine its stability classification.

    Laws progress through the LawStability ladder as more evidence accrues.
    A law may be demoted if new counterexamples are found.

    The ``promote_stability`` and ``demote_stability`` methods implement the
    ladder transitions.  Direct assignment of stability is intentionally not
    supported; clients must go through the methods to prevent skipping levels.
    """

    law_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patch_pair: tuple[str, str] = ("", "")
    predicate_description: str = ""
    stability: LawStability = LawStability.PROVISIONAL
    support_count: int = 0
    violation_count: int = 0
    confidence: float = 0.0
    discovered_in_record_id: str = ""
    provenance: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Stability ladder
    # ------------------------------------------------------------------

    _LADDER: tuple[LawStability, ...] = field(
        default=(
            LawStability.UNSTABLE,
            LawStability.PROVISIONAL,
            LawStability.STABLE,
            LawStability.PROVEN,
        ),
        init=False,
        repr=False,
        compare=False,
    )

    def is_stable(self) -> bool:
        """Return True iff the law has reached at least STABLE classification."""
        return self.stability in (LawStability.STABLE, LawStability.PROVEN)

    def is_proven(self) -> bool:
        """Return True iff the law has reached PROVEN status."""
        return self.stability == LawStability.PROVEN

    def is_provisional(self) -> bool:
        """Return True iff the law is currently PROVISIONAL."""
        return self.stability == LawStability.PROVISIONAL

    def is_unstable(self) -> bool:
        """Return True iff the law has not yet accrued enough support."""
        return self.stability == LawStability.UNSTABLE

    def violation_rate(self) -> float:
        """Return the fraction of observations that violated this law.

        Returns 0.0 when no evidence has been observed yet.
        """
        total = self.support_count + self.violation_count
        if total == 0:
            return 0.0
        return self.violation_count / total

    def observation_count(self) -> int:
        """Return total observations (support + violation)."""
        return self.support_count + self.violation_count

    def promote_stability(self) -> "OverlapLaw":
        """Return a new OverlapLaw one step higher in the stability ladder.

        Has no effect if the law is already PROVEN.
        """
        ladder = [
            LawStability.UNSTABLE,
            LawStability.PROVISIONAL,
            LawStability.STABLE,
            LawStability.PROVEN,
        ]
        idx = ladder.index(self.stability)
        new_stability = ladder[min(idx + 1, len(ladder) - 1)]
        return replace(self, stability=new_stability)

    def demote_stability(self) -> "OverlapLaw":
        """Return a new OverlapLaw one step lower in the stability ladder.

        Has no effect if the law is already UNSTABLE.
        """
        ladder = [
            LawStability.UNSTABLE,
            LawStability.PROVISIONAL,
            LawStability.STABLE,
            LawStability.PROVEN,
        ]
        idx = ladder.index(self.stability)
        new_stability = ladder[max(idx - 1, 0)]
        return replace(self, stability=new_stability)

    def with_observation(self, *, supported: bool) -> "OverlapLaw":
        """Return a new law updated with one observation.

        Recomputes confidence as support_count / (support_count + violation_count).
        """
        new_support = self.support_count + (1 if supported else 0)
        new_violations = self.violation_count + (0 if supported else 1)
        total = new_support + new_violations
        new_conf = new_support / total if total > 0 else 0.0
        return replace(
            self,
            support_count=new_support,
            violation_count=new_violations,
            confidence=new_conf,
        )

    def canonical_pair(self) -> tuple[str, str]:
        """Return the patch pair in canonical (lexicographically sorted) order."""
        a, b = self.patch_pair
        return (a, b) if a <= b else (b, a)

    def involves_patch(self, patch_key: str) -> bool:
        """Return True iff *patch_key* is one of the two patches in this law."""
        return patch_key in self.patch_pair

    def __repr__(self) -> str:
        return (
            f"OverlapLaw("
            f"id={self.law_id[:8]!r}, "
            f"pair={self.patch_pair!r}, "
            f"stability={self.stability.value!r}, "
            f"conf={self.confidence:.3f}, "
            f"support={self.support_count}, violations={self.violation_count})"
        )


@dataclass(frozen=True, slots=True)
class DependentTreaty:
    """A treaty whose acceptance depends on the prior acceptance of other treaties.

    theory2.tex §41.6 introduces dependent treaties to handle situations where
    the validity of one overlap assertion presupposes another.  For example, the
    commutativity law on patch pair (A, B) may depend on the associativity law
    on the triple (A, B, C).

    A DependentTreaty is *resolved* when all its dependency_ids have been
    accepted.  Resolution unlocks the treaty for evaluation.

    The dependency graph over DependentTreaty objects must be a DAG; cycles
    are a pipeline error and should be caught during the DECOMPOSING phase.
    """

    treaty_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patch_keys: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    clause_descriptions: tuple[str, ...] = ()
    is_resolved: bool = False
    resolution_provenance: tuple[str, ...] = ()
    role: TreatyRole = TreatyRole.DERIVED
    required_tier_value: int = 1

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def has_dependencies(self) -> bool:
        """Return True iff this treaty has at least one dependency."""
        return len(self.dependency_ids) > 0

    def dependency_count(self) -> int:
        """Return the number of treaty dependencies."""
        return len(self.dependency_ids)

    def mark_resolved(self, provenance: tuple[str, ...]) -> "DependentTreaty":
        """Return a new DependentTreaty marked as resolved with *provenance*.

        Raises ValueError if the treaty is already resolved.
        """
        if self.is_resolved:
            raise ValueError(
                f"DependentTreaty {self.treaty_id!r} is already resolved; "
                "cannot mark resolved again."
            )
        return replace(self, is_resolved=True, resolution_provenance=provenance)

    def unresolved_dependencies(self, accepted_ids: frozenset[str]) -> tuple[str, ...]:
        """Return the subset of dependency_ids not yet in *accepted_ids*."""
        return tuple(d for d in self.dependency_ids if d not in accepted_ids)

    def is_ready_to_evaluate(self, accepted_ids: frozenset[str]) -> bool:
        """Return True iff all dependencies have been accepted."""
        return len(self.unresolved_dependencies(accepted_ids)) == 0

    def patch_count(self) -> int:
        """Return the number of patches covered by this treaty."""
        return len(self.patch_keys)

    def clause_count(self) -> int:
        """Return the number of clauses in this treaty."""
        return len(self.clause_descriptions)

    def with_dependency(self, dep_id: str) -> "DependentTreaty":
        """Return a new DependentTreaty with *dep_id* added to dependencies."""
        if dep_id in self.dependency_ids:
            return self
        return replace(self, dependency_ids=self.dependency_ids + (dep_id,))

    def with_clause(self, clause: str) -> "DependentTreaty":
        """Return a new DependentTreaty with *clause* added to clause_descriptions."""
        return replace(self, clause_descriptions=self.clause_descriptions + (clause,))

    def __repr__(self) -> str:
        return (
            f"DependentTreaty("
            f"id={self.treaty_id[:8]!r}, "
            f"resolved={self.is_resolved}, "
            f"deps={self.dependency_count()}, "
            f"clauses={self.clause_count()}, "
            f"tier={self.required_tier_value})"
        )


@dataclass(frozen=True, slots=True, eq=False)
class SynthesisOutcome:
    """Final result of a complete hypercover synthesis pass.

    A SynthesisOutcome bundles the accepted laws, acceptance counts, and
    repair suggestions into a single immutable value that can be stored,
    compared, or fed into downstream pipeline stages.

    Partial successes are distinguished from full successes by the *kind*
    field and the presence of *failed_patches*.

    The ``summary()`` method produces a human-readable one-liner suitable
    for logging and synthesis dashboards.
    """

    outcome_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: OutcomeKind = OutcomeKind.FAILURE
    record_id: str = ""
    accepted_laws: tuple[OverlapLaw, ...] = ()
    accepted_treaties_count: int = 0
    failed_patches: tuple[str, ...] = ()
    repair_suggestions: tuple[str, ...] = ()
    total_budget_used: int = 0
    wall_seconds: float = 0.0
    provenance: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SynthesisOutcome):
            return NotImplemented
        return (
            self.kind == other.kind
            and self.accepted_laws == other.accepted_laws
            and self.accepted_treaties_count == other.accepted_treaties_count
            and self.failed_patches == other.failed_patches
            and self.repair_suggestions == other.repair_suggestions
            and self.total_budget_used == other.total_budget_used
            and self.provenance == other.provenance
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_success(self) -> bool:
        """Return True iff the outcome represents full success."""
        return self.kind == OutcomeKind.SUCCESS

    def is_partial(self) -> bool:
        """Return True iff the outcome is a partial success."""
        return self.kind == OutcomeKind.PARTIAL_SUCCESS

    def is_failure(self) -> bool:
        """Return True iff the outcome represents any kind of failure."""
        return self.kind in (
            OutcomeKind.FAILURE,
            OutcomeKind.TIMEOUT,
            OutcomeKind.BUDGET_EXHAUSTED,
        )

    def law_count(self) -> int:
        """Return the number of accepted overlap laws."""
        return len(self.accepted_laws)

    def failed_patch_count(self) -> int:
        """Return the number of patches that could not be processed."""
        return len(self.failed_patches)

    def stable_law_count(self) -> int:
        """Return the number of accepted laws that are at least STABLE."""
        return sum(1 for law in self.accepted_laws if law.is_stable())

    def proven_law_count(self) -> int:
        """Return the number of accepted laws that have reached PROVEN status."""
        return sum(1 for law in self.accepted_laws if law.is_proven())

    def repair_suggestion_count(self) -> int:
        """Return the number of repair suggestions generated."""
        return len(self.repair_suggestions)

    def laws_for_pair(self, patch_a: str, patch_b: str) -> list[OverlapLaw]:
        """Return laws that apply to the given patch pair (order-independent)."""
        canonical = tuple(sorted([patch_a, patch_b]))
        return [
            law
            for law in self.accepted_laws
            if tuple(sorted(law.patch_pair)) == canonical
        ]

    def highest_stability_law(self) -> OverlapLaw | None:
        """Return the law with the highest stability, or None if no laws."""
        if not self.accepted_laws:
            return None
        ladder = [
            LawStability.UNSTABLE,
            LawStability.PROVISIONAL,
            LawStability.STABLE,
            LawStability.PROVEN,
        ]
        return max(self.accepted_laws, key=lambda law: ladder.index(law.stability))

    def summary(self) -> str:
        """Return a human-readable one-line summary of this outcome.

        Suitable for logging and display in synthesis dashboards.
        """
        return (
            f"SynthesisOutcome[{self.kind.value}]: "
            f"{self.law_count()} laws ({self.stable_law_count()} stable, "
            f"{self.proven_law_count()} proven), "
            f"{self.accepted_treaties_count} treaties accepted, "
            f"{self.failed_patch_count()} patches failed, "
            f"budget={self.total_budget_used}, "
            f"wall={self.wall_seconds:.2f}s"
        )

    def __repr__(self) -> str:
        return (
            f"SynthesisOutcome("
            f"id={self.outcome_id[:8]!r}, "
            f"kind={self.kind.value!r}, "
            f"laws={self.law_count()}, "
            f"treaties={self.accepted_treaties_count}, "
            f"failed_patches={self.failed_patch_count()})"
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthesisConfig:
    """Hyperparameters controlling the synthesis procedure.

    All fields have conservative defaults that prioritize correctness over
    speed.  Production deployments should tune *max_budget* and
    *min_law_confidence* based on observed synthesis traces.

    The ``to_dict`` / ``from_dict`` round-trip is guaranteed to be lossless
    for all Python-native field types used here.
    """

    max_refinement_rounds: int = 5
    """Maximum number of cover-refinement iterations before giving up."""

    min_law_confidence: float = 0.7
    """Minimum confidence for a law candidate to be promoted to OverlapLaw."""

    max_budget: int = 100
    """Maximum token/step budget for one synthesis run."""

    strategy_name: str = "iterative"
    """Name of the DescentStrategy: 'eager', 'exhaustive', 'iterative', 'optimistic'."""

    enable_law_mining: bool = True
    """Whether to run the law-mining sub-pass after each synthesis round."""

    enable_dependent_treaties: bool = True
    """Whether to process DependentTreaty objects during finalization."""

    min_support_count: int = 2
    """Minimum number of supporting observations to promote PROVISIONAL → STABLE."""

    max_counterexamples_before_reject: int = 3
    """A candidate with this many counterexamples is immediately rejected."""

    overlap_generalization_depth: int = 3
    """Maximum generalization steps applied to a LawCandidate."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize this config to a plain dictionary."""
        return {
            "max_refinement_rounds": self.max_refinement_rounds,
            "min_law_confidence": self.min_law_confidence,
            "max_budget": self.max_budget,
            "strategy_name": self.strategy_name,
            "enable_law_mining": self.enable_law_mining,
            "enable_dependent_treaties": self.enable_dependent_treaties,
            "min_support_count": self.min_support_count,
            "max_counterexamples_before_reject": self.max_counterexamples_before_reject,
            "overlap_generalization_depth": self.overlap_generalization_depth,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SynthesisConfig":
        """Construct a SynthesisConfig from a plain dictionary.

        Unknown keys in *d* are silently ignored to allow forward-compatible
        config files.
        """
        valid_fields = {
            "max_refinement_rounds", "min_law_confidence", "max_budget",
            "strategy_name", "enable_law_mining", "enable_dependent_treaties",
            "min_support_count", "max_counterexamples_before_reject",
            "overlap_generalization_depth",
        }
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)

    def with_budget(self, max_budget: int) -> "SynthesisConfig":
        """Return a new config with a different max_budget."""
        return replace(self, max_budget=max_budget)

    def with_strategy(self, strategy_name: str) -> "SynthesisConfig":
        """Return a new config with a different strategy_name."""
        valid = {"eager", "exhaustive", "iterative", "optimistic"}
        if strategy_name not in valid:
            raise ValueError(f"Unknown strategy {strategy_name!r}; must be one of {valid}")
        return replace(self, strategy_name=strategy_name)

    def with_confidence_threshold(self, threshold: float) -> "SynthesisConfig":
        """Return a new config with min_law_confidence set to *threshold*."""
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"Confidence threshold must be in [0, 1], got {threshold!r}")
        return replace(self, min_law_confidence=threshold)


# ---------------------------------------------------------------------------
# OverlapLawIndex — mutable registry
# ---------------------------------------------------------------------------


@dataclass
class OverlapLawIndex:
    """Mutable registry of OverlapLaw objects, indexed by patch pair.

    Unlike OverlapLawLibrary (in overlap_laws.py) which maintains a curated
    collection with stability policies, OverlapLawIndex is a fast in-memory
    structure for quick lookup during an active synthesis run.

    Two OverlapLawIndex objects can be merged via ``merge``; the receiver
    wins on conflicts (same law_id with differing fields).

    The pair index is always stored in canonical (lex-sorted) order so that
    query("X", "Y") and query("Y", "X") return identical results.
    """

    _laws: dict[str, OverlapLaw] = field(default_factory=dict)
    _pair_index: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def _canonical(self, a: str, b: str) -> tuple[str, str]:
        """Return (a, b) in lexicographic order."""
        return (a, b) if a <= b else (b, a)

    def add(self, law: OverlapLaw) -> None:
        """Add *law* to the index.

        If a law with the same law_id already exists it is replaced.
        The pair index is updated in canonical (sorted) order so that
        query(A, B) and query(B, A) return the same results.
        """
        # Remove old entry if updating an existing law
        if law.law_id in self._laws:
            self.remove(law.law_id)
        self._laws[law.law_id] = law
        pair_key = self._canonical(*law.patch_pair)
        if pair_key not in self._pair_index:
            self._pair_index[pair_key] = []
        if law.law_id not in self._pair_index[pair_key]:
            self._pair_index[pair_key].append(law.law_id)

    def query(self, patch_a: str, patch_b: str) -> list[OverlapLaw]:
        """Return all laws governing the overlap between *patch_a* and *patch_b*.

        The query is order-independent: query("X", "Y") == query("Y", "X").
        Returns an empty list if no laws are registered for this pair.
        """
        pair_key = self._canonical(patch_a, patch_b)
        ids = self._pair_index.get(pair_key, [])
        return [self._laws[lid] for lid in ids if lid in self._laws]

    def stable_laws(self) -> list[OverlapLaw]:
        """Return all laws with stability STABLE or PROVEN."""
        return [law for law in self._laws.values() if law.is_stable()]

    def all_laws(self) -> list[OverlapLaw]:
        """Return all laws in the index (unordered)."""
        return list(self._laws.values())

    def remove(self, law_id: str) -> bool:
        """Remove the law with *law_id*.  Returns True if it existed."""
        if law_id not in self._laws:
            return False
        law = self._laws.pop(law_id)
        pair_key = self._canonical(*law.patch_pair)
        if pair_key in self._pair_index:
            self._pair_index[pair_key] = [
                lid for lid in self._pair_index[pair_key] if lid != law_id
            ]
            if not self._pair_index[pair_key]:
                del self._pair_index[pair_key]
        return True

    def merge(self, other: "OverlapLawIndex") -> None:
        """Merge all laws from *other* into self.

        Laws in *other* whose law_id already exists in self are skipped
        (self wins on conflict).  This preserves the stability of already-
        accepted laws while incorporating new discoveries.
        """
        for law_id, law in other._laws.items():
            if law_id not in self._laws:
                self.add(law)

    def update_law(self, law: OverlapLaw) -> None:
        """Replace an existing law (same law_id) with *law*.

        Raises KeyError if no law with that id exists.
        """
        if law.law_id not in self._laws:
            raise KeyError(f"No law with id {law.law_id!r} in index.")
        self.add(law)  # add handles replacement via remove-then-insert

    def promote_all_stable(self, min_support: int = 2) -> int:
        """Promote all PROVISIONAL laws with enough support to STABLE.

        A law is promoted when:
        - stability == PROVISIONAL
        - support_count >= min_support
        - violation_rate() < 0.1

        Returns the number of laws that were promoted.
        """
        promoted = 0
        for lid, law in list(self._laws.items()):
            if (
                law.stability == LawStability.PROVISIONAL
                and law.support_count >= min_support
                and law.violation_rate() < 0.1
            ):
                self._laws[lid] = law.promote_stability()
                promoted += 1
        return promoted

    def demote_by_violations(self, max_violation_rate: float = 0.15) -> int:
        """Demote laws whose violation rate exceeds *max_violation_rate*.

        Returns the number of laws that were demoted.
        """
        demoted = 0
        for lid, law in list(self._laws.items()):
            if law.observation_count() >= 3 and law.violation_rate() > max_violation_rate:
                self._laws[lid] = law.demote_stability()
                demoted += 1
        return demoted

    def law_count(self) -> int:
        """Return the total number of laws in the index."""
        return len(self._laws)

    def pair_count(self) -> int:
        """Return the number of distinct patch pairs with at least one law."""
        return len(self._pair_index)

    def coverage_report(self) -> dict[str, Any]:
        """Return a summary dict with counts by stability level."""
        counts: dict[str, int] = {s.value: 0 for s in LawStability}
        for law in self._laws.values():
            counts[law.stability.value] += 1
        return {
            "total_laws": self.law_count(),
            "pair_count": self.pair_count(),
            "by_stability": counts,
        }

    def __repr__(self) -> str:
        return (
            f"OverlapLawIndex("
            f"laws={self.law_count()}, "
            f"pairs={self.pair_count()}, "
            f"stable={len(self.stable_laws())})"
        )


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

EMPTY_SYNTHESIS_RECORD: HypercoverSynthesisRecord = HypercoverSynthesisRecord(
    record_id="00000000-0000-0000-0000-000000000000",
    goal_proposition="",
    target_coordinate_key="",
    phase=SynthesisPhase.DECOMPOSING,
)
"""A sentinel synthesis record with empty fields.

Used as a null object in pipeline stages that have not yet produced
a real record (e.g. before the first synthesis run begins).
"""

DEFAULT_CONFIG: SynthesisConfig = SynthesisConfig()
"""Default synthesis configuration with conservative hyperparameters.

Most pipeline components accept an optional SynthesisConfig and fall back
to DEFAULT_CONFIG when none is supplied.  Override specific fields using
``replace(DEFAULT_CONFIG, max_budget=200)`` or the ``with_*`` builder methods.
"""

EMPTY_LAW_INDEX: OverlapLawIndex = OverlapLawIndex()
"""A read-reference to an empty law index.

Do NOT mutate this object.  Use it only as a cheap sentinel to avoid
None checks.  Real pipeline stages should construct their own OverlapLawIndex
instances via ``OverlapLawIndex()``.
"""
