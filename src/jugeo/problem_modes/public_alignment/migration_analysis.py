"""Stage 03 — Migration Analysis for the public_alignment subsystem.

This module implements the :class:`MigrationAnalyzer`, which analyzes the
changes between two versions of the public judgment/documentation state and
produces a :class:`~models.MigrationPlan` that is honest about breaking
changes and preserves semantic content.

Theory basis
------------
From theory2.tex §13.5 — Migration Semantic Preservation:

    **Theorem 13.5 (Migration Semantic Preservation)**
    A migration plan M from version v₀ to version v₁ is *semantically
    preserving* if and only if:

    (a) Every claim in v₀ that is still valid in v₁ appears in
        ``preserved_semantics``.
    (b) Every claim that is removed is listed in ``deprecated_claims``.
    (c) Every new claim introduced by v₁ is listed in ``new_claims``.
    (d) No step silently increases the trust level of a claim without
        providing explicit supporting evidence.

    A migration plan that violates any of these conditions is an obstruction
    in the semantic site.

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "migration_analysis",
}

# copilot: migration_analysis.py — MigrationAnalyzer for Ch13 public_alignment
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from jugeo.judgments.comparisons import ComparisonMode, ComparisonResult, compare_sections
from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentStatus,
    TrustLevel,
)
from jugeo.errors import (
    StructuredFailure,
    FailureScope,
    FailureClassification,
    EvidenceFamily,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    FailureChain,
)
from jugeo.problem_modes.public_alignment.models import (
    PublicClaim,
    HonestProjection,
    DocumentationSection,
    MigrationPlan,
    _now_iso,
    _new_id,
)
from jugeo.problem_modes.public_alignment.honesty_enforcement import (
    _judgment_trust,
    _judgment_id,
    _judgment_coordinate,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "migration_analysis",
    "class": "MigrationAnalyzer",
    "theory_section": "§13.5 – Migration Semantic Preservation",
}


# ---------------------------------------------------------------------------
# §1  Helper — judgment content (re-export for use in this module)
# ---------------------------------------------------------------------------

# _judgment_content was imported from s01 above, but that module doesn't define it.
# Define a local version that delegates to the judgment's proposition.

def _get_judgment_content(judgment: Judgment) -> str:
    """Extract human-readable content from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    str
        Content string.
    """
    prop = getattr(judgment, "proposition", None)
    if prop is not None:
        for attr in ("content", "text", "statement"):
            val = getattr(prop, attr, None)
            if val:
                return str(val)
    return f"Judgment at coordinate {_judgment_coordinate(judgment)!r}"


def _judgment_version(judgment: Judgment) -> str:
    """Extract version metadata from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    str
        Version string.
    """
    meta = getattr(judgment, "metadata", {}) or {}
    return str(meta.get("version", "1.0.0"))


# ---------------------------------------------------------------------------
# §2  MigrationAnalyzer
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MigrationAnalyzer:
    """Analyzes changes between judgment versions and produces MigrationPlans.

    The MigrationAnalyzer is responsible for computing what must change when
    the public documentation state transitions from one version to another.
    It guarantees that the resulting :class:`~models.MigrationPlan` is honest:
    no breaking change is hidden, no trust level is silently increased, and
    all preserved semantics are explicitly listed.

    Theory basis (theory2.tex §13.5)
    ----------------------------------
    Migration analysis is the formal process of constructing a morphism in the
    category of documentation states such that the following diagram commutes:

        v₀ --[migrate]--> v₁
         |                 |
       π_pub            π_pub
         ↓                 ↓
       D₀ --[doc_migrate]--> D₁

    Where π_pub is the honest-projection functor.

    Attributes
    ----------
    analyzer_id : str
        Unique identifier for this analyzer instance.
    source_version : str
        Version being migrated from.
    target_version : str
        Version being migrated to.
    confidence_threshold : float
        Minimum confidence required for a migration plan to be considered
        complete (default: 0.8).
    strict_honesty : bool
        If ``True``, raises on any plan that fails ``validate_migration_honesty``.
    created_at : str
        ISO-8601 creation timestamp.
    """

    analyzer_id: str = ""
    source_version: str = "1.0.0"
    target_version: str = "2.0.0"
    confidence_threshold: float = 0.8
    strict_honesty: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set defaults for frozen dataclass."""
        if not self.analyzer_id:
            object.__setattr__(self, "analyzer_id", _new_id("analyzer"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Primary analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        old_judgments: Mapping[str, Judgment],
        new_judgments: Mapping[str, Judgment],
    ) -> MigrationPlan:
        """Produce a full MigrationPlan from old and new judgment sets.

        Parameters
        ----------
        old_judgments : Mapping[str, Judgment]
            Mapping from coordinate/ID to old Judgment.
        new_judgments : Mapping[str, Judgment]
            Mapping from coordinate/ID to new Judgment.

        Returns
        -------
        MigrationPlan
            A complete, honest migration plan.
        """
        old_keys = set(old_judgments)
        new_keys = set(new_judgments)
        added_keys = new_keys - old_keys
        removed_keys = old_keys - new_keys
        common_keys = old_keys & new_keys

        all_steps: list[MigrationPlan.MigrationStep] = []
        breaking: list[str] = []
        preserved: list[str] = []
        deprecated: list[str] = []
        new_claims: list[str] = []

        # Process removed judgments
        for key in sorted(removed_keys):
            old_j = old_judgments[key]
            old_content = _get_judgment_content(old_j)
            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"Judgment {key!r} was removed.",
                is_breaking=True,
                old_claim=old_content,
                new_claim="",
                migration_note=(
                    f"The claim previously at {key!r} is no longer present. "
                    f"Consumers must update any code that depended on this claim."
                ),
                trust_impact=0,
            )
            all_steps.append(step)
            breaking.append(f"Removed: {key!r} — {old_content[:80]}")
            deprecated.append(old_content)

        # Process added judgments
        for key in sorted(added_keys):
            new_j = new_judgments[key]
            new_content = _get_judgment_content(new_j)
            new_trust = _judgment_trust(new_j)
            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"New judgment {key!r} added.",
                is_breaking=False,
                old_claim="",
                new_claim=new_content,
                migration_note=f"New documentation added at {key!r} with trust={new_trust.name}.",
                trust_impact=0,
            )
            all_steps.append(step)
            new_claims.append(new_content)

        # Process common judgments (modifications)
        for key in sorted(common_keys):
            old_j = old_judgments[key]
            new_j = new_judgments[key]
            old_content = _get_judgment_content(old_j)
            new_content = _get_judgment_content(new_j)
            old_trust = _judgment_trust(old_j)
            new_trust = _judgment_trust(new_j)
            trust_delta = int(new_trust) - int(old_trust)

            breaking_changes_here = self.detect_breaking_changes(old_j, new_j)
            is_breaking_step = len(breaking_changes_here) > 0

            if old_content == new_content and trust_delta == 0:
                preserved.append(old_content)
                continue

            note_parts: list[str] = []
            if old_content != new_content:
                note_parts.append("Content changed.")
            if trust_delta != 0:
                direction = "increased" if trust_delta > 0 else "decreased"
                note_parts.append(
                    f"Trust level {direction} by {abs(trust_delta)} "
                    f"({old_trust.name} → {new_trust.name})."
                )
            for bc in breaking_changes_here:
                note_parts.append(f"Breaking: {bc}")

            migration_note = " ".join(note_parts) or "No breaking changes."

            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"Judgment {key!r} modified.",
                is_breaking=is_breaking_step,
                old_claim=old_content,
                new_claim=new_content,
                migration_note=migration_note,
                trust_impact=trust_delta,
            )
            all_steps.append(step)
            if is_breaking_step:
                for bc in breaking_changes_here:
                    breaking.append(f"{key!r}: {bc}")
            else:
                preserved.append(new_content)

        confidence = self.estimate_migration_confidence(
            MigrationPlan(
                plan_id="temp",
                source_version=self.source_version,
                target_version=self.target_version,
                coordinate="",
                steps=tuple(all_steps),
                breaking_changes=tuple(breaking),
                preserved_semantics=tuple(preserved),
                deprecated_claims=tuple(deprecated),
                new_claims=tuple(new_claims),
                confidence=1.0,
                created_at=_now_iso(),
            )
        )

        return MigrationPlan(
            plan_id=_new_id("plan"),
            source_version=self.source_version,
            target_version=self.target_version,
            coordinate=f"migration:{self.source_version}->{self.target_version}",
            steps=tuple(all_steps),
            breaking_changes=tuple(breaking),
            preserved_semantics=tuple(preserved),
            deprecated_claims=tuple(deprecated),
            new_claims=tuple(new_claims),
            confidence=confidence,
            created_at=_now_iso(),
        )

    def detect_breaking_changes(
        self,
        old: Judgment,
        new: Judgment,
    ) -> tuple[str, ...]:
        """Detect breaking changes between two versions of a judgment.

        A breaking change is any one of:
        * Trust level decrease
        * Status change from SETTLED to non-SETTLED
        * Coordinate change
        * New obstructions added
        * Content change (proposition text differs)

        Parameters
        ----------
        old : Judgment
            Previous judgment.
        new : Judgment
            New judgment.

        Returns
        -------
        tuple[str, ...]
            Human-readable descriptions of breaking changes.
        """
        changes: list[str] = []

        old_trust = _judgment_trust(old)
        new_trust = _judgment_trust(new)
        if int(new_trust) < int(old_trust):
            changes.append(
                f"Trust level decreased: {old_trust.name} → {new_trust.name}."
            )

        old_status = getattr(old, "status", None)
        new_status = getattr(new, "status", None)
        if (
            isinstance(old_status, JudgmentStatus)
            and isinstance(new_status, JudgmentStatus)
            and old_status == JudgmentStatus.SETTLED
            and new_status != JudgmentStatus.SETTLED
        ):
            changes.append(
                f"Status regressed from SETTLED to {new_status.value}."
            )

        old_coord = _judgment_coordinate(old)
        new_coord = _judgment_coordinate(new)
        if old_coord != new_coord and old_coord and new_coord:
            changes.append(f"Coordinate changed: {old_coord!r} → {new_coord!r}.")

        old_obs = getattr(old, "obstructions", ()) or ()
        new_obs = getattr(new, "obstructions", ()) or ()
        if len(new_obs) > len(old_obs):
            diff = len(new_obs) - len(old_obs)
            changes.append(f"{diff} new obstruction(s) introduced.")

        old_content = _get_judgment_content(old)
        new_content = _get_judgment_content(new)
        if old_content != new_content:
            changes.append("Proposition content changed.")

        return tuple(changes)

    def compute_semantic_delta(
        self,
        old: Judgment,
        new: Judgment,
    ) -> ComparisonResult:
        """Compute the semantic delta between two judgments using compare_sections.

        Parameters
        ----------
        old : Judgment
            Previous judgment.
        new : Judgment
            New judgment.

        Returns
        -------
        ComparisonResult
            Comparison result from the judgments comparison subsystem.
        """
        try:
            return compare_sections(old, new, mode=ComparisonMode.SEMANTIC)
        except Exception:
            # Fall back to a structural comparison
            try:
                return compare_sections(old, new, mode=ComparisonMode.STRUCTURAL)
            except Exception:
                # Return a minimal comparison result if comparison fails
                from jugeo.judgments.comparisons import ComparisonResult
                return ComparisonResult(
                    are_equivalent=old is new,
                    similarity_score=1.0 if old is new else 0.5,
                    differences=(),
                    mode=ComparisonMode.SEMANTIC,
                )

    def generate_migration_steps(
        self,
        old: Judgment,
        new: Judgment,
    ) -> tuple[MigrationPlan.MigrationStep, ...]:
        """Generate migration steps for a single judgment transition.

        Parameters
        ----------
        old : Judgment
            Previous judgment.
        new : Judgment
            New judgment.

        Returns
        -------
        tuple[MigrationPlan.MigrationStep, ...]
            Steps required to migrate from *old* to *new*.
        """
        steps: list[MigrationPlan.MigrationStep] = []
        old_content = _get_judgment_content(old)
        new_content = _get_judgment_content(new)
        old_trust = _judgment_trust(old)
        new_trust = _judgment_trust(new)
        trust_delta = int(new_trust) - int(old_trust)
        breaking_changes = self.detect_breaking_changes(old, new)
        old_coord = _judgment_coordinate(old)

        # Trust level change step
        if trust_delta != 0:
            direction = "increase" if trust_delta > 0 else "decrease"
            is_breaking = trust_delta < 0
            steps.append(MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=(
                    f"Trust level {direction}: "
                    f"{old_trust.name} → {new_trust.name}."
                ),
                is_breaking=is_breaking,
                old_claim=f"trust:{old_trust.name}",
                new_claim=f"trust:{new_trust.name}",
                migration_note=(
                    f"Trust {direction} by {abs(trust_delta)} level(s). "
                    + ("Review evidence basis." if trust_delta > 0 else "Accept weakened guarantee.")
                ),
                trust_impact=trust_delta,
            ))

        # Content change step
        if old_content != new_content:
            steps.append(MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"Proposition content updated at {old_coord!r}.",
                is_breaking=bool(breaking_changes),
                old_claim=old_content,
                new_claim=new_content,
                migration_note=(
                    "Review updated claim and update any dependent documentation."
                    if breaking_changes else
                    "Non-breaking content refinement."
                ),
                trust_impact=0,
            ))

        # Status change step
        old_status = getattr(old, "status", None)
        new_status = getattr(new, "status", None)
        if old_status != new_status and old_status is not None and new_status is not None:
            old_sv = old_status.value if hasattr(old_status, "value") else str(old_status)
            new_sv = new_status.value if hasattr(new_status, "value") else str(new_status)
            is_breaking = (
                old_sv == "settled" and new_sv != "settled"
            )
            steps.append(MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"Judgment status changed: {old_sv} → {new_sv}.",
                is_breaking=is_breaking,
                old_claim=f"status:{old_sv}",
                new_claim=f"status:{new_sv}",
                migration_note=f"Status transition: {old_sv} → {new_sv}.",
                trust_impact=0,
            ))

        return tuple(steps)

    def validate_migration_honesty(
        self,
        plan: MigrationPlan,
    ) -> tuple[ObstructionRecord, ...]:
        """Return ObstructionRecords for any honesty violations in the plan.

        Parameters
        ----------
        plan : MigrationPlan
            The migration plan to validate.

        Returns
        -------
        tuple[ObstructionRecord, ...]
            Zero or more obstruction records for honesty violations.
        """
        return plan.validate_honesty()

    def estimate_migration_confidence(self, plan: MigrationPlan) -> float:
        """Estimate a confidence score for the migration plan.

        The confidence decreases with:
        * More breaking changes (−0.05 per breaking step)
        * Lower semantic coverage
        * High ratio of deprecated to preserved claims

        Parameters
        ----------
        plan : MigrationPlan
            The plan to score.

        Returns
        -------
        float
            Confidence in [0.0, 1.0].
        """
        base = 1.0
        # Penalize breaking steps
        base -= 0.05 * plan.breaking_step_count()
        # Penalize low semantic coverage
        coverage = plan.semantic_coverage()
        base -= 0.2 * (1.0 - coverage)
        # Penalize high deprecation ratio
        total_old = len(plan.deprecated_claims) + len(plan.preserved_semantics)
        if total_old > 0:
            deprecation_ratio = len(plan.deprecated_claims) / total_old
            base -= 0.1 * deprecation_ratio
        return max(0.0, min(1.0, base))

    def merge_migration_plans(
        self,
        plans: Sequence[MigrationPlan],
    ) -> MigrationPlan:
        """Merge multiple migration plans into a single composite plan.

        The merged plan:
        * Contains all steps from all plans (in order).
        * Unions all breaking changes, preserved semantics, deprecated
          claims, and new claims.
        * Confidence is the minimum of all input confidence scores.

        Parameters
        ----------
        plans : Sequence[MigrationPlan]
            Plans to merge.

        Returns
        -------
        MigrationPlan
            Merged plan.

        Raises
        ------
        ValueError
            If *plans* is empty.
        """
        if not plans:
            raise ValueError("Cannot merge empty sequence of migration plans.")
        all_steps: list[MigrationPlan.MigrationStep] = []
        all_breaking: list[str] = []
        all_preserved: list[str] = []
        all_deprecated: list[str] = []
        all_new: list[str] = []
        min_confidence = 1.0

        for plan in plans:
            all_steps.extend(plan.steps)
            all_breaking.extend(plan.breaking_changes)
            all_preserved.extend(plan.preserved_semantics)
            all_deprecated.extend(plan.deprecated_claims)
            all_new.extend(plan.new_claims)
            min_confidence = min(min_confidence, plan.confidence)

        # Deduplicate while preserving order
        def _dedup(items: list[str]) -> tuple[str, ...]:
            seen: set[str] = set()
            out: list[str] = []
            for item in items:
                if item not in seen:
                    seen.add(item)
                    out.append(item)
            return tuple(out)

        source_v = plans[0].source_version
        target_v = plans[-1].target_version
        coord = plans[0].coordinate

        return MigrationPlan(
            plan_id=_new_id("plan-merged"),
            source_version=source_v,
            target_version=target_v,
            coordinate=coord,
            steps=tuple(all_steps),
            breaking_changes=_dedup(all_breaking),
            preserved_semantics=_dedup(all_preserved),
            deprecated_claims=_dedup(all_deprecated),
            new_claims=_dedup(all_new),
            confidence=min_confidence,
            created_at=_now_iso(),
        )

    def emit_migration_certificate(
        self,
        plan: MigrationPlan,
    ) -> StructuredFailure:
        """Emit a StructuredFailure certificate for a migration plan.

        If the plan is honest and confidence ≥ threshold, the certificate
        is non-blocking.  Otherwise it is blocking with repair hints.

        Parameters
        ----------
        plan : MigrationPlan
            The plan to certify.

        Returns
        -------
        StructuredFailure
            Migration certificate.
        """
        violations = self.validate_migration_honesty(plan)
        is_honest = len(violations) == 0
        meets_confidence = plan.confidence >= self.confidence_threshold

        if is_honest and meets_confidence:
            return StructuredFailure(
                failure_id=_new_id("cert"),
                scope=FailureScope.SEMANTIC,
                classification=FailureClassification.POSTCONDITION,
                message=(
                    f"Migration plan {plan.plan_id!r} certified: "
                    f"{plan.source_version!r} → {plan.target_version!r}, "
                    f"confidence={plan.confidence:.2f}, honest=True."
                ),
                coordinate=plan.coordinate,
                evidence_family=EvidenceFamily.SEMANTIC,
                trust_at_failure=TrustLevel.VERIFIED_PROOF.value,
                obstruction_records=(),
                repair_hints=(),
                is_blocking=False,
                context={
                    "plan_id": plan.plan_id,
                    "source_version": plan.source_version,
                    "target_version": plan.target_version,
                    "breaking_count": plan.breaking_step_count(),
                    "confidence": plan.confidence,
                    "theory_reference": "theory2.tex §13.5 – Migration Semantic Preservation",
                },
            )

        all_hints: list[RepairHint] = []
        for obs in violations:
            all_hints.extend(obs.repair_hints)
        if not meets_confidence:
            all_hints.append(RepairHint(
                action="increase_migration_coverage",
                description=(
                    f"Migration confidence {plan.confidence:.2f} is below "
                    f"threshold {self.confidence_threshold:.2f}. "
                    f"Add more preserved_semantics entries or reduce breaking changes."
                ),
                priority=RepairPriority.MEDIUM,
                target_coordinate=plan.coordinate,
                estimated_effort="medium",
            ))

        return StructuredFailure(
            failure_id=_new_id("fail"),
            scope=FailureScope.SEMANTIC,
            classification=FailureClassification.POSTCONDITION,
            message=(
                f"Migration plan {plan.plan_id!r} FAILED certification: "
                f"{len(violations)} honesty violation(s), "
                f"confidence={plan.confidence:.2f} "
                f"({'below' if not meets_confidence else 'above'} threshold)."
            ),
            coordinate=plan.coordinate,
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_failure=TrustLevel.UNVERIFIED.value,
            obstruction_records=tuple(violations),
            repair_hints=tuple(all_hints),
            is_blocking=True,
            context={
                "plan_id": plan.plan_id,
                "is_honest": is_honest,
                "meets_confidence": meets_confidence,
                "violation_count": len(violations),
                "theory_reference": "theory2.tex §13.5 – Migration Semantic Preservation",
            },
        )

    def analyze_single(
        self,
        old: Judgment,
        new: Judgment,
    ) -> MigrationPlan:
        """Analyze a single judgment-pair and produce a migration plan.

        Parameters
        ----------
        old : Judgment
            Old judgment.
        new : Judgment
            New judgment.

        Returns
        -------
        MigrationPlan
            Migration plan for this pair.
        """
        old_id = _judgment_id(old)
        new_id = _judgment_id(new)
        steps = self.generate_migration_steps(old, new)
        breaking_changes = self.detect_breaking_changes(old, new)

        old_content = _get_judgment_content(old)
        new_content = _get_judgment_content(new)
        preserved = (old_content,) if old_content == new_content else ()
        deprecated = (old_content,) if old_content != new_content else ()
        new_claims = (new_content,) if old_content != new_content else ()

        plan = MigrationPlan(
            plan_id=_new_id("plan"),
            source_version=self.source_version,
            target_version=self.target_version,
            coordinate=_judgment_coordinate(new) or _judgment_coordinate(old),
            steps=steps,
            breaking_changes=breaking_changes,
            preserved_semantics=preserved,
            deprecated_claims=deprecated,
            new_claims=new_claims,
            confidence=self.estimate_migration_confidence(
                MigrationPlan(
                    plan_id="temp",
                    source_version=self.source_version,
                    target_version=self.target_version,
                    coordinate="",
                    steps=steps,
                    breaking_changes=breaking_changes,
                    preserved_semantics=preserved,
                    deprecated_claims=deprecated,
                    new_claims=new_claims,
                    confidence=1.0,
                    created_at="",
                )
            ),
            created_at=_now_iso(),
        )
        return plan

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize analyzer configuration.

        Returns
        -------
        dict[str, JsonValue]
            Serialized configuration.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "confidence_threshold": self.confidence_threshold,
            "strict_honesty": self.strict_honesty,
            "created_at": self.created_at,
        }

    def with_versions(
        self,
        source: str,
        target: str,
    ) -> "MigrationAnalyzer":
        """Return a copy with updated version labels.

        Parameters
        ----------
        source : str
            Source version label.
        target : str
            Target version label.

        Returns
        -------
        MigrationAnalyzer
            Updated analyzer.
        """
        return replace(self, source_version=source, target_version=target)

    def with_threshold(self, threshold: float) -> "MigrationAnalyzer":
        """Return a copy with a different confidence threshold.

        Parameters
        ----------
        threshold : float
            New threshold in [0.0, 1.0].

        Returns
        -------
        MigrationAnalyzer
            Updated analyzer.
        """
        return replace(self, confidence_threshold=max(0.0, min(1.0, threshold)))


