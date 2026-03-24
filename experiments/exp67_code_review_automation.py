#!/usr/bin/env python3
"""Paper 67 Experiment — code review via executable JuGeo specifications.

This experiment now exercises JuGeo's real user-facing review surfaces:
    * jugeo.easy.spec  -> executable specification checking on a declared cover
    * jugeo.easy.bugs  -> existing static bug scan for structural issues

Each program is reviewed against a JuGeo-native spec payload containing:
    * an entrypoint to review
    * a spec(result, *args, **kwargs) oracle
    * a declared finite cover of witness inputs

Re-run: python3 experiments/exp67_code_review_automation.py
"""

from __future__ import annotations

import json
import statistics
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper67.tex"

sys.path.insert(0, str(ROOT / "src"))

from jugeo.easy import bugs as review_bugs  # noqa: E402
from jugeo.easy import spec as review_spec  # noqa: E402


def safe_mean(xs: list[float]) -> float:
    return round(statistics.mean(xs), 2) if xs else 0.0


def wrap_review_source(source: str, *, coordinate: str, solve_wrapper: str) -> str:
    return textwrap.dedent(source).strip() + "\n\n" + textwrap.dedent(
        f"""\
        def _paper67_review_coordinate():
            return {coordinate!r}

        {solve_wrapper.strip()}
        """
    )


def cover_points(*items: tuple[tuple, dict]) -> list[dict]:
    return [{"args": list(args), "kwargs": kwargs} for args, kwargs in items]


def runtime_spec(
    *,
    description: str,
    spec_program: str,
    input_cover: list[dict],
    entrypoint: str = "solve",
) -> dict:
    return {
        "description": description,
        "entrypoint": entrypoint,
        "spec_function": "spec",
        "spec_program": textwrap.dedent(spec_program).strip() + "\n",
        "input_cover": input_cover,
    }


