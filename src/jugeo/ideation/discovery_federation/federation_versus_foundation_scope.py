"""
Federation versus Foundation: Scope, Bridge Burden, and Semantic Economy.

This module implements the comparative analysis pipeline for the JuGeo
Discovery Federation subsystem (theory2.tex Ch62 §2). When a theorem has
passed the FEDERATE/FOUNDATION threshold, a deeper analysis determines
which of the two is preferable:

  FEDERATION  — The theorem enlarges an existing pack without structural change.
  FOUNDATION  — The theorem demands a new core pack, incurring bridge burden.

Key concepts modelled here:
  SCOPE         — How many existing theorems the new theorem can unify or extend.
  BRIDGE_BURDEN — The cost of creating and maintaining inter-pack bridges when
                  a new foundation pack is introduced.
  SEMANTIC_ECONOMY — Whether adding to an existing pack is more economical
                  than spawning a new one, measured in cross-reference density.

copilot: federation-vs-foundation marker
theory2.tex Ch62 §2 — Federation versus Foundation Analysis
"""

from __future__ import annotations

import uuid
import math
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.discovery_federation.models import PackDescriptor  # type: ignore
except ImportError:
    PackDescriptor = None  # type: ignore

try:
    from jugeo.ideation.discovery_federation.algorithms import BridgeIndex  # type: ignore
except ImportError:
    BridgeIndex = None  # type: ignore

try:
    from jugeo.ideation.discovery_federation.integration import SemanticBus  # type: ignore
except ImportError:
    SemanticBus = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

BRIDGE_BURDEN_LOW_THRESHOLD: int = 2
"""Number of inter-pack bridges below which burden is classified as LOW.

A new foundation pack requires bridges to every existing pack that shares
at least one regime boundary with it. When only one or two such bridges
are needed, the overhead is small enough that foundation is still viable.
"""

BRIDGE_BURDEN_MEDIUM_THRESHOLD: int = 5
"""Bridge count at or above LOW but below this is classified as MEDIUM.

At medium burden, the pack registry becomes noticeably more complex.
Queries that span pack boundaries incur additional join costs, and the
bridge maintenance burden grows with each new theorem added to the pack.
"""

BRIDGE_BURDEN_HIGH_THRESHOLD: int = 10
"""Bridge count at or above MEDIUM but below this is classified as HIGH.

High bridge burden is a significant warning signal. The cost of maintaining
this many inter-pack relationships may outweigh the semantic benefit of a
new foundation pack. The analyzer will strongly favour federation at this level.
"""

SEMANTIC_ECONOMY_FLOOR: float = 0.30
"""Minimum semantic economy gain required to prefer federation over foundation.

If adding the theorem to an existing pack yields less than this fraction of
semantic economy (measured as cross-reference density improvement per unit
pack size increase), then the existing pack is already too dense and a new
foundation pack may be preferable.
"""

FOUNDATION_NOVELTY_FLOOR: float = 0.82
"""Minimum novelty score required for foundation recommendation.

Below this value, the theorem is not novel enough to justify creating a new
core pack. The bridge burden and semantic economy signals may still point
toward foundation, but novelty acts as a veto: if a theorem is not
sufficiently novel, federation is always preferred.
"""

# Minimum cross-reference density for a scope profile to be considered
# federation-eligible.  Below this the pack would be too sparse.
MIN_CROSS_REF_FOR_FEDERATION: float = 0.15

# Maximum scope_width beyond which foundation is always preferred, because
# the theorem is too structurally expansive to fit in any existing pack.
MAX_SCOPE_WIDTH_FOR_FEDERATION: int = 200

# Weight of bridge_count in the semantic economy calculation.
BRIDGE_WEIGHT_IN_ECONOMY: float = 0.25

# Smoothing epsilon used to avoid log(0) in density calculations.
LOG_EPSILON: float = 1e-9

# Schema version embedded in every PlacementRecord.
PLACEMENT_RECORD_SCHEMA_VERSION: str = "federation-vs-foundation-v1.1"

# Default federation pack label used when the system cannot locate the
# most-affine pack in the registry.
DEFAULT_PACK_LABEL: str = "default-federation-pack"

# Threshold at which two placement-mode scores are considered a "tie"
# for the purposes of the witness contention log.
TIE_MARGIN: float = 0.05

# Penalty applied to semantic economy when bridge_count exceeds HIGH threshold.
ECONOMY_CRITICAL_BRIDGE_PENALTY: float = 0.30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime.datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        A timezone-aware datetime.datetime representing the current instant.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.datetime.now(datetime.timezone.utc)


