"""
jugeo.orchestration.frontier_phases.manifest
=============================================
Manifest, registry, and catalog layer for the frontier_phases sub-system
(Chapter 47).

This module sits *above* :mod:`jugeo.orchestration.frontier_phases.models` in
the dependency graph.  It provides:

* :class:`FrontierPhasesManifest` — a versioned, serialisable record of all
  :class:`~models.PhaseDescriptor` instances that make up one logical
  configuration of the frontier-phases package.
* :class:`PhaseRegistry` — a runtime registry that maps phase IDs to
  :class:`~models.PhaseDescriptor` instances and supports look-up by kind.
* :class:`TransitionTriggerCatalog` — a static catalog describing every
  :class:`~models.TransitionTrigger` member (description, severity, automatic
  vs manual).
* :func:`build_manifest` — factory that builds a standard manifest covering
  all :class:`~models.PhaseKind` values.
* :func:`validate_manifest` — standalone validation function that returns a
  ``(is_valid, errors)`` tuple.

Design notes
------------
* All public-facing classes use either ``@dataclass(slots=True)`` for mutable
  objects or plain ``__init__`` constructors where richer initialisation logic
  is needed.
* The module is dependency-free beyond the standard library and
  :mod:`jugeo.orchestration.frontier_phases.models`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from jugeo.orchestration.frontier_phases.models import (
    ConvergenceCertificate,
    PhaseDescriptor,
    PhaseHistory,
    PhaseHealthStatus,
    PhaseKind,
    PhaseTransitionRecord,
    StallDetector,
    TransitionTrigger,
    DEFAULT_PHASE_DURATION,
    MIN_CONVERGENCE_COVERAGE,
    make_phase_descriptor,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Package version string surfaced in every manifest.
MANIFEST_VERSION: str = "1.0.0"

#: Default chapter reference attached to manifests built by :func:`build_manifest`.
DEFAULT_CHAPTER_REF: str = "Ch47"

#: Minimum number of phase descriptors a valid manifest must contain.
MANIFEST_MIN_PHASES: int = 1

#: Maximum number of phase descriptors a single manifest may contain.
MANIFEST_MAX_PHASES: int = 256

#: Severity description labels keyed by integer severity score.
SEVERITY_LABELS: dict[int, str] = {
    1: "informational",
    2: "routine",
    3: "warning",
    4: "critical",
}

#: Human-readable descriptions for each :class:`~models.TransitionTrigger`.
TRIGGER_DESCRIPTIONS: dict[TransitionTrigger, str] = {
    TransitionTrigger.COVERAGE_THRESHOLD: (
        "A pre-configured frontier coverage ratio was reached, prompting a "
        "phase change."
    ),
    TransitionTrigger.STALL_DETECTED: (
        "The StallDetector signalled that measurable progress had ceased for "
        "the configured stall window."
    ),
    TransitionTrigger.BUDGET_EXHAUSTED: (
        "The computational or wall-clock budget allocated to the current "
        "phase has been fully consumed."
    ),
    TransitionTrigger.DIVERSITY_DROP: (
        "Population or solution diversity fell below an acceptable threshold, "
        "risking premature convergence."
    ),
    TransitionTrigger.MANUAL: (
        "A human operator or external control system explicitly requested a "
        "phase transition."
    ),
    TransitionTrigger.SCHEDULED: (
        "The transition was pre-scheduled at a specific point in the search "
        "timeline and has now been triggered."
    ),
}

#: Recommended default expected durations (seconds) per :class:`~models.PhaseKind`.
KIND_DEFAULT_DURATIONS: dict[PhaseKind, float] = {
    PhaseKind.EXPLORATION: 600.0,
    PhaseKind.EXPLOITATION: 900.0,
    PhaseKind.TRANSITION: 30.0,
    PhaseKind.STALLED: 120.0,
    PhaseKind.CONVERGED: 0.0,
    PhaseKind.DIVERGED: 180.0,
    PhaseKind.RECOVERY: 300.0,
}

#: Standard entry conditions per :class:`~models.PhaseKind`.
KIND_ENTRY_CONDITIONS: dict[PhaseKind, tuple[str, ...]] = {
    PhaseKind.EXPLORATION: ("frontier_initialised",),
    PhaseKind.EXPLOITATION: ("coverage_above_threshold", "diversity_adequate"),
    PhaseKind.TRANSITION: ("transition_requested",),
    PhaseKind.STALLED: ("stall_detected",),
    PhaseKind.CONVERGED: ("coverage_complete", "stability_confirmed"),
    PhaseKind.DIVERGED: ("divergence_detected",),
    PhaseKind.RECOVERY: ("health_critical",),
}

#: Standard exit conditions per :class:`~models.PhaseKind`.
KIND_EXIT_CONDITIONS: dict[PhaseKind, tuple[str, ...]] = {
    PhaseKind.EXPLORATION: ("coverage_above_threshold",),
    PhaseKind.EXPLOITATION: ("stability_confirmed",),
    PhaseKind.TRANSITION: ("target_phase_ready",),
    PhaseKind.STALLED: ("progress_resumed",),
    PhaseKind.CONVERGED: (),
    PhaseKind.DIVERGED: ("divergence_resolved",),
    PhaseKind.RECOVERY: ("health_restored",),
}


# ---------------------------------------------------------------------------
# FrontierPhasesManifest
# ---------------------------------------------------------------------------


class FrontierPhasesManifest:
    """Versioned manifest for the frontier_phases package.

    A :class:`FrontierPhasesManifest` is the top-level configuration artefact
    for a particular deployment of the frontier-phases sub-system.  It lists
    every :class:`~models.PhaseDescriptor` that the orchestrator is permitted
    to use, together with a transition catalog and lightweight metadata.

    Manifests are mutable: phases can be added after construction, but the
    manifest should be validated (via :meth:`validate`) before being handed to
    the orchestrator.

    Parameters
    ----------
    version:
        Semantic version string for this manifest.
    chapter_ref:
        Reference to the design document chapter that specifies this
        configuration (e.g. ``"Ch47"``).
    phase_descriptors:
        Initial list of :class:`~models.PhaseDescriptor` instances.
    transition_catalog:
        Arbitrary dict describing allowed transitions; consumed by the
        orchestrator.
    created_at:
        Unix timestamp at which this manifest was created.
    """

    __slots__ = (
        "version",
        "chapter_ref",
        "phase_descriptors",
        "transition_catalog",
        "created_at",
    )

    def __init__(
        self,
        version: str = MANIFEST_VERSION,
        chapter_ref: str = DEFAULT_CHAPTER_REF,
        phase_descriptors: list[PhaseDescriptor] | None = None,
        transition_catalog: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> None:
        """Initialise a new manifest.

        Parameters
        ----------
        version:
            Semantic version string (default: :data:`MANIFEST_VERSION`).
        chapter_ref:
            Design-doc chapter reference (default: :data:`DEFAULT_CHAPTER_REF`).
        phase_descriptors:
            Optional initial list of descriptors; defaults to ``[]``.
        transition_catalog:
            Optional transition specification; defaults to ``{}``.
        created_at:
            Optional creation timestamp; defaults to ``time.time()``.
        """
        self.version: str = version
        self.chapter_ref: str = chapter_ref
        self.phase_descriptors: list[PhaseDescriptor] = (
            list(phase_descriptors) if phase_descriptors else []
        )
        self.transition_catalog: dict[str, Any] = (
            dict(transition_catalog) if transition_catalog else {}
        )
        self.created_at: float = created_at if created_at is not None else time.time()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_phase(self, descriptor: PhaseDescriptor) -> None:
        """Append a :class:`~models.PhaseDescriptor` to this manifest.

        Parameters
        ----------
        descriptor:
            The descriptor to add.  Duplicate :attr:`~models.PhaseDescriptor.phase_id`
            values are silently replaced (the existing entry is removed first).
        """
        self.phase_descriptors = [
            d for d in self.phase_descriptors if d.phase_id != descriptor.phase_id
        ]
        self.phase_descriptors.append(descriptor)

    def remove_phase(self, phase_id: str) -> bool:
        """Remove the descriptor with the given *phase_id*.

        Parameters
        ----------
        phase_id:
            The ID of the descriptor to remove.

        Returns
        -------
        bool
            ``True`` if a descriptor was found and removed, ``False`` if no
            descriptor with that ID existed.
        """
        before = len(self.phase_descriptors)
        self.phase_descriptors = [
            d for d in self.phase_descriptors if d.phase_id != phase_id
        ]
        return len(self.phase_descriptors) < before

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_phase(self, phase_id: str) -> PhaseDescriptor | None:
        """Look up a descriptor by its :attr:`~models.PhaseDescriptor.phase_id`.

        Parameters
        ----------
        phase_id:
            The phase ID to search for.

        Returns
        -------
        PhaseDescriptor | None
            The matching descriptor, or ``None`` if not found.
        """
        for descriptor in self.phase_descriptors:
            if descriptor.phase_id == phase_id:
                return descriptor
        return None

    def all_phases(self) -> list[PhaseDescriptor]:
        """Return a shallow copy of all descriptors in this manifest.

        Returns
        -------
        list[PhaseDescriptor]
        """
        return list(self.phase_descriptors)

    def phases_by_kind(self, kind: PhaseKind) -> list[PhaseDescriptor]:
        """Return all descriptors whose :attr:`~models.PhaseDescriptor.kind` matches.

        Parameters
        ----------
        kind:
            The :class:`~models.PhaseKind` to filter by.

        Returns
        -------
        list[PhaseDescriptor]
        """
        return [d for d in self.phase_descriptors if d.kind is kind]

    def phase_count(self) -> int:
        """Return the number of descriptors currently in this manifest."""
        return len(self.phase_descriptors)

    def covers_all_kinds(self) -> bool:
        """Return ``True`` if every :class:`~models.PhaseKind` member is represented.

        Returns
        -------
        bool
        """
        present_kinds = {d.kind for d in self.phase_descriptors}
        return set(PhaseKind) == present_kinds

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate this manifest and return a list of error messages.

        The following checks are performed:

        * The version string must be non-empty.
        * The chapter reference must be non-empty.
        * Phase count must be in [MANIFEST_MIN_PHASES, MANIFEST_MAX_PHASES].
        * All phase IDs must be unique.
        * All phase names must be non-empty strings.
        * Expected durations must be non-negative.

        Returns
        -------
        list[str]
            Empty list if the manifest is valid; otherwise a list of
            human-readable error messages.
        """
        errors: list[str] = []

        if not self.version or not self.version.strip():
            errors.append("Manifest version must be a non-empty string.")

        if not self.chapter_ref or not self.chapter_ref.strip():
            errors.append("Manifest chapter_ref must be a non-empty string.")

        n = self.phase_count()
        if n < MANIFEST_MIN_PHASES:
            errors.append(
                f"Manifest must contain at least {MANIFEST_MIN_PHASES} phase "
                f"descriptor(s); found {n}."
            )
        if n > MANIFEST_MAX_PHASES:
            errors.append(
                f"Manifest must not exceed {MANIFEST_MAX_PHASES} phase "
                f"descriptors; found {n}."
            )

        seen_ids: set[str] = set()
        for d in self.phase_descriptors:
            if d.phase_id in seen_ids:
                errors.append(
                    f"Duplicate phase_id detected: {d.phase_id!r}."
                )
            seen_ids.add(d.phase_id)

            if not d.name or not d.name.strip():
                errors.append(
                    f"Phase descriptor {d.phase_id!r} has an empty name."
                )
            if d.expected_duration < 0.0:
                errors.append(
                    f"Phase descriptor {d.phase_id!r} has a negative "
                    f"expected_duration ({d.expected_duration!r})."
                )

        return errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "created_at": self.created_at,
            "phase_count": self.phase_count(),
            "covers_all_kinds": self.covers_all_kinds(),
            "phase_descriptors": [d.to_dict() for d in self.phase_descriptors],
            "transition_catalog": dict(self.transition_catalog),
        }

    def summary(self) -> str:
        """Return a compact human-readable summary of this manifest.

        Returns
        -------
        str
        """
        kind_counts: dict[str, int] = {}
        for d in self.phase_descriptors:
            kind_counts[d.kind.value] = kind_counts.get(d.kind.value, 0) + 1
        kind_summary = ", ".join(
            f"{k}×{v}" for k, v in sorted(kind_counts.items())
        )
        errors = self.validate()
        validity = "valid" if not errors else f"INVALID ({len(errors)} error(s))"
        return (
            f"FrontierPhasesManifest v{self.version} [{self.chapter_ref}] "
            f"phases={self.phase_count()} ({kind_summary}) "
            f"status={validity}"
        )

    def __repr__(self) -> str:  # pragma: no cover
        return self.summary()


