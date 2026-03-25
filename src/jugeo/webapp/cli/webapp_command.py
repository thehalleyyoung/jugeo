"""``jugeo webapp`` subcommand — argument parsing and execution.

Standalone module (no jugeo core imports).  Imports only from sibling
submodules inside ``jugeo.webapp.cli``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _parse_models_arg(models_arg: str) -> list:
    """Parse ``--models``: JSON string or path to a JSON file."""
    if not models_arg:
        return []
    models_arg = models_arg.strip()
    if not models_arg:
        return []
    # Try as file path first
    if os.path.isfile(models_arg):
        with open(models_arg, "r") as fh:
            return json.load(fh)
    # Otherwise treat as inline JSON
    try:
        result = json.loads(models_arg)
        if isinstance(result, list):
            return result
        return [result]
    except json.JSONDecodeError:
        return []


def _parse_routes_arg(routes_arg: str) -> list:
    """Parse ``--routes``: JSON string or path to a JSON file."""
    if not routes_arg:
        return []
    routes_arg = routes_arg.strip()
    if not routes_arg:
        return []
    if os.path.isfile(routes_arg):
        with open(routes_arg, "r") as fh:
            return json.load(fh)
    try:
        result = json.loads(routes_arg)
        if isinstance(result, list):
            return result
        return [result]
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------

def _print_ideation_results(results: dict) -> None:
    """Pretty-print ideation results to stdout."""
    print()
    print("+" + "-" * 58 + "+")
    print("|" + " IDEATION RESULTS".center(58) + "|")
    print("+" + "-" * 58 + "+")
    if not results:
        print("  (no results)")
        return
    for key, value in results.items():
        if isinstance(value, list):
            print(f"  {key}:")
            for item in value:
                print(f"    - {item}")
        elif isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")
    print()


def _print_generation_summary(result: dict) -> None:
    """Pretty-print generation summary to stdout."""
    print()
    print("+" + "-" * 58 + "+")
    print("|" + " GENERATION SUMMARY".center(58) + "|")
    print("+" + "-" * 58 + "+")
    if not result:
        print("  (no result)")
        return
    for key, value in result.items():
        if isinstance(value, list):
            print(f"  {key}: {len(value)} items")
        else:
            print(f"  {key}: {value}")
    print()


def _print_verification_report(result: dict) -> None:
    """Pretty-print verification results to stdout."""
    print()
    print("+" + "-" * 58 + "+")
    print("|" + " VERIFICATION REPORT".center(58) + "|")
    print("+" + "-" * 58 + "+")
    if not result:
        print("  (no result)")
        return
    passed = result.get("passed", False)
    status = "PASS" if passed else "FAIL"
    print(f"  Status: {status}")
    checks = result.get("checks", [])
    if checks:
        print(f"  Checks: {len(checks)}")
        for chk in checks:
            name = chk.get("name", "unknown")
            ok = chk.get("passed", False)
            print(f"    {'[OK]' if ok else '[FAIL]'} {name}")
    errors = result.get("errors", [])
    if errors:
        print("  Errors:")
        for err in errors:
            print(f"    - {err}")
    print()


def _print_descent_check(descent_result: dict) -> None:
    """Pretty-print theory descent check results to stdout."""
    violations = descent_result.get("violations", [])
    if not violations:
        return
    print()
    print("+" + "-" * 58 + "+")
    print("|" + " THEORY DESCENT CHECK".center(58) + "|")
    print("+" + "-" * 58 + "+")
    for v in violations:
        severity = v.get("severity", "info").upper()
        detail = v.get("detail", str(v))
        print(f"  [{severity}] {detail}")
    print()


# ---------------------------------------------------------------------------
# register / run
# ---------------------------------------------------------------------------

def register_webapp_command(subparsers) -> None:
    """Register ``jugeo webapp`` with the given argparse subparsers."""
    parser = subparsers.add_parser(
        "webapp",
        help="Generate a Flask web application",
        description="Generate, verify and report on a Flask web application.",
    )

    # Required
    parser.add_argument(
        "--outdir", "-o",
        required=True,
        help="Output directory for the generated application.",
    )

    # Optional flags
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5000,
        help="Port number for the Flask dev server (default: 5000).",
    )
    parser.add_argument(
        "--name",
        default="app",
        dest="app_name",
        help="Application name (default: app).",
    )
    parser.add_argument(
        "--type", "-t",
        default="crud",
        dest="app_type",
        choices=["crud", "api", "dashboard", "form_workflow", "custom"],
        help="Application type (default: crud).",
    )
    parser.add_argument(
        "--domain",
        default="",
        help="Domain description for ideation.",
    )
    parser.add_argument(
        "--user-population",
        default="",
        dest="user_population",
        help="Description of target user population.",
    )
    parser.add_argument(
        "--template",
        default="standard",
        choices=["minimal", "standard", "full", "custom"],
        help="Template complexity (default: standard).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Run verification after generation (default: True).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="Skip verification.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Verbose output.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        default=False,
        dest="include_tests",
        help="Generate test scaffolding.",
    )
    parser.add_argument(
        "--include-docker",
        action="store_true",
        default=False,
        dest="include_docker",
        help="Generate a Dockerfile.",
    )
    parser.add_argument(
        "--include-security-scan",
        action="store_true",
        default=False,
        dest="include_security_scan",
        help="Run a security scan.",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Models as JSON string or path to JSON file.",
    )
    parser.add_argument(
        "--routes",
        default="",
        help="Routes as JSON string or path to JSON file.",
    )
    parser.add_argument(
        "--ideate",
        action="store_true",
        default=False,
        help="Run ideation only (do not generate).",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        default=False,
        dest="html_only",
        help="Generate an HTML-only app (no Flask required at runtime).",
    )

    parser.set_defaults(func=run_webapp_command)


def run_webapp_command(args) -> int:
    """Handler for ``jugeo webapp``.  Returns 0 on success, 1 on error."""
    from jugeo.webapp.cli.models import WebappConfig, AppType, TemplateChoice
    from jugeo.webapp.cli.pipeline import WebappPipeline, SpecBuilder

    # Parse complex arguments
    models = _parse_models_arg(getattr(args, "models", ""))
    routes = _parse_routes_arg(getattr(args, "routes", ""))

    config = WebappConfig(
        outdir=getattr(args, "outdir", "."),
        port=getattr(args, "port", 5000),
        app_name=getattr(args, "app_name", "app"),
        app_type=AppType(getattr(args, "app_type", "crud")),
        domain=getattr(args, "domain", ""),
        user_population=getattr(args, "user_population", ""),
        template=TemplateChoice(getattr(args, "template", "standard")),
        verify=getattr(args, "verify", True),
        verbose=getattr(args, "verbose", False),
        include_tests=getattr(args, "include_tests", False),
        include_docker=getattr(args, "include_docker", False),
        include_security_scan=getattr(args, "include_security_scan", False),
        models_json=models,
        routes_json=routes,
    )

    # Ideation-only mode
    if getattr(args, "ideate", False):
        spec = SpecBuilder.from_template(config.template.value, config)
        _print_ideation_results(spec)
        return 0

    # HTML-only mode — no Flask required
    if getattr(args, "html_only", False):
        from jugeo.webapp.generation.html_generator import HTMLOnlyGenerator
        from jugeo.webapp.generation.scaffold import AppScaffolder

        scaffolder = AppScaffolder()
        domain = getattr(args, "domain", "") or getattr(args, "app_name", "app")
        html_spec = scaffolder.scaffold_html_from_description(
            config.app_name, domain,
        )
        html_spec.port = config.port
        generator = HTMLOnlyGenerator()
        result = generator.generate(html_spec, config.outdir)
        print()
        print("+" + "-" * 58 + "+")
        print("|" + " HTML-ONLY GENERATION COMPLETE".center(58) + "|")
        print("+" + "-" * 58 + "+")
        print(f"  Output:  {result.output_dir}")
        print(f"  Files:   {len(result.files_created)}")
        print(f"  Lines:   {result.total_lines}")
        print()
        for f in result.files_created:
            rel = os.path.relpath(f, result.output_dir)
            print(f"    {rel}")
        print()
        print(f"  Serve:   cd {result.output_dir} && ./serve.sh")
        print(f"  Or:      python3 -m http.server {html_spec.port}")
        print()
        return 0

    # Full pipeline
    pipeline = WebappPipeline()
    result = pipeline.run(config)

    if result.generation_result:
        _print_generation_summary(result.generation_result)
    if result.verification_result:
        _print_verification_report(result.verification_result)
    if result.descent_result and result.descent_result.get("violations"):
        _print_descent_check(result.descent_result)

    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Standalone entry point (for ``jugeo-webapp`` pip script)
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> None:
    """Entry point for the ``jugeo-webapp`` console script."""
    parser = argparse.ArgumentParser(
        prog="jugeo-webapp",
        description="Sheaf-theoretic Flask app synthesis, verification, and ideation",
    )
    # Re-use the same argument registration
    _add_webapp_arguments(parser)
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args.func = run_webapp_command
    raise SystemExit(run_webapp_command(args))


def _add_webapp_arguments(parser: argparse.ArgumentParser) -> None:
    """Add all webapp arguments to *parser* (shared by subcommand & standalone)."""
    parser.add_argument("--outdir", required=False, default=".",
                        help="Output directory for generated Flask app")
    parser.add_argument("--port", type=int, default=5000,
                        help="Flask port (default: 5000)")
    parser.add_argument("--name", default="app",
                        help="Application name")
    parser.add_argument("--type", dest="app_type", default="crud",
                        choices=["crud", "api", "dashboard", "form", "custom"],
                        help="Application type")
    parser.add_argument("--domain", default="",
                        help="Target domain for ideation")
    parser.add_argument("--users", default="",
                        help="Target user population")
    parser.add_argument("--template", default="standard",
                        choices=["minimal", "standard", "full"],
                        help="Template level")
    parser.add_argument("--verify", action="store_true", default=True,
                        help="Run verification (default)")
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--with-tests", dest="include_tests",
                        action="store_true", default=True)
    parser.add_argument("--no-tests", dest="include_tests",
                        action="store_false")
    parser.add_argument("--with-docker", dest="include_docker",
                        action="store_true", default=False)
    parser.add_argument("--with-security", dest="include_security",
                        action="store_true", default=True)
    parser.add_argument("--no-security", dest="include_security",
                        action="store_false")
    parser.add_argument("--models", default="",
                        help="JSON model definitions (string or file path)")
    parser.add_argument("--routes", default="",
                        help="JSON route definitions (string or file path)")
    parser.add_argument("--ideate", action="store_true", default=False,
                        help="Run ideation pipeline only")
    parser.add_argument("--html-only", dest="html_only", action="store_true",
                        default=False,
                        help="Generate an HTML-only app (no Flask required)")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
