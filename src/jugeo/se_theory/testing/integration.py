r"""Integration layer for the ``jugeo.se_theory.testing`` package.

Provides higher-level facades that take site-like data structures
(coordinates, morphisms, covers, evidence) and produce test suite reports,
obligation lists, and regression scopes without requiring callers to
interact directly with the lower-level algorithms.

Theory note (JuGeo B3):
    SiteTestAnalyzer acts as the *global section assembler* — it takes
    the local data (per-coordinate evidence) and checks whether the descent
    condition holds across the covering.  EvidenceIntegrator translates raw
    test-runner output into the evidence-record format expected by the sheaf.

    copilot: se-theory-testing-integration
"""
from __future__ import annotations

import datetime
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.se_theory.testing.algorithms import (
    CoverageAnalyzer,
    RegressionAnalyzer,
    TestObligationGenerator,
    TestPrioritizer,
    WitnessConstructor,
    higher_trust,
    trust_rank,
)
from jugeo.se_theory.testing.models import (
    CoverageReport,
    ObligationStatus,
    RegressionScope,
    TestLevel,
    TestObligation,
    TestResult,
    TestSuiteReport,
    WitnessSection,
    make_obligation,
    make_result,
)

__all__ = [
    "SiteTestAnalyzer",
    "EvidenceIntegrator",
]


# ---------------------------------------------------------------------------
# SiteTestAnalyzer
# ---------------------------------------------------------------------------


