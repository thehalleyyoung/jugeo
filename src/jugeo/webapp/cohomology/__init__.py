"""Čech cohomology computation for web application sites.

Implements Čech cohomology for detecting descent failures (bugs) in
web applications, where H^0 = global sections (consistent state),
H^1 = gluing failures (cross-language inconsistencies),
H^2 = higher obstructions.
"""
from __future__ import annotations

from .models import (
    NerveCell,
    Cochain,
    CohomologyGroup,
    CechComplex,
)
from .cech_computation import CechCohomologyComputer

__all__ = [
    "NerveCell",
    "Cochain",
    "CohomologyGroup",
    "CechComplex",
    "CechCohomologyComputer",
]
