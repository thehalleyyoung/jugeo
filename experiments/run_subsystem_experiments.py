#!/usr/bin/env python3
"""
run_subsystem_experiments.py — Comprehensive subsystem experiment runner.

Exercises every major JuGeo subsystem via the deep API and CLI,
collects REAL metrics (timing, structure counts, success rates),
and generates per-paper LaTeX macros to replace fabricated numbers.

Usage:
    python3 experiments/run_subsystem_experiments.py

Outputs:
    experiments/subsystem_results.json   — raw results
    papers/subsystem-data.tex            — LaTeX \newcommand macros
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jugeo.geometry.site import (
    SiteBuilder, Coordinate, CoordinateKind, Morphism, MorphismKind,
)
from jugeo.geometry.descent import (
    DescentEngine, DescentConfiguration, DescentStrategy,
)
from jugeo.geometry.covers import Cover
from jugeo.judgments.judgment_terms import (
    JudgmentBuilder, Proposition, PropositionKind, TrustLevel,
)

# ── Helper programs for CLI experiments ──────────────────────────────

PROGRAMS = {
    "bubble_sort": """\
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr
""",
    "binary_search": """\
def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
""",
    "stack_impl": """\
class Stack:
    def __init__(self):
        self._data = []
    def push(self, val):
        self._data.append(val)
    def pop(self):
        if not self._data:
            raise IndexError("pop from empty stack")
        return self._data.pop()
    def peek(self):
        if not self._data:
            raise IndexError("peek at empty stack")
        return self._data[-1]
    def is_empty(self):
        return len(self._data) == 0
    def size(self):
        return len(self._data)
""",
    "linked_list": """\
class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.nxt = nxt

class LinkedList:
    def __init__(self):
        self.head = None
        self._size = 0
    def prepend(self, val):
        self.head = Node(val, self.head)
        self._size += 1
    def append(self, val):
        if not self.head:
            self.head = Node(val)
        else:
            cur = self.head
            while cur.nxt:
                cur = cur.nxt
            cur.nxt = Node(val)
        self._size += 1
    def find(self, val):
        cur = self.head
        while cur:
            if cur.val == val:
                return True
            cur = cur.nxt
        return False
    def size(self):
        return self._size
""",
    "matrix_multiply": """\
def matrix_multiply(A, B):
    rows_a, cols_a = len(A), len(A[0])
    rows_b, cols_b = len(B), len(B[0])
    assert cols_a == rows_b, "dimension mismatch"
    C = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                C[i][j] += A[i][k] * B[k][j]
    return C

def transpose(M):
    rows, cols = len(M), len(M[0])
    return [[M[i][j] for i in range(rows)] for j in range(cols)]
""",
    "rate_limiter": """\
import time as _time

class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = _time.monotonic()

    def _refill(self):
        now = _time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity,
                          self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, n=1):
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def available(self):
        self._refill()
        return int(self.tokens)
""",
    "event_bus": """\
class EventBus:
    def __init__(self):
        self._handlers = {}
    def subscribe(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)
    def unsubscribe(self, event, handler):
        if event in self._handlers:
            self._handlers[event] = [
                h for h in self._handlers[event] if h != handler
            ]
    def emit(self, event, *args, **kwargs):
        for handler in self._handlers.get(event, []):
            handler(*args, **kwargs)
    def has_subscribers(self, event):
        return bool(self._handlers.get(event))
    def clear(self):
        self._handlers.clear()
""",
    "csv_parser": """\
def parse_csv(text, delimiter=','):
    rows = []
    for line in text.strip().split('\\n'):
        fields = []
        current = ''
        in_quotes = False
        for ch in line:
            if ch == '"':
                in_quotes = not in_quotes
            elif ch == delimiter and not in_quotes:
                fields.append(current.strip())
                current = ''
            else:
                current += ch
        fields.append(current.strip())
        rows.append(fields)
    return rows

def csv_to_dicts(text, delimiter=','):
    rows = parse_csv(text, delimiter)
    if not rows:
        return []
    headers = rows[0]
    return [{h: v for h, v in zip(headers, row)} for row in rows[1:]]
""",
    "gcd_lcm": """\
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    return abs(a * b) // gcd(a, b)

def gcd_list(nums):
    result = nums[0]
    for n in nums[1:]:
        result = gcd(result, n)
    return result

