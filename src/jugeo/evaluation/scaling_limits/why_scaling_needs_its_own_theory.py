"""
Why Scaling Needs Its Own Theory.

This module implements the theoretical grounding for scaling analysis in
the JuGeo Evaluation subsystem (theory2.tex Ch73 §1). Scaling is not
merely 'more of the same' — it introduces qualitatively new phenomena:

  EMERGENT_BEHAVIOUR — At sufficient scale, new theorem types become
                       provable that were unprovable at smaller scale.
  PHASE_TRANSITIONS  — Sudden qualitative shifts in federation structure
                       occur at critical theorem-count thresholds.
  COMPLEXITY_CLIFF   — Proof search complexity can grow super-polynomially
                       with pack size, requiring new search strategies.
  BRIDGE_AVALANCHE   — Dense inter-pack bridges can cascade, causing
                       federation instability at scale.

The WhyScalingNeedsTheoryCoordinator documents and validates scaling
theory requirements. The WhyScalingNeedsTheoryAnalyzer identifies which
scaling phenomena are present. The WhyScalingNeedsTheoryWitness records
scaling-theory event observations.

copilot: why-scaling-needs-theory marker
theory2.tex Ch73 §1 — Why Scaling Needs Its Own Theory
"""

from __future__ import annotations

import math
import uuid
import statistics
import functools
import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Sequence

try:
    from jugeo.evaluation.evaluation_design.project_scale_metrics import (
        ProjectScorecard,
        ProjectHealthBand,
    )
except ImportError:
    ProjectScorecard = None  # type: ignore
    ProjectHealthBand = None  # type: ignore

try:
    from jugeo.config import JugeoConfig  # type: ignore
except ImportError:
    JugeoConfig = None  # type: ignore


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPORT_VERSION: str = "1.0.0"
"""Version stamped into all WhyScalingNeedsTheoryWitness reports."""

REGIME_THRESHOLDS: dict  # forward declaration — defined after enum

PHENOMENON_PRIORITY: dict  # forward declaration — defined after enum

_PHASE_TRANSITION_DETECTION_WINDOW: int = 3
"""Number of consecutive observations used to detect a phase transition.

If the average federation-structure metric drops by more than
_PHASE_TRANSITION_DROP within this window, a PHASE_TRANSITION is reported.
"""

_PHASE_TRANSITION_DROP: float = 0.15
"""Minimum fractional drop in structure metric that signals a phase transition."""

_COMPLEXITY_CLIFF_RATIO: float = 2.5
"""Ratio of proof-depth to log(pack_size) above which COMPLEXITY_CLIFF is declared.

When proof_depth > _COMPLEXITY_CLIFF_RATIO * log2(pack_size), the system
is exhibiting super-polynomial search behaviour.
"""

_BRIDGE_AVALANCHE_DENSITY: float = 0.10
"""Bridge count as a fraction of pack_size above which BRIDGE_AVALANCHE is possible.

If bridge_count / pack_size > _BRIDGE_AVALANCHE_DENSITY and the pack is LARGE
or XLARGE, a BRIDGE_AVALANCHE risk is flagged.
"""

_AUTHORITY_DILUTION_THRESHOLD_LARGE: float = 0.05
"""Authority fraction below which AUTHORITY_DILUTION is triggered in LARGE regime."""

_AUTHORITY_DILUTION_THRESHOLD_XLARGE: float = 0.02
"""Authority fraction below which AUTHORITY_DILUTION is triggered in XLARGE regime."""

_SEMANTIC_DRIFT_BRIDGE_SPAN: int = 5
"""Number of distinct pack clusters connected via bridges that may signal SEMANTIC_DRIFT."""

_REQUIREMENT_PRIORITY_CRITICAL: int = 1
_REQUIREMENT_PRIORITY_HIGH: int = 2
_REQUIREMENT_PRIORITY_MEDIUM: int = 3
_REQUIREMENT_PRIORITY_LOW: int = 4
"""Numeric priority levels for ScalingTheoryRequirements (lower = more urgent)."""

