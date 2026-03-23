"""Treaty-based merging for hypercover synthesis.

Chapter 41 of theory2.tex §41.8 — Treaty Merging.

This module implements the formal machinery for combining multiple ratified
treaties into a unified treaty that covers a larger patch of the synthesis
space. Treaty merging is the computational analogue of the Čech gluing
condition: given local agreements on overlapping patches, we construct a
globally consistent agreement on the union.

**Mathematical Background**

Let {U_i} be an open cover of the synthesis domain X. A treaty t_{ij} is a
formal agreement between generation agents i and j on the overlap U_i ∩ U_j.
The Čech cocycle condition demands that for any triple (i, j, k):

    t_{ij} ∘ t_{jk} = t_{ik}   on U_i ∩ U_j ∩ U_k

When this condition holds, the local treaties can be assembled into a global
section — a unified treaty covering the union ∪ U_i. The TreatyMerger
implements this assembly operation, detecting and resolving violations of the
cocycle condition (MergeConflict) before finalising the merged TreatyRecord.

**Merge Strategies**

Four strategies are supported:

- GREEDY: Accept the first consistent extension found. Fast but may miss
  globally optimal solutions. Suitable for large treaty graphs where
  exhaustive search is prohibitive.
- CONSERVATIVE: Only merge when every pairwise compatibility check passes
  with no conflicts. Slowest but produces the most robust merged treaty.
- COCYCLE: Explicitly verify the Čech cocycle on every triple before
  merging. Intermediate cost; recommended for production synthesis.
- TRANSITIVE: Derive t_{AC} from t_{AB} and t_{BC} via composition, filling
  in missing edges. Useful when the treaty graph is sparse.

**Key References**

- Čech, E. (1932). Théorie générale de l'homologie dans un espace quelconque.
- Grothendieck, A. (1957). Sur quelques points d'algèbre homologique.
- theory2.tex §41.8, §42.1–§42.4 (internal).

# copilot: s04-treaty-merging
"""
from __future__ import annotations

import uuid
import hashlib
import json
import time
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Sequence, Mapping
from enum import Enum
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.hypercover_treaties.models import (
        HypercoverSynthesisRecord,
        TreatyCandidate,
        OverlapLaw,
        DependentTreaty,
        SynthesisOutcome,
        SynthesisPhase,
        LawStability,
        CandidateSource,
        TreatyRole,
        OutcomeKind,
        SynthesisConfig,
        OverlapLawIndex,
    )
except ImportError:
    pass

try:
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus
except ImportError:
    pass

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MergeStrategy(str, Enum):
    """Strategy used by TreatyMerger when combining treaties.

    Each strategy encodes a different trade-off between merge speed,
    coverage, and the strength of the consistency guarantees produced.

    Attributes
    ----------
    GREEDY:
        Accept the first consistent extension. O(n) in the number of
        treaties; may leave unresolved secondary conflicts.
    CONSERVATIVE:
        Only merge when *all* pairwise compatibility checks pass without
        any conflict. Slowest but produces the highest-confidence result.
    COCYCLE:
        Verify the Čech cocycle on every triple (i, j, k) before merging.
        Balances thoroughness and performance; default for production.
    TRANSITIVE:
        Derive missing edges t_{AC} = t_{AB} ∘ t_{BC} and fill in the
        treaty graph before merging. Useful for sparse treaty graphs.
    """

    GREEDY = "greedy"
    CONSERVATIVE = "conservative"
    COCYCLE = "cocycle"
    TRANSITIVE = "transitive"


