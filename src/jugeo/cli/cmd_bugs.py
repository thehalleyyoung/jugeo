"""CLI subcommand handler for ``jugeo bugs <file> ...``.

Analyses Python source files for bugs by modelling them as sheaf-theoretic
descent problems over a Grothendieck site.  Each function/class becomes a
coordinate in the site; local judgments (type safety, resource discipline,
closure hygiene, etc.) are assigned to each coordinate as local sections;
the descent engine checks whether these sections glue into a globally
consistent program.  Gluing failures — non-trivial classes in
H¹(U, D) — are reported as bugs.

When the full ``jugeo.problem_modes.bug_detection`` pipeline is available
it is tried first.  Otherwise the geometric analysis runs directly.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_log = logging.getLogger(__name__)

# ── JuGeo subsystem imports (all guarded) ─────────────────────────────

try:
    from jugeo.geometry.site import (
        Site, SiteBuilder, Coordinate, CoordinateKind,
        Morphism, CoveringFamily, GrothendieckTopology,
    )
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentBuilder, Proposition, PropositionKind,
        EvidenceBundle, EvidenceItem, EvidenceItemKind,
        TrustLevel, Obstruction as JObstruction,
        ResidualObligation, Carrier, TrustAnnotation,
    )
    _HAS_JUDGMENTS = True
except Exception:
    _HAS_JUDGMENTS = False

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentConfiguration, LocalSection,
        OverlapCondition, CohomologyClass, DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

try:
    from jugeo.geometry.covers import Cover
    _HAS_COVERS = True
except Exception:
    _HAS_COVERS = False

try:
    from jugeo.evidence.trust import (
        TrustLevel as EvidenceTrustLevel,
        TrustAlgebra,
    )
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, FormulaKind, SolveOutcome
    _HAS_Z3 = True
except Exception:
    _HAS_Z3 = False

try:
    from jugeo.problem_modes.bug_detection import detect_bugs as _detect_bugs_full
    _HAS_FULL_PIPELINE = True
except Exception:
    _HAS_FULL_PIPELINE = False

# -- structured errors -------------------------------------------------------
try:
    from jugeo.errors import (  # type: ignore[import-untyped]
        FailureChain,
        FailureFilter,
        JudgmentError,
        DescentError,
        EncodingError,
    )
    _HAS_ERRORS = True
except Exception:
    _HAS_ERRORS = False

# -- encoding repair results ------------------------------------------------
try:
    from jugeo.encodings.sequence_mutation_encodings.algorithms import (  # type: ignore[import-untyped]
        RepairResult,
        WindowResult,
    )
    _HAS_REPAIR_RESULTS = True
except Exception:
    _HAS_REPAIR_RESULTS = False

# ── Constants ─────────────────────────────────────────────────────────

_SEVERITY_THRESHOLDS: dict[str, float] = {
    "all": 0.0,
    "low": 0.3,
    "medium": 0.5,
    "high": 0.7,
    "critical": 0.9,
}

_BUILTINS = frozenset({
    "print", "len", "range", "type", "id", "list", "dict", "set", "str",
    "int", "float", "bool", "tuple", "map", "filter", "zip", "sum", "min",
    "max", "abs", "open", "input", "hash", "iter", "next", "sorted",
    "reversed", "enumerate", "isinstance", "issubclass", "getattr",
    "setattr", "hasattr", "delattr", "callable", "repr", "format",
    "object", "super", "property", "staticmethod", "classmethod",
})


# ======================================================================
# 1. Build a Site from a Python AST
# ======================================================================

@dataclass
class _ASTEntity:
    """Minimal bookkeeping for an AST node that becomes a coordinate."""
    name: str
    kind: str  # "module" | "function" | "class"
    path: tuple[str, ...]
    lineno: int
    col: int
    node: ast.AST
    return_annotations: list[str] = field(default_factory=list)
    param_annotations: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)


def _collect_entities(tree: ast.Module, filename: str) -> list[_ASTEntity]:
    """Walk the AST and collect functions, classes, and the module itself."""
    basename = os.path.splitext(os.path.basename(filename))[0]
    entities: list[_ASTEntity] = [
        _ASTEntity(
            name=basename, kind="module", path=(basename,),
            lineno=1, col=0, node=tree,
        ),
    ]
    scope_stack: list[str] = [basename]

    def _annotation_str(node: ast.AST | None) -> str:
        if node is None:
            return ""
        return ast.dump(node) if not isinstance(node, ast.Constant) else str(node.value)

    def _visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            path = tuple(scope_stack) + (node.name,)
            ent = _ASTEntity(
                name=node.name, kind="function", path=path,
                lineno=node.lineno, col=node.col_offset, node=node,
            )
            ent.return_annotations.append(_annotation_str(node.returns))
            for arg in node.args.args + node.args.kwonlyargs:
                ent.param_annotations[arg.arg] = _annotation_str(arg.annotation)
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    ent.calls.append(child.func.id)
            entities.append(ent)
            scope_stack.append(node.name)
            for child in ast.iter_child_nodes(node):
                _visit(child)
            scope_stack.pop()
            return
        elif isinstance(node, ast.ClassDef):
            path = tuple(scope_stack) + (node.name,)
            entities.append(_ASTEntity(
                name=node.name, kind="class", path=path,
                lineno=node.lineno, col=node.col_offset, node=node,
            ))
            scope_stack.append(node.name)
            for child in ast.iter_child_nodes(node):
                _visit(child)
            scope_stack.pop()
            return
        for child in ast.iter_child_nodes(node):
            _visit(child)

    for child in ast.iter_child_nodes(tree):
        _visit(child)
    return entities


def _build_site(entities: list[_ASTEntity], filename: str) -> Any:
    """Build a JuGeo Site from collected AST entities.

    Each entity becomes a Coordinate; morphisms encode containment
    (class→method, module→function).  A CoveringFamily is created for
    every parent that has children, modelling the open cover of the
    program.
    """
    if not _HAS_SITE:
        return None

    kind_map = {
        "module": CoordinateKind.MODULE,
        "function": CoordinateKind.FUNCTION,
        "class": CoordinateKind.INTERFACE,
    }
    coords: dict[tuple[str, ...], Coordinate] = {}
    for ent in entities:
        coords[ent.path] = Coordinate(
            components=ent.path,
            kind=kind_map.get(ent.kind, CoordinateKind.REGION),
            metadata={"lineno": ent.lineno, "col": ent.col, "file": filename},
        )

    builder = SiteBuilder(label=filename)
    for coord in coords.values():
        builder.add_coordinate(coord)

    # Morphisms: child → parent (restriction)
    morphisms: list[Morphism] = []
    for path, coord in coords.items():
        parent_path = path[:-1]
        if parent_path in coords:
            m = Morphism(source=coord, target=coords[parent_path])
            morphisms.append(m)
            builder.add_morphism(m)

    # Covering families: each parent is covered by its children
    children_of: dict[tuple[str, ...], list[Morphism]] = {}
    for m in morphisms:
        parent_path = m.target.components
        children_of.setdefault(parent_path, []).append(m)
    for parent_path, child_morphisms in children_of.items():
        if len(child_morphisms) >= 1:
            family = CoveringFamily(
                base=coords[parent_path],
                members=child_morphisms,
                label=f"cover({coords[parent_path].name})",
            )
            builder.add_covering_family(family)

    return builder.build()


# ======================================================================
# 2. AST pattern checks (returns raw findings)
# ======================================================================

@dataclass
class _RawFinding:
    """A single bug finding from AST pattern analysis."""
    file: str
    line: int
    col: int
    kind: str
    severity: float
    description: str
    repair_hint: str
    entity_path: tuple[str, ...]


def _ast_pattern_scan(tree: ast.Module, entities: list[_ASTEntity],
                      filename: str) -> list[_RawFinding]:
    """Run all AST pattern checks and return raw findings."""
    findings: list[_RawFinding] = []

    def _enclosing(lineno: int) -> tuple[str, ...]:
        best = entities[0].path  # module
        for ent in entities:
            if ent.lineno <= lineno:
                if len(ent.path) > len(best):
                    best = ent.path
        return best

    # 1. Mutable default arguments
    for ent in entities:
        if ent.kind != "function":
            continue
        node = ent.node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            is_mutable = isinstance(default, (ast.List, ast.Dict, ast.Set))
            if not is_mutable and isinstance(default, ast.Call):
                if isinstance(default.func, ast.Name) and default.func.id in ("list", "dict", "set"):
                    is_mutable = True
            if is_mutable:
                findings.append(_RawFinding(
                    file=filename, line=getattr(default, "lineno", node.lineno),
                    col=getattr(default, "col_offset", 0),
                    kind="mutable_default", severity=0.7,
                    description=f"Mutable default argument in '{ent.name}'. "
                                "Use None and assign inside the function body.",
                    repair_hint=f"Replace mutable default with None in '{ent.name}'",
                    entity_path=ent.path,
                ))

    # 2. Bare except clauses
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(_RawFinding(
                file=filename, line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                kind="bare_except", severity=0.5,
                description="Bare 'except:' catches all exceptions including "
                            "KeyboardInterrupt and SystemExit.",
                repair_hint="Replace bare 'except:' with 'except Exception:'",
                entity_path=_enclosing(getattr(node, "lineno", 0)),
            ))

    # 3. Late-binding closures in loops
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        loop_vars: set[str] = set()
        for target in ast.walk(node.target):
            if isinstance(target, ast.Name):
                loop_vars.add(target.id)
        if not loop_vars:
            continue
        for child in ast.walk(node):
            if child is node or not isinstance(child, ast.Lambda):
                continue
            for ref in ast.walk(child.body):
                if isinstance(ref, ast.Name) and ref.id in loop_vars:
                    findings.append(_RawFinding(
                        file=filename, line=getattr(child, "lineno", 0),
                        col=getattr(child, "col_offset", 0),
                        kind="late_binding_closure", severity=0.8,
                        description=f"Lambda captures loop variable '{ref.id}' by reference.",
                        repair_hint=f"Bind with default: lambda {ref.id}={ref.id}: ...",
                        entity_path=_enclosing(getattr(child, "lineno", 0)),
                    ))
                    break

    # 4. Shadowing builtins at module scope
    scope_depth: dict[int, int] = {}  # node id -> depth
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in _BUILTINS:
                    findings.append(_RawFinding(
                        file=filename, line=t.lineno, col=t.col_offset,
                        kind="shadow_builtin", severity=0.6,
                        description=f"Assignment shadows the builtin '{t.id}'.",
                        repair_hint=f"Rename '{t.id}' to avoid shadowing the builtin",
                        entity_path=_enclosing(t.lineno),
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _BUILTINS:
                findings.append(_RawFinding(
                    file=filename, line=node.lineno, col=node.col_offset,
                    kind="shadow_builtin", severity=0.6,
                    description=f"Function '{node.name}' shadows the builtin.",
                    repair_hint=f"Rename function '{node.name}'",
                    entity_path=_enclosing(node.lineno),
                ))

    # 5. open() without context manager
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "open":
                findings.append(_RawFinding(
                    file=filename, line=node.lineno, col=node.col_offset,
                    kind="resource_leak", severity=0.65,
                    description="File opened without a context manager.",
                    repair_hint="Use 'with open(...) as f:' instead",
                    entity_path=_enclosing(node.lineno),
                ))

    # 6. Identity comparison with non-singleton literals
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Is, ast.IsNot)):
                continue
            op_str = "is" if isinstance(op, ast.Is) else "is not"
            if isinstance(comparator, ast.Constant):
                if comparator.value is None or isinstance(comparator.value, bool):
                    continue
                findings.append(_RawFinding(
                    file=filename, line=node.lineno, col=node.col_offset,
                    kind="identity_comparison", severity=0.6,
                    description=f"Using '{op_str}' to compare with literal "
                                f"{comparator.value!r}. Use '=='.",
                    repair_hint=f"Replace '{op_str}' with '==' for value comparison",
                    entity_path=_enclosing(node.lineno),
                ))
            elif isinstance(comparator, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                findings.append(_RawFinding(
                    file=filename, line=node.lineno, col=node.col_offset,
                    kind="identity_comparison", severity=0.6,
                    description=f"Using '{op_str}' with a literal collection.",
                    repair_hint=f"Replace '{op_str}' with '==' for value comparison",
                    entity_path=_enclosing(node.lineno),
                ))

    # 7. Cross-boundary type mismatches (call-site annotation checks)
    annotation_map: dict[str, _ASTEntity] = {ent.name: ent for ent in entities
                                              if ent.kind == "function"}
    for ent in entities:
        if ent.kind != "function":
            continue
        for callee_name in ent.calls:
            callee = annotation_map.get(callee_name)
            if callee is None:
                continue
            callee_node = callee.node
            if not isinstance(callee_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if callee_node.returns is None:
                findings.append(_RawFinding(
                    file=filename, line=ent.lineno, col=ent.col,
                    kind="unchecked_return", severity=0.55,
                    description=(f"'{ent.name}' calls '{callee_name}' which has "
                                 "no return type annotation — return value unchecked."),
                    repair_hint=f"Add return type annotation to '{callee_name}'",
                    entity_path=ent.path,
                ))

    return findings


# ======================================================================
# 3. Wrap findings as Judgment obstructions + LocalSections
# ======================================================================

@dataclass
class _BugRecord:
    """Final bug record with full geometric metadata."""
    file: str
    line: int
    col: int
    kind: str
    severity: float
    description: str
    coordinate_name: str
    cohomology_class: str
    trust_tier: str
    repair_hint: str
    obstruction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])


def _severity_to_kind(kind: str) -> str:
    """Map AST finding kinds to PropositionKind values."""
    mapping = {
        "mutable_default": "structural",
        "bare_except": "behavioral",
        "late_binding_closure": "behavioral",
        "shadow_builtin": "semantic",
        "resource_leak": "resource",
        "identity_comparison": "relational",
        "unchecked_return": "relational",
        "syntax_error": "structural",
    }
    return mapping.get(kind, "structural")


def _build_judgment_for_entity(
    ent: _ASTEntity, findings: list[_RawFinding], filename: str,
) -> Any | None:
    """Create a Judgment 8-tuple for one entity.

    The proposition asserts the entity is well-formed; obstructions
    record any findings that contradict this.
    """
    if not _HAS_JUDGMENTS or not _HAS_SITE:
        return None

    coord = Coordinate(
        components=ent.path,
        kind={
            "module": CoordinateKind.MODULE,
            "function": CoordinateKind.FUNCTION,
            "class": CoordinateKind.INTERFACE,
        }.get(ent.kind, CoordinateKind.REGION),
        metadata={"lineno": ent.lineno, "file": filename},
    )

    prop_kind = PropositionKind.STRUCTURAL
    formula = f"{ent.name} : well_formed"
    proposition = Proposition(kind=prop_kind, formula=formula)

    obstructions: list[JObstruction] = []
    for f in findings:
        obs = JObstruction(
            description=f.description,
            coordinate=".".join(f.entity_path),
            repair_hints=(f.repair_hint,),
            cohomology_class=f"H1({'.'.join(f.entity_path)}, {f.kind})",
            severity=int(f.severity * 10),
        )
        obstructions.append(obs)

    evidence_items: list[EvidenceItem] = []
    if findings:
        evidence_items.append(EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={"detector": "ast_pattern_scan", "finding_count": len(findings)},
            trust_level=TrustLevel.ORACLE_PROPOSED,
            channel="jugeo.cli.bugs",
        ))
    else:
        evidence_items.append(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload={"status": "no_findings"},
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            channel="jugeo.cli.bugs",
        ))

    obligations: list[ResidualObligation] = []
    for f in findings:
        obligations.append(ResidualObligation(
            description=f"Repair needed: {f.repair_hint}",
            required_evidence_kind=EvidenceItemKind.SOLVER_PROOF,
            priority=int(f.severity * 10),
        ))

    builder = (
        JudgmentBuilder()
        .at(coord)
        .claiming(proposition)
        .of_type_named(ent.kind)
    )
    for ei in evidence_items:
        builder = builder.with_evidence(ei)
    for obs in obstructions:
        builder = builder.with_obstruction(obs)
    for obl in obligations:
        builder = builder.with_obligation(obl)

    trust_level = TrustLevel.CONTRADICTED if findings else TrustLevel.RUNTIME_WITNESSED
    builder = builder.with_trust_level(trust_level)

    return builder.build()


def _build_local_sections(
    entities: list[_ASTEntity],
    findings_by_path: dict[tuple[str, ...], list[_RawFinding]],
) -> dict[str, dict[str, Any]]:
    """Create a LocalSection for each entity, keyed by coordinate path.

    The judgment_data encodes the entity's signature and any findings,
    so the descent engine can detect gluing failures on overlaps.
    """
    sections: dict[str, dict[str, Any]] = {}
    for ent in entities:
        key = "/".join(ent.path)
        ent_findings = findings_by_path.get(ent.path, [])
        judgment_data: dict[str, Any] = {
            "name": ent.name,
            "kind": ent.kind,
            "return_annotations": ent.return_annotations,
            "param_annotations": ent.param_annotations,
            "finding_kinds": sorted({f.kind for f in ent_findings}),
            "severity_max": max((f.severity for f in ent_findings), default=0.0),
            "obstructed": len(ent_findings) > 0,
        }

        if _HAS_DESCENT:
            ls = LocalSection(
                coordinate=key,
                judgment_data=judgment_data,
                evidence_bundle=tuple(
                    f"{f.kind}@L{f.line}" for f in ent_findings
                ),
                trust_level=0.0 if ent_findings else 1.0,
                provenance=("ast_pattern_scan",),
                is_partial=len(ent_findings) > 0,
                residual_obligations=[f.repair_hint for f in ent_findings],
            )
            _log.debug("LocalSection(%s): trust=%.1f partial=%s",
                        key, ls.trust_level, ls.is_partial)

        sections[key] = judgment_data
    return sections


# ======================================================================
# 4. Run descent to detect gluing failures
# ======================================================================

def _run_descent_analysis(
    entities: list[_ASTEntity],
    sections: dict[str, dict[str, Any]],
    filename: str,
) -> list[tuple[str, str, str, str]]:
    """Use DescentEngine to find gluing obstructions.

    Returns a list of (coordinate, cohomology_class, description, repair_hint)
    tuples — one per violated overlap.
    """
    if not (_HAS_DESCENT and _HAS_COVERS and _HAS_SITE):
        return []

    basename = os.path.splitext(os.path.basename(filename))[0]
    kind_map = {
        "module": CoordinateKind.MODULE,
        "function": CoordinateKind.FUNCTION,
        "class": CoordinateKind.INTERFACE,
    }
    coord_map: dict[str, Coordinate] = {}
    for ent in entities:
        key = "/".join(ent.path)
        coord_map[key] = Coordinate(
            components=ent.path,
            kind=kind_map.get(ent.kind, CoordinateKind.REGION),
        )

    # Build overlaps: every pair of siblings (children of same parent)
    children_of: dict[str, list[str]] = {}
    for ent in entities:
        parent_key = "/".join(ent.path[:-1]) if len(ent.path) > 1 else ""
        children_of.setdefault(parent_key, []).append("/".join(ent.path))

    overlap_pairs: list[tuple[str, str]] = []
    for parent_key, child_keys in children_of.items():
        for i, a in enumerate(child_keys):
            for b in child_keys[i + 1:]:
                overlap_pairs.append((a, b))
    # Also overlap each child with its parent
    for ent in entities:
        key = "/".join(ent.path)
        parent_key = "/".join(ent.path[:-1]) if len(ent.path) > 1 else ""
        if parent_key and parent_key in sections:
            overlap_pairs.append((key, parent_key))

    if not overlap_pairs:
        return []

    module_coord = coord_map.get(basename, Coordinate(
        components=(basename,), kind=CoordinateKind.MODULE,
    ))
    patches = tuple(coord_map.values())
    cover = Cover(
        target=module_coord,
        patches=patches,
        overlaps=tuple(overlap_pairs),
    )

    config = DescentConfiguration(
        depth_limit=3,
        overlap_check_timeout=5.0,
        strategy=DescentStrategy.EXHAUSTIVE,
    )
    engine = DescentEngine(configuration=config)

    _log.debug("Running descent with %d sections, %d overlaps",
               len(sections), len(overlap_pairs))
    report = engine.run(cover, sections)

    gluing_failures: list[tuple[str, str, str, str]] = []
    if not report.success:
        for obs in report.obstructions:
            left, right = obs.overlap
            left_data = sections.get(left, {})
            right_data = sections.get(right, {})
            # Determine the nature of the gluing failure
            desc_parts: list[str] = [obs.message]
            if left_data.get("obstructed") or right_data.get("obstructed"):
                desc_parts.append("(one or both sides carry local obstructions)")
            l_ret = left_data.get("return_annotations", [])
            r_params = right_data.get("param_annotations", {})
            if l_ret and r_params:
                desc_parts.append(
                    f"return annotations {l_ret} may conflict with param "
                    f"annotations {r_params}"
                )
            coord_name = f"{left} ∩ {right}"
            cohom = f"H1({coord_name}, D)"
            repair = (
                f"Reconcile the interface between "
                f"'{left.split('/')[-1]}' and '{right.split('/')[-1]}'"
            )
            gluing_failures.append((coord_name, cohom, "; ".join(desc_parts), repair))

    return gluing_failures


# ======================================================================
# 5. Assign trust levels via TrustAlgebra
# ======================================================================

def _assign_trust(severity: float, verified_by_solver: bool) -> tuple[str, str]:
    """Assign a trust tier and label using TrustAlgebra when available.

    Returns (tier_label, evidence_trust_label).
    """
    if _HAS_TRUST:
        algebra = TrustAlgebra()
        if verified_by_solver:
            level = EvidenceTrustLevel.SOLVER_DISCHARGED
        elif severity >= 0.7:
            level = EvidenceTrustLevel.ORACLE_PROPOSED
            level = algebra.demote(level, EvidenceTrustLevel.ORACLE_PROPOSED)
        else:
            level = EvidenceTrustLevel.COPILOT_SUGGESTED
        return level.label(), level.value
    # Fallback without TrustAlgebra
    if verified_by_solver:
        return "solver_discharged", "solver_discharged"
    elif severity >= 0.7:
        return "oracle_proposed", "oracle_proposed"
    return "copilot_suggested", "copilot_suggested"


# ======================================================================
# 6. Optional Z3 verification of bug conditions
# ======================================================================

def _try_z3_verify(finding: _RawFinding) -> bool:
    """Attempt to encode and verify the bug condition via Z3.

    Returns True if Z3 confirms the condition is satisfiable (i.e. the
    bug is real), False otherwise or if Z3 is unavailable.
    """
    if not _HAS_Z3:
        return False
    try:
        session = Z3Session(timeout_ms=2000)
        if finding.kind == "mutable_default":
            formula = Z3Formula(
                kind=FormulaKind.BOOL,
                expression=f"(and (is_mutable default_{finding.line}) "
                           f"(shared_across_calls default_{finding.line}))",
            )
        elif finding.kind == "resource_leak":
            formula = Z3Formula(
                kind=FormulaKind.BOOL,
                expression=f"(and (is_open fh_{finding.line}) "
                           f"(not (is_closed fh_{finding.line})))",
            )
        elif finding.kind == "late_binding_closure":
            formula = Z3Formula(
                kind=FormulaKind.BOOL,
                expression=f"(and (captures_by_ref lambda_{finding.line}) "
                           f"(loop_var_mutated v_{finding.line}))",
            )
        else:
            formula = Z3Formula(
                kind=FormulaKind.BOOL,
                expression=f"(bug_condition_{finding.kind}_{finding.line})",
            )
        session.assert_formula(formula)
        outcome = session.check_sat()
        _log.debug("Z3 check for %s@L%d: %s", finding.kind, finding.line, outcome)
        return outcome == SolveOutcome.SAT
    except Exception as exc:
        _log.debug("Z3 verification failed: %s", exc)
        return False


# ======================================================================
# 7. Full geometric analysis pipeline
# ======================================================================

def _analyse_file(source: str, filename: str, threshold: float) -> list[_BugRecord]:
    """Run the full geometric bug-detection pipeline on one file.

    Steps:
      1. Parse AST, collect entities.
      2. Build a Site from entities (coordinates + covering families).
      3. Run AST pattern checks → raw findings.
      4. Build LocalSections with judgment data.
      5. Run DescentEngine to detect gluing failures (H¹ obstructions).
      6. Wrap each finding as a Judgment obstruction.
      7. Assign trust via TrustAlgebra, optionally verify with Z3.
      8. Return BugRecords with full geometric metadata.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        trust_tier, _ = _assign_trust(1.0, False)
        return [_BugRecord(
            file=filename, line=exc.lineno or 1, col=exc.offset or 0,
            kind="syntax_error", severity=1.0,
            description=f"SyntaxError: {exc.msg}",
            coordinate_name=os.path.splitext(os.path.basename(filename))[0],
            cohomology_class="H1(module, syntax)",
            trust_tier=trust_tier,
            repair_hint="Fix the syntax error",
        )]

    # Step 1: collect entities
    entities = _collect_entities(tree, filename)
    _log.debug("Collected %d entities from %s", len(entities), filename)

    # Step 2: build site
    site = _build_site(entities, filename)
    if site is not None:
        _log.debug("Site: %d coordinates, %d morphisms",
                    site.coordinate_count(), site.morphism_count())

    # Step 3: AST pattern checks
    findings = _ast_pattern_scan(tree, entities, filename)
    _log.debug("AST scan found %d raw findings", len(findings))

    # Group findings by entity path
    findings_by_path: dict[tuple[str, ...], list[_RawFinding]] = {}
    for f in findings:
        findings_by_path.setdefault(f.entity_path, []).append(f)

    # Step 4: build local sections
    sections = _build_local_sections(entities, findings_by_path)

    # Step 5: run descent
    gluing_failures = _run_descent_analysis(entities, sections, filename)
    _log.debug("Descent found %d gluing failures", len(gluing_failures))

    # Step 6: build Judgment objects for each entity
    judgments: dict[tuple[str, ...], Any] = {}
    for ent in entities:
        ent_findings = findings_by_path.get(ent.path, [])
        j = _build_judgment_for_entity(ent, ent_findings, filename)
        if j is not None:
            judgments[ent.path] = j
            _log.debug("Judgment(%s): obstructions=%d trust=%s",
                        ent.name, j.unresolved_obstruction_count(),
                        j.trust_floor().label() if hasattr(j.trust_floor(), 'label') else j.trust_floor())

    # Step 7-8: produce BugRecords
    records: list[_BugRecord] = []
    seen: set[str] = set()

    # From AST findings
    for f in findings:
        if f.severity < threshold:
            continue
        z3_verified = _try_z3_verify(f)
        trust_tier, _ = _assign_trust(f.severity, z3_verified)
        coord_name = ".".join(f.entity_path)
        cohom = f"H1({coord_name}, {f.kind})"
        dedup_key = f"{f.file}:{f.line}:{f.kind}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        records.append(_BugRecord(
            file=f.file, line=f.line, col=f.col,
            kind=f.kind, severity=f.severity,
            description=f.description,
            coordinate_name=coord_name,
            cohomology_class=cohom,
            trust_tier=trust_tier,
            repair_hint=f.repair_hint,
        ))

    # From descent gluing failures
    for coord_name, cohom, desc, repair in gluing_failures:
        severity = 0.75
        if severity < threshold:
            continue
        trust_tier, _ = _assign_trust(severity, False)
        dedup_key = f"descent:{coord_name}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        records.append(_BugRecord(
            file=filename, line=0, col=0,
            kind="gluing_failure", severity=severity,
            description=f"Descent obstruction: {desc}",
            coordinate_name=coord_name,
            cohomology_class=cohom,
            trust_tier=trust_tier,
            repair_hint=repair,
        ))

    records.sort(key=lambda r: (-r.severity, r.line))
    return records


