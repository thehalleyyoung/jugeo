#!/usr/bin/env python3
"""Paper 29 Experiment — Repair Semantics (program repair suggestions).

Runs JuGeo repair on buggy programs.  Classifies repairs into Spec-bug,
Impl-bug, and Coherence-bug categories.  Measures repair success rates,
iteration counts, and repair-type distribution.

Outputs: papers/data-paper29.tex  (LaTeX macros with \\ppTwentynine… prefix)
Re-run:  python3 experiments/exp29_repair_semantics.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ---------------------------------------------------------------------------
# Buggy test programs (10 programs with seeded defects, tagged by category)
#   - Spec-bug:      specification is wrong / incomplete
#   - Impl-bug:      implementation diverges from correct spec
#   - Coherence-bug: internal inconsistency between components
# ---------------------------------------------------------------------------
PROGRAMS = [
    # --- Spec-bug category ---
    {"id": "spec_missing_edge", "cat": "spec", "code": """
def factorial(n):
    '''Returns n! for non-negative n.'''
    # BUG: no guard for negative n
    if n == 0:
        return 1
    return n * factorial(n - 1)
"""},
    {"id": "spec_wrong_contract", "cat": "spec", "code": """
def divide(a, b):
    '''Divides a by b, returns float.'''
    # BUG: spec says float but returns int when both are int
    return a // b

def safe_sqrt(x):
    '''Returns sqrt of non-negative x.'''
    # BUG: no check for negative
    return x ** 0.5
"""},
    {"id": "spec_incomplete_pre", "cat": "spec", "code": """
def get_element(lst, idx):
    '''Returns element at index idx.'''
    # BUG: no bounds check
    return lst[idx]

def parse_date(s):
    '''Parses date string YYYY-MM-DD.'''
    parts = s.split('-')
    return int(parts[0]), int(parts[1]), int(parts[2])
"""},
    # --- Impl-bug category ---
    {"id": "impl_off_by_one", "cat": "impl", "code": """
def range_sum(start, end):
    '''Sum of integers from start to end inclusive.'''
    total = 0
    for i in range(start, end):  # BUG: should be end+1
        total += i
    return total
"""},
    {"id": "impl_wrong_operator", "cat": "impl", "code": """
def is_even(n):
    return n % 2 == 1  # BUG: should be == 0

def clamp(value, lo, hi):
    if value < lo:
        return lo
    if value < hi:  # BUG: should be >
        return hi
    return value
"""},
    {"id": "impl_wrong_return", "cat": "impl", "code": """
def find_max(arr):
    if not arr:
        return None
    best = arr[0]
    for x in arr[1:]:
        if x > best:
            best = x
    return arr[0]  # BUG: should return best

def absolute(x):
    if x < 0:
        return x  # BUG: should be -x
    return x
"""},
    {"id": "impl_missing_update", "cat": "impl", "code": """
def count_vowels(s):
    count = 0
    for ch in s:
        if ch.lower() in 'aeiou':
            pass  # BUG: forgot count += 1
    return count

def reverse_list(lst):
    result = []
    for item in lst:
        result.append(item)
    return result  # BUG: not reversed
"""},
    # --- Coherence-bug category ---
    {"id": "coh_state_mismatch", "cat": "coherence", "code": """
class Counter:
    def __init__(self):
        self.value = 0
        self.history = []

    def increment(self):
        self.value += 1
        # BUG: doesn't update history

    def decrement(self):
        self.value -= 1
        self.history.append(self.value)

    def get_history(self):
        return self.history
"""},
    {"id": "coh_interface_clash", "cat": "coherence", "code": """
class Encoder:
    def encode(self, data):
        return data.encode('utf-8')

class Decoder:
    def decode(self, data):
        return data.decode('ascii')  # BUG: encoding mismatch with Encoder

class Pipeline:
    def __init__(self):
        self.enc = Encoder()
        self.dec = Decoder()

    def roundtrip(self, text):
        encoded = self.enc.encode(text)
        return self.dec.decode(encoded)
"""},
    {"id": "coh_invariant_break", "cat": "coherence", "code": """
