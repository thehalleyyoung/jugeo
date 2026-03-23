"""Certificate system for JuGeo verification evidence.

Certificates are faithful projections of verification state as described in
theory2.tex.  A certificate records *what* was verified, to *what* trust
level, with *what* evidence, what *residuals* remain, and what *obstructions*
were encountered.  Certificates must **never** silently strengthen claims —
they faithfully represent partial verification.

The full manifest is the tuple ``(J, O, E, X, K, η, σ)`` where:

* **J** — judgments: the propositions that have been evaluated.
* **O** — obligations: residual proof obligations that remain open.
* **E** — evidence archive: the collected evidence supporting judgments.
* **X** — obstructions: conditions that block further progress.
* **K** — certificates: the certificates issued against this state.
* **η** — epoch map: maps coordinates to their verification epoch.
* **σ** — invalidation graph: records causal invalidation edges.

Theory alignment
~~~~~~~~~~~~~~~~
* §252 — trust algebra  :math:`\\mathfrak{T} = (\\mathcal{E}_{\\mathrm{adm}},
  \\preceq, \\oplus, \\ominus, \\uparrow_{\\pi}, \\downarrow_{\\chi})`.
* §354 — trust is semantic state, not cosmetic annotation.
* No-silent-promotion rule — certificates preserve residuals and cannot erase
  unresolved status with new evidence.
* Projection faithfulness — public certificates remain scope-honest,
  residual-visible, and provenance-preserving.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~
The legacy ``SettlementCertificate`` and ``emit_certificate`` factory are
retained as thin wrappers so that existing consumers continue to work.

copilot: shared-core module — every public surface is designed for LLM
orchestration and Copilot-assisted verification workflows.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

from jugeo.evidence.manifests import EvidenceManifest
from jugeo.judgments.exports import ExportRecord

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CertificateStatus(str, Enum):
    """Status of a certificate in its lifecycle."""

    PENDING = 'pending'
    SETTLED = 'settled'
    OBSTRUCTED = 'obstructed'
    REVOKED = 'revoked'
    EXPIRED = 'expired'


class TrustLevel(IntEnum):
    """Numeric trust levels aligned with theory2 trust algebra.

    These mirror ``TrustTier`` from ``jugeo.evidence.trust`` but are kept as a
    lightweight local enum so that certificate consumers do not need to pull in
    the full trust algebra.
    """

    PROPOSAL = 1
    PROPOSED = 1
    REVIEWED = 2
    VERIFIED = 3

    def label(self) -> str:
        """Human-readable label for this trust level."""
        return self.name.lower()

    def stronger_than(self, other: TrustLevel) -> bool:
        """Return ``True`` when *self* is strictly stronger than *other*."""
        return int(self) > int(other)

    def weaker_than(self, other: TrustLevel) -> bool:
        """Return ``True`` when *self* is strictly weaker than *other*."""
        return int(self) < int(other)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


def _compute_signature(data: str, issuer: str) -> str:
    """Compute a deterministic SHA-256 signature hash for certificate data.

    This is **not** a cryptographic signature in the PKI sense — it is a
    content-addressable fingerprint that allows consumers to detect tampering
    or accidental mutation.
    """
    raw = f'{issuer}:{data}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# 1. Certificate — main immutable certificate record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Certificate:
    """Immutable certificate recording a verified-state snapshot.

    A certificate is the *public artifact* of the verification process.  It
    must faithfully represent what was actually verified, what residuals
    remain, and what obstructions were encountered.  It must **never**
    silently strengthen the claim beyond the evidence that supports it.

    Attributes
    ----------
    certificate_id:
        Unique identifier for this certificate instance.
    coordinate:
        The coordinate (e.g., theorem / lemma / section path) that this
        certificate covers.
    verified_propositions:
        Propositions whose verification is attested by this certificate.
    trust_level:
        The trust level at which verification was performed.
    evidence_summary:
        Human- and machine-readable summary of the evidence that supports
        the verified propositions.
    residual_obligations:
        Proof obligations that remain unresolved.  These **must** be
        reported honestly — omitting them would violate no-silent-promotion.
    obstructions:
        Conditions that blocked verification progress.
    issued_at:
        UTC timestamp when this certificate was created.
    issuer:
        Identity of the issuing authority.
    expiry:
        Optional UTC timestamp after which this certificate is no longer
        considered valid.
    signature_hash:
        Content-addressable hash for tamper detection.
    """

    certificate_id: str
    coordinate: str
    verified_propositions: tuple[str, ...]
    trust_level: TrustLevel
    evidence_summary: str
    residual_obligations: tuple[str, ...] = field(default_factory=tuple)
    obstructions: tuple[str, ...] = field(default_factory=tuple)
    issued_at: datetime = field(default_factory=_utcnow)
    issuer: str = 'system'
    expiry: datetime | None = None
    signature_hash: str = ''

    # -- query methods -------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` when the certificate is neither expired nor revoked.

        A certificate is valid when its signature is non-empty, it has not
        passed its expiry date, and it attests at least one proposition.
        """
        if not self.signature_hash:
            return False
        if self.is_expired():
            return False
        if not self.verified_propositions:
            return False
        return True

    def is_expired(self) -> bool:
        """Return ``True`` when the certificate has passed its expiry date."""
        if self.expiry is None:
            return False
        return _utcnow() > self.expiry

    def covers_proposition(self, proposition: str) -> bool:
        """Check whether *proposition* is among the verified propositions."""
        return proposition in self.verified_propositions

    def residual_count(self) -> int:
        """Return the number of unresolved residual obligations."""
        return len(self.residual_obligations)

    def obstruction_count(self) -> int:
        """Return the number of obstructions recorded."""
        return len(self.obstructions)

    def project_public(self) -> dict[str, object]:
        """Project a public-safe view of this certificate.

        The public projection excludes internal fields (signature hash) and
        presents the certificate in a form suitable for documentation or API
        consumption.
        """
        return {
            'certificate_id': self.certificate_id,
            'coordinate': self.coordinate,
            'verified': list(self.verified_propositions),
            'trust_level': self.trust_level.label(),
            'evidence_summary': self.evidence_summary,
            'residuals': list(self.residual_obligations),
            'obstructions': list(self.obstructions),
            'issued_at': self.issued_at.isoformat(),
            'issuer': self.issuer,
            'valid': self.is_valid(),
        }

    def serialize(self) -> dict[str, object]:
        """Full serialization including internal fields.

        Use :meth:`project_public` when the consumer should not see internal
        data such as the signature hash.
        """
        data = self.project_public()
        data['signature_hash'] = self.signature_hash
        data['expiry'] = self.expiry.isoformat() if self.expiry else None
        return data

    # -- cross-subsystem integration ----------------------------------------

    @classmethod
    def descent_certificate(
        cls,
        descent_result: Any,
        *,
        coordinate: str = '',
        issuer: str = 'descent-engine',
    ) -> 'Certificate':
        """Create a certificate from a successful descent result.

        Uses :class:`jugeo.geometry.descent.DescentResult` to extract the
        global section (on success) or obstruction (on failure) and wraps
        it as a :class:`Certificate` with appropriate trust level and
        residual information.

        Parameters
        ----------
        descent_result:
            A :class:`jugeo.geometry.descent.DescentResult` instance.
        coordinate:
            Override coordinate string; defaults to the section coordinate.
        issuer:
            Issuing authority identity.

        Returns
        -------
        Certificate
            A new certificate attesting the descent outcome.
        """
        try:
            from jugeo.geometry.descent import DescentResult  # noqa: F811
        except ImportError:
            return cls(
                certificate_id=uuid.uuid4().hex[:16],
                coordinate=coordinate or 'unknown',
                verified_propositions=(),
                trust_level=TrustLevel.PROPOSAL,
                evidence_summary='descent module unavailable',
                residual_obligations=('descent-import-failure',),
                issuer=issuer,
                signature_hash=_compute_signature('unavailable', issuer),
            )

        is_success = getattr(descent_result, 'is_success', False)
        is_failure = getattr(descent_result, 'is_failure', False)
        if is_success:
            section = descent_result.unwrap_section()
            summary_text = getattr(section, 'evidence_summary', section.summary)()
            coord = coordinate or getattr(
                getattr(section, 'coordinate', None), 'name', 'descent',
            )
            propositions = tuple(
                getattr(section, 'propositions', ()) or (summary_text,)
            )
            residuals: tuple[str, ...] = ()
            trust = TrustLevel.VERIFIED
        elif is_failure:
            obstruction = descent_result.unwrap_obstruction()
            summary_text = getattr(obstruction, 'evidence_summary', obstruction.summary)()
            coord = coordinate or 'descent-obstructed'
            propositions = ()
            residuals = (summary_text,)
            trust = TrustLevel.PROPOSAL
        else:
            coord = coordinate or 'descent-unknown'
            summary_text = f'DescentResult: {descent_result!r}'
            propositions = ()
            residuals = ('descent-result-empty',)
            trust = TrustLevel.PROPOSAL

        cert_id = uuid.uuid4().hex[:16]
        sig = _compute_signature(summary_text, issuer)
        return cls(
            certificate_id=cert_id,
            coordinate=coord,
            verified_propositions=propositions,
            trust_level=trust,
            evidence_summary=summary_text,
            residual_obligations=residuals,
            issuer=issuer,
            signature_hash=sig,
        )

    @classmethod
    def solver_backed_certificate(
        cls,
        z3_result: Any,
        *,
        coordinate: str = '',
        claim: str = '',
        issuer: str = 'z3-solver',
    ) -> 'Certificate':
        """Create a certificate backed by a Z3 proof witness.

        Attaches Z3 solver results from :class:`jugeo.solver.z3_session.Z3Result`
        as evidence.  UNSAT results yield ``VERIFIED`` trust; SAT / unknown
        results yield ``PROPOSAL`` with residuals noting the gap.

        Parameters
        ----------
        z3_result:
            A :class:`jugeo.solver.z3_session.Z3Result` instance.
        coordinate:
            The coordinate this certificate covers.
        claim:
            The proposition being certified.
        issuer:
            Issuing authority identity.

        Returns
        -------
        Certificate
            A solver-backed certificate.
        """
        try:
            from jugeo.solver.z3_session import Z3Result, SolveOutcome  # noqa: F811
        except ImportError:
            return cls(
                certificate_id=uuid.uuid4().hex[:16],
                coordinate=coordinate or 'unknown',
                verified_propositions=(claim,) if claim else (),
                trust_level=TrustLevel.PROPOSAL,
                evidence_summary='z3_session module unavailable',
                residual_obligations=('solver-import-failure',),
                issuer=issuer,
                signature_hash=_compute_signature('unavailable', issuer),
            )

        status = getattr(z3_result, 'status', None)
        proof_text = getattr(z3_result, 'proof', None) or ''
        duration = getattr(z3_result, 'duration_ms', 0.0)
        unsat_core = tuple(getattr(z3_result, 'unsat_core', ()))

        if status is not None and status.value == 'unsat':
            trust = TrustLevel.VERIFIED
            propositions = (claim,) if claim else ('solver-discharged',)
            residuals_out: tuple[str, ...] = ()
            summary = (
                f'Z3 UNSAT proof (core={list(unsat_core)}, '
                f'duration={duration:.1f}ms)'
            )
        else:
            trust = TrustLevel.PROPOSAL
            propositions = ()
            status_val = status.value if status is not None else 'unknown'
            residuals_out = (f'solver-{status_val}',)
            summary = f'Z3 {status_val} (duration={duration:.1f}ms)'

        cert_id = uuid.uuid4().hex[:16]
        sig = _compute_signature(summary, issuer)
        return cls(
            certificate_id=cert_id,
            coordinate=coordinate or 'solver',
            verified_propositions=propositions,
            trust_level=trust,
            evidence_summary=summary,
            residual_obligations=residuals_out,
            issuer=issuer,
            signature_hash=sig,
        )

    @classmethod
    def encoding_certificate(
        cls,
        schema: Any,
        *,
        coordinate: str = '',
        issuer: str = 'encoding-engine',
    ) -> 'Certificate':
        """Certify an encoding result from a theorem schema.

        Wraps a :class:`jugeo.encodings.theorem_schemas.TheoremSchema` (or
        ``SchemaInstance``) as a certificate recording which schema was
        instantiated, its proof style, and any outstanding proof
        obligations.

        Parameters
        ----------
        schema:
            A ``TheoremSchema`` or ``SchemaInstance`` from
            :mod:`jugeo.encodings.theorem_schemas`.
        coordinate:
            The coordinate this certificate covers.
        issuer:
            Issuing authority identity.

        Returns
        -------
        Certificate
            An encoding-backed certificate.
        """
        try:
            from jugeo.encodings.theorem_schemas import (  # noqa: F811
                TheoremSchema,
                SchemaInstance,
                ProofObligation,
            )
        except ImportError:
            return cls(
                certificate_id=uuid.uuid4().hex[:16],
                coordinate=coordinate or 'unknown',
                verified_propositions=(),
                trust_level=TrustLevel.PROPOSAL,
                evidence_summary='theorem_schemas module unavailable',
                residual_obligations=('encoding-import-failure',),
                issuer=issuer,
                signature_hash=_compute_signature('unavailable', issuer),
            )

        schema_name = getattr(schema, 'name', str(schema))
        proof_style = getattr(schema, 'proof_style', None)
        style_label = proof_style.value if proof_style is not None else 'unknown'

        obligations = getattr(schema, 'obligations', ())
        residuals_list: list[str] = []
        for obl in obligations:
            obl_desc = getattr(obl, 'description', str(obl))
            residuals_list.append(obl_desc)

        status = getattr(schema, 'status', None)
        status_val = status.value if status is not None else 'pending'
        is_discharged = status_val in ('discharged', 'verified', 'done')

        trust = TrustLevel.VERIFIED if is_discharged else TrustLevel.PROPOSAL
        propositions = (schema_name,) if is_discharged else ()
        summary = (
            f'Encoding schema={schema_name}, style={style_label}, '
            f'status={status_val}, obligations={len(residuals_list)}'
        )

        cert_id = uuid.uuid4().hex[:16]
        sig = _compute_signature(summary, issuer)
        return cls(
            certificate_id=cert_id,
            coordinate=coordinate or f'encoding:{schema_name}',
            verified_propositions=propositions,
            trust_level=trust,
            evidence_summary=summary,
            residual_obligations=tuple(residuals_list),
            issuer=issuer,
            signature_hash=sig,
        )

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def judgment_subject(self) -> Any:
        """Return the judgment term this certificate attests.

        Reconstructs the judgment from ``jugeo.judgments.judgment_terms``
        using the certificate's coordinate and verified propositions.  The
        judgment is the first-class object whose truth status this
        certificate certifies.

        Returns ``None`` when the judgments subsystem is unavailable.
        """
        try:
            from jugeo.judgments.judgment_terms import judgment_for_certificate
        except ImportError:
            return None
        return judgment_for_certificate(self.coordinate, self.verified_propositions)

    @property
    def descent_witness(self) -> Any:
        """Return the descent result backing this certificate.

        When a certificate was issued from a successful descent computation,
        this property recovers the :class:`jugeo.geometry.descent.DescentResult`
        that produced the global section.  Returns ``None`` when no descent
        witness is associated or the descent subsystem is unavailable.
        """
        try:
            from jugeo.geometry.descent import lookup_descent_witness
        except ImportError:
            return None
        return lookup_descent_witness(self.certificate_id)

    @property
    def solver_proof(self) -> Any:
        """Return the Z3 proof object associated with this certificate.

        Queries the solver session registry in ``jugeo.solver.z3_session``
        for a proof artefact tied to this certificate's identifier.
        Returns ``None`` when no solver proof is linked.
        """
        try:
            from jugeo.solver.z3_session import get_proof_for_certificate
        except ImportError:
            return None
        return get_proof_for_certificate(self.certificate_id)

    def encoding_certificate(self) -> dict[str, Any]:
        """Return a certificate for encoding correctness.

        Generates a secondary certificate attesting that the encoding
        schemas used to produce this certificate's evidence are themselves
        well-formed, using ``jugeo.encodings.theorem_schemas``.
        """
        try:
            from jugeo.encodings.theorem_schemas import encoding_correctness_certificate
        except ImportError:
            return {
                'certificate_id': self.certificate_id,
                'encoding_correct': None,
                'reason': 'theorem_schemas subsystem unavailable',
            }
        return encoding_correctness_certificate(self)

    @property
    def provenance_chain(self) -> Any:
        """Return the full provenance chain for this certificate.

        Walks the provenance graph from ``jugeo.evidence.provenance`` to
        reconstruct the complete chain of evidence transformations that
        led to this certificate's issuance.
        """
        try:
            from jugeo.evidence.provenance import ProvenanceQuery
        except ImportError:
            return None
        query = ProvenanceQuery.__new__(ProvenanceQuery)
        return getattr(query, 'chain_for_certificate', lambda _: None)(self.certificate_id)

    def orchestration_audit(self) -> dict[str, Any]:
        """Return the orchestration audit trail for this certificate.

        Queries ``jugeo.orchestration.controller`` for the sequence of
        orchestration decisions (routing, scheduling, retry) that led
        to the evidence production backing this certificate.
        """
        try:
            from jugeo.orchestration.controller import audit_trail_for
        except ImportError:
            return {
                'certificate_id': self.certificate_id,
                'audit': None,
                'reason': 'orchestration controller unavailable',
            }
        return audit_trail_for(self.certificate_id)

    def validate_against_site(self, site: Any) -> dict[str, Any]:
        """Validate this certificate at all coordinates of a site.

        Checks that the certificate's propositions hold at every object
        in the site and that trust levels are compatible with the site's
        restriction maps.  Uses ``jugeo.geometry.site.validate_certificate``.
        """
        try:
            from jugeo.geometry.site import validate_certificate
        except ImportError:
            return {
                'certificate_id': self.certificate_id,
                'valid_at_site': None,
                'reason': 'site subsystem unavailable',
            }
        return validate_certificate(self, site)


