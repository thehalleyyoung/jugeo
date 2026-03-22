"""jugeo orchestrate — sheaf-theoretic software synthesis from natural language ideas.

Decomposes an idea into a Grothendieck site of modules, generates behavioral
tests first, then iteratively builds and refines code until tests pass.

Phases
------
1. Elaborate  — Decompose idea into site coordinates / morphisms / covers.
2. Test-first — Generate shell-level behavioral tests.
3. Scaffold   — pyproject.toml, CLI entry point, directory tree.
4. Implement  — LLM-generate code per module coordinate.
5. Verify     — Run behavioral tests, collect failures.
6. Refine     — Descent-guided repair loop until convergence.
7. Report     — Final test results and output path.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo geometry imports
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.site import (
        Coordinate,
        CoordinateKind,
        Morphism,
        MorphismKind,
        SiteBuilder,
    )
    from jugeo.geometry.covers import CoverBuilder, CoverMember, score_cover
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentConfiguration,
        LocalSection,
    )
    from jugeo.judgments.judgment_terms import (
        JudgmentBuilder,
        Carrier,
        Proposition,
        PropositionKind,
    )
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra

    _GEOMETRY_AVAILABLE = True
except ImportError:
    _GEOMETRY_AVAILABLE = False


# ===================================================================== data
# =====================================================================

@dataclass
class ModuleSpec:
    """Specification for a single source module."""

    name: str
    purpose: str
    dependencies: list[str] = field(default_factory=list)
    public_api: list[str] = field(default_factory=list)


@dataclass
class CLICommandSpec:
    """Specification for a single CLI sub-command."""

    name: str
    description: str
    flags: list[dict[str, str]] = field(default_factory=list)
    example: str = ""


@dataclass
class BehavioralTest:
    """One end-to-end behavioural test case."""

    name: str
    command: str
    expected_exit_code: int = 0
    expected_stdout_contains: str | None = None
    expected_file_exists: str | None = None
    setup_commands: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SoftwarePlan:
    """A sheaf-theoretic decomposition of a software idea."""

    idea: str
    name: str
    description: str
    modules: list[ModuleSpec]
    cli_commands: list[CLICommandSpec]
    behavioral_tests: list[BehavioralTest]
    # Sheaf artefacts (populated when geometry is available)
    site_coordinates: list[Any] = field(default_factory=list)
    site_morphisms: list[Any] = field(default_factory=list)
    site: Any = None
    cover_score: float = 0.0


@dataclass
class RefinementResult:
    """Result of one refinement iteration."""

    iteration: int
    tests_total: int
    tests_passed: int
    tests_failed: int
    modules_regenerated: list[str] = field(default_factory=list)
    duration: float = 0.0


@dataclass
class TestRunResult:
    """Parsed output of the behavioural test script."""

    passed: int = 0
    failed: int = 0
    total: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    raw_output: str = ""


# ================================================================ helpers
# ================================================================

def _clean_copilot_output(text: str) -> str:
    """Strip Copilot CLI tool narration from stdout, keeping only content."""
    lines = text.split("\n")
    cleaned: list[str] = []
    skip_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("●") or stripped.startswith("✗"):
            skip_block = True
            continue
        if skip_block and (stripped.startswith("│") or stripped.startswith("└")):
            continue
        if skip_block and stripped == "":
            continue
        skip_block = False
        cleaned.append(line)
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    return "\n".join(cleaned).strip()


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from potentially messy LLM output."""
    # Try raw parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # Greedy brace match
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("Could not extract JSON from LLM response")


def _safe_name(idea: str) -> str:
    """Derive a filesystem-safe tool name from an idea string."""
    words = re.sub(r"[^a-z0-9 ]", "", idea.lower()).split()
    # Pick at most the first 3 meaningful words
    stop = {"a", "an", "the", "that", "which", "with", "for", "and", "or"}
    parts = [w for w in words if w not in stop][:3]
    return "-".join(parts) or "tool"


# ================================================================ LLM
# ================================================================

def _call_llm(prompt: str, model: str = "claude-sonnet-4.6",
              max_tokens: int = 4096, verbose: bool = False) -> str:
    """Call LLM via Copilot CLI → Anthropic → OpenAI fallback chain."""

    # 1. Copilot CLI
    if shutil.which("copilot"):
        try:
            tmpdir = tempfile.mkdtemp(prefix="jugeo_orch_")
            try:
                result = subprocess.run(
                    ["copilot", "-p", prompt, "--model", "gpt-5.4",
                     "--available-tools", ""],
                    capture_output=True, text=True, timeout=300,
                    cwd=tmpdir,
                )
            finally:
                try:
                    os.rmdir(tmpdir)
                except OSError:
                    pass
            if result.returncode == 0 and result.stdout.strip():
                return _clean_copilot_output(result.stdout)
            if verbose:
                _log.debug("Copilot CLI rc=%d: %s",
                           result.returncode, result.stderr[:200])
        except Exception as exc:
            if verbose:
                _log.debug("Copilot CLI error: %s", exc)

    # 2. Anthropic
    try:
        import anthropic  # type: ignore[import-untyped]
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except (ImportError, Exception):
        pass

    # 3. OpenAI
    try:
        import openai  # type: ignore[import-untyped]
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens, temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
            timeout=120,
        )
        return resp.choices[0].message.content or ""
    except (ImportError, Exception):
        pass

    raise RuntimeError("No LLM provider available (copilot / anthropic / openai)")


# ============================================================== template
# ==============================================================

