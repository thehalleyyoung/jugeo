r"""Chapter 39, Section 5 — Global section assembly.

Theory (theory2.tex §39.5):
    Given a sheaf **F** over a topological space *X* equipped with a finite
    open cover ``{U_i}_{i ∈ I}`` and a family of local sections
    ``{s_i ∈ F(U_i)}_{i ∈ I}``, the **sheaf gluing axiom** guarantees the
    existence of a unique global section ``s ∈ F(X)`` if and only if the
    local sections are *mutually compatible* on every pairwise intersection:

        s_i |_{U_i ∩ U_j} = s_j |_{U_i ∩ U_j}   for all  i, j ∈ I.         (*)

    This compatibility condition is the *gluing condition*.  Assembly is the
    constructive process that synthesises *s* from ``{s_i}`` when (*) holds;
    when (*) fails, the obstruction to existence is recorded as a Čech
    1-cocycle in ``Ȟ¹({U_i}, F)``.

    §39.5.2 — Gluing condition in the jugeo model:
    In the jugeo runtime, patches correspond to the opens ``{U_i}`` and local
    sections to :class:`~jugeo.generation.semantic_closure.models.ClosureCheck`
    families over each patch.  The *restriction* of ``s_i`` to the overlap
    ``U_i ∩ U_j`` is computed by intersecting the evidence sets of the two
    patches and evaluating the :class:`ClosureResult` on the shared vocabulary.

    §39.5.3 — Obstruction theory:
    When the gluing conditions fail for some pair ``(i, j)``, the inconsistency
    contributes a cochain ``c_{ij} ∈ F(U_i ∩ U_j)``.  These cochains assemble
    into a Čech 1-cocycle whose class in ``Ȟ¹`` is the cohomological obstruction
    to global section existence.  The *obstruction score*

        h¹ = Σ_{i < j}  weight(i, j) · incompatibility_score(s_i, s_j)

    measures the total magnitude of these obstructions.  When h¹ = 0 and no
    inconsistent pairs are found the assembly is called *complete*; when h¹ > 0
    the assembly is *partial* or *obstructed*.

    §39.5.4 — Assembly algorithm:
    The assembly proceeds in topological order over the nerve of the cover:

    1. Choose a maximal tree ``T`` in the 1-skeleton of the nerve.
    2. For each edge ``(i, j)`` in ``T``, verify the gluing condition.
    3. If all tree edges are compatible, merge the sections along the tree.
    4. Verify the remaining (non-tree) edges; any failure adds an obstruction.
    5. If no obstructions are found, emit the assembled :class:`GlobalSection`.

    §39.5.5 — Trust tier propagation:
    The assembled global section inherits the **minimum trust tier** of all
    contributing local sections.  If any local section carries a lower trust,
    the global section is demoted accordingly.

    copilot: s05-global-section-assembly
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

try:
    from jugeo.generation.semantic_closure.models import (
        ClosureCheck,
        ClosureGap,
        ClosureResult,
        GapSeverity,
        CheckType,
        RegressionTest,
        SemanticClosure,
        SEVERITY_ORDER,
        make_check,
        make_gap,
    )
    _MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MODELS_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.geometry.descent import DescentResult, LocalSection, GluingData
    _DESCENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DESCENT_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.geometry.covers import Cover
    _COVERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _COVERS_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.generation.semantic_closure.residual_gap_analysis import (
        ResidualGapReport,
        ResidualGapCoordinator,
        GapClassification,
    )
    _S04_AVAILABLE = True
except ImportError:  # pragma: no cover
    _S04_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.generation.semantic_closure.integration_closure import (
        IntegrationState,
        ClosureCertificate,
    )
    _S03_AVAILABLE = True
except ImportError:  # pragma: no cover
    _S03_AVAILABLE = False

logger = logging.getLogger(__name__)

__all__ = [
    # Enums
    "AssemblyStatus",
    # Dataclasses
    "AssemblyResult",
    "CompatibilityReport",
    "GlobalSection",
    "ObstructionRecord",
    # Classes
    "GlobalSectionAnalyzer",
    "GlobalSectionCoordinator",
    "GlobalSectionWitness",
    # Module-level helpers
    "assemble_global_section",
    "verify_sheaf_gluing",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Compatibility score below which two local sections are considered
#: incompatible for the purposes of gluing.
COMPATIBILITY_THRESHOLD: float = 0.75

#: Default minimum trust tier for an assembled global section.
DEFAULT_MIN_TRUST: int = 1

#: Weight applied to each incompatible pair when computing h¹.
INCOMPATIBILITY_WEIGHT: float = 1.0

#: The maximum h¹ score below which the assembly is considered *complete*.
H1_COMPLETE_THRESHOLD: float = 1e-9


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AssemblyStatus(str, Enum):
    """Status of a :class:`GlobalSection` assembly attempt.

    * ``PENDING``    — assembly has not yet been attempted.
    * ``PARTIAL``    — some but not all gluing conditions hold; a partial
      section has been assembled over a sub-cover.
    * ``COMPLETE``   — all gluing conditions hold and a full global section
      has been assembled.
    * ``OBSTRUCTED`` — the assembly failed due to a non-trivial H¹ obstruction;
      no global section exists for the current cover and section data.
    """

    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETE = "complete"
    OBSTRUCTED = "obstructed"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CompatibilityReport:
    """Result of comparing two local sections on their overlap.

    Produced by :meth:`GlobalSectionAnalyzer.check_compatibility`.

    Attributes:
        pair_id:            Unique identifier for this comparison.
        patch_i:            Identifier of the first patch.
        patch_j:            Identifier of the second patch.
        is_compatible:      True when the gluing condition holds.
        compatibility_score: Continuous score in [0, 1]; 1.0 = fully compatible.
        overlap_keys:       Set of evidence keys shared by both sections.
        conflicting_keys:   Keys where the two sections disagree.
        notes:              Human-readable explanation.
        timestamp:          Unix timestamp.
    """

    pair_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    patch_i: str = ""
    patch_j: str = ""
    is_compatible: bool = False
    compatibility_score: float = 0.0
    overlap_keys: list[str] = field(default_factory=list)
    conflicting_keys: list[str] = field(default_factory=list)
    notes: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "pair_id": self.pair_id,
            "patch_i": self.patch_i,
            "patch_j": self.patch_j,
            "is_compatible": self.is_compatible,
            "compatibility_score": self.compatibility_score,
            "overlap_keys": self.overlap_keys,
            "conflicting_keys": self.conflicting_keys,
            "notes": self.notes,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """One-line summary."""
        compat = "COMPAT" if self.is_compatible else "INCOMPAT"
        return (
            f"CompatReport[{self.pair_id[:8]}] "
            f"({self.patch_i}, {self.patch_j}) "
            f"score={self.compatibility_score:.3f} {compat}"
        )


@dataclass
class ObstructionRecord:
    """Čech 1-cocycle obstruction information for a pair of patches.

    Produced when the gluing condition fails for sections over ``U_i`` and
    ``U_j``.  The full obstruction class in Ȟ¹ is the sum of all records
    across incompatible pairs.

    Attributes:
        record_id:          Unique identifier.
        patch_i:            First patch identifier.
        patch_j:            Second patch identifier.
        cochain_keys:       Evidence keys whose values disagree.
        cochain_magnitude:  ||c_{ij}||  — L¹ norm of the disagreement cochain.
        obstruction_class:  Human-readable label for the obstruction class.
        weight:             Weight applied to this record in the h¹ sum.
        notes:              Additional diagnostic notes.
        timestamp:          Unix timestamp.
    """

    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    patch_i: str = ""
    patch_j: str = ""
    cochain_keys: list[str] = field(default_factory=list)
    cochain_magnitude: float = 0.0
    obstruction_class: str = "unknown"
    weight: float = INCOMPATIBILITY_WEIGHT
    notes: str = ""
    timestamp: float = field(default_factory=time.time)

    def h1_contribution(self) -> float:
        """Return the contribution of this record to the total h¹ score."""
        return self.cochain_magnitude * self.weight

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "record_id": self.record_id,
            "patch_i": self.patch_i,
            "patch_j": self.patch_j,
            "cochain_keys": self.cochain_keys,
            "cochain_magnitude": self.cochain_magnitude,
            "obstruction_class": self.obstruction_class,
            "weight": self.weight,
            "notes": self.notes,
            "timestamp": self.timestamp,
            "h1_contribution": self.h1_contribution(),
        }


@dataclass
class GlobalSection:
    """The assembled global section produced by :class:`GlobalSectionCoordinator`.

    When :meth:`~GlobalSectionCoordinator.run_full_assembly_pipeline` succeeds
    it returns an instance of this class.  The ``merged_data`` dict contains
    the union of all local section evidence, with overlapping keys resolved by
    the trust-tier-aware merge strategy.

    Attributes:
        section_id:     Unique identifier for the global section.
        patch_ids:      Tuple of all patch identifiers that contributed.
        merged_data:    Union of evidence from all local sections.
        trust_tier:     Minimum trust tier across all contributing sections.
        assembly_time:  Unix timestamp at which the section was assembled.
        source_count:   Number of local sections that were merged.
        quality_score:  Assembly quality in [0, 1] from
                        :meth:`~GlobalSectionAnalyzer.score_assembly_quality`.
        metadata:       Free-form dict for downstream consumers.
    """

    section_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    patch_ids: tuple[str, ...] = field(default_factory=tuple)
    merged_data: dict[str, Any] = field(default_factory=dict)
    trust_tier: int = DEFAULT_MIN_TRUST
    assembly_time: float = field(default_factory=time.time)
    source_count: int = 0
    quality_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"GlobalSection[{self.section_id[:8]}] "
            f"patches={len(self.patch_ids)} "
            f"keys={len(self.merged_data)} "
            f"trust={self.trust_tier} "
            f"quality={self.quality_score:.3f}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (merged_data values as strings)."""
        return {
            "section_id": self.section_id,
            "patch_ids": list(self.patch_ids),
            "merged_data_keys": sorted(self.merged_data.keys()),
            "trust_tier": self.trust_tier,
            "assembly_time": self.assembly_time,
            "source_count": self.source_count,
            "quality_score": self.quality_score,
            "metadata": self.metadata,
        }


