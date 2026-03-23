"""
Domain formation: when the right discovery is a new semantic region.

# copilot: shared-core marker

Theory reference: theory2.tex Ch59 — Domain Formation When the Right Discovery
Is a New Semantic Region.  This module implements the stage of the regime
bootstrapping pipeline that decides *when* an entirely new semantic domain must
be created rather than extending or adjusting an existing one.

Background
----------
A new semantic regime in JuGeo corresponds to a new mathematical area being
added to the system's coverage.  Examples include the introduction of motives,
derived categories, or perfectoid geometry: each of these required not just new
theorems but a genuinely new organisational layer.

Domain formation is triggered when existing domains cannot adequately capture a
new kind of mathematical object.  The formal criterion is an *obstruction
clustering test*: if obstruction records arising from failed descent computations
cluster in a region of semantic space where no existing domain has jurisdiction,
the system infers that a new domain is warranted.

Trigger Analysis
----------------
The trigger analysis identifies *semantic gaps*: regions where obstruction
records cluster but no existing domain has jurisdiction.  The analysis proceeds
in three phases:

1. **Cluster detection** — obstruction records are grouped by spatial proximity
   in the semantic embedding space.  Clusters whose membership exceeds
   ``DomainFormationConfig.min_cluster_size`` are retained.

2. **Gap scoring** — each retained cluster is scored via the
   ``_score_cluster_gap`` function which combines the cluster's internal
   severity distribution, the density of its members, and the distance to the
   nearest existing domain boundary.

   Formally, for a cluster *C* with member severities
   ``{s_1, …, s_n}``, density ``ρ``, and nearest-domain distance ``d``, the
   gap score is::

       gap_score(C) = clamp(
           w_sev · mean(s_i) + w_dens · ρ + w_dist · sigmoid(d),
           0.0, 1.0
       )

   where ``w_sev = 0.5``, ``w_dens = 0.3``, ``w_dist = 0.2`` are the default
   blending weights defined as module-level constants.

3. **Severity classification** — gap scores are bucketed into
   ``GapSeverity`` levels using the threshold vector
   ``GAP_SEVERITY_THRESHOLDS``.

Domain Proposal
---------------
Each semantic gap above the configured ``gap_severity_threshold`` triggers a
``DomainProposal``.  The proposal is constructed by:

- Collecting *candidate generators* from the gap's constituent clusters.
- Synthesising *axiom sketches* — human-readable placeholder axioms that
  capture the apparent structural constraints implied by the obstructions.
- Recording a structured rationale string that traces back to the originating
  clusters and gap score.

Domain Validation
-----------------
Proposals pass through a ``DomainValidationResult`` check that verifies:

- The generator set is non-empty and within ``max_domain_generators``.
- The proposal does not substantially overlap an already-registered domain
  (overlap fraction < ``overlap_tolerance``).
- The viability score is above ``viability_threshold``.

Registration
------------
Validated proposals are converted to ``DomainRecord`` objects and stored in the
``DomainFormationCoordinator``'s internal registry.  A
``RegistrationWitnessReport`` is emitted for audit purposes.

Design Notes
------------
* All cross-module imports are wrapped in ``try/except Exception: pass`` blocks
  so the module operates in isolation during testing.
* Value objects (``frozen=True, slots=True``) are used for all data-transfer
  types; mutable fields use ``Optional[Any] = None`` rather than
  ``field(default_factory=dict)`` to preserve hash compatibility.
* The ``DomainFormationAnalyzer`` and ``DomainFormationWitness`` helper classes
  are private-by-convention but included in ``__all__`` for testing purposes.
* The module-level ``run_domain_formation_cycle`` and ``score_semantic_gap``
  functions provide a lightweight façade for callers that do not need the full
  coordinator machinery.

Typical Usage
-------------
::

    from jugeo.ideation.regime_bootstrapping.domain_formation_when_the_right_di import (
        DomainFormationCoordinator,
        DomainFormationConfig,
        ObstructionCluster,
        run_domain_formation_cycle,
        score_semantic_gap,
    )

    config = DomainFormationConfig(
        min_cluster_size=4,
        gap_severity_threshold=GapSeverity.SIGNIFICANT,
        viability_threshold=0.70,
    )
    clusters = [
        ObstructionCluster(
            cluster_id="cl-001",
            centroid=(0.3, 0.7),
            member_ids=("obs-1", "obs-2", "obs-3", "obs-4"),
            obstruction_kinds=("topological", "algebraic"),
            severity_score=0.82,
        )
    ]
    result = run_domain_formation_cycle(clusters, config=config)
    print(result.domains_registered)  # 1

See Also
--------
* ``domain_formation`` — earlier domain-formation pipeline (Ch55).
* ``type_constructors`` — type-constructor search that follows formation.
* ``regime_bootstrapping`` — full bootstrapping orchestration.
* theory2.tex Ch59 for the mathematical treatment.
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
    "GapSeverity",
    "DomainStatus",
    # Config
    "DomainFormationConfig",
    # Value objects
    "ObstructionCluster",
    "SemanticGap",
    "DomainProposal",
    "DomainValidationResult",
    "DomainRecord",
    "DomainFormationResult",
    "CoverageGapReport",
    "ViabilityReport",
    "OverlapReport",
    "GapWitnessReport",
    "ProposalWitnessReport",
    "RegistrationWitnessReport",
    # Classes
    "DomainFormationAnalyzer",
    "DomainFormationWitness",
    "DomainFormationCoordinator",
    # Free functions
    "run_domain_formation_cycle",
    "score_semantic_gap",
    "select_gap_for_formation",
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
        ObstructionField,
        ObstructionKind,
        DomainFormation,
        DomainType,
        TypeConstructor,
        TypeConstructorKind,
        RegimeCandidate,
        BootstrapStep,
        BootstrapPlan,
        BootstrapResult,
        BootstrapStatus,
        BootstrapPriority,
        RegimeBootstrapperConfig,
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

#: Blending weight for mean severity component of gap score (theory2.tex §59.3)
GAP_WEIGHT_SEVERITY: float = 0.50

#: Blending weight for cluster density component of gap score
GAP_WEIGHT_DENSITY: float = 0.30

#: Blending weight for nearest-domain distance component of gap score
GAP_WEIGHT_DISTANCE: float = 0.20

#: Severity bucket thresholds (minor→moderate, moderate→significant, significant→critical)
GAP_SEVERITY_THRESHOLDS: Tuple[float, float, float] = (0.25, 0.50, 0.75)

#: Maximum gap score magnitude; scores are clamped to [0, MAX_GAP_SCORE]
MAX_GAP_SCORE: float = 1.0

#: Minimum gap score below which a cluster is ignored regardless of size
MIN_GAP_SCORE: float = 0.05

#: Default domain name prefix when deriving names from gap identifiers
DOMAIN_NAME_PREFIX: str = "semantic-domain"

#: Default axiom sketch template; ``{gen}`` is replaced with generator names
AXIOM_SKETCH_TEMPLATE: str = "∀ x : {gen}. ∃ y : {gen}. R(x, y)"

#: Viability penalty applied per blocking issue found during validation
VIABILITY_PENALTY_PER_ISSUE: float = 0.10

#: Score bonus for proposals whose generators have a prior coverage record
PRIOR_COVERAGE_BONUS: float = 0.05

#: Maximum number of axiom sketches synthesised per proposal
MAX_AXIOM_SKETCHES: int = 8

#: Sigmoid sharpness parameter used in distance-to-domain scoring
SIGMOID_SHARPNESS: float = 6.0

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
GeneratorTuple = Tuple[str, ...]
RelationTuple = Tuple[str, ...]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GapSeverity(Enum):
    """Severity level assigned to a semantic gap.

    Levels map to quartiles of the gap-score distribution and are used to
    prioritise which gaps receive domain proposals first.

    Attributes
    ----------
    MINOR:
        Gap score < 0.25.  The gap is unlikely to warrant a new domain; more
        data should be collected before acting.
    MODERATE:
        Gap score in [0.25, 0.50).  A new domain may be useful but is not
        urgently required.
    SIGNIFICANT:
        Gap score in [0.50, 0.75).  Existing domains are clearly struggling;
        a proposal should be generated.
    CRITICAL:
        Gap score ≥ 0.75.  Existing domains are inadequate; a proposal is
        generated and marked high-priority.
    """

    MINOR = auto()
    MODERATE = auto()
    SIGNIFICANT = auto()
    CRITICAL = auto()


class DomainStatus(Enum):
    """Lifecycle status of a semantic domain.

    Attributes
    ----------
    PROPOSED:
        The domain has been proposed but not yet validated.
    PROVISIONAL:
        The domain has passed validation but is still on probation; its
        axioms may be revised.
    ACTIVE:
        The domain is fully registered and in active use.
    DEPRECATED:
        The domain has been superseded or found to be redundant.
    """

    PROPOSED = auto()
    PROVISIONAL = auto()
    ACTIVE = auto()
    DEPRECATED = auto()


# ---------------------------------------------------------------------------
# Dataclasses — configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainFormationConfig:
    """Immutable configuration bundle for the domain formation pipeline.

    All parameters have sensible defaults derived from the empirical tuning
    described in theory2.tex §59.4.

    Attributes
    ----------
    min_cluster_size:
        Minimum number of obstruction records required for a cluster to be
        considered during gap analysis.  Smaller clusters are treated as noise.
    gap_severity_threshold:
        Only gaps whose severity meets or exceeds this level will trigger a
        domain proposal.
    max_proposals_per_cycle:
        Upper bound on the number of domain proposals emitted in a single
        formation cycle.  The top-scoring gaps are chosen first.
    overlap_tolerance:
        Maximum permissible Jaccard overlap fraction between the generator set
        of a new proposal and any already-registered domain.  Proposals that
        exceed this threshold are rejected.
    viability_threshold:
        Minimum viability score (in [0, 1]) for a proposal to be registered.
    enable_overlap_check:
        When ``False``, the overlap check is skipped entirely.  Useful during
        exploratory analysis.
    max_domain_generators:
        Hard upper limit on the number of generators a domain proposal may
        contain.  Proposals exceeding this limit are truncated before
        validation.
    """

    min_cluster_size: int = 3
    gap_severity_threshold: GapSeverity = GapSeverity.MODERATE
    max_proposals_per_cycle: int = 5
    overlap_tolerance: float = 0.15
    viability_threshold: float = 0.65
    enable_overlap_check: bool = True
    max_domain_generators: int = 128


# ---------------------------------------------------------------------------
# Dataclasses — value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionCluster:
    """An identified cluster of obstruction records in semantic space.

    Produced by a clustering pass over raw obstruction data (e.g. DBSCAN or
    hierarchical agglomeration) before gap analysis begins.

    Attributes
    ----------
    cluster_id:
        Stable identifier for the cluster, typically a UUID or a hash of the
        member IDs.
    centroid:
        2-D coordinates of the cluster centroid in the semantic embedding
        space.
    member_ids:
        Identifiers of the individual obstruction records belonging to this
        cluster.
    obstruction_kinds:
        Distinct kinds of obstructions represented in the cluster (e.g.
        ``"topological"``, ``"algebraic"``).
    severity_score:
        Aggregate severity of the cluster, pre-computed by the clustering
        pass.  In ``[0.0, 1.0]``.
    metadata:
        Optional auxiliary data (defaults to ``None``).
    """

    cluster_id: str
    centroid: Tuple[float, float]
    member_ids: Tuple[str, ...]
    obstruction_kinds: Tuple[str, ...]
    severity_score: float
    metadata: Optional[Any] = None


@dataclass(frozen=True, slots=True)
class SemanticGap:
    """A semantic gap inferred from one or more obstruction clusters.

    Attributes
    ----------
    gap_id:
        Stable identifier for the gap, derived from the source cluster IDs.
    source_clusters:
        IDs of the clusters that together define this gap.
    gap_score:
        Computed gap score in ``[0.0, 1.0]`` (higher = more severe gap).
    severity:
        Discrete severity classification derived from ``gap_score``.
    description:
        Human-readable description of the gap, including its inferred cause.
    candidate_generators:
        Generator names collected from the source clusters and proposed as
        the axiom substrate for the new domain.
    created_at:
        ISO-8601 UTC timestamp at which the gap was identified.
    """

    gap_id: str
    source_clusters: Tuple[str, ...]
    gap_score: float
    severity: GapSeverity
    description: str
    candidate_generators: Tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class DomainProposal:
    """A proposal to create a new semantic domain.

    Attributes
    ----------
    proposal_id:
        Stable identifier, derived from the gap ID and proposed domain name.
    gap_id:
        The gap that triggered this proposal.
    domain_name:
        Human-readable name for the proposed domain.
    generators:
        Ordered tuple of generator names that constitute the domain's
        algebraic substrate.
    relations:
        Tuple of relation identifiers holding among the generators.
    axiom_sketches:
        Tuple of informal axiom strings that characterise the domain.
    rationale:
        Prose explanation of why this domain is warranted, tracing back to
        the originating gap and clusters.
    status:
        Current lifecycle status of the proposal.
    """

    proposal_id: str
    gap_id: str
    domain_name: str
    generators: Tuple[str, ...]
    relations: Tuple[str, ...]
    axiom_sketches: Tuple[str, ...]
    rationale: str
    status: DomainStatus


@dataclass(frozen=True, slots=True)
class DomainValidationResult:
    """Result of validating a ``DomainProposal``.

    Attributes
    ----------
    proposal_id:
        ID of the proposal that was validated.
    is_valid:
        ``True`` if the proposal passed all validation checks.
    score:
        Numeric validation score in ``[0.0, 1.0]``; higher is better.
    issues:
        Tuple of issue descriptions for every check that failed.
    recommendations:
        Tuple of suggestions for improving the proposal.
    """

    proposal_id: str
    is_valid: bool
    score: float
    issues: Tuple[str, ...]
    recommendations: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainRecord:
    """A registered semantic domain stored in the coordinator's registry.

    Attributes
    ----------
    record_id:
        Stable identifier for the record.
    domain_name:
        Human-readable name of the domain.
    generators:
        Tuple of generator names.
    relations:
        Tuple of relation identifiers.
    status:
        Current lifecycle status.
    registered_at:
        ISO-8601 UTC timestamp of registration.
    metadata:
        Optional auxiliary data (defaults to ``None``).
    """

    record_id: str
    domain_name: str
    generators: Tuple[str, ...]
    relations: Tuple[str, ...]
    status: DomainStatus
    registered_at: str
    metadata: Optional[Any] = None


@dataclass(frozen=True, slots=True)
class DomainFormationResult:
    """Summary of a complete domain formation cycle.

    Attributes
    ----------
    cycle_id:
        Unique identifier for the cycle run.
    gaps_found:
        Number of semantic gaps identified during the cycle.
    proposals_made:
        Number of domain proposals emitted.
    domains_registered:
        Number of proposals that were validated and registered.
    records:
        Tuple of ``record_id`` strings for newly registered domains.
    duration_seconds:
        Wall-clock duration of the cycle in seconds.
    """

    cycle_id: str
    gaps_found: int
    proposals_made: int
    domains_registered: int
    records: Tuple[str, ...]
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CoverageGapReport:
    """Report describing an area of semantic space that remains uncovered.

    Attributes
    ----------
    gap_id:
        Identifier of the coverage gap (may correspond to a ``SemanticGap``).
    uncovered_patterns:
        Observable patterns in the obstruction data that are not addressed by
        any registered domain.
    coverage_fraction:
        Fraction of the gap that is currently addressed, in ``[0.0, 1.0]``.
    severity:
        Severity of the remaining uncovered area.
    notes:
        Free-text commentary added by the analyzer.
    """

    gap_id: str
    uncovered_patterns: Tuple[str, ...]
    coverage_fraction: float
    severity: GapSeverity
    notes: str


@dataclass(frozen=True, slots=True)
class ViabilityReport:
    """Viability assessment for a ``DomainProposal``.

    Attributes
    ----------
    proposal_id:
        ID of the assessed proposal.
    viability_score:
        Computed viability in ``[0.0, 1.0]``; a score above
        ``DomainFormationConfig.viability_threshold`` is required to proceed.
    is_viable:
        ``True`` if ``viability_score >= viability_threshold``.
    blocking_issues:
        Issues that, if present, automatically disqualify the proposal.
    warnings:
        Non-blocking concerns that do not prevent registration but should be
        addressed in a future revision.
    """

    proposal_id: str
    viability_score: float
    is_viable: bool
    blocking_issues: Tuple[str, ...]
    warnings: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverlapReport:
    """Overlap check result comparing a new domain proposal against existing domains.

    Attributes
    ----------
    new_domain_id:
        The proposal or record ID being checked.
    overlapping_domain_ids:
        IDs of existing domains whose generator sets overlap with the new
        domain beyond the zero-tolerance floor.
    max_overlap_fraction:
        The highest Jaccard overlap fraction found across all comparisons.
    is_acceptable:
        ``True`` if ``max_overlap_fraction < overlap_tolerance``.
    """

    new_domain_id: str
    overlapping_domain_ids: Tuple[str, ...]
    max_overlap_fraction: float
    is_acceptable: bool


@dataclass(frozen=True, slots=True)
class GapWitnessReport:
    """Audit record capturing the outcome of a gap-detection pass.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness report.
    cluster_count:
        Number of clusters examined during the pass.
    gap_count:
        Number of semantic gaps identified.
    severity_distribution:
        Mapping from severity-level name to count of gaps at that level.
    timestamp:
        ISO-8601 UTC timestamp of the witness event.
    notes:
        Free-text commentary (e.g. cycle ID, configuration hash).
    """

    witness_id: str
    cluster_count: int
    gap_count: int
    severity_distribution: Optional[Any]  # Dict[str, int] at runtime
    timestamp: str
    notes: str


@dataclass(frozen=True, slots=True)
class ProposalWitnessReport:
    """Audit record capturing the creation of a single ``DomainProposal``.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness report.
    proposal_id:
        The proposal that was created.
    domain_name:
        Human-readable domain name assigned to the proposal.
    generator_count:
        Number of generators included in the proposal.
    is_viable:
        Whether the proposal subsequently passed the viability check.
    timestamp:
        ISO-8601 UTC timestamp of the witness event.
    """

    witness_id: str
    proposal_id: str
    domain_name: str
    generator_count: int
    is_viable: bool
    timestamp: str


@dataclass(frozen=True, slots=True)
class RegistrationWitnessReport:
    """Audit record capturing the registration of a new ``DomainRecord``.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness report.
    record_id:
        The record that was registered.
    domain_name:
        Human-readable domain name of the registered domain.
    status:
        ``DomainStatus`` assigned at registration time.
    timestamp:
        ISO-8601 UTC timestamp of the witness event.
    """

    witness_id: str
    record_id: str
    domain_name: str
    status: DomainStatus
    timestamp: str


# ---------------------------------------------------------------------------
# Module-level private helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp, e.g. ``'2024-01-15T12:34:56.789012+00:00'``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _uid() -> str:
    """Generate a fresh UUID4 string.

    Returns
    -------
    str
        A new UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        Value to clamp.
    lo:
        Lower bound (default ``0.0``).
    hi:
        Upper bound (default ``1.0``).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