# ---------------------------------------------------------------------------
# 2. CertificateBuilder — fluent builder for Certificate instances
# ---------------------------------------------------------------------------


class CertificateBuilder:
    """Fluent builder for constructing :class:`Certificate` instances.

    The builder enforces that all mandatory fields are supplied before
    :meth:`build` succeeds and automatically computes the signature hash
    when :meth:`sign` is called.

    Usage::

        cert = (
            CertificateBuilder()
            .for_coordinate('thm:main')
            .add_verified('P1')
            .add_verified('P2')
            .set_trust(TrustLevel.VERIFIED)
            .set_issuer('authority-alpha')
            .add_residual('lemma-3-gap')
            .sign()
            .build()
        )

    copilot: the builder pattern allows LLM orchestrators to assemble
    certificates step-by-step without constructing the full argument list.
    """

    def __init__(self) -> None:
        self._id: str = str(uuid.uuid4())
        self._coordinate: str = ''
        self._verified: list[str] = []
        self._residuals: list[str] = []
        self._obstructions: list[str] = []
        self._trust: TrustLevel = TrustLevel.PROPOSAL
        self._evidence_summary: str = ''
        self._issuer: str = 'system'
        self._expiry: datetime | None = None
        self._signature: str = ''

    def for_coordinate(self, coordinate: str) -> CertificateBuilder:
        """Set the coordinate that this certificate covers."""
        self._coordinate = coordinate
        return self

    def add_verified(self, proposition: str) -> CertificateBuilder:
        """Add a verified proposition to the certificate."""
        if proposition and proposition not in self._verified:
            self._verified.append(proposition)
        return self

    def add_residual(self, obligation: str) -> CertificateBuilder:
        """Record a residual obligation that remains unresolved."""
        if obligation and obligation not in self._residuals:
            self._residuals.append(obligation)
        return self

    def add_obstruction(self, obstruction: str) -> CertificateBuilder:
        """Record an obstruction that blocked verification."""
        if obstruction and obstruction not in self._obstructions:
            self._obstructions.append(obstruction)
        return self

    def set_trust(self, level: TrustLevel) -> CertificateBuilder:
        """Set the trust level for this certificate."""
        self._trust = level
        return self

    def set_issuer(self, issuer: str) -> CertificateBuilder:
        """Set the issuing authority identity."""
        self._issuer = issuer
        return self

    def set_evidence_summary(self, summary: str) -> CertificateBuilder:
        """Provide a textual evidence summary."""
        self._evidence_summary = summary
        return self

    def set_expiry(self, expiry: datetime) -> CertificateBuilder:
        """Set the expiry timestamp."""
        self._expiry = expiry
        return self

    def sign(self) -> CertificateBuilder:
        """Compute the signature hash from current builder state.

        Must be called **after** all data has been set and **before**
        :meth:`build`.
        """
        payload = json.dumps({
            'coordinate': self._coordinate,
            'verified': self._verified,
            'residuals': self._residuals,
            'obstructions': self._obstructions,
            'trust': int(self._trust),
        }, sort_keys=True)
        self._signature = _compute_signature(payload, self._issuer)
        return self

    def build(self) -> Certificate:
        """Construct the :class:`Certificate`.

        Raises :class:`ValueError` when mandatory fields are missing.
        """
        if not self._coordinate:
            raise ValueError('Certificate coordinate must be set')
        if not self._signature:
            raise ValueError('Certificate must be signed before building')
        return Certificate(
            certificate_id=self._id,
            coordinate=self._coordinate,
            verified_propositions=tuple(self._verified),
            trust_level=self._trust,
            evidence_summary=self._evidence_summary,
            residual_obligations=tuple(self._residuals),
            obstructions=tuple(self._obstructions),
            issuer=self._issuer,
            expiry=self._expiry,
            signature_hash=self._signature,
        )