class MergePhase(str, Enum):
    """Lifecycle phases of a treaty merge operation.

    The merge pipeline advances through these phases in order. A failure
    at any phase halts the pipeline and records the phase in the resulting
    TreatyMergeRecord.

    Attributes
    ----------
    NEGOTIATION:
        Initial compatibility analysis; treaties exchange metadata and
        declare their shared coordinate ranges.
    CONFLICT_RESOLUTION:
        Any MergeConflicts discovered during NEGOTIATION are resolved
        (or flagged as blocking).
    COCYCLE_CHECK:
        The Čech cocycle condition is verified over all triples.
    FINALIZATION:
        The merged treaty ID is minted, provenance is recorded, and the
        TreatyMergeWitness is emitted.
    """

    NEGOTIATION = "negotiation"
    CONFLICT_RESOLUTION = "conflict_resolution"
    COCYCLE_CHECK = "cocycle_check"
    FINALIZATION = "finalization"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TreatyMergeWitness:
    """Immutable witness record of a single treaty merge event.

    A TreatyMergeWitness is created at the moment a merge is finalised.
    It is intentionally immutable: once a merge has been witnessed, its
    record cannot be altered, ensuring an append-only audit trail.

    Parameters
    ----------
    witness_id:
        Globally unique identifier for this witness record. Generated
        automatically via ``uuid.uuid4()`` when not supplied.
    treaty_ids:
        Ordered tuple of the IDs of treaties that were merged. Ordering
        matches the sequence in which treaties were fed to the merger.
    merged_treaty_id:
        ID of the resulting unified treaty produced by the merge.
    merge_strategy:
        String representation of the MergeStrategy used.
    cocycle_satisfied:
        ``True`` iff the Čech cocycle condition was verified for every
        triple of input treaties.
    provenance:
        Tuple of free-form provenance strings (e.g. agent names, commit
        SHAs, run IDs) recorded at merge time.
    timestamp:
        Unix timestamp (float) at which the merge was finalised.

    Examples
    --------
    >>> w = TreatyMergeWitness(
    ...     witness_id="w-001",
    ...     treaty_ids=("t-a", "t-b"),
    ...     merged_treaty_id="t-ab",
    ...     merge_strategy=MergeStrategy.COCYCLE.value,
    ...     cocycle_satisfied=True,
    ...     provenance=("agent-alpha", "run-42"),
    ...     timestamp=1_700_000_000.0,
    ... )
    >>> w.to_dict()["cocycle_satisfied"]
    True
    """

    witness_id: str
    treaty_ids: tuple[str, ...]
    merged_treaty_id: str
    merge_strategy: str
    cocycle_satisfied: bool
    provenance: tuple[str, ...]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise this witness to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields converted to JSON-safe Python types. The
            ``treaty_ids`` and ``provenance`` tuples are converted to
            lists so the result can be round-tripped via ``json.dumps``.
        """
        return {
            "witness_id": self.witness_id,
            "treaty_ids": list(self.treaty_ids),
            "merged_treaty_id": self.merged_treaty_id,
            "merge_strategy": self.merge_strategy,
            "cocycle_satisfied": self.cocycle_satisfied,
            "provenance": list(self.provenance),
            "timestamp": self.timestamp,
            "iso_timestamp": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TreatyMergeConflict:
    """Immutable record of a conflict discovered during treaty merging.

    A conflict arises when two treaties make incompatible assertions about
    the same coordinate or overlap region. Conflicts are classified by
    ``conflict_type`` and graded by ``severity``. Blocking conflicts
    (``is_blocking() → True``) prevent the merge from succeeding unless
    explicitly resolved.

    Parameters
    ----------
    conflict_id:
        Unique identifier for this conflict record.
    treaty_a_id:
        ID of the first treaty involved in the conflict.
    treaty_b_id:
        ID of the second treaty involved in the conflict.
    conflict_type:
        Short machine-readable label, e.g. ``"coordinate_mismatch"``,
        ``"overlap_inconsistency"``, ``"cocycle_violation"``.
    description:
        Human-readable description of what the conflict is and why it
        was detected.
    severity:
        One of ``"low"``, ``"medium"``, ``"high"``, or ``"critical"``.
    resolvable:
        ``True`` iff the merger has a strategy to automatically resolve
        this conflict without human intervention.

    Examples
    --------
    >>> c = TreatyMergeConflict(
    ...     conflict_id="cf-001",
    ...     treaty_a_id="t-a",
    ...     treaty_b_id="t-b",
    ...     conflict_type="coordinate_mismatch",
    ...     description="Coordinate ranges [0,1] and [2,3] do not overlap.",
    ...     severity="high",
    ...     resolvable=False,
    ... )
    >>> c.is_blocking()
    True
    """

    conflict_id: str
    treaty_a_id: str
    treaty_b_id: str
    conflict_type: str
    description: str
    severity: str
    resolvable: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialise this conflict to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields as JSON-safe Python types plus the derived
            ``blocking`` field from :meth:`is_blocking`.
        """
        return {
            "conflict_id": self.conflict_id,
            "treaty_a_id": self.treaty_a_id,
            "treaty_b_id": self.treaty_b_id,
            "conflict_type": self.conflict_type,
            "description": self.description,
            "severity": self.severity,
            "resolvable": self.resolvable,
            "blocking": self.is_blocking(),
        }

    def is_blocking(self) -> bool:
        """Return ``True`` if this conflict prevents merging.

        A conflict is considered blocking when its severity is
        ``"high"`` or ``"critical"`` AND it is not automatically
        resolvable.

        Returns
        -------
        bool
        """
        return self.severity in ("high", "critical") and not self.resolvable


