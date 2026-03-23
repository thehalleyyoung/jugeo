"""Stage 02 — Documentation Projection for the public_alignment subsystem.

This module implements the :class:`DocumentationProjector`, which projects
internal judgment state to :class:`~models.DocumentationSection` objects
using the conservative HonestProjection functor π_pub.

The functor is conservative — it may only weaken, never strengthen, the trust
level of any claim when crossing the publicity boundary.  The projector also
manages the composition of sections, validates projections, and produces
:class:`~models.MigrationPlan` objects when the projection changes.

Theory basis
------------
From theory2.tex §13.3 — Documentation as Conservative Projection:

    Let S be the semantic site and let π_pub : PSh(S) → DocSections be the
    documentation projection functor.  For any presheaf F (representing
    internal judgment state), and any coverage {U_i → U},

        π_pub(F)(U) = section derived from F(U) with trust weakened to
                      the minimum trust level across all U_i.

    In particular, π_pub ∘ restrict = restrict ∘ π_pub (naturality).

    **Theorem 13.3 (Projection Conservativity)**
    The functor π_pub is conservative: for all F and U,

        trust(π_pub(F)(U)) ≤ trust(F(U)).

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "documentation_projection",
}

# copilot: documentation_projection.py — DocumentationProjector for Ch13
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentStatus,
    TrustLevel,
    Proposition,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Provenance,
    ProvenanceSource,
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
    "module": "documentation_projection",
    "class": "DocumentationProjector",
    "theory_section": "§13.3 – Documentation as Conservative Projection",
}


# ---------------------------------------------------------------------------
# §1  Helper — extract proposition content from Judgment
# ---------------------------------------------------------------------------

def _judgment_content(judgment: Judgment) -> str:
    """Extract human-readable content from a Judgment's proposition.

    Parameters
    ----------
    judgment : Judgment
        The judgment to extract from.

    Returns
    -------
    str
        Content string, or a generic fallback.
    """
    prop = getattr(judgment, "proposition", None)
    if prop is not None:
        content = getattr(prop, "content", None)
        if content:
            return str(content)
        text = getattr(prop, "text", None)
        if text:
            return str(text)
    return f"Judgment at {_judgment_coordinate(judgment)}"


def _judgment_evidence_channels(judgment: Judgment) -> tuple[str, ...]:
    """Extract evidence channel names from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    tuple[str, ...]
        Channel names.
    """
    bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
    if bundle is None:
        return ()
    items: tuple[EvidenceItem, ...] = getattr(bundle, "items", ())
    channels: list[str] = []
    for item in items:
        kind = getattr(item, "kind", None)
        if kind is not None:
            channels.append(str(kind.value if hasattr(kind, "value") else kind))
    return tuple(set(channels))


def _judgment_status(judgment: Judgment) -> JudgmentStatus:
    """Extract JudgmentStatus from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    JudgmentStatus
        The status, or ``PROPOSED`` as fallback.
    """
    status = getattr(judgment, "status", None)
    if isinstance(status, JudgmentStatus):
        return status
    return JudgmentStatus.PROPOSED


# ---------------------------------------------------------------------------
# §2  DocumentationProjector
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DocumentationProjector:
    """Projects internal judgment state to public documentation sections.

    The DocumentationProjector implements the HonestProjection functor π_pub
    from theory2.tex §13.3.  For each Judgment it receives, it produces a
    :class:`~models.DocumentationSection` with a trust level that is at most
    the judgment's internal trust level.

    It also supports:

    * Applying a trust ceiling to enforce audience-specific limits.
    * Composing multiple sections into a single aggregate section.
    * Building a full :class:`~models.HonestProjection` from a judgment.
    * Diffing two projections to produce a :class:`~models.MigrationPlan`.

    Attributes
    ----------
    projector_id : str
        Unique identifier for this projector instance.
    default_audience : str
        Default audience identifier used when none is specified.
    default_trust_ceiling : TrustLevel
        Default trust ceiling applied when projecting.
    projection_rules : tuple[str, ...]
        Named rules applied during projection (for audit).
    version : str
        Version of the projection rule set.
    created_at : str
        ISO-8601 creation timestamp.
    """

    projector_id: str = ""
    default_audience: str = "public"
    default_trust_ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF
    projection_rules: tuple[str, ...] = ("conservative_weakening", "audience_ceiling")
    version: str = "1.0.0"
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set default fields for frozen dataclass."""
        if not self.projector_id:
            object.__setattr__(self, "projector_id", _new_id("projector"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Primary projection methods
    # ------------------------------------------------------------------

    def project(self, judgment: Judgment, audience: str = "") -> DocumentationSection:
        """Project a single Judgment to a DocumentationSection.

        The projected trust level is ``min(judgment_trust, trust_ceiling)``.
        The section is marked public and carries the evidence channels from
        the judgment.

        Parameters
        ----------
        judgment : Judgment
            Internal judgment to project.
        audience : str
            Target audience; defaults to ``default_audience``.

        Returns
        -------
        DocumentationSection
            Conservative projection of the judgment.
        """
        if not audience:
            audience = self.default_audience
        j_trust = _judgment_trust(judgment)
        projected_trust = TrustLevel(min(int(j_trust), int(self.default_trust_ceiling)))
        j_coord = _judgment_coordinate(judgment)
        content = self.generate_doc_from_judgment(judgment)
        channels = _judgment_evidence_channels(judgment)
        status = _judgment_status(judgment)

        return DocumentationSection(
            section_id=_new_id("sec"),
            title=f"Documentation for {j_coord or 'unknown coordinate'}",
            content=content,
            coordinate=j_coord,
            trust_level=projected_trust,
            evidence_channels=channels,
            last_updated=_now_iso(),
            is_public=True,
            metadata={
                "audience": audience,
                "source_judgment_id": _judgment_id(judgment),
                "judgment_status": status.value,
                "projector_id": self.projector_id,
                "projection_rules": list(self.projection_rules),
            },
        )

    def project_all(
        self,
        judgments: Sequence[Judgment],
        audience: str = "",
    ) -> tuple[DocumentationSection, ...]:
        """Project all judgments to documentation sections.

        Parameters
        ----------
        judgments : Sequence[Judgment]
            Internal judgments to project.
        audience : str
            Target audience; defaults to ``default_audience``.

        Returns
        -------
        tuple[DocumentationSection, ...]
            Conservative projections, one per judgment.
        """
        if not audience:
            audience = self.default_audience
        return tuple(self.project(j, audience) for j in judgments)

    def build_projection(self, judgment: Judgment) -> HonestProjection:
        """Build a full HonestProjection from a single Judgment.

        Constructs both the projection container and the corresponding
        PublicClaim, validating honesty immediately.

        Parameters
        ----------
        judgment : Judgment
            The internal judgment.

        Returns
        -------
        HonestProjection
            Projection containing a single claim for the judgment.
        """
        j_trust = _judgment_trust(judgment)
        j_id = _judgment_id(judgment)
        j_coord = _judgment_coordinate(judgment)
        projected_trust = TrustLevel(min(int(j_trust), int(self.default_trust_ceiling)))
        statement = _judgment_content(judgment)

        claim = PublicClaim(
            claim_id=_new_id("claim"),
            coordinate=j_coord,
            statement=statement,
            declared_trust_level=projected_trust,
            internal_trust_level=j_trust,
            is_honest=True,
            publication_timestamp=_now_iso(),
            source_judgment_id=j_id,
            evidence_summary=f"Projected by {self.projector_id!r}",
            metadata={"audience": self.default_audience},
        )

        projection = HonestProjection(
            projection_id=_new_id("proj"),
            source_coordinate=j_coord,
            target_audience=self.default_audience,
            projection_rules=self.projection_rules,
            trust_ceiling=self.default_trust_ceiling,
            applied_at=_now_iso(),
            claims=(claim,),
            is_valid=True,
        )
        return projection

    def apply_trust_ceiling(
        self,
        section: DocumentationSection,
        ceiling: TrustLevel,
    ) -> DocumentationSection:
        """Apply a trust ceiling to a DocumentationSection.

        If the section's current trust level exceeds the ceiling, it is
        weakened to the ceiling.  Otherwise, the section is returned unchanged.

        Parameters
        ----------
        section : DocumentationSection
            Section to apply ceiling to.
        ceiling : TrustLevel
            Maximum allowed trust level.

        Returns
        -------
        DocumentationSection
            Section with trust level at most *ceiling*.
        """
        if int(section.trust_level) <= int(ceiling):
            return section
        return replace(
            section,
            trust_level=ceiling,
            last_updated=_now_iso(),
            metadata={
                **section.metadata,
                "ceiling_applied": ceiling.name,
                "ceiling_applied_at": _now_iso(),
                "original_trust": section.trust_level.name,
            },
        )

    def compose_sections(
        self,
        sections: Sequence[DocumentationSection],
    ) -> DocumentationSection:
        """Compose multiple sections into a single aggregate section.

        The composed section's trust level is the minimum of all input
        sections (conservative composition rule from theory2.tex §13.3.3).
        Content is concatenated with section titles as headers.

        Parameters
        ----------
        sections : Sequence[DocumentationSection]
            Sections to compose.

        Returns
        -------
        DocumentationSection
            Composite section.

        Raises
        ------
        ValueError
            If *sections* is empty.
        """
        if not sections:
            raise ValueError("Cannot compose empty sequence of sections.")
        # Conservative trust: minimum across all
        min_trust = min(sections, key=lambda s: int(s.trust_level)).trust_level
        # Collect all evidence channels
        all_channels: set[str] = set()
        for sec in sections:
            all_channels.update(sec.evidence_channels)
        # Compose content
        content_parts: list[str] = []
        for sec in sections:
            content_parts.append(f"## {sec.title}\n\n{sec.content}")
        composed_content = "\n\n".join(content_parts)
        all_subsections = tuple(s.section_id for s in sections)
        # Coordinate: pick first or use "composite"
        coord = sections[0].coordinate if sections else "composite"
        return DocumentationSection(
            section_id=_new_id("sec-composite"),
            title="Composite Documentation Section",
            content=composed_content,
            coordinate=coord,
            trust_level=min_trust,
            evidence_channels=tuple(all_channels),
            last_updated=_now_iso(),
            subsections=all_subsections,
            is_public=all(s.is_public for s in sections),
            metadata={
                "composed_from": [s.section_id for s in sections],
                "composed_by": self.projector_id,
                "composition_rule": "conservative_minimum_trust",
            },
        )

    def validate_projection(
        self,
        projection: HonestProjection,
        judgment: Judgment,
    ) -> bool:
        """Validate that a projection is conservatively derived from a judgment.

        Checks that every claim in the projection has a declared trust level
        ≤ the judgment's internal trust level AND ≤ the projection's
        trust ceiling.

        Parameters
        ----------
        projection : HonestProjection
            The projection to validate.
        judgment : Judgment
            The source judgment.

        Returns
        -------
        bool
            ``True`` if the projection is valid.
        """
        j_trust = _judgment_trust(judgment)
        for claim in projection.claims:
            if int(claim.declared_trust_level) > int(j_trust):
                return False
            if int(claim.declared_trust_level) > int(projection.trust_ceiling):
                return False
        return True

    def generate_doc_from_judgment(self, judgment: Judgment) -> str:
        """Generate markdown documentation text from a Judgment.

        Produces a structured documentation string covering the judgment's
        coordinate, status, trust level, proposition, and evidence channels.

        Parameters
        ----------
        judgment : Judgment
            The judgment to document.

        Returns
        -------
        str
            Markdown-formatted documentation string.
        """
        coord = _judgment_coordinate(judgment)
        j_trust = _judgment_trust(judgment)
        status = _judgment_status(judgment)
        content = _judgment_content(judgment)
        channels = _judgment_evidence_channels(judgment)
        j_id = _judgment_id(judgment)

        # Obstructions
        obstructions_raw = getattr(judgment, "obstructions", ())
        obstruction_count = len(obstructions_raw) if obstructions_raw else 0

        # Residual obligations
        obligations_raw = getattr(judgment, "obligations", ())
        obligation_count = len(obligations_raw) if obligations_raw else 0

        channel_list = ", ".join(channels) if channels else "(none)"
        obs_line = f"{obstruction_count} active obstruction(s)" if obstruction_count else "No active obstructions"
        obl_line = f"{obligation_count} residual obligation(s)" if obligation_count else "No residual obligations"

        doc = (
            f"**Coordinate**: `{coord}`\n\n"
            f"**Judgment ID**: `{j_id}`\n\n"
            f"**Status**: {status.value}\n\n"
            f"**Trust Level**: {j_trust.name}\n\n"
            f"**Claim**:\n\n> {content}\n\n"
            f"**Evidence Channels**: {channel_list}\n\n"
            f"**Obstructions**: {obs_line}\n\n"
            f"**Residual Obligations**: {obl_line}\n\n"
            f"*Generated by DocumentationProjector {self.projector_id} "
            f"at {_now_iso()}*"
        )
        return doc

    def diff_projections(
        self,
        old: HonestProjection,
        new: HonestProjection,
    ) -> MigrationPlan:
        """Produce a MigrationPlan by diffing two projections.

        Computes which claims were added, removed, modified, or preserved
        between *old* and *new*.  All removals and trust-level decreases
        are flagged as breaking changes.

        Parameters
        ----------
        old : HonestProjection
            The previous projection.
        new : HonestProjection
            The current projection.

        Returns
        -------
        MigrationPlan
            A migration plan derived from the diff.
        """
        old_claims = {c.coordinate: c for c in old.claims}
        new_claims = {c.coordinate: c for c in new.claims}

        old_coords = set(old_claims)
        new_coords = set(new_claims)
        added = new_coords - old_coords
        removed = old_coords - new_coords
        common = old_coords & new_coords

        steps: list[MigrationPlan.MigrationStep] = []
        breaking: list[str] = []
        preserved: list[str] = []
        deprecated: list[str] = []
        new_claim_texts: list[str] = []

        for coord in sorted(removed):
            c = old_claims[coord]
            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"Claim at {coord!r} was removed.",
                is_breaking=True,
                old_claim=c.statement,
                new_claim="",
                migration_note="This claim is no longer publicly documented.",
                trust_impact=0,
            )
            steps.append(step)
            breaking.append(f"Removed claim at {coord!r}")
            deprecated.append(c.statement)

        for coord in sorted(added):
            c = new_claims[coord]
            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=f"New claim added at {coord!r}.",
                is_breaking=False,
                old_claim="",
                new_claim=c.statement,
                migration_note="New documentation section.",
                trust_impact=0,
            )
            steps.append(step)
            new_claim_texts.append(c.statement)

        for coord in sorted(common):
            old_c = old_claims[coord]
            new_c = new_claims[coord]
            trust_delta = int(new_c.declared_trust_level) - int(old_c.declared_trust_level)
            if old_c.statement == new_c.statement and trust_delta == 0:
                preserved.append(old_c.statement)
                continue
            is_breaking = trust_delta < 0 or old_c.statement != new_c.statement
            step = MigrationPlan.MigrationStep(
                step_id=_new_id("step"),
                description=(
                    f"Claim at {coord!r} {'changed' if old_c.statement != new_c.statement else 'trust changed'}."
                ),
                is_breaking=is_breaking,
                old_claim=old_c.statement,
                new_claim=new_c.statement,
                migration_note=f"Trust delta: {trust_delta:+d} levels.",
                trust_impact=trust_delta,
            )
            steps.append(step)
            if is_breaking:
                breaking.append(f"Modified claim at {coord!r}")
            else:
                preserved.append(new_c.statement)

        confidence = 1.0 - 0.1 * len(breaking)
        confidence = max(0.0, min(1.0, confidence))

        return MigrationPlan(
            plan_id=_new_id("plan"),
            source_version=f"proj:{old.projection_id[:8]}",
            target_version=f"proj:{new.projection_id[:8]}",
            coordinate=new.source_coordinate or old.source_coordinate,
            steps=tuple(steps),
            breaking_changes=tuple(breaking),
            preserved_semantics=tuple(preserved),
            deprecated_claims=tuple(deprecated),
            new_claims=tuple(new_claim_texts),
            confidence=confidence,
            created_at=_now_iso(),
        )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def with_audience(self, audience: str) -> "DocumentationProjector":
        """Return a copy with a different default audience.

        Parameters
        ----------
        audience : str
            New default audience.

        Returns
        -------
        DocumentationProjector
            Updated projector.
        """
        return replace(self, default_audience=audience)

    def with_ceiling(self, ceiling: TrustLevel) -> "DocumentationProjector":
        """Return a copy with a different default trust ceiling.

        Parameters
        ----------
        ceiling : TrustLevel
            New trust ceiling.

        Returns
        -------
        DocumentationProjector
            Updated projector.
        """
        return replace(self, default_trust_ceiling=ceiling)

    def with_rules(self, rules: tuple[str, ...]) -> "DocumentationProjector":
        """Return a copy with different projection rules.

        Parameters
        ----------
        rules : tuple[str, ...]
            New projection rules.

        Returns
        -------
        DocumentationProjector
            Updated projector.
        """
        return replace(self, projection_rules=rules)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize configuration.

        Returns
        -------
        dict[str, JsonValue]
            Serialized projector.
        """
        return {
            "projector_id": self.projector_id,
            "default_audience": self.default_audience,
            "default_trust_ceiling": self.default_trust_ceiling.value,
            "projection_rules": list(self.projection_rules),
            "version": self.version,
            "created_at": self.created_at,
        }

    def summarize(self) -> str:
        """Return a one-line summary of this projector.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"DocumentationProjector({self.projector_id!r}, "
            f"audience={self.default_audience!r}, "
            f"ceiling={self.default_trust_ceiling.name})"
        )

    def project_to_section_hierarchy(
        self,
        judgments: Sequence[Judgment],
        audience: str = "",
    ) -> tuple[DocumentationSection, tuple[DocumentationSection, ...]]:
        """Project judgments to a hierarchy: one root + one child per judgment.

        Parameters
        ----------
        judgments : Sequence[Judgment]
            Judgments to project.
        audience : str
            Target audience.

        Returns
        -------
        tuple[DocumentationSection, tuple[DocumentationSection, ...]]
            ``(root_section, child_sections)``.
        """
        children = self.project_all(judgments, audience)
        if not children:
            root = DocumentationSection(
                section_id=_new_id("sec-root"),
                title="Documentation Root",
                content="No judgments to document.",
                coordinate="",
                last_updated=_now_iso(),
            )
            return root, ()
        root = self.compose_sections(list(children))
        return root, children

    def emit_projection_certificate(
        self,
        projection: HonestProjection,
        judgment: Judgment,
    ) -> StructuredFailure:
        """Emit a StructuredFailure (as certificate) for a valid projection.

        Uses ``severity=INFO`` semantics (``is_blocking=False``) to confirm
        that the projection is valid.

        Parameters
        ----------
        projection : HonestProjection
            The projection to certify.
        judgment : Judgment
            The source judgment.

        Returns
        -------
        StructuredFailure
            Certificate (non-blocking if valid, blocking if invalid).
        """
        from jugeo.errors import StructuredFailure
        is_valid = self.validate_projection(projection, judgment)
        return StructuredFailure(
            failure_id=_new_id("cert"),
            scope=FailureScope.SEMANTIC,
            classification=FailureClassification.POSTCONDITION,
            message=(
                f"Projection {projection.projection_id!r} is "
                f"{'VALID' if is_valid else 'INVALID'} — "
                f"{len(projection.claims)} claim(s), "
                f"ceiling={projection.trust_ceiling.name}."
            ),
            coordinate=projection.source_coordinate,
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_failure=projection.trust_ceiling.value,
            obstruction_records=(),
            repair_hints=(),
            is_blocking=not is_valid,
            context={
                "projection_id": projection.projection_id,
                "audience": projection.target_audience,
                "is_valid": is_valid,
                "claim_count": len(projection.claims),
                "theory_reference": "theory2.tex §13.3 – Projection Conservativity",
            },
        )