# ---------------------------------------------------------------------------
# PhaseRegistry
# ---------------------------------------------------------------------------


class PhaseRegistry:
    """Runtime registry of named :class:`~models.PhaseDescriptor` instances.

    The registry provides O(1) look-up by phase ID and O(n) iteration by
    :class:`~models.PhaseKind`.  It is intentionally *not* a singleton; the
    caller is responsible for managing its lifetime.

    Usage example::

        registry = PhaseRegistry()
        descriptor = PhaseDescriptor.create(
            name="my_exploration",
            kind=PhaseKind.EXPLORATION,
        )
        registry.register(descriptor)
        found = registry.get(descriptor.phase_id)
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._phases: dict[str, PhaseDescriptor] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, descriptor: PhaseDescriptor) -> None:
        """Add *descriptor* to the registry.

        If a descriptor with the same :attr:`~models.PhaseDescriptor.phase_id`
        already exists it is replaced.

        Parameters
        ----------
        descriptor:
            The :class:`~models.PhaseDescriptor` to register.
        """
        self._phases[descriptor.phase_id] = descriptor

    def register_many(self, descriptors: list[PhaseDescriptor]) -> None:
        """Register multiple descriptors in one call.

        Parameters
        ----------
        descriptors:
            Iterable of descriptors to register.  Each is processed by
            :meth:`register` in order.
        """
        for d in descriptors:
            self.register(d)

    def unregister(self, phase_id: str) -> bool:
        """Remove the descriptor with the given *phase_id*.

        Parameters
        ----------
        phase_id:
            The ID of the descriptor to remove.

        Returns
        -------
        bool
            ``True`` if a descriptor was found and removed.
        """
        if phase_id in self._phases:
            del self._phases[phase_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, phase_id: str) -> PhaseDescriptor | None:
        """Look up a descriptor by ID.

        Parameters
        ----------
        phase_id:
            The phase ID to search for.

        Returns
        -------
        PhaseDescriptor | None
            The matching descriptor, or ``None`` if not found.
        """
        return self._phases.get(phase_id)

    def list_phases(self) -> list[str]:
        """Return a sorted list of all registered phase IDs.

        Returns
        -------
        list[str]
        """
        return sorted(self._phases.keys())

    def find_by_kind(self, kind: PhaseKind) -> list[PhaseDescriptor]:
        """Return all descriptors whose :attr:`~models.PhaseDescriptor.kind` matches.

        Parameters
        ----------
        kind:
            The :class:`~models.PhaseKind` to filter by.

        Returns
        -------
        list[PhaseDescriptor]
            Descriptors matching *kind*, ordered by name.
        """
        return sorted(
            (d for d in self._phases.values() if d.kind is kind),
            key=lambda d: d.name,
        )

    def find_by_name(self, name: str) -> PhaseDescriptor | None:
        """Return the first descriptor whose :attr:`~models.PhaseDescriptor.name` matches exactly.

        Parameters
        ----------
        name:
            Exact name to search for.

        Returns
        -------
        PhaseDescriptor | None
        """
        for d in self._phases.values():
            if d.name == name:
                return d
        return None

    def count(self) -> int:
        """Return the number of registered descriptors.

        Returns
        -------
        int
        """
        return len(self._phases)

    def all_descriptors(self) -> list[PhaseDescriptor]:
        """Return all registered descriptors as a list, ordered by name.

        Returns
        -------
        list[PhaseDescriptor]
        """
        return sorted(self._phases.values(), key=lambda d: d.name)

    def kind_counts(self) -> dict[PhaseKind, int]:
        """Return a mapping of :class:`~models.PhaseKind` to descriptor count.

        Returns
        -------
        dict[PhaseKind, int]
            All kinds are represented; missing kinds map to ``0``.
        """
        counts: dict[PhaseKind, int] = {k: 0 for k in PhaseKind}
        for d in self._phases.values():
            counts[d.kind] += 1
        return counts

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_registry(self) -> list[str]:
        """Validate the current registry contents.

        Checks performed:

        * All phase IDs are non-empty strings.
        * All phase names are non-empty strings.
        * No two descriptors share the same name.
        * Expected durations are non-negative.

        Returns
        -------
        list[str]
            Empty list if valid; otherwise a list of error messages.
        """
        errors: list[str] = []
        seen_names: set[str] = set()

        for phase_id, d in self._phases.items():
            if not phase_id.strip():
                errors.append("A descriptor has an empty phase_id.")
            if not d.name.strip():
                errors.append(
                    f"Descriptor {phase_id!r} has an empty name."
                )
            if d.name in seen_names:
                errors.append(
                    f"Duplicate descriptor name {d.name!r} detected."
                )
            seen_names.add(d.name)
            if d.expected_duration < 0.0:
                errors.append(
                    f"Descriptor {phase_id!r} ({d.name!r}) has negative "
                    f"expected_duration {d.expected_duration!r}."
                )

        return errors

    def __repr__(self) -> str:  # pragma: no cover
        return f"PhaseRegistry(count={self.count()})"


# ---------------------------------------------------------------------------
# TransitionTriggerCatalog
# ---------------------------------------------------------------------------


class TransitionTriggerCatalog:
    """Static catalog describing every :class:`~models.TransitionTrigger` member.

    The catalog is initialised once with all known trigger members and
    provides query methods for descriptions, severity scores, and
    automatic-vs-manual classification.  Instances are effectively stateless
    value objects; multiple instances are interchangeable.

    Usage example::

        catalog = TransitionTriggerCatalog()
        desc = catalog.get_description(TransitionTrigger.STALL_DETECTED)
        sev  = catalog.get_severity(TransitionTrigger.STALL_DETECTED)
    """

    def __init__(self) -> None:
        """Initialise the catalog with all :class:`~models.TransitionTrigger` members."""
        self._triggers: list[TransitionTrigger] = list(TransitionTrigger)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_description(self, trigger: TransitionTrigger) -> str:
        """Return the human-readable description of *trigger*.

        Parameters
        ----------
        trigger:
            The trigger to describe.

        Returns
        -------
        str
            A multi-sentence description from :data:`TRIGGER_DESCRIPTIONS`.
        """
        return TRIGGER_DESCRIPTIONS.get(
            trigger,
            f"No description available for trigger {trigger.value!r}.",
        )

    def get_severity(self, trigger: TransitionTrigger) -> int:
        """Return the integer severity score of *trigger*.

        Scores are defined on :class:`~models.TransitionTrigger` via
        :meth:`~models.TransitionTrigger.severity`.

        Parameters
        ----------
        trigger:
            The trigger to score.

        Returns
        -------
        int
            Severity score in {1, 2, 3, 4}.
        """
        return trigger.severity()

    def get_severity_label(self, trigger: TransitionTrigger) -> str:
        """Return the human-readable severity label for *trigger*.

        Parameters
        ----------
        trigger:
            The trigger to label.

        Returns
        -------
        str
            A word from :data:`SEVERITY_LABELS` (e.g. ``"critical"``).
        """
        return SEVERITY_LABELS.get(trigger.severity(), "unknown")

    def is_automatic(self, trigger: TransitionTrigger) -> bool:
        """Return ``True`` if *trigger* fires without human involvement.

        Parameters
        ----------
        trigger:
            The trigger to test.

        Returns
        -------
        bool
        """
        return trigger.is_automatic()

    def list_triggers(self) -> list[TransitionTrigger]:
        """Return a copy of all known triggers in definition order.

        Returns
        -------
        list[TransitionTrigger]
        """
        return list(self._triggers)

    def triggers_by_severity(self) -> list[tuple[int, TransitionTrigger]]:
        """Return all triggers sorted by severity (ascending).

        Returns
        -------
        list[tuple[int, TransitionTrigger]]
            Each element is ``(severity_score, trigger)``.
        """
        return sorted(
            ((t.severity(), t) for t in self._triggers),
            key=lambda pair: pair[0],
        )

    def automatic_triggers(self) -> list[TransitionTrigger]:
        """Return triggers that fire automatically (no human involvement).

        Returns
        -------
        list[TransitionTrigger]
        """
        return [t for t in self._triggers if t.is_automatic()]

    def manual_triggers(self) -> list[TransitionTrigger]:
        """Return triggers that require human or scheduled involvement.

        Returns
        -------
        list[TransitionTrigger]
        """
        return [t for t in self._triggers if not t.is_automatic()]

    def summary(self) -> str:
        """Return a multi-line text summary of the catalog.

        Returns
        -------
        str
        """
        lines = ["TransitionTriggerCatalog:", ""]
        for trigger in self._triggers:
            auto = "auto" if trigger.is_automatic() else "manual"
            sev_label = self.get_severity_label(trigger)
            desc_preview = self.get_description(trigger)[:60].rstrip()
            lines.append(
                f"  [{trigger.severity()}:{sev_label:>13s}] ({auto:>6s}) "
                f"{trigger.value:<22s}  {desc_preview}…"
            )
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover
        return f"TransitionTriggerCatalog(triggers={len(self._triggers)})"


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def build_manifest(
    version: str = MANIFEST_VERSION,
    chapter_ref: str = DEFAULT_CHAPTER_REF,
) -> FrontierPhasesManifest:
    """Build a default :class:`FrontierPhasesManifest` covering all phase kinds.

    One :class:`~models.PhaseDescriptor` is created for every member of
    :class:`~models.PhaseKind`, using the standard entry/exit conditions and
    default durations defined in this module's constants.

    Parameters
    ----------
    version:
        Manifest version string.
    chapter_ref:
        Design-document chapter reference.

    Returns
    -------
    FrontierPhasesManifest
        A fully populated manifest ready for use or further customisation.
    """
    manifest = FrontierPhasesManifest(
        version=version,
        chapter_ref=chapter_ref,
    )

    for kind in PhaseKind:
        descriptor = PhaseDescriptor.create(
            name=_standard_phase_name(kind),
            kind=kind,
            entry_conditions=KIND_ENTRY_CONDITIONS.get(kind, ()),
            exit_conditions=KIND_EXIT_CONDITIONS.get(kind, ()),
            expected_duration=KIND_DEFAULT_DURATIONS.get(kind, DEFAULT_PHASE_DURATION),
            metadata={
                "generated_by": "build_manifest",
                "chapter_ref": chapter_ref,
                "is_terminal": kind.is_terminal(),
                "requires_intervention": kind.requires_intervention(),
            },
        )
        manifest.add_phase(descriptor)

    # Populate a minimal transition catalog
    manifest.transition_catalog.update(_build_transition_catalog(manifest))

    return manifest


def _standard_phase_name(kind: PhaseKind) -> str:
    """Return the standard name for a phase of the given kind.

    Parameters
    ----------
    kind:
        The :class:`~models.PhaseKind` to name.

    Returns
    -------
    str
    """
    return f"standard_{kind.value}_phase"


def _build_transition_catalog(manifest: FrontierPhasesManifest) -> dict[str, Any]:
    """Build a minimal transition catalog from the descriptors in *manifest*.

    The catalog maps ``"<from_kind> -> <to_kind>"`` strings to lists of
    recommended :class:`~models.TransitionTrigger` values (as strings).

    Parameters
    ----------
    manifest:
        The manifest whose descriptors define the available phases.

    Returns
    -------
    dict[str, Any]
    """
    catalog: dict[str, Any] = {}

    allowed_transitions: list[tuple[PhaseKind, PhaseKind, list[TransitionTrigger]]] = [
        (
            PhaseKind.EXPLORATION,
            PhaseKind.EXPLOITATION,
            [TransitionTrigger.COVERAGE_THRESHOLD, TransitionTrigger.SCHEDULED],
        ),
        (
            PhaseKind.EXPLOITATION,
            PhaseKind.CONVERGED,
            [TransitionTrigger.COVERAGE_THRESHOLD, TransitionTrigger.BUDGET_EXHAUSTED],
        ),
        (
            PhaseKind.EXPLORATION,
            PhaseKind.STALLED,
            [TransitionTrigger.STALL_DETECTED],
        ),
        (
            PhaseKind.EXPLOITATION,
            PhaseKind.STALLED,
            [TransitionTrigger.STALL_DETECTED],
        ),
        (
            PhaseKind.STALLED,
            PhaseKind.RECOVERY,
            [TransitionTrigger.MANUAL, TransitionTrigger.STALL_DETECTED],
        ),
        (
            PhaseKind.RECOVERY,
            PhaseKind.EXPLORATION,
            [TransitionTrigger.MANUAL],
        ),
        (
            PhaseKind.EXPLORATION,
            PhaseKind.DIVERGED,
            [TransitionTrigger.DIVERSITY_DROP],
        ),
        (
            PhaseKind.DIVERGED,
            PhaseKind.RECOVERY,
            [TransitionTrigger.MANUAL],
        ),
        (
            PhaseKind.EXPLORATION,
            PhaseKind.TRANSITION,
            [TransitionTrigger.SCHEDULED, TransitionTrigger.MANUAL],
        ),
        (
            PhaseKind.TRANSITION,
            PhaseKind.EXPLOITATION,
            [TransitionTrigger.SCHEDULED],
        ),
    ]

    # Build a quick kind→phase_id index
    kind_to_ids: dict[PhaseKind, list[str]] = {k: [] for k in PhaseKind}
    for d in manifest.all_phases():
        kind_to_ids[d.kind].append(d.phase_id)

    for from_kind, to_kind, triggers in allowed_transitions:
        key = f"{from_kind.value} -> {to_kind.value}"
        catalog[key] = {
            "from_kind": from_kind.value,
            "to_kind": to_kind.value,
            "triggers": [t.value for t in triggers],
            "from_phase_ids": kind_to_ids[from_kind],
            "to_phase_ids": kind_to_ids[to_kind],
        }

    return catalog


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def validate_manifest(
    manifest: FrontierPhasesManifest,
) -> tuple[bool, list[str]]:
    """Validate *manifest* and return a ``(is_valid, errors)`` tuple.

    This is a standalone wrapper around :meth:`FrontierPhasesManifest.validate`
    that additionally checks for coverage of all :class:`~models.PhaseKind`
    members and verifies that the transition catalog references valid phase IDs.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if the manifest is valid; otherwise ``(False, errors)``
        where *errors* is a non-empty list of human-readable messages.
    """
    errors: list[str] = manifest.validate()

    # Additional check: all kinds represented
    if not manifest.covers_all_kinds():
        missing = sorted(
            k.value
            for k in PhaseKind
            if not manifest.phases_by_kind(k)
        )
        errors.append(
            f"Manifest is missing descriptors for PhaseKind(s): "
            f"{', '.join(missing)}."
        )

    # Additional check: transition catalog phase_id references
    all_phase_ids = {d.phase_id for d in manifest.all_phases()}
    for key, entry in manifest.transition_catalog.items():
        if not isinstance(entry, dict):
            continue
        for id_list_key in ("from_phase_ids", "to_phase_ids"):
            for pid in entry.get(id_list_key, []):
                if pid not in all_phase_ids:
                    errors.append(
                        f"Transition catalog entry {key!r} references "
                        f"unknown phase_id {pid!r} in {id_list_key!r}."
                    )

    is_valid = len(errors) == 0
    return is_valid, errors


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------


def create_registry_from_manifest(
    manifest: FrontierPhasesManifest,
) -> PhaseRegistry:
    """Populate and return a :class:`PhaseRegistry` from *manifest*.

    Parameters
    ----------
    manifest:
        Source of descriptors.

    Returns
    -------
    PhaseRegistry
        A registry pre-loaded with all descriptors from *manifest*.
    """
    registry = PhaseRegistry()
    registry.register_many(manifest.all_phases())
    return registry


def default_catalog() -> TransitionTriggerCatalog:
    """Return a default :class:`TransitionTriggerCatalog` instance.

    Returns
    -------
    TransitionTriggerCatalog
    """
    return TransitionTriggerCatalog()


def manifest_to_registry(
    manifest: FrontierPhasesManifest,
) -> PhaseRegistry:
    """Alias for :func:`create_registry_from_manifest`.

    Parameters
    ----------
    manifest:
        Source of descriptors.

    Returns
    -------
    PhaseRegistry
    """
    return create_registry_from_manifest(manifest)


# ---------------------------------------------------------------------------
# Public API declaration
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "FrontierPhasesManifest",
    "PhaseRegistry",
    "TransitionTriggerCatalog",
    # Factory / validation
    "build_manifest",
    "validate_manifest",
    "create_registry_from_manifest",
    "manifest_to_registry",
    "default_catalog",
    # Constants
    "MANIFEST_VERSION",
    "DEFAULT_CHAPTER_REF",
    "MANIFEST_MIN_PHASES",
    "MANIFEST_MAX_PHASES",
    "SEVERITY_LABELS",
    "TRIGGER_DESCRIPTIONS",
    "KIND_DEFAULT_DURATIONS",
    "KIND_ENTRY_CONDITIONS",
    "KIND_EXIT_CONDITIONS",
    # Re-exported from models for single-import convenience
    "PhaseDescriptor",
    "PhaseTransitionRecord",
    "PhaseHistory",
    "StallDetector",
    "ConvergenceCertificate",
    "PhaseKind",
    "TransitionTrigger",
    "PhaseHealthStatus",
]
