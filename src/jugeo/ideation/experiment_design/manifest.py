"""Experiment design manifests, hypothesis tracking, and design registries.

Ch53 — Experiment Design for Mathematical Ideation Optimization.

A *manifest* is an ordered catalogue of :class:`ExperimentDescriptor` records
that collectively specify what experiments must be executed to validate or
falsify a scientific programme.  Each descriptor carries:

* A unique identifier and experiment type (ablation, calibration, …).
* A falsifiable *hypothesis* string.
* A tuple of :class:`ControlVariable` instances pinning nuisance factors.
* A tuple of :class:`MeasureSpec` records naming the quantities to record.

Design philosophy
-----------------
Manifests are **immutable descriptions**; they record *intent*, not outcomes.
Results live in the ``models`` sub-module.  The registry classes here provide
in-process lookup and validation services.

Validation rules
----------------
* Every ``exp_id`` in a manifest must be unique (no two descriptors share an
  id).
* A hypothesis must be non-empty, at most 512 characters, and contain at least
  one verb-like word so it reads as a falsifiable claim rather than a label.
* Control variable names within a single descriptor must be unique.
* Measure names within a single descriptor must be unique.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__: list[str] = [
    "ExperimentType",
    "ExperimentStatus",
    "ControlVariable",
    "MeasureSpec",
    "ExperimentDescriptor",
    "ExperimentDesignManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "ExperimentRegistry",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExperimentType(str, Enum):
    """Categorical type that determines the statistical analysis template.

    Attributes
    ----------
    ABLATION:
        Remove one component at a time and measure performance degradation.
    CALIBRATION:
        Verify that estimated parameters converge to true values.
    FALSIFICATION:
        Actively seek conditions that contradict the working hypothesis.
    COMPARISON:
        Compare two or more alternative systems or configurations head-to-head.
    SENSITIVITY:
        Vary a single parameter over a range and measure the response surface.
    """

    ABLATION = "ablation"
    CALIBRATION = "calibration"
    FALSIFICATION = "falsification"
    COMPARISON = "comparison"
    SENSITIVITY = "sensitivity"


class ExperimentStatus(str, Enum):
    """Lifecycle state of a single experiment run.

    Attributes
    ----------
    PLANNED:
        Designed but not yet started.
    RUNNING:
        Currently executing.
    COMPLETED:
        Finished normally; results are available.
    FAILED:
        Terminated due to an error; results may be partial or absent.
    ABANDONED:
        Deliberately stopped before completion; results are not used.
    """

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


# ---------------------------------------------------------------------------
# Frozen data records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlVariable:
    """A nuisance factor held constant throughout an experiment.

    Parameters
    ----------
    name:
        Machine-readable identifier (snake_case recommended).
    value:
        The fixed value for this variable during the experiment.
    description:
        Human-readable explanation of what is being held constant and why.

    Examples
    --------
    >>> cv = ControlVariable("random_seed", 42, "RNG seed for reproducibility")
    >>> cv.name
    'random_seed'
    """

    name: str
    value: Any
    description: str


@dataclass(frozen=True, slots=True)
class MeasureSpec:
    """Specification for a quantity to be recorded during an experiment.

    Parameters
    ----------
    name:
        Machine-readable identifier for the measurement (e.g. ``"yield_score"``).
    unit:
        Physical or logical unit (e.g. ``"score [0,1]"``, ``"seconds"``).
    aggregation:
        How multiple observations are aggregated (``"mean"``, ``"sum"``,
        ``"max"``, etc.).
    description:
        Human-readable description of what is being measured.
    """

    name: str
    unit: str
    aggregation: str
    description: str


@dataclass(frozen=True, slots=True)
class ExperimentDescriptor:
    """Complete, self-contained description of one experiment.

    Parameters
    ----------
    exp_id:
        Unique string identifier within a manifest (e.g. ``"abl_01"``).
    exp_type:
        Categorical type of the experiment.
    hypothesis:
        Falsifiable claim that the experiment tests.
    controls:
        Tuple of :class:`ControlVariable` instances held fixed.
    measures:
        Tuple of :class:`MeasureSpec` instances to be collected.
    priority:
        Execution priority (lower number = higher priority).  Default 1.
    tags:
        Optional free-form labels for filtering (e.g. ``("fast", "core")``).
    """

    exp_id: str
    exp_type: ExperimentType
    hypothesis: str
    controls: tuple[ControlVariable, ...]
    measures: tuple[MeasureSpec, ...]
    priority: int = 1
    tags: tuple[str, ...] = ()

    def has_control(self, name: str) -> bool:
        """Return ``True`` if a control variable with *name* exists.

        Parameters
        ----------
        name:
            The control variable name to search for.
        """
        return any(c.name == name for c in self.controls)

    def has_measure(self, name: str) -> bool:
        """Return ``True`` if a measure spec with *name* exists.

        Parameters
        ----------
        name:
            The measure name to search for.
        """
        return any(m.name == name for m in self.measures)

    def summary(self) -> str:
        """Return a single-line human-readable summary of this descriptor.

        The summary contains the id, type, priority, and a truncated form of
        the hypothesis.
        """
        hyp_short = (self.hypothesis[:60] + "…") if len(self.hypothesis) > 60 else self.hypothesis
        return (
            f"[{self.exp_id}] type={self.exp_type.value} priority={self.priority} "
            f"controls={len(self.controls)} measures={len(self.measures)} "
            f'hypothesis="{hyp_short}"'
        )


# ---------------------------------------------------------------------------
# Manifest dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExperimentDesignManifest:
    """Ordered catalogue of experiments for a research programme.

    Parameters
    ----------
    manifest_id:
        Unique identifier for this manifest (e.g. ``"ch53_core"``).
    title:
        Short human-readable title.
    description:
        Longer free-form description of the research programme.
    experiments:
        Mutable list of :class:`ExperimentDescriptor` records.
    created_at:
        Unix timestamp of creation (defaults to ``time.time()`` if not set).
    version:
        Semantic version string of this manifest schema.  Defaults to
        ``"1.0"``.
    """

    manifest_id: str
    title: str
    description: str
    experiments: list[ExperimentDescriptor] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    version: str = "1.0"

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_experiment(self, desc: ExperimentDescriptor) -> None:
        """Append *desc* to the manifest.

        Raises
        ------
        ValueError
            If an experiment with the same ``exp_id`` already exists.
        """
        if self.get_by_id(desc.exp_id) is not None:
            raise ValueError(f"Experiment id {desc.exp_id!r} already registered in manifest {self.manifest_id!r}.")
        self.experiments.append(desc)
        _log.debug("Added experiment %r to manifest %r.", desc.exp_id, self.manifest_id)

    def remove_experiment(self, exp_id: str) -> bool:
        """Remove the experiment with *exp_id*.

        Returns
        -------
        bool
            ``True`` if the experiment was found and removed, ``False`` if no
            experiment with that id existed.
        """
        before = len(self.experiments)
        self.experiments = [e for e in self.experiments if e.exp_id != exp_id]
        removed = len(self.experiments) < before
        if removed:
            _log.debug("Removed experiment %r from manifest %r.", exp_id, self.manifest_id)
        return removed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_by_type(self, exp_type: ExperimentType) -> list[ExperimentDescriptor]:
        """Return all descriptors whose type matches *exp_type*."""
        return [e for e in self.experiments if e.exp_type == exp_type]

    def get_by_id(self, exp_id: str) -> ExperimentDescriptor | None:
        """Return the descriptor with *exp_id*, or ``None`` if absent."""
        for e in self.experiments:
            if e.exp_id == exp_id:
                return e
        return None

    # ------------------------------------------------------------------
    # Validation & summary
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run built-in validation and return a list of error messages.

        An empty list means the manifest is valid.  Errors include duplicate
        ids, empty hypotheses, and duplicate control/measure names within a
        descriptor.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for desc in self.experiments:
            if desc.exp_id in seen_ids:
                errors.append(f"Duplicate exp_id: {desc.exp_id!r}.")
            seen_ids.add(desc.exp_id)
            if not desc.hypothesis.strip():
                errors.append(f"Experiment {desc.exp_id!r} has an empty hypothesis.")
            ctrl_names = [c.name for c in desc.controls]
            if len(ctrl_names) != len(set(ctrl_names)):
                errors.append(f"Experiment {desc.exp_id!r} has duplicate control names.")
            meas_names = [m.name for m in desc.measures]
            if len(meas_names) != len(set(meas_names)):
                errors.append(f"Experiment {desc.exp_id!r} has duplicate measure names.")
        return errors

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the manifest."""
        lines: list[str] = [
            f"Manifest : {self.manifest_id}  (v{self.version})",
            f"Title    : {self.title}",
            f"Experiments: {len(self.experiments)}",
        ]
        type_counts: dict[str, int] = {}
        for e in self.experiments:
            type_counts[e.exp_type.value] = type_counts.get(e.exp_type.value, 0) + 1
        for t, n in sorted(type_counts.items()):
            lines.append(f"  {t}: {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

# Words that suggest a hypothesis is a falsifiable claim rather than a label.
_VERB_HINTS: frozenset[str] = frozenset(
    {
        "increases", "decreases", "reduces", "improves", "degrades",
        "causes", "affects", "enables", "prevents", "is", "are",
        "does", "has", "have", "will", "can", "should", "leads",
        "results", "produces", "yields", "depends", "correlates",
    }
)


def _validate_exp_id(exp_id: str) -> bool:
    """Return ``True`` if *exp_id* is a non-empty string with no whitespace.

    Parameters
    ----------
    exp_id:
        The candidate identifier to validate.
    """
    return bool(exp_id) and exp_id == exp_id.strip() and " " not in exp_id


def _normalize_hypothesis(text: str) -> str:
    """Strip leading/trailing whitespace and collapse internal runs.

    Parameters
    ----------
    text:
        Raw hypothesis string.
    """
    return " ".join(text.split())


def _build_experiment_summary(desc: ExperimentDescriptor) -> str:
    """Construct a detailed multi-line summary for *desc*.

    Parameters
    ----------
    desc:
        The descriptor to summarise.
    """
    lines: list[str] = [
        f"Experiment : {desc.exp_id}",
        f"Type       : {desc.exp_type.value}",
        f"Priority   : {desc.priority}",
        f"Hypothesis : {desc.hypothesis}",
        f"Controls   : {', '.join(c.name for c in desc.controls) or '—'}",
        f"Measures   : {', '.join(m.name for m in desc.measures) or '—'}",
        f"Tags       : {', '.join(desc.tags) or '—'}",
    ]
    return "\n".join(lines)


class ManifestValidator:
    """Validates :class:`ExperimentDesignManifest` instances for correctness.

    All validation methods are pure and stateless; the class acts as a
    namespace rather than holding mutable state.

    Usage
    -----
    >>> validator = ManifestValidator()
    >>> errors = validator.validate(my_manifest)
    >>> if errors:
    ...     for e in errors:
    ...         print(e)
    """

    def validate(self, manifest: ExperimentDesignManifest) -> list[str]:
        """Return a list of error messages for *manifest*.

        An empty list means the manifest passes all checks.

        Parameters
        ----------
        manifest:
            The manifest to validate.
        """
        errors: list[str] = []
        if not manifest.manifest_id.strip():
            errors.append("manifest_id must not be empty.")
        if not manifest.title.strip():
            errors.append("title must not be empty.")
        if not self.check_unique_ids(manifest):
            errors.append("manifest contains duplicate exp_id values.")
        for desc in manifest.experiments:
            if not _validate_exp_id(desc.exp_id):
                errors.append(f"exp_id {desc.exp_id!r} is invalid (must be non-empty, no whitespace).")
            errors.extend(self.check_hypothesis_quality(desc))
        return errors

    def check_unique_ids(self, manifest: ExperimentDesignManifest) -> bool:
        """Return ``True`` if all experiment ids in *manifest* are unique.

        Parameters
        ----------
        manifest:
            The manifest to check.
        """
        ids = [e.exp_id for e in manifest.experiments]
        return len(ids) == len(set(ids))

    def check_hypothesis_quality(self, descriptor: ExperimentDescriptor) -> list[str]:
        """Return quality issues for *descriptor*'s hypothesis.

        Checks performed:

        * Non-empty after normalisation.
        * At most 512 characters.
        * Contains at least one recognisable verb suggesting falsifiability.

        Parameters
        ----------
        descriptor:
            The experiment descriptor whose hypothesis is checked.
        """
        issues: list[str] = []
        hyp = _normalize_hypothesis(descriptor.hypothesis)
        if not hyp:
            issues.append(f"[{descriptor.exp_id}] hypothesis is empty after normalisation.")
            return issues  # further checks pointless
        if len(hyp) > 512:
            issues.append(
                f"[{descriptor.exp_id}] hypothesis is {len(hyp)} characters; limit is 512."
            )
        words = {w.lower().rstrip(".,;:!?") for w in hyp.split()}
        if not words & _VERB_HINTS:
            issues.append(
                f"[{descriptor.exp_id}] hypothesis does not appear to contain a falsifiable verb: {hyp!r}."
            )
        return issues


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """In-process registry of :class:`ExperimentDesignManifest` instances.

    Manifests are stored by their ``manifest_id``.  Registering a manifest
    with a duplicate id raises ``ValueError``.

    Usage
    -----
    >>> registry = ManifestRegistry()
    >>> registry.register(my_manifest)
    >>> registry.get("ch53_core")
    ExperimentDesignManifest(...)
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._store: dict[str, ExperimentDesignManifest] = {}

    def register(self, manifest: ExperimentDesignManifest) -> None:
        """Add *manifest* to the registry.

        Parameters
        ----------
        manifest:
            The manifest to register.

        Raises
        ------
        ValueError
            If a manifest with the same ``manifest_id`` is already registered.
        """
        if manifest.manifest_id in self._store:
            raise ValueError(f"Manifest {manifest.manifest_id!r} already registered.")
        self._store[manifest.manifest_id] = manifest
        _log.debug("Registered manifest %r.", manifest.manifest_id)

    def unregister(self, manifest_id: str) -> bool:
        """Remove the manifest with *manifest_id* from the registry.

        Returns
        -------
        bool
            ``True`` if found and removed, ``False`` if not present.
        """
        if manifest_id in self._store:
            del self._store[manifest_id]
            _log.debug("Unregistered manifest %r.", manifest_id)
            return True
        return False

    def get(self, manifest_id: str) -> ExperimentDesignManifest | None:
        """Return the manifest for *manifest_id*, or ``None``.

        Parameters
        ----------
        manifest_id:
            The id to look up.
        """
        return self._store.get(manifest_id)

    def list_all(self) -> list[ExperimentDesignManifest]:
        """Return all registered manifests in registration order."""
        return list(self._store.values())

    def find_by_tag(self, tag: str) -> list[ExperimentDesignManifest]:
        """Return manifests containing at least one experiment tagged *tag*.

        Parameters
        ----------
        tag:
            The tag string to search for.
        """
        result: list[ExperimentDesignManifest] = []
        for manifest in self._store.values():
            if any(tag in desc.tags for desc in manifest.experiments):
                result.append(manifest)
        return result


class ExperimentRegistry:
    """Global registry mapping experiment ids to their descriptors.

    Unlike :class:`ManifestRegistry` which groups experiments by manifest,
    this registry provides direct O(1) lookup by ``exp_id``.

    Usage
    -----
    >>> reg = ExperimentRegistry()
    >>> reg.register(descriptor, manifest_id="ch53_core")
    >>> reg.lookup("abl_01")
    ExperimentDescriptor(...)
    """

    def __init__(self) -> None:
        """Initialise an empty experiment registry."""
        self._descriptors: dict[str, ExperimentDescriptor] = {}
        self._manifest_map: dict[str, str] = {}  # exp_id -> manifest_id

    def register(self, descriptor: ExperimentDescriptor, manifest_id: str) -> None:
        """Register *descriptor* under *manifest_id*.

        Parameters
        ----------
        descriptor:
            The experiment descriptor to register.
        manifest_id:
            The id of the owning manifest (for bookkeeping only).

        Raises
        ------
        ValueError
            If an experiment with the same ``exp_id`` is already registered.
        """
        if descriptor.exp_id in self._descriptors:
            raise ValueError(f"Experiment {descriptor.exp_id!r} already registered.")
        self._descriptors[descriptor.exp_id] = descriptor
        self._manifest_map[descriptor.exp_id] = manifest_id
        _log.debug("Registered experiment %r (manifest=%r).", descriptor.exp_id, manifest_id)

    def lookup(self, exp_id: str) -> ExperimentDescriptor | None:
        """Return the descriptor for *exp_id*, or ``None`` if absent.

        Parameters
        ----------
        exp_id:
            The experiment id to look up.
        """
        return self._descriptors.get(exp_id)

    def list_by_type(self, exp_type: ExperimentType) -> list[ExperimentDescriptor]:
        """Return all descriptors whose type is *exp_type*.

        Parameters
        ----------
        exp_type:
            The experiment type to filter by.
        """
        return [d for d in self._descriptors.values() if d.exp_type == exp_type]

    def count(self) -> int:
        """Return the total number of registered experiments."""
        return len(self._descriptors)
