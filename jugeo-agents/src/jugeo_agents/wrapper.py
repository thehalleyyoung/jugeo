"""JuGeoAgentWrapper — top-level verification API for multi-agent LLM pipelines.

This is the main entry point for jugeo-agents.  It orchestrates all
subsystems (descent, covers, trust, provenance, convergence, treaties,
routing, calibration, challenges) behind a simple three-method API:

    jugeo = JuGeoAgentWrapper()
    coverage  = jugeo.verify_task_decomposition(task, subtasks)
    result    = jugeo.on_agent_output(agent_id, output, metadata)
    report    = jugeo.on_pipeline_complete()

Internally the wrapper maintains mutable pipeline state and delegates to
the appropriate subsystems at each stage.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from jugeo_agents.types import (
    AgentOutput,
    Challenge,
    ChallengeOutcome,
    CohomologyClass,
    Contradiction,
    ConvergencePhase,
    ConvergenceSnapshot,
    ConvergenceStatus,
    CoverageReport,
    DescentResult,
    EvidenceChannel,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    PipelineReport,
    ProvenanceChain,
    TreatyResolution,
    TrustLevel,
    conservative_join,
)
from jugeo_agents.core.trust import TrustAlgebra
from jugeo_agents.core.claims import make_extractor, make_detector, HeuristicContradictionDetector
from jugeo_agents.core.descent import DescentEngine, LocalSection
from jugeo_agents.core.covers import CoverageChecker
from jugeo_agents.core.obstructions import ObstructionClassifier, ObstructionReporter
from jugeo_agents.core.provenance import ProvenanceGraph
from jugeo_agents.orchestration.convergence import ConvergenceMonitor
from jugeo_agents.orchestration.calibration import CalibrationEngine
from jugeo_agents.orchestration.challenge import (
    ChallengeAdjudicator,
    ChallengeInitiator,
    ChallengeLedger,
)
from jugeo_agents.orchestration.treaty import TreatyNegotiator
from jugeo_agents.orchestration.treaty_memory import TreatyMemory
from jugeo_agents.orchestration.routing import TrustAwareRouter, BudgetTracker
from jugeo_agents.orchestration.control import (
    PhaseAdaptiveControlLaw,
    PipelineState,
    AgentAction,
)
from jugeo_agents.core.bundle import JudgmentBundle


# ---------------------------------------------------------------------------
# Verification result (returned after each agent output)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class VerificationResult:
    """Result of verifying a single agent's output."""

    status: str  # "consistent", "conflict_detected", "conflict_resolved"
    agent_id: str = ""
    trust_level: TrustLevel = TrustLevel.UNGROUNDED_CLAIM
    claims_extracted: int = 0
    obstructions: list[Obstruction] = field(default_factory=list)
    treaties: list[TreatyResolution] = field(default_factory=list)
    challenges: list[Challenge] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.obstructions) > 0


# ---------------------------------------------------------------------------
# Main wrapper
# ---------------------------------------------------------------------------