# ======================================================================
# Formatting helpers
# ======================================================================

def _format_text(records: list[_BugRecord]) -> str:
    if not records:
        return "No bugs found."
    lines: list[str] = []
    for r in records:
        loc = f"{r.file}:{r.line}:{r.col}" if r.line else r.file
        lines.append(
            f"{loc}: [{r.kind}] (severity {r.severity:.1f}, "
            f"trust={r.trust_tier}) {r.description}"
        )
        lines.append(f"  coordinate: {r.coordinate_name}")
        lines.append(f"  cohomology: {r.cohomology_class}")
        lines.append(f"  repair: {r.repair_hint}")
    lines.append(f"\n{len(records)} bug(s) found.")
    return "\n".join(lines)


def _format_json(records: list[_BugRecord]) -> str:
    return json.dumps({
        "bugs": [
            {
                "obstruction_id": r.obstruction_id,
                "file": r.file,
                "line": r.line,
                "col": r.col,
                "kind": r.kind,
                "severity": r.severity,
                "description": r.description,
                "coordinate": r.coordinate_name,
                "cohomology_class": r.cohomology_class,
                "trust_tier": r.trust_tier,
                "repair_hint": r.repair_hint,
            }
            for r in records
        ],
        "count": len(records),
    }, indent=2)


