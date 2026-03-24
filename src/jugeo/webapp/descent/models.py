"""
Data models for the web descent module.

Descent checking detects Čech cohomology obstructions — H¹ (pairwise overlap
failures) and H² (triple overlap failures) — across the language layers of a
web application.

All models use ``@dataclass`` with ``to_dict`` / ``from_dict`` for
serialisation.  Enums use the ``(str, Enum)`` pattern so they serialise as
plain strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


__all__ = [
    "DescentStrategy",
    "CohomologyClass",
    "WebObstruction",
    "DescentResult",
    "DescentConfiguration",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DescentStrategy(str, Enum):
    """Strategy for running descent checks."""

    FULL_CHECK = "full_check"
    """Check every overlap condition across all layers."""

    INCREMENTAL = "incremental"
    """Only check conditions affected by recently changed files."""

    LAYER_BOUNDARY_ONLY = "layer_boundary_only"
    """Only check conditions on boundaries between distinct layers."""

    TRUST_BOUNDARY_ONLY = "trust_boundary_only"
    """Only check conditions that cross a trust boundary."""


class CohomologyClass(str, Enum):
    """
    Which Čech cohomology group an obstruction lives in.

    * **H⁰** — global sections: consistent states that extend across all layers.
    * **H¹** — pairwise overlap obstructions (the standard sheaf condition).
    * **H²** — triple overlap obstructions (higher coherence failures).
    """

    H0_GLOBAL_SECTION = "h0_global_section"
    """A globally consistent section — no obstruction."""

    H1_OVERLAP_OBSTRUCTION = "h1_overlap_obstruction"
    """Pairwise overlap failure between two language layers."""

    H2_TRIPLE_OBSTRUCTION = "h2_triple_obstruction"
    """Triple overlap failure: three layers all disagree simultaneously."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WebObstruction:
    """
    A detected cohomological obstruction in the web application.

    This is the descent module's enrichment of a raw
    :class:`~jugeo.webapp.cross_language.models.OverlapViolation` — it adds the
    cohomology class, affected coordinate names, and a structured evidence dict.

    Parameters
    ----------
    id : str
        Deterministic identifier for this obstruction.
    cohomology_class : CohomologyClass
        Which Čech cohomology group the obstruction belongs to.
    overlap_kind : str
        The :class:`OverlapKind` value string naming the overlap condition.
    description : str
        Human-readable description of what went wrong.
    coordinates : list[str]
        Language-layer coordinate names involved (e.g. ``["python:route:/login",
        "template:login.html:user"]``).
    severity : str
        One of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``.
    repair_hint : str
        Actionable suggestion for fixing the obstruction.
    evidence : dict
        Structured evidence dict with keys depending on the overlap kind
        (e.g. ``{"left_detail": ..., "right_detail": ..., "file": ...}``).
    """

    id: str
    cohomology_class: CohomologyClass
    overlap_kind: str
    description: str
    coordinates: list[str] = field(default_factory=list)
    severity: str = "high"
    repair_hint: str = ""
    evidence: dict = field(default_factory=dict)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cohomology_class": self.cohomology_class.value,
            "overlap_kind": self.overlap_kind,
            "description": self.description,
            "coordinates": list(self.coordinates),
            "severity": self.severity,
            "repair_hint": self.repair_hint,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebObstruction:
        return cls(
            id=data["id"],
            cohomology_class=CohomologyClass(data["cohomology_class"]),
            overlap_kind=data["overlap_kind"],
            description=data["description"],
            coordinates=data.get("coordinates", []),
            severity=data.get("severity", "high"),
            repair_hint=data.get("repair_hint", ""),
            evidence=data.get("evidence", {}),
        )

    # -- helpers -------------------------------------------------------------

    @property
    def is_blocking(self) -> bool:
        """Whether this obstruction should block deployment."""
        return self.severity in ("critical", "high")

    @property
    def affected_layers(self) -> set[str]:
        """Extract layer names from the coordinate strings."""
        layers: set[str] = set()
        for coord in self.coordinates:
            parts = coord.split(":")
            if parts:
                layers.add(parts[0])
        return layers


