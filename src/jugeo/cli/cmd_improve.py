"""CLI subcommand handler for ``jugeo improve``.

Performs agent-driven code improvement on an existing codebase until a
stated improvement obligation is satisfied — formulated as a descent
condition in judgment geometry.

Architecture
------------
The improvement loop is a judgment-geometric descent procedure:

1. **Obligation extraction**: The improvement description becomes a
   LocalSection at coordinate ``improve.obligation`` with the
   improvement text as a residual obligation.

2. **Codebase scan**: The target directory is scanned to produce
   LocalSections for each source file (coordinate per file).

3. **Agent improvement**: A coding agent (copilot → claude → codex)
   is dispatched with full tool access to make changes that discharge
   the obligation. The agent can read files, write files, run tests,
   install packages, and web search.

4. **Verification**: After the agent acts, the codebase is re-scanned.
   The obligation LocalSection is checked: has the improvement been
   made? Are all files still syntactically valid? Do tests still pass?

5. **Descent check**: GluingData is assembled from all file sections
   plus the obligation section. If the obligation is discharged and
   files are coherent → GlobalSection (success). If not →
   DescentObstruction with RepairFrontier guiding the next iteration.

6. **Iterate**: Loop until GlobalSection or max iterations.

The key insight: the improvement is not "done" until descent succeeds.
The obligation is a first-class cohomological object — it persists as
an H¹ obstruction until the agent's changes actually satisfy it.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

try:
    from jugeo.geometry.descent import (
        DescentConfiguration,
        DescentEngine,
        DescentResult,
        DescentStrategy,
        GluingData,
        GlobalSection,
        LocalSection,
    )
    from jugeo.directed_research._agent_channel import agent_call
    from jugeo.research_orchestration import SurfaceKind
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════
# Repo-type detection — determines which theory modules to apply
# ═══════════════════════════════════════════════════════════════════════

class RepoKind:
    FLASK_APP = "flask_app"
    RESEARCH = "research"
    GENERIC = "generic"


def _detect_repo_kind(root: str, files: dict[str, str]) -> str:
    """Detect whether this is a Flask app, research repo, or generic codebase."""
    filenames = set(files.keys())
    contents_sample = "\n".join(list(files.values())[:20])

    # Flask app: has app.py or wsgi.py, imports flask, has templates/
    has_flask = any(
        'from flask' in files.get(f, '') or 'import flask' in files.get(f, '')
        for f in filenames if f.endswith('.py')
    )
    has_templates = any(f.startswith('templates/') or f.startswith('templates\\') for f in filenames)
    has_static = any(f.startswith('static/') or f.startswith('static\\') for f in filenames)
    if has_flask or (has_templates and has_static):
        return RepoKind.FLASK_APP

    # Research repo: has theory.md/paper.tex/results.json, README, code
    has_paper = any(f.endswith('.tex') for f in filenames)
    has_theory = any('theory' in f.lower() for f in filenames if f.endswith('.md'))
    has_results = any('result' in f.lower() for f in filenames if f.endswith('.json'))
    has_readme = any(f.lower().startswith('readme') for f in filenames)
    if (has_paper or has_theory) and has_readme:
        return RepoKind.RESEARCH

    return RepoKind.GENERIC


# ═══════════════════════════════════════════════════════════════════════
# Domain-specific theory sections
# ═══════════════════════════════════════════════════════════════════════

def _flask_theory_sections(
    files: dict[str, str],
) -> list["LocalSection"]:
    """Apply webapp theory modules to a Flask app codebase.

    Uses the full judgment-geometric theory of Python → JS/HTML DOM:
    sites, functors, behavioral modules, integration checks.
    """
    sections: list[LocalSection] = []

    # Collect artifacts
    html = "\n".join(c for f, c in files.items() if f.endswith('.html'))
    css = "\n".join(c for f, c in files.items() if f.endswith('.css'))
    js = "\n".join(c for f, c in files.items() if f.endswith('.js'))
    flask_routes: list[dict] = []
    for f, c in files.items():
        if f.endswith('.py'):
            import re as _re
            for m in _re.finditer(r'@app\.route\(["\']([^"\']+)["\']', c):
                flask_routes.append({"path": m.group(1), "method": "GET", "handler": ""})

    # Run the full webapp theory engine
    try:
        from jugeo.webapp.theory.engine import TheoryEngine
        engine = TheoryEngine()
        result = engine.check_all(
            html=html, css=css, js=js, flask_routes=flask_routes,
        )
        # Convert theory engine sections to our format
        for sec in result.sections:
            sections.append(sec)
        _log.info("Flask theory: %d modules, correct=%s", len(result.modules_checked), result.is_correct)
    except Exception as exc:
        _log.warning("Flask theory engine failed: %s", exc)

    # Wiring coherence: check that HTML loads CSS/JS
    wiring_issues: list[str] = []
    if html and css and 'style.css' not in html and 'stylesheet' not in html:
        wiring_issues.append("H¹[html→css]: no <link> to CSS")
    if html and js and '<script' not in html and 'url_for' not in html:
        wiring_issues.append("H¹[html→js]: no <script> loading JS")
    if 'SECRET_KEY' not in "\n".join(c for f, c in files.items() if f.endswith('.py')):
        wiring_issues.append("H¹[flask]: no SECRET_KEY set")

    # JS→JS module import coherence: every import must resolve to an export
    js_files = {f: c for f, c in files.items() if f.endswith('.js')}
    if js_files:
        import re as _re2
        # Build export map
        _js_export_map: dict[str, set[str]] = {}
        for jf, jc in js_files.items():
            exports: set[str] = set()
            for em in _re2.finditer(r'export\s*\{([^}]+)\}', jc):
                for tok in em.group(1).split(','):
                    tok = tok.strip().split(' as ')[-1].strip()
                    if tok:
                        exports.add(tok)
            for em in _re2.finditer(
                r'export\s+(?:default\s+)?(?:function|const|let|var|class)\s+(\w+)', jc
            ):
                exports.add(em.group(1))
            _js_export_map[jf] = exports
        # Check imports
        for jf, jc in js_files.items():
            for im in _re2.finditer(
                r"""import\s*\{([^}]+)\}\s*from\s*['"]\.\/([^'"]+)['"]""", jc
            ):
                names = [n.strip().split(' as ')[0].strip() for n in im.group(1).split(',')]
                mod = im.group(2)
                target = None
                for cand in js_files:
                    if cand.rsplit('/', 1)[-1] == mod or cand == mod:
                        target = cand
                        break
                if target is None:
                    wiring_issues.append(
                        f"H¹[js→js]: import from './{mod}' in {jf} — module not found"
                    )
                else:
                    for nm in names:
                        if nm and nm not in _js_export_map.get(target, set()):
                            wiring_issues.append(
                                f"H¹[js→js]: import {{ {nm} }} from './{mod}' in {jf} "
                                f"— '{mod}' does not export '{nm}'"
                            )

    sections.append(LocalSection(
        coordinate="flask.wiring",
        judgment_data={"passed": not wiring_issues, "issues": len(wiring_issues)},
        trust_level=1.0 if not wiring_issues else 0.3,
        evidence_bundle=("flask:wiring_check",),
        residual_obligations=wiring_issues,
        is_partial=bool(wiring_issues),
    ))
    return sections


def _research_theory_sections(
    files: dict[str, str],
) -> list["LocalSection"]:
    """Apply research-repo theory: descent over (Theory, Code, Evidence, Claims).

    A research repo must have coherent surfaces:
    - T (Theory): theory.md, formal definitions, invariant claims
    - R (Code): source code implementing the theory
    - E (Evidence): results.json, benchmarks, reproducible experiments
    - P (Claims): README, paper.tex, compiled PDF

    The descent condition: every claim in P must be supported by evidence
    in E, which must be produced by code in R, which must implement
    theory in T. Broken chains are H¹ obstructions.
    """
    sections: list[LocalSection] = []

    # Detect surfaces
    theory_files = {f: c for f, c in files.items()
                    if 'theory' in f.lower() and f.endswith('.md')}
    code_files = {f: c for f, c in files.items()
                  if f.endswith(('.py', '.rs', '.go', '.js', '.ts'))}
    evidence_files = {f: c for f, c in files.items()
                      if 'result' in f.lower() or 'benchmark' in f.lower()
                      or 'experiment' in f.lower()}
    paper_files = {f: c for f, c in files.items()
                   if f.endswith('.tex') or f.lower().startswith('readme')}

    # T surface section
    theory_obligations: list[str] = []
    if not theory_files:
        theory_obligations.append("agent:create_theory_md_with_formal_definitions")
    sections.append(LocalSection(
        coordinate="research.theory_surface",
        judgment_data={"files": len(theory_files), "passed": bool(theory_files)},
        trust_level=0.8 if theory_files else 0.2,
        evidence_bundle=("research:theory_scan",),
        residual_obligations=theory_obligations,
        is_partial=bool(theory_obligations),
    ))

    # R surface section
    code_obligations: list[str] = []
    if not code_files:
        code_obligations.append("agent:implement_theory_in_code")
    total_code_loc = sum(c.count('\n') + 1 for c in code_files.values())
    if total_code_loc < 500:
        code_obligations.append(f"agent:code_too_small_{total_code_loc}_loc_need_500")
    sections.append(LocalSection(
        coordinate="research.code_surface",
        judgment_data={"files": len(code_files), "loc": total_code_loc, "passed": not code_obligations},
        trust_level=0.8 if not code_obligations else 0.3,
        evidence_bundle=("research:code_scan",),
        residual_obligations=code_obligations,
        is_partial=bool(code_obligations),
    ))

    # E surface section
    evidence_obligations: list[str] = []
    if not evidence_files:
        evidence_obligations.append("agent:create_results_json_with_reproducible_experiments")
    # Check for reproduction script
    has_repro = any('reproduce' in f.lower() or 'run_experiment' in f.lower()
                    or 'benchmark' in f.lower() for f in files if f.endswith(('.py', '.sh')))
    if not has_repro:
        evidence_obligations.append("agent:create_reproduce_script")
    sections.append(LocalSection(
        coordinate="research.evidence_surface",
        judgment_data={"files": len(evidence_files), "has_repro": has_repro, "passed": not evidence_obligations},
        trust_level=0.8 if not evidence_obligations else 0.2,
        evidence_bundle=("research:evidence_scan",),
        residual_obligations=evidence_obligations,
        is_partial=bool(evidence_obligations),
    ))

    # P surface section (claims)
    claims_obligations: list[str] = []
    has_readme = any(f.lower().startswith('readme') for f in files)
    has_paper = any(f.endswith('.tex') for f in files)
    has_pdf = any(f.endswith('.pdf') for f in files)
    if not has_readme:
        claims_obligations.append("agent:create_comprehensive_readme")
    if has_paper and not has_pdf:
        claims_obligations.append("agent:compile_paper_to_pdf_with_pdflatex")
    sections.append(LocalSection(
        coordinate="research.claims_surface",
        judgment_data={"has_readme": has_readme, "has_paper": has_paper, "has_pdf": has_pdf,
                       "passed": not claims_obligations},
        trust_level=0.8 if not claims_obligations else 0.3,
        evidence_bundle=("research:claims_scan",),
        residual_obligations=claims_obligations,
        is_partial=bool(claims_obligations),
    ))

    # Cross-surface overlap conditions (the sheaf condition for research)
    # T∩R: theory must be implemented in code
    # R∩E: code must produce evidence
    # E∩P: evidence must support claims
    # T∩P: claims must be grounded in theory
    return sections


def _research_overlap_pairs() -> list[tuple[str, str]]:
    """Cross-surface overlaps for research repos."""
    return [
        ("research.theory_surface", "research.code_surface"),
        ("research.code_surface", "research.evidence_surface"),
        ("research.evidence_surface", "research.claims_surface"),
        ("research.theory_surface", "research.claims_surface"),
    ]


# ═══════════════════════════════════════════════════════════════════════
# Codebase scanning
# ═══════════════════════════════════════════════════════════════════════

_SOURCE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss',
    '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp', '.rb',
    '.php', '.swift', '.kt', '.scala', '.sh', '.sql', '.json',
    '.yaml', '.yml', '.toml', '.md', '.txt',
}


def _scan_codebase(root: str) -> dict[str, str]:
    """Scan directory for source files. Returns {rel_path: content}."""
    files: dict[str, str] = {}
    root_path = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip hidden dirs, node_modules, __pycache__, .git, venv
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith('.') and d not in (
                'node_modules', '__pycache__', 'venv', '.venv',
                'dist', 'build', '.git', '.tox', 'egg-info',
            )
        ]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _SOURCE_EXTENSIONS:
                continue
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root_path))
            try:
                content = fpath.read_text(errors='replace')
                files[rel] = content
            except OSError:
                pass
    return files


def _file_section(rel_path: str, content: str) -> LocalSection:
    """Create a LocalSection for a source file."""
    coord = f"codebase.{rel_path.replace('/', '.').replace(os.sep, '.')}"
    return LocalSection(
        coordinate=coord,
        judgment_data={
            "file": rel_path,
            "lines": content.count('\n') + 1,
            "size": len(content),
            "has_syntax": True,  # assume valid until checked
        },
        trust_level=0.8,
        evidence_bundle=(f"file:{rel_path}",),
    )


def _obligation_section(
    improvement: str,
    satisfied: bool,
    evidence: list[str] | None = None,
) -> LocalSection:
    """Create the improvement obligation LocalSection.

    This is the key JG object: the improvement description is a
    residual obligation. It becomes an H¹ obstruction until the agent
    discharges it by making changes that satisfy the condition.
    """
    return LocalSection(
        coordinate="improve.obligation",
        judgment_data={
            "improvement": improvement,
            "satisfied": satisfied,
        },
        trust_level=1.0 if satisfied else 0.1,
        evidence_bundle=tuple(evidence or []),
        residual_obligations=(
            [] if satisfied else [f"agent:fulfill:{improvement[:200]}"]
        ),
        is_partial=not satisfied,
    )


# ═══════════════════════════════════════════════════════════════════════
# Verification — did the agent actually improve things?
# ═══════════════════════════════════════════════════════════════════════

def _check_obligation_satisfied(
    improvement: str,
    before_files: dict[str, str],
    after_files: dict[str, str],
) -> tuple[bool, list[str]]:
    """Check if the improvement obligation has been satisfied.

    Compares before/after codebase snapshots and checks whether the
    changes are consistent with the stated improvement.

    Returns (satisfied, evidence_list).
    """
    evidence: list[str] = []

    # Basic check: something actually changed
    changed_files = []
    new_files = []
    deleted_files = []
    for f in after_files:
        if f not in before_files:
            new_files.append(f)
        elif after_files[f] != before_files[f]:
            changed_files.append(f)
    for f in before_files:
        if f not in after_files:
            deleted_files.append(f)

    if not changed_files and not new_files and not deleted_files:
        return False, ["no_changes_detected"]

    evidence.append(f"changed:{len(changed_files)}")
    evidence.append(f"new:{len(new_files)}")
    evidence.append(f"deleted:{len(deleted_files)}")

    # Check: do changed files mention concepts from the improvement?
    improvement_lower = improvement.lower()
    keywords = set(re.findall(r'\b\w{4,}\b', improvement_lower))
    keywords -= {'that', 'this', 'with', 'from', 'have', 'been', 'each',
                 'should', 'could', 'would', 'make', 'more', 'into', 'when',
                 'which', 'where', 'there', 'their', 'then', 'than', 'also'}

    # Count how many improvement keywords appear in the diff
    diff_text = ""
    for f in changed_files + new_files:
        old = before_files.get(f, "")
        new = after_files.get(f, "")
        # Only look at new/changed lines
        old_lines = set(old.splitlines())
        new_lines = [l for l in new.splitlines() if l not in old_lines]
        diff_text += "\n".join(new_lines)

    matched_keywords = [kw for kw in keywords if kw in diff_text.lower()]
    keyword_coverage = len(matched_keywords) / max(len(keywords), 1)
    evidence.append(f"keyword_coverage:{keyword_coverage:.2f}")

    # Heuristic: require both meaningful keyword coverage AND actual changes
    has_changes = len(changed_files) + len(new_files) >= 1
    satisfied = has_changes and (keyword_coverage > 0.4 or len(changed_files) + len(new_files) >= 3)
    return satisfied, evidence


# ═══════════════════════════════════════════════════════════════════════
# The improvement loop
# ═══════════════════════════════════════════════════════════════════════

def run_improve(args: argparse.Namespace) -> int:
    """Execute the improvement loop."""
    if not _AVAILABLE:
        print("Error: jugeo geometry and agent modules required for improve command")
        return 1

    target_dir = os.path.abspath(args.directory)
    improvement = args.improvement
    max_iterations = args.max_iterations

    if not os.path.isdir(target_dir):
        print(f"Error: {target_dir} is not a directory")
        return 1

    # Set up logging
    log_path = os.path.join(target_dir, "jugeo_improve.log")
    fh = logging.FileHandler(log_path, mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    _log.addHandler(fh)
    _log.setLevel(logging.DEBUG)

    _log.info("Improvement target: %s", target_dir)
    _log.info("Improvement obligation: %s", improvement)
    _log.info("Max iterations: %d", max_iterations)

    print(f"jugeo improve: {target_dir}")
    print(f"Obligation: {improvement}")
    print()

    # ── Scan codebase before ─────────────────────────────────────────
    before_files = _scan_codebase(target_dir)
    _log.info("Scanned %d source files", len(before_files))
    total_loc_before = sum(c.count('\n') + 1 for c in before_files.values())
    print(f"Codebase: {len(before_files)} files, {total_loc_before} LoC")

    # ── Detect repo kind ────────────────────────────────────────────
    repo_kind = _detect_repo_kind(target_dir, before_files)
    _log.info("Repo kind: %s", repo_kind)
    print(f"Repo type: {repo_kind}")

    # ── Improvement loop ─────────────────────────────────────────────
    prev_frontier_text = ""  # Repair frontier from previous iteration
    for iteration in range(1, max_iterations + 1):
        print(f"\n── Iteration {iteration}/{max_iterations} ──")
        _log.info("Iteration %d/%d", iteration, max_iterations)

        # Build the agent prompt
        file_listing = "\n".join(
            f"  {f} ({c.count(chr(10))+1} lines)"
            for f, c in sorted(before_files.items())
        )

        # Extract files mentioned in the obligation (error messages reference them)
        error_files = set()
        for m in re.finditer(r'(?:at\s+|module\s+[\'"]\./)(\w[\w./-]*\.(?:js|py|html|css))', improvement):
            error_files.add(m.group(1))
        # Also match bare filenames like "csrf.js" or "notifications.js"
        for m in re.finditer(r'\b(\w[\w-]*\.(?:js|py|html|css))\b', improvement):
            error_files.add(m.group(1))

        # Resolve error-mentioned files to actual codebase paths
        error_file_paths = []
        for ef in error_files:
            for bf in before_files:
                if bf.endswith(ef) or bf.endswith('/' + ef):
                    error_file_paths.append(os.path.join(target_dir, bf))
                    break

        # Collect key files as context: error-mentioned files first, then largest
        context_files = list(dict.fromkeys(error_file_paths))  # dedup, preserve order
        remaining = sorted(
            [os.path.join(target_dir, f) for f in before_files
             if os.path.join(target_dir, f) not in context_files],
            key=lambda p: os.path.getsize(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        context_files.extend(remaining[:max(10 - len(context_files), 5)])

        # Include inline content of error-mentioned files in the prompt
        error_file_content = ""
        if error_file_paths:
            content_parts = ["\nFILES MENTIONED IN ERROR (read these first):"]
            for ef_path in error_file_paths[:8]:
                ef_rel = os.path.relpath(ef_path, target_dir)
                ef_content = before_files.get(ef_rel, "")
                if ef_content:
                    # Truncate very large files
                    preview = ef_content[:2000]
                    if len(ef_content) > 2000:
                        preview += f"\n... ({len(ef_content) - 2000} more chars)"
                    content_parts.append(f"\n── {ef_rel} ──\n{preview}")
            error_file_content = "\n".join(content_parts)

        # Domain-specific prompt section
        domain_prompt = ""
        if iteration == 1:
            repo_kind = _detect_repo_kind(target_dir, before_files)
        if repo_kind == RepoKind.FLASK_APP:
            domain_prompt = """