CASES = [
    {
        "id": "factorial",
        "correct": True,
        "source": wrap_review_source(
            """\
            def factorial(n):
                if n < 0:
                    raise ValueError("negative")
                result = 1
                for i in range(2, n + 1):
                    result *= i
                return result
            """,
            coordinate="paper67.factorial",
            solve_wrapper="""
            def solve(n):
                return factorial(n)
            """,
        ),
        "spec": runtime_spec(
            description="factorial matches the mathematical product on the declared cover",
            spec_program="""
            def spec(result, n):
                expected = 1
                for i in range(2, n + 1):
                    expected *= i
                return result == expected
            """,
            input_cover=cover_points(
                ((0,), {}),
                ((1,), {}),
                ((2,), {}),
                ((3,), {}),
                ((4,), {}),
                ((5,), {}),
                ((6,), {}),
                ((7,), {}),
                ((8,), {}),
                ((9,), {}),
            ),
        ),
    },
    {
        "id": "binary_search",
        "correct": True,
        "source": wrap_review_source(
            """\
            def binary_search(arr, target):
                lo, hi = 0, len(arr) - 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if arr[mid] == target:
                        return mid
                    if arr[mid] < target:
                        lo = mid + 1
                    else:
                        hi = mid - 1
                return -1
            """,
            coordinate="paper67.binary_search",
            solve_wrapper="""
            def solve(arr, target):
                return binary_search(arr, target)
            """,
        ),
        "spec": runtime_spec(
            description="binary search returns the index or -1 on the declared finite cover",
            spec_program="""
            def spec(result, arr, target):
                expected = arr.index(target) if target in arr else -1
                return result == expected
            """,
            input_cover=cover_points(
                (([], 3), {}),
                (([1], 1), {}),
                (([1], 2), {}),
                (([1, 3, 5, 7], 1), {}),
                (([1, 3, 5, 7], 7), {}),
                (([1, 3, 5, 7], 4), {}),
                (([2, 4, 6, 8, 10], 8), {}),
                (([-5, -1, 0, 3, 9], -5), {}),
                (([-5, -1, 0, 3, 9], 9), {}),
                (([-5, -1, 0, 3, 9], 10), {}),
            ),
        ),
    },
    {
        "id": "merge",
        "correct": True,
        "source": wrap_review_source(
            """\
            def merge(left, right):
                result, i, j = [], 0, 0
                while i < len(left) and j < len(right):
                    if left[i] <= right[j]:
                        result.append(left[i])
                        i += 1
                    else:
                        result.append(right[j])
                        j += 1
                result.extend(left[i:])
                result.extend(right[j:])
                return result
            """,
            coordinate="paper67.merge",
            solve_wrapper="""
            def solve(left, right):
                return merge(left, right)
            """,
        ),
        "spec": runtime_spec(
            description="merge produces the fully merged sorted output",
            spec_program="""
            def spec(result, left, right):
                return result == sorted(list(left) + list(right))
            """,
            input_cover=cover_points(
                (([], []), {}),
                (([1], []), {}),
                (([], [2]), {}),
                (([1], [2]), {}),
                (([1, 3], [2, 4]), {}),
                (([-3, -1], [-2, 0, 5]), {}),
                (([1, 2, 9], [3, 4, 5]), {}),
                (([0, 2, 2], [1, 2, 3]), {}),
                (([5, 8], [6, 7, 9]), {}),
                (([-5, 0, 10], [-4, 3, 11]), {}),
            ),
        ),
    },
    {
        "id": "gcd",
        "correct": True,
        "source": wrap_review_source(
            """\
            def gcd(a, b):
                while b:
                    a, b = b, a % b
                return a
            """,
            coordinate="paper67.gcd",
            solve_wrapper="""
            def solve(a, b):
                return gcd(a, b)
            """,
        ),
        "spec": runtime_spec(
            description="gcd matches Euclid's algorithm on the declared cover",
            spec_program="""
            def _gcd(a, b):
                a = abs(a)
                b = abs(b)
                while b:
                    a, b = b, a % b
                return a

            def spec(result, a, b):
                return result == _gcd(a, b)
            """,
            input_cover=cover_points(
                ((0, 0), {}),
                ((1, 0), {}),
                ((0, 7), {}),
                ((12, 8), {}),
                ((18, 24), {}),
                ((81, 27), {}),
                ((49, 21), {}),
                ((270, 192), {}),
                ((-24, 18), {}),
                ((17, 13), {}),
            ),
        ),
    },
    {
        "id": "is_palindrome",
        "correct": True,
        "source": wrap_review_source(
            """\
            def is_palindrome(s):
                s = s.lower()
                return s == s[::-1]
            """,
            coordinate="paper67.is_palindrome",
            solve_wrapper="""
            def solve(s):
                return is_palindrome(s)
            """,
        ),
        "spec": runtime_spec(
            description="palindrome check is case-insensitive on the declared cover",
            spec_program="""
            def spec(result, s):
                normalized = s.lower()
                return result == (normalized == normalized[::-1])
            """,
            input_cover=cover_points(
                (("",), {}),
                (("a",), {}),
                (("Aa",), {}),
                (("RaceCar",), {}),
                (("Level",), {}),
                (("Python",), {}),
                (("Noon",), {}),
                (("AbBa",), {}),
                (("abc",), {}),
                (("Rotor",), {}),
            ),
        ),
    },
    {
        "id": "factorial_off_by_one",
        "correct": False,
        "source": wrap_review_source(
            """\
            def factorial(n):
                if n < 0:
                    raise ValueError("negative")
                result = 1
                for i in range(2, n):
                    result *= i
                return result
            """,
            coordinate="paper67.factorial_off_by_one",
            solve_wrapper="""
            def solve(n):
                return factorial(n)
            """,
        ),
        "spec": runtime_spec(
            description="factorial should include n in the product",
            spec_program="""
            def spec(result, n):
                expected = 1
                for i in range(2, n + 1):
                    expected *= i
                return result == expected
            """,
            input_cover=cover_points(
                ((0,), {}),
                ((1,), {}),
                ((2,), {}),
                ((3,), {}),
                ((4,), {}),
                ((5,), {}),
                ((6,), {}),
                ((7,), {}),
                ((8,), {}),
                ((9,), {}),
            ),
        ),
    },
    {
        "id": "binary_search_wrong_mid",
        "correct": False,
        "source": wrap_review_source(
            """\
            def binary_search(arr, target):
                lo, hi = 0, len(arr)
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if arr[mid] == target:
                        return mid
                    if arr[mid] < target:
                        lo = mid + 1
                    else:
                        hi = mid - 1
                return -1
            """,
            coordinate="paper67.binary_search_wrong_mid",
            solve_wrapper="""
            def solve(arr, target):
                return binary_search(arr, target)
            """,
        ),
        "spec": runtime_spec(
            description="binary search should not overrun the right bound",
            spec_program="""
            def spec(result, arr, target):
                expected = arr.index(target) if target in arr else -1
                return result == expected
            """,
            input_cover=cover_points(
                (([], 3), {}),
                (([1], 1), {}),
                (([1], 2), {}),
                (([1, 3, 5, 7], 1), {}),
                (([1, 3, 5, 7], 7), {}),
                (([1, 3, 5, 7], 4), {}),
                (([2, 4, 6, 8, 10], 8), {}),
                (([-5, -1, 0, 3, 9], -5), {}),
                (([-5, -1, 0, 3, 9], 9), {}),
                (([-5, -1, 0, 3, 9], 10), {}),
            ),
        ),
    },
    {
        "id": "merge_missing_extend",
        "correct": False,
        "source": wrap_review_source(
            """\
            def merge(left, right):
                result, i, j = [], 0, 0
                while i < len(left) and j < len(right):
                    if left[i] <= right[j]:
                        result.append(left[i])
                        i += 1
                    else:
                        result.append(right[j])
                        j += 1
                return result
            """,
            coordinate="paper67.merge_missing_extend",
            solve_wrapper="""
            def solve(left, right):
                return merge(left, right)
            """,
        ),
        "spec": runtime_spec(
            description="merge should retain the remaining suffixes",
            spec_program="""
            def spec(result, left, right):
                return result == sorted(list(left) + list(right))
            """,
            input_cover=cover_points(
                (([], []), {}),
                (([1], []), {}),
                (([], [2]), {}),
                (([1], [2]), {}),
                (([1, 3], [2, 4]), {}),
                (([-3, -1], [-2, 0, 5]), {}),
                (([1, 2, 9], [3, 4, 5]), {}),
                (([0, 2, 2], [1, 2, 3]), {}),
                (([5, 8], [6, 7, 9]), {}),
                (([-5, 0, 10], [-4, 3, 11]), {}),
            ),
        ),
    },
    {
        "id": "gcd_wrong_swap",
        "correct": False,
        "source": wrap_review_source(
            """\
            def gcd(a, b):
                while b:
                    a, b = a, a % b
                return a
            """,
            coordinate="paper67.gcd_wrong_swap",
            solve_wrapper="""
            def solve(a, b):
                return gcd(a, b)
            """,
        ),
        "spec": runtime_spec(
            description="gcd should swap to (b, a % b)",
            spec_program="""
            def _gcd(a, b):
                a = abs(a)
                b = abs(b)
                while b:
                    a, b = b, a % b
                return a

            def spec(result, a, b):
                return result == _gcd(a, b)
            """,
            input_cover=cover_points(
                ((0, 0), {}),
                ((1, 0), {}),
                ((0, 7), {}),
                ((12, 8), {}),
                ((18, 24), {}),
                ((81, 27), {}),
                ((49, 21), {}),
                ((270, 192), {}),
                ((-24, 18), {}),
                ((17, 13), {}),
            ),
        ),
    },
    {
        "id": "palindrome_no_lower",
        "correct": False,
        "source": wrap_review_source(
            """\
            def is_palindrome(s):
                return s == s[::-1]
            """,
            coordinate="paper67.palindrome_no_lower",
            solve_wrapper="""
            def solve(s):
                return is_palindrome(s)
            """,
        ),
        "spec": runtime_spec(
            description="palindrome review should include lowercase normalization",
            spec_program="""
            def spec(result, s):
                normalized = s.lower()
                return result == (normalized == normalized[::-1])
            """,
            input_cover=cover_points(
                (("",), {}),
                (("a",), {}),
                (("Aa",), {}),
                (("RaceCar",), {}),
                (("Level",), {}),
                (("Python",), {}),
                (("Noon",), {}),
                (("AbBa",), {}),
                (("abc",), {}),
                (("Rotor",), {}),
            ),
        ),
    },
    {
        "id": "divide_no_check",
        "correct": False,
        "source": wrap_review_source(
            """\
            def safe_divide(a, b):
                return a / b
            """,
            coordinate="paper67.divide_no_check",
            solve_wrapper="""
            def solve(a, b):
                return safe_divide(a, b)
            """,
        ),
        "spec": runtime_spec(
            description="safe_divide should guard zero divisors and return None",
            spec_program="""
            def spec(result, a, b):
                if b == 0:
                    return result is None
                return abs(result - (a / b)) < 1e-12
            """,
            input_cover=cover_points(
                ((1, 1), {}),
                ((4, 2), {}),
                ((9, 3), {}),
                ((5, 2), {}),
                ((0, 7), {}),
                ((8, -2), {}),
                ((3, 0), {}),
                ((-6, 3), {}),
                ((10, 5), {}),
                ((1, 0), {}),
            ),
        ),
    },
    {
        "id": "stack_wrong_pop",
        "correct": False,
        "source": wrap_review_source(
            """\
            class Stack:
                def __init__(self):
                    self.items = []

                def push(self, x):
                    self.items.append(x)

                def pop(self):
                    return self.items.pop(0)
            """,
            coordinate="paper67.stack_wrong_pop",
            solve_wrapper="""
            def solve(items):
                stack = Stack()
                for item in items:
                    stack.push(item)
                popped = []
                while stack.items:
                    popped.append(stack.pop())
                return popped
            """,
        ),
        "spec": runtime_spec(
            description="stack pop should be LIFO on the declared cover",
            spec_program="""
            def spec(result, items):
                return result == list(reversed(items))
            """,
            input_cover=cover_points(
                (([],), {}),
                (([1],), {}),
                (([1, 2],), {}),
                (([1, 2, 3],), {}),
                (([5, 8, 13],), {}),
                (([-1, 0, 1],), {}),
                (([7, 7, 7],), {}),
                (([3, 1, 4, 1],), {}),
                (([9, 8, 7, 6, 5],), {}),
                ((["a", "b", "c"],), {}),
            ),
        ),
    },
    {
        "id": "max_no_empty_check",
        "correct": False,
        "source": wrap_review_source(
            """\
            def find_max(lst):
                m = lst[0]
                for x in lst[1:]:
                    if x > m:
                        m = x
                return m
            """,
            coordinate="paper67.max_no_empty_check",
            solve_wrapper="""
            def solve(lst):
                return find_max(lst)
            """,
        ),
        "spec": runtime_spec(
            description="find_max should return None on empty lists",
            spec_program="""
            def spec(result, lst):
                if not lst:
                    return result is None
                return result == max(lst)
            """,
            input_cover=cover_points(
                (([],), {}),
                (([1],), {}),
                (([1, 2],), {}),
                (([2, 1],), {}),
                (([-3, -1, -5],), {}),
                (([10, 0, 3],), {}),
                (([7, 7, 7],), {}),
                (([5, 9, 2, 8],), {}),
                (([-10, 4, 0],), {}),
                (([42, -1],), {}),
            ),
        ),
    },
]


