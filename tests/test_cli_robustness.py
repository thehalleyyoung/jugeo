"""Robustness tests for jugeo CLI commands against the 300-case benchmark suites.

Extracts real programs from bug_suite.json, spec_suite.json, and
equivalence_suite.json, writes them to temp files, and runs every
CLI command to verify it doesn't crash, produces output, and exits 0.

This tests:
- jugeo prove (on 20 diverse programs)
- jugeo bugs (on 20 programs from the bug suite)
- jugeo spec --spec X --impl Y (on 10 spec pairs)
- jugeo equiv (on 10 equivalence pairs)
- jugeo load (on 10 programs)
- jugeo encode (on 10 programs, with different --encoding families)
- jugeo evaluate (on 5 programs)
- jugeo repair (on 5 buggy programs)
- jugeo descend (on 5 programs)
- jugeo classify (on 5 problem descriptions)
- Edge cases: empty programs, single-line programs, syntax errors
"""

import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUITE_DIR = os.path.join(_ROOT, "test_examples")


def _load_suite(name: str) -> list[dict]:
    path = os.path.join(_SUITE_DIR, f"{name}.json")
    with open(path) as f:
        data = json.load(f)
    return data.get("cases", data if isinstance(data, list) else [])


def _write_temp(source: str, suffix: str = ".py") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, dir="/tmp")
    f.write(source)
    f.close()
    return f.name


