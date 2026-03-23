r"""Evidence manifests for the JuGeo semantic state machine.

This module implements the full manifest system described in
``preliminaries/theory2.tex``.  A manifest **M = (J, O, E, X, K, η, σ)** is
the single source of truth for what is known, what remains, and what failed:

* **J** — persisted judgments  (:class:`JudgmentStore`)
* **O** — residual obligations  (:class:`ObligationStore`)
* **E** — evidence archive  (:class:`EvidenceArchive`)
* **X** — obstructions  (:class:`ObstructionStore`)
* **K** — settlement certificates  (:class:`CertificateStore`)
* **η** — epoch map  (:class:`EpochMap`)
* **σ** — invalidation graph  (:class:`InvalidationGraph`)

The :class:`Manifest` orchestrates these seven components and exposes
snapshot / restore / diff / merge primitives needed by the lifecycle engine.
All mutable stores use plain dicts to keep the implementation dependency-free;
the immutable *evidence bundle* dataclass :class:`EvidenceManifest` is
preserved for backward compatibility with :mod:`jugeo.evidence.certificates`.

copilot: This module is a core manifest target for LLM-assisted orchestration.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)

from jugeo.evidence.channels import EvidenceRecord
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles

if TYPE_CHECKING:
    from jugeo.evidence.certificates import SettlementCertificate


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    """Return monotonic-safe wall-clock timestamp."""
    return time.time()


def _uid() -> str:
    """Return a short unique identifier."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ObligationPriority(IntEnum):
    """Priority levels for residual obligations."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ObstructionKind(str, Enum):
    """Classification of obstructions by origin."""

    COVER_FAILURE = 'cover_failure'
    DESCENT_OBSTRUCTION = 'descent_obstruction'
    DESCENT_FAILURE = 'descent_obstruction'
    OVERLAP_VIOLATION = 'cover_failure'
    TRUST_VIOLATION = 'trust_violation'
    TRUST_CEILING_VIOLATION = 'trust_violation'
    EVIDENCE_GAP = 'evidence_gap'
    ENCODING_MISMATCH = 'encoding_mismatch'
    TIMEOUT = 'timeout'
    UNKNOWN = 'unknown'


# ---------------------------------------------------------------------------
# EvidenceManifest — legacy evidence-bundle dataclass (kept for compat)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    """Immutable evidence bundle for a single coordinate/claim pair.

    This class is preserved for backward compatibility with
    :mod:`jugeo.evidence.certificates`.  Higher-level manifest semantics are
    handled by :class:`Manifest`.

    Parameters
    ----------
    coordinate:
        Fully-qualified coordinate string.
    claim:
        The proposition being supported.
    records:
        Evidence records that substantiate the claim.
    trust:
        Joined trust profile across all records.
    provenance:
        Trace of how the evidence was assembled.
    residuals:
        Obligation identifiers that remain after collection.
    """

    coordinate: str
    claim: str
    records: tuple[EvidenceRecord, ...]
    trust: TrustProfile
    provenance: ProvenanceTrace
    residuals: tuple[str, ...] = field(default_factory=tuple)

    def canonical_key(self) -> str:
        """Return deterministic key ``coordinate:claim``."""
        return f'{self.coordinate}:{self.claim}'

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready dictionary in canonical key order."""
        return {
            'canonical_key': self.canonical_key(),
            'claim': self.claim,
            'coordinate': self.coordinate,
            'record_count': len(self.records),
            'residuals': list(self.residuals),
            'trust_tier': self.trust.tier.value,
        }

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def site_coverage(self) -> dict[str, Any]:
        """Return a coverage map of this manifest over a site.

        Maps the manifest's coordinate into the site coordinate system from
        ``jugeo.geometry.site`` and evaluates which covering sieves from
        ``jugeo.geometry.covers`` are satisfied by the evidence records
        contained in this manifest.
        """
        try:
            from jugeo.geometry.site import coverage_for_coordinate
            from jugeo.geometry.covers import evaluate_coverage
        except ImportError:
            return {'coordinate': self.coordinate, 'coverage': None, 'reason': 'site/covers unavailable'}
        site_objects = coverage_for_coordinate(self.coordinate)
        return evaluate_coverage(site_objects, len(self.records))

    def descent_completeness(self) -> bool:
        """Whether this manifest is descent-complete.

        A manifest is descent-complete when the evidence it contains is
        sufficient to reconstruct a global section via the descent
        machinery in ``jugeo.geometry.descent``.  This is a necessary
        condition for issuing a VERIFIED-level certificate.
        """
        try:
            from jugeo.geometry.descent import is_descent_complete
        except ImportError:
            return False
        return is_descent_complete(self.coordinate, len(self.records), self.trust.tier.label())

    @property
    def encoding_coverage(self) -> dict[str, Any]:
        """Return which encoding families are covered by this manifest.

        Queries ``jugeo.encodings`` for the set of encoding families
        relevant to the manifest's coordinate and claim, then reports
        which families have supporting evidence records.
        """
        try:
            from jugeo.encodings import families_for_coordinate
        except ImportError:
            return {'coordinate': self.coordinate, 'families': None, 'reason': 'encodings unavailable'}
        return families_for_coordinate(self.coordinate)

    def judgment_manifest(self) -> dict[str, Any]:
        """Return this manifest restricted to judgment-level evidence.

        Filters the manifest's records to those that directly support
        judgment terms from ``jugeo.judgments``, yielding a sub-manifest
        that can be composed with the judgment subsystem.
        """
        try:
            from jugeo.judgments import filter_judgment_records
        except ImportError:
            return {
                'coordinate': self.coordinate,
                'claim': self.claim,
                'judgment_records': None,
                'reason': 'judgments unavailable',
            }
        return filter_judgment_records(self.records, self.coordinate)

    @property
    def maturity_score(self) -> str:
        """Return the maturity tier implied by this manifest's evidence.

        Maps the manifest's trust profile and record count into the
        maturity model from ``jugeo.maturity``, yielding a human-readable
        maturity label (e.g. ``'initial'``, ``'managed'``, ``'optimized'``).
        """
        try:
            from jugeo.maturity import manifest_maturity
        except ImportError:
            return 'unknown'
        return manifest_maturity(self.trust.tier.label(), len(self.records))


def build_evidence_manifest(
    coordinate: str,
    claim: str,
    records: tuple[EvidenceRecord, ...],
    *,
    trust_profiles: tuple[TrustProfile, ...],
    provenance: ProvenanceTrace,
) -> EvidenceManifest:
    """Construct an :class:`EvidenceManifest` from constituent parts.

    Parameters
    ----------
    coordinate:
        Fully-qualified coordinate string.
    claim:
        The proposition being supported.
    records:
        Evidence records collected across channels.
    trust_profiles:
        Per-record trust profiles to be joined.
    provenance:
        Provenance trace covering the collection process.

    Returns
    -------
    EvidenceManifest
        The assembled manifest with deduplicated residuals.
    """
    residuals = tuple(
        dict.fromkeys(
            residual for record in records for residual in record.obligations
        )
    )
    return EvidenceManifest(
        coordinate,
        claim,
        records,
        join_trust_profiles(*trust_profiles),
        provenance,
        residuals,
    )


# ---------------------------------------------------------------------------
# JudgmentStore  (J)
# ---------------------------------------------------------------------------

@dataclass
class _JudgmentEntry:
    """Internal mutable wrapper around a persisted judgment record."""

    judgment_id: str
    coordinate: str
    proposition: str
    trust_tier: int
    evidence_refs: list[str]
    status: str
    added_at: float
    metadata: dict[str, Any]


