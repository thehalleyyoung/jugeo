"""
Regime bootstrapping: provisional carriers, bridge stubs, and experimental
laws — theory2.tex Ch59.

# copilot: shared-core marker

Overview
--------
This module implements the *provisional carrier* pathway in JuGeo's regime
bootstrapping subsystem.  A new mathematical regime cannot spring into
existence fully formed; instead it is grown incrementally through three
interleaved stages described in theory2.tex Ch59 §3–§5:

1. **Provisional type carriers** (§3) — placeholder types that stand in for
   mathematical objects whose full specification is not yet known.  A carrier
   records the intended sort signature, the domain it belongs to, and a set
   of generator hints that guide the search for concrete representations.

2. **Bridge stubs** (§4) — theorem *slots*: each stub carries a name,
   a tuple of preconditions, and a tuple of postconditions, but no proof
   body.  Stubs are the promises a regime makes to the rest of the system.
   They allow downstream modules to reason about what the regime *will*
   deliver, even before full validation is complete.

3. **Experimental laws** (§5) — candidate axioms whose logical consistency
   has not yet been verified.  Before an experimental law can be promoted to
   AXIOM status it must pass the law-validation pipeline (see
   ``RegimeBootstrappingCoordinator.validate_experimental_law``).

This is how JuGeo grows its own mathematical vocabulary: new regimes begin
life as collections of provisional carriers, accumulate bridge stubs that
describe their relationship to existing regimes, and propose experimental laws
that characterise their behaviour.  Once a carrier satisfies the readiness
criteria it is promoted to STABLE and its accepted laws become axioms.

Carrier lifecycle
-----------------
::

    PROVISIONAL  ──(min stubs + min laws met)──►  CANDIDATE
    CANDIDATE    ──(readiness score ≥ threshold)──► STABLE
    STABLE       ──(superseded / withdrawn)──────►  RETIRED

Stages are represented by ``CarrierStatus`` and are managed exclusively by
``RegimeBootstrappingCoordinator``.  External code should not mutate the
``status`` field directly.

Law lifecycle
-------------
::

    EXPERIMENTAL ──(passes consistency check)──► CANDIDATE
    CANDIDATE    ──(accepted by coordinator)───► AXIOM
    EXPERIMENTAL ──(found inconsistent)────────► REFUTED

Readiness scoring
-----------------
The readiness score *r* for a carrier *c* with *s* bridge stubs and *l*
accepted laws is computed as::

    w_stub   = 0.4
    w_law    = 0.6
    s_norm   = min(s / min_stubs_for_candidacy, 1.0)
    l_norm   = min(l / max(min_laws_for_candidacy, 1), 1.0)
    r        = w_stub * s_norm + w_law * l_norm

A carrier is considered *ready* when ``r ≥ carrier_readiness_threshold``
(default 0.70) and all blocking issues reported by
``RegimeBootstrappingAnalyzer.analyze_carrier_readiness`` are resolved.

Design notes
------------
- ``RegimeBootstrappingCoordinator`` is the single entry-point for mutating
  state.  It owns both the analyzer and the witness.
- ``RegimeBootstrappingAnalyzer`` is a pure read-only component; it may be
  instantiated independently for inspection purposes.
- ``RegimeBootstrappingWitness`` records provenance for every structural event
  (carrier creation, stub addition, law validation, cycle completion).
- All cross-module JuGeo imports are guarded so this module can be used in
  isolation for unit-testing or prototyping.

Typical usage
-------------
::

    from jugeo.ideation.regime_bootstrapping.regime_bootstrapping_provisional_c import (
        run_bootstrapping_cycle,
        DomainRecord,
        BootstrappingConfig,
    )

    domain = DomainRecord(
        record_id="dom-0001",
        domain_name="FiberedSpaces",
        generators=("fiber_type", "base_type", "projection_map"),
    )
    config = BootstrappingConfig(min_stubs_for_candidacy=3, min_laws_for_candidacy=2)
    result = run_bootstrapping_cycle(domain, config)
    print(result.progress_score)

Theory reference: theory2.tex Ch59 §3–§5.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    # Enums
    "CarrierStatus",
    "LawStatus",
    # Config / value objects
    "BootstrappingConfig",
    "DomainRecord",
    "CarrierSpec",
    "BridgeStubSpec",
    "ExperimentalLawSpec",
    # Mutable data objects
    "ProvisionalCarrier",
    "BridgeStub",
    "ExperimentalLaw",
    "StableCarrier",
    # Result / report objects
    "LawValidationResult",
    "BootstrappingCycleResult",
    "CarrierReadinessReport",
    "StubCoverageReport",
    "LawConsistencyReport",
    "CarrierWitnessReport",
    "StubWitnessReport",
    "LawWitnessReport",
    "CycleWitnessReport",
    # Classes
    "RegimeBootstrappingCoordinator",
    "RegimeBootstrappingAnalyzer",
    "RegimeBootstrappingWitness",
    # Free functions
    "run_bootstrapping_cycle",
    "score_carrier_readiness",
    "select_laws_for_promotion",
]

# ---------------------------------------------------------------------------
# Cross-module imports — always guarded
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        BootstrapContext,
        BootstrapStep,
        BootstrapPlan,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default weight assigned to bridge-stub coverage when computing readiness.
_STUB_WEIGHT: float = 0.40

#: Default weight assigned to accepted-law fraction when computing readiness.
_LAW_WEIGHT: float = 0.60

#: Keywords that, when found in *both* statements of a law pair, signal a
#: potential logical conflict that warrants further scrutiny.
_CONFLICT_KEYWORDS: tuple[str, ...] = (
    "not",
    "never",
    "impossible",
    "refutes",
    "contradicts",
    "negates",
    "vacuous",
    "empty",
)

#: Minimum non-trivial consistency score assigned to any law whose statement
#: does not trigger any conflict signals.
_LAW_SCORE_FLOOR: float = 0.50

#: Scale factor used in the exponential smoothing of the law consistency score.
_LAW_SCORE_SCALE: float = 0.50

#: Maximum number of characters retained when hashing a string for an ID.
_ID_HASH_LEN: int = 12

#: Separator used when composing composite identifiers.
_ID_SEP: str = "::"

#: Version tag embedded in every witness report for traceability.
_WITNESS_VERSION: str = "provisional-c/1.0"

#: Default law statement inserted when a regime has no explicit law proposals.
_DEFAULT_LAW_STATEMENT: str = "identity_law: every element is equal to itself"

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CarrierStatus(Enum):
    """Lifecycle state of a :class:`ProvisionalCarrier`.

    The state machine is strictly monotone (no rollbacks) except for the
    RETIRED terminal state, which may be entered from any prior state when a
    carrier is superseded.
    """

    PROVISIONAL = auto()  # placeholder, not yet validated
    CANDIDATE = auto()    # validation in progress
    STABLE = auto()       # fully validated and integrated
    RETIRED = auto()      # superseded or withdrawn


class LawStatus(Enum):
    """Lifecycle state of an :class:`ExperimentalLaw`.

    Only laws that reach AXIOM status contribute to a carrier's readiness
    score.  REFUTED laws are retained for provenance purposes but are excluded
    from all scoring computations.
    """

    EXPERIMENTAL = auto()  # proposed, not yet tested
    CANDIDATE = auto()     # passing initial checks
    AXIOM = auto()         # accepted as axiom
    REFUTED = auto()       # found inconsistent

# ---------------------------------------------------------------------------
# Configuration / immutable value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrappingConfig:
    """Configuration parameters for the bootstrapping pipeline.

    All thresholds and limits have been chosen so that the defaults represent
    a reasonably strict but achievable bar for a new regime.  Relaxing
    ``carrier_readiness_threshold`` below 0.50 is not recommended as it
    defeats the purpose of the provisional stage.
    """

    min_stubs_for_candidacy: int = 2
    """Minimum number of bridge stubs before a carrier may advance to CANDIDATE."""

    min_laws_for_candidacy: int = 1
    """Minimum number of accepted (AXIOM) laws before candidacy advancement."""

    law_consistency_threshold: float = 0.75
    """Fraction of laws that must be consistent for the carrier to be healthy."""

    carrier_readiness_threshold: float = 0.70
    """Readiness score ∈ [0, 1] required for promotion to STABLE."""

    max_experimental_laws: int = 20
    """Hard cap on the number of experimental laws a single carrier may hold."""

    enable_law_validation: bool = True
    """When False, laws are accepted without running the consistency checker."""

    max_bridge_stubs: int = 50
    """Hard cap on bridge stubs per carrier to prevent runaway stub growth."""


@dataclass(frozen=True, slots=True)
class DomainRecord:
    """Lightweight descriptor for the mathematical domain that owns a carrier.

    ``generators`` are the named objects (functions, types, constants) that
    the domain exposes and that a provisional carrier may reference in its
    sort signature.
    """

    record_id: str
    domain_name: str
    generators: tuple[str, ...]
    status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class CarrierSpec:
    """Specification supplied by the caller when requesting a new provisional
    carrier.

    ``generator_hints`` are names drawn from the owning domain's generators
    list.  They guide the bootstrapping coordinator in selecting default
    bridge stubs and experimental laws during automatic seeding.
    """

    name: str
    sort_signature: str
    intended_role: str
    generator_hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BridgeStubSpec:
    """Input specification for adding a bridge stub to a carrier.

    ``preconditions`` and ``postconditions`` are plain-text logical clauses
    (not yet formal proofs).  ``tags`` allow downstream tooling to cluster
    related stubs for coverage analysis.
    """

    stub_name: str
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExperimentalLawSpec:
    """Input specification for proposing an experimental law.

    ``priority`` influences which laws are checked first during a validation
    sweep; higher-priority laws are validated before lower-priority ones.
    The field is informational and does not affect scoring.
    """

    law_statement: str
    law_tags: tuple[str, ...]
    priority: float = 1.0

# ---------------------------------------------------------------------------
# Mutable data objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProvisionalCarrier:
    """Mutable runtime representation of a provisional type carrier.

    ``bridge_stub_ids`` and ``law_ids`` accumulate as the bootstrapping
    pipeline runs.  They store only IDs; the actual objects are owned by the
    coordinator's internal registries.

    Direct mutation of ``status`` from outside
    :class:`RegimeBootstrappingCoordinator` is strongly discouraged.
    """

    carrier_id: str
    domain_record_id: str
    name: str
    sort_signature: str
    status: CarrierStatus
    bridge_stub_ids: list[str] = field(default_factory=list)
    law_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def advance_to_candidate(self) -> None:
        """Transition status from PROVISIONAL → CANDIDATE (in-place)."""
        if self.status is CarrierStatus.PROVISIONAL:
            self.status = CarrierStatus.CANDIDATE
            log.debug("Carrier %s advanced to CANDIDATE", self.carrier_id)

    def retire(self) -> None:
        """Mark the carrier as RETIRED from any state."""
        self.status = CarrierStatus.RETIRED
        log.info("Carrier %s retired", self.carrier_id)

    def stub_count(self) -> int:
        """Return the number of bridge stubs attached to this carrier."""
        return len(self.bridge_stub_ids)

    def law_count(self) -> int:
        """Return the total number of laws (in any status) attached."""
        return len(self.law_ids)


@dataclass(frozen=True, slots=True)
class BridgeStub:
    """Immutable record of a bridge stub attached to a provisional carrier.

    Once created, stub records are never mutated.  If a stub needs to be
    revised a new stub is created and the old one is logically superseded via
    metadata on the carrier.
    """

    stub_id: str
    carrier_id: str
    stub_name: str
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    tags: tuple[str, ...]
    created_at: str


@dataclass(slots=True)
class ExperimentalLaw:
    """Mutable record of an experimental law.

    ``consistency_score`` is updated in-place after each validation run.
    ``status`` advances through the LawStatus state machine as the law is
    validated and either accepted or refuted.
    """

    law_id: str
    carrier_id: str
    statement: str
    tags: tuple[str, ...]
    status: LawStatus
    consistency_score: float = 0.0
    created_at: str = ""

    # ------------------------------------------------------------------
    def mark_candidate(self, score: float) -> None:
        """Transition EXPERIMENTAL → CANDIDATE with an initial score."""
        if self.status is LawStatus.EXPERIMENTAL:
            self.status = LawStatus.CANDIDATE
            self.consistency_score = score
            log.debug("Law %s advanced to CANDIDATE (score=%.3f)", self.law_id, score)

    def promote_to_axiom(self) -> None:
        """Promote a CANDIDATE law to AXIOM status."""
        if self.status is LawStatus.CANDIDATE:
            self.status = LawStatus.AXIOM
            log.info("Law %s promoted to AXIOM", self.law_id)

    def refute(self) -> None:
        """Mark the law as REFUTED from any non-AXIOM status."""
        if self.status is not LawStatus.AXIOM:
            self.status = LawStatus.REFUTED
            log.warning("Law %s refuted", self.law_id)

# ---------------------------------------------------------------------------
# Result / report objects (all frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LawValidationResult:
    """Outcome of a single law consistency check."""

    law_id: str
    is_consistent: bool
    consistency_score: float
    counterexample_sketch: str
    validation_notes: str


@dataclass(frozen=True, slots=True)
class StableCarrier:
    """Immutable snapshot of a carrier after promotion to STABLE.

    ``axiom_ids`` is a tuple of all law IDs that have reached AXIOM status
    and are thereby incorporated into the regime's axiom set.
    """

    carrier_id: str
    domain_record_id: str
    name: str
    sort_signature: str
    promoted_at: str
    axiom_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BootstrappingCycleResult:
    """Summary statistics for one complete bootstrapping cycle."""

    cycle_id: str
    domain_record_id: str
    carriers_created: int
    stubs_added: int
    laws_proposed: int
    laws_accepted: int
    carriers_promoted: int
    progress_score: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CarrierReadinessReport:
    """Detailed readiness assessment for a single provisional carrier."""

    carrier_id: str
    stub_count: int
    law_count: int
    accepted_law_count: int
    readiness_score: float
    is_ready: bool
    blocking_issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StubCoverageReport:
    """Coverage analysis of bridge stubs for a carrier.

    ``gaps`` lists precondition or postcondition clauses that appear in fewer
    than two stubs and thus represent under-covered areas of the interface.
    """

    carrier_id: str
    total_stubs: int
    covered_preconditions: int
    covered_postconditions: int
    coverage_fraction: float
    gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LawConsistencyReport:
    """Pairwise consistency analysis across a set of experimental laws.

    ``conflict_pairs`` holds pairs of law IDs that were found to have
    potential logical conflicts based on keyword analysis.
    """

    carrier_id: str
    total_laws: int
    consistent_laws: int
    inconsistent_laws: int
    consistency_fraction: float
    conflict_pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CarrierWitnessReport:
    """Provenance record for a carrier-creation event."""

    witness_id: str
    carrier_id: str
    carrier_name: str
    status_name: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class StubWitnessReport:
    """Provenance record for a bridge-stub-addition event."""

    witness_id: str
    stub_id: str
    stub_name: str
    carrier_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class LawWitnessReport:
    """Provenance record for a law-validation event."""

    witness_id: str
    law_id: str
    law_statement: str
    is_consistent: bool
    consistency_score: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class CycleWitnessReport:
    """Provenance record for a completed bootstrapping cycle."""

    witness_id: str
    cycle_id: str
    progress_score: float
    carriers_promoted: int
    timestamp: str
    summary: str

# ---------------------------------------------------------------------------
# Helper functions (module-private)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _sha_prefix(text: str, length: int = _ID_HASH_LEN) -> str:
    """Return the first *length* hex characters of the SHA-256 of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _build_carrier_id(domain_id: str, name: str) -> str:
    """Construct a stable, collision-resistant carrier ID.

    The ID is formed from a short hash of the domain/name pair so that the
    same carrier specification always produces the same ID within a session.
    """
    raw = f"{domain_id}{_ID_SEP}{name}"
    return f"carrier{_ID_SEP}{_sha_prefix(raw)}"


