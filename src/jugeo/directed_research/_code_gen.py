"""Code generation with jg-based verification.

Each generated module is a local section on the Code surface of the workspace
site. The agent writes code directly to files (using its file-write capability),
and then jg prove / jg bugs are run to upgrade trust from COPILOT_SUGGESTED
to SOLVER_DISCHARGED.

The architecture design step is a *covering family decomposition*: the
full codebase is decomposed into modules such that the covering family
{module_1, ..., module_n} covers the entire Code surface. Each module
is a local section, and the integration module ensures the overlap
conditions (import compatibility, type consistency) are satisfied.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    LLMSection,
    MoveKind,
    MoveResult,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json, agent_file_write
from jugeo.directed_research._verification import verify_code_file, check_code_bugs


def design_architecture(
    prompt: str,
    approach: str,
    theory_text: str,
    domain_analysis: dict,
    *,
    verbose: bool = False,
) -> tuple[dict, LLMSection]:
    """Design the package architecture as a covering family decomposition.

    The architecture is a covering family: a set of modules that jointly
    cover the Code surface. Each module is a coordinate in the site, and
    the dependencies between modules are morphisms.
    """
    libs = domain_analysis.get("standard_libraries", [])
    prompt_text = textwrap.dedent(f"""\
        Design the Python package architecture for this tool:

        PRODUCT: {prompt}
        APPROACH: {approach}
        THEORY (first 500 chars): {theory_text[:500]}
        STANDARD LIBRARIES: {json.dumps(libs)}

        Design 8-12 Python modules that form an INTEGRATED system — modules
        must share types, call each other, and form a coherent pipeline.
        Name them after DOMAIN concepts. Include data ingestion, core types,
        algorithms, evaluation/benchmarking, a CLI, and integration with
        {', '.join(libs[:5])} and {', '.join(domain_analysis.get('standard_datasets', [])[:3])}.
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

    return agent_json(
        prompt_text,
        surface=SurfaceKind.CODE,
        coordinate="code.architecture",
    )


def generate_module(
    mod: dict,
    *,
    pkg_name: str,
    output_dir: str,
    prompt: str,
    approach: str,
    theory_text: str,
    domain_analysis: dict,
    existing_modules: dict[str, str],
    verbose: bool = False,
) -> tuple[str, LLMSection]:
    """Generate one module (one local section on the Code surface).

    The agent generates the code, writes it to a file, and the code is
    then verified via jg prove and jg bugs.
    """
    name = mod.get("name", "module")
    pkg_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
    if not pkg_name_clean:
        pkg_name_clean = "research_output"

    pkg_dir = os.path.join(output_dir, "src", pkg_name_clean)
    os.makedirs(pkg_dir, exist_ok=True)

    # Write __init__.py if not yet
    init_path = os.path.join(pkg_dir, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w") as f:
            f.write(f'"""{approach}"""\n__version__ = "0.1.0"\n')

    libs = domain_analysis.get("standard_libraries", [])
    deps = mod.get('dependencies', [])
    path = os.path.join(pkg_dir, f"{name}.py")

    import_hint = ""
    if deps:
        import_hint = f"IMPORTS FROM OTHER MODULES: from .{' import ..., from .'.join(deps)} import ...\n"
    if existing_modules:
        import_hint += f"ALREADY GENERATED (import and USE these): {', '.join(existing_modules.keys())}\n"

    # Tell the agent to WRITE the file to disk at the exact path.
    # The agent has file-write tools — it should use them, not return text.
    prompt_text = (
        f"Write a Python module to the file {path}\n\n"
        f"Module: {name}.py for the {pkg_name_clean} package.\n"
        f"PURPOSE: {mod.get('purpose', '')}\n"
        f"KEY CLASSES: {', '.join(mod.get('key_classes', []))}\n"
        f"KEY FUNCTIONS: {', '.join(mod.get('key_functions', []))}\n"
        f"{import_hint}"
        f"LIBRARIES: {', '.join(libs[:5])}\n\n"
        f"Write 500+ lines of real, production-quality Python 3.10+ code "
        f"with type hints and docstrings. No stubs, no placeholders. "
        f"Write the complete file to {path} using your file-write tool."
    )

    section = agent_call(
        prompt_text,
        surface=SurfaceKind.CODE,
        coordinate=f"code.{name}",
        working_dir=output_dir,
    )

    # The agent should have written the file. Read it back.
    if os.path.exists(path) and os.path.getsize(path) > 50:
        with open(path) as f:
            code = f.read()
    else:
        # Fallback: agent returned code as text instead of writing
        code = section.content
        if code.startswith("```"):
            code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))
        code = code.strip() + "\n"
        with open(path, "w") as f:
            f.write(code)

    # Update the section content to reflect what's actually on disk
    section = LLMSection(
        surface=section.surface,
        coordinate=section.coordinate,
        content=code,
        trust=section.trust,
        provenance=section.provenance,
        prompt_hash=section.prompt_hash,
        elapsed=section.elapsed,
        token_count=len(code.split()),
        agent_backend=section.agent_backend,
        files_touched=[path],
        commands_run=section.commands_run,
    )

    return path, section


