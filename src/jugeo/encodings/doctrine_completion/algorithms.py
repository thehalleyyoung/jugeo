"""
Core algorithms for the doctrine_completion encoding package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It implements the core algorithms used for doctrine checking: grounding, gap
finding, coverage computation, evidence synthesis, claim propagation, doctrine
minimization, incremental checking, and risk assessment.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any, Optional

from .models import (
    DoctrineStatement,
    ImplementationEvidence,
    CompletenessCheck,
    DoctrineGap,
    GapSeverity,
    EvidenceKind,
    StatementStatus,
    ClaimType,
)
from .implementation_evidence import (
    EvidenceAggregator,
    ConfidenceEstimator,
    EvidenceChain,
)
from .completeness import (
    DoctrineGraph,
    CompletenessMetrics,
    CompletionStrategy,
    CompletenessAnalyzer,
)

__all__ = [
    "GroundingAlgorithm",
    "GapFindingAlgorithm",
    "CoverageComputationAlgorithm",
    "EvidenceSynthesisAlgorithm",
    "ClaimPropagationAlgorithm",
    "DoctrineMinimizationAlgorithm",
    "IncrementalCheckAlgorithm",
    "RiskAssessmentAlgorithm",
]


# ---------------------------------------------------------------------------
# GroundingAlgorithm
# ---------------------------------------------------------------------------


class GroundingAlgorithm:
    """Algorithm for determining whether doctrine statements are grounded.

    The grounding algorithm evaluates a DoctrineStatement against a list
    of ImplementationEvidence items and computes a grounding result that
    includes a score, status determination, and explanation.

    Attributes:
        confidence_threshold: Minimum per-item confidence to count as
            satisfying a required evidence kind.
    """

    def __init__(self, confidence_threshold: float = 0.7) -> None:
        """Initialise the grounding algorithm with a confidence threshold.

        Args:
            confidence_threshold: Minimum confidence required for evidence
                to count towards satisfying a requirement (default 0.7).
        """
        self.confidence_threshold = confidence_threshold
        self._algo_id: str = str(uuid.uuid4())
        self._estimator = ConfidenceEstimator()
        self._aggregator = EvidenceAggregator()

    def ground(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> dict[str, Any]:
        """Ground a single statement against available evidences.

        Computes the grounding score and determines whether the statement
        is fully grounded, partially grounded, or ungrounded.

        Args:
            statement: The DoctrineStatement to ground.
            evidences: Available evidence items for this statement.

        Returns:
            Dictionary with keys: statement_id, score, status, satisfied_kinds,
            missing_kinds, evidence_count, explanation.
        """
        score = self.compute_grounding_score(statement, evidences)
        is_full = self.is_fully_grounded(statement, evidences)

        available_kinds = {
            ev.evidence_kind for ev in evidences
            if ev.confidence >= self.confidence_threshold
        }
        required_kinds = set(statement.required_evidence_kinds)
        satisfied = required_kinds & available_kinds
        missing = required_kinds - available_kinds

        if is_full:
            status = StatementStatus.COMPLETE.value
            explanation = f"All {len(required_kinds)} required evidence kinds satisfied."
        elif satisfied:
            status = StatementStatus.PARTIAL.value
            explanation = (
                f"{len(satisfied)}/{len(required_kinds)} required kinds satisfied; "
                f"missing: {[k.value for k in missing]}"
            )
        else:
            status = StatementStatus.UNGROUNDED.value
            explanation = f"No required evidence kinds satisfied for statement {statement.statement_id[:8]}."

        return {
            "statement_id": statement.statement_id,
            "score": score,
            "status": status,
            "satisfied_kinds": [k.value for k in satisfied],
            "missing_kinds": [k.value for k in missing],
            "evidence_count": len(evidences),
            "explanation": explanation,
            "grounded_at": time.time(),
        }

    def ground_batch(
        self,
        pairs: list[tuple[DoctrineStatement, list[ImplementationEvidence]]],
    ) -> list[dict[str, Any]]:
        """Ground a batch of statement-evidence pairs.

        Args:
            pairs: List of (statement, evidences) tuples.

        Returns:
            List of grounding result dicts, in the same order.
        """
        return [self.ground(stmt, evs) for stmt, evs in pairs]

    def compute_grounding_score(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> float:
        """Compute a grounding score in [0.0, 1.0] for a statement.

        The score is the fraction of required evidence kinds that are
        satisfied by at least one evidence item with confidence >=
        confidence_threshold, weighted by the average confidence of
        satisfying items.

        Args:
            statement: The DoctrineStatement to score.
            evidences: Available evidence items.

        Returns:
            Grounding score in [0.0, 1.0].
        """
        required = set(statement.required_evidence_kinds)
        if not required:
            return 1.0
        kind_to_best: dict[EvidenceKind, float] = {}
        for ev in evidences:
            k = ev.evidence_kind
            if k in required and ev.confidence >= self.confidence_threshold:
                existing = kind_to_best.get(k, 0.0)
                kind_to_best[k] = max(existing, ev.confidence)
        if not kind_to_best:
            return 0.0
        fraction_satisfied = len(kind_to_best) / len(required)
        avg_confidence = sum(kind_to_best.values()) / len(kind_to_best)
        return fraction_satisfied * avg_confidence

    def is_fully_grounded(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> bool:
        """Return True if every required kind is covered by sufficient evidence.

        Args:
            statement: The statement to check.
            evidences: Available evidence items.

        Returns:
            True if all required kinds are satisfied.
        """
        required = set(statement.required_evidence_kinds)
        if not required:
            return True
        satisfied = {
            ev.evidence_kind
            for ev in evidences
            if ev.confidence >= self.confidence_threshold
        }
        return required.issubset(satisfied)


# ---------------------------------------------------------------------------
# GapFindingAlgorithm
# ---------------------------------------------------------------------------


class GapFindingAlgorithm:
    """Algorithm for finding evidence gaps in doctrine statements.

    GapFindingAlgorithm analyses statements against available evidence
    and creates DoctrineGap records for any unsatisfied requirements.
    It can also classify, deduplicate, and batch-process gaps.
    """

    def __init__(self) -> None:
        """Initialise the gap finding algorithm.

        Uses a GroundingAlgorithm internally to determine satisfied kinds.
        """
        self._grounder = GroundingAlgorithm()
        self._algo_id: str = str(uuid.uuid4())

    def find_gaps(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> list[DoctrineGap]:
        """Find all evidence gaps for a single statement.

        Creates one DoctrineGap for each unsatisfied evidence kind.

        Args:
            statement: The statement to check for gaps.
            evidences: Currently available evidence.

        Returns:
            List of DoctrineGap instances (empty if fully grounded).
        """
        available_kinds = [ev.evidence_kind for ev in evidences]
        missing = statement.get_gaps(available_kinds)
        if not missing:
            return []

        # Determine severity based on claim type and count of missing kinds
        severity = self._determine_severity(statement, missing)
        gap = DoctrineGap.create(
            statement_id=statement.statement_id,
            missing_kinds=missing,
            severity=severity,
            description=(
                f"Statement '{statement.claim_text[:40]}' is missing "
                f"{len(missing)} evidence kind(s): "
                f"{[k.value for k in missing]}"
            ),
            suggested_fix=(
                f"Collect the following evidence kinds: "
                f"{[k.value for k in missing]}"
            ),
        )
        return [gap]

    def find_all_gaps(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[DoctrineGap]:
        """Find gaps across all statements.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Concatenated list of all DoctrineGap instances found.
        """
        all_gaps: list[DoctrineGap] = []
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            gaps = self.find_gaps(stmt, evs)
            all_gaps.extend(gaps)
        return all_gaps

    def classify_gap(
        self, gap: DoctrineGap, context: dict[str, Any]
    ) -> GapSeverity:
        """Classify/refine the severity of a gap given additional context.

        Context keys:
          - 'is_critical_path': bool — elevates severity to at least CRITICAL
          - 'dependent_count': int — many dependents -> higher severity
          - 'age_days': float — old gaps escalate severity

        Args:
            gap: The gap to reclassify.
            context: Context dictionary with optional hints.

        Returns:
            The (possibly escalated) GapSeverity.
        """
        severity = gap.gap_severity
        if context.get("is_critical_path", False):
            if severity not in (GapSeverity.CRITICAL, GapSeverity.BLOCKING):
                severity = GapSeverity.CRITICAL
        dep_count = context.get("dependent_count", 0)
        if dep_count >= 5 and severity == GapSeverity.MINOR:
            severity = GapSeverity.MODERATE
        elif dep_count >= 10:
            severity = GapSeverity.CRITICAL
        age_days = context.get("age_days", 0.0)
        if age_days >= 30.0 and severity == GapSeverity.MINOR:
            severity = GapSeverity.MODERATE
        elif age_days >= 60.0 and severity == GapSeverity.MODERATE:
            severity = GapSeverity.CRITICAL
        return severity

    def deduplicate_gaps(
        self, gaps: list[DoctrineGap]
    ) -> list[DoctrineGap]:
        """Deduplicate gaps by statement_id, keeping the most severe.

        If multiple gaps exist for the same statement, retains the one
        with the highest severity score.

        Args:
            gaps: List of DoctrineGap instances, possibly with duplicates.

        Returns:
            Deduplicated list of DoctrineGap instances.
        """
        by_stmt: dict[str, DoctrineGap] = {}
        for gap in gaps:
            sid = gap.statement_id
            if sid not in by_stmt:
                by_stmt[sid] = gap
            else:
                existing = by_stmt[sid]
                if gap.compute_severity_score() > existing.compute_severity_score():
                    by_stmt[sid] = gap
        return list(by_stmt.values())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _determine_severity(
        self,
        statement: DoctrineStatement,
        missing_kinds: list[EvidenceKind],
    ) -> GapSeverity:
        """Heuristically assign gap severity from statement type and missing count.

        STRUCTURAL/BEHAVIORAL + many missing -> CRITICAL
        SEMANTIC + few missing -> MINOR
        Default -> MODERATE

        Args:
            statement: The statement with the gap.
            missing_kinds: List of missing evidence kinds.

        Returns:
            Inferred GapSeverity.
        """
        n_missing = len(missing_kinds)
        if statement.claim_type in (ClaimType.STRUCTURAL, ClaimType.BEHAVIORAL):
            if n_missing >= 3:
                return GapSeverity.CRITICAL
            if n_missing >= 2:
                return GapSeverity.MODERATE
            return GapSeverity.MINOR
        if statement.claim_type == ClaimType.SEMANTIC:
            return GapSeverity.MINOR if n_missing <= 1 else GapSeverity.MODERATE
        if n_missing >= 3:
            return GapSeverity.CRITICAL
        if n_missing >= 2:
            return GapSeverity.MODERATE
        return GapSeverity.MINOR


# ---------------------------------------------------------------------------
# CoverageComputationAlgorithm
# ---------------------------------------------------------------------------


class CoverageComputationAlgorithm:
    """Algorithm for computing doctrine coverage metrics.

    CoverageComputationAlgorithm offers multiple coverage computation
    methods: simple fraction, weighted, per-type, and delta.
    """

    def __init__(self) -> None:
        """Initialise the coverage algorithm.

        Uses a GroundingAlgorithm internally for per-statement assessment.
        """
        self._grounder = GroundingAlgorithm()
        self._algo_id: str = str(uuid.uuid4())

    def compute(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> float:
        """Compute simple coverage fraction.

        Coverage = number of fully-grounded statements / total statements.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Coverage fraction in [0.0, 1.0].
        """
        if not statements:
            return 0.0
        grounded_count = 0
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            if self._grounder.is_fully_grounded(stmt, evs):
                grounded_count += 1
        return grounded_count / len(statements)

    def compute_weighted(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
        weights: dict[str, float],
    ) -> float:
        """Compute weighted coverage where each statement has a weight.

        Weights are looked up by statement_id; missing weights default to 1.0.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.
            weights: Dictionary mapping statement_id to weight.

        Returns:
            Weighted coverage in [0.0, 1.0].
        """
        if not statements:
            return 0.0
        total_weight = 0.0
        weighted_grounded = 0.0
        for stmt in statements:
            w = weights.get(stmt.statement_id, 1.0)
            total_weight += w
            evs = evidence_map.get(stmt.statement_id, [])
            if self._grounder.is_fully_grounded(stmt, evs):
                weighted_grounded += w
        if total_weight == 0.0:
            return 0.0
        return weighted_grounded / total_weight

    def compute_by_type(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> dict[str, float]:
        """Compute coverage separately for each ClaimType.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Dictionary mapping claim_type.value to coverage fraction.
        """
        by_type: dict[str, list[DoctrineStatement]] = {}
        for stmt in statements:
            by_type.setdefault(stmt.claim_type.value, []).append(stmt)
        return {
            ctype: self.compute(stmts, evidence_map)
            for ctype, stmts in by_type.items()
        }

    def compute_delta(self, before: float, after: float) -> float:
        """Compute the coverage delta between two snapshots.

        Args:
            before: Coverage fraction before a change.
            after: Coverage fraction after a change.

        Returns:
            Signed delta (can be negative).
        """
        return after - before


# ---------------------------------------------------------------------------
# EvidenceSynthesisAlgorithm
# ---------------------------------------------------------------------------


class EvidenceSynthesisAlgorithm:
    """Algorithm for synthesising multiple evidence items into a single item.

    EvidenceSynthesisAlgorithm merges evidence items, computes synthetic
    confidence, and can merge entire evidence maps.
    """

    def __init__(self) -> None:
        """Initialise the synthesis algorithm.

        Uses EvidenceAggregator internally.
        """
        self._aggregator = EvidenceAggregator()
        self._algo_id: str = str(uuid.uuid4())

    def synthesize(
        self, evidences: list[ImplementationEvidence]
    ) -> ImplementationEvidence:
        """Synthesise a list of evidence items into a single item.

        The synthetic item has:
        - evidence_kind from the most-frequent kind
        - confidence = geometric mean of confidences
        - grounding_depth = max depth
        - copilot_assisted = True if any item is copilot-assisted

        Args:
            evidences: List of evidence items to synthesise.

        Returns:
            A new synthetic ImplementationEvidence.

        Raises:
            ValueError: If evidences is empty.
        """
        if not evidences:
            raise ValueError("Cannot synthesise an empty list of evidence")
        synthetic_confidence = self.compute_synthesis_confidence(evidences)
        max_depth = max(ev.grounding_depth for ev in evidences)
        any_copilot = any(ev.copilot_assisted for ev in evidences)
        # Most frequent kind
        kind_counts: dict[EvidenceKind, int] = {}
        for ev in evidences:
            kind_counts[ev.evidence_kind] = kind_counts.get(ev.evidence_kind, 0) + 1
        dominant_kind = max(kind_counts, key=lambda k: kind_counts[k])
        statement_id = evidences[0].statement_id
        authors = "+".join(sorted({ev.author for ev in evidences}))
        artifact_refs = [ev.artifact_ref for ev in evidences]
        return ImplementationEvidence.create(
            statement_id=statement_id,
            evidence_kind=dominant_kind,
            artifact_ref=f"synthetic://{','.join(artifact_refs[:3])}",
            confidence=synthetic_confidence,
            grounding_depth=max_depth,
            author=f"synthesis:{authors}",
            copilot_assisted=any_copilot,
            metadata={
                "synthesis_count": len(evidences),
                "synthesized_at": time.time(),
                "original_ids": [ev.evidence_id for ev in evidences],
            },
        )

    def synthesize_by_kind(
        self, evidences: list[ImplementationEvidence]
    ) -> dict[EvidenceKind, ImplementationEvidence]:
        """Synthesise evidence separately per evidence kind.

        Args:
            evidences: List of evidence items.

        Returns:
            Dictionary mapping EvidenceKind to a synthetic item for that kind.
        """
        by_kind: dict[EvidenceKind, list[ImplementationEvidence]] = {}
        for ev in evidences:
            by_kind.setdefault(ev.evidence_kind, []).append(ev)
        return {kind: self.synthesize(items) for kind, items in by_kind.items()}

    def compute_synthesis_confidence(
        self, evidences: list[ImplementationEvidence]
    ) -> float:
        """Compute the geometric mean confidence for a set of evidence items.

        The geometric mean is used so that low-confidence items pull the
        aggregate down more than the arithmetic mean would.

        Args:
            evidences: List of evidence items.

        Returns:
            Geometric mean confidence in [0.0, 1.0].
        """
        if not evidences:
            return 0.0
        log_sum = sum(math.log(max(ev.confidence, 1e-9)) for ev in evidences)
        return math.exp(log_sum / len(evidences))

    def merge_evidence_maps(
        self,
        maps: list[dict[str, list[ImplementationEvidence]]],
    ) -> dict[str, list[ImplementationEvidence]]:
        """Merge multiple evidence maps into a single combined map.

        For each statement_id, concatenates all evidence lists from all maps.

        Args:
            maps: List of evidence maps.

        Returns:
            Merged evidence map.
        """
        merged: dict[str, list[ImplementationEvidence]] = {}
        for evidence_map in maps:
            for sid, evs in evidence_map.items():
                merged.setdefault(sid, []).extend(evs)
        return merged


# ---------------------------------------------------------------------------
# ClaimPropagationAlgorithm
# ---------------------------------------------------------------------------


class ClaimPropagationAlgorithm:
    """Algorithm for propagating grounding through statement dependency graphs.

    If statement A depends on statement B and B is grounded, the algorithm
    can propagate some grounding confidence to A (or the reverse, depending
    on convention).  Here, we propagate grounded status downward through
    the dependency graph.
    """

    def __init__(self) -> None:
        """Initialise the propagation algorithm.

        Stateless beyond a unique ID.
        """
        self._algo_id: str = str(uuid.uuid4())

    def propagate(
        self,
        grounded_ids: set[str],
        dependency_graph: dict[str, list[str]],
    ) -> set[str]:
        """Propagate grounding from grounded_ids through the dependency graph.

        A statement becomes derivatively grounded if ALL of its dependencies
        are grounded (either directly or derivatively).

        Args:
            grounded_ids: Initially fully-grounded statement IDs.
            dependency_graph: Mapping from statement_id to list of dependency IDs.

        Returns:
            Expanded set of grounded statement IDs after propagation.
        """
        result = set(grounded_ids)
        changed = True
        while changed:
            changed = False
            for sid, deps in dependency_graph.items():
                if sid in result:
                    continue
                if deps and all(dep in result for dep in deps):
                    result.add(sid)
                    changed = True
        return result

    def propagate_all(
        self,
        evidence_map: dict[str, list[ImplementationEvidence]],
        graph: DoctrineGraph,
    ) -> dict[str, bool]:
        """Propagate grounding through a DoctrineGraph for all statements.

        First determines which statements are directly grounded (have at
        least one evidence item with confidence >= 0.7), then propagates
        through dependencies.

        Args:
            evidence_map: Mapping from statement_id to evidence list.
            graph: The DoctrineGraph of dependencies.

        Returns:
            Dictionary mapping statement_id to bool (is grounded).
        """
        # Directly grounded = has at least one high-confidence evidence item
        directly_grounded: set[str] = set()
        for sid, evs in evidence_map.items():
            if any(ev.confidence >= 0.7 for ev in evs):
                directly_grounded.add(sid)

        dep_graph = graph.to_adjacency_dict()
        propagated = self.propagate(directly_grounded, dep_graph)

        all_ids: set[str] = set(dep_graph.keys()) | set(evidence_map.keys())
        return {sid: (sid in propagated) for sid in all_ids}

    def compute_propagation_reach(
        self, statement_id: str, graph: DoctrineGraph
    ) -> int:
        """Compute how many statements are downstream of statement_id.

        The reach is the size of the set of statements that depend (directly
        or transitively) on statement_id.

        Args:
            statement_id: The starting statement.
            graph: The DoctrineGraph.

        Returns:
            Number of downstream statements.
        """
        return len(graph.reachable_from(statement_id))


# ---------------------------------------------------------------------------
# DoctrineMinimizationAlgorithm
# ---------------------------------------------------------------------------


class DoctrineMinimizationAlgorithm:
    """Algorithm for minimising a set of doctrine statements.

    A statement is redundant if it can be derived from other statements
    through the dependency graph (i.e., its grounding is implied by
    a subset of other statements).  Minimisation removes such statements.
    """

    def __init__(self) -> None:
        """Initialise the minimisation algorithm.

        Stateless beyond a unique ID.
        """
        self._algo_id: str = str(uuid.uuid4())

    def minimize(
        self,
        statements: list[DoctrineStatement],
        dependency_graph: dict[str, list[str]],
    ) -> list[DoctrineStatement]:
        """Remove redundant statements from the doctrine set.

        A statement is redundant if it has the same claim_type as another
        statement and all of its dependencies are a strict subset of that
        other statement's dependencies.

        Args:
            statements: All doctrine statements.
            dependency_graph: Dependency mapping.

        Returns:
            A reduced list with redundant statements removed.
        """
        redundant = set(self.find_redundant(statements, dependency_graph))
        return [s for s in statements if s.statement_id not in redundant]

    def find_redundant(
        self,
        statements: list[DoctrineStatement],
        dependency_graph: dict[str, list[str]],
    ) -> list[str]:
        """Identify statement IDs that are redundant.

        A statement S is redundant if there exists another statement T of
        the same claim_type such that:
        - The set of dependencies of S is a strict subset of T's dependencies
        - S's required_evidence_kinds are a subset of T's required_evidence_kinds

        Args:
            statements: All doctrine statements.
            dependency_graph: Dependency mapping.

        Returns:
            List of redundant statement IDs.
        """
        redundant: list[str] = []
        for i, s in enumerate(statements):
            s_deps = set(dependency_graph.get(s.statement_id, []))
            s_kinds = set(s.required_evidence_kinds)
            for j, t in enumerate(statements):
                if i == j:
                    continue
                if s.claim_type != t.claim_type:
                    continue
                t_deps = set(dependency_graph.get(t.statement_id, []))
                t_kinds = set(t.required_evidence_kinds)
                # S is subsumed by T if S's deps and kinds are subsets of T's
                if s_deps < t_deps and s_kinds <= t_kinds:
                    redundant.append(s.statement_id)
                    break
        return redundant

    def compute_minimality_score(
        self,
        statements: list[DoctrineStatement],
        dependency_graph: dict[str, list[str]],
    ) -> float:
        """Compute a minimality score for the current doctrine set.

        Score = 1.0 - (redundant_count / total_count).

        Args:
            statements: All doctrine statements.
            dependency_graph: Dependency mapping.

        Returns:
            Minimality score in [0.0, 1.0].
        """
        if not statements:
            return 1.0
        redundant = self.find_redundant(statements, dependency_graph)
        return 1.0 - len(redundant) / len(statements)


# ---------------------------------------------------------------------------
# IncrementalCheckAlgorithm
# ---------------------------------------------------------------------------


class IncrementalCheckAlgorithm:
    """Algorithm for incremental doctrine completeness checking.

    When new evidence is added, only the statements affected by that
    evidence need to be re-checked.  This algorithm identifies affected
    statements and merges updated checks with prior results.
    """

    def __init__(self) -> None:
        """Initialise the incremental check algorithm.

        Uses GroundingAlgorithm internally for per-statement checking.
        """
        self._grounder = GroundingAlgorithm()
        self._algo_id: str = str(uuid.uuid4())

    def check_incremental(
        self,
        new_evidence: list[ImplementationEvidence],
        all_statements: list[DoctrineStatement],
        prior_checks: list[CompletenessCheck],
    ) -> list[CompletenessCheck]:
        """Re-check only the statements affected by new evidence.

        Identifies affected statements, re-runs grounding for them, and
        merges the results with prior checks (updating affected entries).

        Args:
            new_evidence: Newly added evidence items.
            all_statements: All doctrine statements.
            prior_checks: Previously computed CompletenessCheck results.

        Returns:
            Updated list of CompletenessCheck results.
        """
        affected = set(self.affected_statements(new_evidence, all_statements))
        if not affected:
            return list(prior_checks)

        # Build evidence map from new evidence only (affected statements)
        new_ev_map: dict[str, list[ImplementationEvidence]] = {}
        for ev in new_evidence:
            new_ev_map.setdefault(ev.statement_id, []).append(ev)

        # Re-check affected statements
        updated_checks: list[CompletenessCheck] = []
        for stmt in all_statements:
            if stmt.statement_id not in affected:
                continue
            evs = new_ev_map.get(stmt.statement_id, [])
            available = [ev.evidence_kind for ev in evs]
            status = stmt.check_completeness(available)
            complete = 1 if status == StatementStatus.COMPLETE else 0
            partial = 1 if status == StatementStatus.PARTIAL else 0
            ungrounded = 1 if status == StatementStatus.UNGROUNDED else 0
            gaps = [stmt.statement_id] if status != StatementStatus.COMPLETE else []
            check = CompletenessCheck.create(
                total_statements=1,
                complete_count=complete,
                partial_count=partial,
                ungrounded_count=ungrounded,
                gap_list=gaps,
            )
            updated_checks.append(check)

        return self.merge_check_results(prior_checks, updated_checks)

    def affected_statements(
        self,
        new_evidence: list[ImplementationEvidence],
        all_statements: list[DoctrineStatement],
    ) -> list[str]:
        """Return IDs of statements affected by the new evidence.

        A statement is affected if any of the new evidence items has a
        matching statement_id.

        Args:
            new_evidence: Newly added evidence items.
            all_statements: All doctrine statements.

        Returns:
            List of affected statement IDs.
        """
        new_stmt_ids = {ev.statement_id for ev in new_evidence}
        return [s.statement_id for s in all_statements if s.statement_id in new_stmt_ids]

    def merge_check_results(
        self,
        prior: list[CompletenessCheck],
        updated: list[CompletenessCheck],
    ) -> list[CompletenessCheck]:
        """Merge updated checks into the prior check list.

        For each updated check, replaces the most recent prior check if
        one exists; otherwise appends.  Matching is done by check_id prefix
        collision avoidance — here we just replace the last entry or append.

        Args:
            prior: Previously computed checks.
            updated: Newly computed checks for affected statements.

        Returns:
            Merged list of CompletenessCheck results.
        """
        # Simple merge: build a combined list
        merged = list(prior)
        merged.extend(updated)
        # Keep only the most recent check per check_id prefix (dedup)
        seen_ids: set[str] = set()
        deduped: list[CompletenessCheck] = []
        for check in reversed(merged):
            if check.check_id not in seen_ids:
                deduped.append(check)
                seen_ids.add(check.check_id)
        deduped.reverse()
        return deduped


# ---------------------------------------------------------------------------
# RiskAssessmentAlgorithm
# ---------------------------------------------------------------------------


class RiskAssessmentAlgorithm:
    """Algorithm for risk assessment of ungrounded doctrine statements.

    Risk reflects the probability that a statement will fail verification
    multiplied by the impact of that failure.  High-risk statements should
    be prioritised for evidence collection.
    """

    def __init__(self) -> None:
        """Initialise the risk assessment algorithm.

        Uses GroundingAlgorithm internally.
        """
        self._grounder = GroundingAlgorithm()
        self._algo_id: str = str(uuid.uuid4())

    def assess_risk(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> dict[str, Any]:
        """Assess the risk for a single statement.

        Risk score = (1 - grounding_score) * impact_weight, where
        impact_weight depends on claim_type (STRUCTURAL/BEHAVIORAL = 1.0,
        RELATIONAL = 0.8, RESOURCE = 0.7, SEMANTIC = 0.5).

        Args:
            statement: The statement to assess.
            evidences: Available evidence items.

        Returns:
            Dictionary with: statement_id, risk_score, grounding_score,
            impact_weight, explanation.
        """
        risk_score = self.compute_risk_score(statement, evidences)
        grounding_score = self._grounder.compute_grounding_score(statement, evidences)
        impact_weight = self._impact_weight(statement)
        if risk_score >= 0.75:
            risk_level = "HIGH"
        elif risk_score >= 0.4:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"
        explanation = (
            f"Grounding score: {grounding_score:.3f}, "
            f"impact: {impact_weight:.2f}, "
            f"risk: {risk_score:.3f} ({risk_level})"
        )
        return {
            "statement_id": statement.statement_id,
            "risk_score": risk_score,
            "grounding_score": grounding_score,
            "impact_weight": impact_weight,
            "risk_level": risk_level,
            "explanation": explanation,
            "assessed_at": time.time(),
        }

    def assess_all(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[dict[str, Any]]:
        """Assess risk for all statements.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            List of risk assessment dicts.
        """
        return [
            self.assess_risk(stmt, evidence_map.get(stmt.statement_id, []))
            for stmt in statements
        ]

    def compute_risk_score(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> float:
        """Compute a normalised risk score in [0.0, 1.0].

        Risk = (1 - grounding_score) * impact_weight.

        Args:
            statement: The statement to score.
            evidences: Available evidence items.

        Returns:
            Risk score in [0.0, 1.0].
        """
        grounding = self._grounder.compute_grounding_score(statement, evidences)
        impact = self._impact_weight(statement)
        return max(0.0, min(1.0, (1.0 - grounding) * impact))

    def prioritize_by_risk(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[DoctrineStatement]:
        """Return statements sorted by descending risk score.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Statements ordered from highest to lowest risk.
        """
        scored = [
            (stmt, self.compute_risk_score(stmt, evidence_map.get(stmt.statement_id, [])))
            for stmt in statements
        ]
        scored.sort(key=lambda x: -x[1])
        return [stmt for stmt, _ in scored]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _impact_weight(self, statement: DoctrineStatement) -> float:
        """Return an impact weight based on claim_type.

        STRUCTURAL/BEHAVIORAL = 1.0 (highest impact),
        RELATIONAL = 0.8, RESOURCE = 0.7, SEMANTIC = 0.5.

        Args:
            statement: The statement.

        Returns:
            Impact weight float.
        """
        weights = {
            ClaimType.STRUCTURAL: 1.0,
            ClaimType.BEHAVIORAL: 1.0,
            ClaimType.RELATIONAL: 0.8,
            ClaimType.RESOURCE: 0.7,
            ClaimType.SEMANTIC: 0.5,
        }
        return weights.get(statement.claim_type, 0.8)