class SiteTestAnalyzer:
    """High-level facade: from site data → test suite reports and obligations.

    Usage example::

        analyzer = SiteTestAnalyzer()
        report = analyzer.analyze(
            coordinates=["mod_a", "mod_b", "mod_c"],
            morphisms=[{"id": "ab", "source": "mod_a", "target": "mod_b"}],
            covers=[{"id": "c1", "members": ["mod_a", "mod_b"]}],
            evidence={"mod_a": {"trust_level": "proof", "passed": True}},
        )
    """

    def __init__(self) -> None:
        self._gen = TestObligationGenerator()
        self._witness = WitnessConstructor()
        self._coverage = CoverageAnalyzer()
        self._prioritizer = TestPrioritizer()
        self._regression = RegressionAnalyzer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
        covers: list[dict[str, Any]],
        evidence: dict[str, Any],
        site_id: str = "",
        suite_id: str = "",
        staleness_threshold_days: float = 7.0,
    ) -> TestSuiteReport:
        """Full analysis: generate obligations, compute coverage, produce report.

        Parameters
        ----------
        coordinates:
            All coordinate IDs in the site.
        morphisms:
            List of morphism dicts: ``{"id", "source", "target"}``.
        covers:
            List of cover dicts: ``{"id", "members": [coord_id, ...]}``.
        evidence:
            Map from coordinate_id → evidence dict or WitnessSection dict.
        site_id:
            Optional site label.
        suite_id:
            Optional suite run label.
        staleness_threshold_days:
            Evidence older than this (in days) is counted as stale.

        Returns
        -------
        TestSuiteReport
        """
        if not suite_id:
            suite_id = uuid.uuid4().hex[:12]

        # Build cover members from coordinates and covers
        cover_members = self._build_cover_members(coordinates, covers)

        # Build overlap list from morphisms
        overlaps = self._morphisms_to_overlaps(morphisms)

        # Generate all obligations
        obligations = self._gen.generate_from_cover(
            cover_members, overlaps, generated_from=site_id or "site"
        )

        # Compute statuses
        satisfied = sum(
            1
            for ob in obligations
            if self._coverage._is_covered(evidence.get(ob.coordinate_id))
        )
        failed = 0
        skipped = 0
        stale = 0

        # Stale: covered but evidence is old (compared to a synthetic change time)
        now = time.time()
        for ob in obligations:
            ev = evidence.get(ob.coordinate_id)
            if ev is None:
                continue
            latest = self._coverage._latest_evidence_time(ev)
            if latest is not None:
                age_days = (now - latest) / 86400.0
                if age_days > staleness_threshold_days:
                    stale += 1

        total = len(obligations)
        pass_rate = satisfied / total if total > 0 else 0.0

        # Geometric coverage
        cov_report = self._coverage.compute_geometric_coverage(
            coordinates, evidence, overlaps, site_id=site_id
        )

        # Trust floor: minimum trust across covered coordinates
        trust_floor = self._compute_trust_floor(coordinates, evidence)

        return TestSuiteReport(
            suite_id=suite_id,
            total_obligations=total,
            satisfied=satisfied,
            failed=failed,
            skipped=skipped,
            stale=stale,
            geometric_coverage=cov_report.geometric_coverage,
            trust_floor=trust_floor,
            pass_rate=pass_rate,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        )

    def suggest_tests(
        self,
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
        covers: list[dict[str, Any]],
        evidence: dict[str, Any],
        max_suggestions: int = 20,
        critical_paths: Optional[list[list[str]]] = None,
    ) -> list[TestObligation]:
        """Return prioritised list of test obligations that should be run.

        Generates the full obligation set, filters out already-satisfied ones,
        then returns the top ``max_suggestions`` by priority score.

        Parameters
        ----------
        coordinates:
            All coordinate IDs.
        morphisms:
            Site morphisms.
        covers:
            Cover dicts.
        evidence:
            Current evidence map.
        max_suggestions:
            Maximum number of obligations to return.
        critical_paths:
            Optional critical-path lists for prioritisation.

        Returns
        -------
        list[TestObligation]
            Sorted by priority descending.
        """
        cover_members = self._build_cover_members(coordinates, covers)
        overlaps = self._morphisms_to_overlaps(morphisms)
        obligations = self._gen.generate_from_cover(cover_members, overlaps)

        # Filter out already-satisfied
        open_obs: list[TestObligation] = [
            ob
            for ob in obligations
            if not self._coverage._is_covered(evidence.get(ob.coordinate_id))
        ]

        if not open_obs:
            return []

        prioritized = self._prioritizer.prioritize(
            open_obs,
            morphisms=morphisms,
            evidence_map=evidence,
            critical_paths=critical_paths,
        )

        # Map back to obligations preserving priority order
        prio_map: dict[str, float] = {p.obligation_id: p.score for p in prioritized}
        open_obs.sort(key=lambda ob: prio_map.get(ob.id, 0.0), reverse=True)
        return open_obs[:max_suggestions]

    def regression_from_diff(
        self,
        changed_files: list[str],
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
        evidence: dict[str, Any],
        change_id: str = "",
    ) -> RegressionScope:
        """Compute regression scope from a set of changed file paths.

        Maps file paths to coordinate IDs by checking whether any coordinate
        ID is a suffix of any changed file path (simple heuristic).

        Parameters
        ----------
        changed_files:
            File paths that changed (e.g. from ``git diff --name-only``).
        coordinates:
            All coordinate IDs in the site.
        morphisms:
            Site morphisms.
        evidence:
            Current evidence map.
        change_id:
            Optional change-set identifier.

        Returns
        -------
        RegressionScope
        """
        # Map file paths to coordinate IDs
        changed_coords: list[str] = []
        for coord in coordinates:
            for filepath in changed_files:
                # Match by suffix or substring
                if coord in filepath or filepath.endswith(coord):
                    if coord not in changed_coords:
                        changed_coords.append(coord)
                    break

        if not changed_coords:
            # Fall back: treat every changed file as its own coordinate
            changed_coords = [f.replace("/", ".").rstrip(".py") for f in changed_files]

        return self._regression.compute_regression_scope(
            changed_coords, morphisms, evidence, change_id=change_id
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_cover_members(
        self,
        coordinates: list[str],
        covers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert coordinate IDs + covers into cover-member dicts."""
        # Collect explicit cover members
        explicit_coords: set[str] = set()
        members: list[dict[str, Any]] = []
        for cover in covers:
            for coord_id in cover.get("members", []):
                if coord_id not in explicit_coords:
                    explicit_coords.add(coord_id)
                    members.append({"id": coord_id, "proposition": f"correctness of {coord_id}"})

        # Add any coordinates not in any cover
        for coord_id in coordinates:
            if coord_id not in explicit_coords:
                members.append({"id": coord_id, "proposition": f"correctness of {coord_id}"})

        return members

    def _morphisms_to_overlaps(
        self,
        morphisms: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert morphism dicts to overlap dicts."""
        overlaps: list[dict[str, Any]] = []
        for m in morphisms:
            src = m.get("source", "")
            tgt = m.get("target", "")
            if src and tgt:
                overlaps.append(
                    {
                        "id": m.get("id", f"{src}_{tgt}"),
                        "coordinate_ids": [src, tgt],
                        "proposition": f"interface between {src} and {tgt}",
                    }
                )
        return overlaps

    def _compute_trust_floor(
        self,
        coordinates: list[str],
        evidence: dict[str, Any],
    ) -> str:
        """Return the minimum trust level across covered coordinates."""
        floor = "verified"  # start at max, bring down
        has_any = False
        for coord in coordinates:
            ev = evidence.get(coord)
            if ev is None:
                continue
            level = self._coverage._extract_trust_level(ev)
            if trust_rank(level) < trust_rank(floor):
                floor = level
            has_any = True
        return floor if has_any else "none"


# ---------------------------------------------------------------------------
# EvidenceIntegrator
# ---------------------------------------------------------------------------


class EvidenceIntegrator:
    """Convert TestResult objects into evidence-record dicts for the sheaf.

    The evidence-record format is a plain dict compatible with WitnessSection's
    ``evidence_records`` list and with the wider JuGeo evidence store.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_result_to_evidence(self, result: TestResult) -> dict[str, Any]:
        """Convert a single TestResult to an evidence-compatible dict.

        The returned dict contains all fields needed by the coverage analyser
        and witness constructor:

        * ``source``: always ``"test_result"``
        * ``result_id``, ``obligation_id``, ``coordinate_id``
        * ``channel``, ``trust_level``, ``passed``
        * ``duration_ms``, ``timestamp``
        * ``evidence_id`` (if present)
        * ``failure_detail`` (if present)

        Parameters
        ----------
        result:
            The TestResult to convert.

        Returns
        -------
        dict[str, Any]
        """
        rec: dict[str, Any] = {
            "source": "test_result",
            "result_id": result.id,
            "obligation_id": result.obligation_id,
            "coordinate_id": result.coordinate_id,
            "channel": result.channel,
            "trust_level": result.trust_achieved,
            "passed": result.passed,
            "duration_ms": result.duration_ms,
            "timestamp": result.timestamp,
        }
        if result.evidence_id is not None:
            rec["evidence_id"] = result.evidence_id
        if result.failure_detail is not None:
            rec["failure_detail"] = result.failure_detail
        return rec

    def bulk_convert(self, results: list[TestResult]) -> list[dict[str, Any]]:
        """Convert a list of TestResults to evidence dicts.

        Parameters
        ----------
        results:
            List of results to convert.

        Returns
        -------
        list[dict[str, Any]]
            One dict per result, in the same order.
        """
        return [self.test_result_to_evidence(r) for r in results]

    def query_stale_evidence(
        self,
        evidence_store: dict[str, list[dict[str, Any]]],
        threshold_days: float = 7.0,
    ) -> list[dict[str, Any]]:
        """Return evidence records whose age exceeds ``threshold_days``.

        Parameters
        ----------
        evidence_store:
            Map from coordinate_id → list of evidence record dicts.
        threshold_days:
            Age threshold in days.

        Returns
        -------
        list[dict[str, Any]]
            Stale evidence records, each augmented with
            ``{"coordinate_id": ..., "age_days": ...}``.
        """
        stale: list[dict[str, Any]] = []
        now = time.time()
        threshold_secs = threshold_days * 86400.0

        for coord_id, records in evidence_store.items():
            for rec in records:
                ts = float(rec.get("timestamp", 0.0))
                age_secs = now - ts
                if age_secs > threshold_secs:
                    augmented = dict(rec)
                    augmented["coordinate_id"] = coord_id
                    augmented["age_days"] = age_secs / 86400.0
                    stale.append(augmented)

        stale.sort(key=lambda r: r.get("age_days", 0.0), reverse=True)
        return stale

    def build_evidence_map(
        self,
        results: list[TestResult],
    ) -> dict[str, Any]:
        """Build a coordinate → evidence dict from a flat list of results.

        When multiple results exist for the same coordinate, the one with
        the highest trust is stored (pass over fail for equal trust).

        Parameters
        ----------
        results:
            All test results.

        Returns
        -------
        dict[str, Any]
            Map from coordinate_id → best evidence dict.
        """
        best: dict[str, dict[str, Any]] = {}
        for result in results:
            coord = result.coordinate_id
            rec = self.test_result_to_evidence(result)
            if coord not in best:
                best[coord] = rec
            else:
                existing_rank = trust_rank(best[coord].get("trust_level", "none"))
                new_rank = trust_rank(rec.get("trust_level", "none"))
                if new_rank > existing_rank or (
                    new_rank == existing_rank and rec.get("passed", False)
                ):
                    best[coord] = rec
        return best