def _sigmoid(x: float, sharpness: float = SIGMOID_SHARPNESS) -> float:
    """Logistic sigmoid centred at zero.

    Used to map unbounded distance values into ``(0, 1)``.

    Parameters
    ----------
    x:
        Input value.
    sharpness:
        Controls how quickly the sigmoid transitions; larger values create a
        steeper curve (default ``SIGMOID_SHARPNESS``).

    Returns
    -------
    float
        ``1 / (1 + exp(-sharpness * x))``.
    """
    try:
        return 1.0 / (1.0 + math.exp(-sharpness * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _score_cluster_gap(
    cluster: ObstructionCluster,
    config: DomainFormationConfig,
) -> float:
    """Compute a semantic gap score for a single obstruction cluster.

    The score blends three components according to the formula in
    theory2.tex §59.3::

        gap_score = clamp(
            w_sev * severity + w_dens * density + w_dist * sig(dist),
            0.0, 1.0
        )

    where:

    * ``severity`` is the cluster's ``severity_score``.
    * ``density`` is approximated as ``len(member_ids) / (min_cluster_size * 8)``
      clamped to ``[0, 1]``.
    * ``dist`` is the normalised centroid distance from the origin (a proxy for
      isolation in the semantic embedding space).

    Parameters
    ----------
    cluster:
        The obstruction cluster to score.
    config:
        Active configuration; ``min_cluster_size`` is used to normalise the
        density component.

    Returns
    -------
    float
        A gap score in ``[0.0, 1.0]``.
    """
    severity = _clamp(cluster.severity_score)

    # Density: normalise member count against a "large cluster" heuristic
    density_raw = len(cluster.member_ids) / max(1, config.min_cluster_size * 8)
    density = _clamp(density_raw)

    # Distance: Euclidean distance of centroid from origin as isolation proxy
    cx, cy = cluster.centroid
    dist_raw = math.sqrt(cx * cx + cy * cy)
    # Normalise to (0,1) via sigmoid; centroids far from origin → higher score
    dist_component = _sigmoid(dist_raw - 1.0)

    score = (
        GAP_WEIGHT_SEVERITY * severity
        + GAP_WEIGHT_DENSITY * density
        + GAP_WEIGHT_DISTANCE * dist_component
    )
    return _clamp(score)


def _severity_from_score(score: float) -> GapSeverity:
    """Classify a numeric gap score into a ``GapSeverity`` level.

    Uses the module-level ``GAP_SEVERITY_THRESHOLDS`` tuple
    ``(minor_max, moderate_max, significant_max)`` to assign a bucket.

    Parameters
    ----------
    score:
        Gap score in ``[0.0, 1.0]``.

    Returns
    -------
    GapSeverity
        The corresponding severity level.
    """
    lo, mid, hi = GAP_SEVERITY_THRESHOLDS
    if score < lo:
        return GapSeverity.MINOR
    if score < mid:
        return GapSeverity.MODERATE
    if score < hi:
        return GapSeverity.SIGNIFICANT
    return GapSeverity.CRITICAL


def _synthesize_axiom_sketches(
    generators: List[str],
    gap: SemanticGap,
) -> List[str]:
    """Synthesise placeholder axiom sketches for a domain proposal.

    For each generator in *generators* a structural axiom is produced using
    ``AXIOM_SKETCH_TEMPLATE``.  Additionally, if there are at least two
    generators, a binary interaction axiom is added.  The list is capped at
    ``MAX_AXIOM_SKETCHES`` entries.

    Parameters
    ----------
    generators:
        Candidate generators for the new domain.
    gap:
        The ``SemanticGap`` that motivated the proposal; its ``gap_id`` is
        embedded in the axioms for traceability.

    Returns
    -------
    list of str
        A list of informal axiom strings, length ≤ ``MAX_AXIOM_SKETCHES``.
    """
    sketches: List[str] = []
    for gen in generators[:MAX_AXIOM_SKETCHES]:
        sketches.append(AXIOM_SKETCH_TEMPLATE.format(gen=gen))

    # Add a binary interaction axiom when multiple generators are present
    if len(generators) >= 2 and len(sketches) < MAX_AXIOM_SKETCHES:
        g0, g1 = generators[0], generators[1]
        sketches.append(
            f"∀ x : {g0}, y : {g1}. ∃ z : {g0}. φ_{gap.gap_id[:8]}(x, y, z)"
        )

    # Add a gap-specific coherence axiom
    if len(sketches) < MAX_AXIOM_SKETCHES:
        sketches.append(
            f"Coherence({gap.gap_id[:8]}): the domain is closed under the"
            f" obstruction-resolution map implied by gap score {gap.gap_score:.3f}."
        )

    return sketches[:MAX_AXIOM_SKETCHES]


def _compute_overlap_fraction(
    domain_a_generators: Tuple[str, ...],
    domain_b_generators: Tuple[str, ...],
) -> float:
    """Compute the Jaccard overlap fraction between two generator sets.

    Parameters
    ----------
    domain_a_generators:
        Generator tuple for domain A.
    domain_b_generators:
        Generator tuple for domain B.

    Returns
    -------
    float
        ``|A ∩ B| / |A ∪ B|``, or ``0.0`` if both sets are empty.
    """
    set_a = set(domain_a_generators)
    set_b = set(domain_b_generators)
    union_size = len(set_a | set_b)
    if union_size == 0:
        return 0.0
    intersection_size = len(set_a & set_b)
    return intersection_size / union_size


def _build_gap_id(cluster_ids: List[str]) -> str:
    """Derive a deterministic gap identifier from a list of cluster IDs.

    Parameters
    ----------
    cluster_ids:
        IDs of the clusters contributing to the gap.

    Returns
    -------
    str
        A hex digest prefix that stably identifies the gap.
    """
    combined = "|".join(sorted(cluster_ids))
    digest = hashlib.sha256(combined.encode()).hexdigest()
    return f"gap-{digest[:16]}"


def _build_proposal_id(gap_id: str, domain_name: str) -> str:
    """Derive a deterministic proposal identifier.

    Parameters
    ----------
    gap_id:
        The gap that motivates the proposal.
    domain_name:
        The human-readable domain name.

    Returns
    -------
    str
        A hex digest prefix that stably identifies the proposal.
    """
    combined = f"{gap_id}::{domain_name}"
    digest = hashlib.sha256(combined.encode()).hexdigest()
    return f"prop-{digest[:16]}"


def _build_record_id(proposal_id: str) -> str:
    """Derive a deterministic record identifier from a proposal ID.

    Parameters
    ----------
    proposal_id:
        The validated proposal being registered.

    Returns
    -------
    str
        A hex digest prefix that stably identifies the domain record.
    """
    digest = hashlib.sha256(proposal_id.encode()).hexdigest()
    return f"rec-{digest[:16]}"


def _domain_name_from_gap(gap: SemanticGap) -> str:
    """Derive a human-readable domain name from a ``SemanticGap``.

    The name is constructed from ``DOMAIN_NAME_PREFIX`` and a short
    abbreviation of the gap's ID and severity level.

    Parameters
    ----------
    gap:
        The semantic gap for which a domain name is needed.

    Returns
    -------
    str
        A domain name string, e.g. ``'semantic-domain-ab12cd34-critical'``.
    """
    short_id = gap.gap_id.replace("gap-", "")[:8]
    severity_tag = gap.severity.name.lower()
    return f"{DOMAIN_NAME_PREFIX}-{short_id}-{severity_tag}"


# ---------------------------------------------------------------------------
# DomainFormationAnalyzer
# ---------------------------------------------------------------------------


class DomainFormationAnalyzer:
    """Analyses obstruction clusters to identify semantic gaps and proposals.

    ``DomainFormationAnalyzer`` encapsulates the gap-detection and
    proposal-synthesis logic.  It is used internally by
    ``DomainFormationCoordinator`` but may also be instantiated directly for
    unit testing or exploratory analysis.

    The analyzer maintains a list of ``registered_domains`` (passed in at
    construction or updated via ``update_registered_domains``) against which
    overlap checks are performed.

    Attributes
    ----------
    config : DomainFormationConfig
        Active configuration.
    _registered_domains : list of DomainRecord
        Snapshot of the currently registered domains, used for overlap checks.

    Examples
    --------
    ::

        analyzer = DomainFormationAnalyzer(config)
        gaps = analyzer.detect_gaps(clusters)
        proposals = [analyzer.build_proposal(g) for g in gaps]
    """

    def __init__(
        self,
        config: DomainFormationConfig,
        registered_domains: Optional[List[DomainRecord]] = None,
    ) -> None:
        """Initialise the analyzer.

        Parameters
        ----------
        config:
            Configuration controlling severity thresholds, overlap tolerance,
            and maximum proposal counts.
        registered_domains:
            Current snapshot of registered domains.  Defaults to an empty list
            if not provided.
        """
        self._config = config
        self._registered_domains: List[DomainRecord] = list(
            registered_domains or []
        )
        self._log = logging.getLogger(self.__class__.__qualname__)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update_registered_domains(self, domains: List[DomainRecord]) -> None:
        """Replace the analyzer's snapshot of registered domains.

        Parameters
        ----------
        domains:
            Updated list of registered ``DomainRecord`` objects.
        """
        self._registered_domains = list(domains)

    def detect_gaps(
        self, clusters: List[ObstructionCluster]
    ) -> List[SemanticGap]:
        """Identify semantic gaps from a list of obstruction clusters.

        Only clusters whose membership meets ``config.min_cluster_size`` and
        whose gap score meets ``config.gap_severity_threshold`` are retained.
        The returned list is sorted by descending gap score.

        Parameters
        ----------
        clusters:
            Obstruction clusters produced by an upstream clustering pass.

        Returns
        -------
        list of SemanticGap
            Identified semantic gaps, sorted by descending ``gap_score``.
        """
        gaps: List[SemanticGap] = []
        threshold_order = list(GapSeverity)
        min_severity_index = threshold_order.index(
            self._config.gap_severity_threshold
        )

        for cluster in clusters:
            # Skip under-populated clusters
            if len(cluster.member_ids) < self._config.min_cluster_size:
                self._log.debug(
                    "Skipping cluster %s: only %d members (min %d)",
                    cluster.cluster_id,
                    len(cluster.member_ids),
                    self._config.min_cluster_size,
                )
                continue

            score = _score_cluster_gap(cluster, self._config)
            if score < MIN_GAP_SCORE:
                continue

            severity = _severity_from_score(score)

            # Filter by configured severity threshold
            if threshold_order.index(severity) < min_severity_index:
                continue

            gap_id = _build_gap_id([cluster.cluster_id])
            # Collect candidate generators from cluster metadata or synthesise
            candidate_gens: List[str] = []
            if cluster.metadata and isinstance(cluster.metadata, dict):
                candidate_gens = list(
                    cluster.metadata.get("generators", [])
                )
            if not candidate_gens:
                candidate_gens = [
                    f"gen_{kind[:6]}_{cluster.cluster_id[:4]}"
                    for kind in cluster.obstruction_kinds
                ] or [f"gen_sigma_{cluster.cluster_id[:4]}"]

            description = (
                f"Semantic gap detected from cluster {cluster.cluster_id} "
                f"(score={score:.4f}, severity={severity.name}, "
                f"members={len(cluster.member_ids)}, "
                f"kinds={', '.join(cluster.obstruction_kinds)})."
            )

            gap = SemanticGap(
                gap_id=gap_id,
                source_clusters=(cluster.cluster_id,),
                gap_score=score,
                severity=severity,
                description=description,
                candidate_generators=tuple(candidate_gens),
                created_at=_utcnow_iso(),
            )
            gaps.append(gap)
            self._log.debug("Gap detected: %s score=%.4f", gap_id, score)

        gaps.sort(key=lambda g: g.gap_score, reverse=True)
        return gaps

    def build_proposal(self, gap: SemanticGap) -> DomainProposal:
        """Construct a ``DomainProposal`` from a ``SemanticGap``.

        Parameters
        ----------
        gap:
            The semantic gap to address.

        Returns
        -------
        DomainProposal
            A freshly constructed proposal in ``DomainStatus.PROPOSED`` state.
        """
        generators = list(gap.candidate_generators)[
            : self._config.max_domain_generators
        ]
        domain_name = _domain_name_from_gap(gap)
        proposal_id = _build_proposal_id(gap.gap_id, domain_name)

        axiom_sketches = _synthesize_axiom_sketches(generators, gap)

        # Derive relations as pairwise incidence constraints on generators
        relations: List[str] = []
        for g0, g1 in itertools.combinations(generators[:6], 2):
            relations.append(f"incidence({g0},{g1})")

        rationale = (
            f"Proposal {proposal_id} addresses gap {gap.gap_id} "
            f"(score={gap.gap_score:.4f}, severity={gap.severity.name}). "
            f"The gap was inferred from clusters {list(gap.source_clusters)} "
            f"and suggests a domain organised around generators "
            f"{generators[:3]} (and {max(0, len(generators)-3)} more)."
        )

        return DomainProposal(
            proposal_id=proposal_id,
            gap_id=gap.gap_id,
            domain_name=domain_name,
            generators=tuple(generators),
            relations=tuple(relations),
            axiom_sketches=tuple(axiom_sketches),
            rationale=rationale,
            status=DomainStatus.PROPOSED,
        )

    def check_viability(
        self,
        proposal: DomainProposal,
    ) -> ViabilityReport:
        """Assess the viability of a ``DomainProposal``.

        Viability starts at ``1.0`` and is reduced by
        ``VIABILITY_PENALTY_PER_ISSUE`` for each blocking issue found.
        Non-blocking concerns are collected as warnings.

        Parameters
        ----------
        proposal:
            The proposal to assess.

        Returns
        -------
        ViabilityReport
            Viability assessment including score, blocking issues, and
            warnings.
        """
        blocking: List[str] = []
        warnings: List[str] = []
        score = 1.0

        # Check 1: non-empty generator set
        if not proposal.generators:
            blocking.append("Proposal has no generators.")
            score -= VIABILITY_PENALTY_PER_ISSUE * 3

        # Check 2: generator count within bounds
        if len(proposal.generators) > self._config.max_domain_generators:
            blocking.append(
                f"Generator count {len(proposal.generators)} exceeds "
                f"max_domain_generators={self._config.max_domain_generators}."
            )
            score -= VIABILITY_PENALTY_PER_ISSUE

        # Check 3: at least one axiom sketch
        if not proposal.axiom_sketches:
            warnings.append("No axiom sketches; domain is axiom-free (unusual).")
            score -= VIABILITY_PENALTY_PER_ISSUE * 0.5

        # Check 4: rationale is non-trivial
        if len(proposal.rationale) < 20:
            warnings.append("Rationale is very short; consider elaborating.")

        # Bonus for prior coverage
        if len(proposal.generators) >= 2:
            score += PRIOR_COVERAGE_BONUS

        score = _clamp(score)
        is_viable = score >= self._config.viability_threshold and not blocking

        return ViabilityReport(
            proposal_id=proposal.proposal_id,
            viability_score=score,
            is_viable=is_viable,
            blocking_issues=tuple(blocking),
            warnings=tuple(warnings),
        )

    def check_overlap(
        self,
        proposal: DomainProposal,
    ) -> OverlapReport:
        """Check a proposal's generator set for overlap with existing domains.

        Parameters
        ----------
        proposal:
            The proposal to check.

        Returns
        -------
        OverlapReport
            Overlap assessment against all currently registered domains.
        """
        overlapping_ids: List[str] = []
        max_frac = 0.0

        for record in self._registered_domains:
            frac = _compute_overlap_fraction(
                proposal.generators, record.generators
            )
            if frac > 0.0:
                overlapping_ids.append(record.record_id)
            if frac > max_frac:
                max_frac = frac

        is_acceptable = max_frac < self._config.overlap_tolerance

        return OverlapReport(
            new_domain_id=proposal.proposal_id,
            overlapping_domain_ids=tuple(overlapping_ids),
            max_overlap_fraction=max_frac,
            is_acceptable=is_acceptable,
        )


# ---------------------------------------------------------------------------
# DomainFormationWitness
# ---------------------------------------------------------------------------


class DomainFormationWitness:
    """Emits and stores audit witness reports for the domain formation pipeline.

    Every significant event in the pipeline — gap detection, proposal creation,
    domain registration — is recorded as an immutable witness report.  The
    witness maintains an ordered log of reports that can be queried for
    debugging or compliance purposes.

    Attributes
    ----------
    _gap_reports : list of GapWitnessReport
        Accumulated gap-detection witness reports.
    _proposal_reports : list of ProposalWitnessReport
        Accumulated proposal witness reports.
    _registration_reports : list of RegistrationWitnessReport
        Accumulated registration witness reports.

    Examples
    --------
    ::

        witness = DomainFormationWitness()
        witness.record_gap_detection(clusters, gaps)
        witness.record_proposal(proposal, viability_report)
        witness.record_registration(record)
        print(witness.summary())
    """

    def __init__(self) -> None:
        """Initialise the witness with empty report logs."""
        self._gap_reports: List[GapWitnessReport] = []
        self._proposal_reports: List[ProposalWitnessReport] = []
        self._registration_reports: List[RegistrationWitnessReport] = []
        self._log = logging.getLogger(self.__class__.__qualname__)

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_gap_detection(
        self,
        clusters: List[ObstructionCluster],
        gaps: List[SemanticGap],
        notes: str = "",
    ) -> GapWitnessReport:
        """Record the outcome of a gap-detection pass.

        Parameters
        ----------
        clusters:
            The clusters that were examined.
        gaps:
            The gaps that were identified.
        notes:
            Optional free-text commentary.

        Returns
        -------
        GapWitnessReport
            The newly created witness report.
        """
        dist: Dict[str, int] = defaultdict(int)
        for gap in gaps:
            dist[gap.severity.name] += 1

        report = GapWitnessReport(
            witness_id=_uid(),
            cluster_count=len(clusters),
            gap_count=len(gaps),
            severity_distribution=dict(dist),
            timestamp=_utcnow_iso(),
            notes=notes or f"Examined {len(clusters)} clusters; found {len(gaps)} gaps.",
        )
        self._gap_reports.append(report)
        self._log.debug(
            "Gap witness: %d clusters → %d gaps", len(clusters), len(gaps)
        )
        return report

    def record_proposal(
        self,
        proposal: DomainProposal,
        viability: ViabilityReport,
    ) -> ProposalWitnessReport:
        """Record the creation and viability check of a proposal.

        Parameters
        ----------
        proposal:
            The domain proposal that was created.
        viability:
            The viability report for the proposal.

        Returns
        -------
        ProposalWitnessReport
            The newly created witness report.
        """
        report = ProposalWitnessReport(
            witness_id=_uid(),
            proposal_id=proposal.proposal_id,
            domain_name=proposal.domain_name,
            generator_count=len(proposal.generators),
            is_viable=viability.is_viable,
            timestamp=_utcnow_iso(),
        )
        self._proposal_reports.append(report)
        self._log.debug(
            "Proposal witness: %s viable=%s",
            proposal.proposal_id,
            viability.is_viable,
        )
        return report

    def record_registration(
        self, record: DomainRecord
    ) -> RegistrationWitnessReport:
        """Record the registration of a new domain.

        Parameters
        ----------
        record:
            The ``DomainRecord`` that was registered.

        Returns
        -------
        RegistrationWitnessReport
            The newly created witness report.
        """
        report = RegistrationWitnessReport(
            witness_id=_uid(),
            record_id=record.record_id,
            domain_name=record.domain_name,
            status=record.status,
            timestamp=_utcnow_iso(),
        )
        self._registration_reports.append(report)
        self._log.info(
            "Registration witness: domain '%s' (record_id=%s)",
            record.domain_name,
            record.record_id,
        )
        return report

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict of all recorded witness events.

        Returns
        -------
        dict
            Keys: ``'gap_passes'``, ``'proposals'``, ``'registrations'``,
            ``'total_gaps_found'``, ``'total_viable_proposals'``.
        """
        total_gaps = sum(r.gap_count for r in self._gap_reports)
        viable = sum(1 for r in self._proposal_reports if r.is_viable)
        return {
            "gap_passes": len(self._gap_reports),
            "proposals": len(self._proposal_reports),
            "registrations": len(self._registration_reports),
            "total_gaps_found": total_gaps,
            "total_viable_proposals": viable,
        }

    @property
    def gap_reports(self) -> List[GapWitnessReport]:
        """Read-only view of accumulated gap-detection reports."""
        return list(self._gap_reports)

    @property
    def proposal_reports(self) -> List[ProposalWitnessReport]:
        """Read-only view of accumulated proposal reports."""
        return list(self._proposal_reports)

    @property
    def registration_reports(self) -> List[RegistrationWitnessReport]:
        """Read-only view of accumulated registration reports."""
        return list(self._registration_reports)


# ---------------------------------------------------------------------------
# DomainFormationCoordinator
# ---------------------------------------------------------------------------


class DomainFormationCoordinator:
    """Orchestrates the full domain formation decision pipeline.

    ``DomainFormationCoordinator`` is the top-level entry point for the
    pipeline described in theory2.tex Ch59.  It holds the configuration, the
    internal analyzer, the witness, and the growing registry of registered
    domains.  Callers that want a lightweight façade should use the
    module-level ``run_domain_formation_cycle`` function instead.

    Lifecycle of a single cycle (``run_domain_formation_cycle``)::

        clusters → analyze_semantic_gaps → [SemanticGap, ...]
                 → propose_new_domain    → [DomainProposal, ...]
                 → validate_domain_proposal → [DomainValidationResult, ...]
                 → register_domain       → [DomainRecord, ...]
                 → DomainFormationResult

    Attributes
    ----------
    _config : DomainFormationConfig
        Active configuration (immutable after construction).
    _analyzer : DomainFormationAnalyzer
        Helper that performs gap detection, proposal building, and checks.
    _witness : DomainFormationWitness
        Audit witness that records every pipeline event.
    _registered_domains : list of DomainRecord
        Registry of all domains registered during this coordinator's lifetime.

    Examples
    --------
    ::

        config = DomainFormationConfig(viability_threshold=0.70)
        coord = DomainFormationCoordinator(config)
        result = coord.run_domain_formation_cycle(clusters)
        print(result.domains_registered)
    """

    def __init__(self, config: DomainFormationConfig) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        config:
            Immutable configuration for the pipeline.
        """
        self._config = config
        self._analyzer = DomainFormationAnalyzer(config)
        self._witness = DomainFormationWitness()
        self._registered_domains: List[DomainRecord] = []
        self._log = logging.getLogger(self.__class__.__qualname__)

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def analyze_semantic_gaps(
        self, obstruction_clusters: List[ObstructionCluster]
    ) -> List[SemanticGap]:
        """Identify semantic gaps from a list of obstruction clusters.

        For each cluster whose membership meets the configured minimum size
        and whose gap score meets the configured severity threshold, a
        ``SemanticGap`` record is created.  Clusters are sorted by descending
        severity score before processing so that the most critical gaps are
        surfaced first.

        Parameters
        ----------
        obstruction_clusters:
            Clusters of obstruction records produced by an upstream analysis
            pass.

        Returns
        -------
        list of SemanticGap
            Identified gaps, sorted by descending ``gap_score``.
        """
        sorted_clusters = sorted(
            obstruction_clusters,
            key=lambda c: c.severity_score,
            reverse=True,
        )
        self._analyzer.update_registered_domains(self._registered_domains)
        gaps = self._analyzer.detect_gaps(sorted_clusters)
        self._witness.record_gap_detection(
            sorted_clusters, gaps, notes=f"cycle analyze pass"
        )
        self._log.info(
            "analyze_semantic_gaps: %d clusters → %d gaps",
            len(sorted_clusters),
            len(gaps),
        )
        return gaps

    def propose_new_domain(self, gap: SemanticGap) -> DomainProposal:
        """Construct a ``DomainProposal`` from a ``SemanticGap``.

        The proposal derives candidate generators from the gap's
        ``candidate_generators`` tuple, synthesises axiom sketches via
        ``_synthesize_axiom_sketches``, and assigns an initial
        ``DomainStatus`` of ``PROPOSED``.

        Parameters
        ----------
        gap:
            The semantic gap for which a domain proposal should be built.

        Returns
        -------
        DomainProposal
            A freshly constructed proposal.
        """
        proposal = self._analyzer.build_proposal(gap)
        viability = self._analyzer.check_viability(proposal)
        self._witness.record_proposal(proposal, viability)
        self._log.info(
            "propose_new_domain: gap=%s → proposal=%s (viable=%s)",
            gap.gap_id,
            proposal.proposal_id,
            viability.is_viable,
        )
        return proposal

    def validate_domain_proposal(
        self, proposal: DomainProposal
    ) -> DomainValidationResult:
        """Validate a ``DomainProposal`` prior to registration.

        Validation checks performed:

        1. Generator set is non-empty and within ``max_domain_generators``.
        2. Viability score exceeds ``viability_threshold``.
        3. Overlap with existing domains is below ``overlap_tolerance``
           (unless ``enable_overlap_check=False``).

        Parameters
        ----------
        proposal:
            The proposal to validate.

        Returns
        -------
        DomainValidationResult
            Full validation outcome including issues and recommendations.
        """
        issues: List[str] = []
        recommendations: List[str] = []

        # Viability sub-check
        viability = self._analyzer.check_viability(proposal)
        issues.extend(viability.blocking_issues)
        recommendations.extend(
            [f"(warning) {w}" for w in viability.warnings]
        )

        # Overlap sub-check
        if self._config.enable_overlap_check:
            overlap = self._analyzer.check_overlap(proposal)
            if not overlap.is_acceptable:
                issues.append(
                    f"Overlap fraction {overlap.max_overlap_fraction:.3f} "
                    f"exceeds tolerance {self._config.overlap_tolerance:.3f} "
                    f"with domains {list(overlap.overlapping_domain_ids)[:3]}."
                )
                recommendations.append(
                    "Consider differentiating generators to reduce overlap, "
                    "or raising overlap_tolerance if the overlap is intentional."
                )

        # Compute overall validation score
        base_score = viability.viability_score
        overlap_penalty = 0.0
        if self._config.enable_overlap_check:
            ov = self._analyzer.check_overlap(proposal)
            overlap_penalty = ov.max_overlap_fraction * 0.3
        final_score = _clamp(base_score - overlap_penalty)

        is_valid = not issues and final_score >= self._config.viability_threshold

        if not proposal.generators:
            recommendations.append("Add at least one generator to the proposal.")

        return DomainValidationResult(
            proposal_id=proposal.proposal_id,
            is_valid=is_valid,
            score=final_score,
            issues=tuple(issues),
            recommendations=tuple(recommendations),
        )

    def register_domain(self, proposal: DomainProposal) -> DomainRecord:
        """Register a validated proposal as a new ``DomainRecord``.

        The domain status is set to ``DomainStatus.PROVISIONAL`` to indicate
        that it is registered but still subject to review.  The record is
        appended to the internal registry and a ``RegistrationWitnessReport``
        is emitted.

        Parameters
        ----------
        proposal:
            A proposal that has passed ``validate_domain_proposal``.

        Returns
        -------
        DomainRecord
            The newly registered domain record.
        """
        record_id = _build_record_id(proposal.proposal_id)
        record = DomainRecord(
            record_id=record_id,
            domain_name=proposal.domain_name,
            generators=proposal.generators,
            relations=proposal.relations,
            status=DomainStatus.PROVISIONAL,
            registered_at=_utcnow_iso(),
            metadata={"source_gap_id": proposal.gap_id,
                      "axiom_sketches": proposal.axiom_sketches},
        )
        self._registered_domains.append(record)
        self._witness.record_registration(record)
        self._log.info(
            "register_domain: '%s' (record_id=%s)", record.domain_name, record_id
        )
        return record

    def run_domain_formation_cycle(
        self, clusters: List[ObstructionCluster]
    ) -> DomainFormationResult:
        """Execute a complete domain formation cycle.

        Runs all pipeline stages in sequence:
        ``analyze_semantic_gaps → propose_new_domain → validate_domain_proposal
        → register_domain``.  At most ``config.max_proposals_per_cycle``
        proposals are made per cycle.

        Parameters
        ----------
        clusters:
            Raw obstruction clusters for the cycle.

        Returns
        -------
        DomainFormationResult
            Summary of the cycle including counts and registered record IDs.
        """
        import time

        cycle_id = _uid()
        t0 = time.monotonic()
        self._log.info("Starting domain formation cycle %s", cycle_id)

        gaps = self.analyze_semantic_gaps(clusters)

        proposals: List[DomainProposal] = []
        records: List[DomainRecord] = []

        for gap in gaps[: self._config.max_proposals_per_cycle]:
            proposal = self.propose_new_domain(gap)
            validation = self.validate_domain_proposal(proposal)
            if validation.is_valid:
                record = self.register_domain(proposal)
                records.append(record)
            proposals.append(proposal)

        duration = time.monotonic() - t0
        self._log.info(
            "Cycle %s complete: %d gaps, %d proposals, %d registered (%.3fs)",
            cycle_id,
            len(gaps),
            len(proposals),
            len(records),
            duration,
        )
        return DomainFormationResult(
            cycle_id=cycle_id,
            gaps_found=len(gaps),
            proposals_made=len(proposals),
            domains_registered=len(records),
            records=tuple(r.record_id for r in records),
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def registered_domains(self) -> List[DomainRecord]:
        """Read-only copy of all currently registered domain records."""
        return list(self._registered_domains)

    @property
    def witness(self) -> DomainFormationWitness:
        """The coordinator's audit witness."""
        return self._witness


# ---------------------------------------------------------------------------
# Module-level free functions (public API façade)
# ---------------------------------------------------------------------------


def run_domain_formation_cycle(
    clusters: List[ObstructionCluster],
    config: Optional[DomainFormationConfig] = None,
) -> DomainFormationResult:
    """Module-level convenience wrapper around ``DomainFormationCoordinator``.

    Instantiates a fresh coordinator with *config* (or the default config if
    ``None``) and immediately runs a single formation cycle.

    Parameters
    ----------
    clusters:
        Obstruction clusters to process.
    config:
        Configuration to use.  Defaults to ``DomainFormationConfig()`` if
        ``None``.

    Returns
    -------
    DomainFormationResult
        Cycle summary.
    """
    cfg = config if config is not None else DomainFormationConfig()
    coordinator = DomainFormationCoordinator(cfg)
    return coordinator.run_domain_formation_cycle(clusters)


def score_semantic_gap(
    cluster: ObstructionCluster,
    config: Optional[DomainFormationConfig] = None,
) -> float:
    """Compute the semantic gap score for a single obstruction cluster.

    A thin wrapper around ``_score_cluster_gap`` for callers that do not need
    the full coordinator machinery.

    Parameters
    ----------
    cluster:
        The cluster to score.
    config:
        Configuration controlling the scoring formula.  Defaults to
        ``DomainFormationConfig()`` if ``None``.

    Returns
    -------
    float
        Gap score in ``[0.0, 1.0]``.
    """
    cfg = config if config is not None else DomainFormationConfig()
    return _score_cluster_gap(cluster, cfg)


def select_gap_for_formation(
    gaps: List[SemanticGap],
    config: Optional[DomainFormationConfig] = None,
) -> Optional[SemanticGap]:
    """Select the highest-priority gap from a list for domain formation.

    Returns the gap with the highest ``gap_score`` that meets or exceeds the
    configured ``gap_severity_threshold``.  Returns ``None`` if no qualifying
    gap is found.

    Parameters
    ----------
    gaps:
        Candidate gaps to evaluate.
    config:
        Configuration controlling the severity threshold.  Defaults to
        ``DomainFormationConfig()`` if ``None``.

    Returns
    -------
    SemanticGap or None
        The highest-scoring qualifying gap, or ``None``.
    """
    cfg = config if config is not None else DomainFormationConfig()
    threshold_order = list(GapSeverity)
    min_index = threshold_order.index(cfg.gap_severity_threshold)

    qualifying = [
        g for g in gaps
        if threshold_order.index(g.severity) >= min_index
    ]
    if not qualifying:
        return None
    return max(qualifying, key=lambda g: g.gap_score)


# ---------------------------------------------------------------------------
# Smoke test / interactive demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    print("=" * 70)
    print("Domain Formation — When the Right Discovery Is a New Semantic Region")
    print("theory2.tex Ch59 smoke test")
    print("=" * 70)

    # --- Build realistic sample clusters -----------------------------------
    sample_clusters = [
        ObstructionCluster(
            cluster_id="cl-alpha",
            centroid=(1.2, 0.8),
            member_ids=("obs-001", "obs-002", "obs-003", "obs-004", "obs-005"),
            obstruction_kinds=("topological", "cohomological"),
            severity_score=0.82,
            metadata={"generators": ["motif_H1", "motif_H2", "cycle_class"]},
        ),
        ObstructionCluster(
            cluster_id="cl-beta",
            centroid=(0.4, 1.6),
            member_ids=("obs-010", "obs-011", "obs-012"),
            obstruction_kinds=("algebraic",),
            severity_score=0.54,
            metadata={"generators": ["derived_obj", "t_structure"]},
        ),
        ObstructionCluster(
            cluster_id="cl-gamma",
            centroid=(0.1, 0.1),
            member_ids=("obs-020", "obs-021"),
            obstruction_kinds=("geometric",),
            severity_score=0.18,
        ),
        ObstructionCluster(
            cluster_id="cl-delta",
            centroid=(2.0, 2.0),
            member_ids=("obs-030", "obs-031", "obs-032", "obs-033"),
            obstruction_kinds=("topological", "algebraic", "cohomological"),
            severity_score=0.91,
            metadata={"generators": ["perfectoid_tilt", "almost_math", "witt_vec"]},
        ),
    ]

    # --- Score individual gaps ---------------------------------------------
    print("\n--- Individual gap scores ---")
    cfg = DomainFormationConfig(min_cluster_size=3, gap_severity_threshold=GapSeverity.MODERATE)
    for cl in sample_clusters:
        s = score_semantic_gap(cl, cfg)
        sev = _severity_from_score(s)
        print(f"  {cl.cluster_id}: score={s:.4f}  severity={sev.name}")

    # --- Full formation cycle ----------------------------------------------
    print("\n--- Running formation cycle ---")
    result = run_domain_formation_cycle(sample_clusters, config=cfg)
    print(f"  cycle_id          : {result.cycle_id}")
    print(f"  gaps_found        : {result.gaps_found}")
    print(f"  proposals_made    : {result.proposals_made}")
    print(f"  domains_registered: {result.domains_registered}")
    print(f"  record_ids        : {list(result.records)}")
    print(f"  duration_seconds  : {result.duration_seconds:.4f}s")

    # --- Coordinator with direct access ------------------------------------
    print("\n--- Coordinator internals ---")
    coord = DomainFormationCoordinator(cfg)
    gaps = coord.analyze_semantic_gaps(sample_clusters)
    print(f"  Gaps detected: {len(gaps)}")
    for gap in gaps:
        print(
            f"    {gap.gap_id}  score={gap.gap_score:.4f}"
            f"  severity={gap.severity.name}"
            f"  generators={list(gap.candidate_generators)[:2]}…"
        )

    if gaps:
        top_gap = gaps[0]
        proposal = coord.propose_new_domain(top_gap)
        print(f"\n  Top proposal: {proposal.proposal_id}")
        print(f"    domain_name   : {proposal.domain_name}")
        print(f"    generators    : {list(proposal.generators)}")
        print(f"    axiom_sketches: {list(proposal.axiom_sketches)[:2]}…")

        validation = coord.validate_domain_proposal(proposal)
        print(f"\n  Validation: is_valid={validation.is_valid}  score={validation.score:.4f}")
        if validation.issues:
            print(f"    issues: {list(validation.issues)}")
        if validation.recommendations:
            print(f"    recommendations: {list(validation.recommendations)[:2]}")

        if validation.is_valid:
            record = coord.register_domain(proposal)
            print(f"\n  Registered: {record.record_id}  status={record.status.name}")

    # --- select_gap_for_formation ------------------------------------------
    print("\n--- select_gap_for_formation ---")
    selected = select_gap_for_formation(
        gaps, config=DomainFormationConfig(gap_severity_threshold=GapSeverity.SIGNIFICANT)
    )
    if selected:
        print(f"  Selected gap: {selected.gap_id}  score={selected.gap_score:.4f}")
    else:
        print("  No qualifying gap found.")

    # --- Witness summary ---------------------------------------------------
    print("\n--- Witness summary ---")
    summary = coord.witness.summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\nSmoke test complete.")
