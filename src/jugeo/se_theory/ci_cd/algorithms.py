"""CI/CD algorithms: pipeline planning, stage execution, certificate emission, incremental verification.

Implements the computational core of the CI/CD module:

* **PipelinePlanner** — determines which verification tasks to run, scoped
  incrementally to changed coordinates via BFS through the morphism graph.
* **StageExecutor** — executes verification tasks in a stage, checking that
  coordinates exist and meet trust requirements.
* **CertificateEmitter** — emits, verifies, and invalidates certificates
  that attest descent holds at a given trust level.
* **IncrementalVerifier** — end-to-end verification of a code change using
  all of the above, with optional caching.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from jugeo.se_theory.ci_cd.models import (
    Certificate,
    IncrementalScope,
    PipelineResult,
    PipelineStage,
    StageRequirement,
    StageResult,
    VerificationTask,
)


# ---------------------------------------------------------------------------
# Trust ordering helper
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = ["claim", "conjecture", "heuristic", "proof", "verified"]


def _trust_rank(level: str) -> int:
    try:
        return _TRUST_ORDER.index(level.lower().strip())
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# PipelinePlanner
# ---------------------------------------------------------------------------


class PipelinePlanner:
    """Determines which verification tasks to run for a set of changes."""

    def plan_incremental(
        self,
        changed_coords: list[str],
        morphisms: dict[str, list[str]],
        evidence: dict[str, dict[str, Any]],
        stage_requirements: list[StageRequirement],
    ) -> list[VerificationTask]:
        """Plan incremental verification: scope the change, then create tasks
        for each needed stage.

        Returns tasks sorted by priority descending.
        """
        scope = self.scope_for_change(changed_coords, morphisms)
        tasks: list[VerificationTask] = []
        for stage in scope.stages_needed:
            req = self._find_requirement(stage, stage_requirements)
            tasks.extend(self._tasks_for_stage(stage, scope, req))
        tasks.sort(key=lambda t: t.priority, reverse=True)
        return tasks

    def plan_full(
        self,
        all_coords: list[str],
        morphisms: dict[str, list[str]],
        evidence: dict[str, dict[str, Any]],
        stage_requirements: list[StageRequirement],
    ) -> list[VerificationTask]:
        """Full verification: create tasks covering all coordinates for all stages."""
        all_stages = [
            PipelineStage.PRE_COMMIT,
            PipelineStage.PRE_MERGE,
            PipelineStage.POST_MERGE,
            PipelineStage.RELEASE_GATE,
        ]
        scope = IncrementalScope(
            affected_coordinates=list(all_coords),
            stages_needed=all_stages,
            estimated_total_duration_s=len(all_coords) * 2.0,
        )
        tasks: list[VerificationTask] = []
        for stage in all_stages:
            req = self._find_requirement(stage, stage_requirements)
            tasks.extend(self._tasks_for_stage(stage, scope, req))
        tasks.sort(key=lambda t: t.priority, reverse=True)
        return tasks

    def scope_for_change(
        self,
        changed_coords: list[str],
        morphisms: dict[str, list[str]],
    ) -> IncrementalScope:
        """BFS to find all transitively affected coordinates."""
        affected: set[str] = set()
        queue = list(changed_coords)
        while queue:
            node = queue.pop(0)
            if node in affected:
                continue
            affected.add(node)
            for nbr in morphisms.get(node, []):
                if nbr not in affected:
                    queue.append(nbr)

        affected_list = sorted(affected)

        # Overlaps: coords that appear in morphism adjacency of affected
        overlap_ids: set[str] = set()
        for coord in affected_list:
            for nbr in morphisms.get(coord, []):
                key = f"{min(coord, nbr)}_{max(coord, nbr)}"
                overlap_ids.add(key)

        stages_needed = [
            PipelineStage.PRE_COMMIT,
            PipelineStage.PRE_MERGE,
            PipelineStage.POST_MERGE,
        ]

        return IncrementalScope(
            affected_coordinates=affected_list,
            affected_overlaps=sorted(overlap_ids),
            stages_needed=stages_needed,
            estimated_total_duration_s=len(affected_list) * 2.0,
        )

    def _tasks_for_stage(
        self,
        stage: PipelineStage,
        scope: IncrementalScope,
        requirement: Optional[StageRequirement],
    ) -> list[VerificationTask]:
        """Create one VerificationTask per batch of coordinates (chunks of 10)."""
        coords = scope.affected_coordinates
        trust_target = requirement.trust_minimum if requirement else "claim"
        chunk_size = 10
        tasks: list[VerificationTask] = []
        for i in range(0, max(len(coords), 1), chunk_size):
            chunk = coords[i : i + chunk_size]
            if not chunk:
                continue
            task = VerificationTask(
                stage=stage,
                coordinates=chunk,
                overlaps_to_check=scope.affected_overlaps,
                trust_target=trust_target,
                priority=1.0,
                estimated_duration_s=self._estimate_duration_for_chunk(chunk, scope),
            )
            tasks.append(task)
        return tasks

    def _estimate_duration(self, task: VerificationTask) -> float:
        """Return ``len(coordinates) * 1.0 + len(overlaps_to_check) * 0.5``."""
        return len(task.coordinates) * 1.0 + len(task.overlaps_to_check) * 0.5

    def _estimate_duration_for_chunk(
        self, chunk: list[str], scope: IncrementalScope
    ) -> float:
        return len(chunk) * 1.0 + len(scope.affected_overlaps) * 0.5

    def _find_requirement(
        self,
        stage: PipelineStage,
        requirements: list[StageRequirement],
    ) -> Optional[StageRequirement]:
        for req in requirements:
            if req.stage == stage:
                return req
        return None


# ---------------------------------------------------------------------------
# StageExecutor
# ---------------------------------------------------------------------------


class StageExecutor:
    """Executes verification tasks in a stage."""

    def execute_stage(
        self,
        tasks: list[VerificationTask],
        sections: dict[str, dict[str, Any]],
        propositions: list[str],
    ) -> StageResult:
        """Run each task, count pass/fail, collect obstructions."""
        if not tasks:
            return StageResult(passed=True)

        stage = tasks[0].stage
        total_run = 0
        total_passed = 0
        total_failed = 0
        all_obstructions: list[str] = []
        total_duration = 0.0

        # Track minimum trust achieved
        min_trust_rank = len(_TRUST_ORDER)
        total_coords = 0
        covered_coords = 0

        for task in tasks:
            passed, obstructions = self._run_task(task, sections, propositions)
            total_run += 1
            if passed:
                total_passed += 1
            else:
                total_failed += 1
                all_obstructions.extend(obstructions)

            for coord in task.coordinates:
                total_coords += 1
                section = sections.get(coord)
                if section:
                    covered_coords += 1
                    rank = _trust_rank(section.get("trust", "claim"))
                    if rank < min_trust_rank:
                        min_trust_rank = rank

            total_duration += task.estimated_duration_s

        if min_trust_rank >= len(_TRUST_ORDER) or min_trust_rank < 0:
            trust_achieved = "claim"
        else:
            trust_achieved = _TRUST_ORDER[min_trust_rank]

        coverage = covered_coords / max(total_coords, 1)

        return StageResult(
            stage=stage,
            tasks_run=total_run,
            tasks_passed=total_passed,
            tasks_failed=total_failed,
            obstructions_found=all_obstructions,
            trust_achieved=trust_achieved,
            coverage_achieved=coverage,
            duration_s=total_duration,
            passed=(total_failed == 0),
        )

    def _run_task(
        self,
        task: VerificationTask,
        sections: dict[str, dict[str, Any]],
        propositions: list[str],
    ) -> tuple[bool, list[str]]:
        """For each coord in task.coordinates:
        - missing in sections -> obstruction
        - trust level < trust_target -> obstruction

        Returns ``(passed, obstructions)``.
        """
        obstructions: list[str] = []
        for coord in task.coordinates:
            section = sections.get(coord)
            if section is None:
                obstructions.append(f"missing:{coord}")
                continue
            actual_trust = section.get("trust", "claim")
            if _trust_rank(actual_trust) < _trust_rank(task.trust_target):
                obstructions.append(f"low_trust:{coord}")
        return (len(obstructions) == 0, obstructions)

    def check_stage_requirements(
        self,
        result: StageResult,
        requirement: StageRequirement,
    ) -> bool:
        """Check that the stage result meets the requirement.

        * trust_achieved >= trust_minimum
        * coverage_achieved >= required_coverage
        * duration_s <= max_duration_s (warn but don't fail)
        """
        trust_ok = _trust_rank(result.trust_achieved) >= _trust_rank(
            requirement.trust_minimum
        )
        coverage_ok = result.coverage_achieved >= requirement.required_coverage
        return trust_ok and coverage_ok


# ---------------------------------------------------------------------------
# CertificateEmitter
# ---------------------------------------------------------------------------


class CertificateEmitter:
    """Emits, verifies, and invalidates descent certificates."""

    def __init__(self) -> None:
        self._invalidated: set[str] = set()

    def emit_certificate(
        self,
        pipeline_result: PipelineResult,
        trust_levels: dict[str, str],
        coverage: float,
        obligations: list[str],
    ) -> Certificate:
        """Emit a certificate if the pipeline passed.

        Raises ``ValueError`` if ``pipeline_result.overall_passed`` is False.
        """
        if not pipeline_result.overall_passed:
            raise ValueError("Cannot emit certificate: pipeline did not pass.")

        cert = Certificate(
            trust_levels=dict(trust_levels),
            coverage=coverage,
            residual_obligations=list(obligations),
        )
        # Compute signature over the certificate data
        cert_data = {
            "id": cert.id,
            "trust_levels": cert.trust_levels,
            "coverage": cert.coverage,
            "residual_obligations": cert.residual_obligations,
            "issued_at": cert.issued_at,
        }
        cert.signature = self._compute_signature(cert_data)
        return cert

    def verify_certificate(
        self,
        certificate: Certificate,
        trust_levels: dict[str, str],
        coverage: float,
    ) -> bool:
        """Verify a certificate's signature and that trust levels haven't degraded."""
        if certificate.id in self._invalidated:
            return False

        cert_data = {
            "id": certificate.id,
            "trust_levels": certificate.trust_levels,
            "coverage": certificate.coverage,
            "residual_obligations": certificate.residual_obligations,
            "issued_at": certificate.issued_at,
        }
        expected_sig = self._compute_signature(cert_data)
        if certificate.signature != expected_sig:
            return False

        # Check trust levels haven't degraded
        for coord, required_trust in certificate.trust_levels.items():
            current = trust_levels.get(coord, "claim")
            if _trust_rank(current) < _trust_rank(required_trust):
                return False

        return True

    def _compute_signature(self, certificate_data: dict[str, Any]) -> str:
        """SHA-256 hex digest of the JSON-serialised certificate data."""
        raw = json.dumps(certificate_data, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def invalidate_certificate(self, certificate_id: str, reason: str) -> None:
        """Track invalidated certificate IDs."""
        self._invalidated.add(certificate_id)

    def certificates_for_coordinate(
        self,
        coord_id: str,
        certificates: list[Certificate],
    ) -> list[Certificate]:
        """Return certificates where *coord_id* is in ``trust_levels`` keys."""
        return [c for c in certificates if coord_id in c.trust_levels]


# ---------------------------------------------------------------------------
# IncrementalVerifier
# ---------------------------------------------------------------------------


class IncrementalVerifier:
    """End-to-end incremental verification of a code change."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[bool, list[str]]] = {}
        self._planner = PipelinePlanner()
        self._executor = StageExecutor()
        self._emitter = CertificateEmitter()

    def verify_change(
        self,
        changed_coords: list[str],
        morphisms: dict[str, list[str]],
        evidence: dict[str, dict[str, Any]],
        requirements: list[StageRequirement],
        sections: Optional[dict[str, dict[str, Any]]] = None,
        propositions: Optional[list[str]] = None,
    ) -> PipelineResult:
        """Plan tasks, group by stage, execute each stage.

        If any stage fails, mark overall as failed.
        If all pass, emit certificate.
        """
        if sections is None:
            sections = {}
        if propositions is None:
            propositions = []

        tasks = self._planner.plan_incremental(
            changed_coords, morphisms, evidence, requirements
        )

        # Group tasks by stage
        stage_tasks: dict[PipelineStage, list[VerificationTask]] = {}
        for task in tasks:
            stage_tasks.setdefault(task.stage, []).append(task)

        scope = self._planner.scope_for_change(changed_coords, morphisms)
        needed_stages = self._select_stages(scope, requirements)

        stage_results: list[StageResult] = []
        all_passed = True
        blocking_issues: list[str] = []

        for stage in needed_stages:
            stage_task_list = stage_tasks.get(stage, [])
            result = self._executor.execute_stage(
                stage_task_list, sections, propositions
            )
            stage_results.append(result)
            if not result.passed:
                all_passed = False
                blocking_issues.extend(result.obstructions_found)

        pipeline_result = PipelineResult(
            stages=stage_results,
            overall_passed=all_passed,
            blocking_issues=blocking_issues,
        )

        if all_passed:
            # Collect trust levels from sections
            trust_levels: dict[str, str] = {}
            for coord in scope.affected_coordinates:
                section = sections.get(coord)
                if section:
                    trust_levels[coord] = section.get("trust", "claim")
            try:
                cert = self._emitter.emit_certificate(
                    pipeline_result, trust_levels, 1.0, []
                )
                pipeline_result.certificate_issued = True
                pipeline_result.certificate_id = cert.id
            except ValueError:
                pass

        return pipeline_result

    def _select_stages(
        self,
        scope: IncrementalScope,
        requirements: list[StageRequirement],
    ) -> list[PipelineStage]:
        """Return stages from scope.stages_needed that have matching requirements."""
        req_stages = {r.stage for r in requirements}
        return [s for s in scope.stages_needed if s in req_stages]

    def _cache_key(self, task: VerificationTask) -> str:
        """Hash of task.id + coordinates joined."""
        raw = task.id + "|" + ",".join(sorted(task.coordinates))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def with_cache(
        self, cache: dict[str, tuple[bool, list[str]]]
    ) -> "IncrementalVerifier":
        """Set the cache and return self for chaining."""
        self._cache = cache
        return self
