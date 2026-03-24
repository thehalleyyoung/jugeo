"""Trust topology and transport algebra for web application verification.

Implements the web trust topology from §2.6 of the Geometry of Web
Applications theory document.
"""
from __future__ import annotations

from .models import (
    TrustBoundary,
    TrustTransport,
    TrustPolicy,
    TrustRule,
    TrustReport,
)
from .web_trust import (
    WebTrustTopology,
    NeverTrustClientChecker,
    TrustPolicyEngine,
)
from .trust_transport import (
    TrustAlgebra,
    TrustCertificate,
    CertificateEmitter,
)
from .bundle import (
    LifecycleStage,
    WebJudgment,
    WebFiber,
    WebTrustConnection,
    WebCurvature,
    LifecycleHolonomy,
    WebTrustBundle,
)

__all__ = [
    "TrustBoundary",
    "TrustTransport",
    "TrustPolicy",
    "TrustRule",
    "TrustReport",
    "WebTrustTopology",
    "NeverTrustClientChecker",
    "TrustPolicyEngine",
    "TrustAlgebra",
    "TrustCertificate",
    "CertificateEmitter",
    "LifecycleStage",
    "WebJudgment",
    "WebFiber",
    "WebTrustConnection",
    "WebCurvature",
    "LifecycleHolonomy",
    "WebTrustBundle",
]