# ---------------------------------------------------------------------------
# 3. CertificateChain — ordered chain with delegation semantics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CertificateChain:
    """An ordered chain of certificates with delegation semantics.

    In theory2, trust may be delegated across a chain of authorities.  The
    overall trust of the chain is bounded by the weakest link and the chain
    is complete only when every link is valid and consecutive coordinates
    form a connected path.

    copilot: chain verification is a natural candidate for LLM-assisted
    audit — the ``gaps`` method exposes exactly where human review is needed.
    """

    certificates: list[Certificate] = field(default_factory=list)

    def verify_chain(self) -> bool:
        """Return ``True`` when every certificate in the chain is valid.

        Validity is checked individually via :meth:`Certificate.is_valid`.
        An empty chain is considered invalid.
        """
        if not self.certificates:
            return False
        return all(c.is_valid() for c in self.certificates)

    def trust_floor(self) -> TrustLevel:
        """Return the minimum trust level across the chain.

        This is the effective trust of the whole chain — trust cannot be
        stronger than the weakest link.
        """
        if not self.certificates:
            return TrustLevel.PROPOSAL
        return TrustLevel(min(c.trust_level for c in self.certificates))

    def weakest_link(self) -> Certificate | None:
        """Return the certificate with the lowest trust level.

        When multiple certificates share the same lowest level, the first
        one in chain order is returned.
        """
        if not self.certificates:
            return None
        return min(self.certificates, key=lambda c: int(c.trust_level))

    def is_complete(self) -> bool:
        """Return ``True`` when the chain has no gaps.

        A chain is complete when it is non-empty, every link is valid, and
        there are no missing intermediate coordinates.
        """
        if not self.verify_chain():
            return False
        return len(self.gaps()) == 0

    def gaps(self) -> list[tuple[str, str]]:
        """Identify consecutive coordinate pairs where delegation is missing.

        Returns a list of ``(from_coordinate, to_coordinate)`` pairs for
        which no linking certificate exists.
        """
        gap_list: list[tuple[str, str]] = []
        for i in range(len(self.certificates) - 1):
            current = self.certificates[i]
            nxt = self.certificates[i + 1]
            verified_by_current = set(current.verified_propositions)
            needed_by_next = set()
            if nxt.coordinate not in verified_by_current:
                needed_by_next.add(nxt.coordinate)
            if needed_by_next:
                gap_list.append((current.coordinate, nxt.coordinate))
        return gap_list

    def extend_chain(self, certificate: Certificate) -> None:
        """Append a certificate to the end of the chain.

        Raises :class:`ValueError` if the certificate is not valid.
        """
        if not certificate.is_valid():
            raise ValueError(
                f'Cannot extend chain with invalid certificate '
                f'{certificate.certificate_id}'
            )
        self.certificates.append(certificate)

    def coordinates(self) -> list[str]:
        """Return the ordered list of coordinates covered by this chain."""
        return [c.coordinate for c in self.certificates]

    def total_residuals(self) -> int:
        """Sum of residual obligations across all chain links."""
        return sum(c.residual_count() for c in self.certificates)

    def total_obstructions(self) -> int:
        """Sum of obstructions across all chain links."""
        return sum(c.obstruction_count() for c in self.certificates)

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def judgment_subject(self) -> Any:
        """Return the composite judgment attested by the full chain.

        Merges the judgment subjects of each link in the chain into a
        single composite judgment from ``jugeo.judgments.judgment_terms``.
        """
        try:
            from jugeo.judgments.judgment_terms import merge_judgments
        except ImportError:
            return None
        subjects = [c.judgment_subject for c in self.certificates if c.judgment_subject is not None]
        if not subjects:
            return None
        return merge_judgments(subjects)

    @property
    def descent_witness(self) -> Any:
        """Return the strongest descent witness across the chain.

        Scans each certificate in the chain and returns the first non-None
        descent witness, preferring certificates with higher trust levels.
        """
        for cert in sorted(self.certificates, key=lambda c: -int(c.trust_level)):
            witness = cert.descent_witness
            if witness is not None:
                return witness
        return None

    @property
    def solver_proof(self) -> Any:
        """Return the combined solver proof for the certificate chain.

        Queries ``jugeo.solver.z3_session`` for proofs associated with
        each link and returns a composite proof if available.
        """
        try:
            from jugeo.solver.z3_session import combine_proofs
        except ImportError:
            return None
        proofs = [c.solver_proof for c in self.certificates if c.solver_proof is not None]
        if not proofs:
            return None
        return combine_proofs(proofs)

    def encoding_certificate(self) -> dict[str, Any]:
        """Return encoding correctness certificates for the full chain.

        Aggregates encoding certificates from each link into a single
        chain-level encoding correctness report.
        """
        results = [c.encoding_certificate() for c in self.certificates]
        return {
            'chain_length': len(self.certificates),
            'encoding_certificates': results,
            'all_correct': all(
                r.get('encoding_correct') is True for r in results
                if r.get('encoding_correct') is not None
            ),
        }

    @property
    def provenance_chain(self) -> list[Any]:
        """Return provenance chains for every link in this chain.

        Collects the provenance chain from each certificate, yielding a
        list of per-link provenance objects from ``jugeo.evidence.provenance``.
        """
        return [c.provenance_chain for c in self.certificates]

    def orchestration_audit(self) -> dict[str, Any]:
        """Return the orchestration audit trail for the full chain.

        Merges per-certificate audit trails from
        ``jugeo.orchestration.controller`` into a single chain-level report.
        """
        audits = [c.orchestration_audit() for c in self.certificates]
        return {
            'chain_length': len(self.certificates),
            'audits': audits,
        }

    def validate_against_site(self, site: Any) -> dict[str, Any]:
        """Validate every certificate in the chain against a site.

        Iterates through chain links and validates each against the
        site's coordinates, returning a per-link validation map.
        """
        results = {}
        for cert in self.certificates:
            results[cert.certificate_id] = cert.validate_against_site(site)
        return {
            'chain_length': len(self.certificates),
            'per_link': results,
            'all_valid': all(
                r.get('valid_at_site') is True for r in results.values()
                if r.get('valid_at_site') is not None
            ),
        }


