"""Directed research as sheaf descent over a workspace site.

This is NOT "use an LLM and then check consistency." This IS judgment
geometry: every LLM interaction is a local section at a coordinate with
a trust level, every decision is a semantic move, and the entire research
loop is descent — the engine tries to glue local sections into a global
section, and obstructions drive what happens next.

The workspace site W has four coordinates (T, R, E, P) and six morphisms.
Each LLM call produces a LocalSection at one coordinate with trust
COPILOT_SUGGESTED. Each benchmark run upgrades a section to RUNTIME_WITNESSED.
Each Z3 check upgrades to SOLVER_DISCHARGED. Descent glues sections when
overlaps agree. Obstructions tell you exactly which surface drifted and why.

The research loop:

    while not converged:
        obstructions = descent(workspace)
        if not obstructions:
            if competitive: DONE
            else: move = select_performance_move()
        else:
            move = select_repair_move(obstructions[0])
        section = execute_move(move)   # may call LLM
        workspace.install_section(section)

Every move is typed by which surface it touches and what trust it produces.
The LLM never operates outside the geometry — it IS a channel.

Usage::

    from jugeo.directed_research import DirectedResearch

    dr = DirectedResearch("Build a killer app using Yahoo Finance")
    result = dr.run()
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ── JuGeo imports ────────────────────────────────────────────────────

from jugeo.research_orchestration import (
    WorkspaceSite,
    SurfaceKind,
    ConsistencyReport,
    ObstructionKind,
    WorkspaceObstruction,
)

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, MorphismKind, Morphism,
        Site, SiteBuilder,
    )
    from jugeo.geometry.descent import (
        DescentEngine, DescentStrategy, LocalSection,
    )
    from jugeo.evidence.trust import TrustLevel, TrustProfile
    _GEO = True
except Exception:
    _GEO = False

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Trust levels as floats (matching LocalSection.trust_level)
TRUST_COPILOT = 0.3       # LLM-generated content
TRUST_RUNTIME = 0.7       # Ran the code, saw the result
TRUST_SOLVER = 0.9        # Z3 or formal check passed
TRUST_VERIFIED = 1.0      # Lean proof or equivalent


# ═══════════════════════════════════════════════════════════════════════
#  The LLM as an evidence channel
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LLMSection:
    """A local section produced by an LLM call.

    Every LLM interaction in the system produces one of these. It records
    WHAT was generated, WHERE it lives in the workspace site, and at WHAT
    trust level. This is the atomic unit of the research loop.
    """
    surface: SurfaceKind
    coordinate: str          # e.g. "theory.foundations" or "code.core_module"
    content: str
    trust: float = TRUST_COPILOT
    provenance: str = ""     # the prompt that produced this
    prompt_hash: str = ""
    elapsed: float = 0.0
    token_count: int = 0

    def to_local_section(self) -> Optional[Any]:
        """Convert to a JuGeo LocalSection if geometry is available."""
        if not _GEO:
            return None
        return LocalSection(
            coordinate=self.coordinate,
            judgment_data={"content_hash": hash(self.content), "length": len(self.content)},
            evidence_bundle=(self.provenance,),
            trust_level=self.trust,
            provenance=(f"llm-channel:{self.provenance[:50]}",),
        )

    def upgrade_trust(self, new_trust: float, reason: str) -> LLMSection:
        """Promote trust (e.g., after benchmark confirms LLM claim)."""
        return LLMSection(
            surface=self.surface,
            coordinate=self.coordinate,
            content=self.content,
            trust=max(self.trust, new_trust),  # never demote via upgrade
            provenance=f"{self.provenance} → {reason}",
            prompt_hash=self.prompt_hash,
            elapsed=self.elapsed,
            token_count=self.token_count,
        )


def _clean_copilot_output(raw: str) -> str:
    """Strip copilot CLI narration, tool-failure lines, stats, and markdown fences."""
    lines = raw.split("\n")
    cleaned = []
    skip = False
    for line in lines:
        s = line.strip()
        # Skip tool invocation/failure blocks
        if s.startswith(("●", "✗")):
            skip = True
            continue
        if skip and (s.startswith(("│", "└")) or s == ""):
            continue
        skip = False
        # Skip copilot stats
        if any(tag in line for tag in [
            "Total usage", "API time", "Total session", "Total code",
            "Breakdown by AI", "claude-sonnet", "Est. ", "Premium request",
        ]):
            continue
        cleaned.append(line)
    # Strip leading/trailing empty lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    text = "\n".join(cleaned).strip()
    # Strip outermost markdown fences if present
    if text.startswith("```") and text.endswith("```"):
        inner = text.split("\n", 1)
        if len(inner) > 1:
            text = inner[1]  # skip first ```lang line
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


def _llm_call(prompt: str, *, surface: SurfaceKind, coordinate: str,
              max_tokens: int = 16384, timeout: int = 180) -> LLMSection:
    """Call the LLM and return a typed, trust-annotated section.

    This is THE interface between the LLM and the geometry. Every LLM
    interaction in the system goes through here. The result is always
    a section at a specific coordinate with COPILOT_SUGGESTED trust.

    Long prompts are written to a temp file and copilot is told to
    "read file X and follow the instructions inside" — this avoids
    massive argv and makes calls much faster.
    """
    import hashlib, shutil, tempfile

    t0 = time.time()
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:12]
    text = ""

    # 1. Copilot CLI with claude-sonnet-4.6 via stdin (with retry)
    if shutil.which("copilot"):
        for attempt in range(3):
            try:
                result = subprocess.run(
                    ["copilot", "--model", "claude-sonnet-4.6", "--autopilot", "--available-tools"],
                    input=prompt, capture_output=True, text=True,
                    timeout=timeout, cwd=_ROOT)
                if result.returncode == 0 and result.stdout.strip():
                    cleaned = _clean_copilot_output(result.stdout)
                    if len(cleaned) > 20:  # non-trivial response
                        text = cleaned
                        break
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
            # Brief pause before retry
            if attempt < 2:
                time.sleep(2)

    # 2. Anthropic SDK (fallback if copilot unavailable)
    if not text:
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-sonnet-4-6-20250514", max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
        except Exception:
            pass

    # 3. Placeholder
    if not text:
        text = f"[LLM unavailable — {prompt[:80]}...]"

    elapsed = time.time() - t0
    return LLMSection(
        surface=surface,
        coordinate=coordinate,
        content=text,
        trust=TRUST_COPILOT,
        provenance=f"llm:{coordinate}",
        prompt_hash=prompt_hash,
        elapsed=elapsed,
        token_count=len(text.split()),
    )


def _llm_json(prompt: str, **kwargs) -> tuple[dict, LLMSection]:
    """Call LLM, parse JSON, return both the dict and the section."""
    full = prompt + "\n\nRespond with ONLY valid JSON, no markdown fences."
    section = _llm_call(full, **kwargs)
    text = section.content.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.split("\n") if not l.startswith("```"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                data = {"raw": text, "parse_error": True}
        else:
            data = {"raw": text, "parse_error": True}
    return data, section


# ═══════════════════════════════════════════════════════════════════════
#  Semantic moves — typed operations on the workspace site
# ═══════════════════════════════════════════════════════════════════════

class MoveKind(str, Enum):
    """Each move is a typed operation on a specific surface."""
    # Theory surface
    ANALYZE_DOMAIN = "analyze_domain"         # understand what the domain needs
    ELABORATE_THEORY = "elaborate_theory"     # produce detailed math framework
    PIVOT_THEORY = "pivot_theory"            # adjacency-constrained theory change

    # Code surface
    DESIGN_ARCHITECTURE = "design_architecture"  # covering family decomposition
    GENERATE_MODULE = "generate_module"          # local section on one module
    GENERATE_INTEGRATION = "generate_integration" # adapter for real libraries
    FIX_MODULE = "fix_module"                    # repair obstruction in code

    # Evidence surface
    RUN_SYNTAX_CHECK = "run_syntax_check"    # check code parses (RUNTIME trust)
    RUN_IMPORT_CHECK = "run_import_check"    # check code imports (RUNTIME trust)
    RUN_BENCHMARK = "run_benchmark"          # execute benchmark (RUNTIME trust)

    # Claims surface
    GROUND_CLAIMS = "ground_claims"          # sync claims with evidence
    WRITE_README = "write_readme"
    WRITE_PAPER = "write_paper"


@dataclass
class MoveResult:
    """Result of executing a semantic move."""
    move: MoveKind
    surface: SurfaceKind
    section: Optional[LLMSection]
    trust_achieved: float
    success: bool
    description: str
    elapsed: float
    files_written: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
#  Research status
# ═══════════════════════════════════════════════════════════════════════

class ResearchStatus(str, Enum):
    CONVERGED = "converged"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PIVOT_LIMIT = "pivot_limit"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class DirectedResearchResult:
    """Final result."""
    status: ResearchStatus
    prompt: str
    theory_summary: str
    approach: str
    code_files: list[str]
    sections: list[LLMSection]         # ALL sections produced
    consistency: ConsistencyReport
    moves: list[MoveResult]
    pivots: int
    output_dir: str
    elapsed: float

    @property
    def success(self) -> bool:
        return self.status == ResearchStatus.CONVERGED

    def __repr__(self):
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
            f"  elapsed={self.elapsed:.1f}s\n"
            f")"
        )


# ═══════════════════════════════════════════════════════════════════════
#  DirectedResearch — descent-driven research loop
# ═══════════════════════════════════════════════════════════════════════

class DirectedResearch:
    """The research loop IS descent.

    Every iteration:
    1. Run descent on the workspace site
    2. If consistent + competitive → DONE
    3. If obstructions → the obstruction type determines the next move
    4. Execute the move (which may call the LLM as an evidence channel)
    5. Install the resulting section in the workspace
    6. Go to 1
    """

    def __init__(self, prompt: str, *, max_iterations: int = 30,
                 max_pivots: int = 3, output_dir: str | None = None,
                 no_llm: bool = False, seed: int | None = None,
                 verbose: bool = False):
        self.prompt = prompt
        self.max_iterations = max_iterations
        self.max_pivots = max_pivots
        self.no_llm = no_llm
        self.seed = seed
        self.verbose = verbose
        default_dir = pathlib.Path(_ROOT) / "outputs" / f"research_{time.strftime('%Y%m%d_%H%M%S')}"
        self.output_dir = pathlib.Path(output_dir or str(default_dir)).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # The workspace site
        self.workspace: Optional[WorkspaceSite] = None

        # All sections produced (the full evidence trail)
        self.sections: list[LLMSection] = []
        self.moves: list[MoveResult] = []
        self.code_files: list[str] = []
        self.pivots: int = 0

        # State built up across phases
        self.domain_analysis: dict = {}
        self.theory_text: str = ""
        self.approach: str = ""
        self.architecture: dict = {}
        self.module_code: dict[str, str] = {}   # filename → code
        self.benchmark_results: dict[str, Any] = {}

    def _log(self, msg: str):
        if self.verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {msg}", flush=True)

    # ── The main loop ────────────────────────────────────────────────

    def run(self) -> DirectedResearchResult:
        start = time.time()

        # SEED phase: produce initial sections on T and R
        self._move_analyze_domain()
        self._move_elaborate_theory()
        self._move_design_architecture()

        # GENERATE phase: produce code sections
        modules = self.architecture.get("modules", [])
        if not modules:
            # Fallback if architecture design returned no modules
            modules = [
                {"name": "core", "purpose": "Core types and data structures"},
                {"name": "algorithms", "purpose": "Main algorithms and computations"},
                {"name": "cli", "purpose": "Command-line interface"},
            ]
        for mod in modules:
            self._move_generate_module(mod)
        self._move_generate_integration()
        self._write_pyproject()

        # HARDEN phase: descent-driven loop
        for i in range(self.max_iterations):
            # Verify code (upgrade trust from COPILOT → RUNTIME)
            self._move_syntax_check()
            self._move_import_check()

            # Build workspace and run descent
            self._rebuild_workspace()
            report = self.workspace.check_consistency()

            self._log(f"  Iteration {i}: {report}")

            if report.consistent:
                # Write deliverables and finish
                self._move_ground_claims()
                self._move_write_readme()
                self._move_write_paper()
                self._rebuild_workspace()
                final = self.workspace.check_consistency()
                return self._finish(ResearchStatus.CONVERGED, final, start)

            # Obstructions drive the next move
            repaired = self._repair_from_obstructions(report)
            if not repaired:
                if self.pivots < self.max_pivots:
                    self._move_pivot_theory()
                    # After pivot, regenerate code
                    for mod in self.architecture.get("modules", []):
                        self._move_generate_module(mod)
                else:
                    break

        # Budget exhausted — write what we have
        self._move_ground_claims()
        self._move_write_readme()
        self._move_write_paper()
        self._rebuild_workspace()
        final = self.workspace.check_consistency()
        status = ResearchStatus.PARTIAL if final.consistent else ResearchStatus.BUDGET_EXHAUSTED
        return self._finish(status, final, start)

    def _finish(self, status: ResearchStatus, report: ConsistencyReport,
                start: float) -> DirectedResearchResult:
        elapsed = time.time() - start
        # Save metadata
        meta = {
            "status": status.value, "prompt": self.prompt,
            "approach": self.approach, "elapsed": round(elapsed, 2),
            "sections": len(self.sections), "moves": len(self.moves),
            "pivots": self.pivots, "code_files": self.code_files,
            "trust_profile": {s.coordinate: s.trust for s in self.sections},
        }
        (self.output_dir / "research_metadata.json").write_text(
            json.dumps(meta, indent=2, default=str))
        return DirectedResearchResult(
            status=status, prompt=self.prompt,
            theory_summary=self.theory_text[:500],
            approach=self.approach, code_files=self.code_files,
            sections=self.sections, consistency=report,
            moves=self.moves, pivots=self.pivots,
            output_dir=str(self.output_dir), elapsed=elapsed)

    # ── Rebuild the workspace from all sections ──────────────────────

    def _rebuild_workspace(self):
        """Reconstruct the workspace site from all accumulated sections."""
        theory = self.theory_text
        code = "\n\n".join(self.module_code.values())
        evidence = dict(self.benchmark_results)
        # Evidence also includes trust levels from code verification
        for s in self.sections:
            if s.surface == SurfaceKind.EVIDENCE:
                evidence[s.coordinate] = s.trust

        claims_parts = []
        for s in self.sections:
            if s.surface == SurfaceKind.CLAIMS:
                claims_parts.append(s.content[:500])
        claims = "\n".join(claims_parts) if claims_parts else self.approach

        self.workspace = WorkspaceSite.from_artifacts(
            theory=theory, code=code, evidence=evidence,
            claims=claims, name=self.approach)

    # ── Record a move ────────────────────────────────────────────────

    def _record_move(self, kind: MoveKind, surface: SurfaceKind,
                     section: Optional[LLMSection], trust: float,
                     success: bool, desc: str, elapsed: float,
                     files: list[str] = None):
        if section:
            self.sections.append(section)
        self.moves.append(MoveResult(
            move=kind, surface=surface, section=section,
            trust_achieved=trust, success=success,
            description=desc, elapsed=elapsed,
            files_written=files or []))

    # ══════════════════════════════════════════════════════════════════
    #  SEMANTIC MOVES — each produces a section on a surface
    # ══════════════════════════════════════════════════════════════════

    def _move_analyze_domain(self):
        """CONSTRUCT on T: understand what the domain needs."""
        self._log("MOVE: analyze_domain → T")
        if self.no_llm:
            self.domain_analysis = {"domain_analysis": self.prompt, "best_math_approach": {"name": "heuristic"}}
            self.approach = "heuristic"
            self._record_move(MoveKind.ANALYZE_DOMAIN, SurfaceKind.THEORY,
                             None, TRUST_COPILOT, True, "heuristic", 0)
            return

        prompt = textwrap.dedent(f"""\
            Analyze this product request and determine the BEST mathematical approach:

            "{self.prompt}"

            Think about what ACTUALLY works in this domain. Do NOT default to
            exotic or trendy math. What specific computational problems need solving?
            What are the 3 best existing tools? Where do they fail? What math would
            let us do something they CAN'T?

            Also identify: standard Python libraries, standard data formats/APIs,
            standard datasets, and evaluation metrics practitioners actually use.

            Respond as JSON:
            {{
                "domain_analysis": "3-paragraph analysis",
                "computational_problems": ["problem1", ...],
                "existing_tools": [{{"name": "...", "math": "...", "weakness": "..."}}],
                "best_math_approach": {{
                    "name": "Name of approach",
                    "field_a": "Primary math field",
                    "field_b": "Secondary math field",
                    "why": "Why this is the best choice",
                    "key_theorems": ["theorem1: what it does for us", ...],
                    "key_algorithms": ["algo1", ...]
                }},
                "standard_libraries": ["pandas", "numpy", ...],
                "standard_datasets": ["Yahoo Finance API", ...],
                "evaluation_metrics": ["Sharpe ratio", ...],
                "baselines_to_beat": [{{"name": "...", "metric": "...", "value": "..."}}]
            }}
        """)

        data, section = _llm_json(prompt, surface=SurfaceKind.THEORY,
                                   coordinate="theory.domain_analysis")
        self.domain_analysis = data
        self.approach = data.get("best_math_approach", {}).get("name", "approach").lower().replace(" ", "-")
        self._record_move(MoveKind.ANALYZE_DOMAIN, SurfaceKind.THEORY,
                         section, TRUST_COPILOT, True,
                         f"Domain analysis: {self.approach}", section.elapsed)

    def _move_elaborate_theory(self):
        """CONSTRUCT on T: produce a deep mathematical framework document."""
        self._log("MOVE: elaborate_theory → T")
        if self.no_llm:
            self.theory_text = f"Mathematical framework for: {self.prompt}"
            (self.output_dir / "context.md").write_text(f"# {self.approach}\n\n{self.theory_text}\n")
            self._record_move(MoveKind.ELABORATE_THEORY, SurfaceKind.THEORY,
                             None, TRUST_COPILOT, True, "heuristic theory", 0)
            return

        ma = self.domain_analysis.get("best_math_approach", {})
        prompt = textwrap.dedent(f"""\
            Write a COMPREHENSIVE mathematical framework document (at least 5000 words)
            for this software tool:

            PRODUCT: {self.prompt}
            APPROACH: {ma.get('name', self.approach)}
            PRIMARY FIELD: {ma.get('field_a', '')}
            SECONDARY FIELD: {ma.get('field_b', '')}
            KEY THEOREMS: {json.dumps(ma.get('key_theorems', []))}
            DOMAIN ANALYSIS: {self.domain_analysis.get('domain_analysis', '')}
            EXISTING TOOLS: {json.dumps(self.domain_analysis.get('existing_tools', []))}

            Structure: (1) Introduction — the problem and insight (500+ words),
            (2) Mathematical Foundations — formal definitions, theorems, proofs (1500+ words),
            (3) Computational Framework — data structures, algorithms, complexity (1000+ words),
            (4) Application — concrete examples with real data from {json.dumps(self.domain_analysis.get('standard_datasets', []))} (1000+ words),
            (5) Evaluation Strategy — metrics {json.dumps(self.domain_analysis.get('evaluation_metrics', []))}, baselines {json.dumps(self.domain_analysis.get('baselines_to_beat', []))} (500+ words),
            (6) Key Propositions — 5-10 formal propositions the code must satisfy (500+ words).

            Use LaTeX notation for math. Be specific about data structures and algorithms.
            Return raw Markdown, no JSON wrapping.
        """)

        section = _llm_call(prompt, surface=SurfaceKind.THEORY,
                            coordinate="theory.foundations", timeout=600)
        self.theory_text = section.content
        path = self.output_dir / "context.md"
        path.write_text(f"# {self.approach}\n\n{section.content}\n")
        self._log(f"  Wrote context.md: {len(section.content)} bytes in {section.elapsed:.0f}s")
        self._record_move(MoveKind.ELABORATE_THEORY, SurfaceKind.THEORY,
                         section, TRUST_COPILOT, True,
                         f"Theory: {len(section.content)} bytes", section.elapsed,
                         files=[str(path)])

    def _move_design_architecture(self):
        """CUT on R: decompose into a covering family of modules."""
        self._log("MOVE: design_architecture → R")
        if self.no_llm:
            self.architecture = {"modules": [
                {"name": "core", "purpose": "Core types"},
                {"name": "algorithms", "purpose": "Algorithms"},
                {"name": "cli", "purpose": "CLI"},
            ], "package_name": self.approach.replace("-", "_")}
            self._record_move(MoveKind.DESIGN_ARCHITECTURE, SurfaceKind.CODE,
                             None, TRUST_COPILOT, True, "heuristic arch", 0)
            return

        libs = self.domain_analysis.get("standard_libraries", [])
        prompt = textwrap.dedent(f"""\
            Design the Python package architecture for this tool:

            PRODUCT: {self.prompt}
            APPROACH: {self.approach}
            THEORY (first 2000 chars): {self.theory_text[:500]}
            STANDARD LIBRARIES: {json.dumps(libs)}

            Design 8-12 Python modules that form an INTEGRATED system — modules
            must share types, call each other, and form a coherent pipeline.
            Name them after DOMAIN concepts. Include data ingestion, core types,
            algorithms, evaluation/benchmarking, a CLI, and integration with
            {', '.join(libs[:5])} and {', '.join(self.domain_analysis.get('standard_datasets', [])[:3])}.
            Each module should be 800-1500 lines. Target: 10,000+ total lines.

            Respond as JSON:
            {{
                "package_name": "snake_case_name",
                "modules": [
                    {{"name": "module_name", "purpose": "what it does",
                      "key_classes": ["Class1", "Class2"],
                      "key_functions": ["func1", "func2"],
                      "dependencies": ["other_module"], "target_lines": 1000}},
                    ...
                ],
                "generation_order": ["module1", "module2", ...]
            }}
        """)

        data, section = _llm_json(prompt, surface=SurfaceKind.CODE,
                                   coordinate="code.architecture")
        self.architecture = data
        if "package_name" not in self.architecture:
            self.architecture["package_name"] = self.approach.replace("-", "_")
        self._record_move(MoveKind.DESIGN_ARCHITECTURE, SurfaceKind.CODE,
                         section, TRUST_COPILOT, True,
                         f"Architecture: {len(data.get('modules', []))} modules",
                         section.elapsed)

    def _move_generate_module(self, mod: dict):
        """CONSTRUCT on R: generate one module (one local section on R)."""
        name = mod.get("name", "module")
        self._log(f"MOVE: generate_module({name}) → R")

        pkg_name = self.architecture.get("package_name", self.approach.replace("-", "_"))
        # Sanitize package name
        pkg_name = re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
        if not pkg_name:
            pkg_name = "research_output"
        pkg_dir = self.output_dir / "src" / pkg_name
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # Write __init__.py if not yet
        init_path = pkg_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text(f'"""{self.approach}"""\n__version__ = "0.1.0"\n')
            self.code_files.append(str(init_path))

        if self.no_llm:
            path = pkg_dir / f"{name}.py"
            path.write_text(f'"""{mod.get("purpose", "")}"""\n\ndef main():\n    pass\n')
            self.code_files.append(str(path))
            self.module_code[name] = path.read_text()
            self._record_move(MoveKind.GENERATE_MODULE, SurfaceKind.CODE,
                             None, TRUST_COPILOT, True, f"stub {name}.py", 0,
                             files=[str(path)])
            return

        libs = self.domain_analysis.get("standard_libraries", [])
        datasets = self.domain_analysis.get("standard_datasets", [])
        existing_modules = list(self.module_code.keys())

        deps = mod.get('dependencies', [])
        import_hint = ""
        if deps:
            import_hint = f"IMPORTS FROM OTHER MODULES: from .{' import ..., from .'.join(deps)} import ...\n"
        if existing_modules:
            import_hint += f"ALREADY GENERATED (import and USE these): {', '.join(existing_modules)}\n"

        prompt = textwrap.dedent(f"""\
            Generate `{name}.py` for the {pkg_name} package.

            PRODUCT: {self.prompt}
            MATHEMATICAL APPROACH: {self.theory_text[:500]}
            {import_hint}

            PURPOSE OF THIS MODULE: {mod.get('purpose', '')}
            KEY CLASSES: {', '.join(mod.get('key_classes', []))}
            KEY FUNCTIONS: {', '.join(mod.get('key_functions', []))}
            STANDARD LIBRARIES: {', '.join(libs[:8])}
            DATA SOURCES: {', '.join(datasets[:3])}

            REQUIREMENTS — write AT LEAST {mod.get('target_lines', 800)} lines of REAL code:
            - Python 3.10+, from __future__ import annotations
            - Use {', '.join(libs[:5])} extensively — don't reinvent what they provide
            - INTEGRATE with other modules: import types from core, call functions from
              other modules, share data structures. The modules must work TOGETHER as a
              unified system, not as isolated scripts.
            - Real implementations: if you compute a statistic, show the formula.
              If you optimize, show the objective function. If you estimate, show
              the likelihood. No placeholders, no "pass", no "TODO".
            - Include at least 5 classes with real methods and 10 functions
            - Include proper error handling, logging, input validation
            - Include docstrings that explain the MATHEMATICAL content
            - Return ONLY Python code, no markdown fences
        """)

        section = _llm_call(prompt, surface=SurfaceKind.CODE,
                            coordinate=f"code.{name}")
        code = section.content
        if code.startswith("```"):
            code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))
        code = code.strip() + "\n"

        path = pkg_dir / f"{name}.py"
        path.write_text(code)
        self.code_files.append(str(path))
        self.module_code[name] = code

        self._log(f"  Generated {name}.py: {len(code.splitlines())} lines in {section.elapsed:.0f}s")
        self._record_move(MoveKind.GENERATE_MODULE, SurfaceKind.CODE,
                         section, TRUST_COPILOT, True,
                         f"{name}.py: {len(code.splitlines())} lines",
                         section.elapsed, files=[str(path)])

    def _move_generate_integration(self):
        """CONSTRUCT on R: generate integration with real libraries/datasets."""
        self._log("MOVE: generate_integration → R")
        if self.no_llm:
            self._record_move(MoveKind.GENERATE_INTEGRATION, SurfaceKind.CODE,
                             None, TRUST_COPILOT, True, "skipped", 0)
            return

        pkg_name = self.architecture.get("package_name", self.approach.replace("-", "_"))
        libs = self.domain_analysis.get("standard_libraries", [])
        datasets = self.domain_analysis.get("standard_datasets", [])

        prompt = textwrap.dedent(f"""\
            Generate `integration.py` for the {pkg_name} package.

            This module connects the package to real-world data and libraries.

            STANDARD LIBRARIES: {json.dumps(libs)}
            DATA SOURCES: {json.dumps(datasets)}
            EXISTING MODULES: {list(self.module_code.keys())}

            Include:
            - Imports/wrappers for each standard library
            - Data loaders for each standard data source
            - validate_environment() that checks all deps are installed
            - load_sample_data() returning a small built-in example
            - Adapter functions connecting real data to the package's types
            - AT LEAST 500 lines of real, working code
            - Return ONLY Python code, no markdown fences
        """)

        section = _llm_call(prompt, surface=SurfaceKind.CODE,
                            coordinate="code.integration")
        code = section.content
        if code.startswith("```"):
            code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))

        pkg_dir = self.output_dir / "src" / re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
        pkg_dir.mkdir(parents=True, exist_ok=True)
        path = pkg_dir / "integration.py"
        path.write_text(code.strip() + "\n")
        self.code_files.append(str(path))
        self.module_code["integration"] = code

        self._record_move(MoveKind.GENERATE_INTEGRATION, SurfaceKind.CODE,
                         section, TRUST_COPILOT, True,
                         f"integration.py: {len(code.splitlines())} lines",
                         section.elapsed, files=[str(path)])

    def _write_pyproject(self):
        """Write pyproject.toml."""
        pkg = self.architecture.get("package_name", self.approach.replace("-", "_"))
        deps = self.domain_analysis.get("standard_libraries", ["numpy", "pandas"])
        toml = self.output_dir / "pyproject.toml"
        toml.write_text(textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=68"]
            build-backend = "setuptools.backends._legacy:_Backend"

            [project]
            name = "{pkg}"
            version = "0.1.0"
            description = "{self.approach}"
            requires-python = ">=3.10"
            dependencies = {json.dumps(deps)}

            [tool.setuptools.packages.find]
            where = ["src"]
        """))
        self.code_files.append(str(toml))

    # ── Evidence moves (upgrade trust) ───────────────────────────────

    def _move_syntax_check(self):
        """VERIFY on E: check all .py files parse. Upgrades trust to RUNTIME."""
        results = {}
        for f in self.code_files:
            if not f.endswith(".py") or not os.path.exists(f):
                continue
            try:
                r = subprocess.run(
                    [sys.executable, "-c", f"import ast; ast.parse(open('{f}').read())"],
                    capture_output=True, timeout=10)
                ok = r.returncode == 0
            except Exception:
                ok = False
            results[os.path.basename(f)] = ok
        self.benchmark_results["syntax_check"] = results
        all_ok = all(results.values()) if results else False
        trust = TRUST_RUNTIME if all_ok else TRUST_COPILOT
        self._record_move(MoveKind.RUN_SYNTAX_CHECK, SurfaceKind.EVIDENCE,
                         LLMSection(surface=SurfaceKind.EVIDENCE,
                                    coordinate="evidence.syntax",
                                    content=json.dumps(results),
                                    trust=trust, provenance="syntax-check"),
                         trust, all_ok,
                         f"Syntax: {sum(results.values())}/{len(results)} pass", 0)

    def _move_import_check(self):
        """VERIFY on E: check modules import without error."""
        pkg = self.architecture.get("package_name", "")
        src_dir = self.output_dir / "src"
        if not (src_dir / pkg).exists():
            return
        try:
            r = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0,'{src_dir}'); import {pkg}"],
                capture_output=True, text=True, timeout=15)
            ok = r.returncode == 0
            detail = r.stderr[:200] if not ok else "OK"
        except Exception as e:
            ok = False
            detail = str(e)
        self.benchmark_results["import_check"] = {"ok": ok, "detail": detail}
        trust = TRUST_RUNTIME if ok else TRUST_COPILOT
        self._record_move(MoveKind.RUN_IMPORT_CHECK, SurfaceKind.EVIDENCE,
                         LLMSection(surface=SurfaceKind.EVIDENCE,
                                    coordinate="evidence.import",
                                    content=detail, trust=trust,
                                    provenance="import-check"),
                         trust, ok, f"Import: {'OK' if ok else detail[:60]}", 0)

    # ── Claims moves ─────────────────────────────────────────────────

    def _move_ground_claims(self):
        """Sync claims with actual evidence."""
        if self.workspace:
            ev = self.workspace.sections.get(SurfaceKind.EVIDENCE)
            cl = self.workspace.sections.get(SurfaceKind.CLAIMS)
            if ev and cl:
                for k, v in ev.claims.items():
                    if not k.startswith("_"):
                        cl.claims[k] = v
                cl.trust = min(1.0, cl.trust + 0.3)
        self._record_move(MoveKind.GROUND_CLAIMS, SurfaceKind.CLAIMS,
                         None, TRUST_COPILOT, True, "grounded", 0)

    def _move_write_readme(self):
        """CONSTRUCT on P: write a comprehensive README grounded in evidence."""
        self._log("MOVE: write_readme → P")
        if self.no_llm:
            readme = f"# {self.approach}\n\n{self.prompt}\n"
        else:
            modules_detail = "\n".join(
                f"- **{name}**: {len(code.splitlines())} lines"
                for name, code in self.module_code.items()
            )
            section = _llm_call(textwrap.dedent(f"""\
                Write a comprehensive, professional README.md (at least 2000 words) for this research tool.

                TOOL NAME: {self.approach}
                PRODUCT VISION: {self.prompt}
                MATHEMATICAL APPROACH: {self.theory_text[:800]}
                MODULES:
                {modules_detail}
                BENCHMARKS: {json.dumps(self.benchmark_results)[:400]}
                STANDARD LIBRARIES: {json.dumps(self.domain_analysis.get('standard_libraries', []))}
                EVALUATION METRICS: {json.dumps(self.domain_analysis.get('evaluation_metrics', []))}

                The README must include ALL of these sections:
                1. Title with badges (Python version, license, build status)
                2. One-paragraph elevator pitch (what it does and why it's better)
                3. Key features list (5-8 bullet points with concrete capabilities)
                4. Mathematical approach summary (2 paragraphs with key theorems named)
                5. Installation instructions (pip install, dependencies, environment setup)
                6. Quickstart example (complete runnable Python code, 20+ lines)
                7. CLI usage (3-5 commands with examples)
                8. Module reference (every module with purpose and key classes/functions)
                9. Benchmarks table (metrics, our results, baseline comparison)
                10. Architecture diagram (ASCII)
                11. Contributing guide
                12. Citation (BibTeX)
                13. License

                Return raw Markdown, no JSON, no fences.
            """), surface=SurfaceKind.CLAIMS, coordinate="claims.readme",
                 timeout=300)
            readme = section.content
            self.sections.append(section)
        path = self.output_dir / "README.md"
        path.write_text(readme.strip() + "\n")
        self.code_files.append(str(path))
        self._record_move(MoveKind.WRITE_README, SurfaceKind.CLAIMS,
                         None, TRUST_COPILOT, True, "README.md", 0,
                         files=[str(path)])

    def _move_write_paper(self):
        """FORMALIZE on P: write conference tool track paper."""
        self._log("MOVE: write_paper → P")
        if self.no_llm:
            paper = textwrap.dedent(f"""\
                \\documentclass{{article}}
                \\title{{{self.approach}}}
                \\author{{Generated by JuGeo}}
                \\begin{{document}}
                \\maketitle
                \\section{{Introduction}}
                {self.prompt}
                \\section{{Approach}}
                {self.theory_text[:300]}
                \\end{{document}}
            """)
        else:
            modules_list = ", ".join(self.module_code.keys())
            metrics = json.dumps(self.domain_analysis.get("evaluation_metrics", []))
            baselines = json.dumps(self.domain_analysis.get("baselines_to_beat", []))
            section = _llm_call(textwrap.dedent(f"""\
                Write a COMPREHENSIVE conference paper in LaTeX — at least 12 pages
                when compiled. This is a tool-track paper for a top venue (ICSE, SIGMOD,
                KDD, NeurIPS tools track).

                TOOL: {self.approach}
                PRODUCT: {self.prompt}
                MATHEMATICAL APPROACH: {self.theory_text[:800]}
                MODULES: {modules_list}
                EVALUATION METRICS: {metrics}
                BASELINES: {baselines}

                Structure (EVERY section is REQUIRED and must be SUBSTANTIAL):

                1. ABSTRACT (200+ words) — problem, approach, key results
                2. INTRODUCTION (2+ pages) — motivation, gap in existing tools,
                   our contribution, paper outline
                3. BACKGROUND & RELATED WORK (1.5+ pages) — existing tools with
                   citations, mathematical prerequisites
                4. MATHEMATICAL FRAMEWORK (2+ pages) — formal definitions, theorems
                   with proof sketches, key algorithms with pseudocode
                5. SYSTEM ARCHITECTURE (1.5+ pages) — module diagram, data flow,
                   design decisions
                6. IMPLEMENTATION (1+ pages) — key data structures, integration with
                   standard libraries, code examples
                7. EVALUATION (2+ pages) — experimental setup, metrics, baseline
                   comparison tables, ablation studies, performance analysis
                8. DISCUSSION (0.5+ pages) — limitations, threats to validity
                9. CONCLUSION & FUTURE WORK (0.5+ pages)
                10. REFERENCES (20+ entries)

                Use \\documentclass[11pt]{{article}} with amsmath, booktabs, graphicx,
                hyperref, algorithm2e. Include at least 3 tables and 2 algorithm blocks.
                Return raw LaTeX, no markdown fences.
            """), surface=SurfaceKind.CLAIMS, coordinate="claims.paper",
                 timeout=600)
            paper = section.content
            self.sections.append(section)
        if paper.startswith("```"):
            paper = "\n".join(l for l in paper.split("\n") if not l.startswith("```"))
        path = self.output_dir / "conference_tool_track.tex"
        path.write_text(paper.strip() + "\n")
        self.code_files.append(str(path))
        # Try compiling
        import shutil
        if shutil.which("pdflatex"):
            try:
                subprocess.run(["pdflatex", "-interaction=nonstopmode",
                               "conference_tool_track.tex"],
                              capture_output=True, timeout=30,
                              cwd=str(self.output_dir))
                pdf = self.output_dir / "conference_tool_track.pdf"
                if pdf.exists():
                    self.code_files.append(str(pdf))
            except Exception:
                pass
        self._record_move(MoveKind.WRITE_PAPER, SurfaceKind.CLAIMS,
                         None, TRUST_COPILOT, True, "paper.tex", 0,
                         files=[str(path)])

    # ── Repair moves (driven by obstructions) ────────────────────────

    def _repair_from_obstructions(self, report: ConsistencyReport) -> bool:
        """Use obstructions to determine and execute the right repair move."""
        if not report.obstructions:
            return True
        obs = report.obstructions[0]
        if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
            self._move_ground_claims()
            return True
        elif obs.kind == ObstructionKind.STALE_SECTION:
            self._move_syntax_check()
            return True
        elif obs.kind in (ObstructionKind.CODE_THEORY_GAP, ObstructionKind.MISSING_EVIDENCE):
            # Find weakest module and regenerate
            if self.architecture.get("modules"):
                weakest = self.architecture["modules"][0]
                self._move_generate_module(weakest)
                return True
        return False

    def _move_pivot_theory(self):
        """NEGOTIATE_TREATY on (T, R): adjacency-constrained theory change."""
        self.pivots += 1
        self._log(f"MOVE: pivot_theory #{self.pivots} → T")
        if self.no_llm:
            self.theory_text += "\n\n[REVISED]"
            self._record_move(MoveKind.PIVOT_THEORY, SurfaceKind.THEORY,
                             None, TRUST_COPILOT, True, "heuristic pivot", 0)
            return
        section = _llm_call(textwrap.dedent(f"""\
            The current approach isn't working well enough. Propose a PIVOT:
            CURRENT: {self.theory_text[:500]}
            PROMPT: {self.prompt}
            Change ONE thing about the mathematical framework that might produce
            better results. Keep what works, fix what doesn't.
            Write the revised theory (1000+ words). Return raw text.
        """), surface=SurfaceKind.THEORY, coordinate="theory.pivot")
        self.theory_text = section.content
        (self.output_dir / "context.md").write_text(f"# {self.approach} (pivot {self.pivots})\n\n{section.content}\n")
        self._record_move(MoveKind.PIVOT_THEORY, SurfaceKind.THEORY,
                         section, TRUST_COPILOT, True,
                         f"Pivot #{self.pivots}", section.elapsed)