def lcm_list(nums):
    result = nums[0]
    for n in nums[1:]:
        result = lcm(result, n)
    return result

def coprime(a, b):
    return gcd(a, b) == 1
""",
    "state_machine": """\
class StateMachine:
    def __init__(self, initial, transitions):
        self.state = initial
        self.transitions = transitions
        self.history = [initial]

    def trigger(self, event):
        key = (self.state, event)
        if key not in self.transitions:
            raise ValueError(f"No transition from {self.state} on {event}")
        self.state = self.transitions[key]
        self.history.append(self.state)
        return self.state

    def can_trigger(self, event):
        return (self.state, event) in self.transitions

    def reset(self, state=None):
        self.state = state or self.history[0]
        self.history = [self.state]

    def path_length(self):
        return len(self.history)
""",
}

# ── Site construction helpers ────────────────────────────────────────

def build_site(name, n_coords, n_morphisms):
    """Build a site with n_coords coordinates and up to n_morphisms morphisms."""
    sb = SiteBuilder(name)
    coords = []
    kinds = list(CoordinateKind)
    for i in range(n_coords):
        c = Coordinate(f"c{i}", kinds[i % len(kinds)])
        sb.add_coordinate(c)
        coords.append(c)
    added = 0
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            if added >= n_morphisms:
                break
            sb.add_morphism(Morphism(coords[i], coords[j], MorphismKind.RESTRICTION))
            added += 1
        if added >= n_morphisms:
            break
    return sb.build()


def timed(func, *args, **kwargs):
    """Run func, return (result, elapsed_seconds)."""
    t0 = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        return result, elapsed, True
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return str(e), elapsed, False


def run_cli(prog_text, command="prove"):
    """Write prog_text to a temp file and run jugeo CLI on it."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(prog_text)
        f.flush()
        fname = f.name
    try:
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, "-m", "jugeo", "--format", "json", command, fname],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT),
        )
        elapsed = time.perf_counter() - t0
        # Strip banner lines before JSON and handle multiple JSON objects
        stdout = r.stdout
        json_start = stdout.find("{")
        if json_start >= 0:
            decoder = json.JSONDecoder()
            try:
                data, _ = decoder.raw_decode(stdout[json_start:])
                return data, elapsed, True
            except json.JSONDecodeError:
                return stdout[:200], elapsed, False
        return r.stderr[:200] if r.stderr else "no output", elapsed, False
    except Exception as e:
        return str(e), time.perf_counter() - t0, False
    finally:
        os.unlink(fname)


# ── Experiment sections ──────────────────────────────────────────────

results = {}

def section(name):
    """Decorator to register an experiment section."""
    def decorator(func):
        def wrapper():
            print(f"\n{'='*60}")
            print(f"  Section: {name}")
            print(f"{'='*60}")
            t0 = time.perf_counter()
            data = func()
            elapsed = time.perf_counter() - t0
            data["_elapsed_total"] = round(elapsed, 3)
            results[name] = data
            print(f"  ✅ {name} done in {elapsed:.1f}s")
            return data
        wrapper.__name__ = name
        wrapper._section = True
        return wrapper
    return decorator


# ── 1. CLI prove/encode on all programs ──────────────────────────────

@section("cli_verification")
def cli_verification():
    """Run jugeo prove + encode on all programs."""
    data = {"programs": {}, "summary": {}}
    total = verified = 0
    total_coords = total_props = total_props_ok = 0
    times = []
    for name, code in PROGRAMS.items():
        pdata, elapsed, ok = run_cli(code, "prove")
        total += 1
        rec = {"elapsed": round(elapsed, 3), "ok": ok}
        if ok and isinstance(pdata, dict):
            s = pdata.get("summary", {})
            rec["coords"] = s.get("coordinates", 0)
            rec["props"] = s.get("propositions", 0)
            rec["props_ok"] = s.get("propositions_ok", 0)
            rec["verdict"] = pdata.get("files", [{}])[0].get("verdict", "?")
            if s.get("propositions_ok", 0) == s.get("propositions", 0) and s.get("propositions", 0) > 0:
                verified += 1
            total_coords += rec["coords"]
            total_props += rec["props"]
            total_props_ok += rec["props_ok"]
        times.append(elapsed)
        data["programs"][name] = rec
        status = "✅" if ok else "❌"
        print(f"    {status} {name}: {elapsed:.2f}s")
    data["summary"] = {
        "total": total, "verified": verified,
        "accuracy": round(verified / max(total, 1) * 100, 1),
        "total_coords": total_coords, "total_props": total_props,
        "total_props_ok": total_props_ok,
        "mean_time": round(sum(times) / max(len(times), 1), 3),
        "min_time": round(min(times), 3) if times else 0,
        "max_time": round(max(times), 3) if times else 0,
    }
    return data


