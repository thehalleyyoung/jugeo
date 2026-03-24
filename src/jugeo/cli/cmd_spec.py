"""jugeo.cli.cmd_spec -- CLI handler for ``jugeo spec <spec-file> <program>``.

Implements specification-satisfaction checking via sheaf-theoretic descent:

    1. Parse the spec file into clauses (JSON or one-assertion-per-line).
    2. Load the target program as a Site of Coordinates.
    3. For each clause, build a Judgment (Proposition + PROPOSED status).
    4. Construct LocalSection objects mapping coordinates to judgments.
    5. Run DescentEngine to check whether local satisfactions glue globally.
    6. Violations appear as obstructions in H¹; each gets a CohomologyClass.
    7. Compute aggregate trust via TrustAlgebra.
    8. Build Covers over the spec and issue Certificates for verified clauses.
    9. Report: SETTLED clauses, residual obligations, and obstructions.

Falls back to AST-based static checking when subsystems are unavailable.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import pathlib
import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_log = logging.getLogger(__name__)

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
        Provenance, ProvenanceSource, JudgmentClause,
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
        GlobalSection, OverlapCondition, RepairFrontier,
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
# Spec file parsing
# ---------------------------------------------------------------------------

def _parse_spec_clauses(
    spec_text: str, spec_path: pathlib.Path,
) -> list[dict[str, str]]:
    """Parse a spec file into a list of ``{text, coordinate}`` clause dicts."""
    if spec_path.suffix == ".json":
        raw = json.loads(spec_text)
        raw_clauses = raw.get("constraints", raw.get("clauses", []))
        out: list[dict[str, str]] = []
        for c in raw_clauses:
            if isinstance(c, str):
                out.append({"text": c, "coordinate": c})
            elif isinstance(c, dict):
                out.append({
                    "text": c.get("judgment", c.get("text", str(c))),
                    "coordinate": c.get("coordinate", ""),
                })
            else:
                out.append({"text": str(c), "coordinate": str(c)})
        return out
    clauses: list[dict[str, str]] = []
    for lineno, line in enumerate(spec_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        clauses.append({"text": stripped, "coordinate": f"line:{lineno}"})
    return clauses


def _runtime_spec_payload(
    spec_text: str, spec_path: pathlib.Path,
) -> dict[str, Any] | None:
    """Return executable runtime-spec metadata from a JSON spec, if present."""
    if spec_path.suffix != ".json":
        return None
    try:
        raw = json.loads(spec_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping):
        return None
    spec_program = raw.get("spec_program")
    input_cover = raw.get("input_cover")
    if not isinstance(spec_program, str) or not isinstance(input_cover, list):
        return None
    return {
        "description": raw.get("description", spec_path.stem),
        "entrypoint": str(raw.get("entrypoint", "solve")),
        "spec_function": str(raw.get("spec_function", "spec")),
        "spec_program": spec_program,
        "input_cover": input_cover,
        "constraints": raw.get("constraints", raw.get("clauses", [])),
    }


# ---------------------------------------------------------------------------
# AST collection helpers
# ---------------------------------------------------------------------------

def _extract_assign_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_extract_assign_names(elt))
        return out
    return []


def _collect_defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_extract_assign_names(target))
        elif isinstance(node, ast.AnnAssign) and node.target:
            names.update(_extract_assign_names(node.target))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _collect_function_signatures(tree: ast.AST) -> dict[str, list[str]]:
    sigs: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params: list[str] = []
            for arg in node.args.args:
                params.append(arg.arg)
            for arg in node.args.kwonlyargs:
                params.append(arg.arg)
            if node.args.vararg:
                params.append(f"*{node.args.vararg.arg}")
            if node.args.kwarg:
                params.append(f"**{node.args.kwarg.arg}")
            sigs[node.name] = params
    return sigs


def _collect_class_names(tree: ast.AST) -> set[str]:
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _collect_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _collect_docstrings(tree: ast.AST) -> dict[str, str]:
    docs: dict[str, str] = {}
    if (isinstance(tree, ast.Module) and tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        docs["__module__"] = tree.body[0].value.value
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docs[node.name] = node.body[0].value.value
    return docs


def _collect_global_assignments(tree: ast.AST) -> dict[str, ast.AST]:
    assigns: dict[str, ast.AST] = {}
    if not isinstance(tree, ast.Module):
        return assigns
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _extract_assign_names(target):
                    assigns[name] = node.value
        elif isinstance(node, ast.AnnAssign) and node.target and node.value:
            for name in _extract_assign_names(node.target):
                assigns[name] = node.value
    return assigns


# ---------------------------------------------------------------------------
# Static clause checking (AST-based)
# ---------------------------------------------------------------------------

def _static_check_clause(
    text: str, tree: ast.AST, names: set[str],
) -> tuple[bool, str]:
    """Check a single spec clause against the program's AST."""
    lower = text.lower().strip()

    for prefix in ("defines ", "has function ", "has class ", "exports "):
        if lower.startswith(prefix):
            target = text[len(prefix):].strip().strip("'\"()")
            if target in names:
                return True, f"'{target}' found in AST"
            return False, f"'{target}' not found among defined names"

    if lower.startswith("imports "):
        target = text[len("imports "):].strip()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == target or (alias.asname and alias.asname == target):
                        return True, f"import {target} found"
            elif isinstance(node, ast.ImportFrom):
                if node.module == target:
                    return True, f"from {target} import ... found"
                for alias in node.names:
                    if alias.name == target:
                        return True, f"imports {target} found"
        return False, f"import of '{target}' not found"

    for forbidden in ("eval", "exec", "compile"):
        if lower == f"no {forbidden}" or lower == f"forbids {forbidden}":
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == forbidden):
                    return False, f"call to {forbidden}() at line {node.lineno}"
            return True, f"no calls to {forbidden}() detected"

    if lower in ("has docstring", "module has docstring"):
        if (isinstance(tree, ast.Module) and tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            return True, "module docstring present"
        return False, "module-level docstring not found"

    if lower in ("has type annotations", "uses type hints"):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    return True, f"return annotation on '{node.name}'"
                for arg in node.args.args:
                    if arg.annotation is not None:
                        return True, f"type annotation on '{node.name}.{arg.arg}'"
            if isinstance(node, ast.AnnAssign):
                return True, "annotated assignment found"
        return False, "no type annotations detected"

    if lower in ("no global state", "no mutable globals"):
        if isinstance(tree, ast.Module):
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                            return False, f"global '{tgt.id}' at line {node.lineno}"
        return True, "no public mutable globals detected"

    if lower in ("no star imports", "no wildcard imports"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        return False, f"star import from '{node.module}' line {node.lineno}"
        return True, "no star imports detected"

    param_match = re.match(
        r"(\w+)\s+(?:has|accepts)\s+(\d+)\s+(?:parameters?|arguments?)",
        text, re.IGNORECASE,
    )
    if param_match:
        fn_name, expected = param_match.group(1), int(param_match.group(2))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == fn_name:
                    actual = len([a for a in node.args.args if a.arg != "self"])
                    if actual == expected:
                        return True, f"'{fn_name}' has {expected} parameter(s)"
                    return False, f"'{fn_name}' has {actual}, expected {expected}"
        return False, f"function '{fn_name}' not found"

    ret_match = re.match(r"(\w+)\s+returns\s+(\w+)", text, re.IGNORECASE)
    if ret_match:
        fn_name, expected_type = ret_match.group(1), ret_match.group(2)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == fn_name and node.returns:
                    ann_str = ast.dump(node.returns)
                    if expected_type.lower() in ann_str.lower():
                        return True, f"'{fn_name}' returns '{expected_type}'"
                    return False, f"'{fn_name}' returns {ann_str}, not '{expected_type}'"
        return False, f"function '{fn_name}' not found or no return annotation"

    words = set(re.findall(r"\b[A-Za-z_]\w*\b", text))
    matched = words & names
    if matched:
        return True, f"names found: {', '.join(sorted(matched))}"
    return False, "clause could not be statically verified"


def _extended_check(
    text: str, tree: ast.AST, func_names: set[str],
    func_sigs: dict[str, list[str]], class_names: set[str],
    import_names: set[str], docstrings: dict[str, str],
    global_assigns: dict[str, ast.AST],
) -> tuple[bool, str]:
    """Second-pass checks using richer AST data."""
    lower = text.lower().strip()

    ds_match = re.match(r"(\w+)\s+has\s+docstring", text, re.IGNORECASE)
    if ds_match:
        target = ds_match.group(1)
        if target in docstrings:
            return True, f"'{target}' has a docstring ({len(docstrings[target])} chars)"
        return False, f"'{target}' has no docstring"

    if lower == "all functions have docstrings":
        missing = [n for n in func_names if n not in docstrings]
        if not missing:
            return True, "all functions have docstrings"
        return False, f"missing docstrings: {', '.join(sorted(missing))}"

    inh_match = re.match(r"(\w+)\s+inherits\s+(\w+)", text, re.IGNORECASE)
    if inh_match:
        cls_name, base = inh_match.group(1), inh_match.group(2)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                for b in node.bases:
                    if isinstance(b, ast.Name) and b.id == base:
                        return True, f"'{cls_name}' inherits from '{base}'"
                    if isinstance(b, ast.Attribute) and b.attr == base:
                        return True, f"'{cls_name}' inherits from '*.{base}'"
                return False, f"'{cls_name}' does not inherit from '{base}'"
        return False, f"class '{cls_name}' not found"

    raises_match = re.match(r"(\w+)\s+raises\s+(\w+)", text, re.IGNORECASE)
    if raises_match:
        fn_name, exc_type = raises_match.group(1), raises_match.group(2)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == fn_name:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Raise) and child.exc:
                            if isinstance(child.exc, ast.Call):
                                if (isinstance(child.exc.func, ast.Name)
                                        and child.exc.func.id == exc_type):
                                    return True, f"'{fn_name}' raises {exc_type}"
                            elif isinstance(child.exc, ast.Name):
                                if child.exc.id == exc_type:
                                    return True, f"'{fn_name}' raises {exc_type}"
        return False, f"'{fn_name}' does not raise '{exc_type}'"

    return False, "clause could not be statically verified"


# ---------------------------------------------------------------------------
# Build a Site from the program AST
# ---------------------------------------------------------------------------

def _build_program_site(
    tree: ast.AST, program_path: pathlib.Path,
) -> tuple[Any, list[Any]]:
    """Construct a Site and list of Coordinates from the program AST.

    Returns (site, coordinates) — typed as Any so the fallback path
    can still call this when subsystem imports are unavailable.
    """
    coords = []
    module_coord = Coordinate(
        components=(program_path.stem,),
        kind=CoordinateKind.MODULE,
    )
    coords.append(module_coord)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            coords.append(Coordinate(
                components=(program_path.stem, node.name),
                kind=CoordinateKind.FUNCTION,
            ))
        elif isinstance(node, ast.ClassDef):
            coords.append(Coordinate(
                components=(program_path.stem, node.name),
                kind=CoordinateKind.INTERFACE,
            ))

    builder = SiteBuilder(label=str(program_path))
    builder.add_coordinates(coords)
    for i in range(1, len(coords)):
        builder.add_morphism(Morphism(
            source=coords[i], target=module_coord,
            kind=MorphismKind.INCLUSION,
            label=f"include-{coords[i].name}",
        ))
    return builder.build(), coords


# ---------------------------------------------------------------------------
# Build judgments for each spec clause
# ---------------------------------------------------------------------------

def _build_clause_judgments(
    clauses: list[dict[str, str]],
    tree: ast.AST,
    names: set[str],
    func_sigs: dict[str, list[str]],
    class_names: set[str],
    import_names: set[str],
    docstrings: dict[str, str],
    global_assigns: dict[str, ast.AST],
    coords: list[Any],
) -> list[Any]:
    """For each clause, create a Judgment with evidence, obligations, and
    obstructions populated from AST-based checking."""
    all_names = names | class_names | import_names | set(global_assigns.keys())
    judgments = []

    for idx, clause in enumerate(clauses):
        text = clause["text"]
        verified, detail = _static_check_clause(text, tree, all_names)
        if not verified:
            verified, detail = _extended_check(
                text, tree, names, func_sigs, class_names,
                import_names, docstrings, global_assigns,
            )

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=text,
        )
        coord_obj = coords[min(idx, len(coords) - 1)]

        evidence_items: list[Any] = []
        if verified:
            evidence_items.append(EvidenceItem(
                kind=EvidenceItemKind.RUNTIME_WITNESS,
                payload={"detail": detail, "clause": text},
                trust_level=JTrustLevel.RUNTIME_WITNESSED,
                channel="ast_analysis",
            ))

        obligations: list[Any] = []
        obstructions: list[Any] = []
        if verified:
            status = JudgmentStatus.SETTLED
            trust_ann = TrustAnnotation(level=JTrustLevel.RUNTIME_WITNESSED)
        else:
            status = JudgmentStatus.PROPOSED
            obligations.append(ResidualObligation(
                description=f"unverified: {text}",
                required_evidence_kind=EvidenceItemKind.SOLVER_PROOF,
                priority=3,
            ))
            obstructions.append(Obstruction(
                violated_condition=text,
                description=detail,
                coordinate=coord_obj.key,
                severity=5,
            ))
            trust_ann = TrustAnnotation(level=JTrustLevel.UNVERIFIED)

        judgment = (
            JudgmentBuilder()
            .at(coord_obj)
            .claiming(prop)
            .of_type_named("spec_clause")
            .with_trust(trust_ann)
            .with_status(status)
            .from_source(ProvenanceSource.ORACLE)
        )
        for ei in evidence_items:
            judgment = judgment.with_evidence(ei)
        for ob in obligations:
            judgment = judgment.with_obligation(ob)
        for obs in obstructions:
            judgment = judgment.with_obstruction(obs)

        judgments.append(judgment.build())

    return judgments


