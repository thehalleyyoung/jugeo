"""Verification pipeline for web applications.

Implements the full verification pipeline that integrates evidence
collection, trust topology, fibered descent, and cohomology computation.
"""
from __future__ import annotations

from .models import (
    VerificationLevel,
    VerificationResult,
    VerificationConfig,
)
from .pipeline import WebVerificationPipeline

__all__ = [
    "VerificationLevel",
    "VerificationResult",
    "VerificationConfig",
    "WebVerificationPipeline",
]