def _format_text_result(filepath: str, result: Any) -> str:
    """Format a BugDetectionResult from the full pipeline as text."""
    lines: list[str] = [f"── {filepath} ({result.status}) ──"]
    for bug in result.bugs:
        sev = f"{bug.severity:.1f}"
        lines.append(
            f"  {bug.coordinate}: [{bug.kind.value}] "
            f"(severity {sev}) {bug.description}"
        )
    return "\n".join(lines)


def _format_json_result(filepath: str, result: Any) -> dict[str, Any]:
    """Convert a BugDetectionResult from the full pipeline to dict."""
    return {
        "file": filepath,
        "status": result.status,
        "session_id": result.session_id,
        "elapsed_s": result.elapsed_s,
        "bugs": [
            {
                "bug_id": b.bug_id,
                "kind": b.kind.value,
                "coordinate": b.coordinate,
                "severity": b.severity,
                "description": b.description,
                "trust_tier": b.trust_tier,
                "cohomology_class": b.cohomology_class,
            }
            for b in result.bugs
        ],
    }


# ======================================================================
# Rich bug detection via problem_modes classes
# ======================================================================

def _rich_bug_detection(
    filepath: str,
    site: Any,
    judgments: dict[tuple[str, ...], Any],
) -> dict[str, Any] | None:
    """Use ``BugDetector`` from problem_modes to produce rich output.

    Returns a dict with structured results or *None* if the problem_modes
    classes are unavailable.  All imports are guarded inside the function
    body so the rest of the CLI never hard-depends on them.
    """
    try:
        from jugeo.problem_modes.bug_detection.detector import BugDetector
        from jugeo.problem_modes.bug_detection.models import (
            BugKind, BugReport, BugDetectionResult,
        )
    except Exception:
        return None

    try:
        detector = BugDetector()
        result: BugDetectionResult = detector.detect_bugs(
            filepath, is_path=True, filename=filepath,
        )
    except Exception as exc:
        _log.debug("BugDetector.detect_bugs failed: %s", exc)
        return None

    # Gather geometric obstructions from the detector
    obstruction_info: dict[str, Any] = {}
    try:
        obstruction_info = detector.as_site_obstructions()
    except Exception:
        pass

    entries: list[dict[str, Any]] = []
    for bug in result.bugs:
        severity_label = (
            "critical" if bug.severity >= 0.9
            else "high" if bug.severity >= 0.7
            else "medium" if bug.severity >= 0.5
            else "low"
        )
        # Extract category and pattern from kind / provenance
        category = bug.kind.value if isinstance(bug.kind, BugKind) else str(bug.kind)
        pattern = bug.provenance.get("pattern", bug.metadata.get("pattern", category))
        evidence_source = bug.provenance.get("source", "ast_pattern")
        trust_tier = bug.trust_tier

        # Build a human-readable obstruction string from cohomology data
        cohom = bug.cohomology_class or bug.compute_cohomology_class()
        coord_short = bug.coordinate.rsplit(".", 1)[-1] if bug.coordinate else "?"
        kind_short = category.split("_")[0] if "_" in category else category
        obstruction_str = (
            f"{cohom} — local sections over {coord_short} "
            f"fail to glue for {kind_short}"
        ) if cohom else ""

        # Try to extract line number from the coordinate string
        line = 0
        if bug.coordinate:
            parts = bug.coordinate.split(":")
            for p in parts:
                if p.isdigit():
                    line = int(p)
                    break

        entries.append({
            "bug_id": bug.bug_id,
            "severity_label": severity_label,
            "severity": bug.severity,
            "line": line,
            "description": bug.description,
            "category": category,
            "pattern": pattern,
            "trust_tier": trust_tier,
            "evidence_source": evidence_source,
            "cohomology_class": cohom,
            "obstruction": obstruction_str,
            "coordinate": bug.coordinate,
        })

    n_bugs = len(entries)
    n_obstructions = sum(1 for e in entries if e["obstruction"])
    return {
        "bugs": entries,
        "count": n_bugs,
        "obstruction_count": n_obstructions,
        "session_id": result.session_id,
        "status": result.status,
        "elapsed_s": result.elapsed_s,
        "site_obstructions": obstruction_info,
    }


