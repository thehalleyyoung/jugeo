"""
Regression testing as semantic memory retention.

# copilot: Regression testing = semantic memory retention — keeping past semantic
# achievements alive. Each test that continues to pass is evidence that the system
# has not forgotten a property it once knew. A regression is amnesia: the loss of
# a semantic achievement that cost effort to establish. This module models the full
# lifecycle — from recording a verified achievement into an oracle-backed memory
# store, through applying a retention policy to prune stale or low-confidence
# memories, to detecting regressions when the current system state no longer
# satisfies a remembered property. The Čech-cohomology framing provides a geometric
# vocabulary: a *closure regression* is a cohomological obstruction that has
# re-emerged after being temporarily resolved. An obstruction in H^1 signals that
# local consistency patches no longer extend to a global semantic closure. By
# treating regression suites as semantic memory stores and retention policies as
# pruning oracles, this module unifies software testing with the broader
# jugeo framework for semantic closure verification.

Design goals
------------
* **Immutability** — every domain object is ``frozen=True`` so that regression
  records can be safely hashed, deduplicated, and stored without mutation hazards.
* **Composability** — all operations return new values; the engine pattern
  (add_test → run_all → report) chains without side effects.
* **Graceful degradation** — optional jugeo sub-packages are imported in
  try/except blocks so the module loads cleanly in isolation.
* **Size** — the module is intentionally comprehensive so that it serves as a
  self-contained reference implementation of the semantic-memory-retention theory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Optional jugeo integrations
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message

    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TrustTier — ordered scale for semantic confidence
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """Ordered confidence scale for semantic memory entries.

    A *tier* reflects how strongly a semantic achievement is believed to hold.
    Lower tiers represent speculative or weakly witnessed claims; higher tiers
    represent solver-discharged or fully-proof-backed facts.

    The lattice operations ``join`` and ``meet`` allow combining tiers from
    multiple evidence sources into a single representative value. ``promote``
    and ``demote`` move one step up or down the scale.

    Tier definitions
    ----------------
    PROPOSAL (1)
        An oracle or human has proposed the property, but it is not yet verified.
    WITNESSED (2)
        A runtime execution has witnessed the property holding at least once.
    DISCHARGED (3)
        A solver or static analysis tool has discharged an obligation for this property.
    STRONGLY_VERIFIED (4)
        Multiple independent verification paths agree that the property holds.
    PROOF_BACKED (5)
        A formal proof (or proof certificate) exists backing the property.
    """

    PROPOSAL = 1
    WITNESSED = 2
    DISCHARGED = 3
    STRONGLY_VERIFIED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    @classmethod
    def join(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
        """Return the *least upper bound* (maximum confidence) of two tiers."""
        return cls(max(int(a), int(b)))

    @classmethod
    def meet(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
        """Return the *greatest lower bound* (minimum confidence) of two tiers."""
        return cls(min(int(a), int(b)))

    def promote(self) -> "TrustTier":
        """Return the next higher tier, saturating at ``PROOF_BACKED``."""
        next_val = min(int(self) + 1, int(TrustTier.PROOF_BACKED))
        return TrustTier(next_val)

    def demote(self) -> "TrustTier":
        """Return the next lower tier, saturating at ``PROPOSAL``."""
        prev_val = max(int(self) - 1, int(TrustTier.PROPOSAL))
        return TrustTier(prev_val)

    def label(self) -> str:
        """Human-readable label for this tier."""
        _labels: Dict[int, str] = {
            1: "proposal",
            2: "witnessed",
            3: "discharged",
            4: "strongly-verified",
            5: "proof-backed",
        }
        return _labels.get(int(self), "unknown")

    def is_verified(self) -> bool:
        """Return ``True`` if this tier is at least ``DISCHARGED``."""
        return int(self) >= int(TrustTier.DISCHARGED)

    def as_trust_level(self) -> int:
        """Return the integer value of this tier for cross-system comparison."""
        return int(self)


# ---------------------------------------------------------------------------
# Shared Judgment and CechObstruction dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """A verdict about whether a semantic property holds, with supporting evidence.

    Judgments are immutable records that can be stored in an oracle, compared
    across regression runs, and used to compute retention priorities.

    Attributes
    ----------
    judgment_id : str
        Unique identifier for this judgment.
    property_id : str
        Identifier of the property being judged.
    verdict : bool
        ``True`` if the property is judged to hold; ``False`` otherwise.
    tier : TrustTier
        Confidence tier associated with this judgment.
    evidence_kind : str
        Kind of evidence supporting this judgment (e.g. ``'solver_proof'``).
    recorded_at : float
        Unix timestamp at which the judgment was recorded.
    provenance : str
        Source of the judgment (solver, runtime, oracle, human).
    note : str
        Optional human-readable annotation.
    """

    judgment_id: str
    property_id: str
    verdict: bool
    tier: TrustTier
    evidence_kind: str
    recorded_at: float
    provenance: str = "unknown"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "judgment_id": self.judgment_id,
            "property_id": self.property_id,
            "verdict": self.verdict,
            "tier": self.tier.label(),
            "tier_value": int(self.tier),
            "evidence_kind": self.evidence_kind,
            "recorded_at": self.recorded_at,
            "provenance": self.provenance,
            "note": self.note,
        }

    def fingerprint(self) -> str:
        """Return a short hex digest identifying this judgment's content."""
        payload = json.dumps(
            {k: v for k, v in self.to_dict().items() if k != "judgment_id"},
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def is_positive(self) -> bool:
        """Return ``True`` if this is a positive (holding) judgment."""
        return self.verdict

    def is_high_confidence(self) -> bool:
        """Return ``True`` if this judgment has at least DISCHARGED tier."""
        return self.tier.is_verified()


@dataclass(frozen=True)
class CechObstruction:
    """A Čech-cohomology-style obstruction blocking semantic closure.

    In the semantic-closure framework an *obstruction* is a local inconsistency
    that prevents a collection of locally valid properties from being patched
    into a globally coherent whole. This dataclass records such an obstruction
    together with its geometric and semantic metadata.

    Attributes
    ----------
    obstruction_id : str
        Unique identifier for this obstruction record.
    concept_id : str
        Identifier of the concept or closure whose patching is blocked.
    degree : int
        Cohomological degree at which the obstruction lives (0, 1, 2, …).
    cover_elements : Tuple[str, ...]
        Identifiers of the cover elements involved in the inconsistency.
    description : str
        Human-readable description of why the obstruction arises.
    detected_at : float
        Unix timestamp at which the obstruction was first detected.
    resolved : bool
        Whether this obstruction has been resolved.
    resolution_note : str
        If resolved, a note describing how the obstruction was cleared.
    """

    obstruction_id: str
    concept_id: str
    degree: int
    cover_elements: Tuple[str, ...]
    description: str
    detected_at: float
    resolved: bool = False
    resolution_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "concept_id": self.concept_id,
            "degree": self.degree,
            "cover_elements": list(self.cover_elements),
            "description": self.description,
            "detected_at": self.detected_at,
            "resolved": self.resolved,
            "resolution_note": self.resolution_note,
        }

    def cohomological_label(self) -> str:
        """Return a label of the form ``H^n(X)`` for this obstruction's degree."""
        return f"H^{self.degree}(X)"

    def involves(self, cover_element: str) -> bool:
        """Return ``True`` if ``cover_element`` participates in this obstruction."""
        return cover_element in self.cover_elements

    def is_resolved(self) -> bool:
        """Return ``True`` if this obstruction has been cleared."""
        return self.resolved

    def resolve(self, note: str) -> "CechObstruction":
        """Return a new ``CechObstruction`` marked as resolved with ``note``."""
        return replace(self, resolved=True, resolution_note=note)


