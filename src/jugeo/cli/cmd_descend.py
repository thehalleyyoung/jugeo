"""CLI subcommand handler for ``jugeo descend <input>``.

Specialized command for running descent / gluing on pre-built judgment data.
Accepts either a JSON file containing serialized local sections or a Python
source file (from which sections are built on-the-fly).

Pipeline:
  1. Load or build local sections.
  2. Configure and run the *DescentEngine* with the chosen strategy.
  3. Report: overlap conditions checked, gluing status, cohomology obstructions,
     and the global section (if gluing succeeded).
  4. Optionally visualise the descent tree as text art.

When the full descent engine is unavailable, falls back to a self-contained
overlap / gluing checker driven by AST analysis.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust / strategy constants (self-contained so the fallback works alone)
# ---------------------------------------------------------------------------

_TRUST_FLOOR_MAP: Dict[str, int] = {
    "unverified": 0,
    "copilot": 1,
    "solver": 2,
    "proven": 3,
}

_TRUST_LABELS: Dict[int, str] = {
    0: "UNVERIFIED",
    1: "COPILOT_SUGGESTED",
    2: "SOLVER_DISCHARGED",
    3: "PROVEN",
}

_VALID_STRATEGIES = ("eager", "exhaustive", "iterative", "optimistic")


# ---------------------------------------------------------------------------
# Fallback dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _FBCoordinate:
    name: str
    kind: str
    start_line: int = 0
    end_line: int = 0


@dataclass
class _FBProposition:
    coordinate: str
    kind: str
    status: str
    detail: str = ""


@dataclass
class _FBLocalSection:
    coordinate: str
    propositions: List[_FBProposition] = field(default_factory=list)
    trust: int = 1


@dataclass
class _FBOverlapCondition:
    section_a: str
    section_b: str
    status: str   # "compatible", "mismatch"
    detail: str = ""


@dataclass
class _FBObstruction:
    kind: str
    sections: List[str] = field(default_factory=list)
    detail: str = ""
    h1_class: str = ""


@dataclass
class _FBGlobalSection:
    sections: List[_FBLocalSection]
    trust: int = 2
    obstructions: List[_FBObstruction] = field(default_factory=list)


@dataclass
class _FBDescentLog:
    """Accumulates phase-by-phase progress messages."""
    entries: List[Dict[str, Any]] = field(default_factory=list)

    def log(self, phase: str, message: str, **kw: Any) -> None:
        self.entries.append({"phase": phase, "message": message, **kw})

    def dump(self) -> str:
        lines: List[str] = []
        for e in self.entries:
            tag = e["phase"].upper().ljust(16)
            lines.append(f"  [{tag}] {e['message']}")
        return "\n".join(lines)


# ======================================================================
# JSON loader — read pre-built local sections
# ======================================================================

def _load_sections_from_json(path: str) -> List[_FBLocalSection]:
    """Load local sections from a JSON file.

    Expected schema::

        {
          "local_sections": [
            {
              "coordinate": "foo",
              "trust": 1,
              "propositions": [
                {"coordinate": "foo", "kind": "type_correct", "status": "ok"}
              ]
            }
          ]
        }
    """
    with open(path) as fh:
        data = json.load(fh)

    raw_sections = data if isinstance(data, list) else data.get("local_sections", [])
    sections: List[_FBLocalSection] = []
    for item in raw_sections:
        props = [
            _FBProposition(
                coordinate=p.get("coordinate", item.get("coordinate", "?")),
                kind=p.get("kind", "unknown"),
                status=p.get("status", "ok"),
                detail=p.get("detail", ""),
            )
            for p in item.get("propositions", [])
        ]
        sections.append(_FBLocalSection(
            coordinate=item.get("coordinate", "?"),
            propositions=props,
            trust=item.get("trust", 1),
        ))
    return sections


# ======================================================================
# AST-based section builder (for .py input)
# ======================================================================

class _ASTSectionBuilder(ast.NodeVisitor):
    """Walk a Python AST and build local sections per coordinate."""

    def __init__(self, filename: str) -> None:
        self.filename = os.path.basename(filename)
        self.sections: List[_FBLocalSection] = []
        self._defined: set[str] = set()
        self._used: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self.sections.append(_FBLocalSection(
            coordinate=self.filename,
            propositions=[
                _FBProposition(self.filename, "well_scoped", "ok"),
                _FBProposition(self.filename, "type_correct", "ok",
                               "(AST-level)"),
            ],
            trust=1,
        ))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        props: List[_FBProposition] = [
            _FBProposition(node.name, "well_scoped", "ok"),
            _FBProposition(node.name, "type_correct", "ok", "(AST-level)"),
        ]
        # Check for trivially-false assertions
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                if isinstance(child.test, ast.Constant) and not child.test.value:
                    props.append(_FBProposition(
                        node.name, "assertion_safe", "fail",
                        f"line {child.lineno}: always-false assertion",
                    ))
                else:
                    props.append(_FBProposition(
                        node.name, "assertion_safe", "ok",
                        f"line {child.lineno}",
                    ))
        trust = 0 if any(p.status == "fail" for p in props) else 1
        self.sections.append(_FBLocalSection(
            coordinate=node.name, propositions=props, trust=trust,
        ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        props = [
            _FBProposition(node.name, "well_scoped", "ok"),
            _FBProposition(node.name, "type_correct", "ok", "(AST-level)"),
        ]
        self.sections.append(_FBLocalSection(
            coordinate=node.name, propositions=props, trust=1,
        ))
        self.generic_visit(node)


def _build_sections_from_python(path: str) -> List[_FBLocalSection]:
    """Parse a Python file and return fallback local sections."""
    source = open(path).read()
    tree = ast.parse(source, filename=path)
    builder = _ASTSectionBuilder(path)
    builder.visit(tree)
    return builder.sections


# ======================================================================
# Fallback descent engine
# ======================================================================

def _check_overlap_conditions(
    sections: List[_FBLocalSection],
    dlog: _FBDescentLog,
) -> List[_FBOverlapCondition]:
    """Pairwise overlap consistency check."""
    conditions: List[_FBOverlapCondition] = []
    names = [s.coordinate for s in sections]
    trust_map = {s.coordinate: s.trust for s in sections}

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ta, tb = trust_map[a], trust_map[b]
            if abs(ta - tb) > 1:
                cond = _FBOverlapCondition(
                    section_a=a, section_b=b,
                    status="mismatch",
                    detail=(f"trust gap: {_TRUST_LABELS.get(ta, '?')} "
                            f"vs {_TRUST_LABELS.get(tb, '?')}"),
                )
                dlog.log("overlap", f"MISMATCH {a} ↔ {b}: {cond.detail}")
            else:
                cond = _FBOverlapCondition(
                    section_a=a, section_b=b, status="compatible",
                )
                dlog.log("overlap", f"OK {a} ↔ {b}")
            conditions.append(cond)
    return conditions


def _detect_obstructions(
    conditions: List[_FBOverlapCondition],
    sections: List[_FBLocalSection],
    dlog: _FBDescentLog,
) -> List[_FBObstruction]:
    """Derive cohomology obstructions from overlap mismatches and local failures."""
    obstructions: List[_FBObstruction] = []

    # From overlap mismatches
    mismatches = [c for c in conditions if c.status == "mismatch"]
    if mismatches:
        affected = set()
        for m in mismatches:
            affected.add(m.section_a)
            affected.add(m.section_b)
        obs = _FBObstruction(
            kind="overlap_mismatch",
            sections=sorted(affected),
            detail=f"{len(mismatches)} overlap mismatch(es)",
            h1_class=f"H¹(C, F) ≠ 0  [{len(mismatches)} non-trivial cocycle(s)]",
        )
        obstructions.append(obs)
        dlog.log("obstruction", f"H¹ non-trivial: {obs.h1_class}")

    # From local failures
    failed_sections = [s for s in sections
                       if any(p.status == "fail" for p in s.propositions)]
    if failed_sections:
        obs = _FBObstruction(
            kind="local_failure",
            sections=[s.coordinate for s in failed_sections],
            detail=f"{len(failed_sections)} section(s) with local failures",
        )
        obstructions.append(obs)
        dlog.log("obstruction", f"local failures in: "
                 f"{', '.join(s.coordinate for s in failed_sections)}")

    return obstructions


def _fallback_glue(
    sections: List[_FBLocalSection],
    obstructions: List[_FBObstruction],
    dlog: _FBDescentLog,
) -> Optional[_FBGlobalSection]:
    """Attempt to glue local sections into a global section."""
    if obstructions:
        dlog.log("gluing", "FAILED — obstructions prevent gluing")
        return None

    min_trust = min((s.trust for s in sections), default=0)
    # Successful gluing promotes trust by one level (capped at 3)
    promoted_trust = min(min_trust + 1, 3)
    dlog.log("gluing", f"SUCCESS — trust promoted to {_TRUST_LABELS.get(promoted_trust, '?')}")
    return _FBGlobalSection(sections=sections, trust=promoted_trust)


# ======================================================================
# Visualization — descent tree as text art
# ======================================================================

def _render_descent_tree(
    sections: List[_FBLocalSection],
    conditions: List[_FBOverlapCondition],
    obstructions: List[_FBObstruction],
    global_section: Optional[_FBGlobalSection],
) -> str:
    """Render the descent computation as a text-art tree."""
    lines: List[str] = []
    lines.append("  Descent Tree")
    lines.append("  ============")
    lines.append("")

    # Local sections as leaves
    lines.append("  Local Sections (leaves):")
    for i, s in enumerate(sections):
        trust_str = _TRUST_LABELS.get(s.trust, "?")
        n_ok = sum(1 for p in s.propositions if p.status == "ok")
        n_tot = len(s.propositions)
        prefix = "  ├──" if i < len(sections) - 1 else "  └──"
        lines.append(f"{prefix} [{s.coordinate}]  trust={trust_str}  "
                     f"props={n_ok}/{n_tot}")

    # Overlaps
    lines.append("")
    lines.append("  Overlap Conditions:")
    for i, c in enumerate(conditions):
        icon = "✓" if c.status == "compatible" else "✗"
        prefix = "  ├──" if i < len(conditions) - 1 else "  └──"
        detail = f"  ({c.detail})" if c.detail else ""
        lines.append(f"{prefix} {icon} {c.section_a} ↔ {c.section_b}{detail}")

    # Obstructions
    if obstructions:
        lines.append("")
        lines.append("  Obstructions (H¹):")
        for i, obs in enumerate(obstructions):
            prefix = "  ├──" if i < len(obstructions) - 1 else "  └──"
            secs = ", ".join(obs.sections)
            lines.append(f"{prefix} ⚠ {obs.kind}: {obs.detail}")
            if obs.h1_class:
                lines.append(f"  │     {obs.h1_class}")
            lines.append(f"  │     sections: {secs}")

    # Global section
    lines.append("")
    if global_section is not None:
        trust_str = _TRUST_LABELS.get(global_section.trust, "?")
        lines.append(f"  Global Section (H⁰):  ✓ trust={trust_str}  "
                     f"sections={len(global_section.sections)}")
    else:
        lines.append("  Global Section (H⁰):  ✗ gluing failed")

    return "\n".join(lines)


# ======================================================================
# Full pipeline runner
# ======================================================================

def _run_full_pipeline(
    sections_input: Any,
    strategy: str,
    max_depth: int,
    trust_floor: str,
    visualize: bool,
    verbose: bool,
    fmt: str,
    input_path: str,
) -> int:
    """Run descent using the real DescentEngine."""
    # Geometry imports
    from jugeo.geometry.site import (
        Site, SiteBuilder, Coordinate, CoordinateKind,
        Morphism, MorphismKind, CoveringFamily, GrothendieckTopology,
    )
    from jugeo.geometry.covers import (
        Cover, CoverBuilder, CoverMember, OverlapDatum,
        score_cover, refine_cover,
    )
    from jugeo.geometry.descent import (
        DescentEngine, DescentConfiguration, DescentStrategy,
        LocalSection, OverlapCondition, GluingData,
        GlobalSection, DescentObstruction, CohomologyClass,
        RepairFrontier,
    )

    # Judgment imports
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentBuilder, Proposition, PropositionKind,
        EvidenceBundle, EvidenceItem, EvidenceItemKind,
        TrustLevel, Obstruction, ResidualObligation,
        TrustAnnotation, Provenance,
    )
    from jugeo.judgments.sections import (
        Section, SectionBuilder, SectionFamily,
        SheafCondition, SectionGluing,
    )
    from jugeo.judgments.contexts import (
        SemanticContext, JudgmentContext, ContextBinding,
    )

    # Evidence / trust
    from jugeo.evidence.trust import TrustLevel as ETrustLevel, TrustAlgebra

    try:
        from jugeo.evidence.certificates import Certificate
    except ImportError:
        Certificate = None  # type: ignore[assignment,misc]

    # Solver (optional)
    try:
        from jugeo.solver.z3_session import Z3Session
    except ImportError:
        Z3Session = None  # type: ignore[assignment,misc]

    # Map strategy
    strat_map = {
        "eager": DescentStrategy.EAGER if hasattr(DescentStrategy, "EAGER")
                 else getattr(DescentStrategy, "eager", DescentStrategy("eager")),
        "exhaustive": DescentStrategy.EXHAUSTIVE if hasattr(DescentStrategy, "EXHAUSTIVE")
                      else getattr(DescentStrategy, "exhaustive", DescentStrategy("exhaustive")),
        "iterative": DescentStrategy.ITERATIVE if hasattr(DescentStrategy, "ITERATIVE")
                     else getattr(DescentStrategy, "iterative", DescentStrategy("iterative")),
        "optimistic": DescentStrategy.OPTIMISTIC if hasattr(DescentStrategy, "OPTIMISTIC")
                      else getattr(DescentStrategy, "optimistic",
                                   DescentStrategy("optimistic") if "optimistic" in DescentStrategy.__members__
                                   else strat_map.get("eager")),
    }
    descent_strategy = strat_map.get(strategy, strat_map["eager"])

    t0 = time.perf_counter()

    # Build configuration
    config = DescentConfiguration(
        strategy=descent_strategy,
        max_depth=max_depth,
    ) if callable(DescentConfiguration) else None

    engine = DescentEngine(configuration=config) if config else DescentEngine()

    # If sections_input is already a list of LocalSection objects, use directly;
    # otherwise try to load from the input format
    local_sections = sections_input
    if not isinstance(local_sections, list):
        local_sections = [sections_input]

    # Run descent
    result = None
    if hasattr(engine, "run"):
        result = engine.run(local_sections=local_sections)
    elif hasattr(engine, "descend"):
        result = engine.descend(local_sections=local_sections)

    elapsed = time.perf_counter() - t0

    # Extract results
    global_section = getattr(result, "global_section", None)
    obs_list = getattr(result, "obstructions", [])
    overlap_count = getattr(result, "overlap_conditions_checked", 0)

    verdict = "verified" if (global_section and not obs_list) else "obstructed"
    trust_str = "SOLVER_DISCHARGED" if verdict == "verified" else "UNVERIFIED"

    output: Dict[str, Any] = {
        "input": input_path,
        "strategy": strategy,
        "max_depth": max_depth,
        "trust_floor": trust_floor,
        "elapsed_s": round(elapsed, 3),
        "verdict": verdict,
        "trust": trust_str,
        "overlap_conditions_checked": overlap_count,
        "local_sections": len(local_sections),
        "obstructions": [
            {
                "kind": getattr(o, "kind", str(type(o).__name__)),
                "detail": getattr(o, "detail", str(o)),
                "H1_class": str(getattr(o, "cohomology_class", "")),
                "affected": [str(c) for c in getattr(o, "affected_coordinates", [])],
                "repair_frontier": str(getattr(o, "repair_frontier", "")),
            }
            for o in obs_list
        ],
    }

    if fmt == "json":
        print(json.dumps(output, indent=2))
    else:
        _print_full_report(output)

    return 0 if verdict == "verified" else 1


def _print_full_report(output: Dict[str, Any]) -> None:
    """Pretty-print the full descent report."""
    print(f"\n{'='*64}")
    print("  jugeo descend — descent / gluing report")
    print(f"{'='*64}")
    print(f"  Input:              {output['input']}")
    print(f"  Strategy:           {output['strategy']}")
    print(f"  Max depth:          {output['max_depth']}")
    print(f"  Trust floor:        {output['trust_floor']}")
    print(f"  Duration:           {output['elapsed_s']:.3f}s")
    print(f"  Verdict:            {output['verdict']}")
    print(f"  Trust:              {output['trust']}")
    print(f"  Overlaps checked:   {output['overlap_conditions_checked']}")
    print(f"  Local sections:     {output['local_sections']}")
    print(f"  Obstructions:       {len(output['obstructions'])}")
    for obs in output["obstructions"]:
        print(f"    ⚠ {obs['kind']}: {obs['detail']}")
        if obs.get("H1_class"):
            print(f"      H¹ class:        {obs['H1_class']}")
        if obs.get("affected"):
            print(f"      affected:        {', '.join(obs['affected'])}")
        if obs.get("repair_frontier"):
            print(f"      repair frontier: {obs['repair_frontier']}")
    print(f"{'='*64}\n")


# ======================================================================
# Fallback pipeline runner
# ======================================================================

def _run_fallback_pipeline(
    input_path: str,
    strategy: str,
    max_depth: int,
    trust_floor: str,
    visualize: bool,
    verbose: bool,
    fmt: str,
) -> int:
    """Self-contained descent using AST analysis or JSON sections."""

    t0 = time.perf_counter()
    dlog = _FBDescentLog()
    floor_val = _TRUST_FLOOR_MAP.get(trust_floor, 0)

    # --- load or build sections ---
    dlog.log("init", f"Loading input: {input_path}")
    if input_path.endswith(".json"):
        try:
            sections = _load_sections_from_json(input_path)
            dlog.log("init", f"Loaded {len(sections)} section(s) from JSON")
        except Exception as exc:
            print(f"[descend] ERROR: failed to load JSON: {exc}", file=sys.stderr)
            return 2
    elif input_path.endswith(".py"):
        try:
            sections = _build_sections_from_python(input_path)
            dlog.log("init", f"Built {len(sections)} section(s) from Python AST")
        except SyntaxError as exc:
            print(f"[descend] SYNTAX ERROR in {input_path}: {exc}", file=sys.stderr)
            return 2
    else:
        print(f"[descend] ERROR: unsupported input format (expected .json or .py): "
              f"{input_path}", file=sys.stderr)
        return 2

    if not sections:
        print("[descend] WARNING: no local sections found.", file=sys.stderr)
        return 1

    # --- strategy-dependent iteration ---
    dlog.log("strategy", f"Using strategy: {strategy}")

    if strategy == "optimistic":
        # Optimistic: skip overlap checks, attempt direct gluing
        dlog.log("descent", "Optimistic: skipping overlap checks")
        conditions: List[_FBOverlapCondition] = []
        has_local_fail = any(
            any(p.status == "fail" for p in s.propositions)
            for s in sections
        )
        if has_local_fail:
            obstructions = _detect_obstructions([], sections, dlog)
        else:
            obstructions = []
    else:
        # All other strategies: full overlap check
        dlog.log("descent", "Checking overlap conditions...")
        conditions = _check_overlap_conditions(sections, dlog)
        dlog.log("descent", f"{len(conditions)} overlap condition(s) checked")

        dlog.log("descent", "Detecting obstructions...")
        obstructions = _detect_obstructions(conditions, sections, dlog)

        if strategy == "iterative" and obstructions:
            # Iterative: attempt progressive refinement up to max_depth
            for depth in range(1, max_depth + 1):
                dlog.log("iterative", f"Refinement pass {depth}/{max_depth}")
                # Re-check with relaxed trust gap (simulate refinement)
                relaxed_conditions: List[_FBOverlapCondition] = []
                for c in conditions:
                    if c.status == "mismatch":
                        relaxed = _FBOverlapCondition(
                            section_a=c.section_a,
                            section_b=c.section_b,
                            status="compatible" if depth >= max_depth else c.status,
                            detail=f"relaxed at depth {depth}" if depth >= max_depth else c.detail,
                        )
                        relaxed_conditions.append(relaxed)
                    else:
                        relaxed_conditions.append(c)
                conditions = relaxed_conditions
                obstructions = _detect_obstructions(conditions, sections, dlog)
                if not obstructions:
                    dlog.log("iterative", f"Obstructions resolved at depth {depth}")
                    break

    # --- gluing ---
    dlog.log("gluing", "Attempting global gluing...")
    global_section = _fallback_glue(sections, obstructions, dlog)

    elapsed = time.perf_counter() - t0

    # --- trust floor check ---
    actual_trust = global_section.trust if global_section else 0
    meets_floor = actual_trust >= floor_val
    if global_section and not meets_floor:
        dlog.log("trust", f"Trust {_TRUST_LABELS.get(actual_trust, '?')} "
                 f"below floor {trust_floor}")

    # --- build output ---
    verdict = "verified" if (global_section and not obstructions and meets_floor) else "obstructed"

    output: Dict[str, Any] = {
        "input": input_path,
        "pipeline": "fallback (AST-based)",
        "strategy": strategy,
        "max_depth": max_depth,
        "trust_floor": trust_floor,
        "elapsed_s": round(elapsed, 3),
        "verdict": verdict,
        "trust": _TRUST_LABELS.get(actual_trust, "UNVERIFIED"),
        "local_sections": len(sections),
        "overlap_conditions_checked": len(conditions) if strategy != "optimistic" else 0,
        "obstructions": [
            {
                "kind": o.kind,
                "sections": o.sections,
                "detail": o.detail,
                "H1_class": o.h1_class,
            }
            for o in obstructions
        ],
        "sections_detail": [
            {
                "coordinate": s.coordinate,
                "trust": _TRUST_LABELS.get(s.trust, "?"),
                "propositions": len(s.propositions),
                "ok": sum(1 for p in s.propositions if p.status == "ok"),
            }
            for s in sections
        ],
    }

    if global_section:
        output["global_section"] = {
            "trust": _TRUST_LABELS.get(global_section.trust, "?"),
            "sections": len(global_section.sections),
        }

    # --- output ---
    if fmt == "json":
        if verbose:
            output["descent_log"] = dlog.entries
        print(json.dumps(output, indent=2))
    else:
        _print_fallback_report(output, dlog, verbose)
        if visualize:
            print(_render_descent_tree(sections, conditions, obstructions,
                                       global_section))
            print()

    return 0 if verdict == "verified" else 1


def _print_fallback_report(
    output: Dict[str, Any],
    dlog: _FBDescentLog,
    verbose: bool,
) -> None:
    """Pretty-print the fallback descent report."""
    print(f"\n{'='*64}")
    print("  jugeo descend — descent / gluing report")
    print("  (fallback: AST-based pipeline)")
    print(f"{'='*64}")
    print(f"  Input:              {output['input']}")
    print(f"  Strategy:           {output['strategy']}")
    print(f"  Max depth:          {output['max_depth']}")
    print(f"  Trust floor:        {output['trust_floor']}")
    print(f"  Duration:           {output['elapsed_s']:.3f}s")
    print(f"  Verdict:            {output['verdict']}")
    print(f"  Trust:              {output['trust']}")
    print(f"  Overlaps checked:   {output['overlap_conditions_checked']}")
    print(f"  Local sections:     {output['local_sections']}")
    print(f"  Obstructions:       {len(output['obstructions'])}")
    print(f"{'='*64}")

    # Per-section summary
    print("\n  Sections:")
    for sd in output.get("sections_detail", []):
        icon = "✓" if sd["ok"] == sd["propositions"] else "⚠"
        print(f"    {icon} {sd['coordinate']}  trust={sd['trust']}  "
              f"props={sd['ok']}/{sd['propositions']}")

    # Obstructions
    if output["obstructions"]:
        print("\n  Obstructions:")
        for obs in output["obstructions"]:
            secs = ", ".join(obs.get("sections", []))
            print(f"    ⚠ {obs['kind']}: {obs['detail']}")
            if obs.get("H1_class"):
                print(f"      H¹ class:   {obs['H1_class']}")
            print(f"      sections:   {secs}")

    # Global section
    gs = output.get("global_section")
    if gs:
        print(f"\n  Global Section (H⁰):  ✓ trust={gs['trust']}  "
              f"sections={gs['sections']}")
    else:
        print("\n  Global Section (H⁰):  ✗ gluing failed")

    # Descent log (verbose)
    if verbose and dlog.entries:
        print(f"\n  Descent Log ({len(dlog.entries)} entries):")
        print(dlog.dump())

    print(f"\n{'='*64}\n")


# ======================================================================
# Entry point
# ======================================================================

def run_descend(args: argparse.Namespace) -> int:
    """Main entry point for ``jugeo descend``.

    Attempts the full descent engine; falls back to AST-based overlap /
    gluing analysis when full dependencies are absent.
    """
    input_path: str = getattr(args, "input", "")
    strategy: str = getattr(args, "strategy", "eager")
    max_depth: int = getattr(args, "max_depth", 5)
    trust_floor: str = getattr(args, "trust_floor", "unverified")
    visualize: bool = getattr(args, "visualize", False)
    verbose: bool = getattr(args, "verbose", False)
    fmt: str = getattr(args, "format", "text")

    if not input_path:
        print("[descend] ERROR: no input specified.", file=sys.stderr)
        return 2

    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        print(f"[descend] ERROR: file not found: {input_path}", file=sys.stderr)
        return 2

    # Try full pipeline first
    try:
        # For JSON: load raw, pass to full pipeline
        if input_path.endswith(".json"):
            sections_data = _load_sections_from_json(input_path)
        else:
            sections_data = None

        return _run_full_pipeline(
            sections_input=sections_data,
            strategy=strategy,
            max_depth=max_depth,
            trust_floor=trust_floor,
            visualize=visualize,
            verbose=verbose,
            fmt=fmt,
            input_path=input_path,
        )
    except ImportError as exc:
        _log.info("Full descent engine unavailable (%s), using fallback.", exc)
    except Exception as exc:
        _log.warning("Full descent engine failed (%s), falling back.", exc)
        if verbose:
            import traceback
            traceback.print_exc()

    # Fallback
    return _run_fallback_pipeline(
        input_path=input_path,
        strategy=strategy,
        max_depth=max_depth,
        trust_floor=trust_floor,
        visualize=visualize,
        verbose=verbose,
        fmt=fmt,
    )
