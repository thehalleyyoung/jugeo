"""Delta computation engine.

``DeltaEngine`` maps file-level ``ChangeSet`` objects to coordinate/morphism
``DeltaRecord`` objects, and provides helpers for merging, applying, and
inverting deltas.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.scaling.incremental.models import (
    ChangeKind,
    ChangeSet,
    DeltaRecord,
    FileChange,
    FileState,
)


# ---------------------------------------------------------------------------
# DeltaEngine
# ---------------------------------------------------------------------------


class DeltaEngine:
    """Computes coordinate and morphism deltas from file-level change sets.

    All methods are stateless — the engine itself carries no persistent data.
    """

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def compute_delta(
        self,
        change_set: ChangeSet,
        file_states: dict[str, FileState],
        import_edges: dict[str, list[tuple[str, str]]],
    ) -> DeltaRecord:
        """Compute a DeltaRecord from *change_set*.

        Parameters
        ----------
        change_set:
            The set of detected file changes.
        file_states:
            Current snapshot of file states (after changes applied).
        import_edges:
            Per-file import edge lists (module-name pairs).
        """
        added_coords: list[str] = []
        removed_coords: list[str] = []
        modified_coords: list[str] = []
        added_morphisms: list[str] = []
        removed_morphisms: list[str] = []
        modified_morphisms: list[str] = []

        for change in change_set.changes:
            kind = change.kind

            if kind == ChangeKind.CREATED:
                state = file_states.get(change.path)
                coords = self._coordinates_for_file(change.path, state) if state else []
                added_coords.extend(coords)
                morphisms = self._morphisms_for_file(
                    change.path, import_edges.get(change.path, [])
                )
                added_morphisms.extend(morphisms)

            elif kind == ChangeKind.DELETED:
                # Use old coordinates from the change_set's affected_coordinates
                # or derive from import edges before deletion
                coords = [
                    cid
                    for cid in change_set.affected_coordinates
                    if cid.startswith(self._file_prefix(change.path))
                ]
                if not coords:
                    # Best-effort: re-derive from previous state
                    state = file_states.get(change.path)
                    coords = self._coordinates_for_file(change.path, state) if state else []
                removed_coords.extend(coords)
                morphisms = self._morphisms_for_file(
                    change.path, import_edges.get(change.path, [])
                )
                removed_morphisms.extend(morphisms)

            elif kind == ChangeKind.MODIFIED:
                state = file_states.get(change.path)
                coords = self._coordinates_for_file(change.path, state) if state else []
                modified_coords.extend(coords)
                morphisms = self._morphisms_for_file(
                    change.path, import_edges.get(change.path, [])
                )
                modified_morphisms.extend(morphisms)

            elif kind in (ChangeKind.RENAMED, ChangeKind.MOVED):
                # Old path coordinates removed, new path coordinates added
                if change.old_path:
                    old_state = file_states.get(change.old_path)
                    old_coords = (
                        self._coordinates_for_file(change.old_path, old_state)
                        if old_state
                        else []
                    )
                    removed_coords.extend(old_coords)
                    old_morphisms = self._morphisms_for_file(
                        change.old_path, import_edges.get(change.old_path, [])
                    )
                    removed_morphisms.extend(old_morphisms)

                new_state = file_states.get(change.path)
                new_coords = (
                    self._coordinates_for_file(change.path, new_state)
                    if new_state
                    else []
                )
                added_coords.extend(new_coords)
                new_morphisms = self._morphisms_for_file(
                    change.path, import_edges.get(change.path, [])
                )
                added_morphisms.extend(new_morphisms)

        return DeltaRecord(
            change_set_id=change_set.id,
            added_coordinates=_dedupe(added_coords),
            removed_coordinates=_dedupe(removed_coords),
            modified_coordinates=_dedupe(modified_coords),
            added_morphisms=_dedupe(added_morphisms),
            removed_morphisms=_dedupe(removed_morphisms),
            modified_morphisms=_dedupe(modified_morphisms),
        )

    # ------------------------------------------------------------------
    # Per-file helpers
    # ------------------------------------------------------------------

    def _coordinates_for_file(
        self, filepath: str, file_state: FileState | None
    ) -> list[str]:
        """Return coordinate IDs for *filepath*.

        Uses ``file_state.coordinate_ids`` if populated; otherwise derives a
        synthetic module-level coordinate ID from the path.
        """
        if file_state and file_state.coordinate_ids:
            return list(file_state.coordinate_ids)
        # Derive module-level coordinate from path
        return [self._file_prefix(filepath)]

    def _morphisms_for_file(
        self,
        filepath: str,
        import_edges: list[tuple[str, str]],
    ) -> list[str]:
        """Convert import edges into morphism IDs.

        Each edge ``(importer, imported)`` becomes the string
        ``"importer->imported"``.
        """
        return [f"{src}->{dst}" for src, dst in import_edges]

    @staticmethod
    def _file_prefix(filepath: str) -> str:
        """Return a stable coordinate-ID prefix derived from *filepath*."""
        # Use the stem (without extension) as the synthetic module ID
        from pathlib import Path

        return Path(filepath).stem

    # ------------------------------------------------------------------
    # Coordinate classification
    # ------------------------------------------------------------------

    def _classify_coordinate_change(
        self,
        old_coords: list[str],
        new_coords: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Return (added, removed, modified) coordinate ID lists.

        Because we only have IDs (not full descriptors) here, "modified" is
        approximated as IDs present in both sets but whose parent file changed.
        """
        old_set = set(old_coords)
        new_set = set(new_coords)
        added = list(new_set - old_set)
        removed = list(old_set - new_set)
        both = list(old_set & new_set)
        return added, removed, both

    # ------------------------------------------------------------------
    # Overlap / cover helpers
    # ------------------------------------------------------------------

    def affected_overlaps(
        self, delta: DeltaRecord, all_overlaps: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return overlaps that reference any coordinate in *delta*."""
        affected_ids = set(delta.all_affected_coordinates())
        result: list[dict[str, Any]] = []
        for overlap in all_overlaps:
            participants = set(overlap.get("coordinates", []))
            if participants & affected_ids:
                result.append(overlap)
        return result

    def affected_covers(
        self, delta: DeltaRecord, all_covers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return covers that reference any coordinate in *delta*."""
        affected_ids = set(delta.all_affected_coordinates())
        result: list[dict[str, Any]] = []
        for cover in all_covers:
            members = set(cover.get("coordinates", []))
            if members & affected_ids:
                result.append(cover)
        return result

    # ------------------------------------------------------------------
    # Delta algebra
    # ------------------------------------------------------------------

    def merge_deltas(self, deltas: list[DeltaRecord]) -> DeltaRecord:
        """Merge multiple DeltaRecords into one combined record.

        The ``change_set_id`` of the merged record is a freshly generated UUID.
        Contradictions (add + remove of same ID) are resolved conservatively:
        net-added IDs win over net-removed IDs.
        """
        if not deltas:
            return DeltaRecord(change_set_id=str(uuid.uuid4()))

        all_added_c: set[str] = set()
        all_removed_c: set[str] = set()
        all_modified_c: set[str] = set()
        all_added_m: set[str] = set()
        all_removed_m: set[str] = set()
        all_modified_m: set[str] = set()

        for d in deltas:
            all_added_c.update(d.added_coordinates)
            all_removed_c.update(d.removed_coordinates)
            all_modified_c.update(d.modified_coordinates)
            all_added_m.update(d.added_morphisms)
            all_removed_m.update(d.removed_morphisms)
            all_modified_m.update(d.modified_morphisms)

        # Resolve conflicts: if something was both added and removed, treat as
        # modified (the net effect is uncertain — conservative choice).
        conflict_c = all_added_c & all_removed_c
        all_added_c -= conflict_c
        all_removed_c -= conflict_c
        all_modified_c |= conflict_c

        conflict_m = all_added_m & all_removed_m
        all_added_m -= conflict_m
        all_removed_m -= conflict_m
        all_modified_m |= conflict_m

        return DeltaRecord(
            change_set_id=str(uuid.uuid4()),
            added_coordinates=sorted(all_added_c),
            removed_coordinates=sorted(all_removed_c),
            modified_coordinates=sorted(all_modified_c),
            added_morphisms=sorted(all_added_m),
            removed_morphisms=sorted(all_removed_m),
            modified_morphisms=sorted(all_modified_m),
        )

    def apply_delta(self, state: dict[str, Any], delta: DeltaRecord) -> dict[str, Any]:
        """Return a *new* state dict with *delta* applied.

        Expects *state* to have optional keys:
        ``coordinates`` (set/list of IDs) and ``morphisms`` (set/list of IDs).
        """
        new_state = copy.deepcopy(state)

        coords: set[str] = set(new_state.get("coordinates", []))
        coords -= set(delta.removed_coordinates)
        coords |= set(delta.added_coordinates)
        # Modified coordinates remain — they already exist
        new_state["coordinates"] = sorted(coords)

        morphisms: set[str] = set(new_state.get("morphisms", []))
        morphisms -= set(delta.removed_morphisms)
        morphisms |= set(delta.added_morphisms)
        new_state["morphisms"] = sorted(morphisms)

        new_state.setdefault("applied_deltas", []).append(delta.change_set_id)
        return new_state

    def invert_delta(self, delta: DeltaRecord) -> DeltaRecord:
        """Return the inverse delta that would undo *delta*'s effects."""
        return DeltaRecord(
            change_set_id=str(uuid.uuid4()),
            # Swap added ↔ removed; modified stays modified
            added_coordinates=list(delta.removed_coordinates),
            removed_coordinates=list(delta.added_coordinates),
            modified_coordinates=list(delta.modified_coordinates),
            added_morphisms=list(delta.removed_morphisms),
            removed_morphisms=list(delta.added_morphisms),
            modified_morphisms=list(delta.modified_morphisms),
        )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _dedupe(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