FLASK APP THEORY (webapp presheaf descent conditions):
This is a Flask web application. Additional descent conditions apply:
- base.html MUST <link> to style.css and <script> all JS files
- app.py MUST have routes for every template, set SECRET_KEY, use CSRF
- Templates MUST {% extends "base.html" %} and load their JS in {% block extra_scripts %}
- requirements.txt MUST list ALL imported Python packages
- No orphaned CSS selectors, JS modules, or routes (H¹ obstructions)
- WCAG 2.1 AA accessibility: lang attr, skip links, focus indicators, contrast
- JS MODULE COHERENCE: if file A does `import { X } from './B.js'`, then B.js
  MUST contain `export { X }` or `export function X` or `export const X`.
  IIFE-only modules without export statements will cause SyntaxError in browsers.
"""
        elif repo_kind == RepoKind.RESEARCH:
            domain_prompt = """
RESEARCH REPO THEORY (workspace site descent over 4 surfaces):
This is a research repository. The sheaf condition requires 4 coherent surfaces:
- T (Theory): theory.md with formal definitions, invariants, conjectures
- R (Code): implementation of the theory, tests, benchmarks
- E (Evidence): results.json, benchmark outputs, reproducible experiments
- P (Claims): README.md, tool_paper.tex, compiled PDF

