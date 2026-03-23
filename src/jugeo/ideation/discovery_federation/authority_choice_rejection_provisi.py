"""
Authority Choice: Rejection, Provisional Archive, Federation, and Foundation.

This module implements the authority-choice decision pipeline for the JuGeo
Discovery Federation subsystem (theory2.tex Ch62 §1). When a new theorem is
submitted, the system must assign it exactly one of four authority dispositions:

  REJECT       — The theorem is erroneous, trivially derivable, or irrelevant.
  PROVISIONAL  — The theorem is plausible but unverified; archived pending review.
  FEDERATE     — The theorem is correct, useful, and fits an existing pack.
  FOUNDATION   — The theorem is correct, useful, and requires a new core pack.

The AuthorityChoiceCoordinator drives the full decision sequence. The
AuthorityChoiceAnalyzer scores each candidate disposition. The
AuthorityChoiceWitness records every decision event in an immutable audit log.

copilot: authority-choice marker
theory2.tex Ch62 §1 — Authority Choice Pipeline
"""

from __future__ import annotations

import uuid
import math
import datetime
import itertools
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

try:
    from jugeo.ideation.discovery_federation.models import PackDescriptor  # type: ignore
except ImportError:
    PackDescriptor = None  # type: ignore

try:
    from jugeo.ideation.discovery_federation.algorithms import ScoringEngine  # type: ignore
except ImportError:
    ScoringEngine = None  # type: ignore

try:
    from jugeo.ideation.discovery_federation.integration import FederationBus  # type: ignore
except ImportError:
    FederationBus = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REJECT_THRESHOLD: float = 0.25
"""Score below which a theorem is unconditionally rejected.

A composite authority score beneath this value indicates that the theorem
cannot clear even the most permissive bar for archival. It is either
demonstrably wrong, a trivial corollary of a result already in the pack
registry, or so far outside the current regime that it would poison the
inference graph if admitted.
"""

PROVISIONAL_THRESHOLD: float = 0.50
"""Score at or above REJECT_THRESHOLD but below this triggers provisional archival.

Theorems in this band are plausible and non-trivial but have not been
independently verified by the minimum required number of validation agents.
They are placed in a quarantine zone pending peer review.
"""

FEDERATE_THRESHOLD: float = 0.75
"""Score at or above PROVISIONAL_THRESHOLD but below this triggers federation.

Federation means the theorem is correct, well-formed, and has high affinity
with an existing pack. It is merged into that pack, extending its coverage
without requiring any structural change to the pack registry.
"""

FOUNDATION_THRESHOLD: float = 0.90
"""Score at or above this triggers foundation placement.

Foundation-level theorems are not only correct and novel — they exceed the
structural capacity of all existing packs. Placing them anywhere other than
a newly created core pack would cause conceptual fragmentation.
"""

DEFAULT_TRUST_WEIGHT: float = 0.35
"""Weight applied to submitter_trust when computing the composite score.

Trust is not infallible, but repeated correct submissions from a trusted
agent significantly shift the prior. The weight reflects the epistemic
discount applied to first-party provenance claims.
"""

DEFAULT_NOVELTY_WEIGHT: float = 0.45
"""Weight applied to novelty_score in composite scoring.

Novelty is the single largest contributor because a theorem that merely
restates known results wastes pack capacity and pollutes the inference
graph with redundant paths. High novelty is a necessary (though not
sufficient) condition for both federation and foundation.
"""

DEFAULT_CORRECTNESS_WEIGHT: float = 0.20
"""Weight applied to correctness_score.

Correctness is partially captured by the peer-validation pipeline upstream,
so by the time a candidate reaches this module it has already been filtered.
A non-zero weight retains sensitivity to probabilistic correctness estimates
that the upstream pipeline cannot fully resolve.
"""

# Minimum number of characters required in a theorem_id for it to be considered
# well-formed by this module's validators.
MIN_THEOREM_ID_LENGTH: int = 8

# Maximum novelty score that can be reported by the upstream novelty detector.
# Scores above this are clamped before entering this pipeline.
MAX_RAW_NOVELTY: float = 10.0

# Scale factor used when normalising raw novelty from the upstream detector.
NOVELTY_SCALE: float = 10.0

# Minimum pack_affinity value that enables the FEDERATE path. Below this,
# the theorem cannot be mapped to any existing pack even if its composite
# score is high enough.
MIN_PACK_AFFINITY_FOR_FEDERATE: float = 0.40

# Regime alignment floor required for a theorem to avoid the REJECT path.
MIN_REGIME_ALIGNMENT: float = 0.10

# Maximum number of candidates processed in a single batch_choose call.
BATCH_SIZE_LIMIT: int = 500

