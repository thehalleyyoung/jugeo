"""Package manifest for jugeo.ideation.novelty_search – theory2.tex Ch57.

Declares the package capabilities, validates configuration, and provides
registry services for the novelty search subsystem.

Module layout::

    PackageCapability   – capability enumeration
    PackageManifest     – frozen manifest record
    ManifestValidator   – validates manifest consistency
    PackageRegistry     – multi-manifest registry
    CapabilityQuery     – declarative capability queries
    ManifestSerializer  – JSON round-trip for manifests
    ManifestDiagnostics – diagnostics and health checks

Theory background (Ch57):
    The novelty search subsystem implements the novelty-driven ideation loop
    from theory2.tex §57.  A *manifest* is a first-class declaration of what
    the package can do, which Python version it requires, how large a portfolio
    it can manage, and what diversity/budget constraints it operates under.
    Manifests are registered in a PackageRegistry so that orchestration layers
    can query capabilities at runtime without importing implementation code.

Usage example::

    from jugeo.ideation.novelty_search.manifest import (
        _DEFAULT_MANIFEST,
        PackageRegistry,
        CapabilityQuery,
        PackageCapability,
    )

    registry = PackageRegistry()
    registry.register(_DEFAULT_MANIFEST)

    query = CapabilityQuery(
        required=frozenset({PackageCapability.NOVELTY_SCORING}),
        preferred=frozenset({PackageCapability.DIVERSITY_OPTIMIZATION}),
        min_budget=50.0,
    )
    results = query.rank(registry.get_all())
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel, TrustAlgebra
from jugeo.ideation.ideas import Idea, IdeaPortfolio, GainProfile, ValidationPath, TrustStatus
from jugeo.ideation.novelty import NoveltyScore, TheoremPortfolio

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_VERSION = "1.0.0"
_PACKAGE_NAME = "jugeo.ideation.novelty_search"
_MIN_PYTHON_MAJOR = 3
_MIN_PYTHON_MINOR = 11
_DEFAULT_BUDGET = 100.0
_DEFAULT_DIVERSITY_WEIGHT = 0.5
_MAX_PORTFOLIO_SIZE = 10_000
_MIN_NOVELTY_THRESHOLD = 0.0
_MAX_NOVELTY_THRESHOLD = 1.0
_CAPABILITY_WEIGHTS: dict[str, float] = {
    "NOVELTY_SCORING": 0.30,
    "PORTFOLIO_COVERAGE": 0.25,
    "DISTANCE_METRICS": 0.20,
    "DIVERSITY_OPTIMIZATION": 0.15,
    "PURPOSE_ALIGNED_SEARCH": 0.10,
}

# ---------------------------------------------------------------------------
# Private helper utilities
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into the closed interval [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenize *text* into a frozenset of lower-case alphanumeric tokens (len > 1)."""
    return frozenset(t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1)


def _slugify(text: str) -> str:
    """Convert *text* to a URL-safe slug."""
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower().strip()).strip("-")


def _normalize(text: str) -> str:
    """Collapse internal whitespace and strip leading/trailing whitespace."""
    return " ".join(text.split()).strip()