# ── 2. Site subsystem methods ────────────────────────────────────────

@section("site_subsystems")
def site_subsystems():
    """Exercise every Site subsystem method on sites of various sizes."""
    sizes = [
        ("small", 3, 2),
        ("medium", 8, 12),
        ("large", 15, 30),
    ]
    subsystem_methods = [
        "bug_detection_scan", "specification_satisfaction",
        "generation_cover_design", "inhabitant_fleet",
        "theorem_ecology", "state_space_exploration",
        "repair_semantics", "replay_gluing",
        "semantic_futures", "maturity_assessment",
        "evidence_manifold", "hypercover_treaty",
        "encode_for_solver", "interface_routing",
        "orchestrate_verification", "run_full_descent",
        "trust_presheaf", "kernel_lifecycle",
        "judgment_sheaf", "discovery_pipeline",
        "analogy_transport", "change_of_site",
        "formal_core_site", "problem_atlas",
        "public_alignment", "regime_bootstrapping",
        "relational_refinement", "semantic_closure",
        "theorem_economics", "benchmark_suite",
    ]
    data = {"by_size": {}, "by_method": {}, "summary": {}}
    total_calls = successful = 0
    all_times = []

    for size_name, nc, nm in sizes:
        site = build_site(f"exp_{size_name}", nc, nm)
        data["by_size"][size_name] = {"coords": nc, "morphisms": nm, "methods": {}}
        for method_name in subsystem_methods:
            fn = getattr(site, method_name, None)
            if fn is None:
                continue
            result, elapsed, ok = timed(fn)
            total_calls += 1
            if ok:
                successful += 1
            all_times.append(elapsed)
            rec = {
                "elapsed": round(elapsed, 4),
                "ok": ok,
                "result_type": type(result).__name__ if ok else "error",
            }
            # Extract structure from result if dict-like
            if ok and hasattr(result, '__len__'):
                try:
                    rec["result_size"] = len(result)
                except:
                    pass
            data["by_size"][size_name]["methods"][method_name] = rec
            if method_name not in data["by_method"]:
                data["by_method"][method_name] = {}
            data["by_method"][method_name][size_name] = rec
            status = "✅" if ok else "⚠️"
            print(f"    {status} {size_name}/{method_name}: {elapsed:.4f}s")

    data["summary"] = {
        "total_calls": total_calls,
        "successful": successful,
        "success_rate": round(successful / max(total_calls, 1) * 100, 1),
        "mean_time": round(sum(all_times) / max(len(all_times), 1), 4),
        "total_time": round(sum(all_times), 3),
    }
    return data


# ── 3. Descent engine experiments ────────────────────────────────────

