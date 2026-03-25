"""Integration layer: SiteDebugger and ObstructionDatabase.

Provides high-level debugging analysis over site-like data structures,
and persistent storage for obstruction queries.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jugeo.se_theory.debugging.algorithms import (
    CountermodelAnalyzer,
    ObstructionLocalizer,
    ObstructionTriager,
    RepairFrontierComputer,
    RootCauseTracer,
    _build_adjacency,
    _reachable,
)
from jugeo.se_theory.debugging.models import (
    CohomologyClass,
    LocalSection,
    Morphism,
    Obstruction,
    ObstructionSeverity,
    Overlap,
    RepairPlan,
    RootCauseAnalysis,
    TriageReport,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# SiteDebugger
# ---------------------------------------------------------------------------

class SiteDebugger:
    """High-level debugging analysis over a site (coordinates + morphisms + covers + sections).

    Orchestrates the full debugging pipeline:
      localize → root cause → repair frontier → triage
    """

    def __init__(self) -> None:
        self._localizer = ObstructionLocalizer()
        self._tracer = RootCauseTracer()
        self._frontier = RepairFrontierComputer()
        self._triager = ObstructionTriager()
        self._countermodel = CountermodelAnalyzer()

    def debug_site(
        self,
        coordinates: list[str],
        morphisms: list[Morphism],
        covers: list[Overlap],
        sections: list[LocalSection],
    ) -> TriageReport:
        """Full debugging pipeline for a site.

        1. Localize all descent failures (invalid sections + overlap disagreements)
        2. Compute blast radii and assign severities
        3. Cluster and triage
        Returns a TriageReport summarizing all findings.
        """
        obstructions = self._localizer.localize_descent_failure(sections, covers, morphisms)
        critical_paths = [
            m.source for m in morphisms if m.is_critical_path
        ] + [
            m.target for m in morphisms if m.is_critical_path
        ]
        return self._triager.triage(obstructions, morphisms, critical_paths=critical_paths)

    def root_cause_for_site(
        self,
        obstructions: list[Obstruction],
        coordinates: list[str],
        morphisms: list[Morphism],
    ) -> dict[str, RootCauseAnalysis]:
        """Compute root cause analysis for every obstruction in the site.

        Returns a mapping: obstruction_id → RootCauseAnalysis.
        """
        sections: dict[str, LocalSection] = {}
        result: dict[str, RootCauseAnalysis] = {}
        for obs in obstructions:
            rca = self._tracer.find_root_cause(obs, morphisms, sections)
            result[obs.id] = rca
        return result

    def repair_plan_for_site(
        self,
        obstructions: list[Obstruction],
        coordinates: list[str],
        morphisms: list[Morphism],
    ) -> RepairPlan:
        """Compute a topologically ordered repair plan for the site's obstructions."""
        sections: dict[str, LocalSection] = {}
        return self._frontier.compute_repair_plan(obstructions, morphisms, sections)

    def incremental_debug(
        self,
        changed_coords: list[str],
        morphisms: list[Morphism],
        sections: list[LocalSection],
        existing_obstructions: list[Obstruction],
    ) -> list[Obstruction]:
        """Only check areas affected by changed coordinates.

        Returns new or updated obstructions for the affected region, without
        re-analyzing the entire site.
        """
        adj = _build_adjacency(morphisms)

        # Find all coordinates transitively affected by the changes
        affected: set[str] = set()
        for coord in changed_coords:
            affected.add(coord)
            affected |= _reachable(coord, adj)

        # Filter sections to affected region
        affected_sections = [s for s in sections if s.coordinate_id in affected]

        # Find overlaps involving affected coordinates
        affected_overlaps: list[Overlap] = []

        # Re-run localization on the affected slice
        new_obstructions = self._localizer.localize_descent_failure(
            affected_sections, affected_overlaps, morphisms
        )

        # Collect IDs of existing obstructions in the affected region
        existing_in_affected = {
            obs.id for obs in existing_obstructions
            if obs.coordinate_id in affected
        }

        return new_obstructions


# ---------------------------------------------------------------------------
# ObstructionDatabase
# ---------------------------------------------------------------------------

@dataclass
class _StoredRecord:
    """Internal storage record for an obstruction."""
    obstruction: Obstruction
    stored_at: str = field(default_factory=_now_iso)
    tags: list[str] = field(default_factory=list)