# Version tag embedded in every ChoiceRecord for downstream schema management.
RECORD_SCHEMA_VERSION: str = "authority-choice-v1.3"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _utcnow() -> datetime.datetime:
    """Return the current UTC time as a timezone-aware datetime object.

    This wrapper exists so that tests can monkey-patch timestamp generation
    without modifying the standard library's datetime class directly.

    Returns:
        A timezone-aware datetime.datetime set to the current UTC instant.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.datetime.now(datetime.timezone.utc)


def _uid() -> str:
    """Generate a compact, unique identifier string.

    The identifier is derived from a UUID4 and formatted without hyphens
    to make it safe for use in file paths, database keys, and log entries.

    Returns:
        A 32-character hexadecimal string unique to this invocation.

    Example:
        >>> uid = _uid()
        >>> len(uid) == 32
        True
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    This is a pure utility used throughout the scoring pipeline to ensure
    that intermediate arithmetic never produces scores outside their
    defined range.

    Args:
        value: The raw floating-point value to constrain.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        *value* if lo ≤ value ≤ hi, otherwise the nearest bound.

    Raises:
        ValueError: If lo > hi, which would define an empty interval.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
        >>> _clamp(-0.1, 0.0, 1.0)
        0.0
        >>> _clamp(0.5, 0.0, 1.0)
        0.5
    """
    if lo > hi:
        raise ValueError(f"Empty interval: lo={lo!r} > hi={hi!r}")
    return max(lo, min(hi, value))


def _normalize_score(raw: float, scale: float) -> float:
    """Map a raw score on [0, scale] to a normalised score on [0.0, 1.0].

    Division-by-zero is avoided by substituting a near-zero denominator
    with a small epsilon. The result is clamped to [0.0, 1.0] as a final
    safety measure.

    Args:
        raw:   The unnormalised score value, expected to lie in [0, scale].
        scale: The maximum possible value of *raw*.

    Returns:
        A float in [0.0, 1.0] representing the normalised score.

    Raises:
        ValueError: If *scale* is negative.

    Example:
        >>> _normalize_score(5.0, 10.0)
        0.5
        >>> _normalize_score(10.0, 10.0)
        1.0
    """
    if scale < 0:
        raise ValueError(f"Scale must be non-negative, got {scale!r}")
    epsilon = 1e-12
    return _clamp(raw / (scale + epsilon), 0.0, 1.0)


def _weighted_sum(values: list[float], weights: list[float]) -> float:
    """Compute the weighted sum of *values* using *weights*, normalised by total weight.

    This function is used extensively in the AuthorityChoiceAnalyzer to
    combine heterogeneous signals into a single composite score. It
    tolerates mismatched list lengths by truncating to the shorter of the two.

    Args:
        values:  List of score values, each expected in [0.0, 1.0].
        weights: List of non-negative weights corresponding to each value.

    Returns:
        Weighted average as a float in [0.0, 1.0], or 0.0 if total weight is zero.

    Example:
        >>> _weighted_sum([0.8, 0.4], [0.6, 0.4])
        0.64
    """
    pairs = list(itertools.islice(zip(values, weights), min(len(values), len(weights))))
    total_weight = sum(w for _, w in pairs)
    if total_weight < 1e-12:
        return 0.0
    return _clamp(sum(v * w for v, w in pairs) / total_weight, 0.0, 1.0)


def _sigmoid(x: float, steepness: float = 10.0, midpoint: float = 0.5) -> float:
    """Apply a logistic sigmoid transformation centred at *midpoint*.

    The sigmoid is used in the scorer to create soft boundaries between
    disposition categories rather than hard threshold cuts. This makes the
    scoring surface differentiable and less sensitive to floating-point
    noise near the threshold values.

    Args:
        x:          Input value, typically in [0.0, 1.0].
        steepness:  Controls how sharp the S-curve transition is.
        midpoint:   The value of x at which the output equals 0.5.

    Returns:
        A float in (0.0, 1.0) representing the sigmoid output.

    Example:
        >>> round(_sigmoid(0.5), 6)
        0.5
    """
    try:
        return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))
    except OverflowError:
        return 0.0 if x < midpoint else 1.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AuthorityDisposition(str, Enum):
    """Enumeration of the four possible authority dispositions for a theorem.

    Every theorem candidate that passes through the JuGeo Discovery
    Federation pipeline is assigned exactly one of these dispositions.
    The disposition determines which downstream subsystems are notified
    and what structural changes (if any) are made to the pack registry.

    The values are string-typed so that they serialise transparently to
    JSON and can be embedded in log messages without conversion.

    Members:
        REJECT:      The theorem is erroneous, trivially derivable, or
                     so far outside the current regime as to be useless.
                     No record is created in the pack registry; only the
                     audit log receives an entry.

        PROVISIONAL: The theorem is plausible and non-trivial but has not
                     yet accumulated sufficient verification signals. It is
                     placed in the provisional archive, from which it may
                     be promoted or demoted on a future review cycle.

        FEDERATE:    The theorem is correct, well-verified, and has strong
                     pack affinity. It is merged into the most affine
                     existing pack. No new packs are created.

        FOUNDATION:  The theorem is correct, maximally novel, and exceeds
                     the structural capacity of all existing packs. A new
                     core pack is created to contain it and any related
                     theorems that will follow.
    """

    REJECT = "REJECT"
    PROVISIONAL = "PROVISIONAL"
    FEDERATE = "FEDERATE"
    FOUNDATION = "FOUNDATION"


class ChoiceReason(str, Enum):
    """Enumeration of the specific reason codes attached to an authority choice.

    A ChoiceReason narrows down *why* a particular AuthorityDisposition was
    selected. Multiple reasons may apply internally, but only the primary
    reason is recorded in the ChoiceRecord to keep the audit trail readable.

    Members:
        ERRONEOUS:            A formal verification step detected a logical
                              error in the theorem's proof or statement.

        TRIVIAL:              The theorem is a trivial corollary of one or
                              more theorems already present in the registry.

        IRRELEVANT:           The theorem lies entirely outside the known
                              regimes and cannot be linked to any pack.

        UNVERIFIED:           The theorem has not yet been confirmed by
                              the required number of independent validators.

        LOW_NOVELTY:          The theorem's novelty score falls below the
                              threshold required for federation or foundation.

        PACK_FIT:             The theorem fits naturally into an existing
                              pack with high affinity — federation is optimal.

        NOVELTY_EXCEEDS_PACK: The theorem's novelty exceeds what any existing
                              pack can absorb; a new foundation pack is needed.

        REGIME_MISMATCH:      The theorem's regime alignment is too low to
                              justify any positive disposition.

        TRUST_DEFICIT:        The submitter's trust score is too low to admit
                              the theorem without additional verification.

        BATCH_OVERFLOW:       The batch_choose call exceeded BATCH_SIZE_LIMIT
                              and this record was generated as a fallback.
    """

    ERRONEOUS = "ERRONEOUS"
    TRIVIAL = "TRIVIAL"
    IRRELEVANT = "IRRELEVANT"
    UNVERIFIED = "UNVERIFIED"
    LOW_NOVELTY = "LOW_NOVELTY"
    PACK_FIT = "PACK_FIT"
    NOVELTY_EXCEEDS_PACK = "NOVELTY_EXCEEDS_PACK"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    TRUST_DEFICIT = "TRUST_DEFICIT"
    BATCH_OVERFLOW = "BATCH_OVERFLOW"


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremCandidate:
    """Immutable snapshot of a theorem's scoring attributes at submission time.

    A TheoremCandidate is the primary input to the AuthorityChoiceAnalyzer.
    It encodes the five dimensions that determine which authority disposition
    a theorem receives. All numeric fields must lie in [0.0, 1.0] after
    normalisation; the coordinator enforces this contract via _clamp before
    constructing candidates.

    The frozen/slots combination makes TheoremCandidate safe for use as a
    dictionary key and as an element of sets, which is useful when deduplicating
    large batches submitted by federated agents that may repeat the same theorem
    under slightly different IDs.

    Fields:
        theorem_id:        A globally unique identifier for this theorem.
                           Must be at least MIN_THEOREM_ID_LENGTH characters.
                           Typically a UUID4 hex string generated by the
                           upstream submission gateway.

        novelty_score:     A float in [0.0, 1.0] representing how structurally
                           novel the theorem is relative to the current pack
                           registry. Computed by the upstream novelty detector.
                           A score of 1.0 means no existing theorem subsumes
                           it; 0.0 means it is identical to an existing one.

        correctness_score: A float in [0.0, 1.0] representing the probability
                           that the theorem's proof is logically valid, as
                           estimated by the automated verification pipeline.
                           Human review may later override this estimate.

        pack_affinity:     A float in [0.0, 1.0] representing how well the
                           theorem fits the most affine existing pack.
                           High affinity (> FEDERATE_THRESHOLD) makes
                           federation the natural choice. Low affinity
                           (< MIN_PACK_AFFINITY_FOR_FEDERATE) blocks federation.

        regime_alignment:  A float in [0.0, 1.0] indicating how well the
                           theorem's domain overlaps with the currently active
                           inference regimes. A theorem with low regime
                           alignment may be technically correct but practically
                           useless for the current research programme.

        submitter_trust:   A float in [0.0, 1.0] representing the historical
                           reliability of the agent that submitted this
                           theorem. Derived from the submitter's track record
                           across previous submissions in the federation log.
    """

    theorem_id: str
    novelty_score: float
    correctness_score: float
    pack_affinity: float
    regime_alignment: float
    submitter_trust: float


@dataclass(frozen=True, slots=True)
class ChoiceRecord:
    """Immutable audit record produced for every authority-choice decision.

    A ChoiceRecord is written to the AuthorityChoiceWitness log regardless
    of the disposition chosen. Even rejected theorems produce a record so
    that the audit trail remains complete and retrospective analysis can
    identify patterns in what gets rejected.

    The schema version field allows downstream consumers to detect format
    changes introduced between releases of the authority-choice pipeline.

    Fields:
        candidate_id:    The theorem_id of the TheoremCandidate that was
                         evaluated.  Carried forward verbatim from the
                         candidate so that records can be joined with
                         upstream submission logs.

        disposition:     The AuthorityDisposition assigned by the coordinator.
                         This is the primary output of the pipeline.

        reason:          The primary ChoiceReason that determined the
                         disposition. Secondary reasons are not recorded
                         in this field to keep the schema stable, but they
                         are embedded in the coordinator's internal state
                         for diagnostic purposes.

        score:           The composite authority score (a float in [0.0, 1.0])
                         at the moment of decision. This is the value that
                         was compared against the threshold constants.

        timestamp:       UTC datetime at which the coordinator produced this
                         record.  Always timezone-aware.

        auditor_id:      Identifier of the coordinator instance that made
                         this decision.  Useful for debugging multi-instance
                         deployments where multiple coordinators may be
                         running concurrently.

        schema_version:  Version string of the record schema, defaults to
                         RECORD_SCHEMA_VERSION. Downstream consumers should
                         reject records with unrecognised versions.
    """

    candidate_id: str
    disposition: AuthorityDisposition
    reason: ChoiceReason
    score: float
    timestamp: datetime.datetime
    auditor_id: str
    schema_version: str = RECORD_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class AuthorityChoiceAnalyzer:
    """Scores a TheoremCandidate against each of the four authority dispositions.

    The AuthorityChoiceAnalyzer is the computational core of the authority-choice
    pipeline. It exposes five methods: one for each of the four dispositions plus
    a ranking method that returns all four scores in descending order. The
    coordinator calls rank_dispositions to select the winning disposition.

    Each scoring method applies a different combination of the candidate's fields
    weighted by the module-level constants. The weights encode the JuGeo Design
    Committee's policy about which signals matter most for each disposition:

    - REJECT scoring down-weights novelty (because a novel-but-wrong theorem is
      still rejected) and up-weights the inverse of correctness.
    - PROVISIONAL scoring looks for plausibility without verification; it rewards
      moderate novelty and moderate correctness.
    - FEDERATE scoring prioritises pack_affinity and regime_alignment because
      a federated theorem must slot into an existing structure.
    - FOUNDATION scoring prioritises novelty_score and penalises pack_affinity
      because a foundational theorem by definition cannot fit anywhere else.

    The analyzer is stateless: every call to any scoring method is a pure
    function of its inputs. This allows the same instance to be shared across
    multiple coordinators in a multi-threaded environment.

    Attributes:
        trust_weight:       Weight for submitter_trust in composite calculations.
        novelty_weight:     Weight for novelty_score in composite calculations.
        correctness_weight: Weight for correctness_score in composite calculations.
    """

    def __init__(
        self,
        trust_weight: float = DEFAULT_TRUST_WEIGHT,
        novelty_weight: float = DEFAULT_NOVELTY_WEIGHT,
        correctness_weight: float = DEFAULT_CORRECTNESS_WEIGHT,
    ) -> None:
        """Initialise the analyzer with configurable scoring weights.

        Args:
            trust_weight:       Contribution of submitter_trust to the composite
                                score. Defaults to DEFAULT_TRUST_WEIGHT.
            novelty_weight:     Contribution of novelty_score to the composite
                                score. Defaults to DEFAULT_NOVELTY_WEIGHT.
            correctness_weight: Contribution of correctness_score to the composite
                                score. Defaults to DEFAULT_CORRECTNESS_WEIGHT.

        Raises:
            ValueError: If any weight is negative.

        Example:
            >>> analyzer = AuthorityChoiceAnalyzer()
            >>> analyzer.trust_weight == DEFAULT_TRUST_WEIGHT
            True
        """
        if any(w < 0 for w in (trust_weight, novelty_weight, correctness_weight)):
            raise ValueError("All weights must be non-negative.")
        self.trust_weight = trust_weight
        self.novelty_weight = novelty_weight
        self.correctness_weight = correctness_weight

    def score_reject(self, candidate: TheoremCandidate) -> float:
        """Compute the REJECT disposition score for *candidate*.

        A high REJECT score means the system is confident the theorem should
        be discarded. The score is dominated by low correctness and low regime
        alignment; novelty is deliberately down-weighted because a novel-but-
        wrong theorem is still rejected. The sigmoid transformation creates
        a sharp boundary near low correctness values.

        The logic applied here follows theory2.tex Ch62 §1.2 "Rejection Criteria":
        (a) If correctness_score < 0.15, the theorem is almost certainly wrong
            and should be rejected regardless of other signals.
        (b) If regime_alignment < MIN_REGIME_ALIGNMENT, the theorem cannot be
            placed anywhere useful.
        (c) If pack_affinity is very low AND novelty is also low, the theorem
            provides no value.

        Args:
            candidate: A TheoremCandidate with normalised float fields.

        Returns:
            A float in [0.0, 1.0] where higher values indicate stronger
            evidence that the theorem should be rejected.

        Raises:
            TypeError: If *candidate* is not a TheoremCandidate instance.

        Example:
            >>> c = TheoremCandidate("abc12345", 0.1, 0.05, 0.1, 0.05, 0.2)
            >>> AuthorityChoiceAnalyzer().score_reject(c) > 0.7
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")

        # Primary signal: inverse of correctness (low correctness → high reject)
        inv_correctness = 1.0 - candidate.correctness_score

        # Secondary signal: inverse of regime alignment
        inv_regime = 1.0 - candidate.regime_alignment

        # Tertiary signal: inverse of novelty (trivial theorems should be rejected)
        inv_novelty = 1.0 - candidate.novelty_score

        # Hard override: near-zero correctness triggers maximum rejection signal
        hard_override = 1.0 if candidate.correctness_score < 0.15 else 0.0

        # Compute base weighted sum
        base = _weighted_sum(
            [inv_correctness, inv_regime, inv_novelty],
            [0.50, 0.30, 0.20],
        )

        # Blend with hard override
        blended = _clamp(base * 0.70 + hard_override * 0.30, 0.0, 1.0)

        # Apply sigmoid to sharpen the boundary
        sharpened = _sigmoid(blended, steepness=8.0, midpoint=0.45)

        # Trust deficit penalty: low trust amplifies rejection signal
        trust_penalty = (1.0 - candidate.submitter_trust) * 0.10
        final = _clamp(sharpened + trust_penalty, 0.0, 1.0)

        return final

    def score_provisional(self, candidate: TheoremCandidate) -> float:
        """Compute the PROVISIONAL disposition score for *candidate*.

        A high PROVISIONAL score means the theorem is plausible and worth
        archiving but has not been sufficiently verified. The scoring logic
        rewards moderate correctness (indicating plausibility but not proof)
        and moderate novelty (indicating the theorem is not trivial).

        The provisional band is specifically designed to capture theorems
        that a cautious human reviewer would say "I think this is right but
        I want to see it checked". Very high correctness should *reduce*
        the provisional score (because the theorem can be federated or
        given a foundation), while very low correctness reduces it (because
        the theorem should be rejected instead).

        Args:
            candidate: A TheoremCandidate with normalised float fields.

        Returns:
            A float in [0.0, 1.0] where higher values indicate stronger
            evidence that the theorem should be provisionally archived.

        Raises:
            TypeError: If *candidate* is not a TheoremCandidate instance.

        Example:
            >>> c = TheoremCandidate("abc12345", 0.45, 0.40, 0.3, 0.5, 0.6)
            >>> 0.3 < AuthorityChoiceAnalyzer().score_provisional(c) < 0.8
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")

        # Provisional score peaks in the middle of the correctness range.
        # We use a bell-shaped function centred at 0.45.
        correctness_bell = math.exp(-((candidate.correctness_score - 0.45) ** 2) / 0.08)

        # Novelty contribution: moderate novelty is best for provisional
        novelty_bell = math.exp(-((candidate.novelty_score - 0.50) ** 2) / 0.10)

        # Regime alignment penalty: completely irrelevant theorems should not
        # even be provisionally archived.
        regime_factor = _clamp(candidate.regime_alignment / (MIN_REGIME_ALIGNMENT + 0.20), 0.0, 1.0)

        # Trust modulator: low trust increases the chance that provisional is
        # the right disposition (because we want more validation before full
        # federation), but very low trust might push toward reject.
        trust_modulator = _clamp(0.30 + candidate.submitter_trust * 0.40, 0.30, 0.70)

        # Pack affinity neutral: provisional is not about pack fit.
        # Combine the signals.
        raw = (correctness_bell * 0.40 + novelty_bell * 0.30 + regime_factor * 0.20) * trust_modulator

        return _clamp(raw, 0.0, 1.0)

    def score_federate(self, candidate: TheoremCandidate) -> float:
        """Compute the FEDERATE disposition score for *candidate*.

        A high FEDERATE score means the theorem is correct, useful, and
        naturally maps into an existing pack. The dominant signals are
        pack_affinity and regime_alignment. Novelty is required to be
        moderate-to-high (to avoid federating trivial corollaries), but
        it must not be so extreme that the theorem outgrows any existing
        pack (which would indicate FOUNDATION instead).

        The scoring logic encodes theory2.tex Ch62 §1.4 "Federation Eligibility":
        (a) pack_affinity must exceed MIN_PACK_AFFINITY_FOR_FEDERATE.
        (b) correctness_score must be high enough to avoid provisional status.
        (c) The novelty window for federation is roughly [0.40, 0.88].

        Args:
            candidate: A TheoremCandidate with normalised float fields.

        Returns:
            A float in [0.0, 1.0] where higher values indicate stronger
            evidence that the theorem should be federated into an existing pack.

        Raises:
            TypeError: If *candidate* is not a TheoremCandidate instance.

        Example:
            >>> c = TheoremCandidate("abc12345", 0.65, 0.85, 0.80, 0.75, 0.9)
            >>> AuthorityChoiceAnalyzer().score_federate(c) > 0.6
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")

        # Hard gate: insufficient pack affinity means federation is impossible.
        if candidate.pack_affinity < MIN_PACK_AFFINITY_FOR_FEDERATE:
            return 0.0

        # Pack affinity is the primary signal.
        pack_signal = _sigmoid(candidate.pack_affinity, steepness=12.0, midpoint=0.60)

        # Correctness gate: federated theorems must be well-verified.
        correctness_gate = _sigmoid(candidate.correctness_score, steepness=10.0, midpoint=0.55)

        # Novelty window: prefer moderate-to-high novelty.
        # Penalise theorems that are either too trivial or too extreme.
        novelty_lo_penalty = _clamp(1.0 - candidate.novelty_score / 0.40, 0.0, 1.0) if candidate.novelty_score < 0.40 else 0.0
        novelty_hi_penalty = _clamp((candidate.novelty_score - 0.88) / 0.12, 0.0, 1.0) if candidate.novelty_score > 0.88 else 0.0
        novelty_factor = _clamp(1.0 - novelty_lo_penalty - novelty_hi_penalty, 0.0, 1.0)

        # Regime alignment enhancer
        regime_boost = _clamp(candidate.regime_alignment * 0.20, 0.0, 0.20)

        # Trust enhancer: high trust makes federation more attractive.
        trust_boost = _clamp(candidate.submitter_trust * 0.10, 0.0, 0.10)

        raw = pack_signal * 0.40 + correctness_gate * 0.30 + novelty_factor * 0.20 + regime_boost + trust_boost
        return _clamp(raw, 0.0, 1.0)

    def score_foundation(self, candidate: TheoremCandidate) -> float:
        """Compute the FOUNDATION disposition score for *candidate*.

        A high FOUNDATION score means the theorem is so novel and structurally
        important that it cannot be absorbed by any existing pack. The dominant
        signal is novelty_score; high pack_affinity *reduces* the foundation
        score because a theorem that fits an existing pack does not need its
        own core pack.

        The scoring logic encodes theory2.tex Ch62 §1.5 "Foundation Eligibility":
        (a) novelty_score must exceed a high floor (close to FOUNDATION_THRESHOLD).
        (b) pack_affinity must be relatively low.
        (c) regime_alignment must be positive (the theorem must be relevant).
        (d) correctness_score must be very high.

        Args:
            candidate: A TheoremCandidate with normalised float fields.

        Returns:
            A float in [0.0, 1.0] where higher values indicate stronger
            evidence that the theorem deserves its own foundational core pack.

        Raises:
            TypeError: If *candidate* is not a TheoremCandidate instance.

        Example:
            >>> c = TheoremCandidate("abc12345", 0.95, 0.92, 0.15, 0.80, 0.95)
            >>> AuthorityChoiceAnalyzer().score_foundation(c) > 0.7
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")

        # Novelty is the dominant signal for foundation.
        novelty_signal = _sigmoid(candidate.novelty_score, steepness=14.0, midpoint=0.82)

        # Correctness must be very high for a foundational theorem.
        correctness_signal = _sigmoid(candidate.correctness_score, steepness=12.0, midpoint=0.80)

        # High pack affinity penalises foundation: if it fits an existing pack,
        # creating a new one would be wasteful (bridge burden).
        pack_penalty = candidate.pack_affinity * 0.35

        # Regime alignment is required: a foundational theorem must be relevant.
        regime_signal = _clamp(candidate.regime_alignment, 0.0, 1.0) * 0.15

        # Trust amplifier: foundational claims from trusted submitters are given
        # more weight because the consequences of a wrong foundation are severe.
        trust_amp = 0.80 + candidate.submitter_trust * 0.20

        raw = (novelty_signal * 0.45 + correctness_signal * 0.35 + regime_signal) * trust_amp - pack_penalty
        return _clamp(raw, 0.0, 1.0)

    def rank_dispositions(
        self, candidate: TheoremCandidate
    ) -> list[tuple[AuthorityDisposition, float]]:
        """Rank all four dispositions by their scores for *candidate*.

        This method calls the four individual scoring methods and returns a
        sorted list of (disposition, score) tuples in descending order of
        score. The coordinator uses the first element of this list to select
        the winning disposition.

        The method also logs the full ranking internally so that downstream
        diagnostic tools can reconstruct the decision path.

        Args:
            candidate: A TheoremCandidate with normalised float fields.

        Returns:
            A list of four (AuthorityDisposition, float) tuples sorted by
            score descending, i.e., the best-scoring disposition is first.

        Raises:
            TypeError: If *candidate* is not a TheoremCandidate instance.

        Example:
            >>> c = TheoremCandidate("abc12345", 0.9, 0.9, 0.2, 0.8, 0.95)
            >>> ranks = AuthorityChoiceAnalyzer().rank_dispositions(c)
            >>> ranks[0][0] == AuthorityDisposition.FOUNDATION
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")

        scores = {
            AuthorityDisposition.REJECT: self.score_reject(candidate),
            AuthorityDisposition.PROVISIONAL: self.score_provisional(candidate),
            AuthorityDisposition.FEDERATE: self.score_federate(candidate),
            AuthorityDisposition.FOUNDATION: self.score_foundation(candidate),
        }

        # Sort by score descending, using the enum value as a tiebreaker for
        # deterministic ordering when two scores are exactly equal.
        ranked = sorted(
            scores.items(),
            key=lambda kv: (-kv[1], kv[0].value),
        )
        return ranked


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class AuthorityChoiceCoordinator:
    """Orchestrates the full authority-choice decision sequence for theorem candidates.

    The AuthorityChoiceCoordinator is the entry point for all external callers
    that need to assign authority dispositions to theorems. It wraps the
    AuthorityChoiceAnalyzer with pre/post-processing logic: input validation,
    threshold comparison, reason code selection, record construction, and
    optional witness notification.

    The coordinator enforces the following invariants:
      1. Every candidate receives exactly one disposition.
      2. Every decision produces exactly one ChoiceRecord.
      3. Batch operations are limited to BATCH_SIZE_LIMIT candidates to
         protect against denial-of-service scenarios.
      4. The coordinator is re-entrant: multiple threads may call choose()
         concurrently without sharing mutable state.

    Attributes:
        coordinator_id: A unique identifier for this coordinator instance.
        analyzer:       The AuthorityChoiceAnalyzer used for scoring.
        witness:        An optional AuthorityChoiceWitness that receives
                        every ChoiceRecord produced by this coordinator.
                        May be None if auditing is disabled.
    """

    def __init__(
        self,
        analyzer: AuthorityChoiceAnalyzer | None = None,
        witness: "AuthorityChoiceWitness | None" = None,
    ) -> None:
        """Initialise the coordinator with an optional analyzer and witness.

        If *analyzer* is None, a default AuthorityChoiceAnalyzer is constructed
        using the module-level weight constants. If *witness* is None, decision
        records are produced but not stored; callers that need auditing must
        either supply a witness or collect the return values themselves.

        Args:
            analyzer: AuthorityChoiceAnalyzer instance. Created with defaults
                      if None.
            witness:  AuthorityChoiceWitness instance to receive every record.
                      May be None if auditing is disabled.

        Example:
            >>> coord = AuthorityChoiceCoordinator()
            >>> coord.coordinator_id is not None
            True
        """
        self.coordinator_id: str = _uid()
        self.analyzer: AuthorityChoiceAnalyzer = analyzer or AuthorityChoiceAnalyzer()
        self.witness: "AuthorityChoiceWitness | None" = witness

    def choose(self, candidate: TheoremCandidate) -> ChoiceRecord:
        """Assign an authority disposition to a single TheoremCandidate.

        The choose method performs the following steps:
          1. Validate that the candidate's theorem_id meets the minimum length
             requirement.
          2. Normalise all numeric fields with _clamp to ensure they lie in
             [0.0, 1.0].
          3. Call rank_dispositions to obtain the ordered score list.
          4. Compare the top score against the threshold hierarchy to select
             the winning disposition (this step may override the raw winner
             when the winning score does not clear the appropriate threshold).
          5. Select the primary ChoiceReason based on the winning disposition
             and the candidate's field values.
          6. Construct and return a ChoiceRecord.
          7. Notify the witness (if any) of the new record.

        Args:
            candidate: The TheoremCandidate to evaluate.

        Returns:
            A ChoiceRecord describing the disposition, reason, score,
            timestamp, and auditor ID for this decision.

        Raises:
            ValueError: If theorem_id is shorter than MIN_THEOREM_ID_LENGTH.
            TypeError:  If *candidate* is not a TheoremCandidate.

        Example:
            >>> coord = AuthorityChoiceCoordinator()
            >>> c = TheoremCandidate("theorem01", 0.9, 0.9, 0.1, 0.8, 0.9)
            >>> rec = coord.choose(c)
            >>> rec.disposition in list(AuthorityDisposition)
            True
        """
        if not isinstance(candidate, TheoremCandidate):
            raise TypeError(f"Expected TheoremCandidate, got {type(candidate)}")
        if len(candidate.theorem_id) < MIN_THEOREM_ID_LENGTH:
            raise ValueError(
                f"theorem_id '{candidate.theorem_id}' is shorter than "
                f"MIN_THEOREM_ID_LENGTH={MIN_THEOREM_ID_LENGTH}"
            )

        # Rebuild a normalised candidate (immutable, so construct a new one)
        normed = TheoremCandidate(
            theorem_id=candidate.theorem_id,
            novelty_score=_clamp(candidate.novelty_score, 0.0, 1.0),
            correctness_score=_clamp(candidate.correctness_score, 0.0, 1.0),
            pack_affinity=_clamp(candidate.pack_affinity, 0.0, 1.0),
            regime_alignment=_clamp(candidate.regime_alignment, 0.0, 1.0),
            submitter_trust=_clamp(candidate.submitter_trust, 0.0, 1.0),
        )

        # Obtain ranked dispositions from the analyzer
        ranked = self.analyzer.rank_dispositions(normed)
        top_disposition, top_score = ranked[0]

        # Threshold enforcement: even if the top scorer is FOUNDATION, if
        # the score doesn't clear FOUNDATION_THRESHOLD we fall back.
        disposition, score = self._apply_threshold_policy(normed, top_disposition, top_score, ranked)

        # Determine primary reason
        reason = self._select_reason(normed, disposition)

        record = ChoiceRecord(
            candidate_id=normed.theorem_id,
            disposition=disposition,
            reason=reason,
            score=round(score, 6),
            timestamp=_utcnow(),
            auditor_id=self.coordinator_id,
        )

        if self.witness is not None:
            self.witness.observe(record)

        return record

    def _apply_threshold_policy(
        self,
        candidate: TheoremCandidate,
        raw_winner: AuthorityDisposition,
        raw_score: float,
        ranked: list[tuple[AuthorityDisposition, float]],
    ) -> tuple[AuthorityDisposition, float]:
        """Apply the threshold ladder to select the final disposition.

        This private method implements the threshold cascade described in
        theory2.tex Ch62 §1.3. The raw winner from the scorer may not
        clear its corresponding threshold; in that case, we fall back to
        the next-best disposition that does clear its threshold.

        Args:
            candidate:   The normalised TheoremCandidate.
            raw_winner:  The disposition with the highest raw score.
            raw_score:   The score of the raw winner.
            ranked:      Full ranked list from rank_dispositions.

        Returns:
            A (disposition, score) tuple representing the final decision.
        """
        score_map = dict(ranked)

        # FOUNDATION: needs score ≥ FOUNDATION_THRESHOLD
        if score_map.get(AuthorityDisposition.FOUNDATION, 0.0) >= FOUNDATION_THRESHOLD:
            return AuthorityDisposition.FOUNDATION, score_map[AuthorityDisposition.FOUNDATION]

        # FEDERATE: needs score ≥ FEDERATE_THRESHOLD and sufficient pack affinity
        if (score_map.get(AuthorityDisposition.FEDERATE, 0.0) >= FEDERATE_THRESHOLD
                and candidate.pack_affinity >= MIN_PACK_AFFINITY_FOR_FEDERATE):
            return AuthorityDisposition.FEDERATE, score_map[AuthorityDisposition.FEDERATE]

        # PROVISIONAL: needs score ≥ PROVISIONAL_THRESHOLD
        if score_map.get(AuthorityDisposition.PROVISIONAL, 0.0) >= PROVISIONAL_THRESHOLD:
            return AuthorityDisposition.PROVISIONAL, score_map[AuthorityDisposition.PROVISIONAL]

        # Default: REJECT
        return AuthorityDisposition.REJECT, score_map.get(AuthorityDisposition.REJECT, 1.0)

    def _select_reason(
        self, candidate: TheoremCandidate, disposition: AuthorityDisposition
    ) -> ChoiceReason:
        """Derive the primary ChoiceReason for the given disposition.

        Args:
            candidate:   The normalised TheoremCandidate.
            disposition: The final AuthorityDisposition assigned.

        Returns:
            The most descriptive ChoiceReason for this disposition.
        """
        if disposition == AuthorityDisposition.REJECT:
            if candidate.correctness_score < 0.15:
                return ChoiceReason.ERRONEOUS
            if candidate.novelty_score < 0.15:
                return ChoiceReason.TRIVIAL
            if candidate.regime_alignment < MIN_REGIME_ALIGNMENT:
                return ChoiceReason.REGIME_MISMATCH
            if candidate.submitter_trust < 0.20:
                return ChoiceReason.TRUST_DEFICIT
            return ChoiceReason.IRRELEVANT

        if disposition == AuthorityDisposition.PROVISIONAL:
            return ChoiceReason.UNVERIFIED

        if disposition == AuthorityDisposition.FEDERATE:
            return ChoiceReason.PACK_FIT

        # FOUNDATION
        return ChoiceReason.NOVELTY_EXCEEDS_PACK

    def batch_choose(self, candidates: list[TheoremCandidate]) -> list[ChoiceRecord]:
        """Process a list of TheoremCandidates and return their ChoiceRecords.

        Batch processing is limited to BATCH_SIZE_LIMIT candidates per call.
        Candidates beyond the limit are assigned REJECT with BATCH_OVERFLOW
        reason to signal that they were not evaluated.

        Args:
            candidates: A list of TheoremCandidate objects to process.

        Returns:
            A list of ChoiceRecord objects, one per candidate, in the same
            order as the input list.

        Raises:
            TypeError: If *candidates* is not a list.

        Example:
            >>> coord = AuthorityChoiceCoordinator()
            >>> c = TheoremCandidate("theorem01", 0.8, 0.85, 0.7, 0.8, 0.9)
            >>> records = coord.batch_choose([c])
            >>> len(records) == 1
            True
        """
        if not isinstance(candidates, list):
            raise TypeError(f"Expected list, got {type(candidates)}")

        records: list[ChoiceRecord] = []
        for idx, candidate in enumerate(candidates):
            if idx >= BATCH_SIZE_LIMIT:
                # Overflow fallback
                overflow_record = ChoiceRecord(
                    candidate_id=getattr(candidate, "theorem_id", "unknown"),
                    disposition=AuthorityDisposition.REJECT,
                    reason=ChoiceReason.BATCH_OVERFLOW,
                    score=0.0,
                    timestamp=_utcnow(),
                    auditor_id=self.coordinator_id,
                )
                records.append(overflow_record)
                continue
            records.append(self.choose(candidate))

        return records

    def summarize(self, records: list[ChoiceRecord]) -> dict[str, Any]:
        """Produce a statistical summary of a list of ChoiceRecords.

        The summary includes counts and percentages for each disposition,
        the mean and standard deviation of the composite scores, the most
        common reason codes, and metadata about the batch.

        Args:
            records: A list of ChoiceRecord objects to summarize.

        Returns:
            A dict with keys: disposition_counts, disposition_pcts,
            mean_score, std_score, reason_counts, record_count,
            coordinator_id.

        Example:
            >>> coord = AuthorityChoiceCoordinator()
            >>> summary = coord.summarize([])
            >>> summary["record_count"] == 0
            True
        """
        n = len(records)
        if n == 0:
            return {
                "disposition_counts": {},
                "disposition_pcts": {},
                "mean_score": 0.0,
                "std_score": 0.0,
                "reason_counts": {},
                "record_count": 0,
                "coordinator_id": self.coordinator_id,
            }

        disposition_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        scores: list[float] = []

        for rec in records:
            d_key = rec.disposition.value
            disposition_counts[d_key] = disposition_counts.get(d_key, 0) + 1
            r_key = rec.reason.value
            reason_counts[r_key] = reason_counts.get(r_key, 0) + 1
            scores.append(rec.score)

        mean_score = sum(scores) / n
        variance = sum((s - mean_score) ** 2 for s in scores) / n
        std_score = math.sqrt(variance)
        disposition_pcts = {k: round(v / n * 100, 2) for k, v in disposition_counts.items()}

        return {
            "disposition_counts": disposition_counts,
            "disposition_pcts": disposition_pcts,
            "mean_score": round(mean_score, 6),
            "std_score": round(std_score, 6),
            "reason_counts": reason_counts,
            "record_count": n,
            "coordinator_id": self.coordinator_id,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class AuthorityChoiceWitness:
    """Immutable audit log for all authority-choice decisions.

    The AuthorityChoiceWitness records every ChoiceRecord produced by the
    coordinator and exposes replay and summary methods for retrospective
    analysis. It is designed to be instantiated once and shared across
    coordinator instances.

    The internal log is a plain Python list and is therefore not thread-safe
    for concurrent writes. In a multi-threaded environment, callers should
    use a thread-safe subclass or wrap observe() with a lock.

    Attributes:
        witness_id: A unique identifier for this witness instance.
    """

    def __init__(self) -> None:
        """Initialise the witness with an empty audit log.

        Example:
            >>> w = AuthorityChoiceWitness()
            >>> w.audit_summary()["total_records"] == 0
            True
        """
        self.witness_id: str = _uid()
        self._log: list[ChoiceRecord] = []

    def observe(self, record: ChoiceRecord) -> None:
        """Append *record* to the internal audit log.

        This method is called by the coordinator after every successful
        choose() invocation. It is intentionally side-effect-only: it
        returns None and does not transform the record in any way.

        Args:
            record: The ChoiceRecord to persist in the log.

        Raises:
            TypeError: If *record* is not a ChoiceRecord instance.

        Example:
            >>> w = AuthorityChoiceWitness()
            >>> rec = ChoiceRecord("t1234567", AuthorityDisposition.REJECT,
            ...     ChoiceReason.TRIVIAL, 0.1, _utcnow(), "coord-1")
            >>> w.observe(rec)
            >>> len(w._log) == 1
            True
        """
        if not isinstance(record, ChoiceRecord):
            raise TypeError(f"Expected ChoiceRecord, got {type(record)}")
        self._log.append(record)

    def replay(
        self,
        from_ts: datetime.datetime | None = None,
        to_ts: datetime.datetime | None = None,
    ) -> list[ChoiceRecord]:
        """Return all records whose timestamp falls within [from_ts, to_ts].

        Both bounds are inclusive. If *from_ts* is None, the replay starts
        from the beginning of the log. If *to_ts* is None, the replay
        extends to the most recent record.

        Args:
            from_ts: Lower bound (inclusive) for the timestamp filter.
                     May be None to start from the beginning.
            to_ts:   Upper bound (inclusive) for the timestamp filter.
                     May be None to include all records up to the present.

        Returns:
            A list of ChoiceRecord objects matching the time window,
            in chronological order.

        Raises:
            ValueError: If both bounds are provided and from_ts > to_ts.

        Example:
            >>> w = AuthorityChoiceWitness()
            >>> records = w.replay()
            >>> isinstance(records, list)
            True
        """
        if from_ts is not None and to_ts is not None and from_ts > to_ts:
            raise ValueError(f"from_ts {from_ts!r} is after to_ts {to_ts!r}")

        result: list[ChoiceRecord] = []
        for rec in self._log:
            if from_ts is not None and rec.timestamp < from_ts:
                continue
            if to_ts is not None and rec.timestamp > to_ts:
                continue
            result.append(rec)
        return result

    def audit_summary(self) -> dict[str, Any]:
        """Return a high-level statistical summary of all records in the log.

        The summary is suitable for embedding in operational dashboards or
        automated health checks. It includes total record count, per-
        disposition counts, and the IDs of the first and last records.

        Returns:
            A dict with keys: total_records, disposition_counts,
            first_record_ts, last_record_ts, witness_id.

        Example:
            >>> w = AuthorityChoiceWitness()
            >>> s = w.audit_summary()
            >>> "total_records" in s
            True
        """
        total = len(self._log)
        disp_counts: dict[str, int] = {}
        for rec in self._log:
            key = rec.disposition.value
            disp_counts[key] = disp_counts.get(key, 0) + 1

        first_ts = self._log[0].timestamp.isoformat() if self._log else None
        last_ts = self._log[-1].timestamp.isoformat() if self._log else None

        return {
            "total_records": total,
            "disposition_counts": disp_counts,
            "first_record_ts": first_ts,
            "last_record_ts": last_ts,
            "witness_id": self.witness_id,
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Authority Choice Pipeline — Smoke Test")
    print("=" * 70)

    # Instantiate all three main classes
    witness = AuthorityChoiceWitness()
    analyzer = AuthorityChoiceAnalyzer()
    coordinator = AuthorityChoiceCoordinator(analyzer=analyzer, witness=witness)

    # Define a representative set of candidates covering all four dispositions
    candidates = [
        TheoremCandidate(
            theorem_id="thm-reject-00",
            novelty_score=0.05,
            correctness_score=0.05,
            pack_affinity=0.05,
            regime_alignment=0.03,
            submitter_trust=0.10,
        ),
        TheoremCandidate(
            theorem_id="thm-provis-01",
            novelty_score=0.45,
            correctness_score=0.40,
            pack_affinity=0.30,
            regime_alignment=0.55,
            submitter_trust=0.60,
        ),
        TheoremCandidate(
            theorem_id="thm-federt-02",
            novelty_score=0.65,
            correctness_score=0.88,
            pack_affinity=0.82,
            regime_alignment=0.78,
            submitter_trust=0.90,
        ),
        TheoremCandidate(
            theorem_id="thm-foundn-03",
            novelty_score=0.96,
            correctness_score=0.93,
            pack_affinity=0.12,
            regime_alignment=0.85,
            submitter_trust=0.95,
        ),
    ]

    print("\n--- Individual rankings ---")
    for cand in candidates:
        ranks = analyzer.rank_dispositions(cand)
        print(f"  {cand.theorem_id}: top={ranks[0][0].value} ({ranks[0][1]:.4f})")

    print("\n--- Batch choose ---")
    records = coordinator.batch_choose(candidates)
    for rec in records:
        print(
            f"  {rec.candidate_id}: {rec.disposition.value:12s} "
            f"reason={rec.reason.value:20s} score={rec.score:.4f}"
        )

    print("\n--- Coordinator summary ---")
    summary = coordinator.summarize(records)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n--- Witness audit summary ---")
    audit = witness.audit_summary()
    for k, v in audit.items():
        print(f"  {k}: {v}")

    print("\n--- Replay (all records) ---")
    replayed = witness.replay()
    print(f"  Replayed {len(replayed)} records")

    print("\nSmoke test PASSED.")