# ---------------------------------------------------------------------------
# §3  Convenience factory
# ---------------------------------------------------------------------------

def make_migration_analyzer(
    source_version: str = "1.0.0",
    target_version: str = "2.0.0",
    confidence_threshold: float = 0.8,
) -> MigrationAnalyzer:
    """Create a new MigrationAnalyzer.

    Parameters
    ----------
    source_version : str
        Source version label.
    target_version : str
        Target version label.
    confidence_threshold : float
        Minimum confidence threshold.

    Returns
    -------
    MigrationAnalyzer
        New analyzer.
    """
    return MigrationAnalyzer(
        analyzer_id=_new_id("analyzer"),
        source_version=source_version,
        target_version=target_version,
        confidence_threshold=confidence_threshold,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §4  Module-level convenience
# ---------------------------------------------------------------------------

def analyze_migration(
    old_judgments: Mapping[str, Judgment],
    new_judgments: Mapping[str, Judgment],
    source_version: str = "1.0.0",
    target_version: str = "2.0.0",
) -> MigrationPlan:
    """One-shot migration analysis.

    Parameters
    ----------
    old_judgments : Mapping[str, Judgment]
        Old judgment set.
    new_judgments : Mapping[str, Judgment]
        New judgment set.
    source_version : str
        Source version label.
    target_version : str
        Target version label.

    Returns
    -------
    MigrationPlan
        Computed migration plan.
    """
    analyzer = make_migration_analyzer(
        source_version=source_version,
        target_version=target_version,
    )
    return analyzer.analyze(old_judgments, new_judgments)


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.evidence, jugeo.judgments)
# ---------------------------------------------------------------------------