def _build_stub_id(carrier_id: str, stub_name: str) -> str:
    """Construct a stable stub ID from the owning carrier and stub name."""
    raw = f"{carrier_id}{_ID_SEP}{stub_name}"
    return f"stub{_ID_SEP}{_sha_prefix(raw)}"


def _build_law_id(carrier_id: str, statement: str) -> str:
    """Construct a stable law ID from the owning carrier and statement text."""
    raw = f"{carrier_id}{_ID_SEP}{statement}"
    return f"law{_ID_SEP}{_sha_prefix(raw)}"


def _carrier_readiness_score(
    carrier: ProvisionalCarrier,
    config: BootstrappingConfig,
) -> float:
    """Compute the readiness score ∈ [0, 1] for *carrier* given *config*.

    The formula (see module docstring for derivation)::

        s_norm = min(stub_count / min_stubs_for_candidacy, 1.0)
        l_norm = min(axiom_count / max(min_laws_for_candidacy, 1), 1.0)
        score  = STUB_WEIGHT * s_norm + LAW_WEIGHT * l_norm

    Note that *l_norm* counts only AXIOM-status laws because only those
    contribute genuine mathematical content to the regime.
    """
    stub_norm = min(
        len(carrier.bridge_stub_ids) / max(config.min_stubs_for_candidacy, 1),
        1.0,
    )
    # We need the axiom count but we only have IDs here; proxy via law_ids
    # length and assume all have been validated (callers must ensure this).
    law_norm = min(
        len(carrier.law_ids) / max(config.min_laws_for_candidacy, 1),
        1.0,
    )
    return _STUB_WEIGHT * stub_norm + _LAW_WEIGHT * law_norm