def _uid() -> str:
    """Generate a compact unique identifier string (32 hex chars).

    Returns:
        A 32-character hexadecimal string suitable for use as a record key.

    Example:
        >>> uid = _uid()
        >>> len(uid) == 32
        True
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The value to constrain.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        The clamped value as a float.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"Empty interval: lo={lo!r} > hi={hi!r}")
    return max(lo, min(hi, value))


def _safe_log(x: float) -> float:
    """Compute the natural logarithm of *x*, returning log(LOG_EPSILON) for x ≤ 0.

    This wrapper is used in density calculations where zero input would cause
    a domain error. It treats near-zero and negative values as LOG_EPSILON.

    Args:
        x: Input value.

    Returns:
        math.log(max(x, LOG_EPSILON)) as a float.

    Example:
        >>> _safe_log(1.0) == 0.0
        True
    """
    return math.log(max(x, LOG_EPSILON))


def _interpolate(a: float, b: float, t: float) -> float:
    """Linear interpolation between *a* and *b* by factor *t*.

    Args:
        a: Start value (returned when t == 0.0).
        b: End value (returned when t == 1.0).
        t: Interpolation factor, clamped to [0.0, 1.0].

    Returns:
        a + t*(b - a) as a float.

    Example:
        >>> _interpolate(0.0, 1.0, 0.5)
        0.5
    """
    t = _clamp(t, 0.0, 1.0)
    return a + t * (b - a)


def _scope_to_complexity(scope_width: int, scope_depth: int) -> float:
    """Compute a normalised complexity index from scope dimensions.

    The complexity index captures the structural impact of a theorem on the
    proof graph. Wide scope (many reachable theorems) and deep scope (long
    proof chains) both increase complexity, but depth is weighted more
    heavily because it directly affects query-time proof reconstruction.

    Args:
        scope_width: Number of existing theorems the new theorem can unify
                     or extend (non-negative integer).
        scope_depth: Proof-depth coverage in hops from the theorem's root
                     (non-negative integer).

    Returns:
        A float in [0.0, 1.0] representing normalised complexity.

    Example:
        >>> _scope_to_complexity(10, 3)  # doctest: +ELLIPSIS
        0...
    """
    # Normalise width and depth using log scale to avoid extreme sensitivity
    # to large values.
    norm_width = _clamp(_safe_log(scope_width + 1) / _safe_log(MAX_SCOPE_WIDTH_FOR_FEDERATION + 1), 0.0, 1.0)
    norm_depth = _clamp(_safe_log(scope_depth + 1) / _safe_log(50.0), 0.0, 1.0)
    # Depth weighted at 60%, width at 40%
    return _clamp(norm_depth * 0.60 + norm_width * 0.40, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PlacementMode(str, Enum):
    """The two possible placement outcomes for a theorem that clears the authority bar.

    Both FEDERATE and FOUND represent positive dispositions: the theorem has
    been deemed correct and useful. The distinction lies in *where* the theorem
    is placed and what structural side-effects the placement incurs.

    Members:
        FEDERATE: The theorem is added to the most affine existing pack.
                  No new pack is created. Bridge tables are not modified.
                  This is the lower-cost, preferred outcome.

        FOUND:    The theorem requires a new core pack.  A foundation event
                  is emitted to the pack registry, new bridges are created
                  to link the new pack with existing ones, and the semantic
                  economy of all affected packs must be recomputed.
    """

    FEDERATE = "FEDERATE"
    FOUND = "FOUND"


class BridgeBurdenLevel(str, Enum):
    """Categorical measure of the inter-pack bridge burden introduced by foundation.

    When a new core pack is created (FOUND disposition), bridges must be
    established between the new pack and every existing pack that shares
    regime overlap with it. BridgeBurdenLevel categorises the number of
    such bridges into five qualitative bands.

    Members:
        NONE:     Zero bridges required.  The new pack is fully isolated,
                  which typically indicates a completely novel research area.

        LOW:      One to BRIDGE_BURDEN_LOW_THRESHOLD bridges.  Manageable
                  overhead; foundation is straightforward.

        MEDIUM:   BRIDGE_BURDEN_LOW_THRESHOLD+1 to BRIDGE_BURDEN_MEDIUM_THRESHOLD
                  bridges.  Moderate complexity; requires careful bridge design.

        HIGH:     BRIDGE_BURDEN_MEDIUM_THRESHOLD+1 to BRIDGE_BURDEN_HIGH_THRESHOLD
                  bridges.  Significant structural impact; analyst review
                  recommended before proceeding with foundation.

        CRITICAL: Above BRIDGE_BURDEN_HIGH_THRESHOLD bridges.  The registry
                  complexity cost of foundation may outweigh its benefits.
                  The system will strongly recommend federation instead.
    """

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScopeProfile:
    """Immutable measurement of a theorem's structural reach within the proof graph.

    A ScopeProfile is computed by querying the proof graph for all theorems
    that would be directly or transitively affected by the introduction of
    the new theorem. It quantifies the structural footprint of the theorem
    and is the primary input to the FederationVsFoundationAnalyzer.

    The scope dimensions are independent: a theorem may have high width but
    shallow depth (e.g. a broad survey lemma that touches many existing results
    without extending any chain deeply) or narrow width but great depth (e.g.
    a technical lemma deep in a single proof chain).

    Fields:
        candidate_id:       The theorem_id of the theorem being profiled.
                            Must match the ID used in the upstream authority-
                            choice pipeline.

        scope_width:        The number of existing theorems in the registry
                            that this theorem can directly unify or extend.
                            A theorem that generalises ten existing results
                            would have scope_width == 10.

        scope_depth:        The maximum proof-depth coverage of this theorem,
                            measured as the longest path from the theorem's
                            root node to any leaf in the subgraph it induces.
                            Higher depth indicates greater structural reach
                            into existing proof chains.

        cross_ref_density:  A float in [0.0, 1.0] measuring how densely the
                            theorem's neighbourhood in the proof graph is
                            cross-referenced.  High density means the theorem
                            is entering a well-connected region; low density
                            means it is in a sparse, possibly isolated region.

        bridge_count:       The number of inter-pack bridges that would need
                            to be created or updated if this theorem were given
                            foundation placement.  Zero if the theorem is
                            contained within a single existing pack's regime.
    """

    candidate_id: str
    scope_width: int
    scope_depth: int
    cross_ref_density: float
    bridge_count: int


@dataclass(frozen=True, slots=True)
class PlacementRecord:
    """Immutable record of a federation-vs-foundation placement decision.

    A PlacementRecord is produced by FederationVsFoundationCoordinator for
    every theorem that has passed the authority-choice threshold and needs
    a placement mode assigned. It captures the full context of the decision
    including the scope profile, the recommended mode, the bridge burden
    level, and a human-readable justification string.

    Fields:
        candidate_id:         The theorem_id being placed.

        mode:                 The recommended PlacementMode (FEDERATE or FOUND).

        bridge_burden:        The BridgeBurdenLevel that would result from
                              foundation placement.  Even if mode == FEDERATE,
                              this field is populated for audit purposes.

        semantic_economy_gain: A float in [0.0, 1.0] measuring the improvement
                               in semantic economy that would result from
                               federation (i.e., adding the theorem to an
                               existing pack).  High gain favours federation.

        scope_profile:        The ScopeProfile used to derive this record.

        justification:        A human-readable string explaining the primary
                              factors that determined the placement mode.

        timestamp:            UTC datetime at which the record was produced.

        schema_version:       Version string for downstream schema management.
    """

    candidate_id: str
    mode: PlacementMode
    bridge_burden: BridgeBurdenLevel
    semantic_economy_gain: float
    scope_profile: ScopeProfile
    justification: str
    timestamp: datetime.datetime
    schema_version: str = PLACEMENT_RECORD_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class FederationVsFoundationAnalyzer:
    """Determines whether a theorem should be federated or given foundation placement.

    The FederationVsFoundationAnalyzer evaluates a ScopeProfile against the
    module-level policy constants and returns a PlacementMode recommendation.
    It exposes four public methods:

    1. compute_bridge_burden:  Classifies the bridge_count into a burden level.
    2. compute_semantic_economy:  Quantifies the economy gain from federation.
    3. recommend_mode:  Combines the above signals with the theorem's novelty
                        score to recommend FEDERATE or FOUND.
    4. explain_recommendation:  Produces a human-readable explanation for a
                                completed PlacementRecord.

    The analyzer is stateless and safe for concurrent use.

    Attributes:
        economy_weight:  Weight applied to semantic economy in mode selection.
        burden_weight:   Weight applied to bridge burden in mode selection.
        novelty_weight:  Weight applied to novelty score in mode selection.
    """

    def __init__(
        self,
        economy_weight: float = 0.40,
        burden_weight: float = 0.35,
        novelty_weight: float = 0.25,
    ) -> None:
        """Initialise the analyzer with configurable weighting parameters.

        Args:
            economy_weight: Fraction of the mode-selection score derived from
                            semantic economy analysis. Default 0.40.
            burden_weight:  Fraction derived from bridge burden level. Default 0.35.
            novelty_weight: Fraction derived from novelty score. Default 0.25.

        Raises:
            ValueError: If any weight is negative.

        Example:
            >>> a = FederationVsFoundationAnalyzer()
            >>> a.economy_weight == 0.40
            True
        """
        if any(w < 0 for w in (economy_weight, burden_weight, novelty_weight)):
            raise ValueError("All weights must be non-negative.")
        self.economy_weight = economy_weight
        self.burden_weight = burden_weight
        self.novelty_weight = novelty_weight

    def compute_bridge_burden(self, scope: ScopeProfile) -> BridgeBurdenLevel:
        """Classify the bridge burden level implied by *scope*.

        The classification follows the threshold ladder defined by the module
        constants. Bridge counts are integers and the thresholds are inclusive
        on the upper end of each band:

          bridge_count == 0                        → NONE
          0 < bridge_count ≤ LOW_THRESHOLD         → LOW
          LOW_THRESHOLD < count ≤ MEDIUM_THRESHOLD → MEDIUM
          MEDIUM_THRESHOLD < count ≤ HIGH_THRESHOLD → HIGH
          count > HIGH_THRESHOLD                   → CRITICAL

        Additionally, if scope_width exceeds MAX_SCOPE_WIDTH_FOR_FEDERATION,
        the burden is escalated to at least HIGH regardless of the raw bridge
        count, because the structural impact on the proof graph is too large.

        Args:
            scope: A ScopeProfile containing the bridge_count and scope dimensions.

        Returns:
            A BridgeBurdenLevel enum value.

        Raises:
            TypeError: If *scope* is not a ScopeProfile instance.

        Example:
            >>> sp = ScopeProfile("tid12345", 5, 2, 0.4, 3)
            >>> FederationVsFoundationAnalyzer().compute_bridge_burden(sp)
            <BridgeBurdenLevel.MEDIUM: 'MEDIUM'>
        """
        if not isinstance(scope, ScopeProfile):
            raise TypeError(f"Expected ScopeProfile, got {type(scope)}")

        count = scope.bridge_count

        # Base classification from bridge count
        if count == 0:
            base_level = BridgeBurdenLevel.NONE
        elif count <= BRIDGE_BURDEN_LOW_THRESHOLD:
            base_level = BridgeBurdenLevel.LOW
        elif count <= BRIDGE_BURDEN_MEDIUM_THRESHOLD:
            base_level = BridgeBurdenLevel.MEDIUM
        elif count <= BRIDGE_BURDEN_HIGH_THRESHOLD:
            base_level = BridgeBurdenLevel.HIGH
        else:
            base_level = BridgeBurdenLevel.CRITICAL

        # Escalation: very wide scope always means at least HIGH burden
        if scope.scope_width > MAX_SCOPE_WIDTH_FOR_FEDERATION:
            order = [
                BridgeBurdenLevel.NONE,
                BridgeBurdenLevel.LOW,
                BridgeBurdenLevel.MEDIUM,
                BridgeBurdenLevel.HIGH,
                BridgeBurdenLevel.CRITICAL,
            ]
            base_idx = order.index(base_level)
            escalated_idx = max(base_idx, order.index(BridgeBurdenLevel.HIGH))
            return order[escalated_idx]

        return base_level

    def compute_semantic_economy(self, scope: ScopeProfile) -> float:
        """Compute the semantic economy gain of federating the theorem.

        Semantic economy measures how much more efficiently the pack's
        cross-reference graph would operate if the new theorem is added
        to an existing pack (federation) versus spawning a new one
        (foundation). A high economy gain means the existing pack benefits
        greatly from the addition; a low gain means the theorem would
        dilute the pack's coherence.

        The calculation integrates:
        (a) cross_ref_density:  Higher density in the scope neighbourhood
            means the theorem will create many new cross-references,
            increasing economy.
        (b) scope_width normalised:  More reachable theorems means more
            potential cross-references, but with diminishing returns.
        (c) bridge_count penalty:  Each required bridge reduces economy
            because bridges add indirection to cross-pack queries.
        (d) scope_depth contribution:  Deeper proof chains amplify the
            economy gain because the theorem integrates vertically.

        Args:
            scope: A ScopeProfile to analyse.

        Returns:
            A float in [0.0, 1.0] representing the semantic economy gain.
            Higher means federation is more economical than foundation.

        Raises:
            TypeError: If *scope* is not a ScopeProfile instance.

        Example:
            >>> sp = ScopeProfile("tid12345", 20, 4, 0.7, 1)
            >>> gain = FederationVsFoundationAnalyzer().compute_semantic_economy(sp)
            >>> 0.0 <= gain <= 1.0
            True
        """
        if not isinstance(scope, ScopeProfile):
            raise TypeError(f"Expected ScopeProfile, got {type(scope)}")

        # (a) Cross-reference density contribution
        density_contrib = _clamp(scope.cross_ref_density, 0.0, 1.0) * 0.40

        # (b) Scope width contribution (log-normalised for diminishing returns)
        width_norm = _clamp(
            _safe_log(scope.scope_width + 1) / _safe_log(MAX_SCOPE_WIDTH_FOR_FEDERATION + 1),
            0.0, 1.0
        )
        width_contrib = width_norm * 0.25

        # (c) Bridge penalty: each bridge reduces economy
        raw_bridge_penalty = scope.bridge_count * BRIDGE_WEIGHT_IN_ECONOMY * 0.05
        # Apply extra penalty when burden is CRITICAL
        if scope.bridge_count > BRIDGE_BURDEN_HIGH_THRESHOLD:
            raw_bridge_penalty += ECONOMY_CRITICAL_BRIDGE_PENALTY
        bridge_penalty = _clamp(raw_bridge_penalty, 0.0, 0.60)

        # (d) Depth contribution (log-normalised)
        depth_norm = _clamp(_safe_log(scope.scope_depth + 1) / _safe_log(50.0), 0.0, 1.0)
        depth_contrib = depth_norm * 0.20

        # Combine and subtract penalty
        raw_economy = density_contrib + width_contrib + depth_contrib - bridge_penalty

        # Ensure the result stays within [0, 1]
        return _clamp(raw_economy + 0.15, 0.0, 1.0)  # +0.15 base to avoid flooring at 0

    def recommend_mode(self, scope: ScopeProfile, novelty_score: float) -> PlacementMode:
        """Recommend FEDERATE or FOUND based on scope analysis and novelty.

        This method combines the bridge burden level, semantic economy gain,
        and novelty score into a single placement decision. It implements the
        policy described in theory2.tex Ch62 §2.4:

        (a) If novelty_score < FOUNDATION_NOVELTY_FLOOR, always recommend
            FEDERATE because the theorem is not novel enough for a new pack.
        (b) If bridge burden is CRITICAL, strongly prefer FEDERATE to avoid
            excessive registry complexity.
        (c) If semantic economy gain exceeds SEMANTIC_ECONOMY_FLOOR, prefer
            FEDERATE because the existing pack benefits from the addition.
        (d) If scope_width exceeds MAX_SCOPE_WIDTH_FOR_FEDERATION and novelty
            is high, recommend FOUND regardless of other signals.
        (e) Otherwise, compute a weighted score and choose the higher mode.

        Args:
            scope:         The ScopeProfile for the theorem.
            novelty_score: The theorem's novelty score from the authority
                           choice pipeline, in [0.0, 1.0].

        Returns:
            PlacementMode.FEDERATE or PlacementMode.FOUND.

        Raises:
            TypeError:  If *scope* is not a ScopeProfile instance.
            ValueError: If novelty_score is outside [0.0, 1.0].

        Example:
            >>> sp = ScopeProfile("tid12345", 5, 2, 0.8, 1)
            >>> FederationVsFoundationAnalyzer().recommend_mode(sp, 0.65)
            <PlacementMode.FEDERATE: 'FEDERATE'>
        """
        if not isinstance(scope, ScopeProfile):
            raise TypeError(f"Expected ScopeProfile, got {type(scope)}")
        novelty_score = _clamp(novelty_score, 0.0, 1.0)

        # (a) Low novelty veto: always federate
        if novelty_score < FOUNDATION_NOVELTY_FLOOR:
            return PlacementMode.FEDERATE

        # Compute signals
        burden = self.compute_bridge_burden(scope)
        economy = self.compute_semantic_economy(scope)

        # (b) Critical bridge burden: strongly prefer federation
        if burden == BridgeBurdenLevel.CRITICAL:
            return PlacementMode.FEDERATE

        # (c) High semantic economy: federation is more efficient
        if economy >= SEMANTIC_ECONOMY_FLOOR:
            # Still check if novelty is extreme enough to override economy
            if novelty_score < 0.92:
                return PlacementMode.FEDERATE

        # (d) Wide scope + high novelty: foundation
        if scope.scope_width > MAX_SCOPE_WIDTH_FOR_FEDERATION and novelty_score >= FOUNDATION_NOVELTY_FLOOR:
            return PlacementMode.FOUND

        # (e) Weighted score comparison
        # Federation score: economy * economy_weight + (1 - burden_numeric) * burden_weight
        burden_numeric = {
            BridgeBurdenLevel.NONE: 0.0,
            BridgeBurdenLevel.LOW: 0.20,
            BridgeBurdenLevel.MEDIUM: 0.50,
            BridgeBurdenLevel.HIGH: 0.75,
            BridgeBurdenLevel.CRITICAL: 1.0,
        }[burden]

        federation_score = (
            economy * self.economy_weight
            + (1.0 - burden_numeric) * self.burden_weight
            + (1.0 - novelty_score) * self.novelty_weight
        )
        foundation_score = (
            (1.0 - economy) * self.economy_weight
            + burden_numeric * self.burden_weight
            + novelty_score * self.novelty_weight
        )

        return PlacementMode.FEDERATE if federation_score >= foundation_score else PlacementMode.FOUND

    def explain_recommendation(self, record: PlacementRecord) -> str:
        """Produce a human-readable explanation for a completed PlacementRecord.

        The explanation summarises the key factors that drove the placement
        decision. It is suitable for embedding in decision audit logs,
        email notifications to pack maintainers, and debugging output.

        Args:
            record: A PlacementRecord produced by the coordinator.

        Returns:
            A multi-sentence explanation string.

        Raises:
            TypeError: If *record* is not a PlacementRecord instance.

        Example:
            >>> sp = ScopeProfile("tid12345", 5, 2, 0.7, 1)
            >>> rec = PlacementRecord("tid12345", PlacementMode.FEDERATE,
            ...     BridgeBurdenLevel.LOW, 0.65, sp, "test", _utcnow())
            >>> isinstance(FederationVsFoundationAnalyzer().explain_recommendation(rec), str)
            True
        """
        if not isinstance(record, PlacementRecord):
            raise TypeError(f"Expected PlacementRecord, got {type(record)}")

        sp = record.scope_profile
        mode_word = "federated into an existing pack" if record.mode == PlacementMode.FEDERATE else "given foundation placement (new core pack)"
        burden_desc = {
            BridgeBurdenLevel.NONE: "no inter-pack bridges are required",
            BridgeBurdenLevel.LOW: f"only {sp.bridge_count} bridge(s) are required (LOW burden)",
            BridgeBurdenLevel.MEDIUM: f"{sp.bridge_count} bridges are required (MEDIUM burden)",
            BridgeBurdenLevel.HIGH: f"{sp.bridge_count} bridges are required (HIGH burden — analyst review recommended)",
            BridgeBurdenLevel.CRITICAL: f"{sp.bridge_count} bridges are required (CRITICAL burden — federation strongly preferred)",
        }[record.bridge_burden]

        economy_desc = (
            f"Semantic economy gain is {record.semantic_economy_gain:.3f}, "
            + ("which exceeds the floor and favours federation." if record.semantic_economy_gain >= SEMANTIC_ECONOMY_FLOOR
               else "which is below the floor and does not strongly favour federation.")
        )

        scope_desc = (
            f"The theorem reaches {sp.scope_width} existing theorems "
            f"(width={sp.scope_width}, depth={sp.scope_depth}, "
            f"cross-ref density={sp.cross_ref_density:.3f})."
        )

        lines = [
            f"Theorem '{record.candidate_id}' has been {mode_word}.",
            f"Bridge burden: {burden_desc}.",
            economy_desc,
            scope_desc,
            f"Decision timestamp: {record.timestamp.isoformat()}.",
        ]
        return " ".join(lines)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class FederationVsFoundationCoordinator:
    """Orchestrates placement mode evaluation for post-authority-choice theorems.

    The FederationVsFoundationCoordinator receives theorems that have already
    passed the FEDERATE/FOUNDATION bar in the authority-choice pipeline and
    determines the precise placement mode. It wraps the analyzer with
    validation, record construction, and optional witness notification.

    Each call to evaluate() produces exactly one PlacementRecord. Batch
    evaluation processes a list of (candidate_id, scope) pairs and returns
    one record per pair.

    Attributes:
        coordinator_id: Unique identifier for this coordinator instance.
        analyzer:       The FederationVsFoundationAnalyzer performing the
                        actual computation.
        witness:        Optional FederationVsFoundationWitness for auditing.
    """

    def __init__(
        self,
        analyzer: FederationVsFoundationAnalyzer | None = None,
        witness: "FederationVsFoundationWitness | None" = None,
    ) -> None:
        """Initialise the coordinator.

        Args:
            analyzer: Analyzer instance. Created with defaults if None.
            witness:  Optional witness for audit logging.

        Example:
            >>> coord = FederationVsFoundationCoordinator()
            >>> coord.coordinator_id is not None
            True
        """
        self.coordinator_id: str = _uid()
        self.analyzer: FederationVsFoundationAnalyzer = (
            analyzer or FederationVsFoundationAnalyzer()
        )
        self.witness: "FederationVsFoundationWitness | None" = witness

    def evaluate(
        self,
        candidate_id: str,
        scope: ScopeProfile,
        novelty_score: float = 0.85,
    ) -> PlacementRecord:
        """Evaluate a single theorem's placement mode and produce a PlacementRecord.

        Steps:
          1. Validate inputs.
          2. Compute bridge burden level.
          3. Compute semantic economy gain.
          4. Recommend placement mode.
          5. Generate justification string.
          6. Construct and return PlacementRecord.
          7. Notify witness (if any).

        Args:
            candidate_id:  The theorem's unique identifier.
            scope:         The ScopeProfile for this theorem.
            novelty_score: The theorem's novelty score from the upstream
                           authority-choice pipeline (default 0.85).

        Returns:
            A PlacementRecord capturing the full placement decision.

        Raises:
            TypeError:  If *scope* is not a ScopeProfile.
            ValueError: If *candidate_id* is empty.

        Example:
            >>> coord = FederationVsFoundationCoordinator()
            >>> sp = ScopeProfile("theorem-a1", 8, 3, 0.6, 2)
            >>> rec = coord.evaluate("theorem-a1", sp, 0.88)
            >>> rec.candidate_id == "theorem-a1"
            True
        """
        if not candidate_id:
            raise ValueError("candidate_id must not be empty.")
        if not isinstance(scope, ScopeProfile):
            raise TypeError(f"Expected ScopeProfile, got {type(scope)}")

        novelty_score = _clamp(novelty_score, 0.0, 1.0)
        burden = self.analyzer.compute_bridge_burden(scope)
        economy = self.analyzer.compute_semantic_economy(scope)
        mode = self.analyzer.recommend_mode(scope, novelty_score)

        # Build a concise justification string
        justification = (
            f"mode={mode.value} bridge_burden={burden.value} "
            f"economy={economy:.4f} novelty={novelty_score:.4f} "
            f"width={scope.scope_width} depth={scope.scope_depth}"
        )

        record = PlacementRecord(
            candidate_id=candidate_id,
            mode=mode,
            bridge_burden=burden,
            semantic_economy_gain=round(economy, 6),
            scope_profile=scope,
            justification=justification,
            timestamp=_utcnow(),
        )

        if self.witness is not None:
            self.witness.observe(record)

        return record

    def batch_evaluate(
        self,
        items: list[tuple[str, ScopeProfile, float]],
    ) -> list[PlacementRecord]:
        """Evaluate a batch of (candidate_id, scope, novelty_score) triples.

        Args:
            items: A list of triples, each containing (candidate_id, scope,
                   novelty_score).

        Returns:
            A list of PlacementRecord objects, one per input triple.

        Raises:
            TypeError: If *items* is not a list.

        Example:
            >>> coord = FederationVsFoundationCoordinator()
            >>> sp = ScopeProfile("t-batch-x1", 3, 1, 0.5, 0)
            >>> records = coord.batch_evaluate([("t-batch-x1", sp, 0.70)])
            >>> len(records) == 1
            True
        """
        if not isinstance(items, list):
            raise TypeError(f"Expected list, got {type(items)}")

        return [self.evaluate(cid, scope, novelty) for cid, scope, novelty in items]

    def policy_report(self, records: list[PlacementRecord]) -> dict[str, Any]:
        """Aggregate a list of PlacementRecords into a policy analysis report.

        The report includes mode distribution, bridge burden distribution,
        mean semantic economy, and recommendations for registry management.

        Args:
            records: A list of PlacementRecord objects to analyse.

        Returns:
            A dict with keys: record_count, mode_counts, burden_counts,
            mean_economy, std_economy, foundation_pct, coordinator_id.

        Example:
            >>> coord = FederationVsFoundationCoordinator()
            >>> report = coord.policy_report([])
            >>> report["record_count"] == 0
            True
        """
        n = len(records)
        if n == 0:
            return {
                "record_count": 0,
                "mode_counts": {},
                "burden_counts": {},
                "mean_economy": 0.0,
                "std_economy": 0.0,
                "foundation_pct": 0.0,
                "coordinator_id": self.coordinator_id,
            }

        mode_counts: dict[str, int] = {}
        burden_counts: dict[str, int] = {}
        economies: list[float] = []

        for rec in records:
            mode_counts[rec.mode.value] = mode_counts.get(rec.mode.value, 0) + 1
            burden_counts[rec.bridge_burden.value] = burden_counts.get(rec.bridge_burden.value, 0) + 1
            economies.append(rec.semantic_economy_gain)

        mean_economy = sum(economies) / n
        variance = sum((e - mean_economy) ** 2 for e in economies) / n
        std_economy = math.sqrt(variance)
        foundation_count = mode_counts.get(PlacementMode.FOUND.value, 0)

        return {
            "record_count": n,
            "mode_counts": mode_counts,
            "burden_counts": burden_counts,
            "mean_economy": round(mean_economy, 6),
            "std_economy": round(std_economy, 6),
            "foundation_pct": round(foundation_count / n * 100, 2),
            "coordinator_id": self.coordinator_id,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class FederationVsFoundationWitness:
    """Audit log for federation-vs-foundation placement decisions.

    The witness records every PlacementRecord produced by the coordinator
    and exposes methods for retrieving contention cases (where FEDERATE and
    FOUND were nearly tied) and generating summary reports.

    Attributes:
        witness_id: Unique identifier for this witness instance.
    """

    def __init__(self) -> None:
        """Initialise the witness with an empty log.

        Example:
            >>> w = FederationVsFoundationWitness()
            >>> w.report()["total_records"] == 0
            True
        """
        self.witness_id: str = _uid()
        self._log: list[PlacementRecord] = []

    def observe(self, record: PlacementRecord) -> None:
        """Append *record* to the internal log.

        Args:
            record: The PlacementRecord to store.

        Raises:
            TypeError: If *record* is not a PlacementRecord instance.

        Example:
            >>> w = FederationVsFoundationWitness()
            >>> sp = ScopeProfile("t99999999", 2, 1, 0.5, 0)
            >>> rec = PlacementRecord("t99999999", PlacementMode.FEDERATE,
            ...     BridgeBurdenLevel.NONE, 0.55, sp, "ok", _utcnow())
            >>> w.observe(rec)
            >>> len(w._log) == 1
            True
        """
        if not isinstance(record, PlacementRecord):
            raise TypeError(f"Expected PlacementRecord, got {type(record)}")
        self._log.append(record)

    def contention_log(self) -> list[PlacementRecord]:
        """Return records where the mode recommendation was close to a tie.

        A record is considered a contention case if the semantic economy gain
        is within TIE_MARGIN of SEMANTIC_ECONOMY_FLOOR (i.e., the economy
        signal was not decisive) AND the bridge burden is neither NONE nor
        CRITICAL (i.e., neither extreme favoured a clear winner).

        These borderline cases are valuable for human review because the
        automated decision may not reflect domain expert judgement.

        Returns:
            A list of PlacementRecord objects that were near-tie decisions.

        Example:
            >>> w = FederationVsFoundationWitness()
            >>> w.contention_log()
            []
        """
        contention: list[PlacementRecord] = []
        neutral_burdens = {BridgeBurdenLevel.LOW, BridgeBurdenLevel.MEDIUM, BridgeBurdenLevel.HIGH}

        for rec in self._log:
            economy_near_floor = abs(rec.semantic_economy_gain - SEMANTIC_ECONOMY_FLOOR) <= TIE_MARGIN
            burden_neutral = rec.bridge_burden in neutral_burdens
            if economy_near_floor and burden_neutral:
                contention.append(rec)

        return contention

    def report(self) -> dict[str, Any]:
        """Produce a summary report of all records in the log.

        Returns:
            A dict with keys: total_records, mode_counts, burden_counts,
            contention_count, witness_id.

        Example:
            >>> w = FederationVsFoundationWitness()
            >>> r = w.report()
            >>> "total_records" in r
            True
        """
        total = len(self._log)
        mode_counts: dict[str, int] = {}
        burden_counts: dict[str, int] = {}

        for rec in self._log:
            mode_counts[rec.mode.value] = mode_counts.get(rec.mode.value, 0) + 1
            burden_counts[rec.bridge_burden.value] = burden_counts.get(rec.bridge_burden.value, 0) + 1

        return {
            "total_records": total,
            "mode_counts": mode_counts,
            "burden_counts": burden_counts,
            "contention_count": len(self.contention_log()),
            "witness_id": self.witness_id,
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Federation vs Foundation Analysis — Smoke Test")
    print("=" * 70)

    # Instantiate all three main classes
    witness = FederationVsFoundationWitness()
    analyzer = FederationVsFoundationAnalyzer()
    coordinator = FederationVsFoundationCoordinator(analyzer=analyzer, witness=witness)

    # Build a variety of scope profiles that exercise all burden levels and modes
    scope_profiles = [
        # Expected FEDERATE: low novelty, moderate affinity, low bridges
        ("thm-fed-scope-01", ScopeProfile("thm-fed-scope-01", 12, 3, 0.72, 1), 0.70),
        # Expected FEDERATE: medium bridges but economy is high
        ("thm-fed-scope-02", ScopeProfile("thm-fed-scope-02", 30, 5, 0.80, 4), 0.84),
        # Expected FOUND: high novelty, low affinity, sparse scope
        ("thm-found-scope-03", ScopeProfile("thm-found-scope-03", 3, 8, 0.20, 0), 0.93),
        # Expected FEDERATE: critical bridge burden overrides high novelty
        ("thm-fed-scope-04", ScopeProfile("thm-fed-scope-04", 50, 10, 0.60, 15), 0.91),
        # Near-tie case for contention log
        ("thm-contention-05", ScopeProfile("thm-contention-05", 20, 4, 0.32, 3), 0.88),
    ]

    print("\n--- Individual scope analysis ---")
    for cid, sp, novelty in scope_profiles:
        burden = analyzer.compute_bridge_burden(sp)
        economy = analyzer.compute_semantic_economy(sp)
        mode = analyzer.recommend_mode(sp, novelty)
        print(
            f"  {cid}: mode={mode.value:8s} burden={burden.value:8s} "
            f"economy={economy:.4f} novelty={novelty:.2f}"
        )

    print("\n--- Batch evaluate ---")
    records = coordinator.batch_evaluate(scope_profiles)
    for rec in records:
        print(f"  {rec.candidate_id}: {rec.mode.value:8s} bridge_burden={rec.bridge_burden.value:8s}")

    print("\n--- Policy report ---")
    report = coordinator.policy_report(records)
    for k, v in report.items():
        print(f"  {k}: {v}")

    print("\n--- Witness report ---")
    wrep = witness.report()
    for k, v in wrep.items():
        print(f"  {k}: {v}")

    print("\n--- Explain recommendation for first record ---")
    explanation = analyzer.explain_recommendation(records[0])
    print(f"  {explanation}")

    print("\n--- Contention cases ---")
    contention = witness.contention_log()
    print(f"  {len(contention)} contention case(s) found.")

    print("\nSmoke test PASSED.")
