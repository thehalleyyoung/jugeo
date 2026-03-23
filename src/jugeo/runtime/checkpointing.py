"""Semantic checkpointing for JuGeo runtime and manifest state.

This module expands JuGeo checkpointing from a tiny cache snapshot helper into
an audit-grade subsystem aligned with ``theory2.tex``.  A checkpoint marks a
semantic boundary where the runtime can safely pause, resume, or explain the
state transition later.  Typical boundaries include:

* kernel lifecycle phases such as ``establishing-trust`` or
  ``connecting-copilot``;
* major integration and replay boundaries where downstream work depends on a
  stable manifest;
* treaty ratifications that change what overlap laws may be relied upon; and
* recovery points taken before risky invalidation or repair work.

The preserved semantic state is centred on the manifest
``M = (J, O, E, X, K, η, σ)``:

* judgments,
* obligations,
* evidence archive,
* obstructions,
* certificates,
* epoch map, and
* invalidation graph.

The implementation favours deterministic JSON serialization, explicit retention
policy, and enough metadata for a copilot-driven runtime to audit why a
checkpoint was taken and whether it is still safe to restore.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jugeo.evidence.manifests import Manifest, ManifestSerializer
from jugeo.kernel.lifecycle import KernelPhase
from jugeo.runtime.cache import SemanticCache
from jugeo.runtime.memory import SemanticMemory


def _now() -> float:
    """Return the current wall-clock time as UNIX seconds."""

    return time.time()


def _uid(prefix: str) -> str:
    """Return a short stable-looking identifier with the requested prefix."""

    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def _phase_value(phase: str | KernelPhase) -> str:
    """Normalize a lifecycle phase enum or label to its serialized value."""

    if isinstance(phase, KernelPhase):
        return phase.value
    return str(phase).strip().lower()


def _canonical_json(payload: Any) -> str:
    """Serialize *payload* to deterministic JSON for hashing and persistence."""

    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _deepcopy_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied ``dict`` from an arbitrary mapping."""

    return copy.deepcopy(dict(payload))