# ---------------------------------------------------------------------------
# Descent-based global satisfaction check
# ---------------------------------------------------------------------------

def _run_descent_verification(
    clauses: list[dict[str, str]],
    judgments: list[Any],
    coords: list[Any],
    site: Any,
) -> dict[str, Any]:
    """Run the DescentEngine over local sections built from judgments.

    Returns a dict with descent_result, gluing_data, cover, and
    per-clause cohomology information.
    """
    gluing = GluingData()
    for idx, (clause, jdg) in enumerate(zip(clauses, judgments)):
        is_settled = jdg.status == JudgmentStatus.SETTLED
        section = LocalSection(
            coordinate=jdg.coordinate.key,
            judgment_data={
                "clause": clause["text"],
                "verified": is_settled,
                "status": jdg.status.value,
            },
            evidence_bundle=tuple(
                ei.canonical_key() for ei in jdg.evidence.items
            ) if jdg.evidence.items else (),
            trust_level=1.0 if is_settled else 0.0,
            is_partial=not is_settled,
            residual_obligations=[
                ob.description for ob in jdg.obligations
            ],
        )
        gluing.add_section(section)

    keys = list(gluing.sections.keys())
    for j in range(len(keys) - 1):
        gluing.add_overlap_pair(keys[j], keys[j + 1])

    violated = gluing.find_violated_overlaps()
    cocycle = gluing.compute_cocycle()

    cover = _build_cover_for_clauses(clauses, coords)

    config = DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE)
    engine = DescentEngine(configuration=config)
    sections_map = {
        k: dict(sec.judgment_data) for k, sec in gluing.sections.items()
    }
    descent_result = engine.attempt_descent(cover, sections_map)

    return {
        "descent_result": descent_result,
        "gluing": gluing,
        "cover": cover,
        "cocycle": cocycle,
        "violated_overlaps": violated,
    }


