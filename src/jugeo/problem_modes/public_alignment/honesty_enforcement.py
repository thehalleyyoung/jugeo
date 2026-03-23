"""Stage 01 — Honesty Enforcement for the public_alignment subsystem.

This module implements the :class:`HonestyEnforcer`, which is the core
validation component that ensures public outputs never silently strengthen
internal claims (theory2.tex §13.2 — the Honesty Monotonicity Law).

The HonestyEnforcer is the gatekeeper between the internal judgment algebra
and the public projection surface.  Every projection must pass through
the enforcer before it can be published.

Theory basis
------------
From theory2.tex §13.2:

    **Theorem 13.1 (Honesty Monotonicity)**
    For any HonestProjection π_pub and any internal judgment J,

        trust(π_pub(J)) ≤ trust(J)

    Any projection that violates this is a non-trivial class in Ȟ¹ of the
    semantic site and must be recorded as an ObstructionRecord.

    **Corollary 13.2 (No Silent Upgrade)**
    There is no admissible repair that increases the declared trust level
    of a public claim without also increasing the internal trust level.

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "honesty_enforcement",
}

# copilot: honesty_enforcement.py — HonestyEnforcer for Ch13 public_alignment
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

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
    as_failure_payload,
)
from jugeo.problem_modes.public_alignment.models import (
    PublicClaim,
    HonestProjection,
    MigrationPlan,
    _now_iso,
    _new_id,
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
    "module": "honesty_enforcement",
    "class": "HonestyEnforcer",
    "theory_section": "§13.2 – Honesty Monotonicity Law",
}


# ---------------------------------------------------------------------------
# §1  Helper — extract trust level from a Judgment
# ---------------------------------------------------------------------------

def _judgment_trust(judgment: Judgment) -> TrustLevel:
    """Extract the TrustLevel from a Judgment, with a safe fallback.

    Parameters
    ----------
    judgment : Judgment
        The internal judgment.

    Returns
    -------
    TrustLevel
        The trust level, or ``TrustLevel.UNVERIFIED`` if unavailable.
    """
    trust_ann = getattr(judgment, "trust", None)
    if trust_ann is not None:
        level = getattr(trust_ann, "level", None)
        if isinstance(level, TrustLevel):
            return level
    # Fall back to a direct attribute
    level = getattr(judgment, "trust_level", None)
    if isinstance(level, TrustLevel):
        return level
    return TrustLevel.UNVERIFIED


def _judgment_id(judgment: Judgment) -> str:
    """Extract a string identifier from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    str
        Judgment ID or a generated fallback.
    """
    jid = getattr(judgment, "judgment_id", None)
    if jid:
        return str(jid)
    return _new_id("jid")


def _judgment_coordinate(judgment: Judgment) -> str:
    """Extract the coordinate string from a Judgment.

    Parameters
    ----------
    judgment : Judgment
        The judgment.

    Returns
    -------
    str
        Coordinate path string.
    """
    coord = getattr(judgment, "coordinate", None)
    if coord is None:
        return ""
    if hasattr(coord, "path"):
        return str(coord.path)
    return str(coord)


# ---------------------------------------------------------------------------
# §2  HonestyEnforcer
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HonestyEnforcer:
    """Validates that public outputs never silently strengthen internal claims.

    The HonestyEnforcer is the primary gatekeeper of the public-alignment
    pipeline.  It checks every :class:`~models.HonestProjection` against the
    internal :class:`~jugeo.judgments.judgment_terms.Judgment` objects that
    produced it, and flags any claim where the declared trust level exceeds
    the internal trust level (a Ȟ¹ violation).

    Theory basis (theory2.tex §13.2)
    ---------------------------------
    The core invariant is:

        trust(π_pub(J)) ≤ trust(J)

    Violations are *silent-strengthening obstructions*.  They are recorded as
    :class:`~jugeo.errors.ObstructionRecord` objects, which are first-class
    semantic objects in the JuGeo cohomology framework.

    Attributes
    ----------
    enforcer_id : str
        Unique identifier for this enforcer instance.
    strict_mode : bool
        If ``True``, any violation raises :class:`~jugeo.errors.JuGeoError`
        immediately.  If ``False`` (default), violations are collected and
        returned for downstream handling.
    trust_tolerance : int
        Number of trust levels by which declared may exceed internal before
        a violation is recorded.  Default is 0 (zero tolerance).
    audit_log : tuple[str, ...]
        Append-only audit log of enforcement actions.
    created_at : str
        ISO-8601 creation timestamp.
    """

    enforcer_id: str = ""
    strict_mode: bool = False
    trust_tolerance: int = 0
    audit_log: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set default fields via object.__setattr__ for frozen dataclass."""
        if not self.enforcer_id:
            object.__setattr__(self, "enforcer_id", _new_id("enforcer"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Primary enforcement methods
    # ------------------------------------------------------------------

    def enforce(
        self,
        projection: HonestProjection,
        internal_judgment: Judgment,
    ) -> tuple[bool, tuple[ObstructionRecord, ...]]:
        """Enforce the honesty invariant for a single projection.

        Validates every :class:`~models.PublicClaim` in *projection* against
        *internal_judgment*.  A claim is honest iff its declared trust level
        does not exceed the judgment's trust level (within ``trust_tolerance``).

        Parameters
        ----------
        projection : HonestProjection
            The projection to validate.
        internal_judgment : Judgment
            The internal judgment that *projection* was derived from.

        Returns
        -------
        tuple[bool, tuple[ObstructionRecord, ...]]
            ``(True, ())`` if all claims are honest;
            ``(False, (violations...))`` if any are not.

        Raises
        ------
        JuGeoError
            If ``strict_mode=True`` and any violation is found.
        """
        obstructions: list[ObstructionRecord] = []
        j_trust = _judgment_trust(internal_judgment)
        j_id = _judgment_id(internal_judgment)
        j_coord = _judgment_coordinate(internal_judgment)

        for claim in projection.claims:
            # Use claim's source_judgment_id to associate; if not matching, skip
            if claim.source_judgment_id and claim.source_judgment_id != j_id:
                # This claim comes from a different judgment — skip
                continue
            obs = self._check_claim_against_trust(claim, j_trust, j_coord)
            if obs is not None:
                obstructions.append(obs)

        # Also check claims with no source_judgment_id against the global trust
        for claim in projection.claims:
            if not claim.source_judgment_id:
                obs = self._check_claim_against_trust(claim, j_trust, j_coord)
                if obs is not None and obs not in obstructions:
                    obstructions.append(obs)

        is_valid = len(obstructions) == 0

        if self.strict_mode and not is_valid:
            fail = self.emit_honesty_report(obstructions)
            raise JuGeoError(
                f"HonestyEnforcer strict-mode: {len(obstructions)} violation(s) found.",
                failure=fail,
            )

        return is_valid, tuple(obstructions)

    def _check_claim_against_trust(
        self,
        claim: PublicClaim,
        j_trust: TrustLevel,
        j_coord: str,
    ) -> ObstructionRecord | None:
        """Check a single claim against a given internal trust level.

        Parameters
        ----------
        claim : PublicClaim
            The claim to check.
        j_trust : TrustLevel
            The internal trust level to check against.
        j_coord : str
            Coordinate string for the judgment.

        Returns
        -------
        ObstructionRecord | None
            ``None`` if honest; an ObstructionRecord if violated.
        """
        delta = int(claim.declared_trust_level) - int(j_trust)
        if delta <= self.trust_tolerance:
            return None  # honest

        hint = RepairHint(
            action="weaken_declared_trust",
            description=(
                f"Lower declared_trust_level of claim {claim.claim_id!r} from "
                f"{claim.declared_trust_level.name} to {j_trust.name} "
                f"(over-declaration by {delta} levels)."
            ),
            priority=RepairPriority.HIGH,
            target_coordinate=claim.coordinate or j_coord,
            estimated_effort="low",
        )
        return ObstructionRecord(
            obstruction_id=_new_id("obs"),
            coordinate=claim.coordinate or j_coord,
            condition_violated="honesty_monotonicity",
            description=(
                f"Silent strengthening on claim {claim.claim_id!r}: "
                f"declared={claim.declared_trust_level.name}, "
                f"internal={j_trust.name}, "
                f"delta=+{delta}."
            ),
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_violation=j_trust.value,
            repair_hints=(hint,),
            is_blocking=True,
            source_claim_id=claim.claim_id,
        )

    def check_claim_honesty(
        self,
        claim: PublicClaim,
        judgment: Judgment,
    ) -> bool:
        """Check whether a single claim is honest w.r.t. a judgment.

        Parameters
        ----------
        claim : PublicClaim
            The public claim to validate.
        judgment : Judgment
            The corresponding internal judgment.

        Returns
        -------
        bool
            ``True`` if honest; ``False`` otherwise.
        """
        j_trust = _judgment_trust(judgment)
        delta = int(claim.declared_trust_level) - int(j_trust)
        return delta <= self.trust_tolerance

    def detect_silent_strengthening(
        self,
        claims: Sequence[PublicClaim],
        judgments: Mapping[str, Judgment],
    ) -> tuple[PublicClaim, ...]:
        """Detect all claims that silently strengthen their source judgment.

        Matches each claim to its source judgment by ``source_judgment_id``.
        Claims with no matching judgment entry are skipped.

        Parameters
        ----------
        claims : Sequence[PublicClaim]
            The public claims to inspect.
        judgments : Mapping[str, Judgment]
            Mapping from judgment ID to Judgment object.

        Returns
        -------
        tuple[PublicClaim, ...]
            Claims that violate the honesty invariant.
        """
        violations: list[PublicClaim] = []
        for claim in claims:
            jid = claim.source_judgment_id
            if not jid or jid not in judgments:
                continue
            judgment = judgments[jid]
            if not self.check_claim_honesty(claim, judgment):
                violations.append(claim)
        return tuple(violations)

    def generate_honesty_obstruction(
        self,
        claim: PublicClaim,
        judgment: Judgment,
    ) -> ObstructionRecord:
        """Generate an ObstructionRecord for a dishonest claim.

        The record is generated regardless of whether the claim is actually
        honest or not.  Use ``detect_silent_strengthening`` to filter first.

        Parameters
        ----------
        claim : PublicClaim
            The (potentially dishonest) public claim.
        judgment : Judgment
            The corresponding internal judgment.

        Returns
        -------
        ObstructionRecord
            The obstruction record for this violation.
        """
        j_trust = _judgment_trust(judgment)
        j_coord = _judgment_coordinate(judgment)
        delta = int(claim.declared_trust_level) - int(j_trust)
        hint = RepairHint(
            action="weaken_declared_trust",
            description=(
                f"Reduce declared trust of {claim.claim_id!r} "
                f"from {claim.declared_trust_level.name} "
                f"to {j_trust.name}."
            ),
            priority=RepairPriority.HIGH if delta > 1 else RepairPriority.MEDIUM,
            target_coordinate=claim.coordinate or j_coord,
            estimated_effort="low",
        )
        return ObstructionRecord(
            obstruction_id=_new_id("obs"),
            coordinate=claim.coordinate or j_coord,
            condition_violated="honesty_monotonicity",
            description=(
                f"Claim {claim.claim_id!r} at {claim.coordinate!r}: "
                f"declared={claim.declared_trust_level.name}, "
                f"internal={j_trust.name}, "
                f"delta=+{max(0, delta)}."
            ),
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_violation=j_trust.value,
            repair_hints=(hint,),
            is_blocking=delta > 0,
            source_claim_id=claim.claim_id,
        )

    def enforce_batch(
        self,
        projections: Sequence[HonestProjection],
        judgments: Mapping[str, Judgment],
    ) -> tuple[ObstructionRecord, ...]:
        """Batch-check all projections against their corresponding judgments.

        Each claim in each projection is matched to its source judgment by
        ``source_judgment_id``.  All violations are collected and returned.

        Parameters
        ----------
        projections : Sequence[HonestProjection]
            The projections to check.
        judgments : Mapping[str, Judgment]
            Mapping from judgment ID to Judgment.

        Returns
        -------
        tuple[ObstructionRecord, ...]
            All violations found across all projections.
        """
        all_violations: list[ObstructionRecord] = []
        for projection in projections:
            for claim in projection.claims:
                jid = claim.source_judgment_id
                if not jid or jid not in judgments:
                    continue
                j = judgments[jid]
                j_trust = _judgment_trust(j)
                j_coord = _judgment_coordinate(j)
                obs = self._check_claim_against_trust(claim, j_trust, j_coord)
                if obs is not None:
                    all_violations.append(obs)
        return tuple(all_violations)

    def repair_dishonest_claim(
        self,
        claim: PublicClaim,
        judgment: Judgment,
    ) -> PublicClaim:
        """Repair a dishonest claim by weakening its declared trust level.

        Returns the original claim if already honest.  Otherwise weakens the
        declared trust level to exactly match the judgment's internal trust
        level (the canonical repair from theory2.tex §13.2.6).

        Parameters
        ----------
        claim : PublicClaim
            The claim to repair.
        judgment : Judgment
            The corresponding internal judgment.

        Returns
        -------
        PublicClaim
            A repaired (honest) claim.
        """
        j_trust = _judgment_trust(judgment)
        if int(claim.declared_trust_level) <= int(j_trust) + self.trust_tolerance:
            return claim.with_honesty_checked()
        repaired = replace(
            claim,
            declared_trust_level=j_trust,
            internal_trust_level=j_trust,
            is_honest=True,
            metadata={
                **claim.metadata,
                "repaired_by_enforcer": True,
                "repaired_at": _now_iso(),
                "original_declared": claim.declared_trust_level.name,
            },
        )
        return repaired

    def compute_trust_delta(
        self,
        claim: PublicClaim,
        judgment: Judgment,
    ) -> int:
        """Compute the trust over-declaration delta for a claim.

        Returns the number of trust levels by which *claim* over-declares
        relative to *judgment*.  A positive value means over-declaration
        (violation); non-positive means honest.

        Parameters
        ----------
        claim : PublicClaim
            The public claim.
        judgment : Judgment
            The corresponding internal judgment.

        Returns
        -------
        int
            ``int(declared) - int(internal)``.  Positive = violation.
        """
        j_trust = _judgment_trust(judgment)
        return int(claim.declared_trust_level) - int(j_trust)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def emit_honesty_report(
        self,
        violations: Sequence[ObstructionRecord],
    ) -> StructuredFailure:
        """Emit a StructuredFailure summarizing all honesty violations.

        Parameters
        ----------
        violations : Sequence[ObstructionRecord]
            The collected violations.

        Returns
        -------
        StructuredFailure
            A structured failure payload describing the violations.
        """
        if not violations:
            # Return a clean "no violations" report
            return StructuredFailure(
                failure_id=_new_id("fail"),
                scope=FailureScope.SEMANTIC,
                classification=FailureClassification.POSTCONDITION,
                message="Honesty enforcement: no violations found.",
                coordinate="",
                evidence_family=EvidenceFamily.SEMANTIC,
                trust_at_failure=TrustLevel.VERIFIED_PROOF.value,
                obstruction_records=(),
                repair_hints=(),
                is_blocking=False,
                context={"enforcer_id": self.enforcer_id, "violation_count": 0},
            )

        all_hints: list[RepairHint] = []
        for obs in violations:
            all_hints.extend(obs.repair_hints)

        coords = list({obs.coordinate for obs in violations})
        return StructuredFailure(
            failure_id=_new_id("fail"),
            scope=FailureScope.SEMANTIC,
            classification=FailureClassification.POSTCONDITION,
            message=(
                f"Honesty enforcement failed: {len(violations)} silent-strengthening "
                f"obstruction(s) detected across {len(coords)} coordinate(s)."
            ),
            coordinate=coords[0] if len(coords) == 1 else f"[{len(coords)} coords]",
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_failure=min(
                (obs.trust_at_violation for obs in violations),
                default=TrustLevel.UNVERIFIED.value,
            ),
            obstruction_records=tuple(violations),
            repair_hints=tuple(all_hints),
            is_blocking=any(obs.is_blocking for obs in violations),
            context={
                "enforcer_id": self.enforcer_id,
                "violation_count": len(violations),
                "affected_coordinates": coords,
                "theory_reference": "theory2.tex §13.2 – Honesty Monotonicity",
            },
        )

    def audit_projection(
        self,
        projection: HonestProjection,
        judgments: Mapping[str, Judgment],
    ) -> dict[str, JsonValue]:
        """Produce a full audit report for a single projection.

        Parameters
        ----------
        projection : HonestProjection
            The projection to audit.
        judgments : Mapping[str, Judgment]
            Mapping from judgment ID to Judgment.

        Returns
        -------
        dict[str, JsonValue]
            Audit report with claim-level detail.
        """
        claim_reports: list[dict[str, JsonValue]] = []
        total_violations = 0
        for claim in projection.claims:
            jid = claim.source_judgment_id
            if jid and jid in judgments:
                j = judgments[jid]
                delta = self.compute_trust_delta(claim, j)
                is_honest = delta <= self.trust_tolerance
                if not is_honest:
                    total_violations += 1
            else:
                is_honest = claim.check_honesty()
                delta = -claim.honesty_delta()
                if not is_honest:
                    total_violations += 1
            claim_reports.append({
                "claim_id": claim.claim_id,
                "coordinate": claim.coordinate,
                "declared": claim.declared_trust_level.name,
                "internal": claim.internal_trust_level.name,
                "delta": delta,
                "honest": is_honest,
            })

        return {
            "projection_id": projection.projection_id,
            "audience": projection.target_audience,
            "total_claims": len(projection.claims),
            "total_violations": total_violations,
            "is_valid": total_violations == 0,
            "enforcer_id": self.enforcer_id,
            "audited_at": _now_iso(),
            "claims": claim_reports,
        }

    def with_strict_mode(self, strict: bool) -> "HonestyEnforcer":
        """Return a copy with strict_mode set.

        Parameters
        ----------
        strict : bool
            New strict mode setting.

        Returns
        -------
        HonestyEnforcer
            Updated enforcer.
        """
        return replace(self, strict_mode=strict)

    def with_tolerance(self, tolerance: int) -> "HonestyEnforcer":
        """Return a copy with a different trust_tolerance.

        Parameters
        ----------
        tolerance : int
            New tolerance (0 = zero tolerance).

        Returns
        -------
        HonestyEnforcer
            Updated enforcer.
        """
        return replace(self, trust_tolerance=max(0, tolerance))

    def log_action(self, message: str) -> "HonestyEnforcer":
        """Return a copy with *message* appended to the audit log.

        Parameters
        ----------
        message : str
            Audit log entry.

        Returns
        -------
        HonestyEnforcer
            Updated enforcer.
        """
        entry = f"[{_now_iso()}] {message}"
        return replace(self, audit_log=(*self.audit_log, entry))

    # ------------------------------------------------------------------
    # Advanced methods
    # ------------------------------------------------------------------

    def repair_projection(
        self,
        projection: HonestProjection,
        judgments: Mapping[str, Judgment],
    ) -> HonestProjection:
        """Return a new projection with all dishonest claims repaired.

        Parameters
        ----------
        projection : HonestProjection
            The projection to repair.
        judgments : Mapping[str, Judgment]
            Mapping from judgment ID to Judgment.

        Returns
        -------
        HonestProjection
            Repaired projection (all claims honest).
        """
        repaired_claims: list[PublicClaim] = []
        for claim in projection.claims:
            jid = claim.source_judgment_id
            if jid and jid in judgments:
                repaired_claims.append(
                    self.repair_dishonest_claim(claim, judgments[jid])
                )
            else:
                repaired_claims.append(claim.with_honesty_checked())

        return replace(
            projection,
            claims=tuple(repaired_claims),
            is_valid=True,
        )

    def summarize_violations(
        self,
        violations: Sequence[ObstructionRecord],
    ) -> str:
        """Produce a human-readable summary of violations.

        Parameters
        ----------
        violations : Sequence[ObstructionRecord]
            Violations to summarize.

        Returns
        -------
        str
            Multi-line summary text.
        """
        if not violations:
            return "No honesty violations detected."
        lines = [
            f"Honesty Enforcement Report — {len(violations)} violation(s):",
            "=" * 60,
        ]
        for i, obs in enumerate(violations, 1):
            lines.append(f"  [{i}] {obs.description}")
            for hint in obs.repair_hints:
                lines.append(f"       REPAIR: {hint.description}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def compute_honesty_score(self, projection: HonestProjection) -> float:
        """Return the fraction of claims in *projection* that are honest.

        Parameters
        ----------
        projection : HonestProjection
            The projection to score.

        Returns
        -------
        float
            Honesty score in [0.0, 1.0].
        """
        if not projection.claims:
            return 1.0
        honest_count = sum(1 for c in projection.claims if c.check_honesty())
        return honest_count / len(projection.claims)

    def is_projection_admissible(
        self,
        projection: HonestProjection,
        judgments: Mapping[str, Judgment],
    ) -> bool:
        """Return ``True`` iff the projection passes full honesty enforcement.

        Parameters
        ----------
        projection : HonestProjection
            The projection to check.
        judgments : Mapping[str, Judgment]
            Source judgments.

        Returns
        -------
        bool
            ``True`` if admissible.
        """
        violations = self.enforce_batch([projection], judgments)
        return len(violations) == 0

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize this enforcer's configuration.

        Returns
        -------
        dict[str, JsonValue]
            Serialized configuration.
        """
        return {
            "enforcer_id": self.enforcer_id,
            "strict_mode": self.strict_mode,
            "trust_tolerance": self.trust_tolerance,
            "audit_log": list(self.audit_log),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# §3  Convenience factory
# ---------------------------------------------------------------------------

def make_honesty_enforcer(
    strict_mode: bool = False,
    trust_tolerance: int = 0,
) -> HonestyEnforcer:
    """Create a new HonestyEnforcer instance.

    Parameters
    ----------
    strict_mode : bool
        If ``True``, violations raise :class:`~jugeo.errors.JuGeoError`.
    trust_tolerance : int
        Number of trust levels by which declared may exceed internal.

    Returns
    -------
    HonestyEnforcer
        New enforcer.
    """
    return HonestyEnforcer(
        enforcer_id=_new_id("enforcer"),
        strict_mode=strict_mode,
        trust_tolerance=trust_tolerance,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §4  Module-level convenience functions
# ---------------------------------------------------------------------------

def enforce_once(
    projection: HonestProjection,
    judgment: Judgment,
    strict: bool = False,
) -> tuple[bool, tuple[ObstructionRecord, ...]]:
    """One-shot enforcement of a single projection against a single judgment.

    Parameters
    ----------
    projection : HonestProjection
        The projection to validate.
    judgment : Judgment
        The internal judgment.
    strict : bool
        If ``True``, raise on first violation.

    Returns
    -------
    tuple[bool, tuple[ObstructionRecord, ...]]
        ``(is_valid, violations)``.
    """
    enforcer = make_honesty_enforcer(strict_mode=strict)
    return enforcer.enforce(projection, judgment)


def quick_honesty_check(claim: PublicClaim) -> bool:
    """Quick structural honesty check (no judgment lookup required).

    Uses only the claim's own ``declared_trust_level`` vs
    ``internal_trust_level`` fields.

    Parameters
    ----------
    claim : PublicClaim
        The claim to check.

    Returns
    -------
    bool
        ``True`` if honest.
    """
    return claim.check_honesty()


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
    "HonestyEnforcer",
    "MANIFEST_SPEC_PROVENANCE",
    # Helpers
    "make_honesty_enforcer",
    "enforce_once",
    "quick_honesty_check",
    "_judgment_trust",
    "_judgment_id",
    "_judgment_coordinate",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: honesty_enforcement.py — Ch13 HonestyEnforcer implementation