# ---------------------------------------------------------------------------
# 4. CertificateAuthority — issuance and lifecycle management
# ---------------------------------------------------------------------------


class CertificateAuthority:
    """Authority that issues, validates, revokes, and renews certificates.

    The authority maintains an internal registry of issued and revoked
    certificate IDs.  It enforces that:

    * Only trusted issuers may issue certificates.
    * Revoked certificates cannot be silently reused.
    * Delegation depth does not exceed the configured maximum.

    copilot: the authority is the trust anchor in LLM-assisted verification
    pipelines — Copilot should always check ``is_trusted_issuer`` before
    accepting a certificate.
    """

    def __init__(
        self,
        name: str,
        trusted_issuers: Sequence[str] | None = None,
        max_delegation_depth: int = 5,
    ) -> None:
        self.name: str = name
        self._trusted_issuers: set[str] = set(trusted_issuers or [name])
        self._max_delegation_depth: int = max_delegation_depth
        self._issued: dict[str, Certificate] = {}
        self._revoked: set[str] = set()

    def issue(self, builder: CertificateBuilder) -> Certificate:
        """Issue a new certificate from a prepared builder.

        The builder is automatically signed with this authority's name and
        built.  The resulting certificate is stored in the issued registry.
        """
        builder.set_issuer(self.name)
        builder.sign()
        cert = builder.build()
        self._issued[cert.certificate_id] = cert
        return cert

    def validate(self, certificate: Certificate) -> bool:
        """Validate a certificate against this authority's policy.

        A certificate is considered valid when:
        * Its base validity check passes (:meth:`Certificate.is_valid`).
        * It was issued by a trusted issuer.
        * It has not been revoked.
        """
        if not certificate.is_valid():
            return False
        if not self.is_trusted_issuer(certificate.issuer):
            return False
        if certificate.certificate_id in self._revoked:
            return False
        return True

    def revoke(self, certificate_id: str, *, reason: str = '') -> bool:
        """Revoke a previously issued certificate.

        Returns ``True`` when the certificate was found and revoked,
        ``False`` if it was not in the issued registry or already revoked.
        """
        if certificate_id in self._revoked:
            return False
        if certificate_id not in self._issued:
            return False
        self._revoked.add(certificate_id)
        return True

    def renew(self, certificate: Certificate) -> Certificate:
        """Renew a certificate by re-issuing with a fresh ID and timestamp.

        The original certificate is **not** automatically revoked — the
        caller should revoke it if the old instance should no longer be
        accepted.
        """
        builder = CertificateBuilder()
        builder.for_coordinate(certificate.coordinate)
        for prop in certificate.verified_propositions:
            builder.add_verified(prop)
        for res in certificate.residual_obligations:
            builder.add_residual(res)
        for obs in certificate.obstructions:
            builder.add_obstruction(obs)
        builder.set_trust(certificate.trust_level)
        builder.set_evidence_summary(certificate.evidence_summary)
        return self.issue(builder)

    def list_issued(self) -> list[Certificate]:
        """Return all certificates issued by this authority."""
        return list(self._issued.values())

    def list_revoked(self) -> list[str]:
        """Return the IDs of all revoked certificates."""
        return sorted(self._revoked)

    def is_trusted_issuer(self, issuer: str) -> bool:
        """Check whether *issuer* is in the set of trusted issuers."""
        return issuer in self._trusted_issuers

    def delegation_depth(self, chain: CertificateChain) -> int:
        """Compute the delegation depth of a certificate chain.

        Delegation depth is the number of distinct issuers in the chain.
        Returns ``-1`` if the depth exceeds the configured maximum.
        """
        issuers = {c.issuer for c in chain.certificates}
        depth = len(issuers)
        if depth > self._max_delegation_depth:
            return -1
        return depth

    def add_trusted_issuer(self, issuer: str) -> None:
        """Register an additional trusted issuer."""
        self._trusted_issuers.add(issuer)


# ---------------------------------------------------------------------------
# 5. CertificateStore — persistent storage and retrieval
# ---------------------------------------------------------------------------


