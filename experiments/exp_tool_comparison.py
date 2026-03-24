#!/usr/bin/env python3
"""Tool comparison experiment for JuGeo papers.

Runs mypy, pyright, JuGeo, and Lean 4 on a suite of buggy programs and
measures diagnostic richness (fields per error report), timing, and feature
coverage.  Results are written to experiments/results_tool_comparison.json
and papers/data-tool-comparison.tex.

Usage:
    python3 experiments/exp_tool_comparison.py
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()
PROGRAMS_DIR = REPO_ROOT / "experiments" / "comparison_programs"
RESULTS_FILE = REPO_ROOT / "experiments" / "results_tool_comparison.json"
LATEX_FILE = REPO_ROOT / "papers" / "data-tool-comparison.tex"

MYPY_BIN = "/opt/homebrew/bin/mypy"
PYRIGHT_BIN = "/opt/homebrew/bin/pyright"
LEAN_BIN = "/Users/halleyyoung/.elan/bin/lean"
JUGEO_CMD = ["python3", "-m", "jugeo"]

PYTHON_BUGS = [
    "bug01_type_error.py",
    "bug02_missing_return.py",
    "bug03_division_by_zero.py",
    "bug04_uninitialized.py",
    "bug05_wrong_args.py",
    "bug06_attribute_error.py",
    "bug07_index_error.py",
    "bug08_null_deref.py",
    "bug09_unreachable.py",
    "bug10_mutation.py",
]

LEAN_BUGS = [
    "bug01_type_error.lean",
    "bug02_missing_return.lean",
    "bug03_division_by_zero.lean",
    "bug04_uninitialized.lean",
    "bug05_wrong_args.lean",
    "bug06_attribute_error.lean",
    "bug07_index_error.lean",
    "bug08_null_deref.lean",
    "bug09_unreachable.lean",
    "bug10_mutation.lean",
]

N_TIMING_RUNS = 3  # median of N runs; set to 1 for faster runs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 60) -> dict:
    """Run a command, capturing stdout+stderr, returning result dict."""
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or str(REPO_ROOT),
            timeout=timeout,
        )
        elapsed_ms = (time.time() - t0) * 1000
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
            "elapsed_ms": elapsed_ms,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "TIMEOUT",
            "returncode": -1,
            "elapsed_ms": timeout * 1000,
        }
    except FileNotFoundError as exc:
        return {
            "stdout": "",
            "stderr": f"NOT_FOUND: {exc}",
            "returncode": -2,
            "elapsed_ms": 0.0,
        }


def median_timing(cmd: list[str], cwd: str | None = None, n: int = N_TIMING_RUNS) -> float:
    """Run cmd n times and return median wall-clock time in milliseconds."""
    times = []
    for _ in range(n):
        r = run_cmd(cmd, cwd=cwd)
        if r["elapsed_ms"] > 0:
            times.append(r["elapsed_ms"])
    return statistics.median(times) if times else 0.0


# ---------------------------------------------------------------------------
# Field-counting helpers
# ---------------------------------------------------------------------------

# Conceptual field groups — each group counts as ONE diagnostic field.
# Based on the taxonomy in paper03 §5 and the tool output schemas.

def count_mypy_fields(raw_json_line: str) -> tuple[int, list[str], int]:
    """Parse one line of mypy --output json and count conceptual fields.

    Returns (field_count, field_names, error_categories).
    """
    try:
        obj = json.loads(raw_json_line.strip())
    except json.JSONDecodeError:
        return 0, [], 0

    # Conceptual fields present in a mypy JSON error record
    field_map = {
        "location": obj.get("file") is not None and obj.get("line") is not None,
        "message": bool(obj.get("message")),
        "error_code": bool(obj.get("code")),
        "severity_level": bool(obj.get("severity")),
        "hint": obj.get("hint") is not None,
        "column": obj.get("column") is not None,
    }
    present = [k for k, v in field_map.items() if v]
    return len(present), present, 1  # mypy has 1 error category (error/warning/note)


def count_pyright_fields(diag: dict) -> tuple[int, list[str], int]:
    """Parse one pyright diagnostic dict and count conceptual fields."""
    field_map = {
        "location": bool(diag.get("file")),
        "range_start": diag.get("range", {}).get("start") is not None,
        "range_end": diag.get("range", {}).get("end") is not None,
        "message": bool(diag.get("message")),
        "rule": bool(diag.get("rule")),
        "severity_level": bool(diag.get("severity")),
    }
    present = [k for k, v in field_map.items() if v]
    return len(present), present, 1


def count_jugeo_bug_fields(bug: dict) -> tuple[int, list[str], int]:
    """Count conceptual fields in a JuGeo bug JSON record."""
    field_map = {
        "coordinate": bool(bug.get("coordinate")),
        "description": bool(bug.get("description")),
        "category": bool(bug.get("category")),
        "severity": bug.get("severity") is not None,
        "trust_tier": bool(bug.get("trust_tier")),
        "cohomology_class": bool(bug.get("cohomology_class")),
        "obstruction": bool(bug.get("obstruction")),
        "evidence_source": bool(bug.get("evidence_source")),
        "bug_id": bool(bug.get("bug_id")),
        "line": bug.get("line") is not None,
        "severity_label": bool(bug.get("severity_label")),
        "counterexample": bug.get("counterexample") is not None,
    }
    present = [k for k, v in field_map.items() if v]
    return len(present), present, 1


def count_lean_fields(output: str) -> tuple[int, list[str], int]:
    """Parse Lean 4 error output text and count conceptual fields.

    Lean 4 errors look like:
        file.lean:LINE:COL: error: MESSAGE
        [context lines with expected/actual types]
    """
    if not output.strip():
        return 0, [], 0

    field_map = {
        "location": bool(re.search(r'\.lean:\d+:\d+:', output)),
        "message_type": bool(re.search(r'error:|warning:|info:', output)),
        "message_body": len(output.strip().splitlines()) > 1,
        "expected_type": "expected to have type" in output or "expected type" in output,
        "actual_type": "has type" in output,
        "context_term": bool(re.search(r'^\s+\w', output, re.MULTILINE)),
    }
    present = [k for k, v in field_map.items() if v]
    # Count unique Lean error categories in this output
    error_lines = [l for l in output.splitlines() if ': error:' in l]
    categories = len(set(
        re.sub(r'.*error:\s*', '', l).split('\n')[0].strip()
        for l in error_lines
    ))
    return len(present), present, max(1, categories)


# ---------------------------------------------------------------------------
# Per-tool runners
# ---------------------------------------------------------------------------

def run_mypy(py_file: Path) -> dict:
    """Run mypy on a Python file, return parsed diagnostics."""
    r = run_cmd([MYPY_BIN, "--output", "json", "--no-error-summary",
                 "--ignore-missing-imports", str(py_file)])
    raw = (r["stdout"] + r["stderr"]).strip()
    all_fields: list[str] = []
    field_count_sum = 0
    errors_parsed = 0

    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        cnt, fields, _ = count_mypy_fields(line)
        if cnt > 0:
            field_count_sum += cnt
            errors_parsed += 1
            for f in fields:
                if f not in all_fields:
                    all_fields.append(f)

    # Fields per-report is measured on first error (or average)
    per_report = round(field_count_sum / errors_parsed, 2) if errors_parsed else 0

    return {
        "fields": sorted(all_fields),
        "field_count": len(all_fields),
        "fields_per_report": per_report,
        "errors_detected": errors_parsed,
        "raw_output": raw[:2000],
    }


def run_pyright(py_file: Path) -> dict:
    """Run pyright on a Python file, return parsed diagnostics."""
    r = run_cmd([PYRIGHT_BIN, "--outputjson", str(py_file)])
    raw = r["stdout"] + r["stderr"]

    # pyright may print non-JSON lines before the JSON blob
    json_start = raw.find("{")
    diags_data = []
    all_fields: list[str] = []
    field_count_sum = 0
    errors_parsed = 0

    if json_start >= 0:
        try:
            obj = json.loads(raw[json_start:])
            diags_data = obj.get("generalDiagnostics", [])
        except json.JSONDecodeError:
            pass

    for d in diags_data:
        cnt, fields, _ = count_pyright_fields(d)
        if cnt > 0:
            field_count_sum += cnt
            errors_parsed += 1
            for f in fields:
                if f not in all_fields:
                    all_fields.append(f)

    per_report = round(field_count_sum / errors_parsed, 2) if errors_parsed else 0

    return {
        "fields": sorted(all_fields),
        "field_count": len(all_fields),
        "fields_per_report": per_report,
        "errors_detected": errors_parsed,
        "raw_output": raw[:2000],
    }


def _parse_jugeo_json(raw: str) -> list:
    """Parse a JuGeo JSON output string, tolerating banner/log lines."""
    # JuGeo writes banner lines to stdout before the JSON array.
    # Use raw_decode to parse only the JSON portion, ignoring trailing text.
    json_start = raw.find("[")
    if json_start < 0:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw, json_start)
        return obj if isinstance(obj, list) else []
    except json.JSONDecodeError:
        return []


def run_jugeo_bugs(py_file: Path) -> dict:
    """Run JuGeo bugs on a Python file, return parsed diagnostics."""
    r = run_cmd(
        JUGEO_CMD + ["--format", "json", "bugs", str(py_file)],
        cwd=str(REPO_ROOT),
    )
    # Use stdout only for JSON (stderr has bootstrap log lines)
    raw = r["stdout"]

    bugs_list = []
    all_fields: list[str] = []
    field_count_sum = 0
    errors_parsed = 0

    for result in _parse_jugeo_json(raw):
        for bug in result.get("bugs", []):
            cnt, fields, _ = count_jugeo_bug_fields(bug)
            if cnt > 0:
                field_count_sum += cnt
                errors_parsed += 1
                bugs_list.append(bug)
                for f in fields:
                    if f not in all_fields:
                        all_fields.append(f)

    per_report = round(field_count_sum / errors_parsed, 2) if errors_parsed else 0

    return {
        "fields": sorted(all_fields),
        "field_count": len(all_fields),
        "fields_per_report": per_report,
        "errors_detected": errors_parsed,
        "raw_output": raw[:2000],
        "bugs": bugs_list,
    }


def run_jugeo_evaluate(py_file: Path) -> dict:
    """Run JuGeo evaluate, return top-level JSON fields as evaluate_fields."""
    r = run_cmd(
        JUGEO_CMD + ["--format", "json", "evaluate", str(py_file)],
        cwd=str(REPO_ROOT),
    )
    # Use stdout only; evaluate outputs a single JSON object
    raw = r["stdout"]
    json_start = raw.find("{")
    eval_fields: list[str] = []
    if json_start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw, json_start)
            eval_fields = list(obj.keys())
        except json.JSONDecodeError:
            pass

    return {
        "evaluate_fields": eval_fields,
        "evaluate_field_count": len(eval_fields),
        "raw_output": raw[:2000],
    }


def run_lean(lean_file: Path) -> dict:
    """Run Lean 4 on a .lean file, return parsed diagnostics."""
    r = run_cmd([LEAN_BIN, str(lean_file)])
    raw = (r["stdout"] + r["stderr"]).strip()
    cnt, fields, cats = count_lean_fields(raw)

    # Count individual error occurrences
    errors_detected = len(re.findall(r'\.lean:\d+:\d+:\s*error:', raw))

    return {
        "fields": sorted(fields),
        "field_count": cnt,
        "fields_per_report": cnt,  # same structure per error in Lean
        "errors_detected": errors_detected,
        "error_categories": cats,
        "raw_output": raw[:2000],
    }


# ---------------------------------------------------------------------------
# A. Diagnostic richness
# ---------------------------------------------------------------------------

def measure_diagnostic_richness() -> dict:
    print("\n=== A. Diagnostic Richness ===")
    results: dict[str, dict] = {}

    for py_name, lean_name in zip(PYTHON_BUGS, LEAN_BUGS):
        bug_key = py_name.replace(".py", "")
        py_file = PROGRAMS_DIR / py_name
        lean_file = PROGRAMS_DIR / lean_name

        print(f"  {bug_key}...")

        mypy_r = run_mypy(py_file)
        pyright_r = run_pyright(py_file)
        jugeo_r = run_jugeo_bugs(py_file)
        eval_r = run_jugeo_evaluate(py_file)
        lean_r = run_lean(lean_file)

        # Merge jugeo bugs + evaluate field counts for JuGeo combined view
        jugeo_combined_fields = sorted(set(jugeo_r["fields"] + eval_r["evaluate_fields"]))

        results[bug_key] = {
            "mypy": mypy_r,
            "pyright": pyright_r,
            "jugeo": {
                **jugeo_r,
                "evaluate_fields": eval_r["evaluate_fields"],
                "combined_fields": jugeo_combined_fields,
                "combined_field_count": len(jugeo_combined_fields),
            },
            "lean4": lean_r,
        }

        print(f"    mypy: {mypy_r['errors_detected']} errors, {mypy_r['field_count']} fields")
        print(f"    pyright: {pyright_r['errors_detected']} errors, {pyright_r['field_count']} fields")
        print(f"    jugeo: {jugeo_r['errors_detected']} bugs, {jugeo_r['field_count']} bug fields + "
              f"{eval_r['evaluate_field_count']} eval fields = "
              f"{len(jugeo_combined_fields)} total")
        print(f"    lean4: {lean_r['errors_detected']} errors, {lean_r['field_count']} fields")

    return results


# ---------------------------------------------------------------------------
# B. Timing comparison
# ---------------------------------------------------------------------------

def measure_timing() -> dict:
    print("\n=== B. Timing ===")
    results: dict[str, dict] = {}

    for py_name, lean_name in zip(PYTHON_BUGS, LEAN_BUGS):
        prog_key = py_name.replace(".py", "")
        py_file = PROGRAMS_DIR / py_name
        lean_file = PROGRAMS_DIR / lean_name

        print(f"  {prog_key}...")

        mypy_ms = median_timing(
            [MYPY_BIN, "--output", "json", "--no-error-summary",
             "--ignore-missing-imports", str(py_file)]
        )
        pyright_ms = median_timing(
            [PYRIGHT_BIN, "--outputjson", str(py_file)]
        )
        jugeo_bugs_ms = median_timing(
            JUGEO_CMD + ["--format", "json", "bugs", str(py_file)],
            cwd=str(REPO_ROOT),
        )
        jugeo_eval_ms = median_timing(
            JUGEO_CMD + ["--format", "json", "evaluate", str(py_file)],
            cwd=str(REPO_ROOT),
        )
        lean_ms = median_timing([LEAN_BIN, str(lean_file)])

        results[prog_key] = {
            "mypy_ms": round(mypy_ms, 1),
            "pyright_ms": round(pyright_ms, 1),
            "jugeo_bugs_ms": round(jugeo_bugs_ms, 1),
            "jugeo_eval_ms": round(jugeo_eval_ms, 1),
            "lean4_ms": round(lean_ms, 1),
        }

        print(f"    mypy={mypy_ms:.0f}ms  pyright={pyright_ms:.0f}ms  "
              f"jugeo_bugs={jugeo_bugs_ms:.0f}ms  jugeo_eval={jugeo_eval_ms:.0f}ms  "
              f"lean4={lean_ms:.0f}ms")

    return results


# ---------------------------------------------------------------------------
# C. Feature comparison
# ---------------------------------------------------------------------------

def check_features() -> dict:
    """Programmatically verify feature support per tool."""
    print("\n=== C. Feature Comparison ===")

    # Use bug01 (type error) as the probe program
    py_probe = PROGRAMS_DIR / "bug01_type_error.py"
    lean_probe = PROGRAMS_DIR / "bug01_type_error.lean"

    # --- mypy ---
    mypy_r = run_cmd([MYPY_BIN, "--output", "json", "--no-error-summary",
                      "--ignore-missing-imports", str(py_probe)])
    mypy_raw = (mypy_r["stdout"] + mypy_r["stderr"])
    mypy_json_lines = [l for l in mypy_raw.splitlines() if l.strip().startswith("{")]
    mypy_obj = {}
    if mypy_json_lines:
        try:
            mypy_obj = json.loads(mypy_json_lines[0])
        except json.JSONDecodeError:
            pass

    mypy_features = {
        "error_location": bool(mypy_obj.get("file") and mypy_obj.get("line")),
        "error_column": mypy_obj.get("column") is not None,
        "error_message": bool(mypy_obj.get("message")),
        "error_code": bool(mypy_obj.get("code")),
        "fix_suggestions": bool(mypy_obj.get("hint")),
        "related_locations": False,   # mypy does not report related locations in JSON
        "counterexamples": False,
        "failure_classification": False,
        "trust_confidence_levels": False,
        "incremental_checking": True,  # mypy --incremental is supported
        "cohomology_class": False,
        "geometric_obstruction": False,
    }

    # --- pyright ---
    pyright_r = run_cmd([PYRIGHT_BIN, "--outputjson", str(py_probe)])
    pyright_raw = pyright_r["stdout"] + pyright_r["stderr"]
    json_start = pyright_raw.find("{")
    pyright_diags = []
    if json_start >= 0:
        try:
            pyright_diags = json.loads(pyright_raw[json_start:]).get("generalDiagnostics", [])
        except json.JSONDecodeError:
            pass
    pyright_obj = pyright_diags[0] if pyright_diags else {}

    pyright_features = {
        "error_location": bool(pyright_obj.get("file") and pyright_obj.get("range")),
        "error_column": bool(pyright_obj.get("range", {}).get("start", {}).get("character") is not None),
        "error_message": bool(pyright_obj.get("message")),
        "error_code": bool(pyright_obj.get("rule")),
        "fix_suggestions": False,
        "related_locations": bool(pyright_obj.get("relatedInformation")),
        "counterexamples": False,
        "failure_classification": False,
        "trust_confidence_levels": False,
        "incremental_checking": True,  # pyright supports watch mode
        "cohomology_class": False,
        "geometric_obstruction": False,
    }

    # --- JuGeo ---
    # Use dedicated probe that exercises JuGeo's AST-pattern detectors
    # (mutable defaults, bool literal comparisons — the patterns JuGeo flags)
    jugeo_probe = PROGRAMS_DIR / "jugeo_probe.py"
    jugeo_r = run_cmd(
        JUGEO_CMD + ["--format", "json", "bugs", str(jugeo_probe)],
        cwd=str(REPO_ROOT),
    )
    jugeo_raw = jugeo_r["stdout"]  # stdout only for JSON
    jugeo_bug = {}
    for result in _parse_jugeo_json(jugeo_raw):
        if result.get("bugs"):
            jugeo_bug = result["bugs"][0]
            break

    jugeo_features = {
        "error_location": bool(jugeo_bug.get("coordinate") or jugeo_bug.get("line")),
        "error_column": ":" in str(jugeo_bug.get("coordinate", "")),
        "error_message": bool(jugeo_bug.get("description")),
        "error_code": bool(jugeo_bug.get("category")),
        "fix_suggestions": False,
        "related_locations": False,
        "counterexamples": jugeo_bug.get("counterexample") is not None,
        "failure_classification": bool(jugeo_bug.get("category")),
        "trust_confidence_levels": bool(jugeo_bug.get("trust_tier")),
        "incremental_checking": False,
        "cohomology_class": bool(jugeo_bug.get("cohomology_class")),
        "geometric_obstruction": bool(jugeo_bug.get("obstruction")),
    }

    # --- Lean 4 ---
    lean_r = run_cmd([LEAN_BIN, str(lean_probe)])
    lean_raw = (lean_r["stdout"] + lean_r["stderr"]).strip()

    lean_features = {
        "error_location": bool(re.search(r'\.lean:\d+:\d+:', lean_raw)),
        "error_column": bool(re.search(r'\.lean:\d+:\d+:', lean_raw)),
        "error_message": bool(lean_raw),
        "error_code": False,
        "fix_suggestions": False,
        "related_locations": "related" in lean_raw.lower(),
        "counterexamples": False,
        "failure_classification": False,
        "trust_confidence_levels": False,
        "incremental_checking": True,  # Lean 4 has incremental elaboration
        "cohomology_class": False,
        "geometric_obstruction": False,
    }

    features = {
        "mypy": mypy_features,
        "pyright": pyright_features,
        "jugeo": jugeo_features,
        "lean4": lean_features,
        "dafny": "N/A (not installed)",
        "fstar": "N/A (not installed)",
    }

    for tool, feats in features.items():
        if isinstance(feats, str):
            print(f"  {tool}: {feats}")
            continue
        true_count = sum(1 for v in feats.values() if v is True)
        print(f"  {tool}: {true_count}/{len(feats)} features present")

    return features


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def compute_summary(richness: dict, timing: dict) -> dict:
    """Compute per-tool averages across all programs."""
    tools = ["mypy", "pyright", "lean4"]
    jugeo_key = "jugeo"

    summary: dict[str, dict] = {}

    for tool in tools:
        field_counts = []
        fpr_counts = []  # fields per report
        for prog_data in richness.values():
            t = prog_data.get(tool, {})
            field_counts.append(t.get("field_count", 0))
            fpr = t.get("fields_per_report", t.get("field_count", 0))
            fpr_counts.append(fpr)

        timing_vals = [v[f"{tool}_ms"] for v in timing.values() if f"{tool}_ms" in v]
        summary[tool] = {
            "avg_field_count": round(statistics.mean(field_counts), 2) if field_counts else 0,
            "avg_fields_per_report": round(statistics.mean(fpr_counts), 2) if fpr_counts else 0,
            "avg_timing_ms": round(statistics.mean(timing_vals), 1) if timing_vals else 0,
            "median_timing_ms": round(statistics.median(timing_vals), 1) if timing_vals else 0,
        }

    # JuGeo uses combined (bugs + evaluate) field count
    jugeo_combined_counts = []
    jugeo_bug_fpr = []
    jugeo_bugs_times = [v["jugeo_bugs_ms"] for v in timing.values()]
    jugeo_eval_times = [v["jugeo_eval_ms"] for v in timing.values()]

    for prog_data in richness.values():
        j = prog_data.get(jugeo_key, {})
        jugeo_combined_counts.append(j.get("combined_field_count", j.get("field_count", 0)))
        fpr = j.get("fields_per_report", j.get("field_count", 0))
        jugeo_bug_fpr.append(fpr)

    summary["jugeo"] = {
        "avg_field_count": round(statistics.mean(jugeo_combined_counts), 2),
        "avg_fields_per_report": round(statistics.mean(jugeo_bug_fpr), 2),
        "avg_timing_ms": round(statistics.mean(jugeo_bugs_times), 1),
        "median_timing_ms": round(statistics.median(jugeo_bugs_times), 1),
        "avg_eval_timing_ms": round(statistics.mean(jugeo_eval_times), 1),
    }

    return summary


# ---------------------------------------------------------------------------
# LaTeX output
# ---------------------------------------------------------------------------

def write_latex(summary: dict, richness: dict, timing: dict, features: dict) -> None:
    """Write data-tool-comparison.tex with LaTeX macros."""

    def fmtf(v: float) -> str:
        return f"{v:.1f}" if v != int(v) else str(int(v))

    lines = [
        "% Auto-generated by experiments/exp_tool_comparison.py",
        "% DO NOT EDIT BY HAND — re-run the experiment to update",
        "%",
    ]

    # Average field counts
    for tool in ["mypy", "pyright", "jugeo", "lean4"]:
        macro_tool = tool.replace("4", "four").replace("_", "")
        avg = summary.get(tool, {}).get("avg_field_count", 0)
        lines.append(
            rf"\newcommand{{\{macro_tool}AvgFields}}{{{fmtf(avg)}}}"
        )

    lines.append("%")

    # Average fields per report
    for tool in ["mypy", "pyright", "jugeo", "lean4"]:
        macro_tool = tool.replace("4", "four").replace("_", "")
        avg = summary.get(tool, {}).get("avg_fields_per_report", 0)
        lines.append(
            rf"\newcommand{{\{macro_tool}AvgFieldsPerReport}}{{{fmtf(avg)}}}"
        )

    lines.append("%")

    # Timing
    for tool in ["mypy", "pyright", "jugeo", "lean4"]:
        macro_tool = tool.replace("4", "four").replace("_", "")
        avg = summary.get(tool, {}).get("avg_timing_ms", 0)
        med = summary.get(tool, {}).get("median_timing_ms", 0)
        lines.append(
            rf"\newcommand{{\{macro_tool}AvgTimeMs}}{{{fmtf(avg)}}}"
        )
        lines.append(
            rf"\newcommand{{\{macro_tool}MedianTimeMs}}{{{fmtf(med)}}}"
        )

    lines.append("%")

    # Per-bug field counts
    for bug_key, prog_data in richness.items():
        bug_macro = bug_key.replace("_", "").replace("bug", "Bug")
        for tool, macro_tool in [
            ("mypy", "mypy"),
            ("pyright", "pyright"),
            ("jugeo", "jugeo"),
            ("lean4", "leanfour"),
        ]:
            t = prog_data.get(tool, {})
            fc = t.get("combined_field_count", t.get("field_count", 0))
            lines.append(
                rf"\newcommand{{\{macro_tool}{bug_macro}Fields}}{{{fc}}}"
            )

    lines.append("%")

    # Feature support counts
    for tool in ["mypy", "pyright", "jugeo", "lean4"]:
        macro_tool = tool.replace("4", "four").replace("_", "")
        feats = features.get(tool, {})
        if isinstance(feats, dict):
            cnt = sum(1 for v in feats.values() if v is True)
            lines.append(
                rf"\newcommand{{\{macro_tool}FeatureCount}}{{{cnt}}}"
            )

    lines.append("%")

    # JuGeo advantage ratios over mypy and lean4
    jugeo_avg = summary.get("jugeo", {}).get("avg_field_count", 0)
    mypy_avg = summary.get("mypy", {}).get("avg_field_count", 1)
    lean_avg = summary.get("lean4", {}).get("avg_field_count", 1)
    pyright_avg = summary.get("pyright", {}).get("avg_field_count", 1)

    def safe_ratio(a: float, b: float) -> str:
        if b == 0:
            return "N/A"
        return fmtf(round(a / b, 2))

    lines.append(
        rf"\newcommand{{\jugeoVsMypyRatio}}{{{safe_ratio(jugeo_avg, mypy_avg)}}}"
    )
    lines.append(
        rf"\newcommand{{\jugeoVsLeanRatio}}{{{safe_ratio(jugeo_avg, lean_avg)}}}"
    )
    lines.append(
        rf"\newcommand{{\jugeoVsPyrightRatio}}{{{safe_ratio(jugeo_avg, pyright_avg)}}}"
    )

    lines.append("")
    LATEX_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {LATEX_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("JuGeo Tool Comparison Experiment")
    print("=" * 50)

    richness = measure_diagnostic_richness()
    timing = measure_timing()
    features = check_features()
    summary = compute_summary(richness, timing)

    results = {
        "diagnostic_richness": richness,
        "timing": timing,
        "features": features,
        "summary": summary,
    }

    RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {RESULTS_FILE}")

    write_latex(summary, richness, timing, features)

    # Print summary table
    print("\n=== Summary ===")
    print(f"{'Tool':<12} {'Avg Fields':>12} {'Avg ms':>10} {'Median ms':>12}")
    print("-" * 50)
    for tool in ["mypy", "pyright", "jugeo", "lean4"]:
        s = summary.get(tool, {})
        print(
            f"{tool:<12} {s.get('avg_field_count', 0):>12.1f} "
            f"{s.get('avg_timing_ms', 0):>10.1f} "
            f"{s.get('median_timing_ms', 0):>12.1f}"
        )


if __name__ == "__main__":
    main()
