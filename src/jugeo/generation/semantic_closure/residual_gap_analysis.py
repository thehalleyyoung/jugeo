r"""Chapter 39, Section 4 — Residual gap analysis.

Theory (theory2.tex §39.4):
    After *n* rounds of integration closure the engine has discharged as many
    semantic obligations as its repair strategies allow.  The obligations that
    remain open after all repair attempts constitute the **residual gap set**
    ``R ⊆ O``.  Understanding R is essential before deciding whether to
    (a) abort the construction, (b) escalate to cross-patch negotiation, or
    (c) accept a weaker notion of global section existence.

    Formally, define

        R(n) = { g ∈ O  |  g is still open after n repair rounds }

    Every element of R(n) can be placed into one of three non-overlapping
    categories (theory2.tex §39.4.2):

    **Locally irreparable** — the gap cannot be discharged by any evidence
    available inside its host patch alone.  The obstruction is *local* in the
    sense that the only feasible repairs require data from a neighbouring chart.
    These gaps are candidates for cross-patch negotiation protocols.

    **Globally removable** — a *different* choice of section assignment over
    the full cover would render the gap non-existent.  The section currently
    installed is not the only valid choice; by backtracking to an earlier
    construction decision and choosing a different branch the obligation
    disappears.  These gaps carry a non-zero ``global_removability_index``.

    **Fundamental obstructions** — gaps whose existence is independent of the
    section assignment.  They witness the fact that no global section exists
    for the chosen cover and gluing data.  Their presence implies a non-trivial
    cohomology class in H¹(X, F).  The construction must either refine the
    cover or relax the sheaf axiom (e.g., by working with stacks or twisted
    sections) before any progress is possible.

    The *residual score* of a gap g is defined as

        ρ(g) = severity_weight(g) · (1 + cross_patch_factor(g)) · age_factor(g)

    where age_factor(g) = 1 + log(1 + age(g)) grows slowly with the number
    of rounds the gap has survived.

    The *total obstruction score* of a residual gap set R is

        Ω(R) = Σ_{g ∈ R_fund} ρ(g)  +  0.5 · Σ_{g ∈ R_cross} ρ(g)

    Fundamental obstructions contribute in full; cross-patch gaps contribute
    at half weight because they are resolvable given sufficient negotiation.
    Locally irreparable and globally removable gaps do not contribute to Ω.

    When Ω(R) > ω_threshold (configurable), the coordinator declares the
    construction *irrecoverably obstructed* and refuses to certify a global
    section.

    copilot: s04-residual-gap-analysis
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
    from jugeo.geometry.descent import DescentResult, LocalSection
    _DESCENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DESCENT_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.geometry.covers import Cover
    _COVERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _COVERS_AVAILABLE = False

try:  # pragma: no cover
    from jugeo.generation.semantic_closure.integration_closure import (
        IntegrationState,
        IntegrationClosureEngine,
        ClosureCertificate,
    )
    _INTEGRATION_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INTEGRATION_AVAILABLE = False

logger = logging.getLogger(__name__)

__all__ = [
    # Enums
    "GapClassification",
    # Dataclasses
    "ResidualGapReport",
    "ResidualGapWitness",
    # Classes
    "ResidualGapAnalyzer",
    "ResidualGapCoordinator",
    # Module-level helpers
    "analyze_residual_gaps",
    "classify_gap_batch",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default obstruction threshold above which the construction is declared
#: irrecoverably obstructed.
DEFAULT_OBSTRUCTION_THRESHOLD: float = 10.0

#: Weight multiplier applied to FUNDAMENTAL_OBSTRUCTION gaps when computing Ω.
FUNDAMENTAL_WEIGHT: float = 1.0

#: Weight multiplier applied to CROSS_PATCH gaps when computing Ω.
CROSS_PATCH_WEIGHT: float = 0.5

#: Severity → numeric weight mapping for residual scoring.
_SEVERITY_WEIGHT: dict[str, float] = {
    "blocking": 4.0,
    "critical": 3.0,
    "major": 2.0,
    "minor": 1.0,
    "info": 0.25,
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GapClassification(str, Enum):
    """Classification of a residual :class:`~jugeo.generation.semantic_closure.models.ClosureGap`.

    The four categories defined in theory2.tex §39.4.2 map directly onto
    these enum members.

    * ``LOCAL``                   — gap can in principle be closed by
      evidence within its own patch but no such evidence was found in the
      allotted rounds.
    * ``CROSS_PATCH``             — gap requires data from a neighbouring
      patch; it is a candidate for cross-patch negotiation.
    * ``GLOBAL_REMOVABLE``        — gap disappears under a different valid
      section assignment; it witnesses a non-optimal construction choice.
    * ``FUNDAMENTAL_OBSTRUCTION`` — gap witnesses a non-trivial H¹ class;
      no reassignment eliminates it.
    """

    LOCAL = "local"
    CROSS_PATCH = "cross_patch"
    GLOBAL_REMOVABLE = "global_removable"
    FUNDAMENTAL_OBSTRUCTION = "fundamental_obstruction"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ResidualGapReport:
    """Summary of all residual gaps produced by :class:`ResidualGapCoordinator`.

    Attributes:
        report_id:              Unique identifier for this report.
        total_gaps:             Total number of residual gaps analysed.
        count_local:            Number of gaps classified as LOCAL.
        count_cross_patch:      Number of gaps classified as CROSS_PATCH.
        count_global_removable: Number of gaps classified as GLOBAL_REMOVABLE.
        count_fundamental:      Number of FUNDAMENTAL_OBSTRUCTION gaps.
        total_obstruction_score: Ω(R) — the weighted obstruction score.
        is_irrecoverable:       True when Ω(R) > obstruction_threshold.
        witnesses:              Ordered list of :class:`ResidualGapWitness`
                                records, sorted by descending residual score.
        repair_hints_by_gap:    Mapping from gap_id to list of hint strings.
        created_at:             Unix timestamp of report creation.
        metadata:               Free-form dict for downstream consumers.
    """

    report_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    total_gaps: int = 0
    count_local: int = 0
    count_cross_patch: int = 0
    count_global_removable: int = 0
    count_fundamental: int = 0
    total_obstruction_score: float = 0.0
    is_irrecoverable: bool = False
    witnesses: list["ResidualGapWitness"] = field(default_factory=list)
    repair_hints_by_gap: dict[str, list[str]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def classification_counts(self) -> dict[str, int]:
        """Return a dict mapping each classification name to its count."""
        return {
            GapClassification.LOCAL.value: self.count_local,
            GapClassification.CROSS_PATCH.value: self.count_cross_patch,
            GapClassification.GLOBAL_REMOVABLE.value: self.count_global_removable,
            GapClassification.FUNDAMENTAL_OBSTRUCTION.value: self.count_fundamental,
        }

    def fundamental_witnesses(self) -> list["ResidualGapWitness"]:
        """Return only those witnesses classified as FUNDAMENTAL_OBSTRUCTION."""
        return [
            w for w in self.witnesses
            if w.classification == GapClassification.FUNDAMENTAL_OBSTRUCTION.value
        ]

    def summary(self) -> str:
        """One-line human-readable summary of this report."""
        status = "IRRECOVERABLE" if self.is_irrecoverable else "recoverable"
        return (
            f"ResidualGapReport[{self.report_id}] "
            f"total={self.total_gaps} "
            f"local={self.count_local} "
            f"cross={self.count_cross_patch} "
            f"removable={self.count_global_removable} "
            f"fundamental={self.count_fundamental} "
            f"Ω={self.total_obstruction_score:.3f} "
            f"status={status}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for logging / persistence."""
        return {
            "report_id": self.report_id,
            "total_gaps": self.total_gaps,
            "counts": self.classification_counts(),
            "total_obstruction_score": self.total_obstruction_score,
            "is_irrecoverable": self.is_irrecoverable,
            "witness_count": len(self.witnesses),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ResidualGapWitness:
    """Immutable record of a witnessed residual gap.

    An instance is created by :class:`ResidualGapCoordinator` for every gap
    that survives all repair rounds.  It captures the full analytical picture
    of that gap at the moment of witness creation.

    Attributes:
        witness_id:        Unique identifier for this witness record.
        gap_id:            The ``gap_id`` of the underlying :class:`ClosureGap`.
        classification:    String value of the :class:`GapClassification` enum.
        residual_score:    ρ(g) — the computed residual severity score.
        cross_patch_deps:  Patch identifiers that this gap depends on.
        repair_cost:       Estimated cost (in abstract units) to close the gap.
        repair_hints:      Ordered suggestions for addressing this gap.
        timestamp:         Unix timestamp at which the witness was created.
    """

    witness_id: str
    gap_id: str
    classification: str
    residual_score: float
    cross_patch_deps: tuple[str, ...]
    repair_cost: float
    repair_hints: tuple[str, ...]
    timestamp: float

    def is_fundamental(self) -> bool:
        """Return True if the classification is FUNDAMENTAL_OBSTRUCTION."""
        return self.classification == GapClassification.FUNDAMENTAL_OBSTRUCTION.value

    def is_cross_patch(self) -> bool:
        """Return True if the classification is CROSS_PATCH."""
        return self.classification == GapClassification.CROSS_PATCH.value

    def contributes_to_obstruction(self) -> bool:
        """Return True if this witness contributes to the global obstruction score Ω(R)."""
        return self.classification in (
            GapClassification.FUNDAMENTAL_OBSTRUCTION.value,
            GapClassification.CROSS_PATCH.value,
        )

    def obstruction_contribution(self) -> float:
        """Return the contribution of this witness to Ω(R)."""
        if self.classification == GapClassification.FUNDAMENTAL_OBSTRUCTION.value:
            return self.residual_score * FUNDAMENTAL_WEIGHT
        if self.classification == GapClassification.CROSS_PATCH.value:
            return self.residual_score * CROSS_PATCH_WEIGHT
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "gap_id": self.gap_id,
            "classification": self.classification,
            "residual_score": self.residual_score,
            "cross_patch_deps": list(self.cross_patch_deps),
            "repair_cost": self.repair_cost,
            "repair_hints": list(self.repair_hints),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _severity_weight(severity: str) -> float:
    """Map a :class:`~jugeo.generation.semantic_closure.models.GapSeverity`
    string to a numeric weight for residual scoring."""
    return _SEVERITY_WEIGHT.get(severity.lower(), 1.0)


def _age_factor(gap: Any) -> float:
    """Compute the age factor 1 + log(1 + age) for *gap*.

    Falls back to 1.0 if the gap object does not expose an ``age`` attribute.
    """
    age = getattr(gap, "age", None)
    if age is None:
        age = getattr(gap, "repair_attempts", 0)
    try:
        return 1.0 + math.log1p(float(age))
    except (TypeError, ValueError):
        return 1.0


def _patch_id_from_gap(gap: Any) -> str:
    """Extract the patch identifier from a gap-like object."""
    for attr in ("patch_id", "patch", "location", "check_id"):
        val = getattr(gap, attr, None)
        if val:
            return str(val)
    return "unknown"


def _gap_id(gap: Any) -> str:
    """Return a stable gap identifier string."""
    for attr in ("gap_id", "id", "obligation_id"):
        val = getattr(gap, attr, None)
        if val:
            return str(val)
    # Fall back to a hash of the repr
    return hashlib.sha1(repr(gap).encode()).hexdigest()[:12]


def _gap_severity_str(gap: Any) -> str:
    """Return the severity string of *gap*, defaulting to 'minor'."""
    sev = getattr(gap, "severity", None)
    if sev is None:
        return "minor"
    if hasattr(sev, "value"):
        return str(sev.value)
    return str(sev).lower()


def _gap_description(gap: Any) -> str:
    """Return a human-readable description of *gap*."""
    for attr in ("description", "message", "reason"):
        val = getattr(gap, attr, None)
        if val:
            return str(val)
    return repr(gap)


# ---------------------------------------------------------------------------
# ResidualGapAnalyzer
# ---------------------------------------------------------------------------


class ResidualGapAnalyzer:
    """Deep analysis of a single residual :class:`ClosureGap`.

    This class provides the per-gap analytical primitives used by
    :class:`ResidualGapCoordinator` to build a :class:`ResidualGapReport`.
    Each method is designed to be callable independently so that callers
    can use only the analyses they need.

    The analyzer is **stateless between calls** — it holds no mutable
    cache and can therefore be shared safely across threads.

    Typical usage::

        analyzer = ResidualGapAnalyzer()
        score = analyzer.compute_residual_score(gap)
        deps  = analyzer.detect_cross_patch_dependency(gap)
        cost  = analyzer.estimate_repair_cost(gap)
        hints = analyzer.find_analogous_closed_gaps(gap, registry)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_residual_score(self, gap: Any) -> float:
        """Compute the residual score ρ(g) for *gap*.

        The formula is::

            ρ(g) = severity_weight(g) · (1 + cross_patch_factor(g)) · age_factor(g)

        where ``cross_patch_factor`` is 1.0 when cross-patch dependencies are
        detected (doubling the score) and 0.0 otherwise.

        Args:
            gap: A :class:`~jugeo.generation.semantic_closure.models.ClosureGap`
                 or any object exposing ``severity``, ``age``/``repair_attempts``,
                 and optionally ``cross_patch_deps``.

        Returns:
            A non-negative float representing the residual severity of the gap.
        """
        sev_w = _severity_weight(_gap_severity_str(gap))
        age_f = _age_factor(gap)
        cross_deps = self.detect_cross_patch_dependency(gap)
        cp_factor = 1.0 if cross_deps else 0.0
        score = sev_w * (1.0 + cp_factor) * age_f
        logger.debug(
            "residual_score gap=%s sev_w=%.2f age_f=%.2f cp_factor=%.1f → ρ=%.4f",
            _gap_id(gap), sev_w, age_f, cp_factor, score,
        )
        return round(score, 6)

    def detect_cross_patch_dependency(self, gap: Any) -> list[str]:
        """Return a list of patch identifiers that *gap* depends on.

        A gap is considered cross-patch if its description or metadata
        references identifiers of the form ``U_<name>`` or ``patch:<name>``,
        or if the gap object carries an explicit ``cross_patch_deps`` field.

        Args:
            gap: Gap-like object to inspect.

        Returns:
            List of cross-patch dependency identifiers (may be empty).
        """
        # Explicit field takes priority
        explicit = getattr(gap, "cross_patch_deps", None)
        if explicit:
            if isinstance(explicit, (list, tuple, set, frozenset)):
                return [str(x) for x in explicit if x]
        # Scan description for patch references
        desc = _gap_description(gap)
        deps: list[str] = []
        import re
        for m in re.finditer(r"\bU_\w+\b|\bpatch:\w+\b|\bsection:\w+\b", desc):
            token = m.group(0)
            host = _patch_id_from_gap(gap)
            if token != host:
                deps.append(token)
        # Also inspect metadata dict if present
        meta: dict[str, Any] = getattr(gap, "metadata", {}) or {}
        for key in ("depends_on", "cross_patch", "neighbour_patches"):
            val = meta.get(key)
            if isinstance(val, (list, tuple)):
                deps.extend(str(v) for v in val)
            elif isinstance(val, str) and val:
                deps.append(val)
        return list(dict.fromkeys(deps))  # deduplicate, preserve order

    def estimate_repair_cost(self, gap: Any) -> float:
        """Estimate the abstract repair cost for *gap*.

        The cost model is::

            cost(g) = base_cost(severity) · (1 + 0.3 · n_cross_deps)
                      · (1 + 0.1 · repair_attempts)

        where ``base_cost`` follows the same scale as ``severity_weight``,
        ``n_cross_deps`` is the number of cross-patch dependencies, and
        ``repair_attempts`` is the number of failed repairs already recorded.

        Args:
            gap: Gap-like object.

        Returns:
            Estimated repair cost as a positive float.
        """
        base = _severity_weight(_gap_severity_str(gap)) * 10.0
        n_cross = len(self.detect_cross_patch_dependency(gap))
        attempts = float(getattr(gap, "repair_attempts", 0) or 0)
        cost = base * (1.0 + 0.3 * n_cross) * (1.0 + 0.1 * attempts)
        return round(cost, 4)

    def find_analogous_closed_gaps(
        self,
        gap: Any,
        registry: list[Any],
    ) -> list[Any]:
        """Find previously-closed gaps that are analogous to *gap*.

        Two gaps are considered analogous if they share the same severity
        and the same patch identifier (or the same obligation prefix).  The
        result is useful for generating repair hints based on what worked
        before.

        Args:
            gap:      The residual gap to look up.
            registry: A list of :class:`~jugeo.generation.semantic_closure.models.ClosureCheck`
                      or similar objects to search.

        Returns:
            Ordered list of analogous records, most-recent first.
        """
        target_sev = _gap_severity_str(gap)
        target_patch = _patch_id_from_gap(gap)
        matches: list[Any] = []
        for record in registry:
            rec_sev = _gap_severity_str(record)
            rec_patch = _patch_id_from_gap(record)
            result = getattr(record, "result", None)
            if hasattr(result, "value"):
                result = result.value
            if (
                rec_sev == target_sev
                and rec_patch == target_patch
                and str(result) == "closed"
            ):
                matches.append(record)
        # Sort by timestamp descending (most recent first)
        matches.sort(key=lambda r: getattr(r, "timestamp", 0.0), reverse=True)
        return matches


# ---------------------------------------------------------------------------
# ResidualGapCoordinator
# ---------------------------------------------------------------------------


class ResidualGapCoordinator:
    """Orchestrates the full residual gap analysis pipeline.

    The coordinator:

    1. Accepts a list of :class:`~jugeo.generation.semantic_closure.models.ClosureGap`
       objects from the integration closure engine.
    2. Classifies each gap via :meth:`classify_gap`.
    3. Prioritises the list via :meth:`prioritize`.
    4. Generates repair hints via :meth:`generate_repair_hints`.
    5. Assembles a :class:`ResidualGapReport` with :class:`ResidualGapWitness`
       records for every gap.

    The coordinator is thread-safe: each call to :meth:`analyze` is fully
    self-contained and does not mutate shared state.

    Args:
        obstruction_threshold: Ω threshold above which the construction is
            declared irrecoverable.  Default :data:`DEFAULT_OBSTRUCTION_THRESHOLD`.
        analyzer:              Injected :class:`ResidualGapAnalyzer` instance.
            If *None*, a fresh default instance is created.
        closed_registry:       Optional list of previously-closed checks used
            for analogous-gap lookup.
        strict_mode:           When *True*, any FUNDAMENTAL_OBSTRUCTION gap
            immediately triggers ``is_irrecoverable = True`` regardless of Ω.

    Example::

        coord = ResidualGapCoordinator(obstruction_threshold=5.0)
        report = coord.analyze(open_gaps)
        print(report.summary())
    """

    def __init__(
        self,
        obstruction_threshold: float = DEFAULT_OBSTRUCTION_THRESHOLD,
        analyzer: ResidualGapAnalyzer | None = None,
        closed_registry: list[Any] | None = None,
        strict_mode: bool = False,
    ) -> None:
        self.obstruction_threshold = obstruction_threshold
        self.analyzer = analyzer or ResidualGapAnalyzer()
        self.closed_registry: list[Any] = list(closed_registry or [])
        self.strict_mode = strict_mode
        logger.debug(
            "ResidualGapCoordinator initialised threshold=%.2f strict=%s",
            self.obstruction_threshold, self.strict_mode,
        )

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def analyze(self, gap_list: list[Any]) -> ResidualGapReport:
        """Run the full residual gap analysis pipeline on *gap_list*.

        This is the primary entry point.  It classifies every gap, scores it,
        computes per-gap repair hints, builds witnesses, and assembles the
        final :class:`ResidualGapReport`.

        Args:
            gap_list: List of open (un-closed) gap-like objects.  May be
                empty, in which case the report will have zero gaps and
                ``is_irrecoverable = False``.

        Returns:
            A fully-populated :class:`ResidualGapReport`.
        """
        logger.info(
            "ResidualGapCoordinator.analyze: %d gaps to analyse", len(gap_list)
        )
        report = ResidualGapReport(
            report_id=uuid.uuid4().hex[:16],
            total_gaps=len(gap_list),
            created_at=time.time(),
        )
        if not gap_list:
            logger.info("No residual gaps — analysis trivially complete.")
            return report

        prioritised = self.prioritize(gap_list)
        witnesses: list[ResidualGapWitness] = []
        obstruction_score = 0.0
        counts: dict[str, int] = {c.value: 0 for c in GapClassification}

        for gap in prioritised:
            gap_id = _gap_id(gap)
            classification = self.classify_gap(gap)
            residual_score = self.analyzer.compute_residual_score(gap)
            cross_deps = self.analyzer.detect_cross_patch_dependency(gap)
            repair_cost = self.analyzer.estimate_repair_cost(gap)
            hints = self.generate_repair_hints(gap)
            analogous = self.analyzer.find_analogous_closed_gaps(
                gap, self.closed_registry
            )
            if analogous:
                extra_hint = (
                    f"Analogous gap closed previously via: "
                    f"{_gap_description(analogous[0])[:80]}"
                )
                if extra_hint not in hints:
                    hints.append(extra_hint)

            witness = ResidualGapWitness(
                witness_id=uuid.uuid4().hex[:16],
                gap_id=gap_id,
                classification=classification.value,
                residual_score=residual_score,
                cross_patch_deps=tuple(cross_deps),
                repair_cost=repair_cost,
                repair_hints=tuple(hints),
                timestamp=time.time(),
            )
            witnesses.append(witness)
            counts[classification.value] += 1
            obstruction_score += witness.obstruction_contribution()
            report.repair_hints_by_gap[gap_id] = hints
            logger.debug(
                "gap=%s class=%s ρ=%.4f Ω_contrib=%.4f",
                gap_id, classification.value, residual_score,
                witness.obstruction_contribution(),
            )

        report.witnesses = sorted(
            witnesses, key=lambda w: w.residual_score, reverse=True
        )
        report.count_local = counts[GapClassification.LOCAL.value]
        report.count_cross_patch = counts[GapClassification.CROSS_PATCH.value]
        report.count_global_removable = counts[GapClassification.GLOBAL_REMOVABLE.value]
        report.count_fundamental = counts[GapClassification.FUNDAMENTAL_OBSTRUCTION.value]
        report.total_obstruction_score = round(obstruction_score, 6)
        report.is_irrecoverable = self._check_irrecoverable(report)
        logger.info(
            "Analysis complete: %s", report.summary()
        )
        return report

    def classify_gap(self, gap: Any) -> GapClassification:
        """Classify a single *gap* into a :class:`GapClassification`.

        The classification algorithm (theory2.tex §39.4.2):

        1. If the gap carries an explicit ``classification`` field, honour it.
        2. If cross-patch dependencies are detected → ``CROSS_PATCH``.
        3. If the gap's ``global_removability_index`` > 0 → ``GLOBAL_REMOVABLE``.
        4. If the gap severity is ``blocking`` or ``critical`` and the repair
           attempt count exceeds 3 → ``FUNDAMENTAL_OBSTRUCTION``.
        5. Otherwise → ``LOCAL``.

        Args:
            gap: Gap-like object.

        Returns:
            The appropriate :class:`GapClassification` member.
        """
        # Honour explicit override
        explicit = getattr(gap, "classification", None)
        if explicit is not None:
            try:
                return GapClassification(str(explicit))
            except ValueError:
                pass

        cross_deps = self.analyzer.detect_cross_patch_dependency(gap)
        if cross_deps:
            return GapClassification.CROSS_PATCH

        gri = float(getattr(gap, "global_removability_index", 0) or 0)
        if gri > 0.0:
            return GapClassification.GLOBAL_REMOVABLE

        attempts = int(getattr(gap, "repair_attempts", 0) or 0)
        sev = _gap_severity_str(gap)
        if sev in ("blocking", "critical") and attempts > 3:
            return GapClassification.FUNDAMENTAL_OBSTRUCTION

        return GapClassification.LOCAL

    def prioritize(self, gaps: list[Any]) -> list[Any]:
        """Return *gaps* sorted by descending residual score.

        Ties in residual score are broken by severity (higher severity first),
        then by gap_id for deterministic ordering.

        Args:
            gaps: Unsorted list of gap-like objects.

        Returns:
            New list sorted in priority order (highest-priority first).
        """
        _sev_rank = {"blocking": 5, "critical": 4, "major": 3, "minor": 2, "info": 1}

        def _sort_key(g: Any) -> tuple[float, int, str]:
            score = self.analyzer.compute_residual_score(g)
            sev_n = _sev_rank.get(_gap_severity_str(g), 0)
            gid = _gap_id(g)
            return (-score, -sev_n, gid)

        return sorted(gaps, key=_sort_key)

    def generate_repair_hints(self, gap: Any) -> list[str]:
        """Generate a list of actionable repair hints for *gap*.

        Hints are generated from three sources:

        1. The gap's own ``suggested_fix`` / ``remediation`` field (if any).
        2. Classification-specific heuristic hints.
        3. Cross-patch dependency hints when applicable.

        Args:
            gap: Gap-like object.

        Returns:
            Ordered list of hint strings, most actionable first.
        """
        hints: list[str] = []
        # 1. Explicit suggestions on the gap object itself
        for attr in ("suggested_fix", "remediation", "hint", "notes"):
            val = getattr(gap, attr, None)
            if val and isinstance(val, str):
                hints.append(val)
                break

        classification = self.classify_gap(gap)
        sev = _gap_severity_str(gap)
        patch = _patch_id_from_gap(gap)

        # 2. Classification-driven hints
        if classification == GapClassification.LOCAL:
            hints.append(
                f"[LOCAL] Re-examine evidence within patch {patch!r} "
                f"for obligation {_gap_id(gap)!r}."
            )
            hints.append(
                f"[LOCAL] Consider adding a direct semantic annotation "
                f"to satisfy the {sev!r} obligation."
            )
        elif classification == GapClassification.CROSS_PATCH:
            cross_deps = self.analyzer.detect_cross_patch_dependency(gap)
            deps_str = ", ".join(cross_deps[:3])
            hints.append(
                f"[CROSS-PATCH] Initiate negotiation with neighbouring "
                f"patches: {deps_str}."
            )
            hints.append(
                f"[CROSS-PATCH] Verify that the overlap treaty between "
                f"{patch!r} and {deps_str!r} covers this obligation."
            )
        elif classification == GapClassification.GLOBAL_REMOVABLE:
            hints.append(
                "[GLOBAL-REMOVABLE] Backtrack to the section assignment "
                "decision point and select an alternative branch."
            )
            hints.append(
                "[GLOBAL-REMOVABLE] Use the global removability index to "
                "identify the divergence node in the construction tree."
            )
        elif classification == GapClassification.FUNDAMENTAL_OBSTRUCTION:
            hints.append(
                "[FUNDAMENTAL] This gap witnesses a non-trivial H¹ class. "
                "Consider refining the cover or relaxing the sheaf axiom."
            )
            hints.append(
                "[FUNDAMENTAL] Inspect the Čech 1-cocycle associated with "
                f"patch {patch!r} for a candidate obstruction class."
            )
            hints.append(
                "[FUNDAMENTAL] If refinement is not feasible, document "
                "this gap as a known limitation in the closure certificate."
            )

        # 3. Cross-patch dependency list if present
        cross_deps = self.analyzer.detect_cross_patch_dependency(gap)
        if cross_deps:
            hints.append(
                f"[INFO] Detected {len(cross_deps)} cross-patch "
                f"dependenc{'y' if len(cross_deps) == 1 else 'ies'}: "
                + ", ".join(cross_deps)
            )

        return hints

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_irrecoverable(self, report: ResidualGapReport) -> bool:
        """Decide whether *report* represents an irrecoverable obstruction."""
        if self.strict_mode and report.count_fundamental > 0:
            logger.warning(
                "strict_mode: fundamental obstruction detected — "
                "declaring irrecoverable."
            )
            return True
        if report.total_obstruction_score > self.obstruction_threshold:
            logger.warning(
                "Ω=%.3f > threshold=%.3f — declaring irrecoverable.",
                report.total_obstruction_score, self.obstruction_threshold,
            )
            return True
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def analyze_residual_gaps(
    gaps: list[Any],
    threshold: float = DEFAULT_OBSTRUCTION_THRESHOLD,
    strict_mode: bool = False,
) -> ResidualGapReport:
    """Convenience wrapper: analyse *gaps* and return a :class:`ResidualGapReport`.

    Constructs a :class:`ResidualGapCoordinator` with *threshold* and delegates
    to :meth:`~ResidualGapCoordinator.analyze`.

    Args:
        gaps:        List of open gap-like objects.
        threshold:   Obstruction threshold for ``is_irrecoverable``.
        strict_mode: Passed through to :class:`ResidualGapCoordinator`.

    Returns:
        Fully-populated :class:`ResidualGapReport`.

    Example::

        report = analyze_residual_gaps(open_gaps, threshold=8.0)
        if report.is_irrecoverable:
            raise RuntimeError("Construction is irrecoverably obstructed.")
    """
    coord = ResidualGapCoordinator(
        obstruction_threshold=threshold, strict_mode=strict_mode
    )
    return coord.analyze(gaps)


def classify_gap_batch(gaps: list[Any]) -> dict[str, GapClassification]:
    """Classify every gap in *gaps* and return a dict from gap_id to classification.

    Uses a default :class:`ResidualGapCoordinator` (obstruction_threshold=10).
    This is useful when you only need classifications without the full report.

    Args:
        gaps: List of gap-like objects.

    Returns:
        Mapping ``{gap_id: GapClassification}`` for every gap in *gaps*.

    Example::

        classifications = classify_gap_batch(open_gaps)
        fundamentals = [g for g,c in classifications.items()
                        if c == GapClassification.FUNDAMENTAL_OBSTRUCTION]
    """
    coord = ResidualGapCoordinator()
    return {_gap_id(g): coord.classify_gap(g) for g in gaps}


# ---------------------------------------------------------------------------
# Iterator helpers
# ---------------------------------------------------------------------------


def _iter_fundamental(report: ResidualGapReport) -> Iterator[ResidualGapWitness]:
    """Yield witnesses classified as FUNDAMENTAL_OBSTRUCTION."""
    yield from report.fundamental_witnesses()


def _iter_cross_patch(report: ResidualGapReport) -> Iterator[ResidualGapWitness]:
    """Yield witnesses classified as CROSS_PATCH."""
    for w in report.witnesses:
        if w.is_cross_patch():
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
    print("Chapter 39 §4 — Residual Gap Analysis — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Build synthetic gap objects without needing the full models import
    # ------------------------------------------------------------------

    @dataclass
    class _SyntheticGap:
        gap_id: str
        severity: str
        patch_id: str
        description: str
        repair_attempts: int = 0
        age: int = 0
        global_removability_index: float = 0.0
        cross_patch_deps: list[str] = field(default_factory=list)
        metadata: dict[str, Any] = field(default_factory=dict)
        suggested_fix: str = ""

    gaps_input: list[_SyntheticGap] = [
        _SyntheticGap(
            gap_id="gap-alpha-001",
            severity="critical",
            patch_id="U_alpha",
            description="Missing evidence for proposition P over U_alpha",
            repair_attempts=2,
            age=3,
        ),
        _SyntheticGap(
            gap_id="gap-beta-002",
            severity="blocking",
            patch_id="U_beta",
            description="Overlap treaty between U_beta and U_gamma is violated",
            repair_attempts=5,
            age=7,
            cross_patch_deps=["U_gamma"],
        ),
        _SyntheticGap(
            gap_id="gap-gamma-003",
            severity="major",
            patch_id="U_gamma",
            description="Alternative section assignment removes this gap",
            repair_attempts=1,
            age=1,
            global_removability_index=0.8,
        ),
        _SyntheticGap(
            gap_id="gap-delta-004",
            severity="minor",
            patch_id="U_delta",
            description="Local evidence just outside the search radius",
            repair_attempts=0,
            age=0,
        ),
        _SyntheticGap(
            gap_id="gap-epsilon-005",
            severity="blocking",
            patch_id="U_epsilon",
            description="Fundamental cohomology obstruction witnessed at patch:U_epsilon",
            repair_attempts=6,
            age=10,
        ),
    ]

    # --- Test ResidualGapAnalyzer directly ---
    print("\n--- ResidualGapAnalyzer ---")
    analyzer = ResidualGapAnalyzer()
    for g in gaps_input:
        score = analyzer.compute_residual_score(g)
        deps = analyzer.detect_cross_patch_dependency(g)
        cost = analyzer.estimate_repair_cost(g)
        print(
            f"  gap={g.gap_id:25s}  ρ={score:6.3f}  "
            f"cost={cost:7.3f}  cross_deps={deps}"
        )

    # --- Test ResidualGapCoordinator.classify_gap ---
    print("\n--- Gap classifications ---")
    coord = ResidualGapCoordinator(obstruction_threshold=8.0, strict_mode=False)
    for g in gaps_input:
        cls = coord.classify_gap(g)
        print(f"  {g.gap_id:25s}  →  {cls.value}")

    # --- Test classify_gap_batch ---
    print("\n--- classify_gap_batch ---")
    batch = classify_gap_batch(gaps_input)
    for gid, cls in batch.items():
        print(f"  {gid:25s}  →  {cls.value}")

    # --- Full analysis pipeline ---
    print("\n--- Full analysis pipeline ---")
    report = coord.analyze(gaps_input)
    print(f"  {report.summary()}")
    print(f"  is_irrecoverable: {report.is_irrecoverable}")
    print(f"  Ω = {report.total_obstruction_score:.4f}")
    print(f"  classification counts: {report.classification_counts()}")
    print(f"  witnesses ({len(report.witnesses)}):")
    for w in report.witnesses:
        print(
            f"    {w.witness_id[:8]}  gap={w.gap_id:25s}  "
            f"class={w.classification:28s}  ρ={w.residual_score:.3f}  "
            f"Ω_contrib={w.obstruction_contribution():.3f}"
        )
        for hint in w.repair_hints[:2]:
            print(f"      hint: {hint[:80]}")

    # --- analyze_residual_gaps convenience function ---
    print("\n--- analyze_residual_gaps (strict_mode=True) ---")
    strict_report = analyze_residual_gaps(gaps_input, threshold=8.0, strict_mode=True)
    print(f"  {strict_report.summary()}")

    # --- ResidualGapWitness immutability check ---
    print("\n--- ResidualGapWitness immutability ---")
    w0 = report.witnesses[0]
    try:
        w0.residual_score = 999.0  # type: ignore[misc]
        print("  ERROR: mutation succeeded (frozen=True not enforced)")
    except (AttributeError, TypeError):
        print("  OK: ResidualGapWitness is correctly immutable (frozen=True)")

    # --- to_dict serialisation ---
    print("\n--- to_dict serialisation ---")
    d = report.to_dict()
    print(f"  report keys: {sorted(d.keys())}")
    print(f"  total_gaps={d['total_gaps']}  Ω={d['total_obstruction_score']:.4f}")

    # --- Empty gap list edge case ---
    print("\n--- Empty gap list ---")
    empty_report = analyze_residual_gaps([])
    print(f"  {empty_report.summary()}")
    assert empty_report.total_gaps == 0
    assert not empty_report.is_irrecoverable

    print("\n" + "=" * 70)
    print("Smoke test PASSED")
    print("=" * 70)