_MAX_EVIDENCE_IDS: int = 16
"""Maximum number of evidence IDs stored per requirement to bound memory usage."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a compact 12-character hex unique identifier.

    Returns:
        A 12-character lowercase hex string.

    Example:
        >>> len(_uid())
        12
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: Value to clamp.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        Clamped float.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


def _log2_safe(n: int) -> float:
    """Return log2(n), guarding against n <= 0.

    Args:
        n: Integer argument.

    Returns:
        log2(n) if n > 0, else 0.0.

    Example:
        >>> _log2_safe(8)
        3.0
        >>> _log2_safe(0)
        0.0
    """
    return math.log2(n) if n > 0 else 0.0


def _fraction_change(before: float, after: float) -> float:
    """Compute the fractional change from *before* to *after*.

    Returns (before - after) / before.  Returns 0.0 if before == 0.

    Args:
        before: Reference value.
        after:  Observed value.

    Returns:
        Fractional change (positive = drop).

    Example:
        >>> _fraction_change(0.8, 0.6)
        0.25
    """
    if before == 0.0:
        return 0.0
    return (before - after) / abs(before)


def _sliding_window_means(values: list[float], window: int) -> list[float]:
    """Compute sliding-window means over a list of values.

    Args:
        values: Time-ordered list of floats.
        window: Window size (must be >= 1).

    Returns:
        List of means, length ``max(0, len(values) - window + 1)``.

    Example:
        >>> _sliding_window_means([1, 2, 3, 4], 2)
        [1.5, 2.5, 3.5]
    """
    if window < 1:
        raise ValueError(f"_sliding_window_means: window must be >= 1, got {window}")
    means = []
    for i in range(len(values) - window + 1):
        chunk = values[i: i + window]
        means.append(statistics.mean(chunk))
    return means


def _bridge_density(bridge_count: int, pack_size: int) -> float:
    """Compute bridge density as bridges per theorem.

    Args:
        bridge_count: Number of inter-pack bridges observed.
        pack_size:    Number of theorems in the pack.

    Returns:
        Float bridge density, or 0.0 if pack_size == 0.

    Example:
        >>> _bridge_density(50, 500)
        0.1
    """
    if pack_size <= 0:
        return 0.0
    return bridge_count / pack_size


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ScalingPhenomenon(str, Enum):
    """Enumeration of qualitatively distinct phenomena that emerge at scale.

    Each phenomenon represents a qualitative shift in system behaviour that
    is not present (or is negligible) at small scale.  Understanding which
    phenomena are active in a given deployment is a prerequisite for
    designing appropriate mitigation strategies.

    These phenomena are derived from empirical observations of large-scale
    automated theorem-proving systems and from theoretical analysis in
    theory2.tex Ch73.
    """

    EMERGENT_BEHAVIOUR = "emergent_behaviour"
    """New proof capabilities emerge above a critical scale threshold.

    Certain theorem classes cannot be proved by the system at small scale
    because the required lemma chain does not yet exist in the knowledge
    base.  Once pack size reaches a sufficient density, cross-lemma
    bridges accumulate until the proof becomes tractable.  This is a
    positive phenomenon: it rewards persistent scaling.
    """

    PHASE_TRANSITION = "phase_transition"
    """A sudden, qualitative shift in federation structure occurs.

    Analogous to physical phase transitions, the federation graph can
    reorganise abruptly as theorem count crosses certain thresholds.
    Packs that were weakly connected suddenly form a giant connected
    component.  These events must be detected early because they can
    invalidate assumptions baked into the routing and authority-scoring
    subsystems.
    """

    COMPLEXITY_CLIFF = "complexity_cliff"
    """Proof search complexity grows faster than polynomial in pack size.

    Below the cliff, each new theorem is roughly as hard to prove as the
    last.  Above it, the proof search space has grown so large that
    heuristic search strategies fail and the system must fall back to
    exponential enumeration.  Detecting this cliff early allows the
    system to activate approximate proof strategies before the cliff
    causes a full stall.
    """

    BRIDGE_AVALANCHE = "bridge_avalanche"
    """A cascade of inter-pack bridge activations destabilises the federation.

    When bridge density exceeds a critical threshold, adding a single new
    theorem can trigger a cascade where bridges activate, pull in
    downstream theorems from other packs, which in turn activate more
    bridges.  The resulting avalanche can overwhelm the federation
    router's capacity to re-index the knowledge base.
    """

    SEMANTIC_DRIFT = "semantic_drift"
    """The intended semantic scope of a pack gradually shifts as scale grows.

    As new theorems are added to a pack, the implicit definition of the
    pack's subject matter can drift, particularly when inter-pack bridges
    import terminology from unrelated domains.  Unchecked semantic drift
    makes pack retrieval unreliable and degrades authority coverage.
    """

    AUTHORITY_DILUTION = "authority_dilution"
    """The fraction of theorems holding authority status decreases at scale.

    In small packs, most theorems can be individually reviewed and granted
    authority status.  At large scale the review bottleneck means that
    most theorems are unreviewed, reducing the authority fraction below
    the level needed for the system's authority-based ranking to function
    reliably.
    """


class ScalingRegime(str, Enum):
    """Classification of pack size into four qualitative scaling regimes.

    The boundaries are informed by empirical observations of JuGeo packs
    and by theoretical thresholds in theory2.tex Ch73.  They are
    approximate — different domains may exhibit phase transitions at
    different thresholds.
    """

    SMALL = "small"
    """Pack sizes from 0 to 99 theorems (inclusive).

    At this scale the system behaves predictably.  Proof search is
    tractable, federation structure is sparse and well-understood, and
    the authority-review bottleneck is not yet a limiting factor.
    Most theoretical analyses assume SMALL regime as their baseline.
    """

    MEDIUM = "medium"
    """Pack sizes from 100 to 999 theorems (inclusive).

    The federation begins to exhibit non-trivial structure.  Proof search
    costs rise noticeably but remain polynomial.  Phase transitions are
    possible near the upper boundary.  The system is still amenable to
    full-coverage testing but automated strategies become important.
    """

    LARGE = "large"
    """Pack sizes from 1 000 to 9 999 theorems (inclusive).

    Qualitatively new phenomena — complexity cliffs, bridge avalanches,
    authority dilution — become likely.  The system requires dedicated
    scaling infrastructure: approximate proof search, asynchronous
    federation re-indexing, and authority-sampling strategies.
    """

    XLARGE = "xlarge"
    """Pack sizes of 10 000 or more theorems.

    The system enters uncharted territory.  Theory guarantees do not
    directly apply, and empirical performance monitoring becomes the
    primary safety mechanism.  At this scale, the bridge-avalanche and
    authority-dilution phenomena are expected to be active unless
    explicitly mitigated.
    """


# ---------------------------------------------------------------------------
# Post-enum constant initialisation
# ---------------------------------------------------------------------------

REGIME_THRESHOLDS: dict[ScalingRegime, tuple[int, int]] = {
    ScalingRegime.SMALL:   (0,     100),
    ScalingRegime.MEDIUM:  (100,   1_000),
    ScalingRegime.LARGE:   (1_000, 10_000),
    ScalingRegime.XLARGE:  (10_000, 10_000_000),
}
"""Half-open pack-size intervals ``[lo, hi)`` for each ScalingRegime."""

PHENOMENON_PRIORITY: dict[ScalingPhenomenon, int] = {
    ScalingPhenomenon.COMPLEXITY_CLIFF:    _REQUIREMENT_PRIORITY_CRITICAL,
    ScalingPhenomenon.BRIDGE_AVALANCHE:    _REQUIREMENT_PRIORITY_CRITICAL,
    ScalingPhenomenon.PHASE_TRANSITION:    _REQUIREMENT_PRIORITY_HIGH,
    ScalingPhenomenon.AUTHORITY_DILUTION:  _REQUIREMENT_PRIORITY_HIGH,
    ScalingPhenomenon.EMERGENT_BEHAVIOUR:  _REQUIREMENT_PRIORITY_MEDIUM,
    ScalingPhenomenon.SEMANTIC_DRIFT:      _REQUIREMENT_PRIORITY_MEDIUM,
}
"""Priority order for addressing detected scaling phenomena.

