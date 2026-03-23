"""Evidence manifest for the JuGeo discovery engine — theory2.tex Ch58.

This module implements manifest construction and management for the discovery
pipeline.  A manifest is an ordered collection of evidence records that
documents what evidence was consumed, produced, or transformed during a
discovery pipeline run.

Theory reference: theory2.tex Ch58 §4 — Discovery Evidence Manifests.

copilot: shared-core marker

Purpose and Design
==================

Every discovery pipeline run produces a :class:`DiscoveryEngineManifest` that
captures a structured audit trail.  The manifest serves three purposes:

1. **Reproducibility** — the input evidence entries document exactly which
   data drove the pipeline run, enabling deterministic replay.
2. **Auditability** — intermediate and diagnostic entries record the
   intermediate state of the pipeline, supporting post-hoc debugging.
3. **Integration** — output entries document what was added to the pack
   registry or other downstream systems, enabling downstream consumers to
   reconcile their state.

Manifest Lifecycle
==================

A manifest begins life in the :attr:`ManifestStatus.DRAFT` state, in which
entries may be added freely.  Once the pipeline run completes, the manifest
is :meth:`~DiscoveryEngineManifest.sealed` (transition to
:attr:`ManifestStatus.SEALED`), after which no further entries may be added.
Sealed manifests may subsequently be :meth:`~DiscoveryEngineManifest.archived`
(transition to :attr:`ManifestStatus.ARCHIVED`) for long-term storage.

::

    DRAFT ──seal()──► SEALED ──archive()──► ARCHIVED

Fluent Builder
==============

The :class:`ManifestBuilder` class provides a fluent interface for
constructing manifests::

    from jugeo.ideation.discovery_engine.manifest import ManifestBuilder

    manifest = (
        ManifestBuilder(pipeline_run_id="run-001")
        .add_input("Candidate C1", source="corpus", confidence=0.9)
        .add_intermediate("Novelty score 0.75", source="novelty_stage")
        .add_output("Theorem T1 promoted", source="promotion_stage")
        .add_audit("Run completed in 1.2s", source="pipeline")
        .with_metadata("environment", "production")
        .build()
    )

    print(manifest.entry_count)    # 4
    print(manifest.status)         # ManifestStatus.SEALED

Integration with evidence subsystem
====================================

When the full JuGeo evidence subsystem is available, manifests produced by
this module can be bridged to ``jugeo.evidence.manifests.Manifest`` objects
via the guarded imports at the top of this module.  When the evidence
subsystem is absent, the discovery engine operates in standalone mode with
the native :class:`DiscoveryEngineManifest` type.

Merging Manifests
=================

Multiple partial manifests (e.g. produced by parallel sub-pipeline branches)
can be merged into a single combined manifest via :func:`merge_manifests`::

    combined = merge_manifests([manifest_a, manifest_b, manifest_c])

The combined manifest is returned as a new DRAFT manifest.  Entries are
concatenated in the order the source manifests appear in the input list.
Merging sealed manifests is allowed; the result is DRAFT and must be
explicitly sealed by the caller.

Notes
=====

Thread safety: :class:`DiscoveryEngineManifest` is not thread-safe.  Do not
share a manifest instance between threads without external synchronisation.

All timestamps in this module are UTC POSIX timestamps (float seconds since
the Unix epoch) produced by :func:`_utcnow`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import time
import uuid
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Guarded cross-module imports
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

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Helper utilities
    "_utcnow",
    "_uid",
    "_clamp",
    # Enumerations
    "ManifestStatus",
    "EvidenceEntryKind",
    # Data classes
    "EvidenceEntry",
    "DiscoveryEngineManifest",
    "ManifestBuilder",
    # Factory / utility functions
    "build_discovery_manifest",
    "merge_manifests",
    "validate_manifest",
]


# ---------------------------------------------------------------------------
# §1 Helper utilities
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return current UTC timestamp as a float (seconds since epoch).

    Thin wrapper around :func:`time.time` kept as a named helper so that
    tests can monkeypatch it without patching the standard library directly.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC).

    Example
    -------
    ::

        t = _utcnow()
        assert isinstance(t, float)
        assert t > 1_700_000_000.0  # sanity: after 2023
    """
    return time.time()


