#!/usr/bin/env python3
"""Paper 70 Experiment — Bridging Natural Language to Formal Specs.

Runs JuGeo on programs with rich docstrings containing NL specifications,
measuring how well the judgment pipeline captures NL intent: spec
extraction rates, proposition generation, and verification coverage.
Generates papers/data-paper70.tex with \ppLXX... macros.

Re-run: python3 experiments/exp70_natural_language_specs.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper70.tex"

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

# ─── Programs with NL specs (rich docstrings) ──────────────────────────────

NL_PROGRAMS = {
    "sorted_list": textwrap.dedent('''\
        def insertion_sort(arr):
            """Sort the list in ascending order.

            Ensures the output is a sorted list where each element
            is less than or equal to the next element.
            Raises no exceptions for valid list input.
            """
            for i in range(1, len(arr)):
                key = arr[i]; j = i - 1
                while j >= 0 and arr[j] > key:
                    arr[j+1] = arr[j]; j -= 1
                arr[j+1] = key
            return arr
    '''),
    "unique_elements": textwrap.dedent('''\
        def unique(lst):
            """Remove duplicates from a list.

            Returns a list with no duplicates — every element appears
            exactly once. Preserves the order of first occurrence.
            The result length is at most the input length.
            """
            seen = set()
            result = []
            for x in lst:
                if x not in seen:
                    seen.add(x)
                    result.append(x)
            return result
    '''),
    "positive_filter": textwrap.dedent('''\
        def filter_positive(nums):
            """Filter to only positive integers.

            Returns a list containing only positive values (> 0).
            All returned values are strictly greater than zero.
            The result is a subset of the input.
            """
            return [x for x in nums if x > 0]
    '''),
    "safe_divide": textwrap.dedent('''\
        def safe_divide(a, b):
            """Safely divide a by b.

            Raises ValueError if b is zero — division by zero is not allowed.
            Returns a float result when b is not zero.
            The result times b approximately equals a.
            """
            if b == 0:
                raise ValueError("division by zero")
            return a / b
    '''),
    "bounded_buffer": textwrap.dedent('''\
        class BoundedBuffer:
            """A fixed-capacity buffer.

            The buffer has a maximum capacity. Adding beyond capacity
            raises OverflowError. Removing from empty raises IndexError.
            Size is always between 0 and capacity inclusive.
            """
            def __init__(self, capacity):
                if capacity <= 0:
                    raise ValueError("capacity must be positive")
                self.capacity = capacity
                self.items = []
            def put(self, item):
                """Add item to buffer. Raises OverflowError if full."""
                if len(self.items) >= self.capacity:
                    raise OverflowError("buffer full")
                self.items.append(item)
            def get(self):
                """Remove and return oldest item. Raises IndexError if empty."""
                if not self.items:
                    raise IndexError("buffer empty")
                return self.items.pop(0)
            def size(self):
                """Return current number of items. Always >= 0."""
                return len(self.items)
    '''),
    "string_validator": textwrap.dedent('''\
        def is_valid_email(email):
            """Check if email is valid.

            A valid email contains exactly one @ symbol.
            The local part (before @) is not empty.
            The domain part (after @) contains at least one dot.
            """
            if '@' not in email: return False
            parts = email.split('@')
            if len(parts) != 2: return False
            local, domain = parts
            return len(local) > 0 and '.' in domain
    '''),
    "range_check": textwrap.dedent('''\
        def clamp(value, minimum, maximum):
            """Clamp value to range [minimum, maximum].

            The result is always >= minimum and <= maximum.
            If value is within range, returns value unchanged.
            Requires minimum <= maximum.
            """
            if minimum > maximum:
                raise ValueError("minimum must be <= maximum")
            if value < minimum: return minimum
            if value > maximum: return maximum
            return value
    '''),
    "fibonacci_spec": textwrap.dedent('''\
        def fibonacci(n):
            """Compute the n-th Fibonacci number.

            For n=0 returns 0, for n=1 returns 1.
            For n>=2, returns fibonacci(n-1) + fibonacci(n-2).
            The result is always non-negative.
            Raises ValueError for negative n.
            Time complexity is O(n).
            """
            if n < 0:
                raise ValueError("n must be non-negative")
            if n <= 1: return n
            a, b = 0, 1
            for _ in range(2, n + 1):
                a, b = b, a + b
            return b
    '''),
    "stack_contract": textwrap.dedent('''\
        class Stack:
            """A LIFO stack with bounded operations.

            push adds an element to the top.
            pop removes and returns the top element.
            pop on empty stack raises IndexError.
            After push(x) then pop(), the result is x.
            size() is always non-negative.
            """
            def __init__(self):
                self.items = []
            def push(self, x):
                """Push x onto the stack."""
                self.items.append(x)
            def pop(self):
                """Pop top element. Raises IndexError if empty."""
                if not self.items:
                    raise IndexError("pop from empty stack")
                return self.items.pop()
            def size(self):
                """Return number of elements. Always >= 0."""
                return len(self.items)
            def is_empty(self):
                """Return True if stack has no elements."""
                return len(self.items) == 0
    '''),
    "matrix_multiply": textwrap.dedent('''\
        def mat_mul(a, b):
            """Multiply two matrices.

            Requires the number of columns in a equals the number of
            rows in b. The result has dimensions (rows_a x cols_b).
            Each element result[i][j] is the dot product of row i of a
            and column j of b.
            """
            if not a or not b:
                raise ValueError("empty matrix")
            if len(a[0]) != len(b):
                raise ValueError("incompatible dimensions")
            rows, cols, inner = len(a), len(b[0]), len(b)
            result = [[0]*cols for _ in range(rows)]
            for i in range(rows):
                for j in range(cols):
                    for k in range(inner):
                        result[i][j] += a[i][k] * b[k][j]
            return result
    '''),
    "search_with_spec": textwrap.dedent('''\
        def binary_search(arr, target):
            """Search for target in a sorted array.

            Precondition: arr is sorted in ascending order.
            Returns the index of target if found, -1 otherwise.
            If result >= 0, then arr[result] == target.
            If result == -1, target is not in arr.
            """
            lo, hi = 0, len(arr) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if arr[mid] == target: return mid
                elif arr[mid] < target: lo = mid + 1
                else: hi = mid - 1
            return -1
    '''),
    "counter_invariant": textwrap.dedent('''\
        class Counter:
            """A non-negative counter.

            The value is always >= 0.
            increment increases value by 1.
            decrement decreases value by 1 but never below 0.
            reset sets value to 0.
            """
            def __init__(self):
                self.value = 0
            def increment(self):
                """Increase by 1."""
                self.value += 1
            def decrement(self):
                """Decrease by 1, minimum 0."""
                if self.value > 0:
                    self.value -= 1
            def reset(self):
                """Set to 0."""
                self.value = 0
            def get(self):
                """Return current value. Always >= 0."""
                return self.value
    '''),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 70: Natural Language Specs Experiments")
print("=" * 60)

results = []
for prog_id, source in NL_PROGRAMS.items():
    print(f"  [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()

        # Full pipeline
        spec = run_jugeo_json("spec", tmp)
        desc = run_jugeo_json("descend", tmp)
        enc = run_jugeo_json("encode", tmp)
        eval_r = run_jugeo_json("evaluate", tmp)

        elapsed = time.perf_counter() - t0

        d = desc[0] if desc else {}
        e = enc[0] if enc else {}
        ev = eval_r[0] if eval_r else {}

        files_enc = e.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
        secs = d.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in secs)
        ok_p = sum(s.get("ok", 0) for s in secs)
        verdict = d.get("verdict", "unknown")
        trust = d.get("trust", "UNKNOWN")

        # Count docstring lines as NL spec lines
        nl_lines = sum(1 for line in source.split('\n')
                       if line.strip().startswith('"""') or line.strip().startswith("'''")
                       or (line.strip() and not line.strip().startswith(('def ', 'class ', 'if ',
                           'for ', 'while ', 'return ', 'raise ', 'self.', 'import '))
                           and not line.strip().startswith('#')
                           and not line.strip().startswith(('result', 'seen', 'key',
                                                           'lo', 'hi', 'mid', 'a,', 'rows'))))

        rec = {
            "id": prog_id,
            "n_coords": n_coords, "props": props, "ok": ok_p,
            "verdict": verdict, "trust": trust,
            "nl_lines": nl_lines,
            "time_s": round(elapsed, 3),
        }
        results.append(rec)
        print(f"coords={n_coords} props={props}/{ok_p} nl={nl_lines} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": prog_id, "error": str(e)})
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok_r = [r for r in results if "error" not in r]
n_total = len(NL_PROGRAMS)
n_ok = len(ok_r)
verified = sum(1 for r in ok_r if r["verdict"] == "verified")

