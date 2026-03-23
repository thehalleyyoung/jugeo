r"""Package manifest for the ``descent_locality`` foundations module.

This module is the canonical manifest for
``jugeo.foundations.descent_locality``, the Python implementation companion
to Theory2.tex Chapter 4: *Locality, Transport, Gluing, and Obstruction*.

Theory2.tex Ch4 is the geometric heart of JuGeo.  It formalises the
*locality principle* — that global semantic objects are assembled from local
data by explicit transport and gluing laws — and the *descent obstruction* —
the cohomological witness that records exactly where and why gluing fails.

The manifest here records:

* the capabilities this sub-package exposes (covers, descent, gluing,
  obstructions, local-to-global, sheaf axioms, cohomology);
* the theorem targets it must discharge (sheaf condition, descent criterion,
  H¹ obstruction class uniqueness, etc.);
* the explicit dependency order on adjacent foundations sub-packages; and
* a machine-readable provenance record anchoring all claims to their
  authoritative theory source.

Manifest responsibilities
--------------------------

:data:`MANIFEST_PROVENANCE`
    Frozen ``MappingProxyType`` describing the authoritative theory source,
    structural role, and file targets.

:data:`SUBSYSTEM_CAPABILITIES`
    Tuple of short capability identifiers claimed by the sub-package.

:data:`THEOREM_TARGETS`
    Tuple of theorem-target strings this module must support.

:data:`DEPENDENCY_ORDER`
    Canonical dependency order for the sub-package.

:class:`CapabilityStatus`
    Lifecycle status of a declared capability.

:class:`CapabilityRecord`
    Structured record for a single declared capability.

:class:`DependencyRecord`
    Structured record for a single dependency declaration.

:class:`PackageManifest`
    Root manifest dataclass: validates the record, exposes projection
    helpers, and can emit a JSON report.

:class:`ManifestValidator`
    Stateful validator that cross-checks capability and theorem coverage.

:class:`SubsystemManifest`
    Compact, frozen declaration of the sub-package as a single semantic unit.

:data:`DESCENT_LOCALITY_MANIFEST`
    Module-level :class:`SubsystemManifest` instance — the canonical export.

copilot: shared-core marker
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Module-level provenance constant
# ---------------------------------------------------------------------------

MANIFEST_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": "preliminaries/theory2.tex",
        "semantic_source_chapter": "4",
        "semantic_source_role": "authoritative-semantic-source",
        "structural_blueprint": "theory2-src-blueprint.json",
        "structural_generation_order": "theory2-generation-order.json",
        "structural_hint_role": "structure-only",
        "target_package": "jugeo.foundations.descent_locality",
        "target_manifest": "src/jugeo/foundations/descent_locality/manifest.py",
        "target_models": "src/jugeo/foundations/descent_locality/models.py",
        "stage": "foundations-descent-locality",
        "sequence": 4,
        "copilot_channel": "shared-core",
    }
)

# ---------------------------------------------------------------------------
# Capability and theorem constants
# ---------------------------------------------------------------------------

SUBSYSTEM_CAPABILITIES: Final[tuple[str, ...]] = (
    "covers",
    "descent",
    "gluing",
    "obstructions",
    "local-to-global",
    "sheaf-axioms",
    "cohomology",
    "transport",
)

THEOREM_TARGETS: Final[tuple[str, ...]] = (
    "sheaf-condition",
    "descent-criterion",
    "H1-obstruction-class-uniqueness",
    "locality-principle-soundness",
    "gluing-completeness",
    "transport-coherence",
    "cover-refinement-monotonicity",
    "obstruction-triviality-characterisation",
    "local-to-global-faithfulness",
    "cocycle-boundary-exactness",
)

DEPENDENCY_ORDER: Final[tuple[str, ...]] = (
    "jugeo.geometry.site",
    "jugeo.geometry.covers",
    "jugeo.geometry.descent",
    "jugeo.foundations.formal_core",
    "jugeo.foundations.type_objects",
    "jugeo.foundations.judgment_products",
)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CapabilityStatus(Enum):
    """Lifecycle status of a declared capability.

    ``STABLE`` capabilities are covered by theorem targets and may be used
    by dependents without risk of breaking change.  ``PROVISIONAL`` capabilities
    are implemented but not yet covered by formal targets — they may change.
    ``EXPERIMENTAL`` capabilities are exploratory scaffolding; callers must not
    rely on them outside internal tests.

    copilot: shared-core marker
    """

    STABLE = "stable"
    PROVISIONAL = "provisional"
    EXPERIMENTAL = "experimental"

    @property
    def ordinal(self) -> int:
        """Integer rank for status comparison; higher is more stable."""
        return {"experimental": 0, "provisional": 1, "stable": 2}[self.value]

    def is_production_ready(self) -> bool:
        """Return True only if this capability has reached STABLE status."""
        return self == CapabilityStatus.STABLE

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CapabilityStatus):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CapabilityStatus):
            return NotImplemented
        return self.ordinal <= other.ordinal


# ---------------------------------------------------------------------------
# CapabilityRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """Structured record for a single declared capability.

    A capability record pairs a short name with a human-readable description,
    a :class:`CapabilityStatus`, and the theorem targets that discharge it.
    Theorem targets are the mechanism by which ``PROVISIONAL`` capabilities
    graduate to ``STABLE``: all listed targets must appear in
    :data:`THEOREM_TARGETS` and be marked as covered.

    Parameters
    ----------
    name:
        Short, lower-case, hyphen-separated capability identifier matching
        an entry in :data:`SUBSYSTEM_CAPABILITIES`.
    description:
        One-sentence prose description of what this capability provides.
    status:
        Lifecycle status from :class:`CapabilityStatus`.
    theorem_support:
        Tuple of theorem-target strings that ground this capability.  An
        empty tuple is acceptable for EXPERIMENTAL capabilities only.

    copilot: shared-core marker
    """

    name: str
    description: str
    status: CapabilityStatus
    theorem_support: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("CapabilityRecord.name must not be empty.")
        if not self.description:
            raise ValueError("CapabilityRecord.description must not be empty.")
        if self.status == CapabilityStatus.STABLE and not self.theorem_support:
            raise ValueError(
                f"STABLE capability {self.name!r} must list at least one theorem target."
            )

    def is_grounded(self) -> bool:
        """Return True when at least one theorem target backs this capability."""
        return len(self.theorem_support) > 0

    def covers_theorem(self, theorem: str) -> bool:
        """Return True when *theorem* appears in this capability's support set."""
        return theorem in self.theorem_support

    def grade_up(self, new_status: CapabilityStatus) -> CapabilityRecord:
        """Return a new record with *new_status*, preserving all other fields.

        Raises ``ValueError`` when trying to grade *down* (reduce status),
        preventing accidental capability regression.
        """
        if new_status < self.status:
            raise ValueError(
                f"Cannot grade capability {self.name!r} down from "
                f"{self.status.value!r} to {new_status.value!r}."
            )
        return CapabilityRecord(
            name=self.name,
            description=self.description,
            status=new_status,
            theorem_support=self.theorem_support,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "theorem_support": list(self.theorem_support),
            "is_grounded": self.is_grounded(),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        grounded = "grounded" if self.is_grounded() else "ungrounded"
        return (
            f"[{self.status.value.upper()}|{grounded}] {self.name}: {self.description}"
        )


# ---------------------------------------------------------------------------
# DependencyRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    """Structured record for a single declared dependency.

    Dependency records are explicit declarations rather than inferred import
    relations.  Each record names the dependency, states a version constraint
    (or ``"any"``), indicates whether it is required or optional, and gives
    a brief justification anchored to the theory.

    Parameters
    ----------
    name:
        Dotted Python module path of the dependency.
    version_constraint:
        PEP-440-style version constraint string or the literal ``"any"`` to
        indicate no constraint.
    required:
        Whether this dependency is required for the sub-package to function.
        Optional dependencies may be absent and the sub-package still
        initialises, but with reduced capability.
    justification:
        One-sentence explanation of why this dependency is needed, anchored
        to a Theory2.tex concept if possible.

    copilot: shared-core marker
    """

    name: str
    version_constraint: str
    required: bool
    justification: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DependencyRecord.name must not be empty.")
        if not self.justification:
            raise ValueError("DependencyRecord.justification must not be empty.")
        if not self.name.startswith("jugeo"):
            raise ValueError(
                f"Dependency {self.name!r} must be within the jugeo namespace."
            )

    def is_satisfied_by(self, available_version: str) -> bool:
        """Return True when *available_version* satisfies the constraint.

        For ``"any"`` constraints, always returns True.  For constraints of
        the form ``">=X.Y"`` or ``"==X.Y"`` a simple prefix match is used;
        full PEP-440 parsing is intentionally deferred to setuptools.
        """
        if self.version_constraint == "any":
            return True
        if self.version_constraint.startswith(">="):
            minimum = self.version_constraint[2:].strip()
            return available_version >= minimum
        if self.version_constraint.startswith("=="):
            required = self.version_constraint[2:].strip()
            return available_version == required
        return False

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "name": self.name,
            "version_constraint": self.version_constraint,
            "required": self.required,
            "justification": self.justification,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        req = "required" if self.required else "optional"
        return f"[{req}] {self.name} ({self.version_constraint}): {self.justification}"


# ---------------------------------------------------------------------------
# SubsystemManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubsystemManifest:
    """Compact, frozen declaration of the ``descent_locality`` sub-package.

    ``SubsystemManifest`` is the primary export of this module and the
    identity record that other packages use when expressing a dependency on
    ``jugeo.foundations.descent_locality``.  It deliberately mirrors
    ``SubsystemManifest`` from the root ``package_manifest.py`` so that the
    registry can aggregate them uniformly.

    Parameters
    ----------
    subsystem_id:
        Dotted package path, e.g. ``"jugeo.foundations.descent_locality"``.
    capabilities:
        Tuple of :class:`CapabilityRecord` objects.
    theorems:
        Tuple of theorem-target strings.
    dependencies:
        Tuple of :class:`DependencyRecord` objects.
    copilot_channel:
        Identifier of the copilot channel governing proposals to this
        subsystem.  Defaults to ``"shared-core"``.

    copilot: shared-core marker
    """

    subsystem_id: str
    capabilities: tuple[CapabilityRecord, ...]
    theorems: tuple[str, ...]
    dependencies: tuple[DependencyRecord, ...]
    copilot_channel: str = "shared-core"

    def __post_init__(self) -> None:
        if not self.subsystem_id:
            raise ValueError("SubsystemManifest.subsystem_id must not be empty.")
        if not self.subsystem_id.startswith("jugeo"):
            raise ValueError("subsystem_id must be in the jugeo namespace.")
        if not self.capabilities:
            raise ValueError("SubsystemManifest must declare at least one capability.")
        if not self.theorems:
            raise ValueError("SubsystemManifest must declare at least one theorem target.")

    def capability_names(self) -> tuple[str, ...]:
        """Return the names of all declared capabilities."""
        return tuple(cap.name for cap in self.capabilities)

    def stable_capabilities(self) -> list[CapabilityRecord]:
        """Return only capabilities with STABLE status."""
        return [c for c in self.capabilities if c.status == CapabilityStatus.STABLE]

    def has_capability(self, name: str) -> bool:
        """Return True when a capability with *name* is declared."""
        return any(c.name == name for c in self.capabilities)

    def required_dependencies(self) -> list[DependencyRecord]:
        """Return only required dependency records."""
        return [d for d in self.dependencies if d.required]

    def theorem_count(self) -> int:
        """Return the number of declared theorem targets."""
        return len(self.theorems)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "subsystem_id": self.subsystem_id,
            "capabilities": [c.as_dict() for c in self.capabilities],
            "theorems": list(self.theorems),
            "dependencies": [d.as_dict() for d in self.dependencies],
            "copilot_channel": self.copilot_channel,
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary."""
        lines = [
            f"SubsystemManifest: {self.subsystem_id}",
            f"  copilot channel : {self.copilot_channel}",
            f"  capabilities    : {len(self.capabilities)} "
            f"({len(self.stable_capabilities())} stable)",
            f"  theorem targets : {self.theorem_count()}",
            f"  dependencies    : {len(self.dependencies)} "
            f"({len(self.required_dependencies())} required)",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Root manifest for the ``descent_locality`` foundations module.

    ``PackageManifest`` is a richer, mutable-during-construction record used
    by build tooling, CI gates, and documentation generators.  After
    construction it should be treated as logically immutable; all mutation
    helpers return new state and record changes in the ``_audit_log``.

    Parameters
    ----------
    subsystem_name:
        Human-readable name, e.g. ``"descent_locality"``.
    stage:
        Theory2.tex stage identifier, e.g. ``"foundations-descent-locality"``.
    capabilities:
        Sequence of :class:`CapabilityRecord` objects.
    theorem_targets:
        Sequence of theorem-target strings.
    dependency_order:
        Sequence of dependency module paths in resolution order.
    provenance:
        Mapping anchoring this manifest to its authoritative theory source.

    copilot: shared-core marker
    """

    subsystem_name: str
    stage: str
    capabilities: list[CapabilityRecord]
    theorem_targets: list[str]
    dependency_order: list[str]
    provenance: Mapping[str, Any]
    _audit_log: list[str] = field(default_factory=list, repr=False, compare=False)
    _created_at: float = field(default_factory=time.time, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.subsystem_name:
            raise ValueError("PackageManifest.subsystem_name must not be empty.")
        if not self.stage:
            raise ValueError("PackageManifest.stage must not be empty.")
        self._audit_log.append(
            f"[{time.time():.3f}] manifest initialised for {self.subsystem_name!r}"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the manifest and return a list of error messages.

        Validation rules:
        * Every STABLE capability must have at least one theorem target.
        * Every declared theorem target must appear in :data:`THEOREM_TARGETS`.
        * Every dependency must be within the jugeo namespace.
        * ``dependency_order`` must contain no duplicates.

        Returns an empty list when the manifest is valid.
        """
        errors: list[str] = []
        known_theorems = set(THEOREM_TARGETS)
        for cap in self.capabilities:
            if cap.status == CapabilityStatus.STABLE and not cap.theorem_support:
                errors.append(
                    f"STABLE capability {cap.name!r} has no theorem support."
                )
            for thm in cap.theorem_support:
                if thm not in known_theorems:
                    errors.append(
                        f"Capability {cap.name!r} references unknown theorem {thm!r}."
                    )
        for thm in self.theorem_targets:
            if thm not in known_theorems:
                errors.append(f"Theorem target {thm!r} not in THEOREM_TARGETS.")
        for dep in self.dependency_order:
            if not dep.startswith("jugeo"):
                errors.append(
                    f"Dependency {dep!r} is outside the jugeo namespace."
                )
        seen: set[str] = set()
        for dep in self.dependency_order:
            if dep in seen:
                errors.append(f"Duplicate dependency {dep!r} in dependency_order.")
            seen.add(dep)
        self._audit_log.append(
            f"[{time.time():.3f}] validate() returned {len(errors)} error(s)"
        )
        return errors

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "subsystem_name": self.subsystem_name,
            "stage": self.stage,
            "capabilities": [c.as_dict() for c in self.capabilities],
            "theorem_targets": list(self.theorem_targets),
            "dependency_order": list(self.dependency_order),
            "provenance": dict(self.provenance),
            "created_at": self._created_at,
        }

    def project_capabilities(
        self,
        status_filter: CapabilityStatus | None = None,
    ) -> list[CapabilityRecord]:
        """Return capabilities, optionally filtered to *status_filter*.

        Parameters
        ----------
        status_filter:
            When given, only capabilities whose status equals *status_filter*
            are returned.  When ``None``, all capabilities are returned.
        """
        if status_filter is None:
            return list(self.capabilities)
        return [c for c in self.capabilities if c.status == status_filter]

    def check_dependency_satisfied(
        self,
        dep: str,
        available_packages: Iterable[str] | None = None,
    ) -> bool:
        """Return True when *dep* appears in ``dependency_order``.

        When *available_packages* is provided, also checks that *dep* is
        listed there — a lightweight availability check suitable for use
        before a heavy import.

        Parameters
        ----------
        dep:
            Module path to check.
        available_packages:
            Optional iterable of module paths known to be available.
        """
        in_order = dep in self.dependency_order
        if not in_order:
            return False
        if available_packages is not None:
            available_set = set(available_packages)
            return dep in available_set
        return True

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest."""
        stable = [c for c in self.capabilities if c.status == CapabilityStatus.STABLE]
        provisional = [c for c in self.capabilities if c.status == CapabilityStatus.PROVISIONAL]
        experimental = [c for c in self.capabilities if c.status == CapabilityStatus.EXPERIMENTAL]
        lines = [
            f"PackageManifest: {self.subsystem_name}",
            f"  stage           : {self.stage}",
            f"  capabilities    : {len(self.capabilities)} total "
            f"({len(stable)} stable, {len(provisional)} provisional, "
            f"{len(experimental)} experimental)",
            f"  theorem targets : {len(self.theorem_targets)}",
            f"  dependency order: {len(self.dependency_order)} entries",
            f"  provenance src  : {self.provenance.get('semantic_source', 'unknown')}",
        ]
        return "\n".join(lines)

    def serialize_to_json(self, indent: int = 2) -> str:
        """Serialise the manifest to a JSON string.

        The output is deterministic: keys are sorted at every nesting level
        so that the serialisation can be used as a stable artefact in CI.
        """
        raw = self.as_dict()
        self._audit_log.append(
            f"[{time.time():.3f}] serialize_to_json() called"
        )
        return json.dumps(raw, indent=indent, sort_keys=True, default=str)

    def digest(self) -> str:
        """Return a stable SHA-256 digest of the serialised manifest."""
        raw = self.serialize_to_json(indent=0)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def audit_trail(self) -> list[str]:
        """Return a copy of the internal audit log."""
        return list(self._audit_log)


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Stateful validator for :class:`PackageManifest` records.

    ``ManifestValidator`` runs a suite of checks against a manifest and
    accumulates a structured report.  It is designed to be used both in
    CI pipelines and interactively during development.

    Parameters
    ----------
    manifest:
        The :class:`PackageManifest` to validate.

    copilot: shared-core marker
    """

    def __init__(self, manifest: PackageManifest) -> None:
        self._manifest = manifest
        self._errors: list[str] = []
        self._warnings: list[str] = []
        self._info: list[str] = []
        self._ran: bool = False

    # ------------------------------------------------------------------
    # Primary validation entry points
    # ------------------------------------------------------------------

    def validate_manifest(self) -> bool:
        """Run all validation checks and return True when the manifest is valid.

        Populates ``_errors``, ``_warnings``, and ``_info`` as side-effects.
        Safe to call multiple times; each call resets state and re-runs.
        """
        self._errors.clear()
        self._warnings.clear()
        self._info.clear()
        self._ran = True

        schema_errors = self._manifest.validate()
        self._errors.extend(schema_errors)
        self._check_capability_coverage()
        self._check_theorem_coverage()
        self._check_provenance()
        self._check_dependency_completeness()
        return len(self._errors) == 0

    def check_capability_coverage(self) -> dict[str, bool]:
        """Return a mapping of capability names to whether they are grounded.

        A capability is *grounded* when it has at least one theorem target
        that appears in :data:`THEOREM_TARGETS`.  Ungrounded STABLE
        capabilities are errors; ungrounded PROVISIONAL capabilities are
        warnings.
        """
        result: dict[str, bool] = {}
        for cap in self._manifest.capabilities:
            grounded = cap.is_grounded() and all(
                t in THEOREM_TARGETS for t in cap.theorem_support
            )
            result[cap.name] = grounded
            if not grounded:
                if cap.status == CapabilityStatus.STABLE:
                    self._errors.append(
                        f"STABLE capability {cap.name!r} is not grounded by any "
                        "known theorem target."
                    )
                elif cap.status == CapabilityStatus.PROVISIONAL:
                    self._warnings.append(
                        f"PROVISIONAL capability {cap.name!r} has no theorem grounding."
                    )
        return result

    def check_theorem_coverage(self) -> dict[str, bool]:
        """Return a mapping of theorem targets to whether they are assigned.

        A theorem target is *assigned* when at least one capability's
        ``theorem_support`` tuple references it.  Unassigned targets are
        warnings: they are declared but nothing claims to discharge them.
        """
        assigned_in_caps: set[str] = set()
        for cap in self._manifest.capabilities:
            assigned_in_caps.update(cap.theorem_support)
        result: dict[str, bool] = {}
        for thm in THEOREM_TARGETS:
            assigned = thm in assigned_in_caps
            result[thm] = assigned
            if not assigned:
                self._warnings.append(
                    f"Global theorem target {thm!r} is not assigned to any capability."
                )
        for thm in self._manifest.theorem_targets:
            if thm not in assigned_in_caps:
                self._warnings.append(
                    f"Manifest theorem target {thm!r} is not assigned to any capability."
                )
        return result

    def report(self) -> str:
        """Return a human-readable validation report.

        Calls :meth:`validate_manifest` if it has not already been called
        in this validator's lifetime.
        """
        if not self._ran:
            self.validate_manifest()
        status = "PASS" if not self._errors else "FAIL"
        lines = [
            f"ManifestValidator report — {self._manifest.subsystem_name}",
            f"  overall status : {status}",
            f"  errors         : {len(self._errors)}",
            f"  warnings       : {len(self._warnings)}",
            f"  info           : {len(self._info)}",
        ]
        if self._errors:
            lines.append("  ERRORS:")
            for e in self._errors:
                lines.append(f"    - {e}")
        if self._warnings:
            lines.append("  WARNINGS:")
            for w in self._warnings:
                lines.append(f"    - {w}")
        if self._info:
            lines.append("  INFO:")
            for i in self._info:
                lines.append(f"    - {i}")
        return "\n".join(lines)

    def errors(self) -> list[str]:
        """Return a copy of the accumulated error messages."""
        return list(self._errors)

    def warnings(self) -> list[str]:
        """Return a copy of the accumulated warning messages."""
        return list(self._warnings)

    def is_valid(self) -> bool:
        """Return True when no errors have been accumulated."""
        return len(self._errors) == 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_capability_coverage(self) -> None:
        self.check_capability_coverage()

    def _check_theorem_coverage(self) -> None:
        self.check_theorem_coverage()

    def _check_provenance(self) -> None:
        required_keys = {"semantic_source", "stage", "target_package"}
        prov_keys = set(self._manifest.provenance.keys())
        missing = required_keys - prov_keys
        for key in missing:
            self._errors.append(
                f"Provenance record is missing required key {key!r}."
            )
        if self._manifest.provenance.get("target_package", "") != (
            "jugeo.foundations.descent_locality"
        ):
            self._warnings.append(
                "Provenance target_package does not match expected sub-package path."
            )

    def _check_dependency_completeness(self) -> None:
        declared = set(self._manifest.dependency_order)
        known = set(DEPENDENCY_ORDER)
        extra = declared - known
        for dep in extra:
            self._warnings.append(
                f"Dependency {dep!r} is declared but not in the canonical "
                "DEPENDENCY_ORDER constant."
            )
        missing = known - declared
        for dep in missing:
            self._info.append(
                f"Canonical dependency {dep!r} is not listed in dependency_order."
            )


# ---------------------------------------------------------------------------
# Module-level manifest instance
# ---------------------------------------------------------------------------

_CAPABILITIES: tuple[CapabilityRecord, ...] = (
    CapabilityRecord(
        name="covers",
        description=(
            "Grothendieck covers and cover refinement for the Jugeo site, "
            "following Theory2.tex §4.1."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "sheaf-condition",
            "cover-refinement-monotonicity",
        ),
    ),
    CapabilityRecord(
        name="descent",
        description=(
            "Descent engine: overlap checking, cocycle computation, and "
            "global section assembly from local sections."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "descent-criterion",
            "gluing-completeness",
        ),
    ),
    CapabilityRecord(
        name="gluing",
        description=(
            "Explicit gluing maps and compatibility matrices relating sections "
            "on overlapping patches."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "gluing-completeness",
            "local-to-global-faithfulness",
        ),
    ),
    CapabilityRecord(
        name="obstructions",
        description=(
            "Obstruction classes in Čech cohomology (H¹) recording exactly "
            "where and why gluing fails."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "H1-obstruction-class-uniqueness",
            "obstruction-triviality-characterisation",
        ),
    ),
    CapabilityRecord(
        name="local-to-global",
        description=(
            "Local-to-global principle: assembling global semantic objects from "
            "compatible local data."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "local-to-global-faithfulness",
            "locality-principle-soundness",
        ),
    ),
    CapabilityRecord(
        name="sheaf-axioms",
        description=(
            "Machine-checkable sheaf axioms: locality (uniqueness from local "
            "data) and gluing (existence from compatible local data)."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "sheaf-condition",
            "locality-principle-soundness",
        ),
    ),
    CapabilityRecord(
        name="cohomology",
        description=(
            "Čech cohomology groups H⁰ and H¹ for the Jugeo Grothendieck "
            "topology, supporting descent obstruction analysis."
        ),
        status=CapabilityStatus.STABLE,
        theorem_support=(
            "H1-obstruction-class-uniqueness",
            "cocycle-boundary-exactness",
        ),
    ),
    CapabilityRecord(
        name="transport",
        description=(
            "Transport maps: morphism-induced restriction and extension of "
            "local data across the site."
        ),
        status=CapabilityStatus.PROVISIONAL,
        theorem_support=(
            "transport-coherence",
        ),
    ),
)

_DEPENDENCIES: tuple[DependencyRecord, ...] = (
    DependencyRecord(
        name="jugeo.geometry.site",
        version_constraint="any",
        required=True,
        justification=(
            "Provides the Grothendieck site, coordinate objects, and "
            "morphism graph that descent_locality builds upon."
        ),
    ),
    DependencyRecord(
        name="jugeo.geometry.covers",
        version_constraint="any",
        required=True,
        justification=(
            "Provides Cover, CoverMember, and cover refinement logic "
            "consumed by descent and sheaf axiom checks."
        ),
    ),
    DependencyRecord(
        name="jugeo.geometry.descent",
        version_constraint="any",
        required=True,
        justification=(
            "Provides DescentEngine, LocalSection, and GluingData: the "
            "primary computational substrate for descent_locality."
        ),
    ),
    DependencyRecord(
        name="jugeo.foundations.formal_core",
        version_constraint="any",
        required=True,
        justification=(
            "Provides formal core types and proof-carrying data structures "
            "shared across all foundations sub-packages."
        ),
    ),
    DependencyRecord(
        name="jugeo.foundations.type_objects",
        version_constraint="any",
        required=False,
        justification=(
            "Provides typed object representations; optional but improves "
            "type safety of transport maps."
        ),
    ),
    DependencyRecord(
        name="jugeo.foundations.judgment_products",
        version_constraint="any",
        required=False,
        justification=(
            "Provides product and pullback operations on judgments; optional "
            "dependency for advanced gluing scenarios."
        ),
    ),
)

DESCENT_LOCALITY_MANIFEST: Final[SubsystemManifest] = SubsystemManifest(
    subsystem_id="jugeo.foundations.descent_locality",
    capabilities=_CAPABILITIES,
    theorems=THEOREM_TARGETS,
    dependencies=_DEPENDENCIES,
    copilot_channel="shared-core",
)

_PACKAGE_MANIFEST: PackageManifest = PackageManifest(
    subsystem_name="descent_locality",
    stage="foundations-descent-locality",
    capabilities=list(_CAPABILITIES),
    theorem_targets=list(THEOREM_TARGETS),
    dependency_order=list(DEPENDENCY_ORDER),
    provenance=MANIFEST_PROVENANCE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__: list[str] = [
    "MANIFEST_PROVENANCE",
    "SUBSYSTEM_CAPABILITIES",
    "THEOREM_TARGETS",
    "DEPENDENCY_ORDER",
    "CapabilityStatus",
    "CapabilityRecord",
    "DependencyRecord",
    "PackageManifest",
    "ManifestValidator",
    "SubsystemManifest",
    "DESCENT_LOCALITY_MANIFEST",
]