def _template_plan() -> dict:
    """Fallback plan for --no-llm mode: a *data-transform* CLI tool."""
    return {
        "name": "datatx",
        "description": (
            "A versatile CLI data-transformation toolkit that converts between "
            "CSV, JSON, JSONL, and XML formats.  Supports filtering rows by "
            "expression, sorting by column, aggregation (count / sum / mean / "
            "min / max), column selection / renaming, and basic statistics.  "
            "Includes a 'demo' subcommand that works without input files."
        ),
        "modules": [
            {
                "name": "cli.py",
                "purpose": "Argument parsing and subcommand dispatch via argparse.",
                "dependencies": [],
                "public_api": ["build_parser", "main"],
            },
            {
                "name": "reader.py",
                "purpose": "Unified reader: detect format by extension and load into list[dict].",
                "dependencies": [],
                "public_api": ["read_file", "detect_format", "Format"],
            },
            {
                "name": "writer.py",
                "purpose": "Write list[dict] to CSV / JSON / JSONL / XML.",
                "dependencies": [],
                "public_api": ["write_file", "format_table"],
            },
            {
                "name": "convert.py",
                "purpose": "Orchestrate read → optional transform → write for format conversion.",
                "dependencies": ["reader", "writer"],
                "public_api": ["convert"],
            },
            {
                "name": "filter_engine.py",
                "purpose": "Row filtering with a safe expression evaluator (no eval).",
                "dependencies": [],
                "public_api": ["apply_filter", "parse_expression"],
            },
            {
                "name": "sort_engine.py",
                "purpose": "Multi-column sort with type awareness (numeric vs lexicographic).",
                "dependencies": [],
                "public_api": ["apply_sort"],
            },
            {
                "name": "aggregate.py",
                "purpose": "Group-by aggregation: count, sum, mean, min, max.",
                "dependencies": [],
                "public_api": ["aggregate", "AggFunc"],
            },
            {
                "name": "stats.py",
                "purpose": "Quick dataset statistics: row count, column types, null counts, basic numeric stats.",
                "dependencies": ["reader"],
                "public_api": ["compute_stats", "print_stats"],
            },
        ],
        "cli_commands": [
            {
                "name": "convert",
                "description": "Convert data between CSV, JSON, JSONL, and XML.",
                "flags": [
                    {"name": "input", "type": "str", "help": "Input file path."},
                    {"name": "--output", "type": "str", "help": "Output file path (format inferred from extension)."},
                    {"name": "--format", "type": "str", "help": "Explicit output format (csv|json|jsonl|xml)."},
                ],
                "example": "datatx convert data.csv --output data.json",
            },
            {
                "name": "filter",
                "description": "Filter rows by an expression.",
                "flags": [
                    {"name": "input", "type": "str", "help": "Input file."},
                    {"name": "--where", "type": "str", "help": "Filter expression e.g. 'age > 30'."},
                    {"name": "--output", "type": "str", "help": "Output file."},
                ],
                "example": "datatx filter data.csv --where 'age > 30' --output filtered.csv",
            },
            {
                "name": "sort",
                "description": "Sort rows by one or more columns.",
                "flags": [
                    {"name": "input", "type": "str", "help": "Input file."},
                    {"name": "--by", "type": "str", "help": "Comma-separated column names."},
                    {"name": "--desc", "type": "bool", "help": "Sort descending."},
                    {"name": "--output", "type": "str", "help": "Output file."},
                ],
                "example": "datatx sort data.csv --by name --output sorted.csv",
            },
            {
                "name": "aggregate",
                "description": "Aggregate rows with group-by.",
                "flags": [
                    {"name": "input", "type": "str", "help": "Input file."},
                    {"name": "--group-by", "type": "str", "help": "Grouping column."},
                    {"name": "--func", "type": "str", "help": "Aggregation: count|sum|mean|min|max."},
                    {"name": "--column", "type": "str", "help": "Column to aggregate."},
                    {"name": "--output", "type": "str", "help": "Output file."},
                ],
                "example": "datatx aggregate sales.csv --group-by region --func sum --column revenue",
            },
            {
                "name": "stats",
                "description": "Show dataset statistics.",
                "flags": [
                    {"name": "input", "type": "str", "help": "Input file."},
                ],
                "example": "datatx stats data.csv",
            },
            {
                "name": "demo",
                "description": "Run a built-in demo (no input files needed).",
                "flags": [],
                "example": "datatx demo",
            },
        ],
        "behavioral_tests": [
            {
                "name": "demo_runs",
                "setup_commands": [],
                "command": "$TOOL demo",
                "expected_exit_code": 0,
                "expected_stdout_contains": "Demo",
                "expected_file_exists": None,
                "description": "The demo subcommand runs without error.",
            },
            {
                "name": "help_flag",
                "setup_commands": [],
                "command": "$TOOL --help",
                "expected_exit_code": 0,
                "expected_stdout_contains": "usage",
                "expected_file_exists": None,
                "description": "--help prints usage information.",
            },
            {
                "name": "convert_csv_to_json",
                "setup_commands": [
                    "printf 'name,age\\nAlice,30\\nBob,25\\n' > /tmp/_dtx_test.csv",
                ],
                "command": "$TOOL convert /tmp/_dtx_test.csv --output /tmp/_dtx_out.json",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_out.json",
                "description": "CSV → JSON conversion creates output file.",
            },
            {
                "name": "convert_json_to_csv",
                "setup_commands": [
                    'printf \'[{"x":1,"y":2},{"x":3,"y":4}]\\n\' > /tmp/_dtx_test.json',
                ],
                "command": "$TOOL convert /tmp/_dtx_test.json --output /tmp/_dtx_out.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_out.csv",
                "description": "JSON → CSV conversion creates output file.",
            },
            {
                "name": "convert_csv_to_jsonl",
                "setup_commands": [
                    "printf 'a,b\\n1,2\\n3,4\\n' > /tmp/_dtx_test2.csv",
                ],
                "command": "$TOOL convert /tmp/_dtx_test2.csv --output /tmp/_dtx_out.jsonl",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_out.jsonl",
                "description": "CSV → JSONL conversion creates output file.",
            },
            {
                "name": "filter_rows",
                "setup_commands": [
                    "printf 'name,age\\nAlice,30\\nBob,25\\nCarol,35\\n' > /tmp/_dtx_filt.csv",
                ],
                "command": "$TOOL filter /tmp/_dtx_filt.csv --where 'age > 28' --output /tmp/_dtx_filt_out.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_filt_out.csv",
                "description": "Filter keeps rows matching expression.",
            },
            {
                "name": "sort_by_column",
                "setup_commands": [
                    "printf 'name,age\\nCarol,35\\nAlice,30\\nBob,25\\n' > /tmp/_dtx_sort.csv",
                ],
                "command": "$TOOL sort /tmp/_dtx_sort.csv --by age --output /tmp/_dtx_sort_out.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_sort_out.csv",
                "description": "Sort orders rows by specified column.",
            },
            {
                "name": "aggregate_count",
                "setup_commands": [
                    "printf 'dept,salary\\neng,100\\neng,120\\nsales,80\\nsales,90\\nsales,85\\n' > /tmp/_dtx_agg.csv",
                ],
                "command": "$TOOL aggregate /tmp/_dtx_agg.csv --group-by dept --func count --column salary --output /tmp/_dtx_agg_out.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_agg_out.csv",
                "description": "Aggregate counts rows per group.",
            },
            {
                "name": "stats_output",
                "setup_commands": [
                    "printf 'x,y\\n1,2\\n3,4\\n5,6\\n' > /tmp/_dtx_stats.csv",
                ],
                "command": "$TOOL stats /tmp/_dtx_stats.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": "rows",
                "expected_file_exists": None,
                "description": "Stats subcommand prints row count information.",
            },
            {
                "name": "convert_missing_file",
                "setup_commands": [],
                "command": "$TOOL convert /tmp/_dtx_NOEXIST_FILE.csv --output /tmp/_dtx_no.json",
                "expected_exit_code": 1,
                "expected_stdout_contains": None,
                "expected_file_exists": None,
                "description": "Non-existent input file exits with code 1.",
            },
            {
                "name": "convert_stdout",
                "setup_commands": [
                    "printf 'k,v\\nfoo,1\\n' > /tmp/_dtx_stdout.csv",
                ],
                "command": "$TOOL convert /tmp/_dtx_stdout.csv --format json",
                "expected_exit_code": 0,
                "expected_stdout_contains": "foo",
                "expected_file_exists": None,
                "description": "Convert with --format but no --output writes to stdout.",
            },
            {
                "name": "aggregate_sum",
                "setup_commands": [
                    "printf 'dept,salary\\neng,100\\neng,120\\nsales,80\\n' > /tmp/_dtx_agg2.csv",
                ],
                "command": "$TOOL aggregate /tmp/_dtx_agg2.csv --group-by dept --func sum --column salary --output /tmp/_dtx_agg2_out.csv",
                "expected_exit_code": 0,
                "expected_stdout_contains": None,
                "expected_file_exists": "/tmp/_dtx_agg2_out.csv",
                "description": "Aggregate sum produces output.",
            },
        ],
    }