print("=" * 60)
print("Paper 67: Code Review Automation Experiments")
print("=" * 60)
print("Review surface: jugeo.easy.spec with declared finite covers")

correct_results: list[dict] = []
buggy_results: list[dict] = []

for case in CASES:
    bucket = correct_results if case["correct"] else buggy_results
    label = "Correct" if case["correct"] else "Buggy"
    print(f"\n  [{label}] {case['id']} ...", end=" ", flush=True)
    t0 = time.perf_counter()
    review = review_spec(case["source"], case["spec"])
    static_bugs = review_bugs(case["source"])
    elapsed = time.perf_counter() - t0

    props = len(review.clauses)
    ok_props = sum(1 for clause in review.clauses if clause.get("pass"))
    bug_count = len(static_bugs)
    obstruction_count = len(review.obstructions)
    detected = (not review.satisfied) or bug_count > 0 or obstruction_count > 0
    verdict = "verified" if review.satisfied else "obstructed"

    record = {
        "id": case["id"],
        "correct": case["correct"],
        "verdict": verdict,
        "props": props,
        "ok": ok_props,
        "bugs_reported": bug_count,
        "obstructions": obstruction_count,
        "witness_count": review.witness_count,
        "detected": detected,
        "mode": review.mode,
        "time_s": round(elapsed, 3),
        "trust_level": round(review.trust_level, 4),
        "residual_obligations": list(review.residual_obligations),
        "witnesses": list(review.witnesses),
        "review_obstructions": list(review.obstructions),
    }
    if case["correct"]:
        record["false_positive"] = detected
    bucket.append(record)

    outcome = "DETECTED" if detected and not case["correct"] else (
        "FALSE POSITIVE" if detected else "ok"
    )
    print(
        f"verdict={verdict} clauses={ok_props}/{props} "
        f"static_bugs={bug_count} obs={obstruction_count} {outcome} "
        f"t={elapsed:.2f}s"
    )