def _format_rich_bug_text(filepath: str, rich: dict[str, Any]) -> str:
    """Format rich bug detection results as human-readable text."""
    lines: list[str] = [f"── {filepath} ──"]
    lines.append("  Bug detection via Čech cohomology:")
    if not rich["bugs"]:
        lines.append("    (no bugs detected)")
    for entry in rich["bugs"]:
        loc = f"line {entry['line']}" if entry["line"] else entry["coordinate"]
        lines.append(
            f"    ⚠ BUG [{entry['severity_label']}] {loc}: "
            f"{entry['description']}"
        )
        lines.append(
            f"      Category: {entry['category']} | "
            f"Pattern: {entry['pattern']}"
        )
        lines.append(
            f"      Evidence: trust={entry['trust_tier']}, "
            f"source={entry['evidence_source']}"
        )
        if entry["obstruction"]:
            lines.append(f"      Obstruction: {entry['obstruction']}")
    lines.append(
        f"  Total: {rich['count']} bug(s) detected, "
        f"{rich['obstruction_count']} non-trivial obstruction(s)"
    )
    return "\n".join(lines)


def _format_rich_bug_json(filepath: str, rich: dict[str, Any]) -> dict[str, Any]:
    """Format rich bug detection results as a JSON-friendly dict."""
    return {
        "file": filepath,
        "mode": "rich_problem_modes",
        "session_id": rich["session_id"],
        "status": rich["status"],
        "elapsed_s": rich["elapsed_s"],
        "bugs": rich["bugs"],
        "count": rich["count"],
        "obstruction_count": rich["obstruction_count"],
    }