def _parse_plan(raw: dict) -> SoftwarePlan:
    """Convert a raw JSON dict into a typed SoftwarePlan."""
    modules = [
        ModuleSpec(
            name=m["name"],
            purpose=m.get("purpose", ""),
            dependencies=m.get("dependencies", []),
            public_api=m.get("public_api", []),
        )
        for m in raw.get("modules", [])
    ]
    commands = [
        CLICommandSpec(
            name=c["name"],
            description=c.get("description", ""),
            flags=c.get("flags", []),
            example=c.get("example", ""),
        )
        for c in raw.get("cli_commands", [])
    ]
    tests = [
        BehavioralTest(
            name=t["name"],
            command=t["command"],
            expected_exit_code=t.get("expected_exit_code", 0),
            expected_stdout_contains=t.get("expected_stdout_contains"),
            expected_file_exists=t.get("expected_file_exists"),
            setup_commands=t.get("setup_commands", []),
            description=t.get("description", ""),
        )
        for t in raw.get("behavioral_tests", [])
    ]
    return SoftwarePlan(
        idea=raw.get("idea", ""),
        name=raw.get("name", "tool"),
        description=raw.get("description", ""),
        modules=modules,
        cli_commands=commands,
        behavioral_tests=tests,
    )


# ============================================================ geometry
# ============================================================

def _build_site_model(plan: SoftwarePlan) -> None:
    """Populate site coordinates, morphisms, and cover score on *plan*."""
    if not _GEOMETRY_AVAILABLE:
        _log.info("Geometry subsystem not available; skipping site model")
        return

    builder = SiteBuilder(plan.name)
    coord_map: dict[str, Any] = {}

    for mod in plan.modules:
        c = Coordinate(mod.name, kind=CoordinateKind.MODULE)
        builder.add_coordinate(c)
        coord_map[mod.name] = c
        plan.site_coordinates.append(c)

    for mod in plan.modules:
        for dep in mod.dependencies:
            dep_key = dep if dep in coord_map else dep + ".py"
            if dep_key in coord_map:
                m = Morphism(
                    source=coord_map[mod.name],
                    target=coord_map[dep_key],
                    kind=MorphismKind.RESTRICTION,
                    label="imports",
                )
                builder.add_morphism(m)
                plan.site_morphisms.append(m)

    site = builder.build()
    plan.site = site

    # Build judgments for each module
    for mod in plan.modules:
        coord = coord_map[mod.name]
        try:
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula=mod.purpose,
            )
            _j = (
                JudgmentBuilder()
                .at(coord)
                .claiming(prop)
                .of_type(Carrier("module"))
                .build()
            )
        except Exception:
            pass  # non-critical; proceed without judgments

    # Score the cover
    try:
        cb = CoverBuilder()
        if plan.site_coordinates:
            cb.set_base(plan.site_coordinates[0])
            for idx, coord in enumerate(plan.site_coordinates):
                morph_obj = type(
                    "M", (), {"source": coord.name, "target": plan.site_coordinates[0].name, "reason": "cover"}
                )()
                try:
                    from jugeo.geometry.site import CoordinateMorphism
                    cm = CoordinateMorphism(
                        source=coord.name,
                        target=plan.site_coordinates[0].name,
                        reason="cover",
                    )
                    member = CoverMember(
                        source_coordinate=coord,
                        target_coordinate=plan.site_coordinates[0],
                        restriction_morphism=cm,
                        index=idx,
                    )
                    cb.add_member(member)
                except Exception:
                    pass
            try:
                built_cover = cb.build()
                metric = score_cover(built_cover)
                plan.cover_score = getattr(metric, "completeness", 0.0)
            except Exception:
                pass
    except Exception:
        pass

    n_coords = len(plan.site_coordinates)
    n_morphs = len(plan.site_morphisms)
    _log.info(
        "Site: %d coords, %d morphisms, cover_score=%.2f",
        n_coords, n_morphs, plan.cover_score,
    )


def _descent_identify_failing_modules(
    plan: SoftwarePlan, failures: list[dict[str, str]]
) -> list[str]:
    """Use descent heuristics to map test failures to module names."""
    if not _GEOMETRY_AVAILABLE or not failures:
        return []

    # Build local sections from module specs
    sections: dict[str, LocalSection] = {}
    for mod in plan.modules:
        sections[mod.name] = LocalSection(
            coordinate=mod.name,
            judgment_data={"purpose": mod.purpose},
            evidence_bundle=tuple(mod.public_api),
            trust_level=0.5,
            provenance=("orchestrate",),
        )

    # Simple heuristic: for each failing test, find the module whose
    # public_api symbols appear in the test command or failure output.
    failing_modules: set[str] = set()
    for fail in failures:
        cmd = fail.get("command", "").lower()
        output = fail.get("output", "").lower()
        text = cmd + " " + output
        for mod in plan.modules:
            # Check if any of the module's API or name is referenced
            if mod.name.replace(".py", "") in text:
                failing_modules.add(mod.name)
            for api in mod.public_api:
                if api.lower() in text:
                    failing_modules.add(mod.name)
        # Also map CLI subcommand names to modules via dependency graph
        for cli_cmd in plan.cli_commands:
            if cli_cmd.name in text:
                # Find the module that implements this command
                for mod in plan.modules:
                    if cli_cmd.name in mod.name or cli_cmd.name in mod.purpose.lower():
                        failing_modules.add(mod.name)

    # If no modules found, mark cli.py (entry point) and the first module
    if not failing_modules and plan.modules:
        failing_modules.add(plan.modules[0].name)

    # Attempt formal descent analysis
    try:
        engine = DescentEngine(configuration=DescentConfiguration(depth_limit=3))
        # Intentionally light usage — just logs insights
        _log.debug("DescentEngine instantiated for refinement guidance")
    except Exception:
        pass

    return sorted(failing_modules)


# ============================================================ orchestrator
# ============================================================