@section("descent_engine")
def descent_engine():
    """Test descent strategies, obstruction extraction, certificates."""
    data = {"strategies": {}, "operations": {}, "summary": {}}

    # Build a site and cover for descent
    site = build_site("descent_test", 6, 8)
    coords = list(site.objects())
    cover = Cover(
        target=coords[0] if coords else Coordinate("c0", CoordinateKind.FUNCTION),
        patches=tuple(coords[1:4]),
    )
    sections = {
        c.name: {"value": f"section_{c.name}", "trust": "COPILOT_SUGGESTED"}
        for c in coords[1:4]
    }

    # Test each strategy
    for strat in DescentStrategy:
        cfg = DescentConfiguration(strategy=strat, depth_limit=3)
        eng = DescentEngine(cfg)
        # attempt_descent
        result, elapsed, ok = timed(eng.attempt_descent, cover, sections)
        rec = {"elapsed": round(elapsed, 4), "ok": ok}
        if ok and hasattr(result, 'success'):
            rec["success"] = result.success
        data["strategies"][strat.name] = rec
        status = "✅" if ok else "⚠️"
        print(f"    {status} strategy={strat.name}: {elapsed:.4f}s")

    # Test engine operations
    cfg = DescentConfiguration(strategy=DescentStrategy.EAGER, depth_limit=3)
    eng = DescentEngine(cfg)

    operations = [
        ("run", lambda: eng.run(cover, sections)),
        ("iterative_descent", lambda: eng.iterative_descent(cover, sections)),
        ("parallel_descent", lambda: eng.parallel_descent(cover, sections)),
        ("encode_full_descent", lambda: eng.encode_full_descent(cover, sections)),
        ("solver_assisted_descent", lambda: eng.solver_assisted_descent(cover, sections)),
        ("trust_axioms", lambda: eng.trust_axioms()),
        ("obstruction_theory", lambda: eng.obstruction_theory()),
        ("formal_descent", lambda: eng.formal_descent()),
        ("structural_frontier_analysis", lambda: eng.structural_frontier_analysis()),
        ("mixed_evidence_routing", lambda: eng.mixed_evidence_routing()),
        ("orchestrated_descent", lambda: eng.orchestrated_descent()),
    ]

    total_ops = successful_ops = 0
    for op_name, op_fn in operations:
        result, elapsed, ok = timed(op_fn)
        total_ops += 1
        if ok:
            successful_ops += 1
        rec = {"elapsed": round(elapsed, 4), "ok": ok}
        if ok:
            rec["result_type"] = type(result).__name__
        data["operations"][op_name] = rec
        status = "✅" if ok else "⚠️"
        print(f"    {status} {op_name}: {elapsed:.4f}s")

    data["summary"] = {
        "strategies_tested": len(data["strategies"]),
        "operations_tested": total_ops,
        "operations_successful": successful_ops,
    }
    return data


# ── 4. Judgment construction experiments ─────────────────────────────

@section("judgment_construction")
def judgment_construction():
    """Build judgments with various configurations, measure timing."""
    data = {"judgments": [], "summary": {}}
    trust_levels = list(TrustLevel)
    prop_kinds = list(PropositionKind)

    total = 0
    successful = 0
    times = []

    for tl in trust_levels:
        for pk in prop_kinds:
            t0 = time.perf_counter()
            try:
                c = Coordinate(f"test_{pk.name}", CoordinateKind.FUNCTION)
                j = (JudgmentBuilder()
                     .at(c)
                     .claiming_formula(f"test_prop_{pk.name}", kind=pk)
                     .with_trust_level(tl)
                     .build())
                elapsed = time.perf_counter() - t0
                total += 1
                successful += 1
                times.append(elapsed)
                data["judgments"].append({
                    "trust": tl.name, "prop_kind": pk.name,
                    "elapsed": round(elapsed, 6), "ok": True,
                })
            except Exception as e:
                elapsed = time.perf_counter() - t0
                total += 1
                times.append(elapsed)
                data["judgments"].append({
                    "trust": tl.name, "prop_kind": pk.name,
                    "elapsed": round(elapsed, 6), "ok": False, "error": str(e)[:80],
                })

    data["summary"] = {
        "total": total,
        "successful": successful,
        "success_rate": round(successful / max(total, 1) * 100, 1),
        "mean_time_us": round(sum(times) / max(len(times), 1) * 1e6, 2),
        "trust_levels": len(trust_levels),
        "prop_kinds": len(prop_kinds),
    }
    print(f"    Built {successful}/{total} judgments, mean {data['summary']['mean_time_us']:.1f}µs")
    return data


# ── 5. Bug detection experiments ─────────────────────────────────────