# ---------------------------------------------------------------------------
# Domain classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticRegression:
    """A detected regression in a previously achieved semantic property.

    A semantic regression is the loss of a property that was once verified to
    hold. It carries enough metadata to classify the regression, measure its
    age, and route it to the appropriate remediation workflow.

    Attributes
    ----------
    regression_id : str
        Unique identifier for this regression event.
    property_id : str
        Identifier of the property that regressed.
    property_description : str
        Human-readable description of the property.
    previously_achieved_at : float
        Unix timestamp when the property was last verified as holding.
    detected_at : float
        Unix timestamp when the regression was detected.
    severity : str
        Severity classification: ``'critical'``, ``'high'``, ``'medium'``, or ``'low'``.
    affected_cover_elements : Tuple[str, ...]
        Cover elements (e.g. subsystems, modules) affected by this regression.
    """

    regression_id: str
    property_id: str
    property_description: str
    previously_achieved_at: float
    detected_at: float
    severity: str
    affected_cover_elements: Tuple[str, ...]

    def is_critical(self) -> bool:
        """Return ``True`` if this regression has ``'critical'`` severity."""
        return self.severity.lower() == "critical"

    def age_seconds(self) -> float:
        """Return how many seconds have elapsed since the regression was detected."""
        return time.time() - self.detected_at

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "regression_id": self.regression_id,
            "property_id": self.property_id,
            "property_description": self.property_description,
            "previously_achieved_at": self.previously_achieved_at,
            "detected_at": self.detected_at,
            "severity": self.severity,
            "affected_cover_elements": list(self.affected_cover_elements),
            "age_seconds": self.age_seconds(),
            "is_critical": self.is_critical(),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this regression."""
        age = self.age_seconds()
        age_str = f"{age:.1f}s ago" if age < 3600 else f"{age / 3600:.1f}h ago"
        return (
            f"[{self.severity.upper()}] Regression in '{self.property_id}': "
            f"{self.property_description} (detected {age_str})"
        )

    def time_since_achievement(self) -> float:
        """Return seconds between the last achievement and the regression detection."""
        return max(0.0, self.detected_at - self.previously_achieved_at)

    def affects(self, element: str) -> bool:
        """Return ``True`` if ``element`` is in ``affected_cover_elements``."""
        return element in self.affected_cover_elements


@dataclass(frozen=True)
class MemoryRetentionPolicy:
    """Policy governing which semantic memories are retained and for how long.

    Retention policies are the gatekeepers of the semantic memory store. They
    decide which achievements deserve to be remembered, preventing the store
    from growing unbounded while ensuring that high-value memories persist.

    Attributes
    ----------
    policy_id : str
        Unique identifier for this policy.
    retain_verified_only : bool
        If ``True``, only retain memories with ``TrustTier >= DISCHARGED``.
    max_age_seconds : float
        Discard memories older than this many seconds. ``math.inf`` means keep forever.
    max_entries : int
        Maximum number of memory entries in the store after pruning.
    priority_filter : FrozenSet[str]
        If non-empty, only retain memories whose property_id is in this set.
    """

    policy_id: str
    retain_verified_only: bool = False
    max_age_seconds: float = math.inf
    max_entries: int = 10_000
    priority_filter: FrozenSet[str] = field(default_factory=frozenset)

    def should_retain(self, achievement: Dict[str, Any]) -> bool:
        """Return ``True`` if ``achievement`` should be kept under this policy.

        Parameters
        ----------
        achievement:
            A dictionary with at least the keys ``'property_id'``,
            ``'tier'`` (int), and ``'recorded_at'`` (float).
        """
        now = time.time()
        recorded_at: float = achievement.get("recorded_at", 0.0)
        tier_value: int = achievement.get("tier", 0)
        property_id: str = achievement.get("property_id", "")

        if self.retain_verified_only and tier_value < int(TrustTier.DISCHARGED):
            return False
        if now - recorded_at > self.max_age_seconds:
            return False
        if self.priority_filter and property_id not in self.priority_filter:
            return False
        return True

    def prune(self, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return a pruned copy of ``memories`` conforming to this policy.

        Pruning proceeds in two phases:
        1. Remove entries that ``should_retain`` rejects.
        2. If the list still exceeds ``max_entries``, evict the oldest entries
           (by ``recorded_at``) until the limit is met.
        """
        retained = [m for m in memories if self.should_retain(m)]
        retained.sort(key=lambda m: m.get("recorded_at", 0.0), reverse=True)
        return retained[: self.max_entries]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "retain_verified_only": self.retain_verified_only,
            "max_age_seconds": self.max_age_seconds if math.isfinite(self.max_age_seconds) else None,
            "max_entries": self.max_entries,
            "priority_filter": sorted(self.priority_filter),
        }

    def is_strict(self) -> bool:
        """Return ``True`` if this policy uses any non-default filtering."""
        return (
            self.retain_verified_only
            or math.isfinite(self.max_age_seconds)
            or self.max_entries < 10_000
            or bool(self.priority_filter)
        )

    def with_priority_filter(self, ids: FrozenSet[str]) -> "MemoryRetentionPolicy":
        """Return a copy of this policy with ``priority_filter`` replaced by ``ids``."""
        return replace(self, priority_filter=ids)