def _semver_tuple(version: str) -> tuple[int, int, int] | None:
    """Parse a semver string into (major, minor, patch) or return None on failure."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# PackageCapability
# ---------------------------------------------------------------------------


class PackageCapability(str, Enum):
    """Enumeration of capabilities provided by the novelty_search package.

    Each capability corresponds to a well-defined algorithmic service:

    NOVELTY_SCORING
        Scoring the novelty of individual ideas relative to a portfolio using
        semantic distance, purpose conditioning, and feasibility estimates.

    PORTFOLIO_COVERAGE
        Measuring how uniformly a portfolio covers the target idea space and
        identifying under-explored regions (gap analysis).

    DISTANCE_METRICS
        Providing configurable distance metrics (semantic, structural,
        topological, hybrid) that can be composed and tuned per problem.

    DIVERSITY_OPTIMIZATION
        Running optimization loops that maximize pairwise diversity subject
        to budget and feasibility constraints.

    PURPOSE_ALIGNED_SEARCH
        Restricting novelty search to ideas that align with a declared
        research purpose vector, preventing semantic drift.
    """

    NOVELTY_SCORING = "novelty_scoring"
    PORTFOLIO_COVERAGE = "portfolio_coverage"
    DISTANCE_METRICS = "distance_metrics"
    DIVERSITY_OPTIMIZATION = "diversity_optimization"
    PURPOSE_ALIGNED_SEARCH = "purpose_aligned_search"

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def weight(self) -> float:
        """Return the importance weight of this capability (0–1).

        Weights are drawn from ``_CAPABILITY_WEIGHTS`` and sum to 1.0 across
        all capabilities.  They represent the relative contribution of each
        capability to the overall package value in a typical research workflow.
        """
        return _CAPABILITY_WEIGHTS.get(self.name, 0.0)

    def description(self) -> str:
        """Return a one-sentence human-readable description of this capability."""
        _descriptions: dict[str, str] = {
            "NOVELTY_SCORING": (
                "Scores novelty of ideas using semantic distance and purpose alignment."
            ),
            "PORTFOLIO_COVERAGE": (
                "Measures portfolio coverage density and identifies gap regions."
            ),
            "DISTANCE_METRICS": (
                "Provides configurable semantic, structural, and hybrid distance metrics."
            ),
            "DIVERSITY_OPTIMIZATION": (
                "Runs diversity-maximizing search subject to budget and feasibility constraints."
            ),
            "PURPOSE_ALIGNED_SEARCH": (
                "Filters novelty search results to align with a declared research purpose."
            ),
        }
        return _descriptions.get(self.name, f"Capability: {self.value}")

    def is_core(self) -> bool:
        """Return True if this capability is considered core (non-optional).

        Core capabilities are NOVELTY_SCORING and PORTFOLIO_COVERAGE.  A
        manifest that lacks these is considered incomplete and will not pass
        strict validation.
        """
        return self in (
            PackageCapability.NOVELTY_SCORING,
            PackageCapability.PORTFOLIO_COVERAGE,
        )


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageManifest:
    """Frozen declaration of a novelty_search package configuration.

    A PackageManifest captures every parameter that affects how the novelty
    search subsystem behaves: which capabilities are enabled, what budget is
    available, how large the portfolio may grow, and what novelty / diversity
    thresholds govern filtering.

    Instances are immutable (``frozen=True``) so they can safely be used as
    dictionary keys or stored in sets.  Use ``with_capability`` /
    ``without_capability`` to derive modified copies.

    Attributes
    ----------
    name:
        Dot-separated package name, e.g. ``"jugeo.ideation.novelty_search"``.
    version:
        Semantic version string, e.g. ``"1.0.0"``.
    description:
        Human-readable description of this manifest's purpose.
    capabilities:
        Frozenset of enabled ``PackageCapability`` values.
    min_python:
        Minimum Python version required as ``(major, minor)``.
    default_budget:
        Default computational budget for search runs (must be > 0).
    max_portfolio_size:
        Maximum number of ideas the portfolio may hold.
    novelty_threshold:
        Minimum novelty score for an idea to be considered (0–1).
    diversity_weight:
        Weighting between novelty and diversity in the objective (0–1).
    created_at:
        ISO-8601 UTC timestamp of manifest creation.
    manifest_id:
        Unique UUID for this manifest instance.
    """

    name: str
    version: str
    description: str
    capabilities: frozenset[PackageCapability]
    min_python: tuple[int, int]
    default_budget: float
    max_portfolio_size: int
    novelty_threshold: float
    diversity_weight: float
    created_at: str = field(default_factory=_now_iso)
    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        """Normalize and validate all fields after construction."""
        # Normalize text fields (can't use self.x = … on frozen dataclass,
        # so we use object.__setattr__)
        object.__setattr__(self, "name", _normalize(self.name))
        object.__setattr__(self, "version", _normalize(self.version))
        object.__setattr__(self, "description", _normalize(self.description))

        # Validate and clamp numeric fields
        if not (0.0 <= self.novelty_threshold <= 1.0):
            raise ValueError(
                f"novelty_threshold must be in [0, 1], got {self.novelty_threshold!r}"
            )
        if not (0.0 <= self.diversity_weight <= 1.0):
            raise ValueError(
                f"diversity_weight must be in [0, 1], got {self.diversity_weight!r}"
            )
        if self.default_budget <= 0.0:
            raise ValueError(
                f"default_budget must be positive, got {self.default_budget!r}"
            )
        if self.max_portfolio_size < 1:
            raise ValueError(
                f"max_portfolio_size must be >= 1, got {self.max_portfolio_size!r}"
            )

    # ------------------------------------------------------------------
    # Capability queries
    # ------------------------------------------------------------------

    def capability_weight(self) -> float:
        """Return the total weighted score of enabled capabilities.

        Computes the weighted sum over all enabled capabilities using the
        per-capability weight from ``PackageCapability.weight()``.  A fully
        capable manifest scores 1.0; a manifest with no capabilities scores 0.0.

        Returns
        -------
        float
            Value in [0.0, 1.0] representing the aggregate capability weight.
        """
        return sum(cap.weight() for cap in self.capabilities)

    def is_capable_of(self, cap: PackageCapability) -> bool:
        """Return True if *cap* is in this manifest's capability set."""
        return cap in self.capabilities

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a multi-line human-readable description of this manifest.

        The output includes the name, version, description, all enabled
        capabilities with their weights, budget/portfolio limits, and the
        novelty/diversity thresholds.
        """
        lines: list[str] = [
            f"Manifest: {self.name}  v{self.version}",
            f"  ID:          {self.manifest_id}",
            f"  Created:     {self.created_at}",
            f"  Description: {self.description}",
            f"  Python req:  >= {self.min_python[0]}.{self.min_python[1]}",
            f"  Budget:      {self.default_budget:.2f}",
            f"  Max pool:    {self.max_portfolio_size:,}",
            f"  Novelty thr: {self.novelty_threshold:.3f}",
            f"  Diversity w: {self.diversity_weight:.3f}",
            f"  Capabilities ({len(self.capabilities)}):",
        ]
        for cap in sorted(self.capabilities, key=lambda c: c.name):
            core_tag = " [core]" if cap.is_core() else ""
            lines.append(
                f"    {cap.value:<32}  w={cap.weight():.2f}{core_tag}"
            )
        lines.append(f"  Aggregate weight: {self.capability_weight():.3f}")
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact single-line summary of this manifest."""
        cap_names = ", ".join(sorted(c.value for c in self.capabilities))
        return (
            f"{self.name} v{self.version} "
            f"[budget={self.default_budget:.0f}, "
            f"threshold={self.novelty_threshold:.2f}, "
            f"caps={cap_names}]"
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this manifest to a plain Python dict (JSON-compatible)."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": sorted(c.value for c in self.capabilities),
            "min_python": list(self.min_python),
            "default_budget": self.default_budget,
            "max_portfolio_size": self.max_portfolio_size,
            "novelty_threshold": self.novelty_threshold,
            "diversity_weight": self.diversity_weight,
            "created_at": self.created_at,
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PackageManifest":
        """Construct a PackageManifest from a plain Python dict.

        Parameters
        ----------
        d:
            Dictionary as returned by ``to_dict()``.

        Returns
        -------
        PackageManifest
            Reconstructed manifest.

        Raises
        ------
        KeyError
            If a required key is missing.
        ValueError
            If a value cannot be coerced to the expected type.
        """
        caps = frozenset(
            PackageCapability(v) for v in d.get("capabilities", [])
        )
        mp = d["min_python"]
        return cls(
            name=str(d["name"]),
            version=str(d["version"]),
            description=str(d["description"]),
            capabilities=caps,
            min_python=(int(mp[0]), int(mp[1])),
            default_budget=float(d["default_budget"]),
            max_portfolio_size=int(d["max_portfolio_size"]),
            novelty_threshold=float(d["novelty_threshold"]),
            diversity_weight=float(d["diversity_weight"]),
            created_at=str(d.get("created_at", _now_iso())),
            manifest_id=str(d.get("manifest_id", str(uuid.uuid4()))),
        )

    def to_json(self) -> str:
        """Serialize this manifest to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "PackageManifest":
        """Construct a PackageManifest from a JSON string."""
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty list = valid).

        Checks performed:
        - Name is non-empty
        - Version matches semver format
        - At least one capability is declared
        - Core capabilities (NOVELTY_SCORING, PORTFOLIO_COVERAGE) are present
        - Budget is positive and finite
        - Portfolio size is positive
        - Threshold values are in [0, 1]
        - Python version major >= 3
        """
        errors: list[str] = []

        if not self.name:
            errors.append("name must not be empty")

        if _semver_tuple(self.version) is None:
            errors.append(
                f"version {self.version!r} is not a valid semver string (expected X.Y.Z)"
            )

        if not self.capabilities:
            errors.append("capabilities must not be empty")

        missing_core = [
            cap.value
            for cap in (
                PackageCapability.NOVELTY_SCORING,
                PackageCapability.PORTFOLIO_COVERAGE,
            )
            if cap not in self.capabilities
        ]
        if missing_core:
            errors.append(
                f"missing core capabilities: {', '.join(missing_core)}"
            )

        if not (math.isfinite(self.default_budget) and self.default_budget > 0):
            errors.append(
                f"default_budget must be a positive finite number, got {self.default_budget}"
            )

        if self.max_portfolio_size < 1:
            errors.append(
                f"max_portfolio_size must be >= 1, got {self.max_portfolio_size}"
            )

        if not (0.0 <= self.novelty_threshold <= 1.0):
            errors.append(
                f"novelty_threshold must be in [0, 1], got {self.novelty_threshold}"
            )

        if not (0.0 <= self.diversity_weight <= 1.0):
            errors.append(
                f"diversity_weight must be in [0, 1], got {self.diversity_weight}"
            )

        if self.min_python[0] < 3:
            errors.append(
                f"min_python major version must be >= 3, got {self.min_python[0]}"
            )

        return errors

    # ------------------------------------------------------------------
    # Immutable update helpers
    # ------------------------------------------------------------------

    def with_capability(self, cap: PackageCapability) -> "PackageManifest":
        """Return a new manifest with *cap* added to the capability set."""
        return replace(self, capabilities=self.capabilities | {cap})

    def without_capability(self, cap: PackageCapability) -> "PackageManifest":
        """Return a new manifest with *cap* removed from the capability set."""
        return replace(self, capabilities=self.capabilities - {cap})


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates PackageManifest instances against a configurable rule set.

    In strict mode (the default) all core capabilities must be present and
    the version must be well-formed semver.  In lenient mode only fatal
    structural errors are reported.

    Parameters
    ----------
    strict:
        When True, missing core capabilities and non-semver versions are
        reported as errors.  When False, they are reported as warnings only.
    """

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    # ------------------------------------------------------------------
    # Sub-validators
    # ------------------------------------------------------------------

    def validate_version(self, version: str) -> list[str]:
        """Validate that *version* is a well-formed semver string.

        Returns
        -------
        list[str]
            Empty if valid; one-element list with error message otherwise.
        """
        if _semver_tuple(version) is None:
            msg = (
                f"Version {version!r} does not match semver format X.Y.Z."
            )
            return [msg]
        return []

    def validate_capabilities(
        self, caps: frozenset[PackageCapability]
    ) -> list[str]:
        """Validate that core capabilities are present in *caps*.

        Returns
        -------
        list[str]
            Error strings for any missing core capabilities.
        """
        errors: list[str] = []
        for cap in PackageCapability:
            if cap.is_core() and cap not in caps:
                errors.append(
                    f"Core capability {cap.value!r} is missing from the capability set."
                )
        if not caps:
            errors.append("At least one capability must be declared.")
        return errors

    def validate_budget(self, budget: float) -> list[str]:
        """Validate that *budget* is a positive finite number."""
        errors: list[str] = []
        if not math.isfinite(budget):
            errors.append(f"Budget must be finite, got {budget!r}.")
        elif budget <= 0:
            errors.append(f"Budget must be positive, got {budget!r}.")
        return errors

    def validate_thresholds(
        self, threshold: float, diversity: float
    ) -> list[str]:
        """Validate that *threshold* and *diversity* are both in [0, 1]."""
        errors: list[str] = []
        if not (0.0 <= threshold <= 1.0):
            errors.append(
                f"novelty_threshold must be in [0, 1], got {threshold!r}."
            )
        if not (0.0 <= diversity <= 1.0):
            errors.append(
                f"diversity_weight must be in [0, 1], got {diversity!r}."
            )
        return errors

    # ------------------------------------------------------------------
    # Aggregate validation
    # ------------------------------------------------------------------

    def validate(self, manifest: PackageManifest) -> list[str]:
        """Run all validation checks and return a list of error strings.

        Checks include: name non-empty, version semver, capabilities present,
        core capabilities (if strict), budget positive/finite, portfolio size,
        and threshold values in range.
        """
        errors: list[str] = []

        if not manifest.name.strip():
            errors.append("Manifest name must not be empty or whitespace-only.")

        if not manifest.description.strip():
            errors.append("Manifest description must not be empty.")

        if self.strict:
            errors.extend(self.validate_version(manifest.version))
            errors.extend(self.validate_capabilities(manifest.capabilities))
        else:
            # In lenient mode, still error on completely empty capabilities
            if not manifest.capabilities:
                errors.append("At least one capability must be declared.")

        errors.extend(self.validate_budget(manifest.default_budget))
        errors.extend(
            self.validate_thresholds(
                manifest.novelty_threshold, manifest.diversity_weight
            )
        )

        if manifest.max_portfolio_size < 1:
            errors.append(
                f"max_portfolio_size must be >= 1, got {manifest.max_portfolio_size}."
            )

        if manifest.min_python[0] < 3:
            errors.append(
                f"min_python major version must be >= 3, got {manifest.min_python[0]}."
            )

        return errors

    def is_valid(self, manifest: PackageManifest) -> bool:
        """Return True if the manifest passes all validation checks."""
        return len(self.validate(manifest)) == 0

    def assert_valid(self, manifest: PackageManifest) -> None:
        """Raise ValueError with all errors if the manifest is invalid.

        Raises
        ------
        ValueError
            Concatenated error messages separated by newlines.
        """
        errors = self.validate(manifest)
        if errors:
            raise ValueError(
                f"Manifest {manifest.name!r} failed validation:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    def diagnostics(self, manifest: PackageManifest) -> dict[str, Any]:
        """Return a structured diagnostic report for *manifest*.

        The report includes:
        - ``"errors"``: list of error strings from full validation
        - ``"warnings"``: list of advisory strings (non-fatal)
        - ``"score"``: float in [0, 1] representing validation health
        - ``"capability_weight"``: total capability weight
        - ``"is_valid"``: boolean shorthand

        The score is computed as ``1 - (error_count / total_checks)`` where
        total_checks is the number of individual checks performed.
        """
        errors = self.validate(manifest)
        warnings: list[str] = []

        # Advisory warnings (never errors)
        if manifest.default_budget < 10.0:
            warnings.append(
                f"Budget {manifest.default_budget:.1f} is very low; "
                "consider increasing to at least 10.0."
            )
        if manifest.novelty_threshold < 0.1:
            warnings.append(
                "novelty_threshold < 0.1 may admit very low-novelty ideas."
            )
        if manifest.max_portfolio_size > 100_000:
            warnings.append(
                f"max_portfolio_size {manifest.max_portfolio_size:,} is very large; "
                "memory usage may be significant."
            )
        if not manifest.description:
            warnings.append("No description provided.")

        total_checks = 8  # rough count of distinct checks
        score = _clamp(1.0 - len(errors) / max(total_checks, 1))

        return {
            "errors": errors,
            "warnings": warnings,
            "score": score,
            "capability_weight": manifest.capability_weight(),
            "is_valid": len(errors) == 0,
        }


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------


class PackageRegistry:
    """Multi-manifest registry providing lookup and query services.

    Manifests are keyed by their ``name`` field.  Registering a second
    manifest with the same name replaces the first (and records the
    replacement in the history log).

    Attributes
    ----------
    _manifests:
        Internal dict mapping package name → PackageManifest.
    _history:
        Ordered list of (name, action, timestamp) tuples recording every
        ``register`` and ``unregister`` call.
    """

    def __init__(self) -> None:
        self._manifests: dict[str, PackageManifest] = {}
        self._history: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, manifest: PackageManifest) -> None:
        """Add or replace a manifest in the registry.

        Parameters
        ----------
        manifest:
            The manifest to register.  If a manifest with the same ``name``
            already exists, it is replaced and the action is recorded as
            ``"replace"`` in the history.
        """
        action = "replace" if manifest.name in self._manifests else "register"
        self._manifests[manifest.name] = manifest
        self._history.append((manifest.name, action, _now_iso()))

    def unregister(self, name: str) -> bool:
        """Remove the manifest with the given *name* from the registry.

        Parameters
        ----------
        name:
            The package name to remove.

        Returns
        -------
        bool
            True if the manifest was found and removed; False otherwise.
        """
        if name in self._manifests:
            del self._manifests[name]
            self._history.append((name, "unregister", _now_iso()))
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> PackageManifest | None:
        """Return the manifest for *name*, or None if not registered."""
        return self._manifests.get(name)

    def get_all(self) -> list[PackageManifest]:
        """Return all registered manifests, sorted by name."""
        return sorted(self._manifests.values(), key=lambda m: m.name)

    def find_by_capability(
        self, cap: PackageCapability
    ) -> list[PackageManifest]:
        """Return manifests that include the given capability."""
        return [m for m in self.get_all() if m.is_capable_of(cap)]

    def find_by_version(self, version: str) -> list[PackageManifest]:
        """Return manifests with an exact version match."""
        return [m for m in self.get_all() if m.version == version]

    def find_compatible(self, min_budget: float) -> list[PackageManifest]:
        """Return manifests whose default_budget >= *min_budget*."""
        return [m for m in self.get_all() if m.default_budget >= min_budget]

    def capability_coverage(self) -> dict[PackageCapability, int]:
        """Return a count of how many manifests support each capability."""
        counts: dict[PackageCapability, int] = {
            cap: 0 for cap in PackageCapability
        }
        for manifest in self._manifests.values():
            for cap in manifest.capabilities:
                counts[cap] = counts.get(cap, 0) + 1
        return counts

    def total_weight(self) -> float:
        """Return the sum of capability weights across all registered manifests."""
        return sum(m.capability_weight() for m in self._manifests.values())

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of the registry state."""
        lines = [
            f"PackageRegistry ({len(self._manifests)} manifest(s)):",
        ]
        if not self._manifests:
            lines.append("  (empty)")
        else:
            for name, manifest in sorted(self._manifests.items()):
                lines.append(
                    f"  {name:<50} v{manifest.version}  "
                    f"weight={manifest.capability_weight():.2f}  "
                    f"budget={manifest.default_budget:.0f}"
                )
        lines.append(f"  Total weight: {self.total_weight():.3f}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the registry to a plain Python dict."""
        return {
            "manifests": [m.to_dict() for m in self.get_all()],
            "history": [
                {"name": h[0], "action": h[1], "timestamp": h[2]}
                for h in self._history
            ],
        }

    def history(self) -> list[tuple[str, str, str]]:
        """Return the full registration history as a list of (name, action, timestamp)."""
        return list(self._history)


# ---------------------------------------------------------------------------
# CapabilityQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityQuery:
    """Declarative query for filtering and ranking manifests by capabilities.

    A CapabilityQuery expresses what capabilities are *required* (hard
    filter) and which are *preferred* (soft ranking), along with budget and
    weight lower-bounds.

    Attributes
    ----------
    required:
        Capabilities that a manifest must have to pass the ``matches`` check.
    preferred:
        Additional capabilities that boost the ``score`` of a manifest.
    min_budget:
        Minimum ``default_budget`` required.
    min_weight:
        Minimum ``capability_weight()`` required.
    """

    required: frozenset[PackageCapability]
    preferred: frozenset[PackageCapability] = field(default_factory=frozenset)
    min_budget: float = 0.0
    min_weight: float = 0.0

    def matches(self, manifest: PackageManifest) -> bool:
        """Return True if *manifest* satisfies all hard constraints.

        A manifest matches if:
        - It has all required capabilities.
        - Its default_budget >= min_budget.
        - Its capability_weight() >= min_weight.
        """
        if not self.required.issubset(manifest.capabilities):
            return False
        if manifest.default_budget < self.min_budget:
            return False
        if manifest.capability_weight() < self.min_weight:
            return False
        return True

    def score(self, manifest: PackageManifest) -> float:
        """Return a match score in [0, 1] for *manifest* against this query.

        The score combines:
        - A binary term for required capabilities (0 or 1).
        - A soft term for preferred capabilities (fraction present).
        - A normalized budget term.
        - The manifest's aggregate capability weight.

        Only manifests that pass ``matches`` should be expected to score > 0;
        non-matching manifests receive 0.
        """
        if not self.matches(manifest):
            return 0.0

        # Preferred capability coverage
        if self.preferred:
            pref_score = len(
                self.preferred & manifest.capabilities
            ) / len(self.preferred)
        else:
            pref_score = 1.0

        # Capability weight (already in [0, 1])
        weight_score = manifest.capability_weight()

        # Combine equally weighted sub-scores
        return _clamp(0.5 * pref_score + 0.5 * weight_score)

    def filter(
        self, manifests: Iterable[PackageManifest]
    ) -> list[PackageManifest]:
        """Return only the manifests that pass ``matches``."""
        return [m for m in manifests if self.matches(m)]

    def rank(
        self, manifests: Iterable[PackageManifest]
    ) -> list[PackageManifest]:
        """Return matching manifests sorted by score descending."""
        matching = self.filter(manifests)
        return sorted(matching, key=lambda m: self.score(m), reverse=True)

    def explain(self, manifest: PackageManifest) -> str:
        """Return a human-readable explanation of why *manifest* matches or not.

        The explanation lists which required capabilities are met or missing,
        which preferred capabilities are present, and the budget/weight check.
        """
        lines = [f"Query explanation for {manifest.name!r}:"]

        # Required
        for cap in sorted(self.required, key=lambda c: c.name):
            ok = cap in manifest.capabilities
            tag = "✓" if ok else "✗"
            lines.append(f"  {tag} required: {cap.value}")

        # Preferred
        for cap in sorted(self.preferred, key=lambda c: c.name):
            ok = cap in manifest.capabilities
            tag = "✓" if ok else "·"
            lines.append(f"  {tag} preferred: {cap.value}")

        # Budget
        budget_ok = manifest.default_budget >= self.min_budget
        lines.append(
            f"  {'✓' if budget_ok else '✗'} budget: "
            f"{manifest.default_budget:.1f} >= {self.min_budget:.1f}"
        )

        # Weight
        weight_ok = manifest.capability_weight() >= self.min_weight
        lines.append(
            f"  {'✓' if weight_ok else '✗'} weight: "
            f"{manifest.capability_weight():.3f} >= {self.min_weight:.3f}"
        )

        lines.append(
            f"  → matches={self.matches(manifest)}, "
            f"score={self.score(manifest):.4f}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------


class ManifestSerializer:
    """JSON round-trip serializer for PackageManifest and PackageRegistry.

    All methods produce and accept UTF-8 JSON strings.  The serialized format
    is the same as ``PackageManifest.to_dict()`` / ``PackageManifest.from_dict()``.

    This class is intentionally stateless; instantiate once and reuse freely.
    """

    def serialize(self, manifest: PackageManifest) -> str:
        """Serialize a single manifest to a JSON string.

        Parameters
        ----------
        manifest:
            The manifest to serialize.

        Returns
        -------
        str
            A pretty-printed JSON string (indent=2, sorted keys).
        """
        return manifest.to_json()

    def deserialize(self, data: str) -> PackageManifest:
        """Deserialize a JSON string to a PackageManifest.

        Parameters
        ----------
        data:
            JSON string produced by ``serialize`` or ``PackageManifest.to_json``.

        Returns
        -------
        PackageManifest
            Reconstructed manifest.

        Raises
        ------
        json.JSONDecodeError
            If *data* is not valid JSON.
        KeyError / ValueError
            If required fields are missing or have invalid values.
        """
        return PackageManifest.from_json(data)

    def serialize_registry(self, registry: PackageRegistry) -> str:
        """Serialize an entire registry to a JSON string."""
        return json.dumps(registry.to_dict(), indent=2, sort_keys=True)

    def deserialize_registry(self, data: str) -> PackageRegistry:
        """Deserialize a JSON string to a PackageRegistry.

        Parameters
        ----------
        data:
            JSON string produced by ``serialize_registry``.

        Returns
        -------
        PackageRegistry
            Reconstructed registry with all manifests registered (history is
            not restored, as it would duplicate registration events).
        """
        raw = json.loads(data)
        registry = PackageRegistry()
        for manifest_dict in raw.get("manifests", []):
            registry.register(PackageManifest.from_dict(manifest_dict))
        return registry

    def serialize_list(self, manifests: Sequence[PackageManifest]) -> str:
        """Serialize a list of manifests to a JSON string."""
        return json.dumps(
            [m.to_dict() for m in manifests], indent=2, sort_keys=True
        )

    def deserialize_list(self, data: str) -> list[PackageManifest]:
        """Deserialize a JSON string to a list of PackageManifest objects."""
        raw = json.loads(data)
        return [PackageManifest.from_dict(d) for d in raw]


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


class ManifestDiagnostics:
    """Diagnostics and health-check services for a PackageRegistry.

    Performs structural analysis of the registry: duplicate detection,
    orphaned manifests (missing required capabilities), capability gap
    analysis, and a narrative health report suitable for display to users or
    copilot agents.

    Parameters
    ----------
    registry:
        The registry to analyse.  The registry is queried lazily, so changes
        made after construction are reflected in subsequent method calls.
    """

    def __init__(self, registry: PackageRegistry) -> None:
        self._registry = registry
        self._validator = ManifestValidator(strict=True)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def check_health(self) -> dict[str, Any]:
        """Return a comprehensive health-check report for the registry.

        The report dictionary contains:

        ``"healthy"``
            True if there are no errors.
        ``"issues"``
            List of error strings.
        ``"warnings"``
            List of advisory strings.
        ``"manifest_count"``
            Number of registered manifests.
        ``"capability_coverage"``
            Dict mapping capability name → manifest count.
        ``"total_weight"``
            Aggregate capability weight across all manifests.
        ``"orphaned_count"``
            Number of orphaned manifests.
        ``"gap_capabilities"``
            List of capability values not covered by any manifest.
        """
        issues: list[str] = []
        warnings: list[str] = []

        manifests = self._registry.get_all()

        if not manifests:
            warnings.append("Registry is empty – no manifests registered.")

        # Per-manifest validation
        for m in manifests:
            errs = self._validator.validate(m)
            for err in errs:
                issues.append(f"[{m.name}] {err}")

        # Orphaned
        orphaned = self.find_orphaned()
        if orphaned:
            issues.append(
                f"{len(orphaned)} orphaned manifest(s) missing core capabilities: "
                + ", ".join(m.name for m in orphaned)
            )

        # Gaps
        gaps = self.capability_gaps()
        if gaps:
            warnings.append(
                f"Capability gap(s) not covered by any manifest: "
                + ", ".join(c.value for c in gaps)
            )

        # Coverage
        cov = self._registry.capability_coverage()
        cov_named = {c.value: n for c, n in cov.items()}

        return {
            "healthy": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "manifest_count": len(manifests),
            "capability_coverage": cov_named,
            "total_weight": self._registry.total_weight(),
            "orphaned_count": len(orphaned),
            "gap_capabilities": [c.value for c in gaps],
        }

    # ------------------------------------------------------------------
    # Structural analysis
    # ------------------------------------------------------------------

    def find_duplicates(self) -> list[tuple[str, str]]:
        """Return pairs of manifest names that share the same package name.

        Since the registry keyed by name prevents exact name duplicates, this
        method checks for *logically* duplicate names (same slug after
        normalization and slugification).

        Returns
        -------
        list[tuple[str, str]]
            List of (name_a, name_b) pairs that look like duplicates.
        """
        by_slug: dict[str, list[str]] = defaultdict(list)
        for m in self._registry.get_all():
            slug = _slugify(m.name)
            by_slug[slug].append(m.name)

        duplicates: list[tuple[str, str]] = []
        for names in by_slug.values():
            if len(names) >= 2:
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        duplicates.append((names[i], names[j]))
        return duplicates

    def find_orphaned(self) -> list[PackageManifest]:
        """Return manifests that are missing at least one core capability.

        A manifest is considered *orphaned* if it lacks either
        NOVELTY_SCORING or PORTFOLIO_COVERAGE.  Such manifests cannot
        participate in the full novelty search pipeline.
        """
        return [
            m
            for m in self._registry.get_all()
            if not all(
                m.is_capable_of(cap)
                for cap in PackageCapability
                if cap.is_core()
            )
        ]

    def capability_gaps(self) -> list[PackageCapability]:
        """Return capabilities that are not covered by any registered manifest.

        Returns
        -------
        list[PackageCapability]
            Capabilities absent from all registered manifests.
        """
        cov = self._registry.capability_coverage()
        return [cap for cap, count in cov.items() if count == 0]

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def generate_report(self) -> str:
        """Generate a multi-line text report of registry health.

        The report covers:
        - Registry overview (manifest count, total weight)
        - Per-manifest validation summary
        - Capability coverage table
        - Orphaned manifests
        - Capability gaps
        - Overall health verdict
        """
        health = self.check_health()
        lines: list[str] = [
            "=" * 60,
            "  ManifestDiagnostics Report",
            "=" * 60,
            f"  Manifests registered : {health['manifest_count']}",
            f"  Total weight         : {health['total_weight']:.3f}",
            f"  Orphaned manifests   : {health['orphaned_count']}",
            "",
            "  Capability Coverage:",
        ]
        for cap_name, count in sorted(health["capability_coverage"].items()):
            bar = "█" * count + "░" * max(0, 5 - count)
            lines.append(f"    {cap_name:<35} {bar} ({count})")

        if health["gap_capabilities"]:
            lines.append("")
            lines.append("  ⚠  Capability Gaps:")
            for g in health["gap_capabilities"]:
                lines.append(f"    - {g}")

        if health["issues"]:
            lines.append("")
            lines.append("  ✗  Issues:")
            for issue in health["issues"]:
                lines.append(f"    - {issue}")

        if health["warnings"]:
            lines.append("")
            lines.append("  ⚠  Warnings:")
            for warn in health["warnings"]:
                lines.append(f"    - {warn}")

        lines.append("")
        verdict = "HEALTHY ✓" if health["healthy"] else "UNHEALTHY ✗"
        lines.append(f"  Overall: {verdict}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def copilot_summary(self) -> str:
        """Return a concise one-paragraph summary for copilot agents.

        The summary is intended to be injected into a copilot context window
        to give the agent a quick overview of the registry state without
        overwhelming detail.
        """
        health = self.check_health()
        n = health["manifest_count"]
        w = health["total_weight"]
        gaps = health["gap_capabilities"]
        issues = health["issues"]

        gap_text = (
            f"Capability gaps exist for: {', '.join(gaps)}. "
            if gaps
            else "All capabilities are covered. "
        )
        issue_text = (
            f"{len(issues)} validation issue(s) detected. "
            if issues
            else "No validation issues. "
        )
        health_text = "Registry is healthy." if health["healthy"] else "Registry requires attention."

        return (
            f"The novelty_search package registry contains {n} manifest(s) "
            f"with a total capability weight of {w:.3f}. "
            f"{gap_text}"
            f"{issue_text}"
            f"{health_text}"
        )


# ---------------------------------------------------------------------------
# Module-level default manifest
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST = PackageManifest(
    name=_PACKAGE_NAME,
    version=_VERSION,
    description=(
        "Novelty search algorithms for jugeo ideation with "
        "purpose-aligned diversity maximization."
    ),
    capabilities=frozenset(PackageCapability),
    min_python=(_MIN_PYTHON_MAJOR, _MIN_PYTHON_MINOR),
    default_budget=_DEFAULT_BUDGET,
    max_portfolio_size=_MAX_PORTFOLIO_SIZE,
    novelty_threshold=0.3,
    diversity_weight=_DEFAULT_DIVERSITY_WEIGHT,
)
"""The default PackageManifest for the jugeo.ideation.novelty_search package.

This manifest declares all five capabilities enabled, uses the module-level
defaults for budget (100.0), portfolio size (10 000), novelty threshold (0.3),
and diversity weight (0.5).  It is provided as a convenience so that callers
can register a fully-functional manifest without supplying every parameter.

Example::

    registry = PackageRegistry()
    registry.register(_DEFAULT_MANIFEST)
    assert registry.get(_PACKAGE_NAME) is not None
"""