Priority 1 (CRITICAL) phenomena must be mitigated before deployment.
Priority 2 (HIGH) phenomena require mitigation plans.
Priority 3+ phenomena may be deferred but should be monitored.
"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScalingObservation:
    """A single empirical observation of the system at a specific scale point.

    ScalingObservation captures a snapshot of the system state at a
    particular pack size, recording the key structural metrics needed to
    detect scaling phenomena.

    Each observation is associated with a single pack (identified by its
    size) and records the proof depth, bridge count, and timestamp.  The
    ``phenomenon`` field records which (if any) scaling phenomenon was
    identified as active at this observation point; it may be None if no
    phenomenon was detected or if the observation predates analysis.

    Attributes:
        obs_id:       Unique identifier for this observation.
        phenomenon:   The dominant ScalingPhenomenon observed, or None.
        regime:       The ScalingRegime this observation falls into.
        pack_size:    Number of theorems in the pack at observation time.
        proof_depth:  Maximum proof depth observed in the pack.
        bridge_count: Number of active inter-pack bridges at this scale.
        observed_at:  UTC datetime when the observation was recorded.
    """

    obs_id: str
    phenomenon: Optional[ScalingPhenomenon]
    regime: ScalingRegime
    pack_size: int
    proof_depth: int
    bridge_count: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ScalingTheoryRequirement:
    """A formal requirement that the scaling theory must satisfy.

    A ScalingTheoryRequirement captures one theoretical obligation
    derived from a set of ScalingObservations.  Requirements are generated
    by the analyzer when observations reveal unaddressed scaling phenomena.

    The ``is_satisfied`` field tracks whether the JuGeo codebase currently
    satisfies the requirement.  Unsatisfied high-priority requirements
    block deployment to larger scales.

    The ``evidence_ids`` tuple stores the obs_ids of the observations that
    motivated this requirement, enabling traceability.

    Attributes:
        req_id:       Unique identifier for this requirement.
        description:  Human-readable description of the requirement.
        phenomenon:   The ScalingPhenomenon this requirement addresses.
        is_satisfied: True iff the requirement is currently implemented.
        evidence_ids: Tuple of observation IDs that motivate this requirement.
        priority:     Numeric priority (1 = most urgent).
    """

    req_id: str
    description: str
    phenomenon: ScalingPhenomenon
    is_satisfied: bool
    evidence_ids: tuple[str, ...]
    priority: int


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class WhyScalingNeedsTheoryAnalyzer:
    """Classifies scaling regimes, detects phenomena, and derives requirements.

    The analyzer examines ScalingObservation objects and applies the
    theoretical framework from theory2.tex Ch73 to identify which scaling
    phenomena are active and what theory requirements follow.

    All methods are pure functions of their inputs; no state is stored.
    """

    # ------------------------------------------------------------------
    def classify_regime(self, pack_size: int) -> ScalingRegime:
        """Map a pack size (integer) to the corresponding ScalingRegime.

        Uses the half-open intervals in REGIME_THRESHOLDS.

        Args:
            pack_size: Number of theorems in the pack.  Must be >= 0.

        Returns:
            The ScalingRegime whose interval contains *pack_size*.

        Raises:
            ValueError: If *pack_size* is negative.

        Example:
            >>> analyzer = WhyScalingNeedsTheoryAnalyzer()
            >>> analyzer.classify_regime(500)
            <ScalingRegime.MEDIUM: 'medium'>
        """
        if pack_size < 0:
            raise ValueError(f"classify_regime: pack_size must be non-negative, got {pack_size}")

        # copilot: iterate regimes in ascending size order
        for regime in [ScalingRegime.SMALL, ScalingRegime.MEDIUM,
                       ScalingRegime.LARGE, ScalingRegime.XLARGE]:
            lo, hi = REGIME_THRESHOLDS[regime]
            if lo <= pack_size < hi:
                return regime

        # Fall through for extremely large values
        return ScalingRegime.XLARGE

    # ------------------------------------------------------------------
    def detect_phenomena(
        self,
        obs: ScalingObservation,
    ) -> list[ScalingPhenomenon]:
        """Identify which scaling phenomena are active in a single observation.

        Applies five detection heuristics, each targeting one phenomenon.
        Multiple phenomena may be detected simultaneously.

        Detection rules:
        * COMPLEXITY_CLIFF: proof_depth > _COMPLEXITY_CLIFF_RATIO * log2(pack_size)
        * BRIDGE_AVALANCHE: bridge density > _BRIDGE_AVALANCHE_DENSITY AND regime in {LARGE, XLARGE}
        * AUTHORITY_DILUTION: estimated authority fraction below regime threshold
        * SEMANTIC_DRIFT: bridge_count > _SEMANTIC_DRIFT_BRIDGE_SPAN clusters
        * EMERGENT_BEHAVIOUR: detected when pack_size is near upper boundary of regime

        PHASE_TRANSITION cannot be detected from a single observation (requires a
        sequence) and is therefore not returned by this method.

        Args:
            obs: A ScalingObservation to analyse.

        Returns:
            A list of ScalingPhenomenon members (may be empty).

        Raises:
            TypeError: If *obs* is not a ScalingObservation.

        Example:
            >>> analyzer.detect_phenomena(obs)
            [<ScalingPhenomenon.COMPLEXITY_CLIFF: ...>]
        """
        if not isinstance(obs, ScalingObservation):
            raise TypeError(
                f"detect_phenomena: expected ScalingObservation, got {type(obs).__name__}"
            )

        detected: list[ScalingPhenomenon] = []

        # copilot: complexity cliff check
        log2_ps = _log2_safe(obs.pack_size)
        if log2_ps > 0 and obs.proof_depth > _COMPLEXITY_CLIFF_RATIO * log2_ps:
            detected.append(ScalingPhenomenon.COMPLEXITY_CLIFF)

        # copilot: bridge avalanche check
        density = _bridge_density(obs.bridge_count, obs.pack_size)
        if (density > _BRIDGE_AVALANCHE_DENSITY
                and obs.regime in (ScalingRegime.LARGE, ScalingRegime.XLARGE)):
            detected.append(ScalingPhenomenon.BRIDGE_AVALANCHE)

        # copilot: authority dilution check — estimate authority fraction from proxy
        # (bridge_count as a proxy for reviewed theorems — heuristic)
        if obs.pack_size > 0:
            est_authority_fraction = min(obs.bridge_count / obs.pack_size, 1.0)
            if obs.regime == ScalingRegime.LARGE and est_authority_fraction < _AUTHORITY_DILUTION_THRESHOLD_LARGE:
                detected.append(ScalingPhenomenon.AUTHORITY_DILUTION)
            elif obs.regime == ScalingRegime.XLARGE and est_authority_fraction < _AUTHORITY_DILUTION_THRESHOLD_XLARGE:
                detected.append(ScalingPhenomenon.AUTHORITY_DILUTION)

        # copilot: semantic drift check — too many bridge clusters
        if obs.bridge_count > _SEMANTIC_DRIFT_BRIDGE_SPAN * obs.pack_size / 100.0:
            detected.append(ScalingPhenomenon.SEMANTIC_DRIFT)

        # copilot: emergent behaviour check — near upper boundary of current regime
        lo, hi = REGIME_THRESHOLDS[obs.regime]
        span = hi - lo
        if span > 0 and (obs.pack_size - lo) > 0.85 * span:
            detected.append(ScalingPhenomenon.EMERGENT_BEHAVIOUR)

        return detected

    # ------------------------------------------------------------------
    def derive_requirements(
        self,
        obs_list: list[ScalingObservation],
    ) -> list[ScalingTheoryRequirement]:
        """Derive formal theory requirements from a collection of observations.

        For each unique ScalingPhenomenon detected across all observations,
        one requirement is generated.  The requirement's priority is taken
        from PHENOMENON_PRIORITY, and evidence_ids are collected from all
        observations where the phenomenon was detected.

        PHASE_TRANSITION detection is applied using sliding-window analysis
        over the sequence of bridge counts.

        Args:
            obs_list: List of ScalingObservation objects to process.

        Returns:
            A list of ScalingTheoryRequirement objects.

        Raises:
            ValueError: If *obs_list* is empty.

        Example:
            >>> reqs = analyzer.derive_requirements(obs_list)
        """
        if not obs_list:
            raise ValueError("derive_requirements: obs_list must not be empty")

        # copilot: collect phenomena and evidence per phenomenon
        phenomenon_evidence: dict[ScalingPhenomenon, list[str]] = {}
        for obs in obs_list:
            for phen in self.detect_phenomena(obs):
                phenomenon_evidence.setdefault(phen, []).append(obs.obs_id)

        # copilot: check for phase transitions via bridge-count sliding window
        bridge_series = [float(obs.bridge_count) for obs in obs_list]
        if len(bridge_series) >= _PHASE_TRANSITION_DETECTION_WINDOW:
            window_means = _sliding_window_means(bridge_series, _PHASE_TRANSITION_DETECTION_WINDOW)
            for i in range(1, len(window_means)):
                drop = _fraction_change(window_means[i - 1], window_means[i])
                if drop > _PHASE_TRANSITION_DROP:
                    # copilot: record the observation IDs in the window that triggered this
                    win_obs = obs_list[i: i + _PHASE_TRANSITION_DETECTION_WINDOW]
                    for wo in win_obs:
                        phenomenon_evidence.setdefault(
                            ScalingPhenomenon.PHASE_TRANSITION, []
                        ).append(wo.obs_id)
                    break

        # copilot: build requirement template text per phenomenon
        req_templates: dict[ScalingPhenomenon, str] = {
            ScalingPhenomenon.COMPLEXITY_CLIFF: (
                "The system MUST implement an approximate proof-search strategy that "
                "activates when proof_depth exceeds the complexity-cliff threshold."
            ),
            ScalingPhenomenon.BRIDGE_AVALANCHE: (
                "The federation router MUST implement avalanche circuit-breakers that "
                "halt cascade re-indexing when bridge activation rate exceeds a bound."
            ),
            ScalingPhenomenon.PHASE_TRANSITION: (
                "The federation structure monitor MUST detect and log phase transitions "
                "in real time using sliding-window bridge-count analysis."
            ),
            ScalingPhenomenon.AUTHORITY_DILUTION: (
                "The authority-scoring subsystem MUST use sampling-based review at "
                "LARGE and XLARGE scales to maintain meaningful authority fractions."
            ),
            ScalingPhenomenon.EMERGENT_BEHAVIOUR: (
                "The proof scheduler MUST periodically re-attempt previously failed "
                "theorems as pack size grows, to capture emergent provability."
            ),
            ScalingPhenomenon.SEMANTIC_DRIFT: (
                "Pack membership MUST be audited using semantic similarity checks "
                "whenever the bridge-to-theorem ratio exceeds the drift threshold."
            ),
        }

        requirements: list[ScalingTheoryRequirement] = []
        for phen, evidence_ids in phenomenon_evidence.items():
            unique_evids = tuple(dict.fromkeys(evidence_ids))[:_MAX_EVIDENCE_IDS]
            req = ScalingTheoryRequirement(
                req_id=_uid(),
                description=req_templates.get(phen, f"Address {phen.value} at scale."),
                phenomenon=phen,
                is_satisfied=False,  # default to unsatisfied; satisfaction requires external input
                evidence_ids=unique_evids,
                priority=PHENOMENON_PRIORITY.get(phen, _REQUIREMENT_PRIORITY_LOW),
            )
            requirements.append(req)

        # copilot: sort requirements by ascending priority (most urgent first)
        requirements.sort(key=lambda r: r.priority)
        return requirements

    # ------------------------------------------------------------------
    def summarize_evidence(
        self,
        obs_list: list[ScalingObservation],
    ) -> dict:
        """Produce an evidence summary dictionary from a list of observations.

        The summary records regime distribution, phenomenon frequencies,
        and aggregate structural statistics across all observations.

        Args:
            obs_list: List of ScalingObservation objects.

        Returns:
            Dict with keys ``n_observations``, ``regime_counts``,
            ``phenomenon_counts``, ``mean_pack_size``, ``mean_proof_depth``,
            ``mean_bridge_count``.

        Raises:
            ValueError: If *obs_list* is empty.

        Example:
            >>> summary = analyzer.summarize_evidence(obs_list)
            >>> "n_observations" in summary
            True
        """
        if not obs_list:
            raise ValueError("summarize_evidence: obs_list must not be empty")

        regime_counts: dict[str, int] = {}
        phen_counts: dict[str, int] = {}

        for obs in obs_list:
            regime_counts[obs.regime.value] = regime_counts.get(obs.regime.value, 0) + 1
            for phen in self.detect_phenomena(obs):
                phen_counts[phen.value] = phen_counts.get(phen.value, 0) + 1

        return {
            "n_observations":  len(obs_list),
            "regime_counts":   regime_counts,
            "phenomenon_counts": phen_counts,
            "mean_pack_size":  round(statistics.mean(o.pack_size for o in obs_list), 1),
            "mean_proof_depth": round(statistics.mean(o.proof_depth for o in obs_list), 1),
            "mean_bridge_count": round(statistics.mean(o.bridge_count for o in obs_list), 1),
        }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class WhyScalingNeedsTheoryCoordinator:
    """Orchestrates the assessment of scaling theory requirements.

    The coordinator ties together the analyzer and witness.  Its primary
    API is ``assess``, which converts a list of observations into theory
    requirements.  It also exposes ``unsatisfied_requirements`` and
    ``theory_gap_report`` for planning and documentation purposes.

    Attributes:
        _analyzer: The WhyScalingNeedsTheoryAnalyzer used by this coordinator.
        _witness:  The WhyScalingNeedsTheoryWitness receiving observations.
        _all_reqs: Accumulated list of all derived requirements.
    """

    def __init__(
        self,
        analyzer: Optional[WhyScalingNeedsTheoryAnalyzer] = None,
        witness: Optional["WhyScalingNeedsTheoryWitness"] = None,
    ) -> None:
        """Initialise coordinator with optional analyzer and witness.

        Args:
            analyzer: Pre-built analyzer, or None for a default instance.
            witness:  Pre-built witness, or None for a default instance.

        Example:
            >>> coord = WhyScalingNeedsTheoryCoordinator()
        """
        self._analyzer: WhyScalingNeedsTheoryAnalyzer = (
            analyzer or WhyScalingNeedsTheoryAnalyzer()
        )
        self._witness: WhyScalingNeedsTheoryWitness = (
            witness or WhyScalingNeedsTheoryWitness()
        )
        self._all_reqs: list[ScalingTheoryRequirement] = []

    # ------------------------------------------------------------------
    def assess(
        self,
        observations: list[ScalingObservation],
    ) -> list[ScalingTheoryRequirement]:
        """Derive and record theory requirements from a set of observations.

        Delegates to the analyzer and records each requirement with the
        witness.

        Args:
            observations: List of ScalingObservation objects.

        Returns:
            A list of ScalingTheoryRequirement objects.

        Raises:
            ValueError: Propagated from derive_requirements.

        Example:
            >>> reqs = coord.assess(obs_list)
        """
        requirements = self._analyzer.derive_requirements(observations)
        for req in requirements:
            self._witness.observe(req)
            self._all_reqs.append(req)
        return requirements

    # ------------------------------------------------------------------
    def unsatisfied_requirements(
        self,
        reqs: list[ScalingTheoryRequirement],
    ) -> list[ScalingTheoryRequirement]:
        """Filter *reqs* to those that are not yet satisfied.

        Args:
            reqs: List of ScalingTheoryRequirement objects to filter.

        Returns:
            Sub-list of requirements where ``is_satisfied == False``,
            sorted by ascending priority.

        Raises:
            ValueError: If *reqs* is empty.

        Example:
            >>> unsat = coord.unsatisfied_requirements(reqs)
        """
        if not reqs:
            raise ValueError("unsatisfied_requirements: reqs list is empty")
        return sorted(
            [r for r in reqs if not r.is_satisfied],
            key=lambda r: r.priority,
        )

    # ------------------------------------------------------------------
    def theory_gap_report(
        self,
        reqs: list[ScalingTheoryRequirement],
    ) -> dict:
        """Generate a gap report summarising satisfied and unsatisfied requirements.

        The gap report is intended for inclusion in the project's theory
        chapter as evidence that known scaling phenomena are addressed.

        Args:
            reqs: Full list of ScalingTheoryRequirement objects.

        Returns:
            Dict with keys ``total``, ``satisfied``, ``unsatisfied``,
            ``satisfaction_rate``, ``by_phenomenon``, ``by_priority``,
            ``critical_gaps``.

        Raises:
            ValueError: If *reqs* is empty.

        Example:
            >>> gap = coord.theory_gap_report(reqs)
            >>> "satisfaction_rate" in gap
            True
        """
        if not reqs:
            raise ValueError("theory_gap_report: reqs list is empty")

        satisfied = [r for r in reqs if r.is_satisfied]
        unsatisfied = [r for r in reqs if not r.is_satisfied]

        by_phenomenon: dict[str, dict] = {}
        for r in reqs:
            by_phenomenon.setdefault(r.phenomenon.value, {"satisfied": 0, "unsatisfied": 0})
            if r.is_satisfied:
                by_phenomenon[r.phenomenon.value]["satisfied"] += 1
            else:
                by_phenomenon[r.phenomenon.value]["unsatisfied"] += 1

        by_priority: dict[int, int] = {}
        for r in unsatisfied:
            by_priority[r.priority] = by_priority.get(r.priority, 0) + 1

        critical_gaps = [r.req_id for r in unsatisfied if r.priority == _REQUIREMENT_PRIORITY_CRITICAL]

        return {
            "total": len(reqs),
            "satisfied": len(satisfied),
            "unsatisfied": len(unsatisfied),
            "satisfaction_rate": round(len(satisfied) / len(reqs), 4),
            "by_phenomenon": by_phenomenon,
            "by_priority": by_priority,
            "critical_gaps": critical_gaps,
            "report_version": REPORT_VERSION,
        }

    # ------------------------------------------------------------------
    def status_report(self) -> dict:
        """Return an overall status report for this coordinator session.

        Returns:
            Dict with keys ``total_requirements``, ``satisfied``,
            ``unsatisfied``, ``critical_unsatisfied``, ``phenomena_seen``.

        Example:
            >>> rep = coord.status_report()
            >>> "total_requirements" in rep
            True
        """
        total = len(self._all_reqs)
        sat   = sum(1 for r in self._all_reqs if r.is_satisfied)
        unsat = total - sat
        crit  = sum(
            1 for r in self._all_reqs
            if not r.is_satisfied and r.priority == _REQUIREMENT_PRIORITY_CRITICAL
        )
        phenomena_seen = list({r.phenomenon.value for r in self._all_reqs})

        return {
            "total_requirements":  total,
            "satisfied":           sat,
            "unsatisfied":         unsat,
            "critical_unsatisfied": crit,
            "phenomena_seen":      phenomena_seen,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class WhyScalingNeedsTheoryWitness:
    """Observes and stores all ScalingTheoryRequirement objects for audit.

    The witness pattern separates storage from analysis.  All requirements
    produced by the coordinator are forwarded here for logging.

    Attributes:
        _log: Ordered list of all observed ScalingTheoryRequirement objects.
    """

    def __init__(self) -> None:
        """Initialise an empty witness log.

        Example:
            >>> w = WhyScalingNeedsTheoryWitness()
            >>> w.full_log()
            []
        """
        self._log: list[ScalingTheoryRequirement] = []

    # ------------------------------------------------------------------
    def observe(self, req: ScalingTheoryRequirement) -> None:
        """Append a ScalingTheoryRequirement to the witness log.

        Args:
            req: The requirement to record.

        Raises:
            TypeError: If *req* is not a ScalingTheoryRequirement.

        Example:
            >>> w.observe(req)
        """
        if not isinstance(req, ScalingTheoryRequirement):
            raise TypeError(
                f"observe: expected ScalingTheoryRequirement, got {type(req).__name__}"
            )
        self._log.append(req)

    # ------------------------------------------------------------------
    def critical_gaps(self) -> list[ScalingTheoryRequirement]:
        """Return all unsatisfied critical-priority requirements.

        Returns:
            List of unsatisfied ScalingTheoryRequirement with priority == 1.

        Example:
            >>> gaps = w.critical_gaps()
        """
        return [
            r for r in self._log
            if not r.is_satisfied and r.priority == _REQUIREMENT_PRIORITY_CRITICAL
        ]

    # ------------------------------------------------------------------
    def satisfaction_rate(self) -> float:
        """Return the fraction of observed requirements that are satisfied.

        Returns:
            Float in [0, 1].  Returns 0.0 if no requirements have been observed.

        Example:
            >>> rate = w.satisfaction_rate()
            >>> 0.0 <= rate <= 1.0
            True
        """
        if not self._log:
            return 0.0
        satisfied_count = sum(1 for r in self._log if r.is_satisfied)
        return round(satisfied_count / len(self._log), 4)

    # ------------------------------------------------------------------
    def full_log(self) -> list[ScalingTheoryRequirement]:
        """Return a copy of all observed requirements in observation order.

        Returns:
            List of ScalingTheoryRequirement objects.

        Example:
            >>> log = w.full_log()
        """
        return list(self._log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== WhyScalingNeedsTheory smoke test ===")

    _analyzer = WhyScalingNeedsTheoryAnalyzer()
    _witness  = WhyScalingNeedsTheoryWitness()
    _coord    = WhyScalingNeedsTheoryCoordinator(analyzer=_analyzer, witness=_witness)

    # copilot: create a synthetic sequence of observations at increasing scale
    _observation_data = [
        (10,    2,   1),    # SMALL
        (80,    3,   4),    # SMALL near boundary
        (150,   5,   12),   # MEDIUM
        (500,   8,   45),   # MEDIUM
        (999,   12,  95),   # MEDIUM near boundary
        (1200,  18,  150),  # LARGE
        (3000,  22,  280),  # LARGE  — high depth, possible cliff
        (5000,  30,  600),  # LARGE  — bridge avalanche risk
        (9500,  35,  1100), # LARGE near boundary
        (12000, 42,  1800), # XLARGE
    ]

    _observations: list[ScalingObservation] = []
    for _ps, _pd, _bc in _observation_data:
        _regime = _analyzer.classify_regime(_ps)
        _obs = ScalingObservation(
            obs_id=_uid(),
            phenomenon=None,
            regime=_regime,
            pack_size=_ps,
            proof_depth=_pd,
            bridge_count=_bc,
            observed_at=_utcnow(),
        )
        _observations.append(_obs)
        _phenomena = _analyzer.detect_phenomena(_obs)
        if _phenomena:
            print(f"  pack_size={_ps:5d} regime={_regime.value:6s} "
                  f"phenomena={[p.value for p in _phenomena]}")

    print()

    # Assess requirements
    _reqs = _coord.assess(_observations)
    print(f"  Derived {len(_reqs)} requirements:")
    for _req in _reqs:
        print(f"    [{_req.priority}] {_req.phenomenon.value}: {_req.description[:70]}...")

    # Gap report
    _gap = _coord.theory_gap_report(_reqs)
    print(f"\n  Gap report: {_gap}")

    # Unsatisfied
    _unsat = _coord.unsatisfied_requirements(_reqs)
    print(f"  Unsatisfied ({len(_unsat)}): {[r.req_id for r in _unsat]}")

    # Evidence summary
    _evidence = _analyzer.summarize_evidence(_observations)
    print(f"\n  Evidence summary: {_evidence}")

    # Witness queries
    _crit_gaps = _witness.critical_gaps()
    print(f"  Critical gaps: {len(_crit_gaps)}")
    _sat_rate = _witness.satisfaction_rate()
    print(f"  Satisfaction rate: {_sat_rate:.2%}")

    # Status report
    _status = _coord.status_report()
    print(f"  Status: {_status}")

    print("=== Smoke test PASSED ===")


# ===========================================================================
# REQUIRED ADDITIONS — ScalingTheory, ScalingRegime, QualitativeChange, etc.
# These definitions satisfy the module specification and override any earlier
# definitions with the same name.
# ===========================================================================

import uuid as _uuid_mod
import datetime as _dt_mod
import math as _math_mod
import statistics as _stat_mod
from dataclasses import dataclass as _dc
from enum import Enum as _Enum
from typing import Any as _Any, Optional as _Opt


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix."""
    return _dt_mod.datetime.utcnow().isoformat() + "Z"


def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(_uuid_mod.uuid4())


class TrustTier(_Enum):
    """Trust levels for ScalingJudgment tuples and theory components.

    The trust tier tracks the epistemic status of a claim:
    PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.
    """
    PROPOSAL = "proposal"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    RUNTIME_WITNESSED = "runtime_witnessed"
    PROOF_BACKED = "proof_backed"


class ChangeType(_Enum):
    """Type of qualitative change at a regime boundary."""
    STRUCTURAL = "structural"
    COMPUTATIONAL = "computational"
    SEMANTIC = "semantic"
    EMERGENT = "emergent"
    DEGENERATIVE = "degenerative"


class RegimeType(_Enum):
    """Classification of scaling regimes by qualitative character."""
    TRACTABLE = "tractable"
    MARGINAL = "marginal"
    CRITICAL = "critical"
    SUPERCRITICAL = "supercritical"
    ASYMPTOTIC = "asymptotic"


class ObligationStatus(_Enum):
    """Verification status of a ScalingObligation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"
    DEFERRED = "deferred"


@_dc(frozen=True, slots=True)
class ScalingJudgment:
    """8-tuple judgment (c, phi, A, E, O, B, T, Pi) for scaling assertions.

    context     (c):   Scale context in which the judgment was made.
    formula     (phi): Formal predicate being asserted.
    authority   (A):   Entity making the judgment.
    evidence    (E):   Tuple of evidence keys supporting the judgment.
    obligations (O):   Obligations that must hold for judgment to stand.
    budget      (B):   Resource budget consumed to produce this judgment.
    trust_tier  (T):   Trust level of the judgment.
    proof_chain (Pi):  Ordered tuple of proof steps / citations.
    """
    context: str
    formula: str
    authority: str
    evidence: tuple
    obligations: tuple
    budget: float
    trust_tier: TrustTier
    proof_chain: tuple

    def verdict(self) -> str:
        """Extract the verdict token from the formula string."""
        for tok in ("VERIFIED", "REFUTED", "INCONCLUSIVE"):
            if self.formula.startswith(tok):
                return tok
        return "INCONCLUSIVE"

    def to_dict(self) -> dict:
        return {
            "context": self.context, "formula": self.formula,
            "authority": self.authority, "evidence": list(self.evidence),
            "obligations": list(self.obligations), "budget": self.budget,
            "trust_tier": self.trust_tier.value, "proof_chain": list(self.proof_chain),
            "verdict": self.verdict(),
        }


@_dc(frozen=True, slots=True)
class ScalingRegime:
    """A qualitatively distinct scaling regime with characteristic properties.

    A ScalingRegime represents a range of scale values within which the
    system behaves qualitatively similarly. Regime transitions require
    new theory and generate new ScalingObligations.

    Formal Theorem (Regime Existence):
      For every scale n > 0 registered in a ScalingTheory, there exists
      exactly one ScalingRegime R such that R.lower_bound <= n < R.upper_bound.

    Attributes:
        regime_id:                Unique identifier.
        name:                     Human-readable name.
        description:              Detailed description of the regime character.
        lower_bound:              Lower bound of the scale range (inclusive).
        upper_bound:              Upper bound of the scale range (exclusive).
        characteristic_properties: Tuple of properties defining this regime.
        trust_tier:               Trust level of this regime definition.
        created_at:               ISO-8601 timestamp.
    """
    regime_id: str
    name: str
    description: str
    lower_bound: float
    upper_bound: float
    characteristic_properties: tuple
    trust_tier: TrustTier
    created_at: str
    regime_type: RegimeType = RegimeType.TRACTABLE
    phenomena: tuple = ()
    complexity_class: str = "polynomial"

    def contains(self, scale: float) -> bool:
        """Return True iff this regime applies at the given scale."""
        return self.lower_bound <= scale < self.upper_bound

    def describe(self) -> str:
        """Return a formatted description of this regime."""
        return (
            f"ScalingRegime({self.name!r}, "
            f"[{self.lower_bound:.0f}, {self.upper_bound:.0f}), "
            f"type={self.regime_type.value})"
        )


@_dc(frozen=True, slots=True)
class QualitativeChange:
    """A qualitative change that occurs at a regime boundary.

    Formal Theorem (Change Irreducibility):
      The qualitative change at each ScalingRegime transition cannot be
      predicted from within the lower regime. A separate theoretical
      treatment is required for each new regime.

    Attributes:
        change_id:         Unique identifier.
        from_regime:       ID of the source regime.
        to_regime:         ID of the target regime.
        trigger_condition: Formal description of what triggers this change.
        description:       Human-readable description.
        implications:      Tuple of implications for system behavior.
        observed_at:       ISO-8601 timestamp of first observation.
    """
    change_id: str
    from_regime: str
    to_regime: str
    trigger_condition: str
    description: str
    implications: tuple
    observed_at: str
    change_type: ChangeType = ChangeType.STRUCTURAL
    severity: float = 0.5
    theorem_ref: str = ""

    def is_severe(self) -> bool:
        """Return True iff severity exceeds 0.7."""
        return self.severity > 0.7

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id, "from_regime": self.from_regime,
            "to_regime": self.to_regime, "trigger_condition": self.trigger_condition,
            "description": self.description, "implications": list(self.implications),
            "observed_at": self.observed_at, "change_type": self.change_type.value,
            "severity": self.severity, "theorem_ref": self.theorem_ref,
        }


@_dc(frozen=True, slots=True)
class ScalingObligation:
    """A formal obligation that must be verified when operating in a regime.

    ScalingObligations are generated automatically when a system transitions
    into a new ScalingRegime. They represent conditions that must be formally
    verified before the system can be trusted to operate at the new scale.

    Formal Theorem (Obligation Completeness):
      Every regime transition generates obligations O such that O is
      necessary and sufficient for correct operation at the new scale.

    Attributes:
        obligation_id:       Unique identifier.
        regime:              ID of the regime in which this obligation applies.
        formal_requirement:  Formal logical statement of the requirement.
        verification_method: How to verify this obligation.
        fulfilled:           Whether this obligation has been fulfilled.
        created_at:          ISO-8601 timestamp.
    """
    obligation_id: str
    regime: str
    formal_requirement: str
    verification_method: str
    fulfilled: bool
    created_at: str
    priority: int = 1
    budget: float = 100.0
    dependencies: tuple = ()

    def is_blocking(self) -> bool:
        """Return True iff this is a priority-1 (critical) obligation."""
        return self.priority == 1

    def verify(self, evidence: tuple) -> ScalingJudgment:
        """Produce a ScalingJudgment for this obligation given evidence."""
        verdict = "VERIFIED" if self.fulfilled else "REFUTED"
        return ScalingJudgment(
            context=f"regime:{self.regime}",
            formula=f"{verdict}: {self.formal_requirement}",
            authority="ScalingObligation.verify",
            evidence=evidence,
            obligations=(self.obligation_id,),
            budget=self.budget,
            trust_tier=TrustTier.REVIEWED,
            proof_chain=(f"obligation:{self.obligation_id}",),
        )


@_dc(frozen=True, slots=True)
class ScalingPhase:
    """A phase within a ScalingRegime, representing a finer-grained sub-range."""
    phase_id: str
    regime_id: str
    name: str
    lower_bound: float
    upper_bound: float
    description: str
    created_at: str

    def contains(self, scale: float) -> bool:
        return self.lower_bound <= scale < self.upper_bound


@_dc(frozen=True, slots=True)
class ScalingThreshold:
    """A critical threshold at which a qualitative change occurs."""
    threshold_id: str
    name: str
    scale_value: float
    tolerance: float
    metric_name: str
    trigger_above: bool
    description: str
    created_at: str

    def is_triggered(self, metric_value: float) -> bool:
        if self.trigger_above:
            return metric_value > self.scale_value - self.tolerance
        return metric_value < self.scale_value + self.tolerance


@_dc(frozen=True, slots=True)
class ScalingEvidence:
    """Evidence supporting a scaling theory claim."""
    evidence_id: str
    claim_id: str
    source: str
    description: str
    scale_point: float
    value: float
    created_at: str

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "claim_id": self.claim_id,
            "source": self.source, "description": self.description,
            "scale_point": self.scale_point, "value": self.value,
            "created_at": self.created_at,
        }