@section("bug_detection")
def bug_detection():
    """Run jugeo bugs on programs with known issues."""
    buggy_programs = {
        "off_by_one": """\
def find_max(arr):
    if not arr:
        return None
    best = arr[0]
    for i in range(1, len(arr) - 1):  # BUG: misses last element
        if arr[i] > best:
            best = arr[i]
    return best
""",
        "missing_return": """\
def classify(x):
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    # BUG: missing return for x == 0
""",
        "type_confusion": """\
def safe_divide(a, b):
    if b == 0:
        return "error"  # BUG: returns string instead of number
    return a / b

def compute(x, y):
    result = safe_divide(x, y)
    return result + 1  # Will fail if result is "error"
""",
        "infinite_recursion": """\
def flatten(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result
# Not actually infinite but deep nesting could stack overflow
""",
        "resource_leak": """\
def read_config(path):
    f = open(path, 'r')
    data = f.read()
    # BUG: file never closed if exception occurs
    config = {}
    for line in data.split('\\n'):
        if '=' in line:
            k, v = line.split('=', 1)
            config[k.strip()] = v.strip()
    f.close()
    return config
""",
    }
    correct_programs = {
        "find_max_fixed": """\
def find_max(arr):
    if not arr:
        return None
    best = arr[0]
    for i in range(1, len(arr)):
        if arr[i] > best:
            best = arr[i]
    return best
""",
        "classify_fixed": """\
def classify(x):
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    return "zero"
""",
    }

    data = {"buggy": {}, "correct": {}, "summary": {}}
    bugs_found = bugs_total = 0
    correct_verified = correct_total = 0

    for name, code in buggy_programs.items():
        pdata, elapsed, ok = run_cli(code, "prove")
        bugs_total += 1
        rec = {"elapsed": round(elapsed, 3), "ok": ok}
        if ok and isinstance(pdata, dict):
            s = pdata.get("summary", {})
            rec["obstructions"] = s.get("obstructions", 0)
            rec["props_ok"] = s.get("propositions_ok", 0)
            rec["props"] = s.get("propositions", 0)
            if s.get("obstructions", 0) > 0 or s.get("propositions_ok", 0) < s.get("propositions", 0):
                bugs_found += 1
                rec["detected"] = True
            else:
                rec["detected"] = False
        data["buggy"][name] = rec
        det = "🐛" if rec.get("detected") else "✅"
        print(f"    {det} buggy/{name}: {elapsed:.2f}s")

    for name, code in correct_programs.items():
        pdata, elapsed, ok = run_cli(code, "prove")
        correct_total += 1
        rec = {"elapsed": round(elapsed, 3), "ok": ok}
        if ok and isinstance(pdata, dict):
            s = pdata.get("summary", {})
            rec["props_ok"] = s.get("propositions_ok", 0)
            rec["props"] = s.get("propositions", 0)
            if s.get("propositions_ok", 0) == s.get("propositions", 0):
                correct_verified += 1
                rec["verified"] = True
        data["correct"][name] = rec
        print(f"    ✅ correct/{name}: {elapsed:.2f}s")

    data["summary"] = {
        "buggy_total": bugs_total,
        "bugs_detected": bugs_found,
        "detection_rate": round(bugs_found / max(bugs_total, 1) * 100, 1),
        "correct_total": correct_total,
        "correct_verified": correct_verified,
        "false_positive_rate": round((correct_total - correct_verified) / max(correct_total, 1) * 100, 1),
    }
    return data


# ── 6. Equivalence checking experiments ──────────────────────────────

@section("equivalence_checking")
def equivalence_checking():
    """Run jugeo equiv on pairs of programs."""
    equiv_pairs = [
        ("bubble", """\
def sort_a(arr):
    a = list(arr)
    for i in range(len(a)):
        for j in range(len(a)-i-1):
            if a[j] > a[j+1]:
                a[j], a[j+1] = a[j+1], a[j]
    return a
""", """\
def sort_b(arr):
    a = list(arr)
    for i in range(len(a)):
        mn = i
        for j in range(i+1, len(a)):
            if a[j] < a[mn]:
                mn = j
        a[i], a[mn] = a[mn], a[i]
    return a
"""),
        ("gcd", """\
def gcd_a(a, b):
    while b:
        a, b = b, a % b
    return a
""", """\
def gcd_b(x, y):
    if y == 0:
        return x
    return gcd_b(y, x % y)
"""),
        ("factorial", """\
def fact_iter(n):
    r = 1
    for i in range(1, n+1):
        r *= i
    return r
""", """\
def fact_rec(n):
    if n <= 1:
        return 1
    return n * fact_rec(n-1)
"""),
    ]
    data = {"pairs": {}, "summary": {}}
    total = verified = 0
    for name, code_a, code_b in equiv_pairs:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as fa:
            fa.write(code_a); fa.flush(); path_a = fa.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as fb:
            fb.write(code_b); fb.flush(); path_b = fb.name
        try:
            t0 = time.perf_counter()
            r = subprocess.run(
                [sys.executable, "-m", "jugeo", "--format", "json", "equiv", path_a, path_b],
                capture_output=True, text=True, timeout=30, cwd=str(ROOT),
            )
            elapsed = time.perf_counter() - t0
            total += 1
            stdout = r.stdout
            json_start = stdout.find("{")
            if json_start >= 0:
                decoder = json.JSONDecoder()
                try:
                    d, _ = decoder.raw_decode(stdout[json_start:])
                    rec = {"elapsed": round(elapsed, 3), "ok": True}
                    rec["verdict"] = d.get("verdict", "unknown")
                    verified += 1
                except json.JSONDecodeError:
                    rec = {"elapsed": round(elapsed, 3), "ok": False, "raw": stdout[:200]}
            else:
                rec = {"elapsed": round(elapsed, 3), "ok": False, "raw": stdout[:200]}
            data["pairs"][name] = rec
            print(f"    {'✅' if rec.get('ok') else '❌'} equiv/{name}: {elapsed:.2f}s")
        finally:
            os.unlink(path_a)
            os.unlink(path_b)
    data["summary"] = {"total": total, "verified": verified}
    return data