total_props = sum(r["props"] for r in ok_r)
total_ok = sum(r["ok"] for r in ok_r)
total_nl = sum(r["nl_lines"] for r in ok_r)

coords_list = [r["n_coords"] for r in ok_r]
props_list = [r["props"] for r in ok_r]
nl_list = [r["nl_lines"] for r in ok_r]
times = [r["time_s"] for r in ok_r]

prop_rate = round(total_ok / max(total_props, 1), 4)

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper70.tex — AUTO-GENERATED by exp70_natural_language_specs.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp70_natural_language_specs.py",
    f"% Generated from {n_total} NL-annotated programs",
    "",
    f"\\newcommand{{\\ppLXXprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXXprogramsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXXverified}}{{{verified}}}",
    f"\\newcommand{{\\ppLXXverifiedPct}}{{{round(100*verified/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXXpropsTotal}}{{{total_props}}}",
    f"\\newcommand{{\\ppLXXpropsOk}}{{{total_ok}}}",
    f"\\newcommand{{\\ppLXXpropRate}}{{{round(prop_rate*100,1)}\\%}}",
    f"\\newcommand{{\\ppLXXpropsMean}}{{{safe_mean(props_list)}}}",
    "",
    f"\\newcommand{{\\ppLXXnlLinesTotal}}{{{total_nl}}}",
    f"\\newcommand{{\\ppLXXnlLinesMean}}{{{safe_mean(nl_list)}}}",
    f"\\newcommand{{\\ppLXXcoordsMean}}{{{safe_mean(coords_list)}}}",
    "",
    f"\\newcommand{{\\ppLXXtimeMean}}{{{safe_mean(times)}\\,s}}",
    f"\\newcommand{{\\ppLXXtimeTotal}}{{{round(sum(times),2)}\\,s}}",
    f"\\newcommand{{\\ppLXXtimeMin}}{{{round(min(times),3) if times else 0}\\,s}}",
    f"\\newcommand{{\\ppLXXtimeMax}}{{{round(max(times),3) if times else 0}\\,s}}",
    "",
    "% Per-program NL pipeline results",
]
for r in ok_r:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}Props}}{{{r['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}Ok}}{{{r['ok']}}}")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}NlLines}}{{{r['nl_lines']}}}")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}Verdict}}{{{r['verdict']}}}")
    lines.append(f"\\newcommand{{\\ppLXXnl{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper70.json"
with open(json_path, "w") as f:
    json.dump({"paper": 70, "programs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