def _uid() -> str:
    """Generate a short unique identifier string.

    Produces a RFC-4122 UUID4 string (36 characters, hyphen-separated).

    Returns
    -------
    str
        UUID4 string.

    Example
    -------
    ::

        id1 = _uid()
        id2 = _uid()
        assert id1 != id2
        assert "-" in id1
    """
    return str(uuid.uuid4())


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to the closed interval ``[lower, upper]``.

    Parameters
    ----------
    value:
        The value to clamp.
    lower:
        Lower bound (inclusive).  Default ``0.0``.
    upper:
        Upper bound (inclusive).  Default ``1.0``.

    Returns
    -------
    float
        Clamped value.

    Raises
    ------
    ValueError
        If ``lower > upper``.

    Example
    -------
    ::

        assert _clamp(1.5) == 1.0
        assert _clamp(-0.1) == 0.0
        assert _clamp(0.5, 0.0, 1.0) == 0.5
        assert _clamp(7.0, 0.0, 5.0) == 5.0
    """
    if lower > upper:
        raise ValueError(f"lower ({lower}) must not exceed upper ({upper})")
    return max(lower, min(upper, float(value)))


# ---------------------------------------------------------------------------
# §2 Enumerations
# ---------------------------------------------------------------------------


class ManifestStatus(str, Enum):
    """Lifecycle status of a :class:`DiscoveryEngineManifest`.

    A manifest progresses through three states in strict order:

    DRAFT
        The manifest is being constructed.  Entries may be freely added via
        :meth:`~DiscoveryEngineManifest.add_entry`.  This is the initial
        state for all manifests.

    SEALED
        The pipeline run that produced this manifest has completed.  No
        further entries may be added; attempting to do so raises
        :class:`RuntimeError`.  The manifest may still be read and queried.

    ARCHIVED
        The manifest has been moved to long-term storage.  It is immutable
        and read-only.

    Transition table::

        DRAFT ──seal()──► SEALED ──archive()──► ARCHIVED

    Notes
    -----
    Inherits from ``str`` so status values round-trip through JSON without
    an explicit serialisation step.
    """

    DRAFT = "DRAFT"
    """The manifest is open and accepting new entries."""

    SEALED = "SEALED"
    """The manifest is sealed; no new entries may be added."""

    ARCHIVED = "ARCHIVED"
    """The manifest has been archived for long-term storage."""

    @property
    def is_mutable(self) -> bool:
        """Return ``True`` if the manifest may still accept new entries.

        Only :attr:`DRAFT` manifests are mutable.

        Returns
        -------
        bool
            ``True`` iff status is :attr:`DRAFT`.

        Example
        -------
        ::

            assert ManifestStatus.DRAFT.is_mutable
            assert not ManifestStatus.SEALED.is_mutable
            assert not ManifestStatus.ARCHIVED.is_mutable
        """
        return self is ManifestStatus.DRAFT


class EvidenceEntryKind(str, Enum):
    """Semantic classification of a single :class:`EvidenceEntry`.

    Each entry in a :class:`DiscoveryEngineManifest` is tagged with one of
    the following kinds to describe its role in the pipeline:

    INPUT
        Raw data or candidate submitted to the pipeline at the start of
        the run.  Input entries document what drove this particular run.

    OUTPUT
        Data or results produced by the pipeline and committed to
        downstream systems (e.g. pack registry, evidence channels).

    INTERMEDIATE
        Transformed or annotated data that exists only within the pipeline
        run and is not persisted externally.  Useful for debugging stage
        transitions.

    DIAGNOSTIC
        Diagnostic information attached by stages during execution — timing
        measurements, counters, health indicators.

    AUDIT
        High-level audit records that summarise what the pipeline run did.
        Audit entries are typically written by the orchestration layer.

    Notes
    -----
    Inherits from ``str`` for direct JSON serialisation.
    """

    INPUT = "INPUT"
    """Raw pipeline input data."""

    OUTPUT = "OUTPUT"
    """Pipeline output committed to downstream systems."""

    INTERMEDIATE = "INTERMEDIATE"
    """Intermediate data produced during the pipeline run."""

    DIAGNOSTIC = "DIAGNOSTIC"
    """Diagnostic and monitoring information."""

    AUDIT = "AUDIT"
    """High-level audit summary records."""


# ---------------------------------------------------------------------------
# §3 EvidenceEntry
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """Immutable record of a single piece of evidence within a manifest.

    An :class:`EvidenceEntry` captures everything needed to understand one
    artifact of a pipeline run: what it is (:attr:`content`), where it came
    from (:attr:`source`), when it was recorded (:attr:`timestamp`), how
    confident we are about it (:attr:`confidence`), and what semantic role
    it plays (:attr:`kind`).

    Parameters
    ----------
    entry_id:
        Unique identifier for this entry.
    kind:
        :class:`EvidenceEntryKind` describing the role of this entry.
    content:
        String representation of the evidence content.
    source:
        Human-readable source identifier (stage name, component, etc.).
    timestamp:
        UTC POSIX timestamp at which the entry was created.
    confidence:
        Confidence score in ``[0, 1]`` associated with this evidence.
    tags:
        Tuple of string tags for free-form categorisation.

    Example
    -------
    ::

        entry = EvidenceEntry.create(
            kind=EvidenceEntryKind.INPUT,
            content="Candidate C1 — genus-2 surface",
            source="corpus_loader",
            confidence=0.95,
            tags=("topology", "surface"),
        )
        assert entry.kind is EvidenceEntryKind.INPUT
        age = entry.age_secs()
        assert age >= 0.0
    """

    entry_id: str
    kind: EvidenceEntryKind
    content: str
    source: str
    timestamp: float
    confidence: float = 1.0
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this entry to a plain dictionary.

        All values in the returned dictionary are JSON-serialisable
        primitives (strings, floats, lists).

        Returns
        -------
        dict[str, Any]
            Serialised entry.
        """
        return {
            "entry_id": self.entry_id,
            "kind": self.kind.value,
            "content": self.content,
            "source": self.source,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "tags": list(self.tags),
        }

    def age_secs(self, now: float | None = None) -> float:
        """Return the age of this entry in seconds.

        Parameters
        ----------
        now:
            Reference timestamp.  Defaults to :func:`_utcnow()`.

        Returns
        -------
        float
            Age in seconds (non-negative).

        Example
        -------
        ::

            entry = EvidenceEntry.create(...)
            time.sleep(0.01)
            assert entry.age_secs() >= 0.0
        """
        reference = _utcnow() if now is None else float(now)
        return max(0.0, reference - self.timestamp)

    @classmethod
    def create(
        cls,
        kind: EvidenceEntryKind,
        content: str,
        source: str,
        confidence: float = 1.0,
        tags: tuple[str, ...] = (),
    ) -> EvidenceEntry:
        """Factory: create a new entry with an auto-generated ID and timestamp.

        Parameters
        ----------
        kind:
            :class:`EvidenceEntryKind` of the new entry.
        content:
            Evidence content string.
        source:
            Source identifier.
        confidence:
            Confidence score in ``[0, 1]``.
        tags:
            Optional tuple of tags.

        Returns
        -------
        EvidenceEntry
            Newly created entry.

        Example
        -------
        ::

            e = EvidenceEntry.create(
                kind=EvidenceEntryKind.OUTPUT,
                content="Theorem T1 promoted",
                source="promotion_stage",
            )
            assert e.kind is EvidenceEntryKind.OUTPUT
        """
        return cls(
            entry_id=_uid(),
            kind=kind,
            content=str(content),
            source=str(source),
            timestamp=_utcnow(),
            confidence=_clamp(float(confidence)),
            tags=tuple(tags),
        )