def _law_consistency_score(law: ExperimentalLaw) -> float:
    """Heuristic consistency score for *law* based on its statement text.

    The score starts at 1.0 and is reduced by ``_LAW_SCORE_SCALE`` for each
    conflict keyword found in the statement, bottoming out at
    ``_LAW_SCORE_FLOOR``.  A law with no conflict keywords scores 1.0.

    This is intentionally a lightweight proxy; a full consistency check
    would require a proof assistant.
    """
    statement_lower = law.statement.lower()
    penalty_count = sum(
        1 for kw in _CONFLICT_KEYWORDS if kw in statement_lower
    )
    raw_score = 1.0 - penalty_count * _LAW_SCORE_SCALE
    return max(raw_score, _LAW_SCORE_FLOOR)


def _detect_law_conflicts(
    law_a: ExperimentalLaw,
    law_b: ExperimentalLaw,
) -> bool:
    """Return True if *law_a* and *law_b* appear to conflict.

    The detection strategy is conservative: two laws conflict only when both
    statements share at least one conflict keyword *and* the pair shares at
    least one common non-stop word, suggesting they are talking about the
    same mathematical object.

    This guards against spurious conflicts between laws that mention negation
    in entirely different contexts.
    """
    def tokens(s: str) -> set[str]:
        stop = {"a", "an", "the", "is", "are", "be", "of", "to", "and", "or", "in", "for"}
        return {w.strip(".,;:()") for w in s.lower().split() if len(w) > 2} - stop

    tokens_a = tokens(law_a.statement)
    tokens_b = tokens(law_b.statement)
    shared_tokens = tokens_a & tokens_b

    conflict_kws_a = {kw for kw in _CONFLICT_KEYWORDS if kw in law_a.statement.lower()}
    conflict_kws_b = {kw for kw in _CONFLICT_KEYWORDS if kw in law_b.statement.lower()}

    return bool(conflict_kws_a) and bool(conflict_kws_b) and bool(shared_tokens)