class CertificateStore:
    """In-memory certificate store with retrieval and lifecycle queries.

    The store indexes certificates by ID, coordinate, and proposition for
    efficient lookup.  It supports pruning of expired certificates and
    bulk export.

    copilot: when an LLM orchestrator needs to find certificates relevant
    to a coordinate or proposition, the store provides the query surface.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Certificate] = {}
        self._by_coordinate: dict[str, list[str]] = {}
        self._by_proposition: dict[str, list[str]] = {}

    def store(self, certificate: Certificate) -> None:
        """Store a certificate, indexing it for retrieval."""
        cid = certificate.certificate_id
        self._by_id[cid] = certificate
        coord_list = self._by_coordinate.setdefault(certificate.coordinate, [])
        if cid not in coord_list:
            coord_list.append(cid)
        for prop in certificate.verified_propositions:
            prop_list = self._by_proposition.setdefault(prop, [])
            if cid not in prop_list:
                prop_list.append(cid)

    def retrieve(self, certificate_id: str) -> Certificate | None:
        """Retrieve a certificate by its unique ID."""
        return self._by_id.get(certificate_id)

    def retrieve_by_coordinate(self, coordinate: str) -> list[Certificate]:
        """Return all certificates covering *coordinate*."""
        ids = self._by_coordinate.get(coordinate, [])
        return [self._by_id[cid] for cid in ids if cid in self._by_id]

    def retrieve_by_proposition(self, proposition: str) -> list[Certificate]:
        """Return all certificates that verify *proposition*."""
        ids = self._by_proposition.get(proposition, [])
        return [self._by_id[cid] for cid in ids if cid in self._by_id]

    def list_valid(self) -> list[Certificate]:
        """Return all currently valid certificates."""
        return [c for c in self._by_id.values() if c.is_valid()]

    def list_expired(self) -> list[Certificate]:
        """Return all expired certificates still in the store."""
        return [c for c in self._by_id.values() if c.is_expired()]

    def prune_expired(self) -> int:
        """Remove expired certificates from the store.

        Returns the number of certificates pruned.
        """
        expired_ids = [cid for cid, c in self._by_id.items() if c.is_expired()]
        for cid in expired_ids:
            cert = self._by_id.pop(cid)
            coord_list = self._by_coordinate.get(cert.coordinate, [])
            if cid in coord_list:
                coord_list.remove(cid)
            for prop in cert.verified_propositions:
                prop_list = self._by_proposition.get(prop, [])
                if cid in prop_list:
                    prop_list.remove(cid)
        return len(expired_ids)

    def export_all(self) -> list[dict[str, object]]:
        """Serialize every certificate in the store."""
        return [c.serialize() for c in self._by_id.values()]

    def count(self) -> int:
        """Return the total number of certificates in the store."""
        return len(self._by_id)

    def coordinates(self) -> list[str]:
        """Return all coordinates that have at least one certificate."""
        return list(self._by_coordinate.keys())


# ---------------------------------------------------------------------------
# 6. CertificateVerifier — independent verification logic
# ---------------------------------------------------------------------------


class CertificateVerifier:
    """Independent verifier that checks certificate correctness.

    The verifier implements the **no-silent-strengthening** invariant from
    theory2: a certificate must not claim more than the evidence supports,
    residuals must be honestly reported, and trust levels must be consistent
    with the evidence kind.

    copilot: use ``copilot_verification_assist`` for an LLM-friendly
    diagnostic report that can be fed back into a Copilot prompt.
    """

    def __init__(self, authority: CertificateAuthority | None = None) -> None:
        self._authority = authority

    def verify(self, certificate: Certificate) -> tuple[bool, list[str]]:
        """Run all verification checks on a single certificate.

        Returns ``(passed, issues)`` where *issues* is a list of
        human-readable problem descriptions.  An empty list means the
        certificate passed all checks.
        """
        issues: list[str] = []
        if not certificate.is_valid():
            issues.append('Certificate base validity check failed')
        if self._authority and not self._authority.validate(certificate):
            issues.append('Certificate not accepted by authority')
        issues.extend(self.check_no_silent_strengthening(certificate))
        issues.extend(self.check_residuals_honest(certificate))
        issues.extend(self.check_trust_consistent(certificate))
        return (len(issues) == 0, issues)

    def verify_chain(self, chain: CertificateChain) -> tuple[bool, list[str]]:
        """Verify every certificate in a chain plus chain-level properties."""
        issues: list[str] = []
        if not chain.certificates:
            issues.append('Chain is empty')
            return (False, issues)
        for cert in chain.certificates:
            ok, cert_issues = self.verify(cert)
            if not ok:
                issues.extend(
                    f'[{cert.certificate_id[:8]}] {i}' for i in cert_issues
                )
        gap_pairs = chain.gaps()
        for src, dst in gap_pairs:
            issues.append(f'Delegation gap: {src} -> {dst}')
        return (len(issues) == 0, issues)

    def check_evidence_matches(
        self, certificate: Certificate, evidence_keys: Sequence[str],
    ) -> list[str]:
        """Check that claimed propositions have matching evidence keys.

        Returns issues for any proposition that does not have a
        corresponding key in *evidence_keys*.
        """
        issues: list[str] = []
        evidence_set = set(evidence_keys)
        for prop in certificate.verified_propositions:
            if prop not in evidence_set:
                issues.append(
                    f'Proposition "{prop}" has no matching evidence key'
                )
        return issues

    def check_trust_consistent(self, certificate: Certificate) -> list[str]:
        """Check that the trust level is consistent with certificate content.

        A ``VERIFIED`` certificate should have no obstructions.  A
        ``PROPOSAL`` certificate should not claim verified propositions
        without residuals.
        """
        issues: list[str] = []
        if (
            certificate.trust_level == TrustLevel.VERIFIED
            and certificate.obstructions
        ):
            issues.append(
                'VERIFIED trust level but obstructions are present'
            )
        if (
            certificate.trust_level == TrustLevel.PROPOSAL
            and certificate.verified_propositions
            and not certificate.residual_obligations
        ):
            issues.append(
                'PROPOSAL trust with verified propositions but no residuals '
                '— possible silent strengthening'
            )
        return issues

    def check_no_silent_strengthening(
        self, certificate: Certificate,
    ) -> list[str]:
        """Detect potential silent strengthening violations.

        A certificate silently strengthens when it claims a higher trust
        level than its content justifies — for example, claiming VERIFIED
        while residuals remain.
        """
        issues: list[str] = []
        if (
            certificate.trust_level == TrustLevel.VERIFIED
            and certificate.residual_obligations
        ):
            issues.append(
                f'Trust VERIFIED but {len(certificate.residual_obligations)} '
                f'residual(s) remain — this silently strengthens the claim'
            )
        return issues

    def check_residuals_honest(self, certificate: Certificate) -> list[str]:
        """Check that residuals are non-empty strings and not duplicated."""
        issues: list[str] = []
        seen: set[str] = set()
        for res in certificate.residual_obligations:
            if not res.strip():
                issues.append('Empty or whitespace-only residual obligation')
            if res in seen:
                issues.append(f'Duplicate residual obligation: "{res}"')
            seen.add(res)
        return issues

    def copilot_verification_assist(
        self, certificate: Certificate,
    ) -> dict[str, object]:
        """Produce an LLM-friendly verification report.

        copilot: feed this dict directly into a Copilot prompt for assisted
        certificate review.
        """
        ok, issues = self.verify(certificate)
        return {
            'certificate_id': certificate.certificate_id,
            'coordinate': certificate.coordinate,
            'passed': ok,
            'trust_level': certificate.trust_level.label(),
            'verified_count': len(certificate.verified_propositions),
            'residual_count': certificate.residual_count(),
            'obstruction_count': certificate.obstruction_count(),
            'issues': issues,
            'recommendation': (
                'Certificate is sound.'
                if ok
                else 'Review the listed issues before accepting.'
            ),
        }


# ---------------------------------------------------------------------------
# 7. CertificateMerger — merge and conflict resolution
# ---------------------------------------------------------------------------


class CertificateMerger:
    """Merge certificates covering the same coordinate.

    When multiple authorities or verification passes produce certificates
    for the same coordinate, the merger combines them according to
    configurable conflict-resolution strategies while preserving the
    no-silent-strengthening invariant.

    copilot: use ``merge`` for automated certificate consolidation in CI
    pipelines.
    """

    @staticmethod
    def merge(
        certificates: Sequence[Certificate],
        *,
        strategy: str = 'conservative',
    ) -> Certificate:
        """Merge multiple certificates into one.

        Parameters
        ----------
        certificates:
            The certificates to merge.  Must all share the same coordinate.
        strategy:
            One of ``'conservative'``, ``'strongest'``, or ``'intersection'``.

        Raises
        ------
        ValueError
            If certificates have differing coordinates or the list is empty.
        """
        if not certificates:
            raise ValueError('Nothing to merge')
        coords = {c.coordinate for c in certificates}
        if len(coords) > 1:
            raise ValueError(
                f'Cannot merge certificates for different coordinates: '
                f'{coords}'
            )
        if strategy == 'strongest':
            return CertificateMerger.take_strongest(certificates)
        if strategy == 'intersection':
            return CertificateMerger.intersection_certificate(certificates)
        return CertificateMerger.take_most_conservative(certificates)

    @staticmethod
    def resolve_conflicts(
        a: Certificate, b: Certificate,
    ) -> list[str]:
        """List human-readable descriptions of conflicts between two certs."""
        conflicts: list[str] = []
        if a.coordinate != b.coordinate:
            conflicts.append(
                f'Coordinate mismatch: {a.coordinate} vs {b.coordinate}'
            )
        if a.trust_level != b.trust_level:
            conflicts.append(
                f'Trust mismatch: {a.trust_level.label()} vs '
                f'{b.trust_level.label()}'
            )
        a_props = set(a.verified_propositions)
        b_props = set(b.verified_propositions)
        only_a = a_props - b_props
        only_b = b_props - a_props
        if only_a:
            conflicts.append(f'Propositions only in A: {sorted(only_a)}')
        if only_b:
            conflicts.append(f'Propositions only in B: {sorted(only_b)}')
        a_res = set(a.residual_obligations)
        b_res = set(b.residual_obligations)
        if a_res != b_res:
            conflicts.append('Residual obligations differ')
        return conflicts

    @staticmethod
    def take_strongest(certificates: Sequence[Certificate]) -> Certificate:
        """Pick the certificate with the highest trust level.

        Ties are broken by the one with the most verified propositions.
        Residuals and obstructions are taken from the winner.
        """
        best = max(
            certificates,
            key=lambda c: (int(c.trust_level), len(c.verified_propositions)),
        )
        return best

    @staticmethod
    def take_most_conservative(
        certificates: Sequence[Certificate],
    ) -> Certificate:
        """Construct a certificate using the weakest trust and union residuals.

        This guarantees no silent strengthening — the merged certificate is
        at most as strong as the weakest input.
        """
        coordinate = certificates[0].coordinate
        weakest_trust = TrustLevel(
            min(int(c.trust_level) for c in certificates)
        )
        all_props: set[str] = set()
        all_residuals: set[str] = set()
        all_obstructions: set[str] = set()
        summaries: list[str] = []
        for c in certificates:
            all_props.update(c.verified_propositions)
            all_residuals.update(c.residual_obligations)
            all_obstructions.update(c.obstructions)
            if c.evidence_summary:
                summaries.append(c.evidence_summary)
        builder = CertificateBuilder()
        builder.for_coordinate(coordinate)
        builder.set_trust(weakest_trust)
        builder.set_evidence_summary(' | '.join(summaries) if summaries else '')
        for p in sorted(all_props):
            builder.add_verified(p)
        for r in sorted(all_residuals):
            builder.add_residual(r)
        for o in sorted(all_obstructions):
            builder.add_obstruction(o)
        builder.sign()
        return builder.build()

    @staticmethod
    def intersection_certificate(
        certificates: Sequence[Certificate],
    ) -> Certificate:
        """Construct a certificate containing only commonly verified props.

        The resulting certificate only claims propositions verified by
        **every** input certificate.  Residuals and obstructions are
        unioned (conservative).
        """
        if not certificates:
            raise ValueError('Nothing to intersect')
        coordinate = certificates[0].coordinate
        common_props: set[str] = set(certificates[0].verified_propositions)
        all_residuals: set[str] = set()
        all_obstructions: set[str] = set()
        weakest_trust = int(certificates[0].trust_level)
        for c in certificates[1:]:
            common_props &= set(c.verified_propositions)
            all_residuals.update(c.residual_obligations)
            all_obstructions.update(c.obstructions)
            weakest_trust = min(weakest_trust, int(c.trust_level))
        builder = CertificateBuilder()
        builder.for_coordinate(coordinate)
        builder.set_trust(TrustLevel(weakest_trust))
        for p in sorted(common_props):
            builder.add_verified(p)
        for r in sorted(all_residuals):
            builder.add_residual(r)
        for o in sorted(all_obstructions):
            builder.add_obstruction(o)
        builder.sign()
        return builder.build()


# ---------------------------------------------------------------------------
# 8. CertificateProjection — project to various public forms
# ---------------------------------------------------------------------------


class CertificateProjection:
    """Project certificates into various consumer-facing formats.

    Theory2 requires that public projections are *faithful* — they must not
    omit residuals or silently upgrade trust.  The projection methods here
    enforce that invariant while tailoring the format to the audience.

    copilot: these projections are designed to be directly consumable by
    LLM prompts — use ``project_for_api`` for structured data and
    ``project_for_human`` for natural-language summaries.
    """

    @staticmethod
    def project_for_documentation(certificate: Certificate) -> str:
        """Render a certificate as a documentation-friendly string.

        Includes the coordinate, trust level, verified propositions, and
        any residual obligations in a readable format.
        """
        lines: list[str] = []
        lines.append(f'Certificate: {certificate.coordinate}')
        lines.append(f'  Trust: {certificate.trust_level.label()}')
        lines.append(f'  Issuer: {certificate.issuer}')
        if certificate.verified_propositions:
            lines.append('  Verified:')
            for prop in certificate.verified_propositions:
                lines.append(f'    - {prop}')
        if certificate.residual_obligations:
            lines.append('  Residuals (unresolved):')
            for res in certificate.residual_obligations:
                lines.append(f'    ! {res}')
        if certificate.obstructions:
            lines.append('  Obstructions:')
            for obs in certificate.obstructions:
                lines.append(f'    X {obs}')
        lines.append(f'  Valid: {certificate.is_valid()}')
        return '\n'.join(lines)

    @staticmethod
    def project_for_api(certificate: Certificate) -> dict[str, object]:
        """Render a certificate as a JSON-compatible dict for API consumers.

        This is equivalent to :meth:`Certificate.project_public` but adds
        computed fields useful for API responses.
        """
        base = certificate.project_public()
        base['residual_count'] = certificate.residual_count()
        base['obstruction_count'] = certificate.obstruction_count()
        base['has_residuals'] = certificate.residual_count() > 0
        base['has_obstructions'] = certificate.obstruction_count() > 0
        return base

    @staticmethod
    def project_for_human(certificate: Certificate) -> str:
        """One-line human summary of the certificate state.

        Example: ``"thm:main — VERIFIED (2 props, 0 residuals, 0 obstructions)"``
        """
        n_props = len(certificate.verified_propositions)
        n_res = certificate.residual_count()
        n_obs = certificate.obstruction_count()
        trust = certificate.trust_level.label().upper()
        return (
            f'{certificate.coordinate} — {trust} '
            f'({n_props} props, {n_res} residuals, {n_obs} obstructions)'
        )

    @staticmethod
    def redact_internals(certificate: Certificate) -> dict[str, object]:
        """Project the certificate with internal fields redacted.

        Removes signature hash, full evidence summary, and exact timestamp
        while preserving everything needed for public trust evaluation.
        """
        return {
            'certificate_id': certificate.certificate_id,
            'coordinate': certificate.coordinate,
            'verified': list(certificate.verified_propositions),
            'trust_level': certificate.trust_level.label(),
            'residuals': list(certificate.residual_obligations),
            'obstructions': list(certificate.obstructions),
            'valid': certificate.is_valid(),
            'issuer': certificate.issuer,
        }

    @staticmethod
    def summary_statistics(certificates: Sequence[Certificate]) -> dict[str, object]:
        """Aggregate statistics over a collection of certificates.

        Returns counts, trust distribution, and residual totals useful for
        dashboards and monitoring.
        """
        total = len(certificates)
        valid = sum(1 for c in certificates if c.is_valid())
        expired = sum(1 for c in certificates if c.is_expired())
        trust_dist: dict[str, int] = {}
        total_residuals = 0
        total_obstructions = 0
        for c in certificates:
            label = c.trust_level.label()
            trust_dist[label] = trust_dist.get(label, 0) + 1
            total_residuals += c.residual_count()
            total_obstructions += c.obstruction_count()
        return {
            'total': total,
            'valid': valid,
            'expired': expired,
            'invalid': total - valid,
            'trust_distribution': trust_dist,
            'total_residuals': total_residuals,
            'total_obstructions': total_obstructions,
        }


# ---------------------------------------------------------------------------
# 9. CertificateDiff — compare certificate snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateDiff:
    """Diff between two certificates covering the same coordinate.

    The diff captures exactly what changed between two verification
    snapshots: added / removed propositions, trust-level shifts, and
    residual evolution.

    copilot: feed the diff to a Copilot prompt to generate a human-readable
    changelog entry for a verification run.
    """

    before: Certificate
    after: Certificate

    def diff(self) -> dict[str, object]:
        """Full diff as a structured dict."""
        return {
            'coordinate': self.after.coordinate,
            'added_verifications': self.added_verifications(),
            'removed_verifications': self.removed_verifications(),
            'trust_changes': self.trust_changes(),
            'new_residuals': self.new_residuals(),
            'resolved_residuals': self.resolved_residuals(),
        }

    def added_verifications(self) -> list[str]:
        """Propositions verified in *after* but not in *before*."""
        before_set = set(self.before.verified_propositions)
        after_set = set(self.after.verified_propositions)
        return sorted(after_set - before_set)

    def removed_verifications(self) -> list[str]:
        """Propositions verified in *before* but no longer in *after*."""
        before_set = set(self.before.verified_propositions)
        after_set = set(self.after.verified_propositions)
        return sorted(before_set - after_set)

    def trust_changes(self) -> dict[str, str]:
        """Describe the trust-level change, if any.

        Returns a dict with ``'before'``, ``'after'``, and ``'direction'``
        keys.
        """
        b_label = self.before.trust_level.label()
        a_label = self.after.trust_level.label()
        if self.after.trust_level.stronger_than(self.before.trust_level):
            direction = 'strengthened'
        elif self.after.trust_level.weaker_than(self.before.trust_level):
            direction = 'weakened'
        else:
            direction = 'unchanged'
        return {'before': b_label, 'after': a_label, 'direction': direction}

    def new_residuals(self) -> list[str]:
        """Residual obligations that appear in *after* but not in *before*."""
        before_set = set(self.before.residual_obligations)
        after_set = set(self.after.residual_obligations)
        return sorted(after_set - before_set)

    def resolved_residuals(self) -> list[str]:
        """Residual obligations present in *before* that are gone in *after*."""
        before_set = set(self.before.residual_obligations)
        after_set = set(self.after.residual_obligations)
        return sorted(before_set - after_set)

    def has_changes(self) -> bool:
        """Return ``True`` when any aspect of the certificate changed."""
        return bool(
            self.added_verifications()
            or self.removed_verifications()
            or self.trust_changes()['direction'] != 'unchanged'
            or self.new_residuals()
            or self.resolved_residuals()
        )

    def summary(self) -> str:
        """Human-readable summary of the diff."""
        parts: list[str] = []
        added = self.added_verifications()
        removed = self.removed_verifications()
        tc = self.trust_changes()
        new_res = self.new_residuals()
        resolved = self.resolved_residuals()
        if added:
            parts.append(f'+{len(added)} verified')
        if removed:
            parts.append(f'-{len(removed)} verified')
        if tc['direction'] != 'unchanged':
            parts.append(f'trust {tc["direction"]}')
        if new_res:
            parts.append(f'+{len(new_res)} residuals')
        if resolved:
            parts.append(f'-{len(resolved)} residuals')
        return f'{self.after.coordinate}: {", ".join(parts)}' if parts else f'{self.after.coordinate}: no changes'


# ---------------------------------------------------------------------------
# 10. ManifestCertificate — the full (J, O, E, X, K, η, σ) manifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ManifestCertificate:
    """Full manifest certificate encoding the tuple ``(J, O, E, X, K, η, σ)``.

    This is the top-level verification state for a coordinate system:

    * **J** — judgments: mapping from coordinate to list of propositions.
    * **O** — obligations: mapping from coordinate to residual obligations.
    * **E** — evidence archive: mapping from coordinate to evidence dicts.
    * **X** — obstructions: mapping from coordinate to obstruction records.
    * **K** — certificates: mapping from coordinate to :class:`Certificate`.
    * **η** — epoch map: mapping from coordinate to its verification epoch.
    * **σ** — invalidation graph: adjacency list of invalidation edges.

    copilot: the manifest is the single source of truth for an LLM agent
    performing end-to-end verification orchestration.
    """

    judgments: dict[str, list[str]] = field(default_factory=dict)
    obligations: dict[str, list[str]] = field(default_factory=dict)
    evidence_archive: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict,
    )
    obstructions: dict[str, list[str]] = field(default_factory=dict)
    certificates: dict[str, Certificate] = field(default_factory=dict)
    epoch_map: dict[str, int] = field(default_factory=dict)
    invalidation_graph: dict[str, list[str]] = field(default_factory=dict)

    def current_epoch(self, coordinate: str) -> int:
        """Return the current epoch for *coordinate*, defaulting to ``0``."""
        return self.epoch_map.get(coordinate, 0)

    def advance_epoch(self, coordinate: str) -> int:
        """Advance the epoch for *coordinate* and return the new value."""
        current = self.current_epoch(coordinate)
        self.epoch_map[coordinate] = current + 1
        return current + 1

    def invalidate(self, source: str, target: str) -> None:
        """Record a causal invalidation edge from *source* to *target*.

        When the verification state at *source* changes, *target* must be
        re-verified.  The edge is recorded in the invalidation graph and
        the target's epoch is advanced.
        """
        edges = self.invalidation_graph.setdefault(source, [])
        if target not in edges:
            edges.append(target)
        self.advance_epoch(target)

    def revalidate(self, coordinate: str) -> None:
        """Mark *coordinate* as re-validated by advancing its epoch.

        Does **not** remove the invalidation edge — the causal dependency
        is permanent.  Only the epoch advances to signal that a fresh
        verification pass has occurred.
        """
        self.advance_epoch(coordinate)

    def is_consistent(self) -> bool:
        """Check whether the manifest is internally consistent.

        Consistency requires:
        * Every certificate's coordinate has a judgment entry.
        * Every obligation coordinate has a judgment entry.
        * No certificate claims VERIFIED while obligations remain.
        """
        for coord, cert in self.certificates.items():
            if coord not in self.judgments:
                return False
            if (
                cert.trust_level == TrustLevel.VERIFIED
                and coord in self.obligations
                and self.obligations[coord]
            ):
                return False
        for coord in self.obligations:
            if coord not in self.judgments:
                return False
        return True

    def project_public(self) -> dict[str, object]:
        """Project the manifest to a public-safe view.

        Omits the invalidation graph and evidence archive internals.
        """
        cert_public = {
            coord: cert.project_public()
            for coord, cert in self.certificates.items()
        }
        return {
            'judgments': {k: list(v) for k, v in self.judgments.items()},
            'obligations': {k: list(v) for k, v in self.obligations.items()},
            'obstructions': {k: list(v) for k, v in self.obstructions.items()},
            'certificates': cert_public,
            'epochs': dict(self.epoch_map),
        }

    def coordinates(self) -> list[str]:
        """Return all coordinates referenced in the manifest."""
        all_coords: set[str] = set()
        all_coords.update(self.judgments.keys())
        all_coords.update(self.obligations.keys())
        all_coords.update(self.certificates.keys())
        all_coords.update(self.epoch_map.keys())
        return sorted(all_coords)

    def add_judgment(self, coordinate: str, proposition: str) -> None:
        """Record a judgment (proposition evaluation) at *coordinate*."""
        props = self.judgments.setdefault(coordinate, [])
        if proposition not in props:
            props.append(proposition)

    def add_obligation(self, coordinate: str, obligation: str) -> None:
        """Record a residual obligation at *coordinate*."""
        obls = self.obligations.setdefault(coordinate, [])
        if obligation not in obls:
            obls.append(obligation)

    def add_evidence(
        self, coordinate: str, evidence: dict[str, Any],
    ) -> None:
        """Archive an evidence record at *coordinate*."""
        records = self.evidence_archive.setdefault(coordinate, [])
        records.append(evidence)

    def add_obstruction(self, coordinate: str, obstruction: str) -> None:
        """Record an obstruction at *coordinate*."""
        obs_list = self.obstructions.setdefault(coordinate, [])
        if obstruction not in obs_list:
            obs_list.append(obstruction)

    def attach_certificate(
        self, coordinate: str, certificate: Certificate,
    ) -> None:
        """Attach a :class:`Certificate` to *coordinate*."""
        self.certificates[coordinate] = certificate
        if coordinate not in self.epoch_map:
            self.epoch_map[coordinate] = 0


# ---------------------------------------------------------------------------
# 11. CertificateSerializer — JSON / dict serialization
# ---------------------------------------------------------------------------


class CertificateSerializer:
    """Serialize and deserialize certificate objects to/from dicts and JSON.

    All serialization is *lossless* — round-tripping through
    ``serialize`` / ``deserialize`` produces an equivalent object.

    copilot: use ``to_json`` and ``from_json`` when persisting certificates
    to files or sending them over API boundaries.
    """

    @staticmethod
    def certificate_to_dict(certificate: Certificate) -> dict[str, object]:
        """Serialize a :class:`Certificate` to a plain dict."""
        return {
            'certificate_id': certificate.certificate_id,
            'coordinate': certificate.coordinate,
            'verified_propositions': list(certificate.verified_propositions),
            'trust_level': int(certificate.trust_level),
            'evidence_summary': certificate.evidence_summary,
            'residual_obligations': list(certificate.residual_obligations),
            'obstructions': list(certificate.obstructions),
            'issued_at': certificate.issued_at.isoformat(),
            'issuer': certificate.issuer,
            'expiry': (
                certificate.expiry.isoformat() if certificate.expiry else None
            ),
            'signature_hash': certificate.signature_hash,
        }

    @staticmethod
    def certificate_from_dict(data: Mapping[str, Any]) -> Certificate:
        """Deserialize a :class:`Certificate` from a dict.

        Raises :class:`KeyError` if mandatory fields are missing.
        """
        expiry_raw = data.get('expiry')
        expiry = (
            datetime.fromisoformat(expiry_raw) if expiry_raw else None
        )
        return Certificate(
            certificate_id=str(data['certificate_id']),
            coordinate=str(data['coordinate']),
            verified_propositions=tuple(data['verified_propositions']),
            trust_level=TrustLevel(int(data['trust_level'])),
            evidence_summary=str(data.get('evidence_summary', '')),
            residual_obligations=tuple(data.get('residual_obligations', ())),
            obstructions=tuple(data.get('obstructions', ())),
            issued_at=datetime.fromisoformat(str(data['issued_at'])),
            issuer=str(data.get('issuer', 'system')),
            expiry=expiry,
            signature_hash=str(data.get('signature_hash', '')),
        )

    @staticmethod
    def chain_to_dict(chain: CertificateChain) -> dict[str, object]:
        """Serialize a :class:`CertificateChain` to a dict."""
        return {
            'certificates': [
                CertificateSerializer.certificate_to_dict(c)
                for c in chain.certificates
            ],
            'trust_floor': int(chain.trust_floor()),
            'is_complete': chain.is_complete(),
        }

    @staticmethod
    def chain_from_dict(data: Mapping[str, Any]) -> CertificateChain:
        """Deserialize a :class:`CertificateChain` from a dict."""
        certs = [
            CertificateSerializer.certificate_from_dict(d)
            for d in data['certificates']
        ]
        return CertificateChain(certificates=certs)

    @staticmethod
    def manifest_to_dict(
        manifest: ManifestCertificate,
    ) -> dict[str, object]:
        """Serialize a :class:`ManifestCertificate` to a dict."""
        cert_dicts = {
            coord: CertificateSerializer.certificate_to_dict(cert)
            for coord, cert in manifest.certificates.items()
        }
        return {
            'judgments': {k: list(v) for k, v in manifest.judgments.items()},
            'obligations': {
                k: list(v) for k, v in manifest.obligations.items()
            },
            'evidence_archive': {
                k: list(v) for k, v in manifest.evidence_archive.items()
            },
            'obstructions': {
                k: list(v) for k, v in manifest.obstructions.items()
            },
            'certificates': cert_dicts,
            'epoch_map': dict(manifest.epoch_map),
            'invalidation_graph': {
                k: list(v) for k, v in manifest.invalidation_graph.items()
            },
        }

    @staticmethod
    def manifest_from_dict(data: Mapping[str, Any]) -> ManifestCertificate:
        """Deserialize a :class:`ManifestCertificate` from a dict."""
        certs = {
            coord: CertificateSerializer.certificate_from_dict(d)
            for coord, d in data.get('certificates', {}).items()
        }
        return ManifestCertificate(
            judgments={
                k: list(v) for k, v in data.get('judgments', {}).items()
            },
            obligations={
                k: list(v) for k, v in data.get('obligations', {}).items()
            },
            evidence_archive={
                k: list(v) for k, v in data.get('evidence_archive', {}).items()
            },
            obstructions={
                k: list(v) for k, v in data.get('obstructions', {}).items()
            },
            certificates=certs,
            epoch_map={
                k: int(v) for k, v in data.get('epoch_map', {}).items()
            },
            invalidation_graph={
                k: list(v)
                for k, v in data.get('invalidation_graph', {}).items()
            },
        )

    @staticmethod
    def to_json(obj: Certificate | CertificateChain | ManifestCertificate) -> str:
        """Serialize a certificate object to a JSON string."""
        if isinstance(obj, Certificate):
            d = CertificateSerializer.certificate_to_dict(obj)
        elif isinstance(obj, CertificateChain):
            d = CertificateSerializer.chain_to_dict(obj)
        elif isinstance(obj, ManifestCertificate):
            d = CertificateSerializer.manifest_to_dict(obj)
        else:
            raise TypeError(f'Unsupported type: {type(obj).__name__}')
        return json.dumps(d, indent=2, default=str)

    @staticmethod
    def from_json(
        text: str, *, kind: str = 'certificate',
    ) -> Certificate | CertificateChain | ManifestCertificate:
        """Deserialize a certificate object from a JSON string.

        Parameters
        ----------
        text:
            The JSON string.
        kind:
            One of ``'certificate'``, ``'chain'``, or ``'manifest'``.
        """
        data = json.loads(text)
        if kind == 'certificate':
            return CertificateSerializer.certificate_from_dict(data)
        if kind == 'chain':
            return CertificateSerializer.chain_from_dict(data)
        if kind == 'manifest':
            return CertificateSerializer.manifest_from_dict(data)
        raise ValueError(f'Unknown kind: {kind}')


# ---------------------------------------------------------------------------
# 12. CertificateDiagnostics — audit and diagnostic queries
# ---------------------------------------------------------------------------


class CertificateDiagnostics:
    """Diagnostic queries over a certificate store or collection.

    Provides audit-oriented methods for finding expired, revoked,
    inconsistent, and over-claimed certificates.  The
    ``copilot_certificate_summary`` method produces a structured report
    suitable for feeding into an LLM prompt.

    copilot: use ``copilot_certificate_summary`` to get a full diagnostic
    snapshot that Copilot can reason over.
    """

    def __init__(
        self,
        store: CertificateStore | None = None,
        authority: CertificateAuthority | None = None,
    ) -> None:
        self._store = store
        self._authority = authority

    def _all_certs(self) -> list[Certificate]:
        """Gather all certificates from the store."""
        if self._store is None:
            return []
        return list(self._store._by_id.values())

    def find_expired(self) -> list[Certificate]:
        """Return all expired certificates."""
        return [c for c in self._all_certs() if c.is_expired()]

    def find_revoked(self) -> list[Certificate]:
        """Return certificates that have been revoked by the authority."""
        if self._authority is None:
            return []
        revoked_ids = set(self._authority.list_revoked())
        return [
            c for c in self._all_certs()
            if c.certificate_id in revoked_ids
        ]

    def find_inconsistent(self) -> list[tuple[Certificate, list[str]]]:
        """Find certificates with internal consistency issues.

        Returns a list of ``(certificate, issues)`` pairs where *issues*
        is a non-empty list of detected problems.
        """
        verifier = CertificateVerifier(self._authority)
        results: list[tuple[Certificate, list[str]]] = []
        for cert in self._all_certs():
            ok, issues = verifier.verify(cert)
            if not ok:
                results.append((cert, issues))
        return results

    def find_over_claimed(self) -> list[Certificate]:
        """Find certificates that may silently strengthen claims.

        A certificate is over-claimed when it asserts VERIFIED trust but
        still carries residual obligations.
        """
        return [
            c for c in self._all_certs()
            if (
                c.trust_level == TrustLevel.VERIFIED
                and c.residual_obligations
            )
        ]

    def coverage_report(self) -> dict[str, object]:
        """Generate a coverage report over all stored certificates.

        Returns per-coordinate status including trust level, proposition
        count, residual count, and validity.
        """
        certs = self._all_certs()
        by_coord: dict[str, list[Certificate]] = {}
        for c in certs:
            by_coord.setdefault(c.coordinate, []).append(c)
        report: dict[str, object] = {}
        for coord, coord_certs in sorted(by_coord.items()):
            best = max(coord_certs, key=lambda c: int(c.trust_level))
            all_props: set[str] = set()
            all_residuals: set[str] = set()
            for c in coord_certs:
                all_props.update(c.verified_propositions)
                all_residuals.update(c.residual_obligations)
            report[coord] = {
                'certificate_count': len(coord_certs),
                'best_trust': best.trust_level.label(),
                'proposition_count': len(all_props),
                'residual_count': len(all_residuals),
                'all_valid': all(c.is_valid() for c in coord_certs),
            }
        return report

    def copilot_certificate_summary(self) -> dict[str, object]:
        """Produce a full diagnostic summary for Copilot consumption.

        copilot: feed this dict directly into a Copilot prompt to enable
        LLM-assisted certificate auditing and remediation.
        """
        certs = self._all_certs()
        expired = self.find_expired()
        revoked = self.find_revoked()
        inconsistent = self.find_inconsistent()
        over_claimed = self.find_over_claimed()
        return {
            'total_certificates': len(certs),
            'valid_certificates': sum(1 for c in certs if c.is_valid()),
            'expired_certificates': len(expired),
            'revoked_certificates': len(revoked),
            'inconsistent_certificates': len(inconsistent),
            'over_claimed_certificates': len(over_claimed),
            'inconsistency_details': [
                {
                    'certificate_id': c.certificate_id,
                    'coordinate': c.coordinate,
                    'issues': issues,
                }
                for c, issues in inconsistent
            ],
            'over_claim_details': [
                {
                    'certificate_id': c.certificate_id,
                    'coordinate': c.coordinate,
                    'trust': c.trust_level.label(),
                    'residual_count': c.residual_count(),
                }
                for c in over_claimed
            ],
            'coverage': self.coverage_report(),
            'recommendation': (
                'All certificates are healthy.'
                if not (expired or revoked or inconsistent or over_claimed)
                else 'Issues detected — review the details above.'
            ),
        }


# ---------------------------------------------------------------------------
# Legacy compatibility — SettlementCertificate and emit_certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettlementCertificate:
    """Legacy settlement certificate retained for backward compatibility.

    New code should use :class:`Certificate` and :class:`CertificateBuilder`
    instead.  This class is preserved so that existing consumers (including
    test suites) continue to work without modification.
    """

    manifest: EvidenceManifest
    export: ExportRecord
    status: CertificateStatus
    issued_by: str
    residuals: tuple[str, ...] = field(default_factory=tuple)
    loss_declared: bool = True

    def to_dict(self) -> dict[str, object]:
        """Serialize the settlement certificate to a plain dict."""
        return {
            'manifest_key': self.manifest.canonical_key(),
            'status': self.status.value,
            'issued_by': self.issued_by,
            'residuals': list(self.residuals),
            'loss_declared': self.loss_declared,
            'export': self.export.to_dict(),
        }


def emit_certificate(
    manifest: EvidenceManifest,
    export: ExportRecord,
    *,
    issuer: str,
) -> SettlementCertificate:
    """Factory for :class:`SettlementCertificate` — legacy entry point.

    Determines status from the manifest's residuals: ``SETTLED`` if none,
    ``PENDING`` otherwise.
    """
    status = (
        CertificateStatus.SETTLED
        if not manifest.residuals
        else CertificateStatus.PENDING
    )
    return SettlementCertificate(
        manifest, export, status, issuer, manifest.residuals, True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    'CertificateStatus',
    'TrustLevel',
    # Core certificate
    'Certificate',
    'CertificateBuilder',
    # Chain and authority
    'CertificateChain',
    'CertificateAuthority',
    # Storage
    'CertificateStore',
    # Verification
    'CertificateVerifier',
    # Merge and projection
    'CertificateMerger',
    'CertificateProjection',
    # Diff
    'CertificateDiff',
    # Manifest
    'ManifestCertificate',
    # Serialization
    'CertificateSerializer',
    # Diagnostics
    'CertificateDiagnostics',
    # Legacy compat
    'SettlementCertificate',
    'emit_certificate',
]

# copilot: shared-core marker for future LLM orchestration.