# ---------------------------------------------------------------------------
# §4 DiscoveryEngineManifest
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class DiscoveryEngineManifest:
    """Ordered collection of evidence entries for one discovery pipeline run.

    A :class:`DiscoveryEngineManifest` is the primary audit artefact
    produced by the discovery pipeline.  It accumulates
    :class:`EvidenceEntry` objects as the pipeline progresses and is
    :meth:`sealed` once the run completes.

    Manifest IDs are distinct from pipeline run IDs.  A single pipeline
    run always produces exactly one manifest, but manifests from multiple
    runs can be :meth:`merged` into a combined manifest for batch analysis.

    Parameters
    ----------
    manifest_id:
        Unique identifier for this manifest.
    pipeline_run_id:
        Identifier of the pipeline run that produced this manifest.
    entries:
        Ordered list of :class:`EvidenceEntry` objects.
    status:
        Current lifecycle status.
    created_at:
        UTC POSIX timestamp at which this manifest was instantiated.
    metadata:
        Arbitrary key-value metadata attached to this manifest.

    Raises
    ------
    RuntimeError
        :meth:`add_entry` raises if the manifest is not in :attr:`ManifestStatus.DRAFT`.

    Example
    -------
    ::

        m = DiscoveryEngineManifest(
            manifest_id="m-001",
            pipeline_run_id="run-001",
        )
        m.add_entry(EvidenceEntry.create(
            EvidenceEntryKind.INPUT, "Candidate C1", "corpus"
        ))
        m.seal()
        assert m.status is ManifestStatus.SEALED
        assert m.entry_count == 1
    """

    manifest_id: str
    pipeline_run_id: str
    entries: list[EvidenceEntry] = dataclasses.field(default_factory=list)
    status: ManifestStatus = ManifestStatus.DRAFT
    created_at: float = dataclasses.field(default_factory=_utcnow)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation methods (DRAFT only)
    # ------------------------------------------------------------------

    def add_entry(self, entry: EvidenceEntry) -> None:
        """Append *entry* to the manifest.

        Parameters
        ----------
        entry:
            :class:`EvidenceEntry` to append.

        Raises
        ------
        RuntimeError
            If the manifest is not in :attr:`ManifestStatus.DRAFT`.

        Example
        -------
        ::

            m = DiscoveryEngineManifest("m", "run")
            m.add_entry(EvidenceEntry.create(EvidenceEntryKind.INPUT, "x", "s"))
            assert m.entry_count == 1
        """
        if not self.status.is_mutable:
            raise RuntimeError(
                f"Cannot add entries to a manifest in status {self.status.value!r}."
                " Seal or archive transitions are one-way."
            )
        self.entries.append(entry)

    def seal(self) -> None:
        """Seal the manifest, preventing further entry additions.

        Transitions :attr:`status` from :attr:`ManifestStatus.DRAFT` to
        :attr:`ManifestStatus.SEALED`.

        Raises
        ------
        RuntimeError
            If the manifest is already sealed or archived.

        Example
        -------
        ::

            m = DiscoveryEngineManifest("m", "run")
            m.seal()
            assert m.status is ManifestStatus.SEALED
        """
        if self.status is not ManifestStatus.DRAFT:
            raise RuntimeError(
                f"Can only seal a DRAFT manifest; current status is {self.status.value!r}."
            )
        self.status = ManifestStatus.SEALED

    def archive(self) -> None:
        """Archive the manifest for long-term storage.

        Transitions :attr:`status` from :attr:`ManifestStatus.SEALED` to
        :attr:`ManifestStatus.ARCHIVED`.

        Raises
        ------
        RuntimeError
            If the manifest is not sealed.

        Example
        -------
        ::

            m = DiscoveryEngineManifest("m", "run")
            m.seal()
            m.archive()
            assert m.status is ManifestStatus.ARCHIVED
        """
        if self.status is not ManifestStatus.SEALED:
            raise RuntimeError(
                f"Can only archive a SEALED manifest; current status is {self.status.value!r}."
            )
        self.status = ManifestStatus.ARCHIVED

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def entries_by_kind(self, kind: EvidenceEntryKind) -> list[EvidenceEntry]:
        """Return all entries with the given *kind*.

        Parameters
        ----------
        kind:
            The :class:`EvidenceEntryKind` to filter by.

        Returns
        -------
        list[EvidenceEntry]
            Matching entries in insertion order.

        Example
        -------
        ::

            inputs = m.entries_by_kind(EvidenceEntryKind.INPUT)
        """
        return [e for e in self.entries if e.kind is kind]

    @property
    def entry_count(self) -> int:
        """Return the total number of entries.

        Returns
        -------
        int
            ``len(self.entries)``.
        """
        return len(self.entries)

    @property
    def input_entries(self) -> list[EvidenceEntry]:
        """Return all :attr:`EvidenceEntryKind.INPUT` entries.

        Returns
        -------
        list[EvidenceEntry]
            Input entries.
        """
        return self.entries_by_kind(EvidenceEntryKind.INPUT)

    @property
    def output_entries(self) -> list[EvidenceEntry]:
        """Return all :attr:`EvidenceEntryKind.OUTPUT` entries.

        Returns
        -------
        list[EvidenceEntry]
            Output entries.
        """
        return self.entries_by_kind(EvidenceEntryKind.OUTPUT)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a plain dictionary.

        All nested structures are recursively serialised to JSON-compatible
        primitives.

        Returns
        -------
        dict[str, Any]
            Serialised manifest.

        Example
        -------
        ::

            d = m.to_dict()
            assert d["manifest_id"] == m.manifest_id
            assert isinstance(d["entries"], list)
        """
        return {
            "manifest_id": self.manifest_id,
            "pipeline_run_id": self.pipeline_run_id,
            "entries": [e.to_dict() for e in self.entries],
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the manifest.

        Returns
        -------
        str
            Summary string.

        Example
        -------
        ::

            print(m.summary())
        """
        # Count entries by kind
        kind_counts: dict[str, int] = {}
        for e in self.entries:
            kind_counts[e.kind.value] = kind_counts.get(e.kind.value, 0) + 1

        lines = [
            f"DiscoveryEngineManifest",
            f"  manifest_id    : {self.manifest_id}",
            f"  pipeline_run_id: {self.pipeline_run_id}",
            f"  status         : {self.status.value}",
            f"  entry_count    : {self.entry_count}",
            f"  created_at     : {self.created_at:.3f}",
        ]
        if kind_counts:
            lines.append("  entries by kind:")
            for k, n in sorted(kind_counts.items()):
                lines.append(f"    {k}: {n}")
        if self.metadata:
            lines.append("  metadata:")
            for k, v in self.metadata.items():
                lines.append(f"    {k}: {v!r}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def merge(self, other: DiscoveryEngineManifest) -> DiscoveryEngineManifest:
        """Return a new DRAFT manifest combining entries from ``self`` and *other*.

        The combined manifest receives a fresh :func:`_uid` manifest ID and
        the same ``pipeline_run_id`` as ``self``.  Entries from ``self`` are
        listed first, followed by entries from *other*.

        The combined manifest is in :attr:`ManifestStatus.DRAFT` regardless
        of the statuses of the source manifests.

        Parameters
        ----------
        other:
            The manifest to merge with.

        Returns
        -------
        DiscoveryEngineManifest
            New combined DRAFT manifest.

        Example
        -------
        ::

            combined = m1.merge(m2)
            assert combined.entry_count == m1.entry_count + m2.entry_count
            assert combined.status is ManifestStatus.DRAFT
        """
        combined = DiscoveryEngineManifest(
            manifest_id=_uid(),
            pipeline_run_id=self.pipeline_run_id,
            entries=list(self.entries) + list(other.entries),
            status=ManifestStatus.DRAFT,
            created_at=_utcnow(),
            metadata={**self.metadata, **other.metadata},
        )
        return combined


# ---------------------------------------------------------------------------
# §5 ManifestBuilder
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class ManifestBuilder:
    """Fluent builder for constructing :class:`DiscoveryEngineManifest` objects.

    :class:`ManifestBuilder` wraps an internal DRAFT manifest and provides
    typed helper methods for each :class:`EvidenceEntryKind`.  Each helper
    returns ``self`` to enable method chaining.

    Call :meth:`build` to seal the internal manifest and return it.  After
    :meth:`build` is called the builder's internal state is reset; further
    calls to :meth:`build` will raise :class:`RuntimeError`.

    Parameters
    ----------
    pipeline_run_id:
        Pipeline run identifier embedded in all produced manifests.

    Example
    -------
    ::

        from jugeo.ideation.discovery_engine.manifest import ManifestBuilder

        manifest = (
            ManifestBuilder("run-007")
            .add_input("Candidate C42", source="corpus")
            .add_intermediate("Novelty 0.82", source="novelty_stage")
            .add_output("Theorem promoted", source="promotion_stage")
            .add_diagnostic("Stage NOVELTY: 0.05s", source="timer")
            .add_audit("Run complete", source="orchestrator")
            .with_metadata("environment", "ci")
            .build()
        )
        assert manifest.entry_count == 5
        assert manifest.status.value == "SEALED"

    Notes
    -----
    Each builder instance is single-use.  Once :meth:`build` is called, the
    internal manifest is sealed and must not be mutated.  To build a new
    manifest, construct a fresh :class:`ManifestBuilder`.
    """

    pipeline_run_id: str
    _manifest: DiscoveryEngineManifest = dataclasses.field(init=False)
    _built: bool = dataclasses.field(init=False, default=False)

    def __post_init__(self) -> None:
        """Initialise the internal DRAFT manifest."""
        self._manifest = DiscoveryEngineManifest(
            manifest_id=_uid(),
            pipeline_run_id=self.pipeline_run_id,
        )

    # ------------------------------------------------------------------
    # Fluent entry-adding methods
    # ------------------------------------------------------------------

    def _add(
        self,
        kind: EvidenceEntryKind,
        content: str,
        source: str,
        confidence: float = 1.0,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Internal helper: create and append an entry.

        Parameters
        ----------
        kind:
            Entry kind.
        content:
            Evidence content string.
        source:
            Source identifier.
        confidence:
            Confidence score.
        tags:
            Optional tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.
        """
        if self._built:
            raise RuntimeError(
                "ManifestBuilder.build() has already been called; "
                "create a new builder to produce additional manifests."
            )
        self._manifest.add_entry(
            EvidenceEntry.create(
                kind=kind,
                content=content,
                source=source,
                confidence=confidence,
                tags=tags,
            )
        )
        return self

    def add_input(
        self,
        content: str,
        source: str,
        confidence: float = 1.0,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Append an :attr:`EvidenceEntryKind.INPUT` entry.

        Parameters
        ----------
        content:
            Input evidence content.
        source:
            Source identifier.
        confidence:
            Confidence score in ``[0, 1]``.
        tags:
            Optional categorisation tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.

        Example
        -------
        ::

            builder.add_input("Candidate C1", source="corpus", confidence=0.9)
        """
        return self._add(EvidenceEntryKind.INPUT, content, source, confidence, tags)

    def add_output(
        self,
        content: str,
        source: str,
        confidence: float = 1.0,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Append an :attr:`EvidenceEntryKind.OUTPUT` entry.

        Parameters
        ----------
        content:
            Output evidence content.
        source:
            Source identifier.
        confidence:
            Confidence score in ``[0, 1]``.
        tags:
            Optional categorisation tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.

        Example
        -------
        ::

            builder.add_output("Theorem T1 promoted", source="promotion_stage")
        """
        return self._add(EvidenceEntryKind.OUTPUT, content, source, confidence, tags)

    def add_intermediate(
        self,
        content: str,
        source: str,
        confidence: float = 1.0,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Append an :attr:`EvidenceEntryKind.INTERMEDIATE` entry.

        Parameters
        ----------
        content:
            Intermediate evidence content.
        source:
            Source identifier.
        confidence:
            Confidence score in ``[0, 1]``.
        tags:
            Optional categorisation tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.
        """
        return self._add(
            EvidenceEntryKind.INTERMEDIATE, content, source, confidence, tags
        )

    def add_diagnostic(
        self,
        content: str,
        source: str,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Append an :attr:`EvidenceEntryKind.DIAGNOSTIC` entry.

        Diagnostic entries always receive confidence ``1.0`` (they are
        factual measurements, not probabilistic assessments).

        Parameters
        ----------
        content:
            Diagnostic content string.
        source:
            Source identifier.
        tags:
            Optional tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.
        """
        return self._add(EvidenceEntryKind.DIAGNOSTIC, content, source, 1.0, tags)

    def add_audit(
        self,
        content: str,
        source: str,
        tags: tuple[str, ...] = (),
    ) -> ManifestBuilder:
        """Append an :attr:`EvidenceEntryKind.AUDIT` entry.

        Audit entries always receive confidence ``1.0``.

        Parameters
        ----------
        content:
            Audit record content string.
        source:
            Source identifier.
        tags:
            Optional tags.

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.
        """
        return self._add(EvidenceEntryKind.AUDIT, content, source, 1.0, tags)

    def with_metadata(self, key: str, value: Any) -> ManifestBuilder:
        """Attach a metadata key-value pair to the manifest.

        Parameters
        ----------
        key:
            Metadata key.
        value:
            Metadata value (should be JSON-serialisable).

        Returns
        -------
        ManifestBuilder
            ``self`` for chaining.

        Example
        -------
        ::

            builder.with_metadata("environment", "production")
                   .with_metadata("version", "0.1.0")
        """
        if self._built:
            raise RuntimeError(
                "ManifestBuilder.build() has already been called."
            )
        self._manifest.metadata[key] = value
        return self

    # ------------------------------------------------------------------
    # Terminal operation
    # ------------------------------------------------------------------

    def build(self) -> DiscoveryEngineManifest:
        """Seal the internal manifest and return it.

        After :meth:`build` is called the builder enters a consumed state
        and will raise :class:`RuntimeError` on any further mutation calls.

        Returns
        -------
        DiscoveryEngineManifest
            The sealed manifest.

        Raises
        ------
        RuntimeError
            If :meth:`build` has already been called on this builder.

        Example
        -------
        ::

            manifest = ManifestBuilder("run-1").add_input("x", "s").build()
            assert manifest.status is ManifestStatus.SEALED
        """
        if self._built:
            raise RuntimeError(
                "ManifestBuilder.build() has already been called; "
                "this builder is consumed."
            )
        self._manifest.seal()
        self._built = True
        return self._manifest

    # ------------------------------------------------------------------
    # Query property
    # ------------------------------------------------------------------

    @property
    def entry_count(self) -> int:
        """Return the number of entries accumulated so far (before :meth:`build`).

        Returns
        -------
        int
            Current entry count.
        """
        return self._manifest.entry_count

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"ManifestBuilder(pipeline_run_id={self.pipeline_run_id!r},"
            f" entries={self.entry_count},"
            f" built={self._built})"
        )


# ---------------------------------------------------------------------------
# §6 Module-level factory and utility functions
# ---------------------------------------------------------------------------


def build_discovery_manifest(
    pipeline_run_id: str,
    entries: list[EvidenceEntry] | None = None,
) -> DiscoveryEngineManifest:
    """Create a :class:`DiscoveryEngineManifest` with optional initial entries.

    This factory function constructs a new DRAFT manifest for the given
    *pipeline_run_id*.  If *entries* is provided, each entry is appended
    to the manifest before it is returned (in DRAFT state, ready for more
    entries).

    Parameters
    ----------
    pipeline_run_id:
        Identifier of the pipeline run.
    entries:
        Optional list of :class:`EvidenceEntry` objects to pre-populate
        the manifest.  The manifest remains in DRAFT state after this
        call.

    Returns
    -------
    DiscoveryEngineManifest
        New DRAFT manifest.

    Example
    -------
    ::

        from jugeo.ideation.discovery_engine.manifest import (
            build_discovery_manifest, EvidenceEntry, EvidenceEntryKind
        )

        e = EvidenceEntry.create(EvidenceEntryKind.INPUT, "C1", "corpus")
        m = build_discovery_manifest("run-001", entries=[e])
        assert m.entry_count == 1
        assert m.status is ManifestStatus.DRAFT
    """
    manifest = DiscoveryEngineManifest(
        manifest_id=_uid(),
        pipeline_run_id=pipeline_run_id,
    )
    for entry in (entries or []):
        manifest.add_entry(entry)
    return manifest


def merge_manifests(
    manifests: list[DiscoveryEngineManifest],
) -> DiscoveryEngineManifest:
    """Merge a list of manifests into a single combined DRAFT manifest.

    Entries from all source manifests are concatenated in list order.  The
    combined manifest receives a fresh :func:`_uid` manifest ID.  The
    ``pipeline_run_id`` is taken from the first manifest in the list; if
    the list is empty the ``pipeline_run_id`` is set to ``"merged"``.

    The combined manifest is returned in :attr:`ManifestStatus.DRAFT` state
    regardless of the statuses of the input manifests.

    Parameters
    ----------
    manifests:
        Manifests to merge.  May be empty, sealed, or archived.

    Returns
    -------
    DiscoveryEngineManifest
        Combined DRAFT manifest.

    Raises
    ------
    TypeError
        If *manifests* is not a list.

    Example
    -------
    ::

        combined = merge_manifests([m1, m2, m3])
        assert combined.entry_count == m1.entry_count + m2.entry_count + m3.entry_count
        assert combined.status is ManifestStatus.DRAFT
    """
    if not isinstance(manifests, list):
        raise TypeError(
            f"merge_manifests expects a list; got {type(manifests).__name__!r}"
        )

    all_entries: list[EvidenceEntry] = []
    combined_metadata: dict[str, Any] = {}
    pipeline_run_id = manifests[0].pipeline_run_id if manifests else "merged"

    for m in manifests:
        all_entries.extend(m.entries)
        combined_metadata.update(m.metadata)

    combined = DiscoveryEngineManifest(
        manifest_id=_uid(),
        pipeline_run_id=pipeline_run_id,
        entries=list(all_entries),
        status=ManifestStatus.DRAFT,
        created_at=_utcnow(),
        metadata=combined_metadata,
    )
    return combined


def validate_manifest(manifest: DiscoveryEngineManifest) -> list[str]:
    """Validate a :class:`DiscoveryEngineManifest` and return a list of errors.

    Performs structural validation checks:

    * ``manifest_id`` must be a non-empty string.
    * ``pipeline_run_id`` must be a non-empty string.
    * All entries must be :class:`EvidenceEntry` instances.
    * Each entry's ``confidence`` must be in ``[0, 1]``.
    * Each entry's ``timestamp`` must be a positive float.
    * The manifest must contain at least one :attr:`EvidenceEntryKind.INPUT`
      entry (a warning, not an error, if absent).

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    list[str]
        List of validation error strings.  Empty means valid.

    Example
    -------
    ::

        errors = validate_manifest(m)
        if errors:
            for err in errors:
                print(f"ERROR: {err}")
        else:
            print("Manifest is valid")
    """
    if manifest is None:
        return ["manifest must not be None"]

    errors: list[str] = []

    # Check manifest_id
    if not manifest.manifest_id or not str(manifest.manifest_id).strip():
        errors.append("manifest_id must be a non-empty string")

    # Check pipeline_run_id
    if not manifest.pipeline_run_id or not str(manifest.pipeline_run_id).strip():
        errors.append("pipeline_run_id must be a non-empty string")

    # Check entries
    for idx, entry in enumerate(manifest.entries):
        if not isinstance(entry, EvidenceEntry):
            errors.append(
                f"Entry at index {idx} is not an EvidenceEntry instance: "
                f"{type(entry).__name__!r}"
            )
            continue
        if not (0.0 <= entry.confidence <= 1.0):
            errors.append(
                f"Entry {entry.entry_id!r} has invalid confidence "
                f"{entry.confidence!r} (must be in [0, 1])"
            )
        if entry.timestamp <= 0.0:
            errors.append(
                f"Entry {entry.entry_id!r} has invalid timestamp "
                f"{entry.timestamp!r} (must be > 0)"
            )
        if not entry.content:
            errors.append(
                f"Entry {entry.entry_id!r} has empty content"
            )
        if not entry.source:
            errors.append(
                f"Entry {entry.entry_id!r} has empty source"
            )

    # Warn (as error) if no INPUT entries in a non-empty manifest
    if manifest.entries and not manifest.input_entries:
        errors.append(
            "Manifest contains no INPUT entries; at least one INPUT entry is "
            "expected to document the pipeline inputs."
        )

    return errors