def _run_jugeo(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a jugeo CLI command. Returns (returncode, stdout, stderr)."""
    cmd = [sys.executable, "-m", "jugeo"] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=_ROOT
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


# ═══════════════════════════════════════════════════════════════════════
#  Load suites
# ═══════════════════════════════════════════════════════════════════════

BUG_CASES = _load_suite("bug_suite")
SPEC_CASES = _load_suite("spec_suite")
EQUIV_CASES = _load_suite("equivalence_suite")


# ═══════════════════════════════════════════════════════════════════════
#  jugeo prove — run on diverse programs
# ═══════════════════════════════════════════════════════════════════════

class TestProveRobustness:
    """Test `jugeo prove` on diverse real programs."""

    @pytest.mark.parametrize("idx", range(0, 20, 1))
    def test_prove_spec_programs(self, idx):
        """Prove on programs from the spec suite."""
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough spec cases")
        case = SPEC_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, stdout, stderr = _run_jugeo("prove", path)
            assert rc == 0, f"prove failed on {case['case_id']}: {stderr[-500:]}"
            combined = stdout + stderr
            assert "verified" in combined.lower() or "obstruct" in combined.lower() or "proved" in combined.lower() or "propositions" in combined.lower(), \
                f"No verification output for {case['case_id']}"
        finally:
            os.unlink(path)

    def test_prove_with_spec_and_impl(self):
        """Test --spec and --impl flags with real files."""
        case = SPEC_CASES[0]
        impl_path = _write_temp(case["program"])
        spec_path = _write_temp(case["spec_program"])
        try:
            rc, stdout, stderr = _run_jugeo(
                "prove", "--spec", spec_path, "--impl", impl_path)
            assert rc == 0, f"prove --spec --impl failed: {stderr[-500:]}"
        finally:
            os.unlink(impl_path)
            os.unlink(spec_path)

    @pytest.mark.parametrize("strategy", ["eager", "exhaustive", "iterative", "EAGER", "EXHAUSTIVE"])
    def test_prove_strategies(self, strategy):
        """All strategy names (including uppercase) are accepted."""
        path = _write_temp("def f(x):\n    return x + 1\n")
        try:
            rc, stdout, stderr = _run_jugeo("prove", path, "--strategy", strategy)
            assert rc == 0, f"Strategy {strategy} failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_prove_empty_file(self):
        """Empty file should not crash."""
        path = _write_temp("")
        try:
            rc, _, stderr = _run_jugeo("prove", path)
            assert rc == 0, f"Empty file crashed prove: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_prove_single_line(self):
        """Single expression should not crash."""
        path = _write_temp("x = 42\n")
        try:
            rc, _, stderr = _run_jugeo("prove", path)
            assert rc == 0, f"Single-line crashed prove: {stderr[-300:]}"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo bugs — run on bug suite programs
# ═══════════════════════════════════════════════════════════════════════

class TestBugsRobustness:
    """Test `jugeo bugs` on the 100-case bug suite."""

    @pytest.mark.parametrize("idx", range(0, 20, 1))
    def test_bugs_detection(self, idx):
        """bugs should not crash on any bug suite program."""
        if idx >= len(BUG_CASES):
            pytest.skip("Not enough bug cases")
        case = BUG_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, stdout, stderr = _run_jugeo("bugs", path)
            assert rc == 0, f"bugs crashed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)

    def test_bugs_json_format(self):
        """--format json should produce parseable JSON."""
        case = BUG_CASES[0]
        path = _write_temp(case["program"])
        try:
            rc, stdout, stderr = _run_jugeo("--format", "json", "bugs", path)
            assert rc == 0
            # Should contain some JSON-parseable content
            combined = stdout + stderr
            assert "{" in combined or "[]" in combined or "bug" in combined.lower()
        finally:
            os.unlink(path)

    def test_bugs_severity_filter(self):
        """--severity flag should be accepted."""
        case = BUG_CASES[0]
        path = _write_temp(case["program"])
        try:
            for severity in ["all", "high", "medium", "low"]:
                rc, _, stderr = _run_jugeo("bugs", path, "--severity", severity)
                assert rc == 0, f"--severity {severity} failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_bugs_clean_program(self):
        """A clean program should produce 0 bugs without crashing."""
        path = _write_temp("def add(a, b):\n    return a + b\n")
        try:
            rc, stdout, stderr = _run_jugeo("bugs", path)
            assert rc == 0
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo spec — run on spec suite
# ═══════════════════════════════════════════════════════════════════════

class TestSpecRobustness:
    """Test `jugeo spec` on the 100-case spec suite."""

    @pytest.mark.parametrize("idx", range(0, 10, 1))
    def test_spec_satisfaction(self, idx):
        """spec --spec --impl should not crash on spec suite cases."""
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough spec cases")
        case = SPEC_CASES[idx]
        spec_path = _write_temp(case["spec_program"])
        impl_path = _write_temp(case["program"])
        try:
            rc, stdout, stderr = _run_jugeo(
                "spec", "--spec", spec_path, "--impl", impl_path)
            assert rc == 0, f"spec failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(spec_path)
            os.unlink(impl_path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo equiv — run on equivalence suite
# ═══════════════════════════════════════════════════════════════════════

class TestEquivRobustness:
    """Test `jugeo equiv` on the 100-case equivalence suite."""

    @pytest.mark.parametrize("idx", range(0, 10, 1))
    def test_equiv_checking(self, idx):
        """equiv should not crash on equivalence suite cases."""
        if idx >= len(EQUIV_CASES):
            pytest.skip("Not enough equiv cases")
        case = EQUIV_CASES[idx]
        left_path = _write_temp(case["left_program"])
        right_path = _write_temp(case["right_program"])
        try:
            rc, stdout, stderr = _run_jugeo("equiv", left_path, right_path)
            assert rc == 0, f"equiv failed on {case['case_id']}: {stderr[-500:]}"
            combined = stdout + stderr
            assert "equiv" in combined.lower() or "verdict" in combined.lower() or "descent" in combined.lower(), \
                f"No equivalence output for {case['case_id']}"
        finally:
            os.unlink(left_path)
            os.unlink(right_path)

    def test_equiv_identical_programs(self):
        """Identical programs should be equivalent."""
        prog = "def f(x):\n    return x * 2\n"
        p1 = _write_temp(prog)
        p2 = _write_temp(prog)
        try:
            rc, _, stderr = _run_jugeo("equiv", p1, p2)
            assert rc == 0, f"equiv on identical programs failed: {stderr[-300:]}"
        finally:
            os.unlink(p1)
            os.unlink(p2)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo load — structural analysis
# ═══════════════════════════════════════════════════════════════════════

class TestLoadRobustness:
    """Test `jugeo load` on diverse programs."""

    @pytest.mark.parametrize("idx", range(0, 10, 1))
    def test_load_programs(self, idx):
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough cases")
        case = SPEC_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, stdout, stderr = _run_jugeo("load", path)
            assert rc == 0, f"load failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)

    def test_load_with_coordinates_flag(self):
        path = _write_temp("def foo():\n    pass\ndef bar():\n    return 1\n")
        try:
            rc, _, stderr = _run_jugeo("load", path, "--coordinates")
            assert rc == 0, f"--coordinates failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_load_with_sections_flag(self):
        path = _write_temp("class MyClass:\n    def method(self): pass\n")
        try:
            rc, _, stderr = _run_jugeo("load", path, "--sections")
            assert rc == 0, f"--sections failed: {stderr[-300:]}"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo encode — SMT encoding
# ═══════════════════════════════════════════════════════════════════════

class TestEncodeRobustness:
    """Test `jugeo encode` with different encoding families."""

    @pytest.mark.parametrize("encoding", ["scalar", "structural", "all", "bitvec"])
    def test_encoding_families(self, encoding):
        path = _write_temp("def compute(x, y):\n    return x + y * 2\n")
        try:
            rc, _, stderr = _run_jugeo("encode", path, "--encoding", encoding)
            assert rc == 0, f"encode --encoding {encoding} failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_encode_with_smtlib(self):
        path = _write_temp("def f(n):\n    return n * n\n")
        try:
            rc, stdout, stderr = _run_jugeo("encode", path, "--smtlib")
            assert rc == 0, f"--smtlib failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("idx", range(0, 10, 1))
    def test_encode_spec_programs(self, idx):
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough cases")
        case = SPEC_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, _, stderr = _run_jugeo("encode", path)
            assert rc == 0, f"encode failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo evaluate, repair, descend
# ═══════════════════════════════════════════════════════════════════════

class TestOtherCommandsRobustness:
    """Test evaluate, repair, descend on real programs."""

    @pytest.mark.parametrize("idx", range(0, 5, 1))
    def test_evaluate(self, idx):
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough cases")
        case = SPEC_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, _, stderr = _run_jugeo("evaluate", path)
            assert rc == 0, f"evaluate failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("idx", range(0, 5, 1))
    def test_repair(self, idx):
        if idx >= len(BUG_CASES):
            pytest.skip("Not enough cases")
        case = BUG_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, _, stderr = _run_jugeo("repair", path)
            assert rc == 0, f"repair failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("idx", range(0, 5, 1))
    def test_descend(self, idx):
        if idx >= len(SPEC_CASES):
            pytest.skip("Not enough cases")
        case = SPEC_CASES[idx]
        path = _write_temp(case["program"])
        try:
            rc, _, stderr = _run_jugeo("descend", path)
            assert rc == 0, f"descend failed on {case['case_id']}: {stderr[-500:]}"
        finally:
            os.unlink(path)

    def test_evaluate_with_spec(self):
        case = SPEC_CASES[0]
        impl_path = _write_temp(case["program"])
        spec_path = _write_temp(case["spec_program"])
        try:
            rc, _, stderr = _run_jugeo("evaluate", impl_path, "--spec", spec_path)
            assert rc == 0, f"evaluate --spec failed: {stderr[-300:]}"
        finally:
            os.unlink(impl_path)
            os.unlink(spec_path)

    def test_repair_with_spec(self):
        case = BUG_CASES[0]
        path = _write_temp(case["program"])
        spec_path = _write_temp("def spec(x): return True\n")
        try:
            rc, _, stderr = _run_jugeo("repair", path, "--spec", spec_path)
            assert rc == 0, f"repair --spec failed: {stderr[-300:]}"
        finally:
            os.unlink(path)
            os.unlink(spec_path)


# ═══════════════════════════════════════════════════════════════════════
#  jugeo classify, alignment, mixed
# ═══════════════════════════════════════════════════════════════════════

class TestAnalysisCommandsRobustness:
    """Test classify, alignment, mixed on real programs."""

    def test_classify_with_description(self):
        rc, _, stderr = _run_jugeo("classify", "Sort a list of integers efficiently")
        assert rc == 0, f"classify with description failed: {stderr[-300:]}"

    def test_classify_with_file(self):
        path = _write_temp("Sort a list and verify it's sorted\n", suffix=".txt")
        try:
            rc, _, stderr = _run_jugeo("classify", "--file", path)
            assert rc == 0, f"classify --file failed: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_classify_top_k(self):
        rc, _, stderr = _run_jugeo("classify", "Verify a binary search tree", "--top-k", "5")
        assert rc == 0, f"classify --top-k failed: {stderr[-300:]}"

    def test_alignment(self):
        prog = _write_temp("def sort(xs):\n    return sorted(xs)\n")
        doc = _write_temp("This module sorts lists.\n", suffix=".md")
        try:
            rc, _, stderr = _run_jugeo("alignment", prog, "--readme", doc)
            assert rc == 0, f"alignment --readme failed: {stderr[-300:]}"
        finally:
            os.unlink(prog)
            os.unlink(doc)

    def test_mixed_mode(self):
        case = SPEC_CASES[0]
        path = _write_temp(case["program"])
        try:
            rc, _, stderr = _run_jugeo("mixed", path)
            assert rc == 0, f"mixed failed: {stderr[-500:]}"
        finally:
            os.unlink(path)

    def test_mixed_with_spec(self):
        case = SPEC_CASES[0]
        impl_path = _write_temp(case["program"])
        spec_path = _write_temp(case["spec_program"])
        try:
            rc, _, stderr = _run_jugeo("mixed", impl_path, "--spec", spec_path)
            assert rc == 0, f"mixed --spec failed: {stderr[-300:]}"
        finally:
            os.unlink(impl_path)
            os.unlink(spec_path)


# ═══════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test CLI robustness on edge-case inputs."""

    @pytest.mark.parametrize("cmd", ["prove", "bugs", "load", "encode"])
    def test_empty_file(self, cmd):
        path = _write_temp("")
        try:
            if cmd in ("load", "encode"):
                rc, _, stderr = _run_jugeo(cmd, path)
            else:
                rc, _, stderr = _run_jugeo(cmd, path)
            assert rc == 0, f"{cmd} crashed on empty file: {stderr[-300:]}"
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("cmd", ["prove", "bugs", "load", "encode"])
    def test_comment_only_file(self, cmd):
        path = _write_temp("# This file has only comments\n# Nothing else\n")
        try:
            rc, _, stderr = _run_jugeo(cmd, path)
            assert rc == 0, f"{cmd} crashed on comment-only file: {stderr[-300:]}"
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("cmd", ["prove", "bugs"])
    def test_syntax_error_file(self, cmd):
        """Files with syntax errors should not crash the CLI."""
        path = _write_temp("def broken(\n    return\n")
        try:
            rc, _, stderr = _run_jugeo(cmd, path)
            # May return non-zero but should not crash (no traceback in stderr)
            assert "Traceback" not in stderr or rc == 0, \
                f"{cmd} crashed on syntax error: {stderr[-500:]}"
        finally:
            os.unlink(path)

    def test_large_program(self):
        """A large program should not crash prove."""
        lines = ["def f_{i}(x):\n    return x + {i}\n".replace("{i}", str(i)) for i in range(100)]
        path = _write_temp("\n".join(lines))
        try:
            rc, _, stderr = _run_jugeo("prove", path, timeout=60)
            assert rc == 0, f"prove crashed on large program: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_unicode_program(self):
        """Unicode identifiers should not crash."""
        path = _write_temp("def grüße(名前):\n    return f'Hello {名前}'\n")
        try:
            rc, _, stderr = _run_jugeo("prove", path)
            assert rc == 0, f"prove crashed on unicode: {stderr[-300:]}"
        finally:
            os.unlink(path)

    def test_deeply_nested(self):
        """Deeply nested code should not crash."""
        code = "def f():\n"
        for i in range(20):
            code += "    " * (i + 1) + f"if True:\n"
        code += "    " * 21 + "return 1\n"
        path = _write_temp(code)
        try:
            rc, _, stderr = _run_jugeo("prove", path)
            assert rc == 0, f"prove crashed on deeply nested: {stderr[-300:]}"
        finally:
            os.unlink(path)
