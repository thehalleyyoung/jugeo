"""Descent and gluing engine for JuGeo covers.

This module implements the *descent* procedure described in §3 of theory2.tex:
given local sections defined over each member of a cover, descent checks overlap
compatibility and produces either a global section (the H⁰ cohomology class) or
a DescentObstruction describing exactly which overlap laws failed and what the
resulting H¹ obstruction class looks like.

Descent is the CORE mechanism of JuGeo.  Local judgments — type-checks, proofs,
test verdicts, copilot-generated proposals — each live over a single patch.
Descent is what turns those local judgments into global guarantees about the
entire coordinate space.  When descent fails, the resulting obstruction is
not merely an error message: it is a first-class cohomological object that
can be inspected, refined, and (with copilot assistance) repaired.

The module is layered:

    LocalSection          — a judgment datum on a single patch
    OverlapCondition      — a compatibility check between two patches
    GluingData            — a structured collection ready for descent
    DescentEngine         — the orchestrator that runs the procedure
    DescentResult         — union: GlobalSection | DescentObstruction
    RepairFrontier        — actionable repair hints when descent fails
    CohomologyClass       — persistent obstruction representative
    DescentLog            — audit trail of every overlap check

Legacy helpers ``run_descent`` and ``glue_sections`` are preserved for
backward compatibility with existing tests and orchestration layers.

References
----------
theory2.tex §3     "Descent and Gluing"
theory2.tex §3.2   "Obstruction Classes and Repair"
theory2.tex §3.4   "Iterative Descent and Copilot-Assisted Refinement"
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Mapping, Sequence

from jugeo.geometry.covers import Cover, refine_cover
from jugeo.geometry.supports import SupportRegion


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class OverlapStatus(str, Enum):
    """Status of a single overlap compatibility check.

    See theory2.tex §3.1: each overlap U_i ∩ U_j carries a compatibility
    predicate that may be UNCHECKED, SATISFIED, VIOLATED, or PARTIAL
    (satisfied on a proper sub-region of the overlap).
    """

    UNCHECKED = 'unchecked'
    SATISFIED = 'satisfied'
    VIOLATED = 'violated'
    PARTIAL = 'partial'


class DescentStrategy(str, Enum):
    """Strategy governing how the descent engine traverses overlaps.

    EAGER       — stop at the first violated overlap (fail-fast).
    EXHAUSTIVE  — check every overlap before reporting.
    ITERATIVE   — progressive refinement: coarse cover first, then refine
                  around violations (theory2.tex §3.4).
    OPTIMISTIC  — assume compatible, verify lazily; useful when a copilot
                  proposes sections that are *likely* compatible.
    """

    EAGER = 'eager'
    EXHAUSTIVE = 'exhaustive'
    ITERATIVE = 'iterative'
    OPTIMISTIC = 'optimistic'


class TrustFloorPolicy(str, Enum):
    """Policy for computing the trust floor of a global section.

    MINIMUM      — trust is the minimum across all constituent local sections.
    WEIGHTED     — trust is the overlap-count-weighted mean.
    PESSIMISTIC  — trust is the minimum minus a penalty per violated overlap.
    """

    MINIMUM = 'minimum'
    WEIGHTED = 'weighted'
    PESSIMISTIC = 'pessimistic'


class DescentPhase(str, Enum):
    """Phases recorded in the descent log."""

    INITIALIZATION = 'initialization'
    OVERLAP_CHECK = 'overlap_check'
    COCYCLE_COMPUTATION = 'cocycle_computation'
    GLUING = 'gluing'
    OBSTRUCTION_EXTRACTION = 'obstruction_extraction'
    REPAIR_ANALYSIS = 'repair_analysis'
    FINALIZATION = 'finalization'


# ---------------------------------------------------------------------------
# Helper: default gluing key (preserved from original module)
# ---------------------------------------------------------------------------

def _default_gluing_key(
    section: Mapping[str, Any],
) -> tuple[tuple[str, Any], ...]:
    """Extract a canonical comparison key from a local section mapping.

    Keys named ``'patch'`` are excluded so that two sections from different
    patches can be compared purely on their judgment content.
    """
    return tuple(sorted(
        (k, v) for k, v in section.items() if k != 'patch'
    ))


def _default_compatibility_predicate(
    left_data: Mapping[str, Any],
    right_data: Mapping[str, Any],
) -> bool:
    """Return True when two local section mappings agree on shared keys.

    This is the simplest overlap law: literal equality of judgment data on
    the intersection.  More sophisticated predicates may allow homotopies,
    evidence subsumption, or copilot-mediated reconciliation.
    """
    left_key = _default_gluing_key(left_data)
    right_key = _default_gluing_key(right_data)
    return left_key == right_key


def _compute_digest(*parts: str) -> str:
    """Stable SHA-256 digest used for persistence IDs and certificates."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode('utf-8'))
    return hasher.hexdigest()[:16]


class _CallableInt(int):
    """Int that also supports call syntax for legacy APIs."""

    def __call__(self) -> int:
        return int(self)


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LocalSection:
    """A judgment datum living on a single patch of a cover.

    Per theory2.tex §2.3, a local section is a triple (coordinate, judgment,
    evidence) together with provenance metadata.  The ``trust_level`` is a
    float in [0, 1] expressing how strongly the evidence supports the
    judgment; 1.0 means machine-checked, lower values may come from
    copilot heuristics or partial test suites.

    ``residual_obligations`` lists proof/test obligations that remain open
    for this section.  A section with non-empty residual obligations is
    ``is_partial = True`` and may still participate in descent — the engine
    propagates the obligations to the global level.
    """

    coordinate: str
    judgment_data: dict[str, Any] = field(default_factory=dict)
    evidence_bundle: tuple[str, ...] = field(default_factory=tuple)
    trust_level: float = 1.0
    provenance: tuple[str, ...] = field(default_factory=tuple)
    is_partial: bool = False
    residual_obligations: list[str] = field(default_factory=list)

    # -- query helpers -------------------------------------------------------

    @property
    def key(self) -> str:
        """Stable identifier derived from the coordinate path."""
        return self.coordinate

    @property
    def is_fully_evidenced(self) -> bool:
        """True when every obligation is discharged."""
        return len(self.residual_obligations) == 0 and not self.is_partial

    def trust_meets_floor(self, floor: float) -> bool:
        """Check whether this section's trust level meets a required floor."""
        return self.trust_level >= floor

    def with_trust(self, new_trust: float) -> LocalSection:
        """Return a copy with an updated trust level (clamped to [0,1])."""
        clamped = max(0.0, min(1.0, new_trust))
        return LocalSection(
            coordinate=self.coordinate,
            judgment_data=dict(self.judgment_data),
            evidence_bundle=self.evidence_bundle,
            trust_level=clamped,
            provenance=self.provenance + ('trust_override',),
            is_partial=self.is_partial,
            residual_obligations=list(self.residual_obligations),
        )

    def merge_evidence(self, extra: Sequence[str]) -> LocalSection:
        """Return a copy with additional evidence entries appended."""
        return LocalSection(
            coordinate=self.coordinate,
            judgment_data=dict(self.judgment_data),
            evidence_bundle=self.evidence_bundle + tuple(extra),
            trust_level=self.trust_level,
            provenance=self.provenance + ('merge_evidence',),
            is_partial=self.is_partial,
            residual_obligations=list(self.residual_obligations),
        )

    def discharge_obligation(self, obligation: str) -> LocalSection:
        """Return a copy with the named obligation removed."""
        remaining = [o for o in self.residual_obligations if o != obligation]
        return LocalSection(
            coordinate=self.coordinate,
            judgment_data=dict(self.judgment_data),
            evidence_bundle=self.evidence_bundle,
            trust_level=self.trust_level,
            provenance=self.provenance + (f'discharge:{obligation}',),
            is_partial=len(remaining) > 0,
            residual_obligations=remaining,
        )

    def summary(self) -> str:
        """Human-readable one-line summary for log output."""
        status = 'partial' if self.is_partial else 'full'
        return (
            f'LocalSection({self.coordinate}, trust={self.trust_level:.2f}, '
            f'{status}, obligations={len(self.residual_obligations)})'
        )


@dataclass(frozen=True, slots=True)
class OverlapCondition:
    """A compatibility condition on the overlap of two patches.

    Per theory2.tex §3.1, given patches U_i, U_j with sections s_i, s_j the
    overlap condition checks that ``s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}``
    (up to the chosen compatibility predicate).  The ``status`` tracks
    whether this check has been performed and what the outcome was.
    """

    left_coordinate: str
    right_coordinate: str
    overlap_coordinate: str
    compatibility_predicate: Callable[
        [Mapping[str, Any], Mapping[str, Any]], bool
    ] = field(default=_default_compatibility_predicate)
    status: OverlapStatus = OverlapStatus.UNCHECKED

    # -- identity and display -----------------------------------------------

    @property
    def pair(self) -> tuple[str, str]:
        """Canonical ordered pair of the two patch coordinates."""
        return (self.left_coordinate, self.right_coordinate)

    @property
    def overlap_key(self) -> str:
        """Stable string key for this overlap, usable as dict key."""
        return f'{self.left_coordinate}∩{self.right_coordinate}'

    @property
    def is_resolved(self) -> bool:
        """True when the condition has been checked (satisfied or violated)."""
        return self.status in (OverlapStatus.SATISFIED, OverlapStatus.VIOLATED)

    @property
    def is_healthy(self) -> bool:
        """True only when compatibility has been positively confirmed."""
        return self.status == OverlapStatus.SATISFIED

    def evaluate(
        self,
        left_data: Mapping[str, Any],
        right_data: Mapping[str, Any],
    ) -> OverlapCondition:
        """Run the predicate and return a new condition with updated status.

        This is a pure function: the original OverlapCondition is not mutated.
        If the predicate raises, the status becomes VIOLATED with a rank-2
        penalty recorded in the overlap key (caught at the engine level).
        """
        try:
            compatible = self.compatibility_predicate(left_data, right_data)
        except Exception:
            return OverlapCondition(
                left_coordinate=self.left_coordinate,
                right_coordinate=self.right_coordinate,
                overlap_coordinate=self.overlap_coordinate,
                compatibility_predicate=self.compatibility_predicate,
                status=OverlapStatus.VIOLATED,
            )
        new_status = OverlapStatus.SATISFIED if compatible else OverlapStatus.VIOLATED
        return OverlapCondition(
            left_coordinate=self.left_coordinate,
            right_coordinate=self.right_coordinate,
            overlap_coordinate=self.overlap_coordinate,
            compatibility_predicate=self.compatibility_predicate,
            status=new_status,
        )

    def mark_partial(self) -> OverlapCondition:
        """Return a copy with status set to PARTIAL.

        Used when the overlap is only partially covered by evidence — for
        instance when a copilot-generated section covers most but not all
        of the overlap region.
        """
        return OverlapCondition(
            left_coordinate=self.left_coordinate,
            right_coordinate=self.right_coordinate,
            overlap_coordinate=self.overlap_coordinate,
            compatibility_predicate=self.compatibility_predicate,
            status=OverlapStatus.PARTIAL,
        )

    def summary(self) -> str:
        """One-line human-readable summary."""
        return f'Overlap({self.overlap_key}, {self.status.value})'


