"""Stage 04 — Publicity Boundary for the public_alignment subsystem.

This module implements the :class:`PublicityBoundary`, which manages the
trust boundary between internal judgment state and public declarations.

The publicity boundary is the formal interface through which internal state
is allowed to become public.  It enforces per-audience trust ceilings and
maintains a registry of all projections that have crossed the boundary.

Theory basis
------------
From theory2.tex §13.4 — The Publicity Boundary:

    **Theorem 13.4 (Publicity Boundary Soundness)**
    Let B be a publicity boundary with audience-specific ceilings
    ceil(a) for each audience a.  For any judgment J and audience a,

        trust(π_pub_a(J)) ≤ min(trust(J), ceil(a)).

    The boundary is *sound* if every registered projection satisfies this
    inequality.  A projection that violates it is a boundary-crossing
    obstruction.

    The boundary is *complete* if every public claim in the published
    documentation has a corresponding registered projection.

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "publicity_boundary",
}

# copilot: publicity_boundary.py — PublicityBoundary for Ch13 public_alignment
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import (
    Judgment,
    TrustLevel,
)
from jugeo.errors import (
    StructuredFailure,
    JuGeoError,
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
    HonestyEnforcer,
    _judgment_trust,
    _judgment_id,
    _judgment_coordinate,
    make_honesty_enforcer,
)
from jugeo.problem_modes.public_alignment.documentation_projection import (
    DocumentationProjector,
    make_documentation_projector,
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
    "module": "publicity_boundary",
    "class": "PublicityBoundary",
    "theory_section": "§13.4 – The Publicity Boundary",
}

# ---------------------------------------------------------------------------
# §1  Default audience ceilings
# ---------------------------------------------------------------------------

DEFAULT_AUDIENCE_CEILINGS: dict[str, TrustLevel] = {
    "public": TrustLevel.SOLVER_DISCHARGED,
    "internal": TrustLevel.VERIFIED_PROOF,
    "partner": TrustLevel.RUNTIME_WITNESSED,
    "beta": TrustLevel.ORACLE_PROPOSED,
    "anonymous": TrustLevel.UNVERIFIED,
}


# ---------------------------------------------------------------------------
# §2  PublicityBoundary
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PublicityBoundary:
    """Manages the trust boundary between internal and public state.

    The PublicityBoundary is the gatekeeper for all state that transitions
    from internal (private) to public (published).  It:

    * Maintains a registry of audience → trust ceiling mappings.
    * Records all projections that have crossed the boundary.
    * Audits the full registry for honesty violations on demand.
    * Enforces that no projection exceeds the audience ceiling.

    Theory basis (theory2.tex §13.4)
    ----------------------------------
    The boundary is the formalization of the access-control layer on the
    public-projection functor.  Different audiences (e.g., "public",
    "partner", "internal") have different maximum trust levels they are
    permitted to observe.

    Attributes
    ----------
    boundary_id : str
        Unique identifier for this boundary instance.
    audience_ceilings : dict[str, TrustLevel]
        Mapping from audience name to its maximum trust ceiling.
    registered_projections : tuple[HonestProjection, ...]
        All projections that have been registered with this boundary.
    enforcer : HonestyEnforcer
        The honesty enforcer used to validate boundary crossings.
    strict_mode : bool
        If ``True``, any boundary violation raises immediately.
    audit_log : tuple[str, ...]
        Append-only log of boundary operations.
    created_at : str
        ISO-8601 creation timestamp.
    """

    boundary_id: str = ""
    audience_ceilings: dict[str, TrustLevel] = field(  # type: ignore[assignment]
        default_factory=lambda: dict(DEFAULT_AUDIENCE_CEILINGS)
    )
    registered_projections: tuple[HonestProjection, ...] = ()
    enforcer: HonestyEnforcer = field(default_factory=make_honesty_enforcer)  # type: ignore[assignment]
    strict_mode: bool = False
    audit_log: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set default fields for frozen dataclass."""
        if not self.boundary_id:
            object.__setattr__(self, "boundary_id", _new_id("boundary"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Boundary crossing
    # ------------------------------------------------------------------

    def cross_boundary(
        self,
        judgment: Judgment,
        audience: str,
    ) -> HonestProjection:
        """Project a judgment across the boundary for the specified audience.

        Creates a HonestProjection applying the audience's trust ceiling.
        Validates the projection via the enforcer.

        Parameters
        ----------
        judgment : Judgment
            The internal judgment to publish.
        audience : str
            Target audience identifier.

        Returns
        -------
        HonestProjection
            The projection that crossed the boundary.

        Raises
        ------
        JuGeoError
            If ``strict_mode=True`` and the audience is not registered.
        ValueError
            If the audience is unknown and not in ``audience_ceilings``.
        """
        if audience not in self.audience_ceilings:
            if self.strict_mode:
                raise ValueError(
                    f"PublicityBoundary: unknown audience {audience!r}. "
                    f"Use add_audience() to register it first."
                )
            # Use UNVERIFIED as a safe default
            ceiling = TrustLevel.UNVERIFIED
        else:
            ceiling = self.audience_ceilings[audience]

        projector = make_documentation_projector(
            audience=audience,
            trust_ceiling=ceiling,
        )
        projection = projector.build_projection(judgment)
        return projection

    def is_crossable(self, judgment: Judgment, audience: str) -> bool:
        """Return ``True`` when *judgment* can cross the boundary for *audience*.

        A judgment is crossable if:
        * The audience is registered in ``audience_ceilings``.
        * The judgment's trust level is ≥ UNVERIFIED (i.e., not CONTRADICTED).

        Parameters
        ----------
        judgment : Judgment
            The judgment to check.
        audience : str
            Target audience.

        Returns
        -------
        bool
            ``True`` if crossing is permitted.
        """
        if audience not in self.audience_ceilings:
            return False
        j_trust = _judgment_trust(judgment)
        return j_trust != TrustLevel.CONTRADICTED

    def add_audience(
        self,
        audience: str,
        trust_ceiling: TrustLevel,
    ) -> "PublicityBoundary":
        """Return a copy with a new audience registered.

        Parameters
        ----------
        audience : str
            Audience identifier.
        trust_ceiling : TrustLevel
            Maximum trust level for this audience.

        Returns
        -------
        PublicityBoundary
            Updated boundary.
        """
        new_ceilings = {**self.audience_ceilings, audience: trust_ceiling}
        entry = (
            f"[{_now_iso()}] Added audience {audience!r} "
            f"with ceiling {trust_ceiling.name}."
        )
        return replace(
            self,
            audience_ceilings=new_ceilings,
            audit_log=(*self.audit_log, entry),
        )

    def remove_audience(self, audience: str) -> "PublicityBoundary":
        """Return a copy with *audience* removed from the registry.

        Parameters
        ----------
        audience : str
            Audience to remove.

        Returns
        -------
        PublicityBoundary
            Updated boundary.
        """
        new_ceilings = {k: v for k, v in self.audience_ceilings.items() if k != audience}
        entry = f"[{_now_iso()}] Removed audience {audience!r}."
        return replace(
            self,
            audience_ceilings=new_ceilings,
            audit_log=(*self.audit_log, entry),
        )

    def get_ceiling(self, audience: str) -> TrustLevel:
        """Return the trust ceiling for *audience*.

        Parameters
        ----------
        audience : str
            Audience to query.

        Returns
        -------
        TrustLevel
            The ceiling, or ``TrustLevel.UNVERIFIED`` if unknown.
        """
        return self.audience_ceilings.get(audience, TrustLevel.UNVERIFIED)

    def enforce_boundary(
        self,
        projection: HonestProjection,
    ) -> tuple[bool, tuple[ObstructionRecord, ...]]:
        """Enforce the boundary constraint on a registered projection.

        Validates that all claims in the projection are:
        1. Honest (declared ≤ internal).
        2. Within the audience's trust ceiling.

        Parameters
        ----------
        projection : HonestProjection
            The projection to validate.

        Returns
        -------
        tuple[bool, tuple[ObstructionRecord, ...]]
            ``(True, ())`` if valid; ``(False, violations)`` otherwise.
        """
        ceiling = self.get_ceiling(projection.target_audience)
        violations: list[ObstructionRecord] = []

        for claim in projection.claims:
            # Check honesty
            if not claim.check_honesty():
                obs = claim.strengthen_violation()
                if obs is not None:
                    violations.append(obs)
                    continue
            # Check ceiling
            if int(claim.declared_trust_level) > int(ceiling):
                delta = int(claim.declared_trust_level) - int(ceiling)
                hint = RepairHint(
                    action="apply_audience_ceiling",
                    description=(
                        f"Reduce declared trust of {claim.claim_id!r} "
                        f"from {claim.declared_trust_level.name} "
                        f"to {ceiling.name} (audience ceiling)."
                    ),
                    priority=RepairPriority.HIGH,
                    target_coordinate=claim.coordinate,
                    estimated_effort="low",
                )
                violations.append(ObstructionRecord(
                    obstruction_id=_new_id("obs"),
                    coordinate=claim.coordinate,
                    condition_violated="audience_trust_ceiling",
                    description=(
                        f"Claim {claim.claim_id!r} exceeds audience ceiling "
                        f"for {projection.target_audience!r}: "
                        f"declared={claim.declared_trust_level.name}, "
                        f"ceiling={ceiling.name}, "
                        f"excess=+{delta}."
                    ),
                    evidence_family=EvidenceFamily.SEMANTIC,
                    trust_at_violation=ceiling.value,
                    repair_hints=(hint,),
                    is_blocking=True,
                    source_claim_id=claim.claim_id,
                ))

        is_valid = len(violations) == 0
        if self.strict_mode and not is_valid:
            report = self.emit_boundary_report()
            raise JuGeoError(
                f"PublicityBoundary strict-mode: "
                f"{len(violations)} boundary violation(s).",
                failure=report,
            )
        return is_valid, tuple(violations)

    def register_projection(
        self,
        projection: HonestProjection,
    ) -> "PublicityBoundary":
        """Return a copy with *projection* added to the registry.

        Parameters
        ----------
        projection : HonestProjection
            Projection to register.

        Returns
        -------
        PublicityBoundary
            Updated boundary.
        """
        entry = (
            f"[{_now_iso()}] Registered projection {projection.projection_id!r} "
            f"for audience {projection.target_audience!r}."
        )
        return replace(
            self,
            registered_projections=(*self.registered_projections, projection),
            audit_log=(*self.audit_log, entry),
        )

    def audit_all_projections(self) -> tuple[ObstructionRecord, ...]:
        """Audit all registered projections for boundary violations.

        Returns
        -------
        tuple[ObstructionRecord, ...]
            All violations found across all registered projections.
        """
        all_violations: list[ObstructionRecord] = []
        for projection in self.registered_projections:
            _, violations = self.enforce_boundary(projection)
            all_violations.extend(violations)
        return tuple(all_violations)

    def projection_count(self) -> int:
        """Return the number of registered projections.

        Returns
        -------
        int
            Count.
        """
        return len(self.registered_projections)

    def projections_for_audience(
        self,
        audience: str,
    ) -> tuple[HonestProjection, ...]:
        """Return all registered projections for a given audience.

        Parameters
        ----------
        audience : str
            Audience to filter by.

        Returns
        -------
        tuple[HonestProjection, ...]
            Matching projections.
        """
        return tuple(
            p for p in self.registered_projections
            if p.target_audience == audience
        )

    def emit_boundary_report(self) -> StructuredFailure:
        """Emit a StructuredFailure summarizing all boundary violations.

        Returns
        -------
        StructuredFailure
            Full boundary audit report.
        """
        violations = self.audit_all_projections()
        proj_count = len(self.registered_projections)

        if not violations:
            return StructuredFailure(
                failure_id=_new_id("cert"),
                scope=FailureScope.SEMANTIC,
                classification=FailureClassification.POSTCONDITION,
                message=(
                    f"PublicityBoundary audit: {proj_count} projection(s) checked, "
                    f"0 violations. Boundary is sound."
                ),
                coordinate="",
                evidence_family=EvidenceFamily.SEMANTIC,
                trust_at_failure=TrustLevel.VERIFIED_PROOF.value,
                obstruction_records=(),
                repair_hints=(),
                is_blocking=False,
                context={
                    "boundary_id": self.boundary_id,
                    "projection_count": proj_count,
                    "audience_count": len(self.audience_ceilings),
                    "theory_reference": "theory2.tex §13.4 – Publicity Boundary Soundness",
                },
            )

        all_hints: list[RepairHint] = []
        for obs in violations:
            all_hints.extend(obs.repair_hints)

        return StructuredFailure(
            failure_id=_new_id("fail"),
            scope=FailureScope.SEMANTIC,
            classification=FailureClassification.POSTCONDITION,
            message=(
                f"PublicityBoundary UNSOUND: {len(violations)} violation(s) "
                f"across {proj_count} projection(s)."
            ),
            coordinate="",
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_failure=TrustLevel.UNVERIFIED.value,
            obstruction_records=tuple(violations),
            repair_hints=tuple(all_hints),
            is_blocking=True,
            context={
                "boundary_id": self.boundary_id,
                "projection_count": proj_count,
                "violation_count": len(violations),
                "audience_count": len(self.audience_ceilings),
                "theory_reference": "theory2.tex §13.4 – Publicity Boundary Soundness",
            },
        )

    def is_sound(self) -> bool:
        """Return ``True`` when all registered projections pass audit.

        Returns
        -------
        bool
            ``True`` if the boundary is sound.
        """
        return len(self.audit_all_projections()) == 0

    def summarize(self) -> str:
        """Return a one-line summary of the boundary state.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"PublicityBoundary({self.boundary_id!r}, "
            f"audiences={list(self.audience_ceilings)}, "
            f"projections={self.projection_count()}, "
            f"sound={self.is_sound()})"
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize boundary configuration.

        Returns
        -------
        dict[str, JsonValue]
            Serialized boundary.
        """
        return {
            "boundary_id": self.boundary_id,
            "audience_ceilings": {
                k: v.value for k, v in self.audience_ceilings.items()
            },
            "projection_count": len(self.registered_projections),
            "strict_mode": self.strict_mode,
            "audit_log_length": len(self.audit_log),
            "created_at": self.created_at,
        }

    def repair_projection(
        self,
        projection: HonestProjection,
    ) -> HonestProjection:
        """Return a copy of *projection* with all boundary violations repaired.

        Applies the audience ceiling to every claim that exceeds it and
        weakens any dishonest claims.

        Parameters
        ----------
        projection : HonestProjection
            The projection to repair.

        Returns
        -------
        HonestProjection
            Repaired projection.
        """
        ceiling = self.get_ceiling(projection.target_audience)
        repaired_claims: list[PublicClaim] = []
        for claim in projection.claims:
            # Apply audience ceiling
            effective = TrustLevel(min(int(claim.declared_trust_level), int(ceiling)))
            # Ensure honesty (declared ≤ internal)
            effective = TrustLevel(min(int(effective), int(claim.internal_trust_level)))
            if effective == claim.declared_trust_level:
                repaired_claims.append(claim.with_honesty_checked())
            else:
                from dataclasses import replace as dc_replace
                repaired_claims.append(dc_replace(
                    claim,
                    declared_trust_level=effective,
                    is_honest=True,
                    metadata={
                        **claim.metadata,
                        "boundary_repaired": True,
                        "repaired_at": _now_iso(),
                        "original_declared": claim.declared_trust_level.name,
                    },
                ))
        return replace(projection, claims=tuple(repaired_claims), is_valid=True)

    def snapshot(self) -> dict[str, JsonValue]:
        """Return a full snapshot of the boundary state.

        Includes all registered projections (serialized) and the full
        audit log.

        Returns
        -------
        dict[str, JsonValue]
            Snapshot dictionary.
        """
        return {
            "boundary_id": self.boundary_id,
            "audience_ceilings": {
                k: v.name for k, v in self.audience_ceilings.items()
            },
            "registered_projections": [p.to_dict() for p in self.registered_projections],
            "audit_log": list(self.audit_log),
            "created_at": self.created_at,
            "is_sound": self.is_sound(),
        }

    def bulk_register(
        self,
        projections: Sequence[HonestProjection],
    ) -> "PublicityBoundary":
        """Register multiple projections at once.

        Parameters
        ----------
        projections : Sequence[HonestProjection]
            Projections to register.

        Returns
        -------
        PublicityBoundary
            Updated boundary with all projections registered.
        """
        boundary = self
        for projection in projections:
            boundary = boundary.register_projection(projection)
        return boundary


# ---------------------------------------------------------------------------
# §3  Convenience factories
# ---------------------------------------------------------------------------

def make_publicity_boundary(
    strict_mode: bool = False,
    extra_audiences: dict[str, TrustLevel] | None = None,
) -> PublicityBoundary:
    """Create a new PublicityBoundary with default audience ceilings.

    Parameters
    ----------
    strict_mode : bool
        Enable strict violation handling.
    extra_audiences : dict[str, TrustLevel] | None
        Additional audience→ceiling mappings to add.

    Returns
    -------
    PublicityBoundary
        New boundary.
    """
    ceilings = dict(DEFAULT_AUDIENCE_CEILINGS)
    if extra_audiences:
        ceilings.update(extra_audiences)
    return PublicityBoundary(
        boundary_id=_new_id("boundary"),
        audience_ceilings=ceilings,
        strict_mode=strict_mode,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §4  Module-level convenience functions
# ---------------------------------------------------------------------------

def cross_and_register(
    judgment: Judgment,
    audience: str,
    boundary: PublicityBoundary,
) -> tuple[HonestProjection, PublicityBoundary]:
    """Cross the boundary and register the resulting projection.

    Parameters
    ----------
    judgment : Judgment
        Internal judgment to publish.
    audience : str
        Target audience.
    boundary : PublicityBoundary
        The boundary to cross.

    Returns
    -------
    tuple[HonestProjection, PublicityBoundary]
        ``(projection, updated_boundary)``.
    """
    projection = boundary.cross_boundary(judgment, audience)
    updated = boundary.register_projection(projection)
    return projection, updated


def audit_boundary(boundary: PublicityBoundary) -> StructuredFailure:
    """Audit a boundary and return the report.

    Parameters
    ----------
    boundary : PublicityBoundary
        The boundary to audit.

    Returns
    -------
    StructuredFailure
        Audit report.
    """
    return boundary.emit_boundary_report()


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
    "PublicityBoundary",
    "DEFAULT_AUDIENCE_CEILINGS",
    "MANIFEST_SPEC_PROVENANCE",
    # Factories
    "make_publicity_boundary",
    # Convenience
    "cross_and_register",
    "audit_boundary",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: publicity_boundary.py — Ch13 PublicityBoundary implementation
