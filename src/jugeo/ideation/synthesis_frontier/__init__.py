"""Synthesis frontier — cross-domain integration tournament.

Sub-modules
-----------

models          Core data structures (FieldNode, PropositionRecord, …).
fields          The 48 foundational mathematical fields.
llm_judge       LLM-as-judge that scores cross-domain integrations.
metaphor_finder Discovers and indexes cross-domain metaphors.
tournament      Binary halving / proposition-doubling tournament engine.
paper_generator Generates a LaTeX mathematics paper from the synthesis node.
code_orchestrator Maps the paper to JuGeo implementation targets.
pipeline        End-to-end pipeline: 48 fields → paper + code plan.

Public API
----------
All public symbols are importable directly from this package::

    from jugeo.ideation.synthesis_frontier import (
        FieldNode, PaperGenerator, SynthesisFrontierPipeline, run_pipeline,
    )
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.models import (
        DomainArea,
        FieldNode,
        MetaphorLink,
        PropositionKind,
        PropositionRecord,
        SynthesisPair,
        TournamentState,
    )
except ImportError:
    try:
        from .models import (  # type: ignore[no-redef]
            DomainArea,
            FieldNode,
            MetaphorLink,
            PropositionKind,
            PropositionRecord,
            SynthesisPair,
            TournamentState,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# fields
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.fields import (
        ALL_48_FIELDS,
        FIELD_BY_ID,
        FIELD_BY_NAME,
        get_fields_by_keywords,
        get_fields_for_obstruction,
    )
except ImportError:
    try:
        from .fields import (  # type: ignore[no-redef]
            ALL_48_FIELDS,
            FIELD_BY_ID,
            FIELD_BY_NAME,
            get_fields_by_keywords,
            get_fields_for_obstruction,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# llm_judge
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.llm_judge import (
        JudgeMode,
        JudgeConfig,
        JudgeVerdict,
        HeuristicJudge,
        SynthesisJudge,
        LLMJudge,
    )
except ImportError:
    try:
        from .llm_judge import (  # type: ignore[no-redef]
            JudgeMode,
            JudgeConfig,
            JudgeVerdict,
            HeuristicJudge,
            SynthesisJudge,
            LLMJudge,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# tournament
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.tournament import (
        BinaryTournamentFrontier,
        MergeResult,
        PairingStrategy,
        Tournament,
    )
except ImportError:
    try:
        from .tournament import (  # type: ignore[no-redef]
            BinaryTournamentFrontier,
            MergeResult,
            PairingStrategy,
            Tournament,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# metaphor_finder
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.metaphor_finder import (
        MetaphorFinder,
        MetaphorKind,
        PatternMatcher,
    )
except ImportError:
    try:
        from .metaphor_finder import (  # type: ignore[no-redef]
            MetaphorFinder,
            MetaphorKind,
            PatternMatcher,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# paper_generator
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.paper_generator import (
        MathPaper,
        PaperGenerator,
        PaperSection,
    )
except ImportError:
    try:
        from .paper_generator import (  # type: ignore[no-redef]
            MathPaper,
            PaperGenerator,
            PaperSection,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# code_orchestrator
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.code_orchestrator import (
        CodeTarget,
        OrchestrationPlan,
        SynthesisCodeOrchestrator,
    )
except ImportError:
    try:
        from .code_orchestrator import (  # type: ignore[no-redef]
            CodeTarget,
            OrchestrationPlan,
            SynthesisCodeOrchestrator,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.pipeline import (
        SynthesisFrontierPipeline,
        run_pipeline,
    )
except ImportError:
    try:
        from .pipeline import (  # type: ignore[no-redef]
            SynthesisFrontierPipeline,
            run_pipeline,
        )
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------
__all__ = [
    # models
    "DomainArea",
    "FieldNode",
    "MetaphorLink",
    "PropositionKind",
    "PropositionRecord",
    "SynthesisPair",
    "TournamentState",
    # fields
    "ALL_48_FIELDS",
    "FIELD_BY_ID",
    "FIELD_BY_NAME",
    "get_fields_by_keywords",
    "get_fields_for_obstruction",
    # llm_judge
    "JudgeMode",
    "JudgeConfig",
    "JudgeVerdict",
    "HeuristicJudge",
    "SynthesisJudge",
    "LLMJudge",
    # tournament
    "BinaryTournamentFrontier",
    "MergeResult",
    "PairingStrategy",
    "Tournament",
    # metaphor_finder
    "MetaphorFinder",
    "MetaphorKind",
    "PatternMatcher",
    # paper_generator
    "MathPaper",
    "PaperGenerator",
    "PaperSection",
    # code_orchestrator
    "CodeTarget",
    "OrchestrationPlan",
    "SynthesisCodeOrchestrator",
    # pipeline
    "SynthesisFrontierPipeline",
    "run_pipeline",
]

# --- auto-registered submodules ---
try:
    from . import judgment_geometry_bridge
except Exception:
    pass
try:
    from . import taxonomy
except Exception:
    pass
try:
    from . import textbook_generator
except Exception:
    pass
