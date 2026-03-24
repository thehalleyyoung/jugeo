"""jugeo-agents — Sheaf-theoretic verification for multi-agent LLM systems.

The type system for agent architectures: automatic contradiction detection,
trust algebra, provenance tracing, convergence guarantees, and treaty
negotiation for any multi-agent LLM pipeline.

Quick start::

    from jugeo_agents import JuGeoAgentWrapper

    jugeo = JuGeoAgentWrapper()

    # Check task decomposition completeness
    coverage = jugeo.verify_task_decomposition(
        task="Write a research report",
        subtasks=["find sources", "analyze", "write draft"],
    )

    # Verify agent outputs for consistency
    result = jugeo.on_agent_output(
        agent_id="researcher",
        output="The company was founded in 2019.",
        metadata={"model": "claude-sonnet-4", "tools_used": []},
    )

    # Get full pipeline report
    report = jugeo.on_pipeline_complete()
"""

__version__ = "0.1.0"

from jugeo_agents.types import (
    AgentOutput,
    ChallengeOutcome,
    ChallengeType,
    CohomologyClass,
    ConvergencePhase,
    ConvergenceStatus,
    EvidenceChannel,
    ObstructionKind,
    TrustLevel,
)
from jugeo_agents.wrapper import JuGeoAgentWrapper
from jugeo_agents.core.fusion import (
    GlobalSectionAssembler,
    VerifiedGlobalSection,
    VerifiedClaim,
    QuarantinedClaim,
    QuarantineReason,
    FusionReport,
    CohomologyComputation,
    compare_to_naive_vote,
)
from jugeo_agents.core.bundle import (
    JudgmentBundle,
    Judgment,
    JudgmentFiber,
    TrustConnection,
    Curvature,
    Holonomy,
    CharacteristicClass,
    StratifiedJudgmentSpace,
    SemanticMove,
    VerificationPath,
)
from jugeo_agents.adapters.coding_agents import (
    ClaudeCodeAdapter,
    CopilotCLIAdapter,
    CodexAdapter,
    CodingAgentOrchestrator,
)

__all__ = [
    "JuGeoAgentWrapper",
    "GlobalSectionAssembler",
    "VerifiedGlobalSection",
    "VerifiedClaim",
    "QuarantinedClaim",
    "QuarantineReason",
    "FusionReport",
    "CohomologyComputation",
    "compare_to_naive_vote",
    "ClaudeCodeAdapter",
    "CopilotCLIAdapter",
    "CodexAdapter",
    "CodingAgentOrchestrator",
    "TrustLevel",
    "AgentOutput",
    "CohomologyClass",
    "ObstructionKind",
    "ConvergencePhase",
    "ConvergenceStatus",
    "ChallengeType",
    "ChallengeOutcome",
    "EvidenceChannel",
]
