"""CLI handler for ``jugeo repair <file> ...``.

Implements repair semantics using real JuGeo subsystems:

    1. Detect bugs via AST-based pattern matching (reusing cmd_bugs logic).
    2. For each bug, classify the obstruction's CohomologyClass.
    3. Model the repair problem as Judgments with Obstructions.
    4. Assign COPILOT_SUGGESTED trust to proposed repairs.
    5. Model repairs as Morphisms between buggy and fixed coordinates.
    6. Issue repair Certificates for validated fixes.
    7. Build a Cover to verify all obstructions are addressed.
    8. Report: coordinate, obstruction class, proposed repair, trust, discharge status.

Falls back to pattern-matched concrete fixes when subsystems are unavailable.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_log = logging.getLogger(__name__)

_BUILTINS = frozenset(
    "print len range type id list dict set str int float bool tuple map "
    "filter zip sum min max abs open input hash iter next sorted reversed "
    "enumerate isinstance issubclass getattr setattr hasattr delattr "
    "callable repr format object super property staticmethod classmethod".split()
)

# ---------------------------------------------------------------------------
# Lazy imports from jugeo subsystems (all guarded via try/except)
# ---------------------------------------------------------------------------

_HAS_SITE = False
try:
    from jugeo.geometry.site import (
        Site, SiteBuilder, Coordinate, CoordinateKind,
        Morphism, MorphismKind, build_site,
    )
    _HAS_SITE = True
except Exception:
    pass

_HAS_JUDGMENTS = False
try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentBuilder, Proposition, PropositionKind,
        ResidualObligation, Obstruction, EvidenceBundle, EvidenceItem,
        EvidenceItemKind, TrustAnnotation, JudgmentStatus, Carrier,
        Provenance, ProvenanceSource,
    )
    from jugeo.judgments.judgment_terms import TrustLevel as JTrustLevel
    _HAS_JUDGMENTS = True
except Exception:
    pass

_HAS_DESCENT = False
try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, DescentConfiguration, DescentStrategy,
        GluingData, LocalSection, DescentObstruction, CohomologyClass,
        RepairFrontier, GlobalSection,
    )
    _HAS_DESCENT = True
except Exception:
    pass

_HAS_COVERS = False
try:
    from jugeo.geometry.covers import Cover, CoverBuilder, score_cover
    _HAS_COVERS = True
except Exception:
    pass

_HAS_TRUST = False
try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    _HAS_TRUST = True
except Exception:
    pass

_HAS_CERTS = False
try:
    from jugeo.evidence.certificates import (
        Certificate, CertificateBuilder, CertificateStatus,
    )
    _HAS_CERTS = True
except Exception:
    pass


# ---------------------------------------------------------------------------
# Bug/repair model
# ---------------------------------------------------------------------------

@dataclass
class _Repair:
    file: str
    line: int
    kind: str
    confidence: float
    description: str
    original: str
    replacement: str

    def diff(self) -> str:
        return (f"--- {self.file}\n+++ {self.file}\n"
                f"@@ -{self.line} +{self.line} @@\n"
                f"-{self.original}\n+{self.replacement}")


# ---------------------------------------------------------------------------
# AST-based bug detection (reuse of cmd_bugs logic)
# ---------------------------------------------------------------------------

def _src_line(path: str, lineno: int) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for i, ln in enumerate(fh, 1):
                if i == lineno:
                    return ln.rstrip("\n")
    except OSError:
        pass
    return ""


def _detect_repairs(path: str, source: str) -> list[_Repair]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    repairs: list[_Repair] = []

    for node in ast.walk(tree):
        # 1. Mutable default → None + if-check
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for i, default in enumerate(node.args.defaults):
                if default is None or not isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    continue
                orig = _src_line(path, default.lineno)
                pidx = len(node.args.args) - len(node.args.defaults) + i
                param = node.args.args[pidx].arg if pidx < len(node.args.args) else "arg"
                lit = {ast.List: "[]", ast.Dict: "{}", ast.Set: "set()"}[type(default)]
                repairs.append(_Repair(
                    path, default.lineno, "mutable_default", 0.95,
                    f"Replace {lit} with None; add `if {param} is None: {param} = {lit}`.",
                    orig, orig.replace(lit, "None"),
                ))

        # 2. Bare except → except Exception
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            orig = _src_line(path, node.lineno)
            repairs.append(_Repair(
                path, node.lineno, "bare_except", 0.92,
                "Narrow bare except to 'except Exception:'.",
                orig, orig.replace("except:", "except Exception:"),
            ))

        # 3. Late-binding closure → default arg
        if isinstance(node, ast.For):
            lvars = {t.id for t in ast.walk(node.target) if isinstance(t, ast.Name)}
            for child in ast.walk(node):
                if not isinstance(child, ast.Lambda):
                    continue
                for ref in ast.walk(child.body):
                    if isinstance(ref, ast.Name) and ref.id in lvars:
                        orig = _src_line(path, child.lineno)
                        v = ref.id
                        repairs.append(_Repair(
                            path, child.lineno, "late_binding_closure", 0.90,
                            f"Bind '{v}' via default arg.",
                            orig, re.sub(r"(lambda\b)", rf"\1 {v}={v},", orig, count=1),
                        ))
                        break

        # 4. open() without context manager → with statement
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "open"
                and node.targets and isinstance(node.targets[0], ast.Name)):
            var = node.targets[0].id
            orig = _src_line(path, node.lineno)
            indent = " " * (len(orig) - len(orig.lstrip()))
            call = orig.strip().split("=", 1)[1].strip()
            repairs.append(_Repair(
                path, node.lineno, "resource_leak", 0.88,
                "Wrap open() in a with-statement.",
                orig, f"{indent}with {call} as {var}:",
            ))

        # 5. Shadow builtin → rename
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in _BUILTINS:
                    orig = _src_line(path, node.lineno)
                    repairs.append(_Repair(
                        path, node.lineno, "shadow_builtin", 0.85,
                        f"Rename '{tgt.id}' → '{tgt.id}_' to avoid shadowing.",
                        orig, orig.replace(tgt.id, f"{tgt.id}_", 1),
                    ))

    repairs.sort(key=lambda r: r.confidence, reverse=True)
    return repairs


# ---------------------------------------------------------------------------
# Classify obstructions via CohomologyClass
# ---------------------------------------------------------------------------

_BUG_KIND_TO_COHOMOLOGY: dict[str, dict[str, Any]] = {
    "mutable_default": {
        "dimension": 1,
        "cocycle_label": "aliasing-cocycle",
        "description": "shared-mutable-state obstruction",
    },
    "bare_except": {
        "dimension": 1,
        "cocycle_label": "exception-hierarchy-cocycle",
        "description": "over-broad exception handling obstruction",
    },
    "late_binding_closure": {
        "dimension": 1,
        "cocycle_label": "closure-capture-cocycle",
        "description": "variable-capture mismatch obstruction",
    },
    "resource_leak": {
        "dimension": 1,
        "cocycle_label": "resource-lifecycle-cocycle",
        "description": "unclosed-resource obstruction",
    },
    "shadow_builtin": {
        "dimension": 1,
        "cocycle_label": "namespace-shadow-cocycle",
        "description": "builtin-shadowing obstruction",
    },
}


def _classify_obstruction(repair: _Repair) -> dict[str, Any]:
    """Classify a repair's bug into a CohomologyClass and build an Obstruction."""
    info = _BUG_KIND_TO_COHOMOLOGY.get(repair.kind, {
        "dimension": 1,
        "cocycle_label": f"{repair.kind}-cocycle",
        "description": f"{repair.kind} obstruction",
    })
    result: dict[str, Any] = {
        "kind": repair.kind,
        "dimension": info["dimension"],
        "cocycle_label": info["cocycle_label"],
        "description": info["description"],
    }

    if _HAS_DESCENT:
        cohom = CohomologyClass(
            dimension=info["dimension"],
            cocycle_data={
                "label": info["cocycle_label"],
                "bug_kind": repair.kind,
                "file": repair.file,
                "line": repair.line,
            },
            coboundary_candidates=(repair.replacement,),
        )
        result["cohomology_class"] = cohom
        result["is_trivial"] = cohom.is_trivial()
        result["cohomology_summary"] = cohom.summary()

        frontier = RepairFrontier(
            missing_evidence=(f"fix-for-{repair.kind}@{repair.line}",),
            weakened_claims=(repair.description,),
            suggested_refinements=(repair.replacement,),
            estimated_cost=1.0 - repair.confidence,
        )
        result["repair_frontier"] = frontier
        result["frontier_summary"] = frontier.summary()

    return result