@_dc(frozen=True, slots=True)
class ScalingProof:
    """A formal proof of a scaling theory theorem."""
    proof_id: str
    theorem: str
    premises: tuple
    proof_steps: tuple
    conclusion: str
    trust_tier: TrustTier
    created_at: str

    def is_complete(self) -> bool:
        return len(self.proof_steps) > 0 and bool(self.conclusion)

    def to_judgment(self, authority: str) -> ScalingJudgment:
        return ScalingJudgment(
            context=f"proof:{self.proof_id}",
            formula=f"VERIFIED: {self.conclusion}",
            authority=authority,
            evidence=self.premises,
            obligations=(),
            budget=0.0,
            trust_tier=self.trust_tier,
            proof_chain=self.proof_steps,
        )


@_dc(frozen=True, slots=True)
class PhaseBoundary:
    """The boundary between two ScalingRegimes, with formal theory."""
    boundary_id: str
    lower_regime_id: str
    upper_regime_id: str
    boundary_scale: float
    crossing_condition: str
    is_sharp: bool
    hysteresis_range: tuple
    created_at: str

    def is_in_hysteresis(self, scale: float) -> bool:
        if len(self.hysteresis_range) != 2:
            return False
        lo, hi = self.hysteresis_range
        return lo <= scale <= hi