# ---------------------------------------------------------------------------
# §3  Convenience factory
# ---------------------------------------------------------------------------

def make_documentation_projector(
    audience: str = "public",
    trust_ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF,
) -> DocumentationProjector:
    """Create a new DocumentationProjector.

    Parameters
    ----------
    audience : str
        Default audience identifier.
    trust_ceiling : TrustLevel
        Default trust ceiling.

    Returns
    -------
    DocumentationProjector
        New projector instance.
    """
    return DocumentationProjector(
        projector_id=_new_id("projector"),
        default_audience=audience,
        default_trust_ceiling=trust_ceiling,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §4  Module-level convenience functions
# ---------------------------------------------------------------------------

def project_judgment(
    judgment: Judgment,
    audience: str = "public",
    ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF,
) -> DocumentationSection:
    """One-shot projection of a single judgment to a documentation section.

    Parameters
    ----------
    judgment : Judgment
        The judgment to project.
    audience : str
        Target audience.
    ceiling : TrustLevel
        Trust ceiling.

    Returns
    -------
    DocumentationSection
        Projected section.
    """
    projector = make_documentation_projector(audience=audience, trust_ceiling=ceiling)
    return projector.project(judgment, audience)


def build_full_projection(
    judgment: Judgment,
    audience: str = "public",
    ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF,
) -> HonestProjection:
    """Build a full HonestProjection for a single judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment to project.
    audience : str
        Target audience.
    ceiling : TrustLevel
        Trust ceiling.

    Returns
    -------
    HonestProjection
        Completed projection.
    """
    projector = make_documentation_projector(audience=audience, trust_ceiling=ceiling)
    return projector.build_projection(judgment)


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
    "DocumentationProjector",
    "MANIFEST_SPEC_PROVENANCE",
    # Factories
    "make_documentation_projector",
    # Convenience
    "project_judgment",
    "build_full_projection",
    # Helpers
    "_judgment_content",
    "_judgment_evidence_channels",
    "_judgment_status",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: documentation_projection.py — Ch13 DocumentationProjector
