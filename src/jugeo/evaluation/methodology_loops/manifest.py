"""
Manifest structures for the methodology_loops evaluation package.

This module provides the :class:`MethodologyLoopsManifest` — a structured
registry that aggregates summary entries for all active and historical
:class:`~jugeo.evaluation.methodology_loops.models.MethodologyLoop` instances
within a JuGeo evaluation session.

The manifest pattern mirrors the evidence-manifest approach used throughout
the JuGeo framework: each domain maintains a top-level manifest object that
acts as the canonical index of its constituent records.  Tooling (the
orchestrator, the CLI reporter, and the LaTeX typesetter) queries the manifest
rather than individual loop objects, ensuring consistent ordering and
provenance information.

Key components
--------------
- :class:`MethodologyLoopEntry` — frozen summary entry for a single loop.
- :class:`MethodologyLoopsManifest` — mutable registry of entries.
- :class:`MethodologyManifestBuilder` — fluent builder for constructing
  manifests from collections of loop objects.
- :func:`build_methodology_manifest` — top-level factory function.
- :func:`validate_manifest`, :func:`merge_manifests`,
  :func:`diff_manifests`, :func:`manifest_health_score` — utilities.

copilot: shared-core marker
Theory reference: theory2.tex Ch62
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    # Entry and manifest
    "MethodologyLoopEntry",
    "MethodologyLoopsManifest",
    # Builder
    "MethodologyManifestBuilder",
    # Factory and utilities
    "build_methodology_manifest",
    "validate_manifest",
    "merge_manifests",
    "diff_manifests",
    "manifest_health_score",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (float seconds).

    This thin wrapper exists so that tests can monkeypatch time without
    reaching into the standard library directly.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Generate a universally unique identifier (UUID4) as a plain string.

    Each call produces a cryptographically random 128-bit value formatted
    in the canonical ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`` hex
    representation.

    Returns
    -------
    str
        A new UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The raw floating-point number to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.3, 0.0, 1.0)
    0.0
    >>> _clamp(0.7, 0.0, 1.0)
    0.7
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

# Local models import (guarded so this module can be loaded standalone)
try:
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase,
        LoopStatus,
        MethodologyLoop,
        FormalizationLoop,
        ImplementationLoop,
        FalsificationLoop,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# MethodologyLoopEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class MethodologyLoopEntry:
    """Immutable summary entry for a single :class:`MethodologyLoop` in the
    manifest.

    :class:`MethodologyLoopEntry` is a *read-optimised snapshot* of the most
    important properties of a methodology loop at the moment it was registered
    in the manifest.  It is intentionally denormalised so that manifest queries
    (filtering by status, phase, health) do not require loading the full loop
    object.

    Attributes
    ----------
    entry_id : str
        Globally unique identifier for this manifest entry (not the loop id).
    loop_id : str
        Identifier of the underlying :class:`MethodologyLoop` instance.
    loop_kind : str
        Human-readable kind label: ``"base"``, ``"formalization"``,
        ``"implementation"``, or ``"falsification"``.
    phase : str
        The :attr:`~jugeo.evaluation.methodology_loops.models.LoopPhase`
        value string at the time of registration.
    status : str
        The :attr:`~jugeo.evaluation.methodology_loops.models.LoopStatus`
        value string at the time of registration.
    coverage_score : float
        The test-coverage or specification-completeness score in [0, 1] at the
        time of registration.  Interpretation depends on ``loop_kind``.
    falsification_rate : float
        The fraction of falsification attempts that produced counter-examples,
        in [0, 1].  ``0.0`` for non-falsification loops.
    spec_version : str
        The specification version string (e.g. ``"0.3.1"``).  Empty string
        if not applicable.
    created_at : float
        Unix epoch timestamp when this entry was created.
    metadata : dict[str, Any]
        Arbitrary additional data embedded at registration time.
    """

    entry_id: str
    loop_id: str
    loop_kind: str
    phase: str
    status: str
    coverage_score: float
    falsification_rate: float
    spec_version: str
    created_at: float
    metadata: dict[str, Any]

    def __init__(
        self,
        *,
        loop_id: str,
        phase: Any,
        status: Any,
        coverage: float | None = None,
        coverage_score: float | None = None,
        falsification_rate: float = 0.0,
        loop_kind: str = "base",
        spec_version: str = "",
        created_at: float | None = None,
        metadata: Optional[dict[str, Any]] = None,
        entry_id: str | None = None,
    ) -> None:
        object.__setattr__(self, "entry_id", entry_id or _uid())
        object.__setattr__(self, "loop_id", str(loop_id))
        object.__setattr__(self, "loop_kind", str(loop_kind))
        object.__setattr__(self, "phase", getattr(phase, "value", phase))
        object.__setattr__(self, "status", getattr(status, "value", status))
        score = coverage_score if coverage_score is not None else coverage
        object.__setattr__(self, "coverage_score", float(0.0 if score is None else score))
        object.__setattr__(self, "falsification_rate", float(falsification_rate))
        object.__setattr__(self, "spec_version", str(spec_version))
        object.__setattr__(self, "created_at", float(_utcnow() if created_at is None else created_at))
        object.__setattr__(self, "metadata", dict(metadata or {}))

    @property
    def coverage(self) -> float:
        """Compatibility alias for ``coverage_score``."""
        return self.coverage_score

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_healthy(
        self,
        min_coverage: float = 0.70,
        max_falsification_rate: float = 0.20,
    ) -> bool:
        """Return ``True`` if this entry meets the health criteria.

        An entry is considered *healthy* when:

        1. Its :attr:`coverage_score` is at or above *min_coverage*, and
        2. Its :attr:`falsification_rate` is at or below
           *max_falsification_rate* (counter-examples are bad), and
        3. Its :attr:`status` is not ``"failed"``.

        Parameters
        ----------
        min_coverage:
            Minimum acceptable coverage score.  Defaults to ``0.70``.
        max_falsification_rate:
            Maximum acceptable falsification rate (counter-example fraction).
            Defaults to ``0.20``.

        Returns
        -------
        bool
        """
        if self.status == "failed":
            return False
        if self.coverage_score < min_coverage:
            return False
        if self.falsification_rate < max_falsification_rate:
            return False
        return True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A fully JSON-serialisable mapping that round-trips through
            :meth:`from_json`.
        """
        return {
            "entry_id": self.entry_id,
            "loop_id": self.loop_id,
            "loop_kind": self.loop_kind,
            "phase": self.phase,
            "status": self.status,
            "coverage_score": self.coverage_score,
            "falsification_rate": self.falsification_rate,
            "spec_version": self.spec_version,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MethodologyLoopEntry":
        """Deserialise a :class:`MethodologyLoopEntry` from a JSON dictionary.

        Parameters
        ----------
        data:
            Mapping as produced by :meth:`to_json`.

        Returns
        -------
        MethodologyLoopEntry
            A new frozen instance.
        """
        return cls(
            entry_id=str(data.get("entry_id", _uid())),
            loop_id=str(data.get("loop_id", "")),
            loop_kind=str(data.get("loop_kind", "base")),
            phase=str(data.get("phase", "formalization")),
            status=str(data.get("status", "idle")),
            coverage_score=float(data.get("coverage_score", 0.0)),
            falsification_rate=float(data.get("falsification_rate", 0.0)),
            spec_version=str(data.get("spec_version", "")),
            created_at=float(data.get("created_at", _utcnow())),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_loop(cls, loop: Any) -> "MethodologyLoopEntry":
        """Compatibility constructor from a loop object."""
        entry = _entry_from_loop(loop)
        if entry is None:
            raise ValueError("Unsupported loop object for MethodologyLoopEntry.from_loop()")
        return entry

    def summarize(self) -> str:
        """Return a one-line human-readable summary of this entry.

        Returns
        -------
        str
            E.g. ``"[base] loop-abc phase=evaluation status=converged cov=0.92"``.
        """
        health = "✓" if self.is_healthy() else "✗"
        return (
            f"{health} [{self.loop_kind}] {self.loop_id[:8]}… "
            f"phase={self.phase} status={self.status} "
            f"cov={self.coverage_score:.2f} "
            f"falsif={self.falsification_rate:.2f} "
            f"spec={self.spec_version}"
        )


# ---------------------------------------------------------------------------
# MethodologyLoopsManifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MethodologyLoopsManifest:
    """Mutable registry of :class:`MethodologyLoopEntry` records.

    :class:`MethodologyLoopsManifest` is the top-level index for all
    methodology loops tracked within a JuGeo evaluation session.  It provides
    CRUD operations, filtering, health-checking, merging, and diffing
    capabilities, as well as LaTeX rendering for inclusion in theory2.tex Ch62
    appendices.

    Attributes
    ----------
    manifest_id : str
        Globally unique identifier for this manifest instance.
    entries : list[MethodologyLoopEntry]
        Ordered list of registered loop entries.
    created_at : float
        Unix epoch timestamp when this manifest was created.
    updated_at : float
        Unix epoch timestamp of the most recent mutation.
    metadata : dict[str, Any]
        Arbitrary additional data associated with this manifest (e.g. session
        identifier, originating orchestrator ID, human-assigned label).
    """

    manifest_id: str = field(default_factory=_uid)
    entries: list[MethodologyLoopEntry] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)
    updated_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_entry(self, entry: MethodologyLoopEntry) -> None:
        """Register a new :class:`MethodologyLoopEntry` in the manifest.

        If an entry with the same :attr:`~MethodologyLoopEntry.entry_id`
        already exists it is *replaced* (to allow re-registration after a loop
        update).

        Parameters
        ----------
        entry:
            The frozen entry to register.
        """
        # Replace existing entry with the same entry_id if present.
        for i, existing in enumerate(self.entries):
            if existing.entry_id == entry.entry_id:
                self.entries[i] = entry
                self.updated_at = _utcnow()
                return
        self.entries.append(entry)
        self.updated_at = _utcnow()

    def get_entry(self, entry_id: str) -> Optional[MethodologyLoopEntry]:
        """Retrieve an entry by its :attr:`~MethodologyLoopEntry.entry_id`.

        Parameters
        ----------
        entry_id:
            The unique identifier of the entry to retrieve.

        Returns
        -------
        MethodologyLoopEntry or None
            The matching entry, or ``None`` if not found.
        """
        for entry in self.entries:
            if entry.entry_id == entry_id or entry.loop_id == entry_id:
                return entry
        return None

    def remove_entry(self, entry_id: str) -> bool:
        """Remove an entry from the manifest by its identifier.

        Parameters
        ----------
        entry_id:
            Identifier of the entry to remove.

        Returns
        -------
        bool
            ``True`` if an entry was found and removed, ``False`` otherwise.
        """
        original_len = len(self.entries)
        self.entries = [
            e for e in self.entries if e.entry_id != entry_id and e.loop_id != entry_id
        ]
        if len(self.entries) < original_len:
            self.updated_at = _utcnow()
            return True
        return False

    def list_entries(self) -> list[MethodologyLoopEntry]:
        """Return a shallow copy of all registered entries.

        Returns
        -------
        list[MethodologyLoopEntry]
            All entries in registration order.
        """
        return list(self.entries)

    def count(self) -> int:
        """Return the number of registered entries.

        Returns
        -------
        int
        """
        return len(self.entries)

    def entry_count(self) -> int:
        """Compatibility alias for :meth:`count`."""
        return self.count()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_by_status(self, status: str) -> list[MethodologyLoopEntry]:
        """Return all entries whose :attr:`~MethodologyLoopEntry.status`
        matches *status*.

        Parameters
        ----------
        status:
            Status string to match (e.g. ``"converged"``, ``"running"``).

        Returns
        -------
        list[MethodologyLoopEntry]
            All entries with the given status, in registration order.
        """
        return [e for e in self.entries if e.status == status]

    def filter_by_phase(self, phase: str) -> list[MethodologyLoopEntry]:
        """Return all entries whose :attr:`~MethodologyLoopEntry.phase`
        matches *phase*.

        Parameters
        ----------
        phase:
            Phase string to match (e.g. ``"evaluation"``,
            ``"falsification"``).

        Returns
        -------
        list[MethodologyLoopEntry]
            All entries with the given phase, in registration order.
        """
        return [e for e in self.entries if e.phase == phase]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(
        self,
        min_coverage: float = 0.70,
        max_falsification_rate: float = 0.20,
    ) -> dict[str, Any]:
        """Run a health check across all registered entries.

        Evaluates each entry using :meth:`MethodologyLoopEntry.is_healthy`
        and returns aggregated statistics.

        Parameters
        ----------
        min_coverage:
            Coverage threshold forwarded to :meth:`MethodologyLoopEntry.is_healthy`.
        max_falsification_rate:
            Falsification-rate ceiling forwarded to
            :meth:`MethodologyLoopEntry.is_healthy`.

        Returns
        -------
        dict[str, Any]
            Keys: ``"total"``, ``"healthy"``, ``"unhealthy"``,
            ``"health_fraction"``, ``"failed_ids"``.
        """
        total = len(self.entries)
        if total == 0:
            return {
                "total": 0,
                "healthy": 0,
                "unhealthy": 0,
                "health_fraction": 1.0,
                "failed_ids": [],
            }
        healthy_entries = [
            e for e in self.entries
            if e.is_healthy(min_coverage, max_falsification_rate)
        ]
        unhealthy = [
            e.entry_id for e in self.entries
            if not e.is_healthy(min_coverage, max_falsification_rate)
        ]
        return {
            "total": total,
            "healthy": len(healthy_entries),
            "unhealthy": len(unhealthy),
            "health_fraction": len(healthy_entries) / total,
            "failed_ids": unhealthy,
        }

    # ------------------------------------------------------------------
    # Merge and diff
    # ------------------------------------------------------------------

    def merge(self, other: "MethodologyLoopsManifest") -> "MethodologyLoopsManifest":
        """Merge *other* into a new manifest containing all entries from both.

        Entries are merged by :attr:`~MethodologyLoopEntry.entry_id`.  When
        the same entry_id appears in both manifests, the entry from *other* is
        preferred (last-write-wins semantics).

        Parameters
        ----------
        other:
            Another :class:`MethodologyLoopsManifest` whose entries should be
            incorporated.

        Returns
        -------
        MethodologyLoopsManifest
            A new manifest containing the union of both entry sets.
        """
        merged = MethodologyLoopsManifest(
            manifest_id=_uid(),
            metadata={
                "merged_from": [self.manifest_id, other.manifest_id],
                "merged_at": _utcnow(),
            },
        )
        # Add self's entries first, then other's (which overwrite duplicates).
        for entry in self.entries:
            merged.add_entry(entry)
        for entry in other.entries:
            merged.add_entry(entry)
        return merged

    def diff(
        self, other: "MethodologyLoopsManifest"
    ) -> dict[str, list[str]]:
        """Compute the symmetric difference between this manifest and *other*.

        Returns
        -------
        dict[str, list[str]]
            A mapping with three keys:

            - ``"only_in_self"`` — entry_ids present in *self* but not *other*.
            - ``"only_in_other"`` — entry_ids present in *other* but not *self*.
            - ``"in_both"`` — entry_ids present in both manifests.
        """
        self_ids = {e.entry_id for e in self.entries}
        other_ids = {e.entry_id for e in other.entries}
        return {
            "only_in_self": sorted(self_ids - other_ids),
            "only_in_other": sorted(other_ids - self_ids),
            "in_both": sorted(self_ids & other_ids),
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _summary_data(self) -> dict[str, Any]:
        """Build the structured summary data for the manifest."""
        total = len(self.entries)
        if total == 0:
            return {
                "manifest_id": self.manifest_id,
                "total_entries": 0,
                "status_breakdown": {},
                "phase_breakdown": {},
                "avg_coverage": 0.0,
                "avg_falsification_rate": 0.0,
                "health": self.health_check(),
            }

        status_counts: dict[str, int] = {}
        phase_counts: dict[str, int] = {}
        coverage_sum = 0.0
        falsif_sum = 0.0

        for entry in self.entries:
            status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
            phase_counts[entry.phase] = phase_counts.get(entry.phase, 0) + 1
            coverage_sum += entry.coverage_score
            falsif_sum += entry.falsification_rate

        return {
            "manifest_id": self.manifest_id,
            "total_entries": total,
            "status_breakdown": status_counts,
            "phase_breakdown": phase_counts,
            "avg_coverage": round(coverage_sum / total, 4),
            "avg_falsification_rate": round(falsif_sum / total, 4),
            "health": self.health_check(),
        }

    def summary_report(self) -> str:
        """Build a human-readable summary report of the manifest.

        Returns
        -------
        dict[str, Any]
            A mapping with aggregated statistics covering all registered
            entries, suitable for JSON serialisation and display in the CLI.
        """
        report = self._summary_data()
        return (
            f"MethodologyLoopsManifest(total_entries={report['total_entries']}, "
            f"statuses={report['status_breakdown']}, phases={report['phase_breakdown']}, "
            f"avg_coverage={report['avg_coverage']:.4f}, "
            f"avg_falsification_rate={report['avg_falsification_rate']:.4f}, "
            f"health={report['health']})"
        )

    def summarize(self) -> str:
        """Compatibility alias returning a one-line manifest summary."""
        return self.summary_report()

    def render_tex(self) -> str:
        """Render a LaTeX ``longtable`` summarising all manifest entries.

        The table is suitable for inclusion in a theory2.tex Ch62 appendix.
        Each row represents one :class:`MethodologyLoopEntry`.

        Returns
        -------
        str
            A complete LaTeX ``longtable`` environment string.
        """
        header = (
            r"\begin{longtable}{llllrr}" + "\n"
            r"  \toprule" + "\n"
            r"  \textbf{Loop ID} & \textbf{Kind} & \textbf{Phase} & "
            r"\textbf{Status} & \textbf{Coverage} & \textbf{Falsif.} \\" + "\n"
            r"  \midrule" + "\n"
            r"  \endhead" + "\n"
        )
        rows = []
        for e in self.entries:
            short_id = e.loop_id[:8] + "…" if len(e.loop_id) > 8 else e.loop_id
            rows.append(
                f"  \\texttt{{{short_id}}} & {e.loop_kind} & "
                f"\\texttt{{{e.phase}}} & \\texttt{{{e.status}}} & "
                f"{e.coverage_score:.3f} & {e.falsification_rate:.3f} \\\\"
            )
        footer = (
            r"  \bottomrule" + "\n"
            r"\end{longtable}"
        )
        return header + "\n".join(rows) + "\n" + footer

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the manifest to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trippable via :meth:`from_json`.
        """
        return {
            "manifest_id": self.manifest_id,
            "entries": [e.to_json() for e in self.entries],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MethodologyLoopsManifest":
        """Deserialise from a JSON dictionary produced by :meth:`to_json`.

        Parameters
        ----------
        data:
            Mapping with keys matching the class attributes.

        Returns
        -------
        MethodologyLoopsManifest
            A fully populated manifest.
        """
        obj = cls.__new__(cls)
        obj.manifest_id = str(data.get("manifest_id", _uid()))
        obj.entries = [
            MethodologyLoopEntry.from_json(e) for e in data.get("entries", [])
        ]
        obj.created_at = float(data.get("created_at", _utcnow()))
        obj.updated_at = float(data.get("updated_at", _utcnow()))
        obj.metadata = dict(data.get("metadata", {}))
        return obj


# ---------------------------------------------------------------------------
# MethodologyManifestBuilder
# ---------------------------------------------------------------------------


class MethodologyManifestBuilder:
    """Fluent builder for constructing :class:`MethodologyLoopsManifest` objects.

    :class:`MethodologyManifestBuilder` implements the *builder* design pattern,
    allowing manifests to be assembled incrementally via a chain of ``with_*``
    calls before :meth:`build` produces the final immutable-manifest object.

    The builder accumulates entries and metadata and applies light validation
    before handing off to :class:`MethodologyLoopsManifest`.

    Example
    -------
    .. code-block:: python

        manifest = (
            MethodologyManifestBuilder()
            .with_entry(entry1)
            .with_entry(entry2)
            .with_metadata("session_id", "abc-123")
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialise an empty builder with a fresh manifest ID."""
        self._manifest_id: str = _uid()
        self._entries: list[MethodologyLoopEntry] = []
        self._metadata: dict[str, Any] = {}
        self._validation_errors: list[str] = []

    def with_entry(self, entry: MethodologyLoopEntry) -> "MethodologyManifestBuilder":
        """Add a :class:`MethodologyLoopEntry` to the builder's entry queue.

        Parameters
        ----------
        entry:
            The frozen entry to add.  Duplicate ``entry_id`` values will be
            deduplicated at :meth:`build` time (last-write-wins).

        Returns
        -------
        MethodologyManifestBuilder
            ``self``, for method chaining.
        """
        self._entries.append(entry)
        return self

    def add_entry(self, entry: MethodologyLoopEntry) -> "MethodologyManifestBuilder":
        """Compatibility alias for :meth:`with_entry`."""
        return self.with_entry(entry)

    def with_metadata(
        self,
        key: str | dict[str, Any],
        value: Any = None,
    ) -> "MethodologyManifestBuilder":
        """Set a metadata key–value pair on the manifest under construction.

        Parameters
        ----------
        key:
            Metadata key string.
        value:
            Metadata value.  Must be JSON-serialisable.

        Returns
        -------
        MethodologyManifestBuilder
            ``self``, for method chaining.
        """
        if isinstance(key, dict):
            self._metadata.update(key)
            return self
        self._metadata[key] = value
        return self

    def build(self) -> MethodologyLoopsManifest:
        """Finalise and return the constructed :class:`MethodologyLoopsManifest`.

        Entries with duplicate ``entry_id`` values are deduplicated with
        last-write-wins semantics.  The builder's internal state is
        *not* reset after calling :meth:`build`; the same builder instance
        may be used to produce further manifests.

        Returns
        -------
        MethodologyLoopsManifest
            A new manifest containing all accumulated entries and metadata.
        """
        manifest = MethodologyLoopsManifest(
            manifest_id=self._manifest_id,
            metadata=dict(self._metadata),
        )
        for entry in self._entries:
            manifest.add_entry(entry)
        return manifest

    @classmethod
    def from_loops(
        cls,
        loops: Sequence[Any],
        *,
        session_id: str = "",
        label: str = "",
    ) -> "MethodologyManifestBuilder":
        """Construct a builder pre-populated from a sequence of loop objects.

        This class method inspects each item in *loops* and attempts to extract
        the relevant fields for a :class:`MethodologyLoopEntry`.  It handles
        instances of :class:`~jugeo.evaluation.methodology_loops.models.MethodologyLoop`,
        :class:`~jugeo.evaluation.methodology_loops.models.FormalizationLoop`,
        :class:`~jugeo.evaluation.methodology_loops.models.ImplementationLoop`,
        and :class:`~jugeo.evaluation.methodology_loops.models.FalsificationLoop`.
        Unrecognised objects are skipped with a warning.

        Parameters
        ----------
        loops:
            A sequence of loop objects to convert into manifest entries.
        session_id:
            Optional session identifier to embed in manifest metadata.
        label:
            Optional human-assigned label for the manifest.

        Returns
        -------
        MethodologyManifestBuilder
            A builder with one entry per recognised loop object.
        """
        builder = cls()
        if session_id:
            builder.with_metadata("session_id", session_id)
        if label:
            builder.with_metadata("label", label)
        builder.with_metadata("generated_at", _utcnow())

        for loop in loops:
            entry = _entry_from_loop(loop)
            if entry is not None:
                builder.with_entry(entry)

        return builder

    def validate(self) -> list[str]:
        """Validate the accumulated state and return a list of issue strings.

        Returns
        -------
        list[str]
            An empty list if the builder state is valid; otherwise a list of
            problem descriptions.
        """
        issues: list[str] = []
        entry_ids = [e.entry_id for e in self._entries]
        seen: set[str] = set()
        duplicates: list[str] = []
        for eid in entry_ids:
            if eid in seen:
                duplicates.append(eid)
            seen.add(eid)
        if duplicates:
            issues.append(
                f"Duplicate entry_ids will be silently overwritten: {duplicates}"
            )
        if not self._entries:
            issues.append("Builder has no entries; the resulting manifest will be empty.")
        return issues


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _entry_from_loop(loop: Any) -> Optional[MethodologyLoopEntry]:
    """Attempt to build a :class:`MethodologyLoopEntry` from an arbitrary loop object.

    This function uses duck-typing to inspect *loop* for known attributes
    from the methodology loop model classes.  It returns ``None`` if the
    object is unrecognised or lacks a ``loop_id`` attribute.

    Parameters
    ----------
    loop:
        An object that may be a :class:`MethodologyLoop`,
        :class:`FormalizationLoop`, :class:`ImplementationLoop`, or
        :class:`FalsificationLoop`.

    Returns
    -------
    MethodologyLoopEntry or None
        A populated entry, or ``None`` if the loop object cannot be
        introspected.
    """
    loop_id = getattr(loop, "loop_id", None)
    if not loop_id:
        return None

    # Determine loop kind and extract kind-specific metrics.
    coverage_score = 0.0
    falsification_rate = 0.0
    spec_version = ""
    loop_kind = "base"

    # MethodologyLoop — has .state with .phase and .status
    if hasattr(loop, "state") and hasattr(loop, "config"):
        loop_kind = "base"
        state = loop.state
        phase = getattr(state, "phase", None)
        phase_str = phase.value if hasattr(phase, "value") else str(phase)
        status = getattr(state, "status", None)
        status_str = status.value if hasattr(status, "value") else str(status)
    # FormalizationLoop — has completeness_score and spec_version
    elif hasattr(loop, "completeness_score") and hasattr(loop, "spec_version"):
        loop_kind = "formalization"
        phase_str = "formalization"
        status_str = "idle"
        coverage_score = float(getattr(loop, "completeness_score", 0.0))
        spec_version = str(getattr(loop, "spec_version", ""))
    # ImplementationLoop — has test_coverage and build_status
    elif hasattr(loop, "test_coverage") and hasattr(loop, "build_status"):
        loop_kind = "implementation"
        phase_str = "implementation"
        status_str = "running" if getattr(loop, "build_status", "") == "passing" else "idle"
        coverage_score = float(getattr(loop, "test_coverage", 0.0))
    # FalsificationLoop — has falsification_attempts and budget_used
    elif hasattr(loop, "falsification_attempts") and hasattr(loop, "budget_used"):
        loop_kind = "falsification"
        phase_str = "falsification"
        status_str = "running"
        falsification_rate = (
            len(getattr(loop, "counterexamples", []))
            / max(1, len(getattr(loop, "falsification_attempts", [1])))
        )
    else:
        # Unknown loop type — still extract what we can.
        loop_kind = "unknown"
        phase_str = str(getattr(loop, "phase", "unknown"))
        status_str = str(getattr(loop, "status", "unknown"))

    return MethodologyLoopEntry(
        entry_id=_uid(),
        loop_id=str(loop_id),
        loop_kind=loop_kind,
        phase=phase_str,
        status=status_str,
        coverage_score=_clamp(coverage_score, 0.0, 1.0),
        falsification_rate=_clamp(falsification_rate, 0.0, 1.0),
        spec_version=spec_version,
        created_at=_utcnow(),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Public factory function
# ---------------------------------------------------------------------------


def build_methodology_manifest(
    loops: Optional[list[Any]] = None,
    *,
    session_id: str = "",
    label: str = "",
    extra_metadata: Optional[dict[str, Any]] = None,
) -> MethodologyLoopsManifest:
    """Factory function: build a :class:`MethodologyLoopsManifest` from a list
    of loop objects.

    This is the primary entry point for constructing manifests in production
    code.  It delegates to :class:`MethodologyManifestBuilder` and adds any
    *extra_metadata* key–value pairs after construction.

    Parameters
    ----------
    loops:
        A list of loop objects (any combination of
        :class:`~jugeo.evaluation.methodology_loops.models.MethodologyLoop`,
        :class:`~jugeo.evaluation.methodology_loops.models.FormalizationLoop`,
        :class:`~jugeo.evaluation.methodology_loops.models.ImplementationLoop`,
        :class:`~jugeo.evaluation.methodology_loops.models.FalsificationLoop`).
        Unrecognised objects are silently skipped.
    session_id:
        Optional session identifier embedded in the manifest metadata.
    label:
        Optional human-assigned label for the manifest.
    extra_metadata:
        Optional mapping of additional metadata key–value pairs to embed in
        the manifest.

    Returns
    -------
    MethodologyLoopsManifest
        A fully populated manifest ready for querying, serialisation, or
        LaTeX rendering.

    Examples
    --------
    .. code-block:: python

        manifest = build_methodology_manifest(
            loops=[loop1, formalization_loop, impl_loop],
            session_id="session-abc",
            label="sprint-42-evaluation",
        )
        print(manifest.summary_report())
    """
    builder = MethodologyManifestBuilder.from_loops(
        loops or [], session_id=session_id, label=label
    )
    if extra_metadata:
        for k, v in extra_metadata.items():
            builder.with_metadata(k, v)
    return builder.build()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def validate_manifest(manifest: MethodologyLoopsManifest) -> list[str]:
    """Validate a :class:`MethodologyLoopsManifest` and return a list of issues.

    Performs structural and semantic validation:

    - Checks that all entry_ids are unique.
    - Checks that all loop_ids are non-empty strings.
    - Checks that all score fields are in the valid [0, 1] range.
    - Checks that phase and status strings are recognised LoopPhase /
      LoopStatus values.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    list[str]
        An empty list if all checks pass; otherwise a list of human-readable
        issue descriptions.
    """
    issues: list[str] = []
    seen_entry_ids: set[str] = set()
    seen_loop_ids: set[str] = set()

    valid_phases = {"formalization", "implementation", "evaluation",
                    "falsification", "revision", "unknown"}
    valid_statuses = {"idle", "running", "converged", "stalled", "failed",
                      "unknown"}

    for i, entry in enumerate(manifest.entries):
        prefix = f"Entry[{i}] (entry_id={entry.entry_id!r})"

        if entry.entry_id in seen_entry_ids:
            issues.append(f"{prefix}: duplicate entry_id.")
        seen_entry_ids.add(entry.entry_id)

        if not entry.loop_id:
            issues.append(f"{prefix}: loop_id is empty.")
        if entry.loop_id in seen_loop_ids:
            issues.append(f"{prefix}: duplicate loop_id {entry.loop_id!r}.")
        seen_loop_ids.add(entry.loop_id)

        if not (0.0 <= entry.coverage_score <= 1.0):
            issues.append(
                f"{prefix}: coverage_score {entry.coverage_score} out of [0,1]."
            )
        if not (0.0 <= entry.falsification_rate <= 1.0):
            issues.append(
                f"{prefix}: falsification_rate {entry.falsification_rate} out of [0,1]."
            )
        if entry.phase not in valid_phases:
            issues.append(
                f"{prefix}: unrecognised phase {entry.phase!r}. "
                f"Valid values: {sorted(valid_phases)}."
            )
        if entry.status not in valid_statuses:
            issues.append(
                f"{prefix}: unrecognised status {entry.status!r}. "
                f"Valid values: {sorted(valid_statuses)}."
            )

    return issues


def merge_manifests(
    manifests: Sequence[MethodologyLoopsManifest],
) -> MethodologyLoopsManifest:
    """Merge an arbitrary number of manifests into a single combined manifest.

    Merging is performed sequentially using last-write-wins semantics: when
    two manifests share an entry_id, the entry from the *later* manifest in
    the sequence wins.

    Parameters
    ----------
    manifests:
        An ordered sequence of :class:`MethodologyLoopsManifest` objects to
        merge.  Must contain at least one element.

    Returns
    -------
    MethodologyLoopsManifest
        A new manifest containing the union of all entries across all input
        manifests.

    Raises
    ------
    ValueError
        If *manifests* is empty.
    """
    if not manifests:
        raise ValueError("merge_manifests requires at least one manifest.")
    result = manifests[0]
    for other in manifests[1:]:
        result = result.merge(other)
    return result


def diff_manifests(
    left: MethodologyLoopsManifest,
    right: MethodologyLoopsManifest,
) -> dict[str, Any]:
    """Compute a detailed diff between two manifests.

    This function extends :meth:`MethodologyLoopsManifest.diff` by including
    *changed entries* — entries that appear in both manifests but have
    different field values.

    Parameters
    ----------
    left:
        The "before" manifest.
    right:
        The "after" manifest.

    Returns
    -------
    dict[str, Any]
        A mapping with keys:

        - ``"only_in_left"`` — entry_ids only in *left*.
        - ``"only_in_right"`` — entry_ids only in *right*.
        - ``"in_both_unchanged"`` — entry_ids in both with identical content.
        - ``"in_both_changed"`` — entry_ids in both with differing content.
    """
    symmetric = left.diff(right)
    in_both = symmetric["in_both"]

    left_map = {e.entry_id: e for e in left.entries}
    right_map = {e.entry_id: e for e in right.entries}

    unchanged = []
    changed = []
    for eid in in_both:
        le = left_map[eid]
        re = right_map[eid]
        if le.to_json() == re.to_json():
            unchanged.append(eid)
        else:
            changed.append(eid)

    return {
        "only_in_left": symmetric["only_in_self"],
        "only_in_right": symmetric["only_in_other"],
        "in_both_unchanged": unchanged,
        "in_both_changed": changed,
    }


def manifest_health_score(
    manifest: MethodologyLoopsManifest,
    min_coverage: float = 0.70,
    max_falsification_rate: float = 0.20,
) -> float:
    """Compute a scalar health score for an entire manifest.

    The health score is the fraction of entries that pass
    :meth:`MethodologyLoopEntry.is_healthy`.  A score of 1.0 means all
    registered loops are healthy; 0.0 means none are.

    Parameters
    ----------
    manifest:
        The manifest to score.
    min_coverage:
        Coverage threshold forwarded to :meth:`MethodologyLoopEntry.is_healthy`.
    max_falsification_rate:
        Falsification-rate ceiling forwarded to
        :meth:`MethodologyLoopEntry.is_healthy`.

    Returns
    -------
    float
        A value in [0, 1].  Returns ``1.0`` for empty manifests (vacuously
        healthy).
    """
    if not manifest.entries:
        return 1.0
    healthy = sum(
        1 for e in manifest.entries
        if e.is_healthy(min_coverage, max_falsification_rate)
    )
    return healthy / len(manifest.entries)
