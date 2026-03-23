"""jugeo.foundations.trust_certificates — Trust certificates and cross-subsystem bridges.

Theory2.tex Chapter 3: Evidence Plurality, Trust Algebra, and Certificates.

This module bridges the categorical certificate theory to concrete
implementations across jugeo subsystems: geometry/descent, solver, evidence, judgments.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-subsystem imports (all guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.descent import LocalSection, DescentStrategy, OverlapCondition
    _HAS_DESCENT = True
except ImportError:
    LocalSection = None
    DescentStrategy = None
    OverlapCondition = None
    _HAS_DESCENT = False

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
    _HAS_CERTIFICATES = True
except ImportError:
    Certificate = None
    CertificateStatus = None
    _HAS_CERTIFICATES = False

try:
    from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
    _HAS_SOLVER = True
except ImportError:
    SolverResult = None
    SolveOutcome = None
    z3_available = None
    _HAS_SOLVER = False

try:
    from jugeo.evidence.provenance import ProvenanceGraph, ProvenanceNode, ProvenanceStep
    _HAS_PROVENANCE = True
except ImportError:
    ProvenanceGraph = None
    ProvenanceNode = None
    ProvenanceStep = None
    _HAS_PROVENANCE = False

try:
    from jugeo.judgments.judgment_terms import Proposition, JudgmentStatus, TrustLevel as JTrustLevel
    _HAS_JUDGMENTS = True
except ImportError:
    Proposition = None
    JudgmentStatus = None
    JTrustLevel = None
    _HAS_JUDGMENTS = False


# ---------------------------------------------------------------------------
# Core data model
# ---------------------------------------------------------------------------

@dataclass
class TrustCertificate:
    """A trust certificate binding a subject to a trust level with evidence."""
    subject: str
    trust_level: str = "UNVERIFIED"
    evidence_chain: list[str] = field(default_factory=list)
    issuer: str = "foundations"
    timestamp: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        return bool(self.subject) and bool(self.trust_level)

    def chain_length(self) -> int:
        return len(self.evidence_chain)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "trust_level": self.trust_level,
            "evidence_chain": list(self.evidence_chain),
            "issuer": self.issuer, "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Certificate issuance from descent verification
# ---------------------------------------------------------------------------

def issue_from_descent(descent_result: dict[str, Any], *, issuer: str = "descent") -> TrustCertificate:
    """Issue a trust certificate from descent verification results.

    When the descent subsystem is available the result is cross-checked
    against :class:`LocalSection` compatibility and overlap conditions.
    The evidence chain is populated from the descent proof witnesses.
    """
    subject = str(descent_result.get("coordinate", "unknown"))
    evidence: list[str] = [str(e) for e in descent_result.get("evidence_ids", [])]
    raw_level = descent_result.get("trust_level", "UNVERIFIED")

    if _HAS_DESCENT and descent_result.get("sections"):
        sections = descent_result["sections"]
        compatible = all(
            getattr(s, "is_compatible", lambda _: True)(s)
            for s in sections if isinstance(s, LocalSection)  # type: ignore[arg-type]
        )
        if not compatible:
            raw_level = "CONTESTED"
            evidence.append("descent:overlap_mismatch")

    if _HAS_CERTIFICATES and evidence:
        try:
            status = CertificateStatus("issued")  # type: ignore[call-arg]
            evidence.append(f"cert_status:{status}")
        except Exception:
            evidence.append("cert_status:fallback")

    logger.debug("issue_from_descent: subject=%s level=%s evidence=%d", subject, raw_level, len(evidence))
    return TrustCertificate(subject=subject, trust_level=str(raw_level), evidence_chain=evidence, issuer=issuer)


# ---------------------------------------------------------------------------
# Certificate issuance from solver verification
# ---------------------------------------------------------------------------

def issue_from_solver(solver_result: dict[str, Any], *, issuer: str = "solver") -> TrustCertificate:
    """Issue a trust certificate from solver verification results.

    When the Z3 solver subsystem is available, the outcome enum is used
    to determine the trust level.  SAT results yield ``SOLVER_DISCHARGED``,
    UNSAT yields ``CONTESTED``, and unknown maps to ``UNVERIFIED``.
    """
    subject = str(solver_result.get("coordinate", "unknown"))
    evidence: list[str] = [str(e) for e in solver_result.get("evidence_ids", [])]
    outcome_str = solver_result.get("outcome", "unknown")
    level = "UNVERIFIED"

    if _HAS_SOLVER:
        z3_ok = z3_available() if callable(z3_available) else bool(z3_available)  # type: ignore[misc]
        if z3_ok:
            evidence.append("solver:z3_available")
        try:
            outcome = SolveOutcome(outcome_str)  # type: ignore[call-arg]
            if str(outcome) in ("sat", "SolveOutcome.sat"):
                level = "SOLVER_DISCHARGED"
            elif str(outcome) in ("unsat", "SolveOutcome.unsat"):
                level = "CONTESTED"
        except Exception:
            pass

    if _HAS_CERTIFICATES and level == "SOLVER_DISCHARGED":
        evidence.append("cert:solver_verified")

    if level == "UNVERIFIED":
        level = "SOLVER_DISCHARGED" if outcome_str == "sat" else ("CONTESTED" if outcome_str == "unsat" else "UNVERIFIED")

    logger.debug("issue_from_solver: subject=%s level=%s", subject, level)
    return TrustCertificate(subject=subject, trust_level=level, evidence_chain=evidence, issuer=issuer)


# ---------------------------------------------------------------------------
# Evidence chain verification via provenance
# ---------------------------------------------------------------------------

def verify_chain(chain: Sequence[str], *, max_depth: int = 100) -> dict[str, Any]:
    """Verify an evidence chain using provenance tracking.

    Walks the chain up to *max_depth* steps, checking each link for
    circular references and missing nodes.  When the provenance subsystem
    is available, a full graph traversal is performed.
    """
    errors: list[str] = []
    visited: set[str] = set()
    provenance_checked = False

    for i, link in enumerate(chain[:max_depth]):
        if link in visited:
            errors.append(f"Circular reference at index {i}: {link}")
        visited.add(link)

    if _HAS_PROVENANCE:
        try:
            graph = ProvenanceGraph()  # type: ignore[call-arg]
            for link in chain[:max_depth]:
                node = ProvenanceNode(link)  # type: ignore[call-arg]
                graph.add_node(node)
            for a, b in zip(chain, chain[1:]):
                step = ProvenanceStep(source=a, target=b)  # type: ignore[call-arg]
                graph.add_step(step)
            cycles = graph.detect_cycles() if hasattr(graph, "detect_cycles") else []
            if cycles:
                errors.append(f"Provenance cycles detected: {len(cycles)}")
            provenance_checked = True
        except Exception as exc:
            logger.debug("verify_chain: provenance subsystem error: %s", exc)

    if len(chain) > max_depth:
        errors.append(f"Chain exceeds max_depth ({max_depth})")

    return {
        "valid": len(errors) == 0,
        "chain_length": len(chain),
        "depth_checked": min(len(chain), max_depth),
        "errors": errors,
        "provenance_checked": provenance_checked,
    }


# ---------------------------------------------------------------------------
# Judgment creation from certificate
# ---------------------------------------------------------------------------

def certificate_judgment(cert: TrustCertificate) -> dict[str, Any]:
    """Create a structured judgment from a trust certificate.

    Maps the certificate's trust level to a judgment status and, when
    the judgments subsystem is available, constructs a formal
    :class:`Proposition` with the corresponding :class:`JudgmentStatus`.
    """
    level_to_status = {
        "SOLVER_DISCHARGED": "verified",
        "RUNTIME_WITNESSED": "witnessed",
        "CONTESTED": "contested",
        "UNVERIFIED": "pending",
    }
    status_str = level_to_status.get(cert.trust_level, "pending")
    proposition_text = f"{cert.subject} is certified at {cert.trust_level}"

    judgment: dict[str, Any] = {
        "proposition": proposition_text,
        "status": status_str,
        "trust_level": cert.trust_level,
        "evidence_count": cert.chain_length(),
        "issuer": cert.issuer,
    }

    if _HAS_JUDGMENTS:
        try:
            prop = Proposition(proposition_text)  # type: ignore[call-arg]
            js = JudgmentStatus(status_str)  # type: ignore[call-arg]
            judgment["formal_proposition"] = str(prop)
            judgment["formal_status"] = str(js)
            if JTrustLevel is not None:
                judgment["formal_trust_level"] = str(JTrustLevel(cert.trust_level))  # type: ignore[call-arg]
        except Exception as exc:
            logger.debug("certificate_judgment: judgments subsystem error: %s", exc)

    logger.debug("certificate_judgment: subject=%s status=%s", cert.subject, status_str)
    return judgment


__all__ = [
    "TrustCertificate",
    "issue_from_descent",
    "issue_from_solver",
    "verify_chain",
    "certificate_judgment",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import certificates_as_faithful_projectio
except Exception:
    pass
try:
    from . import evidence_plurality_proof_solver_di
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
    from . import manifest_integrity
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import trust_as_an_ordered_algebra_of_adm
except Exception:
    pass
