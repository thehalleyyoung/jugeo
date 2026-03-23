r"""Integration pipeline for the ``jugeo.generation.semantic_closure`` package.

This module provides the glue layer between semantic closure checking and the
broader jugeo generation infrastructure.  It adapts descent results,
construction outcomes, and generation goals into the uniform
:class:`~jugeo.generation.semantic_closure.models.ClosureCheck` /
:class:`~jugeo.generation.semantic_closure.models.ClosureGap` types expected by
the :class:`~jugeo.generation.semantic_closure.closure_checking.ObligationRegistry`
and :class:`~jugeo.generation.semantic_closure.closure_checking.ClosureReport`
machinery.

Architecture Overview
---------------------
The integration pipeline has five key components:

1. **DescentAdaptor** – Converts
   :class:`~jugeo.geometry.descent.DescentResult` objects into evidence tuples
   and :class:`~jugeo.generation.semantic_closure.models.ClosureGap` lists.  A
   successful descent carries high-confidence closure evidence; an obstruction
   produces structured gaps whose severity is proportional to the number of
   violated overlaps.

2. **GoalAdaptor** – Maps
   :class:`~jugeo.generation.goals.GenerationGoal` objects onto string
   obligation IDs.  This decouples the goal system from the closure-checking
   system so that goals can be expressed in domain terms while obligations
   remain simple string identifiers.

3. **FrontierIntegrator** – Translates
   :class:`~jugeo.generation.semantic_closure.models.ClosureGap` objects into
   :class:`~jugeo.orchestration.frontier.FrontierNode` entries and adds them to
   a live :class:`~jugeo.orchestration.frontier.Frontier`.  Gaps with higher
   severity produce nodes with higher ``predicted_closure_gain``, prioritising
   critical repairs.

4. **ConstructionAdaptor** – Mirrors ``DescentAdaptor`` for the construction
   subsystem.  :class:`~jugeo.generation.construction.ConstructionResult`
   objects are adapted to evidence tuples and
   :class:`~jugeo.generation.semantic_closure.models.ClosureCheck` objects.

5. **SemanticClosurePipeline** – End-to-end orchestrator.  Accepts an
   *integration dict* and produces a
   :class:`~jugeo.generation.semantic_closure.models.SemanticClosure`.  The
   pipeline logs each round for post-mortem inspection, supports regression
   testing via snapshots, and exposes a pluggable strategy system
   (``"priority"`` or ``"breadth_first"``).

Integration Dict Schema
-----------------------
The ``integration`` dict passed to :meth:`SemanticClosurePipeline.run` should
contain:

* ``"patches"``        – ``list[str]`` of patch IDs active in this integration
* ``"sections"``       – ``dict[str, Any]`` mapping patch ID → section data
* ``"treaties"``       – ``list[OverlapTreaty]`` governing overlap compatibility
* ``"obligations"``    – ``list[str]`` of obligation IDs to close
* ``"evidence"``       – ``dict[str, tuple[str,...]]`` mapping obligation → evidence
* ``"goals"``          – *(optional)* list of GenerationGoal or ConstructionGoal
* ``"descent_result"`` – *(optional)* DescentResult for the current round
* ``"frontier"``       – *(optional)* live Frontier object

Theory Reference
----------------
See theory2.tex Chapter 39, §§39.1–39.6 for the mathematical definitions
underpinning each adaptor's behaviour.  The pipeline implements the
constructive half of Theorem 39.1 (Closure Completeness) by repeatedly
resolving gaps until all obligations are closed or the round limit is hit.

copilot: semantic-closure-integration
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Optional heavy dependencies – wrapped in try/except per package convention
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import (
        ClosureCheck,
        SemanticClosure,
        ClosureGap,
        RegressionRecord,
        make_check,
        make_gap,
        empty_closure,
        ClosureResult,
        GapSeverity,
        CheckType,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    ClosureCheck = Any  # type: ignore[misc,assignment]
    SemanticClosure = Any  # type: ignore[misc,assignment]
    ClosureGap = Any  # type: ignore[misc,assignment]
    RegressionRecord = Any  # type: ignore[misc,assignment]

    def make_check(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return {}

    def make_gap(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return {}

    def empty_closure(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return {}

try:
    from jugeo.generation.semantic_closure.closure_checking import (
        ClosureChecker,
        ObligationRegistry,
        EvidenceAggregator,
        ClosureReport,
        build_report,
        check_obligations_from_registry,
    )
    _CHECKER_AVAILABLE = True
except ImportError:
    _CHECKER_AVAILABLE = False
    ClosureChecker = None  # type: ignore[assignment,misc]
    ObligationRegistry = None  # type: ignore[assignment,misc]
    EvidenceAggregator = None  # type: ignore[assignment,misc]
    ClosureReport = None  # type: ignore[assignment,misc]

    def build_report(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return None

    def check_obligations_from_registry(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return None

try:
    from jugeo.geometry.descent import DescentResult, DescentObstruction
    _DESCENT_AVAILABLE = True
except ImportError:
    _DESCENT_AVAILABLE = False

try:
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause
    _TREATIES_AVAILABLE = True
except ImportError:
    _TREATIES_AVAILABLE = False

try:
    from jugeo.orchestration.frontier import Frontier, FrontierNode
    _FRONTIER_AVAILABLE = True
except ImportError:
    _FRONTIER_AVAILABLE = False

try:
    from jugeo.generation.construction import ConstructionResult
    _CONSTRUCTION_AVAILABLE = True
except ImportError:
    _CONSTRUCTION_AVAILABLE = False

try:
    from jugeo.generation.goals import GenerationGoal
    _GOALS_AVAILABLE = True
except ImportError:
    _GOALS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of evidence tags extracted per source by default.
MAX_EVIDENCE_TAGS = 12

#: Severity names ordered from least to most severe.
SEVERITY_LEVELS = ("INFO", "MINOR", "MODERATE", "CRITICAL", "BLOCKING")

#: Map severity name → predicted closure gain for FrontierNode creation.
SEVERITY_TO_GAIN: dict[str, float] = {
    "BLOCKING": 1.0,
    "CRITICAL": 0.85,
    "MODERATE": 0.60,
    "MINOR": 0.35,
    "INFO": 0.15,
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stable_id(text: str, prefix: str = "") -> str:
    """Return a short deterministic ID derived from *text*."""
    digest = hashlib.sha1(text.encode()).hexdigest()[:12]
    return f"{prefix}{digest}" if prefix else digest


def _severity_int(gap: Any) -> int:
    """Return an integer priority for a ClosureGap based on its severity."""
    severity_map = {"BLOCKING": 5, "CRITICAL": 4, "MODERATE": 3, "MINOR": 2, "INFO": 1}
    try:
        return severity_map.get(str(gap.severity).upper(), 1)
    except Exception:
        return 1


def _gap_severity_name(gap: Any) -> str:
    """Return the upper-case severity name of *gap*, defaulting to ``MINOR``."""
    try:
        raw = gap.severity
        # GapSeverity enum: use .name or .value
        if hasattr(raw, "name"):
            return raw.name.upper()
        return str(raw).upper()
    except Exception:
        return "MINOR"


# ---------------------------------------------------------------------------
# DescentAdaptor
# ---------------------------------------------------------------------------


class DescentAdaptor:
    """Adapts descent results into closure evidence and gap lists.

    A successful :class:`~jugeo.geometry.descent.DescentResult` carries a
    ``GlobalSection``; its existence is strong evidence that all obligations
    covered by the descent coordinate are closed.  A failed result produces a
    :class:`~jugeo.geometry.descent.DescentObstruction` whose
    ``violated_overlaps`` each become a separate
    :class:`~jugeo.generation.semantic_closure.models.ClosureGap` with severity
    proportional to the violation count.
    """

    def adapt(self, descent_result: Any) -> tuple[str, ...]:
        """Extract a minimal evidence tuple from a DescentResult.

        On success the first tag is always ``"descent_success"``; on failure
        it is ``"descent_failed"``.

        Parameters
        ----------
        descent_result:
            A :class:`~jugeo.geometry.descent.DescentResult` (or duck-typed
            equivalent).

        Returns
        -------
        tuple[str, ...]
            Flat tuple of string evidence tags.
        """
        evidence: list[str] = []
        try:
            if descent_result.is_success:
                evidence.append("descent_success")
                section = descent_result.unwrap_section()
                try:
                    for key in list(section.keys())[:8]:
                        evidence.append(f"section_key:{key}")
                except Exception:
                    pass
            else:
                evidence.append("descent_failed")
                obstruction = descent_result.unwrap_obstruction()
                for vo in list(getattr(obstruction, "violated_overlaps", []))[:4]:
                    try:
                        evidence.append(f"violated_overlap:{vo}")
                    except Exception:
                        evidence.append("violated_overlap:unknown")
        except Exception:
            evidence.append("descent_result_unreadable")
        return tuple(evidence)

    def extract_closure_evidence(self, result: Any) -> tuple[str, ...]:
        """Perform richer evidence extraction including section data keys.

        This method is used when a detailed integration is being built;
        :meth:`adapt` is used for quick checks.

        Parameters
        ----------
        result:
            A :class:`~jugeo.geometry.descent.DescentResult`.

        Returns
        -------
        tuple[str, ...]
        """
        evidence: list[str] = []
        try:
            if result.is_success:
                evidence.append("descent_success")
                evidence.append("global_section_exists")
                section = result.unwrap_section()
                try:
                    for key, val in list(section.items())[:MAX_EVIDENCE_TAGS]:
                        evidence.append(f"section_key:{key}")
                        if isinstance(val, (str, int, float, bool)):
                            evidence.append(f"section_val_type:{type(val).__name__}")
                except Exception:
                    pass
                try:
                    summary = result.evidence_summary()
                    if summary:
                        evidence.append(f"evidence_summary_len:{len(summary)}")
                except Exception:
                    pass
            else:
                obstruction = result.unwrap_obstruction()
                evidence.append("descent_failed")
                try:
                    coord = str(obstruction.coordinate)
                    evidence.append(f"obstruction_coordinate:{coord[:64]}")
                except Exception:
                    pass
                try:
                    violated = list(obstruction.violated_overlaps)
                    evidence.append(f"violated_count:{len(violated)}")
                    for vo in violated[:6]:
                        evidence.append(f"violated_overlap:{str(vo)[:64]}")
                except Exception:
                    pass
                try:
                    partial = obstruction.partial_section
                    if partial:
                        evidence.append("has_partial_section")
                        for k in list(partial.keys())[:4]:
                            evidence.append(f"partial_key:{k}")
                except Exception:
                    pass
                try:
                    pid = str(obstruction.persistence_id)
                    if pid:
                        evidence.append(f"persistence_id:{pid[:32]}")
                except Exception:
                    pass
        except Exception as exc:
            evidence.append(f"extraction_error:{type(exc).__name__}")
        return tuple(evidence)

    def adapt_obstruction(self, obstruction: Any) -> list[Any]:
        """Convert a DescentObstruction into a list of ClosureGaps.

        Each entry in ``obstruction.violated_overlaps`` becomes a separate gap.
        Severity is BLOCKING for more than five violations, CRITICAL for more
        than three, MODERATE for two, MINOR for one.

        Parameters
        ----------
        obstruction:
            A :class:`~jugeo.geometry.descent.DescentObstruction` (or
            duck-typed equivalent).

        Returns
        -------
        list[ClosureGap]
        """
        if not _MODELS_AVAILABLE:
            return []
        gaps: list[Any] = []
        try:
            violated = list(getattr(obstruction, "violated_overlaps", []))
            n = len(violated)
            if n > 5:
                severity_name = "blocking"
            elif n > 3:
                severity_name = "critical"
            elif n > 1:
                severity_name = "moderate"
            else:
                severity_name = "minor"
            coord_str = str(getattr(obstruction, "coordinate", "unknown"))
            persistence_id = str(getattr(obstruction, "persistence_id", ""))
            for i, vo in enumerate(violated):
                gap_id = _stable_id(f"obstruction:{coord_str}:{i}:{vo}", prefix="gap_")
                gap = make_gap(
                    gap_id=gap_id,
                    obligation_id=coord_str,
                    description=f"Descent obstruction: violated overlap #{i} — {str(vo)[:80]}",
                    severity=severity_name,
                    patch_id=coord_str[:64],
                    suggested_fix=(
                        f"Repair the frontier at coordinate {coord_str} "
                        f"to resolve violated overlap {i}"
                    ),
                    source_check_id=persistence_id,
                )
                gaps.append(gap)
        except Exception:
            pass
        return gaps

    def is_closure_evidence(self, result: Any) -> bool:
        """Return True if *result* provides enough evidence to support closure.

        A successful descent is always sufficient.  A failure is sufficient
        only when the obstruction has no violated overlaps (trivial obstruction).

        Parameters
        ----------
        result:
            A :class:`~jugeo.geometry.descent.DescentResult`.
        """
        try:
            if result.is_success:
                return True
            obstruction = result.unwrap_obstruction()
            violated = list(getattr(obstruction, "violated_overlaps", []))
            return len(violated) == 0
        except Exception:
            return False

    def summarise(self, result: Any) -> dict[str, Any]:
        """Return a human-readable summary dict for *result*.

        Returns
        -------
        dict[str, Any]
            Keys: ``success``, ``evidence_count``, ``violated_overlap_count``,
            ``has_partial_section``.
        """
        try:
            if result.is_success:
                section = result.unwrap_section()
                try:
                    n_keys = len(list(section.keys()))
                except Exception:
                    n_keys = 0
                return {
                    "success": True,
                    "evidence_count": n_keys,
                    "violated_overlap_count": 0,
                    "has_partial_section": False,
                }
            else:
                obs = result.unwrap_obstruction()
                violated = list(getattr(obs, "violated_overlaps", []))
                partial = getattr(obs, "partial_section", None)
                return {
                    "success": False,
                    "evidence_count": 0,
                    "violated_overlap_count": len(violated),
                    "has_partial_section": bool(partial),
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# GoalAdaptor
# ---------------------------------------------------------------------------


class GoalAdaptor:
    """Maps generation goals to obligation IDs and closure checks.

    Goal objects (:class:`~jugeo.generation.goals.GenerationGoal`, or
    arbitrary dicts/dataclasses) are mapped to stable string identifiers so
    that they can be tracked in the
    :class:`~jugeo.generation.semantic_closure.closure_checking.ObligationRegistry`.
    The mapping is deterministic: the same goal always produces the same
    obligation ID.
    """

    def goals_to_obligations(self, goals: list[Any]) -> list[str]:
        """Convert a list of goals to a list of obligation IDs.

        The obligation ID is derived from ``goal.goal_id`` when available,
        otherwise from a SHA-1 hash of the goal's string representation.

        Parameters
        ----------
        goals:
            List of goal objects.

        Returns
        -------
        list[str]
        """
        return [self._goal_to_id(goal) for goal in goals]

    def _goal_to_id(self, goal: Any) -> str:
        """Derive a stable obligation ID from a single goal."""
        try:
            if hasattr(goal, "goal_id") and goal.goal_id:
                return str(goal.goal_id)
        except Exception:
            pass
        # Fall back to hashing the repr
        return _stable_id(repr(goal), prefix="goal_")

    def check_goal_closed(self, goal: Any, state: dict[str, Any]) -> bool:
        """Check whether *goal* is satisfied in *state*.

        *state* is a mapping from obligation IDs (or goal_ids) to truthy
        values.  A goal is considered closed if its obligation ID is present
        in *state* and the corresponding value is truthy.

        Parameters
        ----------
        goal:
            A goal object.
        state:
            Mapping from obligation ID → truthy/falsy value.

        Returns
        -------
        bool
        """
        obligation_id = self._goal_to_id(goal)
        if obligation_id in state:
            return bool(state[obligation_id])
        try:
            raw_id = str(goal.goal_id)
            if raw_id in state:
                return bool(state[raw_id])
        except Exception:
            pass
        try:
            status_str = str(goal.status).upper()
            return "ACHIEVED" in status_str or "CLOSED" in status_str
        except Exception:
            pass
        return False

    def goal_to_closure_check(self, goal: Any, state: dict[str, Any]) -> Any:
        """Produce a ClosureCheck describing whether *goal* is closed in *state*.

        Parameters
        ----------
        goal:
            A goal object.
        state:
            Mapping from obligation IDs to closed/open status.

        Returns
        -------
        ClosureCheck | None
        """
        if not _MODELS_AVAILABLE:
            return None
        obligation_id = self._goal_to_id(goal)
        is_closed = self.check_goal_closed(goal, state)
        patch_id = str(getattr(goal, "target_coordinate", ""))
        evidence: tuple[str, ...] = (f"goal_id:{obligation_id}",)
        if is_closed:
            evidence = evidence + ("goal_achieved",)
            result = "closed"
            confidence = 0.90
        else:
            evidence = evidence + ("goal_open",)
            result = "open"
            confidence = 0.10
        # Enrich with goal metadata when available
        try:
            prop = str(goal.required_proposition)[:64]
            if prop:
                evidence = evidence + (f"proposition:{prop}",)
        except Exception:
            pass
        try:
            priority = str(goal.priority)
            evidence = evidence + (f"priority:{priority}",)
        except Exception:
            pass
        return make_check(
            check_id=_stable_id(f"goal_check:{obligation_id}:{is_closed}", prefix="chk_"),
            obligation_id=obligation_id,
            patch_id=patch_id,
            result=result,
            confidence=confidence,
            evidence=evidence,
            check_type="semantic",
            notes=f"Goal closure check for {obligation_id}",
        )

    def goals_to_registry_entries(self, goals: list[Any]) -> list[tuple[str, dict]]:
        """Return ``[(obligation_id, metadata_dict), ...]`` for *goals*.

        The metadata dict contains human-readable information about each goal
        that can be stored in the ObligationRegistry alongside the ID.

        Parameters
        ----------
        goals:
            List of goal objects.

        Returns
        -------
        list[tuple[str, dict]]
        """
        entries: list[tuple[str, dict]] = []
        for goal in goals:
            obligation_id = self._goal_to_id(goal)
            meta: dict[str, Any] = {"source": "goal", "obligation_id": obligation_id}
            try:
                meta["goal_id"] = str(goal.goal_id)
                meta["target_coordinate"] = str(goal.target_coordinate)
                meta["priority"] = str(getattr(goal, "priority", "MEDIUM"))
                meta["status"] = str(getattr(goal, "status", "PENDING"))
                meta["required_proposition"] = str(getattr(goal, "required_proposition", ""))
                meta["budget"] = int(getattr(goal, "budget", 1))
                meta["is_leaf"] = bool(getattr(goal, "is_leaf", True))
            except Exception:
                meta["repr"] = repr(goal)[:120]
            entries.append((obligation_id, meta))
        return entries

    def filter_open_goals(self, goals: list[Any], state: dict[str, Any]) -> list[Any]:
        """Return goals that are NOT yet closed in *state*.

        Parameters
        ----------
        goals:
            List of goal objects.
        state:
            Mapping from obligation IDs to closed/open status.

        Returns
        -------
        list[Any]
        """
        return [g for g in goals if not self.check_goal_closed(g, state)]

    def obligations_to_goal_map(self, goals: list[Any]) -> dict[str, Any]:
        """Return a mapping from obligation IDs back to goal objects.

        Parameters
        ----------
        goals:
            List of goal objects.

        Returns
        -------
        dict[str, Any]
            Mapping from obligation_id → goal.
        """
        return {self._goal_to_id(g): g for g in goals}

    def all_obligation_ids(self, goals: list[Any]) -> set[str]:
        """Return the set of all obligation IDs derived from *goals*."""
        return {self._goal_to_id(g) for g in goals}


# ---------------------------------------------------------------------------
# FrontierIntegrator
# ---------------------------------------------------------------------------


class FrontierIntegrator:
    """Feeds closure gaps back to the search frontier.

    :class:`~jugeo.generation.semantic_closure.models.ClosureGap` objects
    represent obligations that are not yet satisfied.  This class translates
    those gaps into :class:`~jugeo.orchestration.frontier.FrontierNode` entries
    so that the frontier search algorithm can prioritise repairing them.

    Gap severity is mapped to ``predicted_closure_gain`` via
    :data:`SEVERITY_TO_GAIN`.
    """

    def integrate(self, gaps: list[Any], frontier: Any) -> int:
        """Add FrontierNodes derived from *gaps* to *frontier*.

        Parameters
        ----------
        gaps:
            List of :class:`~jugeo.generation.semantic_closure.models.ClosureGap`.
        frontier:
            A :class:`~jugeo.orchestration.frontier.Frontier` (must have
            ``add_node`` method).

        Returns
        -------
        int
            Number of nodes successfully added.
        """
        added = 0
        for gap in gaps:
            try:
                node = self.gap_to_frontier_node(gap)
                if node is not None:
                    frontier.add_node(node)
                    added += 1
            except Exception:
                pass
        return added

    def gap_to_frontier_node(self, gap: Any) -> Any:
        """Create a FrontierNode from a ClosureGap.

        The node's ``predicted_closure_gain`` is derived from the gap's
        severity; ``support_scope`` is set to
        ``frozenset({gap.obligation_id})``.

        Returns ``None`` if FrontierNode is not available.

        Parameters
        ----------
        gap:
            A :class:`~jugeo.generation.semantic_closure.models.ClosureGap`.
        """
        if not _FRONTIER_AVAILABLE:
            return None
        severity_name = _gap_severity_name(gap)
        gain = SEVERITY_TO_GAIN.get(severity_name, 0.35)
        obligation_id = str(getattr(gap, "obligation_id", "unknown"))
        patch_id = str(getattr(gap, "patch_id", ""))
        gap_id = str(getattr(gap, "gap_id", uuid.uuid4().hex[:8]))
        node_id = _stable_id(f"gap:{gap_id}", prefix="fn_")
        move = f"repair_gap:{gap_id}:{patch_id}"
        state_hash = _stable_id(f"{obligation_id}:{severity_name}")
        try:
            node = FrontierNode(
                node_id=node_id,
                semantic_state_hash=state_hash,
                predecessor_id=None,
                move_that_produced=move,
                predicted_closure_gain=gain,
                support_scope=frozenset({obligation_id}),
                depth=1,
                is_terminal=False,
                estimated_cost=max(0.0, 1.0 - gain),
                uncertainty=0.3,
            )
            return node
        except Exception:
            # FrontierNode constructor may have a different signature; try minimal
            try:
                node = FrontierNode(
                    node_id=node_id,
                    semantic_state_hash=state_hash,
                    predecessor_id=None,
                    move_that_produced=move,
                    predicted_closure_gain=gain,
                )
                return node
            except Exception:
                return None

    def extract_gaps_from_frontier(self, frontier: Any) -> list[Any]:
        """Inspect frontier nodes and extract those representing closure gaps.

        A node is considered a closure-gap node when its
        ``move_that_produced`` starts with ``"repair_gap:"``.

        Parameters
        ----------
        frontier:
            A :class:`~jugeo.orchestration.frontier.Frontier`.

        Returns
        -------
        list[ClosureGap]
        """
        if not _MODELS_AVAILABLE:
            return []
        gaps: list[Any] = []
        try:
            nodes = frontier.all_nodes()
        except Exception:
            return []
        for node in nodes:
            try:
                move = str(getattr(node, "move_that_produced", ""))
                if not move.startswith("repair_gap:"):
                    continue
                parts = move.split(":", 2)
                gap_id = parts[1] if len(parts) > 1 else "unknown"
                support = getattr(node, "support_scope", set())
                obligation_id = next(iter(support), "unknown") if support else "unknown"
                gain = float(getattr(node, "predicted_closure_gain", 0.5))
                if gain >= 0.85:
                    severity_name = "critical"
                elif gain >= 0.60:
                    severity_name = "moderate"
                else:
                    severity_name = "minor"
                gap = make_gap(
                    gap_id=gap_id,
                    obligation_id=str(obligation_id),
                    description=(
                        f"Gap extracted from frontier node "
                        f"{getattr(node, 'node_id', '?')} (gain={gain:.2f})"
                    ),
                    severity=severity_name,
                    patch_id="",
                    suggested_fix="",
                    source_check_id="",
                )
                gaps.append(gap)
            except Exception:
                continue
        return gaps

    def update_node_from_gap(self, node: Any, gap: Any) -> Any:
        """Return an updated version of *node* reflecting *gap* information.

        Since FrontierNode is immutable, this method returns a new node rather
        than mutating the existing one.  If FrontierNode is not available, the
        original node is returned unchanged.

        Parameters
        ----------
        node:
            The existing :class:`~jugeo.orchestration.frontier.FrontierNode`.
        gap:
            The :class:`~jugeo.generation.semantic_closure.models.ClosureGap`
            providing updated severity information.
        """
        if not _FRONTIER_AVAILABLE:
            return node
        severity_name = _gap_severity_name(gap)
        new_gain = SEVERITY_TO_GAIN.get(severity_name, 0.35)
        try:
            updated = FrontierNode(
                node_id=getattr(node, "node_id", uuid.uuid4().hex[:8]),
                semantic_state_hash=getattr(node, "semantic_state_hash", ""),
                predecessor_id=getattr(node, "predecessor_id", None),
                move_that_produced=getattr(node, "move_that_produced", ""),
                predicted_closure_gain=new_gain,
                support_scope=getattr(node, "support_scope", frozenset()),
                depth=getattr(node, "depth", 1),
                is_terminal=getattr(node, "is_terminal", False),
                estimated_cost=max(0.0, 1.0 - new_gain),
                uncertainty=getattr(node, "uncertainty", 0.3),
            )
            return updated
        except Exception:
            return node

    def batch_integrate(
        self,
        gaps: list[Any],
        frontier: Any,
        max_nodes: int = 50,
    ) -> dict[str, Any]:
        """Integrate a batch of gaps and return a summary report.

        Parameters
        ----------
        gaps:
            List of :class:`~jugeo.generation.semantic_closure.models.ClosureGap`.
        frontier:
            Target frontier.
        max_nodes:
            Maximum number of nodes to add in this call.

        Returns
        -------
        dict[str, Any]
            Keys: ``added``, ``skipped``, ``total_gaps``.
        """
        added = 0
        skipped = 0
        for gap in gaps[:max_nodes]:
            try:
                node = self.gap_to_frontier_node(gap)
                if node is not None:
                    frontier.add_node(node)
                    added += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        return {"added": added, "skipped": skipped, "total_gaps": len(gaps)}


# ---------------------------------------------------------------------------
# ConstructionAdaptor
# ---------------------------------------------------------------------------


class ConstructionAdaptor:
    """Extracts closure evidence from ConstructionResult objects.

    :class:`~jugeo.generation.construction.ConstructionResult` is the outcome
    of a single construction loop.  This adaptor reads the result's status,
    selected candidate, and residuals to produce evidence tuples and
    :class:`~jugeo.generation.semantic_closure.models.ClosureCheck` objects.
    """

    def adapt(self, construction_result: Any) -> tuple[str, ...]:
        """Extract an evidence tuple from a ConstructionResult.

        Parameters
        ----------
        construction_result:
            A :class:`~jugeo.generation.construction.ConstructionResult`.

        Returns
        -------
        tuple[str, ...]
            Evidence tags derived from the result.
        """
        evidence: list[str] = []
        try:
            if construction_result.succeeded():
                evidence.append("construction_succeeded")
                candidate = construction_result.selected_candidate
                if candidate is not None:
                    evidence.append("candidate_selected")
                    try:
                        channel = construction_result.winning_channel()
                        if channel is not None:
                            evidence.append(f"candidate_channel:{channel}")
                    except Exception:
                        pass
                for tag in list(getattr(construction_result, "residuals_propagated", []))[:6]:
                    evidence.append(f"residual:{tag}")
                for key in list(getattr(construction_result, "evidence_produced", {}).keys())[:6]:
                    evidence.append(f"evidence_key:{key}")
                iters = getattr(construction_result, "iterations", None)
                if iters is not None:
                    evidence.append(f"iterations:{iters}")
            elif construction_result.partial():
                evidence.append("construction_partial")
                residuals = list(getattr(construction_result, "residuals_propagated", []))
                evidence.append(f"residual_count:{len(residuals)}")
                for tag in residuals[:4]:
                    evidence.append(f"residual:{tag}")
            else:
                evidence.append("construction_failed")
                ms = getattr(construction_result, "time_taken_ms", None)
                if ms is not None:
                    evidence.append(f"time_ms:{ms}")
        except Exception as exc:
            evidence.append(f"adapt_error:{type(exc).__name__}")
        return tuple(evidence)

    def result_to_closure_check(
        self,
        result: Any,
        obligation_id: str,
        patch_id: str = "",
    ) -> Any:
        """Convert a ConstructionResult into a ClosureCheck.

        Parameters
        ----------
        result:
            A :class:`~jugeo.generation.construction.ConstructionResult`.
        obligation_id:
            The obligation this construction was meant to satisfy.
        patch_id:
            Optional patch identifier.

        Returns
        -------
        ClosureCheck | None
        """
        if not _MODELS_AVAILABLE:
            return None
        evidence = self.adapt(result)
        try:
            succeeded = result.succeeded()
            partial = result.partial() if not succeeded else False
        except Exception:
            succeeded = False
            partial = False
        if succeeded:
            closure_result = "closed"
            confidence = 0.88
        elif partial:
            closure_result = "partial"
            confidence = 0.45
        else:
            closure_result = "open"
            confidence = 0.05
        check_id = _stable_id(
            f"construction:{obligation_id}:{patch_id}", prefix="chk_"
        )
        return make_check(
            check_id=check_id,
            obligation_id=obligation_id,
            patch_id=patch_id,
            result=closure_result,
            confidence=confidence,
            evidence=evidence,
            check_type="semantic",
            notes=f"Construction result check for {obligation_id}",
        )

    def result_to_gaps(self, result: Any, obligation_id: str) -> list[Any]:
        """Create ClosureGaps when a construction fails.

        Parameters
        ----------
        result:
            A :class:`~jugeo.generation.construction.ConstructionResult`.
        obligation_id:
            The obligation that failed to be satisfied.

        Returns
        -------
        list[ClosureGap]
        """
        if not _MODELS_AVAILABLE:
            return []
        gaps: list[Any] = []
        try:
            if result.succeeded():
                return []
            partial = result.partial()
        except Exception:
            return []
        severity = "moderate" if partial else "critical"
        residuals = list(getattr(result, "residuals_propagated", []))
        description = (
            f"Construction {'partially' if partial else 'fully'} failed for {obligation_id}"
            + (f"; residuals: {residuals[:3]}" if residuals else "")
        )
        gap_id = _stable_id(f"construction_gap:{obligation_id}", prefix="gap_")
        gap = make_gap(
            gap_id=gap_id,
            obligation_id=obligation_id,
            description=description,
            severity=severity,
            patch_id="",
            suggested_fix="Retry construction with expanded context or different strategy",
            source_check_id="",
        )
        gaps.append(gap)
        return gaps

    def batch_adapt(
        self,
        results_and_obligations: list[tuple[Any, str]],
    ) -> list[Any]:
        """Adapt a batch of (result, obligation_id) pairs to ClosureChecks.

        Parameters
        ----------
        results_and_obligations:
            List of ``(ConstructionResult, obligation_id)`` pairs.

        Returns
        -------
        list[ClosureCheck]
        """
        checks: list[Any] = []
        for result, obligation_id in results_and_obligations:
            check = self.result_to_closure_check(result, obligation_id)
            if check is not None:
                checks.append(check)
        return checks


# ---------------------------------------------------------------------------
# IntegrationState (internal helper dataclass)
# ---------------------------------------------------------------------------


@dataclass
class IntegrationState:
    """Mutable state object used internally by :class:`SemanticClosurePipeline`."""

    integration_id: str
    patches: list[str] = field(default_factory=list)
    sections: dict[str, Any] = field(default_factory=dict)
    treaties: list[Any] = field(default_factory=list)
    obligations: list[str] = field(default_factory=list)
    evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    goals: list[Any] = field(default_factory=list)
    descent_result: Any = None
    frontier: Any = None
    round_number: int = 0
    checks: list[Any] = field(default_factory=list)
    gaps: list[Any] = field(default_factory=list)
    registry: Any = None


# ---------------------------------------------------------------------------
# SemanticClosurePipeline
# ---------------------------------------------------------------------------


class SemanticClosurePipeline:
    """End-to-end pipeline for semantic closure.

    The pipeline accepts an *integration dict* and orchestrates the full
    closure-checking workflow:

    1. Build an :class:`~jugeo.generation.semantic_closure.closure_checking.ObligationRegistry`
       from the integration's obligations.
    2. Run :class:`~jugeo.generation.semantic_closure.closure_checking.ClosureChecker`
       on each obligation using all available evidence.
    3. Convert failed checks into
       :class:`~jugeo.generation.semantic_closure.models.ClosureGap` objects.
    4. Optionally feed gaps back to a
       :class:`~jugeo.orchestration.frontier.Frontier`.
    5. Collect all checks into a
       :class:`~jugeo.generation.semantic_closure.closure_checking.ClosureReport`.
    6. Assemble and return a
       :class:`~jugeo.generation.semantic_closure.models.SemanticClosure`.

    The pipeline supports up to ``max_rounds`` rounds of gap resolution.  In
    each round, open obligations are re-checked; if the frontier is provided,
    new nodes derived from gaps are added to it.

    Parameters
    ----------
    max_rounds:
        Maximum number of gap-resolution rounds.  Default is 100.
    strategy:
        Closure strategy name.  ``"priority"`` (default) processes
        high-severity gaps first; ``"breadth_first"`` uses FIFO ordering.
    enable_regression:
        If True, the pipeline records per-round closure fractions which
        can be analysed by the monotonicity theorem.
    """

    def __init__(
        self,
        max_rounds: int = 100,
        strategy: str = "priority",
        enable_regression: bool = True,
    ) -> None:
        self._max_rounds = max_rounds
        self._strategy_name = strategy
        self._enable_regression = enable_regression
        self._descent_adaptor = DescentAdaptor()
        self._goal_adaptor = GoalAdaptor()
        self._construction_adaptor = ConstructionAdaptor()
        self._frontier_integrator = FrontierIntegrator()
        self._pipeline_log: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, integration: dict[str, Any]) -> Any:
        """Run the full closure pipeline on *integration*.

        Parameters
        ----------
        integration:
            Dict with keys: ``patches``, ``sections``, ``treaties``,
            ``obligations``, ``evidence``, and optionally ``goals``,
            ``descent_result``, ``frontier``.

        Returns
        -------
        SemanticClosure
        """
        if not _MODELS_AVAILABLE:
            return {}
        state = self._build_state(integration)
        self._pipeline_log.clear()
        return self._run_pipeline(state)

    def run_with_regression_tests(
        self,
        integration: dict[str, Any],
        baseline: dict[str, Any],
    ) -> tuple[Any, list[Any]]:
        """Run the pipeline and detect regressions vs *baseline*.

        Parameters
        ----------
        integration:
            Current integration dict.
        baseline:
            A snapshot dict from a previous
            :meth:`~jugeo.generation.semantic_closure.closure_checking.ObligationRegistry.snapshot`
            call.  Expected keys: ``closed`` (list of obligation IDs).

        Returns
        -------
        tuple[SemanticClosure, list[RegressionRecord]]
        """
        closure = self.run(integration)
        regressions = self._detect_regressions(integration, baseline)
        return closure, regressions

    def get_pipeline_log(self) -> list[dict]:
        """Return a list of per-round log entries (copies)."""
        return list(self._pipeline_log)

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _build_state(self, integration: dict[str, Any]) -> IntegrationState:
        """Construct an IntegrationState from the integration dict."""
        integration_id = integration.get("integration_id", uuid.uuid4().hex[:12])
        state = IntegrationState(integration_id=integration_id)
        state.patches = list(integration.get("patches", []))
        state.sections = dict(integration.get("sections", {}))
        state.treaties = list(integration.get("treaties", []))
        state.obligations = list(integration.get("obligations", []))
        state.evidence = dict(integration.get("evidence", {}))
        state.goals = list(integration.get("goals", []))
        state.descent_result = integration.get("descent_result")
        state.frontier = integration.get("frontier")
        # Add goals-derived obligations if not already present
        if state.goals:
            extra_ids = self._goal_adaptor.goals_to_obligations(state.goals)
            for oid in extra_ids:
                if oid not in state.obligations:
                    state.obligations.append(oid)
        if _CHECKER_AVAILABLE:
            state.registry = ObligationRegistry(integration_id)
            state.registry.register_many(state.obligations)
        return state

    def _run_pipeline(self, state: IntegrationState) -> Any:
        """Core iterative pipeline loop."""
        if not _CHECKER_AVAILABLE:
            return empty_closure(integration_id=state.integration_id)

        checker = ClosureChecker()
        aggregator = EvidenceAggregator()
        all_checks: list[Any] = []
        all_gaps: list[Any] = []
        closure_fractions: list[float] = []
        prev_open_count = len(state.obligations) + 1  # sentinel

        for round_num in range(self._max_rounds):
            state.round_number = round_num
            open_obligations = state.registry.get_open()
            if not open_obligations:
                self._log_round(round_num, "all_closed", 0, state)
                break

            round_checks: list[Any] = []
            round_gaps: list[Any] = []

            for obligation_id in open_obligations:
                evidence = self._collect_evidence(obligation_id, state, aggregator)
                patch_id = state.patches[0] if state.patches else ""
                check = checker.check(
                    obligation=obligation_id,
                    evidence=evidence,
                    patch_id=patch_id,
                    check_type="combined",
                )
                round_checks.append(check)
                try:
                    if check.is_closed():
                        state.registry.mark_closed(obligation_id, evidence)
                    else:
                        gap = self._check_to_gap(check, obligation_id)
                        if gap is not None:
                            round_gaps.append(gap)
                except Exception:
                    pass

            # Sort gaps by strategy
            round_gaps = self._sort_gaps(round_gaps)

            # Feed back to frontier
            if state.frontier is not None and round_gaps:
                self._frontier_integrator.integrate(round_gaps, state.frontier)

            all_checks.extend(round_checks)
            all_gaps.extend(round_gaps)

            fraction = state.registry.closure_fraction()
            closure_fractions.append(fraction)
            self._log_round(round_num, "in_progress", len(round_gaps), state)

            # Early termination: no open obligations changed
            current_open_count = len(state.registry.get_open())
            if current_open_count == 0:
                break
            if current_open_count >= prev_open_count and round_num > 0:
                self._log_round(round_num, "no_progress", len(round_gaps), state)
                break
            prev_open_count = current_open_count

        # Final fraction
        final_fraction = state.registry.closure_fraction() if _CHECKER_AVAILABLE else 0.0
        closed_ids = tuple(state.registry.get_closed()) if _CHECKER_AVAILABLE else ()
        open_ids = tuple(state.registry.get_open()) if _CHECKER_AVAILABLE else ()

        # Assemble ClosureReport
        report = None
        try:
            report = build_report(
                integration_id=state.integration_id,
                checks=all_checks,
                gaps=all_gaps,
            )
        except Exception:
            pass

        # Build SemanticClosure
        try:
            return SemanticClosure(
                integration_id=state.integration_id,
                fraction_closed=final_fraction,
                report=report,
                fractions=closure_fractions,
                closed_obligations=closed_ids,
                open_obligations=open_ids,
            )
        except Exception:
            return empty_closure(
                integration_id=state.integration_id,
                fraction_closed=final_fraction,
                report=report,
                fractions=closure_fractions,
            )

    def _collect_evidence(
        self,
        obligation_id: str,
        state: IntegrationState,
        aggregator: Any,
    ) -> tuple[str, ...]:
        """Gather all evidence for *obligation_id* from the integration state."""
        evidence_parts: list[tuple[str, ...]] = []

        # 1. Base evidence from integration dict
        if obligation_id in state.evidence:
            evidence_parts.append(state.evidence[obligation_id])

        # 2. Evidence from descent result
        if state.descent_result is not None:
            descent_ev = self._descent_adaptor.extract_closure_evidence(
                state.descent_result
            )
            if descent_ev:
                evidence_parts.append(descent_ev)

        # 3. Evidence derived from section keys matching this obligation
        for patch_id, section in state.sections.items():
            try:
                if obligation_id in str(section) or obligation_id in patch_id:
                    evidence_parts.append((f"section_match:{patch_id}",))
            except Exception:
                pass

        # 4. Treaty evidence
        for treaty in state.treaties:
            try:
                for clause in getattr(treaty, "clauses", []):
                    if (
                        obligation_id in str(getattr(clause, "expectation", ""))
                        and getattr(clause, "satisfied", False)
                    ):
                        evidence_parts.append(
                            (f"treaty_clause_satisfied:{clause.patch}",)
                        )
            except Exception:
                pass

        if not evidence_parts:
            return ()

        try:
            if _CHECKER_AVAILABLE:
                combined = aggregator.aggregate(evidence_parts)
                return combined
        except Exception:
            pass
        flat: list[str] = []
        for ep in evidence_parts:
            flat.extend(ep)
        return tuple(dict.fromkeys(flat))  # dedup preserving order

    def _check_to_gap(self, check: Any, obligation_id: str) -> Any:
        """Convert a non-closed ClosureCheck to a ClosureGap."""
        if not _MODELS_AVAILABLE:
            return None
        try:
            result_str = str(getattr(check, "result", "open")).upper()
            if "CLOSED" in result_str:
                return None
            confidence = float(getattr(check, "confidence", 0.0))
            if confidence >= 0.7:
                severity = "minor"
            elif confidence >= 0.4:
                severity = "moderate"
            elif confidence >= 0.2:
                severity = "critical"
            else:
                severity = "blocking"
            gap_id = _stable_id(
                f"check_gap:{getattr(check, 'check_id', obligation_id)}", prefix="gap_"
            )
            return make_gap(
                gap_id=gap_id,
                obligation_id=obligation_id,
                description=(
                    f"Obligation {obligation_id} is not closed "
                    f"(result={result_str}, confidence={confidence:.2f})"
                ),
                severity=severity,
                patch_id=str(getattr(check, "patch_id", "")),
                suggested_fix="Provide additional evidence or restructure patches",
                source_check_id=str(getattr(check, "check_id", "")),
            )
        except Exception:
            return None

    def _sort_gaps(self, gaps: list[Any]) -> list[Any]:
        """Sort gaps according to the configured strategy."""
        if self._strategy_name == "priority":
            return sorted(gaps, key=_severity_int, reverse=True)
        # breadth_first: keep FIFO order
        return gaps

    def _log_round(
        self,
        round_num: int,
        status: str,
        gap_count: int,
        state: IntegrationState,
    ) -> None:
        """Append a log entry for the current round."""
        fraction = 0.0
        try:
            fraction = state.registry.closure_fraction()
        except Exception:
            pass
        self._pipeline_log.append(
            {
                "round": round_num,
                "status": status,
                "gap_count": gap_count,
                "fraction_closed": fraction,
                "timestamp": time.time(),
            }
        )

    def _detect_regressions(
        self,
        integration: dict[str, Any],
        baseline: dict[str, Any],
    ) -> list[Any]:
        """Compare current closure state against *baseline* for regressions."""
        if not _MODELS_AVAILABLE or not _CHECKER_AVAILABLE:
            return []
        regressions: list[Any] = []
        baseline_closed: set[str] = set(baseline.get("closed", []))
        current_evidence: dict[str, Any] = integration.get("evidence", {})
        checker = ClosureChecker()
        for obligation_id in baseline_closed:
            evidence = current_evidence.get(obligation_id, ())
            if isinstance(evidence, list):
                evidence = tuple(evidence)
            check = checker.check(
                obligation=obligation_id,
                evidence=evidence,
                patch_id="",
                check_type="semantic",
            )
            try:
                if not check.is_closed():
                    rec_id = _stable_id(f"regression:{obligation_id}", prefix="reg_")
                    record = RegressionRecord(
                        record_id=rec_id,
                        key=obligation_id,
                        baseline_value=1.0,
                        current_value=float(getattr(check, "confidence", 0.0)),
                        regression_type="semantic",
                        severity="critical",
                        cause_analysis=(
                            f"Obligation {obligation_id} was closed in baseline "
                            f"but is now {check.result} (confidence={check.confidence:.2f})"
                        ),
                        timestamp=time.time(),
                        patch_id="",
                    )
                    regressions.append(record)
            except Exception:
                pass
        return regressions

    def _build_from_integration(self, integration: dict[str, Any]) -> IntegrationState:
        """Build an IntegrationState from the integration dict (public helper)."""
        return self._build_state(integration)

    def _extract_evidence(
        self, integration: dict[str, Any]
    ) -> dict[str, tuple[str, ...]]:
        """Extract obligation → evidence mapping from the integration dict.

        Also enriches the mapping using any descent result present in *integration*.

        Parameters
        ----------
        integration:
            Integration dict.

        Returns
        -------
        dict[str, tuple[str, ...]]
        """
        raw = integration.get("evidence", {})
        result: dict[str, tuple[str, ...]] = {}
        for k, v in raw.items():
            if isinstance(v, tuple):
                result[str(k)] = v
            elif isinstance(v, list):
                result[str(k)] = tuple(str(x) for x in v)
            else:
                result[str(k)] = (str(v),)
        # Supplement with descent evidence
        descent = integration.get("descent_result")
        if descent is not None:
            descent_ev = self._descent_adaptor.extract_closure_evidence(descent)
            for obligation_id in integration.get("obligations", []):
                existing = result.get(obligation_id, ())
                result[obligation_id] = existing + descent_ev
        return result


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def run_pipeline(integration: dict[str, Any]) -> Any:
    """Convenience function: create a default pipeline and run it.

    Parameters
    ----------
    integration:
        Integration dict as documented in :class:`SemanticClosurePipeline`.

    Returns
    -------
    SemanticClosure
    """
    pipeline = SemanticClosurePipeline()
    return pipeline.run(integration)


def create_pipeline(
    strategy: str = "priority",
    max_rounds: int = 100,
) -> SemanticClosurePipeline:
    """Create a :class:`SemanticClosurePipeline` with the given parameters.

    Parameters
    ----------
    strategy:
        ``"priority"`` (default) or ``"breadth_first"``.
    max_rounds:
        Maximum number of closure rounds.

    Returns
    -------
    SemanticClosurePipeline
    """
    return SemanticClosurePipeline(
        strategy=strategy,
        max_rounds=max_rounds,
        enable_regression=True,
    )


def adapt_descent_result(descent_result: Any) -> tuple[str, ...]:
    """Convenience wrapper: adapt a single DescentResult to evidence tags.

    Parameters
    ----------
    descent_result:
        A :class:`~jugeo.geometry.descent.DescentResult`.

    Returns
    -------
    tuple[str, ...]
    """
    return DescentAdaptor().adapt(descent_result)


def adapt_goals_to_obligations(goals: list[Any]) -> list[str]:
    """Convenience wrapper: convert goals to obligation IDs.

    Parameters
    ----------
    goals:
        List of :class:`~jugeo.generation.goals.GenerationGoal` or compatible
        goal objects.

    Returns
    -------
    list[str]
    """
    return GoalAdaptor().goals_to_obligations(goals)


def adapt_construction_result(
    result: Any,
    obligation_id: str,
    patch_id: str = "",
) -> Any:
    """Convenience wrapper: adapt a ConstructionResult to a ClosureCheck.

    Parameters
    ----------
    result:
        A :class:`~jugeo.generation.construction.ConstructionResult`.
    obligation_id:
        The obligation this result pertains to.
    patch_id:
        Optional patch identifier.

    Returns
    -------
    ClosureCheck | None
    """
    return ConstructionAdaptor().result_to_closure_check(result, obligation_id, patch_id)


def integrate_gaps_into_frontier(gaps: list[Any], frontier: Any) -> int:
    """Convenience wrapper: add frontier nodes for *gaps*.

    Parameters
    ----------
    gaps:
        List of :class:`~jugeo.generation.semantic_closure.models.ClosureGap`.
    frontier:
        A :class:`~jugeo.orchestration.frontier.Frontier`.

    Returns
    -------
    int
        Number of nodes added.
    """
    return FrontierIntegrator().integrate(gaps, frontier)
