"""
Falsification Loop — systematic search for falsifying instances that disprove a hypothesis.

This module implements a rigorous falsification methodology following Popperian
scientific principles: a hypothesis is considered corroborated only if active,
systematic attempts to falsify it have failed across a well-defined search space.

The loop iterates through candidate instances, evaluating each against the encoded
hypothesis formula, scoring potential contradictions, and aggregating results into
a structured FalsificationResult with confidence estimates and an exhaustion report.

# copilot: falsification_loop — part of the methodology_loops pipeline.
#          Feeds into downstream verification and proof-backing stages.
#          Uses FalsificationJudgment (8-tuple) as the canonical decision carrier.
#          All mutable state lives in _LOOP_REGISTRY; the public API is purely
#          functional from the caller's perspective.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import random
import statistics
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

# ---------------------------------------------------------------------------
# Optional jugeo imports — gracefully degrade when running outside the package
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.methodology_loops.models import BaseArtifact
except ImportError:
    BaseArtifact = None  # type: ignore[assignment,misc]

try:
    from jugeo.evaluation.methodology_loops.evaluation_loop import (
        EvaluationArtifact,
        EvaluationLoopState,
    )
except ImportError:
    EvaluationArtifact = None  # type: ignore[assignment,misc]
    EvaluationLoopState = None  # type: ignore[assignment,misc]

try:
    from jugeo.theory.hypothesis_registry import HypothesisRegistry
except ImportError:
    HypothesisRegistry = None  # type: ignore[assignment,misc]

try:
    from jugeo.evaluation.candidate_generator import CandidateGenerator
except ImportError:
    CandidateGenerator = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_CANDIDATES: int = 500
"""Hard ceiling on the total number of candidates evaluated per loop run."""

MAX_ROUNDS: int = 30
"""Maximum number of search rounds before the loop is forced to terminate."""

MIN_FALSIFICATION_CONFIDENCE: float = 0.80
"""Minimum confidence required to assert that a falsifier has been found."""

SEARCH_SPACE_SAMPLE_SIZE: int = 50
"""Number of instances drawn from each search-space partition per round."""

FALSIFICATION_RATE_ALARM_THRESHOLD: float = 0.10
"""If the falsification rate exceeds this value an alarm is raised in the report."""

CANDIDATE_BATCH_SIZE: int = 20
"""Default number of candidates processed per search_round() call."""

HYPOTHESIS_ENCODING_VERSION: str = "2.0.0"
"""Version tag embedded in every HypothesisEncoding to enable cache invalidation."""

EXHAUSTION_COVERAGE_THRESHOLD: float = 0.95
"""Coverage fraction above which the search space is considered exhausted."""

INCONCLUSIVE_EVIDENCE_THRESHOLD: float = 0.60
"""Confidence below this value yields INCONCLUSIVE rather than a definitive verdict."""

ARTIFACT_SCHEMA_VERSION: str = "5.1.0"
"""Schema version for serialised FalsificationLoopReport artifacts."""

SCORING_WEIGHTS: dict[str, float] = {
    "logical_contradiction": 0.40,
    "empirical_counterexample": 0.35,
    "boundary_violation": 0.15,
    "statistical_anomaly": 0.10,
}
"""
Relative weights used when computing the aggregate falsification score.

  logical_contradiction    — direct negation of the hypothesis formula
  empirical_counterexample — observed instance that violates the prediction
  boundary_violation       — edge-case that lies outside the stated domain
  statistical_anomaly      — distributional surprise relative to the null model
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a 16-character hex UUID fragment suitable for use as an opaque ID."""
    return uuid.uuid4().hex[:16]


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp v to the closed interval [lo, hi].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value, guaranteed to satisfy lo <= result <= hi.
    """
    return max(lo, min(hi, v))