@dataclass(frozen=True)
class ClosureRegression:
    """A semantic closure that was attained and subsequently lost.

    This models the lifecycle of a conceptual closure: at some point the closure
    was established (``closed_at``), and later something caused it to reopen
    (``reopened_at``). The ``obstruction`` field optionally records a
    ``CechObstruction`` that explains the geometric/cohomological cause.

    Attributes
    ----------
    closure_regression_id : str
        Unique identifier for this closure-regression record.
    concept_id : str
        Identifier of the concept whose closure regressed.
    closed_at : float
        Unix timestamp when the closure was first established.
    reopened_at : float
        Unix timestamp when the closure was detected as having been lost.
    cause : str
        Short description of what caused the regression.
    obstruction : Optional[CechObstruction]
        Optional Čech obstruction associated with this regression.
    """

    closure_regression_id: str
    concept_id: str
    closed_at: float
    reopened_at: float
    cause: str
    obstruction: Optional[CechObstruction] = None

    def duration_closed(self) -> float:
        """Return how long (in seconds) the closure was held before it regressed."""
        return max(0.0, self.reopened_at - self.closed_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "closure_regression_id": self.closure_regression_id,
            "concept_id": self.concept_id,
            "closed_at": self.closed_at,
            "reopened_at": self.reopened_at,
            "cause": self.cause,
            "duration_closed_seconds": self.duration_closed(),
            "has_obstruction": self.has_obstruction(),
            "obstruction": self.obstruction.to_dict() if self.obstruction else None,
        }

    def has_obstruction(self) -> bool:
        """Return ``True`` if a Čech obstruction is associated with this regression."""
        return self.obstruction is not None

    def obstruction_degree(self) -> Optional[int]:
        """Return the cohomological degree of the obstruction, or ``None``."""
        return self.obstruction.degree if self.obstruction is not None else None

    def summary(self) -> str:
        """One-line summary of this closure regression."""
        obs_str = f" (obstruction: {self.obstruction.cohomological_label()})" if self.obstruction else ""
        return (
            f"ClosureRegression[{self.concept_id}]: "
            f"closed for {self.duration_closed():.1f}s, "
            f"reopened due to '{self.cause}'{obs_str}"
        )