def _manifest_snapshot(manifest: Manifest | Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a manifest object or serialized snapshot to a mutable dict."""

    if isinstance(manifest, Manifest):
        return copy.deepcopy(manifest.snapshot())
    return _deepcopy_mapping(manifest)


def _epoch_from_manifest(snapshot: Mapping[str, Any]) -> int:
    """Return the maximum epoch value carried by a manifest snapshot."""

    epochs = snapshot.get('epoch_map', {})
    if not isinstance(epochs, Mapping) or not epochs:
        return 0
    return max(int(value) for value in epochs.values())


def _manifest_hash(snapshot: Mapping[str, Any]) -> str:
    """Compute the stable SHA-256 digest for a manifest snapshot."""

    return hashlib.sha256(_canonical_json(snapshot).encode('utf-8')).hexdigest()


def _coordinate_scope_from_manifest(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    """Derive a deterministic coordinate scope from manifest contents."""

    coordinates: set[str] = set()
    for judgment in snapshot.get('judgments', ()):
        coordinate = judgment.get('coordinate')
        if coordinate:
            coordinates.add(str(coordinate))
    for obligation in snapshot.get('obligations', ()):
        coordinate = obligation.get('coordinate')
        if coordinate:
            coordinates.add(str(coordinate))
    for obstruction in snapshot.get('obstructions', ()):
        coordinate = obstruction.get('coordinate')
        if coordinate:
            coordinates.add(str(coordinate))
    coordinates.update(str(key) for key in snapshot.get('epoch_map', {}).keys())
    if not coordinates:
        return ('*',)
    return tuple(sorted(coordinates))


def _checkpoint_payload(
    checkpoint: Checkpoint,
    manifest_snapshot: Mapping[str, Any],
    extra_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the persisted semantic payload for *checkpoint*."""

    payload = _deepcopy_mapping(extra_payload or {})
    manifest_data = copy.deepcopy(dict(manifest_snapshot))
    payload.setdefault('checkpoint', checkpoint.to_dict())
    payload['manifest'] = manifest_data
    payload['judgments'] = copy.deepcopy(list(manifest_data.get('judgments', ())))
    payload['obligations'] = copy.deepcopy(list(manifest_data.get('obligations', ())))
    payload['evidence_archive'] = copy.deepcopy(list(manifest_data.get('evidence_archive', ())))
    payload['obstructions'] = copy.deepcopy(list(manifest_data.get('obstructions', ())))
    payload['certificates'] = copy.deepcopy(list(manifest_data.get('certificates', ())))
    payload['epoch_map'] = copy.deepcopy(dict(manifest_data.get('epoch_map', {})))
    payload['invalidation_graph'] = copy.deepcopy(dict(manifest_data.get('invalidation_graph', {})))
    payload.setdefault('runtime_state', {})
    payload.setdefault('provenance', [])
    payload.setdefault('replay_boundary', {'kind': 'full', 'base_checkpoint_id': None})
    return payload


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Metadata describing a stable semantic checkpoint.

    The dataclass is intentionally compact.  The heavy semantic payload lives in
    :class:`CheckpointStore`, while the checkpoint object itself provides the
    immutable identity, phase, scope, manifest digest, and a human-readable
    summary suitable for auditing and scheduling.
    """

    checkpoint_id: str
    epoch: int
    created_at: float
    coordinate_scope: tuple[str, ...]
    manifest_hash: str
    lifecycle_phase: str
    summary: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize generated defaults and immutable fields."""

        checkpoint_id = self.checkpoint_id or _uid('ckpt')
        scope = tuple(sorted({str(item) for item in self.coordinate_scope if str(item)})) or ('*',)
        object.__setattr__(self, 'checkpoint_id', checkpoint_id)
        object.__setattr__(self, 'coordinate_scope', scope)
        object.__setattr__(self, 'epoch', int(self.epoch))
        object.__setattr__(self, 'created_at', float(self.created_at))
        object.__setattr__(self, 'lifecycle_phase', _phase_value(self.lifecycle_phase))
        object.__setattr__(self, 'summary', str(self.summary).strip() or 'semantic checkpoint')
        object.__setattr__(self, 'metadata', copy.deepcopy(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the checkpoint metadata to a JSON-ready dictionary."""

        return {
            'checkpoint_id': self.checkpoint_id,
            'epoch': self.epoch,
            'created_at': self.created_at,
            'coordinate_scope': list(self.coordinate_scope),
            'manifest_hash': self.manifest_hash,
            'lifecycle_phase': self.lifecycle_phase,
            'summary': self.summary,
            'metadata': copy.deepcopy(dict(self.metadata)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Checkpoint:
        """Deserialize checkpoint metadata from a mapping."""

        return cls(
            checkpoint_id=str(payload.get('checkpoint_id', '')),
            epoch=int(payload.get('epoch', 0)),
            created_at=float(payload.get('created_at', _now())),
            coordinate_scope=tuple(str(item) for item in payload.get('coordinate_scope', ('*',))),
            manifest_hash=str(payload.get('manifest_hash', '')),
            lifecycle_phase=_phase_value(str(payload.get('lifecycle_phase', 'running'))),
            summary=str(payload.get('summary', 'semantic checkpoint')),
            metadata=dict(payload.get('metadata', {})),
        )

    def age_seconds(self, now: float | None = None) -> float:
        """Return the age of the checkpoint in seconds."""

        return max(0.0, (now if now is not None else _now()) - self.created_at)

    def covers_coordinate(self, coordinate: str) -> bool:
        """Return whether *coordinate* lies in the checkpoint scope."""

        if '*' in self.coordinate_scope:
            return True
        return coordinate in self.coordinate_scope

    def matches_phase(self, phase: str | KernelPhase) -> bool:
        """Return whether the checkpoint was taken in *phase*."""

        return self.lifecycle_phase == _phase_value(phase)

    def manifest_locator(self) -> str:
        """Return a compact locator combining phase, epoch, and digest prefix."""

        return f'{self.lifecycle_phase}@{self.epoch}:{self.manifest_hash[:12]}'

    def describe(self) -> str:
        """Return a concise one-line description for logs and diagnostics."""

        return (
            f'[{self.checkpoint_id}] phase={self.lifecycle_phase} '
            f'epoch={self.epoch} scope={len(self.coordinate_scope)} '
            f'hash={self.manifest_hash[:12]} summary={self.summary}'
        )


@dataclass(slots=True)
class CheckpointPolicy:
    """Policy controlling when checkpoints are taken and how long they survive.

    Parameters
    ----------
    frequency:
        Minimum periodic spacing in seconds.
    retention_count:
        Minimum number of most-recent checkpoints to keep even if they are old.
    retention_age:
        Maximum preferred age in seconds before a checkpoint becomes eligible
        for pruning.
    trigger_points:
        Named semantic events that should immediately produce a checkpoint.
    """

    frequency: float = 300.0
    retention_count: int = 10
    retention_age: float = 7 * 24 * 60 * 60
    trigger_points: tuple[str, ...] = (
        'phase-boundary',
        'integration-complete',
        'treaty-ratified',
        'replay-boundary',
        'copilot-handshake',
    )

    def __post_init__(self) -> None:
        """Validate and normalize policy configuration."""

        self.frequency = max(1.0, float(self.frequency))
        self.retention_count = max(1, int(self.retention_count))
        self.retention_age = max(self.frequency, float(self.retention_age))
        self.trigger_points = tuple(sorted({str(point) for point in self.trigger_points if str(point)}))

    def should_checkpoint(
        self,
        event: str | None = None,
        *,
        phase: str | KernelPhase | None = None,
        last_checkpoint_at: float | None = None,
        now: float | None = None,
        pressure: float | None = None,
    ) -> bool:
        """Return whether policy recommends a checkpoint now."""

        current = now if now is not None else _now()
        normalized_event = str(event or '').strip().lower()
        normalized_phase = _phase_value(phase) if phase is not None else ''
        if normalized_event in self.trigger_points:
            return True
        if normalized_phase and normalized_phase in self.trigger_points:
            return True
        if pressure is not None and pressure >= 0.95:
            return True
        if last_checkpoint_at is None:
            return True
        return (current - last_checkpoint_at) >= self.frequency

    def retention_cutoff(self, now: float | None = None) -> float:
        """Return the timestamp before which checkpoints are considered old."""

        reference = now if now is not None else _now()
        return reference - self.retention_age

    def should_retain(
        self,
        checkpoint: Checkpoint,
        *,
        index_from_latest: int,
        now: float | None = None,
    ) -> bool:
        """Return whether *checkpoint* should be kept under this policy."""

        if index_from_latest < self.retention_count:
            return True
        if checkpoint.created_at >= self.retention_cutoff(now):
            return True
        if checkpoint.metadata.get('pinned', False):
            return True
        if checkpoint.metadata.get('event') in self.trigger_points:
            return True
        return False

    def prune_candidates(
        self,
        checkpoints: Sequence[Checkpoint],
        *,
        now: float | None = None,
    ) -> tuple[Checkpoint, ...]:
        """Return checkpoints that policy allows to be deleted."""

        ordered = sorted(checkpoints, key=lambda item: (item.created_at, item.checkpoint_id), reverse=True)
        doomed: list[Checkpoint] = []
        for index, checkpoint in enumerate(ordered):
            if not self.should_retain(checkpoint, index_from_latest=index, now=now):
                doomed.append(checkpoint)
        return tuple(sorted(doomed, key=lambda item: (item.created_at, item.checkpoint_id)))

    def apply_overrides(self, overrides: Mapping[str, Any]) -> CheckpointPolicy:
        """Return a new policy with JSON-style *overrides* applied."""

        return CheckpointPolicy(
            frequency=float(overrides.get('frequency', self.frequency)),
            retention_count=int(overrides.get('retention_count', self.retention_count)),
            retention_age=float(overrides.get('retention_age', self.retention_age)),
            trigger_points=tuple(overrides.get('trigger_points', self.trigger_points)),
        )

    def copilot_checkpoint_policy(self) -> dict[str, Any]:
        """Return a copilot-oriented plain-data explanation of the policy."""

        return {
            'frequency_seconds': self.frequency,
            'retention_count': self.retention_count,
            'retention_age_seconds': self.retention_age,
            'trigger_points': list(self.trigger_points),
            'summary': (
                'Take checkpoints at semantic boundaries, preserve recent state, '
                'and keep copilot-triggered trust boundaries auditable.'
            ),
        }


class CheckpointDiff:
    """Compute semantic differences between two checkpoint payloads.

    The diff operates on the persisted payload format used by
    :class:`CheckpointStore`.  Only semantic content is compared: manifest hash,
    judgments, obligations, obstructions, certificates, and epoch changes.
    """

    def __init__(
        self,
        before_payload: Mapping[str, Any] | None,
        after_payload: Mapping[str, Any] | None,
        *,
        precomputed: Mapping[str, Any] | None = None,
    ) -> None:
        """Create a diff from two semantic payload mappings."""

        self._before_payload = _deepcopy_mapping(before_payload or {})
        self._after_payload = _deepcopy_mapping(after_payload or {})
        self._precomputed = _deepcopy_mapping(precomputed or {}) if precomputed is not None else None

    @classmethod
    def from_serialized(cls, payload: Mapping[str, Any]) -> CheckpointDiff:
        """Rehydrate a diff from a previously serialized structure."""

        return cls({}, {}, precomputed=payload)

    def _manifest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return the manifest snapshot embedded in a checkpoint payload."""

        if 'manifest' in payload and isinstance(payload['manifest'], Mapping):
            return _deepcopy_mapping(payload['manifest'])
        return _deepcopy_mapping(payload)

    def added_judgments(self) -> tuple[dict[str, Any], ...]:
        """Return newly introduced judgments."""

        report = self.diff()
        return tuple(copy.deepcopy(report['added_judgments']))

    def removed_judgments(self) -> tuple[dict[str, Any], ...]:
        """Return judgments present before but not after."""

        report = self.diff()
        return tuple(copy.deepcopy(report['removed_judgments']))

    def obligation_changes(self) -> dict[str, list[dict[str, Any]]]:
        """Return added, removed, discharged, and reopened obligations."""

        report = self.diff()
        return copy.deepcopy(report['obligation_changes'])

    def obstruction_changes(self) -> dict[str, list[dict[str, Any]]]:
        """Return added, removed, resolved, and reactivated obstructions."""

        report = self.diff()
        return copy.deepcopy(report['obstruction_changes'])

    def epoch_changes(self) -> dict[str, dict[str, int]]:
        """Return coordinate-wise epoch transitions."""

        report = self.diff()
        return copy.deepcopy(report['epoch_changes'])

    def certificate_changes(self) -> dict[str, list[dict[str, Any]]]:
        """Return added and removed certificate entries."""

        report = self.diff()
        return copy.deepcopy(report['certificate_changes'])

    def diff(self) -> dict[str, Any]:
        """Compute and cache the full semantic diff report."""

        if self._precomputed is not None:
            return copy.deepcopy(self._precomputed)

        before_manifest = self._manifest(self._before_payload)
        after_manifest = self._manifest(self._after_payload)

        before_j = {entry['judgment_id']: entry for entry in before_manifest.get('judgments', ())}
        after_j = {entry['judgment_id']: entry for entry in after_manifest.get('judgments', ())}
        added_judgments = [copy.deepcopy(after_j[key]) for key in sorted(after_j) if key not in before_j]
        removed_judgments = [copy.deepcopy(before_j[key]) for key in sorted(before_j) if key not in after_j]

        before_o = {entry['obligation_id']: entry for entry in before_manifest.get('obligations', ())}
        after_o = {entry['obligation_id']: entry for entry in after_manifest.get('obligations', ())}
        obligation_changes = {
            'added': [copy.deepcopy(after_o[key]) for key in sorted(after_o) if key not in before_o],
            'removed': [copy.deepcopy(before_o[key]) for key in sorted(before_o) if key not in after_o],
            'discharged': [
                copy.deepcopy(after_o[key])
                for key in sorted(after_o)
                if key in before_o and after_o[key].get('discharged') and not before_o[key].get('discharged')
            ],
            'reopened': [
                copy.deepcopy(after_o[key])
                for key in sorted(after_o)
                if key in before_o and before_o[key].get('discharged') and not after_o[key].get('discharged')
            ],
        }

        before_x = {entry['obstruction_id']: entry for entry in before_manifest.get('obstructions', ())}
        after_x = {entry['obstruction_id']: entry for entry in after_manifest.get('obstructions', ())}
        obstruction_changes = {
            'added': [copy.deepcopy(after_x[key]) for key in sorted(after_x) if key not in before_x],
            'removed': [copy.deepcopy(before_x[key]) for key in sorted(before_x) if key not in after_x],
            'resolved': [
                copy.deepcopy(after_x[key])
                for key in sorted(after_x)
                if key in before_x and after_x[key].get('resolved') and not before_x[key].get('resolved')
            ],
            'reactivated': [
                copy.deepcopy(after_x[key])
                for key in sorted(after_x)
                if key in before_x and before_x[key].get('resolved') and not after_x[key].get('resolved')
            ],
        }

        before_k = {
            str(entry.get('certificate_id', '')): entry
            for entry in before_manifest.get('certificates', ())
        }
        after_k = {
            str(entry.get('certificate_id', '')): entry
            for entry in after_manifest.get('certificates', ())
        }
        certificate_changes = {
            'added': [copy.deepcopy(after_k[key]) for key in sorted(after_k) if key not in before_k],
            'removed': [copy.deepcopy(before_k[key]) for key in sorted(before_k) if key not in after_k],
        }

        before_epochs = {str(key): int(value) for key, value in before_manifest.get('epoch_map', {}).items()}
        after_epochs = {str(key): int(value) for key, value in after_manifest.get('epoch_map', {}).items()}
        epoch_changes: dict[str, dict[str, int]] = {}
        for coordinate in sorted(set(before_epochs) | set(after_epochs)):
            old_value = before_epochs.get(coordinate, 0)
            new_value = after_epochs.get(coordinate, 0)
            if old_value != new_value:
                epoch_changes[coordinate] = {'before': old_value, 'after': new_value}

        self._precomputed = {
            'before_manifest_hash': _manifest_hash(before_manifest) if before_manifest else '',
            'after_manifest_hash': _manifest_hash(after_manifest) if after_manifest else '',
            'added_judgments': added_judgments,
            'removed_judgments': removed_judgments,
            'obligation_changes': obligation_changes,
            'obstruction_changes': obstruction_changes,
            'epoch_changes': epoch_changes,
            'certificate_changes': certificate_changes,
            'change_count': (
                len(added_judgments)
                + len(removed_judgments)
                + sum(len(items) for items in obligation_changes.values())
                + sum(len(items) for items in obstruction_changes.values())
                + len(epoch_changes)
                + sum(len(items) for items in certificate_changes.values())
            ),
        }
        return copy.deepcopy(self._precomputed)

    def summary(self) -> str:
        """Return a short textual summary of the diff contents."""

        report = self.diff()
        return (
            f"Δ judgments(+{len(report['added_judgments'])}/-{len(report['removed_judgments'])}) "
            f"obligations={sum(len(items) for items in report['obligation_changes'].values())} "
            f"obstructions={sum(len(items) for items in report['obstruction_changes'].values())} "
            f"epochs={len(report['epoch_changes'])}"
        )


class CheckpointSerializer:
    """JSON serialization helpers for checkpoints, policies, and diffs."""

    def checkpoint_to_dict(self, checkpoint: Checkpoint) -> dict[str, Any]:
        """Serialize a checkpoint object to a plain dictionary."""

        return checkpoint.to_dict()

    def checkpoint_from_dict(self, payload: Mapping[str, Any]) -> Checkpoint:
        """Deserialize a checkpoint object from a plain dictionary."""

        return Checkpoint.from_dict(payload)

    def diff_to_dict(self, diff: CheckpointDiff) -> dict[str, Any]:
        """Serialize a checkpoint diff to a plain dictionary."""

        return diff.diff()

    def diff_from_dict(self, payload: Mapping[str, Any]) -> CheckpointDiff:
        """Deserialize a checkpoint diff from a dictionary payload."""

        return CheckpointDiff.from_serialized(payload)

    def policy_to_dict(self, policy: CheckpointPolicy) -> dict[str, Any]:
        """Serialize a checkpoint policy to a JSON-ready dictionary."""

        return {
            'frequency': policy.frequency,
            'retention_count': policy.retention_count,
            'retention_age': policy.retention_age,
            'trigger_points': list(policy.trigger_points),
        }

    def policy_from_dict(self, payload: Mapping[str, Any]) -> CheckpointPolicy:
        """Deserialize a checkpoint policy from a JSON-ready dictionary."""

        return CheckpointPolicy(
            frequency=float(payload.get('frequency', 300.0)),
            retention_count=int(payload.get('retention_count', 10)),
            retention_age=float(payload.get('retention_age', 7 * 24 * 60 * 60)),
            trigger_points=tuple(payload.get('trigger_points', ()) or ()),
        )

    def to_json(self, obj: Checkpoint | CheckpointDiff | CheckpointPolicy) -> str:
        """Serialize a supported checkpoint object to tagged canonical JSON."""

        if isinstance(obj, Checkpoint):
            payload = {'kind': 'checkpoint', 'data': self.checkpoint_to_dict(obj)}
        elif isinstance(obj, CheckpointDiff):
            payload = {'kind': 'checkpoint-diff', 'data': self.diff_to_dict(obj)}
        elif isinstance(obj, CheckpointPolicy):
            payload = {'kind': 'checkpoint-policy', 'data': self.policy_to_dict(obj)}
        else:
            raise TypeError(f'Unsupported checkpoint serialization type: {type(obj)!r}')
        return _canonical_json(payload)

    def from_json(self, payload: str) -> Checkpoint | CheckpointDiff | CheckpointPolicy:
        """Deserialize a supported checkpoint object from tagged JSON."""

        decoded = json.loads(payload)
        kind = decoded.get('kind')
        data = decoded.get('data', {})
        if kind == 'checkpoint':
            return self.checkpoint_from_dict(data)
        if kind == 'checkpoint-diff':
            return self.diff_from_dict(data)
        if kind == 'checkpoint-policy':
            return self.policy_from_dict(data)
        raise ValueError(f'Unsupported checkpoint payload kind: {kind!r}')


class CheckpointStore:
    """Store and index semantic checkpoints, optionally on disk.

    The store keeps both the lightweight :class:`Checkpoint` metadata and the
    heavier semantic payload required for restore or audit.  If *root_path* is
    supplied each checkpoint is also persisted as a JSON file.
    """

    def __init__(
        self,
        root_path: str | Path | None = None,
        *,
        serializer: CheckpointSerializer | None = None,
    ) -> None:
        """Initialize an empty in-memory store with optional file persistence."""

        self._root_path = Path(root_path).expanduser() if root_path is not None else None
        if self._root_path is not None:
            self._root_path.mkdir(parents=True, exist_ok=True)
        self._serializer = serializer or CheckpointSerializer()
        self._checkpoints: dict[str, Checkpoint] = {}
        self._payloads: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._disk_loaded = False

    def _record_path(self, checkpoint_id: str) -> Path:
        """Return the persistence path for a checkpoint identifier."""

        if self._root_path is None:
            raise ValueError('CheckpointStore is not configured with a root_path.')
        return self._root_path / f'{checkpoint_id}.json'

    def _ensure_disk_loaded(self) -> None:
        """Load persisted checkpoints into memory on first use."""

        if self._root_path is None or self._disk_loaded:
            return
        for record_file in sorted(self._root_path.glob('*.json')):
            raw = record_file.read_text(encoding='utf-8')
            decoded = json.loads(raw)
            checkpoint = self._serializer.checkpoint_from_dict(decoded['checkpoint'])
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._payloads[checkpoint.checkpoint_id] = _deepcopy_mapping(decoded['payload'])
        self._disk_loaded = True

    def record_event(self, event_type: str, details: Mapping[str, Any]) -> None:
        """Append an event to the store's internal audit history."""

        self._events.append(
            {
                'event_type': str(event_type),
                'timestamp': _now(),
                'details': _deepcopy_mapping(details),
            },
        )

    def history(self, event_type: str | None = None) -> tuple[dict[str, Any], ...]:
        """Return the store event history, optionally filtered by type."""

        self._ensure_disk_loaded()
        if event_type is None:
            return tuple(copy.deepcopy(self._events))
        return tuple(copy.deepcopy(event) for event in self._events if event['event_type'] == event_type)

    def store(
        self,
        checkpoint: Checkpoint,
        *,
        manifest: Manifest | Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Persist *checkpoint* and its semantic payload."""

        self._ensure_disk_loaded()
        if manifest is None and payload is None:
            raise ValueError('store() requires either a manifest or an explicit payload.')

        manifest_snapshot = _manifest_snapshot(manifest) if manifest is not None else _manifest_snapshot(payload['manifest'])
        semantic_payload = _checkpoint_payload(checkpoint, manifest_snapshot, payload)
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._payloads[checkpoint.checkpoint_id] = semantic_payload

        if self._root_path is not None:
            record = {
                'checkpoint': self._serializer.checkpoint_to_dict(checkpoint),
                'payload': semantic_payload,
            }
            self._record_path(checkpoint.checkpoint_id).write_text(
                _canonical_json(record),
                encoding='utf-8',
            )
        self.record_event(
            'store',
            {
                'checkpoint_id': checkpoint.checkpoint_id,
                'phase': checkpoint.lifecycle_phase,
                'epoch': checkpoint.epoch,
            },
        )
        return checkpoint

    def load(
        self,
        checkpoint_id: str,
        *,
        include_payload: bool = False,
    ) -> Checkpoint | tuple[Checkpoint, dict[str, Any]] | None:
        """Load a checkpoint, optionally with its semantic payload."""

        self._ensure_disk_loaded()
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            return None
        if not include_payload:
            return checkpoint
        return checkpoint, copy.deepcopy(self._payloads[checkpoint_id])

    def list_checkpoints(self) -> tuple[Checkpoint, ...]:
        """Return all checkpoints ordered from oldest to newest."""

        self._ensure_disk_loaded()
        return tuple(sorted(self._checkpoints.values(), key=lambda item: (item.created_at, item.checkpoint_id)))

    def latest(
        self,
        *,
        phase: str | KernelPhase | None = None,
        coordinate: str | None = None,
    ) -> Checkpoint | None:
        """Return the newest checkpoint, optionally constrained by phase/scope."""

        candidates = list(self.list_checkpoints())
        if phase is not None:
            candidates = [item for item in candidates if item.matches_phase(phase)]
        if coordinate is not None:
            candidates = [item for item in candidates if item.covers_coordinate(coordinate)]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.created_at, item.epoch, item.checkpoint_id))

    def by_epoch(self, epoch: int) -> tuple[Checkpoint, ...]:
        """Return all checkpoints taken at the specified manifest epoch."""

        return tuple(item for item in self.list_checkpoints() if item.epoch == int(epoch))

    def by_phase(self, phase: str | KernelPhase) -> tuple[Checkpoint, ...]:
        """Return all checkpoints recorded for the given lifecycle phase."""

        target = _phase_value(phase)
        return tuple(item for item in self.list_checkpoints() if item.lifecycle_phase == target)

    def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint and its payload from memory and disk."""

        self._ensure_disk_loaded()
        if checkpoint_id not in self._checkpoints:
            return False
        del self._checkpoints[checkpoint_id]
        del self._payloads[checkpoint_id]
        if self._root_path is not None:
            record_path = self._record_path(checkpoint_id)
            if record_path.exists():
                record_path.unlink()
        self.record_event('delete', {'checkpoint_id': checkpoint_id})
        return True

    def compact(self, policy: CheckpointPolicy | None = None) -> tuple[str, ...]:
        """Prune redundant or expired checkpoints according to *policy*."""

        self._ensure_disk_loaded()
        policy = policy or CheckpointPolicy()
        ordered = list(self.list_checkpoints())
        doomed_ids: set[str] = {item.checkpoint_id for item in policy.prune_candidates(ordered)}

        latest_by_hash: dict[str, str] = {}
        for checkpoint in ordered:
            latest_by_hash[checkpoint.manifest_hash] = checkpoint.checkpoint_id
        for checkpoint in ordered:
            if latest_by_hash.get(checkpoint.manifest_hash) != checkpoint.checkpoint_id:
                if not checkpoint.metadata.get('pinned', False):
                    doomed_ids.add(checkpoint.checkpoint_id)

        removed: list[str] = []
        for checkpoint_id in sorted(doomed_ids):
            if self.delete(checkpoint_id):
                removed.append(checkpoint_id)
        self.record_event('compact', {'removed_checkpoint_ids': removed, 'count': len(removed)})
        return tuple(removed)

    def create(
        self,
        name: str,
        *,
        cache: SemanticCache | None = None,
        memory: SemanticMemory | None = None,
        replay: Any | None = None,
        manifest: Manifest | Mapping[str, Any] | None = None,
        lifecycle_phase: str | KernelPhase = KernelPhase.RUNNING,
    ) -> Checkpoint:
        """Legacy compatibility helper that creates and stores a checkpoint."""

        manifest_snapshot = _manifest_snapshot(manifest or Manifest())
        runtime_state = {
            'cache_snapshot': {
                key: {
                    'key': entry.key,
                    'value': repr(entry.value),
                    'support': repr(entry.support),
                    'trust': repr(entry.trust),
                    'provenance': entry.provenance.to_dict(),
                }
                for key, entry in (cache.snapshot().items() if cache is not None else [])
            },
            'memory_snapshot': {
                key: {
                    'key': note.key,
                    'value': repr(note.value),
                    'tags': list(note.tags),
                    'provenance': note.provenance.to_dict() if note.provenance is not None else None,
                }
                for key, note in ((memory.notes or {}).items() if memory is not None else [])
            },
            'replay_size': (
                len(getattr(replay, 'records'))
                if replay is not None and hasattr(replay, 'records')
                else len(replay)
                if replay is not None and hasattr(replay, '__len__')
                else 0
            ),
        }
        checkpoint = Checkpoint(
            checkpoint_id=name,
            epoch=_epoch_from_manifest(manifest_snapshot),
            created_at=_now(),
            coordinate_scope=_coordinate_scope_from_manifest(manifest_snapshot),
            manifest_hash=_manifest_hash(manifest_snapshot),
            lifecycle_phase=_phase_value(lifecycle_phase),
            summary=f'legacy checkpoint {name}',
            metadata={'legacy_api': True, 'event': 'legacy-create'},
        )
        self.store(checkpoint, manifest=manifest_snapshot, payload={'runtime_state': runtime_state})
        return checkpoint

    def restore(self, name: str) -> Checkpoint | None:
        """Legacy compatibility alias for :meth:`load`."""

        loaded = self.load(name)
        return loaded if isinstance(loaded, Checkpoint) else None

    # -- cross-subsystem integration -----------------------------------------

    def judgment_checkpoint(
        self,
        sections: Any,
        *,
        lifecycle_phase: str | KernelPhase = KernelPhase.RUNNING,
        summary: str = "judgment sections checkpoint",
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Create a checkpoint that includes judgment sections.

        Uses ``jugeo.judgments.sections.Section`` objects to build a
        manifest-like snapshot capturing the current state of judgment
        sections, then stores the checkpoint with that data.

        Parameters
        ----------
        sections:
            An iterable of ``Section`` objects from
            ``jugeo.judgments.sections``, or a dict mapping coordinate
            keys to section data.
        lifecycle_phase:
            Kernel lifecycle phase for the checkpoint.
        summary:
            Human-readable summary for auditing.
        metadata:
            Additional metadata to attach.

        Returns
        -------
        Checkpoint
        """
        try:
            from jugeo.judgments.sections import Section
        except ImportError:  # pragma: no cover
            Section = None  # type: ignore[assignment,misc]

        section_data: list[dict[str, Any]] = []
        if isinstance(sections, Mapping):
            for key, sec in sections.items():
                entry: dict[str, Any] = {"coordinate": str(key)}
                if Section is not None and isinstance(sec, Section):
                    entry["section_id"] = getattr(sec, "section_id", str(key))
                    entry["coordinate"] = str(getattr(sec, "coordinate", key))
                else:
                    entry["raw"] = repr(sec)
                section_data.append(entry)
        else:
            for sec in sections:
                entry = {}
                if Section is not None and isinstance(sec, Section):
                    entry["section_id"] = getattr(sec, "section_id", "")
                    entry["coordinate"] = str(getattr(sec, "coordinate", ""))
                else:
                    entry["raw"] = repr(sec)
                section_data.append(entry)

        manifest_data: dict[str, Any] = {
            "judgments": section_data,
            "obligations": [],
            "evidence_archive": [],
            "obstructions": [],
            "certificates": [],
            "epoch_map": {},
            "invalidation_graph": {},
        }
        checkpoint = Checkpoint(
            checkpoint_id=_uid("ckpt-jsec"),
            epoch=0,
            created_at=_now(),
            coordinate_scope=tuple(
                str(e.get("coordinate", "*")) for e in section_data
            ) or ("*",),
            manifest_hash=_manifest_hash(manifest_data),
            lifecycle_phase=_phase_value(lifecycle_phase),
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self.store(checkpoint, payload={"manifest": manifest_data})
        return checkpoint

    def evidence_snapshot(
        self,
        manifest: Any,
        *,
        lifecycle_phase: str | KernelPhase = KernelPhase.RUNNING,
        summary: str = "evidence manifest snapshot",
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Create a checkpoint from an evidence manifest.

        Uses ``jugeo.evidence.manifests.Manifest`` (or
        ``EvidenceManifest``) to capture the evidence archive state into
        a checkpoint for later audit or restore.

        Parameters
        ----------
        manifest:
            A ``Manifest`` or ``EvidenceManifest`` from
            ``jugeo.evidence.manifests``, or a plain mapping.
        lifecycle_phase:
            Kernel lifecycle phase.
        summary:
            Human-readable summary.
        metadata:
            Additional metadata.

        Returns
        -------
        Checkpoint
        """
        try:
            from jugeo.evidence.manifests import Manifest as M, EvidenceManifest
        except ImportError:  # pragma: no cover
            M = None  # type: ignore[assignment,misc]
            EvidenceManifest = None  # type: ignore[assignment,misc]

        if M is not None and isinstance(manifest, M):
            snap = _manifest_snapshot(manifest)
        elif isinstance(manifest, Mapping):
            snap = _deepcopy_mapping(manifest)
        else:
            snap = {"evidence_archive": [repr(manifest)]}

        checkpoint = Checkpoint(
            checkpoint_id=_uid("ckpt-evid"),
            epoch=_epoch_from_manifest(snap),
            created_at=_now(),
            coordinate_scope=_coordinate_scope_from_manifest(snap),
            manifest_hash=_manifest_hash(snap),
            lifecycle_phase=_phase_value(lifecycle_phase),
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self.store(checkpoint, payload={"manifest": snap})
        return checkpoint

    def site_topology_snapshot(
        self,
        site: Any,
        *,
        lifecycle_phase: str | KernelPhase = KernelPhase.RUNNING,
        summary: str = "site topology snapshot",
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Create a checkpoint capturing site topology.

        Uses ``jugeo.geometry.site.Site`` to snapshot the coordinate
        graph, covering families, and topology so that later replay or
        audit can detect structural drift.

        Parameters
        ----------
        site:
            A ``Site`` from ``jugeo.geometry.site``.
        lifecycle_phase:
            Kernel lifecycle phase.
        summary:
            Human-readable summary.
        metadata:
            Additional metadata.

        Returns
        -------
        Checkpoint
        """
        try:
            from jugeo.geometry.site import Site
        except ImportError:  # pragma: no cover
            Site = None  # type: ignore[assignment,misc]

        topology_data: dict[str, Any] = {}
        coord_scope: tuple[str, ...] = ("*",)
        if Site is not None and isinstance(site, Site):
            coords = site.coordinates()
            coord_keys = []
            for c in coords:
                key = ".".join(c.components) if hasattr(c, "components") else str(c)
                coord_keys.append(key)
            topology_data["coordinates"] = coord_keys
            topology_data["morphism_count"] = len(site.morphisms()) if hasattr(site, "morphisms") else 0
            coord_scope = tuple(coord_keys) or ("*",)
        else:
            topology_data["raw"] = repr(site)

        manifest_data: dict[str, Any] = {
            "judgments": [],
            "obligations": [],
            "evidence_archive": [],
            "obstructions": [],
            "certificates": [],
            "epoch_map": {},
            "invalidation_graph": {},
            "topology": topology_data,
        }
        checkpoint = Checkpoint(
            checkpoint_id=_uid("ckpt-topo"),
            epoch=0,
            created_at=_now(),
            coordinate_scope=coord_scope,
            manifest_hash=_manifest_hash(manifest_data),
            lifecycle_phase=_phase_value(lifecycle_phase),
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self.store(checkpoint, payload={"manifest": manifest_data})
        return checkpoint

    def certificate_checkpoint(
        self,
        certificates: Any,
        *,
        lifecycle_phase: str | KernelPhase = KernelPhase.RUNNING,
        summary: str = "certificate checkpoint",
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Create a checkpoint preserving certificate state.

        Uses ``jugeo.evidence.certificates.Certificate`` objects to
        record the current certificate chain and verification state.

        Parameters
        ----------
        certificates:
            An iterable of ``Certificate`` objects from
            ``jugeo.evidence.certificates``, or a list of dicts.
        lifecycle_phase:
            Kernel lifecycle phase.
        summary:
            Human-readable summary.
        metadata:
            Additional metadata.

        Returns
        -------
        Checkpoint
        """
        try:
            from jugeo.evidence.certificates import Certificate
        except ImportError:  # pragma: no cover
            Certificate = None  # type: ignore[assignment,misc]

        cert_data: list[dict[str, Any]] = []
        for cert in certificates:
            if Certificate is not None and isinstance(cert, Certificate):
                entry: dict[str, Any] = {
                    "certificate_id": getattr(cert, "certificate_id", ""),
                    "status": str(getattr(cert, "status", "unknown")),
                }
                if hasattr(cert, "to_dict"):
                    entry = cert.to_dict()
                cert_data.append(entry)
            elif isinstance(cert, Mapping):
                cert_data.append(copy.deepcopy(dict(cert)))
            else:
                cert_data.append({"raw": repr(cert)})

        manifest_data: dict[str, Any] = {
            "judgments": [],
            "obligations": [],
            "evidence_archive": [],
            "obstructions": [],
            "certificates": cert_data,
            "epoch_map": {},
            "invalidation_graph": {},
        }
        checkpoint = Checkpoint(
            checkpoint_id=_uid("ckpt-cert"),
            epoch=0,
            created_at=_now(),
            coordinate_scope=("*",),
            manifest_hash=_manifest_hash(manifest_data),
            lifecycle_phase=_phase_value(lifecycle_phase),
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self.store(checkpoint, payload={"manifest": manifest_data})
        return checkpoint


class CheckpointBuilder:
    """Construct checkpoints from manifests, scopes, and provenance records."""

    def __init__(self) -> None:
        """Initialize an empty builder."""

        self._manifest_snapshot: dict[str, Any] | None = None
        self._checkpoint_id: str = ''
        self._created_at: float = 0.0
        self._epoch: int = 0
        self._lifecycle_phase: str = KernelPhase.RUNNING.value
        self._summary: str = ''
        self._coordinate_scope: set[str] = set()
        self._metadata: dict[str, Any] = {}
        self._provenance: list[dict[str, Any]] = []
        self._payload: dict[str, Any] = {}

    def build_from_manifest(
        self,
        manifest: Manifest | Mapping[str, Any],
        *,
        lifecycle_phase: str | KernelPhase,
        summary: str,
        metadata: Mapping[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> CheckpointBuilder:
        """Seed the builder from a full manifest snapshot."""

        manifest_snapshot = _manifest_snapshot(manifest)
        self._manifest_snapshot = manifest_snapshot
        self._checkpoint_id = checkpoint_id or _uid('ckpt')
        self._created_at = _now()
        self._epoch = _epoch_from_manifest(manifest_snapshot)
        self._lifecycle_phase = _phase_value(lifecycle_phase)
        self._summary = summary
        self._coordinate_scope = set(_coordinate_scope_from_manifest(manifest_snapshot))
        self._metadata = copy.deepcopy(dict(metadata or {}))
        self._payload = {}
        return self

    def build_incremental(
        self,
        previous: Checkpoint | Mapping[str, Any] | None,
        current_manifest: Manifest | Mapping[str, Any],
        *,
        replay_boundary: str | Mapping[str, Any],
        changed_coordinates: Iterable[str] = (),
    ) -> CheckpointBuilder:
        """Build an incremental checkpoint rooted at a prior checkpoint."""

        previous_id = previous.checkpoint_id if isinstance(previous, Checkpoint) else str((previous or {}).get('checkpoint_id', ''))
        manifest_snapshot = _manifest_snapshot(current_manifest)
        self.build_from_manifest(
            manifest_snapshot,
            lifecycle_phase=self._lifecycle_phase,
            summary='incremental semantic checkpoint',
            metadata={
                'event': 'replay-boundary',
                'base_checkpoint_id': previous_id or None,
            },
        )
        self._metadata['incremental'] = True
        self._metadata['base_checkpoint_id'] = previous_id or None
        self._metadata['changed_coordinates'] = sorted({str(item) for item in changed_coordinates if str(item)})
        if isinstance(replay_boundary, Mapping):
            self._payload['replay_boundary'] = _deepcopy_mapping(replay_boundary)
        else:
            self._payload['replay_boundary'] = {'kind': str(replay_boundary), 'base_checkpoint_id': previous_id or None}
        if self._metadata['changed_coordinates']:
            self._coordinate_scope.update(self._metadata['changed_coordinates'])
        return self

    def include_scope(self, coordinates: str | Iterable[str], *more_coordinates: str) -> CheckpointBuilder:
        """Expand the checkpoint coordinate scope."""

        if isinstance(coordinates, str):
            items = [coordinates, *more_coordinates]
        else:
            items = list(coordinates) + list(more_coordinates)
        self._coordinate_scope.update(str(item) for item in items if str(item))
        return self

    def include_provenance(
        self,
        actor: str,
        action: str,
        *,
        coordinate: str = '',
        details: Mapping[str, Any] | None = None,
    ) -> CheckpointBuilder:
        """Append an audit provenance step to the checkpoint payload."""

        self._provenance.append(
            {
                'timestamp': _now(),
                'actor': str(actor),
                'action': str(action),
                'coordinate': str(coordinate),
                'details': _deepcopy_mapping(details or {}),
            },
        )
        return self

    def finalize(self, store: CheckpointStore | None = None) -> Checkpoint:
        """Finalize the checkpoint and optionally persist it to *store*."""

        if self._manifest_snapshot is None:
            raise ValueError('CheckpointBuilder.finalize() requires a manifest to be configured first.')
        manifest_snapshot = copy.deepcopy(self._manifest_snapshot)
        checkpoint = Checkpoint(
            checkpoint_id=self._checkpoint_id or _uid('ckpt'),
            epoch=self._epoch,
            created_at=self._created_at or _now(),
            coordinate_scope=tuple(sorted(self._coordinate_scope)) or ('*',),
            manifest_hash=_manifest_hash(manifest_snapshot),
            lifecycle_phase=self._lifecycle_phase,
            summary=self._summary,
            metadata={
                **copy.deepcopy(self._metadata),
                'provenance_count': len(self._provenance),
            },
        )
        payload = _checkpoint_payload(
            checkpoint,
            manifest_snapshot,
            {
                **copy.deepcopy(self._payload),
                'provenance': copy.deepcopy(self._provenance),
                'metadata': copy.deepcopy(self._metadata),
            },
        )
        if store is not None:
            store.store(checkpoint, manifest=manifest_snapshot, payload=payload)
        return checkpoint


class CheckpointIntegrity:
    """Integrity verification for checkpoint metadata and semantic payloads."""

    def __init__(self, store: CheckpointStore) -> None:
        """Bind the integrity checker to a checkpoint store."""

        self._store = store

    def _resolve(self, target: str | Checkpoint) -> tuple[Checkpoint, dict[str, Any]]:
        """Resolve a checkpoint identifier or object to metadata and payload."""

        checkpoint_id = target.checkpoint_id if isinstance(target, Checkpoint) else str(target)
        loaded = self._store.load(checkpoint_id, include_payload=True)
        if loaded is None:
            raise KeyError(f'Unknown checkpoint: {checkpoint_id}')
        checkpoint, payload = loaded
        return checkpoint, payload

    def verify_hash(self, target: str | Checkpoint) -> bool:
        """Return whether the manifest hash matches the stored payload."""

        checkpoint, payload = self._resolve(target)
        manifest_snapshot = _manifest_snapshot(payload['manifest'])
        ok = checkpoint.manifest_hash == _manifest_hash(manifest_snapshot)
        if not ok:
            self._store.record_event('integrity-failure', {'checkpoint_id': checkpoint.checkpoint_id, 'kind': 'hash-mismatch'})
        return ok

    def verify_manifest_consistency(self, target: str | Checkpoint) -> bool:
        """Return whether mirrored manifest components remain internally consistent."""

        checkpoint, payload = self._resolve(target)
        manifest_snapshot = _manifest_snapshot(payload['manifest'])
        mirrors_match = (
            payload.get('judgments', []) == manifest_snapshot.get('judgments', [])
            and payload.get('obligations', []) == manifest_snapshot.get('obligations', [])
            and payload.get('evidence_archive', []) == manifest_snapshot.get('evidence_archive', [])
            and payload.get('obstructions', []) == manifest_snapshot.get('obstructions', [])
            and payload.get('certificates', []) == manifest_snapshot.get('certificates', [])
            and payload.get('epoch_map', {}) == manifest_snapshot.get('epoch_map', {})
            and payload.get('invalidation_graph', {}) == manifest_snapshot.get('invalidation_graph', {})
        )
        epochs_valid = all(int(value) >= 0 for value in manifest_snapshot.get('epoch_map', {}).values())
        ok = mirrors_match and epochs_valid
        if not ok:
            self._store.record_event(
                'integrity-failure',
                {'checkpoint_id': checkpoint.checkpoint_id, 'kind': 'manifest-inconsistency'},
            )
        return ok

    def verify_restoreability(self, target: str | Checkpoint) -> bool:
        """Return whether the checkpoint can be round-tripped into a Manifest."""

        checkpoint, payload = self._resolve(target)
        try:
            manifest_snapshot = _manifest_snapshot(payload['manifest'])
            manifest = Manifest()
            manifest.restore(manifest_snapshot)
            restored = manifest.snapshot()
            ok = (
                len(restored.get('judgments', [])) == len(manifest_snapshot.get('judgments', []))
                and len(restored.get('obligations', [])) == len(manifest_snapshot.get('obligations', []))
                and len(restored.get('evidence_archive', [])) == len(manifest_snapshot.get('evidence_archive', []))
                and len(restored.get('obstructions', [])) == len(manifest_snapshot.get('obstructions', []))
                and len(restored.get('certificates', [])) == len(manifest_snapshot.get('certificates', []))
                and restored.get('epoch_map', {}) == manifest_snapshot.get('epoch_map', {})
                and restored.get('invalidation_graph', {}) == manifest_snapshot.get('invalidation_graph', {})
                and bool(ManifestSerializer.to_json(manifest))
            )
        except Exception:
            ok = False
        if not ok:
            self._store.record_event(
                'integrity-failure',
                {'checkpoint_id': checkpoint.checkpoint_id, 'kind': 'restoreability'},
            )
        return ok

    def audit_stamp(self, target: str | Checkpoint) -> dict[str, Any]:
        """Return a combined integrity verdict for a checkpoint."""

        checkpoint, _ = self._resolve(target)
        return {
            'checkpoint_id': checkpoint.checkpoint_id,
            'hash_ok': self.verify_hash(checkpoint),
            'manifest_consistent': self.verify_manifest_consistency(checkpoint),
            'restorable': self.verify_restoreability(checkpoint),
        }

    def corruption_report(self, target: str | Checkpoint | None = None) -> dict[str, Any]:
        """Return a structured report describing discovered corruption issues."""

        checkpoint_ids: Iterable[str]
        if target is None:
            checkpoint_ids = [item.checkpoint_id for item in self._store.list_checkpoints()]
        else:
            checkpoint_ids = [target.checkpoint_id if isinstance(target, Checkpoint) else str(target)]

        report: dict[str, Any] = {'checked': [], 'corrupted': [], 'issues': {}}
        for checkpoint_id in checkpoint_ids:
            stamp = self.audit_stamp(checkpoint_id)
            report['checked'].append(checkpoint_id)
            issues: list[str] = []
            if not stamp['hash_ok']:
                issues.append('hash-mismatch')
            if not stamp['manifest_consistent']:
                issues.append('manifest-inconsistency')
            if not stamp['restorable']:
                issues.append('restoreability-failure')
            if issues:
                report['corrupted'].append(checkpoint_id)
                report['issues'][checkpoint_id] = issues
        return report


class CheckpointRestorer:
    """Restore manifest state from stored checkpoints with rollback support."""

    def __init__(
        self,
        store: CheckpointStore,
        *,
        integrity: CheckpointIntegrity | None = None,
    ) -> None:
        """Bind the restorer to a store and optional integrity checker."""

        self._store = store
        self._integrity = integrity or CheckpointIntegrity(store)
        self._rollback_stack: list[dict[str, Any]] = []

    def restore(
        self,
        checkpoint_id: str,
        *,
        manifest: Manifest | None = None,
    ) -> dict[str, Any]:
        """Restore the semantic payload for *checkpoint_id*."""

        if not self.validate_before_restore(checkpoint_id):
            self._store.record_event('restore-failure', {'checkpoint_id': checkpoint_id, 'reason': 'validation'})
            raise ValueError(f'Checkpoint {checkpoint_id} failed integrity validation.')
        loaded = self._store.load(checkpoint_id, include_payload=True)
        if loaded is None:
            self._store.record_event('restore-failure', {'checkpoint_id': checkpoint_id, 'reason': 'not-found'})
            raise KeyError(f'Unknown checkpoint: {checkpoint_id}')
        checkpoint, payload = loaded
        if manifest is not None:
            self._rollback_stack.append({'manifest_snapshot': manifest.snapshot(), 'checkpoint_id': checkpoint_id})
            manifest.restore(payload['manifest'])
        self._store.record_event('restore', {'checkpoint_id': checkpoint.checkpoint_id, 'phase': checkpoint.lifecycle_phase})
        return copy.deepcopy(payload)

    def restore_incremental(
        self,
        checkpoint_id: str,
        target_checkpoint_id: str,
        *,
        manifest: Manifest | None = None,
    ) -> dict[str, Any]:
        """Restore *target_checkpoint_id* and return the diff from *checkpoint_id*."""

        base_loaded = self._store.load(checkpoint_id, include_payload=True)
        target_loaded = self._store.load(target_checkpoint_id, include_payload=True)
        if base_loaded is None or target_loaded is None:
            raise KeyError('restore_incremental() requires both checkpoints to exist.')
        _, base_payload = base_loaded
        target_checkpoint, target_payload = target_loaded
        diff = CheckpointDiff(base_payload, target_payload)
        restored_state = self.restore(target_checkpoint.checkpoint_id, manifest=manifest)
        self._store.record_event(
            'restore-incremental',
            {
                'checkpoint_id': checkpoint_id,
                'target_checkpoint_id': target_checkpoint_id,
                'change_count': diff.diff()['change_count'],
            },
        )
        return {
            'base_checkpoint_id': checkpoint_id,
            'target_checkpoint_id': target_checkpoint_id,
            'diff': diff.diff(),
            'state': restored_state,
        }

    def validate_before_restore(self, checkpoint_id: str) -> bool:
        """Return whether a checkpoint passes all pre-restore checks."""

        stamp = self._integrity.audit_stamp(checkpoint_id)
        return bool(stamp['hash_ok'] and stamp['manifest_consistent'] and stamp['restorable'])

    def rollback_restore(self, *, manifest: Manifest | None = None) -> bool:
        """Rollback the most recent manifest restore operation if possible."""

        if not self._rollback_stack:
            return False
        prior = self._rollback_stack.pop()
        if manifest is not None:
            manifest.restore(prior['manifest_snapshot'])
        self._store.record_event('rollback-restore', {'checkpoint_id': prior['checkpoint_id']})
        return True

    def compare_pre_post(
        self,
        before: str | Checkpoint | Mapping[str, Any],
        after: str | Checkpoint | Mapping[str, Any],
    ) -> CheckpointDiff:
        """Return a semantic diff between two checkpoints or raw payloads."""

        def resolve(value: str | Checkpoint | Mapping[str, Any]) -> dict[str, Any]:
            if isinstance(value, Mapping):
                return _deepcopy_mapping(value)
            loaded = self._store.load(value.checkpoint_id if isinstance(value, Checkpoint) else str(value), include_payload=True)
            if loaded is None:
                raise KeyError(f'Unknown checkpoint for diff: {value!r}')
            return loaded[1]

        return CheckpointDiff(resolve(before), resolve(after))


class CheckpointHistory:
    """Views over checkpoint storage, retention, and restore event history."""

    def __init__(self, store: CheckpointStore) -> None:
        """Bind history queries to a checkpoint store."""

        self._store = store

    def timeline(self, limit: int | None = None) -> tuple[dict[str, Any], ...]:
        """Return a checkpoint-centric timeline ordered by creation time."""

        items = [
            {
                'checkpoint_id': checkpoint.checkpoint_id,
                'phase': checkpoint.lifecycle_phase,
                'epoch': checkpoint.epoch,
                'summary': checkpoint.summary,
                'created_at': checkpoint.created_at,
            }
            for checkpoint in self._store.list_checkpoints()
        ]
        if limit is not None:
            items = items[-max(0, limit):]
        return tuple(items)

    def restore_history(self, limit: int | None = None) -> tuple[dict[str, Any], ...]:
        """Return restore and rollback events from the audit history."""

        events = [
            event for event in self._store.history()
            if event['event_type'] in {'restore', 'restore-incremental', 'rollback-restore'}
        ]
        if limit is not None:
            events = events[-max(0, limit):]
        return tuple(events)

    def retention_history(self, limit: int | None = None) -> tuple[dict[str, Any], ...]:
        """Return compaction and deletion events from the audit history."""

        events = [
            event for event in self._store.history()
            if event['event_type'] in {'compact', 'delete'}
        ]
        if limit is not None:
            events = events[-max(0, limit):]
        return tuple(events)

    def failure_history(self, limit: int | None = None) -> tuple[dict[str, Any], ...]:
        """Return integrity and restore failure events from the audit history."""

        events = [
            event for event in self._store.history()
            if event['event_type'] in {'integrity-failure', 'restore-failure'}
        ]
        if limit is not None:
            events = events[-max(0, limit):]
        return tuple(events)

    def events_for_checkpoint(self, checkpoint_id: str) -> tuple[dict[str, Any], ...]:
        """Return all history events associated with *checkpoint_id*."""

        matched = []
        for event in self._store.history():
            details = event.get('details', {})
            if details.get('checkpoint_id') == checkpoint_id or details.get('target_checkpoint_id') == checkpoint_id:
                matched.append(event)
        return tuple(matched)


class CheckpointScheduler:
    """Schedule checkpoint creation around policy and pressure constraints."""

    def __init__(self, policy: CheckpointPolicy, *, clock: callable | None = None) -> None:
        """Create a scheduler using *policy* and an optional clock function."""

        self._policy = policy
        self._clock = clock or _now

    def schedule_periodic(self, last_checkpoint_at: float | None, now: float | None = None) -> float:
        """Return the next periodic checkpoint timestamp."""

        current = now if now is not None else float(self._clock())
        if last_checkpoint_at is None:
            return current
        return float(last_checkpoint_at) + self._policy.frequency

    def schedule_on_event(
        self,
        event: str,
        *,
        phase: str | KernelPhase | None = None,
        now: float | None = None,
        last_checkpoint_at: float | None = None,
        pressure: float | None = None,
    ) -> bool:
        """Return whether a semantic event should trigger a checkpoint."""

        return self._policy.should_checkpoint(
            event,
            phase=phase,
            last_checkpoint_at=last_checkpoint_at,
            now=now if now is not None else float(self._clock()),
            pressure=pressure,
        )

    def coalesce_nearby(
        self,
        proposals: Iterable[Mapping[str, Any]],
        *,
        window: float | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Merge checkpoint proposals that occur near each other in time."""

        threshold = self._policy.frequency / 4 if window is None else max(0.0, float(window))
        ordered = sorted((dict(item) for item in proposals), key=lambda item: float(item.get('timestamp', 0.0)))
        if not ordered:
            return ()

        merged: list[dict[str, Any]] = [ordered[0]]
        for proposal in ordered[1:]:
            current = merged[-1]
            if (
                float(proposal.get('timestamp', 0.0)) - float(current.get('timestamp', 0.0)) <= threshold
                and proposal.get('phase') == current.get('phase')
            ):
                current_scope = set(current.get('coordinate_scope', []))
                current_scope.update(proposal.get('coordinate_scope', []))
                current['coordinate_scope'] = sorted(current_scope)
                current['events'] = sorted(set(current.get('events', [])) | set(proposal.get('events', [])))
                current['timestamp'] = max(float(current.get('timestamp', 0.0)), float(proposal.get('timestamp', 0.0)))
            else:
                merged.append(proposal)
        return tuple(merged)

    def defer_under_pressure(
        self,
        proposals: Iterable[Mapping[str, Any]],
        pressure: float,
        *,
        max_delay: float | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Delay non-critical proposals when the system is under pressure."""

        delay = min(self._policy.frequency, max_delay if max_delay is not None else self._policy.frequency / 2)
        adjusted: list[dict[str, Any]] = []
        for proposal in proposals:
            item = dict(proposal)
            event_names = set(item.get('events', []))
            urgent = bool(event_names & set(self._policy.trigger_points))
            if pressure >= 0.8 and not urgent:
                item['deferred_until'] = float(item.get('timestamp', self._clock())) + delay
                item['defer_reason'] = 'pressure'
            adjusted.append(item)
        return tuple(adjusted)

    def should_fire(
        self,
        event: str | None = None,
        *,
        phase: str | KernelPhase | None = None,
        last_checkpoint_at: float | None = None,
        pressure: float | None = None,
    ) -> bool:
        """Return whether a checkpoint should fire immediately."""

        current = float(self._clock())
        due_at = self.schedule_periodic(last_checkpoint_at, current)
        periodic_due = current >= due_at
        return periodic_due or self.schedule_on_event(
            event or '',
            phase=phase,
            now=current,
            last_checkpoint_at=last_checkpoint_at,
            pressure=pressure,
        )


class CheckpointDiagnostics:
    """Diagnostic views for checkpoint storage and integrity health."""

    def __init__(
        self,
        store: CheckpointStore,
        *,
        integrity: CheckpointIntegrity | None = None,
        history: CheckpointHistory | None = None,
    ) -> None:
        """Bind diagnostics to a store and optional helper objects."""

        self._store = store
        self._integrity = integrity or CheckpointIntegrity(store)
        self._history = history or CheckpointHistory(store)

    def summary(self) -> dict[str, Any]:
        """Return high-level counts and phase distribution."""

        checkpoints = self._store.list_checkpoints()
        phase_counts: dict[str, int] = {}
        for checkpoint in checkpoints:
            phase_counts[checkpoint.lifecycle_phase] = phase_counts.get(checkpoint.lifecycle_phase, 0) + 1
        return {
            'checkpoint_count': len(checkpoints),
            'latest_checkpoint_id': checkpoints[-1].checkpoint_id if checkpoints else None,
            'phase_counts': phase_counts,
            'failure_events': len(self._history.failure_history()),
        }

    def storage_report(self) -> dict[str, Any]:
        """Return file-backed storage usage and payload sizing information."""

        checkpoints = self._store.list_checkpoints()
        total_bytes = 0
        file_count = 0
        if self._store._root_path is not None:
            for record_file in self._store._root_path.glob('*.json'):
                file_count += 1
                total_bytes += record_file.stat().st_size
        in_memory_payloads = {
            checkpoint.checkpoint_id: len(_canonical_json(self._store.load(checkpoint.checkpoint_id, include_payload=True)[1]))
            for checkpoint in checkpoints
        }
        return {
            'file_count': file_count,
            'disk_bytes': total_bytes,
            'in_memory_payload_bytes': in_memory_payloads,
        }

    def integrity_report(self) -> dict[str, Any]:
        """Return corruption and restoreability diagnostics."""

        return self._integrity.corruption_report()

    def checkpoint_matrix(self) -> tuple[dict[str, Any], ...]:
        """Return a row-wise matrix suitable for tabular diagnostics."""

        rows = []
        for checkpoint in self._store.list_checkpoints():
            rows.append(
                {
                    'checkpoint_id': checkpoint.checkpoint_id,
                    'phase': checkpoint.lifecycle_phase,
                    'epoch': checkpoint.epoch,
                    'age_seconds': round(checkpoint.age_seconds(), 3),
                    'scope_size': len(checkpoint.coordinate_scope),
                    'hash_prefix': checkpoint.manifest_hash[:12],
                },
            )
        return tuple(rows)

    def copilot_checkpoint_summary(self) -> dict[str, Any]:
        """Return a copilot-friendly natural-language checkpoint summary."""

        summary = self.summary()
        integrity = self.integrity_report()
        matrix = self.checkpoint_matrix()
        latest = matrix[-1] if matrix else None
        return {
            'summary': summary,
            'integrity': integrity,
            'latest': latest,
            'narrative': (
                'Checkpoint archive is tracking semantic boundaries for lifecycle, '
                'integration, treaty, and replay events. '
                f"{len(integrity.get('corrupted', []))} corruption findings are currently recorded."
            ),
        }


__all__ = [
    'Checkpoint',
    'CheckpointStore',
    'CheckpointBuilder',
    'CheckpointRestorer',
    'CheckpointPolicy',
    'CheckpointDiff',
    'CheckpointScheduler',
    'CheckpointIntegrity',
    'CheckpointHistory',
    'CheckpointDiagnostics',
    'CheckpointSerializer',
]
