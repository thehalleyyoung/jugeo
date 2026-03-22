"""jugeo.cli.main — comprehensive CLI entry point for jugeo.

Top-level interface:
    jugeo <command> [options]

Commands:
  foundation    Run the full synthesis pipeline (fields → tournament → code → textbook)
  bugs          Find bugs in Python code using semantic analysis
  equiv         Check if two programs are semantically equivalent
  spec          Check if code adheres to a specification
  repair        Suggest repairs for buggy / non-conforming code
  mixed         Mixed-mode: simultaneously check bugs + spec + equiv
  evaluate      Evaluate code quality, maturity, trust level
  generate      Generate code from specs, judgments, or blueprints
  run           Run a jugeo judgment pipeline from a config file
  server        Start jugeo as an HTTP API server
  load          Load & analyze a Python program into judgment sections
  encode        Encode a Python program to Z3/SMT representation
  classify      Classify a problem via the problem atlas
  alignment     Check public-facing documentation for honest projection
  info          Show package info, loaded packs, trust tiers
  test          Run the jugeo test suite / benchmarks
  prove         Full sheaf-theoretic program verification
  descend       Run descent/gluing on judgment data

Design notes:
  - All heavy imports are done lazily inside each command function so that
    ``jugeo --help`` always works even when optional dependencies are absent.
  - Every command returns an integer exit code (0 on success, non-zero on failure).
  - Commands fall back gracefully when optional subsystems are unavailable.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import pathlib
import subprocess
import sys
import time
import traceback
from typing import Any

try:
    from jugeo.cli import __version__
except ImportError:
    __version__ = "0.1.0"

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_output_dir(output_arg: str | None, prefix: str = "foundation") -> pathlib.Path:
    if output_arg is not None:
        return pathlib.Path(output_arg).resolve()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return pathlib.Path.cwd() / "outputs" / f"{prefix}_{timestamp}"


def _open_directory(path: pathlib.Path) -> None:
    import platform
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(path)])
    except Exception as exc:
        _log.warning("Failed to open output directory: %s", exc)


def _safe_import(module_path: str, attr: str | None = None) -> Any:
    """Lazily import a module/attribute, returning None on failure."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        if attr:
            return getattr(mod, attr, None)
        return mod
    except Exception:
        return None


def _print_banner(title: str, items: dict[str, str]) -> None:
    print(f"\n{'='*60}")
    print(f"  jugeo — {title}")
    print(f"{'='*60}")
    for k, v in items.items():
        print(f"  {k}: {v}")
    print(f"{'='*60}\n")


# ===========================================================================
# Subcommand: foundation (the original --orchestrate --ideate --foundation)
# ===========================================================================