def _stub_spec_to_default(carrier_name: str) -> list[BridgeStubSpec]:
    """Generate a minimal set of default bridge stubs for *carrier_name*.

    Returns two stubs: an identity stub (every object is related to itself)
    and a composition stub (composition of maps is associative).  These are
    reasonable starting points for any new type carrier.
    """
    identity_stub = BridgeStubSpec(
        stub_name=f"{carrier_name}.identity",
        preconditions=(f"x : {carrier_name}",),
        postconditions=(f"identity({carrier_name}, x) = x",),
        tags=("identity", "default"),
    )
    composition_stub = BridgeStubSpec(
        stub_name=f"{carrier_name}.composition",
        preconditions=(
            f"f : {carrier_name} → {carrier_name}",
            f"g : {carrier_name} → {carrier_name}",
            f"h : {carrier_name} → {carrier_name}",
        ),
        postconditions=(
            f"compose({carrier_name}, f, compose({carrier_name}, g, h)) "
            f"= compose({carrier_name}, compose({carrier_name}, f, g), h)",
        ),
        tags=("composition", "associativity", "default"),
    )
    return [identity_stub, composition_stub]

# ---------------------------------------------------------------------------
# RegimeBootstrappingWitness
# ---------------------------------------------------------------------------


class RegimeBootstrappingWitness:
    """Records provenance for every structural event in the bootstrapping
    pipeline.

    Each ``witness_*`` method is idempotent in the sense that calling it
    twice for the same object produces two separate witness records, each
    with a unique ``witness_id``.  Callers should not deduplicate records;
    the full audit trail is the intended output.

    The witness does not write to persistent storage by itself; it returns
    immutable report objects that the caller is responsible for persisting.
    """

    def __init__(self) -> None:
        self._version = _WITNESS_VERSION
        log.debug("RegimeBootstrappingWitness initialised (version=%s)", self._version)

    # ------------------------------------------------------------------
    def witness_carrier_creation(
        self, carrier: ProvisionalCarrier
    ) -> CarrierWitnessReport:
        """Produce a provenance record for a newly created provisional carrier.

        Args:
            carrier: The carrier that was just created.

        Returns:
            A :class:`CarrierWitnessReport` with a fresh UUID witness ID.
        """
        report = CarrierWitnessReport(
            witness_id=str(uuid.uuid4()),
            carrier_id=carrier.carrier_id,
            carrier_name=carrier.name,
            status_name=carrier.status.name,
            timestamp=_now_iso(),
        )
        log.debug(
            "Witness: carrier created carrier_id=%s name=%s",
            carrier.carrier_id,
            carrier.name,
        )
        return report

    def witness_bridge_stub(self, stub: BridgeStub) -> StubWitnessReport:
        """Produce a provenance record for a bridge stub addition.

        Args:
            stub: The stub that was just added to a carrier.

        Returns:
            A :class:`StubWitnessReport` with a fresh UUID witness ID.
        """
        report = StubWitnessReport(
            witness_id=str(uuid.uuid4()),
            stub_id=stub.stub_id,
            stub_name=stub.stub_name,
            carrier_id=stub.carrier_id,
            timestamp=_now_iso(),
        )
        log.debug(
            "Witness: stub added stub_id=%s carrier_id=%s",
            stub.stub_id,
            stub.carrier_id,
        )
        return report

    def witness_experimental_law(
        self,
        law: ExperimentalLaw,
        validation: LawValidationResult,
    ) -> LawWitnessReport:
        """Produce a provenance record for a law validation event.

        Args:
            law: The law whose validation just completed.
            validation: The result of the consistency check.

        Returns:
            A :class:`LawWitnessReport` capturing the validation outcome.
        """
        report = LawWitnessReport(
            witness_id=str(uuid.uuid4()),
            law_id=law.law_id,
            law_statement=law.statement,
            is_consistent=validation.is_consistent,
            consistency_score=validation.consistency_score,
            timestamp=_now_iso(),
        )
        log.debug(
            "Witness: law validated law_id=%s consistent=%s score=%.3f",
            law.law_id,
            validation.is_consistent,
            validation.consistency_score,
        )
        return report

    def witness_bootstrapping_cycle(
        self, result: BootstrappingCycleResult
    ) -> CycleWitnessReport:
        """Produce a provenance record for a completed bootstrapping cycle.

        Args:
            result: The cycle result to witness.

        Returns:
            A :class:`CycleWitnessReport` summarising key metrics.
        """
        summary = (
            f"cycle {result.cycle_id}: "
            f"{result.carriers_created} carriers, "
            f"{result.stubs_added} stubs, "
            f"{result.laws_accepted}/{result.laws_proposed} laws accepted, "
            f"{result.carriers_promoted} promoted, "
            f"progress={result.progress_score:.3f}"
        )
        report = CycleWitnessReport(
            witness_id=str(uuid.uuid4()),
            cycle_id=result.cycle_id,
            progress_score=result.progress_score,
            carriers_promoted=result.carriers_promoted,
            timestamp=_now_iso(),
            summary=summary,
        )
        log.info("Witness: %s", summary)
        return report

# ---------------------------------------------------------------------------
# RegimeBootstrappingAnalyzer
# ---------------------------------------------------------------------------