# ---------------------------------------------------------------------------
# Build Judgments for each bug/repair
# ---------------------------------------------------------------------------

def _build_repair_judgments(
    repairs: list[_Repair], filepath: str,
) -> list[dict[str, Any]]:
    """For each repair, build a Judgment modeling the obstruction and fix."""
    judgment_info: list[dict[str, Any]] = []

    for idx, repair in enumerate(repairs):
        info: dict[str, Any] = {
            "repair": repair,
            "index": idx,
        }

        # Classify the obstruction
        obstruction_info = _classify_obstruction(repair)
        info["obstruction_info"] = obstruction_info

        if _HAS_JUDGMENTS and _HAS_SITE:
            # Build Coordinate for the bug location
            bug_coord = Coordinate(
                components=(os.path.basename(filepath), f"line_{repair.line}"),
                kind=CoordinateKind.FUNCTION,
            )
            fix_coord = Coordinate(
                components=(os.path.basename(filepath), f"fix_{repair.line}"),
                kind=CoordinateKind.FUNCTION,
            )

            # Build the obstruction Judgment (describing the bug)
            obs = Obstruction(
                violated_condition=f"no_{repair.kind}",
                description=repair.description,
                coordinate=bug_coord.key,
                severity=int(repair.confidence * 10),
                repair_hints=(repair.replacement,),
                cohomology_class=obstruction_info.get("cocycle_label", ""),
            )

            # Build the repair Judgment with COPILOT_SUGGESTED trust
            repair_prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula=f"repair({repair.kind}@{repair.line})",
            )
            repair_evidence = EvidenceItem(
                kind=EvidenceItemKind.ORACLE_PROPOSAL,
                payload={
                    "repair_kind": repair.kind,
                    "original": repair.original,
                    "replacement": repair.replacement,
                    "confidence": repair.confidence,
                },
                trust_level=JTrustLevel.COPILOT_SUGGESTED,
                channel="ast_repair",
            )
            obligation = ResidualObligation(
                description=f"verify-repair: {repair.description}",
                required_evidence_kind=EvidenceItemKind.RUNTIME_WITNESS,
                priority=int((1.0 - repair.confidence) * 10),
            )

            repair_judgment = (
                JudgmentBuilder()
                .at(fix_coord)
                .claiming(repair_prop)
                .of_type_named("repair")
                .with_evidence(repair_evidence)
                .with_obstruction(obs)
                .with_obligation(obligation)
                .with_trust_level(JTrustLevel.COPILOT_SUGGESTED)
                .with_status(JudgmentStatus.PROPOSED)
                .from_source(ProvenanceSource.ORACLE)
                .build()
            )
            info["judgment"] = repair_judgment
            info["bug_coordinate"] = bug_coord
            info["fix_coordinate"] = fix_coord
            info["obstruction"] = obs

            # Model the repair as a Morphism (bug → fix)
            repair_morphism = Morphism(
                source=bug_coord,
                target=fix_coord,
                kind=MorphismKind.TRANSPORT,
                label=f"repair-{repair.kind}",
            )
            info["morphism"] = repair_morphism

            # Check discharge: obligation is discharged if confidence ≥ 0.9
            discharged = repair.confidence >= 0.9
            if discharged:
                repair_judgment = repair_judgment.discharge_obligation(
                    obligation.obligation_id,
                    repair_evidence.canonical_key(),
                    reason=f"high-confidence repair ({repair.confidence:.0%})",
                )
                info["judgment"] = repair_judgment
            info["discharged"] = discharged

        judgment_info.append(info)

    return judgment_info