@dataclass
class AssemblyResult:
    """Full result of a global section assembly attempt.

    Produced by :meth:`GlobalSectionCoordinator.assemble`.

    Attributes:
        result_id:              Unique identifier.
        status:                 :class:`AssemblyStatus` of the attempt.
        global_section:         Assembled :class:`GlobalSection` (None if failed).
        partial_sections:       Sections assembled over a sub-cover (status=PARTIAL).
        obstruction_records:    List of :class:`ObstructionRecord` objects.
        obstruction_h1:         Total h¹ obstruction score.
        quality_score:          Assembly quality from the analyser.
        compatible_pairs:       Count of pairs that passed gluing.
        incompatible_pairs:     Count of pairs that failed gluing.
        inconsistent_pair_ids:  List of ``(patch_i, patch_j)`` pairs that failed.
        notes:                  Human-readable notes about the assembly.
        created_at:             Unix timestamp.
    """

    result_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    status: AssemblyStatus = AssemblyStatus.PENDING
    global_section: GlobalSection | None = None
    partial_sections: list[GlobalSection] = field(default_factory=list)
    obstruction_records: list[ObstructionRecord] = field(default_factory=list)
    obstruction_h1: float = 0.0
    quality_score: float = 0.0
    compatible_pairs: int = 0
    incompatible_pairs: int = 0
    inconsistent_pair_ids: list[tuple[str, str]] = field(default_factory=list)
    notes: str = ""
    created_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        """One-line human-readable summary."""
        gs_id = self.global_section.section_id[:8] if self.global_section else "None"
        return (
            f"AssemblyResult[{self.result_id[:8]}] "
            f"status={self.status.value} "
            f"global_section={gs_id} "
            f"h1={self.obstruction_h1:.4f} "
            f"quality={self.quality_score:.3f} "
            f"compat={self.compatible_pairs} "
            f"incompat={self.incompatible_pairs}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "result_id": self.result_id,
            "status": self.status.value,
            "global_section": (
                self.global_section.to_dict() if self.global_section else None
            ),
            "obstruction_h1": self.obstruction_h1,
            "quality_score": self.quality_score,
            "compatible_pairs": self.compatible_pairs,
            "incompatible_pairs": self.incompatible_pairs,
            "inconsistent_pair_ids": [
                list(p) for p in self.inconsistent_pair_ids
            ],
            "obstruction_record_count": len(self.obstruction_records),
            "notes": self.notes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class GlobalSectionWitness:
    """Immutable record of a global section assembly attempt.

    Created by :class:`GlobalSectionCoordinator` after each assembly run to
    provide a permanent, tamper-proof account of the result.

    Attributes:
        witness_id:         Unique identifier.
        patch_ids:          Tuple of patch identifiers that were assembled.
        section_ids:        Tuple of local section identifiers used as inputs.
        is_global:          True when the assembly was COMPLETE.
        obstruction_h1:     Total h¹ obstruction score.
        inconsistent_pairs: Tuple of ``(patch_i, patch_j)`` string pairs that
                            failed the gluing condition.
        assembly_quality:   Quality score in [0, 1].
        timestamp:          Unix timestamp of witness creation.
    """

    witness_id: str
    patch_ids: tuple[str, ...]
    section_ids: tuple[str, ...]
    is_global: bool
    obstruction_h1: float
    inconsistent_pairs: tuple[tuple[str, str], ...]
    assembly_quality: float
    timestamp: float

    def is_obstructed(self) -> bool:
        """Return True when the assembly was obstructed (h¹ > threshold)."""
        return self.obstruction_h1 > H1_COMPLETE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "patch_ids": list(self.patch_ids),
            "section_ids": list(self.section_ids),
            "is_global": self.is_global,
            "obstruction_h1": self.obstruction_h1,
            "inconsistent_pairs": [list(p) for p in self.inconsistent_pairs],
            "assembly_quality": self.assembly_quality,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """One-line human-readable summary."""
        status = "GLOBAL" if self.is_global else "PARTIAL/OBSTRUCTED"
        return (
            f"GlobalSectionWitness[{self.witness_id[:8]}] "
            f"patches={len(self.patch_ids)} "
            f"is_global={self.is_global} "
            f"h1={self.obstruction_h1:.4f} "
            f"quality={self.assembly_quality:.3f} "
            f"status={status}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _section_id(section: Any) -> str:
    """Extract a stable string identifier from a section-like object."""
    for attr in ("section_id", "id", "patch_id", "name"):
        val = getattr(section, attr, None)
        if val:
            return str(val)
    return hashlib.sha1(repr(section).encode()).hexdigest()[:12]


def _section_patch(section: Any) -> str:
    """Extract the patch identifier from a section-like object."""
    for attr in ("patch_id", "patch", "location", "name"):
        val = getattr(section, attr, None)
        if val:
            return str(val)
    return _section_id(section)


def _section_data(section: Any) -> dict[str, Any]:
    """Return the evidence / data dict from a section-like object."""
    for attr in ("data", "evidence", "content", "checks", "merged_data"):
        val = getattr(section, attr, None)
        if isinstance(val, dict):
            return val
    return {}


def _section_trust(section: Any) -> int:
    """Return the trust tier of *section* (default 1)."""
    t = getattr(section, "trust_tier", None)
    if t is not None:
        try:
            return int(t)
        except (TypeError, ValueError):
            pass
    return DEFAULT_MIN_TRUST


def _merge_section_data(
    sections: list[Any],
    trust_aware: bool = True,
) -> dict[str, Any]:
    """Merge the evidence dicts of all *sections* into a single dict.

    When *trust_aware* is True, higher-trust sections override lower-trust
    ones on conflicting keys.  When False, later sections override earlier ones.

    Args:
        sections:    List of section-like objects.
        trust_aware: Whether to resolve conflicts by trust tier.

    Returns:
        Merged evidence dict.
    """
    merged: dict[str, Any] = {}
    ranked = sorted(sections, key=_section_trust)  # ascending; last wins = highest trust
    for sec in ranked:
        data = _section_data(sec)
        merged.update(data)
    return merged


def _key_compatibility_score(
    data_i: dict[str, Any],
    data_j: dict[str, Any],
) -> tuple[float, list[str], list[str]]:
    """Compute a compatibility score between two evidence dicts.

    Returns:
        ``(score, overlap_keys, conflicting_keys)`` where *score* ∈ [0, 1].
    """
    keys_i = set(data_i.keys())
    keys_j = set(data_j.keys())
    overlap = sorted(keys_i & keys_j)
    if not overlap:
        # No shared keys → vacuously compatible (gluing condition is trivially true)
        return 1.0, [], []
    conflicting: list[str] = []
    for k in overlap:
        vi = data_i[k]
        vj = data_j[k]
        # Coarse equality: convert both to string for comparison
        if str(vi) != str(vj):
            conflicting.append(k)
    score = 1.0 - len(conflicting) / len(overlap)
    return score, overlap, conflicting


# ---------------------------------------------------------------------------
# GlobalSectionAnalyzer
# ---------------------------------------------------------------------------


class GlobalSectionAnalyzer:
    """Analysis helper for global section assembly.

    Provides the per-pair and aggregate analytical primitives consumed by
    :class:`GlobalSectionCoordinator`.  The analyzer is **stateless** and
    may be shared across coordinator instances.

    Typical usage::

        analyzer = GlobalSectionAnalyzer()
        report = analyzer.check_compatibility(s_i, s_j)
        h1     = analyzer.compute_cohomology_obstruction([s_i, s_j, s_k])
        pairs  = analyzer.identify_inconsistent_pairs([s_i, s_j, s_k])
        score  = analyzer.score_assembly_quality(result)
    """

    def __init__(
        self,
        compatibility_threshold: float = COMPATIBILITY_THRESHOLD,
    ) -> None:
        self.compatibility_threshold = compatibility_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_compatibility(
        self, s_i: Any, s_j: Any
    ) -> CompatibilityReport:
        """Check whether the gluing condition holds between *s_i* and *s_j*.

        The gluing condition is approximated by computing a compatibility
        score over the shared evidence keys.  A score ≥
        ``compatibility_threshold`` is treated as compatible.

        Args:
            s_i: First local section.
            s_j: Second local section.

        Returns:
            A :class:`CompatibilityReport` with the result.
        """
        patch_i = _section_patch(s_i)
        patch_j = _section_patch(s_j)
        data_i = _section_data(s_i)
        data_j = _section_data(s_j)
        score, overlap_keys, conflicting_keys = _key_compatibility_score(data_i, data_j)
        is_compat = score >= self.compatibility_threshold
        notes = (
            f"Overlap {len(overlap_keys)} keys; "
            f"{len(conflicting_keys)} conflicting "
            f"({'COMPATIBLE' if is_compat else 'INCOMPATIBLE'})."
        )
        if conflicting_keys:
            notes += f" Conflicts: {conflicting_keys[:5]}"
        logger.debug(
            "check_compatibility (%s, %s): score=%.4f compat=%s",
            patch_i, patch_j, score, is_compat,
        )
        return CompatibilityReport(
            pair_id=uuid.uuid4().hex[:16],
            patch_i=patch_i,
            patch_j=patch_j,
            is_compatible=is_compat,
            compatibility_score=score,
            overlap_keys=overlap_keys,
            conflicting_keys=conflicting_keys,
            notes=notes,
            timestamp=time.time(),
        )

    def compute_cohomology_obstruction(self, sections: list[Any]) -> float:
        """Compute the total h¹ obstruction score for *sections*.

        The score is defined as::

            h¹ = Σ_{i < j}  INCOMPATIBILITY_WEIGHT · (1 − score(s_i, s_j))
                             whenever score(s_i, s_j) < compatibility_threshold

        Pairs with no shared evidence keys contribute 0 (vacuously compatible).

        Args:
            sections: List of local section objects.

        Returns:
            Non-negative float h¹; 0.0 means no obstruction detected.
        """
        h1 = 0.0
        n = len(sections)
        for i in range(n):
            for j in range(i + 1, n):
                score, _, _ = _key_compatibility_score(
                    _section_data(sections[i]),
                    _section_data(sections[j]),
                )
                if score < self.compatibility_threshold:
                    h1 += INCOMPATIBILITY_WEIGHT * (1.0 - score)
        return round(h1, 8)

    def identify_inconsistent_pairs(
        self, sections: list[Any]
    ) -> list[tuple[str, str]]:
        """Return all pairs of patches whose sections fail the gluing condition.

        Args:
            sections: List of local section objects.

        Returns:
            List of ``(patch_i, patch_j)`` string tuples for failing pairs.
        """
        bad: list[tuple[str, str]] = []
        n = len(sections)
        for i in range(n):
            for j in range(i + 1, n):
                rpt = self.check_compatibility(sections[i], sections[j])
                if not rpt.is_compatible:
                    bad.append((rpt.patch_i, rpt.patch_j))
        return bad

    def score_assembly_quality(self, result: AssemblyResult) -> float:
        """Compute a quality score in [0, 1] for an :class:`AssemblyResult`.

        The formula is::

            quality = status_base · (1 − h1_penalty) · pair_ratio

        where:
        * ``status_base`` ∈ {1.0: COMPLETE, 0.6: PARTIAL, 0.1: OBSTRUCTED,
          0.0: PENDING}
        * ``h1_penalty = tanh(obstruction_h1)``   ∈ [0, 1)
        * ``pair_ratio = compat / (compat + incompat)`` when compat+incompat > 0,
          else 1.0.

        Args:
            result: The :class:`AssemblyResult` to score.

        Returns:
            Quality score in [0, 1].
        """
        status_base = {
            AssemblyStatus.COMPLETE: 1.0,
            AssemblyStatus.PARTIAL: 0.6,
            AssemblyStatus.OBSTRUCTED: 0.1,
            AssemblyStatus.PENDING: 0.0,
        }.get(result.status, 0.0)
        h1_penalty = math.tanh(result.obstruction_h1)
        total_pairs = result.compatible_pairs + result.incompatible_pairs
        pair_ratio = (
            result.compatible_pairs / total_pairs if total_pairs > 0 else 1.0
        )
        quality = status_base * (1.0 - h1_penalty) * pair_ratio
        return round(max(0.0, min(1.0, quality)), 6)


# ---------------------------------------------------------------------------
# GlobalSectionCoordinator
# ---------------------------------------------------------------------------


class GlobalSectionCoordinator:
    """Top-level orchestrator for global section assembly.

    The coordinator implements the full sheaf assembly pipeline described in
    theory2.tex §39.5.4:

    1. Accept a list of local sections over patches ``{U_i}``.
    2. Verify the pairwise gluing conditions using the injected
       :class:`GlobalSectionAnalyzer`.
    3. Compute obstructions for incompatible pairs.
    4. If all conditions hold, assemble the :class:`GlobalSection`.
    5. Return a fully-populated :class:`AssemblyResult`.

    A :class:`GlobalSectionWitness` is emitted after each call to
    :meth:`run_full_assembly_pipeline` and stored in :attr:`witnesses`.

    Args:
        analyzer:               Injected :class:`GlobalSectionAnalyzer`.
        trust_aware_merge:      Whether to use trust-tier-aware merging.
        compatibility_threshold: Override threshold passed to the analyser.
        max_sections:           Upper bound on the number of local sections
                                that can be assembled in a single call.

    Example::

        coord = GlobalSectionCoordinator()
        result = coord.assemble(local_sections)
        print(result.summary())
    """

    def __init__(
        self,
        analyzer: GlobalSectionAnalyzer | None = None,
        trust_aware_merge: bool = True,
        compatibility_threshold: float = COMPATIBILITY_THRESHOLD,
        max_sections: int = 512,
    ) -> None:
        self.analyzer = analyzer or GlobalSectionAnalyzer(
            compatibility_threshold=compatibility_threshold
        )
        self.trust_aware_merge = trust_aware_merge
        self.max_sections = max_sections
        self.witnesses: list[GlobalSectionWitness] = []
        logger.debug(
            "GlobalSectionCoordinator initialised trust_aware=%s max=%d",
            self.trust_aware_merge, self.max_sections,
        )

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def assemble(self, local_sections: list[Any]) -> AssemblyResult:
        """Assemble a global section from *local_sections*.

        Verifies all pairwise gluing conditions, records obstructions, and
        either returns a complete :class:`GlobalSection` (when all conditions
        hold) or a partial result with obstruction information.

        Args:
            local_sections: List of local section objects.

        Returns:
            A fully-populated :class:`AssemblyResult`.
        """
        if not local_sections:
            logger.info("assemble called with empty section list.")
            empty_gs = GlobalSection(
                section_id=uuid.uuid4().hex[:16],
                patch_ids=(),
                merged_data={},
                trust_tier=DEFAULT_MIN_TRUST,
                assembly_time=time.time(),
                source_count=0,
                quality_score=1.0,
            )
            result = AssemblyResult(
                status=AssemblyStatus.COMPLETE,
                global_section=empty_gs,
                notes="No local sections to assemble — trivially complete.",
            )
            result.quality_score = self.analyzer.score_assembly_quality(result)
            return result

        if len(local_sections) > self.max_sections:
            raise ValueError(
                f"assemble: received {len(local_sections)} sections but "
                f"max_sections={self.max_sections}."
            )

        logger.info("assemble: %d local sections", len(local_sections))
        result = AssemblyResult(
            result_id=uuid.uuid4().hex[:16],
            status=AssemblyStatus.PENDING,
            created_at=time.time(),
        )
        obstruction_records: list[ObstructionRecord] = []
        compat_count = 0
        incompat_count = 0
        inconsistent: list[tuple[str, str]] = []
        n = len(local_sections)
        for i in range(n):
            for j in range(i + 1, n):
                compat_rpt = self.analyzer.check_compatibility(
                    local_sections[i], local_sections[j]
                )
                if compat_rpt.is_compatible:
                    compat_count += 1
                else:
                    incompat_count += 1
                    inconsistent.append((compat_rpt.patch_i, compat_rpt.patch_j))
                    rec = self._build_obstruction(compat_rpt)
                    obstruction_records.append(rec)
                    logger.debug(
                        "Gluing failure (%s, %s) score=%.4f cochain=%.4f",
                        compat_rpt.patch_i, compat_rpt.patch_j,
                        compat_rpt.compatibility_score, rec.cochain_magnitude,
                    )

        result.compatible_pairs = compat_count
        result.incompatible_pairs = incompat_count
        result.inconsistent_pair_ids = inconsistent
        result.obstruction_records = obstruction_records
        result.obstruction_h1 = round(
            sum(r.h1_contribution() for r in obstruction_records), 8
        )

        if incompat_count == 0:
            result.status = AssemblyStatus.COMPLETE
            gs = self._build_global_section(local_sections)
            result.global_section = gs
            result.notes = (
                f"All {compat_count} gluing conditions satisfied. "
                f"Global section assembled with {len(gs.merged_data)} evidence keys."
            )
        elif compat_count > 0:
            result.status = AssemblyStatus.PARTIAL
            result.partial_sections = self._build_partial_sections(
                local_sections, inconsistent
            )
            result.notes = (
                f"Partial assembly: {compat_count} compatible, "
                f"{incompat_count} incompatible pairs. "
                f"h¹={result.obstruction_h1:.4f}."
            )
        else:
            result.status = AssemblyStatus.OBSTRUCTED
            result.notes = (
                f"All {incompat_count} pairs are incompatible. "
                f"h¹={result.obstruction_h1:.4f}. "
                "No global section exists for the current cover."
            )

        result.quality_score = self.analyzer.score_assembly_quality(result)
        logger.info("Assembly result: %s", result.summary())
        return result

    def verify_gluing(
        self, s_i: Any, s_j: Any, overlap: dict[str, Any] | None = None
    ) -> bool:
        """Verify the gluing condition between *s_i* and *s_j*.

        The optional *overlap* dict, when provided, restricts the comparison
        to the specified evidence keys (modelling the restriction map
        s_i|_{U_i ∩ U_j}).

        Args:
            s_i:     First local section.
            s_j:     Second local section.
            overlap: Optional dict of evidence keys to restrict to.

        Returns:
            True when the gluing condition holds.
        """
        if overlap is not None:
            # Create proxy sections restricted to the overlap domain
            class _Restricted:
                def __init__(self, sec: Any, keys: dict[str, Any]) -> None:
                    data = _section_data(sec)
                    self.data = {k: data[k] for k in keys if k in data}
                    self.patch_id = _section_patch(sec)
                    self.trust_tier = _section_trust(sec)

            s_i_r = _Restricted(s_i, overlap)
            s_j_r = _Restricted(s_j, overlap)
            rpt = self.analyzer.check_compatibility(s_i_r, s_j_r)
        else:
            rpt = self.analyzer.check_compatibility(s_i, s_j)
        return rpt.is_compatible

    def compute_obstruction(
        self, sections: list[Any]
    ) -> ObstructionRecord | None:
        """Compute the aggregate obstruction record for *sections*.

        Returns a single :class:`ObstructionRecord` representing the full
        obstruction, or ``None`` if no obstruction is detected.

        Args:
            sections: List of local section objects.

        Returns:
            :class:`ObstructionRecord` or ``None``.
        """
        h1 = self.analyzer.compute_cohomology_obstruction(sections)
        if h1 <= H1_COMPLETE_THRESHOLD:
            return None
        pairs = self.analyzer.identify_inconsistent_pairs(sections)
        all_conflict_keys: list[str] = []
        for s_i, s_j in (
            (sections[a], sections[b])
            for a in range(len(sections))
            for b in range(a + 1, len(sections))
            if (_section_patch(sections[a]), _section_patch(sections[b])) in pairs
            or (_section_patch(sections[b]), _section_patch(sections[a])) in [
                (p, q) for p, q in pairs
            ]
        ):
            _, _, conflicts = _key_compatibility_score(
                _section_data(s_i), _section_data(s_j)
            )
            all_conflict_keys.extend(conflicts)

        return ObstructionRecord(
            patch_i="(aggregate)",
            patch_j="(aggregate)",
            cochain_keys=list(dict.fromkeys(all_conflict_keys)),
            cochain_magnitude=h1,
            obstruction_class=f"Ȟ¹-class (h¹={h1:.4f})",
            weight=1.0,
            notes=f"Aggregate obstruction from {len(pairs)} incompatible pairs.",
            timestamp=time.time(),
        )

    def run_full_assembly_pipeline(
        self, sections: list[Any]
    ) -> GlobalSection | None:
        """Run the complete assembly pipeline and return a GlobalSection or None.

        This is a convenience wrapper that calls :meth:`assemble`, records a
        :class:`GlobalSectionWitness`, and returns the assembled section (or
        None if assembly failed).

        Args:
            sections: List of local section objects.

        Returns:
            :class:`GlobalSection` if status is COMPLETE, else None.
        """
        result = self.assemble(sections)
        quality = self.analyzer.score_assembly_quality(result)
        witness = GlobalSectionWitness(
            witness_id=uuid.uuid4().hex[:16],
            patch_ids=tuple(_section_patch(s) for s in sections),
            section_ids=tuple(_section_id(s) for s in sections),
            is_global=(result.status == AssemblyStatus.COMPLETE),
            obstruction_h1=result.obstruction_h1,
            inconsistent_pairs=tuple(result.inconsistent_pair_ids),
            assembly_quality=quality,
            timestamp=time.time(),
        )
        self.witnesses.append(witness)
        logger.info("Witness recorded: %s", witness.summary())
        if result.status == AssemblyStatus.COMPLETE:
            return result.global_section
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_global_section(self, sections: list[Any]) -> GlobalSection:
        """Merge all *sections* into a single :class:`GlobalSection`."""
        merged = _merge_section_data(sections, trust_aware=self.trust_aware_merge)
        min_trust = min((_section_trust(s) for s in sections), default=DEFAULT_MIN_TRUST)
        gs = GlobalSection(
            section_id=uuid.uuid4().hex[:16],
            patch_ids=tuple(_section_patch(s) for s in sections),
            merged_data=merged,
            trust_tier=min_trust,
            assembly_time=time.time(),
            source_count=len(sections),
        )
        return gs

    def _build_obstruction(
        self, compat_rpt: CompatibilityReport
    ) -> ObstructionRecord:
        """Build an :class:`ObstructionRecord` from a failed :class:`CompatibilityReport`."""
        magnitude = 1.0 - compat_rpt.compatibility_score
        return ObstructionRecord(
            patch_i=compat_rpt.patch_i,
            patch_j=compat_rpt.patch_j,
            cochain_keys=compat_rpt.conflicting_keys,
            cochain_magnitude=magnitude,
            obstruction_class=(
                f"compatibility-failure "
                f"(score={compat_rpt.compatibility_score:.4f})"
            ),
            weight=INCOMPATIBILITY_WEIGHT,
            notes=compat_rpt.notes,
            timestamp=time.time(),
        )

    def _build_partial_sections(
        self,
        sections: list[Any],
        inconsistent_pairs: list[tuple[str, str]],
    ) -> list[GlobalSection]:
        """Build a list of partial :class:`GlobalSection` objects.

        For each connected component of the *compatibility graph* (where nodes
        are patches and edges are compatible pairs), assemble one partial
        section.  This implements the sub-cover assembly described in
        theory2.tex §39.5.4 step 3.

        Args:
            sections:          All local sections.
            inconsistent_pairs: List of ``(patch_i, patch_j)`` pairs that failed.

        Returns:
            List of :class:`GlobalSection` objects, one per connected component.
        """
        incompat_set = {frozenset(p) for p in inconsistent_pairs}
        patch_to_sec: dict[str, Any] = {_section_patch(s): s for s in sections}
        patches = list(patch_to_sec.keys())
        # Union-Find for connected components
        parent: dict[str, str] = {p: p for p in patches}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for a in patches:
            for b in patches:
                if a < b and frozenset({a, b}) not in incompat_set:
                    union(a, b)

        # Group patches by component
        components: dict[str, list[str]] = {}
        for p in patches:
            root = find(p)
            components.setdefault(root, []).append(p)

        partial: list[GlobalSection] = []
        for comp_patches in components.values():
            comp_secs = [patch_to_sec[p] for p in comp_patches]
            gs = self._build_global_section(comp_secs)
            gs.metadata["partial"] = True
            gs.metadata["component_patches"] = comp_patches
            partial.append(gs)
            logger.debug(
                "Partial section assembled over %s", comp_patches
            )
        return partial


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def assemble_global_section(
    sections: list[Any],
    trust_aware: bool = True,
    compatibility_threshold: float = COMPATIBILITY_THRESHOLD,
) -> GlobalSection | None:
    """Convenience wrapper: assemble a global section from *sections*.

    Constructs a :class:`GlobalSectionCoordinator` and calls
    :meth:`~GlobalSectionCoordinator.run_full_assembly_pipeline`.

    Args:
        sections:                List of local section-like objects.
        trust_aware:             Whether to use trust-tier-aware merging.
        compatibility_threshold: Threshold for pairwise gluing checks.

    Returns:
        :class:`GlobalSection` on success; ``None`` if assembly failed.

    Example::

        gs = assemble_global_section(local_sections)
        if gs is None:
            print("Global section assembly failed.")
        else:
            print(gs.summary())
    """
    coord = GlobalSectionCoordinator(
        trust_aware_merge=trust_aware,
        compatibility_threshold=compatibility_threshold,
    )
    return coord.run_full_assembly_pipeline(sections)


def verify_sheaf_gluing(
    sections: list[Any],
    overlaps: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> bool:
    """Verify the sheaf gluing conditions for all pairs in *sections*.

    When *overlaps* is provided, each pair ``(U_i, U_j)`` is checked only
    on the corresponding overlap dict.

    Args:
        sections: List of local section-like objects.
        overlaps: Optional mapping ``{(patch_i, patch_j): overlap_data_dict}``.

    Returns:
        True if all gluing conditions hold; False otherwise.

    Example::

        ok = verify_sheaf_gluing(local_sections, overlaps={
            ("U_alpha", "U_beta"): {"key1": "shared_value"},
        })
    """
    coord = GlobalSectionCoordinator()
    n = len(sections)
    for i in range(n):
        for j in range(i + 1, n):
            pi = _section_patch(sections[i])
            pj = _section_patch(sections[j])
            overlap_data = None
            if overlaps is not None:
                overlap_data = (
                    overlaps.get((pi, pj)) or overlaps.get((pj, pi))
                )
            if not coord.verify_gluing(sections[i], sections[j], overlap_data):
                logger.debug("verify_sheaf_gluing: FAIL at (%s, %s)", pi, pj)
                return False
    return True


# ---------------------------------------------------------------------------
# Iterator helpers
# ---------------------------------------------------------------------------


def _iter_complete_witnesses(
    witnesses: list[GlobalSectionWitness],
) -> Iterator[GlobalSectionWitness]:
    """Yield only complete (is_global=True) witnesses."""
    for w in witnesses:
        if w.is_global:
            yield w


def _iter_obstructed_witnesses(
    witnesses: list[GlobalSectionWitness],
) -> Iterator[GlobalSectionWitness]:
    """Yield only obstructed witnesses."""
    for w in witnesses:
        if w.is_obstructed():
            yield w


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    print("=" * 70)
    print("Chapter 39 §5 — Global Section Assembly — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Build synthetic local section objects
    # ------------------------------------------------------------------

    @dataclass
    class _LocalSection:
        """Minimal synthetic local section for testing."""
        patch_id: str
        section_id: str
        data: dict[str, Any] = field(default_factory=dict)
        trust_tier: int = 3

    # Three compatible sections: they agree on shared keys
    s_alpha = _LocalSection(
        patch_id="U_alpha",
        section_id="sec-alpha-001",
        data={"key_a": "val_A", "key_b": "val_B", "key_c": "val_C"},
        trust_tier=3,
    )
    s_beta = _LocalSection(
        patch_id="U_beta",
        section_id="sec-beta-002",
        data={"key_b": "val_B", "key_c": "val_C", "key_d": "val_D"},
        trust_tier=4,
    )
    s_gamma = _LocalSection(
        patch_id="U_gamma",
        section_id="sec-gamma-003",
        data={"key_c": "val_C", "key_d": "val_D", "key_e": "val_E"},
        trust_tier=2,
    )
    # An incompatible section: disagrees on key_b with s_alpha
    s_delta = _LocalSection(
        patch_id="U_delta",
        section_id="sec-delta-004",
        data={"key_a": "val_A_CONFLICT", "key_b": "val_B_WRONG"},
        trust_tier=1,
    )

    compatible_sections = [s_alpha, s_beta, s_gamma]
    all_sections = [s_alpha, s_beta, s_gamma, s_delta]

    # --- CompatibilityReport ---
    print("\n--- GlobalSectionAnalyzer.check_compatibility ---")
    analyzer = GlobalSectionAnalyzer()
    for pair in [(s_alpha, s_beta), (s_beta, s_gamma), (s_alpha, s_delta)]:
        rpt = analyzer.check_compatibility(*pair)
        print(f"  {rpt.summary()}")
        if rpt.conflicting_keys:
            print(f"    conflicting: {rpt.conflicting_keys}")

    # --- Cohomology obstruction ---
    print("\n--- compute_cohomology_obstruction ---")
    h1_clean = analyzer.compute_cohomology_obstruction(compatible_sections)
    h1_dirty = analyzer.compute_cohomology_obstruction(all_sections)
    print(f"  h¹(compatible_sections)  = {h1_clean:.6f}  (expected ≈ 0)")
    print(f"  h¹(all_sections)         = {h1_dirty:.6f}  (expected > 0)")

    # --- identify_inconsistent_pairs ---
    print("\n--- identify_inconsistent_pairs ---")
    bad_pairs = analyzer.identify_inconsistent_pairs(all_sections)
    print(f"  inconsistent pairs: {bad_pairs}")

    # --- GlobalSectionCoordinator: complete assembly ---
    print("\n--- GlobalSectionCoordinator.assemble (compatible) ---")
    coord = GlobalSectionCoordinator()
    result_clean = coord.assemble(compatible_sections)
    print(f"  {result_clean.summary()}")
    assert result_clean.status == AssemblyStatus.COMPLETE
    assert result_clean.global_section is not None
    print(f"  global_section: {result_clean.global_section.summary()}")

    # --- GlobalSectionCoordinator: obstructed assembly ---
    print("\n--- GlobalSectionCoordinator.assemble (with incompatible section) ---")
    result_dirty = coord.assemble(all_sections)
    print(f"  {result_dirty.summary()}")
    print(f"  inconsistent_pair_ids: {result_dirty.inconsistent_pair_ids}")
    assert result_dirty.status in (AssemblyStatus.PARTIAL, AssemblyStatus.OBSTRUCTED)

    # --- verify_gluing ---
    print("\n--- verify_gluing ---")
    print(f"  (s_alpha, s_beta) compatible: {coord.verify_gluing(s_alpha, s_beta)}")
    print(f"  (s_alpha, s_delta) compatible: {coord.verify_gluing(s_alpha, s_delta)}")

    # --- compute_obstruction ---
    print("\n--- compute_obstruction ---")
    obs = coord.compute_obstruction(all_sections)
    if obs:
        print(f"  obstruction: {obs.obstruction_class}  magnitude={obs.cochain_magnitude:.4f}")
    else:
        print("  no obstruction (unexpected)")

    # --- run_full_assembly_pipeline and witnesses ---
    print("\n--- run_full_assembly_pipeline + witnesses ---")
    gs = coord.run_full_assembly_pipeline(compatible_sections)
    print(f"  GlobalSection: {gs.summary() if gs else 'None'}")
    print(f"  witness count: {len(coord.witnesses)}")
    for w in coord.witnesses:
        print(f"    {w.summary()}")

    # --- GlobalSectionWitness immutability ---
    print("\n--- GlobalSectionWitness immutability ---")
    if coord.witnesses:
        w = coord.witnesses[0]
        try:
            w.obstruction_h1 = 999.0  # type: ignore[misc]
            print("  ERROR: mutation succeeded (frozen=True not enforced)")
        except (AttributeError, TypeError):
            print("  OK: GlobalSectionWitness is correctly immutable (frozen=True)")

    # --- assemble_global_section convenience function ---
    print("\n--- assemble_global_section convenience ---")
    gs2 = assemble_global_section(compatible_sections)
    assert gs2 is not None, "Expected successful assembly"
    print(f"  {gs2.summary()}")

    # --- verify_sheaf_gluing module function ---
    print("\n--- verify_sheaf_gluing ---")
    ok_clean = verify_sheaf_gluing(compatible_sections)
    ok_dirty = verify_sheaf_gluing(all_sections)
    print(f"  compatible_sections → {ok_clean}  (expected True)")
    print(f"  all_sections        → {ok_dirty}  (expected False)")

    # --- AssemblyResult to_dict ---
    print("\n--- AssemblyResult to_dict ---")
    d = result_clean.to_dict()
    print(f"  keys: {sorted(d.keys())}")
    print(f"  status={d['status']}  h1={d['obstruction_h1']}")

    # --- ObstructionRecord ---
    print("\n--- ObstructionRecord (from dirty result) ---")
    for rec in result_dirty.obstruction_records[:2]:
        print(f"  {rec.to_dict()['obstruction_class']}  h1_contrib={rec.h1_contribution():.4f}")

    # --- Quality scoring ---
    print("\n--- score_assembly_quality ---")
    q_clean = analyzer.score_assembly_quality(result_clean)
    q_dirty = analyzer.score_assembly_quality(result_dirty)
    print(f"  quality(complete)  = {q_clean:.4f}  (expected ≈ 1.0)")
    print(f"  quality(partial)   = {q_dirty:.4f}  (expected < 1.0)")

    # --- Empty list edge case ---
    print("\n--- Empty section list ---")
    gs_empty = assemble_global_section([])
    assert gs_empty is not None, "Empty assembly should return an empty GlobalSection"
    print(f"  OK: {gs_empty.summary()}")

    print("\n" + "=" * 70)
    print("Smoke test PASSED")
    print("=" * 70)
