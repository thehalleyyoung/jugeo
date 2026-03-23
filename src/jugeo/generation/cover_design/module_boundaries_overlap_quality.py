"""
Module boundary quality — measuring and optimizing overlap between cover elements.

This module provides the core machinery for analysing the quality of boundaries
between adjacent modules in a cover design, quantifying how well neighbouring
sections overlap, and iteratively optimizing those boundaries so that the final
cover satisfies coherence and alignment requirements.

The analysis pipeline runs in three stages:
  1. Boundary extraction – identify the geometric region where two modules share
     content (``analyze_module_boundary``).
  2. Quality measurement – compute a multi-metric score for each boundary
     (``measure_overlap_quality``).
  3. Optimisation – iteratively adjust boundaries to maximise overall quality
     (``optimize_boundaries``).

All domain objects are frozen dataclasses so that they can be safely cached and
shared across concurrent workers without defensive copying.

# copilot: generated as part of the cover-design generation subsystem; keep
# boundary logic decoupled from rendering concerns.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"
        DESCENT_OBSTRUCTION = "descent_obstruction"
        UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError):  # type: ignore[no-redef]
        pass

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message

    def raise_with_scope(  # type: ignore[misc]
        code: str,
        *,
        message: str,
        provenance: Any = None,
        **kw: Any,
    ) -> None:
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """Ordered confidence levels attached to computed boundary judgments.

    Each tier represents a distinct epistemic status, from a raw unreviewed
    proposal through to a fully solver-backed proof.  Tiers support lattice
    operations (``join`` / ``meet``) and monotone transitions (``promote`` /
    ``demote``) so that downstream consumers can track how confidence evolves
    as additional evidence is gathered.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: "TrustTier") -> "TrustTier":
        """Return the least upper bound of *self* and *other* in the trust lattice."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Return the greatest lower bound of *self* and *other* in the trust lattice."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> "TrustTier":
        """Advance one tier upward, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, 5))

    def demote(self) -> "TrustTier":
        """Retreat one tier downward, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, 1))


# ---------------------------------------------------------------------------
# Shared dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """An immutable record pairing a proposition with a trust assessment.

    A ``Judgment`` captures both *what* is believed (``proposition``) and
    *how confidently* it is believed (``tier``).  The optional ``evidence``
    field may hold a serialisable summary of the artefact that raised the
    tier (e.g. a solver certificate or a runtime sample).

    Judgments are frozen so they can be used as dictionary keys or stored in
    sets, and to prevent accidental mutation in concurrent pipelines.
    """

    judgment_id: str
    proposition: str
    tier: TrustTier
    kind: PropositionKind = PropositionKind.STRUCTURAL
    evidence: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def with_tier(self, new_tier: TrustTier) -> "Judgment":
        """Return a copy of this judgment with *new_tier* substituted."""
        return replace(self, tier=new_tier)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding."""
        return {
            "judgment_id": self.judgment_id,
            "proposition": self.proposition,
            "tier": self.tier.name,
            "kind": self.kind.value,
            "evidence": self.evidence,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CechObstruction:
    """A Čech-complex obstruction certificate for a failed boundary coherence check.

    When a boundary fails coherence verification the obstruction is recorded in
    a ``CechObstruction`` so that the optimiser can later inspect the nature of
    the failure and decide whether to attempt a repair.

    The *cycle_key* field encodes the homological cycle (as a stable hash)
    that witnesses the failure, allowing duplicate obstructions to be
    deduplicated across optimisation rounds.
    """

    obstruction_id: str
    boundary_id: str
    cycle_key: str
    description: str
    scope: FailureScope = FailureScope.GEOMETRY
    classification: FailureClassification = FailureClassification.UNCLASSIFIED
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding."""
        return {
            "obstruction_id": self.obstruction_id,
            "boundary_id": self.boundary_id,
            "cycle_key": self.cycle_key,
            "description": self.description,
            "scope": self.scope.value,
            "classification": self.classification.value,
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------


class BoundaryType(str, Enum):
    """Semantic category of the transition between two adjacent modules.

    HARD boundaries represent strict cuts where the two modules share no
    content; SOFT boundaries allow a smooth blend region; OVERLAP boundaries
    have a measurable extent of shared content that is intentional by design.
    """

    HARD = "hard"
    SOFT = "soft"
    OVERLAP = "overlap"
    VIRTUAL = "virtual"


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleBoundary:
    """Geometric descriptor for the boundary between two adjacent cover modules.

    The boundary is defined by two scalar positions *overlap_start* and
    *overlap_end* along a shared axis (normalised to [0, 1]).  The
    ``quality_score`` field summarises the overall fitness of this boundary
    as a single float in [0, 1], where 1.0 is a perfect transition.

    All positional fields are floats so that sub-pixel precision is preserved
    when operating on high-resolution cover canvases.
    """

    boundary_id: str
    left_module_id: str
    right_module_id: str
    overlap_start: float
    overlap_end: float
    boundary_type: BoundaryType = BoundaryType.SOFT
    quality_score: float = 0.0

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def overlap_size(self) -> float:
        """Return the absolute size of the overlap region.

        The size is the distance between *overlap_end* and *overlap_start*.
        Returns 0.0 when the positions are equal (degenerate boundary) and
        raises ``JuGeoError`` when the start exceeds the end.
        """
        if self.overlap_end < self.overlap_start:
            raise_with_scope(
                "BOUNDARY_INVERTED",
                message=(
                    f"Boundary {self.boundary_id!r}: overlap_start "
                    f"({self.overlap_start}) > overlap_end ({self.overlap_end})"
                ),
            )
        return self.overlap_end - self.overlap_start

    def is_valid(self) -> bool:
        """Return True iff the boundary positions are geometrically consistent.

        Validity requires that 0 <= overlap_start <= overlap_end <= 1 and that
        the quality score is a finite float in [0, 1].
        """
        return (
            0.0 <= self.overlap_start <= self.overlap_end <= 1.0
            and math.isfinite(self.quality_score)
            and 0.0 <= self.quality_score <= 1.0
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the boundary to a JSON-compatible dictionary.

        The ``boundary_type`` enum is converted to its string value so that
        the result is directly serialisable by the standard ``json`` module.
        """
        return {
            "boundary_id": self.boundary_id,
            "left_module_id": self.left_module_id,
            "right_module_id": self.right_module_id,
            "overlap_start": self.overlap_start,
            "overlap_end": self.overlap_end,
            "boundary_type": self.boundary_type.value,
            "quality_score": self.quality_score,
        }

    def is_tight(self) -> bool:
        """Return True when the overlap region is smaller than 5 % of the axis.

        A tight boundary is one where the transition is very abrupt; this may
        indicate that the two modules need more blending area to look natural.
        """
        return self.overlap_size() < 0.05

    def is_loose(self) -> bool:
        """Return True when the overlap region exceeds 30 % of the axis.

        A loose boundary risks making the two modules look merged rather than
        distinct; the optimiser may need to tighten such boundaries.
        """
        return self.overlap_size() > 0.30


@dataclass(frozen=True)
class OverlapQuality:
    """Multi-dimensional quality assessment for a single module boundary overlap.

    Each field captures one facet of boundary quality:
    - ``jaccard_index``: set-theoretic overlap of the two modules' content sets.
    - ``boundary_coherence``: how smoothly the visual transition appears.
    - ``size_ratio``: ratio of overlap size to the smaller module's extent.
    - ``alignment_score``: positional alignment of corresponding landmarks.
    - ``overall``: weighted combination of all facets in [0, 1].
    """

    jaccard_index: float
    boundary_coherence: float
    size_ratio: float
    alignment_score: float
    overall: float

    def is_acceptable(self, threshold: float = 0.6) -> bool:
        """Return True when the overall quality meets *threshold*.

        Uses the ``overall`` field as the single gate.  The default threshold
        of 0.6 is derived from empirical cover-quality studies conducted
        during the design-system review.
        """
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be in [0, 1], got {threshold!r}")
        return self.overall >= threshold

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation of all quality facets."""
        return {
            "jaccard_index": self.jaccard_index,
            "boundary_coherence": self.boundary_coherence,
            "size_ratio": self.size_ratio,
            "alignment_score": self.alignment_score,
            "overall": self.overall,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OverlapQuality":
        """Deserialise an ``OverlapQuality`` previously produced by ``to_dict``.

        Unknown keys in *data* are silently ignored, which allows forward-
        compatible deserialisation when new facets are added in later versions.
        """
        return cls(
            jaccard_index=float(data["jaccard_index"]),
            boundary_coherence=float(data["boundary_coherence"]),
            size_ratio=float(data["size_ratio"]),
            alignment_score=float(data["alignment_score"]),
            overall=float(data["overall"]),
        )


@dataclass(frozen=True)
class BoundaryAnalysis:
    """Aggregate result of running the full boundary-analysis pipeline on a cover.

    A ``BoundaryAnalysis`` holds the complete set of ``ModuleBoundary`` objects
    extracted from a cover along with per-boundary quality metrics and a
    timestamp recording when the analysis was performed.  It is the primary
    artefact persisted to the cover-design store after each analysis run.
    """

    analysis_id: str
    cover_id: str
    boundaries: Tuple[ModuleBoundary, ...]
    metrics: Dict[str, float]
    timestamp: float = field(default_factory=time.time)

    def worst_boundary(self) -> Optional[ModuleBoundary]:
        """Return the boundary with the lowest ``quality_score``, or None.

        When multiple boundaries share the minimum score the first one in the
        tuple ordering is returned, which corresponds to the leftmost boundary
        in the cover layout.
        """
        if not self.boundaries:
            return None
        return min(self.boundaries, key=lambda b: b.quality_score)

    def best_boundary(self) -> Optional[ModuleBoundary]:
        """Return the boundary with the highest ``quality_score``, or None.

        This is the complement of ``worst_boundary`` and is useful for
        identifying exemplary boundaries that can serve as templates during
        the optimisation phase.
        """
        if not self.boundaries:
            return None
        return max(self.boundaries, key=lambda b: b.quality_score)

    def average_quality(self) -> float:
        """Compute the arithmetic mean quality score across all boundaries.

        Returns 0.0 for an analysis with no boundaries rather than raising
        ZeroDivisionError, so that callers do not need to guard against empty
        covers.
        """
        if not self.boundaries:
            return 0.0
        return sum(b.quality_score for b in self.boundaries) / len(self.boundaries)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the full analysis result to a JSON-compatible dictionary.

        The ``boundaries`` tuple is converted to a list of per-boundary dicts
        so that the result can be passed directly to ``json.dumps``.
        """
        return {
            "analysis_id": self.analysis_id,
            "cover_id": self.cover_id,
            "boundaries": [b.to_dict() for b in self.boundaries],
            "metrics": dict(self.metrics),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class CoverOverlapMetric:
    """A single named metric used to evaluate cover-module overlap quality.

    Metrics are composable: a ``BoundaryAnalysis`` run may apply an ordered
    list of ``CoverOverlapMetric`` instances, each contributing a weighted
    partial score to the final ``OverlapQuality.overall`` value.

    The ``weight`` field is a non-negative float; metrics with weight 0.0 are
    retained for logging purposes but do not affect the aggregate score.
    """

    metric_id: str
    metric_name: str
    weight: float = 1.0

    def compute(self, left: Dict[str, Any], right: Dict[str, Any]) -> float:
        """Compute the raw metric value for the pair of section descriptors.

        The default implementation computes a simple positional coherence
        score based on the Euclidean distance between the centre-of-mass
        estimates embedded in *left* and *right*.  Subclasses or replacement
        callables should override this method to supply domain-specific logic.
        """
        def _com(section: Dict[str, Any]) -> Tuple[float, float]:
            x = float(section.get("x", 0.0)) + float(section.get("width", 0.0)) / 2.0
            y = float(section.get("y", 0.0)) + float(section.get("height", 0.0)) / 2.0
            return x, y

        lx, ly = _com(left)
        rx, ry = _com(right)
        distance = math.hypot(rx - lx, ry - ly)
        # Map distance in [0, 2] (diagonal of unit square) to score in [0, 1].
        raw = 1.0 - min(distance / math.sqrt(2.0), 1.0)
        return raw

    def normalize(self, raw: float) -> float:
        """Clip *raw* to [0, 1] and scale by ``weight``.

        The returned value is in [0, weight] so that the caller can sum metric
        contributions and divide by the total weight to obtain a unit score.
        Values outside [0, 1] are silently clamped rather than raising an
        error, because floating-point arithmetic can produce small excursions.
        """
        clamped = max(0.0, min(raw, 1.0))
        return clamped * self.weight

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation of the metric descriptor."""
        return {
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class BoundaryOptimizer:
    """Iterative optimiser that refines cover-module boundaries for quality.

    The optimiser maintains an immutable ``history`` of past boundary tuples
    so that convergence can be assessed without mutating state.  Each call to
    ``step`` returns a *new* ``BoundaryOptimizer`` with the updated history
    appended, preserving the frozen-dataclass invariant.

    ``max_iterations`` caps the total number of refinement rounds, and
    ``tolerance`` is the minimum improvement in average quality required to
    continue iterating.
    """

    optimizer_id: str
    max_iterations: int = 50
    tolerance: float = 1e-4
    history: Tuple[Tuple[ModuleBoundary, ...], ...] = field(default_factory=tuple)

    def step(
        self,
        boundaries: Tuple[ModuleBoundary, ...],
    ) -> "BoundaryOptimizer":
        """Append *boundaries* to the history and return the updated optimiser.

        This method does not perform any actual boundary adjustment; it records
        the current state so that ``converged`` can inspect the quality
        trajectory.  The actual adjustment logic lives in ``optimize``.
        """
        new_history = self.history + (boundaries,)
        return replace(self, history=new_history)

    def optimize(
        self,
        cover: List[Dict[str, Any]],
    ) -> Tuple["BoundaryOptimizer", List[ModuleBoundary]]:
        """Run the full iterative optimisation loop over *cover*.

        For each pair of adjacent sections in *cover* an initial boundary is
        synthesised and then refined over up to ``max_iterations`` rounds.
        The loop terminates early when the improvement in average quality
        falls below ``tolerance``.

        Returns the updated optimiser (with full history) and the final list
        of optimised boundaries.
        """
        if len(cover) < 2:
            log.warning(
                "optimizer=%s: cover has fewer than 2 sections; nothing to optimise",
                self.optimizer_id,
            )
            return self, []

        log.info(
            "optimizer=%s: starting optimisation over %d sections",
            self.optimizer_id,
            len(cover),
        )

        # Build initial boundaries between consecutive sections.
        current_boundaries: List[ModuleBoundary] = []
        for i, (left, right) in enumerate(itertools.pairwise(cover)):
            boundary = _synthesise_boundary(left, right, index=i)
            current_boundaries.append(boundary)

        optimizer = self
        prev_avg = _average_quality(current_boundaries)

        for iteration in range(self.max_iterations):
            refined: List[ModuleBoundary] = []
            for boundary in current_boundaries:
                improved = _refine_boundary(boundary)
                refined.append(improved)

            new_avg = _average_quality(refined)
            improvement = new_avg - prev_avg
            log.debug(
                "optimizer=%s iteration=%d avg_quality=%.4f improvement=%.6f",
                self.optimizer_id,
                iteration,
                new_avg,
                improvement,
            )

            optimizer = optimizer.step(tuple(refined))
            current_boundaries = refined

            if improvement < self.tolerance:
                log.info(
                    "optimizer=%s: converged at iteration %d (improvement=%.6f < tol=%.6f)",
                    self.optimizer_id,
                    iteration,
                    improvement,
                    self.tolerance,
                )
                break

            prev_avg = new_avg

        return optimizer, current_boundaries

    def converged(self) -> bool:
        """Return True when the last two history steps show negligible improvement.

        Requires at least two history entries; returns False when the history
        is too short to assess convergence.
        """
        if len(self.history) < 2:
            return False
        prev_avg = _average_quality(list(self.history[-2]))
        curr_avg = _average_quality(list(self.history[-1]))
        return abs(curr_avg - prev_avg) < self.tolerance

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the optimiser state, including a condensed history summary.

        Full history is not embedded verbatim to keep the serialised form
        compact; instead only the average quality at each step is recorded.
        """
        history_summary = [
            _average_quality(list(step)) for step in self.history
        ]
        return {
            "optimizer_id": self.optimizer_id,
            "max_iterations": self.max_iterations,
            "tolerance": self.tolerance,
            "history_length": len(self.history),
            "history_avg_quality": history_summary,
            "converged": self.converged(),
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _stable_id(*parts: str) -> str:
    """Derive a stable UUID-v5-style identifier from *parts*.

    Uses SHA-256 of the concatenated parts as the entropy source and formats
    the first 128 bits as a UUID string for readability.
    """
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(value, hi))


def _average_quality(boundaries: List[ModuleBoundary]) -> float:
    """Return the arithmetic mean quality score of *boundaries*, or 0.0."""
    if not boundaries:
        return 0.0
    return sum(b.quality_score for b in boundaries) / len(boundaries)


def _synthesise_boundary(
    left: Dict[str, Any],
    right: Dict[str, Any],
    index: int = 0,
) -> ModuleBoundary:
    """Build an initial ``ModuleBoundary`` from adjacent section descriptors.

    Computes an overlap region centred on the interface between the sections
    using their ``x`` / ``width`` extents.  Falls back to a small central
    overlap when the sections lack positional metadata.
    """
    left_id = str(left.get("module_id", f"left_{index}"))
    right_id = str(right.get("module_id", f"right_{index}"))
    boundary_id = _stable_id(left_id, right_id, str(index))

    left_end = float(left.get("x", 0.0)) + float(left.get("width", 0.5))
    right_start = float(right.get("x", left_end))

    # Normalise to [0, 1] using the total canvas width hint.
    canvas_width = float(left.get("canvas_width", right.get("canvas_width", 1.0))) or 1.0
    norm_end = _clamp(left_end / canvas_width)
    norm_start_r = _clamp(right_start / canvas_width)

    overlap_start = _clamp(min(norm_end, norm_start_r))
    overlap_end = _clamp(max(norm_end, norm_start_r) + 0.02)  # minimum 2 % overlap

    # Initial quality: score based on how well the ends align.
    raw_quality = 1.0 - abs(norm_end - norm_start_r)
    quality = _clamp(raw_quality)

    b_type = BoundaryType(left.get("boundary_type", BoundaryType.SOFT.value))

    return ModuleBoundary(
        boundary_id=boundary_id,
        left_module_id=left_id,
        right_module_id=right_id,
        overlap_start=overlap_start,
        overlap_end=min(overlap_end, 1.0),
        boundary_type=b_type,
        quality_score=quality,
    )


def _refine_boundary(boundary: ModuleBoundary) -> ModuleBoundary:
    """Apply one refinement step to *boundary*.

    The refinement nudges the overlap region toward the canonical 5–20 % size
    and gently increases the quality score to model incremental improvement.
    This is a simplified proxy for a real gradient-based update.
    """
    size = boundary.overlap_size()
    target_size = 0.10  # 10 % canonical overlap

    delta = (target_size - size) * 0.1  # 10 % of the gap per step
    new_start = _clamp(boundary.overlap_start - delta / 2.0)
    new_end = _clamp(boundary.overlap_end + delta / 2.0)

    if new_end <= new_start:
        new_end = new_start + 1e-6

    # Quality improves proportionally to how close we are to the target size.
    size_err = abs(boundary.overlap_size() - target_size)
    quality_gain = 0.01 * (1.0 - size_err / target_size)
    new_quality = _clamp(boundary.quality_score + quality_gain)

    return replace(
        boundary,
        overlap_start=new_start,
        overlap_end=new_end,
        quality_score=new_quality,
    )


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def analyze_module_boundary(
    left_section: Dict[str, Any],
    right_section: Dict[str, Any],
) -> ModuleBoundary:
    """Extract and characterise the boundary between two adjacent cover modules.

    Given raw section descriptor dictionaries *left_section* and
    *right_section* (as produced by the layout engine), this function
    identifies the geometric overlap region, assigns an appropriate
    ``BoundaryType``, and computes an initial quality score based on positional
    coherence.

    Both dictionaries are expected to contain at minimum the keys ``module_id``,
    ``x``, and ``width``.  Missing keys are treated as zero/empty so that the
    function degrades gracefully when operating on partial metadata.

    Returns a fully populated ``ModuleBoundary`` ready for quality measurement.
    """
    log.debug(
        "analyze_module_boundary: left=%s right=%s",
        left_section.get("module_id"),
        right_section.get("module_id"),
    )

    boundary = _synthesise_boundary(left_section, right_section)

    # Upgrade to OVERLAP type when the sections explicitly share content keys.
    shared_keys = set(left_section.keys()) & set(right_section.keys()) - {
        "module_id", "x", "y", "width", "height", "canvas_width", "boundary_type",
    }
    if shared_keys:
        boundary = replace(boundary, boundary_type=BoundaryType.OVERLAP)

    log.info(
        "analyze_module_boundary: boundary_id=%s type=%s size=%.3f quality=%.3f",
        boundary.boundary_id,
        boundary.boundary_type.value,
        boundary.overlap_size(),
        boundary.quality_score,
    )
    return boundary


def measure_overlap_quality(
    left_section: Dict[str, Any],
    right_section: Dict[str, Any],
    metrics: List[CoverOverlapMetric],
) -> OverlapQuality:
    """Compute a multi-facet quality assessment for the overlap of two modules.

    Each metric in *metrics* contributes a weighted partial score via its
    ``compute`` and ``normalize`` methods.  The ``overall`` field in the
    returned ``OverlapQuality`` is the weighted average of all metric
    contributions.

    The four named facets (``jaccard_index``, ``boundary_coherence``,
    ``size_ratio``, ``alignment_score``) are computed from heuristics
    embedded in this function; the *metrics* list provides additional
    extensibility for callers that need domain-specific facets.

    Returns an ``OverlapQuality`` instance with all facets populated.
    """
    log.debug(
        "measure_overlap_quality: left=%s right=%s metrics=%d",
        left_section.get("module_id"),
        right_section.get("module_id"),
        len(metrics),
    )

    # --- Jaccard index: fraction of shared content keys -----------------------
    left_keys: Set[str] = set(left_section.keys()) - {"module_id", "x", "y", "width", "height", "canvas_width"}
    right_keys: Set[str] = set(right_section.keys()) - {"module_id", "x", "y", "width", "height", "canvas_width"}
    union_size = len(left_keys | right_keys)
    jaccard = len(left_keys & right_keys) / union_size if union_size else 0.0

    # --- Boundary coherence: based on edge-to-edge alignment ------------------
    left_end = float(left_section.get("x", 0.0)) + float(left_section.get("width", 0.5))
    right_start = float(right_section.get("x", left_end))
    canvas = float(left_section.get("canvas_width", right_section.get("canvas_width", 1.0))) or 1.0
    coherence = _clamp(1.0 - abs(left_end - right_start) / canvas)

    # --- Size ratio: overlap relative to the smaller module -------------------
    left_w = float(left_section.get("width", 1.0))
    right_w = float(right_section.get("width", 1.0))
    smaller = min(left_w, right_w)
    overlap_extent = max(0.0, left_end - right_start)
    size_ratio = _clamp(overlap_extent / smaller) if smaller else 0.0

    # --- Alignment score: landmark proximity ----------------------------------
    left_cx = float(left_section.get("x", 0.0)) + left_w / 2.0
    right_cx = float(right_section.get("x", 0.0)) + right_w / 2.0
    dist_norm = _clamp(abs(left_cx - right_cx) / canvas)
    alignment = _clamp(1.0 - dist_norm)

    # --- Weighted combination from caller-supplied metrics --------------------
    if metrics:
        total_weight = sum(m.weight for m in metrics)
        weighted_sum = sum(
            m.normalize(m.compute(left_section, right_section)) for m in metrics
        )
        metric_score = weighted_sum / total_weight if total_weight else 0.0
    else:
        metric_score = (jaccard + coherence + alignment) / 3.0

    overall = _clamp(
        0.25 * jaccard
        + 0.25 * coherence
        + 0.15 * size_ratio
        + 0.20 * alignment
        + 0.15 * metric_score
    )

    quality = OverlapQuality(
        jaccard_index=jaccard,
        boundary_coherence=coherence,
        size_ratio=size_ratio,
        alignment_score=alignment,
        overall=overall,
    )

    log.info(
        "measure_overlap_quality: jaccard=%.3f coherence=%.3f size_ratio=%.3f "
        "alignment=%.3f overall=%.3f",
        jaccard,
        coherence,
        size_ratio,
        alignment,
        overall,
    )
    return quality


def optimize_boundaries(
    cover: List[Dict[str, Any]],
    optimizer: BoundaryOptimizer,
    metrics: List[CoverOverlapMetric],
) -> Tuple[BoundaryOptimizer, List[ModuleBoundary]]:
    """Iteratively optimise all module boundaries in *cover* for quality.

    Delegates to ``BoundaryOptimizer.optimize`` for the main loop and then
    re-scores each final boundary using the supplied *metrics* so that the
    ``quality_score`` fields reflect the full multi-metric evaluation rather
    than the simplified proxy used inside the optimiser.

    The function returns the updated optimiser (for history inspection) and
    the list of re-scored final boundaries in cover order.

    Raises ``JuGeoError`` when *cover* is empty, since there are no boundaries
    to optimise and returning an empty result would silently hide a caller bug.
    """
    if not cover:
        raise_with_scope(
            "EMPTY_COVER",
            message="optimize_boundaries: cover must contain at least one section",
        )

    log.info(
        "optimize_boundaries: optimizer=%s cover_sections=%d metrics=%d",
        optimizer.optimizer_id,
        len(cover),
        len(metrics),
    )

    final_optimizer, boundaries = optimizer.optimize(cover)

    # Re-score boundaries with full metric suite.
    rescored: List[ModuleBoundary] = []
    section_pairs = list(itertools.pairwise(cover))
    for boundary, (left, right) in zip(boundaries, section_pairs):
        quality = measure_overlap_quality(left, right, metrics)
        rescored.append(replace(boundary, quality_score=quality.overall))

    log.info(
        "optimize_boundaries: done; avg_quality=%.4f converged=%s",
        _average_quality(rescored),
        final_optimizer.converged(),
    )
    return final_optimizer, rescored


def score_boundary_quality(
    boundary: ModuleBoundary,
    metrics: List[CoverOverlapMetric],
) -> float:
    """Compute a scalar quality score for *boundary* using *metrics*.

    This is a convenience wrapper for callers that have a pre-built
    ``ModuleBoundary`` and just need a single float score without constructing
    full section descriptors.  It synthesises minimal section proxies from the
    boundary's positional data and delegates to ``measure_overlap_quality``.

    Returns a float in [0, 1]; higher is better.
    """
    log.debug(
        "score_boundary_quality: boundary=%s metrics=%d",
        boundary.boundary_id,
        len(metrics),
    )

    # Build minimal proxy section descriptors from boundary geometry.
    left_proxy: Dict[str, Any] = {
        "module_id": boundary.left_module_id,
        "x": 0.0,
        "width": boundary.overlap_start + boundary.overlap_size() / 2.0,
        "canvas_width": 1.0,
    }
    right_proxy: Dict[str, Any] = {
        "module_id": boundary.right_module_id,
        "x": boundary.overlap_start + boundary.overlap_size() / 2.0,
        "width": 1.0 - (boundary.overlap_start + boundary.overlap_size() / 2.0),
        "canvas_width": 1.0,
    }

    quality = measure_overlap_quality(left_proxy, right_proxy, metrics)
    score = quality.overall

    log.info(
        "score_boundary_quality: boundary=%s score=%.4f",
        boundary.boundary_id,
        score,
    )
    return score


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    print("=" * 70)
    print("module_boundaries_overlap_quality — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Build a small synthetic cover with three sections.
    # ------------------------------------------------------------------
    cover_sections = [
        {
            "module_id": "section_title",
            "x": 0.0,
            "width": 0.40,
            "height": 1.0,
            "canvas_width": 1.0,
            "title": "The Great Journey",
            "palette": "warm",
        },
        {
            "module_id": "section_spine",
            "x": 0.38,
            "width": 0.12,
            "height": 1.0,
            "canvas_width": 1.0,
            "palette": "warm",
            "text_colour": "white",
        },
        {
            "module_id": "section_back",
            "x": 0.50,
            "width": 0.50,
            "height": 1.0,
            "canvas_width": 1.0,
            "blurb": "An epic tale …",
            "palette": "cool",
        },
    ]

    # ------------------------------------------------------------------
    # 2. Analyse boundaries between consecutive sections.
    # ------------------------------------------------------------------
    print("\n--- Step 1: analyze_module_boundary ---")
    b01 = analyze_module_boundary(cover_sections[0], cover_sections[1])
    b12 = analyze_module_boundary(cover_sections[1], cover_sections[2])
    for b in (b01, b12):
        print(
            f"  {b.left_module_id!r:20s} → {b.right_module_id!r:20s} "
            f"type={b.boundary_type.value:8s} size={b.overlap_size():.3f} "
            f"quality={b.quality_score:.3f} valid={b.is_valid()} "
            f"tight={b.is_tight()} loose={b.is_loose()}"
        )

    # ------------------------------------------------------------------
    # 3. Measure overlap quality with a couple of metrics.
    # ------------------------------------------------------------------
    print("\n--- Step 2: measure_overlap_quality ---")
    metrics = [
        CoverOverlapMetric(
            metric_id=_stable_id("positional"),
            metric_name="positional_coherence",
            weight=1.5,
        ),
        CoverOverlapMetric(
            metric_id=_stable_id("content"),
            metric_name="content_affinity",
            weight=1.0,
        ),
    ]
    q01 = measure_overlap_quality(cover_sections[0], cover_sections[1], metrics)
    q12 = measure_overlap_quality(cover_sections[1], cover_sections[2], metrics)
    for tag, q in (("title→spine", q01), ("spine→back", q12)):
        print(f"  {tag}: {json.dumps(q.to_dict(), indent=None)}")
        print(f"    acceptable(0.5)={q.is_acceptable(0.5)}  acceptable(0.9)={q.is_acceptable(0.9)}")

    # ------------------------------------------------------------------
    # 4. Round-trip OverlapQuality serialisation.
    # ------------------------------------------------------------------
    print("\n--- Step 3: OverlapQuality round-trip ---")
    q_dict = q01.to_dict()
    q_restored = OverlapQuality.from_dict(q_dict)
    assert q_restored == q01, "Round-trip failed!"
    print(f"  Round-trip OK: overall={q_restored.overall:.4f}")

    # ------------------------------------------------------------------
    # 5. Run the optimiser.
    # ------------------------------------------------------------------
    print("\n--- Step 4: optimize_boundaries ---")
    optimizer = BoundaryOptimizer(
        optimizer_id=_stable_id("smoke_test_optimizer"),
        max_iterations=30,
        tolerance=1e-5,
    )
    final_optimizer, optimised = optimize_boundaries(cover_sections, optimizer, metrics)
    print(f"  Optimised {len(optimised)} boundaries.")
    for b in optimised:
        print(
            f"  {b.left_module_id!r:20s} → {b.right_module_id!r:20s} "
            f"quality={b.quality_score:.4f}"
        )
    opt_dict = final_optimizer.to_dict()
    print(f"  Optimizer summary: converged={opt_dict['converged']}  "
          f"history_length={opt_dict['history_length']}")

    # ------------------------------------------------------------------
    # 6. score_boundary_quality
    # ------------------------------------------------------------------
    print("\n--- Step 5: score_boundary_quality ---")
    score = score_boundary_quality(b01, metrics)
    print(f"  Score for title→spine boundary: {score:.4f}")

    # ------------------------------------------------------------------
    # 7. BoundaryAnalysis aggregate
    # ------------------------------------------------------------------
    print("\n--- Step 6: BoundaryAnalysis ---")
    analysis = BoundaryAnalysis(
        analysis_id=_stable_id("smoke_analysis"),
        cover_id="smoke_cover_001",
        boundaries=tuple(optimised),
        metrics={"avg_quality": _average_quality(optimised)},
    )
    print(f"  analysis_id={analysis.analysis_id}")
    print(f"  average_quality={analysis.average_quality():.4f}")
    worst = analysis.worst_boundary()
    best = analysis.best_boundary()
    print(f"  worst: {worst.boundary_id if worst else None}")
    print(f"  best:  {best.boundary_id if best else None}")
    analysis_dict = analysis.to_dict()
    assert "boundaries" in analysis_dict
    print(f"  to_dict keys: {sorted(analysis_dict.keys())}")

    # ------------------------------------------------------------------
    # 8. TrustTier lattice operations
    # ------------------------------------------------------------------
    print("\n--- Step 7: TrustTier operations ---")
    t1 = TrustTier.PROPOSAL
    t2 = TrustTier.VERIFIED
    print(f"  join(PROPOSAL, VERIFIED) = {t1.join(t2).name}")
    print(f"  meet(PROPOSAL, VERIFIED) = {t1.meet(t2).name}")
    print(f"  promote(PROPOSAL) = {t1.promote().name}")
    print(f"  demote(VERIFIED)  = {t2.demote().name}")
    print(f"  promote(PROOF_BACKED) = {TrustTier.PROOF_BACKED.promote().name}")
    print(f"  demote(PROPOSAL)      = {TrustTier.PROPOSAL.demote().name}")

    # ------------------------------------------------------------------
    # 9. Judgment and CechObstruction
    # ------------------------------------------------------------------
    print("\n--- Step 8: Judgment ---")
    j = Judgment(
        judgment_id=_stable_id("j_smoke"),
        proposition="title→spine overlap is coherent",
        tier=TrustTier.REVIEWED,
    )
    print(f"  {json.dumps(j.to_dict())}")
    j_promoted = j.with_tier(j.tier.promote())
    print(f"  promoted tier: {j_promoted.tier.name}")

    print("\n--- Step 9: CechObstruction ---")
    obs = CechObstruction(
        obstruction_id=_stable_id("obs_smoke"),
        boundary_id=b01.boundary_id,
        cycle_key=hashlib.sha256(b01.boundary_id.encode()).hexdigest()[:16],
        description="1-cycle detected in title/spine overlap region",
    )
    print(f"  {json.dumps(obs.to_dict())}")

    # ------------------------------------------------------------------
    # 10. ModuleBoundary edge-case validation
    # ------------------------------------------------------------------
    print("\n--- Step 10: ModuleBoundary edge cases ---")
    tight = replace(b01, overlap_start=0.40, overlap_end=0.42, quality_score=0.8)
    loose = replace(b01, overlap_start=0.10, overlap_end=0.45, quality_score=0.6)
    print(f"  tight boundary: is_tight={tight.is_tight()} is_loose={tight.is_loose()}")
    print(f"  loose boundary: is_tight={loose.is_tight()} is_loose={loose.is_loose()}")
    invalid = replace(b01, quality_score=1.5)
    print(f"  invalid quality_score 1.5: is_valid={invalid.is_valid()}")

    print("\n" + "=" * 70)
    print("Smoke test PASSED")
    print("=" * 70)
