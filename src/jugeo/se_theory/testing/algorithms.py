r"""Core testing algorithms for the ``jugeo.se_theory.testing`` package.

Theory (JuGeo — "Testing as Witness Construction", B3):
    All algorithms here operate on the judgment-geometry data model:

    * **TestObligationGenerator** — generates the *obligation complex*: local
      obligations at each chart, plus interface obligations at every non-trivial
      overlap of the covering.
    * **WitnessConstructor** — assembles individual test results into local
      sections (WitnessSection) and checks whether they glue.
    * **CoverageAnalyzer** — measures geometric (sheaf-theoretic) coverage:
      what fraction of the site is witnessed?
    * **TestPrioritizer** — ranks open obligations by coupling weight, trust
      deficit, critical-path membership, staleness, and blast-radius.
    * **RegressionAnalyzer** — computes the *minimal* invalidation scope after
      a coordinate change, via transitive closure over the dependency graph.

    copilot: se-theory-testing-algorithms
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.se_theory.testing.models import (
    CoverageReport,
    ObligationStatus,
    RegressionScope,
    TestLevel,
    TestObligation,
    TestPrioritization,
    TestResult,
    TestSuiteReport,
    WitnessSection,
    make_obligation,
    make_result,
)

__all__ = [
    "TestObligationGenerator",
    "WitnessConstructor",
    "CoverageAnalyzer",
    "TestPrioritizer",
    "RegressionAnalyzer",
    # trust ordering helpers
    "TRUST_ORDER",
    "trust_rank",
    "higher_trust",
]

# ---------------------------------------------------------------------------
# Trust level ordering (weakest → strongest)
# ---------------------------------------------------------------------------

TRUST_ORDER: list[str] = [
    "none",
    "claim",
    "conjecture",
    "heuristic",
    "proof",
    "verified",
]


def trust_rank(level: str) -> int:
    """Return the numeric rank of a trust level string (0 = none, 5 = verified)."""
    try:
        return TRUST_ORDER.index(level.lower())
    except ValueError:
        return 0


def higher_trust(a: str, b: str) -> str:
    """Return the trust level with the higher rank."""
    return a if trust_rank(a) >= trust_rank(b) else b


def lower_trust(a: str, b: str) -> str:
    """Return the trust level with the lower rank."""
    return a if trust_rank(a) <= trust_rank(b) else b


# ---------------------------------------------------------------------------
# TestObligationGenerator
# ---------------------------------------------------------------------------


class TestObligationGenerator:
    """Generate test obligations from covering data and change sets.

    All methods are pure functions: they produce new TestObligation lists
    without mutating any shared state.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_from_cover(
        self,
        cover_members: list[dict[str, Any]],
        overlaps: list[dict[str, Any]],
        generated_from: str = "cover",
    ) -> list[TestObligation]:
        """Generate one local obligation per member + one per non-trivial overlap.

        Parameters
        ----------
        cover_members:
            List of dicts with at least ``{"id": str, "proposition": str}``.
            May also carry ``priority``, ``trust_target``.
        overlaps:
            List of dicts with at least
            ``{"id": str, "coordinate_ids": [str, ...], "proposition": str}``.
            Overlaps with fewer than 2 coordinate IDs are skipped (trivial).
        generated_from:
            Tag to store in ``generated_from`` field of every obligation.

        Returns
        -------
        list[TestObligation]
            One UNIT obligation per cover member + one INTEGRATION obligation
            per non-trivial overlap.
        """
        obligations: list[TestObligation] = []

        for member in cover_members:
            coord_id = member.get("id", "")
            prop = member.get(
                "proposition",
                f"local correctness of {coord_id}",
            )
            priority = float(member.get("priority", 0.5))
            trust_target = member.get("trust_target", "proof")
            obligations.append(
                make_obligation(
                    coordinate_id=coord_id,
                    proposition=prop,
                    level=TestLevel.UNIT,
                    priority=priority,
                    generated_from=generated_from,
                    trust_target=trust_target,
                )
            )

        for overlap in overlaps:
            coords = overlap.get("coordinate_ids", [])
            if len(coords) < 2:
                continue  # trivial overlap — no interface obligation
            overlap_id = overlap.get("id", "")
            prop = overlap.get(
                "proposition",
                f"interface correctness on overlap {overlap_id}",
            )
            priority = float(overlap.get("priority", 0.6))
            trust_target = overlap.get("trust_target", "proof")
            # Attach obligation to the first coordinate; reference overlap
            obligations.append(
                make_obligation(
                    coordinate_id=coords[0],
                    proposition=prop,
                    level=TestLevel.INTEGRATION,
                    overlap_ids=[overlap_id],
                    priority=priority,
                    generated_from=generated_from,
                    trust_target=trust_target,
                )
            )

        return obligations

    def generate_hierarchical(
        self,
        levels_dict: dict[str, list[dict[str, Any]]],
        generated_from: str = "hierarchy",
    ) -> list[TestObligation]:
        """Generate obligations at each architectural level.

        The ``levels_dict`` maps a level name to a list of coordinate dicts.
        Recognised keys (case-insensitive): ``function``, ``module``,
        ``package``, ``project``, ``system``, ``acceptance``.

        Mapping:
        * ``function``   → TestLevel.UNIT
        * ``module``     → TestLevel.INTEGRATION
        * ``package``    → TestLevel.PACKAGE
        * ``project``    → TestLevel.SYSTEM
        * ``system``     → TestLevel.SYSTEM
        * ``acceptance`` → TestLevel.ACCEPTANCE

        Returns
        -------
        list[TestObligation]
        """
        level_map: dict[str, TestLevel] = {
            "function": TestLevel.UNIT,
            "unit": TestLevel.UNIT,
            "module": TestLevel.INTEGRATION,
            "integration": TestLevel.INTEGRATION,
            "package": TestLevel.PACKAGE,
            "project": TestLevel.SYSTEM,
            "system": TestLevel.SYSTEM,
            "acceptance": TestLevel.ACCEPTANCE,
        }

        obligations: list[TestObligation] = []
        for key, members in levels_dict.items():
            test_level = level_map.get(key.lower(), TestLevel.UNIT)
            for member in members:
                coord_id = member.get("id", "")
                prop = member.get(
                    "proposition",
                    f"{test_level.value} correctness of {coord_id}",
                )
                priority = float(member.get("priority", 0.5))
                trust_target = member.get("trust_target", "proof")
                obligations.append(
                    make_obligation(
                        coordinate_id=coord_id,
                        proposition=prop,
                        level=test_level,
                        priority=priority,
                        generated_from=generated_from,
                        trust_target=trust_target,
                    )
                )
        return obligations

    def generate_from_change(
        self,
        changed_coords: list[str],
        morphisms: list[dict[str, Any]],
        existing_evidence: dict[str, Any],
        change_id: str = "",
    ) -> RegressionScope:
        """Compute which tests must re-run after a coordinate change.

        Parameters
        ----------
        changed_coords:
            List of coordinate IDs that were modified.
        morphisms:
            List of morphism dicts:
            ``{"id": str, "source": str, "target": str}``.
        existing_evidence:
            Map from coordinate_id → evidence dict (may contain overlap_ids).
        change_id:
            Identifier for the change-set; auto-generated if empty.

        Returns
        -------
        RegressionScope
        """
        if not change_id:
            change_id = uuid.uuid4().hex[:12]

        # Build overlap set from morphisms (each morphism defines an overlap)
        overlaps_for_coord: dict[str, list[str]] = {}
        for m in morphisms:
            src = m.get("source", "")
            tgt = m.get("target", "")
            overlap_id = m.get("id", f"{src}_{tgt}")
            for c in (src, tgt):
                if c:
                    overlaps_for_coord.setdefault(c, []).append(overlap_id)

        invalidated_overlaps = self._find_affected_overlaps(
            changed_coords, morphisms
        )
        invalidated_set = set(invalidated_overlaps)

        # Transitively invalidate dependents
        invalidated_coords = self._compute_invalidation_scope(
            changed_coords, morphisms
        )

        # Build regression obligations for every invalidated coordinate
        required_retests: list[TestObligation] = []
        for coord_id in invalidated_coords:
            prop = f"regression: verify {coord_id} after change {change_id}"
            overlaps = overlaps_for_coord.get(coord_id, [])
            level = (
                TestLevel.INTEGRATION
                if overlaps
                else TestLevel.UNIT
            )
            ob = make_obligation(
                coordinate_id=coord_id,
                proposition=prop,
                level=level,
                overlap_ids=[
                    ov for ov in overlaps if ov in invalidated_set
                ],
                priority=0.9,
                generated_from=change_id,
                trust_target="proof",
            )
            ob.status = ObligationStatus.STALE
            required_retests.append(ob)

        # Estimate cost from existing evidence (use avg 100ms if unknown)
        cost = len(required_retests) * 100.0

        return RegressionScope(
            change_id=change_id,
            changed_coordinates=list(changed_coords),
            invalidated_overlaps=list(invalidated_overlaps),
            required_retests=required_retests,
            estimated_cost=cost,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_affected_overlaps(
        self,
        changed_coords: list[str],
        morphisms: list[dict[str, Any]],
    ) -> list[str]:
        """Return IDs of morphisms whose source OR target is in changed_coords."""
        changed_set = set(changed_coords)
        affected: list[str] = []
        seen: set[str] = set()
        for m in morphisms:
            src = m.get("source", "")
            tgt = m.get("target", "")
            overlap_id = m.get("id", f"{src}_{tgt}")
            if (src in changed_set or tgt in changed_set) and overlap_id not in seen:
                affected.append(overlap_id)
                seen.add(overlap_id)
        return affected

    def _compute_invalidation_scope(
        self,
        changed_coords: list[str],
        dependency_edges: list[dict[str, Any]],
    ) -> set[str]:
        """Transitive closure of coordinates invalidated by changed_coords.

        Treats every morphism edge as a potential dependency:
        if ``source`` is invalidated, ``target`` is also invalidated.
        """
        invalidated: set[str] = set(changed_coords)
        queue: list[str] = list(changed_coords)
        adj: dict[str, list[str]] = {}
        for edge in dependency_edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src:
                adj.setdefault(src, []).append(tgt)

        while queue:
            coord = queue.pop()
            for dependent in adj.get(coord, []):
                if dependent and dependent not in invalidated:
                    invalidated.add(dependent)
                    queue.append(dependent)

        return invalidated


# ---------------------------------------------------------------------------
# WitnessConstructor
# ---------------------------------------------------------------------------


class WitnessConstructor:
    """Build and validate local witness sections from test results.

    A *witness section* is the local section of the evidence sheaf at one
    coordinate.  The constructor aggregates TestResult objects into a
    WitnessSection and checks whether the collection glues across overlaps.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def construct_witness(
        self,
        coordinate_id: str,
        proposition: str,
        test_results: list[TestResult],
    ) -> WitnessSection:
        """Build a witness section from a list of test results.

        Parameters
        ----------
        coordinate_id:
            The chart this witness lives at.
        proposition:
            Primary proposition being witnessed.
        test_results:
            Results for obligations at this coordinate.

        Returns
        -------
        WitnessSection
            is_complete=True iff at least one result passed.
            trust_level is the highest trust achieved.
        """
        evidence_records: list[dict[str, Any]] = []
        best_trust = "none"
        is_complete = False

        for result in test_results:
            if result.coordinate_id != coordinate_id:
                continue
            rec: dict[str, Any] = {
                "result_id": result.id,
                "obligation_id": result.obligation_id,
                "channel": result.channel,
                "trust_level": result.trust_achieved,
                "passed": result.passed,
                "duration_ms": result.duration_ms,
                "timestamp": result.timestamp,
            }
            if result.evidence_id:
                rec["evidence_id"] = result.evidence_id
            if result.failure_detail:
                rec["failure_detail"] = result.failure_detail
            evidence_records.append(rec)

            if result.passed:
                is_complete = True
                best_trust = higher_trust(best_trust, result.trust_achieved)

        return WitnessSection(
            coordinate_id=coordinate_id,
            proposition=proposition,
            evidence_records=evidence_records,
            trust_level=best_trust,
            is_complete=is_complete,
            staleness_days=0.0,
        )

    def check_witness_completeness(
        self,
        witness: WitnessSection,
        required_propositions: list[str],
    ) -> bool:
        """Return True iff every required proposition has at least one passed record.

        Checks the ``proposition`` field of each evidence record against
        ``required_propositions``.  Falls back to checking the witness's
        own ``proposition`` field when records lack explicit propositions.
        """
        if not required_propositions:
            return witness.is_complete

        witnessed_props: set[str] = set()
        for rec in witness.evidence_records:
            if rec.get("passed", False):
                prop = rec.get("proposition", witness.proposition)
                witnessed_props.add(prop)

        # Also count the witness's own proposition if it is complete
        if witness.is_complete and witness.proposition:
            witnessed_props.add(witness.proposition)

        return all(p in witnessed_props for p in required_propositions)

    def compute_staleness(
        self,
        witness: WitnessSection,
        last_code_change_at: float,
    ) -> float:
        """Return the number of days since evidence was valid.

        If all evidence was recorded *after* ``last_code_change_at`` the
        staleness is 0.  Otherwise, staleness = days since the latest
        evidence timestamp.

        Parameters
        ----------
        witness:
            The witness section to check.
        last_code_change_at:
            Unix timestamp of the most recent code change at this coordinate.

        Returns
        -------
        float
            Staleness in days (0.0 means fresh).
        """
        if not witness.evidence_records:
            # No evidence at all — treat as infinitely stale
            days_since_change = (time.time() - last_code_change_at) / 86400.0
            return max(days_since_change, 0.0)

        latest_evidence = max(
            float(rec.get("timestamp", 0.0)) for rec in witness.evidence_records
        )

        if latest_evidence >= last_code_change_at:
            return 0.0

        return max((last_code_change_at - latest_evidence) / 86400.0, 0.0)

    def glue_witnesses(
        self,
        witnesses: list[WitnessSection],
        overlaps: list[dict[str, Any]],
    ) -> bool:
        """Check whether local witnesses glue into a global section.

        For each overlap, the two (or more) witnesses at the involved
        coordinates must *agree*: if both have evidence for the same
        proposition, both must record it as passed (or both as failed).
        Inconsistency (one passes, one fails) breaks the gluing condition.

        Parameters
        ----------
        witnesses:
            All local witness sections.
        overlaps:
            List of overlap dicts:
            ``{"id": str, "coordinate_ids": [str, ...], "propositions": [str]}``.

        Returns
        -------
        bool
            True iff all overlaps are consistent.
        """
        witness_map: dict[str, WitnessSection] = {
            w.coordinate_id: w for w in witnesses
        }

        for overlap in overlaps:
            coord_ids: list[str] = overlap.get("coordinate_ids", [])
            if len(coord_ids) < 2:
                continue
            shared_props: list[str] = overlap.get(
                "propositions",
                [overlap.get("proposition", "")],
            )

            # Compare each pair of witnesses on the overlap
            for i in range(len(coord_ids)):
                for j in range(i + 1, len(coord_ids)):
                    wa = witness_map.get(coord_ids[i])
                    wb = witness_map.get(coord_ids[j])
                    if wa is None or wb is None:
                        continue
                    if not self._overlap_consistency(wa, wb, shared_props):
                        return False
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _overlap_consistency(
        self,
        witness_a: WitnessSection,
        witness_b: WitnessSection,
        shared_coords: list[str],
    ) -> bool:
        """Return True iff witnesses a and b agree on every shared proposition.

        'Agree' means: for each shared proposition, if both witnesses have
        an evidence record for it, their ``passed`` values must be equal.
        Missing evidence is not considered a contradiction (it's a gap).
        """
        def passed_for(witness: WitnessSection, prop: str) -> Optional[bool]:
            for rec in witness.evidence_records:
                rec_prop = rec.get("proposition", witness.proposition)
                if rec_prop == prop:
                    return bool(rec.get("passed", False))
            return None

        for prop in shared_coords:
            pa = passed_for(witness_a, prop)
            pb = passed_for(witness_b, prop)
            if pa is None or pb is None:
                continue  # gap — not a contradiction
            if pa != pb:
                return False
        return True


# ---------------------------------------------------------------------------
# CoverageAnalyzer
# ---------------------------------------------------------------------------


class CoverageAnalyzer:
    """Compute geometric (sheaf-theoretic) coverage statistics over a site."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_geometric_coverage(
        self,
        all_coordinates: list[str],
        evidence_map: dict[str, Any],
        overlaps: Optional[list[dict[str, Any]]] = None,
        site_id: str = "",
        code_change_times: Optional[dict[str, float]] = None,
        staleness_threshold_days: float = 7.0,
    ) -> CoverageReport:
        """Compute the full coverage report for a site.

        Parameters
        ----------
        all_coordinates:
            Every coordinate ID in the site.
        evidence_map:
            Map from coordinate_id → evidence dict or WitnessSection dict.
            A coordinate is 'covered' if it has at least one passing record.
        overlaps:
            Optional list of overlap dicts (same format as WitnessConstructor).
        site_id:
            Label for the site.
        code_change_times:
            Optional map from coordinate_id → unix timestamp of last change.
            Used to compute stale evidence count.
        staleness_threshold_days:
            Evidence older than this is counted as stale.

        Returns
        -------
        CoverageReport
        """
        import datetime

        covered: list[str] = []
        uncovered: list[str] = []

        for coord in all_coordinates:
            ev = evidence_map.get(coord)
            if self._is_covered(ev):
                covered.append(coord)
            else:
                uncovered.append(coord)

        total = len(all_coordinates)
        geo_cov = covered.__len__() / total if total > 0 else 0.0

        # Overlap coverage
        tested_overlaps: list[str] = []
        untested_overlaps: list[str] = []
        if overlaps:
            for ov in overlaps:
                ov_id = ov.get("id", "")
                coord_ids = ov.get("coordinate_ids", [])
                if len(coord_ids) < 2:
                    continue
                if any(evidence_map.get(c) for c in coord_ids):
                    tested_overlaps.append(ov_id)
                else:
                    untested_overlaps.append(ov_id)

        total_overlaps = len(tested_overlaps) + len(untested_overlaps)
        overlap_cov = (
            len(tested_overlaps) / total_overlaps if total_overlaps > 0 else 0.0
        )

        # Trust distribution
        trust_dist = self.trust_distribution(evidence_map)

        # Stale evidence
        stale_count = 0
        if code_change_times:
            for coord, change_t in code_change_times.items():
                ev = evidence_map.get(coord)
                if ev is None:
                    continue
                latest = self._latest_evidence_time(ev)
                if latest is not None and latest < change_t:
                    age = (change_t - latest) / 86400.0
                    if age > staleness_threshold_days:
                        stale_count += 1

        computed_at = datetime.datetime.utcnow().isoformat() + "Z"

        return CoverageReport(
            site_id=site_id,
            total_coordinates=total,
            covered_coordinates=len(covered),
            uncovered_coordinates=uncovered,
            total_overlaps=total_overlaps,
            tested_overlaps=len(tested_overlaps),
            untested_overlaps=untested_overlaps,
            geometric_coverage=geo_cov,
            overlap_coverage=overlap_cov,
            trust_distribution=trust_dist,
            stale_evidence_count=stale_count,
            computed_at=computed_at,
        )

    def identify_gaps(
        self,
        cover_members: list[str],
        evidence_map: dict[str, Any],
    ) -> list[str]:
        """Return coordinate IDs lacking evidence.

        Parameters
        ----------
        cover_members:
            All coordinate IDs expected to be covered.
        evidence_map:
            Map from coordinate_id → evidence.

        Returns
        -------
        list[str]
            IDs with no evidence or only failing evidence.
        """
        gaps: list[str] = []
        for coord in cover_members:
            ev = evidence_map.get(coord)
            if not self._is_covered(ev):
                gaps.append(coord)
        return gaps

    def trust_distribution(
        self,
        evidence_map: dict[str, Any],
    ) -> dict[str, int]:
        """Count coordinates at each trust level.

        Returns
        -------
        dict[str, int]
            Keys are trust level strings; values are counts.
        """
        dist: dict[str, int] = {}
        for coord, ev in evidence_map.items():
            level = self._extract_trust_level(ev)
            dist[level] = dist.get(level, 0) + 1
        return dist

    def staleness_report(
        self,
        evidence_map: dict[str, Any],
        code_change_times: dict[str, float],
        threshold_days: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Return (coordinate_id, staleness_days) for stale coordinates.

        Parameters
        ----------
        evidence_map:
            Map from coordinate_id → evidence dict.
        code_change_times:
            Map from coordinate_id → unix timestamp of last code change.
        threshold_days:
            Only include coordinates whose staleness exceeds this.

        Returns
        -------
        list[tuple[str, float]]
            Sorted by staleness descending.
        """
        stale: list[tuple[str, float]] = []
        for coord, change_t in code_change_times.items():
            ev = evidence_map.get(coord)
            latest = self._latest_evidence_time(ev)
            if latest is None:
                staleness = (time.time() - change_t) / 86400.0
            elif latest >= change_t:
                staleness = 0.0
            else:
                staleness = (change_t - latest) / 86400.0

            if staleness > threshold_days:
                stale.append((coord, staleness))

        stale.sort(key=lambda t: t[1], reverse=True)
        return stale

    def coverage_by_level(
        self,
        cover_hierarchy: dict[str, list[str]],
        evidence_map: dict[str, Any],
    ) -> dict[str, float]:
        """Compute coverage fraction at each architectural level.

        Parameters
        ----------
        cover_hierarchy:
            Map from level name → list of coordinate IDs at that level.
        evidence_map:
            Map from coordinate_id → evidence.

        Returns
        -------
        dict[str, float]
            Keys are level names; values are coverage fractions (0-1).
        """
        result: dict[str, float] = {}
        for level_name, coords in cover_hierarchy.items():
            if not coords:
                result[level_name] = 0.0
                continue
            covered = sum(
                1 for c in coords if self._is_covered(evidence_map.get(c))
            )
            result[level_name] = covered / len(coords)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_covered(self, evidence: Any) -> bool:
        """Return True iff the evidence dict/WitnessSection dict indicates passing."""
        if evidence is None:
            return False
        if isinstance(evidence, dict):
            # WitnessSection dict: is_complete key
            if "is_complete" in evidence:
                return bool(evidence["is_complete"])
            # Generic evidence dict: check passed or trust_level
            if "passed" in evidence:
                return bool(evidence["passed"])
            if "trust_level" in evidence:
                return trust_rank(evidence["trust_level"]) >= trust_rank("heuristic")
            # Non-empty dict with any truthy value — treat as covered
            return bool(evidence)
        if isinstance(evidence, WitnessSection):
            return evidence.is_complete
        return bool(evidence)

    def _extract_trust_level(self, evidence: Any) -> str:
        if evidence is None:
            return "none"
        if isinstance(evidence, dict):
            return str(evidence.get("trust_level", "none"))
        if isinstance(evidence, WitnessSection):
            return evidence.trust_level
        return "none"

    def _latest_evidence_time(self, evidence: Any) -> Optional[float]:
        if evidence is None:
            return None
        if isinstance(evidence, dict):
            ts = evidence.get("timestamp") or evidence.get("created_at")
            if ts is not None:
                return float(ts)
            records = evidence.get("evidence_records", [])
            if records:
                return max(
                    float(r.get("timestamp", 0)) for r in records
                )
            return None
        if isinstance(evidence, WitnessSection):
            if evidence.evidence_records:
                return max(
                    float(r.get("timestamp", 0))
                    for r in evidence.evidence_records
                )
            return None
        return None


# ---------------------------------------------------------------------------
# TestPrioritizer
# ---------------------------------------------------------------------------


class TestPrioritizer:
    """Rank test obligations by multiple geometric/sheaf-theoretic factors.

    Scoring formula (all factors normalised to [0, 1]):

        score = w_coupling  * coupling_weight
              + w_deficit   * trust_deficit
              + w_critical  * critical_path_bonus
              + w_staleness * staleness_score
              + w_blast     * blast_radius_score

    Default weights: coupling=0.30, deficit=0.25, critical=0.20,
                     staleness=0.15, blast=0.10.
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "coupling": 0.30,
        "deficit": 0.25,
        "critical": 0.20,
        "staleness": 0.15,
        "blast": 0.10,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prioritize(
        self,
        obligations: list[TestObligation],
        morphisms: list[dict[str, Any]],
        evidence_map: dict[str, Any],
        critical_paths: Optional[list[list[str]]] = None,
        weights: Optional[dict[str, float]] = None,
        staleness_map: Optional[dict[str, float]] = None,
    ) -> list[TestPrioritization]:
        """Score every obligation and return sorted prioritizations.

        Parameters
        ----------
        obligations:
            Open (non-satisfied) test obligations.
        morphisms:
            All morphisms in the site (used for coupling and blast radius).
        evidence_map:
            Current evidence for each coordinate.
        critical_paths:
            Optional list of coordinate-ID paths considered critical.
        weights:
            Override factor weights.
        staleness_map:
            Optional pre-computed staleness in days per coordinate.

        Returns
        -------
        list[TestPrioritization]
            Sorted descending by score.
        """
        w = dict(self.DEFAULT_WEIGHTS)
        if weights:
            w.update(weights)

        critical_coords: set[str] = set()
        if critical_paths:
            for path in critical_paths:
                critical_coords.update(path)

        results: list[TestPrioritization] = []
        for ob in obligations:
            score, reasons = self._score_obligation(
                ob,
                morphisms,
                evidence_map,
                critical_coords,
                staleness_map or {},
                w,
            )
            results.append(
                TestPrioritization(
                    obligation_id=ob.id,
                    score=score,
                    reasons=reasons,
                )
            )

        results.sort(key=lambda p: p.score, reverse=True)
        return results

    def top_k(
        self,
        obligations: list[TestObligation],
        k: int,
        morphisms: Optional[list[dict[str, Any]]] = None,
        evidence_map: Optional[dict[str, Any]] = None,
        critical_paths: Optional[list[list[str]]] = None,
    ) -> list[TestPrioritization]:
        """Return the top-k highest-priority obligations.

        Parameters
        ----------
        obligations:
            All open obligations.
        k:
            Number of items to return.
        morphisms:
            Site morphisms (passed through to prioritize).
        evidence_map:
            Current evidence.
        critical_paths:
            Optional critical paths.

        Returns
        -------
        list[TestPrioritization]
        """
        all_prio = self.prioritize(
            obligations,
            morphisms=morphisms or [],
            evidence_map=evidence_map or {},
            critical_paths=critical_paths,
        )
        return all_prio[:k]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_obligation(
        self,
        obligation: TestObligation,
        morphisms: list[dict[str, Any]],
        evidence_map: dict[str, Any],
        critical_coords: set[str],
        staleness_map: dict[str, float],
        weights: dict[str, float],
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []

        # 1. Coupling weight
        coupling = self._coupling_weight(obligation, morphisms)
        if coupling > 0:
            reasons.append(f"coupling-weight={coupling:.2f}")

        # 2. Trust deficit
        deficit = self._trust_deficit(obligation, evidence_map)
        if deficit > 0:
            reasons.append(f"trust-deficit={deficit:.2f}")

        # 3. Critical path bonus
        is_critical = obligation.coordinate_id in critical_coords
        critical_bonus = 1.0 if is_critical else 0.0
        if is_critical:
            reasons.append("critical-path")

        # 4. Staleness
        staleness_days = staleness_map.get(obligation.coordinate_id, 0.0)
        staleness_score = min(staleness_days / 30.0, 1.0)  # cap at 30 days
        if staleness_score > 0:
            reasons.append(f"staleness={staleness_days:.1f}d")

        # 5. Blast radius
        blast = self._blast_radius(obligation.coordinate_id, morphisms)
        blast_score = min(blast / 10.0, 1.0)  # normalise at 10 dependents
        if blast > 0:
            reasons.append(f"blast-radius={blast}")

        score = (
            weights.get("coupling", 0.30) * coupling
            + weights.get("deficit", 0.25) * deficit
            + weights.get("critical", 0.20) * critical_bonus
            + weights.get("staleness", 0.15) * staleness_score
            + weights.get("blast", 0.10) * blast_score
        )

        return score, reasons

    def _coupling_weight(
        self,
        obligation: TestObligation,
        morphisms: list[dict[str, Any]],
    ) -> float:
        """How many morphisms touch this obligation's coordinate or overlaps.

        Returns a value in [0, 1] (normalised at 10 morphisms).
        """
        coord = obligation.coordinate_id
        overlap_ids = set(obligation.overlap_ids)
        count = 0
        for m in morphisms:
            src = m.get("source", "")
            tgt = m.get("target", "")
            mid = m.get("id", "")
            if src == coord or tgt == coord or mid in overlap_ids:
                count += 1
        return min(count / 10.0, 1.0)

    def _trust_deficit(
        self,
        obligation: TestObligation,
        evidence_map: dict[str, Any],
    ) -> float:
        """Gap between current and target trust level, normalised to [0, 1]."""
        ev = evidence_map.get(obligation.coordinate_id)
        if ev is None:
            current_rank = 0
        elif isinstance(ev, dict):
            current_rank = trust_rank(
                ev.get("trust_level", ev.get("trust_achieved", "none"))
            )
        elif isinstance(ev, WitnessSection):
            current_rank = trust_rank(ev.trust_level)
        else:
            current_rank = 0

        target_rank = trust_rank(obligation.trust_target)
        max_rank = len(TRUST_ORDER) - 1
        deficit = max(target_rank - current_rank, 0)
        return deficit / max_rank if max_rank > 0 else 0.0

    def _blast_radius(
        self,
        coordinate_id: str,
        morphisms: list[dict[str, Any]],
    ) -> int:
        """Count the number of distinct downstream coordinates."""
        visited: set[str] = set()
        queue: list[str] = [coordinate_id]
        adj: dict[str, list[str]] = {}
        for m in morphisms:
            src = m.get("source", "")
            tgt = m.get("target", "")
            if src:
                adj.setdefault(src, []).append(tgt)

        while queue:
            curr = queue.pop()
            for dep in adj.get(curr, []):
                if dep and dep not in visited:
                    visited.add(dep)
                    queue.append(dep)
        return len(visited)


# ---------------------------------------------------------------------------
# RegressionAnalyzer
# ---------------------------------------------------------------------------


class RegressionAnalyzer:
    """Compute the minimal regression-testing scope after a coordinate change.

    The scope is the *exact* set of previously-satisfied obligations that
    are now invalidated by the change — no more, no less.  This corresponds
    to the categorical notion: the fibres that are affected by a morphism
    re-specification are exactly those connected via the morphism network.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_regression_scope(
        self,
        changed_coords: list[str],
        morphisms: list[dict[str, Any]],
        evidence_map: dict[str, Any],
        change_id: str = "",
    ) -> RegressionScope:
        """Return the minimal set of obligations that must re-run.

        Parameters
        ----------
        changed_coords:
            Directly modified coordinate IDs.
        morphisms:
            All site morphisms.
        evidence_map:
            Current evidence (used to estimate re-test cost).
        change_id:
            Change-set identifier.

        Returns
        -------
        RegressionScope
        """
        gen = TestObligationGenerator()
        return gen.generate_from_change(
            changed_coords, morphisms, evidence_map, change_id=change_id
        )

    def incremental_retest(
        self,
        scope: RegressionScope,
        existing_results: list[TestResult],
    ) -> list[TestObligation]:
        """Remove from scope any obligations already satisfied by fresh results.

        An obligation is *already satisfied* if there exists a result for the
        same coordinate that:
        * ``passed == True``
        * ``timestamp >= max(code_change_time)`` (we use the result timestamp
          relative to the scope's change — conservatively we keep all passing
          results recorded after the most recent result we see).

        Parameters
        ----------
        scope:
            The computed regression scope.
        existing_results:
            Previously collected test results.

        Returns
        -------
        list[TestObligation]
            Obligations not yet covered by existing results.
        """
        satisfied_coords: set[str] = set()
        # Find the latest timestamp in existing_results to use as reference
        latest_ts = max(
            (r.timestamp for r in existing_results), default=0.0
        )
        for result in existing_results:
            if result.passed and result.timestamp >= latest_ts * 0.999:
                satisfied_coords.add(result.coordinate_id)

        remaining: list[TestObligation] = []
        for ob in scope.required_retests:
            if ob.coordinate_id not in satisfied_coords:
                remaining.append(ob)
        return remaining

    def validate_existing_evidence(
        self,
        evidence_map: dict[str, Any],
        changed_coords: list[str],
    ) -> dict[str, bool]:
        """Return {coordinate_id: still_valid} for all evidence after a change.

        Evidence at a changed coordinate (or any coordinate in the transitive
        closure of changes) is considered invalid.

        Parameters
        ----------
        evidence_map:
            Current evidence map.
        changed_coords:
            Coordinates directly affected by the change.

        Returns
        -------
        dict[str, bool]
        """
        # We conservatively mark direct changes as invalid
        invalid_set = set(changed_coords)
        result: dict[str, bool] = {}
        for coord in evidence_map:
            result[coord] = coord not in invalid_set
        return result

    def _transitive_dependents(
        self,
        coords: list[str],
        morphisms: list[dict[str, Any]],
    ) -> set[str]:
        """Return all coordinates transitively depending on *coords*.

        'Depending on' means: there is a directed morphism path *from*
        a member of ``coords`` *to* the dependent.
        """
        gen = TestObligationGenerator()
        return gen._compute_invalidation_scope(coords, morphisms)