class JuGeoAgentWrapper:
    """Top-level orchestration API for multi-agent LLM verification.

    Wraps the full JuGeo verification stack behind a simple interface.
    Use as a standalone verifier or wrap around CrewAI / LangGraph / AutoGen
    pipelines via the adapter layer.

    Parameters
    ----------
    auto_negotiate : bool
        Automatically attempt treaty negotiation on detected contradictions.
    auto_challenge : bool
        Automatically generate challenges for low-trust contradictions.
    convergence_patience : int
        Number of stalled rounds before declaring the system stuck.
    token_budget : float
        Total routing budget for evidence channels.
    """

    def __init__(
        self,
        *,
        auto_negotiate: bool = True,
        auto_challenge: bool = True,
        convergence_patience: int = 3,
        token_budget: float = 100.0,
    ) -> None:
        # Core subsystems
        self._trust = TrustAlgebra()
        self._extractor = make_extractor()
        self._detector = HeuristicContradictionDetector()
        self._descent = DescentEngine(
            claim_extractor=self._extractor,
            contradiction_detector=self._detector,
            trust_algebra=self._trust,
        )
        self._coverage = CoverageChecker()
        self._obstruction_clf = ObstructionClassifier()
        self._obstruction_rpt = ObstructionReporter()
        self._provenance = ProvenanceGraph()

        # Orchestration subsystems
        self._convergence = ConvergenceMonitor(stall_patience=convergence_patience)
        self._calibration = CalibrationEngine()
        self._challenge_init = ChallengeInitiator()
        self._challenge_adj = ChallengeAdjudicator()
        self._challenge_ledger = ChallengeLedger()
        self._negotiator = TreatyNegotiator()
        self._treaty_memory = TreatyMemory()
        self._router = TrustAwareRouter()
        self._budget = BudgetTracker(total_budget=token_budget)
        self._control = PhaseAdaptiveControlLaw()

        # Judgment Fiber Bundle (geometric stack)
        self._bundle = JudgmentBundle()

        # Config
        self._auto_negotiate = auto_negotiate
        self._auto_challenge = auto_challenge

        # Pipeline state
        self._agent_outputs: dict[str, list[AgentOutput]] = {}
        self._all_treaties: list[TreatyResolution] = []
        self._all_challenges: list[Challenge] = []
        self._round_number = 0
        self._task_description = ""
        self._subtask_list: list[dict[str, str]] = []
        self._coverage_report: CoverageReport | None = None
        self._pipeline_id = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------
    # Public API: pre-execution
    # ------------------------------------------------------------------

    def verify_task_decomposition(
        self,
        task: str,
        subtasks: list[dict[str, str]] | list[str],
    ) -> CoverageReport:
        """Check whether *subtasks* collectively cover *task*.

        Call this **before** running the agent pipeline to catch
        coverage gaps (missing subtasks) before spending tokens.

        Parameters
        ----------
        task : str
            Natural-language description of the full task.
        subtasks : list
            Each element is either a ``str`` (subtask description) or a
            ``dict`` with at least a ``"name"`` or ``"scope"`` key.

        Returns
        -------
        CoverageReport
            Includes gaps, redundancies, coverage score, and suggestions.
        """
        self._task_description = task
        normalised: list[dict[str, str]] = []
        for s in subtasks:
            if isinstance(s, str):
                normalised.append({"name": s, "scope": s})
            else:
                normalised.append(s)
        self._subtask_list = normalised
        self._coverage_report = self._coverage.check(task, normalised)
        return self._coverage_report

    # ------------------------------------------------------------------
    # Public API: per-agent output
    # ------------------------------------------------------------------

    def on_agent_output(
        self,
        agent_id: str,
        output: str,
        metadata: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Process and verify a single agent's output.

        Call this after each agent in the pipeline produces output.
        The method extracts claims, assigns trust, checks descent
        (consistency with all prior agents), and optionally triggers
        treaty negotiation and challenges.

        Parameters
        ----------
        agent_id : str
            Unique identifier for the agent.
        output : str
            The agent's raw text output.
        metadata : dict, optional
            Extra info: ``model``, ``tools_used``, ``rag_sources``,
            ``citations``, ``subtask``, etc.

        Returns
        -------
        VerificationResult
        """
        meta = metadata or {}
        self._round_number += 1

        # 1. Build AgentOutput record
        agent_out = AgentOutput(
            agent_id=agent_id,
            output_text=output,
            model=meta.get("model", ""),
            role=meta.get("role", ""),
            subtask=meta.get("subtask", ""),
            tools_used=meta.get("tools_used", []),
            tool_results=meta.get("tool_results", {}),
            rag_sources=meta.get("rag_sources", []),
            citations=meta.get("citations", []),
            round_number=self._round_number,
        )

        # 2. Extract claims
        claims = self._extractor.extract(output, agent_id=agent_id)
        agent_out.claims = claims

        # 3. Classify trust
        trust = self._trust.classify_output(agent_out)
        agent_out.trust = trust
        for c in claims:
            c.trust = self._trust.classify_claim(c, agent_out)

        # 4. Record in provenance graph
        derived = meta.get("derived_from", [])
        self._provenance.add_agent_output(agent_out, derived_from=derived)

        # 5. Add section to descent engine and check consistency
        section = self._descent.add_section(agent_out)
        descent_result = self._descent.check_incremental(section)

        # 6. Store output
        self._agent_outputs.setdefault(agent_id, []).append(agent_out)

        # 6b. Bundle integration — feed agent output into the judgment fiber bundle
        self._bundle.add_agent_output(agent_out)

        # 7. Classify obstructions
        obstructions = descent_result.obstructions

        # 8. Auto-negotiate treaties for detected contradictions
        treaties: list[TreatyResolution] = []
        if self._auto_negotiate and obstructions:
            for obs in obstructions:
                for contradiction in obs.contradictions:
                    resolution = self._negotiator.negotiate(contradiction)
                    treaties.append(resolution)
                    self._all_treaties.append(resolution)
                    self._treaty_memory.record(
                        contradiction, resolution, resolution.success,
                    )

        # 9. Auto-challenge low-trust claims
        challenges: list[Challenge] = []
        if self._auto_challenge and obstructions:
            for obs in obstructions:
                for contradiction in obs.contradictions:
                    challenge = self._challenge_init.from_contradiction(
                        contradiction, challenger_agent=agent_id,
                    )
                    adjudicated = self._challenge_adj.adjudicate(
                        challenge,
                        challenger_trust=trust,
                        challenged_trust=contradiction.claim_b.trust,
                    )
                    challenges.append(adjudicated)
                    self._challenge_ledger.record(adjudicated)
                    self._all_challenges.append(adjudicated)

        # 10. Update convergence monitor
        coverage_score = self._coverage_report.coverage_score if self._coverage_report else 0.0
        n_obstructions = len(self._descent.global_status().obstructions)
        total_claims = sum(
            len(o.claims) for outs in self._agent_outputs.values() for o in outs
        )
        ungrounded = sum(
            1
            for outs in self._agent_outputs.values()
            for o in outs
            for c in o.claims
            if c.trust <= TrustLevel.WEAK_MODEL_GENERATED
        )
        trust_debt = ungrounded / max(total_claims, 1)
        obstruction_density = n_obstructions / max(len(self._agent_outputs), 1)

        self._convergence.record_round(
            coverage=coverage_score,
            obstruction_density=obstruction_density,
            trust_debt=trust_debt,
        )

        # 11. Build suggestions
        suggestions: list[str] = []
        if obstructions:
            suggestions.append(
                f"Found {len(obstructions)} obstruction(s) involving {agent_id}."
            )
        if trust == TrustLevel.UNGROUNDED_CLAIM:
            suggestions.append(
                f"Agent '{agent_id}' output is ungrounded — consider tool verification."
            )
        conv_status = self._convergence.status()
        if conv_status == ConvergenceStatus.STUCK:
            suggestions.append("Pipeline appears stuck — consider changing strategy.")
        elif conv_status == ConvergenceStatus.DIVERGING:
            suggestions.append("Pipeline is diverging — stop and investigate.")

        # 12. Determine overall status
        if not obstructions:
            status = "consistent"
        elif treaties and all(t.success for t in treaties):
            status = "conflict_resolved"
        else:
            status = "conflict_detected"

        return VerificationResult(
            status=status,
            agent_id=agent_id,
            trust_level=trust,
            claims_extracted=len(claims),
            obstructions=obstructions,
            treaties=treaties,
            challenges=challenges,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------
    # Public API: post-pipeline
    # ------------------------------------------------------------------

    def on_pipeline_complete(self) -> PipelineReport:
        """Produce the full verification report after all agents finish.

        Returns
        -------
        PipelineReport
            Comprehensive report with descent status, coverage, trust
            summary, convergence history, provenance, treaties, and
            challenges.
        """
        # Final global descent check (includes H2 / phantom detection)
        descent = self._descent.check_all()
        cascading = self._descent.detect_cascading_hallucinations()
        phantom = self._descent.detect_phantom_sections()
        all_obstructions = descent.obstructions + cascading + phantom
        descent = DescentResult(
            is_consistent=descent.is_consistent and not cascading and not phantom,
            obstructions=all_obstructions,
            checked_pairs=descent.checked_pairs,
            total_claims_checked=descent.total_claims_checked,
            consistency_score=descent.consistency_score,
        )

        # Coverage
        coverage = self._coverage_report or CoverageReport(
            is_complete=False, coverage_score=0.0,
        )

        # Trust distribution
        trust_dist: dict[str, int] = {}
        total_claims = 0
        for outs in self._agent_outputs.values():
            for o in outs:
                for c in o.claims:
                    name = c.trust.name
                    trust_dist[name] = trust_dist.get(name, 0) + 1
                    total_claims += 1

        # Provenance
        provenance_chains = self._provenance.trace_all_claims()

        # Convergence
        conv_history = self._convergence.history
        final_phase = self._convergence.current_phase()
        final_v = conv_history[-1].lyapunov_v if conv_history else 1.0

        # Bundle diagnostics
        self._bundle.build_connection()
        bundle_diag = self._bundle.diagnose()

        return PipelineReport(
            descent_result=descent,
            coverage=coverage,
            trust_summary=trust_dist,
            convergence_history=conv_history,
            provenance_chains=provenance_chains,
            treaties=self._all_treaties,
            challenges=self._all_challenges,
            total_agents=len(self._agent_outputs),
            total_claims=total_claims,
            total_rounds=self._round_number,
            final_phase=final_phase,
            final_lyapunov=final_v,
            bundle_diagnostics=bundle_diag,
        )

    # ------------------------------------------------------------------
    # Public API: query helpers
    # ------------------------------------------------------------------

    def suggest_next_action(self) -> AgentAction:
        """Suggest the next action based on current pipeline state."""
        coverage = self._coverage_report or CoverageReport(
            is_complete=False, coverage_score=0.0,
        )
        descent = self._descent.global_status()
        ungrounded = [
            c
            for outs in self._agent_outputs.values()
            for o in outs
            for c in o.claims
            if c.trust <= TrustLevel.WEAK_MODEL_GENERATED
        ]
        state = PipelineState(
            coverage=coverage,
            descent=descent,
            phase=self._convergence.current_phase(),
            round_number=self._round_number,
            agent_outputs={
                k: [o.output_text for o in v] for k, v in self._agent_outputs.items()
            },
            unresolved_obstructions=descent.obstructions,
            ungrounded_claims=ungrounded,
            budget_remaining=self._budget.remaining(),
        )
        return self._control.select_action(state)

    def convergence_status(self) -> ConvergenceStatus:
        """Current convergence status."""
        return self._convergence.status()

    def trust_summary(self) -> dict[str, int]:
        """Per-trust-level claim counts."""
        dist: dict[str, int] = {}
        for outs in self._agent_outputs.values():
            for o in outs:
                for c in o.claims:
                    name = c.trust.name
                    dist[name] = dist.get(name, 0) + 1
        return dist

    def provenance_for(self, claim_text: str) -> ProvenanceChain | None:
        """Trace provenance for a claim matching *claim_text*."""
        for outs in self._agent_outputs.values():
            for o in outs:
                for c in o.claims:
                    if claim_text.lower() in c.text.lower():
                        return self._provenance.trace_claim(c)
        return None

    def conflict_pairs(self) -> list[tuple[str, str, int]]:
        """Agent pairs ranked by number of conflicts."""
        return self._challenge_ledger.conflict_pairs()

    def calibration_summary(self) -> str:
        """Human-readable calibration summary."""
        return self._calibration.summary()

    def bundle_diagnostics(self) -> dict:
        """Return the Judgment Fiber Bundle diagnostic for the current pipeline state.

        This includes curvature (trust inconsistency), holonomy (loop trust defects),
        first Chern class (global trust invariant), and trust stratification.
        """
        self._bundle.build_connection()
        return self._bundle.diagnose()

    def route_claim(
        self, claim: FactualClaim, required_trust: TrustLevel,
    ):
        """Route a claim to the best evidence channel."""
        return self._router.route(claim, required_trust)

    def reset(self) -> None:
        """Reset all pipeline state for a new run."""
        self._descent.reset()
        self._provenance = ProvenanceGraph()
        patience = 3
        sd = getattr(self._convergence, "_stall_detector", None)
        if sd is not None:
            patience = getattr(sd, "_patience", 3)
        self._convergence = ConvergenceMonitor(stall_patience=patience)
        self._agent_outputs.clear()
        self._all_treaties.clear()
        self._all_challenges.clear()
        self._round_number = 0
        self._coverage_report = None
        self._pipeline_id = uuid.uuid4().hex[:12]
        self._bundle.reset()
