"""High-level evidence collector integrating all subsystems.

Provides ``WebEvidenceCollector`` which orchestrates file loading,
evidence collection, bundling, gap analysis, and reporting.
"""
from __future__ import annotations

import os
from collections import defaultdict

from .models import (
    TRUST_ORDER,
    EvidenceBundle,
    EvidenceGap,
    WebEvidence,
    trust_level_index,
)
from .multi_channel import (
    EvidenceCombiner,
    EvidenceGapAnalyzer,
    MultiChannelEvidenceEngine,
)


# ---------------------------------------------------------------------------
# File-extension helpers
# ---------------------------------------------------------------------------

_EXT_MAP: dict[str, str] = {
    ".py": "py_files",
    ".html": "template_files",
    ".jinja2": "template_files",
    ".j2": "template_files",
    ".css": "css_files",
    ".js": "js_files",
    ".sql": "sql_files",
}


def _load_project_files(project_dir: str) -> dict[str, dict[str, str]]:
    """Walk *project_dir* and return sources grouped by category."""
    buckets: dict[str, dict[str, str]] = {
        "py_files": {},
        "template_files": {},
        "css_files": {},
        "html_files": {},
        "js_files": {},
        "sql_files": {},
    }

    if not os.path.isdir(project_dir):
        return buckets

    for dirpath, _dirnames, filenames in os.walk(project_dir):
        # Skip common non-source directories.
        rel = os.path.relpath(dirpath, project_dir)
        parts = rel.split(os.sep)
        if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv") for p in parts):
            continue

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            bucket_key = _EXT_MAP.get(ext)
            if bucket_key is None:
                continue

            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, project_dir)

            try:
                with open(full_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue

            buckets[bucket_key][rel_path] = content

            # HTML files also go into the html_files bucket
            # (template_files are Jinja2 superset of HTML).
            if ext == ".html" and bucket_key == "template_files":
                buckets["html_files"][rel_path] = content

    return buckets


# ---------------------------------------------------------------------------
# WebEvidenceCollector
# ---------------------------------------------------------------------------

class WebEvidenceCollector:
    """Orchestrator that loads, collects, bundles, and analyses evidence."""

    def __init__(self) -> None:
        self._engine = MultiChannelEvidenceEngine()
        self._combiner = EvidenceCombiner()
        self._gap_analyzer = EvidenceGapAnalyzer()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def collect_and_analyze(self, project_dir: str) -> dict:
        """Load sources from *project_dir*, collect evidence, and analyse.

        Returns a dict with keys:
        * ``evidence`` – serialised evidence items
        * ``bundles`` – serialised evidence bundles
        * ``gaps`` – serialised evidence gaps
        * ``summary`` – high-level statistics
        """
        project_data = _load_project_files(project_dir)

        evidence = self._engine.collect_evidence(project_data)

        # Group by coordinate_id → bundle.
        by_coord: dict[str, list[WebEvidence]] = defaultdict(list)
        for ev in evidence:
            by_coord[ev.coordinate_id].append(ev)

        bundles = [
            self._combiner.combine(items)
            for items in by_coord.values()
        ]

        all_coordinates = sorted(by_coord.keys())
        gaps = self._gap_analyzer.find_gaps(bundles, all_coordinates)

        report = self.generate_evidence_report(evidence, bundles)

        return {
            "evidence": [e.to_dict() for e in evidence],
            "bundles": [b.to_dict() for b in bundles],
            "gaps": [g.to_dict() for g in gaps],
            "summary": report,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_evidence_report(
        self,
        evidence: list[WebEvidence],
        bundles: list[EvidenceBundle],
    ) -> dict:
        """Return aggregate statistics about the collected evidence."""
        by_channel: dict[str, int] = defaultdict(int)
        by_trust: dict[str, int] = defaultdict(int)

        for ev in evidence:
            by_channel[ev.channel.value] += 1
            by_trust[ev.trust_level.value] += 1

        convergence_values = [b.convergence_score for b in bundles]
        avg_convergence = (
            sum(convergence_values) / len(convergence_values)
            if convergence_values
            else 0.0
        )

        return {
            "total_evidence": len(evidence),
            "total_bundles": len(bundles),
            "by_channel": dict(by_channel),
            "by_trust_level": dict(by_trust),
            "avg_convergence": round(avg_convergence, 4),
        }

    def trust_summary(self, bundles: list[EvidenceBundle]) -> dict:
        """Summarise trust levels across all bundles."""
        level_counts: dict[str, int] = defaultdict(int)
        indices: list[int] = []

        for b in bundles:
            level_counts[b.combined_trust] += 1
            idx = trust_level_index(b.combined_trust)
            if idx >= 0:
                indices.append(idx)

        if not indices:
            return {
                "level_counts": {},
                "highest_trust": "",
                "lowest_trust": "",
                "avg_trust_index": 0.0,
            }

        return {
            "level_counts": dict(level_counts),
            "highest_trust": TRUST_ORDER[max(indices)],
            "lowest_trust": TRUST_ORDER[min(indices)],
            "avg_trust_index": round(sum(indices) / len(indices), 4),
        }

    def coverage_summary(
        self,
        bundles: list[EvidenceBundle],
        all_coords: list[str],
    ) -> dict:
        """Summarise evidence coverage over the coordinate space."""
        covered = {b.coordinate_id for b in bundles}
        total = len(all_coords)
        covered_count = sum(1 for c in all_coords if c in covered)
        uncovered = sorted(set(all_coords) - covered)

        return {
            "total_coords": total,
            "covered_coords": covered_count,
            "coverage_pct": round(covered_count / total * 100, 2) if total else 0.0,
            "uncovered_coords": uncovered,
        }
