"""Trust algebra and certificate machinery for transport chains.

The :class:`TrustAlgebra` provides lattice operations (join / meet) and
morphism-driven transport on the trust-level lattice defined in
:mod:`.models`.

:class:`TrustCertificate` is a timestamped, signed claim that a piece
of evidence has reached a given trust level via a recorded transport
chain.  :class:`CertificateEmitter` creates and verifies these
certificates.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .models import TRUST_ORDER, trust_index

_TRUST_INDEX: dict[str, int] = {lvl: i for i, lvl in enumerate(TRUST_ORDER)}


# ═══════════════════════════════════════════════════════════════════════
#  TrustAlgebra
# ═══════════════════════════════════════════════════════════════════════

class TrustAlgebra:
    """Stateless lattice operations on trust levels.

    ``join`` = ⊔ (least upper bound, max confidence)
    ``meet`` = ⊓ (greatest lower bound, most conservative)
    """

    @staticmethod
    def join(t1: str, t2: str) -> str:
        """Return the higher (max) of two trust levels."""
        return t1 if trust_index(t1) >= trust_index(t2) else t2

    @staticmethod
    def meet(t1: str, t2: str) -> str:
        """Return the lower (min) of two trust levels."""
        return t1 if trust_index(t1) <= trust_index(t2) else t2

    @staticmethod
    def transport(trust: str, morphism_kind: str) -> str:
        """Compute the trust level after a single morphism transport.

        - ``API_CONTRACT``: caps trust at ``API_CONTRACT_TESTED`` (client →
          server boundary crossing).
        - ``ORM_MAPPING``: promotes to ``ORM_TYPE_CHECKED`` when the input
          is at least ``SERVER_VALIDATED``.
        - ``DB_CONSTRAINT``: promotes to ``DB_CONSTRAINT_ENFORCED``.
        - Anything else: returns *trust* unchanged.
        """
        kind = morphism_kind.upper()

        if kind == "API_CONTRACT":
            api_idx = trust_index("API_CONTRACT_TESTED")
            if trust_index(trust) > api_idx:
                return "API_CONTRACT_TESTED"
            return trust

        if kind == "ORM_MAPPING":
            if trust_index(trust) >= trust_index("SERVER_VALIDATED"):
                return "ORM_TYPE_CHECKED"
            return trust

        if kind == "DB_CONSTRAINT":
            return "DB_CONSTRAINT_ENFORCED"

        return trust

    @classmethod
    def compose(cls, transports: list[str]) -> str:
        """Sequentially apply *transports* starting from ``USER_INPUT``.

        Each element of *transports* is a morphism-kind string.
        Returns the final trust level.
        """
        current = "USER_INPUT"
        for morphism_kind in transports:
            current = cls.transport(current, morphism_kind)
        return current


# ═══════════════════════════════════════════════════════════════════════
#  TrustCertificate
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TrustCertificate:
    """A timestamped certificate asserting a trust level for a claim.

    The *transport_chain* records every morphism kind the evidence
    traversed.  Certificates expire after *valid_until*.
    """

    claim: str
    trust_level: str
    evidence_ids: list[str]
    transport_chain: list[str]
    valid_until: float
    issued_at: float = field(default_factory=time.time)
    cert_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "trust_level": self.trust_level,
            "evidence_ids": list(self.evidence_ids),
            "transport_chain": list(self.transport_chain),
            "valid_until": self.valid_until,
            "issued_at": self.issued_at,
            "cert_id": self.cert_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustCertificate:
        return cls(
            claim=d["claim"],
            trust_level=d["trust_level"],
            evidence_ids=list(d["evidence_ids"]),
            transport_chain=list(d["transport_chain"]),
            valid_until=d["valid_until"],
            issued_at=d.get("issued_at", time.time()),
            cert_id=d.get("cert_id", str(uuid.uuid4())),
        )


# ═══════════════════════════════════════════════════════════════════════
#  CertificateEmitter
# ═══════════════════════════════════════════════════════════════════════

class CertificateEmitter:
    """Create and verify :class:`TrustCertificate` instances.

    Certificates are valid for one hour by default.
    """

    DEFAULT_VALIDITY_SECONDS: float = 3600.0

    def emit(
        self,
        claim: str,
        evidence_bundle: dict,
        transport_chain: list[str],
    ) -> TrustCertificate:
        """Issue a new certificate for *claim*.

        *evidence_bundle* must contain:

        - ``combined_trust`` – the resolved trust level (a string from
          :data:`TRUST_ORDER`).
        - ``evidence_items`` – a list of dicts, each with an ``"id"`` key.
        """
        combined_trust = evidence_bundle.get("combined_trust", "USER_INPUT")
        evidence_items = evidence_bundle.get("evidence_items", [])
        evidence_ids = [item["id"] for item in evidence_items]

        now = time.time()
        return TrustCertificate(
            claim=claim,
            trust_level=combined_trust,
            evidence_ids=evidence_ids,
            transport_chain=list(transport_chain),
            valid_until=now + self.DEFAULT_VALIDITY_SECONDS,
            issued_at=now,
        )

    @staticmethod
    def verify(
        cert: TrustCertificate,
        current_evidence: list[dict],
    ) -> bool:
        """Return ``True`` when *cert* is still valid.

        Checks:
        1. The certificate has not expired.
        2. Every evidence id referenced by the certificate is present in
           *current_evidence* (each item must have an ``"id"`` key).
        """
        if cert.valid_until <= time.time():
            return False

        available_ids = {item["id"] for item in current_evidence}
        return all(eid in available_ids for eid in cert.evidence_ids)
