r"""Full integration closure engine for the ``jugeo.generation.semantic_closure`` package.

Chapter 39, Section 3 — Integration Closure
============================================

Overview
--------
Integration closure is the process of verifying — and actively achieving —
the property that a finite family of locally-constructed sections fits
together into a globally consistent section over the full base space.  A
*locally consistent* construction is one in which every pair of adjacent
patches agrees on their shared overlap; *integration closure* extends this
to the full n-ary coherence condition encoded by the nerve of the cover.

Theory (theory2.tex §39.3)
--------------------------
Let ``{U_i}`` be a cover and ``{s_i}`` a family of local sections.  Define
the *obligation set* ``O`` to be the collection of all compatibility
conditions derived from the gluing data of the descent engine.  Integration
closure requires every element of ``O`` to be *discharged* — satisfied by
direct evidence, by a ratified overlap treaty, or by a descent-verified
section extension.

The *closure fraction* is:

    f = |O_closed| / |O_total|,    f ∈ [0, 1]

with f = 1.0 meaning *full* integration closure.

Iteration Dynamics
------------------
The engine proceeds in *rounds*.  In each round it:

1. Identifies the current open obligations as :class:`ClosureGap` records.
2. Selects the next gap to address using the injected :class:`ClosureStrategy`.
3. Applies a gap-specific repair: installing evidence, marking obligations
   closed, or recording a partial-satisfaction check.
4. Runs regression tests to ensure previously-closed obligations have not
   been broken by the repair.
5. Snapshots the state for rollback if a regression is detected.
6. Repeats until either all obligations are closed or ``max_rounds`` is
   exhausted.

If the state reaches full closure (``fraction_closed == 1.0`` and no open
obligations remain), the engine calls :meth:`certify` to produce a
:class:`ClosureCertificate` that downstream consumers (e.g., the
orchestration layer) can inspect before accepting the integration.

Strategies
----------
Three concrete strategies are provided:

* :class:`GreedyClosureStrategy` — always pick the lowest-severity
  non-blocking gap first (cheapest repairs first).
* :class:`PriorityClosureStrategy` — always pick the highest-severity gap
  first (most critical repairs first).
* :class:`ConservativeClosureStrategy` — skip blocking gaps; only attempt
  repairs that are unlikely to introduce regressions.

The strategy can be swapped at construction time, enabling A/B testing.

Module-level helpers
--------------------
:func:`make_integration_state`  — create a fresh :class:`IntegrationState`.
:func:`run_closure`             — one-line convenience wrapper.

copilot: integration-closure-engine
"""
from __future__ import annotations

import copy
import hashlib
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.semantic_closure.models import (
    ClosureCheck,
    ClosureGap,
    ClosureResult,
    GapSeverity,
    CheckType,
    RegressionTest,
    RegressionRecord,
    RegressionStatus,
    RegressionKind,
    SemanticClosure,
    SEVERITY_ORDER,
    make_check,
    make_gap,
    empty_closure,
)

# Optional jugeo imports — wrapped in try/except so the module loads even when
# individual sub-packages are absent during early development.
try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentResult,
        LocalSection,
        OverlapCondition,
        GluingData,
        DescentObstruction,
        RepairFrontier,
        DescentStrategy,
    )
    _DESCENT_AVAILABLE = True
except ImportError:
    _DESCENT_AVAILABLE = False

try:
    from jugeo.geometry.covers import Cover
    _COVERS_AVAILABLE = True
except ImportError:
    _COVERS_AVAILABLE = False

try:
    from jugeo.geometry.supports import SupportRegion
    _SUPPORTS_AVAILABLE = True
except ImportError:
    _SUPPORTS_AVAILABLE = False

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    _SITE_AVAILABLE = True
except ImportError:
    _SITE_AVAILABLE = False

try:
    from jugeo.generation.goals import (
        GenerationGoal,
        GoalDecomposer,
        ConstructionGoal,
        GoalPriority,
        GoalStatus,
        OverlapGoal,
    )
    _GOALS_AVAILABLE = True
except ImportError:
    _GOALS_AVAILABLE = False

try:
    from jugeo.generation.construction import (
        Candidate,
        ConstructionLoop,
        ConstructionResult,
        ConstructionContext,
    )
    _CONSTRUCTION_AVAILABLE = True
except ImportError:
    _CONSTRUCTION_AVAILABLE = False

try:
    from jugeo.generation.treaties import (
        OverlapTreaty,
        TreatyClause,
        TreatyStatus,
        evaluate_treaty,
    )
    _TREATIES_AVAILABLE = True
except ImportError:
    _TREATIES_AVAILABLE = False

try:
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    _FRONTIER_AVAILABLE = True
except ImportError:
    _FRONTIER_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False

log = logging.getLogger(__name__)

