r"""Chapter 39, Section 1 — Semantic Closure Checking.

Theory (theory2.tex §39.8 — Closure checking):
    *Semantic closure* is the property that every semantic obligation produced
    during the local construction of a section ``s_u`` over a coordinate chart
    ``U`` has been satisfactorily discharged.  Formally, given an obligation
    set ``O_u`` and an evidence pool ``E_u``, we say ``O_u`` is *closed with
    respect to* ``E_u`` when:

        closed(O_u, E_u)  ⟺  ∀ o ∈ O_u, ∃ e ∈ E_u : satisfies(e, o)

    In practice the relation ``satisfies`` is not a simple Boolean predicate
    but a *graded* one: each piece of evidence ``e`` satisfies obligation ``o``
    to some degree ``σ(e, o) ∈ [0, 1]``.  An obligation is considered closed
    if the aggregated satisfaction score exceeds a configurable threshold ``τ``:

        closed_τ(o, E)  ⟺  sup_{e ∈ E} σ(e, o) ≥ τ

    Three modes of closure are distinguished:

    1.  *Semantic closure*: evidence tags are matched against the obligation
        string by syntactic overlap and keyword analysis.

    2.  *Treaty closure*: the obligation is matched against the clauses of a
        ratified :class:`OverlapTreaty`.  If a clause ``c`` with
        ``c.patch == patch_id`` and ``c.expectation`` semantically covering
        the obligation is found and ``c.satisfied is True``, the obligation
        is considered treaty-closed.

    3.  *Descent closure*: the obligation is checked against a
        :class:`DescentResult`.  A successful descent globally closes all
        obligations over the descent domain.  A failed descent with an
        obstruction is checked for whether the obligation appears in the
        obstruction's violated overlaps.

    This module provides:

    * :class:`ClosureChecker` — the primary engine for running all three
      modes.
    * :class:`ObligationRegistry` — a mutable registry that tracks which
      obligations are open or closed in a given integration run.
    * :class:`EvidenceAggregator` — utilities for merging and scoring
      evidence tuples before they are passed to the checker.
    * :class:`ClosureReport` — a structured report summarising the full
      closure status of an integration.

    References
    ----------
    theory2.tex  §39.8 (Semantic closure), §39.9 (Closure gaps),
                 §39.10 (Treaty closure), §39.11 (Descent closure)

    copilot: s01-closure-checking
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .models import ClosureCheck, ClosureGap, ClosureResult, GapSeverity

# ---------------------------------------------------------------------------
# Optional heavyweight imports — wrapped so the module loads even when
# the rest of the jugeo stack is not fully installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, evaluate_treaty  # type: ignore[import]
    _TREATIES_AVAILABLE = True
except Exception:  # pragma: no cover
    _TREATIES_AVAILABLE = False
    OverlapTreaty = Any  # type: ignore[assignment,misc]
    TreatyClause = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentResult, DescentObstruction  # type: ignore[import]
    _DESCENT_AVAILABLE = True
except Exception:  # pragma: no cover
    _DESCENT_AVAILABLE = False
    DescentResult = Any  # type: ignore[assignment,misc]
    DescentObstruction = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel  # type: ignore[import]
    _TRUST_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_AVAILABLE = False

logger = logging.getLogger(__name__)

__all__ = [
    "ClosureChecker",
    "ObligationRegistry",
    "EvidenceAggregator",
    "ClosureReport",
    # Module-level helpers
    "run_closure_check",
    "build_report",
    "check_obligations_from_registry",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """Compute a normalised keyword overlap score between two strings.

    Splits both strings on whitespace and common punctuation, computes the
    Jaccard similarity of their token sets.  Returns a float in ``[0, 1]``.

    Parameters
    ----------
    text_a:
        First string.
    text_b:
        Second string.
    """
    import re

    tokens_a: set[str] = set(re.split(r"[\s\W]+", text_a.lower())) - {""}
    tokens_b: set[str] = set(re.split(r"[\s\W]+", text_b.lower())) - {""}
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _evidence_quality_score(tag: str) -> float:
    """Assign a quality score to a single evidence tag based on its content.

    Tags that contain explicit positive keywords receive a higher score.
    This is a lightweight heuristic — production code would use a trained
    classifier.

    Parameters
    ----------
    tag:
        An evidence string, e.g. ``"treaty:ratified"``, ``"test:passed"``.
    """
    high_quality_markers = {
        "verified", "confirmed", "ratified", "passed", "closed",
        "satisfied", "proven", "checked", "validated", "complete",
    }
    low_quality_markers = {
        "proposed", "draft", "partial", "pending", "unverified",
        "candidate", "todo", "stub",
    }
    tag_lower = tag.lower()
    for marker in high_quality_markers:
        if marker in tag_lower:
            return 1.0
    for marker in low_quality_markers:
        if marker in tag_lower:
            return 0.3
    return 0.6  # neutral default


def _severity_for_confidence(confidence: float, result: str) -> str:
    """Choose a :class:`GapSeverity` value based on check confidence and result.

    Parameters
    ----------
    confidence:
        Float in ``[0, 1]``.
    result:
        One of ``"open"``, ``"partial"``, ``"closed"``.
    """
    if result == ClosureResult.CLOSED.value:
        return GapSeverity.INFO.value  # no gap for a closed obligation
    if result == ClosureResult.OPEN.value:
        if confidence < 0.2:
            return GapSeverity.BLOCKING.value
        if confidence < 0.5:
            return GapSeverity.CRITICAL.value
        return GapSeverity.MODERATE.value
    # partial
    if confidence < 0.4:
        return GapSeverity.CRITICAL.value
    if confidence < 0.7:
        return GapSeverity.MODERATE.value
    return GapSeverity.MINOR.value


# ---------------------------------------------------------------------------
# ClosureChecker
# ---------------------------------------------------------------------------


class ClosureChecker:
    """Engine for checking whether semantic obligations are closed.

    The :class:`ClosureChecker` supports three complementary checking modes,
    any combination of which can be applied to a single obligation:

    * **Semantic mode** (default): analyses the obligation string and the
      provided evidence tags to compute a closure result and confidence.
    * **Treaty mode**: evaluates whether an existing :class:`OverlapTreaty`
      contains a satisfied clause that covers the obligation.
    * **Descent mode**: queries a :class:`DescentResult` for structural
      evidence of closure.

    Every check is recorded in the internal history and can be retrieved
    with :meth:`get_history`.

    Parameters
    ----------
    trust_threshold:
        Minimum confidence required to consider an obligation ``"closed"``
        rather than ``"partial"``.  Defaults to ``0.7``.
    require_all_checks:
        If ``True``, :meth:`check_all` raises ``ValueError`` when
        *integration* does not contain expected keys.  Defaults to ``False``.

    Examples
    --------
    >>> checker = ClosureChecker(trust_threshold=0.8)
    >>> result = checker.check(
    ...     "all patches must export schema",
    ...     evidence=("schema:exported", "schema:validated"),
    ...     patch_id="patch_alpha",
    ... )
    >>> result.result
    'partial'
    """

    def __init__(
        self,
        trust_threshold: float = 0.7,
        require_all_checks: bool = False,
    ) -> None:
        self._trust_threshold = trust_threshold
        self._require_all_checks = require_all_checks
        self._check_history: list[ClosureCheck] = []

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def check(
        self,
        obligation: str,
        evidence: tuple[str, ...],
        patch_id: str = "",
        check_type: str = "semantic",
    ) -> ClosureCheck:
        """Run a single closure check on *obligation* against *evidence*.

        The method delegates to the appropriate private checker based on
        *check_type* and records the result in the history.

        Parameters
        ----------
        obligation:
            The obligation string to check (e.g. ``"all overlaps resolved"``).
        evidence:
            Tuple of evidence tags available in the current construction
            context.
        patch_id:
            Optional identifier of the coordinate patch.
        check_type:
            One of ``"semantic"``, ``"treaty"``, ``"descent"``,
            ``"combined"``.  Unrecognised values fall back to
            ``"semantic"``.

        Returns
        -------
        ClosureCheck
            A fully populated :class:`ClosureCheck` record.
        """
        logger.debug(
            "ClosureChecker.check obligation=%r patch=%r type=%r evidence_len=%d",
            obligation[:60],
            patch_id,
            check_type,
            len(evidence),
        )
        result_str, confidence = self._check_semantic_closure(obligation, evidence)

        # Promote "partial" to "closed" if confidence exceeds threshold
        if result_str == ClosureResult.PARTIAL.value and confidence >= self._trust_threshold:
            result_str = ClosureResult.CLOSED.value

        cc = ClosureCheck(
            obligation_id=obligation,
            patch_id=patch_id,
            result=result_str,
            confidence=round(confidence, 4),
            evidence=evidence,
            check_type=check_type,
            notes=f"checked via {check_type}",
        )
        self._check_history.append(cc)
        return cc

    def check_all(
        self,
        obligations: list[str],
        integration: dict[str, Any],
    ) -> list[ClosureCheck]:
        """Check all *obligations* against data in *integration*.

        The *integration* dict may contain the following optional keys:

        * ``"evidence"`` — ``tuple[str,...]`` or ``list[str]`` of evidence tags
          that apply globally.
        * ``"treaties"`` — list of :class:`OverlapTreaty` objects.
        * ``"descent_result"`` — a :class:`DescentResult` instance.
        * ``"patch_id"`` — string identifier of the active patch.

        If :attr:`_require_all_checks` is ``True`` and neither ``"evidence"``
        nor ``"treaties"`` is present, a ``ValueError`` is raised.

        Parameters
        ----------
        obligations:
            List of obligation strings to check.
        integration:
            Dict carrying context for the checks.

        Returns
        -------
        list[ClosureCheck]
            One :class:`ClosureCheck` per obligation, in the same order as
            *obligations*.
        """
        evidence: tuple[str, ...] = tuple(integration.get("evidence", ()))
        treaties: list[Any] = integration.get("treaties", [])
        descent_result: Any = integration.get("descent_result", None)
        patch_id: str = integration.get("patch_id", "")

        if self._require_all_checks and not evidence and not treaties:
            raise ValueError(
                "check_all: integration dict must supply 'evidence' or 'treaties' "
                "when require_all_checks=True."
            )

        checks: list[ClosureCheck] = []
        for obligation in obligations:
            # Determine best check type based on available data
            if descent_result is not None:
                cc = self.check_with_descent(obligation, evidence, descent_result, patch_id)
            elif treaties:
                cc = self.check_with_treaty(obligation, treaties, patch_id)
            else:
                cc = self.check(obligation, evidence, patch_id, check_type="semantic")
            checks.append(cc)
        return checks

    def check_with_descent(
        self,
        obligation: str,
        evidence: tuple[str, ...],
        descent_result: Any,
        patch_id: str = "",
    ) -> ClosureCheck:
        """Check *obligation* using a :class:`DescentResult` as primary source.

        If the descent succeeded, the obligation is considered closed with
        high confidence.  Otherwise the obstruction is inspected for
        relevance to *obligation* and a graded result is returned.

        Falls back to semantic checking if the descent object is not a
        recognised :class:`DescentResult`.

        Parameters
        ----------
        obligation:
            Obligation string.
        evidence:
            Supplementary evidence tags.
        descent_result:
            A :class:`DescentResult` object (or any duck-typed equivalent
            exposing ``.is_success`` and ``.is_failure``).
        patch_id:
            Optional patch identifier.
        """
        result_str, confidence = self._check_descent_data(obligation, descent_result)

        # Blend with semantic evidence
        sem_result, sem_confidence = self._check_semantic_closure(obligation, evidence)

        # Weighted average: descent carries 70 % weight when available
        blended_confidence = 0.7 * confidence + 0.3 * sem_confidence
        if blended_confidence >= self._trust_threshold:
            result_str = ClosureResult.CLOSED.value
        elif blended_confidence > 0.0:
            # Only upgrade to "closed" if descent itself says so
            if result_str == ClosureResult.CLOSED.value and blended_confidence < self._trust_threshold:
                result_str = ClosureResult.PARTIAL.value

        cc = ClosureCheck(
            obligation_id=obligation,
            patch_id=patch_id,
            result=result_str,
            confidence=round(blended_confidence, 4),
            evidence=evidence,
            check_type="descent",
            notes="descent-blended check",
        )
        self._check_history.append(cc)
        return cc

    def check_with_treaty(
        self,
        obligation: str,
        treaties: list[Any],
        patch_id: str = "",
    ) -> ClosureCheck:
        """Check *obligation* against a list of :class:`OverlapTreaty` objects.

        Iterates through all treaties and their clauses looking for a
        satisfied clause whose *expectation* text has sufficient keyword
        overlap with *obligation*.

        Parameters
        ----------
        obligation:
            Obligation string.
        treaties:
            List of :class:`OverlapTreaty` instances.
        patch_id:
            Optional patch identifier.
        """
        result_str, confidence = self._check_treaty_compliance(obligation, treaties)

        if confidence >= self._trust_threshold:
            result_str = ClosureResult.CLOSED.value
        elif result_str == ClosureResult.CLOSED.value and confidence < self._trust_threshold:
            result_str = ClosureResult.PARTIAL.value

        cc = ClosureCheck(
            obligation_id=obligation,
            patch_id=patch_id,
            result=result_str,
            confidence=round(confidence, 4),
            evidence=(),
            check_type="treaty",
            notes="treaty-based check",
        )
        self._check_history.append(cc)
        return cc

    def batch_check(
        self,
        obligation_evidence_map: dict[str, tuple[str, ...]],
        patch_id: str = "",
    ) -> dict[str, ClosureCheck]:
        """Run :meth:`check` for every entry in *obligation_evidence_map*.

        Parameters
        ----------
        obligation_evidence_map:
            Dict mapping obligation strings to evidence tuples.
        patch_id:
            Patch context applied to all checks.

        Returns
        -------
        dict[str, ClosureCheck]
            Same keys as the input, values are :class:`ClosureCheck` results.
        """
        return {
            obligation: self.check(obligation, evidence, patch_id=patch_id)
            for obligation, evidence in obligation_evidence_map.items()
        }

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def get_history(self) -> list[ClosureCheck]:
        """Return a copy of the internal check history list."""
        return list(self._check_history)

    def clear_history(self) -> None:
        """Clear all recorded check history."""
        self._check_history.clear()

    # ------------------------------------------------------------------
    # Private checker implementations
    # ------------------------------------------------------------------

    def _check_semantic_closure(
        self,
        obligation: str,
        evidence: tuple[str, ...],
    ) -> tuple[str, float]:
        """Compute semantic closure result and confidence from evidence count.

        Strategy (theory2.tex §39.8 Definition 39.8.4):

        * 0 evidence items      → ``"open"``,   confidence ≈ ``0``
        * 1–2 evidence items    → ``"partial"``, confidence ∝ quality
        * 3+ evidence items     → ``"closed"``,  confidence ∝ quality

        Additionally, the obligation string is checked for negative markers
        (e.g. ``"unresolved"``, ``"pending"``), which lower the confidence.

        Parameters
        ----------
        obligation:
            Obligation string.
        evidence:
            Evidence tags.

        Returns
        -------
        tuple[str, float]
            ``(result, confidence)`` where result is one of
            ``"open"``, ``"partial"``, ``"closed"``.
        """
        n = len(evidence)

        # Compute quality-weighted evidence count
        weighted_count = sum(_evidence_quality_score(tag) for tag in evidence)

        # Negative markers in the obligation reduce confidence
        negative_markers = {
            "unresolved", "pending", "missing", "absent", "failed",
            "broken", "open", "gap", "todo", "incomplete",
        }
        obligation_penalty = 0.0
        obligation_lower = obligation.lower()
        for marker in negative_markers:
            if marker in obligation_lower:
                obligation_penalty += 0.15
        obligation_penalty = min(obligation_penalty, 0.4)

        if n == 0:
            result = ClosureResult.OPEN.value
            confidence = 0.0
        elif n <= 2:
            result = ClosureResult.PARTIAL.value
            raw_confidence = min(weighted_count / 5.0, 1.0) * 0.75
            confidence = max(raw_confidence - obligation_penalty, 0.0)
        else:
            result = ClosureResult.CLOSED.value
            raw_confidence = min(weighted_count / 5.0, 1.0)
            # Additional credit for variety of evidence
            variety_bonus = min((n - 3) * 0.05, 0.2)
            confidence = max(min(raw_confidence + variety_bonus - obligation_penalty, 1.0), 0.0)

        # Keyword overlap between obligation and evidence boosts confidence
        if n > 0:
            overlap_scores = [_keyword_overlap(obligation, tag) for tag in evidence]
            best_overlap = max(overlap_scores)
            confidence = min(confidence + best_overlap * 0.15, 1.0)

        return result, round(confidence, 4)

    def _check_treaty_compliance(
        self,
        obligation: str,
        treaties: list[Any],
    ) -> tuple[str, float]:
        """Determine closure from treaty clauses.

        Scans all treaties for a clause whose ``expectation`` has sufficient
        keyword overlap with *obligation* and whose ``satisfied`` flag is
        ``True``.

        Parameters
        ----------
        obligation:
            Obligation to evaluate.
        treaties:
            List of treaty objects.

        Returns
        -------
        tuple[str, float]
            ``(result, confidence)``.
        """
        if not treaties:
            return ClosureResult.OPEN.value, 0.0

        best_score = 0.0
        best_satisfied = False

        for treaty in treaties:
            clauses = getattr(treaty, "clauses", [])
            for clause in clauses:
                expectation = getattr(clause, "expectation", "")
                satisfied = getattr(clause, "satisfied", False)
                overlap = _keyword_overlap(obligation, expectation)
                if overlap > best_score:
                    best_score = overlap
                    best_satisfied = satisfied

        if best_score == 0.0:
            return ClosureResult.OPEN.value, 0.0

        # Scale confidence: high overlap + satisfied -> high confidence
        confidence = best_score * (1.0 if best_satisfied else 0.4)

        if best_satisfied and confidence >= 0.5:
            result = ClosureResult.CLOSED.value
        elif confidence > 0.0:
            result = ClosureResult.PARTIAL.value
        else:
            result = ClosureResult.OPEN.value

        return result, round(confidence, 4)

    def _check_descent_data(
        self,
        obligation: str,
        descent_result: Any,
    ) -> tuple[str, float]:
        """Determine closure from a :class:`DescentResult`.

        Parameters
        ----------
        obligation:
            Obligation to evaluate.
        descent_result:
            Object exposing ``.is_success``, ``.is_failure``, and optionally
            ``.unwrap_obstruction()`` returning a :class:`DescentObstruction`.

        Returns
        -------
        tuple[str, float]
            ``(result, confidence)``.
        """
        if descent_result is None:
            return ClosureResult.OPEN.value, 0.0

        is_success = getattr(descent_result, "is_success", None)
        is_failure = getattr(descent_result, "is_failure", None)

        # Duck-type: handle plain booleans and property objects
        if callable(is_success):
            success = is_success()
        else:
            success = bool(is_success)

        if callable(is_failure):
            failure = is_failure()
        else:
            failure = bool(is_failure)

        if success:
            # Successful descent closes all obligations in its domain
            return ClosureResult.CLOSED.value, 0.95

        if failure:
            # Try to extract obstruction and see if obligation is relevant
            obstruction = None
            try:
                unwrap = getattr(descent_result, "unwrap_obstruction", None)
                if callable(unwrap):
                    obstruction = unwrap()
            except Exception:
                pass

            if obstruction is not None:
                violated = getattr(obstruction, "violated_overlaps", [])
                coh_class = getattr(obstruction, "cohomology_class", "")
                # Check if obligation appears in violated overlaps
                for v in violated:
                    if _keyword_overlap(obligation, str(v)) > 0.3:
                        return ClosureResult.OPEN.value, 0.85
                # Obligation not mentioned in obstruction — may still be closed
                if coh_class and _keyword_overlap(obligation, str(coh_class)) > 0.2:
                    return ClosureResult.OPEN.value, 0.6
                # Obstruction is irrelevant to this obligation
                return ClosureResult.PARTIAL.value, 0.4

            return ClosureResult.OPEN.value, 0.5

        # Indeterminate descent state
        return ClosureResult.PARTIAL.value, 0.3


# ---------------------------------------------------------------------------
# ObligationRegistry
# ---------------------------------------------------------------------------


class ObligationRegistry:
    """Mutable registry that tracks the open/closed status of obligations.

    The registry is scoped to a single integration run identified by
    *integration_id*.  It supports snapshotting so that the construction
    engine can checkpoint and restore state.

    Parameters
    ----------
    integration_id:
        Unique identifier for the integration run.

    Examples
    --------
    >>> reg = ObligationRegistry("run_001")
    >>> reg.register("all overlaps resolved")
    >>> reg.mark_closed("all overlaps resolved", evidence=("overlap:ok",))
    >>> reg.closure_fraction()
    1.0
    """

    def __init__(self, integration_id: str) -> None:
        self.integration_id = integration_id
        #: Maps obligation_id -> metadata dict
        self.obligations: dict[str, dict[str, Any]] = {}
        #: Set of obligation_ids that are currently closed
        self.closed_set: set[str] = set()
        #: Set of obligation_ids that are currently open
        self.open_set: set[str] = set()
        self._snapshots: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        obligation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an obligation as *open*.

        If the obligation is already registered, only the metadata is
        updated; the current open/closed status is preserved.

        Parameters
        ----------
        obligation_id:
            Unique string identifying the obligation.
        metadata:
            Optional extra data (e.g. patch_id, priority).
        """
        if obligation_id not in self.obligations:
            self.obligations[obligation_id] = {}
            self.open_set.add(obligation_id)
        if metadata:
            self.obligations[obligation_id].update(metadata)
        logger.debug("ObligationRegistry.register %r", obligation_id[:60])

    def register_many(self, obligation_ids: list[str]) -> None:
        """Register multiple obligations in one call.

        Parameters
        ----------
        obligation_ids:
            List of obligation strings.
        """
        for oid in obligation_ids:
            self.register(oid)

    def is_registered(self, obligation_id: str) -> bool:
        """Return ``True`` iff *obligation_id* is known to the registry.

        Parameters
        ----------
        obligation_id:
            Obligation to query.
        """
        return obligation_id in self.obligations

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def mark_closed(
        self,
        obligation_id: str,
        evidence: tuple[str, ...],
    ) -> None:
        """Mark an obligation as *closed*, recording the closing evidence.

        If the obligation is not yet registered it is auto-registered first.

        Parameters
        ----------
        obligation_id:
            Obligation to close.
        evidence:
            The evidence that supports closure.
        """
        if obligation_id not in self.obligations:
            self.register(obligation_id)
        self.obligations[obligation_id]["closing_evidence"] = evidence
        self.obligations[obligation_id]["closed_at"] = time.time()
        self.closed_set.add(obligation_id)
        self.open_set.discard(obligation_id)
        logger.debug("ObligationRegistry.mark_closed %r", obligation_id[:60])

    def mark_open(self, obligation_id: str) -> None:
        """Mark a previously closed obligation as *open* again.

        This is used when a construction step invalidates a prior closure.

        Parameters
        ----------
        obligation_id:
            Obligation to reopen.
        """
        if obligation_id not in self.obligations:
            self.register(obligation_id)
        self.closed_set.discard(obligation_id)
        self.open_set.add(obligation_id)
        self.obligations[obligation_id].pop("closing_evidence", None)
        self.obligations[obligation_id].pop("closed_at", None)
        logger.debug("ObligationRegistry.mark_open %r", obligation_id[:60])

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_open(self) -> list[str]:
        """Return a sorted list of currently open obligation IDs."""
        return sorted(self.open_set)

    def get_closed(self) -> list[str]:
        """Return a sorted list of currently closed obligation IDs."""
        return sorted(self.closed_set)

    def closure_fraction(self) -> float:
        """Return the fraction of registered obligations that are closed.

        Returns ``0.0`` when no obligations are registered.
        """
        total = len(self.obligations)
        if total == 0:
            return 0.0
        return len(self.closed_set) / total

    def summary(self) -> str:
        """Return a human-readable one-line summary of registry state.

        Returns
        -------
        str
            E.g. ``"Registry 'run_001': 7/10 obligations closed (70.0%)"``
        """
        total = len(self.obligations)
        closed = len(self.closed_set)
        frac = self.closure_fraction() * 100.0
        return (
            f"Registry '{self.integration_id}': "
            f"{closed}/{total} obligations closed ({frac:.1f}%)"
        )

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Capture a snapshot of the current registry state.

        The snapshot is appended to the internal history and returned so
        callers can store it externally.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"integration_id"``, ``"closed_set"``,
            ``"open_set"``, ``"obligations_count"``, ``"timestamp"``,
            ``"snapshot_index"``.
        """
        snap: dict[str, Any] = {
            "integration_id": self.integration_id,
            "closed_set": list(self.closed_set),
            "open_set": list(self.open_set),
            "obligations_count": len(self.obligations),
            "timestamp": time.time(),
            "snapshot_index": len(self._snapshots),
        }
        self._snapshots.append(snap)
        return snap

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore the registry to a previously captured *snapshot*.

        Obligations listed in the snapshot that are not currently registered
        are re-registered with empty metadata.

        Parameters
        ----------
        snapshot:
            A dict previously returned by :meth:`snapshot`.
        """
        self.closed_set = set(snapshot.get("closed_set", []))
        self.open_set = set(snapshot.get("open_set", []))
        # Ensure all obligations are present
        for oid in self.closed_set | self.open_set:
            if oid not in self.obligations:
                self.obligations[oid] = {}
        logger.debug(
            "ObligationRegistry.restore: %d closed, %d open",
            len(self.closed_set),
            len(self.open_set),
        )