# ======================================================================
# Registry
# ======================================================================


def _bug_detection_registry() -> dict[str, type]:
    registry: dict[str, type] = {}

    try:
        from jugeo.problem_modes.bug_detection.models import (
            BugKind,
            BugReport,
            BugDetectionResult,
            DetectionSession,
        )
        registry["BugKind"] = BugKind
        registry["BugReport"] = BugReport
        registry["BugDetectionResult"] = BugDetectionResult
        registry["DetectionSession"] = DetectionSession
    except Exception:
        pass

    try:
        from jugeo.problem_modes.bug_detection.detector import BugDetector
        registry["BugDetector"] = BugDetector
    except Exception:
        pass

    try:
        from jugeo.problem_modes.bug_detection.integration import BugDetectionOrchestrator
        registry["BugDetectionOrchestrator"] = BugDetectionOrchestrator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.bug_detection.ast_bridge import (
            ASTCoordinate,
            SymbolicNode,
            ASTBridgeConfig,
            PythonASTBridge,
        )
        registry["ASTCoordinate"] = ASTCoordinate
        registry["SymbolicNode"] = SymbolicNode
        registry["ASTBridgeConfig"] = ASTBridgeConfig
        registry["PythonASTBridge"] = PythonASTBridge
    except Exception:
        pass

    return registry