def _cmd_foundation(args: argparse.Namespace) -> int:
    output_dir = _resolve_output_dir(args.output, "foundation")

    try:
        from jugeo.cli.foundation_pipeline import FoundationPipeline
    except ImportError as exc:
        print(f"[jugeo] ERROR: Cannot load FoundationPipeline: {exc}", file=sys.stderr)
        return 2

    pool_size = int(args.n_fields * args.entropy_factor)
    judge = "heuristic" if args.no_llm else args.model
    _print_banner("Foundational Mathematics Synthesis", {
        "Mode": "foundation (orchestrate + ideate + foundation)",
        "Fields": f"{args.n_fields}  (pool x{args.entropy_factor} = {pool_size} candidates)",
        "Rounds": f"{args.rounds}   Judge: {judge}",
        "Output": str(output_dir),
    })

    t0 = time.perf_counter()
    try:
        # Build a namespace that FoundationPipeline expects
        pipeline = FoundationPipeline(args=args, output_dir=output_dir)
        result = pipeline.run()
    except KeyboardInterrupt:
        print("\n[jugeo] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        _log.exception("Pipeline failed: %s", exc)
        print(f"\n[jugeo] Pipeline failed: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - t0
    print(f"\n{'='*60}")
    print("  Run complete")
    print(f"{'='*60}")
    print(f"  Duration:   {elapsed:.1f}s")
    print(f"  Winner:     {getattr(result, 'winner_name', 'unknown')}")
    if getattr(result, "textbook_path", None):
        print(f"  Textbook:   {result.textbook_path}")
    if getattr(result, "code_files", None):
        print(f"  Code files: {len(result.code_files)} file(s) in {output_dir / 'src'}")
        for f_path in result.code_files:
            print(f"    - {f_path}")
    print(f"  Output dir: {output_dir}")
    print(f"{'='*60}\n")

    if args.open_output:
        _open_directory(output_dir)
    return 0


# ===========================================================================
# Subcommand: bugs
# ===========================================================================

def _cmd_bugs(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_bugs import run_bugs
    return run_bugs(args)


# ===========================================================================
# Subcommand: spec
# ===========================================================================

def _cmd_spec(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_spec import run_spec
    return run_spec(args)


# ===========================================================================
# Subcommand: repair
# ===========================================================================

def _cmd_repair(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_repair import run_repair
    return run_repair(args)


# ===========================================================================
# Subcommand: equiv
# ===========================================================================

def _cmd_equiv(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_equiv import run_equiv
    return run_equiv(args)


# ===========================================================================
# Subcommand: evaluate
# ===========================================================================

def _cmd_evaluate(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_evaluate import run_evaluate
    return run_evaluate(args)


# ===========================================================================
# Subcommand: generate
# ===========================================================================

def _cmd_generate(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_generate import run_generate
    return run_generate(args)


# ===========================================================================
# Subcommand: run
# ===========================================================================

def _cmd_run(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_run import run_pipeline
    return run_pipeline(args)


# ===========================================================================
# Subcommand: server
# ===========================================================================

def _cmd_server(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_server import run_server
    return run_server(args)


# ===========================================================================
# Subcommand: load
# ===========================================================================

def _cmd_load(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_load import run_load
    return run_load(args)


# ===========================================================================
# Subcommand: encode
# ===========================================================================

def _cmd_encode(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_encode import run_encode
    return run_encode(args)


# ===========================================================================
# Subcommand: classify
# ===========================================================================

def _cmd_classify(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_classify import run_classify
    return run_classify(args)


# ===========================================================================
# Subcommand: alignment
# ===========================================================================

def _cmd_alignment(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_alignment import run_alignment
    return run_alignment(args)


# ===========================================================================
# Subcommand: mixed
# ===========================================================================

def _cmd_mixed(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_mixed import run_mixed
    return run_mixed(args)


# ===========================================================================
# Subcommand: info
# ===========================================================================

def _cmd_info(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_info import run_info
    return run_info(args)


# ===========================================================================
# Subcommand: test
# ===========================================================================

def _cmd_test(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_test import run_test
    return run_test(args)


# ===========================================================================
# Subcommand: ideate
# ===========================================================================

def _cmd_ideate(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_ideate import run_ideate
    return run_ideate(args)


# ===========================================================================
# Subcommand: prove
# ===========================================================================

def _cmd_prove(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_prove import run_prove
    return run_prove(args)


# ===========================================================================
# Subcommand: descend
# ===========================================================================

def _cmd_descend(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_descend import run_descend
    return run_descend(args)


# ===========================================================================
# Subcommand: orchestrate
# ===========================================================================

def _cmd_orchestrate(args: argparse.Namespace) -> int:
    from jugeo.cli.cmd_orchestrate import run
    return run(args)


# ===========================================================================
# Argument parser — subcommand architecture
# ===========================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jugeo",
        description="JuGeo — Judgment Geometry verification & synthesis engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  jugeo foundation --rounds 6 --n-fields 48\n"
            "  jugeo bugs program.py --format json\n"
            "  jugeo equiv left.py right.py\n"
            "  jugeo spec spec.txt program.py\n"
            "  jugeo evaluate program.py --ablation\n"
            "  jugeo info --packs --maturity\n"
            "  jugeo server --port 8080\n"
            "\n"
            "Legacy mode (still supported):\n"
            "  jugeo --orchestrate --ideate --foundation\n"
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", default=False, help="Verbose output.")
    parser.add_argument("--version", action="version", version=f"jugeo {__version__}")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text).")
    parser.add_argument("--output", "-o", type=str, default=None, metavar="DIR", help="Output directory.")
    parser.add_argument("--no-llm", action="store_true", default=False, help="Skip LLM calls; use heuristic fallbacks.")
    parser.add_argument("--model", type=str, default="claude-sonnet-4.6", metavar="STR", help="LLM model slug.")

    # Legacy flags (kept for backward compat: jugeo --orchestrate --ideate --foundation)
    parser.add_argument("--orchestrate", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--ideate", action="store_true", default=False, help=argparse.SUPPRESS)

    subs = parser.add_subparsers(dest="command", help="Available commands")

    # -- foundation --
    p_found = subs.add_parser("foundation", help="Run the full synthesis pipeline (tournament → code → textbook).",
                               aliases=["synthesize"])
    p_found.add_argument("--rounds", type=int, default=6, metavar="INT", help="Tournament rounds (default: 6).")
    p_found.add_argument("--n-fields", type=int, default=3, metavar="INT", help="Bracket size (default: 3).")
    p_found.add_argument("--entropy-factor", type=float, default=2.5, metavar="FLOAT",
                          help="Pool multiplier (default: 2.5).")
    p_found.add_argument("--seed", type=int, default=None, metavar="INT", help="Random seed.")
    p_found.add_argument("--open", action="store_true", default=False, dest="open_output", help="Open output dir.")
    p_found.add_argument("--execute-code", action="store_true", default=False, help="Generate code files.")
    p_found.add_argument("--latex-only", action="store_true", default=False, help="Only produce LaTeX textbook.")
    p_found.set_defaults(func=_cmd_foundation, orchestrate=True, ideate=True, foundation=True)

    # -- bugs --
    p_bugs = subs.add_parser("bugs", help="Detect bugs in Python source files.")
    p_bugs.add_argument("files", nargs="*", help="Python file(s) to analyze.")
    p_bugs.add_argument("--severity", choices=["all", "high", "medium", "low"], default="all",
                         help="Filter by minimum severity.")
    p_bugs.add_argument("--registry", action="store_true", default=False,
                         help="List all registered bug-detection classes.")
    p_bugs.set_defaults(func=_cmd_bugs)

    # -- spec --
    p_spec = subs.add_parser("spec", help="Check code against a specification.")
    p_spec.add_argument("spec_file", nargs="?", default=None, help="Specification file (text or JSON).")
    p_spec.add_argument("program", nargs="?", default=None, help="Python program to verify.")
    p_spec.add_argument("--strict", action="store_true", default=False,
                         help="Fail on any residual obligation.")
    p_spec.add_argument("--registry", action="store_true", default=False,
                         help="List all registered specification-satisfaction classes.")
    p_spec.set_defaults(func=_cmd_spec)

    # -- repair --
    p_repair = subs.add_parser("repair", help="Suggest repairs for buggy code.")
    p_repair.add_argument("files", nargs="*", help="Python file(s) to repair.")
    p_repair.add_argument("--max-repairs", type=int, default=5, metavar="N",
                           help="Maximum repairs to suggest (default: 5).")
    p_repair.add_argument("--auto-apply", action="store_true", default=False,
                           help="Automatically apply top-ranked repair.")
    p_repair.add_argument("--registry", action="store_true", default=False,
                           help="List all registered repair-semantics classes.")
    p_repair.set_defaults(func=_cmd_repair)

    # -- equiv --
    p_equiv = subs.add_parser("equiv", help="Check semantic equivalence of two programs.")
    p_equiv.add_argument("left", nargs="?", default=None, help="First program.")
    p_equiv.add_argument("right", nargs="?", default=None, help="Second program.")
    p_equiv.add_argument("--direction", choices=["both", "left-to-right", "right-to-left"],
                          default="both", help="Refinement direction (default: both).")
    p_equiv.add_argument("--registry", action="store_true", default=False,
                          help="List all registered relational-refinement classes.")
    p_equiv.set_defaults(func=_cmd_equiv)

    # -- evaluate --
    p_eval = subs.add_parser("evaluate", help="Evaluate code quality, maturity, and trust.")
    p_eval.add_argument("target", nargs="?", default=".", help="File or directory to evaluate.")
    p_eval.add_argument("--ablation", action="store_true", default=False, help="Run ablation study.")
    p_eval.add_argument("--calibration", action="store_true", default=False, help="Run calibration metrics.")
    p_eval.add_argument("--benchmarks", action="store_true", default=False, help="Run benchmark suite.")
    p_eval.add_argument("--registry", action="store_true", default=False,
                         help="List all registered evaluation classes.")
    p_eval.set_defaults(func=_cmd_evaluate)

    # -- generate --
    p_gen = subs.add_parser("generate", help="Generate code from synthesis results or novel problems.")
    p_gen.add_argument("--from-synthesis", type=str, default=None, metavar="DIR",
                        help="Generate from a previous synthesis output directory.")
    p_gen.add_argument("--novel-problems", action="store_true", default=False,
                        help="Identify and codify novel (previously inarticulable) problems.")
    p_gen.add_argument("--goal", type=str, default=None, help="High-level generation goal description.")
    p_gen.add_argument("--strategy", choices=["cover_design", "inhabitant_fleet", "full_pipeline"],
                        default="full_pipeline",
                        help="Generation strategy: cover_design, inhabitant_fleet, or full_pipeline (default).")
    p_gen.set_defaults(func=_cmd_generate)

    # -- run --
    p_run = subs.add_parser("run", help="Run a jugeo pipeline from a config file.")
    p_run.add_argument("config", help="JSON or YAML config file path, or a Python file when using --compose.")
    p_run.add_argument("--dry-run", action="store_true", default=False, help="Validate config without executing.")
    p_run.add_argument("--orchestrate", action="store_true", default=False,
                        help="Show rich orchestration output using Fleet, Frontier, and MoveGenerator.")
    p_run.add_argument("--compose", action="store_true", default=False,
                        help="Run the full cross-command composed pipeline "
                             "(load→encode→prove→bugs→spec→repair→generate→evaluate→classify→alignment) "
                             "with feedback loop.")
    p_run.set_defaults(func=_cmd_run)

    # -- server --
    p_srv = subs.add_parser("server", help="Start jugeo as an HTTP API server.")
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    p_srv.add_argument("--port", type=int, default=8741, help="Bind port (default: 8741).")
    p_srv.add_argument("--registry", action="store_true", default=False,
                        help="List all registered interface classes.")
    p_srv.set_defaults(func=_cmd_server)

    # -- load --
    p_load = subs.add_parser("load", help="Load & analyze a Python program into judgment sections.")
    p_load.add_argument("files", nargs="+", help="Python file(s) to load.")
    p_load.add_argument("--coordinates", action="store_true", default=False,
                         help="Show coordinate map of the program.")
    p_load.add_argument("--sections", action="store_true", default=False,
                         help="Show judgment sections.")
    p_load.add_argument("--deep", action="store_true", default=False,
                         help="Show rich program analysis using python_runtime classes.")
    p_load.set_defaults(func=_cmd_load)

    # -- encode --
    p_enc = subs.add_parser("encode", help="Encode a Python program to Z3/SMT representation.")
    p_enc.add_argument("files", nargs="+", help="Python file(s) to encode.")
    p_enc.add_argument("--encoding", choices=["scalar", "structural", "tensor", "sequence", "text", "all"],
                        default="all", help="Encoding family (default: all).")
    p_enc.add_argument("--emit-smt2", action="store_true", default=False, help="Emit SMT-LIB2 file.")
    p_enc.add_argument("--encoding-families", action="store_true", default=False,
                        help="Show rich multi-family encoding analysis across all encoding sub-packages.")
    p_enc.set_defaults(func=_cmd_encode)

    # -- classify --
    p_cls = subs.add_parser("classify", help="Classify a problem via the problem atlas.")
    p_cls.add_argument("description", nargs="?", default=None, help="Problem description (text).")
    p_cls.add_argument("--file", type=str, default=None, help="Read problem from file.")
    p_cls.add_argument("--registry", action="store_true", default=False,
                        help="List all registered problem-atlas classes.")
    p_cls.set_defaults(func=_cmd_classify)

    # -- alignment --
    p_align = subs.add_parser("alignment", help="Check public documentation for honest projection.")
    p_align.add_argument("program", nargs="?", default=None, help="Program or internal claim source.")
    p_align.add_argument("--docs", type=str, default=None, help="Documentation file to check against.")
    p_align.add_argument("--registry", action="store_true", default=False,
                          help="List all registered public-alignment classes.")
    p_align.set_defaults(func=_cmd_alignment)

    # -- mixed --
    p_mix = subs.add_parser("mixed", help="Mixed-mode: bugs + spec + equiv simultaneously.")
    p_mix.add_argument("files", nargs="+", help="Python file(s) to analyze.")
    p_mix.add_argument("--spec", type=str, default=None, help="Optional spec file.")
    p_mix.add_argument("--routing", action="store_true", default=False,
                        help="Show rich mixed-evidence routing output with channels and negotiation.")
    p_mix.set_defaults(func=_cmd_mixed)

    # -- info --
    p_info = subs.add_parser("info", help="Show package info, packs, trust tiers, maturity.")
    p_info.add_argument("--packs", action="store_true", default=False, help="List loaded domain packs.")
    p_info.add_argument("--maturity", action="store_true", default=False, help="Show maturity level.")
    p_info.add_argument("--thesis", action="store_true", default=False, help="Show thesis claims & contributions.")
    p_info.add_argument("--kernel", action="store_true", default=False, help="Show kernel lifecycle state.")
    p_info.add_argument("--all", action="store_true", default=False, dest="show_all",
                         help="Show everything.")
    p_info.add_argument("--registry", action="store_true", default=False,
                         help="List all registered info classes (thesis/maturity/kernel/packs/interfaces/benchmarks/runtime).")
    p_info.set_defaults(func=_cmd_info)

    # -- test --
    p_test = subs.add_parser("test", help="Run the jugeo test / benchmark suite.")
    p_test.add_argument("--suite", choices=["all", "site", "descent", "trust",
                                            "judgment", "encode", "bugs", "spec",
                                            "equiv", "unit"],
                         default="all", help="Test suite to run (default: all).")
    p_test.add_argument("--registry", action="store_true", default=False,
                         help="List all registered benchmark classes.")
    p_test.set_defaults(func=_cmd_test)

    # -- prove --
    sub_prove = subs.add_parser("prove", help="Full sheaf-theoretic program verification.")
    sub_prove.add_argument("files", nargs="*", help="Python file(s) to verify.")
    sub_prove.add_argument("--trust-floor", choices=["unverified", "copilot", "solver", "proven"],
                           default="copilot")
    sub_prove.add_argument("--max-depth", type=int, default=5)
    sub_prove.add_argument("--strategy", choices=["eager", "exhaustive", "iterative"],
                           default="eager")
    sub_prove.add_argument("--registry", action="store_true",
                           help="Show all available foundations classes.")
    sub_prove.set_defaults(func=_cmd_prove)

    # -- descend --
    sub_descend = subs.add_parser("descend", help="Run descent/gluing on judgment data.")
    sub_descend.add_argument("input", help="JSON judgment data or Python file.")
    sub_descend.add_argument("--strategy", choices=["eager", "exhaustive", "iterative", "optimistic"],
                             default="eager")
    sub_descend.add_argument("--max-depth", type=int, default=5)
    sub_descend.add_argument("--visualize", action="store_true")
    sub_descend.add_argument("--trust-floor", choices=["unverified", "copilot", "solver", "proven"],
                             default="unverified")
    sub_descend.set_defaults(func=_cmd_descend)

    # -- ideate --
    sub_ideate = subs.add_parser("ideate", help="Mathematical ideation and theorem discovery.")
    sub_ideate.add_argument("--fields", action="store_true", help="List available mathematical fields.")
    sub_ideate.add_argument("--discover", action="store_true", help="Run theorem discovery pipeline.")
    sub_ideate.add_argument("--economics", action="store_true", help="Show theorem yield economics.")
    sub_ideate.add_argument("--cross-domain", action="store_true", help="Run cross-domain synthesis.")
    sub_ideate.add_argument("--registry", action="store_true",
                            help="Show all available ideation classes.")
    sub_ideate.add_argument("--field", type=str, default=None, metavar="FIELD",
                            help="Run theorem ecology for a mathematical field (e.g. algebra, topology).")
    sub_ideate.add_argument("--analogy", type=str, default=None, metavar="SPEC",
                            help='Run analogy transport between domains (e.g. "topology → verification").')
    sub_ideate.add_argument("--discover-engine", action="store_true",
                            help="Run the discovery engine pipeline for idea generation.")
    sub_ideate.add_argument("--theorem-economics", action="store_true",
                            help="Run theorem economics portfolio analysis.")
    sub_ideate.set_defaults(func=_cmd_ideate)

    # -- catalog --
    from jugeo.cli import cmd_catalog
    cmd_catalog.add_subparser(subs)

    # -- orchestrate --
    from jugeo.cli import cmd_orchestrate
    p_orch = cmd_orchestrate.add_subparser(subs)
    p_orch.set_defaults(func=_cmd_orchestrate)

    return parser


# ===========================================================================
# main
# ===========================================================================

def _is_legacy_mode(argv: list[str]) -> bool:
    """Detect legacy invocation: jugeo --orchestrate --ideate --foundation [opts]."""
    return "--orchestrate" in argv and "--ideate" in argv

def _rewrite_legacy_argv(argv: list[str]) -> list[str]:
    """Rewrite legacy flags into subcommand form.

    ``jugeo --orchestrate --ideate --foundation --rounds 2 --no-llm``
    becomes ``jugeo foundation --rounds 2 --no-llm``

    Global flags (--verbose, --no-llm, --model, --format, --output) must
    appear *before* the subcommand for argparse to pick them up.
    """
    skip_flags = {"--orchestrate", "--ideate", "--foundation"}
    global_flags = {"--verbose", "-v", "--no-llm", "--version"}
    global_with_arg = {"--model", "--format", "--output", "-o"}

    before_sub = []  # global flags
    after_sub = []   # subcommand flags
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in skip_flags:
            i += 1
            continue
        if arg in global_flags:
            before_sub.append(arg)
            i += 1
        elif arg in global_with_arg:
            before_sub.append(arg)
            if i + 1 < len(argv):
                i += 1
                before_sub.append(argv[i])
            i += 1
        else:
            after_sub.append(arg)
            i += 1
    return before_sub + ["foundation"] + after_sub


def _bootstrap_check(verbose: bool = False):
    """Attempt to bootstrap all JuGeo subsystems, returning the controller or None."""
    try:
        from jugeo.bootstrap import JuGeoBootstrap, SubsystemStatus, SubsystemName, SubsystemRecord
        from jugeo.errors import FailureChain, JudgmentError, DescentError, EncodingError, FailureFilter

        boot = JuGeoBootstrap()
        result = boot.initialize()

        healthy = 0
        total = 0
        for name in SubsystemName:
            total += 1
            rec = boot._registry.get(name.value)
            if rec is not None and rec.status == SubsystemStatus.HEALTHY:
                healthy += 1

        version = __version__
        print(f"JuGeo v{version} — {healthy} subsystems ready")

        if verbose:
            for name in SubsystemName:
                rec = boot._registry.get(name.value)
                if rec is not None:
                    status_str = rec.status if isinstance(rec.status, str) else rec.status.value
                    symbol = "✓" if rec.status == SubsystemStatus.HEALTHY else "✗"
                    print(f"  {symbol} {name.value:<16s} {status_str.lower()}")

        return boot
    except Exception:
        _log.debug("Bootstrap check unavailable", exc_info=True)
        return None


def main(argv: list[str] | None = None) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]

    # Legacy mode: jugeo --orchestrate --ideate --foundation [opts]
    if _is_legacy_mode(raw_argv):
        raw_argv = _rewrite_legacy_argv(raw_argv)

    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    _configure_logging(getattr(args, "verbose", False))

    if not args.command:
        parser.print_help()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0

    # Bootstrap subsystem check
    verbose = getattr(args, "verbose", False)
    _bootstrap_check(verbose=verbose)

    try:
        from jugeo.errors import JudgmentError, DescentError, EncodingError
    except ImportError:
        JudgmentError = DescentError = EncodingError = None

    try:
        return func(args)
    except KeyboardInterrupt:
        print("\n[jugeo] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Surface domain-specific error types with clearer messages
        if JudgmentError is not None and isinstance(exc, JudgmentError):
            print(f"\n[jugeo] Judgment error: {exc}", file=sys.stderr)
        elif DescentError is not None and isinstance(exc, DescentError):
            print(f"\n[jugeo] Descent error: {exc}", file=sys.stderr)
        elif EncodingError is not None and isinstance(exc, EncodingError):
            print(f"\n[jugeo] Encoding error: {exc}", file=sys.stderr)
        else:
            _log.exception("Command failed: %s", exc)
            print(f"\n[jugeo] Command failed: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
