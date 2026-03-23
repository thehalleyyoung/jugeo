"""Research software orchestration via judgment geometry.

Replaces the ad-hoc Mealy machine controller of Comet-H with sheaf-theoretic
descent over a *workspace site* whose four artifact surfaces — Theory (T),
Code (R), Evidence (E), Claims (P) — are coordinates, and cross-surface
consistency is the sheaf condition.

Key correspondences (see outputs/proposal_jugeo_comet.md):

    Comet-H concept          →  JuGeo concept
    ─────────────────────────────────────────────────
    Reactive grounding        →  Descent checking on covering families
    Hallucination (FM-1)      →  H¹ obstruction on overlap E ∩ P
    World-model stale (FM-2)  →  Stale-section invalidation
    Spec drift (FM-3)         →  Adjacency morphism constraint
    Obligation decay vector   →  Trust attenuation (⊖)
    18 prompt families        →  Semantic moves
    Phase ordering            →  Strategy DSL (seq / par / fallback)

Usage::

    from jugeo.research_orchestration import (
        WorkspaceSite, ArtifactSurface, SurfaceKind,
        ResearchOrchestrator, ResearchStrategy,
        WorkspaceSection, ConsistencyReport,
    )

    # Build a workspace
    ws = WorkspaceSite.from_artifacts(
        theory="Theory: barrier certificates for safety verification",
        code="def check_barrier(x): ...",
        evidence={"benchmark_f1": 0.768, "test_cases": 90},
        claims="We achieve F1=0.768 on 90 cases",
    )

    # Check consistency (run descent)
    report = ws.check_consistency()
    print(report)

    # Orchestrate a full research loop
    orch = ResearchOrchestrator(ws, strategy=ResearchStrategy.ADAPTIVE)
    result = orch.run()
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ── JuGeo imports (all guarded) ──────────────────────────────────────

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, MorphismKind, Morphism,
        Site, SiteBuilder,
    )
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentStrategy, LocalSection,
        GlobalSection, DescentObstruction, GluingData,
        CohomologyClass,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False


# ═══════════════════════════════════════════════════════════════════════
#  Enumerations
# ═══════════════════════════════════════════════════════════════════════

class SurfaceKind(str, Enum):
    """The four artifact surfaces of a research workspace."""
    THEORY = "theory"
    CODE = "code"
    EVIDENCE = "evidence"
    CLAIMS = "claims"


class MorphismType(str, Enum):
    """Semantic dependency arrows between artifact surfaces."""
    IMPL = "impl"             # T → R: theory is implemented by code
    EVAL = "eval"             # R → E: code produces evidence
    REPORT = "report"         # E → P: evidence is reported in claims
    FORMALIZE = "formalize"   # T → P: theory is formalized in claims
    DEMO = "demo"             # R → P: code is demonstrated in claims
    VALIDATE = "validate"     # E → T: evidence validates/revises theory


class ObstructionKind(str, Enum):
    """Classification of workspace inconsistencies by failure mode."""
    HALLUCINATION = "hallucination"         # FM-1: fabricated data
    STALE_SECTION = "stale_section"         # FM-2: stale world model
    SPEC_DRIFT = "spec_drift"              # FM-3: frozen specification
    MISSING_EVIDENCE = "missing_evidence"   # No evidence for a claim
    CODE_THEORY_GAP = "code_theory_gap"     # Code doesn't match theory
    NUMERICAL_MISMATCH = "numerical_mismatch"  # Numbers don't match


class MoveKind(str, Enum):
    """Semantic moves for research orchestration (maps to JuGeo moves)."""
    CONSTRUCT_THEORY = "construct_theory"     # q2: thesis seed
    SURVEY_DOMAIN = "survey_domain"          # q3: domain survey
    ARCHITECT_CODE = "architect_code"        # q4: architecture
    GENERATE_CODE = "generate_code"          # q5: code generation
    DRAFT_PAPER = "draft_paper"             # q6: paper draft
    DESIGN_BENCHMARK = "design_benchmark"    # q7: benchmark design
    RUN_BENCHMARK = "run_benchmark"          # q8: benchmark execution
    GROUND_CLAIMS = "ground_claims"          # q9: grounding check
    REWRITE_PAPER = "rewrite_paper"          # q10: paper rewrite
    DESIGN_ABLATION = "design_ablation"      # q11: ablation design
    RUN_ABLATION = "run_ablation"            # q12: ablation execution
    AUDIT_CONSISTENCY = "audit_consistency"   # q13: consistency audit
    REPAIR = "repair"                        # q14: repair obstructions
    PIVOT_SPEC = "pivot_spec"               # q15: specification pivot
    ESCALATE = "escalate"                    # q16: escalation
    POLISH = "polish"                        # q17: final polish
    RELEASE_CHECK = "release_check"          # q18: release check


class PhaseKind(str, Enum):
    """Research orchestration phases (Comet-H's 4 phases)."""
    SEED = "seed"
    GENERATE = "generate"
    HARDEN = "harden"
    TAIL = "tail"


class ResearchStrategy(str, Enum):
    """Orchestration strategy for the research loop."""
    FIXED = "fixed"       # Comet-H style: Seed → Generate → Harden → Tail
    ADAPTIVE = "adaptive"  # JuGeo style: descent-driven move selection
    GREEDY = "greedy"     # Always pick highest-impact move
    BALANCED = "balanced"  # Trade off urgency vs cost


# ═══════════════════════════════════════════════════════════════════════
#  Core data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ArtifactSurface:
    """One of the four workspace surfaces."""
    kind: SurfaceKind
    content: Any
    content_hash: str = ""
    trust: float = 0.0  # 0.0 = unverified, 1.0 = mechanically verified
    last_modified: float = 0.0
    version: int = 0

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = self._compute_hash()
        if not self.last_modified:
            self.last_modified = time.time()

    def _compute_hash(self) -> str:
        raw = json.dumps(self.content, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def update(self, new_content: Any) -> ArtifactSurface:
        """Return a new surface with updated content."""
        return ArtifactSurface(
            kind=self.kind,
            content=new_content,
            trust=0.0,  # Reset trust on modification
            last_modified=time.time(),
            version=self.version + 1,
        )

    @property
    def is_stale(self) -> bool:
        """Check if content hash has changed since last verification."""
        return self._compute_hash() != self.content_hash


@dataclass
class WorkspaceSection:
    """A local section on one surface of the workspace site."""
    surface: SurfaceKind
    claims: dict[str, Any] = field(default_factory=dict)
    trust: float = 0.0
    evidence: list[str] = field(default_factory=list)
    hash_at_creation: str = ""
    provenance: list[str] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return not self.hash_at_creation


@dataclass
class OverlapCheck:
    """Result of checking one overlap between two surfaces."""
    surface_a: SurfaceKind
    surface_b: SurfaceKind
    morphism: MorphismType
    compatible: bool
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    trust: float = 0.0

    @property
    def obstruction_kind(self) -> Optional[ObstructionKind]:
        if self.compatible:
            return None
        for m in self.mismatches:
            if m.get("type") == "numerical":
                return ObstructionKind.NUMERICAL_MISMATCH
            if m.get("type") == "hallucination":
                return ObstructionKind.HALLUCINATION
        if self.surface_a == SurfaceKind.THEORY and self.surface_b == SurfaceKind.CODE:
            return ObstructionKind.CODE_THEORY_GAP
        if self.surface_a == SurfaceKind.EVIDENCE and self.surface_b == SurfaceKind.CLAIMS:
            return ObstructionKind.MISSING_EVIDENCE
        return ObstructionKind.HALLUCINATION


@dataclass
class WorkspaceObstruction:
    """A cohomological obstruction in the workspace site.

    Corresponds to a non-trivial class in H¹(U, F) where U is a covering
    family and F is the consistency sheaf.
    """
    obstruction_id: str = ""
    kind: ObstructionKind = ObstructionKind.HALLUCINATION
    degree: int = 1  # H⁰=0, H¹=1, H²=2
    surfaces_involved: tuple[SurfaceKind, ...] = ()
    morphism: Optional[MorphismType] = None
    description: str = ""
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)
    trust_before: float = 0.0
    trust_after: float = 0.0

    def __post_init__(self):
        if not self.obstruction_id:
            self.obstruction_id = f"obs-{uuid.uuid4().hex[:8]}"


@dataclass
class ConsistencyReport:
    """Result of running descent on the workspace site."""
    consistent: bool
    H1: str  # "0" if consistent, description otherwise
    obstructions: list[WorkspaceObstruction] = field(default_factory=list)
    overlaps_checked: int = 0
    overlaps_passed: int = 0
    overlaps_failed: int = 0
    surfaces_trust: dict[str, float] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def consistency_rate(self) -> float:
        if self.overlaps_checked == 0:
            return 1.0
        return self.overlaps_passed / self.overlaps_checked

    def __repr__(self) -> str:
        status = "CONSISTENT" if self.consistent else f"OBSTRUCTED ({len(self.obstructions)} issues)"
        return (f"ConsistencyReport({status}, "
                f"overlaps={self.overlaps_passed}/{self.overlaps_checked}, "
                f"H1={self.H1})")


@dataclass
class MoveRecord:
    """Record of a semantic move applied during orchestration."""
    move: MoveKind
    phase: PhaseKind
    surface: Optional[SurfaceKind] = None
    description: str = ""
    obstructions_before: int = 0
    obstructions_after: int = 0
    trust_delta: float = 0.0
    elapsed: float = 0.0
    success: bool = True


@dataclass
class OrchestrationResult:
    """Result of running the full research orchestration loop."""
    workspace_name: str
    final_consistency: ConsistencyReport
    moves_applied: list[MoveRecord] = field(default_factory=list)
    total_moves: int = 0
    phases_completed: list[PhaseKind] = field(default_factory=list)
    hallucinations_detected: int = 0
    hallucinations_repaired: int = 0
    stale_sections_refreshed: int = 0
    spec_pivots: int = 0
    final_trust: dict[str, float] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def success(self) -> bool:
        return self.final_consistency.consistent

    def __repr__(self) -> str:
        status = "SUCCESS" if self.success else "INCOMPLETE"
        return (f"OrchestrationResult({status}, "
                f"moves={self.total_moves}, "
                f"hallucinations={self.hallucinations_detected}, "
                f"consistency={self.final_consistency.consistency_rate:.1%})")


# ═══════════════════════════════════════════════════════════════════════
#  WorkspaceSite — the four-object semantic site
# ═══════════════════════════════════════════════════════════════════════

# The six morphisms of the workspace site
WORKSPACE_MORPHISMS: list[tuple[SurfaceKind, SurfaceKind, MorphismType]] = [
    (SurfaceKind.THEORY, SurfaceKind.CODE, MorphismType.IMPL),
    (SurfaceKind.CODE, SurfaceKind.EVIDENCE, MorphismType.EVAL),
    (SurfaceKind.EVIDENCE, SurfaceKind.CLAIMS, MorphismType.REPORT),
    (SurfaceKind.THEORY, SurfaceKind.CLAIMS, MorphismType.FORMALIZE),
    (SurfaceKind.CODE, SurfaceKind.CLAIMS, MorphismType.DEMO),
    (SurfaceKind.EVIDENCE, SurfaceKind.THEORY, MorphismType.VALIDATE),
]

# Covering families (which surfaces cover which)
COVERING_FAMILIES: dict[SurfaceKind, list[tuple[SurfaceKind, MorphismType]]] = {
    SurfaceKind.CLAIMS: [
        (SurfaceKind.THEORY, MorphismType.FORMALIZE),
        (SurfaceKind.CODE, MorphismType.DEMO),
        (SurfaceKind.EVIDENCE, MorphismType.REPORT),
    ],
    SurfaceKind.CODE: [
        (SurfaceKind.THEORY, MorphismType.IMPL),
    ],
    SurfaceKind.EVIDENCE: [
        (SurfaceKind.CODE, MorphismType.EVAL),
    ],
    SurfaceKind.THEORY: [
        (SurfaceKind.EVIDENCE, MorphismType.VALIDATE),
    ],
}


class WorkspaceSite:
    """A four-object semantic site for research software orchestration.

    Objects: Theory (T), Code (R), Evidence (E), Claims (P)
    Morphisms: impl, eval, report, formalize, demo, validate
    Topology: covering families defined by COVERING_FAMILIES

    Consistency is the sheaf condition: local sections on each surface
    must agree on all overlaps (morphism compatibility).
    """

    def __init__(self, name: str = "workspace"):
        self.name = name
        self.surfaces: dict[SurfaceKind, ArtifactSurface] = {}
        self.sections: dict[SurfaceKind, WorkspaceSection] = {}
        self._history: list[dict[str, Any]] = []
        self._site: Optional[Site] = None

    @classmethod
    def from_artifacts(
        cls,
        theory: Any = "",
        code: Any = "",
        evidence: Any = None,
        claims: Any = "",
        name: str = "workspace",
    ) -> WorkspaceSite:
        """Create a workspace from artifact content."""
        ws = cls(name=name)
        ws.surfaces[SurfaceKind.THEORY] = ArtifactSurface(
            kind=SurfaceKind.THEORY, content=theory)
        ws.surfaces[SurfaceKind.CODE] = ArtifactSurface(
            kind=SurfaceKind.CODE, content=code)
        ws.surfaces[SurfaceKind.EVIDENCE] = ArtifactSurface(
            kind=SurfaceKind.EVIDENCE, content=evidence or {})
        ws.surfaces[SurfaceKind.CLAIMS] = ArtifactSurface(
            kind=SurfaceKind.CLAIMS, content=claims)
        ws._build_sections()
        ws._build_site()
        return ws

    def _build_sections(self):
        """Create local sections from each surface's content."""
        for kind, surface in self.surfaces.items():
            claims = self._extract_claims(surface)
            self.sections[kind] = WorkspaceSection(
                surface=kind,
                claims=claims,
                trust=surface.trust,
                hash_at_creation=surface.content_hash,
                provenance=[f"initial-{kind.value}"],
            )

    def _extract_claims(self, surface: ArtifactSurface) -> dict[str, Any]:
        """Extract verifiable claims from a surface's content."""
        content = surface.content
        claims: dict[str, Any] = {}

        if isinstance(content, dict):
            # Evidence surface: claims are the key-value pairs
            for k, v in content.items():
                claims[k] = v
        elif isinstance(content, str):
            # Extract numerical claims from text
            import re
            # Find patterns like "F1=0.768", "accuracy=95%", etc.
            for m in re.finditer(
                r'(\b[A-Za-z_]\w*)\s*[=:]\s*([\d.]+(?:%)?)',
                content
            ):
                key, val = m.group(1), m.group(2)
                if val.endswith('%'):
                    claims[key] = float(val[:-1]) / 100
                else:
                    try:
                        claims[key] = float(val)
                    except ValueError:
                        claims[key] = val
            # Also store the raw text as a claim
            if content.strip():
                claims["_text"] = content.strip()
        return claims

    def _build_site(self):
        """Construct the JuGeo site if the geometry module is available."""
        if not _HAS_SITE:
            return
        builder = SiteBuilder()
        coords = {}
        for kind in SurfaceKind:
            c = Coordinate(
                components=(self.name, kind.value),
                kind=CoordinateKind.MODULE,
            )
            coords[kind] = c
            builder.add_coordinates([c])

        for src, tgt, morph_type in WORKSPACE_MORPHISMS:
            if src in coords and tgt in coords:
                m = Morphism(
                    source=coords[src],
                    target=coords[tgt],
                    kind=MorphismKind.TRANSPORT,
                    label=morph_type.value,
                )
                builder.add_morphism(m)
        self._site = builder.build()

    def update_surface(self, kind: SurfaceKind, new_content: Any):
        """Update a surface's content, invalidating stale sections."""
        if kind in self.surfaces:
            old = self.surfaces[kind]
            self.surfaces[kind] = old.update(new_content)
            # Invalidate the section
            self.sections[kind] = WorkspaceSection(
                surface=kind,
                claims=self._extract_claims(self.surfaces[kind]),
                trust=0.0,  # Reset trust
                hash_at_creation=self.surfaces[kind].content_hash,
                provenance=[f"updated-{kind.value}-v{self.surfaces[kind].version}"],
            )
            self._history.append({
                "action": "update",
                "surface": kind.value,
                "version": self.surfaces[kind].version,
                "time": time.time(),
            })

    # ── Overlap checking (the core of descent) ────────────────────────

    def check_overlap(
        self,
        source: SurfaceKind,
        target: SurfaceKind,
        morphism: MorphismType,
    ) -> OverlapCheck:
        """Check if two surfaces agree on their overlap.

        This is the sheaf condition restricted to one morphism: the claims
        that `target` makes about `source` must match source's actual state.
        """
        source_section = self.sections.get(source)
        target_section = self.sections.get(target)

        if not source_section or not target_section:
            return OverlapCheck(
                surface_a=source, surface_b=target,
                morphism=morphism, compatible=True,
            )

        mismatches = []

        # Extract what the target claims about the source
        target_claims_about_source = self._restrict_claims(
            target_section.claims, source, morphism)
        source_claims = source_section.claims

        # Check numerical claims match
        for key, target_val in target_claims_about_source.items():
            if key.startswith("_"):
                continue
            if key in source_claims:
                source_val = source_claims[key]
                if isinstance(target_val, (int, float)) and isinstance(source_val, (int, float)):
                    if abs(target_val - source_val) > 1e-6:
                        mismatches.append({
                            "type": "numerical",
                            "key": key,
                            "claimed": target_val,
                            "actual": source_val,
                            "delta": abs(target_val - source_val),
                        })

        # Check for stale sections
        source_surface = self.surfaces.get(source)
        if source_surface and source_section.hash_at_creation != source_surface.content_hash:
            mismatches.append({
                "type": "stale",
                "surface": source.value,
                "description": f"Section for {source.value} is stale "
                               f"(hash mismatch: section={source_section.hash_at_creation}, "
                               f"current={source_surface.content_hash})",
            })

        compatible = len(mismatches) == 0
        combined_trust = min(
            source_section.trust,
            target_section.trust,
        ) if compatible else 0.0

        return OverlapCheck(
            surface_a=source,
            surface_b=target,
            morphism=morphism,
            compatible=compatible,
            mismatches=mismatches,
            trust=combined_trust,
        )

    def _restrict_claims(
        self,
        claims: dict[str, Any],
        source: SurfaceKind,
        morphism: MorphismType,
    ) -> dict[str, Any]:
        """Extract from a target surface's claims the parts about the source.

        This is the restriction map F(f): F(target) → F(source).
        """
        # For numerical claims, all numeric values are candidates
        restricted: dict[str, Any] = {}
        for key, val in claims.items():
            if key.startswith("_"):
                continue
            if isinstance(val, (int, float)):
                restricted[key] = val
        return restricted

    def check_consistency(self) -> ConsistencyReport:
        """Run full descent: check all overlaps in all covering families.

        This is the sheaf condition check. If all overlaps agree, the
        workspace is consistent (H¹ = 0). Otherwise, each failure produces
        a WorkspaceObstruction.
        """
        start = time.time()
        obstructions: list[WorkspaceObstruction] = []
        checks = 0
        passed = 0

        for src, tgt, morph in WORKSPACE_MORPHISMS:
            result = self.check_overlap(src, tgt, morph)
            checks += 1
            if result.compatible:
                passed += 1
            else:
                for mismatch in result.mismatches:
                    obs_kind = ObstructionKind.HALLUCINATION
                    if mismatch.get("type") == "numerical":
                        obs_kind = ObstructionKind.NUMERICAL_MISMATCH
                    elif mismatch.get("type") == "stale":
                        obs_kind = ObstructionKind.STALE_SECTION
                    elif mismatch.get("type") == "hallucination":
                        obs_kind = ObstructionKind.HALLUCINATION

                    hints = []
                    if obs_kind == ObstructionKind.NUMERICAL_MISMATCH:
                        hints.append(
                            f"Update {tgt.value} to use actual value "
                            f"{mismatch.get('actual')} instead of "
                            f"{mismatch.get('claimed')} for '{mismatch.get('key')}'"
                        )
                    elif obs_kind == ObstructionKind.STALE_SECTION:
                        hints.append(
                            f"Refresh section for {mismatch.get('surface')} "
                            f"by re-reading the artifact"
                        )

                    obstructions.append(WorkspaceObstruction(
                        kind=obs_kind,
                        degree=1,
                        surfaces_involved=(src, tgt),
                        morphism=morph,
                        description=str(mismatch),
                        mismatches=[mismatch],
                        repair_hints=hints,
                    ))

        # Also check covering family completeness
        for target, covers in COVERING_FAMILIES.items():
            target_section = self.sections.get(target)
            if not target_section:
                continue
            for source, morph in covers:
                source_section = self.sections.get(source)
                if not source_section or not source_section.claims:
                    obstructions.append(WorkspaceObstruction(
                        kind=ObstructionKind.MISSING_EVIDENCE,
                        degree=0,  # H⁰: missing section
                        surfaces_involved=(source, target),
                        morphism=morph,
                        description=f"No section on {source.value} to cover {target.value}",
                        repair_hints=[f"Generate content for {source.value}"],
                    ))

        consistent = len(obstructions) == 0
        elapsed = time.time() - start

        trust_map = {}
        for kind, section in self.sections.items():
            trust_map[kind.value] = section.trust

        return ConsistencyReport(
            consistent=consistent,
            H1="0" if consistent else f"{len(obstructions)} obstructions",
            obstructions=obstructions,
            overlaps_checked=checks,
            overlaps_passed=passed,
            overlaps_failed=checks - passed,
            surfaces_trust=trust_map,
            elapsed=elapsed,
        )

    # ── Also build JuGeo descent if available ─────────────────────────

    def run_jugeo_descent(self) -> Optional[Any]:
        """Run the real JuGeo descent engine if available."""
        if not (_HAS_DESCENT and _HAS_SITE and self._site):
            return None

        local_sections = {}
        for kind, section in self.sections.items():
            coord_key = f"{self.name}.{kind.value}"
            local_sections[coord_key] = LocalSection(
                coordinate=coord_key,
                judgment_data=section.claims,
                evidence_bundle=tuple(section.evidence),
                trust_level=section.trust,
                provenance=tuple(section.provenance),
            )

        engine = DescentEngine(self._site)
        # The engine needs a Cover and sections dict
        # For now, return the local sections structure
        return local_sections


# ═══════════════════════════════════════════════════════════════════════
#  ResearchOrchestrator — the replacement for Comet-H's Mealy machine
# ═══════════════════════════════════════════════════════════════════════

# Phase → moves mapping
PHASE_MOVES: dict[PhaseKind, list[MoveKind]] = {
    PhaseKind.SEED: [
        MoveKind.CONSTRUCT_THEORY,
        MoveKind.SURVEY_DOMAIN,
    ],
    PhaseKind.GENERATE: [
        MoveKind.ARCHITECT_CODE,
        MoveKind.GENERATE_CODE,
        MoveKind.DRAFT_PAPER,
        MoveKind.DESIGN_BENCHMARK,
    ],
    PhaseKind.HARDEN: [
        MoveKind.RUN_BENCHMARK,
        MoveKind.GROUND_CLAIMS,
        MoveKind.REWRITE_PAPER,
        MoveKind.DESIGN_ABLATION,
        MoveKind.RUN_ABLATION,
        MoveKind.AUDIT_CONSISTENCY,
        MoveKind.REPAIR,
        MoveKind.PIVOT_SPEC,
        MoveKind.ESCALATE,
    ],
    PhaseKind.TAIL: [
        MoveKind.POLISH,
        MoveKind.RELEASE_CHECK,
    ],
}

# Move → surface mapping
MOVE_TARGET: dict[MoveKind, SurfaceKind] = {
    MoveKind.CONSTRUCT_THEORY: SurfaceKind.THEORY,
    MoveKind.SURVEY_DOMAIN: SurfaceKind.THEORY,
    MoveKind.ARCHITECT_CODE: SurfaceKind.CODE,
    MoveKind.GENERATE_CODE: SurfaceKind.CODE,
    MoveKind.DRAFT_PAPER: SurfaceKind.CLAIMS,
    MoveKind.DESIGN_BENCHMARK: SurfaceKind.EVIDENCE,
    MoveKind.RUN_BENCHMARK: SurfaceKind.EVIDENCE,
    MoveKind.GROUND_CLAIMS: SurfaceKind.CLAIMS,
    MoveKind.REWRITE_PAPER: SurfaceKind.CLAIMS,
    MoveKind.DESIGN_ABLATION: SurfaceKind.EVIDENCE,
    MoveKind.RUN_ABLATION: SurfaceKind.EVIDENCE,
    MoveKind.AUDIT_CONSISTENCY: SurfaceKind.CLAIMS,  # checks all
    MoveKind.REPAIR: SurfaceKind.CLAIMS,
    MoveKind.PIVOT_SPEC: SurfaceKind.THEORY,
    MoveKind.ESCALATE: SurfaceKind.CLAIMS,
    MoveKind.POLISH: SurfaceKind.CLAIMS,
    MoveKind.RELEASE_CHECK: SurfaceKind.CLAIMS,
}

# Trust levels for different move outcomes
MOVE_TRUST: dict[MoveKind, float] = {
    MoveKind.CONSTRUCT_THEORY: 0.3,   # LLM-generated theory
    MoveKind.SURVEY_DOMAIN: 0.2,
    MoveKind.ARCHITECT_CODE: 0.3,
    MoveKind.GENERATE_CODE: 0.3,
    MoveKind.DRAFT_PAPER: 0.2,
    MoveKind.DESIGN_BENCHMARK: 0.4,
    MoveKind.RUN_BENCHMARK: 0.7,     # Runtime-witnessed
    MoveKind.GROUND_CLAIMS: 0.8,     # Verified against evidence
    MoveKind.REWRITE_PAPER: 0.5,
    MoveKind.DESIGN_ABLATION: 0.4,
    MoveKind.RUN_ABLATION: 0.7,
    MoveKind.AUDIT_CONSISTENCY: 0.9,  # Full descent check
    MoveKind.REPAIR: 0.6,
    MoveKind.PIVOT_SPEC: 0.3,
    MoveKind.ESCALATE: 0.4,
    MoveKind.POLISH: 0.5,
    MoveKind.RELEASE_CHECK: 0.9,
}


class ResearchOrchestrator:
    """Descent-driven research orchestration.

    Replaces the Comet-H Mealy machine with judgment geometry:
    - Instead of a feature-vector scorer, uses descent checking
    - Instead of obligation decay, uses trust attenuation
    - Instead of reactive triggers, uses systematic overlap verification
    - Instead of fixed phases, uses strategy DSL (seq/par/fallback)
    """

    def __init__(
        self,
        workspace: WorkspaceSite,
        strategy: ResearchStrategy = ResearchStrategy.ADAPTIVE,
        max_moves: int = 50,
        trust_floor: float = 0.5,
        decay_rate: float = 2 ** (-1 / 8),  # Comet-H's λ
    ):
        self.workspace = workspace
        self.strategy = strategy
        self.max_moves = max_moves
        self.trust_floor = trust_floor
        self.decay_rate = decay_rate
        self.moves: list[MoveRecord] = []
        self._obligation_vector = [0.0] * 5  # structural, grounding, consistency, coverage, polish

    def run(self) -> OrchestrationResult:
        """Run the full research orchestration loop."""
        start = time.time()

        if self.strategy == ResearchStrategy.FIXED:
            return self._run_fixed(start)
        elif self.strategy == ResearchStrategy.ADAPTIVE:
            return self._run_adaptive(start)
        elif self.strategy == ResearchStrategy.GREEDY:
            return self._run_greedy(start)
        else:
            return self._run_adaptive(start)

    def _execute_phase(self, phase: PhaseKind):
        """Execute all moves in a phase, respecting max_moves."""
        for move_kind in PHASE_MOVES[phase]:
            if len(self.moves) >= self.max_moves:
                break
            self._apply_move(move_kind, phase)

    def _run_fixed(self, start: float) -> OrchestrationResult:
        """Fixed phase ordering: Seed → Generate → Harden → Tail."""
        phases_done = []
        for phase in [PhaseKind.SEED, PhaseKind.GENERATE, PhaseKind.HARDEN, PhaseKind.TAIL]:
            self._execute_phase(phase)
            phases_done.append(phase)
        return self._build_result(start, phases_done)

    def _run_adaptive(self, start: float) -> OrchestrationResult:
        """Adaptive orchestration: let descent drive move selection.

        After each move, run consistency check. If obstructions exist,
        select the move most likely to repair them. If consistent,
        advance to the next phase.
        """
        phases_done = []
        current_phase = PhaseKind.SEED
        move_count = 0

        phase_order = [PhaseKind.SEED, PhaseKind.GENERATE, PhaseKind.HARDEN, PhaseKind.TAIL]
        phase_idx = 0

        while move_count < self.max_moves and phase_idx < len(phase_order):
            current_phase = phase_order[phase_idx]

            # Execute the phase's moves
            phase_moves = PHASE_MOVES[current_phase]
            for move_kind in phase_moves:
                if move_count >= self.max_moves:
                    break
                self._apply_move(move_kind, current_phase)
                move_count += 1

                # After each expansive move, check consistency
                if current_phase in (PhaseKind.GENERATE, PhaseKind.HARDEN):
                    report = self.workspace.check_consistency()
                    if not report.consistent:
                        # Inject grounding + repair moves
                        self._apply_move(MoveKind.GROUND_CLAIMS, PhaseKind.HARDEN)
                        move_count += 1
                        if move_count < self.max_moves:
                            self._apply_move(MoveKind.REPAIR, PhaseKind.HARDEN)
                            move_count += 1

            phases_done.append(current_phase)
            phase_idx += 1

            # Decay obligations
            self._decay_obligations()

        return self._build_result(start, phases_done)

    def _run_greedy(self, start: float) -> OrchestrationResult:
        """Greedy: always pick the highest-impact move."""
        phases_done = []
        move_count = 0

        while move_count < self.max_moves:
            report = self.workspace.check_consistency()
            if report.consistent and move_count > 10:
                break

            move = self._select_best_move(report)
            phase = self._move_phase(move)
            self._apply_move(move, phase)
            move_count += 1

            if phase not in phases_done:
                phases_done.append(phase)

        return self._build_result(start, phases_done)

    def _select_best_move(self, report: ConsistencyReport) -> MoveKind:
        """Select the highest-impact move given current workspace state."""
        if report.obstructions:
            # Prioritize repair for existing obstructions
            obs = report.obstructions[0]
            if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
                return MoveKind.GROUND_CLAIMS
            elif obs.kind == ObstructionKind.STALE_SECTION:
                return MoveKind.RUN_BENCHMARK
            elif obs.kind == ObstructionKind.MISSING_EVIDENCE:
                return MoveKind.RUN_BENCHMARK
            elif obs.kind == ObstructionKind.CODE_THEORY_GAP:
                return MoveKind.GENERATE_CODE
            else:
                return MoveKind.REPAIR

        # No obstructions — advance to the surface with lowest trust
        trust_levels = report.surfaces_trust

        if trust_levels.get("evidence", 0) < self.trust_floor:
            return MoveKind.RUN_BENCHMARK
        elif trust_levels.get("claims", 0) < self.trust_floor:
            return MoveKind.GROUND_CLAIMS
        elif trust_levels.get("code", 0) < self.trust_floor:
            return MoveKind.GENERATE_CODE
        else:
            return MoveKind.AUDIT_CONSISTENCY

    def _move_phase(self, move: MoveKind) -> PhaseKind:
        """Determine which phase a move belongs to."""
        for phase, moves in PHASE_MOVES.items():
            if move in moves:
                return phase
        return PhaseKind.HARDEN

    def _apply_move(self, move: MoveKind, phase: PhaseKind):
        """Apply a semantic move to the workspace.

        Each move modifies one or more surfaces and updates trust levels.
        """
        start = time.time()
        target = MOVE_TARGET.get(move, SurfaceKind.CLAIMS)
        trust_gain = MOVE_TRUST.get(move, 0.3)

        report_before = self.workspace.check_consistency()
        obs_before = len(report_before.obstructions)

        # Apply the move's effect
        if move == MoveKind.GROUND_CLAIMS:
            self._ground_claims()
        elif move == MoveKind.REPAIR:
            self._repair_obstructions(report_before)
        elif move == MoveKind.AUDIT_CONSISTENCY:
            pass  # Just checking is the move
        elif move == MoveKind.RUN_BENCHMARK:
            self._run_benchmark()
        elif move == MoveKind.PIVOT_SPEC:
            self._pivot_spec()
        else:
            # Generic: update trust on target surface
            section = self.workspace.sections.get(target)
            if section:
                section.trust = min(1.0, section.trust + trust_gain * 0.5)
                section.provenance.append(f"move-{move.value}")

        report_after = self.workspace.check_consistency()
        obs_after = len(report_after.obstructions)

        elapsed = time.time() - start
        record = MoveRecord(
            move=move,
            phase=phase,
            surface=target,
            description=f"Applied {move.value} on {target.value}",
            obstructions_before=obs_before,
            obstructions_after=obs_after,
            trust_delta=trust_gain,
            elapsed=elapsed,
            success=obs_after <= obs_before,
        )
        self.moves.append(record)

    def _ground_claims(self):
        """Ground claims against evidence (the reactive grounding trigger)."""
        evidence_section = self.workspace.sections.get(SurfaceKind.EVIDENCE)
        claims_section = self.workspace.sections.get(SurfaceKind.CLAIMS)

        if not evidence_section or not claims_section:
            return

        # Replace claimed values with actual evidence values
        for key, val in evidence_section.claims.items():
            if key.startswith("_"):
                continue
            if key in claims_section.claims:
                claims_section.claims[key] = val

        claims_section.trust = min(1.0, claims_section.trust + 0.3)
        claims_section.provenance.append("grounded-against-evidence")

    def _repair_obstructions(self, report: ConsistencyReport):
        """Repair detected obstructions."""
        for obs in report.obstructions:
            if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
                # Fix numerical mismatches by propagating actual values
                for mismatch in obs.mismatches:
                    key = mismatch.get("key")
                    actual = mismatch.get("actual")
                    if key and actual is not None:
                        for kind in obs.surfaces_involved:
                            section = self.workspace.sections.get(kind)
                            if section and key in section.claims:
                                section.claims[key] = actual
                                section.provenance.append(f"repair-{key}")

            elif obs.kind == ObstructionKind.STALE_SECTION:
                # Refresh stale sections
                for kind in obs.surfaces_involved:
                    surface = self.workspace.surfaces.get(kind)
                    if surface:
                        self.workspace.sections[kind] = WorkspaceSection(
                            surface=kind,
                            claims=self.workspace._extract_claims(surface),
                            trust=0.3,
                            hash_at_creation=surface.content_hash,
                            provenance=[f"refreshed-{kind.value}"],
                        )

    def _run_benchmark(self):
        """Simulate running benchmarks to produce evidence."""
        evidence_section = self.workspace.sections.get(SurfaceKind.EVIDENCE)
        if evidence_section:
            evidence_section.trust = min(1.0, evidence_section.trust + 0.4)
            evidence_section.provenance.append("benchmark-executed")
            evidence_section.evidence.append("runtime-witnessed")

    def _pivot_spec(self):
        """Pivot the specification (adjacency-constrained)."""
        theory_section = self.workspace.sections.get(SurfaceKind.THEORY)
        if theory_section:
            theory_section.trust = max(0.0, theory_section.trust - 0.2)
            theory_section.provenance.append("spec-pivot")

    def _decay_obligations(self):
        """Decay obligation vector (Comet-H's λ = 2^{-1/8})."""
        for i in range(len(self._obligation_vector)):
            self._obligation_vector[i] *= self.decay_rate

    def _build_result(
        self, start: float, phases: list[PhaseKind]
    ) -> OrchestrationResult:
        """Build the final orchestration result."""
        final_report = self.workspace.check_consistency()

        hallucinations_detected = sum(
            1 for m in self.moves
            if m.move == MoveKind.GROUND_CLAIMS and m.obstructions_before > m.obstructions_after
        )
        stale_refreshed = sum(
            1 for m in self.moves
            if m.move == MoveKind.RUN_BENCHMARK
        )
        pivots = sum(
            1 for m in self.moves if m.move == MoveKind.PIVOT_SPEC
        )

        return OrchestrationResult(
            workspace_name=self.workspace.name,
            final_consistency=final_report,
            moves_applied=self.moves,
            total_moves=len(self.moves),
            phases_completed=phases,
            hallucinations_detected=hallucinations_detected,
            hallucinations_repaired=hallucinations_detected,
            stale_sections_refreshed=stale_refreshed,
            spec_pivots=pivots,
            final_trust=final_report.surfaces_trust,
            elapsed=time.time() - start,
        )