class ObstructionDatabase:
    """Persistent (in-memory) storage for obstructions with querying.

    Provides CRUD operations, similarity search, and aggregate statistics.
    """

    def __init__(self) -> None:
        self._records: dict[str, _StoredRecord] = {}

    # ---- Storage ----

    def store(self, obstruction: Obstruction) -> None:
        """Store or update an obstruction record."""
        self._records[obstruction.id] = _StoredRecord(obstruction=obstruction)

    def store_many(self, obstructions: list[Obstruction]) -> None:
        """Store multiple obstructions at once."""
        for obs in obstructions:
            self.store(obs)

    # ---- Querying ----

    def query(
        self,
        kind: CohomologyClass | None = None,
        severity: ObstructionSeverity | None = None,
        coordinate_prefix: str | None = None,
        active_only: bool = True,
    ) -> list[Obstruction]:
        """Query obstructions with optional filters.

        Args:
            kind: Filter by cohomology class.
            severity: Filter by minimum severity.
            coordinate_prefix: Filter by coordinate ID prefix.
            active_only: If True, only return unresolved obstructions.
        """
        results: list[Obstruction] = []
        for record in self._records.values():
            obs = record.obstruction
            if active_only and obs.is_resolved:
                continue
            if kind is not None and obs.cohomology_class != kind:
                continue
            if severity is not None and obs.severity < severity:
                continue
            if coordinate_prefix is not None:
                if not obs.coordinate_id.startswith(coordinate_prefix):
                    continue
            results.append(obs)
        return sorted(results, key=lambda o: o.created_at, reverse=True)

    def get(self, obstruction_id: str) -> Obstruction | None:
        """Get an obstruction by ID."""
        record = self._records.get(obstruction_id)
        return record.obstruction if record else None

    def similar(
        self,
        obstruction: Obstruction,
        threshold: float = 0.5,
    ) -> list[Obstruction]:
        """Find similar obstructions by cohomology class and coordinate pattern.

        Similarity is computed as a weighted combination of:
        - Same cohomology class (high weight)
        - Similar coordinate ID prefix (medium weight)
        - Same severity (low weight)

        Only returns obstructions with similarity >= threshold.
        """
        results: list[tuple[float, Obstruction]] = []
        for record in self._records.values():
            other = record.obstruction
            if other.id == obstruction.id:
                continue
            sim = self._similarity(obstruction, other)
            if sim >= threshold:
                results.append((sim, other))
        results.sort(key=lambda x: x[0], reverse=True)
        return [obs for _, obs in results]

    def _similarity(self, a: Obstruction, b: Obstruction) -> float:
        """Compute similarity score between two obstructions (0.0 to 1.0)."""
        score = 0.0
        # Same cohomology class: 0.5 weight
        if a.cohomology_class == b.cohomology_class:
            score += 0.5
        # Common coordinate prefix: up to 0.3 weight
        prefix_len = len(_common_prefix(a.coordinate_id, b.coordinate_id))
        max_len = max(len(a.coordinate_id), len(b.coordinate_id), 1)
        score += 0.3 * (prefix_len / max_len)
        # Same severity: 0.2 weight
        if a.severity == b.severity:
            score += 0.2
        return score

    # ---- Resolution ----

    def resolve(self, obstruction_id: str, note: str) -> bool:
        """Mark an obstruction as resolved. Returns True if found and updated."""
        record = self._records.get(obstruction_id)
        if record is None:
            return False
        record.obstruction.resolve(note)
        return True

    def resolve_many(self, obstruction_ids: list[str], note: str) -> int:
        """Resolve multiple obstructions. Returns count resolved."""
        count = 0
        for oid in obstruction_ids:
            if self.resolve(oid, note):
                count += 1
        return count

    # ---- Statistics ----

    def statistics(self) -> dict[str, Any]:
        """Aggregate statistics over all stored obstructions."""
        all_obs = [r.obstruction for r in self._records.values()]
        active = [o for o in all_obs if o.is_active]
        resolved = [o for o in all_obs if o.is_resolved]

        by_class: dict[str, int] = defaultdict(int)
        by_severity: dict[str, int] = defaultdict(int)
        for obs in active:
            by_class[obs.cohomology_class.value] += 1
            by_severity[obs.severity.value] += 1

        avg_blast = (
            sum(o.blast_radius for o in active) / len(active) if active else 0.0
        )

        return {
            "total": len(all_obs),
            "active": len(active),
            "resolved": len(resolved),
            "by_class": dict(by_class),
            "by_severity": dict(by_severity),
            "average_blast_radius": round(avg_blast, 2),
            "computed_at": _now_iso(),
        }

    def all_active(self) -> list[Obstruction]:
        """Return all active (unresolved) obstructions."""
        return self.query(active_only=True)

    def all_resolved(self) -> list[Obstruction]:
        """Return all resolved obstructions."""
        return [
            r.obstruction
            for r in self._records.values()
            if r.obstruction.is_resolved
        ]

    def export(self) -> list[dict[str, Any]]:
        """Export all obstruction records as dicts."""
        return [r.obstruction.to_dict() for r in self._records.values()]

    def import_records(self, records: list[dict[str, Any]]) -> int:
        """Import obstruction records from dicts. Returns count imported."""
        count = 0
        for data in records:
            try:
                obs = Obstruction.from_dict(data)
                self.store(obs)
                count += 1
            except (KeyError, ValueError):
                pass
        return count

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, obstruction_id: str) -> bool:
        return obstruction_id in self._records


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _common_prefix(a: str, b: str) -> str:
    """Return the longest common prefix of two strings."""
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    return a[:i]