@dataclass(frozen=True)
class RegressionOracle:
    """A queryable store of semantic memories and the policy governing them.

    The oracle is the heart of the semantic memory system. It holds a collection
    of verified achievements (``memory_store``) and applies a ``policy`` to
    decide what to keep. Callers can query the oracle for a specific property
    or evaluate an entire current state against all stored memories.

    Attributes
    ----------
    oracle_id : str
        Unique identifier for this oracle instance.
    memory_store : Tuple[Dict[str, Any], ...]
        Tuple of achievement dictionaries representing stored semantic memories.
    policy : MemoryRetentionPolicy
        Retention policy that governs which memories are preserved.
    """

    oracle_id: str
    memory_store: Tuple[Dict[str, Any], ...]
    policy: MemoryRetentionPolicy

    def query(self, property_id: str) -> Optional[Dict[str, Any]]:
        """Return the most-recent memory entry for ``property_id``, or ``None``.

        The most recent entry is determined by the ``recorded_at`` timestamp.
        """
        candidates = [m for m in self.memory_store if m.get("property_id") == property_id]
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.get("recorded_at", 0.0))

    def evaluate(self, current_state: Dict[str, Any]) -> List[SemanticRegression]:
        """Evaluate ``current_state`` against all stored memories.

        For each memory in the store, check whether the corresponding property
        still holds in ``current_state``. If it does not, emit a
        ``SemanticRegression``.

        ``current_state`` is expected to be a dict mapping ``property_id`` →
        ``bool`` (``True`` = property currently holds).
        """
        regressions: List[SemanticRegression] = []
        seen: Set[str] = set()
        for memory in self.memory_store:
            pid = memory.get("property_id", "")
            if pid in seen:
                continue
            seen.add(pid)
            if not self.policy.should_retain(memory):
                continue
            currently_holds = current_state.get(pid, False)
            if not currently_holds:
                severity = memory.get("severity", "medium")
                regression = SemanticRegression(
                    regression_id=str(uuid.uuid4()),
                    property_id=pid,
                    property_description=memory.get("description", pid),
                    previously_achieved_at=memory.get("recorded_at", 0.0),
                    detected_at=time.time(),
                    severity=severity,
                    affected_cover_elements=tuple(memory.get("cover_elements", [])),
                )
                regressions.append(regression)
        return regressions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "policy": self.policy.to_dict(),
            "memory_size": self.memory_size(),
            "memory_store": list(self.memory_store),
        }

    def memory_size(self) -> int:
        """Return the number of entries currently in the memory store."""
        return len(self.memory_store)

    def with_memory(self, extra: Tuple[Dict[str, Any], ...]) -> "RegressionOracle":
        """Return a new oracle with ``extra`` memories appended and then pruned."""
        combined = list(self.memory_store) + list(extra)
        pruned = self.policy.prune(combined)
        return replace(self, memory_store=tuple(pruned))

    def property_ids(self) -> FrozenSet[str]:
        """Return the set of property IDs currently in the memory store."""
        return frozenset(m.get("property_id", "") for m in self.memory_store if m.get("property_id"))