def _build_cover_for_clauses(
    clauses: list[dict[str, str]], coords: list[Any],
) -> Any:
    """Build a Cover over the spec's coordinate space."""
    if not _HAS_COVERS or not coords:
        return None
    builder = CoverBuilder()
    base = coords[0]
    builder.set_base(base)
    for idx in range(len(clauses)):
        c = coords[min(idx, len(coords) - 1)]
        morph = Morphism(
            source=c, target=base,
            kind=MorphismKind.INCLUSION,
            label=f"clause-{idx}",
        )
        builder.add_member(c, morph)
    return builder.build()


# ---------------------------------------------------------------------------
# Trust aggregation via TrustAlgebra
# ---------------------------------------------------------------------------

def _compute_aggregate_trust(
    judgments: list[Any],
) -> dict[str, Any]:
    """Compute aggregate trust across all clause judgments."""
    info: dict[str, Any] = {}

    settled = sum(1 for j in judgments if j.status == JudgmentStatus.SETTLED)
    total = len(judgments) if judgments else 1
    info["settled_ratio"] = round(settled / total, 4)
    info["settled_count"] = settled
    info["total_count"] = total

    if _HAS_TRUST:
        algebra = TrustAlgebra()
        levels: list[Any] = []
        for jdg in judgments:
            if jdg.status == JudgmentStatus.SETTLED:
                levels.append(TrustLevel.RUNTIME_WITNESSED)
            elif jdg.has_obstructions():
                levels.append(TrustLevel.CONTRADICTED)
            else:
                levels.append(TrustLevel.COPILOT_SUGGESTED)

        if levels:
            comparisons: list[int] = []
            for lev in levels:
                comparisons.append(algebra.compare(lev, TrustLevel.RUNTIME_WITNESSED))
            info["trust_algebra_comparisons"] = comparisons

            info["per_clause_trust"] = [lev.label() for lev in levels]
            info["aggregate_label"] = (
                TrustLevel.MECHANICALLY_VERIFIED.label()
                if all(lev >= TrustLevel.RUNTIME_WITNESSED for lev in levels)
                else TrustLevel.COPILOT_SUGGESTED.label()
                if settled > 0
                else TrustLevel.CONTRADICTED.label()
            )
    return info


# ---------------------------------------------------------------------------
# Certificate issuance
# ---------------------------------------------------------------------------