@dataclass
class DescentResult:
    """
    Result of running a descent check.

    Aggregates all detected obstructions together with coverage statistics
    and timing information.

    Parameters
    ----------
    strategy : DescentStrategy
        Which strategy was used for the check.
    obstructions : list[WebObstruction]
        All detected obstructions.
    checked_conditions : int
        Number of overlap conditions that were evaluated.
    passed_conditions : int
        Number of overlap conditions that passed (no violations).
    coverage_score : float
        Fraction of total conditions that were checked, in ``[0, 1]``.
    timing_ms : float
        Wall-clock time in milliseconds.
    """

    strategy: DescentStrategy
    obstructions: list[WebObstruction] = field(default_factory=list)
    checked_conditions: int = 0
    passed_conditions: int = 0
    coverage_score: float = 0.0
    timing_ms: float = 0.0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "obstructions": [o.to_dict() for o in self.obstructions],
            "checked_conditions": self.checked_conditions,
            "passed_conditions": self.passed_conditions,
            "coverage_score": self.coverage_score,
            "timing_ms": self.timing_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DescentResult:
        return cls(
            strategy=DescentStrategy(data["strategy"]),
            obstructions=[
                WebObstruction.from_dict(o)
                for o in data.get("obstructions", [])
            ],
            checked_conditions=data.get("checked_conditions", 0),
            passed_conditions=data.get("passed_conditions", 0),
            coverage_score=data.get("coverage_score", 0.0),
            timing_ms=data.get("timing_ms", 0.0),
        )

    # -- helpers -------------------------------------------------------------

    @property
    def has_obstructions(self) -> bool:
        """``True`` if any obstructions were found."""
        return len(self.obstructions) > 0

    @property
    def blocking_count(self) -> int:
        """Number of obstructions that should block deployment."""
        return sum(1 for o in self.obstructions if o.is_blocking)

    @property
    def h1_count(self) -> int:
        """Number of H¹ (pairwise overlap) obstructions."""
        return sum(
            1 for o in self.obstructions
            if o.cohomology_class == CohomologyClass.H1_OVERLAP_OBSTRUCTION
        )

    @property
    def h2_count(self) -> int:
        """Number of H² (triple overlap) obstructions."""
        return sum(
            1 for o in self.obstructions
            if o.cohomology_class == CohomologyClass.H2_TRIPLE_OBSTRUCTION
        )

    def obstructions_by_kind(self) -> dict[str, list[WebObstruction]]:
        """Group obstructions by their overlap kind."""
        result: dict[str, list[WebObstruction]] = {}
        for o in self.obstructions:
            result.setdefault(o.overlap_kind, []).append(o)
        return result

    def summary(self) -> str:
        """One-line human-readable summary."""
        total = len(self.obstructions)
        if total == 0:
            return (
                f"Descent check passed: {self.passed_conditions}/"
                f"{self.checked_conditions} conditions OK "
                f"(coverage {self.coverage_score:.0%})"
            )
        return (
            f"Descent check found {total} obstruction(s): "
            f"{self.h1_count} H¹, {self.h2_count} H², "
            f"{self.blocking_count} blocking "
            f"({self.passed_conditions}/{self.checked_conditions} passed, "
            f"coverage {self.coverage_score:.0%})"
        )


@dataclass
class DescentConfiguration:
    """
    Configuration for a descent check run.

    Parameters
    ----------
    strategy : DescentStrategy
        Which checking strategy to use.
    layers_to_check : list[str] | None
        Specific layers to check.  ``None`` means all layers.
    max_depth : int
        Maximum Čech nerve depth (2 = pairwise only, 3 = include triples).
    timeout_ms : float
        Hard timeout in milliseconds.
    trust_threshold : str
        Minimum trust level value string below which a violation is flagged.
    """

    strategy: DescentStrategy = DescentStrategy.FULL_CHECK
    layers_to_check: list[str] | None = None
    max_depth: int = 5
    timeout_ms: float = 30000.0
    trust_threshold: str = "server_validated"

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "layers_to_check": (
                list(self.layers_to_check)
                if self.layers_to_check is not None
                else None
            ),
            "max_depth": self.max_depth,
            "timeout_ms": self.timeout_ms,
            "trust_threshold": self.trust_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DescentConfiguration:
        raw_layers = data.get("layers_to_check")
        return cls(
            strategy=DescentStrategy(
                data.get("strategy", DescentStrategy.FULL_CHECK.value)
            ),
            layers_to_check=(
                list(raw_layers) if raw_layers is not None else None
            ),
            max_depth=data.get("max_depth", 5),
            timeout_ms=data.get("timeout_ms", 30000.0),
            trust_threshold=data.get("trust_threshold", "server_validated"),
        )

    # -- helpers -------------------------------------------------------------

    @property
    def effective_layers(self) -> list[str]:
        """Layers to check, defaulting to all known layers."""
        if self.layers_to_check is not None:
            return list(self.layers_to_check)
        return ["python", "template", "js", "css", "html", "sql", "orm"]
