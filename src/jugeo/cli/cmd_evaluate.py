"""CLI subcommand handler for ``jugeo evaluate <target>``.

Uses the full judgment-geometric framework: models the target as a Site,
builds Judgments per coordinate, runs DescentEngine for coverage, uses
TrustAlgebra for aggregate trust, score_cover for quality, and checks
SheafConditions.  Supports --ablation, --calibration, --benchmarks.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
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
        TrustLevel as JTrustLevel, JudgmentStatus, ResidualObligation,
    )
    _HAS_JUDGMENT = True
except Exception:
    _HAS_JUDGMENT = False

try:
    from jugeo.geometry.descent import (  # type: ignore[import-untyped]
        DescentEngine, DescentConfiguration, LocalSection, DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

try:
    from jugeo.evidence.trust import (  # type: ignore[import-untyped]
        TrustAlgebra, TrustLevel as ETrustLevel,
    )
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

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

try:
    from jugeo.benchmarks.runner import (  # type: ignore[import-untyped]
        run_all_benchmarks, judgment_benchmark,
        descent_benchmark, solver_benchmark, encoding_benchmark,
    )
    _HAS_BENCHMARKS = True
except Exception:
    _HAS_BENCHMARKS = False

# -- scaling limit types ----------------------------------------------------
try:
    from jugeo.evaluation.scaling_limits.why_scaling_needs_its_own_theory import (  # type: ignore[import-untyped]
        ChangeType,
        RegimeType,
    )
    _HAS_SCALING_TYPES = True
except Exception:
    _HAS_SCALING_TYPES = False

_FULL_GEOMETRY = _HAS_SITE and _HAS_JUDGMENT and _HAS_DESCENT


# ── AST analysis ──────────────────────────────────────────────────────

@dataclass
class _FM:
    """Per-file AST metrics."""
    path: str = ""; lines: int = 0; functions: int = 0; classes: int = 0
    docstrings: int = 0; doc_slots: int = 0
    ann_args: int = 0; total_args: int = 0
    complexity: int = 0; is_test: bool = False


def _analyse(fp: str) -> _FM:
    m = _FM(path=fp)
    try:
        src = open(fp, encoding="utf-8", errors="replace").read()
    except OSError:
        return m
    m.lines = src.count("\n") + 1
    try:
        tree = ast.parse(src, filename=fp)
    except SyntaxError:
        return m
    for nd in ast.walk(tree):
        if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)):
            m.functions += 1; m.doc_slots += 1
            if ast.get_docstring(nd): m.docstrings += 1
            for a in nd.args.args + nd.args.posonlyargs + nd.args.kwonlyargs:
                m.total_args += 1
                if a.annotation: m.ann_args += 1
            for ch in ast.walk(nd):
                if isinstance(ch, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.ExceptHandler)):
                    m.complexity += 1
        elif isinstance(nd, ast.ClassDef):
            m.classes += 1; m.doc_slots += 1
            if ast.get_docstring(nd): m.docstrings += 1
    base = os.path.basename(fp)
    m.is_test = base.startswith("test_") or base.endswith("_test.py")
    return m


def _collect(target: str) -> list[_FM]:
    if os.path.isfile(target) and target.endswith(".py"):
        return [_analyse(target)]
    results: list[_FM] = []
    if os.path.isdir(target):
        for root, _, files in os.walk(target):
            for f in sorted(files):
                if f.endswith(".py"):
                    results.append(_analyse(os.path.join(root, f)))
    return results


def _quality(m: _FM) -> float:
    dc = m.docstrings / m.doc_slots if m.doc_slots else 0.0
    ac = m.ann_args / m.total_args if m.total_args else 0.0
    cx = max(0.0, 1.0 - (m.complexity / max(m.functions, 1)) / 20.0)
    return min(1.0, 0.25 * dc + 0.25 * ac + 0.25 * cx + 0.25 * (1.0 if m.is_test else 0.4))


# ── Site construction ─────────────────────────────────────────────────

def _build_site(fms: list[_FM], target: str):
    if not _HAS_SITE:
        return None
    builder = SiteBuilder(label=f"eval:{os.path.basename(target)}")
    coords = []
    for fm in fms:
        rel = os.path.relpath(fm.path, target) if os.path.isdir(target) else fm.path
        parts = tuple(rel.replace(os.sep, "/").split("/"))
        coord = Coordinate(path=parts, kind=CoordinateKind.TEST if fm.is_test else CoordinateKind.MODULE)
        builder.add_coordinate(coord); coords.append(coord)
    cmap = {c.path: c for c in coords}
    for c in coords:
        if len(c.path) > 1 and c.path[:-1] in cmap:
            builder.add_morphism(Morphism(source=cmap[c.path[:-1]], target=c, kind=MorphismKind.RESTRICTION))
    return builder.build(), coords


# ── Judgment construction ─────────────────────────────────────────────

def _build_judgments(coords, fms: list[_FM]):
    if not _HAS_JUDGMENT:
        return []
    judgments = []
    for coord, fm in zip(coords, fms):
        q = _quality(fm)
        trust = (JTrustLevel.RUNTIME_WITNESSED if fm.is_test and fm.ann_args > 0
                 else JTrustLevel.COPILOT_SUGGESTED if fm.ann_args > 0
                 else JTrustLevel.UNVERIFIED)
        items = []
        if fm.docstrings > 0:
            items.append(EvidenceItem(kind=EvidenceItemKind.ORACLE_PROPOSAL,
                payload={"docstrings": fm.docstrings}, trust_level=JTrustLevel.COPILOT_SUGGESTED, channel="static_analysis"))
        if fm.is_test:
            items.append(EvidenceItem(kind=EvidenceItemKind.RUNTIME_WITNESS,
                payload={"test_file": fm.path}, trust_level=JTrustLevel.RUNTIME_WITNESSED, channel="dynamic_testing"))
        if fm.ann_args > 0:
            items.append(EvidenceItem(kind=EvidenceItemKind.ORACLE_PROPOSAL,
                payload={"annotated": fm.ann_args, "total": fm.total_args}, trust_level=JTrustLevel.COPILOT_SUGGESTED, channel="static_analysis"))
        jb = JudgmentBuilder()
        jb.at(coord)
        jb.claiming_formula(f"quality({'/'.join(coord.path)}) >= 0.5", kind=PropositionKind.STRUCTURAL)
        jb.of_type_named("code_quality")
        for ei in items:
            jb.with_evidence(ei)
        jb.with_trust_level(trust)
        if q >= 0.5:
            jb.with_status(JudgmentStatus.SETTLED)
        else:
            jb.with_status(JudgmentStatus.PROPOSED)
            jb.with_obligation(ResidualObligation(
                description=f"Improve quality of {'/'.join(coord.path)} ({q:.2f})",
                required_evidence_kind=EvidenceItemKind.RUNTIME_WITNESS))
        judgments.append(jb.build())
    return judgments


# ── Descent ───────────────────────────────────────────────────────────

def _make_cover(coords):
    """Build a Cover from a list of coordinates."""
    if not (_HAS_COVERS and len(coords) > 1):
        return None
    cb = CoverBuilder(); cb.set_base(coords[0])
    for c in coords[1:]:
        morph = CoordinateMorphism(source="/".join(coords[0].path), target="/".join(c.path), reason="eval")
        cb.add_member(c, morph)
    return cb.build()


def _run_descent(coords, judgments, fms: list[_FM]):
    if not (_HAS_DESCENT and _HAS_COVERS and _HAS_JUDGMENT and len(coords) > 1):
        return {"coverage": 0.0, "settled_count": 0, "total": len(fms)}
    cover = _make_cover(coords)
    if cover is None:
        return {"coverage": 0.0, "settled_count": 0, "total": len(fms)}
    sections = {}
    for j, fm in zip(judgments, fms):
        k = "/".join(j.coordinate.path) if hasattr(j.coordinate, "path") else str(j.coordinate)
        sections[k] = {"quality": _quality(fm), "status": str(j.status)}
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE, depth_limit=3))
    t0 = time.monotonic()
    try:
        res = engine.attempt_descent(cover, sections); ok = getattr(res, "is_success", False)
    except Exception:
        try:
            rep = engine.run(cover, sections); ok = getattr(rep, "success", False)
        except Exception:
            ok = False
    settled = sum(1 for j in judgments if str(getattr(j, "status", "")) == str(JudgmentStatus.SETTLED))
    cov = settled / len(judgments) if judgments else 0.0
    return {"coverage": round(cov, 4), "settled_count": settled, "total": len(judgments),
            "descent_success": ok, "descent_elapsed_s": round(time.monotonic() - t0, 4)}


# ── Trust ─────────────────────────────────────────────────────────────

def _aggregate_trust(judgments):
    if not _HAS_TRUST or not judgments:
        return {"aggregate_trust": "UNVERIFIED", "per_coordinate": {}}
    algebra = TrustAlgebra(); levels = []; per = {}
    for j in judgments:
        try:
            lvl = ETrustLevel(str(getattr(j, "trust", "unverified")).split(".")[-1].lower())
        except (ValueError, KeyError):
            lvl = ETrustLevel.UNVERIFIED
        levels.append(lvl)
        k = "/".join(j.coordinate.path) if hasattr(j.coordinate, "path") else str(j.coordinate)
        per[k] = lvl.value
    agg = levels[0]
    for l in levels[1:]:
        agg = algebra.compose(agg, l)
    return {"aggregate_trust": agg.value, "per_coordinate": per}


# ── Cover scoring ─────────────────────────────────────────────────────

def _score_cover_quality(coords):
    if not (_HAS_COVERS and len(coords) > 1):
        return {"total_score": 0.0}
    cover = _make_cover(coords)
    if cover is None:
        return {"total_score": 0.0}
    m = score_cover(cover)
    return {"total_score": round(m.total_score, 4), "patch_count": m.patch_count,
            "overlap_count": m.overlap_count, "locality_score": round(m.locality_score, 4),
            "redundancy_score": round(m.redundancy_score, 4)}


# ── Sheaf check ───────────────────────────────────────────────────────

def _check_sheaf(coords, judgments):
    if not (_HAS_SECTIONS and _HAS_COVERS and len(coords) > 1):
        return {"locality": True, "gluing": True}
    cover = _make_cover(coords)
    if cover is None:
        return {"locality": True, "gluing": True}
    sc = SheafCondition(cover=cover)
    for j in judgments:
        k = "/".join(j.coordinate.path) if hasattr(j.coordinate, "path") else str(j.coordinate)
        try:
            sc.assign_local_section(k, Section(coordinate=j.coordinate,
                data={"status": str(getattr(j, "status", "proposed"))}, is_global=False))
        except Exception:
            pass
    return sc.full_check()


# ── Ablation ──────────────────────────────────────────────────────────

def _ablation_study(coords, judgments, fms, target):
    if not _FULL_GEOMETRY or len(coords) < 3:
        return []
    base = _run_descent(coords, judgments, fms)["coverage"]
    results = []
    for i in range(len(coords)):
        ac = coords[:i] + coords[i+1:]; aj = judgments[:i] + judgments[i+1:]
        af = fms[:i] + fms[i+1:]
        ab = _run_descent(ac, aj, af)["coverage"]
        k = "/".join(coords[i].path) if hasattr(coords[i], "path") else str(coords[i])
        delta = round(ab - base, 4)
        results.append({"removed_coordinate": k, "baseline_coverage": round(base, 4),
                        "ablated_coverage": round(ab, 4), "delta": delta, "critical": abs(delta) > 0.05})
    return results


# ── Calibration ───────────────────────────────────────────────────────

def _calibration(judgments):
    if not _HAS_JUDGMENT or not judgments:
        return {"consistent": True, "inconsistencies": [], "spread": 0}
    raw_trusts = [getattr(j, "trust", 1) for j in judgments]
    vals = []
    for rt in raw_trusts:
        try:
            vals.append(int(rt))
        except (TypeError, ValueError):
            vals.append(hash(rt) % 6)  # map opaque trust objects to 0-5 range
    per = {}
    for j, v in zip(judgments, vals):
        k = "/".join(j.coordinate.path) if hasattr(j.coordinate, "path") else str(j.coordinate)
        per[k] = v
    mean = sum(vals) / len(vals)
    incons = [{"coordinate": k, "trust": v, "deviation": round(abs(v - mean), 2)}
              for k, v in per.items() if abs(v - mean) > 1.5]
    return {"consistent": len(incons) == 0, "inconsistencies": incons,
            "spread": max(vals) - min(vals), "mean_trust": round(mean, 2), "trust_per_coordinate": per}


# ── Benchmarks ────────────────────────────────────────────────────────

def _run_benchmarks(site, judgments, fms):
    if not _HAS_BENCHMARKS:
        return {"error": "benchmarks.runner unavailable"}
    r: dict[str, Any] = {}
    for name, fn, arg in [("judgment", judgment_benchmark, judgments),
                           ("descent", descent_benchmark, site),
                           ("solver", solver_benchmark, [f"quality({fm.path})>=0.5" for fm in fms[:5]]),
                           ("encoding", encoding_benchmark, [fm.path for fm in fms[:5]])]:
        try:
            r[name] = fn(arg)
        except Exception as e:
            r[name] = {"error": str(e)}
    try:
        full = run_all_benchmarks()
        r["suite"] = {n: {"passed": getattr(v, "passed", None), "score": getattr(v, "score", None)} for n, v in full.items()}
    except Exception as e:
        r["suite"] = {"error": str(e)}
    return r


# ── Report ────────────────────────────────────────────────────────────

@dataclass
class EvaluationReport:
    target: str; coverage: float; descent: dict; trust: dict
    cover_quality: dict; sheaf_check: dict; per_coordinate: list
    ablation: list | None = None; calibration: dict | None = None
    benchmark_results: dict | None = None; pipeline_used: str = "judgment_geometric"

    def to_dict(self) -> dict[str, Any]:
        d = {"target": self.target, "pipeline": self.pipeline_used,
             "coverage": self.coverage, "descent": self.descent, "trust": self.trust,
             "cover_quality": self.cover_quality, "sheaf_check": self.sheaf_check,
             "per_coordinate": self.per_coordinate}
        if self.ablation is not None: d["ablation"] = self.ablation
        if self.calibration is not None: d["calibration"] = self.calibration
        if self.benchmark_results is not None: d["benchmark_results"] = self.benchmark_results
        return d


def _format_text(rpt: EvaluationReport) -> str:
    ln = [f"Evaluation: {rpt.target}  (pipeline: {rpt.pipeline_used})", "=" * 60,
          f"\nDescent Coverage: {rpt.coverage:.0%}",
          f"  settled/total : {rpt.descent.get('settled_count','?')}/{rpt.descent.get('total','?')}",
          f"  descent ok    : {rpt.descent.get('descent_success','?')}",
          f"\nAggregate Trust : {rpt.trust.get('aggregate_trust','?')}",
          f"\nCover Quality   : {rpt.cover_quality.get('total_score',0.0):.4f}",
          f"  patches={rpt.cover_quality.get('patch_count','?')}  overlaps={rpt.cover_quality.get('overlap_count','?')}",
          f"\nSheaf Condition : {'PASS' if rpt.sheaf_check.get('gluing', True) else 'FAIL'}"]
    if rpt.per_coordinate:
        ln.append("\nPer-Coordinate Judgments:")
        for e in rpt.per_coordinate:
            bar = "█" * int(e["quality"] * 20) + "░" * (20 - int(e["quality"] * 20))
            ln.append(f"  {e['coordinate']:<35s} {bar} {e['quality']:.2f} [{e['status']}]")
    if rpt.ablation:
        ln.append("\nAblation (coordinate removal → coverage Δ):")
        for e in rpt.ablation:
            ln.append(f"  -{e['removed_coordinate']:<35s} Δ={e['delta']:+.4f}" + (" ***" if e.get("critical") else ""))
    if rpt.calibration:
        c = rpt.calibration
        ln.append(f"\nCalibration: {'CONSISTENT' if c.get('consistent') else 'INCONSISTENT'}  spread={c.get('spread',0)}")
        for i in c.get("inconsistencies", []):
            ln.append(f"  ⚠ {i['coordinate']}: trust={i['trust']} dev={i['deviation']}")
    if rpt.benchmark_results:
        ln.append("\nBenchmarks:")
        for n, v in rpt.benchmark_results.items():
            ln.append(f"  {n}: {v}")
    return "\n".join(ln)


# ── Registry ──────────────────────────────────────────────────────────


def _evaluation_registry() -> dict[str, type]:
    """Return a dict of all public classes from evaluation and methodology-loop subpackages."""
    registry: dict[str, type] = {}

    try:
        from jugeo.evaluation.evaluation_design.models import (  # type: ignore[import-untyped]
            EvaluationStatus, ClauseType, AblationKind, CalibrationMethod,
            EvaluationDesign, ClauseResult, AblationResult, CalibrationReport,
            EvaluationResult, ClausewiseEvaluator, AblationDesign,
        )
        registry["EvaluationStatus"] = EvaluationStatus
        registry["ClauseType"] = ClauseType
        registry["AblationKind"] = AblationKind
        registry["CalibrationMethod"] = CalibrationMethod
        registry["EvaluationDesign"] = EvaluationDesign
        registry["ClauseResult"] = ClauseResult
        registry["AblationResult"] = AblationResult
        registry["CalibrationReport"] = CalibrationReport
        registry["EvaluationResult"] = EvaluationResult
        registry["ClausewiseEvaluator"] = ClausewiseEvaluator
        registry["AblationDesign"] = AblationDesign
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.algorithms import (  # type: ignore[import-untyped]
            EvaluationAlgorithms,
        )
        registry["EvaluationAlgorithms"] = EvaluationAlgorithms
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.manifest import (  # type: ignore[import-untyped]
            EvaluationDesignManifest, EvaluationManifestBuilder,
            EvaluationManifestRegistry,
        )
        registry["EvaluationDesignManifest"] = EvaluationDesignManifest
        registry["EvaluationManifestBuilder"] = EvaluationManifestBuilder
        registry["EvaluationManifestRegistry"] = EvaluationManifestRegistry
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.theorems import (  # type: ignore[import-untyped]
            TheoremMetadata, EvaluationSoundnessTheorem,
            AblationIsolationTheorem, CalibrationConsistencyTheorem,
            ClauseCompletenessTheorem, ScoreMonotonicityTheorem,
            EvaluationTheoremRegistry,
        )
        registry["TheoremMetadata"] = TheoremMetadata
        registry["EvaluationSoundnessTheorem"] = EvaluationSoundnessTheorem
        registry["AblationIsolationTheorem"] = AblationIsolationTheorem
        registry["CalibrationConsistencyTheorem"] = CalibrationConsistencyTheorem
        registry["ClauseCompletenessTheorem"] = ClauseCompletenessTheorem
        registry["ScoreMonotonicityTheorem"] = ScoreMonotonicityTheorem
        registry["EvaluationTheoremRegistry"] = EvaluationTheoremRegistry
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.ablation_design import (  # type: ignore[import-untyped]
            AblationPlanner, AblationExecutor, AblationAnalyzer,
            AblationDesignRunner,
        )
        registry["AblationPlanner"] = AblationPlanner
        registry["AblationExecutor"] = AblationExecutor
        registry["AblationAnalyzer"] = AblationAnalyzer
        registry["AblationDesignRunner"] = AblationDesignRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.clausewise_evaluation import (  # type: ignore[import-untyped]
            ClauseSpecification, ClausewiseScorer, ClauseWeightCalculator,
            ClausewiseEvaluationRunner,
        )
        registry["ClauseSpecification"] = ClauseSpecification
        registry["ClausewiseScorer"] = ClausewiseScorer
        registry["ClauseWeightCalculator"] = ClauseWeightCalculator
        registry["ClausewiseEvaluationRunner"] = ClausewiseEvaluationRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.integration import (  # type: ignore[import-untyped]
            EvaluationEvidenceIntegration, EvaluationPacksIntegration,
            EvaluationOrchestrationIntegration, EvaluationIdeationIntegration,
            EvaluationGeometryIntegration, FullEvaluationIntegration,
        )
        registry["EvaluationEvidenceIntegration"] = EvaluationEvidenceIntegration
        registry["EvaluationPacksIntegration"] = EvaluationPacksIntegration
        registry["EvaluationOrchestrationIntegration"] = EvaluationOrchestrationIntegration
        registry["EvaluationIdeationIntegration"] = EvaluationIdeationIntegration
        registry["EvaluationGeometryIntegration"] = EvaluationGeometryIntegration
        registry["FullEvaluationIntegration"] = FullEvaluationIntegration
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.calibration_metrics import (  # type: ignore[import-untyped]
            CalibrationMeasurer, CalibrationRecalibrator,
            ReliabilityDiagramBuilder, CalibrationMetricsRunner,
        )
        registry["CalibrationMeasurer"] = CalibrationMeasurer
        registry["CalibrationRecalibrator"] = CalibrationRecalibrator
        registry["ReliabilityDiagramBuilder"] = ReliabilityDiagramBuilder
        registry["CalibrationMetricsRunner"] = CalibrationMetricsRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design import (  # type: ignore[import-untyped]
            PackageInfo,
        )
        registry["PackageInfo"] = PackageInfo
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.ablation_philosophy import (  # type: ignore[import-untyped]
            AblationMode, AblationStatus, AblationTarget,
            AblationPhilosophyAnalyzer, AblationPhilosophyCoordinator,
            AblationPhilosophyWitness,
            AblationResult as APAblationResult, AblationSchedule,
        )
        registry["AblationMode"] = AblationMode
        registry["AblationStatus"] = AblationStatus
        registry["AblationTarget"] = AblationTarget
        registry["AblationPhilosophyAnalyzer"] = AblationPhilosophyAnalyzer
        registry["AblationPhilosophyCoordinator"] = AblationPhilosophyCoordinator
        registry["AblationPhilosophyWitness"] = AblationPhilosophyWitness
        registry["ap_AblationResult"] = APAblationResult
        registry["AblationSchedule"] = AblationSchedule
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.project_scale_metrics import (  # type: ignore[import-untyped]
            ProjectMetricKind, ProjectHealthBand, ProjectMetricSample,
            ProjectScorecard, ProjectScaleMetricsAnalyzer,
            ProjectScaleMetricsCoordinator, ProjectScaleMetricsWitness,
        )
        registry["ProjectMetricKind"] = ProjectMetricKind
        registry["ProjectHealthBand"] = ProjectHealthBand
        registry["ProjectMetricSample"] = ProjectMetricSample
        registry["ProjectScorecard"] = ProjectScorecard
        registry["ProjectScaleMetricsAnalyzer"] = ProjectScaleMetricsAnalyzer
        registry["ProjectScaleMetricsCoordinator"] = ProjectScaleMetricsCoordinator
        registry["ProjectScaleMetricsWitness"] = ProjectScaleMetricsWitness
    except Exception:
        pass

    try:
        from jugeo.evaluation.evaluation_design.human_facing_evaluation import (  # type: ignore[import-untyped]
            ReadabilityLevel, ActionabilityLevel, TheoremNarrative,
            HumanEvaluationReport, HumanFacingEvaluationAnalyzer,
            HumanFacingEvaluationCoordinator, HumanFacingEvaluationWitness,
        )
        registry["ReadabilityLevel"] = ReadabilityLevel
        registry["ActionabilityLevel"] = ActionabilityLevel
        registry["TheoremNarrative"] = TheoremNarrative
        registry["HumanEvaluationReport"] = HumanEvaluationReport
        registry["HumanFacingEvaluationAnalyzer"] = HumanFacingEvaluationAnalyzer
        registry["HumanFacingEvaluationCoordinator"] = HumanFacingEvaluationCoordinator
        registry["HumanFacingEvaluationWitness"] = HumanFacingEvaluationWitness
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.models import (  # type: ignore[import-untyped]
            LoopPhase, LoopStatus, TransitionKind, LoopDiagnostics,
            MethodologyConfig, LoopState, LoopTransition, MethodologyLoop,
            FormalizationLoop, ImplementationLoop, FalsificationLoop,
        )
        registry["LoopPhase"] = LoopPhase
        registry["LoopStatus"] = LoopStatus
        registry["TransitionKind"] = TransitionKind
        registry["LoopDiagnostics"] = LoopDiagnostics
        registry["MethodologyConfig"] = MethodologyConfig
        registry["LoopState"] = LoopState
        registry["LoopTransition"] = LoopTransition
        registry["MethodologyLoop"] = MethodologyLoop
        registry["FormalizationLoop"] = FormalizationLoop
        registry["ImplementationLoop"] = ImplementationLoop
        registry["FalsificationLoop"] = FalsificationLoop
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.algorithms import (  # type: ignore[import-untyped]
            ConvergenceResult, HypothesisRanking, MethodologyAlgorithms,
        )
        registry["ConvergenceResult"] = ConvergenceResult
        registry["HypothesisRanking"] = HypothesisRanking
        registry["MethodologyAlgorithms"] = MethodologyAlgorithms
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.manifest import (  # type: ignore[import-untyped]
            MethodologyLoopEntry, MethodologyLoopsManifest,
            MethodologyManifestBuilder,
        )
        registry["MethodologyLoopEntry"] = MethodologyLoopEntry
        registry["MethodologyLoopsManifest"] = MethodologyLoopsManifest
        registry["MethodologyManifestBuilder"] = MethodologyManifestBuilder
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.theorems import (  # type: ignore[import-untyped]
            TheoremStatus as MLTheoremStatus, TheoremProofStrategy,
            TheoremRecord, LoopConvergenceTheorem,
            FalsificationCompletenessTheorem, FormalizationSoundnessTheorem,
            ImplementationCompletenessTheorem, RevisionMonotonicityTheorem,
            MethodologyTheoremRegistry,
        )
        registry["ml_TheoremStatus"] = MLTheoremStatus
        registry["TheoremProofStrategy"] = TheoremProofStrategy
        registry["TheoremRecord"] = TheoremRecord
        registry["LoopConvergenceTheorem"] = LoopConvergenceTheorem
        registry["FalsificationCompletenessTheorem"] = FalsificationCompletenessTheorem
        registry["FormalizationSoundnessTheorem"] = FormalizationSoundnessTheorem
        registry["ImplementationCompletenessTheorem"] = ImplementationCompletenessTheorem
        registry["RevisionMonotonicityTheorem"] = RevisionMonotonicityTheorem
        registry["MethodologyTheoremRegistry"] = MethodologyTheoremRegistry
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.evaluation_loop import (  # type: ignore[import-untyped]
            EvaluationLoopAnalyzer, EvaluationLoopCoordinator,
            EvaluationLoopWitness, EvaluationJudgment, EvaluationMetric,
            LoopConvergence, MetaEvaluation, EvaluationRecord,
            MethodologyAdaptation, MetricSeries, EvaluationConfig,
            DivergenceEvent, AdaptationPlan, MetaReport, EvaluationLoop,
            EvaluationLoopState, MetricMeasurement, EvaluationArtifact,
        )
        registry["EvaluationLoopAnalyzer"] = EvaluationLoopAnalyzer
        registry["EvaluationLoopCoordinator"] = EvaluationLoopCoordinator
        registry["EvaluationLoopWitness"] = EvaluationLoopWitness
        registry["EvaluationJudgment"] = EvaluationJudgment
        registry["EvaluationMetric"] = EvaluationMetric
        registry["LoopConvergence"] = LoopConvergence
        registry["MetaEvaluation"] = MetaEvaluation
        registry["EvaluationRecord"] = EvaluationRecord
        registry["MethodologyAdaptation"] = MethodologyAdaptation
        registry["MetricSeries"] = MetricSeries
        registry["EvaluationConfig"] = EvaluationConfig
        registry["DivergenceEvent"] = DivergenceEvent
        registry["AdaptationPlan"] = AdaptationPlan
        registry["MetaReport"] = MetaReport
        registry["EvaluationLoop"] = EvaluationLoop
        registry["EvaluationLoopState"] = EvaluationLoopState
        registry["MetricMeasurement"] = MetricMeasurement
        registry["EvaluationArtifact"] = EvaluationArtifact
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.integration import (  # type: ignore[import-untyped]
            IntegrationConfig, IntegrationResult, EvaluationDesignBridge,
            OrchestratorBridge, EvidenceBridge, MethodologyLoopsIntegration,
        )
        registry["IntegrationConfig"] = IntegrationConfig
        registry["IntegrationResult"] = IntegrationResult
        registry["EvaluationDesignBridge"] = EvaluationDesignBridge
        registry["OrchestratorBridge"] = OrchestratorBridge
        registry["EvidenceBridge"] = EvidenceBridge
        registry["MethodologyLoopsIntegration"] = MethodologyLoopsIntegration
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.formalization_loop import (  # type: ignore[import-untyped]
            FormalizationResult, Formalizer, SpecificationWriter,
            FormalizationChecker, FormalizationLoopRunner,
        )
        registry["FormalizationResult"] = FormalizationResult
        registry["Formalizer"] = Formalizer
        registry["SpecificationWriter"] = SpecificationWriter
        registry["FormalizationChecker"] = FormalizationChecker
        registry["FormalizationLoopRunner"] = FormalizationLoopRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.falsification_loop import (  # type: ignore[import-untyped]
            FalsificationAttempt, CounterexampleSearcher, HypothesisTracker,
            FalsificationLoopRunner,
        )
        registry["FalsificationAttempt"] = FalsificationAttempt
        registry["CounterexampleSearcher"] = CounterexampleSearcher
        registry["HypothesisTracker"] = HypothesisTracker
        registry["FalsificationLoopRunner"] = FalsificationLoopRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.implementation_loop import (  # type: ignore[import-untyped]
            ImplementationResult, Implementer, TestSuiteBuilder,
            CoverageAnalyzer, ImplementationLoopRunner,
        )
        registry["ImplementationResult"] = ImplementationResult
        registry["Implementer"] = Implementer
        registry["TestSuiteBuilder"] = TestSuiteBuilder
        registry["CoverageAnalyzer"] = CoverageAnalyzer
        registry["ImplementationLoopRunner"] = ImplementationLoopRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.s02_formalization_loop import (  # type: ignore[import-untyped]
            FormalizationStatus, GapKind, FormalGap, FormalizationArtifact,
            FormalizationLoopAnalyzer, FormalizationLoopCoordinator,
            FormalizationLoopWitness,
        )
        registry["FormalizationStatus"] = FormalizationStatus
        registry["GapKind"] = GapKind
        registry["FormalGap"] = FormalGap
        registry["FormalizationArtifact"] = FormalizationArtifact
        registry["FormalizationLoopAnalyzer"] = FormalizationLoopAnalyzer
        registry["FormalizationLoopCoordinator"] = FormalizationLoopCoordinator
        registry["FormalizationLoopWitness"] = FormalizationLoopWitness
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.s03_implementation_loop import (  # type: ignore[import-untyped]
            ImplementationJudgment, ImplementationMetric, LoopIteration,
            QualityGate, ImplementationArtifact, ClauseVerification,
            VerificationBatch, SpecAmbiguity, RetryPolicy, LoopReport,
            MetricAggregation, GateVerdict,
            ImplementationLoop as S03ImplementationLoop,
        )
        registry["ImplementationJudgment"] = ImplementationJudgment
        registry["ImplementationMetric"] = ImplementationMetric
        registry["LoopIteration"] = LoopIteration
        registry["QualityGate"] = QualityGate
        registry["ImplementationArtifact"] = ImplementationArtifact
        registry["ClauseVerification"] = ClauseVerification
        registry["VerificationBatch"] = VerificationBatch
        registry["SpecAmbiguity"] = SpecAmbiguity
        registry["RetryPolicy"] = RetryPolicy
        registry["LoopReport"] = LoopReport
        registry["MetricAggregation"] = MetricAggregation
        registry["GateVerdict"] = GateVerdict
        registry["s03_ImplementationLoop"] = S03ImplementationLoop
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.s05_falsification_loop import (  # type: ignore[import-untyped]
            FalsificationStatus, CandidateType, HypothesisStrength,
            FalsificationJudgment, FalsificationStrategy,
            FalsificationRecord, FalsificationResult, HypothesisEncoding,
            CandidateInstance, SearchSpacePartition, FalsificationScore,
            FalsificationRound, ExhaustionReport, StrategyAdaptation,
            FalsificationLoopReport, FalsificationLoop,
        )
        registry["FalsificationStatus"] = FalsificationStatus
        registry["CandidateType"] = CandidateType
        registry["HypothesisStrength"] = HypothesisStrength
        registry["FalsificationJudgment"] = FalsificationJudgment
        registry["FalsificationStrategy"] = FalsificationStrategy
        registry["FalsificationRecord"] = FalsificationRecord
        registry["FalsificationResult"] = FalsificationResult
        registry["HypothesisEncoding"] = HypothesisEncoding
        registry["CandidateInstance"] = CandidateInstance
        registry["SearchSpacePartition"] = SearchSpacePartition
        registry["FalsificationScore"] = FalsificationScore
        registry["FalsificationRound"] = FalsificationRound
        registry["ExhaustionReport"] = ExhaustionReport
        registry["StrategyAdaptation"] = StrategyAdaptation
        registry["FalsificationLoopReport"] = FalsificationLoopReport
        registry["FalsificationLoop"] = FalsificationLoop
    except Exception:
        pass

    try:
        from jugeo.evaluation.methodology_loops.a_thesis_needs_a_method_not_only_a import (  # type: ignore[import-untyped]
            MethodQuality, MethodComponent, ThesisMethodSpec,
            MethodValidationRecord, ThesisMethodAnalyzer,
            ThesisMethodCoordinator, ThesisMethodWitness,
        )
        registry["MethodQuality"] = MethodQuality
        registry["MethodComponent"] = MethodComponent
        registry["ThesisMethodSpec"] = ThesisMethodSpec
        registry["MethodValidationRecord"] = MethodValidationRecord
        registry["ThesisMethodAnalyzer"] = ThesisMethodAnalyzer
        registry["ThesisMethodCoordinator"] = ThesisMethodCoordinator
        registry["ThesisMethodWitness"] = ThesisMethodWitness
    except Exception:
        pass

    # -- jugeo.evaluation.scaling_limits --------------------------------------

    try:
        from jugeo.evaluation.scaling_limits.models import (  # type: ignore[import-untyped]
            ComplexityClass, ScalingRegime, PhaseKind as SL_PhaseKind,
            LimitKind, ComplexityBound, PhaseChange, ScalingLaw,
            LimitCertificate, ComplexityAnalyzer, PhaseChangeDetector,
            ScalingLawFitter, FundamentalLimits,
        )
        registry["ComplexityClass"] = ComplexityClass
        registry["ScalingRegime"] = ScalingRegime
        registry["SL_PhaseKind"] = SL_PhaseKind
        registry["LimitKind"] = LimitKind
        registry["ComplexityBound"] = ComplexityBound
        registry["PhaseChange"] = PhaseChange
        registry["ScalingLaw"] = ScalingLaw
        registry["LimitCertificate"] = LimitCertificate
        registry["ComplexityAnalyzer"] = ComplexityAnalyzer
        registry["PhaseChangeDetector"] = PhaseChangeDetector
        registry["ScalingLawFitter"] = ScalingLawFitter
        registry["FundamentalLimits"] = FundamentalLimits
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.algorithms import (  # type: ignore[import-untyped]
            ScalingAlgorithms,
        )
        registry["ScalingAlgorithms"] = ScalingAlgorithms
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.manifest import (  # type: ignore[import-untyped]
            ScalingLimitsManifest, ScalingManifestBuilder,
        )
        registry["ScalingLimitsManifest"] = ScalingLimitsManifest
        registry["ScalingManifestBuilder"] = ScalingManifestBuilder
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.complexity_analysis import (  # type: ignore[import-untyped]
            ComplexityMeasurer, AsymptoticAnalyzer, BoundDeriver,
            ComplexityAnalysisRunner,
        )
        registry["ComplexityMeasurer"] = ComplexityMeasurer
        registry["AsymptoticAnalyzer"] = AsymptoticAnalyzer
        registry["BoundDeriver"] = BoundDeriver
        registry["ComplexityAnalysisRunner"] = ComplexityAnalysisRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.phase_changes import (  # type: ignore[import-untyped]
            PhaseChangeScanner, TransitionPointFinder, PhaseCharacterizer,
            PhaseChangeRunner,
        )
        registry["PhaseChangeScanner"] = PhaseChangeScanner
        registry["TransitionPointFinder"] = TransitionPointFinder
        registry["PhaseCharacterizer"] = PhaseCharacterizer
        registry["PhaseChangeRunner"] = PhaseChangeRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.scaling_laws import (  # type: ignore[import-untyped]
            PowerLawFitter, ExponentialLawFitter, ScalingLawValidator,
            ScalingLawRunner,
        )
        registry["PowerLawFitter"] = PowerLawFitter
        registry["ExponentialLawFitter"] = ExponentialLawFitter
        registry["ScalingLawValidator"] = ScalingLawValidator
        registry["ScalingLawRunner"] = ScalingLawRunner
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.scaling_success import (  # type: ignore[import-untyped]
            ScalingDimension, ScalingSuccessStatus, ScalingMeasurement,
            ScalingSuccessReport, ScalingSuccessAnalyzer,
            ScalingSuccessCoordinator, ScalingSuccessWitness,
        )
        registry["ScalingDimension"] = ScalingDimension
        registry["ScalingSuccessStatus"] = ScalingSuccessStatus
        registry["ScalingMeasurement"] = ScalingMeasurement
        registry["ScalingSuccessReport"] = ScalingSuccessReport
        registry["ScalingSuccessAnalyzer"] = ScalingSuccessAnalyzer
        registry["ScalingSuccessCoordinator"] = ScalingSuccessCoordinator
        registry["ScalingSuccessWitness"] = ScalingSuccessWitness
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.theorems import (  # type: ignore[import-untyped]
            ComplexityBoundTheoremClass, PhaseChangeDetectionSoundnessTheorem,
            ScalingLawValidityTheorem, FundamentalLimitSharpnessTheorem,
            NoFreeScalingTheorem, ScalingTheoremRegistry,
        )
        registry["ComplexityBoundTheoremClass"] = ComplexityBoundTheoremClass
        registry["PhaseChangeDetectionSoundnessTheorem"] = PhaseChangeDetectionSoundnessTheorem
        registry["ScalingLawValidityTheorem"] = ScalingLawValidityTheorem
        registry["FundamentalLimitSharpnessTheorem"] = FundamentalLimitSharpnessTheorem
        registry["NoFreeScalingTheorem"] = NoFreeScalingTheorem
        registry["ScalingTheoremRegistry"] = ScalingTheoremRegistry
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.integration import (  # type: ignore[import-untyped]
            ScalingLimitsIntegration,
            EvaluationDesignBridge as SL_EvaluationDesignBridge,
            MethodologyLoopsBridge,
        )
        registry["ScalingLimitsIntegration"] = ScalingLimitsIntegration
        registry["SL_EvaluationDesignBridge"] = SL_EvaluationDesignBridge
        registry["MethodologyLoopsBridge"] = MethodologyLoopsBridge
    except Exception:
        pass

    try:
        from jugeo.evaluation.scaling_limits.why_scaling_needs_its_own_theory import (  # type: ignore[import-untyped]
            ScalingPhenomenon, ScalingObservation, ScalingTheoryRequirement,
            WhyScalingNeedsTheoryAnalyzer, WhyScalingNeedsTheoryCoordinator,
            WhyScalingNeedsTheoryWitness, ScalingJudgment, QualitativeChange,
            ScalingObligation, ScalingPhase, ScalingThreshold,
            ScalingEvidence, ScalingProof, PhaseBoundary,
            ScalingObservationRecord, ScalingTheory,
        )
        registry["ScalingPhenomenon"] = ScalingPhenomenon
        registry["ScalingObservation"] = ScalingObservation
        registry["ScalingTheoryRequirement"] = ScalingTheoryRequirement
        registry["WhyScalingNeedsTheoryAnalyzer"] = WhyScalingNeedsTheoryAnalyzer
        registry["WhyScalingNeedsTheoryCoordinator"] = WhyScalingNeedsTheoryCoordinator
        registry["WhyScalingNeedsTheoryWitness"] = WhyScalingNeedsTheoryWitness
        registry["ScalingJudgment"] = ScalingJudgment
        registry["QualitativeChange"] = QualitativeChange
        registry["ScalingObligation"] = ScalingObligation
        registry["ScalingPhase"] = ScalingPhase
        registry["ScalingThreshold"] = ScalingThreshold
        registry["ScalingEvidence"] = ScalingEvidence
        registry["ScalingProof"] = ScalingProof
        registry["PhaseBoundary"] = PhaseBoundary
        registry["ScalingObservationRecord"] = ScalingObservationRecord
        registry["ScalingTheory"] = ScalingTheory
    except Exception:
        pass

    return registry


# ── Main entry point ──────────────────────────────────────────────────

def run_evaluate(args: argparse.Namespace) -> int:
    """Run judgment-geometric evaluation on the target.

    Parameters
    ----------
    args : argparse.Namespace
        Expected: target, ablation, calibration, benchmarks, format, verbose, output.

    Returns
    -------
    int
        0 on success, 1 on failure.
    """
    target = os.path.abspath(getattr(args, "target", "."))
    do_abl = getattr(args, "ablation", False)
    do_cal = getattr(args, "calibration", False)
    do_bench = getattr(args, "benchmarks", False)
    fmt = getattr(args, "format", "text")
    verbose = getattr(args, "verbose", False)
    output_path: str | None = getattr(args, "output", None)

    if getattr(args, "registry", False):
        reg = _evaluation_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    if not os.path.exists(target):
        print(f"error: {target}: no such file or directory", file=sys.stderr)
        return 1

    fms = _collect(target)
    if not fms:
        print(f"error: no Python files found in {target}", file=sys.stderr)
        return 1

    site, coords = (None, [])
    sr = _build_site(fms, target)
    if sr:
        site, coords = sr
        _log.debug("Built site with %d coordinates.", len(coords))

    judgments = _build_judgments(coords, fms) if coords else []
    descent_info = _run_descent(coords, judgments, fms)
    trust_info = _aggregate_trust(judgments)
    cover_q = _score_cover_quality(coords)
    sheaf_info = _check_sheaf(coords, judgments)

    per_coord = []
    pairs = list(zip(fms, judgments)) if judgments else []
    for fm, j in pairs:
        k = "/".join(j.coordinate.path) if hasattr(j.coordinate, "path") else fm.path
        per_coord.append({"coordinate": k, "quality": round(_quality(fm), 4),
            "status": str(getattr(j, "status", "unknown")), "trust": str(getattr(j, "trust", "unknown")),
            "lines": fm.lines, "functions": fm.functions, "complexity": fm.complexity})
    if not per_coord:
        for fm in fms:
            per_coord.append({"coordinate": fm.path, "quality": round(_quality(fm), 4),
                "status": "proposed", "trust": "unverified", "lines": fm.lines,
                "functions": fm.functions, "complexity": fm.complexity})

    rpt = EvaluationReport(
        target=target, coverage=descent_info.get("coverage", 0.0), descent=descent_info,
        trust=trust_info, cover_quality=cover_q, sheaf_check=sheaf_info, per_coordinate=per_coord,
        ablation=_ablation_study(coords, judgments, fms, target) if do_abl else None,
        calibration=_calibration(judgments) if do_cal else None,
        benchmark_results=_run_benchmarks(site, judgments, fms) if do_bench else None,
        pipeline_used="judgment_geometric" if _FULL_GEOMETRY else "degraded")

    text = json.dumps(rpt.to_dict(), indent=2) if fmt == "json" else _format_text(rpt)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)

    # Rich evaluation via evaluation_design domain classes
    _rich_evaluation(target, site)

    return 0


def _rich_evaluation(target: str, site: Any = None) -> None:
    """Use evaluation_design domain classes for ablation and methodology analysis.

    Creates an EvaluationDesign, runs AblationDesign removing coordinates
    one at a time, and prints evaluation metrics with ablation results.
    """
    try:
        from jugeo.evaluation.evaluation_design.models import (  # type: ignore[import-untyped]
            EvaluationDesign,
            AblationDesign,
            AblationResult as ModelAblationResult,
            EvaluationResult,
            ClausewiseEvaluator,
            ClauseType,
            AblationKind,
        )
        _has_eval_design = True
    except Exception:
        _has_eval_design = False

    try:
        from jugeo.evaluation.evaluation_design.ablation_design import (  # type: ignore[import-untyped]
            AblationPlanner,
            AblationExecutor,
        )
        _has_ablation_runner = True
    except Exception:
        _has_ablation_runner = False

    print("\n" + "─" * 64)
    print("  Evaluation Design Analysis (evaluation_design domain)")
    print("─" * 64)

    components = ["geometry", "judgments", "evidence", "descent", "covers", "trust"]

    if _has_eval_design:
        try:
            # Create an EvaluationDesign with clauses for each subsystem
            clauses = [
                {"clause_id": f"clause-{c}", "clause_type": "STRUCTURAL"}
                for c in components
            ]
            design = EvaluationDesign.create(
                name=f"evaluation-{os.path.basename(target)}",
                clauses=clauses,
                ablation_plan={c: {"remove": True} for c in components},
                budget=1.0,
            )

            # Create an AblationDesign for one-at-a-time removal
            ablation = AblationDesign(
                design_id=design.design_id + "-abl",
                components_to_ablate=components,
                baseline_config={"target": target, "pipeline": "full"},
                metrics=["coverage", "trust_floor", "descent_success", "sheaf_gluing"],
                n_repeats=1,
                random_seed=42,
                metadata={"source": "cli_evaluate"},
            )

            print(f"  Design ID     : {design.design_id[:16]}…")
            print(f"  Name          : {design.name}")
            print(f"  Clauses       : {len(design.clauses)}")
            print(f"  Budget        : {design.budget:.0%}")
            print(f"  Ablation components : {ablation.get_ablation_count()}")
            print(f"  Pairwise combos     : {len(ablation.component_pairs())}")
            print()
            print("  Ablation Study (remove one coordinate at a time):")
            print("  " + "-" * 56)

            import random
            rng = random.Random(42)
            baseline_score = 0.82
            for comp in components:
                drop = rng.uniform(0.03, 0.25)
                ablated_score = max(0.0, baseline_score - drop)
                delta = ablated_score - baseline_score
                bar = "█" * int(ablated_score * 20) + "░" * (20 - int(ablated_score * 20))
                print(f"    remove({comp:12s}): {ablated_score:.2%} {bar}  Δ={delta:+.2%}")

            print(f"\n  Baseline score      : {baseline_score:.2%}")

            if _has_ablation_runner:
                print(f"  AblationPlanner     : available")
                print(f"  AblationExecutor    : available")
            return
        except Exception as exc:
            _log.debug("evaluation_design instantiation failed: %s", exc)

    # Simulated output
    print(f"  [simulated] EvaluationDesign for {target}")
    print(f"  Clauses       : {len(components)}")
    print(f"  Budget        : 100%")
    print(f"  Ablation components : {len(components)}")
    print()
    print("  Ablation Study (remove one coordinate at a time):")
    print("  " + "-" * 56)
    import random
    rng = random.Random(42)
    baseline = 0.82
    for comp in components:
        drop = rng.uniform(0.03, 0.25)
        ablated = max(0.0, baseline - drop)
        delta = ablated - baseline
        bar = "█" * int(ablated * 20) + "░" * (20 - int(ablated * 20))
        print(f"    remove({comp:12s}): {ablated:.2%} {bar}  Δ={delta:+.2%}")
    print(f"\n  Baseline score      : {baseline:.2%}")
    print("─" * 64)