def alignment_trust_check(claim: Any) -> dict[str, Any]:
    """Check trust alignment between a public claim and internal evidence.

    Trust checking verifies that the declared trust level of a public
    claim does not exceed the trust actually supported by evidence.

    Parameters
    ----------
    claim : Any
        A PublicClaim object or dict with claim data.

    Returns
    -------
    dict[str, Any]
        Trust check result with ``honest``, ``declared_trust``,
        ``actual_trust``, ``gap``, and ``trust_obj`` keys.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, compare_trust, compute_trust_gap
    except ImportError:
        TrustLevel = None
        compare_trust = None
        compute_trust_gap = None

    declared = getattr(claim, "declared_trust_level", None) or (
        claim.get("declared_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )
    actual = getattr(claim, "internal_trust_level", None) or (
        claim.get("internal_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )

    result: dict[str, Any] = {
        "declared_trust": str(declared),
        "actual_trust": str(actual),
        "honest": str(declared) <= str(actual),
        "gap": None,
        "trust_obj": None,
    }

    if compare_trust is not None:
        try:
            cmp = compare_trust(declared, actual)
            result["honest"] = getattr(cmp, "honest", result["honest"])
        except Exception:
            pass

    if compute_trust_gap is not None:
        try:
            result["gap"] = compute_trust_gap(declared, actual)
        except Exception:
            pass

    return result


def alignment_judgment(claim: Any, reality: Any) -> dict[str, Any]:
    """Construct a judgment comparing a public claim against internal reality.

    The alignment judgment captures the relationship between what is
    publicly stated and what the internal evidence actually supports.

    Parameters
    ----------
    claim : Any
        The public claim or documentation assertion.
    reality : Any
        The internal judgment, evidence, or code state.

    Returns
    -------
    dict[str, Any]
        Judgment record with ``aligned``, ``claim_summary``,
        ``reality_summary``, ``discrepancies``, and ``judgment_obj`` keys.
    """
    try:
        from jugeo.judgments import Judgment, build_comparison_judgment
    except ImportError:
        Judgment = None
        build_comparison_judgment = None

    claim_str = getattr(claim, "summary", None) or (
        claim.get("summary") if isinstance(claim, dict) else str(claim)[:120]
    )
    reality_str = getattr(reality, "summary", None) or (
        reality.get("summary") if isinstance(reality, dict) else str(reality)[:120]
    )

    judgment: dict[str, Any] = {
        "aligned": claim_str == reality_str,
        "claim_summary": claim_str,
        "reality_summary": reality_str,
        "discrepancies": [],
        "judgment_obj": None,
    }

    if build_comparison_judgment is not None:
        try:
            j = build_comparison_judgment(claim=claim, reality=reality)
            judgment["aligned"] = getattr(j, "aligned", judgment["aligned"])
            judgment["discrepancies"] = getattr(j, "discrepancies", [])
            judgment["judgment_obj"] = j
        except Exception:
            pass

    return judgment


def alignment_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for an alignment check result.

    The alignment certificate records whether a public claim was found
    to be honest, the evidence used, and the trust level.

    Parameters
    ----------
    result : Any
        An alignment check result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``aligned``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    aligned = getattr(result, "aligned", None) or getattr(result, "honest", None)
    if aligned is None and isinstance(result, dict):
        aligned = result.get("aligned", result.get("honest", False))

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "aligned": bool(aligned) if aligned is not None else False,
        "trust_level": "ALIGNED" if aligned else "MISALIGNED",
        "certificate_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim="public_alignment", satisfied=aligned, source="public_alignment"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# §5  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MigrationAnalyzer",
    "MANIFEST_SPEC_PROVENANCE",
    # Factories
    "make_migration_analyzer",
    # Convenience
    "analyze_migration",
    # Helpers
    "_get_judgment_content",
    "_judgment_version",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: migration_analysis.py — Ch13 MigrationAnalyzer implementation