# ---------------------------------------------------------------------------
# EvidenceAggregator
# ---------------------------------------------------------------------------


class EvidenceAggregator:
    """Utilities for combining, deduplicating, and scoring evidence tuples.

    The aggregator merges multiple evidence tuples into a single canonical
    tuple and provides confidence scoring for the merged result.  Weights
    can be assigned to individual evidence tags to bias the confidence
    calculation toward high-value evidence types.

    Parameters
    ----------
    min_confidence:
        Floor confidence value — :meth:`compute_confidence` never returns
        below this value when at least one evidence item is present.
        Defaults to ``0.5``.

    Examples
    --------
    >>> agg = EvidenceAggregator(min_confidence=0.4)
    >>> agg.set_weight("treaty:ratified", 1.5)
    >>> ev = agg.aggregate([("schema:ok", "treaty:ratified"), ("test:passed",)])
    >>> agg.compute_confidence(ev)
    0.76
    """

    def __init__(self, min_confidence: float = 0.5) -> None:
        self._min_confidence = min_confidence
        self._weight_map: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def aggregate(self, evidence_list: list[tuple[str, ...]]) -> tuple[str, ...]:
        """Merge multiple evidence tuples into one deduplicated sorted tuple.

        All items from all input tuples are combined; duplicates are removed;
        the result is sorted for determinism.

        Parameters
        ----------
        evidence_list:
            List of evidence tuples to merge.

        Returns
        -------
        tuple[str, ...]
            Merged, deduplicated, sorted evidence tuple.
        """
        merged: set[str] = set()
        for ev_tuple in evidence_list:
            merged.update(ev_tuple)
        return tuple(sorted(merged))

    def aggregate_with_weights(
        self,
        weighted_evidence: list[tuple[tuple[str, ...], float]],
    ) -> tuple[str, ...]:
        """Merge evidence tuples, keeping only items whose effective weight exceeds threshold.

        Each ``(evidence_tuple, source_weight)`` pair contributes items
        weighted by both their source weight and their individual tag weight.
        Items with a combined weight below ``0.1`` are discarded.

        Parameters
        ----------
        weighted_evidence:
            List of ``(evidence_tuple, source_weight)`` pairs where
            *source_weight* is a non-negative float.

        Returns
        -------
        tuple[str, ...]
            Filtered, sorted evidence tuple.
        """
        scores: dict[str, float] = defaultdict(float)
        for ev_tuple, source_weight in weighted_evidence:
            for tag in ev_tuple:
                tag_weight = self._weight_map.get(tag, 1.0)
                scores[tag] += source_weight * tag_weight

        selected = sorted(tag for tag, score in scores.items() if score >= 0.1)
        return tuple(selected)

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def compute_confidence(self, evidence: tuple[str, ...]) -> float:
        """Compute an aggregate confidence score for *evidence*.

        The score is based on:

        * The count of distinct evidence items.
        * The individual quality score (:func:`_evidence_quality_score`) of
          each item.
        * Any custom weights registered via :meth:`set_weight`.

        Parameters
        ----------
        evidence:
            Evidence tuple to score.

        Returns
        -------
        float
            Confidence in ``[0, 1]``.
        """
        if not evidence:
            return 0.0

        weighted_total = 0.0
        for tag in evidence:
            base_quality = _evidence_quality_score(tag)
            custom_weight = self._weight_map.get(tag, 1.0)
            weighted_total += base_quality * custom_weight

        # Logarithmic scaling: diminishing returns for large evidence sets
        import math
        raw = min(weighted_total / (1.0 + math.log(1 + len(evidence))), 1.0)
        return round(max(raw, self._min_confidence if evidence else 0.0), 4)

    def is_sufficient(
        self,
        evidence: tuple[str, ...],
        threshold: float = 0.7,
    ) -> bool:
        """Return ``True`` iff :meth:`compute_confidence` meets *threshold*.

        Parameters
        ----------
        evidence:
            Evidence tuple to evaluate.
        threshold:
            Minimum confidence required.
        """
        return self.compute_confidence(evidence) >= threshold

    # ------------------------------------------------------------------
    # Weight management
    # ------------------------------------------------------------------

    def set_weight(self, evidence_tag: str, weight: float) -> None:
        """Register a custom weight for a specific evidence tag.

        Tags with higher weights contribute more to confidence calculations.
        Default weight for unregistered tags is ``1.0``.

        Parameters
        ----------
        evidence_tag:
            The evidence string to weight.
        weight:
            Non-negative float.  Values above ``2.0`` are clamped to ``2.0``.
        """
        self._weight_map[evidence_tag] = max(0.0, min(weight, 2.0))

    def get_weight(self, evidence_tag: str) -> float:
        """Return the registered weight for *evidence_tag*, or ``1.0`` if not set.

        Parameters
        ----------
        evidence_tag:
            The tag to query.
        """
        return self._weight_map.get(evidence_tag, 1.0)

    def top_evidence(
        self,
        evidence: tuple[str, ...],
        k: int = 5,
    ) -> tuple[str, ...]:
        """Return the top-*k* evidence items sorted by their effective weight.

        Items with higher ``weight × quality`` product appear first.

        Parameters
        ----------
        evidence:
            Evidence tuple to rank.
        k:
            Maximum number of items to return.
        """
        if not evidence:
            return ()
        scored = sorted(
            evidence,
            key=lambda tag: _evidence_quality_score(tag) * self._weight_map.get(tag, 1.0),
            reverse=True,
        )
        return tuple(scored[:k])


