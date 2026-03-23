"""
Copilot in Construction — theory2.tex §39

This module implements the copilot's role in the local construction process described
in Chapter 39 of theory2.tex.  The copilot is an intelligent assistant that
participates at every stage of the construction loop — not as a passive observer but
as an active reasoner that shapes the trajectory of each elaboration.

Theory
------
The copilot acts as an intelligent assistant throughout the local construction process.
It proposes candidate inhabitants by analysing the goal structure, evaluating
feasibility using trust and obligation metrics, suggesting interface refinements when
sections fail compliance checks, mediating interface negotiations between competing
loops, generating elaboration schedules that minimise conflicts, detecting and
explaining stalls, proposing budget reallocations when one loop is blocked, explaining
verification failures in human-readable terms, synthesising construction summaries for
downstream consumers, and adapting its strategy based on feedback from previous
construction attempts.

Conceptually the copilot occupies a meta-level above any single local construction
loop: it can observe the full elaboration state, compare loops across sections, and
propose cross-cutting interventions that no single loop could initiate on its own.

The copilot maintains a persistent session (identified by ``session_id``) across
multiple elaborations.  Its internal memory accumulates feedback from every
construction attempt, allowing it to evolve its strategy over time.  Early in a
session the copilot is exploratory — it generates a broad set of candidate proposals
covering diverse law subsets.  Later, as its feedback memory fills, it becomes
focused — concentrating proposals on the law subsets and interface patterns that have
historically been most successful.

The copilot's strategy has three principal modes:

``"solver"``
    Attempt to satisfy all goal obligations directly.  Works best when the
    obligation set is small and the available laws are sufficient.

``"analogy"``
    Look up the proposal history for similar past goals and adapt the most
    successful past candidates.  Works best when the session has accumulated
    enough history.

``"enumeration"``
    Systematically enumerate feasible subsets of the goal's law set.  Works
    best as a fallback when both solver and analogy fail.

The copilot switches modes automatically via :meth:`adapt_strategy_to_feedback`.

Integration
-----------
The copilot integrates with :class:`CoordinatedElaborationEngine` (module
``coordinated_elaboration``) in two ways:

1. It subscribes to elaboration trace events and uses them to drive
   :meth:`adapt_strategy_to_feedback`.
2. It provides ``generate_elaboration_schedule`` to the engine, which can
   replace the engine's built-in schedule with the copilot's critical-path
   aware schedule.

The copilot's outputs are traceable: every proposal, every negotiation record,
and every strategy adaptation is stored in ``self._proposal_history``,
``self._negotiation_records``, and ``self._feedback_memory`` respectively.
These can be serialised for audit purposes.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jugeo.generation.local_construction.local_construction_loop import (
        LocalConstructionLoop,
        InterfaceDiscipline,
        CoordinatedElaboration,
    )

from jugeo.generation.goals import GenerationGoal
from jugeo.generation.construction import ConstructionContext, Candidate

__all__ = [
    "CopilotConstructionParticipant",
    "CopilotProposal",
    "CopilotNegotiationRecord",
    "CopilotStrategyState",
    "StrategyAdaptation",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CopilotProposal:
    """
    A record of a single batch of candidates proposed by the copilot.

    The copilot records every proposal it makes, together with whether the
    proposal was accepted by the construction loop and any feedback received
    from the verification stage.  Over time these records feed
    :meth:`CopilotConstructionParticipant.adapt_strategy_to_feedback`.

    Attributes
    ----------
    proposal_id:
        Unique identifier minted at proposal time.
    goal_id:
        The :class:`GenerationGoal` this proposal targets.
    candidates:
        The raw candidate dicts returned to the caller.
    strategy:
        The strategy mode active when this proposal was generated
        (``"solver"``, ``"analogy"``, or ``"enumeration"``).
    copilot_session_id:
        The session in which the copilot produced this proposal.
    reasoning:
        Free-text explanation of why the copilot chose these candidates.
    accepted:
        ``True`` if at least one candidate was subsequently selected by the
        construction loop, ``False`` if all were rejected, ``None`` if not yet
        known.
    feedback_received:
        Optional feedback string supplied by the caller after the proposal
        was evaluated.
    created_at:
        Unix timestamp.
    """

    proposal_id: str
    goal_id: str
    candidates: list[dict[str, Any]]
    strategy: str
    copilot_session_id: str
    reasoning: str
    accepted: bool | None = None
    feedback_received: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class CopilotNegotiationRecord:
    """
    A record of a single copilot-mediated interface negotiation.

    The copilot acts as a neutral mediator when two loops are in conflict.
    Each mediation session is logged here so that the copilot can learn
    which mediation strategies tend to succeed for which conflict types.

    Attributes
    ----------
    negotiation_id:
        Unique identifier.
    loop_a_id:
        First participant loop.
    loop_b_id:
        Second participant loop.
    mediation_id:
        The ``mediation_id`` returned by
        :meth:`CopilotConstructionParticipant.mediate_interface_negotiation`.
    strategy_used:
        One of ``"split_the_difference"``, ``"temporal_sequencing"``, or
        ``"common_ground"``.
    rounds_taken:
        Number of mediation rounds executed before agreement (or failure).
    agreement_reached:
        Whether the mediation produced a compatible interface state.
    created_at:
        Unix timestamp.
    """

    negotiation_id: str
    loop_a_id: str
    loop_b_id: str
    mediation_id: str
    strategy_used: str
    rounds_taken: int
    agreement_reached: bool
    created_at: float = field(default_factory=time.time)


@dataclass
class CopilotStrategyState:
    """
    A snapshot of the copilot's current strategy parameters.

    The copilot's strategy is not static.  :meth:`adapt_strategy_to_feedback`
    updates the parameters in ``self._strategy_params`` and creates a new
    ``CopilotStrategyState`` snapshot each time the strategy changes.

    Attributes
    ----------
    session_id:
        The copilot session these parameters belong to.
    proposal_strategy:
        Current active strategy mode.
    trust_threshold:
        Minimum trust score for a candidate to be returned.
    max_proposals:
        Maximum candidates to return per goal.
    adaptation_count:
        How many times the strategy has been adapted in this session.
    last_adapted_at:
        Unix timestamp of the most recent adaptation.
    performance_history:
        List of per-round success rates (fraction of proposals accepted),
        used to compute a rolling average for the strategy health metric.
    """

    session_id: str
    proposal_strategy: str
    trust_threshold: float
    max_proposals: int
    adaptation_count: int
    last_adapted_at: float
    performance_history: list[float] = field(default_factory=list)


@dataclass
class StrategyAdaptation:
    """
    A record of a single strategy adaptation event.

    Each time :meth:`CopilotConstructionParticipant.adapt_strategy_to_feedback`
    changes ``self._strategy_params``, it creates one of these records.

    Attributes
    ----------
    adaptation_id:
        Unique identifier.
    trigger:
        Short description of the feedback pattern that triggered the adaptation,
        e.g. ``"majority_trust_failures"`` or ``"majority_stalls"``.
    old_params:
        Copy of ``self._strategy_params`` before the adaptation.
    new_params:
        Copy of ``self._strategy_params`` after the adaptation.
    rationale:
        Human-readable explanation of the adaptation.
    created_at:
        Unix timestamp.
    """

    adaptation_id: str
    trigger: str
    old_params: dict[str, Any]
    new_params: dict[str, Any]
    rationale: str
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Main copilot participant class
# ---------------------------------------------------------------------------


class CopilotConstructionParticipant:
    """
    The copilot's active presence in the local construction process.

    The copilot is not a passive observer.  It proposes, evaluates, mediates,
    schedules, diagnoses, reallocates, explains, summarises, and adapts.  Each
    of these activities is represented by one of the ten core methods below.

    The copilot maintains state across multiple elaborations within a single
    session.  Its ``session_id`` uniquely identifies this lifetime.  All
    proposals, negotiations, and adaptations are recorded in instance
    attributes and can be serialised for audit or replay.

    Parameters
    ----------
    config:
        Optional configuration overrides.  See :meth:`__init__` for valid keys.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialise the copilot with the given configuration.

        Configuration keys
        ------------------
        proposal_strategy : str
            Initial proposal strategy (``"adaptive"``, ``"solver"``,
            ``"analogy"``, or ``"enumeration"``).  Default ``"adaptive"``.
        max_proposals_per_goal : int
            Hard ceiling on the number of candidate dicts returned per call to
            :meth:`propose_candidates_for_goal`.  Default 10.
        trust_threshold : float
            Minimum trust score (0–1) for a candidate to pass the filter.
            Default 0.3.
        interface_negotiation_rounds : int
            Max rounds the copilot will attempt in
            :meth:`mediate_interface_negotiation`.  Default 5.
        schedule_strategy : str
            Strategy used by :meth:`generate_elaboration_schedule`:
            ``"critical_path"`` or ``"greedy"``.  Default ``"critical_path"``.
        stall_explanation_depth : int
            How many levels of cause analysis
            :meth:`detect_and_explain_stalls` performs.  Default 3.
        budget_reallocation_policy : str
            Policy for :meth:`propose_budget_reallocation`:
            ``"proportional"`` or ``"critical_path"``.  Default
            ``"proportional"``.
        summary_verbosity : str
            Verbosity of :meth:`synthesize_construction_summary`:
            ``"brief"``, ``"standard"``, or ``"detailed"``.  Default
            ``"detailed"``.
        adaptation_memory_size : int
            Maximum number of feedback records retained in
            ``self._feedback_memory``.  Oldest entries are evicted when the
            limit is exceeded.  Default 20.
        """
        cfg = config or {}
        self._config: dict[str, Any] = {
            "proposal_strategy": cfg.get("proposal_strategy", "adaptive"),
            "max_proposals_per_goal": cfg.get("max_proposals_per_goal", 10),
            "trust_threshold": cfg.get("trust_threshold", 0.3),
            "interface_negotiation_rounds": cfg.get(
                "interface_negotiation_rounds", 5
            ),
            "schedule_strategy": cfg.get("schedule_strategy", "critical_path"),
            "stall_explanation_depth": cfg.get("stall_explanation_depth", 3),
            "budget_reallocation_policy": cfg.get(
                "budget_reallocation_policy", "proportional"
            ),
            "summary_verbosity": cfg.get("summary_verbosity", "detailed"),
            "adaptation_memory_size": cfg.get("adaptation_memory_size", 20),
        }
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._proposal_history: list[dict[str, Any]] = []
        self._feedback_memory: list[dict[str, Any]] = []
        self._negotiation_records: list[dict[str, Any]] = []
        self._strategy_params: dict[str, Any] = {
            "proposal_strategy": self._config["proposal_strategy"],
            "trust_threshold": self._config["trust_threshold"],
            "max_proposals": self._config["max_proposals_per_goal"],
        }
        self._session_id: str = str(uuid.uuid4())
        self._strategy_adaptations: list[StrategyAdaptation] = []
        self._logger.info("Copilot session initialised: %s", self._session_id)

    # ------------------------------------------------------------------
    # Core copilot methods
    # ------------------------------------------------------------------

    def propose_candidates_for_goal(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
    ) -> list[dict[str, Any]]:
        """
        Propose a list of candidate inhabitants for *goal* in *context*.

        This is the copilot's primary contribution to the local construction
        loop.  The copilot uses one of three strategies — ``"solver"``,
        ``"analogy"``, or ``"enumeration"`` — to generate its proposals and
        then filters the results by trust score.

        Strategy selection
        ------------------
        When the configured strategy is ``"adaptive"``, the copilot reads
        ``self._strategy_params["proposal_strategy"]`` (which is updated by
        :meth:`adapt_strategy_to_feedback`) to decide which of the three
        concrete strategies to apply.

        Trust scoring
        -------------
        Each candidate's trust score is a weighted combination of:

        * **Law coverage** (weight 0.35): ``laws_applied / len(goal.laws)``
        * **Obligation resolution** (weight 0.40): fraction of goal's
          obligations absent from the candidate's residuals.
        * **Context binding richness** (weight 0.25): fraction of context
          bindings that are non-empty.

        The copilot also attaches metadata:
        ``"copilot_session_id"``, ``"proposal_strategy"``, and
        ``"reasoning"`` (a free-text explanation).

        Parameters
        ----------
        goal:
            The goal for which candidates are requested.
        context:
            The construction context providing bindings and evidence.

        Returns
        -------
        list[dict]
            Filtered candidate dicts, each enriched with copilot metadata.
            At most ``max_proposals_per_goal`` entries; all have trust score
            ≥ ``trust_threshold``.
        """
        strategy = self._strategy_params.get("proposal_strategy", "solver")
        if self._config["proposal_strategy"] == "adaptive":
            # Use dynamically updated strategy
            active_strategy = strategy
        else:
            active_strategy = self._config["proposal_strategy"]

        max_proposals: int = self._strategy_params.get(
            "max_proposals", self._config["max_proposals_per_goal"]
        )
        trust_floor: float = self._strategy_params.get(
            "trust_threshold", self._config["trust_threshold"]
        )

        raw_candidates: list[dict[str, Any]] = []

        if active_strategy == "solver":
            raw_candidates = self._propose_via_solver(goal, context, max_proposals)
        elif active_strategy == "analogy":
            raw_candidates = self._propose_via_analogy(goal, context, max_proposals)
        elif active_strategy == "enumeration":
            raw_candidates = self._propose_via_enumeration(goal, context, max_proposals)
        else:
            # Default: try solver first, fall back to enumeration
            raw_candidates = self._propose_via_solver(goal, context, max_proposals)
            if len(raw_candidates) < 2:
                raw_candidates += self._propose_via_enumeration(
                    goal, context, max_proposals - len(raw_candidates)
                )

        # Score and filter
        laws_total = max(len(goal.laws), 1)
        obligations_total = max(len(goal.obligations), 1)
        bindings_total = max(len(context.bindings), 1)

        scored: list[dict[str, Any]] = []
        for cand in raw_candidates:
            laws_applied = cand.get("payload", {}).get("laws_applied", [])
            residuals = cand.get("residual_obligations", ())
            law_coverage = len(laws_applied) / laws_total
            obligation_resolved = (
                obligations_total - len(residuals)
            ) / obligations_total
            binding_richness = sum(
                1 for v in context.bindings.values() if v
            ) / bindings_total

            trust_score = (
                0.35 * law_coverage
                + 0.40 * obligation_resolved
                + 0.25 * binding_richness
            )
            trust_score = min(max(trust_score, 0.0), 1.0)

            if trust_score < trust_floor:
                continue

            cand["trust_score"] = trust_score
            cand["copilot_session_id"] = self._session_id
            cand["proposal_strategy"] = active_strategy
            if "reasoning" not in cand:
                cand["reasoning"] = (
                    f"Copilot ({active_strategy}): law_coverage={law_coverage:.2f}, "
                    f"obligation_resolved={obligation_resolved:.2f}, "
                    f"binding_richness={binding_richness:.2f}"
                )
            scored.append(cand)

        # Sort by trust descending, cap at max_proposals
        scored.sort(key=lambda c: c.get("trust_score", 0.0), reverse=True)
        proposals = scored[:max_proposals]

        # Record for adaptation
        record: dict[str, Any] = {
            "proposal_id": str(uuid.uuid4()),
            "goal_id": goal.goal_id,
            "strategy": active_strategy,
            "count": len(proposals),
            "avg_trust": (
                sum(p.get("trust_score", 0.0) for p in proposals) / max(len(proposals), 1)
            ),
            "created_at": time.time(),
            "copilot_session_id": self._session_id,
        }
        self._proposal_history.append(record)
        self._logger.debug(
            "Copilot proposed %d candidates for goal %s (strategy=%s, avg_trust=%.3f).",
            len(proposals),
            goal.goal_id,
            active_strategy,
            record["avg_trust"],
        )
        return proposals

    def evaluate_candidate_feasibility(
        self,
        candidate: dict[str, Any],
        goal: GenerationGoal,
    ) -> dict[str, Any]:
        """
        Perform a rich multi-dimensional feasibility evaluation of *candidate*.

        The copilot checks five independent dimensions and combines them into a
        single weighted feasibility score.  A candidate is considered feasible
        when the combined score is ≥ 0.5.

        Checks
        ------
        1. **Law coverage** (weight 0.30):
           ``len(laws_applied) / len(goal.laws)``
        2. **Obligation resolution** (weight 0.40):
           ``(total_obligations - residual_count) / total_obligations``
        3. **Trust threshold** (weight 0.20):
           ``1.0`` if ``trust_score >= trust_threshold``, else scaled value.
        4. **Budget compatibility** (weight 0.10):
           ``1.0`` if ``estimated_cost <= 1.0``, else ``max(0, 1 - overshoot)``.
        5. **Semantic coherence** (unweighted gate):
           Applied laws must not contain obvious mutual exclusions.

        Parameters
        ----------
        candidate:
            A candidate dict with at least ``payload``, ``trust_score``, and
            ``residual_obligations`` keys.
        goal:
            The goal against which feasibility is measured.

        Returns
        -------
        dict
            Keys: ``feasible`` (bool), ``feasibility_score`` (float 0–1),
            ``checks`` (per-check results), ``recommendation`` (str),
            ``improvement_hints`` (list[str]), ``copilot_confidence`` (float).
        """
        laws_applied: list[str] = candidate.get("payload", {}).get(
            "laws_applied", []
        )
        residuals: tuple[Any, ...] = candidate.get("residual_obligations", ())
        trust_score: float = candidate.get("trust_score", 0.0)

        laws_total = max(len(goal.laws), 1)
        obligations_total = max(len(goal.obligations), 1)

        # Check 1: law coverage
        law_coverage = len(laws_applied) / laws_total
        check_law: dict[str, Any] = {
            "score": law_coverage,
            "passed": law_coverage >= 0.5,
            "detail": f"{len(laws_applied)}/{laws_total} laws applied",
        }

        # Check 2: obligation resolution
        resolved_count = obligations_total - len(residuals)
        obligation_fraction = resolved_count / obligations_total
        check_obligation: dict[str, Any] = {
            "score": obligation_fraction,
            "passed": obligation_fraction >= 0.5,
            "detail": f"{resolved_count}/{obligations_total} obligations resolved",
        }

        # Check 3: trust threshold
        trust_floor = self._strategy_params.get(
            "trust_threshold", self._config["trust_threshold"]
        )
        trust_pass = trust_score >= trust_floor
        trust_check_score = min(trust_score / max(trust_floor, 1e-9), 1.0)
        check_trust: dict[str, Any] = {
            "score": trust_check_score,
            "passed": trust_pass,
            "detail": f"trust={trust_score:.3f}, threshold={trust_floor:.3f}",
        }

        # Check 4: budget compatibility
        estimated_cost = len(residuals) * 0.1
        budget_score = max(0.0, 1.0 - max(0.0, estimated_cost - 1.0))
        check_budget: dict[str, Any] = {
            "score": budget_score,
            "passed": estimated_cost <= 1.0,
            "detail": f"estimated_cost={estimated_cost:.3f}",
        }

        # Check 5: semantic coherence (basic mutual-exclusion heuristic)
        incoherence_pairs: list[tuple[str, str]] = []
        for i, la in enumerate(laws_applied):
            for lb in laws_applied[i + 1 :]:
                # Heuristic: "neg_X" and "X" in the same list are incoherent
                if la.startswith("neg_") and la[4:] == lb:
                    incoherence_pairs.append((la, lb))
                elif lb.startswith("neg_") and lb[4:] == la:
                    incoherence_pairs.append((la, lb))
        coherent = len(incoherence_pairs) == 0
        check_coherence: dict[str, Any] = {
            "score": 1.0 if coherent else 0.0,
            "passed": coherent,
            "detail": (
                "coherent"
                if coherent
                else f"incoherent pairs: {incoherence_pairs}"
            ),
        }

        # Combined weighted score
        feasibility_score = (
            0.30 * law_coverage
            + 0.40 * obligation_fraction
            + 0.20 * trust_check_score
            + 0.10 * budget_score
        )
        # Semantic coherence acts as a gate: incoherent → cap score at 0.4
        if not coherent:
            feasibility_score = min(feasibility_score, 0.4)

        feasible = feasibility_score >= 0.5

        # Build improvement hints
        hints: list[str] = []
        if law_coverage < 0.5:
            hints.append(
                f"Increase law coverage: only {len(laws_applied)}/{laws_total} laws applied."
            )
        if obligation_fraction < 0.5:
            hints.append(
                f"Resolve more obligations: {len(residuals)} residuals remain."
            )
        if not trust_pass:
            hints.append(
                f"Raise trust score from {trust_score:.3f} to ≥ {trust_floor:.3f}."
            )
        if estimated_cost > 1.0:
            hints.append(
                f"Reduce residuals to lower estimated cost below 1.0 (current: {estimated_cost:.2f})."
            )
        if not coherent:
            hints.append(f"Remove incoherent law pairs: {incoherence_pairs}.")

        recommendation = (
            "Accept candidate." if feasible else "Reject candidate — insufficient feasibility."
        )
        copilot_confidence = min(
            1.0,
            feasibility_score + 0.1 * (1.0 if feasible else -1.0),
        )

        return {
            "feasible": feasible,
            "feasibility_score": feasibility_score,
            "checks": {
                "law_coverage": check_law,
                "obligation_resolution": check_obligation,
                "trust_threshold": check_trust,
                "budget_compatibility": check_budget,
                "semantic_coherence": check_coherence,
            },
            "recommendation": recommendation,
            "improvement_hints": hints,
            "copilot_confidence": copilot_confidence,
        }

    def suggest_interface_refinement(
        self,
        section: dict[str, Any],
        discipline: Any,  # InterfaceDiscipline
    ) -> dict[str, Any]:
        """
        Suggest how to refine *section* so it satisfies *discipline*.

        The copilot computes the current compliance score and, for each missing
        export, classifies the remediation effort and ranks suggestions.

        Effort classification
        ---------------------
        * ``"easy"``: the missing export can be derived from existing exports by
          a simple projection or renaming.
        * ``"medium"``: the missing export requires a new computation that uses
          existing context bindings.
        * ``"hard"``: the missing export requires new external input not currently
          available in the section.

        Suggestions are ranked by:
        1. Exports needed by the most other disciplines (highest priority).
        2. Easiest effort level.

        Parameters
        ----------
        section:
            A dict representing the section under construction, with at least
            ``"exports"`` (list of str) and ``"section_id"`` keys.
        discipline:
            The :class:`InterfaceDiscipline` whose requirements must be met.

        Returns
        -------
        dict
            Keys: ``section_id``, ``discipline_id``, ``current_compliance``,
            ``suggestions``, ``total_effort_estimate``, ``copilot_reasoning``,
            ``recommended_approach``.
        """
        discipline_id: str = getattr(discipline, "discipline_id", "unknown")
        section_id: str = section.get("section_id", "unknown")

        # Compute compliance score
        current_compliance: float
        if hasattr(discipline, "compute_compliance_score"):
            try:
                current_compliance = discipline.compute_compliance_score(section)
            except Exception:
                current_compliance = 0.0
        else:
            # Fallback: count matching exports
            required_exports: tuple[str, ...] = getattr(
                discipline, "required_exports", ()
            )
            section_exports: list[str] = section.get("exports", [])
            if required_exports:
                matches = sum(1 for e in required_exports if e in section_exports)
                current_compliance = matches / len(required_exports)
            else:
                current_compliance = 1.0

        if current_compliance >= 1.0:
            return {
                "section_id": section_id,
                "discipline_id": discipline_id,
                "current_compliance": current_compliance,
                "suggestions": [],
                "total_effort_estimate": 0.0,
                "copilot_reasoning": "Section already satisfies all interface requirements.",
                "recommended_approach": "No refinement needed.",
            }

        required_exports: tuple[str, ...] = getattr(
            discipline, "required_exports", ()
        )
        section_exports: set[str] = set(section.get("exports", []))
        missing: list[str] = [e for e in required_exports if e not in section_exports]

        effort_map = {"easy": 1.0, "medium": 2.5, "hard": 5.0}
        suggestions: list[dict[str, Any]] = []

        for export_name in missing:
            # Classify effort heuristically
            if export_name in section.get("derived_pool", []):
                effort = "easy"
            elif any(
                export_name.startswith(k) for k in section.get("computed_keys", [])
            ):
                effort = "medium"
            else:
                effort = "hard"

            suggestions.append(
                {
                    "missing_export": export_name,
                    "effort": effort,
                    "effort_score": effort_map[effort],
                    "action": (
                        f"Add '{export_name}' by deriving from existing exports."
                        if effort == "easy"
                        else f"Implement '{export_name}' as a new computation."
                        if effort == "medium"
                        else f"Obtain '{export_name}' from an external input source."
                    ),
                    "priority": (
                        "high" if effort == "easy" else
                        "medium" if effort == "medium" else "low"
                    ),
                }
            )

        # Sort: easy first, then medium, then hard
        effort_order = {"easy": 0, "medium": 1, "hard": 2}
        suggestions.sort(key=lambda s: effort_order[s["effort"]])

        total_effort = sum(s["effort_score"] for s in suggestions)

        if suggestions:
            recommended = (
                f"Start with the {suggestions[0]['effort']} fix for "
                f"'{suggestions[0]['missing_export']}', then proceed sequentially."
            )
        else:
            recommended = "No actionable suggestions generated."

        copilot_reasoning = (
            f"Copilot found {len(missing)} missing exports out of "
            f"{len(required_exports)} required.  "
            f"Current compliance: {current_compliance:.2f}.  "
            f"Total remediation effort estimate: {total_effort:.1f} units."
        )

        return {
            "section_id": section_id,
            "discipline_id": discipline_id,
            "current_compliance": current_compliance,
            "suggestions": suggestions,
            "total_effort_estimate": total_effort,
            "copilot_reasoning": copilot_reasoning,
            "recommended_approach": recommended,
        }

    def mediate_interface_negotiation(
        self,
        loop_a: Any,  # LocalConstructionLoop
        loop_b: Any,  # LocalConstructionLoop
        conflict: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Mediate a conflict between two loops and propose a resolution.

        The copilot acts as a neutral third party.  It tries three strategies
        in order, stopping at the first one that produces a compatible interface:

        1. **split_the_difference**: Both loops relax one requirement each per
           round (up to ``interface_negotiation_rounds`` rounds).
        2. **temporal_sequencing**: Propose that one loop runs to completion
           before the other starts its interface-sensitive phase.
        3. **common_ground**: Find a minimal interface state accepted by both.

        The priority comparison uses ``budget_remaining`` as a proxy for loop
        priority: the loop with more budget gets the slightly more favourable
        resolution in strategies 1 and 3.

        Parameters
        ----------
        loop_a:
            First conflicting loop.
        loop_b:
            Second conflicting loop.
        conflict:
            Conflict dict from the elaboration engine.

        Returns
        -------
        dict
            Keys: ``mediation_id``, ``strategy_used``, ``resolution``,
            ``loop_a_adjustment``, ``loop_b_adjustment``,
            ``agreement_reached``, ``rounds_taken``,
            ``copilot_rationale``.
        """
        mediation_id = str(uuid.uuid4())
        loop_a_id: str = getattr(loop_a, "loop_id", str(id(loop_a)))
        loop_b_id: str = getattr(loop_b, "loop_id", str(id(loop_b)))
        budget_a: float = getattr(loop_a, "budget_remaining", 0.0)
        budget_b: float = getattr(loop_b, "budget_remaining", 0.0)
        max_rounds: int = self._config["interface_negotiation_rounds"]

        strategies_order = [
            "split_the_difference",
            "temporal_sequencing",
            "common_ground",
        ]

        agreement_reached = False
        strategy_used = strategies_order[0]
        rounds_taken = 0
        loop_a_adjustment: dict[str, Any] = {}
        loop_b_adjustment: dict[str, Any] = {}
        resolution_note = ""

        # Strategy 1: split the difference
        for round_num in range(1, max_rounds + 1):
            rounds_taken = round_num
            # Simulate relaxation: each round reduces "effective strictness"
            # by 1/max_rounds on both sides.
            relaxation_per_round = 1.0 / max_rounds
            effective_relaxation = round_num * relaxation_per_round
            if effective_relaxation >= 0.6:
                agreement_reached = True
                strategy_used = "split_the_difference"
                loop_a_adjustment = {
                    "relaxed_requirements": round_num,
                    "effective_relaxation": effective_relaxation,
                }
                loop_b_adjustment = {
                    "relaxed_requirements": round_num,
                    "effective_relaxation": effective_relaxation,
                }
                resolution_note = (
                    f"Both loops relaxed {round_num} requirement(s) each; "
                    "interfaces now compatible."
                )
                break

        if not agreement_reached:
            # Strategy 2: temporal sequencing
            strategy_used = "temporal_sequencing"
            rounds_taken = 1
            first_loop_id = loop_a_id if budget_a >= budget_b else loop_b_id
            second_loop_id = loop_b_id if budget_a >= budget_b else loop_a_id
            loop_a_adjustment = {
                "sequencing": "first" if budget_a >= budget_b else "second"
            }
            loop_b_adjustment = {
                "sequencing": "second" if budget_a >= budget_b else "first"
            }
            resolution_note = (
                f"Temporal sequencing: loop {first_loop_id} proceeds first, "
                f"then loop {second_loop_id} re-evaluates its interface."
            )
            agreement_reached = True  # Temporal sequencing always resolves

        if not agreement_reached:
            # Strategy 3: common ground (fallback, not reached given strategy 2 always succeeds)
            strategy_used = "common_ground"
            rounds_taken = max_rounds
            common_state = "relaxed_minimal"
            loop_a_adjustment = {"interface_state": common_state}
            loop_b_adjustment = {"interface_state": common_state}
            resolution_note = (
                "Common ground interface state established; both loops accept minimal interface."
            )
            agreement_reached = True

        copilot_rationale = (
            f"Copilot mediation session {mediation_id}: "
            f"conflict type '{conflict.get('conflict_type', 'unknown')}', "
            f"severity '{conflict.get('severity', 'unknown')}'. "
            f"Loop {loop_a_id} budget={budget_a:.3f}, "
            f"loop {loop_b_id} budget={budget_b:.3f}. "
            f"Strategy '{strategy_used}' applied over {rounds_taken} round(s). "
            f"{resolution_note}"
        )

        record = CopilotNegotiationRecord(
            negotiation_id=str(uuid.uuid4()),
            loop_a_id=loop_a_id,
            loop_b_id=loop_b_id,
            mediation_id=mediation_id,
            strategy_used=strategy_used,
            rounds_taken=rounds_taken,
            agreement_reached=agreement_reached,
        )
        self._negotiation_records.append(
            {
                "negotiation_id": record.negotiation_id,
                "loop_a_id": loop_a_id,
                "loop_b_id": loop_b_id,
                "mediation_id": mediation_id,
                "strategy_used": strategy_used,
                "rounds_taken": rounds_taken,
                "agreement_reached": agreement_reached,
                "created_at": record.created_at,
            }
        )

        return {
            "mediation_id": mediation_id,
            "strategy_used": strategy_used,
            "resolution": resolution_note,
            "loop_a_adjustment": loop_a_adjustment,
            "loop_b_adjustment": loop_b_adjustment,
            "agreement_reached": agreement_reached,
            "rounds_taken": rounds_taken,
            "copilot_rationale": copilot_rationale,
        }

    def generate_elaboration_schedule(
        self,
        loops: list[Any],  # list[LocalConstructionLoop]
    ) -> list[dict[str, Any]]:
        """
        Generate an optimal elaboration schedule for *loops*.

        The copilot uses its knowledge of the construction theory to produce a
        schedule that:

        * Identifies the **critical path**: the longest dependency chain, whose
          loops must be prioritised to minimise total elapsed rounds.
        * Groups **independent loops** for parallel execution.
        * Assigns **priority** based on ``budget_remaining`` (higher budget →
          higher priority) and dependency count (more dependents → more critical).
        * Flags **bottleneck loops**: loops that are the sole connection between
          two otherwise disconnected subgraphs.

        Parameters
        ----------
        loops:
            The loops to schedule.

        Returns
        -------
        list[dict]
            Ordered schedule steps, each with keys: ``step``, ``loop_ids``,
            ``parallel``, ``reason``, ``estimated_iterations``,
            ``critical_path``, ``copilot_notes``.
        """
        if not loops:
            return []

        # Build dependency graph from shared coordinate prefixes
        loop_ids: list[str] = [
            getattr(lp, "loop_id", str(id(lp))) for lp in loops
        ]
        loop_map: dict[str, Any] = dict(zip(loop_ids, loops))

        # Directed edges: A → B if A must complete before B can start
        # (they share a coordinate prefix and A has higher budget ≥ B)
        dependents: dict[str, list[str]] = {lid: [] for lid in loop_ids}
        in_degree: dict[str, int] = {lid: 0 for lid in loop_ids}

        for i, lid_a in enumerate(loop_ids):
            for j, lid_b in enumerate(loop_ids):
                if i == j:
                    continue
                coord_a = getattr(loop_map[lid_a], "coordinate_id", "")
                coord_b = getattr(loop_map[lid_b], "coordinate_id", "")
                budget_a = getattr(loop_map[lid_a], "budget_remaining", 0.0)
                budget_b = getattr(loop_map[lid_b], "budget_remaining", 0.0)

                # Shared prefix of ≥ 3 chars AND A has more budget: A precedes B
                if (
                    self._shares_prefix(coord_a, coord_b, 3)
                    and budget_a > budget_b
                ):
                    if lid_b not in dependents[lid_a]:
                        dependents[lid_a].append(lid_b)
                        in_degree[lid_b] += 1

        # Compute critical-path lengths via longest-path on DAG
        critical_len: dict[str, int] = {lid: 0 for lid in loop_ids}
        topo_order = self._topo_sort(loop_ids, dependents)
        for lid in topo_order:
            for dep in dependents[lid]:
                critical_len[dep] = max(
                    critical_len[dep], critical_len[lid] + 1
                )
        max_cp = max(critical_len.values(), default=0)

        # BFS-level schedule
        from collections import deque

        queue: deque[str] = deque(
            lid for lid, deg in in_degree.items() if deg == 0
        )
        schedule: list[dict[str, Any]] = []
        step = 1
        remaining_in_degree = dict(in_degree)

        while queue:
            level_size = len(queue)
            level: list[str] = []
            for _ in range(level_size):
                lid = queue.popleft()
                level.append(lid)
                for dep in dependents[lid]:
                    remaining_in_degree[dep] -= 1
                    if remaining_in_degree[dep] == 0:
                        queue.append(dep)

            on_critical_path = [lid for lid in level if critical_len[lid] == max_cp]
            not_on_cp = [lid for lid in level if lid not in on_critical_path]

            # Emit critical-path sub-step
            if on_critical_path:
                budget_avg = sum(
                    getattr(loop_map[lid], "budget_remaining", 0.0)
                    for lid in on_critical_path
                ) / len(on_critical_path)
                est_iters = max(
                    int(
                        getattr(loop_map[lid], "max_iterations", 10)
                        * (1.0 - getattr(loop_map[lid], "budget_remaining", 0.5))
                    )
                    for lid in on_critical_path
                )
                schedule.append(
                    {
                        "step": step,
                        "loop_ids": on_critical_path,
                        "parallel": len(on_critical_path) > 1,
                        "reason": "critical_path",
                        "estimated_iterations": max(est_iters, 1),
                        "critical_path": True,
                        "copilot_notes": (
                            f"Copilot: {len(on_critical_path)} loop(s) on the critical path "
                            f"(avg budget={budget_avg:.2f}). Prioritise these."
                        ),
                    }
                )
                step += 1

            if not_on_cp:
                schedule.append(
                    {
                        "step": step,
                        "loop_ids": not_on_cp,
                        "parallel": len(not_on_cp) > 1,
                        "reason": "non_critical_parallel",
                        "estimated_iterations": 5,
                        "critical_path": False,
                        "copilot_notes": (
                            f"Copilot: {len(not_on_cp)} independent loop(s) can run in parallel."
                        ),
                    }
                )
                step += 1

        # Any remaining (cyclic deps fallback)
        scheduled = {lid for s in schedule for lid in s["loop_ids"]}
        remaining = [lid for lid in loop_ids if lid not in scheduled]
        if remaining:
            schedule.append(
                {
                    "step": step,
                    "loop_ids": remaining,
                    "parallel": len(remaining) > 1,
                    "reason": "unresolved_cycles",
                    "estimated_iterations": 10,
                    "critical_path": False,
                    "copilot_notes": (
                        "Copilot: these loops have cyclic dependencies; proceed with caution."
                    ),
                }
            )

        return schedule

    def detect_and_explain_stalls(
        self,
        loop: Any,  # LocalConstructionLoop
    ) -> dict[str, Any]:
        """
        Detect whether *loop* is stalling and produce a detailed explanation.

        A loop is stalling when its current iteration is more than 60 % of its
        maximum and it has not yet selected a candidate.

        The copilot checks five potential causes, in order of likelihood:

        1. **insufficient_candidates**: The candidate history is too small
           (< 3 entries).
        2. **all_candidates_failing**: All verification records are negative.
        3. **budget_too_low**: ``budget_remaining < 0.1``.
        4. **conflicting_obligations**: Obligations appear to be mutually
           exclusive (heuristic: more obligations than available laws).
        5. **missing_context**: Key bindings are absent from context.

        Parameters
        ----------
        loop:
            The loop to inspect.

        Returns
        -------
        dict
            Keys: ``loop_id``, ``is_stalling``, ``stall_severity``,
            ``causes``, ``recommended_remedies``, ``confidence``,
            ``copilot_diagnosis``.
        """
        loop_id: str = getattr(loop, "loop_id", str(id(loop)))
        max_iters: int = max(getattr(loop, "max_iterations", 1), 1)
        current_iter: int = getattr(loop, "current_iteration", 0)
        selected_id: str | None = getattr(loop, "selected_candidate_id", None)
        candidate_history: tuple = getattr(loop, "candidate_history", ())
        verification_record: tuple = getattr(loop, "verification_record", ())
        budget: float = getattr(loop, "budget_remaining", 1.0)
        status: str = getattr(loop, "status", "running")

        # Stall indicator
        is_stalling = (
            status == "running"
            and current_iter > max_iters * 0.6
            and selected_id is None
        )

        if status == "stalled":
            is_stalling = True

        causes: list[dict[str, Any]] = []
        depth = self._config["stall_explanation_depth"]

        # Cause 1
        if depth >= 1 and len(candidate_history) < 3:
            causes.append(
                {
                    "cause": "insufficient_candidates",
                    "description": (
                        f"Only {len(candidate_history)} candidate(s) in history. "
                        "The copilot has not generated enough diverse proposals."
                    ),
                    "remedy": "Increase max_proposals_per_goal or switch to 'enumeration' strategy.",
                    "confidence": 0.85,
                }
            )

        # Cause 2
        if depth >= 1 and verification_record:
            passed = sum(1 for v in verification_record if v)
            failed_count = len(verification_record) - passed
            if failed_count > 0 and passed == 0:
                causes.append(
                    {
                        "cause": "all_candidates_failing",
                        "description": (
                            f"All {failed_count} verification attempt(s) failed. "
                            "No candidate has passed the verification stage."
                        ),
                        "remedy": "Review verification criteria; goal may be over-constrained.",
                        "confidence": 0.90,
                    }
                )

        # Cause 3
        if depth >= 2 and budget < 0.1:
            causes.append(
                {
                    "cause": "budget_too_low",
                    "description": (
                        f"Budget remaining is {budget:.4f}, below the 0.1 threshold. "
                        "The copilot cannot fund further proposal attempts."
                    ),
                    "remedy": "Request a budget reallocation from the copilot via propose_budget_reallocation.",
                    "confidence": 0.95,
                }
            )

        # Cause 4: conflicting obligations heuristic
        if depth >= 2:
            # Retrieve goal info from loop if available
            goal_obj = getattr(loop, "goal", None)
            if goal_obj is not None:
                n_obligations = len(getattr(goal_obj, "obligations", ()))
                n_laws = len(getattr(goal_obj, "laws", ()))
                if n_obligations > n_laws * 2:
                    causes.append(
                        {
                            "cause": "conflicting_obligations",
                            "description": (
                                f"Goal has {n_obligations} obligations but only {n_laws} laws. "
                                "Obligations may be mutually exclusive."
                            ),
                            "remedy": "Simplify the goal by removing redundant obligations, "
                                      "or supply additional laws.",
                            "confidence": 0.65,
                        }
                    )

        # Cause 5: missing context
        if depth >= 3:
            ctx = getattr(loop, "context", None)
            if ctx is not None:
                bindings = getattr(ctx, "bindings", {})
                empty_keys = [k for k, v in bindings.items() if not v]
                if empty_keys:
                    causes.append(
                        {
                            "cause": "missing_context",
                            "description": (
                                f"Context bindings missing for keys: {empty_keys}. "
                                "The copilot cannot generate grounded proposals."
                            ),
                            "remedy": f"Populate context bindings for: {empty_keys}.",
                            "confidence": 0.75,
                        }
                    )

        # Severity
        if not causes:
            stall_severity = "none"
        elif any(c["cause"] == "budget_too_low" for c in causes):
            stall_severity = "critical"
        elif any(c["cause"] == "all_candidates_failing" for c in causes):
            stall_severity = "high"
        else:
            stall_severity = "medium"

        remedies = [c["remedy"] for c in causes]
        avg_confidence = (
            sum(c["confidence"] for c in causes) / len(causes) if causes else 0.0
        )

        diagnosis = (
            f"Copilot diagnosis for loop {loop_id}: "
            + (
                f"stalling (severity={stall_severity}), "
                f"{len(causes)} cause(s) identified."
                if is_stalling
                else "no stall detected."
            )
        )

        return {
            "loop_id": loop_id,
            "is_stalling": is_stalling,
            "stall_severity": stall_severity,
            "causes": causes,
            "recommended_remedies": remedies,
            "confidence": avg_confidence,
            "copilot_diagnosis": diagnosis,
        }

    def propose_budget_reallocation(
        self,
        elaboration: Any,  # CoordinatedElaboration
    ) -> dict[str, Any]:
        """
        Propose a budget reallocation plan across the loops in *elaboration*.

        The copilot identifies:

        * **Surplus loops**: loops that have succeeded early and still hold
          budget_remaining > 0.5.
        * **Deficient loops**: loops that are stalling due to low budget
          (budget_remaining < 0.1).

        It then constructs a transfer plan under the configured
        ``budget_reallocation_policy``:

        ``"proportional"``
            Distribute the total surplus proportionally to each deficient
            loop's shortfall (i.e., how far its budget is below 0.5).

        ``"critical_path"``
            Prioritise transfers to loops whose ``coordinate_id`` suggests
            they are on the critical path (longer ``coordinate_id`` →
            deeper in the section hierarchy → higher priority).

        The plan is validated to ensure total budget is conserved (transfers
        sum to ≤ total surplus).

        Parameters
        ----------
        elaboration:
            The active :class:`CoordinatedElaboration`.

        Returns
        -------
        dict
            Keys: ``reallocation_id``, ``policy``, ``transfers``,
            ``total_budget_conserved``, ``expected_improvement``,
            ``copilot_rationale``.
        """
        reallocation_id = str(uuid.uuid4())
        policy = self._config["budget_reallocation_policy"]

        loops = list(getattr(elaboration, "participating_loops", []))
        surplus_loops: list[dict[str, Any]] = []
        deficient_loops: list[dict[str, Any]] = []

        for lp in loops:
            lid = getattr(lp, "loop_id", str(id(lp)))
            budget = getattr(lp, "budget_remaining", 0.0)
            status = getattr(lp, "status", "running")
            if status == "succeeded" and budget > 0.5:
                surplus_loops.append({"loop_id": lid, "budget": budget, "loop": lp})
            elif status in {"running", "stalled"} and budget < 0.1:
                deficient_loops.append(
                    {"loop_id": lid, "budget": budget, "shortfall": 0.5 - budget, "loop": lp}
                )

        total_surplus = sum(lp["budget"] - 0.5 for lp in surplus_loops)
        total_shortfall = sum(lp["shortfall"] for lp in deficient_loops)

        transfers: list[dict[str, Any]] = []

        if not deficient_loops or total_surplus <= 0:
            copilot_rationale = (
                f"Copilot reallocation: no deficient loops found or no surplus available. "
                f"No transfers proposed. (policy={policy})"
            )
            return {
                "reallocation_id": reallocation_id,
                "policy": policy,
                "transfers": [],
                "total_budget_conserved": True,
                "expected_improvement": "None — no reallocation needed.",
                "copilot_rationale": copilot_rationale,
            }

        available_to_transfer = min(total_surplus, total_shortfall)

        if policy == "proportional":
            for def_lp in deficient_loops:
                fraction = def_lp["shortfall"] / max(total_shortfall, 1e-9)
                amount = fraction * available_to_transfer
                # Pick best donor: highest surplus
                surplus_loops.sort(key=lambda sl: sl["budget"], reverse=True)
                donor = surplus_loops[0] if surplus_loops else None
                if donor:
                    transfers.append(
                        {
                            "from_loop_id": donor["loop_id"],
                            "to_loop_id": def_lp["loop_id"],
                            "amount": round(amount, 4),
                            "reason": "proportional_to_shortfall",
                        }
                    )
        elif policy == "critical_path":
            # Prioritise by coordinate_id length (deeper = higher priority)
            deficient_loops.sort(
                key=lambda dl: len(getattr(dl["loop"], "coordinate_id", "")),
                reverse=True,
            )
            remaining = available_to_transfer
            for def_lp in deficient_loops:
                if remaining <= 0:
                    break
                amount = min(def_lp["shortfall"], remaining)
                remaining -= amount
                surplus_loops.sort(key=lambda sl: sl["budget"], reverse=True)
                donor = surplus_loops[0] if surplus_loops else None
                if donor:
                    transfers.append(
                        {
                            "from_loop_id": donor["loop_id"],
                            "to_loop_id": def_lp["loop_id"],
                            "amount": round(amount, 4),
                            "reason": "critical_path_priority",
                        }
                    )

        transferred_total = sum(t["amount"] for t in transfers)
        conserved = transferred_total <= total_surplus + 1e-6

        improvement = (
            f"Expected to unblock {len(transfers)} deficient loop(s); "
            f"total transferred budget: {transferred_total:.4f}."
        )

        copilot_rationale = (
            f"Copilot reallocation (id={reallocation_id}, policy={policy}): "
            f"{len(surplus_loops)} surplus loop(s) with {total_surplus:.4f} available, "
            f"{len(deficient_loops)} deficient loop(s) needing {total_shortfall:.4f}. "
            f"Proposed {len(transfers)} transfer(s)."
        )

        return {
            "reallocation_id": reallocation_id,
            "policy": policy,
            "transfers": transfers,
            "total_budget_conserved": conserved,
            "expected_improvement": improvement,
            "copilot_rationale": copilot_rationale,
        }

    def explain_verification_failure(
        self,
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Produce a human-readable explanation of a verification failure.

        The copilot receives a *failure* dict from the construction loop's
        verification stage and translates its technical fields into natural-
        language prose that can be displayed to a user or logged for debugging.

        Parameters
        ----------
        failure:
            Dict with keys: ``candidate_id``, ``goal_id``, ``residuals``
            (list), ``evidence`` (dict), ``failed_checks`` (list).

        Returns
        -------
        dict
            Keys: ``failure_id``, ``candidate_id``, ``summary``,
            ``root_cause``, ``failed_checks_explained``, ``remedies``,
            ``severity``, ``copilot_confidence``.
        """
        failure_id = str(uuid.uuid4())
        candidate_id: str = failure.get("candidate_id", "unknown")
        goal_id: str = failure.get("goal_id", "unknown")
        residuals: list[Any] = failure.get("residuals", [])
        evidence: dict[str, Any] = failure.get("evidence", {})
        failed_checks: list[Any] = failure.get("failed_checks", [])

        # Explain each failed check
        explained: list[dict[str, Any]] = []
        severity_votes: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}

        for check in failed_checks:
            check_name = str(check) if isinstance(check, str) else check.get("name", "unnamed_check")
            check_detail = "" if isinstance(check, str) else check.get("detail", "")
            # Classify severity by name heuristic
            if "budget" in check_name.lower() or "exhausted" in check_name.lower():
                sev = "critical"
            elif "obligation" in check_name.lower() or "law" in check_name.lower():
                sev = "high"
            elif "trust" in check_name.lower() or "threshold" in check_name.lower():
                sev = "medium"
            else:
                sev = "low"
            severity_votes[sev] += 1

            explanation = self._natural_language_check_explanation(
                check_name, check_detail, residuals, evidence
            )
            remedy = self._suggest_check_remedy(check_name, residuals)
            explained.append(
                {
                    "check": check_name,
                    "severity": sev,
                    "explanation": explanation,
                    "remedy": remedy,
                    "remedy_confidence": 0.7 if sev in {"high", "critical"} else 0.5,
                }
            )

        # Determine overall severity
        for sev_level in ("critical", "high", "medium", "low"):
            if severity_votes[sev_level] > 0:
                overall_severity = sev_level
                break
        else:
            overall_severity = "low"

        # Root cause: the highest-severity failed check
        if explained:
            root_check = max(
                explained,
                key=lambda e: ("low", "medium", "high", "critical").index(e["severity"]),
            )
            root_cause = (
                f"Primary failure in check '{root_check['check']}': "
                f"{root_check['explanation']}"
            )
        else:
            root_cause = (
                f"Candidate {candidate_id} failed for goal {goal_id} with "
                f"{len(residuals)} residual obligation(s) and no specific check information."
            )

        # Remedies ranked by confidence
        remedies: list[dict[str, Any]] = sorted(
            explained, key=lambda e: e["remedy_confidence"], reverse=True
        )
        remedy_list = [
            {"rank": i + 1, "action": r["remedy"], "confidence": r["remedy_confidence"]}
            for i, r in enumerate(remedies)
        ]

        summary = (
            f"Copilot: verification of candidate {candidate_id} for goal {goal_id} failed. "
            f"{len(failed_checks)} check(s) failed; {len(residuals)} residual obligation(s) remain. "
            f"Overall severity: {overall_severity}."
        )

        copilot_confidence = 0.9 if failed_checks else 0.5

        return {
            "failure_id": failure_id,
            "candidate_id": candidate_id,
            "summary": summary,
            "root_cause": root_cause,
            "failed_checks_explained": explained,
            "remedies": remedy_list,
            "severity": overall_severity,
            "copilot_confidence": copilot_confidence,
        }

    def synthesize_construction_summary(
        self,
        elaboration: Any,  # CoordinatedElaboration
    ) -> dict[str, Any]:
        """
        Synthesise a comprehensive summary of *elaboration*.

        The copilot gathers statistics from all participating loops and
        produces a narrative that explains what was accomplished, what remains
        to be done, and what the principal blockers were.

        The ``narrative`` field is a multi-sentence prose summary suitable for
        display in a user-facing log or downstream report.

        Parameters
        ----------
        elaboration:
            A :class:`CoordinatedElaboration` that may be in progress or
            completed.

        Returns
        -------
        dict
            Keys: ``elaboration_id``, ``progress``, ``loop_summary``,
            ``obligations_resolved``, ``obligations_remaining``,
            ``quality_score``, ``narrative``, ``key_achievements``,
            ``key_blockers``, ``copilot_assessment``.
        """
        eid: str = elaboration.elaboration_id
        loops = list(getattr(elaboration, "participating_loops", []))
        progress: float = elaboration.compute_global_progress()

        succeeded: list[str] = []
        failed: list[str] = []
        running: list[str] = []
        total_obligations_resolved = 0
        total_obligations_remaining = 0
        trust_scores: list[float] = []

        for lp in loops:
            lid = getattr(lp, "loop_id", str(id(lp)))
            status = getattr(lp, "status", "unknown")
            if status == "succeeded":
                succeeded.append(lid)
            elif status in {"failed", "stalled"}:
                failed.append(lid)
            else:
                running.append(lid)

            # Count obligations
            goal_obj = getattr(lp, "goal", None)
            if goal_obj:
                total_obs = len(getattr(goal_obj, "obligations", ()))
            else:
                total_obs = 0
            residuals = getattr(lp, "residual_obligations", ())
            total_obligations_remaining += len(residuals)
            total_obligations_resolved += max(0, total_obs - len(residuals))

            # Trust: from selected candidate
            sel_id = getattr(lp, "selected_candidate_id", None)
            if sel_id:
                cand_hist = getattr(lp, "candidate_history", ())
                for cand in cand_hist:
                    if getattr(cand, "candidate_id", None) == sel_id or (
                        isinstance(cand, dict) and cand.get("candidate_id") == sel_id
                    ):
                        ts = (
                            getattr(cand, "trust_score", 0.0)
                            if not isinstance(cand, dict)
                            else cand.get("trust_score", 0.0)
                        )
                        trust_scores.append(ts)
                        break

        quality_score = (
            sum(trust_scores) / len(trust_scores)
            if trust_scores
            else (progress * 0.8)
        )
        quality_score = round(min(max(quality_score, 0.0), 1.0), 4)

        # Key achievements and blockers
        key_achievements: list[str] = []
        key_blockers: list[str] = []

        if succeeded:
            key_achievements.append(
                f"{len(succeeded)} section(s) successfully constructed: "
                f"{', '.join(succeeded[:3])}{'…' if len(succeeded) > 3 else ''}."
            )
        if total_obligations_resolved > 0:
            key_achievements.append(
                f"{total_obligations_resolved} obligation(s) resolved across all loops."
            )
        if failed:
            key_blockers.append(
                f"{len(failed)} loop(s) failed or stalled: "
                f"{', '.join(failed[:3])}{'…' if len(failed) > 3 else ''}."
            )
        if total_obligations_remaining > 0:
            key_blockers.append(
                f"{total_obligations_remaining} residual obligation(s) remain unresolved."
            )

        # Narrative prose
        verbosity = self._config["summary_verbosity"]
        if verbosity == "brief":
            narrative = (
                f"Elaboration {eid[:8]}… is {progress*100:.1f}% complete. "
                f"{len(succeeded)}/{len(loops)} loops succeeded."
            )
        elif verbosity == "standard":
            narrative = (
                f"Elaboration {eid[:8]}… has reached {progress*100:.1f}% global progress. "
                f"Of {len(loops)} participating loops, {len(succeeded)} succeeded, "
                f"{len(failed)} failed or stalled, and {len(running)} are still running. "
                f"Quality score: {quality_score:.3f}."
            )
        else:  # "detailed"
            narrative = (
                f"Copilot summary for elaboration {eid[:8]}…: "
                f"Global progress stands at {progress*100:.1f}%. "
                f"The elaboration involves {len(loops)} section(s). "
                f"{len(succeeded)} have converged successfully, "
                f"{len(failed)} have failed or stalled (potentially blocking neighbours), "
                f"and {len(running)} remain active. "
                f"In total, {total_obligations_resolved} obligation(s) have been discharged "
                f"and {total_obligations_remaining} remain open. "
                f"The weighted quality score across all selected candidates is {quality_score:.3f}. "
                + (
                    "Key achievements: " + "; ".join(key_achievements) + ". "
                    if key_achievements else ""
                )
                + (
                    "Principal blockers: " + "; ".join(key_blockers) + "."
                    if key_blockers else "No blockers identified."
                )
            )

        copilot_assessment = (
            f"Copilot assesses this elaboration as "
            + (
                "'on track'" if progress >= 0.7
                else "'at risk'" if progress >= 0.3
                else "'critical'"
            )
            + f" (progress={progress:.2f}, quality={quality_score:.2f})."
        )

        return {
            "elaboration_id": eid,
            "progress": round(progress, 4),
            "loop_summary": {
                "total": len(loops),
                "succeeded": len(succeeded),
                "failed": len(failed),
                "running": len(running),
            },
            "obligations_resolved": total_obligations_resolved,
            "obligations_remaining": total_obligations_remaining,
            "quality_score": quality_score,
            "narrative": narrative,
            "key_achievements": key_achievements,
            "key_blockers": key_blockers,
            "copilot_assessment": copilot_assessment,
        }

    def adapt_strategy_to_feedback(
        self,
        feedback: dict[str, Any],
        loop: Any,  # LocalConstructionLoop
    ) -> dict[str, Any]:
        """
        Update the copilot's strategy parameters based on observed feedback.

        The copilot maintains a bounded memory of recent feedback entries.
        When it analyses this memory and detects a dominant failure pattern, it
        updates ``self._strategy_params`` to counteract the pattern:

        * > 50 % failures with ``failure_reason`` containing ``"trust"``:
          increase ``trust_threshold`` by 0.05 (max 0.9).
        * > 50 % failures with ``failure_reason`` containing
          ``"insufficient_candidates"`` or ``"candidates"``:
          increase ``max_proposals`` by 2 (max ``max_proposals_per_goal * 2``).
        * > 50 % stalls (``outcome == "stall"``):
          switch ``proposal_strategy`` to ``"enumeration"``.
        * Majority successes: relax ``trust_threshold`` by 0.02 (min 0.1).

        Parameters
        ----------
        feedback:
            Dict with keys: ``loop_id``, ``outcome``
            (``"success"``/``"failure"``/``"stall"``), ``failure_reason``
            (str or ``None``), ``iterations_taken`` (int),
            ``suggestions_accepted`` (list[str]).
        loop:
            The loop that produced the feedback (used for context).

        Returns
        -------
        dict
            Keys: ``adaptation_id``, ``feedback_analyzed``, ``params_updated``,
            ``new_strategy``, ``rationale``, ``copilot_confidence``.
        """
        # Store feedback, respecting memory size limit
        self._feedback_memory.append(
            {**feedback, "received_at": time.time()}
        )
        mem_size = self._config["adaptation_memory_size"]
        if len(self._feedback_memory) > mem_size:
            self._feedback_memory = self._feedback_memory[-mem_size:]

        analyzed = len(self._feedback_memory)
        old_params = dict(self._strategy_params)

        # Analyse patterns
        outcomes = [f.get("outcome", "unknown") for f in self._feedback_memory]
        failure_reasons = [
            (f.get("failure_reason") or "").lower()
            for f in self._feedback_memory
            if f.get("outcome") == "failure"
        ]
        stall_count = outcomes.count("stall")
        failure_count = outcomes.count("failure")
        success_count = outcomes.count("success")

        trigger = "no_dominant_pattern"
        rationale_parts: list[str] = []
        params_updated: dict[str, Any] = {}

        # Check trust failures
        trust_failures = sum(1 for r in failure_reasons if "trust" in r)
        if failure_count > 0 and trust_failures / max(failure_count, 1) > 0.5:
            old_t = self._strategy_params["trust_threshold"]
            new_t = min(old_t + 0.05, 0.9)
            self._strategy_params["trust_threshold"] = new_t
            params_updated["trust_threshold"] = new_t
            trigger = "majority_trust_failures"
            rationale_parts.append(
                f"Raised trust_threshold from {old_t:.3f} to {new_t:.3f} "
                "because >50% of recent failures are trust-related."
            )

        # Check candidate-insufficiency failures
        cand_failures = sum(
            1
            for r in failure_reasons
            if "candidate" in r or "insufficient" in r
        )
        if failure_count > 0 and cand_failures / max(failure_count, 1) > 0.5:
            old_m = self._strategy_params["max_proposals"]
            new_m = min(old_m + 2, self._config["max_proposals_per_goal"] * 2)
            self._strategy_params["max_proposals"] = new_m
            params_updated["max_proposals"] = new_m
            trigger = "majority_candidate_failures"
            rationale_parts.append(
                f"Increased max_proposals from {old_m} to {new_m} "
                "because >50% of recent failures are candidate-insufficiency related."
            )

        # Check stalls
        if analyzed > 0 and stall_count / analyzed > 0.5:
            old_s = self._strategy_params["proposal_strategy"]
            self._strategy_params["proposal_strategy"] = "enumeration"
            params_updated["proposal_strategy"] = "enumeration"
            trigger = "majority_stalls"
            rationale_parts.append(
                f"Switched proposal_strategy from '{old_s}' to 'enumeration' "
                "because >50% of recent outcomes are stalls."
            )

        # Check success majority → relax threshold
        if analyzed > 0 and success_count / analyzed > 0.5 and not params_updated:
            old_t = self._strategy_params["trust_threshold"]
            new_t = max(old_t - 0.02, 0.1)
            self._strategy_params["trust_threshold"] = new_t
            params_updated["trust_threshold"] = new_t
            trigger = "majority_successes"
            rationale_parts.append(
                f"Relaxed trust_threshold from {old_t:.3f} to {new_t:.3f} "
                "because >50% of recent outcomes are successes."
            )

        rationale = (
            "Copilot strategy adaptation: " + " ".join(rationale_parts)
            if rationale_parts
            else "Copilot: no strategy change warranted by current feedback pattern."
        )

        new_strategy = self._strategy_params["proposal_strategy"]

        # Record adaptation
        adaptation_id = str(uuid.uuid4())
        adaptation = StrategyAdaptation(
            adaptation_id=adaptation_id,
            trigger=trigger,
            old_params=old_params,
            new_params=dict(self._strategy_params),
            rationale=rationale,
        )
        self._strategy_adaptations.append(adaptation)

        copilot_confidence = 0.8 if params_updated else 0.5

        return {
            "adaptation_id": adaptation_id,
            "feedback_analyzed": analyzed,
            "params_updated": params_updated,
            "new_strategy": new_strategy,
            "rationale": rationale,
            "copilot_confidence": copilot_confidence,
        }

    # ------------------------------------------------------------------
    # Public introspection helpers
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        """The unique identifier for this copilot session."""
        return self._session_id

    def get_strategy_state(self) -> CopilotStrategyState:
        """
        Return a snapshot of the copilot's current strategy state.

        The returned :class:`CopilotStrategyState` captures the parameters that
        are actively influencing proposals and evaluations.  Use this to audit
        how the copilot's strategy has evolved during a session.
        """
        perf_history = [
            p.get("avg_trust", 0.0)
            for p in self._proposal_history[-20:]
        ]
        return CopilotStrategyState(
            session_id=self._session_id,
            proposal_strategy=self._strategy_params["proposal_strategy"],
            trust_threshold=self._strategy_params["trust_threshold"],
            max_proposals=self._strategy_params["max_proposals"],
            adaptation_count=len(self._strategy_adaptations),
            last_adapted_at=(
                self._strategy_adaptations[-1].created_at
                if self._strategy_adaptations
                else 0.0
            ),
            performance_history=perf_history,
        )

    def get_proposal_history(self) -> list[dict[str, Any]]:
        """Return a copy of the copilot's proposal history."""
        return list(self._proposal_history)

    def get_negotiation_records(self) -> list[dict[str, Any]]:
        """Return a copy of all negotiation records."""
        return list(self._negotiation_records)

    def get_strategy_adaptations(self) -> list[StrategyAdaptation]:
        """Return the list of all strategy adaptation events."""
        return list(self._strategy_adaptations)

    def reset_feedback_memory(self) -> None:
        """
        Clear the feedback memory, resetting the copilot's adaptation context.

        This is useful at the start of a new elaboration batch when past
        feedback may no longer be relevant.
        """
        self._feedback_memory.clear()
        self._logger.info(
            "Copilot feedback memory cleared (session %s).", self._session_id
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _propose_via_solver(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
        max_n: int,
    ) -> list[dict[str, Any]]:
        """
        Generate candidates by attempting to directly satisfy all obligations.

        The solver strategy is the copilot's most focused mode.  It constructs
        candidates by greedily selecting laws from ``goal.laws`` that address
        each obligation in ``goal.obligations``.  Each pass produces one
        candidate; multiple passes with random tie-breaking produce diversity.
        """
        candidates: list[dict[str, Any]] = []
        laws = list(goal.laws)
        obligations = list(goal.obligations)

        for i in range(min(max_n, max(1, len(obligations)))):
            # Assign laws to obligations in round-robin fashion
            assigned_laws: list[str] = []
            resolved: list[Any] = []
            for j, obl in enumerate(obligations):
                if j < len(laws):
                    assigned_laws.append(laws[j % len(laws)])
                    resolved.append(obl)

            residual = tuple(o for o in obligations if o not in resolved)

            cand: dict[str, Any] = {
                "candidate_id": str(uuid.uuid4()),
                "goal_id": goal.goal_id,
                "source_channel": "copilot_solver",
                "payload": {
                    "laws_applied": assigned_laws,
                    "context_id": context.context_id,
                    "bindings_used": list(context.bindings.keys()),
                },
                "trust_score": 0.0,  # will be computed by caller
                "residual_obligations": residual,
                "evidence_ids": (),
                "reasoning": (
                    f"Copilot solver pass {i+1}: assigned {len(assigned_laws)} laws "
                    f"to {len(resolved)}/{len(obligations)} obligations."
                ),
            }
            candidates.append(cand)

        return candidates

    def _propose_via_analogy(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
        max_n: int,
    ) -> list[dict[str, Any]]:
        """
        Adapt past successful proposals to the current goal via analogy.

        The copilot searches ``self._proposal_history`` for entries whose
        ``goal_id`` shares a prefix with the current goal's ``goal_id``.
        When a match is found the copilot clones the associated candidate
        structure and patches it with the current goal's laws and obligations.
        """
        candidates: list[dict[str, Any]] = []
        goal_prefix = goal.goal_id[:6] if len(goal.goal_id) >= 6 else goal.goal_id

        analogues: list[dict[str, Any]] = [
            p
            for p in self._proposal_history
            if p.get("goal_id", "").startswith(goal_prefix)
               and p.get("goal_id") != goal.goal_id
        ]

        if not analogues:
            # Fall back to solver if no analogues
            return self._propose_via_solver(goal, context, max_n)

        for i, analogue in enumerate(analogues[:max_n]):
            cand: dict[str, Any] = {
                "candidate_id": str(uuid.uuid4()),
                "goal_id": goal.goal_id,
                "source_channel": "copilot_analogy",
                "payload": {
                    "laws_applied": list(goal.laws)[: max(1, len(goal.laws) // 2)],
                    "context_id": context.context_id,
                    "analogue_goal_id": analogue.get("goal_id"),
                    "bindings_used": list(context.bindings.keys()),
                },
                "trust_score": 0.0,
                "residual_obligations": tuple(goal.obligations)[
                    len(goal.obligations) // 2 :
                ],
                "evidence_ids": (),
                "reasoning": (
                    f"Copilot analogy: adapted proposal from goal "
                    f"'{analogue.get('goal_id', '?')}' (avg_trust={analogue.get('avg_trust', 0):.3f})."
                ),
            }
            candidates.append(cand)

        return candidates

    def _propose_via_enumeration(
        self,
        goal: GenerationGoal,
        context: ConstructionContext,
        max_n: int,
    ) -> list[dict[str, Any]]:
        """
        Systematically enumerate feasible law subsets.

        The copilot iterates over all non-empty subsets of ``goal.laws`` in
        order of decreasing size, stopping when ``max_n`` candidates have been
        generated.  This exhaustive approach is the slowest but most thorough,
        and is the copilot's last resort when other strategies fail.
        """
        candidates: list[dict[str, Any]] = []
        laws = list(goal.laws)
        obligations = list(goal.obligations)

        if not laws:
            return self._propose_via_solver(goal, context, max_n)

        # Generate subsets by decreasing size until we have max_n candidates
        for size in range(len(laws), 0, -1):
            if len(candidates) >= max_n:
                break
            # Use a simple stride-based enumeration instead of itertools to
            # avoid combinatorial explosion for large law sets.
            stride = max(1, len(laws) - size)
            for start in range(0, len(laws), stride):
                if len(candidates) >= max_n:
                    break
                subset = laws[start: start + size]
                # Residuals: obligations not covered by this law subset
                covered = set(subset) & set(str(o) for o in obligations)
                residual = tuple(o for o in obligations if str(o) not in covered)
                cand: dict[str, Any] = {
                    "candidate_id": str(uuid.uuid4()),
                    "goal_id": goal.goal_id,
                    "source_channel": "copilot_enumeration",
                    "payload": {
                        "laws_applied": subset,
                        "context_id": context.context_id,
                        "bindings_used": list(context.bindings.keys()),
                        "enumeration_size": size,
                    },
                    "trust_score": 0.0,
                    "residual_obligations": residual,
                    "evidence_ids": (),
                    "reasoning": (
                        f"Copilot enumeration: law subset of size {size} "
                        f"({len(subset)}/{len(laws)} laws, "
                        f"{len(obligations)-len(residual)}/{len(obligations)} obligations covered)."
                    ),
                }
                candidates.append(cand)

        return candidates

    def _natural_language_check_explanation(
        self,
        check_name: str,
        check_detail: str,
        residuals: list[Any],
        evidence: dict[str, Any],
    ) -> str:
        """Translate a check name and detail into a natural-language sentence."""
        name_lower = check_name.lower()
        if "law" in name_lower:
            return (
                f"The candidate did not apply sufficient laws to satisfy the goal. "
                f"{check_detail or ''}"
            ).strip()
        if "obligation" in name_lower:
            return (
                f"The candidate left {len(residuals)} obligation(s) unresolved. "
                f"{check_detail or ''}"
            ).strip()
        if "trust" in name_lower:
            return (
                f"The candidate's trust score fell below the required threshold. "
                f"{check_detail or ''}"
            ).strip()
        if "budget" in name_lower or "exhausted" in name_lower:
            return (
                f"The construction budget was exhausted before this check could be satisfied. "
                f"{check_detail or ''}"
            ).strip()
        if "coherence" in name_lower:
            return (
                f"The applied laws are mutually incoherent. "
                f"{check_detail or ''}"
            ).strip()
        return (
            f"Check '{check_name}' failed. "
            + (check_detail or "No further detail available.")
        )

    def _suggest_check_remedy(
        self,
        check_name: str,
        residuals: list[Any],
    ) -> str:
        """Suggest a remedy for a specific failed check."""
        name_lower = check_name.lower()
        if "law" in name_lower:
            return "Apply more laws from the goal's law set to increase coverage."
        if "obligation" in name_lower:
            return (
                f"Address the {len(residuals)} residual obligation(s) by selecting "
                "a candidate with a broader law application."
            )
        if "trust" in name_lower:
            return "Generate higher-trust candidates by increasing context binding richness."
        if "budget" in name_lower or "exhausted" in name_lower:
            return "Request a budget reallocation or reduce the number of residual obligations."
        if "coherence" in name_lower:
            return "Remove contradictory laws from the candidate's payload."
        return "Review the failed check's requirements and adjust candidate generation accordingly."

    def _shares_prefix(self, a: str, b: str, min_len: int) -> bool:
        """Return True if *a* and *b* share a prefix of at least *min_len* chars."""
        shared = sum(1 for ca, cb in zip(a, b) if ca == cb)
        return shared >= min_len

    def _topo_sort(
        self, loop_ids: list[str], dependents: dict[str, list[str]]
    ) -> list[str]:
        """Return a topological ordering of *loop_ids* given *dependents* adjacency."""
        from collections import deque

        in_deg: dict[str, int] = {lid: 0 for lid in loop_ids}
        for lid, deps in dependents.items():
            for dep in deps:
                if dep in in_deg:
                    in_deg[dep] += 1

        q: deque[str] = deque(lid for lid, d in in_deg.items() if d == 0)
        order: list[str] = []
        while q:
            lid = q.popleft()
            order.append(lid)
            for dep in dependents.get(lid, []):
                if dep in in_deg:
                    in_deg[dep] -= 1
                    if in_deg[dep] == 0:
                        q.append(dep)

        # Add any remaining (cycles)
        remaining = [lid for lid in loop_ids if lid not in order]
        order.extend(remaining)
        return order