n_correct = len(correct_results)
n_buggy = len(buggy_results)
n_total = n_correct + n_buggy

fp_count = sum(1 for r in correct_results if r.get("false_positive", False))
detected_count = sum(1 for r in buggy_results if r.get("detected", False))
missed_count = n_buggy - detected_count

tp = detected_count
tn = n_correct - fp_count
fp = fp_count
fn = missed_count
precision = round(tp / max(tp + fp, 1), 4)
recall = round(tp / max(tp + fn, 1), 4)
f_one = round(2 * precision * recall / max(precision + recall, 0.0001), 4)
accuracy = round((tp + tn) / max(n_total, 1), 4)

correct_times = [r["time_s"] for r in correct_results]
buggy_times = [r["time_s"] for r in buggy_results]
all_times = correct_times + buggy_times

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper67.tex — AUTO-GENERATED by exp67_code_review_automation.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp67_code_review_automation.py",
    f"% Generated from {n_total} programs ({n_correct} correct, {n_buggy} buggy)",
    "% Review workflow: jugeo.easy.spec declared-cover witnesses + jugeo.easy.bugs",
    "",
    f"\\newcommand{{\\ppLXVIIprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXVIIcorrectCount}}{{{n_correct}}}",
    f"\\newcommand{{\\ppLXVIIbuggyCount}}{{{n_buggy}}}",
    "",
    f"\\newcommand{{\\ppLXVIItruePositive}}{{{tp}}}",
    f"\\newcommand{{\\ppLXVIItrueNegative}}{{{tn}}}",
    f"\\newcommand{{\\ppLXVIIfalsePositive}}{{{fp}}}",
    f"\\newcommand{{\\ppLXVIIfalseNegative}}{{{fn}}}",
    "",
    f"\\newcommand{{\\ppLXVIIprecision}}{{{precision}}}",
    f"\\newcommand{{\\ppLXVIIrecall}}{{{recall}}}",
    f"\\newcommand{{\\ppLXVIIfOne}}{{{f_one}}}",
    f"\\newcommand{{\\ppLXVIIaccuracy}}{{{accuracy}}}",
    f"\\newcommand{{\\ppLXVIIaccuracyPct}}{{{round(accuracy * 100, 1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVIIdetected}}{{{detected_count}}}",
    f"\\newcommand{{\\ppLXVIIdetectedPct}}{{{round(100 * detected_count / max(n_buggy, 1), 1)}\\%}}",
    f"\\newcommand{{\\ppLXVIImissed}}{{{missed_count}}}",
    f"\\newcommand{{\\ppLXVIIfpCount}}{{{fp_count}}}",
    f"\\newcommand{{\\ppLXVIIfpRate}}{{{round(100 * fp_count / max(n_correct, 1), 1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVIIcorrectTimeMean}}{{{safe_mean(correct_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIIbuggyTimeMean}}{{{safe_mean(buggy_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIItimeMean}}{{{safe_mean(all_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIItimeTotal}}{{{round(sum(all_times), 2)}\\,s}}",
    "",
    "% Per-program review results",
]

for record in correct_results + buggy_results:
    tag = record["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Verdict}}{{{record['verdict']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Props}}{{{record['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Time}}{{{record['time_s']}\\,s}}")

TEX_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

json_path = ROOT / "experiments" / "results_paper67.json"
json_path.write_text(json.dumps({
    "paper": 67,
    "workflow": "jugeo.easy.spec declared-cover review + jugeo.easy.bugs",
    "correct": correct_results,
    "buggy": buggy_results,
    "metrics": {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f_one,
        "accuracy": accuracy,
    },
}, indent=2), encoding="utf-8")

macro_count = sum(1 for line in lines if line.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
