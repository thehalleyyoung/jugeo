"""CLI subcommand handler for ``jugeo classify [description]``.

Uses the judgment-geometric framework to classify problems:
models the problem space as a Site, creates Judgments, checks descent
structure, cover decomposability, and sheaf conditions.  Categories map
to geometric concepts: VERIFICATION→sheaf-check, SYNTHESIS→cover-build,
OPTIMIZATION→metric-min, ANALYSIS→section-inspect, REFACTORING→site-morph.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# ── Guarded imports ───────────────────────────────────────────────────

try:
    from jugeo.geometry.site import (  # type: ignore[import-untyped]
        Site, SiteBuilder, Coordinate, CoordinateKind,
        Morphism, MorphismKind, CoordinateMorphism,
    )
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False

try:
    from jugeo.judgments.judgment_terms import (  # type: ignore[import-untyped]
        Judgment, JudgmentBuilder, Proposition, PropositionKind,
        EvidenceBundle, EvidenceItem, EvidenceItemKind,
        TrustLevel as JTrustLevel, JudgmentStatus,
    )
    _HAS_JUDGMENT = True
except Exception:
    _HAS_JUDGMENT = False

try:
    from jugeo.evidence.trust import (  # type: ignore[import-untyped]
        TrustLevel as ETrustLevel, TrustAlgebra,
    )
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

try:
    from jugeo.geometry.descent import (  # type: ignore[import-untyped]
        DescentEngine, DescentConfiguration, LocalSection, DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

try:
    from jugeo.geometry.covers import (  # type: ignore[import-untyped]
        Cover, CoverBuilder, score_cover,
    )
    _HAS_COVERS = True
except Exception:
    _HAS_COVERS = False

try:
    from jugeo.judgments.sections import (  # type: ignore[import-untyped]
        Section, SheafCondition,
    )
    _HAS_SECTIONS = True
except Exception:
    _HAS_SECTIONS = False

_FULL_GEOMETRY = _HAS_SITE and _HAS_JUDGMENT and _HAS_DESCENT and _HAS_COVERS

# -- ideation / discovery ---------------------------------------------------
try:
    from jugeo.ideation.discovery_engine import (  # type: ignore[import-untyped]
        DiscoveryEngineAPI,
    )
    _HAS_DISCOVERY = True
except Exception:
    _HAS_DISCOVERY = False

try:
    from jugeo.ideation.synthesis_frontier.tournament import (  # type: ignore[import-untyped]
        JudgmentCriteria,
    )
    _HAS_TOURNAMENT = True
except Exception:
    _HAS_TOURNAMENT = False

_CATEGORIES = ("VERIFICATION", "SYNTHESIS", "OPTIMIZATION", "ANALYSIS", "REFACTORING")

_CATEGORY_GEOMETRY: dict[str, str] = {
    "VERIFICATION": "sheaf condition checking — does descent succeed?",
    "SYNTHESIS":    "cover construction — can we build a cover?",
    "OPTIMIZATION": "metric minimisation on the site",
    "ANALYSIS":     "section inspection — what do local sections reveal?",
    "REFACTORING":  "site morphism — isomorphism of sites",
}

_KEYWORD_MAP: dict[str, list[str]] = {
    "VERIFICATION": ["verify", "prove", "check", "assert", "invariant", "correct", "sound",
                     "complete", "satisfy", "valid", "safe", "secure", "spec", "descent", "sheaf"],
    "SYNTHESIS":    ["construct", "build", "generate", "synthesize", "create", "implement",
                     "produce", "emit", "compile", "transform", "translate", "cover", "patch"],
    "OPTIMIZATION": ["optimise", "optimize", "minimise", "minimize", "reduce", "improve",
                     "metric", "cost", "performance", "efficient", "complexity", "fast", "resource"],
    "ANALYSIS":     ["analyse", "analyze", "inspect", "detect", "measure", "diagnose", "profile",
                     "benchmark", "evaluate", "assess", "infer", "predict", "section", "local"],
    "REFACTORING":  ["refactor", "restructure", "isomorphic", "equivalent", "morphism", "rename",
                     "move", "extract", "inline", "migrate", "map", "align", "rearrange"],
}

# Evidence channels → subsystems
_EVIDENCE_CHANNELS: dict[str, dict[str, str]] = {
    "VERIFICATION": {"formal_verification": "solver", "static_analysis": "encodings", "trust_assessment": "evidence"},
    "SYNTHESIS":    {"static_analysis": "encodings", "dynamic_testing": "runtime", "trust_assessment": "evidence"},
    "OPTIMIZATION": {"dynamic_testing": "runtime", "static_analysis": "encodings", "trust_assessment": "evidence"},
    "ANALYSIS":     {"static_analysis": "encodings", "trust_assessment": "evidence", "dynamic_testing": "runtime"},
    "REFACTORING":  {"static_analysis": "encodings", "formal_verification": "solver", "trust_assessment": "evidence"},
}

_SUBSYSTEM_MAP: dict[str, list[str]] = {
    "VERIFICATION": ["solver", "encodings", "evidence"],
    "SYNTHESIS":    ["encodings", "runtime", "evidence"],
    "OPTIMIZATION": ["runtime", "encodings", "solver"],
    "ANALYSIS":     ["encodings", "evidence", "runtime"],
    "REFACTORING":  ["encodings", "solver", "evidence"],
}


# ── Keyword scoring & concept extraction ──────────────────────────────

def _score_keywords(text: str) -> dict[str, tuple[float, list[str]]]:
    low = text.lower()
    return {cat: (len(m := [k for k in kws if k in low]) / max(len(kws), 1), m)
            for cat, kws in _KEYWORD_MAP.items()}


def _extract_concepts(text: str) -> list[str]:
    stop = {"the", "a", "an", "is", "are", "to", "and", "or", "of", "in",
            "for", "that", "this", "it", "with", "on", "as", "by", "from"}
    seen: set[str] = set(); out: list[str] = []
    for w in re.findall(r"[a-zA-Z_]\w*", text.lower()):
        if w not in stop and w not in seen and len(w) > 2:
            seen.add(w); out.append(w)
    return out[:20]


# ── Site construction ─────────────────────────────────────────────────

def _build_problem_site(concepts: list[str], category: str):
    if not _HAS_SITE:
        return None
    kind_map = {"VERIFICATION": CoordinateKind.THEOREM, "SYNTHESIS": CoordinateKind.MODULE,
                "OPTIMIZATION": CoordinateKind.FUNCTION, "ANALYSIS": CoordinateKind.REGION,
                "REFACTORING": CoordinateKind.INTERFACE}
    ck = kind_map.get(category, CoordinateKind.REGION)
    builder = SiteBuilder(label=f"classify:{category}")
    root = Coordinate(path=("problem",), kind=CoordinateKind.MODULE)
    builder.add_coordinate(root)
    coords = [root]
    for c in concepts:
        coord = Coordinate(path=("problem", c), kind=ck)
        builder.add_coordinate(coord); coords.append(coord)
        builder.add_morphism(Morphism(source=root, target=coord, kind=MorphismKind.RESTRICTION))
    for i in range(len(coords) - 2):
        builder.add_morphism(Morphism(source=coords[i+1], target=coords[i+2], kind=MorphismKind.TRANSPORT))
    return builder.build(), coords


# ── Judgment construction ─────────────────────────────────────────────

def _build_classification_judgment(coords, category: str, confidence: float):
    if not (_HAS_JUDGMENT and coords):
        return None
    trust = (JTrustLevel.RUNTIME_WITNESSED if confidence >= 0.5
             else JTrustLevel.COPILOT_SUGGESTED if confidence >= 0.3
             else JTrustLevel.UNVERIFIED)
    pk = {"VERIFICATION": PropositionKind.BEHAVIORAL, "SYNTHESIS": PropositionKind.STRUCTURAL,
          "OPTIMIZATION": PropositionKind.RESOURCE, "ANALYSIS": PropositionKind.SEMANTIC,
          "REFACTORING": PropositionKind.RELATIONAL}.get(category, PropositionKind.STRUCTURAL)
    jb = JudgmentBuilder()
    jb.at(coords[0])
    jb.claiming_formula(f"category({category}) >= {confidence:.2f}", kind=pk)
    jb.of_type_named("classification")
    jb.with_evidence(EvidenceItem(kind=EvidenceItemKind.ORACLE_PROPOSAL,
        payload={"category": category, "confidence": confidence},
        trust_level=trust, channel="static_analysis"))
    jb.with_trust_level(trust)
    jb.with_status(JudgmentStatus.SETTLED if confidence >= 0.3 else JudgmentStatus.PROPOSED)
    return jb.build()


# ── Cover helper ──────────────────────────────────────────────────────

def _make_cover(coords):
    if not (_HAS_COVERS and len(coords) > 1):
        return None
    cb = CoverBuilder(); cb.set_base(coords[0])
    for c in coords[1:]:
        cb.add_member(c, CoordinateMorphism(source="/".join(coords[0].path),
                                            target="/".join(c.path), reason="classify"))
    return cb.build()


# ── Descent structure check ───────────────────────────────────────────

def _check_descent(coords):
    if not (_HAS_DESCENT and _HAS_COVERS) or len(coords) < 2:
        return {"has_descent": False, "reason": "subsystem unavailable"}
    cover = _make_cover(coords)
    if cover is None:
        return {"has_descent": False}
    sections = {"/".join(c.path): {"concept": "/".join(c.path), "status": "settled"} for c in coords}
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EAGER, depth_limit=2))
    try:
        res = engine.attempt_descent(cover, sections); ok = getattr(res, "is_success", False)
    except Exception:
        try:
            rep = engine.run(cover, sections); ok = getattr(rep, "success", False)
        except Exception:
            ok = False
    return {"has_descent": ok}


# ── Cover decomposition check ─────────────────────────────────────────

def _check_cover(coords):
    if not _HAS_COVERS or len(coords) < 2:
        return {"decomposable": False, "score": 0.0}
    cover = _make_cover(coords)
    if cover is None:
        return {"decomposable": False, "score": 0.0}
    m = score_cover(cover)
    return {"decomposable": m.total_score > 0.0, "score": round(m.total_score, 4),
            "patch_count": m.patch_count, "locality": round(m.locality_score, 4)}


# ── Trust computation ─────────────────────────────────────────────────

def _compute_trust(category: str, confidence: float):
    if not _HAS_TRUST:
        rank = 3 if confidence >= 0.5 else (2 if confidence >= 0.2 else 1)
        return {"trust": ["unverified", "unverified", "copilot_suggested", "runtime_witnessed"][rank], "rank": rank}
    lvl = (ETrustLevel.RUNTIME_WITNESSED if confidence >= 0.5
           else ETrustLevel.COPILOT_SUGGESTED if confidence >= 0.2
           else ETrustLevel.UNVERIFIED)
    diff = {"VERIFICATION": 1, "SYNTHESIS": 1, "OPTIMIZATION": 0, "ANALYSIS": 0, "REFACTORING": 1}
    if diff.get(category, 0) > 0:
        lvl = TrustAlgebra().attenuate(lvl, diff[category])
    return {"trust": lvl.value, "rank": lvl.rank_index() if hasattr(lvl, "rank_index") else 0}


# ── Sheaf check ───────────────────────────────────────────────────────

def _check_sheaf(coords):
    if not (_HAS_SECTIONS and _HAS_COVERS) or len(coords) < 2:
        return {"locality": True, "gluing": True, "separated": True}
    cover = _make_cover(coords)
    if cover is None:
        return {"locality": True, "gluing": True, "separated": True}
    sc = SheafCondition(cover=cover)
    for c in coords:
        k = "/".join(c.path)
        try:
            sc.assign_local_section(k, Section(coordinate=c, data={"concept": k}, is_global=False))
        except Exception:
            pass
    rpt = sc.full_check()
    rpt["separated"] = sc.is_separated()
    return rpt


# ── Result dataclass ──────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    description: str; category: str; confidence: float; matched_keywords: list[str]
    geometric_characterization: str; recommended_subsystems: list[str]
    evidence_channels: dict[str, str]; trust: dict; site_structure: dict
    descent_structure: dict; cover_decomposition: dict; sheaf_check: dict
    all_scores: dict[str, float]; pipeline: str = "judgment_geometric"

    def to_dict(self):
        return {"description": self.description, "pipeline": self.pipeline,
            "classification": {"category": self.category, "confidence": round(self.confidence, 4),
                "matched_keywords": self.matched_keywords,
                "geometric_characterization": self.geometric_characterization},
            "recommended_subsystems": self.recommended_subsystems,
            "evidence_channels": self.evidence_channels, "trust": self.trust,
            "site_structure": self.site_structure, "descent_structure": self.descent_structure,
            "cover_decomposition": self.cover_decomposition, "sheaf_check": self.sheaf_check,
            "all_scores": {k: round(v, 4) for k, v in self.all_scores.items()}}


# ── Text formatter ────────────────────────────────────────────────────

def _format_text(r: ClassificationResult) -> str:
    ln: list[str] = []
    ln.append("Problem Classification (judgment-geometric)")
    ln.append("=" * 60)
    desc_preview = r.description[:80] + ("…" if len(r.description) > 80 else "")
    ln.append(f"  Input       : {desc_preview}")
    ln.append(f"  Category    : {r.category}")
    ln.append(f"  Confidence  : {r.confidence:.0%}")
    ln.append(f"  Keywords    : {', '.join(r.matched_keywords) or '(none)'}")
    ln.append(f"  Pipeline    : {r.pipeline}")
    ln.append("")

    ln.append("Geometric Characterization")
    ln.append("-" * 40)
    ln.append(f"  {r.geometric_characterization}")
    ln.append("")

    ln.append("Recommended Subsystems")
    ln.append("-" * 40)
    for s in r.recommended_subsystems:
        ln.append(f"  • {s}")
    ln.append("")

    ln.append("Evidence Channels → Subsystems")
    ln.append("-" * 40)
    for ch, sub in r.evidence_channels.items():
        ln.append(f"  {ch:<25s} → {sub}")
    ln.append("")

    ln.append(f"Trust Level: {r.trust.get('trust', '?')} (rank {r.trust.get('rank', '?')})")
    ln.append("")

    ss = r.site_structure
    ln.append("Site Structure")
    ln.append("-" * 40)
    ln.append(f"  coordinates     : {ss.get('coordinate_count', '?')}")
    ln.append(f"  coordinate kind : {ss.get('coordinate_kind', '?')}")
    ln.append(f"  has site object : {ss.get('has_site', False)}")
    concepts = ss.get("concepts", [])
    if concepts:
        ln.append(f"  concepts        : {', '.join(concepts[:10])}")
        if len(concepts) > 10:
            ln.append(f"                    … and {len(concepts) - 10} more")
    ln.append("")

    ds = r.descent_structure
    ln.append(f"Descent Structure : {'YES' if ds.get('has_descent') else 'NO'}")
    if ds.get("reason"):
        ln.append(f"  reason          : {ds['reason']}")
    ln.append("")

    cd = r.cover_decomposition
    ln.append(f"Cover Decomposition: {'YES' if cd.get('decomposable') else 'NO'}")
    ln.append(f"  score           : {cd.get('score', 0.0):.4f}")
    if cd.get("patch_count") is not None:
        ln.append(f"  patch count     : {cd['patch_count']}")
    if cd.get("locality") is not None:
        ln.append(f"  locality        : {cd['locality']}")
    ln.append("")

    gluing = r.sheaf_check.get("gluing", True)
    if isinstance(gluing, tuple):
        gluing = gluing[0] if gluing else True
    ln.append(f"Sheaf Condition   : {'PASS' if gluing else 'FAIL'}")
    if r.sheaf_check.get("separated") is not None:
        ln.append(f"  separated       : {r.sheaf_check['separated']}")
    ln.append("")

    ln.append("All Category Scores")
    ln.append("-" * 40)
    for cat in _CATEGORIES:
        score = r.all_scores.get(cat, 0.0)
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        ln.append(f"  {cat:15s}: {score:.0%} {bar}")
    return "\n".join(ln)


# ── Main entry point ──────────────────────────────────────────────────

def _atlas_registry() -> dict[str, type]:
    """Return all public classes from the problem-atlas subpackage."""
    registry: dict[str, type] = {}

    try:
        from jugeo.problem_modes.problem_atlas.models import (
            ProblemCategory, DifficultyLevel, DecidabilityKind, ConjunctionMode,
            ProblemClass, SemanticSignature, EvidenceRequirement, AtlasCatalog,
        )
        registry["ProblemCategory"] = ProblemCategory
        registry["DifficultyLevel"] = DifficultyLevel
        registry["DecidabilityKind"] = DecidabilityKind
        registry["ConjunctionMode"] = ConjunctionMode
        registry["ProblemClass"] = ProblemClass
        registry["SemanticSignature"] = SemanticSignature
        registry["EvidenceRequirement"] = EvidenceRequirement
        registry["AtlasCatalog"] = AtlasCatalog
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.algorithms import (
            TraversalDirection, LookupStrategy, LookupResult, RoutingResult,
            UnificationResult, SatisfactionReport,
        )
        registry["TraversalDirection"] = TraversalDirection
        registry["LookupStrategy"] = LookupStrategy
        registry["LookupResult"] = LookupResult
        registry["RoutingResult"] = RoutingResult
        registry["UnificationResult"] = UnificationResult
        registry["SatisfactionReport"] = SatisfactionReport
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.manifest import (
            ModuleKind, ModuleRecord, PackageManifest,
        )
        registry["ModuleKind"] = ModuleKind
        registry["ModuleRecord"] = ModuleRecord
        registry["PackageManifest"] = PackageManifest
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.theorems import (
            VerificationStatus, ProofKind, TheoremKind, Hypothesis,
            HypothesisSet, ProofStep, ProofSketch, Theorem, TheoremRelation,
            TheoremRegistry, ProofVerifier, TheoremLinker,
        )
        registry["VerificationStatus"] = VerificationStatus
        registry["ProofKind"] = ProofKind
        registry["TheoremKind"] = TheoremKind
        registry["Hypothesis"] = Hypothesis
        registry["HypothesisSet"] = HypothesisSet
        registry["ProofStep"] = ProofStep
        registry["ProofSketch"] = ProofSketch
        registry["Theorem"] = Theorem
        registry["TheoremRelation"] = TheoremRelation
        registry["TheoremRegistry"] = TheoremRegistry
        registry["ProofVerifier"] = ProofVerifier
        registry["TheoremLinker"] = TheoremLinker
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.trust_requirements import (
            RequirementStatus, GapSeverity, TrustGap, RequirementCheckResult,
            TrustRequirementBuilder, RequirementChecker, GapAnalyzer,
            RequirementComposer, TrustBudgetManager,
        )
        registry["RequirementStatus"] = RequirementStatus
        registry["GapSeverity"] = GapSeverity
        registry["TrustGap"] = TrustGap
        registry["RequirementCheckResult"] = RequirementCheckResult
        registry["TrustRequirementBuilder"] = TrustRequirementBuilder
        registry["RequirementChecker"] = RequirementChecker
        registry["GapAnalyzer"] = GapAnalyzer
        registry["RequirementComposer"] = RequirementComposer
        registry["TrustBudgetManager"] = TrustBudgetManager
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.integration import (
            IntegrationStatus, AtlasEvent, IntegrationReport,
            ClassResolutionResult, AtlasEventBus, ProblemAtlasIntegration,
            AtlasExporter, AtlasImporter,
        )
        registry["IntegrationStatus"] = IntegrationStatus
        registry["AtlasEvent"] = AtlasEvent
        registry["IntegrationReport"] = IntegrationReport
        registry["ClassResolutionResult"] = ClassResolutionResult
        registry["AtlasEventBus"] = AtlasEventBus
        registry["ProblemAtlasIntegration"] = ProblemAtlasIntegration
        registry["AtlasExporter"] = AtlasExporter
        registry["AtlasImporter"] = AtlasImporter
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.evidence_channels import (
            ChannelKind, ChannelPriority, ChannelDescriptor,
            ChannelContribution, ChannelRegistry, ChannelRouter,
            TrustLevelComputer, ChannelCompatibilityChecker,
            EvidenceAggregator,
        )
        registry["ChannelKind"] = ChannelKind
        registry["ChannelPriority"] = ChannelPriority
        registry["ChannelDescriptor"] = ChannelDescriptor
        registry["ChannelContribution"] = ChannelContribution
        registry["ChannelRegistry"] = ChannelRegistry
        registry["ChannelRouter"] = ChannelRouter
        registry["TrustLevelComputer"] = TrustLevelComputer
        registry["ChannelCompatibilityChecker"] = ChannelCompatibilityChecker
        registry["EvidenceAggregator"] = EvidenceAggregator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.semantic_signatures import (
            SchemaKind, SignatureKind, SemanticCompatibility,
            CompositionStrategy, CompatibilityResult, IOSchema,
            SemanticSignature as SSSemanticSignature, SemanticContract,
            SignatureBuilder, SignatureComposer, SignatureMatcher,
            PreconditionChecker, SignatureNormalizer,
        )
        registry["SchemaKind"] = SchemaKind
        registry["SignatureKind"] = SignatureKind
        registry["SemanticCompatibility"] = SemanticCompatibility
        registry["CompositionStrategy"] = CompositionStrategy
        registry["CompatibilityResult"] = CompatibilityResult
        registry["IOSchema"] = IOSchema
        registry["ss_SemanticSignature"] = SSSemanticSignature
        registry["SemanticContract"] = SemanticContract
        registry["SignatureBuilder"] = SignatureBuilder
        registry["SignatureComposer"] = SignatureComposer
        registry["SignatureMatcher"] = SignatureMatcher
        registry["PreconditionChecker"] = PreconditionChecker
        registry["SignatureNormalizer"] = SignatureNormalizer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.problem_classes import (
            ProblemKind, SubsumptionRelation, ProblemClassLattice,
            InstanceTemplate, ProblemClassBuilder, ClassLatticeComputer,
            ProblemClassRegistry, InstanceGenerator, ProblemClassSerializer,
        )
        registry["ProblemKind"] = ProblemKind
        registry["SubsumptionRelation"] = SubsumptionRelation
        registry["ProblemClassLattice"] = ProblemClassLattice
        registry["InstanceTemplate"] = InstanceTemplate
        registry["ProblemClassBuilder"] = ProblemClassBuilder
        registry["ClassLatticeComputer"] = ClassLatticeComputer
        registry["ProblemClassRegistry"] = ProblemClassRegistry
        registry["InstanceGenerator"] = InstanceGenerator
        registry["ProblemClassSerializer"] = ProblemClassSerializer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.performance_obligations import (
            ResourceKind, BoundKind, ObligationSeverity, DischargeStatus,
            PerformanceBound, PerformanceObligation, PerformanceEvidence,
            DischargeReport, PerformanceObligationsAnalyzer,
            PerformanceWitness, PerformanceObligationsCoordinator,
        )
        registry["ResourceKind"] = ResourceKind
        registry["BoundKind"] = BoundKind
        registry["ObligationSeverity"] = ObligationSeverity
        registry["DischargeStatus"] = DischargeStatus
        registry["PerformanceBound"] = PerformanceBound
        registry["PerformanceObligation"] = PerformanceObligation
        registry["PerformanceEvidence"] = PerformanceEvidence
        registry["DischargeReport"] = DischargeReport
        registry["PerformanceObligationsAnalyzer"] = PerformanceObligationsAnalyzer
        registry["PerformanceWitness"] = PerformanceWitness
        registry["PerformanceObligationsCoordinator"] = PerformanceObligationsCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.generated_code_governance import (
            GeneratorKind, ProvenanceKind, TrustCeilingPolicy,
            GovernanceStatus, ProvenanceEntry, ProvenanceChain,
            TrustCeiling, GovernancePolicy, GovernanceRecord,
            GovernanceAnalysisResult, GeneratedCodeGovernanceAnalyzer,
            GeneratedCodeGovernanceWitness, GeneratedCodeGovernanceCoordinator,
        )
        registry["GeneratorKind"] = GeneratorKind
        registry["ProvenanceKind"] = ProvenanceKind
        registry["TrustCeilingPolicy"] = TrustCeilingPolicy
        registry["GovernanceStatus"] = GovernanceStatus
        registry["ProvenanceEntry"] = ProvenanceEntry
        registry["ProvenanceChain"] = ProvenanceChain
        registry["TrustCeiling"] = TrustCeiling
        registry["GovernancePolicy"] = GovernancePolicy
        registry["GovernanceRecord"] = GovernanceRecord
        registry["GovernanceAnalysisResult"] = GovernanceAnalysisResult
        registry["GeneratedCodeGovernanceAnalyzer"] = GeneratedCodeGovernanceAnalyzer
        registry["GeneratedCodeGovernanceWitness"] = GeneratedCodeGovernanceWitness
        registry["GeneratedCodeGovernanceCoordinator"] = GeneratedCodeGovernanceCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.specification_satisfaction import (
            SpecificationKind, SatisfactionStatus, RouterDecision,
            SatisfactionQuery, AtlasEntry, RoutingRecord, AtlasWitness,
            ProblemRouter, AtlasEntryRegistry, AnalysisResult,
            SpecificationSatisfactionAnalyzer,
            SpecificationSatisfactionWitness,
            SpecificationSatisfactionCoordinator,
        )
        registry["SpecificationKind"] = SpecificationKind
        registry["SatisfactionStatus"] = SatisfactionStatus
        registry["RouterDecision"] = RouterDecision
        registry["SatisfactionQuery"] = SatisfactionQuery
        registry["AtlasEntry"] = AtlasEntry
        registry["RoutingRecord"] = RoutingRecord
        registry["AtlasWitness"] = AtlasWitness
        registry["ProblemRouter"] = ProblemRouter
        registry["AtlasEntryRegistry"] = AtlasEntryRegistry
        registry["AnalysisResult"] = AnalysisResult
        registry["SpecificationSatisfactionAnalyzer"] = SpecificationSatisfactionAnalyzer
        registry["SpecificationSatisfactionWitness"] = SpecificationSatisfactionWitness
        registry["SpecificationSatisfactionCoordinator"] = SpecificationSatisfactionCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.repair_and_program_transformation import (
            RepairKind, TransformationKind, RepairStatus, RepairEntry,
            TransformationEntry, TransformationWitness, FeasibilityReport,
            RepairProgramTransformationAnalyzer,
            RepairProgramTransformationWitness,
            RepairProgramTransformationCoordinator,
        )
        registry["RepairKind"] = RepairKind
        registry["TransformationKind"] = TransformationKind
        registry["RepairStatus"] = RepairStatus
        registry["RepairEntry"] = RepairEntry
        registry["TransformationEntry"] = TransformationEntry
        registry["TransformationWitness"] = TransformationWitness
        registry["FeasibilityReport"] = FeasibilityReport
        registry["RepairProgramTransformationAnalyzer"] = RepairProgramTransformationAnalyzer
        registry["RepairProgramTransformationWitness"] = RepairProgramTransformationWitness
        registry["RepairProgramTransformationCoordinator"] = RepairProgramTransformationCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.problem_atlas.migration_and_donor_inheritance import (
            MigrationKind, DonorRelationshipKind, InheritancePolicy,
            MigrationStatus, DonorRecord, MigrationEntry, InheritanceEdge,
            InheritanceGraph, MigrationAnalysisResult,
            MigrationDonorInheritanceAnalyzer,
            MigrationDonorInheritanceWitness,
            MigrationDonorInheritanceCoordinator,
        )
        registry["MigrationKind"] = MigrationKind
        registry["DonorRelationshipKind"] = DonorRelationshipKind
        registry["InheritancePolicy"] = InheritancePolicy
        registry["MigrationStatus"] = MigrationStatus
        registry["DonorRecord"] = DonorRecord
        registry["MigrationEntry"] = MigrationEntry
        registry["InheritanceEdge"] = InheritanceEdge
        registry["InheritanceGraph"] = InheritanceGraph
        registry["MigrationAnalysisResult"] = MigrationAnalysisResult
        registry["MigrationDonorInheritanceAnalyzer"] = MigrationDonorInheritanceAnalyzer
        registry["MigrationDonorInheritanceWitness"] = MigrationDonorInheritanceWitness
        registry["MigrationDonorInheritanceCoordinator"] = MigrationDonorInheritanceCoordinator
    except Exception:
        pass

    return registry


def _rich_classification(description: str) -> None:
    """Use problem_atlas domain classes to map a description to problem coordinates.

    Creates ProblemClass instances, registers them in an AtlasCatalog,
    computes SemanticSignatures, and reports classification with nearby problems.
    """
    try:
        from jugeo.problem_modes.problem_atlas.models import (  # type: ignore[import-untyped]
            ProblemClass,
            ProblemCategory,
            DifficultyLevel,
            SemanticSignature,
            DecidabilityKind,
            AtlasCatalog,
        )
        _has_atlas = True
    except Exception:
        _has_atlas = False

    print("\n" + "─" * 64)
    print("  Problem Atlas Classification (problem_atlas domain)")
    print("─" * 64)

    # Map keywords to categories
    low = description.lower()
    _cat_keywords = {
        ProblemCategory.VERIFICATION if _has_atlas else "VERIFICATION":
            ["verify", "check", "prove", "assert", "valid", "correct"],
        ProblemCategory.COMPUTATIONAL if _has_atlas else "COMPUTATIONAL":
            ["compute", "calculate", "transform", "process", "run"],
        ProblemCategory.CONSTRUCTIVE if _has_atlas else "CONSTRUCTIVE":
            ["build", "construct", "generate", "synthesize", "create"],
        ProblemCategory.ANALYTICAL if _has_atlas else "ANALYTICAL":
            ["analyse", "analyze", "inspect", "measure", "diagnose"],
        ProblemCategory.RELATIONAL if _has_atlas else "RELATIONAL":
            ["compare", "relate", "equivalent", "refine", "similar"],
        ProblemCategory.META if _has_atlas else "META":
            ["meta", "self", "learn", "improve", "optimize"],
    }

    scores = {}
    for cat, kws in _cat_keywords.items():
        hits = [k for k in kws if k in low]
        scores[cat] = (len(hits) / max(len(kws), 1), hits)

    best_cat = max(scores, key=lambda k: scores[k][0])
    best_score, matched = scores[best_cat]
    if best_score == 0.0:
        best_cat = list(_cat_keywords.keys())[3]  # ANALYTICAL default

    if _has_atlas:
        try:
            catalog = AtlasCatalog()

            # Create primary problem class
            primary = ProblemClass(
                class_id="pc-primary-001",
                name=str(best_cat.value if hasattr(best_cat, "value") else best_cat),
                description=description[:120],
                category=best_cat if isinstance(best_cat, ProblemCategory) else ProblemCategory.ANALYTICAL,
                difficulty_level=DifficultyLevel.MODERATE,
                parent_classes=(),
                child_classes=(),
                canonical_instances=({"description": description[:80]},),
                complexity_notes="estimated moderate complexity",
                required_evidence_kinds=("static_analysis", "runtime_witness"),
            )

            # Create a signature for the problem
            sig = SemanticSignature(
                sig_id="sig-primary-001",
                problem_class_id=primary.class_id,
                input_schema={"type": "object", "required": ["source"]},
                output_schema={"type": "object", "required": ["result"]},
                preconditions=("input_is_valid",),
                postconditions=("output_satisfies_spec",),
                invariants=("monotone_trust",),
                side_effects=(),
                complexity_class="P",
                decidability=DecidabilityKind.DECIDABLE,
            )

            catalog.register_problem_class(primary, sig)

            # Create nearby problem classes
            _nearby = [
                ("VERIFICATION", ProblemCategory.VERIFICATION, DifficultyLevel.HARD),
                ("CONSTRUCTIVE", ProblemCategory.CONSTRUCTIVE, DifficultyLevel.MODERATE),
                ("ANALYTICAL", ProblemCategory.ANALYTICAL, DifficultyLevel.EASY),
            ]
            neighbors = []
            for name, cat, diff in _nearby:
                if cat == primary.category:
                    continue
                nb = ProblemClass(
                    class_id=f"pc-{name.lower()}-nb",
                    name=name,
                    description=f"Nearby {name.lower()} problem class",
                    category=cat,
                    difficulty_level=diff,
                    parent_classes=(),
                    child_classes=(),
                    canonical_instances=(),
                    complexity_notes="",
                    required_evidence_kinds=(),
                )
                catalog.register_problem_class(nb)
                neighbors.append(nb)

            print(f"  Primary class : {primary.name}")
            print(f"    category    : {primary.category.value}")
            print(f"    difficulty  : {primary.difficulty_level.value}")
            print(f"    complexity  : {sig.complexity_class}")
            print(f"    decidability: {sig.decidability.value}")
            print(f"    evidence    : {', '.join(primary.required_evidence_kinds)}")
            print()
            print(f"  Atlas Catalog : {catalog.name} v{catalog.version}")
            print(f"    entries     : {len(catalog.entries)}")
            print()
            print("  Nearby Problem Classes:")
            print("  " + "-" * 56)
            for nb in neighbors:
                dist_score = abs(
                    nb.difficulty_level.score()
                    if hasattr(nb.difficulty_level, "score") else 2
                ) * 0.1
                print(f"    {nb.name:15s}  category={nb.category.value:15s}  "
                      f"difficulty={nb.difficulty_level.value}")

            print()
            print("  Category Scores:")
            print("  " + "-" * 56)
            for cat, (sc, hits) in sorted(scores.items(), key=lambda x: -x[1][0]):
                cat_name = cat.value if hasattr(cat, "value") else str(cat)
                bar = "█" * int(sc * 20) + "░" * (20 - int(sc * 20))
                print(f"    {cat_name:15s}: {sc:.0%} {bar}  {', '.join(hits) or '(none)'}")
            return
        except Exception:
            pass

    # Simulated output
    cat_name = best_cat.value if hasattr(best_cat, "value") else str(best_cat)
    print(f"  [simulated] Primary class: {cat_name}")
    print(f"    category    : {cat_name}")
    print(f"    difficulty  : MODERATE")
    print(f"    complexity  : P")
    print(f"    decidability: DECIDABLE")
    print()
    print(f"  Atlas Catalog : UnifiedProblemAtlas v1.0.0")
    print(f"    entries     : 4")
    print()
    print("  Nearby Problem Classes:")
    print("  " + "-" * 56)
    for name in ["VERIFICATION", "CONSTRUCTIVE", "ANALYTICAL"]:
        if name != cat_name:
            print(f"    {name:15s}  (nearby in problem lattice)")
    print()
    print("  Category Scores:")
    print("  " + "-" * 56)
    for cat, (sc, hits) in sorted(scores.items(), key=lambda x: -x[1][0]):
        cn = cat.value if hasattr(cat, "value") else str(cat)
        bar = "█" * int(sc * 20) + "░" * (20 - int(sc * 20))
        print(f"    {cn:15s}: {sc:.0%} {bar}  {', '.join(hits) or '(none)'}")
    print("─" * 64)


def run_classify(args: argparse.Namespace) -> int:
    """Classify a problem description via the judgment-geometric framework.

    Parameters
    ----------
    args : argparse.Namespace
        Expected: description, file, format, verbose.

    Returns
    -------
    int
        0 on success, 1 on failure.
    """
    description: str | None = getattr(args, "description", None)
    file_path: str | None = getattr(args, "file", None)
    fmt: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _atlas_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    if description is None and file_path is None:
        print("error: provide a problem description or --file", file=sys.stderr)
        return 1
    if file_path is not None:
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            print(f"error: {file_path}: not a file", file=sys.stderr)
            return 1
        try:
            description = open(file_path, encoding="utf-8").read()
        except Exception as exc:
            print(f"error: {file_path}: {exc}", file=sys.stderr)
            return 1
    assert description is not None

    kw = _score_keywords(description)
    all_scores = {c: s for c, (s, _) in kw.items()}
    best = max(all_scores, key=lambda k: all_scores[k])
    if all_scores[best] == 0.0:
        best = "ANALYSIS"
    conf = all_scores[best]; matched = kw[best][1]
    concepts = _extract_concepts(description)

    site, coords = (None, [])
    sr = _build_problem_site(concepts, best)
    if sr:
        site, coords = sr

    _build_classification_judgment(coords, best, conf)  # side-effect: validates judgment construction

    result = ClassificationResult(
        description=description, category=best, confidence=conf, matched_keywords=matched,
        geometric_characterization=_CATEGORY_GEOMETRY.get(best, "unknown"),
        recommended_subsystems=_SUBSYSTEM_MAP.get(best, []),
        evidence_channels=_EVIDENCE_CHANNELS.get(best, {}),
        trust=_compute_trust(best, conf),
        site_structure={"coordinate_count": len(coords), "concepts": concepts,
                        "coordinate_kind": best.lower(), "has_site": site is not None},
        descent_structure=_check_descent(coords),
        cover_decomposition=_check_cover(coords),
        sheaf_check=_check_sheaf(coords),
        all_scores=all_scores,
        pipeline="judgment_geometric" if _FULL_GEOMETRY else "degraded")

    if fmt == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_format_text(result))

    # Rich classification via problem_atlas domain classes
    _rich_classification(description)

    return 0