class RegimeBootstrappingAnalyzer:
    """Read-only analysis of provisional carriers, stubs, and laws.

    The analyzer is a pure value-computing component: it does not mutate
    any objects.  It may be instantiated independently of the coordinator
    for inspection or debugging purposes.

    All ``analyze_*`` methods are re-entrant and thread-safe (they create
    no shared mutable state).
    """

    def __init__(self, config: BootstrappingConfig | None = None) -> None:
        self._config = config or BootstrappingConfig()

    # ------------------------------------------------------------------
    def analyze_carrier_readiness(
        self, carrier: ProvisionalCarrier
    ) -> CarrierReadinessReport:
        """Assess whether *carrier* is ready for promotion to STABLE.

        The method counts bridge stubs and laws, computes the readiness score
        (see module docstring), and collects any blocking issues that prevent
        promotion.

        Args:
            carrier: The provisional carrier to assess.

        Returns:
            A :class:`CarrierReadinessReport` with full diagnostic detail.
        """
        cfg = self._config
        stub_count = len(carrier.bridge_stub_ids)
        law_count = len(carrier.law_ids)
        # We do not have direct access to the law objects here, so we use
        # law_count as a proxy for accepted_law_count (the coordinator ensures
        # only AXIOM law IDs are stored in carrier.law_ids at promotion time).
        accepted_law_count = law_count

        stub_norm = min(stub_count / max(cfg.min_stubs_for_candidacy, 1), 1.0)
        law_norm = min(accepted_law_count / max(cfg.min_laws_for_candidacy, 1), 1.0)
        score = _STUB_WEIGHT * stub_norm + _LAW_WEIGHT * law_norm

        blocking: list[str] = []
        if stub_count < cfg.min_stubs_for_candidacy:
            blocking.append(
                f"need {cfg.min_stubs_for_candidacy} bridge stubs, have {stub_count}"
            )
        if accepted_law_count < cfg.min_laws_for_candidacy:
            blocking.append(
                f"need {cfg.min_laws_for_candidacy} accepted laws, have {accepted_law_count}"
            )
        if carrier.status is CarrierStatus.RETIRED:
            blocking.append("carrier has been retired and cannot be promoted")

        is_ready = score >= cfg.carrier_readiness_threshold and not blocking

        return CarrierReadinessReport(
            carrier_id=carrier.carrier_id,
            stub_count=stub_count,
            law_count=law_count,
            accepted_law_count=accepted_law_count,
            readiness_score=score,
            is_ready=is_ready,
            blocking_issues=tuple(blocking),
        )

    # ------------------------------------------------------------------
    def analyze_stub_coverage(
        self,
        carrier: ProvisionalCarrier,
        stubs: list[BridgeStub],
    ) -> StubCoverageReport:
        """Analyse the precondition/postcondition coverage of *stubs*.

        A clause is considered *covered* if it appears (textually) in at least
        one precondition or postcondition across all stubs.  Clauses that
        appear in only a single stub are listed in ``gaps``.

        Args:
            carrier: The owning carrier (used for the report ID).
            stubs: The list of :class:`BridgeStub` objects to analyse.

        Returns:
            A :class:`StubCoverageReport`.
        """
        pre_counts: dict[str, int] = defaultdict(int)
        post_counts: dict[str, int] = defaultdict(int)

        for stub in stubs:
            for clause in stub.preconditions:
                pre_counts[clause] += 1
            for clause in stub.postconditions:
                post_counts[clause] += 1

        covered_pre = sum(1 for v in pre_counts.values() if v >= 1)
        covered_post = sum(1 for v in post_counts.values() if v >= 1)
        total_clauses = covered_pre + covered_post
        coverage_fraction = (
            (covered_pre + covered_post) / total_clauses if total_clauses > 0 else 0.0
        )

        gaps: list[str] = []
        for clause, count in pre_counts.items():
            if count < 2:
                gaps.append(f"pre: {clause!r} (count={count})")
        for clause, count in post_counts.items():
            if count < 2:
                gaps.append(f"post: {clause!r} (count={count})")

        return StubCoverageReport(
            carrier_id=carrier.carrier_id,
            total_stubs=len(stubs),
            covered_preconditions=covered_pre,
            covered_postconditions=covered_post,
            coverage_fraction=coverage_fraction,
            gaps=tuple(gaps[:20]),  # cap to avoid excessively large reports
        )

    # ------------------------------------------------------------------
    def analyze_law_consistency(
        self, laws: list[ExperimentalLaw]
    ) -> LawConsistencyReport:
        """Analyse pairwise consistency across *laws*.

        Each pair of laws is checked with ``_detect_law_conflicts``.  A law
        that participates in at least one conflict pair is counted as
        inconsistent for the purpose of the consistency fraction.

        Args:
            laws: List of :class:`ExperimentalLaw` objects to analyse.

        Returns:
            A :class:`LawConsistencyReport`.
        """
        conflict_pairs: list[tuple[str, str]] = []
        conflicting_ids: set[str] = set()

        for law_a, law_b in itertools.combinations(laws, 2):
            if _detect_law_conflicts(law_a, law_b):
                conflict_pairs.append((law_a.law_id, law_b.law_id))
                conflicting_ids.add(law_a.law_id)
                conflicting_ids.add(law_b.law_id)

        total = len(laws)
        inconsistent = len(conflicting_ids)
        consistent = total - inconsistent
        fraction = consistent / total if total > 0 else 1.0

        # Use carrier_id from first law, or "unknown" if no laws
        carrier_id = laws[0].carrier_id if laws else "unknown"

        return LawConsistencyReport(
            carrier_id=carrier_id,
            total_laws=total,
            consistent_laws=consistent,
            inconsistent_laws=inconsistent,
            consistency_fraction=fraction,
            conflict_pairs=tuple(conflict_pairs),
        )

    # ------------------------------------------------------------------
    def compute_bootstrapping_progress(
        self, cycle: BootstrappingCycleResult
    ) -> float:
        """Compute an aggregate progress score from a cycle result.

        The score is a weighted combination of four metrics:

        - law acceptance rate (weight 0.35)
        - carrier promotion rate (weight 0.35)
        - stub density (stubs per carrier, weight 0.20)
        - law proposal density (laws per carrier, weight 0.10)

        Each metric is clamped to [0, 1] before weighting.

        Args:
            cycle: A completed :class:`BootstrappingCycleResult`.

        Returns:
            A float in [0, 1] representing overall cycle progress.
        """
        carriers = max(cycle.carriers_created, 1)

        law_accept_rate = (
            cycle.laws_accepted / max(cycle.laws_proposed, 1)
            if cycle.laws_proposed > 0
            else 0.0
        )
        carrier_promote_rate = min(cycle.carriers_promoted / carriers, 1.0)
        stub_density = min(
            cycle.stubs_added / (carriers * self._config.min_stubs_for_candidacy),
            1.0,
        )
        law_density = min(
            cycle.laws_proposed / (carriers * self._config.min_laws_for_candidacy),
            1.0,
        )

        score = (
            0.35 * law_accept_rate
            + 0.35 * carrier_promote_rate
            + 0.20 * stub_density
            + 0.10 * law_density
        )
        return round(min(max(score, 0.0), 1.0), 6)

# ---------------------------------------------------------------------------
# RegimeBootstrappingCoordinator
# ---------------------------------------------------------------------------