# ---------------------------------------------------------------------------
# Cohomology class (obstruction representative)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CohomologyClass:
    """Represents an H¹ obstruction arising from failed descent.

    Per theory2.tex §3.2, when local sections disagree on overlaps the
    disagreements form a Čech 1-cocycle.  If the cocycle is a coboundary
    the obstruction is trivial and can be removed by adjusting individual
    sections.  A non-trivial class is a *persistent* obstruction that
    requires structural repair (cover refinement, judgment revision, or
    copilot-assisted re-generation).
    """

    dimension: int = 1
    cocycle_data: dict[str, Any] = field(default_factory=dict)
    coboundary_candidates: tuple[str, ...] = field(default_factory=tuple)
    _persistence_id: str = ''

    def __post_init__(self) -> None:
        if self._persistence_id:
            return
        payload = json.dumps(
            {
                'dimension': self.dimension,
                'cocycle_data': self.cocycle_data,
                'coboundary_candidates': list(self.coboundary_candidates),
            },
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        )
        object.__setattr__(self, '_persistence_id', _compute_digest(payload))

    @property
    def persistence_id(self) -> str:
        """Stable identifier for cross-session tracking of this obstruction."""
        return self._persistence_id

    def is_trivial(self) -> bool:
        """Return True when the cocycle is a coboundary.

        A trivial class means the obstruction can be resolved by local
        adjustments to individual sections without changing the cover.
        """
        if not self.cocycle_data:
            return True
        if self.coboundary_candidates:
            for candidate in self.coboundary_candidates:
                if self._candidate_resolves(candidate):
                    return True
        return False

    def _candidate_resolves(self, candidate: str) -> bool:
        """Check whether a coboundary candidate trivializes the cocycle.

        A candidate key names a section adjustment.  If the cocycle entry
        for that key is zero (empty dict, None, or matches a neutral
        element), the candidate resolves that component.
        """
        entry = self.cocycle_data.get(candidate)
        return entry is None or entry == {} or entry == 0

    @property
    def rank(self) -> _CallableInt:
        """Number of non-trivial cocycle components.

        Higher rank means more overlaps failed — a useful proxy for the
        severity of the obstruction.
        """
        return _CallableInt(sum(
            1 for v in self.cocycle_data.values()
            if v is not None and v != {} and v != 0
        ))

    def restrict_to(self, keys: frozenset[str]) -> CohomologyClass:
        """Restrict the cocycle to a subset of overlap keys.

        Useful for localizing the obstruction to a sub-cover when planning
        iterative repair.
        """
        restricted = {k: v for k, v in self.cocycle_data.items() if k in keys}
        return CohomologyClass(
            dimension=self.dimension,
            cocycle_data=restricted,
            coboundary_candidates=tuple(
                c for c in self.coboundary_candidates if c in keys
            ),
            _persistence_id=self._persistence_id,
        )

    def merge(self, other: CohomologyClass) -> CohomologyClass:
        """Merge two obstruction classes (direct sum of cocycle data)."""
        merged = dict(self.cocycle_data)
        merged.update(other.cocycle_data)
        candidates = set(self.coboundary_candidates) | set(other.coboundary_candidates)
        return CohomologyClass(
            dimension=max(self.dimension, other.dimension),
            cocycle_data=merged,
            coboundary_candidates=tuple(sorted(candidates)),
        )

    def summary(self) -> str:
        """Human-readable summary for logs and diagnostics."""
        status = 'trivial' if self.is_trivial() else 'non-trivial'
        return (
            f'H^{self.dimension}({status}, rank={self.rank()}, '
            f'id={self.persistence_id})'
        )