class Orchestrator:
    """Main orchestration engine for the ``jugeo orchestrate`` command."""

    def __init__(self, idea: str, args: argparse.Namespace) -> None:
        self.idea = idea
        self.args = args
        self.output_dir = pathlib.Path(
            getattr(args, "output", None)
            or f"outputs/orchestrate_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        self.max_iterations: int = getattr(args, "max_iterations", 5)
        self._no_llm: bool = getattr(args, "no_llm", False)
        self._model: str = getattr(args, "model", "claude-sonnet-4.6")
        self._verbose: bool = getattr(args, "verbose", False)
        self.plan: SoftwarePlan | None = None
        self.refinements: list[RefinementResult] = []

    # ---- LLM helper ------------------------------------------------

    def _llm(self, prompt: str, max_tokens: int = 4096) -> str:
        """Call LLM; raises RuntimeError if unavailable."""
        return _call_llm(prompt, model=self._model,
                         max_tokens=max_tokens, verbose=self._verbose)

    # ---- Phase 1: Elaborate -----------------------------------------

    def _phase1_elaborate(self) -> SoftwarePlan:
        """Decompose the idea into a sheaf site of modules, commands, tests."""
        if self._no_llm:
            raw = _template_plan()
            raw["idea"] = self.idea
            plan = _parse_plan(raw)
            _build_site_model(plan)
            return plan

        prompt = textwrap.dedent(f"""\
            You are a senior software architect.  Given this software idea:

            "{self.idea}"

            Produce a JSON plan with these keys:
            1. "name": a short CLI tool name (lowercase, hyphens ok)
            2. "description": one paragraph describing the tool
            3. "modules": list of modules, each with:
               - "name": filename (e.g. "parser.py")
               - "purpose": what the module does
               - "dependencies": list of other module filenames it imports
               - "public_api": list of exported function/class names
            4. "cli_commands": list of subcommands, each with:
               - "name": subcommand name
               - "description": what it does
               - "flags": list of {{"name","type","help"}} dicts
               - "example": example invocation
            5. "behavioral_tests": list of end-to-end tests, each with:
               - "name": test name
               - "setup_commands": shell commands to create fixtures
               - "command": CLI invocation (use $TOOL as placeholder)
               - "expected_exit_code": 0 for success
               - "expected_stdout_contains": string that should appear (or null)
               - "expected_file_exists": path that should exist after (or null)
               - "description": what this test verifies

            Requirements:
            - 5-8 modules, 4-8 subcommands, 10-15 behavioral tests
            - Tests must be runnable with the tool + standard unix utils
            - Include a "demo" subcommand that works without input files
            - Design for a genuinely useful CLI utility

            Return ONLY valid JSON, no markdown fences.
        """)

        try:
            text = self._llm(prompt, max_tokens=4096)
            raw = _extract_json(text)
        except Exception as exc:
            _log.warning("LLM elaboration failed (%s); using template", exc)
            raw = _template_plan()

        raw["idea"] = self.idea
        plan = _parse_plan(raw)
        _build_site_model(plan)
        return plan

    # ---- Phase 2: Generate behavioural tests -------------------------

    def _phase2_generate_tests(self, plan: SoftwarePlan) -> pathlib.Path:
        """Write ``tests/test_behavior.sh`` — the behavioural test runner."""
        test_dir = self.output_dir / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "#!/usr/bin/env bash",
            "# Auto-generated behavioural test suite for: " + plan.name,
            "# Idea: " + plan.idea[:80],
            "set -uo pipefail",
            "",
            f'TOOL="{plan.name}"',
            "PASSED=0",
            "FAILED=0",
            "TOTAL=0",
            "FAIL_DETAILS=''",
            "",
            'echo "Running behavioral tests for $TOOL"',
            'echo "=========================================="',
            "",
        ]

        for i, test in enumerate(plan.behavioral_tests, 1):
            tag = f"[{i}/{len(plan.behavioral_tests)}]"
            safe_name = test.name.replace('"', '\\"')
            lines.append(f"# Test {i}: {safe_name}")
            lines.append(f'echo -n "  {tag} {safe_name}... "')
            lines.append("TOTAL=$((TOTAL + 1))")

            # Setup
            for setup_cmd in test.setup_commands:
                lines.append(f"{setup_cmd} 2>/dev/null || true")

            # Run command, capture exit code
            lines.append(f"OUTPUT=$({test.command} 2>&1) && EXIT_CODE=$? || EXIT_CODE=$?")

            expected_exit = test.expected_exit_code
            lines.append(f'if [ "$EXIT_CODE" -eq {expected_exit} ]; then')

            # Stdout pattern check
            pattern = test.expected_stdout_contains
            file_check = test.expected_file_exists
            if pattern:
                safe_pat = pattern.replace('"', '\\"').replace("'", "'\\''")
                lines.append(f'  if echo "$OUTPUT" | grep -qi "{safe_pat}"; then')
                if file_check:
                    lines.append(f'    if [ -f "{file_check}" ]; then')
                    lines.append('      echo "PASS"')
                    lines.append("      PASSED=$((PASSED + 1))")
                    lines.append("    else")
                    lines.append(f'      echo "FAIL (file missing: {file_check})"')
                    lines.append("      FAILED=$((FAILED + 1))")
                    lines.append(f"      FAIL_DETAILS=\"$FAIL_DETAILS\\n  FAIL {safe_name}: file missing {file_check}\"")
                    lines.append("    fi")
                else:
                    lines.append('    echo "PASS"')
                    lines.append("    PASSED=$((PASSED + 1))")
                lines.append("  else")
                lines.append(f'    echo "FAIL (stdout missing: {safe_pat})"')
                lines.append("    FAILED=$((FAILED + 1))")
                lines.append(f"    FAIL_DETAILS=\"$FAIL_DETAILS\\n  FAIL {safe_name}: stdout missing '{safe_pat}'\"")
                lines.append("  fi")
            elif file_check:
                lines.append(f'  if [ -f "{file_check}" ]; then')
                lines.append('    echo "PASS"')
                lines.append("    PASSED=$((PASSED + 1))")
                lines.append("  else")
                lines.append(f'    echo "FAIL (file missing: {file_check})"')
                lines.append("    FAILED=$((FAILED + 1))")
                lines.append(f"    FAIL_DETAILS=\"$FAIL_DETAILS\\n  FAIL {safe_name}: file missing {file_check}\"")
                lines.append("  fi")
            else:
                lines.append('  echo "PASS"')
                lines.append("  PASSED=$((PASSED + 1))")

            lines.append("else")
            lines.append(
                f'  echo "FAIL (exit code: $EXIT_CODE, expected: {expected_exit})"'
            )
            lines.append("  FAILED=$((FAILED + 1))")
            lines.append(
                f"  FAIL_DETAILS=\"$FAIL_DETAILS\\n  FAIL {safe_name}: exit=$EXIT_CODE expected={expected_exit}\""
            )
            lines.append("fi")
            lines.append("")

        lines.extend([
            'echo "=========================================="',
            'echo "Results: $PASSED/$TOTAL passed, $FAILED failed"',
            'if [ "$FAILED" -gt 0 ]; then',
            '  echo ""',
            '  echo "Failure details:"',
            '  echo -e "$FAIL_DETAILS"',
            '  exit 1',
            "fi",
        ])

        test_script = test_dir / "test_behavior.sh"
        test_script.write_text("\n".join(lines) + "\n")
        test_script.chmod(0o755)
        return test_script

    # ---- Phase 3: Scaffold -------------------------------------------

    def _phase3_scaffold(self, plan: SoftwarePlan) -> pathlib.Path:
        """Create project directory, pyproject.toml, and module stubs."""
        pkg = plan.name.replace("-", "_")
        proj_dir = self.output_dir / pkg
        src_dir = proj_dir / "src" / pkg
        src_dir.mkdir(parents=True, exist_ok=True)

        # pyproject.toml
        toml = textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=68", "wheel"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{plan.name}"
            version = "0.1.0"
            description = "{plan.description[:120]}"
            requires-python = ">=3.10"
            dependencies = []

            [project.scripts]
            {plan.name} = "{pkg}.cli:main"

            [tool.setuptools.packages.find]
            where = ["src"]
        """)
        (proj_dir / "pyproject.toml").write_text(toml)

        # README
        readme = textwrap.dedent(f"""\
            # {plan.name}

            > Generated by `jugeo orchestrate`

            {plan.description}

            ## Installation

            ```bash
            pip install -e .
            ```

            ## Usage

            ```bash
            {plan.name} --help
            ```
        """)
        (proj_dir / "README.md").write_text(readme)

        # __init__.py
        (src_dir / "__init__.py").write_text(
            f'"""Auto-generated package: {plan.name}."""\n__version__ = "0.1.0"\n'
        )

        # Stub each module
        for mod in plan.modules:
            path = src_dir / mod.name
            if not path.suffix:
                path = path.with_suffix(".py")
            path.write_text(
                f'"""{mod.name} — {mod.purpose}"""\n'
                f"# TODO: implement {', '.join(mod.public_api)}\n"
            )

        return proj_dir

    # ---- Phase 4: Implement ------------------------------------------

    def _phase4_implement(
        self, plan: SoftwarePlan, proj_dir: pathlib.Path
    ) -> list[pathlib.Path]:
        """Generate code for every module via LLM or templates."""
        pkg = plan.name.replace("-", "_")
        src_dir = proj_dir / "src" / pkg
        written: list[pathlib.Path] = []

        if self._no_llm:
            self._implement_template(plan, src_dir)
        else:
            self._implement_llm(plan, src_dir)

        # Collect all .py files
        for p in sorted(src_dir.rglob("*.py")):
            written.append(p)
        return written

    def _implement_llm(
        self, plan: SoftwarePlan, src_dir: pathlib.Path
    ) -> None:
        """Use the LLM to generate each module's source code."""
        pkg = plan.name.replace("-", "_")

        # Build a context block summarising all modules
        module_summary = "\n".join(
            f"  - {m.name}: {m.purpose} (exports: {', '.join(m.public_api)})"
            for m in plan.modules
        )
        command_summary = "\n".join(
            f"  - {c.name}: {c.description} (example: {c.example})"
            for c in plan.cli_commands
        )
        test_summary = "\n".join(
            f"  - {t.name}: {t.command} (expects exit {t.expected_exit_code})"
            for t in plan.behavioral_tests
        )

        for mod in plan.modules:
            deps = ", ".join(mod.dependencies) if mod.dependencies else "none"
            prompt = textwrap.dedent(f"""\
                You are implementing a Python CLI tool called "{plan.name}"
                (package: {pkg}).

                Tool description: {plan.description}

                All modules:
                {module_summary}

                CLI subcommands:
                {command_summary}

                Behavioral tests the tool must pass:
                {test_summary}

                Now write the COMPLETE Python source for module "{mod.name}":
                  Purpose: {mod.purpose}
                  Exports: {', '.join(mod.public_api)}
                  Dependencies (other modules in package): {deps}

                Requirements:
                - Use only stdlib imports (no third-party packages).
                - Import sibling modules as: from {pkg}.X import Y
                  (where X is the module name without .py).
                - The code must be production-quality and handle errors.
                - Include docstrings for all public functions / classes.
                - Return ONLY the Python source code, no markdown fences.
            """)

            try:
                code = self._llm(prompt, max_tokens=4096)
                # Strip markdown fences if present
                code = re.sub(r"^```python\s*\n", "", code)
                code = re.sub(r"\n```\s*$", "", code)
            except Exception as exc:
                _log.warning("LLM failed for %s: %s", mod.name, exc)
                code = f'"""{mod.name} — {mod.purpose} (LLM unavailable)."""\n'

            path = src_dir / mod.name
            if not path.suffix:
                path = path.with_suffix(".py")
            path.write_text(code + "\n")

    def _implement_template(
        self, plan: SoftwarePlan, src_dir: pathlib.Path
    ) -> None:
        """Generate deterministic template code (no LLM needed)."""
        pkg = plan.name.replace("-", "_")
        self._write_template_modules(pkg, src_dir, plan)

    # ---- Template code generation ------------------------------------

    def _write_template_modules(
        self, pkg: str, src_dir: pathlib.Path, plan: SoftwarePlan
    ) -> None:
        """Write full, runnable template code for the datatx tool."""

        # ---- cli.py ----
        (src_dir / "cli.py").write_text(textwrap.dedent(f'''\
            """{pkg}.cli — argument parsing and subcommand dispatch."""
            from __future__ import annotations

            import argparse
            import sys


            def build_parser() -> argparse.ArgumentParser:
                """Build the top-level argument parser."""
                parser = argparse.ArgumentParser(
                    prog="{plan.name}",
                    description="{plan.description[:100]}",
                )
                subs = parser.add_subparsers(dest="command", help="Available commands")

                # convert
                p_conv = subs.add_parser("convert", help="Convert between data formats.")
                p_conv.add_argument("input", help="Input file path.")
                p_conv.add_argument("--output", "-o", help="Output file path.")
                p_conv.add_argument("--format", "-f", dest="fmt",
                                    choices=["csv", "json", "jsonl", "xml"],
                                    help="Explicit output format.")

                # filter
                p_filt = subs.add_parser("filter", help="Filter rows by expression.")
                p_filt.add_argument("input", help="Input file.")
                p_filt.add_argument("--where", required=True, help="Filter expression.")
                p_filt.add_argument("--output", "-o", help="Output file.")

                # sort
                p_sort = subs.add_parser("sort", help="Sort rows by column.")
                p_sort.add_argument("input", help="Input file.")
                p_sort.add_argument("--by", required=True, help="Column name.")
                p_sort.add_argument("--desc", action="store_true", help="Descending order.")
                p_sort.add_argument("--output", "-o", help="Output file.")

                # aggregate
                p_agg = subs.add_parser("aggregate", help="Aggregate with group-by.")
                p_agg.add_argument("input", help="Input file.")
                p_agg.add_argument("--group-by", required=True, help="Grouping column.")
                p_agg.add_argument("--func", required=True,
                                   choices=["count", "sum", "mean", "min", "max"],
                                   help="Aggregation function.")
                p_agg.add_argument("--column", required=True, help="Target column.")
                p_agg.add_argument("--output", "-o", help="Output file.")

                # stats
                p_stats = subs.add_parser("stats", help="Show dataset statistics.")
                p_stats.add_argument("input", help="Input file.")

                # demo
                subs.add_parser("demo", help="Run built-in demo (no files needed).")

                return parser


            def main(argv: list[str] | None = None) -> int:
                """CLI entry point."""
                parser = build_parser()
                args = parser.parse_args(argv if argv is not None else sys.argv[1:])

                if not args.command:
                    parser.print_help()
                    return 0

                try:
                    if args.command == "convert":
                        from {pkg}.convert import convert
                        return convert(args.input,
                                       output=getattr(args, "output", None),
                                       fmt=getattr(args, "fmt", None))
                    elif args.command == "filter":
                        from {pkg}.filter_engine import cmd_filter
                        return cmd_filter(args.input,
                                          where=args.where,
                                          output=getattr(args, "output", None))
                    elif args.command == "sort":
                        from {pkg}.sort_engine import cmd_sort
                        return cmd_sort(args.input,
                                        by=args.by,
                                        desc=getattr(args, "desc", False),
                                        output=getattr(args, "output", None))
                    elif args.command == "aggregate":
                        from {pkg}.aggregate import cmd_aggregate
                        return cmd_aggregate(args.input,
                                             group_by=args.group_by,
                                             func=args.func,
                                             column=args.column,
                                             output=getattr(args, "output", None))
                    elif args.command == "stats":
                        from {pkg}.stats import cmd_stats
                        return cmd_stats(args.input)
                    elif args.command == "demo":
                        from {pkg}.stats import run_demo
                        return run_demo()
                    else:
                        parser.print_help()
                        return 1
                except FileNotFoundError as exc:
                    print(f"Error: {{exc}}", file=sys.stderr)
                    return 1
                except Exception as exc:
                    print(f"Error: {{exc}}", file=sys.stderr)
                    return 1


            if __name__ == "__main__":
                sys.exit(main())
        '''))

        # ---- reader.py ----
        (src_dir / "reader.py").write_text(textwrap.dedent(f'''\
            """{pkg}.reader — unified file reader for CSV, JSON, JSONL, XML."""
            from __future__ import annotations

            import csv
            import io
            import json
            import os
            import xml.etree.ElementTree as ET
            from enum import Enum
            from typing import Any


            class Format(Enum):
                """Supported data formats."""
                CSV = "csv"
                JSON = "json"
                JSONL = "jsonl"
                XML = "xml"


            def detect_format(path: str) -> Format:
                """Detect format from file extension."""
                ext = os.path.splitext(path)[1].lower().lstrip(".")
                mapping = {{"csv": Format.CSV, "json": Format.JSON,
                           "jsonl": Format.JSONL, "xml": Format.XML}}
                if ext in mapping:
                    return mapping[ext]
                raise ValueError(f"Cannot detect format for extension '.{{ext}}'")


            def read_file(path: str, fmt: Format | None = None) -> list[dict[str, Any]]:
                """Read a data file and return a list of row dicts."""
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"File not found: {{path}}")
                if fmt is None:
                    fmt = detect_format(path)
                if fmt == Format.CSV:
                    return _read_csv(path)
                elif fmt == Format.JSON:
                    return _read_json(path)
                elif fmt == Format.JSONL:
                    return _read_jsonl(path)
                elif fmt == Format.XML:
                    return _read_xml(path)
                raise ValueError(f"Unsupported format: {{fmt}}")


            def _read_csv(path: str) -> list[dict[str, Any]]:
                with open(path, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    return list(reader)


            def _read_json(path: str) -> list[dict[str, Any]]:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return [data]
                raise ValueError("JSON root must be array or object")


            def _read_jsonl(path: str) -> list[dict[str, Any]]:
                rows: list[dict[str, Any]] = []
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            rows.append(json.loads(line))
                return rows


            def _read_xml(path: str) -> list[dict[str, Any]]:
                tree = ET.parse(path)
                root = tree.getroot()
                rows: list[dict[str, Any]] = []
                for child in root:
                    row: dict[str, Any] = {{}}
                    for elem in child:
                        row[elem.tag] = elem.text
                    rows.append(row)
                return rows
        '''))

        # ---- writer.py ----
        (src_dir / "writer.py").write_text(textwrap.dedent(f'''\
            """{pkg}.writer — write list[dict] to CSV / JSON / JSONL / XML."""
            from __future__ import annotations

            import csv
            import io
            import json
            import sys
            import xml.etree.ElementTree as ET
            from typing import Any


            def write_file(rows: list[dict[str, Any]], path: str | None,
                           fmt: str = "json") -> None:
                """Write rows to a file or stdout."""
                text = _format(rows, fmt)
                if path:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                else:
                    sys.stdout.write(text)
                    if not text.endswith("\\n"):
                        sys.stdout.write("\\n")


            def format_table(rows: list[dict[str, Any]]) -> str:
                """Format rows as a simple ASCII table."""
                if not rows:
                    return "(empty)"
                cols = list(rows[0].keys())
                widths = {{c: len(c) for c in cols}}
                for r in rows:
                    for c in cols:
                        widths[c] = max(widths[c], len(str(r.get(c, ""))))
                header = " | ".join(c.ljust(widths[c]) for c in cols)
                sep = "-+-".join("-" * widths[c] for c in cols)
                body = "\\n".join(
                    " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols)
                    for r in rows
                )
                return header + "\\n" + sep + "\\n" + body


            def _format(rows: list[dict[str, Any]], fmt: str) -> str:
                if fmt == "csv":
                    return _to_csv(rows)
                elif fmt == "json":
                    return json.dumps(rows, indent=2) + "\\n"
                elif fmt == "jsonl":
                    return "\\n".join(json.dumps(r) for r in rows) + "\\n"
                elif fmt == "xml":
                    return _to_xml(rows)
                raise ValueError(f"Unknown format: {{fmt}}")


            def _to_csv(rows: list[dict[str, Any]]) -> str:
                if not rows:
                    return ""
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
                return buf.getvalue()


            def _to_xml(rows: list[dict[str, Any]]) -> str:
                root = ET.Element("data")
                for row in rows:
                    item = ET.SubElement(root, "item")
                    for k, v in row.items():
                        child = ET.SubElement(item, k)
                        child.text = str(v) if v is not None else ""
                return ET.tostring(root, encoding="unicode") + "\\n"
        '''))

        # ---- convert.py ----
        (src_dir / "convert.py").write_text(textwrap.dedent(f'''\
            """{pkg}.convert — format conversion orchestration."""
            from __future__ import annotations

            import os
            from typing import Any

            from {pkg}.reader import read_file, detect_format, Format
            from {pkg}.writer import write_file


            _EXT_MAP = {{"csv": "csv", "json": "json", "jsonl": "jsonl", "xml": "xml"}}


            def convert(input_path: str, output: str | None = None,
                        fmt: str | None = None) -> int:
                """Convert input file to the target format.

                If *output* is given, format is inferred from its extension.
                If *fmt* is given, it overrides the inferred format.
                If neither *output* nor *fmt* is given, prints JSON to stdout.
                """
                rows = read_file(input_path)

                if fmt is None and output:
                    ext = os.path.splitext(output)[1].lstrip(".").lower()
                    fmt = _EXT_MAP.get(ext, "json")
                elif fmt is None:
                    fmt = "json"

                write_file(rows, output, fmt=fmt)
                return 0
        '''))

        # ---- filter_engine.py ----
        (src_dir / "filter_engine.py").write_text(textwrap.dedent(f'''\
            """{pkg}.filter_engine — row filtering with safe expression evaluation."""
            from __future__ import annotations

            import operator
            import re
            from typing import Any

            from {pkg}.reader import read_file
            from {pkg}.writer import write_file


            _OPS = {{
                ">": operator.gt,
                "<": operator.lt,
                ">=": operator.ge,
                "<=": operator.le,
                "==": operator.eq,
                "!=": operator.ne,
            }}

            _EXPR_RE = re.compile(
                r"^\\s*(\\w+)\\s*(>=|<=|!=|==|>|<)\\s*(.+?)\\s*$"
            )


            def parse_expression(expr: str) -> tuple[str, str, str]:
                """Parse 'column op value' into (column, op, value)."""
                m = _EXPR_RE.match(expr)
                if not m:
                    raise ValueError(f"Invalid filter expression: {{expr!r}}")
                return m.group(1), m.group(2), m.group(3)


            def _coerce(val: str) -> int | float | str:
                """Try to interpret a string as a number."""
                try:
                    return int(val)
                except ValueError:
                    pass
                try:
                    return float(val)
                except ValueError:
                    return val


            def apply_filter(rows: list[dict[str, Any]],
                             expr: str) -> list[dict[str, Any]]:
                """Return rows matching *expr*."""
                col, op_str, raw_val = parse_expression(expr)
                op_fn = _OPS[op_str]
                target = _coerce(raw_val.strip("'\\\""))
                result: list[dict[str, Any]] = []
                for row in rows:
                    cell = row.get(col, "")
                    cell_val = _coerce(str(cell))
                    try:
                        if op_fn(cell_val, target):
                            result.append(row)
                    except TypeError:
                        pass
                return result


            def cmd_filter(input_path: str, where: str,
                           output: str | None = None) -> int:
                """CLI handler for the filter subcommand."""
                import os
                rows = read_file(input_path)
                filtered = apply_filter(rows, where)
                fmt = "csv"
                if output:
                    ext = os.path.splitext(output)[1].lstrip(".").lower()
                    fmt = ext if ext in ("csv", "json", "jsonl", "xml") else "csv"
                write_file(filtered, output, fmt=fmt)
                return 0
        '''))

        # ---- sort_engine.py ----
        (src_dir / "sort_engine.py").write_text(textwrap.dedent(f'''\
            """{pkg}.sort_engine — multi-column sort with type awareness."""
            from __future__ import annotations

            import os
            from typing import Any

            from {pkg}.reader import read_file
            from {pkg}.writer import write_file


            def _sort_key(row: dict[str, Any], col: str) -> Any:
                """Generate a sort key, preferring numeric comparison."""
                val = row.get(col, "")
                try:
                    return (0, float(str(val)))
                except (ValueError, TypeError):
                    return (1, str(val).lower())


            def apply_sort(rows: list[dict[str, Any]], by: str,
                           desc: bool = False) -> list[dict[str, Any]]:
                """Sort rows by the given column."""
                return sorted(rows, key=lambda r: _sort_key(r, by), reverse=desc)


            def cmd_sort(input_path: str, by: str, desc: bool = False,
                         output: str | None = None) -> int:
                """CLI handler for the sort subcommand."""
                rows = read_file(input_path)
                sorted_rows = apply_sort(rows, by, desc)
                fmt = "csv"
                if output:
                    ext = os.path.splitext(output)[1].lstrip(".").lower()
                    fmt = ext if ext in ("csv", "json", "jsonl", "xml") else "csv"
                write_file(sorted_rows, output, fmt=fmt)
                return 0
        '''))

        # ---- aggregate.py ----
        (src_dir / "aggregate.py").write_text(textwrap.dedent(f'''\
            """{pkg}.aggregate — group-by aggregation: count, sum, mean, min, max."""
            from __future__ import annotations

            import os
            from collections import defaultdict
            from enum import Enum
            from typing import Any

            from {pkg}.reader import read_file
            from {pkg}.writer import write_file


            class AggFunc(Enum):
                """Supported aggregation functions."""
                COUNT = "count"
                SUM = "sum"
                MEAN = "mean"
                MIN = "min"
                MAX = "max"


            def aggregate(rows: list[dict[str, Any]], group_by: str,
                          func: str, column: str) -> list[dict[str, Any]]:
                """Aggregate *rows* by *group_by*, applying *func* to *column*."""
                groups: dict[str, list[float]] = defaultdict(list)
                for row in rows:
                    key = str(row.get(group_by, ""))
                    try:
                        val = float(str(row.get(column, 0)))
                    except (ValueError, TypeError):
                        val = 0.0
                    groups[key].append(val)

                result: list[dict[str, Any]] = []
                for key in sorted(groups):
                    vals = groups[key]
                    if func == "count":
                        agg_val = len(vals)
                    elif func == "sum":
                        agg_val = sum(vals)
                    elif func == "mean":
                        agg_val = sum(vals) / len(vals) if vals else 0
                    elif func == "min":
                        agg_val = min(vals) if vals else 0
                    elif func == "max":
                        agg_val = max(vals) if vals else 0
                    else:
                        raise ValueError(f"Unknown aggregation: {{func}}")
                    result.append({{group_by: key, f"{{func}}_{{column}}": agg_val}})
                return result


            def cmd_aggregate(input_path: str, group_by: str, func: str,
                              column: str, output: str | None = None) -> int:
                """CLI handler for the aggregate subcommand."""
                rows = read_file(input_path)
                agg_rows = aggregate(rows, group_by, func, column)
                fmt = "csv"
                if output:
                    ext = os.path.splitext(output)[1].lstrip(".").lower()
                    fmt = ext if ext in ("csv", "json", "jsonl", "xml") else "csv"
                write_file(agg_rows, output, fmt=fmt)
                return 0
        '''))

        # ---- stats.py ----
        (src_dir / "stats.py").write_text(textwrap.dedent(f'''\
            """{pkg}.stats — dataset statistics and demo command."""
            from __future__ import annotations

            import json
            import sys
            from typing import Any

            from {pkg}.reader import read_file
            from {pkg}.writer import format_table


            def compute_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
                """Compute basic statistics for a dataset."""
                if not rows:
                    return {{"rows": 0, "columns": 0, "column_info": {{}}}}
                cols = list(rows[0].keys())
                info: dict[str, dict[str, Any]] = {{}}
                for c in cols:
                    vals = [r.get(c) for r in rows]
                    non_null = [v for v in vals if v is not None and str(v).strip()]
                    nums: list[float] = []
                    for v in non_null:
                        try:
                            nums.append(float(str(v)))
                        except (ValueError, TypeError):
                            pass
                    col_info: dict[str, Any] = {{
                        "non_null": len(non_null),
                        "null": len(vals) - len(non_null),
                        "type": "numeric" if len(nums) == len(non_null) and nums else "string",
                    }}
                    if nums:
                        col_info["min"] = min(nums)
                        col_info["max"] = max(nums)
                        col_info["mean"] = sum(nums) / len(nums)
                    info[c] = col_info
                return {{"rows": len(rows), "columns": len(cols), "column_info": info}}


            def print_stats(stats: dict[str, Any]) -> None:
                """Print statistics to stdout."""
                print(f"Dataset: {{stats['rows']}} rows, {{stats['columns']}} columns")
                for col, info in stats.get("column_info", {{}}).items():
                    dtype = info.get("type", "unknown")
                    non_null = info.get("non_null", 0)
                    null = info.get("null", 0)
                    line = f"  {{col}}: {{dtype}}, {{non_null}} non-null, {{null}} null"
                    if "mean" in info:
                        line += f", mean={{info['mean']:.2f}}, min={{info['min']}}, max={{info['max']}}"
                    print(line)


            def cmd_stats(input_path: str) -> int:
                """CLI handler for the stats subcommand."""
                rows = read_file(input_path)
                st = compute_stats(rows)
                print_stats(st)
                return 0


            def run_demo() -> int:
                """Run a built-in demo using sample data."""
                sample = [
                    {{"name": "Alice", "age": "30", "dept": "Engineering"}},
                    {{"name": "Bob", "age": "25", "dept": "Sales"}},
                    {{"name": "Carol", "age": "35", "dept": "Engineering"}},
                    {{"name": "Dave", "age": "28", "dept": "Sales"}},
                    {{"name": "Eve", "age": "32", "dept": "Marketing"}},
                ]
                print("Demo: Sample employee dataset")
                print("=" * 50)
                print()
                print(format_table(sample))
                print()
                st = compute_stats(sample)
                print_stats(st)
                print()
                print("Demo complete. Try: datatx --help")
                return 0
        '''))

    # ---- Phase 5: Verify ---------------------------------------------

    def _phase5_verify(
        self, plan: SoftwarePlan, proj_dir: pathlib.Path,
        test_script: pathlib.Path,
    ) -> TestRunResult:
        """Install the project in dev mode and run behavioural tests."""
        result = TestRunResult()

        # Install project
        install_cmd = [
            sys.executable, "-m", "pip", "install", "-e", str(proj_dir),
            "--quiet", "--no-deps",
        ]
        # Homebrew / PEP 668 environments need --break-system-packages
        probe = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run",
             "--break-system-packages", "pip"],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            install_cmd.append("--break-system-packages")

        install_proc = subprocess.run(
            install_cmd,
            capture_output=True, text=True, timeout=120,
        )
        if install_proc.returncode != 0:
            _log.warning("pip install failed: %s", install_proc.stderr[:300])

        # Run tests
        test_proc = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        raw = test_proc.stdout + "\n" + test_proc.stderr
        result.raw_output = raw

        # Parse results
        m = re.search(r"Results:\s*(\d+)/(\d+)\s*passed,\s*(\d+)\s*failed", raw)
        if m:
            result.passed = int(m.group(1))
            result.total = int(m.group(2))
            result.failed = int(m.group(3))
        else:
            # Count PASS/FAIL lines
            result.passed = raw.count("PASS")
            result.failed = raw.count("FAIL")
            result.total = result.passed + result.failed

        # Extract failure details
        for line in raw.split("\n"):
            if "FAIL" in line and "FAIL" != line.strip():
                result.failures.append({
                    "line": line.strip(),
                    "command": "",
                    "output": line,
                })

        return result

    # ---- Phase 6: Refine ---------------------------------------------

    def _phase6_refine(
        self, plan: SoftwarePlan, proj_dir: pathlib.Path,
        test_results: TestRunResult,
    ) -> TestRunResult:
        """Re-generate failing modules guided by descent analysis."""
        pkg = plan.name.replace("-", "_")
        src_dir = proj_dir / "src" / pkg

        failing_modules = _descent_identify_failing_modules(
            plan, test_results.failures
        )

        if not failing_modules:
            # Heuristic: re-generate cli.py if we can't pinpoint the issue
            failing_modules = ["cli.py"]

        if self._verbose:
            print(f"    Descent identified modules to repair: {failing_modules}")

        if self._no_llm:
            # In no-llm mode the template is already complete; nothing to fix
            return test_results

        for mod_name in failing_modules:
            mod_spec = next((m for m in plan.modules if m.name == mod_name), None)
            if mod_spec is None:
                continue

            # Read existing code
            path = src_dir / mod_name
            if not path.suffix:
                path = path.with_suffix(".py")
            existing_code = ""
            if path.exists():
                existing_code = path.read_text()

            prompt = textwrap.dedent(f"""\
                You are fixing a Python module that is causing test failures.

                Tool: {plan.name} (package: {pkg})
                Module: {mod_name}
                Purpose: {mod_spec.purpose}
                Exports: {', '.join(mod_spec.public_api)}

                Current code:
                ```python
                {existing_code[:3000]}
                ```

                Test failures:
                {test_results.raw_output[-2000:]}

                Fix the module so all tests pass.  Return ONLY the complete
                Python source, no markdown fences.
            """)

            try:
                code = self._llm(prompt, max_tokens=4096)
                code = re.sub(r"^```python\s*\n", "", code)
                code = re.sub(r"\n```\s*$", "", code)
                path.write_text(code + "\n")
            except Exception as exc:
                _log.warning("LLM repair failed for %s: %s", mod_name, exc)

        return test_results

    # ---- Phase 7: Report ---------------------------------------------

    def _phase7_report(
        self, plan: SoftwarePlan, proj_dir: pathlib.Path,
        final_results: TestRunResult, duration: float,
    ) -> None:
        """Print the final summary report."""
        width = 60
        print(f"\n{'=' * width}")
        print("  Orchestration complete")
        print(f"{'=' * width}")
        print(f"  Tool name  : {plan.name}")
        print(f"  Modules    : {len(plan.modules)}")
        print(f"  Commands   : {len(plan.cli_commands)}")
        print(f"  Tests      : {final_results.passed}/{final_results.total} passed")
        if _GEOMETRY_AVAILABLE and plan.site:
            print(f"  Site coords: {len(plan.site_coordinates)}")
            print(f"  Morphisms  : {len(plan.site_morphisms)}")
            print(f"  Cover score: {plan.cover_score:.2f}")
        print(f"  Duration   : {duration:.1f}s")
        print(f"  Project    : {proj_dir}")
        print(f"  Install    : pip install -e {proj_dir}")
        print(f"  Run        : {plan.name} --help")
        print(f"{'=' * width}")

        # Write plan JSON
        plan_path = self.output_dir / "plan.json"
        plan_dict = {
            "idea": plan.idea,
            "name": plan.name,
            "description": plan.description,
            "modules": [
                {"name": m.name, "purpose": m.purpose,
                 "dependencies": m.dependencies, "public_api": m.public_api}
                for m in plan.modules
            ],
            "cli_commands": [
                {"name": c.name, "description": c.description,
                 "example": c.example}
                for c in plan.cli_commands
            ],
            "tests_passed": final_results.passed,
            "tests_total": final_results.total,
            "duration_seconds": round(duration, 2),
            "refinement_iterations": len(self.refinements),
        }
        plan_path.write_text(json.dumps(plan_dict, indent=2) + "\n")

    # ---- Main run loop -----------------------------------------------

    def run(self) -> int:
        """Execute the full orchestration pipeline."""
        t0 = time.perf_counter()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        width = 60
        print(f"\n{'=' * width}")
        print("  jugeo orchestrate — sheaf-theoretic software synthesis")
        print(f"{'=' * width}")
        print(f"  Idea: {self.idea}")
        print(f"  Output: {self.output_dir}")
        print(f"  Max iterations: {self.max_iterations}")
        if _GEOMETRY_AVAILABLE:
            print("  Geometry: available ✓")
        else:
            print("  Geometry: unavailable (running without sheaf model)")
        print(f"{'=' * width}\n")

        # Phase 1
        print("  Phase 1: Elaborating idea into sheaf model...")
        self.plan = self._phase1_elaborate()
        plan = self.plan
        print(
            f"    ✓ {plan.name}: {len(plan.modules)} modules, "
            f"{len(plan.cli_commands)} commands, "
            f"{len(plan.behavioral_tests)} tests"
        )
        if plan.site:
            print(
                f"    ✓ Site: {len(plan.site_coordinates)} coordinates, "
                f"{len(plan.site_morphisms)} morphisms"
            )

        # Phase 2
        print("\n  Phase 2: Generating behavioral tests...")
        test_script = self._phase2_generate_tests(plan)
        print(f"    ✓ {len(plan.behavioral_tests)} tests → {test_script}")

        # Phase 3
        print("\n  Phase 3: Scaffolding project...")
        proj_dir = self._phase3_scaffold(plan)
        print(f"    ✓ Project at {proj_dir}")

        # Phase 4
        print("\n  Phase 4: Implementing modules...")
        files = self._phase4_implement(plan, proj_dir)
        print(f"    ✓ {len(files)} files generated")

        # Phase 5-6: Verify & refine loop
        final_results = TestRunResult()
        for iteration in range(1, self.max_iterations + 1):
            iter_t0 = time.perf_counter()
            print(
                f"\n  Phase 5-6: Verify & Refine "
                f"(iteration {iteration}/{self.max_iterations})..."
            )
            final_results = self._phase5_verify(plan, proj_dir, test_script)

            passed = final_results.passed
            total = final_results.total
            failed = final_results.failed
            iter_dur = time.perf_counter() - iter_t0

            self.refinements.append(
                RefinementResult(
                    iteration=iteration,
                    tests_total=total,
                    tests_passed=passed,
                    tests_failed=failed,
                    duration=iter_dur,
                )
            )

            print(f"    Tests: {passed}/{total} passed")

            if passed == total and total > 0:
                print(
                    f"    ✓ All tests pass! "
                    f"Converged in {iteration} iteration(s)"
                )
                break

            if iteration < self.max_iterations:
                print(f"    ↻ Refining ({failed} failures)...")
                self._phase6_refine(plan, proj_dir, final_results)

        # Phase 7
        duration = time.perf_counter() - t0
        self._phase7_report(plan, proj_dir, final_results, duration)

        return 0 if final_results.failed == 0 else 1


# ===================================================================== CLI
# =====================================================================

def add_subparser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Register the ``orchestrate`` subcommand."""
    p = subparsers.add_parser(
        "orchestrate",
        help="Synthesize a complete program from a natural language idea.",
        description=(
            "Uses sheaf-theoretic elaboration to plan, generate, test, "
            "and refine software from a natural language idea."
        ),
    )
    p.add_argument(
        "idea", type=str,
        help="The software idea to implement (quoted string).",
    )
    p.add_argument(
        "--max-iterations", type=int, default=5, metavar="N",
        help="Maximum refinement iterations (default: 5).",
    )
    p.add_argument(
        "--output", "-o", type=str, default=None, metavar="DIR",
        help="Output directory for the generated project.",
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrate command."""
    orch = Orchestrator(args.idea, args)
    return orch.run()