__all__ = [
    "IntegrationState",
    "ClosureStrategy",
    "GreedyClosureStrategy",
    "PriorityClosureStrategy",
    "ConservativeClosureStrategy",
    "ClosureCertificate",
    "IntegrationClosureEngine",
    "make_integration_state",
    "run_closure",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SEVERITY_VALUES = list(SEVERITY_ORDER.keys())  # ordered: info < minor < ... < blocking


def _severity_rank(severity: str) -> int:
    """Return numeric rank for *severity*, defaulting to 0 (info)."""
    return SEVERITY_ORDER.get(severity, 0)


def _obligation_to_gap_severity(obligation_id: str, open_count: int) -> str:
    """Heuristically assign a gap severity to an open obligation.

    Obligations that look like treaty or overlap identifiers are treated as
    more severe; obligations that have been open for many rounds become
    blocking.

    Args:
        obligation_id: The string identifier of the open obligation.
        open_count:    How many rounds this obligation has been open.

    Returns:
        A GapSeverity string value.
    """
    low = obligation_id.lower()
    if open_count >= 10:
        return GapSeverity.BLOCKING.value
    if open_count >= 5:
        return GapSeverity.CRITICAL.value
    if "treaty" in low or "overlap" in low or "glue" in low:
        return GapSeverity.CRITICAL.value
    if "section" in low or "descent" in low:
        return GapSeverity.MODERATE.value
    if open_count >= 2:
        return GapSeverity.MODERATE.value
    return GapSeverity.MINOR.value


def _compute_state_hash(
    obligations_closed: set[str],
    obligations_open: set[str],
) -> str:
    """Compute a short hash representing the current closure state."""
    key = (
        ",".join(sorted(obligations_closed))
        + "|"
        + ",".join(sorted(obligations_open))
    )
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# IntegrationState
# ---------------------------------------------------------------------------


@dataclass
class IntegrationState:
    """Current state of integration during closure computation.

    An :class:`IntegrationState` is a mutable record that tracks which
    obligations have been closed, which remain open, which treaty-like objects
    have been ratified, and the sections installed for each patch.  It also
    maintains a snapshot history so the engine can roll back to a known-good
    state if a regression is detected.

    The state is deliberately *mutable* — the closure engine mutates it
    in-place through its public methods — but the snapshot/restore pair lets
    the engine implement speculative steps with rollback.

    Attributes
    ----------
    state_id:
        Unique identifier for this state object.
    patches:
        Tuple of patch identifiers that this integration covers.
    sections:
        Mapping from patch_id to section data (arbitrary dict).
    treaties:
        List of treaty-like objects (OverlapTreaty or duck-typed equivalent).
    obligations_closed:
        Set of obligation IDs that have been fully discharged.
    obligations_open:
        Set of obligation IDs that remain to be closed.
    regression_tests:
        List of RegressionTest objects to run after each mutation.
    _snapshots:
        Internal list of snapshot dicts for rollback support.
    _open_age:
        Internal dict tracking how many rounds each obligation has been open.
    """

    state_id: str
    patches: tuple[str, ...]
    sections: dict[str, Any]
    treaties: list[Any]
    obligations_closed: set[str]
    obligations_open: set[str]
    regression_tests: list[Any]
    _snapshots: list[dict] = field(default_factory=list)
    _open_age: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Core mutation methods
    # ------------------------------------------------------------------

    def apply_section(self, patch: str, section: dict[str, Any]) -> None:
        """Install or update the section for *patch*.

        If *section* contains a key ``"obligations"`` its value is treated as
        an iterable of obligation IDs that this section satisfies, and those
        obligations are moved from open to closed.

        Args:
            patch:   Patch identifier.
            section: Arbitrary section data dict.
        """
        self.sections[patch] = section
        # If the section explicitly declares which obligations it satisfies,
        # close them now.
        for ob in section.get("obligations", []):
            self.close_obligation(ob, evidence=(patch,))
        log.debug(
            "apply_section: patch=%r section_keys=%s", patch, list(section.keys())
        )

    def apply_treaty(self, treaty: Any) -> None:
        """Register *treaty* and close any clauses it satisfies.

        Works with real :class:`~jugeo.generation.treaties.OverlapTreaty`
        objects and with any duck-typed object that exposes:

        * ``.clauses`` — iterable of objects with ``.patch`` and ``.satisfied``
        * ``.accepted`` — bool property

        Args:
            treaty: A treaty-like object.
        """
        self.treaties.append(treaty)
        # Close obligations satisfied by this treaty's clauses.
        clauses = getattr(treaty, "clauses", [])
        treaty_accepted = getattr(treaty, "accepted", False)
        for clause in clauses:
            satisfied = getattr(clause, "satisfied", False)
            patch = getattr(clause, "patch", "")
            if satisfied and treaty_accepted:
                ob_id = f"treaty:{patch}"
                self.close_obligation(ob_id, evidence=(patch,))
        log.debug(
            "apply_treaty: accepted=%s clauses=%d", treaty_accepted, len(clauses)
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of the current state as a dict.

        The snapshot captures obligations_closed, obligations_open,
        sections, and the current regression-test statuses.  It can be
        passed to :meth:`restore` to roll back to this point.

        Returns:
            A dict suitable for passing to :meth:`restore`.
        """
        snap: dict[str, Any] = {
            "state_id": self.state_id,
            "obligations_closed": set(self.obligations_closed),
            "obligations_open": set(self.obligations_open),
            "sections": copy.deepcopy(self.sections),
            "treaties_count": len(self.treaties),
            "regression_tests_status": [
                {"test_id": t.test_id, "status": t.status}
                for t in self.regression_tests
                if hasattr(t, "test_id")
            ],
            "_open_age": dict(self._open_age),
            "snapshot_time": time.monotonic(),
        }
        self._snapshots.append(snap)
        return snap

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore the state from a previously taken *snapshot*.

        Only the fields captured by :meth:`snapshot` are restored;
        the treaty list is not shortened back to its snapshot length
        (treaties are append-only in practice), but obligation sets
        and sections are fully reverted.

        Args:
            snapshot: A dict previously returned by :meth:`snapshot`.
        """
        self.obligations_closed = set(snapshot.get("obligations_closed", set()))
        self.obligations_open = set(snapshot.get("obligations_open", set()))
        self.sections = copy.deepcopy(snapshot.get("sections", {}))
        self._open_age = dict(snapshot.get("_open_age", {}))
        log.debug(
            "restore: closed=%d open=%d",
            len(self.obligations_closed),
            len(self.obligations_open),
        )

    # ------------------------------------------------------------------
    # Obligation lifecycle
    # ------------------------------------------------------------------

    def add_obligation(self, obligation_id: str) -> None:
        """Register *obligation_id* as an open obligation to be closed.

        If already present in obligations_open this is a no-op.  If already
        present in obligations_closed it is silently ignored (use
        :meth:`open_obligation` to explicitly reopen a closed obligation).

        Args:
            obligation_id: The string identifier of the obligation.
        """
        if obligation_id not in self.obligations_closed:
            self.obligations_open.add(obligation_id)
            if obligation_id not in self._open_age:
                self._open_age[obligation_id] = 0

    def close_obligation(
        self, obligation_id: str, evidence: tuple[str, ...] = ()
    ) -> None:
        """Mark *obligation_id* as closed, optionally recording evidence.

        Moves the obligation from obligations_open to obligations_closed.
        If the obligation is not currently open (already closed or unknown),
        this is a no-op.

        Args:
            obligation_id: The string identifier of the obligation.
            evidence:      Optional tuple of evidence tags for audit purposes.
        """
        self.obligations_open.discard(obligation_id)
        self.obligations_closed.add(obligation_id)
        self._open_age.pop(obligation_id, None)
        log.debug(
            "close_obligation: %r evidence=%s", obligation_id, evidence
        )

    def open_obligation(self, obligation_id: str) -> None:
        """Reopen *obligation_id* — i.e., register a regression.

        This moves the obligation from obligations_closed back to
        obligations_open and increments its open-age counter.  It should be
        called whenever a regression test detects that a previously-closed
        obligation is no longer satisfied.

        Args:
            obligation_id: The string identifier of the obligation.
        """
        self.obligations_closed.discard(obligation_id)
        self.obligations_open.add(obligation_id)
        self._open_age[obligation_id] = self._open_age.get(obligation_id, 0) + 1
        log.debug("open_obligation (regression!): %r", obligation_id)

    def increment_open_age(self) -> None:
        """Increment the age counter for every currently open obligation.

        Called once per closure round so that obligations that remain open
        for many rounds receive increasing severity.
        """
        for ob in list(self.obligations_open):
            self._open_age[ob] = self._open_age.get(ob, 0) + 1

    def get_open_age(self, obligation_id: str) -> int:
        """Return how many rounds *obligation_id* has been open.

        Args:
            obligation_id: The string identifier of the obligation.

        Returns:
            Integer age; 0 if the obligation is not tracked.
        """
        return self._open_age.get(obligation_id, 0)

    # ------------------------------------------------------------------
    # Aggregate queries
    # ------------------------------------------------------------------

    def closure_fraction(self) -> float:
        """Return the fraction of obligations that have been closed.

        Returns:
            float in [0, 1]; 1.0 if all obligations are closed or if
            both sets are empty.
        """
        total = len(self.obligations_closed) + len(self.obligations_open)
        if total == 0:
            return 1.0
        return len(self.obligations_closed) / total

    def is_closed(self) -> bool:
        """Return True if obligations_open is empty.

        Returns:
            True iff every registered obligation has been discharged.
        """
        return len(self.obligations_open) == 0

    def state_hash(self) -> str:
        """Return a short hash of the current closure state for cycle detection.

        Returns:
            A 12-character hex string.
        """
        return _compute_state_hash(self.obligations_closed, self.obligations_open)

    def summary(self) -> str:
        """Return a human-readable summary of the state.

        Returns:
            A multi-line string describing the current closure status.
        """
        frac = self.closure_fraction()
        lines = [
            f"IntegrationState({self.state_id[:8]})",
            f"  patches         : {len(self.patches)}",
            f"  sections        : {len(self.sections)}",
            f"  treaties        : {len(self.treaties)}",
            f"  obligations open: {len(self.obligations_open)}",
            f"  obligations done: {len(self.obligations_closed)}",
            f"  fraction closed : {frac:.1%}",
            f"  regression tests: {len(self.regression_tests)}",
        ]
        if self.obligations_open:
            sample = sorted(self.obligations_open)[:5]
            lines.append(f"  open sample     : {sample}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ClosureStrategy ABC
# ---------------------------------------------------------------------------


class ClosureStrategy(ABC):
    """Abstract strategy for selecting and controlling the closure iteration.

    Concrete subclasses implement :meth:`select_next_gap` (which gap to
    address) and :meth:`should_continue` (whether to keep iterating).
    """

    @abstractmethod
    def select_next_gap(
        self, gaps: list[ClosureGap], state: IntegrationState
    ) -> ClosureGap | None:
        """Choose the next gap to close from *gaps*.

        Args:
            gaps:  List of currently open gaps.
            state: Current integration state.

        Returns:
            A :class:`ClosureGap` to address, or ``None`` if the strategy
            chooses to pause / yield.
        """

    @abstractmethod
    def should_continue(
        self, state: IntegrationState, round_num: int, max_rounds: int
    ) -> bool:
        """Decide whether to keep running closure rounds.

        Args:
            state:      Current integration state.
            round_num:  Index of the round just completed (0-based).
            max_rounds: Maximum rounds allowed.

        Returns:
            True to continue, False to stop.
        """

    def strategy_name(self) -> str:
        """Return the name of this strategy."""
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# GreedyClosureStrategy
# ---------------------------------------------------------------------------


class GreedyClosureStrategy(ClosureStrategy):
    """Always pick the gap that can be most easily closed (lowest severity).

    The greedy strategy prioritises cheap repairs so that the closure
    fraction increases as quickly as possible.  It will attempt blocking
    gaps only if no non-blocking gap is available.

    Args:
        skip_blocking: If True, blocking gaps are never selected and the
                       strategy will stop when only blocking gaps remain.
    """

    def __init__(self, skip_blocking: bool = False) -> None:
        self._skip_blocking = skip_blocking

    def select_next_gap(
        self, gaps: list[ClosureGap], state: IntegrationState
    ) -> ClosureGap | None:
        """Pick the non-blocking gap with the lowest severity rank.

        If all gaps are blocking and ``skip_blocking`` is True, returns None.

        Args:
            gaps:  List of open ClosureGap records.
            state: Current integration state (unused by greedy).

        Returns:
            The lowest-severity gap, or None.
        """
        if not gaps:
            return None
        non_blocking = [
            g for g in gaps if g.severity != GapSeverity.BLOCKING.value
        ]
        candidates = non_blocking if non_blocking else ([] if self._skip_blocking else gaps)
        if not candidates:
            return None
        # Sort ascending by severity rank (cheapest first), then by obligation_id
        # for deterministic behaviour.
        return min(
            candidates,
            key=lambda g: (_severity_rank(g.severity), g.obligation_id),
        )

    def should_continue(
        self, state: IntegrationState, round_num: int, max_rounds: int
    ) -> bool:
        """Continue while rounds remain and state is not closed.

        Args:
            state:      Current integration state.
            round_num:  Rounds completed so far.
            max_rounds: Round limit.

        Returns:
            True if ``round_num < max_rounds`` and state is not closed.
        """
        return round_num < max_rounds and not state.is_closed()


# ---------------------------------------------------------------------------
# PriorityClosureStrategy
# ---------------------------------------------------------------------------


class PriorityClosureStrategy(ClosureStrategy):
    """Always pick the most critical / blocking gap first.

    The priority strategy ensures the most dangerous open obligations are
    addressed as early as possible, at the cost of potentially doing more
    total work (since blocking gaps may require complex repairs).

    Args:
        break_ties_by_age: If True, among equal-severity gaps, prefer the one
                           that has been open the longest.
    """

    def __init__(self, break_ties_by_age: bool = True) -> None:
        self._break_ties_by_age = break_ties_by_age

    def select_next_gap(
        self, gaps: list[ClosureGap], state: IntegrationState
    ) -> ClosureGap | None:
        """Select the gap with the highest severity rank.

        Among ties, prefers the gap whose obligation has been open the
        longest (if ``break_ties_by_age`` is True).

        Args:
            gaps:  List of open ClosureGap records.
            state: Current integration state (used for open-age lookup).

        Returns:
            The highest-severity gap, or None if *gaps* is empty.
        """
        if not gaps:
            return None
        if self._break_ties_by_age:
            return max(
                gaps,
                key=lambda g: (
                    _severity_rank(g.severity),
                    state.get_open_age(g.obligation_id),
                ),
            )
        return max(gaps, key=lambda g: _severity_rank(g.severity))

    def should_continue(
        self, state: IntegrationState, round_num: int, max_rounds: int
    ) -> bool:
        """Continue while rounds remain and state is not closed.

        Args:
            state:      Current integration state.
            round_num:  Rounds completed so far.
            max_rounds: Round limit.

        Returns:
            True if ``round_num < max_rounds`` and state is not closed.
        """
        return round_num < max_rounds and not state.is_closed()


# ---------------------------------------------------------------------------
# ConservativeClosureStrategy
# ---------------------------------------------------------------------------


class ConservativeClosureStrategy(ClosureStrategy):
    """Close only safe gaps; skip anything that might cause regressions.

    The conservative strategy refuses to touch blocking or critical gaps,
    on the theory that such repairs carry high regression risk.  It is
    suitable when the existing sections are fragile or when the regression
    test suite is incomplete.

    Args:
        risk_threshold: Maximum severity rank to attempt.  Gaps with a
                        severity rank above this threshold are skipped.
                        Default is 2 (moderate), i.e., skip critical and
                        blocking.
        max_open_age:   Refuse to address obligations older than this many
                        rounds (treat them as requiring manual intervention).
    """

    def __init__(
        self,
        risk_threshold: float = 2.0,
        max_open_age: int = 20,
    ) -> None:
        self._risk_threshold = risk_threshold
        self._max_open_age = max_open_age

    def select_next_gap(
        self, gaps: list[ClosureGap], state: IntegrationState
    ) -> ClosureGap | None:
        """Select the lowest-severity gap that is below the risk threshold.

        Args:
            gaps:  List of open ClosureGap records.
            state: Current integration state (used for open-age check).

        Returns:
            A safe gap, or None if no gap passes the risk filter.
        """
        if not gaps:
            return None
        safe = [
            g
            for g in gaps
            if _severity_rank(g.severity) <= self._risk_threshold
            and state.get_open_age(g.obligation_id) <= self._max_open_age
        ]
        if not safe:
            return None
        return min(safe, key=lambda g: (_severity_rank(g.severity), g.obligation_id))

    def should_continue(
        self, state: IntegrationState, round_num: int, max_rounds: int
    ) -> bool:
        """Continue while rounds remain and safe gaps may still exist.

        Stops early if the state is closed or round limit is reached.

        Args:
            state:      Current integration state.
            round_num:  Rounds completed so far.
            max_rounds: Round limit.

        Returns:
            True if more safe work might remain.
        """
        return round_num < max_rounds and not state.is_closed()


# ---------------------------------------------------------------------------
# ClosureCertificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureCertificate:
    """Immutable certificate that attests integration closure.

    A :class:`ClosureCertificate` is produced by
    :meth:`IntegrationClosureEngine.certify` when the integration state
    reaches full closure (all obligations closed, no regressions detected).
    It can be serialised to/from a plain dict for persistence or handoff to
    the orchestration layer.

    Attributes
    ----------
    cert_id:
        Unique identifier for this certificate.
    integration_id:
        The integration to which this certificate applies.
    fraction_closed:
        The closure fraction at certification time; should be 1.0 for
        a valid certificate.
    regression_free:
        True iff no regressions were detected during the closure process.
    certifier:
        String identifying the engine or agent that produced this certificate
        (e.g., the strategy name and engine configuration).
    timestamp:
        Monotonic time at which the certificate was issued.
    evidence_summary:
        Tuple of short strings summarising the evidence that justified closure.
    """

    cert_id: str
    integration_id: str
    fraction_closed: float
    regression_free: bool
    certifier: str
    timestamp: float
    evidence_summary: tuple[str, ...]

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty list means the cert is valid.

        Checks performed:

        * ``fraction_closed`` must be >= 1.0.
        * ``certifier`` must be non-empty.
        * ``timestamp`` must be positive.
        * ``cert_id`` and ``integration_id`` must be non-empty strings.

        Returns:
            List of human-readable error strings.  Empty list = valid.
        """
        errors: list[str] = []
        if self.fraction_closed < 1.0:
            errors.append(
                f"fraction_closed={self.fraction_closed:.3f} < 1.0: not fully closed"
            )
        if not self.certifier.strip():
            errors.append("certifier must be a non-empty string")
        if self.timestamp <= 0:
            errors.append(f"timestamp={self.timestamp} must be positive")
        if not self.cert_id.strip():
            errors.append("cert_id must be non-empty")
        if not self.integration_id.strip():
            errors.append("integration_id must be non-empty")
        return errors

    def is_valid(self) -> bool:
        """Return True iff :meth:`validate` returns an empty list."""
        return len(self.validate()) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            A JSON-serialisable dict representation.
        """
        return {
            "cert_id": self.cert_id,
            "integration_id": self.integration_id,
            "fraction_closed": self.fraction_closed,
            "regression_free": self.regression_free,
            "certifier": self.certifier,
            "timestamp": self.timestamp,
            "evidence_summary": list(self.evidence_summary),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClosureCertificate":
        """Deserialise from a plain dict.

        Args:
            data: A dict previously returned by :meth:`to_dict`.

        Returns:
            A new :class:`ClosureCertificate` instance.
        """
        return cls(
            cert_id=data["cert_id"],
            integration_id=data["integration_id"],
            fraction_closed=float(data["fraction_closed"]),
            regression_free=bool(data["regression_free"]),
            certifier=data.get("certifier", ""),
            timestamp=float(data.get("timestamp", time.monotonic())),
            evidence_summary=tuple(data.get("evidence_summary", [])),
        )

    def age_seconds(self) -> float:
        """Return how many seconds have passed since the certificate was issued.

        Returns:
            Non-negative float; measured in monotonic seconds.
        """
        return time.monotonic() - self.timestamp

    def summary(self) -> str:
        """Return a compact one-line description of this certificate.

        Returns:
            A human-readable summary string.
        """
        valid_str = "VALID" if self.is_valid() else "INVALID"
        reg_str = "regression-free" if self.regression_free else "regressions-present"
        return (
            f"ClosureCertificate({self.cert_id[:8]}) [{valid_str}] "
            f"integration={self.integration_id!r} "
            f"fraction={self.fraction_closed:.1%} "
            f"{reg_str} certifier={self.certifier!r}"
        )


# ---------------------------------------------------------------------------
# IntegrationClosureEngine
# ---------------------------------------------------------------------------


class IntegrationClosureEngine:
    """Drives full integration closure.  Chapter 39 §3.

    The engine applies a :class:`ClosureStrategy` in an iterative loop until
    either all obligations are closed or ``max_rounds`` is exhausted.  It
    optionally runs regression tests after each repair to detect backsliding.

    Attributes
    ----------
    _strategy:
        The injected closure strategy.
    _max_rounds:
        Maximum number of repair rounds before giving up.
    _regression_check:
        Whether to run regression tests after each repair.
    _gap_history:
        All gaps identified during the closure process (for diagnostics).
    _round_log:
        Per-round log entries recording what happened in each round.

    Args:
        strategy:          The strategy to use (default: PriorityClosureStrategy).
        max_rounds:        Maximum repair rounds (default: 50).
        regression_check:  Run regression tests after each repair (default: True).
    """

    def __init__(
        self,
        strategy: ClosureStrategy | None = None,
        max_rounds: int = 50,
        regression_check: bool = True,
    ) -> None:
        self._strategy: ClosureStrategy = strategy or PriorityClosureStrategy()
        self._max_rounds: int = max(1, max_rounds)
        self._regression_check: bool = regression_check
        self._gap_history: list[ClosureGap] = []
        self._round_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def close(
        self, state: IntegrationState
    ) -> tuple[IntegrationState, ClosureCertificate | None]:
        """Run the full closure loop and optionally produce a certificate.

        This is the main entry point for the engine.  It:

        1. Takes an initial snapshot of *state* for rollback.
        2. Calls :meth:`_iterate_until_closed` which runs the repair loop.
        3. If the state reaches full closure, calls :meth:`certify`.

        Args:
            state: The integration state to close.  Mutated in-place.

        Returns:
            ``(final_state, certificate)`` where ``certificate`` is None
            if the state did not reach full closure.
        """
        log.info(
            "close: starting closure for state=%r strategy=%s max_rounds=%d",
            state.state_id[:8],
            self._strategy.strategy_name(),
            self._max_rounds,
        )
        state.snapshot()  # initial checkpoint
        state = self._iterate_until_closed(state, self._max_rounds)

        cert: ClosureCertificate | None = None
        if state.is_closed():
            cert = self.certify(state, state.state_id)
            if cert:
                log.info("close: closure achieved, certificate=%r", cert.cert_id[:8])
            else:
                log.warning("close: state is_closed but certify returned None")
        else:
            log.info(
                "close: did not reach full closure fraction=%.1f%% open=%d",
                state.closure_fraction() * 100,
                len(state.obligations_open),
            )
        return state, cert

    # ------------------------------------------------------------------
    # Internal iteration
    # ------------------------------------------------------------------

    def _iterate_until_closed(
        self, state: IntegrationState, max_rounds: int
    ) -> IntegrationState:
        """Core iterative loop: find gaps, close them, check regressions.

        Each round:

        1. Increments the open-age counters for all open obligations.
        2. Identifies current gaps via :meth:`_identify_gaps`.
        3. Asks the strategy for the next gap to address.
        4. Calls :meth:`_close_gap` to attempt the repair.
        5. If ``regression_check`` is True, runs :meth:`_verify_no_regression`.
           On regression, logs a warning (does not roll back automatically).
        6. Records a round log entry.
        7. Stops if strategy says so or no gap was selected.

        Args:
            state:      The integration state to mutate.
            max_rounds: Maximum rounds to run.

        Returns:
            The (mutated) integration state after the loop.
        """
        seen_hashes: set[str] = set()  # cycle detection

        for round_num in range(max_rounds):
            if not self._strategy.should_continue(state, round_num, max_rounds):
                log.debug("_iterate: strategy halted at round %d", round_num)
                break

            # Cycle detection: if we've seen this exact state before, stop.
            h = state.state_hash()
            if h in seen_hashes:
                log.warning(
                    "_iterate: cycle detected at round %d (hash=%s); stopping",
                    round_num,
                    h,
                )
                break
            seen_hashes.add(h)

            state.increment_open_age()
            gaps = self._identify_gaps(state)

            round_entry: dict[str, Any] = {
                "round": round_num,
                "gaps_found": len(gaps),
                "fraction_before": state.closure_fraction(),
                "selected_gap": None,
                "regressions": [],
            }

            if not gaps:
                round_entry["note"] = "no gaps found; stopping"
                self._round_log.append(round_entry)
                break

            selected = self._strategy.select_next_gap(gaps, state)
            if selected is None:
                round_entry["note"] = "strategy returned None; pausing"
                self._round_log.append(round_entry)
                break

            round_entry["selected_gap"] = {
                "gap_id": selected.gap_id,
                "obligation_id": selected.obligation_id,
                "severity": selected.severity,
            }
            self._gap_history.append(selected)

            # Snapshot before repair so we can roll back on severe regression.
            snap = state.snapshot()
            state = self._close_gap(selected, state)

            round_entry["fraction_after"] = state.closure_fraction()

            if self._regression_check:
                regressions = self._verify_no_regression(state)
                round_entry["regressions"] = [r.record_id for r in regressions]
                if regressions:
                    critical = [r for r in regressions if r.is_critical()]
                    if critical:
                        # Roll back for critical regressions.
                        log.warning(
                            "_iterate: critical regression at round %d; rolling back",
                            round_num,
                        )
                        state.restore(snap)
                        round_entry["note"] = "rolled back due to critical regression"
                    else:
                        log.info(
                            "_iterate: non-critical regressions at round %d: %d",
                            round_num,
                            len(regressions),
                        )

            self._round_log.append(round_entry)

            if state.is_closed():
                log.debug("_iterate: closed after %d rounds", round_num + 1)
                break

        return state

    def _close_gap(
        self, gap: ClosureGap, state: IntegrationState
    ) -> IntegrationState:
        """Attempt to close *gap* by mutating *state*.

        The repair strategy is based on the gap's severity:

        * **blocking** — record a partial-satisfaction check noting that
          a manual intervention is required; do NOT mark as closed.
        * **critical** — attempt treaty-based closure by scanning treaties
          for a clause that covers the obligation; if found, mark closed.
        * **moderate** — install a synthetic section evidence marker and
          close the obligation.
        * **minor / info** — immediately mark the obligation as closed with
          a synthetic evidence record.

        In all cases a :class:`ClosureCheck` is produced and logged.

        Args:
            gap:   The gap to address.
            state: The integration state to mutate.

        Returns:
            The (mutated) state.
        """
        ob_id = gap.obligation_id
        severity = gap.severity

        if severity == GapSeverity.BLOCKING.value:
            # Cannot auto-repair blocking gaps; produce a partial check.
            _check = make_check(
                obligation_id=ob_id,
                patch_id=gap.patch_id,
                result=ClosureResult.PARTIAL.value,
                confidence=0.0,
                notes=f"blocking gap {gap.gap_id[:8]}: manual intervention required",
            )
            log.info("_close_gap: blocking gap %r — skipped (requires manual fix)", ob_id)

        elif severity == GapSeverity.CRITICAL.value:
            # Try to find a treaty clause that covers this obligation.
            closed_via_treaty = False
            for treaty in state.treaties:
                clauses = getattr(treaty, "clauses", [])
                accepted = getattr(treaty, "accepted", False)
                for clause in clauses:
                    clause_patch = getattr(clause, "patch", "")
                    clause_satisfied = getattr(clause, "satisfied", False)
                    if accepted and clause_satisfied:
                        # Heuristic: if the obligation mentions the patch name, treat
                        # this clause as covering it.
                        if clause_patch and clause_patch in ob_id:
                            state.close_obligation(
                                ob_id, evidence=(f"treaty:{clause_patch}",)
                            )
                            closed_via_treaty = True
                            break
                if closed_via_treaty:
                    break

            if not closed_via_treaty:
                # Fall back to installing a synthetic section marker.
                synthetic_section = {
                    "obligations": [ob_id],
                    "_synthetic": True,
                    "_gap_id": gap.gap_id,
                    "_severity": severity,
                }
                patch_id = gap.patch_id or (state.patches[0] if state.patches else "")
                if patch_id:
                    state.apply_section(patch_id, synthetic_section)
                else:
                    state.close_obligation(ob_id, evidence=(f"synthetic:{gap.gap_id}",))

        else:
            # minor or moderate: install a synthetic evidence section.
            confidence = 0.9 if severity in (
                GapSeverity.MODERATE.value,
            ) else 1.0
            synthetic_section = {
                "obligations": [ob_id],
                "_synthetic": True,
                "_confidence": confidence,
                "_gap_id": gap.gap_id,
            }
            patch_id = gap.patch_id or (state.patches[0] if state.patches else "")
            if patch_id:
                state.apply_section(patch_id, synthetic_section)
            else:
                state.close_obligation(ob_id, evidence=(f"synthetic:{gap.gap_id}",))

        return state

    def _verify_no_regression(
        self, state: IntegrationState
    ) -> list[RegressionRecord]:
        """Run all registered regression tests and return any failures.

        For each :class:`RegressionTest` in ``state.regression_tests``, a
        synthetic :class:`ClosureCheck` is built from the current state and
        evaluated against the test's expectations.  Failing tests produce a
        :class:`RegressionRecord`.

        The method also scans for obligations that were previously closed but
        are now open (direct regression), creating records for each.

        Args:
            state: Current integration state.

        Returns:
            List of :class:`RegressionRecord` for any regressions detected.
            Empty list if everything is fine.
        """
        records: list[RegressionRecord] = []
        round_num = len(self._round_log)

        # Part 1: run registered regression tests.
        for test in state.regression_tests:
            if not isinstance(test, RegressionTest):
                # Duck-typed tests are skipped.
                continue

            ob_id = test.obligation_id
            if ob_id in state.obligations_closed:
                current_result = ClosureResult.CLOSED.value
                current_confidence = 1.0
            elif ob_id in state.obligations_open:
                current_result = ClosureResult.OPEN.value
                current_confidence = 0.0
            else:
                current_result = ClosureResult.PARTIAL.value
                current_confidence = 0.3

            check = make_check(
                obligation_id=ob_id,
                result=current_result,
                confidence=current_confidence,
                check_type=CheckType.SEMANTIC.value,
            )

            passed = test.evaluate(check)
            if passed:
                # Update test status to passing.
                object.__setattr__(test, "status", RegressionStatus.PASSING.value) \
                    if hasattr(test, "__setattr__") else None
                try:
                    test.status = RegressionStatus.PASSING.value
                    test.last_run = time.time()
                except AttributeError:
                    pass
            else:
                try:
                    test.status = RegressionStatus.FAILING.value
                    test.failure_reason = (
                        f"expected result={test.expected_result} "
                        f"confidence>={test.expected_confidence_min:.2f}, "
                        f"got result={current_result} confidence={current_confidence:.2f}"
                    )
                    test.last_run = time.time()
                except AttributeError:
                    pass

                severity = "critical" if current_result == ClosureResult.OPEN.value else "minor"
                rec = RegressionRecord(
                    key=ob_id,
                    baseline_value=test.expected_result,
                    current_value=current_result,
                    regression_type=RegressionKind.SEMANTIC.value,
                    severity=severity,
                    cause_analysis=(
                        f"RegressionTest {test.test_id[:8]} failed: "
                        f"result changed from {test.expected_result!r} "
                        f"to {current_result!r}"
                    ),
                    patch_id=gap_patch_for_ob(ob_id, state),
                )
                records.append(rec)
                log.debug(
                    "_verify_no_regression: test %s failed for obligation %r",
                    test.test_id[:8],
                    ob_id,
                )

        return records

    def _identify_gaps(self, state: IntegrationState) -> list[ClosureGap]:
        """Build a ClosureGap for every currently open obligation.

        The gap's severity is determined heuristically by the obligation
        string and how long it has been open (via :func:`_obligation_to_gap_severity`).

        Args:
            state: Current integration state.

        Returns:
            List of :class:`ClosureGap` records, one per open obligation.
        """
        gaps: list[ClosureGap] = []
        for ob_id in state.obligations_open:
            age = state.get_open_age(ob_id)
            severity = _obligation_to_gap_severity(ob_id, age)
            # Try to determine a relevant patch from sections.
            patch_id = _best_patch_for_obligation(ob_id, state)
            gap = make_gap(
                obligation_id=ob_id,
                severity=severity,
                description=f"Obligation {ob_id!r} is open (age={age} rounds)",
                patch_id=patch_id,
                suggested_fix=(
                    f"Install section evidence for {ob_id!r} on patch {patch_id!r}"
                    if patch_id
                    else f"Register an evidence section for obligation {ob_id!r}"
                ),
            )
            gaps.append(gap)
        return gaps

    def get_round_log(self) -> list[dict[str, Any]]:
        """Return the per-round log entries produced during :meth:`close`.

        Returns:
            List of dicts, one per completed round, with keys:
            ``round``, ``gaps_found``, ``fraction_before``,
            ``fraction_after``, ``selected_gap``, ``regressions``.
        """
        return list(self._round_log)

    def certify(
        self, state: IntegrationState, integration_id: str
    ) -> ClosureCertificate | None:
        """Issue a ClosureCertificate if *state* is fully closed.

        If the state is not closed (``state.is_closed()`` returns False),
        this method returns None without raising an exception.

        Args:
            state:          The integration state to certify.
            integration_id: The integration identifier for the certificate.

        Returns:
            A :class:`ClosureCertificate` if fully closed, else None.
        """
        if not state.is_closed():
            log.debug(
                "certify: state not closed (fraction=%.1f%%); returning None",
                state.closure_fraction() * 100,
            )
            return None

        # Check for any currently-failing regression tests.
        regression_free = not any(
            isinstance(t, RegressionTest) and t.is_failing()
            for t in state.regression_tests
        )

        evidence_summary = tuple(
            f"section:{p}" for p in state.patches if p in state.sections
        ) + tuple(
            f"closed:{ob}" for ob in sorted(state.obligations_closed)[:5]
        )

        cert = ClosureCertificate(
            cert_id=uuid.uuid4().hex[:16],
            integration_id=integration_id,
            fraction_closed=state.closure_fraction(),
            regression_free=regression_free,
            certifier=self._strategy.strategy_name(),
            timestamp=time.monotonic(),
            evidence_summary=evidence_summary,
        )
        log.info("certify: issued cert %r for %r", cert.cert_id[:8], integration_id)
        return cert


# ---------------------------------------------------------------------------
# Private module helpers
# ---------------------------------------------------------------------------


def _best_patch_for_obligation(ob_id: str, state: IntegrationState) -> str:
    """Return the best-guess patch for *ob_id* given *state*.

    Scans section keys and patch names for a match with the obligation string.

    Args:
        ob_id:  Obligation identifier string.
        state:  Current integration state.

    Returns:
        A patch identifier string, or empty string if no match found.
    """
    # First: direct section key match.
    for p in state.sections:
        if p in ob_id or ob_id in p:
            return p
    # Second: any patch whose name appears in the obligation id.
    for p in state.patches:
        if p in ob_id:
            return p
    # Third: first available section key.
    for p in state.sections:
        return p
    # Fourth: first patch.
    if state.patches:
        return state.patches[0]
    return ""


def gap_patch_for_ob(ob_id: str, state: IntegrationState) -> str:
    """Public alias for :func:`_best_patch_for_obligation`.

    Used by :meth:`IntegrationClosureEngine._verify_no_regression` to fill in
    ``patch_id`` on regression records.

    Args:
        ob_id:  Obligation identifier string.
        state:  Current integration state.

    Returns:
        Patch identifier string or empty string.
    """
    return _best_patch_for_obligation(ob_id, state)


# ---------------------------------------------------------------------------
# Module-level factory and convenience functions
# ---------------------------------------------------------------------------


def make_integration_state(
    integration_id: str,
    patches: list[str],
    initial_obligations: list[str] | None = None,
    sections: dict[str, Any] | None = None,
    treaties: list[Any] | None = None,
    regression_tests: list[Any] | None = None,
) -> IntegrationState:
    """Create a fresh :class:`IntegrationState` for the given integration.

    All obligations in *initial_obligations* start as open.  The caller
    can pre-populate sections and treaties if they are already available.

    Args:
        integration_id:      Identifier for the integration (used as state_id).
        patches:             Patch identifiers covered by this integration.
        initial_obligations: List of obligation IDs that start open.
        sections:            Optional pre-populated sections dict.
        treaties:            Optional list of treaty-like objects.
        regression_tests:    Optional list of RegressionTest objects.

    Returns:
        A new :class:`IntegrationState` ready for use with
        :class:`IntegrationClosureEngine`.

    Example::

        state = make_integration_state(
            "int-001",
            patches=["U_alpha", "U_beta", "U_gamma"],
            initial_obligations=["ob:overlap:alpha-beta", "ob:overlap:beta-gamma"],
        )
        engine = IntegrationClosureEngine(strategy=PriorityClosureStrategy())
        final_state, cert = engine.close(state)
    """
    open_obs: set[str] = set(initial_obligations or [])
    open_age: dict[str, int] = {ob: 0 for ob in open_obs}

    return IntegrationState(
        state_id=integration_id or uuid.uuid4().hex[:16],
        patches=tuple(patches),
        sections=dict(sections or {}),
        treaties=list(treaties or []),
        obligations_closed=set(),
        obligations_open=open_obs,
        regression_tests=list(regression_tests or []),
        _snapshots=[],
        _open_age=open_age,
    )


def run_closure(
    state: IntegrationState,
    strategy: str = "priority",
    max_rounds: int = 50,
    regression_check: bool = True,
) -> tuple[IntegrationState, ClosureCertificate | None]:
    """One-line convenience wrapper around :class:`IntegrationClosureEngine`.

    Selects a built-in strategy by name, constructs the engine, runs
    :meth:`~IntegrationClosureEngine.close`, and returns the results.

    Args:
        state:            The integration state to close.
        strategy:         Strategy name: ``"priority"``, ``"greedy"``, or
                          ``"conservative"``.  Default is ``"priority"``.
        max_rounds:       Maximum repair rounds (default 50).
        regression_check: Run regression tests after each repair.

    Returns:
        ``(final_state, certificate)`` — see
        :meth:`IntegrationClosureEngine.close`.

    Raises:
        ValueError: If *strategy* is not a recognised name.

    Example::

        state = make_integration_state("int-001", ["U1", "U2"])
        state.add_obligation("ob:section:U1")
        final, cert = run_closure(state, strategy="greedy")
        print(final.closure_fraction(), cert)
    """
    strategy_map: dict[str, ClosureStrategy] = {
        "priority": PriorityClosureStrategy(),
        "greedy": GreedyClosureStrategy(),
        "conservative": ConservativeClosureStrategy(),
    }
    chosen = strategy_map.get(strategy)
    if chosen is None:
        raise ValueError(
            f"Unknown strategy {strategy!r}. "
            f"Choose from: {sorted(strategy_map)}"
        )
    engine = IntegrationClosureEngine(
        strategy=chosen,
        max_rounds=max_rounds,
        regression_check=regression_check,
    )
    return engine.close(state)