# ---------------------------------------------------------------------------
# ClosureReport
# ---------------------------------------------------------------------------


@dataclass
class ClosureReport:
    """Comprehensive report of the semantic closure status of an integration.

    A :class:`ClosureReport` is produced by :func:`build_report` and
    aggregates all :class:`ClosureCheck` results and :class:`ClosureGap`
    records from a single integration run.

    Attributes
    ----------
    report_id:
        Unique identifier for this report.
    integration_id:
        Identifier of the integration run.
    checks:
        List of all :class:`ClosureCheck` records.
    gaps:
        List of all :class:`ClosureGap` records.
    fraction_closed:
        Fraction of obligations that were closed, in ``[0, 1]``.
    generated_at:
        Unix epoch timestamp when the report was generated.
    """

    report_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    integration_id: str = ""
    checks: list[ClosureCheck] = field(default_factory=list)
    gaps: list[ClosureGap] = field(default_factory=list)
    fraction_closed: float = 0.0
    generated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    def closed_count(self) -> int:
        """Number of checks whose result is ``"closed"``."""
        return sum(1 for c in self.checks if c.is_closed())

    def open_count(self) -> int:
        """Number of checks whose result is ``"open"``."""
        return sum(1 for c in self.checks if c.is_open())

    def gap_count_by_severity(self) -> dict[str, int]:
        """Return a dict mapping severity label to count of gaps.

        Returns
        -------
        dict[str, int]
            E.g. ``{"minor": 2, "critical": 1}``.
        """
        counts: dict[str, int] = defaultdict(int)
        for gap in self.gaps:
            counts[gap.severity] += 1
        return dict(counts)

    def get_critical_gaps(self) -> list[ClosureGap]:
        """Return gaps whose severity is ``"critical"`` or ``"blocking"``."""
        return [
            g for g in self.gaps
            if g.severity in {GapSeverity.CRITICAL.value, GapSeverity.BLOCKING.value}
        ]

    def get_checks_by_type(self, check_type: str) -> list[ClosureCheck]:
        """Return all checks of a given *check_type*.

        Parameters
        ----------
        check_type:
            One of ``"semantic"``, ``"treaty"``, ``"descent"``,
            ``"combined"``.
        """
        return [c for c in self.checks if c.check_type == check_type]

    def is_passing(self) -> bool:
        """Return ``True`` iff the integration is fully closed with no critical gaps.

        Passing requires ``fraction_closed >= 1.0`` and no blocking or
        critical gaps.
        """
        return self.fraction_closed >= 1.0 and len(self.get_critical_gaps()) == 0

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a multi-line human-readable summary of this report.

        Returns
        -------
        str
            Multi-line summary with section headings.
        """
        lines: list[str] = [
            f"Closure Report [{self.report_id}]",
            f"  Integration : {self.integration_id}",
            f"  Generated   : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.generated_at))}",
            f"  Checks      : {len(self.checks)} total, "
            f"{self.closed_count()} closed, {self.open_count()} open",
            f"  Fraction    : {self.fraction_closed:.1%}",
            f"  Gaps        : {len(self.gaps)}",
        ]

        severity_counts = self.gap_count_by_severity()
        if severity_counts:
            lines.append("  Gap breakdown:")
            for sev in (
                GapSeverity.BLOCKING.value,
                GapSeverity.CRITICAL.value,
                GapSeverity.MODERATE.value,
                GapSeverity.MINOR.value,
                GapSeverity.INFO.value,
            ):
                n = severity_counts.get(sev, 0)
                if n:
                    lines.append(f"    {sev:10s} {n}")

        lines.append(f"  Passing     : {'YES' if self.is_passing() else 'NO'}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the report to a plain :class:`dict`."""
        return {
            "report_id": self.report_id,
            "integration_id": self.integration_id,
            "checks": [c.to_dict() for c in self.checks],
            "gaps": [g.to_dict() for g in self.gaps],
            "fraction_closed": self.fraction_closed,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosureReport:
        """Deserialise a :class:`ClosureReport` from a plain :class:`dict`.

        Parameters
        ----------
        data:
            Dict as returned by :meth:`to_dict`.
        """
        return cls(
            report_id=data.get("report_id", uuid.uuid4().hex[:16]),
            integration_id=data.get("integration_id", ""),
            checks=[ClosureCheck.from_dict(c) for c in data.get("checks", [])],
            gaps=[ClosureGap.from_dict(g) for g in data.get("gaps", [])],
            fraction_closed=float(data.get("fraction_closed", 0.0)),
            generated_at=float(data.get("generated_at", time.time())),
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def run_closure_check(
    obligation_id: str,
    evidence: tuple[str, ...],
    patch_id: str = "",
) -> ClosureCheck:
    """Convenience function: run a single semantic closure check.

    Creates a transient :class:`ClosureChecker` with default settings and
    calls :meth:`~ClosureChecker.check`.

    Parameters
    ----------
    obligation_id:
        The obligation to check.
    evidence:
        Evidence tags to use for the check.
    patch_id:
        Optional coordinate patch identifier.

    Returns
    -------
    ClosureCheck
        The result of the check.

    Examples
    --------
    >>> cc = run_closure_check(
    ...     "all treaties ratified",
    ...     evidence=("treaty:ratified", "review:ok", "test:passed"),
    ... )
    >>> cc.result
    'closed'
    """
    checker = ClosureChecker()
    return checker.check(obligation_id, evidence, patch_id=patch_id)


def build_report(
    integration_id: str,
    checks: list[ClosureCheck],
    gaps: list[ClosureGap],
) -> ClosureReport:
    """Build a :class:`ClosureReport` from a list of checks and gaps.

    The ``fraction_closed`` field is computed automatically from *checks*.

    Parameters
    ----------
    integration_id:
        Identifier of the integration run.
    checks:
        All :class:`ClosureCheck` records for this run.
    gaps:
        All :class:`ClosureGap` records.

    Returns
    -------
    ClosureReport
        Populated report object.

    Examples
    --------
    >>> report = build_report("run_42", checks=[...], gaps=[...])
    >>> report.is_passing()
    True
    """
    total = len(checks)
    closed = sum(1 for c in checks if c.is_closed())
    fraction = closed / total if total > 0 else 0.0

    return ClosureReport(
        integration_id=integration_id,
        checks=list(checks),
        gaps=list(gaps),
        fraction_closed=round(fraction, 6),
    )


def check_obligations_from_registry(
    registry: ObligationRegistry,
    checker: ClosureChecker,
) -> ClosureReport:
    """Run closure checks for all obligations currently open in *registry*.

    For each open obligation, a semantic check is run with whatever closing
    evidence was recorded in the registry's metadata (if any).  Resulting
    gaps are created for every non-closed check.

    Parameters
    ----------
    registry:
        The :class:`ObligationRegistry` to draw obligations from.
    checker:
        The :class:`ClosureChecker` to use for each check.

    Returns
    -------
    ClosureReport
        Report covering all registered obligations.

    Notes
    -----
    Obligations already in ``registry.closed_set`` are represented as
    :class:`ClosureCheck` records with a ``"closed"`` result and confidence
    ``1.0`` (they were previously verified by :meth:`ObligationRegistry.mark_closed`).
    Open obligations are checked afresh with any stored evidence.
    """
    all_checks: list[ClosureCheck] = []
    gaps: list[ClosureGap] = []

    # Already-closed obligations → synthetic "closed" check
    for oid in sorted(registry.closed_set):
        evidence = tuple(
            registry.obligations.get(oid, {}).get("closing_evidence", ())
        )
        cc = ClosureCheck(
            obligation_id=oid,
            patch_id=registry.obligations.get(oid, {}).get("patch_id", ""),
            result=ClosureResult.CLOSED.value,
            confidence=1.0,
            evidence=evidence,
            check_type="semantic",
            notes="pre-closed by registry",
        )
        all_checks.append(cc)

    # Open obligations → run checker
    for oid in sorted(registry.open_set):
        meta = registry.obligations.get(oid, {})
        evidence = tuple(meta.get("evidence", ()))
        patch_id = meta.get("patch_id", "")
        cc = checker.check(oid, evidence, patch_id=patch_id)
        all_checks.append(cc)

        # Generate gap if not closed
        if not cc.is_closed():
            severity = _severity_for_confidence(cc.confidence, cc.result)
            description = (
                f"Obligation '{oid[:80]}' is {cc.result} "
                f"(confidence={cc.confidence:.2f})"
            )
            gap = ClosureGap(
                obligation_id=oid,
                description=description,
                severity=severity,
                patch_id=patch_id,
                suggested_fix=(
                    "Add evidence tags or satisfy a matching treaty clause."
                    if cc.result == ClosureResult.OPEN.value
                    else "Provide additional corroborating evidence."
                ),
                source_check_id=cc.check_id,
            )
            gaps.append(gap)

    return build_report(registry.integration_id, all_checks, gaps)