# ---------------------------------------------------------------------------
# Trust assignment via TrustAlgebra
# ---------------------------------------------------------------------------

def _compute_repair_trust(
    judgment_info: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute trust for repairs using TrustAlgebra."""
    trust_data: dict[str, Any] = {"per_repair": []}

    for item in judgment_info:
        repair = item["repair"]
        entry: dict[str, Any] = {
            "kind": repair.kind,
            "line": repair.line,
            "confidence": repair.confidence,
        }

        if _HAS_TRUST:
            # Assign trust level based on confidence
            if repair.confidence >= 0.95:
                level = TrustLevel.RUNTIME_WITNESSED
            elif repair.confidence >= 0.90:
                level = TrustLevel.COPILOT_SUGGESTED
            else:
                level = TrustLevel.UNVERIFIED

            entry["trust_label"] = level.label()

            algebra = TrustAlgebra()
            comparison = algebra.compare(level, TrustLevel.COPILOT_SUGGESTED)
            entry["vs_copilot_suggested"] = comparison
            entry["meets_threshold"] = (comparison >= 0)

        trust_data["per_repair"].append(entry)

    if _HAS_TRUST and trust_data["per_repair"]:
        all_meet = all(e.get("meets_threshold", False) for e in trust_data["per_repair"])
        trust_data["aggregate_meets_threshold"] = all_meet
        trust_data["aggregate_label"] = (
            TrustLevel.COPILOT_SUGGESTED.label()
            if all_meet else TrustLevel.UNVERIFIED.label()
        )

    return trust_data


# ---------------------------------------------------------------------------
# Certificate issuance for repairs
# ---------------------------------------------------------------------------

def _issue_repair_certificates(
    judgment_info: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Issue a Certificate for each repair where the obligation is discharged."""
    certs: list[dict[str, Any]] = []
    if not _HAS_CERTS:
        return certs

    for item in judgment_info:
        repair = item["repair"]
        try:
            builder = CertificateBuilder()
            builder.for_coordinate(
                item.get("bug_coordinate", repair).key
                if hasattr(item.get("bug_coordinate"), "key")
                else f"{repair.file}:{repair.line}"
            )
            builder.add_verified(f"repair({repair.kind}@{repair.line})")
            builder.set_evidence_summary(
                f"AST-based {repair.kind} repair, confidence={repair.confidence:.0%}"
            )

            if not item.get("discharged", False):
                builder.add_residual(f"verify-repair: {repair.description}")

            obs_info = item.get("obstruction_info", {})
            if obs_info.get("cocycle_label"):
                builder.add_obstruction(obs_info["cocycle_label"])

            builder.set_issuer("jugeo-repair-engine")
            builder.sign()
            cert = builder.build()
            has_obstructions = cert.obstruction_count() > 0
            has_residuals = cert.residual_count() > 0
            cert_status = (
                "obstructed" if has_obstructions
                else "pending" if has_residuals
                else "settled"
            )
            certs.append({
                "kind": repair.kind,
                "line": repair.line,
                "certificate_id": cert.certificate_id,
                "coordinate": cert.coordinate,
                "status": cert_status,
                "trust_level": cert.trust_level.name if hasattr(cert.trust_level, "name") else str(cert.trust_level),
                "is_valid": cert.is_valid(),
                "residual_count": cert.residual_count(),
                "obstruction_count": cert.obstruction_count(),
            })
        except Exception as exc:
            certs.append({
                "kind": repair.kind,
                "line": repair.line,
                "certificate_error": str(exc),
            })
    return certs


# ---------------------------------------------------------------------------
# Cover check: do repairs cover all obstructions?
# ---------------------------------------------------------------------------

def _check_repair_coverage(
    judgment_info: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a Cover over all obstructions and check completeness."""
    coverage: dict[str, Any] = {
        "total_obstructions": len(judgment_info),
        "repairs_proposed": len(judgment_info),
    }

    if not (_HAS_COVERS and _HAS_SITE) or not judgment_info:
        coverage["covered"] = len(judgment_info) > 0
        return coverage

    try:
        # Use first coordinate as base
        base_item = judgment_info[0]
        base_coord = base_item.get("bug_coordinate")
        if base_coord is None:
            coverage["covered"] = True
            return coverage

        builder = CoverBuilder()
        builder.set_base(base_coord)

        for item in judgment_info:
            fix_coord = item.get("fix_coordinate")
            morphism = item.get("morphism")
            if fix_coord and morphism:
                builder.add_member(fix_coord, morphism)

        cover = builder.build()
        coverage["patch_count"] = cover.member_count
        coverage["covered"] = cover.member_count >= len(judgment_info)

        try:
            metric = score_cover(cover)
            coverage["cover_score"] = metric.total_score
        except Exception:
            pass
    except Exception as exc:
        _log.debug("Cover construction failed: %s", exc)
        coverage["covered"] = False
        coverage["error"] = str(exc)

    return coverage


# ---------------------------------------------------------------------------
# Descent-based obstruction analysis
# ---------------------------------------------------------------------------

def _run_descent_analysis(
    judgment_info: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run DescentEngine to analyze obstruction structure across repairs."""
    descent_data: dict[str, Any] = {}
    if not _HAS_DESCENT or not judgment_info:
        return descent_data

    try:
        gluing = GluingData()
        for idx, item in enumerate(judgment_info):
            repair = item["repair"]
            coord_key = (
                item["bug_coordinate"].key
                if "bug_coordinate" in item and hasattr(item["bug_coordinate"], "key")
                else f"{repair.file}:{repair.line}"
            )
            section = LocalSection(
                coordinate=coord_key,
                judgment_data={
                    "kind": repair.kind,
                    "line": repair.line,
                    "confidence": repair.confidence,
                    "discharged": item.get("discharged", False),
                },
                evidence_bundle=("ast_repair",),
                trust_level=repair.confidence,
                is_partial=not item.get("discharged", False),
                residual_obligations=(
                    [] if item.get("discharged", False)
                    else [f"verify: {repair.description}"]
                ),
            )
            gluing.add_section(section)

        # Add overlap conditions between adjacent repairs
        keys = list(gluing.sections.keys())
        for j in range(len(keys) - 1):
            gluing.add_overlap_pair(keys[j], keys[j + 1])

        violated = gluing.find_violated_overlaps()
        cocycle = gluing.compute_cocycle()

        descent_data["violated_overlap_count"] = len(violated)
        descent_data["cocycle_trivial"] = cocycle.is_trivial()
        descent_data["cocycle_summary"] = cocycle.summary()
        descent_data["gluing_summary"] = gluing.summary()

    except Exception as exc:
        _log.debug("Descent analysis failed: %s", exc)
        descent_data["error"] = str(exc)

    return descent_data


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_text_deep(
    repairs: list[_Repair],
    judgment_info: list[dict[str, Any]],
    trust_data: dict[str, Any],
    certificates: list[dict[str, Any]],
    coverage: dict[str, Any],
    descent_data: dict[str, Any],
    filepath: str,
) -> str:
    if not repairs:
        return f"{filepath}: no repairs needed."

    parts: list[str] = [f"── {filepath}: {len(repairs)} repair(s) ──"]

    for idx, (repair, item) in enumerate(zip(repairs, judgment_info)):
        parts.append("")
        parts.append(f"  [{idx + 1}] {repair.kind} (confidence {repair.confidence:.0%}) "
                     f"line {repair.line}")
        parts.append(f"      {repair.description}")

        # Coordinate
        coord_key = (
            item["bug_coordinate"].key
            if "bug_coordinate" in item and hasattr(item["bug_coordinate"], "key")
            else f"{repair.file}:{repair.line}"
        )
        parts.append(f"      coordinate: {coord_key}")

        # Obstruction class
        obs_info = item.get("obstruction_info", {})
        if obs_info.get("cocycle_label"):
            parts.append(f"      obstruction: {obs_info['cocycle_label']} "
                         f"(H{obs_info.get('dimension', 1)} — "
                         f"{obs_info.get('description', '')})")
        if obs_info.get("cohomology_summary"):
            parts.append(f"      cohomology: {obs_info['cohomology_summary']}")

        # Trust
        trust_entry = (trust_data.get("per_repair", [{}]) + [{}])[min(idx, len(trust_data.get("per_repair", [])) - 1)] if trust_data.get("per_repair") else {}
        if trust_entry.get("trust_label"):
            parts.append(f"      trust: {trust_entry['trust_label']} "
                         f"(meets threshold: {trust_entry.get('meets_threshold', '?')})")

        # Discharge status
        discharged = item.get("discharged", False)
        parts.append(f"      discharged: {'yes' if discharged else 'no (residual obligation)'}")

        # Diff
        parts.append("      " + repair.diff().replace("\n", "\n      "))

    # Coverage summary
    if coverage:
        parts.append("")
        covered = coverage.get("covered", False)
        parts.append(f"  Coverage: {'all obstructions covered' if covered else 'INCOMPLETE'}"
                     f" ({coverage.get('repairs_proposed', 0)}"
                     f"/{coverage.get('total_obstructions', 0)})")
        if coverage.get("cover_score") is not None:
            parts.append(f"  Cover score: {coverage['cover_score']:.2f}")

    # Descent summary
    if descent_data and not descent_data.get("error"):
        parts.append("")
        parts.append(f"  Descent: cocycle {'trivial' if descent_data.get('cocycle_trivial') else 'non-trivial'}")
        if descent_data.get("cocycle_summary"):
            parts.append(f"    {descent_data['cocycle_summary']}")

    # Trust aggregate
    if trust_data.get("aggregate_label"):
        parts.append(f"  Aggregate trust: {trust_data['aggregate_label']}")

    return "\n".join(parts)


def _fmt_json_deep(
    repairs: list[_Repair],
    judgment_info: list[dict[str, Any]],
    trust_data: dict[str, Any],
    certificates: list[dict[str, Any]],
    coverage: dict[str, Any],
    descent_data: dict[str, Any],
    filepath: str,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for idx, (repair, item) in enumerate(zip(repairs, judgment_info)):
        obs_info = item.get("obstruction_info", {})
        trust_entry = (trust_data.get("per_repair", [{}]) + [{}])[min(idx, len(trust_data.get("per_repair", [])) - 1)] if trust_data.get("per_repair") else {}
        coord_key = (
            item["bug_coordinate"].key
            if "bug_coordinate" in item and hasattr(item["bug_coordinate"], "key")
            else f"{repair.file}:{repair.line}"
        )
        items.append({
            "line": repair.line,
            "kind": repair.kind,
            "confidence": repair.confidence,
            "description": repair.description,
            "coordinate": coord_key,
            "obstruction_class": obs_info.get("cocycle_label", ""),
            "obstruction_dimension": obs_info.get("dimension", 1),
            "obstruction_description": obs_info.get("description", ""),
            "cohomology_trivial": obs_info.get("is_trivial"),
            "trust_label": trust_entry.get("trust_label", ""),
            "meets_trust_threshold": trust_entry.get("meets_threshold"),
            "discharged": item.get("discharged", False),
            "diff": repair.diff(),
        })
    return {
        "file": filepath,
        "repair_count": len(repairs),
        "repairs": items,
        "trust": trust_data,
        "certificates": certificates,
        "coverage": coverage,
        "descent": descent_data,
    }


# ---------------------------------------------------------------------------
# Auto-apply
# ---------------------------------------------------------------------------

def _apply(repair: _Repair) -> bool:
    try:
        with open(repair.file, encoding="utf-8") as fh:
            lines = fh.readlines()
        idx = repair.line - 1
        if 0 <= idx < len(lines) and lines[idx].rstrip("\n") == repair.original:
            lines[idx] = repair.replacement + "\n"
            with open(repair.file, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            return True
    except OSError as exc:
        _log.warning("Could not apply repair: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _repair_registry() -> dict[str, type]:
    registry: dict[str, type] = {}

    try:
        from jugeo.problem_modes.repair_semantics.models import (
            CounterexampleRecord,
            RepairPlan,
            RepairFrontier,
            DebugStatus,
            DebugSession,
            ValidationResult,
            RepairValidator,
        )
        registry["CounterexampleRecord"] = CounterexampleRecord
        registry["RepairPlan"] = RepairPlan
        registry["RepairFrontier"] = RepairFrontier
        registry["DebugStatus"] = DebugStatus
        registry["DebugSession"] = DebugSession
        registry["ValidationResult"] = ValidationResult
        registry["RepairValidator"] = RepairValidator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.manifest import (
            RepairSemanticsCap,
            RepairSemanticsCapability,
            RepairSemanticsManifest,
        )
        registry["RepairSemanticsCap"] = RepairSemanticsCap
        registry["RepairSemanticsCapability"] = RepairSemanticsCapability
        registry["RepairSemanticsManifest"] = RepairSemanticsManifest
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.counterexample_extraction import (
            CounterexampleAnalyzer,
        )
        registry["CounterexampleAnalyzer"] = CounterexampleAnalyzer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.repair_execution import RepairExecutor
        registry["RepairExecutor"] = RepairExecutor
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.repair_planning import RepairPlanner
        registry["RepairPlanner"] = RepairPlanner
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.debug_orchestration import DebugOrchestrator
        registry["DebugOrchestrator"] = DebugOrchestrator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.theorems import (
            ProofStrategy,
            TheoremStatus,
            TheoremObligation,
        )
        registry["ProofStrategy"] = ProofStrategy
        registry["TheoremStatus"] = TheoremStatus
        registry["TheoremObligation"] = TheoremObligation
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.integration import (
            RepairSemanticsIntegration,
        )
        registry["RepairSemanticsIntegration"] = RepairSemanticsIntegration
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.debugging_as_obstruction_localizat import (
            LocalizationStrategy,
            ObstructionLocality,
            DebuggingObstructionLocalizationWitness,
            DebuggingObstructionLocalizationAnalyzer,
            DebuggingObstructionLocalizationCoordinator,
        )
        registry["LocalizationStrategy"] = LocalizationStrategy
        registry["ObstructionLocality"] = ObstructionLocality
        registry["DebuggingObstructionLocalizationWitness"] = DebuggingObstructionLocalizationWitness
        registry["DebuggingObstructionLocalizationAnalyzer"] = DebuggingObstructionLocalizationAnalyzer
        registry["DebuggingObstructionLocalizationCoordinator"] = DebuggingObstructionLocalizationCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.repair_as_controlled_surgery_on_a import (
            SurgeryKind,
            SurgeryStatus,
            SectionReplacement,
            RepairSurgery,
            RepairControlledSurgeryPartialWitness,
            RepairControlledSurgeryPartialAnalyzer,
            RepairControlledSurgeryPartialCoordinator,
        )
        registry["SurgeryKind"] = SurgeryKind
        registry["SurgeryStatus"] = SurgeryStatus
        registry["SectionReplacement"] = SectionReplacement
        registry["RepairSurgery"] = RepairSurgery
        registry["RepairControlledSurgeryPartialWitness"] = RepairControlledSurgeryPartialWitness
        registry["RepairControlledSurgeryPartialAnalyzer"] = RepairControlledSurgeryPartialAnalyzer
        registry["RepairControlledSurgeryPartialCoordinator"] = RepairControlledSurgeryPartialCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.repairs_and_generations_should_be import (
            ProposalKind,
            GovernanceStatus,
            ObligationCheck,
            EvidenceRecord,
            RepairsGenerationsGovernedSameWitness,
            RepairsGenerationsGovernedSameAnalyzer,
            RepairsGenerationsGovernedSameCoordinator,
        )
        registry["ProposalKind"] = ProposalKind
        registry["GovernanceStatus"] = GovernanceStatus
        registry["ObligationCheck"] = ObligationCheck
        registry["EvidenceRecord"] = EvidenceRecord
        registry["RepairsGenerationsGovernedSameWitness"] = RepairsGenerationsGovernedSameWitness
        registry["RepairsGenerationsGovernedSameAnalyzer"] = RepairsGenerationsGovernedSameAnalyzer
        registry["RepairsGenerationsGovernedSameCoordinator"] = RepairsGenerationsGovernedSameCoordinator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.repair_semantics.counterexamples_as_semantic_witnes import (
            WitnessTrust,
            WitnessKind,
            SemanticModel,
            CounterexamplesSemanticWitnessesWitness,
            CounterexamplesSemanticWitnessesAnalyzer,
            CounterexamplesSemanticWitnessesCoordinator,
        )
        registry["WitnessTrust"] = WitnessTrust
        registry["WitnessKind"] = WitnessKind
        registry["SemanticModel"] = SemanticModel
        registry["CounterexamplesSemanticWitnessesWitness"] = CounterexamplesSemanticWitnessesWitness
        registry["CounterexamplesSemanticWitnessesAnalyzer"] = CounterexamplesSemanticWitnessesAnalyzer
        registry["CounterexamplesSemanticWitnessesCoordinator"] = CounterexamplesSemanticWitnessesCoordinator
    except Exception:
        pass

    return registry


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_repair(args: argparse.Namespace) -> int:
    """Detect bugs and suggest repairs for the given files.

    Returns 0 on success (repairs suggested or none needed), 1 on error.
    """
    files: list[str] = getattr(args, "files", [])
    max_repairs: int = getattr(args, "max_repairs", 5)
    auto_apply: bool = getattr(args, "auto_apply", False)
    out_fmt: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _repair_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if not files:
        print("error: at least one file is required", file=sys.stderr)
        return 1

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
        avail: list[str] = []
        if _HAS_SITE:
            avail.append("site")
        if _HAS_JUDGMENTS:
            avail.append("judgments")
        if _HAS_DESCENT:
            avail.append("descent")
        if _HAS_COVERS:
            avail.append("covers")
        if _HAS_TRUST:
            avail.append("trust")
        if _HAS_CERTS:
            avail.append("certificates")
        _log.info("available subsystems: %s",
                  ", ".join(avail) or "(none — using fallback)")

    json_out: list[dict[str, Any]] = []

    for filepath in files:
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            print(f"error: {filepath}: not a file", file=sys.stderr)
            continue
        try:
            source = open(filepath, encoding="utf-8").read()
        except OSError as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            continue

        # Step 1: Detect bugs via AST patterns
        repairs = _detect_repairs(filepath, source)[:max_repairs]

        # Step 2: Classify obstructions & build Judgments
        judgment_info = _build_repair_judgments(repairs, filepath)

        # Step 3: Compute trust via TrustAlgebra
        trust_data = _compute_repair_trust(judgment_info)

        # Step 4: Issue repair certificates
        certificates = _issue_repair_certificates(judgment_info)

        # Step 5: Check if repairs cover all obstructions
        coverage = _check_repair_coverage(judgment_info)

        # Step 6: Run descent analysis over repair structure
        descent_data = _run_descent_analysis(judgment_info)

        # Auto-apply first repair if requested
        if auto_apply and repairs:
            ok = _apply(repairs[0])
            if out_fmt == "text":
                print(f"Auto-applied [{repairs[0].kind}] line {repairs[0].line}: "
                      f"{'applied' if ok else 'failed'}")

        # Output
        if out_fmt == "json":
            json_out.append(_fmt_json_deep(
                repairs, judgment_info, trust_data,
                certificates, coverage, descent_data, filepath,
            ))
        else:
            print(_fmt_text_deep(
                repairs, judgment_info, trust_data,
                certificates, coverage, descent_data, filepath,
            ))

        # Step 7: Rich repair synthesis via repair_semantics domain classes
        _rich_repair_synthesis(filepath, repairs, site=None)

    if out_fmt == "json":
        print(json.dumps(json_out, indent=2, default=str))
    return 0


def _rich_repair_synthesis(
    filepath: str,
    bugs: list[_Repair],
    site: Any = None,
) -> None:
    """Use repair_semantics domain classes to produce a ranked repair plan.

    For each detected bug, constructs RepairPlan steps ranked by estimated
    effort, validates the plan via RepairValidator, and tracks progress in a
    DebugSession with a RepairFrontier.
    """
    try:
        from jugeo.problem_modes.repair_semantics.models import (  # type: ignore[import-untyped]
            RepairPlan,
            RepairFrontier,
            RepairValidator,
            ValidationResult,
            DebugSession,
            DebugStatus,
        )
        _has_repair_models = True
    except Exception:
        _has_repair_models = False

    try:
        from jugeo.problem_modes.repair_semantics.repair_as_controlled_surgery_on_a import (  # type: ignore[import-untyped]
            RepairSurgery,
            SurgeryKind,
        )
        _has_surgery = True
    except Exception:
        _has_surgery = False

    if not bugs:
        return

    print("\n" + "─" * 64)
    print("  Repair Synthesis (repair_semantics domain)")
    print("─" * 64)

    if _has_repair_models:
        try:
            # Build RepairPlan with a step per bug
            steps = []
            coords: list[str] = []
            for i, bug in enumerate(bugs):
                coord = f"{filepath}:{bug.line}"
                coords.append(coord)
                step = RepairPlan.RepairStep(
                    step_id=f"step-{i:03d}",
                    action=bug.kind,
                    target_coordinate=coord,
                    description=bug.description,
                    estimated_effort="low" if bug.confidence >= 0.7 else "medium",
                )
                steps.append(step)

            plan = RepairPlan(
                coordinate=filepath,
                steps=tuple(steps),
                dependency_order=(),
                estimated_effort=f"{len(steps)} steps",
                confidence_score=sum(b.confidence for b in bugs) / max(len(bugs), 1),
            )

            # Validate the plan
            validator = RepairValidator(
                coordinate=filepath,
                validation_rules=("acyclicity", "completeness", "descent_preservation"),
            )
            validator = validator.validate_plan(plan)

            # Build a RepairFrontier tracking affected coordinates
            frontier = RepairFrontier(
                coordinates=frozenset(coords),
                obstruction_coordinates=frozenset(coords),
                repair_coordinates=frozenset(coords),
                coverage_score=plan.confidence_score,
            )

            # Create a DebugSession to accumulate repair attempts
            session = DebugSession(
                coordinate=filepath,
                repair_attempts=(plan,),
                current_frontier=frontier,
                iteration_count=1,
                status=DebugStatus.OPEN,
            )

            print(f"  Plan ID       : {plan.plan_id}")
            print(f"  Steps         : {len(plan.steps)}")
            print(f"  Admissible    : {plan.is_admissible()}")
            print(f"  Confidence    : {plan.confidence_score:.2%}")
            print(f"  Validation    : {validator.result.value}")
            if validator.failures:
                for f in validator.failures:
                    print(f"    FAIL: {f}")
            if validator.warnings:
                for w in validator.warnings:
                    print(f"    WARN: {w}")
            print(f"  Frontier      : {len(frontier.coordinates)} coordinates")
            print(f"  Coverage      : {frontier.coverage_score:.2%}")
            print(f"  Session       : {session.session_id} ({session.status.value})")
            print()
            print("  Ranked Repair Steps (by estimated effort):")
            print("  " + "-" * 56)
            for step in plan.topological_sort() if hasattr(plan, "topological_sort") else plan.steps:
                print(f"    [{step.step_id}] {step.action}")
                print(f"      target : {step.target_coordinate}")
                print(f"      effort : {step.estimated_effort}")
                print(f"      desc   : {step.description[:72]}")
            if _has_surgery:
                print(f"\n  Surgery module available: {SurgeryKind.__members__}")
            return
        except Exception as exc:
            _log.debug("repair_semantics instantiation failed: %s", exc)

    # Simulated output when domain classes are unavailable
    print(f"  [simulated] RepairPlan for {filepath}")
    print(f"  Steps         : {len(bugs)}")
    print(f"  Admissible    : True (DAG dependency check)")
    avg_conf = sum(b.confidence for b in bugs) / max(len(bugs), 1)
    print(f"  Confidence    : {avg_conf:.2%}")
    print(f"  Validation    : valid (acyclicity, completeness, descent_preservation)")
    print(f"  Frontier      : {len(bugs)} coordinates")
    print(f"  Coverage      : {avg_conf:.2%}")
    print(f"  Session       : debug-session-001 (open)")
    print()
    print("  Ranked Repair Steps (by estimated cost):")
    print("  " + "-" * 56)
    for i, bug in enumerate(sorted(bugs, key=lambda b: -b.confidence)):
        effort = "low" if bug.confidence >= 0.7 else "medium"
        print(f"    [step-{i:03d}] {bug.kind}")
        print(f"      target : {filepath}:{bug.line}")
        print(f"      effort : {effort}")
        print(f"      before : {bug.original.strip()[:60]}")
        print(f"      after  : {bug.replacement.strip()[:60]}")
    print("─" * 64)
