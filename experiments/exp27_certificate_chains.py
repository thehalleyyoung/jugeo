#!/usr/bin/env python3
"""Paper 27 Experiment — Certificate Chains (compositional proof architecture).

Runs JuGeo load/descend/spec on diverse programs. Each coordinate becomes a
"certificate", descend checks form "chain verification".  Measures per-category
(Specification, Equivalence, Bug-detection) latencies.

Outputs: papers/data-paper27.tex  (LaTeX macros with \\ppTwentyseven… prefix)
Re-run:  python3 experiments/exp27_certificate_chains.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ---------------------------------------------------------------------------
# Test programs (12 diverse programs, tagged by verification category)
# ---------------------------------------------------------------------------
PROGRAMS = [
    # --- Specification category ---
    {"id": "gcd_spec", "cat": "specification", "code": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def spec_gcd(a, b):
    assert gcd(a, b) == gcd(b, a)
    assert gcd(a, 0) == a
"""},
    {"id": "sort_spec", "cat": "specification", "code": """
def insertion_sort(xs):
    result = list(xs)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result

def is_sorted(xs):
    return all(xs[i] <= xs[i+1] for i in range(len(xs)-1))
"""},
    {"id": "stack_spec", "cat": "specification", "code": """
class Stack:
    def __init__(self):
        self._items = []
    def push(self, x):
        self._items.append(x)
    def pop(self):
        return self._items.pop()
    def peek(self):
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
"""},
    {"id": "binary_search_spec", "cat": "specification", "code": """
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
"""},
    # --- Equivalence category ---
    {"id": "fib_equiv", "cat": "equivalence", "code": """
def fib_rec(n):
    if n <= 1:
        return n
    return fib_rec(n-1) + fib_rec(n-2)

def fib_iter(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

def check_equiv(n):
    return fib_rec(n) == fib_iter(n)
"""},
    {"id": "sum_equiv", "cat": "equivalence", "code": """
def sum_loop(n):
    total = 0
    for i in range(1, n+1):
        total += i
    return total

def sum_formula(n):
    return n * (n + 1) // 2

def sum_recursive(n):
    if n <= 0:
        return 0
    return n + sum_recursive(n - 1)
"""},
    {"id": "power_equiv", "cat": "equivalence", "code": """
def power_naive(base, exp):
    result = 1
    for _ in range(exp):
        result *= base
    return result

def power_fast(base, exp):
    result = 1
    while exp > 0:
        if exp % 2 == 1:
            result *= base
        base *= base
        exp //= 2
    return result
"""},
    {"id": "flatten_equiv", "cat": "equivalence", "code": """
def flatten_recursive(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten_recursive(item))
        else:
            result.append(item)
    return result

def flatten_iterative(lst):
    stack = list(reversed(lst))
    result = []
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(reversed(item))
        else:
            result.append(item)
    return result
"""},
    # --- Bug-detection category ---
    {"id": "off_by_one", "cat": "bug_detection", "code": """
def find_max(arr):
    if not arr:
        return None
    mx = arr[0]
    for i in range(1, len(arr)):
        if arr[i] > mx:
            mx = arr[i]
    return mx

def count_occurrences(arr, target):
    count = 0
    for i in range(len(arr)):
        if arr[i] == target:
            count += 1
    return count
"""},
    {"id": "null_check", "cat": "bug_detection", "code": """
def safe_divide(a, b):
    if b == 0:
        return None
    return a / b

def lookup(table, key, default=None):
    if table is None:
        return default
    return table.get(key, default)

def process(data):
    result = lookup(data, 'value')
    if result is not None:
        return result * 2
    return 0
"""},
    {"id": "resource_mgmt", "cat": "bug_detection", "code": """
class Connection:
    def __init__(self, url):
        self.url = url
        self.open = True
    def close(self):
        self.open = False
    def query(self, sql):
        if not self.open:
            raise RuntimeError("closed")
        return []

def safe_query(url, sql):
    conn = Connection(url)
    try:
        return conn.query(sql)
    finally:
        conn.close()
"""},
    {"id": "type_coerce", "cat": "bug_detection", "code": """
def parse_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0

def add_values(a, b):
    return parse_int(a) + parse_int(b)

def format_record(name, age, score):
    return f"{name}: age={parse_int(age)}, score={parse_int(score)}"
"""},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_jugeo(*args, timeout=30):
    """Run jugeo CLI and return list of parsed JSON objects."""
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    categories = {"specification": [], "equivalence": [], "bug_detection": []}
    cat_labels = {
        "specification": "Specification",
        "equivalence": "Equivalence",
        "bug_detection": "Bug detection",
    }

    print("Paper 27 — Certificate Chains Experiment")
    print("=" * 60)

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])
        cat = prog["cat"]
        timings = []

        try:
            # load — gather certificate (coordinate) count
            t0 = time.perf_counter()
            load_objs = run_jugeo("load", tmp)
            t_load = (time.perf_counter() - t0) * 1000
            load_data = load_objs[0] if load_objs else {}
            summary = load_data.get("summary", load_data)
            n_coords = summary.get("coordinates", 0)
            n_morphisms = summary.get("morphisms", 0)
            timings.append(t_load)

            # descend — chain verification
            t0 = time.perf_counter()
            desc_objs = run_jugeo("descend", tmp)
            t_descend = (time.perf_counter() - t0) * 1000
            desc = desc_objs[0] if desc_objs else {}
            verdict = desc.get("verdict", "unknown")
            local_sections = desc.get("local_sections", 0)
            overlaps = desc.get("overlap_conditions_checked", 0)
            timings.append(t_descend)

            # spec — specification satisfaction
            t0 = time.perf_counter()
            spec_objs = run_jugeo("spec", tmp)
            t_spec = (time.perf_counter() - t0) * 1000
            timings.append(t_spec)

            total_ms = sum(timings)

            categories[cat].append({
                "id": prog["id"],
                "coords": n_coords,
                "morphisms": n_morphisms,
                "verdict": verdict,
                "local_sections": local_sections,
                "overlaps": overlaps,
                "timings": timings,
                "total_ms": total_ms,
            })

            print(f"  {prog['id']:20s}  [{cat:15s}]  coords={n_coords:3d}  "
                  f"verdict={verdict:10s}  time={total_ms:8.2f}ms")
        except Exception as e:
            print(f"  {prog['id']:20s}  ERROR: {e}")
            categories[cat].append({
                "id": prog["id"], "coords": 0, "morphisms": 0,
                "verdict": "error", "local_sections": 0, "overlaps": 0,
                "timings": [0], "total_ms": 0,
            })
        finally:
            cleanup(tmp)

    # ------------------------------------------------------------------
    # Aggregate per category
    # ------------------------------------------------------------------
    P = "ppTwentyseven"
    tex = [
        "% data-paper27.tex — AUTO-GENERATED by exp27_certificate_chains.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp27_certificate_chains.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    all_times = []
    print("\n" + "=" * 60)
    print(f"{'Category':<16} {'Count':>6} {'Mean(ms)':>10} {'P99(ms)':>10} {'Max(ms)':>10}")
    print("-" * 60)

    for cat_key, label in [("specification", "Spec"),
                           ("equivalence", "Equiv"),
                           ("bug_detection", "BugDet")]:
        entries = categories[cat_key]
        times = [e["total_ms"] for e in entries]
        all_times.extend(times)
        count = len(entries)

        if times:
            mean_t = statistics.mean(times)
            sorted_t = sorted(times)
            p99_idx = max(0, int(len(sorted_t) * 0.99) - 1)
            p99_t = sorted_t[p99_idx] if sorted_t else 0
            max_t = max(times)
        else:
            mean_t = p99_t = max_t = 0

        full_label = cat_labels[cat_key]
        print(f"  {full_label:<14} {count:>6} {mean_t:>10.2f} {p99_t:>10.2f} {max_t:>10.2f}")

        m(f"{label}Count", count)
        m(f"{label}Mean", ms_str(mean_t))
        m(f"{label}Pnn", ms_str(p99_t))
        m(f"{label}Max", ms_str(max_t))

    # All-category aggregate
    total_count = len(all_times)
    if all_times:
        all_mean = statistics.mean(all_times)
        all_sorted = sorted(all_times)
        all_p99_idx = max(0, int(len(all_sorted) * 0.99) - 1)
        all_p99 = all_sorted[all_p99_idx]
        all_max = max(all_times)
    else:
        all_mean = all_p99 = all_max = 0

    print(f"  {'All':<14} {total_count:>6} {all_mean:>10.2f} {all_p99:>10.2f} {all_max:>10.2f}")

    m("AllCount", total_count)
    m("AllMean", ms_str(all_mean))
    m("AllPnn", ms_str(all_p99))
    m("AllMax", ms_str(all_max))

    # Extra certificate-chain metrics
    all_entries = [e for cat in categories.values() for e in cat]
    total_coords = sum(e["coords"] for e in all_entries)
    total_morphisms = sum(e["morphisms"] for e in all_entries)
    verified_count = sum(1 for e in all_entries if e["verdict"] == "verified")
    total_sections = sum(e["local_sections"] for e in all_entries)
    total_overlaps = sum(e["overlaps"] for e in all_entries)
    avg_chain_len = (statistics.mean([e["local_sections"] for e in all_entries])
                     if all_entries else 0)

    m("TotalPrograms", len(PROGRAMS))
    m("TotalCertificates", total_coords)
    m("TotalMorphisms", total_morphisms)
    m("VerifiedCount", verified_count)
    m("VerifiedRate", pct_str(verified_count / max(total_count, 1)))
    m("TotalSections", total_sections)
    m("TotalOverlaps", total_overlaps)
    m("AvgChainLen", f"{avg_chain_len:.1f}")

    # Write LaTeX
    tex_path = os.path.join(ROOT, "papers", "data-paper27.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper27.json")
    results = {cat_key: categories[cat_key] for cat_key in categories}
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