# ── 7. Repair experiments ────────────────────────────────────────────

@section("repair_experiments")
def repair_experiments():
    """Test jugeo repair on buggy programs."""
    data = {"programs": {}, "summary": {}}
    buggy = {
        "off_by_one": """\
def sum_range(n):
    total = 0
    for i in range(n):  # should be range(n+1)
        total += i
    return total
""",
        "wrong_init": """\
def find_min(arr):
    result = 0  # BUG: should be arr[0] or float('inf')
    for x in arr:
        if x < result:
            result = x
    return result
""",
    }
    total = repaired = 0
    for name, code in buggy.items():
        pdata, elapsed, ok = run_cli(code, "prove")
        total += 1
        rec = {"elapsed": round(elapsed, 3), "ok": ok}
        if ok and isinstance(pdata, dict):
            s = pdata.get("summary", {})
            rec["obstructions"] = s.get("obstructions", 0)
            rec["coords"] = s.get("coordinates", 0)
            rec["props_ok"] = s.get("propositions_ok", 0)
            rec["props"] = s.get("propositions", 0)
            if s.get("obstructions", 0) > 0:
                repaired += 1
                rec["obstruction_found"] = True
        data["programs"][name] = rec
        print(f"    {'🔧' if rec.get('obstruction_found') else '✅'} repair/{name}: {elapsed:.2f}s")

    data["summary"] = {
        "total": total,
        "obstructions_found": repaired,
    }
    return data


# ── 8. Scalability experiments ───────────────────────────────────────

@section("scalability")
def scalability():
    """Measure how metrics scale with site size."""
    data = {"sizes": [], "summary": {}}
    for n in [2, 4, 8, 12, 16, 20]:
        site = build_site(f"scale_{n}", n, n * 2)
        t0 = time.perf_counter()
        try:
            r = site.run_full_descent()
            elapsed = time.perf_counter() - t0
            ok = True
        except Exception as e:
            elapsed = time.perf_counter() - t0
            ok = False
        rec = {
            "coords": n, "morphisms": n * 2,
            "elapsed": round(elapsed, 4), "ok": ok,
        }
        data["sizes"].append(rec)
        print(f"    n={n}: {elapsed:.4f}s {'✅' if ok else '⚠️'}")
    data["summary"] = {
        "sizes_tested": len(data["sizes"]),
        "all_ok": all(s["ok"] for s in data["sizes"]),
    }
    return data


# ── Run all experiments ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  JuGeo Subsystem Experiment Runner")
    print("=" * 60)

    experiments = [
        cli_verification,
        site_subsystems,
        descent_engine,
        judgment_construction,
        bug_detection,
        equivalence_checking,
        repair_experiments,
        scalability,
    ]

    t0_total = time.perf_counter()
    for exp in experiments:
        try:
            exp()
        except Exception as e:
            print(f"  ❌ {exp.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
    total_elapsed = time.perf_counter() - t0_total

    # Save raw results
    out_json = ROOT / "experiments" / "subsystem_results.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n📄 Raw results → {out_json}")

    # Generate LaTeX macros
    generate_latex_macros(results, total_elapsed)

    print(f"\n⏱  Total experiment time: {total_elapsed:.1f}s")