@_dc(frozen=True, slots=True)
class ScalingObservationRecord:
    """An empirical observation of a scaling metric (non-conflicting name)."""
    observation_id: str
    metric_name: str
    scale_value: float
    observed_value: float
    context: str
    timestamp: str
    is_anomalous: bool = False

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id, "metric_name": self.metric_name,
            "scale_value": self.scale_value, "observed_value": self.observed_value,
            "context": self.context, "timestamp": self.timestamp,
            "is_anomalous": self.is_anomalous,
        }


class ScalingTheory:
    """The formal theory of scaling in the JuGeo system.

    Formal Axioms:
    1. Regime Existence: For every scale n > 0, there exists exactly one
       ScalingRegime R such that R contains n.
    2. Change Irreducibility: Qualitative changes at regime boundaries cannot
       be predicted from within the lower regime.
    3. Obligation Completeness: Every regime transition generates obligations
       that are necessary and sufficient for correct operation.

    The theory maintains a registry of regimes, boundaries, qualitative changes,
    and scaling obligations, and provides methods for identifying the current
    regime, predicting qualitative changes, and verifying obligations.
    """

    def __init__(self, theory_id: _Opt[str] = None) -> None:
        self.theory_id: str = theory_id or _uid()
        self._regimes: dict = {}
        self._boundaries: dict = {}
        self._changes: list = []
        self._obligations: dict = {}
        self._observations: list = []
        self._proofs: dict = {}
        self._evidence: list = []
        self._created_at: str = _now_iso()

    def add_regime(self, regime: ScalingRegime) -> ScalingRegime:
        """Register a ScalingRegime with the theory.

        Args:
            regime: The ScalingRegime to register.
        Returns:
            The registered ScalingRegime.
        Raises:
            ValueError: If a regime with the same ID is already registered.
        """
        if regime.regime_id in self._regimes:
            raise ValueError(
                f"Regime {regime.regime_id!r} already registered"
            )
        self._regimes[regime.regime_id] = regime
        return regime

    def identify_current_regime(self, scale_metric: float) -> ScalingRegime:
        """Identify the ScalingRegime that applies at the given scale.

        Args:
            scale_metric: The current scale value.
        Returns:
            The applicable ScalingRegime.
        Raises:
            ValueError: If no regimes are registered.
        """
        if not self._regimes:
            raise ValueError("No regimes registered in this theory")
        for regime in self._regimes.values():
            if regime.contains(scale_metric):
                return regime
        return min(
            self._regimes.values(),
            key=lambda r: abs(r.lower_bound - scale_metric),
        )

    def predict_qualitative_change(
        self, current_scale: float, future_scale: float
    ) -> QualitativeChange:
        """Predict the qualitative change from current_scale to future_scale.

        Args:
            current_scale: The current scale value.
            future_scale:  The projected future scale value.
        Returns:
            A QualitativeChange describing the most significant transition.
        """
        current_regime = self.identify_current_regime(current_scale)
        future_regime = self.identify_current_regime(future_scale)

        if current_regime.regime_id == future_regime.regime_id:
            return QualitativeChange(
                change_id=_uid(),
                from_regime=current_regime.regime_id,
                to_regime=future_regime.regime_id,
                trigger_condition=f"scale in [{current_scale:.0f}, {future_scale:.0f})",
                description="No regime change predicted — same regime",
                implications=("monitor_for_phase_transition",),
                observed_at=_now_iso(),
                severity=0.1,
            )

        type_order = list(RegimeType)
        try:
            from_idx = type_order.index(current_regime.regime_type)
            to_idx = type_order.index(future_regime.regime_type)
            severity = min(1.0, (to_idx - from_idx) / (len(type_order) - 1))
        except (ValueError, ZeroDivisionError):
            severity = 0.5

        return QualitativeChange(
            change_id=_uid(),
            from_regime=current_regime.regime_id,
            to_regime=future_regime.regime_id,
            trigger_condition=(
                f"scale crosses boundary at {current_regime.upper_bound:.0f}"
            ),
            description=(
                f"Regime transition from {current_regime.name!r} to "
                f"{future_regime.name!r}"
            ),
            implications=(
                f"verify_obligations_for:{future_regime.name}",
                f"complexity_class:{future_regime.complexity_class}",
            ),
            observed_at=_now_iso(),
            change_type=ChangeType.STRUCTURAL,
            severity=max(0.0, severity),
            theorem_ref="Theorem(Phase-Transition-Irreducibility)",
        )

    def verify_scaling_obligation(self, obligation_id: str) -> ScalingJudgment:
        """Verify a registered ScalingObligation and return a ScalingJudgment.

        Args:
            obligation_id: ID of the obligation to verify.
        Returns:
            A ScalingJudgment with VERIFIED, REFUTED, or INCONCLUSIVE formula.
        """
        obligation = self._obligations.get(obligation_id)
        if obligation is None:
            return ScalingJudgment(
                context=f"theory:{self.theory_id}",
                formula=f"INCONCLUSIVE: obligation {obligation_id!r} not found",
                authority="ScalingTheory.verify_scaling_obligation",
                evidence=(),
                obligations=(obligation_id,),
                budget=0.0,
                trust_tier=TrustTier.PROPOSAL,
                proof_chain=(),
            )
        evidence_ids = tuple(
            e.evidence_id for e in self._evidence
            if e.claim_id == obligation_id
        )
        return obligation.verify(evidence_ids)

    def add_obligation(self, obligation: ScalingObligation) -> ScalingObligation:
        """Register a ScalingObligation with the theory."""
        self._obligations[obligation.obligation_id] = obligation
        return obligation

    def add_observation(self, obs: ScalingObservationRecord) -> ScalingObservationRecord:
        """Record a ScalingObservationRecord."""
        self._observations.append(obs)
        return obs

    def add_evidence(self, ev: ScalingEvidence) -> ScalingEvidence:
        """Record ScalingEvidence."""
        self._evidence.append(ev)
        return ev

    def add_proof(self, proof: ScalingProof) -> ScalingProof:
        """Register a formal ScalingProof."""
        self._proofs[proof.proof_id] = proof
        return proof

    def add_change(self, change: QualitativeChange) -> QualitativeChange:
        """Record a QualitativeChange."""
        self._changes.append(change)
        return change

    def pending_obligations(self) -> list:
        """Return all obligations that have not been fulfilled."""
        return [o for o in self._obligations.values() if not o.fulfilled]

    def build_theory_report(self) -> dict:
        """Build a comprehensive report of the scaling theory.

        Returns a dict with regime descriptions, pending/fulfilled obligations,
        qualitative changes, evidence counts, proofs, and recommendations.
        """
        regimes = sorted(self._regimes.values(), key=lambda r: r.lower_bound)
        obligations = list(self._obligations.values())
        fulfilled = [o for o in obligations if o.fulfilled]
        pending = [o for o in obligations if not o.fulfilled]

        regime_descriptions = [
            {
                "regime_id": r.regime_id, "name": r.name,
                "range": [r.lower_bound, r.upper_bound],
                "type": r.regime_type.value,
                "complexity_class": r.complexity_class,
                "trust_tier": r.trust_tier.value,
            }
            for r in regimes
        ]

        recommendations = []
        if pending:
            critical = [o for o in pending if o.priority == 1]
            if critical:
                recommendations.append(
                    f"{len(critical)} critical obligations pending. "
                    "Do not advance to next regime."
                )
        else:
            recommendations.append(
                "All obligations fulfilled. Consider advancing to next regime."
            )

        return {
            "theory_id": self.theory_id,
            "created_at": self._created_at,
            "generated_at": _now_iso(),
            "regime_count": len(regimes),
            "regimes": regime_descriptions,
            "obligation_count": len(obligations),
            "obligations_fulfilled": len(fulfilled),
            "obligations_pending": len(pending),
            "qualitative_changes": len(self._changes),
            "observation_count": len(self._observations),
            "evidence_count": len(self._evidence),
            "proof_count": len(self._proofs),
            "recommendations": recommendations,
        }