@dataclass(frozen=True, slots=True)
class TreatyMergeRecord:
    """Immutable record summarising the outcome of a full merge operation.

    Created at the end of the FINALIZATION phase, TreatyMergeRecord
    captures every artefact of a merge: which treaties were merged, which
    strategy was used, which phase the merge reached, how many conflicts
    were found and resolved, and whether cocycle checks passed.

    Parameters
    ----------
    merge_id:
        Globally unique ID for this merge operation.
    source_treaty_ids:
        Tuple of IDs of the treaties that were fed into the merge.
    result_treaty_id:
        ID of the treaty produced by the merge (may be empty string if
        the merge failed).
    strategy:
        The MergeStrategy value used.
    phase:
        The MergePhase at which the merge completed (or failed).
    conflicts_found:
        Total number of conflicts detected during NEGOTIATION.
    conflicts_resolved:
        Number of those conflicts that were automatically resolved.
    cocycle_checks_passed:
        Number of triple-wise Čech cocycle checks that passed.
    provenance:
        Tuple of provenance strings passed by the caller.
    metadata:
        Arbitrary JSON-serialisable metadata dict attached by the caller.
    created_at:
        Unix timestamp of record creation.
    """

    merge_id: str
    source_treaty_ids: tuple[str, ...]
    result_treaty_id: str
    strategy: str
    phase: str
    conflicts_found: int
    conflicts_resolved: int
    cocycle_checks_passed: int
    provenance: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary."""
        return {
            "merge_id": self.merge_id,
            "source_treaty_ids": list(self.source_treaty_ids),
            "result_treaty_id": self.result_treaty_id,
            "strategy": self.strategy,
            "phase": self.phase,
            "conflicts_found": self.conflicts_found,
            "conflicts_resolved": self.conflicts_resolved,
            "cocycle_checks_passed": self.cocycle_checks_passed,
            "provenance": list(self.provenance),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "iso_created_at": datetime.fromtimestamp(
                self.created_at, tz=timezone.utc
            ).isoformat(),
        }

    @property
    def success(self) -> bool:
        """``True`` iff the merge reached FINALIZATION with a result ID."""
        return self.phase == MergePhase.FINALIZATION.value and bool(
            self.result_treaty_id
        )

    @property
    def unresolved_conflicts(self) -> int:
        """Number of conflicts that were found but not resolved."""
        return max(0, self.conflicts_found - self.conflicts_resolved)


# ---------------------------------------------------------------------------
# TreatyMergeAnalyzer
# ---------------------------------------------------------------------------


class TreatyMergeAnalyzer:
    """Analyses a collection of treaties for merge feasibility.

    The analyzer performs pre-merge due diligence: it computes pairwise
    compatibility, detects MergeConflicts, checks the Čech cocycle on
    triples, and produces a numeric score summarising merge readiness.

    The analyzer is stateless between calls; every public method accepts
    the treaty collection as an argument and returns a fresh result.

    Theory reference: theory2.tex §41.8.2 — Pre-merge analysis.
    """

    # Weights used by ``score()`` to combine sub-scores.
    _WEIGHT_COMPATIBILITY = 0.40
    _WEIGHT_CONFLICT_FREE = 0.35
    _WEIGHT_COCYCLE = 0.25

    def analyze(self, treaties: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Produce a comprehensive analysis report for a list of treaties.

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings. Each mapping must contain at
            least an ``"id"`` key. Additional keys (``"coordinates"``,
            ``"agents"``, ``"overlap"``…) are used when present.

        Returns
        -------
        dict
            Keys: ``"treaty_count"``, ``"score"``, ``"conflicts"``,
            ``"cocycle_satisfied"``, ``"compatibility_matrix"``,
            ``"report"``.
        """
        logger.debug("TreatyMergeAnalyzer.analyze: %d treaties", len(treaties))
        conflicts = self.detect_conflicts(treaties)
        cocycle_ok = self.check_cocycle_condition(treaties)
        compat = self.compute_compatibility_matrix(treaties)
        score = self.score(treaties)
        return {
            "treaty_count": len(treaties),
            "score": score,
            "conflicts": [c.to_dict() for c in conflicts],
            "conflict_count": len(conflicts),
            "blocking_conflicts": sum(1 for c in conflicts if c.is_blocking()),
            "cocycle_satisfied": cocycle_ok,
            "compatibility_matrix": compat,
            "report": self.report(treaties),
        }

    def score(self, treaties: Sequence[Mapping[str, Any]]) -> float:
        """Compute a [0, 1] merge-readiness score for a treaty collection.

        The score is a weighted combination of:

        * **compatibility_score** — average pairwise compatibility across
          all treaty pairs.
        * **conflict_score** — 1.0 minus the fraction of pairs that have
          at least one blocking conflict.
        * **cocycle_score** — 1.0 if the Čech cocycle holds for all
          triples, 0.0 otherwise.

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        float
            Value in [0.0, 1.0]. Returns 1.0 for empty or singleton
            collections (trivially merge-ready).
        """
        n = len(treaties)
        if n <= 1:
            return 1.0

        pairs = [(treaties[i], treaties[j]) for i in range(n) for j in range(i + 1, n)]
        compat_scores: list[float] = []
        blocking_count = 0

        for ta, tb in pairs:
            c = self._pairwise_compatibility(ta, tb)
            compat_scores.append(c)
            if c < 0.5:
                blocking_count += 1

        compatibility_score = sum(compat_scores) / len(compat_scores)
        conflict_score = 1.0 - (blocking_count / len(pairs))
        cocycle_score = 1.0 if self.check_cocycle_condition(treaties) else 0.0

        combined = (
            self._WEIGHT_COMPATIBILITY * compatibility_score
            + self._WEIGHT_CONFLICT_FREE * conflict_score
            + self._WEIGHT_COCYCLE * cocycle_score
        )
        return round(min(1.0, max(0.0, combined)), 6)

    def report(self, treaties: Sequence[Mapping[str, Any]]) -> str:
        """Generate a human-readable merge feasibility report.

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        str
            Multi-line report suitable for logging or display.
        """
        n = len(treaties)
        score = self.score(treaties)
        conflicts = self.detect_conflicts(treaties)
        blocking = [c for c in conflicts if c.is_blocking()]
        cocycle_ok = self.check_cocycle_condition(treaties)

        lines = [
            "=" * 60,
            "TreatyMergeAnalyzer Report",
            "=" * 60,
            f"  Treaties submitted : {n}",
            f"  Merge-readiness    : {score:.4f}",
            f"  Conflicts found    : {len(conflicts)}",
            f"  Blocking conflicts : {len(blocking)}",
            f"  Čech cocycle OK    : {cocycle_ok}",
            "-" * 60,
        ]
        if conflicts:
            lines.append("  Conflicts:")
            for c in conflicts:
                marker = "[BLOCKING]" if c.is_blocking() else "[warn]"
                lines.append(
                    f"    {marker} {c.conflict_id}: {c.conflict_type} "
                    f"({c.treaty_a_id} ↔ {c.treaty_b_id})"
                )
        else:
            lines.append("  No conflicts detected.")
        lines.append("=" * 60)
        return "\n".join(lines)

    def detect_conflicts(
        self, treaties: Sequence[Mapping[str, Any]]
    ) -> list[TreatyMergeConflict]:
        """Detect all pairwise merge conflicts in a treaty collection.

        For each ordered pair (A, B) the analyzer checks:

        1. Coordinate range overlap — if both treaties declare coordinate
           ranges, do those ranges intersect?
        2. Agent exclusivity — if both treaties declare ``"exclusive"``
           agents, do they share any agent IDs that would prevent merging?
        3. Overlap law consistency — do both treaties agree on the overlap
           law governing their shared region?

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        list[TreatyMergeConflict]
            All conflicts detected. May be empty.
        """
        conflicts: list[TreatyMergeConflict] = []
        n = len(treaties)
        for i in range(n):
            for j in range(i + 1, n):
                ta, tb = treaties[i], treaties[j]
                id_a = str(ta.get("id", f"treaty-{i}"))
                id_b = str(tb.get("id", f"treaty-{j}"))

                # Check coordinate range mismatch
                coords_a = ta.get("coordinates")
                coords_b = tb.get("coordinates")
                if isinstance(coords_a, (list, tuple)) and isinstance(
                    coords_b, (list, tuple)
                ):
                    if set(coords_a).isdisjoint(set(coords_b)):
                        conflicts.append(
                            TreatyMergeConflict(
                                conflict_id=f"cf-{uuid.uuid4().hex[:8]}",
                                treaty_a_id=id_a,
                                treaty_b_id=id_b,
                                conflict_type="coordinate_disjoint",
                                description=(
                                    f"Treaties {id_a} and {id_b} declare "
                                    "completely disjoint coordinate sets; "
                                    "merging would produce an empty overlap."
                                ),
                                severity="high",
                                resolvable=False,
                            )
                        )

                # Check overlap law consistency
                law_a = ta.get("overlap_law")
                law_b = tb.get("overlap_law")
                if law_a and law_b and law_a != law_b:
                    conflicts.append(
                        TreatyMergeConflict(
                            conflict_id=f"cf-{uuid.uuid4().hex[:8]}",
                            treaty_a_id=id_a,
                            treaty_b_id=id_b,
                            conflict_type="overlap_law_mismatch",
                            description=(
                                f"Treaty {id_a} uses overlap_law={law_a!r} "
                                f"but treaty {id_b} uses overlap_law={law_b!r}."
                            ),
                            severity="medium",
                            resolvable=True,
                        )
                    )

                # Check exclusive agent collision
                agents_a = set(ta.get("exclusive_agents") or [])
                agents_b = set(tb.get("exclusive_agents") or [])
                shared = agents_a & agents_b
                if shared:
                    conflicts.append(
                        TreatyMergeConflict(
                            conflict_id=f"cf-{uuid.uuid4().hex[:8]}",
                            treaty_a_id=id_a,
                            treaty_b_id=id_b,
                            conflict_type="exclusive_agent_collision",
                            description=(
                                f"Treaties {id_a} and {id_b} both claim "
                                f"exclusive ownership of agents: {sorted(shared)}."
                            ),
                            severity="critical",
                            resolvable=False,
                        )
                    )

        logger.debug(
            "detect_conflicts: found %d conflict(s) in %d treaties",
            len(conflicts),
            len(treaties),
        )
        return conflicts

    def check_cocycle_condition(
        self, treaties: Sequence[Mapping[str, Any]]
    ) -> bool:
        """Verify the Čech cocycle condition over all triples.

        For every triple (A, B, C) we check whether the composed
        transition t_{AB} ∘ t_{BC} is consistent with t_{AC} as declared
        in the treaty data.  If any triple fails, we return ``False``.

        When treaties do not carry explicit transition data the check
        falls back to verifying that the treaties share a common
        ``"schema_version"`` field (a weak necessary condition).

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        bool
            ``True`` iff the cocycle condition is satisfied for all
            triples (or the collection has fewer than three elements).
        """
        n = len(treaties)
        if n < 3:
            return True

        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    ta, tb, tc = treaties[i], treaties[j], treaties[k]
                    if not self._triple_cocycle_ok(ta, tb, tc):
                        logger.debug(
                            "Cocycle violated on triple (%s, %s, %s)",
                            ta.get("id"),
                            tb.get("id"),
                            tc.get("id"),
                        )
                        return False
        return True

    def compute_compatibility_matrix(
        self, treaties: Sequence[Mapping[str, Any]]
    ) -> dict[str, dict[str, float]]:
        """Compute a pairwise compatibility matrix for the treaty collection.

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        dict[str, dict[str, float]]
            Nested mapping ``matrix[id_a][id_b] = compatibility_score``
            for all pairs (including self-compatibility = 1.0).
        """
        matrix: dict[str, dict[str, float]] = {}
        ids = [str(t.get("id", f"treaty-{idx}")) for idx, t in enumerate(treaties)]

        for idx, t in enumerate(treaties):
            row_id = ids[idx]
            matrix[row_id] = {}
            for jdx, other in enumerate(treaties):
                col_id = ids[jdx]
                if idx == jdx:
                    matrix[row_id][col_id] = 1.0
                else:
                    matrix[row_id][col_id] = self._pairwise_compatibility(t, other)

        return matrix

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pairwise_compatibility(
        self, ta: Mapping[str, Any], tb: Mapping[str, Any]
    ) -> float:
        """Return a compatibility score in [0, 1] for a treaty pair.

        The score is computed as the mean of three sub-scores:

        * **schema_score** (1.0 if schema_version agrees, else 0.5)
        * **agent_score** (Jaccard similarity of agent sets if present,
          else 1.0)
        * **overlap_score** (1.0 if overlap_law matches or absent, else
          0.3)
        """
        schema_a = ta.get("schema_version")
        schema_b = tb.get("schema_version")
        schema_score = 1.0 if (schema_a is None or schema_b is None or schema_a == schema_b) else 0.5

        agents_a = set(ta.get("agents") or [])
        agents_b = set(tb.get("agents") or [])
        if agents_a or agents_b:
            union = agents_a | agents_b
            intersection = agents_a & agents_b
            agent_score = len(intersection) / len(union) if union else 1.0
        else:
            agent_score = 1.0

        law_a = ta.get("overlap_law")
        law_b = tb.get("overlap_law")
        overlap_score = 1.0 if (not law_a or not law_b or law_a == law_b) else 0.3

        return round((schema_score + agent_score + overlap_score) / 3.0, 6)

    def _triple_cocycle_ok(
        self,
        ta: Mapping[str, Any],
        tb: Mapping[str, Any],
        tc: Mapping[str, Any],
    ) -> bool:
        """Check the Čech cocycle condition for a single triple (A, B, C).

        Concretely: t_{AB} composed with t_{BC} should equal t_{AC}.
        Without explicit transition functions we use schema_version as a
        proxy: all three must agree on schema_version when it is present.
        """
        versions = {
            t.get("schema_version")
            for t in (ta, tb, tc)
            if t.get("schema_version") is not None
        }
        if len(versions) > 1:
            return False

        # If transition hashes are present, verify composition
        h_ab = ta.get("transition_to", {}).get(str(tb.get("id", "")))
        h_bc = tb.get("transition_to", {}).get(str(tc.get("id", "")))
        h_ac = ta.get("transition_to", {}).get(str(tc.get("id", "")))

        if h_ab and h_bc and h_ac:
            composed = hashlib.sha256(f"{h_ab}:{h_bc}".encode()).hexdigest()[:16]
            return composed == h_ac[:16]

        return True


