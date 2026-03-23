"""Integration layer for the public_alignment subsystem.

This module provides the :class:`PublicAlignmentIntegration` class, which
connects the public-alignment subsystem to the main JuGeo judgment algebra.

The integration layer is responsible for:

* Attaching documentation evidence to judgments.
* Exporting public obligations as ResidualObligation objects.
* Merging honesty violations back into judgments as obstructions.
* Building EvidenceItem objects from projections.
* Generating "public" judgments for external consumption.
* Exporting FailureChain objects from violations.

Theory basis
------------
From theory2.tex §13.6 — Integration with the Judgment Algebra:

    The public-alignment subsystem interacts with the judgment algebra via:

    1. **Evidence attachment**: documentation evidence is an EvidenceItem
       with kind=ORACLE_PROPOSED (or stronger if the projection was
       validated by a verifier).

    2. **Obligation export**: any claim that appears in public documentation
       but is not yet fully discharged creates a ResidualObligation on the
       internal judgment.

    3. **Obstruction merge**: honesty violations detected by the enforcer
       are merged back into the corresponding judgment's obstruction set.

    4. **Public judgment generation**: a "public" judgment is a copy of an
       internal judgment with its trust level capped at the audience ceiling
       and its evidence bundle restricted to publicly shareable items.

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "integration",
}

# copilot: integration.py — PublicAlignmentIntegration for Ch13 public_alignment
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
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    ResidualObligation,
    Obstruction,
    TrustAnnotation,
    Provenance,
    ProvenanceSource,
    JudgmentAlgebra,
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
    as_failure_payload,
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
    HonestyEnforcer,
    make_honesty_enforcer,
)
from jugeo.problem_modes.public_alignment.documentation_projection import (
    DocumentationProjector,
    make_documentation_projector,
)
from jugeo.problem_modes.public_alignment.algorithms import (
    project_trust_level,
    generate_honesty_certificate,
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
    "module": "integration",
    "class": "PublicAlignmentIntegration",
    "theory_section": "§13.6 – Integration with the Judgment Algebra",
}


# ---------------------------------------------------------------------------
# §1  Helper — build a minimal EvidenceItem from a projection
# ---------------------------------------------------------------------------

def _make_evidence_item_from_projection(
    projection: HonestProjection,
) -> EvidenceItem:
    """Build an EvidenceItem from a HonestProjection.

    The kind is ORACLE_PROPOSED unless all claims are honest and the
    projection was validated (is_valid=True), in which case it is
    RUNTIME_WITNESSED.

    Parameters
    ----------
    projection : HonestProjection
        The projection to convert.

    Returns
    -------
    EvidenceItem
        An evidence item representing this projection.
    """
    is_valid = projection.is_valid or projection.validate()
    kind = (
        EvidenceItemKind.RUNTIME_WITNESSED
        if is_valid
        else EvidenceItemKind.ORACLE_PROPOSED
    )
    content = (
        f"Public projection {projection.projection_id!r} "
        f"for audience {projection.target_audience!r} "
        f"({len(projection.claims)} claim(s), "
        f"ceiling={projection.trust_ceiling.name}, "
        f"valid={is_valid})."
    )
    return EvidenceItem(
        item_id=_new_id("ev"),
        kind=kind,
        content=content,
        trust_level=projection.trust_ceiling,
        coordinate=projection.source_coordinate,
        provenance=Provenance(
            source=ProvenanceSource.ORACLE,
            description=f"Generated by public_alignment integration from projection {projection.projection_id!r}.",
            timestamp=_now_iso(),
        ),
    )


def _make_residual_obligation_from_claim(
    claim: PublicClaim,
    judgment_id: str,
) -> ResidualObligation:
    """Build a ResidualObligation from a PublicClaim.

    A public claim that is not fully discharged (trust < VERIFIED_PROOF)
    creates a residual obligation to provide stronger evidence.

    Parameters
    ----------
    claim : PublicClaim
        The public claim.
    judgment_id : str
        The ID of the source judgment.

    Returns
    -------
    ResidualObligation
        The residual obligation.
    """
    description = (
        f"Public claim {claim.claim_id!r} at {claim.coordinate!r} has "
        f"declared trust {claim.declared_trust_level.name}. "
        f"Provide evidence to raise to VERIFIED_PROOF, or lower the public trust claim."
    )
    return ResidualObligation(
        obligation_id=_new_id("obl"),
        coordinate=claim.coordinate,
        description=description,
        required_trust_level=TrustLevel.VERIFIED_PROOF,
        current_trust_level=claim.declared_trust_level,
        source_judgment_id=judgment_id,
        created_at=_now_iso(),
        metadata={"claim_id": claim.claim_id, "audience": str(claim.metadata.get("audience", ""))},
    )


# ---------------------------------------------------------------------------
# §2  PublicAlignmentIntegration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PublicAlignmentIntegration:
    """Connects the public-alignment subsystem to the JuGeo judgment algebra.

    The integration layer bridges:

    * The internal judgment algebra (Judgment, EvidenceBundle, etc.)
    * The public-alignment subsystem (HonestProjection, PublicClaim, etc.)

    It provides methods to:

    * Attach documentation evidence to judgments.
    * Export public obligations.
    * Merge honesty violations as obstructions.
    * Generate public judgments for external consumption.

    Theory basis (theory2.tex §13.6)
    ----------------------------------
    This class implements the natural transformation:

        η : PublicAlignment → JudgmentAlgebra

    that makes public alignment first-class within the judgment framework.

    Attributes
    ----------
    integration_id : str
        Unique identifier.
    default_audience : str
        Default audience for projection.
    enforcer : HonestyEnforcer
        Honesty enforcer for validation.
    projector : DocumentationProjector
        Documentation projector.
    algebra : JudgmentAlgebra
        Reference to the judgment algebra (optional).
    created_at : str
        ISO-8601 creation timestamp.
    """

    integration_id: str = ""
    default_audience: str = "public"
    enforcer: HonestyEnforcer = None  # type: ignore[assignment]
    projector: DocumentationProjector = None  # type: ignore[assignment]
    algebra: JudgmentAlgebra | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        """Initialize default fields."""
        if not self.integration_id:
            object.__setattr__(self, "integration_id", _new_id("integration"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())
        if self.enforcer is None:
            object.__setattr__(self, "enforcer", make_honesty_enforcer())
        if self.projector is None:
            object.__setattr__(
                self,
                "projector",
                make_documentation_projector(audience=self.default_audience),
            )

    # ------------------------------------------------------------------
    # Primary integration methods
    # ------------------------------------------------------------------

    def integrate_with_judgment(
        self,
        judgment: Judgment,
        projection: HonestProjection,
    ) -> Judgment:
        """Attach projection evidence to a judgment and return the updated judgment.

        Adds an EvidenceItem derived from *projection* to the judgment's
        evidence bundle, reflecting that the judgment has been publicly projected.

        Parameters
        ----------
        judgment : Judgment
            The internal judgment to update.
        projection : HonestProjection
            The projection to attach as evidence.

        Returns
        -------
        Judgment
            Updated judgment with projection evidence attached.
        """
        evidence_item = _make_evidence_item_from_projection(projection)
        existing_bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if existing_bundle is not None:
            existing_items = getattr(existing_bundle, "items", ())
            new_bundle = replace(
                existing_bundle,
                items=(*existing_items, evidence_item),
            )
            return replace(judgment, evidence=new_bundle)
        # Fallback: create a new bundle
        new_bundle = EvidenceBundle(
            bundle_id=_new_id("bundle"),
            items=(evidence_item,),
            coordinate=_judgment_coordinate(judgment),
        )
        return replace(judgment, evidence=new_bundle)

    def attach_documentation_evidence(
        self,
        judgment: Judgment,
        section: DocumentationSection,
    ) -> Judgment:
        """Attach documentation section as evidence to a judgment.

        Creates an EvidenceItem from a DocumentationSection and adds it
        to the judgment's evidence bundle.

        Parameters
        ----------
        judgment : Judgment
            The judgment to update.
        section : DocumentationSection
            The documentation section to attach.

        Returns
        -------
        Judgment
            Updated judgment.
        """
        content = (
            f"Documentation section {section.section_id!r}: "
            f"{section.title!r} "
            f"(trust={section.trust_level.name}, "
            f"version={section.version})."
        )
        item = EvidenceItem(
            item_id=_new_id("ev"),
            kind=EvidenceItemKind.ORACLE_PROPOSED,
            content=content,
            trust_level=section.trust_level,
            coordinate=section.coordinate,
            provenance=Provenance(
                source=ProvenanceSource.ORACLE,
                description=(
                    f"From DocumentationSection {section.section_id!r} "
                    f"via PublicAlignmentIntegration."
                ),
                timestamp=_now_iso(),
            ),
        )
        existing_bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if existing_bundle is not None:
            existing_items = getattr(existing_bundle, "items", ())
            new_bundle = replace(existing_bundle, items=(*existing_items, item))
            return replace(judgment, evidence=new_bundle)
        new_bundle = EvidenceBundle(
            bundle_id=_new_id("bundle"),
            items=(item,),
            coordinate=_judgment_coordinate(judgment),
        )
        return replace(judgment, evidence=new_bundle)

    def export_public_obligations(
        self,
        judgment: Judgment,
    ) -> tuple[ResidualObligation, ...]:
        """Export public obligations from a judgment's evidence.

        For each EvidenceItem that was attached via a public projection,
        if the trust level is below VERIFIED_PROOF, a ResidualObligation
        is produced.

        Parameters
        ----------
        judgment : Judgment
            The judgment to export obligations from.

        Returns
        -------
        tuple[ResidualObligation, ...]
            Residual obligations for public claims not yet fully discharged.
        """
        obligations: list[ResidualObligation] = []
        bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if bundle is None:
            return ()
        items = getattr(bundle, "items", ())
        j_id = _judgment_id(judgment)
        j_coord = _judgment_coordinate(judgment)
        for item in items:
            item_trust = getattr(item, "trust_level", None)
            if not isinstance(item_trust, TrustLevel):
                continue
            if item_trust == TrustLevel.VERIFIED_PROOF:
                continue
            # Build a minimal public claim to generate an obligation
            content = getattr(item, "content", "")
            coord = getattr(item, "coordinate", j_coord) or j_coord
            claim = PublicClaim(
                claim_id=_new_id("claim"),
                coordinate=coord,
                statement=str(content),
                declared_trust_level=item_trust,
                internal_trust_level=item_trust,
                source_judgment_id=j_id,
            )
            obl = _make_residual_obligation_from_claim(claim, j_id)
            obligations.append(obl)
        return tuple(obligations)

    def build_public_evidence_item(
        self,
        projection: HonestProjection,
    ) -> EvidenceItem:
        """Build an EvidenceItem from a HonestProjection.

        Parameters
        ----------
        projection : HonestProjection
            The source projection.

        Returns
        -------
        EvidenceItem
            Evidence item for use in judgment evidence bundles.
        """
        return _make_evidence_item_from_projection(projection)

    def validate_public_state(
        self,
        judgment: Judgment,
        projections: Sequence[HonestProjection],
    ) -> bool:
        """Validate that all projections derived from a judgment are honest.

        Parameters
        ----------
        judgment : Judgment
            The source judgment.
        projections : Sequence[HonestProjection]
            Projections to validate.

        Returns
        -------
        bool
            ``True`` if all projections are honest w.r.t. the judgment.
        """
        j_id = _judgment_id(judgment)
        j_trust = _judgment_trust(judgment)
        for proj in projections:
            for claim in proj.claims:
                if claim.source_judgment_id != j_id:
                    continue
                if int(claim.declared_trust_level) > int(j_trust):
                    return False
                if int(claim.declared_trust_level) > int(proj.trust_ceiling):
                    return False
        return True

    def merge_public_obstructions(
        self,
        judgment: Judgment,
        violations: Sequence[ObstructionRecord],
    ) -> Judgment:
        """Merge honesty violation records into a judgment's obstruction set.

        Each violation becomes an Obstruction on the judgment, making the
        violation a first-class semantic object that must be resolved before
        the judgment can settle.

        Parameters
        ----------
        judgment : Judgment
            The judgment to update.
        violations : Sequence[ObstructionRecord]
            Honesty violation records to merge.

        Returns
        -------
        Judgment
            Updated judgment with violations merged as obstructions.
        """
        if not violations:
            return judgment
        existing_obs = getattr(judgment, "obstructions", ()) or ()
        new_obs: list[Obstruction] = []
        for rec in violations:
            obs = Obstruction(
                obstruction_id=rec.obstruction_id,
                coordinate=rec.coordinate,
                condition_violated=rec.condition_violated,
                description=rec.description,
                is_blocking=rec.is_blocking,
                created_at=_now_iso(),
                source="public_alignment_integration",
                metadata={
                    "evidence_family": rec.evidence_family.value
                    if hasattr(rec.evidence_family, "value") else str(rec.evidence_family),
                    "trust_at_violation": rec.trust_at_violation,
                },
            )
            new_obs.append(obs)
        return replace(judgment, obstructions=(*existing_obs, *new_obs))

    def export_honesty_chain(
        self,
        violations: Sequence[ObstructionRecord],
    ) -> FailureChain:
        """Export a FailureChain from a sequence of honesty violations.

        Parameters
        ----------
        violations : Sequence[ObstructionRecord]
            The violations to chain.

        Returns
        -------
        FailureChain
            A FailureChain covering all violations.
        """
        failures: list[StructuredFailure] = []
        for obs in violations:
            failures.append(StructuredFailure(
                failure_id=_new_id("fail"),
                scope=FailureScope.SEMANTIC,
                classification=FailureClassification.POSTCONDITION,
                message=obs.description,
                coordinate=obs.coordinate,
                evidence_family=EvidenceFamily.SEMANTIC,
                trust_at_failure=obs.trust_at_violation,
                obstruction_records=(obs,),
                repair_hints=tuple(obs.repair_hints),
                is_blocking=obs.is_blocking,
                context={
                    "source_claim_id": obs.source_claim_id,
                    "condition_violated": obs.condition_violated,
                    "theory_reference": "theory2.tex §13.2 – Honesty Monotonicity",
                },
            ))
        return FailureChain(
            chain_id=_new_id("chain"),
            failures=tuple(failures),
            description=(
                f"Public alignment honesty chain: "
                f"{len(failures)} violation(s)."
            ),
            created_at=_now_iso(),
        )

    def generate_public_judgment(
        self,
        judgment: Judgment,
        audience: str = "",
    ) -> Judgment:
        """Generate a public version of a judgment for external consumption.

        The public judgment:
        * Has its trust level capped at the default trust ceiling.
        * Has its evidence bundle restricted to public-safe items.
        * Has a Provenance indicating it was derived via public alignment.
        * Has status set to PROPOSED (conservative).

        Parameters
        ----------
        judgment : Judgment
            The internal judgment.
        audience : str
            Target audience (uses ``default_audience`` if empty).

        Returns
        -------
        Judgment
            A public version of the judgment.
        """
        if not audience:
            audience = self.default_audience
        j_trust = _judgment_trust(judgment)
        ceiling = self.projector.default_trust_ceiling
        public_trust = project_trust_level(j_trust, ceiling)

        # Build a public trust annotation
        trust_ann = getattr(judgment, "trust", None)
        if trust_ann is not None and hasattr(trust_ann, "__class__"):
            try:
                new_trust = replace(trust_ann, level=public_trust)
            except TypeError:
                new_trust = trust_ann
        else:
            new_trust = TrustAnnotation(
                level=public_trust,
                justification=f"Public projection for audience {audience!r}.",
            )

        # Build public provenance
        prov = Provenance(
            source=ProvenanceSource.ORACLE,
            description=(
                f"Public judgment generated by PublicAlignmentIntegration "
                f"for audience {audience!r} "
                f"(ceiling={ceiling.name})."
            ),
            timestamp=_now_iso(),
        )

        # Restrict evidence bundle to public-safe items
        bundle = getattr(judgment, "evidence", None)
        if bundle is not None:
            items = getattr(bundle, "items", ())
            public_items = tuple(
                item for item in items
                if getattr(item, "kind", None) != EvidenceItemKind.RUNTIME_WITNESSED
                or getattr(item, "trust_level", TrustLevel.UNVERIFIED) <= public_trust
            )
            try:
                new_bundle = replace(bundle, items=public_items)
            except TypeError:
                new_bundle = bundle
        else:
            new_bundle = None

        # Build updated judgment
        kwargs: dict[str, object] = {
            "trust": new_trust,
            "provenance": prov,
            "status": JudgmentStatus.PROPOSED,
        }
        if new_bundle is not None:
            kwargs["evidence"] = new_bundle

        try:
            return replace(judgment, **kwargs)
        except TypeError:
            # If some fields cannot be replaced, return judgment with just trust
            try:
                return replace(judgment, trust=new_trust, status=JudgmentStatus.PROPOSED)
            except TypeError:
                return judgment

    # ------------------------------------------------------------------
    # Batch methods
    # ------------------------------------------------------------------

    def batch_integrate(
        self,
        judgments: Sequence[Judgment],
        projections: Sequence[HonestProjection],
    ) -> tuple[Judgment, ...]:
        """Integrate a batch of judgments with their corresponding projections.

        Matches projections to judgments by source_coordinate or claim
        source_judgment_id.

        Parameters
        ----------
        judgments : Sequence[Judgment]
            Internal judgments.
        projections : Sequence[HonestProjection]
            Corresponding projections.

        Returns
        -------
        tuple[Judgment, ...]
            Updated judgments with projection evidence attached.
        """
        # Build a mapping: judgment_id → projection
        proj_map: dict[str, HonestProjection] = {}
        for proj in projections:
            for claim in proj.claims:
                jid = claim.source_judgment_id
                if jid:
                    proj_map[jid] = proj

        result: list[Judgment] = []
        for j in judgments:
            j_id = _judgment_id(j)
            if j_id in proj_map:
                result.append(self.integrate_with_judgment(j, proj_map[j_id]))
            else:
                result.append(j)
        return tuple(result)

    def generate_all_public_obligations(
        self,
        judgments: Sequence[Judgment],
    ) -> tuple[ResidualObligation, ...]:
        """Export public obligations for all judgments.

        Parameters
        ----------
        judgments : Sequence[Judgment]
            Judgments to process.

        Returns
        -------
        tuple[ResidualObligation, ...]
            All exported obligations.
        """
        all_obligations: list[ResidualObligation] = []
        for j in judgments:
            all_obligations.extend(self.export_public_obligations(j))
        return tuple(all_obligations)

    def produce_honesty_certificate(
        self,
        projections: Sequence[HonestProjection],
    ) -> dict[str, JsonValue]:
        """Produce a honesty certificate for a set of projections.

        Parameters
        ----------
        projections : Sequence[HonestProjection]
            Projections to certify.

        Returns
        -------
        dict[str, JsonValue]
            Certificate dictionary.
        """
        return generate_honesty_certificate(projections)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def with_audience(self, audience: str) -> "PublicAlignmentIntegration":
        """Return a copy with a different default audience.

        Parameters
        ----------
        audience : str
            New default audience.

        Returns
        -------
        PublicAlignmentIntegration
            Updated integration.
        """
        return replace(
            self,
            default_audience=audience,
            projector=make_documentation_projector(
                audience=audience,
                trust_ceiling=self.projector.default_trust_ceiling,
            ),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize configuration.

        Returns
        -------
        dict[str, JsonValue]
            Serialized configuration.
        """
        return {
            "integration_id": self.integration_id,
            "default_audience": self.default_audience,
            "created_at": self.created_at,
        }

    def summarize(self) -> str:
        """Return a one-line summary.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"PublicAlignmentIntegration({self.integration_id!r}, "
            f"audience={self.default_audience!r})"
        )


# ---------------------------------------------------------------------------
# §3  Convenience factories
# ---------------------------------------------------------------------------

def make_public_alignment_integration(
    audience: str = "public",
    trust_ceiling: TrustLevel = TrustLevel.SOLVER_DISCHARGED,
) -> PublicAlignmentIntegration:
    """Create a new PublicAlignmentIntegration.

    Parameters
    ----------
    audience : str
        Default audience.
    trust_ceiling : TrustLevel
        Default trust ceiling for projections.

    Returns
    -------
    PublicAlignmentIntegration
        New integration instance.
    """
    return PublicAlignmentIntegration(
        integration_id=_new_id("integration"),
        default_audience=audience,
        enforcer=make_honesty_enforcer(),
        projector=make_documentation_projector(
            audience=audience,
            trust_ceiling=trust_ceiling,
        ),
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §4  Module-level convenience functions
# ---------------------------------------------------------------------------

def attach_projection_to_judgment(
    judgment: Judgment,
    projection: HonestProjection,
    audience: str = "public",
) -> Judgment:
    """One-shot: attach a projection as evidence to a judgment.

    Parameters
    ----------
    judgment : Judgment
        Internal judgment.
    projection : HonestProjection
        The projection to attach.
    audience : str
        Target audience (used for logging only).

    Returns
    -------
    Judgment
        Updated judgment.
    """
    integration = make_public_alignment_integration(audience=audience)
    return integration.integrate_with_judgment(judgment, projection)


def export_failure_chain_for_violations(
    violations: Sequence[ObstructionRecord],
) -> FailureChain:
    """Export a FailureChain for a sequence of obstruction records.

    Parameters
    ----------
    violations : Sequence[ObstructionRecord]
        The violation records.

    Returns
    -------
    FailureChain
        A failure chain covering all violations.
    """
    integration = make_public_alignment_integration()
    return integration.export_honesty_chain(violations)


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
    "PublicAlignmentIntegration",
    "MANIFEST_SPEC_PROVENANCE",
    # Factories
    "make_public_alignment_integration",
    # Convenience
    "attach_projection_to_judgment",
    "export_failure_chain_for_violations",
    # Helpers
    "_make_evidence_item_from_projection",
    "_make_residual_obligation_from_claim",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: integration.py — Ch13 PublicAlignmentIntegration