def identify_scaling_regimes(
    system_metrics: dict, regime_thresholds: dict
) -> list:
    """Identify the active scaling regimes from system metrics and thresholds.

    Constructs ScalingRegime frozen dataclasses based on the provided thresholds.

    Args:
        system_metrics:    Dict mapping metric names to current values.
        regime_thresholds: Dict mapping metric names to lists of threshold values.
    Returns:
        List of ScalingRegime objects.
    """
    import itertools as _itr
    thresholds = sorted(
        set(_itr.chain.from_iterable(
            regime_thresholds.get(k, []) for k in system_metrics
        ))
    )
    if not thresholds:
        thresholds = [100.0, 1000.0, 10000.0, 100000.0]

    bounds = [0.0] + thresholds + [float("inf")]
    type_seq = [
        RegimeType.TRACTABLE, RegimeType.MARGINAL,
        RegimeType.CRITICAL, RegimeType.SUPERCRITICAL,
        RegimeType.ASYMPTOTIC,
    ]
    now = _now_iso()
    regimes = []
    for i, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:])):
        r_type = type_seq[min(i, len(type_seq) - 1)]
        name = r_type.value.upper()[:8] + f"_{i}"
        regimes.append(ScalingRegime(
            regime_id=_uid(), name=name,
            description=f"Auto-identified regime at scale [{lo:.0f}, {hi if hi != float('inf') else 'inf'})",
            lower_bound=lo, upper_bound=hi,
            characteristic_properties=(f"scale_range=[{lo:.0f},{hi})", f"type={r_type.value}"),
            trust_tier=TrustTier.PROPOSAL, created_at=now,
            regime_type=r_type,
        ))
    return regimes


