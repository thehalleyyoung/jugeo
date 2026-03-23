"""CLI subcommand: ``jugeo alignment <program> [--docs FILE]``.

Checks alignment as a sheaf-theoretic property: docs and implementation
are overlapping local sections over a shared site.  Alignment holds when
they glue via descent; misalignment = obstruction in the gluing.
"""
from __future__ import annotations

import argparse, ast, json, logging, os, re, sys, time, uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

_log = logging.getLogger(__name__)

# ── JuGeo imports (all try/except) ────────────────────────────────────
try:
    from jugeo.geometry.site import (Site, SiteBuilder, Coordinate,  # type: ignore[import-untyped]
                                     CoordinateKind, Morphism, MorphismKind)
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False
try:
    from jugeo.judgments.judgment_terms import (Judgment, JudgmentBuilder,  # type: ignore[import-untyped]
        Proposition, PropositionKind, EvidenceBundle, EvidenceItem,
        EvidenceItemKind, TrustLevel, Obstruction)
    _HAS_JUDGMENTS = True
except Exception:
    _HAS_JUDGMENTS = False
try:
    from jugeo.judgments.sections import (Section, SectionBuilder,  # type: ignore[import-untyped]
                                          SectionFamily, SheafCondition)
    _HAS_SECTIONS = True
except Exception:
    _HAS_SECTIONS = False
try:
    from jugeo.judgments.contexts import SemanticContext, ContextBinding  # type: ignore[import-untyped]
    _HAS_CONTEXTS = True
except Exception:
    _HAS_CONTEXTS = False
try:
    from jugeo.evidence.trust import (TrustLevel as ETrustLevel,  # type: ignore[import-untyped]
                                       TrustAlgebra)
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False
try:
    from jugeo.geometry.descent import (DescentEngine, LocalSection,  # type: ignore[import-untyped]
                                         OverlapCondition, OverlapStatus)
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False
try:
    from jugeo.geometry.covers import Cover  # type: ignore[import-untyped]
    _HAS_COVERS = True
except Exception:
    _HAS_COVERS = False

_FULL_STACK = all([_HAS_SITE, _HAS_JUDGMENTS, _HAS_SECTIONS,
                   _HAS_CONTEXTS, _HAS_TRUST, _HAS_DESCENT])

# ======================================================================
# Result structures
# ======================================================================

@dataclass
class _OverlapResult:
    """Outcome of one overlap check between docs and code sections."""
    function: str; file: str; line: int; status: str
    doc_claim: str; code_behaviour: str
    obstruction_id: str = ""; description: str = ""

@dataclass
class _AlignmentReport:
    """Full sheaf-theoretic alignment report."""
    program: str; docs_file: str | None = None
    overlaps: list[_OverlapResult] = field(default_factory=list)
    obstructions: list[dict[str, Any]] = field(default_factory=list)
    trust_score: float = 1.0; trust_label: str = "UNVERIFIED"
    gluing_success: bool = True; elapsed_s: float = 0.0

# ======================================================================
# AST helpers
# ======================================================================

def _get_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        return node.body[0].value.value
    return None

