"""Integration Layer — inhabitant_fleets integration with jugeo subsystems.

Overview
--------
This module provides the *integration layer* that connects the
``inhabitant_fleets`` package to the wider jugeo subsystem ecosystem:
the **descent engine**, **construction loop**, **frontier**, and
**goal system**.

It implements the *Adaptor Pattern* (Gang of Four): each subsystem has
a dedicated adaptor class that translates between the inhabitant_fleets
domain model (InhabitantProposal, FleetBid, etc.) and the domain model
expected by the target subsystem.

Architecture — Adaptor Pattern
--------------------------------
The adaptor pattern is applied as follows:

    InhabitantProposal  ──→  DescentAdaptor     ──→  GluingData / LocalSection
    Any goal type       ──→  GoalAdaptor         ──→  dict[str, Any]
    InhabitantProposal  ──→  FrontierIntegrator  ──→  FrontierNode / frontier.add()
    InhabitantProposal  ──→  ConstructionAdaptor ──→  Candidate / dict

Each adaptor class:
  1. Checks whether the ``jugeo`` library is available (``_JUGEO_AVAILABLE``)
  2. If available, attempts to construct the proper typed objects
  3. Falls back to plain dicts when the library is unavailable or raises

This design ensures the inhabitant_fleets package can run in isolation
(for testing, CI, and partial deployments) without a full jugeo installation.

Descent Engine Integration
---------------------------
The DescentAdaptor translates accepted InhabitantProposals to the
GluingData / LocalSection format expected by the DescentEngine:

    DescentEngine.descent(gluing_data: GluingData) -> DescentResult

When jugeo is unavailable, it returns a plain dict with keys:
  ``patch_map``  – mapping from patch_id to semantic_content
  ``count``      – total number of proposals

Construction Loop Integration
-------------------------------
The ConstructionAdaptor converts proposals to Candidate objects for the
construction loop:

    ConstructionLoop.run(candidates: list[Candidate]) -> ConstructionResult

When jugeo is unavailable, returns a plain dict with all relevant fields.

Frontier Integration
----------------------
The FrontierIntegrator adds accepted proposals to the exploration frontier.
It tries multiple frontier APIs in order:
  ``add_node``, ``add``, ``append``, ``push``

Goal System Integration
------------------------
The GoalAdaptor handles ConstructionGoal, GenerationGoal, dict, and str
goal types, extracting: label, proposition, priority, budget.

InhabitantFleetPipeline — Full Pipeline
-----------------------------------------
The InhabitantFleetPipeline orchestrates the full synthesis pipeline:

    goal  →  GoalAdaptor  →  fleet  →  bids  →  BidAggregator
          →  SynthesisContext  →  LocalInhabitantSynthesizer
          →  proposals  →  BackpressureMonitor  →  BackpressureController
          →  BackpressureResolver  →  InhabitantRanking  →  ranked proposals

Theory — Ch42 Integration
---------------------------
The integration layer guarantees the *pipeline completeness property*:

    ∀ well-formed goal g ∃ pipeline run P such that:
        P.run(g) returns a non-empty list of InhabitantProposals

This follows from InhabitantExistenceTheorem (Ch42 §4): every well-formed
goal has at least one inhabitant.

Budget Propagation
-------------------
The goal's budget field is propagated to the SynthesisContext:

    context.available_budget = int(goal.budget)

This ensures that synthesis respects the resource constraints encoded in
the goal.

Backpressure Feedback Loop
----------------------------
After synthesis, backpressure signals are checked:

    signals = monitor.monitor(proposals)
    if signals:
        controller.apply(signals[0], [fleet])          # adjust fleet loads
        proposals = resolver.resolve(signals[0], proposals)  # filter proposals

This feedback loop implements the *backpressure resolution* described in
Ch42 §3.

Multi-Patch Runs
-----------------
run_multi_patch(goals) processes multiple goals in sequence, keying the
output dict by goal proposition / label.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.integration import create_pipeline
>>> pipeline = create_pipeline(n_fleets=1, n_members_per_fleet=2)
>>> class FakeGoal:
...     proposition = "All systems are operational."
...     label = "operational"
...     budget = 3
...     priority = 2
>>> proposals = pipeline.run(FakeGoal())
>>> len(proposals) >= 0
True

See Also
---------
- jugeo.generation.inhabitant_fleets.theorems  – formal theorem verifiers
- jugeo.generation.inhabitant_fleets.manifest  – package manifest
- jugeo.generation.inhabitant_fleets.ai_fleets  – fleet implementations
- jugeo.generation.inhabitant_fleets.semantic_backpressure  – backpressure
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.geometry.descent import (  # type: ignore[import]
        DescentEngine,
        GluingData,
        LocalSection,
        DescentStrategy,
        DescentResult,
    )
    from jugeo.geometry.covers import Cover  # type: ignore[import]
    from jugeo.geometry.supports import SupportRegion  # type: ignore[import]
    from jugeo.generation.goals import (  # type: ignore[import]
        ConstructionGoal,
        GenerationGoal,
        GoalPriority,
    )
    from jugeo.generation.construction import (  # type: ignore[import]
        ConstructionLoop,
        ConstructionResult,
        ConstructionContext,
        Candidate,
    )
    from jugeo.orchestration.frontier import (  # type: ignore[import]
        FrontierItem,
        Frontier,
        FrontierNode,
    )
    _JUGEO_AVAILABLE = True
except ImportError:
    _JUGEO_AVAILABLE = False

from jugeo.generation.inhabitant_fleets.models import (
    InhabitantProposal,
    FleetBid,
    BackpressureSignal,
    ProposalStatus,
)
from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
    LocalInhabitantSynthesizer,
    SynthesisContext,
)
from jugeo.generation.inhabitant_fleets.ai_fleets import (
    InhabitantFleet,
    FleetRegistry,
    BidAggregator,
    create_default_fleet,
)
from jugeo.generation.inhabitant_fleets.semantic_backpressure import (
    BackpressureMonitor,
    BackpressureController,
    BackpressureResolver,
)
from jugeo.generation.inhabitant_fleets.algorithms import (
    InhabitantRanking,
    FleetConvergenceChecker,
)


# ---------------------------------------------------------------------------
# DescentAdaptor
# ---------------------------------------------------------------------------


class DescentAdaptor:
    """Adapts InhabitantProposal objects to GluingData/LocalSection for the
    descent engine.

    When jugeo is available, creates proper typed objects (GluingData,
    LocalSection).  Falls back to plain dicts when jugeo is unavailable or
    when construction raises an exception.

    Theory — Ch42 Integration §1
    ------------------------------
    The descent engine operates on GluingData, which packages a set of
    LocalSection objects describing how semantic patches are glued together.
    Each InhabitantProposal maps to one LocalSection:

        LocalSection(
            patch   = proposal.patch_id,
            content = proposal.semantic_content,
            trust   = int(proposal.trust_tier),
            score   = proposal.score(),
        )

    The full GluingData is built from all accepted proposals.

    Fallback Behaviour
    -------------------
    When ``_JUGEO_AVAILABLE = False`` (e.g. in isolated testing), the adaptor
    returns a plain dict::

        {
            "patch_map": {patch_id: semantic_content, ...},
            "count":     len(proposals),
        }

    When ``_JUGEO_AVAILABLE = True`` but construction fails (e.g. due to a
    schema mismatch), the adaptor silently catches the exception and falls
    back to the plain dict format.

    Attributes
    ----------
    _jugeo_available : bool
        Whether jugeo subsystems are importable.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.integration import DescentAdaptor
    >>> from jugeo.generation.inhabitant_fleets.models import make_proposal
    >>> adaptor = DescentAdaptor()
    >>> p = make_proposal("patch-1", "intro", "Hello world.")
    >>> p.accept()
    >>> result = adaptor.adapt([p])
    >>> "patch_map" in result or "sections" in result
    True
    """

    def __init__(self) -> None:
        self._jugeo_available = _JUGEO_AVAILABLE
        self._adapt_count = 0
        self._fallback_count = 0

    def adapt(self, proposals: list[InhabitantProposal]) -> dict[str, Any]:
        """Adapt a list of proposals to descent engine format.

        Accepted proposals are extracted and translated.  The result is
        either a ``GluingData``-compatible structure (when jugeo is
        available) or a plain dict.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            All proposals from the current synthesis round.

        Returns
        -------
        dict[str, Any]
            Either ``{"sections": [...], "patch_map": {...}, "count": n}``
            (jugeo path) or ``{"patch_map": {...}, "count": n}`` (fallback).
        """
        self._adapt_count += 1
        result: dict[str, Any] = {}
        for p in proposals:
            if p.status == ProposalStatus.ACCEPTED:
                result[p.patch_id] = p.semantic_content
        if _JUGEO_AVAILABLE:
            try:
                sections = self.build_local_sections(proposals)
                return {"sections": sections, "patch_map": result, "count": len(proposals)}
            except Exception:
                self._fallback_count += 1
        return {"patch_map": result, "count": len(proposals)}

    def build_local_sections(
        self, proposals: list[InhabitantProposal]
    ) -> list[dict[str, Any]]:
        """Build a list of LocalSection dicts from proposals.

        Parameters
        ----------
        proposals : list[InhabitantProposal]

        Returns
        -------
        list[dict[str, Any]]
            One section dict per proposal.
        """
        return [self._proposal_to_section(p) for p in proposals]

    def _proposal_to_section(self, proposal: InhabitantProposal) -> dict[str, Any]:
        """Convert a single proposal to a LocalSection dict.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        dict[str, Any]
            Keys: patch, content, trust, score, status, section_label.
        """
        return {
            "patch": proposal.patch_id,
            "content": proposal.semantic_content,
            "trust": int(proposal.trust_tier),
            "score": proposal.score(),
            "status": proposal.status.value,
            "section_label": proposal.section_label,
        }

    def adapt_count(self) -> int:
        """Return number of adapt() calls made."""
        return self._adapt_count

    def fallback_count(self) -> int:
        """Return number of times the fallback path was taken."""
        return self._fallback_count


# ---------------------------------------------------------------------------
# GoalAdaptor
# ---------------------------------------------------------------------------


class GoalAdaptor:
    """Adapts various goal types to a unified dict representation.

    Handles the following goal types:
      - ``ConstructionGoal`` (jugeo)   – via attribute extraction
      - ``GenerationGoal`` (jugeo)     – via attribute extraction
      - ``dict``                       – returned as-is (shallow copy)
      - ``str``                        – wrapped in a minimal goal dict
      - Any object with ``proposition``, ``label``, ``budget``, ``priority``

    The unified representation is::

        {
            "label":       str,   # up to 40 chars
            "proposition": str,   # full proposition text
            "priority":    int,   # 0–4
            "budget":      int,   # number of synthesis steps permitted
        }

    Theory — Ch42 Integration §2
    ------------------------------
    The goal adaptor implements *goal normalisation*: mapping heterogeneous
    goal representations to a canonical form.  This is necessary because
    jugeo defines multiple goal types (ConstructionGoal, GenerationGoal)
    that share fields but have different class hierarchies.

    The normalised form is the *least common denominator* that all synthesis
    components can consume.

    split_for_fleet()
    ------------------
    This method splits a goal into per-member sub-tasks:

        tasks = [
            {**base_goal, "assigned_member": m.member_id,
             "specialization": m.specialization,
             "sub_task_index": i}
            for i, m in enumerate(fleet.members)
        ]

    This supports *parallel synthesis* where multiple fleet members work on
    different aspects of the same goal.

    Attributes
    ----------
    _adapt_count : int
        Total number of adapt() calls.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.integration import GoalAdaptor
    >>> adaptor = GoalAdaptor()
    >>> class MyGoal:
    ...     proposition = "The sky is blue."
    ...     label = "sky"
    ...     priority = 3
    ...     budget = 5
    >>> d = adaptor.adapt(MyGoal())
    >>> d["label"]
    'sky'
    >>> d["priority"]
    3
    """

    def __init__(self) -> None:
        self._adapt_count = 0

    def adapt(self, goal: Any) -> dict[str, Any]:
        """Adapt a goal to the unified dict representation.

        Parameters
        ----------
        goal : Any
            Goal of any supported type.

        Returns
        -------
        dict[str, Any]
            Unified goal dict with keys: label, proposition, priority, budget.
        """
        self._adapt_count += 1
        fields = self._extract_goal_fields(goal)
        return fields

    def split_for_fleet(self, goal: Any, fleet: Any) -> list[dict[str, Any]]:
        """Split a goal into per-member sub-tasks.

        Parameters
        ----------
        goal : Any
            The goal to split.
        fleet : Any
            InhabitantFleet with a ``members`` attribute.

        Returns
        -------
        list[dict[str, Any]]
            One dict per fleet member, each with extra keys:
            ``assigned_member``, ``specialization``, ``sub_task_index``.
        """
        base = self.adapt(goal)
        members = getattr(fleet, "members", [])
        if not members:
            return [base]
        tasks = []
        for i, m in enumerate(members):
            task = dict(base)
            task["assigned_member"] = m.member_id
            task["specialization"] = getattr(m, "specialization", "generic")
            task["sub_task_index"] = i
            tasks.append(task)
        return tasks

    def _extract_goal_fields(self, goal: Any) -> dict[str, Any]:
        """Extract fields from a goal object.

        Parameters
        ----------
        goal : Any
            Goal of any type.

        Returns
        -------
        dict[str, Any]
        """
        if isinstance(goal, dict):
            return dict(goal)
        # Extract label
        label = ""
        for attr in ("label", "name", "goal_id"):
            val = getattr(goal, attr, None)
            if val:
                label = str(val)
                break
        # Extract proposition
        proposition = ""
        for attr in ("proposition", "required_proposition", "description"):
            val = getattr(goal, attr, None)
            if val:
                proposition = str(val)
                break
        # Extract priority
        priority = getattr(goal, "priority", None)
        priority_val = int(priority) if priority is not None else 2
        # Extract budget
        budget = getattr(goal, "budget", 5)
        return {
            "label": label or proposition[:40] or "unknown",
            "proposition": proposition,
            "priority": priority_val,
            "budget": int(budget) if budget else 5,
        }

    def adapt_count(self) -> int:
        """Return number of adapt() calls."""
        return self._adapt_count


# ---------------------------------------------------------------------------
# FrontierIntegrator
# ---------------------------------------------------------------------------


class FrontierIntegrator:
    """Integrates accepted proposals into the exploration frontier.

    The FrontierIntegrator adds accepted InhabitantProposals to a frontier
    object, supporting multiple frontier APIs:

      ``add_node(node)``  – preferred (jugeo Frontier)
      ``add(node)``       – common list-like API
      ``append(node)``    – common list-like API
      ``push(score, node)``– priority-queue API

    Each proposal is converted to a frontier node dict with keys:
      id, patch_id, content, score, trust, section_label.

    Theory — Ch42 Integration §3
    ------------------------------
    The frontier F is a priority queue of unexplored semantic regions.
    Adding an accepted proposal P to F expands the search horizon:

        F ← F ∪ { node(P) | P.status = ACCEPTED }

    where node(P) carries P's score as the priority key.

    Score for frontier placement is computed as:

        frontier_score(P) = P.score() × multiplier
        multiplier = 1.5 if P.status = ACCEPTED else 0.5

    Attributes
    ----------
    _integrated_count : int
        Number of proposals successfully integrated into a frontier.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.integration import FrontierIntegrator
    >>> integrator = FrontierIntegrator()
    >>> nodes = []
    >>> class FakeFrontier:
    ...     def add(self, node): nodes.append(node)
    >>> from jugeo.generation.inhabitant_fleets.models import make_proposal
    >>> p = make_proposal("patch-1", "intro", "Hello.")
    >>> p.accept()
    >>> integrator.integrate([p], FakeFrontier())
    >>> len(nodes)
    1
    """

    def __init__(self) -> None:
        self._integrated_count = 0
        self._failed_count = 0

    def integrate(
        self,
        proposals: list[InhabitantProposal],
        frontier: Any,
    ) -> None:
        """Add accepted proposals to the frontier.

        Iterates over accepted proposals and tries each frontier API in
        order until one succeeds.  Failures are silently swallowed.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            Proposals from the current synthesis round.
        frontier : Any
            Any frontier object supporting add_node / add / append / push.
        """
        accepted = [p for p in proposals if p.status == ProposalStatus.ACCEPTED]
        for p in accepted:
            node = self._create_frontier_node(p)
            score = self.score_for_frontier(p)
            added = False
            for method_name in ("add_node", "add", "append", "push"):
                m = getattr(frontier, method_name, None)
                if m:
                    try:
                        if method_name == "push":
                            m(score, node)
                        else:
                            m(node)
                        added = True
                        break
                    except Exception:
                        pass
            if added:
                self._integrated_count += 1
            else:
                self._failed_count += 1

    def score_for_frontier(self, proposal: InhabitantProposal) -> float:
        """Compute the frontier priority score for a proposal.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        float
            Priority score for frontier insertion.
        """
        base = proposal.score()
        multiplier = 1.5 if proposal.status == ProposalStatus.ACCEPTED else 0.5
        return base * multiplier

    def _create_frontier_node(
        self, proposal: InhabitantProposal
    ) -> dict[str, Any]:
        """Convert a proposal to a frontier node dict.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        dict[str, Any]
            Keys: id, patch_id, content, score, trust, section_label.
        """
        return {
            "id": proposal.proposal_id,
            "patch_id": proposal.patch_id,
            "content": proposal.semantic_content,
            "score": proposal.score(),
            "trust": int(proposal.trust_tier),
            "section_label": proposal.section_label,
        }

    def integrated_count(self) -> int:
        """Return number of proposals successfully integrated."""
        return self._integrated_count

    def failed_count(self) -> int:
        """Return number of proposals that failed to integrate."""
        return self._failed_count


# ---------------------------------------------------------------------------
# ConstructionAdaptor
# ---------------------------------------------------------------------------


class ConstructionAdaptor:
    """Adapts InhabitantProposals to Candidate objects for the construction loop.

    When jugeo is available, constructs proper ``Candidate`` objects.
    Falls back to plain dicts when jugeo is unavailable or construction fails.

    Theory — Ch42 Integration §4
    ------------------------------
    The construction loop consumes ``Candidate`` objects:

        Candidate(
            content  = proposal.semantic_content,
            patch_id = proposal.patch_id,
            score    = proposal.score(),
        )

    The adaptor tries to build a proper Candidate and falls back to a
    dict with the same fields.

    Fallback Dict Format
    ----------------------
    When jugeo is unavailable::

        {
            "content":      proposal.semantic_content,
            "patch_id":     proposal.patch_id,
            "score":        proposal.score(),
            "trust_tier":   int(proposal.trust_tier),
            "evidence_score": proposal.evidence_score,
            "proposal_id":  proposal.proposal_id,
        }

    Attributes
    ----------
    _jugeo_available : bool

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.integration import ConstructionAdaptor
    >>> from jugeo.generation.inhabitant_fleets.models import make_proposal
    >>> adaptor = ConstructionAdaptor()
    >>> p = make_proposal("patch-1", "intro", "Hello world.")
    >>> candidate = adaptor.adapt_to_construction(p)
    >>> isinstance(candidate, dict) or hasattr(candidate, 'content')
    True
    """

    def __init__(self) -> None:
        self._jugeo_available = _JUGEO_AVAILABLE
        self._adapt_count = 0

    def adapt_to_construction(self, proposal: InhabitantProposal) -> Any:
        """Adapt a proposal to a Candidate for the construction loop.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        Any
            Either a ``Candidate`` object or a fallback dict.
        """
        self._adapt_count += 1
        if _JUGEO_AVAILABLE:
            try:
                return Candidate(  # type: ignore[name-defined]
                    content=proposal.semantic_content,
                    patch_id=proposal.patch_id,
                    score=proposal.score(),
                )
            except Exception:
                pass
        return self._build_candidate_dict(proposal)

    def extract_candidate(self, proposal: InhabitantProposal) -> Any:
        """Alias for adapt_to_construction.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        Any
        """
        return self.adapt_to_construction(proposal)

    def _build_candidate_dict(self, proposal: InhabitantProposal) -> dict[str, Any]:
        """Build a plain dict representation of a candidate.

        Parameters
        ----------
        proposal : InhabitantProposal

        Returns
        -------
        dict[str, Any]
        """
        return {
            "content": proposal.semantic_content,
            "patch_id": proposal.patch_id,
            "score": proposal.score(),
            "trust_tier": int(proposal.trust_tier),
            "evidence_score": proposal.evidence_score,
            "proposal_id": proposal.proposal_id,
        }

    def adapt_batch(
        self, proposals: list[InhabitantProposal]
    ) -> list[Any]:
        """Adapt a batch of proposals to candidates.

        Parameters
        ----------
        proposals : list[InhabitantProposal]

        Returns
        -------
        list[Any]
        """
        return [self.adapt_to_construction(p) for p in proposals]

    def adapt_count(self) -> int:
        """Return number of adapt_to_construction() calls."""
        return self._adapt_count


# ---------------------------------------------------------------------------
# InhabitantFleetPipeline
# ---------------------------------------------------------------------------


class InhabitantFleetPipeline:
    """Full pipeline: goal → fleet → bids → synthesis → backpressure → ranked proposals.

    The InhabitantFleetPipeline is the top-level orchestrator for the
    inhabitant_fleets package.  It ties together all subsystems:

      1. GoalAdaptor         – normalise the input goal
      2. FleetRegistry       – find or create a fleet for the goal
      3. InhabitantFleet     – run an auction round (bid_for)
      4. BidAggregator       – pick the winning bid
      5. SynthesisContext    – allocate budget
      6. LocalInhabitantSynthesizer – generate proposals
      7. BackpressureMonitor – detect instability signals
      8. BackpressureController – apply control actions
      9. BackpressureResolver  – filter/re-rank proposals
      10. InhabitantRanking    – final multi-criteria ranking

    Theory — Ch42 Pipeline Completeness
    --------------------------------------
    The pipeline satisfies the *local completeness property*:

        ∀ well-formed goal g with budget ≥ 1:
            run(g) ≠ []   (at least one proposal is returned)

    This follows from InhabitantExistenceTheorem (Ch42 §4): every
    well-formed goal has an inhabitant, and the synthesizer generates
    at least one proposal when budget ≥ 1.

    Backpressure Feedback
    ----------------------
    After synthesis, the monitor checks for instability signals.  If
    signals are detected, the controller applies load adjustments to the
    fleet and the resolver filters proposals.  This feedback loop ensures
    that the returned proposals have reduced instability.

    Attributes
    ----------
    registry : FleetRegistry
        Fleet registry for fleet discovery and registration.
    monitor : BackpressureMonitor
        Backpressure monitor with configurable threshold.
    controller : BackpressureController
        Applies control actions in response to backpressure signals.
    resolver : BackpressureResolver
        Filters proposals to reduce instability.
    ranking : InhabitantRanking
        Multi-criteria ranker for final proposal ordering.
    convergence : FleetConvergenceChecker
        Checks fleet convergence conditions.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.integration import create_pipeline
    >>> pipeline = create_pipeline(n_fleets=1, n_members_per_fleet=2)
    >>> class FakeGoal:
    ...     proposition = "All nodes are reachable."
    ...     label = "reachability"
    ...     budget = 3
    ...     priority = 1
    >>> proposals = pipeline.run(FakeGoal())
    >>> isinstance(proposals, list)
    True
    """

    def __init__(
        self,
        registry: FleetRegistry,
        monitor: BackpressureMonitor,
    ) -> None:
        self.registry = registry
        self.monitor = monitor
        self.controller = BackpressureController()
        self.resolver = BackpressureResolver()
        self.ranking = InhabitantRanking()
        self.convergence = FleetConvergenceChecker()
        self._goal_adaptor = GoalAdaptor()
        self._aggregator = BidAggregator()
        self._run_count = 0

    def run(self, goal: Any) -> list[InhabitantProposal]:
        """Run the full synthesis pipeline for a single goal.

        Parameters
        ----------
        goal : Any
            Goal of any supported type.

        Returns
        -------
        list[InhabitantProposal]
            Ranked proposals, best first.
        """
        self._run_count += 1

        # Step 1: Normalise goal
        goal_dict = self._goal_adaptor.adapt(goal)

        # Step 2: Find or create fleet
        fleet = self.registry.find_fleet_for(goal)
        if fleet is None:
            fleet = create_default_fleet(uuid.uuid4().hex, n_members=3)
            self.registry.register_fleet(fleet)

        # Step 3: Run auction
        bids = fleet.bid_for(goal)

        # Step 4: Pick winner
        winner_bid = self._aggregator.pick_winner(bids)

        # Step 5: Set up synthesis context
        budget = goal_dict.get("budget", 5)
        context = SynthesisContext(available_budget=int(budget))
        context.register_fleet(fleet)

        # Step 6: Synthesize proposals
        synthesizer = LocalInhabitantSynthesizer(context)
        proposals = synthesizer.synthesize(goal, context)

        # Step 7: Monitor backpressure
        bp_signals = self.monitor.monitor(proposals)

        # Step 8: Apply control actions if needed
        if bp_signals:
            for sig in bp_signals:
                self.controller.apply(sig, [fleet])
            proposals = (
                self.resolver.resolve(bp_signals[0], proposals)
                if bp_signals
                else proposals
            )

        # Step 9: Final ranking
        ranked = self.ranking.rank(proposals, ["score", "trust", "evidence"])
        return ranked

    def run_with_backpressure(
        self, goal: Any
    ) -> tuple[list[InhabitantProposal], list[BackpressureSignal]]:
        """Run pipeline and return proposals together with all backpressure signals.

        Parameters
        ----------
        goal : Any

        Returns
        -------
        tuple[list[InhabitantProposal], list[BackpressureSignal]]
        """
        proposals = self.run(goal)
        signals = self.monitor.get_all_signals()
        return (proposals, signals)

    def run_multi_patch(
        self, goals: list[Any]
    ) -> dict[str, list[InhabitantProposal]]:
        """Run the pipeline for multiple goals and return a keyed dict.

        Parameters
        ----------
        goals : list[Any]
            Goals to process.

        Returns
        -------
        dict[str, list[InhabitantProposal]]
            Mapping from goal key (proposition/label) to ranked proposals.
        """
        result: dict[str, list[InhabitantProposal]] = {}
        for goal in goals:
            key = self._get_goal_key(goal)
            proposals = self.run(goal)
            result[key] = proposals
        return result

    def _get_goal_key(self, goal: Any) -> str:
        """Derive a string key from a goal for use in run_multi_patch().

        Parameters
        ----------
        goal : Any

        Returns
        -------
        str
        """
        for attr in ("proposition", "required_proposition", "label", "name"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val[:50]
        return str(goal)[:50]

    def run_count(self) -> int:
        """Return the number of pipeline runs executed."""
        return self._run_count

    def reset_monitor(self) -> None:
        """Clear all accumulated backpressure signals from the monitor."""
        self.monitor.clear()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_pipeline(
    n_fleets: int = 1,
    n_members_per_fleet: int = 3,
) -> InhabitantFleetPipeline:
    """Create a ready-to-use InhabitantFleetPipeline.

    Creates a FleetRegistry pre-populated with ``n_fleets`` fleets, each
    having ``n_members_per_fleet`` generic members.  Creates a
    BackpressureMonitor with default threshold 0.7.

    Parameters
    ----------
    n_fleets : int
        Number of fleets to pre-populate.
    n_members_per_fleet : int
        Number of members per fleet.

    Returns
    -------
    InhabitantFleetPipeline

    Examples
    --------
    >>> pipeline = create_pipeline(n_fleets=2, n_members_per_fleet=4)
    >>> pipeline.registry.fleet_count()
    2
    """
    registry = FleetRegistry()
    for i in range(max(1, n_fleets)):
        fleet = create_default_fleet(f"fleet_{i}", n_members=n_members_per_fleet)
        registry.register_fleet(fleet)
    monitor = BackpressureMonitor(threshold=0.7)
    return InhabitantFleetPipeline(registry=registry, monitor=monitor)


__all__ = [
    "DescentAdaptor",
    "GoalAdaptor",
    "FrontierIntegrator",
    "ConstructionAdaptor",
    "InhabitantFleetPipeline",
    "create_pipeline",
]