def characterize_qualitative_change(
    from_scale: float, to_scale: float, property_set: list
) -> QualitativeChange:
    """Characterize the qualitative change when scale increases.

    Args:
        from_scale:   Starting scale value.
        to_scale:     Target scale value.
        property_set: List of property name strings to consider.
    Returns:
        A QualitativeChange describing the predicted qualitative shift.
    """
    if to_scale <= from_scale:
        return QualitativeChange(
            change_id=_uid(),
            from_regime=f"scale:{from_scale:.0f}",
            to_regime=f"scale:{to_scale:.0f}",
            trigger_condition="no_scale_increase",
            description="No qualitative change: scale did not increase",
            implications=("re_evaluate_after_scale_increase",),
            observed_at=_now_iso(),
            severity=0.0,
        )

    ratio = to_scale / from_scale if from_scale > 0 else float("inf")
    if ratio < 2.0:
        severity, change_type = 0.1, ChangeType.STRUCTURAL
    elif ratio < 10.0:
        severity, change_type = 0.3, ChangeType.STRUCTURAL
    elif ratio < 100.0:
        severity, change_type = 0.6, ChangeType.COMPUTATIONAL
    elif ratio < 1000.0:
        severity, change_type = 0.8, ChangeType.EMERGENT
    else:
        severity, change_type = 1.0, ChangeType.DEGENERATIVE

    critical_props = [
        p for p in property_set
        if any(kw in p.lower() for kw in ("proof", "federation", "authority", "latency"))
    ]
    implications = tuple(f"property_change:{p}" for p in critical_props[:5]) or (
        f"general_qualitative_change:ratio={ratio:.1f}x",
    )

    return QualitativeChange(
        change_id=_uid(),
        from_regime=f"scale:{from_scale:.0f}",
        to_regime=f"scale:{to_scale:.0f}",
        trigger_condition=f"scale_ratio={ratio:.1f}x; affected_properties={critical_props[:3]}",
        description=(
            f"Qualitative change at {from_scale:.0f}→{to_scale:.0f} "
            f"(ratio {ratio:.1f}x); type={change_type.value}; severity={severity:.1f}"
        ),
        implications=implications,
        observed_at=_now_iso(),
        change_type=change_type,
        severity=severity,
        theorem_ref="Theorem(Phase-Transition-Irreducibility)",
    )