# ======================================================================
# Main entry point
# ======================================================================

def run_bugs(args: argparse.Namespace) -> int:
    """Run bug detection on the files specified in *args*.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``files``    – list of file paths to analyse
        - ``severity`` – minimum severity filter
        - ``format``   – output format (``"text"`` or ``"json"``)
        - ``verbose``  – enable debug logging

    Returns
    -------
    int
        0 if no bugs found, 1 if bugs found.
    """
    try:
        from jugeo.errors import JudgmentError, DescentError, FailureChain, StructuredFailure
    except ImportError:
        JudgmentError = DescentError = Exception
        FailureChain = StructuredFailure = None

    files: list[str] = getattr(args, "files", [])
    severity_filter: str = getattr(args, "severity", "all")
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _bug_detection_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if not files:
        print("error: at least one file is required", file=sys.stderr)
        return 1

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    threshold = _SEVERITY_THRESHOLDS.get(severity_filter, 0.0)

    # ── Try rich problem_modes detection first ────────────────────────
    rich_used = False
    rich_found_any = False
    rich_json_results: list[dict[str, Any]] = []
    for filepath in files:
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            continue
        rich = _rich_bug_detection(filepath, site=None, judgments={})
        if rich is None:
            break  # problem_modes unavailable – fall through
        rich_used = True
        # Apply severity filter
        rich["bugs"] = [
            b for b in rich["bugs"] if b["severity"] >= threshold
        ]
        rich["count"] = len(rich["bugs"])
        rich["obstruction_count"] = sum(
            1 for b in rich["bugs"] if b["obstruction"]
        )
        if rich["count"]:
            rich_found_any = True
        if out_format == "json":
            rich_json_results.append(
                _format_rich_bug_json(filepath, rich)
            )
        else:
            text = _format_rich_bug_text(filepath, rich)
            if rich["count"] or verbose:
                print(text)

    if rich_used:
        if out_format == "json":
            print(json.dumps(rich_json_results, indent=2))
        if not rich_found_any and out_format == "text":
            print("No bugs found.")
        return 1 if rich_found_any else 0

    # ── Try the full detection pipeline next ──────────────────────────
    if _HAS_FULL_PIPELINE:
        _log.debug("Using full jugeo.problem_modes.bug_detection pipeline.")
        found_any = False
        json_results: list[dict[str, Any]] = []
        for filepath in files:
            filepath = os.path.abspath(filepath)
            if not os.path.isfile(filepath):
                print(f"error: {filepath}: not a file", file=sys.stderr)
                continue
            try:
                result = _detect_bugs_full(filepath, is_path=True)
            except Exception as exc:
                if JudgmentError is not Exception and isinstance(exc, (JudgmentError, DescentError)):
                    if FailureChain is not None and StructuredFailure is not None:
                        sf = StructuredFailure(message=str(exc))
                        chain = FailureChain(failures=(sf,), context_coordinate="bugs_pipeline")
                        print(f"  ✗ Analysis error ({filepath}): {chain.summary}", file=sys.stderr)
                    else:
                        print(f"  ✗ Analysis error ({filepath}): {exc}", file=sys.stderr)
                else:
                    print(f"error: {filepath}: {exc}", file=sys.stderr)
                continue
            filtered = tuple(b for b in result.bugs if b.severity >= threshold)
            if filtered:
                found_any = True
                from dataclasses import replace as _replace
                result = _replace(result, bugs=filtered)
            if out_format == "json":
                json_results.append(_format_json_result(filepath, result))
            else:
                text = _format_text_result(filepath, result)
                if filtered or verbose:
                    print(text)
        if out_format == "json":
            print(json.dumps(json_results, indent=2))
        if not found_any and out_format == "text":
            print("No bugs found.")
        return 1 if found_any else 0

    # ── Geometric analysis path ───────────────────────────────────────
    _log.debug(
        "Subsystem availability: site=%s judgments=%s descent=%s "
        "covers=%s trust=%s z3=%s",
        _HAS_SITE, _HAS_JUDGMENTS, _HAS_DESCENT,
        _HAS_COVERS, _HAS_TRUST, _HAS_Z3,
    )
    all_records: list[_BugRecord] = []
    for filepath in files:
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            print(f"error: {filepath}: not a file", file=sys.stderr)
            continue
        _log.debug("Analysing %s …", filepath)
        try:
            with open(filepath, encoding="utf-8") as fh:
                source = fh.read()
        except Exception as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            continue
        records = _analyse_file(source, filepath, threshold)
        all_records.extend(records)

    found_any = len(all_records) > 0
    if out_format == "json":
        print(_format_json(all_records))
    else:
        print(_format_text(all_records))

    return 1 if found_any else 0
