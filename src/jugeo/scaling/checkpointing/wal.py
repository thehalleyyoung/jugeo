from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import CheckpointConfig, WALEntry, WALEntryKind, WALSegment

_SEGMENT_MAX_ENTRIES = 1000


class WriteAheadLog:
    """Write-ahead log with optional disk persistence.

    Entries are stored in segments.  When a segment reaches
    ``_SEGMENT_MAX_ENTRIES`` entries it is rotated and (optionally)
    flushed to disk.
    """

    def __init__(self, config: CheckpointConfig) -> None:
        self._config = config
        self._segments: List[WALSegment] = []
        self._current_segment: WALSegment = self._new_segment(start_seq=0)
        self._sequence: int = 0

        # Ensure wal_dir exists
        if config.wal_dir:
            os.makedirs(config.wal_dir, exist_ok=True)

        # Load existing segments from disk
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, kind: WALEntryKind, entity_id: str, data: dict) -> int:
        seq = self._sequence
        checksum = self._compute_checksum(data)
        entry = WALEntry(
            sequence_number=seq,
            kind=kind,
            entity_id=entity_id,
            data=data,
            timestamp=time.time(),
            checksum=checksum,
        )
        self._write_entry(entry)
        self._sequence += 1

        if len(self._current_segment.entries) >= _SEGMENT_MAX_ENTRIES:
            self._rotate_segment()

        return seq

    def append_batch(self, entries: List[Tuple[WALEntryKind, str, dict]]) -> List[int]:
        seqs: List[int] = []
        for kind, entity_id, data in entries:
            seqs.append(self.append(kind, entity_id, data))
        return seqs

    def read(self, from_seq: int, to_seq: Optional[int] = None) -> List[WALEntry]:
        results: List[WALEntry] = []
        for seg in self._all_segments():
            for entry in seg.entries:
                if entry.sequence_number < from_seq:
                    continue
                if to_seq is not None and entry.sequence_number > to_seq:
                    continue
                results.append(entry)
        results.sort(key=lambda e: e.sequence_number)
        return results

    def replay(self, from_seq: int = 0) -> List[WALEntry]:
        return self.read(from_seq)

    def current_sequence(self) -> int:
        return self._sequence

    def flush(self) -> None:
        if not self._config.wal_dir:
            return
        self._save_segment_to_disk(self._current_segment)

    def compact(self, snapshot_seq: int) -> None:
        keep: List[WALSegment] = []
        for seg in self._segments:
            if seg.end_seq <= snapshot_seq:
                self._delete_segment_file(seg)
            else:
                keep.append(seg)
        self._segments = keep

    def segments(self) -> List[WALSegment]:
        return list(self._segments) + [self._current_segment]

    def total_size_bytes(self) -> int:
        total = 0
        for seg in self._all_segments():
            total += seg.size_bytes
        return total

    def statistics(self) -> dict:
        all_segs = self._all_segments()
        total_entries = sum(len(s.entries) for s in all_segs)
        return {
            "total_entries": total_entries,
            "total_segments": len(all_segs),
            "current_sequence": self._sequence,
            "total_size_bytes": self.total_size_bytes(),
        }

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def _compute_checksum(self, data: dict) -> str:
        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _verify_integrity(self, entry: WALEntry) -> bool:
        expected = self._compute_checksum(entry.data)
        return expected == entry.checksum

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _all_segments(self) -> List[WALSegment]:
        return self._segments + [self._current_segment]

    def _new_segment(self, start_seq: int) -> WALSegment:
        return WALSegment(
            id=str(uuid.uuid4()),
            entries=[],
            start_seq=start_seq,
            end_seq=start_seq,
            created_at=time.time(),
            size_bytes=0,
        )

    def _write_entry(self, entry: WALEntry) -> None:
        self._current_segment.entries.append(entry)
        self._current_segment.end_seq = entry.sequence_number
        entry_size = len(json.dumps(entry.to_dict()).encode("utf-8"))
        self._current_segment.size_bytes += entry_size

    def _rotate_segment(self) -> None:
        self._save_segment_to_disk(self._current_segment)
        self._segments.append(self._current_segment)
        self._current_segment = self._new_segment(start_seq=self._sequence)

    def _save_segment_to_disk(self, segment: WALSegment) -> None:
        if not self._config.wal_dir:
            return
        path = os.path.join(self._config.wal_dir, f"{segment.id}.json")
        with open(path, "w") as fh:
            json.dump(segment.to_dict(), fh)

    def _delete_segment_file(self, segment: WALSegment) -> None:
        if not self._config.wal_dir:
            return
        path = os.path.join(self._config.wal_dir, f"{segment.id}.json")
        if os.path.exists(path):
            os.remove(path)

    def _load_from_disk(self) -> None:
        if not self._config.wal_dir or not os.path.isdir(self._config.wal_dir):
            return

        files = sorted(
            f for f in os.listdir(self._config.wal_dir) if f.endswith(".json")
        )
        max_seq = -1
        for fname in files:
            path = os.path.join(self._config.wal_dir, fname)
            try:
                with open(path, "r") as fh:
                    seg_data = json.load(fh)
                seg = WALSegment.from_dict(seg_data)
                self._segments.append(seg)
                if seg.entries:
                    last = seg.entries[-1].sequence_number
                    if last > max_seq:
                        max_seq = last
            except (json.JSONDecodeError, KeyError):
                continue

        if max_seq >= 0:
            self._sequence = max_seq + 1
            self._current_segment = self._new_segment(start_seq=self._sequence)