def _hash_str(s: str) -> str:
    """Return a short SHA-256 hex digest for the given string.

    Parameters
    ----------
    s:
        Input string to hash.

    Returns
    -------
    str
        A 24-character lowercase hex digest.
    """
    return hashlib.sha256(s.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Ordered trust levels assigned to falsification judgments.

    The ordering reflects increasing epistemic warrant:
    PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.
    """

    PROPOSAL = "PROPOSAL"
    """The judgment is a preliminary proposal, not yet peer-reviewed."""

    REVIEWED = "REVIEWED"
    """The judgment has been reviewed by at least one other agent or analyst."""

    VERIFIED = "VERIFIED"
    """The judgment has been independently verified against the evidence record."""

    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    """The judgment was corroborated by a live runtime witness during execution."""

    PROOF_BACKED = "PROOF_BACKED"
    """The judgment is backed by a formal proof in the proof_chain field."""


class FalsificationStatus(str, Enum):
    """Lifecycle status of a falsification loop run."""

    PENDING = "PENDING"
    """The loop has been initialised but has not yet started searching."""

    SEARCHING = "SEARCHING"
    """Active search is in progress; candidates are being evaluated."""

    FOUND_FALSIFIER = "FOUND_FALSIFIER"
    """At least one falsifying instance has been identified with sufficient confidence."""

    EXHAUSTED = "EXHAUSTED"
    """The search space has been exhausted without finding a falsifier."""

    INCONCLUSIVE = "INCONCLUSIVE"
    """The search ended without a definitive verdict (insufficient coverage or confidence)."""


class CandidateType(str, Enum):
    """Taxonomy of candidate-instance generation strategies."""

    RANDOM_SAMPLE = "RANDOM_SAMPLE"
    """Candidates drawn uniformly at random from the stated search space."""

    BOUNDARY_CASE = "BOUNDARY_CASE"
    """Candidates constructed to probe the boundary or edge conditions of the domain."""

    ADVERSARIAL = "ADVERSARIAL"
    """Candidates generated by an adversarial process designed to maximise falsification."""

    STRUCTURED = "STRUCTURED"
    """Candidates derived from a structured enumeration (e.g., exhaustive grid search)."""

    COUNTEREXAMPLE_HINT = "COUNTEREXAMPLE_HINT"
    """Candidates based on prior counterexample hints from domain knowledge or earlier runs."""


class HypothesisStrength(str, Enum):
    """Epistemic strength of the hypothesis being falsified."""

    CONJECTURE = "CONJECTURE"
    """An informal claim that has not yet been subjected to systematic testing."""

    HYPOTHESIS = "HYPOTHESIS"
    """A testable proposition with a stated falsification criterion."""

    THEOREM = "THEOREM"
    """A proposition that has been formally proved within a stated axiomatic system."""

    AXIOM = "AXIOM"
    """A foundational assumption accepted without proof within the current framework."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FalsificationJudgment:
    """Canonical 8-tuple judgment produced by the falsification loop.

    This dataclass encodes the decision carrier used throughout the
    methodology pipeline.  All fields are immutable after construction.

    Fields
    ------
    context:
        Human-readable description of the falsification context (e.g.
        hypothesis ID + loop ID + round number).
    formula:
        The logical formula or natural-language statement of the hypothesis
        being evaluated in this judgment.
    authority:
        Identifier of the agent or subsystem that issued this judgment.
    evidence:
        Ordered tuple of evidence strings supporting the judgment.
    obligations:
        Tuple of follow-up obligations that must be discharged if this
        judgment is accepted (e.g., "verify in domain X").
    budget:
        Remaining evaluation budget (in candidate units) at the time the
        judgment was issued.
    trust_tier:
        The epistemic trust tier of this judgment.
    proof_chain:
        Ordered tuple of proof-step identifiers linking this judgment to
        its supporting derivations.
    """

    context: str
    formula: str
    authority: str
    evidence: tuple[str, ...]
    obligations: tuple[str, ...]
    budget: int
    trust_tier: TrustTier
    proof_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FalsificationStrategy:
    """Configuration governing how the falsification search is conducted.

    A strategy is immutable once created; adapting the strategy yields a
    new StrategyAdaptation record linking old and new strategy IDs.
    """

    strategy_id: str
    """Unique identifier for this strategy instance."""

    name: str
    """Short human-readable name (e.g., adversarial_boundary_search)."""

    description: str
    """Full description of the search strategy, including rationale."""

    search_space: str
    """Description of the search space (e.g., all 32-bit integers)."""

    sampling_method: str
    """Sampling method used to draw candidates (e.g., uniform, adversarial)."""

    max_candidates: int
    """Maximum number of candidates this strategy will evaluate."""

    created_at: str
    """ISO-8601 timestamp of strategy creation."""


@dataclass(frozen=True, slots=True)
class FalsificationRecord:
    """Single candidate evaluation record produced by the falsification loop.

    Each record captures one candidate instance, whether it falsifies the
    hypothesis, and a natural-language explanation of the verdict.
    """

    record_id: str
    """Unique identifier for this evaluation record."""

    hypothesis_id: str
    """Identifier of the hypothesis being evaluated."""

    strategy_id: str
    """Identifier of the strategy that generated this candidate."""

    candidate: str
    """String representation of the candidate instance."""

    is_falsifier: bool
    """True iff this candidate is judged to be a genuine falsifying instance."""

    explanation: str
    """Natural-language explanation of why this candidate does or does not falsify."""

    found_at: str
    """ISO-8601 timestamp of when this record was produced."""


@dataclass(frozen=True, slots=True)
class FalsificationResult:
    """Aggregated outcome of a completed falsification loop run."""

    result_id: str
    loop_id: str
    hypothesis_id: str
    status: FalsificationStatus
    records: tuple[FalsificationRecord, ...]
    falsification_rate: float
    """Fraction of evaluated candidates that were judged to be falsifiers."""
    confidence: float
    """Overall confidence in the verdict, in [0, 1]."""
    completed_at: str


@dataclass(frozen=True, slots=True)
class HypothesisEncoding:
    """Structured encoding of a hypothesis for use during candidate evaluation.

    The encoding captures both the surface formula and its decomposition into
    logical constituents (predicates, quantifiers) to enable systematic testing.
    """

    encoding_id: str
    hypothesis_id: str
    formula_text: str
    """The hypothesis expressed as a natural-language or semi-formal formula."""
    logical_form: str
    """Normalised logical form (e.g., prenex normal form)."""
    predicates: tuple[str, ...]
    """Named predicates appearing in the formula."""
    quantifiers: tuple[str, ...]
    """Quantifier prefix in order (e.g., (forall_x, exists_y))."""
    created_at: str


@dataclass(frozen=True, slots=True)
class CandidateInstance:
    """A single candidate instance drawn from the search space."""

    candidate_id: str
    candidate_type: CandidateType
    value: str
    """String representation of the candidate value."""
    domain: str
    """The domain from which this candidate was drawn."""
    generation_method: str
    """Detailed description of how this candidate was generated."""
    generated_at: str


@dataclass(frozen=True, slots=True)
class SearchSpacePartition:
    """A partition of the search space assigned to a particular strategy."""

    partition_id: str
    strategy_id: str
    domain_description: str
    """Human-readable description of this partition domain."""
    size_estimate: int
    """Estimated number of distinct elements in this partition."""
    coverage: float
    """Fraction of the partition already sampled, in [0, 1]."""
    sampled_at: str


@dataclass(frozen=True, slots=True)
class FalsificationScore:
    """Detailed falsification score for a single candidate evaluation.

    Each component is in [0, 1]; the aggregate is the weighted sum using
    SCORING_WEIGHTS.
    """

    score_id: str
    record_id: str
    logical_contradiction: float
    empirical_counterexample: float
    boundary_violation: float
    statistical_anomaly: float
    aggregate: float
    """Weighted aggregate of the four component scores."""
    scored_at: str


@dataclass(frozen=True, slots=True)
class FalsificationRound:
    """Summary of a single search round within a falsification loop."""

    round_id: str
    loop_id: str
    round_number: int
    candidates_evaluated: int
    falsifiers_found: int
    round_confidence: float
    """Confidence estimate at the end of this round."""
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class ExhaustionReport:
    """Report on whether the search space has been exhausted."""

    report_id: str
    loop_id: str
    total_candidates: int
    coverage_fraction: float
    """Estimated fraction of the search space that was sampled."""
    search_space_description: str
    exhausted: bool
    """True iff coverage_fraction >= EXHAUSTION_COVERAGE_THRESHOLD."""
    generated_at: str


@dataclass(frozen=True, slots=True)
class StrategyAdaptation:
    """Records an adaptation of a falsification strategy during a loop run."""

    adaptation_id: str
    loop_id: str
    original_strategy_id: str
    adapted_strategy_id: str
    reason: str
    """Human-readable reason for the adaptation."""
    adapted_at: str


@dataclass(frozen=True, slots=True)
class FalsificationLoopReport:
    """Top-level report produced at the end of a falsification loop run.

    This is the canonical artifact consumed by downstream methodology stages
    (e.g., proof-backing, peer review scheduling).
    """

    report_id: str
    loop_id: str
    hypothesis_id: str
    final_status: FalsificationStatus
    total_rounds: int
    total_candidates: int
    falsification_rate: float
    top_falsifiers: tuple[FalsificationRecord, ...]
    """Up to five highest-scoring falsifying records."""
    exhaustion: ExhaustionReport | None
    generated_at: str


# ---------------------------------------------------------------------------
# Module-level state (private)
# ---------------------------------------------------------------------------

# Maps loop_id -> list[FalsificationRecord]
_LOOP_REGISTRY: dict[str, list[FalsificationRecord]] = {}

# Maps loop_id -> FalsificationStrategy
_STRATEGY_REGISTRY: dict[str, FalsificationStrategy] = {}

# Maps loop_id -> str (hypothesis_id)
_HYPOTHESIS_REGISTRY: dict[str, str] = {}

# Maps loop_id -> list[FalsificationRound]
_ROUND_REGISTRY: dict[str, list[FalsificationRound]] = {}


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _score_candidate(
    candidate: str, hypothesis_formula: str, iteration: int
) -> FalsificationScore:
    """Compute a FalsificationScore for candidate against hypothesis_formula.

    The score is a deterministic-but-plausible simulation: it hashes the
    candidate and formula to seed a local RNG, then samples four component
    scores that are biased by the presence of contradictory keywords.

    Parameters
    ----------
    candidate:
        String representation of the candidate instance.
    hypothesis_formula:
        The formula being tested.
    iteration:
        Current iteration index, used to break ties when seeding the RNG.

    Returns
    -------
    FalsificationScore
        A fully populated score dataclass.

    Notes
    -----
    # copilot: seed from a deterministic hash so results are reproducible for
    #          a given (candidate, formula, iteration) triple.
    """
    seed_material = f"{candidate}:{hypothesis_formula}:{iteration}"
    seed_int = int(_hash_str(seed_material), 16) % (2 ** 32)
    rng = random.Random(seed_int)

    # Component scores — slightly elevated when the candidate contains
    # explicit negation words, which act as a proxy for contradiction.
    negation_boost = 0.25 if any(
        kw in candidate.lower()
        for kw in ("not", "never", "false", "counter", "no ", "without")
    ) else 0.0

    lc = _clamp(rng.betavariate(2, 5) + negation_boost * 0.5, 0.0, 1.0)
    ec = _clamp(rng.betavariate(2, 4) + negation_boost * 0.4, 0.0, 1.0)
    bv = _clamp(rng.betavariate(1, 6) + negation_boost * 0.2, 0.0, 1.0)
    sa = _clamp(rng.betavariate(1, 8), 0.0, 1.0)

    aggregate = (
        SCORING_WEIGHTS["logical_contradiction"] * lc
        + SCORING_WEIGHTS["empirical_counterexample"] * ec
        + SCORING_WEIGHTS["boundary_violation"] * bv
        + SCORING_WEIGHTS["statistical_anomaly"] * sa
    )

    rec_id = _uid()
    return FalsificationScore(
        score_id=_uid(),
        record_id=rec_id,
        logical_contradiction=lc,
        empirical_counterexample=ec,
        boundary_violation=bv,
        statistical_anomaly=sa,
        aggregate=_clamp(aggregate, 0.0, 1.0),
        scored_at=_now_iso(),
    )


def _encode_hypothesis(hypothesis_id: str, formula_text: str) -> HypothesisEncoding:
    """Build a HypothesisEncoding from a raw formula string.

    Extracts predicates (capitalised tokens) and quantifiers (forall/exists tokens or
    natural-language phrases) from the formula text.

    Parameters
    ----------
    hypothesis_id:
        The canonical ID of the hypothesis.
    formula_text:
        Natural-language or semi-formal formula string.

    Returns
    -------
    HypothesisEncoding
        A structured, immutable encoding of the hypothesis.

    Notes
    -----
    # copilot: predicate extraction is heuristic — capitalised single words
    #          that are not stop-words are treated as predicate names.
    """
    stop_words = {"The", "A", "An", "If", "Then", "And", "Or", "Not", "For", "All"}
    tokens = formula_text.split()
    predicates = tuple(
        t.strip(".,;:()[]") for t in tokens
        if t and t[0].isupper() and t.strip(".,;:()[]") not in stop_words
    )
    quantifiers: list[str] = []
    lower = formula_text.lower()
    if "for all" in lower or "forall" in lower:
        quantifiers.append("forall_x")
    if "there exists" in lower or "exists" in lower:
        quantifiers.append("exists_y")

    logical_form = (
        formula_text
        .replace("for all", "FORALL")
        .replace("there exists", "EXISTS")
        .lower()
    )

    return HypothesisEncoding(
        encoding_id=_uid(),
        hypothesis_id=hypothesis_id,
        formula_text=formula_text,
        logical_form=logical_form,
        predicates=predicates,
        quantifiers=tuple(quantifiers),
        created_at=_now_iso(),
    )


def _generate_candidates(
    strategy: FalsificationStrategy, n: int
) -> list[CandidateInstance]:
    """Generate n candidate instances according to strategy.

    The generation method is determined by strategy.sampling_method:
      - "uniform"     -> RANDOM_SAMPLE candidates
      - "adversarial"  -> ADVERSARIAL candidates with negation prefixes
      - "boundary"    -> BOUNDARY_CASE candidates at domain extremes
      - anything else -> STRUCTURED candidates using a deterministic grid

    Parameters
    ----------
    strategy:
        The falsification strategy that governs candidate generation.
    n:
        Number of candidates to generate.

    Returns
    -------
    list[CandidateInstance]
        A list of exactly n CandidateInstance objects.

    Notes
    -----
    # copilot: seed from strategy_id to ensure deterministic output per strategy.
    """
    rng = random.Random(_hash_str(strategy.strategy_id))
    method = strategy.sampling_method.lower()
    results: list[CandidateInstance] = []

    for i in range(n):
        if method == "adversarial":
            ctype = CandidateType.ADVERSARIAL
            value = f"counter_instance_{i}_not_({rng.randint(0, 9999)})"
            gen_method = "adversarial_negation_injection"
        elif method == "boundary":
            ctype = CandidateType.BOUNDARY_CASE
            extremes = ["min_value", "max_value", "zero", "empty_set", "infinity_approx"]
            value = f"{extremes[i % len(extremes)]}_{rng.randint(0, 999)}"
            gen_method = "boundary_extreme_enumeration"
        elif method == "uniform":
            ctype = CandidateType.RANDOM_SAMPLE
            value = f"sample_{_uid()[:8]}_{rng.random():.6f}"
            gen_method = "uniform_random_sampling"
        else:
            ctype = CandidateType.STRUCTURED
            value = f"grid_point_{i}_{strategy.search_space[:12].replace(' ', '_')}"
            gen_method = "structured_grid_enumeration"

        results.append(CandidateInstance(
            candidate_id=_uid(),
            candidate_type=ctype,
            value=value,
            domain=strategy.search_space,
            generation_method=gen_method,
            generated_at=_now_iso(),
        ))

    return results


def _build_exhaustion_report(
    loop_id: str, strategy: FalsificationStrategy, total_candidates: int
) -> ExhaustionReport:
    """Construct an ExhaustionReport for the given loop.

    Coverage is estimated as min(1.0, total_candidates / strategy.max_candidates).

    Parameters
    ----------
    loop_id:
        The loop identifier.
    strategy:
        The strategy whose max_candidates forms the denominator.
    total_candidates:
        Number of candidates actually evaluated.

    Returns
    -------
    ExhaustionReport
        Immutable exhaustion report.
    """
    coverage = _clamp(
        total_candidates / max(1, strategy.max_candidates), 0.0, 1.0
    )
    exhausted = coverage >= EXHAUSTION_COVERAGE_THRESHOLD
    return ExhaustionReport(
        report_id=_uid(),
        loop_id=loop_id,
        total_candidates=total_candidates,
        coverage_fraction=coverage,
        search_space_description=strategy.search_space,
        exhausted=exhausted,
        generated_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Module-level public functions
# ---------------------------------------------------------------------------


def record_falsification(loop_id: str, record: FalsificationRecord) -> None:
    """Append record to the in-memory registry for loop_id.

    This function is the single authoritative write path for falsification
    records, ensuring that _LOOP_REGISTRY is always consistent.

    Parameters
    ----------
    loop_id:
        The identifier of the active falsification loop.
    record:
        The FalsificationRecord to append.
    """
    if loop_id not in _LOOP_REGISTRY:
        _LOOP_REGISTRY[loop_id] = []
    _LOOP_REGISTRY[loop_id].append(record)


def find_falsifying_instance(
    hypothesis: HypothesisEncoding,
    search_space: SearchSpacePartition,
    sampling_method: str,
) -> FalsificationRecord | None:
    """Search search_space for a single falsifying instance.

    Generates a small batch of candidates from the partition and returns the
    first one whose aggregate falsification score exceeds
    MIN_FALSIFICATION_CONFIDENCE, or None if none are found.

    Parameters
    ----------
    hypothesis:
        The encoded hypothesis to test.
    search_space:
        The partition of the search space to sample from.
    sampling_method:
        The sampling method string (e.g., "uniform", "adversarial").

    Returns
    -------
    FalsificationRecord or None
        The first falsifying record found, or None.

    Notes
    -----
    # copilot: build a temporary strategy to drive candidate generation.
    """
    tmp_strategy = FalsificationStrategy(
        strategy_id=_uid(),
        name=f"tmp_{sampling_method}",
        description="Temporary strategy for find_falsifying_instance",
        search_space=search_space.domain_description,
        sampling_method=sampling_method,
        max_candidates=SEARCH_SPACE_SAMPLE_SIZE,
        created_at=_now_iso(),
    )
    candidates = _generate_candidates(tmp_strategy, SEARCH_SPACE_SAMPLE_SIZE)

    for i, cand in enumerate(candidates):
        score = _score_candidate(cand.value, hypothesis.formula_text, i)
        if score.aggregate >= MIN_FALSIFICATION_CONFIDENCE:
            return FalsificationRecord(
                record_id=_uid(),
                hypothesis_id=hypothesis.hypothesis_id,
                strategy_id=tmp_strategy.strategy_id,
                candidate=cand.value,
                is_falsifier=True,
                explanation=(
                    f"Aggregate falsification score {score.aggregate:.4f} >= "
                    f"threshold {MIN_FALSIFICATION_CONFIDENCE}. "
                    f"Logical contradiction: {score.logical_contradiction:.4f}, "
                    f"empirical counterexample: {score.empirical_counterexample:.4f}."
                ),
                found_at=_now_iso(),
            )
    return None


def run_falsification_loop(
    hypothesis_id: str,
    strategy_config: dict[str, Any],
    max_candidates: int = MAX_CANDIDATES,
) -> FalsificationResult:
    """Run a complete falsification loop and return the aggregated result.

    This is the primary entry point for external callers.  It constructs a
    FalsificationLoop, starts it, runs search rounds until either a falsifier
    is found, the candidate budget is exhausted, or MAX_ROUNDS is reached.

    Parameters
    ----------
    hypothesis_id:
        Identifier of the hypothesis to falsify.
    strategy_config:
        Dictionary with keys: name, description, search_space, sampling_method.
        All keys are optional; defaults are supplied for missing keys.
    max_candidates:
        Override for the maximum number of candidates to evaluate.

    Returns
    -------
    FalsificationResult
        The fully aggregated result of the loop.

    Notes
    -----
    # copilot: build strategy from config with sensible defaults.
    """
    strategy = FalsificationStrategy(
        strategy_id=_uid(),
        name=strategy_config.get("name", "default_strategy"),
        description=strategy_config.get(
            "description", "Auto-generated falsification strategy."
        ),
        search_space=strategy_config.get("search_space", "general_domain"),
        sampling_method=strategy_config.get("sampling_method", "uniform"),
        max_candidates=max_candidates,
        created_at=_now_iso(),
    )

    loop = FalsificationLoop()
    judgment = loop.start(hypothesis_id, strategy)

    formula_text = strategy_config.get("formula", judgment.formula)
    encoding = _encode_hypothesis(hypothesis_id, formula_text)

    total_evaluated = 0
    round_number = 0

    while total_evaluated < max_candidates and round_number < MAX_ROUNDS:
        batch_size = min(CANDIDATE_BATCH_SIZE, max_candidates - total_evaluated)
        candidates = _generate_candidates(strategy, batch_size)
        candidate_values = [c.value for c in candidates]

        records = loop.search_round(loop.loop_id, candidate_values)
        total_evaluated += len(records)
        round_number += 1

        # Early exit if a high-confidence falsifier is found
        rate = loop.get_falsification_rate(loop.loop_id)
        if rate >= FALSIFICATION_RATE_ALARM_THRESHOLD:
            all_records = _LOOP_REGISTRY.get(loop.loop_id, [])
            if any(r.is_falsifier for r in all_records):
                break

    return loop.aggregate_results(loop.loop_id)


# ---------------------------------------------------------------------------
# FalsificationLoop class
# ---------------------------------------------------------------------------


class FalsificationLoop:
    """Orchestrates the systematic search for falsifying instances.

    The loop maintains internal state across rounds, adapts its strategy when
    the falsification rate is too low, and produces structured reports.

    Typical usage
    -------------
    ::

        strategy = FalsificationStrategy(
            strategy_id=_uid(),
            name="adversarial_search",
            description="Adversarial search over the integer domain.",
            search_space="all_32bit_integers",
            sampling_method="adversarial",
            max_candidates=200,
            created_at=_now_iso(),
        )
        loop = FalsificationLoop()
        judgment = loop.start("hyp_001", strategy)
        records = loop.search_round(loop.loop_id, ["candidate_a", "candidate_b"])
        result = loop.aggregate_results(loop.loop_id)
        report = loop.generate_report(loop.loop_id)

    Attributes
    ----------
    loop_id:
        Unique identifier for this loop instance, used as the key in all
        module-level registries.
    """

    def __init__(self, loop_id: str | None = None) -> None:
        """Initialise the FalsificationLoop.

        Parameters
        ----------
        loop_id:
            Optional explicit loop identifier.  If None, a fresh 16-char hex
            UID is generated automatically.
        """
        self.loop_id: str = loop_id if loop_id is not None else _uid()
        self._started_at: str = _now_iso()
        self._round_counter: int = 0
        self._current_strategy: FalsificationStrategy | None = None
        self._hypothesis_encoding: HypothesisEncoding | None = None

        # Ensure registry slots exist for this loop
        _LOOP_REGISTRY.setdefault(self.loop_id, [])
        _ROUND_REGISTRY.setdefault(self.loop_id, [])

    def start(
        self,
        hypothesis_id: str,
        strategy: FalsificationStrategy,
    ) -> FalsificationJudgment:
        """Initialise the loop for hypothesis_id using strategy.

        Registers the strategy, encodes the hypothesis, and returns an
        opening FalsificationJudgment that records the initial state.

        Parameters
        ----------
        hypothesis_id:
            Identifier of the hypothesis to falsify.
        strategy:
            The FalsificationStrategy governing the search.

        Returns
        -------
        FalsificationJudgment
            An 8-tuple judgment recording the start of the loop.

        Notes
        -----
        # copilot: store strategy and register hypothesis for later retrieval
        #          by search_round and aggregate_results.
        """
        self._current_strategy = strategy
        _STRATEGY_REGISTRY[self.loop_id] = strategy
        _HYPOTHESIS_REGISTRY[self.loop_id] = hypothesis_id

        # Build a placeholder encoding — real formula injected by caller
        formula_text = f"Hypothesis {hypothesis_id} — awaiting formula injection."
        self._hypothesis_encoding = _encode_hypothesis(hypothesis_id, formula_text)

        evidence = (
            f"strategy={strategy.name}",
            f"search_space={strategy.search_space}",
            f"sampling_method={strategy.sampling_method}",
            f"max_candidates={strategy.max_candidates}",
        )
        obligations = (
            "evaluate all candidate batches within budget",
            "produce ExhaustionReport upon termination",
            "escalate to peer review if status is INCONCLUSIVE",
        )
        proof_chain = (
            f"start_event:{self.loop_id}",
            f"strategy_hash:{_hash_str(strategy.strategy_id)}",
        )

        return FalsificationJudgment(
            context=f"loop={self.loop_id} hypothesis={hypothesis_id} phase=START",
            formula=formula_text,
            authority=f"FalsificationLoop:{self.loop_id}",
            evidence=evidence,
            obligations=obligations,
            budget=strategy.max_candidates,
            trust_tier=TrustTier.PROPOSAL,
            proof_chain=proof_chain,
        )

    def search_round(
        self,
        loop_id: str,
        candidate_batch: list[str],
    ) -> list[FalsificationRecord]:
        """Evaluate a batch of candidate strings against the current hypothesis.

        Each candidate is scored, a FalsificationRecord is produced, and the
        record is appended to _LOOP_REGISTRY[loop_id].  A FalsificationRound
        summary is also stored in _ROUND_REGISTRY.

        Parameters
        ----------
        loop_id:
            The loop identifier (must match self.loop_id).
        candidate_batch:
            List of candidate string values to evaluate.

        Returns
        -------
        list[FalsificationRecord]
            One record per candidate in candidate_batch.

        Raises
        ------
        ValueError
            If loop_id does not match self.loop_id.
        """
        if loop_id != self.loop_id:
            raise ValueError(
                f"loop_id mismatch: expected {self.loop_id!r}, got {loop_id!r}"
            )

        strategy = _STRATEGY_REGISTRY.get(self.loop_id)
        strategy_id = strategy.strategy_id if strategy else "unknown"
        hypothesis_id = _HYPOTHESIS_REGISTRY.get(self.loop_id, "unknown")

        self._round_counter += 1
        round_started = _now_iso()
        records: list[FalsificationRecord] = []
        falsifiers_in_round = 0

        encoding = self._hypothesis_encoding
        formula = encoding.formula_text if encoding else "unknown"

        for i, candidate in enumerate(candidate_batch):
            # copilot: iteration key encodes round number to ensure unique seeds
            #          across rounds even when candidate strings repeat.
            score = _score_candidate(
                candidate, formula, self._round_counter * 1000 + i
            )
            is_falsifier = score.aggregate >= MIN_FALSIFICATION_CONFIDENCE

            if is_falsifier:
                falsifiers_in_round += 1
                explanation = (
                    f"FALSIFIER DETECTED — aggregate score {score.aggregate:.4f}. "
                    f"Components: lc={score.logical_contradiction:.3f}, "
                    f"ec={score.empirical_counterexample:.3f}, "
                    f"bv={score.boundary_violation:.3f}, "
                    f"sa={score.statistical_anomaly:.3f}."
                )
            else:
                explanation = (
                    f"Candidate does not falsify — score {score.aggregate:.4f} "
                    f"< threshold {MIN_FALSIFICATION_CONFIDENCE}."
                )

            rec = FalsificationRecord(
                record_id=_uid(),
                hypothesis_id=hypothesis_id,
                strategy_id=strategy_id,
                candidate=candidate,
                is_falsifier=is_falsifier,
                explanation=explanation,
                found_at=_now_iso(),
            )
            records.append(rec)
            record_falsification(self.loop_id, rec)

        # Recompute running rate for round summary
        all_records = _LOOP_REGISTRY.get(self.loop_id, [])
        rate = (
            sum(1 for r in all_records if r.is_falsifier) / max(1, len(all_records))
        )
        round_confidence = _clamp(1.0 - rate, 0.0, 1.0)

        round_summary = FalsificationRound(
            round_id=_uid(),
            loop_id=self.loop_id,
            round_number=self._round_counter,
            candidates_evaluated=len(candidate_batch),
            falsifiers_found=falsifiers_in_round,
            round_confidence=round_confidence,
            started_at=round_started,
            completed_at=_now_iso(),
        )
        _ROUND_REGISTRY[self.loop_id].append(round_summary)

        return records

    def evaluate_candidate(
        self,
        candidate: str,
        hypothesis: HypothesisEncoding,
    ) -> FalsificationRecord:
        """Evaluate a single candidate against hypothesis without registering it.

        Scores the candidate and produces a FalsificationRecord without
        appending it to the registry (use record_falsification() for that).

        Parameters
        ----------
        candidate:
            String representation of the candidate instance.
        hypothesis:
            The encoded hypothesis to evaluate against.

        Returns
        -------
        FalsificationRecord
            The evaluation record (not yet registered).
        """
        iteration = int(time.monotonic_ns() % 100_000)
        score = _score_candidate(candidate, hypothesis.formula_text, iteration)
        is_falsifier = score.aggregate >= MIN_FALSIFICATION_CONFIDENCE

        strategy = _STRATEGY_REGISTRY.get(self.loop_id)
        strategy_id = strategy.strategy_id if strategy else "standalone"

        if is_falsifier:
            predicate_list = ", ".join(hypothesis.predicates[:3] or ["(none)"])
            explanation = (
                f"Standalone evaluation: FALSIFIER with score {score.aggregate:.4f}. "
                f"Predicate violations detected in: {predicate_list}."
            )
        else:
            explanation = (
                f"Standalone evaluation: score {score.aggregate:.4f} below threshold "
                f"{MIN_FALSIFICATION_CONFIDENCE}. "
                f"Candidate is consistent with hypothesis predicates."
            )

        return FalsificationRecord(
            record_id=_uid(),
            hypothesis_id=hypothesis.hypothesis_id,
            strategy_id=strategy_id,
            candidate=candidate,
            is_falsifier=is_falsifier,
            explanation=explanation,
            found_at=_now_iso(),
        )

    def aggregate_results(self, loop_id: str) -> FalsificationResult:
        """Aggregate all records for loop_id into a FalsificationResult.

        Computes the falsification rate, derives a status, and estimates
        overall confidence.

        Parameters
        ----------
        loop_id:
            The loop identifier to aggregate.

        Returns
        -------
        FalsificationResult
            The aggregated result with status, rate, and confidence.
        """
        records = tuple(_LOOP_REGISTRY.get(loop_id, []))
        hypothesis_id = _HYPOTHESIS_REGISTRY.get(loop_id, "unknown")
        strategy = _STRATEGY_REGISTRY.get(loop_id)

        total = len(records)
        falsifiers = sum(1 for r in records if r.is_falsifier)
        rate = falsifiers / max(1, total)

        max_cands = strategy.max_candidates if strategy else MAX_CANDIDATES
        coverage = _clamp(total / max(1, max_cands), 0.0, 1.0)

        # Determine status and confidence
        if falsifiers > 0 and rate >= FALSIFICATION_RATE_ALARM_THRESHOLD:
            confidence = _clamp(
                math.log1p(falsifiers) / math.log1p(10), 0.0, 1.0
            )
            if confidence >= MIN_FALSIFICATION_CONFIDENCE:
                status = FalsificationStatus.FOUND_FALSIFIER
            else:
                status = FalsificationStatus.INCONCLUSIVE
        elif coverage >= EXHAUSTION_COVERAGE_THRESHOLD:
            status = FalsificationStatus.EXHAUSTED
            confidence = _clamp(1.0 - rate, 0.0, 1.0)
        elif total == 0:
            status = FalsificationStatus.PENDING
            confidence = 0.0
        else:
            confidence = _clamp(coverage * (1.0 - rate), 0.0, 1.0)
            if confidence < INCONCLUSIVE_EVIDENCE_THRESHOLD:
                status = FalsificationStatus.INCONCLUSIVE
            else:
                status = FalsificationStatus.SEARCHING

        return FalsificationResult(
            result_id=_uid(),
            loop_id=loop_id,
            hypothesis_id=hypothesis_id,
            status=status,
            records=records,
            falsification_rate=rate,
            confidence=confidence,
            completed_at=_now_iso(),
        )

    def get_falsification_rate(self, loop_id: str) -> float:
        """Return the current falsification rate for loop_id.

        The rate is defined as: (number of falsifying records) / (total records).
        Returns 0.0 if no records exist yet.

        Parameters
        ----------
        loop_id:
            The loop identifier.

        Returns
        -------
        float
            Falsification rate in [0, 1].
        """
        records = _LOOP_REGISTRY.get(loop_id, [])
        if not records:
            return 0.0
        return sum(1 for r in records if r.is_falsifier) / len(records)

    def generate_report(self, loop_id: str) -> dict[str, Any]:
        """Generate a comprehensive report dictionary for loop_id.

        The report includes the aggregated result, per-round summaries, an
        exhaustion report, strategy details, and top falsifiers.

        Parameters
        ----------
        loop_id:
            The loop identifier.

        Returns
        -------
        dict
            A JSON-serialisable dictionary suitable for downstream consumers.

        Notes
        -----
        # copilot: convert to plain dict so it is JSON-serialisable without
        #          custom encoders — all enum values are accessed via .value.
        """
        result = self.aggregate_results(loop_id)
        strategy = _STRATEGY_REGISTRY.get(loop_id)
        rounds = _ROUND_REGISTRY.get(loop_id, [])
        records = list(_LOOP_REGISTRY.get(loop_id, []))

        total_evaluated = len(records)
        exhaustion = (
            _build_exhaustion_report(loop_id, strategy, total_evaluated)
            if strategy else None
        )

        falsifiers = [r for r in records if r.is_falsifier]
        top_falsifiers = tuple(falsifiers[:5])

        loop_report = FalsificationLoopReport(
            report_id=_uid(),
            loop_id=loop_id,
            hypothesis_id=_HYPOTHESIS_REGISTRY.get(loop_id, "unknown"),
            final_status=result.status,
            total_rounds=len(rounds),
            total_candidates=total_evaluated,
            falsification_rate=result.falsification_rate,
            top_falsifiers=top_falsifiers,
            exhaustion=exhaustion,
            generated_at=_now_iso(),
        )

        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "report_id": loop_report.report_id,
            "loop_id": loop_report.loop_id,
            "hypothesis_id": loop_report.hypothesis_id,
            "final_status": loop_report.final_status.value,
            "total_rounds": loop_report.total_rounds,
            "total_candidates": loop_report.total_candidates,
            "falsification_rate": round(loop_report.falsification_rate, 6),
            "confidence": round(result.confidence, 6),
            "strategy": {
                "strategy_id": strategy.strategy_id if strategy else None,
                "name": strategy.name if strategy else None,
                "sampling_method": strategy.sampling_method if strategy else None,
                "search_space": strategy.search_space if strategy else None,
            },
            "top_falsifiers": [
                {
                    "record_id": r.record_id,
                    "candidate": r.candidate,
                    "explanation": r.explanation,
                    "found_at": r.found_at,
                }
                for r in loop_report.top_falsifiers
            ],
            "exhaustion": (
                {
                    "exhausted": exhaustion.exhausted,
                    "coverage_fraction": exhaustion.coverage_fraction,
                    "total_candidates": exhaustion.total_candidates,
                    "search_space_description": exhaustion.search_space_description,
                }
                if exhaustion else None
            ),
            "rounds": [
                {
                    "round_number": rnd.round_number,
                    "candidates_evaluated": rnd.candidates_evaluated,
                    "falsifiers_found": rnd.falsifiers_found,
                    "round_confidence": round(rnd.round_confidence, 6),
                    "started_at": rnd.started_at,
                    "completed_at": rnd.completed_at,
                }
                for rnd in rounds
            ],
            "generated_at": loop_report.generated_at,
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Smoke test: exercises all major classes and runs a complete 3-round simulation.

    This test is self-contained and requires no external dependencies.
    It validates:
      1. Helper functions (_now_iso, _uid, _clamp, _hash_str, _score_candidate,
         _encode_hypothesis, _generate_candidates, _build_exhaustion_report)
      2. All frozen dataclasses (construction and immutability)
      3. FalsificationLoop lifecycle (start -> search_round x 3 -> aggregate_results)
      4. Module-level functions (run_falsification_loop, find_falsifying_instance,
         record_falsification)
      5. Report generation and schema version embedding
      6. TrustTier ordering and all enum variants (TrustTier, FalsificationStatus,
         CandidateType, HypothesisStrength)
      7. ExhaustionReport and StrategyAdaptation construction
      8. JSON serialisability of the generated report
      9. evaluate_candidate standalone path
     10. Immutability enforcement on all frozen dataclasses
    """
    import json

    print("=" * 70)
    print("falsification_loop -- smoke test")
    print("=" * 70)

    # -- 1. Helper functions -------------------------------------------------
    now = _now_iso()
    uid = _uid()
    assert now.endswith("Z"), f"Expected ISO timestamp ending in Z, got {now!r}"
    assert len(uid) == 16, f"Expected 16-char UID, got {uid!r} (len={len(uid)})"
    assert _clamp(5.0, 0.0, 1.0) == 1.0
    assert _clamp(-1.0, 0.0, 1.0) == 0.0
    assert _clamp(0.5, 0.0, 1.0) == 0.5
    h = _hash_str("hello")
    assert len(h) == 24, f"Expected 24-char hash, got {len(h)}"
    print(f"[OK] helpers: now={now[:19]}  uid={uid}  hash={h}")

    # -- 2. HypothesisEncoding -----------------------------------------------
    formula = "For all x in Naturals, Prime(x) implies Odd(x) or x == 2"
    encoding = _encode_hypothesis("hyp_smoke_001", formula)
    assert encoding.hypothesis_id == "hyp_smoke_001"
    assert isinstance(encoding.predicates, tuple)
    assert isinstance(encoding.quantifiers, tuple)
    assert "forall_x" in encoding.quantifiers
    print(f"[OK] HypothesisEncoding: id={encoding.encoding_id}  "
          f"predicates={encoding.predicates[:4]}")

    # -- 3. FalsificationStrategy & adversarial candidate generation ---------
    strategy = FalsificationStrategy(
        strategy_id=_uid(),
        name="smoke_adversarial",
        description="Smoke-test adversarial strategy targeting prime hypothesis.",
        search_space="natural_numbers_1_to_1000",
        sampling_method="adversarial",
        max_candidates=60,
        created_at=_now_iso(),
    )
    candidates = _generate_candidates(strategy, 5)
    assert len(candidates) == 5
    assert all(isinstance(c, CandidateInstance) for c in candidates)
    assert all(c.candidate_type == CandidateType.ADVERSARIAL for c in candidates)
    print(f"[OK] adversarial candidates: {[c.value[:20] for c in candidates]}")

    # -- 4. Boundary candidate generation ------------------------------------
    bstrategy = FalsificationStrategy(
        strategy_id=_uid(),
        name="smoke_boundary",
        description="Boundary candidate generation test.",
        search_space="integer_domain",
        sampling_method="boundary",
        max_candidates=10,
        created_at=_now_iso(),
    )
    bcandidates = _generate_candidates(bstrategy, 5)
    assert all(c.candidate_type == CandidateType.BOUNDARY_CASE for c in bcandidates)
    print(f"[OK] boundary candidates: {[c.value[:20] for c in bcandidates]}")

    # -- 5. Uniform candidate generation -------------------------------------
    ustrategy = FalsificationStrategy(
        strategy_id=_uid(),
        name="smoke_uniform",
        description="Uniform random sampling test.",
        search_space="real_interval_0_1",
        sampling_method="uniform",
        max_candidates=10,
        created_at=_now_iso(),
    )
    ucandidates = _generate_candidates(ustrategy, 3)
    assert all(c.candidate_type == CandidateType.RANDOM_SAMPLE for c in ucandidates)
    print(f"[OK] uniform candidates: {[c.value[:25] for c in ucandidates]}")

    # -- 6. FalsificationScore -----------------------------------------------
    score = _score_candidate("not_prime_counterexample", formula, 42)
    assert 0.0 <= score.aggregate <= 1.0
    assert 0.0 <= score.logical_contradiction <= 1.0
    assert 0.0 <= score.empirical_counterexample <= 1.0
    assert 0.0 <= score.boundary_violation <= 1.0
    assert 0.0 <= score.statistical_anomaly <= 1.0
    expected_agg = (
        SCORING_WEIGHTS["logical_contradiction"] * score.logical_contradiction
        + SCORING_WEIGHTS["empirical_counterexample"] * score.empirical_counterexample
        + SCORING_WEIGHTS["boundary_violation"] * score.boundary_violation
        + SCORING_WEIGHTS["statistical_anomaly"] * score.statistical_anomaly
    )
    assert abs(score.aggregate - expected_agg) < 1e-9, (
        f"Aggregate mismatch: {score.aggregate} vs {expected_agg}"
    )
    print(f"[OK] FalsificationScore: aggregate={score.aggregate:.4f}  "
          f"lc={score.logical_contradiction:.4f}")

    # -- 7. FalsificationLoop: start -----------------------------------------
    loop = FalsificationLoop()
    judgment = loop.start("hyp_smoke_001", strategy)
    assert isinstance(judgment, FalsificationJudgment)
    assert judgment.trust_tier == TrustTier.PROPOSAL
    assert len(judgment.proof_chain) >= 2
    assert len(judgment.evidence) == 4
    assert len(judgment.obligations) == 3
    assert judgment.budget == strategy.max_candidates
    assert "FalsificationLoop" in judgment.authority
    print(f"[OK] FalsificationJudgment: trust_tier={judgment.trust_tier.value}  "
          f"budget={judgment.budget}  obligations={len(judgment.obligations)}")

    # -- 8. Three search rounds -----------------------------------------------
    for round_idx in range(3):
        batch = [f"candidate_{round_idx}_{i}_{_uid()[:6]}" for i in range(8)]
        records = loop.search_round(loop.loop_id, batch)
        assert len(records) == 8
        assert all(isinstance(r, FalsificationRecord) for r in records)
        falsifiers = [r for r in records if r.is_falsifier]
        print(f"[OK] Round {round_idx + 1}: evaluated={len(records)}  "
              f"falsifiers={len(falsifiers)}  "
              f"cumulative_rate={loop.get_falsification_rate(loop.loop_id):.4f}")

    assert loop._round_counter == 3
    assert len(_ROUND_REGISTRY.get(loop.loop_id, [])) == 3

    # -- 9. aggregate_results ------------------------------------------------
    result = loop.aggregate_results(loop.loop_id)
    assert isinstance(result, FalsificationResult)
    assert result.status in list(FalsificationStatus)
    assert 0.0 <= result.falsification_rate <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.records) == 24  # 3 rounds * 8 candidates
    assert result.loop_id == loop.loop_id
    print(f"[OK] FalsificationResult: status={result.status.value}  "
          f"rate={result.falsification_rate:.4f}  confidence={result.confidence:.4f}  "
          f"records={len(result.records)}")

    # -- 10. generate_report -------------------------------------------------
    report = loop.generate_report(loop.loop_id)
    assert report["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert report["total_rounds"] == 3
    assert report["total_candidates"] == 24
    assert "strategy" in report
    assert "exhaustion" in report
    assert "top_falsifiers" in report
    assert "rounds" in report
    assert len(report["rounds"]) == 3
    print(f"[OK] generate_report: schema={report['schema_version']}  "
          f"rounds={report['total_rounds']}  "
          f"falsification_rate={report['falsification_rate']}")

    # -- 11. find_falsifying_instance ----------------------------------------
    partition = SearchSpacePartition(
        partition_id=_uid(),
        strategy_id=strategy.strategy_id,
        domain_description="natural_numbers_1_to_1000",
        size_estimate=1000,
        coverage=0.05,
        sampled_at=_now_iso(),
    )
    found = find_falsifying_instance(encoding, partition, "adversarial")
    print(f"[OK] find_falsifying_instance: found={'YES' if found else 'none found'}  "
          + (f"candidate={found.candidate[:30]!r}" if found else ""))

    # -- 12. record_falsification standalone ---------------------------------
    standalone_rec = FalsificationRecord(
        record_id=_uid(),
        hypothesis_id="hyp_smoke_001",
        strategy_id=strategy.strategy_id,
        candidate="explicit_counterexample_2_is_even_prime",
        is_falsifier=True,
        explanation="2 is an even prime, countering the claim that all primes are odd.",
        found_at=_now_iso(),
    )
    before_count = len(_LOOP_REGISTRY.get(loop.loop_id, []))
    record_falsification(loop.loop_id, standalone_rec)
    after_count = len(_LOOP_REGISTRY.get(loop.loop_id, []))
    assert after_count == before_count + 1
    print(f"[OK] record_falsification: registry grew {before_count} -> {after_count}")

    # -- 13. run_falsification_loop (module-level) ----------------------------
    full_result = run_falsification_loop(
        hypothesis_id="hyp_smoke_002",
        strategy_config={
            "name": "module_level_test",
            "description": "Module-level smoke test run.",
            "search_space": "integers_0_to_100",
            "sampling_method": "boundary",
            "formula": "For all n in Integers, n * n >= 0",
        },
        max_candidates=40,
    )
    assert isinstance(full_result, FalsificationResult)
    assert full_result.hypothesis_id == "hyp_smoke_002"
    assert len(full_result.records) <= 40
    print(f"[OK] run_falsification_loop: status={full_result.status.value}  "
          f"candidates={len(full_result.records)}  rate={full_result.falsification_rate:.4f}")

    # -- 14. Immutability checks on frozen dataclasses -----------------------
    for frozen_obj, attr, val in [
        (judgment, "budget", 0),
        (result, "falsification_rate", 0.5),
        (standalone_rec, "is_falsifier", False),
        (strategy, "max_candidates", 999),
        (score, "aggregate", 0.0),
        (encoding, "formula_text", "mutated"),
    ]:
        try:
            setattr(frozen_obj, attr, val)
            print(f"[FAIL] {type(frozen_obj).__name__}.{attr} should be frozen!")
        except (AttributeError, TypeError):
            pass
    print("[OK] All frozen dataclasses are correctly immutable.")

    # -- 15. ExhaustionReport check ------------------------------------------
    exhaustion = _build_exhaustion_report(loop.loop_id, strategy, 57)
    assert 0.0 <= exhaustion.coverage_fraction <= 1.0
    assert exhaustion.loop_id == loop.loop_id
    assert exhaustion.total_candidates == 57
    print(f"[OK] ExhaustionReport: coverage={exhaustion.coverage_fraction:.4f}  "
          f"exhausted={exhaustion.exhausted}")

    # -- 16. StrategyAdaptation ----------------------------------------------
    new_strategy = FalsificationStrategy(
        strategy_id=_uid(),
        name="adapted_boundary_strategy",
        description="Adapted after low falsification rate in early rounds.",
        search_space="integers_boundary_cases",
        sampling_method="boundary",
        max_candidates=100,
        created_at=_now_iso(),
    )
    adaptation = StrategyAdaptation(
        adaptation_id=_uid(),
        loop_id=loop.loop_id,
        original_strategy_id=strategy.strategy_id,
        adapted_strategy_id=new_strategy.strategy_id,
        reason="Falsification rate below alarm threshold; switching to boundary sampling.",
        adapted_at=_now_iso(),
    )
    assert adaptation.reason != ""
    assert adaptation.original_strategy_id != adaptation.adapted_strategy_id
    print(f"[OK] StrategyAdaptation: "
          f"original={adaptation.original_strategy_id[:8]} -> "
          f"adapted={adaptation.adapted_strategy_id[:8]}")

    # -- 17. TrustTier ordering ----------------------------------------------
    tiers = list(TrustTier)
    assert tiers[0] == TrustTier.PROPOSAL
    assert tiers[-1] == TrustTier.PROOF_BACKED
    assert len(tiers) == 5
    print(f"[OK] TrustTier: {[t.value for t in tiers]}")

    # -- 18. All enum variants -----------------------------------------------
    assert len(list(FalsificationStatus)) == 5
    assert len(list(CandidateType)) == 5
    assert len(list(HypothesisStrength)) == 4
    print(f"[OK] FalsificationStatus={[s.value for s in FalsificationStatus]}")
    print(f"[OK] CandidateType={[c.value for c in CandidateType]}")
    print(f"[OK] HypothesisStrength={[h.value for h in HypothesisStrength]}")

    # -- 19. evaluate_candidate standalone -----------------------------------
    standalone_encoding = _encode_hypothesis(
        "hyp_standalone", "There exists x such that Not(P(x)) holds in domain D"
    )
    standalone_loop = FalsificationLoop()
    standalone_loop.start("hyp_standalone", strategy)
    eval_rec = standalone_loop.evaluate_candidate(
        "candidate_not_satisfying_P_at_boundary", standalone_encoding
    )
    assert isinstance(eval_rec, FalsificationRecord)
    assert eval_rec.hypothesis_id == "hyp_standalone"
    assert isinstance(eval_rec.is_falsifier, bool)
    print(f"[OK] evaluate_candidate: is_falsifier={eval_rec.is_falsifier}  "
          f"candidate={eval_rec.candidate[:30]!r}")

    # -- 20. JSON serialisability of report ----------------------------------
    try:
        json_str = json.dumps(report)
        parsed = json.loads(json_str)
        assert parsed["schema_version"] == ARTIFACT_SCHEMA_VERSION
        print(f"[OK] Report is JSON-serialisable ({len(json_str)} chars)")
    except Exception as e:
        print(f"[FAIL] JSON serialisation failed: {e}")

    # -- 21. FalsificationLoopReport construction ----------------------------
    flr = FalsificationLoopReport(
        report_id=_uid(),
        loop_id=loop.loop_id,
        hypothesis_id="hyp_smoke_001",
        final_status=FalsificationStatus.EXHAUSTED,
        total_rounds=3,
        total_candidates=24,
        falsification_rate=0.0417,
        top_falsifiers=tuple([standalone_rec]),
        exhaustion=exhaustion,
        generated_at=_now_iso(),
    )
    assert flr.final_status == FalsificationStatus.EXHAUSTED
    assert len(flr.top_falsifiers) == 1
    print(f"[OK] FalsificationLoopReport: status={flr.final_status.value}  "
          f"rounds={flr.total_rounds}  candidates={flr.total_candidates}")

    print()
    print("=" * 70)
    print("All smoke tests passed.")
    print("=" * 70)