@dataclass(frozen=True)
class RegressionEngine:
    """Orchestrates a suite of regression checks against a ``RegressionOracle``.

    The engine holds a tuple of test descriptors (``suite``) and, after running,
    accumulates results (``results``). Because the engine is frozen, each run
    produces a *new* engine instance carrying the updated results.

    Attributes
    ----------
    engine_id : str
        Unique identifier for this engine instance.
    oracle : RegressionOracle
        The oracle providing semantic memory for regression evaluation.
    suite : Tuple[Dict[str, Any], ...]
        Tuple of test descriptors. Each descriptor is a dict with at least
        ``'test_id'``, ``'property_id'``, and ``'current_value'`` (bool).
    results : Tuple[Dict[str, Any], ...]
        Accumulated results from previous ``run_all()`` calls.
    """

    engine_id: str
    oracle: RegressionOracle
    suite: Tuple[Dict[str, Any], ...]
    results: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    def add_test(self, test: Dict[str, Any]) -> "RegressionEngine":
        """Return a new engine with ``test`` appended to the suite."""
        return replace(self, suite=self.suite + (test,))

    def run_all(self) -> "RegressionEngine":
        """Run all tests in the suite and return a new engine carrying results.

        Each test descriptor is evaluated against the oracle. A test passes if
        the oracle has no memory of the property *or* if the property currently
        holds. A test fails if the oracle remembers the property as once holding
        but it does not hold now.
        """
        new_results: List[Dict[str, Any]] = []
        for test in self.suite:
            pid = test.get("property_id", "")
            current_value: bool = bool(test.get("current_value", False))
            memory = self.oracle.query(pid)
            if memory is None:
                outcome = "no_memory"
                passed = True
            elif current_value:
                outcome = "passed"
                passed = True
            else:
                outcome = "regressed"
                passed = False
            result: Dict[str, Any] = {
                "test_id": test.get("test_id", str(uuid.uuid4())),
                "property_id": pid,
                "outcome": outcome,
                "passed": passed,
                "current_value": current_value,
                "memory_tier": memory.get("tier") if memory else None,
                "recorded_at": memory.get("recorded_at") if memory else None,
                "run_at": time.time(),
            }
            new_results.append(result)
        return replace(self, results=self.results + tuple(new_results))

    def report(self) -> Dict[str, Any]:
        """Return a structured report summarising the most recent run."""
        passed = [r for r in self.results if r.get("passed")]
        failed = [r for r in self.results if not r.get("passed")]
        return {
            "engine_id": self.engine_id,
            "total": len(self.results),
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": len(passed) / max(len(self.results), 1),
            "results": list(self.results),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "oracle_id": self.oracle.oracle_id,
            "suite_size": len(self.suite),
            "results_count": len(self.results),
            "suite": list(self.suite),
            "results": list(self.results),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of the engine's state."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.get("passed"))
        failed = total - passed
        return (
            f"RegressionEngine({self.engine_id[:8]}): "
            f"{passed}/{total} passed, {failed} failed "
            f"(oracle={self.oracle.oracle_id[:8]}, suite={len(self.suite)} tests)"
        )

    def failed_tests(self) -> List[Dict[str, Any]]:
        """Return the subset of results that failed."""
        return [r for r in self.results if not r.get("passed")]

    def passed_tests(self) -> List[Dict[str, Any]]:
        """Return the subset of results that passed."""
        return [r for r in self.results if r.get("passed")]


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def check_semantic_regression(
    new_state: Dict[str, Any],
    oracle: RegressionOracle,
) -> List[SemanticRegression]:
    """Check ``new_state`` against the oracle and return any detected regressions.

    This is the primary entry point for comparing a freshly-observed system
    state against the oracle's semantic memory. It delegates to
    :meth:`RegressionOracle.evaluate` and applies mild post-processing to
    sort regressions by severity and age.

    Parameters
    ----------
    new_state:
        A mapping from ``property_id`` (str) to a boolean indicating whether
        the property currently holds.
    oracle:
        The :class:`RegressionOracle` whose memory store will be consulted.

    Returns
    -------
    list[SemanticRegression]
        Detected regressions, ordered by severity (critical first) and then
        by time since the property was previously achieved (oldest first).
    """
    _severity_rank: Dict[str, int] = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }

    regressions = oracle.evaluate(new_state)

    def sort_key(r: SemanticRegression) -> Tuple[int, float]:
        sev = _severity_rank.get(r.severity.lower(), 99)
        return (sev, r.previously_achieved_at)

    regressions.sort(key=sort_key)
    log.debug(
        "check_semantic_regression: %d regression(s) detected for oracle %s",
        len(regressions),
        oracle.oracle_id,
    )
    return regressions