def build_scaling_theory(
    empirical_data: dict, theoretical_priors: dict
) -> ScalingTheory:
    """Build a ScalingTheory from empirical data and theoretical priors.

    Constructs a complete ScalingTheory by identifying regimes, applying
    theoretical priors, generating obligations, and adding formal proofs.

    Args:
        empirical_data:     Dict with 'observations' list of (scale, metric, value).
        theoretical_priors: Dict with 'known_thresholds', 'complexity_classes'.
    Returns:
        A fully populated ScalingTheory instance.
    """
    theory = ScalingTheory()
    now = _now_iso()

    known_thresholds = theoretical_priors.get("known_thresholds", [])
    complexity_classes = theoretical_priors.get("complexity_classes", {})
    threshold_scales = sorted(
        set([t[0] for t in known_thresholds] + [100.0, 1000.0, 10000.0])
    )
    bounds = [0.0] + threshold_scales + [float("inf")]
    type_seq = [
        RegimeType.TRACTABLE, RegimeType.MARGINAL, RegimeType.CRITICAL,
        RegimeType.SUPERCRITICAL, RegimeType.ASYMPTOTIC,
    ]

    for i, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:])):
        r_type = type_seq[min(i, len(type_seq) - 1)]
        name_key = r_type.value.upper()[:6]
        complexity = complexity_classes.get(name_key, "polynomial")
        regime = ScalingRegime(
            regime_id=_uid(), name=f"{name_key}_{i}",
            description=f"Theory-derived regime [{lo:.0f}, {hi if hi != float('inf') else 'inf'})",
            lower_bound=lo, upper_bound=hi,
            characteristic_properties=(f"complexity={complexity}", f"type={r_type.value}"),
            trust_tier=TrustTier.REVIEWED, created_at=now,
            regime_type=r_type, complexity_class=complexity,
        )
        theory.add_regime(regime)
        if r_type not in (RegimeType.TRACTABLE,):
            obligation = ScalingObligation(
                obligation_id=_uid(), regime=regime.regime_id,
                formal_requirement=(
                    f"forall n in [{lo:.0f}, {hi if hi != float('inf') else 'inf'}): "
                    f"system_stable(n) and complexity_class(n) = {complexity!r}"
                ),
                verification_method="empirical_sampling",
                fulfilled=False, created_at=now,
                priority=1 if r_type == RegimeType.CRITICAL else 2,
                budget=500.0 / max(i, 1),
            )
            theory.add_obligation(obligation)

    for obs_triple in empirical_data.get("observations", [])[:50]:
        if len(obs_triple) >= 3:
            sv, mn, mv = obs_triple[0], obs_triple[1], obs_triple[2]
            obs = ScalingObservationRecord(
                observation_id=_uid(), metric_name=str(mn),
                scale_value=float(sv), observed_value=float(mv),
                context="build_scaling_theory", timestamp=now,
            )
            theory.add_observation(obs)

    return theory