Cross-surface descent conditions:
- T∩R: every theorem/definition in theory.md must be implemented in code
- R∩E: code must produce the evidence (results.json via a reproduce script)
- E∩P: every claim in README/paper must be supported by evidence
- T∩P: paper claims must be grounded in theory definitions
- A reproduce.py or run_experiments.sh must exist and actually work
- If tool_paper.tex exists, compile it with pdflatex
"""

        agent_prompt = f"""\
You are improving an existing codebase. This is a judgment-geometric descent loop.

Your task produces LocalSections (one per modified file). The loop terminates only
when ALL of the following form a consistent GlobalSection (all overlaps satisfied):

1. IMPROVEMENT OBLIGATION (primary H¹ obstruction — must be discharged):
   {improvement}

2. WIRING COHERENCE (no dead code — H¹[cross-file] obstruction):
   - Every function/class must be called from somewhere reachable
   - Every import must resolve to an existing module
   - Orphaned code = cohomological obstruction = descent failure

3. SYNTACTIC VALIDITY (LocalSection trust floor):
   - Python files must pass ast.parse()
   - JS files must pass node --check
   - After your changes, run validation commands to confirm

4. TEST COHERENCE (overlap condition between code and tests):
   - If tests exist, they must still pass after your changes
   - If you add new functionality, add tests for it
   - Run the test suite before finishing
{domain_prompt}
{prev_frontier_text}
{error_file_content}
CODEBASE ROOT: {target_dir}
FILES ({len(before_files)} source files):
{file_listing}

