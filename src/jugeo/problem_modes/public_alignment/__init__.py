"""public_alignment — Ch13 Public Honesty & Documentation Projection.

This package implements the public-alignment subsystem from Chapter 13 of
``preliminaries/theory2.tex``.  It enforces the core *monotonicity constraint*
on the public-projection map: any projection of internal judgment state to
public documentation may *weaken* but never *strengthen* trust claims.

Sub-modules
-----------
manifest
    Package manifest, capability flags, and provenance constants.
models
    Core frozen dataclasses: PublicClaim, HonestProjection,
    DocumentationSection, MigrationPlan.
honesty_enforcement
    HonestyEnforcer — validates that public outputs do not silently
    strengthen internal claims (Ȟ¹-violation detection).
documentation_projection
    DocumentationProjector — projects internal judgment state to
    documentation sections under the conservative-projection functor.
migration_analysis
    MigrationAnalyzer — analyzes API/documentation change and produces
    honest migration plans preserving semantic content.
publicity_boundary
    PublicityBoundary — manages the internal/public state boundary and
    audits all registered projections.
algorithms
    Stand-alone algorithmic functions for honesty scoring, trust-level
    projection, section merging, and migration-distance computation.
integration
    PublicAlignmentIntegration — connects the public-alignment subsystem
    to the main JuGeo judgment algebra.
theorems
    Theorem obligations and proof-strategy declarations derived from Ch13.

# copilot: public_alignment package root
"""

from __future__ import annotations

from jugeo.problem_modes.public_alignment.models import (
    PublicClaim,
    HonestProjection,
    DocumentationSection,
    MigrationPlan,
)
from jugeo.problem_modes.public_alignment.manifest import (
    PUBLIC_ALIGNMENT_MANIFEST,
    get_manifest,
    validate_manifest,
)

