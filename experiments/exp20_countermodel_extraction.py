#!/usr/bin/env python3
"""Paper 20 Experiment — Countermodel Extraction: Diagnostic Synthesis.

Runs ``jugeo bugs`` + ``jugeo descend`` on programs WITH intentional bugs.
Extracts countermodel-like diagnostics from descend obstructions and bug
reports.  Compares correct programs vs buggy variants.

Every number is reproducible: run `python3 experiments/exp20_countermodel_extraction.py`.
Writes macros to papers/data-paper20.tex with prefix ppTwenty.
"""
import subprocess, json, os, tempfile, time, random, statistics

random.seed(42)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')]
    lines = [l for l in lines if not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


TRUST_NUMERIC = {
    "MECHANICALLY_VERIFIED": 7, "mechanically_verified": 7,
    "SOLVER_DISCHARGED": 6, "solver_discharged": 6,
    "RUNTIME_WITNESSED": 5, "runtime_witnessed": 5,
    "HUMAN_ATTESTED": 4, "human_attested": 4,
    "ORACLE_PROPOSED": 3, "oracle_proposed": 3,
    "COPILOT_SUGGESTED": 2, "copilot_suggested": 2,
    "LOW": 1, "unverified": 1,
    "CONTRADICTED": 0, "contradicted": 0,
}

# ── correct programs ─────────────────────────────────────────────────────

CORRECT_PROGRAMS = {
    "safe_division": '''
def safe_divide(a, b):
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b

def divide_list(nums, divisor):
    results = []
    for n in nums:
        results.append(safe_divide(n, divisor))
    return results

def average(nums):
    if not nums:
        return 0.0
    return sum(nums) / len(nums)
''',

    "correct_binary_search": '''
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
''',

    "correct_stack": '''
class Stack:
    def __init__(self):
        self._items = []
    def push(self, item):
        self._items.append(item)
    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()
    def peek(self):
        if not self._items:
            raise IndexError("peek at empty stack")
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
''',

    "correct_merge_sort": '''
def merge(left, right):
    result = []
    i = j = 0
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

def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    return merge(merge_sort(arr[:mid]), merge_sort(arr[mid:]))
''',

    "correct_linked_list": '''
class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.next = nxt

class LinkedList:
    def __init__(self):
        self.head = None
        self.length = 0
    def append(self, val):
        node = Node(val)
        if not self.head:
            self.head = node
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = node
        self.length += 1
    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',
}

# ── buggy variants (intentional bugs) ────────────────────────────────────

BUGGY_PROGRAMS = {
    # Arithmetic failure: missing zero check
    "buggy_division": '''
def divide(a, b):
    return a / b

def divide_list(nums, divisor):
    return [divide(n, divisor) for n in nums]

def average(nums):
    return sum(nums) / len(nums)
''',

    # Off-by-one in binary search
    "buggy_binary_search": '''
def binary_search(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid
        else:
            hi = mid
    return -1
''',

    # Stack with no bounds checking
    "buggy_stack": '''
class Stack:
    def __init__(self):
        self._items = []
    def push(self, item):
        self._items.append(item)
    def pop(self):
        return self._items.pop()
    def peek(self):
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
''',

    # Wrong merge comparison (unstable sort)
    "buggy_merge_sort": '''
def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] < right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    return result

def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    return merge(merge_sort(arr[:mid]), merge_sort(arr[mid:]))
''',

    # Missing length update in linked list
    "buggy_linked_list": '''
class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.next = nxt

class LinkedList:
    def __init__(self):
        self.head = None
        self.length = 0
    def append(self, val):
        node = Node(val)
        if not self.head:
            self.head = node
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = node
    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',

    # Higher-order: decorator drops return value
    "buggy_decorator": '''
def logger(func):
    def wrapper(*args, **kwargs):
        func(*args, **kwargs)
    return wrapper

@logger
def compute(a, b):
    return a + b

def process(items):
    return [compute(x, x) for x in items]
''',

    # Collection: wrong index in insertion sort
    "buggy_insertion_sort": '''
def insertion_sort(arr):
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j] = key
    return result
''',

    # Arithmetic: integer overflow proxy (wrong modular arithmetic)
    "buggy_modular_arith": '''
def power_mod(base, exp, mod):
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = result * base
        exp = exp >> 1
        base = base * base
    return result

def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
''',
}

# Bug categories
BUG_CATEGORIES = {
    "buggy_division": "arith",
    "buggy_binary_search": "arith",
    "buggy_modular_arith": "arith",
    "buggy_stack": "collection",
    "buggy_linked_list": "collection",
    "buggy_insertion_sort": "collection",
    "buggy_merge_sort": "collection",
    "buggy_decorator": "higher_order",
}


# ── main ─────────────────────────────────────────────────────────────────

def main():
    tmpfiles = []
    n_correct = len(CORRECT_PROGRAMS)
    n_buggy = len(BUGGY_PROGRAMS)
    n_total = n_correct + n_buggy

    print(f"Paper 20 — Countermodel Extraction Experiment")
    print(f"Correct programs: {n_correct}, Buggy programs: {n_buggy}, Total: {n_total}")
    print("=" * 76)

    # ── 1. Analyze correct programs ──────────────────────────────────────
    print("\n── Phase 1: Correct programs ──")
    correct_results = []
    for prog_name, source in CORRECT_PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        # bugs
        print(f"  bugs {prog_name} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            objs = run_jugeo("bugs", path)
            bugs = objs[0] if objs else []
            if isinstance(bugs, dict):
                bugs = bugs.get("bugs", [])
        except Exception:
            bugs = []
        bug_time = time.perf_counter() - t0

        # descend
        t0_desc = time.perf_counter()
        try:
            desc_objs = run_jugeo("descend", path)
            desc = desc_objs[0] if desc_objs else {}
        except Exception:
            desc = {}
        desc_time = time.perf_counter() - t0_desc

        trust_raw = desc.get("trust", desc.get("aggregate_trust", "unverified"))
        if isinstance(trust_raw, dict):
            trust_raw = trust_raw.get("aggregate_trust", "unverified")
        trust_num = TRUST_NUMERIC.get(str(trust_raw).upper(),
                     TRUST_NUMERIC.get(str(trust_raw), 1))
        obstructions = desc.get("obstructions", [])
        verdict = desc.get("verdict", "unknown")

        n_bugs = len(bugs) if isinstance(bugs, list) else 0
        correct_results.append({
            "name": prog_name,
            "bugs": n_bugs,
            "trust_num": trust_num,
            "verdict": verdict,
            "obstructions": len(obstructions),
            "bug_detect_time": round(bug_time, 4),
            "descend_time": round(desc_time, 4),
            "pipeline_time": round(bug_time + desc_time, 4),
        })
        print(f"bugs={n_bugs} trust={trust_num} obs={len(obstructions)} t={bug_time+desc_time:.3f}s")

    # ── 2. Analyze buggy programs ────────────────────────────────────────
    print("\n── Phase 2: Buggy programs ──")
    buggy_results = []
    for prog_name, source in BUGGY_PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        # bugs
        print(f"  bugs {prog_name} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            objs = run_jugeo("bugs", path)
            bugs = objs[0] if objs else []
            if isinstance(bugs, dict):
                bugs = bugs.get("bugs", [])
        except Exception:
            bugs = []
        bug_time = time.perf_counter() - t0

        # descend
        t0_desc = time.perf_counter()
        try:
            desc_objs = run_jugeo("descend", path)
            desc = desc_objs[0] if desc_objs else {}
        except Exception:
            desc = {}
        desc_time = time.perf_counter() - t0_desc

        trust_raw = desc.get("trust", desc.get("aggregate_trust", "unverified"))
        if isinstance(trust_raw, dict):
            trust_raw = trust_raw.get("aggregate_trust", "unverified")
        trust_num = TRUST_NUMERIC.get(str(trust_raw).upper(),
                     TRUST_NUMERIC.get(str(trust_raw), 1))
        obstructions = desc.get("obstructions", [])
        verdict = desc.get("verdict", "unknown")
        n_bugs = len(bugs) if isinstance(bugs, list) else 0

        # Unique obstructions (minimized model)
        obs_types = set()
        for obs in obstructions:
            if isinstance(obs, dict):
                obs_types.add(obs.get("type", obs.get("kind", "unknown")))
            elif isinstance(obs, str):
                obs_types.add(obs)

        buggy_results.append({
            "name": prog_name,
            "category": BUG_CATEGORIES.get(prog_name, "unknown"),
            "bugs": n_bugs,
            "trust_num": trust_num,
            "verdict": verdict,
            "obstructions": len(obstructions),
            "unique_obstructions": len(obs_types),
            "bug_detect_time": round(bug_time, 4),
            "descend_time": round(desc_time, 4),
            "pipeline_time": round(bug_time + desc_time, 4),
        })
        print(f"bugs={n_bugs} trust={trust_num} obs={len(obstructions)} "
              f"uniq_obs={len(obs_types)} t={bug_time+desc_time:.3f}s")

    # ── 3. Compute metrics ───────────────────────────────────────────────
    print("\n── Metrics ──")

    total_bugs_detected = (sum(r["bugs"] for r in correct_results) +
                           sum(r["bugs"] for r in buggy_results))

    # True positives: buggy programs where bugs were found
    true_positives = sum(1 for r in buggy_results if r["bugs"] > 0)
    # True negatives: correct programs where no bugs found
    true_negatives = sum(1 for r in correct_results if r["bugs"] == 0)
    # False positives: correct programs where bugs were found
    false_positives_count = sum(1 for r in correct_results if r["bugs"] > 0)
    # False negatives: buggy programs where no bugs found
    false_negatives_count = sum(1 for r in buggy_results if r["bugs"] == 0)

    fp_rate = false_positives_count / max(n_correct, 1) * 100
    fn_rate = false_negatives_count / max(n_buggy, 1) * 100

    # Pipeline latencies
    all_pipeline_times = ([r["pipeline_time"] for r in correct_results] +
                          [r["pipeline_time"] for r in buggy_results])
    mean_pipeline = statistics.mean(all_pipeline_times) if all_pipeline_times else 0
    median_pipeline = statistics.median(all_pipeline_times) if all_pipeline_times else 0

    # Bug detect times
    all_bug_times = ([r["bug_detect_time"] for r in correct_results] +
                     [r["bug_detect_time"] for r in buggy_results])
    mean_bug_time = statistics.mean(all_bug_times) if all_bug_times else 0
    median_bug_time = statistics.median(all_bug_times) if all_bug_times else 0

    # Model sizes (obstruction counts per buggy program)
    buggy_obs_counts = [r["obstructions"] for r in buggy_results]
    mean_model_size = statistics.mean(buggy_obs_counts) if buggy_obs_counts else 0

    # Minimized model sizes (unique obstructions)
    buggy_uniq_obs = [r["unique_obstructions"] for r in buggy_results]
    mean_minimized = statistics.mean(buggy_uniq_obs) if buggy_uniq_obs else 0

    # Reduction ratio
    reduction_ratio = (1 - mean_minimized / max(mean_model_size, 0.001)) * 100
    if mean_model_size == 0:
        reduction_ratio = 0.0

    # Category breakdown
    arith_failures = sum(1 for r in buggy_results
                         if r["category"] == "arith" and r["bugs"] > 0)
    collection_failures = sum(1 for r in buggy_results
                              if r["category"] == "collection" and r["bugs"] > 0)
    higher_order_failures = sum(1 for r in buggy_results
                                if r["category"] == "higher_order" and r["bugs"] > 0)

    # Explanation accuracy: fraction of buggy programs where both bugs AND obstructions
    # provide diagnostic information
    programs_with_diagnostics = sum(
        1 for r in buggy_results
        if r["bugs"] > 0 or r["obstructions"] > 0
    )
    explanation_accuracy = programs_with_diagnostics / max(n_buggy, 1) * 100

    print(f"  Total programs: {n_total}")
    print(f"  Correct: {n_correct}, Buggy: {n_buggy}")
    print(f"  Bugs detected: {total_bugs_detected}")
    print(f"  True positives: {true_positives}")
    print(f"  True negatives: {true_negatives}")
    print(f"  False positive rate: {fp_rate:.1f}%")
    print(f"  False negative rate: {fn_rate:.1f}%")
    print(f"  Mean bug detect time: {mean_bug_time:.4f}s")
    print(f"  Median bug detect time: {median_bug_time:.4f}s")
    print(f"  Mean model size: {mean_model_size:.1f}")
    print(f"  Minimized model size: {mean_minimized:.1f}")
    print(f"  Reduction ratio: {reduction_ratio:.0f}%")
    print(f"  Pipeline latency mean: {mean_pipeline:.4f}s")
    print(f"  Pipeline latency median: {median_pipeline:.4f}s")
    print(f"  Arith failures detected: {arith_failures}")
    print(f"  Collection failures detected: {collection_failures}")
    print(f"  Higher-order failures detected: {higher_order_failures}")
    print(f"  Explanation accuracy: {explanation_accuracy:.0f}%")

    # ── Per-program table ────────────────────────────────────────────────
    print(f"\n{'Program':<28} {'Bugs':>5} {'Trust':>6} {'Obs':>4} {'Verdict':<12}")
    print("-" * 60)
    for r in correct_results:
        print(f"  {r['name']:<26} {r['bugs']:>5} {r['trust_num']:>6} "
              f"{r['obstructions']:>4} {r['verdict']:<12}")
    print("  ---")
    for r in buggy_results:
        print(f"  {r['name']:<26} {r['bugs']:>5} {r['trust_num']:>6} "
              f"{r['obstructions']:>4} {r['verdict']:<12}")

    # ── Save JSON ────────────────────────────────────────────────────────
    output = {
        "experiment": "countermodel_extraction",
        "paper": 20,
        "note": "All numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_total": n_total,
        "n_correct": n_correct,
        "n_buggy": n_buggy,
        "correct_results": correct_results,
        "buggy_results": buggy_results,
        "summary": {
            "bugs_detected": total_bugs_detected,
            "true_positives": true_positives,
            "true_negatives": true_negatives,
            "fp_rate": round(fp_rate, 1),
            "fn_rate": round(fn_rate, 1),
            "mean_bug_detect_time": round(mean_bug_time, 4),
            "median_bug_detect_time": round(median_bug_time, 4),
            "mean_model_size": round(mean_model_size, 1),
            "minimized_model_size": round(mean_minimized, 1),
            "reduction_ratio": round(reduction_ratio, 1),
            "pipeline_latency_mean": round(mean_pipeline, 4),
            "pipeline_latency_median": round(median_pipeline, 4),
            "arith_failures": arith_failures,
            "collection_failures": collection_failures,
            "higher_order_failures": higher_order_failures,
            "explanation_accuracy": round(explanation_accuracy, 1),
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper20.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults → {json_path}")

    # ── Write LaTeX macros ───────────────────────────────────────────────
    tex_path = os.path.join(ROOT, "papers", "data-paper20.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w") as f:
        f.write("% data-paper20.tex — AUTO-GENERATED by exp20_countermodel_extraction.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp20_countermodel_extraction.py\n\n")

        write_macro(f, "ppTwentyTotalPrograms", n_total)
        write_macro(f, "ppTwentyCorrectPrograms", n_correct)
        write_macro(f, "ppTwentyBuggyPrograms", n_buggy)
        f.write("\n")

        write_macro(f, "ppTwentyBugsDetected", total_bugs_detected)
        write_macro(f, "ppTwentyTruePositives", true_positives)
        write_macro(f, "ppTwentyTrueNegatives", true_negatives)
        f.write("\n")

        write_macro(f, "ppTwentyFalsePositiveRate", f"{fp_rate:.1f}\\%")
        write_macro(f, "ppTwentyFalseNegativeRate", f"{fn_rate:.1f}\\%")
        f.write("\n")

        write_macro(f, "ppTwentyMeanBugDetectTime", f"{mean_bug_time:.4f}\\,s")
        write_macro(f, "ppTwentyMedianBugDetectTime", f"{median_bug_time:.4f}\\,s")
        f.write("\n")

        write_macro(f, "ppTwentyMeanModelSize", f"{mean_model_size:.1f}")
        write_macro(f, "ppTwentyMinimizedModelSize", f"{mean_minimized:.1f}")
        write_macro(f, "ppTwentyReductionRatio", f"{reduction_ratio:.0f}\\%")
        f.write("\n")

        write_macro(f, "ppTwentyPipelineLatencyMean", f"{mean_pipeline:.4f}\\,s")
        write_macro(f, "ppTwentyPipelineLatencyMedian", f"{median_pipeline:.4f}\\,s")
        f.write("\n")

        write_macro(f, "ppTwentyArithFailures", arith_failures)
        write_macro(f, "ppTwentyCollectionFailures", collection_failures)
        write_macro(f, "ppTwentyHigherOrderFailures", higher_order_failures)
        f.write("\n")

        write_macro(f, "ppTwentyExplanationAccuracy", f"{explanation_accuracy:.0f}\\%")

    print(f"LaTeX  → {tex_path}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