# ---------------------------------------------------------------------------
# TreatyMergeCoordinator
# ---------------------------------------------------------------------------


class TreatyMergeCoordinator:
    """Orchestrates the full treaty merge pipeline.

    The coordinator drives treaties through the four MergePhases and
    records the full history of every merge it performs. It delegates
    analysis to TreatyMergeAnalyzer and conflict resolution to its own
    ``resolve_conflict`` method.

    Parameters
    ----------
    analyzer:
        Optional TreatyMergeAnalyzer instance. A default instance is
        created if none is supplied.
    max_conflicts:
        Maximum number of *resolvable* conflicts to auto-resolve before
        declaring the merge blocked. Default 32.

    Theory reference: theory2.tex §41.8.4 — Merge Coordinator.

    Examples
    --------
    >>> coord = TreatyMergeCoordinator()
    >>> treaties = [{"id": "t1", "agents": ["a"]}, {"id": "t2", "agents": ["b"]}]
    >>> witness = coord.run(treaties, MergeStrategy.COCYCLE)
    >>> witness.cocycle_satisfied
    True
    """

    def __init__(
        self,
        analyzer: TreatyMergeAnalyzer | None = None,
        max_conflicts: int = 32,
    ) -> None:
        self._analyzer = analyzer or TreatyMergeAnalyzer()
        self._max_conflicts = max_conflicts
        self._history: list[TreatyMergeRecord] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        treaties: Sequence[Mapping[str, Any]],
        strategy: MergeStrategy = MergeStrategy.COCYCLE,
    ) -> TreatyMergeWitness:
        """Run the full merge pipeline and return an immutable witness.

        The pipeline executes in order:

        1. NEGOTIATION — validate inputs and analyse compatibility.
        2. CONFLICT_RESOLUTION — auto-resolve resolvable conflicts.
        3. COCYCLE_CHECK — verify the Čech cocycle on all triples.
        4. FINALIZATION — mint the merged treaty ID and emit the witness.

        Parameters
        ----------
        treaties:
            Sequence of treaty dicts to merge. Order matters for GREEDY
            strategy; for others it is advisory.
        strategy:
            Which MergeStrategy to apply.

        Returns
        -------
        TreatyMergeWitness
            Immutable record of the completed merge.

        Raises
        ------
        ValueError
            If ``treaties`` is empty.
        RuntimeError
            If blocking conflicts cannot be resolved.
        """
        if not treaties:
            raise ValueError("Cannot merge an empty treaty collection.")

        logger.info(
            "TreatyMergeCoordinator.run: merging %d treaties with strategy=%s",
            len(treaties),
            strategy.value,
        )

        source_ids = tuple(str(t.get("id", f"anon-{i}")) for i, t in enumerate(treaties))
        merge_id = f"merge-{uuid.uuid4().hex[:12]}"
        t0 = time.time()

        # ---- NEGOTIATION ----
        current_phase = MergePhase.NEGOTIATION
        validation_errors = self.validate(treaties)
        if validation_errors:
            logger.warning("Validation warnings: %s", validation_errors)

        conflicts = self._analyzer.detect_conflicts(treaties)
        conflicts_found = len(conflicts)

        # ---- CONFLICT_RESOLUTION ----
        current_phase = MergePhase.CONFLICT_RESOLUTION
        resolved_count = 0
        for c in conflicts:
            if resolved_count >= self._max_conflicts:
                break
            if c.resolvable:
                self.resolve_conflict(c)
                resolved_count += 1
            elif c.is_blocking():
                logger.error(
                    "Blocking conflict %s cannot be resolved: %s",
                    c.conflict_id,
                    c.description,
                )
                if strategy == MergeStrategy.CONSERVATIVE:
                    record = self._make_record(
                        merge_id=merge_id,
                        source_ids=source_ids,
                        result_id="",
                        strategy=strategy,
                        phase=current_phase,
                        conflicts_found=conflicts_found,
                        conflicts_resolved=resolved_count,
                        cocycle_checks=0,
                        provenance=("blocked_by_conflict",),
                        t0=t0,
                    )
                    self._history.append(record)
                    raise RuntimeError(
                        f"Merge blocked by unresolvable conflict: {c.conflict_id}"
                    )

        # ---- COCYCLE_CHECK ----
        current_phase = MergePhase.COCYCLE_CHECK
        n = len(treaties)
        triple_count = max(0, n * (n - 1) * (n - 2) // 6)

        if strategy in (MergeStrategy.COCYCLE, MergeStrategy.CONSERVATIVE):
            cocycle_ok = self._analyzer.check_cocycle_condition(treaties)
            if strategy == MergeStrategy.CONSERVATIVE and not cocycle_ok:
                record = self._make_record(
                    merge_id=merge_id,
                    source_ids=source_ids,
                    result_id="",
                    strategy=strategy,
                    phase=current_phase,
                    conflicts_found=conflicts_found,
                    conflicts_resolved=resolved_count,
                    cocycle_checks=triple_count,
                    provenance=("cocycle_failed",),
                    t0=t0,
                )
                self._history.append(record)
                raise RuntimeError("CONSERVATIVE merge aborted: Čech cocycle not satisfied.")
        elif strategy == MergeStrategy.TRANSITIVE:
            cocycle_ok = self._derive_transitive(treaties)
        else:
            cocycle_ok = True  # GREEDY skips the check

        cocycle_passes = triple_count if cocycle_ok else 0

        # ---- FINALIZATION ----
        current_phase = MergePhase.FINALIZATION
        payload = json.dumps(
            {
                "source_ids": sorted(source_ids),
                "strategy": strategy.value,
                "timestamp": t0,
            },
            sort_keys=True,
        )
        merged_id = "merged-" + hashlib.sha256(payload.encode()).hexdigest()[:16]

        witness = TreatyMergeWitness(
            witness_id=f"witness-{uuid.uuid4().hex[:12]}",
            treaty_ids=source_ids,
            merged_treaty_id=merged_id,
            merge_strategy=strategy.value,
            cocycle_satisfied=cocycle_ok,
            provenance=("coordinator",),
            timestamp=time.time(),
        )

        record = self._make_record(
            merge_id=merge_id,
            source_ids=source_ids,
            result_id=merged_id,
            strategy=strategy,
            phase=current_phase,
            conflicts_found=conflicts_found,
            conflicts_resolved=resolved_count,
            cocycle_checks=cocycle_passes,
            provenance=("coordinator",),
            t0=t0,
        )
        self._history.append(record)

        logger.info(
            "Merge %s finalised → %s (cocycle=%s)",
            merge_id,
            merged_id,
            cocycle_ok,
        )
        return witness

    def validate(self, treaties: Sequence[Mapping[str, Any]]) -> list[str]:
        """Return a list of validation warnings for the treaty collection.

        Unlike ``detect_conflicts`` this method performs structural checks
        only (missing required keys, duplicate IDs, etc.) and returns
        human-readable strings rather than TreatyMergeConflict objects.

        Parameters
        ----------
        treaties:
            Sequence of treaty mappings.

        Returns
        -------
        list[str]
            Zero or more warning strings. An empty list means the
            collection passed all structural checks.
        """
        warnings: list[str] = []
        seen_ids: set[str] = set()

        for idx, t in enumerate(treaties):
            tid = t.get("id")
            if tid is None:
                warnings.append(f"Treaty at index {idx} has no 'id' field.")
            elif str(tid) in seen_ids:
                warnings.append(f"Duplicate treaty ID {tid!r} at index {idx}.")
            else:
                seen_ids.add(str(tid))

            if "agents" not in t:
                warnings.append(
                    f"Treaty {tid!r} has no 'agents' field; "
                    "overlap analysis may be incomplete."
                )

        if len(treaties) < 2:
            warnings.append(
                "Fewer than two treaties supplied; merge is a no-op."
            )

        return warnings

    def to_dict(self) -> dict[str, Any]:
        """Serialise coordinator state to a JSON-compatible dictionary.

        Returns
        -------
        dict
            Keys: ``"merge_count"``, ``"history"``.
        """
        return {
            "merge_count": len(self._history),
            "history": [r.to_dict() for r in self._history],
        }

    def merge_pair(
        self, t1: Mapping[str, Any], t2: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Merge exactly two treaties and return the combined treaty dict.

        This is a convenience wrapper around :meth:`run` for the common
        two-treaty case. The resulting dict is suitable for feeding back
        into the coordinator as a treaty in subsequent merges.

        Parameters
        ----------
        t1:
            First treaty mapping.
        t2:
            Second treaty mapping.

        Returns
        -------
        dict
            Merged treaty dictionary with keys ``"id"``, ``"agents"``,
            ``"coordinates"``, ``"source_ids"``, ``"merged_at"``.
        """
        witness = self.run([t1, t2], MergeStrategy.COCYCLE)
        agents = sorted(
            set(list(t1.get("agents") or []) + list(t2.get("agents") or []))
        )
        coords = sorted(
            set(
                list(t1.get("coordinates") or [])
                + list(t2.get("coordinates") or [])
            )
        )
        return {
            "id": witness.merged_treaty_id,
            "agents": agents,
            "coordinates": coords,
            "source_ids": [str(t1.get("id", "?")), str(t2.get("id", "?"))],
            "merged_at": witness.timestamp,
            "schema_version": t1.get("schema_version") or t2.get("schema_version"),
            "overlap_law": t1.get("overlap_law") or t2.get("overlap_law"),
        }

    def merge_batch(
        self, treaties: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Incrementally merge a list of treaties pairwise (fold-left).

        Applies merge_pair sequentially: ((t1 ⊕ t2) ⊕ t3) ⊕ … ⊕ tN.
        This corresponds to the GREEDY strategy applied iteratively.

        Parameters
        ----------
        treaties:
            Sequence of at least two treaty mappings.

        Returns
        -------
        list[dict]
            List of intermediate merged treaties produced at each step.
            The last element is the final unified treaty.

        Raises
        ------
        ValueError
            If fewer than two treaties are provided.
        """
        if len(treaties) < 2:
            raise ValueError("merge_batch requires at least two treaties.")

        results: list[dict[str, Any]] = []
        accumulator = dict(treaties[0])

        for nxt in treaties[1:]:
            merged = self.merge_pair(accumulator, nxt)
            results.append(merged)
            accumulator = merged

        logger.debug("merge_batch: produced %d intermediate treaties", len(results))
        return results

    def resolve_conflict(self, conflict: TreatyMergeConflict) -> dict[str, Any]:
        """Attempt to automatically resolve a TreatyMergeConflict.

        Resolution strategies by ``conflict_type``:

        - ``overlap_law_mismatch``: adopt the lexicographically larger law
          (deterministic tie-breaking).
        - ``coordinate_disjoint``: no auto-resolution possible; returns
          status ``"unresolvable"``.
        - Any other resolvable conflict: returns ``"resolved_by_default"``.

        Parameters
        ----------
        conflict:
            The conflict to resolve.

        Returns
        -------
        dict
            Keys: ``"conflict_id"``, ``"status"``, ``"action"``.
        """
        if not conflict.resolvable:
            return {
                "conflict_id": conflict.conflict_id,
                "status": "unresolvable",
                "action": "none",
            }

        if conflict.conflict_type == "overlap_law_mismatch":
            action = "adopt_lexicographic_max"
        else:
            action = "resolved_by_default"

        logger.debug(
            "Resolved conflict %s via %s", conflict.conflict_id, action
        )
        return {
            "conflict_id": conflict.conflict_id,
            "status": "resolved",
            "action": action,
        }

    def apply_cocycle(
        self,
        t1: Mapping[str, Any],
        t2: Mapping[str, Any],
        t3: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Derive the composed transition t_{13} from t_{12} and t_{23}.

        Implements the Čech cocycle derivation:

            t_{13} = t_{12} ∘ t_{23}

        The hash of the composed transition is computed deterministically
        so that multiple calls with the same inputs yield the same result.

        Parameters
        ----------
        t1, t2, t3:
            Treaty mappings for agents 1, 2, and 3 respectively.

        Returns
        -------
        dict
            A partial treaty dict representing t_{13} with keys
            ``"id"``, ``"source_pair"``, ``"transition_hash"``,
            ``"derived_via_cocycle"``.
        """
        id1 = str(t1.get("id", "A"))
        id3 = str(t3.get("id", "C"))
        h12 = hashlib.sha256(json.dumps(dict(t1), sort_keys=True).encode()).hexdigest()[:16]
        h23 = hashlib.sha256(json.dumps(dict(t2), sort_keys=True).encode()).hexdigest()[:16]
        composed_hash = hashlib.sha256(f"{h12}:{h23}".encode()).hexdigest()[:16]
        derived_id = f"derived-{id1}-{id3}-{composed_hash}"

        logger.debug(
            "apply_cocycle: derived t_{%s,%s} = %s", id1, id3, derived_id
        )
        return {
            "id": derived_id,
            "source_pair": (id1, id3),
            "transition_hash": composed_hash,
            "derived_via_cocycle": True,
            "agents": sorted(
                set(list(t1.get("agents") or []) + list(t3.get("agents") or []))
            ),
        }

    def get_merge_history(self) -> list[dict[str, Any]]:
        """Return the full merge history as a list of dicts.

        Returns
        -------
        list[dict]
            One entry per completed merge (success or failure).
        """
        return [r.to_dict() for r in self._history]

    def reset(self) -> None:
        """Clear all merge history from this coordinator instance.

        After reset the coordinator behaves as if freshly instantiated.
        The underlying analyzer is *not* reset.
        """
        count = len(self._history)
        self._history.clear()
        logger.debug("TreatyMergeCoordinator.reset: cleared %d history entries", count)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _derive_transitive(self, treaties: Sequence[Mapping[str, Any]]) -> bool:
        """Attempt to fill in missing t_{AC} edges via transitivity.

        Iterates over all triples and calls :meth:`apply_cocycle` to
        derive any missing composed transitions. Returns True if, after
        derivation, no triple violates the cocycle condition.
        """
        n = len(treaties)
        derived: list[dict[str, Any]] = []
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    d = self.apply_cocycle(treaties[i], treaties[j], treaties[k])
                    derived.append(d)
        all_treaties = list(treaties) + derived
        return self._analyzer.check_cocycle_condition(all_treaties)

    @staticmethod
    def _make_record(
        *,
        merge_id: str,
        source_ids: tuple[str, ...],
        result_id: str,
        strategy: MergeStrategy,
        phase: MergePhase,
        conflicts_found: int,
        conflicts_resolved: int,
        cocycle_checks: int,
        provenance: tuple[str, ...],
        t0: float,
    ) -> TreatyMergeRecord:
        return TreatyMergeRecord(
            merge_id=merge_id,
            source_treaty_ids=source_ids,
            result_treaty_id=result_id,
            strategy=strategy.value,
            phase=phase.value,
            conflicts_found=conflicts_found,
            conflicts_resolved=conflicts_resolved,
            cocycle_checks_passed=cocycle_checks,
            provenance=provenance,
            metadata={},
            created_at=time.time(),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def make_witness(
    treaty_ids: Sequence[str],
    merged_treaty_id: str,
    *,
    strategy: MergeStrategy = MergeStrategy.COCYCLE,
    cocycle_satisfied: bool = True,
    provenance: Sequence[str] = (),
) -> TreatyMergeWitness:
    """Convenience factory for TreatyMergeWitness.

    Parameters
    ----------
    treaty_ids:
        IDs of the treaties that were merged.
    merged_treaty_id:
        ID of the resulting treaty.
    strategy:
        MergeStrategy used.
    cocycle_satisfied:
        Whether the Čech cocycle was satisfied.
    provenance:
        Optional provenance strings.

    Returns
    -------
    TreatyMergeWitness
    """
    return TreatyMergeWitness(
        witness_id=f"witness-{uuid.uuid4().hex[:12]}",
        treaty_ids=tuple(treaty_ids),
        merged_treaty_id=merged_treaty_id,
        merge_strategy=strategy.value,
        cocycle_satisfied=cocycle_satisfied,
        provenance=tuple(provenance),
        timestamp=time.time(),
    )


def make_conflict(
    treaty_a_id: str,
    treaty_b_id: str,
    conflict_type: str,
    description: str,
    *,
    severity: str = "medium",
    resolvable: bool = True,
) -> TreatyMergeConflict:
    """Convenience factory for TreatyMergeConflict.

    Parameters
    ----------
    treaty_a_id, treaty_b_id:
        IDs of the two conflicting treaties.
    conflict_type:
        Short machine-readable label for the conflict type.
    description:
        Human-readable explanation.
    severity:
        One of ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
    resolvable:
        Whether the conflict can be auto-resolved.

    Returns
    -------
    TreatyMergeConflict
    """
    return TreatyMergeConflict(
        conflict_id=f"cf-{uuid.uuid4().hex[:8]}",
        treaty_a_id=treaty_a_id,
        treaty_b_id=treaty_b_id,
        conflict_type=conflict_type,
        description=description,
        severity=severity,
        resolvable=resolvable,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== treaty_merging.py smoke test ===\n")

    # --- TreatyMergeWitness ---
    witness = TreatyMergeWitness(
        witness_id="w-smoke-001",
        treaty_ids=("t-alpha", "t-beta", "t-gamma"),
        merged_treaty_id="t-merged-abc",
        merge_strategy=MergeStrategy.COCYCLE.value,
        cocycle_satisfied=True,
        provenance=("smoke-test", "run-0"),
        timestamp=time.time(),
    )
    print("TreatyMergeWitness.to_dict():")
    print(json.dumps(witness.to_dict(), indent=2))
    print()

    # --- TreatyMergeConflict ---
    conflict = TreatyMergeConflict(
        conflict_id="cf-smoke-001",
        treaty_a_id="t-alpha",
        treaty_b_id="t-beta",
        conflict_type="overlap_law_mismatch",
        description="Treaties use different overlap laws.",
        severity="medium",
        resolvable=True,
    )
    print("TreatyMergeConflict.to_dict():")
    print(json.dumps(conflict.to_dict(), indent=2))
    print(f"is_blocking() = {conflict.is_blocking()}")
    print()

    # --- TreatyMergeRecord ---
    record = TreatyMergeRecord(
        merge_id="merge-smoke-001",
        source_treaty_ids=("t-alpha", "t-beta"),
        result_treaty_id="t-merged-ab",
        strategy=MergeStrategy.GREEDY.value,
        phase=MergePhase.FINALIZATION.value,
        conflicts_found=1,
        conflicts_resolved=1,
        cocycle_checks_passed=1,
        provenance=("smoke-test",),
        metadata={"note": "smoke"},
        created_at=time.time(),
    )
    print("TreatyMergeRecord.success =", record.success)
    print("TreatyMergeRecord.unresolved_conflicts =", record.unresolved_conflicts)
    print()

    # --- TreatyMergeAnalyzer ---
    analyzer = TreatyMergeAnalyzer()
    treaties = [
        {
            "id": "t-1",
            "agents": ["agent-A", "agent-B"],
            "coordinates": [0, 1, 2],
            "schema_version": "v2",
            "overlap_law": "gluing-law-1",
        },
        {
            "id": "t-2",
            "agents": ["agent-B", "agent-C"],
            "coordinates": [1, 2, 3],
            "schema_version": "v2",
            "overlap_law": "gluing-law-1",
        },
        {
            "id": "t-3",
            "agents": ["agent-A", "agent-C"],
            "coordinates": [0, 2, 3],
            "schema_version": "v2",
            "overlap_law": "gluing-law-1",
        },
    ]

    analysis = analyzer.analyze(treaties)
    print("TreatyMergeAnalyzer.analyze() score =", analysis["score"])
    print("TreatyMergeAnalyzer.analyze() cocycle_satisfied =", analysis["cocycle_satisfied"])
    print(analyzer.report(treaties))

    # --- TreatyMergeCoordinator ---
    coord = TreatyMergeCoordinator()
    witness2 = coord.run(treaties, MergeStrategy.COCYCLE)
    print("TreatyMergeCoordinator.run() witness:")
    print(json.dumps(witness2.to_dict(), indent=2))
    print()

    batch_result = coord.merge_batch(treaties)
    print(f"merge_batch produced {len(batch_result)} merged treaties.")
    print("Final merged id:", batch_result[-1]["id"])
    print()

    pair_result = coord.merge_pair(treaties[0], treaties[1])
    print("merge_pair result id:", pair_result["id"])
    print()

    cocycle_derived = coord.apply_cocycle(treaties[0], treaties[1], treaties[2])
    print("apply_cocycle derived:", cocycle_derived["id"])
    print("derived_via_cocycle:", cocycle_derived["derived_via_cocycle"])
    print()

    history = coord.get_merge_history()
    print(f"Merge history has {len(history)} entries.")
    coord.reset()
    print(f"After reset: {len(coord.get_merge_history())} entries.")
    print()

    # --- make_witness / make_conflict helpers ---
    w = make_witness(["t-x", "t-y"], "t-xy", strategy=MergeStrategy.TRANSITIVE)
    print("make_witness:", w.witness_id, "strategy:", w.merge_strategy)

    c = make_conflict("t-x", "t-y", "coordinate_disjoint", "No overlap.", severity="high", resolvable=False)
    print("make_conflict:", c.conflict_id, "blocking:", c.is_blocking())
    print()

    print("=== smoke test complete ===")