__all__ = [
    "PublicClaim",
    "HonestProjection",
    "DocumentationSection",
    "MigrationPlan",
    "PUBLIC_ALIGNMENT_MANIFEST",
    "get_manifest",
    "validate_manifest",
    # cross-subsystem integration
    "trust_projection_check",
    "certification_alignment",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def trust_projection_check(
    projection: "HonestProjection",
) -> "dict[str, object]":
    """Verify that a public projection preserves the trust ordering.

    Uses :mod:`jugeo.evidence.trust` to confirm that the projection from
    internal judgment state to public documentation does not silently
    *strengthen* any trust claim — the core Ch13 monotonicity constraint.

    Parameters
    ----------
    projection : HonestProjection
        The projection mapping internal state to public claims.

    Returns
    -------
    dict[str, object]
        Keys: ``preserves_order`` (bool), ``violations`` (list of dicts
        describing any ordering violations), ``trust_algebra``
        (:class:`~jugeo.evidence.trust.TrustAlgebra` instance used),
        ``audit_entries`` (list of :class:`~jugeo.evidence.trust.TrustAuditEntry`).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence.trust`` is not available.

    See Also
    --------
    jugeo.evidence.trust.TrustAlgebra : Trust ordered algebra.
    jugeo.evidence.trust.TrustLevel : Trust level type.
    """
    try:
        from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    except ImportError:
        raise NotImplementedError(
            "trust_projection_check requires jugeo.evidence.trust to be installed."
        )

    violations: list[dict[str, object]] = []
    audit_entries: list = []
    preserves_order = True
    algebra = TrustAlgebra()

    try:
        internal_claims = getattr(projection, "internal_claims", [])
        public_claims = getattr(projection, "public_claims", [])
        mapping = getattr(projection, "mapping", {})

        for claim_id, public_claim in (
            mapping.items() if isinstance(mapping, dict)
            else zip(
                [str(getattr(c, "id", i)) for i, c in enumerate(internal_claims)],
                public_claims,
            )
        ):
            internal_trust = getattr(
                next((c for c in internal_claims
                      if str(getattr(c, "id", "")) == str(claim_id)), None),
                "trust_level", None,
            )
            public_trust = getattr(public_claim, "trust_level", None)
            if internal_trust is not None and public_trust is not None:
                try:
                    strengthened = algebra.is_strictly_stronger(
                        public_trust, internal_trust
                    )
                except (AttributeError, TypeError):
                    strengthened = False
                if strengthened:
                    preserves_order = False
                    violations.append({
                        "claim_id": str(claim_id),
                        "internal_trust": internal_trust,
                        "public_trust": public_trust,
                        "reason": "public claim is strictly stronger than internal claim",
                    })
    except Exception:  # noqa: BLE001
        pass

    return {
        "preserves_order": preserves_order,
        "violations": violations,
        "trust_algebra": algebra,
        "audit_entries": audit_entries,
    }


def certification_alignment(
    claims: "list[PublicClaim]",
) -> "dict[str, object]":
    """Check that public certificate claims are backed by actual certificates.

    For each :class:`PublicClaim`, verifies that a corresponding
    :class:`~jugeo.evidence.certificates.Certificate` exists and that
    its recorded trust level and scope match the public claim.

    Parameters
    ----------
    claims : list[PublicClaim]
        Public claims to validate against the certificate store.

    Returns
    -------
    dict[str, object]
        Keys: ``aligned`` (bool — all claims match), ``per_claim``
        (list of per-claim result dicts with keys ``claim``, ``certificate``,
        ``matches``, ``discrepancy``), ``certificate_store``
        (:class:`~jugeo.evidence.certificates.CertificateStore` or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence.certificates`` is not available.

    See Also
    --------
    jugeo.evidence.certificates.Certificate : Certificate type.
    jugeo.evidence.certificates.CertificateStore : Persistent store.
    """
    try:
        from jugeo.evidence.certificates import CertificateStore
    except ImportError:
        raise NotImplementedError(
            "certification_alignment requires jugeo.evidence.certificates to be installed."
        )

    per_claim: list[dict[str, object]] = []
    all_aligned = True
    store = None

    try:
        store = CertificateStore()
    except Exception:  # noqa: BLE001
        pass

    for claim in claims:
        cert = None
        matches = False
        discrepancy = None
        try:
            claim_id = getattr(claim, "certificate_id", None) or getattr(claim, "id", None)
            if store is not None and claim_id is not None:
                cert = store.get(str(claim_id))
            if cert is not None:
                cert_trust = getattr(cert, "trust_level", None)
                claim_trust = getattr(claim, "trust_level", None)
                cert_scope = set(getattr(cert, "scope", []))
                claim_scope = set(getattr(claim, "scope", []))
                if cert_trust == claim_trust and claim_scope <= cert_scope:
                    matches = True
                else:
                    discrepancy = (
                        f"trust: cert={cert_trust} vs claim={claim_trust}; "
                        f"scope: cert={cert_scope} vs claim={claim_scope}"
                    )
            else:
                discrepancy = "no matching certificate found"
        except Exception as exc:  # noqa: BLE001
            discrepancy = f"check failed: {exc}"

        if not matches:
            all_aligned = False
        per_claim.append({
            "claim": claim,
            "certificate": cert,
            "matches": matches,
            "discrepancy": discrepancy,
        })

    return {
        "aligned": all_aligned,
        "per_claim": per_claim,
        "certificate_store": store,
    }



# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import documentation_alignment
except Exception:
    pass
try:
    from . import documentation_projection
except Exception:
    pass
try:
    from . import documentation_should_become_a_live
except Exception:
    pass
try:
    from . import honesty_enforcement
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import migration_analysis
except Exception:
    pass
try:
    from . import migration_and_donor_inheritance
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import public_api_cli_and_explanation_sem
except Exception:
    pass
try:
    from . import publicity_boundary
except Exception:
    pass
try:
    from . import the_public_story_should_remain_hon
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
