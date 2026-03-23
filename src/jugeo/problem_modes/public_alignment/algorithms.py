"""Stand-alone algorithmic functions for the public_alignment subsystem.

This module provides pure functions (no class instances required) for the
core algorithmic operations of public alignment:

* Honesty scoring
* Silent-strengthening detection
* Trust-level projection
* Section merging
* Migration distance computation
* Migration step ordering
* Projection conservativity validation
* Honesty certificate generation

These functions are designed to be used independently of the stage classes,
making them easy to compose, test, and reason about.

Theory basis
------------
From theory2.tex Ch13:
- §13.2: Honesty Monotonicity Law
- §13.3: Conservative Projection
- §13.4: Publicity Boundary
- §13.5: Migration Semantic Preservation

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "algorithms",
}

# copilot: algorithms.py — stand-alone algorithmic functions for Ch13 public_alignment
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping, Sequence

from jugeo.judgments.judgment_terms import (
    Judgment,
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
    "module": "algorithms",
    "theory_sections": ["§13.2", "§13.3", "§13.4", "§13.5"],
}


# ═══════════════════════════════════════════════════════════════════════════
# §1  Honesty scoring
# ═══════════════════════════════════════════════════════════════════════════

def compute_honesty_score(claims: Sequence[PublicClaim]) -> float:
    """Compute the honesty score for a sequence of public claims.

    The honesty score is the fraction of claims that satisfy the honesty
    invariant (``declared_trust_level ≤ internal_trust_level``).  A score
    of 1.0 means all claims are honest.

    Parameters
    ----------
    claims : Sequence[PublicClaim]
        The claims to score.

    Returns
    -------
    float
        Score in [0.0, 1.0].  Returns 1.0 for empty sequences.

    Examples
    --------
    >>> from jugeo.problem_modes.public_alignment.models import make_public_claim
    >>> from jugeo.judgments.judgment_terms import TrustLevel
    >>> c = make_public_claim("coord", "stmt", TrustLevel.UNVERIFIED, TrustLevel.SOLVER_DISCHARGED)
    >>> compute_honesty_score([c])
    1.0
    """
    if not claims:
        return 1.0
    honest_count = sum(1 for c in claims if c.check_honesty())
    return honest_count / len(claims)


def compute_weighted_honesty_score(claims: Sequence[PublicClaim]) -> float:
    """Compute a trust-weighted honesty score.

    Claims with higher declared trust levels contribute more to the score
    when they are honest.  The weight of each claim is its declared trust
    level value (0–5).

    Parameters
    ----------
    claims : Sequence[PublicClaim]
        The claims to score.

    Returns
    -------
    float
        Weighted score in [0.0, 1.0].  Returns 1.0 for empty sequences.
    """
    if not claims:
        return 1.0
    total_weight = sum(int(c.declared_trust_level) for c in claims)
    if total_weight == 0:
        return 1.0
    honest_weight = sum(
        int(c.declared_trust_level) for c in claims if c.check_honesty()
    )
    return honest_weight / total_weight


# ═══════════════════════════════════════════════════════════════════════════
# §2  Silent-strengthening detection
# ═══════════════════════════════════════════════════════════════════════════

def detect_strengthening_violations(
    claims: Sequence[PublicClaim],
    judgments: Mapping[str, Judgment],
) -> list[tuple[PublicClaim, Judgment]]:
    """Detect all (claim, judgment) pairs where the claim silently strengthens.

    For each claim in *claims* whose ``source_judgment_id`` is present in
    *judgments*, checks whether the declared trust level exceeds the
    judgment's trust level.

    Parameters
    ----------
    claims : Sequence[PublicClaim]
        Public claims to inspect.
    judgments : Mapping[str, Judgment]
        Mapping from judgment ID to Judgment.

    Returns
    -------
    list[tuple[PublicClaim, Judgment]]
        Pairs where silent strengthening occurs.
    """
    violations: list[tuple[PublicClaim, Judgment]] = []
    for claim in claims:
        jid = claim.source_judgment_id
        if not jid or jid not in judgments:
            continue
        j = judgments[jid]
        j_trust = _judgment_trust(j)
        if int(claim.declared_trust_level) > int(j_trust):
            violations.append((claim, j))
    return violations


def find_worst_violation(
    claims: Sequence[PublicClaim],
    judgments: Mapping[str, Judgment],
) -> tuple[PublicClaim, Judgment] | None:
    """Return the (claim, judgment) pair with the largest trust over-declaration.

    Parameters
    ----------
    claims : Sequence[PublicClaim]
        Public claims.
    judgments : Mapping[str, Judgment]
        Source judgments.

    Returns
    -------
    tuple[PublicClaim, Judgment] | None
        The worst violation, or ``None`` if none found.
    """
    violations = detect_strengthening_violations(claims, judgments)
    if not violations:
        return None
    return max(
        violations,
        key=lambda pair: int(pair[0].declared_trust_level) - int(_judgment_trust(pair[1])),
    )


# ═══════════════════════════════════════════════════════════════════════════
# §3  Trust-level projection
# ═══════════════════════════════════════════════════════════════════════════

def project_trust_level(
    internal: TrustLevel,
    ceiling: TrustLevel,
) -> TrustLevel:
    """Project an internal trust level through a ceiling constraint.

    Returns ``min(internal, ceiling)``.  This is the core operation of the
    HonestProjection functor.

    Parameters
    ----------
    internal : TrustLevel
        The internal trust level.
    ceiling : TrustLevel
        The maximum allowed trust level.

    Returns
    -------
    TrustLevel
        The projected trust level.
    """
    return TrustLevel(min(int(internal), int(ceiling)))


def apply_ceiling_to_claims(
    claims: Sequence[PublicClaim],
    ceiling: TrustLevel,
) -> tuple[PublicClaim, ...]:
    """Apply a trust ceiling to a sequence of claims.

    Each claim's declared trust level is clamped to at most *ceiling*.

    Parameters
    ----------
    claims : Sequence[PublicClaim]
        Claims to process.
    ceiling : TrustLevel
        The ceiling to apply.

    Returns
    -------
    tuple[PublicClaim, ...]
        Claims with trust ceilings applied.
    """
    from dataclasses import replace
    result: list[PublicClaim] = []
    for claim in claims:
        new_level = project_trust_level(claim.declared_trust_level, ceiling)
        if new_level == claim.declared_trust_level:
            result.append(claim)
        else:
            result.append(replace(
                claim,
                declared_trust_level=new_level,
                is_honest=None,
                metadata={
                    **claim.metadata,
                    "ceiling_applied": ceiling.name,
                    "ceiling_applied_at": _now_iso(),
                },
            ))
    return tuple(result)


def compute_effective_ceiling(
    ceilings: Sequence[TrustLevel],
) -> TrustLevel:
    """Compute the effective ceiling from a sequence of ceilings.

    The effective ceiling is the minimum of all provided ceilings, since
    multiple constraints must all be satisfied simultaneously.

    Parameters
    ----------
    ceilings : Sequence[TrustLevel]
        The ceilings to combine.

    Returns
    -------
    TrustLevel
        The effective (minimum) ceiling.
    """
    if not ceilings:
        return TrustLevel.VERIFIED_PROOF
    return min(ceilings, key=int)


# ═══════════════════════════════════════════════════════════════════════════
# §4  Section merging
# ═══════════════════════════════════════════════════════════════════════════

def merge_documentation_sections(
    sections: Sequence[DocumentationSection],
) -> DocumentationSection:
    """Merge a sequence of documentation sections into one composite section.

    The merge uses conservative trust (minimum across all sections) and
    unions all evidence channels.  Content is concatenated with section
    titles as headers.

    Parameters
    ----------
    sections : Sequence[DocumentationSection]
        Sections to merge.

    Returns
    -------
    DocumentationSection
        Merged section.

    Raises
    ------
    ValueError
        If *sections* is empty.
    """
    if not sections:
        raise ValueError("Cannot merge empty sequence of DocumentationSections.")
    min_trust = min(sections, key=lambda s: int(s.trust_level)).trust_level
    all_channels: set[str] = set()
    for sec in sections:
        all_channels.update(sec.evidence_channels)
    content_parts = [f"## {s.title}\n\n{s.content}" for s in sections]
    coord = sections[0].coordinate
    subsections = tuple(s.section_id for s in sections)
    return DocumentationSection(
        section_id=_new_id("sec-merged"),
        title="Merged Documentation",
        content="\n\n".join(content_parts),
        coordinate=coord,
        trust_level=min_trust,
        evidence_channels=tuple(all_channels),
        last_updated=_now_iso(),
        subsections=subsections,
        is_public=all(s.is_public for s in sections),
        metadata={
            "merged_from": [s.section_id for s in sections],
            "merge_rule": "conservative_minimum_trust",
        },
    )


def split_section_by_trust(
    section: DocumentationSection,
    threshold: TrustLevel,
) -> tuple[DocumentationSection | None, DocumentationSection | None]:
    """Split a section into above-threshold and below-threshold parts.

    Parameters
    ----------
    section : DocumentationSection
        The section to split.
    threshold : TrustLevel
        The dividing trust level.

    Returns
    -------
    tuple[DocumentationSection | None, DocumentationSection | None]
        ``(above_or_equal, below)`` — either may be ``None``.
    """
    from dataclasses import replace
    if int(section.trust_level) >= int(threshold):
        return section, None
    else:
        return None, section


def filter_sections_by_trust(
    sections: Sequence[DocumentationSection],
    minimum: TrustLevel,
) -> tuple[DocumentationSection, ...]:
    """Return only sections with trust_level ≥ *minimum*.

    Parameters
    ----------
    sections : Sequence[DocumentationSection]
        Sections to filter.
    minimum : TrustLevel
        Minimum trust level.

    Returns
    -------
    tuple[DocumentationSection, ...]
        Filtered sections.
    """
    return tuple(s for s in sections if int(s.trust_level) >= int(minimum))


# ═══════════════════════════════════════════════════════════════════════════
# §5  Migration distance
# ═══════════════════════════════════════════════════════════════════════════

def compute_migration_distance(
    plan_a: MigrationPlan,
    plan_b: MigrationPlan,
) -> float:
    """Compute a distance metric between two migration plans.

    The distance reflects how different the two plans are in terms of:
    * Breaking changes: |breaking_a △ breaking_b| / max(1, union size)
    * Preserved semantics: 1 - Jaccard(preserved_a, preserved_b)
    * Step count difference: |len(steps_a) - len(steps_b)| / max(1, max(len))
    * Confidence gap: |confidence_a - confidence_b|

    The result is a weighted average of these components.

    Parameters
    ----------
    plan_a : MigrationPlan
        First plan.
    plan_b : MigrationPlan
        Second plan.

    Returns
    -------
    float
        Distance in [0.0, 1.0].  0.0 means identical; 1.0 means maximally different.
    """
    # Breaking changes Jaccard distance
    set_a_break = set(plan_a.breaking_changes)
    set_b_break = set(plan_b.breaking_changes)
    union_break = set_a_break | set_b_break
    intersect_break = set_a_break & set_b_break
    if union_break:
        break_dist = 1.0 - len(intersect_break) / len(union_break)
    else:
        break_dist = 0.0

    # Preserved semantics Jaccard distance
    set_a_pres = set(plan_a.preserved_semantics)
    set_b_pres = set(plan_b.preserved_semantics)
    union_pres = set_a_pres | set_b_pres
    intersect_pres = set_a_pres & set_b_pres
    if union_pres:
        pres_dist = 1.0 - len(intersect_pres) / len(union_pres)
    else:
        pres_dist = 0.0

    # Step count distance
    len_a = len(plan_a.steps)
    len_b = len(plan_b.steps)
    max_len = max(1, max(len_a, len_b))
    step_dist = abs(len_a - len_b) / max_len

    # Confidence gap
    conf_dist = abs(plan_a.confidence - plan_b.confidence)

    # Weighted average
    weights = (0.35, 0.30, 0.20, 0.15)
    components = (break_dist, pres_dist, step_dist, conf_dist)
    distance = sum(w * c for w, c in zip(weights, components))
    return min(1.0, max(0.0, distance))


def plans_are_equivalent(
    plan_a: MigrationPlan,
    plan_b: MigrationPlan,
    tolerance: float = 0.05,
) -> bool:
    """Return ``True`` when two migration plans are approximately equivalent.

    Parameters
    ----------
    plan_a : MigrationPlan
        First plan.
    plan_b : MigrationPlan
        Second plan.
    tolerance : float
        Maximum allowed distance (default: 0.05).

    Returns
    -------
    bool
        ``True`` if plans are within *tolerance* of each other.
    """
    return compute_migration_distance(plan_a, plan_b) <= tolerance


# ═══════════════════════════════════════════════════════════════════════════
# §6  Migration step ordering
# ═══════════════════════════════════════════════════════════════════════════

def order_migration_steps(
    plan: MigrationPlan,
) -> list[MigrationPlan.MigrationStep]:
    """Return migration steps in a recommended execution order.

    The ordering is:
    1. Non-breaking steps first (least disruptive).
    2. Breaking steps last (require consumer coordination).
    3. Within each group, steps with larger trust_impact magnitude come last.

    Parameters
    ----------
    plan : MigrationPlan
        The migration plan.

    Returns
    -------
    list[MigrationPlan.MigrationStep]
        Steps in recommended order.
    """
    non_breaking = [s for s in plan.steps if not s.is_breaking]
    breaking = [s for s in plan.steps if s.is_breaking]
    # Sort non-breaking: ascending trust_impact magnitude (small changes first)
    non_breaking.sort(key=lambda s: abs(s.trust_impact))
    # Sort breaking: descending trust_impact magnitude (biggest changes last)
    breaking.sort(key=lambda s: abs(s.trust_impact))
    return non_breaking + breaking


def partition_steps_by_impact(
    plan: MigrationPlan,
) -> dict[str, list[MigrationPlan.MigrationStep]]:
    """Partition migration steps by their trust impact direction.

    Returns a dict with keys ``"strengthening"``, ``"weakening"``,
    ``"neutral"``.

    Parameters
    ----------
    plan : MigrationPlan
        The migration plan.

    Returns
    -------
    dict[str, list[MigrationPlan.MigrationStep]]
        Partitioned steps.
    """
    result: dict[str, list[MigrationPlan.MigrationStep]] = {
        "strengthening": [],
        "weakening": [],
        "neutral": [],
    }
    for step in plan.steps:
        if step.trust_impact > 0:
            result["strengthening"].append(step)
        elif step.trust_impact < 0:
            result["weakening"].append(step)
        else:
            result["neutral"].append(step)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# §7  Projection conservativity validation
# ═══════════════════════════════════════════════════════════════════════════

def validate_projection_conservativity(
    projection: HonestProjection,
    judgment: Judgment,
) -> bool:
    """Check that a projection is conservatively derived from a judgment.

    A projection is conservative iff every claim satisfies:
    * ``declared_trust_level ≤ internal_trust_level`` (honesty)
    * ``declared_trust_level ≤ projection.trust_ceiling``

    Parameters
    ----------
    projection : HonestProjection
        The projection to check.
    judgment : Judgment
        The source judgment.

    Returns
    -------
    bool
        ``True`` if conservative.
    """
    j_trust = _judgment_trust(judgment)
    for claim in projection.claims:
        if int(claim.declared_trust_level) > int(j_trust):
            return False
        if int(claim.declared_trust_level) > int(projection.trust_ceiling):
            return False
    return True


def count_conservativity_violations(
    projection: HonestProjection,
    judgment: Judgment,
) -> int:
    """Count the number of claims that violate conservativity.

    Parameters
    ----------
    projection : HonestProjection
        The projection.
    judgment : Judgment
        The source judgment.

    Returns
    -------
    int
        Number of violating claims.
    """
    j_trust = _judgment_trust(judgment)
    count = 0
    for claim in projection.claims:
        if int(claim.declared_trust_level) > int(j_trust):
            count += 1
        elif int(claim.declared_trust_level) > int(projection.trust_ceiling):
            count += 1
    return count


def all_projections_conservative(
    projections: Sequence[HonestProjection],
    judgments: Mapping[str, Judgment],
) -> bool:
    """Return ``True`` when all projections are conservative.

    Parameters
    ----------
    projections : Sequence[HonestProjection]
        Projections to check.
    judgments : Mapping[str, Judgment]
        Source judgments keyed by ID.

    Returns
    -------
    bool
        ``True`` if all are conservative.
    """
    for proj in projections:
        for claim in proj.claims:
            jid = claim.source_judgment_id
            if jid and jid in judgments:
                j = judgments[jid]
                if not validate_projection_conservativity(
                    HonestProjection(
                        projection_id=proj.projection_id,
                        source_coordinate=proj.source_coordinate,
                        target_audience=proj.target_audience,
                        trust_ceiling=proj.trust_ceiling,
                        claims=(claim,),
                    ),
                    j,
                ):
                    return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
# §8  Honesty certificate generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_honesty_certificate(
    projections: Sequence[HonestProjection],
) -> dict[str, JsonValue]:
    """Generate a machine-readable honesty certificate for a set of projections.

    The certificate includes:
    * Total claim count
    * Honest claim count and score
    * Per-projection summary
    * Timestamp and theory reference

    Parameters
    ----------
    projections : Sequence[HonestProjection]
        Projections to certify.

    Returns
    -------
    dict[str, JsonValue]
        Honesty certificate dictionary.
    """
    total_claims = 0
    honest_claims = 0
    projection_summaries: list[dict[str, JsonValue]] = []
    all_violations: list[dict[str, JsonValue]] = []

    for proj in projections:
        proj_claims = list(proj.claims)
        proj_honest = sum(1 for c in proj_claims if c.check_honesty())
        proj_violations = [c.to_dict() for c in proj_claims if not c.check_honesty()]
        total_claims += len(proj_claims)
        honest_claims += proj_honest
        all_violations.extend(proj_violations)
        projection_summaries.append({
            "projection_id": proj.projection_id,
            "audience": proj.target_audience,
            "claim_count": len(proj_claims),
            "honest_count": proj_honest,
            "ceiling": proj.trust_ceiling.name,
            "is_valid": proj_honest == len(proj_claims),
        })

    score = honest_claims / total_claims if total_claims > 0 else 1.0
    is_fully_honest = len(all_violations) == 0

    return {
        "certificate_id": _new_id("cert"),
        "generated_at": _now_iso(),
        "theory_reference": "theory2.tex §13.2 – Honesty Monotonicity Law",
        "total_projections": len(projections),
        "total_claims": total_claims,
        "honest_claims": honest_claims,
        "honesty_score": score,
        "is_fully_honest": is_fully_honest,
        "violation_count": len(all_violations),
        "projection_summaries": projection_summaries,
        "violations": all_violations,
        "verdict": "HONEST" if is_fully_honest else "VIOLATIONS_DETECTED",
    }


def generate_batch_honesty_report(
    projections: Sequence[HonestProjection],
    judgments: Mapping[str, Judgment],
) -> dict[str, JsonValue]:
    """Generate a comprehensive honesty report with judgment cross-references.

    Parameters
    ----------
    projections : Sequence[HonestProjection]
        Projections to report on.
    judgments : Mapping[str, Judgment]
        Source judgments.

    Returns
    -------
    dict[str, JsonValue]
        Comprehensive honesty report.
    """
    claim_reports: list[dict[str, JsonValue]] = []
    for proj in projections:
        for claim in proj.claims:
            jid = claim.source_judgment_id
            if jid and jid in judgments:
                j = judgments[jid]
                j_trust = _judgment_trust(j)
                delta = int(claim.declared_trust_level) - int(j_trust)
                is_honest = delta <= 0
            else:
                is_honest = claim.check_honesty()
                delta = -claim.honesty_delta()
            claim_reports.append({
                "claim_id": claim.claim_id,
                "projection_id": proj.projection_id,
                "audience": proj.target_audience,
                "coordinate": claim.coordinate,
                "declared": claim.declared_trust_level.name,
                "internal": claim.internal_trust_level.name,
                "judgment_trust": j_trust.name if jid and jid in judgments else "(unknown)",
                "delta": delta,
                "is_honest": is_honest,
            })

    honest_count = sum(1 for r in claim_reports if r["is_honest"])
    score = honest_count / max(1, len(claim_reports))

    return {
        "report_id": _new_id("rpt"),
        "generated_at": _now_iso(),
        "total_claims": len(claim_reports),
        "honest_claims": honest_count,
        "honesty_score": score,
        "claims": claim_reports,
        "verdict": "HONEST" if score == 1.0 else "VIOLATIONS_DETECTED",
    }


# ═══════════════════════════════════════════════════════════════════════════
# §9  Miscellaneous helpers
# ═══════════════════════════════════════════════════════════════════════════

def summarize_projection(projection: HonestProjection) -> str:
    """Return a human-readable summary of a projection.

    Parameters
    ----------
    projection : HonestProjection
        The projection to summarize.

    Returns
    -------
    str
        Multi-line summary.
    """
    violations = projection.violations()
    score = compute_honesty_score(projection.claims)
    lines = [
        f"Projection: {projection.projection_id!r}",
        f"  Audience:  {projection.target_audience!r}",
        f"  Ceiling:   {projection.trust_ceiling.name}",
        f"  Claims:    {len(projection.claims)}",
        f"  Honest:    {len(projection.claims) - len(violations)}/{len(projection.claims)} ({score:.1%})",
        f"  Valid:     {projection.is_valid}",
    ]
    if violations:
        lines.append("  Violations:")
        for v in violations[:5]:
            lines.append(f"    - {v.claim_id!r}: {v.declared_trust_level.name} > {v.internal_trust_level.name}")
        if len(violations) > 5:
            lines.append(f"    ... and {len(violations) - 5} more")
    return "\n".join(lines)


def trust_level_difference(a: TrustLevel, b: TrustLevel) -> int:
    """Return the signed difference between two trust levels.

    Parameters
    ----------
    a : TrustLevel
        First trust level.
    b : TrustLevel
        Second trust level.

    Returns
    -------
    int
        ``int(a) - int(b)``.  Positive if a > b.
    """
    return int(a) - int(b)


def clamp_trust_level(level: TrustLevel, lo: TrustLevel, hi: TrustLevel) -> TrustLevel:
    """Clamp a trust level to [lo, hi].

    Parameters
    ----------
    level : TrustLevel
        The level to clamp.
    lo : TrustLevel
        Lower bound.
    hi : TrustLevel
        Upper bound.

    Returns
    -------
    TrustLevel
        Clamped level.
    """
    return TrustLevel(max(int(lo), min(int(hi), int(level))))


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
# §10  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Honesty scoring
    "compute_honesty_score",
    "compute_weighted_honesty_score",
    # Silent-strengthening detection
    "detect_strengthening_violations",
    "find_worst_violation",
    # Trust projection
    "project_trust_level",
    "apply_ceiling_to_claims",
    "compute_effective_ceiling",
    # Section merging
    "merge_documentation_sections",
    "split_section_by_trust",
    "filter_sections_by_trust",
    # Migration distance
    "compute_migration_distance",
    "plans_are_equivalent",
    # Step ordering
    "order_migration_steps",
    "partition_steps_by_impact",
    # Conservativity
    "validate_projection_conservativity",
    "count_conservativity_violations",
    "all_projections_conservative",
    # Certificates
    "generate_honesty_certificate",
    "generate_batch_honesty_report",
    # Misc
    "summarize_projection",
    "trust_level_difference",
    "clamp_trust_level",
    # Provenance
    "MANIFEST_SPEC_PROVENANCE",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: algorithms.py — stand-alone algorithmic functions for Ch13 public_alignment