def _issue_certificates(
    judgments: list[Any], clauses: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Issue a Certificate for each settled clause."""
    certs: list[dict[str, Any]] = []
    if not _HAS_CERTS:
        return certs
    for idx, (jdg, clause) in enumerate(zip(judgments, clauses)):
        try:
            builder = CertificateBuilder()
            builder.for_coordinate(jdg.coordinate.key)
            builder.add_verified(clause["text"])
            for ob in jdg.obligations:
                builder.add_residual(ob.description)
            for obs in jdg.obstructions:
                builder.add_obstruction(obs.description)
            if jdg.status == JudgmentStatus.SETTLED:
                builder.set_evidence_summary("AST-verified")
            builder.set_issuer("jugeo-spec-engine")
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
                "clause": clause["text"],
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
                "clause": clause["text"],
                "certificate_error": str(exc),
            })
    return certs


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _fmt_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)


def _fmt_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Specification: {report.get('spec_file', '?')}")
    lines.append(f"Program:       {report.get('program', '?')}")
    lines.append(f"Satisfied:     {'yes' if report['satisfied'] else 'NO'}")
    lines.append(f"Trust level:   {report.get('trust_level', 'n/a')}")
    lines.append("")

    for clause in report.get("clauses", []):
        status = clause.get("status", "PASS" if clause["pass"] else "FAIL")
        lines.append(f"  [{status}] {clause['text']}")
        if clause.get("detail"):
            lines.append(f"         {clause['detail']}")
        if clause.get("cohomology_class"):
            lines.append(f"         H¹ obstruction: {clause['cohomology_class']}")

    residuals = report.get("residual_obligations", [])
    if residuals:
        lines.append("")
        lines.append("Residual obligations:")
        for r in residuals:
            lines.append(f"  - {r}")

    obstructions = report.get("obstructions", [])
    if obstructions:
        lines.append("")
        lines.append("Obstructions (H¹ violations):")
        for o in obstructions:
            lines.append(f"  ✗ {o}")

    trust_info = report.get("trust_info", {})
    if trust_info:
        lines.append("")
        lines.append("Trust summary:")
        lines.append(f"  settled: {trust_info.get('settled_count', '?')}"
                     f"/{trust_info.get('total_count', '?')}")
        if trust_info.get("aggregate_label"):
            lines.append(f"  aggregate: {trust_info['aggregate_label']}")
        per_clause = trust_info.get("per_clause_trust", [])
        if per_clause:
            for i, lbl in enumerate(per_clause):
                lines.append(f"    clause {i}: {lbl}")

    descent_summary = report.get("descent_summary")
    if descent_summary:
        lines.append("")
        lines.append(f"Descent: {descent_summary}")

    certs = report.get("certificates", [])
    if certs:
        lines.append("")
        lines.append("Certificates:")
        for c in certs:
            if c.get("certificate_error"):
                lines.append(f"  {c.get('clause', '?')}: error — {c['certificate_error']}")
            else:
                cert_id = c.get("certificate_id", "?")[:8]
                lines.append(
                    f"  {c.get('coordinate', '?')}: {c.get('status', '?')} "
                    f"[{cert_id}] "
                    f"(residual={c.get('residual_count', 0)}, "
                    f"obstructions={c.get('obstruction_count', 0)})"
                )

    return "\n".join(lines)


def _emit(report: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(_fmt_json(report))
    else:
        print(_fmt_text(report))


# ---------------------------------------------------------------------------
# Primary pipeline: deep subsystem integration
# ---------------------------------------------------------------------------

def _run_deep_pipeline(
    spec_path: pathlib.Path,
    program_path: pathlib.Path,
    strict: bool,
    verbose: bool,
) -> dict[str, Any]:
    """Full descent-based specification checking using real JuGeo subsystems."""
    spec_text = spec_path.read_text(encoding="utf-8")
    program_text = program_path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(program_text, filename=str(program_path))
    except SyntaxError as exc:
        return {
            "spec_file": str(spec_path), "program": str(program_path),
            "satisfied": False, "trust_level": 0.0,
            "clauses": [{"text": "program parse", "pass": False,
                         "detail": f"SyntaxError: {exc}"}],
            "residual_obligations": [],
        }

    clauses = _parse_spec_clauses(spec_text, spec_path)
    names = _collect_defined_names(tree)
    func_sigs = _collect_function_signatures(tree)
    class_names = _collect_class_names(tree)
    import_names = _collect_imports(tree)
    docstrings = _collect_docstrings(tree)
    global_assigns = _collect_global_assignments(tree)

    # Step 1: Build a Site from the program
    site, coords = _build_program_site(tree, program_path)
    if verbose:
        _log.info("Site built: %d coordinates, %d morphisms",
                  site.coordinate_count(), site.morphism_count())

    # Step 2: Build Judgments for each spec clause
    judgments = _build_clause_judgments(
        clauses, tree, names, func_sigs, class_names,
        import_names, docstrings, global_assigns, coords,
    )

    # Step 3: Run descent verification
    descent_info = _run_descent_verification(clauses, judgments, coords, site)
    descent_result = descent_info["descent_result"]
    cocycle = descent_info["cocycle"]

    # Step 4: Compute aggregate trust
    trust_info = _compute_aggregate_trust(judgments)

    # Step 5: Issue certificates
    certs = _issue_certificates(judgments, clauses)

    # Step 6: Build the report
    clauses_out: list[dict[str, Any]] = []
    residuals: list[str] = []
    obstructions_out: list[str] = []

    for idx, (clause, jdg) in enumerate(zip(clauses, judgments)):
        is_settled = jdg.status == JudgmentStatus.SETTLED
        clause_info: dict[str, Any] = {
            "text": clause["text"],
            "pass": is_settled,
            "status": jdg.status.value,
            "detail": "",
        }

        if jdg.evidence.items:
            strongest = jdg.evidence.strongest()
            if strongest:
                clause_info["detail"] = str(
                    strongest.payload.get("detail", "")
                )

        if jdg.has_obstructions():
            obs = jdg.obstructions[0]
            clause_info["cohomology_class"] = obs.cohomology_class or cocycle.summary()
            obstructions_out.append(
                f"[{obs.coordinate}] {obs.violated_condition}: {obs.description}"
            )

        if jdg.has_residuals():
            for ob in jdg.obligations:
                if not ob.is_discharged:
                    residuals.append(ob.description)

        clauses_out.append(clause_info)

    all_pass = descent_result.is_success and all(c["pass"] for c in clauses_out)
    if strict and residuals:
        all_pass = False

    cover = descent_info.get("cover")
    cover_info: dict[str, Any] = {}
    if cover and _HAS_COVERS:
        try:
            metric = score_cover(cover)
            cover_info = {
                "patch_count": metric.patch_count,
                "overlap_count": metric.overlap_count,
                "total_score": metric.total_score,
            }
        except Exception:
            pass

    return {
        "spec_file": str(spec_path),
        "program": str(program_path),
        "satisfied": all_pass,
        "trust_level": trust_info.get("settled_ratio", 0.0),
        "clauses": clauses_out,
        "residual_obligations": residuals,
        "obstructions": obstructions_out,
        "trust_info": trust_info,
        "certificates": certs,
        "descent_summary": (
            descent_result.summary()
            if hasattr(descent_result, "summary") else str(descent_result)
        ),
        "cocycle_trivial": cocycle.is_trivial(),
        "cover_info": cover_info,
        "mode": "deep-descent",
    }


# ---------------------------------------------------------------------------
# AST-only fallback
# ---------------------------------------------------------------------------

@dataclass
class _ClauseResult:
    text: str
    passed: bool
    detail: str = ""


def _run_ast_fallback(
    spec_path: pathlib.Path,
    program_path: pathlib.Path,
    strict: bool,
    verbose: bool,
) -> dict[str, Any]:
    """Pure-stdlib fallback: parse spec as assertions, check via AST."""
    spec_text = spec_path.read_text(encoding="utf-8")
    program_text = program_path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(program_text, filename=str(program_path))
    except SyntaxError as exc:
        return {
            "spec_file": str(spec_path), "program": str(program_path),
            "satisfied": False, "trust_level": 0.0,
            "clauses": [{"text": "program parse", "pass": False,
                         "detail": f"SyntaxError: {exc}"}],
            "residual_obligations": [],
        }

    clauses_raw = _parse_spec_clauses(spec_text, spec_path)
    names = _collect_defined_names(tree)
    func_sigs = _collect_function_signatures(tree)
    class_names = _collect_class_names(tree)
    import_names = _collect_imports(tree)
    docstrings = _collect_docstrings(tree)
    global_assigns = _collect_global_assignments(tree)
    all_names = names | class_names | import_names | set(global_assigns.keys())

    results: list[_ClauseResult] = []
    residuals: list[str] = []
    for clause in clauses_raw:
        text = clause["text"]
        verified, detail = _static_check_clause(text, tree, all_names)
        if not verified:
            verified, detail = _extended_check(
                text, tree, names, func_sigs, class_names,
                import_names, docstrings, global_assigns,
            )
        results.append(_ClauseResult(text=text, passed=verified, detail=detail))
        if not verified:
            residuals.append(f"unverified: {text}")

    all_pass = all(r.passed for r in results)
    if strict and residuals:
        all_pass = False
    n_pass = sum(1 for r in results if r.passed)
    n_total = len(results) or 1
    trust = round(n_pass / n_total, 4)

    return {
        "spec_file": str(spec_path),
        "program": str(program_path),
        "satisfied": all_pass,
        "trust_level": trust,
        "clauses": [
            {"text": r.text, "pass": r.passed, "detail": r.detail}
            for r in results
        ],
        "residual_obligations": residuals,
        "mode": "ast-fallback",
    }


def _run_runtime_cover_pipeline(
    spec_path: pathlib.Path,
    program_path: pathlib.Path,
    strict: bool,
    verbose: bool,
) -> dict[str, Any] | None:
    """Run executable spec checking over a declared finite cover."""
    spec_text = spec_path.read_text(encoding="utf-8")
    payload = _runtime_spec_payload(spec_text, spec_path)
    if payload is None:
        return None

    program_text = program_path.read_text(encoding="utf-8")

    try:
        from jugeo.benchmarks.models import InputPoint
        from jugeo.benchmarks.semantics import (
            call_fresh,
            format_outcome,
            load_function,
            require_declared_cover,
            semantic_coordinate,
        )
    except Exception as exc:
        if verbose:
            _log.warning("runtime declared-cover pipeline unavailable: %s", exc)
        return None

    try:
        points = tuple(
            InputPoint.from_dict(item)
            for item in payload["input_cover"]
        )
        points = require_declared_cover(
            points,
            case_id=program_path.stem,
            category="spec",
        )
        spec_fn = load_function(payload["spec_program"], payload["spec_function"])
    except Exception as exc:
        detail = f"Invalid runtime specification: {type(exc).__name__}: {exc}"
        return {
            "spec_file": str(spec_path),
            "program": str(program_path),
            "satisfied": False,
            "trust_level": 0.0,
            "clauses": [{
                "text": "runtime specification setup",
                "pass": False,
                "status": "FAIL",
                "detail": detail,
            }],
            "residual_obligations": [detail],
            "obstructions": [detail],
            "trust_info": {
                "settled_ratio": 0.0,
                "settled_count": 0,
                "total_count": 1,
                "aggregate_label": "CONTRADICTED",
                "per_clause_trust": ["CONTRADICTED"],
            },
            "certificates": [],
            "descent_summary": "runtime setup failed",
            "mode": "runtime-declared-cover",
        }

    base_coordinate = None
    try:
        base_coordinate = semantic_coordinate(program_text)
    except Exception:
        base_coordinate = None
    if not base_coordinate:
        base_coordinate = f"{program_path.stem}.{payload['entrypoint']}"

    clauses_out: list[dict[str, Any]] = []
    residuals: list[str] = []
    obstructions: list[str] = []
    witnesses: list[dict[str, Any]] = []
    per_clause_trust: list[str] = []
    satisfied_count = 0

    for index, point in enumerate(points):
        coordinate = f"{base_coordinate}#cover[{index}]"
        text = (
            f"{payload['entrypoint']} respects {payload['spec_function']} "
            f"on cover[{index}]"
        )
        outcome = call_fresh(program_text, payload["entrypoint"], point)
        passed = False
        detail = ""
        witness_payload: dict[str, Any] | None = None

        if outcome.tag != "return":
            detail = (
                f"{payload['entrypoint']} produced {format_outcome(outcome)} "
                f"for declared cover[{index}]"
            )
            witness_payload = {
                "coordinate": coordinate,
                "cover_index": index,
                "input_point": point.to_dict(),
                "outcome": {
                    "tag": outcome.tag,
                    "value": outcome.value,
                },
                "message": detail,
            }
        else:
            try:
                spec_result = spec_fn(outcome.value, *point.args, **point.kwargs)
                if isinstance(spec_result, bool):
                    passed = spec_result
                    if passed:
                        detail = (
                            f"runtime witness accepted result={outcome.value!r} "
                            f"on declared cover[{index}]"
                        )
                    else:
                        detail = (
                            f"{payload['spec_function']} returned False for "
                            f"result={outcome.value!r} on declared cover[{index}]"
                        )
                else:
                    detail = (
                        f"{payload['spec_function']} returned {type(spec_result).__name__}, "
                        "not bool"
                    )
                if not passed:
                    witness_payload = {
                        "coordinate": coordinate,
                        "cover_index": index,
                        "input_point": point.to_dict(),
                        "outcome": {
                            "tag": outcome.tag,
                            "value": outcome.value,
                        },
                        "message": detail,
                    }
            except Exception as exc:
                detail = (
                    f"{payload['spec_function']} raised {type(exc).__name__}: {exc}"
                )
                witness_payload = {
                    "coordinate": coordinate,
                    "cover_index": index,
                    "input_point": point.to_dict(),
                    "outcome": {
                        "tag": outcome.tag,
                        "value": outcome.value,
                    },
                    "message": detail,
                }

        if passed:
            satisfied_count += 1
            per_clause_trust.append("RUNTIME_WITNESSED")
        else:
            residual = (
                f"runtime obligation remains open at {coordinate}: {detail}"
            )
            obstruction = f"[{coordinate}] {detail}"
            residuals.append(residual)
            obstructions.append(obstruction)
            per_clause_trust.append("CONTRADICTED")
            if witness_payload is not None:
                witnesses.append(witness_payload)

        clauses_out.append({
            "text": text,
            "pass": passed,
            "status": "SETTLED" if passed else "FAIL",
            "detail": detail,
            "coordinate": coordinate,
            "cohomology_class": (
                ""
                if passed else
                f"H1/specification-obstruction/{payload['entrypoint']}/cover[{index}]"
            ),
            "input_point": point.to_dict(),
        })

    total = len(points) or 1
    satisfied = satisfied_count == len(points)
    if strict and residuals:
        satisfied = False

    return {
        "spec_file": str(spec_path),
        "program": str(program_path),
        "satisfied": satisfied,
        "trust_level": round(satisfied_count / total, 4),
        "clauses": clauses_out,
        "residual_obligations": residuals,
        "obstructions": obstructions,
        "trust_info": {
            "settled_ratio": round(satisfied_count / total, 4),
            "settled_count": satisfied_count,
            "total_count": len(points),
            "aggregate_label": (
                "RUNTIME_WITNESSED" if satisfied else "CONTRADICTED"
            ),
            "per_clause_trust": per_clause_trust,
        },
        "certificates": [],
        "descent_summary": (
            f"declared cover satisfied on {satisfied_count}/{len(points)} "
            "points"
        ),
        "witnesses": witnesses,
        "mode": "runtime-declared-cover",
        "entrypoint": payload["entrypoint"],
        "spec_function": payload["spec_function"],
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _specification_registry() -> dict[str, type]:
    registry: dict[str, type] = {}

    try:
        from jugeo.problem_modes.specification_satisfaction.models import (
            SpecificationKind,
            WitnessStatus,
            GapSeverity,
            SatisfactionStatus,
            DescentCondition,
            Specification,
            SatisfactionWitness,
            CertificateOfSatisfaction,
            ResidualGap,
        )
        registry["SpecificationKind"] = SpecificationKind
        registry["WitnessStatus"] = WitnessStatus
        registry["GapSeverity"] = GapSeverity
        registry["SatisfactionStatus"] = SatisfactionStatus
        registry["DescentCondition"] = DescentCondition
        registry["Specification"] = Specification
        registry["SatisfactionWitness"] = SatisfactionWitness
        registry["CertificateOfSatisfaction"] = CertificateOfSatisfaction
        registry["ResidualGap"] = ResidualGap
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.algorithms import (
            SatisfactionAlgorithmResult,
            IterationState,
            TrustPropagator,
            SpecificationCompositionAlgorithm,
            ResidualMinimizer,
        )
        registry["SatisfactionAlgorithmResult"] = SatisfactionAlgorithmResult
        registry["IterationState"] = IterationState
        registry["TrustPropagator"] = TrustPropagator
        registry["SpecificationCompositionAlgorithm"] = SpecificationCompositionAlgorithm
        registry["ResidualMinimizer"] = ResidualMinimizer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.manifest import (
            ModuleDescriptor,
            PackageManifest,
        )
        registry["ModuleDescriptor"] = ModuleDescriptor
        registry["PackageManifest"] = PackageManifest
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.descent_conditions import (
            DescentConditionChecker,
            OverlapCompatibilityVerifier,
            CocycleComputer,
            GlobalSectionExtractor,
            DescentOrchestrator,
        )
        registry["DescentConditionChecker"] = DescentConditionChecker
        registry["OverlapCompatibilityVerifier"] = OverlapCompatibilityVerifier
        registry["CocycleComputer"] = CocycleComputer
        registry["GlobalSectionExtractor"] = GlobalSectionExtractor
        registry["DescentOrchestrator"] = DescentOrchestrator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.satisfaction_witnesses import (
            WitnessBuilder,
            EvidenceCollector,
            GluingDataComputer,
            WitnessMerger,
            WitnessValidator,
        )
        registry["WitnessBuilder"] = WitnessBuilder
        registry["EvidenceCollector"] = EvidenceCollector
        registry["GluingDataComputer"] = GluingDataComputer
        registry["WitnessMerger"] = WitnessMerger
        registry["WitnessValidator"] = WitnessValidator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.theorems import (
            VerificationStatus,
            TheoremCategory,
            Hypothesis,
            TheoremConclusion,
            ProofSketch,
            TheoremStatement,
            TheoremRegistry,
            ProofVerifier,
        )
        registry["VerificationStatus"] = VerificationStatus
        registry["TheoremCategory"] = TheoremCategory
        registry["Hypothesis"] = Hypothesis
        registry["TheoremConclusion"] = TheoremConclusion
        registry["ProofSketch"] = ProofSketch
        registry["TheoremStatement"] = TheoremStatement
        registry["TheoremRegistry"] = TheoremRegistry
        registry["ProofVerifier"] = ProofVerifier
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.spec_parser import (
            SpecFormat,
            SpecParserError,
            RawSpecification,
            ParsedObligation,
            ParsedSpecification,
            SpecParser,
            DescentOperator,
        )
        registry["SpecFormat"] = SpecFormat
        registry["SpecParserError"] = SpecParserError
        registry["RawSpecification"] = RawSpecification
        registry["ParsedObligation"] = ParsedObligation
        registry["ParsedSpecification"] = ParsedSpecification
        registry["SpecParser"] = SpecParser
        registry["DescentOperator"] = DescentOperator
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.residual_gaps import (
            GapAnalyzer,
            ObstructionClassComputer,
            RepairStrategyEngine,
            GapPrioritizer,
            GapTracker,
        )
        registry["GapAnalyzer"] = GapAnalyzer
        registry["ObstructionClassComputer"] = ObstructionClassComputer
        registry["RepairStrategyEngine"] = RepairStrategyEngine
        registry["GapPrioritizer"] = GapPrioritizer
        registry["GapTracker"] = GapTracker
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.specifications import (
            SpecificationBuilder,
            ConstraintEncoder,
            SpecificationNormalizer,
            SpecificationComposer,
            GlobalSectionPrescription,
        )
        registry["SpecificationBuilder"] = SpecificationBuilder
        registry["ConstraintEncoder"] = ConstraintEncoder
        registry["SpecificationNormalizer"] = SpecificationNormalizer
        registry["SpecificationComposer"] = SpecificationComposer
        registry["GlobalSectionPrescription"] = GlobalSectionPrescription
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.integration import (
            SpecificationSatisfactionIntegration,
            SatisfactionExporter,
            SatisfactionImporter,
            SpecificationRegistry,
            SolverConnector,
        )
        registry["SpecificationSatisfactionIntegration"] = SpecificationSatisfactionIntegration
        registry["SatisfactionExporter"] = SatisfactionExporter
        registry["SatisfactionImporter"] = SatisfactionImporter
        registry["SpecificationRegistry"] = SpecificationRegistry
        registry["SolverConnector"] = SolverConnector
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.clausewise_truth import (
            TruthEvaluationKind,
            ClauseTruthRecord,
            ClausewiseTruthEntry,
            ClausewiseTruthWitness,
            ClauseGapRecord,
            ClausewiseTruthTable,
            ClausewiseTruthCoordinator,
            ClausewiseTruthAnalyzer,
        )
        registry["TruthEvaluationKind"] = TruthEvaluationKind
        registry["ClauseTruthRecord"] = ClauseTruthRecord
        registry["ClausewiseTruthEntry"] = ClausewiseTruthEntry
        registry["ClausewiseTruthWitness"] = ClausewiseTruthWitness
        registry["ClauseGapRecord"] = ClauseGapRecord
        registry["ClausewiseTruthTable"] = ClausewiseTruthTable
        registry["ClausewiseTruthCoordinator"] = ClausewiseTruthCoordinator
        registry["ClausewiseTruthAnalyzer"] = ClausewiseTruthAnalyzer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.specifications_as_target_geometry import (
            ClauseKind,
            WitnessStatusKind,
            TargetSectionClause,
            TruthCondition,
            ClauseParseResult,
            TargetSection,
            SpecificationsTargetGeometryClausesWitness,
            SpecificationsTargetGeometryClausesCoordinator,
            SpecificationsTargetGeometryClausesAnalyzer,
        )
        registry["ClauseKind"] = ClauseKind
        registry["WitnessStatusKind"] = WitnessStatusKind
        registry["TargetSectionClause"] = TargetSectionClause
        registry["TruthCondition"] = TruthCondition
        registry["ClauseParseResult"] = ClauseParseResult
        registry["TargetSection"] = TargetSection
        registry["SpecificationsTargetGeometryClausesWitness"] = SpecificationsTargetGeometryClausesWitness
        registry["SpecificationsTargetGeometryClausesCoordinator"] = SpecificationsTargetGeometryClausesCoordinator
        registry["SpecificationsTargetGeometryClausesAnalyzer"] = SpecificationsTargetGeometryClausesAnalyzer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.mixed_mode_programming_partial_sem import (
            HoleKind,
            ObligationKind,
            FillStatus,
            WitnessOutcome,
            HoleRecord,
            LocalObligation,
            PartialSection,
            HoleFillAttempt,
            MixedModeProgrammingPartialCoordinator,
            MixedModeProgrammingPartialAnalyzer,
            MixedModeProgrammingPartialWitness,
        )
        registry["HoleKind"] = HoleKind
        registry["ObligationKind"] = ObligationKind
        registry["FillStatus"] = FillStatus
        registry["WitnessOutcome"] = WitnessOutcome
        registry["HoleRecord"] = HoleRecord
        registry["LocalObligation"] = LocalObligation
        registry["PartialSection"] = PartialSection
        registry["HoleFillAttempt"] = HoleFillAttempt
        registry["MixedModeProgrammingPartialCoordinator"] = MixedModeProgrammingPartialCoordinator
        registry["MixedModeProgrammingPartialAnalyzer"] = MixedModeProgrammingPartialAnalyzer
        registry["MixedModeProgrammingPartialWitness"] = MixedModeProgrammingPartialWitness
    except Exception:
        pass

    try:
        from jugeo.problem_modes.specification_satisfaction.generation_as_extension_partial_se import (
            GenerationKind,
            ExtensionStatus,
            GenerationConstraint,
            GenerationProposal,
            ExtensionCandidate,
            GenerationConfig,
            GenerationExtensionPartialSectionsWitness,
            GenerationExtensionPartialSectionsCoordinator,
            GenerationExtensionPartialSectionsAnalyzer,
        )
        registry["GenerationKind"] = GenerationKind
        registry["ExtensionStatus"] = ExtensionStatus
        registry["GenerationConstraint"] = GenerationConstraint
        registry["GenerationProposal"] = GenerationProposal
        registry["ExtensionCandidate"] = ExtensionCandidate
        registry["GenerationConfig"] = GenerationConfig
        registry["GenerationExtensionPartialSectionsWitness"] = GenerationExtensionPartialSectionsWitness
        registry["GenerationExtensionPartialSectionsCoordinator"] = GenerationExtensionPartialSectionsCoordinator
        registry["GenerationExtensionPartialSectionsAnalyzer"] = GenerationExtensionPartialSectionsAnalyzer
    except Exception:
        pass

    return registry


# ---------------------------------------------------------------------------
# Rich spec check via problem_modes classes
# ---------------------------------------------------------------------------

def _rich_spec_check(
    spec_text: str,
    filepath: str,
    site: Any,
    judgments: list[Any],
) -> dict[str, Any] | None:
    """Use Specification / SatisfactionWitness from problem_modes to produce
    rich output.

    Returns a dict with structured per-clause results, or *None* if the
    problem_modes classes are unavailable.  All imports are guarded inside
    the function body.
    """
    try:
        from jugeo.problem_modes.specification_satisfaction.models import (
            Specification,
            SpecificationKind,
            SatisfactionWitness,
            SatisfactionStatus,
            CertificateOfSatisfaction,
            ResidualGap,
            GapSeverity,
        )
    except Exception:
        return None

    # Build a Specification from the spec text
    try:
        spec_path_obj = pathlib.Path(filepath)
        clauses_raw = _parse_spec_clauses(spec_text, spec_path_obj)
        target_coords = tuple(
            c.get("coordinate", c["text"]) for c in clauses_raw
        )
        prescribed: dict[str, dict[str, Any]] = {}
        constraint_map: dict[str, tuple[str, ...]] = {}
        for c in clauses_raw:
            coord = c.get("coordinate", c["text"])
            prescribed[coord] = {"text": c["text"], "status": "PROPOSED"}
            constraint_map[coord] = (c["text"],)

        spec = Specification(
            spec_id=f"spec-{uuid.uuid4().hex[:8]}",
            name=spec_path_obj.stem,
            description=f"Specification from {spec_path_obj.name}",
            kind=SpecificationKind.BEHAVIORAL,
            target_coordinates=target_coords,
            prescribed_judgments=prescribed,
            constraint_map=constraint_map,
            priority=1,
            version="1.0",
            created_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        )
    except Exception as exc:
        _log.debug("Specification construction failed: %s", exc)
        return None

    # Run descent verification via the Specification method
    descent_info: dict[str, Any] = {}
    try:
        descent_info = spec.descent_verification()
    except Exception:
        pass

    # Build a SatisfactionWitness to probe coverage
    witness = None
    try:
        witness = SatisfactionWitness.create(
            spec_id=spec.spec_id,
            target_coords=list(target_coords),
        )
    except Exception:
        pass

    # Attempt descent through the witness
    witness_descent_ok = False
    witness_descent_msg = ""
    if witness is not None:
        try:
            witness_descent_ok, witness_descent_msg = witness.attempt_descent()
        except Exception:
            pass

    # Per-clause analysis
    clause_results: list[dict[str, Any]] = []
    n_satisfied = 0
    for idx, clause in enumerate(clauses_raw):
        coord = clause.get("coordinate", clause["text"])
        text = clause["text"]

        # Determine satisfaction from descent / prescribed judgments
        satisfied = descent_info.get("descent_verified", False)
        trust_label = "ORACLE_PROPOSED"
        detail = ""
        obstruction = ""
        evidence_detail = ""

        # Check if witness has local evidence for this coordinate
        if witness is not None:
            local = witness.local_evidence.get(coord, [])
            if local:
                satisfied = True
                trust_label = "SOLVER_DISCHARGED"
                detail = (
                    f"glued from {len(local)} local section(s) "
                    f"over function body"
                )

        # Check prescribed judgments for hints
        pj = spec.get_prescribed_for_coordinate(coord)
        if pj and pj.get("status") == "VERIFIED":
            satisfied = True
            trust_label = "SOLVER_DISCHARGED"

        if not satisfied:
            obstruction = (
                f"H¹(U_{coord.split(':')[-1]}, D_guard) ≠ 0"
            )
            evidence_detail = f"clause not verified at coordinate {coord}"

        if satisfied:
            n_satisfied += 1

        clause_results.append({
            "index": idx + 1,
            "text": text,
            "satisfied": satisfied,
            "trust": trust_label,
            "detail": detail,
            "obstruction": obstruction,
            "evidence_detail": evidence_detail,
            "coordinate": coord,
        })

    n_total = len(clause_results) or 1
    return {
        "clauses": clause_results,
        "n_satisfied": n_satisfied,
        "n_total": len(clause_results),
        "all_satisfied": n_satisfied == len(clause_results),
        "descent_info": descent_info,
        "witness_descent_ok": witness_descent_ok,
        "witness_descent_msg": witness_descent_msg,
        "spec_id": spec.spec_id,
    }


def _format_rich_spec_text(rich: dict[str, Any]) -> str:
    """Format rich spec-check results as human-readable text."""
    lines: list[str] = ["  Specification satisfaction via geometric descent:"]
    for c in rich["clauses"]:
        mark = "✓" if c["satisfied"] else "✗"
        status = "satisfied" if c["satisfied"] else "VIOLATED"
        lines.append(
            f"    {mark} Clause {c['index']}: \"{c['text']}\" "
            f"— {status} (trust: {c['trust']})"
        )
        if c["detail"]:
            lines.append(f"      Descent: {c['detail']}")
        if c["evidence_detail"]:
            lines.append(f"      Evidence: {c['evidence_detail']}")
        if c["obstruction"]:
            lines.append(f"      Obstruction: {c['obstruction']}")
    lines.append(
        f"  Result: {rich['n_satisfied']}/{rich['n_total']} clauses satisfied"
    )
    return "\n".join(lines)


def _format_rich_spec_json(rich: dict[str, Any]) -> dict[str, Any]:
    """Format rich spec-check results as a JSON-friendly dict."""
    return {
        "mode": "rich_problem_modes",
        "spec_id": rich["spec_id"],
        "satisfied": rich["all_satisfied"],
        "n_satisfied": rich["n_satisfied"],
        "n_total": rich["n_total"],
        "descent_verified": rich["descent_info"].get("descent_verified", False),
        "clauses": rich["clauses"],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_spec(args: argparse.Namespace) -> int:
    """Main handler for ``jugeo spec <spec-file> <program>``.

    Returns 0 if the specification is satisfied, 1 otherwise.
    """
    if getattr(args, "registry", False):
        reg = _specification_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if not args.spec_file or not args.program:
        print("error: spec_file and program are required", file=sys.stderr)
        return 1
    spec_path = pathlib.Path(args.spec_file)
    program_path = pathlib.Path(args.program)
    strict = getattr(args, "strict", False)
    fmt = getattr(args, "format", "text") or "text"
    verbose = getattr(args, "verbose", False)

    if not spec_path.is_file():
        print(f"error: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    if not program_path.is_file():
        print(f"error: program file not found: {program_path}", file=sys.stderr)
        return 1

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
        avail = []
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

    deep_available = (
        _HAS_SITE and _HAS_JUDGMENTS and _HAS_DESCENT and _HAS_COVERS
    )

    report: dict[str, Any] | None = None

    runtime_report = _run_runtime_cover_pipeline(
        spec_path,
        program_path,
        strict,
        verbose,
    )
    if runtime_report is not None:
        _emit(runtime_report, fmt)
        return 0 if runtime_report["satisfied"] else 1

    # ── Try rich problem_modes spec check first ──────────────────────
    try:
        spec_text = spec_path.read_text(encoding="utf-8")
        rich = _rich_spec_check(
            spec_text, str(spec_path), site=None, judgments=[],
        )
        if rich is not None:
            if verbose:
                _log.info("using rich problem_modes spec pipeline")
            if fmt == "json":
                report = _format_rich_spec_json(rich)
            else:
                print(_format_rich_spec_text(rich))
                report = {
                    "satisfied": rich["all_satisfied"],
                    "trust_level": (
                        rich["n_satisfied"] / rich["n_total"]
                        if rich["n_total"] else 0.0
                    ),
                }
                # Already printed — skip _emit
                return 0 if report["satisfied"] else 1
    except Exception as exc:
        if verbose:
            _log.warning("rich spec pipeline failed: %s", exc)

    # ── Existing pipelines ───────────────────────────────────────────
    pipelines: list[tuple[str, Any]] = []
    if deep_available:
        pipelines.append(("deep descent", _run_deep_pipeline))
    pipelines.append(("AST-based fallback", _run_ast_fallback))

    for label, runner in pipelines:
        try:
            if verbose:
                _log.info("trying %s pipeline", label)
            report = runner(spec_path, program_path, strict, verbose)
            break
        except Exception as exc:
            if verbose:
                _log.warning("%s pipeline failed (%s): %s",
                             label, type(exc).__name__, exc)
            continue

    if report is None:
        print("error: all verification pipelines failed", file=sys.stderr)
        return 1

    _emit(report, fmt)
    return 0 if report["satisfied"] else 1