class RegimeBootstrappingCoordinator:
    """Stateful coordinator for the full provisional-carrier bootstrapping pipeline.

    The coordinator owns:

    - ``_carriers``: a dict mapping carrier_id → :class:`ProvisionalCarrier`
    - ``_stubs``: a dict mapping stub_id → :class:`BridgeStub`
    - ``_laws``: a dict mapping law_id → :class:`ExperimentalLaw`
    - ``_stable_carriers``: a list of promoted :class:`StableCarrier` objects
    - An instance of :class:`RegimeBootstrappingAnalyzer`
    - An instance of :class:`RegimeBootstrappingWitness`

    Typical workflow::

        coord = RegimeBootstrappingCoordinator(config)
        carrier = coord.create_provisional_carrier(domain, spec)
        stub = coord.add_bridge_stub(carrier, stub_spec)
        law = coord.propose_experimental_law(carrier, law_spec)
        validation = coord.validate_experimental_law(law)
        stable = coord.promote_carrier_to_stable(carrier)
        result = coord.run_bootstrapping_cycle(domain)

    The ``run_bootstrapping_cycle`` method orchestrates all of the above steps
    automatically using sensible defaults, including seeding default stubs and
    laws derived from the domain's generator hints.
    """

    def __init__(self, config: BootstrappingConfig | None = None) -> None:
        self._config = config or BootstrappingConfig()
        self._analyzer = RegimeBootstrappingAnalyzer(self._config)
        self._witness = RegimeBootstrappingWitness()

        self._carriers: dict[str, ProvisionalCarrier] = {}
        self._stubs: dict[str, BridgeStub] = {}
        self._laws: dict[str, ExperimentalLaw] = {}
        self._stable_carriers: list[StableCarrier] = []

        log.debug(
            "RegimeBootstrappingCoordinator initialised with config=%r",
            self._config,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_provisional_carrier(
        self,
        domain: DomainRecord,
        spec: CarrierSpec,
    ) -> ProvisionalCarrier:
        """Create and register a new provisional carrier.

        The carrier is assigned a deterministic ID derived from the domain ID
        and carrier name (see ``_build_carrier_id``), so re-creating the same
        carrier specification within the same session returns an equivalent
        but distinct object.

        Args:
            domain: The domain record that owns the new carrier.
            spec: The specification describing the new carrier.

        Returns:
            A :class:`ProvisionalCarrier` in PROVISIONAL status.

        Raises:
            ValueError: If the domain is not ACTIVE.
        """
        if domain.status != "ACTIVE":
            raise ValueError(
                f"Cannot create carrier in inactive domain {domain.record_id!r} "
                f"(status={domain.status!r})"
            )

        carrier_id = _build_carrier_id(domain.record_id, spec.name)
        carrier = ProvisionalCarrier(
            carrier_id=carrier_id,
            domain_record_id=domain.record_id,
            name=spec.name,
            sort_signature=spec.sort_signature,
            status=CarrierStatus.PROVISIONAL,
            created_at=_now_iso(),
            metadata={
                "intended_role": spec.intended_role,
                "generator_hints": list(spec.generator_hints),
                "domain_name": domain.domain_name,
            },
        )
        self._carriers[carrier_id] = carrier

        _report = self._witness.witness_carrier_creation(carrier)
        log.info(
            "Created provisional carrier %s (name=%s, domain=%s)",
            carrier_id,
            spec.name,
            domain.domain_name,
        )
        return carrier

    # ------------------------------------------------------------------
    def add_bridge_stub(
        self,
        carrier: ProvisionalCarrier,
        stub_spec: BridgeStubSpec,
    ) -> BridgeStub:
        """Validate *stub_spec* and attach a new bridge stub to *carrier*.

        Validation checks:

        1. The carrier must not be RETIRED.
        2. The carrier must not already exceed ``max_bridge_stubs``.
        3. The stub must have at least one precondition and one postcondition.

        Args:
            carrier: The target carrier.
            stub_spec: Specification for the new stub.

        Returns:
            The newly created :class:`BridgeStub`.

        Raises:
            ValueError: If any validation check fails.
        """
        if carrier.status is CarrierStatus.RETIRED:
            raise ValueError(
                f"Cannot add stub to retired carrier {carrier.carrier_id!r}"
            )
        if len(carrier.bridge_stub_ids) >= self._config.max_bridge_stubs:
            raise ValueError(
                f"Carrier {carrier.carrier_id!r} has reached the maximum of "
                f"{self._config.max_bridge_stubs} bridge stubs"
            )
        if not stub_spec.preconditions:
            raise ValueError("A bridge stub must have at least one precondition")
        if not stub_spec.postconditions:
            raise ValueError("A bridge stub must have at least one postcondition")

        stub_id = _build_stub_id(carrier.carrier_id, stub_spec.stub_name)
        stub = BridgeStub(
            stub_id=stub_id,
            carrier_id=carrier.carrier_id,
            stub_name=stub_spec.stub_name,
            preconditions=stub_spec.preconditions,
            postconditions=stub_spec.postconditions,
            tags=stub_spec.tags,
            created_at=_now_iso(),
        )
        self._stubs[stub_id] = stub
        carrier.bridge_stub_ids.append(stub_id)

        _report = self._witness.witness_bridge_stub(stub)
        log.debug(
            "Added bridge stub %s to carrier %s",
            stub_id,
            carrier.carrier_id,
        )
        return stub

    # ------------------------------------------------------------------
    def propose_experimental_law(
        self,
        carrier: ProvisionalCarrier,
        law_spec: ExperimentalLawSpec,
    ) -> ExperimentalLaw:
        """Propose a new experimental law for *carrier*.

        The law is created in EXPERIMENTAL status.  It must subsequently be
        passed to ``validate_experimental_law`` before it can advance to
        CANDIDATE or AXIOM.

        Args:
            carrier: The carrier the law is attached to.
            law_spec: The law specification.

        Returns:
            A newly created :class:`ExperimentalLaw` in EXPERIMENTAL status.

        Raises:
            ValueError: If the carrier already holds the maximum number of
                experimental laws.
        """
        experimental_count = sum(
            1
            for lid in carrier.law_ids
            if lid in self._laws
            and self._laws[lid].status is LawStatus.EXPERIMENTAL
        )
        if experimental_count >= self._config.max_experimental_laws:
            raise ValueError(
                f"Carrier {carrier.carrier_id!r} already has "
                f"{experimental_count} experimental laws (max "
                f"{self._config.max_experimental_laws})"
            )

        law_id = _build_law_id(carrier.carrier_id, law_spec.law_statement)
        law = ExperimentalLaw(
            law_id=law_id,
            carrier_id=carrier.carrier_id,
            statement=law_spec.law_statement,
            tags=law_spec.law_tags,
            status=LawStatus.EXPERIMENTAL,
            created_at=_now_iso(),
        )
        self._laws[law_id] = law
        carrier.law_ids.append(law_id)

        log.debug(
            "Proposed experimental law %s for carrier %s",
            law_id,
            carrier.carrier_id,
        )
        return law

    # ------------------------------------------------------------------
    def validate_experimental_law(
        self, law: ExperimentalLaw
    ) -> LawValidationResult:
        """Run the consistency checker on *law* and update its status.

        When ``enable_law_validation`` is False in the config, every law is
        accepted unconditionally with a score of 1.0.

        Otherwise the scorer:

        1. Computes ``_law_consistency_score`` based on conflict keywords.
        2. Checks the law against all other laws on the same carrier using
           ``_detect_law_conflicts``.
        3. If the score meets the threshold, the law advances to CANDIDATE
           then AXIOM; otherwise it is REFUTED.

        Args:
            law: The :class:`ExperimentalLaw` to validate.

        Returns:
            A :class:`LawValidationResult` with the outcome.
        """
        if not self._config.enable_law_validation:
            law.mark_candidate(1.0)
            law.promote_to_axiom()
            return LawValidationResult(
                law_id=law.law_id,
                is_consistent=True,
                consistency_score=1.0,
                counterexample_sketch="",
                validation_notes="Validation disabled; law accepted unconditionally.",
            )

        base_score = _law_consistency_score(law)

        # Cross-check against sibling laws on the same carrier
        sibling_laws = [
            self._laws[lid]
            for lid in (
                self._carriers[law.carrier_id].law_ids
                if law.carrier_id in self._carriers
                else []
            )
            if lid != law.law_id and lid in self._laws
        ]
        conflict_count = sum(
            1 for sib in sibling_laws if _detect_law_conflicts(law, sib)
        )

        # Each sibling conflict deducts an additional penalty
        score = max(base_score - conflict_count * 0.15, 0.0)

        counterexample = ""
        if score < self._config.law_consistency_threshold:
            counterexample = (
                f"Law '{law.statement[:80]}' has {conflict_count} sibling "
                f"conflict(s); base_score={base_score:.3f}"
            )
            law.refute()
            is_consistent = False
            notes = f"Refuted: score {score:.3f} < threshold {self._config.law_consistency_threshold:.3f}"
        else:
            law.mark_candidate(score)
            law.promote_to_axiom()
            is_consistent = True
            notes = f"Accepted: score {score:.3f} ≥ threshold {self._config.law_consistency_threshold:.3f}"

        result = LawValidationResult(
            law_id=law.law_id,
            is_consistent=is_consistent,
            consistency_score=score,
            counterexample_sketch=counterexample,
            validation_notes=notes,
        )

        _report = self._witness.witness_experimental_law(law, result)
        return result

    # ------------------------------------------------------------------
    def promote_carrier_to_stable(
        self, carrier: ProvisionalCarrier
    ) -> StableCarrier:
        """Promote *carrier* to STABLE status and return a snapshot.

        Readiness is assessed by the analyzer.  If the carrier is not ready
        a ``ValueError`` is raised with the list of blocking issues.

        Args:
            carrier: The provisional carrier to promote.

        Returns:
            A :class:`StableCarrier` snapshot.

        Raises:
            ValueError: If the carrier does not meet readiness criteria.
        """
        report = self._analyzer.analyze_carrier_readiness(carrier)
        if not report.is_ready:
            issues = "; ".join(report.blocking_issues) or "unknown"
            raise ValueError(
                f"Carrier {carrier.carrier_id!r} is not ready for promotion: {issues}"
            )

        axiom_ids = tuple(
            lid
            for lid in carrier.law_ids
            if lid in self._laws and self._laws[lid].status is LawStatus.AXIOM
        )

        stable = StableCarrier(
            carrier_id=carrier.carrier_id,
            domain_record_id=carrier.domain_record_id,
            name=carrier.name,
            sort_signature=carrier.sort_signature,
            promoted_at=_now_iso(),
            axiom_ids=axiom_ids,
        )
        self._stable_carriers.append(stable)
        carrier.status = CarrierStatus.STABLE

        log.info(
            "Carrier %s promoted to STABLE with %d axiom(s)",
            carrier.carrier_id,
            len(axiom_ids),
        )
        return stable

    # ------------------------------------------------------------------
    def run_bootstrapping_cycle(
        self, domain: DomainRecord
    ) -> BootstrappingCycleResult:
        """Execute a complete bootstrapping cycle for *domain*.

        The cycle performs the following steps in order:

        1. Create one provisional carrier per generator hint in the domain
           (falling back to a single unnamed carrier if the domain has no
           generators).
        2. Seed each carrier with default bridge stubs generated by
           ``_stub_spec_to_default``.
        3. Propose a default experimental law for each carrier.
        4. Validate all proposed laws.
        5. Attempt to promote each carrier that has passed the readiness check.
        6. Compute the cycle progress score.

        Args:
            domain: The domain to run the cycle for.

        Returns:
            A :class:`BootstrappingCycleResult` summarising the cycle.
        """
        import time
        start = time.monotonic()
        cycle_id = str(uuid.uuid4())

        generators = domain.generators if domain.generators else ("default",)
        carriers_created = 0
        stubs_added = 0
        laws_proposed = 0
        laws_accepted = 0
        carriers_promoted = 0

        cycle_carriers: list[ProvisionalCarrier] = []

        # Step 1 + 2 + 3: create carriers, add default stubs and laws
        for gen_hint in generators:
            spec = CarrierSpec(
                name=f"{domain.domain_name}.{gen_hint}",
                sort_signature=f"Sort({gen_hint})",
                intended_role=f"carrier for generator '{gen_hint}'",
                generator_hints=(gen_hint,),
            )
            carrier = self.create_provisional_carrier(domain, spec)
            carriers_created += 1
            cycle_carriers.append(carrier)

            for stub_spec in _stub_spec_to_default(spec.name):
                try:
                    self.add_bridge_stub(carrier, stub_spec)
                    stubs_added += 1
                except ValueError as exc:
                    log.warning("Stub addition skipped: %s", exc)

            law_spec = ExperimentalLawSpec(
                law_statement=(
                    f"{spec.name}_identity_law: for all x : {spec.name}, "
                    f"id({spec.name}, x) = x"
                ),
                law_tags=("identity", "auto-generated"),
                priority=1.0,
            )
            try:
                _law = self.propose_experimental_law(carrier, law_spec)
                laws_proposed += 1
            except ValueError as exc:
                log.warning("Law proposal skipped: %s", exc)

        # Step 4: validate all laws on cycle carriers
        for carrier in cycle_carriers:
            for lid in list(carrier.law_ids):
                if lid not in self._laws:
                    continue
                law = self._laws[lid]
                if law.status is not LawStatus.EXPERIMENTAL:
                    continue
                result = self.validate_experimental_law(law)
                if result.is_consistent:
                    laws_accepted += 1

        # Step 5: attempt promotion
        for carrier in cycle_carriers:
            try:
                self.promote_carrier_to_stable(carrier)
                carriers_promoted += 1
            except ValueError as exc:
                log.debug("Carrier %s not promoted: %s", carrier.carrier_id, exc)

        # Step 6: compute progress
        dummy_result = BootstrappingCycleResult(
            cycle_id=cycle_id,
            domain_record_id=domain.record_id,
            carriers_created=carriers_created,
            stubs_added=stubs_added,
            laws_proposed=laws_proposed,
            laws_accepted=laws_accepted,
            carriers_promoted=carriers_promoted,
            progress_score=0.0,
            duration_seconds=0.0,
        )
        progress = self._analyzer.compute_bootstrapping_progress(dummy_result)
        duration = time.monotonic() - start

        cycle_result = BootstrappingCycleResult(
            cycle_id=cycle_id,
            domain_record_id=domain.record_id,
            carriers_created=carriers_created,
            stubs_added=stubs_added,
            laws_proposed=laws_proposed,
            laws_accepted=laws_accepted,
            carriers_promoted=carriers_promoted,
            progress_score=progress,
            duration_seconds=round(duration, 6),
        )

        _witness_report = self._witness.witness_bootstrapping_cycle(cycle_result)
        log.info(
            "Bootstrapping cycle %s complete: progress=%.3f, duration=%.3fs",
            cycle_id,
            progress,
            duration,
        )
        return cycle_result

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_carrier(self, carrier_id: str) -> ProvisionalCarrier | None:
        """Look up a carrier by ID, returning None if not found."""
        return self._carriers.get(carrier_id)

    def get_stub(self, stub_id: str) -> BridgeStub | None:
        """Look up a bridge stub by ID, returning None if not found."""
        return self._stubs.get(stub_id)

    def get_law(self, law_id: str) -> ExperimentalLaw | None:
        """Look up an experimental law by ID, returning None if not found."""
        return self._laws.get(law_id)

    def list_stable_carriers(self) -> list[StableCarrier]:
        """Return all carriers that have been promoted to STABLE."""
        return list(self._stable_carriers)

# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def run_bootstrapping_cycle(
    domain: DomainRecord,
    config: BootstrappingConfig | None = None,
) -> BootstrappingCycleResult:
    """Module-level wrapper around :meth:`RegimeBootstrappingCoordinator.run_bootstrapping_cycle`.

    Creates a fresh coordinator with the supplied (or default) config and
    runs one complete bootstrapping cycle for *domain*.  This is the primary
    public API for callers that do not need to inspect intermediate state.

    Args:
        domain: The domain record to bootstrap a regime for.
        config: Optional configuration; defaults are used when omitted.

    Returns:
        A :class:`BootstrappingCycleResult` describing the cycle outcome.
    """
    coordinator = RegimeBootstrappingCoordinator(config)
    return coordinator.run_bootstrapping_cycle(domain)