def build_regression_suite(
    memories: List[Dict[str, Any]],
    policy: MemoryRetentionPolicy,
) -> List[Dict[str, Any]]:
    """Build a regression test suite from a list of memory entries.

    For each memory entry that passes the retention policy, a test descriptor
    is emitted. The descriptor's ``'current_value'`` is initialised to
    ``False`` (the caller is expected to fill it in before running the engine).

    Parameters
    ----------
    memories:
        Raw memory entries. Each must have at least ``'property_id'``.
    policy:
        The retention policy used to filter which memories become tests.

    Returns
    -------
    list[dict]
        Test descriptors ready to be loaded into a :class:`RegressionEngine`.
    """
    suite: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    pruned = policy.prune(memories)
    for memory in pruned:
        pid = memory.get("property_id", "")
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        descriptor: Dict[str, Any] = {
            "test_id": f"test-{hashlib.md5(pid.encode()).hexdigest()[:8]}",
            "property_id": pid,
            "description": memory.get("description", pid),
            "current_value": False,
            "tier": memory.get("tier", int(TrustTier.PROPOSAL)),
            "recorded_at": memory.get("recorded_at", 0.0),
            "severity": memory.get("severity", "medium"),
        }
        suite.append(descriptor)

    log.debug("build_regression_suite: produced %d test(s)", len(suite))
    return suite


def retain_semantic_memory(
    achievement: Dict[str, Any],
    policy: MemoryRetentionPolicy,
    store: List[Dict[str, Any]],
) -> bool:
    """Attempt to add ``achievement`` to ``store`` under ``policy``.

    If the policy rejects the achievement, the store is left unmodified and
    ``False`` is returned. Otherwise the achievement is appended, the store is
    pruned in-place, and ``True`` is returned.

    Parameters
    ----------
    achievement:
        A memory entry to potentially add. Must have at least
        ``'property_id'`` and ``'recorded_at'``.
    policy:
        The retention policy that governs admission and pruning.
    store:
        The mutable list representing the current memory store.

    Returns
    -------
    bool
        ``True`` if the achievement was retained; ``False`` if it was rejected.
    """
    if not policy.should_retain(achievement):
        log.debug(
            "retain_semantic_memory: rejected '%s' under policy '%s'",
            achievement.get("property_id"),
            policy.policy_id,
        )
        return False

    store.append(achievement)
    pruned = policy.prune(store)
    store.clear()
    store.extend(pruned)
    log.debug(
        "retain_semantic_memory: retained '%s', store now has %d entries",
        achievement.get("property_id"),
        len(store),
    )
    return True