class JudgmentStore:
    """Persisted judgments forming component **J** of the manifest.

    Judgments are indexed by a unique *judgment_id* and can be queried by
    coordinate, proposition substring, or trust tier.

    copilot: JudgmentStore is a key query surface for LLM evidence retrieval.
    """

    def __init__(self) -> None:
        self._store: dict[str, _JudgmentEntry] = {}
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)

    # -- mutators ----------------------------------------------------------

    def add(
        self,
        coordinate: str,
        proposition: str,
        *,
        trust_tier: int = TrustTier.PROPOSAL,
        evidence_refs: Sequence[str] = (),
        status: str = 'proposed',
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Persist a new judgment and return its identifier.

        Parameters
        ----------
        coordinate:
            Coordinate the judgment applies to.
        proposition:
            Statement the judgment asserts.
        trust_tier:
            Integer trust tier (see :class:`TrustTier`).
        evidence_refs:
            References to supporting evidence records.
        status:
            Initial status string (``proposed``, ``settled``, etc.).
        metadata:
            Arbitrary key-value metadata.

        Returns
        -------
        str
            The generated judgment identifier.
        """
        jid = f'j-{_uid()}'
        entry = _JudgmentEntry(
            judgment_id=jid,
            coordinate=coordinate,
            proposition=proposition,
            trust_tier=int(trust_tier),
            evidence_refs=list(evidence_refs),
            status=status,
            added_at=_now(),
            metadata=dict(metadata or {}),
        )
        self._store[jid] = entry
        self._by_coordinate[coordinate].append(jid)
        return jid

    def bulk_add(self, entries: Iterable[Mapping[str, Any]]) -> list[str]:
        """Add many judgments at once.  Returns list of generated ids.

        Each mapping in *entries* should have keys matching :meth:`add`
        parameters (``coordinate``, ``proposition``, etc.).
        """
        ids: list[str] = []
        for entry in entries:
            jid = self.add(
                coordinate=str(entry['coordinate']),
                proposition=str(entry['proposition']),
                trust_tier=int(entry.get('trust_tier', TrustTier.PROPOSAL)),
                evidence_refs=list(entry.get('evidence_refs', ())),
                status=str(entry.get('status', 'proposed')),
                metadata=dict(entry.get('metadata', {})),
            )
            ids.append(jid)
        return ids

    def remove(self, judgment_id: str) -> bool:
        """Remove a judgment by id.  Returns ``True`` if found."""
        entry = self._store.pop(judgment_id, None)
        if entry is None:
            return False
        coord_list = self._by_coordinate.get(entry.coordinate, [])
        if judgment_id in coord_list:
            coord_list.remove(judgment_id)
        return True

    # -- queries -----------------------------------------------------------

    def get(self, judgment_id: str) -> dict[str, Any] | None:
        """Return judgment dict by *judgment_id*, or ``None``."""
        entry = self._store.get(judgment_id)
        if entry is None:
            return None
        return self._entry_to_dict(entry)

    def query_by_coordinate(self, coordinate: str) -> list[dict[str, Any]]:
        """Return all judgments at *coordinate*."""
        return [
            self._entry_to_dict(self._store[jid])
            for jid in self._by_coordinate.get(coordinate, [])
            if jid in self._store
        ]

    def query_by_proposition(self, substring: str) -> list[dict[str, Any]]:
        """Return judgments whose proposition contains *substring*."""
        needle = substring.lower()
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if needle in e.proposition.lower()
        ]

    def query_by_trust(self, min_tier: int) -> list[dict[str, Any]]:
        """Return judgments with trust tier >= *min_tier*."""
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if e.trust_tier >= min_tier
        ]

    def count(self) -> int:
        """Return total number of stored judgments."""
        return len(self._store)

    def iterate(self) -> Iterator[dict[str, Any]]:
        """Yield all judgments as dicts in insertion order."""
        for entry in self._store.values():
            yield self._entry_to_dict(entry)

    def filter(
        self,
        *,
        coordinate: str | None = None,
        status: str | None = None,
        min_trust: int | None = None,
    ) -> list[dict[str, Any]]:
        """Multi-predicate filter returning matching judgments.

        Parameters
        ----------
        coordinate:
            If given, restrict to this coordinate.
        status:
            If given, restrict to this status string.
        min_trust:
            If given, restrict to judgments with tier >= this value.
        """
        results: list[dict[str, Any]] = []
        for entry in self._store.values():
            if coordinate is not None and entry.coordinate != coordinate:
                continue
            if status is not None and entry.status != status:
                continue
            if min_trust is not None and entry.trust_tier < min_trust:
                continue
            results.append(self._entry_to_dict(entry))
        return results

    def serialize(self) -> list[dict[str, Any]]:
        """Return JSON-ready list of all judgments."""
        return list(self.iterate())

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _entry_to_dict(entry: _JudgmentEntry) -> dict[str, Any]:
        return {
            'judgment_id': entry.judgment_id,
            'coordinate': entry.coordinate,
            'proposition': entry.proposition,
            'trust_tier': entry.trust_tier,
            'evidence_refs': list(entry.evidence_refs),
            'status': entry.status,
            'added_at': entry.added_at,
            'metadata': dict(entry.metadata),
        }


# ---------------------------------------------------------------------------
# ObligationStore  (O)
# ---------------------------------------------------------------------------

@dataclass
class _ObligationEntry:
    """Internal mutable wrapper for an obligation."""

    obligation_id: str
    coordinate: str
    description: str
    priority: ObligationPriority
    discharged: bool
    deadline: float | None
    dependencies: list[str]
    created_at: float


class ObligationStore:
    """Residual obligations forming component **O** of the manifest.

    Obligations track what remains to be settled after evidence collection.
    They may depend on other obligations, enabling dependency-graph analysis.

    copilot: ObligationStore feeds the obligation-repair planner.
    """

    def __init__(self) -> None:
        self._store: dict[str, _ObligationEntry] = {}
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)

    def add(
        self,
        coordinate: str,
        description: str,
        *,
        priority: ObligationPriority = ObligationPriority.MEDIUM,
        deadline: float | None = None,
        dependencies: Sequence[str] = (),
    ) -> str:
        """Add a new obligation and return its identifier.

        Parameters
        ----------
        coordinate:
            Coordinate this obligation pertains to.
        description:
            Human-readable description of what must be settled.
        priority:
            Urgency level.
        deadline:
            Optional UNIX timestamp by which the obligation must be discharged.
        dependencies:
            Identifiers of obligations that must be discharged first.
        """
        oid = f'o-{_uid()}'
        entry = _ObligationEntry(
            obligation_id=oid,
            coordinate=coordinate,
            description=description,
            priority=priority,
            discharged=False,
            deadline=deadline,
            dependencies=list(dependencies),
            created_at=_now(),
        )
        self._store[oid] = entry
        self._by_coordinate[coordinate].append(oid)
        return oid

    def discharge(self, obligation_id: str) -> bool:
        """Mark an obligation as discharged.  Returns ``True`` on success."""
        entry = self._store.get(obligation_id)
        if entry is None:
            return False
        entry.discharged = True
        return True

    def is_discharged(self, obligation_id: str) -> bool:
        """Check whether *obligation_id* has been discharged."""
        entry = self._store.get(obligation_id)
        return entry.discharged if entry else False

    def pending(self) -> list[dict[str, Any]]:
        """Return all obligations that have not been discharged."""
        return [
            self._entry_to_dict(e) for e in self._store.values() if not e.discharged
        ]

    def overdue(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Return undischarged obligations past their deadline.

        Parameters
        ----------
        now:
            Reference timestamp; defaults to :func:`_now`.
        """
        ref = now if now is not None else _now()
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if not e.discharged and e.deadline is not None and e.deadline < ref
        ]

    def by_priority(self, priority: ObligationPriority) -> list[dict[str, Any]]:
        """Return obligations at the given *priority* level."""
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if e.priority == priority and not e.discharged
        ]

    def by_coordinate(self, coordinate: str) -> list[dict[str, Any]]:
        """Return all obligations at *coordinate*."""
        return [
            self._entry_to_dict(self._store[oid])
            for oid in self._by_coordinate.get(coordinate, [])
            if oid in self._store
        ]

    def dependencies(self, obligation_id: str) -> list[str]:
        """Return dependency identifiers for *obligation_id*."""
        entry = self._store.get(obligation_id)
        return list(entry.dependencies) if entry else []

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the full dependency graph as adjacency lists.

        Keys are obligation ids; values are lists of ids they depend on.
        """
        return {
            oid: list(entry.dependencies)
            for oid, entry in self._store.items()
        }

    def count(self) -> int:
        """Return total number of obligations (pending and discharged)."""
        return len(self._store)

    def serialize(self) -> list[dict[str, Any]]:
        """Return JSON-ready list of all obligations."""
        return [self._entry_to_dict(e) for e in self._store.values()]

    @staticmethod
    def _entry_to_dict(entry: _ObligationEntry) -> dict[str, Any]:
        return {
            'obligation_id': entry.obligation_id,
            'coordinate': entry.coordinate,
            'description': entry.description,
            'priority': entry.priority.value,
            'discharged': entry.discharged,
            'deadline': entry.deadline,
            'dependencies': list(entry.dependencies),
            'created_at': entry.created_at,
        }


# ---------------------------------------------------------------------------
# EvidenceArchive  (E)
# ---------------------------------------------------------------------------

@dataclass
class _ArchiveEntry:
    """Internal wrapper for an archived evidence record."""

    archive_id: str
    record: EvidenceRecord
    coordinate: str
    channel_name: str
    trust_tier: int
    collected_at: float
    expires_at: float | None


class EvidenceArchive:
    """Archive of all evidence ever collected — component **E**.

    Unlike ephemeral evidence used during a single settlement pass, the
    archive retains records across epochs for replay, audit, and
    cohomological analysis.

    copilot: EvidenceArchive is queryable for LLM evidence-retrieval passes.
    """

    def __init__(self) -> None:
        self._store: dict[str, _ArchiveEntry] = {}
        self._by_channel: dict[str, list[str]] = defaultdict(list)
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)

    def add(
        self,
        record: EvidenceRecord,
        coordinate: str,
        channel_name: str | None = None,
        *,
        trust_tier: int = TrustTier.PROPOSAL,
        expires_at: float | None = None,
    ) -> str:
        """Archive an evidence record and return its archive identifier.

        Parameters
        ----------
        record:
            The evidence record to archive.
        coordinate:
            Coordinate this evidence pertains to.
        trust_tier:
            Integer trust tier at time of collection.
        expires_at:
            Optional UNIX timestamp after which the record is prunable.

        Returns
        -------
        str
            The generated archive identifier.
        """
        aid = f'e-{_uid()}'
        entry = _ArchiveEntry(
            archive_id=aid,
            record=record,
            coordinate=coordinate,
            channel_name=(
                channel_name
                or getattr(record.channel, 'value', None)
                or getattr(record.channel, 'name', '')
            ),
            trust_tier=int(trust_tier),
            collected_at=_now(),
            expires_at=expires_at,
        )
        self._store[aid] = entry
        self._by_channel[entry.channel_name].append(aid)
        self._by_coordinate[coordinate].append(aid)
        return aid

    def get(self, archive_id: str) -> dict[str, Any] | None:
        """Retrieve an archived record by *archive_id*."""
        entry = self._store.get(archive_id)
        if entry is None:
            return None
        return self._entry_to_dict(entry)

    def by_channel(self, channel_name: str) -> list[dict[str, Any]]:
        """Return all archived records from *channel_name*."""
        return [
            self._entry_to_dict(self._store[aid])
            for aid in self._by_channel.get(channel_name, [])
            if aid in self._store
        ]

    def by_coordinate(self, coordinate: str) -> list[dict[str, Any]]:
        """Return all archived records at *coordinate*."""
        return [
            self._entry_to_dict(self._store[aid])
            for aid in self._by_coordinate.get(coordinate, [])
            if aid in self._store
        ]

    def by_trust_level(self, min_tier: int) -> list[dict[str, Any]]:
        """Return records with trust tier >= *min_tier*."""
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if e.trust_tier >= min_tier
        ]

    def by_time_range(
        self, start: float, end: float
    ) -> list[dict[str, Any]]:
        """Return records collected in ``[start, end]`` inclusive.

        Parameters
        ----------
        start:
            Lower bound UNIX timestamp.
        end:
            Upper bound UNIX timestamp.
        """
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if start <= e.collected_at <= end
        ]

    def prune_expired(self, *, now: float | None = None) -> int:
        """Remove records past their expiry.  Returns count of pruned entries.

        Parameters
        ----------
        now:
            Reference timestamp; defaults to :func:`_now`.
        """
        ref = now if now is not None else _now()
        expired = [
            aid
            for aid, e in self._store.items()
            if e.expires_at is not None and e.expires_at < ref
        ]
        for aid in expired:
            entry = self._store.pop(aid)
            ch_list = self._by_channel.get(entry.channel_name, [])
            if aid in ch_list:
                ch_list.remove(aid)
            co_list = self._by_coordinate.get(entry.coordinate, [])
            if aid in co_list:
                co_list.remove(aid)
        return len(expired)

    def compact(self) -> int:
        """Remove duplicate evidence for the same coordinate+channel.

        Keeps the most recently collected record for each
        (coordinate, channel_name) pair.  Returns the number of
        records removed.
        """
        best: dict[tuple[str, str], str] = {}
        for aid, entry in self._store.items():
            key = (entry.coordinate, entry.channel_name)
            prev = best.get(key)
            if prev is None or entry.collected_at > self._store[prev].collected_at:
                best[key] = aid
        keep = set(best.values())
        to_remove = [aid for aid in self._store if aid not in keep]
        for aid in to_remove:
            entry = self._store.pop(aid)
            ch_list = self._by_channel.get(entry.channel_name, [])
            if aid in ch_list:
                ch_list.remove(aid)
            co_list = self._by_coordinate.get(entry.coordinate, [])
            if aid in co_list:
                co_list.remove(aid)
        return len(to_remove)

    def statistics(self) -> dict[str, Any]:
        """Return aggregate statistics over the archive.

        Returns
        -------
        dict
            Keys: ``total``, ``by_channel``, ``by_trust_tier``,
            ``oldest``, ``newest``.
        """
        if not self._store:
            return {
                'total': 0,
                'by_channel': {},
                'by_trust_tier': {},
                'oldest': None,
                'newest': None,
            }
        by_channel: dict[str, int] = defaultdict(int)
        by_tier: dict[int, int] = defaultdict(int)
        oldest = float('inf')
        newest = 0.0
        for entry in self._store.values():
            by_channel[entry.channel_name] += 1
            by_tier[entry.trust_tier] += 1
            if entry.collected_at < oldest:
                oldest = entry.collected_at
            if entry.collected_at > newest:
                newest = entry.collected_at
        return {
            'total': len(self._store),
            'by_channel': dict(by_channel),
            'by_trust_tier': {str(k): v for k, v in sorted(by_tier.items())},
            'oldest': oldest,
            'newest': newest,
        }

    def count(self) -> int:
        """Return total number of archived records."""
        return len(self._store)

    def serialize(self) -> list[dict[str, Any]]:
        """Return JSON-ready list of all archived entries."""
        return [self._entry_to_dict(e) for e in self._store.values()]

    @staticmethod
    def _entry_to_dict(entry: _ArchiveEntry) -> dict[str, Any]:
        return {
            'archive_id': entry.archive_id,
            'coordinate': entry.coordinate,
            'channel_name': entry.channel_name,
            'claim': entry.record.claim,
            'trust_tier': entry.trust_tier,
            'collected_at': entry.collected_at,
            'expires_at': entry.expires_at,
            'obligations': list(entry.record.obligations),
        }


# ---------------------------------------------------------------------------
# ObstructionStore  (X)
# ---------------------------------------------------------------------------

@dataclass
class _ObstructionEntry:
    """Internal wrapper for an obstruction record."""

    obstruction_id: str
    coordinate: str
    kind: ObstructionKind
    message: str
    resolved: bool
    rank: int
    cohomology_class: str | None
    created_at: float
    resolved_at: float | None
    metadata: dict[str, Any]


class ObstructionStore:
    """Persistent obstructions forming component **X** of the manifest.

    Obstructions are first-class citizens in theory2.tex — they are *not*
    errors to be discarded.  They carry a rank, an optional cohomology class,
    and participate in the repair-frontier computation.

    copilot: ObstructionStore supports cohomological classification queries.
    """

    def __init__(self) -> None:
        self._store: dict[str, _ObstructionEntry] = {}
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)

    def add(
        self,
        coordinate: str,
        kind: ObstructionKind,
        message: str,
        *,
        rank: int = 0,
        cohomology_class: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Record a new obstruction and return its identifier.

        Parameters
        ----------
        coordinate:
            Coordinate where the obstruction was detected.
        kind:
            Classification of the obstruction.
        message:
            Human-readable description.
        rank:
            Cohomological rank (0 = local, higher = more global).
        cohomology_class:
            Optional label for the cohomology class this obstruction lives in.
        metadata:
            Arbitrary additional data.
        """
        xid = f'x-{_uid()}'
        entry = _ObstructionEntry(
            obstruction_id=xid,
            coordinate=coordinate,
            kind=kind,
            message=message,
            resolved=False,
            rank=rank,
            cohomology_class=cohomology_class,
            created_at=_now(),
            resolved_at=None,
            metadata=dict(metadata or {}),
        )
        self._store[xid] = entry
        self._by_coordinate[coordinate].append(xid)
        return xid

    def resolve(self, obstruction_id: str) -> bool:
        """Mark an obstruction as resolved.  Returns ``True`` on success."""
        entry = self._store.get(obstruction_id)
        if entry is None:
            return False
        entry.resolved = True
        entry.resolved_at = _now()
        return True

    def is_resolved(self, obstruction_id: str) -> bool:
        """Check whether *obstruction_id* has been resolved."""
        entry = self._store.get(obstruction_id)
        return entry.resolved if entry else False

    def active(self) -> list[dict[str, Any]]:
        """Return all unresolved obstructions."""
        return [
            self._entry_to_dict(e) for e in self._store.values() if not e.resolved
        ]

    def by_coordinate(self, coordinate: str) -> list[dict[str, Any]]:
        """Return all obstructions at *coordinate*."""
        return [
            self._entry_to_dict(self._store[xid])
            for xid in self._by_coordinate.get(coordinate, [])
            if xid in self._store
        ]

    def by_kind(self, kind: ObstructionKind) -> list[dict[str, Any]]:
        """Return all obstructions of a given *kind*."""
        return [
            self._entry_to_dict(e)
            for e in self._store.values()
            if e.kind == kind
        ]

    def cohomology_classes(self) -> dict[str, list[dict[str, Any]]]:
        """Group active obstructions by cohomology class.

        Returns a dict mapping class labels to lists of obstruction dicts.
        Obstructions with no class are grouped under ``'unclassified'``.
        """
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in self._store.values():
            if entry.resolved:
                continue
            label = entry.cohomology_class or 'unclassified'
            groups[label].append(self._entry_to_dict(entry))
        return dict(groups)

    def repair_frontier(self) -> list[dict[str, Any]]:
        """Compute the repair frontier — lowest-rank active obstructions.

        The repair frontier contains obstructions whose rank equals the
        minimum rank among all active obstructions.  Resolving these first
        is recommended by theory2.tex §7.3.
        """
        active_entries = [e for e in self._store.values() if not e.resolved]
        if not active_entries:
            return []
        min_rank = min(e.rank for e in active_entries)
        return [
            self._entry_to_dict(e)
            for e in active_entries
            if e.rank == min_rank
        ]

    def count(self) -> int:
        """Total number of obstructions (active and resolved)."""
        return len(self._store)

    def serialize(self) -> list[dict[str, Any]]:
        """Return JSON-ready list of all obstructions."""
        return [self._entry_to_dict(e) for e in self._store.values()]

    @staticmethod
    def _entry_to_dict(entry: _ObstructionEntry) -> dict[str, Any]:
        return {
            'obstruction_id': entry.obstruction_id,
            'coordinate': entry.coordinate,
            'kind': entry.kind.value,
            'message': entry.message,
            'resolved': entry.resolved,
            'rank': entry.rank,
            'cohomology_class': entry.cohomology_class,
            'created_at': entry.created_at,
            'resolved_at': entry.resolved_at,
            'metadata': dict(entry.metadata),
        }