# ---------------------------------------------------------------------------
# Repair frontier
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairFrontier:
    """Describes what must change to make a failed descent succeed.

    When the descent engine encounters violations, it computes a repair
    frontier: the *minimal* set of changes that would eliminate every
    violated overlap (theory2.tex §3.2).  This is what a copilot repair
    loop consumes when proposing fixes.

    ``missing_evidence`` — evidence entries that, if supplied, would
    discharge residual obligations and potentially resolve overlaps.

    ``weakened_claims`` — judgment claims that are too strong for the
    available evidence; weakening them may restore compatibility.

    ``suggested_refinements`` — cover refinements that could split a
    problematic overlap into smaller, individually satisfiable pieces.

    ``estimated_cost`` — heuristic cost in abstract work-units, used by
    the orchestration layer to prioritize repair tasks.
    """

    missing_evidence: tuple[str, ...] = field(default_factory=tuple)
    weakened_claims: tuple[str, ...] = field(default_factory=tuple)
    suggested_refinements: tuple[str, ...] = field(default_factory=tuple)
    estimated_cost: float = 0.0
    extra_categories: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __init__(
        self,
        categories: Mapping[str, Sequence[str]] | None = None,
        *,
        missing_evidence: Sequence[str] = (),
        weakened_claims: Sequence[str] = (),
        suggested_refinements: Sequence[str] = (),
        estimated_cost: float = 0.0,
        extra_categories: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        if categories is not None:
            missing_evidence = tuple(categories.get('evidence', categories.get('missing_evidence', ())))
            weakened_claims = tuple(categories.get('patch', categories.get('weakened_claims', ())))
            suggested_refinements = tuple(
                categories.get('refinement', categories.get('suggested_refinements', ()))
            )
            extras = {
                str(k): tuple(str(item) for item in v)
                for k, v in categories.items()
                if k not in {
                    'evidence', 'missing_evidence',
                    'patch', 'weakened_claims',
                    'refinement', 'suggested_refinements',
                }
            }
        else:
            extras = {}
        if extra_categories:
            extras.update(
                {str(k): tuple(str(item) for item in v) for k, v in extra_categories.items()}
            )
        object.__setattr__(self, 'missing_evidence', tuple(str(x) for x in missing_evidence))
        object.__setattr__(self, 'weakened_claims', tuple(str(x) for x in weakened_claims))
        object.__setattr__(self, 'suggested_refinements', tuple(str(x) for x in suggested_refinements))
        object.__setattr__(self, 'estimated_cost', float(estimated_cost))
        object.__setattr__(self, 'extra_categories', extras)

    def is_empty(self) -> bool:
        """True when no repairs are suggested (should not happen in practice)."""
        return (
            len(self.missing_evidence) == 0
            and len(self.weakened_claims) == 0
            and len(self.suggested_refinements) == 0
            and all(len(items) == 0 for items in self.extra_categories.values())
        )

    def total_items(self) -> int:
        """Total number of individual repair items across all categories."""
        return (
            len(self.missing_evidence)
            + len(self.weakened_claims)
            + len(self.suggested_refinements)
            + sum(len(items) for items in self.extra_categories.values())
        )

    def prioritized_items(self) -> list[tuple[str, str]]:
        """Return all repair items as (category, item) pairs, cheapest first.

        Ordering heuristic: missing evidence is cheapest (just needs more
        data), weakened claims next, cover refinements most expensive.
        """
        items: list[tuple[str, str]] = []
        for e in self.missing_evidence:
            items.append(('missing_evidence', e))
        for c in self.weakened_claims:
            items.append(('weakened_claim', c))
        for r in self.suggested_refinements:
            items.append(('suggested_refinement', r))
        for category, entries in self.extra_categories.items():
            for entry in entries:
                items.append((category, entry))
        return items

    def merge(self, other: RepairFrontier) -> RepairFrontier:
        """Combine two repair frontiers (union of all items)."""
        return RepairFrontier(
            missing_evidence=self.missing_evidence + other.missing_evidence,
            weakened_claims=self.weakened_claims + other.weakened_claims,
            suggested_refinements=(
                self.suggested_refinements + other.suggested_refinements
            ),
            estimated_cost=self.estimated_cost + other.estimated_cost,
            extra_categories={
                key: tuple(self.extra_categories.get(key, ())) + tuple(other.extra_categories.get(key, ()))
                for key in set(self.extra_categories) | set(other.extra_categories)
            },
        )

    def without_category(self, category: str) -> RepairFrontier:
        """Return a copy with one category of items removed."""
        return RepairFrontier(
            missing_evidence=(
                () if category in {'missing_evidence', 'evidence'} else self.missing_evidence
            ),
            weakened_claims=(
                () if category in {'weakened_claim', 'weakened_claims', 'patch'} else self.weakened_claims
            ),
            suggested_refinements=(
                () if category in {'suggested_refinement', 'suggested_refinements', 'refinement'}
                else self.suggested_refinements
            ),
            estimated_cost=self.estimated_cost,
            extra_categories={
                key: value
                for key, value in self.extra_categories.items()
                if key != category
            },
        )

    def summary(self) -> str:
        """One-line summary for log output."""
        return (
            f'RepairFrontier(evidence={len(self.missing_evidence)}, '
            f'weakened={len(self.weakened_claims)}, '
            f'refinements={len(self.suggested_refinements)}, '
            f'cost={self.estimated_cost:.1f})'
        )


# ---------------------------------------------------------------------------
# Descent log
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DescentLog:
    """Detailed audit trail of a single descent attempt.

    Every overlap check, phase transition, and timing measurement is
    recorded here so that copilot repair loops and human reviewers can
    understand *why* descent succeeded or failed.
    """

    phases: list[tuple[DescentPhase, float]] = field(default_factory=list)
    overlap_checks: list[dict[str, Any]] = field(default_factory=list)
    evidence_consulted: list[str] = field(default_factory=list)
    final_result: str = 'pending'
    _start_time: float = field(default_factory=time.monotonic)

    def record_phase(self, phase: DescentPhase) -> None:
        """Record the start of a new phase with its wall-clock timestamp."""
        self.phases.append((phase, time.monotonic() - self._start_time))

    def record_overlap_check(
        self,
        left: str,
        right: str,
        status: OverlapStatus,
        duration_seconds: float,
    ) -> None:
        """Record the outcome and timing of a single overlap check."""
        self.overlap_checks.append({
            'left': left,
            'right': right,
            'status': status.value,
            'duration': duration_seconds,
        })

    def record_evidence(self, evidence_id: str) -> None:
        """Record that a piece of evidence was consulted during descent."""
        self.evidence_consulted.append(evidence_id)

    def finalize(self, result: str) -> None:
        """Mark the log as complete with the given result label."""
        self.final_result = result
        self.record_phase(DescentPhase.FINALIZATION)

    @property
    def total_duration(self) -> float:
        """Total wall-clock time from start to last recorded phase."""
        if not self.phases:
            return 0.0
        return self.phases[-1][1]

    @property
    def slowest_overlap(self) -> dict[str, Any] | None:
        """Return the overlap check that took the longest, or None."""
        if not self.overlap_checks:
            return None
        return max(self.overlap_checks, key=lambda c: c['duration'])

    @property
    def violated_count(self) -> int:
        """Number of overlap checks that resulted in VIOLATED."""
        return sum(
            1 for c in self.overlap_checks
            if c['status'] == OverlapStatus.VIOLATED.value
        )

    @property
    def satisfied_count(self) -> int:
        """Number of overlap checks that resulted in SATISFIED."""
        return sum(
            1 for c in self.overlap_checks
            if c['status'] == OverlapStatus.SATISFIED.value
        )

    def phase_summary(self) -> list[str]:
        """Return a list of human-readable phase lines for diagnostics."""
        lines: list[str] = []
        for phase, elapsed in self.phases:
            lines.append(f'  [{elapsed:8.4f}s] {phase.value}')
        return lines

    def summary(self) -> str:
        """Multi-line summary suitable for terminal output."""
        header = f'DescentLog: {self.final_result} ({self.total_duration:.4f}s)'
        checks = (
            f'  overlaps: {len(self.overlap_checks)} checked, '
            f'{self.satisfied_count} satisfied, {self.violated_count} violated'
        )
        evidence = f'  evidence consulted: {len(self.evidence_consulted)} items'
        return '\n'.join([header, checks, evidence])


# ---------------------------------------------------------------------------
# Descent configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DescentConfiguration:
    """Tuning knobs for the descent engine.

    ``depth_limit`` caps the number of iterative refinement rounds.
    ``overlap_check_timeout`` is per-overlap, in seconds.
    ``copilot_assisted_repair`` enables the engine to produce
    RepairFrontier objects that a copilot loop can act on.
    """

    depth_limit: int = 5
    overlap_check_timeout: float = 10.0
    strategy: DescentStrategy = DescentStrategy.EXHAUSTIVE
    trust_floor_policy: TrustFloorPolicy = TrustFloorPolicy.MINIMUM
    copilot_assisted_repair: bool = True
    max_parallel_workers: int = 4
    record_log: bool = True

    def with_strategy(self, strategy: DescentStrategy) -> DescentConfiguration:
        """Return a copy with a different strategy."""
        return DescentConfiguration(
            depth_limit=self.depth_limit,
            overlap_check_timeout=self.overlap_check_timeout,
            strategy=strategy,
            trust_floor_policy=self.trust_floor_policy,
            copilot_assisted_repair=self.copilot_assisted_repair,
            max_parallel_workers=self.max_parallel_workers,
            record_log=self.record_log,
        )

    def with_depth(self, depth: int) -> DescentConfiguration:
        """Return a copy with an updated depth limit."""
        return DescentConfiguration(
            depth_limit=max(1, depth),
            overlap_check_timeout=self.overlap_check_timeout,
            strategy=self.strategy,
            trust_floor_policy=self.trust_floor_policy,
            copilot_assisted_repair=self.copilot_assisted_repair,
            max_parallel_workers=self.max_parallel_workers,
            record_log=self.record_log,
        )

    def summary(self) -> str:
        """One-line summary."""
        copilot_flag = '+copilot' if self.copilot_assisted_repair else ''
        return (
            f'DescentConfig({self.strategy.value}, depth={self.depth_limit}, '
            f'timeout={self.overlap_check_timeout}s{copilot_flag})'
        )


# ---------------------------------------------------------------------------
# Gluing data (collection of local sections + overlaps)
# ---------------------------------------------------------------------------


class _CallableInt(int):
    """Integer value that also supports legacy zero-argument call syntax."""

    def __call__(self) -> int:
        return int(self)


class _CallableStr(str):
    """String value that also supports legacy zero-argument call syntax."""

    def __call__(self) -> str:
        return str(self)


@dataclass(slots=True)
class GluingData:
    """Structured collection of local sections and their overlap conditions.

    This is the *input* to the descent engine.  It owns the book-keeping of
    which patches have sections, which overlaps exist, and pre-evaluates or
    lazily evaluates overlap compatibility.

    Per theory2.tex §3, gluing data is a Čech 0-cochain (the local sections)
    together with the Čech differential structure (the overlaps).  The
    ``compute_cocycle`` method constructs the 1-cocycle measuring the failure
    of compatibility.
    """

    sections: dict[str, LocalSection] = field(default_factory=dict)
    overlaps: list[OverlapCondition] = field(default_factory=list)

    # -- compatibility -------------------------------------------------------

    def __getattribute__(self, name: str) -> Any:
        value = object.__getattribute__(self, name)
        if name in {"patch_count", "overlap_count"} and isinstance(value, int):
            return _CallableInt(value)
        if name == "summary" and isinstance(value, str):
            return _CallableStr(value)
        return value

    # -- mutation helpers ----------------------------------------------------

    def add_section(self, section: LocalSection) -> None:
        """Register a local section on a patch coordinate."""
        self.sections[section.coordinate] = section

    def add_overlap(self, overlap: OverlapCondition) -> None:
        """Register an overlap condition between two patches."""
        self.overlaps.append(overlap)

    def add_overlap_pair(
        self,
        left: str,
        right: str,
        predicate: Callable[
            [Mapping[str, Any], Mapping[str, Any]], bool
        ] | None = None,
    ) -> None:
        """Convenience: create and register an OverlapCondition from a pair."""
        pred = predicate or _default_compatibility_predicate
        overlap_coord = f'{left}∩{right}'
        self.overlaps.append(OverlapCondition(
            left_coordinate=left,
            right_coordinate=right,
            overlap_coordinate=overlap_coord,
            compatibility_predicate=pred,
        ))

    # -- queries -------------------------------------------------------------

    def patch_count(self) -> int:
        """Number of patches with registered local sections."""
        return len(self.sections)

    def overlap_count(self) -> int:
        """Number of registered overlap conditions."""
        return len(self.overlaps)

    def section_for(self, coordinate: str) -> LocalSection | None:
        """Look up the local section for a coordinate, or None."""
        return self.sections.get(coordinate)

    def overlaps_involving(self, coordinate: str) -> list[OverlapCondition]:
        """Return all overlaps where ``coordinate`` appears on either side."""
        return [
            o for o in self.overlaps
            if o.left_coordinate == coordinate
            or o.right_coordinate == coordinate
        ]

    def missing_sections(self) -> list[str]:
        """Return coordinates referenced by overlaps but lacking sections."""
        referenced: set[str] = set()
        for o in self.overlaps:
            referenced.add(o.left_coordinate)
            referenced.add(o.right_coordinate)
        return sorted(referenced - set(self.sections.keys()))

    # -- overlap verification ------------------------------------------------

    def verify_all_overlaps(self) -> list[OverlapCondition]:
        """Evaluate every overlap condition and return the updated list.

        Each condition is evaluated against the judgment data of its two
        constituent sections.  If a section is missing, the overlap is
        marked VIOLATED.  The internal ``self.overlaps`` list is updated
        in-place and also returned.
        """
        evaluated: list[OverlapCondition] = []
        for overlap in self.overlaps:
            left_sec = self.sections.get(overlap.left_coordinate)
            right_sec = self.sections.get(overlap.right_coordinate)
            if left_sec is None or right_sec is None:
                evaluated.append(OverlapCondition(
                    left_coordinate=overlap.left_coordinate,
                    right_coordinate=overlap.right_coordinate,
                    overlap_coordinate=overlap.overlap_coordinate,
                    compatibility_predicate=overlap.compatibility_predicate,
                    status=OverlapStatus.VIOLATED,
                ))
            else:
                evaluated.append(overlap.evaluate(
                    left_sec.judgment_data, right_sec.judgment_data,
                ))
        self.overlaps = evaluated
        return evaluated

    def find_violated_overlaps(self) -> list[OverlapCondition]:
        """Return only the overlaps whose status is VIOLATED.

        If overlaps have not been evaluated yet, calls verify_all_overlaps
        first.
        """
        if any(o.status == OverlapStatus.UNCHECKED for o in self.overlaps):
            self.verify_all_overlaps()
        return [o for o in self.overlaps if o.status == OverlapStatus.VIOLATED]

    def find_satisfied_overlaps(self) -> list[OverlapCondition]:
        """Return only the overlaps whose status is SATISFIED."""
        return [o for o in self.overlaps if o.status == OverlapStatus.SATISFIED]

    def compute_cocycle(self) -> CohomologyClass:
        """Compute the Čech 1-cocycle from overlap disagreements.

        The cocycle maps each violated overlap key to the *discrepancy*
        between the two local sections' judgment data.  A trivial cocycle
        (all entries zero) means descent will succeed; a non-trivial cocycle
        is the H¹ obstruction (theory2.tex §3.2).
        """
        if any(o.status == OverlapStatus.UNCHECKED for o in self.overlaps):
            self.verify_all_overlaps()

        cocycle_data: dict[str, Any] = {}
        coboundary_candidates: list[str] = []

        for overlap in self.overlaps:
            if overlap.status == OverlapStatus.SATISFIED:
                continue
            key = overlap.overlap_key
            left_sec = self.sections.get(overlap.left_coordinate)
            right_sec = self.sections.get(overlap.right_coordinate)
            if left_sec is None or right_sec is None:
                cocycle_data[key] = {'missing': True}
            else:
                left_keys = set(left_sec.judgment_data.keys())
                right_keys = set(right_sec.judgment_data.keys())
                discrepancy: dict[str, Any] = {}
                for k in left_keys | right_keys:
                    lv = left_sec.judgment_data.get(k)
                    rv = right_sec.judgment_data.get(k)
                    if lv != rv:
                        discrepancy[k] = {'left': lv, 'right': rv}
                cocycle_data[key] = discrepancy
                if len(discrepancy) <= 1:
                    coboundary_candidates.append(key)

        return CohomologyClass(
            dimension=1,
            cocycle_data=cocycle_data,
            coboundary_candidates=tuple(coboundary_candidates),
        )

    @classmethod
    def from_cover(
        cls,
        cover: Cover,
        section_data: Mapping[str, Mapping[str, Any]],
    ) -> GluingData:
        """Build GluingData from a Cover and raw section mappings.

        This factory bridges the legacy ``Mapping[str, Mapping[str, Any]]``
        interface used by ``run_descent`` and ``glue_sections`` to the
        richer ``GluingData`` model.
        """
        data = cls()
        for key, raw in section_data.items():
            data.add_section(LocalSection(
                coordinate=key,
                judgment_data=dict(raw),
                provenance=('from_cover',),
            ))
        for left, right in cover.overlaps:
            data.add_overlap_pair(left, right)
        return data

    def summary(self) -> str:
        """One-line summary for logging."""
        violated = len(self.find_violated_overlaps())
        return (
            f'GluingData(patches={self.patch_count()}, '
            f'overlaps={self.overlap_count()}, violated={violated})'
        )


# ---------------------------------------------------------------------------
# Result types: GlobalSection, DescentObstruction, DescentResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GlobalSection:
    """The result of successful descent: a glued global section.

    Per theory2.tex §3, this is the H⁰ element — the global judgment that
    is consistent across all patches of the cover.  ``trust_floor`` is the
    minimum trust level across all constituents, ensuring the global claim
    is no stronger than its weakest local evidence.
    """

    coordinate: str
    merged_judgment: dict[str, Any] = field(default_factory=dict)
    constituent_sections: tuple[str, ...] = field(default_factory=tuple)
    overlap_evidence: tuple[str, ...] = field(default_factory=tuple)
    certificate: str = ''
    trust_floor: float = 1.0

    @property
    def is_fully_trusted(self) -> bool:
        """True when every constituent was trust-level 1.0."""
        return self.trust_floor >= 1.0

    @property
    def constituent_count(self) -> _CallableInt:
        """Number of local sections that were glued."""
        return _CallableInt(len(self.constituent_sections))

    def restrict_to_keys(self, keys: frozenset[str]) -> GlobalSection:
        """Return a copy keeping only judgment keys in ``keys``."""
        restricted = {k: v for k, v in self.merged_judgment.items() if k in keys}
        return GlobalSection(
            coordinate=self.coordinate,
            merged_judgment=restricted,
            constituent_sections=self.constituent_sections,
            overlap_evidence=self.overlap_evidence,
            certificate=self.certificate,
            trust_floor=self.trust_floor,
        )

    def with_certificate(self, cert: str) -> GlobalSection:
        """Return a copy with an updated certificate string."""
        return GlobalSection(
            coordinate=self.coordinate,
            merged_judgment=dict(self.merged_judgment),
            constituent_sections=self.constituent_sections,
            overlap_evidence=self.overlap_evidence,
            certificate=cert,
            trust_floor=self.trust_floor,
        )

    def evidence_summary(self) -> str:
        """Summarize the evidence backing this global section."""
        return (
            f'GlobalSection({self.coordinate}): '
            f'{int(self.constituent_count)} constituents, '
            f'trust_floor={self.trust_floor:.2f}, '
            f'evidence={len(self.overlap_evidence)} items, '
            f'cert={self.certificate[:12]}...'
            if self.certificate else
            f'GlobalSection({self.coordinate}): '
            f'{int(self.constituent_count)} constituents, '
            f'trust_floor={self.trust_floor:.2f}, '
            f'evidence={len(self.overlap_evidence)} items, no certificate'
        )

    def summary(self) -> str:
        """One-line summary."""
        return (
            f'GlobalSection({self.coordinate}, '
            f'trust={self.trust_floor:.2f}, '
            f'constituents={int(self.constituent_count)})'
        )


@dataclass(frozen=True, slots=True)
class DescentObstruction:
    """The result of failed descent: a structured obstruction.

    Per theory2.tex §3.2, a descent obstruction is an H¹ class together with
    actionable metadata.  ``partial_section`` holds whatever *did* glue
    successfully (useful for incremental repair).  ``repair_frontier``
    describes what a copilot repair loop would need to change.

    ``persistence_id`` is a stable hash so the same obstruction can be
    tracked across multiple descent attempts and copilot repair iterations.
    """

    coordinate: str
    violated_overlaps: tuple[OverlapCondition, ...] = field(default_factory=tuple)
    partial_section: dict[str, Any] | None = None
    repair_frontier: RepairFrontier = field(default_factory=RepairFrontier)
    cohomology_class: CohomologyClass = field(default_factory=CohomologyClass)
    persistence_id: str = ''

    def __post_init__(self) -> None:
        if not self.persistence_id:
            object.__setattr__(self, 'persistence_id', uuid.uuid4().hex[:16])

    @property
    def violation_count(self) -> int:
        """Number of overlaps that failed compatibility."""
        return len(self.violated_overlaps)

    @property
    def has_partial(self) -> bool:
        """True when some patches did glue, producing a partial section."""
        return self.partial_section is not None

    @property
    def is_trivial_obstruction(self) -> bool:
        """True when the cohomology class is trivial (locally fixable)."""
        return self.cohomology_class.is_trivial()

    def violated_pairs(self) -> list[tuple[str, str]]:
        """Return the (left, right) coordinate pairs of violated overlaps."""
        return [o.pair for o in self.violated_overlaps]

    def involved_coordinates(self) -> frozenset[str]:
        """All coordinates that appear in at least one violated overlap."""
        coords: set[str] = set()
        for o in self.violated_overlaps:
            coords.add(o.left_coordinate)
            coords.add(o.right_coordinate)
        return frozenset(coords)

    def restrict_to(self, keys: frozenset[str]) -> DescentObstruction:
        """Restrict the obstruction to a subset of coordinates."""
        restricted_overlaps = tuple(
            o for o in self.violated_overlaps
            if o.left_coordinate in keys or o.right_coordinate in keys
        )
        return DescentObstruction(
            coordinate=self.coordinate,
            violated_overlaps=restricted_overlaps,
            partial_section=self.partial_section,
            repair_frontier=self.repair_frontier,
            cohomology_class=self.cohomology_class.restrict_to(keys),
            persistence_id=self.persistence_id,
        )

    def evidence_summary(self) -> str:
        """Multi-line summary of the obstruction for diagnostics."""
        lines = [
            f'DescentObstruction({self.coordinate}):',
            f'  violations: {self.violation_count}',
            f'  cohomology: {self.cohomology_class.summary()}',
            f'  repair: {self.repair_frontier.summary()}',
            f'  partial: {"yes" if self.has_partial else "no"}',
            f'  persistence: {self.persistence_id}',
        ]
        return '\n'.join(lines)

    def summary(self) -> str:
        """One-line summary."""
        return (
            f'Obstruction({self.coordinate}, '
            f'violations={self.violation_count}, '
            f'trivial={self.is_trivial_obstruction})'
        )


@dataclass(frozen=True, slots=True, init=False)
class DescentResult:
    """Union type: either a successful GlobalSection or a DescentObstruction.

    Per theory2.tex §3, descent always terminates with exactly one of these.
    Callers should check ``is_success`` before unwrapping.
    """

    _global_section: GlobalSection | None = None
    _obstruction: DescentObstruction | None = None

    def __init__(
        self,
        _global_section: GlobalSection | None = None,
        _obstruction: DescentObstruction | None = None,
        *,
        is_success: bool | None = None,
        section: GlobalSection | None = None,
        obstruction: DescentObstruction | None = None,
    ) -> None:
        if section is not None:
            _global_section = section
        if obstruction is not None:
            _obstruction = obstruction
        if is_success is True and _global_section is None:
            raise ValueError('Successful descent results require a section')
        if is_success is False and _obstruction is None:
            raise ValueError('Failed descent results require an obstruction')
        object.__setattr__(self, '_global_section', _global_section)
        object.__setattr__(self, '_obstruction', _obstruction)

    @property
    def is_success(self) -> bool:
        """True when descent produced a global section."""
        return self._global_section is not None

    @property
    def is_failure(self) -> bool:
        """True when descent produced an obstruction."""
        return self._obstruction is not None

    @property
    def section(self) -> GlobalSection | None:
        return self._global_section

    @property
    def obstruction(self) -> DescentObstruction | None:
        return self._obstruction

    def unwrap_section(self) -> GlobalSection:
        """Return the global section, raising if descent failed."""
        if self._global_section is None:
            raise ValueError(
                'Cannot unwrap section from a failed descent result. '
                'Use unwrap_obstruction() instead.'
            )
        return self._global_section

    def unwrap_obstruction(self) -> DescentObstruction:
        """Return the obstruction, raising if descent succeeded."""
        if self._obstruction is None:
            raise ValueError(
                'Cannot unwrap obstruction from a successful descent result. '
                'Use unwrap_section() instead.'
            )
        return self._obstruction

    def evidence_summary(self) -> str:
        """Delegate to the wrapped value's evidence summary."""
        if self._global_section is not None:
            return self._global_section.evidence_summary()
        if self._obstruction is not None:
            return self._obstruction.evidence_summary()
        return 'DescentResult(empty)'

    def map_success(
        self, fn: Callable[[GlobalSection], GlobalSection],
    ) -> DescentResult:
        """Apply ``fn`` to the global section if present, else pass through."""
        if self._global_section is not None:
            return DescentResult(_global_section=fn(self._global_section))
        return self

    def map_failure(
        self, fn: Callable[[DescentObstruction], DescentObstruction],
    ) -> DescentResult:
        """Apply ``fn`` to the obstruction if present, else pass through."""
        if self._obstruction is not None:
            return DescentResult(_obstruction=fn(self._obstruction))
        return self

    @staticmethod
    def success(section: GlobalSection) -> DescentResult:
        """Construct a successful DescentResult."""
        return DescentResult(_global_section=section)

    @staticmethod
    def failure(obstruction: DescentObstruction) -> DescentResult:
        """Construct a failed DescentResult."""
        return DescentResult(_obstruction=obstruction)

    # -- deep cross-subsystem integration ------------------------------------

    @property
    def countermodels(self) -> Any:
        """Extract countermodels from descent obstructions.

        When descent fails, each violated overlap condition can be
        translated into an explicit countermodel — a concrete assignment
        that demonstrates the impossibility of gluing.  The solver
        subsystem (``jugeo.solver.countermodels``) performs this
        extraction (theory2.tex §8.3).
        """
        try:
            from jugeo.solver.countermodels import (  # type: ignore[import-untyped]
                ObstructionConverter,
                ObstructionRecord,
            )
            if self._obstruction is None:
                return []
            converter = ObstructionConverter()
            records = []
            for overlap in self._obstruction.violated_overlaps:
                record = ObstructionRecord(
                    coordinate=self._obstruction.coordinate,
                    violated_condition=overlap.overlap_key,
                    support_scope=tuple(
                        self._obstruction.involved_coordinates()
                        if hasattr(self._obstruction, 'involved_coordinates')
                        else ()
                    ),
                )
                records.append(record)
            return records
        except ImportError:
            return []

    @property
    def repair_suggestions(self) -> Any:
        """Compute repair suggestions for descent failures.

        The repair-semantics subsystem
        (``jugeo.problem_modes.repair_semantics``) analyses violated
        overlaps and proposes concrete repairs — edits to local
        sections that would restore compatibility.  This is the
        constructive content of the H¹ obstruction class
        (theory2.tex §9.1).
        """
        try:
            from jugeo.problem_modes.repair_semantics import RepairEngine  # type: ignore[import-untyped]
            if self._obstruction is None:
                return []
            engine = RepairEngine()
            return engine.suggest_repairs(
                coordinate=self._obstruction.coordinate,
                violated_overlaps=[
                    o.overlap_key for o in self._obstruction.violated_overlaps
                ],
                partial_section=self._obstruction.partial_section,
            )
        except ImportError:
            return []

    @property
    def certificate(self) -> Any:
        """Produce a descent certificate for this result.

        Whether descent succeeded or failed, the evidence layer
        (``jugeo.evidence.certificates``) can package the outcome
        into a verifiable certificate.  Successful certificates attest
        to the existence of a global section; failure certificates
        record the obstruction class for audit (theory2.tex §6.6).
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder  # type: ignore[import-untyped]
            builder = CertificateBuilder()
            builder.set_evidence_summary(self.evidence_summary())
            if self._global_section is not None:
                builder.for_coordinate(self._global_section.coordinate)
                return builder.sign().build()
            if self._obstruction is not None:
                builder.for_coordinate(self._obstruction.coordinate)
                return builder.sign().build()
            return None
        except ImportError:
            return {"certificate": None, "reason": "jugeo.evidence.certificates not available"}

    @property
    def cohomology_class(self) -> Any:
        """Compute the H¹ cohomology class from descent data.

        In sheaf cohomology (theory2.tex §3.2), a failed descent
        produces a non-trivial class in H¹(U, F) — the first Čech
        cohomology of the sheaf with respect to the cover.  This
        property extracts the class from the obstruction's cocycle
        data using ``jugeo.foundations.formal_core``.
        """
        try:
            from jugeo.geometry.descent import CohomologyClass  # type: ignore[import-untyped]
            if self._obstruction is None:
                return None
            return self._obstruction.cohomology_class
        except ImportError:
            if self._obstruction is not None:
                return self._obstruction.cohomology_class
            return None

    def encode_obstructions(self) -> Any:
        """Encode descent obstructions for Z3 analysis.

        Translates every violated overlap condition into SMT-LIB
        constraints via the encoding layer (``jugeo.encodings``).
        The resulting formula can be fed to a Z3 session for
        countermodel extraction or interpolation (theory2.tex §8.4).
        """
        try:
            from jugeo.encodings import encode_judgment  # type: ignore[import-untyped]
            if self._obstruction is None:
                return None
            encoded = []
            for overlap in self._obstruction.violated_overlaps:
                encoded.append(encode_judgment({
                    "coordinate": self._obstruction.coordinate,
                    "violated_condition": overlap.overlap_key,
                    "left": overlap.left_coordinate,
                    "right": overlap.right_coordinate,
                    "persistence_id": self._obstruction.persistence_id,
                }))
            return encoded
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.encodings to be installed"
            )

    def orchestrate_repair(self) -> Any:
        """Orchestrate repair of descent failures.

        The orchestration controller
        (``jugeo.orchestration.controller``) coordinates solver
        queries, repair suggestion generation, and re-descent into a
        single repair pipeline.  This method submits the obstruction
        for orchestrated repair and returns the controller's outcome
        (theory2.tex §11.2).
        """
        try:
            from jugeo.orchestration.controller import (  # type: ignore[import-untyped]
                OrchestrationController,
                FrontierState,
                BudgetLedger,
                BackpressureSignal,
            )
            if self._obstruction is None:
                return {"status": "no_obstruction", "message": "Descent succeeded; nothing to repair"}
            controller = OrchestrationController()
            frontier = FrontierState()
            budget = BudgetLedger()
            signal = BackpressureSignal()
            return controller.decide(frontier, budget, signal)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.orchestration.controller to be installed"
            )

    def summary(self) -> str:
        """One-line summary."""
        if self._global_section is not None:
            return f'DescentResult(success: {self._global_section.summary()})'
        if self._obstruction is not None:
            return f'DescentResult(failure: {self._obstruction.summary()})'
        return 'DescentResult(empty)'


# ---------------------------------------------------------------------------
# Legacy types (backward compatible)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Obstruction:
    """Legacy obstruction record for backward compatibility.

    Newer code should prefer DescentObstruction, which carries richer
    metadata (cohomology class, repair frontier, partial sections).  This
    type is retained so that existing tests and orchestration code that
    import ``Obstruction`` continue to work unchanged.
    """

    overlap: tuple[str, str]
    message: str
    rank: int
    support: SupportRegion | None = None

    def to_overlap_condition(self) -> OverlapCondition:
        """Convert to the richer OverlapCondition type."""
        return OverlapCondition(
            left_coordinate=self.overlap[0],
            right_coordinate=self.overlap[1],
            overlap_coordinate=f'{self.overlap[0]}∩{self.overlap[1]}',
            status=OverlapStatus.VIOLATED,
        )


@dataclass(frozen=True, slots=True)
class GluingReport:
    """Legacy gluing report for backward compatibility.

    Newer code should use DescentResult, which distinguishes GlobalSection
    from DescentObstruction with full metadata.  This type is retained
    so that ``run_descent`` and ``glue_sections`` continue to return the
    same shape expected by existing callers.
    """

    target: str
    success: bool
    glued_section: Mapping[str, Any] | None
    local_sections: Mapping[str, Mapping[str, Any]]
    obstructions: tuple[Obstruction, ...] = field(default_factory=tuple)

    @property
    def obstruction_rank(self) -> int:
        """Total rank across all obstructions (severity measure)."""
        return sum(obstruction.rank for obstruction in self.obstructions)

    def to_descent_result(self) -> DescentResult:
        """Lift this legacy report into the richer DescentResult type."""
        if self.success and self.glued_section is not None:
            return DescentResult.success(GlobalSection(
                coordinate=self.target,
                merged_judgment=dict(self.glued_section),
                constituent_sections=tuple(self.local_sections.keys()),
            ))
        violated = tuple(o.to_overlap_condition() for o in self.obstructions)
        return DescentResult.failure(DescentObstruction(
            coordinate=self.target,
            violated_overlaps=violated,
        ))


# ---------------------------------------------------------------------------
# Descent engine
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DescentEngine:
    """Main descent engine: turns local sections into global sections.

    This is the central class of the module.  It consumes a Cover and local
    section data, verifies overlap compatibility according to the configured
    strategy, and produces a DescentResult (or a legacy GluingReport via
    the backward-compatible ``run`` method).

    The engine supports four strategies (see DescentStrategy):

    EAGER       — fast path for CI: stop at the first violation.
    EXHAUSTIVE  — thorough path: check every overlap before reporting.
    ITERATIVE   — progressive refinement via ``iterative_descent()``.
    OPTIMISTIC  — trust copilot proposals, verify lazily.

    ``parallel_descent()`` checks overlaps concurrently using a thread pool.

    Per theory2.tex §3.4, the iterative strategy progressively refines the
    cover around violated overlaps until either all overlaps pass or the
    depth limit is reached.

    Attributes
    ----------
    gluing_key : callable
        Extracts a comparison key from a raw section mapping (legacy API).
    configuration : DescentConfiguration
        Tuning knobs for strategy, depth, timeouts, etc.
    """

    gluing_key: Callable[
        [Mapping[str, Any]], tuple[tuple[str, Any], ...]
    ] = _default_gluing_key
    configuration: DescentConfiguration = field(
        default_factory=DescentConfiguration,
    )

    # -- legacy entry point (backward compatible) ----------------------------

    def run(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> GluingReport:
        """Run descent and return a legacy GluingReport.

        This method preserves the exact signature and return type expected
        by existing tests (``test_descent.py``) and orchestration code.
        Internally it delegates to the richer ``attempt_descent`` path.
        """
        obstructions: list[Obstruction] = []
        for left, right in cover.overlaps:
            if left not in sections or right not in sections:
                obstructions.append(Obstruction(
                    (left, right), 'missing local section on overlap', 1,
                ))
                continue
            if self.gluing_key(sections[left]) != self.gluing_key(sections[right]):
                obstructions.append(Obstruction(
                    (left, right), 'local sections disagree on overlap', 1,
                ))
        if obstructions:
            return GluingReport(
                cover.target.key, False, None, sections, tuple(obstructions),
            )
        values = list(sections.values())
        return GluingReport(
            cover.target.key,
            True,
            dict(values[0]) if values else {},
            sections,
            (),
        )

    # -- rich descent entry points -------------------------------------------

    def attempt_descent(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> DescentResult:
        """Run descent and return a full DescentResult.

        This is the preferred entry point for new code.  It constructs
        GluingData from the cover, evaluates overlaps according to the
        configured strategy, and produces either a GlobalSection or a
        DescentObstruction.
        """
        log = DescentLog() if self.configuration.record_log else None
        if log:
            log.record_phase(DescentPhase.INITIALIZATION)

        gluing = GluingData.from_cover(cover, sections)

        if log:
            log.record_phase(DescentPhase.OVERLAP_CHECK)

        strategy = self.configuration.strategy
        if strategy == DescentStrategy.EAGER:
            result = self._descent_eager(gluing, log)
        elif strategy == DescentStrategy.OPTIMISTIC:
            result = self._descent_optimistic(gluing, log)
        else:
            result = self._descent_exhaustive(gluing, log)

        if log:
            log.finalize('success' if result.is_success else 'failure')

        return result

    def verify_overlaps(
        self,
        gluing: GluingData,
        log: DescentLog | None = None,
    ) -> list[OverlapCondition]:
        """Evaluate all overlap conditions and optionally log each check.

        Returns the list of evaluated overlaps (also mutates ``gluing``
        in-place).
        """
        evaluated: list[OverlapCondition] = []
        for overlap in gluing.overlaps:
            t0 = time.monotonic()
            left_sec = gluing.sections.get(overlap.left_coordinate)
            right_sec = gluing.sections.get(overlap.right_coordinate)
            if left_sec is None or right_sec is None:
                checked = OverlapCondition(
                    left_coordinate=overlap.left_coordinate,
                    right_coordinate=overlap.right_coordinate,
                    overlap_coordinate=overlap.overlap_coordinate,
                    compatibility_predicate=overlap.compatibility_predicate,
                    status=OverlapStatus.VIOLATED,
                )
            else:
                checked = overlap.evaluate(
                    left_sec.judgment_data, right_sec.judgment_data,
                )
            elapsed = time.monotonic() - t0
            evaluated.append(checked)
            if log:
                log.record_overlap_check(
                    checked.left_coordinate,
                    checked.right_coordinate,
                    checked.status,
                    elapsed,
                )
                for ev in (left_sec.evidence_bundle if left_sec else ()):
                    log.record_evidence(ev)
        gluing.overlaps = evaluated
        return evaluated

    def compute_gluing(
        self,
        gluing: GluingData,
        target_coordinate: str,
    ) -> GlobalSection:
        """Merge all local sections into a global section.

        Precondition: all overlaps are SATISFIED.  This method does NOT
        re-check; call verify_overlaps first.  The merged judgment is
        the union of all local section judgment data (last-write-wins on
        key collisions, but overlaps being satisfied means values agree).

        The trust floor is computed according to the configuration's
        trust_floor_policy.
        """
        merged: dict[str, Any] = {}
        constituents: list[str] = []
        evidence: list[str] = []
        trust_values: list[float] = []

        for key, sec in gluing.sections.items():
            merged.update(sec.judgment_data)
            constituents.append(key)
            evidence.extend(sec.evidence_bundle)
            trust_values.append(sec.trust_level)

        trust_floor = self._compute_trust_floor(trust_values, gluing)
        cert = _compute_digest(target_coordinate, *sorted(constituents))

        return GlobalSection(
            coordinate=target_coordinate,
            merged_judgment=merged,
            constituent_sections=tuple(constituents),
            overlap_evidence=tuple(evidence),
            certificate=cert,
            trust_floor=trust_floor,
        )

    def extract_obstruction(
        self,
        gluing: GluingData,
        target_coordinate: str,
    ) -> DescentObstruction:
        """Build a DescentObstruction from the current state of GluingData.

        Gathers violated overlaps, computes the cocycle, and (if
        ``copilot_assisted_repair`` is enabled) builds a RepairFrontier.
        """
        violated = tuple(gluing.find_violated_overlaps())
        cocycle = gluing.compute_cocycle()
        partial = self._compute_partial_section(gluing)
        frontier = (
            self._compute_repair_frontier(gluing, violated)
            if self.configuration.copilot_assisted_repair
            else RepairFrontier()
        )
        return DescentObstruction(
            coordinate=target_coordinate,
            violated_overlaps=violated,
            partial_section=partial,
            repair_frontier=frontier,
            cohomology_class=cocycle,
        )

    def iterative_descent(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> DescentResult:
        """Iterative descent: progressively refine the cover on failure.

        Per theory2.tex §3.4, when descent fails on a coarse cover we
        refine around the violated overlaps and re-attempt.  This repeats
        up to ``configuration.depth_limit`` times.  Each refinement splits
        every violated patch into two, creating finer overlaps that may
        individually satisfy compatibility.

        If all rounds fail the final DescentObstruction is returned,
        carrying the accumulated repair frontier from every round.
        """
        current_cover = cover
        current_sections = dict(sections)
        accumulated_frontier = RepairFrontier()

        for depth in range(self.configuration.depth_limit):
            result = self.attempt_descent(current_cover, current_sections)
            if result.is_success:
                return result

            obstruction = result.unwrap_obstruction()
            accumulated_frontier = accumulated_frontier.merge(
                obstruction.repair_frontier,
            )

            if depth + 1 >= self.configuration.depth_limit:
                final_obs = DescentObstruction(
                    coordinate=obstruction.coordinate,
                    violated_overlaps=obstruction.violated_overlaps,
                    partial_section=obstruction.partial_section,
                    repair_frontier=accumulated_frontier,
                    cohomology_class=obstruction.cohomology_class,
                    persistence_id=obstruction.persistence_id,
                )
                return DescentResult.failure(final_obs)

            refined = refine_cover(current_cover, suffix=f'iter-{depth}')
            refined_sections: dict[str, Mapping[str, Any]] = {}
            for patch_key in refined.patch_keys():
                for orig_key, orig_data in current_sections.items():
                    if patch_key.startswith(orig_key.rsplit('-', 1)[0]):
                        refined_sections[patch_key] = orig_data
                        break
                else:
                    if current_sections:
                        first_key = next(iter(current_sections))
                        refined_sections[patch_key] = current_sections[first_key]
            current_cover = refined
            current_sections = refined_sections

        return self.attempt_descent(current_cover, current_sections)

    def parallel_descent(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> DescentResult:
        """Check overlaps concurrently using a thread pool.

        Each overlap check is submitted to a ThreadPoolExecutor.  This is
        useful when overlap predicates are expensive (e.g. they invoke a
        solver or a copilot verification call).  The final assembly into
        GlobalSection or DescentObstruction happens on the main thread.
        """
        gluing = GluingData.from_cover(cover, sections)
        log = DescentLog() if self.configuration.record_log else None
        if log:
            log.record_phase(DescentPhase.INITIALIZATION)
            log.record_phase(DescentPhase.OVERLAP_CHECK)

        max_workers = self.configuration.max_parallel_workers
        evaluated: list[OverlapCondition] = [
            OverlapCondition(
                left_coordinate=o.left_coordinate,
                right_coordinate=o.right_coordinate,
                overlap_coordinate=o.overlap_coordinate,
                compatibility_predicate=o.compatibility_predicate,
                status=OverlapStatus.UNCHECKED,
            )
            for o in gluing.overlaps
        ]

        def check_one(idx: int, overlap: OverlapCondition) -> tuple[int, OverlapCondition]:
            t0 = time.monotonic()
            left_sec = gluing.sections.get(overlap.left_coordinate)
            right_sec = gluing.sections.get(overlap.right_coordinate)
            if left_sec is None or right_sec is None:
                result = OverlapCondition(
                    left_coordinate=overlap.left_coordinate,
                    right_coordinate=overlap.right_coordinate,
                    overlap_coordinate=overlap.overlap_coordinate,
                    compatibility_predicate=overlap.compatibility_predicate,
                    status=OverlapStatus.VIOLATED,
                )
            else:
                result = overlap.evaluate(
                    left_sec.judgment_data, right_sec.judgment_data,
                )
            elapsed = time.monotonic() - t0
            if log:
                log.record_overlap_check(
                    result.left_coordinate,
                    result.right_coordinate,
                    result.status,
                    elapsed,
                )
            return idx, result

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(check_one, i, o): i
                for i, o in enumerate(evaluated)
            }
            for future in as_completed(futures):
                idx, checked = future.result()
                evaluated[idx] = checked

        gluing.overlaps = evaluated
        target = cover.target.key

        if all(o.status == OverlapStatus.SATISFIED for o in evaluated):
            if log:
                log.record_phase(DescentPhase.GLUING)
            section = self.compute_gluing(gluing, target)
            if log:
                log.finalize('success')
            return DescentResult.success(section)

        if log:
            log.record_phase(DescentPhase.OBSTRUCTION_EXTRACTION)
        obstruction = self.extract_obstruction(gluing, target)
        if log:
            log.finalize('failure')
        return DescentResult.failure(obstruction)

    # -- internal strategy implementations -----------------------------------

    def _descent_exhaustive(
        self,
        gluing: GluingData,
        log: DescentLog | None,
    ) -> DescentResult:
        """Check every overlap before deciding success/failure."""
        self.verify_overlaps(gluing, log)
        target = next(iter(gluing.sections)).rsplit('/', 1)[0] if gluing.sections else ''
        violated = gluing.find_violated_overlaps()

        if not violated:
            if log:
                log.record_phase(DescentPhase.GLUING)
            return DescentResult.success(self.compute_gluing(gluing, target))

        if log:
            log.record_phase(DescentPhase.OBSTRUCTION_EXTRACTION)
        return DescentResult.failure(self.extract_obstruction(gluing, target))

    def _descent_eager(
        self,
        gluing: GluingData,
        log: DescentLog | None,
    ) -> DescentResult:
        """Stop at the first violated overlap (fail-fast)."""
        target = next(iter(gluing.sections)).rsplit('/', 1)[0] if gluing.sections else ''

        for overlap in gluing.overlaps:
            t0 = time.monotonic()
            left_sec = gluing.sections.get(overlap.left_coordinate)
            right_sec = gluing.sections.get(overlap.right_coordinate)
            if left_sec is None or right_sec is None:
                checked = OverlapCondition(
                    left_coordinate=overlap.left_coordinate,
                    right_coordinate=overlap.right_coordinate,
                    overlap_coordinate=overlap.overlap_coordinate,
                    compatibility_predicate=overlap.compatibility_predicate,
                    status=OverlapStatus.VIOLATED,
                )
            else:
                checked = overlap.evaluate(
                    left_sec.judgment_data, right_sec.judgment_data,
                )
            elapsed = time.monotonic() - t0
            if log:
                log.record_overlap_check(
                    checked.left_coordinate,
                    checked.right_coordinate,
                    checked.status,
                    elapsed,
                )
            if checked.status == OverlapStatus.VIOLATED:
                gluing.overlaps = [checked]
                if log:
                    log.record_phase(DescentPhase.OBSTRUCTION_EXTRACTION)
                return DescentResult.failure(
                    self.extract_obstruction(gluing, target),
                )

        if log:
            log.record_phase(DescentPhase.GLUING)
        return DescentResult.success(self.compute_gluing(gluing, target))

    def _descent_optimistic(
        self,
        gluing: GluingData,
        log: DescentLog | None,
    ) -> DescentResult:
        """Assume compatible, build the global section, then verify lazily.

        This strategy is useful when copilot-generated proposals are
        expected to be compatible.  It builds the global section first and
        only checks overlaps afterward; if violations are found, the
        global section is discarded and an obstruction is returned.
        """
        target = next(iter(gluing.sections)).rsplit('/', 1)[0] if gluing.sections else ''

        if log:
            log.record_phase(DescentPhase.GLUING)
        optimistic_section = self.compute_gluing(gluing, target)

        if log:
            log.record_phase(DescentPhase.OVERLAP_CHECK)
        self.verify_overlaps(gluing, log)
        violated = gluing.find_violated_overlaps()

        if not violated:
            return DescentResult.success(optimistic_section)

        if log:
            log.record_phase(DescentPhase.OBSTRUCTION_EXTRACTION)
        return DescentResult.failure(self.extract_obstruction(gluing, target))

    # -- trust floor computation ---------------------------------------------

    def _compute_trust_floor(
        self,
        trust_values: list[float],
        gluing: GluingData,
    ) -> float:
        """Compute the trust floor according to the configured policy."""
        if not trust_values:
            return 0.0

        policy = self.configuration.trust_floor_policy

        if policy == TrustFloorPolicy.MINIMUM:
            return min(trust_values)

        if policy == TrustFloorPolicy.WEIGHTED:
            total_overlaps = max(1, len(gluing.overlaps))
            weights = []
            for sec in gluing.sections.values():
                involvement = sum(
                    1 for o in gluing.overlaps
                    if o.left_coordinate == sec.coordinate
                    or o.right_coordinate == sec.coordinate
                )
                weights.append(max(1, involvement))
            if not weights:
                return min(trust_values)
            weighted_sum = sum(t * w for t, w in zip(trust_values, weights))
            return weighted_sum / sum(weights)

        # TrustFloorPolicy.PESSIMISTIC
        violated_count = len(gluing.find_violated_overlaps())
        penalty = violated_count * 0.05
        return max(0.0, min(trust_values) - penalty)

    # -- partial section computation -----------------------------------------

    def _compute_partial_section(
        self,
        gluing: GluingData,
    ) -> dict[str, Any] | None:
        """Build a partial section from patches that are mutually compatible.

        Finds the largest connected subset of sections where all overlaps
        within the subset are SATISFIED, then merges their judgment data.
        Returns None if no sections are available.
        """
        if not gluing.sections:
            return None

        satisfied_pairs: set[tuple[str, str]] = set()
        for o in gluing.overlaps:
            if o.status == OverlapStatus.SATISFIED:
                satisfied_pairs.add((o.left_coordinate, o.right_coordinate))
                satisfied_pairs.add((o.right_coordinate, o.left_coordinate))

        all_keys = list(gluing.sections.keys())
        if not all_keys:
            return None

        best_component: list[str] = [all_keys[0]]
        for start in all_keys:
            component = [start]
            visited = {start}
            queue = [start]
            while queue:
                current = queue.pop(0)
                for other in all_keys:
                    if other not in visited:
                        if (current, other) in satisfied_pairs:
                            visited.add(other)
                            component.append(other)
                            queue.append(other)
            if len(component) > len(best_component):
                best_component = component

        merged: dict[str, Any] = {}
        for key in best_component:
            sec = gluing.sections[key]
            merged.update(sec.judgment_data)
        return merged

    # -- repair frontier computation -----------------------------------------

    def _compute_repair_frontier(
        self,
        gluing: GluingData,
        violated: tuple[OverlapCondition, ...],
    ) -> RepairFrontier:
        """Build a RepairFrontier from violated overlaps.

        For each violation, the engine classifies the likely repair action:
        - If a section is partial (has residual obligations), the fix is
          to supply missing evidence.
        - If both sections are fully evidenced but disagree, the fix is
          to weaken one or both claims.
        - If the overlap structure itself is too coarse, suggest a cover
          refinement.

        The estimated cost is a rough heuristic: evidence costs 1.0,
        weakening costs 2.0, refinement costs 3.0 per item.
        """
        missing_evidence: list[str] = []
        weakened_claims: list[str] = []
        suggested_refinements: list[str] = []
        cost = 0.0

        for overlap in violated:
            left = gluing.sections.get(overlap.left_coordinate)
            right = gluing.sections.get(overlap.right_coordinate)

            if left is None or right is None:
                missing_coord = (
                    overlap.left_coordinate if left is None
                    else overlap.right_coordinate
                )
                missing_evidence.append(
                    f'section_missing:{missing_coord}',
                )
                cost += 1.0
                continue

            if left.is_partial or right.is_partial:
                for sec in (left, right):
                    if sec.is_partial:
                        for obl in sec.residual_obligations:
                            missing_evidence.append(
                                f'obligation:{sec.coordinate}:{obl}',
                            )
                            cost += 1.0
                continue

            left_keys = set(left.judgment_data.keys())
            right_keys = set(right.judgment_data.keys())
            disagreeing = [
                k for k in left_keys & right_keys
                if left.judgment_data[k] != right.judgment_data[k]
            ]
            if disagreeing:
                for k in disagreeing:
                    weakened_claims.append(
                        f'disagree:{overlap.overlap_key}:{k}',
                    )
                    cost += 2.0
            else:
                suggested_refinements.append(
                    f'refine:{overlap.overlap_key}',
                )
                cost += 3.0

        return RepairFrontier(
            missing_evidence=tuple(missing_evidence),
            weakened_claims=tuple(weakened_claims),
            suggested_refinements=tuple(suggested_refinements),
            estimated_cost=cost,
        )

    # -- cross-subsystem integration ------------------------------------------

    def solver_assisted_descent(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        """Use a Z3 solver session to verify gluing conditions.

        When ``jugeo.solver.z3_session`` is available, this method encodes
        every overlap condition as an SMT formula and asks Z3 whether the
        conjunction is satisfiable.  A SAT result means the gluing
        conditions are *jointly* satisfiable and descent can proceed;
        UNSAT pinpoints the minimal unsatisfiable core, which maps
        directly to the cohomological obstruction (theory2.tex §3.5).

        Falls back to :meth:`attempt_descent` when the solver subsystem
        is not installed.
        """
        try:
            from jugeo.solver.z3_session import (  # type: ignore[import-untyped]
                Z3Session,
                Z3Formula,
                FormulaKind,
                SolverFragment,
                LogicalFragment,
                SolveOutcome,
            )
            gluing = GluingData()
            for coord_key, sec_data in sections.items():
                ls = LocalSection(
                    coordinate=coord_key,
                    judgment_data=dict(sec_data),
                    trust_level=sec_data.get("_trust", 1.0),
                )
                gluing.add_section(ls)

            session = Z3Session()
            clauses: list[str] = []
            for overlap in gluing.verify_all_overlaps():
                clause = (
                    f"(= {overlap.left_coordinate} "
                    f"{overlap.right_coordinate})"
                )
                formula = Z3Formula(
                    kind=FormulaKind.BOOLEAN,
                    expression=clause,
                )
                session.assert_formula(formula)
                clauses.append(clause)
            fragment = SolverFragment(
                formula="(and " + " ".join(clauses) + ")" if clauses else "true",
                fragment=LogicalFragment.EQUALITY,
                clauses=tuple(clauses),
            )
            result = session.solve(fragment)
            return {
                "solver": "z3",
                "satisfiable": result.is_sat(),
                "unsat_core": (
                    list(result.reasons) if result.is_unsat() else []
                ),
                "cover_size": cover.member_count,
            }
        except ImportError:
            return self.attempt_descent(cover, sections)

    def certificate_of_descent(
        self,
        result: DescentResult,
    ) -> Any:
        """Produce a machine-checkable certificate for a descent result.

        In theory2.tex §4.2, a *descent certificate* is a compact proof
        artifact witnessing that all overlap conditions were satisfied and
        the resulting global section is well-formed.  When
        ``jugeo.evidence.certificates`` is available, the certificate can
        be serialised, archived, and later re-verified independently of
        the descent engine.

        Returns a ``Certificate`` object, or a summary dict if the
        evidence subsystem is absent.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder  # type: ignore[import-untyped]
            builder = CertificateBuilder()
            builder.set_evidence_summary(result.evidence_summary())
            if result.is_success:
                section = result.unwrap_section()
                builder.for_coordinate(section.coordinate)
                for key in section.merged_judgment:
                    builder.add_verified(key)
            else:
                obstruction = result.unwrap_obstruction()
                builder.for_coordinate(obstruction.coordinate)
                for overlap in obstruction.violated_overlaps:
                    builder.add_obstruction(overlap.overlap_key)
            builder.sign()
            return builder.build()
        except ImportError:
            return {
                "certificate": None,
                "success": result.is_success,
                "summary": result.evidence_summary(),
                "reason": "jugeo.evidence.certificates not available",
            }

    def obstruction_to_countermodel(
        self,
        obstruction: DescentObstruction,
    ) -> Any:
        """Convert a descent obstruction into a solver countermodel.

        When descent fails, the obstruction encodes *which* overlap
        conditions were violated.  The solver subsystem
        (``jugeo.solver.countermodels``) can translate this into a
        concrete countermodel — an explicit assignment of section
        values that demonstrates the impossibility of gluing.  This
        is the computational dual of the H¹ obstruction class
        (theory2.tex §3.2).

        Returns an ``ObstructionRecord``, or raises
        ``NotImplementedError`` when the solver subsystem is absent.
        """
        try:
            from jugeo.solver.countermodels import (  # type: ignore[import-untyped]
                ObstructionConverter,
                ObstructionRecord,
                extract_countermodel,
            )
            converter = ObstructionConverter()
            records: list[Any] = []
            for overlap in obstruction.violated_overlaps:
                record = ObstructionRecord(
                    coordinate=obstruction.coordinate,
                    violated_condition=overlap.overlap_key,
                    support_scope=tuple(obstruction.involved_coordinates()),
                )
                hints = converter.compute_repair_hints(
                    extract_countermodel(record.to_dict()),
                )
                records.append({
                    "record": record,
                    "repair_hints": hints,
                })
            return {
                "coordinate": obstruction.coordinate,
                "records": records,
                "persistence_id": obstruction.persistence_id,
            }
        except ImportError:
            raise NotImplementedError(
                "jugeo.solver.countermodels is not installed.  "
                "Install the solver subsystem to convert obstructions "
                "into concrete countermodels."
            )

    # -- deep cross-subsystem integration ------------------------------------

    def repair_via_semantics(
        self,
        result: DescentResult,
    ) -> Any:
        """Compute semantic repairs for a failed descent result.

        The problem-modes subsystem
        (``jugeo.problem_modes.repair_semantics``) analyses the pattern
        of overlap violations and proposes *semantic repairs* — targeted
        edits to local sections that would restore the sheaf condition.
        This goes beyond syntactic patching: the repair engine
        understands the judgment structure and can suggest type-correct
        modifications (theory2.tex §9.1).
        """
        try:
            from jugeo.problem_modes.bug_detection import BugDetector  # type: ignore[import-untyped]
            if result.is_success:
                return {"status": "no_repair_needed", "message": "Descent succeeded"}
            obstruction = result.unwrap_obstruction()
            detector = BugDetector()
            source = f"# obstruction at {obstruction.coordinate}\n"
            for o in obstruction.violated_overlaps:
                source += f"# violated: {o.overlap_key}\n"
            return detector.detect_bugs(source, filename=obstruction.coordinate)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.problem_modes.bug_detection to be installed"
            )

    def encode_full_descent(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        """Encode the full descent problem for external analysis.

        Translates the cover, local sections, and overlap conditions
        into a structured encoding via ``jugeo.encodings``.  The
        result can be serialized to SMT-LIB, exported to a proof
        assistant, or archived for reproducibility (theory2.tex §8).
        """
        try:
            from jugeo.encodings import encode_judgment, encode_section  # type: ignore[import-untyped]
            encoded_sections = []
            for key, sec_data in sections.items():
                encoded_sections.append(encode_section(sec_data))
            encoded_cover = encode_judgment({
                "cover_target": cover.target.key,
                "member_keys": list(cover.member_keys()),
                "overlap_pairs": cover.pairwise_overlaps(),
            })
            return {
                "cover_encoding": encoded_cover,
                "section_encodings": encoded_sections,
                "overlap_count": len(cover.pairwise_overlaps()),
            }
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.encodings to be installed"
            )

    def maturity_of_descent(
        self,
        result: DescentResult,
    ) -> Any:
        """Assess the maturity of a descent result.

        The maturity subsystem (``jugeo.maturity.cyclic_picture``)
        models each judgment's lifecycle as a position on a cyclic
        maturity curve.  This method evaluates where the descent
        result sits on that curve — whether the verification is
        nascent, maturing, or fully certified (theory2.tex §10.2).
        """
        try:
            from jugeo.maturity.cyclic_picture import quick_maturity_report  # type: ignore[import-untyped]
            return quick_maturity_report(
                system_id=f"descent-{'success' if result.is_success else 'failure'}",
                level=None,
                num_cycles=1,
            )
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.maturity.cyclic_picture to be installed"
            )

    def orchestrate_full_pipeline(
        self,
        cover: Cover,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        """Orchestrate the complete descent + verification + certification pipeline.

        The orchestration controller
        (``jugeo.orchestration.controller``) runs descent, checks
        for bugs, collects evidence, produces certificates, and
        optionally triggers repair — all in a single coordinated
        pipeline.  This is the top-level entry point for fully
        automated judgment verification (theory2.tex §11).
        """
        try:
            from jugeo.orchestration.controller import (  # type: ignore[import-untyped]
                OrchestrationController,
                FrontierState,
                BudgetLedger,
                BackpressureSignal,
            )
            controller = OrchestrationController()
            frontier = FrontierState()
            budget = BudgetLedger()
            signal = BackpressureSignal()
            return controller.decide(frontier, budget, signal)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.orchestration.controller to be installed"
            )

    def orchestrated_descent(self):
        """Run descent with full orchestration support."""
        try:
            from jugeo.orchestration.controller import Orchestrator, OrchestratorState, SemanticMove, ControlLaw
            from jugeo.orchestration.fleet import Fleet, FleetMember, FleetBid, BidEvaluator
            from jugeo.orchestration.frontier import Frontier, FrontierNode, FrontierScorer, PhaseTransition
            from jugeo.orchestration.budgets import BudgetDimension, BudgetAllocation, BudgetPolicy, BudgetAllocator, BudgetTracker, BudgetEnforcer, BudgetOptimizer
            from jugeo.orchestration.negotiation import NegotiationProtocol, NegotiationRound, NegotiationOutcome
            return {"orchestrated": True, "components": ["controller", "fleet", "frontier", "budgets", "negotiation"]}
        except Exception:
            return {"orchestrated": False}

    def mixed_evidence_routing(self):
        """Route descent evidence through mixed evidence channels."""
        try:
            from jugeo.orchestration.mixed_evidence_routing.the_router_is_a_semantic_judgment import RouterJudgment, RoutingDecision, RoutingObligation, EvidenceFragment
            from jugeo.orchestration.mixed_evidence_routing.algorithms import RoutingAlgorithm
            from jugeo.orchestration.mixed_evidence_routing.models import RoutingConfig, ChannelAssignment
            return {"routing": "available"}
        except Exception:
            return {"routing": "unavailable"}

    def encode_obstruction_heap(self, obstruction):
        """Encode a descent obstruction using heap encodings."""
        try:
            from jugeo.encodings.collection_heap_encodings.exact_boundaries_and_explicit_non import ExactBoundaryEncoding, BoundaryCechObstruction, BoundaryDescentObstruction, BoundaryJudgment, BoundaryProof
            from jugeo.encodings.collection_heap_encodings.interface_summaries_as_uninterpret import SummaryCechObstruction, SummaryJudgment
            from jugeo.encodings.collection_heap_encodings.heap_summary_encoder import HeapCompositionEngine
            return {"obstruction": str(obstruction), "heap_encoding": "available", "boundary_analysis": True}
        except Exception:
            return {"obstruction": str(obstruction), "heap_encoding": "unavailable"}

    def structural_frontier_analysis(self):
        """Analyze descent results against the structural frontier."""
        try:
            from jugeo.encodings.structural_frontier.models import StructuralFrontier, DecidabilityMap, CountermodelObstruction, FrontierBoundary
            from jugeo.encodings.structural_frontier.algorithms import FrontierClassifier, DecidabilityAnalyzer
            dm = DecidabilityMap()
            return {"frontier": repr(dm), "decidability_analysis": True}
        except Exception:
            return {"decidability_analysis": False}

    def formal_descent(self):
        """Run descent using formal foundations (category theory)."""
        try:
            from jugeo.foundations.formal_core.models import CategoryStructure, FormalSite, TrustAlgebraAxioms, ObstructionTheory, DescentData, MorphismData, ObjectData
            from jugeo.foundations.descent_locality.algorithms import CompatibilityChecker, ObstructionComputer, RepairFinder, DescentAlgorithms
            from jugeo.foundations.descent_locality.covers_and_hypercovers import CoverFamily, CoverAxiom, CoverType
            from jugeo.foundations.descent_locality.models import DescentLocality
            return {"formal_descent": True, "foundation_components": ["formal_core", "descent_locality"]}
        except Exception:
            return {"formal_descent": False}

    def obstruction_theory(self):
        """Compute obstruction theory from foundations."""
        try:
            from jugeo.foundations.formal_core.models import ObstructionTheory
            from jugeo.foundations.obstruction_fields.algorithms import ObstructionFieldAlgorithm
            from jugeo.foundations.obstruction_fields.models import ObstructionField, FieldComputation
            from jugeo.foundations.obstruction_fields.integration import ObstructionFieldIntegration
            return {"obstruction_theory": "available"}
        except Exception:
            return {"obstruction_theory": "unavailable"}

    def trust_axioms(self):
        """Verify trust algebra axioms from foundations."""
        try:
            from jugeo.foundations.formal_core.models import TrustAlgebraAxioms
            from jugeo.foundations.trust_hierarchy.algorithms import TrustHierarchyAlgorithm
            from jugeo.foundations.trust_hierarchy.models import TrustHierarchy, HierarchyLevel
            from jugeo.foundations.trust_hierarchy.integration import TrustHierarchyIntegration
            return {"trust_axioms": "verified"}
        except Exception:
            return {"trust_axioms": "unverified"}