def run_regression(
    engine: RegressionEngine,
    current_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Run the engine against ``current_state`` and return a structured result.

    This is the high-level façade for a complete regression run. It patches the
    engine's suite with values from ``current_state``, executes all tests, and
    assembles a result dict suitable for logging, CI integration, or UI display.

    Parameters
    ----------
    engine:
        The :class:`RegressionEngine` to run.
    current_state:
        A mapping from ``property_id`` to bool indicating current property values.

    Returns
    -------
    dict
        Keys:
        - ``'regressions'``: list of :class:`SemanticRegression` dicts for failed tests.
        - ``'passed'``: count of passing tests.
        - ``'failed'``: count of failing tests.
        - ``'judgment'``: a high-level verdict string.
        - ``'obstructions'``: list of obstruction descriptions carried in oracle memories.
    """
    patched_suite: Tuple[Dict[str, Any], ...] = tuple(
        {**t, "current_value": current_state.get(t.get("property_id", ""), False)}
        for t in engine.suite
    )
    patched_engine = replace(engine, suite=patched_suite)
    run_engine = patched_engine.run_all()

    report = run_engine.report()
    regressions = check_semantic_regression(current_state, engine.oracle)

    failed_count: int = report["failed"]
    if failed_count == 0:
        judgment = "PASS — all semantic memories retained"
    elif failed_count <= 2:
        judgment = f"WARN — {failed_count} semantic regression(s) detected"
    else:
        judgment = f"FAIL — {failed_count} semantic regression(s) detected"

    obstructions: List[Dict[str, Any]] = []
    for memory in engine.oracle.memory_store:
        if "obstruction" in memory and memory["obstruction"]:
            obstructions.append(memory["obstruction"])

    return {
        "regressions": [r.to_dict() for r in regressions],
        "passed": report["passed"],
        "failed": report["failed"],
        "judgment": judgment,
        "obstructions": obstructions,
        "report": report,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Verify core behaviours of this module with a lightweight self-test."""
    print("=" * 70)
    print("Smoke test: regression_as_semantic_memory_retention")
    print("=" * 70)

    # --- TrustTier operations -----------------------------------------------
    assert TrustTier.join(TrustTier.PROPOSAL, TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert TrustTier.meet(TrustTier.WITNESSED, TrustTier.DISCHARGED) == TrustTier.WITNESSED
    assert TrustTier.PROOF_BACKED.promote() == TrustTier.PROOF_BACKED, "saturates at top"
    assert TrustTier.PROPOSAL.demote() == TrustTier.PROPOSAL, "saturates at bottom"
    assert TrustTier.WITNESSED.promote() == TrustTier.DISCHARGED
    assert TrustTier.DISCHARGED.demote() == TrustTier.WITNESSED
    assert TrustTier.STRONGLY_VERIFIED.label() == "strongly-verified"
    assert TrustTier.DISCHARGED.is_verified()
    assert not TrustTier.WITNESSED.is_verified()
    print("  ✓ TrustTier lattice operations")

    # --- Judgment fingerprint ------------------------------------------------
    j = Judgment(
        judgment_id="j-001",
        property_id="prop.commutativity",
        verdict=True,
        tier=TrustTier.DISCHARGED,
        evidence_kind="solver_proof",
        recorded_at=1_700_000_000.0,
        provenance="solver",
        note="SMT solver discharged in 42ms",
    )
    fp1 = j.fingerprint()
    fp2 = j.fingerprint()
    assert fp1 == fp2, "fingerprint must be deterministic"
    assert len(fp1) == 16
    assert j.is_positive()
    assert j.is_high_confidence()
    print(f"  ✓ Judgment fingerprint: {fp1}")

    # --- CechObstruction -----------------------------------------------------
    obs = CechObstruction(
        obstruction_id="obs-001",
        concept_id="concept.transitivity",
        degree=1,
        cover_elements=("U_alpha", "U_beta", "U_gamma"),
        description="Cocycle condition fails on triple overlap.",
        detected_at=1_700_000_100.0,
    )
    assert obs.cohomological_label() == "H^1(X)"
    assert obs.involves("U_beta")
    assert not obs.involves("U_delta")
    assert not obs.is_resolved()
    resolved_obs = obs.resolve("Identified conflicting axioms and unified.")
    assert resolved_obs.is_resolved()
    assert resolved_obs.resolution_note == "Identified conflicting axioms and unified."
    print(f"  ✓ CechObstruction: {obs.cohomological_label()}")

    # --- SemanticRegression --------------------------------------------------
    reg = SemanticRegression(
        regression_id="reg-001",
        property_id="prop.idempotency",
        property_description="f(f(x)) == f(x) for all x",
        previously_achieved_at=time.time() - 3600,
        detected_at=time.time() - 10,
        severity="high",
        affected_cover_elements=("module.core", "module.algebra"),
    )
    assert not reg.is_critical()
    assert reg.age_seconds() < 60
    assert "HIGH" in reg.summary()
    assert reg.affects("module.core")
    assert not reg.affects("module.network")
    assert reg.time_since_achievement() > 3500
    d = reg.to_dict()
    assert d["severity"] == "high"
    print(f"  ✓ SemanticRegression summary: {reg.summary()[:60]}…")

    # --- MemoryRetentionPolicy -----------------------------------------------
    policy = MemoryRetentionPolicy(
        policy_id="policy-default",
        retain_verified_only=True,
        max_age_seconds=7200.0,
        max_entries=5,
    )
    assert policy.is_strict()

    now = time.time()
    memories = [
        {"property_id": f"prop.{i}", "tier": int(TrustTier.DISCHARGED), "recorded_at": now - i * 100}
        for i in range(10)
    ]
    pruned = policy.prune(memories)
    assert len(pruned) <= 5, f"Expected ≤5 entries after pruning, got {len(pruned)}"

    weak_memory = {"property_id": "prop.weak", "tier": int(TrustTier.PROPOSAL), "recorded_at": now}
    assert not policy.should_retain(weak_memory), "Policy should reject PROPOSAL-tier memory"

    pf_policy = policy.with_priority_filter(frozenset(["prop.important"]))
    assert pf_policy.priority_filter == frozenset(["prop.important"])
    print(f"  ✓ MemoryRetentionPolicy: pruned {len(memories)} → {len(pruned)}")

    # --- ClosureRegression ---------------------------------------------------
    cr = ClosureRegression(
        closure_regression_id="cr-001",
        concept_id="concept.associativity",
        closed_at=now - 500,
        reopened_at=now - 100,
        cause="Refactor introduced non-associative operator",
        obstruction=obs,
    )
    assert cr.has_obstruction()
    assert abs(cr.duration_closed() - 400.0) < 5.0
    assert cr.obstruction_degree() == 1
    crd = cr.to_dict()
    assert crd["has_obstruction"] is True
    cr_summary = cr.summary()
    assert "concept.associativity" in cr_summary
    print(f"  ✓ ClosureRegression duration_closed: {cr.duration_closed():.1f}s")

    # --- RegressionOracle and check_semantic_regression ----------------------
    store_memories = [
        {
            "property_id": "prop.commutativity",
            "description": "a + b == b + a",
            "tier": int(TrustTier.DISCHARGED),
            "recorded_at": now - 200,
            "severity": "high",
            "cover_elements": ["algebra"],
        },
        {
            "property_id": "prop.associativity",
            "description": "(a + b) + c == a + (b + c)",
            "tier": int(TrustTier.WITNESSED),
            "recorded_at": now - 300,
            "severity": "medium",
            "cover_elements": ["algebra"],
        },
        {
            "property_id": "prop.identity",
            "description": "a + 0 == a",
            "tier": int(TrustTier.STRONGLY_VERIFIED),
            "recorded_at": now - 100,
            "severity": "critical",
            "cover_elements": ["algebra", "core"],
        },
    ]
    open_policy = MemoryRetentionPolicy(policy_id="open", retain_verified_only=False)
    oracle = RegressionOracle(
        oracle_id="oracle-001",
        memory_store=tuple(store_memories),
        policy=open_policy,
    )
    assert oracle.memory_size() == 3
    q = oracle.query("prop.commutativity")
    assert q is not None and q["property_id"] == "prop.commutativity"
    assert oracle.query("prop.nonexistent") is None
    assert "prop.commutativity" in oracle.property_ids()

    oracle2 = oracle.with_memory(({
        "property_id": "prop.distributivity",
        "tier": int(TrustTier.WITNESSED),
        "recorded_at": now,
        "severity": "low",
        "description": "a * (b + c) == a*b + a*c",
    },))
    assert oracle2.memory_size() == 4

    current_state: Dict[str, bool] = {
        "prop.commutativity": True,
        "prop.associativity": False,
        "prop.identity": True,
    }
    regressions = check_semantic_regression(current_state, oracle)
    assert len(regressions) == 1
    assert regressions[0].property_id == "prop.associativity"
    print(f"  ✓ check_semantic_regression: {len(regressions)} regression detected")

    # --- build_regression_suite and RegressionEngine -------------------------
    suite_tests = build_regression_suite(store_memories, open_policy)
    assert len(suite_tests) == 3, f"Expected 3 tests, got {len(suite_tests)}"

    engine = RegressionEngine(
        engine_id="engine-001",
        oracle=oracle,
        suite=tuple(suite_tests),
    )
    engine = engine.add_test({
        "test_id": "test-extra",
        "property_id": "prop.distributivity",
        "current_value": True,
    })
    assert len(engine.suite) == 4

    result = run_regression(engine, current_state)
    assert result["passed"] >= 2
    assert result["failed"] >= 1
    assert "regressions" in result
    assert "judgment" in result
    assert "obstructions" in result
    print(f"  ✓ run_regression: passed={result['passed']}, failed={result['failed']}")
    print(f"  ✓ Judgment: {result['judgment']}")

    # --- retain_semantic_memory ----------------------------------------------
    mutable_store: List[Dict[str, Any]] = list(store_memories[:2])
    new_achievement = {
        "property_id": "prop.closure",
        "tier": int(TrustTier.DISCHARGED),
        "recorded_at": time.time(),
        "severity": "low",
        "description": "Set is closed under the binary operation",
    }
    accepted = retain_semantic_memory(new_achievement, open_policy, mutable_store)
    assert accepted
    assert any(m["property_id"] == "prop.closure" for m in mutable_store)

    rejected_achievement = {
        "property_id": "prop.too_old",
        "tier": int(TrustTier.PROPOSAL),
        "recorded_at": time.time() - 99999,
        "severity": "low",
    }
    strict_policy = MemoryRetentionPolicy(
        policy_id="strict",
        retain_verified_only=True,
        max_age_seconds=1000.0,
    )
    rejected = retain_semantic_memory(rejected_achievement, strict_policy, mutable_store)
    assert not rejected
    print(f"  ✓ retain_semantic_memory: accepted={accepted}, rejected-old={not rejected}")

    # --- engine summary and report -------------------------------------------
    run_engine = engine.run_all()
    print(f"  ✓ Engine summary: {run_engine.summary()}")
    report_out = run_engine.report()
    assert "pass_rate" in report_out
    assert 0.0 <= report_out["pass_rate"] <= 1.0
    assert len(run_engine.failed_tests()) + len(run_engine.passed_tests()) == len(run_engine.results)

    print()
    print("All smoke tests passed.")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _smoke_test()