class SortedList:
    def __init__(self):
        self._data = []

    def insert(self, x):
        self._data.append(x)  # BUG: doesn't maintain sorted order

    def contains(self, x):
        lo, hi = 0, len(self._data) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._data[mid] == x:
                return True
            elif self._data[mid] < x:
                lo = mid + 1
            else:
                hi = mid - 1
        return False  # binary search assumes sorted
"""},
]

# ---------------------------------------------------------------------------
# Repair-type canonical names → macro labels
# ---------------------------------------------------------------------------
REPAIR_TYPES = [
    ("FIX_IMPLEMENTATION",      "FixImpl"),
    ("STRENGTHEN_PRECONDITION", "StrengthenPre"),
    ("REFINE_FUNCTION_SPEC",    "RefineSpec"),
    ("ADD_INVARIANT",           "AddInvariant"),
    ("SPLIT_COVER",             "SplitCover"),
    ("WEAKEN_POSTCONDITION",    "WeakenPost"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_jugeo(*args, timeout=30):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=ROOT, timeout=timeout)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo")]
    text = "\n".join(lines)
    decoder = json.JSONDecoder()
    objects = []
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


def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False,
                                    dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def pct_str(val):
    return f"{val * 100:.1f}\\%"


def ms_str(val):
    return f"{val:.2f}\\,\\text{{ms}}"


CAT_LABELS = {"spec": "Spec", "impl": "Impl", "coherence": "Coherence"}
CAT_DISPLAY = {"spec": "Spec-bug", "impl": "Impl-bug", "coherence": "Coherence-bug"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from jugeo.geometry import SiteBuilder

    print("Paper 29 — Repair Semantics Experiment")
    print("=" * 60)

    cat_results = {"spec": [], "impl": [], "coherence": []}
    repair_type_counts = {rt: 0 for rt, _ in REPAIR_TYPES}
    total_repairs_all = 0

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])
        cat = prog["cat"]

        try:
            # Run repair CLI
            t0 = time.perf_counter()
            objs = run_jugeo("repair", tmp)
            wall_s = time.perf_counter() - t0

            repair_data = objs[0] if objs else {}
            if isinstance(repair_data, list):
                repair_data = repair_data[0] if repair_data else {}

            repair_count = repair_data.get("repair_count", 0)
            repairs = repair_data.get("repairs", [])
            coverage = repair_data.get("coverage", {})
            total_obstructions = coverage.get("total_obstructions", 0)
            repairs_proposed = coverage.get("repairs_proposed", repair_count)

            # Also run bugs to see obstruction count
            bug_objs = run_jugeo("bugs", tmp)
            bug_data = bug_objs[0] if bug_objs else {}
            if isinstance(bug_data, list):
                bug_data = bug_data[0] if bug_data else {}
            obstruction_count = bug_data.get("obstruction_count",
                                             bug_data.get("count", 0))

            # Use SiteBuilder repair_semantics for iteration data
            site = SiteBuilder(prog["code"]).build()
            rs = site.repair_semantics()
            iterations = rs.get("iterations", rs.get("repair_rounds", 1))
            if isinstance(iterations, list):
                iterations = len(iterations)
            iterations = max(iterations, 1)

            # Classify repair types
            for rep in repairs:
                rtype = rep.get("type", rep.get("repair_type", "FIX_IMPLEMENTATION"))
                if rtype in repair_type_counts:
                    repair_type_counts[rtype] += 1
                    total_repairs_all += 1
                else:
                    repair_type_counts["FIX_IMPLEMENTATION"] += 1
                    total_repairs_all += 1

            # If no explicit repairs list, count repair_count as FIX_IMPLEMENTATION
            if not repairs and repair_count > 0:
                repair_type_counts["FIX_IMPLEMENTATION"] += repair_count
                total_repairs_all += repair_count

            success = repair_count > 0 or repairs_proposed > 0

            cat_results[cat].append({
                "id": prog["id"],
                "repair_count": repair_count,
                "obstructions": total_obstructions,
                "iterations": iterations,
                "success": success,
                "wall_ms": wall_s * 1000,
            })

            print(f"  {prog['id']:25s}  [{CAT_DISPLAY[cat]:14s}]  "
                  f"repairs={repair_count:2d}  iter={iterations:2d}  "
                  f"obstr={obstruction_count:2d}  time={wall_s*1000:.1f}ms")

        except Exception as e:
            print(f"  {prog['id']:25s}  ERROR: {e}")
            cat_results[cat].append({
                "id": prog["id"], "repair_count": 0, "obstructions": 0,
                "iterations": 1, "success": False, "wall_ms": 0,
            })
        finally:
            cleanup(tmp)

    # ------------------------------------------------------------------
    # Build LaTeX macros
    # ------------------------------------------------------------------
    P = "ppTwentynine"
    tex = [
        "% data-paper29.tex — AUTO-GENERATED by exp29_repair_semantics.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp29_repair_semantics.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    # --- Table 1: Repair success rates ---
    print("\n" + "=" * 60)
    print(f"{'Category':<16} {'Count':>6} {'Success%':>9} {'AvgRepairs':>11}")
    print("-" * 60)

    total_count = 0
    total_success = 0

    for cat_key in ["spec", "impl", "coherence"]:
        entries = cat_results[cat_key]
        count = len(entries)
        successes = sum(1 for e in entries if e["success"])
        total_count += count
        total_success += successes
        success_rate = successes / max(count, 1)
        avg_repairs = (statistics.mean([e["repair_count"] for e in entries])
                       if entries else 0)

        label = CAT_LABELS[cat_key]
        print(f"  {CAT_DISPLAY[cat_key]:<14} {count:>6} {success_rate*100:>8.1f}% {avg_repairs:>11.1f}")

        m(f"{label}Count", count)
        m(f"{label}SuccessRate", pct_str(success_rate))
        m(f"{label}AvgRepairs", f"{avg_repairs:.1f}")

    overall_success = total_success / max(total_count, 1)
    print(f"  {'Overall':<14} {total_count:>6} {overall_success*100:>8.1f}%")

    m("RepairTotal", total_count)
    m("RepairSuccessRate", pct_str(overall_success))
    m("RepairSuccessCount", total_success)

    # --- Table 2: Iteration counts ---
    print("\n" + "=" * 60)
    print(f"{'Category':<16} {'Avg Iterations':>15}")
    print("-" * 40)

    all_iters = []
    for cat_key in ["spec", "impl", "coherence"]:
        entries = cat_results[cat_key]
        iters = [e["iterations"] for e in entries]
        all_iters.extend(iters)
        avg_iter = statistics.mean(iters) if iters else 0

        label = CAT_LABELS[cat_key]
        print(f"  {CAT_DISPLAY[cat_key]:<14} {avg_iter:>15.1f}")

        m(f"{label}AvgIter", f"{avg_iter:.1f}")

    overall_iter = statistics.mean(all_iters) if all_iters else 0
    print(f"  {'Overall':<14} {overall_iter:>15.1f}")
    m("OverallAvgIter", f"{overall_iter:.1f}")

    # --- Table 3: Repair type distribution ---
    print("\n" + "=" * 60)
    print(f"{'Repair Type':<30} {'Count':>6} {'Fraction':>9}")
    print("-" * 50)

    # Ensure at least 1 to avoid div-by-zero
    denom = max(total_repairs_all, 1)

    for rtype, macro_label in REPAIR_TYPES:
        cnt = repair_type_counts[rtype]
        frac = cnt / denom
        print(f"  {rtype:<28} {cnt:>6} {frac:>8.1%}")

        m(f"{macro_label}Count", cnt)
        m(f"{macro_label}Frac", pct_str(frac))

    m("TotalRepairActions", total_repairs_all)
    m("TotalPrograms", len(PROGRAMS))

    # Write LaTeX
    tex_path = os.path.join(ROOT, "papers", "data-paper29.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper29.json")
    results = {
        "categories": {k: v for k, v in cat_results.items()},
        "repair_types": repair_type_counts,
    }
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
