"""
Package manifest for frontier_objectives — Ch47 Frontier algorithms and phase transitions.

This module provides the authoritative registry of exported symbols, standard
objective entries, phase-transition catalogue, and validation utilities for the
:mod:`jugeo.orchestration.frontier_objectives` sub-package.

It mirrors the manifest pattern used throughout the jugeo project (see, for
example, :mod:`jugeo.foundations.formal_core.manifest`) and is the canonical
entry-point for tooling that needs to discover or validate the contents of this
package without executing full algorithmic code.

Design goals
------------
1. **Self-describing** — :class:`FrontierObjectivesManifest` carries version,
   chapter reference, exported symbols, and a checksum so that automated
   dependency checkers can verify package integrity.

2. **Pre-populated registries** — :class:`ObjectiveRegistry` and
   :class:`PhaseTransitionCatalog` ship with sensible defaults derived from
   theory2.tex §47.2–47.3 so consumers can use them out-of-the-box.

3. **Validation** — :class:`ManifestValidator` and :class:`ManifestReport`
   provide structured, machine-readable validation output rather than raw
   exceptions, making them suitable for CI/CD pipelines.

4. **Module-level convenience** — :func:`build_manifest`,
   :func:`validate_manifest`, :func:`get_default_registry`, and
   :func:`get_default_catalog` offer zero-argument entry-points for the common
   case.

Chapter reference: theory2.tex Ch47 — Frontier objectives.

copilot
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Upstream imports — guarded for isolated testing
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier_objectives.models import (
        BudgetPolicy,
        ClosureGainEstimate,
        DiversityMetric,
        FrontierObjective,
        ObjectiveKind,
        PhaseKind,
    )
except Exception:
    # Allow the manifest to be imported in isolation (e.g. during testing or
    # when the models module is not yet installed).
    ObjectiveKind = None  # type: ignore[assignment,misc]
    BudgetPolicy = None  # type: ignore[assignment,misc]
    PhaseKind = None  # type: ignore[assignment,misc]
    FrontierObjective = None  # type: ignore[assignment,misc]
    ClosureGainEstimate = None  # type: ignore[assignment,misc]
    DiversityMetric = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Current version of the frontier_objectives package.
MANIFEST_VERSION: str = "1.0.0"

#: Theory chapter reference.
CHAPTER_REF: str = "Ch47"

#: All public symbols exported by this package.
EXPORTED_SYMBOLS: tuple[str, ...] = (
    # models.py — enumerations
    "ObjectiveKind",
    "BudgetPolicy",
    "PhaseKind",
    # models.py — frozen value objects
    "FrontierObjective",
    "PhaseTransitionModel",
    "ClosureGainEstimate",
    "DiversityMetric",
    "ObjectiveResult",
    # models.py — mutable dataclasses
    "FrontierBudgetModel",
    "ObjectiveSet",
    "ScoringState",
    # algorithms.py — result types
    "ClusteringResult",
    "BeamSearchResult",
    "PhaseDetectionResult",
    "ExpectedImprovementResult",
    "BudgetAllocationResult",
    # algorithms.py — functions
    "cluster_nodes",
    "beam_search",
    "detect_phase",
    "expected_improvement",
    "allocate_budget",
    # manifest.py — this module
    "FrontierObjectivesManifest",
    "ObjectiveEntry",
    "ObjectiveRegistry",
    "PhaseTransitionEntry",
    "PhaseTransitionCatalog",
    "ManifestValidator",
    "ManifestReport",
    "build_manifest",
    "validate_manifest",
    "get_default_registry",
    "get_default_catalog",
    "MANIFEST_VERSION",
    "CHAPTER_REF",
    "EXPORTED_SYMBOLS",
    "DEFAULT_MANIFEST",
)

# ---------------------------------------------------------------------------
# Standard objective definitions — mirrors theory2.tex §47.2
# ---------------------------------------------------------------------------

#: Canonical list of standard objective definitions shipped with this package.
_STANDARD_OBJECTIVE_DEFS: list[dict[str, Any]] = [
    {
        "name": "closure_gain",
        "kind": "CLOSURE_GAIN",
        "description": (
            "Measures the increase in logical closure achieved by a frontier move.  "
            "Higher is better (direction: maximize).  See theory2.tex §47.2.1."
        ),
        "default_weight": 1.0,
        "default_threshold": 0.5,
    },
    {
        "name": "stability",
        "kind": "STABILITY",
        "description": (
            "Measures how stable (low-variance) the frontier trajectory is over "
            "recent steps.  Higher is better (direction: maximize).  "
            "See theory2.tex §47.2.2."
        ),
        "default_weight": 0.8,
        "default_threshold": 0.6,
    },
    {
        "name": "diversity",
        "kind": "DIVERSITY",
        "description": (
            "Measures the structural diversity of frontier candidates.  "
            "Higher is better (direction: maximize).  "
            "See theory2.tex §47.3 (diversity maintainability theorem)."
        ),
        "default_weight": 0.6,
        "default_threshold": 0.4,
    },
    {
        "name": "cost",
        "kind": "COST",
        "description": (
            "Measures the computational or resource cost of frontier exploration.  "
            "Scored inversely — lower raw cost yields higher score.  "
            "See theory2.tex §47.4 (budget-allocation feasibility theorem)."
        ),
        "default_weight": 0.4,
        "default_threshold": 0.7,
    },
]

# ---------------------------------------------------------------------------
# Standard phase-transition definitions — mirrors theory2.tex §47.3
# ---------------------------------------------------------------------------

#: Canonical list of phase-transition definitions.
_STANDARD_TRANSITION_DEFS: list[dict[str, Any]] = [
    {
        "from_phase": "exploration",
        "to_phase": "exploitation",
        "trigger": "closure_gain_plateau",
        "description": (
            "Switch from broad exploration to focused exploitation when closure "
            "gain plateaus for a sustained period.  This is the canonical "
            "exploration-to-exploitation transition described in theory2.tex §47.3.1."
        ),
        "expected_gain_delta": 0.15,
    },
    {
        "from_phase": "exploitation",
        "to_phase": "exploration",
        "trigger": "diversity_drop",
        "description": (
            "Return to exploration when the population diversity falls below the "
            "threshold defined in theory2.tex §47.3.2, preventing premature "
            "convergence."
        ),
        "expected_gain_delta": -0.05,
    },
    {
        "from_phase": "exploration",
        "to_phase": "transition",
        "trigger": "budget_exhaustion",
        "description": (
            "Enter the transition phase when the exploration budget is nearly "
            "exhausted, allowing the system to gracefully wind down exploration "
            "before switching strategy."
        ),
        "expected_gain_delta": 0.0,
    },
    {
        "from_phase": "exploitation",
        "to_phase": "converged",
        "trigger": "objective_all_satisfied",
        "description": (
            "Mark the frontier as converged when all objectives in the active "
            ":class:`ObjectiveSet` are simultaneously satisfied.  "
            "See theory2.tex §47.3.3."
        ),
        "expected_gain_delta": 0.0,
    },
    {
        "from_phase": "exploration",
        "to_phase": "stalled",
        "trigger": "no_improvement_timeout",
        "description": (
            "Declare a stall when no closure-gain improvement has been recorded "
            "within the timeout window.  Requires external intervention to escape.  "
            "See theory2.tex §47.3.4."
        ),
        "expected_gain_delta": 0.0,
    },
    {
        "from_phase": "stalled",
        "to_phase": "exploration",
        "trigger": "perturbation_injected",
        "description": (
            "Resume exploration after a perturbation (e.g. random restart or "
            "diversity injection) breaks the stall.  "
            "See theory2.tex §47.3.4."
        ),
        "expected_gain_delta": 0.05,
    },
    {
        "from_phase": "transition",
        "to_phase": "exploitation",
        "trigger": "transition_complete",
        "description": (
            "Complete the transition phase and move into exploitation once the "
            "transition criteria have been met."
        ),
        "expected_gain_delta": 0.1,
    },
    {
        "from_phase": "transition",
        "to_phase": "exploration",
        "trigger": "transition_aborted",
        "description": (
            "Abort the transition and return to exploration if the transition "
            "conditions could not be met within the allowed window."
        ),
        "expected_gain_delta": -0.05,
    },
]


# ---------------------------------------------------------------------------
# Helper — compute a lightweight checksum for manifest integrity
# ---------------------------------------------------------------------------


def _compute_checksum(version: str, chapter_ref: str, symbols: tuple[str, ...]) -> str:
    """Return a short SHA-256-based checksum string for manifest integrity.

    Parameters
    ----------
    version:
        The manifest version string.
    chapter_ref:
        The chapter reference string.
    symbols:
        The exported symbol tuple.

    Returns
    -------
    str:
        First 16 hex characters of the SHA-256 digest.
    """
    raw = f"{version}:{chapter_ref}:{','.join(sorted(symbols))}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return digest[:16]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierObjectivesManifest:
    """Authoritative manifest for the frontier_objectives package.

    The manifest records version metadata, the chapter reference in
    theory2.tex, and the full set of exported public symbols.  It is used by
    tooling to verify that the installed package matches expectations.

    Parameters
    ----------
    version:
        Semantic version string (e.g. ``"1.0.0"``).
    chapter_ref:
        Theory chapter reference (e.g. ``"Ch47"``).
    module_name:
        Fully-qualified module name of the package.
    exported_symbols:
        Tuple of all public symbol names in the package.
    description:
        Human-readable description of the package.
    created_at:
        Unix timestamp when this manifest instance was created.
    checksum:
        Short integrity checksum derived from version + symbols.
    """

    version: str
    chapter_ref: str
    module_name: str
    exported_symbols: tuple[str, ...]
    description: str
    created_at: float
    checksum: str

    def validate(self) -> bool:
        """Return ``True`` if the manifest passes basic self-consistency checks.

        Checks performed:

        * ``version`` is non-empty.
        * ``chapter_ref`` is non-empty.
        * ``exported_symbols`` is non-empty.
        * The stored ``checksum`` matches the recomputed value.

        Returns
        -------
        bool:
            ``True`` if all checks pass.
        """
        if not self.version:
            return False
        if not self.chapter_ref:
            return False
        if not self.exported_symbols:
            return False
        expected = _compute_checksum(self.version, self.chapter_ref, self.exported_symbols)
        return self.checksum == expected

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "module_name": self.module_name,
            "exported_symbols": list(self.exported_symbols),
            "description": self.description,
            "created_at": self.created_at,
            "checksum": self.checksum,
        }

    def copilot_report(self) -> str:
        """Return a human-readable report suitable for logging or display.

        Returns
        -------
        str:
            A multi-line string summarising the manifest.
        """
        valid_str = "VALID" if self.validate() else "INVALID"
        lines = [
            f"FrontierObjectivesManifest [{valid_str}]",
            f"  version      : {self.version}",
            f"  chapter_ref  : {self.chapter_ref}",
            f"  module_name  : {self.module_name}",
            f"  symbol_count : {len(self.exported_symbols)}",
            f"  checksum     : {self.checksum}",
            f"  description  : {self.description}",
        ]
        return "\n".join(lines)

    @classmethod
    def build(cls, version: str = MANIFEST_VERSION) -> FrontierObjectivesManifest:
        """Construct a :class:`FrontierObjectivesManifest` from module constants.

        Parameters
        ----------
        version:
            Version string (defaults to :data:`MANIFEST_VERSION`).

        Returns
        -------
        FrontierObjectivesManifest:
            A fully populated, valid manifest.
        """
        checksum = _compute_checksum(version, CHAPTER_REF, EXPORTED_SYMBOLS)
        return cls(
            version=version,
            chapter_ref=CHAPTER_REF,
            module_name="jugeo.orchestration.frontier_objectives",
            exported_symbols=EXPORTED_SYMBOLS,
            description=(
                "Ch47 Frontier objectives — closure-gain optimisation, phase "
                "transitions, and budget allocation over the "
                "exploration–exploitation spectrum."
            ),
            created_at=time.time(),
            checksum=checksum,
        )


# ---------------------------------------------------------------------------
# Objective catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectiveEntry:
    """A registry entry describing a single named objective.

    Parameters
    ----------
    name:
        Canonical name of the objective (e.g. ``"closure_gain"``).
    kind:
        String name of the :class:`~jugeo.orchestration.frontier_objectives.models.ObjectiveKind`
        member (e.g. ``"CLOSURE_GAIN"``).
    description:
        Human-readable description including theory references.
    default_weight:
        Default weight to use when building a
        :class:`~jugeo.orchestration.frontier_objectives.models.FrontierObjective`.
    default_threshold:
        Default threshold to use when building a
        :class:`~jugeo.orchestration.frontier_objectives.models.FrontierObjective`.
    """

    name: str
    kind: str
    description: str
    default_weight: float
    default_threshold: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "default_weight": self.default_weight,
            "default_threshold": self.default_threshold,
        }

    def to_objective(self) -> FrontierObjective:
        """Build a :class:`~jugeo.orchestration.frontier_objectives.models.FrontierObjective` from this entry.

        Uses the factory class methods on
        :class:`~jugeo.orchestration.frontier_objectives.models.FrontierObjective`
        when available, falling back to direct construction.

        Returns
        -------
        FrontierObjective:
            A fully constructed objective instance.

        Raises
        ------
        RuntimeError:
            If the models module is not available (guarded import failed).
        """
        if FrontierObjective is None:
            raise RuntimeError(
                "FrontierObjective is not available — models import failed."
            )
        _factory_map: dict[str, Any] = {
            "CLOSURE_GAIN": FrontierObjective.make_closure_gain,
            "STABILITY": FrontierObjective.make_stability,
            "DIVERSITY": FrontierObjective.make_diversity,
            "COST": FrontierObjective.make_cost,
        }
        factory = _factory_map.get(self.kind)
        if factory is not None:
            return factory(
                weight=self.default_weight,
                threshold=self.default_threshold,
            )
        # Fallback for composite / unknown kinds
        kind_enum = ObjectiveKind[self.kind] if ObjectiveKind is not None else None
        return FrontierObjective(
            objective_id=str(uuid.uuid4()),
            name=self.name,
            weight=self.default_weight,
            kind=kind_enum,
            target_metric=self.name,
            threshold=self.default_threshold,
            direction="maximize",
        )


@dataclass(slots=True)
class ObjectiveRegistry:
    """Mutable registry mapping objective names to :class:`ObjectiveEntry` instances.

    Parameters
    ----------
    entries:
        Dictionary from objective name to :class:`ObjectiveEntry`.
    _lock:
        When ``True``, :meth:`register` raises :exc:`RuntimeError` to prevent
        further modifications.
    """

    entries: dict[str, ObjectiveEntry]
    _lock: bool = False

    def register(self, entry: ObjectiveEntry) -> None:
        """Add *entry* to the registry.

        Parameters
        ----------
        entry:
            The objective entry to register.

        Raises
        ------
        RuntimeError:
            If the registry is locked.
        """
        if self._lock:
            raise RuntimeError("ObjectiveRegistry is locked — no further registrations allowed.")
        self.entries[entry.name] = entry

    def get(self, name: str) -> ObjectiveEntry | None:
        """Return the :class:`ObjectiveEntry` for *name*, or ``None``.

        Parameters
        ----------
        name:
            The objective name to look up.

        Returns
        -------
        ObjectiveEntry | None:
            The matching entry, or ``None`` if not found.
        """
        return self.entries.get(name)

    def list_names(self) -> list[str]:
        """Return a sorted list of all registered objective names.

        Returns
        -------
        list[str]:
            Sorted names.
        """
        return sorted(self.entries.keys())

    def build_objective(self, name: str) -> FrontierObjective | None:
        """Build and return the :class:`FrontierObjective` for *name*.

        Parameters
        ----------
        name:
            The objective name to build.

        Returns
        -------
        FrontierObjective | None:
            The constructed objective, or ``None`` if *name* is not registered
            or the models module is unavailable.
        """
        entry = self.get(name)
        if entry is None:
            return None
        try:
            return entry.to_objective()
        except Exception:
            return None

    def build_all(self) -> list[FrontierObjective]:
        """Build and return all registered objectives.

        Entries for which construction fails are silently skipped.

        Returns
        -------
        list[FrontierObjective]:
            List of successfully constructed objectives.
        """
        results: list[FrontierObjective] = []
        for name in self.list_names():
            obj = self.build_objective(name)
            if obj is not None:
                results.append(obj)
        return results

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            Mapping from objective name to serialised entry.
        """
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    @classmethod
    def default(cls) -> ObjectiveRegistry:
        """Return a pre-populated registry with the standard objectives.

        Standard objectives are defined in :data:`_STANDARD_OBJECTIVE_DEFS`
        and correspond to theory2.tex §47.2.

        Returns
        -------
        ObjectiveRegistry:
            A registry containing the four standard objectives.
        """
        registry = cls(entries={})
        for defn in _STANDARD_OBJECTIVE_DEFS:
            entry = ObjectiveEntry(
                name=defn["name"],
                kind=defn["kind"],
                description=defn["description"],
                default_weight=defn["default_weight"],
                default_threshold=defn["default_threshold"],
            )
            registry.register(entry)
        return registry