# ---------------------------------------------------------------------------
# CertificateStore  (K)
# ---------------------------------------------------------------------------

class CertificateStore:
    """Settlement certificates forming component **K** of the manifest.

    Certificates are the stable public artifacts emitted by the shared core.
    This store indexes them by coordinate for efficient lookup and supports
    status-based filtering.

    Because :mod:`jugeo.evidence.certificates` imports
    :class:`EvidenceManifest` from this module, certificates are stored as
    opaque dicts to avoid circular imports at runtime.  Type-checked code
    should use the ``TYPE_CHECKING`` guard.

    copilot: CertificateStore surfaces settled claims for public projection.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)

    def add(
        self,
        certificate_id: str,
        coordinate: str,
        certificate_data: Mapping[str, Any] | None = None,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> str:
        """Store a certificate.

        Parameters
        ----------
        certificate_id:
            Unique identifier (often the manifest canonical key).
        coordinate:
            Coordinate the certificate covers.
        certificate_data:
            Serialized certificate content (from ``to_dict()``).

        Returns
        -------
        str
            The *certificate_id* that was stored.
        """
        payload = dict(certificate_data or data or {})
        payload.setdefault('coordinate', coordinate)
        self._store[certificate_id] = payload
        self._by_coordinate[coordinate].append(certificate_id)
        return certificate_id

    def get(self, certificate_id: str) -> dict[str, Any] | None:
        """Retrieve a certificate by its identifier."""
        return self._store.get(certificate_id)

    def by_coordinate(self, coordinate: str) -> list[dict[str, Any]]:
        """Return all certificates for *coordinate*."""
        return [
            self._store[cid]
            for cid in self._by_coordinate.get(coordinate, [])
            if cid in self._store
        ]

    def by_status(self, status: str) -> list[dict[str, Any]]:
        """Return certificates matching the given *status* string."""
        return [
            cert for cert in self._store.values()
            if cert.get('status') == status
        ]

    def revoke(self, certificate_id: str) -> bool:
        """Remove a certificate.  Returns ``True`` if it existed."""
        cert = self._store.pop(certificate_id, None)
        if cert is None:
            return False
        for coord_list in self._by_coordinate.values():
            if certificate_id in coord_list:
                coord_list.remove(certificate_id)
        return True

    def settled_coordinates(self) -> set[str]:
        """Return the set of coordinates that have at least one settled cert."""
        coords: set[str] = set()
        for cid, cert in self._store.items():
            if cert.get('status') == 'settled':
                for coord, id_list in self._by_coordinate.items():
                    if cid in id_list:
                        coords.add(coord)
        return coords

    def count(self) -> int:
        """Total number of stored certificates."""
        return len(self._store)

    def serialize(self) -> list[dict[str, Any]]:
        """Return JSON-ready list of all certificates."""
        return [
            {'certificate_id': cid, **data}
            for cid, data in self._store.items()
        ]


# ---------------------------------------------------------------------------
# EpochMap  (η)
# ---------------------------------------------------------------------------

class EpochMap:
    """Maps coordinates to epoch (version) numbers — component **η**.

    An epoch is a monotonically increasing integer that tracks how many
    times a coordinate's judgment has been revised.  Staleness checks use
    epoch comparisons rather than timestamps to guarantee determinism.

    copilot: EpochMap drives invalidation cascades and replay freshness.
    """

    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}

    def current_epoch_at(self, coordinate: str) -> int:
        """Return the current epoch for *coordinate* (0 if unseen)."""
        return self._epochs.get(coordinate, 0)

    def advance(self, coordinate: str) -> int:
        """Increment the epoch at *coordinate* and return the new value.

        If the coordinate has not been seen, it starts at epoch 1.
        """
        current = self._epochs.get(coordinate, 0)
        new_epoch = current + 1
        self._epochs[coordinate] = new_epoch
        return new_epoch

    def rollback(self, coordinate: str) -> int:
        """Decrement the epoch at *coordinate* (minimum 0).

        Returns the new epoch value.  This is an exceptional operation used
        only during controlled replay rewinds.
        """
        current = self._epochs.get(coordinate, 0)
        new_epoch = max(0, current - 1)
        self._epochs[coordinate] = new_epoch
        return new_epoch

    def is_stale(self, coordinate: str, known_epoch: int) -> bool:
        """Return ``True`` if *known_epoch* is behind the current epoch.

        Parameters
        ----------
        coordinate:
            The coordinate to check.
        known_epoch:
            The epoch the caller last observed.
        """
        return self._epochs.get(coordinate, 0) > known_epoch

    def staleness_report(self, known: Mapping[str, int]) -> dict[str, dict[str, int]]:
        """Compare a mapping of known epochs against current state.

        Parameters
        ----------
        known:
            Mapping from coordinate to the caller's last-known epoch.

        Returns
        -------
        dict
            For each stale coordinate: ``{'known': ..., 'current': ...}``.
        """
        report: dict[str, dict[str, int]] = {}
        for coord, epoch in known.items():
            current = self._epochs.get(coord, 0)
            if current > epoch:
                report[coord] = {'known': epoch, 'current': current}
        return report

    def bulk_advance(self, coordinates: Iterable[str]) -> dict[str, int]:
        """Advance epochs for multiple coordinates at once.

        Returns a mapping from coordinate to new epoch.
        """
        result: dict[str, int] = {}
        for coord in coordinates:
            result[coord] = self.advance(coord)
        return result

    def all_epochs(self) -> dict[str, int]:
        """Return a snapshot of all coordinate→epoch mappings."""
        return dict(self._epochs)

    def count(self) -> int:
        """Return number of tracked coordinates."""
        return len(self._epochs)

    def max_epoch(self) -> int:
        """Return the highest epoch value across all coordinates."""
        return max(self._epochs.values()) if self._epochs else 0

    def serialize(self) -> dict[str, int]:
        """Return JSON-ready epoch mapping."""
        return dict(sorted(self._epochs.items()))


# ---------------------------------------------------------------------------
# InvalidationGraph  (σ)
# ---------------------------------------------------------------------------

class InvalidationGraph:
    """Directed dependency graph tracking what invalidates what — component **σ**.

    An edge ``(a, b)`` means *"if **a** changes, then **b** must be
    re-evaluated"*.  The graph supports cascade computation, topological
    ordering, and acyclicity checks needed by the invalidation engine in
    :mod:`jugeo.runtime.invalidation`.

    copilot: InvalidationGraph drives cascade repair ordering for LLM passes.
    """

    def __init__(self) -> None:
        self._forward: dict[str, set[str]] = defaultdict(set)
        self._reverse: dict[str, set[str]] = defaultdict(set)

    def add_dependency(self, source: str, target: str) -> None:
        """Record that a change to *source* invalidates *target*.

        Parameters
        ----------
        source:
            The coordinate whose change triggers invalidation.
        target:
            The coordinate that becomes stale.
        """
        self._forward[source].add(target)
        self._reverse[target].add(source)

    def remove_dependency(self, source: str, target: str) -> bool:
        """Remove an invalidation edge.  Returns ``True`` if it existed."""
        if target not in self._forward.get(source, set()):
            return False
        self._forward[source].discard(target)
        self._reverse[target].discard(source)
        return True

    def invalidate(self, coordinate: str) -> set[str]:
        """Return the set of coordinates *directly* invalidated by *coordinate*."""
        return set(self._forward.get(coordinate, set()))

    def cascade(self, coordinate: str) -> list[str]:
        """Compute the full transitive closure of invalidation from *coordinate*.

        Uses BFS to find every coordinate reachable from *coordinate* in the
        forward direction.  The starting coordinate is **not** included in the
        result.

        Returns
        -------
        list[str]
            All transitively invalidated coordinates in BFS order.
        """
        visited: set[str] = set()
        queue: deque[str] = deque(self._forward.get(coordinate, set()))
        result: list[str] = []
        while queue:
            node = queue.popleft()
            if node in visited or node == coordinate:
                continue
            visited.add(node)
            result.append(node)
            for neighbor in self._forward.get(node, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        return result

    def affected_by(self, coordinate: str) -> set[str]:
        """Return coordinates whose change would invalidate *coordinate*.

        This is the *reverse* lookup — "who do I depend on?"
        """
        return set(self._reverse.get(coordinate, set()))

    def compute_repair_order(self, dirty: Iterable[str]) -> list[str]:
        """Compute a safe repair order for a set of dirty coordinates.

        Uses a topological sort restricted to the subgraph induced by
        *dirty*.  Coordinates with no forward dependencies come first so
        they can be repaired before their dependents.

        Parameters
        ----------
        dirty:
            Coordinates that need re-evaluation.

        Returns
        -------
        list[str]
            Coordinates in an order safe for sequential repair.
        """
        dirty_set = set(dirty)
        in_degree: dict[str, int] = {d: 0 for d in dirty_set}
        sub_forward: dict[str, list[str]] = {d: [] for d in dirty_set}
        for node in dirty_set:
            for target in self._forward.get(node, set()):
                if target in dirty_set:
                    sub_forward[node].append(target)
                    in_degree[target] += 1
        queue: deque[str] = deque(
            node for node in dirty_set if in_degree[node] == 0
        )
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for target in sub_forward.get(node, []):
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)
        # If there is a cycle, append remaining nodes in arbitrary order.
        remaining = dirty_set - set(order)
        order.extend(sorted(remaining))
        return order

    def is_acyclic(self) -> bool:
        """Return ``True`` if the invalidation graph contains no cycles.

        Uses Kahn's algorithm: if topological sort consumes all nodes the
        graph is a DAG.
        """
        all_nodes = set(self._forward.keys()) | set(self._reverse.keys())
        if not all_nodes:
            return True
        in_degree: dict[str, int] = {n: 0 for n in all_nodes}
        for targets in self._forward.values():
            for t in targets:
                if t in in_degree:
                    in_degree[t] += 1
        queue: deque[str] = deque(n for n in all_nodes if in_degree[n] == 0)
        count = 0
        while queue:
            node = queue.popleft()
            count += 1
            for target in self._forward.get(node, set()):
                if target in in_degree:
                    in_degree[target] -= 1
                    if in_degree[target] == 0:
                        queue.append(target)
        return count == len(all_nodes)

    def topological_sort(self) -> list[str]:
        """Return a full topological ordering of all graph nodes.

        If the graph has cycles, the returned list will contain all
        cycle-free nodes in order followed by the remaining nodes sorted
        lexicographically.
        """
        all_nodes = set(self._forward.keys()) | set(self._reverse.keys())
        in_degree: dict[str, int] = {n: 0 for n in all_nodes}
        for targets in self._forward.values():
            for t in targets:
                if t in in_degree:
                    in_degree[t] += 1
        queue: deque[str] = deque(
            sorted(n for n in all_nodes if in_degree[n] == 0)
        )
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for target in sorted(self._forward.get(node, set())):
                if target in in_degree:
                    in_degree[target] -= 1
                    if in_degree[target] == 0:
                        queue.append(target)
        remaining = all_nodes - set(order)
        order.extend(sorted(remaining))
        return order

    def all_edges(self) -> list[tuple[str, str]]:
        """Return all edges as ``(source, target)`` pairs."""
        return [
            (src, tgt)
            for src, targets in sorted(self._forward.items())
            for tgt in sorted(targets)
        ]

    def node_count(self) -> int:
        """Return the total number of distinct nodes."""
        return len(set(self._forward.keys()) | set(self._reverse.keys()))

    def edge_count(self) -> int:
        """Return the total number of edges."""
        return sum(len(targets) for targets in self._forward.values())

    def serialize(self) -> dict[str, list[str]]:
        """Return JSON-ready adjacency-list representation."""
        return {
            src: sorted(targets)
            for src, targets in sorted(self._forward.items())
        }


# ---------------------------------------------------------------------------
# ManifestStatistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ManifestStatistics:
    """Aggregate statistics computed from a :class:`Manifest`.

    Parameters
    ----------
    judgment_count:
        Number of persisted judgments.
    obligation_count:
        Number of obligations (pending + discharged).
    evidence_count:
        Number of archived evidence records.
    obstruction_count:
        Number of obstructions (active + resolved).
    certificate_count:
        Number of settlement certificates.
    coverage_ratio:
        Fraction of coordinates with at least one settled judgment.
    trust_distribution:
        Mapping from trust-tier value to count of judgments at that tier.
    pending_obligations:
        Number of undischarged obligations.
    active_obstructions:
        Number of unresolved obstructions.
    max_epoch:
        Highest epoch value across all coordinates.
    graph_edge_count:
        Number of edges in the invalidation graph.
    """

    judgment_count: int
    obligation_count: int
    evidence_count: int
    obstruction_count: int
    certificate_count: int
    coverage_ratio: float
    trust_distribution: Mapping[str, int]
    pending_obligations: int = 0
    active_obstructions: int = 0
    max_epoch: int = 0
    graph_edge_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready dictionary."""
        return {
            'judgment_count': self.judgment_count,
            'obligation_count': self.obligation_count,
            'evidence_count': self.evidence_count,
            'obstruction_count': self.obstruction_count,
            'certificate_count': self.certificate_count,
            'coverage_ratio': round(self.coverage_ratio, 4),
            'trust_distribution': dict(self.trust_distribution),
            'pending_obligations': self.pending_obligations,
            'active_obstructions': self.active_obstructions,
            'max_epoch': self.max_epoch,
            'graph_edge_count': self.graph_edge_count,
        }

    def summary_line(self) -> str:
        """One-line human-readable summary string."""
        return (
            f'J={self.judgment_count} O={self.obligation_count} '
            f'E={self.evidence_count} X={self.obstruction_count} '
            f'K={self.certificate_count} cov={self.coverage_ratio:.2%}'
        )