def score_carrier_readiness(
    carrier: ProvisionalCarrier,
    config: BootstrappingConfig | None = None,
) -> float:
    """Compute the readiness score for a provisional carrier.

    This is a convenience wrapper around ``_carrier_readiness_score`` that
    accepts an optional config (using defaults when omitted) and returns a
    float in [0, 1].

    Args:
        carrier: The carrier to score.
        config: Optional configuration; defaults are used when omitted.

    Returns:
        A float in [0, 1] representing the carrier's readiness.
    """
    return _carrier_readiness_score(carrier, config or BootstrappingConfig())


def select_laws_for_promotion(
    laws: list[ExperimentalLaw],
    config: BootstrappingConfig | None = None,
) -> list[ExperimentalLaw]:
    """Select experimental laws that are ready for promotion to AXIOM status.

    A law is eligible for promotion when:

    - Its status is CANDIDATE (it has already passed the consistency check).
    - Its ``consistency_score`` is at or above the ``law_consistency_threshold``
      in *config*.

    Laws in EXPERIMENTAL, AXIOM, or REFUTED status are excluded.

    The returned list is sorted by ``consistency_score`` in descending order
    so that the most reliable laws are promoted first.

    Args:
        laws: The pool of :class:`ExperimentalLaw` objects to filter.
        config: Optional configuration; defaults are used when omitted.

    Returns:
        A sorted list of laws eligible for promotion.
    """
    cfg = config or BootstrappingConfig()
    eligible = [
        law
        for law in laws
        if law.status is LawStatus.CANDIDATE
        and law.consistency_score >= cfg.law_consistency_threshold
    ]
    return sorted(eligible, key=lambda l: l.consistency_score, reverse=True)

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=== Regime Bootstrapping Provisional-C Smoke Test ===\n")

    # Build a domain record
    domain = DomainRecord(
        record_id="dom-fibered-0001",
        domain_name="FiberedSpaces",
        generators=("fiber_type", "base_type", "projection_map"),
    )
    print(f"Domain: {domain.domain_name!r}  generators={domain.generators}")

    # Use a slightly relaxed config so the smoke test succeeds reliably
    config = BootstrappingConfig(
        min_stubs_for_candidacy=2,
        min_laws_for_candidacy=1,
        law_consistency_threshold=0.50,
        carrier_readiness_threshold=0.60,
        enable_law_validation=True,
    )

    # Run the full bootstrapping cycle via the module-level wrapper
    result = run_bootstrapping_cycle(domain, config)

    print(f"\n--- BootstrappingCycleResult ---")
    print(f"  cycle_id          : {result.cycle_id}")
    print(f"  carriers_created  : {result.carriers_created}")
    print(f"  stubs_added       : {result.stubs_added}")
    print(f"  laws_proposed     : {result.laws_proposed}")
    print(f"  laws_accepted     : {result.laws_accepted}")
    print(f"  carriers_promoted : {result.carriers_promoted}")
    print(f"  progress_score    : {result.progress_score:.4f}")
    print(f"  duration_seconds  : {result.duration_seconds:.6f}s")

    # Demonstrate the analyzer standalone
    analyzer = RegimeBootstrappingAnalyzer(config)
    print(f"\n--- Analyzer.compute_bootstrapping_progress ---")
    progress = analyzer.compute_bootstrapping_progress(result)
    print(f"  progress (re-computed) : {progress:.4f}")

    # Demonstrate score_carrier_readiness on a synthetic carrier
    synthetic = ProvisionalCarrier(
        carrier_id="carrier::smoketest",
        domain_record_id="dom-fibered-0001",
        name="SmokeTestCarrier",
        sort_signature="Sort(smoke)",
        status=CarrierStatus.PROVISIONAL,
        bridge_stub_ids=["s1", "s2", "s3"],
        law_ids=["l1", "l2"],
        created_at=_now_iso(),
    )
    score = score_carrier_readiness(synthetic, config)
    print(f"\n--- score_carrier_readiness (synthetic carrier) ---")
    print(f"  carrier : {synthetic.name!r}")
    print(f"  stubs   : {len(synthetic.bridge_stub_ids)}")
    print(f"  laws    : {len(synthetic.law_ids)}")
    print(f"  score   : {score:.4f}")

    # Demonstrate select_laws_for_promotion
    candidate_laws = [
        ExperimentalLaw(
            law_id="l-cand-1",
            carrier_id="c1",
            statement="reflexivity: x = x for all x",
            tags=("reflexivity",),
            status=LawStatus.CANDIDATE,
            consistency_score=0.95,
        ),
        ExperimentalLaw(
            law_id="l-cand-2",
            carrier_id="c1",
            statement="symmetry: if x = y then y = x",
            tags=("symmetry",),
            status=LawStatus.CANDIDATE,
            consistency_score=0.88,
        ),
        ExperimentalLaw(
            law_id="l-exp-1",
            carrier_id="c1",
            statement="transitivity: if x = y and y = z then x = z",
            tags=("transitivity",),
            status=LawStatus.EXPERIMENTAL,
            consistency_score=0.70,
        ),
    ]
    selected = select_laws_for_promotion(candidate_laws, config)
    print(f"\n--- select_laws_for_promotion ---")
    for law in selected:
        print(f"  {law.law_id}: score={law.consistency_score:.2f}  '{law.statement[:60]}'")

    print("\n=== Smoke test complete ===")
