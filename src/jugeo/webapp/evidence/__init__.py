"""Evidence collection and analysis for web application verification.

Implements the multi-channel evidence architecture from §4 of the
Geometry of Web Applications theory document.
"""
from __future__ import annotations

from .models import (
    WebEvidenceChannel,
    WebTrustLevel,
    WebEvidence,
    EvidenceBundle,
    ChannelCapability,
    EvidenceGap,
    TRUST_ORDER,
)
from .multi_channel import (
    MultiChannelEvidenceEngine,
    EvidenceCombiner,
    EvidenceGapAnalyzer,
)
from .static_analysis import CrossLanguageStaticAnalyzer
from .security_scanner import WebSecurityScanner, SecuritySeverity
from .integration import WebEvidenceCollector

__all__ = [
    "WebEvidenceChannel",
    "WebTrustLevel",
    "WebEvidence",
    "EvidenceBundle",
    "ChannelCapability",
    "EvidenceGap",
    "TRUST_ORDER",
    "MultiChannelEvidenceEngine",
    "EvidenceCombiner",
    "EvidenceGapAnalyzer",
    "CrossLanguageStaticAnalyzer",
    "WebSecurityScanner",
    "SecuritySeverity",
    "WebEvidenceCollector",
]