def generate_tests(
    pkg_name: str,
    output_dir: str,
    modules: list[dict],
    *,
    verbose: bool = False,
) -> tuple[str, LLMSection]:
    """Generate test suite for all modules.

    Tests are local sections on the Evidence surface — they produce
    runtime evidence about code behavior.
    """
    pkg_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
    mod_names = [m.get("name", "module") for m in modules]

    test_dir = os.path.join(output_dir, "tests")
    os.makedirs(test_dir, exist_ok=True)
    test_path = os.path.join(test_dir, f"test_{pkg_name_clean}.py")

    section = agent_call(
        f"""Write a comprehensive pytest test suite to the file {test_path}

Package to test: {pkg_name_clean}
Modules to test: {', '.join(mod_names)}

Write tests that:
1. Import each module
2. Test key classes and functions
3. Include edge cases
4. Use pytest fixtures
5. Include at least 3 tests per module

Write the complete test file to {test_path} using your file-write tool.
""",
        surface=SurfaceKind.EVIDENCE,
        coordinate="evidence.tests",
        working_dir=output_dir,
    )

    # Read back what the agent wrote
    if os.path.exists(test_path) and os.path.getsize(test_path) > 50:
        with open(test_path) as f:
            code = f.read()
    else:
        # Fallback
        code = section.content
        if code.startswith("```"):
            code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))
        with open(test_path, "w") as f:
            f.write(code.strip() + "\n")

    return test_path, section


def write_pyproject(
    output_dir: str,
    pkg_name: str,
    approach: str,
    dependencies: list[str],
) -> str:
    """Write pyproject.toml."""
    pkg_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
    path = os.path.join(output_dir, "pyproject.toml")
    with open(path, "w") as f:
        f.write(textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=68"]
            build-backend = "setuptools.backends._legacy:_Backend"

            [project]
            name = "{pkg_name_clean}"
            version = "0.1.0"
            description = "{approach}"
            requires-python = ">=3.10"
            dependencies = {json.dumps(dependencies)}

            [tool.setuptools.packages.find]
            where = ["src"]
        """))
    return path


def syntax_check_all(code_files: list[str]) -> dict[str, bool]:
    """Check all .py files parse correctly."""
    results = {}
    for f in code_files:
        if not f.endswith(".py") or not os.path.exists(f):
            continue
        try:
            r = subprocess.run(
                [sys.executable, "-c", f"import ast; ast.parse(open('{f}').read())"],
                capture_output=True, timeout=10)
            results[os.path.basename(f)] = r.returncode == 0
        except Exception:
            results[os.path.basename(f)] = False
    return results


def import_check(output_dir: str, pkg_name: str) -> tuple[bool, str]:
    """Check the package imports without error."""
    pkg_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg_name).strip('_').lower()
    src_dir = os.path.join(output_dir, "src")
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, '{src_dir}'); import {pkg_name_clean}"],
            capture_output=True, text=True, timeout=15)
        ok = r.returncode == 0
        detail = r.stderr[:200] if not ok else "OK"
        return ok, detail
    except Exception as e:
        return False, str(e)