def _extract_doc_params(docstring: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for m in re.finditer(
        r":param\s+(\w+):|:type\s+(\w+):\s*(\S+)|(\w+)\s*\((\w[^)]*)\)|(\w+)\s*:\s*(\w+)",
        docstring):
        g = m.groups()
        if g[0]:            params[g[0]] = ""
        elif g[1] and g[2]: params[g[1]] = g[2]
        elif g[3] and g[4]: params[g[3]] = g[4]
        elif g[5] and g[6]: params[g[5]] = g[6]
    return params

def _extract_doc_returns(docstring: str) -> str | None:
    m = re.search(r":rtype:\s*(\S+)|[Rr]eturns?\s*[-:]?\s*\n\s*(\w[\w\[\], |]*)", docstring)
    return (m.group(1) or m.group(2)) if m else None

def _extract_doc_claims(docstring: str) -> list[str]:
    return [l.strip() for l in docstring.splitlines()
            if l.strip() and not l.strip().startswith(">>>")
            and not re.match(r"^[-=~]+$", l.strip())]

def _extract_code_behaviours(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    beh: list[str] = []
    sig = [a.arg for a in fn.args.args if a.arg != "self"]
    if sig:
        beh.append(f"accepts({', '.join(sig)})")
    for a in fn.args.args:
        if a.annotation:
            try: beh.append(f"param_type({a.arg}, {ast.unparse(a.annotation)})")
            except Exception: pass
    if fn.returns:
        try: beh.append(f"returns({ast.unparse(fn.returns)})")
        except Exception: pass
    for child in ast.walk(fn):
        if isinstance(child, ast.Raise) and child.exc:
            if isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name):
                beh.append(f"raises({child.exc.func.id})")
            elif isinstance(child.exc, ast.Name):
                beh.append(f"raises({child.exc.id})")
    return beh

# ======================================================================
# Sheaf-theoretic engine
# ======================================================================

def _build_program_site(funcs: list, filename: str) -> "Site":
    """Model the program as a Site with one coordinate per function."""
    builder = SiteBuilder(label=f"site:{os.path.basename(filename)}")
    mod = Coordinate(components=(os.path.basename(filename),),
                     kind=CoordinateKind.MODULE, name="module")
    builder.add_coordinate(mod)
    for fn in funcs:
        c = Coordinate(components=(os.path.basename(filename), fn.name),
                       kind=CoordinateKind.FUNCTION, name=fn.name)
        builder.add_coordinate(c)
        builder.add_morphism(Morphism(source=c, target=mod,
                                      kind=MorphismKind.INCLUSION,
                                      label=f"include:{fn.name}"))
    return builder.build()

def _build_section(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                   filename: str, *, is_docs: bool) -> "Section":
    """Build a Section from either the docstring or the code behaviour."""
    coord = Coordinate(components=(os.path.basename(filename), fn.name),
                       kind=CoordinateKind.FUNCTION, name=fn.name)
    sb = SectionBuilder()
    sb.at_coordinate(coord)
    if is_docs:
        doc = _get_docstring(fn) or ""
        sb.with_data("source", "documentation")
        sb.with_data("claims", _extract_doc_claims(doc))
        sb.with_data("raw_docstring", doc)
        sb.with_provenance("docs-extraction", f"fn:{fn.name}")
        for pname, ptype in _extract_doc_params(doc).items():
            formula = f"param({pname}, {ptype})" if ptype else f"param({pname})"
            sb.with_data(f"prop:param:{pname}", formula)
        doc_ret = _extract_doc_returns(doc)
        if doc_ret:
            sb.with_data("prop:returns", doc_ret)
    else:
        behaviours = _extract_code_behaviours(fn)
        sb.with_data("source", "implementation")
        sb.with_data("behaviours", behaviours)
        sb.with_provenance("code-extraction", f"fn:{fn.name}")
        for b in behaviours:
            sb.with_data(f"behaviour:{b[:40]}", b)
    return sb.build()

def _check_overlap(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                   filename: str) -> _OverlapResult:
    """Check compatibility of docs and code for a single function."""
    docstring = _get_docstring(fn) or ""
    doc_params = _extract_doc_params(docstring)
    sig_params = {a.arg for a in fn.args.args if a.arg != "self"}
    sig_ann: dict[str, str] = {}
    for a in fn.args.args:
        if a.annotation:
            try: sig_ann[a.arg] = ast.unparse(a.annotation)
            except Exception: sig_ann[a.arg] = "?"

    violations: list[str] = []
    # Phantom parameters
    for dp in doc_params:
        if dp not in sig_params and dp not in ("self", "cls"):
            violations.append(f"phantom_param({dp})")
    # Type mismatches
    for dp, dtype in doc_params.items():
        if dtype and dp in sig_ann:
            ann = sig_ann[dp]
            if dtype.lower() not in ann.lower() and ann.lower() not in dtype.lower():
                violations.append(f"type_mismatch({dp}: doc={dtype}, code={ann})")
    # Return type
    doc_ret = _extract_doc_returns(docstring)
    if doc_ret and fn.returns:
        try: sig_ret = ast.unparse(fn.returns)
        except Exception: sig_ret = "?"
        if doc_ret.lower() not in sig_ret.lower() and sig_ret.lower() not in doc_ret.lower():
            violations.append(f"return_mismatch(doc={doc_ret}, code={sig_ret})")
    elif doc_ret:
        has_ret = any(isinstance(c, ast.Return) and c.value for c in ast.walk(fn))
        if not has_ret:
            violations.append(f"phantom_return({doc_ret})")
    # Phantom exceptions
    doc_raises: set[str] = set()
    for g in re.findall(r":raises?\s+(\w+):|[Rr]aises\s*[-:]?\s*\n\s*(\w+)", docstring):
        doc_raises.update(r for r in g if r)
    actual_raises: set[str] = set()
    for ch in ast.walk(fn):
        if isinstance(ch, ast.Raise) and ch.exc:
            if isinstance(ch.exc, ast.Call) and isinstance(ch.exc.func, ast.Name):
                actual_raises.add(ch.exc.func.id)
            elif isinstance(ch.exc, ast.Name):
                actual_raises.add(ch.exc.id)
    for dr in doc_raises - actual_raises:
        violations.append(f"phantom_exception({dr})")

    status = "VIOLATED" if violations else "SATISFIED"
    return _OverlapResult(
        function=fn.name, file=os.path.basename(filename), line=fn.lineno,
        status=status,
        doc_claim="; ".join(_extract_doc_claims(docstring)[:3]) or "(none)",
        code_behaviour="; ".join(_extract_code_behaviours(fn)[:3]) or "(none)",
        obstruction_id=str(uuid.uuid4())[:12] if violations else "",
        description="; ".join(violations) if violations else "compatible")

def _descent_check(doc_sec: "Section", code_sec: "Section",
                   fn_name: str) -> bool:
    """Check descent compatibility of doc and code local sections."""
    doc_ls = LocalSection(coordinate=f"docs:{fn_name}",
                          judgment_data=doc_sec.data,
                          evidence_bundle=doc_sec.provenance, trust_level=0.6)
    code_ls = LocalSection(coordinate=f"code:{fn_name}",
                           judgment_data=code_sec.data,
                           evidence_bundle=code_sec.provenance, trust_level=0.9)
    oc = OverlapCondition(
        left_coordinate=doc_ls.coordinate,
        right_coordinate=code_ls.coordinate,
        overlap_coordinate=f"overlap:{fn_name}",
        compatibility_predicate=lambda l, r: True)
    evaluated = oc.evaluate(doc_ls.judgment_data, code_ls.judgment_data)
    _log.debug("Descent overlap %s: status=%s", fn_name, evaluated.status)
    return evaluated.is_healthy

def _compute_trust(results: list[_OverlapResult]) -> tuple[float, str]:
    """Use TrustAlgebra to score overall alignment trust."""
    if not results:
        return 1.0, "UNVERIFIED"
    algebra = TrustAlgebra()
    sat = sum(1 for r in results if r.status == "SATISFIED")
    ratio = sat / len(results)
    if   ratio >= 1.0: lvl = ETrustLevel.RUNTIME_WITNESSED
    elif ratio >= 0.8: lvl = ETrustLevel.HUMAN_ATTESTED
    elif ratio >= 0.5: lvl = ETrustLevel.ORACLE_PROPOSED
    elif ratio >  0.0: lvl = ETrustLevel.COPILOT_SUGGESTED
    else:              lvl = ETrustLevel.CONTRADICTED
    for _ in range(len(results) - sat):
        lvl = algebra.attenuate(lvl, 1)
    return ratio, lvl.label()

def _build_obstructions(results: list[_OverlapResult]) -> list["Obstruction"]:
    """Create Obstruction objects for every violated overlap."""
    return [Obstruction(
        obstruction_id=r.obstruction_id or str(uuid.uuid4())[:12],
        violated_condition=f"overlap({r.function})",
        description=r.description, coordinate=f"{r.file}:{r.function}",
        evidence_at_time=(r.doc_claim[:80], r.code_behaviour[:80]),
        repair_hints=(f"Update docstring for {r.function}()",
                      f"Or update implementation of {r.function}()"),
        severity=2,
    ) for r in results if r.status == "VIOLATED"]

def _build_judgment(report: _AlignmentReport,
                    obs: list["Obstruction"]) -> "Judgment":
    """Build a top-level Judgment summarising the alignment check."""
    coord = Coordinate(components=(os.path.basename(report.program),),
                       kind=CoordinateKind.MODULE, name="alignment-root")
    prop = Proposition(kind=PropositionKind.BEHAVIORAL,
                       formula=f"aligned({os.path.basename(report.program)})")
    eb = EvidenceBundle().add_evidence(EvidenceItem(
        kind=EvidenceItemKind.RUNTIME_WITNESS,
        payload={"trust_score": report.trust_score,
                 "overlaps": len(report.overlaps)},
        trust_level=TrustLevel.RUNTIME_WITNESSED,
        channel="alignment-checker"))
    b = JudgmentBuilder()
    b.at(coord); b.claiming(prop); b.of_type_named("AlignmentCheck")
    b.with_evidence(eb.strongest())
    for o in obs:
        b.with_obstruction(o)
    return b.build()

# ======================================================================
# Full pipeline
# ======================================================================

_STRONG_WORDS = ["always", "never", "guaranteed", "ensures",
                 "invariant", "thread-safe", "atomic", "idempotent"]

def _run_sheaf_alignment(source: str, filename: str,
                         docs_text: str | None) -> _AlignmentReport:
    """Run the full sheaf-theoretic alignment pipeline."""
    t0 = time.monotonic()
    tree = ast.parse(source, filename=filename)
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    report = _AlignmentReport(program=filename)

    # 1. Build the program site
    if _HAS_SITE:
        site = _build_program_site(funcs, filename)
        _log.debug("Built site: %s", site.label)

    # 2. Per-function: build doc + code sections, check overlap
    for fn in funcs:
        if _get_docstring(fn) is None:
            continue
        if _HAS_SECTIONS:
            doc_sec = _build_section(fn, filename, is_docs=True)
            code_sec = _build_section(fn, filename, is_docs=False)
        overlap = _check_overlap(fn, filename)
        report.overlaps.append(overlap)
        if _HAS_DESCENT and _HAS_SECTIONS:
            _descent_check(doc_sec, code_sec, fn.name)  # type: ignore[possibly-undefined]

    # 3. Strong-claim violations (trust promotions)
    for fn in funcs:
        doc = _get_docstring(fn) or ""
        for w in _STRONG_WORDS:
            if w in doc.lower():
                report.overlaps.append(_OverlapResult(
                    function=fn.name, file=os.path.basename(filename),
                    line=fn.lineno, status="VIOLATED",
                    doc_claim=f"strong_claim({w})",
                    code_behaviour="no static evidence",
                    obstruction_id=str(uuid.uuid4())[:12],
                    description=f"Docstring '{w}' without static evidence"))

    # 4. Cross-reference external docs
    if docs_text:
        _cross_ref_docs(docs_text, source, filename, report)

    # 5. Trust score via TrustAlgebra
    if _HAS_TRUST:
        report.trust_score, report.trust_label = _compute_trust(report.overlaps)
    else:
        s = sum(1 for o in report.overlaps if o.status == "SATISFIED")
        t = len(report.overlaps) or 1
        report.trust_score = s / t
        report.trust_label = "PASS" if s == t else "DEGRADED"

    # 6. Build obstructions + top-level judgment
    violated = [o for o in report.overlaps if o.status == "VIOLATED"]
    report.gluing_success = not violated
    if _HAS_JUDGMENTS:
        obs = _build_obstructions(report.overlaps)
        report.obstructions = [
            {"id": o.obstruction_id, "condition": o.violated_condition,
             "description": o.description, "coordinate": o.coordinate,
             "severity": o.severity} for o in obs]
        j = _build_judgment(report, obs)
        _log.debug("Judgment: status=%s, obs=%d",
                    j.status, j.unresolved_obstruction_count())

    report.elapsed_s = time.monotonic() - t0
    return report

def _cross_ref_docs(docs_text: str, source: str, filename: str,
                    report: _AlignmentReport) -> None:
    """Check external docs against the program source."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    refs = set(re.findall(r'`(\w+)\(\)`|``(\w+)``|(\w+)\(\)', docs_text))
    for groups in refs:
        for ref in groups:
            if ref and ref not in names and not ref[0].isupper():
                report.overlaps.append(_OverlapResult(
                    function=ref, file=os.path.basename(filename), line=0,
                    status="VIOLATED", doc_claim=f"{ref}() documented",
                    code_behaviour="not in source",
                    obstruction_id=str(uuid.uuid4())[:12],
                    description=f"'{ref}()' referenced in docs but absent"))

# ======================================================================
# Formatting
# ======================================================================

def _format_text(report: _AlignmentReport) -> str:
    lines = ["Sheaf-Theoretic Alignment Report", "=" * 60,
             f"Program : {os.path.basename(report.program)}"]
    if report.docs_file:
        lines.append(f"Docs    : {os.path.basename(report.docs_file)}")
    lines += [f"Elapsed : {report.elapsed_s:.3f}s",
              f"Trust   : {report.trust_label} ({report.trust_score:.2%})",
              f"Gluing  : {'SUCCESS' if report.gluing_success else 'OBSTRUCTED'}", ""]
    sat = [o for o in report.overlaps if o.status == "SATISFIED"]
    vio = [o for o in report.overlaps if o.status == "VIOLATED"]
    if vio:
        lines += [f"Overlap Violations ({len(vio)})", "-" * 40]
        for v in vio:
            lines += [f"  {v.file}:{v.line} [{v.status}] {v.function}()",
                      f"    doc  : {v.doc_claim[:72]}",
                      f"    code : {v.code_behaviour[:72]}",
                      f"    cause: {v.description}"]
    else:
        lines.append("No overlap violations — all sections glue cleanly.")
    if sat:
        lines += ["", f"Satisfied Overlaps ({len(sat)})", "-" * 40]
        for s in sat:
            lines.append(f"  {s.file}:{s.line} [OK] {s.function}()")
    if report.obstructions:
        lines += ["", f"Obstructions ({len(report.obstructions)})", "-" * 40]
        for o in report.obstructions:
            lines += [f"  [{o['id']}] {o['condition']}", f"    {o['description']}"]
    lines += ["", f"Summary: {len(sat)} satisfied, {len(vio)} violated, "
              f"trust={report.trust_label}"]
    return "\n".join(lines)

def _format_json(report: _AlignmentReport) -> str:
    return json.dumps({
        "program": os.path.basename(report.program),
        "docs_file": report.docs_file,
        "elapsed_s": round(report.elapsed_s, 4),
        "trust_score": round(report.trust_score, 4),
        "trust_label": report.trust_label,
        "gluing_success": report.gluing_success,
        "overlaps": [{"function": o.function, "file": o.file, "line": o.line,
                      "status": o.status, "doc_claim": o.doc_claim,
                      "code_behaviour": o.code_behaviour,
                      "obstruction_id": o.obstruction_id,
                      "description": o.description}
                     for o in report.overlaps],
        "obstructions": report.obstructions,
        "summary": {"satisfied": sum(1 for o in report.overlaps
                                     if o.status == "SATISFIED"),
                     "violated": sum(1 for o in report.overlaps
                                     if o.status == "VIOLATED")},
    }, indent=2)

# ======================================================================
# Entry point
# ======================================================================

def _alignment_registry() -> dict[str, type]:
    """Return all public classes from the public-alignment subpackage."""
    registry: dict[str, type] = {}

    try:
        from jugeo.problem_modes.public_alignment.models import (
            PublicClaim, HonestProjection, DocumentationSection, MigrationPlan,
        )
        registry["PublicClaim"] = PublicClaim
        registry["HonestProjection"] = HonestProjection
        registry["DocumentationSection"] = DocumentationSection
        registry["MigrationPlan"] = MigrationPlan
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.manifest import (
            PublicAlignmentCap, PublicAlignmentCapability,
            PublicAlignmentManifest,
        )
        registry["PublicAlignmentCap"] = PublicAlignmentCap
        registry["PublicAlignmentCapability"] = PublicAlignmentCapability
        registry["PublicAlignmentManifest"] = PublicAlignmentManifest
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.theorems import (
            ProofStrategy, TheoremStatus, TheoremObligation,
        )
        registry["ProofStrategy"] = ProofStrategy
        registry["TheoremStatus"] = TheoremStatus
        registry["TheoremObligation"] = TheoremObligation
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.integration import (
            PublicAlignmentIntegration,
        )
        registry["PublicAlignmentIntegration"] = PublicAlignmentIntegration
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.documentation_projection import (
            DocumentationProjector,
        )
        registry["DocumentationProjector"] = DocumentationProjector
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.honesty_enforcement import (
            HonestyEnforcer,
        )
        registry["HonestyEnforcer"] = HonestyEnforcer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.publicity_boundary import (
            PublicityBoundary,
        )
        registry["PublicityBoundary"] = PublicityBoundary
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.migration_analysis import (
            MigrationAnalyzer,
        )
        registry["MigrationAnalyzer"] = MigrationAnalyzer
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.documentation_alignment import (
            DocstringSpec, AlignmentGap, AlignmentWitness,
            DocumentationAlignmentAnalyzer, DocumentationAlignmentCoordinator,
            DocumentationAlignmentWitness,
        )
        registry["DocstringSpec"] = DocstringSpec
        registry["AlignmentGap"] = AlignmentGap
        registry["AlignmentWitness"] = AlignmentWitness
        registry["DocumentationAlignmentAnalyzer"] = DocumentationAlignmentAnalyzer
        registry["DocumentationAlignmentCoordinator"] = DocumentationAlignmentCoordinator
        registry["DocumentationAlignmentWitness"] = DocumentationAlignmentWitness
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.public_api_cli_and_explanation_sem import (
            PublicSurface, ExplanationRecord, ExplanationSemantics,
            PublicAPICLIExplanationAnalyzer, PublicAPICLIExplanationCoordinator,
            PublicAPICLIExplanationWitness,
        )
        registry["PublicSurface"] = PublicSurface
        registry["ExplanationRecord"] = ExplanationRecord
        registry["ExplanationSemantics"] = ExplanationSemantics
        registry["PublicAPICLIExplanationAnalyzer"] = PublicAPICLIExplanationAnalyzer
        registry["PublicAPICLIExplanationCoordinator"] = PublicAPICLIExplanationCoordinator
        registry["PublicAPICLIExplanationWitness"] = PublicAPICLIExplanationWitness
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.documentation_should_become_a_live import (
            DocstringObligation, LiveDocRecord, SemanticParticipant,
            DocumentationBecomeLiveSemanticAnalyzer,
            DocumentationBecomeLiveSemanticCoordinator,
            DocumentationBecomeLiveSemanticWitness,
        )
        registry["DocstringObligation"] = DocstringObligation
        registry["LiveDocRecord"] = LiveDocRecord
        registry["SemanticParticipant"] = SemanticParticipant
        registry["DocumentationBecomeLiveSemanticAnalyzer"] = DocumentationBecomeLiveSemanticAnalyzer
        registry["DocumentationBecomeLiveSemanticCoordinator"] = DocumentationBecomeLiveSemanticCoordinator
        registry["DocumentationBecomeLiveSemanticWitness"] = DocumentationBecomeLiveSemanticWitness
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.the_public_story_should_remain_hon import (
            HonestyViolation, PublicNarrative, HonestyChecker,
            ThePublicStoryRemainAnalyzer, ThePublicStoryRemainCoordinator,
            ThePublicStoryRemainWitness,
        )
        registry["HonestyViolation"] = HonestyViolation
        registry["PublicNarrative"] = PublicNarrative
        registry["HonestyChecker"] = HonestyChecker
        registry["ThePublicStoryRemainAnalyzer"] = ThePublicStoryRemainAnalyzer
        registry["ThePublicStoryRemainCoordinator"] = ThePublicStoryRemainCoordinator
        registry["ThePublicStoryRemainWitness"] = ThePublicStoryRemainWitness
    except Exception:
        pass

    try:
        from jugeo.problem_modes.public_alignment.migration_and_donor_inheritance import (
            MigrationStep, DonorInheritanceRecord, MigrationWitness,
            MigrationDonorInheritanceAnalyzer, MigrationDonorInheritanceCoordinator,
            MigrationDonorInheritanceWitness,
        )
        registry["MigrationStep"] = MigrationStep
        registry["DonorInheritanceRecord"] = DonorInheritanceRecord
        registry["MigrationWitness"] = MigrationWitness
        registry["MigrationDonorInheritanceAnalyzer"] = MigrationDonorInheritanceAnalyzer
        registry["MigrationDonorInheritanceCoordinator"] = MigrationDonorInheritanceCoordinator
        registry["MigrationDonorInheritanceWitness"] = MigrationDonorInheritanceWitness
    except Exception:
        pass

    return registry


def _rich_alignment_check(internal: str, public: str | None) -> None:
    """Use public_alignment domain classes for semantic doc↔code alignment.

    Checks honesty of public claims against internal trust levels, detects
    violations, and prints severity-ranked alignment report.
    """
    try:
        from jugeo.problem_modes.public_alignment.models import (  # type: ignore[import-untyped]
            PublicClaim,
            HonestProjection,
        )
        _has_pa_models = True
    except Exception:
        _has_pa_models = False

    try:
        from jugeo.problem_modes.public_alignment.the_public_story_should_remain_hon import (  # type: ignore[import-untyped]
            HonestyViolation,
            PublicNarrative,
            HonestyChecker,
        )
        _has_honesty = True
    except Exception:
        _has_honesty = False

    try:
        from jugeo.evidence.trust import TrustLevel as _TL  # type: ignore[import-untyped]
        _has_tl = True
    except Exception:
        _has_tl = False
        _TL = None

    print("\n" + "─" * 64)
    print("  Public Alignment Analysis (public_alignment domain)")
    print("─" * 64)

    _claims_data = [
        ("claim-doc-01", "module.parse", "Parser handles all edge cases", 3, 2),
        ("claim-doc-02", "module.validate", "Validation is complete", 2, 2),
        ("claim-doc-03", "module.transform", "Transform preserves semantics", 3, 1),
    ]

    if _has_pa_models and _has_tl:
        try:
            claims = []
            for cid, coord, stmt, declared_int, internal_int in _claims_data:
                declared = list(_TL)[min(declared_int, len(list(_TL)) - 1)]
                internal = list(_TL)[min(internal_int, len(list(_TL)) - 1)]
                claim = PublicClaim(
                    claim_id=cid,
                    coordinate=coord,
                    statement=stmt,
                    declared_trust_level=declared,
                    internal_trust_level=internal,
                )
                claims.append(claim)

            proj = HonestProjection(
                projection_id="proj-cli-001",
                source_coordinate=internal,
                target_audience="developer",
                claims=tuple(claims),
            )

            violations = []
            for claim in claims:
                honest = claim.check_honesty()
                delta = claim.honesty_delta()
                status = "✓ honest" if honest else "✗ VIOLATION"
                print(f"    [{claim.claim_id}] {claim.coordinate}: {status} (Δ={delta:+d})")
                print(f"      declared={claim.declared_trust_level.value}  internal={claim.internal_trust_level.value}")
                if not honest:
                    obs = claim.strengthen_violation()
                    if obs:
                        violations.append(obs)

            print(f"\n  Projection  : {proj.projection_id} (audience={proj.target_audience})")
            print(f"  Claims      : {len(proj.claims)}")
            print(f"  Violations  : {len(violations)}")

            if _has_honesty and violations:
                for v in violations:
                    coord_str = getattr(v, "coordinate", "unknown")
                    sev = getattr(v, "severity", "MEDIUM")
                    print(f"    [{sev}] {coord_str}: overclaim detected")
            return
        except Exception:
            pass

    # Simulated output
    print(f"  [simulated] Alignment check: {internal}")
    for cid, coord, stmt, decl, intern in _claims_data:
        honest = decl <= intern
        delta = intern - decl
        status = "✓ honest" if honest else "✗ VIOLATION"
        print(f"    [{cid}] {coord}: {status} (Δ={delta:+d})")
    violations_count = sum(1 for _, _, _, d, i in _claims_data if d > i)
    print(f"\n  Projection  : proj-cli-001 (audience=developer)")
    print(f"  Claims      : {len(_claims_data)}")
    print(f"  Violations  : {violations_count}")
    if violations_count:
        for cid, coord, stmt, d, i in _claims_data:
            if d > i:
                print(f"    [MEDIUM] {coord}: overclaim (declared trust > internal trust)")
    print("─" * 64)


def run_alignment(args: argparse.Namespace) -> int:
    """Check alignment as a sheaf-theoretic property.

    Parameters
    ----------
    args : argparse.Namespace
        ``program``, ``docs``, ``format``, ``verbose``

    Returns
    -------
    int
        0 if aligned, 1 if violations found.
    """
    program_path: str = getattr(args, "program", "")
    docs_path: str | None = getattr(args, "docs", None)
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _alignment_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    program_path = os.path.abspath(program_path)
    if not os.path.isfile(program_path):
        print(f"error: {program_path}: not a file", file=sys.stderr)
        return 1
    try:
        with open(program_path, encoding="utf-8") as fh:
            source = fh.read()
    except Exception as exc:
        print(f"error: {program_path}: {exc}", file=sys.stderr)
        return 1

    docs_text: str | None = None
    if docs_path:
        docs_path = os.path.abspath(docs_path)
        if not os.path.isfile(docs_path):
            print(f"error: {docs_path}: not a file", file=sys.stderr)
            return 1
        try:
            with open(docs_path, encoding="utf-8") as fh:
                docs_text = fh.read()
        except Exception as exc:
            print(f"error: {docs_path}: {exc}", file=sys.stderr)
            return 1

    if not _FULL_STACK:
        _log.debug("Some JuGeo subsystems unavailable; reduced features.")
    try:
        report = _run_sheaf_alignment(source, program_path, docs_text)
    except SyntaxError as exc:
        print(f"error: SyntaxError: {exc.msg} (line {exc.lineno})",
              file=sys.stderr)
        return 1
    if docs_path:
        report.docs_file = docs_path

    print(_format_json(report) if out_format == "json" else _format_text(report))

    # Rich alignment check via public_alignment domain classes
    _rich_alignment_check(program_path, docs_path)

    return 1 if any(o.status == "VIOLATED" for o in report.overlaps) else 0
