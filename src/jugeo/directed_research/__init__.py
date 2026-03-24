"""Directed research as sheaf descent over a workspace site.

This package implements the full geometry-of-ideation and geometry-of-orchestration
pipeline for automated research. Given a natural-language prompt, it:

    1. **Ideates** a novel approach via cross-domain synthesis (§9 of concept-ideation)
       — domains as sites, solution presheaves, H^1 novelty, stalk usefulness
    2. **Generates** a large implementation via covering-family decomposition
       — each module is a local section on the Code surface
    3. **Benchmarks** against SOTA baselines via evidence sections
       — every metric claim is a section at an Evidence coordinate
    4. **Verifies** all claims via JuGeo's ``jg prove``, ``jg bugs``, ``jg spec``
       — no hallucinated claims survive descent
    5. **Refines or pivots** driven by cohomological obstructions
       — H^1 tells you what's inconsistent and why
    6. **Writes** a 12-page academic paper and comprehensive README
       — every claim in both is jg-verified against actual evidence

The workspace site W has four coordinates (T, R, E, P) — Theory, Code, Evidence,
Claims — connected by six morphisms. Consistency is the sheaf condition: local
sections on each surface must agree on all overlaps. The research loop IS descent.

Architecture of this package::

    directed_research/
        __init__.py           — this file; re-exports the public API
        _types.py             — all enums, dataclasses, type definitions
        _trust_algebra.py     — trust level management via jugeo.evidence.trust
        _llm_channel.py       — LLM as typed evidence channel (copilot/SDK/fallback)
        _workspace.py         — extended WorkspaceSite with deep geometry integration
        _domain_site.py       — domain decomposition into sub-domain sites
        _solution_presheaf.py — solution presheaf, demand sheaf, usefulness pairing
        _morphism_discovery.py— analogy morphism finding between domains
        _novelty.py           — H^1 computation, excess novelty fraction, bridge ideas
        _cech_complex.py      — Cech complex for cross-domain overlap computation
        _graded_usefulness.py — relevance filtration, graded UNS scoring
        _problem_functor.py   — problem localization, search space reshaping
        _ideation.py          — geometry of ideation: domain sites, H^1, stalks
        _tournament.py        — binary tournament over domain pairings
        _moves.py             — all semantic moves (theory, code, evidence, claims)
        _code_gen.py          — code generation with jg bugs/prove checks
        _benchmarking.py      — benchmark establishment, SOTA comparison, metrics
        _verification.py      — jg-based verification of all claims/citations/README
        _paper_gen.py         — academic paper generation with jg-verified claims
        _readme_gen.py        — README generation with jg-verified instructions
        _pivot.py             — theory pivoting with adjacency constraints
        _consistency.py       — full consistency checking with jg integration
        _provenance.py        — audit trail, provenance tracking, reproducibility
        _orchestration.py     — phase orchestration (seed/generate/harden/tail)
        _descent_loop.py      — the main descent-driven research loop

Usage::

    from jugeo.directed_research import DirectedResearch

    dr = DirectedResearch("Build a killer app in finance using advanced math")
    result = dr.run()

Or via CLI::

    jg research "Build a killer app in finance using advanced math" --verbose

Every LLM output is a local section at a coordinate with trust COPILOT_SUGGESTED.
Every benchmark run upgrades trust to RUNTIME_WITNESSED. Every ``jg prove`` check
upgrades to SOLVER_DISCHARGED. Descent glues sections when overlaps agree.
Obstructions tell you exactly which surface drifted and why.

The ideation engine (§9 of concept-ideation.html) operates on the *domain site*:
domains like "finance" decompose into sub-domains (derivatives pricing, risk
management, etc.) with methodological translation morphisms between them. The
*solution presheaf* assigns known techniques to each sub-domain. The *demand sheaf*
encodes open problems. A "useful novel idea" lives in H^1 (not in the image of
either restriction map) and has nonzero germ at the problem point (useful). The
*problem functor* localizes the search space to exactly the fiber over your problem.
"""

from __future__ import annotations

# ── Re-export the public API ─────────────────────────────────────────
# These are the names that external code imports:
#   from jugeo.directed_research import DirectedResearch
#   from jugeo.directed_research import DirectedResearchResult, ResearchStatus

from jugeo.directed_research._types import (
    # Core result types
    DirectedResearchResult,
    ResearchStatus,
    # Section types
    LLMSection,
    # Move types
    MoveKind,
    MoveResult,
    # Trust constants
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    TRUST_VERIFIED,
    # Ideation types
    DomainSite,
    SubDomain,
    MethodologicalTranslation,
    SolutionSection,
    DemandSection,
    BridgeProposition,
    UsefulNoveltyScore,
    IdeationResult,
    # Domain types
    DomainFiltrationLevel,
    ExcessNoveltyFraction,
    ProductivePairingCriterion,
    RelevanceFiltrationLevel,
)

from jugeo.directed_research._descent_loop import DirectedResearch

__all__ = [
    # The main class
    "DirectedResearch",
    # Result types
    "DirectedResearchResult",
    "ResearchStatus",
    # Section types
    "LLMSection",
    # Move types
    "MoveKind",
    "MoveResult",
    # Trust constants
    "TRUST_COPILOT",
    "TRUST_RUNTIME",
    "TRUST_SOLVER",
    "TRUST_VERIFIED",
    # Ideation types
    "DomainSite",
    "SubDomain",
    "MethodologicalTranslation",
    "SolutionSection",
    "DemandSection",
    "BridgeProposition",
    "UsefulNoveltyScore",
    "IdeationResult",
    # Domain types
    "DomainFiltrationLevel",
    "ExcessNoveltyFraction",
    "ProductivePairingCriterion",
    "RelevanceFiltrationLevel",
]
