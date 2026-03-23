#!/usr/bin/env python3
"""Paper 47 Experiment — Specification Satisfaction tiers and incremental checking.

Measures composition scaling: for k = 1..5 incremental specification levels,
reports median total time and marginal per-Spec_k cost across 10 programs.

Outputs: papers/data-paper47.tex  (LaTeX macros with \\ppFortySeven… prefix)
Re-run:  python3 experiments/exp47_spec_satisfaction.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def fmt_time(secs):
    if secs < 0.001: return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0: return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"


def fmt_pct(val):
    return f"{val*100:.1f}\\%"


def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0


def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 10 diverse programs
# ---------------------------------------------------------------------------
PROGRAMS = [
    {"name": "gcd", "code": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
"""},
    {"name": "binary_search", "code": """
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
    {"name": "merge_sort", "code": """
def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    merged, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i]); i += 1
        else:
            merged.append(right[j]); j += 1
    return merged + left[i:] + right[j:]
"""},
    {"name": "fibonacci", "code": """
def fibonacci(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""},
    {"name": "stack", "code": """
class Stack:
    def __init__(self):
        self._items = []
    def push(self, x):
        self._items.append(x)
    def pop(self):
        if not self._items:
            raise IndexError("empty")
        return self._items.pop()
    def peek(self):
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
"""},
    {"name": "flatten", "code": """
def flatten(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result
"""},
    {"name": "matrix_mult", "code": """
def matrix_mult(A, B):
    n = len(A)
    m = len(B[0])
    k = len(B)
    C = [[0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            for p in range(k):
                C[i][j] += A[i][p] * B[p][j]
    return C
"""},
    {"name": "dijkstra", "code": """
import heapq
def dijkstra(graph, start):
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float('inf')):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float('inf')):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist
"""},
    {"name": "knapsack", "code": """
def knapsack(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i-1][w]
            if weights[i-1] <= w:
                dp[i][w] = max(dp[i][w], dp[i-1][w - weights[i-1]] + values[i-1])
    return dp[n][capacity]
"""},
    {"name": "prime_sieve", "code": """
def sieve_of_eratosthenes(limit):
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, limit + 1, i):
                is_prime[j] = False
    return [i for i in range(limit + 1) if is_prime[i]]
"""},
]

MAX_K = 5  # specification levels 1..5


def main():
    from jugeo.geometry import SiteBuilder
    from jugeo.maturity import CyclicSystemCoordinator

    # per_k[k] = {"total_times": [...], "marginal_times": [...]}
    per_k = {k: {"total_times": [], "marginal_times": []} for k in range(1, MAX_K + 1)}
    all_sat_levels = []

    for prog in PROGRAMS:
        tmp = write_temp_py(prog["code"])
        print(f"  Program: {prog['name']}")

        # Build site and query specification satisfaction subsystem
        site = SiteBuilder(prog["code"]).build()
        spec_info = site.specification_satisfaction()

        # Run evaluate CLI for per-program verification baseline
        ev_objs = run_jugeo("evaluate", tmp)
        ev = ev_objs[0] if ev_objs else {}

        # Incremental specification checking via maturity cycles k=1..5
        coord = CyclicSystemCoordinator.create(prog["name"] + "-spec")
        prev_total = 0.0

        for k in range(1, MAX_K + 1):
            t0 = time.perf_counter()
            record, transitions = coord.run_full_cycle({
                "source": prog["code"],
                "spec_level": k,
            })
            cycle_time = time.perf_counter() - t0

            metrics = coord.get_metrics().to_dict()
            total_time = metrics["mean_cycle_duration"] * metrics["total_cycles"]
            marginal = total_time - prev_total

            # Also include real wall-clock cost of the CLI evaluate
            t1 = time.perf_counter()
            run_jugeo("evaluate", tmp)
            eval_time = time.perf_counter() - t1

            combined_total = cycle_time + eval_time / MAX_K
            combined_marginal = combined_total - (prev_total if k > 1 else 0.0)

            per_k[k]["total_times"].append(combined_total)
            per_k[k]["marginal_times"].append(combined_marginal)
            prev_total = combined_total

            print(f"    k={k}: total={combined_total:.4f}s  marginal={combined_marginal:.4f}s")

        # Satisfaction level: trust score after all cycles
        final_metrics = coord.get_metrics().to_dict()
        sat_level = final_metrics.get("mean_trust_score", 0.0)
        all_sat_levels.append(sat_level)

        cleanup(tmp)

    # Aggregate per k
    k_agg = {}
    for k in range(1, MAX_K + 1):
        k_agg[k] = {
            "median_total": safe_median(per_k[k]["total_times"]),
            "median_marginal": safe_median(per_k[k]["marginal_times"]),
        }

    mean_sat_level = safe_mean(all_sat_levels)

    # Compute incremental overhead: mean of marginal/total across k=2..5
    overhead_ratios = []
    for k in range(2, MAX_K + 1):
        for i in range(len(PROGRAMS)):
            total = per_k[k]["total_times"][i]
            marginal = per_k[k]["marginal_times"][i]
            if total > 0:
                overhead_ratios.append(marginal / total)
    incremental_overhead = safe_mean(overhead_ratios) if overhead_ratios else 0.0

    # Print summary
    print("\n" + "=" * 60)
    print("SPECIFICATION SATISFACTION — COMPOSITION SCALING")
    print(f"  Programs: {len(PROGRAMS)}")
    print(f"  Mean satisfaction level: {mean_sat_level:.3f}")
    print(f"  Incremental overhead: {fmt_pct(incremental_overhead)}")
    print(f"  {'k':>3s}  {'Median total':>14s}  {'Marginal':>14s}")
    for k in range(1, MAX_K + 1):
        a = k_agg[k]
        print(f"  {k:3d}  {a['median_total']*1000:13.1f}ms  {a['median_marginal']*1000:13.1f}ms")

    # ── Generate LaTeX macros ──────────────────────────────────────────
    P = "ppFortySeven"
    tex = [
        "% data-paper47.tex — AUTO-GENERATED by exp47_spec_satisfaction.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp47_spec_satisfaction.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    m("TotalPrograms", len(PROGRAMS))

    k_labels = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    for k in range(1, MAX_K + 1):
        a = k_agg[k]
        label = k_labels[k]
        m(f"Spec{label}Median", fmt_time(a["median_total"]))
        m(f"Spec{label}Marginal", fmt_time(a["median_marginal"]))

    m("MeanSatLevel", f"{mean_sat_level:.3f}")
    m("IncrementalOverhead", fmt_pct(incremental_overhead))

    tex_path = os.path.join(ROOT, "papers", "data-paper47.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper47.json")
    with open(json_path, "w") as f:
        json.dump({
            "n_programs": len(PROGRAMS),
            "mean_sat_level": mean_sat_level,
            "incremental_overhead": incremental_overhead,
            "per_k": {str(k): v for k, v in k_agg.items()},
        }, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