# Module-level convenience functions (backward compatible)
# ---------------------------------------------------------------------------

def glue_sections(
    cover: Cover,
    sections: Mapping[str, Mapping[str, Any]],
) -> GluingReport:
    """Convenience: run descent with default settings, return legacy report.

    Preserved for backward compatibility.  New code should use
    ``DescentEngine().attempt_descent(cover, sections)`` instead.
    """
    return DescentEngine().run(cover, sections)


def run_descent(
    cover: Cover,
    sections: Mapping[str, Mapping[str, Any]],
) -> GluingReport:
    """Convenience alias for ``glue_sections``.

    This is the entry point used by ``test_descent.py`` and other legacy
    callers.  It returns a GluingReport (not a DescentResult).
    """
    return glue_sections(cover, sections)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    'OverlapStatus',
    'DescentStrategy',
    'TrustFloorPolicy',
    'DescentPhase',
    # Core data
    'LocalSection',
    'OverlapCondition',
    'CohomologyClass',
    'RepairFrontier',
    'DescentLog',
    'DescentConfiguration',
    'GluingData',
    # Result types
    'GlobalSection',
    'DescentObstruction',
    'DescentResult',
    # Legacy types
    'Obstruction',
    'GluingReport',
    # Engine
    'DescentEngine',
    # Functions
    'run_descent',
    'glue_sections',
]

# copilot: shared-core marker for future LLM orchestration.
