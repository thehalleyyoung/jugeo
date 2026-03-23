#!/usr/bin/env python3
"""Paper 67 Experiment — Code Review Automation via Judgment-Based Review.

Runs JuGeo on correct and intentionally-buggy programs to measure
review quality: bug detection, false positive rates, review times,
and judgment-level precision.
Generates papers/data-paper67.tex with \ppLXVII... macros.

Re-run: python3 experiments/exp67_code_review_automation.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper67.tex"

def run_jugeo_json(*args, timeout=30):
    cmd = [sys.executable, "-m", "jugeo", "--format", "json"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    lines = [l for l in r.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = dec.raw_decode(remaining)
            objects.append(obj); idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source); f.close(); return f.name

def safe_mean(xs): return round(statistics.mean(xs), 2) if xs else 0.0

# ─── Correct programs ──────────────────────────────────────────────────────

CORRECT = {
    "factorial": textwrap.dedent("""\
        def factorial(n):
            if n < 0: raise ValueError("negative")
            result = 1
            for i in range(2, n+1): result *= i
            return result
    """),
    "binary_search": textwrap.dedent("""\
        def binary_search(arr, target):
            lo, hi = 0, len(arr) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if arr[mid] == target: return mid
                elif arr[mid] < target: lo = mid + 1
                else: hi = mid - 1
            return -1
    """),
    "merge": textwrap.dedent("""\
        def merge(left, right):
            result, i, j = [], 0, 0
            while i < len(left) and j < len(right):
                if left[i] <= right[j]:
                    result.append(left[i]); i += 1
                else:
                    result.append(right[j]); j += 1
            result.extend(left[i:]); result.extend(right[j:])
            return result
    """),
    "gcd": textwrap.dedent("""\
        def gcd(a, b):
            while b: a, b = b, a % b
            return a
    """),
    "is_palindrome": textwrap.dedent("""\
        def is_palindrome(s):
            s = s.lower()
            return s == s[::-1]
    """),
}

# ─── Buggy variants ────────────────────────────────────────────────────────

BUGGY = {
    "factorial_off_by_one": textwrap.dedent("""\
        def factorial(n):
            if n < 0: raise ValueError("negative")
            result = 1
            for i in range(2, n): result *= i  # bug: should be n+1
            return result
    """),
    "binary_search_wrong_mid": textwrap.dedent("""\
        def binary_search(arr, target):
            lo, hi = 0, len(arr)  # bug: should be len(arr)-1
            while lo <= hi:
                mid = (lo + hi) // 2
                if arr[mid] == target: return mid
                elif arr[mid] < target: lo = mid + 1
                else: hi = mid - 1
            return -1
    """),
    "merge_missing_extend": textwrap.dedent("""\
        def merge(left, right):
            result, i, j = [], 0, 0
            while i < len(left) and j < len(right):
                if left[i] <= right[j]:
                    result.append(left[i]); i += 1
                else:
                    result.append(right[j]); j += 1
            # bug: missing extend of remaining elements
            return result
    """),
    "gcd_wrong_swap": textwrap.dedent("""\
        def gcd(a, b):
            while b: a, b = a, a % b  # bug: should be b, a%b
            return a
    """),
    "palindrome_no_lower": textwrap.dedent("""\
        def is_palindrome(s):
            return s == s[::-1]  # bug: missing .lower()
    """),
    "divide_no_check": textwrap.dedent("""\
        def safe_divide(a, b):
            return a / b  # bug: no zero check
    """),
    "stack_wrong_pop": textwrap.dedent("""\
        class Stack:
            def __init__(self): self.items = []
            def push(self, x): self.items.append(x)
            def pop(self):
                return self.items.pop(0)  # bug: should be pop() not pop(0)
    """),
    "max_no_empty_check": textwrap.dedent("""\
        def find_max(lst):
            m = lst[0]  # bug: no empty list check
            for x in lst[1:]:
                if x > m: m = x
            return m
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 67: Code Review Automation Experiments")
print("=" * 60)

correct_results = []
buggy_results = []

print("\n  Correct programs:")
for prog_id, source in CORRECT.items():
    print(f"    [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()
        desc = run_jugeo_json("descend", tmp)
        bugs = run_jugeo_json("bugs", tmp)
        elapsed = time.perf_counter() - t0

        d = desc[0] if desc else {}
        b = bugs[0] if bugs else {}
        secs = d.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in secs)
        ok_p = sum(s.get("ok", 0) for s in secs)
        bug_count = b.get("count", 0) if isinstance(b, dict) else 0

        rec = {"id": prog_id, "correct": True, "verdict": d.get("verdict", "unknown"),
               "props": props, "ok": ok_p, "bugs_reported": bug_count,
               "false_positive": bug_count > 0, "time_s": round(elapsed, 3)}
        correct_results.append(rec)
        fp = "FP!" if bug_count > 0 else "ok"
        print(f"verdict={rec['verdict']} bugs={bug_count} {fp} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        try: os.unlink(tmp)
        except: pass

print("\n  Buggy programs:")
for prog_id, source in BUGGY.items():
    print(f"    [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()
        desc = run_jugeo_json("descend", tmp)
        bugs = run_jugeo_json("bugs", tmp)
        elapsed = time.perf_counter() - t0

        d = desc[0] if desc else {}
        b = bugs[0] if bugs else {}
        secs = d.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in secs)
        ok_p = sum(s.get("ok", 0) for s in secs)
        bug_count = b.get("count", 0) if isinstance(b, dict) else 0
        obs = len(d.get("obstructions", []))

        detected = bug_count > 0 or obs > 0 or (props > 0 and ok_p < props)
        rec = {"id": prog_id, "correct": False, "verdict": d.get("verdict", "unknown"),
               "props": props, "ok": ok_p, "bugs_reported": bug_count,
               "obstructions": obs, "detected": detected,
               "time_s": round(elapsed, 3)}
        buggy_results.append(rec)
        det = "DETECTED" if detected else "MISSED"
        print(f"verdict={rec['verdict']} bugs={bug_count} obs={obs} {det} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

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

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper67.tex — AUTO-GENERATED by exp67_code_review_automation.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp67_code_review_automation.py",
    f"% Generated from {n_total} programs ({n_correct} correct, {n_buggy} buggy)",
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
    f"\\newcommand{{\\ppLXVIIaccuracyPct}}{{{round(accuracy*100,1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVIIdetected}}{{{detected_count}}}",
    f"\\newcommand{{\\ppLXVIIdetectedPct}}{{{round(100*detected_count/max(n_buggy,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXVIImissed}}{{{missed_count}}}",
    f"\\newcommand{{\\ppLXVIIfpCount}}{{{fp_count}}}",
    f"\\newcommand{{\\ppLXVIIfpRate}}{{{round(100*fp_count/max(n_correct,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVIIcorrectTimeMean}}{{{safe_mean(correct_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIIbuggyTimeMean}}{{{safe_mean(buggy_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIItimeMean}}{{{safe_mean(all_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIItimeTotal}}{{{round(sum(all_times),2)}\\,s}}",
    "",
    "% Per-program review results",
]
for r in correct_results + buggy_results:
    tag = r["id"].replace("_", "")
    kind = "correct" if r["correct"] else "buggy"
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Verdict}}{{{r['verdict']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Props}}{{{r['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIrev{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper67.json"
with open(json_path, "w") as f:
    json.dump({"paper": 67, "correct": correct_results, "buggy": buggy_results,
               "metrics": {"tp": tp, "tn": tn, "fp": fp, "fn": fn,
                           "precision": precision, "recall": recall, "f1": f_one}},
              f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
