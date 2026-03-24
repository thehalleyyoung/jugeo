"""
Data models for the web-application verification pipeline.

``VerificationLevel`` enumerates the depth of analysis — from lightweight
syntax checks through full fibered-descent and visual-invariant verification.
``VerificationResult`` captures every finding, and ``VerificationConfig``
controls which checks are enabled.

All models use @dataclass with to_dict / from_dict for serialisation.
Enums use the (str, Enum) pattern so they serialise as plain strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


__all__ = [
    "VerificationLevel",
    "VerificationResult",
    "VerificationConfig",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VerificationLevel(str, Enum):
    """Depth of analysis performed by the verification pipeline.

    Levels are ordered — each includes the checks of all preceding levels.
    """

    SYNTAX_ONLY = "syntax_only"
    """Parse every source file for basic syntactic validity."""

    SINGLE_LANGUAGE = "single_language"
    """Per-language structural checks (e.g. balanced braces, imports)."""

    CROSS_LANGUAGE = "cross_language"
    """Cross-language consistency checks across layer boundaries."""

    FULL_DESCENT = "full_descent"
    """Full fibered-descent verification of gluing conditions."""

    VISUAL_INVARIANTS = "visual_invariants"
    """Visual-invariant checks (viewport units, responsive breakpoints)."""


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Complete output of the verification pipeline.

    ``passed`` and ``failed`` contain human-readable check descriptions.
    ``obstructions`` list cohomology-style gluing failures as dicts with
    keys: *id*, *description*, *severity*, *location*, *repair_hint*.
    """

    level: VerificationLevel
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    obstructions: list[dict[str, str]] = field(default_factory=list)
    evidence_coverage: dict[str, Any] = field(default_factory=lambda: {
        "total": 0,
        "covered": 0,
        "pct": 0.0,
    })
    trust_summary: dict[str, Any] = field(default_factory=lambda: {
        "highest": "",
        "lowest": "",
        "distribution": {},
    })
    timing_ms: float = 0.0
    overall_passed: bool = True

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "passed": list(self.passed),
            "failed": list(self.failed),
            "warnings": list(self.warnings),
            "obstructions": [dict(o) for o in self.obstructions],
            "evidence_coverage": dict(self.evidence_coverage),
            "trust_summary": dict(self.trust_summary),
            "timing_ms": self.timing_ms,
            "overall_passed": self.overall_passed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        return cls(
            level=VerificationLevel(data["level"]),
            passed=data.get("passed", []),
            failed=data.get("failed", []),
            warnings=data.get("warnings", []),
            obstructions=data.get("obstructions", []),
            evidence_coverage=data.get("evidence_coverage", {
                "total": 0, "covered": 0, "pct": 0.0,
            }),
            trust_summary=data.get("trust_summary", {
                "highest": "", "lowest": "", "distribution": {},
            }),
            timing_ms=data.get("timing_ms", 0.0),
            overall_passed=data.get("overall_passed", True),
        )

    # -- helpers -------------------------------------------------------------

    @property
    def pass_count(self) -> int:
        """Number of checks that passed."""
        return len(self.passed)

    @property
    def fail_count(self) -> int:
        """Number of checks that failed."""
        return len(self.failed)

    @property
    def obstruction_count(self) -> int:
        """Number of cohomology obstructions detected."""
        return len(self.obstructions)


# ---------------------------------------------------------------------------
# Verification configuration
# ---------------------------------------------------------------------------

@dataclass
class VerificationConfig:
    """Controls which checks the verification pipeline executes.

    ``layers_to_check`` names the language layers (coordinate patches)
    to include.  ``trust_threshold`` is the minimum evidence-trust level
    required for a cross-language claim to be accepted.
    """

    level: VerificationLevel = VerificationLevel.CROSS_LANGUAGE
    layers_to_check: list[str] = field(default_factory=lambda: [
        "python",
        "javascript",
        "css",
        "html",
        "sql",
        "template",
    ])
    trust_threshold: str = "SERVER_VALIDATED"
    timeout_ms: float = 30000.0
    include_security: bool = True
    include_accessibility: bool = False

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "layers_to_check": list(self.layers_to_check),
            "trust_threshold": self.trust_threshold,
            "timeout_ms": self.timeout_ms,
            "include_security": self.include_security,
            "include_accessibility": self.include_accessibility,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationConfig:
        return cls(
            level=VerificationLevel(data.get(
                "level", VerificationLevel.CROSS_LANGUAGE.value,
            )),
            layers_to_check=data.get("layers_to_check", [
                "python", "javascript", "css", "html", "sql", "template",
            ]),
            trust_threshold=data.get("trust_threshold", "SERVER_VALIDATED"),
            timeout_ms=data.get("timeout_ms", 30000.0),
            include_security=data.get("include_security", True),
            include_accessibility=data.get("include_accessibility", False),
        )