# ---------------------------------------------------------------------------
# Phase-transition catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseTransitionEntry:
    """A catalogue entry describing a single phase transition.

    Parameters
    ----------
    transition_id:
        Unique identifier for this entry.
    from_phase:
        The originating phase.
    to_phase:
        The target phase.
    trigger:
        The event or condition that causes the transition.
    description:
        Human-readable description including theory references.
    expected_gain_delta:
        Expected change in closure-gain score caused by the transition
        (may be negative).
    """

    transition_id: str
    from_phase: str
    to_phase: str
    trigger: str
    description: str
    expected_gain_delta: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "transition_id": self.transition_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "trigger": self.trigger,
            "description": self.description,
            "expected_gain_delta": self.expected_gain_delta,
        }


@dataclass(slots=True)
class PhaseTransitionCatalog:
    """Mutable catalogue of :class:`PhaseTransitionEntry` instances.

    Parameters
    ----------
    entries:
        List of all registered phase-transition entries.
    """

    entries: list[PhaseTransitionEntry]

    def add(self, entry: PhaseTransitionEntry) -> None:
        """Append *entry* to the catalogue.

        Parameters
        ----------
        entry:
            The entry to add.
        """
        self.entries.append(entry)

    def get_by_trigger(self, trigger: str) -> list[PhaseTransitionEntry]:
        """Return all entries whose :attr:`~PhaseTransitionEntry.trigger` matches.

        Parameters
        ----------
        trigger:
            The trigger string to filter by.

        Returns
        -------
        list[PhaseTransitionEntry]:
            All matching entries (may be empty).
        """
        return [e for e in self.entries if e.trigger == trigger]

    def get_by_from_phase(self, phase: str) -> list[PhaseTransitionEntry]:
        """Return all entries originating from *phase*.

        Parameters
        ----------
        phase:
            The originating phase name to filter by.

        Returns
        -------
        list[PhaseTransitionEntry]:
            All matching entries (may be empty).
        """
        return [e for e in self.entries if e.from_phase == phase]

    def all_triggers(self) -> set[str]:
        """Return the set of all unique trigger strings in the catalogue.

        Returns
        -------
        set[str]:
            Unique trigger names.
        """
        return {e.trigger for e in self.entries}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            Mapping from transition ID to serialised entry.
        """
        return {e.transition_id: e.to_dict() for e in self.entries}

    @classmethod
    def default(cls) -> PhaseTransitionCatalog:
        """Return a pre-populated catalogue with the standard transitions.

        Standard transitions are defined in :data:`_STANDARD_TRANSITION_DEFS`
        and correspond to theory2.tex §47.3.

        Returns
        -------
        PhaseTransitionCatalog:
            A catalogue containing the eight standard transitions.
        """
        catalog = cls(entries=[])
        for defn in _STANDARD_TRANSITION_DEFS:
            entry = PhaseTransitionEntry(
                transition_id=str(uuid.uuid4()),
                from_phase=defn["from_phase"],
                to_phase=defn["to_phase"],
                trigger=defn["trigger"],
                description=defn["description"],
                expected_gain_delta=defn["expected_gain_delta"],
            )
            catalog.add(entry)
        return catalog


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ManifestValidator:
    """Validator for :class:`FrontierObjectivesManifest` instances.

    Performs a series of checks and returns structured error lists rather than
    raising exceptions immediately, making the validator suitable for CI/CD
    integration.
    """

    def validate(self, manifest: FrontierObjectivesManifest) -> list[str]:
        """Validate *manifest* and return a list of error strings.

        An empty list indicates a valid manifest.

        Parameters
        ----------
        manifest:
            The manifest to validate.

        Returns
        -------
        list[str]:
            List of human-readable error descriptions.  Empty means valid.
        """
        errors: list[str] = []

        if not manifest.version:
            errors.append("manifest.version must not be empty")

        if not manifest.chapter_ref:
            errors.append("manifest.chapter_ref must not be empty")

        if not manifest.module_name:
            errors.append("manifest.module_name must not be empty")

        if not manifest.exported_symbols:
            errors.append("manifest.exported_symbols must contain at least one symbol")

        if not manifest.description:
            errors.append("manifest.description must not be empty")

        if manifest.created_at <= 0:
            errors.append("manifest.created_at must be a positive Unix timestamp")

        expected_checksum = _compute_checksum(
            manifest.version,
            manifest.chapter_ref,
            manifest.exported_symbols,
        )
        if manifest.checksum != expected_checksum:
            errors.append(
                f"manifest.checksum mismatch: stored={manifest.checksum!r} "
                f"expected={expected_checksum!r}"
            )

        # Warn (as error) if version does not look like semver
        parts = manifest.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"manifest.version {manifest.version!r} does not follow semver (X.Y.Z)"
            )

        return errors

    def is_valid(self, manifest: FrontierObjectivesManifest) -> bool:
        """Return ``True`` if *manifest* has no validation errors.

        Parameters
        ----------
        manifest:
            The manifest to check.

        Returns
        -------
        bool:
            ``True`` when :meth:`validate` returns an empty list.
        """
        return len(self.validate(manifest)) == 0


@dataclass(frozen=True)
class ManifestReport:
    """Structured report produced by :func:`validate_manifest`.

    Parameters
    ----------
    manifest_version:
        The version string from the validated manifest.
    valid:
        Whether the manifest passed all validation checks.
    errors:
        Tuple of error strings (empty if valid).
    warnings:
        Tuple of non-fatal warning strings.
    symbol_count:
        Number of exported symbols declared in the manifest.
    generated_at:
        Unix timestamp when this report was generated.
    """

    manifest_version: str
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    symbol_count: int
    generated_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "manifest_version": self.manifest_version,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "symbol_count": self.symbol_count,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        """Return a concise one-line summary of the report.

        Returns
        -------
        str:
            Summary string including version, validity, error count, and
            symbol count.
        """
        status = "VALID" if self.valid else f"INVALID ({len(self.errors)} error(s))"
        return (
            f"ManifestReport v{self.manifest_version} [{status}] "
            f"symbols={self.symbol_count} warnings={len(self.warnings)}"
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def build_manifest(version: str = MANIFEST_VERSION) -> FrontierObjectivesManifest:
    """Build and return a :class:`FrontierObjectivesManifest`.

    This is the recommended zero-argument entry-point for obtaining the package
    manifest.

    Parameters
    ----------
    version:
        Manifest version string (defaults to :data:`MANIFEST_VERSION`).

    Returns
    -------
    FrontierObjectivesManifest:
        A freshly constructed, valid manifest.
    """
    return FrontierObjectivesManifest.build(version=version)


def validate_manifest(manifest: FrontierObjectivesManifest) -> ManifestReport:
    """Validate *manifest* and return a structured :class:`ManifestReport`.

    Parameters
    ----------
    manifest:
        The manifest instance to validate.

    Returns
    -------
    ManifestReport:
        A structured report with errors, warnings, and a validity flag.
    """
    validator = ManifestValidator()
    errors = validator.validate(manifest)

    # Generate advisory warnings (not failures)
    warnings: list[str] = []
    if len(manifest.exported_symbols) < 5:
        warnings.append("Fewer than 5 exported symbols — package may be incomplete.")
    if manifest.chapter_ref != CHAPTER_REF:
        warnings.append(
            f"chapter_ref {manifest.chapter_ref!r} differs from expected {CHAPTER_REF!r}."
        )

    return ManifestReport(
        manifest_version=manifest.version,
        valid=len(errors) == 0,
        errors=tuple(errors),
        warnings=tuple(warnings),
        symbol_count=len(manifest.exported_symbols),
        generated_at=time.time(),
    )


def get_default_registry() -> ObjectiveRegistry:
    """Return the default :class:`ObjectiveRegistry` pre-populated with standard objectives.

    Returns
    -------
    ObjectiveRegistry:
        A registry with the four standard frontier objectives.
    """
    return ObjectiveRegistry.default()


def get_default_catalog() -> PhaseTransitionCatalog:
    """Return the default :class:`PhaseTransitionCatalog` with standard transitions.

    Returns
    -------
    PhaseTransitionCatalog:
        A catalogue with the eight standard phase transitions.
    """
    return PhaseTransitionCatalog.default()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: The default manifest instance for this package.  Available at import time.
DEFAULT_MANIFEST: FrontierObjectivesManifest = FrontierObjectivesManifest.build()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "MANIFEST_VERSION",
    "CHAPTER_REF",
    "EXPORTED_SYMBOLS",
    # Manifest
    "FrontierObjectivesManifest",
    "DEFAULT_MANIFEST",
    # Objective catalogue
    "ObjectiveEntry",
    "ObjectiveRegistry",
    # Phase-transition catalogue
    "PhaseTransitionEntry",
    "PhaseTransitionCatalog",
    # Validation
    "ManifestValidator",
    "ManifestReport",
    # Convenience functions
    "build_manifest",
    "validate_manifest",
    "get_default_registry",
    "get_default_catalog",
]
