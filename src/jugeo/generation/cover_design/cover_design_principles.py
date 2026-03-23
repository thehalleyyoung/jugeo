r"""Cover design principles for the cover_design sub-package (Stage 1).

Theory (theory2.tex §43.1 — Cover Design Principles):
    Stage 1 of the cover design pipeline establishes the fundamental
    principles that any admissible cover of the judgment site J must satisfy.
    A *cover* is a finite collection of patches {U_i} such that:

        1. **Completeness** — every point x ∈ J belongs to at least one U_i:

               J ⊆ ⋃_i  U_i

        2. **Čech compatibility** — on every non-empty pairwise intersection
           U_i ∩ U_j the locally constructed sections s_i and s_j must agree:

               ∀ i≠j :  s_i |_{U_i ∩ U_j}  =  s_j |_{U_i ∩ U_j}

        3. **Minimality** — no patch U_k is redundant, i.e. removing any single
           patch U_k would violate completeness:

               ∀ k :  J ⊄ ⋃_{i ≠ k}  U_i

    These three design principles are encoded in :class:`CoverPrinciple` and
    their violations are recorded as :class:`PrincipleViolation` objects.

    The :class:`CoverDesignPrinciplesCoordinator` orchestrates the full
    analysis pipeline, delegating geometry analysis to the
    :class:`CoverDesignPrinciplesAnalyzer` and issuing a formal witness via
    :class:`CoverDesignPrinciplesWitness`.

    **Trust tier**: generated cover designs enter at the *PROPOSAL* tier and
    must be promoted by the witness before being consumed by downstream stages.

    copilot: s01-cover-design-principles

References
----------
theory2.tex  §43.1 (Cover design principles), §43.2 (Čech condition),
             §43.3 (Minimality), §38 (Budget as first-class object)

Usage::

    from jugeo.generation.cover_design.cover_design_principles import (
        CoverDesignPrinciplesCoordinator,
        CoverDesignPrinciplesAnalyzer,
        CoverDesignPrinciplesWitness,
        CoverageGap,
        CoverPrinciple,
        PrincipleViolation,
    )

    coordinator = CoverDesignPrinciplesCoordinator()
    plan = coordinator.run(
        site_points=frozenset(["p1", "p2", "p3", "p4"]),
        candidate_patches={
            "U1": frozenset(["p1", "p2"]),
            "U2": frozenset(["p2", "p3"]),
            "U3": frozenset(["p3", "p4"]),
        },
        sections={
            "U1": {"p2": "v_a"},
            "U2": {"p2": "v_a", "p3": "v_b"},
            "U3": {"p3": "v_b"},
        },
    )
    print(plan.phase, plan.violations)
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.generation.cover_design.models import (
        Budget,
        CoverDesignPhase,
        CoverDesignPlan,
        PatchDescriptor,
    )
except ImportError:
    Budget = Any  # type: ignore[assignment,misc]
    CoverDesignPhase = Any  # type: ignore[assignment,misc]
    CoverDesignPlan = Any  # type: ignore[assignment,misc]
    PatchDescriptor = Any  # type: ignore[assignment,misc]

__all__ = [
    "CoverageGap",
    "CoverPrinciple",
    "PrincipleViolation",
    "CoverDesignPrinciplesCoordinator",
    "CoverDesignPrinciplesAnalyzer",
    "CoverDesignPrinciplesWitness",
]

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class _PrincipleKind(str, Enum):
    """Which of the three design principles a :class:`CoverPrinciple` encodes."""

    COMPLETENESS = "completeness"
    CECH_COMPATIBILITY = "cech_compatibility"
    MINIMALITY = "minimality"


# ---------------------------------------------------------------------------
# Helper dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """Describes a region of the judgment site that is not covered by any patch.

    Attributes
    ----------
    gap_id:
        Unique identifier for this gap record.
    uncovered_points:
        Frozenset of site-point identifiers that belong to no patch.
    severity:
        A scalar in [0, 1] indicating how serious the gap is; 1.0 means
        *all* site points are uncovered.
    detected_at:
        Unix timestamp when this gap was detected.
    context:
        Optional free-form dictionary for additional diagnostic data.
    """

    gap_id: str
    uncovered_points: frozenset[str]
    severity: float
    detected_at: float
    context: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"CoverageGap(gap_id={self.gap_id!r}, "
            f"uncovered={len(self.uncovered_points)} pts, "
            f"severity={self.severity:.3f})"
        )

    @property
    def is_critical(self) -> bool:
        """Return ``True`` if severity is above the critical threshold (0.5)."""
        return self.severity > 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "uncovered_points": sorted(self.uncovered_points),
            "severity": self.severity,
            "detected_at": self.detected_at,
            "is_critical": self.is_critical,
            "context": self.context,
        }


@dataclass(frozen=True, slots=True)
class CoverPrinciple:
    """Encodes one of the three admissibility conditions a cover must satisfy.

    Attributes
    ----------
    principle_id:
        Unique identifier for this principle instance.
    kind:
        Which of the three design principles this record encodes
        (completeness, Čech compatibility, or minimality).
    description:
        Human-readable statement of the principle.
    formal_statement:
        A LaTeX-style formal statement referencing theory2.tex notation.
    weight:
        Relative importance when computing aggregate compliance scores.
    """

    principle_id: str
    kind: str
    description: str
    formal_statement: str
    weight: float = 1.0

    def __repr__(self) -> str:
        return (
            f"CoverPrinciple(id={self.principle_id!r}, "
            f"kind={self.kind!r}, weight={self.weight})"
        )

    def is_structural(self) -> bool:
        """Return ``True`` for structural principles (completeness, minimality)."""
        return self.kind in (
            _PrincipleKind.COMPLETENESS.value,
            _PrincipleKind.MINIMALITY.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "principle_id": self.principle_id,
            "kind": self.kind,
            "description": self.description,
            "formal_statement": self.formal_statement,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class PrincipleViolation:
    """Records a single violation of a :class:`CoverPrinciple`.

    Attributes
    ----------
    violation_id:
        Unique identifier for this violation record.
    principle_id:
        The principle that was violated.
    offending_patches:
        The patch identifiers directly involved in the violation.
    offending_points:
        The site points at which the violation is witnessed.
    message:
        Human-readable description of the violation.
    remediation_hint:
        Optional suggestion for how to fix the violation.
    detected_at:
        Unix timestamp when the violation was first recorded.
    """

    violation_id: str
    principle_id: str
    offending_patches: frozenset[str]
    offending_points: frozenset[str]
    message: str
    remediation_hint: str = ""
    detected_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return (
            f"PrincipleViolation(id={self.violation_id!r}, "
            f"principle={self.principle_id!r}, "
            f"patches={sorted(self.offending_patches)}, "
            f"pts={len(self.offending_points)})"
        )

    def is_fixable(self) -> bool:
        """Return ``True`` if a remediation hint is available."""
        return bool(self.remediation_hint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_id": self.violation_id,
            "principle_id": self.principle_id,
            "offending_patches": sorted(self.offending_patches),
            "offending_points": sorted(self.offending_points),
            "message": self.message,
            "remediation_hint": self.remediation_hint,
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# Internal plan object (used when models import fails)
# ---------------------------------------------------------------------------


@dataclass
class _FallbackCoverDesignPlan:
    """Minimal stand-in for CoverDesignPlan when the models module is absent."""

    plan_id: str
    phase: str
    site_points: frozenset[str]
    patches: dict[str, frozenset[str]]
    violations: list[PrincipleViolation]
    gaps: list[CoverageGap]
    compliance_score: float
    certified: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"CoverDesignPlan(id={self.plan_id!r}, phase={self.phase!r}, "
            f"violations={len(self.violations)}, "
            f"certified={self.certified})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "phase": self.phase,
            "site_points": sorted(self.site_points),
            "patches": {k: sorted(v) for k, v in self.patches.items()},
            "violations": [v.to_dict() for v in self.violations],
            "gaps": [g.to_dict() for g in self.gaps],
            "compliance_score": self.compliance_score,
            "certified": self.certified,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Built-in principles catalogue
# ---------------------------------------------------------------------------

_COMPLETENESS_PRINCIPLE = CoverPrinciple(
    principle_id="P-COMPLETENESS",
    kind=_PrincipleKind.COMPLETENESS.value,
    description=(
        "Every point of the judgment site J must belong to at least one patch U_i."
    ),
    formal_statement=r"J \subseteq \bigcup_i U_i",
    weight=1.0,
)

_CECH_PRINCIPLE = CoverPrinciple(
    principle_id="P-CECH",
    kind=_PrincipleKind.CECH_COMPATIBILITY.value,
    description=(
        "On every non-empty intersection U_i ∩ U_j the sections s_i and s_j must agree."
    ),
    formal_statement=r"\forall i\neq j:\; s_i|_{U_i\cap U_j} = s_j|_{U_i\cap U_j}",
    weight=1.5,
)

_MINIMALITY_PRINCIPLE = CoverPrinciple(
    principle_id="P-MINIMALITY",
    kind=_PrincipleKind.MINIMALITY.value,
    description=(
        "No patch U_k may be removed without violating completeness."
    ),
    formal_statement=r"\forall k:\; J \not\subseteq \bigcup_{i\neq k} U_i",
    weight=0.8,
)

_DEFAULT_PRINCIPLES: tuple[CoverPrinciple, ...] = (
    _COMPLETENESS_PRINCIPLE,
    _CECH_PRINCIPLE,
    _MINIMALITY_PRINCIPLE,
)


# ---------------------------------------------------------------------------
# CoverDesignPrinciplesAnalyzer
# ---------------------------------------------------------------------------


class CoverDesignPrinciplesAnalyzer:
    """Analyses the judgment-site geometry to determine coverage requirements.

    The analyser inspects a candidate collection of patches against the
    full set of site points and detects:

    - **Coverage gaps** — regions of J not contained in any patch.
    - **Čech violations** — overlapping patches whose sections disagree.
    - **Redundant patches** — patches whose removal would not create any gap.

    Parameters
    ----------
    principles:
        The ordered tuple of :class:`CoverPrinciple` objects to enforce.
        Defaults to the standard three-principle catalogue.
    section_equality:
        A callable ``(val_i, val_j) -> bool`` used to test section equality
        on overlaps.  Defaults to ``==``.
    """

    def __init__(
        self,
        principles: tuple[CoverPrinciple, ...] = _DEFAULT_PRINCIPLES,
        section_equality: Any = None,
    ) -> None:
        self._principles = principles
        self._eq = section_equality if section_equality is not None else (lambda a, b: a == b)
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_gaps(
        self,
        site_points: frozenset[str],
        patches: dict[str, frozenset[str]],
    ) -> list[CoverageGap]:
        """Return a list of :class:`CoverageGap` objects for uncovered regions.

        Parameters
        ----------
        site_points:
            The complete set of points in the judgment site J.
        patches:
            Mapping patch-id → frozenset of points covered by that patch.

        Returns
        -------
        list[CoverageGap]
            One gap per connected uncovered region; empty if fully covered.
        """
        covered: set[str] = set()
        for pts in patches.values():
            covered |= pts

        uncovered = frozenset(site_points - covered)
        if not uncovered:
            return []

        severity = len(uncovered) / max(len(site_points), 1)
        gap = CoverageGap(
            gap_id=str(uuid.uuid4()),
            uncovered_points=uncovered,
            severity=severity,
            detected_at=time.time(),
            context={"site_size": len(site_points), "patch_count": len(patches)},
        )
        self._logger.info(
            "Detected coverage gap: %d/%d points uncovered (severity=%.3f)",
            len(uncovered),
            len(site_points),
            severity,
        )
        return [gap]

    def detect_cech_violations(
        self,
        patches: dict[str, frozenset[str]],
        sections: dict[str, dict[str, Any]],
    ) -> list[PrincipleViolation]:
        """Detect Čech-compatibility violations across all patch pairs.

        For every pair (i, j) with i < j, we examine the intersection
        ``patches[i] ∩ patches[j]`` and check that the section values agree
        on every shared point.

        Parameters
        ----------
        patches:
            Mapping patch-id → frozenset of point identifiers.
        sections:
            Mapping patch-id → (point-id → section value).

        Returns
        -------
        list[PrincipleViolation]
            One violation per incompatible pair; empty if all overlaps agree.
        """
        patch_ids = sorted(patches)
        violations: list[PrincipleViolation] = []

        for idx_i, pi in enumerate(patch_ids):
            for pj in patch_ids[idx_i + 1:]:
                overlap = patches[pi] & patches[pj]
                if not overlap:
                    continue

                conflicting_points: set[str] = set()
                for pt in overlap:
                    val_i = sections.get(pi, {}).get(pt)
                    val_j = sections.get(pj, {}).get(pt)
                    if val_i is None or val_j is None:
                        # Missing section data is treated as a potential conflict
                        if val_i != val_j:
                            conflicting_points.add(pt)
                    elif not self._eq(val_i, val_j):
                        conflicting_points.add(pt)

                if conflicting_points:
                    v = PrincipleViolation(
                        violation_id=str(uuid.uuid4()),
                        principle_id=_CECH_PRINCIPLE.principle_id,
                        offending_patches=frozenset({pi, pj}),
                        offending_points=frozenset(conflicting_points),
                        message=(
                            f"Patches {pi!r} and {pj!r} disagree on "
                            f"{len(conflicting_points)} overlap point(s): "
                            f"{sorted(conflicting_points)!r}"
                        ),
                        remediation_hint=(
                            f"Reconcile section values for {pi!r} and {pj!r} "
                            f"on their shared points, or shrink the overlap."
                        ),
                        detected_at=time.time(),
                    )
                    violations.append(v)
                    self._logger.warning(
                        "Čech violation between %r and %r on %d points",
                        pi,
                        pj,
                        len(conflicting_points),
                    )

        return violations

    def detect_redundant_patches(
        self,
        site_points: frozenset[str],
        patches: dict[str, frozenset[str]],
    ) -> list[PrincipleViolation]:
        """Identify patches whose removal would not leave any site point uncovered.

        A patch U_k is *redundant* iff every point of U_k is already
        covered by at least one other patch.

        Parameters
        ----------
        site_points:
            The complete set of points in J.
        patches:
            Mapping patch-id → frozenset of points.

        Returns
        -------
        list[PrincipleViolation]
            One violation per redundant patch; empty if the cover is minimal.
        """
        violations: list[PrincipleViolation] = []

        for candidate_id, candidate_pts in patches.items():
            # Build the union of all *other* patches
            others_union: set[str] = set()
            for pid, pts in patches.items():
                if pid != candidate_id:
                    others_union |= pts

            # If every point of candidate_pts is already in others_union,
            # then candidate_id is redundant
            if candidate_pts <= others_union:
                v = PrincipleViolation(
                    violation_id=str(uuid.uuid4()),
                    principle_id=_MINIMALITY_PRINCIPLE.principle_id,
                    offending_patches=frozenset({candidate_id}),
                    offending_points=candidate_pts,
                    message=(
                        f"Patch {candidate_id!r} is redundant: all "
                        f"{len(candidate_pts)} of its points are covered "
                        f"by the remaining patches."
                    ),
                    remediation_hint=(
                        f"Remove {candidate_id!r} from the cover to satisfy minimality."
                    ),
                    detected_at=time.time(),
                )
                violations.append(v)
                self._logger.info("Redundant patch detected: %r", candidate_id)

        return violations

    def compute_compliance_score(
        self,
        violations: list[PrincipleViolation],
    ) -> float:
        """Return an aggregate compliance score in [0, 1].

        Weights are drawn from the ``weight`` field of each
        :class:`CoverPrinciple`.  A perfect cover (no violations) scores 1.0.

        Parameters
        ----------
        violations:
            All detected violations.

        Returns
        -------
        float
            1.0 if no violations; decreases proportionally to violation weight.
        """
        if not violations:
            return 1.0

        total_weight = sum(p.weight for p in self._principles)
        if total_weight == 0.0:
            return 0.0

        # Aggregate penalty per principle
        principle_index = {p.principle_id: p for p in self._principles}
        penalty_per_principle: dict[str, float] = {}
        for v in violations:
            pid = v.principle_id
            principle = principle_index.get(pid)
            w = principle.weight if principle else 1.0
            penalty_per_principle[pid] = max(penalty_per_principle.get(pid, 0.0), w)

        total_penalty = sum(penalty_per_principle.values())
        score = max(0.0, 1.0 - total_penalty / total_weight)
        return score

    def full_analysis(
        self,
        site_points: frozenset[str],
        patches: dict[str, frozenset[str]],
        sections: dict[str, dict[str, Any]],
    ) -> tuple[list[CoverageGap], list[PrincipleViolation], float]:
        """Run all three checks and return (gaps, violations, compliance_score).

        Parameters
        ----------
        site_points:
            The complete judgment-site point set.
        patches:
            Mapping patch-id → point frozenset.
        sections:
            Mapping patch-id → section value dict.

        Returns
        -------
        (gaps, violations, compliance_score)
        """
        gaps = self.detect_gaps(site_points, patches)
        completeness_violations: list[PrincipleViolation] = []
        for gap in gaps:
            completeness_violations.append(
                PrincipleViolation(
                    violation_id=str(uuid.uuid4()),
                    principle_id=_COMPLETENESS_PRINCIPLE.principle_id,
                    offending_patches=frozenset(),
                    offending_points=gap.uncovered_points,
                    message=(
                        f"Coverage gap: {len(gap.uncovered_points)} site point(s) "
                        f"are not covered by any patch."
                    ),
                    remediation_hint="Add a new patch that covers the uncovered points.",
                    detected_at=gap.detected_at,
                )
            )

        cech_violations = self.detect_cech_violations(patches, sections)
        redundancy_violations = self.detect_redundant_patches(site_points, patches)

        all_violations = completeness_violations + cech_violations + redundancy_violations
        score = self.compute_compliance_score(all_violations)
        return gaps, all_violations, score


# ---------------------------------------------------------------------------
# CoverDesignPrinciplesWitness
# ---------------------------------------------------------------------------


class CoverDesignPrinciplesWitness:
    """Certifies that a cover satisfies all three design principles.

    A witness record is issued only when:

    1. There are no coverage gaps (completeness holds).
    2. There are no Čech violations (sections agree on all overlaps).
    3. There are no redundant patches (minimality holds).

    When the cover has *known* violations the witness can still issue a
    *conditional* certificate at a reduced trust level, recording the
    violations as outstanding obligations.

    Parameters
    ----------
    require_minimality:
        If ``False``, redundancy violations do not block certification.
        Useful during early design stages when patches may still be pruned.
    """

    def __init__(self, require_minimality: bool = True) -> None:
        self._require_minimality = require_minimality
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._issued: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def certify(
        self,
        plan: _FallbackCoverDesignPlan,
        violations: list[PrincipleViolation],
        gaps: list[CoverageGap],
    ) -> bool:
        """Attempt to certify *plan* and update ``plan.certified`` in place.

        Parameters
        ----------
        plan:
            The cover design plan under review.
        violations:
            All violations detected by the analyser.
        gaps:
            All coverage gaps detected by the analyser.

        Returns
        -------
        bool
            ``True`` if the plan is fully certified; ``False`` otherwise.
        """
        blocking = self._compute_blocking_violations(violations, gaps)

        if not blocking:
            plan.certified = True
            plan.phase = "certified"
            self._record_certificate(plan, violations, blocking=[], conditional=False)
            self._logger.info(
                "Cover plan %s CERTIFIED (compliance=%.3f)",
                plan.plan_id,
                plan.compliance_score,
            )
            return True

        # Issue conditional certificate if only minimality violations block
        only_minimality = all(
            v.principle_id == _MINIMALITY_PRINCIPLE.principle_id
            for v in blocking
        )
        if only_minimality and not self._require_minimality:
            plan.certified = True
            plan.phase = "conditionally_certified"
            self._record_certificate(plan, violations, blocking=blocking, conditional=True)
            self._logger.warning(
                "Cover plan %s conditionally certified with %d minimality violation(s)",
                plan.plan_id,
                len(blocking),
            )
            return True

        plan.certified = False
        plan.phase = "rejected"
        self._record_certificate(plan, violations, blocking=blocking, conditional=False)
        self._logger.error(
            "Cover plan %s REJECTED: %d blocking violation(s)",
            plan.plan_id,
            len(blocking),
        )
        return False

    def get_issued_certificates(self) -> list[dict[str, Any]]:
        """Return all certificates issued during this witness's lifetime."""
        return list(self._issued)

    def summarise(self) -> dict[str, Any]:
        """Return a summary of all certification decisions."""
        total = len(self._issued)
        certified = sum(1 for c in self._issued if c["certified"])
        conditional = sum(1 for c in self._issued if c["conditional"])
        rejected = total - certified - conditional
        return {
            "total_reviewed": total,
            "certified": certified,
            "conditionally_certified": conditional,
            "rejected": rejected,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_blocking_violations(
        self,
        violations: list[PrincipleViolation],
        gaps: list[CoverageGap],
    ) -> list[PrincipleViolation]:
        """Return the subset of *violations* that must block certification."""
        blocking: list[PrincipleViolation] = []
        for v in violations:
            if v.principle_id == _COMPLETENESS_PRINCIPLE.principle_id:
                blocking.append(v)
            elif v.principle_id == _CECH_PRINCIPLE.principle_id:
                blocking.append(v)
            elif v.principle_id == _MINIMALITY_PRINCIPLE.principle_id:
                if self._require_minimality:
                    blocking.append(v)
        return blocking

    def _record_certificate(
        self,
        plan: _FallbackCoverDesignPlan,
        violations: list[PrincipleViolation],
        blocking: list[PrincipleViolation],
        conditional: bool,
    ) -> None:
        self._issued.append(
            {
                "plan_id": plan.plan_id,
                "certified": plan.certified,
                "conditional": conditional,
                "phase": plan.phase,
                "compliance_score": plan.compliance_score,
                "total_violations": len(violations),
                "blocking_violations": len(blocking),
                "timestamp": time.time(),
            }
        )


# ---------------------------------------------------------------------------
# CoverDesignPrinciplesCoordinator
# ---------------------------------------------------------------------------


class CoverDesignPrinciplesCoordinator:
    """Orchestrates the full cover-design-principles pipeline.

    The coordinator advances a :class:`_FallbackCoverDesignPlan` through
    the following phases:

        initialising → analysing → validating → certifying → certified
                                                            ↘ rejected

    It holds an internal :class:`CoverDesignPrinciplesAnalyzer` and a
    :class:`CoverDesignPrinciplesWitness`, delegating all mathematical
    checks to those objects and keeping only orchestration logic here.

    Parameters
    ----------
    analyzer:
        Optional pre-built analyzer.  A default one is constructed otherwise.
    witness:
        Optional pre-built witness.  A default one is constructed otherwise.
    require_minimality:
        Passed through to the witness when a default one is constructed.
    """

    def __init__(
        self,
        analyzer: CoverDesignPrinciplesAnalyzer | None = None,
        witness: CoverDesignPrinciplesWitness | None = None,
        require_minimality: bool = True,
    ) -> None:
        self._analyzer = analyzer or CoverDesignPrinciplesAnalyzer()
        self._witness = witness or CoverDesignPrinciplesWitness(
            require_minimality=require_minimality
        )
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        site_points: frozenset[str],
        candidate_patches: dict[str, frozenset[str]],
        sections: dict[str, dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _FallbackCoverDesignPlan:
        """Execute the full cover-design-principles pipeline.

        Parameters
        ----------
        site_points:
            The complete set of judgment-site points J.
        candidate_patches:
            Mapping patch-id → frozenset of points covered by that patch.
        sections:
            Mapping patch-id → section value dict.  If ``None``, Čech
            checking is skipped.
        metadata:
            Optional extra data attached to the plan.

        Returns
        -------
        _FallbackCoverDesignPlan
            The plan after all phases have been executed.
        """
        plan = self._init_plan(site_points, candidate_patches, metadata or {})
        self._record_history("started", plan)

        # --- Phase: analysing ---
        plan.phase = "analysing"
        effective_sections = sections or {}
        gaps, violations, score = self._analyzer.full_analysis(
            site_points, candidate_patches, effective_sections
        )
        plan.gaps = gaps
        plan.violations = violations
        plan.compliance_score = score
        self._record_history("analysed", plan)
        self._logger.info(
            "Plan %s analysed: %d gap(s), %d violation(s), score=%.3f",
            plan.plan_id,
            len(gaps),
            len(violations),
            score,
        )

        # --- Phase: validating invariants ---
        plan.phase = "validating"
        inv_errors = self.validate_invariants(plan)
        if inv_errors:
            self._logger.error(
                "Plan %s failed invariant checks: %s", plan.plan_id, inv_errors
            )
        self._record_history("validated", plan)

        # --- Phase: certifying ---
        plan.phase = "certifying"
        self._witness.certify(plan, violations, gaps)
        self._record_history("certified_or_rejected", plan)

        return plan

    def advance_phase(
        self,
        plan: _FallbackCoverDesignPlan,
        target_phase: str,
    ) -> None:
        """Manually advance *plan* to *target_phase*.

        This is intended for use in multi-step workflows where the caller
        wants fine-grained control over phase transitions.

        Parameters
        ----------
        plan:
            The plan to advance.
        target_phase:
            The target phase string.

        Raises
        ------
        ValueError
            If the transition is illegal (e.g. advancing a rejected plan).
        """
        _LEGAL_TRANSITIONS: dict[str, list[str]] = {
            "initialising": ["analysing"],
            "analysing": ["validating"],
            "validating": ["certifying"],
            "certifying": ["certified", "conditionally_certified", "rejected"],
            "certified": [],
            "conditionally_certified": ["certifying"],
            "rejected": [],
        }
        allowed = _LEGAL_TRANSITIONS.get(plan.phase, [])
        if target_phase not in allowed:
            raise ValueError(
                f"Illegal phase transition {plan.phase!r} → {target_phase!r}. "
                f"Allowed: {allowed}"
            )
        self._logger.debug(
            "Plan %s: %s → %s", plan.plan_id, plan.phase, target_phase
        )
        plan.phase = target_phase

    def validate_invariants(self, plan: _FallbackCoverDesignPlan) -> list[str]:
        """Check theory2.tex invariants and return a list of error strings.

        Invariants checked:

        1. Every patch in ``plan.patches`` must have at least one point.
        2. The compliance score must be in [0, 1].
        3. A certified plan must have zero completeness violations.

        Parameters
        ----------
        plan:
            The plan to validate.

        Returns
        -------
        list[str]
            Empty list if all invariants hold; otherwise one string per error.
        """
        errors: list[str] = []

        for pid, pts in plan.patches.items():
            if not pts:
                errors.append(f"Patch {pid!r} is empty (has no points).")

        if not (0.0 <= plan.compliance_score <= 1.0):
            errors.append(
                f"Compliance score {plan.compliance_score} is outside [0, 1]."
            )

        if plan.certified:
            completeness_violations = [
                v for v in plan.violations
                if v.principle_id == _COMPLETENESS_PRINCIPLE.principle_id
            ]
            if completeness_violations:
                errors.append(
                    f"Plan is marked certified but has "
                    f"{len(completeness_violations)} completeness violation(s)."
                )

        return errors

    def get_history(self) -> list[dict[str, Any]]:
        """Return the full event history for all plans run by this coordinator."""
        return list(self._history)

    def summarise(self) -> dict[str, Any]:
        """Return high-level statistics about all plans processed."""
        total = len(self._history)
        phases = [entry["phase"] for entry in self._history]
        witness_summary = self._witness.summarise()
        return {
            "total_events": total,
            "phase_counts": {p: phases.count(p) for p in set(phases)},
            "witness": witness_summary,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _init_plan(
        self,
        site_points: frozenset[str],
        patches: dict[str, frozenset[str]],
        metadata: dict[str, Any],
    ) -> _FallbackCoverDesignPlan:
        return _FallbackCoverDesignPlan(
            plan_id=str(uuid.uuid4()),
            phase="initialising",
            site_points=site_points,
            patches=patches,
            violations=[],
            gaps=[],
            compliance_score=0.0,
            certified=False,
            metadata={
                **metadata,
                "created_at": time.time(),
                "trust_tier": "PROPOSAL",
            },
        )

    def _record_history(
        self, event: str, plan: _FallbackCoverDesignPlan
    ) -> None:
        self._history.append(
            {
                "event": event,
                "plan_id": plan.plan_id,
                "phase": plan.phase,
                "violations": len(plan.violations),
                "gaps": len(plan.gaps),
                "compliance_score": plan.compliance_score,
                "certified": plan.certified,
                "timestamp": time.time(),
            }
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("cover_design_principles — smoke test")
    print("=" * 70)

    # --- Example 1: fully compliant cover ---
    # U1 = {p1, p2}: only cover for p1.  U2 = {p2, p3, p4}: only cover for p3, p4.
    # Both patches are essential → cover is minimal.  p2 overlap agrees on "beta".
    print("\n[1] Fully compliant cover")
    coordinator = CoverDesignPrinciplesCoordinator()
    plan1 = coordinator.run(
        site_points=frozenset(["p1", "p2", "p3", "p4"]),
        candidate_patches={
            "U1": frozenset(["p1", "p2"]),
            "U2": frozenset(["p2", "p3", "p4"]),
        },
        sections={
            "U1": {"p1": "alpha", "p2": "beta"},
            "U2": {"p2": "beta", "p3": "gamma", "p4": "delta"},
        },
    )
    print(f"  Plan: {plan1}")
    print(f"  Violations: {plan1.violations}")
    print(f"  Gaps: {plan1.gaps}")
    print(f"  Compliance score: {plan1.compliance_score:.3f}")
    print(f"  Certified: {plan1.certified}")
    assert plan1.certified, "Expected certification for a valid cover"

    # --- Example 2: Čech violation ---
    print("\n[2] Cover with Čech violation (sections disagree on overlap)")
    plan2 = coordinator.run(
        site_points=frozenset(["p1", "p2", "p3"]),
        candidate_patches={
            "U1": frozenset(["p1", "p2"]),
            "U2": frozenset(["p2", "p3"]),
        },
        sections={
            "U1": {"p1": "alpha", "p2": "BETA_v1"},   # p2 value differs
            "U2": {"p2": "BETA_v2", "p3": "gamma"},   # p2 conflict
        },
    )
    print(f"  Plan: {plan2}")
    print(f"  Violations: {[str(v) for v in plan2.violations]}")
    print(f"  Certified: {plan2.certified}")
    assert not plan2.certified, "Expected rejection due to Čech violation"

    # --- Example 3: coverage gap ---
    print("\n[3] Cover with a gap")
    plan3 = coordinator.run(
        site_points=frozenset(["p1", "p2", "p3", "p4"]),
        candidate_patches={
            "U1": frozenset(["p1", "p2"]),
            # p3, p4 are not covered
        },
        sections={"U1": {"p1": "x", "p2": "y"}},
    )
    print(f"  Plan: {plan3}")
    print(f"  Gaps: {plan3.gaps}")
    print(f"  Certified: {plan3.certified}")
    assert not plan3.certified, "Expected rejection due to coverage gap"

    # --- Example 4: redundant patch ---
    print("\n[4] Cover with redundant patch")
    coordinator_no_min = CoverDesignPrinciplesCoordinator(require_minimality=False)
    plan4 = coordinator_no_min.run(
        site_points=frozenset(["p1", "p2"]),
        candidate_patches={
            "U1": frozenset(["p1", "p2"]),
            "U2": frozenset(["p1"]),    # U2 is redundant
        },
        sections={
            "U1": {"p1": "a", "p2": "b"},
            "U2": {"p1": "a"},
        },
    )
    print(f"  Plan: {plan4}")
    redundancy_v = [v for v in plan4.violations if "minimality" in v.principle_id.lower()]
    print(f"  Redundancy violations: {redundancy_v}")
    print(f"  Certified (minimality not required): {plan4.certified}")

    # --- Coordinator summary ---
    print("\n[Coordinator summary]")
    import json
    print(json.dumps(coordinator.summarise(), indent=2))

    print("\n✓ All smoke-test assertions passed.")
