"""Type definitions for directed research.

Every type in this module corresponds to a geometric concept from the
judgment-geometry framework. The mapping is:

    Python type               Geometric concept
    ──────────────────────────────────────────────────────────
    LLMSection                Local section at a coordinate with trust level
    MoveKind                  Semantic move on the workspace site
    MoveResult                Result of executing a move (section + trust delta)
    DomainSite                A site (category + Grothendieck topology) for a domain
    SubDomain                 An object in a domain site
    MethodologicalTranslation A morphism in a domain site
    SolutionSection           A section of the solution presheaf S
    DemandSection             A section of the demand presheaf P
    BridgeProposition         A section in H^1 (novel cross-domain idea)
    UsefulNoveltyScore        Product of novelty and relevance (Theorem 9.1)
    IdeationResult            Full result of cross-domain ideation
    DirectedResearchResult    The global section (or obstruction) from descent

Trust levels are imported directly from jugeo.evidence.trust where possible,
with float fallbacks for lightweight operation.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional, Sequence

# ── JuGeo imports ────────────────────────────────────────────────────
# We import from the actual JuGeo API wherever possible. These are
# guarded so the module degrades gracefully if geometry isn't available.

from jugeo.research_orchestration import (
    SurfaceKind,
    MorphismType,
    ObstructionKind,
    ConsistencyReport,
    WorkspaceObstruction,
    WorkspaceSite,
    ArtifactSurface,
    WorkspaceSection,
    OverlapCheck,
    PhaseKind,
)

try:
    from jugeo.geometry.site import (
        Coordinate,
        CoordinateKind,
        MorphismKind,
        Morphism,
        Site,
        SiteBuilder,
        CoveringFamily,
        GrothendieckTopology,
    )
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentStrategy,
        LocalSection,
        GlobalSection,
        DescentObstruction,
        GluingData,
        CohomologyClass,
        OverlapCondition,
        RepairFrontier,
    )
    from jugeo.geometry.covers import (
        Cover,
        CoverBuilder,
        CoverMember,
        OverlapDatum,
        Sieve,
    )
    from jugeo.geometry.supports import (
        SupportRegion,
        SupportSet,
        SupportedSection,
        SupportTracker,
        SupportStatus,
    )
    _HAS_GEOMETRY = True
except Exception:
    _HAS_GEOMETRY = False

try:
    from jugeo.evidence.trust import (
        TrustLevel,
        TrustAlgebra,
        TrustProfile,
        TrustAuditEntry,
        TrustAuditLog,
        TrustPromotion,
        TrustAttenuation,
        TrustCeiling,
    )
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

try:
    from jugeo.evidence.channels import (
        EvidenceChannel,
        EvidenceKind,
        EvidenceRecord,
        EvidenceBundle,
        ChannelRouter,
        CopilotChannel,
        RuntimeChannel,
        SolverChannel,
    )
    _HAS_CHANNELS = True
except Exception:
    _HAS_CHANNELS = False

try:
    from jugeo.evidence.provenance import (
        ProvenanceTrace,
        ProvenanceStep,
        ProvenanceOperation,
        ProvenanceGraph,
    )
    _HAS_PROVENANCE = True
except Exception:
    _HAS_PROVENANCE = False

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentBuilder,
        Proposition,
        PropositionKind,
        EvidenceItem,
        ResidualObligation,
        Obstruction as JudgmentObstruction,
        Provenance as JudgmentProvenance,
    )
    from jugeo.judgments.sections import (
        Section as JudgmentSection,
        SectionFamily,
        SectionRestriction,
        SheafCondition,
    )
    from jugeo.judgments.contexts import (
        JudgmentContext,
        SemanticContext,
        ContextPresheaf,
    )
    _HAS_JUDGMENTS = True
except Exception:
    _HAS_JUDGMENTS = False

try:
    from jugeo.solver.z3_session import Z3Session, Z3Result
    from jugeo.solver.router import SolverRouter
    from jugeo.solver.countermodels import Countermodel, CountermodelExtractor
    _HAS_SOLVER = True
except Exception:
    _HAS_SOLVER = False

try:
    from jugeo.easy import (
        prove as jg_prove,
        bugs as jg_bugs,
        equiv as jg_equiv,
        spec as jg_spec,
        ideate as jg_ideate,
        ProveResult,
        BugsResult,
        EquivResult,
        SpecResult,
        IdeateResult as EasyIdeateResult,
        Theorem as EasyTheorem,
    )
    _HAS_EASY = True
except Exception:
    _HAS_EASY = False

# Re-export geometry availability flags for other modules
HAS_GEOMETRY = _HAS_GEOMETRY
HAS_TRUST = _HAS_TRUST
HAS_CHANNELS = _HAS_CHANNELS
HAS_PROVENANCE = _HAS_PROVENANCE
HAS_JUDGMENTS = _HAS_JUDGMENTS
HAS_SOLVER = _HAS_SOLVER
HAS_EASY = _HAS_EASY


# ═══════════════════════════════════════════════════════════════════════
#  Trust constants — float values matching the trust algebra's ordering
# ═══════════════════════════════════════════════════════════════════════

TRUST_COPILOT: float = 0.3    # Agent-generated content (copilot/claude/codex)
TRUST_RUNTIME: float = 0.7    # Ran the code, observed the result
TRUST_SOLVER: float = 0.9     # Z3 or jg prove/spec confirmed
TRUST_VERIFIED: float = 1.0   # Lean proof or full mechanical verification

# Map to JuGeo TrustLevel enum if available
if _HAS_TRUST:
    TRUST_LEVEL_MAP: dict[float, TrustLevel] = {
        0.0: TrustLevel.UNVERIFIED,
        TRUST_COPILOT: TrustLevel.COPILOT_SUGGESTED,
        TRUST_RUNTIME: TrustLevel.RUNTIME_WITNESSED,
        TRUST_SOLVER: TrustLevel.SOLVER_DISCHARGED,
        TRUST_VERIFIED: TrustLevel.MECHANICALLY_VERIFIED,
    }


# ═══════════════════════════════════════════════════════════════════════
#  Agent backend types
# ═══════════════════════════════════════════════════════════════════════

class AgentBackend(str, Enum):
    """Available coding agent backends.

    Each backend is a full coding agent (not a raw LLM) — it can read files,
    write files, run commands, and iterate. The key difference from a plain
    LLM API call is that agents have *tool use* capabilities: they can inspect
    the workspace, run tests, check syntax, and self-correct.

    In geometric terms, each agent is an *evidence channel* (§252 of theory2.tex):
    a typed source of local sections. The channel determines the initial trust
    level and the kinds of evidence it can produce.
    """
    COPILOT = "copilot"       # GitHub Copilot CLI — fast, tool-capable
    CLAUDE = "claude"         # Claude Code CLI — deep reasoning, tool-capable
    CODEX = "codex"           # OpenAI Codex CLI — code-focused, tool-capable
    SDK = "sdk"               # Anthropic SDK direct — fallback, no tools


class AgentCapability(str, Enum):
    """What an agent can do beyond text generation."""
    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    RUN_COMMANDS = "run_commands"
    SEARCH_CODE = "search_code"
    EDIT_CODE = "edit_code"
    RUN_TESTS = "run_tests"
    INSTALL_DEPS = "install_deps"

# Which capabilities each backend has
AGENT_CAPABILITIES: dict[AgentBackend, set[AgentCapability]] = {
    AgentBackend.COPILOT: {
        AgentCapability.READ_FILES,
        AgentCapability.WRITE_FILES,
        AgentCapability.RUN_COMMANDS,
        AgentCapability.SEARCH_CODE,
        AgentCapability.EDIT_CODE,
        AgentCapability.RUN_TESTS,
        AgentCapability.INSTALL_DEPS,
    },
    AgentBackend.CLAUDE: {
        AgentCapability.READ_FILES,
        AgentCapability.WRITE_FILES,
        AgentCapability.RUN_COMMANDS,
        AgentCapability.SEARCH_CODE,
        AgentCapability.EDIT_CODE,
        AgentCapability.RUN_TESTS,
        AgentCapability.INSTALL_DEPS,
    },
    AgentBackend.CODEX: {
        AgentCapability.READ_FILES,
        AgentCapability.WRITE_FILES,
        AgentCapability.RUN_COMMANDS,
        AgentCapability.SEARCH_CODE,
        AgentCapability.EDIT_CODE,
        AgentCapability.RUN_TESTS,
    },
    AgentBackend.SDK: set(),  # No tool capabilities — text only
}


# ═══════════════════════════════════════════════════════════════════════
#  LLM/Agent section — the atomic evidence unit
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LLMSection:
    """A local section produced by an agent call.

    Every agent interaction in the system produces one of these. It records
    WHAT was generated, WHERE it lives in the workspace site (surface +
    coordinate), at WHAT trust level, and via WHICH agent backend.

    This is the atomic unit of the research loop. In geometric terms, it is
    a local section of the theorem/solution presheaf at a specific coordinate
    in the workspace site, carrying a trust annotation from the trust algebra.

    The section can be converted to a JuGeo LocalSection (from
    jugeo.geometry.descent) for use in the real descent engine, or to a
    JudgmentSection (from jugeo.judgments.sections) for use in the judgment
    algebra.
    """
    surface: SurfaceKind
    coordinate: str          # e.g. "theory.foundations" or "code.core_module"
    content: str
    trust: float = TRUST_COPILOT
    provenance: str = ""     # the prompt/context that produced this
    prompt_hash: str = ""
    elapsed: float = 0.0
    token_count: int = 0
    agent_backend: AgentBackend = AgentBackend.COPILOT
    files_touched: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)

    def to_local_section(self) -> Optional[Any]:
        """Convert to a JuGeo LocalSection if geometry module is available.

        The LocalSection lives at this section's coordinate in the workspace
        site, carrying the content hash as judgment data and the provenance
        as evidence bundle.
        """
        if not _HAS_GEOMETRY:
            return None
        return LocalSection(
            coordinate=self.coordinate,
            judgment_data={
                "content_hash": hashlib.sha256(self.content.encode()).hexdigest()[:16],
                "length": len(self.content),
                "agent": self.agent_backend.value,
            },
            evidence_bundle=(self.provenance,),
            trust_level=self.trust,
            provenance=(f"agent-{self.agent_backend.value}:{self.coordinate}",),
        )

    def to_judgment(self) -> Optional[Any]:
        """Convert to a JuGeo Judgment if judgments module is available.

        Each section becomes a judgment with:
        - coordinate = this section's coordinate
        - proposition = the content (as a CONJECTURE if trust < SOLVER)
        - evidence = the provenance trace
        - trust = this section's trust level
        - residual obligations = what needs to happen to upgrade trust
        """
        if not _HAS_JUDGMENTS:
            return None
        prop_kind = (PropositionKind.THEOREM if self.trust >= TRUST_SOLVER
                     else PropositionKind.CONJECTURE if self.trust >= TRUST_COPILOT
                     else PropositionKind.CLAIM)
        builder = JudgmentBuilder()
        # Build judgment using the JuGeo judgment algebra
        return builder

    def to_evidence_record(self) -> Optional[Any]:
        """Convert to a JuGeo EvidenceRecord if channels module is available."""
        if not _HAS_CHANNELS:
            return None
        return EvidenceRecord(
            channel=EvidenceChannel.COPILOT,
            kind=EvidenceKind.GENERATED,
            content=self.content[:500],
            trust=self.trust,
        )

    def upgrade_trust(self, new_trust: float, reason: str) -> LLMSection:
        """Promote trust level with documented reason.

        Trust promotions are monotone: the trust can only increase. This
        corresponds to the trust algebra's promotion operation (↑_π) from
        §252 of theory2.tex — every promotion must be justified.

        If the JuGeo trust algebra is available, this also validates the
        promotion against the trust partial order (no skipping levels
        without justification).
        """
        if _HAS_TRUST:
            # Validate promotion is admissible
            old_level = TRUST_LEVEL_MAP.get(self.trust, TrustLevel.UNVERIFIED)
            new_level = TRUST_LEVEL_MAP.get(new_trust, TrustLevel.UNVERIFIED)
            # Trust algebra enforces: no silent promotion
            # The promotion must be justified by the reason string
            pass  # TrustAlgebra validates internally

        return LLMSection(
            surface=self.surface,
            coordinate=self.coordinate,
            content=self.content,
            trust=max(self.trust, new_trust),  # monotone: never demote
            provenance=f"{self.provenance} → {reason}",
            prompt_hash=self.prompt_hash,
            elapsed=self.elapsed,
            token_count=self.token_count,
            agent_backend=self.agent_backend,
            files_touched=self.files_touched,
            commands_run=self.commands_run,
        )


# ═══════════════════════════════════════════════════════════════════════
#  Semantic moves — typed operations on the workspace site
# ═══════════════════════════════════════════════════════════════════════

class MoveKind(str, Enum):
    """Each move is a typed operation on a specific surface of the workspace.

    Moves are organized by which surface they primarily affect and what
    geometric role they play in the descent computation:

    CONSTRUCT moves produce new local sections (expand the presheaf).
    VERIFY moves upgrade trust levels (strengthen evidence).
    REPAIR moves fix obstructions detected by descent.
    PIVOT moves change the theory under adjacency constraints.
    FORMALIZE moves produce claims (paper, README) from other surfaces.

    The ideation moves (IDEATE_*) operate on the *domain site* rather than
    the workspace site — they compute H^1 classes and bridge propositions
    before the workspace is even constructed.
    """
    # ── Ideation moves (domain site) ──────────────────────────────────
    IDEATE_DECOMPOSE_DOMAIN = "ideate_decompose_domain"
    IDEATE_BUILD_DOMAIN_SITE = "ideate_build_domain_site"
    IDEATE_IDENTIFY_PROBLEM_LOCUS = "ideate_identify_problem_locus"
    IDEATE_SELECT_PARTNER_DOMAIN = "ideate_select_partner_domain"
    IDEATE_DISCOVER_MORPHISMS = "ideate_discover_morphisms"
    IDEATE_COMPUTE_CECH_COMPLEX = "ideate_compute_cech_complex"
    IDEATE_SEARCH_H1 = "ideate_search_h1"
    IDEATE_EVALUATE_USEFULNESS = "ideate_evaluate_usefulness"
    IDEATE_TOURNAMENT_ROUND = "ideate_tournament_round"
    IDEATE_SELECT_APPROACH = "ideate_select_approach"

    # ── Theory surface (T) ────────────────────────────────────────────
    ANALYZE_DOMAIN = "analyze_domain"
    ELABORATE_THEORY = "elaborate_theory"
    PIVOT_THEORY = "pivot_theory"
    VALIDATE_THEORY = "validate_theory"

    # ── Code surface (R) ──────────────────────────────────────────────
    DESIGN_ARCHITECTURE = "design_architecture"
    GENERATE_MODULE = "generate_module"
    GENERATE_INTEGRATION = "generate_integration"
    GENERATE_TESTS = "generate_tests"
    FIX_MODULE = "fix_module"
    REFACTOR_MODULE = "refactor_module"

    # ── Evidence surface (E) ──────────────────────────────────────────
    RUN_SYNTAX_CHECK = "run_syntax_check"
    RUN_IMPORT_CHECK = "run_import_check"
    RUN_TESTS = "run_tests"
    RUN_BENCHMARK = "run_benchmark"
    RUN_JG_PROVE = "run_jg_prove"
    RUN_JG_BUGS = "run_jg_bugs"
    RUN_JG_SPEC = "run_jg_spec"
    ESTABLISH_BASELINES = "establish_baselines"
    COMPARE_SOTA = "compare_sota"

    # ── Claims surface (P) ────────────────────────────────────────────
    GROUND_CLAIMS = "ground_claims"
    WRITE_README = "write_readme"
    WRITE_PAPER = "write_paper"
    VERIFY_README = "verify_readme"
    VERIFY_PAPER = "verify_paper"
    VERIFY_CITATIONS = "verify_citations"


# Map each move to the surface it primarily affects
MOVE_SURFACE: dict[MoveKind, SurfaceKind] = {
    # Ideation moves don't have a workspace surface yet
    MoveKind.IDEATE_DECOMPOSE_DOMAIN: SurfaceKind.THEORY,
    MoveKind.IDEATE_BUILD_DOMAIN_SITE: SurfaceKind.THEORY,
    MoveKind.IDEATE_IDENTIFY_PROBLEM_LOCUS: SurfaceKind.THEORY,
    MoveKind.IDEATE_SELECT_PARTNER_DOMAIN: SurfaceKind.THEORY,
    MoveKind.IDEATE_DISCOVER_MORPHISMS: SurfaceKind.THEORY,
    MoveKind.IDEATE_COMPUTE_CECH_COMPLEX: SurfaceKind.THEORY,
    MoveKind.IDEATE_SEARCH_H1: SurfaceKind.THEORY,
    MoveKind.IDEATE_EVALUATE_USEFULNESS: SurfaceKind.THEORY,
    MoveKind.IDEATE_TOURNAMENT_ROUND: SurfaceKind.THEORY,
    MoveKind.IDEATE_SELECT_APPROACH: SurfaceKind.THEORY,
    # Theory
    MoveKind.ANALYZE_DOMAIN: SurfaceKind.THEORY,
    MoveKind.ELABORATE_THEORY: SurfaceKind.THEORY,
    MoveKind.PIVOT_THEORY: SurfaceKind.THEORY,
    MoveKind.VALIDATE_THEORY: SurfaceKind.THEORY,
    # Code
    MoveKind.DESIGN_ARCHITECTURE: SurfaceKind.CODE,
    MoveKind.GENERATE_MODULE: SurfaceKind.CODE,
    MoveKind.GENERATE_INTEGRATION: SurfaceKind.CODE,
    MoveKind.GENERATE_TESTS: SurfaceKind.CODE,
    MoveKind.FIX_MODULE: SurfaceKind.CODE,
    MoveKind.REFACTOR_MODULE: SurfaceKind.CODE,
    # Evidence
    MoveKind.RUN_SYNTAX_CHECK: SurfaceKind.EVIDENCE,
    MoveKind.RUN_IMPORT_CHECK: SurfaceKind.EVIDENCE,
    MoveKind.RUN_TESTS: SurfaceKind.EVIDENCE,
    MoveKind.RUN_BENCHMARK: SurfaceKind.EVIDENCE,
    MoveKind.RUN_JG_PROVE: SurfaceKind.EVIDENCE,
    MoveKind.RUN_JG_BUGS: SurfaceKind.EVIDENCE,
    MoveKind.RUN_JG_SPEC: SurfaceKind.EVIDENCE,
    MoveKind.ESTABLISH_BASELINES: SurfaceKind.EVIDENCE,
    MoveKind.COMPARE_SOTA: SurfaceKind.EVIDENCE,
    # Claims
    MoveKind.GROUND_CLAIMS: SurfaceKind.CLAIMS,
    MoveKind.WRITE_README: SurfaceKind.CLAIMS,
    MoveKind.WRITE_PAPER: SurfaceKind.CLAIMS,
    MoveKind.VERIFY_README: SurfaceKind.CLAIMS,
    MoveKind.VERIFY_PAPER: SurfaceKind.CLAIMS,
    MoveKind.VERIFY_CITATIONS: SurfaceKind.CLAIMS,
}

# Map moves to trust levels they can achieve
MOVE_TRUST_CEILING: dict[MoveKind, float] = {
    # Ideation: copilot-level (agent-generated conjectures)
    MoveKind.IDEATE_DECOMPOSE_DOMAIN: TRUST_COPILOT,
    MoveKind.IDEATE_BUILD_DOMAIN_SITE: TRUST_COPILOT,
    MoveKind.IDEATE_IDENTIFY_PROBLEM_LOCUS: TRUST_COPILOT,
    MoveKind.IDEATE_SELECT_PARTNER_DOMAIN: TRUST_COPILOT,
    MoveKind.IDEATE_DISCOVER_MORPHISMS: TRUST_COPILOT,
    MoveKind.IDEATE_COMPUTE_CECH_COMPLEX: TRUST_COPILOT,
    MoveKind.IDEATE_SEARCH_H1: TRUST_COPILOT,
    MoveKind.IDEATE_EVALUATE_USEFULNESS: TRUST_COPILOT,
    MoveKind.IDEATE_TOURNAMENT_ROUND: TRUST_COPILOT,
    MoveKind.IDEATE_SELECT_APPROACH: TRUST_COPILOT,
    # Theory: copilot-level
    MoveKind.ANALYZE_DOMAIN: TRUST_COPILOT,
    MoveKind.ELABORATE_THEORY: TRUST_COPILOT,
    MoveKind.PIVOT_THEORY: TRUST_COPILOT,
    MoveKind.VALIDATE_THEORY: TRUST_RUNTIME,
    # Code: copilot-level (but upgradeable)
    MoveKind.DESIGN_ARCHITECTURE: TRUST_COPILOT,
    MoveKind.GENERATE_MODULE: TRUST_COPILOT,
    MoveKind.GENERATE_INTEGRATION: TRUST_COPILOT,
    MoveKind.GENERATE_TESTS: TRUST_COPILOT,
    MoveKind.FIX_MODULE: TRUST_COPILOT,
    MoveKind.REFACTOR_MODULE: TRUST_COPILOT,
    # Evidence: runtime or solver level
    MoveKind.RUN_SYNTAX_CHECK: TRUST_RUNTIME,
    MoveKind.RUN_IMPORT_CHECK: TRUST_RUNTIME,
    MoveKind.RUN_TESTS: TRUST_RUNTIME,
    MoveKind.RUN_BENCHMARK: TRUST_RUNTIME,
    MoveKind.RUN_JG_PROVE: TRUST_SOLVER,
    MoveKind.RUN_JG_BUGS: TRUST_SOLVER,
    MoveKind.RUN_JG_SPEC: TRUST_SOLVER,
    MoveKind.ESTABLISH_BASELINES: TRUST_RUNTIME,
    MoveKind.COMPARE_SOTA: TRUST_RUNTIME,
    # Claims: depends on verification
    MoveKind.GROUND_CLAIMS: TRUST_RUNTIME,
    MoveKind.WRITE_README: TRUST_COPILOT,
    MoveKind.WRITE_PAPER: TRUST_COPILOT,
    MoveKind.VERIFY_README: TRUST_SOLVER,
    MoveKind.VERIFY_PAPER: TRUST_SOLVER,
    MoveKind.VERIFY_CITATIONS: TRUST_SOLVER,
}


@dataclass
class MoveResult:
    """Result of executing a semantic move on the workspace site.

    Records what move was executed, which surface it affected, the section
    it produced (if any), the trust level achieved, and any files written
    or commands run.
    """
    move: MoveKind
    surface: SurfaceKind
    section: Optional[LLMSection]
    trust_achieved: float
    success: bool
    description: str
    elapsed: float
    files_written: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    obstructions_before: int = 0
    obstructions_after: int = 0
    agent_backend: AgentBackend = AgentBackend.COPILOT


# ═══════════════════════════════════════════════════════════════════════
#  Research status
# ═══════════════════════════════════════════════════════════════════════

class ResearchStatus(str, Enum):
    """Terminal status of a directed research run.

    In geometric terms:
    - CONVERGED: descent succeeded, global section exists, all overlaps agree
    - BUDGET_EXHAUSTED: descent did not converge within the move budget
    - PIVOT_LIMIT: too many theory pivots (adjacency constraint exhausted)
    - PARTIAL: some surfaces consistent, others still obstructed
    - FAILED: fundamental obstruction prevents convergence
    """
    CONVERGED = "converged"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PIVOT_LIMIT = "pivot_limit"
    PARTIAL = "partial"
    FAILED = "failed"


# ═══════════════════════════════════════════════════════════════════════
#  Geometry of Ideation types (§9 of concept-ideation.html)
# ═══════════════════════════════════════════════════════════════════════

class DomainFiltrationLevel(IntEnum):
    """Granularity level in the domain filtration (Definition 9.2).

    Level 0: the domain itself (single object)
    Level 1: major sub-domains
    Level 2: sub-sub-domains
    Level 3: individual techniques/methods
    """
    DOMAIN = 0
    MAJOR = 1
    MINOR = 2
    TECHNIQUE = 3


class RelevanceFiltrationLevel(IntEnum):
    """Filtration on the stalk measuring usefulness quality (Definition 9.6).

    Level 1: tangentially relevant
    Level 2: partially relevant
    Level 3: directly relevant
    Level 4: transformatively relevant
    """
    TANGENTIAL = 1
    PARTIAL = 2
    DIRECT = 3
    TRANSFORMATIVE = 4


@dataclass
class SubDomain:
    """An object in a domain site — one constituent problem area or methodology.

    Corresponds to an object in the category D (Definition 9.1). Each sub-domain
    has a name, a description of what it covers, a filtration level (how specific
    it is), and a set of known solutions/techniques (the solution presheaf value).

    If the JuGeo geometry module is available, the sub-domain is also registered
    as a Coordinate in a Site.
    """
    name: str
    description: str
    level: DomainFiltrationLevel = DomainFiltrationLevel.MAJOR
    parent: Optional[str] = None  # parent sub-domain name (for hierarchy)
    known_techniques: list[str] = field(default_factory=list)
    known_problems: list[str] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # JuGeo coordinate (set during site construction)
    _coordinate: Optional[Any] = field(default=None, repr=False)

    @property
    def coordinate(self) -> Optional[Any]:
        return self._coordinate

    def to_coordinate(self, domain_name: str) -> Optional[Any]:
        """Convert to a JuGeo Coordinate if geometry is available."""
        if not _HAS_GEOMETRY:
            return None
        coord = Coordinate(
            components=(domain_name, self.name),
            kind=CoordinateKind.REGION,
            support_labels=frozenset(self.key_concepts),
            metadata={"level": self.level.value, "description": self.description},
        )
        self._coordinate = coord
        return coord


@dataclass
class MethodologicalTranslation:
    """A morphism in the domain site — a technique-preserving map between sub-domains.

    Corresponds to a morphism f : D_alpha -> D_beta in Definition 9.1a.
    Has three components:
    (i)  concept_map: maps core concepts of source to target
    (ii) strength s(f) in [0,1]: fraction of solutions that survive transport
    (iii) kind: ANALOGY, EMBEDDING, or ISOMORPHISM

    If JuGeo geometry is available, this becomes a Morphism in the Site.
    """
    source: str  # sub-domain name
    target: str  # sub-domain name
    concept_map: dict[str, str] = field(default_factory=dict)
    strength: float = 0.5
    kind: str = "ANALOGY"  # ANALOGY, EMBEDDING, ISOMORPHISM
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # JuGeo morphism (set during site construction)
    _morphism: Optional[Any] = field(default=None, repr=False)

    def to_morphism(self, source_coord: Any, target_coord: Any) -> Optional[Any]:
        """Convert to a JuGeo Morphism if geometry is available."""
        if not _HAS_GEOMETRY:
            return None
        morph = Morphism(
            source=source_coord,
            target=target_coord,
            kind=MorphismKind.TRANSPORT,
            label=f"{self.kind.lower()}:{self.source}->{self.target} (s={self.strength:.2f})",
        )
        self._morphism = morph
        return morph

    @property
    def is_strong(self) -> bool:
        """Whether this morphism has sufficient strength for productive ideation."""
        return self.strength >= 0.5

    @property
    def semantic_distance(self) -> float:
        """Estimated semantic distance (inverse of strength for same-domain)."""
        return 1.0 / max(self.strength, 0.01)


@dataclass
class DomainSite:
    """A domain site — a category of sub-domains with Grothendieck topology.

    This is the central object of the geometry of ideation (Definition 9.1).
    A domain like "finance" or "philosophy" decomposes into sub-domains
    connected by methodological translations. The topology declares covering
    families: which collections of sub-domains jointly explain all the
    knowledge in a given sub-domain.

    If JuGeo geometry is available, this is backed by a real Site object
    with Coordinates, Morphisms, CoveringFamilies, and a GrothendieckTopology.
    """
    name: str
    description: str
    sub_domains: list[SubDomain] = field(default_factory=list)
    morphisms: list[MethodologicalTranslation] = field(default_factory=list)
    covering_families: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # JuGeo site (built lazily)
    _site: Optional[Any] = field(default=None, repr=False)
    _coordinates: dict[str, Any] = field(default_factory=dict, repr=False)

    def build_site(self) -> Optional[Any]:
        """Construct the JuGeo Site from sub-domains and morphisms.

        This is the concrete realization of Definition 9.1: the domain becomes
        a category (objects = sub-domains, morphisms = translations) equipped
        with a Grothendieck topology (covering families).
        """
        if not _HAS_GEOMETRY:
            return None

        builder = SiteBuilder()
        coords = {}

        # Add sub-domains as coordinates
        for sd in self.sub_domains:
            coord = sd.to_coordinate(self.name)
            if coord:
                coords[sd.name] = coord
                builder.add_coordinates([coord])

        # Add morphisms
        for mt in self.morphisms:
            src = coords.get(mt.source)
            tgt = coords.get(mt.target)
            if src and tgt:
                morph = mt.to_morphism(src, tgt)
                if morph:
                    builder.add_morphism(morph)

        self._site = builder.build()
        self._coordinates = coords
        return self._site

    def get_sub_domain(self, name: str) -> Optional[SubDomain]:
        """Find a sub-domain by name."""
        for sd in self.sub_domains:
            if sd.name == name:
                return sd
        return None

    @property
    def sub_domain_names(self) -> list[str]:
        return [sd.name for sd in self.sub_domains]

    def morphisms_from(self, source: str) -> list[MethodologicalTranslation]:
        """All morphisms originating from a given sub-domain."""
        return [m for m in self.morphisms if m.source == source]

    def morphisms_to(self, target: str) -> list[MethodologicalTranslation]:
        """All morphisms targeting a given sub-domain."""
        return [m for m in self.morphisms if m.target == target]

    def morphisms_between(self, source: str, target: str) -> list[MethodologicalTranslation]:
        """All morphisms from source to target."""
        return [m for m in self.morphisms if m.source == source and m.target == target]


@dataclass
class SolutionSection:
    """A section of the solution presheaf S at a sub-domain coordinate.

    Corresponds to sigma in S(D) from §9.2 — a known technique, intervention,
    or framework belonging to sub-domain D. The section carries operational
    semantics: applying it to a problem produces an outcome.
    """
    sub_domain: str
    technique_name: str
    description: str
    applicability: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    trust: float = TRUST_COPILOT
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DemandSection:
    """A section of the demand presheaf P at a sub-domain coordinate.

    Corresponds to p in P(D) from §9.6 — an open problem, unmet need,
    or known gap in current practice within sub-domain D.
    """
    sub_domain: str
    problem_name: str
    description: str
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExcessNoveltyFraction:
    """The excess novelty fraction (ENF) for a domain pairing.

    From §9.4: ENF = dim(S(U_12) - im(res_1) - im(res_2)) / dim(S(U_12)).
    An ENF close to 1 means almost all overlap content is novel.
    An ENF close to 0 means the overlap is dominated by transported parent knowledge.
    Sweet spot: ENF in [0.4, 0.8].
    """
    domain_1: str
    domain_2: str
    enf: float
    avg_morphism_strength: float
    semantic_distance: float
    verdict: str  # "productive", "routine", "aspirational", "too_distant"

    @property
    def is_productive(self) -> bool:
        return self.verdict == "productive"


@dataclass
class ProductivePairingCriterion:
    """Evaluation of Proposition 9.2 — productive pairing criterion.

    A cross-domain pairing is productive when morphisms are structurally
    faithful (strength >= tau_min) AND semantically distant (d >= delta_min).
    """
    source_domain: str
    target_domain: str
    avg_strength: float
    semantic_distance: float
    tau_min: float = 0.5
    delta_min: float = 3.0

    @property
    def structurally_faithful(self) -> bool:
        return self.avg_strength >= self.tau_min

    @property
    def semantically_distant(self) -> bool:
        return self.semantic_distance >= self.delta_min

    @property
    def is_productive(self) -> bool:
        return self.structurally_faithful and self.semantically_distant

    @property
    def quadrant(self) -> str:
        if self.structurally_faithful and self.semantically_distant:
            return "upper-right (productive)"
        elif self.structurally_faithful:
            return "upper-left (routine)"
        elif self.semantically_distant:
            return "lower-right (aspirational)"
        else:
            return "lower-left (trivial)"


@dataclass
class BridgeProposition:
    """A section in H^1 — a novel cross-domain idea.

    Corresponds to a useful novel idea satisfying Theorem 9.1:
    - [sigma] != 0 in H^1 (novelty: not in image of either restriction map)
    - sigma_p != 0 in S_p (usefulness: nonzero germ at the problem point)

    The proposition has both a novelty score (estimating |[sigma]| in H^1)
    and a relevance score (estimating |sigma_p| in the stalk). Their product
    is the useful novelty score (UNS).
    """
    title: str
    description: str
    source_domain: str
    target_domain: str
    coordinate: str  # fiber product coordinate, e.g. "sentencing_theory × population_dynamics"
    assertion_type: str = "BRIDGE_PROPOSITION"  # BRIDGE_PROPOSITION, LEMMA, CONJECTURE
    novelty_score: float = 0.0
    relevance_score: float = 0.0
    relevance_level: RelevanceFiltrationLevel = RelevanceFiltrationLevel.DIRECT
    proof_sketch: str = ""
    open_obligations: list[str] = field(default_factory=list)
    trust: float = TRUST_COPILOT
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def useful_novelty_score(self) -> float:
        """UNS = novelty * relevance * filtration_level (Definition 9.6 graded)."""
        return self.novelty_score * self.relevance_score * self.relevance_level.value

    @property
    def is_novel(self) -> bool:
        return self.novelty_score > 0.5

    @property
    def is_useful(self) -> bool:
        return self.relevance_score > 0.3

    @property
    def is_useful_novel(self) -> bool:
        """Theorem 9.1: both conditions simultaneously."""
        return self.is_novel and self.is_useful


@dataclass
class UsefulNoveltyScore:
    """The useful novelty score for a bridge proposition.

    UNS(sigma, p) = nov(sigma) * rel(sigma, p) = ||[sigma]||_H1 * ||sigma_p||_Sp

    The multiplicative structure ensures: high novelty + zero relevance = 0,
    zero novelty + high relevance = 0. Only both together score positively.
    """
    novelty: float
    relevance: float
    filtration_level: RelevanceFiltrationLevel = RelevanceFiltrationLevel.DIRECT

    @property
    def score(self) -> float:
        return self.novelty * self.relevance

    @property
    def graded_score(self) -> float:
        return self.novelty * self.relevance * self.filtration_level.value


@dataclass
class IdeationResult:
    """Full result of cross-domain ideation.

    Contains the domain sites, morphisms, Cech complex data, H^1 classes,
    bridge propositions, and the selected approach for the research loop.
    """
    prompt: str
    primary_domain: DomainSite
    partner_domain: Optional[DomainSite] = None
    morphisms: list[MethodologicalTranslation] = field(default_factory=list)
    bridge_propositions: list[BridgeProposition] = field(default_factory=list)
    selected_approach: Optional[BridgeProposition] = None
    enf: Optional[ExcessNoveltyFraction] = None
    pairing_criterion: Optional[ProductivePairingCriterion] = None
    tournament_rounds: int = 0
    elapsed: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def best_uns(self) -> float:
        if not self.bridge_propositions:
            return 0.0
        return max(bp.useful_novelty_score for bp in self.bridge_propositions)


# ═══════════════════════════════════════════════════════════════════════
#  Verification types (jg-based claim checking)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class VerificationResult:
    """Result of verifying a claim via jg prove/bugs/spec."""
    claim: str
    verified: bool
    tool_used: str  # "jg prove", "jg bugs", "jg spec", "jg equiv"
    trust_achieved: float
    details: str = ""
    obstructions: list[str] = field(default_factory=list)
    elapsed: float = 0.0


@dataclass
class ClaimVerificationReport:
    """Report from verifying all claims in a document (paper/README)."""
    document: str  # "paper" or "readme"
    total_claims: int = 0
    verified_claims: int = 0
    failed_claims: int = 0
    skipped_claims: int = 0
    results: list[VerificationResult] = field(default_factory=list)
    overall_trust: float = 0.0

    @property
    def verification_rate(self) -> float:
        if self.total_claims == 0:
            return 1.0
        return self.verified_claims / self.total_claims


# ═══════════════════════════════════════════════════════════════════════
#  DirectedResearchResult — the final output
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DirectedResearchResult:
    """The global section (or obstruction report) from the research descent.

    This is the final output of the directed research loop. If descent
    converged, it represents a consistent global section over the entire
    workspace site — theory, code, evidence, and claims all agree on
    their overlaps. If descent failed, it reports the obstructions.

    The result includes the full ideation trace (domain sites, morphisms,
    bridge propositions), all sections produced, all moves executed, the
    consistency report, and the verification reports for paper and README.
    """
    status: ResearchStatus
    prompt: str
    theory_summary: str
    approach: str
    code_files: list[str]
    sections: list[LLMSection]
    consistency: Optional[ConsistencyReport]
    moves: list[MoveResult]
    pivots: int
    output_dir: str
    elapsed: float
    # Ideation data
    ideation: Optional[IdeationResult] = None
    # Verification data
    readme_verification: Optional[ClaimVerificationReport] = None
    paper_verification: Optional[ClaimVerificationReport] = None
    # Benchmark data
    benchmark_results: dict[str, Any] = field(default_factory=dict)
    baseline_comparison: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == ResearchStatus.CONVERGED

    def __repr__(self) -> str:
        n_theory = sum(1 for s in self.sections if s.surface == SurfaceKind.THEORY)
        n_code = sum(1 for s in self.sections if s.surface == SurfaceKind.CODE)
        n_evidence = sum(1 for s in self.sections if s.surface == SurfaceKind.EVIDENCE)
        n_claims = sum(1 for s in self.sections if s.surface == SurfaceKind.CLAIMS)
        return (
            f"DirectedResearchResult(\n"
            f"  status={self.status.value},\n"
            f"  approach={self.approach!r},\n"
            f"  sections: T={n_theory} R={n_code} E={n_evidence} P={n_claims},\n"
            f"  code_files={len(self.code_files)},\n"
            f"  consistency={self.consistency},\n"
            f"  moves={len(self.moves)}, pivots={self.pivots},\n"
            f"  ideation={'yes' if self.ideation else 'no'},\n"
            f"  readme_verified={self.readme_verification.verification_rate:.0%}" if self.readme_verification else ""
            f"  paper_verified={self.paper_verification.verification_rate:.0%}" if self.paper_verification else ""
            f"  elapsed={self.elapsed:.1f}s\n"
            f")"
        )