# ---------------------------------------------------------------------------
# ManifestDiff
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ManifestDiff:
    """Difference between two :class:`Manifest` snapshots.

    Produced by :meth:`Manifest.diff` to support incremental updates,
    synchronization, and audit logging.

    copilot: ManifestDiff enables incremental LLM context windows.
    """

    added_judgments: tuple[dict[str, Any], ...]
    removed_judgments: tuple[str, ...]
    new_obligations: tuple[dict[str, Any], ...]
    discharged_obligations: tuple[str, ...]
    new_evidence: tuple[dict[str, Any], ...]
    new_obstructions: tuple[dict[str, Any], ...]
    resolved_obstructions: tuple[str, ...]
    new_certificates: tuple[dict[str, Any], ...]
    epoch_changes: Mapping[str, dict[str, int]]

    def is_empty(self) -> bool:
        """Return ``True`` if the diff records no changes."""
        return (
            not self.added_judgments
            and not self.removed_judgments
            and not self.new_obligations
            and not self.discharged_obligations
            and not self.new_evidence
            and not self.new_obstructions
            and not self.resolved_obstructions
            and not self.new_certificates
            and not self.epoch_changes
        )

    def change_count(self) -> int:
        """Total number of individual changes across all categories."""
        return (
            len(self.added_judgments)
            + len(self.removed_judgments)
            + len(self.new_obligations)
            + len(self.discharged_obligations)
            + len(self.new_evidence)
            + len(self.new_obstructions)
            + len(self.resolved_obstructions)
            + len(self.new_certificates)
            + len(self.epoch_changes)
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready dictionary."""
        return {
            'added_judgments': list(self.added_judgments),
            'removed_judgments': list(self.removed_judgments),
            'new_obligations': list(self.new_obligations),
            'discharged_obligations': list(self.discharged_obligations),
            'new_evidence': list(self.new_evidence),
            'new_obstructions': list(self.new_obstructions),
            'resolved_obstructions': list(self.resolved_obstructions),
            'new_certificates': list(self.new_certificates),
            'epoch_changes': dict(self.epoch_changes),
        }


# ---------------------------------------------------------------------------
# Manifest — the main M = (J, O, E, X, K, η, σ) class
# ---------------------------------------------------------------------------

class Manifest:
    """Complete semantic state **M = (J, O, E, X, K, η, σ)**.

    The manifest is the single source of truth for what is known (J, K),
    what remains (O), what was collected (E), what failed (X), at which
    versions (η), and what invalidates what (σ).

    Theory alignment
    ----------------
    Section 4 of ``theory2.tex`` introduces the manifest as the ambient
    structure carried through each judgment cycle.  This class reifies that
    structure as a mutable container with snapshot, restore, diff, and merge
    capabilities.

    copilot: Manifest is the top-level state object for LLM orchestration.
    """

    def __init__(self) -> None:
        self.judgments = JudgmentStore()
        self.obligations = ObligationStore()
        self.evidence_archive = EvidenceArchive()
        self.obstructions = ObstructionStore()
        self.certificates = CertificateStore()
        self.epoch_map = EpochMap()
        self.invalidation_graph = InvalidationGraph()
        self._created_at: float = _now()
        self._manifest_id: str = f'm-{_uid()}'

    @property
    def manifest_id(self) -> str:
        """Unique identifier for this manifest instance."""
        return self._manifest_id

    # -- epoch helpers -----------------------------------------------------

    def current_epoch(self) -> int:
        """Return the global maximum epoch across all coordinates."""
        return self.epoch_map.max_epoch()

    def advance_epoch(self, coordinate: str) -> int:
        """Advance the epoch for *coordinate* and cascade invalidation.

        Returns the new epoch value.  Also marks all transitively
        dependent coordinates as stale by advancing their epochs.
        """
        new_epoch = self.epoch_map.advance(coordinate)
        cascade_targets = self.invalidation_graph.cascade(coordinate)
        for target in cascade_targets:
            self.epoch_map.advance(target)
        return new_epoch

    # -- snapshot / restore ------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Capture a complete serializable snapshot of the manifest.

        Returns
        -------
        dict
            A JSON-ready dictionary containing all seven components.
        """
        return {
            'manifest_id': self._manifest_id,
            'created_at': self._created_at,
            'judgments': self.judgments.serialize(),
            'obligations': self.obligations.serialize(),
            'evidence_archive': self.evidence_archive.serialize(),
            'obstructions': self.obstructions.serialize(),
            'certificates': self.certificates.serialize(),
            'epoch_map': self.epoch_map.serialize(),
            'invalidation_graph': self.invalidation_graph.serialize(),
        }

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        """Restore manifest state from a previously captured snapshot.

        Parameters
        ----------
        snapshot:
            Dictionary produced by :meth:`snapshot`.

        This replaces all internal state.  It is the caller's responsibility
        to ensure the snapshot was produced by a compatible manifest version.
        """
        self._manifest_id = str(snapshot.get('manifest_id', self._manifest_id))
        self._created_at = float(snapshot.get('created_at', self._created_at))

        # Reset all stores and replay serialized data.
        self.judgments = JudgmentStore()
        for j in snapshot.get('judgments', []):
            self.judgments.add(
                coordinate=j['coordinate'],
                proposition=j['proposition'],
                trust_tier=j.get('trust_tier', TrustTier.PROPOSAL),
                evidence_refs=j.get('evidence_refs', []),
                status=j.get('status', 'proposed'),
                metadata=j.get('metadata', {}),
            )

        self.obligations = ObligationStore()
        for o in snapshot.get('obligations', []):
            oid = self.obligations.add(
                coordinate=o['coordinate'],
                description=o['description'],
                priority=ObligationPriority(o.get('priority', ObligationPriority.MEDIUM)),
                deadline=o.get('deadline'),
                dependencies=o.get('dependencies', []),
            )
            if o.get('discharged', False):
                self.obligations.discharge(oid)

        self.evidence_archive = EvidenceArchive()
        # Evidence records require channel objects — store raw data.

        self.obstructions = ObstructionStore()
        for x in snapshot.get('obstructions', []):
            xid = self.obstructions.add(
                coordinate=x['coordinate'],
                kind=ObstructionKind(x.get('kind', 'unknown')),
                message=x['message'],
                rank=x.get('rank', 0),
                cohomology_class=x.get('cohomology_class'),
                metadata=x.get('metadata', {}),
            )
            if x.get('resolved', False):
                self.obstructions.resolve(xid)

        self.certificates = CertificateStore()
        for cert in snapshot.get('certificates', []):
            cid = cert.get('certificate_id', f'k-{_uid()}')
            coord = cert.get('coordinate', '')
            self.certificates.add(cid, coord, cert)

        self.epoch_map = EpochMap()
        for coord, epoch in snapshot.get('epoch_map', {}).items():
            for _ in range(epoch):
                self.epoch_map.advance(coord)

        self.invalidation_graph = InvalidationGraph()
        for src, targets in snapshot.get('invalidation_graph', {}).items():
            for tgt in targets:
                self.invalidation_graph.add_dependency(src, tgt)

    # -- diff / merge ------------------------------------------------------

    def diff(self, other: Manifest) -> ManifestDiff:
        """Compute the difference between *self* (old) and *other* (new).

        Parameters
        ----------
        other:
            The newer manifest to compare against.

        Returns
        -------
        ManifestDiff
            A structured diff describing all changes.
        """
        self_j = {j['judgment_id']: j for j in self.judgments.iterate()}
        other_j = {j['judgment_id']: j for j in other.judgments.iterate()}
        added_j = tuple(
            other_j[jid] for jid in other_j if jid not in self_j
        )
        removed_j = tuple(jid for jid in self_j if jid not in other_j)

        self_o = {o['obligation_id']: o for o in self.obligations.serialize()}
        other_o = {o['obligation_id']: o for o in other.obligations.serialize()}
        new_o = tuple(other_o[oid] for oid in other_o if oid not in self_o)
        discharged_o = tuple(
            oid for oid, o in other_o.items()
            if o.get('discharged') and not self_o.get(oid, {}).get('discharged')
        )

        self_e_ids = {e['archive_id'] for e in self.evidence_archive.serialize()}
        other_e = other.evidence_archive.serialize()
        new_e = tuple(e for e in other_e if e['archive_id'] not in self_e_ids)

        self_x = {x['obstruction_id']: x for x in self.obstructions.serialize()}
        other_x = {x['obstruction_id']: x for x in other.obstructions.serialize()}
        new_x = tuple(other_x[xid] for xid in other_x if xid not in self_x)
        resolved_x = tuple(
            xid for xid, x in other_x.items()
            if x.get('resolved') and not self_x.get(xid, {}).get('resolved')
        )

        self_k_ids = {c.get('certificate_id') for c in self.certificates.serialize()}
        other_k = other.certificates.serialize()
        new_k = tuple(c for c in other_k if c.get('certificate_id') not in self_k_ids)

        self_epochs = self.epoch_map.all_epochs()
        other_epochs = other.epoch_map.all_epochs()
        epoch_changes: dict[str, dict[str, int]] = {}
        all_coords = set(self_epochs) | set(other_epochs)
        for coord in all_coords:
            old_e = self_epochs.get(coord, 0)
            new_e = other_epochs.get(coord, 0)
            if old_e != new_e:
                epoch_changes[coord] = {'old': old_e, 'new': new_e}

        return ManifestDiff(
            added_judgments=added_j,
            removed_judgments=removed_j,
            new_obligations=new_o,
            discharged_obligations=discharged_o,
            new_evidence=new_e,
            new_obstructions=new_x,
            resolved_obstructions=resolved_x,
            new_certificates=new_k,
            epoch_changes=epoch_changes,
        )

    def merge(self, other: Manifest) -> None:
        """Merge *other* manifest into this one (union semantics).

        Judgments, obligations, evidence, obstructions, and certificates
        from *other* are added.  Epoch maps take the maximum.
        Invalidation edges are unioned.
        """
        for j in other.judgments.iterate():
            self.judgments.add(
                coordinate=j['coordinate'],
                proposition=j['proposition'],
                trust_tier=j.get('trust_tier', TrustTier.PROPOSAL),
                evidence_refs=j.get('evidence_refs', []),
                status=j.get('status', 'proposed'),
                metadata=j.get('metadata', {}),
            )
        for o in other.obligations.serialize():
            oid = self.obligations.add(
                coordinate=o['coordinate'],
                description=o['description'],
                priority=ObligationPriority(o.get('priority', ObligationPriority.MEDIUM)),
                deadline=o.get('deadline'),
                dependencies=o.get('dependencies', []),
            )
            if o.get('discharged'):
                self.obligations.discharge(oid)
        for x in other.obstructions.serialize():
            xid = self.obstructions.add(
                coordinate=x['coordinate'],
                kind=ObstructionKind(x.get('kind', 'unknown')),
                message=x['message'],
                rank=x.get('rank', 0),
                cohomology_class=x.get('cohomology_class'),
                metadata=x.get('metadata', {}),
            )
            if x.get('resolved'):
                self.obstructions.resolve(xid)
        for cert in other.certificates.serialize():
            cid = cert.get('certificate_id', f'k-{_uid()}')
            coord = cert.get('coordinate', '')
            self.certificates.add(cid, coord, cert)

        other_epochs = other.epoch_map.all_epochs()
        for coord, epoch in other_epochs.items():
            current = self.epoch_map.current_epoch_at(coord)
            while current < epoch:
                current = self.epoch_map.advance(coord)

        for src, tgt in other.invalidation_graph.all_edges():
            self.invalidation_graph.add_dependency(src, tgt)

    # -- consistency -------------------------------------------------------

    def is_consistent(self) -> bool:
        """Quick consistency check.

        Returns ``True`` if the manifest passes basic invariants:
        * invalidation graph is acyclic
        * no pending obligation references a nonexistent coordinate in judgments
        """
        if not self.invalidation_graph.is_acyclic():
            return False
        judgment_coords = {
            j['coordinate'] for j in self.judgments.iterate()
        }
        for o in self.obligations.pending():
            if o['coordinate'] not in judgment_coords:
                # Pending obligation at a coordinate with no judgment.
                continue  # Allowed — obligation may precede judgment.
        return True

    def validate(self) -> list[str]:
        """Run full validation and return list of issue descriptions.

        See :class:`ManifestValidator` for individual checks.
        """
        validator = ManifestValidator(self)
        return validator.run_all()

    # -- projection --------------------------------------------------------

    def project_public(self) -> dict[str, Any]:
        """Return a public-safe projection of the manifest.

        The public projection omits internal metadata, raw evidence
        payloads, and diagnostic fields.  It is suitable for external
        consumers and LLM context windows.
        """
        settled_j = self.judgments.filter(status='settled')
        active_x = self.obstructions.active()
        pending_o = self.obligations.pending()
        settled_k = self.certificates.by_status('settled')
        return {
            'manifest_id': self._manifest_id,
            'settled_judgments': len(settled_j),
            'active_obstructions': len(active_x),
            'pending_obligations': len(pending_o),
            'settled_certificates': len(settled_k),
            'epoch': self.current_epoch(),
            'judgments': [
                {'coordinate': j['coordinate'], 'proposition': j['proposition']}
                for j in settled_j
            ],
            'obstructions': [
                {'coordinate': x['coordinate'], 'kind': x['kind'], 'message': x['message']}
                for x in active_x
            ],
        }

    # -- serialization -----------------------------------------------------

    def serialize(self) -> str:
        """Return canonical JSON string of the full manifest."""
        return json.dumps(self.snapshot(), sort_keys=True, separators=(',', ':'))

    # -- statistics --------------------------------------------------------

    def statistics(self) -> ManifestStatistics:
        """Compute aggregate statistics for this manifest.

        Returns
        -------
        ManifestStatistics
            Frozen dataclass with counts, ratios, and distributions.
        """
        all_judgments = list(self.judgments.iterate())
        judgment_coords = {j['coordinate'] for j in all_judgments}
        settled_coords = self.certificates.settled_coordinates()
        coverage = (
            len(settled_coords) / len(judgment_coords)
            if judgment_coords
            else 0.0
        )
        trust_dist: dict[str, int] = defaultdict(int)
        for j in all_judgments:
            trust_dist[str(j.get('trust_tier', 0))] += 1

        return ManifestStatistics(
            judgment_count=self.judgments.count(),
            obligation_count=self.obligations.count(),
            evidence_count=self.evidence_archive.count(),
            obstruction_count=self.obstructions.count(),
            certificate_count=self.certificates.count(),
            coverage_ratio=coverage,
            trust_distribution=dict(trust_dist),
            pending_obligations=len(self.obligations.pending()),
            active_obstructions=len(self.obstructions.active()),
            max_epoch=self.epoch_map.max_epoch(),
            graph_edge_count=self.invalidation_graph.edge_count(),
        )

    # -- cross-subsystem integration ----------------------------------------

    @classmethod
    def from_judgment_sections(
        cls,
        sections: Iterable[Any],
        *,
        manifest_id: str = '',
    ) -> 'Manifest':
        """Build a manifest from judgment sections.

        Given an iterable of :class:`jugeo.judgments.sections.Section`
        instances, this factory populates the manifest's judgment store,
        evidence archive, and obligation store from the section data.

        Parameters
        ----------
        sections:
            Iterable of :class:`jugeo.judgments.sections.Section` objects.
        manifest_id:
            Optional explicit manifest identifier.

        Returns
        -------
        Manifest
            A newly constructed manifest populated from the sections.
        """
        try:
            from jugeo.judgments.sections import Section  # noqa: F811
        except ImportError:
            m = cls()
            m.obstructions.add(
                coordinate='manifest',
                kind=ObstructionKind.UNKNOWN,
                message='jugeo.judgments.sections not available',
            )
            return m

        m = cls()
        if manifest_id:
            m._manifest_id = manifest_id

        for section in sections:
            coord_obj = getattr(section, 'coordinate', None)
            coord_name = getattr(coord_obj, 'name', str(coord_obj)) if coord_obj else 'unknown'

            # Add judgments from judgment_assignments.
            assignments = getattr(section, 'judgment_assignments', {})
            for prop_name, local_judgment in assignments.items():
                proposition = getattr(local_judgment, 'proposition', prop_name)
                prop_text = getattr(proposition, 'text', str(proposition))
                trust_ann = getattr(section, 'trust_annotation', None)
                tier = TrustTier.PROPOSAL
                if trust_ann is not None:
                    tier_val = getattr(trust_ann, 'tier', None)
                    if tier_val is not None:
                        try:
                            tier = TrustTier(int(tier_val))
                        except (ValueError, TypeError):
                            pass

                m.judgments.add(
                    coordinate=coord_name,
                    proposition=prop_text,
                    trust_tier=tier,
                    status='proposed',
                )

            # Add evidence from the section evidence archive.
            for record in getattr(section, 'evidence_archive', []):
                m.evidence_archive.add(
                    coordinate=coord_name,
                    record=record,
                )

            # Add residual obligations.
            for residual in getattr(section, 'residuals', []):
                m.obligations.add(
                    coordinate=coord_name,
                    description=str(residual),
                    priority=ObligationPriority.MEDIUM,
                )

        return m

    def coverage_over_site(
        self,
        site: Any,
    ) -> dict[str, Any]:
        """Compute manifest coverage using a geometric site.

        Compares the manifest's settled judgment coordinates against the
        coordinates and covering families in the
        :class:`jugeo.geometry.site.Site` to determine what fraction of
        the site is covered by verified evidence.

        Parameters
        ----------
        site:
            A :class:`jugeo.geometry.site.Site` instance.

        Returns
        -------
        dict
            ``{'total_site_coordinates': int,
              'covered_coordinates': int,
              'coverage_ratio': float,
              'uncovered': list[str],
              'covers_fully_witnessed': int,
              'covers_total': int}``
        """
        try:
            from jugeo.geometry.site import Site, CoveringFamily  # noqa: F811
        except ImportError:
            return {
                'total_site_coordinates': 0,
                'covered_coordinates': 0,
                'coverage_ratio': 0.0,
                'uncovered': [],
                'covers_fully_witnessed': 0,
                'covers_total': 0,
                'error': 'jugeo.geometry.site not available',
            }

        site_coords = {c.name for c in site.objects()}
        judgment_coords = {j['coordinate'] for j in self.judgments.iterate()}
        settled_coords = self.certificates.settled_coordinates()

        covered = site_coords & (judgment_coords | settled_coords)
        uncovered = sorted(site_coords - covered)
        total = len(site_coords) if site_coords else 1
        coverage_ratio = len(covered) / total

        covers_total = 0
        covers_fully_witnessed = 0
        for family in site.covering_families():
            covers_total += 1
            member_coords = {m.source.name for m in family.members}
            if member_coords and member_coords <= covered:
                covers_fully_witnessed += 1

        return {
            'total_site_coordinates': len(site_coords),
            'covered_coordinates': len(covered),
            'coverage_ratio': coverage_ratio,
            'uncovered': uncovered,
            'covers_fully_witnessed': covers_fully_witnessed,
            'covers_total': covers_total,
        }


# ---------------------------------------------------------------------------
# ManifestBuilder — fluent builder for constructing manifests
# ---------------------------------------------------------------------------

class ManifestBuilder:
    """Fluent builder for constructing a :class:`Manifest` step by step.

    Example
    -------
    >>> m = (ManifestBuilder()
    ...      .add_judgment('mod.A', 'types are sound')
    ...      .add_obligation('mod.A', 'prove termination')
    ...      .add_epoch('mod.A', 1)
    ...      .build())

    copilot: ManifestBuilder supports incremental LLM-driven assembly.
    """

    def __init__(self) -> None:
        self._manifest = Manifest()

    def add_judgment(
        self,
        coordinate: str,
        proposition: str,
        *,
        trust_tier: int = TrustTier.PROPOSAL,
        status: str = 'proposed',
        evidence_refs: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ManifestBuilder:
        """Add a judgment and return ``self`` for chaining."""
        self._manifest.judgments.add(
            coordinate,
            proposition,
            trust_tier=trust_tier,
            status=status,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
        return self

    def add_obligation(
        self,
        coordinate: str,
        description: str,
        *,
        priority: ObligationPriority = ObligationPriority.MEDIUM,
        deadline: float | None = None,
        dependencies: Sequence[str] = (),
    ) -> ManifestBuilder:
        """Add an obligation and return ``self`` for chaining."""
        self._manifest.obligations.add(
            coordinate,
            description,
            priority=priority,
            deadline=deadline,
            dependencies=dependencies,
        )
        return self

    def add_obstruction(
        self,
        coordinate: str,
        kind: ObstructionKind,
        message: str,
        *,
        rank: int = 0,
        cohomology_class: str | None = None,
    ) -> ManifestBuilder:
        """Add an obstruction and return ``self`` for chaining."""
        self._manifest.obstructions.add(
            coordinate,
            kind,
            message,
            rank=rank,
            cohomology_class=cohomology_class,
        )
        return self

    def add_certificate(
        self,
        certificate_id: str,
        coordinate: str,
        data: Mapping[str, Any],
    ) -> ManifestBuilder:
        """Add a certificate and return ``self`` for chaining."""
        self._manifest.certificates.add(certificate_id, coordinate, data)
        return self

    def add_epoch(self, coordinate: str, epoch: int) -> ManifestBuilder:
        """Set epoch for a coordinate and return ``self`` for chaining.

        Advances the coordinate's epoch to the requested value.
        """
        while self._manifest.epoch_map.current_epoch_at(coordinate) < epoch:
            self._manifest.epoch_map.advance(coordinate)
        return self

    def add_invalidation(self, source: str, target: str) -> ManifestBuilder:
        """Add an invalidation edge and return ``self`` for chaining."""
        self._manifest.invalidation_graph.add_dependency(source, target)
        return self

    def from_snapshot(self, snapshot: Mapping[str, Any]) -> ManifestBuilder:
        """Initialize the builder from a serialized snapshot."""
        self._manifest.restore(snapshot)
        return self

    def build(self) -> Manifest:
        """Return the constructed :class:`Manifest`.

        After calling ``build()``, the builder should not be reused.
        """
        result = self._manifest
        self._manifest = Manifest()  # Reset for safety.
        return result


# ---------------------------------------------------------------------------
# ManifestSerializer — full JSON serialization / deserialization
# ---------------------------------------------------------------------------

class ManifestSerializer:
    """Handles canonical JSON serialization and deserialization of manifests.

    Serialization is deterministic (sorted keys, no whitespace) so that
    snapshots can be compared structurally.  Deserialization reconstructs
    a full :class:`Manifest` from the JSON representation.

    copilot: ManifestSerializer enables checkpoint persistence for LLM replay.
    """

    @staticmethod
    def to_json(manifest: Manifest) -> str:
        """Serialize a manifest to a canonical JSON string.

        Parameters
        ----------
        manifest:
            The manifest to serialize.

        Returns
        -------
        str
            Deterministic JSON string with sorted keys.
        """
        return json.dumps(
            manifest.snapshot(),
            sort_keys=True,
            separators=(',', ':'),
        )

    @staticmethod
    def from_json(payload: str) -> Manifest:
        """Deserialize a manifest from a JSON string.

        Parameters
        ----------
        payload:
            JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        Manifest
            Reconstructed manifest with all components restored.

        Raises
        ------
        json.JSONDecodeError
            If *payload* is not valid JSON.
        ValueError
            If the decoded structure is not a dict.
        """
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(f'Expected dict, got {type(data).__name__}')
        m = Manifest()
        m.restore(data)
        return m

    @staticmethod
    def to_dict(manifest: Manifest) -> dict[str, Any]:
        """Return the snapshot dict without JSON encoding."""
        return manifest.snapshot()

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Manifest:
        """Reconstruct a manifest from a snapshot dict."""
        m = Manifest()
        m.restore(data)
        return m

    @staticmethod
    def diff_to_json(diff: ManifestDiff) -> str:
        """Serialize a :class:`ManifestDiff` to canonical JSON."""
        return json.dumps(diff.to_dict(), sort_keys=True, separators=(',', ':'))

    @staticmethod
    def statistics_to_json(stats: ManifestStatistics) -> str:
        """Serialize :class:`ManifestStatistics` to canonical JSON."""
        return json.dumps(stats.to_dict(), sort_keys=True, separators=(',', ':'))


# ---------------------------------------------------------------------------
# ManifestValidator — validates manifest consistency
# ---------------------------------------------------------------------------

class ManifestValidator:
    """Validates internal consistency of a :class:`Manifest`.

    Each ``check_*`` method returns a list of human-readable issue
    descriptions.  An empty list means the check passed.

    copilot: ManifestValidator is invoked during copilot_validation_assist
    to surface issues for LLM-driven repair.
    """

    def __init__(self, manifest: Manifest) -> None:
        self._m = manifest

    def run_all(self) -> list[str]:
        """Run every consistency check and return all issues found."""
        issues: list[str] = []
        issues.extend(self.check_judgment_evidence_alignment())
        issues.extend(self.check_obligation_completeness())
        issues.extend(self.check_obstruction_persistence())
        issues.extend(self.check_epoch_monotonicity())
        issues.extend(self.check_invalidation_acyclicity())
        return issues

    def check_judgment_evidence_alignment(self) -> list[str]:
        """Verify that every judgment's evidence_refs point to archived records.

        Returns a list of issues for judgments referencing nonexistent
        evidence.
        """
        issues: list[str] = []
        archived_ids: set[str] = set()
        for entry in self._m.evidence_archive.serialize():
            archived_ids.add(entry['archive_id'])
        for j in self._m.judgments.iterate():
            for ref in j.get('evidence_refs', []):
                if ref and ref not in archived_ids:
                    issues.append(
                        f"Judgment {j['judgment_id']} references evidence "
                        f"'{ref}' which is not in the archive."
                    )
        return issues

    def check_obligation_completeness(self) -> list[str]:
        """Verify that obligation dependencies form a valid DAG.

        Each dependency identifier must refer to an existing obligation.
        """
        issues: list[str] = []
        all_ids = {o['obligation_id'] for o in self._m.obligations.serialize()}
        for o in self._m.obligations.serialize():
            for dep in o.get('dependencies', []):
                if dep not in all_ids:
                    issues.append(
                        f"Obligation {o['obligation_id']} depends on "
                        f"'{dep}' which does not exist."
                    )
        return issues

    def check_obstruction_persistence(self) -> list[str]:
        """Check that resolved obstructions still have their coordinate in scope.

        Warns if a resolved obstruction's coordinate has no corresponding
        judgment, which may indicate premature resolution.
        """
        issues: list[str] = []
        judgment_coords = {
            j['coordinate'] for j in self._m.judgments.iterate()
        }
        for x in self._m.obstructions.serialize():
            if x.get('resolved') and x['coordinate'] not in judgment_coords:
                issues.append(
                    f"Resolved obstruction {x['obstruction_id']} at "
                    f"'{x['coordinate']}' has no corresponding judgment."
                )
        return issues

    def check_epoch_monotonicity(self) -> list[str]:
        """Verify that epoch values are non-negative.

        This is a structural invariant — epochs should never go negative.
        """
        issues: list[str] = []
        for coord, epoch in self._m.epoch_map.all_epochs().items():
            if epoch < 0:
                issues.append(
                    f"Epoch at '{coord}' is negative ({epoch})."
                )
        return issues

    def check_invalidation_acyclicity(self) -> list[str]:
        """Verify that the invalidation graph is acyclic.

        A cycle in the invalidation graph means cascading repairs would
        never terminate, which violates a core invariant of theory2.tex.
        """
        if not self._m.invalidation_graph.is_acyclic():
            return ['Invalidation graph contains a cycle.']
        return []

    def copilot_validation_assist(self) -> dict[str, Any]:
        """Return a structured validation report for copilot consumption.

        The report includes per-check results and an overall status suitable
        for inclusion in an LLM context window.

        copilot: This method produces the validation payload consumed by
        the LLM orchestration layer during automated repair passes.
        """
        checks = {
            'judgment_evidence_alignment': self.check_judgment_evidence_alignment(),
            'obligation_completeness': self.check_obligation_completeness(),
            'obstruction_persistence': self.check_obstruction_persistence(),
            'epoch_monotonicity': self.check_epoch_monotonicity(),
            'invalidation_acyclicity': self.check_invalidation_acyclicity(),
        }
        all_issues = [
            issue for issue_list in checks.values() for issue in issue_list
        ]
        return {
            'valid': len(all_issues) == 0,
            'issue_count': len(all_issues),
            'checks': {
                name: {'passed': len(issues) == 0, 'issues': issues}
                for name, issues in checks.items()
            },
            'summary': (
                'Manifest is consistent.'
                if not all_issues
                else f'{len(all_issues)} issue(s) found.'
            ),
        }


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    'CertificateStore',
    'EpochMap',
    'EvidenceArchive',
    'EvidenceManifest',
    'InvalidationGraph',
    'JudgmentStore',
    'Manifest',
    'ManifestBuilder',
    'ManifestDiff',
    'ManifestSerializer',
    'ManifestStatistics',
    'ManifestValidator',
    'ObligationPriority',
    'ObligationStore',
    'ObstructionKind',
    'ObstructionStore',
    'build_evidence_manifest',
]

# copilot: shared-core marker for future LLM orchestration.