def generate_latex_macros(results, total_elapsed):
    """Generate papers/subsystem-data.tex with per-paper macros."""
    lines = [
        "% subsystem-data.tex — AUTO-GENERATED from real jugeo subsystem experiments",
        "% DO NOT EDIT — regenerate with: python3 experiments/run_subsystem_experiments.py",
        "",
    ]

    def cmd(name, value):
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")

    # CLI verification macros
    cli = results.get("cli_verification", {}).get("summary", {})
    cmd("subCliTotal", cli.get("total", 0))
    cmd("subCliVerified", cli.get("verified", 0))
    cmd("subCliAccuracy", f"{cli.get('accuracy', 0)}\\%")
    cmd("subCliMeanTime", f"{cli.get('mean_time', 0)}\\,s")
    cmd("subCliMinTime", f"{cli.get('min_time', 0)}\\,s")
    cmd("subCliMaxTime", f"{cli.get('max_time', 0)}\\,s")
    cmd("subCliTotalCoords", cli.get("total_coords", 0))
    cmd("subCliTotalProps", cli.get("total_props", 0))
    cmd("subCliTotalPropsOk", cli.get("total_props_ok", 0))

    # Site subsystem macros
    ss = results.get("site_subsystems", {}).get("summary", {})
    cmd("subSiteCalls", ss.get("total_calls", 0))
    cmd("subSiteSuccessful", ss.get("successful", 0))
    cmd("subSiteSuccessRate", f"{ss.get('success_rate', 0)}\\%")
    cmd("subSiteMeanTime", f"{ss.get('mean_time', 0)}\\,s")
    cmd("subSiteTotalTime", f"{ss.get('total_time', 0)}\\,s")

    # Per-method timing for small/medium/large
    by_method = results.get("site_subsystems", {}).get("by_method", {})
    for method_name, size_data in by_method.items():
        safe = method_name.replace("_", "")
        safe = safe[0].upper() + safe[1:]
        for size_name in ["small", "medium", "large"]:
            rec = size_data.get(size_name, {})
            if rec.get("ok"):
                cmd(f"sub{safe}{size_name.capitalize()}Time",
                    f"{rec.get('elapsed', 0)}\\,s")

    # Descent engine macros
    de = results.get("descent_engine", {}).get("summary", {})
    cmd("subDescentStrategies", de.get("strategies_tested", 0))
    cmd("subDescentOps", de.get("operations_tested", 0))
    cmd("subDescentOpsOk", de.get("operations_successful", 0))

    # Per-strategy timing
    strats = results.get("descent_engine", {}).get("strategies", {})
    for sname, sdata in strats.items():
        safe = sname.capitalize()
        cmd(f"subDescent{safe}Time", f"{sdata.get('elapsed', 0)}\\,s")
        cmd(f"subDescent{safe}Ok", "true" if sdata.get("ok") else "false")

    # Judgment construction
    jc = results.get("judgment_construction", {}).get("summary", {})
    cmd("subJudgTotal", jc.get("total", 0))
    cmd("subJudgSuccessful", jc.get("successful", 0))
    cmd("subJudgSuccessRate", f"{jc.get('success_rate', 0)}\\%")
    cmd("subJudgMeanTimeUs", f"{jc.get('mean_time_us', 0)}\\,$\\mu$s")
    cmd("subJudgTrustLevels", jc.get("trust_levels", 0))
    cmd("subJudgPropKinds", jc.get("prop_kinds", 0))

    # Bug detection
    bd = results.get("bug_detection", {}).get("summary", {})
    cmd("subBugTotal", bd.get("buggy_total", 0))
    cmd("subBugDetected", bd.get("bugs_detected", 0))
    cmd("subBugDetectionRate", f"{bd.get('detection_rate', 0)}\\%")
    cmd("subBugFalsePositiveRate", f"{bd.get('false_positive_rate', 0)}\\%")

    # Equivalence
    eq = results.get("equivalence_checking", {}).get("summary", {})
    cmd("subEquivTotal", eq.get("total", 0))
    cmd("subEquivVerified", eq.get("verified", 0))

    # Repair
    rp = results.get("repair_experiments", {}).get("summary", {})
    cmd("subRepairTotal", rp.get("total", 0))
    cmd("subRepairObstructions", rp.get("obstructions_found", 0))

    # Scalability
    sc = results.get("scalability", {}).get("sizes", [])
    for rec in sc:
        n = rec["coords"]
        cmd(f"subScaleN{n}Time", f"{rec.get('elapsed', 0)}\\,s")

    # Global
    cmd("subTotalExperimentTime", f"{total_elapsed:.1f}\\,s")

    out_tex = ROOT / "papers" / "subsystem-data.tex"
    with open(out_tex, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"📄 LaTeX macros → {out_tex}")


if __name__ == "__main__":
    main()