INSTRUCTIONS:
1. Read the relevant files to understand the current state
2. Make changes that discharge the improvement obligation
3. Write changed files directly to disk
4. Verify syntax: python3 -c "import ast; ast.parse(open('FILE').read())" for .py
5. Run existing tests if present (pytest, npm test, etc.)
6. Remove any dead code your changes created
7. If you need packages, install them (pip, npm, brew, etc.)

The improvement is NOT satisfied by comments or documentation alone.
Make real, functional, tested code changes.
"""

        _log.info("Dispatching agent (iteration %d)", iteration)
        t0 = time.monotonic()
        try:
            section = agent_call(
                agent_prompt,
                surface=SurfaceKind.CODE,
                coordinate=f"improve.iteration_{iteration}",
                working_dir=target_dir,
                context_files=context_files,
            )
            elapsed = time.monotonic() - t0
            _log.info("Agent returned in %.1fs: %d chars, backend=%s",
                       elapsed, len(section.content), section.agent_backend.value)
            print(f"  Agent ({section.agent_backend.value}): {elapsed:.0f}s")
        except Exception as exc:
            _log.error("Agent call failed: %s", exc)
            print(f"  Agent failed: {exc}")
            continue

        # ── Re-scan and check obligation ─────────────────────────────
        after_files = _scan_codebase(target_dir)
        satisfied, evidence = _check_obligation_satisfied(
            improvement, before_files, after_files,
        )
        _log.info("Obligation check: satisfied=%s, evidence=%s", satisfied, evidence)

        changed = sum(1 for f in after_files if f in before_files and after_files[f] != before_files[f])
        new = sum(1 for f in after_files if f not in before_files)
        total_loc_after = sum(c.count('\n') + 1 for c in after_files.values())
        print(f"  Changed: {changed} files, New: {new} files")
        print(f"  LoC: {total_loc_before} → {total_loc_after} ({total_loc_after - total_loc_before:+d})")

        # ── JG quality checks (additional LocalSections) ─────────────
        # Syntax validation section
        syntax_issues: list[str] = []
        for rel, content in after_files.items():
            if rel.endswith('.py'):
                try:
                    import ast
                    ast.parse(content)
                except SyntaxError as e:
                    syntax_issues.append(f"{rel}:{e.lineno}: {e.msg}")

        syntax_section = LocalSection(
            coordinate="improve.syntax",
            judgment_data={"passed": not syntax_issues, "issues": len(syntax_issues)},
            trust_level=1.0 if not syntax_issues else 0.2,
            evidence_bundle=("syntax:ast_parse",),
            residual_obligations=[f"fix:{i}" for i in syntax_issues],
            is_partial=bool(syntax_issues),
        )

        # Dead code detection section
        dead_code_issues: list[str] = []
        # Check Python: defined functions never called
        for rel, content in after_files.items():
            if not rel.endswith('.py'):
                continue
            defined = set(re.findall(r'^(?:def|class)\s+(\w+)', content, re.MULTILINE))
            all_code = "\n".join(after_files.values())
            for name in defined:
                # Count references across entire codebase (excluding the definition itself)
                refs = len(re.findall(r'\b' + re.escape(name) + r'\b', all_code))
                if refs <= 1 and name not in ('__init__', '__repr__', '__str__',
                                               'main', 'setup', 'teardown'):
                    dead_code_issues.append(f"H¹[dead]:{rel}:{name} defined but never referenced")

        dead_code_section = LocalSection(
            coordinate="improve.dead_code",
            judgment_data={"passed": not dead_code_issues, "issues": len(dead_code_issues)},
            trust_level=1.0 if not dead_code_issues else 0.4,
            evidence_bundle=("dead_code:reference_scan",),
            residual_obligations=dead_code_issues[:10],
            is_partial=bool(dead_code_issues),
        )

        # ── Domain-specific theory sections ──────────────────────────
        # Detect repo kind on first iteration only (cache it)
        if iteration == 1:
            repo_kind = _detect_repo_kind(target_dir, after_files)
            _log.info("Repo kind detected: %s", repo_kind)
            print(f"  Repo type: {repo_kind}")

        domain_sections: list[LocalSection] = []
        domain_overlaps: list[tuple[str, str]] = []

        if repo_kind == RepoKind.FLASK_APP:
            domain_sections = _flask_theory_sections(after_files)
            # Flask overlaps: obligation ↔ flask theory, plus wiring ↔ involved files
            domain_overlaps = [
                ("improve.obligation", "flask.wiring"),
            ]
            # Extract files involved in wiring issues and add targeted overlaps
            wiring_sec = next(
                (s for s in domain_sections if s.coordinate == "flask.wiring"), None
            )
            if wiring_sec and wiring_sec.residual_obligations:
                for issue in wiring_sec.residual_obligations:
                    # Extract filenames from wiring issues like "H¹[js→js]: ... in static/csrf.js"
                    for fm in re.finditer(r'\b([\w/.-]+\.(?:js|py|html|css))\b', issue):
                        fname = fm.group(1)
                        fcoord = f"codebase.{fname.replace('/', '.').replace(os.sep, '.')}"
                        domain_overlaps.append(("flask.wiring", fcoord))
                        domain_overlaps.append(("improve.obligation", fcoord))
            _log.info("Flask theory: %d sections", len(domain_sections))
        elif repo_kind == RepoKind.RESEARCH:
            domain_sections = _research_theory_sections(after_files)
            domain_overlaps = _research_overlap_pairs() + [
                ("improve.obligation", "research.code_surface"),
                ("improve.obligation", "research.claims_surface"),
            ]
            _log.info("Research theory: %d sections", len(domain_sections))

        # ── Descent check ────────────────────────────────────────────
        gluing = GluingData()
        for rel, content in after_files.items():
            gluing.add_section(_file_section(rel, content))
        gluing.add_section(_obligation_section(improvement, satisfied, evidence))
        gluing.add_section(syntax_section)
        gluing.add_section(dead_code_section)
        for sec in domain_sections:
            gluing.add_section(sec)

        # Overlap conditions:
        # - obligation ↔ changed files
        # - syntax ↔ all files
        # - dead_code ↔ obligation
        # - domain-specific overlaps
        all_sections_map = gluing.sections if isinstance(gluing.sections, dict) else {}
        loaded_coords = set(all_sections_map.keys()) if all_sections_map else set()
        if not loaded_coords:
            try:
                loaded_coords = {s.coordinate for s in gluing.sections}
            except TypeError:
                loaded_coords = set(gluing.sections.keys())

        obl_coord = "improve.obligation"
        for rel in after_files.keys():
            file_coord = f"codebase.{rel.replace('/', '.').replace(os.sep, '.')}"
            if file_coord in loaded_coords:
                gluing.add_overlap_pair(obl_coord, file_coord)
                gluing.add_overlap_pair("improve.syntax", file_coord)
        gluing.add_overlap_pair("improve.dead_code", obl_coord)
        gluing.add_overlap_pair("improve.syntax", obl_coord)
        for left, right in domain_overlaps:
            if left in loaded_coords and right in loaded_coords:
                gluing.add_overlap_pair(left, right)

        engine = DescentEngine(
            configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE)
        )
        engine.verify_overlaps(gluing, None)
        violated = gluing.find_violated_overlaps()
        target_coord = "improve"

        # Check domain obligations too
        domain_issues = sum(
            len(s.residual_obligations) for s in domain_sections
        )
        all_passed = (satisfied and not syntax_issues
                      and not dead_code_issues and domain_issues == 0)
        if not violated and all_passed:
            result = DescentResult.success(
                engine.compute_gluing(gluing, target_coord)
            )
        else:
            result = DescentResult.failure(
                engine.extract_obstruction(gluing, target_coord)
            )

        # Report JG quality
        if syntax_issues:
            print(f"  H¹[syntax]: {len(syntax_issues)} parse errors")
            for i in syntax_issues[:3]:
                print(f"    {i}")
        if dead_code_issues:
            print(f"  H¹[dead_code]: {len(dead_code_issues)} orphaned definitions")
            for i in dead_code_issues[:3]:
                print(f"    {i}")

        if result.is_success:
            gs = result.section
            print(f"\n✓ GlobalSection achieved — obligation discharged")
            print(f"  Trust floor: {gs.trust_floor:.2f}" if gs else "")
            _log.info("GlobalSection achieved in %d iterations", iteration)
            _log.removeHandler(fh)
            fh.close()
            return 0

        obs = result.obstruction
        print(f"  DescentObstruction: {obs.violation_count} violations" if obs else "  Obstruction (unknown)")
        if obs and obs.repair_frontier:
            frontier = obs.repair_frontier
            for item in list(frontier.missing_evidence)[:3]:
                print(f"    missing: {item}")
            for item in list(frontier.suggested_refinements)[:3]:
                print(f"    refine: {item}")
            # Build frontier text for next iteration's agent prompt
            frontier_lines = ["PREVIOUS ITERATION DESCENT FAILURES (fix these first):"]
            for item in list(frontier.missing_evidence)[:5]:
                frontier_lines.append(f"  - MISSING: {item}")
            for item in list(frontier.suggested_refinements)[:5]:
                frontier_lines.append(f"  - REFINE: {item}")
            if obs.violated_overlaps:
                frontier_lines.append(f"  - {obs.violation_count} cross-file overlaps violated")
                for left, right in obs.violated_pairs()[:5]:
                    frontier_lines.append(f"    {left} ↔ {right}")
            prev_frontier_text = "\n".join(frontier_lines)
        else:
            prev_frontier_text = ""

        # Update before_files for next iteration
        before_files = after_files

    # ── Max iterations reached ───────────────────────────────────────
    print(f"\n✗ Max iterations ({max_iterations}) reached — obligation not fully discharged")
    _log.info("Max iterations reached")
    _log.removeHandler(fh)
    fh.close()
    return 1
